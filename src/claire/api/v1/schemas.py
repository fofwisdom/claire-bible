"""Sealed Pydantic v2 schemas for `/api/v1/*` (AR2, AR3, PRINCIPLES C4)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class SealedModel(BaseModel):
    """Base response model with strict typing and no unknown fields."""

    model_config = ConfigDict(extra="forbid", strict=True)


class InputModel(BaseModel):
    """Base JSON request model that forbids unknown fields."""

    model_config = ConfigDict(extra="forbid", strict=True)


class ErrorDetail(SealedModel):
    """Structured error object inside the v1 error envelope."""

    code: str
    message: str
    request_id: str
    details: dict[str, Any] | list[Any] | None = None


class ErrorEnvelope(SealedModel):
    """Unified v1 error response envelope (AR4)."""

    error: ErrorDetail


class HealthResponse(SealedModel):
    """Response for `GET /api/v1/health`."""

    ok: bool
    api_version: str = "v1"
    database: Literal["ok", "unavailable"]


class ServerModeInfo(SealedModel):
    """Server operating mode descriptor."""

    mode: Literal["single", "network"]


class MeResponse(SealedModel):
    """Response for `GET /api/v1/me`."""

    scope: Literal["owner", "write", "readonly", "anonymous"]
    server: ServerModeInfo
    relation: Literal["owner", "delegate", "visitor"]
    is_admin: bool


class KnowledgeBaseStats(SealedModel):
    """Document, entity, and relation counts for a knowledge base."""

    documents: int
    entities: int
    relations: int


class LegacyHostEntry(SealedModel):
    """Legacy host redirect entry visible to KB settings managers."""

    host: str
    activated_at: float
    sunset_at: float | None = None


class KnowledgeBaseSummary(SealedModel):
    """Public or settings-enriched representation of a knowledge base."""

    number: str | None
    label: str
    description: str
    icon: str
    is_personal: bool
    is_shared: bool
    is_public: bool
    knowledge_type: Literal["real", "fictional"] | None = None
    join_policy: str
    state: str
    presentation_enabled: bool
    seed_entities: list[str] = Field(default_factory=list)
    url: str | None = None
    role: str | None = None
    membership_state: str | None = None
    stats: KnowledgeBaseStats
    created_at: float
    updated_at: float
    archived_at: float | None = None
    embed_provider: str = ""
    embed_model: str = ""
    sorcerer: str | None = None
    ga_measurement_id: str | None = None
    reembed_required: bool | None = None
    active_moderators: int | None = None
    legacy_hosts: list[LegacyHostEntry] | None = None


class KnowledgeBaseListResponse(SealedModel):
    """Paginated response for `GET /api/v1/kbs`."""

    items: list[KnowledgeBaseSummary]
    next_cursor: str | None
    total: int
    personal_kb: KnowledgeBaseSummary | None = None


class CreateKnowledgeBaseRequest(InputModel):
    """Request body for `POST /api/v1/kbs`."""

    label: str = Field(min_length=1, max_length=200)
    knowledge_type: Literal["real", "fictional"]
    description: str = ""
    icon: str = "📁"
    is_public: bool = True
    ga_measurement_id: str = ""
    sorcerer: str = ""
    presentation_enabled: bool = False
    seed_entities: list[str] | None = None


class UpdateKnowledgeBaseRequest(InputModel):
    """Request body for `PATCH /api/v1/kbs/{kb}`."""

    label: str | None = None
    description: str | None = None
    icon: str | None = None
    is_public: bool | None = None
    ga_measurement_id: str | None = None
    sorcerer: str | None = None
    presentation_enabled: bool | None = None
    join_policy: str | None = None
    knowledge_type: Literal["real", "fictional"] | None = None
    state: str | None = None
    seed_entities: list[str] | None = None


class KnowledgeBaseDetailResponse(SealedModel):
    """Response for `POST /api/v1/kbs`, `GET /api/v1/kbs/{kb}`, and `PATCH /api/v1/kbs/{kb}`."""

    knowledge_base: KnowledgeBaseSummary
    ingest_effort: dict[str, Any] | None = None
    extraction_modes: list[Literal["provider", "client"]] | None = None
    ingesting: bool | None = None
    active_jobs: list[dict[str, Any]] | None = None


class DeleteKnowledgeBasePlan(SealedModel):
    """Count summary of objects deleted with a shared knowledge base."""

    documents: int
    entities: int
    relations: int
    members: int
    contributions: int


class DeleteKnowledgeBaseResponse(SealedModel):
    """Response for `DELETE /api/v1/kbs/{kb}`."""

    applied: bool
    knowledge_base: KnowledgeBaseSummary
    would_delete: DeleteKnowledgeBasePlan


class ThemeSummary(SealedModel):
    """Theme entry in `GET /api/v1/themes`."""

    number: str
    url: str
    label: str
    description: str
    knowledge_type: Literal["real", "fictional"]
    state: str
    knowledge_bases: int
    remote_knowledge_bases: int


class ThemeListResponse(SealedModel):
    """Paginated response for `GET /api/v1/themes`."""

    items: list[ThemeSummary]
    next_cursor: str | None
    total: int


class DocumentListItem(SealedModel):
    """Summary entry for one document in `GET /api/v1/kbs/{kb}/documents`."""

    id: str
    title: str
    url: str | None = None
    source_type: str | None = None
    fetched_at: float | None = None
    seen: bool
    watch: bool
    pinned: bool
    hidden: bool
    summary: str
    has_presentation: bool
    has_detail: bool
    detail_format: str


class DocumentListResponse(SealedModel):
    """Paginated response for `GET /api/v1/kbs/{kb}/documents`."""

    items: list[DocumentListItem]
    next_cursor: str | None
    total: int


class DocumentEntityItem(SealedModel):
    """Canonical entity entry attached to a document detail response."""

    id: str
    name: str
    type: str
    observations: list[str]
    aliases: list[str]


class DocumentDetailResponse(SealedModel):
    """Response for `GET /api/v1/kbs/{kb}/documents/{doc_id}`."""

    id: str
    kb_number: str | None
    title: str
    url: str | None = None
    permalink: str | None = None
    source_type: str | None = None
    summary: str
    detail: str
    detail_format: str
    detail_html: str
    hidden: bool
    pinned: bool
    seen: bool
    watch: bool
    author: str | None = None
    published_at: str | None = None
    fetched_at: float | None = None
    extra_sources: list[dict[str, Any]] = Field(default_factory=list)
    entities: list[DocumentEntityItem] = Field(default_factory=list)
    has_presentation: bool
    presentation_status: str | None = None
    has_raw_file: bool
    raw_file_name: str | None = None
    extraction_provider: str | None = None
    extraction_model: str | None = None
    raw_truncated: bool = False
    meta: dict[str, Any] = Field(default_factory=dict)


class UpdateDocumentRequest(InputModel):
    """Request body for `PATCH /api/v1/kbs/{kb}/documents/{doc_id}`."""

    title: str | None = None
    pinned: bool | None = None
    hidden: bool | None = None
    seen: bool | None = None


class UpdateDocumentResponse(SealedModel):
    """Response for `PATCH /api/v1/kbs/{kb}/documents/{doc_id}`."""

    id: str
    kb_number: str | None
    title: str | None = None
    pinned: bool
    hidden: bool
    seen: bool


class DeleteDocumentResponse(SealedModel):
    """Response for `DELETE /api/v1/kbs/{kb}/documents/{doc_id}`."""

    id: str
    kb_number: str | None
    deleted: bool
    dry_run: bool
    tombstone: bool
    report: dict[str, Any]


class IngestRequest(InputModel):
    """Request body for `POST /api/v1/kbs/personal/ingest` and `/stream`."""

    payload: str = Field(min_length=1)
    extraction_mode: Literal["provider", "client"]
    full_content: bool = False
    effort: str | None = None
    expand_max: int | None = None
    format: str | None = None


class IngestResponse(SealedModel):
    """Response for `POST /api/v1/kbs/personal/ingest`."""

    document_id: str | None = None
    title: str | None = None
    source_type: str = ""
    duplicate: bool = False
    updated: bool = False
    partial: bool = False
    summary: str = ""
    entities_created: int = 0
    entities_linked: int = 0
    relations_added: int = 0
    relations_rejected: int = 0
    cross_relations_added: int = 0
    cross_linked_relations: list[str] = Field(default_factory=list)
    proposals: int = 0
    error: str | None = None
    inbox_id: int | None = None
    new_entity_names: list[str] = Field(default_factory=list)
    linked_entity_names: list[str] = Field(default_factory=list)
    candidates: list[str] = Field(default_factory=list)
    focus: str | None = None
    full_content: bool = False
    effort: str | None = None
    has_transcript: bool | None = None
    is_stt: bool = False
    stt_error: str | None = None
    pdf_parser_requested: str | None = None
    pdf_parser_used: str | None = None
    pdf_parser_fallback: bool = False
    pdf_parser_fallback_reason: str | None = None
    pdf_encoding_flaw_detected: bool = False
    pdf_encoding_flaws: list[str] = Field(default_factory=list)
    pdf_is_scanned: bool = False
    presentation_pdfs: int = 0
    presentation_pdf_chars: int = 0
    presentation_pdf_parsers: list[str] = Field(default_factory=list)
    heatmap_matrix: dict[str, Any] | None = None
    has_decision_stream: bool = False
    composite_ingest: bool = False
    detail_rendered: bool = True
    detail_error: str | None = None
    summary_rendered: bool = True
    summary_error: str | None = None
    vlm_transcribed: bool = False
    vlm_images_count: int = 0
    vlm_model: str | None = None
    vlm_language: str | None = None
    stt_language: str | None = None
    caption_language: str | None = None
    model_lineage: dict[str, Any] = Field(default_factory=dict)
    notices: list[dict[str, Any]] = Field(default_factory=list)
    staged: bool = False
    extraction_status: str = "extracted"
    content_hash: str | None = None
    raw_chars: int = 0
    message: str | None = None
    kb_number: str | None = None


class SearchRequest(InputModel):
    """Request body for `POST /api/v1/kbs/{kb}/search`."""

    query: str = Field(min_length=1, max_length=500)
    limit: int = Field(default=8, ge=1, le=50)
    mode: Literal["hybrid", "semantic", "fts"] | None = None
    summarize: bool = True


class SearchHitItem(SealedModel):
    """Single entity match returned by `POST /api/v1/kbs/{kb}/search`."""

    id: str
    type: str
    name: str
    via: list[str]
    score: float


class SearchResponse(SealedModel):
    """Response for `POST /api/v1/kbs/{kb}/search`."""

    query: str
    mode: Literal["hybrid", "fts"]
    answer: str | None = None
    kb_number: str | None
    hits: list[SearchHitItem]


class GraphEntityItem(SealedModel):
    """Canonical entity vertex in `GET /api/v1/kbs/{kb}/graph`."""

    id: str
    name: str
    type: str
    sources: list[str]
    observation_preview: str
    created_at: float | None = None
    degree: int


class GraphRelationItem(SealedModel):
    """Canonical directed relation in `GET /api/v1/kbs/{kb}/graph`."""

    id: str
    source_id: str
    target_id: str
    type: str
    provisional: bool


class GraphStats(SealedModel):
    """Summary counts for a knowledge graph."""

    entities: int
    relations: int
    max_degree: int


class GraphResponse(SealedModel):
    """Response for `GET /api/v1/kbs/{kb}/graph`."""

    kb_number: str | None
    entities: list[GraphEntityItem]
    relations: list[GraphRelationItem]
    stats: GraphStats


class EntityRelationSummary(SealedModel):
    """Adjacent entity and relation descriptor for `GET /api/v1/kbs/{kb}/entities/{entity_id}`."""

    id: str
    name: str
    type: str
    relation: str
    direction: Literal["in", "out"]
    provisional: bool


class EntitySourceDocument(SealedModel):
    """Source document entry attached to an entity detail response."""

    id: str
    title: str
    url: str | None = None
    summary: str
    fetched_at: float | None = None
    has_detail: bool | None = None
    source_type: str | None = None
    has_raw_file: bool | None = None
    detail: str | None = None
    detail_format: str | None = None
    detail_html: str | None = None


class EntityDetailResponse(SealedModel):
    """Response for `GET /api/v1/kbs/{kb}/entities/{entity_id}`."""

    id: str
    kb_number: str | None
    name: str
    type: str
    aliases: list[str]
    observations: list[str]
    provisional: bool
    permalink: str | None = None
    link_access: bool | None = None
    relations: list[EntityRelationSummary]
    documents: list[EntitySourceDocument]


class NeighborViaItem(SealedModel):
    """Relation hop connecting a neighbor to a seed entity."""

    from_id: str
    relation: str
    direction: Literal["in", "out"]


class NeighborEntityItem(SealedModel):
    """1-hop neighbor entry in `GET /api/v1/kbs/{kb}/entities/{entity_id}/neighbors`."""

    id: str
    name: str
    type: str
    degree: int
    via: list[NeighborViaItem]


class EntityNeighborsResponse(SealedModel):
    """Response for `GET /api/v1/kbs/{kb}/entities/{entity_id}/neighbors`."""

    entity_id: str
    kb_number: str | None
    items: list[NeighborEntityItem]
    truncated: bool
    omitted: int


class GraphPathEntityItem(SealedModel):
    """Entity step along a graph path."""

    id: str
    name: str
    type: str


class GraphPathRelationItem(SealedModel):
    """Relation step along a graph path."""

    type: str
    direction: Literal["in", "out"]


class GraphPathResponse(SealedModel):
    """Response for `GET /api/v1/kbs/{kb}/graph/path`."""

    kb_number: str | None
    found: bool
    path: list[GraphPathEntityItem] = Field(default_factory=list)
    relations: list[GraphPathRelationItem] = Field(default_factory=list)
    reason: str | None = None
    hops_tried: int | None = None
