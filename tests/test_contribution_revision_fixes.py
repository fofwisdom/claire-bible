"""Data defects of revisions, curation, and fork of contributions (SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md Sections 9.5, 9.7, 9.8).

A new version of a curated copy gets the files of the document that it was copied from; a revision that waits behind a failed first merge becomes the version that a retry merges; the purge of an old copy removes the observations and aliases that only that copy gave, also from an entity that it created; a fork does not copy the observations and aliases of hidden documents.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_contribution_data_fixes import KOREA_MORE, _at_entity, _contribution, _documents, _entities, _new_root_version, _run_jobs
from tests.test_contributions import EXTRACTION, _personal_document, world  # noqa: F401 (fixtures)
from tests.test_curation_and_fork import theme  # noqa: F401 (fixture)

from claire.collaboration import contributions
from claire.ingest.normalize import content_hash
from claire.store import db as dbm
from claire.store import kb_collab

ANN, BEN, CHO = 2, 3, 4


def _korea(observations: list[str], aliases: list[str], *, busan: bool = True) -> dict:
    """An extraction of the page about Korea with other observations and aliases of ``Korea``."""
    entities = [{"name": "Korea", "type": "Concept", "aliases": aliases, "observations": observations}]
    if busan:
        entities.append({"name": "Busan", "type": "Concept", "aliases": [], "observations": ["A port city of Korea."]})
    return {"summary": "Korea.", "key_claims": [], "entities": entities, "relations": [{"source": "Busan", "target": "Korea", "type": "part_of"}] if busan else []}


# --- defect 1: the files of a new version of a curated copy ----------------------------------------------------------


def test_e2e_new_version_of_a_failed_curated_copy_gets_the_files_of_the_root(world, theme) -> None:  # noqa: F811
    data_dir = Path(world.settings.data_dir)
    personal = world.manager.personal_kb_for(BEN).id
    document = _personal_document(world.settings, world.manager, BEN, "Seoul and Busan are cities of Korea. " * 3, url="https://example.org/cities", extraction=EXTRACTION)
    (data_dir / "images").mkdir(parents=True, exist_ok=True)
    (data_dir / "images" / f"{document}_0.png").write_bytes(b"png-bytes")
    conn = dbm.connect(world.settings.db_file)
    try:
        dbm.set_document_images(conn, document, [{"url": "https://example.org/a.png", "local": f"images/{document}_0.png"}], kb_id=personal)
        conn.execute("UPDATE documents SET detail=? WHERE id=?", (f"image::/image?p=images/{document}_0.png[a]", document))
        conn.commit()
        # ann curates the document into Cities; cho curates the copy of Cities into Towns. The root of the two copies is the document of ben.
        cities = contributions.curate(conn, kb_id=world.kb["id"], source=contributions.curation_source(conn, kb_id=world.kb["id"], source_kb_id=personal), document_id=document, curator_account_id=ANN)
        conn.commit()
        _run_jobs(world, conn)
        cities_copy = kb_collab.get_contribution(conn, cities["public_id"])["document_id"]
        towns = contributions.curate(conn, kb_id=theme.towns["id"], source=contributions.curation_source(conn, kb_id=theme.towns["id"], source_kb_id=world.kb["id"]), document_id=cities_copy, curator_account_id=CHO)
        conn.commit()
        # The first merge in Towns fails: the job does not run, and the state is ``failed``.
        conn.execute("UPDATE jobs SET status='done' WHERE status='pending'")
        kb_collab.update_contribution(conn, towns["public_id"], state="failed", decision_reason="merge_failed")
        conn.commit()

        # A new version of the root replaces the failed copy of Towns; its merge job copies the files of the root, not of the copy in Cities.
        text = "Seoul and Busan are big cities of Korea. " * 3
        _new_root_version(world, conn, document, text, EXTRACTION)
        _run_jobs(world, conn)
        row = kb_collab.get_contribution(conn, towns["public_id"])
        new_copy = row["document_id"]
        assert row["state"] == "visible" and new_copy != towns["document_id"]
        assert conn.execute("SELECT content_hash FROM documents WHERE id=?", (new_copy,)).fetchone()[0] == content_hash(text)
        assert (data_dir / "images" / f"{new_copy}_0.png").read_bytes() == b"png-bytes"
        meta = json.loads(conn.execute("SELECT meta FROM documents WHERE id=?", (new_copy,)).fetchone()[0])
        assert [image["local"] for image in meta["images"]] == [f"images/{new_copy}_0.png"]
        assert conn.execute("SELECT detail FROM documents WHERE id=?", (new_copy,)).fetchone()[0] == f"image::/image?p=images/{new_copy}_0.png[a]"
    finally:
        conn.close()


# --- defect 2: a revision that waits behind a first merge that fails -------------------------------------------------


def test_revision_that_waits_behind_a_failed_merge_is_the_version_that_the_retry_merges(world, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    kb_id = world.kb["id"]
    document = _personal_document(world.settings, world.manager, BEN, "The first text of a page about Korea. " * 3, url="https://example.org/page", extraction=EXTRACTION)
    second_text = "The second text of the page about Korea. " * 3
    conn = dbm.connect(world.settings.db_file)
    try:
        merging = _contribution(world, conn, document, run=False)
        _new_root_version(world, conn, document, second_text, KOREA_MORE)
        (revision,) = kb_collab.list_revisions(conn, merging["public_id"])

        # The first merge fails one time. The revision job runs after it in the same sweep.
        failures: list[int] = []

        def _fail_once() -> None:
            if not failures:
                failures.append(1)
                raise RuntimeError("the merge fails")

        _at_entity(monkeypatch, "Korea", _fail_once)
        results = _run_jobs(world, conn)
        assert [r.state for r in results] == ["failed", "failed"] and results[0].error == "merge_failed"

        # The new version replaced the copy of the failed contribution; the old copy is gone; the reason of the failure stays.
        row = kb_collab.get_contribution(conn, merging["public_id"])
        assert (row["state"], row["decision_reason"], row["document_id"]) == ("failed", "merge_failed", revision["document_id"])
        assert kb_collab.get_revision(conn, revision["id"])["state"] == "done"
        assert _documents(conn, kb_id) == [revision["document_id"]]

        # The retry merges the newest version.
        contributions.retry_merge(conn, contribution=merging["public_id"])
        conn.commit()
        _run_jobs(world, conn)
        row = kb_collab.get_contribution(conn, merging["public_id"])
        assert (row["state"], row["document_id"]) == ("visible", revision["document_id"])
        assert conn.execute("SELECT content_hash FROM documents WHERE id=?", (row["document_id"],)).fetchone()[0] == content_hash(second_text)
        assert set(_entities(conn, kb_id)) == {"Korea", "Seoul", "Jeju"}
    finally:
        conn.close()


# --- defect 3: the observations of an old copy after a revision ------------------------------------------------------


def test_revision_removes_the_observations_that_only_the_old_copy_gave(world) -> None:  # noqa: F811
    kb_id = world.kb["id"]
    first = _personal_document(world.settings, world.manager, BEN, "Seoul and Busan are cities of Korea. " * 3, url="https://example.org/cities", extraction=EXTRACTION)
    second = _personal_document(world.settings, world.manager, BEN, "Korea has mountains and the island Jeju. " * 3, extraction=KOREA_MORE)
    conn = dbm.connect(world.settings.db_file)
    try:
        one = _contribution(world, conn, first)
        two = _contribution(world, conn, second)
        # The first copy created Korea, Seoul, and Busan; the second copy merged into Korea and Seoul (its observation of Seoul is the same).
        entities = _entities(conn, kb_id)
        assert entities["Korea"]["observations"] == ["A country in East Asia.", "Korea has many mountains."] and entities["Seoul"]["sources"] == [one["document_id"], two["document_id"]]

        # A new version of the first document: Korea keeps one observation of the old version and gets a new one; Seoul is not in it.
        _new_root_version(world, conn, first, "A new text about Korea and Busan. " * 3, _korea(["A country in East Asia.", "A state in East Asia."], ["ROK"]))
        _run_jobs(world, conn)
        revised = kb_collab.get_contribution(conn, one["public_id"])["document_id"]
        entities = _entities(conn, kb_id)
        # The old copy created Korea. Its alias goes; its observation stays because the new copy gives it too.
        assert (entities["Korea"]["observations"], entities["Korea"]["aliases"], entities["Korea"]["sources"]) == (["A country in East Asia.", "Korea has many mountains.", "A state in East Asia."], ["Hanguk", "ROK"], [two["document_id"], revised])
        # The second copy gives the observation of Seoul too, so it stays.
        assert (entities["Seoul"]["observations"], entities["Seoul"]["sources"]) == (["The capital of Korea."], [two["document_id"]])
        assert entities["Busan"]["sources"] == [revised]

        # One more version: the observation that the copy before kept (it did not add it) goes too, because no copy that stays gives it.
        _new_root_version(world, conn, first, "The third text about Korea. " * 3, _korea(["A nation in East Asia."], [], busan=False))
        _run_jobs(world, conn)
        third = kb_collab.get_contribution(conn, one["public_id"])["document_id"]
        entities = _entities(conn, kb_id)
        assert (entities["Korea"]["observations"], entities["Korea"]["aliases"], entities["Korea"]["sources"]) == (["Korea has many mountains.", "A nation in East Asia."], ["Hanguk"], [two["document_id"], third])
        assert set(entities) == {"Korea", "Seoul", "Jeju"}
    finally:
        conn.close()


# --- defect 4: a fork and the hidden documents of its source ---------------------------------------------------------


def test_e2e_fork_does_not_copy_observations_and_aliases_of_hidden_documents(world) -> None:  # noqa: F811
    kb_id = world.kb["id"]
    secret = {
        "summary": "A secret about Korea.",
        "key_claims": [],
        "entities": [
            {"name": "Korea", "type": "Concept", "aliases": ["Hidden Korea"], "observations": ["A secret plan of Korea."]},
            {"name": "Seoul", "type": "Concept", "aliases": [], "observations": ["The capital of Korea.", "A secret place in Seoul."]},
        ],
        "relations": [],
    }
    visible = _personal_document(world.settings, world.manager, BEN, "Seoul and Busan are cities of Korea. " * 3, url="https://example.org/cities", extraction=EXTRACTION)
    hidden = _personal_document(world.settings, world.manager, BEN, "A secret text about Korea. " * 3, extraction=secret)
    conn = dbm.connect(world.settings.db_file)
    try:
        _contribution(world, conn, visible)
        second = _contribution(world, conn, hidden)
        contributions.set_hidden(conn, kb_id=kb_id, contribution=second["public_id"], account_id=ANN, hidden=True)
        conn.commit()
        before = _entities(conn, kb_id)
        assert "A secret plan of Korea." in before["Korea"]["observations"] and "Hidden Korea" in before["Korea"]["aliases"]
    finally:
        conn.close()

    made = world.client.post(f"{world.base}/forks", headers=world.cho)
    assert made.status_code == 201, made.text
    fork_id = int(made.json()["knowledge_base"]["id"])
    conn = dbm.connect(world.settings.db_file)
    try:
        entities = _entities(conn, fork_id)
        assert set(entities) == {"Korea", "Seoul", "Busan"}
        assert (entities["Korea"]["observations"], entities["Korea"]["aliases"]) == (["A country in East Asia."], ["Republic of Korea"])
        assert (entities["Seoul"]["observations"], entities["Seoul"]["aliases"]) == (["The capital of Korea."], [])
        # The search index of the fork does not have the text of the hidden document.
        bodies = " ".join(str(r[0]) for r in conn.execute("SELECT body FROM entities_fts WHERE entity_id IN (SELECT id FROM entities WHERE kb_id=?)", (fork_id,)))
        assert "secret" not in bodies.lower() and "Hidden" not in bodies and "Republic" in bodies
        # The source did not change.
        assert _entities(conn, kb_id) == before
    finally:
        conn.close()
