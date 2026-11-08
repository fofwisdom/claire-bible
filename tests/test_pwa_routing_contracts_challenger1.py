"""Empirical stress-test suite by Challenger 1 for Milestone 2: Root Launch & Per-User Workspace Routing (/?pwa=1).

Covers all 5 core requirements from dispatch:
1. Authenticated member session: 302 Found, Location: /@{username}/?pwa=1, Cache-Control: no-store.
2. Authenticated admin session: 302 Found, Location: /@{admin_username}/?pwa=1, Cache-Control: no-store.
3. Anonymous + anonymous_readonly=True: 302 Found, Location: /@{admin_username}/?pwa=1, Cache-Control: no-store.
4. Anonymous + anonymous_readonly=False: 302 Found, Location: /login, Cache-Control: no-store.
5. HEAD requests to /?pwa=1 across all states: 302 Found, Location, Cache-Control: no-store, empty body.

Additional adversarial stress tests:
- Query string preservation (encoded values, multiple parameters, ordering).
- Sliding session expiration extension verification.
- Suspended/deleting account fallback to anonymous policy.
- Expired session fallback to anonymous policy.
- Unconfigured/suspended admin fallback to /login under anonymous_readonly=True.
- Single-mode isolation: 200 OK, no redirect.
- Target workspace functional verification following redirect.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
import pytest
from starlette.testclient import TestClient

from claire import ids
from claire.api.server import create_app
from claire.config import Settings
from claire.store import accounts as accounts_store
from claire.store import db as dbm
from claire.store.token_store import reset_token_stores


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch):
    for name in list(os.environ):
        if name.startswith("CLAIRE_"):
            monkeypatch.delenv(name, raising=False)
    reset_token_stores()
    yield
    reset_token_stores()


def _setup_challenger1_network_env(
    tmp_path: Path,
    *,
    anonymous_readonly: bool = True,
    admin_active: bool = True,
    admin_username: str = "superadmin",
):
    db_file = tmp_path / "claire.db"
    conn = dbm.connect(db_file)
    dbm.init_db(conn)
    now = time.time()
    salt = dbm.read_id_salt(conn)

    # 1. Admin setup (id=1)
    if admin_active:
        conn.execute(
            "UPDATE accounts SET username=?, status='active', activated_at=?, profile_public=1 WHERE id=1",
            (admin_username, now),
        )
    else:
        conn.execute(
            "UPDATE accounts SET username=NULL, status='setup_required', activated_at=NULL WHERE id=1"
        )

    # 2. Member setup: Alice (active member)
    alice_pub = ids.timestamp_id("accounts", salt=salt, unix_ms=int(now * 1000) + 1)
    cur = conn.execute(
        "INSERT INTO accounts (public_id, username, role, status, display_name, profile_public, created_at, activated_at, updated_at) "
        "VALUES (?, 'alice_tester', 'member', 'active', 'Alice Tester', 1, ?, ?, ?)",
        (alice_pub, now, now, now),
    )
    alice_id = int(cur.lastrowid)

    # Theme for Alice
    alice_theme_pub = ids.timestamp_id("themes", salt=salt, unix_ms=int(now * 1000) + 10)
    conn.execute(
        "INSERT INTO themes (id, seq, number, label, account_id, is_default, is_public, created_at, updated_at) "
        "VALUES (10, 10, ?, 'Alice Theme', ?, 1, 1, ?, ?)",
        (alice_theme_pub, alice_id, now, now),
    )

    # 3. Suspended member: Dave (status='suspended')
    dave_pub = ids.timestamp_id("accounts", salt=salt, unix_ms=int(now * 1000) + 2)
    cur = conn.execute(
        "INSERT INTO accounts (public_id, username, role, status, display_name, profile_public, created_at, activated_at, updated_at) "
        "VALUES (?, 'dave_suspended', 'member', 'suspended', 'Dave Suspended', 1, ?, ?, ?)",
        (dave_pub, now, now, now),
    )
    dave_id = int(cur.lastrowid)

    # 4. Deleting member: Eve (status='deleting')
    eve_pub = ids.timestamp_id("accounts", salt=salt, unix_ms=int(now * 1000) + 3)
    cur = conn.execute(
        "INSERT INTO accounts (public_id, username, role, status, display_name, profile_public, created_at, activated_at, updated_at) "
        "VALUES (?, 'eve_deleting', 'member', 'deleting', 'Eve Deleting', 1, ?, ?, ?)",
        (eve_pub, now, now, now),
    )
    eve_id = int(cur.lastrowid)

    # 5. Switch to network mode
    store = accounts_store.AccountStore(conn)
    store.set_setting("mode", "network", updated_by=1)
    conn.commit()

    # 6. Sessions
    alice_session = accounts_store.create_account_session(conn, account_id=alice_id, scope="owner", now=now)
    dave_session = accounts_store.create_account_session(conn, account_id=dave_id, scope="owner", now=now)
    eve_session = accounts_store.create_account_session(conn, account_id=eve_id, scope="owner", now=now)

    admin_session = None
    if admin_active:
        admin_session = accounts_store.create_account_session(conn, account_id=1, scope="owner", now=now)

    # Expired session for Alice (expired 1 hour ago)
    expired_token = accounts_store.create_account_session(conn, account_id=alice_id, scope="owner", now=now - 7200)
    conn.execute("UPDATE auth_sessions SET expires_at=? WHERE session_token=?", (now - 3600, expired_token))

    # Session in sliding window (< half TTL remaining)
    sliding_token = accounts_store.create_account_session(conn, account_id=alice_id, scope="owner", now=now - 40000)
    sliding_initial_expires = now + 1000.0  # < half of 12hr TTL
    conn.execute("UPDATE auth_sessions SET expires_at=? WHERE session_token=?", (sliding_initial_expires, sliding_token))

    conn.commit()
    conn.close()

    s = Settings(
        CLAIRE_DB_PATH=str(db_file),
        CLAIRE_VAULT_PATH=str(tmp_path / "vault"),
        CLAIRE_MULTI_THEME=True,
        CLAIRE_ENVIRONMENT="development",
        CLAIRE_PUBLIC_URL="http://127.0.0.1:8765",
        CLAIRE_ANONYMOUS_READONLY=anonymous_readonly,
    )
    return s, {
        "db_file": db_file,
        "alice_id": alice_id,
        "dave_id": dave_id,
        "eve_id": eve_id,
        "alice_session": alice_session,
        "dave_session": dave_session,
        "eve_session": eve_session,
        "admin_session": admin_session,
        "expired_token": expired_token,
        "sliding_token": sliding_token,
        "sliding_initial_expires": sliding_initial_expires,
        "admin_username": admin_username,
    }


def _setup_challenger1_single_env(tmp_path: Path, *, anonymous_readonly: bool = True):
    db_file = tmp_path / "single_claire.db"
    conn = dbm.connect(db_file)
    dbm.init_db(conn)
    now = time.time()
    conn.execute(
        "UPDATE accounts SET username='singleadmin', status='active', activated_at=? WHERE id=1",
        (now,),
    )
    admin_session = accounts_store.create_account_session(conn, account_id=1, scope="owner", now=now)
    conn.commit()
    conn.close()

    s = Settings(
        CLAIRE_DB_PATH=str(db_file),
        CLAIRE_VAULT_PATH=str(tmp_path / "vault"),
        CLAIRE_ENVIRONMENT="development",
        CLAIRE_PUBLIC_URL="http://127.0.0.1:8765",
        CLAIRE_ANONYMOUS_READONLY=anonymous_readonly,
    )
    return s, {"db_file": db_file, "admin_session": admin_session}


# ==============================================================================
# 1. Authenticated Member Session Test
# ==============================================================================

def test_challenger1_authenticated_member_redirect(tmp_path: Path):
    """Requirement 1: Authenticated member session on /?pwa=1
    Must return 302 Found, Location: /@{username}/?pwa=1, Cache-Control: no-store.
    """
    s, ids_dict = _setup_challenger1_network_env(tmp_path)
    app = create_app(s)
    client = TestClient(app, base_url=s.public_url, follow_redirects=False)
    client.cookies.set("claire_session", ids_dict["alice_session"])

    res = client.get("/?pwa=1")
    assert res.status_code == 302, f"Expected 302, got {res.status_code}"
    assert res.headers.get("location") == "/@alice_tester/?pwa=1"
    assert res.headers.get("cache-control") == "no-store"


# ==============================================================================
# 2. Authenticated Admin Session Test
# ==============================================================================

def test_challenger1_authenticated_admin_redirect(tmp_path: Path):
    """Requirement 2: Authenticated admin session on /?pwa=1
    Must return 302 Found, Location: /@{admin_username}/?pwa=1, Cache-Control: no-store.
    """
    s, ids_dict = _setup_challenger1_network_env(tmp_path, admin_username="superadmin")
    app = create_app(s)
    client = TestClient(app, base_url=s.public_url, follow_redirects=False)
    client.cookies.set("claire_session", ids_dict["admin_session"])

    res = client.get("/?pwa=1")
    assert res.status_code == 302, f"Expected 302, got {res.status_code}"
    assert res.headers.get("location") == "/@superadmin/?pwa=1"
    assert res.headers.get("cache-control") == "no-store"


# ==============================================================================
# 3. Anonymous + anonymous_readonly=True Test
# ==============================================================================

def test_challenger1_anonymous_readonly_true_redirect(tmp_path: Path):
    """Requirement 3: Anonymous request with anonymous_readonly=True on /?pwa=1
    Must return 302 Found, Location: /@{admin_username}/?pwa=1, Cache-Control: no-store.
    """
    s, _ = _setup_challenger1_network_env(tmp_path, anonymous_readonly=True, admin_username="superadmin")
    app = create_app(s)
    client = TestClient(app, base_url=s.public_url, follow_redirects=False)

    res = client.get("/?pwa=1")
    assert res.status_code == 302, f"Expected 302, got {res.status_code}"
    assert res.headers.get("location") == "/@superadmin/?pwa=1"
    assert res.headers.get("cache-control") == "no-store"


# ==============================================================================
# 4. Anonymous + anonymous_readonly=False Test
# ==============================================================================

def test_challenger1_anonymous_readonly_false_redirect(tmp_path: Path):
    """Requirement 4: Anonymous request with anonymous_readonly=False on /?pwa=1
    Must return 302 Found, Location: /login, Cache-Control: no-store.
    """
    s, _ = _setup_challenger1_network_env(tmp_path, anonymous_readonly=False)
    app = create_app(s)
    client = TestClient(app, base_url=s.public_url, follow_redirects=False)

    res = client.get("/?pwa=1")
    assert res.status_code == 302, f"Expected 302, got {res.status_code}"
    assert res.headers.get("location") == "/login"
    assert res.headers.get("cache-control") == "no-store"


# ==============================================================================
# 5. HEAD Requests Parity Test
# ==============================================================================

def test_challenger1_head_requests_parity(tmp_path: Path):
    """Requirement 5: HEAD requests on /?pwa=1
    Must return 302 Found, identical Location header, Cache-Control: no-store, and empty body.
    Empirically verified across all 4 authorization states.
    """
    # 5a. Member HEAD
    s_ro, ids_ro = _setup_challenger1_network_env(tmp_path / "ro", anonymous_readonly=True, admin_username="superadmin")
    app_ro = create_app(s_ro)
    client_ro = TestClient(app_ro, base_url=s_ro.public_url, follow_redirects=False)

    client_ro.cookies.set("claire_session", ids_ro["alice_session"])
    res_member = client_ro.head("/?pwa=1")
    assert res_member.status_code == 302
    assert res_member.headers.get("location") == "/@alice_tester/?pwa=1"
    assert res_member.headers.get("cache-control") == "no-store"
    assert res_member.text == ""
    assert len(res_member.content) == 0

    # 5b. Admin HEAD
    client_ro.cookies.set("claire_session", ids_ro["admin_session"])
    res_admin = client_ro.head("/?pwa=1")
    assert res_admin.status_code == 302
    assert res_admin.headers.get("location") == "/@superadmin/?pwa=1"
    assert res_admin.headers.get("cache-control") == "no-store"
    assert res_admin.text == ""
    assert len(res_admin.content) == 0

    # 5c. Anonymous + readonly=True HEAD
    client_ro.cookies.clear()
    res_anon_ro = client_ro.head("/?pwa=1")
    assert res_anon_ro.status_code == 302
    assert res_anon_ro.headers.get("location") == "/@superadmin/?pwa=1"
    assert res_anon_ro.headers.get("cache-control") == "no-store"
    assert res_anon_ro.text == ""
    assert len(res_anon_ro.content) == 0

    # 5d. Anonymous + readonly=False HEAD
    s_noro, _ = _setup_challenger1_network_env(tmp_path / "noro", anonymous_readonly=False)
    app_noro = create_app(s_noro)
    client_noro = TestClient(app_noro, base_url=s_noro.public_url, follow_redirects=False)

    res_anon_noro = client_noro.head("/?pwa=1")
    assert res_anon_noro.status_code == 302
    assert res_anon_noro.headers.get("location") == "/login"
    assert res_anon_noro.headers.get("cache-control") == "no-store"
    assert res_anon_noro.text == ""
    assert len(res_anon_noro.content) == 0


# ==============================================================================
# 6. Adversarial Query String Preservation
# ==============================================================================

@pytest.mark.parametrize(
    "raw_query,expected_query",
    [
        ("pwa=1&tab=reader", "pwa=1&tab=reader"),
        ("tab=reader&pwa=1", "tab=reader&pwa=1"),
        ("pwa=1&filter=starred&sort=asc", "pwa=1&filter=starred&sort=asc"),
        ("pwa=1&korean=%ED%95%9C%EA%B8%80", "pwa=1&korean=%ED%95%9C%EA%B8%80"),
        ("pwa=1&flag=&empty", "pwa=1&flag=&empty"),
        ("pwa=1&view=compact&view=expanded", "pwa=1&view=compact&view=expanded"),
    ],
)
def test_challenger1_query_parameter_preservation(tmp_path: Path, raw_query: str, expected_query: str):
    """Verify arbitrary query parameters are preserved in order and format across PWA 302 redirects."""
    s, ids_dict = _setup_challenger1_network_env(tmp_path)
    app = create_app(s)
    client = TestClient(app, base_url=s.public_url, follow_redirects=False)
    client.cookies.set("claire_session", ids_dict["alice_session"])

    res = client.get(f"/?{raw_query}")
    assert res.status_code == 302
    assert res.headers.get("location") == f"/@alice_tester/?{expected_query}"
    assert res.headers.get("cache-control") == "no-store"


# ==============================================================================
# 7. Sliding Session Expiration Stress Test
# ==============================================================================

def test_challenger1_sliding_session_expiration(tmp_path: Path):
    """Stress test sliding expiration during PWA root redirect.
    When a session has < half TTL remaining, validate_session_binding updates expires_at.
    Verify:
    1. No sqlite3 readonly operational error occurs.
    2. Session expires_at in the database actually increases.
    3. User is still correctly redirected to personal workspace.
    """
    s, ids_dict = _setup_challenger1_network_env(tmp_path)
    db_file = ids_dict["db_file"]
    sliding_token = ids_dict["sliding_token"]
    initial_expires = ids_dict["sliding_initial_expires"]

    app = create_app(s)
    client = TestClient(app, base_url=s.public_url, follow_redirects=False)
    client.cookies.set("claire_session", sliding_token)

    res = client.get("/?pwa=1")
    assert res.status_code == 302
    assert res.headers.get("location") == "/@alice_tester/?pwa=1"

    # Verify expires_at was updated in sqlite
    conn = dbm.connect_existing(db_file, readonly=True)
    row = conn.execute("SELECT expires_at FROM auth_sessions WHERE session_token=?", (sliding_token,)).fetchone()
    conn.close()
    assert row is not None
    assert row[0] > initial_expires, f"Expected expires_at > {initial_expires}, got {row[0]}"


# ==============================================================================
# 8. Account Status & Invalid Session Fallbacks
# ==============================================================================

def test_challenger1_suspended_account_fallback(tmp_path: Path):
    """Verify suspended accounts (Dave) cannot access personal workspace via PWA root."""
    s, ids_dict = _setup_challenger1_network_env(tmp_path, anonymous_readonly=False)
    app = create_app(s)
    client = TestClient(app, base_url=s.public_url, follow_redirects=False)
    client.cookies.set("claire_session", ids_dict["dave_session"])

    res = client.get("/?pwa=1")
    assert res.status_code == 302
    assert res.headers.get("location") == "/login"


def test_challenger1_deleting_account_fallback(tmp_path: Path):
    """Verify deleting accounts (Eve) cannot access personal workspace via PWA root."""
    s, ids_dict = _setup_challenger1_network_env(tmp_path, anonymous_readonly=False)
    app = create_app(s)
    client = TestClient(app, base_url=s.public_url, follow_redirects=False)
    client.cookies.set("claire_session", ids_dict["eve_session"])

    res = client.get("/?pwa=1")
    assert res.status_code == 302
    assert res.headers.get("location") == "/login"


def test_challenger1_expired_session_fallback(tmp_path: Path):
    """Verify expired session cookies fall back to anonymous routing rule."""
    # Under anonymous_readonly=True -> redirects to admin
    s_ro, ids_ro = _setup_challenger1_network_env(tmp_path / "ro", anonymous_readonly=True, admin_username="superadmin")
    app_ro = create_app(s_ro)
    client_ro = TestClient(app_ro, base_url=s_ro.public_url, follow_redirects=False)
    client_ro.cookies.set("claire_session", ids_ro["expired_token"])

    res_ro = client_ro.get("/?pwa=1")
    assert res_ro.status_code == 302
    assert res_ro.headers.get("location") == "/@superadmin/?pwa=1"

    # Under anonymous_readonly=False -> redirects to /login
    s_noro, ids_noro = _setup_challenger1_network_env(tmp_path / "noro", anonymous_readonly=False)
    app_noro = create_app(s_noro)
    client_noro = TestClient(app_noro, base_url=s_noro.public_url, follow_redirects=False)
    client_noro.cookies.set("claire_session", ids_noro["expired_token"])

    res_noro = client_noro.get("/?pwa=1")
    assert res_noro.status_code == 302
    assert res_noro.headers.get("location") == "/login"


# ==============================================================================
# 9. Admin Lifecycle Edge Case
# ==============================================================================

def test_challenger1_unconfigured_admin_fallback(tmp_path: Path):
    """When anonymous_readonly=True but admin is unconfigured (setup_required), fallback must be /login."""
    s, _ = _setup_challenger1_network_env(tmp_path, anonymous_readonly=True, admin_active=False)
    app = create_app(s)
    client = TestClient(app, base_url=s.public_url, follow_redirects=False)

    res = client.get("/?pwa=1")
    assert res.status_code == 302
    assert res.headers.get("location") == "/login"
    assert res.headers.get("cache-control") == "no-store"


# ==============================================================================
# 10. Single Mode Isolation
# ==============================================================================

def test_challenger1_single_mode_isolation(tmp_path: Path):
    """Verify single mode serves workspace HTML directly (200 OK) with NO redirects on /?pwa=1."""
    s, ids_dict = _setup_challenger1_single_env(tmp_path)
    app = create_app(s)
    client = TestClient(app, base_url=s.public_url, follow_redirects=False)

    # 1. Anonymous GET
    res_get = client.get("/?pwa=1")
    assert res_get.status_code == 200
    assert "text/html" in res_get.headers.get("content-type", "")

    # 2. Anonymous HEAD
    res_head = client.head("/?pwa=1")
    assert res_head.status_code == 200
    assert res_head.text == ""

    # 3. Authenticated GET
    client.cookies.set("claire_session", ids_dict["admin_session"])
    res_auth = client.get("/?pwa=1")
    assert res_auth.status_code == 200
    assert "text/html" in res_auth.headers.get("content-type", "")


# ==============================================================================
# 11. Functional Target Workspace Verification
# ==============================================================================

def test_challenger1_follow_redirect_to_workspace(tmp_path: Path):
    """Verify that following the 302 redirect from /?pwa=1 successfully reaches a 200 OK workspace."""
    s, ids_dict = _setup_challenger1_network_env(tmp_path)
    app = create_app(s)
    client = TestClient(app, base_url=s.public_url, follow_redirects=False)
    client.cookies.set("claire_session", ids_dict["alice_session"])

    res_root = client.get("/?pwa=1")
    assert res_root.status_code == 302
    target_url = res_root.headers.get("location")
    assert target_url == "/@alice_tester/?pwa=1"

    res_ws = client.get(target_url)
    assert res_ws.status_code == 200
    assert "text/html" in res_ws.headers.get("content-type", "")
    assert "claire" in res_ws.text.lower() or "지식 그래프" in res_ws.text
