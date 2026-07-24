"""웹 근사중복 병합의 입력·백업·artifact 경계 회귀 테스트."""

from __future__ import annotations

from pathlib import Path

import pytest

from claire.config import Settings
from claire.ingest.service import IngestService
from claire.ontology.base import Document
from claire.store import db as dbm


KEEPER = "doc_aaaaaaaaaaaa"
LOSER = "doc_bbbbbbbbbbbb"
THIRD = "doc_cccccccccccc"
MISSING = "doc_dddddddddddd"
BODY = "security merge regression sentence with several stable tokens " * 20


def _service(monkeypatch, tmp_path) -> tuple[Settings, IngestService]:
    monkeypatch.setenv("CLAIRE_DB_PATH", str(tmp_path / "data" / "claire.db"))
    monkeypatch.setenv("CLAIRE_VAULT_PATH", str(tmp_path / "vault"))
    monkeypatch.setenv("CLAIRE_PROVIDER", "mock")
    settings = Settings()
    return settings, IngestService(settings)


def _insert(settings: Settings, *ids: str, bodies: list[str] | None = None) -> None:
    conn = dbm.connect(settings.db_file)
    dbm.init_db(conn)
    try:
        for index, did in enumerate(ids):
            body = bodies[index] if bodies is not None else BODY
            dbm.insert_document(
                conn,
                Document(
                    id=did,
                    url=f"https://example.com/{did}",
                    canonical_url=f"https://example.com/{did}",
                    title="same article",
                    raw_text=body,
                    source_type="web",
                    content_hash=f"hash-{did}",
                ),
            )
    finally:
        conn.close()


def _artifact(settings: Settings, did: str) -> Path:
    path = settings.data_dir / "raw" / "artifacts" / f"{did}.txt.gz"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"artifact")
    return path


def _document_exists(settings: Settings, did: str) -> bool:
    conn = dbm.connect(settings.db_file)
    try:
        return dbm.get_document_row(conn, did) is not None
    finally:
        conn.close()


def test_merge_one_cluster_preserves_legitimate_merge(monkeypatch, tmp_path):
    settings, service = _service(monkeypatch, tmp_path)
    _insert(settings, KEEPER, LOSER)
    artifact = _artifact(settings, LOSER)

    result = service.merge_one_cluster(KEEPER, [LOSER])

    assert result["deleted"] == 1
    assert result["deleted_ids"] == [LOSER]
    assert _document_exists(settings, KEEPER)
    assert not _document_exists(settings, LOSER)
    assert not artifact.exists()
    assert result["backup"]
    assert Path(result["backup"]).is_file()


def test_merge_rejects_invalid_or_missing_document_ids(monkeypatch, tmp_path):
    settings, service = _service(monkeypatch, tmp_path)
    _insert(settings, KEEPER, LOSER)

    with pytest.raises(ValueError, match="invalid loser"):
        service.merge_one_cluster(KEEPER, ["../../escape"])
    with pytest.raises(ValueError, match="do not exist"):
        service.merge_one_cluster(KEEPER, [MISSING])

    assert _document_exists(settings, KEEPER)
    assert _document_exists(settings, LOSER)


def test_merge_requires_same_current_cluster(monkeypatch, tmp_path):
    settings, service = _service(monkeypatch, tmp_path)
    unrelated = "unrelated cooking recipe with different ingredients " * 20
    _insert(settings, KEEPER, LOSER, THIRD, bodies=[BODY, BODY, unrelated])

    with pytest.raises(ValueError, match="same current dedup cluster"):
        service.merge_one_cluster(KEEPER, [THIRD])

    assert all(_document_exists(settings, did) for did in (KEEPER, LOSER, THIRD))


def test_merge_rejects_artifact_symlink_escape(monkeypatch, tmp_path):
    settings, service = _service(monkeypatch, tmp_path)
    _insert(settings, KEEPER, LOSER)
    outside = tmp_path / "outside.txt.gz"
    outside.write_bytes(b"outside")
    artifact = settings.data_dir / "raw" / "artifacts" / f"{LOSER}.txt.gz"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.symlink_to(outside)

    with pytest.raises(ValueError, match="escapes"):
        service.merge_one_cluster(KEEPER, [LOSER])

    assert outside.read_bytes() == b"outside"
    assert _document_exists(settings, LOSER)


def test_backup_failure_aborts_before_merge(monkeypatch, tmp_path):
    settings, service = _service(monkeypatch, tmp_path)
    _insert(settings, KEEPER, LOSER)
    artifact = _artifact(settings, LOSER)

    def _fail_backup(_src, _dest):
        raise OSError("disk full")

    monkeypatch.setattr(dbm, "backup_database", _fail_backup)
    with pytest.raises(RuntimeError, match="backup failed"):
        service.merge_one_cluster(KEEPER, [LOSER])

    assert _document_exists(settings, KEEPER)
    assert _document_exists(settings, LOSER)
    assert artifact.exists()


def test_only_deleted_ids_remove_artifacts_and_backup_names_are_unique(
    monkeypatch, tmp_path,
):
    settings, service = _service(monkeypatch, tmp_path)
    _insert(settings, KEEPER, LOSER)
    artifact = _artifact(settings, LOSER)
    destinations = []

    def _record_backup(_src, dest):
        destinations.append(Path(dest))
        return dest

    def _no_delete(_conn, keeper, losers):
        return {"keeper": keeper, "losers": losers, "deleted": 0}

    monkeypatch.setattr(dbm, "backup_database", _record_backup)
    monkeypatch.setattr(dbm, "merge_documents", _no_delete)

    first = service.merge_one_cluster(KEEPER, [LOSER])
    second = service.merge_one_cluster(KEEPER, [LOSER])

    assert first["deleted_ids"] == second["deleted_ids"] == []
    assert artifact.exists()
    assert len(destinations) == 2
    assert destinations[0] != destinations[1]
