"""Adversarial Challenge Test Suite for Requirement R3 (Token Exchange Account Binding Integrity).

Target: RFC 8693 Token Exchange endpoint in src/claire/api/oauth.py.
Tested by: Challenger 1 (teamwork_challenger_m2_1) for Milestone M2.
"""

from __future__ import annotations

import concurrent.futures
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
class ChallengerSettings:
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


def _init_settings(tmp_path: Path) -> ChallengerSettings:
    db_file = tmp_path / "challenger_test.db"
    conn = dbm.connect(db_file)
    dbm.init_db(conn)
    conn.close()
    return ChallengerSettings(
        db_file=db_file,
        data_dir=tmp_path / "data",
    )


def _app(s: ChallengerSettings):
    return server.create_app(s, StubService())


def _add_account(
    conn: sqlite3.Connection,
    account_id: int,
    username: str,
    status: str = "active",
    role: str = "member",
) -> None:
    now = time.time()
    conn.execute(
        "INSERT INTO accounts (id, public_id, username, role, status, display_name, profile_public, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)",
        (account_id, (1 << 57) + account_id, username, role, status, username.capitalize(), now, now),
    )
    conn.commit()


# ============================================================================
# 1. Probe Legacy Unbound Session Tokens
# ============================================================================
def test_probe_legacy_unbound_session_token(tmp_path: Path) -> None:
    """Verify that session tokens with NULL account_id are strictly rejected."""
    s = _init_settings(tmp_path)
    app = _app(s)  # App creation executes DB migrations first

    conn = dbm.connect(s.db_file)
    tok = accounts_store.create_account_session(conn, account_id=1, scope="owner")
    # Simulate legacy or unbound session where account_id is NULL
    conn.execute("UPDATE auth_sessions SET account_id=NULL WHERE session_token=?", (tok,))
    conn.commit()
    token_count_before = conn.execute("SELECT COUNT(*) FROM oauth_tokens").fetchone()[0]
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
    token_count_after = conn.execute("SELECT COUNT(*) FROM oauth_tokens").fetchone()[0]
    conn.close()
    assert token_count_after == token_count_before


def test_probe_session_token_with_nonexistent_account_id(tmp_path: Path) -> None:
    """Verify that session tokens bound to a non-existent account ID are rejected."""
    s = _init_settings(tmp_path)
    app = _app(s)

    conn = dbm.connect(s.db_file)
    tok = accounts_store.create_account_session(conn, account_id=1, scope="owner")
    # Point session to non-existent account ID 99999
    conn.execute("UPDATE auth_sessions SET account_id=99999 WHERE session_token=?", (tok,))
    conn.commit()
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
        assert data["error_description"] == "Account not found"


# ============================================================================
# 2. Probe Non-Existent, Malformed, and Adversarial Token Strings
# ============================================================================
@pytest.mark.parametrize(
    "bad_token",
    [
        "non_existent_token_string_1234567890abcdef",
        "invalid' OR '1'='1",
        "'; DROP TABLE oauth_tokens; --",
        "🔥💎🔑🚀",
        "A" * 4096,
        "token\x00nullbyte",
        "Bearer some_bearer_token_string",
    ],
)
def test_probe_non_existent_and_adversarial_tokens(tmp_path: Path, bad_token: str) -> None:
    """Verify adversarial and malformed subject tokens fail gracefully without SQL injection or leaks."""
    s = _init_settings(tmp_path)
    with TestClient(_app(s), base_url="http://127.0.0.1:8765") as client:
        resp = client.post(
            "/oauth/token",
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
                "subject_token": bad_token,
                "subject_token_type": "urn:ietf:params:oauth:token-type:access_token",
            },
        )
        assert resp.status_code == 400
        data = resp.json()
        assert data["error"] == "invalid_grant"
        assert data["error_description"] == "Subject token is invalid, expired, or unbound"


# ============================================================================
# 3. Probe Empty, Missing, Null, and Varied Parameter Payloads
# ============================================================================
def test_probe_missing_and_empty_subject_token_form(tmp_path: Path) -> None:
    """Probe form-encoded missing, empty, and whitespace subject_token."""
    s = _init_settings(tmp_path)
    with TestClient(_app(s), base_url="http://127.0.0.1:8765") as client:
        # Missing parameter completely
        r1 = client.post("/oauth/token", data={"grant_type": "urn:ietf:params:oauth:grant-type:token-exchange"})
        assert r1.status_code == 400
        assert r1.json()["error"] == "invalid_request"
        assert r1.json()["error_description"] == "Missing subject_token"

        # Empty string
        r2 = client.post("/oauth/token", data={"grant_type": "urn:ietf:params:oauth:grant-type:token-exchange", "subject_token": ""})
        assert r2.status_code == 400
        assert r2.json()["error"] == "invalid_request"
        assert r2.json()["error_description"] == "Missing subject_token"

        # Whitespace string
        r3 = client.post("/oauth/token", data={"grant_type": "urn:ietf:params:oauth:grant-type:token-exchange", "subject_token": "    "})
        assert r3.status_code == 400
        assert r3.json()["error"] == "invalid_grant"


def test_probe_json_payloads_with_null_and_types(tmp_path: Path) -> None:
    """Probe JSON payloads with null, boolean, integer, list, dict as subject_token."""
    s = _init_settings(tmp_path)
    with TestClient(_app(s), base_url="http://127.0.0.1:8765") as client:
        # null subject_token
        r_null = client.post(
            "/oauth/token",
            json={"grant_type": "urn:ietf:params:oauth:grant-type:token-exchange", "subject_token": None},
        )
        assert r_null.status_code == 400
        assert r_null.json()["error"] == "invalid_request"

        # empty object
        r_empty = client.post(
            "/oauth/token",
            json={"grant_type": "urn:ietf:params:oauth:grant-type:token-exchange"},
        )
        assert r_empty.status_code == 400
        assert r_empty.json()["error"] == "invalid_request"

        # integer subject_token
        r_int = client.post(
            "/oauth/token",
            json={"grant_type": "urn:ietf:params:oauth:grant-type:token-exchange", "subject_token": 12345},
        )
        assert r_int.status_code == 400
        assert r_int.json()["error"] == "invalid_grant"

        # boolean subject_token
        r_bool = client.post(
            "/oauth/token",
            json={"grant_type": "urn:ietf:params:oauth:grant-type:token-exchange", "subject_token": False},
        )
        assert r_bool.status_code == 400
        assert r_bool.json()["error"] == "invalid_request"

        # list subject_token
        r_list = client.post(
            "/oauth/token",
            json={"grant_type": "urn:ietf:params:oauth:grant-type:token-exchange", "subject_token": ["a", "b"]},
        )
        assert r_list.status_code == 400

        # dict subject_token
        r_dict = client.post(
            "/oauth/token",
            json={"grant_type": "urn:ietf:params:oauth:grant-type:token-exchange", "subject_token": {"token": "xyz"}},
        )
        assert r_dict.status_code == 400


# ============================================================================
# 4. Probe Accounts with Inactive, Suspended, or Non-credential Statuses
# ============================================================================
@pytest.mark.parametrize(
    "status",
    [
        "suspended",
        "disabled",
        "pending_verification",
        "archived",
        "locked",
        "deleted",
        "purged",
    ],
)
def test_probe_non_credential_accepting_statuses(tmp_path: Path, status: str) -> None:
    """Verify that accounts with status not in CREDENTIAL_ACCEPTING_STATUSES are rejected."""
    s = _init_settings(tmp_path)
    app = _app(s)

    conn = dbm.connect(s.db_file)
    account_id = 50
    _add_account(conn, account_id, f"user_{status}", status=status)
    tok = accounts_store.create_account_session(conn, account_id=account_id, scope="collaborator")
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
        assert f"Account is {status}" in data["error_description"]


# ============================================================================
# 5. Probe Non-Admin Member Account Binding & Identity Isolation
# ============================================================================
def test_probe_non_admin_member_account_binding_and_api_identity(tmp_path: Path) -> None:
    """Empirically verify non-admin member tokens are bound to member ID and never admin ID 1."""
    s = _init_settings(tmp_path)
    app = _app(s)

    conn = dbm.connect(s.db_file)
    member_id = 42
    _add_account(conn, member_id, "charlie", status="active", role="member")
    member_session = accounts_store.create_account_session(conn, account_id=member_id, scope="collaborator")
    conn.close()

    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
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
        access_token = data["access_token"]
        refresh_token = data["refresh_token"]

        # 1. Direct database verification
        conn = dbm.connect(s.db_file)
        row = conn.execute("SELECT account_id, scope FROM oauth_tokens WHERE token=?", (access_token,)).fetchone()
        assert row is not None
        assert row[0] == member_id
        assert row[0] != 1, "SECURITY CRITICAL: Token was bound to admin account ID 1!"
        assert row[1] == "readonly"

        # 2. Binding verification helper
        binding = accounts_store.validate_oauth_binding(conn, access_token)
        conn.close()
        assert binding is not None
        assert binding.account_id == member_id
        assert binding.account_id != 1

        # 3. Authenticated endpoint verification: calling /whoami with Bearer token
        whoami_resp = client.get(
            "/whoami",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert whoami_resp.status_code == 200
        whoami_data = whoami_resp.json()
        assert whoami_data["scope"] == "readonly"
        assert whoami_data["is_admin"] is False, "SECURITY CRITICAL: Non-admin member has is_admin=True!"

        # 4. Verify that readonly OAuth token is prevented from accessing owner-only /api/me
        me_resp = client.get(
            "/api/me",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        # 404 is Claire's standard response when security middleware denies insufficient scope
        assert me_resp.status_code == 404

        # 5. Token refresh preserves member account_id
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
        conn.close()
        assert new_row is not None
        assert new_row[0] == member_id
        assert new_row[0] != 1


# ============================================================================
# 6. Concurrency and Multi-Account Stress Test
# ============================================================================
def test_probe_concurrent_token_exchanges(tmp_path: Path) -> None:
    """Stress test concurrent token exchange across multiple distinct accounts."""
    s = _init_settings(tmp_path)
    app = _app(s)

    conn = dbm.connect(s.db_file)
    accounts = [
        (1, "admin", "owner"),
        (2, "alice", "collaborator"),
        (3, "bob", "collaborator"),
        (4, "carol", "collaborator"),
        (5, "dan", "collaborator"),
    ]
    for acc_id, uname, _ in accounts[1:]:
        _add_account(conn, acc_id, uname, status="active", role="member")

    sessions: dict[int, str] = {}
    for acc_id, _, scope in accounts:
        sessions[acc_id] = accounts_store.create_account_session(conn, account_id=acc_id, scope=scope)
    conn.close()

    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        def do_exchange(acc_id: int, session_token: str) -> tuple[int, int, str]:
            resp = client.post(
                "/oauth/token",
                data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
                    "subject_token": session_token,
                    "subject_token_type": "urn:ietf:params:oauth:token-type:access_token",
                },
            )
            assert resp.status_code == 200
            token = resp.json()["access_token"]
            return acc_id, resp.status_code, token

        tasks = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            for _ in range(4):  # 4 rounds x 5 accounts = 20 concurrent exchanges
                for acc_id in sessions:
                    tasks.append(executor.submit(do_exchange, acc_id, sessions[acc_id]))

        results = [f.result() for f in concurrent.futures.as_completed(tasks)]
        assert len(results) == 20

    conn = dbm.connect(s.db_file)
    for expected_acc_id, status_code, issued_token in results:
        assert status_code == 200
        row = conn.execute("SELECT account_id FROM oauth_tokens WHERE token=?", (issued_token,)).fetchone()
        assert row is not None
        assert row[0] == expected_acc_id, f"Token mismatch: expected account {expected_acc_id}, got {row[0]}"
        if expected_acc_id != 1:
            assert row[0] != 1
    conn.close()


# ============================================================================
# 7. Adversarial Failure Mode: DB Rollback & Update Failure Observation
# ============================================================================
def test_probe_rollback_and_update_failure(tmp_path: Path) -> None:
    """Investigate what happens if UPDATE oauth_tokens fails after create_oauth_token commits.

    This probe checks whether a failure during UPDATE leaves an orphaned token in oauth_tokens with account_id=1.
    """
    s = _init_settings(tmp_path)
    app = _app(s)

    conn = dbm.connect(s.db_file)
    _add_account(conn, 2, "alice", status="active")
    member_session = accounts_store.create_account_session(conn, account_id=2, scope="collaborator")

    # Install a trigger on oauth_tokens that will abort any UPDATE statement
    conn.execute(
        """
        CREATE TRIGGER test_abort_update
        BEFORE UPDATE ON oauth_tokens
        BEGIN
            SELECT RAISE(ABORT, 'Simulated update failure for adversarial test');
        END;
        """
    )
    conn.commit()

    tokens_before = conn.execute("SELECT token, account_id FROM oauth_tokens").fetchall()
    conn.close()

    with TestClient(app, base_url="http://127.0.0.1:8765", raise_server_exceptions=False) as client:
        resp = client.post(
            "/oauth/token",
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
                "subject_token": member_session,
                "subject_token_type": "urn:ietf:params:oauth:token-type:access_token",
            },
        )
        # Server returns 500 because the UPDATE failed with OperationalError
        assert resp.status_code == 500

    # Now inspect the database
    conn = dbm.connect(s.db_file)
    tokens_after = conn.execute("SELECT token, account_id, client_id FROM oauth_tokens").fetchall()
    conn.close()

    # Empirical observation:
    # An orphaned token was inserted by create_oauth_token with account_id=1
    orphaned_tokens = [t for t in tokens_after if t not in tokens_before]

    # The caller did NOT get the token (HTTP 500 with no body containing the token).
    # However, create_oauth_token committed account_id=1 before UPDATE occurred.
    assert len(orphaned_tokens) == 1
    orphaned = orphaned_tokens[0]
    assert orphaned[1] == 1, "Orphaned token has account_id 1 because create_oauth_token commits ADMIN_ACCOUNT_ID"


# ============================================================================
# 8. Scope Escalation Probe
# ============================================================================
def test_probe_scope_escalation_impossible(tmp_path: Path) -> None:
    """Verify that token exchange always yields readonly scope regardless of session scope."""
    s = _init_settings(tmp_path)
    app = _app(s)

    conn = dbm.connect(s.db_file)
    # Admin session has scope='owner'
    admin_session = accounts_store.create_account_session(conn, account_id=1, scope="owner")
    conn.close()

    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        resp = client.post(
            "/oauth/token",
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
                "subject_token": admin_session,
                "subject_token_type": "urn:ietf:params:oauth:token-type:access_token",
                "scope": "owner admin write",  # Attacker attempts to request higher scopes
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["scope"] == "readonly"
        access_token = data["access_token"]

        conn = dbm.connect(s.db_file)
        row = conn.execute("SELECT scope FROM oauth_tokens WHERE token=?", (access_token,)).fetchone()
        conn.close()
        assert row[0] == "readonly"


# ============================================================================
# 9. Additional Adversarial Invalidation & Input Probes
# ============================================================================
def test_probe_oauth_token_immediately_invalidated_when_account_suspended(tmp_path: Path) -> None:
    """Verify defense-in-depth: suspending an account immediately revokes all issued tokens."""
    s = _init_settings(tmp_path)
    app = _app(s)

    conn = dbm.connect(s.db_file)
    _add_account(conn, 2, "alice", status="active")
    member_session = accounts_store.create_account_session(conn, account_id=2, scope="collaborator")
    conn.close()

    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        resp = client.post(
            "/oauth/token",
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
                "subject_token": member_session,
                "subject_token_type": "urn:ietf:params:oauth:token-type:access_token",
            },
        )
        assert resp.status_code == 200
        access_token = resp.json()["access_token"]
        refresh_token = resp.json()["refresh_token"]

        # Token works initially
        r_ok = client.get("/whoami", headers={"Authorization": f"Bearer {access_token}"})
        assert r_ok.status_code == 200

        # Suspend the account in the database
        conn = dbm.connect(s.db_file)
        conn.execute("UPDATE accounts SET status='suspended' WHERE id=2")
        conn.commit()
        conn.close()

        # Token must immediately be rejected on /whoami (404/401 because credential validation fails)
        r_fail = client.get("/whoami", headers={"Authorization": f"Bearer {access_token}"})
        assert r_fail.status_code in (401, 404)

        # Refresh must also fail
        r_ref = client.post("/oauth/token", data={"grant_type": "refresh_token", "refresh_token": refresh_token})
        assert r_ref.status_code == 400
        assert r_ref.json()["error"] == "invalid_grant"
        assert r_ref.json()["error_description"] == "Account is suspended"


def test_probe_oauth_token_cannot_be_used_as_subject_token(tmp_path: Path) -> None:
    """Verify that an OAuth token itself cannot be used as a subject_token in token exchange."""
    s = _init_settings(tmp_path)
    app = _app(s)

    conn = dbm.connect(s.db_file)
    tok1 = accounts_store.create_account_session(conn, account_id=1, scope="owner")
    conn.close()

    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        # Obtain valid OAuth access token
        resp1 = client.post(
            "/oauth/token",
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
                "subject_token": tok1,
                "subject_token_type": "urn:ietf:params:oauth:token-type:access_token",
            },
        )
        assert resp1.status_code == 200
        oauth_access_token = resp1.json()["access_token"]

        # Attempt to use the issued OAuth access token as subject_token for another exchange
        resp2 = client.post(
            "/oauth/token",
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
                "subject_token": oauth_access_token,
                "subject_token_type": "urn:ietf:params:oauth:token-type:access_token",
            },
        )
        assert resp2.status_code == 400
        assert resp2.json()["error"] == "invalid_grant"
        assert resp2.json()["error_description"] == "Subject token is invalid, expired, or unbound"

