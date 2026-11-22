"""Text/keyword and local or uploaded document file fetcher."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ...core.config import get_settings
from ...extract.table_budget import slice_document_text
from ...ontology.base import Document
from ..normalize import content_hash
from .base import FetchError, fetch_settings

MAX_STAGE_FILE_BYTES = 20 * 1024 * 1024
_TEXT_FILE_SUFFIXES: frozenset[str] = frozenset({".md", ".txt", ".markdown", ".rst", ".adoc", ".asciidoc", ""})


def fetch_text(payload: str, *, full_content: bool = False) -> Document:
    """Build a Note-style Document from plain text."""
    del full_content
    text = payload.strip()
    title = text.splitlines()[0][:80] if text else "untitled note"
    return Document(
        title=title,
        raw_text=text,
        source_type="text",
        content_hash=content_hash(text),
        meta={"raw_truncated": False, "orig_chars": len(text), "raw_chars": len(text)},
    )


def _extract_raw_file_content(raw_bytes: bytes, *, filename: str) -> tuple[str, str, str, dict[str, Any]]:
    """Decode bytes of one document file into ``(title, text, source_type, extra_meta)``."""
    safe_name = Path(filename or "document.txt").name
    stem = Path(safe_name).stem or "document"
    suffix = Path(safe_name).suffix.lower()
    extra_meta: dict[str, Any] = {"file_name": safe_name, "byte_length": len(raw_bytes)}

    if suffix == ".odt":
        from .odt import extract_odt_bytes

        odt_res = extract_odt_bytes(raw_bytes, fallback_title=stem)
        title, text, _, _, oerr, _ = odt_res[:6]
        if oerr or not text:
            raise FetchError(oerr or f"empty ODT file: {safe_name}")
        extra_meta["odt_parser_used"] = getattr(odt_res, "parser_used", "odt")
        return title or stem, text, "odt", extra_meta

    if suffix == ".docx":
        from .docx import extract_docx_bytes

        docx_res = extract_docx_bytes(raw_bytes, fallback_title=stem)
        title, text, _, _, derr, _ = docx_res[:6]
        if derr or not text:
            raise FetchError(derr or f"empty DOCX file: {safe_name}")
        extra_meta["docx_parser_used"] = getattr(docx_res, "parser_used", "docx")
        extra_meta["file_format"] = "docx"
        return title or stem, text, "docx", extra_meta

    if suffix == ".pdf" or raw_bytes.startswith(b"%PDF-"):
        from .pdf import extract_pdf_bytes

        pdf_res = extract_pdf_bytes(raw_bytes, fallback_title=stem)
        title, text, _, _, perr, _ = pdf_res[:6]
        if perr or not text:
            raise FetchError(perr or f"empty PDF file: {safe_name}")
        extra_meta["pdf_parser_requested"] = getattr(pdf_res, "parser_requested", "pypdfium2")
        extra_meta["pdf_parser_used"] = getattr(pdf_res, "parser_used", "pypdfium2")
        extra_meta["pdf_parser_fallback"] = bool(getattr(pdf_res, "parser_fallback", False))
        if getattr(pdf_res, "parser_fallback_reason", None):
            extra_meta["pdf_parser_fallback_reason"] = getattr(pdf_res, "parser_fallback_reason")
        extra_meta["pdf_encoding_flaw_detected"] = bool(getattr(pdf_res, "encoding_flaw_detected", False))
        extra_meta["pdf_encoding_flaws"] = list(getattr(pdf_res, "encoding_flaws", []))
        extra_meta["pdf_is_scanned"] = bool(getattr(pdf_res, "is_scanned", False))
        if getattr(pdf_res, "pdf_metadata", None):
            extra_meta["pdf_metadata"] = getattr(pdf_res, "pdf_metadata")
        return title or stem, text, "pdf", extra_meta

    from .odt import extract_odt_bytes, is_odt_bytes

    if is_odt_bytes(raw_bytes):
        odt_res = extract_odt_bytes(raw_bytes, fallback_title=stem)
        title, text, _, _, oerr, _ = odt_res[:6]
        if oerr or not text:
            raise FetchError(oerr or f"empty ODT file: {safe_name}")
        extra_meta["odt_parser_used"] = getattr(odt_res, "parser_used", "odt")
        return title or stem, text, "odt", extra_meta

    from .docx import extract_docx_bytes, is_docx_bytes

    if is_docx_bytes(raw_bytes):
        docx_res = extract_docx_bytes(raw_bytes, fallback_title=stem)
        title, text, _, _, derr, _ = docx_res[:6]
        if derr or not text:
            raise FetchError(derr or f"empty DOCX file: {safe_name}")
        extra_meta["docx_parser_used"] = getattr(docx_res, "parser_used", "docx")
        extra_meta["file_format"] = "docx"
        return title or stem, text, "docx", extra_meta

    if suffix in {".html", ".htm"}:
        from .web import _extract_html

        decoded = raw_bytes.decode(encoding="utf-8", errors="ignore")
        html_title, text, _, _, herr, _ = _extract_html(decoded)
        if herr or not text or not text.strip():
            raise FetchError(herr or f"empty HTML file: {safe_name}")
        extra_meta["file_format"] = "html"
        return html_title or stem, text, "file", extra_meta

    if suffix in _TEXT_FILE_SUFFIXES:
        text = raw_bytes.decode(encoding="utf-8", errors="ignore")
        if not text.strip():
            raise FetchError(f"empty text file: {safe_name}")
        return stem, text, "file", extra_meta

    raise FetchError(f"unsupported file type (M1): {suffix}")


def fetch_file_bytes(raw_bytes: bytes, *, filename: str, full_content: bool = True, max_bytes: int = MAX_STAGE_FILE_BYTES) -> Document:
    """Parse uploaded or local document bytes into a staged or ingestable Document."""
    if not raw_bytes:
        raise FetchError(f"empty file: {filename}")
    if len(raw_bytes) > max_bytes:
        raise FetchError(f"file exceeds size limit ({len(raw_bytes)} > {max_bytes} bytes)")

    title, text, source_type, meta = _extract_raw_file_content(raw_bytes, filename=filename)
    settings = fetch_settings(None, get_settings)
    budget = 0 if full_content else (settings.pdf_max_extract_chars if source_type in ("pdf", "odt", "docx") else settings.raw_char_budget)
    appendix_truncated = False
    references_truncated = False
    if source_type == "pdf":
        from .pdf import slice_pdf_text

        exclude_app = False if full_content else settings.pdf_exclude_appendix
        exclude_ref = False if full_content else settings.pdf_exclude_references
        raw_text, is_truncated, appendix_truncated, references_truncated, orig_chars, raw_chars = slice_pdf_text(text or "", budget, strategy=settings.slicing_strategy, exclude_appendix=exclude_app, exclude_references=exclude_ref)
    else:
        raw_text, is_truncated, orig_chars, raw_chars = slice_document_text(text or "", budget, strategy=settings.slicing_strategy)

    meta.update({
        "raw_truncated": is_truncated,
        "appendix_truncated": appendix_truncated,
        "references_truncated": references_truncated,
        "orig_chars": orig_chars,
        "raw_chars": raw_chars,
    })
    return Document(
        url=None,
        title=title,
        author=None,
        published_at=None,
        raw_text=raw_text,
        source_type=source_type,
        content_hash=content_hash(title, text),
        meta=meta,
    )


def fetch_file(path: str, *, full_content: bool = False) -> Document:
    """Load a local document file (.md/.txt/.rst/.adoc/.html/.pdf/.odt/.docx)."""
    p = Path(path)
    if not p.exists():
        raise FetchError(f"file not found: {path}")

    format_preference = None
    orig_requested_path = None
    if p.suffix.lower() == ".pdf":
        odt_sibling = p.with_suffix(".odt")
        if odt_sibling.is_file():
            orig_requested_path = str(p.resolve())
            p = odt_sibling
            format_preference = "odt_over_pdf"

    raw_bytes = p.read_bytes()
    doc = fetch_file_bytes(raw_bytes, filename=p.name, full_content=full_content)
    if doc.meta is None:
        doc.meta = {}
    if format_preference:
        doc.meta["format_preference"] = format_preference
        if orig_requested_path:
            doc.meta["original_requested_file"] = orig_requested_path
    return doc
