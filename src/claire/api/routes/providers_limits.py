"""Admin `/providers*`, `/limits`, and `/ingest-limits` routes extracted from `claire.api.server`."""

from __future__ import annotations

from collections.abc import Callable
import logging
from pathlib import Path
from typing import Any

from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from ...accounts import provider_policy as policy, server_tier
from ...accounts.languages import with_server_languages
from ...accounts.provider_view import describe_server_providers
from ...core.config import Settings, get_settings
from ...ingest.service import IngestServicePool
from ...store import db as dbm
from ..accounts_routes.common import same_origin
from ..principal import request_principal
from ..security import request_auth_scope
from . import log_operation_failure, read_json_object, with_deprecation_headers

log = logging.getLogger("claire.api")


def build_providers_limits_routes(
    *,
    get_settings_fn: Callable[[], Settings],
    update_settings_fn: Callable[[Settings], None],
    service_pool: IngestServicePool,
) -> list[Route]:
    """Build `/providers*`, `/limits`, and `/ingest-limits` Starlette routes."""

    def _load_server_tier_payload(curr_settings: Settings) -> dict[str, Any]:
        """Read current ServerTier configuration for /providers responses."""
        try:
            db_path = Path(curr_settings.db_file)
            if db_path.is_file():
                conn = dbm.connect_existing(db_path, readonly=True)
                try:
                    raw = server_tier.read_stored_server_tier(conn)
                    return {
                        "configured": raw is not None,
                        "tier": server_tier.load_server_tier(conn).to_dict(),
                    }
                finally:
                    conn.close()
        except Exception as exc:
            log.debug("providers server_tier read skipped: %s", exc)
        return {"configured": False, "tier": policy.ServerTier().to_dict()}

    def _add_provider_view(curr_settings: Settings, cfg: dict[str, Any], pm: Any) -> None:
        current = with_server_languages(curr_settings.model_copy(update=pm.get_settings_dict()))
        cfg["effective_provider"] = current.effective_provider
        cfg["effective_stt_provider"] = current.effective_stt_provider
        cfg["effective"] = describe_server_providers(current)
        tier_payload = _load_server_tier_payload(curr_settings)
        cfg["server_tier"] = tier_payload["tier"]
        cfg["server_tier_configured"] = tier_payload["configured"]

    async def providers_get_route(request: Request) -> JSONResponse:
        scope = request_auth_scope(request)
        if scope != "owner":
            raise HTTPException(status_code=403, detail="지식 관리자(owner) 권한이 필요합니다.")
        from ...core.provider_manager import get_provider_manager

        curr_settings = get_settings_fn()
        pm = get_provider_manager(curr_settings.data_dir)
        cfg = pm.get_sanitized_config()
        _add_provider_view(curr_settings, cfg, pm)
        return JSONResponse(cfg)

    async def providers_update_route(request: Request) -> JSONResponse:
        scope = request_auth_scope(request)
        if scope != "owner":
            raise HTTPException(status_code=403, detail="지식 관리자(owner) 권한이 필요합니다.")
        body = await read_json_object(request)
        from ...core.provider_manager import get_provider_manager

        curr_settings = get_settings_fn()
        if str(body.get("active_provider") or "").strip().lower() == "mock" and curr_settings.is_production:
            raise HTTPException(status_code=400, detail="mock_not_allowed_in_production")
        providers_in = body.get("providers")
        if isinstance(providers_in, dict) and isinstance(providers_in.get("stt"), dict):
            providers_in["stt"] = {k: v for k, v in providers_in["stt"].items() if k in ("enabled", "language")} | {"provider": "gemini"}
        body.pop("server_tier", None)
        pm = get_provider_manager(curr_settings.data_dir)
        try:
            updated = pm.update_config(body, server_scope=True)
            get_settings.cache_clear()
            refreshed = curr_settings.model_copy(update=pm.get_settings_dict())
            update_settings_fn(refreshed)
            if hasattr(request, "app") and hasattr(request.app, "state"):
                request.app.state.settings = refreshed
            service_pool.clear(refreshed)
            _add_provider_view(refreshed, updated, pm)
            return JSONResponse({"ok": True, "config": updated})
        except ValueError as val_err:
            raise HTTPException(status_code=400, detail=str(val_err)) from val_err
        except Exception as exc:
            log_operation_failure(log, request, "provider update", exc)
            raise HTTPException(status_code=500, detail="failed to update provider configuration") from exc

    def _require_admin_browser_session(request: Request) -> None:
        principal = request_principal(request)
        if principal.channel != "cookie" or principal.server_role != "admin" or principal.scope != "owner":
            raise HTTPException(status_code=403, detail="서버 관리자 브라우저 세션이 필요합니다.")
        if not same_origin(request):
            raise HTTPException(status_code=403, detail="same-origin 요청만 허용됩니다.")

    async def providers_test_route(request: Request) -> JSONResponse:
        _require_admin_browser_session(request)
        body = await read_json_object(request)
        provider_name = str(body.get("provider") or "").strip().lower()
        if not provider_name:
            raise HTTPException(status_code=400, detail="provider is required")
        from ...core.provider_manager import get_provider_manager

        pm = get_provider_manager(get_settings_fn().data_dir)
        override = body.get("config") if isinstance(body.get("config"), dict) else None
        res = pm.test_connection(provider_name, config=override)
        return JSONResponse(res)

    async def providers_check_route(request: Request) -> JSONResponse:
        _require_admin_browser_session(request)
        body = await read_json_object(request)
        from ...core.provider_manager import get_provider_manager

        pm = get_provider_manager(get_settings_fn().data_dir)
        live = bool(body.get("live", False))
        try:
            caps = pm.check_capabilities(body, live=live)
        except ValueError as val_err:
            raise HTTPException(status_code=400, detail=str(val_err)) from val_err
        return JSONResponse(caps)

    async def limits_get_route(request: Request) -> JSONResponse:
        scope = request_auth_scope(request)
        if scope != "owner":
            raise HTTPException(status_code=403, detail="지식 관리자(owner) 권한이 필요합니다.")
        from ...core.ingest_limit_manager import get_ingest_limit_manager

        curr_settings = get_settings_fn()
        app_settings = getattr(getattr(request, "app", None), "state", None) and getattr(request.app.state, "settings", curr_settings)
        data_dir = getattr(app_settings or curr_settings, "data_dir", curr_settings.data_dir)
        ilm = get_ingest_limit_manager(data_dir)
        return JSONResponse(ilm.get_sanitized_config())

    async def limits_update_route(request: Request) -> JSONResponse:
        scope = request_auth_scope(request)
        if scope != "owner":
            raise HTTPException(status_code=403, detail="지식 관리자(owner) 권한이 필요합니다.")
        body = await read_json_object(request)
        from ...core.ingest_limit_manager import get_ingest_limit_manager

        curr_settings = get_settings_fn()
        app_settings = getattr(getattr(request, "app", None), "state", None) and getattr(request.app.state, "settings", curr_settings)
        data_dir = getattr(app_settings or curr_settings, "data_dir", curr_settings.data_dir)
        ilm = get_ingest_limit_manager(data_dir)
        try:
            updated = ilm.update_config(body)
            get_settings.cache_clear()
            refreshed = curr_settings.model_copy(update=ilm.get_settings_dict())
            update_settings_fn(refreshed)
            if hasattr(request, "app") and hasattr(request.app, "state"):
                request.app.state.settings = refreshed
            service_pool.clear(refreshed)
            return JSONResponse({"ok": True, "config": updated})
        except ValueError as val_err:
            raise HTTPException(status_code=400, detail=str(val_err)) from val_err
        except Exception as exc:
            log_operation_failure(log, request, "ingest limits update", exc)
            raise HTTPException(status_code=500, detail="failed to update ingest limits configuration") from exc

    return [
        Route("/providers", providers_get_route, methods=["GET", "HEAD"]),
        Route("/providers", providers_update_route, methods=["PATCH", "POST"]),
        Route("/providers/test", providers_test_route, methods=["POST"]),
        Route("/providers/check", providers_check_route, methods=["POST"]),
        Route("/limits", limits_get_route, methods=["GET", "HEAD"]),
        Route("/limits", limits_update_route, methods=["PATCH", "POST"]),
        Route("/ingest-limits", with_deprecation_headers(limits_get_route, "/limits"), methods=["GET", "HEAD"]),
        Route("/ingest-limits", with_deprecation_headers(limits_update_route, "/limits"), methods=["PATCH", "POST"]),
    ]
