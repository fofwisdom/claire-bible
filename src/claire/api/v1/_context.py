"""Shared runtime context, CT23 KB resolution, and capability helpers for `/api/v1/*`."""

from __future__ import annotations

import asyncio
import sqlite3
from dataclasses import dataclass
from typing import Any, TypeVar

from pydantic import BaseModel
from starlette.requests import Request

from ...accounts import kb_access
from ...core import ids
from ...ingest.client_extraction import resolve_oauth_client_id
from ...ingest.service import IngestService, IngestServicePool
from ...store import db as dbm
from ...store import kb_collab
from ...store.knowledge_base import KnowledgeBaseInfo, KnowledgeBaseManager, KnowledgeBaseNotFound
from ...store.queries import documents_with_hidden_source, hide_document_source
from ..namespace import NamespaceDirectory, SqliteNamespaceDirectory, request_namespace
from ..principal import Principal, request_principal
from ..security import MAX_REQUEST_BODY, InvalidJSONBody, WebRuntimeConfig, read_json_body, request_auth_scope
from .errors import V1Error, not_found_error

ModelT = TypeVar("ModelT", bound=BaseModel)

MAX_ANONYMOUS_SEARCH_RESULTS = 20
MAX_SEARCH_RESULTS = 50
MAX_EXPENSIVE_CONCURRENT = 4
MAX_ANONYMOUS_SEARCH_CONCURRENT = 2


@dataclass
class V1Context:
    """Shared dependencies and concurrency guards for v1 endpoint handlers."""

    settings: Any
    service: IngestService
    theme_mgr: KnowledgeBaseManager
    service_pool: IngestServicePool
    namespace_directory: NamespaceDirectory
    public_origin_fallback: str = ""
    active_expensive_jobs: int = 0
    active_anonymous_search_jobs: int = 0

    @classmethod
    def build(
        cls,
        settings: Any,
        *,
        service: Any | None = None,
        theme_manager: KnowledgeBaseManager | None = None,
        service_pool: IngestServicePool | None = None,
        namespace_directory: NamespaceDirectory | None = None,
    ) -> V1Context:
        svc = service if service is not None else IngestService(settings)
        mgr = theme_manager if theme_manager is not None else KnowledgeBaseManager(settings)
        pool = service_pool if service_pool is not None else IngestServicePool(settings, svc, theme_manager=mgr)
        ns_dir = namespace_directory if namespace_directory is not None else SqliteNamespaceDirectory(settings.db_file)
        origin = ""
        try:
            origin = WebRuntimeConfig.from_settings(settings).public_origin.rstrip("/")
        except Exception:
            origin = ""
        return cls(
            settings=settings,
            service=svc,
            theme_mgr=mgr,
            service_pool=pool,
            namespace_directory=ns_dir,
            public_origin_fallback=origin,
        )

    async def run_expensive(self, func: Any, /, *args: Any, **kwargs: Any) -> Any:
        if self.active_expensive_jobs >= MAX_EXPENSIVE_CONCURRENT:
            raise V1Error(429, "server_busy", "Server is busy; retry later")
        self.active_expensive_jobs += 1
        try:
            return await asyncio.to_thread(func, *args, **kwargs)
        finally:
            self.active_expensive_jobs = max(0, self.active_expensive_jobs - 1)

    async def run_anonymous_search(self, func: Any, /, *args: Any, **kwargs: Any) -> Any:
        if self.active_anonymous_search_jobs >= MAX_ANONYMOUS_SEARCH_CONCURRENT:
            raise V1Error(429, "server_busy", "Server is busy; retry later")
        self.active_anonymous_search_jobs += 1
        try:
            return await asyncio.to_thread(func, *args, **kwargs)
        finally:
            self.active_anonymous_search_jobs = max(0, self.active_anonymous_search_jobs - 1)


def enforce_v1_auth_policy(request: Request) -> Principal:
    """Enforce AR5 (no legacy manual tokens) and return the request Principal."""
    principal = request_principal(request)
    cred_id = principal.credential_id or ""
    if cred_id.startswith(("api:", "env:")):
        raise V1Error(
            401,
            "invalid_token",
            "Legacy manual tokens are retired; use OAuth 2.1 or a browser session",
        )
    return principal


def reject_retired_params(request: Request) -> None:
    """Reject retired query parameters such as `theme` (AR8, CT23)."""
    if "theme" in request.query_params:
        raise V1Error(
            400,
            "invalid_request",
            "Query parameter 'theme' is retired; use the {kb} path parameter",
        )


async def read_v1_json_dict(
    request: Request,
    *,
    max_bytes: int = MAX_REQUEST_BODY,
) -> dict[str, Any]:
    """Read a strict `application/json` object body."""
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type != "application/json":
        raise InvalidJSONBody("content-type must be application/json")
    return await read_json_body(request, max_bytes=max_bytes)


async def read_v1_json(
    request: Request,
    model_cls: type[ModelT],
    *,
    max_bytes: int = MAX_REQUEST_BODY,
) -> ModelT:
    """Parse and validate a sealed Pydantic input model from a strict JSON body."""
    raw = await read_v1_json_dict(request, max_bytes=max_bytes)
    return model_cls.model_validate(raw)


def kb_number_str(kb: KnowledgeBaseInfo) -> str | None:
    """Format a knowledge base external number as a decimal string."""
    return ids.format_public_id(kb.number) if kb.number is not None else None


def public_origin(ctx: V1Context, request: Request) -> str:
    """Resolve the public origin for building absolute resource URLs."""
    if ctx.public_origin_fallback:
        return ctx.public_origin_fallback
    return str(request.base_url).rstrip("/")


def kb_actor(ctx: V1Context, request: Request, kb: KnowledgeBaseInfo) -> kb_access.Actor:
    """Build the `kb_access.Actor` for the current request."""
    principal = enforce_v1_auth_policy(request)
    membership: tuple[str, str] | None = None
    if kb.is_shared and principal.account_id is not None:
        conn = dbm.connect_existing(ctx.settings.db_file, readonly=True)
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


def _single_mode(request: Request) -> bool:
    try:
        return request_namespace(request).mode == "single"
    except Exception:
        return True


def kb_decide(ctx: V1Context, request: Request, kb: KnowledgeBaseInfo, capability: str) -> str:
    """Evaluate a KB capability decision (`allow`, `forbidden`, or `not_found`)."""
    if capability == kb_access.NO_KB:
        raise RuntimeError("route declares no knowledge base capability")
    if kb.is_personal:
        capability = kb_access.personal_capability(capability)
    elif capability == "write":
        return "forbidden"
    return kb_access.decide(
        is_personal=kb.is_personal,
        owner_account_id=kb.account_id,
        is_public=kb.is_public,
        capability=capability,
        actor=kb_actor(ctx, request, kb),
        single_mode=_single_mode(request),
        archived=kb.is_shared and kb.state == "archived",
    )


def require_kb(ctx: V1Context, request: Request, kb: KnowledgeBaseInfo, capability: str) -> None:
    """Enforce a KB capability decision, preserving 404 existence-hiding."""
    decision = kb_decide(ctx, request, kb, capability)
    if decision == "not_found":
        raise not_found_error("Knowledge base not found")
    if decision == "forbidden":
        if kb.is_shared and capability == "write":
            code = "kb_ingest_by_contribution"
            msg = "Shared knowledge bases accept documents via contributions"
        elif kb.is_shared and kb.state == "archived" and capability in kb_access.ARCHIVED_BLOCKED:
            code = "kb_archived"
            msg = "Archived knowledge base is read-only"
        else:
            code = "kb_capability_required"
            msg = "Insufficient knowledge base permissions"
        raise V1Error(403, code, msg)


def resolve_kb(
    ctx: V1Context,
    request: Request,
    *,
    capability: str = "read",
    kb_param: str | None = None,
) -> KnowledgeBaseInfo:
    """Resolve `{kb}` strictly per CT23 (`"personal"` or external KB number)."""
    principal = enforce_v1_auth_policy(request)
    reject_retired_params(request)
    raw = str(kb_param if kb_param is not None else request.path_params.get("kb") or "").strip()
    if raw == "personal":
        if principal.account_id is None or principal.is_anonymous:
            raise not_found_error("Knowledge base not found")
        try:
            kb = ctx.theme_mgr.personal_kb_for(principal.account_id)
        except KnowledgeBaseNotFound as exc:
            raise not_found_error("Knowledge base not found") from exc
    else:
        if not raw or not raw.isascii() or not raw.isdigit() or raw[0] == "0":
            raise V1Error(400, "invalid_kb", "kb must be 'personal' or an external knowledge base number")
        try:
            number = ids.parse_public_id(raw)
        except ValueError as exc:
            raise V1Error(400, "invalid_kb", "kb must be 'personal' or an external knowledge base number") from exc
        if number < ids.MIN_CURRENT_NUMBER:
            raise V1Error(400, "invalid_kb", "Internal integer kb_id is not accepted; use 'personal' or an external knowledge base number")
        try:
            kb = ctx.theme_mgr.get_kb_by_number(number)
        except KnowledgeBaseNotFound as exc:
            raise not_found_error("Knowledge base not found") from exc
    if not getattr(ctx.settings, "multi_theme", False) and kb.id != 0:
        raise not_found_error("Knowledge base not found")
    require_kb(ctx, request, kb, capability)
    return kb


def kb_runtime_context(
    ctx: V1Context,
    request: Request,
    kb: KnowledgeBaseInfo,
) -> tuple[KnowledgeBaseInfo, Any, IngestService]:
    """Return `(kb, kb_settings, ingest_service)` for an already authorized KB."""
    try:
        kb_settings = ctx.theme_mgr.get_settings_for_kb(kb.id, ctx.settings)
        if kb.is_shared:
            acting = enforce_v1_auth_policy(request).account_id
            if acting is not None and hasattr(kb_settings, "model_copy"):
                kb_settings = kb_settings.model_copy(update={"account_id": acting})
            kb_svc = ctx.service_pool.get_service(kb.id, acting_account_id=acting)
        else:
            kb_svc = ctx.service_pool.get_service(kb.id)
    except KnowledgeBaseNotFound as exc:
        raise not_found_error("Knowledge base not found") from exc
    return kb, kb_settings, kb_svc


def resolve_personal_kb_context(
    ctx: V1Context,
    request: Request,
) -> tuple[KnowledgeBaseInfo, Any, IngestService]:
    """Resolve the caller's personal KB context for ingest routes (CT4)."""
    principal = enforce_v1_auth_policy(request)
    reject_retired_params(request)
    if principal.account_id is None or principal.is_anonymous:
        raise V1Error(401, "authentication_required", "Authentication required")
    try:
        kb = ctx.theme_mgr.personal_kb_for(principal.account_id)
        kb_settings = ctx.theme_mgr.get_settings_for_kb(kb.id, ctx.settings)
        kb_svc = ctx.service_pool.get_service(kb.id)
    except KnowledgeBaseNotFound as exc:
        raise not_found_error("Knowledge base not found") from exc
    return kb, kb_settings, kb_svc


def sees_hidden(ctx: V1Context, request: Request, kb: KnowledgeBaseInfo) -> bool:
    """Return True when the caller can read hidden documents in `kb`."""
    if kb.is_shared:
        return kb_decide(ctx, request, kb, "review") == "allow"
    principal = enforce_v1_auth_policy(request)
    return (
        principal.account_id is not None
        and principal.account_id == kb.account_id
        and request_auth_scope(request) != "anonymous"
    )


def hides_sources(ctx: V1Context, request: Request, kb: KnowledgeBaseInfo) -> bool:
    """Return True when personal KB source URLs may be masked for non-owners."""
    if not kb.is_personal or kb.account_id is None:
        return False
    principal = enforce_v1_auth_policy(request)
    return not (
        principal.account_id is not None
        and principal.account_id == kb.account_id
        and request_auth_scope(request) != "anonymous"
    )


def apply_source_rule(
    conn: sqlite3.Connection,
    kb: KnowledgeBaseInfo,
    documents: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Strip hidden source URLs from document dicts when required."""
    hidden = documents_with_hidden_source(
        conn,
        account_id=int(kb.account_id or 0),
        kb_id=kb.id,
        document_ids=[str(d.get("id") or "") for d in documents],
    )
    return [hide_document_source(d) if str(d.get("id") or "") in hidden else d for d in documents]


def is_agent_client_request(ctx: V1Context, request: Request) -> bool:
    """Return True when authenticated by an OAuth 2.1 agent client token (AED4-rev)."""
    principal = enforce_v1_auth_policy(request)
    credential_id = principal.credential_id or ""
    if principal.channel != "bearer" or principal.account_id is None or not credential_id or credential_id.startswith(("api:", "env:")):
        return False
    conn = dbm.connect_existing(ctx.settings.db_file, readonly=True)
    try:
        return resolve_oauth_client_id(
            conn,
            account_id=int(principal.account_id),
            credential_id=credential_id,
            credential_kind="bearer",
        ) is not None
    finally:
        conn.close()


async def refuse_server_api_for_agent_client(ctx: V1Context, request: Request) -> None:
    """Reject agent client requests attempting `extraction_mode='provider'` (AED4-rev)."""
    if await asyncio.to_thread(is_agent_client_request, ctx, request):
        raise V1Error(
            403,
            "agent_client_provider_refused",
            "Agent clients must extract sources locally using extraction_mode='client'",
        )


def entity_language_kwargs(request: Request) -> dict[str, Any]:
    """Return `entity_language` keyword argument when set on the Principal."""
    lang = request_principal(request).entity_language
    return {"entity_language": lang} if lang else {}
