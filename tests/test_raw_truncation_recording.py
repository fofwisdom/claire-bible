"""원문 절단 메타데이터 기록 및 웹 UI docmeta 렌더링 검증 테스트."""

import pytest

from claire.extract.table_budget import (
    slice_text_with_table_exemption,
    slice_text_with_table_exemption_info,
)
from claire.graphview import document_detail, render_graph_html, shared_html
from claire.ingest.fetchers.textfile import fetch_file, fetch_text
from claire.ingest.fetchers.web import fetch_web
from claire.ontology.base import Document
from claire.store import db as dbm


def test_slice_text_with_table_exemption_info_short_text():
    text = "Short text under budget."
    sliced, is_trunc, orig_chars, sliced_chars = slice_text_with_table_exemption_info(text, limit=1000)
    assert sliced == text
    assert is_trunc is False
    assert orig_chars == len(text)
    assert sliced_chars == len(text)


def test_slice_text_with_table_exemption_info_long_text_truncated():
    prose = "A" * 15000
    sliced, is_trunc, orig_chars, sliced_chars = slice_text_with_table_exemption_info(prose, limit=10000)
    assert len(sliced) == 10000
    assert is_trunc is True
    assert orig_chars == 15000
    assert sliced_chars == 10000


def test_slice_text_with_table_exemption_info_with_table():
    prose_before = "P" * 8000
    table = "\n| Col1 | Col2 |\n|---|---|\n| Val1 | Val2 |\n"
    prose_after = "Q" * 7000  # Total prose = 15,000
    full_text = prose_before + table + prose_after

    sliced, is_trunc, orig_chars, sliced_chars = slice_text_with_table_exemption_info(full_text, limit=10000)
    assert is_trunc is True
    assert orig_chars == len(full_text)
    # Table must be completely preserved
    assert table in sliced
    # Prose before + prose after should sum to 10,000
    assert "P" * 8000 in sliced
    assert "Q" * 2000 in sliced
    assert "Q" * 2001 not in sliced


def test_fetch_text_meta_truncation():
    text = "Simple note text."
    doc = fetch_text(text)
    assert doc.meta.get("raw_truncated") is False
    assert doc.meta.get("orig_chars") == len(text)
    assert doc.meta.get("raw_chars") == len(text)


def test_fetch_file_meta_truncation(tmp_path):
    # Short file
    f_short = tmp_path / "short.txt"
    f_short.write_text("Hello world", encoding="utf-8")
    doc_short = fetch_file(str(f_short))
    assert doc_short.meta.get("raw_truncated") is False
    assert doc_short.meta.get("orig_chars") == 11
    assert doc_short.meta.get("raw_chars") == 11

    # Long file (>20,000 chars)
    f_long = tmp_path / "long.txt"
    long_content = "X" * 25000
    f_long.write_text(long_content, encoding="utf-8")
    doc_long = fetch_file(str(f_long))
    assert doc_long.meta.get("raw_truncated") is True
    assert doc_long.meta.get("orig_chars") == 25000
    assert doc_long.meta.get("raw_chars") == 20000
    assert len(doc_long.raw_text) == 20000


def test_fetch_web_meta_truncation(monkeypatch):
    from claire.ingest.fetchers import web

    # Mock _fetch_static to return >20,000 chars
    long_body = "This is a detailed paragraph. " * 1000  # ~30,000 chars
    monkeypatch.setattr(
        web,
        "_fetch_static",
        lambda url: ("Test Web Title", long_body, ["https://example.com/sub"], {}, None, url, []),
    )
    monkeypatch.setattr(web, "_is_usable", lambda title, text: (True, None))

    doc = fetch_web("https://example.com/long-article")
    assert doc.meta.get("raw_truncated") is True
    assert doc.meta.get("orig_chars") == len(long_body)
    assert doc.meta.get("raw_chars") == len(doc.raw_text)
    assert len(doc.raw_text) == 20000


def test_document_detail_returns_truncation_meta():
    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    dbm.init_db(conn)
    doc = Document(
        id="doc_trunc_test",
        title="Truncated Doc Test",
        url="https://example.com/test",
        raw_text="A" * 20000,
        source_type="web",
        meta={
            "raw_truncated": True,
            "orig_chars": 35000,
            "raw_chars": 20000,
        },
    )
    dbm.insert_document(conn, doc)

    detail = document_detail(conn, "doc_trunc_test")
    assert detail is not None
    assert detail["raw_truncated"] is True
    assert detail["orig_chars"] == 35000
    assert detail["raw_chars"] == 20000
    assert detail["meta"]["raw_truncated"] is True


def test_graph_html_contains_truncation_ui_and_css():
    html = render_graph_html()
    assert ".trunc-tag" in html
    assert ".directive-tag" in html
    assert ".stt-tag" in html
    assert "docMetaHtml" in html
    assert "✂️ 원문 일부 절단" in html
    assert "🎯" in html
    assert "🎙️ STT" in html
    assert "음성 인식(STT)을 적용하여 작성한 문서" in html
    assert "적재 시 지정한 초점: " in html
    assert "원문의 부록(Appendix) 부분을 절단한 문서" in html
    assert "글자 수 상한으로 원문 일부를 절단한 문서" in html
    assert "작성된 본문" not in html
    assert "적재되었습니다" not in html
    # 20,000자가 하드코딩되어 있지 않고 동적이어야 함
    assert "(20,000자)" not in html


def test_shared_html_contains_docmeta_ui_and_css():
    doc = {
        "id": "doc_test_share",
        "title": "공유 문서 테스트",
        "url": "https://example.com/share-doc",
        "source_type": "web",
        "raw_truncated": True,
        "appendix_truncated": True,
        "orig_chars": 50000,
        "raw_chars": 20000,
        "directive": "핵심 알고리즘 분석",
        "is_stt": True,
        "summary": "테스트 요약",
    }
    html = shared_html(doc)
    assert ".docmeta" in html
    assert ".docmeta-tags" in html
    assert ".trunc-tag" in html
    assert ".trunc-tag.trunc-appendix" in html
    assert ".directive-tag" in html
    assert ".stt-tag" in html
    assert "docMetaHtml" in html
    assert "✂️ 원문 일부 절단" in html
    assert "🎯" in html
    assert "🎙️ STT" in html
    assert "음성 인식(STT)을 적용하여 작성한 문서" in html
    assert "적재 시 지정한 초점: " in html
    assert "원문의 부록(Appendix) 부분을 절단한 문서" in html
    assert "글자 수 상한으로 원문 일부를 절단한 문서" in html
    assert "작성된 본문" not in html
    assert "적재되었습니다" not in html
    assert "h1 .rmeta" in html
    assert "(20,000자)" not in html



def test_document_detail_and_ui_with_directive():
    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    dbm.init_db(conn)
    doc = Document(
        id="doc_dir_test",
        title="Directive Doc Test",
        url="https://example.com/test-dir",
        raw_text="A" * 1000,
        source_type="web",
        meta={
            "directive": "시스템 아키텍처 및 내부 구조 중심",
            "raw_truncated": False,
        },
    )
    dbm.insert_document(conn, doc)

    detail = document_detail(conn, "doc_dir_test")
    assert detail is not None
    assert detail["directive"] == "시스템 아키텍처 및 내부 구조 중심"
    assert detail["meta"]["directive"] == "시스템 아키텍처 및 내부 구조 중심"


def test_document_detail_and_ui_with_stt():
    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    dbm.init_db(conn)
    doc = Document(
        id="doc_stt_test",
        title="STT Video Test",
        url="https://example.com/video",
        raw_text="STT transcribed speech content",
        source_type="video",
        meta={
            "is_stt": True,
            "has_transcript": True,
            "raw_truncated": False,
        },
    )
    dbm.insert_document(conn, doc)

    detail = document_detail(conn, "doc_stt_test")
    assert detail is not None
    assert detail["is_stt"] is True
    assert detail["meta"]["is_stt"] is True

