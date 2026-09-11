"""Tests for PDF author and title reconciliation, DTP artifact rejection, and safe extraction."""

from claire.extract.classifier import (
    PaperClassificationResult,
    extract_author_heuristic,
    extract_title_heuristic,
    is_dtp_title,
    is_dtp_typesetter_artifact,
    reconcile_pdf_metadata,
)
from claire.ontology.base import Document


def test_dtp_typesetter_artifact_detection():
    # DTP usernames / designer IDs
    assert is_dtp_typesetter_artifact("park-sy") is True
    assert is_dtp_typesetter_artifact("kim-jh") is True
    assert is_dtp_typesetter_artifact("lee_ej") is True
    assert is_dtp_typesetter_artifact("admin") is True
    assert is_dtp_typesetter_artifact("designer") is True
    assert is_dtp_typesetter_artifact("typesetter") is True
    assert is_dtp_typesetter_artifact("조판") is True
    assert is_dtp_typesetter_artifact("편집") is True

    # With QuarkXPress / InDesign creator metadata
    quark_meta = {"Creator": "QuarkXPress(R) 22.0", "Producer": "QuarkXPress(R) 22.0"}
    assert is_dtp_typesetter_artifact("park-sy", quark_meta) is True
    assert is_dtp_typesetter_artifact("user1", quark_meta) is True

    # Legitimate authors must NOT be flagged as DTP artifacts
    assert is_dtp_typesetter_artifact("이보미 선임연구위원") is False
    assert is_dtp_typesetter_artifact("이보미") is False
    assert is_dtp_typesetter_artifact("홍길동") is False
    assert is_dtp_typesetter_artifact("John von Neumann") is False
    assert is_dtp_typesetter_artifact("Andrew Ng") is False
    assert is_dtp_typesetter_artifact(None) is False
    assert is_dtp_typesetter_artifact("") is False


def test_dtp_title_detection():
    # Issue / volume numbers used as temporary DTP layout filenames
    assert is_dtp_title("35 17") is True
    assert is_dtp_title("35 17 ") is True
    assert is_dtp_title("35-17") is True
    assert is_dtp_title("2024_01") is True
    assert is_dtp_title("untitled") is True
    assert is_dtp_title("Microsoft Word - Doc1") is True
    assert is_dtp_title("") is True
    assert is_dtp_title(None) is True

    # Real titles
    assert is_dtp_title("코스닥시장 세그먼트 개편: 일본의 시장구분 재편이 주는 시사점") is False
    assert is_dtp_title("Attention Is All You Need") is False


def test_extract_author_heuristic():
    sample_text = (
        "3\n"
        "Korea Institute of Finance\n"
        "2026.08.08. ~ 08.21.\n"
        "35권 17호\n"
        "금융브리프\n"
        "논단\n"
        "코스닥시장 세그먼트 개편:\n"
        "일본의 시장구분 재편이 주는 시사점\n"
        "이보미 선임연구위원 | 02-3705-6320\n"
        "요약\n"
        "정부는 2026년 3월..."
    )
    assert extract_author_heuristic(sample_text) == "이보미 선임연구위원"

    other_text = "저자: 홍길동 연구위원 (한국개발연구원)"
    assert extract_author_heuristic(other_text) == "홍길동 연구위원"

    prof_text = "김철수 교수, 서울대학교 경제학부"
    assert extract_author_heuristic(prof_text) == "김철수 교수"

    no_author_text = "이 문서는 일반적인 안내문입니다."
    assert extract_author_heuristic(no_author_text) is None


def test_extract_title_heuristic():
    sample_text = (
        "3\n"
        "Korea Institute of Finance\n"
        "2026.08.08. ~ 08.21.\n"
        "35권 17호\n"
        "금융브리프\n"
        "논단\n"
        "코스닥시장 세그먼트 개편:\n"
        "일본의 시장구분 재편이 주는 시사점\n"
        "이보미 선임연구위원 | 02-3705-6320\n"
    )
    title = extract_title_heuristic(sample_text)
    assert title is not None
    assert "코스닥시장 세그먼트 개편" in title


def test_reconcile_pdf_metadata_overrides_dtp_artifact_with_real_author():
    sample_text = (
        "Korea Institute of Finance\n"
        "35권 17호 금융브리프 논단\n"
        "코스닥시장 세그먼트 개편:\n"
        "일본의 시장구분 재편이 주는 시사점\n"
        "이보미 선임연구위원 | 02-3705-6320\n"
        "요약\n"
        "정부는 2026년 3월 자본시장 체질개선 방안을 발표..."
    )
    doc = Document(
        url="https://kif.re.kr/sample.pdf",
        title="35 17",
        author="park-sy",
        raw_text=sample_text,
        source_type="pdf",
        content_hash="dummyhash",
        meta={
            "pdf_metadata": {
                "Title": "35 17 ",
                "Author": "park-sy",
                "Creator": "QuarkXPress(R) 22.0",
            }
        },
    )

    res = reconcile_pdf_metadata(doc)
    assert isinstance(res, PaperClassificationResult)
    assert res.is_paper is True

    # Author must be reconciled to the real researcher
    assert doc.author == "이보미 선임연구위원"
    assert doc.meta.get("author_reconciled") is True
    assert doc.meta.get("meta_author_raw") == "park-sy"

    # Title must be reconciled from DTP layout filename '35 17' to the real paper title
    assert "코스닥시장 세그먼트 개편" in doc.title
    assert doc.meta.get("title_reconciled") is True
    assert doc.meta.get("meta_title_raw") == "35 17"


def test_reconcile_pdf_metadata_wipes_dtp_artifact_when_no_text_author():
    sample_text = (
        "일반적인 내부 업무 매뉴얼 문서입니다.\n"
        "시스템 로그인 및 계정 관리 가이드라인..."
    )
    doc = Document(
        url="https://example.com/manual.pdf",
        title="Doc1",
        author="park-sy",
        raw_text=sample_text,
        source_type="pdf",
        content_hash="dummyhash2",
        meta={
            "pdf_metadata": {
                "Author": "park-sy",
                "Creator": "QuarkXPress(R) 22.0",
            }
        },
    )

    res = reconcile_pdf_metadata(doc)
    # Since park-sy is a DTP artifact and no real text author was found,
    # author must be cleared to None to prevent KG entity pollution.
    assert doc.author is None
    assert doc.meta.get("author_rejected") == "dtp_typesetter_artifact"
    assert doc.meta.get("meta_author_raw") == "park-sy"


def test_reconcile_pdf_metadata_preserves_legitimate_author():
    sample_text = "This paper analyzes the macro impact of interest rate hikes."
    doc = Document(
        url="https://example.com/paper.pdf",
        title="Interest Rate Dynamics",
        author="John Doe",
        raw_text=sample_text,
        source_type="pdf",
        content_hash="dummyhash3",
        meta={},
    )

    reconcile_pdf_metadata(doc)
    # Legitimate author is retained
    assert doc.author == "John Doe"
