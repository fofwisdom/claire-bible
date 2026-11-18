"""통합 콘솔 라우트: ``/admin``, ``/admin/*``, ``/settings``, ``/settings/*``, ``/api/admin/mcp/telemetry`` (설계 D1–D3, P1).

- ``/admin`` 및 ``/admin/*``는 관리자(``server_role == "admin"``, ``scope == "owner"``) 전용이며 비관리자 요청에는 404를 돌려준다(B1, N5).
- ``/settings`` 및 ``/settings/*``는 계정 소유자(``scope == "owner"``) 전용 ``_system("owner")`` 표면이다(D1).
- 관리자가 ``/settings/tokens``에 접근하면 ``/admin/tokens``로 302 리다이렉트하고, 통합 사이드바에서도 중복 노출하지 않는다(D3). 폐지된 ``/settings/themes``는 308로 응답한다(관리자는 ``/admin/themes``, 그 외 계정은 ``/settings/kb``).
- 비관리자 응답 HTML에는 ``/admin/*`` 네비게이션 링크나 관리자 전용 마크업을 절대 포함하지 않는다.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlencode

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from ... import ids, ui_chrome
from ...i18n import SUPPORTED_LOCALES, get_locale, t
from ...ontology.label_language import ENTITY_LANGUAGES
from ...store import accounts as accounts_store
from ...store.telemetry import query_mcp_telemetry
from ...store.knowledge_base import KnowledgeBaseInfo, KnowledgeBaseManager, KnowledgeBaseNotFound
from ..principal import Principal, request_principal
from .admin import is_admin
from .common import (
    AccountsContext,
    FORM_PAGE_REFERRER_POLICY,
    escape,
    format_ts,
    json_error,
    not_found,
    open_db,
    prototype_badge,
    prototype_note,
    t_code,
    render_template,
)
from .console_federation_panels import settings_federation_body
from .console_syndication_panels import SYNDICATION_SECTION_SLUGS, SettingsSectionRender, build_syndication_section

# P3-a: Group 1 (내 계정 · 워크스페이스) 섹션이 쓰는 모듈.
from ...accounts import server_tier as server_tier_mod
from ...accounts.provider_policy import MEMBER_KEY_PROVIDERS, ServerTier
from ...store import auth_db
from ...store import usage_ledger
from . import features as feature_routes
from . import keys as keys_routes
from .admin import account_role_text, account_status_text
from .common import icon_html, open_auth_db

# 유저 단위 기능 화면(api.accounts_routes.features가 본문을 그리고 console_features.js가 동작을 맡는다).
FEATURE_SETTINGS_SLUGS: frozenset[str] = frozenset({"telegram", "bundles", "backups"})

ADMIN_INDEX_PATH = "/admin"
SETTINGS_INDEX_PATH = "/settings"
ADMIN_MCP_TELEMETRY_API_PATH = "/api/admin/mcp/telemetry"


@dataclass(frozen=True, slots=True)
class ConsoleSectionSpec:
    """One section of the console. The text comes from the catalogs (static/locales/{en,ko}.json): the keys ``console.section.<area>.<slug>.{nav,title,badge,desc}`` and ``console.group.<group_id>``. The properties give the text of the active locale."""

    slug: str
    path: str
    area: str  # "settings" | "admin"
    group_id: str
    theme_scoped: bool = False
    # Name of a line icon in static/js/icons.js (HD4 of HOME_OVERVIEW_AND_CONTEXT_PANEL_DESIGN.md).
    icon: str = ""

    def _text(self, field: str) -> str:
        return t(f"console.section.{self.area}.{self.slug}.{field}")

    @property
    def group_label(self) -> str:
        return t(f"console.group.{self.group_id}")

    @property
    def nav_label(self) -> str:
        return self._text("nav")

    @property
    def title(self) -> str:
        return self._text("title")

    @property
    def badge(self) -> str:
        return self._text("badge")

    @property
    def description(self) -> str:
        return self._text("desc")


SETTINGS_GROUP_1_SPECS: tuple[ConsoleSectionSpec, ...] = (
    ConsoleSectionSpec(
        slug="account",
        path="/settings/account",
        area="settings",
        group_id="workspace",
        icon="user",
    ),
    ConsoleSectionSpec(
        slug="federation",
        path="/settings/federation",
        area="settings",
        group_id="workspace",
        icon="globe",
    ),
    ConsoleSectionSpec(
        slug="keys",
        path="/settings/keys",
        area="settings",
        group_id="workspace",
        icon="lock",
    ),
    ConsoleSectionSpec(
        slug="tokens",
        path="/settings/tokens",
        area="settings",
        group_id="workspace",
        icon="key",
    ),
    ConsoleSectionSpec(
        slug="telegram",
        path="/settings/telegram",
        area="settings",
        group_id="workspace",
        icon="bot",
    ),
    ConsoleSectionSpec(
        slug="bundles",
        path="/settings/bundles",
        area="settings",
        group_id="workspace",
        icon="package",
    ),
    ConsoleSectionSpec(
        slug="backups",
        path="/settings/backups",
        area="settings",
        group_id="workspace",
        icon="save",
    ),
)

SETTINGS_KB_SPECS: tuple[ConsoleSectionSpec, ...] = (
    ConsoleSectionSpec(
        slug="kb",
        path="/settings/kb",
        area="settings",
        group_id="kb",
        icon="folder",
    ),
    ConsoleSectionSpec(
        slug="kb_manage",
        path="/settings/kb/manage",
        area="settings",
        group_id="kb",
        icon="check-circle",
    ),
)

SETTINGS_GROUP_2_SPECS: tuple[ConsoleSectionSpec, ...] = (
    ConsoleSectionSpec(
        slug="reader",
        path="/settings/reader",
        area="settings",
        group_id="syndication",
        icon="book",
        theme_scoped=True,
    ),
    ConsoleSectionSpec(
        slug="connections",
        path="/settings/connections",
        area="settings",
        group_id="syndication",
        icon="plug",
        theme_scoped=True,
    ),
    ConsoleSectionSpec(
        slug="feeds",
        path="/settings/feeds",
        area="settings",
        group_id="syndication",
        icon="broadcast",
        theme_scoped=True,
    ),
    ConsoleSectionSpec(
        slug="tags",
        path="/settings/tags",
        area="settings",
        group_id="syndication",
        icon="tag",
        theme_scoped=True,
    ),
    ConsoleSectionSpec(
        slug="rules",
        path="/settings/rules",
        area="settings",
        group_id="syndication",
        icon="bolt",
        theme_scoped=True,
    ),
    ConsoleSectionSpec(
        slug="outbound",
        path="/settings/outbound",
        area="settings",
        group_id="syndication",
        icon="repeat",
        theme_scoped=True,
    ),
    ConsoleSectionSpec(
        slug="opml",
        path="/settings/opml",
        area="settings",
        group_id="syndication",
        icon="package",
        theme_scoped=True,
    ),
)

SETTINGS_GROUP_3_SPECS: tuple[ConsoleSectionSpec, ...] = (
    ConsoleSectionSpec(
        slug="mcp",
        path="/settings/mcp",
        area="settings",
        group_id="agents",
        icon="puzzle",
        theme_scoped=True,
    ),
    ConsoleSectionSpec(
        slug="webmcp",
        path="/settings/webmcp",
        area="settings",
        group_id="agents",
        icon="graph",
        theme_scoped=True,
    ),
)

ADMIN_KB_SPEC = ConsoleSectionSpec(
    slug="kb",
    path="/admin/kb",
    area="admin",
    group_id="admin_server",
    icon="folder",
)

ADMIN_SIDEBAR_GROUP_4_SPECS: tuple[ConsoleSectionSpec, ...] = (
    ConsoleSectionSpec(
        slug="server",
        path="/admin/server",
        area="admin",
        group_id="admin_server",
        icon="server",
    ),
    ConsoleSectionSpec(
        slug="federation",
        path="/admin/federation",
        area="admin",
        group_id="admin_server",
        icon="globe",
    ),
    ConsoleSectionSpec(
        slug="accounts",
        path="/admin/accounts",
        area="admin",
        group_id="admin_server",
        icon="users",
    ),
    ADMIN_KB_SPEC,
    ConsoleSectionSpec(
        slug="idp",
        path="/admin/idp",
        area="admin",
        group_id="admin_server",
        icon="id",
    ),
)

ADMIN_CONSOLE_GROUP_5_SPECS: tuple[ConsoleSectionSpec, ...] = (
    ConsoleSectionSpec(
        slug="providers",
        path="/admin/providers",
        area="admin",
        group_id="admin_system",
        icon="bolt",
    ),
    ConsoleSectionSpec(
        slug="limits",
        path="/admin/limits",
        area="admin",
        group_id="admin_system",
        icon="ruler",
    ),
    ConsoleSectionSpec(
        slug="themes",
        path="/admin/themes",
        area="admin",
        group_id="admin_system",
        icon="folder",
    ),
    ConsoleSectionSpec(
        slug="tokens",
        path="/admin/tokens",
        area="admin",
        group_id="admin_system",
        icon="key",
    ),
    ConsoleSectionSpec(
        slug="mcp",
        path="/admin/mcp",
        area="admin",
        group_id="admin_system",
        icon="pulse",
    ),
    ConsoleSectionSpec(
        slug="backups",
        path="/admin/backups",
        area="admin",
        group_id="admin_system",
        icon="restore",
    ),
    ConsoleSectionSpec(
        slug="storage",
        path="/admin/storage",
        area="admin",
        group_id="admin_system",
        icon="database",
    ),
)

ALL_SETTINGS_SPECS: tuple[ConsoleSectionSpec, ...] = (
    *SETTINGS_GROUP_1_SPECS,
    *SETTINGS_KB_SPECS,
    *SETTINGS_GROUP_2_SPECS,
    *SETTINGS_GROUP_3_SPECS,
)
ALL_ADMIN_SPECS: tuple[ConsoleSectionSpec, ...] = (
    *ADMIN_SIDEBAR_GROUP_4_SPECS,
    *ADMIN_CONSOLE_GROUP_5_SPECS,
)
SETTINGS_BY_PATH: dict[str, ConsoleSectionSpec] = {spec.path: spec for spec in ALL_SETTINGS_SPECS}
ADMIN_CONSOLE_BY_PATH: dict[str, ConsoleSectionSpec] = {
    **{spec.path: spec for spec in ADMIN_CONSOLE_GROUP_5_SPECS},
    ADMIN_KB_SPEC.path: ADMIN_KB_SPEC,
}
ADMIN_SPEC_BY_SLUG: dict[str, ConsoleSectionSpec] = {spec.slug: spec for spec in ALL_ADMIN_SPECS}


def _is_owner(principal: Principal) -> bool:
    return principal.scope == "owner" and principal.account_id is not None


def _redirect(location: str, *, status_code: int = 302) -> Response:
    return Response(
        status_code=status_code,
        headers={
            "Location": location,
            "Cache-Control": "no-store",
            "Referrer-Policy": FORM_PAGE_REFERRER_POLICY,
        },
    )


def _append_theme_query(path: str, theme_query: str | None) -> str:
    if not theme_query:
        return path
    return f"{path}?{urlencode({'kb': theme_query})}"


def _kb_manager(ctx: AccountsContext) -> KnowledgeBaseManager:
    data_dir = ctx.data_dir if ctx.data_dir is not None else ctx.db_file.parent
    return KnowledgeBaseManager(SimpleNamespace(db_file=ctx.db_file, db_path=str(ctx.db_file), data_dir=data_dir, multi_theme=True))


def _resolve_caller_theme_and_list(
    ctx: AccountsContext,
    *,
    account_id: int,
    raw_theme: str | None,
) -> tuple[KnowledgeBaseInfo | None, list[KnowledgeBaseInfo], str | None] | None:
    """``?kb=``(번호)가 주어졌으면 호출자 계정의 지식 베이스인지 엄격히 검증한다(C1, CT23). 없으면 콘솔의 주인인 계정의 개인 지식 베이스다: ``/settings/*``는 그 계정의 콘솔이다.

    유효하지 않거나 다른 계정의 테마면 ``None``을 돌려줘 호출자가 404를 내게 한다.
    """
    tm = _kb_manager(ctx)
    owned = tm.list_account_kbs(account_id)
    if raw_theme is not None:
        cleaned = raw_theme.strip()
        if not cleaned:
            return None
        try:
            resolved = tm.get_kb_by_reference(cleaned)
        except KnowledgeBaseNotFound:
            return None
        if resolved.account_id != account_id:
            return None
        return resolved, owned, cleaned
    default_theme = owned[0] if owned else None
    for t in owned:
        if t.is_personal:
            default_theme = t
            break
    return default_theme, owned, None


def _is_shared_kb_number(ctx: AccountsContext, number: int) -> bool:
    try:
        return _kb_manager(ctx).get_kb_by_number(number).is_shared
    except KnowledgeBaseNotFound:
        return False


def _account_context_info(ctx: AccountsContext, account_id: int) -> tuple[accounts_store.ServerMode, accounts_store.Account]:
    conn = open_db(ctx)
    try:
        store = accounts_store.AccountStore(conn)
        return store.server_mode(), store.get(account_id)
    finally:
        conn.close()


def _build_sidebar_html(
    *,
    active_path: str,
    admin_viewer: bool,
    theme_query: str | None,
) -> str:
    groups: list[tuple[str, str, list[ConsoleSectionSpec]]] = [
        (
            "workspace",
            t("console.group.workspace"),
            [
                spec
                for spec in SETTINGS_GROUP_1_SPECS
                # D3: admin manages tokens in /admin/tokens.
                if not (admin_viewer and spec.slug == "tokens")
            ],
        ),
        ("kb", t("console.group.kb"), list(SETTINGS_KB_SPECS)),
        ("syndication", t("console.group.syndication"), list(SETTINGS_GROUP_2_SPECS)),
        ("agents", t("console.group.agents"), list(SETTINGS_GROUP_3_SPECS)),
    ]
    if admin_viewer:
        groups.append(("admin_server", t("console.group.admin_server"), list(ADMIN_SIDEBAR_GROUP_4_SPECS)))
        groups.append(("admin_system", t("console.group.admin_system"), list(ADMIN_CONSOLE_GROUP_5_SPECS)))

    parts: list[str] = []
    for group_id, group_label, specs in groups:
        has_active = any(
            spec.path == active_path or (spec.slug == "kb_manage" and active_path.startswith("/settings/kb/"))
            for spec in specs
        )
        group_cls = "console-nav-group has-active" if has_active else "console-nav-group"
        list_id = f"console-grp-{group_id}"
        parts.append(f'<div class="{group_cls}" data-nav-group="{escape(group_id)}">')
        parts.append(f'  <button type="button" class="console-nav-group-btn" data-nav-group-toggle="{escape(group_id)}" aria-expanded="true" aria-controls="{escape(list_id)}"><span class="console-nav-group-label">{escape(group_label)}</span><span class="console-nav-chev" aria-hidden="true">▾</span></button>')
        parts.append(f'  <ul id="{escape(list_id)}" class="console-nav-list" role="list">')
        for spec in specs:
            href = _append_theme_query(spec.path, theme_query) if spec.theme_scoped else spec.path
            is_cur = spec.path == active_path or (spec.slug == "kb_manage" and active_path.startswith("/settings/kb/"))
            current = ' aria-current="page"' if is_cur else ""
            parts.append(f'    <li><a href="{escape(href)}" class="console-nav-link" data-nav-section="{escape(spec.slug)}"{current}>{icon_html(spec.icon)}<span>{escape(spec.nav_label)}</span></a></li>')
        parts.append("  </ul>")
        parts.append("</div>")
    return "\n      ".join(parts)


def _build_theme_selector_html(
    *,
    spec: ConsoleSectionSpec,
    owned_themes: list[KnowledgeBaseInfo],
    selected_theme: KnowledgeBaseInfo | None,
) -> str:
    if not spec.theme_scoped or not owned_themes:
        return ""
    options: list[str] = []
    selected_id = selected_theme.id if selected_theme is not None else None
    for theme in owned_themes:
        ref = ids.format_public_id(theme.number) if theme.number is not None else str(theme.id)
        sel = " selected" if theme.id == selected_id else ""
        options.append(f'<option value="{escape(ref)}"{sel}>{escape(theme.icon)} {escape(theme.label)}</option>')
    # The same theme picker as in the application bar of the workspace (static/css/appbar.css).
    label = escape(t("nav.theme_select"))
    return (
        '<div class="theme-picker-wrap">'
        f'<label for="console-theme-select" class="sr-only">{label}</label>'
        '<div class="theme-select-control">'
        f'<select id="console-theme-select" class="theme-select" aria-label="{label}">{"".join(options)}</select>'
        '<span class="theme-caret" aria-hidden="true">▾</span>'
        "</div></div>"
    )


def _build_admin_section_body_and_extra(ctx: AccountsContext, slug: str, principal: Principal | None = None) -> tuple[str, dict[str, Any]]:
    if slug == "backups" and principal is not None:
        return feature_routes.admin_backups_body(ctx, principal=principal), {}

    if slug == "storage":
        from .storage import admin_storage_body

        body, extra = admin_storage_body(ctx)
        return body, {"admin_storage": extra}

    if slug == "providers":
        body = (
            f'<div class="card" id="admin-providers-card" aria-label="{escape(t("console.admin.providers.label"))}">'
            '<div id="admin-providers-host" data-admin-panel-host="providers">'
            f'<p class="hint">{escape(t("console.admin.providers.loading"))}</p>'
            "</div>"
            "</div>"
            f'<section class="card" id="admin-providers-embed-card" aria-label="{escape(t("console.admin.providers.label"))}">'
            '<div id="admin-providers-embed-host"></div>'
            "</section>"
        )
        return body, {}

    if slug == "kb":
        body = (
            f'<section class="card" id="admin-kb-card" aria-label="{escape(t("console.section.admin.kb.title"))}">'
            '<div id="admin-kb-host"></div>'
            "</section>"
        )
        return body, {}

    if slug == "limits":
        body = (
            f'<div class="card" id="admin-limits-card" aria-label="{escape(t("console.admin.limits.label"))}">'
            '<div id="admin-limits-host" data-admin-panel-host="limits">'
            f'<p class="hint">{escape(t("console.admin.limits.loading"))}</p>'
            "</div>"
            "</div>"
            # The review of the shadow records of the same-subject source merge (EXPANSION.md §3.1). console_admin.js fills it from GET /api/admin/expansion/shadow.
            f'<div class="card" id="admin-expansion-review-card" aria-label="{escape(t("console.js.admin.expansion_review.title"))}">'
            '<div id="admin-expansion-review-host">'
            f'<p class="hint">{escape(t("console.admin.limits.loading"))}</p>'
            "</div>"
            "</div>"
        )
        return body, {}

    if slug == "themes":
        # The console of the theme judgement (SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md Section 10.3 rule 4). console_admin.js fills the host from GET /api/admin/themes and GET /api/admin/reputations.
        body = (
            f'<section class="card" id="admin-themes-card" aria-label="{escape(t("console.section.admin.themes.title"))}">'
            '<div id="admin-themes-host" data-admin-panel-host="themes">'
            f'<p class="hint">{escape(t("console.common.loading"))}</p>'
            "</div>"
            "</section>"
        )
        return body, {}

    if slug == "tokens":
        conn = open_db(ctx)
        try:
            oauth_rows = conn.execute(
                "SELECT c.client_id, c.client_name, c.redirect_uris, c.created_at, MAX(t.expires_at) "
                "FROM oauth_clients c LEFT JOIN oauth_tokens t ON c.client_id = t.client_id "
                "GROUP BY c.client_id ORDER BY c.created_at DESC"
            ).fetchall()
        except Exception:
            oauth_rows = []
        finally:
            conn.close()

        client_trs: list[str] = []
        oauth_clients_list: list[dict[str, Any]] = []
        for cid, cname, ruris_raw, created_at, max_exp in oauth_rows:
            try:
                ruris = json.loads(ruris_raw) if isinstance(ruris_raw, str) else []
            except Exception:
                ruris = [str(ruris_raw)] if ruris_raw else []
            ruri_str = ", ".join(str(u) for u in ruris) if ruris else "-"
            created_str = format_ts(created_at)
            exp_str = format_ts(max_exp) if max_exp else t("console.admin.tokens.no_token")
            client_trs.append(
                f"<tr>"
                f"<td><strong>{escape(cname or 'External MCP Client')}</strong></td>"
                f'<td><code class="pref-mono">{escape(cid)}</code><br/><span class="pref-mono" style="color:var(--muted)">{escape(ruri_str)}</span></td>'
                f"<td>{escape(created_str)}</td>"
                f"<td>{escape(exp_str)}</td>"
                f"</tr>"
            )
            oauth_clients_list.append(
                {
                    "client_id": cid,
                    "client_name": cname or "External MCP Client",
                    "redirect_uris": ruris,
                    "created_at": created_at,
                    "expires_at": max_exp,
                }
            )
        clients_tbody = (
            "".join(client_trs)
            if client_trs
            else f'<tr><td colspan="4" style="color:var(--muted);text-align:center;">{escape(t("console.admin.tokens.clients_empty"))}</td></tr>'
        )
        body = (
            f'<section class="card" id="admin-tokens-manager-card" aria-label="{escape(t("console.admin.tokens.manager_label"))}">'
            '<div id="admin-tokens-host" data-admin-panel-host="tokens">'
            f'<p class="hint">{escape(t("console.admin.tokens.loading"))}</p>'
            "</div>"
            "</section>"
            f'<section class="card" id="admin-oauth-clients-card" aria-label="{escape(t("console.admin.tokens.clients_label"))}">'
            f'<h2><span data-icon="lock"></span> {escape(t("console.admin.tokens.clients_title"))}</h2>'
            f'<p class="hint">{t_code("console.admin.tokens.clients_hint", register="/oauth/register", authorize="/oauth/authorize")}</p>'
            '<div class="pref-table-wrap">'
            '<table class="pref-table">'
            f'<thead><tr><th>{escape(t("console.oauth.col_client_name"))}</th><th>client_id · redirect_uris</th><th>{escape(t("console.admin.tokens.col_registered"))}</th><th>{escape(t("console.admin.tokens.col_expires"))}</th></tr></thead>'
            f'<tbody id="pref-mcp-clients-tbody">{clients_tbody}</tbody>'
            "</table>"
            "</div>"
            "</section>"
            f'<section class="card" id="admin-token-policy-card" aria-label="{escape(t("console.admin.tokens.policy_label"))}">'
            f'<h2><span data-icon="shield"></span> {escape(t("console.admin.tokens.policy_title"))} {prototype_badge("admin-token-policy-summary", "static")}</h2>'
            f'{prototype_note("admin-token-policy-summary", t("console.admin.tokens.policy_note"))}'
            '<dl>'
            f'<dt>{t_code("console.admin.tokens.policy_permanent", prefix="cb_…")}</dt><dd>{t_code("console.admin.tokens.policy_permanent_desc", owner="owner", write="write", readonly="readonly", revoke="/tokens/revoke")}</dd>'
            f'<dt>{escape(t("console.admin.tokens.policy_oauth"))}</dt><dd>{t_code("console.admin.tokens.policy_oauth_desc", readonly="readonly")}</dd>'
            f'<dt>{escape(t("console.admin.tokens.policy_session"))}</dt><dd>{t_code("console.admin.tokens.policy_session_desc", reauth="/api/auth/reauth")}</dd>'
            "</dl>"
            "</section>"
        )
        return body, {"oauth_clients": oauth_clients_list}

    if slug == "mcp":
        body = (
            f'<section class="card" id="admin-mcp-telemetry-card" aria-label="{escape(t("console.admin.mcp.audit_label"))}">'
            '<div style="display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap">'
            f'<h2><span data-icon="list"></span> {t_code("console.admin.mcp.audit_title", table="mcp_telemetry")}</h2>'
            '<div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">'
            f'<label style="font-size:12px;font-weight:600">{escape(t("console.common.status"))} <select id="admin-mcp-filter-status" class="pref-select">'
            f'<option value="">{escape(t("console.admin.mcp.filter_all"))}</option>'
            '<option value="SUCCESS">SUCCESS</option>'
            '<option value="success">success</option>'
            '<option value="ERROR">ERROR</option>'
            '<option value="UNAUTHORIZED">UNAUTHORIZED</option>'
            '<option value="auth_error">auth_error</option>'
            "</select></label>"
            f'<label style="font-size:12px;font-weight:600">{escape(t("console.admin.mcp.filter_tool"))} <input type="text" id="admin-mcp-filter-tool" class="pref-input" placeholder="{escape(t("console.admin.mcp.filter_tool_placeholder"))}" style="width:130px"/></label>'
            f'<label style="font-size:12px;font-weight:600">{escape(t("console.admin.mcp.filter_limit"))} <select id="admin-mcp-filter-limit" class="pref-select">'
            f'<option value="25">{escape(t("console.admin.mcp.limit_25"))}</option>'
            f'<option value="50" selected>{escape(t("console.admin.mcp.limit_50"))}</option>'
            f'<option value="100">{escape(t("console.admin.mcp.limit_100"))}</option>'
            f'<option value="200">{escape(t("console.admin.mcp.limit_200"))}</option>'
            "</select></label>"
            f'<button type="button" id="admin-mcp-refresh-btn" class="pref-btn">{escape(t("console.common.refresh"))}</button>'
            "</div>"
            "</div>"
            f'<p class="hint">{t_code("console.admin.mcp.audit_hint", endpoint="GET /api/admin/mcp/telemetry")}</p>'
            '<p class="result" id="admin-mcp-telemetry-status" role="status" aria-live="polite"></p>'
            '<div class="pref-table-wrap">'
            '<table class="pref-table">'
            f'<thead><tr><th>{escape(t("console.admin.mcp.col_time"))}</th><th>call_type</th><th>tool_name</th><th>status</th><th>duration_ms</th><th>response_bytes</th><th>client_id</th></tr></thead>'
            f'<tbody id="pref-mcp-audit-tbody"><tr><td colspan="7" style="color:var(--muted);text-align:center;">{escape(t("console.admin.mcp.audit_loading"))}</td></tr></tbody>'
            "</table>"
            "</div>"
            "</section>"
            f'<section class="card" id="admin-mcp-rbac-card" aria-label="{escape(t("console.admin.mcp.rbac_label"))}">'
            f'<h2><span data-icon="compass"></span> {escape(t("console.admin.mcp.rbac_title"))} {prototype_badge("admin-mcp-rbac-table", "static")}</h2>'
            f'{prototype_note("admin-mcp-rbac-table", t("console.admin.mcp.rbac_note"))}'
            f'<p class="hint">{t_code("console.admin.mcp.rbac_hint", endpoint="/mcp")}</p>'
            '<div class="pref-table-wrap">'
            '<table class="pref-table">'
            f'<thead><tr><th>{escape(t("console.admin.mcp.rbac_col_scope"))}</th><th>{escape(t("console.admin.mcp.rbac_col_design"))}</th><th>{escape(t("console.admin.mcp.rbac_col_current"))}</th><th>{escape(t("console.admin.mcp.rbac_col_result"))}</th></tr></thead>'
            '<tbody id="pref-mcp-rbac-tbody">'
            f'<tr><td><code class="pref-mono">owner</code></td><td>{escape(t("console.admin.mcp.rbac_owner_design"))}</td><td>{escape(t("console.admin.mcp.rbac_owner_current"))}</td><td><span class="pref-pill pref-pill-mcp">{escape(t("console.admin.mcp.rbac_match"))}</span></td></tr>'
            f'<tr><td><code class="pref-mono">write</code></td><td>{escape(t("console.admin.mcp.rbac_write_design"))}</td><td>{escape(t("console.admin.mcp.rbac_readonly_tools"))}</td><td><span class="pref-pill pref-pill-mcp">{escape(t("console.admin.mcp.rbac_review"))}</span></td></tr>'
            f'<tr><td><code class="pref-mono">readonly</code></td><td>{escape(t("console.admin.mcp.rbac_readonly_design"))}</td><td>{escape(t("console.admin.mcp.rbac_readonly_tools"))}</td><td><span class="pref-pill pref-pill-mcp">{escape(t("console.admin.mcp.rbac_match"))}</span></td></tr>'
            f'<tr><td><code class="pref-mono">anonymous</code></td><td>{escape(t("console.admin.mcp.rbac_anonymous_design"))}</td><td>{escape(t("console.admin.mcp.rbac_anonymous_current"))}</td><td><span class="pref-pill">{escape(t("console.admin.mcp.rbac_stricter"))}</span></td></tr>'
            "</tbody>"
            "</table>"
            "</div>"
            "</section>"
            f'<section class="card" id="admin-webmcp-policy-card" aria-label="{escape(t("console.admin.mcp.webmcp_label"))}">'
            f'<h2><span data-icon="graph"></span> {t_code("console.admin.mcp.webmcp_title", header="Permissions-Policy")} {prototype_badge("admin-webmcp-global-switch", "client_only")}</h2>'
            f'{prototype_note("admin-webmcp-global-switch", t("console.admin.mcp.webmcp_note"))}'
            '<div class="pref-row">'
            '<div class="pref-row-label">'
            f'<strong>{t_code("console.admin.mcp.webmcp_switch", api="document.modelContext")}</strong>'
            f'<span>{t_code("console.admin.mcp.webmcp_switch_desc", header="Permissions-Policy: tools=()")}</span>'
            "</div>"
            f'<input id="pref-webmcp-enabled" type="checkbox" switch checked aria-label="{escape(t("console.admin.mcp.webmcp_switch_label"))}"/>'
            "</div>"
            f'<p class="hint" id="admin-webmcp-policy-hint">{t_code("console.admin.mcp.webmcp_state_hint", path="/settings/webmcp")}</p>'
            "</section>"
            f'<section class="card" id="admin-support-bundle-card" aria-label="{escape(t("console.admin.mcp.bundle_label"))}">'
            f'<h2><span data-icon="package"></span> {t_code("console.admin.mcp.bundle_title", endpoint="POST /support/bundle")}</h2>'
            f'<p class="hint">{escape(t("console.admin.mcp.bundle_hint"))}</p>'
            '<form id="admin-support-bundle-form" data-support-bundle-form style="display:flex;gap:10px;align-items:flex-end;flex-wrap:wrap">'
            f'<label style="display:flex;flex-direction:column;gap:4px;font-size:12px;font-weight:600">{escape(t("console.admin.mcp.bundle_days"))}'
            '<input type="number" name="days" id="admin-support-bundle-days" class="pref-input" min="1" max="30" value="7" style="width:90px" required/>'
            "</label>"
            f'<button type="submit" class="pref-btn pref-btn-accent" id="admin-support-bundle-btn"><span data-icon="pulse"></span> {escape(t("console.admin.mcp.bundle_submit"))}</button>'
            "</form>"
            '<p class="result" id="admin-support-bundle-result" data-support-bundle-result role="status" aria-live="polite"></p>'
            "</section>"
        )
        return body, {}

    return (
        f'<div class="console-placeholder" data-area="admin" data-slug="{escape(slug)}">'
        f'<p class="console-desc">{t_code("console.admin.placeholder_path", path=f"/admin/{slug}")}</p>'
        "</div>"
    ), {}


# Catalog keys of the notices of /settings/account?notice=. A value that is not in this map shows nothing: the value is a display hint, not an identifier.
SETTINGS_ACCOUNT_NOTICE_TEXT = {
    "password_not_saved": "console.settings.account.notice.password_not_saved",
    "already_linked": "console.settings.account.notice.already_linked",
    "identity_linked": "console.settings.account.notice.identity_linked",
}


def _build_settings_account_section(
    ctx: AccountsContext,
    *,
    spec: ConsoleSectionSpec,
    principal: Principal,
    mode: accounts_store.ServerMode,
    account: accounts_store.Account,
    owned_themes: list[KnowledgeBaseInfo],
    notice: str | None = None,
) -> SettingsSectionRender:
    """Group 1 섹션(``account``, ``keys``, ``themes``, ``tokens``) 본문 (설계 P3-a).

    - 기존 API만 쓴다(C1): ``/api/me``, ``/api/me/password``, ``/api/auth/reauth``, ``/api/me/identities*``, ``POST /graph/bundle``, ``/api/me/keys*``, ``/themes``, ``/tokens``.
    - 싱글 모드에서는 USERNAME과 ``profile_public`` 행을 내지 않는다(S9).
    - 비관리자 응답에는 ``/admin/*`` 링크를 넣지 않는다(불변식 3). ``/admin/providers`` 링크는 관리자 뷰어에게만 준다.
    """
    if principal.account_id is None or principal.account_id != account.id:
        raise RuntimeError("settings Group 1 requires the caller's own account principal")
    admin_viewer = is_admin(principal)
    if spec.slug == "account":
        body, extra = _settings_account_body(ctx, principal=principal, mode=mode, account=account, owned_themes=owned_themes, admin_viewer=admin_viewer, notice=notice)
    elif spec.slug == "federation":
        body, extra = settings_federation_body(ctx, mode=mode, account=account)
    elif spec.slug == "keys":
        body, extra = _settings_keys_body(ctx, principal=principal, mode=mode, account=account, admin_viewer=admin_viewer)
    elif spec.slug == "kb":
        # An account has one personal knowledge base (design Section 3). The page shows its number.
        personal_num = next((ids.format_public_id(kb.number) for kb in owned_themes if kb.is_personal and kb.number is not None), "")
        return SettingsSectionRender(
            body_html='<div id="settings-kb-host"></div>',
            extra_bootstrap={"account": {"public_id": str(account.public_id), "display_name": account.display_name, "username": account.username}, "personal_kb_number": personal_num},
            extra_scripts_html='<script src="/static/js/console_kb.js" defer></script>',
        )
    elif spec.slug == "kb_manage":
        return SettingsSectionRender(
            body_html='<div id="settings-kb-manage-host"></div>',
            extra_bootstrap={"account": {"public_id": str(account.public_id), "display_name": account.display_name, "username": account.username}},
            extra_scripts_html='<script src="/static/js/console_kb.js" defer></script>',
        )
    elif spec.slug == "tokens":
        if admin_viewer:
            raise RuntimeError("/settings/tokens must redirect the admin to /admin/tokens")
        body, extra = _settings_tokens_body(ctx, account=account)
    elif spec.slug in FEATURE_SETTINGS_SLUGS:
        if spec.slug == "telegram":
            body = feature_routes.settings_telegram_body(ctx, account=account)
        elif spec.slug == "bundles":
            body = feature_routes.settings_bundles_body(ctx, account=account, owned_themes=owned_themes)
        else:
            body = feature_routes.settings_backups_body(ctx, account=account)
        return SettingsSectionRender(
            body_html=body,
            extra_bootstrap={},
            extra_scripts_html='<script src="/static/js/console_features.js" defer></script>',
        )
    else:
        raise KeyError(spec.slug)
    return SettingsSectionRender(
        body_html=f'<div class="settings-group1" data-settings-section="{escape(spec.slug)}">{body}</div>',
        extra_bootstrap={"settings_account": extra},
        extra_scripts_html='<script src="/static/js/console_account.js" defer></script>',
    )


def _settings_format_bytes(value: int) -> str:
    if value < 1024:
        return f"{value} B"
    if value < 1024 * 1024:
        return f"{value / 1024:.1f} KB"
    return f"{value / (1024 * 1024):.1f} MB"


def _settings_account_usage(ctx: AccountsContext, account_id: int) -> dict[str, Any]:
    """호출자 계정의 자원 사용량과 서버 등급. 다른 계정 행은 읽지 않는다."""
    conn = open_db(ctx)
    try:
        tier = server_tier_mod.load_server_tier(conn)
        documents = server_tier_mod.account_document_count(conn, account_id)
        storage_bytes = server_tier_mod.account_storage_bytes(conn, account_id)
    finally:
        conn.close()
    auth = open_auth_db(ctx)
    try:
        usage = usage_ledger.get_usage(auth, account_id=account_id)
    finally:
        auth.close()
    return {
        "tier": tier,
        "documents": documents,
        "storage_bytes": storage_bytes,
        "period": usage.period,
        "tokens_total": usage.tokens_total,
        "daily_day": usage.daily_day,
        "daily_ingests": usage.daily_ingests,
    }


def _settings_tier_rows_html(tier: ServerTier) -> str:
    features = ", ".join(sorted(tier.features)) if tier.features else t("console.common.none")
    rows = (
        (t("console.tier.provider"), t("console.tier.provider_none") if tier.provider == "none" else tier.provider),
        (t("console.tier.model"), tier.model or "-"),
        (t("console.tier.monthly_tokens"), f"{tier.monthly_tokens:,}"),
        (t("console.tier.daily_ingests"), f"{tier.daily_ingests:,}"),
        (t("console.tier.max_effort"), tier.max_effort),
        (t("console.tier.features"), features),
        (t("console.tier.storage"), f"{tier.storage_mb:,} MB"),
        (t("console.tier.max_documents"), f"{tier.max_documents:,}"),
        (t("console.tier.byok_concurrency"), f"{tier.byok_max_concurrency:,}"),
    )
    return "".join(f"<dt>{escape(k)}</dt><dd>{escape(v)}</dd>" for k, v in rows)


def _settings_usage_stats_html(usage: dict[str, Any], *, admin_viewer: bool) -> str:
    tier: ServerTier = usage["tier"]
    if admin_viewer:
        stats = (
            ("documents", f"{usage['documents']:,}", t("console.usage.documents_own")),
            ("storage", _settings_format_bytes(usage["storage_bytes"]), t("console.usage.storage_own")),
        )
    else:
        stats = (
            ("documents", f"{usage['documents']:,} / {tier.max_documents:,}", t("console.usage.documents")),
            ("storage", f"{_settings_format_bytes(usage['storage_bytes'])} / {tier.storage_mb:,} MB", t("console.usage.storage")),
            ("tokens", f"{usage['tokens_total']:,} / {tier.monthly_tokens:,}", t("console.usage.tokens", period=usage['period'])),
            ("ingests", f"{usage['daily_ingests']:,} / {tier.daily_ingests:,}", t("console.usage.ingests", day=usage['daily_day'])),
        )
    return "".join(
        f'<div class="pref-stat" data-usage-stat="{escape(key)}"><b>{escape(value)}</b><span>{escape(label)}</span></div>'
        for key, value, label in stats
    )


def _settings_reauth_form_html(*, has_password: bool, form_id: str) -> str:
    if not has_password:
        return (
            f'<section class="console-subcard" id="{escape(form_id)}" data-settings-reauth="none" aria-label="{escape(t("console.reauth.submit"))}">'
            f"<h2><span data-icon='lock'></span> {escape(t('console.reauth.submit'))}</h2>"
            f'<p class="hint">{escape(t("console.reauth.no_password_hint"))}</p>'
            "</section>"
        )
    return (
        f'<form class="console-subcard" id="{escape(form_id)}" data-settings-form="reauth" aria-label="{escape(t("console.reauth.submit"))}">'
        f"<h2><span data-icon='lock'></span> {escape(t('console.reauth.form_title'))}</h2>"
        f'<p class="hint">{escape(t("console.reauth.form_hint"))}</p>'
        f'<label style="display:flex;flex-direction:column;gap:4px;font-size:12.5px;font-weight:600">{escape(t("console.settings.account.current_password"))}<input name="password" type="password" class="pref-input" required autocomplete="current-password"/></label>'
        f'<button type="submit" class="pref-btn pref-btn-accent">{escape(t("console.reauth.submit"))}</button>'
        '<p class="result" data-settings-result role="status" aria-live="polite"></p>'
        "</form>"
    )


def _settings_enabled_idp_providers(ctx: AccountsContext) -> list[auth_db.IdpProviderMeta]:
    data_dir = ctx.data_dir or ctx.auth_db_file.parent.parent
    return [p for p in auth_db.load_idp_providers(data_dir, include_secrets=False) if p.enabled]


def _federation_on(ctx: AccountsContext) -> bool:
    conn = open_db(ctx)
    try:
        return accounts_store.AccountStore(conn).federation_state() == "on"
    finally:
        conn.close()


def _settings_account_body(
    ctx: AccountsContext,
    *,
    principal: Principal,
    mode: accounts_store.ServerMode,
    account: accounts_store.Account,
    owned_themes: list[KnowledgeBaseInfo],
    admin_viewer: bool,
    notice: str | None = None,
) -> tuple[str, dict[str, Any]]:
    auth = open_auth_db(ctx)
    try:
        has_password = auth_db.has_password(auth, account.id)
    finally:
        auth.close()
    usage = _settings_account_usage(ctx, account.id)
    providers = _settings_enabled_idp_providers(ctx)

    # S9: 싱글 모드에서는 USERNAME과 profile_public 행을 내지 않는다.
    info_rows: list[tuple[str, str, str]] = [("public_id", t("console.settings.account.field.number"), str(account.public_id))]
    if mode == "network":
        info_rows.append(("username", "USERNAME", f"@{account.username}" if account.username else t("console.settings.account.username_unset")))
    info_rows.extend(
        (
            ("display_name", t("console.settings.account.display_name"), account.display_name),
            ("role", t("console.settings.account.field.role"), account_role_text(account.role)),
            ("status", t("console.common.status"), account_status_text(account.status)),
            ("password", t("console.settings.account.password"), t("console.settings.account.password_set") if has_password else t("console.settings.account.password_unset")),
        )
    )
    if mode == "network":
        info_rows.append(("profile_public", t("console.settings.account.profile_public"), t("console.settings.account.profile_public_on") if account.profile_public else t("console.settings.account.profile_public_off")))
    info_html = "".join(f'<dt>{escape(label)}</dt><dd data-account-field="{escape(key)}">{escape(value)}</dd>' for key, label, value in info_rows)
    # Fedify sidecar design Sections 8.1 and 8.4: with federation on, a public profile is a federation actor and the change to public needs a step-up.
    federation_on = mode == "network" and _federation_on(ctx)
    step_up_attr = ""
    if federation_on:
        step_up_attr = ' data-step-up="password"' if has_password else ' data-step-up="session"'
    federation_hint_html = (
        f'<p class="hint" data-federation-hint>{escape(t("console.settings.account.federation_hint"))}</p>'
        if federation_on
        else ""
    )
    profile_toggle_html = (
        '<div style="margin-top:12px;display:flex;align-items:center;gap:8px">'
        f'<label for="pref-profile-public" style="font-size:12.5px;font-weight:600">{escape(t("console.settings.account.profile_public"))}</label>'
        f'<input id="pref-profile-public" type="checkbox" switch data-settings-toggle="profile-public"{step_up_attr}{" checked" if account.profile_public else ""} aria-label="{escape(t("console.settings.account.profile_public_label"))}"/>'
        '</div>'
        f'{federation_hint_html}'
        '<p class="result" data-settings-result role="status" aria-live="polite"></p>'
        if mode == "network"
        else ""
    )
    # Schema v37: the sources of the documents of the personal knowledge base for other readers. Both modes: in single mode other readers can read the knowledge base of the admin too.
    source_toggle_html = (
        '<div data-source-public>'
        '<div style="margin-top:12px;display:flex;align-items:center;gap:8px">'
        f'<label for="pref-personal-source-public" style="font-size:12.5px;font-weight:600">{escape(t("console.settings.account.source_public"))}</label>'
        f'<input id="pref-personal-source-public" type="checkbox" switch data-settings-toggle="personal-source-public"{" checked" if account.personal_source_public else ""} aria-label="{escape(t("console.settings.account.source_public"))}"/>'
        '</div>'
        f'<p class="hint" data-source-public-hint>{escape(t("console.settings.account.source_public_hint"))}</p>'
        '<p class="result" data-settings-result role="status" aria-live="polite"></p>'
        '</div>'
    )

    current_pw_html = (
        f'<label style="display:flex;flex-direction:column;gap:4px;font-size:12.5px;font-weight:600">{escape(t("console.settings.account.current_password"))}<input name="current_password" type="password" class="pref-input" required autocomplete="current-password"/></label>'
        if has_password
        else f'<p class="hint">{escape(t("console.settings.account.no_password_hint"))}</p>'
    )
    min_len = auth_db.MIN_PASSWORD_LENGTH

    provider_buttons = "".join(
        f'<button type="button" class="pref-btn" data-identity-link="{escape(p.id)}">+ {escape(t("console.settings.account.identity_link", name=p.display_name))}</button>'
        for p in providers
    )
    link_html = (
        f'<div style="display:flex;gap:8px;flex-wrap:wrap" data-identity-link-list>{provider_buttons}</div>'
        if provider_buttons
        else f'<p class="hint" data-identity-link-empty>{escape(t("console.settings.account.identity_link_empty"))}</p>'
    )

    bundle_options = "".join(
        f'<option value="{escape(ids.format_public_id(t.number) if t.number is not None else str(t.id))}">{escape(t.icon)} {escape(t.label)}</option>'
        for t in owned_themes
    )
    if bundle_options:
        bundle_html = (
            '<form style="display:flex;gap:10px;align-items:flex-end;flex-wrap:wrap" data-settings-form="bundle-export">'
            f'<label style="display:flex;flex-direction:column;gap:4px;font-size:12.5px;font-weight:600">{escape(t("console.settings.account.bundle_theme"))}<select name="theme" class="pref-select" required>{bundle_options}</select></label>'
            f'<button type="submit" class="pref-btn pref-btn-accent"><span data-icon="download"></span> {escape(t("console.settings.account.bundle_submit"))}</button>'
            "</form>"
            '<p class="result" data-settings-result role="status" aria-live="polite"></p>'
            '<div data-bundle-output aria-live="polite"></div>'
        )
    else:
        bundle_html = f'<p class="hint" data-bundle-empty>{escape(t("console.settings.account.bundle_empty"))}</p>'

    if admin_viewer:
        lifecycle_html = f'<p class="hint" data-lifecycle="admin">{escape(t("console.settings.account.lifecycle_admin"))}</p>'
    elif account.status == "deleting":
        lifecycle_html = (
            '<div class="pref-notice" data-tone="warn" data-lifecycle="deleting">'
            f"{escape(t('console.settings.account.lifecycle_deleting', when=format_ts(account.delete_after)))}"
            "</div>"
        )
    else:
        lifecycle_html = (
            f'<p class="hint" data-lifecycle="member">{escape(t("console.settings.account.lifecycle_member"))}</p>'
            f'<p class="hint">{escape(t("console.settings.account.lifecycle_member_hint"))}</p>'
        )

    quota_hint = (
        f"{escape(t('console.settings.account.quota_hint_admin'))}"
        if admin_viewer
        else t("console.settings.account.quota_hint")
    )

    notice_key = SETTINGS_ACCOUNT_NOTICE_TEXT.get(notice or "")
    notice_text = t(notice_key) if notice_key else ""
    notice_html = f'<p class="pref-notice" data-account-notice="{escape(notice or "")}" role="status">{escape(notice_text)}</p>' if notice_text else ""
    body = (
        f"{notice_html}"
        f'<section class="console-subcard" id="settings-account-info" aria-label="{escape(t("console.settings.account.info_title"))}">'
        f"<h2><span data-icon='id'></span> {escape(t('console.settings.account.info_title'))}</h2>"
        f"<dl>{info_html}</dl>"
        f"{profile_toggle_html}"
        f"{source_toggle_html}"
        "</section>"
        f'<form class="console-subcard" id="settings-display-name" data-settings-form="display-name" aria-label="{escape(t("console.settings.account.display_name"))}">'
        f"<h2><span data-icon='tag'></span> {escape(t('console.settings.account.display_name'))}</h2>"
        f'<label style="display:flex;flex-direction:column;gap:4px;font-size:12.5px;font-weight:600">{escape(t("console.settings.account.display_name"))}<input name="display_name" type="text" class="pref-input" value="{escape(account.display_name)}" maxlength="64" autocomplete="name"/></label>'
        f'<button type="submit" class="pref-btn pref-btn-accent">{escape(t("common.action.save"))}</button>'
        '<p class="result" data-settings-result role="status" aria-live="polite"></p>'
        "</form>"
        f"{_settings_locale_html(account)}"
        f"{_settings_entity_language_html(account)}"
        f"{_settings_reauth_form_html(has_password=has_password, form_id='settings-account-reauth')}"
        f'<form class="console-subcard" id="settings-password" data-settings-form="password" data-has-password="{"1" if has_password else "0"}" aria-label="{escape(t("console.settings.account.password"))}">'
        f"<h2><span data-icon='key'></span> {escape(t('console.settings.account.password_change') if has_password else t('console.settings.account.password_setup'))}</h2>"
        f"{current_pw_html}"
        f'<label style="display:flex;flex-direction:column;gap:4px;font-size:12.5px;font-weight:600">{escape(t("console.settings.account.new_password"))}<input name="new_password" type="password" class="pref-input" required minlength="{min_len}" autocomplete="new-password"/></label>'
        f'<label style="display:flex;flex-direction:column;gap:4px;font-size:12.5px;font-weight:600">{escape(t("console.settings.account.new_password_confirm"))}<input name="new_password_confirm" type="password" class="pref-input" required minlength="{min_len}" autocomplete="new-password"/></label>'
        f"<p class=\"hint\">{escape(t('console.settings.account.password_rules', min=min_len))}</p>"
        f'<button type="submit" class="pref-btn pref-btn-accent">{escape(t("common.action.save"))}</button>'
        '<p class="result" data-settings-result role="status" aria-live="polite"></p>'
        "</form>"
        f'<section class="console-subcard" id="settings-identities" data-settings-identities aria-label="{escape(t("console.settings.account.identities_label"))}">'
        f"<h2><span data-icon='link'></span> {escape(t('console.settings.account.identities_title'))}</h2>"
        f'<p class="hint">{escape(t("console.settings.account.identities_hint"))}</p>'
        '<div class="pref-table-wrap"><table class="pref-table">'
        f"<thead><tr><th>{escape(t('console.settings.account.identities_col_provider'))}</th><th>{escape(t('console.settings.account.identities_col_linked'))}</th><th>{escape(t('console.settings.account.identities_col_last_login'))}</th><th></th></tr></thead>"
        f'<tbody data-identities-tbody><tr><td colspan="4" style="color:var(--muted);text-align:center;">{escape(t("console.settings.account.identities_loading"))}</td></tr></tbody>'
        "</table></div>"
        f"{link_html}"
        '<p class="result" data-settings-result role="status" aria-live="polite"></p>'
        "</section>"
        f'<section class="console-subcard" id="settings-quota" aria-label="{escape(t("console.settings.account.quota_title"))}">'
        f"<h2><span data-icon='ruler'></span> {escape(t('console.settings.account.quota_title'))}</h2>"
        f'<p class="hint">{escape(quota_hint)}</p>'
        f'<div class="pref-stat-row" data-usage-stats>{_settings_usage_stats_html(usage, admin_viewer=admin_viewer)}</div>'
        "</section>"
        f'<section class="console-subcard" id="settings-bundle" aria-label="{escape(t("console.settings.account.bundle_label"))}">'
        f"<h2><span data-icon='package'></span> {escape(t('console.settings.account.bundle_title'))}</h2>"
        f'<p class="hint">{t_code("console.settings.account.bundle_hint", endpoint="POST /graph/bundle")}</p>'
        f"{bundle_html}"
        "</section>"
        f'<section class="console-subcard" id="settings-account-lifecycle" aria-label="{escape(t("console.settings.account.lifecycle_title"))}">'
        f"<h2><span data-icon='trash'></span> {escape(t('console.settings.account.lifecycle_title'))}</h2>"
        f"{lifecycle_html}"
        "</section>"
    )
    extra = {
        "has_password": has_password,
        "identity_providers": [{"id": p.id, "display_name": p.display_name, "type": p.type} for p in providers],
    }
    return body, extra


def _settings_locale_html(account: accounts_store.Account) -> str:
    """The interface language selector ``#pref-account-locale`` (I18N design Section 6.2). The empty value is automatic negotiation. ``console_account.js`` sends the change with ``PATCH /api/me`` and loads the page again."""
    title = icon_html("globe") + " " + escape(t("console.account.locale_title"))
    label = escape(t("console.account.locale_label"))
    options = f'<option value=""{"" if account.locale else " selected"}>{escape(t("console.account.locale_auto"))}</option>' + "".join(
        f'<option value="{code}"{" selected" if account.locale == code else ""}>{escape(t(f"locale.name.{code}"))}</option>' for code in SUPPORTED_LOCALES
    )
    return (
        f'<section class="console-subcard" id="settings-locale" aria-label="{label}">'
        f"<h2>{title}</h2>"
        f'<label for="pref-account-locale" style="display:flex;flex-direction:column;gap:4px;font-size:12.5px;font-weight:600">{label}'
        f'<select id="pref-account-locale" class="pref-input" data-settings-locale>{options}</select></label>'
        f'<p class="hint">{escape(t("console.account.locale_hint"))}</p>'
        '<p class="result" data-settings-result role="status" aria-live="polite"></p>'
        "</section>"
    )


def _settings_entity_language_html(account: accounts_store.Account) -> str:
    """The entity language selector ``#pref-account-entity-language`` (ontology.label_language). The empty value shows the stored main labels. ``console_account.js`` sends the change with ``PATCH /api/me``."""
    title = icon_html("globe") + " " + escape(t("console.account.entity_language_title"))
    label = escape(t("console.account.entity_language_label"))
    options = f'<option value=""{"" if account.entity_language else " selected"}>{escape(t("console.account.entity_language_none"))}</option>' + "".join(
        f'<option value="{code}"{" selected" if account.entity_language == code else ""}>{escape(t(f"entity_language.name.{code}"))}</option>' for code in ENTITY_LANGUAGES
    )
    return (
        f'<section class="console-subcard" id="settings-entity-language" aria-label="{label}">'
        f"<h2>{title}</h2>"
        f'<label for="pref-account-entity-language" style="display:flex;flex-direction:column;gap:4px;font-size:12.5px;font-weight:600">{label}'
        f'<select id="pref-account-entity-language" class="pref-input" data-settings-entity-language>{options}</select></label>'
        f'<p class="hint">{escape(t("console.account.entity_language_hint"))}</p>'
        '<p class="result" data-settings-result role="status" aria-live="polite"></p>'
        "</section>"
    )


def _settings_keys_body(
    ctx: AccountsContext,
    *,
    principal: Principal,
    mode: accounts_store.ServerMode,
    account: accounts_store.Account,
    admin_viewer: bool,
) -> tuple[str, dict[str, Any]]:
    if admin_viewer:
        # 관리자는 BYOK 키 대신 서버 프로바이더 설정 및 개인 CLI 설정을 쓴다(keys.py: 403 admin_uses_provider_settings).
        notice = t("console.settings.keys.admin_notice")
        body = (
            f'<section class="console-subcard" id="settings-keys-admin-providers" data-keys-mode="admin" aria-label="{escape(t("console.settings.keys.admin_label"))}">'
            f"<h2><span data-icon='bolt'></span> {escape(t('console.settings.keys.admin_title'))}</h2>"
            f'<p class="hint">{escape(notice)}</p>'
            f'<a class="pref-btn pref-btn-accent" href="/admin/providers"><span data-icon="bolt"></span> {escape(t("console.settings.keys.admin_open"))}</a>'
            "</section>"
            f'<section class="console-subcard" id="admin-personal-cli-host" data-admin-personal-cli aria-label="{escape(t("console.settings.keys.cli_label"))}">'
            f"<h2><span data-icon='terminal'></span> {escape(t('console.settings.keys.cli_title'))}</h2>"
            f'<p class="hint">{escape(t("console.settings.keys.cli_hint"))}</p>'
            f'<p class="hint" data-personal-cli-loading>{escape(t("console.settings.keys.cli_loading"))}</p>'
            "</section>"
        )
        return body, {"keys_mode": "admin"}
    if mode != "network" or principal.server_role != "member":
        # 싱글 모드의 비관리자: /api/me/keys는 404이고 관리자 화면도 쓸 수 없다. 관리자 링크 없이 안내만 한다(fail-closed).
        body = (
            f'<section class="console-subcard" id="settings-keys-unavailable" data-keys-mode="unavailable" aria-label="{escape(t("console.settings.keys.unavailable_label"))}">'
            f"<h2><span data-icon='lock'></span> {escape(t('console.settings.keys.unavailable_title'))}</h2>"
            f'<p class="hint">{escape(t("console.settings.keys.unavailable_hint"))}</p>'
            "</section>"
        )
        return body, {"keys_mode": "unavailable"}

    items = keys_routes.list_meta(ctx, account.id)
    usage = _settings_account_usage(ctx, account.id)
    auth = open_auth_db(ctx)
    try:
        has_password = auth_db.has_password(auth, account.id)
    finally:
        auth.close()
    provider_options = "".join(
        f'<option value="{escape(p)}">{escape(keys_routes.PROVIDER_TEXT.get(p, p))}</option>' for p in MEMBER_KEY_PROVIDERS
    )
    body = (
        f'<section class="console-subcard" id="settings-keys-meta" data-keys-mode="byok" aria-label="{escape(t("console.settings.keys.meta_label"))}">'
        f"<h2><span data-icon='lock'></span> {escape(t('console.settings.keys.meta_title'))}</h2>"
        f'<p class="hint">{escape(t("console.settings.keys.meta_hint"))}</p>'
        f'<p class="hint" data-split-key-notice>{escape(t("console.settings.keys.split_key_notice"))}</p>'
        f'<div data-provider-key-meta aria-live="polite">{keys_routes._meta_rows(items)}</div>'
        "</section>"
        f"{_settings_reauth_form_html(has_password=has_password, form_id='settings-keys-reauth')}"
        f'<form class="console-subcard" id="settings-keys-put" data-settings-form="provider-key-put" aria-label="{escape(t("console.settings.keys.put_title"))}">'
        f"<h2><span data-icon='plus'></span> {escape(t('console.settings.keys.put_title'))}</h2>"
        f'<label style="display:flex;flex-direction:column;gap:4px;font-size:12.5px;font-weight:600">{escape(t("console.settings.keys.provider"))}<select name="provider" class="pref-select">{provider_options}</select></label>'
        '<div data-byok-policy-summary class="hint" aria-live="polite"></div>'
        f'<label style="display:flex;flex-direction:column;gap:4px;font-size:12.5px;font-weight:600">{escape(t("console.settings.keys.model"))}<select name="model" class="pref-select" data-byok-model-select><option value="">{escape(t("console.settings.keys.model_auto"))}</option></select></label>'
        f'<label style="display:flex;flex-direction:column;gap:4px;font-size:12.5px;font-weight:600">{escape(t("console.settings.keys.effort"))}<select name="effort" class="pref-select" data-byok-effort-select><option value="">{escape(t("console.settings.keys.effort_auto"))}</option><option value="low">low</option><option value="medium">medium</option><option value="high">high</option></select></label>'
        f'<label style="display:flex;flex-direction:column;gap:4px;font-size:12.5px;font-weight:600">{escape(t("console.settings.keys.api_key"))}<input name="api_key" type="password" class="pref-input" required autocomplete="off" spellcheck="false" maxlength="{keys_routes.API_KEY_MAX_LENGTH}"/></label>'
        f'<p class="hint">{escape(t("console.settings.keys.put_hint"))}</p>'
        f'<button type="submit" class="pref-btn pref-btn-accent">{escape(t("console.settings.keys.put_submit"))}</button>'
        '<p class="result" data-settings-result role="status" aria-live="polite"></p>'
        "</form>"
        f'<form class="console-subcard" id="settings-keys-verify" data-settings-form="provider-key-verify" aria-label="{escape(t("console.settings.keys.verify_title"))}">'
        f"<h2><span data-icon='repeat'></span> {escape(t('console.settings.keys.verify_title'))}</h2>"
        f'<label style="display:flex;flex-direction:column;gap:4px;font-size:12.5px;font-weight:600">{escape(t("console.settings.keys.provider"))}<select name="provider" class="pref-select">{provider_options}</select></label>'
        f'<p class="hint">{escape(t("console.settings.keys.verify_hint"))}</p>'
        f'<button type="submit" class="pref-btn">{escape(t("console.settings.keys.verify_submit"))}</button>'
        '<p class="result" data-settings-result role="status" aria-live="polite"></p>'
        "</form>"
        f'<form class="console-subcard" id="settings-keys-delete" data-settings-form="provider-key-delete" aria-label="{escape(t("console.settings.keys.delete_title"))}">'
        f"<h2><span data-icon='trash'></span> {escape(t('console.settings.keys.delete_title'))}</h2>"
        f'<label style="display:flex;flex-direction:column;gap:4px;font-size:12.5px;font-weight:600">{escape(t("console.settings.keys.provider"))}<select name="provider" class="pref-select">{provider_options}</select></label>'
        f'<p class="hint">{escape(t("console.settings.keys.delete_hint"))}</p>'
        f'<button type="submit" class="pref-btn pref-btn-danger">{escape(t("common.action.delete"))}</button>'
        '<p class="result" data-settings-result role="status" aria-live="polite"></p>'
        "</form>"
        f'<section class="console-subcard" id="settings-keys-tier" aria-label="{escape(t("console.settings.keys.tier_label"))}">'
        f"<h2><span data-icon='chart'></span> {escape(t('console.settings.keys.tier_title'))}</h2>"
        f'<p class="hint">{escape(t("console.settings.keys.tier_hint"))}</p>'
        f"<dl data-server-tier>{_settings_tier_rows_html(usage['tier'])}</dl>"
        f'<div class="pref-stat-row" data-usage-stats>{_settings_usage_stats_html(usage, admin_viewer=False)}</div>'
        "</section>"
    )
    return body, {"keys_mode": "byok", "has_password": has_password}


def _settings_own_oauth_clients(ctx: AccountsContext, account_id: int) -> list[dict[str, Any]]:
    """호출자 계정에 발급된 OAuth 토큰이 있는 클라이언트만 고른다(``oauth_tokens.account_id``). 토큰 값은 읽지 않는다."""
    conn = open_db(ctx)
    try:
        rows = conn.execute(
            "SELECT c.client_id, c.client_name, c.redirect_uris, MIN(t.created_at), MAX(t.expires_at), COUNT(t.token) "
            "FROM oauth_tokens t JOIN oauth_clients c ON c.client_id = t.client_id "
            "WHERE t.account_id = ? GROUP BY c.client_id ORDER BY MIN(t.created_at) DESC",
            (account_id,),
        ).fetchall()
    finally:
        conn.close()
    clients: list[dict[str, Any]] = []
    for client_id, client_name, redirect_uris_raw, first_issued, last_expiry, token_count in rows:
        redirect_uris = json.loads(redirect_uris_raw)
        if not isinstance(redirect_uris, list):
            raise ValueError(f"oauth_clients.redirect_uris is not a JSON list for client {client_id!r}")
        clients.append(
            {
                "client_id": str(client_id),
                "client_name": client_name or "External MCP Client",
                "redirect_uris": [str(u) for u in redirect_uris],
                "first_issued_at": first_issued,
                "expires_at": last_expiry,
                "token_count": int(token_count),
            }
        )
    return clients


def _settings_tokens_body(ctx: AccountsContext, *, account: accounts_store.Account) -> tuple[str, dict[str, Any]]:
    clients = _settings_own_oauth_clients(ctx, account.id)
    trs = "".join(
        "<tr>"
        f"<td><strong>{escape(c['client_name'])}</strong></td>"
        f'<td><code class="pref-mono">{escape(c["client_id"])}</code><br/><span class="pref-mono" style="color:var(--muted)">{escape(", ".join(c["redirect_uris"]) or "-")}</span></td>'
        f"<td>{escape(format_ts(c['first_issued_at']))}</td>"
        f"<td>{escape(format_ts(c['expires_at']))}</td>"
        f"<td>{escape(c['token_count'])}</td>"
        "</tr>"
        for c in clients
    )
    tbody = trs or f'<tr><td colspan="5" style="color:var(--muted);text-align:center;">{escape(t("console.settings.tokens.clients_empty"))}</td></tr>'
    body = (
        f'<section class="console-subcard" id="settings-tokens-manager-card" aria-label="{escape(t("console.settings.tokens.manager_label"))}">'
        '<div id="settings-tokens-host" data-settings-panel-host="tokens">'
        f'<p class="hint">{escape(t("console.settings.tokens.loading"))}</p>'
        "</div>"
        "</section>"
        f'<section class="console-subcard" id="settings-oauth-clients" aria-label="{escape(t("console.settings.tokens.clients_label"))}">'
        f"<h2><span data-icon='lock'></span> {escape(t('console.settings.tokens.clients_title'))}</h2>"
        f'<p class="hint">{t_code("console.settings.tokens.clients_hint", authorize="/oauth/authorize")}</p>'
        '<div class="pref-table-wrap"><table class="pref-table">'
        f"<thead><tr><th>{escape(t('console.oauth.col_client_name'))}</th><th>client_id · redirect_uris</th><th>{escape(t('console.settings.tokens.col_first_issued'))}</th><th>{escape(t('console.settings.tokens.col_last_expiry'))}</th><th>{escape(t('console.settings.tokens.col_count'))}</th></tr></thead>"
        f"<tbody data-oauth-clients-tbody>{tbody}</tbody>"
        "</table></div>"
        "</section>"
    )
    return body, {"oauth_clients": clients}


def _render_console_section(
    ctx: AccountsContext,
    *,
    spec: ConsoleSectionSpec,
    principal: Principal,
    raw_theme: str | None = None,
    custom_body_html: str | None = None,
    extra_bootstrap: dict[str, Any] | None = None,
    extra_scripts_html: str = "",
    notice: str | None = None,
) -> Response:
    assert principal.account_id is not None
    theme_lookup = _resolve_caller_theme_and_list(ctx, account_id=principal.account_id, raw_theme=raw_theme)
    if theme_lookup is None:
        return not_found()
    selected_theme, owned_themes, validated_theme_query = theme_lookup
    mode, account = _account_context_info(ctx, principal.account_id)
    admin_viewer = is_admin(principal)

    # S9: 싱글 모드에서는 USERNAME을 어디에도 노출하지 않는다. 고유 기능 경로(/syndication)는 항상 루트에 마운트한다.
    graph_href = f"/@{account.username}/" if (mode == "network" and account.username) else "/"
    syndication_href = "/syndication"
    if validated_theme_query:
        syndication_href = _append_theme_query(syndication_href, validated_theme_query)

    scope_badge = t("console.scope.admin_single") if (admin_viewer and mode == "single") else (t("console.scope.admin_network") if admin_viewer else t("console.scope.account"))
    sidebar_html = _build_sidebar_html(
        active_path=spec.path,
        admin_viewer=admin_viewer,
        theme_query=validated_theme_query,
    )
    theme_selector_html = _build_theme_selector_html(
        spec=spec,
        owned_themes=owned_themes,
        selected_theme=selected_theme,
    )
    built_extra: dict[str, Any] = {}
    if custom_body_html is not None:
        section_body_html = custom_body_html
    elif spec.area == "admin":
        section_body_html, built_extra = _build_admin_section_body_and_extra(ctx, spec.slug, principal)
        if spec.slug == "backups":
            extra_scripts_html = '<script src="/static/js/console_features.js" defer></script>' + extra_scripts_html
    else:
        if spec.slug in SYNDICATION_SECTION_SLUGS:
            # P3-b: Group 2–3 본문은 console_syndication_panels 모듈이 그린다.
            rendered = build_syndication_section(
                ctx,
                slug=spec.slug,
                path=spec.path,
                mode=mode,
                account=account,
                selected_theme=selected_theme,
                owned_themes=owned_themes,
                theme_query=validated_theme_query,
            )
        else:
            # P3-a: Group 1 본문(account, keys, themes, tokens).
            rendered = _build_settings_account_section(
                ctx,
                spec=spec,
                principal=principal,
                mode=mode,
                account=account,
                owned_themes=owned_themes,
                notice=notice,
            )
        section_body_html = rendered.body_html
        built_extra = dict(rendered.extra_bootstrap)
        extra_scripts_html = rendered.extra_scripts_html + extra_scripts_html
    bootstrap_payload: dict[str, Any] = {
        "area": spec.area,
        "section": spec.slug,
        "path": spec.path,
        "mode": mode,
        "is_admin": admin_viewer,
        # I18N design Section 6.2: the stored preference (None = automatic) and the locale of this response.
        "locale": account.locale,
        "effective_locale": get_locale(),
        "theme": (
            ids.format_public_id(selected_theme.number)
            if (selected_theme is not None and selected_theme.number is not None)
            else (str(selected_theme.id) if selected_theme is not None else None)
        ),
        **built_extra,
        **(extra_bootstrap or {}),
    }
    common_appbar_html = ui_chrome.render_appbar_html(
        bar_id="console-bar",
        brand_id="console-back-graph",
        brand_href=graph_href,
        brand_extra_html=theme_selector_html,
        center_id="console-bar-center",
        center_extra_html=f'<span class="appbar-title console-brand"><span data-icon="settings"></span> ${{t:nav.topbar.console}}</span><span class="console-badge" id="console-scope-badge">{escape(scope_badge)}</span>',
        end_id="console-bar-end",
        ingest_enabled=False,
        home_href=graph_href,
        synd_href=syndication_href,
        console_href="/admin" if admin_viewer else "/settings",
        console_icon="wrench" if admin_viewer else "settings",
        console_current=True,
        show_logout=True,
    )
    return render_template(
        "console_shell.html",
        text={
            "page_title": spec.title,
            "storage_key": str(account.public_id),
            "active_area": spec.area,
            "active_section": spec.slug,
            "graph_href": graph_href,
            "syndication_href": syndication_href,
            "scope_badge": scope_badge,
            "section_title": spec.title,
            "section_badge": spec.badge,
            "section_desc": spec.description,
        },
        markup={
            "common_appbar_html": common_appbar_html,
            "section_icon_html": icon_html(spec.icon),
            "theme_selector_html": theme_selector_html,
            "sidebar_nav_html": sidebar_html,
            "section_body_html": section_body_html,
            # <script type="application/json"> 탈출을 막도록 <, >, & 를 유니코드 이스케이프한다.
            "bootstrap_json": json.dumps(bootstrap_payload, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026"),
            "extra_scripts_html": extra_scripts_html,
            # 비관리자 /settings/* 응답에는 관리자 JS 번들을 싣지 않는다(불변식 3).
            "admin_scripts_html": '<script src="/static/js/console_admin.js" defer></script>' if spec.area == "admin" else "",
        },
    )


def routes(ctx: AccountsContext) -> list[Route]:
    async def admin_index(request: Request) -> Response:
        principal = request_principal(request)
        if not is_admin(principal):
            return not_found()
        raw_theme = request.query_params.get("kb") if "kb" in request.query_params else None
        if raw_theme is not None:
            assert principal.account_id is not None
            if _resolve_caller_theme_and_list(ctx, account_id=principal.account_id, raw_theme=raw_theme) is None:
                return not_found()
        return _redirect("/admin/server")

    async def settings_index(request: Request) -> Response:
        principal = request_principal(request)
        if not _is_owner(principal):
            return not_found()
        assert principal.account_id is not None
        raw_theme = request.query_params.get("kb") if "kb" in request.query_params else None
        validated_theme: str | None = None
        if raw_theme is not None:
            resolved = _resolve_caller_theme_and_list(ctx, account_id=principal.account_id, raw_theme=raw_theme)
            if resolved is None:
                return not_found()
            validated_theme = resolved[2]
        target = "/settings/reader" if request.query_params.get("from") == "syndication" else "/settings/account"
        return _redirect(_append_theme_query(target, validated_theme))

    async def admin_section_page(request: Request) -> Response:
        principal = request_principal(request)
        if not is_admin(principal):
            return not_found()
        spec = ADMIN_CONSOLE_BY_PATH.get(request.url.path)
        if spec is None:
            return not_found()
        raw_theme = request.query_params.get("kb") if "kb" in request.query_params else None
        return await asyncio.to_thread(
            _render_console_section,
            ctx,
            spec=spec,
            principal=principal,
            raw_theme=raw_theme,
        )

    async def settings_old_themes_page(request: Request) -> Response:
        """``/settings/themes`` (old web link of a retired page): 308 to ``/settings/kb``, which has its function (design Section 10.3). The admin gets 308 to ``/admin/themes``."""
        principal = request_principal(request)
        if not _is_owner(principal):
            return not_found()
        assert principal.account_id is not None
        raw_theme = request.query_params.get("kb") if "kb" in request.query_params else None
        validated_theme: str | None = None
        if raw_theme is not None:
            resolved = _resolve_caller_theme_and_list(ctx, account_id=principal.account_id, raw_theme=raw_theme)
            if resolved is None:
                return not_found()
            validated_theme = resolved[2]
        if is_admin(principal):
            return _redirect(_append_theme_query("/admin/themes", validated_theme), status_code=308)
        return _redirect("/settings/kb", status_code=308)

    async def settings_kb_manage_page(request: Request) -> Response:
        """``/settings/kb/{number}``: the page of a reviewer for one shared knowledge base (design Section 10.3). The number of a personal knowledge base, or an unknown number, gets 404."""
        principal = request_principal(request)
        if not _is_owner(principal):
            return not_found()
        spec = SETTINGS_BY_PATH["/settings/kb/manage"]
        raw_theme = request.query_params.get("kb") if "kb" in request.query_params else None
        kb_number = int(request.path_params["kb_number"])

        def _render() -> Response:
            if not _is_shared_kb_number(ctx, kb_number):
                return not_found()
            return _render_console_section(ctx, spec=spec, principal=principal, raw_theme=raw_theme, extra_bootstrap={"kb_number": ids.format_public_id(kb_number)})

        return await asyncio.to_thread(_render)

    async def settings_section_page(request: Request) -> Response:
        principal = request_principal(request)
        if not _is_owner(principal):
            return not_found()
        spec = SETTINGS_BY_PATH.get(request.url.path)
        if spec is None:
            return not_found()
        assert principal.account_id is not None
        raw_theme = request.query_params.get("kb") if "kb" in request.query_params else None
        # D3: admin accessing /settings/tokens redirects 302 to /admin/tokens.
        if is_admin(principal) and spec.slug == "tokens":
            validated_theme: str | None = None
            if raw_theme is not None:
                resolved = _resolve_caller_theme_and_list(ctx, account_id=principal.account_id, raw_theme=raw_theme)
                if resolved is None:
                    return not_found()
                validated_theme = resolved[2]
            return _redirect(_append_theme_query("/admin/tokens", validated_theme))
        return await asyncio.to_thread(
            _render_console_section,
            ctx,
            spec=spec,
            principal=principal,
            raw_theme=raw_theme,
            notice=request.query_params.get("notice") if spec.slug == "account" else None,
        )

    async def admin_mcp_telemetry_api(request: Request) -> Response:
        principal = request_principal(request)
        if not is_admin(principal):
            return not_found()
        limit = 50
        if "limit" in request.query_params:
            raw_limit = request.query_params.get("limit", "").strip()
            if not raw_limit or not raw_limit.lstrip("-").isdigit():
                return json_error(400, "invalid_limit")
            limit = int(raw_limit)
            if limit < 1 or limit > 200:
                return json_error(400, "invalid_limit")
        status = request.query_params.get("status", "").strip() or None
        tool_name = request.query_params.get("tool_name", "").strip() or None
        since: float | None = None
        if "since" in request.query_params:
            # C1: 해석하지 못한 since를 조용히 빼고 전체를 돌려주지 않는다. ISO 8601만 받는다.
            try:
                since = datetime.fromisoformat(request.query_params.get("since", "").strip().replace("Z", "+00:00")).timestamp()
            except ValueError:
                return json_error(400, "invalid_since")
        rows = await asyncio.to_thread(
            query_mcp_telemetry,
            ctx.data_dir,
            since=since,
            tool_name=tool_name,
            status=status,
            limit=limit,
        )
        return JSONResponse({"records": rows, "count": len(rows)})

    mounted: list[Route] = [
        Route(ADMIN_INDEX_PATH, admin_index, methods=["GET", "HEAD"]),
        Route(SETTINGS_INDEX_PATH, settings_index, methods=["GET", "HEAD"]),
        Route(ADMIN_MCP_TELEMETRY_API_PATH, admin_mcp_telemetry_api, methods=["GET", "HEAD"]),
        Route(ADMIN_KB_SPEC.path, admin_section_page, methods=["GET", "HEAD"]),
        Route("/settings/themes", settings_old_themes_page, methods=["GET", "HEAD"]),
        Route("/settings/kb/{kb_number:int}", settings_kb_manage_page, methods=["GET", "HEAD"]),
    ]
    for spec in ADMIN_CONSOLE_GROUP_5_SPECS:
        mounted.append(Route(spec.path, admin_section_page, methods=["GET", "HEAD"]))
    for spec in ALL_SETTINGS_SPECS:
        mounted.append(Route(spec.path, settings_section_page, methods=["GET", "HEAD"]))
    return mounted
