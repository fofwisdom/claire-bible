"""원문 절단(슬라이싱) 탐지 및 메타데이터 소급 갱신(Backfill) CLI 테스트."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from claire import cli
from claire.config import Settings, get_settings
from claire.ingest.normalize import content_hash
from claire.ingest.service import IngestService
from claire.ontology.base import Document
from claire.store import db as dbm


@pytest.fixture
def test_env(tmp_path: Path, monkeypatch):
    db_file = tmp_path / "test.db"
    vault_dir = tmp_path / "vault"
    monkeypatch.setenv("CLAIRE_DB_PATH", str(db_file))
    monkeypatch.setenv("CLAIRE_VAULT_PATH", str(vault_dir))
    monkeypatch.setenv("CLAIRE_PROVIDER", "mock")
    monkeypatch.setenv("CLAIRE_VECTOR_BACKEND", "mock")
    get_settings.cache_clear()

    conn = dbm.connect(db_file)
    dbm.init_db(conn)

    # 1. 온전한 짧은 문서 (Intact)
    doc_intact = Document(
        id="doc_intact",
        title="Intact Doc",
        url="https://example.com/intact",
        raw_text="Short content",
        source_type="web",
        content_hash=content_hash("Intact Doc", "Short content"),
        meta={"directive": "정상 방향성"},
    )
    dbm.insert_document(conn, doc_intact)

    # 2. 이미 기록된 절단 문서 (Recorded Truncated)
    doc_rec = Document(
        id="doc_rec",
        title="Recorded Truncated Doc",
        url="https://example.com/rec",
        raw_text="A" * 20000,
        source_type="web",
        content_hash=content_hash("Recorded Truncated Doc", "A" * 35000),
        meta={"raw_truncated": True, "orig_chars": 35000, "raw_chars": 20000},
    )
    dbm.insert_document(conn, doc_rec)

    # 3. 과거 수집되어 메타데이터가 누락된 절단 문서 (Unmarked Truncated via Hash Mismatch)
    doc_unmarked_hash = Document(
        id="doc_unmarked_hash",
        title="Unmarked Hash Doc",
        url="https://example.com/unmarked-hash",
        raw_text="B" * 20000,
        source_type="web",
        content_hash=content_hash("Unmarked Hash Doc", "B" * 50000),
        meta={"fetch_via": "law"},  # raw_truncated 없음
    )
    dbm.insert_document(conn, doc_unmarked_hash)

    # 4. 과거 수집되어 메타데이터가 누락된 유튜브 문서 (Unmarked Truncated Youtube)
    doc_unmarked_yt = Document(
        id="doc_unmarked_yt",
        title="Unmarked YT Doc",
        url="https://youtube.com/watch?v=12345",
        raw_text="C" * 20000,
        source_type="youtube",
        content_hash=content_hash("C" * 40000),
        meta={},
    )
    dbm.insert_document(conn, doc_unmarked_yt)

    conn.close()
    return {"db_file": db_file, "vault_dir": vault_dir}


def test_scan_truncation_status_db_function(test_env):
    db_file = test_env["db_file"]
    conn = dbm.connect(db_file)
    try:
        res = dbm.scan_truncation_status(conn)
        assert res["total_documents"] == 4
        assert res["intact_count"] == 1
        assert res["recorded_truncated_count"] == 1
        assert res["unmarked_truncated_count"] == 2

        unmarked_ids = {it["id"] for it in res["unmarked_items"]}
        assert unmarked_ids == {"doc_unmarked_hash", "doc_unmarked_yt"}

        # 단건 검사
        single_res = dbm.scan_truncation_status(conn, doc_id="doc_unmarked_hash")
        assert single_res["total_documents"] == 1
        assert single_res["unmarked_truncated_count"] == 1
        assert single_res["unmarked_items"][0]["id"] == "doc_unmarked_hash"
        assert single_res["unmarked_items"][0]["hash_mismatch"] is True
    finally:
        conn.close()


def test_backfill_truncation_metadata_db_function(test_env):
    db_file = test_env["db_file"]
    conn = dbm.connect(db_file)
    try:
        # 1. Backfill unmarked documents with mark_refresh
        out = dbm.backfill_truncation_metadata(conn, mark_refresh=True)
        assert out["scanned_total"] == 4
        assert out["updated_count"] == 2
        assert out["refreshed_count"] == 2

        # 2. Verify documents.meta has raw_truncated=True and preserved other keys
        doc_hash_row = conn.execute("SELECT meta FROM documents WHERE id='doc_unmarked_hash'").fetchone()
        meta_hash = json.loads(doc_hash_row["meta"])
        assert meta_hash.get("raw_truncated") is True
        assert meta_hash.get("raw_chars") == 20000
        assert meta_hash.get("fetch_via") == "law"  # 기존 키 보존

        # 3. Verify refresh_queue entries
        rq_rows = conn.execute("SELECT document_id, reason, status FROM refresh_queue").fetchall()
        rq_doc_ids = {r["document_id"] for r in rq_rows}
        assert "doc_unmarked_hash" in rq_doc_ids
        assert "doc_unmarked_yt" in rq_doc_ids
        for r in rq_rows:
            assert r["reason"] == "truncated_backfill"
            assert r["status"] == "pending"

        # 4. Re-scan should show 0 unmarked
        res_after = dbm.scan_truncation_status(conn)
        assert res_after["unmarked_truncated_count"] == 0
        assert res_after["recorded_truncated_count"] == 3
        assert res_after["intact_count"] == 1
    finally:
        conn.close()


def test_ingest_service_truncation_methods(test_env):
    s = get_settings()
    svc = IngestService(s)

    scan = svc.scan_truncation_status()
    assert scan["unmarked_truncated_count"] == 2

    backfill_res = svc.backfill_truncation(mark_refresh=False)
    assert backfill_res["updated_count"] == 2

    scan_after = svc.scan_truncation_status()
    assert scan_after["unmarked_truncated_count"] == 0


def test_cli_truncation_status(test_env, capsys):
    # 1. Text format
    ret = cli.main(["truncation-status"])
    assert ret == 0
    out = capsys.readouterr().out
    assert "원문 절단(슬라이싱) 및 메타데이터 현황" in out
    assert "전체 검사 문서 수             : 4 건" in out
    assert "메타데이터 누락 절단 문서   : 2 건" in out
    assert "doc_unmarked_hash" in out

    # 2. JSON format
    ret_json = cli.main(["truncation-status", "--json"])
    assert ret_json == 0
    json_out = json.loads(capsys.readouterr().out)
    assert json_out["total_documents"] == 4
    assert json_out["unmarked_truncated_count"] == 2

    # 3. Alias truncation-scan
    ret_alias = cli.main(["truncation-scan", "--json"])
    assert ret_alias == 0
    json_alias = json.loads(capsys.readouterr().out)
    assert json_alias["unmarked_truncated_count"] == 2

    # 4. Single target by ID
    ret_single = cli.main(["truncation-status", "doc_unmarked_hash", "--json"])
    assert ret_single == 0
    json_single = json.loads(capsys.readouterr().out)
    assert json_single["total_documents"] == 1
    assert json_single["unmarked_items"][0]["id"] == "doc_unmarked_hash"


def test_cli_truncation_backfill(test_env, capsys):
    # 1. Dry run (default)
    ret_dry = cli.main(["truncation-backfill"])
    assert ret_dry == 0
    out_dry = capsys.readouterr().out
    assert "기본 Dry-Run 모드로 실행되어" in out_dry
    assert "doc_unmarked_hash" in out_dry

    # 2. Apply with --yes
    ret_apply = cli.main(["truncation-backfill", "--apply", "--yes", "--mark-refresh"])
    assert ret_apply == 0
    out_apply = capsys.readouterr().out
    assert "소급 갱신 완료: 총 2건 메타데이터 업데이트 완료" in out_apply
    assert "재수집 큐 등록 2건" in out_apply

    # 3. Verify status after backfill
    ret_check = cli.main(["truncation-status", "--json"])
    assert ret_check == 0
    json_check = json.loads(capsys.readouterr().out)
    assert json_check["unmarked_truncated_count"] == 0
    assert json_check["recorded_truncated_count"] == 3

    # 4. Alias backfill-truncation with JSON output
    ret_alias = cli.main(["backfill-truncation", "--apply", "--yes", "--json", "--force"])
    assert ret_alias == 0
    json_alias = json.loads(capsys.readouterr().out)
    assert json_alias["updated_count"] == 4
