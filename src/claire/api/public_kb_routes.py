"""Public HTML routes for Shared KBs (/kb, /kb/{number}/) and Public Themes (/themes, /explore/themes)."""

from __future__ import annotations

import json
from typing import Any

from starlette.requests import Request
from starlette.responses import RedirectResponse, Response
from starlette.routing import Route

from .. import ids, ui_chrome
from ..i18n import catalog as i18n_catalog
from .accounts_routes.admin import is_admin
from .accounts_routes.common import AccountsContext, FORM_PAGE_REFERRER_POLICY, render_template
from .principal import request_principal


def _caller_context(request: Request, ctx: AccountsContext | None = None) -> dict[str, Any]:
    principal = request_principal(request)
    is_auth = principal.account_id is not None and principal.scope in {"owner", "collaborator", "readonly"}
    admin = is_admin(principal)
    home_href = "/"
    mode = "single"
    if ctx is not None:
        try:
            from .accounts_routes.common import open_db
            from ..store import accounts as accounts_store

            conn = open_db(ctx)
            try:
                store = accounts_store.AccountStore(conn)
                mode = store.server_mode()
                if principal.account_id is not None and mode == "network":
                    acc = store.get(principal.account_id)
                    if acc.username:
                        home_href = f"/@{acc.username}/"
            finally:
                conn.close()
        except Exception:
            pass
    return {
        "is_authenticated": is_auth,
        "is_admin": admin,
        "scope": principal.scope,
        "home_href": home_href,
        "console_href": "/admin" if admin else "/settings",
        "mode": mode,
    }


def render_public_kb_page(
    request: Request,
    *,
    view: str,
    title: str,
    ref: str | None = None,
    ctx: AccountsContext | None = None,
) -> Response:
    caller = _caller_context(request, ctx)
    bootstrap = {
        "view": view,
        "ref": ref or "",
        "is_authenticated": caller["is_authenticated"],
        "is_admin": caller["is_admin"],
        "scope": caller["scope"],
        "home_href": caller["home_href"],
        "mode": caller["mode"],
    }
    bootstrap_json = json.dumps(bootstrap, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    appbar_html = ui_chrome.render_appbar_html(
        bar_id="public-bar",
        brand_id="brand-home",
        brand_href="/",
        center_id="public-bar-center",
        end_id="public-bar-end",
        ingest_enabled=False,
        home_href=caller["home_href"],
        synd_href="/syndication" if caller["is_authenticated"] else "",
        synd_hidden=not caller["is_authenticated"],
        console_href=caller["console_href"] if caller["is_authenticated"] else "/settings",
        console_icon="wrench" if caller["is_admin"] else "settings",
        console_hidden=not caller["is_authenticated"],
        show_logout=caller["is_authenticated"],
        logout_hidden=not caller["is_authenticated"],
    )
    return render_template(
        "public_kb.html",
        text={
            "page_title": title,
            "view_name": view,
        },
        markup={
            "common_appbar_html": appbar_html,
            "bootstrap_json": bootstrap_json,
        },
    )


def explore_head_markup(request: Request) -> str:
    """Return shared stylesheet and script tags plus a scoped KB catalog for ``explore_accounts.html``."""
    full = i18n_catalog.browser_catalog(scope="page")
    scoped = {
        k: v
        for k, v in full.items()
        if k.startswith(("kb.", "common.action.cancel", "common.action.close", "common.action.save"))
    }
    catalog_json = json.dumps(scoped, ensure_ascii=False, sort_keys=True).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    return (
        f'<script id="claire-i18n-catalog" type="application/json">{catalog_json}</script>\n'
        '<script src="/static/js/i18n.js" defer></script>\n'
        '<link rel="stylesheet" href="/static/css/theme.css"/>\n'
        '<link rel="stylesheet" href="/static/css/shared_ui.css"/>\n'
        '<script src="/static/js/icons.js" defer></script>\n'
        '<script src="/static/js/shared_ui.js" defer></script>\n'
        '<script src="/static/js/kb_dialogs.js" defer></script>\n'
        '<script src="/static/js/public_kb.js" defer></script>'
    )


def _wants_json_only(request: Request) -> bool:
    accept = (request.headers.get("accept") or "").lower()
    dest = (request.headers.get("sec-fetch-dest") or "").lower()
    return dest != "document" and "application/json" in accept and "text/html" not in accept


def routes(ctx: AccountsContext) -> list[Route]:
    async def kb_collection_page(request: Request) -> Response:
        if _wants_json_only(request):
            return Response(
                content=json.dumps({"ok": False, "error": "not_implemented"}),
                status_code=404,
                media_type="application/json",
            )
        return render_public_kb_page(request, view="kb_list", title="Shared Knowledge Bases", ctx=ctx)

    async def kb_detail_page(request: Request) -> Response:
        raw_num = str(request.path_params.get("kb_number", "")).strip()
        if not raw_num or not raw_num.isdigit() or int(raw_num) > ids.MAX_NUMBER:
            return Response("Not Found", status_code=404)
        # Keep non-HTML callers cleanly informed so API clients do not parse HTML as JSON.
        if _wants_json_only(request):
            return Response(
                content=json.dumps({"ok": False, "error": "not_implemented", "number": raw_num}),
                status_code=404,
                media_type="application/json",
            )
        if not request.url.path.endswith("/"):
            qs = str(request.url.query)
            loc = f"{request.url.path}/" + (f"?{qs}" if qs else "")
            return RedirectResponse(loc, status_code=308, headers={"Referrer-Policy": FORM_PAGE_REFERRER_POLICY})
        return render_public_kb_page(request, view="kb_detail", title=f"Shared KB #{raw_num}", ref=raw_num, ctx=ctx)

    async def explore_themes_page(request: Request) -> Response:
        raw_num = request.path_params.get("theme_number")
        if _wants_json_only(request):
            return Response(
                content=json.dumps({"ok": False, "error": "not_implemented"}),
                status_code=404,
                media_type="application/json",
            )
        if raw_num is not None:
            s_num = str(raw_num).strip()
            if not s_num or not s_num.isdigit() or int(s_num) > ids.MAX_NUMBER:
                return Response("Not Found", status_code=404)
            return render_public_kb_page(request, view="theme_detail", title=f"Theme #{s_num}", ref=s_num, ctx=ctx)
        return render_public_kb_page(request, view="themes_list", title="Public Themes", ctx=ctx)

    return [
        Route("/kb", kb_collection_page, methods=["GET", "HEAD"]),
        Route("/kb/{kb_number:int}", kb_detail_page, methods=["GET", "HEAD"]),
        Route("/kb/{kb_number:int}/", kb_detail_page, methods=["GET", "HEAD"]),
        Route("/explore/themes", explore_themes_page, methods=["GET", "HEAD"]),
        Route("/explore/themes/{theme_number:int}", explore_themes_page, methods=["GET", "HEAD"]),
    ]
