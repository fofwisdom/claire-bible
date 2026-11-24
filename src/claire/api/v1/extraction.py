"""Client-driven extraction endpoints for `/api/v1/extraction/*` (CT4, AED9)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from starlette.datastructures import UploadFile
from starlette.requests import Request
from starlette.responses import JSONResponse

from ...ingest import client_extraction as ce
from ...store import db as dbm
from ._context import (
    V1Context,
    kb_number_str,
    read_optional_v1_json_dict,
    read_v1_json_dict,
    reject_foreign_kb_input,
    resolve_personal_kb_context,
)
from .errors import V1Error, v1_endpoint
from .pagination import decode_number_cursor, encode_number_cursor, paginated_payload, parse_limit
from .schemas import (
    ExtractionClaimRequest,
    ExtractionClaimResponse,
    ExtractionRenewRequest,
    ExtractionSourceDetailResponse,
    ExtractionSourceListItem,
    ExtractionSourceListResponse,
    ExtractionSpecResponse,
    ExtractionStageRequest,
    ExtractionStageResponse,
    ExtractionSubmitRequest,
    ExtractionSubmitResponse,
)


def _extraction_v1_error(exc: ce.ClientExtractionError) -> V1Error:
    details: dict[str, Any] = {}
    if exc.errors:
        details["errors"] = exc.errors
    if exc.metadata:
        details.update(exc.metadata)
    return V1Error(exc.status_code, exc.code, exc.message, details=details or None)


def _parse_bool_flag(raw: Any, *, default: bool, name: str) -> bool:
    if raw is None:
        return default
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        low = raw.strip().lower()
        if low in ("1", "true", "yes", "on"):
            return True
        if low in ("0", "false", "no", "off"):
            return False
    raise V1Error(400, f"invalid_{name}", f"{name} must be a boolean")


def _parse_optional_int(raw: str | None, name: str) -> int | None:
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except ValueError as exc:
        raise V1Error(400, f"invalid_{name}", f"{name} must be an integer") from exc


def build_extraction_handlers(ctx: V1Context) -> dict[str, Any]:
    """Build Starlette handlers for v1 client-extraction endpoints."""

    @v1_endpoint
    async def get_extraction_spec(request: Request) -> JSONResponse:
        del request
        spec = ce.get_extraction_spec()
        payload = ExtractionSpecResponse.model_validate(spec)
        return JSONResponse(payload.model_dump())

    @v1_endpoint
    async def stage_source(request: Request) -> JSONResponse:
        kb, kb_settings, _ = resolve_personal_kb_context(ctx, request)
        principal = request.state.claire_principal
        assert principal.account_id is not None
        ctype = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()

        raw_bytes: bytes | None = None
        filename: str | None = None
        content_base64: str | None = None
        document_id: str | None = None
        url_or_text = ""
        title: str | None = None
        source_type: str | None = None
        full_content = True
        force = False
        refetch = False

        if ctype == "multipart/form-data":
            form = await request.form()
            kb_val = form.get("kb") or form.get("kb_id") or form.get("theme")
            reject_foreign_kb_input(
                ctx,
                request,
                kb,
                {"kb": kb_val, **({"theme": form.get("theme")} if form.get("theme") is not None else {})}
                if kb_val is not None
                else None,
            )
            uploaded = form.get("file")
            if not isinstance(uploaded, UploadFile):
                raise V1Error(400, "file_required", "Multipart form field 'file' is required")
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = await uploaded.read(65536)
                if not chunk:
                    break
                total += len(chunk)
                if total > ce.MAX_STAGE_FILE_BYTES:
                    raise V1Error(413, "file_too_large", f"File size exceeds limit of {ce.MAX_STAGE_FILE_BYTES} bytes")
                chunks.append(chunk)
            raw_bytes = b"".join(chunks)
            filename = str(getattr(uploaded, "filename", None) or form.get("filename") or "").strip()
            raw_title = form.get("title")
            raw_stype = form.get("source_type")
            title = str(raw_title) if raw_title is not None else None
            source_type = str(raw_stype) if raw_stype is not None else None
            full_content = _parse_bool_flag(form.get("full_content"), default=True, name="full_content")
            force = _parse_bool_flag(form.get("force"), default=False, name="force")
        else:
            raw_body = await read_v1_json_dict(request)
            reject_foreign_kb_input(ctx, request, kb, raw_body)
            req = ExtractionStageRequest.model_validate(raw_body)
            title = req.title
            source_type = req.source_type
            full_content = req.full_content
            force = req.force
            refetch = req.refetch
            raw_doc_id = (req.document_id or "").strip()
            if raw_doc_id:
                document_id = raw_doc_id
            elif req.content_base64 is not None or req.filename is not None:
                filename = (req.filename or "").strip()
                content_base64 = req.content_base64 or ""
            else:
                url_or_text = (req.url_or_text or req.url or req.text or "").strip()
            if refetch and not document_id:
                raise V1Error(400, "invalid_refetch", "refetch=True requires document_id")

        account_id = int(principal.account_id)
        data_dir = Path(ctx.settings.data_dir) if getattr(ctx.settings, "data_dir", None) else None

        def _work() -> ExtractionStageResponse:
            conn = dbm.connect_existing(kb_settings.db_file)
            try:
                common_kwargs: dict[str, Any] = {
                    "kb_id": kb.id,
                    "account_id": account_id,
                    "title": title,
                    "source_type": source_type,
                    "full_content": full_content,
                    "settings": kb_settings,
                    "data_dir": data_dir,
                }
                if document_id is not None:
                    res = ce.restage_existing_source(conn, document_id=document_id, refetch=refetch, **common_kwargs)
                elif raw_bytes is not None:
                    res = ce.stage_file_bytes(conn, raw_bytes=raw_bytes, filename=filename or "", force=force, **common_kwargs)
                elif content_base64 is not None:
                    res = ce.stage_file_base64(conn, filename=filename or "", content_base64=content_base64, force=force, **common_kwargs)
                else:
                    res = ce.stage_source(conn, url_or_text=url_or_text, force=force, **common_kwargs)
                return ExtractionStageResponse(
                    ok=bool(res.get("ok", True)),
                    document_id=str(res["document_id"]),
                    kb_number=kb_number_str(kb),
                    title=res.get("title"),
                    source_type=res.get("source_type"),
                    extraction_status=str(res.get("extraction_status") or "pending"),
                    content_hash=res.get("content_hash"),
                    raw_chars=int(res.get("raw_chars") or 0),
                    full_content=bool(res.get("full_content", True)),
                    staged=bool(res.get("staged", True)),
                    duplicate=bool(res.get("duplicate", False)),
                    restaged=bool(res.get("restaged", False)),
                    message=res.get("message"),
                )
            except ce.ClientExtractionError as exc:
                raise _extraction_v1_error(exc) from exc
            finally:
                conn.close()

        payload = await asyncio.to_thread(_work)
        return JSONResponse(payload.model_dump(exclude_none=True), status_code=200)

    @v1_endpoint
    async def list_sources(request: Request) -> JSONResponse:
        kb, kb_settings, _ = resolve_personal_kb_context(ctx, request)
        reject_foreign_kb_input(ctx, request, kb)
        principal = request.state.claire_principal
        assert principal.account_id is not None
        params = request.query_params
        status_filter: str | None = params.get("status", "pending")
        if status_filter == "all":
            status_filter = None
        try:
            limit = parse_limit(params.get("limit"), default=50, minimum=1, maximum=200)
        except ValueError as exc:
            raise V1Error(400, "invalid_limit", "limit must be an integer between 1 and 200") from exc
        raw_cursor = params.get("cursor")
        cursor_idx: int | None = None
        if raw_cursor:
            try:
                cursor_idx = decode_number_cursor(raw_cursor)
            except ValueError as exc:
                raise V1Error(400, "invalid_cursor", "Invalid pagination cursor") from exc
        account_id = int(principal.account_id)

        def _work() -> ExtractionSourceListResponse:
            conn = dbm.connect_existing(kb_settings.db_file, readonly=True)
            try:
                res = ce.list_staged_sources(conn, kb_id=kb.id, account_id=account_id, status=status_filter, limit=500)
                all_items = [
                    ExtractionSourceListItem(
                        document_id=str(src["document_id"]),
                        title=src.get("title"),
                        url=src.get("url"),
                        source_type=src.get("source_type"),
                        extraction_status=str(src.get("extraction_status") or "pending"),
                        content_hash=src.get("content_hash"),
                        raw_chars=int(src.get("raw_chars") or 0),
                        fetched_at=float(src["fetched_at"]) if src.get("fetched_at") is not None else None,
                        claimed_by_client_id=src.get("claimed_by_client_id"),
                        lease_expires_at=float(src["lease_expires_at"]) if src.get("lease_expires_at") is not None else None,
                    )
                    for src in (res.get("sources") or [])
                ]
                total = len(all_items)
                start = cursor_idx if cursor_idx is not None else 0
                page_items = all_items[start : start + limit]
                next_cursor = encode_number_cursor(start + limit) if (start + limit) < total else None
                return ExtractionSourceListResponse.model_validate(
                    {
                        **paginated_payload(page_items, next_cursor=next_cursor, total=total),
                        "status": status_filter,
                    }
                )
            except ce.ClientExtractionError as exc:
                raise _extraction_v1_error(exc) from exc
            finally:
                conn.close()

        payload = await asyncio.to_thread(_work)
        return JSONResponse(payload.model_dump(exclude_none=True))

    @v1_endpoint
    async def get_source(request: Request) -> JSONResponse:
        kb, kb_settings, _ = resolve_personal_kb_context(ctx, request)
        reject_foreign_kb_input(ctx, request, kb)
        principal = request.state.claire_principal
        assert principal.account_id is not None
        doc_id = str(request.path_params.get("document_id") or "")
        params = request.query_params
        claim_token = params.get("claim_token")
        offset = _parse_optional_int(params.get("offset"), "offset") or 0
        limit = _parse_optional_int(params.get("limit"), "limit")
        line_offset = _parse_optional_int(params.get("line_offset"), "line_offset")
        line_limit = _parse_optional_int(params.get("line_limit"), "line_limit")
        full_content = _parse_bool_flag(params.get("full_content"), default=True, name="full_content")
        account_id = int(principal.account_id)

        def _work() -> ExtractionSourceDetailResponse:
            conn = dbm.connect_existing(kb_settings.db_file, readonly=True)
            try:
                res = ce.get_staged_source(
                    conn,
                    kb_id=kb.id,
                    account_id=account_id,
                    document_id=doc_id,
                    claim_token=claim_token,
                    offset=offset,
                    limit=limit,
                    full_content=full_content,
                    line_offset=line_offset,
                    line_limit=line_limit,
                )
                return ExtractionSourceDetailResponse(
                    ok=bool(res.get("ok", True)),
                    document_id=str(res["document_id"]),
                    kb_number=kb_number_str(kb),
                    title=res.get("title"),
                    url=res.get("url"),
                    source_type=res.get("source_type"),
                    extraction_status=str(res.get("extraction_status") or "pending"),
                    content_hash=res.get("content_hash"),
                    raw_chars=int(res.get("raw_chars") or 0),
                    offset=int(res.get("offset") or 0),
                    returned_chars=int(res.get("returned_chars") or 0),
                    has_more=bool(res.get("has_more", False)),
                    next_offset=res.get("next_offset"),
                    line_offset=res.get("line_offset"),
                    line_limit=res.get("line_limit"),
                    total_lines=res.get("total_lines"),
                    returned_lines=res.get("returned_lines"),
                    next_line_offset=res.get("next_line_offset"),
                    nonce=str(res["nonce"]),
                    framed_content=str(res["content"]),
                )
            except ce.ClientExtractionError as exc:
                raise _extraction_v1_error(exc) from exc
            finally:
                conn.close()

        payload = await asyncio.to_thread(_work)
        return JSONResponse(payload.model_dump(exclude_none=True))

    @v1_endpoint
    async def claim_source(request: Request) -> JSONResponse:
        kb, kb_settings, _ = resolve_personal_kb_context(ctx, request)
        raw_body = await read_optional_v1_json_dict(request)
        reject_foreign_kb_input(ctx, request, kb, raw_body)
        req = ExtractionClaimRequest.model_validate(raw_body)
        principal = request.state.claire_principal
        assert principal.account_id is not None
        doc_id = str(request.path_params.get("document_id") or "")
        account_id = int(principal.account_id)

        def _work() -> ExtractionClaimResponse:
            conn = dbm.connect_existing(kb_settings.db_file)
            try:
                res = ce.claim_staged_source(
                    conn,
                    kb_id=kb.id,
                    account_id=account_id,
                    document_id=doc_id,
                    client_id=req.client_id,
                    lease_seconds=req.lease_seconds,
                    force=req.force,
                )
                return ExtractionClaimResponse(
                    ok=bool(res.get("ok", True)),
                    document_id=str(res["document_id"]),
                    kb_number=kb_number_str(kb),
                    claim_token=str(res["claim_token"]),
                    claimed_by_client_id=res.get("claimed_by_client_id"),
                    lease_expires_at=float(res["lease_expires_at"]),
                    content_hash=res.get("content_hash"),
                    expected_content_hash=res.get("expected_content_hash"),
                    extraction_status=res.get("extraction_status"),
                )
            except ce.ClientExtractionError as exc:
                raise _extraction_v1_error(exc) from exc
            finally:
                conn.close()

        payload = await asyncio.to_thread(_work)
        return JSONResponse(payload.model_dump(exclude_none=True))

    @v1_endpoint
    async def renew_source_claim(request: Request) -> JSONResponse:
        kb, kb_settings, _ = resolve_personal_kb_context(ctx, request)
        raw_body = await read_v1_json_dict(request)
        reject_foreign_kb_input(ctx, request, kb, raw_body)
        req = ExtractionRenewRequest.model_validate(raw_body)
        principal = request.state.claire_principal
        assert principal.account_id is not None
        doc_id = str(request.path_params.get("document_id") or "")
        account_id = int(principal.account_id)

        def _work() -> ExtractionClaimResponse:
            conn = dbm.connect_existing(kb_settings.db_file)
            try:
                res = ce.renew_staged_claim(
                    conn,
                    kb_id=kb.id,
                    account_id=account_id,
                    document_id=doc_id,
                    claim_token=req.claim_token,
                    lease_seconds=req.lease_seconds,
                )
                return ExtractionClaimResponse(
                    ok=bool(res.get("ok", True)),
                    document_id=str(res["document_id"]),
                    kb_number=kb_number_str(kb),
                    claim_token=str(res["claim_token"]),
                    claimed_by_client_id=res.get("claimed_by_client_id"),
                    lease_expires_at=float(res["lease_expires_at"]),
                    content_hash=res.get("content_hash"),
                    expected_content_hash=res.get("expected_content_hash"),
                    extraction_status=res.get("extraction_status"),
                )
            except ce.ClientExtractionError as exc:
                raise _extraction_v1_error(exc) from exc
            finally:
                conn.close()

        payload = await asyncio.to_thread(_work)
        return JSONResponse(payload.model_dump(exclude_none=True))

    @v1_endpoint
    async def submit_extraction(request: Request) -> JSONResponse:
        kb, kb_settings, kb_svc = resolve_personal_kb_context(ctx, request)
        raw_body = await read_v1_json_dict(request)
        reject_foreign_kb_input(ctx, request, kb, raw_body)
        req = ExtractionSubmitRequest.model_validate(raw_body)
        principal = request.state.claire_principal
        assert principal.account_id is not None
        doc_id = str(request.path_params.get("document_id") or "")
        expected_hash = (req.expected_content_hash or req.content_hash or "").strip()
        account_id = int(principal.account_id)

        def _work() -> ExtractionSubmitResponse:
            try:
                res = ce.submit_client_extraction(
                    kb_settings.db_file,
                    kb_id=kb.id,
                    account_id=account_id,
                    document_id=doc_id,
                    claim_token=req.claim_token,
                    expected_content_hash=expected_hash,
                    payload=req.payload,
                    dry_run=req.dry_run,
                    theme_svc=kb_svc,
                    kb_settings=kb_settings,
                    root_settings=ctx.settings,
                    root_provider=getattr(ctx.service, "provider", None),
                    credential_id=principal.credential_id,
                    credential_kind=principal.channel,
                )
            except ce.ClientExtractionError as exc:
                raise _extraction_v1_error(exc) from exc
            return ExtractionSubmitResponse(
                ok=bool(res.get("ok", False)),
                dry_run=bool(res.get("dry_run", False)),
                committed=bool(res.get("committed", False)),
                document_id=str(res.get("document_id") or doc_id),
                kb_number=kb_number_str(kb),
                extraction_status=res.get("extraction_status"),
                extraction_origin=res.get("extraction_origin"),
                client_id=res.get("client_id"),
                oauth_client_id=res.get("oauth_client_id"),
                model=res.get("model"),
                schema_version=res.get("schema_version"),
                expected_content_hash=res.get("expected_content_hash"),
                summary=res.get("summary"),
                entities_count=res.get("entities_count"),
                relations_count=res.get("relations_count"),
                detail_chars=res.get("detail_chars"),
                entities_created=res.get("entities_created"),
                entities_linked=res.get("entities_linked"),
                relations_added=res.get("relations_added"),
                proposals_logged=res.get("proposals_logged"),
                warnings=[str(w) for w in (res.get("warnings") or [])],
                errors=[dict(e) for e in (res.get("errors") or [])],
            )

        payload = await asyncio.to_thread(_work)
        return JSONResponse(payload.model_dump(exclude_none=True), status_code=200 if payload.ok else 422)

    return {
        "get_extraction_spec": get_extraction_spec,
        "stage_source": stage_source,
        "list_sources": list_sources,
        "get_source": get_source,
        "claim_source": claim_source,
        "renew_source_claim": renew_source_claim,
        "submit_extraction": submit_extraction,
    }
