"""진행률(ProgressReporter) 및 중단 보고서, 큐 대시보드 검증 테스트."""

from __future__ import annotations

import io
import sqlite3
import sys

import pytest

from claire import cli
from claire.config import Settings
from claire.ontology.base import Document
from claire.progress import ProgressReporter, track_batch_progress
from claire.status import build_queue_dashboard, build_status_text
from claire.store import db as dbm


def _db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    dbm.init_db(conn)
    return conn


def test_progress_reporter_basic_flow():
    buf = io.StringIO()
    reporter = ProgressReporter(task_name="테스트 작업", total=3, stream=buf)
    assert reporter.total == 3
    assert reporter.completed_count == 0

    # 1번째 아이템 시작 및 단계 업데이트
    with reporter.item(1, "doc_1", title="문서 1번", url="https://example.com/1") as on_step:
        on_step("요약 LLM 추출 중...", "model=gemini-2.5")
        on_step("엔티티 해소 중...", "후보 2건")
    assert reporter.completed_count == 1

    # 2번째 아이템
    with reporter.item(2, "doc_2", title="문서 2번") as on_step:
        on_step("본문 렌더링 중...")
    assert reporter.completed_count == 2

    # 3번째 아이템 실패
    with pytest.raises(RuntimeError):
        with reporter.item(3, "doc_3", title="문서 3번") as on_step:
            on_step("추출 중...")
            raise RuntimeError("Quota exceeded")
    assert reporter.failed_count == 1

    reporter.print_summary()
    output = buf.getvalue()
    assert "[1/3]" in output
    assert "문서 1번" in output
    assert "요약 LLM 추출 중..." in output
    assert "테스트 작업" in output
    assert "완료 2건 · 실패 1건" in output


def test_progress_reporter_interruption_report():
    buf = io.StringIO()
    reporter = ProgressReporter(task_name="문서 재생성", total=10, stream=buf)

    # 1~2 완료
    with reporter.item(1, "doc_1", title="문서 1"):
        pass
    with reporter.item(2, "doc_2", title="문서 2"):
        pass

    # 3번째에서 중단 발생
    try:
        with reporter.item(3, "doc_3", title="중단된 문서", url="https://example.com/3") as on_step:
            on_step("LLM 동일체 판정 (엔티티 해소)", "비교 대상: Python")
            raise KeyboardInterrupt()
    except KeyboardInterrupt:
        pass

    report = reporter.format_interruption_report(reason="KeyboardInterrupt (사용자 강제 종료 Ctrl+C)")
    assert "작업이 중단되었습니다" in report
    assert "KeyboardInterrupt" in report
    assert "doc_3" in report
    assert "중단된 문서" in report
    assert "https://example.com/3" in report
    assert "LLM 동일체 판정 (엔티티 해소)" in report
    assert "비교 대상: Python" in report
    assert "2/10건 완료" in report
    assert "8건 미처리" in report
    assert "데이터 보존" in report


def test_track_batch_progress_context_manager():
    buf = io.StringIO()
    with track_batch_progress("배치 테스트", 2, stream=buf) as reporter:
        with reporter.item(1, "d1", title="문서1"):
            pass
        with reporter.item(2, "d2", title="문서2"):
            pass

    out = buf.getvalue()
    assert "[1/2]" in out
    assert "[2/2]" in out


def test_track_batch_progress_keyboard_interrupt():
    buf = io.StringIO()
    with pytest.raises(KeyboardInterrupt):
        with track_batch_progress("배치 테스트", 5, stream=buf) as reporter:
            with reporter.item(1, "d1", title="성공 문서"):
                pass
            with reporter.item(2, "d2", title="중단 대상 문서") as on_step:
                on_step("심층 추론 중...", "effort=high")
                raise KeyboardInterrupt()

    out = buf.getvalue()
    assert "작업이 중단되었습니다" in out
    assert "중단 대상 문서" in out
    assert "심층 추론 중..." in out


def test_queue_dashboard_output(tmp_path):
    db_file = tmp_path / "test.db"
    s = Settings(db_path=str(db_file))
    conn = dbm.connect(db_file)
    dbm.init_db(conn)

    # 1. inbox 데이터 생성
    i1 = dbm.log_inbox(conn, source="cli", payload="https://example.com/failed", kind="url")
    dbm.update_inbox(conn, i1, status="failed", error="404 Not Found")
    i2 = dbm.log_inbox(conn, source="cli", payload="https://example.com/error", kind="url")
    dbm.update_inbox(conn, i2, status="error", error="Timeout")

    # 2. refresh 데이터 생성
    dbm.insert_document(conn, Document(id="doc_ref", title="갱신 문서", raw_text="짧음", content_hash="h1"))
    dbm.enqueue_refresh(conn, document_id="doc_ref", payload="https://example.com/ref", reason="thin_body")

    # 3. expand 데이터 생성
    dbm.insert_document(conn, Document(id="doc_exp", title="확장 문서", raw_text="본문", content_hash="h2"))
    dbm.enqueue_expand(conn, "doc_exp")

    conn.close()

    dash = build_queue_dashboard(s)
    assert "Claire 비동기 큐 & 작업 대시보드" in dash
    assert "raw_inbox" in dash
    assert "refresh_queue" in dash
    assert "expand_queue" in dash
    assert "https://example.com/failed" in dash
    assert "doc_ref" in dash
    assert "doc_exp" in dash
    assert "doc_exp" in dash

    # status 텍스트에도 expand_queue 반영 확인
    st = build_status_text(s, full=True)
    assert "[자동확장 큐]" in st


def test_queue_list_accepts_positional_queue_name(tmp_path, monkeypatch, capsys):
    db_file = tmp_path / "queue.db"
    conn = dbm.connect(db_file)
    dbm.init_db(conn)
    inbox_id = dbm.log_inbox(
        conn, source="cli", payload="https://example.com/failed", kind="url"
    )
    dbm.update_inbox(conn, inbox_id, status="failed", error="404 Not Found")
    conn.close()

    monkeypatch.setenv("CLAIRE_DB_PATH", str(db_file))
    cli.get_settings.cache_clear()
    try:
        assert cli.main(["queue", "list", "inbox"]) == 0
    finally:
        cli.get_settings.cache_clear()

    output = capsys.readouterr().out
    assert "raw_inbox" in output
    assert "https://example.com/failed" in output
    assert "[2]" not in output
    assert "[3]" not in output


def test_queue_list_requires_queue_name(capsys):
    assert cli.main(["queue", "list"]) == 2
    assert "조회할 큐를 지정" in capsys.readouterr().err
