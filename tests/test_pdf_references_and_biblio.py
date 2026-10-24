"""PDF 논문 참고문헌(References) 제외, 서지 메타데이터 추출/예산 면제, 파서 폴백(pypdfium2/pypdf) 테스트."""

from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from claire.config import get_settings
from claire.extract.prompts import doc_to_prompt
from claire.ingest.fetchers.pdf import (
    PdfExtractResult,
    extract_pdf_bytes,
    extract_pdf_stream,
    find_references_split,
    slice_pdf_text,
)
from claire.ingest.fetchers.textfile import fetch_file
from claire.ingest.fetchers.web import fetch_web
from claire.ontology.base import Document


def test_find_references_split_patterns():
    """영문 및 국문, 마크다운 참고문헌 헤더 검출 검증."""
    # 1. 영문 References
    t1 = "Main paper contribution.\n\nReferences\n[1] Vaswani et al. 2017."
    res1 = find_references_split(t1)
    assert res1 is not None
    assert "References" in res1[1]
    assert t1[:res1[0]].rstrip() == "Main paper contribution."

    # 2. 영문 대문자 REFERENCES
    t2 = "Main text.\n\nREFERENCES:\n[1] Author, Title, 2020."
    res2 = find_references_split(t2)
    assert res2 is not None
    assert "REFERENCES" in res2[1]

    # 3. Bibliography
    t3 = "Historical analysis.\n\nBibliography\nSmith, J. (1999). Book Title."
    res3 = find_references_split(t3)
    assert res3 is not None
    assert "Bibliography" in res3[1]

    # 4. Works Cited
    t4 = "Literary essay.\n\nWorks Cited\nShakespeare, W. Hamlet."
    res4 = find_references_split(t4)
    assert res4 is not None
    assert "Works Cited" in res4[1]

    # 5. 국문 참고문헌
    t5 = "논문 본문입니다.\n\n참고문헌\n1. 홍길동 (2021). 연구 논문."
    res5 = find_references_split(t5)
    assert res5 is not None
    assert "참고문헌" in res5[1]

    t5b = "본문입니다.\n\n[참고문헌]\n1. 김철수 (2022)."
    res5b = find_references_split(t5b)
    assert res5b is not None
    assert "참고문헌" in res5b[1]

    # 6. 마크다운 헤더 (# References, ## 참고문헌)
    t6 = "Introduction and method.\n\n## References\n- Item 1\n- Item 2"
    res6 = find_references_split(t6)
    assert res6 is not None
    assert "References" in res6[1]

    t6b = "결론입니다.\n\n# 참고문헌\n1. 가나다"
    res6b = find_references_split(t6b)
    assert res6b is not None
    assert "참고문헌" in res6b[1]


def test_find_references_split_false_positives():
    """본문 문장 내 인라인 언급 시 절단 헤더로 오인하지 않는지 검증."""
    t1 = "As mentioned in the references [1, 2], our baseline achieves good accuracy.\nNext line."
    assert find_references_split(t1) is None

    t2 = "See references at the end of section 3 for more details."
    assert find_references_split(t2) is None

    t3 = "본 연구에서 참고문헌의 결과를 바탕으로 실험을 설계하였다."
    assert find_references_split(t3) is None


def test_slice_pdf_text_references_only():
    """부록 없이 참고문헌만 존재하는 경우 references_truncated=True 검증."""
    main_text = "Paper Core Body Paragraph. " * 300
    ref_text = "\n\nReferences\n" + ("[1] Author et al. Paper title.\n" * 100)
    full_text = main_text + ref_text

    sliced, is_trunc, app_trunc, ref_trunc, orig, raw = slice_pdf_text(
        full_text, limit=50000, exclude_appendix=True, exclude_references=True
    )
    assert is_trunc is True
    assert app_trunc is False
    assert ref_trunc is True
    assert orig == len(full_text)
    assert raw == len(main_text.rstrip())
    assert sliced == main_text.rstrip()
    assert "References" not in sliced


def test_slice_pdf_text_both_references_and_appendix():
    """참고문헌과 부록이 둘 다 존재하는 경우 선행 섹션 기준으로 본문만 보존되는지 검증."""
    main_text = "Paper Core Body Paragraph. " * 200
    ref_text = "\n\nReferences\n" + ("[1] Citation item.\n" * 50)
    app_text = "\n\nAppendix A. Extra Details\n" + ("Proof detail.\n" * 50)

    # Case 1: 본문 -> References -> Appendix 순서
    full_text1 = main_text + ref_text + app_text
    sliced1, is_trunc1, app_trunc1, ref_trunc1, orig1, raw1 = slice_pdf_text(
        full_text1, limit=50000, exclude_appendix=True, exclude_references=True
    )
    assert is_trunc1 is True
    assert ref_trunc1 is True
    assert app_trunc1 is True
    assert sliced1 == main_text.rstrip()

    # Case 2: 본문 -> Appendix -> References 순서
    full_text2 = main_text + app_text + ref_text
    sliced2, is_trunc2, app_trunc2, ref_trunc2, orig2, raw2 = slice_pdf_text(
        full_text2, limit=50000, exclude_appendix=True, exclude_references=True
    )
    assert is_trunc2 is True
    assert ref_trunc2 is True
    assert app_trunc2 is True
    assert sliced2 == main_text.rstrip()


def test_slice_pdf_text_exclude_references_disabled():
    """exclude_references=False 설정 시 참고문헌이 잘리지 않고 보존되는지 검증."""
    main_text = "Main paper."
    ref_text = "\n\nReferences\n[1] Kept reference."
    full_text = main_text + ref_text

    sliced, is_trunc, app_trunc, ref_trunc, orig, raw = slice_pdf_text(
        full_text, limit=50000, exclude_appendix=True, exclude_references=False
    )
    assert is_trunc is False
    assert ref_trunc is False
    assert sliced == full_text


def test_pdf_extract_has_no_biblio_pollution():
    """PDF 추출 시 임의 서지 메타데이터가 생성되지 않음을 검증."""
    res = PdfExtractResult("Test Paper", "Content text", [], {}, None, [])
    assert res.title == "Test Paper"
    assert res.text == "Content text"
    assert getattr(res, "biblio", {}) == {}


def test_doc_to_prompt_no_biblio_header_injection(monkeypatch: pytest.MonkeyPatch):
    """doc_to_prompt에서 서지 메타데이터가 프롬프트 헤더에 주입되지 않음을 검증."""
    monkeypatch.setenv("CLAIRE_PDF_MAX_EXTRACT_CHARS", "20000")
    monkeypatch.setenv("CLAIRE_EXTRACT_CHAR_BUDGET", "10000")
    get_settings.cache_clear()

    doc = Document(
        id="doc_paper_1",
        title="Attention Is All You Need",
        url="https://arxiv.org/abs/1706.03762",
        author=None,
        published_at=None,
        raw_text="Content " * 3000,
        source_type="pdf",
        meta={},
    )

    prompt = doc_to_prompt(doc)
    assert "TITLE: Attention Is All You Need" in prompt
    assert "AUTHORS:" not in prompt
    assert "PUBLISHED_AT:" not in prompt
    assert "DOI:" not in prompt
    assert "SOURCE_TYPE: pdf" in prompt

    assert "CONTENT:\n" in prompt
    body_part = prompt.split("CONTENT:\n", 1)[1]
    assert len(body_part) <= 20000


def test_fetch_file_with_references_and_no_biblio(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """fetch_file()로 PDF 수집 시 참고문헌 제외가 동작하고 doc.meta에 biblio가 소각되었음을 검증."""
    pdf_path = tmp_path / "paper_with_refs.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 dummy")

    main_text = "Research paper core findings."
    ref_text = "\n\nReferences\n[1] Reference 1.\n[2] Reference 2."
    full_content = main_text + ref_text

    fake_result = PdfExtractResult(
        "Paper With References",
        full_content,
        ["https://doi.org/10.1234/test"],
        {},
        None,
        [],
    )

    monkeypatch.setattr(
        "claire.ingest.fetchers.pdf.extract_pdf_bytes",
        lambda data, url=None, fallback_title=None, engine=None: fake_result,
    )

    doc = fetch_file(str(pdf_path))
    assert doc.source_type == "pdf"
    assert doc.raw_text == main_text
    assert doc.author is None
    assert doc.published_at is None
    assert doc.meta["references_truncated"] is True
    assert doc.meta["appendix_truncated"] is False
    assert "biblio" not in doc.meta


def test_pdf_parser_fallback_metadata_and_ingest_report(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """pypdfium2 실패 시 PdfExtractResult 및 Document.meta, IngestReport에 pypdf 폴백 사유가 기록되는지 검증."""
    import sqlite3
    from claire.ingest.pipeline import IngestReport
    from claire.store import db as dbm

    monkeypatch.setenv("CLAIRE_PDF_PARSER", "default")
    get_settings.cache_clear()

    def mock_pdfium_fail(stream, url=None, fallback_title=None):
        return PdfExtractResult(None, "", [], {}, "Corrupt PDF stream error", [], {})

    monkeypatch.setattr("claire.ingest.fetchers.pdf.extract_pdf_stream_pypdfium2", mock_pdfium_fail)
    monkeypatch.setattr(
        "claire.ingest.fetchers.pdf.extract_pdf_stream_pypdf",
        lambda stream, url=None, fallback_title=None: PdfExtractResult(
            "Fallback Paper", "Fallback body text from pypdf", [], {}, None, []
        ),
    )

    pdf_file = tmp_path / "corrupt_paper.pdf"
    pdf_file.write_bytes(b"%PDF-1.4 dummy")

    # 1. fetch_file 수집 시 doc.meta에 폴백 이력 기록 확인
    doc = fetch_file(str(pdf_file))
    assert doc.meta["pdf_parser_requested"] == "pypdfium2"
    assert doc.meta["pdf_parser_used"] == "pypdf"
    assert doc.meta["pdf_parser_fallback"] is True
    assert "Corrupt PDF stream" in doc.meta["pdf_parser_fallback_reason"]

    # 2. IngestReport 및 telegram_summary에 경과 보고 반영 확인
    report = IngestReport()
    report.title = doc.title
    report.source_type = "pdf"
    report.pdf_parser_fallback = doc.meta["pdf_parser_fallback"]
    report.pdf_parser_fallback_reason = doc.meta["pdf_parser_fallback_reason"]

    summary_text = report.telegram_summary()
    assert "⚠️ PDF 파서 대체 적재 (PyPDFium2 실패 → PyPDF)" in summary_text
    assert "Corrupt PDF stream error" in summary_text


def test_graphview_parser_fallback_badge():
    """웹 UI 및 document_detail API에서 pdf_parser_fallback 및 주황색 배지가 노출되는지 검증."""
    import sqlite3
    from claire.graphview import document_detail, render_graph_html
    from claire.store import db as dbm

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    dbm.init_db(conn)

    doc = Document(
        id="doc_fallback_test",
        title="Fallback Paper",
        url="https://example.com/fallback.pdf",
        raw_text="Extracted via pypdf fallback.",
        source_type="pdf",
        meta={
            "pdf_parser_requested": "pypdfium2",
            "pdf_parser_used": "pypdf",
            "pdf_parser_fallback": True,
            "pdf_parser_fallback_reason": "pypdfium2 런타임 오류: Corrupt stream",
        },
    )
    dbm.insert_document(conn, doc)

    # 1. document_detail API 응답 검증
    detail = document_detail(conn, "doc_fallback_test")
    assert detail is not None
    assert detail["pdf_parser_fallback"] is True
    assert "Corrupt stream" in detail["pdf_parser_fallback_reason"]

    # 2. HTML 렌더링에 CSS 및 태그 로직 포함 검증
    html = render_graph_html()
    assert "parser-fallback-tag" in html
    assert "PyPDFium2" in html

