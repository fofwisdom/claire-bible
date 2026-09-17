"""상세 본문 선두 서지 행의 중복 원문 링크 제거 정책 시험."""

from __future__ import annotations

import pytest

from claire.extract.prompts import (
    remove_leading_original_link,
    render_detail_prompt,
)
from claire.ingest.pipeline import ensure_document_detail
from claire.ontology.base import Document
from claire.store import db as dbm


@pytest.mark.parametrize("format", ["md", "adoc"])
def test_render_detail_prompt_forbids_forcing_biblio_in_body(format: str):
    prompt = render_detail_prompt(
        "URL: https://example.com/article\n\nCONTENT:\n본문",
        [],
        merged=False,
        format=format,
    )

    assert "서지 정보 표기" not in prompt
    assert "저자(AUTHORS)" not in prompt


@pytest.mark.parametrize(
    ("detail", "expected"),
    [
        (
            "= 제목\n_저자: Kim | 출처: Example (https://example.com[원문])_\n\n'''\n\n== 개요\n본문",
            "= 제목\n_저자: Kim | 출처: Example_\n\n'''\n\n== 개요\n본문",
        ),
        (
            "= 제목\n\n_출처: Example | URL: https://example.com[원문]_\n\n'''\n\n== 개요\n본문",
            "= 제목\n\n_출처: Example_\n\n'''\n\n== 개요\n본문",
        ),
        (
            "# 제목\n\n> 저자: Kim | 발행일: 2026-09-03 | 출처: [Example](https://example.com)\n\n---\n\n## 개요\n본문",
            "# 제목\n\n> 저자: Kim | 발행일: 2026-09-03 | 출처: Example\n\n---\n\n## 개요\n본문",
        ),
        (
            "출처: [Example](https://example.com)\n\n본문",
            "출처: Example\n\n본문",
        ),
    ],
)
def test_remove_leading_original_link(detail: str, expected: str):
    assert remove_leading_original_link(detail) == expected


def test_remove_leading_original_link_preserves_body_links_and_quotes():
    detail = (
        "= 제목\n\n== 배경\n"
        "본문의 https://example.com[참고 링크]는 문맥상 필요하다.\n\n"
        "[quote, 저자/출처]\n____\n핵심 선언이다.\n____"
    )

    assert remove_leading_original_link(detail) == detail


def test_remove_leading_original_link_preserves_leading_prose_with_url_label():
    detail = "URL:이라는 필드는 주소를 나타낸다. 이 문장은 출처 메타데이터 행이 아니다."

    assert remove_leading_original_link(detail) == detail


def test_remove_leading_original_link_preserves_biblio_without_link():
    detail = (
        "= 제목\n\n"
        "_저자: Simon Sharwood | 발행일: 2026-09-02 | "
        "출처: The Register (Exclusive)_\n\n'''\n\n== 개요\n본문"
    )

    assert remove_leading_original_link(detail) == detail


def test_ensure_document_detail_cleans_before_storage(tmp_path):
    conn = dbm.connect(tmp_path / "claire.db")
    dbm.init_db(conn)
    doc = Document(id="doc-source-header", title="제목", raw_text="본문")
    dbm.insert_document(conn, doc)

    class ProviderWithSourceHeader:
        effort = "medium"

        def render_detail(self, doc, format="adoc", focus=None, effort=None):
            return (
                "= 제목\n"
                "_저자: Kim | 출처: Example (https://example.com[원문])_\n\n"
                "'''\n\n== 개요\n본문"
            )

    assert ensure_document_detail(
        conn, ProviderWithSourceHeader(), doc, format="adoc"
    )
    assert dbm.get_document_detail(conn, doc.id) == (
        "= 제목\n_저자: Kim | 출처: Example_\n\n'''\n\n== 개요\n본문"
    )
    conn.close()


def test_recompile_all_detail_html_cleans_existing_documents(tmp_path):
    db_file = tmp_path / "claire.db"
    conn = dbm.connect(db_file)
    dbm.init_db(conn)
    detail = (
        "= 제목\n"
        "_출처: Example | URL: https://example.com[원문]_\n\n"
        "'''\n\n== 개요\n본문"
    )
    conn.execute(
        "INSERT INTO documents "
        "(id, title, raw_text, detail, detail_format, detail_html) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("doc-existing", "제목", "본문", detail, "adoc", "<p>stale source</p>"),
    )
    conn.commit()

    # 조회 시점에는 런타임 우회나 몽키패칭 없이 DB 캐시를 그대로 반환 (O(1))
    assert dbm.get_document_detail(conn, "doc-existing") == detail
    assert dbm.get_document_detail_html(conn, "doc-existing") == "<p>stale source</p>"

    # 마이그레이션 / 재컴파일 실행 시 일괄 정제되어 영구 반영
    updated = dbm.recompile_all_detail_html(conn)
    assert updated == 1

    expected_detail = "= 제목\n_출처: Example_\n\n'''\n\n== 개요\n본문"
    assert dbm.get_document_detail(conn, "doc-existing") == expected_detail
    html = dbm.get_document_detail_html(conn, "doc-existing")
    assert html is not None
    assert "stale source" not in html
    assert "출처: Example" in html
    assert "<h2>개요</h2>" in html

    stored = conn.execute(
        "SELECT detail, detail_html FROM documents WHERE id='doc-existing'"
    ).fetchone()
    assert stored["detail"] == expected_detail
    assert stored["detail_html"] == html
    conn.close()


def test_render_detail_prompt_explicitly_forbids_author_lines():
    """상세 프롬프트가 제목 직후 저자/부제 행 작성 및 플랫폼 인용을 명시적으로 금지하는지 검증."""
    prompt_adoc = render_detail_prompt(
        "CONTENT:\n본문", [], merged=False, format="adoc"
    )
    assert "문서 레벨 서지 정보 및 저자 라인 표기 절대 금지" in prompt_adoc
    assert "단순 비디오 채널/호스팅 플랫폼/업로더 계정명은 출처로 인용하지 않는다" in prompt_adoc

    prompt_md = render_detail_prompt(
        "CONTENT:\n본문", [], merged=False, format="md"
    )
    assert "문서 레벨 서지 정보 및 부제 행 표기 절대 금지" in prompt_md


def test_sanitize_rendered_detail_strips_adoc_bare_author_and_quotes():
    """AsciiDoc 제목 아래의 비인가 저자/부제 행 및 플랫폼 인용 블록 소각 검증."""
    from claire.extract.prompts import sanitize_rendered_detail

    # 1. 단일 채널/부제 행 제거 및 인용 소각
    dirty_detail_1 = (
        "= VMware Cloud Foundation 9.1\n"
        "Orbrium 파트너 테크 데이 기술 브리핑 지식 문서\n"
        ":toc: macro\n\n"
        "[quote, Orbrium 기술 세미나]\n"
        "인프라의 핵심이다."
    )
    cleaned_1 = sanitize_rendered_detail(dirty_detail_1, format="adoc")
    assert cleaned_1 == (
        "= VMware Cloud Foundation 9.1\n"
        ":toc: macro\n\n"
        "[quote]\n"
        "인프라의 핵심이다."
    )

    # 2. 복합 저자/소속 텍스트 행 제거
    dirty_detail_2 = (
        "= VCF 9.1 ANS 기술 가이드\n"
        "Orbrium; 허재홍 (Broadcom ANS BU)\n"
        ":toc:\n\n"
        "== 개요\n본문"
    )
    cleaned_2 = sanitize_rendered_detail(dirty_detail_2, format="adoc")
    assert cleaned_2 == "= VCF 9.1 ANS 기술 가이드\n:toc:\n\n== 개요\n본문"


def test_doc_to_prompt_does_not_inject_author_header():
    """Document 객체에 author가 설정되어 있더라도 doc_to_prompt 헤더로 누출되지 않는지 검증."""
    from claire.extract.prompts import doc_to_prompt

    doc = Document(
        title="테스트 문서",
        author="Orbrium",
        url="https://youtube.com/watch?v=123",
        raw_text="본문 내용",
    )
    prompt_body = doc_to_prompt(doc)
    assert "AUTHOR: Orbrium" not in prompt_body
    assert "TITLE: 테스트 문서" in prompt_body

