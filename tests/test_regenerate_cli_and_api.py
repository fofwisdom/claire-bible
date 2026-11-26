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
from claire.core.config import Settings
from claire.extract.provider import MockProvider
from claire.ingest.normalize import content_hash
from claire.ingest.pipeline import IngestReport, ingest
from claire.ontology.base import Document
from claire.store import db as dbm
from claire.store.vectors import make_vector_store
from tests.auth_helpers import bearer_headers


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
        "--renditions",
        "--apply",
        "-y",
        "--effort", "high",
        "--format", "adoc",
        "--json",
    ])
    assert args_alias.target == "doc_123"
    assert args_alias.detail is True
    assert args_alias.renditions is True
    assert args_alias.apply is True
    assert args_alias.yes is True
    assert args_alias.effort == "high"
    assert args_alias.format == "adoc"
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

    from claire.core.config import get_settings
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
    assert "Regeneration of the document components is complete" in out_apply


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
    monkeypatch.setenv("CLAIRE_ENVIRONMENT", "development")
    monkeypatch.setenv("CLAIRE_FQDN", "127.0.0.1:8765")

    from claire.core.config import get_settings
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
        data_dir=data_dir,
        source="api-test",
        fetch_fn=lambda _: mock_doc,
    )
    conn.close()
    assert rep.document_id is not None
    doc_id = rep.document_id

    owner_headers = bearer_headers(db_file, scope="owner")
    readonly_headers = bearer_headers(db_file, scope="readonly")
    app = create_app(settings)
    client = TestClient(app, base_url=settings.public_url)

    # 1. Readonly user must be rejected with 403 (the scope is too small)
    res_ro = client.post("/regenerate", json={"target": doc_id}, headers=readonly_headers)
    assert res_ro.status_code == 403

    # 2. Owner dry-run
    res_dry = client.post(
        "/regenerate",
        json={"target": doc_id, "component": "detail", "dry_run": True},
        headers=owner_headers,
    )
    assert res_dry.status_code == 200
    d_dry = res_dry.json()
    assert d_dry.get("dry_run") is True
    assert d_dry.get("count") == 1

    # 3. Owner apply via /document/regenerate
    res_app = client.post(
        "/document/regenerate",
        json={"target": doc_id, "component": "summary", "dry_run": False},
        headers=owner_headers,
    )
    assert res_app.status_code == 200
    d_app = res_app.json()
    assert d_app.get("count") == 1

    # 4. Streaming regeneration via /regenerate-stream
    res_stream = client.post(
        "/regenerate-stream",
        json={"target": doc_id, "component": "detail", "dry_run": False},
        headers=owner_headers,
    )
    assert res_stream.status_code == 200
    lines = [ln for ln in res_stream.text.split("\n") if ln.strip()]
    assert len(lines) >= 1
    last_event = json.loads(lines[-1])
    assert last_event.get("done") is True
    assert last_event["result"].get("count") == 1


def test_regenerate_include_renditions_and_refetch_clears_stale_renditions(tmp_path: Path, monkeypatch):
    """--renditions re-renders existing additional renditions; --refetch clears stale additional renditions and tombstones published objects."""
    from claire.ingest.service import IngestService
    from claire.store import accounts as accounts_store, document_renditions as renditions_store, federation as federation_store

    db_file = tmp_path / "claire_rend.db"
    vault_dir = tmp_path / "vault"
    data_dir = tmp_path / "data"
    vault_dir.mkdir(parents=True)
    data_dir.mkdir(parents=True)

    monkeypatch.setenv("CLAIRE_DB_PATH", str(db_file))
    monkeypatch.setenv("CLAIRE_DATA_DIR", str(data_dir))
    monkeypatch.setenv("CLAIRE_VAULT_DIR", str(vault_dir))
    monkeypatch.setenv("CLAIRE_ENVIRONMENT", "development")
    monkeypatch.setenv("CLAIRE_FQDN", "127.0.0.1:8765")

    from claire.core.config import get_settings

    get_settings.cache_clear()
    settings = get_settings()

    conn = dbm.connect(db_file)
    dbm.init_db(conn)
    acct = accounts_store.AccountStore(conn).create_member(username="alice", display_name="Alice")
    salt = accounts_store.AccountStore(conn).id_salt()
    prov = MockProvider()
    vstore = make_vector_store(conn, "brute")
    url = "https://example.com/regen-renditions"
    doc_v1 = Document(url=url, canonical_url=url, title="Regen Renditions", raw_text="Initial text body " * 20, source_type="web", content_hash=content_hash("Initial text body " * 20))
    rep = ingest(payload=url, conn=conn, provider=prov, vstore=vstore, data_dir=data_dir, source="unit-test", fetch_fn=lambda _: doc_v1)
    assert rep.document_id is not None
    doc_id = rep.document_id
    renditions_store.store_additional(conn, doc_id, 0, template="concise", detail="= Old Concise\n\nOld.", lineage={"provider": "mock"}, now=1800000100.0)
    renditions_store.store_additional(conn, doc_id, 0, template="academic", detail="= Old Academic\n\nOld.", lineage={"provider": "mock"}, now=1800000100.0)
    obj, _, _ = federation_store.publish_document(conn, account_id=acct.id, kb_id=0, document_id=doc_id, published_by_account_id=acct.id, salt=salt, now=1800000110.0)
    pub_rends = {r.template: r.public_id for r in federation_store.rendition_objects_of(conn, obj.public_id)}
    federation_store.record_body_reader(conn, pub_rends["concise"], "https://remote.example/ap/actors/server", now=1800000115.0)
    conn.commit()
    conn.close()

    svc = IngestService(settings, kb_id=0)
    res_regen = svc.regenerate_components(doc_id=doc_id, detail=True, include_renditions=True, force=True)
    assert res_regen["targets"][0]["updated"] is True
    assert [(r["template"], r["state"]) for r in res_regen["targets"][0]["renditions"]] == [("concise", "made"), ("academic", "made")]

    conn = dbm.connect_existing(db_file, readonly=True)
    try:
        concise = renditions_store.get_rendition(conn, doc_id, 0, "concise")
        academic = renditions_store.get_rendition(conn, doc_id, 0, "academic")
        assert concise is not None and "[mock-detail-adoc] [template: concise]" in (concise.detail or "")
        assert academic is not None and "[mock-detail-adoc] [template: academic]" in (academic.detail or "")
    finally:
        conn.close()

    # Refetch overwrites raw_text -> clears stale additional renditions and tombstones published rendition objects.
    doc_v2 = Document(url=url, canonical_url=url, title="Regen Renditions v2", raw_text="Completely updated raw text " * 20, source_type="web", content_hash=content_hash("Completely updated raw text " * 20))
    with patch("claire.ingest.service.default_fetch", return_value=doc_v2):
        res_refetch = svc.regenerate_components(doc_id=doc_id, detail=True, include_renditions=True, refetch=True, force=True)
    assert res_refetch["targets"][0]["refetched"] is True
    assert "renditions" not in res_refetch["targets"][0]

    conn = dbm.connect_existing(db_file, readonly=True)
    try:
        assert [r.template for r in renditions_store.list_renditions(conn, doc_id, 0)] == ["standard"]
        assert federation_store.get_tombstone(conn, pub_rends["concise"]) is not None
        assert federation_store.get_tombstone(conn, pub_rends["academic"]) is not None
        gone_at = conn.execute("SELECT gone_at FROM federation_body_readers WHERE object_public_id=?", (pub_rends["concise"],)).fetchone()[0]
        assert gone_at is not None
    finally:
        conn.close()


