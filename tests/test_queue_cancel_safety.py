"""Tests for Stage 1 and Stage 2 cancel and purge safety controls."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from claire.extract.decision import ResolutionDecision, rollback_resolution
from claire.extract.provider import ExtractedEntity, ExtractedRelation, ExtractionResult
from claire.ingest.active import active_job, clear_job_cancel_request, get_active_jobs, get_job_cancel_request, request_job_cancel
from claire.ingest.pipeline import ingest
from claire.ingest.queue_manager import get_queue_coordinator, reset_queue_coordinators
from claire.ontology.base import Document, Entity, Relation
from claire.store import db as dbm
from claire.store.vectors import make_vector_store


def _setup_db(tmp_path: Path) -> tuple[sqlite3.Connection, Path]:
    db_file = tmp_path / "claire.db"
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    conn = dbm.connect(db_file)
    dbm.init_db(conn)
    return conn, data_dir


def test_estimate_document_purge_impact_and_dry_run_report(tmp_path: Path) -> None:
    """Verify blast-radius counts across entities, relations, disk files, and federation_objects."""
    conn, data_dir = _setup_db(tmp_path)
    try:
        doc_a = Document(id="doc_a", url="https://example.com/a", canonical_url="https://example.com/a", title="Doc A", raw_text="Body A", content_hash="hash_a")
        doc_b = Document(id="doc_b", url="https://example.com/b", canonical_url="https://example.com/b", title="Doc B", raw_text="Body B", content_hash="hash_b")
        dbm.insert_document(conn, doc_a, kb_id=0)
        dbm.insert_document(conn, doc_b, kb_id=0)

        ent_orphan = Entity(id="ent_orphan", type="Concept", name="OnlyA", sources=["doc_a"])
        ent_shared = Entity(id="ent_shared", type="Concept", name="BothAB", sources=["doc_a", "doc_b"])
        ent_other = Entity(id="ent_other", type="Concept", name="OnlyB", sources=["doc_b"])
        dbm.upsert_entity(conn, ent_orphan, kb_id=0)
        dbm.upsert_entity(conn, ent_shared, kb_id=0)
        dbm.upsert_entity(conn, ent_other, kb_id=0)

        dbm.upsert_relation(conn, Relation(type="related_to", source_id="ent_orphan", target_id="ent_shared", sources=["doc_a"]), kb_id=0)
        dbm.upsert_relation(conn, Relation(type="related_to", source_id="ent_shared", target_id="ent_other", sources=["doc_b"]), kb_id=0)

        art_dir = data_dir / "raw" / "artifacts"
        art_dir.mkdir(parents=True, exist_ok=True)
        (art_dir / "doc_a.txt.zst").write_bytes(b"zst")
        img_dir = data_dir / "images"
        img_dir.mkdir(parents=True, exist_ok=True)
        (img_dir / "doc_a_0.png").write_bytes(b"png")
        att_dir = data_dir / "raw" / "attachments" / "doc_a"
        att_dir.mkdir(parents=True, exist_ok=True)
        (att_dir / "file.pdf").write_bytes(b"pdf")

        now = time.time()
        acc_row = conn.execute("SELECT id FROM accounts ORDER BY id LIMIT 1").fetchone()
        acc_id = int(acc_row[0]) if acc_row else 1
        if acc_row is None:
            conn.execute("INSERT INTO accounts (id, public_id, username, role, status, created_at, updated_at) VALUES (1, 10001, 'alice', 'member', 'active', ?, ?)", (now, now))
        conn.execute("INSERT OR IGNORE INTO knowledge_bases (id, label, is_personal, created_at, updated_at) VALUES (0, 'Default', 1, ?, ?)", (now, now))
        conn.execute(
            "INSERT INTO federation_objects (public_id, account_id, kb_id, document_id, published_by_account_id, published_at, updated_at) VALUES (1001, ?, 0, 'doc_a', ?, ?, ?)",
            (acc_id, acc_id, now, now),
        )
        conn.commit()

        impact = dbm.estimate_document_purge_impact(conn, data_dir, ["doc_a"], kb_id=0)
        assert impact["purged_count"] == 1
        assert len(impact["target_documents"]) == 1
        assert impact["orphan_entities_count"] == 1
        assert impact["shared_entities_count"] == 1
        assert impact["orphan_relations_count"] == 1
        assert impact["disk_files_count"] == 3
        assert impact["federation_objects_count"] == 1

        dry = dbm.purge_document_cascade(conn, data_dir, ["doc_a"], dry_run=True, kb_id=0)
        assert dry["dry_run"] is True
        assert dry["orphan_entities_count"] == 1
        assert dry["shared_entities_count"] == 1
        assert dry["orphan_relations_count"] == 1
        assert dry["federation_objects_count"] == 1
        assert dry["disk_files_count"] == 3
        assert dbm.get_document(conn, "doc_a", kb_id=0) is not None
    finally:
        conn.close()


def test_purge_document_cascade_atomic_rollback_on_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure a mid-purge database failure rolls back both entity graph changes and table deletes."""
    conn, data_dir = _setup_db(tmp_path)
    try:
        dec = ResolutionDecision(
            entity="SharedNode",
            stage="exact_match",
            decision="MERGE",
            candidate="SharedNode",
            target_entity_id="ent_shared",
            rollback_payload={"target_entity_id": "ent_shared", "added_aliases": ["AliasFromBadDoc"], "added_observations": ["ObsFromBadDoc"], "added_source": "doc_bad", "prev_type": None},
        )
        doc_bad = Document(id="doc_bad", url="https://example.com/bad", canonical_url="https://example.com/bad", title="Bad Doc", raw_text="Bad body", content_hash="hash_bad", meta={"resolution_log": [dec.to_dict()]})
        dbm.insert_document(conn, doc_bad, kb_id=0)
        dbm.upsert_entity(conn, Entity(id="ent_shared", type="Concept", name="SharedNode", aliases=["AliasFromBadDoc"], observations=["BaseObs", "ObsFromBadDoc"], sources=["doc_good", "doc_bad"]), kb_id=0)

        from claire.store import federation as federation_store

        def _boom(*args, **kwargs):
            raise RuntimeError("simulated federation failure")

        monkeypatch.setattr(federation_store, "retract_objects", _boom)

        with pytest.raises(RuntimeError, match="simulated federation failure"):
            dbm.purge_document_cascade(conn, data_dir, ["doc_bad"], dry_run=False, kb_id=0, heal=False, vacuum=False)

        assert dbm.get_document(conn, "doc_bad", kb_id=0) is not None
        ent = dbm.get_entity(conn, "ent_shared", kb_id=0)
        assert ent is not None
        assert "AliasFromBadDoc" in ent.aliases
        assert "ObsFromBadDoc" in ent.observations
        assert ent.sources == ["doc_good", "doc_bad"]
    finally:
        conn.close()


def test_rollback_resolution_sequential_prev_type_guard(tmp_path: Path) -> None:
    """Keep the entity type when a later remaining MERGE changed it, and restore prev_type when none did."""
    conn, _ = _setup_db(tmp_path)
    try:
        ent = Entity(id="ent_1", type="Person", name="Kafka", observations=["Obs1", "Obs2", "Obs3"], sources=["doc_1", "doc_2", "doc_3"])
        dbm.upsert_entity(conn, ent, kb_id=0)

        dec_2 = ResolutionDecision(
            entity="Kafka",
            stage="exact_match",
            decision="MERGE",
            candidate="Kafka",
            target_entity_id="ent_1",
            timestamp=200.0,
            rollback_payload={"target_entity_id": "ent_1", "added_aliases": [], "added_observations": ["Obs2"], "added_source": "doc_2", "prev_type": "Note"},
        )
        dec_3 = ResolutionDecision(
            entity="Kafka",
            stage="exact_match",
            decision="MERGE",
            candidate="Kafka",
            target_entity_id="ent_1",
            timestamp=300.0,
            rollback_payload={"target_entity_id": "ent_1", "added_aliases": [], "added_observations": ["Obs3"], "added_source": "doc_3", "prev_type": "Concept"},
        )
        dbm.insert_document(conn, Document(id="doc_1", title="Doc 1", raw_text="A", content_hash="h1", fetched_at=100.0, meta={}), kb_id=0)
        dbm.insert_document(conn, Document(id="doc_2", title="Doc 2", raw_text="B", content_hash="h2", fetched_at=200.0, meta={"resolution_log": [dec_2.to_dict()]}), kb_id=0)
        dbm.insert_document(conn, Document(id="doc_3", title="Doc 3", raw_text="C", content_hash="h3", fetched_at=300.0, meta={"resolution_log": [dec_3.to_dict()]}), kb_id=0)

        # Roll back doc_2 while later doc_3 stays: keep Person instead of regressing to Note.
        ok = rollback_resolution(conn, dec_2, kb_id=0, document_id="doc_2", gone=["doc_2"])
        assert ok is True
        after_first = dbm.get_entity(conn, "ent_1", kb_id=0)
        assert after_first is not None
        assert after_first.type == "Person"
        assert after_first.sources == ["doc_1", "doc_3"]

        # Roll back doc_3 while only earlier doc_1 stays: restore prev_type to Concept.
        ok2 = rollback_resolution(conn, dec_3, kb_id=0, document_id="doc_3", gone=["doc_3"])
        assert ok2 is True
        after_second = dbm.get_entity(conn, "ent_1", kb_id=0)
        assert after_second is not None
        assert after_second.type == "Concept"
        assert after_second.sources == ["doc_1"]
    finally:
        conn.close()


def test_cross_process_cancel_marker_lifecycle(tmp_path: Path) -> None:
    """Verify active_jobs cancel markers persist across worker progress updates and prune on completion."""
    data_dir = tmp_path / "data"
    assert request_job_cancel(data_dir, "../bad") is None
    assert request_job_cancel(data_dir, "missing_job") is None

    with active_job(data_dir, kind="ingest", payload="https://example.com/live", kb_id=0, inbox_id=42) as job:
        assert job.state["inbox_id"] == 42
        marker = request_job_cancel(data_dir, job.job_id, tombstone=True, reason="test_cancel")
        assert marker is not None
        assert marker["tombstone"] is True

        # Worker progress updates write active_jobs/<job_id>.json without erasing <job_id>.cancel.json.
        job.update(stage="extract", msg="Extracting...")
        loaded_marker = get_job_cancel_request(data_dir, job.job_id)
        assert loaded_marker is not None
        assert loaded_marker["reason"] == "test_cancel"

        active_list = get_active_jobs(data_dir)
        assert len(active_list) == 1
        assert active_list[0]["cancel_requested"] is True
        assert active_list[0]["cancel_tombstone"] is True
        assert active_list[0]["inbox_id"] == 42

        job_id = job.job_id

    # Exiting active_job cleans up the sibling cancel marker.
    assert get_job_cancel_request(data_dir, job_id) is None

    # Orphaned cancel markers are pruned by get_active_jobs.
    orphan_cancel = data_dir / "active_jobs" / "orphan_job_1.cancel.json"
    orphan_cancel.write_text(json.dumps({"job_id": "orphan_job_1", "tombstone": False}), encoding="utf-8")
    get_active_jobs(data_dir)
    assert not orphan_cancel.exists()


def test_pipeline_cancel_before_document_leaves_zero_writes(tmp_path: Path) -> None:
    """Cancelling after fetch and before insert_document writes zero documents, entities, or relations."""
    conn, data_dir = _setup_db(tmp_path)
    try:
        vstore = make_vector_store(conn, "brute")
        provider = MagicMock()
        provider.name = "mock"
        provider.model = "mock"

        url = "https://example.com/cancel-pre-doc"
        doc = Document(url=url, canonical_url=url, title="Pre-Doc Cancel", raw_text="Enough content for extraction test without creating a document.", source_type="web", content_hash="hash_pre_doc")

        def _fetch_and_cancel(payload: str) -> Document:
            jobs = get_active_jobs(data_dir)
            assert len(jobs) == 1
            request_job_cancel(data_dir, jobs[0]["job_id"], tombstone=True, reason="pre_doc_abort")
            return doc

        report = ingest(url, conn=conn, provider=provider, vstore=vstore, data_dir=data_dir, source="web", fetch_fn=_fetch_and_cancel)
        assert report.cancelled is True
        assert report.error == "cancelled_by_user"
        assert report.document_id is None
        assert conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM relations").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM raw_inbox").fetchone()[0] == 0
        assert dbm.is_tombstoned(conn, url=url) is True

        provider.extract.assert_not_called()
        jobs_after = get_active_jobs(data_dir)
        assert len(jobs_after) == 1
        assert jobs_after[0]["active"] is False
        assert jobs_after[0]["stage"] == "cancelled"
        assert get_job_cancel_request(data_dir, jobs_after[0]["job_id"]) is None
    finally:
        conn.close()


def test_pipeline_cancel_mid_resolve_loop_compensates_atomically(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Cancelling mid-resolve loop flushes partial decisions and purges leaked graph nodes without VACUUM."""
    conn, data_dir = _setup_db(tmp_path)
    try:
        vstore = make_vector_store(conn, "brute")
        dbm.upsert_entity(conn, Entity(id="ent_existing", type="Concept", name="SharedConcept", aliases=["OldAlias"], observations=["Existing obs"], sources=["doc_keep"]), kb_id=0)
        conn.execute("INSERT INTO documents (id, kb_id, title, raw_text, source_type, content_hash, hidden, meta) VALUES ('doc_keep', 0, 'Keep', 'Keep body', 'web', 'hash_keep', 0, '{}')")
        conn.commit()

        vacuums: list[str] = []
        original_heal = dbm.heal_graph
        heals: list[int] = []
        monkeypatch.setattr(dbm, "heal_graph", lambda *a, **kw: heals.append(1) or original_heal(*a, **kw))

        provider = MagicMock()
        provider.name = "mock"
        provider.model = "mock"
        provider.effort = "low"
        provider.has_embed_capability.return_value = False
        provider.render_detail.return_value = "Detail text"
        provider.extract.return_value = ExtractionResult(
            summary="Summary text",
            entities=[
                ExtractedEntity(name="SharedConcept", type="Concept", aliases=["AddedAlias"], observations=["Added obs"]),
                ExtractedEntity(name="TempNodeA", type="Tool", aliases=[], observations=["Temp obs"]),
                ExtractedEntity(name="TempNodeB", type="Tool", aliases=[], observations=["Never created"]),
            ],
            relations=[ExtractedRelation(source="SharedConcept", target="TempNodeA", type="related_to")],
            model="mock",
            prompt_version="v1",
            raw_response="{}",
        )

        # Trigger cancel right after TempNodeA resolves (2nd entity) and before TempNodeB starts.
        from claire.ingest import pipeline as pipeline_mod

        orig_resolve = pipeline_mod.resolve_or_create
        resolve_calls = 0

        def _wrapped_resolve(*args, **kwargs):
            nonlocal resolve_calls
            res = orig_resolve(*args, **kwargs)
            resolve_calls += 1
            if resolve_calls == 2:
                active_jobs = get_active_jobs(data_dir)
                assert len(active_jobs) == 1
                request_job_cancel(data_dir, active_jobs[0]["job_id"], tombstone=False, reason="mid_loop_abort")
            return res

        monkeypatch.setattr(pipeline_mod, "resolve_or_create", _wrapped_resolve)
        conn.set_trace_callback(lambda sql: vacuums.append(sql) if "VACUUM" in sql.upper() else None)

        url = "https://example.com/cancel-mid-resolve"
        doc = Document(url=url, canonical_url=url, title="Mid Loop Cancel", raw_text="Long enough text for extraction and resolution cancellation test.", source_type="web", content_hash="hash_mid_loop")
        report = ingest(url, conn=conn, provider=provider, vstore=vstore, data_dir=data_dir, source="web", fetch_fn=lambda _: doc)
        conn.set_trace_callback(None)

        assert resolve_calls == 2
        assert report.cancelled is True
        assert report.error == "cancelled_by_user"
        assert heals == []
        assert vacuums == []
        assert dbm.get_document(conn, doc.id, kb_id=0) is None
        assert conn.execute("SELECT COUNT(*) FROM raw_inbox").fetchone()[0] == 0
        assert dbm.find_entities_by_norm(conn, "tempnodea", kb_id=0) == []
        assert dbm.find_entities_by_norm(conn, "tempnodeb", kb_id=0) == []

        shared = dbm.get_entity(conn, "ent_existing", kb_id=0)
        assert shared is not None
        assert shared.sources == ["doc_keep"]
        assert "AddedAlias" not in shared.aliases
        assert "Added obs" not in shared.observations

        jobs_after = get_active_jobs(data_dir)
        assert len(jobs_after) == 1
        assert jobs_after[0]["active"] is False
        assert jobs_after[0]["stage"] == "cancelled"
        assert jobs_after[0]["heatmap_matrix"] is None
    finally:
        conn.close()


def test_pipeline_cancel_after_extract_skips_detail_and_relation_judge(tmp_path: Path) -> None:
    """Cancelling right after provider.extract skips render_detail and relation linking and purges the doc."""
    conn, data_dir = _setup_db(tmp_path)
    try:
        vstore = make_vector_store(conn, "brute")
        provider = MagicMock()
        provider.name = "mock"
        provider.model = "mock"
        provider.effort = "low"
        provider.has_embed_capability.return_value = False

        url = "https://example.com/cancel-post-extract"
        doc = Document(url=url, canonical_url=url, title="Post-Extract Cancel", raw_text="Long enough text for extraction and cancel after extract checkpoint.", source_type="web", content_hash="hash_post_extract")

        def _extract_and_cancel(*args, **kwargs):
            active_jobs = get_active_jobs(data_dir)
            assert len(active_jobs) == 1
            request_job_cancel(data_dir, active_jobs[0]["job_id"], tombstone=False, reason="post_extract_abort")
            return ExtractionResult(
                summary="Summary",
                entities=[ExtractedEntity(name="AlphaNode", type="Tool", aliases=[], observations=["Obs"])],
                relations=[],
                model="mock",
                prompt_version="v1",
                raw_response="{}",
            )

        provider.extract.side_effect = _extract_and_cancel

        report = ingest(url, conn=conn, provider=provider, vstore=vstore, data_dir=data_dir, source="web", fetch_fn=lambda _: doc)
        assert report.cancelled is True
        assert report.error == "cancelled_by_user"
        provider.render_detail.assert_not_called()
        provider.judge_relationship.assert_not_called()
        assert dbm.get_document(conn, doc.id, kb_id=0) is None
        assert conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM raw_inbox").fetchone()[0] == 0
    finally:
        conn.close()


def test_upsert_entity_tokenizes_before_lock_and_retries_busy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify upsert_entity tokenizes before acquiring write locks and retries on SQLite busy errors."""
    db_file = tmp_path / "claire.db"
    conn1 = dbm.connect(db_file)
    try:
        dbm.init_db(conn1)
        conn1.execute("PRAGMA busy_timeout=10;")

        from claire.retrieval import tokenizer as tok_mod

        orig_get_tok = tok_mod.get_search_tokenizer
        tok_calls = 0

        class _CountingTokenizer:
            def __init__(self, inner) -> None:
                self._inner = inner

            def add_user_words(self, words) -> None:
                self._inner.add_user_words(words)

            def extract_search_tokens(self, text: str) -> list[str]:
                nonlocal tok_calls
                tok_calls += 1
                assert not conn1.in_transaction
                return self._inner.extract_search_tokens(text)

        monkeypatch.setattr(tok_mod, "get_search_tokenizer", lambda: _CountingTokenizer(orig_get_tok()))

        locked_evt = threading.Event()
        release_evt = threading.Event()

        def _hold_and_release_lock() -> None:
            conn2 = dbm.connect(db_file)
            try:
                conn2.execute("BEGIN IMMEDIATE")
                locked_evt.set()
                time.sleep(0.15)
                conn2.commit()
            finally:
                conn2.close()
                release_evt.set()

        locker = threading.Thread(target=_hold_and_release_lock, daemon=True)
        locker.start()
        assert locked_evt.wait(timeout=2.0)

        ent = Entity(id="ent_busy_1", type="Concept", name="SQLiteWAL", aliases=["WALMode"], observations=["Non-blocking read"], sources=["d1"])
        dbm.upsert_entity(conn1, ent, kb_id=0)
        assert release_evt.wait(timeout=2.0)
        assert tok_calls == 1
        saved = dbm.get_entity(conn1, "ent_busy_1", kb_id=0)
        assert saved is not None and saved.name == "SQLiteWAL"
    finally:
        conn1.close()


def test_purge_document_cascade_two_phase_prefilter_and_counts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify two-phase rollback planning, _sources_like_clause prefiltering, unrelated preservation, and rollback on error."""
    conn, data_dir = _setup_db(tmp_path)
    try:
        dbm.insert_document(conn, Document(id="doc_keep_1", url="https://example.com/k1", canonical_url="https://example.com/k1", title="Keep 1", raw_text="K1", content_hash="hk1"), kb_id=0)
        dbm.insert_document(conn, Document(id="doc_keep_2", url="https://example.com/k2", canonical_url="https://example.com/k2", title="Keep 2", raw_text="K2", content_hash="hk2"), kb_id=0)
        ent_shared = Entity(id="ent_shared_p", type="Concept", name="SharedP", aliases=["SharedAlias"], observations=["KeepObs", "DropObs"], sources=["doc_keep_1", "doc_drop"])
        ent_orphan = Entity(id="ent_orphan_p", type="Tool", name="OrphanP", observations=["OnlyDrop"], sources=["doc_drop"])
        ent_unrel_a = Entity(id="ent_unrel_a", type="Concept", name="UnrelA", observations=["StayA"], sources=["doc_keep_2"])
        ent_unrel_b = Entity(id="ent_unrel_b", type="Concept", name="UnrelB", observations=["StayB"], sources=["doc_keep_2"])
        dbm.upsert_entity(conn, ent_shared, kb_id=0)
        dbm.upsert_entity(conn, ent_orphan, kb_id=0)
        dbm.upsert_entity(conn, ent_unrel_a, kb_id=0)
        dbm.upsert_entity(conn, ent_unrel_b, kb_id=0)
        dbm.upsert_relation(conn, Relation(type="uses", source_id="ent_orphan_p", target_id="ent_shared_p", sources=["doc_drop"]), kb_id=0)
        dbm.upsert_relation(conn, Relation(type="relates_to", source_id="ent_unrel_a", target_id="ent_unrel_b", sources=["doc_keep_2"]), kb_id=0)

        dec_merge = ResolutionDecision(entity="SharedP", stage="exact_match", decision="MERGE", candidate="SharedP", target_entity_id="ent_shared_p", rollback_payload={"target_entity_id": "ent_shared_p", "added_aliases": ["SharedAlias"], "added_observations": ["DropObs"], "added_source": "doc_drop", "prev_type": None})
        dec_new = ResolutionDecision(entity="OrphanP", stage="no_match", decision="CREATE_NEW", candidate="OrphanP", target_entity_id="ent_orphan_p", rollback_payload={"target_entity_id": "ent_orphan_p"})
        dbm.insert_document(conn, Document(id="doc_drop", url="https://example.com/drop", canonical_url="https://example.com/drop", title="Drop", raw_text="Drop body", content_hash="hdrop", meta={"resolution_log": [dec_merge.to_dict(), dec_new.to_dict()]}), kb_id=0)

        impact = dbm.estimate_document_purge_impact(conn, data_dir, ["doc_drop"], kb_id=0)
        assert impact["purged_count"] == 1
        assert impact["orphan_entities_count"] == 1
        assert impact["shared_entities_count"] == 1
        assert impact["orphan_relations_count"] == 1

        report = dbm.purge_document_cascade(conn, data_dir, ["doc_drop"], dry_run=False, tombstone=True, kb_id=0, heal=False, vacuum=False)
        assert report["purged_count"] == 1
        assert dbm.get_document(conn, "doc_drop", kb_id=0) is None
        assert dbm.get_entity(conn, "ent_orphan_p", kb_id=0) is None
        shared_after = dbm.get_entity(conn, "ent_shared_p", kb_id=0)
        assert shared_after is not None and shared_after.sources == ["doc_keep_1"] and "DropObs" not in shared_after.observations
        assert dbm.get_entity(conn, "ent_unrel_a", kb_id=0) is not None
        assert dbm.get_entity(conn, "ent_unrel_b", kb_id=0) is not None
        unrel_rels = conn.execute("SELECT COUNT(*) FROM relations WHERE source_id='ent_unrel_a' AND target_id='ent_unrel_b'").fetchone()[0]
        assert unrel_rels == 1

        # Failure on a second purge rolls back atomically.
        from claire.store import federation as federation_store

        monkeypatch.setattr(federation_store, "retract_objects", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom")))
        with pytest.raises(RuntimeError, match="boom"):
            dbm.purge_document_cascade(conn, data_dir, ["doc_keep_2"], dry_run=False, kb_id=0, heal=False, vacuum=False)
        assert dbm.get_document(conn, "doc_keep_2", kb_id=0) is not None
        assert dbm.get_entity(conn, "ent_unrel_a", kb_id=0) is not None
    finally:
        conn.close()


def test_queue_coordinator_defers_purge_while_active_and_caps_retries(tmp_path: Path) -> None:
    """Verify IngestQueueCoordinator defers purges behind active jobs and caps retry concurrency."""
    db_file = tmp_path / "claire.db"
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    conn = dbm.connect(db_file)
    try:
        dbm.init_db(conn)
        now = time.time()
        conn.execute("INSERT INTO documents (id, kb_id, url, title, fetched_at, raw_text, source_type, extraction_status) VALUES ('doc_defer', 0, 'https://example.com/defer', 'Defer', ?, 'Body', 'web', 'extracted')", (now,))
        cur = conn.execute("INSERT INTO raw_inbox (kb_id, received_at, source, kind, payload, document_id, status) VALUES (0, ?, 'web', 'url', 'https://example.com/defer', 'doc_defer', 'cancelling')", (now,))
        cancel_inbox_id = int(cur.lastrowid)

        retry_ids: list[int] = []
        for idx in range(6):
            r_cur = conn.execute("INSERT INTO raw_inbox (kb_id, received_at, source, kind, payload, status) VALUES (0, ?, 'web', 'url', ?, 'received')", (now + idx, f"https://example.com/r{idx}"))
            retry_ids.append(int(r_cur.lastrowid))
        cancelled_retry_id = retry_ids[-1]
        conn.execute("DELETE FROM raw_inbox WHERE id=?", (cancelled_retry_id,))
        conn.commit()
    finally:
        conn.close()

    jobs_dir = data_dir / "active_jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)
    job_path = jobs_dir / "j_live_block.json"
    job_path.write_text(json.dumps({"active": True, "job_id": "j_live_block", "kb_id": 0, "payload": "https://example.com/live", "started_at": time.time(), "updated_at": time.time()}), encoding="utf-8")

    try:
        coord = get_queue_coordinator(data_dir, poll_interval=0.02)
        op_file = data_dir / "queue_ops" / f"cancel_{cancel_inbox_id}.json"
        coord.enqueue_cancel_purge(db_file=db_file, inbox_id=cancel_inbox_id, kb_id=0, document_id="doc_defer", prev_status="done", tombstone=True, force=False, reason="test_defer", file_ref=None)

        gate = threading.Event()
        lock = threading.Lock()
        active_now = 0
        max_concurrent = 0
        ran_retries: list[int] = []

        def _retry_fn(kb_settings, kb_id: int, inbox_id: int) -> None:
            nonlocal active_now, max_concurrent
            with lock:
                active_now += 1
                if active_now > max_concurrent:
                    max_concurrent = active_now
                ran_retries.append(inbox_id)
            gate.wait(timeout=2.0)
            with lock:
                active_now -= 1

        for iid in retry_ids:
            coord.enqueue_retry(db_file=db_file, kb_settings=None, kb_id=0, inbox_id=iid, retry_fn=_retry_fn)

        # While cancel op waits on active job, cancel op remains queued and queued retries also wait.
        time.sleep(0.12)
        assert op_file.is_file()
        assert ran_retries == []
        check_conn = dbm.connect_existing(db_file, readonly=True)
        try:
            assert dbm.get_document(check_conn, "doc_defer", kb_id=0) is not None
        finally:
            check_conn.close()

        # Mark active job inactive -> cancel op drains first, then retries execute up to _MAX_EXPENSIVE_JOBS (4).
        job_path.write_text(json.dumps({"active": False, "job_id": "j_live_block", "kb_id": 0, "completed_at": time.time()}), encoding="utf-8")
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and op_file.is_file():
            time.sleep(0.02)
        assert not op_file.is_file()

        # Wait for first batch of 4 concurrent retries to hit the gate, then release gate.
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and len(ran_retries) < 4:
            time.sleep(0.02)
        time.sleep(0.08)
        assert max_concurrent <= 4
        gate.set()

        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and len(ran_retries) < 5:
            time.sleep(0.02)
        time.sleep(0.05)
        assert sorted(ran_retries) == sorted(retry_ids[:-1])
        assert cancelled_retry_id not in ran_retries
        assert max_concurrent <= 4

        after_conn = dbm.connect_existing(db_file, readonly=True)
        try:
            assert dbm.get_document(after_conn, "doc_defer", kb_id=0) is None
            assert dbm.get_inbox(after_conn, cancel_inbox_id) is None
        finally:
            after_conn.close()
    finally:
        reset_queue_coordinators()
