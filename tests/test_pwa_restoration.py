"""Comprehensive End-to-End Acceptance Test Suite for Claire PWA Restoration (Milestones 1-4)."""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient

from claire import ids
from claire.api.server import create_app
from claire.config import Settings
from claire.graphview import render_graph_html
from claire.ontology.base import Document
from claire.store import accounts as accounts_store
from claire.store import db as dbm
from claire.store.theme import ThemeManager
from claire.store.token_store import reset_token_stores

PROJECT_ROOT = Path(__file__).resolve().parent.parent
APP_JS_PATH = PROJECT_ROOT / "src" / "claire" / "static" / "js" / "app.js"
READER_JS_PATH = PROJECT_ROOT / "src" / "claire" / "static" / "js" / "reader.js"
INDEX_HTML_PATH = PROJECT_ROOT / "src" / "claire" / "templates" / "index.html"
MANIFEST_PATH = PROJECT_ROOT / "src" / "claire" / "static" / "icons" / "manifest.json"
WEBMANIFEST_PATH = PROJECT_ROOT / "src" / "claire" / "static" / "icons" / "site.webmanifest"


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch):
    for name in list(os.environ):
        if name.startswith("CLAIRE_"):
            monkeypatch.delenv(name, raising=False)
    reset_token_stores()
    yield
    reset_token_stores()


def _setup_pwa_network_env(tmp_path: Path, *, anonymous_readonly: bool = True):
    db_file = tmp_path / "claire.db"
    conn = dbm.connect(db_file)
    dbm.init_db(conn)
    now = time.time()
    salt = dbm.read_id_salt(conn)

    # Admin account (id=1, username='adminuser')
    conn.execute(
        "UPDATE accounts SET username='adminuser', status='active', activated_at=?, profile_public=1 WHERE id=1",
        (now,),
    )

    # Alice account (id=2, username='alice')
    alice_pub = ids.timestamp_id("accounts", salt=salt, unix_ms=int(now * 1000) + 1)
    cur = conn.execute(
        "INSERT INTO accounts (public_id, username, role, status, display_name, profile_public, created_at, activated_at, updated_at) "
        "VALUES (?, 'alice', 'member', 'active', 'Alice Liddell', 1, ?, ?, ?)",
        (alice_pub, now, now, now),
    )
    alice_id = int(cur.lastrowid)

    # Bob account (id=3, username='bob')
    bob_pub = ids.timestamp_id("accounts", salt=salt, unix_ms=int(now * 1000) + 2)
    cur = conn.execute(
        "INSERT INTO accounts (public_id, username, role, status, display_name, profile_public, created_at, activated_at, updated_at) "
        "VALUES (?, 'bob', 'member', 'active', 'Bob Builder', 1, ?, ?, ?)",
        (bob_pub, now, now, now),
    )
    bob_id = int(cur.lastrowid)

    # Themes
    alice_theme_pub = ids.timestamp_id("themes", salt=salt, unix_ms=int(now * 1000) + 10)
    conn.execute(
        "INSERT INTO themes (id, seq, number, label, account_id, is_default, is_public, created_at, updated_at) "
        "VALUES (10, 10, ?, 'Alice Personal Theme', ?, 1, 1, ?, ?)",
        (alice_theme_pub, alice_id, now, now),
    )
    bob_theme_pub = ids.timestamp_id("themes", salt=salt, unix_ms=int(now * 1000) + 20)
    conn.execute(
        "INSERT INTO themes (id, seq, number, label, account_id, is_default, is_public, created_at, updated_at) "
        "VALUES (20, 20, ?, 'Bob Secret Theme', ?, 1, 0, ?, ?)",
        (bob_theme_pub, bob_id, now, now),
    )

    # Documents
    doc_alice = Document(
        id="doc_alice_1",
        url="https://example.com/alice/ch1",
        title="Alice In Wonderland Notes",
        raw_text="Curiouser and curiouser cried Alice.",
        source_type="web",
        content_hash="hash_alice_1",
    )
    dbm.insert_document(conn, doc_alice, theme_id=10)

    doc_bob = Document(
        id="doc_bob_private",
        url="https://example.com/bob/secret",
        title="Bob Secret Blueprints",
        raw_text="Can we fix it? Yes we can!",
        source_type="web",
        content_hash="hash_bob_1",
    )
    dbm.insert_document(conn, doc_bob, theme_id=20)

    # Set network mode
    store = accounts_store.AccountStore(conn)
    store.set_setting("mode", "network", updated_by=1)
    conn.commit()

    # Sessions
    admin_session = accounts_store.create_account_session(conn, account_id=1, scope="owner", now=now)
    alice_session = accounts_store.create_account_session(conn, account_id=alice_id, scope="owner", now=now)
    bob_session = accounts_store.create_account_session(conn, account_id=bob_id, scope="owner", now=now)
    conn.close()

    s = Settings(
        CLAIRE_DB_PATH=str(db_file),
        CLAIRE_VAULT_PATH=str(tmp_path / "vault"),
        CLAIRE_MULTI_THEME=True,
        CLAIRE_ENVIRONMENT="development",
        CLAIRE_PUBLIC_URL="http://127.0.0.1:8765",
        CLAIRE_ANONYMOUS_READONLY=anonymous_readonly,
        CLAIRE_SESSION_SECRET="test-secret-pwa-restore",
    )
    return s, {
        "db_file": db_file,
        "alice_id": alice_id,
        "bob_id": bob_id,
        "admin_session": admin_session,
        "alice_session": alice_session,
        "bob_session": bob_session,
    }


def _setup_pwa_single_env(tmp_path: Path):
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
        CLAIRE_SINGLE_MODE=True,
        CLAIRE_ENVIRONMENT="development",
        CLAIRE_PUBLIC_URL="http://127.0.0.1:8765",
        CLAIRE_SESSION_SECRET="test-secret-pwa-restore",
    )
    return s, {"db_file": db_file, "admin_session": admin_session}


# ==============================================================================
# Milestone 1 Acceptance: Manifests, Service Worker, and Template PWA Head Tags
# ==============================================================================

def test_m1_manifest_contracts_and_icon_purpose_separation(tmp_path: Path):
    """Verify both manifest.json and site.webmanifest satisfy W3C PWA installation criteria."""
    s, _ = _setup_pwa_network_env(tmp_path)
    app = create_app(s)
    client = TestClient(app, base_url=s.public_url)

    for path in ["/manifest.json", "/site.webmanifest"]:
        res = client.get(path)
        assert res.status_code == 200
        assert "application/manifest+json" in res.headers["content-type"] or "application/json" in res.headers["content-type"]
        data = res.json()
        assert data["id"] == "/"
        assert data["scope"] == "/"
        assert data["start_url"] == "/?pwa=1"
        assert data["display"] == "standalone"

        icons = data.get("icons", [])
        assert len(icons) >= 2
        purposes = {icon.get("purpose") for icon in icons}
        assert "any" in purposes
        assert "maskable" in purposes
        # Ensure 'any maskable' combined purpose is NOT used
        for icon in icons:
            assert icon.get("purpose") != "any maskable"


def test_m1_service_worker_contract(tmp_path: Path):
    """Verify /sw.js route serves network-passthrough SW with Service-Worker-Allowed and no-cache headers."""
    s, _ = _setup_pwa_network_env(tmp_path)
    app = create_app(s)
    client = TestClient(app, base_url=s.public_url)

    # Test GET
    res_get = client.get("/sw.js")
    assert res_get.status_code == 200
    assert "application/javascript" in res_get.headers["content-type"]
    assert res_get.headers["service-worker-allowed"] == "/"
    assert "no-cache" in res_get.headers["cache-control"]
    assert "no-store" in res_get.headers["cache-control"]
    content = res_get.text
    assert "install" in content
    assert "skipWaiting" in content
    assert "clients.claim()" in content
    assert "fetch" in content

    # Test HEAD
    res_head = client.head("/sw.js")
    assert res_head.status_code == 200
    assert res_head.headers["service-worker-allowed"] == "/"


def test_m1_pwa_head_tags_across_entry_points(tmp_path: Path):
    """Verify manifest link, pwa-register.js, and favicon tags across all entry templates."""
    s, ids_dict = _setup_pwa_network_env(tmp_path)
    app = create_app(s)
    client = TestClient(app, base_url=s.public_url)

    entry_routes = ["/", "/explore/accounts"]
    for route in entry_routes:
        res = client.get(route, headers={"accept": "text/html"})
        assert res.status_code == 200, f"Route {route} failed with {res.status_code}"
        html = res.text
        assert 'href="/manifest.json"' in html or 'href="/site.webmanifest"' in html
        assert "pwa-register.js" in html

    # Login template (script-free for credential isolation, includes full PWA head tags)
    res_login = client.get("/login")
    assert res_login.status_code == 200
    assert 'href="/manifest.json"' in res_login.text
    assert 'name="theme-color"' in res_login.text

    # Also verify authenticated console shell
    client.cookies.set("claire_session", ids_dict["admin_session"])
    res_console = client.get("/admin/instance")
    assert res_console.status_code == 200
    assert "pwa-register.js" in res_console.text


# ==============================================================================
# Milestone 2 Acceptance: Root Launch & Per-User Workspace Routing (/?pwa=1)
# ==============================================================================

def test_m2_root_launch_authenticated_member(tmp_path: Path):
    """Authenticated member launching /?pwa=1 is redirected to their workspace /@{username}/?pwa=1."""
    s, ids_dict = _setup_pwa_network_env(tmp_path)
    app = create_app(s)
    client = TestClient(app, base_url=s.public_url, follow_redirects=False)
    client.cookies.set("claire_session", ids_dict["alice_session"])

    res = client.get("/?pwa=1")
    assert res.status_code == 302
    assert res.headers["location"] == "/@alice/?pwa=1"
    assert res.headers.get("cache-control") == "no-store"


def test_m2_root_launch_anonymous_readonly_true(tmp_path: Path):
    """Anonymous user with anonymous_readonly=True launching /?pwa=1 is redirected to admin's workspace."""
    s, _ = _setup_pwa_network_env(tmp_path, anonymous_readonly=True)
    app = create_app(s)
    client = TestClient(app, base_url=s.public_url, follow_redirects=False)

    res = client.get("/?pwa=1")
    assert res.status_code == 302
    assert res.headers["location"] == "/@adminuser/?pwa=1"
    assert res.headers.get("cache-control") == "no-store"


def test_m2_root_launch_anonymous_readonly_false(tmp_path: Path):
    """Anonymous user with anonymous_readonly=False launching /?pwa=1 is redirected to /login."""
    s, _ = _setup_pwa_network_env(tmp_path, anonymous_readonly=False)
    app = create_app(s)
    client = TestClient(app, base_url=s.public_url, follow_redirects=False)

    res = client.get("/?pwa=1")
    assert res.status_code == 302
    assert res.headers["location"] == "/login"
    assert res.headers.get("cache-control") == "no-store"


def test_m2_root_launch_single_mode(tmp_path: Path):
    """Single mode instance launching /?pwa=1 renders the default workspace at / directly (200 OK)."""
    s, _ = _setup_pwa_single_env(tmp_path)
    app = create_app(s)
    client = TestClient(app, base_url=s.public_url, follow_redirects=False)

    res = client.get("/?pwa=1")
    assert res.status_code == 200
    assert "Claire Bible" in res.text or "reader" in res.text


def test_m2_normal_root_preservation(tmp_path: Path):
    """Normal GET / without ?pwa=1 renders the public accounts directory in network mode."""
    s, _ = _setup_pwa_network_env(tmp_path)
    app = create_app(s)
    client = TestClient(app, base_url=s.public_url, follow_redirects=False)

    res = client.get("/")
    assert res.status_code == 200
    assert "public" in res.text.lower() or "accounts" in res.text.lower() or "explore" in res.text.lower()


def test_m2_query_parameters_preserved(tmp_path: Path):
    """Launching /?pwa=1&theme=10&custom=val preserves all query parameters upon redirection."""
    s, ids_dict = _setup_pwa_network_env(tmp_path)
    app = create_app(s)
    client = TestClient(app, base_url=s.public_url, follow_redirects=False)
    client.cookies.set("claire_session", ids_dict["alice_session"])

    res = client.get("/?pwa=1&theme=10&custom=val")
    assert res.status_code == 302
    assert res.headers["location"] == "/@alice/?pwa=1&theme=10&custom=val"


# ==============================================================================
# Milestone 3 Acceptance: Ingested Document Reading & Themes Filtering
# ==============================================================================

def test_m3_whoami_namespacing_and_relation_scoping(tmp_path: Path):
    """Verify /@{username}/whoami returns 'owner' for workspace owner and 'visitor' for others."""
    s, ids_dict = _setup_pwa_network_env(tmp_path)
    app = create_app(s)
    client = TestClient(app, base_url=s.public_url)

    # Alice checking her own workspace
    client.cookies.set("claire_session", ids_dict["alice_session"])
    res_owner = client.get("/@alice/whoami")
    assert res_owner.status_code == 200
    data_owner = res_owner.json()
    assert data_owner["relation"] == "owner"
    assert data_owner["scope"] == "owner"
    assert data_owner["is_admin"] is False

    # Bob visiting Alice's workspace
    client.cookies.set("claire_session", ids_dict["bob_session"])
    res_visitor = client.get("/@alice/whoami")
    assert res_visitor.status_code == 200
    data_visitor = res_visitor.json()
    assert data_visitor["relation"] == "visitor"
    assert data_visitor["scope"] in {"anonymous", "readonly"}
    assert data_visitor["is_admin"] is False

    # Anonymous visiting Alice's workspace
    client.cookies.delete("claire_session")
    res_anon = client.get("/@alice/whoami")
    assert res_anon.status_code == 200
    data_anon = res_anon.json()
    assert data_anon["relation"] == "visitor"
    assert data_anon["scope"] == "anonymous"


def test_m3_reader_js_reader_api_url_contract():
    """Verify reader.js wraps document, seen, title, share, and presentation API requests with readerApiUrl."""
    content = READER_JS_PATH.read_text(encoding="utf-8")
    assert "function readerApiUrl(path)" in content
    assert "readerApiUrl('document?id='" in content
    assert "readerApiUrl('document/seen')" in content
    assert "readerApiUrl('document/title')" in content
    assert "readerApiUrl('share')" in content
    assert "readerApiUrl('document/presentation" in content


def test_m3_render_graph_html_theme_isolation(tmp_path: Path):
    """Verify render_graph_html filters themes so Alice's workspace contains only Alice's themes, zero Bob themes."""
    s, ids_dict = _setup_pwa_network_env(tmp_path)
    app = create_app(s)
    client = TestClient(app, base_url=s.public_url)

    res = client.get("/@alice/")
    assert res.status_code == 200
    html = res.text
    # Alice's theme should be present
    assert "Alice Personal Theme" in html
    # Bob's private theme must be strictly excluded
    assert "Bob Secret Theme" not in html


def test_m3_cross_account_document_fallback_isolation(tmp_path: Path):
    """Accessing Bob's private document from Alice's workspace fails closed with 404."""
    s, ids_dict = _setup_pwa_network_env(tmp_path)
    app = create_app(s)
    client = TestClient(app, base_url=s.public_url)
    client.cookies.set("claire_session", ids_dict["alice_session"])

    # Document fetch for foreign private document
    res = client.get("/@alice/document?id=doc_bob_private")
    assert res.status_code == 404

    # Raw download fetch for foreign private document
    res_raw = client.get("/@alice/document/raw?id=doc_bob_private")
    assert res_raw.status_code == 404

    # Own document fetch succeeds
    res_own = client.get("/@alice/document?id=doc_alice_1")
    assert res_own.status_code == 200
    assert res_own.json()["title"] == "Alice In Wonderland Notes"


# ==============================================================================
# Milestone 4 Acceptance: Mobile Reader-First View & Comprehensive Verification
# ==============================================================================

def test_m4_app_js_mobile_reader_first_and_auto_select_contracts():
    """Verify app.js provides genuine isPwaOrMobileLaunch, shouldDefaultToReader, and auto-select logic."""
    content = APP_JS_PATH.read_text(encoding="utf-8")
    assert "function isPwaOrMobileLaunch()" in content
    assert "function shouldDefaultToReader(stats)" in content
    assert "initialDefaultToReader" in content
    assert "setCenterView('reader')" in content
    assert "allDocs[0].id" in content


def test_m4_syndication_strictly_excluded_from_pwa_and_mobile():
    """Verify syndication is excluded from manifests, index.html, and app.js defaults."""
    # Manifest
    for p in [MANIFEST_PATH, WEBMANIFEST_PATH]:
        data = json.loads(p.read_text(encoding="utf-8"))
        assert "syndication" not in data.get("start_url", "")
        for sc in data.get("shortcuts", []):
            assert "syndication" not in sc.get("url", "").lower()

    # index.html
    index_html = INDEX_HTML_PATH.read_text(encoding="utf-8")
    assert "syndication" not in index_html.lower()

    # app.js
    app_js = APP_JS_PATH.read_text(encoding="utf-8")
    assert "setCenterView('syndication')" not in app_js


def test_m4_end_to_end_reader_flow_in_workspace(tmp_path: Path):
    """End-to-end test verifying workspace HTML provides reader container and can fetch documents."""
    s, ids_dict = _setup_pwa_network_env(tmp_path)
    app = create_app(s)
    client = TestClient(app, base_url=s.public_url)
    client.cookies.set("claire_session", ids_dict["alice_session"])

    # Launch workspace
    res = client.get("/@alice/?pwa=1")
    assert res.status_code == 200
    # Container elements for reader and graph
    assert 'id="reader"' in res.text
    assert 'id="netwrap"' in res.text
    assert 'data-center-view=' in res.text

    # Fetch document list
    res_docs = client.get("/@alice/documents")
    assert res_docs.status_code == 200
    docs = res_docs.json().get("documents", [])
    assert len(docs) >= 1
    assert docs[0]["id"] == "doc_alice_1"

    # Fetch first document body directly
    res_doc = client.get(f"/@alice/document?id={docs[0]['id']}")
    assert res_doc.status_code == 200
    assert res_doc.json()["title"] == "Alice In Wonderland Notes"
