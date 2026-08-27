"""AOT(Ahead-of-Time) 사전 렌더러 모듈.

Antora의 AOT 컴파일 철학을 계승하여, 클라이언트 브라우저가 Asciidoctor.js JIT 파서나
'unsafe-eval' CSP 없이 순수 HTML/CSS만으로 기술 문서를 즉시 렌더링할 수 있도록
백엔드(Ingest/DB 저장/API 서빙) 단계에서 시맨틱 HTML로 사전 변환합니다.
"""

from __future__ import annotations

import html
import re


def _inline_adoc_format(text: str) -> str:
    """AsciiDoc 인라인 서식(강조, 형광, 링크, 코드 등)을 시맨틱 HTML로 변환."""
    if not text:
        return ""
    # HTML 특수문자 이스케이프 선행
    s = html.escape(text, quote=False)
    # 줄바꿈: ' +' (공백 + 플러스 기호가 라인 끝 또는 뒤에 올 때) -> <br>
    s = re.sub(r"\s+\+\s*(?:$|\n)", "<br>", s)
    # 형광 하이라이트: #텍스트# -> <mark>텍스트</mark>
    s = re.sub(r"#(?!\s)([^#\n]+?)(?<!\s)#", r"<mark>\1</mark>", s)
    # 굵은 글씨: *텍스트* -> <strong>텍스트</strong>
    s = re.sub(r"\*(?!\s)([^*\n]+?)(?<!\s)\*", r"<strong>\1</strong>", s)
    # 기울임꼴: _텍스트_ -> <em>텍스트</em>
    s = re.sub(r"_(?!\s)([^_\n]+?)(?<!\s)_", r"<em>\1</em>", s)
    # 인라인 코드: `텍스트` -> <code>텍스트</code>
    s = re.sub(r"`(?!\s)([^`\n]+?)(?<!\s)`", r"<code>\1</code>", s)
    # 명시적 링크: https://url[텍스트] -> <a href="url" target="_blank" rel="noopener">텍스트</a>
    s = re.sub(
        r"(https?://[^\s\[\]]+)\[(.*?)\]",
        r'<a href="\1" target="_blank" rel="noopener">\2</a>',
        s,
    )
    # 자동 URL 링크 (대괄호 없는 단독 URL)
    s = re.sub(
        r'(?<!href=")(https?://[^\s<>"\'\)]+)',
        r'<a href="\1" target="_blank" rel="noopener">\1</a>',
        s,
    )
    return s


def _split_table_cells(line: str) -> list[str]:
    """한 줄에서 unescaped '|' 구분자로 셀 목록 추출."""
    raw = line.strip()
    if not raw.startswith("|"):
        return [raw]
    # 선두 '|' 제거
    raw = raw[1:]
    # 말미 '|' 제거 (이스케이프 되지 않은 경우)
    if raw.endswith("|") and not raw.endswith(r"\|"):
        raw = raw[:-1]
    # 이스케이프 되지 않은 '|' 기준으로 분리
    parts = re.split(r"(?<!\\)\|", raw)
    return [p.replace(r"\|", "|").strip() for p in parts]


def _parse_cell_spec(spec_str: str) -> tuple[int, int, str | None, str | None]:
    """AsciiDoc 셀 접두사(spec)에서 (colspan, rowspan, align, style) 파싱."""
    colspan = 1
    rowspan = 1
    align = None
    style = None
    if not spec_str:
        return colspan, rowspan, align, style

    spec = spec_str.strip()
    m_span = re.search(r"(\d+)?\.(\d+)\+", spec)
    if m_span:
        if m_span.group(1):
            colspan = int(m_span.group(1))
        if m_span.group(2):
            rowspan = int(m_span.group(2))
    else:
        m_col = re.search(r"(?<!\.)(\d+)\+", spec)
        if m_col:
            colspan = int(m_col.group(1))
        m_row = re.search(r"\.(\d+)\+", spec)
        if m_row:
            rowspan = int(m_row.group(1))
        m_dup = re.search(r"^(\d+)\*$", spec)
        if m_dup:
            colspan = int(m_dup.group(1))

    if "^" in spec:
        align = "center"
    elif ">" in spec:
        align = "right"
    elif "<" in spec:
        align = "left"

    return colspan, rowspan, align, style


class _AdocTableCell:
    def __init__(self, text: str, spec: str = ""):
        self.text = text
        self.spec = spec
        self.colspan, self.rowspan, self.align, self.style = _parse_cell_spec(spec)


def _parse_cols_attr(text: str) -> int | None:
    """AsciiDoc [cols="..."] 속성 문자열에서 컬럼 수 파싱."""
    if not text:
        return None
    m = re.search(r'cols=["\']?([^"\'\]]+)["\']?', text, re.IGNORECASE)
    cols_val = m.group(1).strip() if m else text.strip("[] \t")

    star_m = re.match(r"^(\d+)\*", cols_val)
    if star_m:
        return int(star_m.group(1))

    if "," in cols_val:
        parts = [p.strip() for p in cols_val.split(",") if p.strip()]
        if parts:
            total = 0
            for p in parts:
                sm = re.match(r"^(\d+)\*", p)
                if sm:
                    total += int(sm.group(1))
                else:
                    total += 1
            return total if total > 0 else None

    if cols_val.isdigit():
        return int(cols_val)

    return None


def _extract_adoc_table_cells(
    table_lines: list[str], explicit_cols: int | None = None
) -> tuple[list[_AdocTableCell], int]:
    """AsciiDoc 테이블 줄들에서 개별 셀 객체 목록과 전체 열(column) 수를 파싱 및 산출."""
    placeholder = "\x00"
    cell_token_re = re.compile(
        r"(?:^|(?<=\s))((?:\d*\.?\d+\+|\d+\*)?[\^<>]?[a-z]?|[\^<>]?[a-z]?)\|"
    )

    cells: list[_AdocTableCell] = []
    first_line_cols: int | None = None
    first_block_cols = 0
    in_first_block = True

    for line in table_lines:
        raw = line.strip()
        if not raw:
            if in_first_block and cells:
                in_first_block = False
            continue

        safe = raw.replace(r"\|", placeholder)
        matches = list(cell_token_re.finditer(safe))

        if not matches or matches[0].start() > 0:
            # 선두 구분자가 없는 경우 -> 직전 셀 텍스트의 줄바꿈 연속으로 처리
            if cells and not matches:
                cells[-1].text += " " + raw.replace(r"\|", "|")
                continue
            elif not matches:
                cell = _AdocTableCell(raw.replace(r"\|", "|"))
                cells.append(cell)
                if in_first_block:
                    first_block_cols += cell.colspan
                continue

        line_cells_count = 0
        for i in range(len(matches)):
            m = matches[i]
            spec = m.group(1).strip()
            start_pos = m.end()
            end_pos = matches[i + 1].start() if i + 1 < len(matches) else len(safe)
            cell_text = safe[start_pos:end_pos].strip().replace(placeholder, "|")
            cell = _AdocTableCell(cell_text, spec)
            cells.append(cell)
            line_cells_count += cell.colspan
            if in_first_block:
                first_block_cols += cell.colspan

        if first_line_cols is None and line_cells_count > 0:
            first_line_cols = line_cells_count

    num_cols = explicit_cols
    if not num_cols or num_cols <= 0:
        if first_line_cols and first_line_cols > 1:
            num_cols = first_line_cols
        elif first_block_cols > 1:
            num_cols = first_block_cols
        else:
            num_cols = 1

    return cells, num_cols


def _parse_adoc_table_rows(
    table_lines: list[str], explicit_cols: int | None = None
) -> list[list[_AdocTableCell]]:
    """AsciiDoc 테이블 셀들을 rowspan/colspan 및 빈 열을 고려하여 2차원 행/열 그리드로 조립."""
    cells, num_cols = _extract_adoc_table_cells(table_lines, explicit_cols=explicit_cols)
    if not cells:
        return []

    rows: list[list[_AdocTableCell]] = []
    cell_idx = 0
    occupied = [0] * num_cols

    while cell_idx < len(cells):
        row_cells: list[_AdocTableCell] = []
        col = 0
        while col < num_cols and cell_idx < len(cells):
            if occupied[col] > 0:
                occupied[col] -= 1
                col += 1
                continue

            cell = cells[cell_idx]
            cell_idx += 1
            row_cells.append(cell)

            if cell.rowspan > 1:
                for span_c in range(cell.colspan):
                    if col + span_c < num_cols:
                        occupied[col + span_c] = cell.rowspan - 1

            col += cell.colspan

        while col < num_cols:
            if occupied[col] > 0:
                occupied[col] -= 1
            col += 1

        if row_cells:
            rows.append(row_cells)

    return rows


def _render_table_html(
    table_lines: list[str], block_meta: dict[str, str]
) -> str:
    explicit_cols = _parse_cols_attr(block_meta.get("cols", ""))
    rows = _parse_adoc_table_rows(table_lines, explicit_cols=explicit_cols)
    if not rows:
        return ""

    t_html = ["<table>"]
    title = block_meta.get("title")
    if title:
        t_html.append(f"<caption>{html.escape(title)}</caption>")

    def _render_cell(cell: _AdocTableCell, tag: str) -> str:
        attrs = []
        if cell.rowspan > 1:
            attrs.append(f'rowspan="{cell.rowspan}"')
        if cell.colspan > 1:
            attrs.append(f'colspan="{cell.colspan}"')
        if cell.align:
            attrs.append(f'style="text-align:{cell.align}"')
        attr_str = (" " + " ".join(attrs)) if attrs else ""
        return f"<{tag}{attr_str}>{_inline_adoc_format(cell.text)}</{tag}>"

    t_html.append(
        "<thead><tr>"
        + "".join(_render_cell(c, "th") for c in rows[0])
        + "</tr></thead>"
    )
    if len(rows) > 1:
        t_html.append("<tbody>")
        for r in rows[1:]:
            t_html.append(
                "<tr>"
                + "".join(_render_cell(c, "td") for c in r)
                + "</tr>"
            )
        t_html.append("</tbody>")
    t_html.append("</table>")
    return "".join(t_html)


def render_adoc_to_html(raw: str) -> str:
    """AsciiDoc 텍스트를 시맨틱 HTML 문자열로 AOT 변환."""
    if not raw or not raw.strip():
        return ""

    lines = raw.splitlines()
    out: list[str] = []
    in_block: str | None = None
    block_meta: dict[str, str] = {}
    block_lines: list[str] = []

    def flush_block() -> None:
        nonlocal in_block, block_meta, block_lines
        if not in_block:
            return

        if in_block == "quote":
            q_paragraphs = []
            curr_p = []
            for bl in block_lines:
                if not bl.strip():
                    if curr_p:
                        q_paragraphs.append("<br>".join(_inline_adoc_format(l) for l in curr_p))
                        curr_p = []
                else:
                    curr_p.append(bl)
            if curr_p:
                q_paragraphs.append("<br>".join(_inline_adoc_format(l) for l in curr_p))
            q_content = "".join(f"<p>{p}</p>" for p in q_paragraphs) if q_paragraphs else ""

            attr = ""
            if block_meta.get("author") or block_meta.get("source"):
                author = html.escape(block_meta.get("author", ""))
                source = html.escape(block_meta.get("source", ""))
                attr_text = author + (f" — {source}" if source else "")
                attr = f'<div class="attribution">{attr_text}</div>'
            out.append(f'<div class="quoteblock"><blockquote>{q_content}</blockquote>{attr}</div>')

        elif in_block == "admonition":
            adm_paragraphs = []
            curr_p = []
            for bl in block_lines:
                if not bl.strip():
                    if curr_p:
                        adm_paragraphs.append("<br>".join(_inline_adoc_format(l) for l in curr_p))
                        curr_p = []
                else:
                    curr_p.append(bl)
            if curr_p:
                adm_paragraphs.append("<br>".join(_inline_adoc_format(l) for l in curr_p))
            adm_content = "".join(f"<p>{p}</p>" for p in adm_paragraphs) if adm_paragraphs else ""

            adm_type = (block_meta.get("type") or "NOTE").lower()
            title = html.escape(block_meta.get("type") or "NOTE")
            out.append(
                f'<div class="admonitionblock {adm_type}">'
                f'<div class="title">{title}</div>'
                f'<div class="content">{adm_content}</div>'
                f'</div>'
            )

        elif in_block == "code":
            code_text = html.escape("\n".join(block_lines))
            code_text = re.sub(r"&lt;(\d+)&gt;", r'<span class="conum">&lt;\1&gt;</span>', code_text)
            lang = html.escape(block_meta.get("lang", ""))
            lang_cls = f" language-{lang}" if lang else ""
            out.append(
                f'<div class="listingblock"><div class="content">'
                f'<pre><code class="{lang_cls}">{code_text}</code></pre>'
                f'</div></div>'
            )

        elif in_block == "table":
            tbl_html = _render_table_html(block_lines, block_meta)
            if tbl_html:
                out.append(tbl_html)

        in_block = None
        block_meta = {}
        block_lines = []

    pending_meta: dict[str, str] | None = None

    for line in lines:
        trimmed = line.strip()

        if not in_block:
            # 1. 인용 메타데이터: [quote, 저자, 출처]
            qm = re.match(r"^\[quote(?:,\s*([^,\]]+))?(?:,\s*([^\]]+))?\]", trimmed, re.IGNORECASE)
            if qm:
                pending_meta = {
                    "kind": "quote",
                    "author": (qm.group(1) or "").strip(),
                    "source": (qm.group(2) or "").strip(),
                }
                continue

            # 2. Admonition 메타데이터: [NOTE], [TIP], [IMPORTANT], [WARNING], [CAUTION]
            am = re.match(r"^\[(NOTE|IMPORTANT|TIP|WARNING|CAUTION)\]", trimmed, re.IGNORECASE)
            if am:
                pending_meta = {"kind": "admonition", "type": am.group(1).upper()}
                continue

            # 3. 코드 블록 메타데이터: [source, python]
            sm = re.match(r"^\[source(?:,\s*([a-zA-Z0-9_-]+))?\]", trimmed, re.IGNORECASE)
            if sm:
                pending_meta = {"kind": "code", "lang": (sm.group(1) or "").strip()}
                continue

            # 3-1. 테이블 메타데이터: [cols="..."] 또는 [%header...]
            tm = re.match(r"^\[(.*cols.*|.*header.*|\d+\*|[0-9,]+)\]$", trimmed, re.IGNORECASE)
            if tm:
                if pending_meta and pending_meta.get("kind") == "table":
                    pending_meta["cols"] = tm.group(1)
                else:
                    pending_meta = {"kind": "table", "cols": tm.group(1)}
                continue

            # 3-2. 테이블 제목 / 캡션: .Table Title
            title_m = re.match(r"^\.([^\.\s].*)$", trimmed)
            if title_m:
                if pending_meta and pending_meta.get("kind") == "table":
                    pending_meta["title"] = title_m.group(1).strip()
                else:
                    pending_meta = {"kind": "table", "title": title_m.group(1).strip()}
                continue

            # 4. 블록 구분자 진입
            if trimmed == "____":
                in_block = "quote"
                block_meta = pending_meta if (pending_meta and pending_meta.get("kind") == "quote") else {}
                pending_meta = None
                block_lines = []
                continue

            if trimmed == "====":
                in_block = "admonition"
                block_meta = (
                    pending_meta
                    if (pending_meta and pending_meta.get("kind") == "admonition")
                    else {"type": "NOTE"}
                )
                pending_meta = None
                block_lines = []
                continue

            if trimmed == "----":
                in_block = "code"
                block_meta = pending_meta if (pending_meta and pending_meta.get("kind") == "code") else {}
                pending_meta = None
                block_lines = []
                continue

            if trimmed == "|===":
                in_block = "table"
                block_meta = pending_meta if (pending_meta and pending_meta.get("kind") == "table") else {}
                pending_meta = None
                block_lines = []
                continue

            # 5. 이미지 블록: image::url[alt, title="캡션"]
            img_m = re.match(
                r'^image::([^\[]+)\[([^,\]]*)(?:,\s*title=(?:"([^"]*)"|\'([^\']*)\'|([^\]]*)))?\]',
                trimmed,
            )
            if img_m:
                src = img_m.group(1).strip()
                alt = (img_m.group(2) or "").strip()
                cap = img_m.group(3) or img_m.group(4) or img_m.group(5) or ""
                cap_html = f'<div class="title">{html.escape(cap)}</div>' if cap else ""
                out.append(
                    f'<div class="imageblock"><img src="{html.escape(src)}" alt="{html.escape(alt)}">'
                    f"{cap_html}</div>"
                )
                continue

            # 6. 코드 라인 콜아웃: <1> 설명
            col_m = re.match(r"^<(\d+)>\s*(.+)", trimmed)
            if col_m:
                conum = col_m.group(1)
                text = _inline_adoc_format(col_m.group(2))
                out.append(f'<div class="colist"><span class="conum">&lt;{conum}&gt;</span> {text}</div>')
                continue

            # 7. 섹션 헤더 (=, ==, ===, ====) 및 문서 속성
            h1_m = re.match(r"^=\s+(.+)$", trimmed)
            if h1_m:
                out.append(f"<h1>{_inline_adoc_format(h1_m.group(1))}</h1>")
                continue
            h2_m = re.match(r"^==\s+(.+)$", trimmed)
            if h2_m:
                out.append(f"<h2>{_inline_adoc_format(h2_m.group(1))}</h2>")
                continue
            h3_m = re.match(r"^===\s+(.+)$", trimmed)
            if h3_m:
                out.append(f"<h3>{_inline_adoc_format(h3_m.group(1))}</h3>")
                continue
            h4_m = re.match(r"^====\s+(.+)$", trimmed)
            if h4_m:
                out.append(f"<h4>{_inline_adoc_format(h4_m.group(1))}</h4>")
                continue

            # 문서 속성 (:key: value)
            attr_m = re.match(r"^:[a-zA-Z0-9_-]+:\s*(.*)$", trimmed)
            if attr_m:
                # 일반적인 Asciidoc 속성은 시각적으로 렌더링하지 않습니다.
                continue

            # 8. 단일 행 인라인 Admonition (예: NOTE: 설명)
            single_adm = re.match(r"^(NOTE|TIP|IMPORTANT|WARNING|CAUTION):\s*(.+)$", trimmed, re.IGNORECASE)
            if single_adm:
                adm_type = single_adm.group(1).upper()
                adm_text = _inline_adoc_format(single_adm.group(2))
                out.append(
                    f'<div class="admonitionblock {adm_type.lower()}">'
                    f'<div class="title">{html.escape(adm_type)}</div>'
                    f'<div class="content"><p>{adm_text}</p></div>'
                    f'</div>'
                )
                continue

            # 9. 리스트 항목 (*, -, .)
            list_m = re.match(r"^([\*\-\.])\s+(.+)$", trimmed)
            if list_m:
                item_text = _inline_adoc_format(list_m.group(2))
                out.append(f"<ul><li>{item_text}</li></ul>")
                continue

            # 10. 빈 줄
            if not trimmed:
                continue

            # 11. 일반 단락
            out.append(f"<p>{_inline_adoc_format(trimmed)}</p>")

        else:
            # 블록 내부 처리 및 블록 닫기
            if (
                (in_block == "quote" and trimmed == "____")
                or (in_block == "admonition" and trimmed == "====")
                or (in_block == "code" and trimmed == "----")
                or (in_block == "table" and trimmed == "|===")
            ):
                flush_block()
            else:
                block_lines.append(line)

    flush_block()
    return "\n".join(out)


def render_md_to_html(raw: str) -> str:
    """Markdown 텍스트를 시맨틱 HTML 문자열로 AOT 변환."""
    if not raw or not raw.strip():
        return ""

    # 형광 하이라이트: ==텍스트== -> <mark>텍스트</mark>
    text = re.sub(r"==([^=\n]+?)==", r"<mark>\1</mark>", raw)

    try:
        import markdown

        html_out = markdown.markdown(
            text,
            extensions=["fenced_code", "tables", "nl2br"],
        )
        return html_out
    except ImportError:
        pass

    try:
        import markdown_it

        md = markdown_it.MarkdownIt("commonmark", {"breaks": True, "html": True})
        return md.render(text)
    except ImportError:
        pass

    # 폴백: 최소 변환
    paragraphs = [html.escape(p.strip()) for p in text.split("\n\n") if p.strip()]
    return "".join(f"<p>{p}</p>" for p in paragraphs)


def render_to_html(raw: str, format: str = "md") -> str:
    """포맷(adoc 또는 md)에 따라 본문 텍스트를 시맨틱 HTML로 AOT 사전 변환."""
    if not raw or not raw.strip():
        return ""

    fmt = (format or "md").strip().lower()
    if fmt in ("asciidoc", "adoc"):
        return render_adoc_to_html(raw)
    return render_md_to_html(raw)
