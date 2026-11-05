"""통합 콘솔 라우트: ``/admin``, ``/admin/*``, ``/settings``, ``/settings/*``, ``/api/admin/mcp/telemetry`` (설계 D1–D3, P1).

- ``/admin`` 및 ``/admin/*``는 관리자(``instance_role == "admin"``, ``scope == "owner"``) 전용이며 비관리자 요청에는 404를 돌려준다(B1, N5).
- ``/settings`` 및 ``/settings/*``는 계정 소유자(``scope == "owner"``) 전용 ``_system("owner")`` 표면이다(D1).
- 관리자가 ``/settings/themes`` 또는 ``/settings/tokens``에 접근하면 ``/admin/themes`` 또는 ``/admin/tokens``로 302 리다이렉트하고, 통합 사이드바에서도 중복 노출하지 않는다(D3).
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

from ... import ids
from ...store import accounts as accounts_store
from ...store.telemetry import query_mcp_telemetry
from ...store.theme import ThemeInfo, ThemeManager, ThemeNotFound
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
    render_template,
)
from .console_syndication_panels import SYNDICATION_SECTION_SLUGS, SettingsSectionRender, build_syndication_section

# P3-a: Group 1 (내 계정 · 워크스페이스) 섹션이 쓰는 모듈.
from ...accounts import server_tier as server_tier_mod
from ...accounts.provider_policy import MEMBER_KEY_PROVIDERS, ServerTier
from ...store import auth_db
from ...store import usage_ledger
from . import keys as keys_routes
from .admin import ROLE_TEXT, STATUS_TEXT
from .common import open_auth_db

ADMIN_INDEX_PATH = "/admin"
SETTINGS_INDEX_PATH = "/settings"
ADMIN_MCP_TELEMETRY_API_PATH = "/api/admin/mcp/telemetry"


@dataclass(frozen=True, slots=True)
class ConsoleSectionSpec:
    slug: str
    path: str
    area: str  # "settings" | "admin"
    group_id: str
    group_label: str
    nav_label: str
    title: str
    badge: str
    description: str
    theme_scoped: bool = False


SETTINGS_GROUP_1_SPECS: tuple[ConsoleSectionSpec, ...] = (
    ConsoleSectionSpec(
        slug="account",
        path="/settings/account",
        area="settings",
        group_id="workspace",
        group_label="내 계정 · 워크스페이스",
        nav_label="👤 내 계정 · 프로필 · 보안",
        title="👤 내 계정 · 프로필 · 보안",
        badge="계정 전역",
        description="프로필 표시 이름, 비밀번호 및 단계 인증, 외부 IdP 연결, 할당량 현황 및 계정 수명주기를 관리합니다.",
    ),
    ConsoleSectionSpec(
        slug="keys",
        path="/settings/keys",
        area="settings",
        group_id="workspace",
        group_label="내 계정 · 워크스페이스",
        nav_label="🔐 BYOK 외부 API 키",
        title="🔐 BYOK 외부 API 키 및 서버 등급",
        badge="계정 전역",
        description="네트워크 모드 멤버 계정의 쓰기 전용 외부 프로바이더 API 키와 서버 등급 허용량을 관리합니다.",
    ),
    ConsoleSectionSpec(
        slug="themes",
        path="/settings/themes",
        area="settings",
        group_id="workspace",
        group_label="내 계정 · 워크스페이스",
        nav_label="📁 지식 테마 관리",
        title="📁 내 지식 테마 관리",
        badge="계정 전역",
        description="내 계정이 소유한 지식 테마의 생성, 공개 범위 및 메타데이터를 관리합니다.",
    ),
    ConsoleSectionSpec(
        slug="tokens",
        path="/settings/tokens",
        area="settings",
        group_id="workspace",
        group_label="내 계정 · 워크스페이스",
        nav_label="🔑 API · MCP 상시 키",
        title="🔑 내 API · MCP 상시 이용 토큰",
        badge="계정 전역",
        description="내 계정에 귀속된 API · MCP 상시 이용 토큰 발급·폐기 및 OAuth 2.1 승인 클라이언트를 관리합니다.",
    ),
)

SETTINGS_GROUP_2_SPECS: tuple[ConsoleSectionSpec, ...] = (
    ConsoleSectionSpec(
        slug="reader",
        path="/settings/reader",
        area="settings",
        group_id="syndication",
        group_label="신디케이션 · 리더 (테마별)",
        nav_label="📖 리더 · 목록 스타일",
        title="📖 리더 및 목록 스타일 환경설정",
        badge="선택 테마",
        description="작업 공간 기본 탭, 목록 밀도, 3단/2단 화면 분할, 수집 원문 ⇄ 적재 본문 전환 및 본문 서체를 설정합니다.",
        theme_scoped=True,
    ),
    ConsoleSectionSpec(
        slug="connections",
        path="/settings/connections",
        area="settings",
        group_id="syndication",
        group_label="신디케이션 · 리더 (테마별)",
        nav_label="🔌 연결 서버 계정 (AP · AT)",
        title="🔌 연결 서버 계정 (ActivityPub · AT Protocol)",
        badge="선택 테마",
        description="선택한 테마에서 클라이언트로 접속할 ActivityPub(Mastodon 5.0+) 및 AT Protocol 서버 계정을 관리합니다.",
        theme_scoped=True,
    ),
    ConsoleSectionSpec(
        slug="feeds",
        path="/settings/feeds",
        area="settings",
        group_id="syndication",
        group_label="신디케이션 · 리더 (테마별)",
        nav_label="📡 정기 수집 대상 · 폴더",
        title="📡 정기 수집 대상 및 카테고리 폴더 관리",
        badge="선택 테마",
        description="선택한 테마의 RSS/Atom 피드와 연결 계정 경유 ActivityPub · AT 수집 대상 및 주기를 관리합니다.",
        theme_scoped=True,
    ),
    ConsoleSectionSpec(
        slug="tags",
        path="/settings/tags",
        area="settings",
        group_id="syndication",
        group_label="신디케이션 · 리더 (테마별)",
        nav_label="🏷️ 태그=엔티티 적재 정책",
        title="🏷️ 수집 태그 → 적재 시 엔티티 승격 정책",
        badge="선택 테마",
        description="수집 원문 태그의 [태그] 파이프라인 전달, 잠정 엔티티 승격, 해시태그 역변환 및 노이즈 태그 필터를 설정합니다.",
        theme_scoped=True,
    ),
    ConsoleSectionSpec(
        slug="rules",
        path="/settings/rules",
        area="settings",
        group_id="syndication",
        group_label="신디케이션 · 리더 (테마별)",
        nav_label="⚡ 오거나이즈 · 자동 적재",
        title="⚡ 테마 DB 기반 오거나이즈 · 자동 적재 규칙",
        badge="선택 테마",
        description="엔티티 연관도(Affinity Score) 임계 점수와 자동 적재 전달 규칙을 관리합니다.",
        theme_scoped=True,
    ),
    ConsoleSectionSpec(
        slug="outbound",
        path="/settings/outbound",
        area="settings",
        group_id="syndication",
        group_label="신디케이션 · 리더 (테마별)",
        nav_label="🔁 부스트 · 적재 발행",
        title="🔁 연결 계정 부스트·인용 발행 및 적재 본문 공유 정책",
        badge="선택 테마",
        description="프로토콜별 기본 발행 모드, 공개 열람 링크 첨부 및 사용자 간 발행 본문 공유 정책(peerSharePolicy)을 설정합니다.",
        theme_scoped=True,
    ),
    ConsoleSectionSpec(
        slug="opml",
        path="/settings/opml",
        area="settings",
        group_id="syndication",
        group_label="신디케이션 · 리더 (테마별)",
        nav_label="📦 OPML · 툼스톤 진단",
        title="📦 OPML 구독 목록 입출력 및 소각 툼스톤 진단",
        badge="선택 테마",
        description="선택한 테마의 OPML 2.0 구독 목록 입출력과 purged_tombstones 재수집 차단 상태를 점검합니다.",
        theme_scoped=True,
    ),
)

SETTINGS_GROUP_3_SPECS: tuple[ConsoleSectionSpec, ...] = (
    ConsoleSectionSpec(
        slug="mcp",
        path="/settings/mcp",
        area="settings",
        group_id="agents",
        group_label="AI 에이전트 연동",
        nav_label="🧩 MCP 클라이언트 연결",
        title="🧩 MCP 서버 (/mcp) 클라이언트 연결 및 도구 안내",
        badge="계정 · 테마",
        description="외부 AI 하네스용 /mcp 엔드포인트, 테마 인자 예시, 연결 스니펫 및 읽기 전용 도구 목록을 확인합니다.",
        theme_scoped=True,
    ),
    ConsoleSectionSpec(
        slug="webmcp",
        path="/settings/webmcp",
        area="settings",
        group_id="agents",
        group_label="AI 에이전트 연동",
        nav_label="🕸️ WebMCP 페이지 도구",
        title="🕸️ WebMCP 페이지 도구 설정 및 시뮬레이터",
        badge="선택 테마",
        description="브라우저 내장 에이전트용 document.modelContext 페이지 도구 노출 여부, 사용자 확인 정책 및 호출 시뮬레이터를 제공합니다.",
        theme_scoped=True,
    ),
)

ADMIN_SIDEBAR_GROUP_4_SPECS: tuple[ConsoleSectionSpec, ...] = (
    ConsoleSectionSpec(
        slug="instance",
        path="/admin/instance",
        area="admin",
        group_id="admin_instance",
        group_label="인스턴스 · 계정 · 인증 (관리자)",
        nav_label="🖥️ 인스턴스 모드 · 서버 등급",
        title="🖥️ 인스턴스 설정 · 모드 전환 및 서버 등급",
        badge="서버 전역 · 관리자",
        description="싱글 ↔ 네트워크 모드 전환, G1–G9 준비 검사, 레거시 호스트 일몰 시계 및 기본 서버 등급을 관리합니다.",
    ),
    ConsoleSectionSpec(
        slug="accounts",
        path="/admin/accounts",
        area="admin",
        group_id="admin_instance",
        group_label="인스턴스 · 계정 · 인증 (관리자)",
        nav_label="👥 계정 · 초대 · 퇴역 이름",
        title="👥 사용자 계정 · 가입 초대 · 퇴역 USERNAME 관리",
        badge="서버 전역 · 관리자",
        description="계정 상태 전환, 초대 코드 발급·비활성화, 퇴역 USERNAME 정보 초기화 및 운영자 워크스페이스 현황을 관리합니다.",
    ),
    ConsoleSectionSpec(
        slug="idp",
        path="/admin/idp",
        area="admin",
        group_id="admin_instance",
        group_label="인스턴스 · 계정 · 인증 (관리자)",
        nav_label="🪪 외부 IdP 로그인 정책",
        title="🪪 외부 IdP 관리 · OIDC 공급자 및 가입 정책",
        badge="서버 전역 · 관리자",
        description="외부 IdP 공급자 등록, Discovery 검증, 이메일 도메인 제한 및 로그인 흐름 시뮬레이터를 관리합니다.",
    ),
)

ADMIN_CONSOLE_GROUP_5_SPECS: tuple[ConsoleSectionSpec, ...] = (
    ConsoleSectionSpec(
        slug="providers",
        path="/admin/providers",
        area="admin",
        group_id="admin_system",
        group_label="시스템 관리 및 설정 (관리자)",
        nav_label="⚡ 프로바이더",
        title="⚡ LLM · STT · VLM 및 하이퍼스케일러 프로바이더 설정",
        badge="서버 전역 · 관리자",
        description="서비스 루트 '관리 및 설정'의 인스턴스 전역 프로바이더 설정(data/providers.json) 및 연결 테스트를 관리합니다.",
    ),
    ConsoleSectionSpec(
        slug="limits",
        path="/admin/limits",
        area="admin",
        group_id="admin_system",
        group_label="시스템 관리 및 설정 (관리자)",
        nav_label="📏 적재 제한",
        title="📏 상황별 적재 제한 및 슬라이싱 예산 설정",
        badge="서버 전역 · 관리자",
        description="서비스 루트 '관리 및 설정'의 인스턴스 전역 적재 제한 및 예산(data/ingest_limits.json)을 관리합니다.",
    ),
    ConsoleSectionSpec(
        slug="themes",
        path="/admin/themes",
        area="admin",
        group_id="admin_system",
        group_label="시스템 관리 및 설정 (관리자)",
        nav_label="📁 테마 관리",
        title="📁 관리자 지식 테마 관리 및 인스턴스 슬롯 현황",
        badge="관리자 워크스페이스",
        description="관리자 계정의 지식 테마를 관리하고 인스턴스 전체 테마 슬롯 수 집계를 확인합니다(멤버 비공개 콘텐츠 비접근).",
    ),
    ConsoleSectionSpec(
        slug="tokens",
        path="/admin/tokens",
        area="admin",
        group_id="admin_system",
        group_label="시스템 관리 및 설정 (관리자)",
        nav_label="🔑 API/MCP 키",
        title="🔑 관리자 API · MCP 상시 이용 토큰 관리",
        badge="관리자 워크스페이스",
        description="관리자 계정의 API · MCP 상시 이용 토큰 발급·폐기 및 OAuth 클라이언트 현황을 관리합니다.",
    ),
    ConsoleSectionSpec(
        slug="mcp",
        path="/admin/mcp",
        area="admin",
        group_id="admin_system",
        group_label="시스템 관리 및 설정 (관리자)",
        nav_label="🩺 MCP 감사 · 진단 번들",
        title="🩺 MCP 호출 감사 기록(mcp_telemetry) 및 진단 번들",
        badge="서버 전역 · 관리자",
        description="인스턴스 전역 MCP 호출 기록 조회, RBAC 대조 진단, 전역 WebMCP 헤더 정책 및 지원 번들 생성을 관리합니다.",
    ),
)

ALL_SETTINGS_SPECS: tuple[ConsoleSectionSpec, ...] = (
    *SETTINGS_GROUP_1_SPECS,
    *SETTINGS_GROUP_2_SPECS,
    *SETTINGS_GROUP_3_SPECS,
)
ALL_ADMIN_SPECS: tuple[ConsoleSectionSpec, ...] = (
    *ADMIN_SIDEBAR_GROUP_4_SPECS,
    *ADMIN_CONSOLE_GROUP_5_SPECS,
)
SETTINGS_BY_PATH: dict[str, ConsoleSectionSpec] = {spec.path: spec for spec in ALL_SETTINGS_SPECS}
ADMIN_CONSOLE_BY_PATH: dict[str, ConsoleSectionSpec] = {spec.path: spec for spec in ADMIN_CONSOLE_GROUP_5_SPECS}
ADMIN_SPEC_BY_SLUG: dict[str, ConsoleSectionSpec] = {spec.slug: spec for spec in ALL_ADMIN_SPECS}


def _is_owner(principal: Principal) -> bool:
    return principal.scope == "owner" and principal.account_id is not None


def _redirect(location: str) -> Response:
    return Response(
        status_code=302,
        headers={
            "Location": location,
            "Cache-Control": "no-store",
            "Referrer-Policy": FORM_PAGE_REFERRER_POLICY,
        },
    )


def _append_theme_query(path: str, theme_query: str | None) -> str:
    if not theme_query:
        return path
    return f"{path}?{urlencode({'theme': theme_query})}"


def _resolve_caller_theme_and_list(
    ctx: AccountsContext,
    *,
    account_id: int,
    raw_theme: str | None,
) -> tuple[ThemeInfo | None, list[ThemeInfo], str | None] | None:
    """``?theme=``가 주어졌으면 호출자 계정의 테마인지 엄격히 검증한다(C1).

    유효하지 않거나 다른 계정의 테마면 ``None``을 돌려줘 호출자가 404를 내게 한다.
    """
    data_dir = ctx.data_dir if ctx.data_dir is not None else ctx.db_file.parent
    tm_settings = SimpleNamespace(
        db_file=ctx.db_file,
        db_path=str(ctx.db_file),
        data_dir=data_dir,
        multi_theme=True,
    )
    tm = ThemeManager(tm_settings)
    owned = tm.list_account_themes(account_id)
    if raw_theme is not None:
        cleaned = raw_theme.strip()
        if not cleaned:
            return None
        try:
            resolved = tm.resolve_theme_ref(cleaned, account_id=account_id)
        except ThemeNotFound:
            return None
        return resolved.theme, owned, cleaned
    default_theme = owned[0] if owned else None
    for t in owned:
        if t.is_default:
            default_theme = t
            break
    return default_theme, owned, None


def _account_context_info(ctx: AccountsContext, account_id: int) -> tuple[accounts_store.InstanceMode, accounts_store.Account]:
    conn = open_db(ctx)
    try:
        store = accounts_store.AccountStore(conn)
        return store.instance_mode(), store.get(account_id)
    finally:
        conn.close()


def _build_sidebar_html(
    *,
    active_path: str,
    admin_viewer: bool,
    theme_query: str | None,
) -> str:
    groups: list[tuple[str, list[ConsoleSectionSpec]]] = [
        (
            "내 계정 · 워크스페이스",
            [
                spec
                for spec in SETTINGS_GROUP_1_SPECS
                # D3: 관리자는 테마·토큰 관리를 /admin/themes·/admin/tokens 한 곳에서만 노출한다.
                if not (admin_viewer and spec.slug in {"themes", "tokens"})
            ],
        ),
        ("신디케이션 · 리더 (테마별)", list(SETTINGS_GROUP_2_SPECS)),
        ("AI 에이전트 연동", list(SETTINGS_GROUP_3_SPECS)),
    ]
    if admin_viewer:
        groups.append(("인스턴스 · 계정 · 인증 (관리자)", list(ADMIN_SIDEBAR_GROUP_4_SPECS)))
        groups.append(("시스템 관리 및 설정 (관리자)", list(ADMIN_CONSOLE_GROUP_5_SPECS)))

    parts: list[str] = []
    for group_label, specs in groups:
        parts.append(f'<div class="console-nav-group-label">{escape(group_label)}</div>')
        for spec in specs:
            href = _append_theme_query(spec.path, theme_query) if spec.theme_scoped else spec.path
            current = ' aria-current="page"' if spec.path == active_path else ""
            parts.append(
                f'<a href="{escape(href)}" class="console-nav-link" data-nav-section="{escape(spec.slug)}"{current}>{escape(spec.nav_label)}</a>'
            )
    return "\n      ".join(parts)


def _build_theme_selector_html(
    *,
    spec: ConsoleSectionSpec,
    owned_themes: list[ThemeInfo],
    selected_theme: ThemeInfo | None,
) -> str:
    if not spec.theme_scoped or not owned_themes:
        return ""
    options: list[str] = []
    selected_id = selected_theme.id if selected_theme is not None else None
    for t in owned_themes:
        ref = ids.format_public_id(t.number) if t.number is not None else str(t.id)
        sel = " selected" if t.id == selected_id else ""
        options.append(f'<option value="{escape(ref)}"{sel}>{escape(t.icon)} {escape(t.label)}</option>')
    return (
        '<label for="console-theme-select" style="font-size:12px;font-weight:700;color:var(--muted)">대상 테마</label>'
        f'<select id="console-theme-select" class="console-select" aria-label="대상 테마 선택">{"".join(options)}</select>'
    )


def _build_admin_section_body_and_extra(ctx: AccountsContext, slug: str) -> tuple[str, dict[str, Any]]:
    if slug == "providers":
        body = (
            '<div class="card" id="admin-providers-card" aria-label="프로바이더 설정">'
            '<div id="admin-providers-host" data-admin-panel-host="providers">'
            '<p class="hint">프로바이더 설정 화면을 불러오는 중입니다...</p>'
            "</div>"
            "</div>"
        )
        return body, {}

    if slug == "limits":
        body = (
            '<div class="card" id="admin-limits-card" aria-label="적재 제한 설정">'
            '<div id="admin-limits-host" data-admin-panel-host="limits">'
            '<p class="hint">적재 제한 설정 화면을 불러오는 중입니다...</p>'
            "</div>"
            "</div>"
        )
        return body, {}

    if slug == "themes":
        conn = open_db(ctx)
        try:
            total_accounts = int(conn.execute("SELECT COUNT(*) FROM accounts WHERE status != 'purged'").fetchone()[0])
            total_themes = int(conn.execute("SELECT COUNT(*) FROM themes").fetchone()[0])
            admin_themes = int(conn.execute("SELECT COUNT(*) FROM themes WHERE account_id = 1 OR account_id IS NULL").fetchone()[0])
        finally:
            conn.close()
        body = (
            '<section class="card" id="admin-themes-summary" aria-label="인스턴스 테마 슬롯 현황">'
            '<h2>📊 인스턴스 전체 테마 슬롯 현황 <span class="badge">집계 전용 · 비공개 콘텐츠 비접근 (D5/K5)</span></h2>'
            '<p class="hint">운영자는 인스턴스 전체 계정 수와 테마 슬롯 개수만 확인하며, 일반 멤버의 테마 이름·설명·문서 본문은 절대 열람하지 않습니다.</p>'
            '<div class="pref-stat-row" id="admin-themes-stats">'
            f'<div class="pref-stat"><b data-stat="total_accounts">{total_accounts}</b><span>활성·등록 계정 수 (total_accounts)</span></div>'
            f'<div class="pref-stat"><b data-stat="total_themes">{total_themes}</b><span>인스턴스 전체 테마 슬롯 (total_themes)</span></div>'
            f'<div class="pref-stat"><b data-stat="admin_themes">{admin_themes}</b><span>관리자 소유 테마 수</span></div>'
            "</div>"
            "</section>"
            '<section class="card" id="admin-themes-manager-card" aria-label="관리자 지식 테마 관리">'
            '<div id="admin-themes-host" data-admin-panel-host="themes">'
            '<p class="hint">관리자 지식 테마 목록을 불러오는 중입니다...</p>'
            "</div>"
            "</section>"
        )
        return body, {
            "theme_summary": {
                "total_accounts": total_accounts,
                "total_themes": total_themes,
                "admin_themes": admin_themes,
            }
        }

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
            exp_str = format_ts(max_exp) if max_exp else "발급 토큰 없음"
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
            else '<tr><td colspan="4" style="color:var(--muted);text-align:center;">등록된 OAuth 2.1 클라이언트가 없습니다.</td></tr>'
        )
        body = (
            '<section class="card" id="admin-tokens-manager-card" aria-label="관리자 상시 이용 토큰 관리">'
            '<div id="admin-tokens-host" data-admin-panel-host="tokens">'
            '<p class="hint">관리자 상시 이용 토큰 목록을 불러오는 중입니다...</p>'
            "</div>"
            "</section>"
            '<section class="card" id="admin-oauth-clients-card" aria-label="OAuth 2.1 등록 클라이언트">'
            '<h2>🔐 OAuth 2.1 등록 및 승인 클라이언트 현황</h2>'
            '<p class="hint"><code>/oauth/register</code>(DCR) 및 <code>/oauth/authorize</code>로 등록된 원격 MCP 클라이언트 목록입니다. 토큰 원문은 저장·표시하지 않습니다.</p>'
            '<div class="pref-table-wrap">'
            '<table class="pref-table">'
            '<thead><tr><th>클라이언트 이름</th><th>client_id · redirect_uris</th><th>등록 시각</th><th>만료 시각</th></tr></thead>'
            f'<tbody id="pref-mcp-clients-tbody">{clients_tbody}</tbody>'
            "</table>"
            "</div>"
            "</section>"
            '<section class="card" id="admin-token-policy-card" aria-label="인스턴스 토큰 정책 요약">'
            f'<h2>🛡️ 인스턴스 인증 채널 및 토큰 정책 {prototype_badge("admin-token-policy-summary", "static")}</h2>'
            f'{prototype_note("admin-token-policy-summary", "코드 기본값을 옮겨 적은 고정 안내문입니다. 실행 중인 설정을 읽어 오지 않으며, 여기서 정책을 바꿀 수 없습니다.")}'
            '<dl>'
            '<dt>상시 이용 토큰 (<code>cb_…</code>)</dt><dd>계정별 격리 발급 · <code>owner</code> / <code>collaborator</code> / <code>readonly</code> 권한 · 즉시 폐기(<code>/tokens/revoke</code>)</dd>'
            '<dt>OAuth 2.1 원격 커넥터</dt><dd>PKCE(S256) 필수 · <code>readonly</code> 스코프 고정 · Access 30일 / Refresh 1년 회전</dd>'
            '<dt>세션 쿠키</dt><dd>7일 슬라이딩 만료 · 민감 작업 시 15분 단계 인증(<code>/api/auth/reauth</code>) 요구</dd>'
            "</dl>"
            "</section>"
        )
        return body, {"oauth_clients": oauth_clients_list}

    if slug == "mcp":
        body = (
            '<section class="card" id="admin-mcp-telemetry-card" aria-label="MCP 호출 감사 기록">'
            '<div style="display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap">'
            '<h2>📜 실시간 MCP 호출 감사 기록 (<code>mcp_telemetry</code>)</h2>'
            '<div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">'
            '<label style="font-size:12px;font-weight:600">상태 <select id="admin-mcp-filter-status" class="pref-select">'
            '<option value="">전체 상태</option>'
            '<option value="SUCCESS">SUCCESS</option>'
            '<option value="success">success</option>'
            '<option value="ERROR">ERROR</option>'
            '<option value="UNAUTHORIZED">UNAUTHORIZED</option>'
            '<option value="auth_error">auth_error</option>'
            "</select></label>"
            '<label style="font-size:12px;font-weight:600">도구 <input type="text" id="admin-mcp-filter-tool" class="pref-input" placeholder="예: search" style="width:130px"/></label>'
            '<label style="font-size:12px;font-weight:600">표시 건수 <select id="admin-mcp-filter-limit" class="pref-select">'
            '<option value="25">25건</option>'
            '<option value="50" selected>50건</option>'
            '<option value="100">100건</option>'
            '<option value="200">200건</option>'
            "</select></label>"
            '<button type="button" id="admin-mcp-refresh-btn" class="pref-btn">↻ 새로고침</button>'
            "</div>"
            "</div>"
            '<p class="hint"><code>GET /api/admin/mcp/telemetry</code>에서 조회한 최신 도구 호출 및 인증 감사 기록입니다. 인자 및 응답 본문은 저장하지 않습니다.</p>'
            '<p class="result" id="admin-mcp-telemetry-status" role="status" aria-live="polite"></p>'
            '<div class="pref-table-wrap">'
            '<table class="pref-table">'
            '<thead><tr><th>시각</th><th>call_type</th><th>tool_name</th><th>status</th><th>duration_ms</th><th>response_bytes</th><th>client_id</th></tr></thead>'
            '<tbody id="pref-mcp-audit-tbody"><tr><td colspan="7" style="color:var(--muted);text-align:center;">감사 기록을 불러오는 중입니다...</td></tr></tbody>'
            "</table>"
            "</div>"
            "</section>"
            '<section class="card" id="admin-mcp-rbac-card" aria-label="RBAC 설계 대조 진단">'
            f'<h2>🧭 RBAC 설계 vs 현재 MCP 접근 판정 대조 {prototype_badge("admin-mcp-rbac-table", "static")}</h2>'
            f'{prototype_note("admin-mcp-rbac-table", "고정 문구로 그린 설계 대조표입니다. 실제 권한 판정을 실행하거나 서버 정책을 읽어 오지 않으며, 판정 칸도 고정값입니다.")}'
            '<p class="hint">권한 범위별 REST RBAC 정책과 <code>/mcp</code> 엔드포인트 도구 호출 권한 대조표입니다.</p>'
            '<div class="pref-table-wrap">'
            '<table class="pref-table">'
            '<thead><tr><th>권한 (Scope)</th><th>RBAC 설계 (REST)</th><th>현재 MCP (/mcp)</th><th>판정</th></tr></thead>'
            '<tbody id="pref-mcp-rbac-tbody">'
            '<tr><td><code class="pref-mono">owner</code></td><td>읽기 · 쓰기 (자신 소유 테마)</td><td>읽기 전용 도구 10종 실행</td><td><span class="pref-pill pref-pill-mcp">일치</span></td></tr>'
            '<tr><td><code class="pref-mono">collaborator</code></td><td>협업 허용 테마 읽기 · 쓰기 (Theme 0 차단)</td><td>읽기 전용 도구 실행</td><td><span class="pref-pill pref-pill-mcp">점검 대상</span></td></tr>'
            '<tr><td><code class="pref-mono">readonly</code></td><td>공개·허용 테마 읽기 전용</td><td>읽기 전용 도구 실행</td><td><span class="pref-pill pref-pill-mcp">일치</span></td></tr>'
            '<tr><td><code class="pref-mono">anonymous</code></td><td>공개 테마 열람 (비공개 404 은폐)</td><td>항상 거부 (401 Unauthorized)</td><td><span class="pref-pill">더 엄격</span></td></tr>'
            "</tbody>"
            "</table>"
            "</div>"
            "</section>"
            '<section class="card" id="admin-webmcp-policy-card" aria-label="전역 WebMCP 정책">'
            f'<h2>🕸️ 전역 WebMCP 브라우저 페이지 도구 정책 (<code>Permissions-Policy</code>) {prototype_badge("admin-webmcp-global-switch", "client_only")}</h2>'
            f'{prototype_note("admin-webmcp-global-switch", "이 스위치는 지금 보고 있는 브라우저(localStorage)에만 기억됩니다. 서버 설정을 바꾸지 않고 Permissions-Policy 헤더도 보내지 않으며, 다른 운영자·멤버 화면에 적용되지 않습니다.")}'
            '<div class="pref-row">'
            '<div class="pref-row-label">'
            '<strong>WebMCP 페이지 도구 전역 허용 (<code>document.modelContext</code>)</strong>'
            '<span>비활성화 시 브라우저 에이전트의 페이지 도구 등록을 차단하고 <code>Permissions-Policy: tools=()</code> 헤더 정책을 적용하는 것이 설계 목표입니다(서버 쪽 미구현).</span>'
            "</div>"
            '<input id="pref-webmcp-enabled" type="checkbox" switch checked aria-label="WebMCP 도구 등록 전역 허용"/>'
            "</div>"
            '<p class="hint" id="admin-webmcp-policy-hint">현재 상태: 활성 (테마별 상세 노출 설정은 <code>/settings/webmcp</code>에서 구성합니다).</p>'
            "</section>"
            '<section class="card" id="admin-support-bundle-card" aria-label="진단 지원 번들 생성">'
            '<h2>📦 진단 지원 번들 (<code>POST /support/bundle</code>) 내보내기</h2>'
            '<p class="hint">최근 텔레메트리, 스키마 상태, 환경 구성을 비밀정보 마스킹 후 압축 번들로 생성합니다.</p>'
            '<form id="admin-support-bundle-form" data-support-bundle-form style="display:flex;gap:10px;align-items:flex-end;flex-wrap:wrap">'
            '<label style="display:flex;flex-direction:column;gap:4px;font-size:12px;font-weight:600">수집 기간 (일)'
            '<input type="number" name="days" id="admin-support-bundle-days" class="pref-input" min="1" max="30" value="7" style="width:90px" required/>'
            "</label>"
            '<button type="submit" class="pref-btn pref-btn-accent" id="admin-support-bundle-btn">🩺 지원 번들 생성</button>'
            "</form>"
            '<p class="result" id="admin-support-bundle-result" data-support-bundle-result role="status" aria-live="polite"></p>'
            "</section>"
        )
        return body, {}

    return (
        f'<div class="console-placeholder" data-area="admin" data-slug="{escape(slug)}">'
        f'<p class="console-desc">경로: <code>/admin/{escape(slug)}</code></p>'
        "</div>"
    ), {}


# /settings/account?notice= 안내문 (옛 /me?notice=). 목록에 없는 값은 아무것도 보여 주지 않는다(표시용 힌트일 뿐 식별자가 아니다).
SETTINGS_ACCOUNT_NOTICE_TEXT = {
    "password_not_saved": "계정 설정은 확정됐지만 비밀번호를 저장하지 못했습니다. 아래에서 비밀번호를 설정하세요.",
    "already_linked": "이 외부 계정은 이미 현재 계정에 연결되어 있습니다.",
    "identity_linked": "외부 계정이 성공적으로 연결되었습니다.",
}


def _build_settings_account_section(
    ctx: AccountsContext,
    *,
    spec: ConsoleSectionSpec,
    principal: Principal,
    mode: accounts_store.InstanceMode,
    account: accounts_store.Account,
    owned_themes: list[ThemeInfo],
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
    elif spec.slug == "keys":
        body, extra = _settings_keys_body(ctx, principal=principal, mode=mode, account=account, admin_viewer=admin_viewer)
    elif spec.slug == "themes":
        if admin_viewer:
            # D3: 라우터가 관리자를 /admin/themes로 보낸다. 여기에 오면 결함이다.
            raise RuntimeError("/settings/themes must redirect the admin to /admin/themes")
        body, extra = _settings_themes_body(ctx, account=account, owned_themes=owned_themes)
    elif spec.slug == "tokens":
        if admin_viewer:
            raise RuntimeError("/settings/tokens must redirect the admin to /admin/tokens")
        body, extra = _settings_tokens_body(ctx, account=account)
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
        themes = server_tier_mod.account_theme_count(conn, account_id)
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
        "themes": themes,
        "documents": documents,
        "storage_bytes": storage_bytes,
        "period": usage.period,
        "tokens_total": usage.tokens_total,
        "daily_day": usage.daily_day,
        "daily_ingests": usage.daily_ingests,
    }


def _settings_tier_rows_html(tier: ServerTier) -> str:
    features = ", ".join(sorted(tier.features)) if tier.features else "없음"
    rows = (
        ("서버 등급 프로바이더", "없음 (none)" if tier.provider == "none" else tier.provider),
        ("모델", tier.model or "-"),
        ("월 LLM 토큰 상한 (UTC 월)", f"{tier.monthly_tokens:,}"),
        ("일 적재 건수 상한 (UTC 일)", f"{tier.daily_ingests:,}"),
        ("최대 처리 강도 (max_effort)", tier.max_effort),
        ("허용 기능", features),
        ("저장 용량 상한", f"{tier.storage_mb:,} MB"),
        ("테마 수 상한", f"{tier.max_themes:,}"),
        ("문서 수 상한", f"{tier.max_documents:,}"),
        ("BYOK 동시 작업 상한", f"{tier.byok_max_concurrency:,}"),
    )
    return "".join(f"<dt>{escape(k)}</dt><dd>{escape(v)}</dd>" for k, v in rows)


def _settings_usage_stats_html(usage: dict[str, Any], *, admin_viewer: bool) -> str:
    tier: ServerTier = usage["tier"]
    if admin_viewer:
        stats = (
            ("themes", f"{usage['themes']:,}", "내 테마 수"),
            ("documents", f"{usage['documents']:,}", "내 문서 수"),
            ("storage", _settings_format_bytes(usage["storage_bytes"]), "저장 용량 (근사)"),
        )
    else:
        stats = (
            ("themes", f"{usage['themes']:,} / {tier.max_themes:,}", "테마 수 / 상한"),
            ("documents", f"{usage['documents']:,} / {tier.max_documents:,}", "문서 수 / 상한"),
            ("storage", f"{_settings_format_bytes(usage['storage_bytes'])} / {tier.storage_mb:,} MB", "저장 용량 (근사) / 상한"),
            ("tokens", f"{usage['tokens_total']:,} / {tier.monthly_tokens:,}", f"이번 달 LLM 토큰 ({usage['period']} UTC) / 서버 등급 상한"),
            ("ingests", f"{usage['daily_ingests']:,} / {tier.daily_ingests:,}", f"오늘 적재 건수 ({usage['daily_day']} UTC) / 상한"),
        )
    return "".join(
        f'<div class="pref-stat" data-usage-stat="{escape(key)}"><b>{escape(value)}</b><span>{escape(label)}</span></div>'
        for key, value, label in stats
    )


def _settings_reauth_form_html(*, has_password: bool, form_id: str) -> str:
    if not has_password:
        return (
            f'<section class="console-subcard" id="{escape(form_id)}" data-settings-reauth="none" aria-label="본인 확인">'
            "<h2>🔐 본인 확인</h2>"
            '<p class="hint">이 계정에는 아직 비밀번호가 없습니다. 로그인 링크로 방금 들어온 세션은 15분 동안 본인 확인을 대신합니다. 시간이 지났으면 새 로그인 링크로 다시 로그인하세요.</p>'
            "</section>"
        )
    return (
        f'<form class="console-subcard" id="{escape(form_id)}" data-settings-form="reauth" aria-label="본인 확인">'
        "<h2>🔐 본인 확인 (단계 인증)</h2>"
        '<p class="hint">비밀번호 변경, 외부 계정 연결·해제, API 키 등록·삭제는 15분 안에 본인 확인을 해야 합니다.</p>'
        f'<label style="display:flex;flex-direction:column;gap:4px;font-size:12.5px;font-weight:600">현재 비밀번호<input name="password" type="password" class="pref-input" required autocomplete="current-password"/></label>'
        '<button type="submit" class="pref-btn pref-btn-accent">본인 확인</button>'
        '<p class="result" data-settings-result role="status" aria-live="polite"></p>'
        "</form>"
    )


def _settings_enabled_idp_providers(ctx: AccountsContext) -> list[auth_db.IdpProviderMeta]:
    data_dir = ctx.data_dir or ctx.auth_db_file.parent.parent
    return [p for p in auth_db.load_idp_providers(data_dir, include_secrets=False) if p.enabled]


def _settings_account_body(
    ctx: AccountsContext,
    *,
    principal: Principal,
    mode: accounts_store.InstanceMode,
    account: accounts_store.Account,
    owned_themes: list[ThemeInfo],
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
    info_rows: list[tuple[str, str, str]] = [("public_id", "번호", str(account.public_id))]
    if mode == "network":
        info_rows.append(("username", "USERNAME", f"@{account.username}" if account.username else "(설정 전)"))
    info_rows.extend(
        (
            ("display_name", "표시 이름", account.display_name),
            ("role", "역할", ROLE_TEXT.get(account.role, account.role)),
            ("status", "상태", STATUS_TEXT.get(account.status, account.status)),
            ("password", "비밀번호", "설정됨" if has_password else "설정 안 됨"),
        )
    )
    if mode == "network":
        info_rows.append(("profile_public", "프로필 공개", "공개 (공개 디렉터리에 표시)" if account.profile_public else "비공개"))
    info_html = "".join(f'<dt>{escape(label)}</dt><dd data-account-field="{escape(key)}">{escape(value)}</dd>' for key, label, value in info_rows)
    profile_hint = (
        '<p class="hint" data-profile-public-hint>프로필 공개 범위를 바꾸는 API가 아직 없어 이 화면에서는 현재 값만 보여 줍니다.</p>'
        if mode == "network"
        else ""
    )

    current_pw_html = (
        '<label style="display:flex;flex-direction:column;gap:4px;font-size:12.5px;font-weight:600">현재 비밀번호<input name="current_password" type="password" class="pref-input" required autocomplete="current-password"/></label>'
        if has_password
        else '<p class="hint">이 계정에는 아직 비밀번호가 없습니다. 본인 확인 시간(15분) 안에 새 비밀번호를 설정하세요.</p>'
    )
    min_len = auth_db.MIN_PASSWORD_LENGTH

    provider_buttons = "".join(
        f'<button type="button" class="pref-btn" data-identity-link="{escape(p.id)}">+ {escape(p.display_name)} 연결</button>'
        for p in providers
    )
    link_html = (
        f'<div style="display:flex;gap:8px;flex-wrap:wrap" data-identity-link-list>{provider_buttons}</div>'
        if provider_buttons
        else '<p class="hint" data-identity-link-empty>이 인스턴스에서 연결할 수 있는 외부 로그인 공급자가 없습니다.</p>'
    )

    bundle_options = "".join(
        f'<option value="{escape(ids.format_public_id(t.number) if t.number is not None else str(t.id))}">{escape(t.icon)} {escape(t.label)}</option>'
        for t in owned_themes
    )
    if bundle_options:
        bundle_html = (
            '<form style="display:flex;gap:10px;align-items:flex-end;flex-wrap:wrap" data-settings-form="bundle-export">'
            f'<label style="display:flex;flex-direction:column;gap:4px;font-size:12.5px;font-weight:600">내보낼 테마<select name="theme" class="pref-select" required>{bundle_options}</select></label>'
            '<button type="submit" class="pref-btn pref-btn-accent">⬇ 그래프 번들 만들기</button>'
            "</form>"
            '<p class="result" data-settings-result role="status" aria-live="polite"></p>'
            '<div data-bundle-output aria-live="polite"></div>'
        )
    else:
        bundle_html = '<p class="hint" data-bundle-empty>내 계정이 소유한 테마가 없어 내보낼 번들이 없습니다.</p>'

    if admin_viewer:
        lifecycle_html = '<p class="hint" data-lifecycle="admin">관리자 계정은 인스턴스 소유 계정이므로 삭제할 수 없습니다.</p>'
    elif account.status == "deleting":
        lifecycle_html = (
            '<div class="pref-notice" data-tone="warn" data-lifecycle="deleting">'
            f"이 계정은 삭제 예약 상태입니다. 예정 시각: {escape(format_ts(account.delete_after))}. 유예 기간 안에 취소하려면 인스턴스 운영자에게 요청하세요."
            "</div>"
        )
    else:
        lifecycle_html = (
            '<p class="hint" data-lifecycle="member">계정 삭제는 인스턴스 운영자가 예약합니다. 예약하면 즉시 로그인 세션과 토큰을 끊고, 7일 유예 뒤 내 테마·문서를 소각합니다.</p>'
            '<p class="hint">이 화면에서 직접 보낼 수 있는 삭제 요청 API는 아직 없습니다. 삭제를 원하면 인스턴스 운영자에게 요청하세요. 삭제 전에 위의 그래프 번들로 내 테마를 내보낼 수 있습니다.</p>'
        )

    quota_hint = (
        "관리자 계정은 서버 등급 한도를 받지 않습니다. 아래는 내 계정의 현재 사용량입니다."
        if admin_viewer
        else "서버 등급 한도는 BYOK 키를 등록해도 자원 한도(테마·문서·저장 용량·일 적재 건수)에 적용됩니다. 월 토큰 상한은 서버 등급 프로바이더를 쓸 때만 적용됩니다."
    )

    notice_text = SETTINGS_ACCOUNT_NOTICE_TEXT.get(notice or "")
    notice_html = f'<p class="pref-notice" data-account-notice="{escape(notice or "")}" role="status">{escape(notice_text)}</p>' if notice_text else ""
    body = (
        f"{notice_html}"
        '<section class="console-subcard" id="settings-account-info" aria-label="계정 정보">'
        "<h2>🪪 계정 정보</h2>"
        f"<dl>{info_html}</dl>"
        f"{profile_hint}"
        "</section>"
        '<form class="console-subcard" id="settings-display-name" data-settings-form="display-name" aria-label="표시 이름">'
        "<h2>🏷️ 표시 이름</h2>"
        f'<label style="display:flex;flex-direction:column;gap:4px;font-size:12.5px;font-weight:600">표시 이름<input name="display_name" type="text" class="pref-input" value="{escape(account.display_name)}" maxlength="64" autocomplete="name"/></label>'
        '<button type="submit" class="pref-btn pref-btn-accent">저장</button>'
        '<p class="result" data-settings-result role="status" aria-live="polite"></p>'
        "</form>"
        f"{_settings_reauth_form_html(has_password=has_password, form_id='settings-account-reauth')}"
        f'<form class="console-subcard" id="settings-password" data-settings-form="password" data-has-password="{"1" if has_password else "0"}" aria-label="비밀번호">'
        f"<h2>🔑 {'비밀번호 변경' if has_password else '비밀번호 설정'}</h2>"
        f"{current_pw_html}"
        f'<label style="display:flex;flex-direction:column;gap:4px;font-size:12.5px;font-weight:600">새 비밀번호<input name="new_password" type="password" class="pref-input" required minlength="{min_len}" autocomplete="new-password"/></label>'
        f'<label style="display:flex;flex-direction:column;gap:4px;font-size:12.5px;font-weight:600">새 비밀번호 확인<input name="new_password_confirm" type="password" class="pref-input" required minlength="{min_len}" autocomplete="new-password"/></label>'
        f"<p class=\"hint\">{min_len}자 이상. 영문 대소문자, 숫자, 표준 특수문자만 쓸 수 있고 공백·한글은 쓸 수 없습니다. USERNAME을 넣을 수 없습니다. 바꾸면 이 세션을 뺀 다른 로그인 세션은 모두 끊깁니다.</p>"
        '<button type="submit" class="pref-btn pref-btn-accent">저장</button>'
        '<p class="result" data-settings-result role="status" aria-live="polite"></p>'
        "</form>"
        '<section class="console-subcard" id="settings-identities" data-settings-identities aria-label="연결된 외부 로그인">'
        "<h2>🔗 연결된 외부 로그인 (OIDC · GitHub)</h2>"
        '<p class="hint">연결과 해제는 본인 확인을 먼저 해야 합니다. 비밀번호가 없으면 마지막 외부 로그인은 해제할 수 없습니다.</p>'
        '<div class="pref-table-wrap"><table class="pref-table">'
        "<thead><tr><th>공급자</th><th>연결 시각</th><th>마지막 로그인</th><th></th></tr></thead>"
        '<tbody data-identities-tbody><tr><td colspan="4" style="color:var(--muted);text-align:center;">연결 목록을 불러오는 중입니다...</td></tr></tbody>'
        "</table></div>"
        f"{link_html}"
        '<p class="result" data-settings-result role="status" aria-live="polite"></p>'
        "</section>"
        '<section class="console-subcard" id="settings-quota" aria-label="할당량 및 사용량">'
        "<h2>📏 할당량 및 사용량</h2>"
        f'<p class="hint">{escape(quota_hint)}</p>'
        f'<div class="pref-stat-row" data-usage-stats>{_settings_usage_stats_html(usage, admin_viewer=admin_viewer)}</div>'
        "</section>"
        '<section class="console-subcard" id="settings-bundle" aria-label="테마 그래프 번들 내보내기">'
        "<h2>📦 내 테마 그래프 번들 내보내기</h2>"
        '<p class="hint">선택한 내 테마의 엔티티·관계·문서를 그래프 번들(<code>POST /graph/bundle</code>)로 묶고 본인 전용 내려받기 링크를 만듭니다. 링크는 만료 시각까지만 유효합니다.</p>'
        f"{bundle_html}"
        "</section>"
        '<section class="console-subcard" id="settings-account-lifecycle" aria-label="계정 삭제">'
        "<h2>🗑️ 계정 삭제</h2>"
        f"{lifecycle_html}"
        "</section>"
    )
    extra = {
        "has_password": has_password,
        "identity_providers": [{"id": p.id, "display_name": p.display_name, "type": p.type} for p in providers],
    }
    return body, extra


def _settings_keys_body(
    ctx: AccountsContext,
    *,
    principal: Principal,
    mode: accounts_store.InstanceMode,
    account: accounts_store.Account,
    admin_viewer: bool,
) -> tuple[str, dict[str, Any]]:
    if admin_viewer:
        # 관리자는 BYOK 키 대신 인스턴스 프로바이더 설정을 쓴다(keys.py: 403 admin_uses_provider_settings).
        notice = "싱글 모드에서는 인스턴스 프로바이더 설정이 모든 LLM·STT·VLM 작업에 쓰입니다." if mode == "single" else "관리자 계정은 BYOK 키 대신 인스턴스 프로바이더 설정을 씁니다."
        body = (
            '<section class="console-subcard" id="settings-keys-admin-providers" data-keys-mode="admin" aria-label="프로바이더 설정 안내">'
            "<h2>⚡ 인스턴스 프로바이더 설정</h2>"
            f'<p class="hint">{escape(notice)}</p>'
            '<a class="pref-btn pref-btn-accent" href="/admin/providers">⚡ 프로바이더 설정 열기</a>'
            "</section>"
        )
        return body, {"keys_mode": "admin"}
    if mode != "network" or principal.instance_role != "member":
        # 싱글 모드의 비관리자: /api/me/keys는 404이고 관리자 화면도 쓸 수 없다. 관리자 링크 없이 안내만 한다(fail-closed).
        body = (
            '<section class="console-subcard" id="settings-keys-unavailable" data-keys-mode="unavailable" aria-label="API 키 안내">'
            "<h2>🔐 외부 API 키</h2>"
            '<p class="hint">이 인스턴스는 싱글 모드로 운영 중이므로 개인 API 키(BYOK)를 등록할 수 없습니다. LLM·STT·VLM 프로바이더는 인스턴스 운영자가 관리합니다.</p>'
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
        '<section class="console-subcard" id="settings-keys-meta" data-keys-mode="byok" aria-label="등록한 API 키">'
        "<h2>🔐 내 API 키 (쓰기 전용)</h2>"
        '<p class="hint">LLM 작업에 쓸 본인 API 키입니다. 등록한 키는 암호화해 저장하고, 다시 보거나 복사할 수 없습니다. 화면과 API에는 키에서 파생한 fingerprint만 나옵니다. 키가 거부되거나 할당량이 다 되면 작업은 실패하며, 서버 등급이나 다른 키로 바꿔 쓰지 않습니다.</p>'
        '<p class="hint">한계: 이 보호는 애플리케이션 경로로 키를 읽는 일을 막습니다. 서버가 키로 API를 호출하려면 복호화할 수밖에 없으므로, 서버 호스트의 root 권한자는 막지 못합니다.</p>'
        f'<div data-provider-key-meta aria-live="polite">{keys_routes._meta_rows(items)}</div>'
        "</section>"
        f"{_settings_reauth_form_html(has_password=has_password, form_id='settings-keys-reauth')}"
        '<form class="console-subcard" id="settings-keys-put" data-settings-form="provider-key-put" aria-label="키 등록·교체">'
        "<h2>➕ 키 등록·교체</h2>"
        f'<label style="display:flex;flex-direction:column;gap:4px;font-size:12.5px;font-weight:600">프로바이더<select name="provider" class="pref-select">{provider_options}</select></label>'
        f'<label style="display:flex;flex-direction:column;gap:4px;font-size:12.5px;font-weight:600">API 키<input name="api_key" type="password" class="pref-input" required autocomplete="off" spellcheck="false" maxlength="{keys_routes.API_KEY_MAX_LENGTH}"/></label>'
        '<p class="hint">저장하기 전에 시험 호출로 키를 확인하고, 실패하면 저장하지 않습니다. 이미 등록한 키가 있으면 새 키로 바꿉니다.</p>'
        '<button type="submit" class="pref-btn pref-btn-accent">확인 후 저장</button>'
        '<p class="result" data-settings-result role="status" aria-live="polite"></p>'
        "</form>"
        '<form class="console-subcard" id="settings-keys-verify" data-settings-form="provider-key-verify" aria-label="키 다시 확인">'
        "<h2>🔁 키 다시 확인</h2>"
        f'<label style="display:flex;flex-direction:column;gap:4px;font-size:12.5px;font-weight:600">프로바이더<select name="provider" class="pref-select">{provider_options}</select></label>'
        '<p class="hint">저장한 키로 시험 호출을 다시 해서 상태를 갱신합니다.</p>'
        '<button type="submit" class="pref-btn">다시 확인</button>'
        '<p class="result" data-settings-result role="status" aria-live="polite"></p>'
        "</form>"
        '<form class="console-subcard" id="settings-keys-delete" data-settings-form="provider-key-delete" aria-label="키 삭제">'
        "<h2>🗑️ 키 삭제</h2>"
        f'<label style="display:flex;flex-direction:column;gap:4px;font-size:12.5px;font-weight:600">프로바이더<select name="provider" class="pref-select">{provider_options}</select></label>'
        '<p class="hint">삭제하면 서버 등급이 허용하는 범위에서만 LLM 작업을 합니다.</p>'
        '<button type="submit" class="pref-btn pref-btn-danger">삭제</button>'
        '<p class="result" data-settings-result role="status" aria-live="polite"></p>'
        "</form>"
        '<section class="console-subcard" id="settings-keys-tier" aria-label="서버 등급 허용량">'
        "<h2>📊 현재 서버 등급 허용량 (ServerTier)</h2>"
        '<p class="hint">키를 등록하지 않으면 아래 서버 등급으로 작업합니다. 자원 한도는 키를 등록해도 적용됩니다.</p>'
        f"<dl data-server-tier>{_settings_tier_rows_html(usage['tier'])}</dl>"
        f'<div class="pref-stat-row" data-usage-stats>{_settings_usage_stats_html(usage, admin_viewer=False)}</div>'
        "</section>"
    )
    return body, {"keys_mode": "byok", "has_password": has_password}


def _settings_themes_body(
    ctx: AccountsContext,
    *,
    account: accounts_store.Account,
    owned_themes: list[ThemeInfo],
) -> tuple[str, dict[str, Any]]:
    conn = open_db(ctx)
    try:
        tier = server_tier_mod.load_server_tier(conn)
    finally:
        conn.close()
    body = (
        '<section class="console-subcard" id="settings-themes-quota" aria-label="테마 슬롯">'
        f'<p class="hint" data-theme-slots>내 테마 {len(owned_themes):,}개 / 서버 등급 상한 {tier.max_themes:,}개</p>'
        "</section>"
        '<section class="console-subcard" id="settings-themes-manager-card" aria-label="내 지식 테마 관리">'
        '<div id="settings-themes-host" data-settings-panel-host="themes">'
        '<p class="hint">내 지식 테마 목록을 불러오는 중입니다...</p>'
        "</div>"
        "</section>"
    )
    return body, {"theme_slots": {"owned": len(owned_themes), "max_themes": tier.max_themes}}


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
    tbody = trs or '<tr><td colspan="5" style="color:var(--muted);text-align:center;">내 계정으로 승인한 OAuth 2.1 클라이언트가 없습니다.</td></tr>'
    body = (
        '<section class="console-subcard" id="settings-tokens-manager-card" aria-label="내 상시 이용 토큰">'
        '<div id="settings-tokens-host" data-settings-panel-host="tokens">'
        '<p class="hint">내 상시 이용 토큰 목록을 불러오는 중입니다...</p>'
        "</div>"
        "</section>"
        '<section class="console-subcard" id="settings-oauth-clients" aria-label="내가 승인한 OAuth 클라이언트">'
        "<h2>🔐 내가 승인한 OAuth 2.1 클라이언트</h2>"
        '<p class="hint">내 계정으로 <code>/oauth/authorize</code> 승인을 받아 토큰을 발급받은 원격 MCP 클라이언트입니다. 토큰 원문은 표시하지 않습니다.</p>'
        '<div class="pref-table-wrap"><table class="pref-table">'
        "<thead><tr><th>클라이언트 이름</th><th>client_id · redirect_uris</th><th>첫 발급 시각</th><th>마지막 만료 시각</th><th>토큰 수</th></tr></thead>"
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

    # S9: 싱글 모드에서는 USERNAME을 어디에도 노출하지 않는다.
    if mode == "network" and account.username:
        graph_href = f"/@{account.username}/"
        syndication_href = f"/@{account.username}/syndication"
    else:
        graph_href = "/"
        syndication_href = "/syndication"
    if validated_theme_query:
        syndication_href = _append_theme_query(syndication_href, validated_theme_query)

    scope_badge = "관리자 · 싱글 모드" if (admin_viewer and mode == "single") else (
        "관리자 · 네트워크 모드" if admin_viewer else "내 계정 설정"
    )
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
        section_body_html, built_extra = _build_admin_section_body_and_extra(ctx, spec.slug)
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
        "theme": (
            ids.format_public_id(selected_theme.number)
            if (selected_theme is not None and selected_theme.number is not None)
            else (str(selected_theme.id) if selected_theme is not None else None)
        ),
        **built_extra,
        **(extra_bootstrap or {}),
    }
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
        raw_theme = request.query_params.get("theme") if "theme" in request.query_params else None
        if raw_theme is not None:
            assert principal.account_id is not None
            if _resolve_caller_theme_and_list(ctx, account_id=principal.account_id, raw_theme=raw_theme) is None:
                return not_found()
        return _redirect("/admin/instance")

    async def settings_index(request: Request) -> Response:
        principal = request_principal(request)
        if not _is_owner(principal):
            return not_found()
        assert principal.account_id is not None
        raw_theme = request.query_params.get("theme") if "theme" in request.query_params else None
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
        raw_theme = request.query_params.get("theme") if "theme" in request.query_params else None
        return await asyncio.to_thread(
            _render_console_section,
            ctx,
            spec=spec,
            principal=principal,
            raw_theme=raw_theme,
        )

    async def settings_section_page(request: Request) -> Response:
        principal = request_principal(request)
        if not _is_owner(principal):
            return not_found()
        spec = SETTINGS_BY_PATH.get(request.url.path)
        if spec is None:
            return not_found()
        assert principal.account_id is not None
        raw_theme = request.query_params.get("theme") if "theme" in request.query_params else None
        # D3: 관리자가 /settings/themes 또는 /settings/tokens를 요청하면 ?theme= 검증 후 /admin/*으로 302 이동한다.
        if is_admin(principal) and spec.slug in {"themes", "tokens"}:
            validated_theme: str | None = None
            if raw_theme is not None:
                resolved = _resolve_caller_theme_and_list(ctx, account_id=principal.account_id, raw_theme=raw_theme)
                if resolved is None:
                    return not_found()
                validated_theme = resolved[2]
            return _redirect(_append_theme_query(f"/admin/{spec.slug}", validated_theme))
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
    ]
    for spec in ADMIN_CONSOLE_GROUP_5_SPECS:
        mounted.append(Route(spec.path, admin_section_page, methods=["GET", "HEAD"]))
    for spec in ALL_SETTINGS_SPECS:
        mounted.append(Route(spec.path, settings_section_page, methods=["GET", "HEAD"]))
    return mounted
