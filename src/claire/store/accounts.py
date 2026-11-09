"""계정 저장소와 자격증명의 계정 귀속 (설계 §8.2–§8.4, §13.1).

``accounts``, ``username_tombstones``, ``instance_settings`` 테이블과 ``auth_sessions``·``oauth_tokens``의 ``account_id``·``authenticated_at`` 열을 다룬다. 스키마와 v15 이관은 `store.db`에 있다.

``AccountStore``는 호출자가 연 연결을 감싼다. 연결의 수명은 호출자가 정하고, 변경 메서드는 끝에서 커밋한다(`store.db`의 기존 함수와 같은 관례).
"""

from __future__ import annotations

import hashlib
import json
import secrets
import shutil
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from .. import ids
from ..accounts import username_policy
from ..accounts.username_policy import UsernameRejected
from . import db as dbm
from .raw import remove_artifact

__all__ = [
    "ADMIN_ACCOUNT_ID",
    "Account",
    "AccountNotFound",
    "AccountRole",
    "AccountStateError",
    "AccountStatus",
    "AccountStore",
    "CredentialBinding",
    "INSTANCE_MODES",
    "InstanceMode",
    "UsernameRejected",
    "create_account_session",
    "delete_session",
    "mark_session_authenticated",
    "preview_release_username",
    "release_username",
    "revoke_other_sessions",
    "validate_oauth_binding",
    "validate_session_binding",
]

AccountRole = Literal["admin", "member"]
AccountStatus = Literal["setup_required", "active", "suspended", "deleting", "purged"]
# 인스턴스 모드(§4). ``instance_settings.mode``에 둔다. 행이 없으면 싱글 모드다(P2 이전 설치).
InstanceMode = Literal["single", "network"]
INSTANCE_MODES: frozenset[str] = frozenset({"single", "network"})

# v15 이관이 만드는 관리자 행. v15 이전 자격증명과 테마는 모두 이 계정의 것이다(§8.3, §8.4).
# 값(1)의 정의는 v15 이관과 자격증명 쓰기 함수가 있는 `store.db`에 하나만 둔다.
ADMIN_ACCOUNT_ID = dbm.ADMIN_ACCOUNT_ID

# 세션 자격증명 범위. create_account_session은 이 밖의 값을 거부한다(C1).
SESSION_SCOPES: frozenset[str] = frozenset({"owner", "collaborator", "readonly"})

_ACCOUNT_COLUMNS = (
    "id, public_id, username, role, status, display_name, profile_public, "
    "created_at, activated_at, delete_after, updated_at"
)

# 자격증명이 통하는 계정 상태. 그 밖의 상태(suspended 등)는 자격증명을 거부한다(§8.2).
CREDENTIAL_ACCEPTING_STATUSES: frozenset[str] = frozenset({"setup_required", "active"})


class AccountNotFound(LookupError):
    """계정이 없다."""


class AccountStateError(RuntimeError):
    """현재 상태에서 허용되지 않는 전이다. 예: ``active`` 계정에 ``complete_setup``."""


@dataclass(frozen=True, slots=True)
class Account:
    id: int
    public_id: int
    username: str | None
    role: AccountRole
    status: AccountStatus
    display_name: str
    profile_public: bool
    created_at: float
    activated_at: float | None
    delete_after: float | None
    updated_at: float


@dataclass(frozen=True, slots=True)
class CredentialBinding:
    """세션·OAuth 토큰을 검증한 결과. 토큰 값은 담지 않는다."""

    scope: str
    account_id: int
    # 마지막으로 사람이 인증한 시각(로그인, /setup, 부트스트랩 링크 교환). 단계 인증(§8.5)에 쓴다. OAuth는 None.
    authenticated_at: float | None
    # 감사용 식별자. 세션은 ``nonce``의 SHA-256 앞 16자 같은 비가역 값이며 토큰 값이 아니다.
    credential_id: str


def _row_to_account(row: sqlite3.Row | tuple) -> Account:
    (
        account_id, public_id, username, role, status, display_name, profile_public,
        created_at, activated_at, delete_after, updated_at,
    ) = tuple(row)
    return Account(
        id=int(account_id),
        public_id=int(public_id),
        username=None if username is None else str(username),
        role=role,
        status=status,
        display_name=str(display_name),
        profile_public=bool(profile_public),
        created_at=float(created_at),
        activated_at=None if activated_at is None else float(activated_at),
        delete_after=None if delete_after is None else float(delete_after),
        updated_at=float(updated_at),
    )


def _credential_id(value: str) -> str:
    """감사용 비가역 식별자: SHA-256 hex 앞 16자. 토큰 값 자체는 남기지 않는다."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _clear_account_credentials(conn: sqlite3.Connection, account_id: int) -> None:
    for tbl in ("auth_sessions", "oauth_codes", "oauth_tokens", "oauth_auth_requests"):
        try:
            conn.execute(f"DELETE FROM {tbl} WHERE account_id=?", (account_id,))
        except sqlite3.OperationalError:
            pass


def _detect_data_dir(conn: sqlite3.Connection) -> Path | None:
    try:
        row = conn.execute("PRAGMA database_list").fetchone()
        if row and row[2]:
            return Path(row[2]).parent
    except Exception:
        pass
    return None


def _gc_account_disk_artifacts(conn: sqlite3.Connection, account_id: int, data_dir: Path | str | None = None) -> dict[str, int]:
    """타 테마에서 참조하지 않는 해당 계정의 디스크 산출물(artifacts, raw files, images 등) 정리."""
    stats = {"artifacts": 0, "images": 0, "attachments": 0, "files": 0, "bundles": 0}
    ddir = Path(data_dir) if data_dir is not None else _detect_data_dir(conn)

    owned_rows = conn.execute("SELECT id FROM themes WHERE account_id=?", (account_id,)).fetchall()
    owned_theme_ids = [int(r[0]) for r in owned_rows]

    if owned_theme_ids:
        ph_owned = ",".join("?" for _ in owned_theme_ids)
        owned_docs = {r[0] for r in conn.execute(f"SELECT id FROM documents WHERE theme_id IN ({ph_owned})", owned_theme_ids).fetchall()}
        other_docs = {r[0] for r in conn.execute(f"SELECT id FROM documents WHERE theme_id NOT IN ({ph_owned})", owned_theme_ids).fetchall()}
        orphan_docs = owned_docs - other_docs

        owned_files = {r[0] for r in conn.execute(f"SELECT file_ref FROM raw_inbox WHERE theme_id IN ({ph_owned}) AND file_ref IS NOT NULL AND trim(file_ref) != ''", owned_theme_ids).fetchall()}
        other_files = {r[0] for r in conn.execute(f"SELECT file_ref FROM raw_inbox WHERE theme_id NOT IN ({ph_owned}) AND file_ref IS NOT NULL AND trim(file_ref) != ''", owned_theme_ids).fetchall()}
        orphan_files = owned_files - other_files

        if ddir is not None and ddir.exists():
            for did in orphan_docs:
                for art in (ddir / "raw" / "artifacts" / f"{did}.txt.zst", ddir / "raw" / "artifacts" / f"{did}.txt.gz"):
                    if art.exists():
                        art.unlink(missing_ok=True)
                        stats["artifacts"] += 1
                img_dir = ddir / "images"
                if img_dir.exists():
                    for img in img_dir.glob(f"{did}_*"):
                        if img.is_file():
                            img.unlink(missing_ok=True)
                            stats["images"] += 1
                att_dir = ddir / "raw" / "attachments" / did
                if att_dir.exists():
                    shutil.rmtree(att_dir, ignore_errors=True)
                    stats["attachments"] += 1
                pres_file = ddir / "presentations" / f"{did}.html"
                if pres_file.exists():
                    pres_file.unlink(missing_ok=True)

            for fref in orphan_files:
                fpath = Path(fref) if Path(fref).is_absolute() else (ddir / fref)
                if fpath.is_file():
                    fpath.unlink(missing_ok=True)
                    stats["files"] += 1

            bundles_dir = ddir / "graph_bundles"
            registry_dir = bundles_dir / ".registry"
            if registry_dir.exists():
                for reg_file in registry_dir.glob("*.json"):
                    try:
                        data = json.loads(reg_file.read_text(encoding="utf-8"))
                        if data.get("theme_id") in owned_theme_ids:
                            filename = data.get("filename")
                            if filename and (bundles_dir / filename).exists():
                                (bundles_dir / filename).unlink(missing_ok=True)
                            reg_file.unlink(missing_ok=True)
                            stats["bundles"] += 1
                    except Exception:
                        pass

        for tid in owned_theme_ids:
            dbm.reset_graph(conn, theme_id=tid)
            for tbl in (
                "documents", "document_snapshots", "document_presentations", "raw_inbox",
                "extractions", "proposals", "jobs", "refresh_queue", "expand_queue",
                "doc_shares", "purged_tombstones", "syndication_dispatches", "syndication_items",
            ):
                try:
                    conn.execute(f"DELETE FROM {tbl} WHERE theme_id=?", (tid,))
                except sqlite3.OperationalError:
                    pass
            try:
                conn.execute("DELETE FROM legacy_hosts WHERE theme_id=?", (tid,))
            except sqlite3.OperationalError:
                pass
            conn.execute("DELETE FROM themes WHERE id=?", (tid,))

    try:
        conn.execute("DELETE FROM public_directory_counters WHERE account_id=?", (account_id,))
    except sqlite3.OperationalError:
        pass

    return stats


class AccountStore:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    # --- 조회 -------------------------------------------------------------
    def get(self, account_id: int) -> Account:
        """없으면 ``AccountNotFound``."""
        row = self.conn.execute(
            f"SELECT {_ACCOUNT_COLUMNS} FROM accounts WHERE id=?", (account_id,)
        ).fetchone()
        if row is None:
            raise AccountNotFound(account_id)
        return _row_to_account(row)

    def find_by_username(self, username: str) -> Account | None:
        """정규화한 이름으로 정확히 일치하는 계정. 부분 일치는 하지 않는다."""
        row = self.conn.execute(
            f"SELECT {_ACCOUNT_COLUMNS} FROM accounts WHERE username=?", (username,)
        ).fetchone()
        return None if row is None else _row_to_account(row)

    def admin(self) -> Account:
        """관리자 계정(``role='admin'``). 없으면 ``AccountNotFound``(이관이 안 된 DB)."""
        row = self.conn.execute(
            f"SELECT {_ACCOUNT_COLUMNS} FROM accounts WHERE role='admin'"
        ).fetchone()
        if row is None:
            raise AccountNotFound("admin")
        return _row_to_account(row)

    def list_accounts(self) -> list[Account]:
        """``id`` 오름차순."""
        rows = self.conn.execute(f"SELECT {_ACCOUNT_COLUMNS} FROM accounts ORDER BY id").fetchall()
        return [_row_to_account(r) for r in rows]

    def is_username_retired(self, username: str) -> bool:
        """``username_tombstones``에 있고 ``released_at``이 NULL이면 True."""
        row = self.conn.execute(
            "SELECT 1 FROM username_tombstones WHERE username=? AND released_at IS NULL", (username,)
        ).fetchone()
        return row is not None

    # --- 인스턴스 설정 ------------------------------------------------------
    def get_setting(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM instance_settings WHERE key=?", (key,)).fetchone()
        return None if row is None else str(row[0])

    def set_setting(self, key: str, value: str, *, updated_by: int | None) -> None:
        self.conn.execute(
            "INSERT INTO instance_settings (key, value, updated_at, updated_by) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at, "
            "updated_by=excluded.updated_by",
            (key, value, time.time(), updated_by),
        )
        self.conn.commit()

    def instance_mode(self) -> InstanceMode:
        """``instance_settings.mode``. 행이 없으면 ``"single"``. 알 수 없는 값이면 ``RuntimeError``(C1)."""
        raw = self.get_setting("mode")
        if raw is None:
            return "single"
        if raw not in INSTANCE_MODES:
            raise RuntimeError(f"instance_settings.mode has an unknown value: {raw!r}")
        return raw  # type: ignore[return-value]  # INSTANCE_MODES가 걸렀다

    def set_instance_mode(self, mode: InstanceMode, *, updated_by: int) -> None:
        """모드를 기록한다. 전환 조건(§4.2 준비 검사·멤버 없음)은 호출자(``accounts_routes``)가 먼저 판정한다."""
        raise NotImplementedError  # P2 L2-f

    def id_salt(self) -> bytes:
        """``instance_settings.id_salt``(hex). 없으면 ``RuntimeError``(이관 누락, C1)."""
        raw = self.get_setting("id_salt")
        if raw is None:
            raise RuntimeError("instance_settings.id_salt is missing (schema v15 migration not applied)")
        try:
            salt = bytes.fromhex(raw)
        except ValueError as exc:
            raise RuntimeError("instance_settings.id_salt is not valid hex") from exc
        if not salt:
            raise RuntimeError("instance_settings.id_salt is empty")
        return salt

    # --- 변경 -------------------------------------------------------------
    def complete_setup(
        self,
        account_id: int,
        *,
        username: str,
        display_name: str,
        now: float | None = None,
    ) -> Account:
        """최초 관리자 설정(§8.4-5). ``status='setup_required'``인 관리자만 ``active``로 바꾼다.

        ``username``은 `accounts.username_policy.validate_username`을 이미 통과한 값이어야 한다. 퇴역 이름이면 ``UsernameRejected("retired")``, 다른 계정이 쓰면 ``UsernameRejected("taken")``. 상태가 맞지 않으면 ``AccountStateError``. 한 번의 조건부 UPDATE로 처리해 동시 요청 중 하나만 성공한다.
        """
        when = time.time() if now is None else now
        current = self.get(account_id)
        self._require_setup_pending(current)
        if self.is_username_retired(username):
            raise UsernameRejected("retired")
        taken = self.find_by_username(username)
        if taken is not None and taken.id != account_id:
            raise UsernameRejected("taken")
        try:
            # 상태·역할·이름 미설정·퇴역 아님을 모두 WHERE에 넣는다. 동시 요청은 SQLite 쓰기 직렬화로 하나만 rowcount=1을 얻는다.
            cur = self.conn.execute(
                "UPDATE accounts SET username=?, display_name=?, status='active', activated_at=?, updated_at=? "
                "WHERE id=? AND role='admin' AND status='setup_required' AND username IS NULL "
                "AND NOT EXISTS (SELECT 1 FROM username_tombstones WHERE username=? AND released_at IS NULL)",
                (username, display_name, when, when, account_id, username),
            )
        except sqlite3.IntegrityError as exc:
            self.conn.rollback()
            if str(exc) == "UNIQUE constraint failed: accounts.username":
                raise UsernameRejected("taken") from exc
            raise
        if cur.rowcount != 1:
            self.conn.rollback()
            # 검사와 UPDATE 사이에 다른 요청이 먼저 바꿨다. 무엇이 바뀌었는지 다시 읽어 사유를 정한다.
            self._require_setup_pending(self.get(account_id))
            if self.is_username_retired(username):
                raise UsernameRejected("retired")
            raise AccountStateError(f"account {account_id}: setup could not be completed")
        self.conn.commit()
        return self.get(account_id)

    @staticmethod
    def _require_setup_pending(account: Account) -> None:
        if account.role != "admin" or account.status != "setup_required" or account.username is not None:
            raise AccountStateError(
                f"account {account.id}: complete_setup needs an admin in setup_required "
                f"(role={account.role}, status={account.status})"
            )

    def update_display_name(self, account_id: int, display_name: str, *, now: float | None = None) -> Account:
        when = time.time() if now is None else now
        cur = self.conn.execute(
            "UPDATE accounts SET display_name=?, updated_at=? WHERE id=?",
            (display_name, when, account_id),
        )
        if cur.rowcount != 1:
            self.conn.rollback()
            raise AccountNotFound(account_id)
        self.conn.commit()
        return self.get(account_id)

    def update_profile_public(self, account_id: int, profile_public: int | bool, *, now: float | None = None) -> Account:
        when = time.time() if now is None else now
        value = 1 if profile_public else 0
        cur = self.conn.execute(
            "UPDATE accounts SET profile_public=?, updated_at=? WHERE id=?",
            (value, when, account_id),
        )
        if cur.rowcount != 1:
            self.conn.rollback()
            raise AccountNotFound(account_id)
        self.conn.commit()
        return self.get(account_id)


    def suspend(self, account_id: int, *, now: float | None = None) -> Account:
        """active 계정을 suspended 상태로 전이하고 모든 자격증명을 즉시 정리한다."""
        when = time.time() if now is None else now
        current = self.get(account_id)
        if current.role == "admin":
            raise AccountStateError("admin account cannot be suspended")
        if current.status != "active":
            raise AccountStateError(f"account {account_id}: cannot suspend from status {current.status!r}")
        _clear_account_credentials(self.conn, account_id)
        cur = self.conn.execute(
            "UPDATE accounts SET status='suspended', updated_at=? WHERE id=? AND status='active'",
            (when, account_id),
        )
        if cur.rowcount != 1:
            self.conn.rollback()
            raise AccountStateError(f"account {account_id}: could not transition to suspended")
        self.conn.commit()
        return self.get(account_id)

    def resume(self, account_id: int, *, now: float | None = None) -> Account:
        """suspended 계정을 active 상태로 복원한다."""
        when = time.time() if now is None else now
        current = self.get(account_id)
        if current.status != "suspended":
            raise AccountStateError(f"account {account_id}: cannot resume from status {current.status!r}")
        cur = self.conn.execute(
            "UPDATE accounts SET status='active', updated_at=? WHERE id=? AND status='suspended'",
            (when, account_id),
        )
        if cur.rowcount != 1:
            self.conn.rollback()
            raise AccountStateError(f"account {account_id}: could not transition to active")
        self.conn.commit()
        return self.get(account_id)

    def schedule_delete(self, account_id: int, *, delete_after: float | None = None, now: float | None = None) -> Account:
        """active 또는 suspended 계정을 deleting 상태로 전이하고 7일 유예를 부여하며 자격증명을 즉시 정리한다."""
        when = time.time() if now is None else now
        current = self.get(account_id)
        if current.role == "admin":
            raise AccountStateError("admin account cannot be deleted")
        if current.status not in ("active", "suspended"):
            raise AccountStateError(f"account {account_id}: cannot schedule deletion from status {current.status!r}")
        target_delete_after = (when + 7 * 86400.0) if delete_after is None else float(delete_after)
        _clear_account_credentials(self.conn, account_id)
        cur = self.conn.execute(
            "UPDATE accounts SET status='deleting', delete_after=?, updated_at=? WHERE id=? AND status IN ('active', 'suspended')",
            (target_delete_after, when, account_id),
        )
        if cur.rowcount != 1:
            self.conn.rollback()
            raise AccountStateError(f"account {account_id}: could not schedule deletion")
        self.conn.commit()
        return self.get(account_id)

    def cancel_delete(self, account_id: int, *, now: float | None = None) -> Account:
        """deleting 상태의 계정 삭제 예약을 취소하고 active로 복원한다."""
        when = time.time() if now is None else now
        current = self.get(account_id)
        if current.status != "deleting":
            raise AccountStateError(f"account {account_id}: cannot cancel deletion from status {current.status!r}")
        cur = self.conn.execute(
            "UPDATE accounts SET status='active', delete_after=NULL, updated_at=? WHERE id=? AND status='deleting'",
            (when, account_id),
        )
        if cur.rowcount != 1:
            self.conn.rollback()
            raise AccountStateError(f"account {account_id}: could not cancel deletion")
        self.conn.commit()
        return self.get(account_id)

    def purge(self, account_id: int, *, data_dir: Path | str | None = None, now: float | None = None) -> Account:
        """계정을 완전히 삭제(purged) 처리하고, tombstones 등록, 자격증명 및 디스크 산출물을 정리한다."""
        when = time.time() if now is None else now
        current = self.get(account_id)
        if current.role == "admin":
            raise AccountStateError("admin account cannot be purged")
        if current.status not in ("deleting", "suspended", "active"):
            raise AccountStateError(f"account {account_id}: cannot purge from status {current.status!r}")

        _clear_account_credentials(self.conn, account_id)

        if current.username:
            self.conn.execute(
                "INSERT INTO username_tombstones (username, account_id, was_public, retired_at, released_at, released_by) "
                "VALUES (?, ?, ?, ?, NULL, NULL) "
                "ON CONFLICT(username) DO UPDATE SET "
                "account_id=excluded.account_id, was_public=excluded.was_public, retired_at=excluded.retired_at, "
                "released_at=NULL, released_by=NULL",
                (current.username, current.id, 1 if current.profile_public else 0, when),
            )

        _gc_account_disk_artifacts(self.conn, account_id, data_dir=data_dir)

        cur = self.conn.execute(
            "UPDATE accounts SET status='purged', delete_after=NULL, updated_at=? WHERE id=?",
            (when, account_id),
        )
        if cur.rowcount != 1:
            self.conn.rollback()
            raise AccountStateError(f"account {account_id}: could not purge")
        self.conn.commit()
        return self.get(account_id)

    def preview_release_username(
        self,
        username: str,
        *,
        auth_conn: sqlite3.Connection | None = None,
        data_dir: Path | str | None = None,
    ) -> dict[str, Any]:
        """초기화 대상 잔여 항목(410 표식, 예약 초대 등) 미리보기 데이터 반환."""
        norm_name = username.strip().lower()
        row = self.conn.execute(
            "SELECT username, account_id, was_public, retired_at, released_at, released_by "
            "FROM username_tombstones WHERE username=? AND released_at IS NULL",
            (norm_name,),
        ).fetchone()
        if row is None:
            raise LookupError(f"username {username!r} is not retired or already released")

        account_id = int(row["account_id"])
        was_public = bool(row["was_public"])

        reserved_invitations = 0
        if auth_conn is not None:
            try:
                inv_row = auth_conn.execute(
                    "SELECT COUNT(*) FROM invitations WHERE reserved_username=?",
                    (norm_name,),
                ).fetchone()
                if inv_row is not None:
                    reserved_invitations = int(inv_row[0])
            except sqlite3.OperationalError:
                pass

        public_directory_entries = 0
        try:
            p_row = self.conn.execute(
                "SELECT COUNT(*) FROM public_directory_counters WHERE account_id=?",
                (account_id,),
            ).fetchone()
            if p_row is not None:
                public_directory_entries = int(p_row[0])
        except sqlite3.OperationalError:
            pass

        residual_sessions = 0
        try:
            s_row = self.conn.execute("SELECT COUNT(*) FROM auth_sessions WHERE account_id=?", (account_id,)).fetchone()
            if s_row is not None:
                residual_sessions = int(s_row[0])
        except sqlite3.OperationalError:
            pass

        residual_oauth_tokens = 0
        try:
            o_row = self.conn.execute("SELECT COUNT(*) FROM oauth_tokens WHERE account_id=?", (account_id,)).fetchone()
            if o_row is not None:
                residual_oauth_tokens = int(o_row[0])
        except sqlite3.OperationalError:
            pass

        return {
            "username": norm_name,
            "account_id": account_id,
            "retired_at": float(row["retired_at"]),
            "was_public": was_public,
            "has_410_marker": was_public,
            "reserved_invitations": reserved_invitations,
            "public_directory_entries": public_directory_entries,
            "residual_sessions": residual_sessions,
            "residual_oauth_tokens": residual_oauth_tokens,
        }

    def release_username(
        self,
        username: str,
        released_by: int,
        *,
        auth_conn: sqlite3.Connection | None = None,
        now: float | None = None,
    ) -> bool:
        """username_tombstones의 released_at, released_by 업데이트 및 410 표식 해제."""
        when = time.time() if now is None else now
        norm_name = username.strip().lower()
        row = self.conn.execute(
            "SELECT account_id FROM username_tombstones WHERE username=? AND released_at IS NULL",
            (norm_name,),
        ).fetchone()
        if row is None:
            return False

        account_id = int(row["account_id"])

        self.conn.execute(
            "UPDATE username_tombstones SET released_at=?, released_by=?, was_public=0 "
            "WHERE username=? AND released_at IS NULL",
            (when, released_by, norm_name),
        )

        if auth_conn is not None:
            try:
                auth_conn.execute(
                    "UPDATE invitations SET reserved_username=NULL WHERE reserved_username=?",
                    (norm_name,),
                )
                auth_conn.commit()
            except sqlite3.OperationalError:
                pass

        _clear_account_credentials(self.conn, account_id)

        self.conn.execute(
            "DELETE FROM accounts WHERE username=? AND status='purged'",
            (norm_name,),
        )

        self.conn.commit()
        return True

    def list_retired_usernames(self) -> list[dict[str, Any]]:
        """퇴역 상태(released_at IS NULL)인 USERNAME 목록을 반환한다."""
        rows = self.conn.execute(
            "SELECT username, account_id, was_public, retired_at "
            "FROM username_tombstones WHERE released_at IS NULL ORDER BY retired_at DESC"
        ).fetchall()
        return [
            {
                "username": str(r["username"]),
                "account_id": int(r["account_id"]),
                "was_public": bool(r["was_public"]),
                "retired_at": float(r["retired_at"]),
            }
            for r in rows
        ]

    def validate_username(
        self,
        username: str,
        *,
        account_id: int | None = None,
        instance_reserved: frozenset[str] = frozenset(),
    ) -> str:
        """username 문법, 예약어, 인스턴스 예약, 퇴역(retired), 중복(taken)을 종합 검증한다."""
        norm_name = username_policy.validate_username(username, instance_reserved=instance_reserved)
        if self.is_username_retired(norm_name):
            raise UsernameRejected("retired")
        taken = self.find_by_username(norm_name)
        if taken is not None and (account_id is None or taken.id != account_id):
            raise UsernameRejected("taken")
        return norm_name

    def create_member(
        self,
        *,
        username: str,
        display_name: str = "",
        profile_public: bool = False,
        now: float | None = None,
        instance_reserved: frozenset[str] = frozenset(),
    ) -> Account:
        """신규 멤버 계정을 생성한다. 퇴역/중복 시 UsernameRejected."""
        when = time.time() if now is None else now
        norm_name = self.validate_username(username, instance_reserved=instance_reserved)
        salt = self.id_salt()

        def _insert(public_id: int) -> None:
            self.conn.execute(
                "INSERT INTO accounts (public_id, username, role, status, display_name, profile_public, created_at, activated_at, updated_at) "
                "VALUES (?, ?, 'member', 'active', ?, ?, ?, ?, ?)",
                (public_id, norm_name, display_name, 1 if profile_public else 0, when, when, when),
            )

        ids.insert_with_timestamp_id(self.conn, table="accounts", column="public_id", salt=salt, insert=_insert)
        self.conn.commit()
        created = self.find_by_username(norm_name)
        assert created is not None
        return created


# --- 자격증명 귀속 (auth_sessions, oauth_tokens) -------------------------------


def create_account_session(
    conn: sqlite3.Connection,
    *,
    account_id: int,
    scope: str = "owner",
    replace_token: str | None = None,
    now: float | None = None,
) -> str:
    """사람이 방금 인증한 세션을 새로 발급한다(로그인, /setup 확정). ``authenticated_at=now``.

    ``replace_token``이 있으면 그 세션을 지운다(세션 고정 방지, §8.4-5). 기존 ``create_session``과 달리 같은 scope의 **다른 계정** 세션은 건드리지 않는다.
    """
    if scope not in SESSION_SCOPES:
        raise ValueError(f"unknown session scope: {scope!r}")
    if conn.execute("SELECT 1 FROM accounts WHERE id=?", (account_id,)).fetchone() is None:
        raise AccountNotFound(account_id)
    when = time.time() if now is None else now
    token = secrets.token_urlsafe(32)
    try:
        if replace_token:
            conn.execute("DELETE FROM auth_sessions WHERE session_token=?", (replace_token,))
        conn.execute(
            "INSERT INTO auth_sessions "
            "(nonce, session_token, approved, created_at, expires_at, scope, account_id, authenticated_at) "
            "VALUES (?, ?, 1, ?, ?, ?, ?, ?)",
            (token, token, when, when + dbm.SESSION_TTL, scope, account_id, when),
        )
    except BaseException:
        conn.rollback()
        raise
    conn.commit()
    return token


def validate_session_binding(
    conn: sqlite3.Connection,
    token: str,
    *,
    scopes: tuple[str, ...] = ("owner", "collaborator", "readonly"),
) -> CredentialBinding | None:
    """`store.db.validate_session_scope`와 같은 검증·슬라이딩 만료에 계정 귀속을 더한다.

    ``account_id``가 NULL인 행은 거부한다(v15 이관이 모두 채우므로 NULL은 결함이다, C1). 계정 상태는 여기서 보지 않는다. 호출자(`api.security`)가 ``AccountStore.get``으로 확인한다.
    """
    if not dbm.plausible_session_token(token) or not scopes:
        return None
    ph = ",".join("?" for _ in scopes)
    row = conn.execute(
        f"SELECT nonce, expires_at, scope, account_id, authenticated_at FROM auth_sessions "
        f"WHERE session_token=? AND approved=1 AND scope IN ({ph})",
        (token, *scopes),
    ).fetchone()
    now = time.time()
    if row is None or row["expires_at"] is None or row["expires_at"] < now:
        return None
    if row["account_id"] is None:
        return None
    ttl = dbm.SESSION_TTL
    if row["expires_at"] < now + (ttl / 2):
        conn.execute("UPDATE auth_sessions SET expires_at=? WHERE session_token=?", (now + ttl, token))
        conn.commit()
    authenticated_at = row["authenticated_at"]
    return CredentialBinding(
        scope=str(row["scope"]),
        account_id=int(row["account_id"]),
        authenticated_at=None if authenticated_at is None else float(authenticated_at),
        # nonce는 세션 행의 고정 키다. 교환·회전으로 session_token이 바뀌어도 같은 식별자가 남는다.
        credential_id=_credential_id(str(row["nonce"])),
    )


def validate_oauth_binding(conn: sqlite3.Connection, token: str) -> CredentialBinding | None:
    """`store.db.validate_oauth_token`에 계정 귀속을 더한다. ``authenticated_at``은 None."""
    if not token or len(token) < 20:
        return None
    row = conn.execute(
        "SELECT scope, account_id FROM oauth_tokens WHERE token=? AND expires_at>=?",
        (token, time.time()),
    ).fetchone()
    if row is None or row["account_id"] is None:
        return None
    return CredentialBinding(
        scope=str(row["scope"]),
        account_id=int(row["account_id"]),
        authenticated_at=None,
        credential_id=_credential_id(token),
    )


def mark_session_authenticated(conn: sqlite3.Connection, token: str, *, now: float | None = None) -> bool:
    """단계 인증(§8.5)을 다시 통과한 세션의 ``authenticated_at``을 갱신한다. 세션이 없으면 False."""
    if not dbm.plausible_session_token(token):
        return False
    when = time.time() if now is None else now
    cur = conn.execute(
        "UPDATE auth_sessions SET authenticated_at=? "
        "WHERE session_token=? AND approved=1 AND expires_at>=? AND account_id IS NOT NULL",
        (when, token, when),
    )
    conn.commit()
    return cur.rowcount == 1


def delete_session(conn: sqlite3.Connection, token: str) -> int:
    """세션 행 하나를 지운다(로그아웃). 형식이 맞지 않는 값은 DB에 묻지 않는다."""
    if not dbm.plausible_session_token(token):
        return 0
    cur = conn.execute("DELETE FROM auth_sessions WHERE session_token=?", (token,))
    conn.commit()
    return int(cur.rowcount)


def revoke_other_sessions(conn: sqlite3.Connection, account_id: int, *, keep_token: str | None) -> int:
    """그 계정의 승인된 세션(부트스트랩 링크 포함) 중 ``keep_token``을 뺀 나머지를 지운다. OAuth·API 토큰은 건드리지 않는다."""
    cur = conn.execute(
        "DELETE FROM auth_sessions WHERE account_id=? AND approved=1 AND session_token IS NOT ?",
        (account_id, keep_token),
    )
    conn.commit()
    return int(cur.rowcount)


def preview_release_username(
    conn: sqlite3.Connection,
    username: str,
    *,
    auth_conn: sqlite3.Connection | None = None,
    data_dir: Path | str | None = None,
) -> dict[str, Any]:
    """초기화 대상 잔여 항목 미리보기 데이터 반환."""
    return AccountStore(conn).preview_release_username(username, auth_conn=auth_conn, data_dir=data_dir)


def release_username(
    conn: sqlite3.Connection,
    username: str,
    released_by: int,
    *,
    auth_conn: sqlite3.Connection | None = None,
    now: float | None = None,
) -> bool:
    """username_tombstones의 released_at, released_by 업데이트 및 410 표식 해제."""
    return AccountStore(conn).release_username(username, released_by, auth_conn=auth_conn, now=now)
