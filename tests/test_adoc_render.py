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
from claire.render import render_adoc_to_html, render_md_to_html, render_to_html
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


def test_aot_render_adoc():
    """render_adoc_to_html 이 AsciiDoc 문법 전체를 올바른 시맨틱 HTML 로 AOT 변환하는지 검증."""
    sample = """
== 섹션 제목
*굵은 글씨* 및 _기울임_ 및 #형광 하이라이트# 및 `인라인 코드`
https://example.com[링크 텍스트]

[quote, 댄 앨런, 안토라 리드]
____
AOT 사전 렌더링으로 브라우저 eval을 완전히 제거합니다.
____

[NOTE]
====
중요한 노트 알림 상자입니다.
====

[source,python]
----
def greet(name):  # <1>
    return f"Hello {name}"
----
<1> 인사말 반환 함수

|===
|기능 |AOT |JIT
|Eval 불필요 |O |X
|===

image::https://example.com/diagram.png[구조도, title="AOT 파이프라인"]
"""
    html_out = render_adoc_to_html(sample)
    assert "<h2>섹션 제목</h2>" in html_out
    assert "<strong>굵은 글씨</strong>" in html_out
    assert "<em>기울임</em>" in html_out
    assert "<mark>형광 하이라이트</mark>" in html_out
    assert "<code>인라인 코드</code>" in html_out
    assert '<a href="https://example.com" target="_blank" rel="noopener">링크 텍스트</a>' in html_out
    assert '<div class="quoteblock"><blockquote><p>AOT 사전 렌더링으로 브라우저 eval을 완전히 제거합니다.</p></blockquote><div class="attribution">댄 앨런 — 안토라 리드</div></div>' in html_out
    assert '<div class="admonitionblock note"><div class="title">NOTE</div><div class="content"><p>중요한 노트 알림 상자입니다.</p></div></div>' in html_out
    assert '<pre><code class=" language-python">' in html_out or '<pre><code class="language-python">' in html_out
    assert '<span class="conum">&lt;1&gt;</span>' in html_out
    assert '<div class="colist"><span class="conum">&lt;1&gt;</span> 인사말 반환 함수</div>' in html_out
    assert "<table><thead><tr><th>기능</th><th>AOT</th><th>JIT</th></tr></thead>" in html_out
    assert '<div class="imageblock"><img src="https://example.com/diagram.png" alt="구조도"><div class="title">AOT 파이프라인</div></div>' in html_out


def test_aot_render_md():
    """render_md_to_html 이 마크다운과 ==형광== 문법을 올바른 HTML 로 AOT 변환하는지 검증."""
    sample = """## 마크다운 제목
일반 텍스트 및 ==형광 텍스트== 입니다.

> 인용 블록
"""
    html_out = render_md_to_html(sample)
    assert "<h2" in html_out and "마크다운 제목" in html_out
    assert "<mark>형광 텍스트</mark>" in html_out
    assert "<blockquote>" in html_out


def test_db_detail_format_storage_and_migration():
    """DB documents 테이블에 detail_format 및 detail_html 컬럼이 정상 저장/조회되고 마이그레이션되는지 검증."""
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
    assert dbm.get_document_detail_html(conn, doc_id) is None

    # adoc 포맷으로 detail 저장 (html 인자 없이 자동 AOT 렌더링)
    adoc_detail = "[quote, 저자]\n____\n인용 본문\n____"
    dbm.set_document_detail(conn, doc_id, adoc_detail, format="adoc")
    assert dbm.get_document_detail(conn, doc_id) == adoc_detail
    assert dbm.get_document_detail_format(conn, doc_id) == "adoc"
    adoc_html = dbm.get_document_detail_html(conn, doc_id)
    assert adoc_html is not None
    assert '<div class="quoteblock">' in adoc_html
    assert "인용 본문" in adoc_html

    # md 포맷으로 갱신
    md_detail = "==형광== 본문"
    dbm.set_document_detail(conn, doc_id, md_detail, format="md")
    assert dbm.get_document_detail(conn, doc_id) == md_detail
    assert dbm.get_document_detail_format(conn, doc_id) == "md"
    md_html = dbm.get_document_detail_html(conn, doc_id)
    assert md_html is not None
    assert "<mark>형광</mark>" in md_html

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
    """ensure_document_detail 이 전달된 format 에 따라 detail 과 detail_html 을 함께 생성·저장하는지 검증."""
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
    adoc_html = dbm.get_document_detail_html(conn, doc.id)
    assert adoc_html is not None
    assert "mock-detail-adoc" in adoc_html

    # 2. force=False 일 때 이미 존재하므로 False
    assert ensure_document_detail(conn, prov, doc, force=False, format="adoc") is False

    # 3. md 포맷으로 force 갱신
    ok = ensure_document_detail(conn, prov, doc, force=True, format="md")
    assert ok is True
    assert dbm.get_document_detail_format(conn, doc.id) == "md"
    assert "[mock-detail]" in dbm.get_document_detail(conn, doc.id)
    md_html = dbm.get_document_detail_html(conn, doc.id)
    assert md_html is not None
    assert "mock-detail" in md_html

    conn.close()


def test_graphview_detail_format_and_html():
    """graphview 의 document_detail, node_detail, HTML 템플릿이 detail_html 을 올바르게 포함하고 Asciidoctor CDN 을 배제하는지 검증."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    dbm.init_db(conn)

    doc_id = "doc-gv-1"
    conn.execute(
        "INSERT INTO documents (id, title, url, raw_text, fetched_at, detail_format) VALUES (?, ?, ?, ?, ?, ?)",
        (doc_id, "웹 뷰어 테스트", "https://example.com", "본문", 1000.0, "adoc"),
    )
    dbm.set_document_detail(conn, doc_id, "[NOTE]\n====\n중요 노트\n====", format="adoc")

    # document_detail API 결과 검증 (detail_html 포함)
    dd = document_detail(conn, doc_id)
    assert dd is not None
    assert dd["detail_format"] == "adoc"
    assert "[NOTE]" in dd["detail"]
    assert dd["detail_html"] is not None
    assert '<div class="admonitionblock note">' in dd["detail_html"]

    # shared_html 렌더링 검증
    s_html = shared_html(dd)
    assert "convertAsciidocToHtml" in s_html
    assert "renderContent" in s_html
    assert ".admonitionblock" in s_html
    # unpkg asciidoctor CDN 스크립트 제거 확인 (Zero-eval)
    assert "unpkg.com/@asciidoctor/core" not in s_html

    # GRAPH_HTML 검증
    assert "convertAsciidocToHtml" in GRAPH_HTML
    assert "renderContent" in GRAPH_HTML
    assert ".admonitionblock" in GRAPH_HTML
    assert ".quoteblock" in GRAPH_HTML
    assert "format-warn-banner" in GRAPH_HTML
    # unpkg asciidoctor CDN 스크립트 제거 확인 (Zero-eval)
    assert "unpkg.com/@asciidoctor/core" not in GRAPH_HTML

    conn.close()


def test_check_format_mismatch():
    """check_format_mismatch 가 설정 포맷과 DB 의 detail_format 불일치 여부를 정확히 진단하는지 검증."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    dbm.init_db(conn)

    # 1. 문서가 없을 때
    res_empty = dbm.check_format_mismatch(conn, "adoc")
    assert res_empty["configured"] == "adoc"
    assert res_empty["total_with_detail"] == 0
    assert res_empty["mismatched"] == 0
    assert res_empty["needs_migration"] is False

    # 2. md 포맷 문서 2개 추가
    conn.execute(
        "INSERT INTO documents (id, title, url, raw_text, fetched_at, detail, detail_format) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("d1", "문서1", "https://example.com/1", "텍스트1", 1000.0, "본문1", "md"),
    )
    conn.execute(
        "INSERT INTO documents (id, title, url, raw_text, fetched_at, detail, detail_format) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("d2", "문서2", "https://example.com/2", "텍스트2", 1000.0, "본문2", "md"),
    )

    # 설정이 'md' 이면 일치
    res_md = dbm.check_format_mismatch(conn, "md")
    assert res_md["needs_migration"] is False
    assert res_md["mismatched"] == 0

    # 설정이 'adoc' 이면 불일치 감지
    res_adoc = dbm.check_format_mismatch(conn, "adoc")
    assert res_adoc["needs_migration"] is True
    assert res_adoc["mismatched"] == 2
    assert res_adoc["total_with_detail"] == 2

    # 1개 문서를 adoc 으로 갱신
    dbm.set_document_detail(conn, "d1", "[NOTE]\n====\n노트\n====", format="adoc")
    res_adoc_partial = dbm.check_format_mismatch(conn, "adoc")
    assert res_adoc_partial["needs_migration"] is True
    assert res_adoc_partial["mismatched"] == 1

    # 나머지 1개도 adoc 으로 갱신하면 완전 일치
    dbm.set_document_detail(conn, "d2", "[TIP]\n====\n팁\n====", format="adoc")
    res_adoc_full = dbm.check_format_mismatch(conn, "adoc")
    assert res_adoc_full["needs_migration"] is False
    assert res_adoc_full["mismatched"] == 0

    conn.close()
