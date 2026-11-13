"""AsciiDoc forms for the raw text that fetchers write (RD24: Claire writes no Markdown).

The raw text goes to extraction and to the body prompt, which ask for AsciiDoc. ``extract/table_budget.py`` finds the table form of this module (``|===``).
"""

from __future__ import annotations


def adoc_cell(text: str) -> str:
    """One table cell: whitespace joined to single spaces, and the cell separator ``|`` escaped."""
    return " ".join((text or "").split()).replace("|", "\\|")


def adoc_table(rows: list[list[str]], caption: str = "") -> str:
    """An AsciiDoc table with the first row as the header. ``rows`` has cells that ``adoc_cell`` made. Rows with fewer cells get empty cells. An empty table gives ``""``."""
    rows = [r for r in rows if any(r)]
    if not rows:
        return ""
    cols = max(len(r) for r in rows)
    if cols == 0:
        return ""
    lines: list[str] = []
    if caption:
        lines.append(f".{' '.join(caption.split())}")
    lines.append(f'[%header,cols="{cols}*"]')
    lines.append("|===")
    for r in rows:
        cells = r + [""] * (cols - len(r))
        lines.append(" ".join(f"|{c}" if c == "" else f"| {c}" for c in cells))
    lines.append("|===")
    return "\n".join(lines)


def adoc_list_item(text: str, depth: int = 0) -> str:
    """One item of an unordered list. ``depth`` 0 is the first level (``*``)."""
    return f"{'*' * (depth + 1)} {text}"


def adoc_link_text(text: str) -> str:
    """Text inside the brackets of an AsciiDoc link or image macro: ``]`` is escaped, so the macro ends at the right place."""
    return " ".join((text or "").split()).replace("]", "\\]")
