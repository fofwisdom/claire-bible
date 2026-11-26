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
from tests.typing_helpers import need


def _extract_docdata(html_text: str) -> dict:
    match = re.search(r'<script id="docdata" type="application/json">(.*?)</script>', html_text, re.S)
    assert match is not None, "missing #docdata script"
    return json.loads(match.group(1))


def _extract_json_ld(html_text: str) -> dict:
    match = re.search(r'<script type="application/ld\+json">(.*?)</script>', html_text, re.S)
    assert match is not None, "missing JSON-LD script"
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
        "opinions_url": f"/silvercord/{tok_doc1}/opinions",
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
        "remote_renditions": [],
        # A visitor without a session gets the sign-in address of the +1 control (phase E9b).
        "plus_one": {"url": None, "sign_in": f"/login?next=%2Fsilvercord%2F{tok_doc1}"},
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
            "opinions_url": f"/silvercord/{tok_doc1}/opinions",
            "object_id": None,
            "entity_count": 2,
            "link_only": True,
            "renditions": [],
            "rendition": None,
            "add_rendition_url": None,
            "missing_renditions": [],
            "rendition_account": "",
            "same_source_renditions": [],
            "remote_renditions": [],
            # The document has no rendition object: no +1. Only a visitor without a session gets the sign-in address.
            "plus_one": {"url": None, "sign_in": f"/login?next=%2Fsilvercord%2F{tok_doc1}" if caller is anon else None},
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
    from claire.store import federation as federation_store

    s, env = _setup_network_env(tmp_path)
    conn = dbm.connect_existing(env["db_file"])
    try:
        alice_session = accounts_store.create_account_session(conn, account_id=env["alice_id"], scope="owner")
        conn.execute("UPDATE documents SET url='https://arxiv.org/abs/2101.00001v2?utm_source=feed' WHERE id='doc1'")
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
    assert '<link rel="alternate" type="application/activity+json"' not in page.text
    assert f'<link rel="canonical" href="{origin}/silvercord/{token}"/>' in page.text
    assert f'<meta property="og:url" content="{origin}/silvercord/{token}"/>' in page.text
    page_ld = _extract_json_ld(page.text)
    assert page_ld["mainEntityOfPage"]["@id"] == f"{origin}/silvercord/{token}"
    assert page_ld["isBasedOn"] == {"@type": "CreativeWork", "url": "https://arxiv.org/abs/2101.00001v2?utm_source=feed"}
    assert page_ld["exampleOfWork"] == {"@type": "CreativeWork", "url": "https://arxiv.org/abs/2101.00001"}
    assert page_ld["version"] == "standard/standard-v1"
    owner_sc = _extract_docdata(alice("GET", f"/silvercord/{token}").text)["silvercord"]
    assert owner_sc["add_rendition_url"].startswith("/api/v1/kbs/") and owner_sc["add_rendition_url"].endswith("/documents/doc1/renditions")

    concise = anon("GET", f"/silvercord/{token}/renditions/concise")
    assert concise.status_code == 200
    data = _extract_docdata(concise.text)
    assert data["silvercord"]["rendition"] == "concise" and "간결 본문" in data["detail_html"] and "기본 본문" not in data["detail_html"]
    # The 의견 tab of a rendition page asks for the opinions of the rendition object.
    assert data["silvercord"]["opinions_url"] == f"/silvercord/{token}/renditions/concise/opinions"
    assert f'<link rel="cite-as" href="{origin}/silvercord/{token}/renditions/concise"/>' in concise.text
    assert f'<link rel="canonical" href="{origin}/silvercord/{token}/renditions/concise"/>' in concise.text
    assert f'<meta property="og:url" content="{origin}/silvercord/{token}/renditions/concise"/>' in concise.text
    assert '<link rel="alternate" type="application/activity+json"' not in concise.text
    concise_ld = _extract_json_ld(concise.text)
    assert concise_ld["mainEntityOfPage"]["@id"] == f"{origin}/silvercord/{token}/renditions/concise"
    assert concise_ld["exampleOfWork"] == {"@type": "CreativeWork", "url": "https://arxiv.org/abs/2101.00001"}
    assert concise_ld["version"] == "concise/concise-v1"
    # The 대표 정리본 has the address of the document; a template outside the set or one that the document does not have is not a page.
    standard = anon("GET", f"/silvercord/{token}/renditions/standard")
    assert (standard.status_code, standard.headers["location"]) == (308, f"/silvercord/{token}")
    assert anon("GET", f"/silvercord/{token}/renditions/academic").status_code == 404
    assert anon("GET", f"/silvercord/{token}/renditions/default").status_code == 404
    # An unpublished rendition returns 406 for ActivityPub requests.
    test_client.cookies.clear()
    unpub_ap = test_client.get(f"/silvercord/{token}/renditions/concise", headers={"Accept": "application/activity+json"})
    assert unpub_ap.status_code == 406 and unpub_ap.headers["vary"] == "Accept"

    # A published rendition redirects ActivityPub requests with 303 to its own rendition object ID and emits <link rel="alternate">.
    conn = dbm.connect_existing(env["db_file"])
    try:
        salt = accounts_store.AccountStore(conn).id_salt()
        doc_obj, _task, _made = federation_store.publish_document(conn, account_id=env["alice_id"], kb_id=10, document_id="doc1", published_by_account_id=env["alice_id"], salt=salt, now=1800000200.0)
        rend_obj = next(r for r in federation_store.rendition_objects_of(conn, doc_obj.public_id) if r.template == "concise")
        conn.commit()
    finally:
        conn.close()
    test_client.cookies.clear()
    pub_ap_doc = test_client.get(f"/silvercord/{token}", headers={"Accept": "application/activity+json"})
    assert (pub_ap_doc.status_code, pub_ap_doc.headers["location"], pub_ap_doc.headers["vary"]) == (303, f"/ap/objects/{doc_obj.public_id}", "Accept")
    pub_ap_concise = test_client.get(f"/silvercord/{token}/renditions/concise", headers={"Accept": "application/activity+json"})
    assert (pub_ap_concise.status_code, pub_ap_concise.headers["location"], pub_ap_concise.headers["vary"]) == (303, f"/ap/objects/{rend_obj.public_id}", "Accept")
    pub_page = anon("GET", f"/silvercord/{token}")
    assert f'<link rel="alternate" type="application/activity+json" href="{origin}/ap/objects/{doc_obj.public_id}"/>' in pub_page.text
    pub_concise = anon("GET", f"/silvercord/{token}/renditions/concise")
    assert f'<link rel="alternate" type="application/activity+json" href="{origin}/ap/objects/{rend_obj.public_id}"/>' in pub_concise.text
    assert f"/ap/objects/{doc_obj.public_id}" not in pub_concise.text

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
        alice_session = accounts_store.create_account_session(conn, account_id=env["alice_id"], scope="owner")
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
    origin = s.public_url.rstrip("/")
    anon = _make_caller(test_client, origin)
    alice = _make_caller(test_client, origin, alice_session)
    assert alice("POST", "/api/renditions/plus-one", json={"token": bob_token, "rendition": "academic", "on": True}).status_code == 200
    sc = _extract_docdata(anon("GET", f"/silvercord/{token}").text)["silvercord"]
    assert [(r["template"], r["url"], r["primary"], r["plus"]) for r in sc["same_source_renditions"]] == [("standard", f"/silvercord/{bob_token}", True, {"score": 0}), ("academic", f"/silvercord/{bob_token}/renditions/academic", False, {"score": 1})]
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


def test_the_page_lists_the_renditions_of_other_servers_and_the_frame_route_gives_a_sandbox_page(tmp_path: Path) -> None:
    """Remote renditions on the Silvercord page (DOCUMENT_RENDITIONS_AND_PUBLICATION_DESIGN.md Sections 5.8 rule 4 and 5.10 rule 4, phase E5c): the page data has the copies that the cache can show, and the frame route gives the body as a page without script, with the access rules of the document."""
    import hashlib
    import time as _time

    from claire.ingest.source_key import source_key
    from claire.store import document_renditions as renditions_store

    s, env = _setup_network_env(tmp_path)
    key = source_key("https://arxiv.org/abs/2101.00001")
    iri = "https://b.example/ap/objects/9002"
    body = "= 간결\n\n본문."
    digest = hashlib.sha256(body.encode()).hexdigest()
    conn = dbm.connect_existing(env["db_file"])
    try:
        now = _time.time()
        conn.execute("UPDATE documents SET source_key=?, source_id_version=1, detail='= 기본\n\n본문.' WHERE id='doc1'", (key,))
        renditions_store.record_primary(conn, "doc1", 10, lineage={}, now=now)
        conn.execute(
            "INSERT INTO remote_renditions (iri, domain, source_key, template, template_version, rendition_of, account_iri, account_handle, account_name, name, url, updated, body_iri, body_hash, cached_body_hash, body_html, body_source, fetched_at, expires_at) "
            "VALUES (?, 'b.example', ?, 'concise', 'concise-v1', NULL, 'https://b.example/ap/actors/7001', '@bob@b.example', 'Bob', 'Same paper', 'https://b.example/silvercord/tok/renditions/concise', '2027-01-15T08:05:20Z', ?, ?, ?, '<h2>핵심</h2><p>본문.</p><script>x</script>', ?, ?, ?)",
            (iri, key, f"{iri}/body", digest, digest, body, now, now + 3600),
        )
        token = permalinks.ensure_document(conn, "doc1", 10)
        conn.commit()
    finally:
        conn.close()
    test_client = TestClient(create_app(s), base_url=s.public_url, follow_redirects=False)
    anon = _make_caller(test_client, s.public_url.rstrip("/"))
    sc = _extract_docdata(anon("GET", f"/silvercord/{token}").text)["silvercord"]
    remote_key = hashlib.sha256(iri.encode()).hexdigest()[:16]
    assert sc["remote_renditions"] == [{"key": remote_key, "template": "concise", "version": "concise-v1", "updated": "2027-01-15T08:05:20Z", "account": "@bob@b.example", "domain": "b.example", "origin_url": "https://b.example/silvercord/tok/renditions/concise", "frame_url": f"/silvercord/{token}/renditions/remote/{remote_key}", "estimated_height": 360, "opinions_url": f"/silvercord/{token}/renditions/remote/{remote_key}/opinions", "plus": {"score": 0, "mine": False, "own": False}}]
    frame = anon("GET", sc["remote_renditions"][0]["frame_url"])
    assert frame.status_code == 200
    csp = frame.headers["content-security-policy"]
    assert "sandbox allow-popups allow-popups-to-escape-sandbox" in csp and "default-src 'none'" in csp and "frame-ancestors 'self'" in csp
    assert frame.headers["x-frame-options"] == "SAMEORIGIN"
    assert "<h2>핵심</h2>" in frame.text and "<script" not in frame.text
    # The opinions of the remote rendition come from its origin (Section 5.11 C2): the request asks for a fetch that starts from the object (own false), and the JSON has no control of this server and no likes or boosts.
    from claire.federation import opinions as opinions_mod
    from claire.store import federation_opinions as opinions_store

    first = anon("GET", sc["remote_renditions"][0]["opinions_url"]).json()
    assert (first["object"], first["origin_url"], first["counts"], first["actions"], first["moderation"], first["refresh"]["status"]) == (iri, "https://b.example/silvercord/tok/renditions/concise", {"replies": 0, "quotes": 0, "likes": None, "shares": None}, None, None, "running")
    client = client_db.connect_client_existing(client_db.client_db_path(env["db_file"]))
    try:
        assert need(opinions_store.get_fetch(client, iri)).own is False
        reply = {"object_iri": "https://c.example/notes/1", "url": None, "type": "Note", "author": {"iri": "https://c.example/users/dora", "name": "Dora", "handle": "@dora@c.example"}, "name": None, "summary": None, "sensitive": False, "content_html": "<p>On the concise one</p>", "language": "en", "published_at": 1800000000.0, "updated_at": None, "attachments": [], "context": None, "in_reply_to": iri, "quote": None}
        assert opinions_store.store_objects(client, iri, [reply], quoting=set(), delivered=set(), now=1800000001.0) == 1
        client.commit()
    finally:
        client.close()
    listed = anon("GET", sc["remote_renditions"][0]["opinions_url"]).json()["opinions"]
    reply_key = opinions_store.opinion_key("https://c.example/notes/1")
    assert [(o["iri"], o["parent"], o["frame_url"]) for o in listed] == [("https://c.example/notes/1", iri, f"/silvercord/{token}/renditions/remote/{remote_key}/opinions/{reply_key}")]
    # App-origin fallback frame route for remote rendition opinion works and hides hidden opinions.
    op_frame = anon("GET", listed[0]["frame_url"])
    assert op_frame.status_code == 200 and "On the concise one" in op_frame.text
    assert "frame-ancestors 'self'" in op_frame.headers["content-security-policy"]
    assert op_frame.headers["x-frame-options"] == "SAMEORIGIN" and op_frame.headers["x-robots-tag"] == "noindex, nofollow"
    # Signed-in owner actor gets two-way actions (including remote_object=True) and can like + reply.
    conn = dbm.connect_existing(env["db_file"])
    try:
        bob_session = accounts_store.create_account_session(conn, account_id=env["bob_id"], scope="owner")
        conn.commit()
    finally:
        conn.close()
    bob = _make_caller(test_client, s.public_url.rstrip("/"), bob_session)
    bob_op = bob("GET", sc["remote_renditions"][0]["opinions_url"]).json()
    assert bob_op["actions"] == {"item_url": "/api/federation/opinions/item", "notes_url": "/api/federation/notes", "post": True, "liked": False, "states": {}, "remote_object": True}
    item_res = bob("POST", bob_op["actions"]["item_url"], json={"object": iri, "opinion": iri})
    assert item_res.status_code == 200
    assert bob("POST", f"/api/federation/timeline/{item_res.json()['item_id']}/favourite").json()["favourited"] is True
    assert bob("GET", sc["remote_renditions"][0]["opinions_url"]).json()["actions"]["liked"] is True
    assert bob("POST", bob_op["actions"]["notes_url"], json={"content": "Bob on remote rendition", "in_reply_to": item_res.json()["item_id"]}).status_code == 201
    local_replies = [o for o in anon("GET", sc["remote_renditions"][0]["opinions_url"]).json()["opinions"] if o["local"]]
    assert [(o["parent"], o["text"]) for o in local_replies] == [(iri, [[{"text": "Bob on remote rendition"}]])]
    # A key that the cache does not have, and a document that the caller cannot read: the same 404.
    assert anon("GET", f"/silvercord/{token}/renditions/remote/{'0' * 16}").status_code == 404
    assert anon("GET", f"/silvercord/{token}/renditions/remote/{'0' * 16}/opinions").status_code == 404
    assert anon("GET", f"/silvercord/{token}/renditions/remote/{remote_key}/opinions/{'0' * 16}").status_code == 404
    assert anon("GET", f"/silvercord/{token}/renditions/remote/not-a-key").status_code == 404
    conn = dbm.connect_existing(env["db_file"])
    try:
        conn.execute("UPDATE documents SET hidden=1 WHERE id='doc1'")
        conn.commit()
    finally:
        conn.close()
    assert anon("GET", sc["remote_renditions"][0]["frame_url"]).status_code == 404
    assert anon("GET", listed[0]["frame_url"]).status_code == 404
    # A copy that leaves the cache loses its opinions at the next run.
    conn = dbm.connect_existing(env["db_file"])
    client = client_db.connect_client_existing(client_db.client_db_path(env["db_file"]))
    try:
        assert opinions_mod.purge_gone_objects(conn, client, origin=s.public_url) == 0
        conn.execute("DELETE FROM remote_renditions WHERE iri=?", (iri,))
        conn.commit()
        assert opinions_mod.purge_gone_objects(conn, client, origin=s.public_url) == 1
        client.commit()
    finally:
        client.close()
        conn.close()


def test_the_opinions_route_gives_the_tree_the_counts_and_the_refresh_state(tmp_path: Path) -> None:
    """``GET /silvercord/{token}/opinions`` (DOCUMENT_RENDITIONS_AND_PUBLICATION_DESIGN.md Section 5.11 B and D, phase E7a): the opinions as text and links, the counts, a fetch for an object older than five minutes, and no indexing."""
    import time as _time

    from claire.store import document_renditions as renditions_store
    from claire.store import federation as federation_store
    from claire.store import federation_opinions as opinions_store

    s, env = _setup_network_env(tmp_path)
    conn = dbm.connect_existing(env["db_file"])
    try:
        conn.execute("UPDATE documents SET detail='= 기본\n\n본문.' WHERE id IN ('doc1', 'doc2')")
        renditions_store.record_primary(conn, "doc1", 10, lineage={}, now=1.0)
        obj, _task, _made = federation_store.publish_document(conn, account_id=env["alice_id"], kb_id=10, document_id="doc1", published_by_account_id=env["alice_id"], salt=accounts_store.AccountStore(conn).id_salt(), now=_time.time() - 3600)
        token = permalinks.ensure_document(conn, "doc1", 10)
        token2 = permalinks.ensure_document(conn, "doc2", 10)
        conn.commit()
    finally:
        conn.close()
    origin = s.public_url.rstrip("/")
    object_iri = f"{origin}/ap/objects/{obj.public_id}"
    client = client_db.connect_client_db(client_db.client_db_path(env["db_file"]))
    try:
        reply = {"object_iri": "https://remote.example/users/carol/notes/9", "url": "https://remote.example/@carol/9", "type": "Note", "author": {"iri": "https://remote.example/users/carol", "name": "Carol", "handle": "@carol@remote.example"}, "name": None, "summary": "spoiler", "sensitive": True, "content_html": "<p>Good <a href=\"https://example.org/a\">read</a></p><img src=\"https://x/i.png\">", "language": "en", "published_at": 1800000000.0, "updated_at": None, "attachments": [{"url": "https://remote.example/media/1.png", "media_type": "image/png", "name": "A chart", "width": None, "height": None, "blurhash": None}, {"url": "javascript:alert(1)", "media_type": None, "name": None, "width": None, "height": None, "blurhash": None}], "context": None, "in_reply_to": object_iri, "quote": None}
        assert opinions_store.store_objects(client, object_iri, [reply], quoting=set(), delivered=set(), now=1800000001.0) == 1
        client.commit()
    finally:
        client.close()
    test_client = TestClient(create_app(s), base_url=s.public_url, follow_redirects=False)
    anon = _make_caller(test_client, origin)
    resp = anon("GET", f"/silvercord/{token}/opinions")
    assert resp.status_code == 200
    assert resp.headers["x-robots-tag"] == "noindex, nofollow" and resp.headers["cache-control"] == "private, no-store"
    view = resp.json()
    assert (view["published"], view["object"]) == (True, object_iri)
    assert view["counts"] == {"replies": 1, "quotes": 0, "likes": 0, "shares": 0}
    assert view["moderation"] is None
    assert view["opinions"] == [{"iri": reply["object_iri"], "parent": object_iri, "kind": "reply", "local": False, "hideable": True, "hidden": False, "author": reply["author"], "url": "https://remote.example/@carol/9", "summary": "spoiler", "sensitive": True, "language": "en", "published": 1800000000.0, "text": [[{"text": "Good "}, {"text": "read", "href": "https://example.org/a"}]], "attachments": 1, "frame_url": f"/silvercord/{token}/opinions/{opinions_store.opinion_key(reply['object_iri'])}"}]
    # The object is older than five minutes and has no fetch: the request starts one, and the page sees it run.
    assert view["refresh"]["status"] == "running" and view["refresh"]["retry"] == 3
    assert anon("GET", f"/silvercord/{token}/opinions").json()["refresh"]["id"] == view["refresh"]["id"]
    # A document that is not published has no opinions; a template without an object is the same.
    assert anon("GET", f"/silvercord/{token2}/opinions").json() == {"published": False, "object": None, "opinions": [], "counts": None, "refresh": None}
    assert anon("GET", f"/silvercord/{token}/renditions/concise/opinions").json()["published"] is False
    assert anon("GET", f"/silvercord/{token}/renditions/default/opinions").status_code == 404

    # Phase E7e (Section 5.11 D2): the frame of one opinion has its formatted body and its attachments as links, no script, no resource, and an opaque origin.
    frame = anon("GET", view["opinions"][0]["frame_url"])
    assert frame.status_code == 200
    csp = frame.headers["content-security-policy"]
    assert "default-src 'none'" in csp and "sandbox allow-popups allow-popups-to-escape-sandbox" in csp and "frame-ancestors 'self'" in csp and "img-src" not in csp
    assert frame.headers["x-frame-options"] == "SAMEORIGIN" and frame.headers["x-robots-tag"] == "noindex, nofollow"
    assert "Good" in frame.text and 'href="https://example.org/a"' in frame.text and 'href="https://remote.example/media/1.png"' in frame.text and "A chart" in frame.text
    assert "<img" not in frame.text and "javascript:" not in frame.text
    assert anon("GET", f"/silvercord/{token}/opinions/{'0' * 16}").status_code == 404
    assert anon("GET", f"/silvercord/{token}/opinions/XYZ").status_code == 404

    # Phase E7c (Section 5.11 D5): the author of the object and a server admin hide an opinion from the page and from the replies collection, and show it again. Another account and a visitor cannot.
    conn = dbm.connect_existing(env["db_file"])
    try:
        sessions = {name: accounts_store.create_account_session(conn, account_id=account_id, scope="owner") for name, account_id in (("alice", env["alice_id"]), ("bob", env["bob_id"]), ("admin", 1))}
        conn.commit()
    finally:
        conn.close()
    alice, bob, admin = (_make_caller(test_client, origin, sessions[name]) for name in ("alice", "bob", "admin"))
    # The view of the published document in /syndication asks for the same fetch (Section 5.11 B1); another account gets 404.
    asked = alice("POST", f"/api/federation/objects/{obj.public_id}/opinions")
    assert (asked.status_code, asked.json()["refresh"]["id"]) == (200, view["refresh"]["id"])
    assert bob("POST", f"/api/federation/objects/{obj.public_id}/opinions").status_code == 404
    hide = {"object": object_iri, "opinion": reply["object_iri"], "hidden": True}
    assert alice("GET", f"/silvercord/{token}/opinions").json()["moderation"] == {"url": "/api/federation/opinions/hide"}
    assert bob("GET", f"/silvercord/{token}/opinions").json()["moderation"] is None
    assert bob("POST", "/api/federation/opinions/hide", json=hide).status_code == 404
    assert anon("POST", "/api/federation/opinions/hide", json=hide).status_code in (401, 403, 404)
    assert alice("POST", "/api/federation/opinions/hide", json={**hide, "opinion": "https://remote.example/other"}).status_code == 404
    assert alice("POST", "/api/federation/opinions/hide", json={**hide, "extra": 1}).status_code == 400
    resp = alice("POST", "/api/federation/opinions/hide", json=hide)
    assert (resp.status_code, resp.json()) == (200, {"hidden": True})
    # A hidden opinion has no frame.
    assert anon("GET", view["opinions"][0]["frame_url"]).status_code == 404
    shown = anon("GET", f"/silvercord/{token}/opinions").json()
    assert (shown["opinions"], shown["counts"]["replies"]) == ([], 0)
    assert [(o["iri"], o["hidden"]) for o in admin("GET", f"/silvercord/{token}/opinions").json()["opinions"]] == [(reply["object_iri"], True)]
    assert admin("POST", "/api/federation/opinions/hide", json={**hide, "hidden": False}).json() == {"hidden": False}
    assert [o["iri"] for o in anon("GET", f"/silvercord/{token}/opinions").json()["opinions"]] == [reply["object_iri"]]

    # Phase E7d (Sections 5.6 rule 4 and 5.11 D4 and D9): the actions of a signed-in actor. The author has no like of an own post; a visitor has no actions. Since RD30 the like of the post is the favourite of the item of the object (a Like that the sidecar delivers, as to another server).
    assert anon("GET", f"/silvercord/{token}/opinions").json()["actions"] is None
    assert alice("GET", f"/silvercord/{token}/opinions").json()["actions"]["post"] is False
    actions = bob("GET", f"/silvercord/{token}/opinions").json()["actions"]
    assert actions == {"item_url": "/api/federation/opinions/item", "notes_url": "/api/federation/notes", "post": True, "liked": False, "states": {}}
    post_item = bob("POST", actions["item_url"], json={"object": object_iri, "opinion": object_iri}).json()
    assert bob("POST", f"/api/federation/timeline/{post_item['item_id']}/favourite").json()["favourited"] is True
    assert bob("GET", f"/silvercord/{token}/opinions").json()["actions"]["liked"] is True
    # The favourite makes a like task for the sidecar, with the object and its author.
    client = client_db.connect_client_existing(client_db.client_db_path(env["db_file"]))
    try:
        assert [tuple(r) for r in client.execute("SELECT kind, target_iri FROM federation_client_outbox WHERE account_id=? AND kind='like'", (env["bob_id"],))] == [("like", object_iri)]
    finally:
        client.close()
    # An opinion becomes an item of the timeline of bob (not in the home stream); the favourite of the federation client works on it.
    item = bob("POST", actions["item_url"], json={"object": object_iri, "opinion": reply["object_iri"]})
    assert item.status_code == 200 and item.json()["favourited"] is False
    assert bob("POST", actions["item_url"], json={"object": object_iri, "opinion": "https://remote.example/none"}).status_code == 404
    assert bob("POST", f"/api/federation/timeline/{item.json()['item_id']}/favourite").json()["favourited"] is True
    assert bob("POST", actions["item_url"], json={"object": object_iri, "opinion": reply["object_iri"]}).json() == {"item_id": item.json()["item_id"], "favourited": True, "boosted": False}
    assert bob("GET", f"/silvercord/{token}/opinions").json()["actions"]["states"] == {reply["object_iri"]: {"favourited": True, "boosted": False}}
    # A reply to the opinion (an item) and a reply to the object of the page (in_reply_to_object, which mentions the author of the object, so that the delivery reaches her); the tab shows both as replies of this server.
    assert bob("POST", actions["notes_url"], json={"content": "Bob answers Carol", "in_reply_to": item.json()["item_id"]}).status_code == 201
    on_paper = bob("POST", actions["notes_url"], json={"content": "Bob on the paper", "in_reply_to_object": object_iri})
    assert on_paper.status_code == 201
    assert any(m.endswith(f"/ap/actors/{_public_of(env, 'alice')}") or m.startswith("@alice@") for m in on_paper.json()["note"]["mentions"])
    assert bob("POST", actions["notes_url"], json={"content": "x", "in_reply_to_object": "https://other.example/ap/objects/1"}).json()["error"] == "object_not_found"
    local = [(o["parent"], o["text"]) for o in anon("GET", f"/silvercord/{token}/opinions").json()["opinions"] if o["local"]]
    assert local == [(reply["object_iri"], [[{"text": "Bob answers Carol"}]]), (object_iri, [[{"text": "Bob on the paper"}]])]


def _public_of(env: dict, name: str) -> int:
    conn = dbm.connect_existing(env["db_file"])
    try:
        return int(conn.execute("SELECT public_id FROM accounts WHERE id=?", (env[f"{name}_id"],)).fetchone()[0])
    finally:
        conn.close()


def test_silvercord_usercontent_origin_frame_urls_and_estimated_height(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When usercontent_fqdn is configured, remote renditions and opinions emit usercontent frame_urls and estimated_height."""
    import hashlib
    import time as _time

    from claire.store import document_renditions as renditions_store
    from claire.store import federation as federation_store
    from claire.store import federation_opinions as opinions_store
    from claire.ingest.source_key import source_key

    monkeypatch.setenv("CLAIRE_SECRET_KEY", "0123456789abcdef0123456789abcdef" * 2)
    s, env = _setup_network_env(tmp_path)
    app_host = s.public_url.split("://", 1)[1].rstrip("/")
    app_ip = app_host.split(":", 1)[0]
    uc_host = f"{app_ip}:8767"
    media_host = f"{app_ip}:8768"
    s.usercontent_fqdn = uc_host
    s.media_fqdn = media_host

    key = source_key("https://arxiv.org/abs/2101.00001")
    remote_iri = "https://b.example/ap/objects/9010"
    long_body = "".join(f"<h2>Section {i}</h2><p>{'word ' * 120}</p>" for i in range(6)) + '<img src="https://b.example/img.png" alt="fig">'
    digest = hashlib.sha256(b"long").hexdigest()

    conn = dbm.connect_existing(env["db_file"])
    try:
        now = _time.time()
        conn.execute("UPDATE documents SET source_key=?, source_id_version=1, detail='= 기본\n\n본문.' WHERE id='doc1'", (key,))
        renditions_store.record_primary(conn, "doc1", 10, lineage={}, now=now - 3600)
        obj, _task, _made = federation_store.publish_document(conn, account_id=env["alice_id"], kb_id=10, document_id="doc1", published_by_account_id=env["alice_id"], salt=accounts_store.AccountStore(conn).id_salt(), now=now - 3600)
        conn.execute(
            "INSERT INTO remote_renditions (iri, domain, source_key, template, template_version, rendition_of, account_iri, account_handle, account_name, name, url, updated, body_iri, body_hash, cached_body_hash, body_html, body_source, fetched_at, expires_at) "
            "VALUES (?, 'b.example', ?, 'academic', 'academic-v1', 'https://b.example/ap/objects/9000', 'https://b.example/ap/actors/7001', '@bob@b.example', 'Bob', 'Same paper', 'https://b.example/silvercord/tok/renditions/academic', '2027-01-15T08:05:20Z', ?, ?, ?, ?, 'long', ?, ?)",
            (remote_iri, key, f"{remote_iri}/body", digest, digest, long_body, now, now + 3600),
        )
        token = permalinks.ensure_document(conn, "doc1", 10)
        conn.commit()
    finally:
        conn.close()

    own_iri = f"{s.public_url.rstrip('/')}/ap/objects/{obj.public_id}"
    client = client_db.connect_client_db(client_db.client_db_path(env["db_file"]))
    try:
        opinions_store.store_objects(client, own_iri, [{"object_iri": "https://remote.example/notes/own-1", "url": "https://remote.example/@a/1", "type": "Note", "author": {"iri": "https://remote.example/users/a", "name": "A", "handle": "@a@remote.example"}, "name": None, "summary": None, "sensitive": False, "content_html": "<p>Own opinion</p>", "language": "en", "published_at": now - 10, "updated_at": None, "attachments": [], "context": None, "in_reply_to": own_iri, "quote": None}], quoting=set(), delivered=set(), now=now)
        opinions_store.store_objects(client, remote_iri, [{"object_iri": "https://remote.example/notes/rem-1", "url": "https://remote.example/@b/1", "type": "Note", "author": {"iri": "https://remote.example/users/b", "name": "B", "handle": "@b@remote.example"}, "name": None, "summary": None, "sensitive": False, "content_html": "<p>Remote opinion</p>", "language": "en", "published_at": now - 10, "updated_at": None, "attachments": [], "context": None, "in_reply_to": remote_iri, "quote": None}], quoting=set(), delivered=set(), now=now)
        client.commit()
    finally:
        client.close()

    app = create_app(s)
    with TestClient(app, base_url=s.public_url, follow_redirects=False) as tc:
        sc = _extract_docdata(tc.get(f"/silvercord/{token}").text)["silvercord"]
        rr = sc["remote_renditions"][0]
        assert rr["estimated_height"] == 900
        assert rr["frame_url"].startswith(f"http://{uc_host}/render/remote-rendition/{rr['key']}?t=")

        own_opinions = tc.get(f"/silvercord/{token}/opinions").json()["opinions"]
        assert own_opinions[0]["frame_url"].startswith(f"http://{uc_host}/render/opinion/")

        rem_opinions = tc.get(rr["opinions_url"]).json()
        assert rem_opinions["opinions"][0]["frame_url"].startswith(f"http://{uc_host}/render/opinion/")

        # Load both frame URLs against the usercontent host.
        uc_client = TestClient(app, base_url=f"http://{uc_host}", follow_redirects=False)
        rr_path = rr["frame_url"].split(uc_host, 1)[1]
        rr_resp = uc_client.get(rr_path)
        assert rr_resp.status_code == 200 and f"http://{media_host}/media/remote/" in rr_resp.text
        assert f"frame-ancestors http://{app_host}" in rr_resp.headers["content-security-policy"]

        op_path = rem_opinions["opinions"][0]["frame_url"].split(uc_host, 1)[1]
        op_resp = uc_client.get(op_path)
        assert op_resp.status_code == 200 and "Remote opinion" in op_resp.text
        assert f"frame-ancestors http://{app_host}" in op_resp.headers["content-security-policy"]

        # When rendition_of is set on remote_renditions, signed-in owner gets post=False.
        conn = dbm.connect_existing(env["db_file"])
        try:
            alice_session = accounts_store.create_account_session(conn, account_id=env["alice_id"], scope="owner")
            conn.commit()
        finally:
            conn.close()
        alice_rem = tc.get(rr["opinions_url"], headers={"Cookie": f"claire_session={alice_session}"}).json()
        assert alice_rem["actions"]["post"] is False and alice_rem["actions"]["remote_object"] is True
