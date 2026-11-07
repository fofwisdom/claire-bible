"""Claire HTTP API와 graph UI를 제공하는 ASGI 애플리케이션.

Starlette는 라우팅/응답 계층만 담당하고 Uvicorn은 단일 worker로 실행한다. 외부 Reverse Proxy는 TLS와 public hostname을 담당하지만, 애플리케이션은 forwarded header를 신뢰하지 않고 설정된 public URL의 authority를 직접 검증한다.
"""

from __future__ import annotations

import asyncio
import hashlib
import html
import json
import logging
import re
import string
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import mimetypes
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode

from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from starlette.types import ASGIApp

from .. import ids
from ..accounts import provider_policy as policy
from ..accounts import server_tier
from ..config import Settings, get_settings
from ..ingest.pipeline import IngestReport
from ..ingest.report_json import report_to_dict
from ..ingest.service import IngestService, IngestServicePool
from ..secret_scrub import install_log_scrubber, scrub_exception, scrub_text
from ..store import accounts as accounts_store
from ..store import db as dbm
from ..store.public_index import SqlitePublicIndexStore
from ..store.queries import theme_summary
from ..store.legacy_hosts import SqliteLegacyHostDirectory
from ..store.theme import ThemeInfo, ThemeManager, ThemeNotFound, get_theme_manager
from ..store.token_store import get_token_store
from .accounts_routes import build_account_routes, prepare_auth_db
from .host_router import LegacyHost
from . import collections
from .namespace import SqliteNamespaceDirectory, _public_origin, namespace_link, request_namespace, theme_base_path
from .principal import request_principal
from .mcp_tools import build_mcp_app
from .oauth import (
    handle_authorization_server_metadata,
    handle_authorize,
    handle_authorize_poll,
    handle_protected_resource_metadata,
    handle_register,
    handle_telegram_push,
    handle_token,
)
from .security import (
    ErrorBoundaryMiddleware,
    WebRuntimeConfig,
    read_json_body,
    read_session_cookie,
    request_auth_scope,
    request_id,
    wrap_web_app,
)

log = logging.getLogger("claire.api")
_IMAGE_PATH_RE = re.compile(
    r"^images/[A-Za-z0-9_.-]+\.(?:jpg|jpeg|png|webp|gif)$"
)
_STATIC_ICONS_DIR = Path(__file__).resolve().parent.parent / "static" / "icons"
_ICON_FILENAME_RE = re.compile(
    r"^[A-Za-z0-9_.-]+\.(?:png|svg|ico|json|xml|webmanifest)$"
)
_STATIC_FONTS_DIR = Path(__file__).resolve().parent.parent / "static" / "fonts"
_FONT_FILENAME_RE = re.compile(r"^[A-Za-z0-9_.-]+\.woff2$")
FONTS = (
    "NotoSansKR-Regular.woff2",
    "NotoSansKR-Bold.woff2",
    "NotoSerifKR-Regular.woff2",
    "NotoSerifKR-Bold.woff2",
    "D2Coding.woff2",
    "D2CodingBold.woff2",
)
_MAX_SEARCH_QUERY_LENGTH = 2000
_MAX_SEARCH_RESULTS = 50
_MAX_ANONYMOUS_SEARCH_RESULTS = 20
_MAX_EXPENSIVE_JOBS = 4
_MAX_ANONYMOUS_SEARCH_JOBS = 4
_PROGRESS_QUEUE_SIZE = 64

# D4: 옛 /syndication/preferences?tab= → /settings/* 대응표 (설계 §2.2). 테마별 화면이라 ?theme=를 넘긴다.
_LEGACY_PREFS_SETTINGS_TABS = {
    "reader": "/settings/reader",
    "accounts": "/settings/connections",
    "feeds": "/settings/feeds",
    "tags": "/settings/tags",
    "rules": "/settings/rules",
    "outbound": "/settings/outbound",
    "opml": "/settings/opml",
    "mcp": "/settings/mcp",
    "webmcp": "/settings/webmcp",
}
# 관리자 대상 탭. 관리자가 아니면 /settings/account로 보낸다.
_LEGACY_PREFS_ACCOUNT_TABS = {
    "idp": "/admin/idp",
    "userdb": "/admin/accounts",
}

_sitemap_cache: dict[str, tuple[float, str, bytes]] = {}


def _resolve_query_func(name: str, default: Any) -> Any:
    """테스트 monkeypatch 및 하위 호환성을 위해 graphview 속성이 패치된 경우 우선 사용한다."""
    try:
        from .. import graphview

        fn = getattr(graphview, name, None)
        if fn is not None and fn is not default:
            return fn
    except ImportError:
        pass
    return default


async def _http_error(_request: Request, exc: HTTPException) -> JSONResponse:
    detail = exc.detail if isinstance(exc.detail, str) else "request failed"
    return JSONResponse(
        {"error": detail},
        status_code=exc.status_code,
        headers=exc.headers,
    )


# 정책 오류의 HTTP 상태(설계 §10.2 폴백 없음). 본문은 {"error": code, "remedy": 복구 방법}이다.
# not allowed 403, 예산·한도 429(재시도 가능, UTC 경계에 풀린다), 프로바이더 없음·키 거부 409(사용자 조치가 필요한 상태).
POLICY_ERROR_STATUS: dict[str, int] = {
    policy.ProviderNotAllowed.code: 403,
    policy.ProviderBudgetExceeded.code: 429,
    policy.ProviderNotConfigured.code: 409,
    policy.ProviderKeyRejected.code: 409,
}


def policy_error_body(exc: policy.ProviderPolicyError) -> dict[str, str]:
    """정책 오류의 응답 본문. 메시지(계정 번호·내부 사유)는 싣지 않고 코드와 복구 방법만 준다."""
    return {"error": exc.code, "remedy": scrub_text(exc.remedy or "")}


def policy_error_response(exc: policy.ProviderPolicyError) -> JSONResponse:
    return JSONResponse(policy_error_body(exc), status_code=POLICY_ERROR_STATUS.get(exc.code, 403))


async def _policy_error(_request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, policy.ProviderPolicyError)
    return policy_error_response(exc)


def _failure_result(exc: BaseException, label: str) -> dict[str, Any]:
    """스트림·작업 결과에 싣는 실패 본문. 정책 오류는 코드·복구 방법, 그 밖에는 키를 지운 메시지."""
    if isinstance(exc, policy.ProviderPolicyError):
        return {**policy_error_body(exc), "ok": False}
    return {"error": f"{label} failed: {scrub_exception(exc)}", "ok": False}


async def _json_object(request: Request) -> dict[str, Any]:
    body = await read_json_body(request)
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="json object required")
    return body


def _consume_task_result(task: asyncio.Task[Any]) -> None:
    """연결이 끊어진 뒤 끝나는 worker task의 예외를 회수한다."""

    try:
        task.exception()
    except (asyncio.CancelledError, Exception):  # noqa: BLE001
        pass


def _log_operation_failure(request: Request, operation: str, exc: Exception) -> None:
    """비밀이 섞일 수 있는 예외 문자열은 기록하지 않고 추적 정보만 남긴다."""

    log.warning(
        "%s failed request_id=%s error_type=%s",
        operation,
        request_id(request),
        type(exc).__name__,
    )


def create_app(
    settings: Settings | None = None,
    service: IngestService | Any | None = None,
) -> ASGIApp:
    """설정과 서비스를 주입할 수 있는 ASGI app factory."""

    s = settings or get_settings()
    # 프로바이더 오류 문자열에 섞인 키가 로그에 남지 않게 한다(§11.3).
    install_log_scrubber()
    # Schema migration과 WAL 설정은 요청마다 반복하지 않고 프로세스 시작 시 한 번만 한다.
    conn = dbm.connect(s.db_file)
    try:
        dbm.init_db(conn)
    finally:
        conn.close()
    # auth.db(설계 §13.3)도 요청을 받기 전에 준비하고 지난 로그인 실패 기록을 정리한다.
    prepare_auth_db(s.data_dir)

    token_store = get_token_store(data_dir=s.data_dir)
    token_store.migrate_env_tokens(
        getattr(s, "inject_token", ""),
        getattr(s, "readonly_token", ""),
        getattr(s, "collaborator_token", ""),
    )
    if not getattr(s, "inject_token", ""):
        owner_tok = token_store.ensure_default_owner_token(fallback_token=getattr(s, "inject_token", ""))
        try:
            s.inject_token = owner_tok["token"]
        except Exception:
            try:
                object.__setattr__(s, "inject_token", owner_tok["token"])
            except Exception:
                pass
    account_routes = build_account_routes(s, WebRuntimeConfig.from_settings(s))
    svc = service or IngestService(s)
    theme_mgr = ThemeManager(s)
    service_pool = IngestServicePool(s, svc, theme_manager=theme_mgr)
    namespace_directory = SqliteNamespaceDirectory(s.db_file)
    active_expensive_jobs = 0
    active_anonymous_search_jobs = 0

    def _extract_theme_ref(
        request: Request, body: dict[str, Any] | None = None
    ) -> int | str | None:
        raw = request.query_params.get("theme")
        body_theme = body.get("theme") if body else None
        hdr = request.headers.get("x-claire-theme")
        explicit: int | str | None = None
        if raw is not None and str(raw).strip():
            explicit = str(raw).strip()
        elif body_theme is not None and str(body_theme).strip():
            explicit = str(body_theme).strip()
        elif hdr is not None and hdr.strip():
            explicit = hdr.strip()

        try:
            ns = request_namespace(request)
        except Exception:
            ns = None

        if ns is not None and ns.pinned_theme_id is not None:
            if explicit is not None:
                account_id = ns.account_id if ns.account_id is not None else 1
                try:
                    resolved = theme_mgr.resolve_theme_ref(explicit, account_id=account_id)
                    if resolved.theme.id != ns.pinned_theme_id:
                        raise HTTPException(
                            status_code=400,
                            detail="explicit theme parameter contradicts pinned theme in path",
                        )
                except ThemeNotFound:
                    raise HTTPException(
                        status_code=400,
                        detail="explicit theme parameter contradicts pinned theme in path",
                    )
            return ns.pinned_theme_id

        return explicit

    def _theme_accessible(request: Request, theme: ThemeInfo) -> bool:
        try:
            ns = request_namespace(request)
        except Exception:
            ns = None

        principal = request_principal(request)
        if ns is not None and ns.mode == "network" and ns.account_id is not None:
            if principal.account_id != ns.account_id:
                # O20: Cross-account visitor can only read public themes
                if not theme.is_public:
                    return False
                is_search = request.method == "POST" and request.url.path.rstrip("/").endswith("/search")
                if request.method not in ("GET", "HEAD") and not is_search:
                    return False

        scope = request_auth_scope(request)
        if scope == "anonymous":
            if not theme.is_public:
                return False
        elif scope == "collaborator" and not (theme.is_public or theme.is_collaborator_accessible):
            return False
        return True

    def _visible_active_jobs(request: Request, data_dir: Path) -> list[dict[str, Any]]:
        """요청자가 접근할 수 있는 테마의 작업만 돌려준다 (단일 DB 이후 작업 디렉터리는 모든 테마 공용)."""
        from ..ingest.active import get_active_jobs

        jobs = get_active_jobs(data_dir)
        if not getattr(s, "multi_theme", False):
            return jobs
        visible = []
        for job in jobs:
            try:
                job_theme = theme_mgr.get_theme(int(job.get("theme_id") or 0))
            except Exception:  # noqa: BLE001
                continue
            if _theme_accessible(request, job_theme):
                visible.append(job)
        return visible

    def _job_theme_id(request: Request, theme: ThemeInfo) -> int:
        """작업 조회 대상 테마. 멀티 테마에서는 권한 검사를 거친 테마만 쓴다."""
        if getattr(s, "multi_theme", False):
            return theme.id
        raw_theme = request.query_params.get("theme")
        try:
            return int(raw_theme) if raw_theme is not None else theme.id
        except ValueError:
            return theme.id

    def _get_theme_ctx(
        request: Request, body: dict[str, Any] | None = None
    ) -> tuple[ThemeInfo, Settings, IngestService]:
        try:
            ns = request_namespace(request)
        except Exception:
            ns = None

        principal = request_principal(request)
        if ns is not None and ns.account_id is not None:
            account_id = ns.account_id
        elif principal.account_id is not None:
            account_id = principal.account_id
        else:
            account_id = 1
        ref = _extract_theme_ref(request, body)

        try:
            if ns is not None and ns.pinned_theme_id is not None:
                theme = theme_mgr.get_theme(ns.pinned_theme_id)
                via_internal = False
            else:
                resolved = theme_mgr.resolve_theme_ref(ref, account_id=account_id)
                theme = resolved.theme
                via_internal = resolved.via_internal_id
                if not getattr(s, "multi_theme", False):
                    if theme.id != 0:
                        raise ThemeNotFound(ref)

            if not _theme_accessible(request, theme):
                raise HTTPException(status_code=404, detail="theme not found")

            if via_internal:
                request.state.claire_theme_deprecated = True

            theme_settings = theme_mgr.get_settings_for_theme(theme.id, s)
            theme_svc = service_pool.get_service(theme.id)
        except ThemeNotFound as exc:
            # 없는 테마를 테마 0으로 바꾸지 않는다. 접근 불가와 같은 응답이라 비공개 테마의 존재가 드러나지 않는다.
            raise HTTPException(status_code=404, detail="theme not found") from exc
        return theme, theme_settings, theme_svc

    def _first_live_target(
        targets: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], ThemeInfo] | None:
        """문서 후보 중 테마가 아직 있는 첫 후보와 그 테마. 삭제된 테마에 남은 후보는 건너뛴다(테마 0으로 바꾸지 않는다)."""
        for target in targets:
            tid = target.get("theme_id")
            if tid is None:
                continue
            try:
                return target, theme_mgr.get_theme(tid)
            except ThemeNotFound:
                continue
        return None

    # --- 프로바이더 정책 (설계 §10.1–§10.4) ---------------------------------------------

    def _account_limits_context(account_id: int | None) -> policy.ProviderContext | None:
        """테마 소유 계정의 한도용 컨텍스트. 관리자(또는 호스트 운영자)면 None.

        한도(테마·문서·일 적재 수)는 키 상태와 관계없이 서버 등급 값으로 판정하므로 키를 복호화하지 않는다.
        계정 행이 없으면 관리자 계정 번호일 때만 관리자로 보고, 그 밖에는 fail-closed로 거부한다.
        """
        if account_id is None:
            return None
        conn = dbm.connect_existing(s.db_file, readonly=True)
        try:
            row = conn.execute("SELECT role FROM accounts WHERE id=?", (int(account_id),)).fetchone()
            if row is None:
                if int(account_id) == dbm.ADMIN_ACCOUNT_ID:
                    return None
                raise policy.ProviderNotAllowed(
                    f"account {account_id}: not found", remedy="관리자에게 문의한다."
                )
            if str(row[0]) == "admin":
                return None
            tier = server_tier.load_server_tier(conn)
        finally:
            conn.close()
        return policy.ProviderContext(
            account_id=int(account_id),
            is_admin=False,
            source="none",
            provider="none",
            model="",
            embed_model="",
            effort="none",
            credential_key="tier",
            tier=tier,
        )

    def _theme_provider_context(theme: ThemeInfo, theme_svc: Any | None = None) -> policy.ProviderContext | None:
        """테마 작업의 프로바이더 컨텍스트. 서비스가 붙인 컨텍스트(L4-a2i)를 먼저 쓰고, 없으면 테마 소유 계정으로 정한다."""
        ctx = getattr(theme_svc, "provider_context", None) if theme_svc is not None else None
        if isinstance(ctx, policy.ProviderContext):
            return ctx
        account_id = getattr(theme, "account_id", None)
        if _account_limits_context(account_id) is None:
            return None
        return policy.resolve_provider_context(account_id, settings=s)

    def _require_theme_feature_sync(theme: ThemeInfo, theme_svc: Any | None, feature: str) -> None:
        if feature in policy.ADMIN_ONLY_FEATURES:
            # 관리자 전용 기능은 키 상태를 보기 전에 거부한다(멤버 키 복호화 불필요).
            ctx = getattr(theme_svc, "provider_context", None) if theme_svc is not None else None
            if isinstance(ctx, policy.ProviderContext):
                policy.require_feature(ctx, feature)
                return
            policy.require_feature(_account_limits_context(getattr(theme, "account_id", None)), feature)
            return
        policy.require_feature(_theme_provider_context(theme, theme_svc), feature)

    async def _require_theme_feature(theme: ThemeInfo, theme_svc: Any | None, feature: str) -> None:
        """멤버 테마에서 기능이 허용되지 않으면 ``ProviderNotAllowed``(→ 403). 관리자 테마는 그대로 통과한다."""
        await asyncio.to_thread(_require_theme_feature_sync, theme, theme_svc, feature)

    def _reembed_required(theme: ThemeInfo) -> bool:
        """테마의 고정 임베딩 공간과 지금 소유 계정 컨텍스트의 임베딩이 다른가(§10.4). 판정할 수 없으면 False."""
        from types import SimpleNamespace

        from ..store.theme import embed_mismatch
        from ..store.vectors import resolve_embed_identity

        try:
            ctx = _theme_provider_context(theme)
        except policy.ProviderPolicyError:
            return False
        if ctx is None:
            pin = resolve_embed_identity(service_pool_provider(), s)
        elif ctx.provider in ("", "none"):
            return False
        else:
            pin = resolve_embed_identity(SimpleNamespace(name=ctx.provider, embed_model=ctx.embed_model), s)
        conn = dbm.connect_existing(s.db_file, readonly=True)
        try:
            row = conn.execute(
                "SELECT embed_provider, embed_model FROM themes WHERE id=?", (theme.id,)
            ).fetchone()
        except Exception:  # noqa: BLE001 - 열이 없는 레거시 DB
            row = None
        finally:
            conn.close()
        if row is None:
            return False
        fresh = SimpleNamespace(embed_provider=row[0], embed_model=row[1])
        return embed_mismatch(fresh, pin.provider, pin.model)  # type: ignore[arg-type]

    def service_pool_provider() -> Any:
        return getattr(service_pool, "shared_provider", None) or getattr(svc, "provider", None)

    def _reserve_expensive_job() -> None:
        nonlocal active_expensive_jobs
        if active_expensive_jobs >= _MAX_EXPENSIVE_JOBS:
            raise HTTPException(status_code=503, detail="server is busy")
        active_expensive_jobs += 1

    def _release_expensive_job(_task: asyncio.Task[Any] | None = None) -> None:
        nonlocal active_expensive_jobs
        active_expensive_jobs -= 1

    async def _run_expensive(func: Any, /, *args: Any, **kwargs: Any) -> Any:
        _reserve_expensive_job()
        try:
            task = asyncio.create_task(asyncio.to_thread(func, *args, **kwargs))
        except BaseException:
            _release_expensive_job()
            raise
        task.add_done_callback(_release_expensive_job)
        task.add_done_callback(_consume_task_result)
        # 요청이 끊겨도 sync worker는 중단할 수 없다. task를 shield해 실제 worker가
        # 끝날 때까지 admission slot이 유지되게 한다.
        return await asyncio.shield(task)

    def _reserve_anonymous_search_job() -> None:
        nonlocal active_anonymous_search_jobs
        if active_anonymous_search_jobs >= _MAX_ANONYMOUS_SEARCH_JOBS:
            raise HTTPException(
                status_code=429,
                detail="too many search requests",
                headers={"Retry-After": "1"},
            )
        active_anonymous_search_jobs += 1

    def _release_anonymous_search_job(
        _task: asyncio.Task[Any] | None = None,
    ) -> None:
        nonlocal active_anonymous_search_jobs
        active_anonymous_search_jobs -= 1

    async def _run_anonymous_search(
        func: Any,
        /,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        _reserve_anonymous_search_job()
        try:
            task = asyncio.create_task(asyncio.to_thread(func, *args, **kwargs))
        except BaseException:
            _release_anonymous_search_job()
            raise
        task.add_done_callback(_release_anonymous_search_job)
        task.add_done_callback(_consume_task_result)
        return await asyncio.shield(task)

    async def health(_request: Request) -> JSONResponse:
        from ..health import liveness_report

        report = await asyncio.to_thread(liveness_report, s)
        ok = bool(report.get("ok"))
        return JSONResponse({"ok": ok}, status_code=200 if ok else 503)

    async def whoami(request: Request) -> JSONResponse:
        scope = request_auth_scope(request)
        if scope not in {"owner", "collaborator", "readonly", "anonymous"}:
            raise HTTPException(status_code=401, detail="authentication required")
        # §9.1 O10: 주체 범위(호환), 인스턴스 모드, 네임스페이스와의 관계. USERNAME은 싣지 않는다(S9).
        namespace = request_namespace(request)
        principal = request_principal(request)
        if principal.account_id is None or principal.account_id != namespace.account_id:
            relation = "visitor"
        elif principal.scope == "owner":
            relation = "owner"
        else:
            relation = "delegate"
        is_admin = bool(principal.instance_role == "admin" and principal.scope == "owner" and principal.account_id is not None)
        return JSONResponse(
            {"scope": scope, "instance": {"mode": namespace.mode}, "relation": relation, "is_admin": is_admin}
        )

    async def stats(request: Request) -> JSONResponse:
        include_hidden = request_auth_scope(request) != "anonymous"
        theme, theme_settings, _ = _get_theme_ctx(request)

        def _counts() -> dict[str, Any]:
            conn = dbm.connect_existing(theme_settings.db_file, readonly=True)
            try:
                c = dict(dbm.counts(conn, include_hidden=include_hidden, theme_id=theme.id))
                c["theme_id"] = theme.id
                c["theme_label"] = theme.label
                return c
            finally:
                conn.close()

        res = await asyncio.to_thread(_counts)
        from ..ingest.active import get_primary_active_job

        jobs = _visible_active_jobs(request, theme_settings.data_dir)
        primary = get_primary_active_job(
            theme_settings.data_dir, theme_id=_job_theme_id(request, theme), jobs=jobs
        )

        res["active_jobs"] = jobs
        if primary:
            res["ingesting"] = bool(primary.get("active"))
            res["active_ingest"] = primary
        else:
            res["ingesting"] = False
            res["active_ingest"] = None
        return JSONResponse(res)

    async def active_ingest_route(request: Request) -> JSONResponse:
        theme, theme_settings, _ = _get_theme_ctx(request)
        from ..ingest.active import get_primary_active_job

        jobs = _visible_active_jobs(request, theme_settings.data_dir)
        primary = get_primary_active_job(
            theme_settings.data_dir, theme_id=_job_theme_id(request, theme), jobs=jobs
        )
        res = dict(primary) if primary else {"active": False}
        res["active_jobs"] = jobs
        return JSONResponse(res)

    async def themes_list_route(request: Request) -> Response:
        try:
            ns = request_namespace(request)
        except Exception:
            ns = None

        account_id = ns.account_id if (ns is not None and ns.account_id is not None) else 1
        origin = _public_origin(request)
        if ns is not None and ns.mode == "network" and ns.username:
            collection_url = f"{origin}/@{ns.username}/themes"
        else:
            collection_url = f"{origin}/themes"

        scope = request_auth_scope(request)
        principal = request_principal(request)
        if ns is not None and ns.mode == "network" and principal.account_id != account_id:
            visible_scope = "anonymous"
        else:
            visible_scope = scope

        if collections.wants_html(request):
            from ..graphview import render_graph_html

            return HTMLResponse(
                render_graph_html(
                    s,
                    include_private=(visible_scope in {"owner", "readonly"}),
                    collaborator=(visible_scope == "collaborator"),
                    theme=None,
                    host=request.headers.get("host"),
                    request=request,
                )
            )

        def _fetch_themes_data() -> tuple[list[ThemeInfo], list[ThemeInfo]]:
            owned = theme_mgr.list_account_themes(account_id)
            if not getattr(s, "multi_theme", False):
                owned = [t for t in owned if t.id == 0]
            if visible_scope in {"owner", "readonly"}:
                vis = owned
            elif visible_scope == "collaborator":
                vis = [t for t in owned if t.is_public or t.is_collaborator_accessible]
            else:
                vis = [t for t in owned if t.is_public]
            return owned, vis

        owned_themes, visible_themes = await asyncio.to_thread(_fetch_themes_data)
        total_items = len(visible_themes)

        query = collections.parse_collection_query(request.query_params)
        legacy_dir = SqliteLegacyHostDirectory(s.db_file)

        def _build_theme_stats(t: ThemeInfo) -> dict[str, int]:
            abs_db = theme_mgr.get_settings_for_theme(t.id, s).db_file
            t_stats = {"documents": 0, "entities": 0, "relations": 0}
            if abs_db.is_file():
                try:
                    conn = dbm.connect_existing(abs_db, readonly=True)
                    try:
                        t_stats = theme_summary(conn, include_hidden=(visible_scope != "anonymous"), theme_id=t.id)
                    finally:
                        conn.close()
                except Exception:
                    pass
            return t_stats

        if query.page:
            filtered = list(visible_themes)
            if query.max_id is not None:
                filtered = [t for t in filtered if t.number is not None and t.number < query.max_id]
            elif query.min_id is not None:
                candidates = [t for t in filtered if t.number is not None and t.number > query.min_id]
                candidates.sort(key=lambda t: t.number or 0)
                candidates = candidates[:query.limit]
                candidates.reverse()
                filtered = candidates
            elif query.since_id is not None:
                filtered = [t for t in filtered if t.number is not None and t.number > query.since_id]

            page_slice = filtered[:query.limit] if query.min_id is None else filtered

            items = []
            etag_parts = []
            for t in page_slice:
                num_str = str(t.number) if t.number is not None else str(t.id)
                t_stats = await asyncio.to_thread(_build_theme_stats, t)
                item_dict: dict[str, Any] = {
                    "number": num_str,
                    "url": f"{collection_url}/{num_str}/",
                    "label": t.label,
                    "is_default": t.is_default,
                    "is_public": t.is_public,
                    "stats": t_stats,
                }
                if visible_scope in {"owner", "readonly"}:
                    hosts = legacy_dir.list_hosts_for_theme(t.id)
                    item_dict["legacy_hosts"] = [
                        {"host": h.host, "activated_at": h.activated_at, "sunset_at": h.sunset_at}
                        for h in hosts
                    ]
                etag_part = f"{num_str}:{t.updated_at}"
                if visible_scope == "owner":
                    # §10.4 re-embed 필요 표시는 owner 요청에만 싣는다(공개·visitor 응답 제외).
                    item_dict["reembed_required"] = await asyncio.to_thread(_reembed_required, t)
                    etag_part += f":r{int(item_dict['reembed_required'])}"
                items.append(item_dict)
                etag_parts.append(etag_part)

            is_first_page = (query.max_id is None and query.min_id is None and query.since_id is None)
            page_data = collections.collection_page(
                collection_url=collection_url,
                query=query,
                items=items,
                item_number="number",
                is_first_page=is_first_page,
            )
            etag = collections.collection_etag(etag_parts)
            hdrs = collections.collection_cache_headers(anonymous=(visible_scope == "anonymous"), etag=etag)
            if request.headers.get("if-none-match") == etag:
                return Response(status_code=304, headers=hdrs)
            return JSONResponse(page_data, headers=hdrs)

        root_data = collections.collection_root(collection_url=collection_url, total_items=total_items)

        legacy_theme_list = []
        etag_parts = []
        for t in visible_themes:
            t_stats = await asyncio.to_thread(_build_theme_stats, t)
            td = t.to_dict()
            eff_sorcerer = (t.sorcerer or "").strip() or getattr(s, "effective_sorcerer", getattr(s, "sorcerer", "owner"))
            td["sorcerer"] = (t.sorcerer or "").strip()
            td["effective_sorcerer"] = eff_sorcerer
            td["knowledge_manager"] = eff_sorcerer
            td["stats"] = t_stats
            if visible_scope in {"owner", "readonly"}:
                hosts = legacy_dir.list_hosts_for_theme(t.id)
                td["legacy_hosts"] = [
                    {"host": h.host, "activated_at": h.activated_at, "sunset_at": h.sunset_at}
                    for h in hosts
                ]
            num_str = str(t.number) if t.number is not None else str(t.id)
            etag_part = f"{num_str}:{t.updated_at}"
            if visible_scope == "owner":
                # §10.4 re-embed 필요 표시는 owner 요청에만 싣는다(공개·visitor 응답 제외).
                td["reembed_required"] = await asyncio.to_thread(_reembed_required, t)
                etag_part += f":r{int(td['reembed_required'])}"
            legacy_theme_list.append(td)
            etag_parts.append(etag_part)

        root_data["themes"] = legacy_theme_list
        try:
            def_theme = theme_mgr.default_theme_for(account_id)
            root_data["default_theme_id"] = def_theme.id
            root_data["default_theme_number"] = str(def_theme.number) if def_theme.number is not None else None
        except ThemeNotFound:
            root_data["default_theme_id"] = 0
            root_data["default_theme_number"] = None
        root_data["multi_theme"] = bool(getattr(s, "multi_theme", False))

        etag = collections.collection_etag(etag_parts)
        hdrs = collections.collection_cache_headers(anonymous=(visible_scope == "anonymous"), etag=etag)
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers=hdrs)
        return JSONResponse(root_data, headers=hdrs)

    async def explore_accounts_route(request: Request, *, force_html: bool = False) -> Response:
        query = collections.parse_collection_query(request.query_params)
        principal = request_principal(request)

        def _fetch_data() -> tuple[int, list[Any]]:
            conn = dbm.connect_existing(s.db_file, readonly=True)
            try:
                store = SqlitePublicIndexStore(conn)
                total = store.count_public_accounts()
                accs = store.list_public_accounts(
                    limit=query.limit,
                    max_id=query.max_id,
                    min_id=query.min_id,
                    since_id=query.since_id,
                    include_themes=True,
                )
                return total, accs
            finally:
                conn.close()

        total_items, accounts = await asyncio.to_thread(_fetch_data)
        base_origin = _public_origin(request).rstrip("/")
        collection_url = f"{base_origin}/explore/accounts"

        account_id = principal.account_id
        if account_id is None:
            cookie_present, cookie_token = read_session_cookie(request.scope)
            if cookie_present and cookie_token:
                conn_s = dbm.connect_existing(s.db_file, readonly=True)
                try:
                    binding = accounts_store.validate_session_binding(conn_s, cookie_token)
                    if binding is not None:
                        account_id = binding.account_id
                finally:
                    conn_s.close()

        if force_html or collections.wants_html(request):
            template_path = Path(__file__).resolve().parent.parent / "templates" / "explore_accounts.html"
            template_text = template_path.read_text(encoding="utf-8")

            username = None
            if account_id is not None:
                try:
                    acc = namespace_directory.account(account_id)
                    username = acc.username
                except Exception:
                    pass

            if account_id is not None:
                home_url = f"/@{username}/" if username else "/me"
                nav_auth = (
                    f'<a href="{html.escape(home_url)}">내 작업 공간</a>'
                    f'<form method="post" action="/logout"><button type="submit" class="link">로그아웃</button></form>'
                )
            else:
                nav_auth = '<a href="/login">로그인</a>'

            if not accounts:
                if account_id is not None:
                    btn_html = f'<a class="btn" href="{html.escape(f"/@{username}/" if username else "/me")}">내 작업 공간으로 이동</a>'
                else:
                    btn_html = '<a class="btn" href="/login">로그인</a>'
                content_html = (
                    '<section class="empty-state">'
                    '<h2>등록된 공개 계정이 없습니다</h2>'
                    '<p>아직 공개된 지식 프로필이나 테마가 없습니다. 로그인하여 내 작업 공간을 확인하세요.</p>'
                    f'{btn_html}'
                    '</section>'
                )
            else:
                cards = []
                for a in accounts:
                    t_items = []
                    for t in a.themes:
                        t_url = f"/@{html.escape(a.username)}/themes/{t.number}/" if t.number else f"/@{html.escape(a.username)}/"
                        t_items.append(
                            f'<li><a href="{html.escape(t_url)}">{html.escape(t.label)}</a><span class="themes-count">({t.documents}개 문서)</span></li>'
                        )
                    if t_items:
                        themes_html = f'<ul class="themes-list">{"".join(t_items)}</ul>'
                    else:
                        themes_html = '<p class="subtitle" style="margin:.5rem 0 0;font-size:.88rem;">공개 테마가 아직 없습니다.</p>'

                    card = (
                        '<article class="account-card">'
                        '<div class="account-header">'
                        f'<a class="account-name" href="/@{html.escape(a.username)}/">{html.escape(a.display_name or a.username)}'
                        f'<span class="account-handle">@{html.escape(a.username)}</span></a>'
                        f'<span class="badge">공개 테마 {a.public_themes}개 · 공개 문서 {a.public_documents}개</span>'
                        '</div>'
                        f'{themes_html}'
                        '</article>'
                    )
                    cards.append(card)
                content_html = "".join(cards)

            pagination_parts = []
            if query.page:
                if len(accounts) == query.limit:
                    last_id = ids.format_public_id(accounts[-1].public_id)
                    pagination_parts.append(f'<a href="/explore/accounts?page=true&max_id={last_id}">다음 페이지 &rarr;</a>')
                if query.max_id is not None and accounts:
                    first_id = ids.format_public_id(accounts[0].public_id)
                    pagination_parts.insert(0, f'<a href="/explore/accounts?page=true&min_id={first_id}">&larr; 이전 페이지</a>')
            pagination_html = f'<div class="pagination">{"".join(pagination_parts)}</div>' if pagination_parts else ""

            rendered = string.Template(template_text).substitute(
                title="공개 디렉터리",
                nav_auth=nav_auth,
                content=content_html,
                pagination=pagination_html,
            )

            etag_parts = [f"{a.public_id}:{a.public_themes}:{a.public_documents}" for a in accounts]
            etag = collections.collection_etag(etag_parts if etag_parts else ["empty"])
            headers = collections.collection_cache_headers(anonymous=(account_id is None), etag=etag)
            if request.headers.get("if-none-match") == etag:
                return Response(status_code=304, headers=headers)
            if request.method == "HEAD":
                return Response(status_code=200, headers=headers, media_type="text/html; charset=utf-8")
            return HTMLResponse(rendered, headers=headers)

        # JSON response
        if not query.page:
            root_data = collections.collection_root(collection_url=collection_url, total_items=total_items)
            etag = collections.collection_etag([f"root:{total_items}"])
            headers = collections.collection_cache_headers(anonymous=(account_id is None), etag=etag)
            if request.headers.get("if-none-match") == etag:
                return Response(status_code=304, headers=headers)
            if request.method == "HEAD":
                return Response(status_code=200, headers=headers, media_type="application/json")
            return JSONResponse(root_data, headers=headers)

        items = []
        etag_parts = []
        for a in accounts:
            num_str = ids.format_public_id(a.public_id)
            item_dict: dict[str, Any] = {
                "public_id": num_str,
                "username": a.username,
                "display_name": a.display_name,
                "url": f"{base_origin}/@{a.username}/",
                "public_themes": a.public_themes,
                "public_documents": a.public_documents,
                "created_at": a.created_at,
            }
            if a.themes:
                item_dict["themes"] = [
                    {
                        "number": str(t.number) if t.number is not None else str(t.id),
                        "url": f"{base_origin}/@{a.username}/themes/{t.number}/" if t.number else f"{base_origin}/@{a.username}/",
                        "label": t.label,
                        "is_default": t.is_default,
                        "documents": t.documents,
                    }
                    for t in a.themes
                ]
            items.append(item_dict)
            etag_parts.append(f"{a.public_id}:{a.public_themes}:{a.public_documents}")

        is_first_page = (query.max_id is None and query.min_id is None and query.since_id is None)
        page_data = collections.collection_page(
            collection_url=collection_url,
            query=query,
            items=items,
            item_number="public_id",
            is_first_page=is_first_page,
        )
        etag = collections.collection_etag(etag_parts if etag_parts else ["empty"])
        headers = collections.collection_cache_headers(anonymous=principal.is_anonymous, etag=etag)
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers=headers)
        if request.method == "HEAD":
            return Response(status_code=200, headers=headers, media_type="application/json")
        return JSONResponse(page_data, headers=headers)

    async def theme_define_route(request: Request) -> JSONResponse:
        scope = request_auth_scope(request)
        if scope != "owner":
            raise HTTPException(
                status_code=403,
                detail="지식 관리자(owner) 권한이 필요합니다.",
            )
        if not getattr(s, "multi_theme", False):
            raise HTTPException(
                status_code=403,
                detail="멀티 테마 모드가 비활성화되어 있습니다 (CLAIRE_MULTI_THEME=1 필요)",
            )
        body = await _json_object(request)
        if "fqdn" in body and body["fqdn"]:
            raise HTTPException(
                status_code=400,
                detail="FQDN assignment is deprecated and no longer supported",
            )
        label = str(body.get("label") or "").strip()
        if not label:
            raise HTTPException(status_code=400, detail="label is required")
        desc = str(body.get("description") or "").strip()
        icon = str(body.get("icon") or "📁").strip()
        is_pub = bool(body.get("is_public", body.get("public", True)))
        is_collab = bool(
            body.get(
                "is_collaborator_accessible",
                body.get("collaborator_accessible", body.get("collaborator", True)),
            )
        )
        def_focus = str(
            body.get("default_focus")
            or body.get("focus")
            or ""
        ).strip()
        ga_id = str(body.get("ga_measurement_id", body.get("ga_id", "")) or "").strip()
        sorcerer = str(body.get("sorcerer", body.get("knowledge_manager", "")) or "").strip()
        pres_enabled = bool(body.get("presentation_enabled", body.get("is_presentation_enabled", False)))
        principal = request_principal(request)
        target_account_id = principal.account_id if principal.account_id is not None else 1

        def _check_theme_quota() -> None:
            lim = _account_limits_context(target_account_id)
            if lim is None:
                return
            conn = dbm.connect_existing(s.db_file, readonly=True)
            try:
                server_tier.check_theme_quota(conn, lim)
            finally:
                conn.close()

        # 멤버는 서버 등급의 max_themes를 넘겨 만들 수 없다(§10.3, 자기 키가 있어도 적용).
        await asyncio.to_thread(_check_theme_quota)
        try:
            theme = theme_mgr.define_theme(
                label,
                description=desc,
                icon=icon,
                is_public=is_pub,
                is_collaborator_accessible=is_collab,
                default_focus=def_focus,
                ga_measurement_id=ga_id,
                sorcerer=sorcerer,
                presentation_enabled=pres_enabled,
                account_id=target_account_id,
            )
            return JSONResponse({"ok": True, "theme": theme.to_dict()}, status_code=201)
        except ValueError as val_err:
            raise HTTPException(status_code=400, detail=str(val_err)) from val_err
        except Exception as exc:
            _log_operation_failure(request, "theme define", exc)
            raise HTTPException(status_code=500, detail="failed to define theme") from exc

    async def theme_update_route(request: Request) -> JSONResponse:
        scope = request_auth_scope(request)
        if scope != "owner":
            raise HTTPException(
                status_code=403,
                detail="지식 관리자(owner) 권한이 필요합니다.",
            )
        if not getattr(s, "multi_theme", False):
            raise HTTPException(
                status_code=403,
                detail="멀티 테마 모드가 비활성화되어 있습니다 (CLAIRE_MULTI_THEME=1 필요)",
            )
        body = await _json_object(request)
        if "fqdn" in body and body["fqdn"]:
            raise HTTPException(
                status_code=400,
                detail="FQDN assignment is deprecated and no longer supported",
            )
        theme_id = (
            request.query_params.get("id")
            or (str(body.get("id")) if body.get("id") is not None else None)
            or request.path_params.get("theme_id", "")
        )
        if not theme_id:
            raise HTTPException(status_code=400, detail="id is required")

        try:
            ns = request_namespace(request)
        except Exception:
            ns = None
        account_id = ns.account_id if (ns is not None and ns.account_id is not None) else 1
        try:
            resolved = theme_mgr.resolve_theme_ref(theme_id, account_id=account_id)
            target_theme = resolved.theme
        except ThemeNotFound:
            raise HTTPException(status_code=404, detail="theme not found")

        label = body.get("label")
        desc = body.get("description") if "description" in body else body.get("desc")
        icon = body.get("icon")
        is_pub = (
            body.get("is_public")
            if "is_public" in body
            else (body.get("public") if "public" in body else None)
        )
        if is_pub is not None:
            is_pub = bool(is_pub)
        is_collab = (
            body.get("is_collaborator_accessible")
            if "is_collaborator_accessible" in body
            else (
                body.get("collaborator_accessible")
                if "collaborator_accessible" in body
                else (body.get("collaborator") if "collaborator" in body else None)
            )
        )
        if is_collab is not None:
            is_collab = bool(is_collab)
        def_focus = (
            body.get("default_focus")
            if "default_focus" in body
            else (body.get("focus") if "focus" in body else None)
        )
        ga_id = (
            body.get("ga_measurement_id")
            if "ga_measurement_id" in body
            else (body.get("ga_id") if "ga_id" in body else None)
        )
        sorcerer = (
            body.get("sorcerer")
            if "sorcerer" in body
            else (body.get("knowledge_manager") if "knowledge_manager" in body else None)
        )
        pres_enabled = (
            body.get("presentation_enabled")
            if "presentation_enabled" in body
            else (body.get("is_presentation_enabled") if "is_presentation_enabled" in body else None)
        )
        if pres_enabled is not None:
            pres_enabled = bool(pres_enabled)
        try:
            theme = theme_mgr.update_theme(
                target_theme.id,
                label=label,
                description=desc,
                icon=icon,
                is_public=is_pub,
                is_collaborator_accessible=is_collab,
                default_focus=def_focus,
                ga_measurement_id=ga_id,
                sorcerer=sorcerer,
                presentation_enabled=pres_enabled,
            )
            return JSONResponse({"ok": True, "theme": theme.to_dict()})
        except KeyError as k_err:
            raise HTTPException(status_code=404, detail=str(k_err)) from k_err
        except ValueError as v_err:
            raise HTTPException(status_code=400, detail=str(v_err)) from v_err
        except Exception as exc:
            _log_operation_failure(request, "theme update", exc)
            raise HTTPException(status_code=500, detail="failed to update theme") from exc

    async def theme_delete_route(request: Request) -> JSONResponse:
        scope = request_auth_scope(request)
        if scope != "owner":
            raise HTTPException(
                status_code=403,
                detail="지식 관리자(owner) 권한이 필요합니다.",
            )
        if not getattr(s, "multi_theme", False):
            raise HTTPException(
                status_code=403,
                detail="멀티 테마 모드가 비활성화되어 있습니다 (CLAIRE_MULTI_THEME=1 필요)",
            )
        body: dict[str, Any] = {}
        if request.headers.get("content-type") == "application/json":
            try:
                raw_body = await _json_object(request)
                if isinstance(raw_body, dict):
                    body = raw_body
            except Exception:
                pass

        theme_id = (
            request.query_params.get("id")
            or (str(body.get("id")) if body.get("id") is not None else None)
            or request.path_params.get("theme_id", "")
        )
        if not theme_id:
            raise HTTPException(status_code=400, detail="id is required")

        purge = (
            request.query_params.get("purge", "0").strip().lower()
            in ("1", "true", "yes")
        )
        if not purge and isinstance(body, dict):
            purge = bool(body.get("purge", False))

        try:
            ns = request_namespace(request)
        except Exception:
            ns = None
        account_id = ns.account_id if (ns is not None and ns.account_id is not None) else 1
        try:
            resolved = theme_mgr.resolve_theme_ref(theme_id, account_id=account_id)
            target_theme = resolved.theme
        except ThemeNotFound:
            raise HTTPException(status_code=404, detail="theme not found")

        try:
            deleted = theme_mgr.delete_theme(target_theme.id, purge=purge)
            if hasattr(service_pool, "remove_service"):
                service_pool.remove_service(deleted.id)
            return JSONResponse({"ok": True, "deleted": deleted.to_dict()})
        except KeyError as k_err:
            raise HTTPException(status_code=404, detail=str(k_err)) from k_err
        except ValueError as v_err:
            raise HTTPException(status_code=400, detail=str(v_err)) from v_err
        except Exception as exc:
            _log_operation_failure(request, "theme delete", exc)
            raise HTTPException(status_code=500, detail="failed to delete theme") from exc

    async def providers_get_route(request: Request) -> JSONResponse:
        scope = request_auth_scope(request)
        if scope != "owner":
            raise HTTPException(
                status_code=403,
                detail="지식 관리자(owner) 권한이 필요합니다.",
            )
        from ..provider_manager import get_provider_manager

        pm = get_provider_manager(s.data_dir)
        cfg = pm.get_sanitized_config()
        cfg["effective_provider"] = s.effective_provider
        cfg["effective_stt_provider"] = s.effective_stt_provider
        return JSONResponse(cfg)

    async def providers_update_route(request: Request) -> JSONResponse:
        nonlocal s
        scope = request_auth_scope(request)
        if scope != "owner":
            raise HTTPException(
                status_code=403,
                detail="지식 관리자(owner) 권한이 필요합니다.",
            )
        body = await _json_object(request)
        from ..provider_manager import get_provider_manager

        pm = get_provider_manager(s.data_dir)
        try:
            updated = pm.update_config(body)
            get_settings.cache_clear()
            s = get_settings()
            if hasattr(request, "app") and hasattr(request.app, "state"):
                request.app.state.settings = s
            # 캐시된 서비스·프로바이더(관리자 공유 포함)를 버리고 새 설정으로 다시 만든다.
            service_pool.clear(s)
            updated["effective_provider"] = s.effective_provider
            updated["effective_stt_provider"] = s.effective_stt_provider
            return JSONResponse({"ok": True, "config": updated})
        except ValueError as val_err:
            raise HTTPException(status_code=400, detail=str(val_err)) from val_err
        except Exception as exc:
            _log_operation_failure(request, "provider update", exc)
            raise HTTPException(status_code=500, detail="failed to update provider configuration") from exc

    async def providers_test_route(request: Request) -> JSONResponse:
        scope = request_auth_scope(request)
        if scope != "owner":
            raise HTTPException(
                status_code=403,
                detail="지식 관리자(owner) 권한이 필요합니다.",
            )
        body = await _json_object(request)
        provider_name = str(body.get("provider") or "").strip().lower()
        if not provider_name:
            raise HTTPException(status_code=400, detail="provider is required")
        from ..provider_manager import get_provider_manager

        pm = get_provider_manager(s.data_dir)
        override = body.get("config") if isinstance(body.get("config"), dict) else None
        res = pm.test_connection(provider_name, config=override)
        return JSONResponse(res)

    async def limits_get_route(request: Request) -> JSONResponse:
        scope = request_auth_scope(request)
        if scope != "owner":
            raise HTTPException(
                status_code=403,
                detail="지식 관리자(owner) 권한이 필요합니다.",
            )
        from ..ingest_limit_manager import get_ingest_limit_manager

        curr_settings = (
            getattr(getattr(request, "app", None), "state", None)
            and getattr(request.app.state, "settings", s)
        ) or s
        data_dir = getattr(curr_settings, "data_dir", s.data_dir)
        ilm = get_ingest_limit_manager(data_dir)
        cfg = ilm.get_sanitized_config()
        return JSONResponse(cfg)

    async def limits_update_route(request: Request) -> JSONResponse:
        nonlocal s
        scope = request_auth_scope(request)
        if scope != "owner":
            raise HTTPException(
                status_code=403,
                detail="지식 관리자(owner) 권한이 필요합니다.",
            )
        body = await _json_object(request)
        from ..ingest_limit_manager import get_ingest_limit_manager

        curr_settings = (
            getattr(getattr(request, "app", None), "state", None)
            and getattr(request.app.state, "settings", s)
        ) or s
        data_dir = getattr(curr_settings, "data_dir", s.data_dir)
        ilm = get_ingest_limit_manager(data_dir)
        try:
            updated = ilm.update_config(body)
            get_settings.cache_clear()
            s = s.model_copy(update=ilm.get_settings_dict())
            if hasattr(request, "app") and hasattr(request.app, "state"):
                request.app.state.settings = s
            # 캐시된 서비스·프로바이더(관리자 공유 포함)를 버리고 새 설정으로 다시 만든다.
            service_pool.clear(s)
            return JSONResponse({"ok": True, "config": updated})
        except ValueError as val_err:
            raise HTTPException(status_code=400, detail=str(val_err)) from val_err
        except Exception as exc:
            _log_operation_failure(request, "ingest limits update", exc)
            raise HTTPException(status_code=500, detail="failed to update ingest limits configuration") from exc

    async def tokens_list_route(request: Request) -> JSONResponse:
        scope = request_auth_scope(request)
        if scope != "owner":
            raise HTTPException(
                status_code=403,
                detail="지식 관리자(owner) 권한이 필요합니다.",
            )
        principal = request_principal(request)
        target_account_id = principal.account_id if principal.account_id is not None else 1
        store = get_token_store(data_dir=s.data_dir)
        tokens = store.list_api_tokens(include_revoked=True, account_id=target_account_id)
        sanitized = []
        for t in tokens:
            raw_val = t["token"]
            prefix = raw_val[:8] if len(raw_val) >= 8 else raw_val[:4]
            suffix = raw_val[-4:] if len(raw_val) >= 8 else ""
            masked = f"{prefix}...{suffix}" if suffix else f"{prefix}..."
            item = dict(t)
            item["token_masked"] = masked
            del item["token"]
            sanitized.append(item)
        return JSONResponse({"tokens": sanitized})

    async def tokens_create_route(request: Request) -> JSONResponse:
        scope = request_auth_scope(request)
        if scope != "owner":
            raise HTTPException(
                status_code=403,
                detail="지식 관리자(owner) 권한이 필요합니다.",
            )
        principal = request_principal(request)
        target_account_id = principal.account_id if principal.account_id is not None else 1
        body = await _json_object(request)
        name = str(body.get("name") or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="토큰 이름(name)은 필수입니다.")
        tok_scope = str(body.get("scope") or "readonly").strip().lower()
        expires_in = body.get("expires_in_days")
        try:
            expires_in_days = int(expires_in) if expires_in is not None and str(expires_in).strip() else None
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail="만료 기간(expires_in_days)은 정수여야 합니다.")

        store = get_token_store(data_dir=s.data_dir)
        try:
            created = store.create_api_token(name=name, scope=tok_scope, expires_in_days=expires_in_days, account_id=target_account_id)
            return JSONResponse({
                "ok": True,
                "token": created,
                "message": "API/MCP 토큰이 성공적으로 생성되었습니다.",
            }, status_code=201)
        except ValueError as v_err:
            raise HTTPException(status_code=400, detail=str(v_err)) from v_err
        except Exception as exc:
            _log_operation_failure(request, "token create", exc)
            raise HTTPException(status_code=500, detail="토큰 생성 중 오류가 발생했습니다.") from exc

    async def tokens_revoke_route(request: Request) -> JSONResponse:
        scope = request_auth_scope(request)
        if scope != "owner":
            raise HTTPException(
                status_code=403,
                detail="지식 관리자(owner) 권한이 필요합니다.",
            )
        principal = request_principal(request)
        target_account_id = principal.account_id if principal.account_id is not None else 1
        body = await _json_object(request) if request.method in {"POST", "DELETE"} else {}
        token_id = body.get("token_id") or body.get("id") or request.query_params.get("id") or request.query_params.get("token_id")
        if not token_id:
            raise HTTPException(status_code=400, detail="폐기할 토큰 식별자(id)가 필요합니다.")

        store = get_token_store(data_dir=s.data_dir)
        try:
            revoked = store.revoke_api_token(token_id, account_id=target_account_id)
            if not revoked:
                raise HTTPException(status_code=404, detail="토큰을 찾을 수 없거나 이미 폐기되었습니다.")
            return JSONResponse({"ok": True, "message": "토큰이 성공적으로 폐기되었습니다."})
        except HTTPException:
            raise
        except Exception as exc:
            _log_operation_failure(request, "token revoke", exc)
            raise HTTPException(status_code=500, detail="토큰 폐기 중 오류가 발생했습니다.") from exc

    async def do_ingest(request: Request) -> JSONResponse:
        body = await _json_object(request)
        theme, _, theme_svc = _get_theme_ctx(request, body)
        scope = request_auth_scope(request)
        if scope == "collaborator":
            if theme.is_default or theme.id == 0:
                raise HTTPException(
                    status_code=403,
                    detail="Collaborator는 기본 지식베이스에 적재할 수 없습니다. 추가 지식 테마를 선택하세요.",
                )
            if not theme.is_collaborator_accessible:
                raise HTTPException(
                    status_code=403,
                    detail="Collaborator에게 공개되지 않은 테마입니다.",
                )
        payload = str(body.get("payload") or "").strip()
        if not payload:
            raise HTTPException(status_code=400, detail="payload required")
        expand_max = body.get("expand_max")
        format_arg = str(body.get("format") or "").strip() or None
        focus = str(body.get("focus") or "").strip() or None
        if not focus:
            from ..telegram_bot import parse_message_focus

            payload, parsed_focus = parse_message_focus(payload)
            if parsed_focus:
                focus = parsed_focus
            elif not theme.is_default and theme.id > 0 and getattr(theme, "default_focus", None):
                focus = theme.default_focus.strip() or None

        full_content = bool(body.get("full_content") or body.get("no_truncate") or False)
        effort = str(body.get("effort") or "").strip() or None

        ingest_kwargs: dict[str, Any] = {
            "source": "api",
            "expand_max": expand_max,
        }
        if full_content:
            ingest_kwargs["full_content"] = True
        if effort is not None:
            ingest_kwargs["effort"] = effort
        if format_arg is not None:
            ingest_kwargs["format"] = format_arg
        if focus is not None:
            ingest_kwargs["focus"] = focus
        try:
            report = await _run_expensive(
                theme_svc.ingest,
                payload,
                **ingest_kwargs,
            )
        except (HTTPException, policy.ProviderPolicyError):
            raise
        except Exception as exc:  # noqa: BLE001
            # 원본은 raw_inbox에 남아 replay-failed로 복구할 수 있다.
            _log_operation_failure(request, "ingest", exc)
            return JSONResponse(
                {"error": "ingest failed", "ok": False},
                status_code=500,
            )
        rep_dict = report_to_dict(report)
        rep_dict["theme_id"] = theme.id
        rep_dict["theme_label"] = theme.label
        return JSONResponse(rep_dict)

    async def do_reingest(request: Request) -> JSONResponse:
        body = await _json_object(request)
        theme, theme_settings, theme_svc = _get_theme_ctx(request, body)
        scope = request_auth_scope(request)
        if scope == "collaborator":
            if theme.is_default or theme.id == 0:
                raise HTTPException(
                    status_code=403,
                    detail="Collaborator는 기본 지식베이스에 재적재할 수 없습니다. 추가 지식 테마를 선택하세요.",
                )
            if not theme.is_collaborator_accessible:
                raise HTTPException(
                    status_code=403,
                    detail="Collaborator에게 공개되지 않은 테마입니다.",
                )
        target = str(body.get("target") or body.get("payload") or body.get("id") or body.get("doc_id") or "").strip()
        if not target:
            raise HTTPException(status_code=400, detail="target or payload required")
        expand_max = body.get("expand_max")
        format_arg = str(body.get("format") or "").strip() or None
        focus = str(body.get("focus") or "").strip() or None
        if not focus and not theme.is_default and theme.id > 0 and getattr(theme, "default_focus", None):
            focus = theme.default_focus.strip() or None

        full_content = bool(body.get("full_content") or body.get("no_truncate") or False)
        effort = str(body.get("effort") or "").strip() or None
        untombstone = bool(body.get("untombstone", False))

        if untombstone:
            def _lift():
                conn = dbm.connect_existing(theme_settings.db_file)
                try:
                    return dbm.remove_tombstones(conn, target=target, theme_id=theme.id)
                finally:
                    conn.close()
            await asyncio.to_thread(_lift)

        def _run() -> Any:
            conn = dbm.connect_existing(theme_settings.db_file)
            try:
                matched = dbm.resolve_document_targets(conn, target=target, theme_id=theme.id)
            finally:
                conn.close()

            if matched:
                t_doc = matched[0]
                res = theme_svc.regenerate_components(
                    doc_id=t_doc["id"],
                    refetch=not full_content,
                    refetch_full=full_content,
                    all_components=True,
                    force=True,
                    effort=effort,
                    format=format_arg,
                    focus=focus,
                )
                t_info = res.get("targets", [{}])[0] if res.get("targets") else {}
                return {
                    "document_id": t_doc["id"],
                    "title": t_info.get("title") or t_doc.get("title") or target,
                    "updated": True,
                    "duplicate": False,
                    "summary": t_info.get("new_summary") or "",
                    "entities_created": t_info.get("entities_created", 0),
                    "entities_linked": t_info.get("entities_linked", 0),
                    "relations_added": t_info.get("relations_added", 0),
                    "full_content": full_content,
                    "effort": effort,
                    "focus": focus,
                    "theme_id": theme.id,
                    "theme_label": theme.label,
                }
            else:
                ingest_kwargs: dict[str, Any] = {
                    "source": "api-reingest",
                    "expand_max": expand_max,
                }
                if full_content:
                    ingest_kwargs["full_content"] = True
                if effort is not None:
                    ingest_kwargs["effort"] = effort
                if format_arg is not None:
                    ingest_kwargs["format"] = format_arg
                if focus is not None:
                    ingest_kwargs["focus"] = focus
                rep = theme_svc.ingest(target, **ingest_kwargs)
                rep_dict = report_to_dict(rep)
                rep_dict["theme_id"] = theme.id
                rep_dict["theme_label"] = theme.label
                return rep_dict

        try:
            result = await _run_expensive(_run)
            return JSONResponse(result)
        except (HTTPException, policy.ProviderPolicyError):
            raise
        except Exception as exc:  # noqa: BLE001
            _log_operation_failure(request, "reingest", exc)
            return JSONResponse(_failure_result(exc, "reingest"), status_code=500)

    async def do_regenerate(request: Request) -> JSONResponse:
        body = await _json_object(request)
        theme, theme_settings, theme_svc = _get_theme_ctx(request, body)
        scope = request_auth_scope(request)
        target = str(body.get("target") or body.get("payload") or body.get("id") or body.get("doc_id") or "").strip()
        doc_id = str(body.get("doc_id") or "").strip() or None
        token = str(body.get("token") or "").strip() or None
        if not target and not doc_id and not token:
            raise HTTPException(status_code=400, detail="target, doc_id, or token required")

        theme_ref = _extract_theme_ref(request, body)
        if theme_ref is None and getattr(s, "multi_theme", False) and theme_mgr is not None:
            targets = await asyncio.to_thread(
                theme_mgr.resolve_document_targets,
                target=target or None,
                doc_id=doc_id,
                token=token,
            )
            live = _first_live_target(targets)
            if live is not None:
                cand_theme = live[1]
                if cand_theme.id != theme.id:
                    try:
                        if scope != "collaborator" or cand_theme.is_collaborator_accessible or cand_theme.is_public:
                            theme = cand_theme
                            theme_settings = theme_mgr.get_settings_for_theme(theme.id, s)
                            theme_svc = service_pool.get_service(theme.id)
                    except Exception:
                        pass

        if scope == "collaborator":
            if theme.is_default or theme.id == 0:
                raise HTTPException(
                    status_code=403,
                    detail="Collaborator는 기본 지식베이스를 직접 재생성할 수 없습니다. 추가 지식 테마를 선택하세요.",
                )
            if not theme.is_collaborator_accessible:
                raise HTTPException(
                    status_code=403,
                    detail="Collaborator에게 공개되지 않은 테마입니다.",
                )

        component = str(body.get("component") or "").strip().lower()
        all_comp = False
        summary = False
        detail = False
        graph = False
        if component in ("all", ""):
            all_comp = True
        elif component in ("detail", "format"):
            detail = True
        elif component == "summary":
            summary = True
        elif component == "graph":
            graph = True

        if body.get("all") or body.get("all_components"):
            all_comp = True
            summary = detail = graph = False
        elif body.get("detail"):
            detail = True
            all_comp = summary = graph = False
        elif body.get("summary"):
            summary = True
            all_comp = detail = graph = False
        elif body.get("graph"):
            graph = True
            all_comp = summary = detail = False

        dry_run = bool(body.get("dry_run", False))
        force = not dry_run
        refetch = bool(body.get("refetch", False))
        refetch_full = bool(body.get("refetch_full", False))
        effort = str(body.get("effort") or "").strip() or None
        format_arg = str(body.get("format") or "").strip() or None
        focus = str(body.get("focus") or "").strip() or None
        if not focus and not theme.is_default and theme.id > 0 and getattr(theme, "default_focus", None):
            focus = theme.default_focus.strip() or None

        def _run() -> Any:
            res = theme_svc.regenerate_components(
                target=target or None,
                doc_id=doc_id,
                token=token,
                summary=summary,
                detail=detail,
                graph=graph,
                all_components=all_comp,
                refetch=refetch,
                refetch_full=refetch_full,
                force=force,
                effort=effort,
                format=format_arg,
                focus=focus,
            )
            if isinstance(res, dict) and theme.id != 0:
                res["theme_id"] = theme.id
                res["theme_label"] = theme.label
            return res

        try:
            result = await _run_expensive(_run)
            return JSONResponse(result)
        except (HTTPException, policy.ProviderPolicyError):
            raise
        except Exception as exc:  # noqa: BLE001
            _log_operation_failure(request, "regenerate", exc)
            return JSONResponse(_failure_result(exc, "regenerate"), status_code=500)

    async def do_search(request: Request) -> JSONResponse:
        body = await _json_object(request)
        theme, _, theme_svc = _get_theme_ctx(request, body)
        query = str(body.get("query") or "").strip()
        if not query:
            raise HTTPException(status_code=400, detail="query required")
        if len(query) > _MAX_SEARCH_QUERY_LENGTH:
            raise HTTPException(status_code=400, detail="query is too long")
        try:
            limit = int(body.get("limit", 8))
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="limit must be an integer") from exc
        scope = request_auth_scope(request)
        principal = request_principal(request)
        is_cross_account = (
            principal.account_id is not None
            and getattr(theme, "account_id", None) is not None
            and principal.account_id != theme.account_id
        )
        include_hidden = (scope != "anonymous") and not is_cross_account
        requested_mode = str(body.get("mode") or "").strip().lower()
        if scope == "anonymous" or is_cross_account:
            mode = "fts"
            limit = max(1, min(_MAX_ANONYMOUS_SEARCH_RESULTS, limit))
            summarize = False
            runner = _run_anonymous_search
        elif scope in {"owner", "collaborator", "readonly"}:
            if requested_mode == "fts":
                mode = "fts"
                limit = max(1, min(_MAX_SEARCH_RESULTS, limit))
                summarize = False
                runner = _run_anonymous_search
            elif requested_mode in {"", "hybrid", "semantic"}:
                mode = "hybrid"
                limit = max(1, min(_MAX_SEARCH_RESULTS, limit))
                summarize = scope == "owner" and bool(body.get("summarize", True))
                runner = _run_expensive
            else:
                raise HTTPException(
                    status_code=400,
                    detail=f"unsupported search mode: {requested_mode}",
                )
        else:
            raise HTTPException(status_code=401, detail="authentication required")

        search_kwargs: dict[str, Any] = {
            "limit": limit,
            "summarize": summarize,
            "mode": mode,
        }
        result = await runner(
            theme_svc.search,
            query,
            include_hidden=include_hidden,
            **search_kwargs,
        )
        return JSONResponse(
            {
                "query": result.query,
                "mode": mode,
                "answer": result.answer,
                "theme_id": theme.id,
                "theme_label": theme.label,
                "hits": [
                    {
                        "id": hit.entity.id,
                        "type": hit.entity.type,
                        "name": hit.entity.name,
                        "via": hit.via,
                        "score": hit.score,
                    }
                    for hit in result.hits
                ],
            }
        )

    async def graph_data(request: Request) -> JSONResponse:
        from ..store.queries import graph_json

        _graph_json = _resolve_query_func("graph_json", graph_json)
        include_hidden = request_auth_scope(request) != "anonymous"
        theme, theme_settings, _ = _get_theme_ctx(request)

        def _graph() -> dict[str, Any]:
            conn = dbm.connect_existing(theme_settings.db_file, readonly=True)
            try:
                g = _graph_json(conn, include_hidden=include_hidden, theme_id=theme.id)
                g["theme_id"] = theme.id
                g["theme_label"] = theme.label
                return g
            finally:
                conn.close()

        return JSONResponse(await asyncio.to_thread(_graph))

    async def graph_ui(request: Request) -> Response:
        from ..graphview import render_graph_html

        setup_redirect = await account_routes.setup_redirect(request)
        if setup_redirect is not None:
            return setup_redirect
        try:
            ns = request_namespace(request)
        except Exception:
            ns = None

        if ns is not None and ns.mode == "network" and not ns.via_prefix:
            params = request.query_params
            if any(k in params for k in ("doc", "node", "theme")):
                admin_username = None
                try:
                    admin = namespace_directory.admin()
                    admin_username = admin.username
                except Exception:
                    pass
                if admin_username:
                    qs = str(request.url.query)
                    target = f"/@{admin_username}/" + (f"?{qs}" if qs else "")
                    return RedirectResponse(target, status_code=308)
                return PlainTextResponse("Not Found", status_code=404)
            return await explore_accounts_route(request, force_html=True)

        scope = request_auth_scope(request)
        principal = request_principal(request)
        account_id = ns.account_id if (ns is not None and ns.account_id is not None) else 1
        if ns is not None and ns.mode == "network" and principal.account_id != account_id:
            visible_scope = "anonymous"
        else:
            visible_scope = scope
        include_private = visible_scope in {"owner", "readonly"}
        is_collaborator = visible_scope == "collaborator"
        theme = None

        if getattr(s, "multi_theme", False) or (ns is not None and ns.pinned_theme_id is not None):
            try:
                theme, _, _ = _get_theme_ctx(request)
            except HTTPException:
                raise
            except Exception:
                pass
        return HTMLResponse(
            render_graph_html(
                s,
                include_private=include_private,
                collaborator=is_collaborator,
                theme=theme,
                host=request.headers.get("host"),
                request=request,
            )
        )

    def _resolve_syndication_ui_context(request: Request) -> tuple[str, bool, bool, ThemeInfo | None]:
        try:
            ns = request_namespace(request)
        except Exception:
            ns = None
        principal = request_principal(request)
        if ns is not None and ns.mode == "network" and principal.account_id is None:
            raise HTTPException(status_code=404, detail="not found")
        scope = request_auth_scope(request)
        include_private = scope in {"owner", "readonly"}
        is_collaborator = scope == "collaborator"
        theme = None
        if getattr(s, "multi_theme", False):
            try:
                theme, _, _ = _get_theme_ctx(request)
                if scope in {"public", "anonymous"} and theme is not None and not theme.is_public:
                    theme = None
            except HTTPException:
                # 명시한 테마가 없거나 접근할 수 없으면 404다 (첫 테마로 바꿔 보여 주지 않는다)
                if _extract_theme_ref(request) is not None:
                    raise
                theme = None
            except Exception:
                theme = None
        return scope, include_private, is_collaborator, theme

    async def syndication_ui_route(request: Request) -> HTMLResponse:
        from ..graphview import render_syndication_html

        scope, include_private, is_collaborator, theme = _resolve_syndication_ui_context(request)
        return HTMLResponse(
            render_syndication_html(
                s,
                include_private=include_private,
                collaborator=is_collaborator,
                theme=theme,
                host=request.headers.get("host"),
                auth_scope=scope,
                request=request,
            )
        )

    async def prototype_syndication_ui_route(request: Request) -> HTMLResponse:
        from ..graphview import render_prototype_syndication_html

        scope, include_private, is_collaborator, theme = _resolve_syndication_ui_context(request)
        return HTMLResponse(
            render_prototype_syndication_html(
                s,
                include_private=include_private,
                collaborator=is_collaborator,
                theme=theme,
                host=request.headers.get("host"),
                auth_scope=scope,
                request=request,
            )
        )

    async def syndication_preferences_ui_route(request: Request) -> Response:
        """D4 호환 302: 옛 ``/syndication/preferences?tab=``를 ``/settings/*``(또는 관리자의 ``/admin/*``)로 보낸다 (설계 §2.2).

        ``?theme=``를 먼저 엄격히 검증한다(없거나 접근할 수 없으면 404, C1). 모르는 ``?tab=``도 404다. ``/settings/*``는 루트 경로라 위치에 ``/@USERNAME``을 붙이지 않는다(D1, S9).
        """
        theme_query: str | None = None
        if _extract_theme_ref(request) is not None:
            theme, _, _ = _get_theme_ctx(request)
            theme_query = ids.format_public_id(theme.number) if theme.number is not None else str(theme.id)
        tab = request.query_params.get("tab") if "tab" in request.query_params else None
        principal = request_principal(request)
        caller_is_admin = principal.instance_role == "admin" and principal.scope == "owner" and principal.account_id is not None
        if tab is None:
            target = "/settings/reader"
        elif tab in _LEGACY_PREFS_SETTINGS_TABS:
            target = _LEGACY_PREFS_SETTINGS_TABS[tab]
        elif tab in _LEGACY_PREFS_ACCOUNT_TABS:
            # 관리자 화면은 테마와 무관하다. ?theme=를 넘기지 않는다.
            target = _LEGACY_PREFS_ACCOUNT_TABS[tab] if caller_is_admin else "/settings/account"
            theme_query = None
        else:
            raise HTTPException(status_code=404, detail="unknown preferences tab")
        location = f"{target}?{urlencode({'theme': theme_query})}" if theme_query else target
        return Response(
            status_code=302,
            headers={"Location": location, "Cache-Control": "no-store", "Referrer-Policy": "same-origin"},
        )

    async def syndication_dispatch_route(request: Request) -> JSONResponse:
        scope = request_auth_scope(request)
        if scope in {"anonymous", "public"}:
            raise HTTPException(status_code=403, detail="권한이 없습니다.")

        body = await _json_object(request)
        document_id = str(body.get("document_id") or body.get("doc_id") or "").strip()
        if not document_id:
            raise HTTPException(status_code=400, detail="document_id is required")

        target_theme_ref = (
            body.get("target_theme_id")
            if body.get("target_theme_id") is not None
            else body.get("target_theme")
        )
        if target_theme_ref is None:
            raise HTTPException(status_code=400, detail="target_theme_id is required")

        if not getattr(s, "multi_theme", False) and theme_mgr is None:
            try:
                target_tid = int(target_theme_ref)
            except (ValueError, TypeError):
                raise HTTPException(status_code=400, detail="invalid target_theme_id")
            if target_tid != 0:
                raise HTTPException(status_code=400, detail="multi_theme is not enabled")
            target_theme = ThemeInfo(
                id=0,
                seq=0,
                label="기본 지식베이스",
                description="일반 수집 자료 및 기본 지식",
                icon="📚",
                db_path=str(getattr(s, "db_path", "data/claire.db")),
                is_default=True,
                is_public=True,
            )
        else:
            try:
                target_theme = theme_mgr.get_theme(target_theme_ref)
            except Exception:
                raise HTTPException(status_code=404, detail="target theme not found")

        if not _theme_accessible(request, target_theme):
            raise HTTPException(status_code=404, detail="target theme not found")

        principal = request_principal(request)
        is_network = False
        try:
            ns = request_namespace(request)
            if ns is not None and ns.mode == "network":
                is_network = True
        except Exception:
            pass
        if getattr(s, "instance_mode", None) == "network":
            is_network = True

        caller_account_id = principal.account_id if (principal and principal.account_id is not None) else (1 if not is_network else None)
        target_account_id = getattr(target_theme, "account_id", None)

        if is_network or target_account_id is not None:
            if target_account_id is not None and caller_account_id != target_account_id:
                if getattr(principal, "instance_role", None) != "admin":
                    raise HTTPException(
                        status_code=403,
                        detail="다른 계정의 테마에 적재할 권한이 없습니다.",
                    )

        if scope == "collaborator":
            if target_theme.is_default or target_theme.id == 0:
                raise HTTPException(
                    status_code=403,
                    detail="Collaborator는 기본 지식베이스에 적재할 수 없습니다.",
                )
            if not target_theme.is_collaborator_accessible:
                raise HTTPException(
                    status_code=403,
                    detail="Collaborator에게 공개되지 않은 대상 테마입니다.",
                )

        source_theme_ref = (
            body.get("source_theme_id")
            if body.get("source_theme_id") is not None
            else body.get("source_theme")
        )
        if source_theme_ref is not None:
            if not getattr(s, "multi_theme", False) and theme_mgr is None:
                src_theme = target_theme
            else:
                try:
                    src_theme = theme_mgr.get_theme(source_theme_ref)
                except Exception:
                    raise HTTPException(status_code=404, detail="source theme not found")
        else:
            try:
                src_theme, _, _ = _get_theme_ctx(request, body)
            except HTTPException:
                # 요청에 테마를 명시했는데 없거나 접근할 수 없으면 404다 (대상 테마로 바꾸지 않는다)
                if _extract_theme_ref(request, body) is not None:
                    raise
                src_theme = target_theme
            except Exception:
                src_theme = target_theme

        if not _theme_accessible(request, src_theme):
            raise HTTPException(status_code=404, detail="source theme not found")

        conn = dbm.connect_existing(s.db_file)
        try:
            src_doc = dbm.get_document(conn, document_id, theme_id=src_theme.id)
            if src_doc is None and theme_mgr is not None:
                targets = theme_mgr.resolve_document_targets(doc_id=document_id)
                live = _first_live_target(targets)
                if live is not None:
                    src_theme = live[1]
                    if not _theme_accessible(request, src_theme):
                        raise HTTPException(status_code=404, detail="source theme not found")
                    src_doc = dbm.get_document(conn, document_id, theme_id=src_theme.id)
        finally:
            conn.close()

        if src_doc is None:
            raise HTTPException(status_code=404, detail=f"document {document_id} not found")

        if scope == "collaborator" and not (src_theme.is_public or src_theme.is_collaborator_accessible):
            raise HTTPException(status_code=403, detail="접근 권한이 없는 소스 테마입니다.")

        applied_focus = (
            str(body.get("focus") or body.get("applied_focus") or "").strip() or None
        )
        if not applied_focus and target_theme.id > 0 and getattr(target_theme, "default_focus", None):
            applied_focus = target_theme.default_focus.strip() or None

        from ..ontology.base import Document, new_id

        target_doc_id = new_id("doc")
        target_doc = Document(
            id=target_doc_id,
            url=src_doc.url,
            canonical_url=src_doc.canonical_url,
            title=src_doc.title,
            author=src_doc.author,
            published_at=src_doc.published_at,
            raw_text=src_doc.raw_text,
            source_type=src_doc.source_type,
            content_hash=src_doc.content_hash,
            lang=src_doc.lang,
            partial=src_doc.partial,
            meta=dict(src_doc.meta or {}),
        )
        target_doc.meta["syndication"] = {
            "source_theme_id": src_theme.id,
            "source_document_id": src_doc.id,
        }
        if applied_focus:
            target_doc.meta["focus"] = applied_focus

        target_svc = service_pool.get_service(target_theme.id)
        payload = src_doc.url or src_doc.raw_text or src_doc.title or "cross_theme_dispatch"
        try:
            report = await _run_expensive(
                target_svc.ingest,
                payload,
                prefetched=target_doc,
                source=f"syndication:{src_theme.id}",
                focus=applied_focus,
            )
        except (HTTPException, policy.ProviderPolicyError):
            raise
        except Exception as exc:  # noqa: BLE001
            _log_operation_failure(request, "syndication_dispatch", exc)
            return JSONResponse(
                _failure_result(exc, "dispatch"),
                status_code=500,
            )

        status = "error" if getattr(report, "error", None) else "done"
        remote_ref = getattr(report, "document_id", None) or target_doc_id
        error_msg = getattr(report, "error", None)

        dispatched_by_account_id = (
            principal.account_id
            if (principal and principal.account_id is not None)
            else (dbm.ADMIN_ACCOUNT_ID if not is_network else None)
        )

        def _record_dispatch() -> str:
            c = dbm.connect_existing(s.db_file)
            try:
                return dbm.create_syndication_dispatch(
                    c,
                    theme_id=src_theme.id,
                    target_theme_id=target_theme.id,
                    document_id=src_doc.id,
                    mode="cross_theme",
                    status=status,
                    remote_ref=remote_ref,
                    payload=payload,
                    error=error_msg,
                    applied_focus=applied_focus,
                    dispatched_by_account_id=dispatched_by_account_id,
                )
            finally:
                c.close()

        dispatch_id = await asyncio.to_thread(_record_dispatch)
        return JSONResponse(
            {
                "ok": status == "done",
                "dispatch_id": dispatch_id,
                "status": status,
                "source_theme_id": src_theme.id,
                "target_theme_id": target_theme.id,
                "document_id": src_doc.id,
                "target_document_id": remote_ref,
                "applied_focus": applied_focus,
                "dispatched_by_account_id": dispatched_by_account_id,
                "error": error_msg,
            }
        )

    async def syndication_dispatches_route(request: Request) -> JSONResponse:
        scope = request_auth_scope(request)
        if scope in {"anonymous", "public"}:
            raise HTTPException(status_code=403, detail="권한이 없습니다.")

        theme_id_param = request.query_params.get("theme_id")
        target_theme_id_param = request.query_params.get("target_theme_id")
        document_id = (
            request.query_params.get("document_id")
            or request.query_params.get("doc_id")
        )
        status = request.query_params.get("status")
        mode = request.query_params.get("mode")
        try:
            limit = min(int(request.query_params.get("limit", "50")), 200)
        except (ValueError, TypeError):
            limit = 50
        try:
            offset = max(int(request.query_params.get("offset", "0")), 0)
        except (ValueError, TypeError):
            offset = 0

        theme_id = (
            int(theme_id_param)
            if theme_id_param is not None and theme_id_param != ""
            else None
        )
        target_theme_id = (
            int(target_theme_id_param)
            if target_theme_id_param is not None and target_theme_id_param != ""
            else None
        )
        account_id_param = request.query_params.get("account_id") or request.query_params.get("dispatched_by_account_id")
        dispatched_by_account_id = (
            int(account_id_param)
            if account_id_param is not None and account_id_param != ""
            else None
        )

        def _query() -> list[dict[str, Any]]:
            c = dbm.connect_existing(s.db_file)
            try:
                return dbm.list_syndication_dispatches(
                    c,
                    theme_id=theme_id,
                    target_theme_id=target_theme_id,
                    document_id=document_id,
                    status=status,
                    mode=mode,
                    dispatched_by_account_id=dispatched_by_account_id,
                    limit=limit,
                    offset=offset,
                )
            finally:
                c.close()

        dispatches = await asyncio.to_thread(_query)
        return JSONResponse(
            {
                "ok": True,
                "dispatches": dispatches,
                "count": len(dispatches),
            }
        )

    async def robots_txt_route(request: Request) -> Response:
        host_header = request.headers.get("host") or ""
        req_host = host_header.split(":", 1)[0].strip().lower()

        theme: ThemeInfo | None = None
        if getattr(s, "multi_theme", False) and theme_mgr is not None and req_host:
            theme = theme_mgr.get_theme_by_fqdn(req_host)

        is_private = (
            not getattr(s, "anonymous_readonly", True)
            or getattr(s, "robots_policy", "public") == "disallow_all"
            or (theme is not None and not getattr(theme, "is_public", True))
        )

        if is_private:
            return PlainTextResponse("User-agent: *\nDisallow: /\n", media_type="text/plain; charset=utf-8")

        proto = request.headers.get("x-forwarded-proto") or request.url.scheme or "https"
        authority = host_header or getattr(s, "fqdn", "") or "localhost"
        sitemap_url = f"{proto}://{authority}/sitemap.xml"

        lines = [
            "User-agent: *",
            "Allow: /$",
            "Allow: /p?",
            "Allow: /static/",
            "Allow: /images/",
            "Allow: /p/images/",
            "Allow: /fonts/",
            "Allow: /favicon.*",
            "Disallow: /search",
            "Disallow: /ingest",
            "Disallow: /reingest",
            "Disallow: /regenerate",
            "Disallow: /document/raw",
            "Disallow: /document/regenerate",
            "Disallow: /document/presentation/generate",
            "Disallow: /document/presentation/compose",
            "Disallow: /document/presentation/compile",
            "Disallow: /document/seen",
            "Disallow: /document/pin",
            "Disallow: /document/hide",
            "Disallow: /document/title",
            "Disallow: /document/purge",
            "Disallow: /document/matrix/purge",
            "Disallow: /purge",
            "Disallow: /synthesize",
            "Disallow: /research",
            "Disallow: /dedup/",
            "Disallow: /entity/",
            "Disallow: /resolution/",
            "Disallow: /oauth/",
            "Disallow: /.well-known/oauth*",
            "Disallow: /mcp",
            "Disallow: /support/bundle",
            "Disallow: /graph/bundle",
            "Disallow: /whoami",
            "Disallow: /stats",
            "Disallow: /limits",
            "Disallow: /ingest-limits",
            "Disallow: /providers",
            "Disallow: /themes",
            "Disallow: /syndication",
            "Disallow: /prototype",
        ]

        if getattr(s, "block_ai_crawlers", True):
            ai_agents = [
                "GPTBot",
                "ChatGPT-User",
                "ClaudeBot",
                "anthropic-ai",
                "CCBot",
                "Bytespider",
                "PerplexityBot",
                "Cohere-ai",
                "Diffbot",
                "FacebookBot",
            ]
            lines.append("")
            for agent in ai_agents:
                lines.append(f"User-agent: {agent}")
            lines.append("Disallow: /")

        lines.append("")
        lines.append(f"Sitemap: {sitemap_url}")
        content = "\n".join(lines) + "\n"
        return PlainTextResponse(content, media_type="text/plain; charset=utf-8")

    async def sitemap_xml_route(request: Request) -> Response:
        if not getattr(s, "sitemap_enabled", True) or not getattr(s, "anonymous_readonly", True):
            return PlainTextResponse("Not Found", status_code=404)

        host_header = request.headers.get("host") or ""
        req_host = host_header.split(":", 1)[0].strip().lower()

        theme: ThemeInfo | None = None
        if getattr(s, "multi_theme", False) and theme_mgr is not None and req_host:
            theme = theme_mgr.get_theme_by_fqdn(req_host)

        if theme is not None and not getattr(theme, "is_public", True):
            return PlainTextResponse("Not Found", status_code=404)

        proto = request.headers.get("x-forwarded-proto") or request.url.scheme or "https"
        authority = host_header or getattr(s, "fqdn", "") or "localhost"
        base_url = f"{proto}://{authority}".rstrip("/")

        cache_ttl = float(getattr(s, "sitemap_cache_ttl", 900))
        now = time.time()
        cached = _sitemap_cache.get(authority)
        if cached is not None:
            cached_at, cached_etag, cached_xml = cached
            if now - cached_at < cache_ttl:
                if request.headers.get("if-none-match") == cached_etag:
                    return Response(status_code=304, headers={"ETag": cached_etag})
                return Response(
                    cached_xml,
                    media_type="application/xml; charset=utf-8",
                    headers={
                        "ETag": cached_etag,
                        "Cache-Control": f"public, max-age={int(cache_ttl)}",
                    },
                )

        # (DB 파일, 테마 ID). 모든 테마가 한 DB를 쓰므로 테마 ID로 범위를 정한다.
        target_dbs: list[tuple[Path, int]] = []
        if theme is not None:
            t_settings = theme_mgr.get_settings_for_theme(theme.id, s)
            target_dbs = [(t_settings.db_file, theme.id)]
        elif getattr(s, "multi_theme", False) and theme_mgr is not None:
            for t in theme_mgr.list_themes():
                if getattr(t, "is_public", True):
                    t_settings = theme_mgr.get_settings_for_theme(t.id, s)
                    target_dbs.append((t_settings.db_file, t.id))
        else:
            target_dbs = [(s.db_file, 0)]

        all_doc_entries: list[dict[str, Any]] = []
        for db_file, target_tid in target_dbs:
            if not db_file.is_file():
                continue
            try:
                conn = dbm.connect_existing(db_file)
                try:
                    entries = dbm.list_public_sitemap_documents(conn, ensure_shares=True, theme_id=target_tid)
                    all_doc_entries.extend(entries)
                finally:
                    conn.close()
            except Exception:
                pass

        xml_lines = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
            '  <url>',
            f'    <loc>{base_url}/</loc>',
            '    <changefreq>daily</changefreq>',
            '    <priority>1.0</priority>',
            '  </url>',
        ]

        seen_tokens: set[str] = set()
        for doc in all_doc_entries:
            tok = doc.get("token")
            if not tok or tok in seen_tokens:
                continue
            seen_tokens.add(tok)
            loc = f"{base_url}/p?s={tok}"
            lastmod = ""
            fetched_at = doc.get("fetched_at")
            published_at = doc.get("published_at")
            if fetched_at:
                try:
                    dt = datetime.fromtimestamp(float(fetched_at), tz=timezone.utc)
                    lastmod = dt.strftime("%Y-%m-%dT%H:%M:%SZ")
                except Exception:
                    pass
            elif published_at and isinstance(published_at, str) and len(published_at) >= 10:
                lastmod = published_at.strip()

            xml_lines.append("  <url>")
            xml_lines.append(f"    <loc>{loc}</loc>")
            if lastmod:
                xml_lines.append(f"    <lastmod>{lastmod}</lastmod>")
            xml_lines.append("    <changefreq>weekly</changefreq>")
            xml_lines.append("    <priority>0.8</priority>")
            xml_lines.append("  </url>")

        xml_lines.append("</urlset>")
        xml_bytes = "\n".join(xml_lines).encode("utf-8")
        etag = f'"{hashlib.md5(xml_bytes).hexdigest()}"'
        _sitemap_cache[authority] = (now, etag, xml_bytes)

        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers={"ETag": etag})

        return Response(
            xml_bytes,
            media_type="application/xml; charset=utf-8",
            headers={
                "ETag": etag,
                "Cache-Control": f"public, max-age={int(cache_ttl)}",
            },
        )

    async def favicon_ico_route(_request: Request) -> Response:
        path = _STATIC_ICONS_DIR / "favicon.ico"
        if not path.is_file():
            return PlainTextResponse("Not Found", status_code=404)
        return FileResponse(path, media_type="image/x-icon")

    async def favicon_svg_route(_request: Request) -> Response:
        path = _STATIC_ICONS_DIR / "favicon.svg"
        if not path.is_file():
            return PlainTextResponse("Not Found", status_code=404)
        return FileResponse(path, media_type="image/svg+xml")

    async def apple_touch_route(_request: Request) -> Response:
        path = _STATIC_ICONS_DIR / "apple-touch-icon.png"
        if not path.is_file():
            return PlainTextResponse("Not Found", status_code=404)
        return FileResponse(path, media_type="image/png")

    async def manifest_route(_request: Request) -> Response:
        path = _STATIC_ICONS_DIR / "manifest.json"
        if not path.is_file():
            return PlainTextResponse("Not Found", status_code=404)
        return FileResponse(path, media_type="application/manifest+json")

    async def browserconfig_route(_request: Request) -> Response:
        path = _STATIC_ICONS_DIR / "browserconfig.xml"
        if not path.is_file():
            return PlainTextResponse("Not Found", status_code=404)
        return FileResponse(path, media_type="application/xml")

    async def icon_file_route(request: Request) -> Response:
        rel = request.query_params.get("p", "") or request.query_params.get("name", "")
        if not _ICON_FILENAME_RE.fullmatch(rel):
            return PlainTextResponse("Not Found", status_code=404)
        path = _STATIC_ICONS_DIR / rel
        if not path.is_file():
            return PlainTextResponse("Not Found", status_code=404)
        media_type = None
        if rel.endswith(".png"):
            media_type = "image/png"
        elif rel.endswith(".svg"):
            media_type = "image/svg+xml"
        elif rel.endswith(".ico"):
            media_type = "image/x-icon"
        elif rel.endswith(".json") or rel.endswith(".webmanifest"):
            media_type = "application/manifest+json"
        elif rel.endswith(".xml"):
            media_type = "application/xml"
        return FileResponse(path, media_type=media_type)

    def _create_font_file_handler(name: str) -> Any:
        async def _font_route(_request: Request) -> Response:
            path = _STATIC_FONTS_DIR / name
            if not path.is_file():
                return PlainTextResponse("Not Found", status_code=404)
            return FileResponse(
                path,
                media_type="font/woff2",
                headers={"Cache-Control": "public, max-age=31536000, immutable"},
            )

        return _font_route

    async def font_file_route(request: Request) -> Response:
        rel = request.query_params.get("p", "") or request.query_params.get("name", "")
        if not _FONT_FILENAME_RE.fullmatch(rel):
            return PlainTextResponse("Not Found", status_code=404)
        path = _STATIC_FONTS_DIR / rel
        if not path.is_file():
            return PlainTextResponse("Not Found", status_code=404)
        return FileResponse(
            path,
            media_type="font/woff2",
            headers={"Cache-Control": "public, max-age=31536000, immutable"},
        )

    async def image_route(request: Request) -> Response:
        rel = request.query_params.get("p", "")
        if not _IMAGE_PATH_RE.fullmatch(rel):
            return PlainTextResponse("Not Found", status_code=404)
        path = s.data_dir / rel
        if not path.is_file():
            return PlainTextResponse("Not Found", status_code=404)
        return FileResponse(path)

    async def presentation_image_route(request: Request) -> Response:
        filename = request.path_params.get("filename", "").strip().lstrip("/")
        if filename.startswith("images/"):
            filename = filename[len("images/"):]
        rel = f"images/{filename}"
        if not _IMAGE_PATH_RE.fullmatch(rel):
            return PlainTextResponse("Not Found", status_code=404)
        path = s.data_dir / rel
        if not path.is_file():
            return PlainTextResponse("Not Found", status_code=404)
        return FileResponse(path)

    async def documents_list_route(request: Request) -> JSONResponse:
        from ..store.queries import documents_list

        _documents_list = _resolve_query_func("documents_list", documents_list)
        include_hidden = request_auth_scope(request) != "anonymous"
        theme, theme_settings, _ = _get_theme_ctx(request)

        def _documents() -> dict[str, Any]:
            conn = dbm.connect_existing(theme_settings.db_file, readonly=True)
            try:
                format_status = dbm.check_format_mismatch(conn, getattr(theme_settings, "render_format", "md"), theme_id=theme.id)
                docs = _documents_list(conn, limit=300, include_hidden=include_hidden, theme_id=theme.id)
                return {
                    "documents": docs,
                    "format_status": format_status,
                    "theme_id": theme.id,
                    "theme_label": theme.label,
                }
            finally:
                conn.close()

        return JSONResponse(await asyncio.to_thread(_documents))

    async def node_detail(request: Request) -> JSONResponse:
        from ..store.queries import node_detail as _detail

        _detail = _resolve_query_func("node_detail", _detail)
        node_id = request.query_params.get("id", "")
        if not node_id:
            raise HTTPException(status_code=400, detail="id required")

        include_hidden = request_auth_scope(request) != "anonymous"
        theme, theme_settings, _ = _get_theme_ctx(request)

        def _load() -> dict[str, Any] | None:
            conn = dbm.connect_existing(theme_settings.db_file, readonly=True)
            try:
                rep = _detail(conn, node_id, include_hidden=include_hidden, theme_id=theme.id)
                if rep is not None:
                    rep["theme_id"] = theme.id
                    rep["theme_label"] = theme.label
                return rep
            finally:
                conn.close()

        report = await asyncio.to_thread(_load)
        if report is None:
            raise HTTPException(status_code=404, detail="not found")
        return JSONResponse(report)

    async def document_detail_route(request: Request) -> JSONResponse:
        from ..store.queries import document_detail

        _document_detail = _resolve_query_func("document_detail", document_detail)
        document_id = request.query_params.get("id", "")
        if not document_id:
            raise HTTPException(status_code=400, detail="id required")

        include_hidden = request_auth_scope(request) != "anonymous"
        theme_ref = _extract_theme_ref(request)
        theme, theme_settings, _ = _get_theme_ctx(request)

        def _load(settings: Settings, t_info: ThemeInfo) -> dict[str, Any] | None:
            conn = dbm.connect_existing(settings.db_file, readonly=True)
            try:
                # GET은 readonly 사용자에게도 열리므로 열람 상태를 변경하지 않는다.
                rep = _document_detail(conn, document_id, include_hidden=include_hidden, theme_id=t_info.id)
                if rep is not None:
                    rep["theme_id"] = t_info.id
                    rep["theme_label"] = t_info.label
                return rep
            finally:
                conn.close()

        report = await asyncio.to_thread(_load, theme_settings, theme)

        if report is None and theme_ref is None and getattr(s, "multi_theme", False):
            scope = request_auth_scope(request)
            targets = await asyncio.to_thread(
                theme_mgr.resolve_document_targets, doc_id=document_id
            )
            for target in targets:
                tid = target.get("theme_id")
                if tid is None or tid == theme.id:
                    continue
                try:
                    cand_theme = theme_mgr.get_theme(tid)
                except Exception:
                    continue
                if scope == "anonymous" and not cand_theme.is_public:
                    continue
                if scope == "collaborator" and not (
                    cand_theme.is_public or cand_theme.is_collaborator_accessible
                ):
                    continue
                cand_settings = theme_mgr.get_settings_for_theme(cand_theme.id, s)
                cand_report = await asyncio.to_thread(_load, cand_settings, cand_theme)
                if cand_report is not None:
                    report = cand_report
                    break

        if report is None:
            raise HTTPException(status_code=404, detail="not found")
        return JSONResponse(report)

    async def document_raw_route(request: Request) -> Response:
        # 이 경로의 모든 응답(오류 포함)에 붙는 보안 헤더. 전역 미들웨어는 기존 헤더를 덮어쓰지 않는다.
        raw_security_headers = {
            "Content-Security-Policy": "sandbox; default-src 'none'",
            "X-Content-Type-Options": "nosniff",
        }
        doc_id = request.query_params.get("id", "").strip()
        if not doc_id:
            return PlainTextResponse("Missing document id", status_code=400, headers=raw_security_headers)

        theme_ref = _extract_theme_ref(request)
        theme, theme_settings, _ = _get_theme_ctx(request)

        def _find_raw(t_settings: Settings, t_theme_id: int) -> tuple[str, str] | None:
            conn = dbm.connect_existing(t_settings.db_file, readonly=True)
            try:
                try:
                    row = conn.execute(
                        "SELECT file_ref, file_name FROM raw_inbox WHERE document_id = ? AND theme_id = ? AND file_ref IS NOT NULL AND trim(file_ref) != '' ORDER BY id DESC LIMIT 1",
                        (doc_id, t_theme_id),
                    ).fetchone()
                    if row and row["file_ref"]:
                        return str(row["file_ref"]), str(row["file_name"] or "")
                except Exception:
                    pass

                row_doc = dbm.get_document_row(conn, doc_id, theme_id=t_theme_id)
                if row_doc:
                    raw_meta = None
                    try:
                        raw_meta = row_doc["meta"]
                    except (IndexError, KeyError):
                        pass
                    if raw_meta:
                        try:
                            meta = json.loads(raw_meta)
                        except Exception:
                            meta = {}
                        pres_pdf = meta.get("presentation_pdf")
                        if isinstance(pres_pdf, dict) and pres_pdf.get("artifact_path"):
                            return str(pres_pdf["artifact_path"]), str(pres_pdf.get("file_name") or "")
                        if meta.get("original_requested_file"):
                            return str(meta["original_requested_file"]), ""
                return None
            finally:
                conn.close()

        resolved = await asyncio.to_thread(_find_raw, theme_settings, theme.id)
        active_settings = theme_settings

        if resolved is None and theme_ref is None and getattr(s, "multi_theme", False):
            scope = request_auth_scope(request)
            targets = await asyncio.to_thread(
                theme_mgr.resolve_document_targets, doc_id=doc_id
            )
            for target in targets:
                tid = target.get("theme_id")
                if tid is None or tid == theme.id:
                    continue
                try:
                    cand_theme = theme_mgr.get_theme(tid)
                except Exception:
                    continue
                if scope == "collaborator" and not (
                    cand_theme.is_public or cand_theme.is_collaborator_accessible
                ):
                    continue
                cand_settings = theme_mgr.get_settings_for_theme(cand_theme.id, s)
                cand_res = await asyncio.to_thread(_find_raw, cand_settings, cand_theme.id)
                if cand_res is not None:
                    resolved = cand_res
                    active_settings = cand_settings
                    break

        if resolved is None:
            return PlainTextResponse("Not Found", status_code=404, headers=raw_security_headers)

        file_ref, file_name = resolved
        raw_path = Path(file_ref)
        if raw_path.is_absolute():
            target_path = raw_path.resolve()
        else:
            target_path = (active_settings.data_dir / raw_path).resolve()

        allowed_roots: list[Path] = [
            active_settings.data_dir.resolve(),
            s.data_dir.resolve(),
        ]

        is_safe = False
        for root in allowed_roots:
            try:
                target_path.relative_to(root)
                is_safe = True
                break
            except ValueError:
                continue

        if not is_safe or not target_path.is_file():
            return PlainTextResponse("Not Found", status_code=404, headers=raw_security_headers)

        # 업로드 원본은 비신뢰 데이터다. 브라우저가 실행할 수 있는 형식(HTML·SVG·XML·JS)은 앱 origin에서
        # 렌더링되지 않도록 octet-stream + attachment로 내려보낸다. 나머지 안전한 형식만 inline 유지.
        _MEDIA_TYPES = {
            ".pdf": "application/pdf",
            ".odt": "application/vnd.oasis.opendocument.text",
            ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ".txt": "text/plain; charset=utf-8",
            ".md": "text/markdown; charset=utf-8",
            ".json": "application/json",
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".webp": "image/webp",
        }
        _ACTIVE_SUFFIXES = {
            ".html", ".htm", ".xhtml", ".svg", ".svgz", ".xml", ".xsl", ".js", ".mjs",
        }

        def _is_active_type(mt: str) -> bool:
            base = mt.split(";", 1)[0].strip().lower()
            subtype = base.split("/", 1)[1] if "/" in base else ""
            return (
                base in {"text/html", "image/svg+xml", "application/xhtml+xml"}
                or subtype == "xml"
                or subtype.endswith("+xml")
                or "javascript" in base
                or "ecmascript" in base
            )

        suffix = target_path.suffix.lower()
        guessed_type = mimetypes.guess_type(target_path.name)[0] or ""
        if suffix in _ACTIVE_SUFFIXES or _is_active_type(guessed_type):
            media_type = "application/octet-stream"
        else:
            media_type = _MEDIA_TYPES.get(suffix) or guessed_type or "application/octet-stream"
            if _is_active_type(media_type):
                media_type = "application/octet-stream"
        disposition = "attachment" if media_type == "application/octet-stream" else "inline"

        display_name = file_name or target_path.name
        safe_name = re.sub(r'[^a-zA-Z0-9._-]', '_', display_name) or "document"
        quoted_name = quote(display_name, safe="")

        headers = {
            "Content-Disposition": f"{disposition}; filename=\"{safe_name}\"; filename*=UTF-8''{quoted_name}",
            "Cache-Control": "private, no-cache, no-store, must-revalidate",
            **raw_security_headers,
        }
        if media_type == "application/pdf":
            # 브라우저 내장 PDF 뷰어는 CSP sandbox 문서에서 열리지 않는다. PDF는 HTML로 실행되지 않으므로 sandbox만 뺀다.
            headers.pop("Content-Security-Policy", None)
        return FileResponse(str(target_path), media_type=media_type, headers=headers)

    async def document_seen_route(request: Request) -> JSONResponse:
        body = await _json_object(request)
        theme, theme_settings, _ = _get_theme_ctx(request, body)
        document_id = str(body.get("id") or "").strip()
        if not document_id:
            raise HTTPException(status_code=400, detail="id required")

        def _mark() -> bool:
            conn = dbm.connect_existing(theme_settings.db_file)
            try:
                row = dbm.get_document_row(conn, document_id, theme_id=theme.id)
                if row is None:
                    return False
                dbm.set_document_seen(conn, document_id, seen=True, theme_id=theme.id)
                return True
            finally:
                conn.close()

        if not await asyncio.to_thread(_mark):
            raise HTTPException(status_code=404, detail="not found")
        resp = {"id": document_id, "seen": True}
        if theme.id != 0:
            resp["theme_id"] = theme.id
        return JSONResponse(resp)

    async def document_pin_route(request: Request) -> JSONResponse:
        body = await _json_object(request)
        theme, theme_settings, _ = _get_theme_ctx(request, body)
        document_id = str(body.get("id") or "").strip()
        if not document_id:
            raise HTTPException(status_code=400, detail="id required")
        pinned = bool(body.get("pinned", True))

        def _pin() -> bool:
            conn = dbm.connect_existing(theme_settings.db_file)
            try:
                return dbm.set_document_pinned(conn, document_id, pinned, theme_id=theme.id)
            finally:
                conn.close()

        if not await asyncio.to_thread(_pin):
            raise HTTPException(status_code=404, detail="not found")
        resp = {"id": document_id, "pinned": pinned}
        if theme.id != 0:
            resp["theme_id"] = theme.id
        return JSONResponse(resp)

    async def document_hide_route(request: Request) -> JSONResponse:
        body = await _json_object(request)
        theme, theme_settings, _ = _get_theme_ctx(request, body)
        document_id = str(body.get("id") or "").strip()
        if not document_id:
            raise HTTPException(status_code=400, detail="id required")
        hidden = bool(body.get("hidden", True))

        def _hide() -> bool:
            conn = dbm.connect_existing(theme_settings.db_file)
            try:
                return dbm.set_document_hidden(conn, document_id, hidden, theme_id=theme.id)
            finally:
                conn.close()

        if not await asyncio.to_thread(_hide):
            raise HTTPException(status_code=404, detail="not found")
        resp = {"id": document_id, "hidden": hidden}
        if theme.id != 0:
            resp["theme_id"] = theme.id
        return JSONResponse(resp)

    async def document_title_route(request: Request) -> JSONResponse:
        body = await _json_object(request)
        theme, theme_settings, _ = _get_theme_ctx(request, body)
        document_id = str(body.get("id") or "").strip()
        if not document_id:
            raise HTTPException(status_code=400, detail="id required")
        raw_title = body.get("title")
        title = str(raw_title).strip() if raw_title is not None else None

        def _update_title() -> bool:
            conn = dbm.connect_existing(theme_settings.db_file)
            try:
                return dbm.set_document_title(conn, document_id, title, theme_id=theme.id)
            finally:
                conn.close()

        if not await asyncio.to_thread(_update_title):
            raise HTTPException(status_code=404, detail="not found")
        resp = {"id": document_id, "title": title}
        if theme.id != 0:
            resp["theme_id"] = theme.id
        return JSONResponse(resp)

    async def document_matrix_purge_route(request: Request) -> JSONResponse:
        body = await _json_object(request)
        theme, theme_settings, _ = _get_theme_ctx(request, body)
        document_id = str(body.get("id") or "").strip()
        if not document_id:
            raise HTTPException(status_code=400, detail="id required")

        def _purge() -> bool:
            conn = dbm.connect_existing(theme_settings.db_file)
            try:
                row = dbm.get_document_row(conn, document_id, theme_id=theme.id)
                if row is None:
                    return False
                return dbm.purge_document_matrix(conn, document_id, theme_id=theme.id)
            finally:
                conn.close()

        if not await asyncio.to_thread(_purge):
            raise HTTPException(status_code=404, detail="not found")

        from ..ingest.active import purge_active_matrix
        await asyncio.to_thread(purge_active_matrix, theme_settings.data_dir, document_id)

        resp = {"id": document_id, "purged": True}
        if theme.id != 0:
            resp["theme_id"] = theme.id
        return JSONResponse(resp)

    async def document_purge_route(request: Request) -> JSONResponse:
        body = await _json_object(request)
        theme, theme_settings, _ = _get_theme_ctx(request, body)
        scope = request_auth_scope(request)
        if scope != "owner":
            raise HTTPException(status_code=403, detail="소각(Purge) 권한은 소유자(owner) 전용입니다.")
        if not s.is_purge_allowed:
            return JSONResponse(
                {"error": "데이터 소각이 정책에 의해 차단되었습니다 (CLAIRE_DATA_LIFECYCLE=append-only).", "ok": False},
                status_code=403,
            )
        target = str(body.get("target") or body.get("id") or body.get("doc_id") or "").strip()
        if not target:
            raise HTTPException(status_code=400, detail="target required")
        dry_run = bool(body.get("dry_run", False))
        tombstone = bool(body.get("tombstone", True))
        reason = str(body.get("reason") or "web_purge").strip() or "web_purge"

        def _do_purge() -> dict[str, Any]:
            conn = dbm.connect_existing(theme_settings.db_file)
            try:
                matched = dbm.resolve_document_targets(conn, target=target, theme_id=theme.id)
                if not matched:
                    return {"count": 0, "targets": [], "error": f"일치하는 소각 대상 문서를 찾을 수 없습니다: {target}"}
                target_ids = [m["id"] for m in matched]
                report = dbm.purge_document_cascade(
                    conn,
                    data_dir=theme_settings.data_dir,
                    target_ids=target_ids,
                    reason=reason,
                    dry_run=dry_run,
                    tombstone=tombstone,
                    theme_id=theme.id,
                )
                if not dry_run:
                    from ..ingest.active import purge_active_matrix
                    for tid in target_ids:
                        try:
                            purge_active_matrix(theme_settings.data_dir, tid)
                        except Exception:
                            pass
                report["targets"] = matched
                report["theme_id"] = theme.id
                return report
            finally:
                conn.close()

        res = await asyncio.to_thread(_do_purge)
        if res.get("error") and not res.get("targets"):
            return JSONResponse(res, status_code=404)
        return JSONResponse(res)

    async def synthesize_route(request: Request) -> JSONResponse:
        from ..store.queries import synthesize

        _synthesize = _resolve_query_func("synthesize", synthesize)
        body = await _json_object(request)
        theme, theme_settings, theme_svc = _get_theme_ctx(request, body)
        await _require_theme_feature(theme, theme_svc, "synthesize")
        entity_ids = body.get("node_ids") or []
        if not isinstance(entity_ids, list) or not entity_ids:
            raise HTTPException(status_code=400, detail="node_ids required")
        query = body.get("query")

        def _synthesize_job() -> dict[str, Any]:
            conn = dbm.connect_existing(theme_settings.db_file, readonly=True)
            try:
                res = _synthesize(conn, theme_svc.provider, entity_ids, query, theme_id=theme.id)
                res["theme_id"] = theme.id
                res["theme_label"] = theme.label
                return res
            finally:
                conn.close()

        return JSONResponse(await _run_expensive(_synthesize_job))

    async def research_route(request: Request) -> StreamingResponse:
        from ..expand.research import contextual_research

        body = await _json_object(request)
        # 요청 테마의 설정(theme_id)으로 맥락을 읽고 적재한다
        research_theme, research_settings, research_svc = _get_theme_ctx(request, body)
        # research(allow_tools=True)는 관리자 전용이다(§10.1). 멤버 테마면 프로바이더에 닿기 전에 403.
        await _require_theme_feature(research_theme, research_svc, "research")
        query = str(body.get("query") or "").strip()
        if not query:
            raise HTTPException(status_code=400, detail="query required")
        if len(query) > _MAX_SEARCH_QUERY_LENGTH:
            raise HTTPException(status_code=400, detail="query is too long")
        node_id = body.get("node_id") or None
        document_id = body.get("doc_id") or None

        _reserve_expensive_job()
        loop = asyncio.get_running_loop()
        events: asyncio.Queue[dict[str, Any]] = asyncio.Queue(
            maxsize=_PROGRESS_QUEUE_SIZE
        )

        def _enqueue_progress(event: dict[str, Any]) -> None:
            if not events.full():
                events.put_nowait(event)

        def on_progress(event: dict[str, Any]) -> None:
            try:
                loop.call_soon_threadsafe(_enqueue_progress, event)
            except RuntimeError:
                # 서버 종료 뒤 끝난 sync worker의 늦은 진행 알림은 버린다.
                pass

        try:
            task = asyncio.create_task(
                asyncio.to_thread(
                    contextual_research,
                    research_settings,
                    research_svc.provider,
                    query=query,
                    node_id=node_id,
                    doc_id=document_id,
                    progress=on_progress,
                )
            )
        except BaseException:
            _release_expensive_job()
            raise
        task.add_done_callback(_release_expensive_job)
        task.add_done_callback(_consume_task_result)

        async def stream() -> AsyncIterator[bytes]:
            try:
                while True:
                    if task.done() and events.empty():
                        await asyncio.sleep(0.01)
                        if events.empty():
                            break
                    try:
                        event = await asyncio.wait_for(events.get(), timeout=0.1)
                    except asyncio.TimeoutError:
                        continue
                    yield (json.dumps(event, ensure_ascii=False) + "\n").encode()
                try:
                    result = task.result()
                except policy.ProviderPolicyError as exc:
                    result = _failure_result(exc, "research")
                except Exception as exc:  # noqa: BLE001
                    _log_operation_failure(request, "research", exc)
                    result = {"error": "research failed", "ok": False}
                yield (
                    json.dumps(
                        {"done": True, "result": result},
                        ensure_ascii=False,
                    )
                    + "\n"
                ).encode()
            finally:
                if task.done():
                    _consume_task_result(task)

        return StreamingResponse(stream(), media_type="application/x-ndjson")

    async def ingest_stream_route(request: Request) -> StreamingResponse:
        from ..extract.provider import set_progress_callback

        body = await _json_object(request)
        theme, _, theme_svc = _get_theme_ctx(request, body)
        scope = request_auth_scope(request)
        if scope == "collaborator":
            if theme.is_default or theme.id == 0:
                raise HTTPException(
                    status_code=403,
                    detail="Collaborator는 기본 지식베이스에 적재할 수 없습니다. 추가 지식 테마를 선택하세요.",
                )
            if not theme.is_collaborator_accessible:
                raise HTTPException(
                    status_code=403,
                    detail="Collaborator에게 공개되지 않은 테마입니다.",
                )
        payload = str(body.get("payload") or "").strip()
        if not payload:
            raise HTTPException(status_code=400, detail="payload required")
        expand_max = body.get("expand_max")
        format_arg = str(body.get("format") or "").strip() or None
        focus = str(body.get("focus") or "").strip() or None
        if not focus:
            from ..telegram_bot import parse_message_focus

            payload, parsed_focus = parse_message_focus(payload)
            if parsed_focus:
                focus = parsed_focus
            elif not theme.is_default and theme.id > 0 and getattr(theme, "default_focus", None):
                focus = theme.default_focus.strip() or None

        full_content = bool(body.get("full_content") or body.get("no_truncate") or False)
        effort = str(body.get("effort") or "").strip() or None

        _reserve_expensive_job()
        loop = asyncio.get_running_loop()
        events: asyncio.Queue[dict[str, Any]] = asyncio.Queue(
            maxsize=_PROGRESS_QUEUE_SIZE
        )

        def _enqueue_progress(event: dict[str, Any]) -> None:
            if not events.full():
                events.put_nowait(event)

        def on_progress(message: Any) -> None:
            try:
                if isinstance(message, dict):
                    loop.call_soon_threadsafe(_enqueue_progress, message)
                else:
                    loop.call_soon_threadsafe(
                        _enqueue_progress,
                        {"stage": "work", "msg": str(message)},
                    )
            except RuntimeError:
                pass

        def _run() -> Any:
            set_progress_callback(on_progress)
            try:
                ingest_kwargs: dict[str, Any] = {
                    "source": "web",
                    "expand_max": expand_max,
                }
                if full_content:
                    ingest_kwargs["full_content"] = True
                if effort is not None:
                    ingest_kwargs["effort"] = effort
                if format_arg is not None:
                    ingest_kwargs["format"] = format_arg
                if focus is not None:
                    ingest_kwargs["focus"] = focus
                return theme_svc.ingest(
                    payload,
                    **ingest_kwargs,
                )
            finally:
                set_progress_callback(None)

        try:
            task = asyncio.create_task(asyncio.to_thread(_run))
        except BaseException:
            _release_expensive_job()
            raise
        task.add_done_callback(_release_expensive_job)
        task.add_done_callback(_consume_task_result)

        async def stream() -> AsyncIterator[bytes]:
            try:
                while True:
                    if task.done() and events.empty():
                        await asyncio.sleep(0.01)
                        if events.empty():
                            break
                    try:
                        event = await asyncio.wait_for(events.get(), timeout=0.1)
                    except asyncio.TimeoutError:
                        continue
                    yield (json.dumps(event, ensure_ascii=False) + "\n").encode()
                try:
                    result = report_to_dict(task.result())
                    if theme.id != 0:
                        result["theme_id"] = theme.id
                        result["theme_label"] = theme.label
                except policy.ProviderPolicyError as exc:
                    result = _failure_result(exc, "ingest")
                except Exception as exc:  # noqa: BLE001
                    _log_operation_failure(request, "ingest stream", exc)
                    result = {"error": "ingest failed", "ok": False}
                yield (
                    json.dumps(
                        {"done": True, "result": result},
                        ensure_ascii=False,
                    )
                    + "\n"
                ).encode()
            finally:
                if task.done():
                    _consume_task_result(task)

        return StreamingResponse(stream(), media_type="application/x-ndjson")

    async def reingest_stream_route(request: Request) -> StreamingResponse:
        from ..extract.provider import set_progress_callback

        body = await _json_object(request)
        theme, theme_settings, theme_svc = _get_theme_ctx(request, body)
        scope = request_auth_scope(request)
        if scope == "collaborator":
            if theme.is_default or theme.id == 0:
                raise HTTPException(
                    status_code=403,
                    detail="Collaborator는 기본 지식베이스에 재적재할 수 없습니다. 추가 지식 테마를 선택하세요.",
                )
            if not theme.is_collaborator_accessible:
                raise HTTPException(
                    status_code=403,
                    detail="Collaborator에게 공개되지 않은 테마입니다.",
                )
        target = str(body.get("target") or body.get("payload") or body.get("id") or body.get("doc_id") or "").strip()
        if not target:
            raise HTTPException(status_code=400, detail="target or payload required")
        expand_max = body.get("expand_max")
        format_arg = str(body.get("format") or "").strip() or None
        focus = str(body.get("focus") or "").strip() or None
        if not focus and not theme.is_default and theme.id > 0 and getattr(theme, "default_focus", None):
            focus = theme.default_focus.strip() or None

        full_content = bool(body.get("full_content") or body.get("no_truncate") or False)
        effort = str(body.get("effort") or "").strip() or None
        untombstone = bool(body.get("untombstone", False))

        if untombstone:
            def _lift():
                conn = dbm.connect_existing(theme_settings.db_file)
                try:
                    return dbm.remove_tombstones(conn, target=target, theme_id=theme.id)
                finally:
                    conn.close()
            await asyncio.to_thread(_lift)

        _reserve_expensive_job()
        loop = asyncio.get_running_loop()
        events: asyncio.Queue[dict[str, Any]] = asyncio.Queue(
            maxsize=_PROGRESS_QUEUE_SIZE
        )

        def _enqueue_progress(event: dict[str, Any]) -> None:
            if not events.full():
                events.put_nowait(event)

        def on_progress(message: Any) -> None:
            try:
                if isinstance(message, dict):
                    loop.call_soon_threadsafe(_enqueue_progress, message)
                else:
                    loop.call_soon_threadsafe(
                        _enqueue_progress,
                        {"stage": "work", "msg": str(message)},
                    )
            except RuntimeError:
                pass

        def _run() -> Any:
            set_progress_callback(on_progress)
            try:
                conn = dbm.connect_existing(theme_settings.db_file)
                try:
                    matched = dbm.resolve_document_targets(conn, target=target, theme_id=theme.id)
                finally:
                    conn.close()

                if matched:
                    t_doc = matched[0]
                    res = theme_svc.regenerate_components(
                        doc_id=t_doc["id"],
                        refetch=not full_content,
                        refetch_full=full_content,
                        all_components=True,
                        force=True,
                        effort=effort,
                        format=format_arg,
                        focus=focus,
                        on_progress=lambda stage, msg="": on_progress({"stage": stage, "msg": msg or stage}),
                    )
                    t_info = res.get("targets", [{}])[0] if res.get("targets") else {}
                    return {
                        "document_id": t_doc["id"],
                        "title": t_info.get("title") or t_doc.get("title") or target,
                        "updated": True,
                        "duplicate": False,
                        "summary": t_info.get("new_summary") or "",
                        "entities_created": t_info.get("entities_created", 0),
                        "entities_linked": t_info.get("entities_linked", 0),
                        "relations_added": t_info.get("relations_added", 0),
                        "full_content": full_content,
                        "effort": effort,
                        "focus": focus,
                        "theme_id": theme.id,
                        "theme_label": theme.label,
                    }
                else:
                    ingest_kwargs: dict[str, Any] = {
                        "source": "api-reingest",
                        "expand_max": expand_max,
                    }
                    if full_content:
                        ingest_kwargs["full_content"] = True
                    if effort is not None:
                        ingest_kwargs["effort"] = effort
                    if format_arg is not None:
                        ingest_kwargs["format"] = format_arg
                    if focus is not None:
                        ingest_kwargs["focus"] = focus
                    rep = theme_svc.ingest(target, **ingest_kwargs)
                    rep_dict = report_to_dict(rep)
                    rep_dict["theme_id"] = theme.id
                    rep_dict["theme_label"] = theme.label
                    return rep_dict
            finally:
                set_progress_callback(None)

        try:
            task = asyncio.create_task(asyncio.to_thread(_run))
        except BaseException:
            _release_expensive_job()
            raise
        task.add_done_callback(_release_expensive_job)
        task.add_done_callback(_consume_task_result)

        async def stream() -> AsyncIterator[bytes]:
            try:
                while True:
                    if task.done() and events.empty():
                        await asyncio.sleep(0.01)
                        if events.empty():
                            break
                    try:
                        event = await asyncio.wait_for(events.get(), timeout=0.1)
                    except asyncio.TimeoutError:
                        continue
                    yield (json.dumps(event, ensure_ascii=False) + "\n").encode()
                try:
                    result = task.result()
                    if isinstance(result, IngestReport):
                        result = report_to_dict(result)
                    if isinstance(result, dict) and theme.id != 0:
                        result["theme_id"] = theme.id
                        result["theme_label"] = theme.label
                except Exception as exc:  # noqa: BLE001
                    _log_operation_failure(request, "reingest stream", exc)
                    result = _failure_result(exc, "reingest")
                yield (
                    json.dumps(
                        {"done": True, "result": result},
                        ensure_ascii=False,
                    )
                    + "\n"
                ).encode()
            finally:
                if task.done():
                    _consume_task_result(task)

        return StreamingResponse(stream(), media_type="application/x-ndjson")

    async def regenerate_stream_route(request: Request) -> Response:
        from ..extract.provider import set_progress_callback

        body = await _json_object(request)
        theme, theme_settings, theme_svc = _get_theme_ctx(request, body)
        scope = request_auth_scope(request)
        target = str(body.get("target") or body.get("payload") or body.get("id") or body.get("doc_id") or "").strip()
        doc_id = str(body.get("doc_id") or "").strip() or None
        token = str(body.get("token") or "").strip() or None
        if not target and not doc_id and not token:
            raise HTTPException(status_code=400, detail="target, doc_id, or token required")

        theme_ref = _extract_theme_ref(request, body)
        if theme_ref is None and getattr(s, "multi_theme", False) and theme_mgr is not None:
            targets = await asyncio.to_thread(
                theme_mgr.resolve_document_targets,
                target=target or None,
                doc_id=doc_id,
                token=token,
            )
            live = _first_live_target(targets)
            if live is not None:
                cand_theme = live[1]
                if cand_theme.id != theme.id:
                    try:
                        if scope != "collaborator" or cand_theme.is_collaborator_accessible or cand_theme.is_public:
                            theme = cand_theme
                            theme_settings = theme_mgr.get_settings_for_theme(theme.id, s)
                            theme_svc = service_pool.get_service(theme.id)
                    except Exception:
                        pass

        if scope == "collaborator":
            if theme.is_default or theme.id == 0:
                raise HTTPException(
                    status_code=403,
                    detail="Collaborator는 기본 지식베이스를 직접 재생성할 수 없습니다. 추가 지식 테마를 선택하세요.",
                )
            if not theme.is_collaborator_accessible:
                raise HTTPException(
                    status_code=403,
                    detail="Collaborator에게 공개되지 않은 테마입니다.",
                )

        component = str(body.get("component") or "").strip().lower()
        all_comp = False
        summary = False
        detail = False
        graph = False
        if component in ("all", ""):
            all_comp = True
        elif component in ("detail", "format"):
            detail = True
        elif component == "summary":
            summary = True
        elif component == "graph":
            graph = True

        if body.get("all") or body.get("all_components"):
            all_comp = True
            summary = detail = graph = False
        elif body.get("detail"):
            detail = True
            all_comp = summary = graph = False
        elif body.get("summary"):
            summary = True
            all_comp = detail = graph = False
        elif body.get("graph"):
            graph = True
            all_comp = summary = detail = False

        dry_run = bool(body.get("dry_run", False))
        force = not dry_run
        refetch = bool(body.get("refetch", False))
        refetch_full = bool(body.get("refetch_full", False))
        effort = str(body.get("effort") or "").strip() or None
        format_arg = str(body.get("format") or "").strip() or None
        focus = str(body.get("focus") or "").strip() or None
        if not focus and not theme.is_default and theme.id > 0 and getattr(theme, "default_focus", None):
            focus = theme.default_focus.strip() or None

        _reserve_expensive_job()
        loop = asyncio.get_running_loop()
        events: asyncio.Queue[dict[str, Any]] = asyncio.Queue(
            maxsize=_PROGRESS_QUEUE_SIZE
        )

        def _enqueue_progress(event: dict[str, Any]) -> None:
            if not events.full():
                events.put_nowait(event)

        def on_progress(message: Any) -> None:
            try:
                if isinstance(message, dict):
                    loop.call_soon_threadsafe(_enqueue_progress, message)
                else:
                    loop.call_soon_threadsafe(
                        _enqueue_progress,
                        {"stage": "work", "msg": str(message)},
                    )
            except RuntimeError:
                pass

        def _run() -> Any:
            set_progress_callback(on_progress)
            try:
                res = theme_svc.regenerate_components(
                    target=target or None,
                    doc_id=doc_id,
                    token=token,
                    summary=summary,
                    detail=detail,
                    graph=graph,
                    all_components=all_comp,
                    refetch=refetch,
                    refetch_full=refetch_full,
                    force=force,
                    effort=effort,
                    format=format_arg,
                    focus=focus,
                    on_progress=lambda stage, msg="": on_progress({"stage": stage, "msg": msg or stage}),
                )
                if isinstance(res, dict) and theme.id != 0:
                    res["theme_id"] = theme.id
                    res["theme_label"] = theme.label
                return res
            finally:
                set_progress_callback(None)

        try:
            task = asyncio.create_task(asyncio.to_thread(_run))
        except BaseException:
            _release_expensive_job()
            raise
        task.add_done_callback(_release_expensive_job)
        task.add_done_callback(_consume_task_result)

        async def stream() -> AsyncIterator[bytes]:
            try:
                while True:
                    if task.done() and events.empty():
                        await asyncio.sleep(0.01)
                        if events.empty():
                            break
                    try:
                        event = await asyncio.wait_for(events.get(), timeout=0.1)
                    except asyncio.TimeoutError:
                        continue
                    yield (json.dumps(event, ensure_ascii=False) + "\n").encode()
                try:
                    result = task.result()
                    if isinstance(result, IngestReport):
                        result = report_to_dict(result)
                    if isinstance(result, dict) and theme.id != 0:
                        result["theme_id"] = theme.id
                        result["theme_label"] = theme.label
                except Exception as exc:  # noqa: BLE001
                    _log_operation_failure(request, "regenerate stream", exc)
                    result = _failure_result(exc, "regenerate")
                yield (
                    json.dumps(
                        {"done": True, "result": result},
                        ensure_ascii=False,
                    )
                    + "\n"
                ).encode()
            finally:
                if task.done():
                    _consume_task_result(task)

        return StreamingResponse(stream(), media_type="application/x-ndjson")

    async def dedup_scan_route(request: Request) -> JSONResponse:
        from ..store.queries import dedup_clusters

        _dedup_clusters = _resolve_query_func("dedup_clusters", dedup_clusters)
        theme, theme_settings, theme_svc = _get_theme_ctx(request)

        def _scan() -> dict[str, Any]:
            scan = theme_svc.dedup_scan()
            conn = dbm.connect_existing(theme_settings.db_file, readonly=True)
            try:
                res = _dedup_clusters(conn, scan, theme_id=theme.id)
                if theme.id != 0:
                    res["theme_id"] = theme.id
                    res["theme_label"] = theme.label
                return res
            finally:
                conn.close()

        return JSONResponse(await _run_expensive(_scan))

    async def dedup_merge_route(request: Request) -> JSONResponse:
        body = await _json_object(request)
        theme, _, theme_svc = _get_theme_ctx(request, body)
        keeper = str(body.get("keeper") or "").strip()
        losers = [str(item) for item in (body.get("losers") or []) if item]
        if not keeper or not losers:
            raise HTTPException(status_code=400, detail="keeper and losers required")
        try:
            result = await _run_expensive(
                theme_svc.merge_one_cluster,
                keeper,
                losers,
            )
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            _log_operation_failure(request, "dedup merge", exc)
            return JSONResponse(
                {"error": "dedup merge failed"},
                status_code=500,
            )
        return JSONResponse(result)

    async def entity_primary_label_route(request: Request) -> JSONResponse:
        body = await _json_object(request)
        theme, theme_settings, _ = _get_theme_ctx(request, body)
        entity_id = str(body.get("id") or "").strip()
        new_name = str(body.get("name") or "").strip()
        if not entity_id:
            raise HTTPException(status_code=400, detail="id required")
        if not new_name:
            raise HTTPException(status_code=400, detail="name required")

        def _select() -> dict[str, Any]:
            conn = dbm.connect_existing(theme_settings.db_file)
            try:
                try:
                    updated = dbm.select_entity_primary_label(conn, entity_id, new_name, theme_id=theme.id)
                except KeyError as e:
                    raise HTTPException(status_code=404, detail=scrub_exception(e))
                except ValueError as e:
                    raise HTTPException(status_code=400, detail=scrub_exception(e))

                from ..store.queries import node_detail as _detail

                _detail_fn = _resolve_query_func("node_detail", _detail)
                rep = _detail_fn(conn, updated.id, theme_id=theme.id)
                if rep is not None and theme.id != 0:
                    rep["theme_id"] = theme.id
                    rep["theme_label"] = theme.label
                return rep or {
                    "id": updated.id,
                    "name": updated.name,
                    "type": updated.type,
                    "aliases": updated.aliases,
                }
            finally:
                conn.close()

        result = await asyncio.to_thread(_select)
        return JSONResponse(result)

    async def rollback_resolution_route(request: Request) -> JSONResponse:
        body = await _json_object(request)
        theme, theme_settings, theme_svc = _get_theme_ctx(request, body)
        doc_id = str(body.get("document_id") or "").strip()
        entity_name = str(body.get("entity") or "").strip()
        if not doc_id:
            raise HTTPException(status_code=400, detail="document_id required")
        if not entity_name:
            raise HTTPException(status_code=400, detail="entity required")

        from ..extract.decision import get_resolution_log_from_meta, rollback_resolution

        def _do_rollback() -> dict[str, Any]:
            conn = dbm.connect_existing(theme_settings.db_file)
            try:
                doc = dbm.get_document(conn, doc_id, theme_id=theme.id)
                if not doc:
                    raise HTTPException(status_code=404, detail="document not found")
                decisions = get_resolution_log_from_meta(doc.meta)
                target_dec = None
                for d in decisions:
                    if d.entity == entity_name and d.decision == "MERGE" and not getattr(d, "rolled_back", False):
                        target_dec = d
                        break
                if not target_dec:
                    return {"ok": False, "error": "no eligible merge decision found to rollback"}

                success = rollback_resolution(conn, target_dec, theme_id=theme.id)
                if success:
                    raw_log = (doc.meta or {}).get("resolution_log") or []
                    for item in raw_log:
                        if isinstance(item, dict) and item.get("entity") == entity_name and item.get("decision") == "MERGE":
                            item["rolled_back"] = True
                            item["decision"] = "ROLLED_BACK"
                            item["reason"] = (item.get("reason", "") + " (롤백됨)").strip()
                    dbm.update_document_meta(conn, doc_id, doc.meta, theme_id=theme.id)
                    return {"ok": True, "entity": entity_name}
                return {"ok": False, "error": "rollback execution failed"}
            finally:
                conn.close()

        res = await asyncio.to_thread(_do_rollback)
        return JSONResponse(res)

    async def resolution_decisions_route(request: Request) -> JSONResponse:
        theme, theme_settings, _ = _get_theme_ctx(request)
        try:
            limit = min(500, max(1, int(request.query_params.get("limit", 100))))
        except (ValueError, TypeError):
            limit = 100
        try:
            offset = max(0, int(request.query_params.get("offset", 0)))
        except (ValueError, TypeError):
            offset = 0

        from ..extract.decision import get_all_resolution_decisions

        def _fetch() -> list[dict[str, Any]]:
            conn = dbm.connect_existing(theme_settings.db_file)
            try:
                return get_all_resolution_decisions(conn, limit=limit, offset=offset, theme_id=theme.id)
            finally:
                conn.close()

        decisions = await asyncio.to_thread(_fetch)
        return JSONResponse({"ok": True, "decisions": decisions, "limit": limit, "offset": offset})

    async def create_share_route(request: Request) -> JSONResponse:
        body = await _json_object(request)
        theme, theme_settings, _ = _get_theme_ctx(request, body)
        document_id = str(body.get("doc_id") or "").strip()
        if not document_id:
            raise HTTPException(status_code=400, detail="doc_id required")

        include_hidden = request_auth_scope(request) != "anonymous"

        def _share(settings: Settings, target_theme_info: ThemeInfo) -> str | None:
            conn = dbm.connect_existing(settings.db_file)
            try:
                row = dbm.get_document_row(conn, document_id, theme_id=target_theme_info.id)
                if row is None:
                    return None
                if not include_hidden and bool(row["hidden"]):
                    return None
                return dbm.create_doc_share(conn, document_id, theme_id=target_theme_info.id)
            finally:
                conn.close()

        token = await asyncio.to_thread(_share, theme_settings, theme)
        target_theme_id = theme.id

        if not token and getattr(s, "multi_theme", False):
            scope = request_auth_scope(request)
            targets = await asyncio.to_thread(
                theme_mgr.resolve_document_targets, doc_id=document_id
            )
            for target in targets:
                tid = target.get("theme_id")
                if tid is None or tid == theme.id:
                    continue
                try:
                    candidate_theme = theme_mgr.get_theme(tid)
                except Exception:
                    continue
                if scope == "anonymous" and not candidate_theme.is_public:
                    continue
                if scope == "collaborator" and not (
                    candidate_theme.is_public
                    or candidate_theme.is_collaborator_accessible
                ):
                    continue
                cand_settings = theme_mgr.get_settings_for_theme(candidate_theme.id, s)
                cand_token = await asyncio.to_thread(_share, cand_settings, candidate_theme)
                if cand_token:
                    token = cand_token
                    target_theme_id = candidate_theme.id
                    break

        if not token:
            raise HTTPException(status_code=404, detail="document not found")
        return JSONResponse({"token": token, "path": "/p?s=" + token, "theme_id": target_theme_id})

    async def shared_doc_page(request: Request) -> Response:
        from ..graphview import shared_html
        from ..store.queries import document_detail

        _document_detail = _resolve_query_func("document_detail", document_detail)
        token = request.query_params.get("s", "")
        if not dbm.plausible_share_token(token):
            return PlainTextResponse("Not Found", status_code=404)

        def _load() -> dict[str, Any] | None:
            # 1. 모든 테마 DB에서 공유 토큰 자동 검색 (숨김 문서는 공유 페이지 접근 차단)
            resolved = theme_mgr.resolve_share_token(token, include_hidden=False)
            if resolved is not None:
                doc = resolved[2]
                if doc and not doc.get("hidden"):
                    return doc
                return None
            # 2. 기본 DB 폴백
            conn = dbm.connect_existing(s.db_file, readonly=True)
            document_id = None
            try:
                document_id = dbm.resolve_doc_share(conn, token, theme_id=0)
                if not document_id:
                    return None
                doc = _document_detail(conn, document_id, include_hidden=False, theme_id=0)
                if doc is not None and not doc.get("hidden"):
                    return doc
            finally:
                conn.close()

            # When resolving token via default DB fallback: if document_id was found
            # in doc_shares of Theme 0, but _document_detail is None in Theme 0,
            # search other themes via theme_mgr.resolve_document_targets(doc_id=document_id).
            if document_id and getattr(s, "multi_theme", False):
                targets = theme_mgr.resolve_document_targets(doc_id=document_id)
                for target in targets:
                    target_db = target.get("db_file")
                    if not target_db or str(target_db) == str(s.db_file):
                        continue
                    try:
                        t_conn = dbm.connect_existing(Path(target_db), readonly=True)
                        try:
                            doc = _document_detail(t_conn, document_id, include_hidden=False, theme_id=target["theme_id"])
                            if doc is not None and not doc.get("hidden"):
                                return doc
                        finally:
                            t_conn.close()
                    except Exception:
                        continue
            return None

        document = await asyncio.to_thread(_load)
        if document is None or document.get("hidden"):
            return PlainTextResponse("Not Found", status_code=404)

        base_url = ""
        if getattr(s, "public_url", ""):
            base_url = s.public_url.rstrip("/")
        else:
            proto = request.headers.get("x-forwarded-proto") or request.url.scheme
            host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
            if proto and host:
                base_url = f"{proto}://{host}".rstrip("/")
            else:
                base_url = str(request.base_url).rstrip("/")

        scope = request_auth_scope(request)
        if scope not in ("owner", "collaborator"):
            auth_hdr = request.headers.get("authorization", "")
            if auth_hdr.lower().startswith("bearer "):
                bearer_tok = auth_hdr[7:].strip()
                if (getattr(s, "inject_token", None) and bearer_tok == s.inject_token) or (
                    getattr(s, "owner_token", None) and bearer_tok == s.owner_token
                ):
                    scope = "owner"
                elif getattr(s, "collaborator_token", None) and bearer_tok == s.collaborator_token:
                    scope = "collaborator"
            elif (cookie_token := read_session_cookie(request.scope)[1]) is not None:
                try:
                    conn_sess = dbm.connect_existing(s.db_file, readonly=True)
                    try:
                        sess_scope = dbm.validate_session_scope(
                            conn_sess, cookie_token, scopes=("owner", "collaborator")
                        )
                        if sess_scope:
                            scope = sess_scope
                    finally:
                        conn_sess.close()
                except Exception:
                    pass

        can_access_raw = scope in ("owner", "collaborator")
        return HTMLResponse(
            shared_html(
                document,
                s,
                base_url=base_url,
                share_token=token,
                can_access_raw=can_access_raw,
            )
        )

    def _resolve_presentation_target(
        request: Request, doc_id: str = "", token: str = ""
    ) -> tuple[ThemeInfo, Settings, Path, Path, str, str]:
        target_theme, theme_settings, _ = _get_theme_ctx(request, None)
        active_db = theme_settings.db_file
        active_data_dir = theme_settings.data_dir
        resolved_doc_id = doc_id
        resolved_doc_title = ""

        if token:
            resolved = theme_mgr.resolve_share_token(token, include_hidden=False)
            resolved_theme: ThemeInfo | None = None
            if resolved is not None:
                try:
                    resolved_theme = theme_mgr.get_theme(resolved[0])
                except ThemeNotFound:
                    # 삭제된 테마의 공유 토큰은 찾지 못한 것으로 본다 (테마 0으로 바꾸지 않는다)
                    resolved = None
            if resolved is not None and resolved_theme is not None:
                target_theme = resolved_theme
                theme_settings = theme_mgr.get_settings_for_theme(target_theme.id, s)
                active_db = theme_settings.db_file
                active_data_dir = theme_settings.data_dir
                doc_obj = resolved[2]
                if not doc_obj.get("hidden"):
                    resolved_doc_id = doc_obj.get("id", "")
                    resolved_doc_title = doc_obj.get("title", "")
            else:
                conn = dbm.connect_existing(active_db, readonly=True)
                try:
                    cand_id = dbm.resolve_doc_share(conn, token, theme_id=target_theme.id) or ""
                    if cand_id:
                        d_obj = dbm.get_document(conn, cand_id, theme_id=target_theme.id)
                        if d_obj and not bool(d_obj.hidden):
                            resolved_doc_id = cand_id
                            resolved_doc_title = d_obj.title or ""
                finally:
                    conn.close()
        elif getattr(s, "multi_theme", False) and resolved_doc_id:
            targets = theme_mgr.resolve_document_targets(doc_id=resolved_doc_id)
            live = _first_live_target(targets) if targets else None
            if targets and live is None:
                # 삭제된 테마에만 남은 문서는 찾지 못한 것으로 본다 (테마 0 설정으로 다루지 않는다)
                raise HTTPException(status_code=404, detail="not found")
            if live is not None and live[0].get("db_file"):
                live_target, target_theme = live
                active_db = Path(live_target["db_file"])
                theme_settings = theme_mgr.get_settings_for_theme(target_theme.id, s)
                if live_target.get("data_dir"):
                    active_data_dir = Path(live_target["data_dir"])

        return target_theme, theme_settings, active_db, active_data_dir, resolved_doc_id, resolved_doc_title

    async def presentation_page(request: Request) -> Response:
        from starlette.responses import FileResponse, HTMLResponse
        from ..presentation import PresentationService, render_unready_presentation_page

        token = request.query_params.get("s", "").strip()
        doc_id = request.query_params.get("id", "").strip()

        if token and not dbm.plausible_share_token(token):
            return PlainTextResponse("Not Found", status_code=404)

        target_theme, theme_settings, active_db, active_data_dir, doc_id, doc_title = (
            _resolve_presentation_target(request, doc_id=doc_id, token=token)
        )

        if not doc_id:
            return PlainTextResponse("Not Found" if token else "Missing document id or share token", status_code=404 if token else 400)

        if not getattr(target_theme, "presentation_enabled", False):
            back_url = f"/p?s={token}" if token else f"/?id={doc_id}"
            html_content = render_unready_presentation_page(
                title=doc_title or "프레젠테이션",
                status="disabled",
                back_url=back_url,
            )
            return HTMLResponse(html_content, status_code=403)

        slide_theme = request.query_params.get("slide_theme") or request.query_params.get("presentation_theme") or "night"
        transition = request.query_params.get("transition", "slide")

        svc = PresentationService(data_dir=active_data_dir, settings=theme_settings, theme_id=target_theme.id)
        conn = dbm.connect_existing(active_db)
        try:
            if not doc_title:
                d_obj = dbm.get_document(conn, doc_id, theme_id=target_theme.id)
                if d_obj:
                    doc_title = d_obj.title or ""
            # Option A: allow_compose=False -> Never trigger heavy LLM generation on GET
            res = await svc.get_or_create_presentation(
                conn, doc_id, theme=slide_theme, transition=transition, allow_compose=False
            )
        except KeyError:
            return PlainTextResponse("Document not found", status_code=404)
        except Exception as exc:
            return PlainTextResponse(f"Presentation generation failed: {scrub_exception(exc)}", status_code=500)
        finally:
            conn.close()

        file_path = Path(res["file_path"])
        is_ready = bool(res.get("is_ready") or res.get("status") == "ready") and file_path.is_file()

        if not is_ready:
            back_url = f"/p?s={token}" if token else f"/?id={doc_id}"
            status = res.get("status", "not_created")
            html_content = render_unready_presentation_page(
                title=doc_title or "프레젠테이션",
                status=status,
                back_url=back_url,
            )
            resp_status = 200 if status in ("composing", "compiling", "authored") else 404
            return HTMLResponse(html_content, status_code=resp_status)

        try:
            content_str = file_path.read_text(encoding="utf-8")
            if (
                "<!-- Claire Bible Presentation HUD Toolbar -->" not in content_str
                or "cb-subslide-badge" in content_str
                or "cb-down-hint" in content_str
                or "decorateSubslides" in content_str
                or "하위 슬라이드로 계속" in content_str
                or "is-embedded" not in content_str
                or "문서로" in content_str
                or "cbReturnToDoc" in content_str
                or "reveal.js/dist/" in content_str
                or "reveal.js/plugin/" in content_str
            ):
                from ..presentation.hud import inject_hud_toolbar
                cleaned_str = inject_hud_toolbar(content_str)
                file_path.write_text(cleaned_str, encoding="utf-8")
        except Exception:
            pass

        return FileResponse(
            file_path,
            media_type="text/html",
            headers={"Cache-Control": "no-cache"},
        )

    async def document_presentation_get_route(request: Request) -> JSONResponse:
        from ..presentation import PresentationService

        doc_id = request.query_params.get("id", "").strip()
        if not doc_id:
            return JSONResponse({"error": "id parameter required"}, status_code=400)

        target_theme, theme_settings, active_db, active_data_dir, _, _ = (
            _resolve_presentation_target(request, doc_id=doc_id)
        )

        if not getattr(target_theme, "presentation_enabled", False):
            return JSONResponse(
                {
                    "error": "presentation_disabled",
                    "detail": "이 데이터베이스에서는 프레젠테이션 기능이 비활성화되어 있습니다.",
                    "presentation_enabled": False,
                },
                status_code=403,
            )

        svc = PresentationService(data_dir=active_data_dir, settings=theme_settings, theme_id=target_theme.id)
        conn = dbm.connect_existing(active_db, readonly=True)
        try:
            meta = svc.get_presentation_metadata(conn, doc_id)
            if not meta:
                return JSONResponse({"status": "not_created", "document_id": doc_id, "is_ready": False}, status_code=200)
            return JSONResponse(meta, status_code=200)
        finally:
            conn.close()

    async def document_presentation_generate_route(request: Request) -> JSONResponse:
        from ..presentation import PresentationService

        doc_id = request.query_params.get("id", "").strip()
        slide_theme = request.query_params.get("slide_theme") or request.query_params.get("presentation_theme") or "night"
        transition = request.query_params.get("transition", "slide")
        force = request.query_params.get("force", "false").lower() in ("true", "1", "yes")
        wait_param = request.query_params.get("wait")

        if not doc_id:
            try:
                body = await request.json()
                if isinstance(body, dict):
                    doc_id = body.get("id", "").strip()
                    slide_theme = body.get("slide_theme") or body.get("presentation_theme") or body.get("theme", slide_theme)
                    transition = body.get("transition", transition)
                    force = bool(body.get("force", force))
                    if wait_param is None and "wait" in body:
                        wait_param = str(body["wait"])
            except Exception:
                pass

        if not doc_id:
            return JSONResponse({"error": "id parameter required"}, status_code=400)

        wait_for_completion = True
        if wait_param is not None and str(wait_param).lower() in ("false", "0", "no"):
            wait_for_completion = False

        target_theme, theme_settings, active_db, active_data_dir, _, _ = (
            _resolve_presentation_target(request, doc_id=doc_id)
        )

        if not getattr(target_theme, "presentation_enabled", False):
            return JSONResponse(
                {
                    "error": "presentation_disabled",
                    "detail": "이 데이터베이스에서는 프레젠테이션 기능이 비활성화되어 있습니다.",
                    "presentation_enabled": False,
                },
                status_code=403,
            )
        # 프레젠테이션 저작은 LLM을 쓴다. 멤버 테마는 등급 기능(또는 BYOK)일 때만 허용한다(§10.3).
        await _require_theme_feature(target_theme, None, "presentation")

        svc = PresentationService(data_dir=active_data_dir, settings=theme_settings, theme_id=target_theme.id)

        if not wait_for_completion:
            conn = dbm.connect_existing(active_db, readonly=True)
            try:
                if dbm.get_document_row(conn, doc_id, theme_id=target_theme.id) is None:
                    return JSONResponse({"error": "document not found"}, status_code=404)
                meta = svc.get_presentation_metadata(conn, doc_id)
                if not force and meta and meta.get("is_ready") and meta.get("status") == "ready":
                    return JSONResponse(meta, status_code=200)
            finally:
                conn.close()

            async def _bg_generate():
                bg_conn = dbm.connect_existing(active_db)
                try:
                    await svc.get_or_create_presentation(
                        bg_conn,
                        doc_id,
                        theme=slide_theme,
                        transition=transition,
                        force_recompile=force,
                    )
                except Exception as exc:
                    log.warning(
                        "Background presentation generation error for %s: %s", doc_id, scrub_exception(exc)
                    )
                finally:
                    bg_conn.close()

            asyncio.create_task(_bg_generate())
            return JSONResponse(
                {
                    "document_id": doc_id,
                    "status": "composing",
                    "message": "Presentation generation started in background",
                    "retry_after": 2.5,
                },
                status_code=202,
            )

        conn = dbm.connect_existing(active_db)
        try:
            res = await svc.get_or_create_presentation(
                conn, doc_id, theme=slide_theme, transition=transition, force_recompile=force
            )
            return JSONResponse(res, status_code=200)
        except KeyError:
            return JSONResponse({"error": "document not found"}, status_code=404)
        except Exception as exc:
            return JSONResponse({"error": scrub_exception(exc)}, status_code=500)
        finally:
            conn.close()

    async def document_presentation_compose_route(request: Request) -> JSONResponse:
        from ..presentation import PresentationService

        doc_id = request.query_params.get("id", "").strip()
        focus = request.query_params.get("focus")
        slide_budget = int(request.query_params.get("slide_budget", 10))
        theme_val = request.query_params.get("theme", "night")
        trans_val = request.query_params.get("transition", "slide")
        no_compile = request.query_params.get("no_compile", "false").lower() in ("true", "1", "yes")

        try:
            body = await request.json()
            if isinstance(body, dict):
                doc_id = body.get("id", doc_id).strip()
                focus = body.get("focus", focus)
                slide_budget = int(body.get("slide_budget", slide_budget))
                theme_val = body.get("theme", theme_val)
                trans_val = body.get("transition", trans_val)
                no_compile = bool(body.get("no_compile", no_compile))
        except Exception:
            pass

        if not doc_id:
            return JSONResponse({"error": "id parameter required"}, status_code=400)

        target_theme, theme_settings, active_db, active_data_dir, _, _ = (
            _resolve_presentation_target(request, doc_id=doc_id)
        )

        if not getattr(target_theme, "presentation_enabled", False):
            return JSONResponse(
                {
                    "error": "presentation_disabled",
                    "detail": "이 데이터베이스에서는 프레젠테이션 기능이 비활성화되어 있습니다.",
                    "presentation_enabled": False,
                },
                status_code=403,
            )
        # 프레젠테이션 저작은 LLM을 쓴다. 멤버 테마는 등급 기능(또는 BYOK)일 때만 허용한다(§10.3).
        await _require_theme_feature(target_theme, None, "presentation")

        svc = PresentationService(data_dir=active_data_dir, settings=theme_settings, theme_id=target_theme.id)
        conn = dbm.connect_existing(active_db)
        try:
            res = await svc.compose_presentation(
                conn,
                doc_id,
                focus=focus,
                slide_budget=slide_budget,
                theme=theme_val,
                transition=trans_val,
            )
            if not no_compile:
                res = await svc.compile_presentation(
                    conn, doc_id, theme=theme_val, transition=trans_val, force_recompile=True
                )
            return JSONResponse(res, status_code=200)
        except KeyError:
            return JSONResponse({"error": "document not found"}, status_code=404)
        except Exception as exc:
            return JSONResponse({"error": scrub_exception(exc)}, status_code=500)
        finally:
            conn.close()

    async def document_presentation_compile_route(request: Request) -> JSONResponse:
        from ..presentation import PresentationService

        doc_id = request.query_params.get("id", "").strip()
        theme_val = request.query_params.get("theme", "night")
        trans_val = request.query_params.get("transition", "slide")
        force = request.query_params.get("force", "true").lower() in ("true", "1", "yes")

        try:
            body = await request.json()
            if isinstance(body, dict):
                doc_id = body.get("id", doc_id).strip()
                theme_val = body.get("theme", theme_val)
                trans_val = body.get("transition", trans_val)
                force = bool(body.get("force", force))
        except Exception:
            pass

        if not doc_id:
            return JSONResponse({"error": "id parameter required"}, status_code=400)

        target_theme, theme_settings, active_db, active_data_dir, _, _ = (
            _resolve_presentation_target(request, doc_id=doc_id)
        )

        if not getattr(target_theme, "presentation_enabled", False):
            return JSONResponse(
                {
                    "error": "presentation_disabled",
                    "detail": "이 데이터베이스에서는 프레젠테이션 기능이 비활성화되어 있습니다.",
                    "presentation_enabled": False,
                },
                status_code=403,
            )
        # 프레젠테이션 저작은 LLM을 쓴다. 멤버 테마는 등급 기능(또는 BYOK)일 때만 허용한다(§10.3).
        await _require_theme_feature(target_theme, None, "presentation")

        svc = PresentationService(data_dir=active_data_dir, settings=theme_settings, theme_id=target_theme.id)
        conn = dbm.connect_existing(active_db)
        try:
            res = await svc.compile_presentation(
                conn, doc_id, theme=theme_val, transition=trans_val, force_recompile=force
            )
            return JSONResponse(res, status_code=200)
        except KeyError:
            return JSONResponse({"error": "document not found"}, status_code=404)
        except Exception as exc:
            return JSONResponse({"error": scrub_exception(exc)}, status_code=500)
        finally:
            conn.close()

    async def document_presentation_adoc_route(request: Request) -> Response:
        from ..presentation import PresentationService

        doc_id = request.query_params.get("id", "").strip()
        if not doc_id:
            try:
                body = await request.json()
                if isinstance(body, dict):
                    doc_id = body.get("id", "").strip()
            except Exception:
                pass

        if not doc_id:
            return JSONResponse({"error": "id parameter required"}, status_code=400)

        target_theme, theme_settings, active_db, active_data_dir, _, _ = (
            _resolve_presentation_target(request, doc_id=doc_id)
        )

        if not getattr(target_theme, "presentation_enabled", False):
            return JSONResponse(
                {
                    "error": "presentation_disabled",
                    "detail": "이 데이터베이스에서는 프레젠테이션 기능이 비활성화되어 있습니다.",
                    "presentation_enabled": False,
                },
                status_code=403,
            )
        if request.method == "PUT":
            await _require_theme_feature(target_theme, None, "presentation")

        svc = PresentationService(data_dir=active_data_dir, settings=theme_settings, theme_id=target_theme.id)

        if request.method == "GET":
            conn = dbm.connect_existing(active_db, readonly=True)
            try:
                adoc = dbm.get_presentation_adoc(conn, doc_id, theme_id=target_theme.id)
                if not adoc:
                    adoc_path = svc.get_presentation_adoc_path(doc_id)
                    if adoc_path.exists():
                        adoc = adoc_path.read_text(encoding="utf-8")
                if not adoc:
                    return JSONResponse({"error": "presentation adoc not found"}, status_code=404)
                return JSONResponse({"document_id": doc_id, "presentation_adoc": adoc}, status_code=200)
            finally:
                conn.close()

        elif request.method == "PUT":
            body = await request.json()
            if not isinstance(body, dict) or "presentation_adoc" not in body:
                return JSONResponse({"error": "presentation_adoc field required"}, status_code=400)
            adoc_content = str(body["presentation_adoc"])
            recompile = bool(body.get("recompile", True))
            theme_val = body.get("theme", "night")
            trans_val = body.get("transition", "slide")

            conn = dbm.connect_existing(active_db)
            try:
                # Save adoc file
                adoc_path = svc.get_presentation_adoc_path(doc_id)
                adoc_path.write_text(adoc_content, encoding="utf-8")

                content_hash = hashlib.sha256(adoc_content.encode("utf-8")).hexdigest()
                adoc_hash = content_hash
                cache_key = hashlib.sha256(f"{adoc_hash}:{theme_val}:{trans_val}".encode("utf-8")).hexdigest()
                slide_count = adoc_content.count("\n== ") + adoc_content.count("\n=== ") + 1

                html_file = svc.get_presentation_file_path(doc_id)
                dbm.save_presentation_adoc(
                    conn,
                    document_id=doc_id,
                    theme_id=target_theme.id,
                    presentation_adoc=adoc_content,
                    content_hash=content_hash,
                    adoc_hash=adoc_hash,
                    authoring_provider="manual",
                    authoring_model="user-edit",
                    slide_count=slide_count,
                    theme=theme_val,
                    transition=trans_val,
                    cache_key=cache_key,
                    file_path=str(html_file),
                    status="authored",
                )

                if recompile:
                    res = await svc.compile_presentation(
                        conn, doc_id, presentation_adoc=adoc_content, theme=theme_val, transition=trans_val, force_recompile=True
                    )
                    return JSONResponse(res, status_code=200)
                return JSONResponse({"status": "authored", "document_id": doc_id, "slide_count": slide_count}, status_code=200)
            finally:
                conn.close()

        return JSONResponse({"error": "method not allowed"}, status_code=405)

    mcp_app = build_mcp_app(s, theme_mgr=theme_mgr)

    async def mcp_route(request: Request) -> Response:
        response_meta: dict = {}
        chunks: list[bytes] = []

        async def send(message: dict) -> None:
            if message["type"] == "http.response.start":
                response_meta["status"] = message["status"]
                response_meta["headers"] = message.get("headers", [])
            elif message["type"] == "http.response.body":
                chunks.append(message.get("body", b""))

        await mcp_app(request.scope, request.receive, send)
        status_code = response_meta.get("status", 500)
        resp = Response(
            content=b"".join(chunks),
            status_code=status_code,
        )
        for k, v in response_meta.get("headers", []):
            name = k.decode("latin-1")
            if name.lower() == "content-length":
                continue
            resp.headers[name] = v.decode("latin-1")

        if status_code != 200:
            from ..store.telemetry import record_mcp_telemetry

            record_mcp_telemetry(
                s.data_dir,
                call_type="http_mcp",
                tool_name=None,
                status="UNAUTHORIZED" if status_code == 401 else "ERROR",
                error_code=status_code,
                error_message=f"HTTP {status_code}",
                response_bytes=len(resp.body) if hasattr(resp, "body") else len(b"".join(chunks)),
            )

        return resp

    async def create_support_bundle_route(request: Request) -> JSONResponse:
        from ..support_bundle import (
            DEFAULT_SUPPORT_BUNDLE_DAYS,
            create_support_bundle,
            validate_bundle_days,
        )

        content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if content_type in ("application/json", "application/problem+json"):
            body = await _json_object(request)
        else:
            body = {}

        raw_days = body.get("days", DEFAULT_SUPPORT_BUNDLE_DAYS)
        try:
            days = int(raw_days)
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail="days must be an integer")

        target = body.get("target") or body.get("share") or body.get("doc_id")
        if target is not None:
            target = str(target).strip() or None
        source_channel = str(body.get("source_channel") or "api")
        if source_channel not in {"api", "telegram_command", "telegram_callback"}:
            source_channel = "api"

        max_ret = getattr(s, "telemetry_retention_days", 30)
        try:
            validate_bundle_days(days, max_ret)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=scrub_exception(exc))

        def _generate() -> dict[str, Any]:
            info = create_support_bundle(
                s,
                days=days,
                target=target,
                request_context={"channel": source_channel},
            )
            return info.to_dict()

        try:
            bundle_dict = await asyncio.to_thread(_generate)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=scrub_exception(exc))
        return JSONResponse(bundle_dict)

    async def download_support_bundle_route(request: Request) -> Response:
        from ..support_bundle import get_support_bundle, lookup_support_bundle_by_token

        token = request.query_params.get("token", "").strip()
        if not token or len(token) < 16:
            return PlainTextResponse("Not Found", status_code=404)

        record = await asyncio.to_thread(lookup_support_bundle_by_token, s.data_dir, token)
        if not record:
            return PlainTextResponse("Not Found", status_code=404)

        bundle = await asyncio.to_thread(get_support_bundle, s.data_dir, token)
        if not bundle:
            return JSONResponse(
                {"error": "support bundle has expired and was purged"},
                status_code=410,
            )

        filepath = Path(bundle["filepath"])
        if not filepath.is_file():
            return PlainTextResponse("Not Found", status_code=404)

        return FileResponse(
            str(filepath),
            media_type="application/zstd",
            filename=bundle["filename"],
            headers={
                "Cache-Control": "no-store, private",
                "Content-Disposition": f'attachment; filename="{bundle["filename"]}"',
            },
        )

    async def create_graph_bundle_route(request: Request) -> JSONResponse:
        from ..graph_bundle import create_graph_bundle

        scope = request_auth_scope(request)
        if scope != "owner":
            raise HTTPException(status_code=401, detail="owner authorization required")

        content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if content_type in ("application/json", "application/problem+json"):
            body = await _json_object(request)
        else:
            body = {}

        theme, _, _ = _get_theme_ctx(request, body)
        theme_id = theme.id

        source_channel = str(body.get("source_channel") or "api")
        if source_channel not in {"api", "telegram_command", "telegram_callback"}:
            source_channel = "api"

        def _generate() -> dict[str, Any]:
            info = create_graph_bundle(
                s,
                theme_id=theme_id,
                request_context={"channel": source_channel},
            )
            return info.to_dict()

        try:
            bundle_dict = await asyncio.to_thread(_generate)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=scrub_exception(exc))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=scrub_exception(exc))
        return JSONResponse(bundle_dict)

    async def download_graph_bundle_route(request: Request) -> Response:
        from ..graph_bundle import get_graph_bundle, lookup_graph_bundle_by_token

        token = request.query_params.get("token", "").strip()
        if not token or len(token) < 16:
            return PlainTextResponse("Not Found", status_code=404)

        record = await asyncio.to_thread(lookup_graph_bundle_by_token, s.data_dir, token)
        if not record:
            return PlainTextResponse("Not Found", status_code=404)

        bundle = await asyncio.to_thread(get_graph_bundle, s.data_dir, token)
        if not bundle:
            return JSONResponse(
                {"error": "graph bundle has expired and was purged"},
                status_code=410,
            )

        filepath = Path(bundle["filepath"])
        if not filepath.is_file():
            return PlainTextResponse("Not Found", status_code=404)

        return FileResponse(
            str(filepath),
            media_type="application/zstd",
            filename=bundle["filename"],
            headers={
                "Cache-Control": "no-store, private",
                "Content-Disposition": f'attachment; filename="{bundle["filename"]}"',
            },
        )

    @asynccontextmanager
    async def app_lifespan(_app: Starlette):
        from ..support_bundle import purge_expired_bundles
        from ..graph_bundle import purge_expired_graph_bundles

        await asyncio.to_thread(purge_expired_bundles, s.data_dir)
        await asyncio.to_thread(purge_expired_graph_bundles, s.data_dir)
        async with mcp_app.router.lifespan_context(mcp_app):
            yield

    static_dir = Path(__file__).resolve().parent.parent / "static"
    static_dir.mkdir(parents=True, exist_ok=True)
    static_docs_dir = static_dir / "docs"
    docs_template_file = Path(__file__).resolve().parent.parent / "templates" / "docs.html"
    implementation_spec_file = (
        Path(__file__).resolve().parent.parent.parent.parent
        / "docs"
        / "implementation"
        / "openapi.yaml"
    )

    async def docs_ui_route(_request: Request) -> Response:
        if not docs_template_file.is_file():
            return PlainTextResponse("Documentation UI template not found", status_code=500)
        from ..presentation.favicon import inject_favicon_head_tags
        html = docs_template_file.read_text(encoding="utf-8")
        return HTMLResponse(inject_favicon_head_tags(html))

    async def openapi_yaml_route(_request: Request) -> Response:
        spec_path = static_docs_dir / "openapi.yaml"
        if not spec_path.is_file():
            spec_path = implementation_spec_file
        if not spec_path.is_file():
            return PlainTextResponse("OpenAPI specification not found", status_code=404)
        return Response(
            spec_path.read_bytes(),
            media_type="application/yaml",
            headers={"Content-Type": "application/yaml; charset=utf-8"},
        )

    routes = [
        Route("/health", health, methods=["GET"]),
        Route("/robots.txt", robots_txt_route, methods=["GET"]),
        Route("/sitemap.xml", sitemap_xml_route, methods=["GET"]),
        Route("/favicon.ico", favicon_ico_route, methods=["GET"]),
        Route("/favicon.svg", favicon_svg_route, methods=["GET"]),
        Route("/apple-touch-icon.png", apple_touch_route, methods=["GET"]),
        Route("/apple-touch-icon-precomposed.png", apple_touch_route, methods=["GET"]),
        Route("/manifest.json", manifest_route, methods=["GET"]),
        Route("/site.webmanifest", manifest_route, methods=["GET"]),
        Route("/browserconfig.xml", browserconfig_route, methods=["GET"]),
        Route("/icon", icon_file_route, methods=["GET"]),
        Route("/font", font_file_route, methods=["GET"]),
        *(
            Route(f"/fonts/{font_name}", _create_font_file_handler(font_name), methods=["GET"])
            for font_name in FONTS
        ),
        Route("/whoami", whoami, methods=["GET"]),
        Route("/stats", stats, methods=["GET"]),
        Route("/themes", themes_list_route, methods=["GET", "HEAD"]),
        Route("/themes", theme_define_route, methods=["POST"]),
        Route("/themes", theme_update_route, methods=["PATCH"]),
        Route("/themes", theme_delete_route, methods=["DELETE"]),
        Route("/providers", providers_get_route, methods=["GET", "HEAD"]),
        Route("/providers", providers_update_route, methods=["PATCH", "POST"]),
        Route("/providers/test", providers_test_route, methods=["POST"]),
        Route("/limits", limits_get_route, methods=["GET", "HEAD"]),
        Route("/limits", limits_update_route, methods=["PATCH", "POST"]),
        Route("/tokens", tokens_list_route, methods=["GET", "HEAD"]),
        Route("/tokens", tokens_create_route, methods=["POST"]),
        Route("/tokens/revoke", tokens_revoke_route, methods=["POST"]),
        Route("/tokens", tokens_revoke_route, methods=["DELETE"]),
        Route("/ingest-limits", limits_get_route, methods=["GET", "HEAD"]),
        Route("/ingest-limits", limits_update_route, methods=["PATCH", "POST"]),
        Route("/ingest", do_ingest, methods=["POST"]),
        Route("/ingest-stream", ingest_stream_route, methods=["POST"]),
        Route("/reingest", do_reingest, methods=["POST"]),
        Route("/reingest-stream", reingest_stream_route, methods=["POST"]),
        Route("/regenerate", do_regenerate, methods=["POST"]),
        Route("/regenerate-stream", regenerate_stream_route, methods=["POST"]),
        Route("/document/regenerate", do_regenerate, methods=["POST"]),
        Route("/ingest/active", active_ingest_route, methods=["GET"]),
        Route("/search", do_search, methods=["POST"]),
        Route("/explore/accounts", explore_accounts_route, methods=["GET", "HEAD"]),
        Route("/", graph_ui, methods=["GET"]),
        Route("/syndication", syndication_ui_route, methods=["GET", "HEAD"]),
        Route("/prototype/syndication", prototype_syndication_ui_route, methods=["GET", "HEAD"]),
        Route("/syndication/preferences", syndication_preferences_ui_route, methods=["GET", "HEAD"]),
        Route("/api/syndication/dispatch", syndication_dispatch_route, methods=["POST"]),
        Route("/syndication/dispatch", syndication_dispatch_route, methods=["POST"]),
        Route("/api/syndication/dispatches", syndication_dispatches_route, methods=["GET"]),
        Route("/syndication/dispatches", syndication_dispatches_route, methods=["GET"]),
        Route("/graph", graph_data, methods=["GET"]),
        Route("/node", node_detail, methods=["GET"]),
        Route("/documents", documents_list_route, methods=["GET"]),
        Route("/image", image_route, methods=["GET"]),
        Route("/images/{filename:path}", presentation_image_route, methods=["GET", "HEAD"]),
        Route("/p/images/{filename:path}", presentation_image_route, methods=["GET", "HEAD"]),
        Route("/document", document_detail_route, methods=["GET"]),
        Route("/document/raw", document_raw_route, methods=["GET", "HEAD"]),
        Route("/document/seen", document_seen_route, methods=["POST"]),
        Route("/document/pin", document_pin_route, methods=["POST"]),
        Route("/document/hide", document_hide_route, methods=["POST"]),
        Route("/document/title", document_title_route, methods=["POST"]),
        Route("/document/purge", document_purge_route, methods=["POST"]),
        Route("/purge", document_purge_route, methods=["POST"]),
        Route("/document/matrix/purge", document_matrix_purge_route, methods=["POST"]),
        Route("/document/presentation", document_presentation_get_route, methods=["GET", "HEAD"]),
        Route("/document/presentation/generate", document_presentation_generate_route, methods=["POST"]),
        Route("/document/presentation/compose", document_presentation_compose_route, methods=["POST"]),
        Route("/document/presentation/compile", document_presentation_compile_route, methods=["POST"]),
        Route("/document/presentation/adoc", document_presentation_adoc_route, methods=["GET", "PUT"]),
        Route("/synthesize", synthesize_route, methods=["POST"]),
        Route("/research", research_route, methods=["POST"]),
        Route("/dedup/scan", dedup_scan_route, methods=["POST"]),
        Route("/dedup/merge", dedup_merge_route, methods=["POST"]),
        Route("/entity/primary-label", entity_primary_label_route, methods=["POST"]),
        Route("/resolution/rollback", rollback_resolution_route, methods=["POST"]),
        Route("/resolution/decisions", resolution_decisions_route, methods=["GET"]),
        Route("/share", create_share_route, methods=["POST"]),
        Route("/p", shared_doc_page, methods=["GET"]),
        Route("/p/presentation", presentation_page, methods=["GET", "HEAD"]),
        Route("/support/bundle", create_support_bundle_route, methods=["POST"]),
        Route("/support/bundle", download_support_bundle_route, methods=["GET"]),
        Route("/graph/bundle", create_graph_bundle_route, methods=["POST"]),
        Route("/graph/bundle", download_graph_bundle_route, methods=["GET"]),
        Route("/reference", docs_ui_route, methods=["GET"]),
        Route("/openapi.yaml", openapi_yaml_route, methods=["GET"]),
        Route("/.well-known/oauth-protected-resource", handle_protected_resource_metadata, methods=["GET", "HEAD"]),
        Route("/.well-known/oauth-authorization-server", handle_authorization_server_metadata, methods=["GET", "HEAD"]),
        Route("/oauth/register", handle_register, methods=["POST"]),
        Route("/oauth/authorize", handle_authorize, methods=["GET", "POST"]),
        Route("/oauth/authorize/telegram-push", handle_telegram_push, methods=["POST"]),
        Route("/oauth/authorize/poll", handle_authorize_poll, methods=["GET"]),
        Route("/oauth/token", handle_token, methods=["POST"]),
        Route("/mcp", mcp_route, methods=["GET", "POST"]),
        *account_routes.routes,
        Mount("/static", StaticFiles(directory=str(static_dir), check_dir=False), name="static"),
    ]

    app = Starlette(
        debug=False,
        routes=routes,
        # Starlette의 ServerErrorMiddleware 안쪽에서 endpoint 예외를 먼저 정제한다.
        middleware=[Middleware(ErrorBoundaryMiddleware)],
        exception_handlers={HTTPException: _http_error, policy.ProviderPolicyError: _policy_error},
        lifespan=app_lifespan,
    )
    app.state.runtime_config = WebRuntimeConfig.from_settings(s)
    app.state.settings = s

    def _add_static(prefix: str, path: str | Path, name: str = "static") -> None:
        p = Path(path)
        p.mkdir(parents=True, exist_ok=True)
        norm_prefix = "/" + prefix.strip("/")
        for r in app.router.routes:
            if isinstance(r, Mount) and r.path == norm_prefix:
                return
        app.router.mount(norm_prefix, StaticFiles(directory=str(p), check_dir=False), name=name)

    app.router.add_static = _add_static  # type: ignore[attr-defined]
    app.router.add_static("/static/", path=str(static_dir), name="static")

    namespace_directory = SqliteNamespaceDirectory(s.db_file)

    def _legacy_theme_location(legacy: LegacyHost) -> str | None:
        """레거시 호스트의 정본 테마 경로(§5.3). 테마가 없거나 번호가 아직 없으면 None(421)."""
        try:
            theme = theme_mgr.get_theme(legacy.theme_id)
        except ThemeNotFound:
            return None
        if theme.id != legacy.theme_id or theme.number is None:
            return None
        mode = namespace_directory.mode()
        if mode == "single":
            return theme_base_path(mode, None, theme.number)
        try:
            owner = namespace_directory.account(theme.account_id)
        except accounts_store.AccountNotFound:
            return None
        if owner.status != "active" or not owner.username:
            return None
        return theme_base_path(mode, owner.username, theme.number)

    legacy_hosts = SqliteLegacyHostDirectory(s.db_file, theme_location=_legacy_theme_location)
    # GateMiddleware는 없앴다(§5.1 Rev. 2.3). 정적 자산도 HostRouter와 보안 헤더 안쪽을 지난다.
    return wrap_web_app(
        app,
        s,
        theme_manager=theme_mgr,
        legacy_hosts=legacy_hosts,
        namespace_directory=namespace_directory,
    )


def run_api() -> int:
    s = get_settings()
    try:
        import uvicorn
    except Exception as exc:  # noqa: BLE001
        print(f"Uvicorn 미설치: {exc}\n  uv sync 후 다시 시도하세요.")
        return 2

    try:
        conn = dbm.connect(s.db_file)
        try:
            dbm.init_db(conn)
        finally:
            conn.close()

        token_store = get_token_store(data_dir=s.data_dir)
        token_store.migrate_env_tokens(
            getattr(s, "inject_token", ""),
            getattr(s, "readonly_token", ""),
            getattr(s, "collaborator_token", ""),
        )
        if not getattr(s, "inject_token", ""):
            owner_tok = token_store.ensure_default_owner_token(fallback_token=getattr(s, "inject_token", ""))
            try:
                s.inject_token = owner_tok["token"]
            except Exception:
                try:
                    object.__setattr__(s, "inject_token", owner_tok["token"])
                except Exception:
                    pass
        runtime = WebRuntimeConfig.from_settings(s)
        app = create_app(s)
    except ValueError as exc:
        print(f"웹 서비스 설정 오류: {exc}")
        return 2

    logging.basicConfig(level=logging.INFO)
    install_log_scrubber()
    try:
        from logging.handlers import RotatingFileHandler
        logs_dir = Path(getattr(s, "data_dir", "data")) / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        api_log_file = logs_dir / "api.log"
        root_logger = logging.getLogger()
        has_file = any(
            isinstance(h, RotatingFileHandler) and getattr(h, "baseFilename", "") == str(api_log_file)
            for h in root_logger.handlers
        )
        if not has_file:
            fh = RotatingFileHandler(str(api_log_file), maxBytes=10 * 1024 * 1024, backupCount=3, encoding="utf-8")
            fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] [%(name)s] %(message)s"))
            root_logger.addHandler(fh)
    except Exception:
        pass

    from .dev_listeners import dev_listeners

    listeners = dev_listeners(s)
    listen = ", ".join(f"{host}:{port}" for host, port in listeners)
    print(
        "Claire ASGI 웹 서비스 시작: "
        f"{runtime.public_origin} "
        f"(listen={listen}, env={runtime.environment})",
        flush=True,
    )
    uvicorn_options: dict[str, Any] = {
        "workers": 1,
        "access_log": False,
        "proxy_headers": False,
        "server_header": False,
        "limit_concurrency": 64,
        "timeout_keep_alive": 5,
    }
    if len(listeners) == 1:
        uvicorn.run(app, host=s.inject_host, port=s.inject_port, **uvicorn_options)
        return 0

    # 개발 환경의 역할 호스트 포트(§5.1, O16). 한 Server가 리스너 소켓 여럿을 함께 연다.
    # Server를 리스너마다 두면 lifespan(MCP 세션 관리자 등)이 여러 번 돌고 신호 처리기가
    # 서로 덮이므로, 같은 app·같은 이벤트 루프·같은 신호 처리를 쓰도록 소켓만 늘린다.
    import socket

    sockets: list[socket.socket] = []
    try:
        for host, port in listeners:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sockets.append(sock)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((host, port))
            except OSError as exc:
                print(f"listen 실패 {host}:{port}: {exc}")
                return 1
            sock.set_inheritable(True)
        config = uvicorn.Config(app, host=s.inject_host, port=s.inject_port, **uvicorn_options)
        server = uvicorn.Server(config)
        for host, port in listeners:
            logging.getLogger("uvicorn.error").info("Uvicorn running on http://%s:%d", host, port)
        try:
            server.run(sockets=sockets)
        except KeyboardInterrupt:
            pass
    finally:
        for sock in sockets:
            sock.close()
    return 0 if server.started else 1
