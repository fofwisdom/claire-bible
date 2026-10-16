"""API and MCP persistent access token store decoupled from SQLite knowledge database.

Manages dedicated credential file (data/auth/api_tokens.json, chmod 0600) completely
isolated from claire.db schema, migrations, and database write locks.
Provides 100% in-memory token validation hot-path and atomic file persistence.
"""

from __future__ import annotations

import copy
import fcntl
import json
import logging
import os
from pathlib import Path
import secrets
import tempfile
import threading
import time
from typing import Any

log = logging.getLogger("claire.token_store")

API_TOKENS_FILENAME = "api_tokens.json"
API_TOKENS_LOCK_FILENAME = "api_tokens.lock"

_token_store_instances: dict[str, TokenStore] = {}
_instances_lock = threading.Lock()


class TokenStore:
    """JSON-backed persistent credential store for API and MCP access tokens."""

    def __init__(self, storage_path: Path | str | None = None, data_dir: Path | str | None = None) -> None:
        if storage_path is not None:
            self.storage_path = Path(storage_path).resolve()
        elif data_dir is not None:
            self.storage_path = (Path(data_dir) / "auth" / API_TOKENS_FILENAME).resolve()
        else:
            try:
                from ..config import get_settings
                base_data_dir = get_settings().data_dir
            except Exception:
                base_data_dir = Path("data")
            self.storage_path = (Path(base_data_dir) / "auth" / API_TOKENS_FILENAME).resolve()

        self.auth_dir = self.storage_path.parent
        self.lock_path = self.auth_dir / API_TOKENS_LOCK_FILENAME

        self._lock = threading.RLock()
        self._tokens_list: list[dict[str, Any]] = []
        self._tokens_by_token: dict[str, dict[str, Any]] = {}
        self._tokens_by_id: dict[int, dict[str, Any]] = {}
        self._last_loaded_mtime: float = -1.0
        self._pending_last_used: dict[int, float] = {}
        self._last_flush_time: float = time.time()

        self._ensure_storage_dir()
        self.load(force=True)

    def _ensure_storage_dir(self) -> None:
        """Ensure auth directory exists with restricted 0700 permissions."""
        self.auth_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.auth_dir, 0o700)
        except OSError:
            pass

    def _acquire_process_lock(self) -> int:
        """Acquire POSIX advisory file lock for inter-process synchronization."""
        self._ensure_storage_dir()
        fd = os.open(str(self.lock_path), os.O_CREAT | os.O_RDWR, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        return fd

    def _release_process_lock(self, fd: int) -> None:
        """Release POSIX advisory file lock."""
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
        finally:
            try:
                os.close(fd)
            except OSError:
                pass

    def load(self, force: bool = False) -> list[dict[str, Any]]:
        """Load tokens from disk into memory if file changed or forced."""
        with self._lock:
            if not self.storage_path.is_file():
                self._tokens_list = []
                self._tokens_by_token = {}
                self._tokens_by_id = {}
                self._last_loaded_mtime = -1.0
                return []

            try:
                st = self.storage_path.stat()
                mtime = st.st_mtime
                if not force and mtime <= self._last_loaded_mtime:
                    return [copy.deepcopy(t) for t in self._tokens_list]

                raw_content = self.storage_path.read_text(encoding="utf-8")
                if not raw_content.strip():
                    data = {"version": 1, "tokens": []}
                else:
                    data = json.loads(raw_content)

                raw_tokens = data.get("tokens", []) if isinstance(data, dict) else []
                tokens_list: list[dict[str, Any]] = []
                by_token: dict[str, dict[str, Any]] = {}
                by_id: dict[int, dict[str, Any]] = {}

                for item in raw_tokens:
                    if not isinstance(item, dict):
                        continue
                    tok_id = int(item.get("id", 0))
                    token_str = str(item.get("token", "")).strip()
                    if not token_str or tok_id <= 0:
                        continue
                    clean_entry = {
                        "id": tok_id,
                        "token": token_str,
                        "name": str(item.get("name", "")).strip(),
                        "scope": str(item.get("scope", "readonly")).strip().lower(),
                        "created_at": float(item.get("created_at", 0.0)),
                        "expires_at": float(item["expires_at"]) if item.get("expires_at") is not None else None,
                        "last_used_at": float(item["last_used_at"]) if item.get("last_used_at") is not None else None,
                        "revoked_at": float(item["revoked_at"]) if item.get("revoked_at") is not None else None,
                    }
                    tokens_list.append(clean_entry)
                    by_token[token_str] = clean_entry
                    by_id[tok_id] = clean_entry

                # Preserve any recent in-memory last_used_at updates not yet on disk
                for tid, l_used in self._pending_last_used.items():
                    if tid in by_id:
                        by_id[tid]["last_used_at"] = max(by_id[tid]["last_used_at"] or 0.0, l_used)

                self._tokens_list = tokens_list
                self._tokens_by_token = by_token
                self._tokens_by_id = by_id
                self._last_loaded_mtime = mtime
                return [copy.deepcopy(t) for t in self._tokens_list]
            except Exception as exc:
                log.warning("Failed to load token store from %s: %s", self.storage_path, exc)
                return [copy.deepcopy(t) for t in self._tokens_list]

    def _save_unlocked(self) -> None:
        """Atomically persist current in-memory tokens to disk with permissions 0600."""
        self._ensure_storage_dir()
        payload = {
            "version": 1,
            "updated_at": time.time(),
            "tokens": sorted(self._tokens_list, key=lambda x: x["id"]),
        }
        raw_json = json.dumps(payload, ensure_ascii=False, indent=2)

        with tempfile.NamedTemporaryFile("w", dir=self.auth_dir, delete=False, encoding="utf-8") as tf:
            tf.write(raw_json)
            tmp_name = tf.name

        try:
            os.chmod(tmp_name, 0o600)
        except OSError:
            pass

        os.replace(tmp_name, self.storage_path)
        try:
            self._last_loaded_mtime = self.storage_path.stat().st_mtime
        except OSError:
            pass
        self._pending_last_used.clear()
        self._last_flush_time = time.time()

    def create_api_token(
        self,
        name: str,
        scope: str = "readonly",
        expires_in_days: int | None = None,
        token: str | None = None,
    ) -> dict[str, Any]:
        """Create a new persistent access token and save atomically."""
        name_clean = str(name or "").strip()
        if not name_clean:
            raise ValueError("Token name is required.")

        scope_clean = str(scope or "readonly").strip().lower()
        if scope_clean == "collab":
            scope_clean = "collaborator"
        if scope_clean not in ("owner", "collaborator", "readonly"):
            raise ValueError(f"Invalid scope: {scope_clean}. Must be owner, collaborator, or readonly.")

        if token is None:
            token_val = f"cb_{secrets.token_urlsafe(32)}"
        else:
            token_val = str(token).strip()
            if len(token_val) < 16:
                raise ValueError("Token value must have a minimum of 16 characters.")

        with self._lock:
            p_lock = self._acquire_process_lock()
            try:
                self.load(force=True)
                if token_val in self._tokens_by_token:
                    raise ValueError("Token value already exists.")

                next_id = max([t["id"] for t in self._tokens_list], default=0) + 1
                now = time.time()
                expires_at = (now + float(expires_in_days) * 86400.0) if expires_in_days is not None else None

                record = {
                    "id": next_id,
                    "token": token_val,
                    "name": name_clean,
                    "scope": scope_clean,
                    "created_at": now,
                    "expires_at": expires_at,
                    "last_used_at": None,
                    "revoked_at": None,
                }
                self._tokens_list.append(record)
                self._tokens_by_token[token_val] = record
                self._tokens_by_id[next_id] = record
                self._save_unlocked()
                return copy.deepcopy(record)
            finally:
                self._release_process_lock(p_lock)

    def list_api_tokens(self, include_revoked: bool = False) -> list[dict[str, Any]]:
        """List access tokens sorted by id descending."""
        with self._lock:
            self._maybe_reload_from_disk()
            tokens = [t for t in self._tokens_list if include_revoked or t.get("revoked_at") is None]
            tokens.sort(key=lambda x: x["id"], reverse=True)
            return [copy.deepcopy(t) for t in tokens]

    def get_api_token(self, token_or_id: str | int) -> dict[str, Any] | None:
        """Find token by ID, exact token value, or token prefix."""
        with self._lock:
            self._maybe_reload_from_disk()
            if isinstance(token_or_id, int) or (isinstance(token_or_id, str) and token_or_id.isdigit()):
                tok = self._tokens_by_id.get(int(token_or_id))
                return copy.deepcopy(tok) if tok is not None else None

            target = str(token_or_id).strip()
            if target in self._tokens_by_token:
                return copy.deepcopy(self._tokens_by_token[target])

            if len(target) >= 6:
                candidates = [t for t in self._tokens_list if t["token"].startswith(target)]
                if candidates:
                    candidates.sort(key=lambda x: x["id"], reverse=True)
                    return copy.deepcopy(candidates[0])

            return None

    def revoke_api_token(self, token_or_id: str | int) -> bool:
        """Revoke a token immediately."""
        with self._lock:
            p_lock = self._acquire_process_lock()
            try:
                self.load(force=True)
                tok: dict[str, Any] | None = None
                if isinstance(token_or_id, int) or (isinstance(token_or_id, str) and token_or_id.isdigit()):
                    tok = self._tokens_by_id.get(int(token_or_id))
                else:
                    target = str(token_or_id).strip()
                    tok = self._tokens_by_token.get(target)
                    if tok is None and len(target) >= 6:
                        cand = [t for t in self._tokens_list if t["token"].startswith(target)]
                        if cand:
                            cand.sort(key=lambda x: x["id"], reverse=True)
                            tok = cand[0]

                if tok is None or tok.get("revoked_at") is not None:
                    return False

                tok["revoked_at"] = time.time()
                self._save_unlocked()
                return True
            finally:
                self._release_process_lock(p_lock)

    def validate_api_token(self, token: str) -> dict[str, Any] | None:
        """Validate token hot-path. 100% in-memory with zero database or file locks."""
        if not token or len(token) < 16:
            return None

        with self._lock:
            tok = self._tokens_by_token.get(token)
            if tok is None:
                if self._maybe_reload_from_disk():
                    tok = self._tokens_by_token.get(token)
            if tok is None:
                return None

            if tok.get("revoked_at") is not None:
                return None

            now = time.time()
            expires_at = tok.get("expires_at")
            if expires_at is not None and expires_at < now:
                return None

            tok["last_used_at"] = now
            self._pending_last_used[tok["id"]] = now

            if now - self._last_flush_time > 300.0:
                self._flush_pending_last_used_unlocked(now)

            return {
                "id": tok["id"],
                "token": tok["token"],
                "name": tok["name"],
                "scope": tok["scope"],
            }

    def _maybe_reload_from_disk(self) -> bool:
        """Check file mtime and reload if modified on disk."""
        if not self.storage_path.is_file():
            return False
        try:
            mtime = self.storage_path.stat().st_mtime
            if mtime > self._last_loaded_mtime:
                self.load(force=True)
                return True
        except OSError:
            pass
        return False

    def _flush_pending_last_used_unlocked(self, now: float) -> None:
        """Flush debounced last_used_at timestamps to file."""
        if not self._pending_last_used:
            self._last_flush_time = now
            return
        try:
            p_lock = self._acquire_process_lock()
            try:
                self.load(force=False)
                for tid, l_used in list(self._pending_last_used.items()):
                    if tid in self._tokens_by_id:
                        self._tokens_by_id[tid]["last_used_at"] = l_used
                self._save_unlocked()
            finally:
                self._release_process_lock(p_lock)
        except Exception as exc:
            log.warning("Failed to flush token last_used_at: %s", exc)

    def flush(self) -> None:
        """Explicitly flush all pending in-memory updates to disk."""
        with self._lock:
            self._flush_pending_last_used_unlocked(time.time())

    def ensure_default_owner_token(self, fallback_token: str = "") -> dict[str, Any]:
        """Ensure at least one active owner token exists."""
        with self._lock:
            self._maybe_reload_from_disk()
            for tok in self._tokens_list:
                if tok.get("scope") == "owner" and tok.get("revoked_at") is None:
                    return copy.deepcopy(tok)

            fb = str(fallback_token or "").strip()
            val = fb if (fb and len(fb) >= 16) else None
            return self.create_api_token(
                name="Default Knowledge Manager (Owner)",
                scope="owner",
                expires_in_days=None,
                token=val,
            )

    def migrate_env_tokens(
        self,
        env_inject_token: str = "",
        env_readonly_token: str = "",
        env_collab_token: str = "",
    ) -> list[dict[str, Any]]:
        """Migrate legacy environment variable tokens into the file store."""
        with self._lock:
            self.load()
            migrated: list[dict[str, Any]] = []

            def _import(val: str, name: str, scope: str) -> None:
                raw = str(val or "").strip()
                if not raw or len(raw) < 16:
                    return
                if raw in self._tokens_by_token:
                    return
                created = self.create_api_token(name=name, scope=scope, expires_in_days=None, token=raw)
                migrated.append(created)

            _import(env_inject_token, "Legacy Owner Token (.env)", "owner")
            _import(env_readonly_token, "Legacy Readonly Token (.env)", "readonly")
            _import(env_collab_token, "Legacy Collaborator Token (.env)", "collaborator")
            return migrated


def get_token_store(
    data_dir: Path | str | None = None,
    storage_path: Path | str | None = None,
) -> TokenStore:
    """Get or create singleton TokenStore instance for given directory or path."""
    with _instances_lock:
        if storage_path is not None:
            key = str(Path(storage_path).resolve())
        elif data_dir is not None:
            key = str((Path(data_dir) / "auth" / API_TOKENS_FILENAME).resolve())
        else:
            try:
                from ..config import get_settings
                base_data_dir = get_settings().data_dir
            except Exception:
                base_data_dir = Path("data")
            key = str((Path(base_data_dir) / "auth" / API_TOKENS_FILENAME).resolve())

        if key not in _token_store_instances:
            _token_store_instances[key] = TokenStore(storage_path=key)
        return _token_store_instances[key]


def reset_token_stores() -> None:
    """Clear cached TokenStore instances (useful in test teardown)."""
    with _instances_lock:
        _token_store_instances.clear()
