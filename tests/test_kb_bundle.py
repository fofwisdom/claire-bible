"""The data bundle of a shared knowledge base (SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md Section 13.2: "moderators and the admin").

The route is ``POST /kb/{number}/graph/bundle`` with the capability ``settings``. The bundle has the format of each graph bundle (``graph_bundle.py``) and, for a shared knowledge base, only its visible content: the documents that are not hidden and have a ``visible`` contribution, the entities and relations of those documents, and the table ``contributions`` with the public number of the account with the credit.
"""

from __future__ import annotations

import io
import json
import sqlite3
import tarfile
from pathlib import Path

from compression import zstd

from tests.test_contributions import EXTRACTION, _contribute, _fresh_token_stores, _personal_document, no_llm, world  # noqa: F401 (fixtures)

from claire.collaboration import contributions
from claire.core import ids
from claire.ops.graph_bundle import GRAPH_BUNDLE_FORMAT_VERSION, list_account_graph_bundles
from claire.store import db as dbm
from claire.store import kb_collab

ANN, BEN, CHO = 2, 3, 4
OTHER = {"summary": "Mars is a planet.", "key_claims": [], "entities": [{"name": "Mars", "type": "Concept", "aliases": [], "observations": ["A planet."]}], "relations": []}
PENDING = {"summary": "Venus is a planet.", "key_claims": [], "entities": [{"name": "Venus", "type": "Concept", "aliases": [], "observations": ["A planet."]}], "relations": []}


def _open_bundle(client, token: str, into: Path) -> tuple[dict, sqlite3.Connection]:
    """Download the bundle with its token and open its manifest and its export database."""
    response = client.get(f"/graph/bundle?token={token}")
    assert response.status_code == 200, response.text
    into.mkdir(parents=True, exist_ok=True)
    with zstd.ZstdFile(io.BytesIO(response.content), "rb") as reader:
        with tarfile.open(fileobj=reader, mode="r|") as tar:
            tar.extractall(path=into, filter="data")
    manifest = json.loads(next(into.rglob("manifest.json")).read_text(encoding="utf-8"))
    conn = sqlite3.connect(str(next(into.rglob("graph_export.db"))))
    conn.row_factory = sqlite3.Row
    return manifest, conn


def _denied(response) -> tuple[int, dict]:
    return response.status_code, response.json()


def _set_role(world, account_id: int, role: str) -> None:  # noqa: F811
    conn = dbm.connect(world.settings.db_file)
    try:
        kb_collab.update_membership(conn, kb_id=world.kb["id"], account_id=account_id, role=role)
        conn.commit()
    finally:
        conn.close()


def test_e2e_moderator_makes_the_bundle_of_the_visible_content(world, no_llm, tmp_path: Path) -> None:  # noqa: F811
    client, kb_id = world.client, world.kb["id"]
    visible = _personal_document(world.settings, world.manager, BEN, "Seoul and Busan are cities of Korea. " * 3, url="https://example.org/cities", extraction=EXTRACTION)
    hidden = _personal_document(world.settings, world.manager, BEN, "Mars is a planet of the solar system. " * 3, extraction=OTHER)
    pending = _personal_document(world.settings, world.manager, BEN, "Venus is a planet of the solar system. " * 3, extraction=PENDING)
    first = _contribute(world, world.ben, visible).json()["contribution"]
    second = _contribute(world, world.ben, hidden).json()["contribution"]
    assert client.patch(f"{world.base}/contributions/{second['number']}", json={"action": "hide"}, headers=world.ann).status_code == 200
    personal_id = world.manager.personal_kb_for(BEN).id
    conn = dbm.connect(world.settings.db_file)
    try:
        copy_id = contributions.copy_document(conn, source_document_id=pending, source_kb_id=personal_id, target_kb_id=kb_id, hidden=True)
        kb_collab.add_contribution(conn, kb_id=kb_id, document_id=copy_id, origin="direct", state="pending", contributor_account_id=BEN, source_kb_id=personal_id, source_document_id=pending, root_kb_id=personal_id, root_document_id=pending)
        conn.commit()
    finally:
        conn.close()

    # Moderators and the admin (capability ``settings``). A member, a collaborator, and a credential with the scope ``write`` do not get the bundle.
    route = f"{world.base}/graph/bundle"
    assert _denied(client.post(route, json={}, headers=world.ben)) == (403, {"error": "kb_capability_required"})
    _set_role(world, BEN, "collaborator")
    assert _denied(client.post(route, json={}, headers=world.ben)) == (403, {"error": "kb_capability_required"})
    assert client.post(route, json={}, headers=world.bearer("ann", "write")).json() == {"error": "scope_insufficient", "scope": "write", "required": "owner"}
    assert client.post(route, json={}).status_code == 404
    assert client.post(route, json={}, headers=world.admin).status_code == 200

    made = client.post(route, json={}, headers=world.ann)
    assert made.status_code == 200, made.text
    info = made.json()
    assert (info["shared"], info["documents_count"], info["entities_count"], info["relations_count"], info["contributions_count"]) == (True, 1, 3, 2, 1)
    # The registry records the moderator that made the bundle: the bundle is in its list of data bundles.
    listed = list_account_graph_bundles(world.settings.data_dir, ANN, public_url="https://kb.example")
    assert [b["bundle_id"] for b in listed] == [info["bundle_id"]]
    assert listed[0]["download_url"] == f"https://kb.example/graph/bundle?token={info['token']}"

    manifest, export = _open_bundle(client, info["token"], tmp_path / "bundle")
    try:
        assert (manifest["bundle_version"], manifest["shared"], manifest["contributions_count"], manifest["kb_id"]) == (GRAPH_BUNDLE_FORMAT_VERSION, True, 1, kb_id)
        documents = [tuple(r) for r in export.execute("SELECT id, url FROM documents")]
        assert documents == [(first["document"], "https://example.org/cities")]
        # The entities and relations of the hidden copy are not in the bundle; the sources keep only the documents of the bundle.
        entities = {r["name"]: json.loads(r["sources"]) for r in export.execute("SELECT name, sources FROM entities")}
        assert entities == {name: [first["document"]] for name in ("Korea", "Seoul", "Busan")}
        assert export.execute("SELECT COUNT(*) FROM relations").fetchone()[0] == 2
        # The contribution has the public number of the account with the credit, not its internal ID.
        rows = [dict(r) for r in export.execute("SELECT * FROM contributions")]
        assert [(r["number"], r["document_id"], r["origin"], r["contributor_account"], r["remote_credit"]) for r in rows] == [
            (first["number"], first["document"], "direct", ids.format_public_id((1 << 57) + BEN), None)
        ]
        # The review evidence has only the decisions of the documents of the bundle, not those of the hidden copy or of another knowledge base.
        source = dbm.connect_existing(world.settings.db_file, readonly=True)
        try:
            assert "resolution_log" in json.loads(source.execute("SELECT meta FROM documents WHERE id=?", (second["document"],)).fetchone()[0] or "{}")
        finally:
            source.close()
        assert {r[0] for r in export.execute("SELECT DISTINCT document_id FROM review_decisions")} == {first["document"]}
    finally:
        export.close()


def test_bundle_of_a_personal_knowledge_base_has_an_empty_contributions_table(world, tmp_path: Path) -> None:  # noqa: F811
    client = world.client
    _personal_document(world.settings, world.manager, BEN, "Seoul and Busan are cities of Korea. " * 3, extraction=EXTRACTION)
    made = client.post("/@ben/graph/bundle", json={}, headers=world.ben)
    assert made.status_code == 200, made.text
    assert (made.json()["shared"], made.json()["contributions_count"], made.json()["documents_count"]) == (False, 0, 1)
    manifest, export = _open_bundle(client, made.json()["token"], tmp_path / "personal")
    try:
        assert (manifest["shared"], export.execute("SELECT COUNT(*) FROM contributions").fetchone()[0]) == (False, 0)
    finally:
        export.close()
    # Another account does not make the bundle of the personal knowledge base of ben.
    assert client.post("/@ben/graph/bundle", json={}, headers=world.cho).status_code in (403, 404)


def test_bundle_list_of_a_moderator_names_the_shared_knowledge_base(world) -> None:  # noqa: F811
    """The list of the bundles of an account gives the label of a shared knowledge base, so that the page never shows an internal knowledge base ID."""
    made = world.client.post(f"{world.base}/graph/bundle", headers=world.ann)
    assert made.status_code in (200, 201), made.text
    listing = world.client.get("/api/me/data-bundles", headers=world.ann).json()
    labels = {t["kb_id"]: t["label"] for t in listing["themes"]}
    assert any(b["kb_id"] == world.kb["id"] for b in listing["bundles"]) and labels[world.kb["id"]] == "Cities"
