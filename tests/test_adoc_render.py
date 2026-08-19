"""AsciiDoc(ADOC) 및 듀얼 포맷 본문 렌더링 파이프라인 검증 테스트."""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest

from claire.config import Settings
from claire.extract.prompts import (
    render_detail_prompt,
    render_detail_prompt_adoc,
    render_detail_prompt_md,
)
from claire.extract.provider import MockProvider
from claire.graphview import document_detail, node_detail, shared_html, GRAPH_HTML
from claire.ingest.pipeline import ensure_document_detail, IngestReport
from claire.ontology.base import Document
from claire.store import db as dbm


def test_config_render_format_validation():
    """Settings 의 render_format 필드가 md 와 adoc 만 허용하고 소문자로 정규화하는지 검증."""
    s_md = Settings(render_format="md")
    assert s_md.render_format == "md"

    s_adoc = Settings(render_format="ADOC")
    assert s_adoc.render_format == "adoc"

    s_asciidoc = Settings(render_format="asciidoc")
    assert s_asciidoc.render_format == "adoc"

    with pytest.raises(ValueError, match="CLAIRE_RENDER_FORMAT must be"):
        Settings(render_format="html")


def test_db_detail_format_storage_and_migration():
    """DB documents 테이블에 detail_format 컬럼이 정상 저장/조회되고 마이그레이션되는지 검증."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    dbm.init_db(conn)

    # 기본 문서 생성
    doc_id = "doc-test-1"
    conn.execute(
        "INSERT INTO documents (id, title, url, raw_text, fetched_at) VALUES (?, ?, ?, ?, ?)",
        (doc_id, "테스트 문서", "https://example.com/1", "원문 텍스트", 1000.0),
    )

    # 기본 detail_format 조회 -> 'md'
    assert dbm.get_document_detail_format(conn, doc_id) == "md"

    # adoc 포맷으로 detail 저장
    adoc_detail = "[quote, 저자]\n____\n인용 본문\n____"
    dbm.set_document_detail(conn, doc_id, adoc_detail, format="adoc")
    assert dbm.get_document_detail(conn, doc_id) == adoc_detail
    assert dbm.get_document_detail_format(conn, doc_id) == "adoc"

    # md 포맷으로 갱신
    md_detail = "> 인용 본문"
    dbm.set_document_detail(conn, doc_id, md_detail, format="md")
    assert dbm.get_document_detail(conn, doc_id) == md_detail
    assert dbm.get_document_detail_format(conn, doc_id) == "md"

    conn.close()


def test_prompts_dual_format():
    """render_detail_prompt 가 포맷(md vs adoc)에 따라 올바른 규칙 프롬프트를 생성하는지 검증."""
    body = "샘플 본문 내용"
    images = [{"url": "https://example.com/img.png", "alt": "다이어그램", "caption": "설명"}]

    prompt_md = render_detail_prompt(body, images, merged=False, format="md")
    assert "마크다운" in prompt_md
    assert "![설명](url)" in prompt_md or "그림" in prompt_md

    prompt_adoc = render_detail_prompt(body, images, merged=False, format="adoc")
    assert "AsciiDoc" in prompt_adoc
    assert "[quote" in prompt_adoc
    assert "[source" in prompt_adoc
    assert "image::" in prompt_adoc
    assert "|===" in prompt_adoc


def test_mock_provider_dual_format():
    """MockProvider.render_detail 이 포맷(md vs adoc)에 맞게 올바른 stub 을 생성하는지 검증."""
    prov = MockProvider()
    doc = Document(
        id="doc-1",
        title="AsciiDoc 도입",
        url="https://example.com",
        raw_text="AsciiDoc 의 강력한 표현력을 활용한다.",
        meta={"images": [{"url": "https://example.com/arch.png", "alt": "구조도", "caption": "아키텍처"}]},
    )

    md_out = prov.render_detail(doc, format="md")
    assert "[mock-detail]" in md_out
    assert "![구조도]" in md_out

    adoc_out = prov.render_detail(doc, format="adoc")
    assert "[mock-detail-adoc]" in adoc_out
    assert "image::" in adoc_out


def test_pipeline_ensure_document_detail_format():
    """ensure_document_detail 이 전달된 format 또는 doc.meta/설정에 따라 생성하고 저장하는지 검증."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    dbm.init_db(conn)

    prov = MockProvider()
    doc = Document(
        id="doc-pipeline-1",
        title="파이프라인 테스트",
        url="https://example.com",
        raw_text="내용입니다.",
    )
    conn.execute(
        "INSERT INTO documents (id, title, url, raw_text, fetched_at) VALUES (?, ?, ?, ?, ?)",
        (doc.id, doc.title, doc.url, doc.raw_text, 1000.0),
    )

    # 1. adoc 포맷으로 ensure_document_detail 실행
    ok = ensure_document_detail(conn, prov, doc, force=True, format="adoc")
    assert ok is True
    assert dbm.get_document_detail_format(conn, doc.id) == "adoc"
    assert "[mock-detail-adoc]" in dbm.get_document_detail(conn, doc.id)

    # 2. force=False 일 때 이미 존재하므로 False
    assert ensure_document_detail(conn, prov, doc, force=False, format="adoc") is False

    # 3. md 포맷으로 force 갱신
    ok = ensure_document_detail(conn, prov, doc, force=True, format="md")
    assert ok is True
    assert dbm.get_document_detail_format(conn, doc.id) == "md"
    assert "[mock-detail]" in dbm.get_document_detail(conn, doc.id)

    conn.close()


def test_graphview_detail_format_and_html():
    """graphview 의 document_detail, node_detail, HTML 렌더러가 detail_format 을 올바르게 포함하는지 검증."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    dbm.init_db(conn)

    doc_id = "doc-gv-1"
    conn.execute(
        "INSERT INTO documents (id, title, url, raw_text, fetched_at, detail_format) VALUES (?, ?, ?, ?, ?, ?)",
        (doc_id, "웹 뷰어 테스트", "https://example.com", "본문", 1000.0, "adoc"),
    )
    dbm.set_document_detail(conn, doc_id, "[NOTE]\n====\n중요 노트\n====", format="adoc")

    # document_detail API 결과 검증
    dd = document_detail(conn, doc_id)
    assert dd is not None
    assert dd["detail_format"] == "adoc"
    assert "[NOTE]" in dd["detail"]

    # shared_html 렌더링에 convertAsciidocToHtml 및 renderContent 함수 포함 검증
    s_html = shared_html(dd)
    assert "convertAsciidocToHtml" in s_html
    assert "renderContent" in s_html
    assert ".admonitionblock" in s_html
    assert "asciidoctor" in s_html

    # GRAPH_HTML 검증
    assert "convertAsciidocToHtml" in GRAPH_HTML
    assert "renderContent" in GRAPH_HTML
    assert ".admonitionblock" in GRAPH_HTML
    assert ".quoteblock" in GRAPH_HTML
    assert "asciidoctor" in GRAPH_HTML

    conn.close()
