"""AsciiDoc(ADOC) 및 듀얼 포맷 본문 렌더링 파이프라인 검증 테스트."""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest

from claire.config import Settings
from claire.extract.prompts import (
    render_detail_prompt,
)
from claire.extract.provider import MockProvider
from claire.graphview import GRAPH_HTML, document_detail, shared_html
from claire.ingest.pipeline import ensure_document_detail
from claire.ontology.base import Document
from claire.render import render_adoc_to_html, render_md_to_html
from claire.store import db as dbm


def test_config_render_format_validation():
    """Settings 의 render_format 필드가 기본값 adoc 이며 md 와 adoc 만 허용하고 소문자로 정규화하는지 검증."""
    s_default = Settings()
    assert s_default.render_format == "adoc"

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


def test_documents_needing_detail_format():
    """documents_needing_detail_format 이 목표 포맷 미적용(불일치 및 누락) 문서만 정확히 추출하는지 검증."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    dbm.init_db(conn)

    # d1: md detail
    conn.execute(
        "INSERT INTO documents (id, title, url, raw_text, fetched_at, detail, detail_format) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("d1", "문서1", "https://example.com/1", "텍스트1", 1000.0, "본문1", "md"),
    )
    # d2: adoc detail
    conn.execute(
        "INSERT INTO documents (id, title, url, raw_text, fetched_at, detail, detail_format) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("d2", "문서2", "https://example.com/2", "텍스트2", 2000.0, "본문2", "adoc"),
    )
    # d3: detail 누락 (NULL)
    conn.execute(
        "INSERT INTO documents (id, title, url, raw_text, fetched_at, detail, detail_format) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("d3", "문서3", "https://example.com/3", "텍스트3", 3000.0, None, "md"),
    )
    # d4: detail 빈 문자열
    conn.execute(
        "INSERT INTO documents (id, title, url, raw_text, fetched_at, detail, detail_format) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("d4", "문서4", "https://example.com/4", "텍스트4", 4000.0, "   ", "adoc"),
    )

    # target='adoc' -> d4(빈값), d3(누락), d1(md) 총 3건 (최신순: d4, d3, d1). d2는 제외
    needed_adoc = dbm.documents_needing_detail_format(conn, "adoc")
    assert set(needed_adoc) == {"d1", "d3", "d4"}
    assert "d2" not in needed_adoc

    # target='md' -> d4(빈값), d3(누락), d2(adoc) 총 3건. d1은 제외
    needed_md = dbm.documents_needing_detail_format(conn, "md")
    assert set(needed_md) == {"d2", "d3", "d4"}
    assert "d1" not in needed_md

    conn.close()


def test_backfill_details_selective_migration():
    """IngestService.backfill_details 가 force=False 일 때 이미 목표 포맷인 문서는 건너뛰고 미적용 문서만 선별 처리하는지 검증."""
    from claire.ingest.service import IngestService

    with tempfile.TemporaryDirectory() as tmpdir:
        db_file = Path(tmpdir) / "test.db"
        s = Settings(
            db_path=str(db_file),
            vault_dir=str(Path(tmpdir) / "vault"),
            provider="mock",
            render_format="adoc",
        )
        conn = dbm.connect(str(db_file))
        dbm.init_db(conn)

        # d1: 이미 adoc 포맷으로 detail 존재
        conn.execute(
            "INSERT INTO documents (id, title, url, raw_text, fetched_at, detail, detail_format) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("d1", "문서1", "https://example.com/1", "원문1", 1000.0, "[mock-detail-adoc] 기존", "adoc"),
        )
        # d2: md 포맷으로 detail 존재
        conn.execute(
            "INSERT INTO documents (id, title, url, raw_text, fetched_at, detail, detail_format) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("d2", "문서2", "https://example.com/2", "원문2", 2000.0, "[mock-detail] 마크다운", "md"),
        )
        # d3: detail 없음
        conn.execute(
            "INSERT INTO documents (id, title, url, raw_text, fetched_at, detail, detail_format) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("d3", "문서3", "https://example.com/3", "원문3", 3000.0, None, None),
        )
        conn.commit()
        conn.close()

        svc = IngestService(s)
        # force=False 로 adoc 백필 실행 (d2, d3 만 대상이 되어야 함)
        out = svc.backfill_details(force=False, format="adoc")
        assert out["docs"] == 2  # d2, d3
        assert out["ok"] == 2

        # 결과 확인
        conn = dbm.connect(str(db_file))
        assert dbm.get_document_detail(conn, "d1") == "[mock-detail-adoc] 기존"  # 변경되지 않음
        assert dbm.get_document_detail_format(conn, "d1") == "adoc"
        assert dbm.get_document_detail_format(conn, "d2") == "adoc"  # adoc 으로 갱신됨
        assert dbm.get_document_detail_format(conn, "d3") == "adoc"  # adoc 으로 생성됨
        status = dbm.get_format_status(conn, "adoc")
        assert status["needs_migration"] is False
        assert status["matching_docs"] == 3
        conn.close()


def test_aot_render_adoc_table_multiline_rows():
    """빈 줄로 구분된 다중 행/열 AsciiDoc 테이블이 올바른 <tr> 및 <td>들로 렌더링되는지 검증 (vSAN 클러스터 문서 케이스)."""
    sample = """
== 5. 전통적 HCI 대비 vSAN Storage Clusters 비교 (Comparison & Specifications)

|===
| 항목 | 전통적 vSAN HCI 모델 | vSAN Storage Clusters (vSAN Max)

| *스토리지 아키텍처*
| 컴퓨트 + 스토리지 결합형 (HCI)
| 완전 분리형 중앙 공유 스토리지 (Disaggregated)

| *기반 엔진*
| vSAN OSA 또는 vSAN ESA
| *vSAN Express Storage Architecture (ESA)* 전용

| *최소 노드 요구*
| 3 노드 (2-Node 토폴로지 제외)
| 6 노드

| *스케일링 특성*
| 컴퓨트와 스토리지의 동시 확장 필요
| #컴퓨트와 스토리지 독립적 스케일아웃 가능#
|===
"""
    html_out = render_adoc_to_html(sample)
    # Header 검증 (3개 헤더 열)
    assert "<thead><tr><th>항목</th><th>전통적 vSAN HCI 모델</th><th>vSAN Storage Clusters (vSAN Max)</th></tr></thead>" in html_out
    # Body 검증 - 각 행이 3개의 td로 구성되어야 함
    assert "<tr><td><strong>스토리지 아키텍처</strong></td><td>컴퓨트 + 스토리지 결합형 (HCI)</td><td>완전 분리형 중앙 공유 스토리지 (Disaggregated)</td></tr>" in html_out
    assert "<tr><td><strong>기반 엔진</strong></td><td>vSAN OSA 또는 vSAN ESA</td><td><strong>vSAN Express Storage Architecture (ESA)</strong> 전용</td></tr>" in html_out
    assert "<tr><td><strong>최소 노드 요구</strong></td><td>3 노드 (2-Node 토폴로지 제외)</td><td>6 노드</td></tr>" in html_out
    assert "<tr><td><strong>스케일링 특성</strong></td><td>컴퓨트와 스토리지의 동시 확장 필요</td><td><mark>컴퓨트와 스토리지 독립적 스케일아웃 가능</mark></td></tr>" in html_out


def test_aot_render_adoc_table_with_cols_and_caption():
    """[cols=...] 속성 및 .테이블제목 캡션이 지정된 AsciiDoc 테이블 렌더링 검증."""
    sample = """
.vSAN 스펙 비교표
[cols="3*"]
|===
| Spec | Min | Max
| Nodes | 6 | 32
| Net | 25G | 100G
|===
"""
    html_out = render_adoc_to_html(sample)
    assert "<caption>vSAN 스펙 비교표</caption>" in html_out
    assert "<thead><tr><th>Spec</th><th>Min</th><th>Max</th></tr></thead>" in html_out
    assert "<tbody><tr><td>Nodes</td><td>6</td><td>32</td></tr><tr><td>Net</td><td>25G</td><td>100G</td></tr></tbody>" in html_out


def test_aot_render_adoc_table_multiline_cell_and_escaped_pipe():
    """셀 내 줄바꿈 연속 텍스트 및 이스케이프된 파이프(\\|)가 올바르게 보존되는지 검증."""
    sample = r"""
|===
| Command | Description

| `git status`
| 현재 작업 트리의
상태를 표시
| `cmd \| grep`
| 파이프라인 필터링
|===
"""
    html_out = render_adoc_to_html(sample)
    assert "<thead><tr><th>Command</th><th>Description</th></tr></thead>" in html_out
    assert "<tr><td><code>git status</code></td><td>현재 작업 트리의 상태를 표시</td></tr>" in html_out
    assert "<tr><td><code>cmd | grep</code></td><td>파이프라인 필터링</td></tr>" in html_out


def test_db_recompile_all_detail_html():
    """dbm.recompile_all_detail_html 이 기존 DB의 모든 detail_html 을 최신 AOT 렌더러로 정상 갱신하는지 검증."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    dbm.init_db(conn)

    sample_adoc = """
|===
| A | B
| 1
| 2
|===
"""
    doc_id = "doc-recompile-1"
    conn.execute(
        "INSERT INTO documents (id, title, url, raw_text, fetched_at, detail, detail_format, detail_html) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (doc_id, "테스트", "https://example.com", "본문", 1000.0, sample_adoc, "adoc", "<old_html>"),
    )
    conn.commit()

    assert dbm.get_document_detail_html(conn, doc_id) == "<old_html>"

    updated_count = dbm.recompile_all_detail_html(conn)
    assert updated_count == 1

    new_html = dbm.get_document_detail_html(conn, doc_id)
    assert "<old_html>" not in new_html
    assert "<table><thead><tr><th>A</th><th>B</th></tr></thead><tbody><tr><td>1</td><td>2</td></tr></tbody></table>" in new_html

    conn.close()


def test_aot_render_adoc_table_rowspan_and_colspan():
    """AsciiDoc 셀 접두사 문법(.2+|, 2+|, 2.2+|, ^|)에 따른 rowspan, colspan, align 및 행/열 배치가 정상 렌더링되는지 검증."""
    sample = """
|===
| H1 | H2 | H3

.2+| Row 1-2 Col 1
| Row 1 Col 2
| Row 1 Col 3

| Row 2 Col 2
| Row 2 Col 3

2+| Span 2 Cols
^| Centered Col 3
|===
"""
    html_out = render_adoc_to_html(sample)
    assert "<thead><tr><th>H1</th><th>H2</th><th>H3</th></tr></thead>" in html_out
    assert '<tr><td rowspan="2">Row 1-2 Col 1</td><td>Row 1 Col 2</td><td>Row 1 Col 3</td></tr>' in html_out
    assert "<tr><td>Row 2 Col 2</td><td>Row 2 Col 3</td></tr>" in html_out
    assert '<tr><td colspan="2">Span 2 Cols</td><td style="text-align:center">Centered Col 3</td></tr>' in html_out
    assert ".2+|" not in html_out
    assert "2+|" not in html_out
    assert "^|" not in html_out


def test_aot_render_adoc_table_building_blocks_case():
    """9 Building Blocks 문서의 4열 다중 행 병합(.2+|, .3+|) 테이블이 열 밀림 없이 완벽히 렌더링되는지 검증."""
    sample = """
|===
| 영역 (Area) | 빌딩 블록 (Building Block) | 약칭 | 주요 역할 및 개념 정의

.2+| Customers (고객)
| Customer Segments
| CS
| 조직이 도달하고 서비스하려는 하나 이상의 고객 그룹

| Customer Relationships
| CR
| 각 고객 세그먼트와 수립하고 유지하는 관계의 유형

| Offer (제안)
| Value Propositions
| VP
| 고객의 문제를 해결하고 요구를 충족시키는 가치의 묶음

.3+| Infrastructure (인프라)
| Channels
| CH
| 가치 제안을 고객에게 전달하는 커뮤니케이션·유통·영업 채널

| Key Resources
| KR
| 가치 제안 제공 및 비즈니스 모델 운영에 필수적인 핵심 자산

| Key Activities
| KA
| 비즈니스 모델을 원활히 작동시키기 위해 수행해야 하는 핵심 활동
|===
"""
    html_out = render_adoc_to_html(sample)
    # 1. 태그 누출 방지 검증
    assert ".2+|" not in html_out
    assert ".3+|" not in html_out
    # 2. 헤더 검증 (4열)
    assert "<thead><tr><th>영역 (Area)</th><th>빌딩 블록 (Building Block)</th><th>약칭</th><th>주요 역할 및 개념 정의</th></tr></thead>" in html_out
    # 3. Row 1 검증: Customers (rowspan=2), Customer Segments, CS, 설명
    assert '<tr><td rowspan="2">Customers (고객)</td><td>Customer Segments</td><td>CS</td><td>조직이 도달하고 서비스하려는 하나 이상의 고객 그룹</td></tr>' in html_out
    # 4. Row 2 검증: Customer Relationships 가 1열로 밀리지 않고 3개 셀로 바르게 구성
    assert "<tr><td>Customer Relationships</td><td>CR</td><td>각 고객 세그먼트와 수립하고 유지하는 관계의 유형</td></tr>" in html_out
    # 5. Row 3 검증: Offer (1x1 4열)
    assert "<tr><td>Offer (제안)</td><td>Value Propositions</td><td>VP</td><td>고객의 문제를 해결하고 요구를 충족시키는 가치의 묶음</td></tr>" in html_out
    # 6. Row 4 검증: Infrastructure (rowspan=3), Channels, CH, 설명
    assert '<tr><td rowspan="3">Infrastructure (인프라)</td><td>Channels</td><td>CH</td><td>가치 제안을 고객에게 전달하는 커뮤니케이션·유통·영업 채널</td></tr>' in html_out
    # 7. Row 5 & Row 6 검증
    assert "<tr><td>Key Resources</td><td>KR</td><td>가치 제안 제공 및 비즈니스 모델 운영에 필수적인 핵심 자산</td></tr>" in html_out
    assert "<tr><td>Key Activities</td><td>KA</td><td>비즈니스 모델을 원활히 작동시키기 위해 수행해야 하는 핵심 활동</td></tr>" in html_out

