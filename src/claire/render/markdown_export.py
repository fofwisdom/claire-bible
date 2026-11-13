"""Markdown export of an ingested AsciiDoc document (RD24, MD-3, docs/design/MARKDOWN_RETIREMENT_DESIGN.md Section 5).

This is the only place where Claire writes Markdown. The converter has no dependency. It covers the AsciiDoc subset that the body prompt of ingestion asks for (``extract/prompts.py``): section titles, strong, emphasis and highlight marks, links, images, lists, tables, source, literal, quote, admonition and sidebar blocks, thematic breaks, anchors and cross references. A construct that it does not know goes out as text. The export is read-only: it writes nothing in the database.
"""

from __future__ import annotations

import datetime as _dt
import re

__all__ = ["adoc_to_markdown", "document_markdown"]

_ADMONITIONS = ("NOTE", "TIP", "IMPORTANT", "WARNING", "CAUTION")
_HEADING_RE = re.compile(r"^(={1,6})\s+(.+?)\s*$")
_ULIST_RE = re.compile(r"^(\*{1,5}|-)\s+(.*)$")
_OLIST_RE = re.compile(r"^(\.{1,5})\s+(.*)$")
_ATTR_ENTRY_RE = re.compile(r"^:[A-Za-z0-9_-]+!?:.*$")
_BLOCK_ATTR_RE = re.compile(r"^\[([^\]]*)\]\s*$")
_BLOCK_TITLE_RE = re.compile(r"^\.([^.\s].*)$")
_ANCHOR_LINE_RE = re.compile(r"^\[\[[^\]]+\]\]\s*$")
_ADMONITION_PARA_RE = re.compile(r"^(NOTE|TIP|IMPORTANT|WARNING|CAUTION):\s+(.*)$")


def _inline(text: str) -> str:
    """Inline marks of one line."""
    s = text
    # Pass-through and code spans first, so that their content is not changed.
    codes: list[str] = []

    def _keep_code(m: re.Match) -> str:
        codes.append(m.group(1))
        return f"\x00{len(codes) - 1}\x00"

    s = re.sub(r"`([^`]+)`", _keep_code, s)
    s = re.sub(r"\[\[[^\]]+\]\]", "", s)  # inline anchors
    s = re.sub(r"<<[^,>]+,\s*([^>]+)>>", r"\1", s)  # cross reference with text
    s = re.sub(r"<<([^>]+)>>", r"\1", s)
    s = re.sub(r"image:([^\s\[]+)\[([^\]]*)\]", lambda m: f"![{m.group(2).split(',')[0].strip()}]({m.group(1)})", s)
    s = re.sub(r"link:([^\s\[]+)\[([^\]]*)\]", lambda m: f"[{m.group(2) or m.group(1)}]({m.group(1)})", s)
    s = re.sub(r"(?<![\(\[])(https?://[^\s\[\]<>]+)\[([^\]]*)\]", lambda m: f"[{m.group(2) or m.group(1)}]({m.group(1)})", s)
    s = re.sub(r"footnote:\[([^\]]*)\]", r" (\1)", s)
    # Strong (*x*), emphasis (_x_), highlight (#x#). Markdown has no highlight; it becomes strong.
    s = re.sub(r"(?<![\w*])\*(?!\s)([^*\n]+?)(?<!\s)\*(?![\w*])", r"**\1**", s)
    s = re.sub(r"(?<![\w_])_(?!\s)([^_\n]+?)(?<!\s)_(?![\w_])", r"*\1*", s)
    s = re.sub(r"(?<![\w#])#(?!\s)([^#\n]+?)(?<!\s)#(?![\w#])", r"**\1**", s)
    s = re.sub(r"\s\+$", "  ", s)  # hard line break
    return re.sub(r"\x00(\d+)\x00", lambda m: f"`{codes[int(m.group(1))]}`", s)


def _cells(line: str) -> list[str]:
    """Cells of one table line: ``| a | b`` gives ``["a", "b"]``. A span spec before a separator (for example ``2+|``) is removed. An escaped ``\\|`` stays in the cell."""
    cells = re.split(r"(?<!\\)\|", line)[1:]
    spec = re.compile(r"\s+\d*(?:\.\d+)?[+*][<^>]?(?:\.[<^>])?[a-z]?$")
    return [spec.sub("", c).strip() if k < len(cells) - 1 else c.strip() for k, c in enumerate(cells)]


def _table(lines: list[str], header: bool, cols: int | None) -> list[str]:
    cells: list[str] = []
    rows: list[list[str]] = []
    implicit_header = False
    for i, raw in enumerate(lines):
        line = raw.strip()
        if not line:
            if i == 1 and len(cells) > 0:
                implicit_header = True
            continue
        if line.startswith("|"):
            row_cells = _cells(line)
            if cols is None and i == 0:
                cols = len(row_cells)
            cells.extend(row_cells)
        elif cells:
            cells[-1] = f"{cells[-1]} {line}".strip()
    if not cells:
        return []
    width = cols or len(cells)
    for k in range(0, len(cells), width):
        rows.append(cells[k : k + width] + [""] * (width - len(cells[k : k + width])))
    has_header = header or implicit_header
    head = rows[0] if has_header else [""] * width
    body = rows[1:] if has_header else rows
    out = ["| " + " | ".join(_inline(c) for c in head) + " |", "|" + "---|" * width]
    out += ["| " + " | ".join(_inline(c) for c in r) + " |" for r in body]
    return out


def _table_options(attr: str) -> tuple[bool, int | None]:
    header = "%header" in attr or "options=\"header\"" in attr or "options=header" in attr
    cols: int | None = None
    m = re.search(r'cols="?([^",\]]+(?:,[^",\]]+)*)"?', attr)
    if m:
        spec = m.group(1)
        star = re.match(r"^(\d+)\*", spec)
        cols = int(star.group(1)) if star else len([c for c in spec.split(",") if c.strip()])
    return header, cols


def adoc_to_markdown(text: str) -> str:
    """Convert an AsciiDoc body to Markdown (CommonMark with GFM tables)."""
    lines = (text or "").replace("\r\n", "\n").split("\n")
    out: list[str] = []
    attr = ""
    title = ""
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if stripped.startswith("//") and not stripped.startswith("////"):
            i += 1
            continue
        if stripped.startswith("////"):
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("////"):
                i += 1
            i += 1
            continue
        if _ATTR_ENTRY_RE.match(stripped) or _ANCHOR_LINE_RE.match(stripped) or stripped in ("+", "<<<", "toc::[]"):
            i += 1
            continue
        m = _BLOCK_ATTR_RE.match(stripped)
        if m and not stripped.startswith("[["):
            attr = m.group(1)
            i += 1
            continue
        m = _BLOCK_TITLE_RE.match(stripped)
        if m and not _OLIST_RE.match(stripped):
            title = m.group(1).strip()
            i += 1
            continue
        if title and not stripped.startswith(("|===", "----", "....", "____", "====", "****")):
            out += [f"**{_inline(title)}**", ""]
            title = ""
        # Delimited blocks.
        delim = next((d for d in ("|===", "----", "....", "____", "====", "****", "```") if stripped.startswith(d) and set(stripped) <= set(d)), None)
        if delim:
            body: list[str] = []
            i += 1
            while i < len(lines) and lines[i].strip() != stripped:
                body.append(lines[i])
                i += 1
            i += 1
            kind = attr.split(",")[0].strip()
            if title:
                out += [f"**{_inline(title)}**", ""]
                title = ""
            if delim == "|===":
                header, cols = _table_options(attr)
                out += _table(body, header, cols) + [""]
            elif delim in ("----", "```"):
                lang = attr.split(",")[1].strip() if kind == "source" and "," in attr else ""
                out += [f"```{lang}"] + body + ["```", ""]
            elif delim == "....":
                out += ["```"] + body + ["```", ""]
            elif delim == "____" or kind == "quote":
                quoted = adoc_to_markdown("\n".join(body)).split("\n")
                out += [f"> {q}".rstrip() for q in quoted]
                source = [p.strip() for p in attr.split(",")[1:] if p.strip()]
                if source:
                    out.append(f"> — {', '.join(source)}")
                out.append("")
            else:
                label = kind if kind in _ADMONITIONS else ""
                inner = adoc_to_markdown("\n".join(body)).split("\n")
                if label:
                    out.append(f"> **{label}**")
                    out.append(">")
                out += [f"> {q}".rstrip() for q in inner] + [""]
            attr = ""
            continue
        if attr.split(",")[0].strip() in _ADMONITIONS and stripped:
            out.append(f"> **{attr.split(',')[0].strip()}** {_inline(stripped)}")
            attr = ""
            i += 1
            continue
        attr = ""
        if stripped in ("'''", "---", "***"):
            out += ["---", ""]
            i += 1
            continue
        m = _HEADING_RE.match(line)
        if m:
            out += [f"{'#' * len(m.group(1))} {_inline(m.group(2))}", ""]
            i += 1
            continue
        m = re.match(r"^image::([^\[]+)\[([^\]]*)\]\s*$", stripped)
        if m:
            parts = [p.strip() for p in m.group(2).split(",")]
            alt = parts[0] if parts and parts[0] else ""
            cap = next((p.split("=", 1)[1].strip().strip('"') for p in parts if p.startswith("title=")), "")
            out += [f"![{alt}]({m.group(1).strip()})"] + ([f"*{cap}*"] if cap else []) + [""]
            i += 1
            continue
        m = _ADMONITION_PARA_RE.match(stripped)
        if m:
            out.append(f"> **{m.group(1)}** {_inline(m.group(2))}")
            i += 1
            continue
        m = _ULIST_RE.match(stripped)
        if m:
            depth = len(m.group(1)) - 1 if m.group(1) != "-" else 0
            out.append(f"{'  ' * depth}- {_inline(m.group(2))}")
            i += 1
            continue
        m = _OLIST_RE.match(stripped)
        if m:
            depth = len(m.group(1)) - 1
            out.append(f"{'   ' * depth}1. {_inline(m.group(2))}")
            i += 1
            continue
        out.append(_inline(line.rstrip()))
        i += 1
    text_out = "\n".join(out)
    return re.sub(r"\n{3,}", "\n\n", text_out).strip() + "\n"


def _yaml_str(value: str) -> str:
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ") + '"'


def document_markdown(document: dict) -> str:
    """The export file of one document: a YAML front matter header (title, source, date, summary, document ID), a title, and the body in Markdown (MX3)."""
    title = str(document.get("title") or "").strip()
    fetched = document.get("fetched_at")
    header = ["---", f"title: {_yaml_str(title)}"]
    if document.get("url"):
        header.append(f"source: {_yaml_str(document['url'])}")
    if isinstance(fetched, (int, float)) and fetched > 0:
        header.append(f"date: {_dt.datetime.fromtimestamp(float(fetched), _dt.timezone.utc).date().isoformat()}")
    if document.get("summary"):
        header.append(f"summary: {_yaml_str(document['summary'])}")
    header += [f"claire_document_id: {_yaml_str(document.get('id') or '')}", "---", ""]
    body = adoc_to_markdown(str(document.get("detail") or ""))
    first = body.lstrip().split("\n", 1)[0]
    lead = "" if first.startswith("# ") else f"# {title}\n\n"
    return "\n".join(header) + "\n" + lead + body
