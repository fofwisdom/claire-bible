"""Public directory and network mode root routing tests (P3 L3-g, D4)."""

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
from claire.store.public_index import SqlitePublicIndexStore
from claire.store.token_store import reset_token_stores


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch):
    for name in list(os.environ):
        if name.startswith("CLAIRE_"):
            monkeypatch.delenv(name, raising=False)
    reset_token_stores()
    yield
    reset_token_stores()


def _init_test_db(db_file: Path) -> sqlite3.Connection:
    conn = dbm.connect(db_file)
    dbm.init_db(conn)
    return conn


def _setup_network_env(tmp_path: Path):
    db_file = tmp_path / "claire.db"
    conn = _init_test_db(db_file)
    now = time.time()
    salt = dbm.read_id_salt(conn)

    conn.execute(
        "UPDATE accounts SET username='adminuser', status='active', activated_at=?, profile_public=1 WHERE id=1",
        (now,),
    )

    alice_pub = ids.timestamp_id("accounts", salt=salt, unix_ms=int(now * 1000) + 1)
    cur = conn.execute(
        "INSERT INTO accounts (public_id, username, role, status, display_name, profile_public, created_at, activated_at, updated_at) "
        "VALUES (?, 'alice', 'member', 'active', 'Alice In Wonderland', 1, ?, ?, ?)",
        (alice_pub, now, now, now),
    )
    alice_id = int(cur.lastrowid)

    bob_pub = ids.timestamp_id("accounts", salt=salt, unix_ms=int(now * 1000) + 2)
    cur = conn.execute(
        "INSERT INTO accounts (public_id, username, role, status, display_name, profile_public, created_at, activated_at, updated_at) "
        "VALUES (?, 'bob', 'member', 'active', 'Bob Builder', 0, ?, ?, ?)",
        (bob_pub, now, now, now),
    )
    bob_id = int(cur.lastrowid)

    carol_pub = ids.timestamp_id("accounts", salt=salt, unix_ms=int(now * 1000) + 3)
    cur = conn.execute(
        "INSERT INTO accounts (public_id, username, role, status, display_name, profile_public, created_at, activated_at, updated_at) "
        "VALUES (?, 'carol', 'member', 'suspended', 'Carol Singer', 1, ?, ?, ?)",
        (carol_pub, now, now, now),
    )
    carol_id = int(cur.lastrowid)

    dave_pub = ids.timestamp_id("accounts", salt=salt, unix_ms=int(now * 1000) + 4)
    cur = conn.execute(
        "INSERT INTO accounts (public_id, username, role, status, display_name, profile_public, created_at, activated_at, updated_at) "
        "VALUES (?, 'dave', 'member', 'deleting', 'Dave Diver', 1, ?, ?, ?)",
        (dave_pub, now, now, now),
    )
    dave_id = int(cur.lastrowid)

    num_alice_pub = ids.timestamp_id("themes", salt=salt, unix_ms=int(now * 1000) + 10)
    conn.execute(
        "INSERT INTO themes (id, seq, number, label, account_id, is_default, is_public, created_at, updated_at) "
        "VALUES (10, 10, ?, 'Alice Public Theme', ?, 1, 1, ?, ?)",
        (num_alice_pub, alice_id, now, now),
    )

    num_alice_priv = ids.timestamp_id("themes", salt=salt, unix_ms=int(now * 1000) + 11)
    conn.execute(
        "INSERT INTO themes (id, seq, number, label, account_id, is_default, is_public, created_at, updated_at) "
        "VALUES (11, 11, ?, 'Alice Private Theme', ?, 0, 0, ?, ?)",
        (num_alice_priv, alice_id, now, now),
    )

    conn.execute(
        "INSERT INTO documents (id, theme_id, url, title, raw_text, hidden) VALUES ('doc1', 10, 'http://example.com/1', 'Doc 1', 'Text', 0)"
    )
    conn.execute(
        "INSERT INTO documents (id, theme_id, url, title, raw_text, hidden) VALUES ('doc2', 10, 'http://example.com/2', 'Doc 2', 'Text', 0)"
    )
    conn.execute(
        "INSERT INTO documents (id, theme_id, url, title, raw_text, hidden) VALUES ('doc3', 10, 'http://example.com/3', 'Doc 3 (hidden)', 'Text', 1)"
    )
    conn.execute(
        "INSERT INTO documents (id, theme_id, url, title, raw_text, hidden) VALUES ('doc4', 11, 'http://example.com/4', 'Doc 4 (private theme)', 'Text', 0)"
    )

    store = accounts_store.AccountStore(conn)
    store.set_setting("mode", "network", updated_by=1)
    conn.commit()
    conn.close()

    s = Settings(
        CLAIRE_DB_PATH=str(db_file),
        CLAIRE_VAULT_PATH=str(tmp_path / "vault"),
        CLAIRE_MULTI_THEME=True,
        CLAIRE_ENVIRONMENT="development",
        CLAIRE_PUBLIC_URL="http://127.0.0.1:8765",
        CLAIRE_ANONYMOUS_READONLY=True,
    )
    return s, {
        "db_file": db_file,
        "alice_id": alice_id,
        "bob_id": bob_id,
        "carol_id": carol_id,
        "dave_id": dave_id,
        "num_alice_pub": num_alice_pub,
    }


def test_public_index_store_queries_and_counters(tmp_path: Path):
    s, ids_dict = _setup_network_env(tmp_path)
    conn = dbm.connect_existing(ids_dict["db_file"])
    try:
        store = SqlitePublicIndexStore(conn)

        count = store.count_public_accounts()
        assert count == 2

        t_cnt, d_cnt = store.refresh_counters_for_account(ids_dict["alice_id"])
        assert t_cnt == 1
        assert d_cnt == 2

        row = conn.execute(
            "SELECT public_themes, public_documents FROM public_directory_counters WHERE account_id=?",
            (ids_dict["alice_id"],),
        ).fetchone()
        assert row is not None
        assert row[0] == 1
        assert row[1] == 2

        accounts = store.list_public_accounts(limit=10, include_themes=True)
        assert len(accounts) == 2
        usernames = {a.username for a in accounts}
        assert usernames == {"adminuser", "alice"}

        alice = next(a for a in accounts if a.username == "alice")
        assert alice.display_name == "Alice In Wonderland"
        assert alice.public_themes == 1
        assert alice.public_documents == 2
        assert len(alice.themes) == 1
        assert alice.themes[0].label == "Alice Public Theme"
        assert alice.themes[0].documents == 2

        first_page = store.list_public_accounts(limit=1)
        assert len(first_page) == 1
        next_page = store.list_public_accounts(limit=1, max_id=first_page[0].public_id)
        assert len(next_page) == 1
        assert next_page[0].public_id != first_page[0].public_id
        prev_page = store.list_public_accounts(limit=1, min_id=next_page[0].public_id)
        assert len(prev_page) == 1
        assert prev_page[0].public_id == first_page[0].public_id
    finally:
        conn.close()


def test_explore_accounts_json_and_html_negotiation(tmp_path: Path):
    s, ids_dict = _setup_network_env(tmp_path)
    app = create_app(s)
    client = TestClient(app, base_url=s.public_url, follow_redirects=False)

    res_root = client.get("/explore/accounts", headers={"Accept": "application/json"})
    assert res_root.status_code == 200
    assert "application/json" in res_root.headers.get("content-type", "")
    assert res_root.headers.get("vary") == "Cookie, Authorization, Sec-Fetch-Dest, Accept"
    etag_root = res_root.headers.get("etag")
    assert etag_root is not None
    root_json = res_root.json()
    assert root_json["type"] == "OrderedCollection"
    assert root_json["totalItems"] == 2
    assert root_json["first"] == f"{s.public_url.rstrip('/')}/explore/accounts?page=true"

    res_head = client.head("/explore/accounts", headers={"Accept": "application/json"})
    assert res_head.status_code == 200
    assert res_head.headers.get("etag") == etag_root
    assert res_head.text == ""

    res_page = client.get("/explore/accounts?page=true", headers={"Accept": "application/json"})
    assert res_page.status_code == 200
    page_json = res_page.json()
    assert page_json["type"] == "OrderedCollectionPage"
    assert len(page_json["orderedItems"]) == 2
    usernames = [it["username"] for it in page_json["orderedItems"]]
    assert "alice" in usernames
    assert "adminuser" in usernames

    etag_page = res_page.headers.get("etag")
    cond_res = client.get("/explore/accounts?page=true", headers={"Accept": "application/json", "If-None-Match": etag_page})
    assert cond_res.status_code == 304

    res_html = client.get("/explore/accounts", headers={"Accept": "text/html"})
    assert res_html.status_code == 200
    assert "text/html" in res_html.headers.get("content-type", "")
    assert "공개 디렉터리" in res_html.text
    assert "Alice In Wonderland" in res_html.text
    assert "@alice" in res_html.text
    assert "Alice Public Theme" in res_html.text
    assert "로그인" in res_html.text

    res_dest_html = client.get("/explore/accounts", headers={"Sec-Fetch-Dest": "document"})
    assert res_dest_html.status_code == 200
    assert "text/html" in res_dest_html.headers.get("content-type", "")


def test_network_mode_root_routing(tmp_path: Path):
    s, ids_dict = _setup_network_env(tmp_path)
    app = create_app(s)
    client = TestClient(app, base_url=s.public_url, follow_redirects=False)

    res_root = client.get("/")
    assert res_root.status_code == 200
    assert "text/html" in res_root.headers.get("content-type", "")
    assert "공개 디렉터리" in res_root.text

    res_doc = client.get("/?doc=doc123&x=1")
    assert res_doc.status_code == 308
    assert res_doc.headers.get("location") == "/@adminuser/?doc=doc123&x=1"

    res_node = client.get("/?node=node456")
    assert res_node.status_code == 308
    assert res_node.headers.get("location") == "/@adminuser/?node=node456"

    res_theme = client.get("/?theme=theme789")
    assert res_theme.status_code == 308
    assert res_theme.headers.get("location") == "/@adminuser/?theme=theme789"

    res_other = client.get("/?filter=alpha")
    assert res_other.status_code == 200
    assert "공개 디렉터리" in res_other.text


def test_single_mode_explore_accounts_is_404(tmp_path: Path):
    db_file = tmp_path / "claire.db"
    conn = _init_test_db(db_file)
    store = accounts_store.AccountStore(conn)
    assert store.instance_mode() == "single"
    conn.close()

    s = Settings(
        CLAIRE_DB_PATH=str(db_file),
        CLAIRE_VAULT_PATH=str(tmp_path / "vault"),
        CLAIRE_MULTI_THEME=True,
        CLAIRE_ENVIRONMENT="development",
        CLAIRE_PUBLIC_URL="http://127.0.0.1:8765",
        CLAIRE_ANONYMOUS_READONLY=True,
    )
    app = create_app(s)
    client = TestClient(app, base_url=s.public_url, follow_redirects=False)

    assert client.get("/explore/accounts").status_code == 404
    assert client.head("/explore/accounts").status_code == 404

    root_res = client.get("/")
    assert root_res.status_code == 200
    assert "공개 디렉터리" not in root_res.text


def test_account_filtering_profile_public_and_status(tmp_path: Path):
    s, ids_dict = _setup_network_env(tmp_path)
    app = create_app(s)
    client = TestClient(app, base_url=s.public_url, follow_redirects=False)

    res = client.get("/explore/accounts?page=true", headers={"Accept": "application/json"})
    assert res.status_code == 200
    items = res.json()["orderedItems"]
    usernames = {it["username"] for it in items}

    assert "bob" not in usernames
    assert "carol" not in usernames
    assert "dave" not in usernames
    assert "alice" in usernames
    assert "adminuser" in usernames

    html_res = client.get("/explore/accounts", headers={"Accept": "text/html"})
    assert "Bob Builder" not in html_res.text
    assert "@bob" not in html_res.text
    assert "Carol Singer" not in html_res.text
    assert "@carol" not in html_res.text
    assert "Dave Diver" not in html_res.text
    assert "@dave" not in html_res.text


def test_empty_state_rendering(tmp_path: Path):
    db_file = tmp_path / "claire.db"
    conn = _init_test_db(db_file)
    store = accounts_store.AccountStore(conn)
    store.set_setting("mode", "network", updated_by=1)
    conn.execute("UPDATE accounts SET profile_public=0")
    conn.commit()
    conn.close()

    s = Settings(
        CLAIRE_DB_PATH=str(db_file),
        CLAIRE_VAULT_PATH=str(tmp_path / "vault"),
        CLAIRE_MULTI_THEME=True,
        CLAIRE_ENVIRONMENT="development",
        CLAIRE_PUBLIC_URL="http://127.0.0.1:8765",
        CLAIRE_ANONYMOUS_READONLY=True,
    )
    app = create_app(s)
    client = TestClient(app, base_url=s.public_url, follow_redirects=False)

    res = client.get("/explore/accounts", headers={"Accept": "text/html"})
    assert res.status_code == 200
    assert "등록된 공개 계정이 없습니다" in res.text
    assert "로그인" in res.text

    res_root = client.get("/")
    assert res_root.status_code == 200
    assert "등록된 공개 계정이 없습니다" in res_root.text


def test_public_directory_authenticated_user_navigation_e2e(tmp_path: Path):
    """End-to-end user navigation flow for authenticated session in network mode."""
    s, ids_dict = _setup_network_env(tmp_path)
    conn = dbm.connect_existing(ids_dict["db_file"])
    try:
        session_token = accounts_store.create_account_session(
            conn, account_id=ids_dict["alice_id"], scope="owner"
        )
        conn.commit()
    finally:
        conn.close()

    app = create_app(s)
    client = TestClient(app, base_url=s.public_url, follow_redirects=False)

    anon_res = client.get("/explore/accounts", headers={"Accept": "text/html"})
    assert anon_res.status_code == 200
    assert 'href="/login"' in anon_res.text
    assert "로그아웃" not in anon_res.text

    client.cookies.set("claire_session", session_token)
    auth_res = client.get("/explore/accounts", headers={"Accept": "text/html"})
    assert auth_res.status_code == 200
    assert "내 작업 공간" in auth_res.text
    assert 'href="/@alice/"' in auth_res.text
    assert "로그아웃" in auth_res.text

    ws_res = client.get("/@alice/")
    assert ws_res.status_code == 200
