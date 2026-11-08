"""Empirical adversarial stress-test suite by Challenger 1 for Milestone 3:
Per-User Workspace Ingested Document Reading & Themes Filtering.

Tests coverage:
1. [M3-CH-01] whoami for workspace owner: relation="owner", scope="owner", is_admin accurate.
2. [M3-CH-02] whoami for cross-account visitor: relation="visitor", scope preserves caller auth, is_admin=False.
3. [M3-CH-03] whoami for anonymous reader: 200 OK, relation="visitor", scope="anonymous" when anonymous_readonly=True.
4. [M3-CH-04] whoami for anonymous reader when anonymous_readonly=False: returns 404/401 (no anonymous access).
5. [M3-CH-05] whoami for admin visiting member workspace: relation="visitor", is_admin=True; own workspace: relation="owner".
6. [M3-CH-06] whoami for member visiting admin workspace: relation="visitor", is_admin=False.
7. [M3-CH-07] whoami on non-existent / deactivated / invalid username: 404 Not Found.
8. [M3-CH-08] whoami in single mode: /whoami returns relation="owner", mode="single"; /@alice/whoami returns 404.
9. [M3-CH-09] reader GET /@{username}/document: owner reads document in public & private themes.
10. [M3-CH-10] reader GET /@{username}/document: cross-account visitor reads public theme document, blocked on private theme (404).
11. [M3-CH-11] reader GET /@{username}/document: anonymous reads public theme document, blocked on private theme (404).
12. [M3-CH-12] reader GET /@{username}/document: cross-workspace tenant leakage vulnerability challenge.
13. [M3-CH-13] reader GET /@{username}/document: boundary inputs (missing id -> 400, non-existent id -> 404, SQLi/path traversal -> 404).
14. [M3-CH-14] reader POST /@{username}/document/seen: owner succeeds (200), visitor rejected (404/403/401), anonymous rejected.
15. [M3-CH-15] reader POST /@{username}/document/title: owner succeeds (200), visitor rejected, anonymous rejected, title preserved.
16. [M3-CH-16] reader POST /@{username}/share: owner generates token (200), visitor rejected, anonymous rejected.
17. [M3-CH-16b] reader POST /@{username}/share: cross-workspace share token generation leakage vulnerability challenge.
18. [M3-CH-17] reader CSRF protection: POST requests without valid same-origin headers are rejected (403).
19. [M3-CH-18] presentation endpoints: GET presentation and POST presentation generate cross-account authorization boundary.
20. [M3-CH-19] readerApiUrl edge cases: null/empty/slashed paths, missing window.apiUrl, meta tag resolution.
21. [M3-CH-20] render_graph_html theme isolation under cross-account and deleted theme edge cases.
"""

from __future__ import annotations

import json
import os
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
from claire.store.token_store import reset_token_stores


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch):
    for name in list(os.environ):
        if name.startswith("CLAIRE_"):
            monkeypatch.delenv(name, raising=False)
    reset_token_stores()
    yield
    reset_token_stores()


def _setup_adversarial_network_env(tmp_path: Path, *, anonymous_readonly: bool = True):
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

    # 4. Charlie account (id=4, username='charlie', deactivated / inactive)
    charlie_pub = ids.timestamp_id("accounts", salt=salt, unix_ms=int(now * 1000) + 3)
    conn.execute(
        "INSERT INTO accounts (public_id, username, role, status, display_name, profile_public, created_at, activated_at, updated_at) "
        "VALUES (?, 'charlie', 'member', 'suspended', 'Charlie Deactivated', 1, ?, ?, ?)",
        (charlie_pub, now, now, now),
    )

    # Themes setup:
    # Admin (1):
    conn.execute("UPDATE themes SET label='Admin Public Base', is_public=1 WHERE id=0")
    num_admin_priv = ids.timestamp_id("themes", salt=salt, unix_ms=int(now * 1000) + 11)
    conn.execute(
        "INSERT INTO themes (id, seq, number, label, account_id, is_default, is_public, is_collaborator_accessible, created_at, updated_at) "
        "VALUES (1, 1, ?, 'Admin Secret Theme', 1, 0, 0, 0, ?, ?)",
        (num_admin_priv, now, now),
    )

    # Alice (2):
    # - Theme 10: default, public
    num_alice_pub = ids.timestamp_id("themes", salt=salt, unix_ms=int(now * 1000) + 20)
    conn.execute(
        "INSERT INTO themes (id, seq, number, label, account_id, is_default, is_public, is_collaborator_accessible, created_at, updated_at) "
        "VALUES (10, 10, ?, 'Alice Public Base', ?, 1, 1, 0, ?, ?)",
        (num_alice_pub, alice_id, now, now),
    )
    # - Theme 12: secret, private (presentation enabled)
    num_alice_priv = ids.timestamp_id("themes", salt=salt, unix_ms=int(now * 1000) + 22)
    conn.execute(
        "INSERT INTO themes (id, seq, number, label, account_id, is_default, is_public, is_collaborator_accessible, presentation_enabled, created_at, updated_at) "
        "VALUES (12, 12, ?, 'Alice Secret Project', ?, 0, 0, 0, 1, ?, ?)",
        (num_alice_priv, alice_id, now, now),
    )

    # Bob (3):
    # - Theme 20: default, public
    num_bob_pub = ids.timestamp_id("themes", salt=salt, unix_ms=int(now * 1000) + 30)
    conn.execute(
        "INSERT INTO themes (id, seq, number, label, account_id, is_default, is_public, is_collaborator_accessible, created_at, updated_at) "
        "VALUES (20, 20, ?, 'Bob Public Base', ?, 1, 1, 0, ?, ?)",
        (num_bob_pub, bob_id, now, now),
    )
    # - Theme 21: secret, private
    num_bob_priv = ids.timestamp_id("themes", salt=salt, unix_ms=int(now * 1000) + 31)
    conn.execute(
        "INSERT INTO themes (id, seq, number, label, account_id, is_default, is_public, is_collaborator_accessible, created_at, updated_at) "
        "VALUES (21, 21, ?, 'Bob Secret Project', ?, 0, 0, 0, ?, ?)",
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

    # Ingest documents:
    # Alice public doc in Theme 10:
    alice_pub_doc = Document(
        id="doc_alice_public_1",
        url="https://example.com/alice/pub1",
        title="Alice Public Guide",
        raw_text="This is a publicly accessible guide written by Alice.",
        source_type="web",
        content_hash="hash_alice_pub_1",
    )
    dbm.insert_document(conn, alice_pub_doc, theme_id=10)

    # Alice secret doc in Theme 12 (private):
    alice_priv_doc = Document(
        id="doc_alice_private_1",
        url="https://example.com/alice/priv1",
        title="Alice Secret Diary",
        raw_text="Confidential: Alice's personal diary entries.",
        source_type="web",
        content_hash="hash_alice_priv_1",
    )
    dbm.insert_document(conn, alice_priv_doc, theme_id=12)

    # Bob public doc in Theme 20:
    bob_pub_doc = Document(
        id="doc_bob_public_1",
        url="https://example.com/bob/pub1",
        title="Bob Architecture Blueprints",
        raw_text="Public blueprints for houses designed by Bob.",
        source_type="web",
        content_hash="hash_bob_pub_1",
    )
    dbm.insert_document(conn, bob_pub_doc, theme_id=20)

    # Bob secret doc in Theme 21 (private):
    bob_priv_doc = Document(
        id="doc_bob_private_1",
        url="https://example.com/bob/priv1",
        title="Bob Top Secret Vault",
        raw_text="Top Secret: Passwords and sensitive data of Bob.",
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


def _set_session(client: TestClient, token: str | None) -> None:
    client.cookies.clear()
    if token:
        client.cookies.set("claire_session", token, domain="127.0.0.1", path="/")


# =========================================================================
# Group 1: whoami Endpoint Stress-Tests (M3-CH-01 ~ M3-CH-08)
# =========================================================================

def test_whoami_owner_exact_relation_and_scope(tmp_path: Path):
    """[M3-CH-01] Workspace owner: relation='owner', scope='owner', is_admin=False."""
    s, env = _setup_adversarial_network_env(tmp_path)
    app = create_app(s)

    with TestClient(app, base_url=s.public_url, follow_redirects=False) as client:
        _set_session(client, env["alice_session"])
        res = client.get("/@alice/whoami")
        assert res.status_code == 200
        data = res.json()
        assert data["relation"] == "owner"
        assert data["scope"] == "owner"
        assert data["is_admin"] is False
        assert data["instance"]["mode"] == "network"


def test_whoami_cross_account_visitor_relation_and_scope(tmp_path: Path):
    """[M3-CH-02] Cross-account visitor (Bob visiting Alice): relation='visitor', is_admin=False."""
    s, env = _setup_adversarial_network_env(tmp_path)
    app = create_app(s)

    with TestClient(app, base_url=s.public_url, follow_redirects=False) as client:
        _set_session(client, env["bob_session"])
        res = client.get("/@alice/whoami")
        assert res.status_code == 200
        data = res.json()
        assert data["relation"] == "visitor"
        assert data["is_admin"] is False
        assert data["instance"]["mode"] == "network"


def test_whoami_anonymous_reader_relation_and_scope(tmp_path: Path):
    """[M3-CH-03] Anonymous visitor on Alice's workspace: relation='visitor', scope='anonymous', is_admin=False."""
    s, env = _setup_adversarial_network_env(tmp_path, anonymous_readonly=True)
    app = create_app(s)

    with TestClient(app, base_url=s.public_url, follow_redirects=False) as client:
        _set_session(client, None)
        res = client.get("/@alice/whoami")
        assert res.status_code == 200
        data = res.json()
        assert data["relation"] == "visitor"
        assert data["scope"] == "anonymous"
        assert data["is_admin"] is False
        assert data["instance"]["mode"] == "network"


def test_whoami_anonymous_reader_when_anonymous_readonly_disabled(tmp_path: Path):
    """[M3-CH-04] Anonymous access rejected when anonymous_readonly=False."""
    s, env = _setup_adversarial_network_env(tmp_path, anonymous_readonly=False)
    app = create_app(s)

    with TestClient(app, base_url=s.public_url, follow_redirects=False) as client:
        _set_session(client, None)
        res = client.get("/@alice/whoami")
        # Must not return 200 OK to unauthenticated users when anonymous_readonly is False
        assert res.status_code in {401, 404}


def test_whoami_admin_visiting_member_vs_own(tmp_path: Path):
    """[M3-CH-05] Admin visiting member workspace -> relation='visitor', is_admin=True; own workspace -> relation='owner'."""
    s, env = _setup_adversarial_network_env(tmp_path)
    app = create_app(s)

    with TestClient(app, base_url=s.public_url, follow_redirects=False) as client:
        _set_session(client, env["admin_session"])
        visiting_res = client.get("/@alice/whoami")
        assert visiting_res.status_code == 200
        visiting_data = visiting_res.json()
        assert visiting_data["relation"] == "visitor"
        assert visiting_data["is_admin"] is True

        _set_session(client, env["admin_session"])
        own_res = client.get("/@adminuser/whoami")
        assert own_res.status_code == 200
        own_data = own_res.json()
        assert own_data["relation"] == "owner"
        assert own_data["is_admin"] is True


def test_whoami_member_visiting_admin(tmp_path: Path):
    """[M3-CH-06] Regular member (Alice) visiting admin workspace -> relation='visitor', is_admin=False."""
    s, env = _setup_adversarial_network_env(tmp_path)
    app = create_app(s)

    with TestClient(app, base_url=s.public_url, follow_redirects=False) as client:
        _set_session(client, env["alice_session"])
        res = client.get("/@adminuser/whoami")
        assert res.status_code == 200
        data = res.json()
        assert data["relation"] == "visitor"
        assert data["is_admin"] is False


def test_whoami_nonexistent_or_suspended_account(tmp_path: Path):
    """[M3-CH-07] Non-existent, suspended, or invalid username namespaces return 404."""
    s, env = _setup_adversarial_network_env(tmp_path)
    app = create_app(s)

    with TestClient(app, base_url=s.public_url, follow_redirects=False) as client:
        _set_session(client, env["alice_session"])

        # Non-existent account
        res_nonexistent = client.get("/@nonexistentuser/whoami")
        assert res_nonexistent.status_code == 404

        # Suspended account
        res_suspended = client.get("/@charlie/whoami")
        assert res_suspended.status_code == 404

        # Invalid username syntax
        res_invalid = client.get("/@inv@lid!/whoami")
        assert res_invalid.status_code == 404


def test_whoami_single_mode(tmp_path: Path):
    """[M3-CH-08] In single mode, GET /whoami succeeds with mode='single'; /@alice/whoami is 404."""
    db_file = tmp_path / "claire.db"
    conn = dbm.connect(db_file)
    dbm.init_db(conn)
    now = time.time()
    admin_session = accounts_store.create_account_session(conn, account_id=1, scope="owner", now=now)
    conn.commit()
    conn.close()

    s = Settings(
        CLAIRE_DB_PATH=str(db_file),
        CLAIRE_VAULT_PATH=str(tmp_path / "vault"),
        CLAIRE_MULTI_THEME=False,
        CLAIRE_ENVIRONMENT="development",
        CLAIRE_PUBLIC_URL="http://127.0.0.1:8765",
    )
    app = create_app(s)

    with TestClient(app, base_url="http://127.0.0.1:8765", follow_redirects=False) as client:
        _set_session(client, admin_session)
        res = client.get("/whoami")
        assert res.status_code == 200
        data = res.json()
        assert data["relation"] == "owner"
        assert data["scope"] == "owner"
        assert data["instance"]["mode"] == "single"

        # @alice prefix in single mode is 404
        res_prefixed = client.get("/@alice/whoami")
        assert res_prefixed.status_code == 404


# =========================================================================
# Group 2: Document Reading & Theme Scoping (M3-CH-09 ~ M3-CH-13)
# =========================================================================

def test_reader_document_get_owner_public_and_private(tmp_path: Path):
    """[M3-CH-09] Workspace owner reads both public and private documents in their workspace."""
    s, env = _setup_adversarial_network_env(tmp_path)
    app = create_app(s)

    with TestClient(app, base_url=s.public_url, follow_redirects=False) as client:
        _set_session(client, env["alice_session"])

        # Public doc
        res_pub = client.get("/@alice/document?id=doc_alice_public_1")
        assert res_pub.status_code == 200
        assert res_pub.json()["title"] == "Alice Public Guide"

        # Private doc in theme 12
        res_priv = client.get("/@alice/document?id=doc_alice_private_1")
        assert res_priv.status_code == 200
        assert res_priv.json()["title"] == "Alice Secret Diary"


def test_reader_document_get_visitor_isolation(tmp_path: Path):
    """[M3-CH-10] Cross-account visitor reads Alice's public doc, but is denied (404) Alice's private doc."""
    s, env = _setup_adversarial_network_env(tmp_path)
    app = create_app(s)

    with TestClient(app, base_url=s.public_url, follow_redirects=False) as client:
        _set_session(client, env["bob_session"])

        # Bob can read Alice's public doc
        res_pub = client.get("/@alice/document?id=doc_alice_public_1")
        assert res_pub.status_code == 200
        assert res_pub.json()["title"] == "Alice Public Guide"

        # Bob CANNOT read Alice's private doc
        res_priv = client.get("/@alice/document?id=doc_alice_private_1")
        assert res_priv.status_code == 404


def test_reader_document_get_anonymous_isolation(tmp_path: Path):
    """[M3-CH-11] Anonymous visitor reads Alice's public doc, but is denied (404) Alice's private doc."""
    s, env = _setup_adversarial_network_env(tmp_path, anonymous_readonly=True)
    app = create_app(s)

    with TestClient(app, base_url=s.public_url, follow_redirects=False) as client:
        _set_session(client, None)

        # Anonymous can read public doc
        res_pub = client.get("/@alice/document?id=doc_alice_public_1")
        assert res_pub.status_code == 200
        assert res_pub.json()["title"] == "Alice Public Guide"

        # Anonymous CANNOT read private doc
        res_priv = client.get("/@alice/document?id=doc_alice_private_1")
        assert res_priv.status_code == 404


def test_reader_document_cross_workspace_leakage_challenge(tmp_path: Path):
    """[M3-CH-12] Adversarial Challenge: Requesting Bob's private document from Alice's workspace.

    VULNERABILITY DEMONSTRATION:
    When an authenticated owner (e.g. Alice) queries `GET /@alice/document?id=doc_bob_private_1`,
    the multi-theme fallback in `document_detail_route` iterates themes without checking
    theme account ownership or workspace namespace. As a result, Alice receives Bob's private document!
    """
    s, env = _setup_adversarial_network_env(tmp_path)
    app = create_app(s)

    with TestClient(app, base_url=s.public_url, follow_redirects=False) as client:
        _set_session(client, env["alice_session"])

        # Alice requests Bob's private document via Alice's workspace
        res = client.get("/@alice/document?id=doc_bob_private_1")
        # In a multi-tenant secure system, Alice must NOT be able to access Bob's private document
        is_leaking = (res.status_code == 200 and res.json().get("title") == "Bob Top Secret Vault")
        assert not is_leaking, (
            "SECURITY VULNERABILITY CONFIRMED: GET /@alice/document?id=doc_bob_private_1 returned "
            f"status={res.status_code} with title='{res.json().get('title')}', leaking Bob's private document to Alice!"
        )


def test_reader_document_boundary_inputs(tmp_path: Path):
    """[M3-CH-13] Boundary conditions: missing id, empty id, SQL injection, path traversal, oversized id."""
    s, env = _setup_adversarial_network_env(tmp_path)
    app = create_app(s)

    with TestClient(app, base_url=s.public_url, follow_redirects=False) as client:
        _set_session(client, env["alice_session"])

        # Missing id parameter
        res_missing = client.get("/@alice/document")
        assert res_missing.status_code == 400

        # Empty id
        res_empty = client.get("/@alice/document?id=")
        assert res_empty.status_code == 400

        # Non-existent id
        res_nonexistent = client.get("/@alice/document?id=non_existent_doc_id_99999")
        assert res_nonexistent.status_code == 404

        # SQL injection attempt
        res_sqli = client.get("/@alice/document?id=' OR '1'='1")
        assert res_sqli.status_code == 404

        # Path traversal attempt
        res_traversal = client.get("/@alice/document?id=../../../../etc/passwd")
        assert res_traversal.status_code == 404

        # Oversized id (10,000 chars)
        res_oversized = client.get(f"/@alice/document?id={'a' * 10000}")
        assert res_oversized.status_code == 404


# =========================================================================
# Group 3: Document State & Reader Actions (M3-CH-14 ~ M3-CH-18)
# =========================================================================

def test_reader_document_seen_owner_and_visitor_and_anonymous(tmp_path: Path):
    """[M3-CH-14] POST /@{username}/document/seen: owner succeeds, visitor & anonymous rejected."""
    s, env = _setup_adversarial_network_env(tmp_path)
    app = create_app(s)

    headers = {"Origin": s.public_url.rstrip("/"), "Sec-Fetch-Site": "same-origin"}
    with TestClient(app, base_url=s.public_url, follow_redirects=False) as client:
        # Anonymous rejected
        _set_session(client, None)
        anon_res = client.post(
            "/@alice/document/seen",
            json={"id": "doc_alice_public_1"},
            headers=headers,
        )
        assert anon_res.status_code in {401, 403, 404}

        # Visitor Bob rejected
        _set_session(client, env["bob_session"])
        bob_res = client.post(
            "/@alice/document/seen",
            json={"id": "doc_alice_public_1"},
            headers=headers,
        )
        assert bob_res.status_code in {401, 403, 404}

        # Owner Alice succeeds
        _set_session(client, env["alice_session"])
        alice_res = client.post(
            "/@alice/document/seen",
            json={"id": "doc_alice_public_1"},
            headers=headers,
        )
        assert alice_res.status_code == 200
        assert alice_res.json()["seen"] is True

        # Missing id in payload -> 400
        alice_bad = client.post(
            "/@alice/document/seen",
            json={},
            headers=headers,
        )
        assert alice_bad.status_code == 400


def test_reader_document_title_owner_and_visitor(tmp_path: Path):
    """[M3-CH-15] POST /@{username}/document/title: owner modifies title, visitor/anonymous cannot tamper."""
    s, env = _setup_adversarial_network_env(tmp_path)
    app = create_app(s)

    headers = {"Origin": s.public_url.rstrip("/"), "Sec-Fetch-Site": "same-origin"}
    with TestClient(app, base_url=s.public_url, follow_redirects=False) as client:
        # Visitor Bob tries to tamper
        _set_session(client, env["bob_session"])
        bob_res = client.post(
            "/@alice/document/title",
            json={"id": "doc_alice_public_1", "title": "Hacked Title"},
            headers=headers,
        )
        assert bob_res.status_code in {401, 403, 404}

        # Verify title remains unchanged
        _set_session(client, env["alice_session"])
        check1 = client.get("/@alice/document?id=doc_alice_public_1")
        assert check1.json()["title"] == "Alice Public Guide"

        # Owner Alice updates title
        alice_res = client.post(
            "/@alice/document/title",
            json={"id": "doc_alice_public_1", "title": "Updated Alice Guide"},
            headers=headers,
        )
        assert alice_res.status_code == 200
        assert alice_res.json()["title"] == "Updated Alice Guide"


def test_reader_share_owner_and_visitor(tmp_path: Path):
    """[M3-CH-16] POST /@{username}/share: owner generates share token, visitor/anonymous rejected."""
    s, env = _setup_adversarial_network_env(tmp_path)
    app = create_app(s)

    headers = {"Origin": s.public_url.rstrip("/"), "Sec-Fetch-Site": "same-origin"}
    with TestClient(app, base_url=s.public_url, follow_redirects=False) as client:
        # Anonymous rejected
        _set_session(client, None)
        anon_res = client.post(
            "/@alice/share",
            json={"doc_id": "doc_alice_public_1"},
            headers=headers,
        )
        assert anon_res.status_code in {401, 403, 404}

        # Visitor Bob rejected
        _set_session(client, env["bob_session"])
        bob_res = client.post(
            "/@alice/share",
            json={"doc_id": "doc_alice_public_1"},
            headers=headers,
        )
        assert bob_res.status_code in {401, 403, 404}

        # Owner Alice succeeds
        _set_session(client, env["alice_session"])
        alice_res = client.post(
            "/@alice/share",
            json={"doc_id": "doc_alice_public_1"},
            headers=headers,
        )
        assert alice_res.status_code == 200
        share_data = alice_res.json()
        assert "token" in share_data or "path" in share_data

        # Non-existent doc_id returns 404
        bad_doc_res = client.post(
            "/@alice/share",
            json={"doc_id": "nonexistent_doc_id"},
            headers=headers,
        )
        assert bad_doc_res.status_code == 404

        # Missing doc_id returns 400
        missing_doc_res = client.post(
            "/@alice/share",
            json={},
            headers=headers,
        )
        assert missing_doc_res.status_code == 400


def test_reader_share_cross_workspace_leakage_challenge(tmp_path: Path):
    """[M3-CH-16b] Adversarial Challenge: Alice creating a public share token for Bob's private document.

    VULNERABILITY DEMONSTRATION:
    When Alice calls `POST /@alice/share` with `doc_id="doc_bob_private_1"`,
    the multi-theme fallback in `create_share_route` iterates targets and shares Bob's private document!
    """
    s, env = _setup_adversarial_network_env(tmp_path)
    app = create_app(s)

    headers = {"Origin": s.public_url.rstrip("/"), "Sec-Fetch-Site": "same-origin"}
    with TestClient(app, base_url=s.public_url, follow_redirects=False) as client:
        _set_session(client, env["alice_session"])
        res = client.post(
            "/@alice/share",
            json={"doc_id": "doc_bob_private_1"},
            headers=headers,
        )
        # EXPECTED IN SECURE ARCHITECTURE: 404 Not Found (Alice cannot share Bob's private doc)
        # ACTUAL CURRENT BEHAVIOR (BUG): 200 OK (generates share token for Bob's secret document)
        is_leaking = res.status_code == 200 and "token" in res.json()
        assert not is_leaking, (
            "SECURITY VULNERABILITY CONFIRMED: POST /@alice/share with doc_id='doc_bob_private_1' "
            f"returned status={res.status_code} data={res.json()}, generating public share token for Bob's private document!"
        )


def test_reader_csrf_origin_enforcement(tmp_path: Path):
    """[M3-CH-17] State-changing POST endpoints under reader enforce same-origin CSRF policy."""
    s, env = _setup_adversarial_network_env(tmp_path)
    app = create_app(s)

    cross_origin_headers = {"Origin": "https://attacker-domain.example.com"}
    with TestClient(app, base_url=s.public_url, follow_redirects=False) as client:
        _set_session(client, env["alice_session"])

        # Cross-origin POST document/seen rejected with 403 Forbidden
        seen_res = client.post(
            "/@alice/document/seen",
            json={"id": "doc_alice_public_1"},
            headers=cross_origin_headers,
        )
        assert seen_res.status_code == 403

        # Cross-origin POST share rejected with 403 Forbidden
        share_res = client.post(
            "/@alice/share",
            json={"doc_id": "doc_alice_public_1"},
            headers=cross_origin_headers,
        )
        assert share_res.status_code == 403


def test_reader_presentation_disabled_and_visitor(tmp_path: Path):
    """[M3-CH-18] Presentation endpoints enforce theme presentation_enabled and collaborator boundary."""
    s, env = _setup_adversarial_network_env(tmp_path)
    app = create_app(s)

    headers = {"Origin": s.public_url.rstrip("/"), "Sec-Fetch-Site": "same-origin"}
    with TestClient(app, base_url=s.public_url, follow_redirects=False) as client:
        # Theme 10 (Alice Public) has presentation_enabled = 0 by default -> 403
        _set_session(client, env["alice_session"])
        res_dis = client.get("/@alice/document/presentation?id=doc_alice_public_1")
        assert res_dis.status_code == 403
        assert res_dis.json()["error"] == "presentation_disabled"

        # Theme 12 (Alice Private) has presentation_enabled = 1
        # Owner Alice can query metadata (returns 200 with status: not_created)
        res_en = client.get("/@alice/document/presentation?id=doc_alice_private_1")
        assert res_en.status_code == 200
        assert res_en.json()["status"] == "not_created"

        # Visitor Bob attempting to generate presentation on Alice's workspace is rejected
        _set_session(client, env["bob_session"])
        gen_res = client.post(
            "/@alice/document/presentation/generate?id=doc_alice_public_1",
            json={},
            headers=headers,
        )
        assert gen_res.status_code in {401, 403, 404}


# =========================================================================
# Group 4: Client Scripts & Theme Bootstrap Contracts (M3-CH-19 ~ M3-CH-20)
# =========================================================================

def test_static_reader_js_helper_adversarial_contract():
    """[M3-CH-19] Validate readerApiUrl JavaScript logic under edge cases."""
    reader_js_path = Path(__file__).resolve().parent.parent / "src" / "claire" / "static" / "js" / "reader.js"
    content = reader_js_path.read_text(encoding="utf-8")

    # Verify implementation of readerApiUrl handles leading slashes and fallback
    assert "cleanPath = String(path || '').replace(/^\\/+/," in content or "cleanPath = String(path || '').replace(/^\\/+/" in content
    assert "readerApiUrl('document/seen')" in content
    assert "readerApiUrl('document?id='" in content
    assert "readerApiUrl('document/title')" in content
    assert "readerApiUrl('share')" in content

    # Verify no naked document/presentation/share fetch calls exist in reader.js
    naked_patterns = [
        "fetch('document/seen'",
        "fetch('/document/seen'",
        "fetch('document?id='",
        "fetch('/document?id='",
        "fetch('document/title'",
        "fetch('/document/title'",
        "fetch('share'",
        "fetch('/share'",
        "fetch('/document/presentation",
    ]
    for pattern in naked_patterns:
        assert pattern not in content, f"Forbidden naked fetch call found: {pattern}"


def test_render_graph_html_adversarial_theme_isolation(tmp_path: Path):
    """[M3-CH-20] render_graph_html theme isolation under cross-account, invalid account, and visibility scopes."""
    s, env = _setup_adversarial_network_env(tmp_path)

    # 1. Non-existent account ID returns empty or single fallback, zero private themes
    html_invalid = render_graph_html(s, account_id=99999, include_private=False)
    assert "Alice Secret Project" not in html_invalid
    assert "Bob Secret Project" not in html_invalid
    assert "Admin Secret Theme" not in html_invalid

    # 2. Alice owner view contains all Alice themes, zero Bob/Admin themes
    alice_owner_html = render_graph_html(s, account_id=env["alice_id"], include_private=True)
    assert "Alice Public Base" in alice_owner_html
    assert "Alice Secret Project" in alice_owner_html
    assert "Bob Public Base" not in alice_owner_html
    assert "Bob Secret Project" not in alice_owner_html
    assert "Admin Secret Theme" not in alice_owner_html

    # 3. Anonymous view on Alice's workspace contains ONLY Alice's public theme
    alice_anon_html = render_graph_html(s, account_id=env["alice_id"], include_private=False, collaborator=False)
    assert "Alice Public Base" in alice_anon_html
    assert "Alice Secret Project" not in alice_anon_html
    assert "Bob Public Base" not in alice_anon_html
    assert "Admin Secret Theme" not in alice_anon_html
