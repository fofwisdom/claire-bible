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
    s = html.escape(text)
    # 형광 하이라이트: #텍스트# -> <mark>텍스트</mark>
    s = re.sub(r"#([^#\n]+?)#", r"<mark>\1</mark>", s)
    # 굵은 글씨: *텍스트* -> <strong>텍스트</strong>
    s = re.sub(r"(?<!\w)\*([^*\n]+?)\*(?!\w)", r"<strong>\1</strong>", s)
    # 기울임꼴: _텍스트_ -> <em>텍스트</em>
    s = re.sub(r"(?<!\w)_([^_\n]+?)_(?!\w)", r"<em>\1</em>", s)
    # 인라인 코드: `텍스트` -> <code>텍스트</code>
    s = re.sub(r"`([^`\n]+?)`", r"<code>\1</code>", s)
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


def render_adoc_to_html(raw: str) -> str:
    """AsciiDoc 텍스트를 시맨틱 HTML 문자열로 AOT 변환."""
    if not raw or not raw.strip():
        return ""

    lines = raw.splitlines()
    out: list[str] = []
    in_block: str | None = None
    block_meta: dict[str, str] = {}
    block_lines: list[str] = []
    table_rows: list[list[str]] = []

    def flush_block() -> None:
        nonlocal in_block, block_meta, block_lines, table_rows
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
            t_html = ["<table>"]
            if table_rows:
                t_html.append(
                    "<thead><tr>"
                    + "".join(f"<th>{_inline_adoc_format(c)}</th>" for c in table_rows[0])
                    + "</tr></thead>"
                )
                if len(table_rows) > 1:
                    t_html.append("<tbody>")
                    for r in table_rows[1:]:
                        t_html.append(
                            "<tr>"
                            + "".join(f"<td>{_inline_adoc_format(c)}</td>" for c in r)
                            + "</tr>"
                        )
                    t_html.append("</tbody>")
            t_html.append("</table>")
            out.append("".join(t_html))

        in_block = None
        block_meta = {}
        block_lines = []
        table_rows = []

    pending_meta: dict[str, str] | None = None

    for line in lines:
        trimmed = line.strip()

        if not in_block:
            # 1. 인용 메타데이터: [quote, 저자, 출처]
            qm = re.match(r"^\[quote(?:,\s*([^,\]]+))?(?:,\s*([^\]]+))?\]", trimmed, re.I)
            if qm:
                pending_meta = {
                    "kind": "quote",
                    "author": (qm.group(1) or "").strip(),
                    "source": (qm.group(2) or "").strip(),
                }
                continue

            # 2. Admonition 메타데이터: [NOTE], [TIP], [IMPORTANT], [WARNING], [CAUTION]
            am = re.match(r"^\[(NOTE|IMPORTANT|TIP|WARNING|CAUTION)\]", trimmed, re.I)
            if am:
                pending_meta = {"kind": "admonition", "type": am.group(1).upper()}
                continue

            # 3. 코드 블록 메타데이터: [source, python]
            sm = re.match(r"^\[source(?:,\s*([a-zA-Z0-9_-]+))?\]", trimmed, re.I)
            if sm:
                pending_meta = {"kind": "code", "lang": (sm.group(1) or "").strip()}
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
                block_meta = {}
                pending_meta = None
                table_rows = []
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

            # 7. 섹션 헤더 (==, ===, ====)
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

            # 8. 단일 행 인라인 Admonition (예: NOTE: 설명)
            single_adm = re.match(r"^(NOTE|TIP|IMPORTANT|WARNING|CAUTION):\s*(.+)$", trimmed, re.I)
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
            if in_block == "quote" and trimmed == "____":
                flush_block()
            elif in_block == "admonition" and trimmed == "====":
                flush_block()
            elif in_block == "code" and trimmed == "----":
                flush_block()
            elif in_block == "table" and trimmed == "|===":
                flush_block()
            else:
                if in_block == "table":
                    if trimmed.startswith("|"):
                        cells = [c.strip() for c in trimmed[1:].split("|")]
                        if cells:
                            table_rows.append(cells)
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
