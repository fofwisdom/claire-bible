"""Unit tests for reingest CLI command, purge API endpoint, and reingest streaming."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from starlette.testclient import TestClient

from claire import cli
from claire.api import security, server
from claire.api.server import create_app
from claire.config import Settings
from claire.extract.provider import MockProvider
from claire.ingest.normalize import content_hash
from claire.ingest.pipeline import IngestReport, ingest
from claire.ontology.base import Document
from claire.store import db as dbm
from claire.store.vectors import make_vector_store


OWNER_TOKEN = "owner-" + ("o" * 32)
READONLY_TOKEN = "readonly-" + ("r" * 32)
OWNER_HEADERS = {"Authorization": f"Bearer {OWNER_TOKEN}"}
READONLY_HEADERS = {"Authorization": f"Bearer {READONLY_TOKEN}"}


def test_cli_reingest_parser_options():
    """Verify reingest parser defines all required options conforming to CLI standard."""
    parser = cli.build_parser()

    # Default dry-run
    args = parser.parse_args(["reingest", "doc_123"])
    assert args.target == "doc_123"
    assert args.apply is False
    assert args.dry_run is False
    assert args.untombstone is False
    assert args.full_content is False

    # --apply, --untombstone, --full-content, --effort, --focus
    args2 = parser.parse_args([
        "reingest", "https://example.com/test",
        "--apply",
        "--untombstone",
        "--full-content",
        "--effort", "high",
        "--focus", "architecture",
        "--yes",
        "--json",
    ])
    assert args2.target == "https://example.com/test"
    assert args2.apply is True
    assert args2.untombstone is True
    assert args2.full_content is True
    assert args2.effort == "high"
    assert args2.focus == "architecture"
    assert args2.yes is True
    assert args2.json is True


def test_cli_reingest_dry_run_and_apply(tmp_path: Path, monkeypatch, capsys):
    """Test CLI reingest dry-run and apply execution against an existing document."""
    db_file = tmp_path / "claire.db"
    vault_dir = tmp_path / "vault"
    data_dir = tmp_path / "data"
    vault_dir.mkdir(parents=True)
    data_dir.mkdir(parents=True)

    monkeypatch.setenv("CLAIRE_DB_PATH", str(db_file))
    monkeypatch.setenv("CLAIRE_DATA_DIR", str(data_dir))
    monkeypatch.setenv("CLAIRE_VAULT_DIR", str(vault_dir))
    monkeypatch.setenv("CLAIRE_DATA_LIFECYCLE", "purgeable")
    monkeypatch.setenv("CLAIRE_ALLOW_PURGE", "1")

    from claire.config import get_settings
    get_settings.cache_clear()

    conn = dbm.connect(db_file)
    dbm.init_db(conn)
    prov = MockProvider()
    vstore = make_vector_store(conn, "brute")

    url = "https://example.com/reingest-test"
    text = "Initial content for reingest test."
    mock_doc = Document(
        url=url,
        canonical_url=url,
        title="Initial Document",
        raw_text=text,
        source_type="web",
        content_hash=content_hash(text),
    )
    rep = ingest(
        payload=url,
        conn=conn,
        provider=prov,
        vstore=vstore,
        vault_dir=vault_dir,
        data_dir=data_dir,
        source="web",
        fetch_fn=lambda _: mock_doc,
    )
    conn.close()
    assert rep.document_id is not None
    doc_id = rep.document_id

    parser = cli.build_parser()

    # 1. Dry-run without --apply
    args_dry = parser.parse_args(["reingest", doc_id])
    rc_dry = cli.cmd_reingest(args_dry)
    assert rc_dry == 0
    cap_dry = capsys.readouterr()
    assert "[Dry-Run]" in cap_dry.out
    assert "claire reingest" in cap_dry.out
    assert "--apply" in cap_dry.out

    # 2. Dry-run with --json
    args_json = parser.parse_args(["reingest", doc_id, "--json"])
    rc_json = cli.cmd_reingest(args_json)
    assert rc_json == 0
    cap_json = capsys.readouterr()
    parsed_json = json.loads(cap_json.out)
    assert parsed_json.get("dry_run") is True
    assert parsed_json.get("matched_count") == 1

    # 3. Apply execution
    updated_text = "Updated content after reingest."
    new_doc = Document(
        url=url,
        canonical_url=url,
        title="Updated Document Title",
        raw_text=updated_text,
        source_type="web",
        content_hash=content_hash(updated_text),
    )
    with patch("claire.ingest.service.default_fetch", return_value=new_doc):
        args_apply = parser.parse_args(["reingest", doc_id, "--apply", "--yes"])
        rc_apply = cli.cmd_reingest(args_apply)
        assert rc_apply == 0
        cap_apply = capsys.readouterr()
        assert "재적재 완료 문서 수" in cap_apply.out

    conn = dbm.connect(db_file)
    refetched = dbm.get_document(conn, doc_id)
    conn.close()
    assert refetched is not None
    assert refetched.title == "Updated Document Title"


def test_api_document_purge_and_reingest_routes(tmp_path: Path, monkeypatch):
    """Test Starlette API endpoints /document/purge and /reingest with security permissions."""
    db_file = tmp_path / "claire_api.db"
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
    monkeypatch.setenv("CLAIRE_READONLY_TOKEN", READONLY_TOKEN)

    from claire.config import get_settings
    get_settings.cache_clear()
    settings = get_settings()

    conn = dbm.connect(db_file)
    dbm.init_db(conn)
    prov = MockProvider()
    vstore = make_vector_store(conn, "brute")

    url = "https://example.com/api-test-doc"
    text = "Content for API purge and reingest test."
    mock_doc = Document(
        url=url,
        canonical_url=url,
        title="API Test Document",
        raw_text=text,
        source_type="web",
        content_hash=content_hash(text),
    )
    rep = ingest(
        payload=url,
        conn=conn,
        provider=prov,
        vstore=vstore,
        vault_dir=vault_dir,
        data_dir=data_dir,
        source="web",
        fetch_fn=lambda _: mock_doc,
    )
    conn.close()
    assert rep.document_id is not None
    doc_id = rep.document_id

    app = create_app(settings)
    client = TestClient(app, base_url=settings.public_url)

    # 1. Anonymous / Readonly cannot purge
    res_ro = client.post("/document/purge", json={"target": doc_id}, headers=READONLY_HEADERS)
    assert res_ro.status_code in (403, 404)

    # 2. Owner dry-run purge
    res_dry = client.post(
        "/document/purge",
        json={"target": doc_id, "dry_run": True},
        headers=OWNER_HEADERS,
    )
    assert res_dry.status_code == 200
    data_dry = res_dry.json()
    assert data_dry["dry_run"] is True
    assert len(data_dry["targets"]) == 1

    # 3. Owner actual purge
    res_purge = client.post(
        "/document/purge",
        json={"target": doc_id, "dry_run": False, "tombstone": True},
        headers=OWNER_HEADERS,
    )
    assert res_purge.status_code == 200
    data_purge = res_purge.json()
    assert data_purge["dry_run"] is False
    assert data_purge["deleted_documents"] == 1
    assert data_purge["purged_count"] == 1

    # Verify doc is gone from database
    conn = dbm.connect(db_file)
    assert dbm.get_document(conn, doc_id) is None
    assert dbm.is_tombstoned(conn, url=url) is True
    conn.close()

    # 4. Reingest stream with untombstone lifts tombstone and succeeds
    with patch("claire.ingest.pipeline.default_fetch", return_value=mock_doc):
        res_reing = client.post(
            "/reingest",
            json={"target": url, "untombstone": True},
            headers=OWNER_HEADERS,
        )
        assert res_reing.status_code == 200
        data_reing = res_reing.json()
        assert data_reing.get("document_id") is not None
