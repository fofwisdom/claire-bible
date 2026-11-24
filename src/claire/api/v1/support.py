"""Account OAuth grant management and support bundle endpoints for `/api/v1/*`."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Literal

from starlette.requests import Request
from starlette.responses import JSONResponse

from ...ops.account_support_bundle import (
    MAX_ACTIVE_ACCOUNT_BUNDLES,
    AccountSupportBundleError,
    SupportTargetNotFound,
    create_account_support_bundle,
    delete_account_support_bundle,
    delete_server_support_bundle,
    list_account_support_bundles,
    list_server_support_bundles,
)
from ...ops.support_bundle import create_support_bundle, validate_bundle_days
from ...store import db as dbm
from ._context import V1Context, enforce_v1_auth_policy, read_optional_v1_json_dict, read_v1_json
from .errors import V1Error, not_found_error, v1_endpoint
from .pagination import decode_number_cursor, encode_number_cursor, paginated_payload, parse_limit
from .schemas import (
    CreateSupportBundleRequest,
    DeleteSupportBundleResponse,
    OAuthGrantListResponse,
    OAuthGrantSummary,
    RevokeOAuthGrantsRequest,
    RevokeOAuthGrantsResponse,
    SupportBundleDetailResponse,
    SupportBundleListResponse,
    SupportBundleSummary,
)


def _bundle_summary(record: dict[str, Any], *, scope: Literal["account", "server"]) -> SupportBundleSummary:
    return SupportBundleSummary(
        bundle_id=str(record["bundle_id"]),
        scope=scope,
        link=record.get("download_url"),
        days_covered=record.get("days_covered"),
        target_kind=record.get("target_kind"),
        target_link=record.get("target_link"),
        target_resolution_status=record.get("target_resolution_status"),
        size_bytes=record.get("size_bytes"),
        created_at=str(record["created_at"]) if record.get("created_at") is not None else None,
        expires_at=record.get("expires_at"),
    )


def build_support_handlers(ctx: V1Context) -> dict[str, Any]:
    """Build Starlette handlers for `/api/v1/me/oauth/grants` and `/api/v1/support/bundles`."""

    @v1_endpoint
    async def list_oauth_grants(request: Request) -> JSONResponse:
        principal = enforce_v1_auth_policy(request)
        if principal.account_id is None or principal.scope != "owner":
            raise not_found_error("OAuth grants not found")
        account_id = principal.account_id
        params = request.query_params
        try:
            limit = parse_limit(params.get("limit"), default=50, minimum=1, maximum=100)
        except ValueError as exc:
            raise V1Error(400, "invalid_limit", "limit must be an integer between 1 and 100") from exc
        raw_cursor = params.get("cursor")
        cursor_idx: int | None = None
        if raw_cursor:
            try:
                cursor_idx = decode_number_cursor(raw_cursor)
            except ValueError as exc:
                raise V1Error(400, "invalid_cursor", "Invalid pagination cursor") from exc

        def _list() -> OAuthGrantListResponse:
            conn = dbm.connect_existing(ctx.settings.db_file, readonly=True)
            try:
                grants = dbm.list_account_oauth_grants(conn, account_id)
            finally:
                conn.close()
            all_items = [
                OAuthGrantSummary(
                    grant_id=str(g["grant_id"]),
                    client_id=str(g["client_id"]),
                    client_name=str(g["client_name"]),
                    redirect_uris=[str(u) for u in (g.get("redirect_uris") or [])],
                    scope=str(g["scope"]),
                    granted_scope=str(g["granted_scope"]),
                    created_at=float(g["created_at"]),
                    expires_at=float(g["expires_at"]),
                    last_used_at=float(g["last_used_at"]) if g.get("last_used_at") is not None else None,
                    revoked_at=float(g["revoked_at"]) if g.get("revoked_at") is not None else None,
                )
                for g in grants
            ]
            total = len(all_items)
            start = cursor_idx if cursor_idx is not None else 0
            page_items = all_items[start : start + limit]
            next_cursor = encode_number_cursor(start + limit) if (start + limit) < total else None
            return OAuthGrantListResponse.model_validate(
                paginated_payload(page_items, next_cursor=next_cursor, total=total)
            )

        payload = await asyncio.to_thread(_list)
        return JSONResponse(payload.model_dump(exclude_none=True), headers={"Cache-Control": "no-store"})

    @v1_endpoint
    async def revoke_oauth_grants(request: Request) -> JSONResponse:
        principal = enforce_v1_auth_policy(request)
        if principal.account_id is None or principal.scope != "owner":
            raise not_found_error("OAuth grants not found")
        account_id = principal.account_id
        raw_body = await read_optional_v1_json_dict(request)
        if raw_body:
            req = RevokeOAuthGrantsRequest.model_validate(raw_body)
            grant_id = req.grant_id.strip() if req.grant_id else None
            client_id = req.client_id.strip() if req.client_id else None
            revoke_all = req.revoke_all
        else:
            grant_id = (request.query_params.get("grant_id") or "").strip() or None
            client_id = (request.query_params.get("client_id") or "").strip() or None
            raw_all = (request.query_params.get("revoke_all") or request.query_params.get("all") or "").strip().lower()
            revoke_all = raw_all in ("1", "true", "yes")
        if grant_id is None and client_id is None and not revoke_all:
            raise V1Error(400, "invalid_request", "Specify grant_id, client_id, or revoke_all=true")

        def _revoke() -> RevokeOAuthGrantsResponse:
            conn = dbm.connect_existing(ctx.settings.db_file)
            try:
                revoked = dbm.revoke_account_oauth_grants(
                    conn,
                    account_id,
                    grant_id=None if revoke_all else grant_id,
                    client_id=None if revoke_all else client_id,
                )
                return RevokeOAuthGrantsResponse(revoked=int(revoked))
            finally:
                conn.close()

        payload = await asyncio.to_thread(_revoke)
        return JSONResponse(payload.model_dump(), headers={"Cache-Control": "no-store"})

    @v1_endpoint
    async def list_support_bundles(request: Request) -> JSONResponse:
        principal = enforce_v1_auth_policy(request)
        if principal.account_id is None or principal.scope != "owner":
            raise not_found_error("Support bundles not found")
        account_id = principal.account_id
        is_admin = principal.server_role == "admin"
        params = request.query_params
        scope_filter = params.get("scope")
        if scope_filter is not None and scope_filter not in ("account", "server"):
            raise V1Error(400, "invalid_scope", "scope must be 'account' or 'server'")
        if scope_filter == "server" and not is_admin:
            raise not_found_error("Support bundles not found")
        try:
            limit = parse_limit(params.get("limit"), default=50, minimum=1, maximum=100)
        except ValueError as exc:
            raise V1Error(400, "invalid_limit", "limit must be an integer between 1 and 100") from exc
        raw_cursor = params.get("cursor")
        cursor_idx: int | None = None
        if raw_cursor:
            try:
                cursor_idx = decode_number_cursor(raw_cursor)
            except ValueError as exc:
                raise V1Error(400, "invalid_cursor", "Invalid pagination cursor") from exc

        def _list() -> SupportBundleListResponse:
            items: list[SupportBundleSummary] = []
            if scope_filter in (None, "account"):
                items.extend(
                    _bundle_summary(r, scope="account")
                    for r in list_account_support_bundles(ctx.settings, account_id=account_id)
                )
            if is_admin and scope_filter in (None, "server"):
                items.extend(
                    _bundle_summary(r, scope="server")
                    for r in list_server_support_bundles(ctx.settings)
                )
            items.sort(key=lambda item: item.created_at or "", reverse=True)
            total = len(items)
            start = cursor_idx if cursor_idx is not None else 0
            page_items = items[start : start + limit]
            next_cursor = encode_number_cursor(start + limit) if (start + limit) < total else None
            scopes: list[Literal["account", "server"]] = ["account", "server"] if is_admin else ["account"]
            return SupportBundleListResponse.model_validate(
                {
                    **paginated_payload(page_items, next_cursor=next_cursor, total=total),
                    "max_active": MAX_ACTIVE_ACCOUNT_BUNDLES,
                    "scopes": scopes,
                }
            )

        payload = await asyncio.to_thread(_list)
        return JSONResponse(payload.model_dump(exclude_none=True), headers={"Cache-Control": "no-store"})

    @v1_endpoint
    async def create_support_bundle_route(request: Request) -> JSONResponse:
        principal = enforce_v1_auth_policy(request)
        if principal.account_id is None or principal.scope != "owner":
            raise not_found_error("Support bundles not found")
        account_id = principal.account_id
        is_admin = principal.server_role == "admin"
        body = await read_v1_json(request, CreateSupportBundleRequest)
        if body.scope == "server" and not is_admin:
            raise not_found_error("Support bundles not found")
        if body.target is not None and len(body.target) > 2048:
            raise V1Error(400, "invalid_target", "target must be 2048 characters or less")
        clean_target = (body.target or "").strip() or None

        def _create() -> SupportBundleDetailResponse:
            try:
                if body.scope == "server":
                    validate_bundle_days(body.days, getattr(ctx.settings, "telemetry_retention_days", 30))
                    info = create_support_bundle(
                        ctx.settings,
                        days=body.days,
                        target=clean_target,
                        request_context={"channel": "api_v1"},
                    )
                else:
                    info = create_account_support_bundle(
                        ctx.settings,
                        account_id=account_id,
                        days=body.days,
                        target=clean_target,
                        request_context={"channel": "api_v1"},
                    )
            except SupportTargetNotFound as exc:
                raise not_found_error(str(exc)) from exc
            except AccountSupportBundleError as exc:
                raise V1Error(409, "bundle_limit_reached", str(exc)) from exc
            except ValueError as exc:
                raise V1Error(400, "invalid_days", str(exc)) from exc
            return SupportBundleDetailResponse(
                bundle=SupportBundleSummary(
                    bundle_id=info.bundle_id,
                    scope=body.scope,
                    link=info.download_url,
                    days_covered=info.days_covered,
                    target_kind=info.target_kind,
                    target_link=info.target_link,
                    target_resolution_status=info.target_resolution_status,
                    size_bytes=info.size_bytes,
                    expires_at=datetime.fromtimestamp(info.expires_at, timezone.utc).isoformat(),
                )
            )

        payload = await asyncio.to_thread(_create)
        return JSONResponse(payload.model_dump(exclude_none=True), status_code=201, headers={"Cache-Control": "no-store"})

    @v1_endpoint
    async def delete_support_bundle_route(request: Request) -> JSONResponse:
        principal = enforce_v1_auth_policy(request)
        if principal.account_id is None or principal.scope != "owner":
            raise not_found_error("Support bundle not found")
        account_id = principal.account_id
        is_admin = principal.server_role == "admin"
        bundle_id = str(request.path_params.get("bundle_id") or "").strip()
        if not bundle_id:
            raise not_found_error("Support bundle not found")

        def _delete() -> DeleteSupportBundleResponse:
            deleted = delete_account_support_bundle(ctx.settings, account_id=account_id, bundle_id=bundle_id)
            if not deleted and is_admin:
                deleted = delete_server_support_bundle(ctx.settings, bundle_id=bundle_id)
            if not deleted:
                raise not_found_error("Support bundle not found")
            return DeleteSupportBundleResponse(bundle_id=bundle_id, deleted=True)

        payload = await asyncio.to_thread(_delete)
        return JSONResponse(payload.model_dump())

    return {
        "list_oauth_grants": list_oauth_grants,
        "revoke_oauth_grants": revoke_oauth_grants,
        "list_support_bundles": list_support_bundles,
        "create_support_bundle": create_support_bundle_route,
        "delete_support_bundle": delete_support_bundle_route,
    }
