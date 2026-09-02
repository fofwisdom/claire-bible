"""원문 테이블 적재 및 본문 글자 수 제한 예외 처리 단위 테스트."""

import pytest
from claire.extract.prompts import (
    PROMPT_VERSION,
    doc_to_prompt,
    extract_system_prompt,
    render_detail_prompt_adoc,
    render_detail_prompt_md,
)
from claire.extract.table_budget import (
    extract_tables_from_text,
    slice_text_with_table_exemption,
    split_text_segments,
)
from claire.ingest.fetchers.web import _extract_html
from claire.ontology.base import Document


def test_split_text_segments_markdown_table():
    text = (
        "Here is the introduction.\n\n"
        "| Model | Score | Latency |\n"
        "| --- | --- | --- |\n"
        "| Gemini 3.7 | 95.2 | 120ms |\n"
        "| Claude 3.5 | 93.8 | 150ms |\n\n"
        "And here is the conclusion."
    )
    segments = split_text_segments(text)
    assert len(segments) == 3
    assert not segments[0].is_table
    assert "introduction" in segments[0].content

    assert segments[1].is_table
    assert "Gemini 3.7" in segments[1].content
    assert "| --- | --- | --- |" in segments[1].content

    assert not segments[2].is_table
    assert "conclusion" in segments[2].content


def test_split_text_segments_asciidoc_table():
    text = (
        "= Document Title\n\n"
        "[cols=\"2,1,1\", options=\"header\"]\n"
        "|===\n"
        "| Feature | Status | Notes\n"
        "| Async I/O | Supported | High performance\n"
        "| Clustering | Experimental | Under test\n"
        "|===\n\n"
        "Final remarks here."
    )
    segments = split_text_segments(text)
    assert len(segments) == 3
    assert not segments[0].is_table
    assert "Document Title" in segments[0].content

    assert segments[1].is_table
    assert "Async I/O" in segments[1].content
    assert "|===" in segments[1].content

    assert not segments[2].is_table
    assert "Final remarks" in segments[2].content


def test_slice_text_with_table_exemption_preserves_tables_and_exempts_chars():
    # 1. 일반 본문 15,000자 생성 (limit=12,000자 초과)
    long_prose_before = "A" * 8000
    long_prose_after = "B" * 7000  # 총 일반 본문 = 15,000자

    # 2. 대용량 테이블 생성 (3,000자 분량)
    table_lines = [
        "| Item ID | Metric Name | Value | Description |",
        "| --- | --- | --- | --- |",
    ]
    for i in range(50):
        table_lines.append(f"| ITEM_{i:03d} | METRIC_{i} | {i * 12.5:.2f} | Detailed observation data for item {i:03d} |")
    large_table = "\n" + "\n".join(table_lines) + "\n"
    assert len(large_table) > 2500

    full_text = f"{long_prose_before}{large_table}{long_prose_after}"

    # 3. limit=12,000으로 슬라이싱 수행
    result = slice_text_with_table_exemption(full_text, limit=12000)

    # 4. 검증:
    # 1) 테이블 전체가 단 1글자도 누락 없이 완벽하게 포함되어 있어야 함
    assert large_table in result
    assert "ITEM_000" in result
    assert "ITEM_049" in result

    # 2) 일반 본문은 limit(12,000자)만큼 온전히 소비되어야 함
    # (prose_before 8,000자 + prose_after에서 4,000자 = 총 12,000자)
    assert "A" * 8000 in result
    assert "B" * 4000 in result
    assert "B" * 4001 not in result

    # 3) 전체 결과 길이는 (일반 본문 12,000자 + 테이블 전체 길이)가 되어야 함
    # 즉, 테이블 글자 수가 12,000자 예산을 갉아먹지 않음!
    prose_only, tables = extract_tables_from_text(result)
    assert len(prose_only) == 12000
    assert len(tables) == 1


def test_doc_to_prompt_table_budget_exemption():
    # 단일 문서 doc_to_prompt 테스트
    prose = "Introduction text. " + ("X" * 15000)
    table = "\n| Header1 | Header2 |\n| --- | --- |\n| Val1 | Val2 |\n"
    doc = Document(
        title="Test Doc",
        url="https://example.com/test",
        raw_text=f"{prose}\n\n{table}",
    )

    prompt = doc_to_prompt(doc)
    assert "TITLE: Test Doc" in prompt
    assert "URL: https://example.com/test" in prompt
    assert "CONTENT:" in prompt
    # 테이블이 프롬프트 본문에 온전히 보존되어 있어야 함
    assert "| Header1 | Header2 |" in prompt
    assert "| Val1 | Val2 |" in prompt


def test_extract_html_table_to_markdown():
    html = """
    <html>
      <head><title>Benchmark Results</title></head>
      <body>
        <h1>Benchmark Report</h1>
        <p>Overview of the benchmark run.</p>
        <table class="data-table">
          <caption>Performance Comparison</caption>
          <thead>
            <tr><th>Model</th><th>Throughput (tok/s)</th><th>TTFT (ms)</th></tr>
          </thead>
          <tbody>
            <tr><td>Model Alpha</td><td>150.4</td><td>45</td></tr>
            <tr><td>Model Beta</td><td>110.2</td><td>60</td></tr>
          </tbody>
        </table>
        <p>Conclusion of the test.</p>
      </body>
    </html>
    """
    title, text, links, anchors, err, images = _extract_html(html, base_url="https://example.com")
    assert title == "Benchmark Results"
    assert err is None
    assert "Benchmark Report" in text
    assert "Performance Comparison" in text

    # 마크다운 테이블 형태로 구조화되어 텍스트에 보존되었는지 검증
    assert "| Model | Throughput (tok/s) | TTFT (ms) |" in text
    assert "| --- | --- | --- |" in text
    assert "| Model Alpha | 150.4 | 45 |" in text
    assert "| Model Beta | 110.2 | 60 |" in text
    assert "Conclusion of the test." in text


def test_prompt_rules_contain_table_preservation_directives():
    # 1. 추출 프롬프트 검증
    sys_prompt = extract_system_prompt("{ontology}")
    assert "PROMPT_VERSION" in globals() or PROMPT_VERSION == "extract-v6"
    assert "TABLES & DATA MATRICES" in sys_prompt
    assert "MUST NOT omit or ignore the data inside tables" in sys_prompt

    # 2. 가독 렌더 프롬프트 검증 (테이블 보존 + 미디어 제거 허용 조건)
    md_prompt = render_detail_prompt_md("Sample Body", [], merged=False)
    assert "테이블 보존" in md_prompt
    assert "임의로 생략하거나 문장으로 축약하지 말고" in md_prompt
    assert "테이블 내에 들어있는 미디어" in md_prompt
    assert "제거·생략을 허용한다" in md_prompt

    # 3. AsciiDoc 가독 렌더링 프롬프트 검증 (테이블 보존 + 미디어 제거 허용 조건)
    adoc_prompt = render_detail_prompt_adoc("Sample Body", [], merged=False)
    assert "테이블 보존" in adoc_prompt
    assert "|===" in adoc_prompt
    assert "테이블 내에 들어있는 미디어" in adoc_prompt
    assert "제거·생략을 허용한다" in adoc_prompt

    # 4. 이미지 블록 지침 검증
    from claire.extract.prompts import images_block, images_block_adoc

    imgs = [{"url": "https://example.com/diag.png", "alt": "도식", "caption": "설명"}]
    img_block_md = images_block(imgs)
    img_block_adoc = images_block_adoc(imgs)
    assert "테이블(표) 내부에 삽입되어 있던 부속 아이콘/미디어" in img_block_md
    assert "테이블(표) 내부에 삽입되어 있던 부속 아이콘/미디어" in img_block_adoc


def test_extract_html_table_with_media_removal():
    """테이블 셀 내부에 이미지/아이콘/미디어 태그가 포함되어 있어도 텍스트가 안전하게 보존되고 미디어는 제거되는지 검증."""
    html = """
    <html>
      <head><title>System Architecture Benchmark</title></head>
      <body>
        <h1>Benchmark Report</h1>
        <figure>
          <img src="https://example.com/arch-diag.png" alt="Architecture Diagram" width="800" height="600" />
          <figcaption>System Architecture Overview Diagram</figcaption>
        </figure>
        <p>Comparison of systems with status icons in table.</p>
        <table class="data-table">
          <caption>System Feature Matrix</caption>
          <thead>
            <tr>
              <th><img src="https://example.com/header-icon.png" /> Feature</th>
              <th>Status</th>
              <th>Performance</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td><img src="https://example.com/tick.png" width="16" height="16" /> Core Engine</td>
              <td><svg><circle cx="5" cy="5" r="5"/></svg> Operational</td>
              <td>1200 req/s <iframe src="https://example.com/embed"></iframe></td>
            </tr>
            <tr>
              <td><img src="https://example.com/cross.png" /> Legacy Bridge</td>
              <td><video src="https://example.com/video.mp4"></video> Deprecated</td>
              <td>0 req/s</td>
            </tr>
          </tbody>
        </table>
        <p>End of report.</p>
      </body>
    </html>
    """
    title, text, links, anchors, err, images = _extract_html(html, base_url="https://example.com")
    assert title == "System Architecture Benchmark"
    assert err is None

    # 1. 마크다운 테이블 구조 및 텍스트/수치가 온전히 추출되었는지 검증
    assert "| Feature | Status | Performance |" in text
    assert "| Core Engine | Operational | 1200 req/s |" in text
    assert "| Legacy Bridge | Deprecated | 0 req/s |" in text

    # 2. 테이블 내부 이미지는 후보 이미지 목록(images)에서 제외되고, 본문 figure 다이어그램만 수집되었는지 검증
    img_urls = [im["url"] for im in images]
    assert "https://example.com/arch-diag.png" in img_urls
    assert "https://example.com/header-icon.png" not in img_urls
    assert "https://example.com/tick.png" not in img_urls
    assert "https://example.com/cross.png" not in img_urls

