"""Scalar API reference and OpenAPI 3.1 endpoint routes."""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.routing import Route

from ...core.ports import get_web_ui_renderer
from ...i18n import get_locale
from ..principal import request_principal
from ..security import WebRuntimeConfig
from ..v1 import (
    build_account_openapi_spec,
    build_admin_openapi_spec,
    build_v1_openapi_json,
    build_v1_openapi_yaml,
    render_reference_html,
)

_PRIVATE_NO_STORE_HEADERS = {
    "Cache-Control": "private, no-store",
    "Vary": "Cookie, Authorization",
    "Referrer-Policy": "same-origin",
}


def build_reference_routes(runtime_cfg: WebRuntimeConfig) -> list[Route]:
    """Build `/reference` and `/openapi*` Starlette routes."""

    async def docs_ui_route(request: Request) -> Response:
        principal = request_principal(request)
        base_url = str(runtime_cfg.public_origin or "").rstrip("/") or str(request.base_url).rstrip("/")
        html_doc = render_reference_html(
            auth_scope=principal.scope,
            is_admin=(principal.server_role == "admin"),
            locale=get_locale(),
            base_url=base_url,
        )
        return HTMLResponse(
            get_web_ui_renderer().inject_favicon_head_tags(html_doc),
            headers=_PRIVATE_NO_STORE_HEADERS,
        )

    async def openapi_v1_json_route(_request: Request) -> Response:
        return Response(
            build_v1_openapi_json().encode("utf-8"),
            media_type="application/json",
            headers={"Content-Type": "application/json; charset=utf-8"},
        )

    async def openapi_account_json_route(_request: Request) -> Response:
        return JSONResponse(
            build_account_openapi_spec(),
            headers=_PRIVATE_NO_STORE_HEADERS,
        )

    async def openapi_admin_json_route(_request: Request) -> Response:
        return JSONResponse(
            build_admin_openapi_spec(),
            headers=_PRIVATE_NO_STORE_HEADERS,
        )

    async def openapi_yaml_route(_request: Request) -> Response:
        return Response(
            build_v1_openapi_yaml().encode("utf-8"),
            media_type="application/yaml",
            headers={"Content-Type": "application/yaml; charset=utf-8"},
        )

    return [
        Route("/reference", docs_ui_route, methods=["GET"]),
        Route("/openapi/v1.json", openapi_v1_json_route, methods=["GET"]),
        Route("/openapi/account.json", openapi_account_json_route, methods=["GET"]),
        Route("/openapi/admin.json", openapi_admin_json_route, methods=["GET"]),
        Route("/openapi.yaml", openapi_yaml_route, methods=["GET"]),
    ]
