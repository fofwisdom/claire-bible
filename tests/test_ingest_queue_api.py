"""Tests for the account ingest queue routes (/api/me/ingest-queue*)."""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from starlette.testclient import TestClient

from claire.api.accounts_routes import ingest_queue
from claire.api.server import create_app
from claire.core.config import Settings
from claire.ingest.active import _MAX_EXPENSIVE_JOBS
from claire.store import accounts as accounts_store
from claire.store import db as dbm
from claire.store import kb_collab
from claire.store.knowledge_base import KnowledgeBaseManager
from claire.store.secrets import reset_secret_service
from tests.auth_helpers import ensure_test_account, issue_oauth_token, issue_session_token

PUBLIC_URL = "http://127.0.0.1:8765"


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch):
    for key in list(os.environ):
        if key.startswith("CLAIRE_"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("CLAIRE_SECRET_KEY", "44" * 32)
    reset_secret_service()
    yield
    reset_secret_service()


def _make_world(tmp_path: Path) -> SimpleNamespace:
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir(parents=True, exist_ok=True)
    settings = Settings(
        _env_file=None,
        CLAIRE_ENVIRONMENT="development",
        CLAIRE_FQDN=PUBLIC_URL,
        CLAIRE_DB_PATH=str(data_dir / "claire.db"),
        CLAIRE_PLUGINS_DIR=str(plugins_dir),
        CLAIRE_CORS_ALLOWED_ORIGINS="",
        CLAIRE_DATA_LIFECYCLE="purgeable",
        CLAIRE_PROVIDER="mock",
    )
    app = create_app(settings, SimpleNamespace(provider=SimpleNamespace(name="mock", embed_model="mock-embed")))
    conn = dbm.connect_existing(settings.db_file)
    try:
        accounts_store.AccountStore(conn).complete_setup(1, username="adminuser", display_name="Admin")
        accounts_store.AccountStore(conn).set_setting("mode", "network", updated_by=1)
    finally:
        conn.close()
    alice_id = ensure_test_account(settings, account_id=2, username="alice", role="member")
    bob_id = ensure_test_account(settings, account_id=3, username="bob", role="member")
    mgr = KnowledgeBaseManager(settings)
    alice_kb = mgr.define_kb("Alice KB", is_personal=True, is_public=False, account_id=alice_id)
    bob_kb = mgr.define_kb("Bob KB", is_personal=True, is_public=False, account_id=bob_id)
    shared_active_kb = mgr.define_kb("Shared Active", is_personal=False, is_public=True, account_id=1, knowledge_type="real")
    shared_pending_kb = mgr.define_kb("Shared Pending", is_personal=False, is_public=True, account_id=1, knowledge_type="real")
    conn = dbm.connect_existing(settings.db_file)
    try:
        kb_collab.add_membership(conn, kb_id=shared_active_kb.id, account_id=alice_id, role="collaborator", state="active")
        kb_collab.add_membership(conn, kb_id=shared_pending_kb.id, account_id=alice_id, role="collaborator", state="pending")
        conn.commit()
    finally:
        conn.close()
    return SimpleNamespace(
        settings=settings,
        app=app,
        data_dir=data_dir,
        db_file=settings.db_file,
        alice_id=alice_id,
        bob_id=bob_id,
        admin_kb_id=0,
        alice_kb_id=alice_kb.id,
        bob_kb_id=bob_kb.id,
        shared_active_kb_id=shared_active_kb.id,
        shared_pending_kb_id=shared_pending_kb.id,
        admin_token=issue_session_token(settings, account_id=1, scope="owner"),
        alice_token=issue_session_token(settings, account_id=alice_id, scope="owner"),
        bob_token=issue_session_token(settings, account_id=bob_id, scope="owner"),
        alice_write=issue_oauth_token(settings, account_id=alice_id, scope="write", client_id="iq-write"),
    )


def _session_headers(token: str) -> dict[str, str]:
    return {"Cookie": f"claire_session={token}", "Origin": PUBLIC_URL}


def _write_job_file(data_dir: Path, job_id: str, data: dict[str, Any]) -> None:
    jobs_dir = data_dir / "active_jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)
    (jobs_dir / f"{job_id}.json").write_text(json.dumps(data), encoding="utf-8")


def _insert_inbox(
    conn,
    *,
    kb_id: int,
    received_at: float,
    source: str = "web",
    kind: str = "url",
    payload: str = "",
    file_name: str | None = None,
    file_ref: str | None = None,
    document_id: str | None = None,
    status: str = "received",
    error: str | None = None,
    last_attempt: float | None = None,
    extraction_mode: str | None = None,
) -> int:
    cur = conn.execute(
        "INSERT INTO raw_inbox (kb_id, received_at, source, kind, payload, file_name, file_ref, document_id, status, error, last_attempt, extraction_mode) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (kb_id, received_at, source, kind, payload, file_name, file_ref, document_id, status, error, last_attempt, extraction_mode),
    )
    return int(cur.lastrowid)


def _insert_doc(conn, *, doc_id: str, kb_id: int, title: str, extraction_status: str = "extracted") -> None:
    conn.execute(
        "INSERT INTO documents (id, kb_id, url, title, fetched_at, raw_text, source_type, extraction_status) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (doc_id, kb_id, f"https://example.com/{doc_id}", title, time.time(), "Sample raw text", "article", extraction_status),
    )


def test_ingest_queue_auth_visibility_and_scrubbing(tmp_path: Path) -> None:
    world = _make_world(tmp_path)
    now = time.time()
    conn = dbm.connect_existing(world.db_file)
    try:
        _insert_doc(conn, doc_id="doc-alice-done", kb_id=world.alice_kb_id, title="Alice Resolved Title")
        _insert_doc(conn, doc_id="doc-bob-done", kb_id=world.bob_kb_id, title="Bob Secret Doc")
        _insert_inbox(conn, kb_id=world.alice_kb_id, received_at=now - 50, payload="https://example.com/running", status="received")
        _insert_inbox(conn, kb_id=world.alice_kb_id, received_at=now - 30, payload="https://example.com/q2", status="received")
        _insert_inbox(conn, kb_id=world.alice_kb_id, received_at=now - 40, payload="https://example.com/q1?api_key=supersecret123", status="received")
        _insert_inbox(conn, kb_id=world.alice_kb_id, received_at=now - 25, payload="https://example.com/staged", status="staged", extraction_mode="client")
        _insert_inbox(conn, kb_id=world.alice_kb_id, received_at=now - 20, payload="https://example.com/err", status="error", error="upstream failed sk-testsecret1234567890", last_attempt=now - 10)
        _insert_inbox(conn, kb_id=world.alice_kb_id, received_at=now - 15, payload="https://example.com/done", document_id="doc-alice-done", status="done", last_attempt=now - 5)
        _insert_inbox(conn, kb_id=world.shared_active_kb_id, received_at=now - 18, payload="https://example.com/shared-err", status="failed", error="shared error", last_attempt=now - 8)
        _insert_inbox(conn, kb_id=world.shared_pending_kb_id, received_at=now - 17, payload="https://example.com/pending-kb", status="received")
        _insert_inbox(conn, kb_id=world.bob_kb_id, received_at=now - 16, payload="https://example.com/bob-only", document_id="doc-bob-done", status="done", last_attempt=now - 4)
        conn.commit()
    finally:
        conn.close()

    _write_job_file(
        world.data_dir,
        "j_alice_run",
        {
            "active": True,
            "job_id": "j_alice_run",
            "kb_id": world.alice_kb_id,
            "payload": "https://example.com/running",
            "title": "Running Alice Job",
            "source": "web",
            "stage": "resolve",
            "msg": "Resolving entities",
            "started_at": now - 5,
            "updated_at": now - 1,
        },
    )
    _write_job_file(
        world.data_dir,
        "j_bob_run",
        {
            "active": True,
            "job_id": "j_bob_run",
            "kb_id": world.bob_kb_id,
            "payload": "https://example.com/bob-run",
            "title": "Running Bob Job",
            "source": "telegram",
            "stage": "extract",
            "msg": "Extracting Bob",
            "started_at": now - 6,
            "updated_at": now - 2,
        },
    )
    _write_job_file(
        world.data_dir,
        "j_alice_done_dup",
        {
            "active": False,
            "job_id": "j_alice_done_dup",
            "kb_id": world.alice_kb_id,
            "payload": "https://example.com/done",
            "doc_id": "doc-alice-done",
            "title": "Alice Resolved Title",
            "source": "web",
            "stage": "done",
            "msg": "Done",
            "started_at": now - 8,
            "updated_at": now - 4,
            "completed_at": now - 4,
        },
    )
    _write_job_file(
        world.data_dir,
        "j_alice_recent_job",
        {
            "active": False,
            "job_id": "j_alice_recent_job",
            "kb_id": world.alice_kb_id,
            "payload": "https://example.com/recent-memory-job",
            "title": "Recent Completed Job",
            "source": "web",
            "stage": "done",
            "msg": "Completed",
            "started_at": now - 7,
            "updated_at": now - 3,
            "completed_at": now - 3,
        },
    )

    with TestClient(world.app, base_url=PUBLIC_URL, follow_redirects=False) as client:
        # Anonymous and non-owner credentials must be rejected.
        assert client.get("/api/me/ingest-queue").status_code == 404
        assert client.get("/api/me/ingest-queue", headers={"Authorization": f"Bearer {world.alice_write}"}).status_code == 404
        head_resp = client.head("/api/me/ingest-queue", headers=_session_headers(world.alice_token))
        assert head_resp.status_code == 200
        assert head_resp.headers["cache-control"] == "no-store"

        # Member Alice sees only her KB + active shared KB.
        resp = client.get("/api/me/ingest-queue", headers=_session_headers(world.alice_token))
        assert resp.status_code == 200
        assert resp.headers["cache-control"] == "no-store"
        body = resp.json()
        assert body["max_slots"] == _MAX_EXPENSIVE_JOBS == 4
        assert body["busy_slots"] == 1
        assert body["counts"] == {"running": 1, "queued": 2, "staged": 1, "error": 2, "done": 2}
        assert isinstance(body["server_time"], float)

        items = body["items"]
        running = [it for it in items if it["status"] == "running"]
        assert len(running) == 1
        assert running[0]["id"] == "j_alice_run"
        assert running[0]["stage"] == "resolve"
        assert running[0]["stage_index"] == 2
        assert running[0]["order"] is None

        queued = [it for it in items if it["status"] == "queued"]
        assert [it["order"] for it in queued] == [1, 2]
        assert "[REDACTED]" in queued[0]["title"]
        assert "supersecret123" not in queued[0]["title"]
        assert queued[1]["title"] == "https://example.com/q2"

        staged = [it for it in items if it["status"] == "staged"]
        assert len(staged) == 1
        assert staged[0]["mode"] == "client"
        assert staged[0]["order"] is None

        errors = [it for it in items if it["status"] == "error"]
        assert len(errors) == 2
        alice_err = next(it for it in errors if it["kb_id"] == world.alice_kb_id)
        shared_err = next(it for it in errors if it["kb_id"] == world.shared_active_kb_id)
        assert "sk-testsecret" not in alice_err["msg"]
        assert "[REDACTED]" in alice_err["msg"]
        assert alice_err["can_retry"] is True
        assert shared_err["can_retry"] is False

        done = [it for it in items if it["status"] == "done"]
        assert len(done) == 2
        alice_done = next(it for it in done if it["inbox_id"] is not None)
        assert alice_done["title"] == "Alice Resolved Title"
        assert alice_done["doc_url"].startswith("/silvercord/")
        assert alice_done["can_retry"] is False
        assert not any(it["kb_id"] in (world.bob_kb_id, world.shared_pending_kb_id) for it in items)

        # Admin sees all KBs server-wide.
        admin_resp = client.get("/api/me/ingest-queue", headers=_session_headers(world.admin_token))
        assert admin_resp.status_code == 200
        admin_body = admin_resp.json()
        assert admin_body["busy_slots"] == 2
        admin_kb_ids = {it["kb_id"] for it in admin_body["items"]}
        assert {world.alice_kb_id, world.bob_kb_id, world.shared_active_kb_id, world.shared_pending_kb_id}.issubset(admin_kb_ids)
        admin_shared_err = next(it for it in admin_body["items"] if it["kb_id"] == world.shared_active_kb_id and it["status"] == "error")
        assert admin_shared_err["can_retry"] is True


def test_ingest_queue_history_cap_and_stage_mapping(tmp_path: Path) -> None:
    world = _make_world(tmp_path)
    now = time.time()
    conn = dbm.connect_existing(world.db_file)
    try:
        for i in range(15):
            _insert_inbox(conn, kb_id=world.alice_kb_id, received_at=now - 100 + i, payload=f"https://example.com/err-{i}", status="error", error=f"err {i}", last_attempt=now - 50 + i)
            _insert_inbox(conn, kb_id=world.alice_kb_id, received_at=now - 100 + i, payload=f"https://example.com/done-{i}", status="done", last_attempt=now - 50 + i)
        conn.commit()
    finally:
        conn.close()

    for idx, stage in enumerate(("init", "extract", "detail", "done")):
        _write_job_file(
            world.data_dir,
            f"j_stage_{idx}",
            {
                "active": True,
                "job_id": f"j_stage_{idx}",
                "kb_id": world.alice_kb_id,
                "payload": f"https://example.com/run-{idx}",
                "title": f"Job {stage}",
                "stage": stage,
                "started_at": now - 10 + idx,
                "updated_at": now - 5 + idx,
            },
        )

    with TestClient(world.app, base_url=PUBLIC_URL, follow_redirects=False) as client:
        resp = client.get("/api/me/ingest-queue", headers=_session_headers(world.alice_token))
        assert resp.status_code == 200
        body = resp.json()
        assert body["counts"]["error"] == 12
        assert body["counts"]["done"] == 12
        stage_indices = {it["stage"]: it["stage_index"] for it in body["items"] if it["status"] == "running"}
        assert stage_indices == {"init": 0, "extract": 1, "detail": 3, "done": 4}


def test_ingest_queue_retry_auth_conflict_and_background_dispatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    world = _make_world(tmp_path)
    now = time.time()
    conn = dbm.connect_existing(world.db_file)
    try:
        alice_err_id = _insert_inbox(conn, kb_id=world.alice_kb_id, received_at=now - 30, payload="https://example.com/retry-me", status="error", error="boom")
        alice_done_id = _insert_inbox(conn, kb_id=world.alice_kb_id, received_at=now - 20, payload="https://example.com/done-me", status="done")
        bob_err_id = _insert_inbox(conn, kb_id=world.bob_kb_id, received_at=now - 10, payload="https://example.com/bob-err", status="failed", error="bob boom")
        conn.commit()
    finally:
        conn.close()

    called = threading.Event()
    calls: list[tuple[int, int]] = []

    class _StubService:
        def __init__(self, settings: Any, *, kb_id: int) -> None:
            self.kb_id = kb_id

        def retry_inbox(self, inbox_id: int) -> None:
            calls.append((self.kb_id, inbox_id))
            called.set()

    monkeypatch.setattr(ingest_queue, "IngestService", _StubService)

    with TestClient(world.app, base_url=PUBLIC_URL, follow_redirects=False) as client:
        # Anonymous, bad id, and cross-account retry all hide existence with 404.
        assert client.post(f"/api/me/ingest-queue/{alice_err_id}/retry").status_code == 404
        assert client.post("/api/me/ingest-queue/notanint/retry", headers=_session_headers(world.alice_token)).status_code == 404
        assert client.post("/api/me/ingest-queue/999999/retry", headers=_session_headers(world.alice_token)).status_code == 404
        assert client.post(f"/api/me/ingest-queue/{bob_err_id}/retry", headers=_session_headers(world.alice_token)).status_code == 404

        # Non-error/failed row returns 409.
        conflict = client.post(f"/api/me/ingest-queue/{alice_done_id}/retry", headers=_session_headers(world.alice_token))
        assert conflict.status_code == 409
        assert conflict.headers["cache-control"] == "no-store"

        # Valid retry updates DB immediately to received and returns 202.
        ok = client.post(f"/api/me/ingest-queue/{alice_err_id}/retry", headers=_session_headers(world.alice_token))
        assert ok.status_code == 202
        assert ok.headers["cache-control"] == "no-store"
        assert ok.json() == {"ok": True, "inbox_id": alice_err_id, "status": "queued"}

        assert called.wait(timeout=3.0)
        assert calls == [(world.alice_kb_id, alice_err_id)]

    conn = dbm.connect_existing(world.db_file, readonly=True)
    try:
        row = dbm.get_inbox(conn, alice_err_id)
        assert row is not None
        assert row["status"] == "received"
        assert row["error"] is None
        assert row["next_retry_at"] is None
    finally:
        conn.close()


def test_ingest_queue_retry_records_error_when_background_task_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    world = _make_world(tmp_path)
    now = time.time()
    conn = dbm.connect_existing(world.db_file)
    try:
        inbox_id = _insert_inbox(conn, kb_id=world.alice_kb_id, received_at=now - 10, payload="https://example.com/fail-bg", status="failed", error="old error")
        conn.commit()
    finally:
        conn.close()

    failed = threading.Event()

    class _FailingService:
        def __init__(self, settings: Any, *, kb_id: int) -> None:
            self.kb_id = kb_id

        def retry_inbox(self, target_id: int) -> None:
            failed.set()
            raise RuntimeError("background crash sk-secretkey9876543210")

    monkeypatch.setattr(ingest_queue, "IngestService", _FailingService)

    with TestClient(world.app, base_url=PUBLIC_URL, follow_redirects=False) as client:
        resp = client.post(f"/api/me/ingest-queue/{inbox_id}/retry", headers=_session_headers(world.alice_token))
        assert resp.status_code == 202
        assert failed.wait(timeout=3.0)
        deadline = time.monotonic() + 3.0
        status_now = "received"
        err_now = None
        while time.monotonic() < deadline:
            conn = dbm.connect_existing(world.db_file, readonly=True)
            try:
                row = dbm.get_inbox(conn, inbox_id)
                status_now = str(row["status"])
                err_now = row["error"]
            finally:
                conn.close()
            if status_now == "error":
                break
            time.sleep(0.05)
        assert status_now == "error"
        assert err_now is not None
        assert "sk-secretkey" not in err_now
        assert "[REDACTED]" in err_now
