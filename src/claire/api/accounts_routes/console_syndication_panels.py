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
from typing import Any

from ...core import ids
from ...i18n import interpolate, t
from ...store import accounts as accounts_store
from ...store import db as dbm
from ...store.knowledge_base import KnowledgeBaseInfo
from .common import TEMPLATES_DIR, AccountsContext, escape, format_ts, open_db
from .console_ui import console_card, console_notice, console_pill

DEFAULT_AGENT_MARKETPLACE_REPO = "fofwisdom/claire-agents"
_MARKETPLACE_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")

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


def _resolve_agent_marketplace_repo(ctx: AccountsContext, settings: Any) -> str:
    cfg = getattr(ctx, "config", None)
    raw = str(getattr(settings, "agent_marketplace_repo", None) or getattr(cfg, "agent_marketplace_repo", None) or "").strip()
    if _MARKETPLACE_REPO_RE.fullmatch(raw):
        return raw
    return DEFAULT_AGENT_MARKETPLACE_REPO


def _query_agent_onboarding_progress(conn: Any, account: accounts_store.Account, oauth_grants: list[dict[str, Any]]) -> dict[str, Any]:
    pending_row = conn.execute(
        "SELECT COUNT(*) FROM documents d JOIN knowledge_bases k ON k.id = d.kb_id WHERE k.account_id = ? AND d.extraction_status = 'pending'",
        (int(account.id),),
    ).fetchone()
    extracted_row = conn.execute(
        "SELECT COUNT(*) FROM extractions e JOIN knowledge_bases k ON k.id = e.kb_id WHERE k.account_id = ? AND e.provider = 'agent_client'",
        (int(account.id),),
    ).fetchone()
    pending_docs = int(pending_row[0]) if pending_row else 0
    agent_extractions = int(extracted_row[0]) if extracted_row else 0
    has_write_oauth = any({"write", "owner"} & set(str(g.get("scope") or "").replace(",", " ").split()) for g in oauth_grants)
    step1_ok = account.extraction_mode == "client"
    step2_ok = has_write_oauth
    step3_ok = agent_extractions > 0
    return {
        "step1_ok": step1_ok,
        "step2_ok": step2_ok,
        "step3_ok": step3_ok,
        "pending_docs": pending_docs,
        "agent_extractions": agent_extractions,
    }


def _render_agent_extraction_guide_block(account: accounts_store.Account, progress: dict[str, Any]) -> str:
    """Render the Agent-Driven Extraction quick start guide at the top of /settings/mcp."""
    mode = "client" if account.extraction_mode == "client" else "provider"
    mode_text = t("console.settings.account.extraction_mode_client") if mode == "client" else t("console.settings.account.extraction_mode_provider")
    step1_ok = bool(progress.get("step1_ok"))
    step2_ok = bool(progress.get("step2_ok"))
    step3_ok = bool(progress.get("step3_ok"))
    pending_docs = int(progress.get("pending_docs") or 0)
    agent_extractions = int(progress.get("agent_extractions") or 0)
    done_text = t("console.synd.mcp.guide_status_done")
    pending_text = t("console.synd.mcp.guide_status_pending")
    step3_badge = (
        t("console.synd.mcp.guide_step_3_counts_done", extracted=agent_extractions, pending=pending_docs)
        if step3_ok
        else t("console.synd.mcp.guide_step_3_counts_pending", extracted=agent_extractions, pending=pending_docs)
    )
    steps_spec = (
        (1, step1_ok, done_text if step1_ok else pending_text),
        (2, step2_ok, done_text if step2_ok else pending_text),
        (3, step3_ok, step3_badge),
    )
    done_count = sum(1 for _i, ok, _badge in steps_spec if ok)
    steps_html = "".join(
        f'<li data-step="{idx}" data-ok="{"true" if ok else "false"}">'
        f'<div class="pref-flow-head"><span>{escape(t(f"console.synd.mcp.guide_step_{idx}_title"))}</span>'
        f'{console_pill(badge_text, tone="ok" if ok else "danger")}</div>'
        f'<div>{escape(t(f"console.synd.mcp.guide_step_{idx}"))}</div>'
        "</li>"
        for idx, ok, badge_text in steps_spec
    )
    mode_pill = console_pill(mode_text, tone="ok" if step1_ok else "danger")
    progress_pill = console_pill(
        t("console.synd.mcp.guide_progress_badge", done=done_count, total=3),
        tone="ok" if done_count == 3 else ("warn" if done_count > 0 else "danger"),
    )
    body_html = (
        f'<p class="pref-desc">{escape(t("console.synd.mcp.guide_desc"))}</p>'
        '<div class="pref-row">'
        f'<div class="pref-row-label"><strong>{escape(t("console.synd.mcp.guide_mode_label"))}</strong></div>'
        '<div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">'
        f'{mode_pill}<code id="pref-mcp-extraction-mode" class="pref-mono">{escape(mode)}</code>'
        f'<a class="pref-btn" href="/settings/account#settings-extraction-mode">{escape(t("console.synd.mcp.guide_mode_change"))}</a>'
        "</div></div>"
        f'<h3 style="margin:14px 0 8px;display:flex;align-items:center;gap:8px;flex-wrap:wrap">{escape(t("console.synd.mcp.guide_steps_title"))} {progress_pill}</h3>'
        f'<ol class="pref-flow" style="margin:0 0 12px">{steps_html}</ol>'
        '<div style="display:flex;gap:8px;flex-wrap:wrap">'
        f'<a class="pref-btn pref-btn-accent" href="#pref-mcp-client-bundles"><span data-icon="puzzle"></span> {escape(t("console.synd.mcp.guide_jump_bundles"))}</a>'
        "</div>"
    )
    return console_card(
        escape(t("console.synd.mcp.guide_title")),
        body_html,
        id="pref-mcp-agent-guide",
        icon="bolt",
        split=False,
        extra_class="pref-card",
        attrs={"data-extraction-mode": mode},
    )


def _render_agent_marketplace_block(repo: str) -> str:
    git_url = f"https://github.com/{repo}.git"
    releases_url = f"https://github.com/{repo}/releases"
    agent_guidance = console_notice(
        f'<ul style="margin:0;padding-left:1.1rem;display:grid;gap:4px">'
        f'<li>{escape(t("console.synd.mcp.marketplace_guide_claude", repo=repo))}</li>'
        f'<li>{escape(t("console.synd.mcp.marketplace_guide_antigravity"))}</li>'
        "</ul>"
    )
    body_html = (
        f'<p class="pref-desc">{escape(t("console.synd.mcp.marketplace_desc"))}</p>'
        '<div class="pref-row">'
        f'<div class="pref-row-label"><strong>{escape(t("console.synd.mcp.marketplace_repo_label"))}</strong><span>{escape(t("console.synd.mcp.marketplace_repo_desc"))}</span></div>'
        f'<div class="pref-endpoint"><code id="pref-mcp-marketplace-repo" class="pref-mono">{escape(repo)}</code>'
        f'<button type="button" class="pref-btn" data-copy-target="pref-mcp-marketplace-repo">{escape(t("console.common.copy"))}</button></div>'
        "</div>"
        '<div class="pref-row">'
        f'<div class="pref-row-label"><strong>{escape(t("console.synd.mcp.marketplace_git_label"))}</strong><span>{escape(t("console.synd.mcp.marketplace_git_desc"))}</span></div>'
        f'<div class="pref-endpoint"><code id="pref-mcp-marketplace-git" class="pref-mono">{escape(git_url)}</code>'
        f'<button type="button" class="pref-btn" data-copy-target="pref-mcp-marketplace-git">{escape(t("console.common.copy"))}</button></div>'
        "</div>"
        '<div class="pref-row">'
        f'<div class="pref-row-label"><strong>{escape(t("console.synd.mcp.marketplace_releases_label"))}</strong><span>{escape(t("console.synd.mcp.marketplace_releases_desc"))}</span></div>'
        f'<a id="pref-mcp-marketplace-releases" class="pref-btn" href="{escape(releases_url)}" target="_blank" rel="noopener noreferrer">{escape(t("console.synd.mcp.marketplace_releases_link"))}</a>'
        "</div>"
        f'<div style="margin:8px 0 4px">{agent_guidance}</div>'
    )
    return console_card(
        escape(t("console.synd.mcp.marketplace_title")),
        body_html,
        id="pref-mcp-client-bundles",
        icon="puzzle",
        split=False,
        extra_class="pref-card",
        attrs={"data-marketplace-repo": repo},
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
    themes_href = "/moderation/themes" if admin_viewer else "/settings/kb"
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
    marketplace_repo: str | None = None
    onboarding_progress: dict[str, Any] = {}
    if slug == "mcp":
        marketplace_repo = _resolve_agent_marketplace_repo(ctx, settings)
        conn = open_db(ctx)
        try:
            oauth_grants = dbm.list_account_oauth_grants(conn, account.id)
            onboarding_progress = _query_agent_onboarding_progress(conn, account, oauth_grants)
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
        "agent_extraction_guide_html": _render_agent_extraction_guide_block(account, onboarding_progress) if slug == "mcp" else "",
        "agent_clients_delivery_html": _render_agent_marketplace_block(marketplace_repo) if marketplace_repo is not None else "",
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
    if marketplace_repo is not None:
        syndication_bootstrap["agent_marketplace"] = {
            "repo": marketplace_repo,
            "git_url": f"https://github.com/{marketplace_repo}.git",
            "releases_url": f"https://github.com/{marketplace_repo}/releases",
        }
    return SettingsSectionRender(
        body_html=body_html,
        extra_bootstrap={"syndication": syndication_bootstrap},
        extra_scripts_html=CONSOLE_SYNDICATION_SCRIPT_HTML,
    )
