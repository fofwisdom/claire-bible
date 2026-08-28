"""파괴적 앱 작업용 내부 SQLite checkpoint."""

from __future__ import annotations

from pathlib import Path

import pytest

from claire.cli import build_parser
from claire.config import Settings
from claire.ingest.service import IngestService
from claire.ontology.base import Document, Entity, Relation
from claire.store import db as dbm


def _seed(path):
    conn = dbm.connect(path)
    dbm.init_db(conn)
    dbm.insert_document(conn, Document(id="d1", url="https://x/1", raw_text="body",
                                       source_type="web", content_hash="h1"))
    dbm.upsert_entity(conn, Entity(id="e1", type="Tool", name="Foo", sources=["d1"]))
    dbm.upsert_entity(conn, Entity(id="e2", type="Org", name="Bar", sources=["d1"]))
    dbm.upsert_relation(conn, Relation(id="r1", type="authored_by",
                                       source_id="e1", target_id="e2", sources=["d1"]))
    conn.close()


def test_internal_checkpoint_snapshot_is_restorable(tmp_path):
    src = tmp_path / "claire.db"
    _seed(src)
    live = dbm.connect(src)
    live_counts = dbm.counts(live)
    live.close()

    dest = tmp_path / "snap.db"
    out = dbm.checkpoint_database(src, dest)
    assert out.exists() and out.stat().st_size > 0

    # 핵심: checkpoint를 독립적으로 열어 row count가 정확히 일치해야 한다.
    snap = dbm.connect(dest)
    snap_counts = dbm.counts(snap)
    # 스냅샷이 정상 DB 로서 쿼리 가능한지(엔티티 실제 조회)
    ent = dbm.get_entity(snap, "e1")
    snap.close()
    assert snap_counts == live_counts
    assert snap_counts["documents"] == 1 and snap_counts["entities"] == 2
    assert snap_counts["relations"] == 1
    assert ent is not None and ent.name == "Foo"


def _service(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAIRE_DB_PATH", str(tmp_path / "claire.db"))
    monkeypatch.setenv("CLAIRE_VAULT_PATH", str(tmp_path / "vault"))
    monkeypatch.setenv("CLAIRE_PROVIDER", "mock")
    settings = Settings()
    _seed(settings.db_file)
    conn = dbm.connect(settings.db_file)
    dbm.insert_document(
        conn,
        Document(
            id="d2",
            url="https://x/2",
            raw_text="second",
            source_type="web",
            content_hash="h2",
        ),
    )
    conn.close()
    return settings, IngestService(settings)


def test_web_merge_requires_and_returns_internal_checkpoint(monkeypatch, tmp_path):
    settings, service = _service(monkeypatch, tmp_path)

    result = service.merge_one_cluster("d1", ["d2"])

    checkpoint = Path(result["checkpoint"])
    assert checkpoint.parent == settings.data_dir / "checkpoints"
    assert checkpoint.name.startswith("pre-webmerge-")
    assert checkpoint.suffix == ".db"
    assert "backup" not in result

    snap = dbm.connect(checkpoint)
    try:
        assert dbm.get_document_row(snap, "d1") is not None
        assert dbm.get_document_row(snap, "d2") is not None
    finally:
        snap.close()
    live = dbm.connect(settings.db_file)
    try:
        assert dbm.get_document_row(live, "d1") is not None
        assert dbm.get_document_row(live, "d2") is None
    finally:
        live.close()


def test_web_merge_does_not_mutate_when_checkpoint_fails(
    monkeypatch, tmp_path
):
    settings, service = _service(monkeypatch, tmp_path)

    def fail_checkpoint(_src, _dest):
        raise OSError("checkpoint unavailable")

    monkeypatch.setattr(dbm, "checkpoint_database", fail_checkpoint)
    with pytest.raises(OSError, match="checkpoint unavailable"):
        service.merge_one_cluster("d1", ["d2"])

    live = dbm.connect(settings.db_file)
    try:
        assert dbm.get_document_row(live, "d1") is not None
        assert dbm.get_document_row(live, "d2") is not None
    finally:
        live.close()


def test_application_cli_has_no_operational_backup_surface():
    parser = build_parser()
    command_action = next(action for action in parser._actions if action.dest == "cmd")

    assert "backup" not in command_action.choices
    assert "backup-loop" not in command_action.choices
    with pytest.raises(SystemExit):
        parser.parse_args(["reextract", "--no-backup"])
    with pytest.raises(SystemExit):
        parser.parse_args(["dedup-merge", "--keep", "3"])
