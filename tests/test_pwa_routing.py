"""Tests for Milestone 2: Root Launch & Per-User Workspace Routing (/?pwa=1)."""

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


def _setup_network_env(tmp_path: Path, *, anonymous_readonly: bool = True, admin_active: bool = True):
    db_file = tmp_path / "claire.db"
    conn = dbm.connect(db_file)
    dbm.init_db(conn)
    now = time.time()
    salt = dbm.read_id_salt(conn)

    if admin_active:
        conn.execute(
            "UPDATE accounts SET username='adminuser', status='active', activated_at=?, profile_public=1 WHERE id=1",
            (now,),
        )
    else:
        conn.execute(
            "UPDATE accounts SET username=NULL, status='setup_required', activated_at=NULL WHERE id=1"
        )

    alice_pub = ids.timestamp_id("accounts", salt=salt, unix_ms=int(now * 1000) + 1)
    cur = conn.execute(
        "INSERT INTO accounts (public_id, username, role, status, display_name, profile_public, created_at, activated_at, updated_at) "
        "VALUES (?, 'alice', 'member', 'active', 'Alice In Wonderland', 1, ?, ?, ?)",
        (alice_pub, now, now, now),
    )
    alice_id = int(cur.lastrowid)

    num_alice_pub = ids.timestamp_id("themes", salt=salt, unix_ms=int(now * 1000) + 10)
    conn.execute(
        "INSERT INTO themes (id, seq, number, label, account_id, is_default, is_public, created_at, updated_at) "
        "VALUES (10, 10, ?, 'Alice Public Theme', ?, 1, 1, ?, ?)",
        (num_alice_pub, alice_id, now, now),
    )

    carol_pub = ids.timestamp_id("accounts", salt=salt, unix_ms=int(now * 1000) + 2)
    cur = conn.execute(
        "INSERT INTO accounts (public_id, username, role, status, display_name, profile_public, created_at, activated_at, updated_at) "
        "VALUES (?, 'carol', 'member', 'suspended', 'Carol Singer', 1, ?, ?, ?)",
        (carol_pub, now, now, now),
    )
    carol_id = int(cur.lastrowid)

    store = accounts_store.AccountStore(conn)
    store.set_setting("mode", "network", updated_by=1)
    conn.commit()

    alice_session = accounts_store.create_account_session(conn, account_id=alice_id, scope="owner", now=now)
    carol_session = accounts_store.create_account_session(conn, account_id=carol_id, scope="owner", now=now)
    admin_session = None
    if admin_active:
        admin_session = accounts_store.create_account_session(conn, account_id=1, scope="owner", now=now)
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
        "carol_id": carol_id,
        "alice_session": alice_session,
        "carol_session": carol_session,
        "admin_session": admin_session,
    }


def _setup_single_env(tmp_path: Path, *, anonymous_readonly: bool = True):
    db_file = tmp_path / "single_claire.db"
    conn = dbm.connect(db_file)
    dbm.init_db(conn)
    now = time.time()
    conn.execute(
        "UPDATE accounts SET username='adminuser', status='active', activated_at=? WHERE id=1",
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


# ------------------------------------------------------------------------------
# 1. Network Mode: Authenticated User Tests
# ------------------------------------------------------------------------------

def test_network_mode_pwa_logged_in_member_redirect(tmp_path: Path):
    s, ids_dict = _setup_network_env(tmp_path)
    app = create_app(s)
    client = TestClient(app, base_url=s.public_url, follow_redirects=False)
    client.cookies.set("claire_session", ids_dict["alice_session"])

    res = client.get("/?pwa=1")
    assert res.status_code == 302
    assert res.headers["location"] == "/@alice/?pwa=1"
    assert res.headers.get("cache-control") == "no-store"


def test_network_mode_pwa_logged_in_admin_redirect(tmp_path: Path):
    s, ids_dict = _setup_network_env(tmp_path)
    app = create_app(s)
    client = TestClient(app, base_url=s.public_url, follow_redirects=False)
    client.cookies.set("claire_session", ids_dict["admin_session"])

    res = client.get("/?pwa=1")
    assert res.status_code == 302
    assert res.headers["location"] == "/@adminuser/?pwa=1"
    assert res.headers.get("cache-control") == "no-store"


def test_network_mode_pwa_preserves_additional_query_parameters(tmp_path: Path):
    s, ids_dict = _setup_network_env(tmp_path)
    app = create_app(s)
    client = TestClient(app, base_url=s.public_url, follow_redirects=False)
    client.cookies.set("claire_session", ids_dict["alice_session"])

    res1 = client.get("/?pwa=1&foo=bar")
    assert res1.status_code == 302
    assert res1.headers["location"] == "/@alice/?pwa=1&foo=bar"
    assert res1.headers.get("cache-control") == "no-store"

    res2 = client.get("/?foo=bar&pwa=1")
    assert res2.status_code == 302
    assert res2.headers["location"] == "/@alice/?foo=bar&pwa=1"
    assert res2.headers.get("cache-control") == "no-store"

    res3 = client.get("/?pwa=1&tab=documents&view=compact")
    assert res3.status_code == 302
    assert res3.headers["location"] == "/@alice/?pwa=1&tab=documents&view=compact"
    assert res3.headers.get("cache-control") == "no-store"

    res4 = client.get("/?pwa=1&theme=2")
    assert res4.status_code == 302
    assert res4.headers["location"] == "/@alice/?pwa=1&theme=2"
    assert res4.headers.get("cache-control") == "no-store"

    res5 = client.get("/?pwa=1&theme=2&doc=doc-abc")
    assert res5.status_code == 302
    assert res5.headers["location"] == "/@alice/?pwa=1&theme=2&doc=doc-abc"
    assert res5.headers.get("cache-control") == "no-store"


def test_network_mode_pwa_legacy_spa_query_collision_authenticated(tmp_path: Path):
    """Verify ?pwa=1 overrides legacy SPA redirects when theme, doc, or node is present."""
    s, ids_dict = _setup_network_env(tmp_path)
    app = create_app(s)
    client = TestClient(app, base_url=s.public_url, follow_redirects=False)
    client.cookies.set("claire_session", ids_dict["alice_session"])

    res_theme = client.get("/?pwa=1&theme=2")
    assert res_theme.status_code == 302
    assert res_theme.headers["location"] == "/@alice/?pwa=1&theme=2"
    assert res_theme.headers.get("cache-control") == "no-store"

    res_doc = client.get("/?pwa=1&doc=doc-abc")
    assert res_doc.status_code == 302
    assert res_doc.headers["location"] == "/@alice/?pwa=1&doc=doc-abc"
    assert res_doc.headers.get("cache-control") == "no-store"

    res_both = client.get("/?pwa=1&theme=2&doc=doc-abc")
    assert res_both.status_code == 302
    assert res_both.headers["location"] == "/@alice/?pwa=1&theme=2&doc=doc-abc"
    assert res_both.headers.get("cache-control") == "no-store"


# ------------------------------------------------------------------------------
# 2. Network Mode: Anonymous & Config Tests
# ------------------------------------------------------------------------------

def test_network_mode_pwa_anonymous_readonly_true_redirects_to_admin(tmp_path: Path):
    s, _ = _setup_network_env(tmp_path, anonymous_readonly=True)
    app = create_app(s)
    client = TestClient(app, base_url=s.public_url, follow_redirects=False)

    res = client.get("/?pwa=1")
    assert res.status_code == 302
    assert res.headers["location"] == "/@adminuser/?pwa=1"
    assert res.headers.get("cache-control") == "no-store"


def test_network_mode_pwa_anonymous_readonly_true_preserves_query_params(tmp_path: Path):
    s, _ = _setup_network_env(tmp_path, anonymous_readonly=True)
    app = create_app(s)
    client = TestClient(app, base_url=s.public_url, follow_redirects=False)

    res = client.get("/?pwa=1&filter=recent")
    assert res.status_code == 302
    assert res.headers["location"] == "/@adminuser/?pwa=1&filter=recent"
    assert res.headers.get("cache-control") == "no-store"


def test_network_mode_pwa_anonymous_readonly_false_redirects_to_login(tmp_path: Path):
    s, _ = _setup_network_env(tmp_path, anonymous_readonly=False)
    app = create_app(s)
    client = TestClient(app, base_url=s.public_url, follow_redirects=False)

    res = client.get("/?pwa=1")
    assert res.status_code == 302
    assert res.headers["location"] == "/login"
    assert res.headers.get("cache-control") == "no-store"


def test_network_mode_pwa_anonymous_no_admin_falls_back_to_login(tmp_path: Path):
    s, _ = _setup_network_env(tmp_path, anonymous_readonly=True, admin_active=False)
    app = create_app(s)
    client = TestClient(app, base_url=s.public_url, follow_redirects=False)

    res = client.get("/?pwa=1")
    assert res.status_code == 302
    assert res.headers["location"] == "/login"
    assert res.headers.get("cache-control") == "no-store"


# ------------------------------------------------------------------------------
# 3. Network Mode: Expired, Invalid, or Suspended Account Fallbacks
# ------------------------------------------------------------------------------

def test_network_mode_pwa_expired_or_invalid_session_fallback(tmp_path: Path):
    s_ro, _ = _setup_network_env(tmp_path / "ro", anonymous_readonly=True)
    app_ro = create_app(s_ro)
    client_ro = TestClient(app_ro, base_url=s_ro.public_url, follow_redirects=False)
    client_ro.cookies.set("claire_session", "completely_bogus_token_value")

    res_ro = client_ro.get("/?pwa=1")
    assert res_ro.status_code == 302
    assert res_ro.headers["location"] == "/@adminuser/?pwa=1"
    assert res_ro.headers.get("cache-control") == "no-store"

    s_no_ro, _ = _setup_network_env(tmp_path / "no_ro", anonymous_readonly=False)
    app_no_ro = create_app(s_no_ro)
    client_no_ro = TestClient(app_no_ro, base_url=s_no_ro.public_url, follow_redirects=False)
    client_no_ro.cookies.set("claire_session", "completely_bogus_token_value")

    res_no_ro = client_no_ro.get("/?pwa=1")
    assert res_no_ro.status_code == 302
    assert res_no_ro.headers["location"] == "/login"
    assert res_no_ro.headers.get("cache-control") == "no-store"


def test_network_mode_pwa_suspended_account_fallback(tmp_path: Path):
    s_ro, ids_ro = _setup_network_env(tmp_path / "ro", anonymous_readonly=True)
    app_ro = create_app(s_ro)
    client_ro = TestClient(app_ro, base_url=s_ro.public_url, follow_redirects=False)
    client_ro.cookies.set("claire_session", ids_ro["carol_session"])

    res_ro = client_ro.get("/?pwa=1")
    assert res_ro.status_code == 302
    assert res_ro.headers["location"] == "/@adminuser/?pwa=1"
    assert res_ro.headers.get("cache-control") == "no-store"

    s_no_ro, ids_no_ro = _setup_network_env(tmp_path / "no_ro", anonymous_readonly=False)
    app_no_ro = create_app(s_no_ro)
    client_no_ro = TestClient(app_no_ro, base_url=s_no_ro.public_url, follow_redirects=False)
    client_no_ro.cookies.set("claire_session", ids_no_ro["carol_session"])

    res_no_ro = client_no_ro.get("/?pwa=1")
    assert res_no_ro.status_code == 302
    assert res_no_ro.headers["location"] == "/login"
    assert res_no_ro.headers.get("cache-control") == "no-store"


# ------------------------------------------------------------------------------
# 4. Network Mode: Preservation of Normal Root & Legacy Queries
# ------------------------------------------------------------------------------

def test_network_mode_normal_root_preserves_public_directory(tmp_path: Path):
    s, _ = _setup_network_env(tmp_path)
    app = create_app(s)
    client = TestClient(app, base_url=s.public_url, follow_redirects=False)

    res = client.get("/")
    assert res.status_code == 200
    assert "text/html" in res.headers.get("content-type", "")
    assert "공개 계정 둘러보기" in res.text or "explore" in res.text or "등록된 공개 계정" in res.text


def test_network_mode_non_pwa_query_params(tmp_path: Path):
    s, _ = _setup_network_env(tmp_path)
    app = create_app(s)
    client = TestClient(app, base_url=s.public_url, follow_redirects=False)

    res_pwa0 = client.get("/?pwa=0")
    assert res_pwa0.status_code == 200
    assert "text/html" in res_pwa0.headers.get("content-type", "")

    res_pwatrue = client.get("/?pwa=true")
    assert res_pwatrue.status_code == 200
    assert "text/html" in res_pwatrue.headers.get("content-type", "")

    res_filter = client.get("/?filter=alice")
    assert res_filter.status_code == 200
    assert "text/html" in res_filter.headers.get("content-type", "")

    res_doc = client.get("/?doc=doc1")
    assert res_doc.status_code == 308
    assert res_doc.headers.get("location") == "/@adminuser/?doc=doc1"


# ------------------------------------------------------------------------------
# 5. Single Mode Tests
# ------------------------------------------------------------------------------

def test_single_mode_pwa_launch_returns_workspace(tmp_path: Path):
    s, _ = _setup_single_env(tmp_path)
    app = create_app(s)
    client = TestClient(app, base_url=s.public_url, follow_redirects=False)

    res = client.get("/?pwa=1")
    assert res.status_code == 200
    assert "text/html" in res.headers.get("content-type", "")
    assert "claire-storage-key" in res.text or "Claire" in res.text
    assert "explore_accounts" not in res.text


def test_single_mode_root_without_pwa(tmp_path: Path):
    s, _ = _setup_single_env(tmp_path)
    app = create_app(s)
    client = TestClient(app, base_url=s.public_url, follow_redirects=False)

    res = client.get("/")
    assert res.status_code == 200
    assert "text/html" in res.headers.get("content-type", "")


def test_single_mode_pwa_with_query_params(tmp_path: Path):
    s, _ = _setup_single_env(tmp_path)
    app = create_app(s)
    client = TestClient(app, base_url=s.public_url, follow_redirects=False)

    res = client.get("/?pwa=1&filter=all")
    assert res.status_code == 200
    assert "text/html" in res.headers.get("content-type", "")


def test_single_mode_pwa_authenticated_when_anonymous_readonly_false(tmp_path: Path):
    s, ids_dict = _setup_single_env(tmp_path, anonymous_readonly=False)
    app = create_app(s)
    client = TestClient(app, base_url=s.public_url, follow_redirects=False)
    client.cookies.set("claire_session", ids_dict["admin_session"])

    res = client.get("/?pwa=1")
    assert res.status_code == 200
    assert "text/html" in res.headers.get("content-type", "")


# ------------------------------------------------------------------------------
# 6. HEAD Request & Destination Workspace Access Tests
# ------------------------------------------------------------------------------

def test_network_mode_pwa_head_request_parity(tmp_path: Path):
    s, ids_dict = _setup_network_env(tmp_path)
    app = create_app(s)
    client = TestClient(app, base_url=s.public_url, follow_redirects=False)
    client.cookies.set("claire_session", ids_dict["alice_session"])

    res = client.head("/?pwa=1")
    assert res.status_code == 302
    assert res.headers["location"] == "/@alice/?pwa=1"
    assert res.headers.get("cache-control") == "no-store"
    assert res.text == ""

    client.cookies.clear()
    res_anon = client.head("/?pwa=1")
    assert res_anon.status_code == 302
    assert res_anon.headers["location"] == "/@adminuser/?pwa=1"
    assert res_anon.headers.get("cache-control") == "no-store"
    assert res_anon.text == ""


def test_pwa_redirect_target_workspace_accessible(tmp_path: Path):
    s, ids_dict = _setup_network_env(tmp_path)
    app = create_app(s)
    client = TestClient(app, base_url=s.public_url, follow_redirects=False)
    client.cookies.set("claire_session", ids_dict["alice_session"])

    res_root = client.get("/?pwa=1")
    assert res_root.status_code == 302
    target_url = res_root.headers["location"]
    assert target_url == "/@alice/?pwa=1"

    res_ws = client.get(target_url)
    assert res_ws.status_code == 200
    assert "text/html" in res_ws.headers.get("content-type", "")
    assert "지식 그래프" in res_ws.text or "claire" in res_ws.text.lower()
