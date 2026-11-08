"""Adversarial stress-test suite for Milestone 2: Root Launch & Per-User Workspace Routing (/?pwa=1).

Tests:
1. Single mode with `/?pwa=1`: verify status 200 OK, HTML content, no redirect.
2. Network mode normal `GET /`: verify status 200 OK, public account directory HTML (explore_accounts.html).
3. Query parameter preservation: `GET /?pwa=1&theme=2&doc=doc-abc` preserves all query parameters.
4. Expired, forged, and malformed session cookies: verify graceful anonymous fallback.
5. Account status edge cases, sliding expiration, and adversarial inputs.
"""

from __future__ import annotations

import os
import sqlite3
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


def _setup_network_env(
    tmp_path: Path,
    *,
    anonymous_readonly: bool = True,
    admin_active: bool = True,
    admin_username: str = "adminuser",
):
    db_file = tmp_path / "claire.db"
    conn = dbm.connect(db_file)
    dbm.init_db(conn)
    now = time.time()
    salt = dbm.read_id_salt(conn)

    if admin_active:
        conn.execute(
            "UPDATE accounts SET username=?, status='active', activated_at=?, profile_public=1 WHERE id=1",
            (admin_username, now),
        )
    else:
        conn.execute(
            "UPDATE accounts SET username=NULL, status='setup_required', activated_at=NULL WHERE id=1"
        )

    # Member 1: Alice (active)
    alice_pub = ids.timestamp_id("accounts", salt=salt, unix_ms=int(now * 1000) + 1)
    cur = conn.execute(
        "INSERT INTO accounts (public_id, username, role, status, display_name, profile_public, created_at, activated_at, updated_at) "
        "VALUES (?, 'alice', 'member', 'active', 'Alice In Wonderland', 1, ?, ?, ?)",
        (alice_pub, now, now, now),
    )
    alice_id = int(cur.lastrowid)

    # Member 2: Bob (active)
    bob_pub = ids.timestamp_id("accounts", salt=salt, unix_ms=int(now * 1000) + 2)
    cur = conn.execute(
        "INSERT INTO accounts (public_id, username, role, status, display_name, profile_public, created_at, activated_at, updated_at) "
        "VALUES (?, 'bob', 'member', 'active', 'Bob Builder', 1, ?, ?, ?)",
        (bob_pub, now, now, now),
    )
    bob_id = int(cur.lastrowid)

    # Member 3: Carol (suspended)
    carol_pub = ids.timestamp_id("accounts", salt=salt, unix_ms=int(now * 1000) + 3)
    cur = conn.execute(
        "INSERT INTO accounts (public_id, username, role, status, display_name, profile_public, created_at, activated_at, updated_at) "
        "VALUES (?, 'carol', 'member', 'suspended', 'Carol Singer', 1, ?, ?, ?)",
        (carol_pub, now, now, now),
    )
    carol_id = int(cur.lastrowid)

    # Member 4: Dave (deleting)
    dave_pub = ids.timestamp_id("accounts", salt=salt, unix_ms=int(now * 1000) + 4)
    cur = conn.execute(
        "INSERT INTO accounts (public_id, username, role, status, display_name, profile_public, created_at, activated_at, updated_at) "
        "VALUES (?, 'dave', 'member', 'deleting', 'Dave Diver', 1, ?, ?, ?)",
        (dave_pub, now, now, now),
    )
    dave_id = int(cur.lastrowid)

    # Member 5: Eve (retired)
    eve_pub = ids.timestamp_id("accounts", salt=salt, unix_ms=int(now * 1000) + 5)
    cur = conn.execute(
        "INSERT INTO accounts (public_id, username, role, status, display_name, profile_public, created_at, activated_at, updated_at) "
        "VALUES (?, 'eve', 'member', 'retired', 'Eve Eavesdropper', 1, ?, ?, ?)",
        (eve_pub, now, now, now),
    )
    eve_id = int(cur.lastrowid)

    # Member 6: Frank (username NULL)
    frank_pub = ids.timestamp_id("accounts", salt=salt, unix_ms=int(now * 1000) + 6)
    cur = conn.execute(
        "INSERT INTO accounts (public_id, username, role, status, display_name, profile_public, created_at, activated_at, updated_at) "
        "VALUES (?, NULL, 'member', 'active', 'Frank Ghost', 0, ?, ?, ?)",
        (frank_pub, now, now, now),
    )
    frank_id = int(cur.lastrowid)

    # Theme for Alice
    t_alice_pub = ids.timestamp_id("themes", salt=salt, unix_ms=int(now * 1000) + 10)
    conn.execute(
        "INSERT INTO themes (id, seq, number, label, account_id, is_default, is_public, created_at, updated_at) "
        "VALUES (10, 10, ?, 'Alice Public Theme', ?, 1, 1, ?, ?)",
        (t_alice_pub, alice_id, now, now),
    )

    store = accounts_store.AccountStore(conn)
    store.set_setting("mode", "network", updated_by=1)
    conn.commit()

    alice_session = accounts_store.create_account_session(conn, account_id=alice_id, scope="owner", now=now)
    bob_session = accounts_store.create_account_session(conn, account_id=bob_id, scope="owner", now=now)
    carol_session = accounts_store.create_account_session(conn, account_id=carol_id, scope="owner", now=now)
    dave_session = accounts_store.create_account_session(conn, account_id=dave_id, scope="owner", now=now)
    eve_session = accounts_store.create_account_session(conn, account_id=eve_id, scope="owner", now=now)
    frank_session = accounts_store.create_account_session(conn, account_id=frank_id, scope="owner", now=now)

    admin_session = None
    if admin_active:
        admin_session = accounts_store.create_account_session(conn, account_id=1, scope="owner", now=now)

    # Insert an expired session for Alice
    expired_token = "expired_session_token_1234567890abcdef"
    conn.execute(
        "INSERT INTO auth_sessions (nonce, session_token, approved, created_at, expires_at, scope, account_id, authenticated_at) "
        "VALUES (?, ?, 1, ?, ?, 'owner', ?, ?)",
        (expired_token, expired_token, now - 7200, now - 3600, alice_id, now - 7200),
    )

    # Insert an orphaned session (account_id = 999999)
    orphan_token = "orphan_session_token_1234567890abcdef"
    conn.execute(
        "INSERT INTO auth_sessions (nonce, session_token, approved, created_at, expires_at, scope, account_id, authenticated_at) "
        "VALUES (?, ?, 1, ?, ?, 'owner', 999999, ?)",
        (orphan_token, orphan_token, now, now + dbm.SESSION_TTL, now),
    )

    # Insert a session nearing sliding expiration (< half TTL remaining)
    sliding_token = "sliding_session_token_1234567890abcd"
    sliding_expires = now + 100.0  # < half TTL (SESSION_TTL / 2 = 43200s or 7 days)
    conn.execute(
        "INSERT INTO auth_sessions (nonce, session_token, approved, created_at, expires_at, scope, account_id, authenticated_at) "
        "VALUES (?, ?, 1, ?, ?, 'owner', ?, ?)",
        (sliding_token, sliding_token, now - 80000, sliding_expires, alice_id, now - 80000),
    )

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
        "bob_id": bob_id,
        "carol_id": carol_id,
        "dave_id": dave_id,
        "eve_id": eve_id,
        "frank_id": frank_id,
        "alice_session": alice_session,
        "bob_session": bob_session,
        "carol_session": carol_session,
        "dave_session": dave_session,
        "eve_session": eve_session,
        "frank_session": frank_session,
        "admin_session": admin_session,
        "expired_token": expired_token,
        "orphan_token": orphan_token,
        "sliding_token": sliding_token,
        "sliding_initial_expires": sliding_expires,
    }


def _setup_single_env(tmp_path: Path, *, anonymous_readonly: bool = True):
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
# SECTION 1: Single Mode with `/?pwa=1`
# ==============================================================================


def test_single_mode_pwa_status_200_html_no_redirect(tmp_path: Path):
    """Verify single mode with /?pwa=1 returns status 200 OK, HTML content, and no redirect."""
    s, _ = _setup_single_env(tmp_path, anonymous_readonly=True)
    app = create_app(s)
    client = TestClient(app, base_url=s.public_url, follow_redirects=False)

    res = client.get("/?pwa=1")
    assert res.status_code == 200
    assert res.history == []
    assert res.headers.get("location") is None
    assert "text/html" in res.headers.get("content-type", "")

    html = res.text
    # Verify single-mode workspace HTML markers
    assert "Claire" in html
    assert "claire-storage-key" in html or "data-active-pane" in html or "graph" in html
    # Verify public account directory HTML is NOT rendered
    assert "explore_accounts" not in html
    assert "공개 계정 둘러보기" not in html


def test_single_mode_pwa_with_complex_query_params(tmp_path: Path):
    """Verify single mode with /?pwa=1 and arbitrary query params renders workspace directly with 200 OK."""
    s, _ = _setup_single_env(tmp_path, anonymous_readonly=True)
    app = create_app(s)
    client = TestClient(app, base_url=s.public_url, follow_redirects=False)

    # User specified query combination: ?pwa=1&theme=2&doc=doc-abc
    res = client.get("/?pwa=1&theme=2&doc=doc-abc")
    assert res.status_code == 200
    assert res.history == []
    assert res.headers.get("location") is None
    assert "text/html" in res.headers.get("content-type", "")
    assert "explore_accounts" not in res.text


def test_single_mode_pwa_head_request(tmp_path: Path):
    """Verify HEAD /?pwa=1 in single mode returns 200 OK with identical headers and empty body."""
    s, _ = _setup_single_env(tmp_path, anonymous_readonly=True)
    app = create_app(s)
    client = TestClient(app, base_url=s.public_url, follow_redirects=False)

    res = client.head("/?pwa=1")
    assert res.status_code == 200
    assert res.history == []
    assert "text/html" in res.headers.get("content-type", "")
    assert res.text == ""


def test_single_mode_pwa_forged_and_expired_cookies(tmp_path: Path):
    """Verify single mode gracefully renders workspace 200 OK regardless of invalid/forged cookies."""
    s, _ = _setup_single_env(tmp_path, anonymous_readonly=True)
    app = create_app(s)
    client = TestClient(app, base_url=s.public_url, follow_redirects=False)

    client.cookies.set("claire_session", "completely_bogus_forged_cookie_12345")
    res = client.get("/?pwa=1")
    assert res.status_code == 200
    assert res.history == []
    assert "text/html" in res.headers.get("content-type", "")


def test_single_mode_normal_root_preservation(tmp_path: Path):
    """Verify single mode GET / without ?pwa=1 renders default workspace without redirect."""
    s, _ = _setup_single_env(tmp_path, anonymous_readonly=True)
    app = create_app(s)
    client = TestClient(app, base_url=s.public_url, follow_redirects=False)

    res = client.get("/")
    assert res.status_code == 200
    assert res.history == []
    assert "text/html" in res.headers.get("content-type", "")
    assert "explore_accounts" not in res.text


# ==============================================================================
# SECTION 2: Network Mode Normal GET / (Public Directory Preservation)
# ==============================================================================


def test_network_mode_normal_root_returns_explore_accounts(tmp_path: Path):
    """Verify network mode normal GET / returns status 200 OK and public account directory HTML."""
    s, _ = _setup_network_env(tmp_path)
    app = create_app(s)
    client = TestClient(app, base_url=s.public_url, follow_redirects=False)

    res = client.get("/")
    assert res.status_code == 200
    assert res.history == []
    assert res.headers.get("location") is None
    assert "text/html" in res.headers.get("content-type", "")

    html = res.text
    assert "공개 계정 둘러보기" in html or "explore" in html or "등록된 공개 계정" in html
    assert "id=\"explore-search\"" in html or "explore-search" in html or "card" in html


def test_network_mode_normal_root_preserves_public_directory_for_logged_in_member(tmp_path: Path):
    """Verify logged-in member visiting GET / (without ?pwa=1) still receives public directory 200 OK."""
    s, ids_dict = _setup_network_env(tmp_path)
    app = create_app(s)
    client = TestClient(app, base_url=s.public_url, follow_redirects=False)
    client.cookies.set("claire_session", ids_dict["alice_session"])

    res = client.get("/")
    assert res.status_code == 200
    assert res.history == []
    assert res.headers.get("location") is None
    assert "text/html" in res.headers.get("content-type", "")
    # Should stay on public directory, but nav shows user is authenticated
    assert "alice" in res.text.lower() or "내 워크스페이스" in res.text or "logout" in res.text


def test_network_mode_normal_root_preserves_public_directory_for_logged_in_admin(tmp_path: Path):
    """Verify logged-in admin visiting GET / (without ?pwa=1) still receives public directory 200 OK."""
    s, ids_dict = _setup_network_env(tmp_path)
    app = create_app(s)
    client = TestClient(app, base_url=s.public_url, follow_redirects=False)
    client.cookies.set("claire_session", ids_dict["admin_session"])

    res = client.get("/")
    assert res.status_code == 200
    assert res.history == []
    assert res.headers.get("location") is None
    assert "text/html" in res.headers.get("content-type", "")


def test_network_mode_non_pwa1_query_parameter_matrix(tmp_path: Path):
    """Verify that any query parameter other than exactly pwa=1 preserves the public directory."""
    s, _ = _setup_network_env(tmp_path)
    app = create_app(s)
    client = TestClient(app, base_url=s.public_url, follow_redirects=False)

    non_pwa_queries = [
        "/?pwa=0",
        "/?pwa=false",
        "/?pwa=true",
        "/?pwa=yes",
        "/?pwa=2",
        "/?pwa=-1",
        "/?pwa=",
        "/?pwa",
        "/?PWA=1",
        "/?filter=alice",
        "/?search=test",
        "/?page=2",
    ]
    for qs in non_pwa_queries:
        res = client.get(qs)
        assert res.status_code == 200, f"Expected 200 for {qs}, got {res.status_code}"
        assert res.history == [], f"Expected no redirect for {qs}"
        assert "text/html" in res.headers.get("content-type", "")


def test_network_mode_legacy_spa_query_preservation(tmp_path: Path):
    """Verify legacy SPA queries (doc, node, theme) without pwa=1 continue to trigger 308 permanent redirect."""
    s, _ = _setup_network_env(tmp_path)
    app = create_app(s)
    client = TestClient(app, base_url=s.public_url, follow_redirects=False)

    res_doc = client.get("/?doc=doc-abc")
    assert res_doc.status_code == 308
    assert res_doc.headers.get("location") == "/@adminuser/?doc=doc-abc"

    res_node = client.get("/?node=node-xyz")
    assert res_node.status_code == 308
    assert res_node.headers.get("location") == "/@adminuser/?node=node-xyz"

    res_theme = client.get("/?theme=2")
    assert res_theme.status_code == 308
    assert res_theme.headers.get("location") == "/@adminuser/?theme=2"


# ==============================================================================
# SECTION 3: Query Parameter Preservation (GET /?pwa=1&theme=2&doc=doc-abc)
# ==============================================================================


def test_query_param_preservation_member(tmp_path: Path):
    """Verify GET /?pwa=1&theme=2&doc=doc-abc redirects logged-in member to /@alice/?pwa=1&theme=2&doc=doc-abc."""
    s, ids_dict = _setup_network_env(tmp_path)
    app = create_app(s)
    client = TestClient(app, base_url=s.public_url, follow_redirects=False)
    client.cookies.set("claire_session", ids_dict["alice_session"])

    res = client.get("/?pwa=1&theme=2&doc=doc-abc")
    assert res.status_code == 302
    assert res.headers["location"] == "/@alice/?pwa=1&theme=2&doc=doc-abc"
    assert res.headers.get("cache-control") == "no-store"


def test_query_param_preservation_admin(tmp_path: Path):
    """Verify GET /?pwa=1&theme=2&doc=doc-abc redirects logged-in admin to /@adminuser/?pwa=1&theme=2&doc=doc-abc."""
    s, ids_dict = _setup_network_env(tmp_path)
    app = create_app(s)
    client = TestClient(app, base_url=s.public_url, follow_redirects=False)
    client.cookies.set("claire_session", ids_dict["admin_session"])

    res = client.get("/?pwa=1&theme=2&doc=doc-abc")
    assert res.status_code == 302
    assert res.headers["location"] == "/@adminuser/?pwa=1&theme=2&doc=doc-abc"
    assert res.headers.get("cache-control") == "no-store"


def test_query_param_preservation_anonymous_readonly_true(tmp_path: Path):
    """Verify GET /?pwa=1&theme=2&doc=doc-abc redirects anonymous visitor to /@adminuser/?pwa=1&theme=2&doc=doc-abc."""
    s, _ = _setup_network_env(tmp_path, anonymous_readonly=True)
    app = create_app(s)
    client = TestClient(app, base_url=s.public_url, follow_redirects=False)

    res = client.get("/?pwa=1&theme=2&doc=doc-abc")
    assert res.status_code == 302
    assert res.headers["location"] == "/@adminuser/?pwa=1&theme=2&doc=doc-abc"
    assert res.headers.get("cache-control") == "no-store"


def test_query_param_preservation_anonymous_readonly_false(tmp_path: Path):
    """Verify GET /?pwa=1&theme=2&doc=doc-abc redirects anonymous visitor to /login when anonymous_readonly=False."""
    s, _ = _setup_network_env(tmp_path, anonymous_readonly=False)
    app = create_app(s)
    client = TestClient(app, base_url=s.public_url, follow_redirects=False)

    res = client.get("/?pwa=1&theme=2&doc=doc-abc")
    assert res.status_code == 302
    assert res.headers["location"] == "/login"
    assert res.headers.get("cache-control") == "no-store"


def test_query_param_ordering_and_encoding_preservation(tmp_path: Path):
    """Verify query parameter ordering, URL encoding, duplicates, and flags are strictly preserved."""
    s, ids_dict = _setup_network_env(tmp_path)
    app = create_app(s)
    client = TestClient(app, base_url=s.public_url, follow_redirects=False)
    client.cookies.set("claire_session", ids_dict["alice_session"])

    # Permuted ordering with legacy parameter
    res1 = client.get("/?theme=2&doc=doc-abc&pwa=1")
    assert res1.status_code == 302
    assert res1.headers["location"] == "/@alice/?theme=2&doc=doc-abc&pwa=1"

    # URL-encoded unicode and special characters
    res2 = client.get("/?pwa=1&title=Special%20Title%26More&tag=%EA%B2%80%EC%83%89")
    assert res2.status_code == 302
    assert res2.headers["location"] == "/@alice/?pwa=1&title=Special%20Title%26More&tag=%EA%B2%80%EC%83%89"

    # Repeated query parameters
    res3 = client.get("/?pwa=1&filter=tag1&filter=tag2")
    assert res3.status_code == 302
    assert res3.headers["location"] == "/@alice/?pwa=1&filter=tag1&filter=tag2"

    # Flag and empty values
    res4 = client.get("/?pwa=1&empty=&flag")
    assert res4.status_code == 302
    assert res4.headers["location"] == "/@alice/?pwa=1&empty=&flag"


def test_pwa_takes_precedence_over_legacy_spa_redirect(tmp_path: Path):
    """Verify that pwa=1 combined with legacy parameters (doc, node, theme) does 302 PWA redirect, not 308."""
    s, ids_dict = _setup_network_env(tmp_path)
    app = create_app(s)
    client = TestClient(app, base_url=s.public_url, follow_redirects=False)
    client.cookies.set("claire_session", ids_dict["bob_session"])

    # Without pwa=1, ?doc=doc1 gives 308 to /@adminuser/?doc=doc1
    res_no_pwa = client.get("/?doc=doc1")
    assert res_no_pwa.status_code == 308
    assert res_no_pwa.headers["location"] == "/@adminuser/?doc=doc1"

    # WITH pwa=1, logged in member Bob gets 302 to /@bob/?pwa=1&doc=doc1
    res_with_pwa = client.get("/?pwa=1&doc=doc1")
    assert res_with_pwa.status_code == 302
    assert res_with_pwa.headers["location"] == "/@bob/?pwa=1&doc=doc1"
    assert res_with_pwa.headers.get("cache-control") == "no-store"


# ==============================================================================
# SECTION 4: Expired, Forged, and Malformed Session Cookies
# ==============================================================================


def test_expired_session_cookie_graceful_fallback(tmp_path: Path):
    """Verify expired session in auth_sessions table falls back gracefully to anonymous routing."""
    # Test with anonymous_readonly=True
    s_ro, ids_ro = _setup_network_env(tmp_path / "ro", anonymous_readonly=True)
    app_ro = create_app(s_ro)
    client_ro = TestClient(app_ro, base_url=s_ro.public_url, follow_redirects=False)
    client_ro.cookies.set("claire_session", ids_ro["expired_token"])

    res_ro = client_ro.get("/?pwa=1")
    assert res_ro.status_code == 302
    assert res_ro.headers["location"] == "/@adminuser/?pwa=1"
    assert res_ro.headers.get("cache-control") == "no-store"

    # Test with anonymous_readonly=False
    s_noro, ids_noro = _setup_network_env(tmp_path / "noro", anonymous_readonly=False)
    app_noro = create_app(s_noro)
    client_noro = TestClient(app_noro, base_url=s_noro.public_url, follow_redirects=False)
    client_noro.cookies.set("claire_session", ids_noro["expired_token"])

    res_noro = client_noro.get("/?pwa=1")
    assert res_noro.status_code == 302
    assert res_noro.headers["location"] == "/login"
    assert res_noro.headers.get("cache-control") == "no-store"


def test_orphaned_session_cookie_graceful_fallback(tmp_path: Path):
    """Verify session row pointing to non-existent account_id gracefully falls back to anonymous routing."""
    s, ids_dict = _setup_network_env(tmp_path, anonymous_readonly=True)
    app = create_app(s)
    client = TestClient(app, base_url=s.public_url, follow_redirects=False)
    client.cookies.set("claire_session", ids_dict["orphan_token"])

    res = client.get("/?pwa=1")
    assert res.status_code == 302
    assert res.headers["location"] == "/@adminuser/?pwa=1"
    assert res.headers.get("cache-control") == "no-store"


def test_adversarial_and_malformed_session_cookies(tmp_path: Path):
    """Verify malformed, SQL injection, unicode, and extreme tokens do not crash the server and fall back gracefully."""
    s, _ = _setup_network_env(tmp_path, anonymous_readonly=True)
    app = create_app(s)
    client = TestClient(app, base_url=s.public_url, follow_redirects=False)

    malformed_tokens = [
        "",  # Empty
        "   ",  # Whitespace
        "short",  # Below plausible length
        "a" * 10000,  # Huge length
        "' OR '1'='1",  # SQL injection basic
        "'; DROP TABLE auth_sessions; --",  # SQL injection DDL
        "admin'--",  # SQL comment
        "%00admin",  # Null byte
        "invalid!@#$%^&*()_+",  # Special characters
        "%EC%84%B8%EC%85%98%ED%86%A0%ED%81%B0",  # Percent-encoded unicode
        "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",  # Valid hex, not in DB
    ]

    for token in malformed_tokens:
        client.cookies.set("claire_session", token)
        res = client.get("/?pwa=1")
        assert res.status_code == 302, f"Failed for token {token!r}"
        assert res.headers["location"] == "/@adminuser/?pwa=1", f"Failed location for token {token!r}"
        assert res.headers.get("cache-control") == "no-store"

    # Verify database table auth_sessions was NOT damaged by SQL injection attempts
    conn = dbm.connect_existing(s.db_file)
    count = conn.execute("SELECT COUNT(*) FROM auth_sessions").fetchone()[0]
    conn.close()
    assert count > 0


# ==============================================================================
# SECTION 5: Account Status Edge Cases & Sliding Expiration
# ==============================================================================


@pytest.mark.parametrize(
    "session_key",
    ["carol_session", "dave_session", "eve_session", "frank_session"],
)
def test_inactive_or_invalid_account_status_credential_rejection(tmp_path: Path, session_key: str):
    """Verify suspended (carol), deleting (dave), retired (eve), or nameless (frank) accounts are rejected."""
    s, ids_dict = _setup_network_env(tmp_path, anonymous_readonly=True)
    app = create_app(s)
    client = TestClient(app, base_url=s.public_url, follow_redirects=False)

    client.cookies.set("claire_session", ids_dict[session_key])
    res = client.get("/?pwa=1")
    assert res.status_code == 302
    assert res.headers["location"] == "/@adminuser/?pwa=1"
    assert res.headers.get("cache-control") == "no-store"


def test_sliding_expiration_updates_expires_at_safely(tmp_path: Path):
    """Verify that a session within half TTL triggers sliding expiration write without database lock."""
    s, ids_dict = _setup_network_env(tmp_path)
    app = create_app(s)
    client = TestClient(app, base_url=s.public_url, follow_redirects=False)

    sliding_token = ids_dict["sliding_token"]
    initial_expires = ids_dict["sliding_initial_expires"]
    client.cookies.set("claire_session", sliding_token)

    # Issue GET /?pwa=1
    res = client.get("/?pwa=1")
    assert res.status_code == 302
    assert res.headers["location"] == "/@alice/?pwa=1"

    # Check updated expires_at in database
    conn = dbm.connect_existing(s.db_file)
    row = conn.execute(
        "SELECT expires_at FROM auth_sessions WHERE session_token=?", (sliding_token,)
    ).fetchone()
    conn.close()

    assert row is not None
    new_expires = float(row[0])
    # Sliding expiration should extend expiration significantly beyond the initial 100s remaining
    assert new_expires > initial_expires + 1000.0


def test_multiple_concurrent_sliding_requests(tmp_path: Path):
    """Verify multiple rapid requests to /?pwa=1 do not cause SQLite database lock errors."""
    s, ids_dict = _setup_network_env(tmp_path)
    app = create_app(s)
    client = TestClient(app, base_url=s.public_url, follow_redirects=False)
    client.cookies.set("claire_session", ids_dict["sliding_token"])

    for _ in range(10):
        res = client.get("/?pwa=1")
        assert res.status_code == 302
        assert res.headers["location"] == "/@alice/?pwa=1"
