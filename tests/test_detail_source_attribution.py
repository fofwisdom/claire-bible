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
def test_render_detail_prompt_preserves_biblio_but_forbids_original_link(format: str):
    prompt = render_detail_prompt(
        "URL: https://example.com/article\n\nCONTENT:\n본문",
        [],
        merged=False,
        format=format,
    )

    assert "서지 정보 표기" in prompt
    assert "저자·발행일·출처명·문서/세션 ID·DOI 등의 서지 정보는 보존" in prompt
    assert "'원문 열기' 기능이 별도로 있으므로" in prompt
    assert "입력 헤더의 `URL:` 값" in prompt


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

        def render_detail(self, doc, format="adoc", directive=None, effort=None):
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


def test_existing_detail_is_cleaned_during_read_without_writing(tmp_path):
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
    conn.close()

    readonly_conn = dbm.connect_existing(db_file, readonly=True)
    assert dbm.get_document_detail(readonly_conn, "doc-existing") == (
        "= 제목\n_출처: Example_\n\n'''\n\n== 개요\n본문"
    )
    html = dbm.get_document_detail_html(readonly_conn, "doc-existing")
    assert "stale source" not in html
    assert "출처: Example" in html
    assert "<h2>개요</h2>" in html
    readonly_conn.close()

    conn = dbm.connect(db_file)
    stored = conn.execute(
        "SELECT detail, detail_html FROM documents WHERE id='doc-existing'"
    ).fetchone()
    assert stored["detail"] == detail
    assert stored["detail_html"] == "<p>stale source</p>"
    conn.close()
