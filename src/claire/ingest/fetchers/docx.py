"""DOCX (Office Open XML) document extractor with XXE and zip-bomb protection."""

from __future__ import annotations

import io
import logging
import re
from typing import Any, BinaryIO
import zipfile

from lxml import etree as ET

from .adoc_text import adoc_cell, adoc_link_text, adoc_list_item, adoc_table

logger = logging.getLogger("claire.ingest.docx")

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
DC_NS = "http://purl.org/dc/elements/1.1/"

MAX_DOCX_XML_BYTES = 25 * 1024 * 1024

_URL_RE = re.compile(r"https?://[^\s)\[\]\}<>\"']+")
_HEADING_STYLE_RE = re.compile(r"^(?:heading\s*|h)([1-6])$", re.IGNORECASE)

_SAFE_XML_PARSER = ET.XMLParser(resolve_entities=False, no_network=True, huge_tree=False, dtd_validation=False, load_dtd=False)


class DocxExtractResult(tuple):
    """6-tuple result ``(title, text, links, anchors, error, images)`` with biblio and parser metadata."""

    def __new__(cls, title: str | None, text: str, links: list[str], anchors: dict[str, str], error: str | None, images: list[dict[str, Any]], biblio: dict[str, Any] | None = None, parser_used: str = "docx"):
        instance = super().__new__(cls, (title, text, links, anchors, error, images))
        instance.biblio = biblio or {}
        instance.parser_used = parser_used
        return instance


def _read_zip_member_limited(zf: zipfile.ZipFile, member_name: str, *, max_bytes: int = MAX_DOCX_XML_BYTES) -> bytes:
    """Read one ZIP member with a strict decompressed byte limit to stop zip bombs."""
    info = zf.getinfo(member_name)
    if info.file_size > max_bytes:
        raise ValueError(f"DOCX entry {member_name!r} exceeds size limit ({info.file_size} > {max_bytes})")
    chunks: list[bytes] = []
    total = 0
    with zf.open(info, "r") as stream:
        while True:
            chunk = stream.read(65536)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise ValueError(f"DOCX entry {member_name!r} exceeds decompressed size limit ({max_bytes} bytes)")
            chunks.append(chunk)
    return b"".join(chunks)


def is_docx_bytes(data: bytes) -> bool:
    """Return True when ``data`` is a valid DOCX ZIP container."""
    if not data or len(data) < 30 or not data.startswith(b"PK\x03\x04"):
        return False
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = set(zf.namelist())
            return "word/document.xml" in names and "[Content_Types].xml" in names
    except Exception:
        return False


def extract_docx_metadata(core_xml_bytes: bytes) -> tuple[str | None, dict[str, Any]]:
    """Extract document title from ``docProps/core.xml``."""
    title: str | None = None
    if not core_xml_bytes:
        return None, {}
    try:
        root = ET.fromstring(core_xml_bytes, parser=_SAFE_XML_PARSER)
        t_elem = root.find(f".//{{{DC_NS}}}title")
        if t_elem is not None and t_elem.text and t_elem.text.strip():
            title = t_elem.text.strip()[:200]
    except Exception as exc:
        logger.debug("Failed to parse DOCX core.xml: %s", exc)
    return title, {}


def _extract_external_links(rels_bytes: bytes) -> dict[str, str]:
    """Build a relationship-ID to external HTTP(S) URL map from ``word/_rels/document.xml.rels``."""
    mapping: dict[str, str] = {}
    if not rels_bytes:
        return mapping
    try:
        root = ET.fromstring(rels_bytes, parser=_SAFE_XML_PARSER)
    except Exception:
        return mapping
    for rel in root.findall(f".//{{{REL_NS}}}Relationship"):
        rid = str(rel.get("Id") or "").strip()
        target = str(rel.get("Target") or "").strip()
        mode = str(rel.get("TargetMode") or "").strip().lower()
        if rid and target.startswith(("http://", "https://")) and (not mode or mode == "external"):
            mapping[rid] = target
    return mapping


def _extract_run_or_inline_text(node: ET._Element, rel_links: dict[str, str], links: list[str], anchors: dict[str, str]) -> str:
    """Collect inline text, breaks, tabs, and external hyperlinks inside one DOCX paragraph."""
    parts: list[str] = []
    for child in node:
        tag = child.tag
        if tag == f"{{{W_NS}}}pPr":
            continue
        if tag == f"{{{W_NS}}}r":
            parts.append(_extract_run_or_inline_text(child, rel_links, links, anchors))
        elif tag == f"{{{W_NS}}}t" or tag == f"{{{W_NS}}}delText":
            if child.text:
                parts.append(child.text)
        elif tag == f"{{{W_NS}}}tab":
            parts.append("\t")
        elif tag == f"{{{W_NS}}}br" or tag == f"{{{W_NS}}}cr":
            parts.append("\n")
        elif tag == f"{{{W_NS}}}hyperlink":
            rid = str(child.get(f"{{{R_NS}}}id") or "").strip()
            sub = _extract_run_or_inline_text(child, rel_links, links, anchors)
            href = rel_links.get(rid)
            if href:
                if href not in links:
                    links.append(href)
                if href not in anchors and sub.strip():
                    anchors[href] = sub.strip()[:160]
                parts.append(f"link:{href}[{adoc_link_text(sub)}]")
            else:
                parts.append(sub)
        else:
            parts.append(_extract_run_or_inline_text(child, rel_links, links, anchors))
    return "".join(parts)


def _heading_level(p_elem: ET._Element) -> int | None:
    """Return heading level 1..6 when paragraph style represents a heading, else None."""
    p_pr = p_elem.find(f"{{{W_NS}}}pPr")
    if p_pr is None:
        return None
    p_style = p_pr.find(f"{{{W_NS}}}pStyle")
    if p_style is None:
        return None
    val = str(p_style.get(f"{{{W_NS}}}val") or "").strip()
    if not val:
        return None
    low = val.lower()
    if low == "title":
        return 1
    if low == "subtitle":
        return 2
    match = _HEADING_STYLE_RE.fullmatch(low)
    if match:
        return int(match.group(1))
    if val.isdigit() and 1 <= int(val) <= 6:
        return int(val)
    return None


def _list_depth(p_elem: ET._Element) -> int | None:
    """Return unordered list nesting depth when ``w:numPr`` is set, else None."""
    p_pr = p_elem.find(f"{{{W_NS}}}pPr")
    if p_pr is None:
        return None
    num_pr = p_pr.find(f"{{{W_NS}}}numPr")
    if num_pr is None:
        return None
    ilvl = num_pr.find(f"{{{W_NS}}}ilvl")
    if ilvl is None:
        return 0
    try:
        return max(0, min(int(str(ilvl.get(f"{{{W_NS}}}val") or "0")), 5))
    except ValueError:
        return 0


def _format_docx_table(tbl_elem: ET._Element, rel_links: dict[str, str], links: list[str], anchors: dict[str, str]) -> str:
    """Convert one ``<w:tbl>`` element to an AsciiDoc table block."""
    rows: list[list[str]] = []
    max_cols = 0
    for tr_elem in tbl_elem.findall(f"{{{W_NS}}}tr"):
        row_cells: list[str] = []
        for tc_elem in tr_elem.findall(f"{{{W_NS}}}tc"):
            para_texts = [_extract_run_or_inline_text(p, rel_links, links, anchors).strip() for p in tc_elem.findall(f".//{{{W_NS}}}p")]
            row_cells.append(adoc_cell(" ".join(p for p in para_texts if p)))
        while row_cells and not row_cells[-1]:
            row_cells.pop()
        if row_cells:
            rows.append(row_cells)
            max_cols = max(max_cols, len(row_cells))
    if not rows or max_cols == 0:
        return ""
    for row in rows:
        if len(row) < max_cols:
            row.extend([""] * (max_cols - len(row)))
    table = adoc_table(rows)
    return f"\n{table}\n" if table else ""


def _walk_body_elements(container: ET._Element, rel_links: dict[str, str], links: list[str], anchors: dict[str, str], doc_elements: list[str], state: dict[str, str | None]) -> None:
    """Traverse paragraphs, tables, and structured document tags inside DOCX body."""
    for child in container:
        tag = child.tag
        if tag == f"{{{W_NS}}}p":
            text = _extract_run_or_inline_text(child, rel_links, links, anchors).strip()
            if not text:
                continue
            level = _heading_level(child)
            if level is not None:
                if state["first_heading"] is None:
                    state["first_heading"] = text
                doc_elements.append(f"{'=' * (level + 1)} {text}")
                continue
            depth = _list_depth(child)
            if depth is not None:
                doc_elements.append(adoc_list_item(text, depth))
                continue
            doc_elements.append(text)
        elif tag == f"{{{W_NS}}}tbl":
            tbl_text = _format_docx_table(child, rel_links, links, anchors)
            if tbl_text:
                doc_elements.append(tbl_text)
        elif tag == f"{{{W_NS}}}sdt":
            sdt_content = child.find(f"{{{W_NS}}}sdtContent")
            if sdt_content is not None:
                _walk_body_elements(sdt_content, rel_links, links, anchors, doc_elements, state)


def extract_docx_stream(stream: BinaryIO, url: str | None = None, fallback_title: str | None = None, *, max_xml_bytes: int = MAX_DOCX_XML_BYTES) -> DocxExtractResult:
    """Extract title, AsciiDoc-structured body text, and external links from a DOCX stream."""
    del url
    try:
        zf = zipfile.ZipFile(stream)
    except Exception as exc:
        return DocxExtractResult(None, "", [], {}, f"invalid DOCX archive: {exc}", [], {})

    with zf:
        names = set(zf.namelist())
        if "word/document.xml" not in names:
            return DocxExtractResult(None, "", [], {}, "corrupted DOCX: missing word/document.xml", [], {})

        title: str | None = None
        biblio: dict[str, Any] = {}
        if "docProps/core.xml" in names:
            try:
                core_bytes = _read_zip_member_limited(zf, "docProps/core.xml", max_bytes=max_xml_bytes)
                title, biblio = extract_docx_metadata(core_bytes)
            except ValueError as exc:
                return DocxExtractResult(None, "", [], {}, str(exc), [], {})
            except Exception as exc:
                logger.debug("Error reading docProps/core.xml: %s", exc)

        rel_links: dict[str, str] = {}
        if "word/_rels/document.xml.rels" in names:
            try:
                rels_bytes = _read_zip_member_limited(zf, "word/_rels/document.xml.rels", max_bytes=max_xml_bytes)
                rel_links = _extract_external_links(rels_bytes)
            except ValueError as exc:
                return DocxExtractResult(None, "", [], {}, str(exc), [], biblio)

        try:
            doc_bytes = _read_zip_member_limited(zf, "word/document.xml", max_bytes=max_xml_bytes)
            root = ET.fromstring(doc_bytes, parser=_SAFE_XML_PARSER)
        except Exception as exc:
            return DocxExtractResult(None, "", [], {}, f"failed to parse word/document.xml: {exc}", [], biblio)

        body_elem = root.find(f"{{{W_NS}}}body")
        if body_elem is None:
            return DocxExtractResult(title or fallback_title, "", [], {}, "empty DOCX content", [], biblio)

        links: list[str] = []
        anchors: dict[str, str] = {}
        doc_elements: list[str] = []
        state: dict[str, str | None] = {"first_heading": None}
        _walk_body_elements(body_elem, rel_links, links, anchors, doc_elements, state)

        full_text = "\n\n".join(doc_elements).strip()
        if not full_text:
            return DocxExtractResult(title or fallback_title, "", [], {}, "empty DOCX content", [], biblio)

        if not title:
            if state["first_heading"]:
                title = str(state["first_heading"])[:200]
            elif fallback_title:
                title = fallback_title
            else:
                title = full_text.splitlines()[0][:80]

        for matched_url in _URL_RE.findall(full_text):
            cleaned = matched_url.rstrip(".,;)")
            if cleaned not in links:
                links.append(cleaned)

        return DocxExtractResult(title=title, text=full_text, links=links[:50], anchors=anchors, error=None, images=[], biblio=biblio, parser_used="docx")


def extract_docx_bytes(data: bytes, url: str | None = None, fallback_title: str | None = None, *, max_xml_bytes: int = MAX_DOCX_XML_BYTES) -> DocxExtractResult:
    """Extract structured content and metadata from raw DOCX bytes."""
    return extract_docx_stream(io.BytesIO(data), url=url, fallback_title=fallback_title, max_xml_bytes=max_xml_bytes)
