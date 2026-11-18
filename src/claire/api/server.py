"""Claire HTTP API와 graph UI를 제공하는 ASGI 애플리케이션.

Starlette는 라우팅/응답 계층만 담당하고 Uvicorn은 단일 worker로 실행한다. 외부 Reverse Proxy는 TLS와 public hostname을 담당하지만, 애플리케이션은 forwarded header를 신뢰하지 않고 설정된 public URL의 authority를 직접 검증한다.
"""

from __future__ import annotations

import asyncio
import contextvars
import dataclasses
import hashlib
import html
import json
import logging
import re
import sqlite3
import string
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import mimetypes
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode, urlsplit

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
from ..i18n import get_locale
from ..i18n import t as _t
from ..ingest.pipeline import IngestReport
from ..ingest.report_json import report_to_dict
from ..ingest.service import IngestService, IngestServicePool
from ..secret_scrub import install_log_scrubber, scrub_exception, scrub_text
from .. import contributions
from .. import theme_judgement
from .. import reputation
from .. import auto_contribution
from .. import kb_access
from ..store import accounts as accounts_store
from ..store import auth_db
from ..store import kb_collab
from ..store import themes as themes_store
from ..store import federation as federation_store
from ..knowledge_federation import known_servers as kf_known_servers
from ..knowledge_federation import remote_kbs
from ..knowledge_federation import grants as kf_grants
from ..knowledge_federation import offers as kf_offers
from ..store import db as dbm
from ..store.public_index import SqlitePublicIndexStore
from ..store.queries import theme_summary
from ..store.legacy_hosts import SqliteLegacyHostDirectory
from ..store.knowledge_base import KnowledgeBaseInfo, KnowledgeBaseManager, KnowledgeBaseNotFound, get_kb_manager
from ..store.token_store import get_token_store
from .accounts_routes import build_account_routes, prepare_auth_db, prepare_client_db
from .accounts_routes.common import same_origin
from . import public_kb_routes
from .host_router import LegacyHost
from . import collections
from .namespace import SqliteNamespaceDirectory, _public_origin, kb_base_path, namespace_link, request_namespace
from .principal import request_principal
from ..ontology.label_language import display_label
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
from ..presentation.favicon import inject_favicon_head_tags
from .security import (
    ErrorBoundaryMiddleware,
    WebRuntimeConfig,
    lookup_route_rule,
    read_json_body,
    read_session_cookie,
    request_auth_scope,
    request_id,
    step_up_response,
    wrap_web_app,
)

log = logging.getLogger("claire.api")
# The account of the requester in a shared knowledge base, for the provider context of the request (set by ``_get_theme_ctx``).
_kb_acting_account: contextvars.ContextVar[int | None] = contextvars.ContextVar("claire_kb_acting_account", default=None)
# 계정 자동 백업 주기 검사(초). 백업 주기 자체는 계정 정책(매일·매주)이 정한다.
ACCOUNT_BACKUP_SCHEDULER_START_DELAY = 60.0
ACCOUNT_BACKUP_SCHEDULER_INTERVAL = 600.0
_SW_JS_CONTENT: str = (
    "// Claire PWA Network-Passthrough Service Worker\n"
    "self.addEventListener('install', (event) => {\n"
    "  self.skipWaiting();\n"
    "});\n"
    "\n"
    "self.addEventListener('activate', (event) => {\n"
    "  event.waitUntil(self.clients.claim());\n"
    "});\n"
    "\n"
    "self.addEventListener('fetch', (event) => {\n"
    "  event.respondWith(fetch(event.request));\n"
    "});\n"
)
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

# D4: 옛 /syndication/preferences?tab= → /settings/* 대응표 (설계 §2.2). 지식 베이스별 화면이라 ?kb=를 넘긴다.
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


def _entity_language_kwargs(request: Request) -> dict[str, Any]:
    """Keyword for the query functions that select entity labels: the entity language of the account of the request. Empty when the account has none (and for an anonymous visitor), thus those calls do not change."""
    language = request_principal(request).entity_language
    return {"entity_language": language} if language else {}


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
    # client.db (DATA_STORE_LAYOUT_DESIGN.md, RD27): the communication data of accounts. init_db above already moved the feed tables of version 21 into it.
    prepare_client_db(s.db_file)

    token_store = get_token_store(data_dir=s.data_dir)
    token_store.migrate_env_tokens(
        getattr(s, "inject_token", ""),
        getattr(s, "readonly_token", ""),
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
    theme_mgr = KnowledgeBaseManager(s)
    service_pool = IngestServicePool(s, svc, theme_manager=theme_mgr)
    namespace_directory = SqliteNamespaceDirectory(s.db_file)
    active_expensive_jobs = 0
    active_anonymous_search_jobs = 0

    def _kb_actor(request: Request, kb: KnowledgeBaseInfo) -> kb_access.Actor:
        """The requester for the capability matrix (design Sections 6.2 and 6.6)."""
        principal = request_principal(request)
        membership: tuple[str, str] | None = None
        if kb.is_shared and principal.account_id is not None:
            conn = dbm.connect_existing(s.db_file, readonly=True)
            try:
                row = kb_collab.get_membership(conn, kb.id, principal.account_id)
            finally:
                conn.close()
            if row is not None:
                membership = (str(row["role"]), str(row["state"]))
        scope = principal.scope if principal.scope in ("owner", "write", "readonly") else None
        return kb_access.Actor(
            account_id=principal.account_id if scope is not None else None,
            scope=scope,
            is_admin=principal.server_role == "admin",
            membership=membership,
        )

    def _kb_decide(request: Request, kb: KnowledgeBaseInfo, capability: str) -> str:
        """One function decides each capability (design Section 6.9 rule 1). ``capability`` is a route capability (Section 6.2 rule 5)."""
        if capability == kb_access.NO_KB:
            raise RuntimeError("the route declares no knowledge base capability")
        if kb.is_personal:
            capability = kb_access.personal_capability(capability)
        elif capability == "write":
            # A write into the content is for the personal knowledge base only (CT4). Shared knowledge bases are public; contributions come with unit CT-4.
            return "forbidden"
        return kb_access.decide(
            is_personal=kb.is_personal,
            owner_account_id=kb.account_id,
            is_public=kb.is_public,
            capability=capability,
            actor=_kb_actor(request, kb),
            single_mode=request_namespace(request).mode == "single",
            archived=kb.is_shared and kb.state == "archived",
        )

    def _require_kb(request: Request, kb: KnowledgeBaseInfo, capability: str) -> None:
        decision = _kb_decide(request, kb, capability)
        if decision == "not_found":
            raise HTTPException(status_code=404, detail="theme not found")
        if decision == "forbidden":
            if kb.is_shared and capability == "write":
                detail = "kb_ingest_by_contribution"
            elif kb.is_shared and kb.state == "archived" and capability in kb_access.ARCHIVED_BLOCKED:
                detail = "kb_archived"
            else:
                detail = "kb_capability_required"
            raise HTTPException(status_code=403, detail=detail)

    def _request_capability(request: Request) -> str:
        """The capability that the route of ``request`` declares in ``ROUTE_POLICY`` (design Section 6.2 rule 5)."""
        rule = lookup_route_rule(request.method, str(request.scope.get("path") or request.url.path))
        if rule is None:
            raise RuntimeError("request without a route rule")
        return rule.capability

    def _sees_hidden(request: Request, kb: KnowledgeBaseInfo) -> bool:
        """Hidden documents: in a personal knowledge base only for its account; in a shared knowledge base only for reviewers (design Section 6.2 ``review``)."""
        if kb.is_shared:
            return _kb_decide(request, kb, "review") == "allow"
        principal = request_principal(request)
        return principal.account_id is not None and principal.account_id == kb.account_id and request_auth_scope(request) != "anonymous"

    def _hides_sources(request: Request, kb: KnowledgeBaseInfo) -> bool:
        """True when the requester is not the account of a personal knowledge base: then the account setting ``personal_source_public`` decides about the sources (schema v37, ``queries.documents_with_hidden_source``). The sources of a shared knowledge base are public."""
        if not kb.is_personal or kb.account_id is None:
            return False
        principal = request_principal(request)
        return not (principal.account_id is not None and principal.account_id == kb.account_id and request_auth_scope(request) != "anonymous")

    def _apply_source_rule(conn: sqlite3.Connection, kb: KnowledgeBaseInfo, documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Remove the source of each document whose source the requester must not see (``_hides_sources`` is true for the caller)."""
        from ..store.queries import documents_with_hidden_source, hide_document_source

        hidden = documents_with_hidden_source(conn, account_id=int(kb.account_id or 0), kb_id=kb.id, document_ids=[str(d.get("id") or "") for d in documents])
        return [hide_document_source(d) if str(d.get("id") or "") in hidden else d for d in documents]

    def _theme_accessible(request: Request, theme: KnowledgeBaseInfo) -> bool:
        return _kb_decide(request, theme, _request_capability(request)) == "allow"

    def _visible_active_jobs(request: Request, data_dir: Path) -> list[dict[str, Any]]:
        """요청자가 접근할 수 있는 테마의 작업만 돌려준다 (단일 DB 이후 작업 디렉터리는 모든 테마 공용)."""
        from ..ingest.active import get_active_jobs

        jobs = get_active_jobs(data_dir)
        if not getattr(s, "multi_theme", False):
            return jobs
        visible = []
        for job in jobs:
            try:
                job_theme = theme_mgr.get_kb(int(job.get("kb_id") or 0))
            except Exception:  # noqa: BLE001
                continue
            if _theme_accessible(request, job_theme):
                visible.append(job)
        return visible

    def _job_kb_id(request: Request, theme: KnowledgeBaseInfo) -> int:
        """작업 조회 대상 지식 베이스: 선택 규칙(``_get_theme_ctx``)이 정한 것이다."""
        return theme.id

    def _select_kb(request: Request) -> KnowledgeBaseInfo:
        """Select the knowledge base of the request and check the capability of the route (design Sections 5.2 and 6.9, CT23).

        1. A namespaced route: only the path selects. A pinned path selects that knowledge base; else the personal knowledge base of the account of the namespace.
        2. A route without a namespace (surface ``any`` or ``system``): the parameter ``kb`` (number) is required, else 400 ``kb_required``.
        The query ``theme``, the body ``theme``, and the header ``X-Claire-Theme`` are unknown fields and are ignored.
        """
        try:
            ns = request_namespace(request)
        except Exception:
            ns = None
        rule = lookup_route_rule(request.method, str(request.scope.get("path") or request.url.path))
        surface = rule.surface if rule is not None else "namespaced"
        try:
            if surface in ("any", "system"):
                raw = request.query_params.get("kb")
                if raw is None or not str(raw).strip():
                    raise HTTPException(status_code=400, detail="kb_required")
                theme = theme_mgr.get_kb_by_reference(raw)
            elif ns is not None and ns.pinned_kb_id is not None:
                theme = theme_mgr.get_kb(ns.pinned_kb_id)
            else:
                if ns is None or ns.account_id is None:
                    raise HTTPException(status_code=404, detail="theme not found")
                theme = theme_mgr.personal_kb_for(ns.account_id)
            if not getattr(s, "multi_theme", False) and theme.id != 0:
                raise KnowledgeBaseNotFound(theme.id)
        except KnowledgeBaseNotFound as exc:
            # 없는 지식 베이스를 다른 것으로 바꾸지 않는다. 접근 불가와 같은 응답이라 비공개 지식 베이스의 존재가 드러나지 않는다.
            raise HTTPException(status_code=404, detail="theme not found") from exc
        if theme.is_shared:
            _require_kb(request, theme, _request_capability(request))
        elif not _theme_accessible(request, theme):
            raise HTTPException(status_code=404, detail="theme not found")
        return theme

    def _get_theme_ctx(
        request: Request, body: dict[str, Any] | None = None
    ) -> tuple[KnowledgeBaseInfo, Settings, IngestService]:
        """The knowledge base of the request (``_select_kb``), its settings, and its ingest service. ``body`` is not read: no body field selects a knowledge base."""
        del body
        theme = _select_kb(request)
        try:
            theme_settings = theme_mgr.get_settings_for_kb(theme.id, s)
            if theme.is_shared:
                # Design Section 13.1 rule 5: work in a shared knowledge base runs with the provider context of the requester, never with the one of the server.
                acting = request_principal(request).account_id
                _kb_acting_account.set(acting)
                if acting is not None and hasattr(theme_settings, "model_copy"):
                    theme_settings = theme_settings.model_copy(update={"account_id": acting})
                theme_svc = service_pool.get_service(theme.id, acting_account_id=acting)
            else:
                theme_svc = service_pool.get_service(theme.id)
        except KnowledgeBaseNotFound as exc:
            raise HTTPException(status_code=404, detail="theme not found") from exc
        return theme, theme_settings, theme_svc

    def _get_personal_kb_ctx(request: Request) -> tuple[KnowledgeBaseInfo, Settings, IngestService]:
        """Ingest writes only to the personal knowledge base of the account of the credential (design Section 5.2 rule 3, CT4). The route takes no knowledge base reference; a path that pins another knowledge base gets 403."""
        principal = request_principal(request)
        if principal.account_id is None:
            raise HTTPException(status_code=401, detail="authentication required")
        try:
            ns = request_namespace(request)
        except Exception:
            ns = None
        if ns is not None and ns.account_id is not None and ns.account_id != principal.account_id:
            # A write in the namespace of another account stays hidden (404), as before.
            raise HTTPException(status_code=404, detail="theme not found")
        try:
            theme = theme_mgr.personal_kb_for(principal.account_id)
            if ns is not None and ns.pinned_kb_id is not None and ns.pinned_kb_id != theme.id:
                raise HTTPException(status_code=403, detail="kb_ingest_by_contribution")
            theme_settings = theme_mgr.get_settings_for_kb(theme.id, s)
            theme_svc = service_pool.get_service(theme.id)
        except KnowledgeBaseNotFound as exc:
            raise HTTPException(status_code=404, detail="theme not found") from exc
        return theme, theme_settings, theme_svc

    def _validate_request_effort(
        theme_svc: IngestService, theme_settings: Settings, effort: str | None
    ) -> str | None:
        """Validate explicit ingest/reingest/regenerate effort without fallback."""
        if not effort:
            return None
        try:
            return policy.validate_ingest_effort(
                getattr(theme_svc, "s", theme_settings),
                effort,
                ctx=getattr(theme_svc, "provider_context", None),
                allow_mock_standard=True,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

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

    def _provider_account_of(theme: KnowledgeBaseInfo) -> int | None:
        """The account whose provider context pays the work: the owner of a personal knowledge base; in a shared knowledge base the requester (design Section 13.1 rule 5). A shared knowledge base without a requester account is refused."""
        if not theme.is_shared:
            return getattr(theme, "account_id", None)
        acting = _kb_acting_account.get()
        if acting is None:
            raise policy.ProviderNotAllowed("shared knowledge base: no requester account", remedy="Sign in to use this function.")
        return acting

    def _theme_provider_context(theme: KnowledgeBaseInfo, theme_svc: Any | None = None) -> policy.ProviderContext | None:
        """테마 작업의 프로바이더 컨텍스트. 서비스가 붙인 컨텍스트(L4-a2i)를 먼저 쓰고, 없으면 테마 소유 계정으로 정한다."""
        ctx = getattr(theme_svc, "provider_context", None) if theme_svc is not None else None
        if isinstance(ctx, policy.ProviderContext):
            return ctx
        account_id = _provider_account_of(theme)
        if _account_limits_context(account_id) is None:
            return None
        return policy.resolve_provider_context(account_id, settings=s)

    def _require_theme_feature_sync(theme: KnowledgeBaseInfo, theme_svc: Any | None, feature: str) -> None:
        if feature in policy.ADMIN_ONLY_FEATURES:
            # 관리자 전용 기능은 키 상태를 보기 전에 거부한다(멤버 키 복호화 불필요).
            ctx = getattr(theme_svc, "provider_context", None) if theme_svc is not None else None
            if isinstance(ctx, policy.ProviderContext):
                policy.require_feature(ctx, feature)
                return
            policy.require_feature(_account_limits_context(_provider_account_of(theme)), feature)
            return
        policy.require_feature(_theme_provider_context(theme, theme_svc), feature)

    async def _require_theme_feature(theme: KnowledgeBaseInfo, theme_svc: Any | None, feature: str) -> None:
        """멤버 테마에서 기능이 허용되지 않으면 ``ProviderNotAllowed``(→ 403). 관리자 테마는 그대로 통과한다."""
        await asyncio.to_thread(_require_theme_feature_sync, theme, theme_svc, feature)

    def _reembed_required(theme: KnowledgeBaseInfo) -> bool:
        """테마의 고정 임베딩 공간과 지금 소유 계정 컨텍스트의 임베딩이 다른가(§10.4). 판정할 수 없으면 False."""
        from types import SimpleNamespace

        from ..store.knowledge_base import embed_mismatch
        from ..store.vectors import resolve_embed_identity

        try:
            # The embeddings of a shared knowledge base use the provider of the server (CT24).
            ctx = None if theme.is_shared else _theme_provider_context(theme)
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
                "SELECT embed_provider, embed_model FROM knowledge_bases WHERE id=?", (theme.id,)
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
        if scope not in {"owner", "write", "readonly", "anonymous"}:
            raise HTTPException(status_code=401, detail="authentication required")
        # §9.1 O10: 주체 범위(호환), 서버 모드, 네임스페이스와의 관계. USERNAME은 싣지 않는다(S9).
        namespace = request_namespace(request)
        principal = request_principal(request)
        if principal.account_id is None or principal.account_id != namespace.account_id:
            relation = "visitor"
        elif principal.scope == "owner":
            relation = "owner"
        else:
            relation = "delegate"
        is_admin = bool(principal.server_role == "admin" and principal.scope == "owner" and principal.account_id is not None)
        return JSONResponse(
            {"scope": scope, "server": {"mode": namespace.mode}, "relation": relation, "is_admin": is_admin}
        )

    async def stats(request: Request) -> JSONResponse:
        theme, theme_settings, theme_svc = _get_theme_ctx(request)
        include_hidden = _sees_hidden(request, theme)

        def _counts() -> dict[str, Any]:
            conn = dbm.connect_existing(theme_settings.db_file, readonly=True)
            try:
                c = dict(dbm.counts(conn, include_hidden=include_hidden, kb_id=theme.id))
                c["kb_id"] = theme.id
                c["theme_label"] = theme.label
                return c
            finally:
                conn.close()

        res = await asyncio.to_thread(_counts)
        if request_auth_scope(request) in ("owner", "write"):
            res["ingest_effort"] = policy.describe_ingest_effort_options(
                getattr(theme_svc, "s", theme_settings),
                ctx=getattr(theme_svc, "provider_context", None),
                provider_error=getattr(theme_svc, "provider_error", None),
            )
        from ..ingest.active import get_primary_active_job

        jobs = _visible_active_jobs(request, theme_settings.data_dir)
        primary = get_primary_active_job(
            theme_settings.data_dir, kb_id=_job_kb_id(request, theme), jobs=jobs
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
            theme_settings.data_dir, kb_id=_job_kb_id(request, theme), jobs=jobs
        )
        res = dict(primary) if primary else {"active": False}
        res["active_jobs"] = jobs
        return JSONResponse(res)

    def _collection_filter(request: Request, name: str, allowed: tuple[str, ...]) -> str | None:
        """A filter of a collection (design Section 7). An unknown value gets 400; the filter is not guessed."""
        raw = request.query_params.get(name)
        if raw is None:
            return None
        if raw not in allowed:
            raise HTTPException(status_code=400, detail=f"invalid_{name}")
        return raw

    # Fields of ``KnowledgeBaseInfo.to_dict`` that are internal: the file path of the database, internal account IDs, and settings of the operator. Only a requester with the capability ``settings`` gets them.
    _KB_INTERNAL_FIELDS = ("db_path", "seq", "account_id", "created_by_account_id", "visibility_epoch", "fqdn", "ga_measurement_id", "default_focus")

    def _kb_public_dict(kb: KnowledgeBaseInfo, *, settings_view: bool) -> dict[str, Any]:
        item = kb.to_dict()
        if not settings_view:
            for name in _KB_INTERNAL_FIELDS:
                item.pop(name, None)
        return item

    def _kb_collection_item(request: Request, kb: KnowledgeBaseInfo, *, role: str | None, origin: str, membership_state: str | None) -> dict[str, Any]:
        """One knowledge base of a collection. The settings view (legacy hosts, re-embed state) only goes to a requester with the capability ``settings``.

        ``membership_state``: the state of the membership of the requester in the knowledge base (``active``, ``pending``, ``banned``), or None (no membership, an anonymous requester, a personal knowledge base).
        """
        is_settings_view = _kb_decide(request, kb, "settings") == "allow"
        include_hidden = (request_principal(request).account_id == kb.account_id) if kb.is_personal else _kb_decide(request, kb, "review") == "allow"
        t_stats = {"documents": 0, "entities": 0, "relations": 0}
        abs_db = theme_mgr.get_settings_for_kb(kb.id, s).db_file
        if abs_db.is_file():
            conn = dbm.connect_existing(abs_db, readonly=True)
            try:
                t_stats = theme_summary(conn, include_hidden=include_hidden, kb_id=kb.id)
            finally:
                conn.close()
        item = _kb_public_dict(kb, settings_view=is_settings_view)
        eff_sorcerer = (kb.sorcerer or "").strip() or getattr(s, "effective_sorcerer", getattr(s, "sorcerer", "owner"))
        item["sorcerer"] = (kb.sorcerer or "").strip()
        item["effective_sorcerer"] = eff_sorcerer
        item["knowledge_manager"] = eff_sorcerer
        item["stats"] = t_stats
        item["url"] = f"{origin}{kb_base_path(kb.number)}" if (kb.is_shared and kb.number is not None) else None
        if role is not None:
            item["role"] = role
        item["membership_state"] = membership_state
        if is_settings_view:
            item["legacy_hosts"] = [
                {"host": h.host, "activated_at": h.activated_at, "sunset_at": h.sunset_at}
                for h in SqliteLegacyHostDirectory(s.db_file).list_hosts_for_theme(kb.id)
            ]
            item["reembed_required"] = _reembed_required(kb)
            if kb.is_shared:
                # A shared knowledge base without an active moderator has the admin as its only moderator (design Section 10.2). The admin console shows these first.
                conn = dbm.connect_existing(s.db_file, readonly=True)
                try:
                    item["active_moderators"] = len(kb_collab.active_moderators(conn, kb.id))
                finally:
                    conn.close()
        return item

    def _membership_states(account_id: int | None) -> dict[int, str]:
        """The state of each membership of the requester: ``{kb_id: state}``. An anonymous requester has none."""
        if account_id is None:
            return {}
        conn = dbm.connect_existing(s.db_file, readonly=True)
        try:
            return kb_collab.account_membership_states(conn, int(account_id))
        finally:
            conn.close()

    def _collection_response(request: Request, *, collection_url: str, items: list[dict[str, Any]], extra: dict[str, Any]) -> Response:
        """A collection with pages (namespace design Section 6.5). ``items`` are newest first and each has the string ``number``."""
        query = collections.parse_collection_query(request.query_params)
        anonymous = request_auth_scope(request) == "anonymous"
        if query.page:
            filtered = list(items)
            if query.max_id is not None:
                filtered = [i for i in filtered if int(i["number"]) < query.max_id]
            elif query.min_id is not None:
                filtered = sorted((i for i in filtered if int(i["number"]) > query.min_id), key=lambda i: int(i["number"]))[: query.limit]
                filtered.reverse()
            elif query.since_id is not None:
                filtered = [i for i in filtered if int(i["number"]) > query.since_id]
            page_items = filtered[: query.limit] if query.min_id is None else filtered
            data = collections.collection_page(
                collection_url=collection_url,
                query=query,
                items=page_items,
                item_number="number",
                is_first_page=query.max_id is None and query.min_id is None and query.since_id is None,
            )
        else:
            data = collections.collection_root(collection_url=collection_url, total_items=len(items))
            data.update(extra)
        etag = collections.collection_etag([json.dumps(data, sort_keys=True, default=str)])
        hdrs = collections.collection_cache_headers(anonymous=anonymous, etag=etag)
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers=hdrs)
        return JSONResponse(data, headers=hdrs)

    async def kb_collection_route(request: Request) -> Response:
        """``/kb`` and ``/@{u}/kb`` (design Section 7, CT9).

        - ``/kb``: the shared knowledge bases of the server. ``?joined=true``: only those where the requester is an active member. ``personal_kb``: the personal knowledge base of the requester.
        - ``/@{u}/kb``: the shared knowledge bases where ``u`` is an active member. ``personal_kb``: the personal knowledge base of ``u`` when the requester can read it.
        - ``?knowledge_type=real`` or ``?knowledge_type=fictional`` selects one knowledge type.
        """
        ns = request_namespace(request)
        principal = request_principal(request)
        knowledge_type = _collection_filter(request, "knowledge_type", ("real", "fictional"))
        joined = _collection_filter(request, "joined", ("true", "false")) == "true"
        origin = _public_origin(request)
        # The roles shown are those of the requester at the root, and those of ``u`` under ``/@{u}/``.
        role_account = principal.account_id if ns.account_id is None else ns.account_id
        personal_account = role_account
        if ns.account_id is None:
            collection_url = f"{origin}/kb"
            if collections.wants_html(request):
                return await asyncio.to_thread(_public_page, request, view="kb_list", title=_t("kb.pub.kbs_title"))
        else:
            collection_url = f"{origin}{ns.prefix}/kb"
            if collections.wants_html(request):
                from ..graphview import render_graph_html

                return HTMLResponse(
                    render_graph_html(
                        s,
                        include_private=principal.account_id == ns.account_id and principal.scope in ("owner", "readonly"),
                        theme=None,
                        host=request.headers.get("host"),
                        request=request,
                        account_id=ns.account_id,
                    )
                )

        def _build() -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
            # ``CLAIRE_MULTI_THEME=0``: only the knowledge base 0 is served, so the collection has no shared knowledge base.
            shared = theme_mgr.list_shared_kbs(knowledge_type=knowledge_type) if getattr(s, "multi_theme", False) else []
            roles: dict[int, str] = {}
            if role_account is not None:
                conn = dbm.connect_existing(s.db_file, readonly=True)
                try:
                    roles = kb_collab.account_memberships(conn, role_account, state="active")
                finally:
                    conn.close()
            if ns.account_id is not None or joined:
                shared = [t for t in shared if t.id in roles]
            # ``membership_state`` is always that of the requester, also under ``/@{u}/kb`` where ``role`` is that of ``u``.
            states = _membership_states(principal.account_id)
            items = [_kb_collection_item(request, t, role=roles.get(t.id), origin=origin, membership_state=states.get(t.id)) for t in shared]
            personal: dict[str, Any] | None = None
            if personal_account is not None:
                try:
                    kb = theme_mgr.personal_kb_for(personal_account)
                except KnowledgeBaseNotFound:
                    kb = None
                if kb is not None and _kb_decide(request, kb, "read") == "allow":
                    personal = _kb_collection_item(request, kb, role=None, origin=origin, membership_state=None)
            return items, personal

        items, personal = await asyncio.to_thread(_build)
        return _collection_response(
            request,
            collection_url=collection_url,
            items=items,
            extra={"knowledge_bases": items, "personal_kb": personal, "multi_theme": bool(getattr(s, "multi_theme", False))},
        )

    async def themes_collection_route(request: Request) -> Response:
        """``/themes``: the collection of themes (design Section 7). The old ``/@{u}/themes`` gets 308 to ``/@{u}/kb``."""
        ns = request_namespace(request)
        if ns.account_id is not None:
            location = f"{ns.prefix}/kb"
            if request.url.query:
                location += f"?{request.url.query}"
            return Response(status_code=308, headers={"Location": location})
        knowledge_type = _collection_filter(request, "knowledge_type", ("real", "fictional"))
        origin = _public_origin(request)
        if collections.wants_html(request):
            return await asyncio.to_thread(_public_page, request, view="themes_list", title=_t("kb.pub.themes_title"))

        def _build() -> list[dict[str, Any]]:
            conn = dbm.connect_existing(s.db_file, readonly=True)
            try:
                rows = themes_store.list_themes(conn, knowledge_type=knowledge_type)
                remote_ids = {kb.id for kb in remote_kbs.list_all(conn)}
                out = []
                for row in rows:
                    number = ids.format_public_id(int(row["number"]))
                    out.append({
                        "number": number,
                        "url": f"{origin}/themes/{number}",
                        "label": row["label"],
                        "description": row["description"],
                        "knowledge_type": row["knowledge_type"],
                        "state": row["state"],
                        "knowledge_bases": len(themes_store.theme_members(conn, int(row["id"]))),
                        # KNOWLEDGE_FEDERATION_DESIGN.md Section 8.2: the shared knowledge bases of other servers in this theme.
                        "remote_knowledge_bases": sum(1 for m in themes_store.remote_members(conn, theme_id=int(row["id"])) if int(m["remote_kb_id"]) in remote_ids),
                    })
                return out
            finally:
                conn.close()

        items = await asyncio.to_thread(_build)
        return _collection_response(request, collection_url=f"{origin}/themes", items=items, extra={"themes": items})

    FEDERATION_RECENT_MAX = 20
    SIMILAR_MAX = 12
    SIMILAR_SEEDS_MAX = 20

    async def explore_federation_route(request: Request) -> Response:
        """``GET /explore/federation``: the data of the root page of network mode about other servers (KNOWLEDGE_FEDERATION_DESIGN.md Section 8.3 rules 1 to 3). The data is public.

        - ``themes``: the active themes, the one with the most recent activity first, with the counts of the shared knowledge bases of this server and of other servers.
        - ``servers``: the known servers that have shared knowledge bases in the store of remote knowledge bases, with the domain, the software, and the count. A blocked server is not in the list.
        - ``recent``: the recent activity across servers, the newest first (``knowledge_federation.recent``): ``remote_knowledge_base`` (a new shared knowledge base of another server), ``accepted_contribution`` (another server approved a contribution of an account of this server; the account is named only when its profile is public), and ``curation`` (a shared knowledge base of this server copied documents of another server). No item has a document.
        """
        from ..knowledge_federation import recent as kf_recent

        origin = _public_origin(request).rstrip("/")

        def _local_kb(kb_id: int) -> dict[str, Any] | None:
            try:
                kb = theme_mgr.get_kb(int(kb_id))
            except KnowledgeBaseNotFound:
                return None
            if not kb.is_shared or not kb.is_public or kb.number is None:
                return None
            return {"number": ids.format_public_id(kb.number), "label": kb.label, "url": f"{origin}{kb_base_path(kb.number)}"}

        def _build() -> dict[str, Any]:
            views = _active_theme_views(origin=origin)
            conn = dbm.connect_existing(s.db_file, readonly=True)
            try:
                remote = remote_kbs.list_all(conn)
                counts: dict[str, int] = {}
                for kb in remote:
                    counts[kb.domain] = counts.get(kb.domain, 0) + 1
                servers = [
                    {"domain": server.domain, "software_name": server.software_name, "software_version": server.software_version, "knowledge_bases": counts[server.domain]}
                    for server in kf_known_servers.list_servers(conn)
                    if server.domain in counts
                ]
                recent = kf_recent.recent_activity(conn, local_kb=_local_kb, domain_of=lambda iri: urlsplit(iri).hostname or None, limit=FEDERATION_RECENT_MAX)
            finally:
                conn.close()
            return {
                "themes": [{"number": v["number"], "url": v["url"], "label": v["label"], "knowledge_type": v["knowledge_type"], "local_knowledge_bases": v["shared_knowledge_bases"], "remote_knowledge_bases": v["remote_knowledge_bases"]} for v in views],
                "servers": servers,
                "recent": recent,
            }

        return JSONResponse(await asyncio.to_thread(_build), headers={"Cache-Control": "public, max-age=60"})

    async def kb_similar_route(request: Request) -> Response:
        """``GET /api/kb/similar?knowledge_type=&label=&seed=``: the shared knowledge bases of this server and of other servers that are near a subject, for the dialog that makes a shared knowledge base (KNOWLEDGE_FEDERATION_DESIGN.md Section 8.3 rule 4).

        ``type`` is necessary. ``label`` and each ``seed`` (a seed entity, at most 20) are the text that the account typed. With no text, the answer has the knowledge bases of the type, the ones of this server first, and ``matched`` is false. With text, it has the knowledge bases with a score above 0, the highest score first (``theme_judgement.subject_score``).
        """
        knowledge_type = _collection_filter(request, "knowledge_type", ("real", "fictional"))
        if knowledge_type is None:
            raise HTTPException(status_code=400, detail="knowledge_type_required")
        label = request.query_params.get("label", "")
        seeds = request.query_params.getlist("seed")
        if len(label) > 200 or len(seeds) > SIMILAR_SEEDS_MAX or any(len(seed) > 200 for seed in seeds):
            raise HTTPException(status_code=400, detail="invalid_request")
        origin = _public_origin(request).rstrip("/")

        def _build() -> dict[str, Any]:
            shared = theme_mgr.list_shared_kbs(knowledge_type=knowledge_type) if getattr(s, "multi_theme", False) else []
            conn = dbm.connect_existing(s.db_file, readonly=True)
            try:
                local_keys = {int(row["kb_id"]): theme_judgement.signature_keys(row) for row in themes_store.load_signatures(conn)}
                candidates: list[tuple[dict[str, Any], set[tuple[str, str]]]] = [
                    ({"remote": False, "server": None, "number": ids.format_public_id(kb.number), "label": kb.label, "description": kb.description, "knowledge_type": kb.knowledge_type, "url": f"{origin}{kb_base_path(kb.number)}"}, local_keys.get(kb.id, set()))
                    for kb in shared
                    if kb.number is not None
                ]
                for remote in remote_kbs.list_all(conn, knowledge_type=knowledge_type):
                    candidates.append(({"remote": True, "server": remote.domain, "number": None, "label": remote.label, "description": remote.description, "knowledge_type": remote.knowledge_type, "url": remote.url}, {(etype, norm) for etype, norm, _weight in remote_kbs.signature_rows(remote)}))
            finally:
                conn.close()
            scored = [(theme_judgement.subject_score(label=label, seed_names=seeds, candidate_label=item["label"], candidate_keys=keys), item) for item, keys in candidates]
            matched = bool(theme_judgement.subject_terms(label=label, seed_names=seeds))
            if matched:
                scored = sorted((entry for entry in scored if entry[0]), key=lambda entry: -float(entry[0] or 0.0))
            return {"matched": matched, "knowledge_bases": [{**item, "score": score} for score, item in scored[:SIMILAR_MAX]]}

        return JSONResponse(await asyncio.to_thread(_build), headers={"Cache-Control": "no-store"})

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
                    f'<a href="{html.escape(home_url)}">{html.escape(_t("explore.my_workspace"))}</a>'
                    f'<form method="post" action="/logout"><button type="submit" class="link">{html.escape(_t("common.action.logout"))}</button></form>'
                )
            else:
                nav_auth = f'<a href="/login">{html.escape(_t("explore.login"))}</a>'

            if not accounts:
                if account_id is not None:
                    btn_html = f'<a class="btn" href="{html.escape(f"/@{username}/" if username else "/me")}">{html.escape(_t("explore.go_workspace"))}</a>'
                else:
                    btn_html = f'<a class="btn" href="/login">{html.escape(_t("explore.login"))}</a>'
                content_html = (
                    '<section class="empty-state">'
                    f'<h2>{html.escape(_t("explore.empty_title"))}</h2>'
                    f'<p>{html.escape(_t("explore.empty"))}</p>'
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
                            f'<li><a href="{html.escape(t_url)}">{html.escape(t.label)}</a><span class="themes-count">({html.escape(_t("explore.theme_docs", count=t.documents))})</span></li>'
                        )
                    if t_items:
                        themes_html = f'<ul class="themes-list">{"".join(t_items)}</ul>'
                    else:
                        themes_html = f'<p class="subtitle" style="margin:.5rem 0 0;font-size:.88rem;">{html.escape(_t("explore.no_themes"))}</p>'

                    card = (
                        '<article class="account-card">'
                        '<div class="account-header">'
                        f'<a class="account-name" href="/@{html.escape(a.username)}/">{html.escape(a.display_name or a.username)}'
                        f'<span class="account-handle">@{html.escape(a.username)}</span></a>'
                        f'<span class="badge">{html.escape(_t("explore.badge", themes=a.public_themes, documents=a.public_documents))}</span>'
                        '</div>'
                        f'{themes_html}'
                        '</article>'
                    )
                    cards.append(card)
                content_html = "".join(cards)
            if force_html and not query.page:
                # Design Section 8.4: the root page of network mode shows the themes first, the one with the most recent activity first, with a link to the collection.
                theme_views = await asyncio.to_thread(lambda: _active_theme_views(origin=base_origin))
                # KNOWLEDGE_FEDERATION_DESIGN.md Section 8.3 rules 2 and 3: the federated servers and the recent activity across servers come after the themes. The page script fills this element from ``GET /explore/federation``.
                federation_host = '<div id="public-home-federation"></div>'
                if theme_views:
                    content_html = (
                        f'<h2>{html.escape(_t("themes.section_title"))} <a href="/themes" style="font-size:.85rem;font-weight:400;">{html.escape(_t("themes.all"))}</a></h2>'
                        + _theme_cards_html(theme_views)
                        + federation_host
                        + f'<h2>{html.escape(_t("themes.accounts_title"))}</h2>'
                        + content_html
                    )
                else:
                    content_html = federation_host + content_html

            pagination_parts = []
            if query.page:
                if len(accounts) == query.limit:
                    last_id = ids.format_public_id(accounts[-1].public_id)
                    pagination_parts.append(f'<a href="/explore/accounts?page=true&max_id={last_id}">{html.escape(_t("explore.next"))} &rarr;</a>')
                if query.max_id is not None and accounts:
                    first_id = ids.format_public_id(accounts[0].public_id)
                    pagination_parts.insert(0, f'<a href="/explore/accounts?page=true&min_id={first_id}">&larr; {html.escape(_t("explore.prev"))}</a>')
            pagination_html = f'<div class="pagination">{"".join(pagination_parts)}</div>' if pagination_parts else ""

            rendered = string.Template(template_text).substitute(
                title=html.escape(_t("explore.title")),
                html_lang=get_locale(),
                explore_head_html=public_kb_routes.explore_head_markup(request),
                nav_label=html.escape(_t("explore.nav_label")),
                subtitle=html.escape(_t("explore.subtitle")),
                nav_auth=nav_auth,
                content=content_html,
                pagination=pagination_html,
            )
            rendered = inject_favicon_head_tags(rendered)

            # The page text follows the interface language, thus the locale is a part of the entity tag.
            etag_parts = [get_locale(), hashlib.sha256(content_html.encode("utf-8")).hexdigest()] + [f"{a.public_id}:{a.public_themes}:{a.public_documents}" for a in accounts]
            etag = collections.collection_etag(etag_parts if etag_parts else ["empty"])
            headers = collections.collection_cache_headers(anonymous=(account_id is None), etag=etag)
            headers["Vary"] += ", Accept-Language"
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
                        "is_personal": t.is_personal,
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

    # Knowledge base lifecycle (SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md Section 10.1). The body field and its type of each setting. A field with another name is unknown and is ignored (CT23).
    _KB_TEXT_FIELDS = ("label", "description", "icon", "ga_measurement_id", "sorcerer")
    _KB_BOOL_FIELDS = ("is_public", "presentation_enabled")
    _KB_SHARED_FIELDS = ("join_policy", "knowledge_type", "state")

    def _kb_settings_from_body(body: dict[str, Any]) -> dict[str, Any]:
        """The settings of a request body, with the types checked. A wrong type gets 400 with the name of the field."""
        if "fqdn" in body and body["fqdn"]:
            raise HTTPException(status_code=400, detail="FQDN assignment is deprecated and no longer supported")
        values: dict[str, Any] = {}
        for name in (*_KB_TEXT_FIELDS, *_KB_SHARED_FIELDS):
            if name in body:
                if not isinstance(body[name], str):
                    raise HTTPException(status_code=400, detail=f"{name}_invalid")
                values[name] = body[name]
        for name in _KB_BOOL_FIELDS:
            if name in body:
                if not isinstance(body[name], bool):
                    raise HTTPException(status_code=400, detail=f"{name}_invalid")
                values[name] = body[name]
        if "seed_entities" in body:
            values["seed_entities"] = body["seed_entities"]
        return values

    def _require_multi_kb() -> None:
        if not getattr(s, "multi_theme", False):
            raise HTTPException(status_code=403, detail="멀티 테마 모드가 비활성화되어 있습니다 (CLAIRE_MULTI_THEME=1 필요)")

    async def kb_create_route(request: Request) -> JSONResponse:
        """``POST /kb``: each active account makes shared knowledge bases, without a limit (CT3). The founder selects the knowledge type (required, CT12), can give seed entities, and becomes the first moderator (CT8)."""
        _require_multi_kb()
        principal = request_principal(request)
        if principal.account_id is None:
            raise HTTPException(status_code=401, detail="authentication required")
        values = _kb_settings_from_body(await _json_object(request))
        label = str(values.get("label") or "").strip()
        if not label:
            raise HTTPException(status_code=400, detail="label is required")
        if values.get("knowledge_type") not in ("real", "fictional"):
            raise HTTPException(status_code=400, detail="knowledge_type_required")
        for name in ("join_policy", "state"):
            if name in values:
                # A new shared knowledge base is ``active`` with the join policy ``approval`` (design Section 10.1). The settings route changes them.
                raise HTTPException(status_code=400, detail=f"{name}_not_at_create")
        try:
            kb = await asyncio.to_thread(
                theme_mgr.define_kb,
                label,
                description=str(values.get("description") or "").strip(),
                icon=str(values.get("icon") or "📁").strip(),
                is_public=values.get("is_public", True),
                ga_measurement_id=str(values.get("ga_measurement_id") or "").strip(),
                sorcerer=str(values.get("sorcerer") or "").strip(),
                presentation_enabled=values.get("presentation_enabled", False),
                account_id=principal.account_id,
                knowledge_type=values["knowledge_type"],
                seed_entities=values.get("seed_entities"),
            )
        except ValueError as val_err:
            raise HTTPException(status_code=400, detail=str(val_err)) from val_err
        except Exception as exc:
            _log_operation_failure(request, "kb create", exc)
            raise HTTPException(status_code=500, detail="failed to make the knowledge base") from exc
        await asyncio.to_thread(_after_kb_founded, kb.id, int(principal.account_id))
        return JSONResponse({"ok": True, "knowledge_base": kb.to_dict()}, status_code=201)

    def _after_kb_founded(kb_id: int, founder_account_id: int) -> None:
        """A new shared knowledge base: each mutual collaborator of the founder becomes a collaborator at once (design Section 9.10 rule 2), then the automatic rules run (Section 9.9 rule 4: a new shared knowledge base with seed entities)."""
        conn = dbm.connect_existing(s.db_file)
        try:
            auto_contribution.grant_mutual_collaborators(conn, kb_id=kb_id, founder_account_id=founder_account_id)
            conn.commit()
        finally:
            conn.close()
        _auto_run_all()

    async def kb_settings_route(request: Request) -> JSONResponse:
        """``PATCH /kb/{number}/`` and ``PATCH /@{u}/`` (``PATCH /`` under the prefix): change the settings of the knowledge base that the path selects. A shared one needs the capability ``settings``; a personal one takes its account."""
        _require_multi_kb()
        values = _kb_settings_from_body(await _json_object(request))
        kb = _select_kb(request)
        principal = request_principal(request)
        if kb.is_shared and principal.server_role != "admin":
            # The capability ``settings`` of a shared knowledge base has label, description, icon, seed entities, join policy, knowledge type, and archive (design Section 6.2). The analytics ID, the manager name, and the presentation switch stay with the admin.
            refused = sorted(set(values) & {"ga_measurement_id", "sorcerer", "presentation_enabled"})
            if refused:
                raise HTTPException(status_code=403, detail=f"{refused[0]}_admin_only")
        try:
            updated = await asyncio.to_thread(theme_mgr.update_kb, kb.id, **values)
        except KeyError as k_err:
            raise HTTPException(status_code=404, detail="theme not found") from k_err
        except ValueError as v_err:
            raise HTTPException(status_code=400, detail=str(v_err)) from v_err
        except Exception as exc:
            _log_operation_failure(request, "kb settings", exc)
            raise HTTPException(status_code=500, detail="failed to change the knowledge base") from exc
        if updated.is_shared:
            def _log() -> None:
                conn = dbm.connect_existing(s.db_file)
                try:
                    kb_collab.log_moderation(conn, kb_id=updated.id, actor_account_id=principal.account_id, action="settings", target=",".join(sorted(values)))
                    conn.commit()
                finally:
                    conn.close()

            await asyncio.to_thread(_log)
            if "seed_entities" in values:
                await asyncio.to_thread(_auto_run_all)  # design Section 9.9 rule 4: a shared knowledge base gets seed entities
        return JSONResponse({"ok": True, "knowledge_base": updated.to_dict()})

    async def kb_delete_route(request: Request) -> JSONResponse:
        """``DELETE /kb/{number}/``: delete a shared knowledge base with its documents and graph (design Section 10.1: admin only, step-up). Without ``?apply=true`` the answer is the plan only (PRINCIPLES D3)."""
        _require_multi_kb()
        kb = _select_kb(request)
        raw_apply = request.query_params.get("apply")
        if raw_apply not in (None, "true", "false"):
            raise HTTPException(status_code=400, detail="invalid_apply")
        if kb.is_personal:
            raise HTTPException(status_code=400, detail="kb_personal_cannot_delete")

        def _plan() -> dict[str, int]:
            conn = dbm.connect_existing(s.db_file, readonly=True)
            try:
                plan = dict(theme_summary(conn, include_hidden=True, kb_id=kb.id))
                plan["members"] = conn.execute("SELECT COUNT(*) FROM kb_memberships WHERE kb_id=?", (kb.id,)).fetchone()[0]
                plan["contributions"] = conn.execute("SELECT COUNT(*) FROM kb_contributions WHERE kb_id=?", (kb.id,)).fetchone()[0]
                return plan
            finally:
                conn.close()

        plan = await asyncio.to_thread(_plan)
        if raw_apply != "true":
            return JSONResponse({"ok": True, "applied": False, "knowledge_base": kb.to_dict(), "would_delete": plan})
        try:
            deleted = await asyncio.to_thread(theme_mgr.delete_kb, kb.id, purge=True)
        except ValueError as v_err:
            raise HTTPException(status_code=400, detail=str(v_err)) from v_err
        except Exception as exc:
            _log_operation_failure(request, "kb delete", exc)
            raise HTTPException(status_code=500, detail="failed to delete the knowledge base") from exc
        if hasattr(service_pool, "remove_service"):
            service_pool.remove_service(deleted.id)
        principal = request_principal(request)
        if principal.account_id is not None:
            def _audit() -> None:
                auth_conn = auth_db.connect_auth_db(auth_db.auth_db_path(s.data_dir))
                try:
                    auth_db.record_admin_audit(auth_conn, actor_account_id=int(principal.account_id), action="kb_delete", detail={"kb_number": deleted.to_dict()["number"], "deleted": plan})
                finally:
                    auth_conn.close()

            await asyncio.to_thread(_audit)
        return JSONResponse({"ok": True, "applied": True, "deleted": deleted.to_dict(), "would_delete": plan})

    # --- Membership and moderation (SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md Section 10.2) ---

    _MEMBER_ACTIONS = ("approve", "ban", "remove")
    _MEMBER_ROLES = ("member", "collaborator")  # the role ``moderator`` has its own admin route
    _MAX_REASON_LENGTH = 500

    def _shared_kb_of_path(request: Request) -> KnowledgeBaseInfo:
        """The shared knowledge base that the path pins. A personal knowledge base has no members: 404."""
        kb = _select_kb(request)
        if not kb.is_shared:
            raise HTTPException(status_code=404, detail="theme not found")
        return kb

    def _member_view(row: dict[str, Any], *, mode: str) -> dict[str, Any]:
        """One member for a response. The account is its public number. The username goes only in network mode and for a public profile (S9, N1)."""
        view: dict[str, Any] = {
            "number": ids.format_public_id(int(row["membership_public_id"])),
            "account": ids.format_public_id(int(row["account_public_id"])),
            "display_name": row["display_name"],
            "role": row["role"],
            "state": row["state"],
            "requested_at": row["requested_at"],
            "decided_at": row["decided_at"],
        }
        if mode == "network" and row["profile_public"] and row["username"]:
            view["username"] = row["username"]
        return view

    def _member_row(conn: sqlite3.Connection, kb_id: int, account_id: int) -> dict[str, Any] | None:
        rows = [r for r in kb_collab.members_with_accounts(conn, kb_id, states=kb_collab.MEMBERSHIP_STATES) if r["account_id"] == account_id]
        return rows[0] if rows else None

    def _account_of_path(conn: sqlite3.Connection, request: Request) -> accounts_store.Account:
        """The account of the path parameter ``account`` (its public number). An account that is not there, or is not ``active``, gets 404."""
        try:
            public_id = ids.parse_public_id(str(request.path_params.get("account") or ""))
        except ValueError:
            raise HTTPException(status_code=404, detail="account_not_found") from None
        account = accounts_store.AccountStore(conn).find_by_public_id(public_id)
        if account is None or account.status != "active":
            raise HTTPException(status_code=404, detail="account_not_found")
        return account

    def _reason_of(body: dict[str, Any]) -> str:
        reason = body.get("reason", "")
        if not isinstance(reason, str) or len(reason) > _MAX_REASON_LENGTH:
            raise HTTPException(status_code=400, detail="reason_invalid")
        return reason.strip()

    async def kb_join_route(request: Request) -> JSONResponse:
        """``POST /kb/{number}/membership``: ``open`` makes an active member, ``approval`` a pending one. A banned account and an archived knowledge base get 403."""
        kb = _shared_kb_of_path(request)
        principal = request_principal(request)
        if kb.state == "archived":
            raise HTTPException(status_code=403, detail="kb_archived")
        mode = request_namespace(request).mode

        def _join() -> tuple[dict[str, Any], bool]:
            conn = dbm.connect_existing(s.db_file)
            try:
                account_id = int(principal.account_id)
                current = kb_collab.get_membership(conn, kb.id, account_id)
                created = current is None
                if created:
                    kb_collab.add_membership(conn, kb_id=kb.id, account_id=account_id, role="member", state="active" if kb.join_policy == "open" else "pending")
                    conn.commit()
                row = _member_row(conn, kb.id, account_id)
                assert row is not None
                return _member_view(row, mode=mode), created
            finally:
                conn.close()

        member, created = await asyncio.to_thread(_join)
        return JSONResponse({"ok": True, "membership": member}, status_code=201 if created else 200)

    async def kb_leave_route(request: Request) -> JSONResponse:
        """``DELETE /kb/{number}/membership``: leave, or withdraw a join request. The last moderator cannot leave (409)."""
        kb = _shared_kb_of_path(request)
        principal = request_principal(request)

        def _leave() -> None:
            conn = dbm.connect_existing(s.db_file)
            try:
                account_id = int(principal.account_id)
                current = kb_collab.get_membership(conn, kb.id, account_id)
                if current is None:
                    raise HTTPException(status_code=404, detail="membership_not_found")
                if current["role"] == "moderator" and current["state"] == "active" and kb_collab.active_moderators(conn, kb.id) == [account_id]:
                    raise HTTPException(status_code=409, detail="last_moderator")
                kb_collab.delete_membership(conn, kb.id, account_id)
                if current["state"] == "active" and current["role"] != "member":
                    # A role ends: a role change (design Section 6.8 rule 3).
                    kb_collab.log_moderation(conn, kb_id=kb.id, actor_account_id=account_id, action="leave", target=str(request_principal_public_id(conn, account_id)), reason=current["role"])
                conn.commit()
            finally:
                conn.close()

        await asyncio.to_thread(_leave)
        return JSONResponse({"ok": True})

    def request_principal_public_id(conn: sqlite3.Connection, account_id: int) -> str:
        return ids.format_public_id(accounts_store.AccountStore(conn).get(account_id).public_id)

    async def kb_members_route(request: Request) -> Response:
        """``GET /kb/{number}/members``: the active members with their roles, for each reader. An account with the capability ``people`` also gets the pending and the banned entries."""
        kb = _shared_kb_of_path(request)
        sees_all = _kb_decide(request, kb, "people") == "allow"
        mode = request_namespace(request).mode

        def _members() -> list[dict[str, Any]]:
            conn = dbm.connect_existing(s.db_file, readonly=True)
            try:
                states = kb_collab.MEMBERSHIP_STATES if sees_all else ("active",)
                return [_member_view(r, mode=mode) for r in kb_collab.members_with_accounts(conn, kb.id, states=states)]
            finally:
                conn.close()

        items = await asyncio.to_thread(_members)
        collection_url = f"{_public_origin(request)}{kb_base_path(kb.number)}members"
        return _collection_response(request, collection_url=collection_url, items=items, extra={"members": items})

    async def kb_moderation_log_route(request: Request) -> Response:
        """``GET /kb/{number}/moderation-log`` (capability ``people``): the moderation log of the shared knowledge base, the newest first. ``actor`` null is the server, or an account that is gone."""
        kb = _shared_kb_of_path(request)

        def _log() -> list[dict[str, Any]]:
            conn = dbm.connect_existing(s.db_file, readonly=True)
            try:
                store = accounts_store.AccountStore(conn)
                actors: dict[int, dict[str, Any] | None] = {}
                items = []
                for row in reversed(kb_collab.list_moderation_log(conn, kb.id)):
                    actor_id = row["actor_account_id"]
                    if actor_id is not None and int(actor_id) not in actors:
                        try:
                            account = store.get(int(actor_id))
                            actors[int(actor_id)] = {"account": ids.format_public_id(account.public_id), "display_name": account.display_name}
                        except accounts_store.AccountNotFound:
                            actors[int(actor_id)] = None
                    # ``number`` is the cursor of the collection pages: the sequence number of the entry in the log.
                    items.append({"number": str(int(row["id"])), "action": row["action"], "actor": None if actor_id is None else actors[int(actor_id)], "target": row["target"], "reason": row["reason"], "created_at": row["created_at"]})
                return items
            finally:
                conn.close()

        items = await asyncio.to_thread(_log)
        return _collection_response(request, collection_url=f"{_public_origin(request)}{kb_base_path(kb.number)}moderation-log", items=items, extra={"log": items})

    async def kb_member_change_route(request: Request) -> Response:
        """``PATCH /kb/{number}/members/{account}`` (capability ``people``): ``{"action": "approve" | "ban" | "remove"}``, or ``{"role": "collaborator" | "member"}``. A role change needs a recent sign-in in a session (design Section 6.8). A moderator cannot change another moderator."""
        kb = _shared_kb_of_path(request)
        principal = request_principal(request)
        body = await _json_object(request)
        action, role = body.get("action"), body.get("role")
        if (action is None) == (role is None):
            raise HTTPException(status_code=400, detail="action_or_role_required")
        if action is not None and action not in _MEMBER_ACTIONS:
            raise HTTPException(status_code=400, detail="action_invalid")
        if role is not None and role not in _MEMBER_ROLES:
            raise HTTPException(status_code=400, detail="role_invalid")
        reason = _reason_of(body)
        if role is not None:
            refused = step_up_response(principal)
            if refused is not None:
                return refused
        mode = request_namespace(request).mode

        def _change() -> dict[str, Any] | None:
            conn = dbm.connect_existing(s.db_file)
            try:
                actor_id = int(principal.account_id)
                target = _account_of_path(conn, request)
                if target.id == actor_id:
                    raise HTTPException(status_code=400, detail="self_not_allowed")
                current = kb_collab.get_membership(conn, kb.id, target.id)
                if current is not None and current["role"] == "moderator" and current["state"] == "active":
                    # Only the admin changes a moderator, and only by the admin route (``/api/admin/kb/{number}/moderators/{account}``: recent sign-in and admin audit, design Sections 6.8 and 10.2). This route never ends a moderator role, also not by ``ban`` or ``remove``.
                    raise HTTPException(status_code=403, detail="moderator_protected")
                log_action = action if action is not None else f"role_{role}"
                if action == "approve":
                    if current is None or current["state"] != "pending":
                        raise HTTPException(status_code=409, detail="not_pending")
                    kb_collab.update_membership(conn, kb_id=kb.id, account_id=target.id, role="member", state="active", decided_by_account_id=actor_id)
                elif action == "ban":
                    if current is None:
                        kb_collab.add_membership(conn, kb_id=kb.id, account_id=target.id, role="member", state="banned", decided_by_account_id=actor_id)
                    else:
                        kb_collab.update_membership(conn, kb_id=kb.id, account_id=target.id, role="member", state="banned", decided_by_account_id=actor_id)
                elif action == "remove":
                    if current is None:
                        raise HTTPException(status_code=404, detail="membership_not_found")
                    kb_collab.delete_membership(conn, kb.id, target.id)
                else:
                    # Design Section 6.2 rule 4: a collaborator must be an active member first. To remove the role makes it a member again.
                    if current is None or current["state"] != "active":
                        raise HTTPException(status_code=409, detail="not_active_member")
                    kb_collab.update_membership(conn, kb_id=kb.id, account_id=target.id, role=role, decided_by_account_id=actor_id)
                kb_collab.log_moderation(conn, kb_id=kb.id, actor_account_id=actor_id, action=log_action, target=ids.format_public_id(target.public_id), reason=reason)
                conn.commit()
                row = _member_row(conn, kb.id, target.id)
                return None if row is None else _member_view(row, mode=mode)
            finally:
                conn.close()

        member = await asyncio.to_thread(_change)
        return JSONResponse({"ok": True, "member": member})

    async def admin_kb_moderator_route(request: Request) -> JSONResponse:
        """``PUT`` and ``DELETE /api/admin/kb/{number}/moderators/{account}``: the admin gives or removes the role ``moderator`` (design Section 10.2; step-up by ``ROUTE_POLICY``). The log of the knowledge base and ``admin_audit`` get a row."""
        principal = request_principal(request)
        try:
            kb = theme_mgr.get_kb_by_reference(request.path_params.get("number"))
        except KnowledgeBaseNotFound:
            raise HTTPException(status_code=404, detail="theme not found") from None
        if not kb.is_shared:
            raise HTTPException(status_code=404, detail="theme not found")
        _require_kb(request, kb, _request_capability(request))
        give = request.method == "PUT"
        mode = request_namespace(request).mode

        def _change() -> tuple[dict[str, Any], int]:
            conn = dbm.connect_existing(s.db_file)
            try:
                actor_id = int(principal.account_id)
                target = _account_of_path(conn, request)
                current = kb_collab.get_membership(conn, kb.id, target.id)
                if give:
                    if current is None:
                        kb_collab.add_membership(conn, kb_id=kb.id, account_id=target.id, role="moderator", state="active", decided_by_account_id=actor_id)
                    else:
                        kb_collab.update_membership(conn, kb_id=kb.id, account_id=target.id, role="moderator", state="active", decided_by_account_id=actor_id)
                else:
                    if current is None or current["role"] != "moderator":
                        raise HTTPException(status_code=404, detail="not_a_moderator")
                    kb_collab.update_membership(conn, kb_id=kb.id, account_id=target.id, role="member", decided_by_account_id=actor_id)
                kb_collab.log_moderation(conn, kb_id=kb.id, actor_account_id=actor_id, action="moderator_give" if give else "moderator_remove", target=ids.format_public_id(target.public_id))
                conn.commit()
                row = _member_row(conn, kb.id, target.id)
                assert row is not None
                return _member_view(row, mode=mode), target.id
            finally:
                conn.close()

        member, target_id = await asyncio.to_thread(_change)

        def _audit() -> None:
            auth_conn = auth_db.connect_auth_db(auth_db.auth_db_path(s.data_dir))
            try:
                auth_db.record_admin_audit(auth_conn, actor_account_id=int(principal.account_id), action="kb_moderator_give" if give else "kb_moderator_remove", target_account_id=target_id, detail={"kb_number": kb.to_dict()["number"]})
            finally:
                auth_conn.close()

        await asyncio.to_thread(_audit)
        return JSONResponse({"ok": True, "member": member})

    # --- Contributions (SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md Sections 9.1, 9.4 to 9.6) ---

    _REVIEW_ACTIONS = ("approve", "reject", "hide", "unhide", "stop_updates", "restart_updates")

    def _contribution_error(exc: contributions.ContributionError, *, kb: KnowledgeBaseInfo, origin: str) -> JSONResponse:
        body: dict[str, Any] = {"error": exc.code}
        if "document" in exc.extra and exc.extra.get("visible"):
            # Design Section 9.1: a duplicate gets 409 with the URL of the document that K has. A duplicate that is hidden (a pending or hidden contribution, Section 9.4) gives only the code.
            body["url"] = f"{origin}{kb_base_path(kb.number)}?doc={exc.extra['document']}"
        return JSONResponse(body, status_code=exc.status)

    def _contribution_number(request: Request) -> int:
        try:
            return ids.parse_public_id(str(request.path_params.get("number") or ""))
        except ValueError:
            raise HTTPException(status_code=404, detail="contribution_not_found") from None

    def _document_title(conn: sqlite3.Connection, document_id: Any) -> str | None:
        """The title of one document. None: no document id, or the document is gone."""
        if not document_id:
            return None
        found = conn.execute("SELECT title FROM documents WHERE id=?", (str(document_id),)).fetchone()
        return None if found is None else found[0]

    def _contribution_view(conn: sqlite3.Connection, row: dict[str, Any], *, full: bool, review_help: bool = False, cache: dict[str, Any] | None = None) -> dict[str, Any]:
        """One contribution for a response. ``full`` (a reviewer, or the contributor): also the reason of a decision and the source document.

        ``title``: the title of the document of the contribution. When the copy is gone (a rejected, removed, or withdrawn contribution), a full view gets the title of the source document; each other view gets null.
        ``cache``: one dict for all rows of one response. It keeps the reputation of each contributor, the signatures, and the document matcher, so that a list does not compute them again for each row.
        """
        cache = {} if cache is None else cache
        title = _document_title(conn, row["document_id"])
        if title is None and full:
            title = _document_title(conn, row["source_document_id"])
        contributor = None
        if row["contributor_account_id"] is not None:
            try:
                account = accounts_store.AccountStore(conn).get(int(row["contributor_account_id"]))
                contributor = {"account": ids.format_public_id(account.public_id), "display_name": account.display_name}
            except accounts_store.AccountNotFound:
                contributor = None
        view: dict[str, Any] = {
            "number": ids.format_public_id(int(row["public_id"])),
            "document": row["document_id"],
            "title": title,
            "origin": row["origin"],
            "state": row["state"],
            "contributor": contributor,
            "approved": row["approved_at"] is not None,
            "approved_at": row["approved_at"],
            "updates_stopped": bool(row["updates_frozen"]),
            "contributed_at": row["contributed_at"],
            "revised_at": row["revised_at"],
            # A copy of a document of another server (federation design Section 10.1): the document that was copied, its knowledge base actor, and the actor with the credit. None for each other contribution.
            "remote": None if not row["remote_source_iri"] else {"document": row["remote_source_iri"], "knowledge_base": row["remote_source_kb_iri"], "credit": row["remote_credit_iri"]},
        }
        if full:
            # The origin removed the document (federation design Section 10.3): the copy is hidden and waits in the review queue until a reviewer shows it again (``unhide``) or removes it. ``waiting`` is false after the decision.
            view["origin_removed"] = None if row["origin_removed_at"] is None else {"at": row["origin_removed_at"], "reason": row["origin_removed_reason"], "waiting": contributions.in_origin_review(row)}
            view["reason"] = row["decision_reason"]
            view["revisions"] = [{"document": r["document_id"], "state": r["state"], "created_at": r["created_at"]} for r in kb_collab.list_revisions(conn, int(row["public_id"]))]
            # The ``Offer`` of an account of another server that made this contribution (federation design Section 12.1): the actor and its server, and if the offer had a grant of this knowledge base in effect when it came (Section 12.2 rule 2). None for each other contribution. ``actor`` is null after the remote account was deleted (the credit "deleted account").
            offer = kf_offers.inbound_of_contribution(conn, int(row["public_id"]))
            view["offer"] = None if offer is None else {"actor": row["remote_credit_iri"], "server": offer["domain"], "with_grant": bool(offer["capability_iri"]), "grant_valid": bool(offer["grant_valid"])}
        if review_help and row["contributor_account_id"] is None and row["remote_credit_iri"] and not row["remote_source_kb_iri"]:
            # The reputation of an account of another server comes only from the decisions of this server (federation design Section 12.1 rule 5).
            reputations = cache.setdefault("remote_reputations", {})
            actor_iri = str(row["remote_credit_iri"])
            if actor_iri not in reputations:
                reputations[actor_iri] = reputation.compute_remote(conn, actor_iri)
            view["contributor_reputation"] = {"level": reputations[actor_iri][0], "approval_rate": reputations[actor_iri][1]}
        if review_help and row["contributor_account_id"] is not None:
            # Reviewers see the level and the approval rate of the contributor (design Section 8.6).
            reputations = cache.setdefault("reputations", {})
            contributor_id = int(row["contributor_account_id"])
            if contributor_id not in reputations:
                reputations[contributor_id] = reputation.compute(conn, contributor_id)
            contributor_reputation = reputations[contributor_id]
            view["contributor_reputation"] = {"level": contributor_reputation.level, "approval_rate": contributor_reputation.approval_rate}
        if review_help and row["submission_item_id"] is not None and row["state"] == "pending":
            # Help for the decision of a reviewer (design Section 9.3 rule 2), for a contribution that waits for the decision. The server never decides for a reviewer.
            item = conn.execute("SELECT theme_id, match_score, routed_by FROM kb_submission_items WHERE id=?", (int(row["submission_item_id"]),)).fetchone()
            others = [
                {**_kb_ref(int(other["kb_id"])), "decision": contributions.decision_of(other), "reason": other["decision_reason"]}
                for other in kb_collab.contributions_of_item(conn, int(row["submission_item_id"]))
                if int(other["kb_id"]) != int(row["kb_id"])
            ]
            kb_score = None
            if "signatures" not in cache:
                cache["signatures"] = {int(sig["kb_id"]): sig for sig in themes_store.load_signatures(conn)}
            signature = cache["signatures"].get(int(row["kb_id"]))
            if signature is not None and row["document_id"]:
                if "matcher" not in cache:
                    cache["matcher"] = theme_judgement.DocumentMatcher.load(conn)
                matcher = cache["matcher"]
                keys = theme_judgement.document_keys(conn, int(row["source_kb_id"]), str(row["source_document_id"])) if row["source_kb_id"] is not None else set()
                kb_keys = {(str(e[0]), str(e[1])) for e in json.loads(signature["entities"] or "[]")}
                kb_score = matcher.score(keys, kb_keys) if keys else None
            view["review_help"] = {"other_decisions": others, "theme_score": None if item is None else item["match_score"], "routed_by": None if item is None else item["routed_by"], "kb_score": kb_score}
        return view

    def _run_merge_jobs_of(kb: KnowledgeBaseInfo) -> None:
        """Run the merge jobs of K that wait, with the provider of the server (CT24): the pool gives the server context for a shared knowledge base without an acting account."""
        conn = dbm.connect_existing(s.db_file)
        try:
            contributions.run_pending_merge_jobs(conn, lambda kb_id: service_pool.get_service(kb_id).provider, lambda kb_id: theme_mgr.get_settings_for_kb(kb_id, s), kb_id=kb.id)
        finally:
            conn.close()

    async def kb_contribute_route(request: Request) -> JSONResponse:
        """``POST /kb/{number}/contributions`` with ``{"document": "<document ID>"}``: a direct contribution of a document of the personal knowledge base of the requester (design Section 9.1). The merge job runs in this request; the answer has the state after it."""
        kb = _shared_kb_of_path(request)
        principal = request_principal(request)
        body = await _json_object(request)
        document_id = body.get("document")
        if not isinstance(document_id, str) or not document_id.strip():
            raise HTTPException(status_code=400, detail="document_required")
        is_reviewer = _kb_decide(request, kb, "review") == "allow"
        origin = _public_origin(request)

        def _contribute() -> JSONResponse:
            try:
                personal = theme_mgr.personal_kb_for(int(principal.account_id))
            except KnowledgeBaseNotFound:
                return JSONResponse({"error": "document_not_found"}, status_code=404)
            conn = dbm.connect_existing(s.db_file)
            try:
                try:
                    # The reputation ``low`` makes the contribution wait for approval (design Section 8.6).
                    approved, pending = contributions.direct_mode(conn, kb_id=kb.id, account_id=int(principal.account_id), is_reviewer=is_reviewer, is_admin=principal.server_role == "admin")
                    row = contributions.contribute_direct(conn, kb_id=kb.id, source_kb_id=personal.id, source_document_id=document_id.strip(), contributor_account_id=int(principal.account_id), approved=approved, pending=pending)
                except contributions.ContributionError as exc:
                    conn.rollback()
                    return _contribution_error(exc, kb=kb, origin=origin)
                conn.commit()
                public_id = int(row["public_id"])
            finally:
                conn.close()
            _run_merge_jobs_of(kb)
            conn = dbm.connect_existing(s.db_file, readonly=True)
            try:
                return JSONResponse({"ok": True, "contribution": _contribution_view(conn, kb_collab.get_contribution(conn, public_id) or row, full=True)}, status_code=201)
            finally:
                conn.close()

        return await asyncio.to_thread(_contribute)

    def _remote_read_view(read: dict[str, Any]) -> dict[str, Any]:
        """One read of a document of another server for a curation: ``state`` is ``queued`` (the sidecar did not read it), ``done`` (the copy is a contribution), or ``failed`` with ``error``."""
        return {
            "read": int(read["id"]),
            "document": read["target_iri"],
            "title": read.get("title"),
            "state": {"pending": "queued", "done": "done", "failed": "failed"}[read["status"]],
            "error": read["error_code"],
            "contribution": None if read["contribution_public_id"] is None else ids.format_public_id(int(read["contribution_public_id"])),
            "requested_at": read["started_at"],
        }

    def _curate_remote(kb: KnowledgeBaseInfo, account_id: int, body: dict[str, Any], documents: list[str]) -> JSONResponse:
        """The curation from a shared knowledge base of another server (federation design Section 11): ``{"remote_source": <id of the record>, "documents": ["<document IRI>", ...]}``. The sidecar reads each document with a signed GET; then the copy is made. Thus the answer is 202 with one read for each document."""
        from ..knowledge_federation import remote_documents

        remote_id = body.get("remote_source")
        if isinstance(remote_id, bool) or not isinstance(remote_id, int) or "source" in body:
            return JSONResponse({"error": "source_invalid"}, status_code=400)
        results: list[dict[str, Any]] = []
        conn = dbm.connect_existing(s.db_file)
        try:
            try:
                source = remote_documents.source_of(conn, kb_id=kb.id, remote_kb_id=remote_id)
            except remote_documents.SourceError as exc:
                return JSONResponse({"error": exc.code}, status_code=exc.status)
            limit = contributions.read_limits(conn)["submission_max_documents"]
            if len(documents) > limit:
                return JSONResponse({"error": "documents_invalid", "limit": limit}, status_code=400)
            held = kb_collab.remote_sources_in_kb(conn, kb.id)
            now = time.time()
            for document_iri in (d.strip() for d in documents):
                known = held.get(document_iri)
                if known is not None:
                    # The checks that need no read: K has a contribution of this document, or K removed it (a tombstone).
                    results.append({"document": document_iri, "error": "contribution_tombstoned" if known["state"] == "removed" else "contribution_duplicate"})
                    continue
                try:
                    read = remote_documents.request_document(conn, source, document_iri=document_iri, kb_id=kb.id, account_id=account_id, now=now)
                except remote_documents.SourceError as exc:
                    results.append({"document": document_iri, "error": exc.code})
                    continue
                results.append({"document": document_iri, "read": int(read["id"]), "state": "queued"})
            conn.commit()
        finally:
            conn.close()
        return JSONResponse({"ok": True, "curations": results}, status_code=202 if any("read" in result for result in results) else 200)

    # --- Remote collaborators (KNOWLEDGE_FEDERATION_DESIGN.md Section 12.2, KD6) ---

    def _grant_view(row: dict[str, Any], now: float) -> dict[str, Any]:
        return {
            "id": int(row["id"]), "grant": row["grant_iri"], "actor": row["actor_iri"], "server": row["domain"], "state": kf_grants.issued_state(row, now),
            "published_at": row["published_at"], "end_time": row["end_time"], "revoked_at": row["revoked_at"],
        }

    def _federation_origin() -> str | None:
        """The canonical origin of this server for an IRI that leaves the server (the actors and the activities of the knowledge federation): the configured public URL only (``contributions.canonical_origin``), never the address of the request (no fallback). None when the settings give none: then a route that must send an activity refuses with ``public_origin_missing``."""
        return contributions.canonical_origin(s)

    async def kb_remote_collaborators_route(request: Request) -> Response:
        """``GET /kb/{number}/remote-collaborators`` (capability ``people``): the grants of the role ``collaborator`` that this shared knowledge base gave to accounts of other servers, the newest first, with their end times.

        ``POST`` with ``{"actor": "<IRI of the account actor>"}``: give the role for 6 months. The knowledge base actor sends ``Grant``. A grant of the same actor that is in effect is revoked (a renewal). A recent sign-in in a session is necessary, as for the role ``collaborator`` of a local account (design Section 6.8). A remote collaborator has only the capabilities ``contribute_direct`` and ``contribute_theme``.
        """
        from ..knowledge_federation import access as kf_access
        from ..knowledge_federation import collections as kf_collections
        from ..knowledge_federation import outbox as kf_outbox
        from ..knowledge_federation import vocabulary as kf_vocabulary

        kb = _shared_kb_of_path(request)
        principal = request_principal(request)
        if request.method != "POST":
            def _list() -> list[dict[str, Any]]:
                conn = dbm.connect_existing(s.db_file, readonly=True)
                try:
                    now = time.time()
                    return [_grant_view(row, now) for row in kf_grants.list_issued(conn, int(kb.number))]
                finally:
                    conn.close()

            items = await asyncio.to_thread(_list)
            return JSONResponse({"grants": items})
        body = await _json_object(request)
        actor = body.get("actor")
        if set(body) - {"actor"} or not isinstance(actor, str) or not actor.strip():
            raise HTTPException(status_code=400, detail="actor_invalid")
        actor = actor.strip()
        refused = step_up_response(principal)
        if refused is not None:
            return refused

        canonical = _federation_origin()

        def _give() -> JSONResponse:
            conn = dbm.connect_existing(s.db_file)
            try:
                if not kf_access.federation_on(conn):
                    return JSONResponse({"error": "federation_off"}, status_code=409)
                if canonical is None:
                    return JSONResponse({"error": "public_origin_missing"}, status_code=409)
                domain = kf_access.remote_server_domain(actor, canonical)
                parts = urlsplit(actor)
                # The target is an account actor of another Claire server (``/ap/actors/{public ID}``), not its server actor.
                if domain is None or parts.query or parts.fragment or not parts.path.startswith("/ap/actors/") or parts.path == kf_access.SERVER_ACTOR_PATH or len(actor) > 2048:
                    return JSONResponse({"error": "actor_invalid"}, status_code=400)
                if federation_store.blocking_domain(conn, domain) is not None:
                    return JSONResponse({"error": "blocked_domain"}, status_code=409)
                now = time.time()
                kb_iri = kf_collections.knowledge_base_iris(canonical, int(kb.number))["id"]
                grant, replaced = kf_grants.give(conn, kb_number=int(kb.number), iri=kf_grants.grant_iri(kb_iri), actor_iri=actor, domain=domain, account_id=int(principal.account_id), now=now)
                signer = str(int(kb.number))
                for old in replaced:
                    kf_outbox.enqueue(conn, kf_vocabulary.build_revoke(activity_id=f"{old['grant_iri']}/revoke", knowledge_base=kb_iri, account_actor=actor, grant=old["grant_iri"]), signer=signer, recipients=[actor], origin=canonical, now=now)
                kf_outbox.enqueue(conn, kf_vocabulary.build_grant(activity_id=grant["grant_iri"], knowledge_base=kb_iri, account_actor=actor, published=grant["published_at"], end_time=grant["end_time"]), signer=signer, recipients=[actor], origin=canonical, now=now)
                kb_collab.log_moderation(conn, kb_id=kb.id, actor_account_id=int(principal.account_id), action="remote_collaborator_grant", target=actor, now=now)
                conn.commit()
                return JSONResponse({"ok": True, "grant": _grant_view(grant, now), "renewed": bool(replaced)}, status_code=201)
            finally:
                conn.close()

        return await asyncio.to_thread(_give)

    async def kb_remote_collaborator_route(request: Request) -> Response:
        """``DELETE /kb/{number}/remote-collaborators/{grant}`` (capability ``people``, recent sign-in): end one grant at once. The knowledge base actor sends ``Revoke``; the next contribution of the account needs approval again (design Section 12.2 rule 3)."""
        from ..knowledge_federation import collections as kf_collections
        from ..knowledge_federation import outbox as kf_outbox
        from ..knowledge_federation import vocabulary as kf_vocabulary

        kb = _shared_kb_of_path(request)
        principal = request_principal(request)
        raw = str(request.path_params.get("grant") or "")
        if not (raw.isascii() and raw.isdigit()):
            raise HTTPException(status_code=404, detail="grant_not_found")
        refused = step_up_response(principal)
        if refused is not None:
            return refused
        canonical = _federation_origin()

        def _revoke() -> JSONResponse:
            from ..knowledge_federation import access as kf_access

            conn = dbm.connect_existing(s.db_file)
            try:
                now = time.time()
                grant = kf_grants.get_issued(conn, int(raw))
                if grant is None or int(grant["kb_number"]) != int(kb.number):
                    return JSONResponse({"error": "grant_not_found"}, status_code=404)
                federation = kf_access.federation_on(conn)
                if federation and canonical is None:
                    # The ``Revoke`` needs the IRI of the knowledge base actor: the grant does not end without it, so that the other server is not left with a grant that this server ended.
                    return JSONResponse({"error": "public_origin_missing"}, status_code=409)
                if kf_grants.issued_state(grant, now) != "active" or not kf_grants.revoke(conn, int(grant["id"]), account_id=int(principal.account_id), now=now):
                    return JSONResponse({"error": "grant_not_active"}, status_code=409)

                # No ``Revoke`` goes to an address that is not one of another server now; the grant ends on this server, which is the authority for it.
                if federation and canonical is not None and kf_access.remote_server_domain(grant["actor_iri"], canonical) is not None:
                    kb_iri = kf_collections.knowledge_base_iris(canonical, int(kb.number))["id"]
                    kf_outbox.enqueue(conn, kf_vocabulary.build_revoke(activity_id=f"{grant['grant_iri']}/revoke", knowledge_base=kb_iri, account_actor=grant["actor_iri"], grant=grant["grant_iri"]), signer=str(int(kb.number)), recipients=[grant["actor_iri"]], origin=canonical, now=now)
                kb_collab.log_moderation(conn, kb_id=kb.id, actor_account_id=int(principal.account_id), action="remote_collaborator_revoke", target=str(grant["actor_iri"]), now=now)
                conn.commit()
                return JSONResponse({"ok": True, "grant": _grant_view(kf_grants.get_issued(conn, int(grant["id"])) or grant, now)})
            finally:
                conn.close()

        return await asyncio.to_thread(_revoke)

    async def kb_remote_grants_route(request: Request) -> JSONResponse:
        """``GET /api/kb/remote-grants``: the grants of the role ``collaborator`` that the requester got from shared knowledge bases of other servers, the newest first (federation design Section 12.2). ``state`` is ``active``, ``ended``, or ``revoked``, as this server knows it; the other server decides each use. ``remote_id`` is the record of the knowledge base on this server (the value of ``remote_knowledge_base`` of ``POST /submissions``), or null when this server has no record of it."""
        principal = request_principal(request)
        if principal.account_id is None:
            raise HTTPException(status_code=404, detail="not found")

        def _list() -> list[dict[str, Any]]:
            conn = dbm.connect_existing(s.db_file, readonly=True)
            try:
                now = time.time()
                out = []
                for row in kf_grants.received_of_account(conn, int(principal.account_id)):
                    record = remote_kbs.get_by_iri(conn, str(row["kb_iri"]))
                    out.append({
                        "grant": row["grant_iri"], "knowledge_base": row["kb_iri"], "server": row["domain"], "remote_id": None if record is None else record.id, "label": None if record is None else record.label,
                        "url": None if record is None else record.url, "state": kf_grants.issued_state(row, now), "published_at": row["published_at"], "end_time": row["end_time"], "revoked_at": row["revoked_at"],
                    })
                return out
            finally:
                conn.close()

        items = await asyncio.to_thread(_list)
        return JSONResponse({"grants": items})

    async def kb_remote_sources_route(request: Request) -> JSONResponse:
        """``GET /kb/{number}/remote-sources`` (capability ``curate``): the shared knowledge bases of other servers that a reviewer of K can curate from (the remote members of the themes of K, federation design Section 11 rule 1), and the newest reads for curations into K. With ``?source=<id>``: also the documents of that knowledge base. The list of documents comes from the sidecar; the request starts a read when the list is older than 10 minutes."""
        from ..knowledge_federation import outbox as kf_outbox
        from ..knowledge_federation import remote_documents, remote_kbs

        kb = _shared_kb_of_path(request)
        selected_raw = request.query_params.get("source")
        if set(request.query_params) - {"source"} or (selected_raw is not None and not (selected_raw.isascii() and selected_raw.isdigit())):
            raise HTTPException(status_code=400, detail="source_invalid")
        head = request.method == "HEAD"

        def _view() -> JSONResponse:
            conn = dbm.connect_existing(s.db_file)
            try:
                follows = kf_outbox.follow_states(conn)
                body: dict[str, Any] = {
                    "sources": [{**remote_kbs.view(source), "follow": follows.get(source.iri)} for source in remote_documents.sources_of(conn, kb.id)],
                    "curations": [_remote_read_view(read) for read in remote_documents.curation_reads(conn, kb.id)],
                }
                if selected_raw is not None:
                    try:
                        source = remote_documents.source_of(conn, kb_id=kb.id, remote_kb_id=int(selected_raw))
                    except remote_documents.SourceError as exc:
                        return JSONResponse({"error": exc.code}, status_code=exc.status)
                    if not head:
                        remote_documents.request_listing(conn, source, now=time.time())
                        conn.commit()
                    held = kb_collab.remote_sources_in_kb(conn, kb.id)
                    body["selected"] = {
                        "id": source.id,
                        **remote_documents.listing_state(conn, source),
                        "documents": [
                            {"iri": doc["iri"], "title": doc["title"], "url": doc["url"], "published": doc["published"], "updated": doc["updated"], "curated": None if doc["iri"] not in held else held[doc["iri"]]["state"]}
                            for doc in remote_documents.listing(conn, source.iri)
                        ],
                    }
                return JSONResponse(body, headers={"Cache-Control": "no-store"})
            finally:
                conn.close()

        return await asyncio.to_thread(_view)

    async def kb_curations_route(request: Request) -> JSONResponse:
        """``POST /kb/{number}/curations`` with ``{"source": "<number of S>", "documents": [...]}``: a reviewer of K copies visible documents of another knowledge base S of a theme of K (design Section 9.7). Each document has its own result; the merge jobs run in this request. With ``{"remote_source": <id>, "documents": ["<document IRI>", ...]}`` S is a shared knowledge base of another server (``_curate_remote``)."""
        kb = _shared_kb_of_path(request)
        principal = request_principal(request)
        body = await _json_object(request)
        documents = body.get("documents")
        if not isinstance(documents, list) or not documents or not all(isinstance(d, str) and d.strip() for d in documents) or len(set(documents)) != len(documents):
            raise HTTPException(status_code=400, detail="documents_invalid")
        origin = _public_origin(request)
        if "remote_source" in body:
            return await asyncio.to_thread(_curate_remote, kb, int(principal.account_id), body, documents)

        def _curate() -> JSONResponse:
            try:
                source_kb = theme_mgr.get_kb_by_number(ids.parse_public_id(str(body.get("source") or "")))
            except (ValueError, KnowledgeBaseNotFound):
                return JSONResponse({"error": "source_not_found"}, status_code=404)
            results: list[dict[str, Any]] = []
            made: dict[str, int] = {}
            conn = dbm.connect_existing(s.db_file)
            try:
                try:
                    source = contributions.curation_source(conn, kb_id=kb.id, source_kb_id=source_kb.id)
                except contributions.ContributionError as exc:
                    return JSONResponse({"error": exc.code}, status_code=exc.status)
                limit = contributions.read_limits(conn)["submission_max_documents"]
                if len(documents) > limit:
                    return JSONResponse({"error": "documents_invalid", "limit": limit}, status_code=400)
                for document_id in (d.strip() for d in documents):
                    try:
                        row = contributions.curate(conn, kb_id=kb.id, source=source, document_id=document_id, curator_account_id=int(principal.account_id))
                    except contributions.ContributionError as exc:
                        conn.rollback()
                        results.append({"document": document_id, **json.loads(_contribution_error(exc, kb=kb, origin=origin).body)})
                        continue
                    conn.commit()
                    made[document_id] = int(row["public_id"])
                    results.append({"document": document_id})
            finally:
                conn.close()
            if made:
                _run_merge_jobs_of(kb)
            conn = dbm.connect_existing(s.db_file, readonly=True)
            try:
                for result in results:
                    if result["document"] in made:
                        row = kb_collab.get_contribution(conn, made[result["document"]])
                        if row is not None:
                            result["contribution"] = _contribution_view(conn, row, full=True)
            finally:
                conn.close()
            return JSONResponse({"ok": True, "curations": results}, status_code=201 if made else 200)

        return await asyncio.to_thread(_curate)

    async def kb_forks_route(request: Request) -> JSONResponse:
        """``POST /kb/{number}/forks``: an active account forks a shared knowledge base into a new shared knowledge base (design Section 9.8). The copy runs in this request, without an LLM call and without a new embedding. A personal knowledge base that the requester reads gets 403 ``kb_fork_shared_only`` (CT15)."""
        _require_multi_kb()
        kb = _select_kb(request)
        principal = request_principal(request)
        if principal.account_id is None:
            raise HTTPException(status_code=404, detail="theme not found")
        if kb.is_personal:
            return JSONResponse({"error": "kb_fork_shared_only"}, status_code=403)
        decision = _kb_decide(request, kb, "fork")
        if decision != "allow":
            raise HTTPException(status_code=403 if decision == "forbidden" else 404, detail="kb_capability_required" if decision == "forbidden" else "theme not found")
        account_id = int(principal.account_id)

        def _fork() -> JSONResponse:
            try:
                fork = theme_mgr.define_kb(f"{kb.label} (fork)", description=kb.description, icon=kb.icon, account_id=account_id, knowledge_type=kb.knowledge_type, seed_entities=list(kb.seed_entities) or None)
            except ValueError as exc:
                return JSONResponse({"error": str(exc)}, status_code=400)
            conn = dbm.connect_existing(s.db_file)
            try:
                try:
                    counts = contributions.fork_copy(conn, Path(s.data_dir), source_kb_id=kb.id, target_kb_id=fork.id, account_id=account_id)
                    conn.commit()
                except Exception as exc:  # noqa: BLE001 - a fork that did not complete must not stay as an empty knowledge base
                    conn.rollback()
                    _log_operation_failure(request, "kb fork", exc)
                    conn.close()
                    theme_mgr.delete_kb(fork.id, purge=True)
                    return JSONResponse({"error": "fork_failed"}, status_code=500)
            finally:
                conn.close()
            theme_mgr.reload()
            _after_kb_founded(fork.id, account_id)
            return JSONResponse({"ok": True, "knowledge_base": theme_mgr.get_kb(fork.id).to_dict(), "forked_from": ids.format_public_id(kb.number), **counts}, status_code=201)

        return await asyncio.to_thread(_fork)

    async def kb_contributions_route(request: Request) -> Response:
        """``GET /kb/{number}/contributions``: the visible contributions for each reader; each state for a reviewer; the own contributions of the requester in each state."""
        kb = _shared_kb_of_path(request)
        principal = request_principal(request)
        is_reviewer = _kb_decide(request, kb, "review") == "allow"
        state = _collection_filter(request, "state", kb_collab.CONTRIBUTION_STATES)

        def _list() -> list[dict[str, Any]]:
            conn = dbm.connect_existing(s.db_file, readonly=True)
            try:
                items = []
                view_cache: dict[str, Any] = {}
                for row in reversed(kb_collab.list_contributions(conn, kb.id, state=state)):
                    own = principal.account_id is not None and row["contributor_account_id"] == principal.account_id
                    if row["state"] == "visible" or is_reviewer or own:
                        items.append(_contribution_view(conn, row, full=is_reviewer or own, review_help=is_reviewer, cache=view_cache))
                if is_reviewer and state == "pending":
                    # The review queue (design Section 8.6): the level ``good`` first, ``low`` last; in one level the oldest first.
                    items.sort(key=lambda item: (reputation.queue_rank((item.get("contributor_reputation") or {}).get("level")), item["contributed_at"]))
                return items
            finally:
                conn.close()

        items = await asyncio.to_thread(_list)
        return _collection_response(request, collection_url=f"{_public_origin(request)}{kb_base_path(kb.number)}contributions", items=items, extra={"contributions": items})

    async def kb_contribution_withdraw_route(request: Request) -> JSONResponse:
        """``DELETE /kb/{number}/contributions/{contribution}``: the contributor withdraws while the contribution is not approved (CT7). After the approval: 409 ``contribution_approved``."""
        kb = _shared_kb_of_path(request)
        principal = request_principal(request)
        number = _contribution_number(request)
        if principal.account_id is None:
            raise HTTPException(status_code=404, detail="contribution_not_found")

        def _withdraw() -> JSONResponse:
            conn = dbm.connect_existing(s.db_file)
            try:
                result = contributions.withdraw(conn, Path(s.data_dir), kb_id=kb.id, contribution=number, account_id=int(principal.account_id))
            except contributions.ContributionError as exc:
                return JSONResponse({"error": exc.code}, status_code=exc.status)
            finally:
                conn.close()
            # ``contribution_blocked``: the withdrawal of an automatic contribution sets the flag "never contribute" of the source document, so that the rule does not send it again (design Section 9.9 rules 2 and 6).
            return JSONResponse({"ok": True, "contribution_blocked": bool(result["contribution_blocked"])})

        return await asyncio.to_thread(_withdraw)

    async def kb_contribution_review_route(request: Request) -> JSONResponse:
        """``PATCH /kb/{number}/contributions/{contribution}`` (capability ``review``): ``{"action": "approve" | "reject" | "hide" | "unhide" | "stop_updates" | "restart_updates", "reason"?}``. A reject needs a reason code."""
        kb = _shared_kb_of_path(request)
        principal = request_principal(request)
        number = _contribution_number(request)
        body = await _json_object(request)
        action = body.get("action")
        if action not in _REVIEW_ACTIONS:
            raise HTTPException(status_code=400, detail="action_invalid")
        reason = body.get("reason", "")
        actor = int(principal.account_id)

        def _review() -> JSONResponse:
            conn = dbm.connect_existing(s.db_file)
            try:
                try:
                    if action == "approve":
                        contributions.approve(conn, kb_id=kb.id, contribution=number, account_id=actor, is_admin=principal.server_role == "admin")
                    elif action == "reject":
                        contributions.reject(conn, Path(s.data_dir), kb_id=kb.id, contribution=number, account_id=actor, reason=reason)
                    elif action in ("hide", "unhide"):
                        contributions.set_hidden(conn, kb_id=kb.id, contribution=number, account_id=actor, hidden=action == "hide")
                    else:
                        contributions.set_updates_frozen(conn, kb_id=kb.id, contribution=number, account_id=actor, frozen=action == "stop_updates")
                except contributions.ContributionError as exc:
                    conn.rollback()
                    return JSONResponse({"error": exc.code}, status_code=exc.status)
                conn.commit()
            finally:
                conn.close()
            if action == "approve":
                _run_merge_jobs_of(kb)  # the approval of a pending contribution starts its merge job
            conn = dbm.connect_existing(s.db_file, readonly=True)
            try:
                row = kb_collab.get_contribution(conn, number)
                return JSONResponse({"ok": True, "contribution": None if row is None else _contribution_view(conn, row, full=True)})
            finally:
                conn.close()

        return await asyncio.to_thread(_review)

    async def kb_contribution_remove_route(request: Request) -> JSONResponse:
        """``POST /kb/{number}/contributions/{contribution}/remove`` (capability ``remove``) with ``{"reason": "<code>"}``: purge the copy in K and leave a tombstone."""
        kb = _shared_kb_of_path(request)
        principal = request_principal(request)
        number = _contribution_number(request)
        body = await _json_object(request)

        def _remove() -> JSONResponse:
            conn = dbm.connect_existing(s.db_file)
            try:
                contributions.remove(conn, Path(s.data_dir), kb_id=kb.id, contribution=number, account_id=int(principal.account_id), reason=body.get("reason", ""))
            except contributions.ContributionError as exc:
                return JSONResponse({"error": exc.code}, status_code=exc.status)
            finally:
                conn.close()
            return JSONResponse({"ok": True})

        return await asyncio.to_thread(_remove)

    async def kb_contribution_retry_route(request: Request) -> JSONResponse:
        """``POST /kb/{number}/contributions/{contribution}/retry``: the contributor or a reviewer starts the merge job of a failed contribution again (design Section 9.1 step 3)."""
        kb = _shared_kb_of_path(request)
        principal = request_principal(request)
        number = _contribution_number(request)
        is_reviewer = _kb_decide(request, kb, "review") == "allow"
        if kb.state == "archived":
            # An archived knowledge base is read-only: no merge job (design Section 10.1).
            raise HTTPException(status_code=403, detail="kb_archived")
        can_contribute = _kb_decide(request, kb, "contribute_direct") == "allow"

        def _retry() -> JSONResponse:
            conn = dbm.connect_existing(s.db_file)
            try:
                row = kb_collab.get_contribution(conn, number)
                if row is None or int(row["kb_id"]) != kb.id or not (is_reviewer or (principal.account_id is not None and row["contributor_account_id"] == principal.account_id)):
                    return JSONResponse({"error": "contribution_not_found"}, status_code=404)
                if not is_reviewer and not can_contribute:
                    # A contributor that is banned, or that is no member now, does not start a merge job (design Section 6.2 rule 1).
                    return JSONResponse({"error": "kb_capability_required"}, status_code=403)
                try:
                    contributions.retry_merge(conn, contribution=number)
                except contributions.ContributionError as exc:
                    conn.rollback()
                    return JSONResponse({"error": exc.code}, status_code=exc.status)
                conn.commit()
            finally:
                conn.close()
            _run_merge_jobs_of(kb)
            conn = dbm.connect_existing(s.db_file, readonly=True)
            try:
                row = kb_collab.get_contribution(conn, number)
                return JSONResponse({"ok": True, "contribution": None if row is None else _contribution_view(conn, row, full=True)})
            finally:
                conn.close()

        return await asyncio.to_thread(_retry)

    # --- Contribution to a theme (SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md Sections 9.2 and 9.3) ---

    def _kb_ref(kb_id: int) -> dict[str, Any]:
        try:
            kb = theme_mgr.get_kb(int(kb_id))
        except KnowledgeBaseNotFound:
            return {"knowledge_base": None, "label": None}
        return {"knowledge_base": ids.format_public_id(kb.number) if kb.number is not None else None, "label": kb.label}

    def _submission_view(conn: sqlite3.Connection, submission: dict[str, Any]) -> dict[str, Any]:
        """One submission for its account: the theme, the score, and the state of each target of each item (design Section 9.2 rule 8)."""
        items = []
        for item in kb_collab.submission_items(conn, int(submission["public_id"])):
            theme = themes_store.get_theme(conn, int(item["theme_id"])) if item["theme_id"] is not None else None
            made = {int(c["kb_id"]): c for c in kb_collab.contributions_of_item(conn, int(item["id"]))}
            reasons = {int(entry["kb_id"]): entry["reason"] for entry in item["skipped"]}
            targets = []
            for kb_id in item["target_kb_ids"]:
                target = _kb_ref(kb_id)
                if kb_id in made:
                    target.update({"state": made[kb_id]["state"], "contribution": ids.format_public_id(int(made[kb_id]["public_id"])), "approved": made[kb_id]["approved_at"] is not None})
                elif kb_id in reasons:
                    target.update({"state": "skipped", "reason": reasons[kb_id]})
                else:
                    target.update({"state": "withdrawn"})  # the contributor deleted the pending contribution
                targets.append(target)
            # The shared knowledge bases of other servers that got an ``Offer`` for this item, or that were skipped with a reason (federation design Section 12.1 rule 3): the state is the decision of the other server.
            targets.extend(kf_offers.outbound_view(conn, offer) for offer in kf_offers.outbound_of_item(conn, int(item["id"])))
            view: dict[str, Any] = {
                "document": item["source_document_id"],
                "title": _document_title(conn, item["source_document_id"]),
                "theme": None if theme is None else ids.format_public_id(int(theme["number"])),
                "theme_label": None if theme is None else theme["label"],
                "routed_by": item["routed_by"],
                "score": item["match_score"],
                "targets": targets,
            }
            if theme is None and not any(target.get("remote") for target in targets):
                # No theme: the account selects a theme, contributes directly to a shared knowledge base where it is a member, or makes a shared knowledge base (Section 9.2 rule 3). An item of a direct choice of a knowledge base of another server has no theme and is not such an item.
                view["code"] = "no_theme"
            items.append(view)
        return {"number": ids.format_public_id(int(submission["public_id"])), "created_at": submission["created_at"], "items": items}

    async def submissions_route(request: Request) -> Response:
        """``POST /submissions`` with ``{"documents": [...], "theme"?: "<number>"}``: contribute documents of the personal knowledge base of the requester to themes. ``GET /submissions``: the submissions of the requester.

        A theme with shared knowledge bases of other servers sends an ``Offer`` to each of them (federation design Section 12.1). ``{"documents": [...], "remote_knowledge_base": <id of the record>}`` (not with ``theme``) is the direct choice of one shared knowledge base of another server; it needs a grant of that knowledge base in its term (403 ``grant_required``).
        """
        principal = request_principal(request)
        if principal.account_id is None:
            raise HTTPException(status_code=404, detail="not found")
        account_id = int(principal.account_id)
        if request.method != "POST":
            def _list() -> list[dict[str, Any]]:
                conn = dbm.connect_existing(s.db_file, readonly=True)
                try:
                    return [_submission_view(conn, sub) for sub in kb_collab.list_submissions(conn, account_id)]
                finally:
                    conn.close()

            items = await asyncio.to_thread(_list)
            return _collection_response(request, collection_url=f"{_public_origin(request)}/submissions", items=items, extra={"submissions": items})
        body = await _json_object(request)
        documents = body.get("documents")
        if not isinstance(documents, list) or not all(isinstance(d, str) and d.strip() for d in documents):
            raise HTTPException(status_code=400, detail="documents_invalid")
        remote_kb_id = body.get("remote_knowledge_base")
        if "remote_knowledge_base" in body and (isinstance(remote_kb_id, bool) or not isinstance(remote_kb_id, int) or "theme" in body):
            raise HTTPException(status_code=400, detail="remote_knowledge_base_invalid")
        # The offers to other servers have IRIs of this server: only the configured public origin gives them. Without it a direct choice of a remote knowledge base is refused, and a theme has only the targets of this server.
        canonical = _federation_origin()
        if remote_kb_id is not None and canonical is None:
            return JSONResponse({"error": "public_origin_missing"}, status_code=409)

        def _submit() -> JSONResponse:
            try:
                personal = theme_mgr.personal_kb_for(account_id)
            except KnowledgeBaseNotFound:
                return JSONResponse({"error": "document_not_found"}, status_code=404)
            conn = dbm.connect_existing(s.db_file)
            try:
                theme_id = None
                if "theme" in body:
                    try:
                        theme = themes_store.get_theme_by_number(conn, ids.parse_public_id(str(body["theme"])))
                    except ValueError:
                        theme = None
                    if theme is None or theme["state"] != "active":
                        return JSONResponse({"error": "theme_not_found"}, status_code=404)
                    theme_id = int(theme["id"])
                try:
                    number = contributions.submit_to_themes(conn, account_id=account_id, is_admin=principal.server_role == "admin", personal_kb_id=personal.id, documents=[d.strip() for d in documents], theme_id=theme_id, origin=canonical, remote_kb_id=remote_kb_id)
                except contributions.ContributionError as exc:
                    conn.rollback()
                    return JSONResponse({"error": exc.code, **exc.extra}, status_code=exc.status)
                conn.commit()
                # A contribution of a reviewer needs no approval: its merge job runs now, with the provider of the server.
                contributions.run_pending_merge_jobs(conn, lambda kb_id: service_pool.get_service(kb_id).provider, lambda kb_id: theme_mgr.get_settings_for_kb(kb_id, s))
                return JSONResponse({"ok": True, "submission": _submission_view(conn, kb_collab.get_submission(conn, number) or {"public_id": number, "created_at": None})}, status_code=201)
            finally:
                conn.close()

        return await asyncio.to_thread(_submit)

    async def submission_route(request: Request) -> JSONResponse:
        """``GET /submissions/{number}``: one submission of the requester. A submission of another account gets 404."""
        principal = request_principal(request)
        try:
            number = ids.parse_public_id(str(request.path_params.get("number") or ""))
        except ValueError:
            raise HTTPException(status_code=404, detail="not found") from None

        def _load() -> dict[str, Any] | None:
            conn = dbm.connect_existing(s.db_file, readonly=True)
            try:
                submission = kb_collab.get_submission(conn, number)
                if submission is None or principal.account_id is None or submission["account_id"] != principal.account_id:
                    return None
                return _submission_view(conn, submission)
            finally:
                conn.close()

        view = await asyncio.to_thread(_load)
        if view is None:
            raise HTTPException(status_code=404, detail="not found")
        return JSONResponse(view)

    # --- Themes (SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md Sections 8.2 to 8.4) ---

    def _theme_view(conn: sqlite3.Connection, theme: dict[str, Any], *, origin: str, admin: bool = False) -> dict[str, Any]:
        """One theme with its knowledge bases: the shared ones of this server first, then the shared ones of other servers, then the attached personal ones (design Section 8.4; KNOWLEDGE_FEDERATION_DESIGN.md Section 8.2 rule 3). ``admin``: also the excluded rows and the flags of the adjustments.

        An entry of a remote knowledge base has ``remote`` true, ``server`` (the domain of its server), ``url`` (its page on that server, the origin link), and ``remote_id`` (the number of the record on this server, for the admin routes). It has no ``number``: a number is an identifier of this server.
        """
        number = ids.format_public_id(int(theme["number"]))
        rows = themes_store.all_members(conn)
        knowledge_bases = []
        contributions_visible = 0
        for member in rows:
            if int(member["theme_id"]) != int(theme["id"]) or (member["excluded"] and not admin):
                continue
            try:
                kb = theme_mgr.get_kb(int(member["kb_id"]))
            except KnowledgeBaseNotFound:
                continue
            if kb.is_personal and not kb.is_public:
                continue
            owner_level = reputation.level(conn, int(kb.account_id)) if kb.is_personal and kb.account_id is not None else None
            if owner_level is not None and not reputation.attaches_to_themes(owner_level) and not admin:
                continue  # Section 8.6: the level ``low`` has no theme attachment
            entry: dict[str, Any] = {"number": ids.format_public_id(kb.number) if kb.number is not None else None, "label": kb.label, "is_personal": kb.is_personal, "knowledge_type": kb.knowledge_type, "url": f"{origin}{kb_base_path(kb.number)}" if (kb.is_shared and kb.number is not None) else None, "remote": False, "server": None}
            if kb.is_shared and not member["excluded"]:
                contributions_visible += len(kb_collab.list_contributions(conn, kb.id, state="visible"))
            if owner_level is not None:
                # Section 8.6: the level ``limited`` is listed after the others with a mark.
                entry["limited"] = owner_level != "good"
            if admin:
                entry.update({"pinned": bool(member["pinned"]), "excluded": bool(member["excluded"]), "source": member["source"], "score": member["score"]})
            knowledge_bases.append(entry)
        for member in themes_store.remote_members(conn, theme_id=int(theme["id"]), include_excluded=admin):
            remote = remote_kbs.get(conn, int(member["remote_kb_id"]))
            if remote is None or federation_store.blocking_domain(conn, remote.domain) is not None:
                continue  # the record went, or a domain block applies to its server
            entry = {"number": None, "label": remote.label, "is_personal": False, "knowledge_type": remote.knowledge_type, "url": remote.url, "remote": True, "server": remote.domain, "remote_id": remote.id}
            if admin:
                entry.update({"pinned": bool(member["pinned"]), "excluded": bool(member["excluded"]), "source": member["source"], "score": member["score"]})
            knowledge_bases.append(entry)
        knowledge_bases.sort(key=lambda e: (e["is_personal"], e["remote"], bool(e.get("limited")), e["label"]))
        try:
            top_entities = json.loads(theme["top_entities"] or "[]")
        except ValueError:
            top_entities = []
        view = {
            "number": number,
            "url": f"{origin}/themes/{number}/",
            "label": theme["label"],
            "description": theme["description"],
            "knowledge_type": theme["knowledge_type"],
            "state": theme["state"],
            "top_entities": top_entities,
            "shared_knowledge_bases": sum(1 for e in knowledge_bases if not e["is_personal"] and not e["remote"] and not e.get("excluded")),
            "remote_knowledge_bases": sum(1 for e in knowledge_bases if e["remote"] and not e.get("excluded")),
            "visible_contributions": contributions_visible,
            "last_activity_at": theme["last_activity_at"],
            "knowledge_bases": knowledge_bases,
        }
        if admin:
            view["label_locked"] = bool(theme["label_locked"])
            view["computed_at"] = theme["computed_at"]
        return view

    def _theme_cards_html(views: list[dict[str, Any]]) -> str:
        """The themes of a public page as cards: the label, the knowledge type, the counts, and the top entities."""
        cards = []
        for view in views:
            entities = ", ".join(html.escape(str(name)) for name in view["top_entities"][:5])
            items = "".join(
                f'<li>{f"""<a href="{html.escape(kb["url"])}">{html.escape(kb["label"])}</a>""" if kb["url"] else html.escape(kb["label"])}'
                f'<span class="themes-count">({html.escape(kb["server"] if kb["remote"] else _t("themes.kb_personal") if kb["is_personal"] else _t("themes.kb_shared"))})</span></li>'
                for kb in view["knowledge_bases"]
            )
            cards.append(
                '<article class="account-card">'
                '<div class="account-header">'
                f'<a class="account-name" href="/themes/{html.escape(view["number"])}/">{html.escape(view["label"])}</a>'
                f'<span class="badge">{html.escape(_t("kb.knowledge_type." + view["knowledge_type"]))} · {html.escape(_t("themes.counts_federated", knowledge_bases=view["shared_knowledge_bases"], remote=view["remote_knowledge_bases"], contributions=view["visible_contributions"]))}</span>'
                "</div>"
                + (f'<p class="subtitle" style="margin:.5rem 0 0;font-size:.88rem;">{entities}</p>' if entities else "")
                + (f'<ul class="themes-list">{items}</ul>' if items else "")
                + "</article>"
            )
        return "".join(cards)

    def _active_theme_views(*, origin: str, knowledge_type: str | None = None) -> list[dict[str, Any]]:
        """The active themes, the one with the most recent activity first (design Section 8.4)."""
        conn = dbm.connect_existing(s.db_file, readonly=True)
        try:
            themes = [t for t in themes_store.list_themes(conn, knowledge_type=knowledge_type) if t["state"] == "active"]
            themes.sort(key=lambda t: (-(t["last_activity_at"] or 0.0), -int(t["number"])))
            return [_theme_view(conn, t, origin=origin) for t in themes]
        finally:
            conn.close()

    async def theme_page_route(request: Request, number: int) -> Response:
        """``/themes/{number}/``: one theme, as JSON or as the HTML page that shows this JSON (content negotiation of the namespace design Section 6.5).

        ``follows`` is true for a reviewer (capability ``review``) of a shared knowledge base of this server that is a member of the theme, and for the admin: then each shared knowledge base of another server has ``follow``, the state of the follow of this server (``pending``, ``accepted``, or null; KNOWLEDGE_FEDERATION_DESIGN.md Section 9.2 rule 2), and the answer has ``Cache-Control: private, no-store``. Each other reader gets ``follows`` false and no follow state: the design gives the follow state to no public page.
        """
        from ..knowledge_federation import outbox as kf_outbox

        origin = _public_origin(request)
        principal = request_principal(request)

        def _sees_follows(view: dict[str, Any]) -> bool:
            if principal.account_id is None:
                return False
            if principal.server_role == "admin":
                return True
            for entry in view["knowledge_bases"]:
                if entry["remote"] or entry["is_personal"] or entry["number"] is None:
                    continue
                try:
                    kb = theme_mgr.get_kb_by_reference(entry["number"])
                except KnowledgeBaseNotFound:
                    continue
                if _kb_decide(request, kb, "review") == "allow":
                    return True
            return False

        def _load() -> dict[str, Any] | None:
            conn = dbm.connect_existing(s.db_file, readonly=True)
            try:
                theme = themes_store.get_theme_by_number(conn, number)
                if theme is None or theme["state"] != "active":
                    return None
                view = _theme_view(conn, theme, origin=origin)
                view["follows"] = _sees_follows(view)
                if view["follows"]:
                    states = kf_outbox.follow_states(conn)
                    for entry in view["knowledge_bases"]:
                        if entry["remote"]:
                            record = remote_kbs.get(conn, int(entry["remote_id"]))
                            entry["follow"] = None if record is None else states.get(record.iri)
                return view
            finally:
                conn.close()

        view = await asyncio.to_thread(_load)
        if view is None:
            raise HTTPException(status_code=404, detail="theme not found")
        if not collections.wants_html(request):
            return JSONResponse(view, headers={"Cache-Control": "private, no-store"} if view["follows"] else None)
        return await asyncio.to_thread(_public_page, request, view="theme_detail", title=view["label"], ref=view["number"])

    def _admin_themes_payload(conn: sqlite3.Connection, *, origin: str) -> dict[str, Any]:
        last = conn.execute("SELECT value FROM server_settings WHERE key=?", (theme_judgement.LAST_RUN_KEY,)).fetchone()
        return {
            "settings": {**theme_judgement.read_settings(conn), **contributions.read_limits(conn)},
            "last_run_at": float(last[0]) if last is not None else None,
            "themes": [_theme_view(conn, t, origin=origin, admin=True) for t in themes_store.list_themes(conn, include_merged=True)],
            # Design Section 9.3 rule 5: the agreement of the decisions of each shared knowledge base with the majority of the others.
            "decision_agreement": [{**_kb_ref(entry["kb_id"]), "decisions": entry["decisions"], "agreement": entry["agreement"]} for entry in contributions.decision_agreement(conn)],
        }

    def _admin_theme_of_path(conn: sqlite3.Connection, request: Request, name: str = "number") -> dict[str, Any]:
        try:
            theme = themes_store.get_theme_by_number(conn, ids.parse_public_id(str(request.path_params.get(name) or "")))
        except ValueError:
            theme = None
        if theme is None:
            raise HTTPException(status_code=404, detail="theme not found")
        return theme

    async def admin_themes_route(request: Request) -> JSONResponse:
        """``GET /api/admin/themes``: each theme with its members and adjustments, the thresholds, and the time of the last run. ``POST``: the admin makes a theme (the split of design Section 8.3: then it pins knowledge bases to it)."""
        origin = _public_origin(request)
        body = await _json_object(request) if request.method == "POST" else {}

        def _work() -> JSONResponse:
            conn = dbm.connect_existing(s.db_file)
            try:
                if request.method == "POST":
                    label, knowledge_type = body.get("label"), body.get("knowledge_type")
                    if not isinstance(label, str) or not label.strip():
                        return JSONResponse({"error": "label_required"}, status_code=400)
                    if knowledge_type not in themes_store.KNOWLEDGE_TYPES:
                        return JSONResponse({"error": "knowledge_type_required"}, status_code=400)
                    theme = themes_store.create_theme(conn, label=label.strip(), knowledge_type=knowledge_type)
                    themes_store.update_theme(conn, int(theme["id"]), label_locked=1)
                    conn.commit()
                    return JSONResponse({"ok": True, "theme": _theme_view(conn, themes_store.get_theme(conn, int(theme["id"])) or theme, origin=origin, admin=True)}, status_code=201)
                return JSONResponse(_admin_themes_payload(conn, origin=origin))
            finally:
                conn.close()

        return await asyncio.to_thread(_work)

    async def admin_themes_settings_route(request: Request) -> JSONResponse:
        """``PUT /api/admin/themes/settings``: the thresholds of the judgement (CT25): ``theme_match_threshold``, ``document_match_threshold``, ``seed_match_threshold``, each from 0 to 1; and the limit ``submission_max_documents``, an integer of 1 or more."""
        body = await _json_object(request)
        principal = request_principal(request)
        unknown = set(body) - set(theme_judgement.SETTINGS) - set(contributions.LIMITS)
        if unknown or not body:
            raise HTTPException(status_code=400, detail="settings_invalid")

        def _work() -> JSONResponse:
            conn = dbm.connect_existing(s.db_file)
            try:
                try:
                    for key, value in body.items():
                        if key in contributions.LIMITS:
                            contributions.write_limit(conn, key, value, updated_by=principal.account_id)
                        else:
                            theme_judgement.write_setting(conn, key, value, updated_by=principal.account_id)
                except ValueError:
                    conn.rollback()
                    return JSONResponse({"error": "settings_invalid"}, status_code=400)
                conn.commit()
                return JSONResponse({"ok": True, "settings": {**theme_judgement.read_settings(conn), **contributions.read_limits(conn)}})
            finally:
                conn.close()

        return await asyncio.to_thread(_work)

    # --- Automatic contribution and mutual collaboration (SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md Sections 9.9, 9.10) ---

    def _run_all_merge_jobs() -> None:
        conn = dbm.connect_existing(s.db_file)
        try:
            contributions.run_pending_merge_jobs(conn, lambda kb_id: service_pool.get_service(kb_id).provider, lambda kb_id: theme_mgr.get_settings_for_kb(kb_id, s))
        finally:
            conn.close()

    def _auto_origin() -> str | None:
        """The canonical origin of this server for a run of an automatic rule: a run can have targets on other servers only with it (federation design Section 12.3). The same value for a request and for a run in the background."""
        return contributions.canonical_origin(s)

    def _auto_run_all() -> int:
        """A run of each active rule, and the merge jobs of the new contributions. For the events of design Section 9.9 rule 4."""
        conn = dbm.connect_existing(s.db_file)
        try:
            made = auto_contribution.run_all(conn, origin=_auto_origin())
        finally:
            conn.close()
        if made:
            _run_all_merge_jobs()
        return made

    def _account_id_of(request: Request) -> int:
        principal = request_principal(request)
        if principal.account_id is None:
            raise HTTPException(status_code=404, detail="not found")
        return int(principal.account_id)

    def _rule_view(conn: sqlite3.Connection, rule: dict[str, Any] | None) -> dict[str, Any] | None:
        if rule is None:
            return None
        themes = [themes_store.get_theme(conn, theme_id) for theme_id in rule["theme_ids"]]
        return {
            "state": rule["state"],
            "memberships": bool(rule["to_memberships"]),
            "mutual": bool(rule["to_mutual"]),
            "include_uploads": bool(rule["include_uploads"]),
            "themes": [{"number": ids.format_public_id(int(t["number"])), "label": t["label"]} for t in themes if t is not None],
            "activated_at": rule["activated_at"],
        }

    def _rule_from_body(conn: sqlite3.Connection, body: dict[str, Any]) -> dict[str, Any]:
        """The rule that a body gives: ``{"state": "active" | "paused", "memberships", "mutual", "include_uploads": bool, "themes": ["<number>", ...]}``. Each field is required: there is no default for a rule that sends documents."""
        if set(body) != {"state", "memberships", "mutual", "include_uploads", "themes"} or body["state"] not in ("active", "paused"):
            raise HTTPException(status_code=400, detail="rule_invalid")
        if not all(isinstance(body[name], bool) for name in ("memberships", "mutual", "include_uploads")) or not isinstance(body["themes"], list):
            raise HTTPException(status_code=400, detail="rule_invalid")
        theme_ids = []
        for number in body["themes"]:
            try:
                theme = themes_store.get_theme_by_number(conn, ids.parse_public_id(str(number)))
            except ValueError:
                theme = None
            if theme is None or theme["state"] != "active":
                raise HTTPException(status_code=404, detail="theme_not_found")
            theme_ids.append(int(theme["id"]))
        return {"state": body["state"], "to_memberships": body["memberships"], "to_mutual": body["mutual"], "include_uploads": body["include_uploads"], "theme_ids": sorted(set(theme_ids))}

    def _is_wider(new: dict[str, Any], old: dict[str, Any] | None) -> bool:
        """True when the new rule turns on, or sends to more targets or more documents than the old rule (design Section 9.9 rule 6)."""
        if new["state"] != "active":
            return False
        if old is None or old["state"] != "active":
            return True
        return any(new[name] and not old[name] for name in ("to_memberships", "to_mutual", "include_uploads")) or bool(set(new["theme_ids"]) - set(old["theme_ids"]))

    async def auto_rule_route(request: Request) -> Response:
        """``GET /api/auto-rule``: the automatic rule of the requester and the server switch. ``PUT``: set the rule. To turn the rule on or to make it wider needs an ``owner`` session with a recent sign-in."""
        account_id = _account_id_of(request)
        if request.method != "PUT":
            def _load() -> dict[str, Any]:
                conn = dbm.connect_existing(s.db_file, readonly=True)
                try:
                    return {"server_switch": auto_contribution.switch_on(conn), "rule": _rule_view(conn, kb_collab.get_auto_rule(conn, account_id))}
                finally:
                    conn.close()

            return JSONResponse(await asyncio.to_thread(_load))
        body = await _json_object(request)
        principal = request_principal(request)

        def _save() -> Response:
            conn = dbm.connect_existing(s.db_file)
            try:
                rule = _rule_from_body(conn, body)
                if _is_wider(rule, kb_collab.get_auto_rule(conn, account_id)):
                    refused = step_up_response(principal)
                    if refused is not None:
                        return refused
                kb_collab.save_auto_rule(conn, account_id=account_id, **rule)
                conn.commit()
                made = auto_contribution.run_account(conn, account_id, origin=_auto_origin())  # rule 4: when the rule is turned on
                view = _rule_view(conn, kb_collab.get_auto_rule(conn, account_id))
                switch = auto_contribution.switch_on(conn)
            finally:
                conn.close()
            if made:
                _run_all_merge_jobs()
            return JSONResponse({"ok": True, "server_switch": switch, "rule": view, "contributions": made})

        return await asyncio.to_thread(_save)

    def _plan_target_view(target: dict[str, Any]) -> dict[str, Any]:
        """One target of ``auto_contribution.plan`` for a response. A target on another server (federation design Section 12.3) has ``remote`` true, its server, the page on that server, the actor, and the ID of the record; ``knowledge_base`` is null."""
        if "remote_id" in target:
            return {"knowledge_base": None, "label": target["label"], "remote": True, "remote_id": target["remote_id"], "server": target["server"], "url": target["url"], "actor": target["kb_iri"], "mode": target["mode"], "documents": target["documents"]}
        return {**_kb_ref(target["kb_id"]), "remote": False, "mode": target["mode"], "documents": target["documents"]}

    async def auto_rule_dry_run_route(request: Request) -> JSONResponse:
        """``POST /api/auto-rule/dry-run`` with the body of a rule: the documents that the rule would send now to each target (design Section 9.9 rule 6). It writes nothing.

        The targets on other servers (federation design Section 12.3) are in ``targets`` with ``remote`` true; ``remote_state`` is ``on``, or the reason that the rule has no target on another server now (``auto_contribution.REMOTE_STATES``).
        """
        account_id = _account_id_of(request)
        body = await _json_object(request)

        def _plan() -> dict[str, Any]:
            conn = dbm.connect_existing(s.db_file, readonly=True)
            try:
                rule = _rule_from_body(conn, body)
                origin = _auto_origin()
                targets = [_plan_target_view(t) for t in auto_contribution.plan(conn, account_id, rule, origin=origin)]
                return {"server_switch": auto_contribution.switch_on(conn), "remote_state": auto_contribution.remote_state(conn, account_id, origin), "targets": targets}
            finally:
                conn.close()

        return JSONResponse(await asyncio.to_thread(_plan))

    def _automatic_offer_view(conn: sqlite3.Connection, row: dict[str, Any]) -> dict[str, Any]:
        """One automatic contribution to a shared knowledge base of another server (federation design Section 12.3) in the list of ``GET /api/contributions``: the keys of a contribution that the other server decides, and ``remote_target`` with the server, the page, the actor, the ``result`` of an ``Accept``, and the delivery state. It has no number of this server and no withdrawal: the vocabulary has no ``Undo`` of an ``Offer``."""
        target = kf_offers.outbound_view(conn, row)
        return {
            "knowledge_base": None,
            "label": target["label"],
            "number": None,
            "document": row["source_document_id"],
            "title": _document_title(conn, row["source_document_id"]),
            "origin": "auto",
            "state": target["state"],
            "approved": target["approved"],
            "reason": target["reason"],
            "contributed_at": row["created_at"],
            "remote_target": {key: target[key] for key in ("remote_id", "server", "url", "actor", "result", "with_grant", "delivery", "decided_at")},
        }

    async def account_contributions_route(request: Request) -> Response:
        """``GET /api/contributions``: each contribution with the credit of the requester, the newest first. ``?origin=`` selects one origin (one of ``kb_collab.CONTRIBUTION_ORIGINS``); another value gets 400 ``origin_invalid``.

        Without ``origin`` and with ``origin=auto``, the list also has the automatic contributions to shared knowledge bases of other servers (federation design Section 12.3), with ``remote_target``; their state is the decision of the other server.
        """
        account_id = _account_id_of(request)
        origin = request.query_params.get("origin")
        if origin is not None and origin not in kb_collab.CONTRIBUTION_ORIGINS:
            return JSONResponse({"error": "origin_invalid"}, status_code=400)

        def _list() -> list[dict[str, Any]]:
            conn = dbm.connect_existing(s.db_file, readonly=True)
            try:
                items = [{**_kb_ref(int(row["kb_id"])), **_contribution_view(conn, row, full=True)} for row in kb_collab.contributions_of_account(conn, account_id, origin=origin)]
                if origin in (None, "auto"):
                    items += [_automatic_offer_view(conn, row) for row in kf_offers.automatic_of_account(conn, account_id)]
                    items.sort(key=lambda item: -float(item["contributed_at"] or 0.0))
                return items
            finally:
                conn.close()

        items = await asyncio.to_thread(_list)
        return _collection_response(request, collection_url=f"{_public_origin(request)}/api/contributions", items=items, extra={"contributions": items})

    async def contribution_blocks_route(request: Request) -> JSONResponse:
        """``GET /api/contribution-blocks``: the documents of the requester with the flag "never contribute"."""
        account_id = _account_id_of(request)

        def _list() -> list[str]:
            conn = dbm.connect_existing(s.db_file, readonly=True)
            try:
                return sorted(kb_collab.contribution_blocks(conn, account_id))
            finally:
                conn.close()

        return JSONResponse({"documents": await asyncio.to_thread(_list)})

    async def contribution_block_route(request: Request) -> JSONResponse:
        """``PUT`` / ``DELETE /api/contribution-blocks/{document}``: set or remove the flag "never contribute" of a document of the personal knowledge base of the requester."""
        account_id = _account_id_of(request)
        document_id = str(request.path_params.get("document") or "")

        def _set() -> JSONResponse:
            try:
                personal = theme_mgr.personal_kb_for(account_id)
            except KnowledgeBaseNotFound:
                return JSONResponse({"error": "document_not_found"}, status_code=404)
            conn = dbm.connect_existing(s.db_file)
            try:
                if conn.execute("SELECT 1 FROM documents WHERE id=? AND kb_id=?", (document_id, personal.id)).fetchone() is None:
                    return JSONResponse({"error": "document_not_found"}, status_code=404)
                kb_collab.set_contribution_block(conn, account_id=account_id, document_id=document_id, blocked=request.method == "PUT")
                conn.commit()
                return JSONResponse({"ok": True, "document": document_id, "blocked": request.method == "PUT"})
            finally:
                conn.close()

        return await asyncio.to_thread(_set)

    def _mutual_view(conn: sqlite3.Connection, account_id: int) -> list[dict[str, Any]]:
        out = []
        store = accounts_store.AccountStore(conn)
        for row in kb_collab.mutual_rows(conn, account_id):
            try:
                other = store.get(int(row["account_id"]))
            except accounts_store.AccountNotFound:
                continue
            out.append({"account": ids.format_public_id(other.public_id), "display_name": other.display_name, "state": row["state"], "requested_by_me": row["requested_by_me"], "created_at": row["created_at"], "decided_at": row["decided_at"]})
        return out

    def _other_account(conn: sqlite3.Connection, reference: Any, account_id: int) -> int | None:
        """The active account that a public number names, when it is not the requester."""
        try:
            other = accounts_store.AccountStore(conn).find_by_public_id(ids.parse_public_id(str(reference or "")))
        except ValueError:
            return None
        return int(other.id) if other is not None and other.status == "active" and int(other.id) != account_id else None

    async def mutual_collaborations_route(request: Request) -> Response:
        """``GET /api/mutual-collaborations``: the mutual collaborations and requests of the requester. ``POST`` with ``{"account": "<number>"}``: request one (a recent sign-in)."""
        account_id = _account_id_of(request)
        if request.method != "POST":
            def _list() -> list[dict[str, Any]]:
                conn = dbm.connect_existing(s.db_file, readonly=True)
                try:
                    return _mutual_view(conn, account_id)
                finally:
                    conn.close()

            return JSONResponse({"mutual_collaborations": await asyncio.to_thread(_list)})
        body = await _json_object(request)
        refused = step_up_response(request_principal(request))
        if refused is not None:
            return refused

        def _request() -> JSONResponse:
            conn = dbm.connect_existing(s.db_file)
            try:
                other = _other_account(conn, body.get("account"), account_id)
                if other is None:
                    return JSONResponse({"error": "account_not_found"}, status_code=404)
                try:
                    kb_collab.request_mutual(conn, requester=account_id, other=other)
                except ValueError:
                    return JSONResponse({"error": "mutual_exists"}, status_code=409)
                conn.commit()
                return JSONResponse({"ok": True, "mutual_collaborations": _mutual_view(conn, account_id)}, status_code=201)
            finally:
                conn.close()

        return await asyncio.to_thread(_request)

    async def mutual_collaboration_route(request: Request) -> Response:
        """``PUT /api/mutual-collaborations/{account}``: accept the request of that account (a recent sign-in). ``DELETE``: end the mutual collaboration, or remove the request. The end does not change roles that it gave (design Section 9.10 rule 3)."""
        account_id = _account_id_of(request)
        accept = request.method == "PUT"
        if accept:
            refused = step_up_response(request_principal(request))
            if refused is not None:
                return refused

        def _change() -> tuple[JSONResponse, bool]:
            conn = dbm.connect_existing(s.db_file)
            try:
                other = _other_account(conn, request.path_params.get("account"), account_id)
                changed = other is not None and (kb_collab.accept_mutual(conn, account_id=account_id, other=other) if accept else kb_collab.end_mutual(conn, account_id, other))
                if not changed:
                    return JSONResponse({"error": "mutual_not_found"}, status_code=404), False
                conn.commit()
                made = 0
                if accept:
                    # Section 9.9 rule 4: when a mutual collaboration starts, the rules of the two accounts run.
                    made = auto_contribution.run_account(conn, account_id, origin=_auto_origin()) + auto_contribution.run_account(conn, int(other), origin=_auto_origin())
                return JSONResponse({"ok": True, "mutual_collaborations": _mutual_view(conn, account_id)}), bool(made)
            finally:
                conn.close()

        def _work() -> JSONResponse:
            response, made = _change()
            if made:
                _run_all_merge_jobs()
            return response

        return await asyncio.to_thread(_work)

    async def admin_auto_contribution_route(request: Request) -> JSONResponse:
        """``GET /api/admin/auto-contribution``: the server switch and its settings. ``PUT`` with ``{"state": "on" | "off"}`` and the settings ``auto_match_threshold``, ``auto_run_documents``. While the switch is off, no run starts and each rule keeps its settings."""
        principal = request_principal(request)
        body = await _json_object(request) if request.method == "PUT" else {}
        if request.method == "PUT" and (not body or set(body) - {"state", *auto_contribution.SETTINGS} or ("state" in body and body["state"] not in ("on", "off"))):
            raise HTTPException(status_code=400, detail="settings_invalid")

        def _work() -> tuple[JSONResponse, bool]:
            conn = dbm.connect_existing(s.db_file)
            try:
                turned_on = False
                try:
                    for key, value in body.items():
                        if key == "state":
                            turned_on = value == "on" and not auto_contribution.switch_on(conn)
                            auto_contribution.set_switch(conn, value == "on", updated_by=principal.account_id)
                        else:
                            auto_contribution.write_setting(conn, key, value, updated_by=principal.account_id)
                except ValueError:
                    conn.rollback()
                    return JSONResponse({"error": "settings_invalid"}, status_code=400), False
                conn.commit()
                return JSONResponse({"ok": True, "state": "on" if auto_contribution.switch_on(conn) else "off", "settings": auto_contribution.read_settings(conn)}), turned_on
            finally:
                conn.close()

        def _run() -> JSONResponse:
            response, turned_on = _work()
            if turned_on:
                _auto_run_all()
            return response

        return await asyncio.to_thread(_run)

    # --- Reputation (SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md Section 8.6) ---

    async def reputation_route(request: Request) -> JSONResponse:
        """``GET /api/reputation``: the account sees its level, its signals, its limits, and each fiction-like document that counts, with its matched theme. No other account sees it.

        ``limits``: ``pending`` is the number of pending contributions in one shared knowledge base; ``auto_documents`` is the number of documents of one run and target of the automatic contribution (null: no limit; 0: paused).
        """
        principal = request_principal(request)
        if principal.account_id is None:
            raise HTTPException(status_code=404, detail="not found")
        account_id = int(principal.account_id)

        def _load() -> dict[str, Any]:
            conn = dbm.connect_existing(s.db_file)
            try:
                current = reputation.refresh(conn, account_id)
                conn.commit()
                documents = []
                for document_id in current.fiction_like_documents:
                    match = themes_store.get_document_match(conn, document_id)
                    theme = themes_store.get_theme(conn, int(match["theme_id"])) if match is not None and match["theme_id"] is not None else None
                    title = conn.execute("SELECT title FROM documents WHERE id=?", (document_id,)).fetchone()
                    documents.append({"document": document_id, "title": None if title is None else title[0], "theme": None if theme is None else ids.format_public_id(int(theme["number"])), "theme_label": None if theme is None else theme["label"]})
                settings = reputation.read_settings(conn)
                limits = {"pending": reputation.pending_limit(settings, current.level), "auto_documents": reputation.auto_contribution_limit(settings, current.level)}
                return {**current.to_dict(), "limits": limits, "documents": documents}
            finally:
                conn.close()

        return JSONResponse(await asyncio.to_thread(_load))

    async def admin_reputations_route(request: Request) -> JSONResponse:
        """``GET /api/admin/reputations``: the admin sees the level of each active account, and the settings."""

        def _load() -> dict[str, Any]:
            conn = dbm.connect_existing(s.db_file)
            try:
                rows = []
                for account in conn.execute("SELECT id, public_id, display_name FROM accounts WHERE status='active' ORDER BY id").fetchall():
                    current = reputation.refresh(conn, int(account["id"]))
                    rows.append({"account": ids.format_public_id(int(account["public_id"])), "display_name": account["display_name"], **current.to_dict()})
                conn.commit()
                return {"settings": reputation.read_settings(conn), "accounts": rows}
            finally:
                conn.close()

        return JSONResponse(await asyncio.to_thread(_load))

    async def admin_reputation_settings_route(request: Request) -> JSONResponse:
        """``PUT /api/admin/reputations/settings``: the numbers of the reputation (the keys of ``reputation.SETTINGS``)."""
        body = await _json_object(request)
        principal = request_principal(request)
        if not body or set(body) - set(reputation.SETTINGS):
            raise HTTPException(status_code=400, detail="settings_invalid")

        def _work() -> JSONResponse:
            conn = dbm.connect_existing(s.db_file)
            try:
                try:
                    for key, value in body.items():
                        reputation.write_setting(conn, key, value, updated_by=principal.account_id)
                except ValueError:
                    conn.rollback()
                    return JSONResponse({"error": "settings_invalid"}, status_code=400)
                conn.commit()
                return JSONResponse({"ok": True, "settings": reputation.read_settings(conn)})
            finally:
                conn.close()

        return await asyncio.to_thread(_work)

    def _shadow_record_view(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": int(row["id"]),
            "parent": {"document_id": row["parent_document_id"], "title": row["parent_title"], "url": row["parent_url"]},
            "child": {"document_id": row["child_document_id"], "title": row["child_title"], "url": row["child_url"]},
            "same_subject": bool(row["same_subject"]),
            "relevance": row["relevance"],
            "quality": row["quality"],
            "interpretation": row["interpretation"] or "",
            "reason": row["reason"] or "",
            "created_at": row["created_at"],
            "verdict": row["verdict"],
            "verdict_note": row["verdict_note"] or "",
            "verdict_at": row["verdict_at"],
        }

    async def admin_expansion_shadow_route(request: Request) -> JSONResponse:
        """``GET /api/admin/expansion/shadow``: the shadow records of the same-subject source merge in the knowledge bases of the admin account, and the report (docs/implementation/EXPANSION.md §3.1). Query: ``pending=1`` (only records without a verdict), ``limit`` (default 50, 0 = all)."""
        principal = request_principal(request)
        pending = request.query_params.get("pending") == "1"
        try:
            limit = int(request.query_params.get("limit", "50"))
        except ValueError:
            raise HTTPException(status_code=400, detail="limit_invalid") from None
        if limit < 0:
            raise HTTPException(status_code=400, detail="limit_invalid")

        def _load() -> dict[str, Any]:
            conn = dbm.connect_existing(s.db_file)
            try:
                rows = dbm.list_same_subject_shadow(conn, kb_id=None, account_id=principal.account_id, limit=limit, pending=pending)
                report = dbm.same_subject_shadow_report(conn, kb_id=None, account_id=principal.account_id)
            finally:
                conn.close()
            return {"mode": s.same_subject_merge, "report": report, "records": [_shadow_record_view(r) for r in rows]}

        return JSONResponse(await asyncio.to_thread(_load))

    async def admin_expansion_shadow_verdict_route(request: Request) -> JSONResponse:
        """``PUT /api/admin/expansion/shadow/{record_id}/verdict``: the server administrator gives the human verdict of one record. Body: ``{"verdict": "same" | "different" | null, "note": "..."}``. Answer: ``{"ok", "record", "report"}``; 404 ``shadow_record_not_found``; 400 ``verdict_invalid``."""
        principal = request_principal(request)
        try:
            record_id = int(request.path_params["record_id"])
        except (KeyError, ValueError):
            raise HTTPException(status_code=404, detail="shadow_record_not_found") from None
        body = await _json_object(request)
        if set(body) - {"verdict", "note"} or "verdict" not in body:
            raise HTTPException(status_code=400, detail="verdict_invalid")
        verdict = body["verdict"]
        note = body.get("note") or ""
        if verdict is not None and verdict not in dbm.SAME_SUBJECT_VERDICTS:
            raise HTTPException(status_code=400, detail="verdict_invalid")
        if not isinstance(note, str) or len(note) > 500:
            raise HTTPException(status_code=400, detail="verdict_invalid")

        def _work() -> JSONResponse:
            conn = dbm.connect_existing(s.db_file)
            try:
                if not dbm.set_same_subject_verdict(conn, record_id, verdict, verdict_by=principal.account_id, note=note, account_id=principal.account_id):
                    return JSONResponse({"error": "shadow_record_not_found"}, status_code=404)
                row = next(r for r in dbm.list_same_subject_shadow(conn, kb_id=None, account_id=principal.account_id) if int(r["id"]) == record_id)
                report = dbm.same_subject_shadow_report(conn, kb_id=None, account_id=principal.account_id)
            finally:
                conn.close()
            return JSONResponse({"ok": True, "record": _shadow_record_view(row), "report": report})

        return await asyncio.to_thread(_work)

    async def admin_themes_judge_route(request: Request) -> JSONResponse:
        """``POST /api/admin/themes/judge``: the admin starts a run of the judgement (design Section 8.2 rule 10)."""

        def _work() -> JSONResponse:
            conn = dbm.connect_existing(s.db_file)
            try:
                report = theme_judgement.run_judgement(conn)
                conn.commit()
            finally:
                conn.close()
            _auto_run_all()  # design Section 9.9 rule 4: after each judgement run
            return JSONResponse({"ok": True, "report": dataclasses.asdict(report)})

        return await asyncio.to_thread(_work)

    async def admin_theme_route(request: Request) -> JSONResponse:
        """``PATCH /api/admin/themes/{number}``: ``{"label": "..."}`` renames the theme and locks the label; ``{"label_locked": false}`` gives the label back to the judgement (design Section 8.3)."""
        body = await _json_object(request)
        origin = _public_origin(request)

        def _work() -> JSONResponse:
            conn = dbm.connect_existing(s.db_file)
            try:
                theme = _admin_theme_of_path(conn, request)
                changes: dict[str, Any] = {}
                if "label" in body:
                    if not isinstance(body["label"], str) or not body["label"].strip():
                        return JSONResponse({"error": "label_invalid"}, status_code=400)
                    changes.update({"label": body["label"].strip(), "label_locked": 1})
                if "label_locked" in body:
                    if not isinstance(body["label_locked"], bool):
                        return JSONResponse({"error": "label_locked_invalid"}, status_code=400)
                    changes["label_locked"] = 1 if body["label_locked"] else 0
                if not changes:
                    return JSONResponse({"error": "label_required"}, status_code=400)
                themes_store.update_theme(conn, int(theme["id"]), **changes)
                conn.commit()
                return JSONResponse({"ok": True, "theme": _theme_view(conn, themes_store.get_theme(conn, int(theme["id"])) or theme, origin=origin, admin=True)})
            finally:
                conn.close()

        return await asyncio.to_thread(_work)

    async def admin_theme_member_route(request: Request) -> JSONResponse:
        """``PUT /api/admin/themes/{number}/knowledge-bases/{kb}`` with ``{"membership": "pinned" | "excluded" | "computed"}`` (design Section 8.3): pin a knowledge base to the theme, exclude it from the theme, or give the decision back to the judgement."""
        body = await _json_object(request)
        membership = body.get("membership")
        if membership not in ("pinned", "excluded", "computed"):
            raise HTTPException(status_code=400, detail="membership_invalid")
        origin = _public_origin(request)
        try:
            kb = theme_mgr.get_kb_by_reference(request.path_params.get("kb"))
        except KnowledgeBaseNotFound:
            raise HTTPException(status_code=404, detail="theme not found") from None

        def _work() -> JSONResponse:
            conn = dbm.connect_existing(s.db_file)
            try:
                theme = _admin_theme_of_path(conn, request)
                if theme["state"] == "merged":
                    return JSONResponse({"error": "theme_merged"}, status_code=409)
                if membership == "pinned" and themes_store.knowledge_type_for_themes(conn, kb.id) != theme["knowledge_type"]:
                    # A theme has one knowledge type (CT18).
                    return JSONResponse({"error": "knowledge_type_mismatch"}, status_code=409)
                themes_store.set_admin_member(conn, theme_id=int(theme["id"]), kb_id=kb.id, pinned=membership == "pinned", excluded=membership == "excluded")
                if membership == "pinned":
                    themes_store.update_theme(conn, int(theme["id"]), state="active")
                conn.commit()
                return JSONResponse({"ok": True, "theme": _theme_view(conn, themes_store.get_theme(conn, int(theme["id"])) or theme, origin=origin, admin=True)})
            finally:
                conn.close()

        return await asyncio.to_thread(_work)

    async def admin_theme_remote_member_route(request: Request) -> JSONResponse:
        """``PUT /api/admin/themes/{number}/remote-knowledge-bases/{remote_id}`` with ``{"membership": "pinned" | "excluded" | "computed"}``: the adjustments of ``admin_theme_member_route`` for a shared knowledge base of another server (KNOWLEDGE_FEDERATION_DESIGN.md Section 8.2). ``remote_id`` is the number of the record in the store of remote knowledge bases (``remote_id`` of the theme view). The adjustment changes only this server (Section 6.4 rule 3)."""
        body = await _json_object(request)
        membership = body.get("membership")
        if membership not in ("pinned", "excluded", "computed"):
            raise HTTPException(status_code=400, detail="membership_invalid")
        origin = _public_origin(request)
        raw_id = str(request.path_params.get("remote_id") or "")
        if not (raw_id.isascii() and raw_id.isdigit() and raw_id[0] != "0" and len(raw_id) <= 18):
            raise HTTPException(status_code=404, detail="theme not found")

        def _work() -> JSONResponse:
            conn = dbm.connect_existing(s.db_file)
            try:
                theme = _admin_theme_of_path(conn, request)
                remote = remote_kbs.get(conn, int(raw_id))
                if remote is None or federation_store.blocking_domain(conn, remote.domain) is not None:
                    raise HTTPException(status_code=404, detail="theme not found")
                if theme["state"] == "merged":
                    return JSONResponse({"error": "theme_merged"}, status_code=409)
                if membership == "pinned" and remote.knowledge_type != theme["knowledge_type"]:
                    # A theme has one knowledge type (CT18).
                    return JSONResponse({"error": "knowledge_type_mismatch"}, status_code=409)
                themes_store.set_admin_remote_member(conn, theme_id=int(theme["id"]), remote_kb_id=remote.id, pinned=membership == "pinned", excluded=membership == "excluded")
                if membership == "pinned":
                    themes_store.update_theme(conn, int(theme["id"]), state="active")
                conn.commit()
                return JSONResponse({"ok": True, "theme": _theme_view(conn, themes_store.get_theme(conn, int(theme["id"])) or theme, origin=origin, admin=True)})
            finally:
                conn.close()

        return await asyncio.to_thread(_work)

    async def admin_theme_merge_route(request: Request) -> JSONResponse:
        """``POST /api/admin/themes/{number}/merge`` with ``{"into": "<number>"}`` (design Section 8.3): the theme gets the state ``merged``, its members are pinned to the other theme, and its page gives 308 to the other theme."""
        body = await _json_object(request)
        origin = _public_origin(request)

        def _work() -> JSONResponse:
            conn = dbm.connect_existing(s.db_file)
            try:
                source = _admin_theme_of_path(conn, request)
                try:
                    target = themes_store.get_theme_by_number(conn, ids.parse_public_id(str(body.get("into") or "")))
                except ValueError:
                    target = None
                if target is None:
                    return JSONResponse({"error": "into_invalid"}, status_code=400)
                try:
                    themes_store.merge_themes(conn, source_id=int(source["id"]), target_id=int(target["id"]))
                except ValueError as exc:
                    conn.rollback()
                    return JSONResponse({"error": str(exc)}, status_code=409)
                conn.commit()
                return JSONResponse({"ok": True, "theme": _theme_view(conn, themes_store.get_theme(conn, int(target["id"])) or target, origin=origin, admin=True)})
            finally:
                conn.close()

        return await asyncio.to_thread(_work)

    def _load_server_tier_payload() -> dict[str, Any]:
        """Read current ServerTier configuration for /providers responses."""
        try:
            db_path = Path(s.db_file)
            if db_path.is_file():
                conn = dbm.connect_existing(db_path, readonly=True)
                try:
                    raw = server_tier.read_stored_server_tier(conn)
                    return {
                        "configured": raw is not None,
                        "tier": server_tier.load_server_tier(conn).to_dict(),
                    }
                finally:
                    conn.close()
        except Exception as exc:
            log.debug("providers server_tier read skipped: %s", exc)
        return {"configured": False, "tier": policy.ServerTier().to_dict()}

    def _save_server_tier_patch(patch: dict[str, Any]) -> dict[str, Any]:
        """Merge provider/model/max_effort/features fields from /providers PATCH onto ServerTier."""
        db_path = Path(s.db_file)
        if not db_path.is_file():
            return {"configured": False, "tier": policy.ServerTier().to_dict()}
        conn = dbm.connect_existing(db_path)
        try:
            current = server_tier.load_server_tier(conn).to_dict()
            for key in ("provider", "model", "max_effort", "features"):
                if key in patch:
                    current[key] = patch[key]
            validated = policy.ServerTier.from_dict(current)
            server_tier.save_server_tier(conn, validated, updated_by=dbm.ADMIN_ACCOUNT_ID)
            return {"configured": True, "tier": validated.to_dict()}
        finally:
            conn.close()

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
        tier_payload = _load_server_tier_payload()
        cfg["server_tier"] = tier_payload["tier"]
        cfg["server_tier_configured"] = tier_payload["configured"]
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
            updated = pm.update_config(body, server_scope=True)
            tier_payload = _save_server_tier_patch(body["server_tier"]) if isinstance(body.get("server_tier"), dict) else _load_server_tier_payload()
            get_settings.cache_clear()
            s = get_settings()
            if hasattr(request, "app") and hasattr(request.app, "state"):
                request.app.state.settings = s
            # 캐시된 서비스·프로바이더(관리자 공유 포함)를 버리고 새 설정으로 다시 만든다.
            service_pool.clear(s)
            updated["effective_provider"] = s.effective_provider
            updated["effective_stt_provider"] = s.effective_stt_provider
            updated["server_tier"] = tier_payload["tier"]
            updated["server_tier_configured"] = tier_payload["configured"]
            return JSONResponse({"ok": True, "config": updated})
        except ValueError as val_err:
            raise HTTPException(status_code=400, detail=str(val_err)) from val_err
        except Exception as exc:
            _log_operation_failure(request, "provider update", exc)
            raise HTTPException(status_code=500, detail="failed to update provider configuration") from exc

    def _require_admin_browser_session(request: Request) -> None:
        """Require an authenticated server-admin browser cookie session and same-origin request."""
        principal = request_principal(request)
        if principal.channel != "cookie" or principal.server_role != "admin" or principal.scope != "owner":
            raise HTTPException(
                status_code=403,
                detail="서버 관리자 브라우저 세션이 필요합니다.",
            )
        if not same_origin(request):
            raise HTTPException(
                status_code=403,
                detail="same-origin 요청만 허용됩니다.",
            )

    async def providers_test_route(request: Request) -> JSONResponse:
        _require_admin_browser_session(request)
        body = await _json_object(request)
        provider_name = str(body.get("provider") or "").strip().lower()
        if not provider_name:
            raise HTTPException(status_code=400, detail="provider is required")
        from ..provider_manager import get_provider_manager

        pm = get_provider_manager(s.data_dir)
        override = body.get("config") if isinstance(body.get("config"), dict) else None
        res = pm.test_connection(provider_name, config=override)
        return JSONResponse(res)

    async def providers_check_route(request: Request) -> JSONResponse:
        _require_admin_browser_session(request)
        body = await _json_object(request)
        from ..provider_manager import get_provider_manager

        pm = get_provider_manager(s.data_dir)
        live = bool(body.get("live", False))
        refresh_agy = bool(body.get("refresh_agy", False))
        caps = pm.check_capabilities(body, live=live, refresh_agy=refresh_agy)
        return JSONResponse(caps)

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
            item.pop("scope_notice", None)
            sanitized.append(item)
        # SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md Section 6.6 rule 2: the tokens whose scope ``collaborator`` became ``readonly`` are listed once.
        notices = store.take_scope_notices(target_account_id) if request.method == "GET" else []
        return JSONResponse({"tokens": sanitized, "scope_notices": notices})

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
        theme, theme_settings, theme_svc = _get_personal_kb_ctx(request)
        scope = request_auth_scope(request)
        payload = str(body.get("payload") or "").strip()
        if not payload:
            raise HTTPException(status_code=400, detail="payload required")
        expand_max = body.get("expand_max")
        format_arg = str(body.get("format") or "").strip() or None
        # CT28: the focus is sealed. The parser still separates the payload from the text after a separator; the text is ignored.
        from ..telegram_bot import parse_message_focus

        payload, _sealed_focus = parse_message_focus(payload)

        full_content = bool(body.get("full_content") or body.get("no_truncate") or False)
        effort = _validate_request_effort(theme_svc, theme_settings, str(body.get("effort") or "").strip() or None)

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
        rep_dict["kb_id"] = theme.id
        rep_dict["theme_label"] = theme.label
        return JSONResponse(rep_dict)

    async def federation_timeline_ingest_route(request: Request) -> JSONResponse:
        """``POST /api/federation/timeline/{item_id}/ingest`` (FEDERATION_REMOTE_READING_DESIGN.md Section 4.4, unit FP4-5).

        Start the existing URL ingest for one item of the timeline of the caller, into a theme of the caller. The item stays in the timeline and gets the link to the new document (P2). Collection is not ingestion: only this action of the owner brings a received post into a theme.
        """
        from ..store import client_db
        from ..store import federation_client as federation_client_store
        from ..store import feeds as feeds_store

        principal = request_principal(request)
        if principal.scope != "owner" or principal.account_id is None:
            raise HTTPException(status_code=404, detail="Not Found")
        account_id = principal.account_id
        raw_id = request.path_params["item_id"]
        if not (raw_id.isascii() and raw_id.isdigit() and raw_id[0] != "0" and len(raw_id) <= 18):
            return JSONResponse({"error": "item_not_found"}, status_code=404)
        body = await _json_object(request)
        # Ingest writes only to the personal knowledge base of the caller (design Section 5.2 rule 3). A ``theme`` or ``kb_id`` field is ignored.
        theme, _, theme_svc = _get_personal_kb_ctx(request)
        # The owner must be able to write to the theme: the theme is of the account of the caller.
        if getattr(theme, "account_id", None) is not None and theme.account_id != account_id:
            raise HTTPException(status_code=404, detail="theme not found")
        client_path = client_db.client_db_path(s.db_file)

        def _load() -> Any:
            client = client_db.connect_client_existing(client_path, readonly=True)
            try:
                return federation_client_store.get_timeline_item(client, account_id, int(raw_id))
            finally:
                client.close()

        item = await asyncio.to_thread(_load)
        if item is None:
            return JSONResponse({"error": "item_not_found"}, status_code=404)
        ingest_kwargs: dict[str, Any] = {"source": "federation"}
        try:
            report = await _run_expensive(theme_svc.ingest, item.url or item.object_iri, **ingest_kwargs)
        except (HTTPException, policy.ProviderPolicyError):
            raise
        except Exception as exc:  # noqa: BLE001
            _log_operation_failure(request, "federation_timeline_ingest", exc)
            return JSONResponse({"error": "ingest failed", "ok": False}, status_code=500)
        rep_dict = report_to_dict(report)
        rep_dict["kb_id"] = theme.id
        rep_dict["theme_label"] = theme.label
        document_id = getattr(report, "document_id", None)
        if document_id and not getattr(report, "error", None):

            def _link() -> None:
                client = client_db.connect_client_existing(client_path)
                try:
                    federation_client_store.link_timeline_item(client, account_id, item.id, kb_id=theme.id, doc_id=str(document_id))
                    # Merge by URL (unit FP4-F3): a feed item with the address of this post gets the link too.
                    feeds_store.link_by_url(client, account_id, [item.url, item.object_iri], kb_id=theme.id, doc_id=str(document_id), timeline=False)
                    client.commit()
                finally:
                    client.close()

            await asyncio.to_thread(_link)
            rep_dict["linked"] = {"kb_id": theme.id, "document_id": str(document_id)}
        else:
            rep_dict["linked"] = None
        return JSONResponse(rep_dict)

    async def feed_item_ingest_route(request: Request) -> JSONResponse:
        """``POST /api/feeds/items/{item_id}/ingest`` (FEDERATION_REMOTE_READING_DESIGN.md Sections 1.3 rule 7 and 4.4, unit FP4-F3).

        Start the existing URL ingest for one feed item of the caller, into a theme of the caller. The rules are those of ``federation_timeline_ingest_route``. The route works in single mode and in network mode. The item stays in the feed and gets the link to the new document; a timeline item with the same address gets the link too (merge by URL).
        """
        from ..store import client_db
        from ..store import feeds as feeds_store

        principal = request_principal(request)
        if principal.scope != "owner" or principal.account_id is None:
            raise HTTPException(status_code=404, detail="Not Found")
        account_id = principal.account_id
        raw_id = request.path_params["item_id"]
        if not (raw_id.isascii() and raw_id.isdigit() and raw_id[0] != "0" and len(raw_id) <= 18):
            return JSONResponse({"error": "item_not_found"}, status_code=404)
        await _json_object(request)
        # Ingest writes only to the personal knowledge base of the caller (design Section 5.2 rule 3). A ``theme`` or ``kb_id`` field is ignored.
        theme, _, theme_svc = _get_personal_kb_ctx(request)
        if getattr(theme, "account_id", None) is not None and theme.account_id != account_id:
            raise HTTPException(status_code=404, detail="theme not found")
        client_path = client_db.client_db_path(s.db_file)

        def _load() -> Any:
            client = client_db.connect_client_existing(client_path, readonly=True)
            try:
                return feeds_store.get_item(client, account_id, int(raw_id))
            finally:
                client.close()

        item = await asyncio.to_thread(_load)
        if item is None:
            return JSONResponse({"error": "item_not_found"}, status_code=404)
        # The URL ingest needs an address. An item without a link stays in the feed only.
        if not item["url"]:
            return JSONResponse({"error": "item_without_url"}, status_code=409)
        ingest_kwargs: dict[str, Any] = {"source": "feed"}
        try:
            report = await _run_expensive(theme_svc.ingest, item["url"], **ingest_kwargs)
        except (HTTPException, policy.ProviderPolicyError):
            raise
        except Exception as exc:  # noqa: BLE001
            _log_operation_failure(request, "feed_item_ingest", exc)
            return JSONResponse({"error": "ingest failed", "ok": False}, status_code=500)
        rep_dict = report_to_dict(report)
        rep_dict["kb_id"] = theme.id
        rep_dict["theme_label"] = theme.label
        document_id = getattr(report, "document_id", None)
        if document_id and not getattr(report, "error", None):

            def _link() -> None:
                client = client_db.connect_client_existing(client_path)
                try:
                    feeds_store.link_item(client, account_id, item["id"], kb_id=theme.id, doc_id=str(document_id))
                    feeds_store.link_by_url(client, account_id, [item["url"]], kb_id=theme.id, doc_id=str(document_id), timeline=True)
                    client.commit()
                finally:
                    client.close()

            await asyncio.to_thread(_link)
            rep_dict["linked"] = {"kb_id": theme.id, "document_id": str(document_id)}
        else:
            rep_dict["linked"] = None
        return JSONResponse(rep_dict)

    async def do_reingest(request: Request) -> JSONResponse:
        body = await _json_object(request)
        theme, theme_settings, theme_svc = _get_theme_ctx(request, body)
        scope = request_auth_scope(request)
        target = str(body.get("target") or body.get("payload") or body.get("id") or body.get("doc_id") or "").strip()
        if not target:
            raise HTTPException(status_code=400, detail="target or payload required")
        expand_max = body.get("expand_max")
        format_arg = str(body.get("format") or "").strip() or None

        full_content = bool(body.get("full_content") or body.get("no_truncate") or False)
        effort = _validate_request_effort(theme_svc, theme_settings, str(body.get("effort") or "").strip() or None)
        untombstone = bool(body.get("untombstone", False))

        if untombstone:
            def _lift():
                conn = dbm.connect_existing(theme_settings.db_file)
                try:
                    return dbm.remove_tombstones(conn, target=target, kb_id=theme.id)
                finally:
                    conn.close()
            await asyncio.to_thread(_lift)

        def _run() -> Any:
            conn = dbm.connect_existing(theme_settings.db_file)
            try:
                matched = dbm.resolve_document_targets(conn, target=target, kb_id=theme.id)
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
                    "kb_id": theme.id,
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
                rep = theme_svc.ingest(target, **ingest_kwargs)
                rep_dict = report_to_dict(rep)
                rep_dict["kb_id"] = theme.id
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
        effort = _validate_request_effort(theme_svc, theme_settings, str(body.get("effort") or "").strip() or None)
        format_arg = str(body.get("format") or "").strip() or None

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
            )
            if isinstance(res, dict) and theme.id != 0:
                res["kb_id"] = theme.id
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
        ) or (theme.is_shared and _kb_decide(request, theme, "review") != "allow")
        include_hidden = (scope != "anonymous") and not is_cross_account
        requested_mode = str(body.get("mode") or "").strip().lower()
        if scope == "anonymous" or is_cross_account:
            mode = "fts"
            limit = max(1, min(_MAX_ANONYMOUS_SEARCH_RESULTS, limit))
            summarize = False
            runner = _run_anonymous_search
        elif scope in {"owner", "write", "readonly"}:
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
                "kb_id": theme.id,
                "theme_label": theme.label,
                "hits": [
                    {
                        "id": hit.entity.id,
                        "type": hit.entity.type,
                        "name": display_label(hit.entity.name, hit.entity.aliases, principal.entity_language),
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
        theme, theme_settings, _ = _get_theme_ctx(request)
        include_hidden = _sees_hidden(request, theme)

        def _graph() -> dict[str, Any]:
            conn = dbm.connect_existing(theme_settings.db_file, readonly=True)
            try:
                g = _graph_json(conn, include_hidden=include_hidden, kb_id=theme.id, **_entity_language_kwargs(request))
                g["kb_id"] = theme.id
                g["theme_label"] = theme.label
                return g
            finally:
                conn.close()

        return JSONResponse(await asyncio.to_thread(_graph))

    def _public_page(request: Request, *, view: str, title: str, ref: str | None = None) -> Response:
        """The HTML form of a path that also has a JSON form. ``Vary`` keeps the two forms apart in a cache."""
        response = public_kb_routes.render_public_kb_page(request, view=view, title=title, ref=ref, ctx=account_routes.context)
        response.headers["Vary"] = "Cookie, Authorization, Sec-Fetch-Dest, Accept"
        return response

    _WORKSPACE_LINK_PARAMS = ("doc", "node")

    async def _shared_kb_root(request: Request) -> Response | None:
        """``GET /kb/{number}/``: the root of a shared knowledge base (design Section 7).

        1. A request that wants HTML, or one that does not ask for JSON: the page of the knowledge base (members, contributions, actions).
        2. A request with ``Accept: application/json`` that does not want HTML: ``{"knowledge_base": {...}}``, the same item as in the collection ``GET /kb``.
        3. A link into the content (``?doc=`` or ``?node=``): ``None``; the caller gives the workspace, which shows the document or the node.
        """
        kb, _, _ = _get_theme_ctx(request)
        wants_html = collections.wants_html(request)
        if not wants_html and "application/json" in request.headers.get("accept", "").lower():
            principal = request_principal(request)

            def _item() -> dict[str, Any]:
                role = None
                if principal.account_id is not None:
                    conn = dbm.connect_existing(s.db_file, readonly=True)
                    try:
                        role = kb_collab.account_memberships(conn, principal.account_id, state="active").get(kb.id)
                    finally:
                        conn.close()
                return _kb_collection_item(request, kb, role=role, origin=_public_origin(request), membership_state=_membership_states(principal.account_id).get(kb.id))

            return JSONResponse({"knowledge_base": await asyncio.to_thread(_item)}, headers={"Vary": "Cookie, Authorization, Sec-Fetch-Dest, Accept"})
        if any(name in request.query_params for name in _WORKSPACE_LINK_PARAMS):
            return None
        return await asyncio.to_thread(_public_page, request, view="kb_detail", title=kb.label, ref=ids.format_public_id(kb.number))

    async def graph_ui(request: Request) -> Response:
        from ..graphview import render_graph_html

        setup_redirect = await account_routes.setup_redirect(request)
        if setup_redirect is not None:
            return setup_redirect
        try:
            ns = request_namespace(request)
        except Exception:
            ns = None

        if ns is not None and ns.mode == "single" and not ns.prefix:
            # KNOWLEDGE_FEDERATION_DESIGN.md Section 15 rules 2 and 3: single mode has no root page. The namespace middleware sends ``/`` to ``/@{username}/``; a request comes here only while the admin has no USERNAME (the first setup is not complete). The admin credential got ``/setup`` above; each other request gets this page. There is no other target.
            title = html.escape(_t("console.account_status.setup_required"))
            page = f'<!doctype html><html lang="{html.escape(get_locale())}"><head><meta charset="utf-8"><meta name="robots" content="noindex"><title>{title}</title></head><body data-setup-state="setup_required"><main><h1>{title}</h1><p><a href="/login">{html.escape(_t("auth.login.title"))}</a></p></main></body></html>'
            return HTMLResponse(page, status_code=503, headers={"Cache-Control": "no-store", "Retry-After": "3600"})

        if ns is not None and ns.account_id is None and ns.pinned_kb_id is None and ns.prefix.startswith("/themes/"):
            # ``/themes/{number}/``: the page of a theme. The namespace middleware checked that the number is an active theme.
            return await theme_page_route(request, int(ns.prefix[len("/themes/"):]))

        if ns is not None and ns.account_id is None and ns.pinned_kb_id is not None:
            kb_root = await _shared_kb_root(request)
            if kb_root is not None:
                return kb_root

        if ns is not None and ns.mode == "network" and not ns.via_prefix:
            params = request.query_params
            if params.get("pwa") == "1":
                qs = str(request.url.query) or "pwa=1"

                account_id = None
                try:
                    principal = request_principal(request)
                    account_id = principal.account_id
                except Exception:
                    pass
                if account_id is None:
                    try:
                        cookie_present, cookie_token = read_session_cookie(request.scope)
                        if cookie_present and cookie_token:
                            conn_s = dbm.connect_existing(s.db_file)
                            try:
                                binding = accounts_store.validate_session_binding(conn_s, cookie_token)
                                if binding is not None:
                                    account_id = binding.account_id
                            finally:
                                conn_s.close()
                    except Exception:
                        pass

                account = None
                if account_id is not None:
                    try:
                        acc = namespace_directory.account(account_id)
                        if acc.status in accounts_store.CREDENTIAL_ACCEPTING_STATUSES and acc.status == "active" and acc.username:
                            account = acc
                    except Exception:
                        pass

                if account is not None and account.username:
                    return RedirectResponse(f"/@{account.username}/?{qs}", status_code=302, headers={"Cache-Control": "no-store"})

                if bool(getattr(s, "anonymous_readonly", True)):
                    admin = None
                    try:
                        adm = namespace_directory.admin()
                        if adm.status in accounts_store.CREDENTIAL_ACCEPTING_STATUSES and adm.status == "active" and adm.username:
                            admin = adm
                    except Exception:
                        pass
                    if admin is not None and admin.username:
                        return RedirectResponse(f"/@{admin.username}/?{qs}", status_code=302, headers={"Cache-Control": "no-store"})

                return RedirectResponse("/login", status_code=302, headers={"Cache-Control": "no-store"})

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
        include_private = visible_scope in {"owner", "write", "readonly"}
        theme = None

        if getattr(s, "multi_theme", False) or (ns is not None and ns.pinned_kb_id is not None):
            try:
                theme, _, _ = _get_theme_ctx(request)
            except HTTPException:
                raise
            except Exception:
                pass
        html_text = render_graph_html(
            s,
            include_private=include_private,
            theme=theme,
            host=request.headers.get("host"),
            request=request,
            account_id=account_id,
        )
        # The profile page of a federation actor points to the actor ID (Fedify sidecar design Section 5.1 rule 2). The path has no content negotiation.
        headers: dict[str, str] = {}
        if ns is not None and ns.mode == "network" and ns.username is not None and ns.pinned_kb_id is None:
            actor_url = await asyncio.to_thread(_profile_actor_url, account_id)
            if actor_url is not None:
                link_tag = f'<link rel="alternate" type="application/activity+json" href="{html.escape(actor_url, quote=True)}">'
                html_text = html_text.replace("</head>", f"{link_tag}\n</head>", 1)
                headers["Link"] = f'<{actor_url}>; rel="alternate"; type="application/activity+json"'
        return HTMLResponse(html_text, headers=headers)

    def _profile_actor_url(account_id: int) -> str | None:
        """Actor ID of the account, or ``None`` when the account is not a federation actor (FK4)."""
        conn = dbm.connect_existing(s.db_file, readonly=True)
        try:
            store = accounts_store.AccountStore(conn)
            if store.federation_state() != "on":
                return None
            try:
                account = store.get(account_id)
            except accounts_store.AccountNotFound:
                return None
        finally:
            conn.close()
        if account.status != "active" or not account.profile_public:
            return None
        return f"{WebRuntimeConfig.from_settings(s).public_origin}/ap/actors/{account.public_id}"

    def _resolve_syndication_ui_context(request: Request) -> tuple[str, bool, KnowledgeBaseInfo | None]:
        try:
            ns = request_namespace(request)
        except Exception:
            ns = None
        principal = request_principal(request)
        if ns is not None and ns.mode == "network" and principal.account_id is None:
            raise HTTPException(status_code=404, detail="not found")
        scope = request_auth_scope(request)
        include_private = scope in {"owner", "write", "readonly"}
        theme = None
        if getattr(s, "multi_theme", False):
            try:
                theme, _, _ = _get_theme_ctx(request)
                if scope in {"public", "anonymous"} and theme is not None and not theme.is_public:
                    theme = None
            except HTTPException:
                # A given ``kb`` that is not there or not readable is 404 (no other knowledge base is shown in its place).
                if request.query_params.get("kb") is not None:
                    raise
                theme = None
            except Exception:
                theme = None
        return scope, include_private, theme

    async def syndication_ui_route(request: Request) -> HTMLResponse:
        from ..graphview import render_syndication_html

        scope, include_private, theme = _resolve_syndication_ui_context(request)
        return HTMLResponse(
            render_syndication_html(
                s,
                include_private=include_private,
                theme=theme,
                host=request.headers.get("host"),
                auth_scope=scope,
                request=request,
            )
        )

    async def syndication_preferences_ui_route(request: Request) -> Response:
        """D4 호환 302: 옛 ``/syndication/preferences?tab=``를 ``/settings/*``(또는 관리자의 ``/admin/*``)로 보낸다 (설계 §2.2).

        ``?kb=``(번호)가 있으면 먼저 엄격히 검증한다(없거나 접근할 수 없으면 404, C1). 모르는 ``?tab=``도 404다. ``/settings/*``는 루트 경로라 위치에 ``/@USERNAME``을 붙이지 않는다(D1, S9).
        """
        theme_query: str | None = None
        raw_kb = request.query_params.get("kb")
        if raw_kb is not None:
            theme, _, _ = _get_theme_ctx(request)
            # The settings are of the account of the caller: a knowledge base of another account is 404 (C1).
            if theme.account_id != request_principal(request).account_id:
                raise HTTPException(status_code=404, detail="theme not found")
            theme_query = ids.format_public_id(theme.number) if theme.number is not None else str(theme.id)
        tab = request.query_params.get("tab") if "tab" in request.query_params else None
        principal = request_principal(request)
        caller_is_admin = principal.server_role == "admin" and principal.scope == "owner" and principal.account_id is not None
        if tab is None:
            target = "/settings/reader"
        elif tab in _LEGACY_PREFS_SETTINGS_TABS:
            target = _LEGACY_PREFS_SETTINGS_TABS[tab]
        elif tab in _LEGACY_PREFS_ACCOUNT_TABS:
            # 관리자 화면은 테마와 무관하다. ?kb=를 넘기지 않는다.
            target = _LEGACY_PREFS_ACCOUNT_TABS[tab] if caller_is_admin else "/settings/account"
            theme_query = None
        else:
            raise HTTPException(status_code=404, detail="unknown preferences tab")
        location = f"{target}?{urlencode({'kb': theme_query})}" if theme_query else target
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
            body.get("target_kb_id")
            if body.get("target_kb_id") is not None
            else body.get("target_theme")
        )
        if target_theme_ref is None:
            raise HTTPException(status_code=400, detail="target_kb_id is required")

        if not getattr(s, "multi_theme", False) and theme_mgr is None:
            try:
                target_tid = int(target_theme_ref)
            except (ValueError, TypeError):
                raise HTTPException(status_code=400, detail="invalid target_kb_id")
            if target_tid != 0:
                raise HTTPException(status_code=400, detail="multi_theme is not enabled")
            target_theme = KnowledgeBaseInfo(
                id=0,
                seq=0,
                label="기본 지식베이스",
                description="일반 수집 자료 및 기본 지식",
                icon="📚",
                db_path=str(getattr(s, "db_path", "data/claire.db")),
                is_personal=True,
                is_public=True,
            )
        else:
            try:
                target_theme = theme_mgr.get_kb(target_theme_ref)
            except Exception:
                raise HTTPException(status_code=404, detail="target theme not found")

        # The copy is an ingest: its target is the personal knowledge base of the caller (capability ``write`` of the route, CT4). A shared knowledge base gets 403 ``kb_ingest_by_contribution``; documents go there by contribution.
        _require_kb(request, target_theme, _request_capability(request))

        principal = request_principal(request)
        is_network = False
        try:
            ns = request_namespace(request)
            if ns is not None and ns.mode == "network":
                is_network = True
        except Exception:
            pass
        if getattr(s, "server_mode", None) == "network":
            is_network = True

        source_theme_ref = (
            body.get("source_kb_id")
            if body.get("source_kb_id") is not None
            else body.get("source_theme")
        )
        if source_theme_ref is not None:
            if not getattr(s, "multi_theme", False) and theme_mgr is None:
                src_theme = target_theme
            else:
                try:
                    src_theme = theme_mgr.get_kb(source_theme_ref)
                except Exception:
                    raise HTTPException(status_code=404, detail="source theme not found")
        else:
            # No default: the source is required (design Section 5.2, CT23).
            raise HTTPException(status_code=400, detail="source_kb_id is required")

        # The target is the personal knowledge base of the caller (capability ``write`` of the route); the source only needs ``read``.
        if _kb_decide(request, src_theme, "read") != "allow":
            raise HTTPException(status_code=404, detail="source theme not found")

        conn = dbm.connect_existing(s.db_file)
        try:
            src_doc = dbm.get_document(conn, document_id, kb_id=src_theme.id)
        finally:
            conn.close()

        if src_doc is None:
            raise HTTPException(status_code=404, detail=f"document {document_id} not found")



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
            "source_kb_id": src_theme.id,
            "source_document_id": src_doc.id,
        }
        # CT28: the copy takes no focus, also not the stored focus of the source.
        target_doc.meta.pop("focus", None)

        target_svc = service_pool.get_service(target_theme.id)
        payload = src_doc.url or src_doc.raw_text or src_doc.title or "cross_theme_dispatch"
        try:
            report = await _run_expensive(
                target_svc.ingest,
                payload,
                prefetched=target_doc,
                source=f"syndication:{src_theme.id}",
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
                    kb_id=src_theme.id,
                    target_kb_id=target_theme.id,
                    document_id=src_doc.id,
                    mode="cross_theme",
                    status=status,
                    remote_ref=remote_ref,
                    payload=payload,
                    error=error_msg,
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
                "source_kb_id": src_theme.id,
                "target_kb_id": target_theme.id,
                "document_id": src_doc.id,
                "target_document_id": remote_ref,
                "dispatched_by_account_id": dispatched_by_account_id,
                "error": error_msg,
            }
        )

    async def syndication_dispatches_route(request: Request) -> JSONResponse:
        scope = request_auth_scope(request)
        if scope in {"anonymous", "public"}:
            raise HTTPException(status_code=403, detail="권한이 없습니다.")

        kb_id_param = request.query_params.get("kb_id")
        target_kb_id_param = request.query_params.get("target_kb_id")
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

        kb_id = (
            int(kb_id_param)
            if kb_id_param is not None and kb_id_param != ""
            else None
        )
        target_kb_id = (
            int(target_kb_id_param)
            if target_kb_id_param is not None and target_kb_id_param != ""
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
                    kb_id=kb_id,
                    target_kb_id=target_kb_id,
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

    async def ns_context_route(request: Request) -> Response:
        """``GET /ns/v1``: the JSON-LD context of the knowledge federation vocabulary (KNOWLEDGE_FEDERATION_DESIGN.md Section 9.1, KD9). Public, in each mode, also while federation is off: other servers read it."""
        from ..knowledge_federation import vocabulary

        return Response(vocabulary.context_json(), media_type=vocabulary.CONTEXT_MEDIA_TYPE, headers={"Access-Control-Allow-Origin": "*", "Cache-Control": "public, max-age=86400"})

    async def ns_vocabulary_route(request: Request) -> Response:
        """``GET /ns``: the page of the vocabulary, with one anchor for each term."""
        from ..knowledge_federation import vocabulary

        return HTMLResponse(vocabulary.vocabulary_page(), headers={"Cache-Control": "public, max-age=86400"})

    async def robots_txt_route(request: Request) -> Response:
        host_header = request.headers.get("host") or ""
        req_host = host_header.split(":", 1)[0].strip().lower()

        theme: KnowledgeBaseInfo | None = None
        if getattr(s, "multi_theme", False) and theme_mgr is not None and req_host:
            theme = theme_mgr.get_kb_by_fqdn(req_host)

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

        theme: KnowledgeBaseInfo | None = None
        if getattr(s, "multi_theme", False) and theme_mgr is not None and req_host:
            theme = theme_mgr.get_kb_by_fqdn(req_host)

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
            t_settings = theme_mgr.get_settings_for_kb(theme.id, s)
            target_dbs = [(t_settings.db_file, theme.id)]
        elif getattr(s, "multi_theme", False) and theme_mgr is not None:
            for t in theme_mgr.list_kbs():
                if getattr(t, "is_public", True):
                    t_settings = theme_mgr.get_settings_for_kb(t.id, s)
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
                    entries = dbm.list_public_sitemap_documents(conn, ensure_shares=True, kb_id=target_tid)
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

    async def service_worker_route(request: Request) -> Response:
        headers = {
            "Content-Type": "application/javascript; charset=utf-8",
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Service-Worker-Allowed": "/",
        }
        if request.method == "HEAD":
            return Response(status_code=200, headers=headers, media_type="application/javascript")
        return Response(content=_SW_JS_CONTENT, status_code=200, headers=headers, media_type="application/javascript")

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
        from .. import ids as ids_mod  # the name ``ids`` is the list of the ``id`` filter in this function
        from ..store.queries import DOCUMENTS_DEFAULT_LIMIT, DOCUMENTS_MAX_IDS, DOCUMENTS_MAX_LIMIT, documents_list, documents_page

        _documents_list = _resolve_query_func("documents_list", documents_list)
        theme, theme_settings, _ = _get_theme_ctx(request)
        include_hidden = _sees_hidden(request, theme)
        hides_sources = _hides_sources(request, theme)
        render_format = getattr(theme_settings, "render_format", "adoc")
        requester = request_principal(request)
        own_personal = theme.is_personal and requester.account_id is not None and requester.account_id == theme.account_id and request_auth_scope(request) != "anonymous"

        # Keyset paging and list filters (unit LP-5). A request with no parameter gets the first 300 documents, as before, plus next_before and total.
        params = request.query_params

        def _flag(name: str) -> bool | None:
            raw = params.get(name)
            if raw is None or raw == "":
                return None
            if raw not in ("0", "1"):
                raise ValueError(f"{name} must be 0 or 1")
            return raw == "1"

        try:
            # Fixed error texts: the text of a Python exception does not go to the client.
            limit_error = f"limit must be an integer 1..{DOCUMENTS_MAX_LIMIT}"
            try:
                limit = int(params.get("limit") or DOCUMENTS_DEFAULT_LIMIT)
            except ValueError:
                raise ValueError(limit_error) from None
            if not 1 <= limit <= DOCUMENTS_MAX_LIMIT:
                raise ValueError(limit_error)
            before = params.get("before") or None
            if before:
                try:
                    dbm.decode_documents_cursor(before)
                except ValueError:
                    raise ValueError("before must be <fetched_at>:<id>") from None
            filters: dict[str, Any] = {"pinned": _flag("pinned"), "hidden": _flag("hidden")}
            text = (params.get("q") or "").strip()
            if len(text) > 200:
                raise ValueError("q must be 200 characters or less")
            filters["text"] = text or None
            detail = params.get("detail") or None
            if detail is not None and detail not in ("missing", "mismatched"):
                raise ValueError("detail must be missing or mismatched")
            if detail:
                filters["detail"] = detail
                filters["detail_format"] = render_format
            if "id" in params:
                ids = [v for v in params.getlist("id") if v]
                if len(ids) > DOCUMENTS_MAX_IDS:
                    raise ValueError(f"id can be given {DOCUMENTS_MAX_IDS} times or less")
                filters["ids"] = ids
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

        def _documents() -> dict[str, Any]:
            conn = dbm.connect_existing(theme_settings.db_file, readonly=True)
            try:
                format_status = dbm.check_format_mismatch(conn, render_format, kb_id=theme.id)
                page = documents_page(conn, limit, before=before, include_hidden=include_hidden, kb_id=theme.id, list_func=_documents_list, **filters)
                if hides_sources:
                    page["documents"] = _apply_source_rule(conn, theme, page["documents"])
                if own_personal:
                    # The account sees, in its personal knowledge base, the theme match of each document and the flag "never contribute" (design Section 10.3). One query for the page for each of the two.
                    matches = themes_store.document_matches_with_themes(conn, [str(d["id"]) for d in page["documents"]])
                    blocked = kb_collab.contribution_blocks(conn, int(theme.account_id))
                    for doc in page["documents"]:
                        match = matches.get(str(doc["id"]))
                        doc["theme_match"] = None if match is None else {"theme": ids_mod.format_public_id(match["theme_number"]), "theme_label": match["theme_label"], "knowledge_type": match["knowledge_type"], "score": match["score"]}
                        doc["never_contribute"] = str(doc["id"]) in blocked
                return {
                    "documents": page["documents"],
                    "format_status": format_status,
                    "kb_id": theme.id,
                    "theme_label": theme.label,
                    "next_before": page["next_before"],
                    "total": page["total"],
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

        theme, theme_settings, _ = _get_theme_ctx(request)
        include_hidden = _sees_hidden(request, theme)
        hides_sources = _hides_sources(request, theme)
        # docs=brief: the source documents have no detail text (the WebUI sends it). The keyword is sent only in this mode, thus the default call does not change.
        detail_kwargs: dict[str, Any] = {"docs_brief": True} if request.query_params.get("docs", "") == "brief" else {}
        detail_kwargs.update(_entity_language_kwargs(request))

        def _load() -> dict[str, Any] | None:
            conn = dbm.connect_existing(theme_settings.db_file, readonly=True)
            try:
                rep = _detail(conn, node_id, include_hidden=include_hidden, kb_id=theme.id, **detail_kwargs)
                if rep is not None:
                    if hides_sources and isinstance(rep.get("documents"), list):
                        rep["documents"] = _apply_source_rule(conn, theme, rep["documents"])
                    rep["kb_id"] = theme.id
                    rep["theme_label"] = theme.label
                return rep
            finally:
                conn.close()

        report = await asyncio.to_thread(_load)
        if report is None:
            raise HTTPException(status_code=404, detail="not found")
        return JSONResponse(report)

    async def document_export_route(request: Request) -> Response:
        """Export of one document as a file (RD24, MD-3). ``format=markdown`` (or ``md``) is the only format: the AsciiDoc body becomes Markdown with a YAML front matter header. Read-only. The access rule is the rule of ``GET /document``. The federation client offers it in the context menu of an item."""
        from urllib.parse import quote as _quote

        from ..render.markdown_export import document_markdown
        from ..store.queries import document_detail

        document_id = request.query_params.get("id", "").strip()
        if not document_id:
            return JSONResponse({"error": "id required"}, status_code=400)
        fmt = request.query_params.get("format", "markdown").strip().lower()
        if fmt not in ("markdown", "md"):
            return JSONResponse({"error": "unsupported_format", "formats": ["markdown"]}, status_code=400)
        theme, theme_settings, _ = _get_theme_ctx(request)
        include_hidden = _sees_hidden(request, theme)
        hides_sources = _hides_sources(request, theme)

        def _load() -> dict[str, Any] | None:
            conn = dbm.connect_existing(theme_settings.db_file, readonly=True)
            try:
                report = document_detail(conn, document_id, include_hidden=include_hidden, kb_id=theme.id)
                if report is not None and hides_sources:
                    report = _apply_source_rule(conn, theme, [report])[0]
                return report
            finally:
                conn.close()

        report = await asyncio.to_thread(_load)
        if report is None:
            return JSONResponse({"error": "not_found"}, status_code=404)
        body = document_markdown(report)
        ascii_name = re.sub(r"[^A-Za-z0-9._-]+", "-", f"claire-{document_id}").strip("-") + ".md"
        title_name = _quote((str(report.get("title") or document_id).strip() or document_id)[:120] + ".md", safe="")
        return Response(
            body,
            media_type="text/markdown; charset=utf-8",
            headers={
                "Content-Disposition": f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{title_name}",
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    async def document_detail_route(request: Request) -> JSONResponse:
        from ..store.queries import document_detail

        _document_detail = _resolve_query_func("document_detail", document_detail)
        document_id = request.query_params.get("id", "")
        if not document_id:
            raise HTTPException(status_code=400, detail="id required")

        language_kwargs = _entity_language_kwargs(request)
        theme, theme_settings, _ = _get_theme_ctx(request)
        # A hidden document (also the copy of a pending or hidden contribution) is for the account of a personal knowledge base and for the reviewers of a shared one (design Sections 6.2, 9.4, 9.6).
        include_hidden = _sees_hidden(request, theme)
        hides_sources = _hides_sources(request, theme)

        def _load(settings: Settings, t_info: KnowledgeBaseInfo) -> dict[str, Any] | None:
            conn = dbm.connect_existing(settings.db_file, readonly=True)
            try:
                # GET은 readonly 사용자에게도 열리므로 열람 상태를 변경하지 않는다.
                rep = _document_detail(conn, document_id, include_hidden=include_hidden, kb_id=t_info.id, **language_kwargs)
                if rep is not None and hides_sources:
                    rep = _apply_source_rule(conn, t_info, [rep])[0]
                if rep is not None:
                    rep["kb_id"] = t_info.id
                    rep["theme_label"] = t_info.label
                return rep
            finally:
                conn.close()

        report = await asyncio.to_thread(_load, theme_settings, theme)

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

        theme, theme_settings, _ = _get_theme_ctx(request)
        raw_sees_hidden = _sees_hidden(request, theme)

        def _find_raw(t_settings: Settings, t_kb_id: int) -> tuple[str, str] | None:
            conn = dbm.connect_existing(t_settings.db_file, readonly=True)
            try:
                if not raw_sees_hidden:
                    # The source file of a hidden document is not for a requester who does not see hidden documents: the answer is the one of a document that is not there.
                    hidden_row = conn.execute("SELECT hidden FROM documents WHERE id=? AND kb_id=?", (doc_id, t_kb_id)).fetchone()
                    if hidden_row is not None and hidden_row[0]:
                        return None
                try:
                    row = conn.execute(
                        "SELECT file_ref, file_name FROM raw_inbox WHERE document_id = ? AND kb_id = ? AND file_ref IS NOT NULL AND trim(file_ref) != '' ORDER BY id DESC LIMIT 1",
                        (doc_id, t_kb_id),
                    ).fetchone()
                    if row and row["file_ref"]:
                        return str(row["file_ref"]), str(row["file_name"] or "")
                except Exception:
                    pass

                row_doc = dbm.get_document_row(conn, doc_id, kb_id=t_kb_id)
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
                row = dbm.get_document_row(conn, document_id, kb_id=theme.id)
                if row is None:
                    return False
                dbm.set_document_seen(conn, document_id, seen=True, kb_id=theme.id)
                return True
            finally:
                conn.close()

        if not await asyncio.to_thread(_mark):
            raise HTTPException(status_code=404, detail="not found")
        resp = {"id": document_id, "seen": True}
        if theme.id != 0:
            resp["kb_id"] = theme.id
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
                return dbm.set_document_pinned(conn, document_id, pinned, kb_id=theme.id)
            finally:
                conn.close()

        if not await asyncio.to_thread(_pin):
            raise HTTPException(status_code=404, detail="not found")
        resp = {"id": document_id, "pinned": pinned}
        if theme.id != 0:
            resp["kb_id"] = theme.id
        return JSONResponse(resp)

    async def document_hide_route(request: Request) -> JSONResponse:
        body = await _json_object(request)
        theme, theme_settings, _ = _get_theme_ctx(request, body)
        document_id = str(body.get("id") or "").strip()
        if not document_id:
            raise HTTPException(status_code=400, detail="id required")
        hidden = bool(body.get("hidden", True))

        actor_id = request_principal(request).account_id

        def _hide() -> bool:
            conn = dbm.connect_existing(theme_settings.db_file)
            try:
                if theme.is_shared:
                    # The copy of a contribution: hide and unhide change the state of the contribution too (design Section 9.6). A copy that waits for its merge or its approval stays hidden.
                    row = kb_collab.get_contribution_for_document(conn, theme.id, document_id)
                    if row is not None:
                        try:
                            contributions.set_hidden(conn, kb_id=theme.id, contribution=int(row["public_id"]), account_id=actor_id, hidden=hidden)
                        except contributions.ContributionError as exc:
                            raise HTTPException(status_code=exc.status, detail=exc.code) from exc
                        conn.commit()
                        return True
                return dbm.set_document_hidden(conn, document_id, hidden, kb_id=theme.id)
            finally:
                conn.close()

        if not await asyncio.to_thread(_hide):
            raise HTTPException(status_code=404, detail="not found")
        resp = {"id": document_id, "hidden": hidden}
        if theme.id != 0:
            resp["kb_id"] = theme.id
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
                return dbm.set_document_title(conn, document_id, title, kb_id=theme.id)
            finally:
                conn.close()

        if not await asyncio.to_thread(_update_title):
            raise HTTPException(status_code=404, detail="not found")
        resp = {"id": document_id, "title": title}
        if theme.id != 0:
            resp["kb_id"] = theme.id
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
                row = dbm.get_document_row(conn, document_id, kb_id=theme.id)
                if row is None:
                    return False
                return dbm.purge_document_matrix(conn, document_id, kb_id=theme.id)
            finally:
                conn.close()

        if not await asyncio.to_thread(_purge):
            raise HTTPException(status_code=404, detail="not found")

        from ..ingest.active import purge_active_matrix
        await asyncio.to_thread(purge_active_matrix, theme_settings.data_dir, document_id)

        resp = {"id": document_id, "purged": True}
        if theme.id != 0:
            resp["kb_id"] = theme.id
        return JSONResponse(resp)

    async def document_purge_route(request: Request) -> JSONResponse:
        body = await _json_object(request)
        theme, theme_settings, _ = _get_theme_ctx(request, body)
        if theme.is_shared:
            # A document of a shared knowledge base goes only by the removal of its contribution: a reason code, a tombstone, and a row in the moderation log (design Section 9.6). Use ``POST /kb/{number}/contributions/{contribution}/remove``.
            raise HTTPException(status_code=403, detail="kb_remove_by_contribution")
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
                matched = dbm.resolve_document_targets(conn, target=target, kb_id=theme.id)
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
                    kb_id=theme.id,
                )
                if not dry_run:
                    from ..ingest.active import purge_active_matrix
                    for tid in target_ids:
                        try:
                            purge_active_matrix(theme_settings.data_dir, tid)
                        except Exception:
                            pass
                report["targets"] = matched
                report["kb_id"] = theme.id
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
        synthesis_sees_hidden = _sees_hidden(request, theme)

        def _synthesize_job() -> dict[str, Any]:
            conn = dbm.connect_existing(theme_settings.db_file, readonly=True)
            try:
                res = _synthesize(conn, theme_svc.provider, entity_ids, query, kb_id=theme.id, include_hidden=synthesis_sees_hidden)
                res["kb_id"] = theme.id
                res["theme_label"] = theme.label
                return res
            finally:
                conn.close()

        return JSONResponse(await _run_expensive(_synthesize_job))

    async def research_route(request: Request) -> StreamingResponse:
        from ..research import contextual_research

        body = await _json_object(request)
        # 요청 테마의 설정(kb_id)으로 맥락을 읽고 적재한다
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
        if document_id is not None and not _sees_hidden(request, research_theme):
            # The context of the research reads the document: a hidden document is not there for a requester who does not see hidden documents.
            def _doc_hidden() -> bool:
                conn = dbm.connect_existing(research_settings.db_file, readonly=True)
                try:
                    row = conn.execute("SELECT hidden FROM documents WHERE id=? AND kb_id=?", (str(document_id), research_theme.id)).fetchone()
                    return row is not None and bool(row[0])
                finally:
                    conn.close()

            if await asyncio.to_thread(_doc_hidden):
                raise HTTPException(status_code=404, detail="document not found")

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
        theme, theme_settings, theme_svc = _get_personal_kb_ctx(request)
        scope = request_auth_scope(request)
        payload = str(body.get("payload") or "").strip()
        if not payload:
            raise HTTPException(status_code=400, detail="payload required")
        expand_max = body.get("expand_max")
        format_arg = str(body.get("format") or "").strip() or None
        # CT28: the focus is sealed. The parser still separates the payload from the text after a separator; the text is ignored.
        from ..telegram_bot import parse_message_focus

        payload, _sealed_focus = parse_message_focus(payload)

        full_content = bool(body.get("full_content") or body.get("no_truncate") or False)
        effort = _validate_request_effort(theme_svc, theme_settings, str(body.get("effort") or "").strip() or None)

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
                        result["kb_id"] = theme.id
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
        target = str(body.get("target") or body.get("payload") or body.get("id") or body.get("doc_id") or "").strip()
        if not target:
            raise HTTPException(status_code=400, detail="target or payload required")
        expand_max = body.get("expand_max")
        format_arg = str(body.get("format") or "").strip() or None

        full_content = bool(body.get("full_content") or body.get("no_truncate") or False)
        effort = _validate_request_effort(theme_svc, theme_settings, str(body.get("effort") or "").strip() or None)
        untombstone = bool(body.get("untombstone", False))

        if untombstone:
            def _lift():
                conn = dbm.connect_existing(theme_settings.db_file)
                try:
                    return dbm.remove_tombstones(conn, target=target, kb_id=theme.id)
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
                    matched = dbm.resolve_document_targets(conn, target=target, kb_id=theme.id)
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
                        "kb_id": theme.id,
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
                    rep = theme_svc.ingest(target, **ingest_kwargs)
                    rep_dict = report_to_dict(rep)
                    rep_dict["kb_id"] = theme.id
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
                        result["kb_id"] = theme.id
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
        effort = _validate_request_effort(theme_svc, theme_settings, str(body.get("effort") or "").strip() or None)
        format_arg = str(body.get("format") or "").strip() or None

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
                    on_progress=lambda stage, msg="": on_progress({"stage": stage, "msg": msg or stage}),
                )
                if isinstance(res, dict) and theme.id != 0:
                    res["kb_id"] = theme.id
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
                        result["kb_id"] = theme.id
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
                res = _dedup_clusters(conn, scan, kb_id=theme.id)
                if theme.id != 0:
                    res["kb_id"] = theme.id
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
        language_kwargs = _entity_language_kwargs(request)

        def _select() -> dict[str, Any]:
            conn = dbm.connect_existing(theme_settings.db_file)
            try:
                try:
                    updated = dbm.select_entity_primary_label(conn, entity_id, new_name, kb_id=theme.id)
                except KeyError as e:
                    raise HTTPException(status_code=404, detail=scrub_exception(e))
                except ValueError as e:
                    raise HTTPException(status_code=400, detail=scrub_exception(e))

                from ..store.queries import node_detail as _detail

                _detail_fn = _resolve_query_func("node_detail", _detail)
                rep = _detail_fn(conn, updated.id, kb_id=theme.id, **language_kwargs)
                if rep is not None and theme.id != 0:
                    rep["kb_id"] = theme.id
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
                doc = dbm.get_document(conn, doc_id, kb_id=theme.id)
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

                success = rollback_resolution(conn, target_dec, kb_id=theme.id)
                if success:
                    raw_log = (doc.meta or {}).get("resolution_log") or []
                    for item in raw_log:
                        if isinstance(item, dict) and item.get("entity") == entity_name and item.get("decision") == "MERGE":
                            item["rolled_back"] = True
                            item["decision"] = "ROLLED_BACK"
                            item["reason"] = (item.get("reason", "") + " (롤백됨)").strip()
                    dbm.update_document_meta(conn, doc_id, doc.meta, kb_id=theme.id)
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
                return get_all_resolution_decisions(conn, limit=limit, offset=offset, kb_id=theme.id)
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

        include_hidden = _sees_hidden(request, theme)

        def _share(settings: Settings, target_theme_info: KnowledgeBaseInfo) -> str | None:
            conn = dbm.connect_existing(settings.db_file)
            try:
                row = dbm.get_document_row(conn, document_id, kb_id=target_theme_info.id)
                if row is None:
                    return None
                if not include_hidden and bool(row["hidden"]):
                    return None
                return dbm.create_doc_share(conn, document_id, kb_id=target_theme_info.id)
            finally:
                conn.close()

        token = await asyncio.to_thread(_share, theme_settings, theme)
        target_kb_id = theme.id

        if not token:
            raise HTTPException(status_code=404, detail="document not found")
        return JSONResponse({"token": token, "path": "/p?s=" + token, "kb_id": target_kb_id})

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
                document_id = dbm.resolve_doc_share(conn, token, kb_id=0)
                if not document_id:
                    return None
                doc = _document_detail(conn, document_id, include_hidden=False, kb_id=0)
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
                            doc = _document_detail(t_conn, document_id, include_hidden=False, kb_id=target["kb_id"])
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
        if scope not in ("owner", "write"):
            auth_hdr = request.headers.get("authorization", "")
            if auth_hdr.lower().startswith("bearer "):
                bearer_tok = auth_hdr[7:].strip()
                if (getattr(s, "inject_token", None) and bearer_tok == s.inject_token) or (
                    getattr(s, "owner_token", None) and bearer_tok == s.owner_token
                ):
                    scope = "owner"
                else:
                    # A stored API token (design Section 6.6): ``owner`` and ``write`` read the raw file.
                    entry = get_token_store(data_dir=s.data_dir).validate_api_token(bearer_tok)
                    if entry is not None:
                        scope = str(entry.get("scope") or "")
            elif (cookie_token := read_session_cookie(request.scope)[1]) is not None:
                try:
                    conn_sess = dbm.connect_existing(s.db_file, readonly=True)
                    try:
                        sess_scope = dbm.validate_session_scope(
                            conn_sess, cookie_token, scopes=("owner", "write")
                        )
                        if sess_scope:
                            scope = sess_scope
                    finally:
                        conn_sess.close()
                except Exception:
                    pass

        can_access_raw = scope in ("owner", "write")
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
    ) -> tuple[KnowledgeBaseInfo, Settings, Path, Path, str, str]:
        """The knowledge base of a presentation request.

        A share token selects its own knowledge base (the token is the capability). Else the selection rules apply (design Section 5.2): the path, or ``kb`` on a route without a namespace. There is no search in other knowledge bases.
        """
        if token:
            resolved = theme_mgr.resolve_share_token(token, include_hidden=False)
            if resolved is None:
                raise HTTPException(status_code=404, detail="not found")
            try:
                target_theme = theme_mgr.get_kb(resolved[0])
            except KnowledgeBaseNotFound:
                # 삭제된 지식 베이스의 공유 토큰은 찾지 못한 것으로 본다
                raise HTTPException(status_code=404, detail="not found") from None
            theme_settings = theme_mgr.get_settings_for_kb(target_theme.id, s)
            doc_obj = resolved[2]
            if doc_obj.get("hidden"):
                raise HTTPException(status_code=404, detail="not found")
            return (
                target_theme,
                theme_settings,
                theme_settings.db_file,
                theme_settings.data_dir,
                doc_obj.get("id", ""),
                doc_obj.get("title", ""),
            )
        target_theme, theme_settings, _ = _get_theme_ctx(request, None)
        return target_theme, theme_settings, theme_settings.db_file, theme_settings.data_dir, doc_id, ""

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

        svc = PresentationService(data_dir=active_data_dir, settings=theme_settings, kb_id=target_theme.id)
        conn = dbm.connect_existing(active_db)
        try:
            if not doc_title:
                d_obj = dbm.get_document(conn, doc_id, kb_id=target_theme.id)
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

        svc = PresentationService(data_dir=active_data_dir, settings=theme_settings, kb_id=target_theme.id)
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

        svc = PresentationService(data_dir=active_data_dir, settings=theme_settings, kb_id=target_theme.id)

        if not wait_for_completion:
            conn = dbm.connect_existing(active_db, readonly=True)
            try:
                if dbm.get_document_row(conn, doc_id, kb_id=target_theme.id) is None:
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

        svc = PresentationService(data_dir=active_data_dir, settings=theme_settings, kb_id=target_theme.id)
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

        svc = PresentationService(data_dir=active_data_dir, settings=theme_settings, kb_id=target_theme.id)
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

        svc = PresentationService(data_dir=active_data_dir, settings=theme_settings, kb_id=target_theme.id)

        if request.method == "GET":
            conn = dbm.connect_existing(active_db, readonly=True)
            try:
                adoc = dbm.get_presentation_adoc(conn, doc_id, kb_id=target_theme.id)
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
                    kb_id=target_theme.id,
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
        kb_id = theme.id
        # The data bundle of a shared knowledge base (design Section 13.2) is for its moderators and the admin: the capability ``settings`` of the route. The registry records the account that made it, because a shared knowledge base has no owner account.
        bundle_account = request_principal(request).account_id if theme.is_shared else None

        source_channel = str(body.get("source_channel") or "api")
        if source_channel not in {"api", "telegram_command", "telegram_callback"}:
            source_channel = "api"

        def _generate() -> dict[str, Any]:
            info = create_graph_bundle(
                s,
                kb_id=kb_id,
                request_context={"channel": source_channel},
                account_id=bundle_account,
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
        backup_task = asyncio.create_task(_account_backup_scheduler())
        from ..feeds.poller import run_scheduler as _feed_poll_scheduler

        feed_task = asyncio.create_task(_feed_poll_scheduler(s))
        from ..media_proxy import run_cleanup_scheduler as _media_cleanup_scheduler

        media_cleanup_task = asyncio.create_task(_media_cleanup_scheduler(s))
        try:
            async with mcp_app.router.lifespan_context(mcp_app):
                yield
        finally:
            for task in (backup_task, feed_task, media_cleanup_task):
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass

    async def _account_backup_scheduler() -> None:
        """계정 자동 백업(``account_backup.run_due_backups``)을 주기적으로 실행한다. 첫 실행은 기동 1분 뒤다."""
        from ..account_backup import run_due_backups

        await asyncio.sleep(ACCOUNT_BACKUP_SCHEDULER_START_DELAY)
        while True:
            try:
                results = await asyncio.to_thread(run_due_backups, s)
                for item in results:
                    log.info("scheduled account backup: %s", item)
            except Exception as exc:  # noqa: BLE001
                log.warning("scheduled account backup cycle failed: %s", type(exc).__name__)
            await asyncio.sleep(ACCOUNT_BACKUP_SCHEDULER_INTERVAL)

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
        Route("/ns", ns_vocabulary_route, methods=["GET"]),
        Route("/ns/v1", ns_context_route, methods=["GET"]),
        Route("/sitemap.xml", sitemap_xml_route, methods=["GET"]),
        Route("/favicon.ico", favicon_ico_route, methods=["GET"]),
        Route("/favicon.svg", favicon_svg_route, methods=["GET"]),
        Route("/apple-touch-icon.png", apple_touch_route, methods=["GET"]),
        Route("/apple-touch-icon-precomposed.png", apple_touch_route, methods=["GET"]),
        Route("/manifest.json", manifest_route, methods=["GET"]),
        Route("/site.webmanifest", manifest_route, methods=["GET"]),
        Route("/browserconfig.xml", browserconfig_route, methods=["GET"]),
        Route("/sw.js", service_worker_route, methods=["GET"]),
        Route("/icon", icon_file_route, methods=["GET"]),
        Route("/font", font_file_route, methods=["GET"]),
        *(
            Route(f"/fonts/{font_name}", _create_font_file_handler(font_name), methods=["GET"])
            for font_name in FONTS
        ),
        Route("/whoami", whoami, methods=["GET"]),
        Route("/stats", stats, methods=["GET"]),
        Route("/kb", kb_collection_route, methods=["GET", "HEAD"]),
        Route("/kb", kb_create_route, methods=["POST"]),
        Route("/", kb_settings_route, methods=["PATCH"]),
        Route("/", kb_delete_route, methods=["DELETE"]),
        Route("/membership", kb_join_route, methods=["POST"]),
        Route("/membership", kb_leave_route, methods=["DELETE"]),
        Route("/members", kb_members_route, methods=["GET", "HEAD"]),
        Route("/members/{account}", kb_member_change_route, methods=["PATCH"]),
        Route("/moderation-log", kb_moderation_log_route, methods=["GET", "HEAD"]),
        Route("/api/auto-rule", auto_rule_route, methods=["GET", "HEAD", "PUT"]),
        Route("/api/auto-rule/dry-run", auto_rule_dry_run_route, methods=["POST"]),
        Route("/api/contributions", account_contributions_route, methods=["GET", "HEAD"]),
        Route("/api/contribution-blocks", contribution_blocks_route, methods=["GET", "HEAD"]),
        Route("/api/contribution-blocks/{document}", contribution_block_route, methods=["PUT", "DELETE"]),
        Route("/api/mutual-collaborations", mutual_collaborations_route, methods=["GET", "HEAD", "POST"]),
        Route("/api/mutual-collaborations/{account}", mutual_collaboration_route, methods=["PUT", "DELETE"]),
        Route("/api/admin/auto-contribution", admin_auto_contribution_route, methods=["GET", "HEAD", "PUT"]),
        Route("/api/reputation", reputation_route, methods=["GET", "HEAD"]),
        Route("/api/admin/reputations", admin_reputations_route, methods=["GET", "HEAD"]),
        Route("/api/admin/expansion/shadow", admin_expansion_shadow_route, methods=["GET", "HEAD"]),
        Route("/api/admin/expansion/shadow/{record_id}/verdict", admin_expansion_shadow_verdict_route, methods=["PUT"]),
        Route("/api/admin/reputations/settings", admin_reputation_settings_route, methods=["PUT"]),
        Route("/api/admin/themes", admin_themes_route, methods=["GET", "HEAD", "POST"]),
        Route("/api/admin/themes/settings", admin_themes_settings_route, methods=["PUT"]),
        Route("/api/admin/themes/judge", admin_themes_judge_route, methods=["POST"]),
        Route("/api/admin/themes/{number}", admin_theme_route, methods=["PATCH"]),
        Route("/api/admin/themes/{number}/knowledge-bases/{kb}", admin_theme_member_route, methods=["PUT"]),
        Route("/api/admin/themes/{number}/remote-knowledge-bases/{remote_id}", admin_theme_remote_member_route, methods=["PUT"]),
        Route("/api/admin/themes/{number}/merge", admin_theme_merge_route, methods=["POST"]),
        Route("/api/kb/similar", kb_similar_route, methods=["GET", "HEAD"]),
        Route("/explore/federation", explore_federation_route, methods=["GET", "HEAD"]),
        Route("/submissions", submissions_route, methods=["GET", "HEAD", "POST"]),
        Route("/submissions/{number}", submission_route, methods=["GET", "HEAD"]),
        Route("/curations", kb_curations_route, methods=["POST"]),
        Route("/remote-sources", kb_remote_sources_route, methods=["GET", "HEAD"]),
        Route("/remote-collaborators", kb_remote_collaborators_route, methods=["GET", "HEAD", "POST"]),
        Route("/remote-collaborators/{grant}", kb_remote_collaborator_route, methods=["DELETE"]),
        Route("/api/kb/remote-grants", kb_remote_grants_route, methods=["GET", "HEAD"]),
        Route("/forks", kb_forks_route, methods=["POST"]),
        Route("/contributions", kb_contribute_route, methods=["POST"]),
        Route("/contributions", kb_contributions_route, methods=["GET", "HEAD"]),
        Route("/contributions/{number}", kb_contribution_withdraw_route, methods=["DELETE"]),
        Route("/contributions/{number}", kb_contribution_review_route, methods=["PATCH"]),
        Route("/contributions/{number}/remove", kb_contribution_remove_route, methods=["POST"]),
        Route("/contributions/{number}/retry", kb_contribution_retry_route, methods=["POST"]),
        Route("/api/admin/kb/{number}/moderators/{account}", admin_kb_moderator_route, methods=["PUT", "DELETE"]),
        Route("/themes", themes_collection_route, methods=["GET", "HEAD"]),
        Route("/providers", providers_get_route, methods=["GET", "HEAD"]),
        Route("/providers", providers_update_route, methods=["PATCH", "POST"]),
        Route("/providers/test", providers_test_route, methods=["POST"]),
        Route("/providers/check", providers_check_route, methods=["POST"]),
        Route("/limits", limits_get_route, methods=["GET", "HEAD"]),
        Route("/limits", limits_update_route, methods=["PATCH", "POST"]),
        Route("/tokens", tokens_list_route, methods=["GET", "HEAD"]),
        Route("/tokens", tokens_create_route, methods=["POST"]),
        Route("/tokens/revoke", tokens_revoke_route, methods=["POST"]),
        Route("/tokens", tokens_revoke_route, methods=["DELETE"]),
        Route("/ingest-limits", limits_get_route, methods=["GET", "HEAD"]),
        Route("/ingest-limits", limits_update_route, methods=["PATCH", "POST"]),
        Route("/ingest", do_ingest, methods=["POST"]),
        Route("/api/federation/timeline/{item_id}/ingest", federation_timeline_ingest_route, methods=["POST"]),
        Route("/api/feeds/items/{item_id}/ingest", feed_item_ingest_route, methods=["POST"]),
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
        Route("/document/export", document_export_route, methods=["GET"]),
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
            theme = theme_mgr.get_kb(legacy.kb_id)
        except KnowledgeBaseNotFound:
            return None
        if theme.id != legacy.kb_id or theme.number is None:
            return None
        if theme.is_shared:
            return kb_base_path(theme.number)
        mode = namespace_directory.mode()
        if mode == "single":
            return "/"
        try:
            owner = namespace_directory.account(theme.account_id)
        except accounts_store.AccountNotFound:
            return None
        if owner.status != "active" or not owner.username:
            return None
        return f"/@{owner.username}/"

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

    prototype_authority_value = runtime.host_roles.prototype if runtime.host_roles is not None else None
    listeners = dev_listeners(s, prototype=prototype_authority_value)
    # The prototype listener (RD20) is a tool of the maintainer on a fixed port. When another process has that port (a second development server, parallel tests), the server starts without it.
    optional_listeners = set(listeners[1:]) - set(dev_listeners(s))
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
    import socket

    from .federation_internal import federation_socket_path, open_federation_socket

    # The internal federation listener (docs/implementation/FEDERATION.md). A failure here does not stop the public service: the sidecar cannot connect, and the federation readiness check shows that state.
    internal_path = federation_socket_path(s.data_dir)
    internal_sock: socket.socket | None = None
    if hasattr(socket, "AF_UNIX"):
        try:
            internal_sock = open_federation_socket(internal_path)
        except OSError as exc:
            print(f"internal federation listener failed {internal_path}: {exc}", flush=True)
    if internal_sock is None and len(listeners) == 1:
        uvicorn.run(app, host=s.inject_host, port=s.inject_port, **uvicorn_options)
        return 0

    # One Server opens all listener sockets: the app listener, the development role-host ports (§5.1, O16), and the internal federation socket.
    # One Server for each listener would run the lifespan (MCP session manager and others) more than one time and would replace the signal handlers.
    sockets: list[socket.socket] = []
    try:
        if internal_sock is not None:
            sockets.append(internal_sock)
        for host, port in listeners:
            sock = socket.socket(socket.AF_INET6 if ":" in host else socket.AF_INET, socket.SOCK_STREAM)
            sockets.append(sock)
            optional = (host, port) in optional_listeners
            # SO_REUSEADDR lets two processes bind the same port; the second one then fails at listen(). The optional prototype listener listens at once, so that a port in use is found here and skipped.
            if not optional:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((host, port))
                if optional:
                    sock.listen(128)
            except OSError as exc:
                if (host, port) in optional_listeners:
                    print(f"prototype listener skipped {host}:{port}: {exc}", flush=True)
                    sockets.remove(sock)
                    sock.close()
                    continue
                print(f"listen 실패 {host}:{port}: {exc}")
                return 1
            sock.set_inheritable(True)
        config = uvicorn.Config(app, host=s.inject_host, port=s.inject_port, **uvicorn_options)
        server = uvicorn.Server(config)
        for sock in sockets:
            if sock.family in (socket.AF_INET, socket.AF_INET6):
                bound_host, bound_port = sock.getsockname()[:2]
                logging.getLogger("uvicorn.error").info("Uvicorn running on http://%s:%d", bound_host, bound_port)
        if internal_sock is not None:
            logging.getLogger("uvicorn.error").info("Internal federation listener on unix:%s", internal_path)
        try:
            server.run(sockets=sockets)
        except KeyboardInterrupt:
            pass
    finally:
        for sock in sockets:
            sock.close()
        if internal_sock is not None:
            try:
                internal_path.unlink()
            except OSError:
                pass
    return 0 if server.started else 1
