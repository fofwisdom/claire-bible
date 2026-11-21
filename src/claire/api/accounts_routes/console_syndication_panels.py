"""``/settings/*`` Group 2–3 섹션 본문 (설계 P3-b: 신디케이션 · 리더, AI 에이전트 연동).

``console.py``가 Group 2–3 섹션을 그릴 때 `build_syndication_section`을 부른다. 이 모듈은 Lane P3-b 소유다.
관리자 전용 데이터(``mcp_telemetry`` 감사 행, ``panel-idp`` 운영 설정, ``panel-userdb`` 운영 디렉터리)는 절대 넣지 않는다.

섹션 본문은 ``templates/partials/settings_syndication/<slug>.html``에 있다. 이 파일들은 레거시
``syndication_preferences.html``의 패널 마크업을 옮긴 것이며, 컨트롤 ID를 그대로 둔다. 화면 상태는
``static/js/console_syndication.js``가 계정 · 테마별 localStorage 키(``claire_synd_prefs_v2:a{계정}:t{테마}``)에 저장한다.
"""

from __future__ import annotations

import re
import string
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

from ... import ids
from ...i18n import interpolate, t
from ...store import accounts as accounts_store
from ...store import db as dbm
from ...store.knowledge_base import KnowledgeBaseInfo
from .. import clients_host
from ..host_router import HostRoles
from .common import TEMPLATES_DIR, AccountsContext, escape, format_ts, open_db

SYNDICATION_SECTION_SLUGS: frozenset[str] = frozenset(
    {"reader", "connections", "feeds", "tags", "rules", "outbound", "opml", "mcp", "webmcp"}
)

# 사이드바 링크 순서와 같다. JS가 테마 선택을 바꿀 때 이 슬러그의 링크(``data-nav-section``)에만 ``?kb=``를 갱신한다.
THEME_SCOPED_NAV_SLUGS: tuple[str, ...] = ("reader", "connections", "feeds", "tags", "rules", "outbound", "opml", "mcp", "webmcp")

PARTIALS_DIR = TEMPLATES_DIR / "partials" / "settings_syndication"
CONSOLE_SYNDICATION_SCRIPT_HTML = '<script src="/static/js/console_syndication.js" defer></script>'

# 레거시 패널과 같은 저장 알림(토스트) 요소. JS가 텍스트만 바꾼다.
def _toast_html() -> str:
    return f'<div id="pref-toast" class="pref-toast" role="status" aria-live="polite">{escape(t("console.synd.toast_saved"))}</div>'


@dataclass(frozen=True, slots=True)
class SettingsSectionRender:
    """섹션 본문 HTML, 부트스트랩 JSON 추가 키, 추가 ``<script>`` 태그 HTML."""

    body_html: str
    extra_bootstrap: dict[str, Any] = field(default_factory=dict)
    extra_scripts_html: str = ""


def theme_ref(theme: KnowledgeBaseInfo) -> str:
    """콘솔 테마 선택기와 같은 테마 참조값(외부 번호, 없으면 내부 id)."""
    return ids.format_public_id(theme.number) if theme.number is not None else str(theme.id)


def _account_is_admin(account: accounts_store.Account) -> bool:
    # ``Principal.server_role``은 ``Account.role``에서 온다(security.py). /settings/*는 owner 스코프만 들어온다.
    return account.role == "admin"


def _syndication_base_href(mode: accounts_store.ServerMode, account: accounts_store.Account) -> str:
    # 고유 기능 경로(/syndication)는 유저네임 하단 경로로 분리하지 않고 루트 경로로 안내한다.
    _ = (mode, account)
    return "/syndication"


# Translation slots of a partial. ``${t:key}`` becomes the escaped translation. ``${th:key}`` becomes the translation as it is: the catalog value of such a key is HTML (inline ``<b>``, ``<code>``, ``<kbd>``) that the repository owns.
_PARTIAL_SLOT = re.compile(r"\$\{(t|th):([a-z0-9_.]+)\}")


def _partial_slot(match: re.Match[str]) -> str:
    text = t(match.group(2))
    return (escape(text) if match.group(1) == "t" else text).replace("$", "$$")


def _render_partial(slug: str, values: dict[str, str]) -> str:
    template = string.Template(_PARTIAL_SLOT.sub(_partial_slot, (PARTIALS_DIR / f"{slug}.html").read_text(encoding="utf-8")))
    # 빠진 자리가 있으면 KeyError를 낸다(C1).
    return template.substitute(values)


def _no_theme_body(slug: str, *, themes_href: str) -> str:
    return (
        f'<div class="pref-notice" data-tone="warn" id="settings-syndication-no-theme" data-section="{escape(slug)}">'
        f'{escape(t("console.synd.no_theme"))} <a href="{escape(themes_href)}">{escape(t("console.synd.no_theme_link"))}</a>'
        "</div>"
    )


def _resolve_delivery_settings_and_roles(ctx: AccountsContext, settings: Any) -> tuple[Any, HostRoles | None, bool]:
    cfg = getattr(ctx, "config", None)
    settings_obj = settings or cfg or SimpleNamespace(db_file=getattr(ctx, "db_file", None), data_dir=getattr(ctx, "data_dir", None))
    roles = getattr(cfg, "host_roles", None)
    if roles is None and cfg is not None and hasattr(cfg, "environment") and hasattr(cfg, "expected_authority"):
        try:
            roles = HostRoles.from_settings(settings_obj, environment=cfg.environment, app_authority=cfg.expected_authority)
        except Exception:
            roles = None
    anon_ro = bool(getattr(cfg, "anonymous_readonly", getattr(settings_obj, "anonymous_readonly", False)))
    return settings_obj, roles, anon_ro


def _render_agent_clients_delivery_block(summary: dict[str, Any]) -> str:
    mode = str(summary.get("mode") or clients_host.DEFAULT_AGENT_CLIENT_DELIVERY_MODE)
    active_base = str(summary.get("active_base_url") or "/clients")
    clients = summary.get("clients") or []
    rows = []
    for client in clients:
        cid = str(client.get("client_id") or "")
        pid = str(client.get("plugin_id") or "")
        manifest_url = str(client.get("manifest_url") or "")
        bundle_url = str(client.get("bundle_url") or "")
        file_links = []
        for entry in client.get("files") or []:
            fpath = str(entry.get("path") or "")
            furl = str(entry.get("url") or "")
            fsha = str(entry.get("sha256") or "")
            short_sha = fsha[:12] if fsha else "-"
            file_links.append(
                f'<div><a href="{escape(furl)}"><code class="pref-mono">{escape(fpath)}</code></a> '
                f'<code class="pref-mono" data-sha256="{escape(fsha)}" title="{escape(fsha)}">{escape(short_sha)}</code></div>'
            )
        files_html = "".join(file_links) or "-"
        rows.append(
            f'<tr data-agent-client-row="{escape(cid)}">'
            f'<td><strong>{escape(cid)}</strong><br/><code class="pref-mono">{escape(pid)}</code></td>'
            f'<td><a href="{escape(manifest_url)}">manifest.json</a></td>'
            f'<td><a href="{escape(bundle_url)}">bundle.tar.gz</a></td>'
            f"<td>{files_html}</td></tr>"
        )
    tbody = "".join(rows) or f'<tr><td colspan="4" style="color:var(--muted);text-align:center;">{escape(t("console.synd.mcp.bundles_empty"))}</td></tr>'
    auth_note = (
        f'<p class="pref-desc">{escape(t("console.synd.mcp.bundles_auth_note", ttl=clients_host.BUNDLE_TOKEN_TTL))}</p>'
        if summary.get("requires_auth")
        else f'<p class="pref-desc">{escape(t("console.synd.mcp.bundles_public_note"))}</p>'
    )
    return (
        f'<div class="pref-card" id="pref-mcp-client-bundles" data-delivery-mode="{escape(mode)}">'
        f'<h2><span data-icon="puzzle"></span> {escape(t("console.synd.mcp.bundles_title"))}</h2>'
        f"{auth_note}"
        '<div class="pref-row">'
        f'<div class="pref-row-label"><strong>{escape(t("console.synd.mcp.bundles_mode"))}</strong><span>{escape(t("console.synd.mcp.bundles_mode_desc"))}</span></div>'
        f'<code id="pref-mcp-client-delivery-mode" class="pref-mono">{escape(mode)}</code>'
        "</div>"
        '<div class="pref-row">'
        f'<div class="pref-row-label"><strong>{escape(t("console.synd.mcp.bundles_endpoint"))}</strong><span>{escape(t("console.synd.mcp.bundles_endpoint_desc"))}</span></div>'
        f'<div class="pref-endpoint"><code id="pref-mcp-client-delivery-base" class="pref-mono">{escape(active_base)}</code>'
        f'<button type="button" class="pref-btn" data-copy-target="pref-mcp-client-delivery-base">{escape(t("console.common.copy"))}</button></div>'
        "</div>"
        '<div class="pref-table-wrap"><table class="pref-table">'
        f'<thead><tr><th>{escape(t("console.synd.mcp.bundles_col_client"))}</th><th>{escape(t("console.synd.mcp.bundles_col_manifest"))}</th><th>{escape(t("console.synd.mcp.bundles_col_bundle"))}</th><th>{escape(t("console.synd.mcp.bundles_col_files"))}</th></tr></thead>'
        f'<tbody id="pref-mcp-client-bundles-tbody">{tbody}</tbody>'
        "</table></div></div>"
    )


def build_syndication_section(
    ctx: AccountsContext,
    *,
    slug: str,
    path: str,
    mode: accounts_store.ServerMode,
    account: accounts_store.Account,
    selected_theme: KnowledgeBaseInfo | None,
    owned_themes: list[KnowledgeBaseInfo],
    theme_query: str | None,
    settings: Any = None,
) -> SettingsSectionRender:
    if slug not in SYNDICATION_SECTION_SLUGS:
        raise KeyError(slug)
    admin_viewer = _account_is_admin(account)
    themes_href = "/admin/themes" if admin_viewer else "/settings/kb"
    if selected_theme is None:
        # C1: 테마가 없으면 다른 테마로 대체하지 않고 안내만 그린다. 스크립트도 싣지 않는다.
        return SettingsSectionRender(
            body_html=_no_theme_body(slug, themes_href=themes_href),
            extra_bootstrap={"syndication": {"section": slug, "theme_ref": None}},
        )

    ref = theme_ref(selected_theme)
    owned_refs = [theme_ref(t) for t in owned_themes]
    seed_index = next((i for i, t in enumerate(owned_themes) if t.id == selected_theme.id), None)
    if seed_index is None:
        # console.py가 이미 소유 테마만 고른다. 여기 오면 호출 규약 위반이다(C1).
        raise ValueError("selected theme is not in owned_themes")

    client_href = f"{_syndication_base_href(mode, account)}?tab=client&kb={ref}"
    tokens_href = "/admin/tokens" if admin_viewer else "/settings/tokens"
    tokens_label = t("console.synd.tokens_label_admin") if admin_viewer else t("console.synd.tokens_label")
    oauth_grants: list[dict[str, Any]] = []
    client_delivery: dict[str, Any] | None = None
    if slug == "mcp":
        settings_obj, roles, anon_ro = _resolve_delivery_settings_and_roles(ctx, settings)
        conn = open_db(ctx)
        try:
            oauth_grants = dbm.list_account_oauth_grants(conn, account.id)
            client_delivery = clients_host.delivery_summary(
                conn,
                settings_obj,
                roles,
                account_id=account.id,
                anonymous_readonly=anon_ro,
            )
        finally:
            conn.close()
    grant_rows = "".join(
        f"<tr data-oauth-grant-row=\"{escape(str(g['grant_id']))}\"><td><strong>{escape(g['client_name'])}</strong><br/><code class=\"pref-mono\">{escape(g['client_id'])}</code></td>"
        f"<td><code class=\"pref-mono\">{escape(g['scope'])}</code></td>"
        f"<td>{escape(format_ts(g['created_at']))}</td>"
        f"<td>{escape(format_ts(g['last_used_at']) if g.get('last_used_at') else '-')}</td>"
        f"<td>{escape(format_ts(g['expires_at']))}</td>"
        f"<td class=\"pref-actions\"><button type=\"button\" class=\"pref-btn\" data-oauth-revoke-grant=\"{escape(str(g['grant_id']))}\">{escape(t('console.oauth.revoke'))}</button></td></tr>"
        for g in oauth_grants
    ) or f'<tr><td colspan="6" style="color:var(--muted);text-align:center;">{escape(t("console.settings.tokens.clients_empty"))}</td></tr>'
    deprecation_notice_html = interpolate(escape(t("console.tokens.deprecation_notice")), {"mcp": '<a href="/settings/mcp">/settings/mcp</a>'})
    values = {
        "client_href": escape(client_href),
        "tokens_href": escape(tokens_href),
        "tokens_label": escape(tokens_label),
        "oauth_grants_rows": grant_rows,
        "deprecation_notice_html": deprecation_notice_html,
        "agent_clients_delivery_html": _render_agent_clients_delivery_block(client_delivery) if client_delivery is not None else "",
    }
    panel_html = _render_partial(slug, values)
    root_attrs = {
        "id": "settings-syndication-root",
        "data-section": slug,
        "data-path": path,
        "data-theme-ref": ref,
        "data-theme-label": selected_theme.label,
        "data-theme-icon": selected_theme.icon,
        "data-theme-public": "true" if selected_theme.is_public else "false",
        "data-theme-seed": str(seed_index),
    }
    attrs = " ".join(f'{name}="{escape(value)}"' for name, value in root_attrs.items())
    body_html = f"<div {attrs}>\n{panel_html}\n{_toast_html()}\n</div>"
    syndication_bootstrap: dict[str, Any] = {
        "section": slug,
        "theme_ref": ref,
        "owned_theme_refs": owned_refs,
        "nav_slugs": list(THEME_SCOPED_NAV_SLUGS),
    }
    if client_delivery is not None:
        syndication_bootstrap["agent_client_delivery"] = client_delivery
    return SettingsSectionRender(
        body_html=body_html,
        extra_bootstrap={"syndication": syndication_bootstrap},
        extra_scripts_html=CONSOLE_SYNDICATION_SCRIPT_HTML,
    )
