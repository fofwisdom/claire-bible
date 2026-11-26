"""Document lifecycle and personal KB ingest endpoints for `/api/v1/*`."""

from __future__ import annotations

import asyncio
import json
import mimetypes
import re
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from urllib.parse import quote

from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response, StreamingResponse

from ...accounts import provider_policy as policy
from ...collaboration import contributions
from ...core.ports import get_document_renderer
from ...core.secret_scrub import scrub_exception
from ...extract.provider import set_progress_callback
from ...extract.rendition_templates import ADDITIONAL_RENDITION_TEMPLATES, PRIMARY_RENDITION_TEMPLATE
from ...ingest.active import purge_active_matrix
from ...ingest.extraction_mode import parse_extraction_mode
from ...ingest.renditions import run_ingest_renditions
from ...ingest.report_json import report_to_dict
from ...store import db as dbm, document_renditions as renditions_store, kb_collab, permalinks
from ...store.queries import DOCUMENTS_MAX_IDS, DOCUMENTS_MAX_LIMIT, document_detail, documents_list, documents_page
from ...telegram.bot import parse_message_focus
from ._context import (
    V1Context,
    apply_source_rule,
    enforce_v1_auth_policy,
    entity_language_kwargs,
    hides_sources,
    kb_number_str,
    kb_runtime_context,
    read_v1_json,
    read_v1_json_dict,
    refuse_server_api_for_agent_client,
    resolve_kb,
    resolve_personal_kb_context,
    sees_hidden,
)
from .errors import V1Error, error_response, not_found_error, v1_endpoint
from .pagination import decode_document_cursor, encode_document_cursor, paginated_payload, parse_limit
from .schemas import (
    AddRenditionRequest,
    AddRenditionResponse,
    DeleteDocumentResponse,
    DeleteRenditionResponse,
    DocumentDetailResponse,
    DocumentEntityItem,
    DocumentListItem,
    DocumentListResponse,
    DocumentRenditionItem,
    DocumentRenditionsResponse,
    IngestRequest,
    IngestResponse,
    UpdateDocumentRequest,
    UpdateDocumentResponse,
)

_PROGRESS_QUEUE_SIZE = 128

_RAW_SECURITY_HEADERS = {
    "Content-Security-Policy": "sandbox; default-src 'none'",
    "X-Content-Type-Options": "nosniff",
}

_RAW_MEDIA_TYPES = {
    ".pdf": "application/pdf",
    ".odt": "application/vnd.oasis.opendocument.text",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain; charset=utf-8",
    ".md": "text/markdown; charset=utf-8",
    ".json": "application/json",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}

_RAW_ACTIVE_SUFFIXES = {".html", ".htm", ".xhtml", ".svg", ".svgz", ".xml", ".xsl", ".js", ".mjs"}


class _DocumentNotExtracted(Exception):
    pass


def _parse_bool_param(raw: str | None, name: str) -> bool | None:
    if raw is None or raw == "":
        return None
    low = raw.strip().lower()
    if low in ("1", "true"):
        return True
    if low in ("0", "false"):
        return False
    raise V1Error(400, f"invalid_{name}", f"{name} must be true/false or 1/0")


def _is_active_media_type(mt: str) -> bool:
    base = mt.split(";", 1)[0].strip().lower()
    subtype = base.split("/", 1)[1] if "/" in base else ""
    return (
        base in {"text/html", "image/svg+xml", "application/xhtml+xml"}
        or subtype == "xml"
        or subtype.endswith("+xml")
        or "javascript" in base
        or "ecmascript" in base
    )


def _validate_effort(kb_svc: Any, kb_settings: Any, effort: str | None) -> str | None:
    if not effort:
        return None
    try:
        return policy.validate_ingest_effort(
            getattr(kb_svc, "s", kb_settings),
            effort,
            ctx=getattr(kb_svc, "provider_context", None),
            allow_mock_standard=True,
        )
    except ValueError as exc:
        raise V1Error(400, "invalid_effort", str(exc)) from exc


async def _parse_ingest_request(ctx: V1Context, request: Request) -> tuple[IngestRequest, str, str | None]:
    raw = await read_v1_json_dict(request)
    if "extraction_mode" not in raw:
        raise V1Error(400, "invalid_extraction_mode", "extraction_mode is required ('provider' or 'client')")
    try:
        parse_extraction_mode(raw.get("extraction_mode"))
    except ValueError as exc:
        raise V1Error(400, "invalid_extraction_mode", "extraction_mode must be 'provider' or 'client'") from exc
    req = IngestRequest.model_validate(raw)
    if len(set(req.renditions)) != len(req.renditions):
        raise V1Error(400, "invalid_renditions", "renditions must not repeat a template")
    payload_text = req.payload.strip()
    if not payload_text:
        raise V1Error(400, "invalid_payload", "payload is required")
    if req.extraction_mode == "provider":
        await refuse_server_api_for_agent_client(ctx, request)
    payload_clean, _sealed_focus = parse_message_focus(payload_text)
    if not payload_clean:
        raise V1Error(400, "invalid_payload", "payload is required")
    return req, payload_clean, (req.effort.strip() if req.effort else None)


def _ingest_renditions(kb: Any, kb_settings: Any, kb_svc: Any, report: Any, req: IngestRequest, effort: str | None) -> list[dict[str, str]]:
    """The additional renditions of an ingest request (``claire.ingest.renditions``)."""
    return run_ingest_renditions(kb_svc=kb_svc, db_file=kb_settings.db_file, kb_id=kb.id, report=report, templates=list(req.renditions), extraction_mode=req.extraction_mode, effort=effort)


def build_document_handlers(ctx: V1Context) -> dict[str, Any]:
    """Build Starlette route handlers for v1 document and ingest endpoints."""

    @v1_endpoint
    async def list_documents(request: Request) -> JSONResponse:
        kb = resolve_kb(ctx, request, capability="read")
        _, kb_settings, _ = kb_runtime_context(ctx, request, kb)
        include_hidden = sees_hidden(ctx, request, kb)
        mask_sources = hides_sources(ctx, request, kb)
        render_format = getattr(kb_settings, "render_format", "adoc")
        params = request.query_params

        try:
            limit = parse_limit(params.get("limit"), default=50, minimum=1, maximum=DOCUMENTS_MAX_LIMIT)
        except ValueError as exc:
            raise V1Error(400, "invalid_limit", f"limit must be an integer between 1 and {DOCUMENTS_MAX_LIMIT}") from exc

        before_str: str | None = None
        raw_cursor = params.get("cursor")
        if raw_cursor:
            try:
                ts_val, cursor_doc_id = decode_document_cursor(raw_cursor)
                before_str = dbm.encode_documents_cursor(ts_val, cursor_doc_id)
            except ValueError as exc:
                raise V1Error(400, "invalid_cursor", "Invalid pagination cursor") from exc

        filters: dict[str, Any] = {
            "pinned": _parse_bool_param(params.get("pinned"), "pinned"),
            "hidden": _parse_bool_param(params.get("hidden"), "hidden"),
        }
        q_text = (params.get("q") or "").strip()
        if len(q_text) > 200:
            raise V1Error(400, "invalid_query", "q must be 200 characters or less")
        filters["text"] = q_text or None

        detail_filter = params.get("detail") or None
        if detail_filter is not None and detail_filter not in ("missing", "mismatched"):
            raise V1Error(400, "invalid_detail", "detail must be 'missing' or 'mismatched'")
        if detail_filter:
            filters["detail"] = detail_filter
            filters["detail_format"] = render_format

        if "id" in params:
            doc_ids = [v for v in params.getlist("id") if v]
            if len(doc_ids) > DOCUMENTS_MAX_IDS:
                raise V1Error(400, "invalid_ids", f"id can be given {DOCUMENTS_MAX_IDS} times or less")
            filters["ids"] = doc_ids

        def _query() -> DocumentListResponse:
            conn = dbm.connect_existing(kb_settings.db_file, readonly=True)
            try:
                page = documents_page(
                    conn,
                    limit,
                    before=before_str,
                    include_hidden=include_hidden,
                    kb_id=kb.id,
                    list_func=documents_list,
                    **filters,
                )
                docs = page["documents"]
                if mask_sources:
                    docs = apply_source_rule(conn, kb, docs)
                items = [
                    DocumentListItem(
                        id=str(d["id"]),
                        title=str(d.get("title") or "(제목 없음)"),
                        url=d.get("url"),
                        source_type=d.get("source_type"),
                        fetched_at=float(d["fetched_at"]) if d.get("fetched_at") is not None else None,
                        seen=bool(d.get("seen")),
                        watch=bool(d.get("watch")),
                        pinned=bool(d.get("pinned")),
                        hidden=bool(d.get("hidden")),
                        summary=str(d.get("summary") or ""),
                        has_presentation=bool(d.get("has_presentation")),
                        has_detail=bool(d.get("has_detail")),
                        detail_format=str(d.get("detail_format") or "adoc"),
                    )
                    for d in docs
                ]
                next_cursor = None
                if page.get("next_before"):
                    next_ts, next_id = dbm.decode_documents_cursor(str(page["next_before"]))
                    next_cursor = encode_document_cursor(next_ts, next_id)
                return DocumentListResponse.model_validate(
                    paginated_payload(items, next_cursor=next_cursor, total=int(page["total"]))
                )
            finally:
                conn.close()

        payload = await asyncio.to_thread(_query)
        return JSONResponse(payload.model_dump())

    @v1_endpoint
    async def get_document(request: Request) -> JSONResponse:
        kb = resolve_kb(ctx, request, capability="read")
        _, kb_settings, _ = kb_runtime_context(ctx, request, kb)
        doc_id = str(request.path_params.get("doc_id") or "").strip()
        if not doc_id:
            raise V1Error(400, "invalid_document_id", "Document id is required")
        include_hidden = sees_hidden(ctx, request, kb)
        mask_sources = hides_sources(ctx, request, kb)
        lang_kwargs = entity_language_kwargs(request)

        def _load() -> DocumentDetailResponse | None:
            conn = dbm.connect_existing(kb_settings.db_file, readonly=True)
            try:
                rep = document_detail(conn, doc_id, include_hidden=include_hidden, kb_id=kb.id, **lang_kwargs)
                if rep is None:
                    return None
                if mask_sources:
                    rep = apply_source_rule(conn, kb, [rep])[0]
                row = dbm.get_document_row(conn, doc_id, kb_id=kb.id)
                token = permalinks.token_of(conn, "document", doc_id)
            finally:
                conn.close()
            if token is None:
                token = permalinks.ensure_document_token(kb_settings.db_file, doc_id, kb.id)
            permalink = permalinks.permalink_path(token) if token else None
            entities = [
                DocumentEntityItem(
                    id=str(n["id"]),
                    name=str(n["label"]),
                    type=str(n["group"]),
                    observations=[str(o) for o in (n.get("observations") or [])],
                    aliases=[str(a) for a in (n.get("aliases") or [])],
                )
                for n in (rep.get("nodes") or [])
            ]
            return DocumentDetailResponse(
                id=str(rep["id"]),
                kb_number=kb_number_str(kb),
                title=str(rep.get("title") or "(제목 없음)"),
                url=rep.get("url"),
                permalink=permalink,
                source_type=rep.get("source_type"),
                summary=str(rep.get("summary") or ""),
                detail=str(rep.get("detail") or ""),
                detail_format=str(rep.get("detail_format") or "adoc"),
                detail_html=str(rep.get("detail_html") or ""),
                hidden=bool(rep.get("hidden")),
                pinned=bool(row["pinned"]) if row is not None else False,
                seen=bool(row["seen"]) if row is not None else False,
                watch=bool(row["watch_enabled"]) if row is not None else False,
                author=rep.get("author"),
                published_at=rep.get("published_at"),
                fetched_at=float(rep["fetched_at"]) if rep.get("fetched_at") is not None else None,
                extra_sources=list(rep.get("extra_sources") or []),
                entities=entities,
                has_presentation=bool(rep.get("has_presentation")),
                presentation_status=rep.get("presentation_status"),
                has_raw_file=bool(rep.get("has_raw_file")),
                raw_file_name=rep.get("raw_file_name"),
                extraction_provider=rep.get("extraction_provider"),
                extraction_model=rep.get("extraction_model"),
                raw_truncated=bool(rep.get("raw_truncated", False)),
                meta=dict(rep.get("meta") or {}),
            )

        result = await asyncio.to_thread(_load)
        if result is None:
            raise not_found_error("Document not found")
        return JSONResponse(result.model_dump())

    @v1_endpoint
    async def get_document_raw(request: Request) -> Response:
        kb = resolve_kb(ctx, request, capability="read")
        _, kb_settings, _ = kb_runtime_context(ctx, request, kb)
        doc_id = str(request.path_params.get("doc_id") or "").strip()
        if not doc_id:
            return error_response(request, status_code=400, code="invalid_document_id", message="Document id is required", headers=_RAW_SECURITY_HEADERS)
        raw_sees_hidden = sees_hidden(ctx, request, kb)

        def _find_raw() -> tuple[str, str] | None:
            conn = dbm.connect_existing(kb_settings.db_file, readonly=True)
            try:
                doc_state = conn.execute(
                    "SELECT hidden, COALESCE(extraction_status, 'extracted') AS extraction_status FROM documents WHERE id=? AND kb_id=?",
                    (doc_id, kb.id),
                ).fetchone()
                if doc_state is None:
                    return None
                if not raw_sees_hidden and bool(doc_state["hidden"]):
                    return None
                if str(doc_state["extraction_status"] or "extracted") == "pending":
                    if raw_sees_hidden:
                        raise _DocumentNotExtracted()
                    return None
                try:
                    row = conn.execute(
                        "SELECT file_ref, file_name FROM raw_inbox WHERE document_id = ? AND kb_id = ? AND file_ref IS NOT NULL AND trim(file_ref) != '' ORDER BY id DESC LIMIT 1",
                        (doc_id, kb.id),
                    ).fetchone()
                    if row and row["file_ref"]:
                        return str(row["file_ref"]), str(row["file_name"] or "")
                except Exception:  # noqa: BLE001
                    pass
                row_doc = dbm.get_document_row(conn, doc_id, kb_id=kb.id)
                if row_doc:
                    raw_meta = row_doc["meta"] if "meta" in row_doc.keys() else None
                    if raw_meta:
                        try:
                            meta = json.loads(raw_meta)
                        except Exception:  # noqa: BLE001
                            meta = {}
                        pres_pdf = meta.get("presentation_pdf")
                        if isinstance(pres_pdf, dict) and pres_pdf.get("artifact_path"):
                            return str(pres_pdf["artifact_path"]), str(pres_pdf.get("file_name") or "")
                        if meta.get("original_requested_file"):
                            return str(meta["original_requested_file"]), ""
                return None
            finally:
                conn.close()

        try:
            resolved = await asyncio.to_thread(_find_raw)
        except _DocumentNotExtracted:
            return error_response(
                request,
                status_code=409,
                code="document_not_extracted",
                message="Document has not been extracted yet",
                headers=_RAW_SECURITY_HEADERS,
            )

        if resolved is None:
            return error_response(request, status_code=404, code="not_found", message="Raw file not found", headers=_RAW_SECURITY_HEADERS)

        file_ref, file_name = resolved
        raw_path = Path(file_ref)
        target_path = raw_path.resolve() if raw_path.is_absolute() else (kb_settings.data_dir / raw_path).resolve()
        allowed_roots = [kb_settings.data_dir.resolve(), ctx.settings.data_dir.resolve()]
        is_safe = False
        for root in allowed_roots:
            try:
                target_path.relative_to(root)
                is_safe = True
                break
            except ValueError:
                continue
        if not is_safe or not target_path.is_file():
            return error_response(request, status_code=404, code="not_found", message="Raw file not found", headers=_RAW_SECURITY_HEADERS)

        suffix = target_path.suffix.lower()
        guessed_type = mimetypes.guess_type(target_path.name)[0] or ""
        if suffix in _RAW_ACTIVE_SUFFIXES or _is_active_media_type(guessed_type):
            media_type = "application/octet-stream"
        else:
            media_type = _RAW_MEDIA_TYPES.get(suffix) or guessed_type or "application/octet-stream"
            if _is_active_media_type(media_type):
                media_type = "application/octet-stream"
        disposition = "attachment" if media_type == "application/octet-stream" else "inline"
        display_name = file_name or target_path.name
        safe_name = re.sub(r"[^a-zA-Z0-9._-]", "_", display_name) or "document"
        quoted_name = quote(display_name, safe="")
        headers = {
            "Content-Disposition": f'{disposition}; filename="{safe_name}"; filename*=UTF-8\'\'{quoted_name}',
            "Cache-Control": "private, no-cache, no-store, must-revalidate",
            **_RAW_SECURITY_HEADERS,
        }
        if media_type == "application/pdf":
            headers.pop("Content-Security-Policy", None)
        return FileResponse(str(target_path), media_type=media_type, headers=headers)

    @v1_endpoint
    async def export_document(request: Request) -> Response:
        kb = resolve_kb(ctx, request, capability="read")
        _, kb_settings, _ = kb_runtime_context(ctx, request, kb)
        doc_id = str(request.path_params.get("doc_id") or "").strip()
        if not doc_id:
            raise V1Error(400, "invalid_document_id", "Document id is required")
        fmt = request.query_params.get("format", "markdown").strip().lower()
        if fmt not in ("markdown", "md"):
            raise V1Error(400, "unsupported_format", "Only 'markdown' export format is supported", details={"formats": ["markdown"]})
        include_hidden = sees_hidden(ctx, request, kb)
        mask_sources = hides_sources(ctx, request, kb)

        def _load() -> dict[str, Any] | None:
            conn = dbm.connect_existing(kb_settings.db_file, readonly=True)
            try:
                rep = document_detail(conn, doc_id, include_hidden=include_hidden, kb_id=kb.id)
                if rep is not None and mask_sources:
                    rep = apply_source_rule(conn, kb, [rep])[0]
                return rep
            finally:
                conn.close()

        report = await asyncio.to_thread(_load)
        if report is None:
            raise not_found_error("Document not found")
        body = get_document_renderer().document_markdown(report)
        ascii_name = re.sub(r"[^A-Za-z0-9._-]+", "-", f"claire-{doc_id}").strip("-") + ".md"
        title_name = quote((str(report.get("title") or doc_id).strip() or doc_id)[:120] + ".md", safe="")
        return Response(
            body,
            media_type="text/markdown; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{title_name}',
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @v1_endpoint
    async def update_document(request: Request) -> JSONResponse:
        principal = enforce_v1_auth_policy(request)
        kb = resolve_kb(ctx, request, capability="curate")
        _, kb_settings, _ = kb_runtime_context(ctx, request, kb)
        doc_id = str(request.path_params.get("doc_id") or "").strip()
        if not doc_id:
            raise V1Error(400, "invalid_document_id", "Document id is required")
        body = await read_v1_json(request, UpdateDocumentRequest)
        if not body.model_fields_set:
            raise V1Error(400, "invalid_request", "At least one field (title, pinned, hidden, seen) is required")
        for bool_field in ("pinned", "hidden", "seen"):
            if bool_field in body.model_fields_set and getattr(body, bool_field) is None:
                raise V1Error(400, f"invalid_{bool_field}", f"{bool_field} must be a boolean")

        def _apply() -> UpdateDocumentResponse | None:
            conn = dbm.connect_existing(kb_settings.db_file)
            try:
                row = dbm.get_document_row(conn, doc_id, kb_id=kb.id)
                if row is None:
                    return None
                if "title" in body.model_fields_set:
                    new_title = body.title.strip() if body.title is not None else None
                    dbm.set_document_title(conn, doc_id, new_title, kb_id=kb.id)
                if "pinned" in body.model_fields_set and body.pinned is not None:
                    dbm.set_document_pinned(conn, doc_id, body.pinned, kb_id=kb.id)
                if "seen" in body.model_fields_set and body.seen is not None:
                    dbm.set_document_seen(conn, doc_id, seen=body.seen, kb_id=kb.id)
                if "hidden" in body.model_fields_set and body.hidden is not None:
                    if kb.is_shared:
                        contrib = kb_collab.get_contribution_for_document(conn, kb.id, doc_id)
                        if contrib is not None:
                            if principal.account_id is None:
                                raise V1Error(401, "authentication_required", "Authentication required")
                            contributions.set_hidden(
                                conn,
                                kb_id=kb.id,
                                contribution=int(contrib["public_id"]),
                                account_id=principal.account_id,
                                hidden=body.hidden,
                            )
                            conn.commit()
                        else:
                            dbm.set_document_hidden(conn, doc_id, body.hidden, kb_id=kb.id)
                    else:
                        dbm.set_document_hidden(conn, doc_id, body.hidden, kb_id=kb.id)
                updated_row = dbm.get_document_row(conn, doc_id, kb_id=kb.id)
                assert updated_row is not None
                return UpdateDocumentResponse(
                    id=doc_id,
                    kb_number=kb_number_str(kb),
                    title=updated_row["title"],
                    pinned=bool(updated_row["pinned"]),
                    hidden=bool(updated_row["hidden"]),
                    seen=bool(updated_row["seen"]),
                )
            except contributions.ContributionError as exc:
                raise V1Error(exc.status, exc.code, exc.code) from exc
            finally:
                conn.close()

        result = await asyncio.to_thread(_apply)
        if result is None:
            raise not_found_error("Document not found")
        return JSONResponse(result.model_dump())

    @v1_endpoint
    async def delete_document(request: Request) -> JSONResponse:
        kb = resolve_kb(ctx, request, capability="remove")
        _, kb_settings, _ = kb_runtime_context(ctx, request, kb)
        if kb.is_shared:
            raise V1Error(403, "kb_remove_by_contribution", "Shared KB documents must be removed via contribution removal")
        if not getattr(ctx.settings, "is_purge_allowed", True):
            raise V1Error(403, "purge_disabled", "Document deletion is disabled by CLAIRE_DATA_LIFECYCLE=append-only")
        doc_id = str(request.path_params.get("doc_id") or "").strip()
        if not doc_id:
            raise V1Error(400, "invalid_document_id", "Document id is required")
        dry_run = _parse_bool_param(request.query_params.get("dry_run"), "dry_run")
        tombstone = _parse_bool_param(request.query_params.get("tombstone"), "tombstone")
        dry_run_flag = bool(dry_run) if dry_run is not None else False
        tombstone_flag = bool(tombstone) if tombstone is not None else True
        reason = (request.query_params.get("reason") or "api_v1_delete").strip() or "api_v1_delete"

        def _do_delete() -> DeleteDocumentResponse | None:
            conn = dbm.connect_existing(kb_settings.db_file)
            try:
                row = dbm.get_document_row(conn, doc_id, kb_id=kb.id)
                if row is None:
                    return None
                report = dbm.purge_document_cascade(
                    conn,
                    data_dir=kb_settings.data_dir,
                    target_ids=[doc_id],
                    reason=reason,
                    dry_run=dry_run_flag,
                    tombstone=tombstone_flag,
                    kb_id=kb.id,
                )
            finally:
                conn.close()
            if not dry_run_flag:
                try:
                    purge_active_matrix(kb_settings.data_dir, doc_id)
                except Exception:  # noqa: BLE001
                    pass
            return DeleteDocumentResponse(
                id=doc_id,
                kb_number=kb_number_str(kb),
                deleted=not dry_run_flag,
                dry_run=dry_run_flag,
                tombstone=tombstone_flag,
                report=report,
            )

        result = await asyncio.to_thread(_do_delete)
        if result is None:
            raise not_found_error("Document not found")
        return JSONResponse(result.model_dump())

    @v1_endpoint
    async def list_document_renditions(request: Request) -> JSONResponse:
        kb = resolve_kb(ctx, request, capability="read")
        _, kb_settings, _ = kb_runtime_context(ctx, request, kb)
        doc_id = str(request.path_params.get("doc_id") or "").strip()
        if not doc_id:
            raise V1Error(400, "invalid_document_id", "Document id is required")
        include_hidden = sees_hidden(ctx, request, kb)

        def _load() -> DocumentRenditionsResponse | None:
            conn = dbm.connect_existing(kb_settings.db_file, readonly=True)
            try:
                row = dbm.get_document_row(conn, doc_id, kb_id=kb.id)
                if row is None:
                    return None
                doc_status = str(row["extraction_status"] or "extracted") if "extraction_status" in row.keys() else "extracted"
                if not include_hidden and (bool(row["hidden"]) or doc_status == "pending"):
                    return None
                listed = renditions_store.list_renditions(conn, doc_id, kb.id)
                items: list[DocumentRenditionItem] = []
                for r in listed:
                    if r.primary:
                        detail_val = str(row["detail"] or "")
                        detail_html_val = str(row["detail_html"] or "")
                        if detail_val.strip() and not detail_html_val:
                            detail_html_val = get_document_renderer().render_to_html(detail_val, format=str(row["detail_format"] or "adoc"))
                    else:
                        detail_val = str(r.detail or "")
                        detail_html_val = str(r.detail_html or "")
                    items.append(
                        DocumentRenditionItem(
                            template=r.template,
                            template_version=r.template_version,
                            primary=r.primary,
                            detail=detail_val,
                            detail_html=detail_html_val,
                            model_lineage=dict(r.model_lineage),
                            created_at=r.created_at,
                            updated_at=r.updated_at,
                        )
                    )
                return DocumentRenditionsResponse(document_id=doc_id, kb_number=kb_number_str(kb), renditions=items)
            finally:
                conn.close()

        result = await asyncio.to_thread(_load)
        if result is None:
            raise not_found_error("Document not found")
        return JSONResponse(result.model_dump())

    @v1_endpoint
    async def add_document_rendition(request: Request) -> JSONResponse:
        """정리본 추가: one more rendition of a stored document, from its stored raw text, with the server API (Section 5.3 rule 2)."""
        kb = resolve_kb(ctx, request, capability="curate")
        _, kb_settings, kb_svc = kb_runtime_context(ctx, request, kb)
        doc_id = str(request.path_params.get("doc_id") or "").strip()
        if not doc_id:
            raise V1Error(400, "invalid_document_id", "Document id is required")
        body = await read_v1_json(request, AddRenditionRequest)
        # The run uses the server API; an agent client writes renditions only with a staged extraction.
        await refuse_server_api_for_agent_client(ctx, request)
        try:
            items = await ctx.run_expensive(kb_svc.add_renditions, doc_id, [body.template])
        except LookupError:
            raise not_found_error("Document not found") from None
        except ValueError as exc:
            raise V1Error(409, "document_not_extracted", "The document waits for an agent client; add renditions after its extraction") from exc
        return JSONResponse(AddRenditionResponse.model_validate({"document_id": doc_id, "rendition": items[0]}).model_dump())

    @v1_endpoint
    async def delete_document_rendition(request: Request) -> JSONResponse:
        kb = resolve_kb(ctx, request, capability="curate")
        _, kb_settings, _ = kb_runtime_context(ctx, request, kb)
        doc_id = str(request.path_params.get("doc_id") or "").strip()
        if not doc_id:
            raise V1Error(400, "invalid_document_id", "Document id is required")
        template = str(request.path_params.get("template") or "").strip()
        if template == PRIMARY_RENDITION_TEMPLATE:
            raise V1Error(400, "primary_rendition_protected", "The primary standard rendition cannot be deleted")
        if template not in ADDITIONAL_RENDITION_TEMPLATES:
            raise V1Error(400, "invalid_rendition_template", f"Unknown rendition template: {template}")

        def _delete() -> DeleteRenditionResponse | str:
            conn = dbm.connect_existing(kb_settings.db_file)
            try:
                row = dbm.get_document_row(conn, doc_id, kb_id=kb.id)
                if row is None:
                    return "doc_not_found"
                if not renditions_store.delete_additional(conn, doc_id, kb.id, template):
                    return "rendition_not_found"
                conn.commit()
                return DeleteRenditionResponse(document_id=doc_id, kb_number=kb_number_str(kb), template=template, deleted=True)
            finally:
                conn.close()

        result = await asyncio.to_thread(_delete)
        if result == "doc_not_found":
            raise not_found_error("Document not found")
        if result == "rendition_not_found":
            raise not_found_error("Rendition not found")
        assert isinstance(result, DeleteRenditionResponse)
        return JSONResponse(result.model_dump())

    @v1_endpoint
    async def ingest_personal(request: Request) -> JSONResponse:
        kb, kb_settings, kb_svc = resolve_personal_kb_context(ctx, request)
        req, payload, raw_effort = await _parse_ingest_request(ctx, request)
        effort = _validate_effort(kb_svc, kb_settings, raw_effort)

        ingest_kwargs: dict[str, Any] = {
            "source": "api",
            "expand_max": req.expand_max,
            "extraction_mode": req.extraction_mode,
        }
        if req.full_content:
            ingest_kwargs["full_content"] = True
        if effort is not None:
            ingest_kwargs["effort"] = effort
        if req.format is not None and req.format.strip():
            ingest_kwargs["format"] = req.format.strip()

        def _ingest_with_renditions() -> tuple[Any, list[dict[str, str]]]:
            report = kb_svc.ingest(payload, **ingest_kwargs)
            return report, _ingest_renditions(kb, kb_settings, kb_svc, report, req, effort)

        report, renditions = await ctx.run_expensive(_ingest_with_renditions)
        rep_dict = report_to_dict(report)
        rep_dict["kb_number"] = kb_number_str(kb)
        rep_dict["renditions"] = renditions
        resp = IngestResponse.model_validate(rep_dict)
        return JSONResponse(resp.model_dump())

    @v1_endpoint
    async def ingest_personal_stream(request: Request) -> StreamingResponse:
        kb, kb_settings, kb_svc = resolve_personal_kb_context(ctx, request)
        req, payload, raw_effort = await _parse_ingest_request(ctx, request)
        effort = _validate_effort(kb_svc, kb_settings, raw_effort)

        if ctx.active_expensive_jobs >= 4:
            raise V1Error(429, "server_busy", "Server is busy; retry later")
        ctx.active_expensive_jobs += 1
        loop = asyncio.get_running_loop()
        events: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=_PROGRESS_QUEUE_SIZE)

        def _enqueue(event: dict[str, Any]) -> None:
            if not events.full():
                events.put_nowait(event)

        def on_progress(message: Any) -> None:
            try:
                if isinstance(message, dict):
                    loop.call_soon_threadsafe(_enqueue, message)
                else:
                    loop.call_soon_threadsafe(_enqueue, {"stage": "work", "msg": str(message)})
            except RuntimeError:
                pass

        def _run() -> Any:
            set_progress_callback(on_progress)
            try:
                ingest_kwargs: dict[str, Any] = {
                    "source": "api",
                    "expand_max": req.expand_max,
                    "extraction_mode": req.extraction_mode,
                }
                if req.full_content:
                    ingest_kwargs["full_content"] = True
                if effort is not None:
                    ingest_kwargs["effort"] = effort
                if req.format is not None and req.format.strip():
                    ingest_kwargs["format"] = req.format.strip()
                report = kb_svc.ingest(payload, **ingest_kwargs)
                if req.renditions and req.extraction_mode == "provider":
                    on_progress({"stage": "renditions", "msg": f"renditions: {', '.join(req.renditions)}"})
                return report, _ingest_renditions(kb, kb_settings, kb_svc, report, req, effort)
            finally:
                set_progress_callback(None)

        def _release(_task: asyncio.Task[Any] | None = None) -> None:
            ctx.active_expensive_jobs = max(0, ctx.active_expensive_jobs - 1)

        try:
            task = asyncio.create_task(asyncio.to_thread(_run))
        except BaseException:
            _release()
            raise
        task.add_done_callback(_release)

        async def stream() -> AsyncIterator[bytes]:
            try:
                while True:
                    if task.done() and events.empty():
                        await asyncio.sleep(0.01)
                        if events.empty():
                            break
                    try:
                        event = await asyncio.wait_for(events.get(), timeout=0.1)
                    except asyncio.TimeoutError:
                        continue
                    yield (json.dumps(event, ensure_ascii=False) + "\n").encode("utf-8")
                try:
                    report, renditions = task.result()
                    rep_dict = report_to_dict(report)
                    rep_dict["kb_number"] = kb_number_str(kb)
                    rep_dict["renditions"] = renditions
                    result: dict[str, Any] = IngestResponse.model_validate(rep_dict).model_dump()
                except policy.ProviderPolicyError as exc:
                    result = {"ok": False, "error": exc.code, "remedy": exc.remedy or ""}
                except Exception as exc:  # noqa: BLE001
                    result = {"ok": False, "error": f"ingest failed: {scrub_exception(exc)}"}
                yield (json.dumps({"done": True, "result": result}, ensure_ascii=False) + "\n").encode("utf-8")
            finally:
                if task.done():
                    try:
                        task.exception()
                    except (asyncio.CancelledError, Exception):  # noqa: BLE001
                        pass

        return StreamingResponse(stream(), media_type="application/x-ndjson")

    return {
        "list_documents": list_documents,
        "get_document": get_document,
        "get_document_raw": get_document_raw,
        "export_document": export_document,
        "update_document": update_document,
        "list_document_renditions": list_document_renditions,
        "add_document_rendition": add_document_rendition,
        "delete_document_rendition": delete_document_rendition,
        "delete_document": delete_document,
        "ingest_personal": ingest_personal,
        "ingest_personal_stream": ingest_personal_stream,
    }
