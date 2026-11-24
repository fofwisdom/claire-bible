"""Legacy `/api/client-extraction/*` BFF routes extracted from `claire.api.server`."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from ...core.config import Settings
from ...ingest.service import IngestService
from ...store import db as dbm
from ...store.knowledge_base import KnowledgeBaseInfo, KnowledgeBaseManager
from ..principal import request_principal
from . import read_json_object


def _parse_optional_int(raw: str | None, name: str) -> int | None:
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"{name} must be an integer") from exc


def _parse_bool_flag(raw: Any, default: bool = True) -> bool | None:
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
    return None


async def _optional_json_object(request: Request) -> dict[str, Any]:
    ctype = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if not ctype:
        raw_bytes = await request.body()
        if not raw_bytes:
            return {}
        raise HTTPException(status_code=400, detail="content-type must be application/json")
    return await read_json_object(request)


def build_client_extraction_routes(
    *,
    get_settings_fn: Callable[[], Settings],
    svc: Any,
    theme_mgr: KnowledgeBaseManager,
    get_personal_kb_ctx: Callable[[Request], tuple[KnowledgeBaseInfo, Settings, IngestService]],
) -> list[Route]:
    """Build `/api/client-extraction/*` Starlette routes."""

    def _reject_foreign_kb_param(request: Request, personal_kb: KnowledgeBaseInfo, body: dict[str, Any] | None = None) -> None:
        raw_ref = None
        if body is not None:
            raw_ref = body.get("kb") or body.get("kb_id") or body.get("theme")
        if raw_ref is None:
            raw_ref = request.query_params.get("kb") or request.query_params.get("kb_id") or request.query_params.get("theme")
        if raw_ref is None or not str(raw_ref).strip():
            return
        ref_str = str(raw_ref).strip()
        if ref_str.lower() == "personal" or ref_str == str(personal_kb.id):
            return
        target = None
        try:
            target = theme_mgr.get_kb_by_reference(ref_str)
        except Exception:
            target = None
        if target is None:
            try:
                target = theme_mgr.get_kb(int(ref_str))
            except Exception:
                target = None
        if target is None or int(target.id) != int(personal_kb.id):
            if target is not None and not target.is_shared:
                raise HTTPException(status_code=404, detail="theme not found")
            raise HTTPException(status_code=403, detail="kb_ingest_by_contribution")

    async def client_extraction_stage_route(request: Request) -> JSONResponse:
        from ...ingest import client_extraction as ce

        theme, theme_settings, _ = get_personal_kb_ctx(request)
        principal = request_principal(request)
        assert principal.account_id is not None
        account_id = int(principal.account_id)
        ctype = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        root_settings = get_settings_fn()

        raw_bytes: bytes | None = None
        filename: str | None = None
        content_base64: str | None = None
        document_id: str | None = None
        url_or_text = ""
        title: Any = None
        source_type: Any = None
        full_content = True
        force = False
        refetch = False

        if ctype == "multipart/form-data":
            form = await request.form()
            kb_val = form.get("kb") or form.get("kb_id") or form.get("theme")
            _reject_foreign_kb_param(request, theme, {"kb": kb_val} if kb_val is not None else None)
            uploaded = form.get("file")
            if uploaded is None or not hasattr(uploaded, "read"):
                return JSONResponse({"ok": False, "error": "file_required", "message": "multipart form field 'file' is required."}, status_code=400)
            uploaded_file = cast(Any, uploaded)
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = await uploaded_file.read(65536)
                if not chunk:
                    break
                total += len(chunk)
                if total > ce.MAX_STAGE_FILE_BYTES:
                    return JSONResponse({"ok": False, "error": "file_too_large", "message": f"File size exceeds limit of {ce.MAX_STAGE_FILE_BYTES} bytes."}, status_code=413)
                chunks.append(chunk)
            raw_bytes = b"".join(chunks)
            filename = str(getattr(uploaded, "filename", None) or form.get("filename") or "").strip()
            title = form.get("title")
            source_type = form.get("source_type")
            parsed_fc = _parse_bool_flag(form.get("full_content"), default=True)
            if parsed_fc is None:
                return JSONResponse({"ok": False, "error": "invalid_full_content", "message": "full_content must be a boolean."}, status_code=400)
            full_content = parsed_fc
            parsed_force = _parse_bool_flag(form.get("force"), default=False)
            if parsed_force is None:
                return JSONResponse({"ok": False, "error": "invalid_force", "message": "force must be a boolean."}, status_code=400)
            force = parsed_force
        else:
            body = await read_json_object(request)
            _reject_foreign_kb_param(request, theme, body)
            title = body.get("title")
            source_type = body.get("source_type")
            full_content_raw = body.get("full_content", True)
            if not isinstance(full_content_raw, bool):
                return JSONResponse({"ok": False, "error": "invalid_full_content", "message": "full_content must be a boolean."}, status_code=400)
            full_content = full_content_raw
            force_raw = body.get("force", False)
            if not isinstance(force_raw, bool):
                return JSONResponse({"ok": False, "error": "invalid_force", "message": "force must be a boolean."}, status_code=400)
            force = force_raw
            refetch_raw = body.get("refetch", False)
            if not isinstance(refetch_raw, bool):
                return JSONResponse({"ok": False, "error": "invalid_refetch", "message": "refetch must be a boolean."}, status_code=400)
            refetch = refetch_raw
            raw_doc_id = str(body.get("document_id") or "").strip()
            if raw_doc_id:
                document_id = raw_doc_id
            elif "content_base64" in body or "filename" in body:
                filename = str(body.get("filename") or "").strip()
                content_base64 = str(body.get("content_base64") or "")
            else:
                url_or_text = str(body.get("url_or_text") or body.get("url") or body.get("text") or "").strip()
            if refetch and not document_id:
                return JSONResponse({"ok": False, "error": "invalid_refetch", "message": "refetch=True requires document_id."}, status_code=400)

        def _work() -> dict[str, Any]:
            conn = dbm.connect_existing(theme_settings.db_file)
            try:
                common_kwargs: dict[str, Any] = {
                    "kb_id": theme.id,
                    "account_id": account_id,
                    "title": str(title) if title is not None else None,
                    "source_type": str(source_type) if source_type is not None else None,
                    "full_content": full_content,
                    "settings": theme_settings,
                    "data_dir": Path(root_settings.data_dir) if getattr(root_settings, "data_dir", None) else None,
                }
                if document_id is not None:
                    return ce.restage_existing_source(conn, document_id=document_id, refetch=refetch, **common_kwargs)
                if raw_bytes is not None:
                    return ce.stage_file_bytes(conn, raw_bytes=raw_bytes, filename=filename or "", force=force, **common_kwargs)
                if content_base64 is not None:
                    return ce.stage_file_base64(conn, filename=filename or "", content_base64=content_base64, force=force, **common_kwargs)
                return ce.stage_source(conn, url_or_text=url_or_text, force=force, **common_kwargs)
            finally:
                conn.close()

        try:
            res = await asyncio.to_thread(_work)
            return JSONResponse(res, status_code=200)
        except ce.ClientExtractionError as exc:
            return JSONResponse(exc.to_response_dict(), status_code=exc.status_code)

    async def client_extraction_list_route(request: Request) -> JSONResponse:
        from ...ingest import client_extraction as ce

        theme, theme_settings, _ = get_personal_kb_ctx(request)
        _reject_foreign_kb_param(request, theme)
        principal = request_principal(request)
        assert principal.account_id is not None
        account_id = int(principal.account_id)
        status_filter = request.query_params.get("status", "pending")
        if status_filter == "all":
            status_filter = None
        limit_val = _parse_optional_int(request.query_params.get("limit"), "limit")
        limit = 50 if limit_val is None else limit_val

        def _work() -> dict[str, Any]:
            conn = dbm.connect_existing(theme_settings.db_file, readonly=True)
            try:
                return ce.list_staged_sources(conn, kb_id=theme.id, account_id=account_id, status=status_filter, limit=limit)
            finally:
                conn.close()

        try:
            res = await asyncio.to_thread(_work)
            return JSONResponse(res, status_code=200)
        except ce.ClientExtractionError as exc:
            return JSONResponse(exc.to_response_dict(), status_code=exc.status_code)

    async def client_extraction_claim_route(request: Request) -> JSONResponse:
        from ...ingest import client_extraction as ce

        theme, theme_settings, _ = get_personal_kb_ctx(request)
        body = await _optional_json_object(request)
        _reject_foreign_kb_param(request, theme, body)
        principal = request_principal(request)
        assert principal.account_id is not None
        account_id = int(principal.account_id)
        doc_id = request.path_params.get("document_id", "")
        client_id = body.get("client_id")
        lease_raw = body.get("lease_seconds", dbm.DEFAULT_STAGED_CLAIM_TTL_SECONDS)
        if isinstance(lease_raw, bool) or not isinstance(lease_raw, (int, float)):
            return JSONResponse({"ok": False, "error": "invalid_lease_seconds", "message": "lease_seconds must be a number."}, status_code=400)
        force_raw = body.get("force", False)
        if not isinstance(force_raw, bool):
            return JSONResponse({"ok": False, "error": "invalid_force", "message": "force must be a boolean."}, status_code=400)

        def _work() -> dict[str, Any]:
            conn = dbm.connect_existing(theme_settings.db_file)
            try:
                return ce.claim_staged_source(conn, kb_id=theme.id, account_id=account_id, document_id=doc_id, client_id=str(client_id) if client_id is not None else None, lease_seconds=lease_raw, force=force_raw)
            finally:
                conn.close()

        try:
            res = await asyncio.to_thread(_work)
            return JSONResponse(res, status_code=200)
        except ce.ClientExtractionError as exc:
            return JSONResponse(exc.to_response_dict(), status_code=exc.status_code)

    async def client_extraction_renew_route(request: Request) -> JSONResponse:
        from ...ingest import client_extraction as ce

        theme, theme_settings, _ = get_personal_kb_ctx(request)
        body = await read_json_object(request)
        _reject_foreign_kb_param(request, theme, body)
        principal = request_principal(request)
        assert principal.account_id is not None
        account_id = int(principal.account_id)
        doc_id = request.path_params.get("document_id", "")
        claim_token = str(body.get("claim_token") or "")
        lease_raw = body.get("lease_seconds", dbm.DEFAULT_STAGED_CLAIM_TTL_SECONDS)
        if isinstance(lease_raw, bool) or not isinstance(lease_raw, (int, float)):
            return JSONResponse({"ok": False, "error": "invalid_lease_seconds", "message": "lease_seconds must be a number."}, status_code=400)

        def _work() -> dict[str, Any]:
            conn = dbm.connect_existing(theme_settings.db_file)
            try:
                return ce.renew_staged_claim(conn, kb_id=theme.id, account_id=account_id, document_id=doc_id, claim_token=claim_token, lease_seconds=lease_raw)
            finally:
                conn.close()

        try:
            res = await asyncio.to_thread(_work)
            return JSONResponse(res, status_code=200)
        except ce.ClientExtractionError as exc:
            return JSONResponse(exc.to_response_dict(), status_code=exc.status_code)

    async def client_extraction_spec_route(request: Request) -> JSONResponse:
        from ...ingest import client_extraction as ce

        del request
        return JSONResponse(ce.get_extraction_spec(), status_code=200)

    async def client_extraction_get_source_route(request: Request) -> JSONResponse:
        from ...ingest import client_extraction as ce

        theme, theme_settings, _ = get_personal_kb_ctx(request)
        _reject_foreign_kb_param(request, theme)
        principal = request_principal(request)
        assert principal.account_id is not None
        account_id = int(principal.account_id)
        doc_id = request.path_params.get("document_id", "")
        claim_token = request.query_params.get("claim_token")
        offset = _parse_optional_int(request.query_params.get("offset"), "offset") or 0
        limit = _parse_optional_int(request.query_params.get("limit"), "limit")
        line_offset = _parse_optional_int(request.query_params.get("line_offset"), "line_offset")
        line_limit = _parse_optional_int(request.query_params.get("line_limit"), "line_limit")
        fc_param = request.query_params.get("full_content", "true").strip().lower()
        full_content = fc_param not in ("0", "false", "no")

        def _work() -> dict[str, Any]:
            conn = dbm.connect_existing(theme_settings.db_file, readonly=True)
            try:
                return ce.get_staged_source(
                    conn,
                    kb_id=theme.id,
                    account_id=account_id,
                    document_id=doc_id,
                    claim_token=claim_token,
                    offset=offset,
                    limit=limit,
                    full_content=full_content,
                    line_offset=line_offset,
                    line_limit=line_limit,
                )
            finally:
                conn.close()

        try:
            res = await asyncio.to_thread(_work)
            return JSONResponse(res, status_code=200)
        except ce.ClientExtractionError as exc:
            return JSONResponse(exc.to_response_dict(), status_code=exc.status_code)

    async def client_extraction_submit_route(request: Request) -> JSONResponse:
        from ...ingest import client_extraction as ce

        theme, theme_settings, theme_svc = get_personal_kb_ctx(request)
        body = await read_json_object(request)
        _reject_foreign_kb_param(request, theme, body)
        principal = request_principal(request)
        assert principal.account_id is not None
        account_id = int(principal.account_id)
        doc_id = request.path_params.get("document_id", "")
        claim_token = str(body.get("claim_token") or "")
        expected_hash = str(body.get("expected_content_hash") or body.get("content_hash") or "")
        dry_run_raw = body.get("dry_run", False)
        if not isinstance(dry_run_raw, bool):
            return JSONResponse({"ok": False, "error": "invalid_dry_run", "message": "dry_run must be a boolean."}, status_code=400)
        if "payload" in body:
            raw_payload = body["payload"]
        else:
            raw_payload = {k: v for k, v in body.items() if k not in ("claim_token", "expected_content_hash", "content_hash", "dry_run", "kb", "kb_id", "theme")}
        if not isinstance(raw_payload, dict):
            return JSONResponse({"ok": False, "error": "schema_validation_error", "message": "payload must be a JSON object."}, status_code=422)
        root_settings = get_settings_fn()

        def _work() -> dict[str, Any]:
            return ce.submit_client_extraction(
                theme_settings.db_file,
                kb_id=theme.id,
                account_id=account_id,
                document_id=doc_id,
                claim_token=claim_token,
                expected_content_hash=expected_hash,
                payload=raw_payload,
                dry_run=dry_run_raw,
                theme_svc=theme_svc,
                kb_settings=theme_settings,
                root_settings=root_settings,
                root_provider=getattr(svc, "provider", None),
                credential_id=principal.credential_id,
                credential_kind=principal.channel,
            )

        try:
            res = await asyncio.to_thread(_work)
            status = 200 if res.get("ok") else 422
            return JSONResponse(res, status_code=status)
        except ce.ClientExtractionError as exc:
            return JSONResponse(exc.to_response_dict(), status_code=exc.status_code)

    return [
        Route("/api/client-extraction/stage", client_extraction_stage_route, methods=["POST"]),
        Route("/api/client-extraction/sources", client_extraction_list_route, methods=["GET", "HEAD"]),
        Route("/api/client-extraction/sources/{document_id}/claim", client_extraction_claim_route, methods=["POST"]),
        Route("/api/client-extraction/sources/{document_id}/renew", client_extraction_renew_route, methods=["POST"]),
        Route("/api/client-extraction/spec", client_extraction_spec_route, methods=["GET", "HEAD"]),
        Route("/api/client-extraction/sources/{document_id}", client_extraction_get_source_route, methods=["GET", "HEAD"]),
        Route("/api/client-extraction/sources/{document_id}/submit", client_extraction_submit_route, methods=["POST"]),
    ]
