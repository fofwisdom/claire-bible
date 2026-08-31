"""PDF 추출/보존 분량 일치화 및 무료 어댑터 기반 논문 판별/Effort 동적 적용 테스트."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from claire.config import Settings
from claire.extract.classifier import (
    classify_paper,
    get_free_or_default_provider,
    get_lowest_effort_provider,
)
from claire.extract.prompts import doc_to_prompt, classify_paper_prompt
from claire.extract.provider import ExtractionResult, MockProvider
from claire.ingest.fetchers.textfile import fetch_file
from claire.ingest.fetchers.web import fetch_web
from claire.ingest.pipeline import extract_resolve_store, ingest
from claire.ontology.base import Document
from claire.store import db as dbm
from claire.store.vectors import make_vector_store


def test_pdf_budget_in_fetch_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """fetch_file()에서 .pdf 파일은 raw_char_budget이 아닌 pdf_max_extract_chars 한도로 보존되는지 검증."""
    monkeypatch.setenv("CLAIRE_PDF_MAX_EXTRACT_CHARS", "40000")
    monkeypatch.setenv("CLAIRE_RAW_CHAR_BUDGET", "10000")
    from claire.config import get_settings
    get_settings.cache_clear()

    # 25,000자 텍스트 생성
    pdf_text = "A" * 25000
    pdf_path = tmp_path / "test_doc.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 dummy content")

    with patch("claire.ingest.fetchers.pdf.extract_pdf_bytes") as mock_extract:
        mock_extract.return_value = ("Test PDF Title", pdf_text, [], {}, None, [])
        doc = fetch_file(str(pdf_path))

    assert doc.source_type == "pdf"
    # raw_char_budget(10000)으로 잘리지 않고 25000자 전체가 보존되어야 함
    assert len(doc.raw_text) == 25000
    assert doc.meta["raw_chars"] == 25000
    assert not doc.meta["raw_truncated"]


def test_pdf_budget_in_fetch_web(monkeypatch: pytest.MonkeyPatch):
    """fetch_web()에서 PDF 웹 문서는 pdf_max_extract_chars 한도로 보존되는지 검증."""
    monkeypatch.setenv("CLAIRE_PDF_MAX_EXTRACT_CHARS", "40000")
    monkeypatch.setenv("CLAIRE_RAW_CHAR_BUDGET", "10000")
    from claire.config import get_settings
    get_settings.cache_clear()

    pdf_text = "B" * 30000
    with patch("claire.ingest.fetchers.web._fetch_static") as mock_static:
        mock_static.return_value = (
            "Web PDF Title",
            pdf_text,
            [],
            {},
            None,
            "https://example.com/paper.pdf",
            [],
        )
        doc = fetch_web("https://example.com/paper.pdf")

    assert doc.source_type == "pdf"
    # raw_char_budget(10000)이 아닌 30000자가 온전히 보존되어야 함
    assert len(doc.raw_text) == 30000
    assert not doc.meta["raw_truncated"]


def test_pdf_budget_in_fetch_web_query_url(monkeypatch: pytest.MonkeyPatch):
    """URL에 .pdf 확장자가 없는 쿼리 스트링 URL(예: KDB fileView)도 source_type='pdf' 및 50,000자 예산이 적용되는지 검증."""
    monkeypatch.setenv("CLAIRE_PDF_MAX_EXTRACT_CHARS", "50000")
    monkeypatch.setenv("CLAIRE_RAW_CHAR_BUDGET", "20000")
    from claire.config import get_settings
    get_settings.cache_clear()

    # 45,000자 대형 연구 보고서 텍스트
    report_text = "KDB 산은조사월보 스마트 건설 현황과 시사점 " * 1500  # ~45,000자
    query_url = "https://file.kdb.co.kr/fileView?groupId=F5A76F50-0120-6AEC-D82E-643C87B02A5D&fileId=4E3E5F35-13C7-41E6-25C6-AD8DB2BE1BDC"

    with patch("claire.ingest.fetchers.web._fetch_static") as mock_static:
        mock_static.return_value = (
            "스마트 건설 현황과 시사점",
            report_text,
            [],
            {},
            None,
            query_url,
            [],
            True,  # is_pdf=True
        )
        doc = fetch_web(query_url)

    assert doc.source_type == "pdf"
    assert doc.title == "스마트 건설 현황과 시사점"
    # raw_char_budget(20,000 / 25,000)으로 잘리지 않고 45,000자 전체가 보존되어야 함
    assert len(doc.raw_text) == len(report_text)
    assert doc.meta["raw_chars"] == len(report_text)
    assert doc.meta["raw_truncated"] is False


def test_pdf_extract_stream_full_page_and_true_orig_chars(monkeypatch: pytest.MonkeyPatch):
    """PDF 추출 시 페이지 루프에서 조기 중단하지 않고 전체 원문 길이를 측정하여 슬라이싱하는지 검증."""
    import pypdf
    from claire.ingest.fetchers.pdf import extract_pdf_bytes

    class MultiPage:
        def __init__(self, text: str):
            self._text = text

        def extract_text(self):
            return self._text

        def get(self, key):
            return None

    # 10개 페이지, 각 페이지 10,000자 -> 총 100,000자 PDF
    fake_pages = [MultiPage(f"Page {i}: " + "Z" * 9990) for i in range(10)]

    class FakeMultiPageReader:
        is_encrypted = False
        metadata = {"/Title": "Large PDF Paper"}
        pages = fake_pages

    monkeypatch.setattr(pypdf, "PdfReader", lambda stream: FakeMultiPageReader())
    monkeypatch.setenv("CLAIRE_PDF_MAX_EXTRACT_CHARS", "30000")
    monkeypatch.setenv("CLAIRE_RAW_CHAR_BUDGET", "10000")
    from claire.config import get_settings
    get_settings.cache_clear()

    title, full_text, links, anchors, err, imgs = extract_pdf_bytes(b"%PDF-dummy")
    assert err is None
    # extract_pdf_bytes는 30,000자에서 중간에 멈추지 않고 100,000자 전체를 추출하여 반환해야 함
    assert len(full_text) >= 90000

    # fetch_file이나 fetch_web을 통할 때 30,000자로 슬라이싱되되, orig_chars는 100,000자 전체로 기록되어야 함
    with patch("claire.ingest.fetchers.web._fetch_static") as mock_static:
        mock_static.return_value = (
            "Large PDF Paper",
            full_text,
            [],
            {},
            None,
            "https://example.com/large.pdf",
            [],
        )
        doc = fetch_web("https://example.com/large.pdf")

    assert doc.meta["raw_truncated"] is True
    assert doc.meta["raw_chars"] == 30000
    assert doc.meta["orig_chars"] == len(full_text)
    assert len(doc.raw_text) == 30000


def test_doc_to_prompt_pdf_limit(monkeypatch: pytest.MonkeyPatch):
    """_doc_to_prompt()에서 PDF 문서는 pdf_max_extract_chars 한도로 프롬프트에 투입되는지 검증."""
    monkeypatch.setenv("CLAIRE_PDF_MAX_EXTRACT_CHARS", "50000")
    monkeypatch.setenv("CLAIRE_EXTRACT_CHAR_BUDGET", "20000")
    from claire.config import get_settings
    get_settings.cache_clear()

    # 35,000자 PDF 문서
    pdf_doc = Document(
        id="pdf_1",
        title="PDF Paper",
        raw_text="P" * 35000,
        source_type="pdf",
    )
    prompt_pdf = doc_to_prompt(pdf_doc)
    assert "P" * 35000 in prompt_pdf

    # 35,000자 일반 웹 문서
    web_doc = Document(
        id="web_1",
        title="Web Article",
        raw_text="W" * 35000,
        source_type="web",
    )
    prompt_web = doc_to_prompt(web_doc)
    # 일반 웹 문서는 extract_char_budget(20000) 한도로 잘려야 함
    assert "W" * 35000 not in prompt_web
    assert "W" * 20000 in prompt_web


def test_truncation_scan_pdf_budget(monkeypatch: pytest.MonkeyPatch):
    """db.scan_truncation_status에서 PDF 문서는 pdf_max_extract_chars를 기준으로 판정하는지 검증."""
    monkeypatch.setenv("CLAIRE_PDF_MAX_EXTRACT_CHARS", "50000")
    monkeypatch.setenv("CLAIRE_RAW_CHAR_BUDGET", "20000")
    from claire.config import get_settings
    get_settings.cache_clear()

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    dbm.init_db(conn)

    # 20,000자인 PDF 문서 (raw_char_budget과 같지만 pdf_max_extract_chars인 50,000보다 작으므로 intact)
    doc_pdf = Document(
        id="doc_pdf_1",
        title="NBER Paper",
        raw_text="X" * 20000,
        source_type="pdf",
    )
    dbm.insert_document(conn, doc_pdf)

    scan = dbm.scan_truncation_status(conn)
    assert scan["intact_count"] == 1
    assert scan["unmarked_truncated_count"] == 0
    conn.close()


def test_lowest_effort_provider_selection(monkeypatch: pytest.MonkeyPatch):
    """여러 프로바이더 선언 시 effort 레벨이 가장 낮은 프로바이더를 선택하는지 검증."""
    # Case 1: agy effort=high, gemini effort=low -> Gemini 선택
    s1 = Settings(provider="gemini", gemini_api_key="dummy-key", agy_effort="high", gemini_effort="low")
    with patch("claire.extract.classifier.find_agy_executable", return_value="/usr/local/bin/agy"):
        prov1 = get_lowest_effort_provider(s1)
        assert prov1.name == "gemini"

    # Case 2: agy effort=low, gemini effort=high -> Antigravity 선택
    s2 = Settings(provider="gemini", gemini_api_key="dummy-key", agy_effort="low", gemini_effort="high")
    with patch("claire.extract.classifier.find_agy_executable", return_value="/usr/local/bin/agy"):
        prov2 = get_lowest_effort_provider(s2)
        assert prov2.name == "antigravity"

    # Case 3: 동점 (둘 다 medium) -> Antigravity 우선 선택 (무료/로컬 어댑터)
    s3 = Settings(provider="gemini", gemini_api_key="dummy-key", agy_effort="medium", gemini_effort="medium")
    with patch("claire.extract.classifier.find_agy_executable", return_value="/usr/local/bin/agy"):
        prov3 = get_lowest_effort_provider(s3)
        assert prov3.name == "antigravity"

    # Case 4: agy 없음, Gemini만 선언됨 -> Gemini 선택
    s4 = Settings(provider="gemini", gemini_api_key="dummy-key", gemini_effort="high")
    with patch("claire.extract.classifier.find_agy_executable", return_value=None):
        prov4 = get_lowest_effort_provider(s4)
        assert prov4.name == "gemini"

    # Case 5: provider="mock" -> MockProvider 선택
    s5 = Settings(provider="mock")
    prov5 = get_lowest_effort_provider(s5)
    assert prov5.name == "mock"


def test_classify_paper_logic():
    """classify_paper()가 학술 논문 여부를 올바르게 식별하는지 검증."""
    mock_prov = MockProvider()
    paper_doc = Document(
        title="Attention Is All You Need (arXiv:1706.03762)",
        raw_text="Abstract: We propose a new simple network architecture, the Transformer...",
        source_type="pdf",
    )
    is_paper, reason = classify_paper(paper_doc, provider=mock_prov)
    assert is_paper is True

    manual_doc = Document(
        title="User Manual and Quick Start Guide",
        raw_text="Chapter 1: Installation and Setup...",
        source_type="pdf",
    )
    is_paper, reason = classify_paper(manual_doc, provider=mock_prov)
    assert is_paper is False


def test_adaptive_effort_in_pipeline(monkeypatch: pytest.MonkeyPatch):
    """15,000자 이상 논문은 effort='high', 15,000자 미만 또는 비논문은 env 기반 effort로 동작하는지 검증."""
    monkeypatch.setenv("CLAIRE_PDF_PAPER_THRESHOLD_CHARS", "15000")
    monkeypatch.setenv("CLAIRE_PDF_PAPER_EFFORT", "high")
    monkeypatch.setenv("CLAIRE_PDF_DEFAULT_EFFORT", "medium")

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    dbm.init_db(conn)
    vstore = make_vector_store(conn, "mock")

    # Provider 모의
    mock_prov = MagicMock()
    mock_prov.name = "mock"
    mock_prov.effort = "medium"
    mock_prov.extract.return_value = ExtractionResult(
        summary="테스트 요약",
        entities=[],
        relations=[],
    )
    mock_prov.render_detail.return_value = "상세 내용"

    # Case 1: 20,000자 논문 PDF -> effort="high"
    doc_long_paper = Document(
        id="doc_p1",
        title="NBER Working Paper on AI",
        raw_text="Abstract\n" + "A" * 20000,
        source_type="pdf",
    )
    report1 = ingest(
        "dummy",
        conn=conn,
        provider=mock_prov,
        vstore=vstore,
        prefetched=doc_long_paper,
    )
    assert report1.error is None
    # provider.extract 호출 시 effort="high"가 전달되었는지 확인
    assert mock_prov.extract.call_args.kwargs.get("effort") == "high"

    mock_prov.extract.reset_mock()

    # Case 2: 8,000자 짧은 논문 PDF -> effort="medium" (env 기본값)
    doc_short_paper = Document(
        id="doc_p2",
        title="Short arXiv Preprint",
        raw_text="Abstract\n" + "B" * 8000,
        source_type="pdf",
    )
    report2 = ingest(
        "dummy2",
        conn=conn,
        provider=mock_prov,
        vstore=vstore,
        prefetched=doc_short_paper,
    )
    assert report2.error is None
    assert mock_prov.extract.call_args.kwargs.get("effort") == "medium"

    mock_prov.extract.reset_mock()

    # Case 3: 20,000자 일반 매뉴얼 PDF -> effort="medium" (env 기본값)
    doc_long_manual = Document(
        id="doc_p3",
        title="Device User Guide",
        raw_text="Chapter 1\n" + "C" * 20000,
        source_type="pdf",
    )
    with patch("claire.extract.classifier.classify_paper", return_value=(False, "not a paper")):
        report3 = ingest(
            "dummy3",
            conn=conn,
            provider=mock_prov,
            vstore=vstore,
            prefetched=doc_long_manual,
        )
    assert report3.error is None
    assert mock_prov.extract.call_args.kwargs.get("effort") == "medium"

    conn.close()
