"""Data logic of contributions that a review found broken (SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md Sections 9.1, 9.4 to 9.6, 9.8, 9.9, 11.2, 13.2).

A purge of a copy keeps each entity in its knowledge base and runs no heal and no VACUUM; a merge job ends correctly when its contribution changes while it runs; a merge that fails part-way can be undone; a copy has its own image paths; a relation keeps each source document; the newest revision wins; the approval of a purged reviewer stays an approval; the automatic rule reads the personal knowledge base one time for each run.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from tests.test_auto_contribution import KOREA, SPACE, _document
from tests.test_contributions import EXTRACTION, _fresh_token_stores, _personal_document, world  # noqa: F401 (fixtures)

from claire import auto_contribution, contributions, theme_judgement
from claire.extract.decision import ResolutionDecision
from claire.extract.provider import get_provider
from claire.extract.resolver import resolve_or_create as REAL_RESOLVE
from claire.ingest.normalize import content_hash
from claire.ontology.base import Entity
from claire.store import db as dbm
from claire.store import kb_collab
from claire.store import themes as themes_store

ANN, BEN, CHO = 2, 3, 4
# A second document about Korea: its entity ``Korea`` merges into the entity of the first document and adds an alias and an observation.
KOREA_MORE = {
    "summary": "Korea has mountains and the island Jeju.",
    "key_claims": [],
    "entities": [
        {"name": "Korea", "type": "Concept", "aliases": ["Hanguk"], "observations": ["Korea has many mountains."]},
        {"name": "Seoul", "type": "Concept", "aliases": [], "observations": ["The capital of Korea."]},
        {"name": "Jeju", "type": "Concept", "aliases": [], "observations": ["An island of Korea."]},
    ],
    "relations": [{"source": "Seoul", "target": "Korea", "type": "part_of"}, {"source": "Jeju", "target": "Korea", "type": "part_of"}],
}


def _run_jobs(world, conn) -> list[contributions.MergeResult]:  # noqa: F811
    provider = get_provider(world.settings)
    return contributions.run_pending_merge_jobs(conn, lambda _kb: provider, lambda _kb: world.settings)


def _contribution(world, conn, document: str, *, account: int = BEN, approved: bool = False, origin: str = "direct", run: bool = True) -> dict:  # noqa: F811
    """A direct contribution of ``document`` to Cities. With ``run`` its merge job runs."""
    row = contributions.contribute_direct(conn, kb_id=world.kb["id"], source_kb_id=world.manager.personal_kb_for(account).id, source_document_id=document, contributor_account_id=account, approved=approved, origin=origin)
    conn.commit()
    if run:
        _run_jobs(world, conn)
    return kb_collab.get_contribution(conn, row["public_id"])


def _entities(conn, kb_id: int) -> dict[str, dict]:
    return {
        r["name"]: {"id": r["id"], "aliases": json.loads(r["aliases"] or "[]"), "observations": json.loads(r["observations"] or "[]"), "sources": json.loads(r["sources"] or "[]")}
        for r in conn.execute("SELECT id, name, aliases, observations, sources FROM entities WHERE kb_id=?", (kb_id,))
    }


def _relations(conn, kb_id: int) -> dict[tuple[str, str], list[str]]:
    rows = conn.execute("SELECT s.name, t.name, r.sources FROM relations r JOIN entities s ON s.id = r.source_id JOIN entities t ON t.id = r.target_id WHERE r.kb_id=?", (kb_id,))
    return {(r[0], r[1]): json.loads(r[2] or "[]") for r in rows}


def _documents(conn, kb_id: int) -> list[str]:
    return [str(r[0]) for r in conn.execute("SELECT id FROM documents WHERE kb_id=? ORDER BY id", (kb_id,))]


def _at_entity(monkeypatch: pytest.MonkeyPatch, name: str, action) -> None:
    """Run ``action`` in the merge job, immediately before the entity ``name`` is resolved."""

    def _resolve(conn, vstore, **kwargs):
        if kwargs["name"] == name:
            action()
        return REAL_RESOLVE(conn, vstore, **kwargs)

    monkeypatch.setattr(contributions, "resolve_or_create", _resolve)


def _new_root_version(world, conn, document: str, text: str, extraction: dict) -> list[int]:  # noqa: F811
    """The root document gets a new content and a new extraction; the revisions start. The merge jobs do not run."""
    personal = world.manager.personal_kb_for(BEN).id
    conn.execute("UPDATE documents SET raw_text=?, content_hash=? WHERE id=?", (text, content_hash(text), document))
    conn.execute("INSERT INTO extractions (kb_id, document_id, provider, model, prompt_version, raw_response, created_at) VALUES (?, ?, 'mock', 'mock', 'test', ?, ?)", (personal, document, json.dumps(extraction), time.time()))
    conn.commit()
    return contributions.start_revisions(conn, Path(world.settings.data_dir), root_kb_id=personal, root_document_id=document)


# --- findings 1, 2, 16: purge of a copy -------------------------------------------------------------------------------


def test_e2e_purge_of_a_copy_keeps_entities_in_their_knowledge_base_and_runs_no_vacuum(world, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    kb_id, data_dir = world.kb["id"], Path(world.settings.data_dir)
    first = _personal_document(world.settings, world.manager, BEN, "Seoul and Busan are cities of Korea. " * 3, extraction=EXTRACTION)
    second = _personal_document(world.settings, world.manager, BEN, "Korea has mountains and the island Jeju. " * 3, extraction=KOREA_MORE)
    conn = dbm.connect(world.settings.db_file)
    try:
        one = _contribution(world, conn, first)
        conn.execute("UPDATE jobs SET status='done'")
        conn.commit()
        row = contributions.contribute_direct(conn, kb_id=kb_id, source_kb_id=world.manager.personal_kb_for(BEN).id, source_document_id=second, contributor_account_id=BEN, approved=False)
        conn.commit()
        (merged,) = _run_jobs(world, conn)
        two = kb_collab.get_contribution(conn, row["public_id"])
        # The second copy merged into the entities of the first one. A relation that both documents state has both as sources and counts one time (finding 16).
        korea = _entities(conn, kb_id)["Korea"]
        assert "Korea has many mountains." in korea["observations"] and korea["sources"] == [one["document_id"], two["document_id"]]
        assert _relations(conn, kb_id)[("Seoul", "Korea")] == [one["document_id"], two["document_id"]]
        assert (merged.state, merged.relations) == ("visible", 1)  # only Jeju → Korea is new
        outside = conn.execute("SELECT COUNT(*) FROM entities WHERE kb_id != ?", (kb_id,)).fetchone()[0]

        # The withdrawal purges the second copy: no heal of the graph and no VACUUM (finding 2).
        heals: list[int] = []
        real_heal = dbm.heal_graph
        monkeypatch.setattr(dbm, "heal_graph", lambda *args, **kwargs: heals.append(1) or real_heal(*args, **kwargs))
        statements: list[str] = []
        conn.set_trace_callback(statements.append)
        contributions.withdraw(conn, data_dir, kb_id=kb_id, contribution=two["public_id"], account_id=BEN)
        conn.set_trace_callback(None)
        assert heals == [] and not [s for s in statements if s.strip().upper().startswith(("VACUUM", "PRAGMA WAL_CHECKPOINT"))]

        # The entities of K stay in K with the state before the second copy (finding 1); the relation of the first copy stays.
        entities = _entities(conn, kb_id)
        assert set(entities) == {"Korea", "Seoul", "Busan"}
        assert (entities["Korea"]["aliases"], entities["Korea"]["observations"], entities["Korea"]["sources"]) == (["Republic of Korea"], ["A country in East Asia."], [one["document_id"]])
        assert conn.execute("SELECT COUNT(*) FROM entities WHERE kb_id != ?", (kb_id,)).fetchone()[0] == outside
        assert _relations(conn, kb_id) == {("Seoul", "Korea"): [one["document_id"]], ("Busan", "Korea"): [one["document_id"]]}
        assert _documents(conn, kb_id) == [one["document_id"]]

        # Each other caller of the purge keeps the heal and the VACUUM; the two parameters turn them off.
        report = dbm.purge_document_cascade(conn, data_dir=data_dir, target_ids=[second], tombstone=False, kb_id=world.manager.personal_kb_for(BEN).id)
        assert heals == [1] and report["vacuum_executed"] is True and isinstance(report["graph_healed"], dict)
    finally:
        conn.close()


def test_purge_in_a_personal_knowledge_base_keeps_the_merged_entity_in_it(world) -> None:  # noqa: F811
    """A document of a personal knowledge base whose id is not 0 merged into an entity; the purge of the document undoes the merge in that knowledge base."""
    personal, data_dir = world.manager.personal_kb_for(BEN).id, Path(world.settings.data_dir)
    assert personal != 0
    conn = dbm.connect(world.settings.db_file)
    try:
        dbm.upsert_entity(conn, Entity(id="ent_ben_korea", type="Concept", name="Korea", observations=["From A.", "From B.", "From C."], sources=["doc_keep", "doc_b", "doc_c"]), kb_id=personal)
        for document_id, observation in (("doc_keep", ""), ("doc_b", "From B."), ("doc_c", "From C.")):
            decision = ResolutionDecision(entity="Korea", stage="exact_match", decision="MERGE", candidate="Korea", target_entity_id="ent_ben_korea", rollback_payload={"target_entity_id": "ent_ben_korea", "added_aliases": [], "added_observations": [observation], "added_source": document_id, "prev_type": None})
            meta = {"resolution_log": [decision.to_dict()]} if observation else {}
            conn.execute("INSERT INTO documents (id, kb_id, title, raw_text, source_type, content_hash, hidden, meta) VALUES (?, ?, ?, 'text', 'web', ?, 0, ?)", (document_id, personal, document_id, f"hash-{document_id}", json.dumps(meta)))
        conn.commit()

        def _korea() -> tuple:
            row = conn.execute("SELECT kb_id, observations, sources FROM entities WHERE id='ent_ben_korea'").fetchone()
            return int(row["kb_id"]), json.loads(row["observations"]), json.loads(row["sources"])

        # With the knowledge base of the purge, and without it: the entity stays where it is.
        dbm.purge_document_cascade(conn, data_dir=data_dir, target_ids=["doc_b"], tombstone=False, kb_id=personal)
        assert _korea() == (personal, ["From A.", "From C."], ["doc_keep", "doc_c"])
        dbm.purge_document_cascade(conn, data_dir=data_dir, target_ids=["doc_c"], tombstone=False)
        assert _korea() == (personal, ["From A."], ["doc_keep"])
    finally:
        conn.close()


# --- findings 6, 7, 20: the merge job is not one transaction ----------------------------------------------------------


def test_merge_job_ends_correctly_when_the_contribution_changes_while_it_runs(world, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    kb_id, data_dir = world.kb["id"], Path(world.settings.data_dir)
    provider = get_provider(world.settings)
    conn = dbm.connect(world.settings.db_file)

    def _other(action) -> None:
        other = dbm.connect(world.settings.db_file)
        try:
            action(other)
        finally:
            other.close()

    try:
        # A removal while the merge runs: the contribution stays removed with no copy, and the graph of the copy goes.
        removed = _personal_document(world.settings, world.manager, BEN, "A note that a moderator removes while it merges. " * 3, extraction=EXTRACTION)
        row = _contribution(world, conn, removed, run=False)
        (job,) = contributions.pending_merge_jobs(conn)
        _at_entity(monkeypatch, "Busan", lambda: _other(lambda c: contributions.remove(c, data_dir, kb_id=kb_id, contribution=row["public_id"], account_id=ANN, reason="spam")))
        result = contributions.run_merge_job(conn, provider, world.settings, contribution=row["public_id"], job_id=job["id"])
        after = kb_collab.get_contribution(conn, row["public_id"])
        assert (result.state, result.error, after["state"], after["document_id"]) == ("removed", "contribution_changed", "removed", None)
        assert _entities(conn, kb_id) == {} and _relations(conn, kb_id) == {} and _documents(conn, kb_id) == []
        assert conn.execute("SELECT status FROM jobs WHERE id=?", (job["id"],)).fetchone()[0] == "error"

        # A withdrawal while the merge runs: the job ends (no ``running`` job stays) and the entities of the copy go.
        withdrawn = _personal_document(world.settings, world.manager, BEN, "A note that the contributor withdraws while it merges. " * 3, extraction=EXTRACTION)
        row = _contribution(world, conn, withdrawn, run=False)
        (job,) = contributions.pending_merge_jobs(conn)
        _at_entity(monkeypatch, "Busan", lambda: _other(lambda c: contributions.withdraw(c, data_dir, kb_id=kb_id, contribution=row["public_id"], account_id=BEN)))
        result = contributions.run_merge_job(conn, provider, world.settings, contribution=row["public_id"], job_id=job["id"])
        assert (result.state, result.error) == ("removed", "contribution_changed") and kb_collab.get_contribution(conn, row["public_id"]) is None
        assert _entities(conn, kb_id) == {} and _documents(conn, kb_id) == []
        assert conn.execute("SELECT status FROM jobs WHERE id=?", (job["id"],)).fetchone()[0] == "error"

        # The job reports its progress: a job that is at work is not stale, also after the time limit (finding 20).
        steady = _personal_document(world.settings, world.manager, BEN, "A note with a long merge. " * 3, extraction=EXTRACTION)
        row = _contribution(world, conn, steady, run=False)
        (job,) = contributions.pending_merge_jobs(conn)
        stale: list[bool] = []

        def _late_start(c) -> None:
            c.execute("UPDATE jobs SET updated_at=? WHERE id=?", (time.time() - contributions.STALE_JOB_SECONDS - 1, job["id"]))
            c.commit()

        def _resolve(conn_, vstore, **kwargs):
            if kwargs["name"] == "Korea":
                _other(_late_start)
            if kwargs["name"] == "Busan":
                _other(lambda c: stale.append(contributions.has_pending_merge_jobs(c)))
            return REAL_RESOLVE(conn_, vstore, **kwargs)

        monkeypatch.setattr(contributions, "resolve_or_create", _resolve)
        assert contributions.run_merge_job(conn, provider, world.settings, contribution=row["public_id"], job_id=job["id"]).state == "visible"
        assert stale == [False]
    finally:
        conn.close()


def test_merge_that_fails_part_way_is_undone_by_the_purge_of_the_copy(world, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    kb_id, data_dir = world.kb["id"], Path(world.settings.data_dir)
    first = _personal_document(world.settings, world.manager, BEN, "Seoul and Busan are cities of Korea. " * 3, extraction=EXTRACTION)
    second = _personal_document(world.settings, world.manager, BEN, "Korea has mountains and the island Jeju. " * 3, extraction=KOREA_MORE)
    conn = dbm.connect(world.settings.db_file)
    try:
        one = _contribution(world, conn, first)

        def _stop() -> None:
            raise RuntimeError("the embedding service stopped")

        _at_entity(monkeypatch, "Jeju", _stop)
        failed = _contribution(world, conn, second)
        assert (failed["state"], failed["decision_reason"]) == ("failed", "merge_failed")
        # The merge into ``Korea`` was committed before the failure, and its decision is in the log of the copy.
        assert "Korea has many mountains." in _entities(conn, kb_id)["Korea"]["observations"]
        log = json.loads(conn.execute("SELECT meta FROM documents WHERE id=?", (failed["document_id"],)).fetchone()[0])["resolution_log"]
        assert [(d["entity"], d["decision"]) for d in log] == [("Korea", "MERGE"), ("Seoul", "MERGE")]

        contributions.remove(conn, data_dir, kb_id=kb_id, contribution=failed["public_id"], account_id=ANN, reason="low_quality")
        korea = _entities(conn, kb_id)["Korea"]
        assert (korea["aliases"], korea["observations"], korea["sources"]) == (["Republic of Korea"], ["A country in East Asia."], [one["document_id"]])
    finally:
        conn.close()


# --- findings 6, 13, 17: revisions ------------------------------------------------------------------------------------


def test_revisions_wait_for_the_merge_and_the_newest_one_wins(world) -> None:  # noqa: F811
    kb_id, data_dir = world.kb["id"], Path(world.settings.data_dir)
    provider = get_provider(world.settings)
    document = _personal_document(world.settings, world.manager, BEN, "The first text of a page about Korea. " * 3, url="https://example.org/page", extraction=EXTRACTION)
    second_text, third_text = "The second text of the page. " * 3, "The third text of the page. " * 3
    conn = dbm.connect(world.settings.db_file)
    try:
        # A new version while the contribution is ``merging``: the copy that the merge job builds is not replaced; the new version waits as a revision (finding 6).
        merging = _contribution(world, conn, document, run=False)
        (revision_job,) = _new_root_version(world, conn, document, second_text, EXTRACTION)
        row = kb_collab.get_contribution(conn, merging["public_id"])
        (revision,) = kb_collab.list_revisions(conn, merging["public_id"])
        assert (row["state"], row["document_id"], revision["state"]) == ("merging", merging["document_id"], "merging")
        waits = contributions.run_merge_job(conn, provider, world.settings, contribution=merging["public_id"], job_id=revision_job, revision=revision["id"])
        assert (waits.state, waits.error) == ("merging", "revision_waits") and conn.execute("SELECT status FROM jobs WHERE id=?", (revision_job,)).fetchone()[0] == "pending"

        # A newer version before the jobs run: the older revision is closed and its copy is purged (finding 17).
        _new_root_version(world, conn, document, third_text, KOREA_MORE)
        older, newer = kb_collab.list_revisions(conn, merging["public_id"])
        assert (older["state"], newer["state"]) == ("failed", "merging")
        assert _documents(conn, kb_id) == sorted([merging["document_id"], newer["document_id"]])
        # The same version again starts no second revision.
        assert contributions.start_revisions(conn, data_dir, root_kb_id=world.manager.personal_kb_for(BEN).id, root_document_id=document) == []

        _run_jobs(world, conn)
        row = kb_collab.get_contribution(conn, merging["public_id"])
        assert (row["state"], row["document_id"]) == ("visible", newer["document_id"]) and _documents(conn, kb_id) == [newer["document_id"]]
        assert conn.execute("SELECT content_hash FROM documents WHERE id=?", (row["document_id"],)).fetchone()[0] == content_hash(third_text)
        assert set(_entities(conn, kb_id)) == {"Korea", "Seoul", "Jeju"}

        # A withdrawal purges also the copy of a revision that waits: the document can come again (finding 13).
        _new_root_version(world, conn, document, "The fourth text of the page. " * 3, EXTRACTION)
        assert len(_documents(conn, kb_id)) == 2
        contributions.withdraw(conn, data_dir, kb_id=kb_id, contribution=merging["public_id"], account_id=BEN)
        assert _documents(conn, kb_id) == [] and _entities(conn, kb_id) == {}
        assert [r.error for r in _run_jobs(world, conn)] == ["contribution_not_found"]
        again = _contribution(world, conn, document)
        assert again["state"] == "visible" and _documents(conn, kb_id) == [again["document_id"]]
    finally:
        conn.close()


# --- findings 8, 11: files and logs of a copy -------------------------------------------------------------------------


def test_e2e_copy_and_fork_have_their_own_image_paths_and_a_failed_fork_leaves_nothing(world, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    kb_id, data_dir = world.kb["id"], Path(world.settings.data_dir)
    personal = world.manager.personal_kb_for(BEN).id
    document = _personal_document(world.settings, world.manager, BEN, "A page about Korea with two images. " * 3, extraction=EXTRACTION)
    second = _personal_document(world.settings, world.manager, BEN, "Korea has mountains and the island Jeju. " * 3, extraction=KOREA_MORE)
    (data_dir / "images").mkdir(parents=True, exist_ok=True)
    (data_dir / "images" / f"{document}_0.png").write_bytes(b"png-bytes")  # the file of the second image is not there
    conn = dbm.connect(world.settings.db_file)
    try:
        dbm.set_document_images(conn, document, [{"url": "https://example.org/a.png", "local": f"images/{document}_0.png"}, {"url": "https://example.org/b.png", "local": f"images/{document}_1.png"}], kb_id=personal)
        conn.execute(
            "UPDATE documents SET detail=?, detail_html=? WHERE id=?",
            (f"image::/image?p=images/{document}_0.png[a]\n\nimage::/image?p=images/{document}_1.png[b]", f'<img src="/image?p=images/{document}_0.png"><img src="images/{document}_1.png">', document),
        )
        conn.commit()
        copy_id = _contribution(world, conn, document)["document_id"]
        _contribution(world, conn, second)

        def _body(document_id: str) -> tuple[str, str]:
            row = conn.execute("SELECT detail, detail_html FROM documents WHERE id=?", (document_id,)).fetchone()
            return row["detail"], row["detail_html"]

        # The path of the copied file is the path of the copy; the path of the file that was not copied stays.
        assert _body(copy_id) == (f"image::/image?p=images/{copy_id}_0.png[a]\n\nimage::/image?p=images/{document}_1.png[b]", f'<img src="/image?p=images/{copy_id}_0.png"><img src="images/{document}_1.png">')
        assert (data_dir / "images" / f"{copy_id}_0.png").read_bytes() == b"png-bytes"

        # A fork whose file copy fails: the files that it copied go, and no row of the fork stays.
        kbs_before = [int(r[0]) for r in conn.execute("SELECT id FROM knowledge_bases ORDER BY id")]
        files_before = sorted(p.name for p in (data_dir / "images").iterdir())
        real_copy = contributions.copy_document_files
        calls: list[str] = []

        def _copy_then_fail(conn_, data_dir_, **kwargs):
            calls.append(kwargs["target_document_id"])
            real_copy(conn_, data_dir_, **kwargs)
            if len(calls) == 2:
                raise OSError("the disk is full")

        monkeypatch.setattr(contributions, "copy_document_files", _copy_then_fail)
        failed = world.client.post(f"{world.base}/forks", headers=world.cho)
        assert (failed.status_code, failed.json()) == (500, {"error": "fork_failed"}) and len(calls) == 2
        assert sorted(p.name for p in (data_dir / "images").iterdir()) == files_before
        assert [int(r[0]) for r in conn.execute("SELECT id FROM knowledge_bases ORDER BY id")] == kbs_before
        assert conn.execute("SELECT COUNT(*) FROM documents WHERE id IN (?, ?)", calls).fetchone()[0] == 0

        # A fork: each copy has its own image path, and its decision log has the entities of the fork (finding 11).
        monkeypatch.setattr(contributions, "copy_document_files", real_copy)
        made = world.client.post(f"{world.base}/forks", headers=world.cho)
        assert made.status_code == 201, made.text
        fork_id = int(made.json()["knowledge_base"]["id"])
        fork_copy = conn.execute("SELECT document_id FROM kb_contributions WHERE kb_id=? AND source_document_id=?", (fork_id, copy_id)).fetchone()[0]
        assert _body(fork_copy)[0] == f"image::/image?p=images/{fork_copy}_0.png[a]\n\nimage::/image?p=images/{document}_1.png[b]"
        assert (data_dir / "images" / f"{fork_copy}_0.png").read_bytes() == b"png-bytes"
        fork_entities = {e["id"] for e in _entities(conn, fork_id).values()}
        merges = [d for r in conn.execute("SELECT meta FROM documents WHERE kb_id=?", (fork_id,)) for d in json.loads(r[0] or "{}").get("resolution_log", []) if d["decision"] == "MERGE"]
        assert merges and all(d["target_entity_id"] in fork_entities and d["rollback_payload"]["target_entity_id"] in fork_entities for d in merges)
        assert not fork_entities & {e["id"] for e in _entities(conn, kb_id).values()}
    finally:
        conn.close()


# --- findings 3, 14, 15: the automatic rule ---------------------------------------------------------------------------


def test_automatic_rule_reads_the_personal_knowledge_base_one_time_and_follows_merged_themes(world, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    client, kb_id, data_dir = world.client, world.kb["id"], Path(world.settings.data_dir)
    web = _document(world, BEN, "web", KOREA)
    _document(world, BEN, "space", SPACE)
    # Two targets: ben is a member of Cities and of Towns. The two have seed entities.
    assert client.patch(f"{world.base}/", json={"seed_entities": ["Korea", "Seoul"]}, headers=world.ann).status_code == 200
    towns = client.post("/kb", json={"label": "Towns", "knowledge_type": "real", "seed_entities": ["Korea", "Busan"]}, headers=world.ann).json()["knowledge_base"]
    assert client.patch(f"/kb/{towns['number']}/", json={"join_policy": "open"}, headers=world.ann).status_code == 200
    assert client.post(f"/kb/{towns['number']}/membership", headers=world.ben).status_code == 201
    rule = {"to_memberships": 1, "to_mutual": 0, "include_uploads": 0, "theme_ids": []}
    conn = dbm.connect(world.settings.db_file)
    try:
        reads: list[int] = []
        checked: list[str] = []
        real_keys, real_duplicate = theme_judgement._document_keys, contributions.find_duplicate

        def _one_document(*args, **kwargs):
            raise AssertionError("the run read the entities again for one document")

        monkeypatch.setattr(theme_judgement, "_document_keys", lambda c, kb: reads.append(kb) or real_keys(c, kb))
        monkeypatch.setattr(theme_judgement, "document_keys", _one_document)
        monkeypatch.setattr(contributions, "find_duplicate", lambda c, kb, **kwargs: checked.append(kwargs["canonical_url"]) or real_duplicate(c, kb, **kwargs))
        planned = auto_contribution.plan(conn, BEN, rule, origin=None)
        assert [(t["kb_id"], [d["document"] for d in t["documents"]]) for t in planned] == [(kb_id, [web]), (int(towns["id"]), [web])]
        # One read of the entities for the two targets; the checks of one document run only for the document over the threshold.
        assert reads == [world.manager.personal_kb_for(BEN).id] and checked == ["https://example.org/web"] * 2

        # A merged theme of the rule is replaced by its target; a loop of merges gives no target and does not hang (finding 14).
        old, new, loop_a, loop_b = (themes_store.create_theme(conn, label=label, knowledge_type="real") for label in ("Old places", "Places", "Loop A", "Loop B"))
        themes_store.add_theme_member(conn, theme_id=new["id"], kb_id=kb_id, source="admin", pinned=True)
        themes_store.update_theme(conn, old["id"], state="merged", merged_into_theme_id=new["id"])
        themes_store.update_theme(conn, loop_a["id"], state="merged", merged_into_theme_id=loop_b["id"])
        themes_store.update_theme(conn, loop_b["id"], state="merged", merged_into_theme_id=loop_a["id"])
        conn.commit()
        by_theme = {"to_memberships": 0, "to_mutual": 0, "include_uploads": 0}
        assert auto_contribution.targets(conn, BEN, {**by_theme, "theme_ids": [old["id"]]}) == [kb_id]
        assert auto_contribution.targets(conn, BEN, {**by_theme, "theme_ids": [loop_a["id"]]}) == []

        # The withdrawal of an automatic contribution sets the flag "never contribute": the next run does not send the document again (finding 15).
        sent = _contribution(world, conn, web, origin="auto", run=False)
        assert contributions.withdraw(conn, data_dir, kb_id=kb_id, contribution=sent["public_id"], account_id=BEN) == {"contribution_blocked": True}
        assert kb_collab.contribution_blocks(conn, BEN) == {web}
        assert auto_contribution.plan(conn, BEN, rule, origin=None) == []
        # A contribution that the account made itself gets no flag.
        own = _contribution(world, conn, _document(world, BEN, "own", KOREA), run=False)
        assert contributions.withdraw(conn, data_dir, kb_id=kb_id, contribution=own["public_id"], account_id=BEN) == {"contribution_blocked": False}
    finally:
        conn.close()


# --- findings 9, 19, 20: decisions of another account -----------------------------------------------------------------


def test_approval_of_a_purged_reviewer_stays_and_a_self_approval_is_no_decision(tmp_path: Path) -> None:
    """Rows of the store only: the knowledge bases 900 to 904 and the submission item 1 are numbers without rows."""
    conn = dbm.connect(tmp_path / "claire.db")
    try:
        dbm.init_db(conn)
        conn.execute("PRAGMA foreign_keys=OFF")
        now = time.time()
        clock = iter(range(int(now * 1000), int(now * 1000) + 100))  # one public ID for each row

        def _row(kb_id: int, state: str, *, contributor: int, self_approved: bool = False, item: int | None = None) -> int:
            return kb_collab.add_contribution(
                conn, kb_id=kb_id, document_id=None, origin="direct", state=state, contributor_account_id=contributor, submission_item_id=item,
                approved_at=now if self_approved else None, approved_by_account_id=contributor if self_approved else None, now=now, now_ms=next(clock),
            )

        # ann approves a contribution of ben. A contribution that ben made as a reviewer is approved at once: no decision of another account.
        contributions.approve(conn, kb_id=900, contribution=_row(900, "visible", contributor=BEN), account_id=ANN, is_admin=False, now=now)
        _row(900, "visible", contributor=BEN, self_approved=True)
        kb_collab.add_submission(conn, account_id=ANN, now=now, now_ms=next(clock))
        conn.commit()

        def _decisions() -> list[dict]:
            return kb_collab.decisions_of_account(conn, BEN, since=now - 1, origins=("direct",), reasons=("spam",))

        assert _decisions() == [{"kb_id": 900, "approved": True}]
        # The purge of the reviewer keeps the approval in the rate of the contributor (finding 9), and the submission of the purged account has no account (finding 19).
        kb_collab.purge_account(conn, ANN, delete_activity_records=False)
        assert conn.execute("SELECT approved_by_account_id FROM kb_contributions WHERE kb_id=900 AND decided_at IS NOT NULL").fetchone()[0] is None
        assert _decisions() == [{"kb_id": 900, "approved": True}]
        assert [r[0] for r in conn.execute("SELECT account_id FROM kb_submissions")] == [None]

        # The agreement of decisions: 901 rejected, 902 and 903 approved by another account, 904 has only the approval of its own reviewer (finding 20). 904 has no decision; 902 and 903 see no majority of the others.
        _row(901, "rejected", contributor=BEN, item=1)
        for kb_id in (902, 903):
            contributions.approve(conn, kb_id=kb_id, contribution=_row(kb_id, "visible", contributor=BEN, item=1), account_id=CHO, is_admin=False, now=now)
        _row(904, "visible", contributor=BEN, self_approved=True, item=1)
        assert contributions.decision_agreement(conn) == [{"kb_id": 901, "decisions": 1, "agreement": 0.0}]
    finally:
        conn.close()
