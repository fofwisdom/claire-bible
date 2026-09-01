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
    assert "<tr><td>Key Activities</td><td>KA</td><td>비즈니스 모델을 원활히 작동시키기 위해 수행해야 하는 핵심 활동</td></tr>" in html_out


def test_aot_render_adoc_masked_hashes_and_code_protection():
    """마스킹 플레이스홀더(####-##-##, ########) 및 인라인 코드 내 기호가 <mark> 또는 서식으로 왜곡되지 않는지 검증."""
    sample = """
[quote, VMware / Broadcom KB (Article ID: 413102)]
The ramdisk 'vsantraces' is full. As a result the file /vsantraces/vsantraces--####-##-##T##h##m##s###--########-####.####.####-############.zst could not be written.

ESXi 호스트 `[root@esx###:~]` 에서 `rm vsantraces*20####*.zst` 실행.
총 사용량이 #최대 200MB 범위 내로 제한#된다.
UUID: ########-########-####-############
Partitions: naa.#########################
"""
    html_out = render_adoc_to_html(sample)

    # 1. vsantraces 마스킹 경로 왜곡 방지
    assert "/vsantraces/vsantraces--####-##-##T##h##m##s###--########-####.####.####-############.zst" in html_out
    assert "<mark>-</mark>" not in html_out
    assert "<mark>T</mark>" not in html_out
    assert "<mark>h</mark>" not in html_out
    assert "<mark>m</mark>" not in html_out
    assert "<mark>s</mark>" not in html_out
    assert "<mark>.</mark>" not in html_out

    # 2. 인용 블록 및 어트리뷰션
    assert '<div class="quoteblock"><blockquote><p>' in html_out
    assert '<div class="attribution">VMware / Broadcom KB (Article ID: 413102)</div>' in html_out

    # 3. 인라인 코드 보호
    assert "<code>[root@esx###:~]</code>" in html_out
    assert "<code>rm vsantraces*20####*.zst</code>" in html_out

    # 4. 올바른 형광 하이라이트 정상 작동
    assert "<mark>최대 200MB 범위 내로 제한</mark>" in html_out

    # 5. UUID 및 NAA 마스킹 보존
    assert "UUID: ########-########-####-############" in html_out
    assert "Partitions: naa.#########################" in html_out


def test_clean_plain_summary_masked_hashes_protection():
    """clean_plain_summary 에서도 마스킹 해시 패턴(####-##-##)이 왜곡되지 않고 온전히 보존되는지 검증."""
    from claire.extract.prompts import clean_plain_summary

    text = """
= vCenter 경고 분석

The ramdisk 'vsantraces' is full. As a result the file /vsantraces/vsantraces--####-##-##T##h##m##s###--########-####.####.####-############.zst could not be written.
총 사용량이 #최대 200MB 범위 내로 제한#된다.
"""
    cleaned = clean_plain_summary(text)
    assert "/vsantraces/vsantraces--####-##-##T##h##m##s###--########-####.####.####-############.zst" in cleaned
    assert "최대 200MB 범위 내로 제한" in cleaned
    assert "#" not in cleaned or "####" in cleaned  # 형광 # 기호만 제거되고 마스킹 해시는 보존


def test_aot_render_adoc_list_continuation_and_grouping():
    """AsciiDoc 목록 연속 연산자(+) 및 중첩/순서형 목록 그룹화 검증."""
    sample = """
* 첫 번째 항목
+
첫 번째 항목에 연결된 상세 단락입니다.
+
첫 번째 항목에 연결된 두 번째 단락입니다.

* 두 번째 항목
** 하위 항목 1
** 하위 항목 2
* 세 번째 항목

. 순서형 1번
.. 순서형 하위 1-1
.. 순서형 하위 1-2
. 순서형 2번
"""
    html_out = render_adoc_to_html(sample)
    # 1. '+' 기호가 원문 텍스트나 <p>+</p> 로 노출되지 않는지 검증
    assert "<p>+</p>" not in html_out
    assert ">+<" not in html_out
    assert "\n+\n" not in html_out

    # 2. 첫 번째 항목 내부에 두 개의 단락이 <li> 안에 위치하는지 검증
    assert "<li>첫 번째 항목\n<p>첫 번째 항목에 연결된 상세 단락입니다.</p>\n<p>첫 번째 항목에 연결된 두 번째 단락입니다.</p>\n</li>" in html_out

    # 3. 중첩 목록 그룹화 검증
    assert "<li>두 번째 항목\n<ul>\n<li>하위 항목 1\n</li>\n<li>하위 항목 2\n</li>\n</ul>\n</li>" in html_out
    assert "<li>세 번째 항목\n</li>" in html_out

    # 4. 순서형 목록 검증
    assert "<ol>" in html_out
    assert "<li>순서형 1번\n<ol>\n<li>순서형 하위 1-1\n</li>" in html_out


def test_aot_render_adoc_user_sample_case():
    """사용자가 보고한 프라이빗 클라우드 자격 검증 문서(doc_fbcb4fa7a67a) 본문 발췌 케이스 렌더링 검증."""
    sample = """
== 프라이빗 클라우드 자격 검증의 전략적 가치

* 운영 리스크 완화 (Mitigate Operational Risk)

+

컴퓨트, 스토리지, 네트워킹 계층 전반의 관리를 표준화하여 설정 오류(misconfigurations), 다운타임, 보안 취약점을 최소화한다.

* 현대적 워크로드 격차 해소 (Bridge Modern Workload Gap)

+

표준 가상 머신과 Kubernetes 오케스트레이션을 단일하고 일관된 프라이빗 클라우드 환경으로 결합한다.

* 커리어 경로 확장 (Enhance Career Trajectory)

+

엔터프라이즈 하이브리드 클라우드 전환을 주도할 수 있는 역량을 증명하여 아키텍트 및 엔지니어로서의 전문성을 강화한다.
"""
    html_out = render_adoc_to_html(sample)

    # 1. 헤더 검증
    assert "<h2>프라이빗 클라우드 자격 검증의 전략적 가치</h2>" in html_out

    # 2. 독립된 <p>+</p> 태그 및 + 기호 누출 완전 제거 검증
    assert "<p>+</p>" not in html_out
    assert "<p>+ </p>" not in html_out
    assert ">+<" not in html_out

    # 3. 각 항목과 설명 단락이 동일 <li> 내에 올바르게 결합되어 렌더링되는지 검증
    assert "<li>운영 리스크 완화 (Mitigate Operational Risk)\n<p>컴퓨트, 스토리지, 네트워킹 계층 전반의 관리를 표준화하여 설정 오류(misconfigurations), 다운타임, 보안 취약점을 최소화한다.</p>\n</li>" in html_out
    assert "<li>현대적 워크로드 격차 해소 (Bridge Modern Workload Gap)\n<p>표준 가상 머신과 Kubernetes 오케스트레이션을 단일하고 일관된 프라이빗 클라우드 환경으로 결합한다.</p>\n</li>" in html_out
    assert "<li>커리어 경로 확장 (Enhance Career Trajectory)\n<p>엔터프라이즈 하이브리드 클라우드 전환을 주도할 수 있는 역량을 증명하여 아키텍트 및 엔지니어로서의 전문성을 강화한다.</p>\n</li>" in html_out

    # 4. 전체 항목들이 하나의 <ul> 안에 그룹화되어 있는지 검증
    assert html_out.count("<ul") == 1
    assert html_out.count("</ul") == 1


def test_aot_render_adoc_table_with_embedded_lists():
    """테이블 셀 내에 작성된 다중 행 목록(* 불릿)이 <strong>이 아닌 <ul><li> 로 정상 변환되는지 검증."""
    sample = """
|===
| 영역 | 세부 항목

| 리스크
| * 설정 오류 최소화
* 다운타임 감소
* 보안 강화
|===
"""
    html_out = render_adoc_to_html(sample)
    assert "<table>" in html_out
    assert "<thead><tr><th>영역</th><th>세부 항목</th></tr></thead>" in html_out
    assert "<ul>\n<li>설정 오류 최소화\n</li>\n<li>다운타임 감소\n</li>\n<li>보안 강화\n</li>\n</ul>" in html_out
    assert "<strong>설정 오류 최소화</strong>" not in html_out


def test_client_js_convert_asciidoc_to_html_runtime():
    """클라이언트 사이드 JavaScript convertAsciidocToHtml 런타임 결과가 Python AOT 결과와 일치하는지 Node.js 로 검증."""
    import json
    import subprocess
    import shutil

    if shutil.which("node") is None:
        import os
        nvm_node = Path(os.path.expanduser("~/.nvm/versions/node/v26.7.0/bin/node"))
        if nvm_node.is_file():
            os.environ["PATH"] = f"{nvm_node.parent}:{os.environ.get('PATH', '')}"
        else:
            pytest.skip("Node.js is not installed on the system")

    from claire.graphview import GRAPH_HTML
    from tests.test_graphview_runtime import extract_scripts

    scripts = extract_scripts(GRAPH_HTML)
    main_script = scripts[1]  # The main JS logic script

    sample = """
== 프라이빗 클라우드 자격 검증의 전략적 가치

* 운영 리스크 완화 (Mitigate Operational Risk)

+

컴퓨트, 스토리지, 네트워킹 계층 전반의 관리를 표준화하여 설정 오류(misconfigurations), 다운타임, 보안 취약점을 최소화한다.

* 현대적 워크로드 격차 해소 (Bridge Modern Workload Gap)

+

표준 가상 머신과 Kubernetes 오케스트레이션을 단일하고 일관된 프라이빗 클라우드 환경으로 결합한다.
"""
    runner_code = f"""
const fs = require('fs');
const scriptContent = fs.readFileSync(process.argv[2], 'utf8');

// Mock browser globals
class MockElement {{
  constructor(tag, id = '') {{
    this.tagName = (tag || 'div').toUpperCase();
    this.id = id;
    this.className = '';
    this.classList = {{
      _classes: new Set(),
      add(...cls) {{ cls.forEach(c => this._classes.add(c)); }},
      remove(...cls) {{ cls.forEach(c => this._classes.delete(c)); }},
      contains(c) {{ return this._classes.has(c); }},
      toggle(c, force) {{
        if (force === undefined) {{
          if (this._classes.has(c)) this._classes.delete(c);
          else this._classes.add(c);
        }} else if (force) this._classes.add(c);
        else this._classes.delete(c);
      }}
    }};
    this.style = {{}};
    this.dataset = {{}};
    this.attributes = {{}};
    this.innerHTML = '';
    this.textContent = '';
    this.value = '';
    this.children = [];
  }}
  setAttribute(k, v) {{ this.attributes[k] = String(v); }}
  getAttribute(k) {{ return this.attributes[k] !== undefined ? this.attributes[k] : null; }}
  removeAttribute(k) {{ delete this.attributes[k]; }}
  getBoundingClientRect() {{ return {{ width: 1000, height: 700, top: 0, left: 0, right: 1000, bottom: 700 }}; }}
  querySelector(sel) {{ return new MockElement('div'); }}
  querySelectorAll(sel) {{ return []; }}
  addEventListener() {{}}
  removeEventListener() {{}}
  focus() {{}}
  select() {{}}
}}

const elements = new Map();
function getOrCreate(id, tag='div') {{
  if (!elements.has(id)) {{
    elements.set(id, new MockElement(tag, id));
  }}
  return elements.get(id);
}}

global.window = {{
  matchMedia: () => ({{ matches: false, addEventListener: () => {{}}, removeEventListener: () => {{}} }}),
  addEventListener: () => {{}},
  removeEventListener: () => {{}},
  location: {{ search: '', hash: '' }},
  localStorage: {{ getItem: () => null, setItem: () => {{}} }}
}};
global.document = {{
  documentElement: getOrCreate('html', 'html'),
  body: getOrCreate('body', 'body'),
  getElementById(id) {{ return getOrCreate(id); }},
  querySelector(sel) {{
    if (sel.startsWith('#')) return document.getElementById(sel.slice(1));
    return new MockElement('div');
  }},
  querySelectorAll(sel) {{ return []; }},
  addEventListener() {{}},
  removeEventListener() {{}},
  createElement(tag) {{ return new MockElement(tag); }}
}};
global.location = global.window.location;
global.localStorage = global.window.localStorage;
global.requestAnimationFrame = (cb) => setTimeout(cb, 0);

eval(scriptContent);

const input = {json.dumps(sample)};
const result = convertAsciidocToHtml(input);
console.log(JSON.stringify({{ html: result }}));
process.exit(0);
"""
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f_script:
        f_script.write(main_script)
        script_file = f_script.name

    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f_runner:
        f_runner.write(runner_code)
        runner_file = f_runner.name

    proc = subprocess.run(["node", runner_file, script_file], capture_output=True, text=True, timeout=5)
    assert proc.returncode == 0, f"Node eval failed: {proc.stderr}"
    res_data = json.loads(proc.stdout)
    js_html = res_data["html"]

    # JS 런타임 결과 검증
    assert "<h2>프라이빗 클라우드 자격 검증의 전략적 가치</h2>" in js_html
    assert "<p>+</p>" not in js_html
    assert ">+<" not in js_html
    assert "<li>운영 리스크 완화 (Mitigate Operational Risk)\n<p>컴퓨트, 스토리지, 네트워킹 계층 전반의 관리를 표준화하여 설정 오류(misconfigurations), 다운타임, 보안 취약점을 최소화한다.</p>\n</li>" in js_html


def test_aot_render_adoc_math():
    """인라인 stem/latexmath/asciimath 및 블록 latexmath 수식이 올바른 시맨틱 HTML 로 컴파일되는지 검증."""
    sample = """
== 수식 테스트
아인슈타인의 공식은 stem:[E = mc^2] 이며,
통계 공식은 latexmath:[\\sum_{i=1}^n x_i] 입니다.
또한 간단한 수식은 asciimath:[sqrt(x)] 로 표기합니다.

[latexmath]
++++
\\nabla \\times \\mathbf{E} = -\\frac{\\partial \\mathbf{B}}{\\partial t}
++++

[stem]
----
x = \\frac{-b \\pm \\sqrt{b^2 - 4ac}}{2a}
----
"""
    html_out = render_adoc_to_html(sample)
    # 1. 인라인 수식 검증
    assert '<span class="math inline" data-math="stem"><code>E = mc^2</code></span>' in html_out
    assert '<span class="math inline" data-math="latexmath"><code>\\sum_{i=1}^n x_i</code></span>' in html_out
    assert '<span class="math inline" data-math="asciimath"><code>sqrt(x)</code></span>' in html_out

    # 2. 블록 수식 검증
    assert '<div class="mathblock display" data-math="latexmath"><div class="content"><pre class="math"><code>\\nabla \\times \\mathbf{E} = -\\frac{\\partial \\mathbf{B}}{\\partial t}</code></pre></div></div>' in html_out
    assert '<div class="mathblock display" data-math="stem"><div class="content"><pre class="math"><code>x = \\frac{-b \\pm \\sqrt{b^2 - 4ac}}{2a}</code></pre></div></div>' in html_out


def test_aot_render_adoc_cross_reference_and_anchors():
    """앵커 식별자([#id], [[id]]) 및 크로스레퍼런스(<<id, label>>, xref:id[label]) 렌더링 검증."""
    sample = """
상세 내용은 <<sec-arch, 아키텍처 섹션>> 및 <<sec-intro>> 를 참조하라.
또한 xref:note-box[주의사항] 도 확인해야 한다.

[#sec-intro]
== 서론 섹션
[[inline-anchor]]이곳은 인라인 앵커가 포함된 본문입니다.

== [#sec-arch] 아키텍처 섹션

[#note-box]
[NOTE]
====
중요 주의사항입니다.
====

[#p-target]
이 단락은 단독 앵커가 부여된 단락입니다.
"""
    html_out = render_adoc_to_html(sample)
    # 1. 크로스레퍼런스 링크 검증
    assert '<a href="#sec-arch" class="xref">아키텍처 섹션</a>' in html_out
    assert '<a href="#sec-intro" class="xref">sec-intro</a>' in html_out
    assert '<a href="#note-box" class="xref">주의사항</a>' in html_out

    # 2. 앵커 주입 검증
    assert '<h2 id="sec-intro">서론 섹션</h2>' in html_out
    assert '<a id="inline-anchor" class="anchor"></a>' in html_out
    assert '<h2 id="sec-arch">아키텍처 섹션</h2>' in html_out
    assert '<div class="admonitionblock note" id="note-box">' in html_out
    assert '<p id="p-target">이 단락은 단독 앵커가 부여된 단락입니다.</p>' in html_out


def test_prompts_adoc_phase1_guidelines():
    """render_detail_prompt_adoc 에 수식 및 상호참조 지침이 정상 포함되는지 검증."""
    prompt_adoc = render_detail_prompt("샘플 텍스트", [], merged=False, format="adoc")
    assert "stem:[공식]" in prompt_adoc
    assert "[latexmath]" in prompt_adoc
    assert "[#섹션ID]" in prompt_adoc
    assert "<<섹션ID, 제목>>" in prompt_adoc


def test_aot_render_latex_math_delimiters():
    """인라인 $...$, $$...$$, \\(...\\) 수식이 올바른 시맨틱 HTML 로 컴파일되는지 검증."""
    sample = """
경제 내 과업 $t \\in \\{1, \\dots, T\\}$ 및 직종 고용 비중 $\\lambda_o$ 입니다.
인라인 괄호 수식은 \\(\\omega_{o,t} \\ge 0\\) 입니다.
블록 수식은 다음과 같습니다:
$$Y = \\sum_{o} \\lambda_o \\sum_{t} \\omega_{o,t} y_{o,t}$$
"""
    html_out = render_adoc_to_html(sample)
    assert '<span class="math inline" data-math="latex"><code>t \\in \\{1, \\dots, T\\}</code></span>' in html_out
    assert '<span class="math inline" data-math="latex"><code>\\lambda_o</code></span>' in html_out
    assert '<span class="math inline" data-math="latex"><code>\\omega_{o,t} \\ge 0</code></span>' in html_out
    assert '<span class="math inline" data-math="latex"><code>Y = \\sum_{o} \\lambda_o \\sum_{t} \\omega_{o,t} y_{o,t}</code></span>' in html_out


def test_graphview_and_shared_html_katex_integration():
    """GRAPH_HTML 및 shared_html 에 KaTeX 의존성 및 렌더링 파이프라인이 정상 통합되었는지 검증."""
    shared = shared_html({"title": "수식 문서", "detail": "경제 모형 $\\lambda_o$ 및 stem:[E = mc^2]"})

    for page in (GRAPH_HTML, shared):
        assert "katex.min.css" in page
        assert "katex.min.js" in page
        assert "auto-render.min.js" in page
        assert "applyMathRendering" in page
        assert "renderMathInElement" in page
        assert "DOMPURIFY_OPTS" in page






