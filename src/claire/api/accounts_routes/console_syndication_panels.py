"""``/settings/*`` Group 2–3 섹션 본문 (설계 P3-b: 신디케이션 · 리더, AI 에이전트 연동).

``console.py``가 Group 2–3 섹션을 그릴 때 `build_syndication_section`을 부른다. 이 모듈은 Lane P3-b 소유다.
관리자 전용 데이터(``mcp_telemetry`` 감사 행, ``panel-idp`` 운영 설정, ``panel-userdb`` 운영 디렉터리)는 절대 넣지 않는다.

섹션 본문은 ``templates/partials/settings_syndication/<slug>.html``에 있다. 이 파일들은 레거시
``syndication_preferences.html``의 패널 마크업을 옮긴 것이며, 컨트롤 ID를 그대로 둔다. 화면 상태는
``static/js/console_syndication.js``가 계정 · 테마별 localStorage 키(``claire_synd_prefs_v2:a{계정}:t{테마}``)에 저장한다.
"""

from __future__ import annotations

import string
from dataclasses import dataclass, field
from typing import Any

from ... import ids
from ...store import accounts as accounts_store
from ...store.theme import ThemeInfo
from .common import TEMPLATES_DIR, AccountsContext, escape

SYNDICATION_SECTION_SLUGS: frozenset[str] = frozenset(
    {"reader", "connections", "feeds", "tags", "rules", "outbound", "opml", "mcp", "webmcp"}
)

# 사이드바 링크 순서와 같다. JS가 테마 선택을 바꿀 때 이 슬러그의 링크(``data-nav-section``)에만 ``?theme=``를 갱신한다.
THEME_SCOPED_NAV_SLUGS: tuple[str, ...] = ("reader", "connections", "feeds", "tags", "rules", "outbound", "opml", "mcp", "webmcp")

PARTIALS_DIR = TEMPLATES_DIR / "partials" / "settings_syndication"
CONSOLE_SYNDICATION_SCRIPT_HTML = '<script src="/static/js/console_syndication.js" defer></script>'

# 레거시 패널과 같은 저장 알림(토스트) 요소. JS가 텍스트만 바꾼다.
_TOAST_HTML = '<div id="pref-toast" class="pref-toast" role="status" aria-live="polite">설정이 저장되었습니다</div>'


@dataclass(frozen=True, slots=True)
class SettingsSectionRender:
    """섹션 본문 HTML, 부트스트랩 JSON 추가 키, 추가 ``<script>`` 태그 HTML."""

    body_html: str
    extra_bootstrap: dict[str, Any] = field(default_factory=dict)
    extra_scripts_html: str = ""


def theme_ref(theme: ThemeInfo) -> str:
    """콘솔 테마 선택기와 같은 테마 참조값(외부 번호, 없으면 내부 id)."""
    return ids.format_public_id(theme.number) if theme.number is not None else str(theme.id)


def _account_is_admin(account: accounts_store.Account) -> bool:
    # ``Principal.instance_role``은 ``Account.role``에서 온다(security.py). /settings/*는 owner 스코프만 들어온다.
    return account.role == "admin"


def _syndication_base_href(mode: accounts_store.InstanceMode, account: accounts_store.Account) -> str:
    # S9: 싱글 모드에서는 USERNAME을 쓰지 않는다.
    if mode == "network" and account.username:
        return f"/@{account.username}/syndication"
    return "/syndication"


def _render_partial(slug: str, values: dict[str, str]) -> str:
    template = string.Template((PARTIALS_DIR / f"{slug}.html").read_text(encoding="utf-8"))
    # 빠진 자리가 있으면 KeyError를 낸다(C1).
    return template.substitute(values)


def _no_theme_body(slug: str, *, themes_href: str) -> str:
    return (
        f'<div class="pref-notice" data-tone="warn" id="settings-syndication-no-theme" data-section="{escape(slug)}">'
        "이 섹션은 테마별 설정입니다. 소유한 지식 테마가 없어서 표시할 수 없습니다. "
        f'<a href="{escape(themes_href)}">테마 관리</a>에서 테마를 먼저 만드세요.'
        "</div>"
    )


def build_syndication_section(
    ctx: AccountsContext,
    *,
    slug: str,
    path: str,
    mode: accounts_store.InstanceMode,
    account: accounts_store.Account,
    selected_theme: ThemeInfo | None,
    owned_themes: list[ThemeInfo],
    theme_query: str | None,
) -> SettingsSectionRender:
    if slug not in SYNDICATION_SECTION_SLUGS:
        raise KeyError(slug)
    admin_viewer = _account_is_admin(account)
    themes_href = "/admin/themes" if admin_viewer else "/settings/themes"
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

    client_href = f"{_syndication_base_href(mode, account)}?tab=client&theme={ref}"
    tokens_href = "/admin/tokens" if admin_viewer else "/settings/tokens"
    tokens_label = "🛠️ 관리자 콘솔 → 🔑 API/MCP 키" if admin_viewer else "⚙️ 내 설정 → 🔑 API · MCP 상시 이용 토큰"
    values = {
        "client_href": escape(client_href),
        "tokens_href": escape(tokens_href),
        "tokens_label": escape(tokens_label),
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
        "data-theme-collab": "true" if selected_theme.is_collaborator_accessible else "false",
        "data-theme-seed": str(seed_index),
    }
    attrs = " ".join(f'{name}="{escape(value)}"' for name, value in root_attrs.items())
    body_html = f"<div {attrs}>\n{panel_html}\n{_TOAST_HTML}\n</div>"
    return SettingsSectionRender(
        body_html=body_html,
        extra_bootstrap={
            "syndication": {
                "section": slug,
                "theme_ref": ref,
                "owned_theme_refs": owned_refs,
                "nav_slugs": list(THEME_SCOPED_NAV_SLUGS),
            }
        },
        extra_scripts_html=CONSOLE_SYNDICATION_SCRIPT_HTML,
    )
