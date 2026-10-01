"""AsciiDoc to reveal.js presentation preprocessor.

Transforms raw Claire Bible AsciiDoc documents into slide-optimized AsciiDoc
with reveal.js attributes, 2D heading hierarchy, and overflow chunking.
"""

from __future__ import annotations

import re


def preprocess_adoc_to_slides(
    detail: str,
    *,
    title: str,
    author: str | None = None,
    published_at: str | None = None,
    summary: str | None = None,
    theme: str = "night",
    transition: str = "slide",
    customcss: str | None = None,
    revealjsdir: str | None = None,
) -> str:
    """Transform raw AsciiDoc into slide-ready AsciiDoc with reveal.js headers and 2D layout."""
    clean_title = (title or "Claire Bible Presentation").strip().replace("\n", " ")
    # Escape quotes or special chars in title for AsciiDoc header
    clean_title = re.sub(r'["\\]', "", clean_title)

    author_val = (author or "Claire Bible Knowledge Base").strip().replace("\n", " ")
    date_val = (published_at or "").strip()

    # Default reveal.js assets location (CDN fallback if not specified)
    r_dir = revealjsdir or "https://cdn.jsdelivr.net/npm/reveal.js@5.1.0"
    css_attr = f":customcss: {customcss}\n" if customcss else ""

    header_lines = [
        f"= {clean_title}",
        f":author: {author_val}",
    ]
    if date_val:
        header_lines.append(f":revdate: {date_val}")

    header_lines.extend([
        f":revealjs_theme: {theme}",
        f":revealjs_transition: {transition}",
        ":revealjs_slideNumber: c/t",
        ":revealjs_history: true",
        ":revealjs_hash: true",
        ":revealjs_fragmentInURL: true",
        ":revealjs_controls: true",
        ":revealjs_progress: true",
        ":revealjs_center: true",
        ":source-highlighter: highlight.js",
        ":stem: latexmath",
        ":icons: font",
    ])
    if css_attr:
        header_lines.append(css_attr.strip())
    if r_dir:
        header_lines.append(f":revealjsdir: {r_dir}")

    header_lines.append("")  # blank line after header

    # Clean raw detail
    lines = detail.splitlines()
    body_lines: list[str] = []
    skip_header = True

    for line in lines:
        stripped = line.strip()
        # Skip leading document title `= ...` or doc attributes `:...:`
        if skip_header:
            if stripped.startswith("= ") or stripped.startswith(":") or stripped == "'''":
                continue
            if not stripped:
                continue
            skip_header = False

        body_lines.append(line)

    body_text = "\n".join(body_lines)

    # Optional: Insert summary slide right after title if summary exists
    summary_slide = ""
    if summary and summary.strip():
        clean_summary = summary.strip()
        summary_slide = (
            "\n== 핵심 요약 (Executive Summary)\n\n"
            f"[quote, Claire Bible Knowledge Engine]\n"
            f"{clean_summary}\n\n"
        )

    # Optimize slide sections and prevent overflow
    optimized_body = _optimize_sections(body_text)

    return "\n".join(header_lines) + summary_slide + optimized_body


def _optimize_sections(text: str) -> str:
    """Split very long sections (> 15 lines or > 700 chars) into vertical sub-slides with <<<."""
    # Split text by headings `== ` (Level 1)
    parts = re.split(r"(?m)^(?=== )", text)
    optimized_parts: list[str] = []

    for part in parts:
        if not part.strip():
            continue
        # Split by sub-headings `=== ` (Level 2)
        subparts = re.split(r"(?m)^(?==== )", part)
        optimized_subparts: list[str] = []

        for subpart in subparts:
            if not subpart.strip():
                continue
            lines = subpart.strip().splitlines()
            # If section has more than 10 lines or > 400 chars and multiple paragraphs, break it with `<<<`
            if (len(lines) > 10 or len(subpart) > 400) and "\n\n" in subpart:
                chunks = subpart.split("\n\n")
                accumulated: list[str] = []
                cur_len = 0
                for c in chunks:
                    c_lines = len(c.splitlines())
                    if cur_len > 0 and (cur_len + c_lines > 8):
                        accumulated.append("\n<<<\n")
                        cur_len = 0
                    accumulated.append(c)
                    cur_len += c_lines
                optimized_subparts.append("\n\n".join(accumulated))
            else:
                optimized_subparts.append(subpart)

        optimized_parts.append("".join(optimized_subparts))

    return "\n".join(optimized_parts)
