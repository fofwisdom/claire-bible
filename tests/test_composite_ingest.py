"""복합 적재(Composite Ingestion: 첨부 파일 + 하이퍼링크) 단위 및 통합 테스트."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from claire.config import Settings
from claire.ingest.composite import (
    compose_composite_document,
    fetch_composite_components,
)
from claire.ingest.service import IngestService
from claire.ontology.base import Document
from claire.store import db as dbm
from claire.telegram_bot import build_app, parse_caption_composite


def test_parse_caption_composite_variations():
    # 1. URL 단독
    url, focus, tid, full, effort = parse_caption_composite("https://youtube.com/watch?v=abc")
    assert url == "https://youtube.com/watch?v=abc"
    assert focus is None
    assert tid is None
    assert not full
    assert effort is None

    # 2. URL + 파이프 구분 초점
    url, focus, tid, full, effort = parse_caption_composite(
        "https://youtube.com/watch?v=abc | 인프라 아키텍처 중심"
    )
    assert url == "https://youtube.com/watch?v=abc"
    assert focus == "인프라 아키텍처 중심"
    assert tid is None

    # 3. 설명문 + URL + 플래그 + 테마 (다중 테마 모드 매니저 주입)
    class FakeThemeManager:
        def __init__(self):
            self.settings = SimpleNamespace(multi_theme=True)

        def get_theme(self, cand, strict=True):
            return SimpleNamespace(id=int(cand) if str(cand).isdigit() else 1)

    url, focus, tid, full, effort = parse_caption_composite(
        "세션 녹화 영상 https://youtube.com/watch?v=abc --effort high #1",
        theme_mgr=FakeThemeManager(),
    )
    assert url == "https://youtube.com/watch?v=abc"
    assert focus == "세션 녹화 영상"
    assert tid == 1
    assert effort == "high"
    assert not full

    # 4. URL + [초점] 접두어
    url, focus, tid, full, effort = parse_caption_composite(
        "https://example.com/doc [초점] 분산 캐시 설계 --full"
    )
    assert url == "https://example.com/doc"
    assert focus == "분산 캐시 설계"
    assert full is True

    # 5. URL 없음 (일반 파일 캡션)
    url, focus, tid, full, effort = parse_caption_composite("그냥 초점 텍스트")
    assert url is None
    assert focus == "그냥 초점 텍스트"
    assert tid is None

    # 6. 빈 값
    assert parse_caption_composite(None) == (None, None, None, False, None)
    assert parse_caption_composite("") == (None, None, None, False, None)


def test_compose_composite_document_video_and_pdf():
    video_doc = Document(
        id="doc_video_1",
        title="vLLM Architecture Deep Dive",
        url="https://youtube.com/watch?v=vllm123",
        canonical_url="https://youtube.com/watch?v=vllm123",
        source_type="video",
        raw_text="Hello, today we talk about PagedAttention and vLLM serving.",
        meta={"has_transcript": True, "is_stt": True, "caption_language": "en"},
    )
    pdf_doc = Document(
        id="doc_pdf_1",
        title="vLLM Slides",
        url="file://slides.pdf",
        canonical_url="file://slides.pdf",
        source_type="pdf",
        raw_text="Slide 1: PagedAttention KV Cache Management\nSlide 2: Benchmarks",
        meta={"pdf_parser_used": "Docling", "orig_chars": 60, "raw_chars": 60},
    )

    pdf_bytes = b"%PDF-1.4 test pdf content"
    composite = compose_composite_document(
        pdf_doc,
        video_doc,
        file_name="slides.pdf",
        focus="KV Cache 최적화",
        file_bytes=pdf_bytes,
    )

    assert composite.source_type == "video"
    assert composite.title == "vLLM Architecture Deep Dive"
    assert "PagedAttention and vLLM serving" in composite.raw_text
    assert "[발표자료 PDF — slides.pdf]" in composite.raw_text
    assert composite.meta.get("composite_ingest") is True
    assert composite.meta.get("focus") == "KV Cache 최적화"

    # Presentation PDF 메타데이터 검증
    pres = composite.meta.get("presentation_pdf")
    assert pres is not None
    assert pres["status"] == "available"
    assert pres["filename"] == "slides.pdf"
    assert pres["parser_used"] == "Docling"

    # Components 검증
    comps = composite.meta.get("content_components")
    assert len(comps) == 2
    assert comps[0]["kind"] == "transcript"
    assert comps[1]["kind"] == "presentation_pdf"

    # Extra sources 검증
    extra = composite.meta.get("extra_sources")
    assert len(extra) == 2
    assert any(s["url"] == "https://youtube.com/watch?v=vllm123" for s in extra)
    assert any(s["url"] == "file://slides.pdf" for s in extra)

    # Attachments 검증
    assert len(composite.attachments) == 1
    assert composite.attachments[0].kind == "presentation_pdf"
    assert composite.attachments[0].content == pdf_bytes


def test_compose_composite_document_web_and_pdf():
    web_doc = Document(
        id="doc_web_1",
        title="Transformer Explainer",
        url="https://example.com/transformer",
        canonical_url="https://example.com/transformer",
        source_type="web",
        raw_text="Transformers use self-attention to process sequential data.",
        meta={},
    )
    pdf_doc = Document(
        id="doc_pdf_2",
        title="Attention is All You Need",
        url="file://paper.pdf",
        canonical_url="file://paper.pdf",
        source_type="pdf",
        raw_text="The dominant sequence transduction models are based on complex recurrent networks.",
        meta={"pdf_parser_used": "PyPDF"},
    )

    composite = compose_composite_document(
        pdf_doc,
        web_doc,
        file_name="paper.pdf",
        focus="어텐션 메커니즘",
    )

    assert composite.source_type == "web"
    assert composite.title == "Transformer Explainer"
    assert "[발표자료 PDF — paper.pdf]" in composite.raw_text
    assert composite.meta.get("composite_ingest") is True
    assert composite.meta.get("presentation_pdf") is not None
    assert len(composite.meta.get("extra_sources")) == 2


def test_ingest_composite_service_end_to_end(tmp_path: Path):
    settings = Settings(
        data_dir=tmp_path / "data",
        vault_dir=tmp_path / "vault",
        db_file=tmp_path / "data" / "claire.db",
        vector_backend="sqlite",
        provider="mock",
    )
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.vault_dir.mkdir(parents=True, exist_ok=True)

    # 더미 파일 생성
    pdf_path = tmp_path / "test_presentation.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 dummy presentation bytes for test")

    svc = IngestService(settings)

    dummy_web_doc = Document(
        title="Kubernetes Scheduling Deep Dive",
        url="https://kubernetes.io/docs/concepts/scheduling-eviction/",
        canonical_url="https://kubernetes.io/docs/concepts/scheduling-eviction/",
        source_type="web",
        raw_text="In Kubernetes, scheduling refers to making sure that Pods are matched to Nodes.",
        meta={},
    )
    dummy_pdf_doc = Document(
        title="KubeCon Slides",
        url=f"file://{pdf_path}",
        canonical_url=f"file://{pdf_path}",
        source_type="pdf",
        raw_text="Slide 1: Kube-Scheduler internals\nSlide 2: Filter and Score plugins",
        meta={"pdf_parser_used": "PyPDF", "orig_chars": 60, "raw_chars": 60},
    )

    with patch("claire.ingest.composite.fetch_composite_components") as mock_fetch:
        mock_fetch.return_value = (dummy_pdf_doc, dummy_web_doc, pdf_path.read_bytes())
        report = svc.ingest_composite(
            pdf_path,
            "test_presentation.pdf",
            "https://kubernetes.io/docs/concepts/scheduling-eviction/",
            focus="스케줄링 플러그인 구조",
        )

    assert report.error is None
    assert report.document_id is not None
    assert report.presentation_pdfs == 1
    assert "스케줄링 플러그인 구조" in (report.focus or "")
    assert "발표자료 PDF 포함" in report.telegram_summary() or "STT×PDF" in report.telegram_summary() or "CC×PDF" in report.telegram_summary()

    # DB 저장 상태 검증
    conn = dbm.connect(settings.db_file)
    try:
        doc = dbm.get_document(conn, report.document_id)
        assert doc is not None
        assert doc.meta.get("composite_ingest") is True
        assert len(doc.meta.get("extra_sources", [])) == 2
        assert doc.meta.get("presentation_pdf") is not None

        # raw_inbox 검증
        inbox_row = dbm.get_inbox(conn, report.inbox_id)
        assert inbox_row is not None
        assert inbox_row["kind"] == "composite"
        assert inbox_row["status"] == "done"
        payload_data = json.loads(inbox_row["payload"])
        assert payload_data["url"] == "https://kubernetes.io/docs/concepts/scheduling-eviction/"
        assert payload_data["attachment_name"] == "test_presentation.pdf"
    finally:
        conn.close()


def test_composite_recovery_in_retry_and_recover_failed(tmp_path: Path):
    settings = Settings(
        data_dir=tmp_path / "data",
        vault_dir=tmp_path / "vault",
        db_file=tmp_path / "data" / "claire.db",
        vector_backend="sqlite",
        provider="mock",
    )
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.vault_dir.mkdir(parents=True, exist_ok=True)

    pdf_path = tmp_path / "failed_presentation.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 sample content")

    conn = dbm.connect(settings.db_file)
    dbm.init_db(conn)

    # 실패한 composite raw_inbox 행 직접 삽입
    payload_dict = {
        "url": "https://example.com/recovered-session",
        "attachment_name": "failed_presentation.pdf",
        "focus": "장애 복구 검증",
        "effort": None,
        "full_content": False,
        "theme_id": 0,
    }
    inbox_id = dbm.log_inbox(
        conn,
        source="telegram",
        payload=json.dumps(payload_dict),
        kind="composite",
        user_id=1,
        chat_id=1,
        file_name="failed_presentation.pdf",
        file_ref=str(pdf_path),
    )
    dbm.update_inbox(conn, inbox_id, status="error", error="Temporary network timeout")
    conn.close()

    svc = IngestService(settings)

    dummy_web_doc = Document(
        title="Recovered Session Title",
        url="https://example.com/recovered-session",
        canonical_url="https://example.com/recovered-session",
        source_type="web",
        raw_text="Successfully recovered web content.",
        meta={},
    )
    dummy_pdf_doc = Document(
        title="Recovered Slides",
        url=f"file://{pdf_path}",
        canonical_url=f"file://{pdf_path}",
        source_type="pdf",
        raw_text="Successfully recovered PDF slides.",
        meta={"pdf_parser_used": "PyPDF"},
    )

    with patch("claire.ingest.composite.fetch_composite_components") as mock_fetch:
        mock_fetch.return_value = (dummy_pdf_doc, dummy_web_doc, pdf_path.read_bytes())
        # 1. retry_inbox 수동 재시도 검증
        rep = svc.retry_inbox(inbox_id)
        assert rep.error is None
        assert rep.document_id is not None

    conn = dbm.connect(settings.db_file)
    try:
        row = dbm.get_inbox(conn, inbox_id)
        assert row["status"] == "done"
        assert row["document_id"] is not None
    finally:
        conn.close()

    # 2. recover_failed 자동복구 경로 검증
    conn2 = dbm.connect(settings.db_file)
    inbox_id2 = dbm.log_inbox(
        conn2,
        source="telegram",
        payload=json.dumps(payload_dict),
        kind="composite",
        user_id=1,
        chat_id=1,
        file_name="failed_presentation.pdf",
        file_ref=str(pdf_path),
    )
    dbm.update_inbox(conn2, inbox_id2, status="error", error="Temporary 500 error")
    conn2.close()

    with patch("claire.ingest.composite.fetch_composite_components") as mock_fetch:
        mock_fetch.return_value = (dummy_pdf_doc, dummy_web_doc, pdf_path.read_bytes())
        results = svc.recover_failed()

    assert len(results) >= 1
    assert any(r["inbox_id"] == inbox_id2 and r["status"] == "done" for r in results)


@pytest.mark.asyncio
async def test_telegram_on_document_routes_to_composite(tmp_path: Path):
    settings = Settings(
        telegram_bot_token="12345:fake_token_for_test",
        data_dir=tmp_path / "data",
        vault_dir=tmp_path / "vault",
        db_file=tmp_path / "data" / "claire.db",
        vector_backend="sqlite",
        provider="mock",
    )
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.vault_dir.mkdir(parents=True, exist_ok=True)

    app = build_app(settings)

    # Mock Telegram Update
    async def fake_download(dest):
        Path(dest).write_bytes(b"%PDF-1.4 dummy pdf bytes")

    mock_file = AsyncMock()
    mock_file.download_to_drive = fake_download

    mock_doc = MagicMock()
    mock_doc.file_name = "ai_infra.pdf"
    mock_doc.file_unique_id = "uniq_123"
    mock_doc.get_file = AsyncMock(return_value=mock_file)

    mock_msg = AsyncMock()
    mock_msg.document = mock_doc
    mock_msg.video = None
    mock_msg.audio = None
    mock_msg.caption = "https://youtube.com/watch?v=infra999 | 인프라 구조 분석"
    mock_msg.reply_text = AsyncMock()
    mock_msg.set_reaction = AsyncMock()

    status_msg = AsyncMock()
    mock_msg.reply_text.return_value = status_msg

    update = MagicMock()
    update.effective_user = MagicMock(id=100)
    update.effective_chat = MagicMock(id=200)
    update.message = mock_msg
    update.update_id = 9999

    with patch("claire.ingest.service.IngestService.ingest_composite") as mock_composite:
        from claire.ingest.pipeline import IngestReport
        mock_composite.return_value = IngestReport(
            document_id="doc_comp_1",
            title="Infra 999 Video",
            source_type="video",
            presentation_pdfs=1,
            summary="복합 적재 완료 요약",
        )

        # Call on_document handler
        for handler in app.handlers[0]:
            if getattr(handler, "callback", None) and handler.callback.__name__ == "on_document":
                await handler.callback(update, None)
                break

        assert mock_composite.called
        call_args = mock_composite.call_args
        assert call_args[0][1] == "ai_infra.pdf"  # file_name
        assert call_args[0][2] == "https://youtube.com/watch?v=infra999"  # url
        assert call_args[1]["focus"] == "인프라 구조 분석"


@pytest.mark.asyncio
async def test_telegram_on_document_single_file_fallback(tmp_path: Path):
    settings = Settings(
        telegram_bot_token="12345:fake_token_for_test",
        data_dir=tmp_path / "data",
        vault_dir=tmp_path / "vault",
        db_file=tmp_path / "data" / "claire.db",
        vector_backend="sqlite",
        provider="mock",
    )
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.vault_dir.mkdir(parents=True, exist_ok=True)

    app = build_app(settings)

    async def fake_download(dest):
        Path(dest).write_bytes(b"%PDF-1.4 dummy pdf bytes")

    mock_file = AsyncMock()
    mock_file.download_to_drive = fake_download

    mock_doc = MagicMock()
    mock_doc.file_name = "standalone.pdf"
    mock_doc.file_unique_id = "uniq_456"
    mock_doc.get_file = AsyncMock(return_value=mock_file)

    mock_msg = AsyncMock()
    mock_msg.document = mock_doc
    mock_msg.video = None
    mock_msg.audio = None
    mock_msg.caption = "단순 파일 초점: 성능 튜닝"
    mock_msg.reply_text = AsyncMock()
    mock_msg.set_reaction = AsyncMock()

    status_msg = AsyncMock()
    mock_msg.reply_text.return_value = status_msg

    update = MagicMock()
    update.effective_user = MagicMock(id=100)
    update.effective_chat = MagicMock(id=200)
    update.message = mock_msg
    update.update_id = 9998

    with patch("claire.ingest.service.IngestService.ingest") as mock_single, \
         patch("claire.ingest.service.IngestService.ingest_composite") as mock_composite:
        from claire.ingest.pipeline import IngestReport
        mock_single.return_value = IngestReport(
            document_id="doc_single_1",
            title="Standalone PDF",
            source_type="pdf",
            summary="단건 적재 완료",
        )

        for handler in app.handlers[0]:
            if getattr(handler, "callback", None) and handler.callback.__name__ == "on_document":
                await handler.callback(update, None)
                break

        assert not mock_composite.called
        assert mock_single.called
        assert mock_single.call_args[1]["file_name"] == "standalone.pdf"
        assert mock_single.call_args[1]["focus"] == "단순 파일 초점: 성능 튜닝"


def test_partial_failure_blocks_single_ingest_when_url_fails(tmp_path: Path):
    """원격 링크 수집 실패 시 첨부 파일만 단독 적재되는 것을 차단하고 전체 오류 처리."""
    from claire.ingest.fetchers.base import FetchError

    settings = Settings(
        data_dir=tmp_path / "data",
        vault_dir=tmp_path / "vault",
        db_file=tmp_path / "data" / "claire.db",
        vector_backend="sqlite",
        provider="mock",
    )
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.vault_dir.mkdir(parents=True, exist_ok=True)

    pdf_path = tmp_path / "valid_slides.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 valid content")

    svc = IngestService(settings)

    conn_init = dbm.connect(settings.db_file)
    initial_cnt = conn_init.execute("SELECT COUNT(*) as cnt FROM documents").fetchone()["cnt"]
    conn_init.close()

    with patch("claire.ingest.router.fetch", side_effect=FetchError("404 Not Found")):
        report = svc.ingest_composite(
            pdf_path,
            "valid_slides.pdf",
            "https://example.com/broken-link-404",
        )

    # 1. 문서가 생성되지 않아야 함
    assert report.error is not None
    assert "복합 자료 수집 실패" in report.error
    assert report.document_id is None

    # 2. DB에 신규 문서가 기록되지 않아야 함 (단독 적재 차단)
    conn = dbm.connect(settings.db_file)
    try:
        rows = conn.execute("SELECT COUNT(*) as cnt FROM documents").fetchone()
        assert rows["cnt"] == initial_cnt
        assert dbm.find_document_by_canonical_url(conn, "https://example.com/broken-link-404") is None

        # 3. raw_inbox에는 상태가 error로 기록되어야 함 (원터치 재시도/원인 보존)
        inbox = dbm.get_inbox(conn, report.inbox_id)
        assert inbox["status"] == "error"
        assert inbox["kind"] == "composite"
    finally:
        conn.close()


def test_partial_failure_blocks_single_ingest_when_file_fails(tmp_path: Path):
    """첨부 파일 파싱 실패(또는 빈 파일) 시 원격 링크만 단독 적재되는 것을 차단하고 전체 오류 처리."""
    settings = Settings(
        data_dir=tmp_path / "data",
        vault_dir=tmp_path / "vault",
        db_file=tmp_path / "data" / "claire.db",
        vector_backend="sqlite",
        provider="mock",
    )
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.vault_dir.mkdir(parents=True, exist_ok=True)

    # 0바이트 빈 파일 생성
    empty_file = tmp_path / "empty.pdf"
    empty_file.write_bytes(b"")

    svc = IngestService(settings)

    conn_init = dbm.connect(settings.db_file)
    initial_cnt = conn_init.execute("SELECT COUNT(*) as cnt FROM documents").fetchone()["cnt"]
    conn_init.close()

    dummy_web_doc = Document(
        title="Valid Web Page",
        url="https://example.com/valid",
        canonical_url="https://example.com/valid",
        source_type="web",
        raw_text="Valid web body text.",
    )

    with patch("claire.ingest.router.fetch", return_value=dummy_web_doc):
        report = svc.ingest_composite(
            empty_file,
            "empty.pdf",
            "https://example.com/valid",
        )

    assert report.error is not None
    assert "복합 자료 수집 실패" in report.error
    assert report.document_id is None

    conn = dbm.connect(settings.db_file)
    try:
        rows = conn.execute("SELECT COUNT(*) as cnt FROM documents").fetchone()
        assert rows["cnt"] == initial_cnt
        assert dbm.find_document_by_canonical_url(conn, "https://example.com/valid") is None
        inbox = dbm.get_inbox(conn, report.inbox_id)
        assert inbox["status"] == "error"
    finally:
        conn.close()

