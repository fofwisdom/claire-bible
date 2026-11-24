"""Curation and fork (SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md Sections 9.7 and 9.8; unit CT-7).

Completion criteria: a fork of a shared knowledge base has the same visible documents and graph without LLM calls; a fork of a personal knowledge base gets 403 ``kb_fork_shared_only``; curation from a public personal knowledge base works and from a private one gives 404.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from tests.test_contributions import EXTRACTION, _contribute, _graph, _personal_document, no_llm, world  # noqa: F401 (fixtures)

from claire.core import ids
from claire.store import db as dbm
from claire.store import kb_collab
from claire.store import themes as themes_store

ANN, BEN, CHO = 2, 3, 4
OTHER = {"summary": "Mars is a planet.", "key_claims": [], "entities": [{"name": "Mars", "type": "Concept", "aliases": [], "observations": ["A planet."]}], "relations": []}


def _number(world, account_id: int) -> str:  # noqa: F811
    return ids.format_public_id(world.manager.personal_kb_for(account_id).number)


@pytest.fixture
def theme(world):  # noqa: F811
    """The theme ``Places`` holds ``Cities`` (of ann), ``Towns`` (of cho), and the public personal knowledge base of ben."""
    towns = world.client.post("/kb", json={"label": "Towns", "knowledge_type": "real", "seed_entities": ["Seoul"]}, headers=world.cho).json()["knowledge_base"]
    members = (world.kb["id"], towns["id"], world.manager.personal_kb_for(BEN).id)
    conn = dbm.connect(world.settings.db_file)
    try:
        row = themes_store.create_theme(conn, label="Places", knowledge_type="real")
        for kb_id in members:
            themes_store.add_theme_member(conn, theme_id=row["id"], kb_id=kb_id, source="admin", pinned=True)
        conn.commit()
    finally:
        conn.close()
    return SimpleNamespace(id=row["id"], towns=towns, towns_base=f"/kb/{towns['number']}")


def _contribution_rows(world, kb_id: int) -> list[dict]:  # noqa: F811
    conn = dbm.connect_existing(world.settings.db_file, readonly=True)
    try:
        return kb_collab.list_contributions(conn, kb_id)
    finally:
        conn.close()


def test_e2e_curation_from_a_public_personal_knowledge_base_and_from_a_shared_one(world, theme, no_llm) -> None:  # noqa: F811
    client = world.client
    document = _personal_document(world.settings, world.manager, BEN, "Seoul and Busan are cities of Korea. " * 3, url="https://example.org/cities", extraction=EXTRACTION)
    ben_kb = world.manager.personal_kb_for(BEN).id
    # ann is the moderator of Cities: she curates the document from the public personal knowledge base of ben.
    made = client.post(f"{world.base}/curations", json={"source": _number(world, BEN), "documents": [document, "doc_missing"]}, headers=world.ann)
    assert made.status_code == 201, made.text
    first, second = made.json()["curations"]
    assert (first["contribution"]["state"], first["contribution"]["origin"], first["contribution"]["approved"]) == ("visible", "curation", True)
    assert first["contribution"]["contributor"]["display_name"] == "Ben" and second == {"document": "doc_missing", "error": "document_not_found"}
    assert no_llm == [] and _graph(world, world.kb["id"])[0].keys() == {"Korea", "Seoul", "Busan"}
    row = _contribution_rows(world, world.kb["id"])[0]
    # The credit is of the owner of the source; the curator is ann; the root is the document itself.
    assert (row["contributor_account_id"], row["curator_account_id"], row["approved_by_account_id"], row["source_kb_id"], row["root_kb_id"], row["root_document_id"]) == (BEN, ANN, ANN, ben_kb, ben_kb, document)
    conn = dbm.connect_existing(world.settings.db_file, readonly=True)
    try:
        assert [(r["action"], r["actor_account_id"]) for r in kb_collab.list_moderation_log(conn, world.kb["id"]) if r["action"] == "curate"] == [("curate", ANN)]
    finally:
        conn.close()
    # The same document again is a duplicate.
    again = client.post(f"{world.base}/curations", json={"source": _number(world, BEN), "documents": [document]}, headers=world.ann)
    assert again.status_code == 200 and again.json()["curations"][0]["error"] == "contribution_duplicate"

    # cho curates the copy from the shared knowledge base Cities into Towns: the credit and the root come from the contribution in Cities.
    copy_id = row["document_id"]
    into_towns = client.post(f"{theme.towns_base}/curations", json={"source": world.kb["number"], "documents": [copy_id]}, headers=world.cho)
    assert into_towns.status_code == 201, into_towns.text
    town_row = _contribution_rows(world, theme.towns["id"])[0]
    assert (town_row["origin"], town_row["contributor_account_id"], town_row["curator_account_id"], town_row["source_kb_id"], town_row["source_document_id"]) == ("curation", BEN, CHO, world.kb["id"], copy_id)
    assert (town_row["root_kb_id"], town_row["root_document_id"]) == (ben_kb, document)
    assert _graph(world, theme.towns["id"])[0].keys() == {"Korea", "Seoul", "Busan"} and no_llm == []


def test_curation_checks(world, theme) -> None:  # noqa: F811
    client = world.client
    document = _personal_document(world.settings, world.manager, BEN, "Seoul and Busan are cities of Korea. " * 3, extraction=EXTRACTION)
    body = {"source": _number(world, BEN), "documents": [document]}
    # A member is not a reviewer. A scope ``write`` never curates. An anonymous request gets 404.
    assert client.post(f"{world.base}/curations", json=body, headers=world.ben).status_code == 403
    assert client.post(f"{world.base}/curations", json=body, headers=world.bearer("ann", "write")).status_code == 403
    assert client.post(f"{world.base}/curations", json=body).status_code in (401, 404)
    assert client.post(f"{world.base}/curations", json={"source": _number(world, BEN), "documents": []}, headers=world.ann).status_code == 400
    assert client.post(f"{world.base}/curations", json={"source": "1", "documents": [document]}, headers=world.ann).json() == {"error": "source_not_found"}
    assert client.post(f"{world.base}/curations", json={"source": world.kb["number"], "documents": [document]}, headers=world.ann).json() == {"error": "source_invalid"}
    # A knowledge base that shares no theme with Cities is not a source.
    assert client.post(f"{world.base}/curations", json={"source": _number(world, CHO), "documents": [document]}, headers=world.ann).json() == {"error": "source_not_in_theme"}
    # A hidden document of the source is not there.
    conn = dbm.connect(world.settings.db_file)
    try:
        conn.execute("UPDATE documents SET hidden=1 WHERE id=?", (document,))
        conn.commit()
    finally:
        conn.close()
    hidden = client.post(f"{world.base}/curations", json=body, headers=world.ann)
    assert (hidden.status_code, hidden.json()["curations"]) == (200, [{"document": document, "error": "document_not_found"}])
    # A private personal knowledge base gives 404, the same as no knowledge base.
    world.manager.update_kb(world.manager.personal_kb_for(BEN).id, is_public=False)
    private = client.post(f"{world.base}/curations", json=body, headers=world.ann)
    assert (private.status_code, private.json()) == (404, {"error": "source_not_found"})
    assert _contribution_rows(world, world.kb["id"]) == []


def test_e2e_fork_has_the_visible_documents_and_the_graph_without_an_llm_call(world, theme, no_llm) -> None:  # noqa: F811
    client, kb_id = world.client, world.kb["id"]
    visible = _personal_document(world.settings, world.manager, BEN, "Seoul and Busan are cities of Korea. " * 3, url="https://example.org/cities", extraction=EXTRACTION)
    hidden = _personal_document(world.settings, world.manager, BEN, "Mars is a planet of the solar system. " * 3, extraction=OTHER)
    first = _contribute(world, world.ben, visible).json()["contribution"]
    second = _contribute(world, world.ben, hidden).json()["contribution"]
    assert client.patch(f"{world.base}/contributions/{second['number']}", json={"action": "hide"}, headers=world.ann).status_code == 200
    assert client.patch(f"{world.base}/", json={"seed_entities": ["Korea", "Seoul"]}, headers=world.ann).status_code == 200
    source_entities, source_relations = _graph(world, kb_id)
    assert set(source_entities) == {"Korea", "Seoul", "Busan", "Mars"}

    conn = dbm.connect_existing(world.settings.db_file, readonly=True)
    try:
        llm_free = conn.execute("SELECT COUNT(*) FROM extractions").fetchone()[0]
    finally:
        conn.close()
    # cho is not a member of Cities: each account forks.
    made = client.post(f"{world.base}/forks", headers=world.cho)
    assert made.status_code == 201, made.text
    body = made.json()
    fork = body["knowledge_base"]
    assert (fork["label"], fork["knowledge_type"], list(fork["seed_entities"]), body["forked_from"]) == ("Cities (fork)", "real", ["Korea", "Seoul"], world.kb["number"])
    assert (body["documents"], body["entities"], body["relations"]) == (1, 3, 2) and no_llm == []

    # The fork has the visible document and its graph; the hidden document and its entity are not there.
    entities, relations = _graph(world, fork["id"])
    assert set(entities) == {"Korea", "Seoul", "Busan"} and relations == [("Busan", "part_of", "Korea"), ("Seoul", "part_of", "Korea")] == [r for r in source_relations]
    conn = dbm.connect_existing(world.settings.db_file, readonly=True)
    try:
        copies = [tuple(r) for r in conn.execute("SELECT id, hidden, canonical_url FROM documents WHERE kb_id=?", (fork["id"],))]
        assert len(copies) == 1 and copies[0][1:] == (0, "https://example.org/cities") and copies[0][0] != first["document"]
        assert all(sources == [copies[0][0]] for sources in entities.values())
        # One extraction for the copy and no other new one: the fork made no extraction. The embeddings and the FTS rows are copies.
        assert conn.execute("SELECT COUNT(*) FROM extractions").fetchone()[0] == llm_free + 1
        assert conn.execute("SELECT COUNT(*) FROM embeddings WHERE kb_id=?", (fork["id"],)).fetchone()[0] == 3
        source_vectors = sorted(bytes(r[0]) for r in conn.execute("SELECT e.vector FROM embeddings e JOIN entities n ON n.id = e.owner_id WHERE e.kb_id=? AND n.name != 'Mars'", (kb_id,)))
        assert sorted(bytes(r[0]) for r in conn.execute("SELECT vector FROM embeddings WHERE kb_id=?", (fork["id"],))) == source_vectors
        assert conn.execute("SELECT COUNT(*) FROM entities_fts WHERE entity_id IN (SELECT id FROM entities WHERE kb_id=?)", (fork["id"],)).fetchone()[0] == 3
        row = conn.execute("SELECT embed_provider, embed_model, forked_from_kb_id, forked_at IS NOT NULL, created_by_account_id FROM knowledge_bases WHERE id=?", (fork["id"],)).fetchone()
        assert tuple(row) == ("mock", "mock-sha256", kb_id, 1, CHO)
        # The founder is the moderator; the fork is in the themes of its source.
        assert kb_collab.get_membership(conn, fork["id"], CHO)["role"] == "moderator"
        assert theme.id in themes_store.themes_of_kb(conn, fork["id"])
        # The contribution: origin ``fork``, the credit and the root of the source contribution, the approval at the fork time.
        rows = kb_collab.list_contributions(conn, fork["id"])
        assert [(r["origin"], r["state"], r["contributor_account_id"], r["curator_account_id"], r["source_kb_id"], r["source_document_id"], r["root_document_id"], r["approved_at"] is not None) for r in rows] == [
            ("fork", "visible", BEN, CHO, kb_id, first["document"], visible, True)
        ]
    finally:
        conn.close()
    # A reader sees the document of the fork, and the search of the fork finds its entities.
    fork_base = f"/kb/{fork['number']}"
    assert client.get(f"{fork_base}/document?id={copies[0][0]}").status_code == 200
    assert [c["origin"] for c in client.get(f"{fork_base}/contributions").json()["contributions"]] == ["fork"]
    # The source did not change.
    assert _graph(world, kb_id) == (source_entities, source_relations)


def test_fork_of_a_personal_knowledge_base_is_refused(world) -> None:  # noqa: F811
    client = world.client
    # A public personal knowledge base that the requester reads: 403 with the code. Also for its owner.
    for headers in (world.cho, world.ben):
        refused = client.post("/@ben/forks", headers=headers)
        assert (refused.status_code, refused.json()) == (403, {"error": "kb_fork_shared_only"})
    # A private one: 404 for each other account.
    world.manager.update_kb(world.manager.personal_kb_for(BEN).id, is_public=False)
    assert client.post("/@ben/forks", headers=world.cho).status_code == 404
    # A scope ``write`` never forks; an anonymous request gets 404.
    assert client.post(f"{world.base}/forks", headers=world.bearer("cho", "write")).status_code == 403
    assert client.post(f"{world.base}/forks").status_code in (401, 404)
    assert [kb.label for kb in world.manager.list_shared_kbs()] == ["Cities"]
