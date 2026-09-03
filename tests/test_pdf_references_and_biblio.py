"""PDF 논문 참고문헌(References) 제외, 서지 메타데이터 추출/예산 면제, 선택형 파서(pypdf/docling) 테스트."""

from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from claire.config import get_settings
from claire.extract.prompts import doc_to_prompt
from claire.ingest.fetchers.pdf import (
    PdfExtractResult,
    extract_bibliographic_metadata,
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


def test_extract_bibliographic_metadata():
    """PDF 메타데이터 및 본문에서 저자, 일자, DOI, arXiv ID 추출 검증."""
    # 1. 메타데이터 딕셔너리 기반 추출
    pdf_meta = {
        "/Author": "Ashish Vaswani, Noam Shazeer",
        "/CreationDate": "D:20170612180000Z",
    }
    sample_text = (
        "Attention Is All You Need\n"
        "arXiv:1706.03762v5 [cs.CL]\n"
        "https://doi.org/10.48550/arXiv.1706.03762\n"
        "Abstract: The dominant sequence transduction models..."
    )
    biblio = extract_bibliographic_metadata(sample_text, pdf_meta)
    assert biblio["author"] == "Ashish Vaswani, Noam Shazeer"
    assert biblio["published_at"] == "2017-06-12"
    assert biblio["doi"] == "10.48550/arXiv.1706.03762"
    assert biblio["arxiv_id"] == "1706.03762v5"

    # 2. 메타데이터가 없고 텍스트 단서만 있는 경우
    text_only = "A Survey on LLMs. doi: 10.1145/3318464.3389700. In arXiv:2303.18223."
    biblio2 = extract_bibliographic_metadata(text_only, None)
    assert biblio2.get("author") is None
    assert biblio2["doi"] == "10.1145/3318464.3389700"
    assert biblio2["arxiv_id"] == "2303.18223"
    assert biblio2["published_at"] == "2023-03"  # arXiv ID에서 추정된 발행년월


def test_doc_to_prompt_biblio_budget_exemption(monkeypatch: pytest.MonkeyPatch):
    """doc_to_prompt에서 서지 메타데이터가 본문 글자 수 상한(limit)에 깎이지 않고 온전히 프롬프트 헤더에 주입되는지 검증."""
    monkeypatch.setenv("CLAIRE_PDF_MAX_EXTRACT_CHARS", "20000")
    monkeypatch.setenv("CLAIRE_EXTRACT_CHAR_BUDGET", "10000")
    get_settings.cache_clear()

    # 25,000자 본문과 서지 정보를 가진 Document
    doc = Document(
        id="doc_paper_1",
        title="Attention Is All You Need",
        url="https://arxiv.org/abs/1706.03762",
        author="Ashish Vaswani et al.",
        published_at="2017-06-12",
        raw_text="Content " * 3000,  # ~24,000자
        source_type="pdf",
        meta={
            "biblio": {
                "author": "Ashish Vaswani et al.",
                "published_at": "2017-06-12",
                "doi": "10.48550/arXiv.1706.03762",
                "arxiv_id": "1706.03762",
            }
        },
    )

    prompt = doc_to_prompt(doc)
    # 1. 헤더에 서지 메타데이터가 빠짐없이 주입되었는지 확인
    assert "TITLE: Attention Is All You Need" in prompt
    assert "AUTHORS: Ashish Vaswani et al." in prompt
    assert "PUBLISHED_AT: 2017-06-12" in prompt
    assert "DOI: 10.48550/arXiv.1706.03762" in prompt
    assert "ARXIV_ID: 1706.03762" in prompt
    assert "SOURCE_TYPE: pdf" in prompt

    # 2. 본문은 pdf_max_extract_chars(20000) 한도로 슬라이싱되되 헤더는 전혀 손상되지 않음
    assert "CONTENT:\n" in prompt
    body_part = prompt.split("CONTENT:\n", 1)[1]
    assert len(body_part) <= 20000


def test_fetch_file_with_references_and_biblio(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """fetch_file()로 PDF 수집 시 참고문헌 제외 및 서지 정보가 Document와 메타데이터에 기록되는지 검증."""
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
        biblio={
            "author": "John Doe",
            "published_at": "2024-01-15",
            "doi": "10.1234/test",
        },
    )

    monkeypatch.setattr(
        "claire.ingest.fetchers.pdf.extract_pdf_bytes",
        lambda data, url=None, fallback_title=None, engine=None: fake_result,
    )

    doc = fetch_file(str(pdf_path))
    assert doc.source_type == "pdf"
    assert doc.raw_text == main_text
    assert doc.author == "John Doe"
    assert doc.published_at == "2024-01-15"
    assert doc.meta["references_truncated"] is True
    assert doc.meta["appendix_truncated"] is False
    assert doc.meta["biblio"]["doi"] == "10.1234/test"


def test_pdf_parser_selection_docling_fallback(monkeypatch: pytest.MonkeyPatch):
    """CLAIRE_PDF_PARSER='docling' 설정 시 docling 미설치 환경에서 pypdf로 안전하게 자동 폴백하는지 검증."""
    monkeypatch.setenv("CLAIRE_PDF_PARSER", "docling")
    get_settings.cache_clear()

    # extract_pdf_stream_pypdf 모의
    with patch("claire.ingest.fetchers.pdf.extract_pdf_stream_pypdf") as mock_pypdf:
        mock_pypdf.return_value = PdfExtractResult("Fallback Title", "Pypdf Text", [], {}, None, [], {})

        # docling은 설치되지 않은 상태이거나 예외를 일으킴
        res = extract_pdf_bytes(b"%PDF-dummy", fallback_title="Test")
        assert res.title == "Fallback Title"
        assert res.text == "Pypdf Text"
        assert mock_pypdf.called


def test_pdf_parser_docling_mock(monkeypatch: pytest.MonkeyPatch):
    """docling DocumentConverter를 모킹하여 docling 엔진이 성공적으로 마크다운을 반환하는지 검증."""
    class FakeDoc:
        name = "document.pdf"
        def export_to_markdown(self):
            return "# Docling Multi-Column Title\n\n| Col1 | Col2 |\n|---|---|\n| Data1 | Data2 |\n\ndoi: 10.1234/docling"

    class FakeConvResult:
        document = FakeDoc()

    class FakeConverter:
        def convert(self, doc_stream):
            return FakeConvResult()

    fake_docling_mod = MagicMock()
    fake_docling_mod.DocumentConverter = FakeConverter
    fake_docling_mod.DocumentStream = lambda name, stream: stream

    with patch.dict("sys.modules", {
        "docling": fake_docling_mod,
        "docling.document_converter": fake_docling_mod,
        "docling.datamodel.base_models": fake_docling_mod,
    }):
        from claire.ingest.fetchers.pdf import extract_pdf_stream_docling
        stream = io.BytesIO(b"%PDF-dummy")
        res = extract_pdf_stream_docling(stream, fallback_title="Docling Test")
        assert res.title == "Docling Multi-Column Title"
        assert "| Col1 | Col2 |" in res.text
        assert res.biblio.get("doi") == "10.1234/docling"
