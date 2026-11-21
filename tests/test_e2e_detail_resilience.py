"""End-to-End verification for document detail rendering resilience, fallback, and recovery."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from claire.api.server import create_app
from claire.core.config import Settings
from claire.extract.provider import MockProvider
from claire.ingest.pipeline import IngestReport
from claire.ingest.service import IngestService
from claire.ontology.base import Document
from claire.store import db as dbm
from claire.telegram_bot import _settle_status, _status_emoji

# The assertions of this module expect the Korean interface text (fixture in conftest.py).
pytestmark = pytest.mark.usefixtures("korean_interface")


def test_e2e_ingest_detail_timeout_to_telegram_and_web(tmp_path: Path):
    """E2E Scenario 1:
    대용량 문서 적재 도중 가독 본문(detail) 렌더링이 타임아웃/실패했을 때,
    1. IngestReport에 detail_rendered=False 및 상세 오류가 기록된다.
    2. 텔레그램 요약이 '✅ 적재 완료' 대신 '⚠️ 부분 적재 (본문 가독 상세 생성 실패)'로 경고한다.
    3. 반응 이모지가 '👍'가 아닌 '👎'로 매핑된다.
    4. 텔레그램 완료 상태 메시지에 '🔄 본문 재생성 (기존 원문 기준)' 원터치 버튼이 부착된다.
    5. 공유 웹페이지(GET /p?s=...)에 상세 본문 누락 안내 배너가 제공된다.
    """
    db_file = tmp_path / "claire_e2e.db"
    vault_dir = tmp_path / "vault"
    vault_dir.mkdir()

    settings = Settings(
        db_path=str(db_file),
        vault_path=str(vault_dir),
        data_dir=tmp_path,
        provider="mock",
        public_url="http://127.0.0.1:8765",
        environment="development",
        render_format="md",
    )

    class TimeoutDetailProvider(MockProvider):
        def render_detail(self, doc: Document, format: str = "md", focus: str | None = None, **kwargs) -> str:
            raise RuntimeError("detail rendering timed out after 300.0s")

    service = IngestService(settings=settings, provider=TimeoutDetailProvider())

    # Kimodo 테크니컬 리포트 모의 문서
    doc_payload = Document(
        title="2026 Kimodo: Scaling Controllable Human Motion Generation",
        raw_text="NVIDIA Kimodo diffusion model technical report with 58,000 characters...",
        source_type="pdf",
        url="https://research.nvidia.com/labs/sil/projects/kimodo/assets/kimodo_tech_report.pdf",
        content_hash="kimodo_hash_12345",
    )

    report = service.ingest(
        doc_payload.url,
        source="telegram",
        user_id=123456,
        chat_id=123456,
        prefetched=doc_payload,
    )

    # 1. IngestReport 상태 검증
    assert report.error is None
    assert report.detail_rendered is False
    assert "300.0s" in (report.detail_error or "")
    doc_id = report.document_id
    assert doc_id is not None

    # 2. 텔레그램 요약 메시지 검증 (허위 성공 방지)
    summary = report.telegram_summary()
    assert "⚠️ 부분 적재 (본문 가독 상세 생성 실패)" in summary
    assert "본문 가독 상세(detail)가 생성되지 못했습니다" in summary
    assert "300.0s" in summary
    assert "✅ 적재 완료" not in summary

    # 3. 텔레그램 반응 이모지 검증
    is_detail_failed = not report.detail_rendered
    emoji = _status_emoji(
        report.error,
        report.duplicate,
        stt_error=report.stt_error,
        detail_error=report.detail_error if is_detail_failed else None,
    )
    assert emoji == "👎"

    # 4. 텔레그램 상태 메시지 처리 및 복구 버튼 부착 검증
    class FakeStatusMsg:
        def __init__(self):
            self.edited_text = ""
            self.markup = None

        async def edit_text(self, text: str, reply_markup=None):
            self.edited_text = text
            self.markup = reply_markup

    class FakeOrigMsg:
        pass

    import asyncio

    fake_status = FakeStatusMsg()
    asyncio.run(
        _settle_status(
            fake_status,
            FakeOrigMsg(),
            summary,
            report.candidates,
            has_error=is_detail_failed,
            is_detail_failed=is_detail_failed,
            retry_doc_id=doc_id,
        )
    )

    assert "본문 가독 상세 생성 실패" in fake_status.edited_text
    assert fake_status.markup is not None
    keyboard = fake_status.markup.inline_keyboard
    assert keyboard[0][0].callback_data == f"rg:det:{doc_id}"
    assert "본문 재생성 (기존 원문 기준)" in keyboard[0][0].text
    assert keyboard[1][0].callback_data == f"rg:full:{doc_id}"

    # 5. DB 상태 및 웹 공유 페이지 검증
    conn = sqlite3.connect(str(db_file))
    conn.row_factory = sqlite3.Row
    assert dbm.get_document(conn, doc_id) is not None
    assert dbm.get_document_detail(conn, doc_id) is None

    share_token = dbm.create_doc_share(conn, doc_id)
    conn.close()

    app = create_app(settings)
    with TestClient(app, base_url=settings.public_url, raise_server_exceptions=False) as client:
        res = client.get(f"/p?s={share_token}")
        assert res.status_code == 200
        # HTML 템플릿에 본문 누락 안내 배너 및 재생성 안내 스크립트가 포함되어 있는지 검증
        assert "상세 본문 누락 안내" in res.text
        assert "./cb-manuscript app regenerate" in res.text
        assert "--detail --apply" in res.text


def test_e2e_one_touch_detail_regeneration_flow(tmp_path: Path):
    """E2E Scenario 3:
    본문 생성이 누락되었던 문서를 '🔄 본문 재생성 (기존 원문 기준)'을 통해 복구할 때,
    1. service.regenerate_components(doc_id=doc_id, detail=True, force=True) 호출 성공.
    2. DB의 document_detail 및 detail_format이 채워짐.
    3. 웹 공유 페이지(GET /p?s=...)에서 누락 경고 배너가 사라지고 정상 마크다운이 서빙됨.
    """
    db_file = tmp_path / "claire_recovery.db"
    vault_dir = tmp_path / "vault"
    vault_dir.mkdir()

    settings = Settings(
        db_path=str(db_file),
        vault_path=str(vault_dir),
        data_dir=tmp_path,
        provider="mock",
        public_url="http://127.0.0.1:8765",
        environment="development",
        render_format="md",
    )

    conn = sqlite3.connect(str(db_file))
    conn.row_factory = sqlite3.Row
    dbm.init_db(conn)

    doc_id = "doc_recover_999"
    raw_text = "Original Kimodo PDF text content stored safely in the database."
    conn.execute(
        """
        INSERT INTO documents (id, title, url, raw_text, source_type, detail, detail_format)
        VALUES (?, 'Kimodo Paper', 'https://example.com/kimodo.pdf', ?, 'pdf', NULL, 'md')
        """,
        (doc_id, raw_text),
    )
    conn.commit()

    share_token = dbm.create_doc_share(conn, doc_id)
    conn.close()

    # 복구 시점의 정상 렌더 제공자
    class RestoredProvider(MockProvider):
        def render_detail(self, doc: Document, format: str = "md", focus: str | None = None, **kwargs) -> str:
            return "== 복구된 Kimodo 기술 상세\n\n정상적으로 재생성된 가독 본문입니다."

    service = IngestService(settings=settings, provider=RestoredProvider())

    # 원터치 본문 재생성 트리거 (rg:det:doc_id 가 호출하는 서비스 엔드포인트)
    res = service.regenerate_components(doc_id=doc_id, detail=True, force=True)
    assert res.get("count", 0) > 0
    assert res.get("error") is None
    target_info = res["targets"][0]
    # The setting still says md, but the Markdown body format is closed (RD23): the new body is AsciiDoc.
    assert target_info.get("detail_format") == "adoc"
    assert target_info.get("error") is None

    # DB에 본문이 정상 저장되었는지 검증
    check_conn = sqlite3.connect(str(db_file))
    check_conn.row_factory = sqlite3.Row
    stored_detail = dbm.get_document_detail(check_conn, doc_id)
    check_conn.close()

    assert stored_detail is not None
    assert "복구된 Kimodo 기술 상세" in stored_detail

    # 웹 공유 페이지에서 재생성된 본문 데이터가 제공되는지 검증
    app = create_app(settings)
    with TestClient(app, base_url=settings.public_url, raise_server_exceptions=False) as client:
        page_res = client.get(f"/p?s={share_token}")
        assert page_res.status_code == 200
        # JSON 데이터 블록 내에 복구된 본문이 존재
        assert "복구된 Kimodo 기술 상세" in page_res.text
