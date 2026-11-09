"""Tests for RFC 8693 OAuth Token Exchange account binding integrity (R3).

Verifies that subject tokens strictly require valid session bindings to active accounts
and completely prevents unauthorized fallback to admin account ID 1.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from starlette.testclient import TestClient

from claire.api import server
from claire.store import accounts as accounts_store
from claire.store import db as dbm


@dataclass
class StubSettings:
    db_file: Path
    data_dir: Path
    environment: str = "development"
    public_url: str = "http://127.0.0.1:8765"
    inject_host: str = "127.0.0.1"
    inject_port: int = 8765
    inject_token: str = "owner-" + ("o" * 32)
    readonly_token: str = "readonly-" + ("r" * 32)
    cors_allowed_origins: str = ""
    anonymous_readonly: bool = False
    effective_provider: str = "mock"
    telegram_bot_token: str = ""
    telegram_owner_chat_id: int = 0
    allowed_user_ids: set[int] = None  # type: ignore

    def __post_init__(self) -> None:
        if self.allowed_user_ids is None:
            self.allowed_user_ids = set()


class StubService:
    def __init__(self) -> None:
        self.provider = SimpleNamespace(name="stub")


def _settings(tmp_path: Path) -> StubSettings:
    db_file = tmp_path / "test.db"
    conn = dbm.connect(db_file)
    dbm.init_db(conn)
    conn.close()
    return StubSettings(
        db_file=db_file,
        data_dir=tmp_path / "data",
    )


def _app(s: StubSettings):
    return server.create_app(s, StubService())


def _add_member(conn: sqlite3.Connection, account_id: int, username: str, status: str = "active") -> None:
    now = time.time()
    conn.execute(
        "INSERT INTO accounts (id, public_id, username, role, status, display_name, profile_public, created_at, updated_at) "
        "VALUES (?, ?, ?, 'member', ?, ?, 0, ?, ?)",
        (account_id, (1 << 57) + account_id, username, status, username.capitalize(), now, now),
    )
    conn.commit()


def test_token_exchange_missing_subject_token(tmp_path: Path) -> None:
    s = _settings(tmp_path)
    with TestClient(_app(s), base_url="http://127.0.0.1:8765") as client:
        resp = client.post(
            "/oauth/token",
            data={"grant_type": "urn:ietf:params:oauth:grant-type:token-exchange"},
        )
        assert resp.status_code == 400
        data = resp.json()
        assert data["error"] == "invalid_request"
        assert data["error_description"] == "Missing subject_token"


def test_token_exchange_valid_bound_session_token_admin(tmp_path: Path) -> None:
    s = _settings(tmp_path)
    conn = dbm.connect(s.db_file)
    admin_session = accounts_store.create_account_session(conn, account_id=1, scope="owner")
    conn.close()

    with TestClient(_app(s), base_url="http://127.0.0.1:8765") as client:
        resp = client.post(
            "/oauth/token",
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
                "subject_token": admin_session,
                "subject_token_type": "urn:ietf:params:oauth:token-type:access_token",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert "refresh_token" in data
        assert data["issued_token_type"] == "urn:ietf:params:oauth:token-type:access_token"
        assert data["token_type"] == "Bearer"
        assert data["scope"] == "readonly"

        access_token = data["access_token"]
        conn = dbm.connect(s.db_file)
        row = conn.execute("SELECT account_id, scope FROM oauth_tokens WHERE token=?", (access_token,)).fetchone()
        conn.close()
        assert row is not None
        assert row[0] == 1
        assert row[1] == "readonly"


def test_token_exchange_valid_bound_session_token_member(tmp_path: Path) -> None:
    s = _settings(tmp_path)
    conn = dbm.connect(s.db_file)
    _add_member(conn, 2, "alice", status="active")
    member_session = accounts_store.create_account_session(conn, account_id=2, scope="collaborator")
    conn.close()

    with TestClient(_app(s), base_url="http://127.0.0.1:8765") as client:
        resp = client.post(
            "/oauth/token",
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
                "subject_token": member_session,
                "subject_token_type": "urn:ietf:params:oauth:token-type:access_token",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert "refresh_token" in data
        access_token = data["access_token"]

        conn = dbm.connect(s.db_file)
        row = conn.execute("SELECT account_id, scope FROM oauth_tokens WHERE token=?", (access_token,)).fetchone()
        binding = accounts_store.validate_oauth_binding(conn, access_token)
        conn.close()

        assert row is not None
        assert row[0] == 2
        assert row[0] != 1
        assert binding is not None
        assert binding.account_id == 2
        assert binding.account_id != 1


def test_token_exchange_unbound_session_token_rejected(tmp_path: Path) -> None:
    s = _settings(tmp_path)
    app = _app(s)
    conn = dbm.connect(s.db_file)
    tok = accounts_store.create_account_session(conn, account_id=1, scope="owner")
    conn.execute("UPDATE auth_sessions SET account_id=NULL WHERE session_token=?", (tok,))
    conn.commit()

    initial_tokens_count = conn.execute("SELECT COUNT(*) FROM oauth_tokens").fetchone()[0]
    conn.close()

    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        resp = client.post(
            "/oauth/token",
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
                "subject_token": tok,
                "subject_token_type": "urn:ietf:params:oauth:token-type:access_token",
            },
        )
        assert resp.status_code == 400
        data = resp.json()
        assert data["error"] == "invalid_grant"
        assert data["error_description"] == "Subject token is invalid, expired, or unbound"

    conn = dbm.connect(s.db_file)
    final_tokens_count = conn.execute("SELECT COUNT(*) FROM oauth_tokens").fetchone()[0]
    conn.close()
    assert final_tokens_count == initial_tokens_count


def test_token_exchange_invalid_subject_token_rejected(tmp_path: Path) -> None:
    s = _settings(tmp_path)
    with TestClient(_app(s), base_url="http://127.0.0.1:8765") as client:
        resp = client.post(
            "/oauth/token",
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
                "subject_token": "non_existent_token_1234567890abcdef12345678",
                "subject_token_type": "urn:ietf:params:oauth:token-type:access_token",
            },
        )
        assert resp.status_code == 400
        data = resp.json()
        assert data["error"] == "invalid_grant"
        assert data["error_description"] == "Subject token is invalid, expired, or unbound"


def test_token_exchange_expired_subject_token_rejected(tmp_path: Path) -> None:
    s = _settings(tmp_path)
    conn = dbm.connect(s.db_file)
    tok = accounts_store.create_account_session(conn, account_id=1, scope="owner")
    conn.execute("UPDATE auth_sessions SET expires_at=? WHERE session_token=?", (time.time() - 3600, tok))
    conn.commit()
    conn.close()

    with TestClient(_app(s), base_url="http://127.0.0.1:8765") as client:
        resp = client.post(
            "/oauth/token",
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
                "subject_token": tok,
                "subject_token_type": "urn:ietf:params:oauth:token-type:access_token",
            },
        )
        assert resp.status_code == 400
        data = resp.json()
        assert data["error"] == "invalid_grant"
        assert data["error_description"] == "Subject token is invalid, expired, or unbound"


def test_token_exchange_unapproved_subject_token_rejected(tmp_path: Path) -> None:
    s = _settings(tmp_path)
    conn = dbm.connect(s.db_file)
    tok = accounts_store.create_account_session(conn, account_id=1, scope="owner")
    conn.execute("UPDATE auth_sessions SET approved=0 WHERE session_token=?", (tok,))
    conn.commit()
    conn.close()

    with TestClient(_app(s), base_url="http://127.0.0.1:8765") as client:
        resp = client.post(
            "/oauth/token",
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
                "subject_token": tok,
                "subject_token_type": "urn:ietf:params:oauth:token-type:access_token",
            },
        )
        assert resp.status_code == 400
        data = resp.json()
        assert data["error"] == "invalid_grant"
        assert data["error_description"] == "Subject token is invalid, expired, or unbound"


def test_token_exchange_inactive_or_suspended_account_rejected(tmp_path: Path) -> None:
    s = _settings(tmp_path)
    conn = dbm.connect(s.db_file)
    _add_member(conn, 3, "charlie", status="suspended")
    _add_member(conn, 4, "dave", status="disabled")
    suspended_tok = accounts_store.create_account_session(conn, account_id=3, scope="collaborator")
    disabled_tok = accounts_store.create_account_session(conn, account_id=4, scope="collaborator")
    conn.close()

    with TestClient(_app(s), base_url="http://127.0.0.1:8765") as client:
        resp_suspended = client.post(
            "/oauth/token",
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
                "subject_token": suspended_tok,
                "subject_token_type": "urn:ietf:params:oauth:token-type:access_token",
            },
        )
        assert resp_suspended.status_code == 400
        assert resp_suspended.json()["error"] == "invalid_grant"
        assert resp_suspended.json()["error_description"] == "Account is suspended"

        resp_disabled = client.post(
            "/oauth/token",
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
                "subject_token": disabled_tok,
                "subject_token_type": "urn:ietf:params:oauth:token-type:access_token",
            },
        )
        assert resp_disabled.status_code == 400
        assert resp_disabled.json()["error"] == "invalid_grant"
        assert resp_disabled.json()["error_description"] == "Account is disabled"


def test_token_exchange_deleted_account_rejected(tmp_path: Path) -> None:
    s = _settings(tmp_path)
    conn = dbm.connect(s.db_file)
    _add_member(conn, 5, "eve", status="active")
    eve_tok = accounts_store.create_account_session(conn, account_id=5, scope="collaborator")
    conn.execute("DELETE FROM accounts WHERE id=5")
    conn.commit()
    conn.close()

    with TestClient(_app(s), base_url="http://127.0.0.1:8765") as client:
        resp = client.post(
            "/oauth/token",
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
                "subject_token": eve_tok,
                "subject_token_type": "urn:ietf:params:oauth:token-type:access_token",
            },
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_grant"
        assert resp.json()["error_description"] == "Account not found"


def test_token_exchange_non_admin_never_acquires_admin_account_id(tmp_path: Path) -> None:
    s = _settings(tmp_path)
    conn = dbm.connect(s.db_file)
    for account_id, username in ((10, "user10"), (11, "user11"), (12, "user12")):
        _add_member(conn, account_id, username, status="active")
    tokens = {
        account_id: accounts_store.create_account_session(conn, account_id=account_id, scope="collaborator")
        for account_id in (10, 11, 12)
    }
    conn.close()

    with TestClient(_app(s), base_url="http://127.0.0.1:8765") as client:
        for account_id, session_tok in tokens.items():
            resp = client.post(
                "/oauth/token",
                data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
                    "subject_token": session_tok,
                    "subject_token_type": "urn:ietf:params:oauth:token-type:access_token",
                },
            )
            assert resp.status_code == 200
            data = resp.json()
            access_token = data["access_token"]
            refresh_token = data["refresh_token"]

            conn = dbm.connect(s.db_file)
            row = conn.execute("SELECT account_id FROM oauth_tokens WHERE token=?", (access_token,)).fetchone()
            assert row is not None
            assert row[0] == account_id
            assert row[0] != 1

            binding = accounts_store.validate_oauth_binding(conn, access_token)
            assert binding is not None
            assert binding.account_id == account_id
            assert binding.account_id != 1
            conn.close()

            # Verify token rotation preserves the account_id and never mutates to admin account ID 1
            ref_resp = client.post(
                "/oauth/token",
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                },
            )
            assert ref_resp.status_code == 200
            new_access = ref_resp.json()["access_token"]
            conn = dbm.connect(s.db_file)
            new_row = conn.execute("SELECT account_id FROM oauth_tokens WHERE token=?", (new_access,)).fetchone()
            assert new_row is not None
            assert new_row[0] == account_id
            assert new_row[0] != 1
            conn.close()
