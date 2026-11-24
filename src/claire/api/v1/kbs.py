"""System, identity, knowledge base, and theme endpoints for `/api/v1/*`."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, Literal

from starlette.requests import Request
from starlette.responses import JSONResponse

from ...accounts import provider_policy as policy
from ...collaboration import auto_contribution, contributions
from ...core import ids
from ...federation.knowledge import remote_kbs
from ...ingest.active import get_active_jobs, get_primary_active_job
from ...ingest.extraction_mode import available_extraction_modes
from ...notifications import events as notification_events
from ...ops.health import liveness_report
from ...store import auth_db, db as dbm, kb_collab, themes as themes_store
from ...store.knowledge_base import KnowledgeBaseInfo, embed_mismatch
from ...store.legacy_hosts import SqliteLegacyHostDirectory
from ...store.queries import theme_summary
from ...store.vectors import resolve_embed_identity
from ..namespace import kb_base_path, request_namespace
from ..security import request_auth_scope
from ._context import (
    V1Context,
    enforce_v1_auth_policy,
    kb_decide,
    kb_number_str,
    kb_runtime_context,
    public_origin,
    read_v1_json,
    reject_retired_params,
    resolve_kb,
)
from .errors import V1Error, v1_endpoint
from .pagination import decode_number_cursor, encode_number_cursor, paginated_payload, parse_limit
from .schemas import (
    CreateKnowledgeBaseRequest,
    DeleteKnowledgeBasePlan,
    DeleteKnowledgeBaseResponse,
    HealthResponse,
    KnowledgeBaseDetailResponse,
    KnowledgeBaseListResponse,
    KnowledgeBaseStats,
    KnowledgeBaseSummary,
    LegacyHostEntry,
    MeResponse,
    ServerModeInfo,
    ThemeListResponse,
    ThemeSummary,
    UpdateKnowledgeBaseRequest,
)


def _reembed_required(ctx: V1Context, kb: KnowledgeBaseInfo) -> bool:
    if kb.is_shared:
        shared_provider = getattr(ctx.service_pool, "shared_provider", None) or getattr(ctx.service, "provider", None)
        pin = resolve_embed_identity(shared_provider, ctx.settings)
    else:
        try:
            kb_svc = ctx.service_pool.get_service(kb.id)
        except Exception:  # noqa: BLE001
            return False
        provider_ctx = getattr(kb_svc, "provider_context", None)
        if provider_ctx is None:
            pin = resolve_embed_identity(getattr(kb_svc, "provider", None), ctx.settings)
        elif getattr(provider_ctx, "provider", "") in ("", "none"):
            return False
        else:
            pin = resolve_embed_identity(SimpleNamespace(name=provider_ctx.provider, embed_model=provider_ctx.embed_model), ctx.settings)
    conn = dbm.connect_existing(ctx.settings.db_file, readonly=True)
    try:
        row = conn.execute("SELECT embed_provider, embed_model FROM knowledge_bases WHERE id=?", (kb.id,)).fetchone()
    except Exception:  # noqa: BLE001
        row = None
    finally:
        conn.close()
    if row is None:
        return False
    fresh = KnowledgeBaseInfo(
        id=kb.id,
        seq=kb.seq,
        label=kb.label,
        embed_provider=str(row[0] or ""),
        embed_model=str(row[1] or ""),
    )
    return embed_mismatch(fresh, pin.provider, pin.model)


def _kb_summary(
    ctx: V1Context,
    request: Request,
    kb: KnowledgeBaseInfo,
    *,
    role: str | None = None,
    membership_state: str | None = None,
    origin: str,
) -> KnowledgeBaseSummary:
    settings_view = kb_decide(ctx, request, kb, "settings") == "allow"
    principal = enforce_v1_auth_policy(request)
    include_hidden = (principal.account_id == kb.account_id) if kb.is_personal else (kb_decide(ctx, request, kb, "review") == "allow")
    t_stats = {"documents": 0, "entities": 0, "relations": 0}
    abs_db = ctx.theme_mgr.get_settings_for_kb(kb.id, ctx.settings).db_file
    if abs_db.is_file():
        conn = dbm.connect_existing(abs_db, readonly=True)
        try:
            t_stats = theme_summary(conn, include_hidden=include_hidden, kb_id=kb.id)
        finally:
            conn.close()

    legacy_hosts: list[LegacyHostEntry] | None = None
    active_moderators: int | None = None
    reembed: bool | None = None
    sorcerer: str | None = None
    ga_id: str | None = None
    if settings_view:
        sorcerer = (kb.sorcerer or "").strip()
        ga_id = kb.ga_measurement_id
        reembed = _reembed_required(ctx, kb)
        legacy_hosts = [
            LegacyHostEntry(
                host=h.host,
                activated_at=float(h.activated_at or 0.0),
                sunset_at=float(h.sunset_at) if h.sunset_at is not None else None,
            )
            for h in SqliteLegacyHostDirectory(ctx.settings.db_file).list_hosts_for_theme(kb.id)
        ]
        if kb.is_shared:
            conn = dbm.connect_existing(ctx.settings.db_file, readonly=True)
            try:
                active_moderators = len(kb_collab.active_moderators(conn, kb.id))
            finally:
                conn.close()

    url = f"{origin}{kb_base_path(kb.number)}" if (kb.is_shared and kb.number is not None) else None
    ktype = kb.knowledge_type if kb.knowledge_type in ("real", "fictional") else None
    return KnowledgeBaseSummary(
        number=kb_number_str(kb),
        label=kb.label,
        description=kb.description,
        icon=kb.icon,
        is_personal=bool(kb.is_personal),
        is_shared=bool(kb.is_shared),
        is_public=bool(kb.is_public),
        knowledge_type=ktype,  # type: ignore[arg-type]
        join_policy=kb.join_policy,
        state=kb.state,
        presentation_enabled=bool(kb.presentation_enabled),
        seed_entities=list(kb.seed_entities),
        url=url,
        role=role,
        membership_state=membership_state,
        stats=KnowledgeBaseStats(
            documents=int(t_stats.get("documents", 0)),
            entities=int(t_stats.get("entities", 0)),
            relations=int(t_stats.get("relations", 0)),
        ),
        created_at=float(kb.created_at),
        updated_at=float(kb.updated_at),
        archived_at=float(kb.archived_at) if kb.archived_at is not None else None,
        embed_provider=kb.embed_provider,
        embed_model=kb.embed_model,
        sorcerer=sorcerer,
        ga_measurement_id=ga_id,
        reembed_required=reembed,
        active_moderators=active_moderators,
        legacy_hosts=legacy_hosts,
    )


def _require_multi_kb(ctx: V1Context) -> None:
    if not getattr(ctx.settings, "multi_theme", False):
        raise V1Error(403, "multi_theme_disabled", "Multi-KB mode is disabled (CLAIRE_MULTI_THEME=1 required)")


def _after_kb_founded(ctx: V1Context, kb_id: int, founder_account_id: int) -> None:
    conn = dbm.connect_existing(ctx.settings.db_file)
    try:
        granted = auto_contribution.grant_mutual_collaborators(conn, kb_id=kb_id, founder_account_id=founder_account_id)
        conn.commit()
        if granted:
            notification_events.kb_role_given(
                ctx.settings.db_file,
                kb_id=kb_id,
                account_ids=granted,
                role="collaborator",
                actor_account_id=founder_account_id,
            )
        made = auto_contribution.run_all(conn, origin=contributions.canonical_origin(ctx.settings))
    finally:
        conn.close()
    if made:
        _run_all_merge_jobs(ctx)


def _run_all_merge_jobs(ctx: V1Context) -> None:
    conn = dbm.connect_existing(ctx.settings.db_file)
    try:
        contributions.run_pending_merge_jobs(
            conn,
            lambda kb_id: ctx.service_pool.get_service(kb_id).provider,
            lambda kb_id: ctx.theme_mgr.get_settings_for_kb(kb_id, ctx.settings),
        )
    finally:
        conn.close()


def build_kb_handlers(ctx: V1Context) -> dict[str, Any]:
    """Build Starlette route handlers for system, KB, and theme v1 endpoints."""

    @v1_endpoint
    async def health(request: Request) -> JSONResponse:
        del request
        report = await asyncio.to_thread(liveness_report, ctx.settings)
        ok = bool(report.get("ok"))
        body = HealthResponse(ok=ok, api_version="v1", database="ok" if ok else "unavailable")
        return JSONResponse(body.model_dump(), status_code=200 if ok else 503)

    @v1_endpoint
    async def me(request: Request) -> JSONResponse:
        principal = enforce_v1_auth_policy(request)
        if principal.is_anonymous or principal.account_id is None:
            raise V1Error(401, "authentication_required", "Authentication required")
        scope = request_auth_scope(request) or principal.scope
        if scope not in ("owner", "write", "readonly"):
            raise V1Error(401, "authentication_required", "Authentication required")
        try:
            mode = request_namespace(request).mode
        except Exception:
            mode = "single"
        relation = "owner" if scope == "owner" else "delegate"
        is_admin = bool(principal.server_role == "admin" and scope == "owner")
        body = MeResponse(
            scope=scope,  # type: ignore[arg-type]
            server=ServerModeInfo(mode=mode),  # type: ignore[arg-type]
            relation=relation,  # type: ignore[arg-type]
            is_admin=is_admin,
        )
        return JSONResponse(body.model_dump())

    @v1_endpoint
    async def list_kbs(request: Request) -> JSONResponse:
        principal = enforce_v1_auth_policy(request)
        reject_retired_params(request)
        params = request.query_params
        knowledge_type = params.get("knowledge_type")
        if knowledge_type is not None and knowledge_type not in ("real", "fictional"):
            raise V1Error(400, "invalid_knowledge_type", "knowledge_type must be 'real' or 'fictional'")
        joined_raw = params.get("joined")
        if joined_raw is not None and joined_raw not in ("true", "false"):
            raise V1Error(400, "invalid_joined", "joined must be 'true' or 'false'")
        joined = joined_raw == "true"
        try:
            limit = parse_limit(params.get("limit"), default=50, minimum=1, maximum=100)
        except ValueError as exc:
            raise V1Error(400, "invalid_limit", "limit must be an integer between 1 and 100") from exc
        raw_cursor = params.get("cursor")
        cursor_number: int | None = None
        if raw_cursor:
            try:
                cursor_number = decode_number_cursor(raw_cursor)
            except ValueError as exc:
                raise V1Error(400, "invalid_cursor", "Invalid pagination cursor") from exc
        origin = public_origin(ctx, request)

        def _build() -> KnowledgeBaseListResponse:
            shared = ctx.theme_mgr.list_shared_kbs(knowledge_type=knowledge_type) if getattr(ctx.settings, "multi_theme", False) else []
            roles: dict[int, str] = {}
            states: dict[int, str] = {}
            if principal.account_id is not None:
                conn = dbm.connect_existing(ctx.settings.db_file, readonly=True)
                try:
                    roles = kb_collab.account_memberships(conn, principal.account_id, state="active")
                    states = kb_collab.account_membership_states(conn, principal.account_id)
                finally:
                    conn.close()
            if joined:
                shared = [kb for kb in shared if kb.id in roles]
            total = len(shared)
            filtered = [kb for kb in shared if cursor_number is None or (kb.number or 0) < cursor_number]
            page_kbs = filtered[:limit]
            has_more = len(filtered) > limit
            items = [
                _kb_summary(ctx, request, kb, role=roles.get(kb.id), membership_state=states.get(kb.id), origin=origin)
                for kb in page_kbs
            ]
            next_cursor = None
            if has_more and page_kbs and page_kbs[-1].number is not None:
                next_cursor = encode_number_cursor(int(page_kbs[-1].number))

            personal_summary: KnowledgeBaseSummary | None = None
            if principal.account_id is not None and knowledge_type is None and not joined:
                try:
                    personal_kb = ctx.theme_mgr.personal_kb_for(principal.account_id)
                except Exception:  # noqa: BLE001
                    personal_kb = None
                if personal_kb is not None and kb_decide(ctx, request, personal_kb, "read") == "allow":
                    personal_summary = _kb_summary(ctx, request, personal_kb, origin=origin)
            return KnowledgeBaseListResponse.model_validate(
                {
                    **paginated_payload(items, next_cursor=next_cursor, total=total),
                    "personal_kb": personal_summary,
                }
            )

        payload = await asyncio.to_thread(_build)
        return JSONResponse(payload.model_dump(exclude_none=False))

    @v1_endpoint
    async def create_kb(request: Request) -> JSONResponse:
        principal = enforce_v1_auth_policy(request)
        reject_retired_params(request)
        _require_multi_kb(ctx)
        if principal.account_id is None or principal.is_anonymous:
            raise V1Error(401, "authentication_required", "Authentication required")
        body = await read_v1_json(request, CreateKnowledgeBaseRequest)
        label = body.label.strip()
        if not label:
            raise V1Error(400, "invalid_label", "label is required")
        try:
            kb = await asyncio.to_thread(
                ctx.theme_mgr.define_kb,
                label,
                description=body.description.strip(),
                icon=body.icon.strip() or "📁",
                is_public=body.is_public,
                ga_measurement_id=body.ga_measurement_id.strip(),
                sorcerer=body.sorcerer.strip(),
                presentation_enabled=body.presentation_enabled,
                account_id=principal.account_id,
                knowledge_type=body.knowledge_type,
                seed_entities=body.seed_entities,
            )
        except ValueError as exc:
            raise V1Error(400, "invalid_request", str(exc)) from exc
        await asyncio.to_thread(_after_kb_founded, ctx, kb.id, int(principal.account_id))
        origin = public_origin(ctx, request)
        summary = await asyncio.to_thread(_kb_summary, ctx, request, kb, origin=origin)
        resp = KnowledgeBaseDetailResponse(knowledge_base=summary)
        return JSONResponse(resp.model_dump(exclude_none=True), status_code=201)

    @v1_endpoint
    async def get_kb(request: Request) -> JSONResponse:
        kb = resolve_kb(ctx, request, capability="read")
        _, kb_settings, kb_svc = kb_runtime_context(ctx, request, kb)
        origin = public_origin(ctx, request)
        scope = request_auth_scope(request)

        def _build() -> KnowledgeBaseDetailResponse:
            role: str | None = None
            m_state: str | None = None
            principal = enforce_v1_auth_policy(request)
            if kb.is_shared and principal.account_id is not None:
                conn = dbm.connect_existing(ctx.settings.db_file, readonly=True)
                try:
                    role = kb_collab.account_memberships(conn, principal.account_id, state="active").get(kb.id)
                    m_state = kb_collab.account_membership_states(conn, principal.account_id).get(kb.id)
                finally:
                    conn.close()
            summary = _kb_summary(ctx, request, kb, role=role, membership_state=m_state, origin=origin)
            ingest_effort: dict[str, Any] | None = None
            extraction_modes: list[Literal["provider", "client"]] | None = None
            ingesting: bool | None = None
            active_jobs: list[dict[str, Any]] | None = None
            if scope in ("owner", "write"):
                ingest_effort = policy.describe_ingest_effort_options(
                    getattr(kb_svc, "s", kb_settings),
                    ctx=getattr(kb_svc, "provider_context", None),
                    provider_error=getattr(kb_svc, "provider_error", None),
                )
                raw_modes = available_extraction_modes(
                    getattr(kb_svc, "s", kb_settings),
                    ctx=getattr(kb_svc, "provider_context", None),
                    provider_error=getattr(kb_svc, "provider_error", None),
                )
                extraction_modes = ["provider" if m == "provider" else "client" for m in raw_modes if m in ("provider", "client")]
                jobs = [job for job in get_active_jobs(kb_settings.data_dir) if int(job.get("kb_id") or 0) == kb.id]
                primary = get_primary_active_job(kb_settings.data_dir, kb_id=kb.id, jobs=jobs)
                active_jobs = jobs
                ingesting = bool(primary.get("active")) if primary else False
            return KnowledgeBaseDetailResponse(
                knowledge_base=summary,
                ingest_effort=ingest_effort,
                extraction_modes=extraction_modes,
                ingesting=ingesting,
                active_jobs=active_jobs,
            )

        resp = await asyncio.to_thread(_build)
        return JSONResponse(resp.model_dump(exclude_none=True))

    @v1_endpoint
    async def update_kb(request: Request) -> JSONResponse:
        principal = enforce_v1_auth_policy(request)
        _require_multi_kb(ctx)
        kb = resolve_kb(ctx, request, capability="settings")
        body = await read_v1_json(request, UpdateKnowledgeBaseRequest)
        if not body.model_fields_set:
            raise V1Error(400, "invalid_request", "At least one updatable field is required")
        updates: dict[str, Any] = {}
        for field_name in body.model_fields_set:
            val = getattr(body, field_name)
            if val is None and field_name != "seed_entities":
                raise V1Error(400, f"{field_name}_invalid", f"{field_name} must not be null")
            updates[field_name] = val
        if kb.is_shared and principal.server_role != "admin":
            refused = sorted(set(updates) & {"ga_measurement_id", "sorcerer", "presentation_enabled"})
            if refused:
                raise V1Error(403, f"{refused[0]}_admin_only", f"{refused[0]} can only be changed by an admin")
        try:
            updated = await asyncio.to_thread(ctx.theme_mgr.update_kb, kb.id, **updates)
        except KeyError as exc:
            raise V1Error(404, "not_found", "Knowledge base not found") from exc
        except ValueError as exc:
            raise V1Error(400, "invalid_request", str(exc)) from exc
        if updated.is_shared:
            def _log_mod() -> None:
                conn = dbm.connect_existing(ctx.settings.db_file)
                try:
                    kb_collab.log_moderation(
                        conn,
                        kb_id=updated.id,
                        actor_account_id=principal.account_id,
                        action="settings",
                        target=",".join(sorted(updates)),
                    )
                    conn.commit()
                    if "seed_entities" in updates:
                        made = auto_contribution.run_all(conn, origin=contributions.canonical_origin(ctx.settings))
                        if made:
                            contributions.run_pending_merge_jobs(
                                conn,
                                lambda kb_id: ctx.service_pool.get_service(kb_id).provider,
                                lambda kb_id: ctx.theme_mgr.get_settings_for_kb(kb_id, ctx.settings),
                            )
                finally:
                    conn.close()

            await asyncio.to_thread(_log_mod)
        origin = public_origin(ctx, request)
        summary = await asyncio.to_thread(_kb_summary, ctx, request, updated, origin=origin)
        resp = KnowledgeBaseDetailResponse(knowledge_base=summary)
        return JSONResponse(resp.model_dump(exclude_none=True))

    @v1_endpoint
    async def delete_kb(request: Request) -> JSONResponse:
        principal = enforce_v1_auth_policy(request)
        _require_multi_kb(ctx)
        kb = resolve_kb(ctx, request, capability="delete")
        raw_apply = request.query_params.get("apply")
        if raw_apply not in (None, "true", "false"):
            raise V1Error(400, "invalid_apply", "apply must be 'true' or 'false'")
        if kb.is_personal:
            raise V1Error(400, "kb_personal_cannot_delete", "Personal knowledge bases cannot be deleted")

        def _plan() -> DeleteKnowledgeBasePlan:
            conn = dbm.connect_existing(ctx.settings.db_file, readonly=True)
            try:
                counts = theme_summary(conn, include_hidden=True, kb_id=kb.id)
                members = conn.execute("SELECT COUNT(*) FROM kb_memberships WHERE kb_id=?", (kb.id,)).fetchone()[0]
                contribs = conn.execute("SELECT COUNT(*) FROM kb_contributions WHERE kb_id=?", (kb.id,)).fetchone()[0]
                return DeleteKnowledgeBasePlan(
                    documents=int(counts["documents"]),
                    entities=int(counts["entities"]),
                    relations=int(counts["relations"]),
                    members=int(members),
                    contributions=int(contribs),
                )
            finally:
                conn.close()

        origin = public_origin(ctx, request)
        plan = await asyncio.to_thread(_plan)
        summary_before = await asyncio.to_thread(_kb_summary, ctx, request, kb, origin=origin)
        if raw_apply != "true":
            resp = DeleteKnowledgeBaseResponse(applied=False, knowledge_base=summary_before, would_delete=plan)
            return JSONResponse(resp.model_dump(exclude_none=True))

        try:
            deleted = await asyncio.to_thread(ctx.theme_mgr.delete_kb, kb.id, purge=True)
        except ValueError as exc:
            raise V1Error(400, "invalid_request", str(exc)) from exc
        if hasattr(ctx.service_pool, "remove_service"):
            ctx.service_pool.remove_service(deleted.id)
        actor_id = principal.account_id
        if actor_id is not None:
            def _audit() -> None:
                auth_conn = auth_db.connect_auth_db(auth_db.auth_db_path(ctx.settings.data_dir))
                try:
                    auth_db.record_admin_audit(
                        auth_conn,
                        actor_account_id=actor_id,
                        action="kb_delete",
                        detail={"kb_number": kb_number_str(deleted), "deleted": plan.model_dump()},
                    )
                finally:
                    auth_conn.close()

            await asyncio.to_thread(_audit)
        resp = DeleteKnowledgeBaseResponse(applied=True, knowledge_base=summary_before, would_delete=plan)
        return JSONResponse(resp.model_dump(exclude_none=True))

    @v1_endpoint
    async def list_themes(request: Request) -> JSONResponse:
        enforce_v1_auth_policy(request)
        reject_retired_params(request)
        params = request.query_params
        knowledge_type = params.get("knowledge_type")
        if knowledge_type is not None and knowledge_type not in ("real", "fictional"):
            raise V1Error(400, "invalid_knowledge_type", "knowledge_type must be 'real' or 'fictional'")
        try:
            limit = parse_limit(params.get("limit"), default=50, minimum=1, maximum=100)
        except ValueError as exc:
            raise V1Error(400, "invalid_limit", "limit must be an integer between 1 and 100") from exc
        raw_cursor = params.get("cursor")
        cursor_number: int | None = None
        if raw_cursor:
            try:
                cursor_number = decode_number_cursor(raw_cursor)
            except ValueError as exc:
                raise V1Error(400, "invalid_cursor", "Invalid pagination cursor") from exc
        origin = public_origin(ctx, request)

        def _build() -> ThemeListResponse:
            conn = dbm.connect_existing(ctx.settings.db_file, readonly=True)
            try:
                rows = themes_store.list_themes(conn, knowledge_type=knowledge_type)
                remote_ids = {kb.id for kb in remote_kbs.list_all(conn)}
                sorted_rows = sorted(rows, key=lambda r: int(r["number"]), reverse=True)
                total = len(sorted_rows)
                filtered = [r for r in sorted_rows if cursor_number is None or int(r["number"]) < cursor_number]
                page_rows = filtered[:limit]
                has_more = len(filtered) > limit
                items: list[ThemeSummary] = []
                for row in page_rows:
                    num_int = int(row["number"])
                    num_str = ids.format_public_id(num_int)
                    ktype_lit: Literal["real", "fictional"] = "fictional" if str(row["knowledge_type"]) == "fictional" else "real"
                    items.append(
                        ThemeSummary(
                            number=num_str,
                            url=f"{origin}/themes/{num_str}",
                            label=str(row["label"]),
                            description=str(row["description"] or ""),
                            knowledge_type=ktype_lit,
                            state=str(row["state"]),
                            knowledge_bases=len(themes_store.theme_members(conn, int(row["id"]))),
                            remote_knowledge_bases=sum(
                                1
                                for m in themes_store.remote_members(conn, theme_id=int(row["id"]))
                                if int(m["remote_kb_id"]) in remote_ids
                            ),
                        )
                    )
                next_cursor = encode_number_cursor(int(page_rows[-1]["number"])) if (has_more and page_rows) else None
                return ThemeListResponse.model_validate(paginated_payload(items, next_cursor=next_cursor, total=total))
            finally:
                conn.close()

        payload = await asyncio.to_thread(_build)
        return JSONResponse(payload.model_dump())

    return {
        "health": health,
        "me": me,
        "list_kbs": list_kbs,
        "create_kb": create_kb,
        "get_kb": get_kb,
        "update_kb": update_kb,
        "delete_kb": delete_kb,
        "list_themes": list_themes,
    }
