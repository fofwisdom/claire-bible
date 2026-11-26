"""Silvercord document rendering Phase S1 tests (docs/design/SILVERCORD_RENDERING_DESIGN.md)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from claire.api.security import WebRuntimeConfig, resolve_optional_session_principal
from claire.api.server import create_app
from claire.store import accounts as accounts_store
from claire.store import client_db
from claire.store import db as dbm
from claire.store import permalinks
from tests.test_public_directory import _setup_network_env


def _extract_docdata(html_text: str) -> dict:
    match = re.search(r'<script id="docdata" type="application/json">(.*?)</script>', html_text, re.S)
    assert match is not None, "missing #docdata script"
    return json.loads(match.group(1))


def _make_caller(test_client: TestClient, origin: str, session_token: str | None = None):
    def _call(method: str, path: str, **kwargs):
        headers = {"Origin": origin} if method not in ("GET", "HEAD") else {}
        if session_token is not None:
            headers["Cookie"] = f"claire_session={session_token}"
        test_client.cookies.clear()
        response = test_client.request(method, path, headers=headers, **kwargs)
        test_client.cookies.clear()
        return response

    return _call


@pytest.mark.asyncio
async def test_resolve_optional_session_principal_cookie_tossing_and_validation(tmp_path: Path) -> None:
    s, env = _setup_network_env(tmp_path)
    conn = dbm.connect_existing(env["db_file"])
    try:
        alice_token = accounts_store.create_account_session(conn, account_id=env["alice_id"], scope="owner")
        bob_token = accounts_store.create_account_session(conn, account_id=env["bob_id"], scope="readonly")
        conn.execute("UPDATE accounts SET entity_language='ko' WHERE id=?", (env["bob_id"],))
        conn.commit()
    finally:
        conn.close()

    cfg = WebRuntimeConfig.from_settings(s)

    def _scope(cookie_headers: list[str]) -> dict:
        return {"type": "http", "headers": [(b"cookie", value.encode("latin-1")) for value in cookie_headers]}

    assert await resolve_optional_session_principal(cfg, _scope([])) is None
    assert await resolve_optional_session_principal(cfg, _scope(["claire_session=invalid-token"])) is None

    bob = await resolve_optional_session_principal(cfg, _scope([f"claire_session={bob_token}"]))
    assert bob is not None
    assert bob.account_id == env["bob_id"]
    assert bob.channel == "cookie"
    assert bob.scope == "readonly"
    assert bob.entity_language == "ko"

    # S11 cookie-tossing protection: __Host-claire_session wins over injected claire_session, and duplicates are rejected.
    tossed = await resolve_optional_session_principal(cfg, _scope([f"claire_session={bob_token}; __Host-claire_session={alice_token}"]))
    assert tossed is not None
    assert tossed.account_id == env["alice_id"]

    dup_host = await resolve_optional_session_principal(cfg, _scope([f"__Host-claire_session={alice_token}; __Host-claire_session={bob_token}"]))
    assert dup_host is None

    dup_legacy = await resolve_optional_session_principal(cfg, _scope([f"claire_session={alice_token}; claire_session={bob_token}"]))
    assert dup_legacy is None


def test_silvercord_html_headers_and_docdata_across_callers(tmp_path: Path) -> None:
    s, env = _setup_network_env(tmp_path)
    conn = dbm.connect_existing(env["db_file"])
    try:
        alice_session = accounts_store.create_account_session(conn, account_id=env["alice_id"], scope="owner")
        bob_session = accounts_store.create_account_session(conn, account_id=env["bob_id"], scope="owner")
        conn.execute("INSERT INTO entities (id, kb_id, type, name, norm_name, sources) VALUES ('ent_alpha', 10, 'Tool', 'Tool A', 'tool a', '[\"doc1\"]')")
        conn.execute("INSERT INTO entities (id, kb_id, type, name, norm_name, sources) VALUES ('ent_beta', 10, 'Concept', 'Concept B', 'concept b', '[\"doc1\"]')")
        conn.execute("INSERT INTO documents (id, kb_id, url, title, raw_text, hidden, extraction_status) VALUES ('doc-pending', 10, 'http://example.com/p', 'Pending Doc', 'Text', 0, 'pending')")
        conn.execute("INSERT INTO federation_objects (public_id, account_id, kb_id, document_id, published_by_account_id, published_at, updated_at) VALUES (50123, ?, 10, 'doc1', ?, 1000.0, 1000.0)", (env["alice_id"], env["alice_id"]))
        tok_doc1 = permalinks.ensure_document(conn, "doc1", 10)
        tok_hidden = permalinks.ensure_document(conn, "doc3", 10)
        tok_pending = permalinks.ensure_document(conn, "doc-pending", 10)
        conn.commit()
    finally:
        conn.close()

    test_client = TestClient(create_app(s), base_url=s.public_url, follow_redirects=False)
    origin = s.public_url.rstrip("/")
    anon = _make_caller(test_client, origin)
    alice = _make_caller(test_client, origin, alice_session)
    bob = _make_caller(test_client, origin, bob_session)

    # Public KB: anonymous caller gets unified Silvercord view without private Cache-Control or X-Robots-Tag.
    r_anon = anon("GET", f"/silvercord/{tok_doc1}")
    assert r_anon.status_code == 200
    assert 'class="shared-page"' in r_anon.text
    assert r_anon.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert r_anon.headers["vary"] == "Accept, Cookie"
    assert "cache-control" not in r_anon.headers
    assert "x-robots-tag" not in r_anon.headers
    docdata_anon = _extract_docdata(r_anon.text)
    assert docdata_anon["silvercord"] == {
        "token": tok_doc1,
        "kb_label": "Alice Public Theme",
        "kb_public": True,
        "can_open_workspace": True,
        "workspace_url": f"/silvercord/{tok_doc1}/workspace",
        "graph_url": f"/silvercord/{tok_doc1}/graph",
        "object_id": 50123,
        "entity_count": 2,
        "link_only": False,
        # The document has no rendition row (a body written without a body run), so the section "정리본" is not shown.
        "renditions": [],
        "rendition": None,
        "add_rendition_url": None,
        "missing_renditions": [],
        "rendition_account": "Alice In Wonderland",
        "same_source_renditions": [],
    }
    assert f'<link rel="alternate" type="application/activity+json" href="{origin}/ap/objects/50123"/>' in r_anon.text
    # Citation tags (DOCUMENT_RENDITIONS_AND_PUBLICATION_DESIGN.md Section 5.7, phase E2): the preferred address, the account as the author, the time of the publication, and the actor of the account for the byline of a link preview.
    host = origin.split("://", 1)[1]
    for tag in (
        f'<link rel="cite-as" href="{origin}/silvercord/{tok_doc1}"/>',
        '<meta name="citation_title" content="Doc 1"/>',
        '<meta name="citation_author" content="Alice In Wonderland"/>',
        '<meta name="citation_publication_date" content="1970/01/01"/>',
        f'<meta name="citation_public_url" content="{origin}/silvercord/{tok_doc1}"/>',
        '<meta name="citation_language" content="ko"/>',
        f'<meta name="fediverse:creator" content="@alice@{host}"/>',
    ):
        assert tag in r_anon.text, tag
    assert '"isBasedOn": {"@type": "CreativeWork", "url": "http://example.com/1"}' in r_anon.text
    ap_redir = test_client.get(f"/silvercord/{tok_doc1}", headers={"Accept": "application/activity+json"})
    assert ap_redir.status_code == 303 and ap_redir.headers["location"] == "/ap/objects/50123"

    # Public KB: signed-in non-member gets Cache-Control: private, no-store.
    r_bob = bob("GET", f"/silvercord/{tok_doc1}")
    assert r_bob.status_code == 200
    assert r_bob.headers["cache-control"] == "private, no-store"
    assert r_bob.headers["vary"] == "Accept, Cookie"
    assert "x-robots-tag" not in r_bob.headers

    # Public KB: KB member gets unified Silvercord view with workspace link and Cache-Control: private, no-store.
    r_alice = alice("GET", f"/silvercord/{tok_doc1}")
    assert r_alice.status_code == 200
    assert 'class="shared-page"' in r_alice.text
    assert r_alice.headers["cache-control"] == "private, no-store"
    assert "x-robots-tag" not in r_alice.headers
    assert _extract_docdata(r_alice.text)["silvercord"]["can_open_workspace"] is True

    # Pending & hidden documents: 404 for anonymous & non-member, 200 unified Silvercord view for member.
    assert anon("GET", f"/silvercord/{tok_hidden}").status_code == 404
    assert bob("GET", f"/silvercord/{tok_hidden}").status_code == 404
    assert anon("GET", f"/silvercord/{tok_pending}").status_code == 404
    assert bob("GET", f"/silvercord/{tok_pending}").status_code == 404
    assert alice("GET", f"/silvercord/{tok_hidden}").status_code == 200
    assert alice("GET", f"/silvercord/{tok_pending}").status_code == 200

    # Private KB: member gets unified view with noindex + private, no-store + nodes stripped.
    conn = dbm.connect_existing(env["db_file"])
    try:
        conn.execute("UPDATE knowledge_bases SET is_public=0 WHERE id=10")
        conn.commit()
    finally:
        conn.close()

    r_priv_member = alice("GET", f"/silvercord/{tok_doc1}")
    assert r_priv_member.status_code == 200
    assert r_priv_member.headers["cache-control"] == "private, no-store"
    assert r_priv_member.headers["x-robots-tag"] == "noindex, nofollow"
    assert '<meta name="robots" content="noindex, nofollow"/>' in r_priv_member.text
    priv_member_data = _extract_docdata(r_priv_member.text)
    assert "nodes" not in priv_member_data
    assert priv_member_data["silvercord"]["can_open_workspace"] is True
    assert priv_member_data["silvercord"]["kb_public"] is False
    assert priv_member_data["silvercord"]["link_only"] is False

    # Private KB without link_access: non-member delegates to non-readable _workspace_view.
    r_priv_anon = anon("GET", f"/silvercord/{tok_doc1}")
    assert 'class="shared-page"' not in r_priv_anon.text and "Doc 1" not in r_priv_anon.text

    # Private KB with link_access=1: non-member gets link_only view, nodes stripped, zero ent-* in HTML.
    assert alice("POST", "/@alice/share", json={"doc_id": "doc1"}).status_code == 200
    for caller in (anon, bob):
        r_link = caller("GET", f"/silvercord/{tok_doc1}")
        assert r_link.status_code == 200
        assert r_link.headers["cache-control"] == "private, no-store"
        assert r_link.headers["referrer-policy"] == "strict-origin-when-cross-origin"
        assert r_link.headers["vary"] == "Accept, Cookie"
        assert r_link.headers["x-robots-tag"] == "noindex, nofollow"
        assert '<meta name="robots" content="noindex, nofollow"/>' in r_link.text
        assert "ent_alpha" not in r_link.text and "ent_beta" not in r_link.text
        # A page that search engines must not index has the preferred address only: no citation elements and no actor.
        assert f'<link rel="cite-as" href="{origin}/silvercord/{tok_doc1}"/>' in r_link.text
        assert 'name="citation_' not in r_link.text and "fediverse:creator" not in r_link.text
        link_data = _extract_docdata(r_link.text)
        assert "nodes" not in link_data
        assert link_data["silvercord"] == {
            "token": tok_doc1,
            "kb_label": "",
            "kb_public": False,
            "can_open_workspace": False,
            "workspace_url": None,
            "graph_url": f"/silvercord/{tok_doc1}/graph",
            "object_id": None,
            "entity_count": 2,
            "link_only": True,
            "renditions": [],
            "rendition": None,
            "add_rendition_url": None,
            "missing_renditions": [],
            "rendition_account": "",
            "same_source_renditions": [],
        }


def test_silvercord_workspace_route_access_and_redirects(tmp_path: Path) -> None:
    s, env = _setup_network_env(tmp_path)
    client_conn = client_db.connect_client_db(client_db.client_db_path(env["db_file"]))
    try:
        client_db.init_client_db(client_conn)
        client_conn.execute(
            "INSERT INTO federation_notes (public_id, account_id, content_text, content_html, mentions_json, tags_json, published_at, updated_at) VALUES (?, ?, 'Note', '<p>Note</p>', '[]', '[]', 1800000000, 1800000000)",
            (910001, env["alice_id"]),
        )
        client_conn.commit()
    finally:
        client_conn.close()

    conn = dbm.connect_existing(env["db_file"])
    try:
        alice_session = accounts_store.create_account_session(conn, account_id=env["alice_id"], scope="owner")
        conn.execute("INSERT INTO entities (id, kb_id, type, name, norm_name, sources) VALUES ('ent-w', 10, 'Tool', 'W', 'w', '[\"doc1\"]')")
        tok_doc1 = permalinks.ensure_document(conn, "doc1", 10)
        tok_doc2 = permalinks.ensure_document(conn, "doc2", 10)
        tok_hidden = permalinks.ensure_document(conn, "doc3", 10)
        tok_ent = permalinks.ensure_entity(conn, "ent-w")
        permalinks.merge_documents(conn, "doc1", ["doc2"], kb_id=10)
        conn.commit()
    finally:
        conn.close()
    tok_note = permalinks.ensure_note_token(env["db_file"], 910001, env["alice_id"])
    assert tok_note is not None and tok_ent is not None

    test_client = TestClient(create_app(s), base_url=s.public_url, follow_redirects=False)
    origin = s.public_url.rstrip("/")
    anon = _make_caller(test_client, origin)
    alice = _make_caller(test_client, origin, alice_session)

    # Invalid / non-document tokens -> 404.
    assert anon("GET", "/silvercord/invalid/workspace").status_code == 404
    assert anon("GET", "/silvercord/2222222222222222/workspace").status_code == 404
    assert anon("GET", f"/silvercord/{tok_ent}/workspace").status_code == 404
    assert anon("GET", f"/silvercord/{tok_note}/workspace").status_code == 404

    # Alias -> 308 to target /workspace.
    alias_resp = anon("GET", f"/silvercord/{tok_doc2}/workspace")
    assert alias_resp.status_code == 308 and alias_resp.headers["location"] == f"/silvercord/{tok_doc1}/workspace"

    # Public KB readable document -> readable _workspace_view for anonymous and member; HEAD works.
    ws_anon = anon("GET", f"/silvercord/{tok_doc1}/workspace")
    assert ws_anon.status_code == 200 and '<meta name="claire-open-item" content="document:doc1"/>' in ws_anon.text
    assert anon("HEAD", f"/silvercord/{tok_doc1}/workspace").status_code == 200
    ws_alice = alice("GET", f"/silvercord/{tok_doc1}/workspace")
    assert ws_alice.status_code == 200 and '<meta name="claire-open-item" content="document:doc1"/>' in ws_alice.text

    # Hidden document: non-member gets non-readable _workspace_view, member gets readable _workspace_view.
    ws_hidden_anon = anon("GET", f"/silvercord/{tok_hidden}/workspace")
    assert '<meta name="claire-open-item"' not in ws_hidden_anon.text
    ws_hidden_alice = alice("GET", f"/silvercord/{tok_hidden}/workspace")
    assert ws_hidden_alice.status_code == 200 and '<meta name="claire-open-item" content="document:doc3"/>' in ws_hidden_alice.text

    # Private KB with link_access=1: non-member still gets non-readable _workspace_view without doc1 ID; member gets readable.
    conn = dbm.connect_existing(env["db_file"])
    try:
        conn.execute("UPDATE knowledge_bases SET is_public=0 WHERE id=10")
        permalinks.set_link_access(conn, "document", "doc1", True)
        conn.commit()
    finally:
        conn.close()
    ws_priv_anon = anon("GET", f"/silvercord/{tok_doc1}/workspace")
    assert '<meta name="claire-open-item"' not in ws_priv_anon.text and "doc1" not in ws_priv_anon.text
    ws_priv_alice = alice("GET", f"/silvercord/{tok_doc1}/workspace")
    assert ws_priv_alice.status_code == 200 and '<meta name="claire-open-item" content="document:doc1"/>' in ws_priv_alice.text

    # Deleted document -> 410.
    conn = dbm.connect_existing(env["db_file"])
    try:
        conn.execute("DELETE FROM documents WHERE id='doc1'")
        conn.commit()
    finally:
        conn.close()
    assert anon("GET", f"/silvercord/{tok_doc1}/workspace").status_code == 410


def test_silvercord_graph_full_scope_ring_limits_dedup_and_visibility(tmp_path: Path) -> None:
    s, env = _setup_network_env(tmp_path)
    conn = dbm.connect_existing(env["db_file"])
    try:
        alice_session = accounts_store.create_account_session(conn, account_id=env["alice_id"], scope="owner")
        bob_session = accounts_store.create_account_session(conn, account_id=env["bob_id"], scope="owner")
        conn.execute("UPDATE accounts SET entity_language='ko' WHERE id=?", (env["bob_id"],))

        # 6 extra public documents sharing entities with doc1 (to verify top-5 limit) + 1 pending document.
        for idx in range(10, 16):
            conn.execute("INSERT INTO documents (id, kb_id, url, title, raw_text, source_type, hidden) VALUES (?, 10, ?, ?, 'Body', 'web', 0)", (f"doc{idx}", f"http://example.com/{idx}", f"Shared Doc {idx}"))
        conn.execute("INSERT INTO documents (id, kb_id, url, title, raw_text, source_type, hidden, extraction_status) VALUES ('doc-pending', 10, 'http://example.com/pend', 'Pending Doc', 'Body', 'web', 0, 'pending')")

        # 26 Ring 1 entities in doc1 (24 should be rendered, unshown_r1 == 2).
        # ent-r1-00 has highest degree via relations to Ring 2 and also Korean alias for entity_language test.
        for i in range(26):
            eid = f"ent-r1-{i:02d}"
            name = f"Entity R1 {i:02d}"
            aliases = json.dumps(["엔티티 00(ko)"] if i == 0 else [])
            sources = ["doc1"]
            if i == 0:
                sources.extend(["doc2", "doc10", "doc11", "doc12", "doc13", "doc14", "doc15", "doc3", "doc-pending"])
            elif i == 1:
                sources.extend(["doc2", "doc10", "doc11", "doc12", "doc13", "doc14"])
            elif i == 2:
                sources.extend(["doc10", "doc11", "doc12", "doc13"])
            elif i == 3:
                sources.extend(["doc10", "doc11", "doc12"])
            elif i == 4:
                sources.extend(["doc10", "doc11"])
            elif i == 5:
                sources.append("doc10")
            conn.execute(
                "INSERT INTO entities (id, kb_id, type, name, norm_name, aliases, observations, sources) VALUES (?, 10, 'Concept', ?, ?, ?, ?, ?)",
                (eid, name, name.lower(), aliases, json.dumps([f"Obs {i}"]), json.dumps(sources)),
            )

        # 14 Ring 2 entities connected to ent-r1-00 (12 should be rendered, unshown_r2 == 2).
        for j in range(14):
            eid = f"ent-r2-{j:02d}"
            name = f"Neighbor R2 {j:02d}"
            conn.execute(
                "INSERT INTO entities (id, kb_id, type, name, norm_name, observations, sources) VALUES (?, 10, 'Tool', ?, ?, ?, '[\"doc2\"]')",
                (eid, name, name.lower(), json.dumps([f"N-Obs {j}"])),
            )
            conn.execute(
                "INSERT INTO relations (id, kb_id, type, source_id, target_id, provisional) VALUES (?, 10, 'uses', 'ent-r1-00', ?, ?)",
                (f"rel-r2-{j:02d}", eid, 1 if j >= 10 else 0),
            )

        # Hidden-only and pending-only entities connected to ent-r1-00: invisible to non-members!
        conn.execute("INSERT INTO entities (id, kb_id, type, name, norm_name, sources) VALUES ('ent-hidden-only', 10, 'Concept', 'Hidden Only', 'hidden only', '[\"doc3\"]')")
        conn.execute("INSERT INTO entities (id, kb_id, type, name, norm_name, sources) VALUES ('ent-pending-only', 10, 'Concept', 'Pending Only', 'pending only', '[\"doc-pending\"]')")
        conn.execute("INSERT INTO relations (id, kb_id, type, source_id, target_id, provisional) VALUES ('rel-hid', 10, 'relates', 'ent-r1-00', 'ent-hidden-only', 0)")
        conn.execute("INSERT INTO relations (id, kb_id, type, source_id, target_id, provisional) VALUES ('rel-pend', 10, 'relates', 'ent-r1-00', 'ent-pending-only', 0)")

        # Duplicate pair edge between ent-r1-00 and ent-r1-01 (provisional + confirmed -> deduplicated to confirmed!).
        conn.execute("INSERT INTO relations (id, kb_id, type, source_id, target_id, provisional) VALUES ('rel-dup-prov', 10, 'weak_link', 'ent-r1-00', 'ent-r1-01', 1)")
        conn.execute("INSERT INTO relations (id, kb_id, type, source_id, target_id, provisional) VALUES ('rel-dup-firm', 10, 'strong_link', 'ent-r1-01', 'ent-r1-00', 0)")

        # Give ent-r1-01 and ent-r2-00 extra degree so ent-r2-00 ranks top in Ring 2.
        conn.execute("INSERT INTO relations (id, kb_id, type, source_id, target_id, provisional) VALUES ('rel-boost-r2', 10, 'uses', 'ent-r1-01', 'ent-r2-00', 0)")

        tok_doc1 = permalinks.ensure_document(conn, "doc1", 10)
        tok_doc2 = permalinks.ensure_document(conn, "doc2", 10)
        tok_hidden = permalinks.ensure_document(conn, "doc3", 10)
        tok_pending = permalinks.ensure_document(conn, "doc-pending", 10)
        tok_ent0 = permalinks.ensure_entity(conn, "ent-r1-00")
        permalinks.merge_documents(conn, "doc1", ["doc2"], kb_id=10)
        # Re-insert doc2 alias separately on a non-merged alias for test clarity: wait, doc2 was merged into doc1; let's create a dedicated alias token for doc1.
        conn.execute("UPDATE permalinks SET alias_of=NULL WHERE token=?", (tok_doc2,))
        conn.execute("INSERT INTO permalinks (token, kind, kb_id, target, alias_of, created_at) VALUES ('aaaaaaaaaaaaaaaa', 'document', 10, 'doc1', ?, 1000.0)", (tok_doc1,))
        conn.commit()
    finally:
        conn.close()

    test_client = TestClient(create_app(s), base_url=s.public_url, follow_redirects=False)
    origin = s.public_url.rstrip("/")
    anon = _make_caller(test_client, origin)
    bob = _make_caller(test_client, origin, bob_session)
    alice = _make_caller(test_client, origin, alice_session)

    # Error / redirect cases.
    assert anon("GET", "/silvercord/invalid/graph").status_code == 404
    assert anon("GET", "/silvercord/2222222222222222/graph").status_code == 404
    assert anon("GET", f"/silvercord/{tok_ent0}/graph").status_code == 404
    alias_g = anon("GET", "/silvercord/aaaaaaaaaaaaaaaa/graph")
    assert alias_g.status_code == 308 and alias_g.headers["location"] == f"/silvercord/{tok_doc1}/graph"
    assert anon("GET", f"/silvercord/{tok_hidden}/graph").status_code == 404
    assert anon("GET", f"/silvercord/{tok_pending}/graph").status_code == 404

    # Full graph for anonymous caller on public KB.
    resp = anon("GET", f"/silvercord/{tok_doc1}/graph")
    assert resp.status_code == 200
    assert resp.headers["cache-control"] == "private, no-store"
    assert resp.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert resp.headers["x-robots-tag"] == "noindex, nofollow"
    assert resp.headers["vary"] == "Cookie"
    assert anon("HEAD", f"/silvercord/{tok_doc1}/graph").status_code == 200

    data = resp.json()
    assert data["scope"] == "full"
    assert data["document"] == {"token": tok_doc1, "title": "Doc 1"}
    assert len(data["ring1"]) == 24 and data["unshown_r1"] == 2
    assert len(data["ring2"]) == 12 and data["unshown_r2"] == 2
    assert data["ring1"][0]["id"] == "ent-r1-00"
    assert data["ring1"][0]["entity_path"] == f"/entity/{tok_ent0}"
    assert len(data["ring1"][0]["other_docs"]) == 5
    assert data["ring1"][0]["hidden_other_docs_count"] == 0
    # Hidden & pending doc titles must NOT appear in other_docs or shared_docs or ring2 for non-member.
    assert "Hidden Only" not in resp.text and "Pending Only" not in resp.text and "Pending Doc" not in resp.text and "Doc 3" not in resp.text
    assert data["ring2"][0]["id"] == "ent-r2-00"
    assert "ent-r1-00" in data["ring2"][0]["connected_r1"]

    # Edges deduplicated between rendered Ring 1 + Ring 2, preferring non-provisional.
    pair_01 = [e for e in data["edges"] if {e["from"], e["to"]} == {"ent-r1-00", "ent-r1-01"}]
    assert len(pair_01) == 1 and pair_01[0]["provisional"] is False and pair_01[0]["rel"] == "strong_link"

    # Shared docs capped at 5, sorted by shared_count desc, hidden_shared_docs_count == 0.
    assert len(data["shared_docs"]) == 5
    assert data["hidden_shared_docs_count"] == 0
    assert data["shared_docs"][0]["shared_count"] == 6
    assert data["shared_docs"][0]["path"].startswith("/silvercord/")

    # Signed-in non-member with entity_language='ko' receives localized label.
    resp_bob = bob("GET", f"/silvercord/{tok_doc1}/graph")
    assert resp_bob.status_code == 200
    assert resp_bob.json()["ring1"][0]["label"] == "엔티티 00(ko)"

    # Member CAN query /graph on hidden and pending documents.
    assert alice("GET", f"/silvercord/{tok_hidden}/graph").status_code == 200
    assert alice("GET", f"/silvercord/{tok_pending}/graph").status_code == 200


def test_silvercord_graph_link_only_scope_opaque_ids_and_blocked_docs(tmp_path: Path) -> None:
    s, env = _setup_network_env(tmp_path)
    conn = dbm.connect_existing(env["db_file"])
    try:
        alice_session = accounts_store.create_account_session(conn, account_id=env["alice_id"], scope="owner")
        bob_session = accounts_store.create_account_session(conn, account_id=env["bob_id"], scope="owner")
        conn.execute("UPDATE knowledge_bases SET is_public=0 WHERE id=10")
        conn.execute("INSERT INTO entities (id, kb_id, type, name, norm_name, aliases, observations, sources) VALUES ('ent-secret-1', 10, 'Tool', 'vSAN', 'vsan', '[\"Virtual SAN\"]', '[\"Obs vSAN\"]', '[\"doc1\", \"doc2\", \"doc3\"]')")
        conn.execute("INSERT INTO entities (id, kb_id, type, name, norm_name, sources) VALUES ('ent-secret-2', 10, 'Framework', 'ESXi', 'esxi', '[\"doc1\"]')")
        conn.execute("INSERT INTO entities (id, kb_id, type, name, norm_name, sources) VALUES ('ent-secret-r2', 10, 'Concept', 'OSA', 'osa', '[\"doc2\"]')")
        conn.execute("INSERT INTO relations (id, kb_id, type, source_id, target_id, provisional) VALUES ('rel-12', 10, 'runs_on', 'ent-secret-1', 'ent-secret-2', 0)")
        conn.execute("INSERT INTO relations (id, kb_id, type, source_id, target_id, provisional) VALUES ('rel-1r2', 10, 'succeeds', 'ent-secret-1', 'ent-secret-r2', 1)")
        tok_doc1 = permalinks.ensure_document(conn, "doc1", 10)
        tok_doc2 = permalinks.ensure_document(conn, "doc2", 10)
        tok_ent1 = permalinks.ensure_entity(conn, "ent-secret-1")
        permalinks.ensure_entity(conn, "ent-secret-2")
        permalinks.set_link_access(conn, "entity", "ent-secret-1", True)
        conn.commit()
    finally:
        conn.close()

    test_client = TestClient(create_app(s), base_url=s.public_url, follow_redirects=False)
    origin = s.public_url.rstrip("/")
    anon = _make_caller(test_client, origin)
    bob = _make_caller(test_client, origin, bob_session)
    alice = _make_caller(test_client, origin, alice_session)

    # Without link_access on doc1, non-members get 404.
    assert anon("GET", f"/silvercord/{tok_doc1}/graph").status_code == 404
    assert bob("GET", f"/silvercord/{tok_doc1}/graph").status_code == 404

    # Enable link_access on doc1.
    assert alice("POST", "/@alice/share", json={"doc_id": "doc1"}).status_code == 200

    for caller in (anon, bob):
        resp = caller("GET", f"/silvercord/{tok_doc1}/graph")
        assert resp.status_code == 200
        # Never leak internal entity IDs or other document titles/tokens!
        assert "ent-secret" not in resp.text
        assert "Doc 2" not in resp.text and "Doc 3" not in resp.text and tok_doc2 not in resp.text

        payload = resp.json()
        assert payload["scope"] == "link_only"
        assert payload["document"] == {"token": tok_doc1, "title": "Doc 1"}
        assert payload["shared_docs"] == []
        assert payload["hidden_shared_docs_count"] == 1

        r1_ids = [item["id"] for item in payload["ring1"]]
        r2_ids = [item["id"] for item in payload["ring2"]]
        assert all(re.fullmatch(r"e\d+", eid) for eid in r1_ids + r2_ids)
        assert len(r1_ids) == 2 and len(r2_ids) == 1

        # ent-secret-1 has degree 2 so it is first in ring1; it has entity link_access=True.
        vsan_node = next(item for item in payload["ring1"] if item["label"] == "vSAN")
        esxi_node = next(item for item in payload["ring1"] if item["label"] == "ESXi")
        osa_node = payload["ring2"][0]
        assert vsan_node["entity_path"] == f"/entity/{tok_ent1}"
        assert vsan_node["other_docs"] == [] and vsan_node["hidden_other_docs_count"] == 1
        assert esxi_node["entity_path"] is None
        assert osa_node["entity_path"] is None
        assert osa_node["connected_r1"] == [vsan_node["id"]]
        assert all(re.fullmatch(r"e\d+", edge["from"]) and re.fullmatch(r"e\d+", edge["to"]) for edge in payload["edges"])

    # Member on same private KB gets scope == "full" with real IDs and both visible + hidden other docs.
    member_resp = alice("GET", f"/silvercord/{tok_doc1}/graph")
    assert member_resp.status_code == 200
    assert member_resp.json()["scope"] == "full"
    assert member_resp.json()["ring1"][0]["id"] == "ent-secret-1"
    assert len(member_resp.json()["shared_docs"]) == 2

    # Deleted doc -> 410.
    conn = dbm.connect_existing(env["db_file"])
    try:
        conn.execute("DELETE FROM documents WHERE id='doc1'")
        conn.commit()
    finally:
        conn.close()
    assert anon("GET", f"/silvercord/{tok_doc1}/graph").status_code == 410


def test_silvercord_rendition_sub_paths_and_the_section_data(tmp_path: Path) -> None:
    """The renditions of a document on the Silvercord page (DOCUMENT_RENDITIONS_AND_PUBLICATION_DESIGN.md Sections 5.8 and 5.10, phase E3c): one sub-path for each rendition that is not the 대표 정리본, the same access rules as the document, and the route of 정리본 추가 only for the owner."""
    from claire.store import document_renditions as renditions_store

    s, env = _setup_network_env(tmp_path)
    conn = dbm.connect_existing(env["db_file"])
    try:
        alice_session = accounts_store.create_account_session(conn, account_id=env["alice_id"], scope="owner")
        dbm.set_document_detail(conn, "doc1", "= 기본\n\n기본 본문.", kb_id=10)
        renditions_store.record_primary(conn, "doc1", 10, lineage={"provider": "mock", "model": "m"}, now=1800000000.0)
        renditions_store.store_additional(conn, "doc1", 10, template="concise", detail="= 간결\n\n== 핵심\n\n* 간결 본문.", lineage={"provider": "mock", "model": "m"}, now=1800000100.0)
        token = permalinks.ensure_document(conn, "doc1", 10)
        conn.commit()
    finally:
        conn.close()
    test_client = TestClient(create_app(s), base_url=s.public_url, follow_redirects=False)
    origin = s.public_url.rstrip("/")
    anon = _make_caller(test_client, origin)
    alice = _make_caller(test_client, origin, alice_session)

    page = anon("GET", f"/silvercord/{token}")
    sc = _extract_docdata(page.text)["silvercord"]
    assert [(r["template"], r["primary"], r["url"]) for r in sc["renditions"]] == [("standard", True, f"/silvercord/{token}"), ("concise", False, f"/silvercord/{token}/renditions/concise")]
    assert (sc["rendition"], sc["add_rendition_url"], sc["missing_renditions"]) == ("standard", None, ["academic"])
    owner_sc = _extract_docdata(alice("GET", f"/silvercord/{token}").text)["silvercord"]
    assert owner_sc["add_rendition_url"].startswith("/api/v1/kbs/") and owner_sc["add_rendition_url"].endswith("/documents/doc1/renditions")

    concise = anon("GET", f"/silvercord/{token}/renditions/concise")
    assert concise.status_code == 200
    data = _extract_docdata(concise.text)
    assert data["silvercord"]["rendition"] == "concise" and "간결 본문" in data["detail_html"] and "기본 본문" not in data["detail_html"]
    assert f'<link rel="cite-as" href="{origin}/silvercord/{token}/renditions/concise"/>' in concise.text
    # The 대표 정리본 has the address of the document; a template outside the set or one that the document does not have is not a page.
    standard = anon("GET", f"/silvercord/{token}/renditions/standard")
    assert (standard.status_code, standard.headers["location"]) == (308, f"/silvercord/{token}")
    assert anon("GET", f"/silvercord/{token}/renditions/academic").status_code == 404
    assert anon("GET", f"/silvercord/{token}/renditions/default").status_code == 404
    # A rendition has no ActivityPub object before phase E4.
    test_client.cookies.clear()
    assert test_client.get(f"/silvercord/{token}/renditions/concise", headers={"Accept": "application/activity+json"}).status_code == 406
    # A sub-path takes the access rules of the document: a hidden document is not found for another caller.
    conn = dbm.connect_existing(env["db_file"])
    try:
        conn.execute("UPDATE documents SET hidden=1 WHERE id='doc1'")
        conn.commit()
    finally:
        conn.close()
    assert anon("GET", f"/silvercord/{token}/renditions/concise").status_code == 404


def test_the_picker_has_the_published_renditions_of_other_documents_with_the_same_source(tmp_path: Path) -> None:
    """The picker group "같은 출처 · 이 서버" (DOCUMENT_RENDITIONS_AND_PUBLICATION_DESIGN.md Sections 5.4 rule 1 and 5.10 rule 10, phase E4b): the published renditions of the documents of other accounts of this server with the same source key. An unpublished document is not in the group."""
    import time as _time

    from claire.core import ids as _ids
    from claire.ingest.source_key import source_key
    from claire.store import document_renditions as renditions_store
    from claire.store import federation as federation_store

    s, env = _setup_network_env(tmp_path)
    key = source_key("https://arxiv.org/abs/2101.00001")
    conn = dbm.connect_existing(env["db_file"])
    try:
        now = _time.time()
        conn.execute("INSERT INTO knowledge_bases (id, seq, number, label, account_id, is_personal, is_public, created_at, updated_at) VALUES (12, 12, ?, 'Bob Public', ?, 1, 1, ?, ?)", (_ids.timestamp_id("knowledge_bases", salt=b"x", unix_ms=int(now * 1000) + 99), env["bob_id"], now, now))
        for doc_id, kb_id in (("doc1", 10), ("bob-published", 12), ("bob-unpublished", 12)):
            if doc_id != "doc1":
                conn.execute("INSERT INTO documents (id, kb_id, url, title, raw_text, hidden) VALUES (?, ?, 'https://arxiv.org/abs/2101.00001', 'Same paper', 'Text', 0)", (doc_id, kb_id))
            conn.execute("UPDATE documents SET source_key=?, source_id_version=1, detail='= 기본\n\n본문.' WHERE id=?", (key, doc_id))
            renditions_store.record_primary(conn, doc_id, kb_id, lineage={}, now=1800000000.0)
            permalinks.ensure_document(conn, doc_id, kb_id)
        renditions_store.store_additional(conn, "bob-published", 12, template="academic", detail="= 학술\n\n본문.", lineage={}, now=1800000100.0)
        conn.commit()
        salt = accounts_store.AccountStore(conn).id_salt()
        federation_store.publish_document(conn, account_id=env["bob_id"], kb_id=12, document_id="bob-published", published_by_account_id=env["bob_id"], salt=salt, now=now)
        conn.commit()
        token = permalinks.token_of(conn, "document", "doc1")
        bob_token = permalinks.token_of(conn, "document", "bob-published")
    finally:
        conn.close()
    test_client = TestClient(create_app(s), base_url=s.public_url, follow_redirects=False)
    anon = _make_caller(test_client, s.public_url.rstrip("/"))
    sc = _extract_docdata(anon("GET", f"/silvercord/{token}").text)["silvercord"]
    assert [(r["template"], r["url"], r["primary"]) for r in sc["same_source_renditions"]] == [("standard", f"/silvercord/{bob_token}", True), ("academic", f"/silvercord/{bob_token}/renditions/academic", False)]
    assert all(r["account"] for r in sc["same_source_renditions"])

    # The page asks each known server for its renditions of the source, one time in the lifetime (phase E5b). The sidecar does the read later.
    from claire.federation.knowledge import known_servers

    conn = dbm.connect_existing(env["db_file"])
    try:
        known_servers.note_server(conn, "c.example", source="admin", added_by=1, now=1.0)
        conn.commit()
    finally:
        conn.close()
    for _ in range(2):
        assert anon("GET", f"/silvercord/{token}").status_code == 200
    conn = dbm.connect_existing(env["db_file"], readonly=True)
    try:
        scheme = s.public_url.split("://", 1)[0]
        assert [tuple(r) for r in conn.execute("SELECT kind, url, status FROM kf_remote_reads WHERE kind LIKE 'rendition_%'")] == [("rendition_index", f"{scheme}://c.example/ap/sources/{key}", "pending")]
    finally:
        conn.close()
