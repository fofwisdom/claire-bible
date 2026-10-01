"""Presentation management service.

Handles AOT caching, cache invalidation, and orchestration between SQLite
metadata and the Asciidoctor reveal.js compiler.
"""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

from ..store import db as dbm
from .converter import compile_presentation_html, find_asciidoctor_executable
from .preprocessor import preprocess_adoc_to_slides


class PresentationService:
    def __init__(self, data_dir: Path | str | None = None) -> None:
        if data_dir is None:
            # Default to standard Claire Bible data directory
            self.data_dir = Path("data")
        else:
            self.data_dir = Path(data_dir)
        self.presentations_dir = self.data_dir / "presentations"
        self.presentations_dir.mkdir(parents=True, exist_ok=True)

    @classmethod
    def is_engine_available(cls) -> bool:
        """Check if asciidoctor-revealjs compiler is available on system."""
        return find_asciidoctor_executable() is not None

    def get_presentation_file_path(self, doc_id: str) -> Path:
        """Get output path for presentation HTML file."""
        return self.presentations_dir / f"{doc_id}.html"

    def get_presentation_metadata(
        self, conn: sqlite3.Connection, doc_id: str
    ) -> dict | None:
        """Get presentation record from database if exists."""
        return dbm.get_document_presentation(conn, doc_id)

    async def get_or_create_presentation(
        self,
        conn: sqlite3.Connection,
        doc_id: str,
        *,
        theme: str = "night",
        transition: str = "slide",
        force_recompile: bool = False,
    ) -> dict:
        """Retrieve cached presentation or compile a new one."""
        # 1. Fetch document data
        row = conn.execute(
            """
            SELECT id, title, author, published_at, detail, detail_format
            FROM documents
            WHERE id = ?
            """,
            (doc_id,),
        ).fetchone()

        if not row:
            raise KeyError(f"Document not found: {doc_id}")

        summary = dbm.latest_extraction_summary(conn, doc_id)

        detail = row["detail"] or ""
        if not detail.strip():
            raise ValueError(f"Document {doc_id} has no detail content to present")

        # 2. Compute content hash and cache key
        content_hash = hashlib.sha256(detail.encode("utf-8")).hexdigest()
        cache_key = hashlib.sha256(
            f"{content_hash}:{theme}:{transition}".encode("utf-8")
        ).hexdigest()

        target_file = self.get_presentation_file_path(doc_id)

        # 3. Check existing cache
        if not force_recompile:
            existing = dbm.get_document_presentation(conn, doc_id)
            if (
                existing
                and existing.get("status") == "ready"
                and existing.get("content_hash") == content_hash
                and existing.get("theme") == theme
                and existing.get("transition") == transition
                and target_file.exists()
            ):
                return existing

        # 4. Preprocess AsciiDoc to slide format
        slide_adoc = preprocess_adoc_to_slides(
            detail,
            title=row["title"] or "Claire Bible Document",
            author=row["author"],
            published_at=row["published_at"],
            summary=summary,
            theme=theme,
            transition=transition,
            customcss="/static/css/reveal-claire.css",
            revealjsdir="/static/vendor/reveal.js",
        )

        # Count slides roughly based on == and ===
        slide_count = slide_adoc.count("\n== ") + slide_adoc.count("\n=== ") + 1

        # Update status to compiling
        dbm.save_document_presentation(
            conn,
            document_id=doc_id,
            content_hash=content_hash,
            cache_key=cache_key,
            file_path=str(target_file),
            file_size=0,
            theme=theme,
            transition=transition,
            slide_count=slide_count,
            status="compiling",
        )

        # 5. Compile via Asciidoctor reveal.js
        try:
            _, duration_ms = await compile_presentation_html(
                slide_adoc,
                target_file,
                doc_id=doc_id,
            )
            file_size = target_file.stat().st_size

            # Save ready state
            dbm.save_document_presentation(
                conn,
                document_id=doc_id,
                content_hash=content_hash,
                cache_key=cache_key,
                file_path=str(target_file),
                file_size=file_size,
                theme=theme,
                transition=transition,
                slide_count=slide_count,
                status="ready",
                error_message=None,
                compile_duration_ms=duration_ms,
            )
        except Exception as exc:
            dbm.update_document_presentation_status(
                conn,
                document_id=doc_id,
                status="failed",
                error_message=str(exc),
            )
            raise

        record = dbm.get_document_presentation(conn, doc_id)
        return record or {}
