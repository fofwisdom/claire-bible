"""STT 문서 메타데이터 소급 갱신(Backfill) 단위 테스트."""

import json
import sqlite3
from unittest.mock import patch
import pytest

from claire.ontology.base import Document
from claire.store import db as dbm
from claire.cli import main


@pytest.fixture
def memory_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    dbm.init_db(conn)
    return conn


def test_scan_stt_documents_detection_criteria(memory_db):
    # 1. 일반 웹 문서 (STT 아님)
    doc_web = Document(
        id="doc_web_1",
        title="Web Document",
        url="https://example.com/web",
        raw_text="일반 웹 본문 내용입니다.",
        source_type="web",
        meta={},
    )
    # 2. segments가 있는 STT 문서 (아직 is_stt 없음)
    doc_stt_seg = Document(
        id="doc_stt_seg",
        title="STT with Segments",
        url="https://example.com/video1",
        raw_text="비디오 전사 내용",
        source_type="video",
        meta={
            "transcript_segments": [{"start": 0.0, "end": 5.0, "text": "안녕하세요"}],
            "has_transcript": True,
        },
    )
    # 3. 본문 헤더에 '[영상 음성 전사 (STT)]' 포함된 문서
    doc_stt_header = Document(
        id="doc_stt_header",
        title="STT with Header",
        url="https://example.com/video2",
        raw_text="발표자: 홍길동\n\n[영상 음성 전사 (STT)]\n[00:01] 반갑습니다.",
        source_type="video",
        meta={},
    )
    # 4. 이미 is_stt: True가 기록된 문서
    doc_stt_marked = Document(
        id="doc_stt_marked",
        title="STT Marked",
        url="https://example.com/video3",
        raw_text="이미 마킹된 STT 문서",
        source_type="video",
        meta={"is_stt": True, "has_transcript": True},
    )
    # 5. 제작자가 직접 제공한 자막 문서 (STT 아님! has_transcript는 True지만 transcript_segments는 없음)
    doc_creator_sub = Document(
        id="doc_creator_sub",
        title="Creator Subtitles Video",
        url="https://example.com/video_creator",
        raw_text="발표자: 김철수\n\n[영상 자막]\n제작자가 직접 업로드한 정규 자막입니다.",
        source_type="video",
        meta={"has_transcript": True, "transcript_segments": []},
    )

    dbm.insert_document(memory_db, doc_web)
    dbm.insert_document(memory_db, doc_stt_seg)
    dbm.insert_document(memory_db, doc_stt_header)
    dbm.insert_document(memory_db, doc_stt_marked)
    dbm.insert_document(memory_db, doc_creator_sub)

    scan = dbm.scan_stt_documents(memory_db)
    assert scan["total_documents"] == 5
    assert scan["stt_detected_count"] == 3
    assert scan["recorded_stt_count"] == 1
    assert scan["unmarked_stt_count"] == 2

    unmarked_ids = {it["id"] for it in scan["unmarked_items"]}
    assert unmarked_ids == {"doc_stt_seg", "doc_stt_header"}
    assert "doc_creator_sub" not in unmarked_ids


def test_backfill_stt_metadata(memory_db):
    doc_stt = Document(
        id="doc_unmarked_stt",
        title="Unmarked STT Doc",
        url="https://example.com/video_unmarked",
        raw_text="[영상 음성 전사 (STT)]\n전사 본문",
        source_type="video",
        meta={"has_transcript": True, "transcript_segments": [{"start": 0.0, "end": 2.0, "text": "전사 본문"}]},
    )
    dbm.insert_document(memory_db, doc_stt)

    res = dbm.backfill_stt_metadata(memory_db)
    assert res["updated_count"] == 1

    row = dbm.get_document_row(memory_db, "doc_unmarked_stt")
    meta = json.loads(row["meta"])
    assert meta["is_stt"] is True
    assert meta["stt"] is True

    # 다시 실행하면 unmarked가 없으므로 0건 갱신
    res_second = dbm.backfill_stt_metadata(memory_db)
    assert res_second["updated_count"] == 0

    # force=True 실행 시 기존 기록 문서도 갱신
    res_force = dbm.backfill_stt_metadata(memory_db, force=True)
    assert res_force["updated_count"] == 1


def test_cli_stt_backfill_dry_run_and_apply(tmp_path, monkeypatch, capsys):
    test_db = tmp_path / "test.db"
    conn = sqlite3.connect(test_db)
    conn.row_factory = sqlite3.Row
    dbm.init_db(conn)

    doc = Document(
        id="doc_cli_test",
        title="CLI STT Test",
        url="https://example.com/cli_test",
        raw_text="[영상 음성 전사 (STT)]\n음성 전사 테스트",
        source_type="video",
        meta={"has_transcript": True, "transcript_segments": [{"start": 0.0, "end": 1.0, "text": "음성 전사 테스트"}]},
    )
    dbm.insert_document(conn, doc)
    conn.close()

    monkeypatch.setenv("CLAIRE_DB_PATH", str(test_db))
    from claire.config import get_settings
    get_settings.cache_clear()

    # 1. Dry run (기본)
    ret = main(["stt-backfill"])
    assert ret == 0
    out = capsys.readouterr().out
    assert "Dry-Run" in out
    assert "doc_cli_test" in out

    # DB 확인: 아직 is_stt 변경 없어야 함
    conn = sqlite3.connect(test_db)
    conn.row_factory = sqlite3.Row
    row = dbm.get_document_row(conn, "doc_cli_test")
    meta = json.loads(row["meta"])
    assert "is_stt" not in meta
    conn.close()

    # 2. Apply 실행
    ret = main(["stt-backfill", "--apply"])
    assert ret == 0
    out = capsys.readouterr().out
    assert "소급 적용 완료: 총 1건 갱신됨" in out

    # DB 확인: is_stt가 True로 반영됨
    conn = sqlite3.connect(test_db)
    conn.row_factory = sqlite3.Row
    row = dbm.get_document_row(conn, "doc_cli_test")
    meta = json.loads(row["meta"])
    assert meta.get("is_stt") is True
    conn.close()

    # 3. JSON 출력 테스트 (별칭 backfill-stt)
    ret = main(["backfill-stt", "--json", "--apply"])
    assert ret == 0
    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert parsed["scanned_total"] == 1
