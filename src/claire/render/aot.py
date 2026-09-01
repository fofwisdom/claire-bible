"""AOT(Ahead-of-Time) 사전 렌더러 모듈.

Antora의 AOT 컴파일 철학을 계승하여, 클라이언트 브라우저가 Asciidoctor.js JIT 파서나
'unsafe-eval' CSP 없이 순수 HTML/CSS만으로 기술 문서를 즉시 렌더링할 수 있도록
백엔드(Ingest/DB 저장/API 서빙) 단계에서 시맨틱 HTML로 사전 변환합니다.
"""

from __future__ import annotations

import html
import re


def _inline_adoc_format(text: str) -> str:
    """AsciiDoc 인라인 서식(강조, 형광, 링크, 코드, 첨자 등)을 시맨틱 HTML로 변환."""
    if not text:
        return ""
    # HTML 특수문자 이스케이프 선행
    s = html.escape(text, quote=False)

    # 1. 수식(Math) 보호: stem:[...], latexmath:[...], asciimath:[...], $...$, \(...\)
    math_spans: list[str] = []

    def _save_math(m: re.Match) -> str:
        kind = m.group(1).lower()
        math_content = m.group(2)
        math_spans.append(f'<span class="math inline" data-math="{kind}"><code>{math_content}</code></span>')
        return f"\x00ADOCMATH{len(math_spans)-1}\x00"

    s = re.sub(r"(stem|latexmath|asciimath):\[(.*?)\]", _save_math, s, flags=re.IGNORECASE)

    def _save_latex_inline(m: re.Match) -> str:
        math_content = m.group(1)
        math_spans.append(f'<span class="math inline" data-math="latex"><code>{math_content}</code></span>')
        return f"\x00ADOCMATH{len(math_spans)-1}\x00"

    s = re.sub(r"\\\((.*?)\\\)", _save_latex_inline, s)
    s = re.sub(r"\$\$([^\$]+?)\$\$", _save_latex_inline, s)
    s = re.sub(r"(?<![\w\\\$])\$([^\$\n]+?)\$(?![\w\$])", _save_latex_inline, s)

    # 2. 인라인 코드 보호 (코드 블록 내부의 *, _, # 등이 다른 서식으로 변환되는 것 방지)
    code_spans: list[str] = []

    def _save_code(m: re.Match) -> str:
        code_spans.append(f"<code>{m.group(1)}</code>")
        return f"\x00ADOCCODE{len(code_spans)-1}\x00"

    s = re.sub(r"`(?!\s)([^`\n]+?)(?<!\s)`", _save_code, s)
    s = re.sub(r"\+\+(?!\s)([^\+\n]+?)(?<!\s)\+\+", _save_code, s)

    # 3. 명시적 링크 보호: https://url[텍스트] -> <a href="url" target="_blank" rel="noopener">{label}</a>
    link_spans: list[str] = []

    def _save_explicit_link(m: re.Match) -> str:
        url, label = m.group(1), m.group(2)
        link_spans.append(f'<a href="{url}" target="_blank" rel="noopener">{label}</a>')
        return f"\x00ADOCLINK{len(link_spans)-1}\x00"

    s = re.sub(r"(https?://[^\s\[\]]+)\[(.*?)\]", _save_explicit_link, s)

    # 4. 자동 URL 링크 (대괄호 없는 단독 URL)
    def _save_auto_link(m: re.Match) -> str:
        url = m.group(1)
        link_spans.append(f'<a href="{url}" target="_blank" rel="noopener">{url}</a>')
        return f"\x00ADOCLINK{len(link_spans)-1}\x00"

    s = re.sub(r'(?<!href=")(https?://[^\s<>"\'\)]+)', _save_auto_link, s)

    # 5. 상호 참조(Cross-references): <<anchor, label>>, <<anchor>>, xref:anchor[label]
    def _save_xref(m: re.Match) -> str:
        anchor = m.group(1).strip()
        label = (m.group(2) if len(m.groups()) >= 2 and m.group(2) else anchor).strip()
        link_spans.append(f'<a href="#{anchor}" class="xref">{label}</a>')
        return f"\x00ADOCLINK{len(link_spans)-1}\x00"

    s = re.sub(r"&lt;&lt;([a-zA-Z0-9_\-\.\:\/]+)(?:,\s*([^&]+?))?&gt;&gt;", _save_xref, s)
    s = re.sub(r"xref:([a-zA-Z0-9_\-\.\:\/]+)\[(.*?)\]", _save_xref, s, flags=re.IGNORECASE)

    # 6. 인라인 앵커: [[anchor-id]]
    s = re.sub(r"\[\[([a-zA-Z0-9_\-\.\:\/]+)\]\]", r'<a id="\1" class="anchor"></a>', s)

    # 7. 줄바꿈: ' +' (공백 + 플러스 기호가 라인 끝에 올 때) -> <br>
    s = re.sub(r"\s+\+\s*$", "<br>", s)

    # 8. 형광 하이라이트: 단독 #텍스트# (2개 이상의 연속된 ### 마스킹/패턴 등 제외)
    s = re.sub(r"(?<!#)#(?![\s#])([^#\n]+?)(?<![\s#])#(?!#)", r"<mark>\1</mark>", s)

    # 9. 굵은 글씨: **텍스트** 또는 단독 *텍스트* (2개 이상의 연속된 *** 제외)
    s = re.sub(r"\*\*(?![\s\*])([^*\n]+?)(?<![\s\*])\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"(?<!\*)\*(?![\s\*])([^*\n]+?)(?<![\s\*])\*(?!\*)", r"<strong>\1</strong>", s)

    # 10. 기울임꼴: __텍스트__ 또는 단독 _텍스트_ (2개 이상의 연속된 ___ 제외)
    s = re.sub(r"__(?![\s_])([^_\n]+?)(?<![\s_])__", r"<em>\1</em>", s)
    s = re.sub(r"(?<!_)_(?![\s_])([^_\n]+?)(?<![\s_])_(?!_)", r"<em>\1</em>", s)

    # 11. 첨자: ^위첨자^, ~아래첨자~
    s = re.sub(r"\^(?![\s\^])([^\^\n]+?)(?<![\s\^])\^", r"<sup>\1</sup>", s)
    s = re.sub(r"~(?![\s~])([^~\n]+?)(?<![\s~])~", r"<sub>\1</sub>", s)

    # 12. 보호된 링크, 인라인 코드, 수식 복원
    for i, span in enumerate(link_spans):
        s = s.replace(f"\x00ADOCLINK{i}\x00", span)
    for i, span in enumerate(code_spans):
        s = s.replace(f"\x00ADOCCODE{i}\x00", span)
    for i, span in enumerate(math_spans):
        s = s.replace(f"\x00ADOCMATH{i}\x00", span)

    return s

    return s


def _format_paragraph_lines(lines: list[str]) -> str:
    """여러 줄의 단락을 <br> 또는 공백으로 결합하여 인라인 서식 적용."""
    if not lines:
        return ""
    formatted_parts: list[str] = []
    for l in lines:
        part = _inline_adoc_format(l)
        formatted_parts.append(part)
    res = ""
    for i, p in enumerate(formatted_parts):
        if i == 0:
            res = p
        else:
            if res.endswith("<br>"):
                res += p
            else:
                res += " " + p
    return res


def _split_table_cells(line: str) -> list[str]:
    """한 줄에서 unescaped '|' 구분자로 셀 목록 추출."""
    raw = line.strip()
    if not raw.startswith("|"):
        return [raw]
    raw = raw[1:]
    if raw.endswith("|") and not raw.endswith(r"\|"):
        raw = raw[:-1]
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

    m_style = re.search(r"([a-z])(?=\||$)", spec)
    if m_style:
        style = m_style.group(1).lower()

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
            # 선두 구분자가 없는 경우 -> 직전 셀 텍스트의 줄바꿈 연속으로 처리 (개행 보존)
            if cells and not matches:
                cells[-1].text += "\n" + raw.replace(r"\|", "|")
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
    table_lines: list[str], block_meta: dict[str, str], anchor_id: str | None = None
) -> str:
    explicit_cols = _parse_cols_attr(block_meta.get("cols", ""))
    rows = _parse_adoc_table_rows(table_lines, explicit_cols=explicit_cols)
    if not rows:
        return ""

    id_attr = f' id="{html.escape(anchor_id)}"' if anchor_id else ""
    t_html = [f"<table{id_attr}>"]
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

        text = cell.text.strip()
        # 셀 내부가 AsciiDoc 블록(리스트/단락 등)이거나 스타일이 'a'인 경우
        if cell.style == "a" or (
            "\n" in text and bool(re.search(r"(?:^|\n)\s*[\*\-\.]\s+", text))
        ):
            inner_html = render_adoc_to_html(text)
            # 단일 단락 <p>...</p>인 경우 태그 언랩
            if (
                inner_html.startswith("<p>")
                and inner_html.endswith("</p>")
                and inner_html.count("<p>") == 1
                and "\n" not in inner_html
            ):
                inner_html = inner_html[3:-4]
        elif "\n\n" in text:
            inner_html = render_adoc_to_html(text)
        else:
            inner_html = _format_paragraph_lines(text.splitlines())

        return f"<{tag}{attr_str}>{inner_html}</{tag}>"

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


class _ListContext:
    def __init__(self):
        self.stack: list[tuple[str, int]] = []
        self.in_item: bool = False

    def is_active(self) -> bool:
        return len(self.stack) > 0

    def close_item(self) -> list[str]:
        if self.in_item:
            self.in_item = False
            return ["</li>"]
        return []

    def close_all(self) -> list[str]:
        out = self.close_item()
        while self.stack:
            tag, _ = self.stack.pop()
            out.append(f"</{tag}>")
            if self.stack:
                out.append("</li>")
        return out

    def adjust_level(self, tag: str, level: int) -> list[str]:
        out: list[str] = []
        if not self.stack:
            out.append(f"<{tag}>")
            self.stack.append((tag, level))
            return out

        curr_tag, curr_level = self.stack[-1]
        if level > curr_level:
            out.append(f"<{tag}>")
            self.stack.append((tag, level))
        elif level < curr_level:
            out.extend(self.close_item())
            while self.stack and self.stack[-1][1] > level:
                stag, _ = self.stack.pop()
                out.append(f"</{stag}>")
                if self.stack:
                    out.append("</li>")
            if self.stack and self.stack[-1][0] != tag:
                stag, _ = self.stack.pop()
                out.append(f"</{stag}>")
                out.append(f"<{tag}>")
                self.stack.append((tag, level))
        else:
            if curr_tag != tag:
                out.extend(self.close_item())
                stag, _ = self.stack.pop()
                out.append(f"</{stag}>")
                out.append(f"<{tag}>")
                self.stack.append((tag, level))
            else:
                out.extend(self.close_item())
        return out


def _match_list_item(trimmed: str) -> tuple[str, int, str] | None:
    """줄이 리스트 아이템인지 확인하고 (tag, level, item_text) 반환."""
    # 1. Unordered bullet with asterisks: *, **, ***
    m_star = re.match(r"^(\*{1,5})\s+(.+)$", trimmed)
    if m_star:
        return "ul", len(m_star.group(1)), m_star.group(2)

    # 2. Unordered bullet with hyphen: -
    m_hyphen = re.match(r"^-\s+(.+)$", trimmed)
    if m_hyphen:
        return "ul", 1, m_hyphen.group(1)

    # 3. Ordered with dots: ., .., ...
    m_dot = re.match(r"^(\.{1,5})\s+(.+)$", trimmed)
    if m_dot:
        return "ol", len(m_dot.group(1)), m_dot.group(2)

    # 4. Ordered with numbers: 1. 또는 1)
    m_num = re.match(r"^\d+[\.\)]\s+(.+)$", trimmed)
    if m_num:
        return "ol", 1, m_num.group(1)

    return None


def render_adoc_to_html(raw: str) -> str:
    """AsciiDoc 텍스트를 시맨틱 HTML 문자열로 AOT 변환."""
    if not raw or not raw.strip():
        return ""

    lines = raw.splitlines()
    out: list[str] = []
    in_block: str | None = None
    block_meta: dict[str, str] = {}
    block_lines: list[str] = []

    list_ctx = _ListContext()
    pending_anchor: str | None = None

    def flush_list() -> None:
        if list_ctx.is_active():
            out.extend(list_ctx.close_all())

    def flush_block() -> None:
        nonlocal in_block, block_meta, block_lines, pending_anchor
        if not in_block:
            return

        anchor_attr = f' id="{html.escape(pending_anchor)}"' if pending_anchor else ""
        pending_anchor = None

        if in_block == "quote":
            q_paragraphs = []
            curr_p = []
            for bl in block_lines:
                if not bl.strip():
                    if curr_p:
                        q_paragraphs.append(_format_paragraph_lines(curr_p))
                        curr_p = []
                else:
                    curr_p.append(bl)
            if curr_p:
                q_paragraphs.append(_format_paragraph_lines(curr_p))
            q_content = "".join(f"<p>{p}</p>" for p in q_paragraphs) if q_paragraphs else ""

            attr = ""
            if block_meta.get("author") or block_meta.get("source"):
                author = html.escape(block_meta.get("author", ""))
                source = html.escape(block_meta.get("source", ""))
                attr_text = author + (f" — {source}" if source else "")
                attr = f'<div class="attribution">{attr_text}</div>'
            out.append(f'<div class="quoteblock"{anchor_attr}><blockquote>{q_content}</blockquote>{attr}</div>')

        elif in_block == "admonition":
            adm_paragraphs = []
            curr_p = []
            for bl in block_lines:
                if not bl.strip():
                    if curr_p:
                        adm_paragraphs.append(_format_paragraph_lines(curr_p))
                        curr_p = []
                else:
                    curr_p.append(bl)
            if curr_p:
                adm_paragraphs.append(_format_paragraph_lines(curr_p))
            adm_content = "".join(f"<p>{p}</p>" for p in adm_paragraphs) if adm_paragraphs else ""

            adm_type = (block_meta.get("type") or "NOTE").lower()
            title = html.escape(block_meta.get("type") or "NOTE")
            out.append(
                f'<div class="admonitionblock {adm_type}"{anchor_attr}>'
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
                f'<div class="listingblock"{anchor_attr}><div class="content">'
                f'<pre><code class="{lang_cls}">{code_text}</code></pre>'
                f'</div></div>'
            )

        elif in_block == "math":
            math_type = html.escape(block_meta.get("type") or "latex")
            math_text = html.escape("\n".join(block_lines))
            out.append(
                f'<div class="mathblock display"{anchor_attr} data-math="{math_type}"><div class="content">'
                f'<pre class="math"><code>{math_text}</code></pre>'
                f'</div></div>'
            )

        elif in_block == "table":
            tbl_html = _render_table_html(block_lines, block_meta, anchor_id=anchor_attr.replace(' id="', '').rstrip('"') if anchor_attr else None)
            if tbl_html:
                out.append(tbl_html)

        in_block = None
        block_meta = {}
        block_lines = []

    pending_meta: dict[str, str] | None = None
    pending_block_lines: list[str] = []
    continuation_lines: list[str] = []
    normal_p_lines: list[str] = []
    in_continuation: bool = False
    pending_continuation: bool = False

    def flush_pending_single_block() -> None:
        nonlocal pending_meta, pending_block_lines, pending_anchor
        if not pending_meta or pending_meta.get("kind") not in ("quote", "admonition"):
            return
        if not pending_block_lines:
            pending_meta = None
            return
        anchor_attr = f' id="{html.escape(pending_anchor)}"' if pending_anchor else ""
        pending_anchor = None
        p_kind = pending_meta.get("kind")
        p_content = _format_paragraph_lines(pending_block_lines)
        if p_kind == "quote":
            author = html.escape(pending_meta.get("author", ""))
            source = html.escape(pending_meta.get("source", ""))
            attr_text = author + (f" — {source}" if source else "")
            attr = f'<div class="attribution">{attr_text}</div>' if attr_text else ""
            out.append(f'<div class="quoteblock"{anchor_attr}><blockquote><p>{p_content}</p></blockquote>{attr}</div>')
        elif p_kind == "admonition":
            adm_type = (pending_meta.get("type") or "NOTE").lower()
            title = html.escape(pending_meta.get("type") or "NOTE")
            out.append(
                f'<div class="admonitionblock {adm_type}"{anchor_attr}>'
                f'<div class="title">{title}</div>'
                f'<div class="content"><p>{p_content}</p></div>'
                f'</div>'
            )
        pending_meta = None
        pending_block_lines = []

    def flush_continuation() -> None:
        nonlocal in_continuation, pending_continuation, continuation_lines, pending_anchor
        if in_continuation and continuation_lines:
            p_text = _format_paragraph_lines(continuation_lines)
            anchor_attr = f' id="{html.escape(pending_anchor)}"' if pending_anchor else ""
            pending_anchor = None
            out.append(f"<p{anchor_attr}>{p_text}</p>")
        in_continuation = False
        pending_continuation = False
        continuation_lines = []

    def flush_normal_p() -> None:
        nonlocal normal_p_lines, pending_anchor
        if normal_p_lines:
            flush_list()
            p_text = _format_paragraph_lines(normal_p_lines)
            anchor_attr = f' id="{html.escape(pending_anchor)}"' if pending_anchor else ""
            pending_anchor = None
            out.append(f"<p{anchor_attr}>{p_text}</p>")
            normal_p_lines = []

    def _extract_heading_anchor(h_text: str) -> tuple[str, str | None]:
        nonlocal pending_anchor
        m = re.search(r"\[#([a-zA-Z0-9_\-\.\:\/]+)\]|\[\[([a-zA-Z0-9_\-\.\:\/]+)\]\]", h_text)
        if m:
            anc = m.group(1) or m.group(2)
            clean = (h_text[:m.start()] + h_text[m.end():]).strip()
            return clean, anc
        anc = pending_anchor
        pending_anchor = None
        return h_text, anc

    for line in lines:
        trimmed = line.strip()

        if not in_block:
            # 0. 앵커 정의 라인: [#anchor-id] 또는 [[anchor-id]]
            anchor_m = re.match(r"^\[#([a-zA-Z0-9_\-\.\:\/]+)\]$", trimmed) or re.match(
                r"^\[\[([a-zA-Z0-9_\-\.\:\/]+)\]\]$", trimmed
            )
            if anchor_m:
                flush_normal_p()
                flush_continuation()
                flush_pending_single_block()
                pending_anchor = anchor_m.group(1).strip()
                continue

            # 1. 인용 메타데이터: [quote, 저자, 출처]
            qm = re.match(r"^\[quote(?:,\s*([^,\]]+))?(?:,\s*([^\]]+))?\]", trimmed, re.IGNORECASE)
            if qm:
                flush_normal_p()
                flush_continuation()
                flush_pending_single_block()
                flush_list()
                pending_meta = {
                    "kind": "quote",
                    "author": (qm.group(1) or "").strip(),
                    "source": (qm.group(2) or "").strip(),
                }
                pending_block_lines = []
                continue

            # 2. Admonition 메타데이터: [NOTE], [TIP], [IMPORTANT], [WARNING], [CAUTION]
            am = re.match(r"^\[(NOTE|IMPORTANT|TIP|WARNING|CAUTION)\]", trimmed, re.IGNORECASE)
            if am:
                flush_normal_p()
                flush_continuation()
                flush_pending_single_block()
                flush_list()
                pending_meta = {"kind": "admonition", "type": am.group(1).upper()}
                pending_block_lines = []
                continue

            # 3. 코드 블록 메타데이터: [source, python]
            sm = re.match(r"^\[source(?:,\s*([a-zA-Z0-9_-]+))?\]", trimmed, re.IGNORECASE)
            if sm:
                flush_normal_p()
                flush_continuation()
                flush_pending_single_block()
                flush_list()
                pending_meta = {"kind": "code", "lang": (sm.group(1) or "").strip()}
                continue

            # 3-0. 수식 블록 메타데이터: [latexmath], [stem], [asciimath]
            math_m = re.match(r"^\[(latexmath|stem|asciimath)\]$", trimmed, re.IGNORECASE)
            if math_m:
                flush_normal_p()
                flush_continuation()
                flush_pending_single_block()
                flush_list()
                pending_meta = {"kind": "math", "type": math_m.group(1).lower()}
                continue

            # 3-1. 테이블 메타데이터: [cols="..."] 또는 [%header...]
            tm = re.match(r"^\[(.*cols.*|.*header.*|\d+\*|[0-9,]+)\]$", trimmed, re.IGNORECASE)
            if tm:
                flush_normal_p()
                flush_continuation()
                flush_pending_single_block()
                flush_list()
                if pending_meta and pending_meta.get("kind") == "table":
                    pending_meta["cols"] = tm.group(1)
                else:
                    pending_meta = {"kind": "table", "cols": tm.group(1)}
                continue

            # 3-2. 테이블 제목 / 캡션: .Table Title
            title_m = re.match(r"^\.([^\.\s].*)$", trimmed)
            if title_m:
                flush_normal_p()
                flush_continuation()
                flush_pending_single_block()
                flush_list()
                if pending_meta and pending_meta.get("kind") == "table":
                    pending_meta["title"] = title_m.group(1).strip()
                else:
                    pending_meta = {"kind": "table", "title": title_m.group(1).strip()}
                continue

            # 4. 블록 구분자 진입
            if trimmed == "____":
                flush_normal_p()
                flush_continuation()
                flush_list()
                in_block = "quote"
                block_meta = pending_meta if (pending_meta and pending_meta.get("kind") == "quote") else {}
                pending_meta = None
                pending_block_lines = []
                block_lines = []
                continue

            if trimmed == "====":
                flush_normal_p()
                flush_continuation()
                flush_list()
                in_block = "admonition"
                block_meta = (
                    pending_meta
                    if (pending_meta and pending_meta.get("kind") == "admonition")
                    else {"type": "NOTE"}
                )
                pending_meta = None
                pending_block_lines = []
                block_lines = []
                continue

            if trimmed == "----":
                flush_normal_p()
                flush_continuation()
                flush_list()
                if pending_meta and pending_meta.get("kind") == "math":
                    in_block = "math"
                    block_meta = pending_meta
                else:
                    in_block = "code"
                    block_meta = pending_meta if (pending_meta and pending_meta.get("kind") == "code") else {}
                pending_meta = None
                pending_block_lines = []
                block_lines = []
                continue

            if trimmed == "++++":
                flush_normal_p()
                flush_continuation()
                flush_list()
                in_block = "math"
                block_meta = (
                    pending_meta
                    if (pending_meta and pending_meta.get("kind") == "math")
                    else {"kind": "math", "type": "latex"}
                )
                pending_meta = None
                pending_block_lines = []
                block_lines = []
                continue

            if trimmed == "|===":
                flush_normal_p()
                flush_continuation()
                flush_list()
                in_block = "table"
                block_meta = pending_meta if (pending_meta and pending_meta.get("kind") == "table") else {}
                pending_meta = None
                pending_block_lines = []
                block_lines = []
                continue

            # 5. 이미지 블록: image::url[alt, title="캡션"]
            img_m = re.match(
                r'^image::([^\[]+)\[([^,\]]*)(?:,\s*title=(?:"([^"]*)"|\'([^\']*)\'|([^\]]*)))?\]',
                trimmed,
            )
            if img_m:
                flush_normal_p()
                flush_continuation()
                flush_pending_single_block()
                flush_list()
                src = img_m.group(1).strip()
                alt = (img_m.group(2) or "").strip()
                cap = img_m.group(3) or img_m.group(4) or img_m.group(5) or ""
                cap_html = f'<div class="title">{html.escape(cap)}</div>' if cap else ""
                anchor_attr = f' id="{html.escape(pending_anchor)}"' if pending_anchor else ""
                pending_anchor = None
                out.append(
                    f'<div class="imageblock"{anchor_attr}><img src="{html.escape(src)}" alt="{html.escape(alt)}">'
                    f"{cap_html}</div>"
                )
                continue

            # 6. 코드 라인 콜아웃: <1> 설명
            col_m = re.match(r"^<(\d+)>\s*(.+)", trimmed)
            if col_m:
                flush_normal_p()
                flush_continuation()
                flush_pending_single_block()
                flush_list()
                conum = col_m.group(1)
                text = _inline_adoc_format(col_m.group(2))
                out.append(f'<div class="colist"><span class="conum">&lt;{conum}&gt;</span> {text}</div>')
                continue

            # 7. 섹션 헤더 (=, ==, ===, ====) 및 문서 속성
            h1_m = re.match(r"^=\s+(.+)$", trimmed)
            if h1_m:
                flush_normal_p()
                flush_continuation()
                flush_pending_single_block()
                flush_list()
                clean_h, h_anc = _extract_heading_anchor(h1_m.group(1))
                id_attr = f' id="{html.escape(h_anc)}"' if h_anc else ""
                out.append(f"<h1{id_attr}>{_inline_adoc_format(clean_h)}</h1>")
                continue
            h2_m = re.match(r"^==\s+(.+)$", trimmed)
            if h2_m:
                flush_normal_p()
                flush_continuation()
                flush_pending_single_block()
                flush_list()
                clean_h, h_anc = _extract_heading_anchor(h2_m.group(1))
                id_attr = f' id="{html.escape(h_anc)}"' if h_anc else ""
                out.append(f"<h2{id_attr}>{_inline_adoc_format(clean_h)}</h2>")
                continue
            h3_m = re.match(r"^===\s+(.+)$", trimmed)
            if h3_m:
                flush_normal_p()
                flush_continuation()
                flush_pending_single_block()
                flush_list()
                clean_h, h_anc = _extract_heading_anchor(h3_m.group(1))
                id_attr = f' id="{html.escape(h_anc)}"' if h_anc else ""
                out.append(f"<h3{id_attr}>{_inline_adoc_format(clean_h)}</h3>")
                continue
            h4_m = re.match(r"^====\s+(.+)$", trimmed)
            if h4_m:
                flush_normal_p()
                flush_continuation()
                flush_pending_single_block()
                flush_list()
                clean_h, h_anc = _extract_heading_anchor(h4_m.group(1))
                id_attr = f' id="{html.escape(h_anc)}"' if h_anc else ""
                out.append(f"<h4{id_attr}>{_inline_adoc_format(clean_h)}</h4>")
                continue

            # 문서 속성 (:key: value)
            attr_m = re.match(r"^:[a-zA-Z0-9_-]+:\s*(.*)$", trimmed)
            if attr_m:
                continue

            # 8. 단일 행 인라인 Admonition (예: NOTE: 설명)
            single_adm = re.match(r"^(NOTE|TIP|IMPORTANT|WARNING|CAUTION):\s*(.+)$", trimmed, re.IGNORECASE)
            if single_adm:
                flush_normal_p()
                flush_continuation()
                flush_pending_single_block()
                flush_list()
                adm_type = single_adm.group(1).upper()
                adm_text = _inline_adoc_format(single_adm.group(2))
                anchor_attr = f' id="{html.escape(pending_anchor)}"' if pending_anchor else ""
                pending_anchor = None
                out.append(
                    f'<div class="admonitionblock {adm_type.lower()}"{anchor_attr}>'
                    f'<div class="title">{html.escape(adm_type)}</div>'
                    f'<div class="content"><p>{adm_text}</p></div>'
                    f'</div>'
                )
                continue

            # 9. 리스트 연속 연산자 ('+' 단독 행)
            if trimmed == "+":
                if list_ctx.in_item:
                    flush_continuation()
                    pending_continuation = True
                continue

            # 10. 리스트 항목 (*, **, -, ., .. 등)
            list_match = _match_list_item(trimmed)
            if list_match:
                flush_normal_p()
                flush_continuation()
                pending_continuation = False
                flush_pending_single_block()
                tag, level, item_raw = list_match
                item_text = _inline_adoc_format(item_raw)
                adj = list_ctx.adjust_level(tag, level)
                out.extend(adj)
                out.append(f"<li>{item_text}")
                list_ctx.in_item = True
                in_continuation = False
                continue

            # 11. 빈 줄 처리
            if not trimmed:
                if in_continuation:
                    flush_continuation()
                flush_normal_p()
                flush_pending_single_block()
                continue

            # 12. pending_meta (구분자 없는 단일 단락 quote / admonition 의 텍스트 라인)
            if pending_meta and pending_meta.get("kind") in ("quote", "admonition"):
                pending_block_lines.append(trimmed)
                continue

            # 13. 리스트 continuation 단락 라인
            if (pending_continuation or in_continuation) and list_ctx.in_item:
                pending_continuation = False
                in_continuation = True
                continuation_lines.append(trimmed)
                continue

            # 14. 일반 단락 라인
            normal_p_lines.append(trimmed)

        else:
            # 블록 내부 처리 및 블록 닫기
            if (
                (in_block == "quote" and trimmed == "____")
                or (in_block == "admonition" and trimmed == "====")
                or (in_block == "code" and trimmed == "----")
                or (in_block == "math" and (trimmed == "++++" or trimmed == "----"))
                or (in_block == "table" and trimmed == "|===")
            ):
                flush_block()
            else:
                block_lines.append(line)

    flush_block()
    flush_normal_p()
    flush_continuation()
    flush_pending_single_block()
    flush_list()
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
