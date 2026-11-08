"""Tests for Milestone 3: Per-User Workspace Ingested Document Reading & Themes Filtering."""

from __future__ import annotations

import json
import os
import re
import sqlite3
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


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch):
    for name in list(os.environ):
        if name.startswith("CLAIRE_"):
            monkeypatch.delenv(name, raising=False)
    reset_token_stores()
    yield
    reset_token_stores()


def _setup_multi_account_network_env(tmp_path: Path, *, anonymous_readonly: bool = True):
    db_file = tmp_path / "claire.db"
    conn = dbm.connect(db_file)
    dbm.init_db(conn)
    now = time.time()
    salt = dbm.read_id_salt(conn)

    # 1. Admin account (id=1, username='adminuser')
    conn.execute(
        "UPDATE accounts SET username='adminuser', status='active', activated_at=?, profile_public=1 WHERE id=1",
        (now,),
    )

    # 2. Alice account (id=2, username='alice')
    alice_pub = ids.timestamp_id("accounts", salt=salt, unix_ms=int(now * 1000) + 1)
    cur = conn.execute(
        "INSERT INTO accounts (public_id, username, role, status, display_name, profile_public, created_at, activated_at, updated_at) "
        "VALUES (?, 'alice', 'member', 'active', 'Alice In Wonderland', 1, ?, ?, ?)",
        (alice_pub, now, now, now),
    )
    alice_id = int(cur.lastrowid)

    # 3. Bob account (id=3, username='bob')
    bob_pub = ids.timestamp_id("accounts", salt=salt, unix_ms=int(now * 1000) + 2)
    cur = conn.execute(
        "INSERT INTO accounts (public_id, username, role, status, display_name, profile_public, created_at, activated_at, updated_at) "
        "VALUES (?, 'bob', 'member', 'active', 'Bob Builder', 1, ?, ?, ?)",
        (bob_pub, now, now, now),
    )
    bob_id = int(cur.lastrowid)

    # Themes:
    # Admin themes (account_id=1):
    # Theme 0 was created by init_db with is_default=1
    conn.execute("UPDATE themes SET label='Admin Public Base', is_public=1 WHERE id=0")
    num_admin_priv = ids.timestamp_id("themes", salt=salt, unix_ms=int(now * 1000) + 11)
    conn.execute(
        "INSERT INTO themes (id, seq, number, label, account_id, is_default, is_public, is_collaborator_accessible, created_at, updated_at) "
        "VALUES (1, 1, ?, 'Admin Secret Theme', 1, 0, 0, 0, ?, ?)",
        (num_admin_priv, now, now),
    )

    # Alice themes (account_id=2):
    num_alice_pub = ids.timestamp_id("themes", salt=salt, unix_ms=int(now * 1000) + 20)
    conn.execute(
        "INSERT INTO themes (id, seq, number, label, account_id, is_default, is_public, is_collaborator_accessible, created_at, updated_at) "
        "VALUES (10, 10, ?, 'Alice Public Work', ?, 1, 1, 0, ?, ?)",
        (num_alice_pub, alice_id, now, now),
    )
    num_alice_collab = ids.timestamp_id("themes", salt=salt, unix_ms=int(now * 1000) + 21)
    conn.execute(
        "INSERT INTO themes (id, seq, number, label, account_id, is_default, is_public, is_collaborator_accessible, created_at, updated_at) "
        "VALUES (11, 11, ?, 'Alice Collab Work', ?, 0, 0, 1, ?, ?)",
        (num_alice_collab, alice_id, now, now),
    )
    num_alice_priv = ids.timestamp_id("themes", salt=salt, unix_ms=int(now * 1000) + 22)
    conn.execute(
        "INSERT INTO themes (id, seq, number, label, account_id, is_default, is_public, is_collaborator_accessible, created_at, updated_at) "
        "VALUES (12, 12, ?, 'Alice Secret Work', ?, 0, 0, 0, ?, ?)",
        (num_alice_priv, alice_id, now, now),
    )

    # Bob themes (account_id=3):
    num_bob_pub = ids.timestamp_id("themes", salt=salt, unix_ms=int(now * 1000) + 30)
    conn.execute(
        "INSERT INTO themes (id, seq, number, label, account_id, is_default, is_public, is_collaborator_accessible, created_at, updated_at) "
        "VALUES (20, 20, ?, 'Bob Public Work', ?, 1, 1, 0, ?, ?)",
        (num_bob_pub, bob_id, now, now),
    )
    num_bob_priv = ids.timestamp_id("themes", salt=salt, unix_ms=int(now * 1000) + 31)
    conn.execute(
        "INSERT INTO themes (id, seq, number, label, account_id, is_default, is_public, is_collaborator_accessible, created_at, updated_at) "
        "VALUES (21, 21, ?, 'Bob Secret Work', ?, 0, 0, 0, ?, ?)",
        (num_bob_priv, bob_id, now, now),
    )

    # Network mode setting
    store = accounts_store.AccountStore(conn)
    store.set_setting("mode", "network", updated_by=1)
    conn.commit()

    # Sessions
    admin_session = accounts_store.create_account_session(conn, account_id=1, scope="owner", now=now)
    alice_session = accounts_store.create_account_session(conn, account_id=alice_id, scope="owner", now=now)
    bob_session = accounts_store.create_account_session(conn, account_id=bob_id, scope="owner", now=now)
    conn.commit()

    # Ingest test document into Alice's default theme (10)
    alice_doc = Document(
        id="doc_alice_wonderland_1",
        url="https://example.com/alice/ch1",
        title="Down the Rabbit-Hole",
        raw_text="Alice was beginning to get very tired of sitting by her sister on the bank.",
        source_type="web",
        content_hash="hash_alice_1",
    )
    dbm.insert_document(conn, alice_doc, theme_id=10)

    # Ingest test document into Alice's private theme (12)
    alice_priv_doc = Document(
        id="doc_alice_private_1",
        url="https://example.com/alice/secret",
        title="Alice Secret Diary",
        raw_text="Very private notes by Alice.",
        source_type="web",
        content_hash="hash_alice_priv_1",
    )
    dbm.insert_document(conn, alice_priv_doc, theme_id=12)

    # Ingest test document into Bob's private theme (21)
    bob_priv_doc = Document(
        id="doc_bob_private_1",
        url="https://example.com/bob/secret",
        title="Bob Secret Plans",
        raw_text="Top secret architectural blueprints by Bob.",
        source_type="web",
        content_hash="hash_bob_priv_1",
    )
    dbm.insert_document(conn, bob_priv_doc, theme_id=21)
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
        "admin_session": admin_session,
        "alice_session": alice_session,
        "bob_session": bob_session,
    }


# =========================================================================
# Group 1: Static Contract Tests (app.js & reader.js)
# =========================================================================

def test_static_app_js_whoami_call():
    """Verify that app.js calls apiUrl('whoami'), exposes window.apiUrl, and does not use rootUrl('whoami')."""
    app_js_path = Path(__file__).resolve().parent.parent / "src" / "claire" / "static" / "js" / "app.js"
    content = app_js_path.read_text(encoding="utf-8")

    assert "fetch(apiUrl('whoami'))" in content, "app.js must call fetch(apiUrl('whoami'))"
    assert "fetch(rootUrl('whoami'))" not in content, "app.js must not call fetch(rootUrl('whoami'))"
    assert "window.apiUrl = apiUrl;" in content, "app.js must export window.apiUrl = apiUrl;"


def test_static_app_js_relation_handling():
    """Verify that app.js inspects d.relation from whoami to set effectiveScope and enforce readonly for visitors."""
    app_js_path = Path(__file__).resolve().parent.parent / "src" / "claire" / "static" / "js" / "app.js"
    content = app_js_path.read_text(encoding="utf-8")

    assert "d.relation" in content, "app.js must inspect d.relation in whoami handler"
    assert "setAccessScope(effectiveScope)" in content, "app.js must pass effectiveScope to setAccessScope"
    assert "d.relation === 'visitor'" in content or "d.relation !== 'owner'" in content


def test_static_reader_js_reader_api_url_helper_and_calls():
    """Verify readerApiUrl helper is defined and wraps all 8 document/presentation/share endpoints."""
    reader_js_path = Path(__file__).resolve().parent.parent / "src" / "claire" / "static" / "js" / "reader.js"
    content = reader_js_path.read_text(encoding="utf-8")

    assert "function readerApiUrl(path)" in content, "reader.js must define readerApiUrl(path)"

    expected_calls = [
        "fetch(readerApiUrl('document/seen')",
        "fetch(readerApiUrl('document?id=' + encodeURIComponent(docId)))",
        "fetch(readerApiUrl('document?id=' + encodeURIComponent(did)))",
        "fetch(readerApiUrl('document/title')",
        "fetch(readerApiUrl('share')",
        "fetch(readerApiUrl('document/presentation/generate?id=' + encodeURIComponent(targetId)",
        "fetch(readerApiUrl('document/presentation?id=' + encodeURIComponent(targetId)))",
    ]
    for expected in expected_calls:
        assert expected in content, f"Expected {expected} in reader.js"

    # Verify no raw un-prefixed fetch calls remain for document/presentation/share
    assert "fetch('document/seen'" not in content
    assert "fetch('document?id=" not in content
    assert "fetch('document/title'" not in content
    assert "fetch('share'" not in content
    assert "fetch('/document/presentation" not in content


# =========================================================================
# Group 2: Unit Tests for render_graph_html Themes Scoping and Isolation
# =========================================================================

def test_render_graph_html_account_isolation_owner(tmp_path: Path):
    """Verify render_graph_html isolates themes per account when include_private=True."""
    s, env = _setup_multi_account_network_env(tmp_path)

    # 1. Admin (account_id=1) view
    admin_html = render_graph_html(s, account_id=1, include_private=True)
    assert "Admin Public Base" in admin_html
    assert "Admin Secret Theme" in admin_html
    assert "Alice Public Work" not in admin_html
    assert "Alice Secret Work" not in admin_html
    assert "Bob Public Work" not in admin_html

    # 2. Alice (account_id=2) view
    alice_html = render_graph_html(s, account_id=env["alice_id"], include_private=True)
    assert "Alice Public Work" in alice_html
    assert "Alice Collab Work" in alice_html
    assert "Alice Secret Work" in alice_html
    assert "Admin Public Base" not in alice_html
    assert "Admin Secret Theme" not in alice_html
    assert "Bob Public Work" not in alice_html

    # 3. Bob (account_id=3) view
    bob_html = render_graph_html(s, account_id=env["bob_id"], include_private=True)
    assert "Bob Public Work" in bob_html
    assert "Bob Secret Work" in bob_html
    assert "Alice Public Work" not in bob_html
    assert "Admin Secret Theme" not in bob_html


def test_render_graph_html_account_isolation_collaborator(tmp_path: Path):
    """Verify collaborator view includes public and collaborator-accessible themes, excluding purely private ones."""
    s, env = _setup_multi_account_network_env(tmp_path)

    alice_collab_html = render_graph_html(
        s,
        account_id=env["alice_id"],
        include_private=False,
        collaborator=True,
    )
    assert "Alice Public Work" in alice_collab_html
    assert "Alice Collab Work" in alice_collab_html
    assert "Alice Secret Work" not in alice_collab_html
    assert "Admin Public Base" not in alice_collab_html


def test_render_graph_html_account_isolation_anonymous(tmp_path: Path):
    """Verify anonymous view includes only public themes for the target account."""
    s, env = _setup_multi_account_network_env(tmp_path)

    alice_anon_html = render_graph_html(
        s,
        account_id=env["alice_id"],
        include_private=False,
        collaborator=False,
    )
    assert "Alice Public Work" in alice_anon_html
    assert "Alice Collab Work" not in alice_anon_html
    assert "Alice Secret Work" not in alice_anon_html
    assert "Admin Public Base" not in alice_anon_html
    assert "Bob Public Work" not in alice_anon_html


def test_render_graph_html_single_mode_default_theme(tmp_path: Path):
    """Verify that in single mode (multi_theme=False), default single theme dict is rendered."""
    db_file = tmp_path / "claire.db"
    conn = dbm.connect(db_file)
    dbm.init_db(conn)
    conn.close()

    s = Settings(
        CLAIRE_DB_PATH=str(db_file),
        CLAIRE_VAULT_PATH=str(tmp_path / "vault"),
        CLAIRE_MULTI_THEME=False,
    )
    html = render_graph_html(s)
    assert "기본 지식베이스" in html


def test_render_graph_html_theme_param_account_fallback(tmp_path: Path):
    """Verify render_graph_html resolves account_id from theme object if account_id is None."""
    s, env = _setup_multi_account_network_env(tmp_path)
    tm = ThemeManager(s)
    alice_theme = tm.get_theme(10, include_private=True)

    html = render_graph_html(s, theme=alice_theme, account_id=None, include_private=True)
    assert "Alice Public Work" in html
    assert "Admin Secret Theme" not in html


# =========================================================================
# Group 3: Integration Tests for whoami Endpoint Namespacing
# =========================================================================

def test_whoami_network_mode_owner_relation(tmp_path: Path):
    """Workspace owner calling GET /@{username}/whoami receives relation: 'owner'."""
    s, env = _setup_multi_account_network_env(tmp_path)
    app = create_app(s)

    with TestClient(app, base_url=s.public_url, follow_redirects=False) as client:
        client.cookies.set("claire_session", env["alice_session"])
        res = client.get("/@alice/whoami")
        assert res.status_code == 200
        data = res.json()
        assert data["relation"] == "owner"
        assert data["scope"] == "owner"
        assert data["is_admin"] is False
        assert data["instance"]["mode"] == "network"


def test_whoami_network_mode_cross_account_visitor_relation(tmp_path: Path):
    """Cross-account visitor (Bob visiting Alice) receives relation: 'visitor' while preserving scope='owner'."""
    s, env = _setup_multi_account_network_env(tmp_path)
    app = create_app(s)

    with TestClient(app, base_url=s.public_url, follow_redirects=False) as client:
        client.cookies.set("claire_session", env["bob_session"])
        res = client.get("/@alice/whoami")
        assert res.status_code == 200
        data = res.json()
        assert data["relation"] == "visitor"
        assert data["scope"] == "owner"
        assert data["is_admin"] is False


def test_whoami_network_mode_anonymous_visitor_relation(tmp_path: Path):
    """Anonymous visitor on Alice's workspace receives relation: 'visitor' under anonymous_readonly."""
    s, env = _setup_multi_account_network_env(tmp_path)
    app = create_app(s)

    with TestClient(app, base_url=s.public_url, follow_redirects=False) as client:
        res = client.get("/@alice/whoami")
        assert res.status_code == 200
        data = res.json()
        assert data["relation"] == "visitor"
        assert data["scope"] == "anonymous"
        assert data["is_admin"] is False


def test_whoami_network_mode_admin_visiting_member_relation(tmp_path: Path):
    """Admin visiting Alice's workspace receives relation: 'visitor' with is_admin: True."""
    s, env = _setup_multi_account_network_env(tmp_path)
    app = create_app(s)

    with TestClient(app, base_url=s.public_url, follow_redirects=False) as client:
        client.cookies.set("claire_session", env["admin_session"])
        res = client.get("/@alice/whoami")
        assert res.status_code == 200
        data = res.json()
        assert data["relation"] == "visitor"
        assert data["is_admin"] is True


def test_whoami_network_mode_admin_own_workspace(tmp_path: Path):
    """Admin visiting Admin's own workspace receives relation: 'owner' and is_admin: True."""
    s, env = _setup_multi_account_network_env(tmp_path)
    app = create_app(s)

    with TestClient(app, base_url=s.public_url, follow_redirects=False) as client:
        client.cookies.set("claire_session", env["admin_session"])
        res = client.get("/@adminuser/whoami")
        assert res.status_code == 200
        data = res.json()
        assert data["relation"] == "owner"
        assert data["is_admin"] is True


def test_whoami_network_mode_unprefixed_anonymous_rejected(tmp_path: Path):
    """Un-prefixed GET /whoami in network mode returns 404 for anonymous users (Rule N3)."""
    s, env = _setup_multi_account_network_env(tmp_path)
    app = create_app(s)

    with TestClient(app, base_url=s.public_url, follow_redirects=False) as client:
        res = client.get("/whoami")
        assert res.status_code == 404


# =========================================================================
# Group 4: Integration Tests for Workspace HTML Bootstrap (GET /@{username}/)
# =========================================================================

def test_workspace_html_themes_bootstrap_owner(tmp_path: Path):
    """GET /@alice/ with Alice session bootstraps all Alice themes and zero Bob/Admin themes."""
    s, env = _setup_multi_account_network_env(tmp_path)
    app = create_app(s)

    with TestClient(app, base_url=s.public_url, follow_redirects=False) as client:
        client.cookies.set("claire_session", env["alice_session"])
        res = client.get("/@alice/")
        assert res.status_code == 200
        html = res.text
        assert "Alice Public Work" in html
        assert "Alice Secret Work" in html
        assert "Bob Public Work" not in html
        assert "Admin Secret Theme" not in html


def test_workspace_html_themes_bootstrap_visitor(tmp_path: Path):
    """GET /@alice/ with Bob session bootstraps only Alice's public themes."""
    s, env = _setup_multi_account_network_env(tmp_path)
    app = create_app(s)

    with TestClient(app, base_url=s.public_url, follow_redirects=False) as client:
        client.cookies.set("claire_session", env["bob_session"])
        res = client.get("/@alice/")
        assert res.status_code == 200
        html = res.text
        assert "Alice Public Work" in html
        assert "Alice Secret Work" not in html
        assert "Bob Secret Work" not in html
        assert "Admin Secret Theme" not in html


def test_workspace_html_themes_bootstrap_anonymous(tmp_path: Path):
    """GET /@alice/ anonymously bootstraps only Alice's public themes."""
    s, env = _setup_multi_account_network_env(tmp_path)
    app = create_app(s)

    with TestClient(app, base_url=s.public_url, follow_redirects=False) as client:
        res = client.get("/@alice/")
        assert res.status_code == 200
        html = res.text
        assert "Alice Public Work" in html
        assert "Alice Secret Work" not in html
        assert "Bob Public Work" not in html


# =========================================================================
# Group 5: Integration Tests for Reader Endpoints under /@{username}/
# =========================================================================

def test_reader_document_get_namespaced(tmp_path: Path):
    """GET /@alice/document?id=doc_alice_wonderland_1 returns document detail in user workspace."""
    s, env = _setup_multi_account_network_env(tmp_path)
    app = create_app(s)

    with TestClient(app, base_url=s.public_url, follow_redirects=False) as client:
        client.cookies.set("claire_session", env["alice_session"])
        res = client.get("/@alice/document?id=doc_alice_wonderland_1")
        assert res.status_code == 200
        data = res.json()
        assert data["id"] == "doc_alice_wonderland_1"
        assert data["title"] == "Down the Rabbit-Hole"
        assert data["source_type"] == "web"


def test_reader_document_seen_owner_and_cross_account(tmp_path: Path):
    """POST /@alice/document/seen succeeds for owner Alice, rejects for cross-account visitor Bob."""
    s, env = _setup_multi_account_network_env(tmp_path)
    app = create_app(s)

    headers = {"Origin": s.public_url.rstrip("/"), "Sec-Fetch-Site": "same-origin"}
    with TestClient(app, base_url=s.public_url, follow_redirects=False) as client:
        # Cross-account visitor Bob should be denied write access
        client.cookies.set("claire_session", env["bob_session"])
        bob_res = client.post(
            "/@alice/document/seen",
            json={"id": "doc_alice_wonderland_1"},
            headers=headers,
        )
        assert bob_res.status_code in {401, 403, 404}

        # Owner Alice succeeds
        client.cookies.clear()
        client.cookies.set("claire_session", env["alice_session"])
        alice_res = client.post(
            "/@alice/document/seen",
            json={"id": "doc_alice_wonderland_1"},
            headers=headers,
        )
        assert alice_res.status_code == 200
        data = alice_res.json()
        assert data["id"] == "doc_alice_wonderland_1"
        assert data["seen"] is True


def test_reader_document_title_owner_and_cross_account(tmp_path: Path):
    """POST /@alice/document/title updates document title for owner, rejects for visitor."""
    s, env = _setup_multi_account_network_env(tmp_path)
    app = create_app(s)

    headers = {"Origin": s.public_url.rstrip("/"), "Sec-Fetch-Site": "same-origin"}
    with TestClient(app, base_url=s.public_url, follow_redirects=False) as client:
        # Visitor Bob denied
        client.cookies.set("claire_session", env["bob_session"])
        bob_res = client.post(
            "/@alice/document/title",
            json={"id": "doc_alice_wonderland_1", "title": "Tampered By Bob"},
            headers=headers,
        )
        assert bob_res.status_code in {401, 403, 404}

        # Owner Alice succeeds
        client.cookies.clear()
        client.cookies.set("claire_session", env["alice_session"])
        new_title = "Down the Rabbit-Hole (Revised Edition)"
        alice_res = client.post(
            "/@alice/document/title",
            json={"id": "doc_alice_wonderland_1", "title": new_title},
            headers=headers,
        )
        assert alice_res.status_code == 200
        data = alice_res.json()
        assert data["id"] == "doc_alice_wonderland_1"
        assert data["title"] == new_title

        # Verify title updated in document get
        check = client.get("/@alice/document?id=doc_alice_wonderland_1")
        assert check.status_code == 200
        assert check.json()["title"] == new_title


def test_reader_share_post_namespaced(tmp_path: Path):
    """POST /@alice/share generates document share token under user workspace."""
    s, env = _setup_multi_account_network_env(tmp_path)
    app = create_app(s)

    headers = {"Origin": s.public_url.rstrip("/"), "Sec-Fetch-Site": "same-origin"}
    with TestClient(app, base_url=s.public_url, follow_redirects=False) as client:
        client.cookies.set("claire_session", env["alice_session"])
        res = client.post(
            "/@alice/share",
            json={"doc_id": "doc_alice_wonderland_1"},
            headers=headers,
        )
        assert res.status_code == 200
        data = res.json()
        assert "token" in data or "path" in data


def test_reader_document_cross_account_fallback_isolation(tmp_path: Path):
    """Alice in her workspace requesting Bob's private document gets 404 (no cross-account leakage)."""
    s, env = _setup_multi_account_network_env(tmp_path)
    app = create_app(s)

    with TestClient(app, base_url=s.public_url, follow_redirects=False) as client:
        client.cookies.set("claire_session", env["alice_session"])
        res = client.get("/@alice/document?id=doc_bob_private_1")
        assert res.status_code == 404


def test_reader_document_visitor_private_fallback_isolation(tmp_path: Path):
    """Cross-account visitor Bob cannot access Alice's private doc (404), but can access public doc (200)."""
    s, env = _setup_multi_account_network_env(tmp_path)
    app = create_app(s)

    with TestClient(app, base_url=s.public_url, follow_redirects=False) as client:
        client.cookies.set("claire_session", env["bob_session"])

        # Bob can read Alice's public doc
        res_pub = client.get("/@alice/document?id=doc_alice_wonderland_1")
        assert res_pub.status_code == 200
        assert res_pub.json()["title"] == "Down the Rabbit-Hole"

        # Bob cannot read Alice's private doc
        res_priv = client.get("/@alice/document?id=doc_alice_private_1")
        assert res_priv.status_code == 404


def test_reader_share_cross_account_fallback_isolation(tmp_path: Path):
    """Alice calling POST /@alice/share with Bob's document id is rejected with 404."""
    s, env = _setup_multi_account_network_env(tmp_path)
    app = create_app(s)

    headers = {"Origin": s.public_url.rstrip("/"), "Sec-Fetch-Site": "same-origin"}
    with TestClient(app, base_url=s.public_url, follow_redirects=False) as client:
        client.cookies.set("claire_session", env["alice_session"])
        res = client.post(
            "/@alice/share",
            json={"doc_id": "doc_bob_private_1"},
            headers=headers,
        )
        assert res.status_code == 404


def test_reader_document_raw_cross_account_fallback_isolation(tmp_path: Path):
    """Alice requesting Bob's document via /@alice/document/raw is rejected with 404."""
    s, env = _setup_multi_account_network_env(tmp_path)
    app = create_app(s)

    with TestClient(app, base_url=s.public_url, follow_redirects=False) as client:
        client.cookies.set("claire_session", env["alice_session"])
        res = client.get("/@alice/document/raw?id=doc_bob_private_1")
        assert res.status_code == 404

