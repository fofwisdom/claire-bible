"""Presentation management service.

Handles LLM-driven presentation authoring (composition), AOT caching,
cache invalidation, and orchestration with the Asciidoctor reveal.js compiler.
"""

import asyncio
import hashlib
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from ..ontology.base import Document
from ..store import db as dbm
from .converter import compile_presentation_html, find_asciidoctor_executable
from .preprocessor import (
    prepare_presentation_adoc_for_compile,
    preprocess_adoc_to_slides,
)

# Concurrency slot = 1: Dedicated worker pool and semaphore to prevent freezing the server
_PRES_SEMAPHORE = asyncio.Semaphore(1)
_PRES_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pres-worker")
_PRES_IN_FLIGHT: dict[str, asyncio.Future[dict]] = {}
_PRES_IN_FLIGHT_LOCK = asyncio.Lock()


class PresentationService:
    def __init__(
        self,
        data_dir: Path | str | None = None,
        settings: Any = None,
    ) -> None:
        self.settings = settings
        if data_dir is None:
            self.data_dir = Path(settings.data_dir if settings else "data")
        else:
            self.data_dir = Path(data_dir)
        self.presentations_dir = self.data_dir / "presentations"
        self.presentations_dir.mkdir(parents=True, exist_ok=True)

    @classmethod
    def is_engine_available(cls) -> bool:
        """Check if asciidoctor-revealjs compiler is available on system."""
        return find_asciidoctor_executable() is not None

    @classmethod
    def is_in_flight(cls, key: str) -> bool:
        """Check if a presentation authoring/compiling job is currently in-flight."""
        return key in _PRES_IN_FLIGHT

    def get_presentation_file_path(self, doc_id: str) -> Path:
        """Get output path for compiled presentation HTML file."""
        return self.presentations_dir / f"{doc_id}.html"

    def get_presentation_adoc_path(self, doc_id: str) -> Path:
        """Get output path for authored presentation AsciiDoc source file."""
        return self.presentations_dir / f"{doc_id}.adoc"

    def get_presentation_metadata(
        self, conn: sqlite3.Connection, doc_id: str
    ) -> dict | None:
        """Get presentation record from database if exists."""
        return dbm.get_document_presentation(conn, doc_id)

    async def compose_presentation(
        self,
        conn: sqlite3.Connection,
        doc_id: str,
        *,
        provider: Any = None,
        focus: str | None = None,
        slide_budget: int = 10,
        theme: str = "night",
        transition: str = "slide",
        effort: str | None = None,
    ) -> dict:
        """원문 기술 지식을 바탕으로 LLM을 호출하여 발표 전용 AsciiDoc 덱과 발표자 노트를 집필한다."""
        row = conn.execute(
            """
            SELECT id, title, author, published_at, detail, detail_format, meta, url, canonical_url
            FROM documents
            WHERE id = ?
            """,
            (doc_id,),
        ).fetchone()

        if not row:
            raise KeyError(f"Document not found: {doc_id}")

        detail = (row["detail"] or "").strip()
        if not detail:
            raise ValueError(f"Document {doc_id} has no detail content to present")

        summary = dbm.latest_extraction_summary(conn, doc_id)

        # Provider resolving
        if provider is None:
            from ..config import get_settings
            from ..extract.provider import get_provider
            s = self.settings or get_settings()
            provider = get_provider(s)

        import json
        meta_dict = {}
        if "meta" in row.keys() and row["meta"]:
            try:
                meta_dict = json.loads(row["meta"])
            except Exception:
                meta_dict = {}

        # Create Document object for provider call
        doc_obj = Document(
            id=doc_id,
            title=row["title"] or "Claire Bible Document",
            url=row["url"] or row["canonical_url"] or "",
            author=row["author"],
            published_at=row["published_at"],
            raw_text=detail,
            meta=meta_dict,
        )

        # Hash calculations
        content_hash = hashlib.sha256(detail.encode("utf-8")).hexdigest()
        html_file = self.get_presentation_file_path(doc_id)

        # Mark composing state in DB so clients know work is in progress
        dbm.save_document_presentation(
            conn,
            document_id=doc_id,
            content_hash=content_hash,
            adoc_hash="",
            cache_key="",
            file_path=str(html_file),
            file_size=0,
            theme=theme,
            transition=transition,
            slide_count=0,
            presentation_adoc="",
            status="composing",
        )

        t_start = time.perf_counter()
        try:
            loop = asyncio.get_running_loop()
            deck_adoc = await loop.run_in_executor(
                _PRES_EXECUTOR,
                lambda: provider.compose_presentation(
                    doc_obj,
                    summary=summary,
                    focus=focus,
                    slide_budget=slide_budget,
                    theme=theme,
                    transition=transition,
                    effort=effort,
                ),
            )
        except Exception as exc:
            dbm.update_document_presentation_status(
                conn,
                document_id=doc_id,
                status="failed",
                error_message=str(exc),
            )
            raise
        compose_ms = int((time.perf_counter() - t_start) * 1000)
        adoc_hash = hashlib.sha256(deck_adoc.encode("utf-8")).hexdigest()
        cache_key = hashlib.sha256(
            f"{adoc_hash}:{theme}:{transition}".encode("utf-8")
        ).hexdigest()

        # Count slides
        slide_count = deck_adoc.count("\n== ") + deck_adoc.count("\n=== ") + 1

        # Save .adoc to disk
        adoc_file = self.get_presentation_adoc_path(doc_id)
        adoc_file.write_text(deck_adoc, encoding="utf-8")

        html_file = self.get_presentation_file_path(doc_id)

        # Save to DB
        provider_name = getattr(provider, "name", "unknown")
        provider_model = getattr(provider, "model", "")
        dbm.save_presentation_adoc(
            conn,
            document_id=doc_id,
            presentation_adoc=deck_adoc,
            content_hash=content_hash,
            adoc_hash=adoc_hash,
            authoring_provider=provider_name,
            authoring_model=provider_model,
            prompt_version="pres-v1",
            compose_duration_ms=compose_ms,
            slide_count=slide_count,
            theme=theme,
            transition=transition,
            cache_key=cache_key,
            file_path=str(html_file),
            status="authored",
        )

        record = dbm.get_document_presentation(conn, doc_id)
        return record or {}

    async def compile_presentation(
        self,
        conn: sqlite3.Connection,
        doc_id: str,
        *,
        presentation_adoc: str | None = None,
        theme: str = "night",
        transition: str = "slide",
        force_recompile: bool = False,
    ) -> dict:
        """저작된 AsciiDoc 프레젠테이션 덱을 asciidoctor-revealjs로 즉시 컴파일한다."""
        target_file = self.get_presentation_file_path(doc_id)
        existing = dbm.get_document_presentation(conn, doc_id)

        # 1. Resolve presentation AsciiDoc source
        adoc_content = presentation_adoc
        if not adoc_content:
            adoc_content = dbm.get_presentation_adoc(conn, doc_id)
        if not adoc_content:
            adoc_path = self.get_presentation_adoc_path(doc_id)
            if adoc_path.exists():
                adoc_content = adoc_path.read_text(encoding="utf-8")

        # Fallback for legacy documents without authored presentation
        if not adoc_content:
            row = conn.execute(
                "SELECT title, author, published_at, detail FROM documents WHERE id = ?",
                (doc_id,),
            ).fetchone()
            if not row or not (row["detail"] or "").strip():
                raise ValueError(f"Document {doc_id} has no presentation content or detail to compile.")
            summary = dbm.latest_extraction_summary(conn, doc_id)
            adoc_content = preprocess_adoc_to_slides(
                row["detail"],
                title=row["title"] or "Claire Bible Document",
                author=row["author"],
                published_at=row["published_at"],
                summary=summary,
                theme=theme,
                transition=transition,
            )

        # 2. Check cache
        content_hash = (existing or {}).get("content_hash") or hashlib.sha256(adoc_content.encode("utf-8")).hexdigest()
        adoc_hash = hashlib.sha256(adoc_content.encode("utf-8")).hexdigest()
        cache_key = hashlib.sha256(f"{adoc_hash}:{theme}:{transition}".encode("utf-8")).hexdigest()

        if (
            not force_recompile
            and existing
            and existing.get("status") == "ready"
            and (existing.get("adoc_hash") == adoc_hash or existing.get("cache_key") == cache_key)
            and existing.get("theme") == theme
            and existing.get("transition") == transition
            and target_file.exists()
        ):
            res_dict = dict(existing)
            res_dict["is_ready"] = True
            return res_dict

        # 3. Prepare compiler attributes (theme, customcss, revealjsdir)
        compiler_adoc = prepare_presentation_adoc_for_compile(
            adoc_content,
            theme=theme,
            transition=transition,
            customcss="/static/css/reveal-claire.css",
            revealjsdir="/static/vendor/reveal.js",
        )

        slide_count = compiler_adoc.count("\n== ") + compiler_adoc.count("\n=== ") + 1

        # Mark compiling
        dbm.save_document_presentation(
            conn,
            document_id=doc_id,
            content_hash=content_hash,
            adoc_hash=adoc_hash,
            cache_key=cache_key,
            file_path=str(target_file),
            file_size=0,
            theme=theme,
            transition=transition,
            slide_count=slide_count,
            presentation_adoc=adoc_content,
            status="compiling",
        )

        # 4. Execute compilation
        try:
            _, duration_ms = await compile_presentation_html(
                compiler_adoc,
                target_file,
                doc_id=doc_id,
            )
            file_size = target_file.stat().st_size

            dbm.save_document_presentation(
                conn,
                document_id=doc_id,
                content_hash=content_hash,
                adoc_hash=adoc_hash,
                cache_key=cache_key,
                file_path=str(target_file),
                file_size=file_size,
                theme=theme,
                transition=transition,
                slide_count=slide_count,
                presentation_adoc=adoc_content,
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

        record = dbm.get_document_presentation(conn, doc_id) or {}
        if record.get("status") == "ready":
            record["is_ready"] = True
        return record

    async def get_or_create_presentation(
        self,
        conn: sqlite3.Connection,
        doc_id: str,
        *,
        force_compose: bool = False,
        force_recompile: bool = False,
        provider: Any = None,
        focus: str | None = None,
        slide_budget: int = 10,
        theme: str = "night",
        transition: str = "slide",
        effort: str | None = None,
        allow_compose: bool = True,
    ) -> dict:
        """저작 및 컴파일을 오케스트레이션하여 유효한 프레젠테이션을 보장한다."""
        target_file = self.get_presentation_file_path(doc_id)
        existing = dbm.get_document_presentation(conn, doc_id)

        # Check document detail
        row = conn.execute("SELECT detail FROM documents WHERE id = ?", (doc_id,)).fetchone()
        if not row:
            raise KeyError(f"Document not found: {doc_id}")
        detail = (row["detail"] or "").strip()
        if not detail:
            raise ValueError(f"Document {doc_id} has no detail content to present")

        current_content_hash = hashlib.sha256(detail.encode("utf-8")).hexdigest()

        # If cache is valid and up to date, return directly
        if (
            not force_compose
            and not force_recompile
            and existing
            and existing.get("status") == "ready"
            and existing.get("content_hash") == current_content_hash
            and existing.get("theme") == theme
            and existing.get("transition") == transition
            and target_file.exists()
        ):
            return existing

        has_adoc = existing and existing.get("presentation_adoc")

        # If composition is disallowed (e.g. from read-only GET routes) and not already authored
        if not allow_compose and (force_compose or not has_adoc):
            return {
                "document_id": doc_id,
                "status": (existing.get("status") if existing else "not_created"),
                "file_path": str(target_file),
                "is_ready": False,
            }

        cache_key_inflight = f"{doc_id}:{theme}:{transition}"
        async with _PRES_IN_FLIGHT_LOCK:
            if cache_key_inflight in _PRES_IN_FLIGHT:
                future = _PRES_IN_FLIGHT[cache_key_inflight]
                return await future
            loop = asyncio.get_running_loop()
            future = loop.create_future()
            _PRES_IN_FLIGHT[cache_key_inflight] = future

        try:
            async with _PRES_SEMAPHORE:
                existing = dbm.get_document_presentation(conn, doc_id)
                has_adoc = existing and existing.get("presentation_adoc")

                if force_compose or not has_adoc:
                    await self.compose_presentation(
                        conn,
                        doc_id,
                        provider=provider,
                        focus=focus,
                        slide_budget=slide_budget,
                        theme=theme,
                        transition=transition,
                        effort=effort,
                    )

                res = await self.compile_presentation(
                    conn,
                    doc_id,
                    theme=theme,
                    transition=transition,
                    force_recompile=force_recompile,
                )
                if not future.done():
                    future.set_result(res)
                return res
        except Exception as exc:
            if not future.done():
                future.set_exception(exc)
            raise
        finally:
            async with _PRES_IN_FLIGHT_LOCK:
                _PRES_IN_FLIGHT.pop(cache_key_inflight, None)
