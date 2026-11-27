"""Unit and E2E tests for automatic recovery of stale raw_inbox rows stuck in status='received'."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from claire.core.config import Settings
from claire.extract.provider import MockProvider
from claire.ingest import pipeline as pipemod
from claire.ingest import service as svcmod
from claire.ingest.active import active_job
from claire.ingest.service import IngestService
from claire.ontology.base import Document
from claire.store import db as dbm


def _mem_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    dbm.init_db(conn)
    return conn


def _make_settings(monkeypatch, tmp_path: Path) -> Settings:
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("CLAIRE_DATA_DIR", str(data_dir))
    monkeypatch.setenv("CLAIRE_DB_PATH", str(data_dir / "claire.db"))
    monkeypatch.setenv("CLAIRE_VAULT_PATH", str(tmp_path / "vault"))
    monkeypatch.setenv("CLAIRE_PROVIDER", "mock")
    return Settings()


def _patch_fetch(monkeypatch, fn) -> None:
    monkeypatch.setattr(svcmod, "default_fetch", fn)
    monkeypatch.setattr(pipemod, "default_fetch", fn)


# --- Unit tests: db.requeue_stale_received_inbox ---


def test_requeue_stale_received_transitions_stale_and_preserves_fresh() -> None:
    conn = _mem_db()
    now = 10_000.0
    stale_before = now - 1800.0

    stale_id = dbm.log_inbox(conn, source="telegram", payload="https://example.com/crashed-video", kind="url")
    fresh_id = dbm.log_inbox(conn, source="telegram", payload="https://example.com/fresh-video", kind="url")
    done_id = dbm.log_inbox(conn, source="telegram", payload="https://example.com/already-done", kind="url")
    staged_id = dbm.log_inbox(conn, source="web", payload="https://example.com/staged", kind="url", extraction_mode="client")
    conn.execute("UPDATE raw_inbox SET received_at=? WHERE id=?", (now - 3600.0, stale_id))
    conn.execute("UPDATE raw_inbox SET received_at=? WHERE id=?", (now - 60.0, fresh_id))
    conn.execute("UPDATE raw_inbox SET received_at=?, status='done' WHERE id=?", (now - 3600.0, done_id))
    conn.execute("UPDATE raw_inbox SET received_at=?, status='staged' WHERE id=?", (now - 3600.0, staged_id))
    conn.commit()

    requeued = dbm.requeue_stale_received_inbox(conn, stale_before=stale_before)
    assert requeued == [stale_id]

    stale_row = dbm.get_inbox(conn, stale_id)
    assert stale_row is not None
    assert stale_row["status"] == "error"
    assert stale_row["error"] == "interrupted_by_restart"
    assert stale_row["next_retry_at"] is None
    assert stale_row["attempts"] == 0

    fresh_row = dbm.get_inbox(conn, fresh_id)
    assert fresh_row is not None
    assert fresh_row["status"] == "received"
    assert fresh_row["error"] is None

    done_row = dbm.get_inbox(conn, done_id)
    assert done_row is not None
    assert done_row["status"] == "done"

    staged_row = dbm.get_inbox(conn, staged_id)
    assert staged_row is not None
    assert staged_row["status"] == "staged"

    due_ids = [r["id"] for r in dbm.due_for_recovery(conn, max_attempts=5, now=now)]
    assert due_ids == [stale_id]

    failure_ids = [r["id"] for r in dbm.inbox_failures(conn, limit=10)]
    assert failure_ids == [stale_id]


def test_requeue_stale_received_skips_active_payloads() -> None:
    conn = _mem_db()
    now = 20_000.0
    stale_before = now - 1800.0

    active_url_id = dbm.log_inbox(conn, source="telegram", payload="https://example.com/long-stt", kind="url")
    active_file_id = dbm.log_inbox(conn, source="telegram", payload="/tmp/original.pdf", file_ref="/app/data/raw/files/1_paper.pdf", kind="file")
    active_multiline_id = dbm.log_inbox(conn, source="cli", payload="Line one\n  Line two", kind="text")
    active_composite_id = dbm.log_inbox(
        conn,
        source="telegram",
        payload=json.dumps({"url": "https://example.com/composite-video", "attachment_name": "slides.pdf"}),
        file_ref="/app/data/raw/files/2_slides.pdf",
        kind="composite",
    )
    abandoned_id = dbm.log_inbox(conn, source="telegram", payload="https://example.com/abandoned", kind="url")

    for row_id in (active_url_id, active_file_id, active_multiline_id, active_composite_id, abandoned_id):
        conn.execute("UPDATE raw_inbox SET received_at=? WHERE id=?", (now - 3600.0, row_id))
    conn.commit()

    active_payloads = [
        "https://example.com/long-stt",
        "/app/data/raw/files/1_paper.pdf",
        "Line one Line two",
        "https://example.com/composite-video",
    ]
    requeued = dbm.requeue_stale_received_inbox(conn, stale_before=stale_before, active_payloads=active_payloads)
    assert requeued == [abandoned_id]

    for row_id in (active_url_id, active_file_id, active_multiline_id, active_composite_id):
        row = dbm.get_inbox(conn, row_id)
        assert row is not None
        assert row["status"] == "received"

    abandoned_row = dbm.get_inbox(conn, abandoned_id)
    assert abandoned_row is not None
    assert abandoned_row["status"] == "error"
    assert abandoned_row["error"] == "interrupted_by_restart"


def test_requeue_stale_received_kb_id_isolation() -> None:
    conn = _mem_db()
    now = 30_000.0
    conn.execute("INSERT INTO knowledge_bases(id, seq, label, created_at, updated_at) VALUES (2, 2, 'KB 2', ?, ?)", (now, now))
    conn.commit()

    kb0_id = dbm.log_inbox(conn, source="telegram", payload="https://example.com/kb0", kind="url", kb_id=0)
    kb2_id = dbm.log_inbox(conn, source="telegram", payload="https://example.com/kb2", kind="url", kb_id=2)
    conn.execute("UPDATE raw_inbox SET received_at=? WHERE id IN (?, ?)", (now - 3600.0, kb0_id, kb2_id))
    conn.commit()

    requeued_kb2 = dbm.requeue_stale_received_inbox(conn, stale_before=now - 1800.0, kb_id=2)
    assert requeued_kb2 == [kb2_id]

    kb0_row = dbm.get_inbox(conn, kb0_id, kb_id=0)
    kb2_row = dbm.get_inbox(conn, kb2_id, kb_id=2)
    assert kb0_row is not None and kb0_row["status"] == "received"
    assert kb2_row is not None and kb2_row["status"] == "error" and kb2_row["error"] == "interrupted_by_restart"


# --- Integration & E2E tests: IngestService.recover_failed ---


def test_recover_failed_skips_fresh_and_active_received_rows(monkeypatch, tmp_path: Path) -> None:
    s = _make_settings(monkeypatch, tmp_path)
    svc = IngestService(s, provider=MockProvider())

    conn = dbm.connect(s.db_file)
    dbm.init_db(conn)
    now = time.time()
    fresh_id = dbm.log_inbox(conn, source="telegram", payload="https://example.com/fresh-still-running", kind="url")
    active_id = dbm.log_inbox(conn, source="telegram", payload="https://example.com/active-long-stt", kind="url")
    conn.execute("UPDATE raw_inbox SET received_at=? WHERE id=?", (now - 60.0, fresh_id))
    conn.execute("UPDATE raw_inbox SET received_at=? WHERE id=?", (now - 7200.0, active_id))
    conn.commit()
    conn.close()

    fetch_calls: list[str] = []

    def _fail_if_called(payload: str, **_kwargs):
        fetch_calls.append(payload)
        raise AssertionError(f"fetch must not run for fresh or active payload: {payload}")

    _patch_fetch(monkeypatch, _fail_if_called)

    with active_job(s.data_dir, kind="ingest", payload="https://example.com/active-long-stt", kb_id=0, source="telegram"):
        results = svc.recover_failed(stale_received_sec=1800.0)

    assert results == []
    assert fetch_calls == []

    conn2 = dbm.connect(s.db_file)
    try:
        fresh_row = dbm.get_inbox(conn2, fresh_id)
        active_row = dbm.get_inbox(conn2, active_id)
        assert fresh_row is not None and fresh_row["status"] == "received"
        assert active_row is not None and active_row["status"] == "received"
    finally:
        conn2.close()


def test_e2e_crashed_ingest_stale_received_recovers_to_done(monkeypatch, tmp_path: Path) -> None:
    """E2E: a crashed ingest leaves a stale raw_inbox row in status='received'; recover_failed transitions it, re-ingests with MockProvider, creates the document, and marks the row done."""
    s = _make_settings(monkeypatch, tmp_path)
    svc = IngestService(s, provider=MockProvider())
    crashed_url = "https://github.com/example-org/claire-recovery-demo"

    # Simulate a container SIGKILL mid-ingest: raw_inbox stays in status='received' with an old received_at, and active_jobs has an expired file from before the restart.
    conn = dbm.connect(s.db_file)
    dbm.init_db(conn)
    crashed_inbox_id = dbm.log_inbox(conn, source="telegram", payload=crashed_url, kind="url")
    stale_ts = time.time() - 3600.0
    conn.execute("UPDATE raw_inbox SET received_at=? WHERE id=?", (stale_ts, crashed_inbox_id))
    conn.commit()
    assert dbm.due_for_recovery(conn, max_attempts=5) == []
    assert dbm.inbox_failures(conn, limit=10) == []
    assert dbm.counts(conn)["documents"] == 0
    conn.close()

    jobs_dir = s.data_dir / "active_jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)
    (jobs_dir / "j_crashed.json").write_text(
        json.dumps(
            {
                "active": True,
                "job_id": "j_crashed",
                "kind": "ingest",
                "payload": crashed_url,
                "kb_id": 0,
                "started_at": stale_ts,
                "updated_at": stale_ts,
            }
        ),
        encoding="utf-8",
    )

    doc = Document(
        url=crashed_url,
        title="Claire Recovery Demo",
        raw_text="A knowledge graph recovery tool that resumes interrupted video and PDF ingests cleanly. " * 10,
        source_type="web",
        content_hash="hash-crashed-recovery-1",
    )
    _patch_fetch(monkeypatch, lambda _payload: doc)

    results = svc.recover_failed(max_attempts=5, base_delay=300.0, stale_received_sec=1800.0)
    assert results == [{"inbox_id": crashed_inbox_id, "status": "done"}]

    conn2 = dbm.connect(s.db_file)
    try:
        row = dbm.get_inbox(conn2, crashed_inbox_id)
        assert row is not None
        assert row["status"] == "done"
        assert row["document_id"] is not None
        assert row["error"] is None

        stored_doc = dbm.get_document(conn2, row["document_id"])
        assert stored_doc is not None
        assert stored_doc.title == "Claire Recovery Demo"
        assert dbm.counts(conn2)["documents"] == 1
        assert dbm.counts(conn2)["entities"] >= 1
    finally:
        conn2.close()

    # Running recover_failed again must be idempotent.
    assert svc.recover_failed(max_attempts=5, base_delay=300.0, stale_received_sec=1800.0) == []


def test_e2e_crashed_ingest_exposes_failure_when_retry_fails(monkeypatch, tmp_path: Path) -> None:
    """When recovery of a stale received row fails during fetch, the row stays exposed in list_failures with backoff."""
    s = _make_settings(monkeypatch, tmp_path)
    svc = IngestService(s, provider=MockProvider())
    crashed_url = "https://example.com/still-down"

    conn = dbm.connect(s.db_file)
    dbm.init_db(conn)
    crashed_inbox_id = dbm.log_inbox(conn, source="telegram", payload=crashed_url, kind="url")
    conn.execute("UPDATE raw_inbox SET received_at=? WHERE id=?", (time.time() - 3600.0, crashed_inbox_id))
    conn.commit()
    conn.close()

    def _boom(_payload: str):
        raise RuntimeError("upstream unavailable")

    _patch_fetch(monkeypatch, _boom)

    results = svc.recover_failed(max_attempts=5, base_delay=300.0, stale_received_sec=1800.0)
    assert len(results) == 1
    assert results[0]["inbox_id"] == crashed_inbox_id
    assert results[0]["status"] == "error"

    failures = svc.list_failures(limit=10)
    assert len(failures) == 1
    assert failures[0]["id"] == crashed_inbox_id
    assert failures[0]["status"] == "error"
    assert failures[0]["attempts"] == 1
