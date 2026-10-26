"""Unit and End-to-End tests for multi-slot active jobs engine and API.

Test areas:
1. File I/O operations: active_jobs storage and active_ingest synchronization.
2. Interface contracts: active_job context manager and reentrancy protection.
3. Multi-slot concurrent job tracking and stale/retention cleanup.
4. IngestService regenerate_components integration (dry-run vs force).
5. End-to-end API test for /stats and /ingest/active across multiple themes.
"""

from __future__ import annotations

import json
import secrets
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

from starlette.testclient import TestClient

from claire.api import server
from claire.config import Settings
from claire.extract.provider import MockProvider
from claire.ingest.active import (
    _COMPLETED_RETENTION_SEC,
    _MAX_EXPENSIVE_JOBS,
    _STALE_TIMEOUT_SEC,
    active_job,
    clear_active_ingest,
    get_active_ingest,
    get_active_jobs,
    get_primary_active_job,
    purge_active_matrix,
    set_active_ingest,
    update_active_ingest,
)
from claire.ingest.service import IngestService
from claire.ontology.base import Document
from claire.store import db as dbm


def test_active_job_context_manager_io(tmp_path: Path):
    """Test active_job creates job file and syncs active_ingest.json."""
    data_dir = tmp_path / "data"

    assert get_active_jobs(data_dir) == []
    assert get_active_ingest(data_dir) is None

    with active_job(
        data_dir,
        kind="reingest",
        payload="https://example.com/item1",
        theme_id=1,
        theme_label="Network",
        doc_id="doc_net_1",
        title="Network Architecture Guide",
    ) as handle:
        assert handle.job_id.startswith("j_")
        job_file = data_dir / "active_jobs" / f"{handle.job_id}.json"
        assert job_file.exists()

        # Verify synchronized single-slot file
        sync_file = data_dir / "active_ingest.json"
        assert sync_file.exists()
        sync_data = json.loads(sync_file.read_text(encoding="utf-8"))
        assert sync_data["job_id"] == handle.job_id
        assert sync_data["active"] is True
        assert sync_data["kind"] == "reingest"
        assert sync_data["theme_id"] == 1
        assert sync_data["theme_label"] == "Network"
        assert sync_data["doc_id"] == "doc_net_1"
        assert sync_data["title"] == "Network Architecture Guide"

        # Update progress and matrix
        sample_matrix = {"rows": ["A"], "cols": ["B"], "matrix": [[0.8]]}
        handle.update(
            stage="heatmap_matrix",
            msg="Comparing entities...",
            heatmap_matrix=sample_matrix,
        )

        current = get_active_ingest(data_dir)
        assert current is not None
        assert current["stage"] == "heatmap_matrix"
        assert current["heatmap_matrix"] == sample_matrix

    # Context manager exited normally
    completed = get_active_ingest(data_dir)
    assert completed is not None
    assert completed["active"] is False
    assert completed["stage"] == "done"
    assert completed["completed_at"] is not None


def test_active_job_reentrancy_protection(tmp_path: Path):
    """Test reentrant calls in same thread do not overwrite outer job."""
    data_dir = tmp_path / "data"

    with active_job(
        data_dir,
        kind="reingest",
        payload="https://example.com/parent",
        theme_id=2,
        theme_label="Security",
        doc_id="doc_parent",
        title="Parent Document",
    ) as outer_handle:
        outer_id = outer_handle.job_id

        # Inner call simulates nested pipeline.ingest
        with active_job(
            data_dir,
            kind="ingest",
            payload="https://example.com/child",
            theme_id=0,
            theme_label="Default",
            title="Child Document",
        ) as inner_handle:
            # Must delegate to outer handle
            assert inner_handle.job_id == outer_id
            assert inner_handle.is_reentrant is True
            inner_handle.update(msg="Inner stage running", stage="extract")

            cur = get_active_ingest(data_dir)
            assert cur is not None
            assert cur["job_id"] == outer_id
            assert cur["kind"] == "reingest"
            assert cur["theme_id"] == 2
            assert cur["theme_label"] == "Security"
            assert cur["msg"] == "Inner stage running"

        # Inner exit must not mark outer job as completed
        cur_after_inner = get_active_ingest(data_dir)
        assert cur_after_inner is not None
        assert cur_after_inner["active"] is True
        assert cur_after_inner["job_id"] == outer_id

    # Outer exit marks completed
    cur_final = get_active_ingest(data_dir)
    assert cur_final is not None
    assert cur_final["active"] is False
    assert cur_final["job_id"] == outer_id


def test_multi_slot_concurrent_jobs_and_cleanup(tmp_path: Path):
    """Test concurrent jobs produce multiple slots and clean up after timeout."""
    data_dir = tmp_path / "data"
    jobs_dir = data_dir / "active_jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)

    barrier = threading.Barrier(3)
    results: list[str] = []

    def worker(idx: int, theme_id: int, theme_name: str):
        with active_job(
            data_dir,
            kind="ingest",
            payload=f"https://example.com/task_{idx}",
            theme_id=theme_id,
            theme_label=theme_name,
            title=f"Task {idx}",
        ) as handle:
            results.append(handle.job_id)
            barrier.wait()
            # Wait inside context manager so all 3 are active concurrently
            time.sleep(0.05)

    threads = [
        threading.Thread(target=worker, args=(1, 0, "Default")),
        threading.Thread(target=worker, args=(2, 1, "Theme1")),
        threading.Thread(target=worker, args=(3, 2, "Theme2")),
    ]
    for t in threads:
        t.start()

    # Wait until all workers enter context manager
    time.sleep(0.02)
    active_list = get_active_jobs(data_dir)
    assert len(active_list) >= 1

    for t in threads:
        t.join()

    # All 3 completed and retained within 30 seconds
    all_retained = get_active_jobs(data_dir)
    assert len(all_retained) == 3

    # Theme selection test
    primary_theme1 = get_primary_active_job(data_dir, theme_id=1)
    assert primary_theme1 is not None
    assert primary_theme1["theme_id"] == 1

    # Stale cleanup test: simulate expired retention
    for jf in jobs_dir.glob("*.json"):
        data = json.loads(jf.read_text(encoding="utf-8"))
        data["completed_at"] = time.time() - (_COMPLETED_RETENTION_SEC + 10)
        jf.write_text(json.dumps(data), encoding="utf-8")

    assert get_active_jobs(data_dir) == []


def test_purge_active_matrix_multi_slot(tmp_path: Path):
    """Test purge_active_matrix clears heatmap_matrix across slot files."""
    data_dir = tmp_path / "data"

    with active_job(
        data_dir,
        kind="ingest",
        payload="https://example.com/docA",
        doc_id="doc_target",
        title="Document Target",
    ) as jobA:
        jobA.update(heatmap_matrix={"document_id": "doc_target", "matrix": [[1.0]]})

    assert get_active_ingest(data_dir)["heatmap_matrix"] is not None

    purge_active_matrix(data_dir, doc_id="doc_target")

    st = get_active_ingest(data_dir)
    assert st["heatmap_matrix"] is None
    jobs = get_active_jobs(data_dir)
    assert len(jobs) == 1
    assert jobs[0]["heatmap_matrix"] is None


def test_service_regenerate_components_dry_run_vs_force(tmp_path: Path):
    """Test IngestService regenerate_components records state only when force=True."""
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    db_file = data_dir / "claire.db"

    conn = dbm.connect(db_file)
    dbm.init_db(conn)

    doc_id = "doc_test_regen"
    doc = Document(
        id=doc_id,
        url="https://example.com/regen",
        canonical_url="https://example.com/regen",
        title="Regen Test Document",
        raw_text="Test content for regeneration.",
    )
    dbm.insert_document(conn, doc)
    dbm.log_extraction(
        conn,
        document_id=doc_id,
        provider="mock",
        model="mock",
        prompt_version="1",
        raw_response=json.dumps({"summary": "Initial summary"}),
    )
    conn.close()

    s = Settings(
        db_path=str(db_file),
        data_dir=str(data_dir),
    )
    provider = MockProvider()
    svc = IngestService(s, provider=provider, theme_id=0)

    # 1. Dry-run: force=False must not record active job
    res_dry = svc.regenerate_components(doc_id=doc_id, summary=True, force=False)
    assert res_dry["dry_run"] is True
    assert get_active_jobs(data_dir) == []
    assert get_active_ingest(data_dir) is None

    # 2. Force execution: force=True must record active job
    res_force = svc.regenerate_components(doc_id=doc_id, summary=True, force=True)
    assert res_force["dry_run"] is False
    assert res_force["count"] == 1

    # Active job must be retained as completed
    jobs = get_active_jobs(data_dir)
    assert len(jobs) == 1
    job = jobs[0]
    assert job["active"] is False
    assert job["stage"] == "done"
    assert job["kind"] == "regenerate"
    assert job["title"] == "Regen Test Document"


def test_e2e_api_stats_and_active_jobs_multi_theme(tmp_path: Path):
    """End-to-End test: concurrent jobs observed on /stats and /ingest/active across themes."""
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    db_file = data_dir / "claire.db"
    conn = dbm.connect(db_file)
    dbm.init_db(conn)
    conn.close()

    read_tok = secrets.token_urlsafe(32)
    s = Settings(
        db_path=str(db_file),
        data_dir=str(data_dir),
        environment="development",
        public_url="http://127.0.0.1:8765",
        readonly_token=read_tok,
    )

    app = server.create_app(s, MagicMock())
    client = TestClient(app, base_url="http://127.0.0.1:8765")
    auth_header = {"Authorization": f"Bearer {read_tok}"}

    # Step 1: Initial state - no active jobs
    r = client.get("/stats", headers=auth_header)
    assert r.status_code == 200
    st = r.json()
    assert st["ingesting"] is False
    assert st["active_ingest"] is None
    assert st["active_jobs"] == []

    # Step 2: Start Theme 0 job and Theme 1 job in concurrent threads
    ready_barrier = threading.Barrier(3)
    done_event = threading.Event()

    def run_job0():
        with active_job(
            data_dir,
            kind="ingest",
            payload="https://example.com/theme0",
            theme_id=0,
            theme_label="Default",
            title="Default Theme Job",
        ):
            ready_barrier.wait()
            done_event.wait()

    def run_job1():
        with active_job(
            data_dir,
            kind="reingest",
            payload="https://example.com/theme1",
            theme_id=1,
            theme_label="Network",
            title="Network Theme Job",
        ) as job1:
            job1.update(msg="Parsing network protocols...", stage="extract")
            ready_barrier.wait()
            done_event.wait()

    t0 = threading.Thread(target=run_job0)
    t1 = threading.Thread(target=run_job1)
    t0.start()
    t1.start()
    ready_barrier.wait()

    try:
        # GET /stats for Theme 0
        r0 = client.get("/stats?theme=0", headers=auth_header)
        assert r0.status_code == 200
        d0 = r0.json()
        assert d0["ingesting"] is True
        assert d0["active_ingest"]["theme_id"] == 0
        assert d0["active_ingest"]["title"] == "Default Theme Job"
        assert len(d0["active_jobs"]) == 2

        # GET /stats for Theme 1
        r1 = client.get("/stats?theme=1", headers=auth_header)
        assert r1.status_code == 200
        d1 = r1.json()
        assert d1["ingesting"] is True
        assert d1["active_ingest"]["theme_id"] == 1
        assert d1["active_ingest"]["title"] == "Network Theme Job"
        assert d1["active_ingest"]["stage"] == "extract"

        # GET /ingest/active for Theme 1
        ra1 = client.get("/ingest/active?theme=1", headers=auth_header)
        assert ra1.status_code == 200
        act1 = ra1.json()
        assert act1["active"] is True
        assert act1["theme_id"] == 1
        assert act1["stage"] == "extract"
        assert len(act1["active_jobs"]) == 2
    finally:
        done_event.set()
        t0.join()
        t1.join()

    # Step 3: After completion, verify both jobs retained in active_jobs
    r_done = client.get("/stats", headers=auth_header)
    assert r_done.status_code == 200
    d_done = r_done.json()
    assert d_done["ingesting"] is False
    assert len(d_done["active_jobs"]) == 2
