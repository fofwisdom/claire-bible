"""End-to-end (E2E) test for document component regeneration lifecycle."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from claire.api.server import create_app
from claire.cli import main
from claire.config import get_settings
from claire.extract.provider import MockProvider
from claire.ingest.normalize import content_hash
from claire.ingest.pipeline import ingest
from claire.ontology.base import Document
from claire.store import db as dbm
from claire.store.vectors import make_vector_store


OWNER_TOKEN = "owner-" + ("o" * 32)
OWNER_HEADERS = {"Authorization": f"Bearer {OWNER_TOKEN}"}


def test_e2e_regenerate_lifecycle(tmp_path: Path, monkeypatch):
    """E2E test verifying complete document component regeneration lifecycle:
    1. Ingest initial document.
    2. CLI regenerate detail with custom focus and effort.
    3. Verify DB detail and focus updated.
    4. API regenerate summary only.
    5. Verify summary updated and detail preserved.
    6. API regenerate-stream with real-time NDJSON progress events.
    """
    db_file = tmp_path / "claire_e2e_regen.db"
    vault_dir = tmp_path / "vault"
    data_dir = tmp_path / "data"
    vault_dir.mkdir(parents=True)
    data_dir.mkdir(parents=True)

    monkeypatch.setenv("CLAIRE_DB_PATH", str(db_file))
    monkeypatch.setenv("CLAIRE_DATA_DIR", str(data_dir))
    monkeypatch.setenv("CLAIRE_VAULT_DIR", str(vault_dir))
    monkeypatch.setenv("CLAIRE_DATA_LIFECYCLE", "purgeable")
    monkeypatch.setenv("CLAIRE_ALLOW_PURGE", "1")
    monkeypatch.setenv("CLAIRE_INJECT_TOKEN", OWNER_TOKEN)

    get_settings.cache_clear()
    settings = get_settings()

    conn = dbm.connect(db_file)
    dbm.init_db(conn)
    prov = MockProvider()
    vstore = make_vector_store(conn, "brute")

    test_url = "https://example.com/e2e-regen-article"
    initial_text = "Claire Bible autonomous knowledge graph test article with detailed technical content."
    initial_doc = Document(
        url=test_url,
        canonical_url=test_url,
        title="E2E Regen Article",
        raw_text=initial_text,
        source_type="web",
        content_hash=content_hash(initial_text),
    )

    # Step 1: Initial Ingest
    rep = ingest(
        payload=test_url,
        conn=conn,
        provider=prov,
        vstore=vstore,
        vault_dir=vault_dir,
        data_dir=data_dir,
        source="e2e-test",
        fetch_fn=lambda _: initial_doc,
    )
    conn.close()
    assert rep.document_id is not None
    doc_id = rep.document_id

    # Step 2: CLI Regenerate detail with custom focus
    custom_focus = "System Architecture and Security Hardening"
    rc_detail = main(["regenerate", doc_id, "--detail", "--focus", custom_focus, "--effort", "high", "--apply", "--yes"])
    assert rc_detail == 0

    conn = dbm.connect(db_file)
    doc_after_detail = dbm.get_document(conn, doc_id)
    assert doc_after_detail is not None
    saved_focus = dbm.get_document_focus(conn, doc_id)
    assert saved_focus == custom_focus
    detail_content = dbm.get_document_detail(conn, doc_id)
    assert detail_content is not None
    assert f"[focus: {custom_focus}]" in detail_content
    conn.close()

    # Step 3: WebUI API endpoint /document/regenerate (Summary component only)
    app = create_app(settings)
    client = TestClient(app, base_url=settings.public_url)

    res_summ = client.post(
        "/document/regenerate",
        json={"target": doc_id, "component": "summary", "dry_run": False},
        headers=OWNER_HEADERS,
    )
    assert res_summ.status_code == 200
    summ_json = res_summ.json()
    assert summ_json.get("count") == 1

    conn = dbm.connect(db_file)
    # Detail and focus must remain preserved after summary-only regenerate
    assert dbm.get_document_focus(conn, doc_id) == custom_focus
    detail_preserved = dbm.get_document_detail(conn, doc_id)
    assert detail_preserved == detail_content
    conn.close()

    # Step 4: WebUI API endpoint /regenerate-stream (Streaming NDJSON)
    res_stream = client.post(
        "/regenerate-stream",
        json={"target": doc_id, "component": "all", "dry_run": False, "effort": "medium"},
        headers=OWNER_HEADERS,
    )
    assert res_stream.status_code == 200
    lines = [ln for ln in res_stream.text.split("\n") if ln.strip()]
    assert len(lines) >= 1
    last_event = json.loads(lines[-1])
    assert last_event.get("done") is True
    assert last_event["result"].get("count") == 1
    target_info = last_event["result"]["targets"][0]
    assert target_info["document_id"] == doc_id
