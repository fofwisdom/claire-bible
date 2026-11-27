"""The HTML pages of the public views: the shared knowledge bases (``/kb/{number}/``) and the themes (``/themes``, ``/themes/{number}/``).

This module mounts no route. The routes of ``server.py`` own these paths and call ``render_public_kb_page`` when the request wants HTML (content negotiation of the namespace design Section 6.5); they give JSON to other requests.
"""

from __future__ import annotations

import json
from typing import Any

from starlette.requests import Request
from starlette.responses import Response

from ..core.ports import get_web_ui_renderer
from ..i18n import catalog as i18n_catalog, t
from ..store import accounts as accounts_store
from .accounts_routes.admin import is_admin
from .accounts_routes.common import AccountsContext, escape, open_db, render_template
from .principal import request_principal
from .security import read_session_cookie

PUBLIC_VIEWS = ("kb_detail", "themes_list", "theme_detail")


def _caller_context(request: Request, ctx: AccountsContext) -> dict[str, Any]:
    """The data of the requester that the application bar and the page script use."""
    principal = request_principal(request)
    account_id = principal.account_id
    scope = principal.scope
    is_auth = account_id is not None and scope in {"owner", "write", "readonly"}
    admin = is_admin(principal)
    home_href = "/"
    conn = open_db(ctx)
    try:
        store = accounts_store.AccountStore(conn)
        mode = store.server_mode()
        if not is_auth:
            cookie_present, cookie_token = read_session_cookie(request.scope)
            if cookie_present and cookie_token:
                binding = accounts_store.validate_session_binding(conn, cookie_token)
                if binding is not None and binding.scope in {"owner", "write", "readonly"}:
                    try:
                        account = store.get(binding.account_id)
                    except accounts_store.AccountNotFound:
                        account = None
                    if account is not None and account.status in accounts_store.CREDENTIAL_ACCEPTING_STATUSES:
                        account_id = account.id
                        scope = binding.scope
                        is_auth = True
                        admin = account.role == "admin"
        if is_auth and mode == "network" and account_id is not None:
            username = store.get(account_id).username
            if username:
                home_href = f"/@{username}/"
    finally:
        conn.close()
    return {
        "is_authenticated": is_auth,
        "is_admin": admin,
        "scope": scope,
        "home_href": home_href,
        "console_href": "/admin" if admin else "/account",
        "mode": mode,
    }


def public_appbar_html(request: Request, ctx: AccountsContext, *, active: str = "") -> str:
    """Build the shared public application bar with top-level navigation links (Home, Themes, Directory, About)."""
    caller = _caller_context(request, ctx)
    end_extra_html = "" if caller["is_authenticated"] else f'<a href="/login" class="bar-icon-btn">{escape(t("directory.login"))}</a>'
    return get_web_ui_renderer().render_appbar_html(
        bar_id="public-bar",
        brand_id="brand-home",
        brand_href="/",
        primary_nav_active=active,
        show_primary_nav=True,
        center_id="public-bar-center",
        end_id="public-bar-end",
        end_extra_html=end_extra_html,
        ingest_enabled=False,
        home_href=caller["home_href"],
        synd_href="/syndication" if caller["is_authenticated"] else "",
        synd_hidden=not caller["is_authenticated"],
        moderation_href="/moderation" if caller["is_authenticated"] else "",
        moderation_hidden=not caller["is_authenticated"],
        console_href=caller["console_href"] if caller["is_authenticated"] else "/account",
        console_icon="wrench" if caller["is_admin"] else "settings",
        console_hidden=not caller["is_authenticated"],
        show_logout=caller["is_authenticated"],
        logout_hidden=not caller["is_authenticated"],
    )


def render_public_kb_page(
    request: Request,
    *,
    view: str,
    title: str,
    ref: str | None = None,
    ctx: AccountsContext,
) -> Response:
    """The HTML page of the public views of shared knowledge bases and themes. ``view`` is one of ``PUBLIC_VIEWS``; ``ref`` is the number of the knowledge base or the theme of a detail view. The page script gets the data from the JSON form of the same path."""
    if view not in PUBLIC_VIEWS:
        raise ValueError(f"unknown public view: {view!r}")
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
    active_nav = "themes" if view in ("themes_list", "theme_detail", "kb_detail") else ""
    appbar_html = public_appbar_html(request, ctx, active=active_nav)
    footer_links_html = (
        f'<a href="/">{escape(t("kb.pub.nav_home"))}</a>'
        f'<a href="/themes">{escape(t("kb.pub.nav_themes"))}</a>'
        f'<a href="/directory">{escape(t("kb.pub.nav_accounts"))}</a>'
        f'<a href="/about">{escape(t("nav.about"))}</a>'
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
            "footer_links_html": footer_links_html,
        },
    )


def directory_head_markup(request: Request) -> str:
    """Return shared stylesheet and script tags plus a scoped KB catalog for ``public_directory.html``."""
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
