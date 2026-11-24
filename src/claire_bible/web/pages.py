"""Request-aware page hydration connecting Claire data to Bible bundles."""

from __future__ import annotations

from functools import lru_cache
import html as _html
import json as _json
from typing import Any

from bible import STATIC_DIR as _STATIC_DIR
from bible import TEMPLATES_DIR as _TEMPLATES_DIR
from bible.web import metadata as _metadata
from bible.web import pages as _bible_pages
from bible.web.pages import GRAPH_HTML, SYNDICATION_HTML, _SHARED_HTML
from claire.i18n import browser_catalog_json, get_locale, t

__all__ = [
    "GRAPH_HTML",
    "SYNDICATION_HTML",
    "_SHARED_HTML",
    "_STATIC_DIR",
    "_TEMPLATES_DIR",
    "_graph_html_for",
    "_shared_html_for",
    "_syndication_html_for",
    "localize_page",
    "render_graph_html",
    "render_syndication_html",
    "shared_html",
]


def localize_page(page_html: str, locale: str) -> str:
    """Resolve translation slots in ``page_html`` for ``locale``."""
    return _bible_pages.localize_page(page_html, locale, lambda key: t(key, locale=locale))  # type: ignore[arg-type]


@lru_cache(maxsize=None)
def _graph_html_for(locale: str) -> str:
    return localize_page(GRAPH_HTML, locale)


@lru_cache(maxsize=None)
def _syndication_html_for(locale: str) -> str:
    return localize_page(SYNDICATION_HTML, locale)


@lru_cache(maxsize=None)
def _shared_html_for(locale: str) -> str:
    catalog = browser_catalog_json(locale, scope="share")  # type: ignore[arg-type]
    return localize_page(_SHARED_HTML, locale).replace("<!-- __I18N_CATALOG__ -->", f'<script id="claire-i18n-catalog" type="application/json">{catalog}</script>', 1)


def shared_html(doc: dict, settings: Any = None, *, base_url: str = "", share_token: str = "", can_access_raw: bool = False) -> str:
    """Render a standalone shared-document reading page for ``doc``."""
    if isinstance(settings, str):
        ga_id = settings
        resolved_base_url = base_url
        site_name = "Claire Bible"
    elif settings is None:
        from claire.core.config import get_settings

        s = get_settings()
        ga_id = str(getattr(s, "ga_measurement_id", "") or "").strip()
        resolved_base_url = base_url or getattr(s, "public_url", "")
        site_name = getattr(s, "site_name", "Claire Bible")
    else:
        ga_id = str(getattr(settings, "ga_measurement_id", "") or "").strip()
        resolved_base_url = base_url or getattr(settings, "public_url", "")
        site_name = getattr(settings, "site_name", "Claire Bible")

    from claire.ingest.source_types import is_media_source_type

    src_type = str((doc or {}).get("source_type") or "").strip().lower()
    return _bible_pages.render_shared_page(
        _shared_html_for(get_locale()),
        doc=doc,
        ga_id=ga_id,
        base_url=resolved_base_url,
        share_token=share_token,
        site_name=site_name,
        is_media=is_media_source_type(src_type),
        can_access_raw=can_access_raw,
    )


def render_graph_html(settings: Any = None, *, include_private: bool = True, theme: Any | None = None, host: str | None = None, request: Any | None = None, bootstrap: dict[str, str] | None = None, account_id: int | None = None) -> str:
    """Hydrate settings, themes, and locale data into the workspace graph HTML."""
    if settings is None:
        from claire.core.config import get_settings

        s = get_settings()
    else:
        s = settings
    repo = getattr(s, "effective_github_repository", getattr(s, "github_repository", "fofwisdom/claire-bible"))
    base_url = getattr(s, "effective_source_base_url", getattr(s, "source_base_url", f"https://github.com/{repo}"))
    if not base_url:
        base_url = f"https://github.com/{repo}"
    if theme is not None:
        ga_id = str(getattr(theme, "ga_measurement_id", "") or "").strip()
    else:
        ga_id = str(getattr(s, "ga_measurement_id", "") or "").strip()
    kb_id = getattr(theme, "id", None) if theme else None
    theme_label = getattr(theme, "label", "") if theme else ""
    ga_tag = _metadata.render_ga_tag(ga_id, kb_id=kb_id, theme_label=theme_label)
    sorcerer = getattr(s, "sorcerer", "owner")
    raw_sorcerer = str(sorcerer).strip() if sorcerer is not None else ""
    if not raw_sorcerer:
        raw_sorcerer = "owner"
    safe_sorcerer = _html.escape(raw_sorcerer, quote=True)

    if account_id is None and request is not None:
        try:
            from claire.api.namespace import request_namespace

            ns = request_namespace(request)
            if ns is not None and getattr(ns, "account_id", None) is not None:
                account_id = ns.account_id
        except Exception:
            try:
                from claire.api.security import request_namespace

                ns = request_namespace(request)
                if ns is not None and getattr(ns, "account_id", None) is not None:
                    account_id = ns.account_id
            except Exception:
                pass
    if account_id is None and theme is not None and getattr(theme, "account_id", None) is not None:
        account_id = theme.account_id
    if account_id is None:
        account_id = 1

    is_multi = bool(getattr(s, "multi_theme", False))
    theme_mode = "multi" if is_multi else "single"
    if is_multi:
        from claire.store.knowledge_base import get_kb_manager

        tm = get_kb_manager(s)
        owned = tm.list_selectable_kbs(account_id)
        if include_private:
            filtered = owned
        else:
            cleaned_host = ""
            if host:
                cleaned_host = str(host).strip().lower()
                if ":" in cleaned_host:
                    cleaned_host = cleaned_host.split(":", 1)[0].strip()

            if cleaned_host and cleaned_host in getattr(tm, "_fqdn_to_kb_id", {}):
                dedicated_tid = tm._fqdn_to_kb_id[cleaned_host]
                if dedicated_tid is not None:
                    filtered = [t_item for t_item in owned if t_item.id == dedicated_tid and t_item.is_public]
                else:
                    filtered = [t_item for t_item in owned if t_item.is_public and (not t_item.fqdn or t_item.is_personal)]
            else:
                filtered = [t_item for t_item in owned if t_item.is_public and (not t_item.fqdn or t_item.is_personal)]
        themes_list = [t_item.to_dict() for t_item in filtered]
    else:
        themes_list = [
            {
                "id": 0,
                "seq": 0,
                "number": "0",
                "label": "기본 지식베이스",
                "description": "일반 수집 자료 및 기본 지식",
                "icon": "📚",
                "is_personal": True,
                "is_public": True,
                "sorcerer": safe_sorcerer,
                "knowledge_manager": safe_sorcerer,
                "presentation_enabled": bool(getattr(s, "presentation_enabled", False)),
            }
        ]
    themes_json = _json.dumps(themes_list, ensure_ascii=False)
    themes_json_safe = themes_json.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")

    if bootstrap is None and request is not None:
        try:
            from claire.api.namespace import shell_bootstrap

            bootstrap = shell_bootstrap(request)
        except Exception:
            bootstrap = None

    if bootstrap is None:
        bootstrap = {
            "api-base": "/",
            "root-base": "/",
            "storage-key": "anon",
        }

    api_base = _html.escape(bootstrap.get("api-base", "/"), quote=True)
    root_base = _html.escape(bootstrap.get("root-base", "/"), quote=True)
    storage_key = _html.escape(bootstrap.get("storage-key", "anon"), quote=True)

    is_fqdn = False
    if theme and getattr(theme, "fqdn", None) and host:
        hostname = host.split(":", 1)[0].strip().lower()
        if theme.fqdn.strip().lower() == hostname:
            is_fqdn = True

    if is_fqdn:
        picker_style_attr = 'style="display:inline-flex"'
        picker_title_attr = f'title="지식 테마: {_html.escape(getattr(theme, "label", "") or "", quote=True)}"'
        select_style_attr = 'style="display:none"'
        plain_style_attr = 'style="display:inline-flex"'
        plain_icon = _html.escape(getattr(theme, "icon", None) or "📚")
        plain_label = _html.escape(getattr(theme, "label", None) or "")
    else:
        if is_multi and len(themes_list) > 1:
            picker_style_attr = 'style="display:inline-flex"'
        else:
            picker_style_attr = 'style="display:none"'
        picker_title_attr = f'title="{_html.escape(t("ws.bar.theme_title"), quote=True)}"'
        select_style_attr = ""
        plain_style_attr = 'style="display:none"'
        plain_icon = "📚"
        plain_label = ""

    return _bible_pages.render_graph_page(
        _graph_html_for(get_locale()),
        base_url=base_url,
        repo=repo,
        ga_tag=ga_tag,
        catalog_json=browser_catalog_json(),
        safe_sorcerer=safe_sorcerer,
        theme_mode=theme_mode,
        themes_json_safe=themes_json_safe,
        picker_style_attr=picker_style_attr,
        picker_title_attr=picker_title_attr,
        select_style_attr=select_style_attr,
        plain_style_attr=plain_style_attr,
        plain_icon=plain_icon,
        plain_label=plain_label,
        api_base=api_base,
        root_base=root_base,
        storage_key=storage_key,
    )


def _theme_zero_number(settings: Any) -> str | None:
    """Return the external decimal number string for knowledge base 0, or None."""
    from claire.core import ids
    from claire.store.knowledge_base import KnowledgeBaseNotFound, get_kb_manager

    try:
        number = get_kb_manager(settings).get_kb(0).number
    except KnowledgeBaseNotFound:
        return None
    return ids.format_public_id(number) if number is not None else None


def _render_syndication_bundle(bundle_html: str, settings: Any = None, *, include_private: bool = True, theme: Any | None = None, host: str | None = None, auth_scope: str = "owner", request: Any | None = None) -> str:
    """Populate settings, themes, and account tokens for the Syndication HTML bundle."""
    if settings is None:
        from claire.core.config import get_settings

        s = get_settings()
    else:
        s = settings
    repo = getattr(s, "effective_github_repository", getattr(s, "github_repository", "fofwisdom/claire-bible"))
    base_url = getattr(s, "effective_source_base_url", getattr(s, "source_base_url", f"https://github.com/{repo}"))
    if not base_url:
        base_url = f"https://github.com/{repo}"
    if theme is not None:
        ga_id = str(getattr(theme, "ga_measurement_id", "") or "").strip()
    else:
        ga_id = str(getattr(s, "ga_measurement_id", "") or "").strip()
    kb_id = getattr(theme, "id", None) if theme else None
    theme_label = getattr(theme, "label", "") if theme else ""
    ga_tag = _metadata.render_ga_tag(ga_id, kb_id=kb_id, theme_label=theme_label)
    sorcerer = getattr(s, "sorcerer", "owner")
    raw_sorcerer = str(sorcerer).strip() if sorcerer is not None else ""
    if not raw_sorcerer:
        raw_sorcerer = "owner"
    safe_sorcerer = _html.escape(raw_sorcerer, quote=True)

    caller_account_id: int | None = None
    is_network_mode = False
    is_admin_viewer = False
    account_username: str | None = None
    if request is not None:
        try:
            from claire.api.accounts_routes.admin import is_admin
            from claire.api.namespace import request_namespace
            from claire.api.principal import request_principal

            ns = request_namespace(request)
            if ns is not None:
                is_network_mode = ns.mode == "network"
                caller_account_id = ns.account_id
            principal = request_principal(request)
            is_admin_viewer = is_admin(principal)
            if caller_account_id is None and principal.account_id is not None:
                caller_account_id = principal.account_id
            if caller_account_id is None and not is_network_mode and getattr(principal, "scope", "anonymous") != "anonymous":
                caller_account_id = 1
        except Exception:
            pass

    is_multi = bool(getattr(s, "multi_theme", False))
    theme_mode = "multi" if is_multi else "single"
    if is_multi:
        from claire.store.knowledge_base import get_kb_manager

        tm = get_kb_manager(s)
        raw_themes = tm.list_kbs(include_private=include_private, host=host)
        if is_network_mode and caller_account_id is not None:
            raw_themes = [t_item for t_item in raw_themes if getattr(t_item, "account_id", 1) == caller_account_id]
        themes_list = [t_item.to_dict() for t_item in raw_themes]
    else:
        themes_list = [
            {
                "id": 0,
                "seq": 0,
                "label": "기본 지식베이스",
                "description": "일반 수집 자료 및 기본 지식",
                "icon": "📚",
                "is_personal": True,
                "is_public": True,
                "sorcerer": safe_sorcerer,
                "knowledge_manager": safe_sorcerer,
                "presentation_enabled": bool(getattr(s, "presentation_enabled", False)),
                "number": _theme_zero_number(s),
            }
        ]
    themes_json = _json.dumps(themes_list, ensure_ascii=False)
    themes_json_safe = themes_json.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    active_kb_id = str(kb_id if kb_id is not None else (themes_list[0]["id"] if themes_list else 0))
    safe_scope = _html.escape(str(auth_scope or "owner"), quote=True)
    bootstrap: dict[str, str] | None = None
    if request is not None:
        from claire.api.namespace import shell_bootstrap

        bootstrap = shell_bootstrap(request)
    raw_storage_key = (bootstrap or {}).get("storage-key", "anon")
    if caller_account_id is not None and getattr(s, "db_file", None):
        try:
            from claire.store import accounts as accounts_store
            from claire.store import db as dbm

            conn = dbm.connect_existing(s.db_file, readonly=True)
            try:
                acc = accounts_store.AccountStore(conn).get(int(caller_account_id))
                if raw_storage_key == "anon":
                    raw_storage_key = f"a{acc.public_id}"
                account_username = acc.username
            finally:
                conn.close()
        except Exception:
            pass
    storage_key = _html.escape(raw_storage_key, quote=True)
    home_url = _html.escape(f"/@{account_username}/", quote=True) if (is_network_mode and account_username) else None

    return _bible_pages.render_syndication_page(
        bundle_html,
        base_url=base_url,
        repo=repo,
        ga_tag=ga_tag,
        safe_sorcerer=safe_sorcerer,
        theme_mode=theme_mode,
        active_kb_id=active_kb_id,
        safe_scope=safe_scope,
        themes_json_safe=themes_json_safe,
        storage_key=storage_key,
        catalog_json=browser_catalog_json(),
        home_url=home_url,
        is_admin_viewer=is_admin_viewer,
    )


def render_syndication_html(settings: Any = None, *, include_private: bool = True, theme: Any | None = None, host: str | None = None, auth_scope: str = "owner", request: Any | None = None) -> str:
    """Return rendered live Syndication UI HTML."""
    return _render_syndication_bundle(
        _syndication_html_for(get_locale()),
        settings,
        include_private=include_private,
        theme=theme,
        host=host,
        auth_scope=auth_scope,
        request=request,
    )
