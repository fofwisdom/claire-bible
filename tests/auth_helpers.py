"""Shared test authentication helpers backed by real claire.db accounts, OAuth 2.1 tokens, and auth_sessions."""

from __future__ import annotations

import json
import secrets
import time
from pathlib import Path
from typing import Any

from claire.store import accounts as accounts_store
from claire.store import db as dbm


def resolve_db_file(target: Any) -> Path:
    """Resolve a Path to claire.db from a Path, str, or Settings-like object."""
    if isinstance(target, (Path, str)):
        path = Path(target)
        return path / "claire.db" if path.is_dir() else path
    db_file = getattr(target, "db_file", None)
    if db_file is not None:
        return Path(db_file)
    data_dir = getattr(target, "data_dir", None)
    if data_dir is not None:
        return Path(data_dir) / "claire.db"
    raise TypeError(f"Cannot resolve claire.db path from {target!r}")


def _open_initialized_db(target: Any):
    db_file = resolve_db_file(target)
    conn = dbm.connect(db_file)
    dbm.init_db(conn)
    return conn


def ensure_test_account(
    target: Any,
    *,
    account_id: int = 2,
    username: str | None = None,
    role: str = "member",
    status: str = "active",
    display_name: str | None = None,
    profile_public: int = 0,
) -> int:
    """Ensure an account row exists in claire.db and return its account_id."""
    conn = _open_initialized_db(target)
    try:
        row = conn.execute("SELECT id FROM accounts WHERE id=?", (int(account_id),)).fetchone()
        if row is not None:
            return int(row["id"])
        uname = username or f"test_user_{account_id}"
        dname = display_name or uname.replace("_", " ").title()
        now = time.time()
        conn.execute(
            "INSERT INTO accounts (id, public_id, username, role, status, display_name, profile_public, created_at, activated_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (int(account_id), (1 << 57) + int(account_id), uname, role, status, dname, int(profile_public), now, now, now),
        )
        conn.commit()
        return int(account_id)
    finally:
        conn.close()


def issue_oauth_token(
    target: Any,
    *,
    account_id: int = dbm.ADMIN_ACCOUNT_ID,
    scope: str = "write",
    client_id: str | None = None,
    client_name: str = "Test OAuth Client",
    token: str | None = None,
    ttl_seconds: float = 3600.0,
) -> str:
    """Insert an active account-bound OAuth 2.1 access token into oauth_tokens."""
    if scope not in ("write", "readonly"):
        raise ValueError(f"OAuth 2.1 tokens support 'write' or 'readonly', got {scope!r}")
    conn = _open_initialized_db(target)
    try:
        cid = client_id or f"test_client_{account_id}"
        now = time.time()
        conn.execute(
            "INSERT OR IGNORE INTO oauth_clients (client_id, client_name, redirect_uris, created_at) VALUES (?, ?, ?, ?)",
            (cid, client_name, json.dumps(["https://test.local/callback"]), now),
        )
        raw_token = token or f"test_oauth_{secrets.token_urlsafe(24)}"
        refresh_token = f"test_refresh_{secrets.token_urlsafe(24)}"
        family_id = f"fam_{secrets.token_urlsafe(12)}"
        conn.execute(
            "INSERT INTO oauth_tokens (token, refresh_token, client_id, scope, granted_scope, token_family_id, account_id, created_at, expires_at, refresh_expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (raw_token, refresh_token, cid, scope, scope, family_id, int(account_id), now, now + float(ttl_seconds), now + max(float(ttl_seconds), 86400.0)),
        )
        conn.commit()
        return raw_token
    finally:
        conn.close()


def issue_session_token(
    target: Any,
    *,
    account_id: int = dbm.ADMIN_ACCOUNT_ID,
    scope: str = "owner",
    authenticated_ago: float = 0.0,
    ttl_seconds: float | None = None,
) -> str:
    """Create an active account-bound auth_sessions row and return its session_token."""
    conn = _open_initialized_db(target)
    try:
        return accounts_store.create_account_session(
            conn,
            account_id=int(account_id),
            scope=scope,
            now=time.time() - float(authenticated_ago),
            ttl_seconds=ttl_seconds,
        )
    finally:
        conn.close()


def bearer_headers(
    target: Any,
    *,
    account_id: int = dbm.ADMIN_ACCOUNT_ID,
    scope: str = "owner",
    oauth: bool = False,
    **kwargs: Any,
) -> dict[str, str]:
    """Return Authorization: Bearer headers backed by an auth_sessions token (default) or an OAuth 2.1 access token (oauth=True)."""
    if oauth:
        token = issue_oauth_token(target, account_id=account_id, scope=scope, **kwargs)
    else:
        token = issue_session_token(target, account_id=account_id, scope=scope, **kwargs)
    return {"Authorization": f"Bearer {token}"}


def session_headers(
    target: Any,
    *,
    account_id: int = dbm.ADMIN_ACCOUNT_ID,
    scope: str = "owner",
    authenticated_ago: float = 0.0,
) -> dict[str, str]:
    """Return X-Session headers backed by an account-bound auth_sessions row."""
    return {"X-Session": issue_session_token(target, account_id=account_id, scope=scope, authenticated_ago=authenticated_ago)}
