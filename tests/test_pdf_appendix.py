"""PDF 논문 부록(Appendix) 제외 정책 및 웹 UI 녹색 절단 안내 검증 테스트."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from claire.config import get_settings
from claire.graphview import document_detail, render_graph_html
from claire.ingest.fetchers.pdf import (
    find_appendix_split,
    slice_pdf_text,
)
from claire.ingest.fetchers.textfile import fetch_file
from claire.ingest.fetchers.web import fetch_web
from claire.ontology.base import Document
from claire.store import db as dbm


def test_find_appendix_split_patterns():
    """다양한 형식의 부록 헤더(영문/국문/마크다운) 검출 검증."""
    # 1. 영문 Appendix A
    t1 = "Main paper content.\n\nAppendix A. Proofs and Detailed Derivations\nProof 1..."
    res1 = find_appendix_split(t1)
    assert res1 is not None
    assert "Appendix A" in res1[1]
    assert t1[:res1[0]].rstrip() == "Main paper content."

    # 2. 영문 APPENDIX (대문자)
    t2 = "Main paper content.\n\nAPPENDIX: IMPLEMENTATION DETAILS\nDetails..."
    res2 = find_appendix_split(t2)
    assert res2 is not None
    assert "APPENDIX" in res2[1]

    # 3. Appendices 복수형
    t3 = "Main text.\n\nAppendices\nA. First\nB. Second"
    res3 = find_appendix_split(t3)
    assert res3 is not None
    assert "Appendices" in res3[1]

    # 4. Supplementary Material / Information
    t4 = "Main paper.\n\nSupplementary Material\nAdditional Figures and Tables"
    res4 = find_appendix_split(t4)
    assert res4 is not None
    assert "Supplementary Material" in res4[1]

    t4b = "Main paper.\n\nSUPPLEMENTARY INFORMATION\nData summary..."
    res4b = find_appendix_split(t4b)
    assert res4b is not None
    assert "SUPPLEMENTARY INFORMATION" in res4b[1]

    # 5. 국문 부록
    t5 = "논문 본문입니다.\n\n부록 1. 설문 조사 문항\n1. 귀하의 연령은..."
    res5 = find_appendix_split(t5)
    assert res5 is not None
    assert "부록 1" in res5[1]

    t5b = "논문 본문입니다.\n\n[부록] 기초 통계표\n표 1..."
    res5b = find_appendix_split(t5b)
    assert res5b is not None
    assert "부록" in res5b[1]

    t5c = "논문 본문입니다.\n\n부 록\n상세 내용..."
    res5c = find_appendix_split(t5c)
    assert res5c is not None
    assert "부 록" in res5c[1]

    # 6. 마크다운 헤더 (# Appendix, ## 부록)
    t6 = "Main text.\n\n## Appendix B: Additional Experimental Results\nResults..."
    res6 = find_appendix_split(t6)
    assert res6 is not None
    assert "Appendix B" in res6[1]

    t6b = "본문입니다.\n\n# 부록\n내용..."
    res6b = find_appendix_split(t6b)
    assert res6b is not None
    assert "부록" in res6b[1]


def test_find_appendix_split_false_positives():
    """본문 문장 내 인라인 언급 시 절단 지점으로 오인하지 않는지 검증."""
    # 문장 내 단순 언급
    t1 = "In this work, we mention in the appendix that our model achieves SOTA.\nNext line."
    assert find_appendix_split(t1) is None

    t2 = "See Appendix A for more details on the experimental setup."
    assert find_appendix_split(t2) is None

    t3 = "Please refer to supplementary material section 2."
    assert find_appendix_split(t3) is None

    t4 = "본 연구의 부록을 참고하면 산출식을 확인할 수 있다."
    assert find_appendix_split(t4) is None


def test_slice_pdf_text_pure_appendix_truncated():
    """본문이 예산 내에 있고 부록만 절단한 경우 appendix_truncated=True 검증."""
    main_text = "Paper Main Body Paragraph. " * 500  # ~13,500자
    appendix_text = "\n\nAppendix A. Additional Proofs\n" + ("Proof detail. " * 300)
    full_text = main_text + appendix_text

    sliced, is_trunc, app_trunc, ref_trunc, orig, raw = slice_pdf_text(
        full_text, limit=50000, exclude_appendix=True
    )
    assert is_trunc is True
    assert app_trunc is True
    assert ref_trunc is False
    assert orig == len(full_text)
    assert raw == len(main_text.rstrip())
    assert sliced == main_text.rstrip()
    assert "Appendix A" not in sliced


def test_slice_pdf_text_length_and_appendix_truncated():
    """본문 자체도 예산(limit)을 초과한 경우 appendix_truncated=False (일반 길이 절단) 검증."""
    main_text = "Paper Main Body Paragraph. " * 1500  # ~40,500자
    appendix_text = "\n\nAppendix A. Extra Information\n" + ("Extra details. " * 500)
    full_text = main_text + appendix_text

    # limit=20,000자로 본문도 잘리는 경우
    sliced, is_trunc, app_trunc, ref_trunc, orig, raw = slice_pdf_text(
        full_text, limit=20000, exclude_appendix=True
    )
    assert is_trunc is True
    assert app_trunc is False  # 본문도 잘렸으므로 순수 부록 절단이 아님
    assert orig == len(full_text)
    assert raw == 20000
    assert len(sliced) == 20000


def test_slice_pdf_text_no_appendix():
    """부록이 없는 PDF는 일반 슬라이싱 규칙을 따르는지 검증."""
    # 1. 예산 내
    short_text = "Standard paper without any appendix."
    s1, is_t1, app_t1, ref_t1, o1, r1 = slice_pdf_text(short_text, limit=50000)
    assert is_t1 is False
    assert app_t1 is False
    assert ref_t1 is False
    assert s1 == short_text

    # 2. 예산 초과
    long_text = "Long text without appendix. " * 2000
    s2, is_t2, app_t2, ref_t2, o2, r2 = slice_pdf_text(long_text, limit=10000)
    assert is_t2 is True
    assert app_t2 is False
    assert ref_t2 is False
    assert r2 == 10000


def test_fetch_file_pdf_appendix_exclusion(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """fetch_file()로 PDF 파일 수집 시 Appendix가 제외되고 메타데이터가 기록되는지 검증."""
    pdf_path = tmp_path / "paper_with_appendix.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 dummy")

    main_content = "This is the core contribution of the research paper."
    appendix_content = "\n\nAppendix A: Mathematical Derivations\nDetail theorem 1..."
    full_content = main_content + appendix_content

    monkeypatch.setattr(
        "claire.ingest.fetchers.pdf.extract_pdf_bytes",
        lambda data, url=None, fallback_title=None: (
            "Paper with Appendix",
            full_content,
            [],
            {},
            None,
            [],
        ),
    )

    doc = fetch_file(str(pdf_path))
    assert doc.source_type == "pdf"
    assert doc.raw_text == main_content
    assert doc.meta["raw_truncated"] is True
    assert doc.meta["appendix_truncated"] is True
    assert doc.meta["orig_chars"] == len(full_content)
    assert doc.meta["raw_chars"] == len(main_content)


def test_fetch_web_pdf_appendix_exclusion(monkeypatch: pytest.MonkeyPatch):
    """fetch_web()로 웹 PDF 수집 시 Appendix가 제외되고 메타데이터가 기록되는지 검증."""
    main_content = "Web downloaded research paper body with substantial content. " * 30
    appendix_content = "\n\nSupplementary Information\n" + ("Supplementary tables and extra data. " * 20)
    full_content = main_content + appendix_content

    monkeypatch.setattr(
        "claire.ingest.fetchers.web._fetch_static",
        lambda url: (
            "Web Paper Title",
            full_content,
            [],
            {},
            None,
            url,
            [],
            True,  # is_pdf=True
        ),
    )

    doc = fetch_web("https://example.com/paper.pdf")
    assert doc.source_type == "pdf"
    assert doc.raw_text == main_content.rstrip()
    assert doc.meta["raw_truncated"] is True
    assert doc.meta["appendix_truncated"] is True
    assert doc.meta["orig_chars"] == len(full_content)
    assert doc.meta["raw_chars"] == len(main_content.rstrip())


def test_pdf_full_content_bypasses_appendix_exclusion(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """full_content=True 전달 시 부록 제외 없이 전체 원문이 온전히 보존되는지 검증."""
    pdf_path = tmp_path / "paper_full.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 dummy")

    full_content = "Main paper.\n\nAppendix A\nComplete appendix text."
    monkeypatch.setattr(
        "claire.ingest.fetchers.pdf.extract_pdf_bytes",
        lambda data, url=None, fallback_title=None: (
            "Full Paper",
            full_content,
            [],
            {},
            None,
            [],
        ),
    )

    doc = fetch_file(str(pdf_path), full_content=True)
    assert doc.raw_text == full_content
    assert doc.meta["raw_truncated"] is False
    assert doc.meta["appendix_truncated"] is False
    assert doc.meta["orig_chars"] == len(full_content)


def test_pdf_exclude_appendix_disabled_via_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """CLAIRE_PDF_EXCLUDE_APPENDIX=false 설정 시 부록이 제외되지 않는지 검증."""
    monkeypatch.setenv("CLAIRE_PDF_EXCLUDE_APPENDIX", "false")
    get_settings.cache_clear()

    pdf_path = tmp_path / "paper_opt_out.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 dummy")

    full_content = "Main paper.\n\nAppendix A\nRetained appendix text."
    monkeypatch.setattr(
        "claire.ingest.fetchers.pdf.extract_pdf_bytes",
        lambda data, url=None, fallback_title=None: (
            "Opt Out Paper",
            full_content,
            [],
            {},
            None,
            [],
        ),
    )

    doc = fetch_file(str(pdf_path))
    assert doc.raw_text == full_content
    assert doc.meta["raw_truncated"] is False
    assert doc.meta["appendix_truncated"] is False
    get_settings.cache_clear()


def test_graphview_appendix_truncation_ui():
    """웹 UI 및 document_detail에서 appendix_truncated 및 녹색 스타일/툴팁이 노출되는지 검증."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    dbm.init_db(conn)

    doc = Document(
        id="doc_app_test",
        title="Appendix Truncated Doc",
        url="https://example.com/app-paper.pdf",
        raw_text="Main content only.",
        source_type="pdf",
        meta={
            "raw_truncated": True,
            "appendix_truncated": True,
            "orig_chars": 45000,
            "raw_chars": 20000,
        },
    )
    dbm.insert_document(conn, doc)

    # 1. document_detail API 응답 검증
    detail = document_detail(conn, "doc_app_test")
    assert detail is not None
    assert detail["raw_truncated"] is True
    assert detail["appendix_truncated"] is True
    assert detail["meta"]["appendix_truncated"] is True

    # 2. render_graph_html에 녹색 CSS 및 JS 로직 포함 검증
    html = render_graph_html()
    assert ".trunc-tag.trunc-appendix" in html
    assert "#3fb950" in html
    assert "원문의 부록(Appendix) 부분을 절단한 문서" in html
    assert "isAppTrunc" in html
