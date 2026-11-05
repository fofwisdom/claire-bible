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
    json_error,
    not_found,
    open_db,
    render_template,
)

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
        title="🖥️ 인스턴스 모드 전환 및 서버 등급",
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
        description="계정 상태 전환, 초대 코드 발급·비활성화 및 퇴역 USERNAME 정보 초기화를 관리합니다.",
    ),
    ConsoleSectionSpec(
        slug="idp",
        path="/admin/idp",
        area="admin",
        group_id="admin_instance",
        group_label="인스턴스 · 계정 · 인증 (관리자)",
        nav_label="🪪 외부 IdP 로그인 정책",
        title="🪪 외부 OIDC · GitHub IdP 공급자 및 가입 정책",
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
SETTINGS_BY_PATH: dict[str, ConsoleSectionSpec] = {spec.path: spec for spec in ALL_SETTINGS_SPECS}
ADMIN_CONSOLE_BY_PATH: dict[str, ConsoleSectionSpec] = {spec.path: spec for spec in ADMIN_CONSOLE_GROUP_5_SPECS}


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


def _render_console_section(
    ctx: AccountsContext,
    *,
    spec: ConsoleSectionSpec,
    principal: Principal,
    raw_theme: str | None,
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
    section_body_html = (
        f'<div class="console-placeholder" data-area="{escape(spec.area)}" data-slug="{escape(spec.slug)}">'
        f"<p class=\"console-desc\">경로: <code>{escape(spec.path)}</code></p>"
        "</div>"
    )
    bootstrap_payload = {
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
            "bootstrap_json": json.dumps(bootstrap_payload, ensure_ascii=False),
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
        since = request.query_params.get("since", "").strip() or None
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
