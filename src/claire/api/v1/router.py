"""Route registry, `V1_ROUTE_POLICY`, and Starlette route builder for `/api/v1/*`."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel
from starlette.routing import Route

from ..security import AccessLevel, RouteKey, RouteRule, _system
from ._context import V1Context
from .documents import build_document_handlers
from .graph import build_graph_handlers
from .kbs import build_kb_handlers
from .schemas import (
    CreateKnowledgeBaseRequest,
    DeleteDocumentResponse,
    DeleteKnowledgeBaseResponse,
    DocumentDetailResponse,
    DocumentListResponse,
    EntityDetailResponse,
    EntityNeighborsResponse,
    GraphPathResponse,
    GraphResponse,
    HealthResponse,
    IngestRequest,
    IngestResponse,
    KnowledgeBaseDetailResponse,
    KnowledgeBaseListResponse,
    MeResponse,
    SearchRequest,
    SearchResponse,
    ThemeListResponse,
    UpdateDocumentRequest,
    UpdateDocumentResponse,
    UpdateKnowledgeBaseRequest,
)


@dataclass(frozen=True, slots=True)
class V1ParamSpec:
    """OpenAPI parameter metadata for a v1 route."""

    name: str
    location: str
    required: bool = False
    description: str = ""
    schema_type: str = "string"
    enum: tuple[str, ...] = ()
    default: Any = None


@dataclass(frozen=True, slots=True)
class V1RouteSpec:
    """Declarative metadata linking a v1 Starlette route to OpenAPI and ROUTE_POLICY."""

    method: str
    path: str
    handler_key: str
    operation_id: str
    tag: str
    summary: str
    description: str
    access: AccessLevel
    capability: str
    step_up: bool = False
    request_model: type[BaseModel] | None = None
    response_model: type[BaseModel] | None = None
    response_status: int = 200
    response_media_type: str = "application/json"
    params: tuple[V1ParamSpec, ...] = ()
    extra_statuses: tuple[int, ...] = ()


_KB_PARAM = V1ParamSpec(
    name="kb",
    location="path",
    required=True,
    description="Knowledge base selector: 'personal' for the authenticated caller's personal KB, or an 18-digit external KB number.",
)
_DOC_ID_PARAM = V1ParamSpec(
    name="doc_id",
    location="path",
    required=True,
    description="Document identifier (e.g. doc_0123456789ab).",
)
_ENTITY_ID_PARAM = V1ParamSpec(
    name="entity_id",
    location="path",
    required=True,
    description="Entity identifier (e.g. ent_0123456789ab).",
)

V1_ROUTE_SPECS: tuple[V1RouteSpec, ...] = (
    V1RouteSpec(
        method="GET",
        path="/api/v1/health",
        handler_key="health",
        operation_id="v1CheckHealth",
        tag="System & Identity",
        summary="Service liveness and database readiness",
        description="Return service liveness, database readiness status, and API version.",
        access="public",
        capability="none",
        response_model=HealthResponse,
        extra_statuses=(503,),
    ),
    V1RouteSpec(
        method="GET",
        path="/api/v1/me",
        handler_key="me",
        operation_id="v1GetMe",
        tag="System & Identity",
        summary="Current authenticated scope and role",
        description="Return the credential scope, server mode, caller relation, and admin flag without exposing usernames.",
        access="read",
        capability="none",
        response_model=MeResponse,
        extra_statuses=(401,),
    ),
    V1RouteSpec(
        method="GET",
        path="/api/v1/kbs",
        handler_key="list_kbs",
        operation_id="v1ListKbs",
        tag="Knowledge Bases",
        summary="List knowledge bases",
        description="List visible shared knowledge bases with keyset pagination, plus the caller's personal KB when applicable.",
        access="read",
        capability="none",
        response_model=KnowledgeBaseListResponse,
        params=(
            V1ParamSpec("knowledge_type", "query", False, "Filter by knowledge type.", "string", ("real", "fictional")),
            V1ParamSpec("joined", "query", False, "Filter to joined shared knowledge bases.", "string", ("true", "false")),
            V1ParamSpec("cursor", "query", False, "Opaque keyset pagination cursor."),
            V1ParamSpec("limit", "query", False, "Maximum items per page (1-100).", "integer", default=50),
        ),
        extra_statuses=(400,),
    ),
    V1RouteSpec(
        method="POST",
        path="/api/v1/kbs",
        handler_key="create_kb",
        operation_id="v1CreateKb",
        tag="Knowledge Bases",
        summary="Create a shared knowledge base",
        description="Create a new shared knowledge base owned by the server community with the caller as founding moderator.",
        access="owner",
        capability="none",
        request_model=CreateKnowledgeBaseRequest,
        response_model=KnowledgeBaseDetailResponse,
        response_status=201,
        extra_statuses=(400, 401, 403, 422),
    ),
    V1RouteSpec(
        method="GET",
        path="/api/v1/themes",
        handler_key="list_themes",
        operation_id="v1ListThemes",
        tag="Knowledge Bases",
        summary="List themes",
        description="List themes with keyset pagination and optional knowledge_type filter.",
        access="read",
        capability="none",
        response_model=ThemeListResponse,
        params=(
            V1ParamSpec("knowledge_type", "query", False, "Filter by knowledge type.", "string", ("real", "fictional")),
            V1ParamSpec("cursor", "query", False, "Opaque keyset pagination cursor."),
            V1ParamSpec("limit", "query", False, "Maximum items per page (1-100).", "integer", default=50),
        ),
        extra_statuses=(400,),
    ),
    V1RouteSpec(
        method="POST",
        path="/api/v1/kbs/personal/ingest",
        handler_key="ingest_personal",
        operation_id="v1IngestPersonal",
        tag="Documents & Ingest",
        summary="Ingest a source into the caller's personal KB",
        description="Ingest a URL or text into the caller's personal KB using explicit extraction_mode ('provider' or 'client').",
        access="write",
        capability="write",
        request_model=IngestRequest,
        response_model=IngestResponse,
        extra_statuses=(400, 401, 403, 422, 429),
    ),
    V1RouteSpec(
        method="POST",
        path="/api/v1/kbs/personal/ingest/stream",
        handler_key="ingest_personal_stream",
        operation_id="v1IngestPersonalStream",
        tag="Documents & Ingest",
        summary="Ingest a source into the personal KB with NDJSON progress stream",
        description="Stream ingestion progress events and the final report as newline-delimited JSON.",
        access="write",
        capability="write",
        request_model=IngestRequest,
        response_media_type="application/x-ndjson",
        extra_statuses=(400, 401, 403, 422, 429),
    ),
    V1RouteSpec(
        method="GET",
        path="/api/v1/kbs/{kb}",
        handler_key="get_kb",
        operation_id="v1GetKb",
        tag="Knowledge Bases",
        summary="Get knowledge base metadata and counts",
        description="Return knowledge base metadata, document/entity/relation counts, and available ingest modes for writers.",
        access="read",
        capability="read",
        response_model=KnowledgeBaseDetailResponse,
        params=(_KB_PARAM,),
        extra_statuses=(400, 404),
    ),
    V1RouteSpec(
        method="PATCH",
        path="/api/v1/kbs/{kb}",
        handler_key="update_kb",
        operation_id="v1UpdateKb",
        tag="Knowledge Bases",
        summary="Update knowledge base settings",
        description="Partially update settings on the selected knowledge base.",
        access="owner",
        capability="settings",
        request_model=UpdateKnowledgeBaseRequest,
        response_model=KnowledgeBaseDetailResponse,
        params=(_KB_PARAM,),
        extra_statuses=(400, 403, 404, 422),
    ),
    V1RouteSpec(
        method="DELETE",
        path="/api/v1/kbs/{kb}",
        handler_key="delete_kb",
        operation_id="v1DeleteKb",
        tag="Knowledge Bases",
        summary="Delete a shared knowledge base (dry-run by default)",
        description="Preview or execute deletion of a shared knowledge base. Requires step-up session auth and ?apply=true to commit.",
        access="admin",
        capability="delete",
        step_up=True,
        response_model=DeleteKnowledgeBaseResponse,
        params=(
            _KB_PARAM,
            V1ParamSpec("apply", "query", False, "Set 'true' to execute deletion; otherwise returns dry-run plan.", "string", ("true", "false")),
        ),
        extra_statuses=(400, 401, 403, 404),
    ),
    V1RouteSpec(
        method="GET",
        path="/api/v1/kbs/{kb}/documents",
        handler_key="list_documents",
        operation_id="v1ListDocuments",
        tag="Documents & Ingest",
        summary="List documents in a knowledge base",
        description="List documents using opaque keyset cursor pagination and filters.",
        access="read",
        capability="read",
        response_model=DocumentListResponse,
        params=(
            _KB_PARAM,
            V1ParamSpec("cursor", "query", False, "Opaque keyset pagination cursor."),
            V1ParamSpec("limit", "query", False, "Maximum items per page (1-300).", "integer", default=50),
            V1ParamSpec("q", "query", False, "Filter by title or summary substring (max 200 chars)."),
            V1ParamSpec("pinned", "query", False, "Filter by pinned status.", "boolean"),
            V1ParamSpec("hidden", "query", False, "Filter by hidden status.", "boolean"),
            V1ParamSpec("detail", "query", False, "Filter by detail completeness.", "string", ("missing", "mismatched")),
            V1ParamSpec("id", "query", False, "Filter to specific document IDs (repeatable, max 100)."),
        ),
        extra_statuses=(400, 404),
    ),
    V1RouteSpec(
        method="GET",
        path="/api/v1/kbs/{kb}/documents/{doc_id}",
        handler_key="get_document",
        operation_id="v1GetDocument",
        tag="Documents & Ingest",
        summary="Get document detail, entities, and permalink",
        description="Return document metadata, summary, AsciiDoc detail, canonical entities, and permalink.",
        access="read",
        capability="read",
        response_model=DocumentDetailResponse,
        params=(_KB_PARAM, _DOC_ID_PARAM),
        extra_statuses=(400, 404),
    ),
    V1RouteSpec(
        method="GET",
        path="/api/v1/kbs/{kb}/documents/{doc_id}/raw",
        handler_key="get_document_raw",
        operation_id="v1GetDocumentRaw",
        tag="Documents & Ingest",
        summary="Download or view raw document source file",
        description="Serve the archived original uploaded file for a document with strict sandbox headers.",
        access="read",
        capability="read",
        response_media_type="application/octet-stream",
        params=(_KB_PARAM, _DOC_ID_PARAM),
        extra_statuses=(400, 404, 409),
    ),
    V1RouteSpec(
        method="GET",
        path="/api/v1/kbs/{kb}/documents/{doc_id}/export",
        handler_key="export_document",
        operation_id="v1ExportDocument",
        tag="Documents & Ingest",
        summary="Export document as Markdown",
        description="Export a document as a Markdown file with YAML front matter.",
        access="read",
        capability="read",
        response_media_type="text/markdown",
        params=(
            _KB_PARAM,
            _DOC_ID_PARAM,
            V1ParamSpec("format", "query", False, "Export format.", "string", ("markdown", "md"), default="markdown"),
        ),
        extra_statuses=(400, 404),
    ),
    V1RouteSpec(
        method="PATCH",
        path="/api/v1/kbs/{kb}/documents/{doc_id}",
        handler_key="update_document",
        operation_id="v1UpdateDocument",
        tag="Documents & Ingest",
        summary="Update document metadata flags",
        description="Partially update title, pinned, hidden, or seen flags on a document.",
        access="owner",
        capability="curate",
        request_model=UpdateDocumentRequest,
        response_model=UpdateDocumentResponse,
        params=(_KB_PARAM, _DOC_ID_PARAM),
        extra_statuses=(400, 403, 404, 422),
    ),
    V1RouteSpec(
        method="DELETE",
        path="/api/v1/kbs/{kb}/documents/{doc_id}",
        handler_key="delete_document",
        operation_id="v1DeleteDocument",
        tag="Documents & Ingest",
        summary="Delete a document from a personal KB",
        description="Cascade-delete a document from a personal KB (shared KB documents must be removed via contribution removal).",
        access="owner",
        capability="remove",
        response_model=DeleteDocumentResponse,
        params=(
            _KB_PARAM,
            _DOC_ID_PARAM,
            V1ParamSpec("dry_run", "query", False, "Preview cascade impact without deleting.", "boolean", default=False),
            V1ParamSpec("tombstone", "query", False, "Record tombstone to prevent re-ingestion.", "boolean", default=True),
            V1ParamSpec("reason", "query", False, "Audit reason for deletion."),
        ),
        extra_statuses=(400, 403, 404),
    ),
    V1RouteSpec(
        method="POST",
        path="/api/v1/kbs/{kb}/search",
        handler_key="search_kb",
        operation_id="v1SearchKb",
        tag="Graph & Search",
        summary="Search entities in a knowledge base",
        description="Run hybrid or FTS search. Anonymous and cross-account callers are restricted to FTS-only with at most 20 hits and no summary.",
        access="read",
        capability="read",
        request_model=SearchRequest,
        response_model=SearchResponse,
        params=(_KB_PARAM,),
        extra_statuses=(400, 404, 422, 429),
    ),
    V1RouteSpec(
        method="GET",
        path="/api/v1/kbs/{kb}/graph",
        handler_key="get_graph",
        operation_id="v1GetGraph",
        tag="Graph & Search",
        summary="Get knowledge graph entities and relations",
        description="Return canonical entities, relations, and summary statistics for the knowledge base graph.",
        access="read",
        capability="read",
        response_model=GraphResponse,
        params=(_KB_PARAM,),
        extra_statuses=(400, 404),
    ),
    V1RouteSpec(
        method="GET",
        path="/api/v1/kbs/{kb}/entities/{entity_id}",
        handler_key="get_entity",
        operation_id="v1GetEntity",
        tag="Graph & Search",
        summary="Get entity detail, relations, and source documents",
        description="Return entity observations, aliases, direct relations, source documents, and entity permalink.",
        access="read",
        capability="read",
        response_model=EntityDetailResponse,
        params=(
            _KB_PARAM,
            _ENTITY_ID_PARAM,
            V1ParamSpec("docs", "query", False, "Source document detail level.", "string", ("full", "brief"), default="full"),
        ),
        extra_statuses=(400, 404),
    ),
    V1RouteSpec(
        method="GET",
        path="/api/v1/kbs/{kb}/entities/{entity_id}/neighbors",
        handler_key="get_entity_neighbors",
        operation_id="v1GetEntityNeighbors",
        tag="Graph & Search",
        summary="Get 1-hop entity neighborhood",
        description="Return deduplicated 1-hop neighbor entities ordered by degree descending.",
        access="read",
        capability="read",
        response_model=EntityNeighborsResponse,
        params=(
            _KB_PARAM,
            _ENTITY_ID_PARAM,
            V1ParamSpec("exclude_ids", "query", False, "Comma-separated or repeated entity IDs to exclude."),
            V1ParamSpec("limit", "query", False, "Maximum neighbors to return (1-200).", "integer", default=50),
        ),
        extra_statuses=(400, 404),
    ),
    V1RouteSpec(
        method="GET",
        path="/api/v1/kbs/{kb}/graph/path",
        handler_key="get_graph_path",
        operation_id="v1GetGraphPath",
        tag="Graph & Search",
        summary="Find shortest path between two entities",
        description="Find the shortest undirected BFS path between two entities within max_hops.",
        access="read",
        capability="read",
        response_model=GraphPathResponse,
        params=(
            _KB_PARAM,
            V1ParamSpec("from_entity_id", "query", True, "Starting entity ID."),
            V1ParamSpec("to_entity_id", "query", True, "Target entity ID."),
            V1ParamSpec("max_hops", "query", False, "Maximum BFS hops (1-8).", "integer", default=4),
        ),
        extra_statuses=(400, 404),
    ),
)


def _build_route_policy() -> dict[RouteKey, RouteRule]:
    policy_map: dict[RouteKey, RouteRule] = {}
    for spec in V1_ROUTE_SPECS:
        rule = _system(spec.access, spec.capability, step_up=spec.step_up)
        policy_map[(spec.method, spec.path)] = rule
        if spec.method == "GET":
            policy_map[("HEAD", spec.path)] = rule
    return policy_map


V1_ROUTE_POLICY: dict[RouteKey, RouteRule] = _build_route_policy()


def build_v1_routes(
    settings: Any,
    *,
    service: Any | None = None,
    theme_manager: Any | None = None,
    service_pool: Any | None = None,
    namespace_directory: Any | None = None,
    context: V1Context | None = None,
) -> list[Route]:
    """Build all `/api/v1/*` Starlette routes."""
    ctx = context or V1Context.build(
        settings,
        service=service,
        theme_manager=theme_manager,
        service_pool=service_pool,
        namespace_directory=namespace_directory,
    )
    handlers: dict[str, Any] = {}
    handlers.update(build_kb_handlers(ctx))
    handlers.update(build_document_handlers(ctx))
    handlers.update(build_graph_handlers(ctx))

    routes: list[Route] = []
    for spec in V1_ROUTE_SPECS:
        handler = handlers[spec.handler_key]
        routes.append(Route(spec.path, handler, methods=[spec.method]))
    return routes
