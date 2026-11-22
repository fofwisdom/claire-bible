"""Single-source builders for the shared Claire Bible top application bar, command palette, and ingestion modal chrome."""

from __future__ import annotations

from html import escape
from pathlib import Path

_PARTIALS_DIR = Path(__file__).resolve().parent.parent / "templates" / "partials"


def _read_partial(filename: str) -> str:
    return (_PARTIALS_DIR / filename).read_text(encoding="utf-8")


def render_appbar_html(
    *,
    bar_id: str = "bar",
    brand_id: str = "appbar-brand",
    brand_href: str = "/",
    brand_onclick: str = "",
    brand_title_key: str = "nav.brand_home",
    brand_extra_html: str = "",
    center_id: str = "appbar-center",
    center_extra_html: str = "",
    end_id: str = "appbar-end",
    ingest_enabled: bool = False,
    ingest_hidden: bool = False,
    ingest_btn_class: str = "",
    end_extra_html: str = "",
    home_href: str = "/",
    home_onclick: str = "",
    home_current: bool = False,
    synd_href: str = "/syndication",
    synd_current: bool = False,
    synd_hidden: bool = False,
    synd_extra_attrs: str = "",
    hamburger_extra_html: str = "",
    moderation_href: str = "/moderation",
    moderation_label_key: str = "nav.topbar.moderation",
    moderation_icon: str = "shield",
    moderation_current: bool = False,
    moderation_hidden: bool = False,
    console_href: str = "/account",
    console_label_key: str = "nav.topbar.console",
    console_icon: str = "settings",
    console_current: bool = False,
    console_hidden: bool = False,
    show_logout: bool = True,
    logout_hidden: bool = False,
) -> str:
    """Build the unified application bar HTML from ``partials/common_appbar.html``."""
    template = _read_partial("common_appbar.html")

    ingest_html = ""
    if ingest_enabled:
        btn_cls = f" {ingest_btn_class.strip()}" if ingest_btn_class.strip() else ""
        hid_attr = " hidden" if ingest_hidden else ""
        ingest_html = (
            f'<button type="button" id="addbtn" class="pri bar-ingest-btn{btn_cls}" commandfor="pop-ingest-menu" command="toggle-popover" aria-haspopup="menu" aria-expanded="false" data-i18n-title="nav.ingest_menu" title="${{t:nav.ingest_menu}}"{hid_attr}>'
            '<svg class="lineicon" viewBox="0 0 16 16" aria-hidden="true"><path d="M8 3v10M3 8h10"/></svg>'
            ' <span class="btn-label" data-i18n="nav.ingest">${t:nav.ingest}</span>'
            ' <span class="ingest-caret" aria-hidden="true">▾</span>'
            "</button>"
            '<div id="pop-ingest-menu" popover="auto" class="app-popover app-popover-end">'
            '<div focusgroup="menu nomemory" aria-labelledby="addbtn">'
            '<button type="button" id="ingest-opt-new" data-ingest-cmd="ingest">'
            '<span class="btn-icon" data-icon="plus"></span>'
            '<span class="app-menu-text"><span class="app-menu-label" data-i18n="nav.ingest_new">${t:nav.ingest_new}</span><small class="app-menu-desc" data-i18n="nav.ingest_new_desc">${t:nav.ingest_new_desc}</small></span>'
            "</button>"
            '<button type="button" id="ingest-opt-reingest" data-ingest-cmd="reingest">'
            '<span class="btn-icon" data-icon="restore"></span>'
            '<span class="app-menu-text"><span class="app-menu-label" data-i18n="nav.ingest_reingest">${t:nav.ingest_reingest}</span><small class="app-menu-desc" data-i18n="nav.ingest_reingest_desc">${t:nav.ingest_reingest_desc}</small></span>'
            "</button>"
            '<button type="button" id="ingest-opt-regen" data-ingest-cmd="regen">'
            '<span class="btn-icon" data-icon="repeat"></span>'
            '<span class="app-menu-text"><span class="app-menu-label" data-i18n="nav.ingest_regen">${t:nav.ingest_regen}</span><small class="app-menu-desc" data-i18n="nav.ingest_regen_desc">${t:nav.ingest_regen_desc}</small></span>'
            "</button>"
            "</div></div>"
        )

    account_items: list[str] = []
    if hamburger_extra_html.strip():
        account_items.append('<hr class="app-menu-sep" id="menu-account-sep"/>')

    menu_synd_hid = " hidden" if synd_hidden else ""
    menu_synd_cur = ' aria-current="page"' if synd_current else ""
    account_items.append(
        f'<a id="menusyndlink" class="app-menu-item" href="{escape(synd_href, quote=True)}"'
        ' data-i18n-title="nav.syndication" title="${t:nav.syndication}"'
        f"{menu_synd_hid}{menu_synd_cur}>"
        '<span class="btn-icon" data-icon="inbox"></span>'
        '<span class="btn-label" data-i18n="nav.topbar.syndication">${t:nav.topbar.syndication}</span>'
        "</a>"
    )

    mod_hid = " hidden" if moderation_hidden else ""
    mod_cur = ' aria-current="page"' if moderation_current else ""
    account_items.append(
        f'<a id="moderationlinkbtn" class="app-menu-item" href="{escape(moderation_href, quote=True)}"'
        f' data-i18n-title="{escape(moderation_label_key, quote=True)}" title="${{t:{moderation_label_key}}}"'
        f"{mod_hid}{mod_cur}>"
        f'<span class="btn-icon" data-icon="{escape(moderation_icon, quote=True)}"></span>'
        f'<span class="btn-label" data-i18n="{escape(moderation_label_key, quote=True)}">${{t:{moderation_label_key}}}</span>'
        "</a>"
    )

    con_hid = " hidden" if console_hidden else ""
    con_cur = ' aria-current="page"' if console_current else ""
    account_items.append(
        f'<a id="consolelinkbtn" class="app-menu-item" href="{escape(console_href, quote=True)}"'
        f' data-i18n-title="{escape(console_label_key, quote=True)}" title="${{t:{console_label_key}}}"'
        f"{con_hid}{con_cur}>"
        f'<span class="btn-icon" data-icon="{escape(console_icon, quote=True)}"></span>'
        f'<span class="btn-label" data-i18n="{escape(console_label_key, quote=True)}">${{t:{console_label_key}}}</span>'
        "</a>"
    )
    if show_logout:
        out_hid = " hidden" if logout_hidden else ""
        account_items.append(
            f'<form method="post" action="/logout" id="menu-logout-form" class="app-menu-form"{out_hid}>'
            '<button type="submit" id="menu-logout-btn" class="app-menu-item app-menu-danger">'
            '<span class="btn-icon" data-icon="lock"></span>'
            '<span class="btn-label" data-i18n="nav.signout">${t:nav.signout}</span>'
            "</button></form>"
        )

    replacements = {
        "__APPBAR_ID__": escape(bar_id, quote=True),
        "__BRAND_ID__": escape(brand_id, quote=True),
        "__BRAND_HREF__": escape(brand_href, quote=True),
        "__BRAND_ONCLICK_ATTR__": f' onclick="{brand_onclick}"' if brand_onclick else "",
        "__BRAND_TITLE_KEY__": escape(brand_title_key, quote=True),
        "__BRAND_EXTRA_HTML__": brand_extra_html,
        "__CENTER_ID__": escape(center_id, quote=True),
        "__CENTER_EXTRA_HTML__": center_extra_html,
        "__END_ID__": escape(end_id, quote=True),
        "__INGEST_CONTROL_HTML__": ingest_html,
        "__END_EXTRA_HTML__": end_extra_html,
        "__HOME_HREF__": escape(home_href, quote=True),
        "__HOME_ONCLICK_ATTR__": f' onclick="{home_onclick}"' if home_onclick else "",
        "__HOME_CURRENT_ATTR__": ' aria-current="page"' if home_current else "",
        "__SYND_HREF__": escape(synd_href, quote=True),
        "__SYND_HIDDEN_ATTR__": " hidden" if synd_hidden else "",
        "__SYND_CURRENT_ATTR__": ' aria-current="page"' if synd_current else "",
        "__SYND_EXTRA_ATTRS__": f" {synd_extra_attrs.strip()}" if synd_extra_attrs.strip() else "",
        "__HAMBURGER_EXTRA_HTML__": hamburger_extra_html,
        "__HAMBURGER_ACCOUNT_HTML__": "\n".join(account_items),
    }
    html = template
    for marker, value in replacements.items():
        html = html.replace(marker, value)
    return html


def render_cmdpalette_html() -> str:
    """Return the unified Command Palette dialog markup."""
    return _read_partial("common_cmdpalette.html")


def render_ingest_modals_html() -> str:
    """Return the unified ingestion/re-ingestion/regeneration modal dialog markup."""
    return _read_partial("common_ingest_modals.html")


__all__ = [
    "render_appbar_html",
    "render_cmdpalette_html",
    "render_ingest_modals_html",
]
