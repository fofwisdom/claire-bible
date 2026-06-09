"""DB 백업 — VACUUM INTO 스냅샷이 *복원 가능*(row count 일치)한지 + 보존 정리."""

from __future__ import annotations

from claire.ontology.base import Document, Entity, Relation
from claire.store import db as dbm
from claire.cli import _prune_backups


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


def test_backup_snapshot_is_restorable(tmp_path):
    src = tmp_path / "claire.db"
    _seed(src)
    live = dbm.connect(src)
    live_counts = dbm.counts(live)
    live.close()

    dest = tmp_path / "snap.db"
    out = dbm.backup_database(src, dest)
    assert out.exists() and out.stat().st_size > 0

    # 핵심: 스냅샷을 독립적으로 열어 row count 가 정확히 일치해야 한다(파일 존재로 불충분).
    snap = dbm.connect(dest)
    snap_counts = dbm.counts(snap)
    # 스냅샷이 정상 DB 로서 쿼리 가능한지(엔티티 실제 조회)
    ent = dbm.get_entity(snap, "e1")
    snap.close()
    assert snap_counts == live_counts
    assert snap_counts["documents"] == 1 and snap_counts["entities"] == 2
    assert snap_counts["relations"] == 1
    assert ent is not None and ent.name == "Foo"


def test_prune_keeps_recent(tmp_path):
    bdir = tmp_path / "backups"
    bdir.mkdir()
    # 타임스탬프 순으로 정렬되는 이름 5개 생성
    names = [f"claire-2026010{i}-000000.db" for i in range(1, 6)]
    for n in names:
        (bdir / n).write_text("x")
    removed = _prune_backups(bdir, keep=2)
    remaining = sorted(p.name for p in bdir.glob("claire-*.db"))
    assert removed == 3
    assert remaining == names[-2:]  # 최신 2개만 남음


def test_prune_noop_when_under_keep(tmp_path):
    bdir = tmp_path / "backups"
    bdir.mkdir()
    (bdir / "claire-20260101-000000.db").write_text("x")
    assert _prune_backups(bdir, keep=7) == 0
