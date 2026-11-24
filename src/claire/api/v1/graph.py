"""Graph, entity, neighborhood, path, and search endpoints for `/api/v1/*`."""

from __future__ import annotations

import asyncio
from typing import Any, Literal

from starlette.requests import Request
from starlette.responses import JSONResponse

from ...ontology.label_language import display_label
from ...store import db as dbm, permalinks
from ...store.queries import graph_json, node_detail
from ..mcp_tools import graph_neighbors_impl, graph_path_impl
from ..security import request_auth_scope
from ._context import (
    MAX_ANONYMOUS_SEARCH_RESULTS,
    MAX_SEARCH_RESULTS,
    V1Context,
    apply_source_rule,
    enforce_v1_auth_policy,
    entity_language_kwargs,
    hides_sources,
    kb_decide,
    kb_number_str,
    kb_runtime_context,
    read_v1_json,
    resolve_kb,
    sees_hidden,
)
from .errors import V1Error, not_found_error, v1_endpoint
from .pagination import parse_limit
from .schemas import (
    EntityDetailResponse,
    EntityNeighborsResponse,
    EntityRelationSummary,
    EntitySourceDocument,
    GraphEntityItem,
    GraphPathEntityItem,
    GraphPathRelationItem,
    GraphPathResponse,
    GraphRelationItem,
    GraphResponse,
    GraphStats,
    NeighborEntityItem,
    NeighborViaItem,
    SearchHitItem,
    SearchRequest,
    SearchResponse,
)

MAX_NEIGHBORS_LIMIT = 200
MAX_PATH_HOPS = 8


def _direction(raw: Any) -> Literal["in", "out"]:
    return "out" if str(raw) == "out" else "in"


def _parse_exclude_ids(request: Request) -> list[str]:
    values: list[str] = []
    for raw in request.query_params.getlist("exclude_ids"):
        for part in raw.split(","):
            cleaned = part.strip()
            if cleaned:
                values.append(cleaned)
    return values


def _visible_entity_ids(conn: Any, kb_id: int, include_hidden: bool) -> set[str] | None:
    if include_hidden:
        return None
    hidden_docs = dbm.hidden_document_ids(conn, kb_id=kb_id)
    if not hidden_docs:
        return None
    visible: set[str] = set()
    for ent in dbm.all_entities(conn, kb_id=kb_id):
        if not ent.sources or any(src not in hidden_docs for src in ent.sources):
            visible.add(ent.id)
    return visible


def build_graph_handlers(ctx: V1Context) -> dict[str, Any]:
    """Build Starlette route handlers for v1 graph, entity, and search endpoints."""

    @v1_endpoint
    async def search_kb(request: Request) -> JSONResponse:
        principal = enforce_v1_auth_policy(request)
        kb = resolve_kb(ctx, request, capability="read")
        _, _, kb_svc = kb_runtime_context(ctx, request, kb)
        body = await read_v1_json(request, SearchRequest)
        query = body.query.strip()
        if not query:
            raise V1Error(400, "invalid_query", "query is required")

        scope = request_auth_scope(request) or principal.scope
        is_cross_account = (
            principal.account_id is not None
            and kb.account_id is not None
            and principal.account_id != kb.account_id
        ) or (kb.is_shared and kb_decide(ctx, request, kb, "review") != "allow")
        include_hidden = (scope != "anonymous") and not is_cross_account

        requested_mode = body.mode or ""
        if scope == "anonymous" or is_cross_account:
            mode = "fts"
            limit = max(1, min(MAX_ANONYMOUS_SEARCH_RESULTS, body.limit))
            summarize = False
            runner = ctx.run_anonymous_search
        elif scope in ("owner", "write", "readonly"):
            if requested_mode == "fts":
                mode = "fts"
                limit = max(1, min(MAX_SEARCH_RESULTS, body.limit))
                summarize = False
                runner = ctx.run_anonymous_search
            else:
                mode = "hybrid"
                limit = max(1, min(MAX_SEARCH_RESULTS, body.limit))
                summarize = (scope == "owner") and bool(body.summarize)
                runner = ctx.run_expensive
        else:
            raise V1Error(401, "authentication_required", "Authentication required")

        result = await runner(
            kb_svc.search,
            query,
            include_hidden=include_hidden,
            limit=limit,
            summarize=summarize,
            mode=mode,
        )
        hits = [
            SearchHitItem(
                id=str(hit.entity.id),
                type=str(hit.entity.type),
                name=display_label(hit.entity.name, hit.entity.aliases, principal.entity_language),
                via=[str(v) for v in (hit.via or [])],
                score=float(hit.score),
            )
            for hit in result.hits
        ]
        resp = SearchResponse(
            query=str(result.query),
            mode=mode,  # type: ignore[arg-type]
            answer=result.answer if summarize else None,
            kb_number=kb_number_str(kb),
            hits=hits,
        )
        return JSONResponse(resp.model_dump())

    @v1_endpoint
    async def get_graph(request: Request) -> JSONResponse:
        kb = resolve_kb(ctx, request, capability="read")
        _, kb_settings, _ = kb_runtime_context(ctx, request, kb)
        include_hidden = sees_hidden(ctx, request, kb)
        lang_kwargs = entity_language_kwargs(request)

        def _load() -> GraphResponse:
            conn = dbm.connect_existing(kb_settings.db_file, readonly=True)
            try:
                raw = graph_json(conn, include_hidden=include_hidden, kb_id=kb.id, **lang_kwargs)
            finally:
                conn.close()
            entities = [
                GraphEntityItem(
                    id=str(n["id"]),
                    name=str(n["label"]),
                    type=str(n["group"]),
                    sources=[str(s) for s in (n.get("sources") or [])],
                    observation_preview=str(n.get("obs") or ""),
                    created_at=float(n["created_at"]) if n.get("created_at") is not None else None,
                    degree=int(n.get("degree") or 0),
                )
                for n in (raw.get("nodes") or [])
            ]
            relations = [
                GraphRelationItem(
                    id=str(e["id"]),
                    source_id=str(e["from"]),
                    target_id=str(e["to"]),
                    type=str(e["label"]),
                    provisional=bool(e.get("dashes", False)),
                )
                for e in (raw.get("edges") or [])
            ]
            raw_stats = raw.get("stats") or {}
            stats = GraphStats(
                entities=int(raw_stats.get("entities", len(entities))),
                relations=int(raw_stats.get("relations", len(relations))),
                max_degree=int(raw_stats.get("max_degree", 0)),
            )
            return GraphResponse(
                kb_number=kb_number_str(kb),
                entities=entities,
                relations=relations,
                stats=stats,
            )

        payload = await asyncio.to_thread(_load)
        return JSONResponse(payload.model_dump())

    @v1_endpoint
    async def get_entity(request: Request) -> JSONResponse:
        kb = resolve_kb(ctx, request, capability="read")
        _, kb_settings, _ = kb_runtime_context(ctx, request, kb)
        entity_id = str(request.path_params.get("entity_id") or "").strip()
        if not entity_id:
            raise V1Error(400, "invalid_entity_id", "entity_id is required")
        docs_mode = request.query_params.get("docs", "full")
        if docs_mode not in ("full", "brief"):
            raise V1Error(400, "invalid_docs", "docs must be 'full' or 'brief'")
        include_hidden = sees_hidden(ctx, request, kb)
        mask_sources = hides_sources(ctx, request, kb)
        lang_kwargs = entity_language_kwargs(request)

        def _load() -> EntityDetailResponse | None:
            conn = dbm.connect_existing(kb_settings.db_file, readonly=True)
            try:
                rep = node_detail(
                    conn,
                    entity_id,
                    include_hidden=include_hidden,
                    kb_id=kb.id,
                    docs_brief=(docs_mode == "brief"),
                    **lang_kwargs,
                )
                if rep is None:
                    return None
                if mask_sources and isinstance(rep.get("documents"), list):
                    rep["documents"] = apply_source_rule(conn, kb, rep["documents"])
            finally:
                conn.close()

            permalink: str | None = None
            link_access_val: bool | None = None
            try:
                wconn = dbm.connect_existing(kb_settings.db_file)
                try:
                    token = permalinks.ensure_entity(wconn, entity_id)
                    wconn.commit()
                    if token is not None:
                        permalink = permalinks.entity_path(token)
                        link_access_val = bool(permalinks.link_access(wconn, "entity", entity_id))
                finally:
                    wconn.close()
            except Exception:  # noqa: BLE001
                pass

            relations = [
                EntityRelationSummary(
                    id=str(n["id"]),
                    name=str(n["name"]),
                    type=str(n["type"]),
                    relation=str(n["rel"]),
                    direction=_direction(n["dir"]),
                    provisional=bool(n.get("provisional", False)),
                )
                for n in (rep.get("neighbors") or [])
            ]
            documents = [
                EntitySourceDocument(
                    id=str(d["id"]),
                    title=str(d.get("title") or "(제목 없음)"),
                    url=d.get("url"),
                    summary=str(d.get("summary") or ""),
                    fetched_at=float(d["fetched_at"]) if d.get("fetched_at") is not None else None,
                    has_detail=bool(d["has_detail"]) if "has_detail" in d else None,
                    source_type=d.get("source_type"),
                    has_raw_file=bool(d["has_raw_file"]) if "has_raw_file" in d else None,
                    detail=d.get("detail"),
                    detail_format=d.get("detail_format"),
                    detail_html=d.get("detail_html"),
                )
                for d in (rep.get("documents") or [])
            ]
            return EntityDetailResponse(
                id=str(rep["id"]),
                kb_number=kb_number_str(kb),
                name=str(rep["name"]),
                type=str(rep["type"]),
                aliases=[str(a) for a in (rep.get("aliases") or [])],
                observations=[str(o) for o in (rep.get("observations") or [])],
                provisional=bool(rep.get("provisional", False)),
                permalink=permalink,
                link_access=link_access_val,
                relations=relations,
                documents=documents,
            )

        payload = await asyncio.to_thread(_load)
        if payload is None:
            raise not_found_error("Entity not found")
        return JSONResponse(payload.model_dump(exclude_none=True))

    @v1_endpoint
    async def get_entity_neighbors(request: Request) -> JSONResponse:
        principal = enforce_v1_auth_policy(request)
        kb = resolve_kb(ctx, request, capability="read")
        _, kb_settings, _ = kb_runtime_context(ctx, request, kb)
        entity_id = str(request.path_params.get("entity_id") or "").strip()
        if not entity_id:
            raise V1Error(400, "invalid_entity_id", "entity_id is required")
        try:
            limit = parse_limit(request.query_params.get("limit"), default=50, minimum=1, maximum=MAX_NEIGHBORS_LIMIT)
        except ValueError as exc:
            raise V1Error(400, "invalid_limit", f"limit must be an integer between 1 and {MAX_NEIGHBORS_LIMIT}") from exc
        exclude_ids = _parse_exclude_ids(request)
        include_hidden = sees_hidden(ctx, request, kb)

        def _load() -> EntityNeighborsResponse | None:
            conn = dbm.connect_existing(kb_settings.db_file, readonly=True)
            try:
                ent = dbm.get_entity(conn, entity_id, kb_id=kb.id)
                if ent is None:
                    return None
                visible = _visible_entity_ids(conn, kb.id, include_hidden)
                if visible is not None and entity_id not in visible:
                    return None
                hidden_exclude = list(exclude_ids)
                if visible is not None:
                    all_ent_ids = {e.id for e in dbm.all_entities(conn, kb_id=kb.id)}
                    hidden_exclude.extend(eid for eid in all_ent_ids if eid not in visible)
                raw = graph_neighbors_impl(
                    conn,
                    entity_id,
                    exclude_ids=hidden_exclude,
                    limit=limit,
                    kb_id=kb.id,
                )
                items: list[NeighborEntityItem] = []
                for item in raw.get("neighbors") or []:
                    other = dbm.get_entity(conn, str(item["id"]), kb_id=kb.id)
                    label = display_label(other.name, other.aliases, principal.entity_language) if other else str(item["name"])
                    items.append(
                        NeighborEntityItem(
                            id=str(item["id"]),
                            name=label,
                            type=str(item["type"]),
                            degree=int(item["degree"]),
                            via=[
                                NeighborViaItem(
                                    from_id=str(v["from"]),
                                    relation=str(v["rel"]),
                                    direction=_direction(v["dir"]),
                                )
                                for v in (item.get("via") or [])
                            ],
                        )
                    )
                return EntityNeighborsResponse(
                    entity_id=entity_id,
                    kb_number=kb_number_str(kb),
                    items=items,
                    truncated=bool(raw.get("truncated", False)),
                    omitted=int(raw.get("omitted", 0)),
                )
            finally:
                conn.close()

        payload = await asyncio.to_thread(_load)
        if payload is None:
            raise not_found_error("Entity not found")
        return JSONResponse(payload.model_dump())

    @v1_endpoint
    async def get_graph_path(request: Request) -> JSONResponse:
        principal = enforce_v1_auth_policy(request)
        kb = resolve_kb(ctx, request, capability="read")
        _, kb_settings, _ = kb_runtime_context(ctx, request, kb)
        from_entity_id = (request.query_params.get("from_entity_id") or "").strip()
        to_entity_id = (request.query_params.get("to_entity_id") or "").strip()
        if not from_entity_id or not to_entity_id:
            raise V1Error(400, "invalid_request", "from_entity_id and to_entity_id are required")
        try:
            max_hops = parse_limit(request.query_params.get("max_hops"), default=4, minimum=1, maximum=MAX_PATH_HOPS)
        except ValueError as exc:
            raise V1Error(400, "invalid_max_hops", f"max_hops must be an integer between 1 and {MAX_PATH_HOPS}") from exc
        include_hidden = sees_hidden(ctx, request, kb)

        def _load() -> GraphPathResponse:
            conn = dbm.connect_existing(kb_settings.db_file, readonly=True)
            try:
                visible = _visible_entity_ids(conn, kb.id, include_hidden)
                if visible is not None and (from_entity_id not in visible or to_entity_id not in visible):
                    return GraphPathResponse(kb_number=kb_number_str(kb), found=False, reason="entity not found")
                raw = graph_path_impl(conn, from_entity_id, to_entity_id, max_hops=max_hops, kb_id=kb.id)
                path_items: list[GraphPathEntityItem] = []
                for step in raw.get("path") or []:
                    step_id = str(step["id"])
                    if visible is not None and step_id not in visible:
                        return GraphPathResponse(kb_number=kb_number_str(kb), found=False, hops_tried=max_hops)
                    ent = dbm.get_entity(conn, step_id, kb_id=kb.id)
                    label = display_label(ent.name, ent.aliases, principal.entity_language) if ent else str(step["name"])
                    path_items.append(GraphPathEntityItem(id=step_id, name=label, type=str(step["type"])))
                rel_items = [
                    GraphPathRelationItem(type=str(r["type"]), direction=_direction(r["dir"]))
                    for r in (raw.get("relations") or [])
                ]
                return GraphPathResponse(
                    kb_number=kb_number_str(kb),
                    found=bool(raw.get("found", False)),
                    path=path_items,
                    relations=rel_items,
                    reason=raw.get("reason"),
                    hops_tried=raw.get("hops_tried"),
                )
            finally:
                conn.close()

        payload = await asyncio.to_thread(_load)
        return JSONResponse(payload.model_dump(exclude_none=True))

    return {
        "search_kb": search_kb,
        "get_graph": get_graph,
        "get_entity": get_entity,
        "get_entity_neighbors": get_entity_neighbors,
        "get_graph_path": get_graph_path,
    }
