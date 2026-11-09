"""Empirical challenge tests for Requirement R1.

This test file verifies:
1. PATCH /api/me boundary and invalid values.
2. Extra unexpected keys and valid key combinations.
3. SQLite database consistency, data types, and updated_at timestamps.
4. Console rendering in single mode vs network mode.
5. Member accounts, unauthenticated requests, and contract seals.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from starlette.testclient import TestClient

from claire.api import server
from claire.store import accounts as accounts_store
from claire.store import auth_db
from claire.store import db as dbm

PUBLIC_URL = "http://127.0.0.1:8765"
ORIGIN = PUBLIC_URL
USERNAME = "admin_user"
PASSWORD = "challenge-password-1234"


@dataclass
class _Settings:
    db_file: Path
    data_dir: Path
    environment: str = "development"
    public_url: str = PUBLIC_URL
    inject_token: str = "owner-token-32-chars-long-1234567"
    readonly_token: str = "readonly-token-32-chars-long-123"
    cors_allowed_origins: str = ""
    anonymous_readonly: bool = False


class _Client:
    def __init__(self, client: TestClient) -> None:
        self.client = client

    def request(
        self,
        method: str,
        path: str,
        *,
        session: str | None = None,
        bearer: str | None = None,
        **kwargs: Any,
    ):
        headers = dict(kwargs.pop("headers", {}) or {})
        if session is not None:
            headers["Cookie"] = f"claire_session={session}"
        if bearer is not None:
            headers["Authorization"] = f"Bearer {bearer}"
        if method not in {"GET", "HEAD"}:
            headers["Origin"] = ORIGIN
        self.client.cookies.clear()
        response = self.client.request(
            method, path, headers=headers, follow_redirects=False, **kwargs
        )
        self.client.cookies.clear()
        return response


@pytest.fixture
def settings(tmp_path: Path) -> _Settings:
    return _Settings(db_file=tmp_path / "claire.db", data_dir=tmp_path)


@pytest.fixture
def client(settings: _Settings):
    app = server.create_app(
        settings, SimpleNamespace(provider=SimpleNamespace(name="stub"))
    )
    with TestClient(app, base_url=PUBLIC_URL) as tc:
        yield _Client(tc)


def _db(settings: _Settings):
    return dbm.connect_existing(settings.db_file)


def _auth(settings: _Settings):
    return auth_db.connect_auth_db(auth_db.auth_db_path(settings.data_dir))


def _activate_admin(settings: _Settings, *, with_password: bool = True) -> None:
    conn = _db(settings)
    auth = _auth(settings)
    try:
        accounts_store.AccountStore(conn).complete_setup(
            1, username=USERNAME, display_name="관리자"
        )
        if with_password:
            auth_db.set_password(auth, 1, PASSWORD, username=USERNAME)
    finally:
        auth.close()
        conn.close()


def _session(
    settings: _Settings,
    account_id: int = 1,
    *,
    authenticated_ago: float = 0.0,
    scope: str = "owner",
) -> str:
    conn = _db(settings)
    try:
        return accounts_store.create_account_session(
            conn, account_id=account_id, scope=scope, now=time.time() - authenticated_ago
        )
    finally:
        conn.close()


def _add_member(settings: _Settings, username: str = "member_user") -> int:
    conn = _db(settings)
    try:
        cur = conn.execute(
            "INSERT INTO accounts (public_id, username, role, status, display_name, profile_public, created_at, updated_at) "
            "VALUES (?, ?, 'member', 'active', '일반회원', 0, 1000.0, 1000.0)",
            (987654321098, username),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def test_challenge_patch_me_boundary_and_invalid_values(client: _Client, settings: _Settings):
    """Stress-test PATCH /api/me with valid, boundary, and hostile values."""
    _activate_admin(settings)
    token = _session(settings)

    # 1. Baseline GET /api/me
    get_res = client.request("GET", "/api/me", session=token)
    assert get_res.status_code == 200
    baseline = get_res.json()
    assert baseline["profile_public"] == 0
    assert isinstance(baseline["profile_public"], int)

    # 2. Valid values: True, False, 1, 0
    valid_cases = [
        (True, 1),
        (False, 0),
        (1, 1),
        (0, 0),
        (True, 1),
        (0, 0),
    ]
    for input_val, expected_out in valid_cases:
        res = client.request("PATCH", "/api/me", session=token, json={"profile_public": input_val})
        assert res.status_code == 200, f"Failed on valid input: {input_val!r}"
        data = res.json()
        assert data["profile_public"] == expected_out
        assert isinstance(data["profile_public"], int)
        # Verify persistence via GET
        chk = client.request("GET", "/api/me", session=token).json()
        assert chk["profile_public"] == expected_out

    # 3. Invalid integer values: out of {0, 1} range
    invalid_integers = [2, -1, 999, -999, 2147483647, -2147483648]
    for val in invalid_integers:
        res = client.request("PATCH", "/api/me", session=token, json={"profile_public": val})
        assert res.status_code == 400, f"Expected 400 for integer: {val}"
        err = res.json()
        assert err.get("error") == "invalid_profile_public"

    # 4. Invalid float values: floats must NOT be coerced to ints
    invalid_floats = [1.0, 0.0, 1.5, -0.5, 0.999]
    for val in invalid_floats:
        res = client.request("PATCH", "/api/me", session=token, json={"profile_public": val})
        assert res.status_code == 400, f"Expected 400 for float: {val}"
        assert res.json().get("error") == "invalid_profile_public"

    # 5. Invalid string values: strings must NOT be coerced to ints or bools
    invalid_strings = ["true", "false", "1", "0", "True", "False", "", "None", "null", "yes", "no"]
    for val in invalid_strings:
        res = client.request("PATCH", "/api/me", session=token, json={"profile_public": val})
        assert res.status_code == 400, f"Expected 400 for string: {val}"
        assert res.json().get("error") == "invalid_profile_public"

    # 6. Invalid complex/collection types
    invalid_types = [None, {}, [], [0], [1], {"value": 1}]
    for val in invalid_types:
        res = client.request("PATCH", "/api/me", session=token, json={"profile_public": val})
        assert res.status_code == 400, f"Expected 400 for type: {type(val)}: {val}"
        assert res.json().get("error") == "invalid_profile_public"

    # 7. Unexpected extra keys (tampering and privilege escalation attempts)
    extra_key_payloads = [
        {"profile_public": 1, "extra": "unexpected"},
        {"profile_public": 1, "role": "admin"},
        {"profile_public": 0, "status": "active"},
        {"profile_public": 1, "public_id": 99999999},
        {"profile_public": 1, "username": "new_admin"},
        {"profile_public": 1, "has_password": False},
        {"profile_public": 0, "scope": "owner"},
    ]
    for payload in extra_key_payloads:
        res = client.request("PATCH", "/api/me", session=token, json=payload)
        assert res.status_code == 400, f"Expected 400 for extra keys: {payload}"
        assert res.json().get("error") == "invalid_request"

    # 8. Empty payload and non-dict payload
    empty_res = client.request("PATCH", "/api/me", session=token, json={})
    assert empty_res.status_code == 400
    assert empty_res.json().get("error") == "invalid_request"

    list_res = client.request("PATCH", "/api/me", session=token, json=[{"profile_public": 1}])
    assert list_res.status_code == 400

    str_res = client.request("PATCH", "/api/me", session=token, content=b'"profile_public=1"', headers={"Content-Type": "application/json"})
    assert str_res.status_code == 400


def test_challenge_key_combinations(client: _Client, settings: _Settings):
    """Test updating only display_name, only profile_public, and both together."""
    _activate_admin(settings)
    token = _session(settings)

    # Reset state
    client.request("PATCH", "/api/me", session=token, json={"profile_public": 0, "display_name": "초기이름"})

    # Case A: Only display_name
    res_name = client.request("PATCH", "/api/me", session=token, json={"display_name": "새이름"})
    assert res_name.status_code == 200
    body = res_name.json()
    assert body["display_name"] == "새이름"
    assert body["profile_public"] == 0

    # Verify via GET
    get_a = client.request("GET", "/api/me", session=token).json()
    assert get_a["display_name"] == "새이름"
    assert get_a["profile_public"] == 0

    # Case B: Only profile_public
    res_pp = client.request("PATCH", "/api/me", session=token, json={"profile_public": 1})
    assert res_pp.status_code == 200
    body_b = res_pp.json()
    assert body_b["display_name"] == "새이름"
    assert body_b["profile_public"] == 1

    # Verify via GET
    get_b = client.request("GET", "/api/me", session=token).json()
    assert get_b["display_name"] == "새이름"
    assert get_b["profile_public"] == 1

    # Case C: Both display_name and profile_public
    res_both = client.request(
        "PATCH", "/api/me", session=token, json={"display_name": "최종이름", "profile_public": 0}
    )
    assert res_both.status_code == 200
    body_c = res_both.json()
    assert body_c["display_name"] == "최종이름"
    assert body_c["profile_public"] == 0

    get_c = client.request("GET", "/api/me", session=token).json()
    assert get_c["display_name"] == "최종이름"
    assert get_c["profile_public"] == 0


def test_challenge_database_consistency_and_timestamp(client: _Client, settings: _Settings):
    """Directly inspect SQLite database table to verify values and updated_at."""
    _activate_admin(settings)
    token = _session(settings)

    conn = _db(settings)
    try:
        # Check SQLite table schema definition
        schema_rows = conn.execute("PRAGMA table_info(accounts)").fetchall()
        cols = {row[1]: {"type": row[2], "notnull": row[3], "dflt_value": row[4]} for row in schema_rows}
        assert "profile_public" in cols
        assert cols["profile_public"]["notnull"] == 1
        assert "INTEGER" in cols["profile_public"]["type"].upper()

        # Check initial raw value
        cur = conn.execute("SELECT profile_public, updated_at FROM accounts WHERE id = 1")
        row = cur.fetchone()
        assert row is not None
        initial_val, initial_updated = row
        assert initial_val == 0
        assert isinstance(initial_val, int)
    finally:
        conn.close()

    # Sleep slightly to ensure monotonic timestamp advance
    time.sleep(0.02)

    # Update profile_public via PATCH
    res = client.request("PATCH", "/api/me", session=token, json={"profile_public": 1})
    assert res.status_code == 200

    conn = _db(settings)
    try:
        cur = conn.execute("SELECT profile_public, updated_at FROM accounts WHERE id = 1")
        row = cur.fetchone()
        assert row is not None
        val_after, updated_after = row
        assert val_after == 1
        assert isinstance(val_after, int)
        assert updated_after > initial_updated, f"updated_at was not increased: {updated_after} <= {initial_updated}"
    finally:
        conn.close()

    # Test AccountStore direct methods and error handling
    conn = _db(settings)
    try:
        store = accounts_store.AccountStore(conn)
        # Explicit timestamp
        fake_time = 999999.0
        acc = store.update_profile_public(1, 0, now=fake_time)
        assert acc.profile_public is False
        assert acc.updated_at == fake_time

        # Verify raw SQL row
        cur = conn.execute("SELECT profile_public, updated_at FROM accounts WHERE id = 1")
        row = cur.fetchone()
        assert row[0] == 0
        assert row[1] == fake_time

        # Invalid account ID should raise AccountNotFound
        with pytest.raises(accounts_store.AccountNotFound):
            store.update_profile_public(888888, 1)
    finally:
        conn.close()


def test_challenge_single_mode_vs_network_mode(client: _Client, settings: _Settings):
    """Verify console rendering in single mode vs network mode."""
    _activate_admin(settings)
    token = _session(settings)

    # Test in network mode
    conn = _db(settings)
    try:
        accounts_store.AccountStore(conn).set_setting("mode", "network", updated_by=1)
    finally:
        conn.close()

    # Set profile_public to 0
    client.request("PATCH", "/api/me", session=token, json={"profile_public": 0})

    res_net_0 = client.request("GET", "/settings/account", session=token)
    assert res_net_0.status_code == 200
    html_0 = res_net_0.text
    # Check toggle presence and unchecked state
    assert 'data-settings-toggle="profile-public"' in html_0
    assert '<input id="pref-profile-public" type="checkbox" switch data-settings-toggle="profile-public"' in html_0
    assert 'data-settings-toggle="profile-public" checked' not in html_0
    # Check status row
    assert '<dd data-account-field="profile_public">비공개</dd>' in html_0
    # Check that obsolete hint is gone
    assert "data-profile-public-hint" not in html_0

    # Toggle to 1
    client.request("PATCH", "/api/me", session=token, json={"profile_public": 1})

    res_net_1 = client.request("GET", "/settings/account", session=token)
    assert res_net_1.status_code == 200
    html_1 = res_net_1.text
    assert 'data-settings-toggle="profile-public" checked' in html_1
    assert '<dd data-account-field="profile_public">공개 (공개 디렉터리에 표시)</dd>' in html_1
    assert "data-profile-public-hint" not in html_1

    # Test in single mode
    conn = _db(settings)
    try:
        accounts_store.AccountStore(conn).set_setting("mode", "single", updated_by=1)
    finally:
        conn.close()

    res_single = client.request("GET", "/settings/account", session=token)
    assert res_single.status_code == 200
    html_single = res_single.text
    # In single mode, neither the toggle nor the status row must be rendered (Rule S9)
    assert 'data-settings-toggle="profile-public"' not in html_single
    assert 'data-account-field="profile_public"' not in html_single
    assert "pref-profile-public" not in html_single
    assert "data-profile-public-hint" not in html_single

    # Single mode backend contract: PATCH /api/me should still be valid and sealed
    res_patch_single = client.request("PATCH", "/api/me", session=token, json={"profile_public": 0})
    assert res_patch_single.status_code == 200
    assert res_patch_single.json()["profile_public"] == 0


def test_challenge_member_account_and_unauthenticated(client: _Client, settings: _Settings):
    """Verify non-admin member accounts can update profile_public, and unauthenticated requests are blocked."""
    _activate_admin(settings)
    member_id = _add_member(settings, username="regular_member")
    member_token = _session(settings, account_id=member_id, scope="owner")

    # Member GET /api/me
    m_get = client.request("GET", "/api/me", session=member_token)
    assert m_get.status_code == 200
    assert m_get.json()["role"] == "member"
    assert m_get.json()["profile_public"] == 0

    # Member PATCH /api/me
    m_patch = client.request("PATCH", "/api/me", session=member_token, json={"profile_public": 1})
    assert m_patch.status_code == 200
    assert m_patch.json()["profile_public"] == 1

    # Verify directly in DB
    conn = _db(settings)
    try:
        cur = conn.execute("SELECT profile_public FROM accounts WHERE id = ?", (member_id,))
        assert cur.fetchone()[0] == 1
    finally:
        conn.close()

    # Readonly session scope must be denied (Claire returns fail-closed 404)
    ro_token = _session(settings, account_id=member_id, scope="readonly")
    ro_patch = client.request("PATCH", "/api/me", session=ro_token, json={"profile_public": 0})
    assert ro_patch.status_code == 404

    # Unauthenticated PATCH request (fail-closed 404)
    unauth_patch = client.request("PATCH", "/api/me", json={"profile_public": 1})
    assert unauth_patch.status_code == 404

    # Invalid session token (fail-closed 404)
    bad_token_patch = client.request("PATCH", "/api/me", session="invalid-session-token", json={"profile_public": 1})
    assert bad_token_patch.status_code == 404


def test_challenge_atomic_validation_and_partial_failure(client: _Client, settings: _Settings):
    """Verify atomicity of input validation: partial errors must not apply partial updates."""
    _activate_admin(settings)
    token = _session(settings)

    # Setup known initial baseline
    res_init = client.request("PATCH", "/api/me", session=token, json={"display_name": "초기이름", "profile_public": 0})
    assert res_init.status_code == 200

    # 1. Invalid display_name (length > 64) with valid profile_public
    too_long_name = "A" * 65
    res_bad_name = client.request(
        "PATCH", "/api/me", session=token, json={"display_name": too_long_name, "profile_public": 1}
    )
    assert res_bad_name.status_code == 400
    assert res_bad_name.json().get("error") == "invalid_display_name"

    # State must be completely untouched
    me_chk1 = client.request("GET", "/api/me", session=token).json()
    assert me_chk1["display_name"] == "초기이름"
    assert me_chk1["profile_public"] == 0

    # 2. Valid display_name with invalid profile_public
    res_bad_pp = client.request(
        "PATCH", "/api/me", session=token, json={"display_name": "새이름", "profile_public": 999}
    )
    assert res_bad_pp.status_code == 400
    assert res_bad_pp.json().get("error") == "invalid_profile_public"

    # State must be completely untouched
    me_chk2 = client.request("GET", "/api/me", session=token).json()
    assert me_chk2["display_name"] == "초기이름"
    assert me_chk2["profile_public"] == 0


def test_challenge_sqlite_type_and_sql_injection(client: _Client, settings: _Settings):
    """Verify typeof in SQLite is integer, response type is int (not bool), and SQL injection fails safely."""
    _activate_admin(settings)
    token = _session(settings)

    # Test boolean True -> stored as integer 1
    res_bool = client.request("PATCH", "/api/me", session=token, json={"profile_public": True})
    assert res_bool.status_code == 200
    # Crucial check: JSON response type must be integer, NOT boolean
    assert type(res_bool.json()["profile_public"]) is int
    assert res_bool.json()["profile_public"] == 1

    # Check SQLite typeof
    conn = _db(settings)
    try:
        cur = conn.execute("SELECT typeof(profile_public), profile_public FROM accounts WHERE id = 1")
        col_type, val = cur.fetchone()
        assert col_type == "integer"
        assert val == 1
    finally:
        conn.close()

    # Test boolean False -> stored as integer 0
    res_false = client.request("PATCH", "/api/me", session=token, json={"profile_public": False})
    assert res_false.status_code == 200
    assert type(res_false.json()["profile_public"]) is int
    assert res_false.json()["profile_public"] == 0

    # Check SQLite typeof
    conn = _db(settings)
    try:
        cur = conn.execute("SELECT typeof(profile_public), profile_public FROM accounts WHERE id = 1")
        col_type, val = cur.fetchone()
        assert col_type == "integer"
        assert val == 0
    finally:
        conn.close()

    # Hostile SQL injection payloads
    sqli_payloads = [
        "1; DROP TABLE accounts; --",
        "0 OR 1=1",
        "' UNION SELECT 1, 1 --",
        "1' OR '1'='1",
    ]
    for sqli in sqli_payloads:
        res_sqli = client.request("PATCH", "/api/me", session=token, json={"profile_public": sqli})
        assert res_sqli.status_code == 400
        assert res_sqli.json().get("error") == "invalid_profile_public"

    # Confirm table accounts still intact
    conn = _db(settings)
    try:
        cur = conn.execute("SELECT count(*) FROM accounts")
        assert cur.fetchone()[0] >= 1
    finally:
        conn.close()


