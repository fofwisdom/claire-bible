"""Unit tests for regenerate CLI command, API endpoints, and streaming."""

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
COLLAB_TOKEN = "collab-" + ("c" * 32)
READONLY_TOKEN = "readonly-" + ("r" * 32)
OWNER_HEADERS = {"Authorization": f"Bearer {OWNER_TOKEN}"}
COLLAB_HEADERS = {"Authorization": f"Bearer {COLLAB_TOKEN}"}
READONLY_HEADERS = {"Authorization": f"Bearer {READONLY_TOKEN}"}


def test_cli_regenerate_parser_options():
    """Verify regenerate parser defines all required options and aliases conforming to CLI standard."""
    parser = cli.build_parser()

    # Default dry-run with main command
    args = parser.parse_args(["regenerate", "doc_123"])
    assert args.target == "doc_123"
    assert args.apply is False
    assert args.dry_run is False
    assert args.component is None

    # Alias 'regen' with options
    args_alias = parser.parse_args([
        "regen", "doc_123",
        "--detail",
        "--apply",
        "-y",
        "--effort", "high",
        "--focus", "Security focus",
        "--format", "md",
        "--json",
    ])
    assert args_alias.target == "doc_123"
    assert args_alias.detail is True
    assert args_alias.apply is True
    assert args_alias.yes is True
    assert args_alias.effort == "high"
    assert args_alias.focus == "Security focus"
    assert args_alias.format == "md"
    assert args_alias.json is True


def test_cli_regenerate_dry_run_and_apply(tmp_path: Path, monkeypatch, capsys):
    """Test CLI regenerate dry-run and apply execution against an existing document."""
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

    url = "https://example.com/regen-test"
    text = "Initial content for component regenerate test."
    mock_doc = Document(
        url=url,
        canonical_url=url,
        title="Initial Regen Doc",
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
        source="unit-test",
        fetch_fn=lambda _: mock_doc,
    )
    conn.close()
    assert rep.document_id is not None
    doc_id = rep.document_id

    # 1. Dry-run execution
    capsys.readouterr()
    rc_dry = cli.main(["regenerate", doc_id, "--detail"])
    assert rc_dry == 0
    out_dry = capsys.readouterr().out
    assert "[Dry-Run]" in out_dry
    assert doc_id in out_dry
    assert "--apply" in out_dry

    # 2. JSON dry-run output
    rc_json = cli.main(["regenerate", doc_id, "--detail", "--json"])
    assert rc_json == 0
    out_json = capsys.readouterr().out
    parsed = json.loads(out_json)
    d = parsed.get("data", parsed)
    assert d["dry_run"] is True
    assert d["count"] == 1

    # 3. Apply execution
    rc_apply = cli.main(["regen", doc_id, "--detail", "--apply", "--yes"])
    assert rc_apply == 0
    out_apply = capsys.readouterr().out
    assert "재생성 완료" in out_apply or "완료" in out_apply


def test_api_regenerate_and_stream(tmp_path: Path, monkeypatch):
    """Test API endpoints /regenerate, /document/regenerate, and /regenerate-stream."""
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

    url = "https://example.com/api-regen-test"
    text = "API regeneration document content."
    mock_doc = Document(
        url=url,
        canonical_url=url,
        title="API Doc",
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
        source="api-test",
        fetch_fn=lambda _: mock_doc,
    )
    conn.close()
    assert rep.document_id is not None
    doc_id = rep.document_id

    app = create_app(settings)
    client = TestClient(app, base_url=settings.public_url)

    # 1. Readonly user must be rejected with 404 (non-public route hidden)
    res_ro = client.post("/regenerate", json={"target": doc_id}, headers=READONLY_HEADERS)
    assert res_ro.status_code == 404

    # 2. Owner dry-run
    res_dry = client.post(
        "/regenerate",
        json={"target": doc_id, "component": "detail", "dry_run": True},
        headers=OWNER_HEADERS,
    )
    assert res_dry.status_code == 200
    d_dry = res_dry.json()
    assert d_dry.get("dry_run") is True
    assert d_dry.get("count") == 1

    # 3. Owner apply via /document/regenerate
    res_app = client.post(
        "/document/regenerate",
        json={"target": doc_id, "component": "summary", "dry_run": False},
        headers=OWNER_HEADERS,
    )
    assert res_app.status_code == 200
    d_app = res_app.json()
    assert d_app.get("count") == 1

    # 4. Streaming regeneration via /regenerate-stream
    res_stream = client.post(
        "/regenerate-stream",
        json={"target": doc_id, "component": "detail", "dry_run": False, "focus": "Architecture"},
        headers=OWNER_HEADERS,
    )
    assert res_stream.status_code == 200
    lines = [ln for ln in res_stream.text.split("\n") if ln.strip()]
    assert len(lines) >= 1
    last_event = json.loads(lines[-1])
    assert last_event.get("done") is True
    assert last_event["result"].get("count") == 1
