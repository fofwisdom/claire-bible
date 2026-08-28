"""데이터 수명주기(Append-Only vs Purgeable) 및 오염 데이터 소각(Purge)/감사(Audit) 테스트."""

from __future__ import annotations

import io
import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from claire.cli import build_parser, cmd_audit, cmd_purge
from claire.config import Settings
from claire.extract.provider import MockProvider
from claire.ingest.normalize import content_hash
from claire.ingest.pipeline import IngestReport, extract_resolve_store, ingest
from claire.ontology.base import Document
from claire.store import db as dbm
from claire.store.vectors import make_vector_store


def _fetch_doc(doc: Document):
    return lambda payload: doc


def test_config_data_lifecycle_settings(monkeypatch):
    """설정 파싱 및 is_purge_allowed 동작 검증."""
    # 1. 기본값: append-only, allow_purge=False -> 불허
    monkeypatch.delenv("CLAIRE_DATA_LIFECYCLE", raising=False)
    monkeypatch.delenv("CLAIRE_ALLOW_PURGE", raising=False)
    s = Settings()
    assert s.data_lifecycle == "append-only"
    assert s.allow_purge is False
    assert s.is_purge_allowed is False

    # 2. CLAIRE_DATA_LIFECYCLE=purgeable -> 허용
    monkeypatch.setenv("CLAIRE_DATA_LIFECYCLE", "purgeable")
    s2 = Settings()
    assert s2.data_lifecycle == "purgeable"
    assert s2.is_purge_allowed is True

    # 3. CLAIRE_ALLOW_PURGE=1 -> 허용
    monkeypatch.setenv("CLAIRE_DATA_LIFECYCLE", "append-only")
    monkeypatch.setenv("CLAIRE_ALLOW_PURGE", "1")
    s3 = Settings()
    assert s3.is_purge_allowed is True

    # 4. 잘못된 값 검증
    with pytest.raises(ValidationError):
        Settings(CLAIRE_DATA_LIFECYCLE="invalid_mode")


def test_purge_blocked_in_append_only_mode(tmp_path, monkeypatch, capsys):
    """append-only 모드에서는 purge 명령어가 정책에 의해 차단되는지 검증."""
    db_file = tmp_path / "claire.db"
    monkeypatch.setenv("CLAIRE_DB_PATH", str(db_file))
    monkeypatch.setenv("CLAIRE_DATA_LIFECYCLE", "append-only")
    monkeypatch.setenv("CLAIRE_ALLOW_PURGE", "0")

    parser = build_parser()
    args = parser.parse_args(["purge", "doc_test_123"])
    rc = cmd_purge(args)
    assert rc == 1

    captured = capsys.readouterr()
    assert "데이터 소각이 정책에 의해 차단되었습니다" in captured.err
    assert "CLAIRE_DATA_LIFECYCLE" in captured.err


def test_purge_cascade_and_tombstones(tmp_path, monkeypatch):
    """소각 실행 시 8개 DB 테이블, 디스크 파일, 그래프 고아 노드 소각 및 툼스톤 등록 검증."""
    db_file = tmp_path / "claire.db"
    data_dir = tmp_path / "data"
    vault_dir = tmp_path / "vault"
    monkeypatch.setenv("CLAIRE_DB_PATH", str(db_file))
    monkeypatch.setenv("CLAIRE_DATA_LIFECYCLE", "purgeable")

    conn = dbm.connect(db_file)
    dbm.init_db(conn)
    prov = MockProvider()
    vstore = make_vector_store(conn, "brute")

    # 1. 테스트용 오염 문서 적재
    url = "https://legacy-bible.example.com/v1/corrupted-verse"
    text = "폐기된 구버전 번역 텍스트입니다. 오염을 유발합니다. " * 5
    mock_doc = Document(
        url=url,
        canonical_url=url,
        title="Corrupted Legacy Verse",
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
        fetch_fn=_fetch_doc(mock_doc),
    )
    assert rep.document_id is not None
    doc_id = rep.document_id

    # 2. 관련 파일 생성 확인
    art_file = data_dir / "raw" / "artifacts" / f"{doc_id}.txt.gz"
    assert art_file.exists()

    # 스냅샷, 큐, inbox 에 레코드 존재하는지 확인
    dbm.enqueue_refresh(conn, document_id=doc_id, payload=url, reason="test")
    dbm.enqueue_expand(conn, doc_id)
    dbm.save_document_snapshot(
        conn, doc_id, captured_at=1000.0, content_hash="hash123", title="Title", raw_text=text
    )

    # 3. Dry-Run 검증
    dry_report = dbm.purge_document_cascade(
        conn, data_dir=data_dir, vault_dir=vault_dir, target_ids=[doc_id], dry_run=True
    )
    assert dry_report["dry_run"] is True
    assert dry_report["purged_count"] == 1
    assert dry_report["disk_files_count"] >= 1
    # DB에 여전히 남아있어야 함
    assert dbm.get_document(conn, doc_id) is not None

    # 4. 실제 소각 실행 (Cascade Purge)
    purge_report = dbm.purge_document_cascade(
        conn, data_dir=data_dir, vault_dir=vault_dir, target_ids=[doc_id], reason="legacy_test_purge", dry_run=False
    )
    assert purge_report["dry_run"] is False
    assert purge_report["deleted_documents"] == 1
    assert purge_report["disk_files_unlinked"] >= 1
    assert not art_file.exists()

    # 5. DB 8개 테이블에서 완전 소각되었는지 확인
    assert dbm.get_document(conn, doc_id) is None
    assert conn.execute("SELECT COUNT(*) FROM raw_inbox WHERE document_id=?", (doc_id,)).fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM extractions WHERE document_id=?", (doc_id,)).fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM document_snapshots WHERE document_id=?", (doc_id,)).fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM refresh_queue WHERE document_id=?", (doc_id,)).fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM expand_queue WHERE document_id=?", (doc_id,)).fetchone()[0] == 0

    # 6. 툼스톤 등록 확인
    assert dbm.is_tombstoned(conn, url=url) is True
    assert dbm.is_tombstoned(conn, canonical_url=url) is True

    # 7. 재수집 시도 시 툼스톤에 의해 차단되는지 확인
    rep2 = ingest(
        payload=url,
        conn=conn,
        provider=prov,
        vstore=vstore,
        vault_dir=vault_dir,
        data_dir=data_dir,
        source="web",
        fetch_fn=_fetch_doc(mock_doc),
    )
    assert rep2.error is not None
    assert "tombstoned" in rep2.error

    conn.close()


def test_audit_and_doctor_diagnostics(tmp_path, monkeypatch):
    """audit_residuals 및 diagnose_graph 의 잔재 0건 확인 및 위반 탐지 검증."""
    db_file = tmp_path / "claire.db"
    data_dir = tmp_path / "data"
    vault_dir = tmp_path / "vault"
    monkeypatch.setenv("CLAIRE_DB_PATH", str(db_file))
    monkeypatch.setenv("CLAIRE_DATA_LIFECYCLE", "purgeable")

    conn = dbm.connect(db_file)
    dbm.init_db(conn)
    prov = MockProvider()
    vstore = make_vector_store(conn, "brute")

    doc1 = Document(
        url="https://valid-bible.example.com/verse1",
        canonical_url="https://valid-bible.example.com/verse1",
        title="Valid Bible Verse",
        raw_text="유효한 성경 본문 텍스트입니다. " * 5,
        source_type="web",
        content_hash="hash-valid",
    )
    doc2 = Document(
        url="https://corrupted-text.example.com/verse2",
        canonical_url="https://corrupted-text.example.com/verse2",
        title="Corrupted Text Verse",
        raw_text="오염된 텍스트 본문입니다. " * 5,
        source_type="web",
        content_hash="hash-corrupt",
    )

    # 정상 문서 1건 적재
    rep1 = ingest(
        payload="https://valid-bible.example.com/verse1",
        conn=conn,
        provider=prov,
        vstore=vstore,
        vault_dir=vault_dir,
        data_dir=data_dir,
        source="web",
        fetch_fn=_fetch_doc(doc1),
    )
    # 소각 대상 오염 문서 1건 적재
    rep2 = ingest(
        payload="https://corrupted-text.example.com/verse2",
        conn=conn,
        provider=prov,
        vstore=vstore,
        vault_dir=vault_dir,
        data_dir=data_dir,
        source="web",
        fetch_fn=_fetch_doc(doc2),
    )
    bad_id = rep2.document_id

    # 소각 전 audit 검사
    audit_pre = dbm.audit_residuals(conn, data_dir, pattern_or_id="corrupted-text")
    assert audit_pre["clean"] is False
    assert audit_pre["matching_documents_count"] == 1

    # 소각 실행
    dbm.purge_document_cascade(
        conn, data_dir=data_dir, vault_dir=vault_dir, target_ids=[bad_id], reason="pollution", dry_run=False
    )

    # 소각 후 audit 검사 -> 잔재 0건 (Clean)
    audit_post = dbm.audit_residuals(conn, data_dir, pattern_or_id="corrupted-text")
    assert audit_post["clean"] is True
    assert audit_post["matching_documents_count"] == 0
    assert audit_post["matching_disk_artifacts_count"] == 0
    assert audit_post["purged_tombstones_count"] == 1
    assert audit_post["tombstone_violations_count"] == 0

    # Doctor 무결성 점검
    diag = dbm.diagnose_graph(conn)
    assert diag["is_healthy"] is True
    assert diag["purged_tombstones_count"] == 1
    assert diag["tombstone_violations_count"] == 0

    conn.close()


def test_purge_cli_smart_targets(tmp_path, monkeypatch, capsys):
    """CLI purge 명령에서 URL, 도메인, 공유링크, 키워드 자동 식별 및 경고/소각 검증."""
    from claire.config import get_settings
    get_settings.cache_clear()

    db_file = tmp_path / "claire.db"
    data_dir = tmp_path / "data"
    vault_dir = tmp_path / "vault"
    monkeypatch.setenv("CLAIRE_DB_PATH", str(db_file))
    monkeypatch.setenv("CLAIRE_DATA_LIFECYCLE", "purgeable")
    get_settings.cache_clear()

    conn = dbm.connect(db_file)
    dbm.init_db(conn)
    prov = MockProvider()
    vstore = make_vector_store(conn, "brute")

    doc = Document(
        url="https://news.example.com/tech/article-123?utm_source=twitter",
        canonical_url="https://news.example.com/tech/article-123",
        title="Spam Polluted Article",
        raw_text="스팸 오염 문서 본문입니다. " * 5,
        source_type="web",
        content_hash="hash-spam-123",
    )
    rep = ingest(
        payload="https://news.example.com/tech/article-123?utm_source=twitter",
        conn=conn,
        provider=prov,
        vstore=vstore,
        vault_dir=vault_dir,
        data_dir=data_dir,
        source="web",
        fetch_fn=_fetch_doc(doc),
    )
    doc_id = rep.document_id

    # 공유 토큰 발급
    share_token = dbm.create_doc_share(conn, doc_id)
    conn.close()

    parser = build_parser()

    # 1. 0건 검색 시 친절 안내 검증
    args_notfound = parser.parse_args(["purge", "nonexistent_target_xyz"])
    rc = cmd_purge(args_notfound)
    assert rc == 0
    cap = capsys.readouterr()
    assert "일치하는 소각 대상 문서를 찾을 수 없습니다" in cap.err
    assert "최근 수집된 문서 목록" in cap.err

    # 2. 공유 URL로 Dry-Run 검증 (경고 문구 출력)
    share_url = f"https://myclaire.local/p?s={share_token}"
    args_share = parser.parse_args(["purge", share_url])
    rc = cmd_purge(args_share)
    assert rc == 0
    cap = capsys.readouterr()
    assert "[Dry-Run]" in cap.out
    assert "공유 링크(?s=token)로 식별된 문서가 포함되어 있습니다" in cap.out
    assert "Spam Polluted Article" in cap.out

    # 3. 프로토콜 없는 도메인/경로로 --apply -y 소각 검증
    no_proto_target = "news.example.com/tech/article-123"
    args_purge = parser.parse_args(["purge", no_proto_target, "--apply", "-y"])
    rc = cmd_purge(args_purge)
    assert rc == 0
    cap = capsys.readouterr()
    assert "소각된 문서 수 (DB)" in cap.out
    assert "등록된 툼스톤" in cap.out

    # DB에 소각 및 툼스톤 확인
    conn2 = dbm.connect(db_file)
    assert dbm.get_document_row(conn2, doc_id) is None
    assert dbm.is_tombstoned(conn2, canonical_url="https://news.example.com/tech/article-123") is True
    conn2.close()
