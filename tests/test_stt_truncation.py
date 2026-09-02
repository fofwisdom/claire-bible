import json
import sqlite3
from unittest.mock import MagicMock

import pytest

from claire.store import db as dbm
from claire.graphview import document_detail
from claire import cli


@pytest.fixture
def mem_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    dbm.init_db(conn)
    yield conn
    conn.close()


def test_extract_stt_transcript_copyright_protection(mem_db):
    """영상 제공자의 기본 자막(비-STT)은 저작권 보호를 위해 extract_stt_transcript가 None을 반환해야 함."""
    # 비-STT 비디오 문서 (제작자 자막)
    raw_text = "발표자: Creator\n\n안녕하세요 자막입니다."
    meta = {
        "duration_sec": 300,
        "has_transcript": True,
        "transcript_segments": [],
        "is_stt": False,
    }
    res = dbm.extract_stt_transcript(raw_text, meta)
    assert res is None, "제작자 기본 자막 문서는 STT가 아니므로 None이어야 합니다."


def test_extract_stt_transcript_with_stt(mem_db):
    """자체 STT가 적용된 문서는 음성 전사 전문과 세그먼트를 정상 반환해야 함."""
    raw_text = (
        "발표자: Speaker\n\n"
        "[영상 음성 전사 (STT)]\n"
        "[00:00:05] 첫 번째 발화입니다.\n"
        "[00:00:15] 두 번째 발화입니다.\n\n"
        "[영상 설명]\n"
        "설명글"
    )
    meta = {
        "duration_sec": 20,
        "has_transcript": True,
        "transcript_segments": [
            {"start_sec": 5.0, "end_sec": 10.0, "text": "첫 번째 발화입니다."},
            {"start_sec": 15.0, "end_sec": 20.0, "text": "두 번째 발화입니다."},
        ],
        "is_stt": True,
    }
    res = dbm.extract_stt_transcript(raw_text, meta)
    assert res is not None
    assert res["is_stt"] is True
    assert "첫 번째 발화입니다." in res["text"]
    assert "설명글" not in res["text"], "다음 섹션 [영상 설명]은 분리되어 제외되어야 합니다."
    assert len(res["segments"]) == 2
    assert res["stt_truncated"] is False


def test_scan_stt_composite_truncation_and_detail(mem_db):
    """글자 수 상한 슬라이싱, 시간 갭(120초 이상), 본문 작성 여부 복합 판정 검증."""
    # 1. 온전 적재 STT 문서 (본문 작성됨)
    mem_db.execute(
        "INSERT INTO documents (id, title, url, source_type, raw_text, detail, meta) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            "doc_intact",
            "온전한 STT 문서",
            "https://youtube.com/v=intact",
            "video",
            "[영상 음성 전사 (STT)]\n[00:01:00] 내용",
            "# 상세 본문\n작성 완료",
            json.dumps({
                "is_stt": True,
                "duration_sec": 120,
                "transcript_segments": [{"start_sec": 0, "end_sec": 115, "text": "내용"}],
            }),
        ),
    )

    # 2. 글자 수 상한 슬라이싱된 STT 문서 (본문 미작성)
    mem_db.execute(
        "INSERT INTO documents (id, title, url, source_type, raw_text, detail, meta) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            "doc_sliced_no_detail",
            "슬라이싱 STT (본문 미작성)",
            "https://youtube.com/v=sliced1",
            "video",
            "[영상 음성 전사 (STT)]\n[00:01:00] 일부 내용",
            "",
            json.dumps({
                "is_stt": True,
                "stt_truncated": True,
                "orig_chars": 250000,
                "raw_chars": 200000,
                "duration_sec": 600,
                "transcript_segments": [{"start_sec": 0, "end_sec": 590, "text": "일부 내용"}],
            }),
        ),
    )

    # 3. 재생 시간 갭(120초 초과) STT 문서 (본문 작성 완료 ⚠️)
    mem_db.execute(
        "INSERT INTO documents (id, title, url, source_type, raw_text, detail, meta) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            "doc_gap_with_detail",
            "시간갭 STT (본문 작성 완료)",
            "https://youtube.com/v=gap1",
            "video",
            "[영상 음성 전사 (STT)]\n[00:05:00] 전반부 내용",
            "# 상세 본문\n작성 완료",
            json.dumps({
                "is_stt": True,
                "duration_sec": 1200,  # 20분
                "transcript_segments": [{"start_sec": 0, "end_sec": 300, "text": "5분까지만 전사됨"}],  # 900초(15분) 갭
            }),
        ),
    )
    mem_db.commit()

    scan = dbm.scan_stt_documents(mem_db)

    assert scan["total_documents"] == 3
    assert scan["stt_detected_count"] == 3
    assert scan["stt_intact_count"] == 1
    assert scan["stt_truncated_count"] == 2
    assert scan["stt_truncated_with_detail_count"] == 1

    truncated_with_detail = scan["stt_truncated_with_detail_items"]
    assert len(truncated_with_detail) == 1
    assert truncated_with_detail[0]["id"] == "doc_gap_with_detail"
    assert any("duration_gap" in r for r in truncated_with_detail[0]["stt_trunc_reasons"])


def test_backfill_stt_metadata_updates_stt_truncated(mem_db):
    """소급 갱신(backfill) 시 stt_truncated 메타데이터가 DB에 올바르게 기록되는지 확인."""
    # 메타에 is_stt 및 stt_truncated가 누락되었으나 세그먼트와 헤더로 STT 및 절단이 판정되는 레거시 문서
    mem_db.execute(
        "INSERT INTO documents (id, title, url, source_type, raw_text, detail, meta) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            "doc_legacy_trunc",
            "레거시 절단 문서",
            "https://youtube.com/v=legacy",
            "video",
            "[영상 음성 전사 (STT)]\n[00:01:00] 내용",
            "본문",
            json.dumps({
                "raw_truncated": True,
                "orig_chars": 300000,
                "raw_chars": 200000,
                "transcript_segments": [{"start_sec": 0, "end_sec": 100, "text": "내용"}],
            }),
        ),
    )
    mem_db.commit()

    res = dbm.backfill_stt_metadata(mem_db)
    assert res["updated_count"] == 1

    row = mem_db.execute("SELECT meta FROM documents WHERE id='doc_legacy_trunc'").fetchone()
    meta_updated = json.loads(row["meta"])
    assert meta_updated["is_stt"] is True
    assert meta_updated["stt_truncated"] is True


def test_graphview_document_detail_stt_and_truncation(mem_db):
    """graphview.document_detail API가 STT 전사 및 절단 정보를 정확히 제공하는지 확인."""
    mem_db.execute(
        "INSERT INTO documents (id, title, url, source_type, raw_text, detail, meta) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            "doc_view_test",
            "뷰어 테스트 문서",
            "https://youtube.com/v=test",
            "video",
            "[영상 음성 전사 (STT)]\n[00:00:05] 첫 문장\n[00:00:10] 두 번째 문장",
            "상세 내용",
            json.dumps({
                "is_stt": True,
                "duration_sec": 100,
                "raw_truncated": True,
                "transcript_segments": [
                    {"start_sec": 5.0, "end_sec": 8.0, "text": "첫 문장"},
                    {"start_sec": 10.0, "end_sec": 15.0, "text": "두 번째 문장"},
                ],
            }),
        ),
    )
    mem_db.commit()

    detail = document_detail(mem_db, "doc_view_test")
    assert detail["is_stt"] is True
    assert "첫 문장" in detail["stt_transcript"]
    assert len(detail["transcript_segments"]) == 2
    assert detail["stt_truncated"] is True


def test_cli_stt_scan_execution(monkeypatch, mem_db):
    """claire stt-scan CLI 커맨드 실행 검증."""
    mem_db.execute(
        "INSERT INTO documents (id, title, url, source_type, raw_text, detail, meta) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            "doc_cli_test",
            "CLI 테스트 영상",
            "https://youtube.com/v=clitest",
            "video",
            "[영상 음성 전사 (STT)]\n[00:01:00] 내용",
            "본문",
            json.dumps({
                "is_stt": True,
                "duration_sec": 1000,
                "transcript_segments": [{"start_sec": 0, "end_sec": 200, "text": "앞부분"}],
            }),
        ),
    )
    mem_db.commit()

    # CLI scan 호출
    mock_settings = MagicMock()
    mock_settings.db_file = ":memory:"

    monkeypatch.setattr(cli, "get_settings", lambda: mock_settings)
    monkeypatch.setattr(dbm, "connect", lambda _: mem_db)

    args = MagicMock()
    args.target = None
    args.doc_id = None
    args.json = False

    ret = cli.cmd_stt_scan(args)
    assert ret == 0
