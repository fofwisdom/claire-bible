"""텍스트/키워드 및 로컬 파일 fetcher (네트워크 불필요)."""

from __future__ import annotations

from pathlib import Path

from ...ontology.base import Document
from ..normalize import content_hash


def fetch_text(payload: str) -> Document:
    """자유 텍스트/키워드 → Note 성격의 Document."""
    text = payload.strip()
    title = text.splitlines()[0][:80] if text else "untitled note"
    return Document(
        title=title,
        raw_text=text,
        source_type="text",
        content_hash=content_hash(text),
    )


def fetch_file(path: str) -> Document:
    """로컬 파일 (.md/.txt). PDF 등 바이너리는 후순위."""
    p = Path(path)
    if not p.exists():
        from .base import FetchError

        raise FetchError(f"file not found: {path}")
    suffix = p.suffix.lower()
    if suffix in {".md", ".txt", ".markdown", ".rst", ""}:
        text = p.read_text(encoding="utf-8", errors="ignore")
    else:
        from .base import FetchError

        raise FetchError(f"unsupported file type (M1): {suffix}")
    return Document(
        url=f"file://{p.resolve()}",
        title=p.stem,
        raw_text=text,
        source_type="file",
        content_hash=content_hash(text),
    )
