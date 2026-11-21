"""Standalone HTML bundle builders and pure slot assembly helpers."""

from __future__ import annotations

from collections.abc import Callable
import html as _html
import json as _json
from pathlib import Path
import re
from types import SimpleNamespace
from typing import Any

from . import chrome
from .metadata import render_ga_tag, render_open_graph_tags

_PACKAGE_DIR = Path(__file__).resolve().parent.parent
_STATIC_DIR = _PACKAGE_DIR / "static"
_TEMPLATES_DIR = _PACKAGE_DIR / "templates"
# Order matters: core -> simulation -> renderers -> network.
_GRAPH_ENGINE_FILES = ("core.js", "simulation.js", "renderers.js", "network.js")

__all__ = [
    "GRAPH_HTML",
    "SYNDICATION_HTML",
    "_SHARED_HTML",
    "_build_standalone_graph_html",
    "_build_standalone_shared_html",
    "_build_standalone_syndication_html",
    "localize_page",
    "render_graph_page",
    "render_shared_page",
    "render_syndication_page",
]


def _build_standalone_graph_html() -> str:
    """Combine static CSS/JS assets and templates/index.html into standalone GRAPH_HTML."""
    theme_css = (_STATIC_DIR / "css" / "theme.css").read_text(encoding="utf-8")
    shared_ui_css = (_STATIC_DIR / "css" / "shared_ui.css").read_text(encoding="utf-8")
    ws_css = (_STATIC_DIR / "css" / "workspace.css").read_text(encoding="utf-8")
    reader_css = (_STATIC_DIR / "css" / "reader.css").read_text(encoding="utf-8")

    graph_js = "\n".join((_STATIC_DIR / "js" / "graph" / name).read_text(encoding="utf-8") for name in _GRAPH_ENGINE_FILES)
    doc_content_js = (_STATIC_DIR / "js" / "renderers" / "doc_content.js").read_text(encoding="utf-8")
    reader_js = (_STATIC_DIR / "js" / "reader.js").read_text(encoding="utf-8")
    appbar_js = (_STATIC_DIR / "js" / "appbar.js").read_text(encoding="utf-8")
    shared_ui_js = (_STATIC_DIR / "js" / "shared_ui.js").read_text(encoding="utf-8")
    kb_dialogs_js = (_STATIC_DIR / "js" / "kb_dialogs.js").read_text(encoding="utf-8")
    ingest_dialogs_js = (_STATIC_DIR / "js" / "ingest_dialogs.js").read_text(encoding="utf-8")
    command_palette_js = (_STATIC_DIR / "js" / "command_palette.js").read_text(encoding="utf-8")
    app_js = (_STATIC_DIR / "js" / "app.js").read_text(encoding="utf-8")
    i18n_js = (_STATIC_DIR / "js" / "i18n.js").read_text(encoding="utf-8")
    icons_js = (_STATIC_DIR / "js" / "icons.js").read_text(encoding="utf-8")
    index_tmpl = (_TEMPLATES_DIR / "index.html").read_text(encoding="utf-8")

    ws_brand_extra = (
        '<div class="theme-picker-wrap" id="theme-picker-wrap" __THEME_PICKER_TITLE_ATTR__ __THEME_PICKER_STYLE_ATTR__>'
        '<label for="theme-select" class="sr-only">${t:nav.theme_select}</label>'
        '<div class="theme-select-control" id="theme-select-control" __THEME_SELECT_STYLE_ATTR__>'
        '<span class="theme-icon" id="theme-curr-icon" aria-hidden="true">📚</span>'
        '<select id="theme-select" class="theme-select" onchange="switchKnowledgeTheme(this.value)" title="${t:ws.bar.theme_title}" aria-label="${t:nav.theme_select}"><option value="0">${t:ws.bar.theme_default}</option></select>'
        '<span class="theme-caret" aria-hidden="true">▾</span>'
        "</div>"
        '<div class="theme-plain-display" id="theme-plain-display" __THEME_PLAIN_STYLE_ATTR__>'
        '<span class="theme-icon" id="theme-plain-icon" aria-hidden="true">__THEME_PLAIN_ICON__</span>'
        '<span class="theme-plain-label" id="theme-plain-label">__THEME_PLAIN_LABEL__</span>'
        "</div></div>"
        '<button id="listtoggle" type="button" class="bar-icon-btn" aria-expanded="true" aria-controls="docs" onclick="toggleListPane()" data-i18n-title="nav.list_toggle" data-i18n-aria-label="nav.list_toggle"><svg class="lineicon" viewBox="0 0 16 16" aria-hidden="true"><path d="M2.5 3.5h11v9h-11zM6 3.5v9"/></svg></button>'
    )
    ws_center_extra = (
        '<div class="center-tab-group" id="centertab-group">'
        '<button id="mobile-doclist-btn" type="button" class="mobile-doclist-btn" onclick="openDocDrawer()" title="${t:ws.list.open}" aria-label="${t:nav.list}"><span class="btn-icon" data-icon="list"></span> <span class="btn-label">${t:nav.list}</span></button>'
        '<nav id="centertabs" class="center-tabs" data-i18n-aria-label="nav.views">'
        '<button id="centertab-home" type="button" class="center-tab-btn" aria-current="page" onclick="enterHome({reason:\'nav\'})" data-i18n-title="nav.home"><span class="btn-icon"><svg class="lineicon" viewBox="0 0 16 16" aria-hidden="true"><path d="M2.5 8 8 3l5.5 5M4 7v6h3v-3.5h2V13h3V7"/></svg></span> <span class="btn-label" data-i18n="nav.home">${t:nav.home}</span></button>'
        '<button id="centertab-reader" type="button" class="center-tab-btn" onclick="setCenterView(\'reader\')" title="${t:ws.view.reader_title}" aria-label="${t:ws.view.reader}"><span class="btn-icon"><svg class="lineicon" viewBox="0 0 16 16" aria-hidden="true"><path d="M4 2.5h5l3 3v8H4zM9 2.5v3h3M6 8.5h4M6 11h4"/></svg></span> <span class="btn-label">${t:ws.view.reader}</span></button>'
        '<button id="centertab-graph" type="button" class="center-tab-btn" onclick="setCenterView(\'graph\')" title="${t:ws.view.graph_title}" aria-label="${t:nav.graph}"><span class="btn-icon"><svg class="lineicon" viewBox="0 0 16 16" aria-hidden="true"><circle cx="4" cy="4" r="1.8"/><circle cx="12" cy="6" r="1.8"/><circle cx="7" cy="12" r="1.8"/><path d="M5.7 4.5 10.3 5.6M11 7.6 8.2 10.7M4.6 5.7 6.4 10.3"/></svg></span> <span class="btn-label">${t:nav.graph}</span></button>'
        '<button id="centertab-presentation" type="button" class="center-tab-btn" onclick="setCenterView(\'presentation\')" title="${t:ws.view.presentation_title}" aria-label="${t:ws.view.presentation}"><span class="btn-icon"><svg class="lineicon" viewBox="0 0 16 16" aria-hidden="true"><path d="M2.5 3.5h11v7h-11zM8 10.5v3M5.5 13.5h5"/></svg></span> <span class="btn-label">${t:ws.view.presentation}</span></button>'
        "</nav>"
        '<nav id="streamtabs" class="center-tabs stream-tabs" aria-label="${t:ws.view.stream_nav}">'
        '<button id="centertab-stream" type="button" class="center-tab-btn" onclick="setCenterView(\'stream\')" title="${t:ws.view.stream_title}" aria-label="${t:ws.view.stream}"><span class="btn-icon"><svg class="lineicon" viewBox="0 0 16 16" aria-hidden="true"><path d="M3 4h10M3 8h10M3 12h6"/></svg></span> <span class="btn-label">${t:ws.view.stream}</span></button>'
        "</nav></div>"
    )
    ws_hamburger_extra = (
        '<button type="button" id="palettebtn" onclick="openCommandPaletteFromMenu()" data-i18n-title="nav.palette.open_title" data-i18n-aria-label="nav.palette.open"><span class="btn-icon" data-icon="search"></span> <span class="app-menu-label" data-i18n="nav.palette.open">${t:nav.palette.open}</span><kbd class="app-menu-kbd">/</kbd></button>'
        '<div id="graph-section" class="app-menu-sec" aria-label="${t:ws.menu.graph_tools}">'
        '<hr class="app-menu-sep"/>'
        '<div class="app-menu-sec-title" id="menu-section-title">${t:ws.menu.section_title}</div>'
        '<button type="button" id="pathbtn" onclick="togglePathMode()" title="${t:ws.menu.path_title}" aria-label="${t:ws.menu.path}"><span class="btn-icon" data-icon="link"></span> <span class="btn-label">${t:ws.menu.path}</span></button>'
        '<button type="button" id="synthbtn" onclick="synth()" title="${t:ws.menu.synth} (0)" hidden><span class="btn-icon" data-icon="puzzle"></span> <span class="btn-label">${t:ws.menu.synth} (0)</span></button>'
        "</div>"
    )
    ws_appbar = chrome.render_appbar_html(
        bar_id="bar",
        brand_id="appbar-brand",
        brand_href="./",
        brand_onclick="if(!event.ctrlKey&&!event.metaKey&&event.button===0){event.preventDefault();enterHome({reason:'brand'});}",
        brand_title_key="ws.bar.brand_title",
        brand_extra_html=ws_brand_extra,
        center_id="centerhead",
        center_extra_html=ws_center_extra,
        end_id="viewoptions",
        ingest_enabled=True,
        ingest_hidden=True,
        home_href="./",
        home_onclick="if(!event.ctrlKey&&!event.metaKey&&event.button===0){event.preventDefault();enterHome({reason:'nav'});}",
        home_current=True,
        synd_href="/syndication",
        synd_hidden=True,
        hamburger_extra_html=ws_hamburger_extra,
        moderation_href="/moderation",
        moderation_hidden=True,
        console_href="/settings",
        console_label_key="ws.bar.my_settings",
        console_icon="settings",
        console_hidden=True,
        show_logout=True,
        logout_hidden=True,
    )
    ws_appbar = ws_appbar.replace('<div class="appbar-palette-slot">', '<div id="netsearch" class="appbar-palette-slot">')
    index_tmpl = (
        index_tmpl.replace("<!-- __COMMON_APPBAR__ -->", ws_appbar)
        .replace("<!-- __COMMON_CMDPALETTE__ -->", chrome.render_cmdpalette_html())
        .replace("<!-- __COMMON_INGEST_MODALS__ -->", chrome.render_ingest_modals_html())
    )

    appbar_css = (_STATIC_DIR / "css" / "appbar.css").read_text(encoding="utf-8")
    css_bundle = f"<style>\n{theme_css}\n{appbar_css}\n{shared_ui_css}\n{ws_css}\n{reader_css}\n</style>"
    html = re.sub(r"<link rel=stylesheet href=/static/css/[^>]+>\s*", "", index_tmpl)
    html = html.replace("</head>", f"{css_bundle}\n</head>")

    html = re.sub(r"<script>\s*window\.__CLAIRE_CONFIG\s*=[\s\S]*?</script>\s*", "", html)
    html = re.sub(r"<script src=/static/js/[^>]+></script>\s*", "", html)

    config_js = """  window.__CLAIRE_CONFIG = {
    sourceBaseUrl: '__SOURCE_BASE_URL__',
    githubRepository: '__GITHUB_REPOSITORY__',
    sorcerer: '__SORCERER__',
    owner: '__OWNER__',
    knowledgeManager: '__KNOWLEDGE_MANAGER__',
    themeMode: '__THEME_MODE__',
    themes: __THEMES_JSON__
  };
  window.__CLAIRE_THEMES__ = window.__CLAIRE_CONFIG.themes;"""

    js_bundle = f"<script>\n{config_js}\n\n{i18n_js}\n{icons_js}\n{appbar_js}\n{shared_ui_js}\n{kb_dialogs_js}\n{ingest_dialogs_js}\n{graph_js}\n{doc_content_js}\n{reader_js}\n{command_palette_js}\n{app_js}\n</script>"
    html = html.replace("</body>", f"{js_bundle}\n</body>")
    return html


def _build_standalone_shared_html() -> str:
    """Combine static CSS/JS assets and templates/share.html into standalone _SHARED_HTML."""
    theme_css = (_STATIC_DIR / "css" / "theme.css").read_text(encoding="utf-8")
    reader_css = (_STATIC_DIR / "css" / "reader.css").read_text(encoding="utf-8")
    doc_content_js = (_STATIC_DIR / "js" / "renderers" / "doc_content.js").read_text(encoding="utf-8")
    icons_js = (_STATIC_DIR / "js" / "icons.js").read_text(encoding="utf-8")
    share_tmpl = (_TEMPLATES_DIR / "share.html").read_text(encoding="utf-8")

    css_bundle = f"<style>\n{theme_css}\n{reader_css}\n</style>"
    html = re.sub(r"<link rel=stylesheet href=/static/css/[^>]+>\s*", "", share_tmpl)
    html = html.replace("</head>", f"{css_bundle}\n</head>")

    html = html.replace("<script src=/static/js/renderers/doc_content.js></script>\n", "")
    idx = html.find("<script>", html.find('id="docdata"'))
    if idx != -1:
        html = html[: idx + 8] + f"\n{icons_js}\n{doc_content_js}\n" + html[idx + 8 :]
    return html


def _build_standalone_syndication_html() -> str:
    """Combine static CSS/JS assets and templates/syndication.html into standalone SYNDICATION_HTML."""
    theme_css = (_STATIC_DIR / "css" / "theme.css").read_text(encoding="utf-8")
    synd_css = (_STATIC_DIR / "css" / "syndication.css").read_text(encoding="utf-8")
    synd_live_js = (_STATIC_DIR / "js" / "syndication_live.js").read_text(encoding="utf-8")
    syndication_tmpl = (_TEMPLATES_DIR / "syndication.html").read_text(encoding="utf-8")
    synd_brand_extra = (
        '<div class="theme-picker-wrap kb-only">'
        '<label for="theme-selector" class="sr-only" data-i18n="nav.theme_select">Select knowledge theme</label>'
        '<div class="theme-select-control">'
        '<select id="theme-selector" class="theme-select" data-i18n-aria-label="nav.theme_select"></select>'
        '<span class="theme-caret" aria-hidden="true">▾</span>'
        "</div></div>"
    )
    synd_center_extra = (
        '<span id="active-stream-title" class="appbar-title kb-only" data-i18n="synd.kb.stream.all">All ingested documents</span>'
        '<span id="client-stream-title" class="appbar-title client-only"></span>'
    )
    synd_end_extra = (
        '<button type="button" id="btn-note-new" class="bar-ingest-btn client-only" data-i18n-title="synd.compose.new_note_title" hidden>'
        '<span data-icon="edit"></span> <span class="btn-label" data-i18n="synd.compose.new_note">${t:synd.compose.new_note}</span>'
        "</button>"
    )
    synd_hamburger_extra = (
        '<div class="app-menu-sec kb-only">'
        '<div class="app-menu-sec-title" data-i18n="synd.kb.menu.section_kb">${t:synd.kb.menu.section_kb}</div>'
        '<a id="menu-pref-link" class="app-menu-item" href="/settings/reader" data-settings-path="/settings/reader"><span class="btn-icon" data-icon="settings"></span> <span class="app-menu-label" data-i18n="synd.kb.menu.preferences">${t:synd.kb.menu.preferences}</span></a>'
        '<a class="app-menu-item" href="/settings/connections" data-settings-path="/settings/connections"><span class="btn-icon" data-icon="lock"></span> <span class="app-menu-label" data-i18n="synd.kb.menu.connections">${t:synd.kb.menu.connections}</span></a>'
        '<a class="app-menu-item" href="/settings/tags" data-settings-path="/settings/tags"><span class="btn-icon" data-icon="tag"></span> <span class="app-menu-label" data-i18n="synd.kb.menu.tags">${t:synd.kb.menu.tags}</span></a>'
        '<a class="app-menu-item" href="/settings/outbound" data-settings-path="/settings/outbound"><span class="btn-icon" data-icon="repeat"></span> <span class="app-menu-label" data-i18n="synd.kb.menu.outbound">${t:synd.kb.menu.outbound}</span></a>'
        '<hr class="app-menu-sep"/>'
        '<div class="app-menu-sec-title" data-i18n="synd.kb.list_style.title">${t:synd.kb.list_style.title}</div>'
        '<button type="button" data-set-list-style="list"><span class="app-menu-label" data-list-style-label>☰</span><kbd class="app-menu-kbd">l</kbd></button>'
        '<button type="button" data-set-list-style="compact"><span class="app-menu-label" data-list-style-label>≡</span></button>'
        '<button type="button" data-set-list-style="card"><span class="app-menu-label" data-list-style-label>▤</span></button>'
        '<button type="button" data-set-list-style="roomy"><span class="app-menu-label" data-list-style-label>▭</span></button>'
        '<hr class="app-menu-sep"/>'
        '<div class="app-menu-sec-title" data-i18n="synd.kb.layout.title">${t:synd.kb.layout.title}</div>'
        '<button type="button" id="menu-layout-3pane"><span id="menu-layout-3pane-label"></span><kbd class="app-menu-kbd">m</kbd></button>'
        '<button type="button" id="menu-layout-2pane"><span id="menu-layout-2pane-label"></span></button>'
        "</div>"
    )
    synd_appbar = chrome.render_appbar_html(
        bar_id="synd-bar",
        brand_id="appbar-brand",
        brand_href="/",
        brand_title_key="nav.brand_home",
        brand_extra_html=synd_brand_extra,
        center_id="synd-center",
        center_extra_html=synd_center_extra,
        end_id="synd-end",
        ingest_enabled=True,
        ingest_btn_class="kb-only",
        end_extra_html=synd_end_extra,
        home_href="/",
        synd_href="/syndication",
        synd_current=True,
        hamburger_extra_html=synd_hamburger_extra,
        moderation_href="/moderation",
        moderation_hidden=False,
        console_href="/settings/reader",
        console_label_key="nav.topbar.console",
        console_icon="settings",
        show_logout=True,
    )
    syndication_tmpl = (
        syndication_tmpl.replace("<!-- __COMMON_APPBAR__ -->", synd_appbar)
        .replace("<!-- __COMMON_CMDPALETTE__ -->", chrome.render_cmdpalette_html())
        .replace("<!-- __COMMON_INGEST_MODALS__ -->", chrome.render_ingest_modals_html())
    )
    appbar_css = (_STATIC_DIR / "css" / "appbar.css").read_text(encoding="utf-8")
    shared_ui_css = (_STATIC_DIR / "css" / "shared_ui.css").read_text(encoding="utf-8")
    shared_ui_js = (_STATIC_DIR / "js" / "shared_ui.js").read_text(encoding="utf-8")
    kb_dialogs_js = (_STATIC_DIR / "js" / "kb_dialogs.js").read_text(encoding="utf-8")
    css_bundle = f"<style>\n{theme_css}\n{appbar_css}\n{shared_ui_css}\n{synd_css}\n</style>"
    html = re.sub(r'<link rel="stylesheet" href="/static/css/[^>]+>\s*', "", syndication_tmpl)
    html = html.replace("</head>", f"{css_bundle}\n</head>", 1)
    i18n_js = (_STATIC_DIR / "js" / "i18n.js").read_text(encoding="utf-8")
    appbar_js = (_STATIC_DIR / "js" / "appbar.js").read_text(encoding="utf-8")
    ingest_dialogs_js = (_STATIC_DIR / "js" / "ingest_dialogs.js").read_text(encoding="utf-8")
    command_palette_js = (_STATIC_DIR / "js" / "command_palette.js").read_text(encoding="utf-8")
    synd_client_js = (_STATIC_DIR / "js" / "syndication_client.js").read_text(encoding="utf-8")
    synd_feeds_js = (_STATIC_DIR / "js" / "syndication_feeds.js").read_text(encoding="utf-8")
    synd_compose_js = (_STATIC_DIR / "js" / "syndication_compose.js").read_text(encoding="utf-8")
    icons_js = (_STATIC_DIR / "js" / "icons.js").read_text(encoding="utf-8")
    html = re.sub(r'<script src="/static/js/[^"]+"></script>\s*', "", html)
    js_bundle = f"<script>\n{i18n_js}\n{icons_js}\n{appbar_js}\n{shared_ui_js}\n{kb_dialogs_js}\n{ingest_dialogs_js}\n{command_palette_js}\n{synd_live_js}\n{synd_compose_js}\n{synd_feeds_js}\n{synd_client_js}\n</script>"
    html = html.replace("</body>", f"{js_bundle}\n</body>", 1)
    return html


GRAPH_HTML: str = _build_standalone_graph_html()
_SHARED_HTML: str = _build_standalone_shared_html()
SYNDICATION_HTML: str = _build_standalone_syndication_html()

_PAGE_SLOT = re.compile(r"\$\{(?:(t|tj):([a-z0-9_.]+)|html_lang)\}")


def localize_page(page_html: str, locale: str, translate: Callable[[str], str] | None = None) -> str:
    """Resolve translation slots in ``page_html`` using ``locale`` and ``translate``."""

    def _slot(match: re.Match[str]) -> str:
        kind, key = match.group(1), match.group(2)
        if kind is None:
            return locale
        text = translate(key) if translate is not None else key
        if kind == "t":
            return _html.escape(text, quote=True)
        return _json.dumps(text, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")

    return _PAGE_SLOT.sub(_slot, page_html)


def render_shared_page(bundle_html: str, *, doc: dict[str, Any], ga_id: str = "", base_url: str = "", share_token: str = "", site_name: str = "Claire Bible", is_media: bool = False, can_access_raw: bool = False) -> str:
    """Fill slots in a localized shared-document bundle from plain document data."""
    doc_copy = dict(doc or {})
    doc_copy["can_access_raw"] = bool(can_access_raw)
    raw_url = doc_copy.get("url")
    if raw_url and not str(raw_url).lower().startswith(("http://", "https://")):
        doc_copy["url"] = None
    raw_canon = doc_copy.get("canonical_url")
    if raw_canon and not str(raw_canon).lower().startswith(("http://", "https://")):
        doc_copy["canonical_url"] = None
    if isinstance(doc_copy.get("presentation_pdf"), dict):
        pres = dict(doc_copy["presentation_pdf"])
        if pres.get("public_url") and not str(pres["public_url"]).lower().startswith(("http://", "https://")):
            pres["public_url"] = None
        doc_copy["presentation_pdf"] = pres

    doc_id = str(doc_copy.get("id", "") or "").strip()
    ga_tag = render_ga_tag(ga_id, doc_id=doc_id)
    og_tags = render_open_graph_tags(doc_copy, base_url=base_url, share_token=share_token, settings=SimpleNamespace(site_name=site_name), is_media=is_media)

    raw_summary = str(doc_copy.get("summary") or doc_copy.get("detail") or "").strip()
    esc_summary = _html.escape(raw_summary).replace("\n", "<br/>")
    fallback_body = esc_summary
    if not fallback_body:
        plain = re.sub(r"<[^>]*>", " ", str(doc_copy.get("detail_html") or ""))
        plain = re.sub(r"\s+", " ", _html.unescape(plain)).strip()
        fallback_body = _html.escape(plain)
    fallback_body_html = f"<p>{fallback_body}</p>" if fallback_body else ""

    title = (doc_copy.get("title") or "공유 문서").replace("<", "").replace(">", "")
    esc_title = _html.escape(title)
    semantic_fallback = (
        "<noscript>"
        '<article class="cb-semantic-content" style="max-width:860px;margin:2rem auto;padding:1.5rem;line-height:1.7;">'
        f"<h1>{esc_title}</h1>"
        f'<div class="cb-semantic-body">{fallback_body_html}</div>'
        "</article>"
        "</noscript>"
    )

    data = _json.dumps(doc_copy, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    return (
        bundle_html.replace("__DATA__", data)
        .replace("__TITLE__", title)
        .replace("<!-- __GA_TAG__ -->", ga_tag)
        .replace("<!-- __OG_TAGS__ -->", og_tags)
        .replace("<!-- __SEMANTIC_FALLBACK__ -->", semantic_fallback)
    )


def render_graph_page(bundle_html: str, *, base_url: str, repo: str, ga_tag: str, catalog_json: str, safe_sorcerer: str, theme_mode: str, themes_json_safe: str, picker_style_attr: str, picker_title_attr: str, select_style_attr: str, plain_style_attr: str, plain_icon: str, plain_label: str, api_base: str, root_base: str, storage_key: str) -> str:
    """Fill slots in a localized graph workspace bundle from plain DTO values."""
    res = (
        bundle_html.replace("__SOURCE_BASE_URL__", base_url)
        .replace("__GITHUB_REPOSITORY__", repo)
        .replace("<!-- __GA_TAG__ -->", ga_tag)
        .replace("<!-- __I18N_CATALOG__ -->", f'<script id="claire-i18n-catalog" type="application/json">{catalog_json}</script>')
        .replace("__SORCERER__", safe_sorcerer)
        .replace("__OWNER__", safe_sorcerer)
        .replace("__KNOWLEDGE_MANAGER__", safe_sorcerer)
        .replace("__THEME_MODE__", theme_mode)
        .replace("__THEMES_JSON__", themes_json_safe)
        .replace("__THEME_PICKER_STYLE_ATTR__", picker_style_attr)
        .replace("__THEME_PICKER_TITLE_ATTR__", picker_title_attr)
        .replace("__THEME_SELECT_STYLE_ATTR__", select_style_attr)
        .replace("__THEME_PLAIN_STYLE_ATTR__", plain_style_attr)
        .replace("__THEME_PLAIN_ICON__", plain_icon)
        .replace("__THEME_PLAIN_LABEL__", plain_label)
        .replace("__CLAIRE_API_BASE__", api_base)
        .replace("__CLAIRE_ROOT_BASE__", root_base)
        .replace("__CLAIRE_STORAGE_KEY__", storage_key)
    )
    if '<meta name="claire-api-base"' not in res:
        meta_tags = (
            f'<meta name="claire-api-base" content="{api_base}"/>\n'
            f'<meta name="claire-root-base" content="{root_base}"/>\n'
            f'<meta name="claire-storage-key" content="{storage_key}"/>'
        )
        res = res.replace("<head>", f"<head>\n{meta_tags}")
    return res


def render_syndication_page(bundle_html: str, *, base_url: str, repo: str, ga_tag: str, safe_sorcerer: str, theme_mode: str, active_kb_id: str, safe_scope: str, themes_json_safe: str, storage_key: str, catalog_json: str, home_url: str | None = None, is_admin_viewer: bool = False) -> str:
    """Fill slots in a localized syndication bundle from plain DTO values."""
    rendered = (
        bundle_html.replace("__SOURCE_BASE_URL__", base_url)
        .replace("__GITHUB_REPOSITORY__", repo)
        .replace("<!-- __GA_TAG__ -->", ga_tag)
        .replace("__SORCERER__", safe_sorcerer)
        .replace("__OWNER__", safe_sorcerer)
        .replace("__KNOWLEDGE_MANAGER__", safe_sorcerer)
        .replace("__THEME_MODE__", theme_mode)
        .replace("__ACTIVE_THEME_ID__", active_kb_id)
        .replace("__AUTH_SCOPE__", safe_scope)
        .replace("__THEMES_JSON__", themes_json_safe)
        .replace("__CLAIRE_STORAGE_KEY__", storage_key)
        .replace("<!-- __I18N_CATALOG__ -->", f'<script id="claire-i18n-catalog" type="application/json">{catalog_json}</script>')
    )
    if home_url:
        rendered = rendered.replace('href="/" id="appbar-brand"', f'href="{home_url}" id="appbar-brand"').replace(
            'id="homelinkbtn" class="bar-icon-btn" href="/"',
            f'id="homelinkbtn" class="bar-icon-btn" href="{home_url}"',
        )
    if is_admin_viewer:
        rendered = rendered.replace(
            '<a id="consolelinkbtn" class="app-menu-item" href="/settings/reader"',
            '<a id="consolelinkbtn" class="app-menu-item" href="/admin"',
        )
    return rendered
