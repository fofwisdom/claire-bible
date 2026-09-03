"""텍스트/키워드 및 로컬 파일 fetcher (네트워크 불필요)."""

from __future__ import annotations

from pathlib import Path

from ...config import get_settings
from ...extract.table_budget import slice_document_text
from ...ontology.base import Document
from ..normalize import content_hash


def fetch_text(payload: str, *, full_content: bool = False) -> Document:
    """자유 텍스트/키워드 → Note 성격의 Document."""
    text = payload.strip()
    title = text.splitlines()[0][:80] if text else "untitled note"
    return Document(
        title=title,
        raw_text=text,
        source_type="text",
        content_hash=content_hash(text),
        meta={"raw_truncated": False, "orig_chars": len(text), "raw_chars": len(text)},
    )


def fetch_file(path: str, *, full_content: bool = False) -> Document:
    """로컬 파일 (.md/.txt/.pdf 등)."""
    p = Path(path)
    if not p.exists():
        from .base import FetchError

        raise FetchError(f"file not found: {path}")
    suffix = p.suffix.lower()
    raw_bytes = p.read_bytes()
    biblio: dict[str, Any] = {}
    if suffix == ".pdf" or raw_bytes.startswith(b"%PDF-"):
        from .pdf import extract_pdf_bytes

        pdf_res = extract_pdf_bytes(raw_bytes, fallback_title=p.stem)
        title, text, _, _, perr, _ = pdf_res[:6]
        biblio = getattr(pdf_res, "biblio", None) or (pdf_res[6] if len(pdf_res) > 6 and isinstance(pdf_res[6], dict) else {})
        if perr or not text:
            from .base import FetchError

            raise FetchError(perr or f"empty PDF file: {path}")
        source_type = "pdf"
    elif suffix in {".md", ".txt", ".markdown", ".rst", ""}:
        text = raw_bytes.decode(encoding="utf-8", errors="ignore")
        title = p.stem
        source_type = "file"
    else:
        from .base import FetchError

        raise FetchError(f"unsupported file type (M1): {suffix}")
    settings = get_settings()
    budget = 0 if full_content else (settings.pdf_max_extract_chars if source_type == "pdf" else settings.raw_char_budget)
    appendix_truncated = False
    references_truncated = False
    if source_type == "pdf":
        from .pdf import slice_pdf_text

        exclude_app = False if full_content else settings.pdf_exclude_appendix
        exclude_ref = False if full_content else settings.pdf_exclude_references
        raw_text, is_truncated, appendix_truncated, references_truncated, orig_chars, raw_chars = slice_pdf_text(
            text or "",
            budget,
            strategy=settings.slicing_strategy,
            exclude_appendix=exclude_app,
            exclude_references=exclude_ref,
        )
    else:
        raw_text, is_truncated, orig_chars, raw_chars = slice_document_text(
            text or "", budget, strategy=settings.slicing_strategy
        )
    meta: dict[str, Any] = {
        "raw_truncated": is_truncated,
        "appendix_truncated": appendix_truncated,
        "references_truncated": references_truncated,
        "orig_chars": orig_chars,
        "raw_chars": raw_chars,
    }
    if biblio:
        meta["biblio"] = biblio
    return Document(
        url=f"file://{p.resolve()}",
        title=title or p.stem,
        author=biblio.get("author") if biblio else None,
        published_at=biblio.get("published_at") if biblio else None,
        raw_text=raw_text,
        source_type=source_type,
        content_hash=content_hash(title or "", text),
        meta=meta,
    )

