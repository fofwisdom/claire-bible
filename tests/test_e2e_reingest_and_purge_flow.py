"""End-to-End (E2E) test for reingest and purge workflow across CLI and API."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from starlette.testclient import TestClient

from claire.api.server import create_app
from claire.cli import main
from claire.config import Settings, get_settings
from claire.extract.provider import MockProvider
from claire.ingest.normalize import content_hash
from claire.ingest.pipeline import ingest
from claire.ontology.base import Document
from claire.store import db as dbm
from claire.store.vectors import make_vector_store


OWNER_TOKEN = "owner-" + ("o" * 32)
OWNER_HEADERS = {"Authorization": f"Bearer {OWNER_TOKEN}"}


def test_e2e_reingest_and_purge_lifecycle(tmp_path: Path, monkeypatch):
    """E2E test verifying complete document lifecycle: ingest -> purge -> tombstone block -> untombstone reingest -> API purge -> reingest stream."""
    db_file = tmp_path / "claire_e2e_flow.db"
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

    test_url = "https://example.com/e2e-article"
    initial_text = "Claire Bible autonomous knowledge graph test article content."
    initial_doc = Document(
        url=test_url,
        canonical_url=test_url,
        title="E2E Article",
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

    # Step 2: CLI Purge with Tombstone
    rc_purge = main(["purge", doc_id, "--apply", "--yes"])
    assert rc_purge == 0

    conn = dbm.connect(db_file)
    assert dbm.get_document(conn, doc_id) is None
    assert dbm.is_tombstoned(conn, url=test_url) is True
    conn.close()

    # Step 3: Reingest without untombstone must fail due to tombstone
    rc_blocked = main(["reingest", test_url, "--apply"])
    assert rc_blocked == 1

    # Step 4: Reingest with --untombstone --apply lifts tombstone and successfully re-ingests
    reingest_text = "Reingested version of the article with fresh content and entities."
    reingest_doc = Document(
        url=test_url,
        canonical_url=test_url,
        title="E2E Article (Reingested)",
        raw_text=reingest_text,
        source_type="web",
        content_hash=content_hash(reingest_text),
    )
    with patch("claire.ingest.pipeline.default_fetch", return_value=reingest_doc), \
         patch("claire.ingest.service.default_fetch", return_value=reingest_doc):
        rc_reing = main(["reingest", test_url, "--untombstone", "--apply", "--yes", "--full-content"])
        assert rc_reing == 0

    conn = dbm.connect(db_file)
    reingested_row = dbm.find_document_by_canonical_url(conn, test_url)
    assert reingested_row is not None
    reingested_doc = dbm.get_document(conn, reingested_row)
    assert reingested_doc is not None
    assert reingested_doc.title == "E2E Article (Reingested)"
    conn.close()

    # Step 5: WebUI API endpoint /document/purge (Dry-run and Apply)
    app = create_app(settings)
    client = TestClient(app, base_url=settings.public_url)

    res_dry = client.post("/document/purge", json={"target": reingested_row, "dry_run": True}, headers=OWNER_HEADERS)
    assert res_dry.status_code == 200
    assert res_dry.json()["dry_run"] is True

    res_del = client.post("/document/purge", json={"target": reingested_row, "dry_run": False, "tombstone": False}, headers=OWNER_HEADERS)
    assert res_del.status_code == 200
    assert res_del.json()["deleted_documents"] == 1

    # Step 6: WebUI API endpoint /reingest-stream
    with patch("claire.ingest.pipeline.default_fetch", return_value=reingest_doc):
        res_stream = client.post(
            "/reingest-stream",
            json={"target": test_url, "full_content": True},
            headers=OWNER_HEADERS,
        )
        assert res_stream.status_code == 200
        lines = [line for line in res_stream.text.split("\n") if line.strip()]
        assert len(lines) >= 1
        last_event = json.loads(lines[-1])
        assert last_event.get("done") is True
        assert last_event["result"].get("document_id") is not None
