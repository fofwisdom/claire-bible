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


class AccountRefSummary(SealedModel):
    """Public account reference without private internals."""

    account: str
    display_name: str


class ContributionRemoteInfo(SealedModel):
    """Remote origin metadata for a federated contribution."""

    document: str
    knowledge_base: str | None = None
    credit: str | None = None


class ContributionOriginRemovedInfo(SealedModel):
    """Origin removal notice attached to a contribution."""

    at: float
    reason: str | None = None
    waiting: bool


class ContributionRevisionSummary(SealedModel):
    """Revision history entry of a contribution."""

    document_id: str
    state: str
    created_at: float


class ContributionOfferSummary(SealedModel):
    """Federation offer details for a remote contribution."""

    actor: str | None = None
    server: str
    with_grant: bool
    grant_valid: bool


class ContributionReputationSummary(SealedModel):
    """Contributor reputation level and approval rate."""

    level: str
    approval_rate: float | None = None


class ContributionSummary(SealedModel):
    """Canonical representation of one contribution to a shared KB."""

    number: str
    document_id: str | None = None
    title: str | None = None
    origin: str
    state: str
    contributor: AccountRefSummary | None = None
    approved: bool
    approved_at: float | None = None
    updates_stopped: bool
    contributed_at: float
    revised_at: float | None = None
    remote: ContributionRemoteInfo | None = None
    origin_removed: ContributionOriginRemovedInfo | None = None
    reason: str | None = None
    revisions: list[ContributionRevisionSummary] | None = None
    offer: ContributionOfferSummary | None = None
    contributor_reputation: ContributionReputationSummary | None = None
    review_help: dict[str, Any] | None = None


class ContributionListResponse(SealedModel):
    """Paginated response for `GET /api/v1/kbs/{kb}/contributions`."""

    items: list[ContributionSummary]
    next_cursor: str | None
    total: int


class CreateContributionRequest(InputModel):
    """Request body for `POST /api/v1/kbs/{kb}/contributions`."""

    document_id: str = Field(min_length=1, max_length=128)


class ReviewContributionRequest(InputModel):
    """Request body for `PATCH /api/v1/kbs/{kb}/contributions/{number}`."""

    action: Literal["approve", "reject", "hide", "unhide", "stop_updates", "restart_updates"]
    reason: str = ""


class RemoveContributionRequest(InputModel):
    """Request body for `POST /api/v1/kbs/{kb}/contributions/{number}/remove`."""

    reason: str = ""


class ContributionDetailResponse(SealedModel):
    """Response for contribution mutations."""

    contribution: ContributionSummary | None = None


class WithdrawContributionResponse(SealedModel):
    """Response for `DELETE /api/v1/kbs/{kb}/contributions/{number}`."""

    withdrawn: bool
    contribution_blocked: bool


class RemoveContributionResponse(SealedModel):
    """Response for `POST /api/v1/kbs/{kb}/contributions/{number}/remove`."""

    removed: bool


class MemberSummary(SealedModel):
    """Shared KB membership entry."""

    number: str
    account: str
    display_name: str
    role: str
    state: str
    requested_at: float
    decided_at: float | None = None
    username: str | None = None


class MemberListResponse(SealedModel):
    """Paginated response for `GET /api/v1/kbs/{kb}/members`."""

    items: list[MemberSummary]
    next_cursor: str | None
    total: int


class JoinMemberResponse(SealedModel):
    """Response for `POST /api/v1/kbs/{kb}/members`."""

    created: bool
    membership: MemberSummary


class LeaveMemberResponse(SealedModel):
    """Response for `DELETE /api/v1/kbs/{kb}/members`."""

    left: bool


class UpdateMemberRequest(InputModel):
    """Request body for `PATCH /api/v1/kbs/{kb}/members/{account}`."""

    action: Literal["approve", "ban", "remove"] | None = None
    role: Literal["member", "collaborator"] | None = None
    reason: str = ""


class UpdateMemberResponse(SealedModel):
    """Response for `PATCH /api/v1/kbs/{kb}/members/{account}`."""

    member: MemberSummary | None = None


class ExtractionSpecResponse(SealedModel):
    """Response for `GET /api/v1/extraction/spec`."""

    schema_version: str
    prompt_version: str
    prompt_template: str
    prompts: dict[str, Any]
    json_schema: dict[str, Any]
    ontology: dict[str, Any]
    framing_rules: dict[str, Any]
    summary_rules: dict[str, Any]
    asciidoc_rules: dict[str, Any]
    model_rules: dict[str, Any]
    embedding_policy: dict[str, Any]


class ExtractionStageRequest(InputModel):
    """JSON request body for `POST /api/v1/extraction/stage`."""

    url_or_text: str | None = None
    url: str | None = None
    text: str | None = None
    content_base64: str | None = None
    filename: str | None = None
    document_id: str | None = None
    title: str | None = None
    source_type: str | None = None
    full_content: bool = True
    force: bool = False
    refetch: bool = False


class ExtractionStageResponse(SealedModel):
    """Response for `POST /api/v1/extraction/stage`."""

    ok: bool = True
    document_id: str
    kb_number: str | None = None
    title: str | None = None
    source_type: str | None = None
    extraction_status: str
    content_hash: str | None = None
    raw_chars: int = 0
    full_content: bool = True
    staged: bool = True
    duplicate: bool = False
    restaged: bool = False
    message: str | None = None


class ExtractionSourceListItem(SealedModel):
    """Summary entry for a staged source document."""

    document_id: str
    title: str | None = None
    url: str | None = None
    source_type: str | None = None
    extraction_status: str
    content_hash: str | None = None
    raw_chars: int = 0
    fetched_at: float | None = None
    claimed_by_client_id: str | None = None
    lease_expires_at: float | None = None


class ExtractionSourceListResponse(SealedModel):
    """Paginated response for `GET /api/v1/extraction/sources`."""

    items: list[ExtractionSourceListItem]
    next_cursor: str | None
    total: int
    status: str | None = None


class ExtractionSourceDetailResponse(SealedModel):
    """Response for `GET /api/v1/extraction/sources/{document_id}`."""

    ok: bool = True
    document_id: str
    kb_number: str | None = None
    title: str | None = None
    url: str | None = None
    source_type: str | None = None
    extraction_status: str
    content_hash: str | None = None
    raw_chars: int = 0
    offset: int = 0
    returned_chars: int = 0
    has_more: bool = False
    next_offset: int | None = None
    line_offset: int | None = None
    line_limit: int | None = None
    total_lines: int | None = None
    returned_lines: int | None = None
    next_line_offset: int | None = None
    nonce: str
    framed_content: str


class ExtractionClaimRequest(InputModel):
    """Request body for `POST /api/v1/extraction/sources/{document_id}/claim`."""

    client_id: str | None = None
    lease_seconds: float | int = 300.0
    force: bool = False


class ExtractionRenewRequest(InputModel):
    """Request body for `POST /api/v1/extraction/sources/{document_id}/renew`."""

    claim_token: str = Field(min_length=1)
    lease_seconds: float | int = 300.0


class ExtractionClaimResponse(SealedModel):
    """Response for claim and renew endpoints."""

    ok: bool = True
    document_id: str
    kb_number: str | None = None
    claim_token: str
    claimed_by_client_id: str | None = None
    lease_expires_at: float
    content_hash: str | None = None
    expected_content_hash: str | None = None
    extraction_status: str | None = None


class ExtractionSubmitRequest(InputModel):
    """Request body for `POST /api/v1/extraction/sources/{document_id}/submit`."""

    claim_token: str = Field(min_length=1)
    expected_content_hash: str | None = None
    content_hash: str | None = None
    dry_run: bool = False
    payload: dict[str, Any]


class ExtractionSubmitResponse(SealedModel):
    """Response for `POST /api/v1/extraction/sources/{document_id}/submit`."""

    ok: bool
    dry_run: bool = False
    committed: bool = False
    document_id: str
    kb_number: str | None = None
    extraction_status: str | None = None
    extraction_origin: str | None = None
    client_id: str | None = None
    oauth_client_id: str | None = None
    model: str | None = None
    schema_version: str | None = None
    expected_content_hash: str | None = None
    summary: str | None = None
    entities_count: int | None = None
    relations_count: int | None = None
    detail_chars: int | None = None
    entities_created: int | None = None
    entities_linked: int | None = None
    relations_added: int | None = None
    proposals_logged: int | None = None
    warnings: list[str] = Field(default_factory=list)
    errors: list[dict[str, Any]] = Field(default_factory=list)


class FederationFollowerCount(SealedModel):
    """Follower counter summary."""

    total: int


class FederationStatusResponse(SealedModel):
    """Response for `GET /api/v1/federation/status`."""

    handle: str | None = None
    actor_id: str | None = None
    is_actor: bool
    actor_state: Literal["active", "account_not_active", "no_username"]
    discoverable: bool
    followers: FederationFollowerCount
    pending_tasks: int


class FederationPublishRequest(InputModel):
    """Request body for `POST /api/v1/federation/publish`."""

    kb: str = "personal"
    document_id: str = Field(min_length=1, max_length=128)


class FederationObjectSummary(SealedModel):
    """Published ActivityPub object metadata."""

    id: str
    url: str
    published_at: float
    updated_at: float | None = None


class FederationTaskSummary(SealedModel):
    """Outbox delivery task descriptor."""

    id: int
    kind: str


class FederationPublishResponse(SealedModel):
    """Response for `POST /api/v1/federation/publish`."""

    object: FederationObjectSummary
    task: FederationTaskSummary
    created: bool


class FederationRetractRequest(InputModel):
    """Request body for `POST /api/v1/federation/retract`."""

    object_id: str = Field(min_length=1)


class FederationRetractResponse(SealedModel):
    """Response for `POST /api/v1/federation/retract`."""

    retracted: bool
    object_id: str
    task: FederationTaskSummary


class FeedSummary(SealedModel):
    """Feed subscription entry."""

    id: int
    url: str
    site_url: str | None = None
    title: str
    folder: str
    format: str | None = None
    websub_hub: str | None = None
    last_polled_at: float | None = None
    last_success_at: float | None = None
    next_poll_at: float | None = None
    last_status: str | None = None
    last_error: str | None = None
    failure_count: int = 0
    created_at: float
    items: int | None = None
    unread: int | None = None


class FeedListResponse(SealedModel):
    """Paginated response for `GET /api/v1/feeds`."""

    items: list[FeedSummary]
    next_cursor: str | None
    total: int
    folders: list[str]
    limit_per_account: int


class CreateFeedRequest(InputModel):
    """Request body for `POST /api/v1/feeds`."""

    url: str = Field(min_length=1)
    title: str = ""
    folder: str = ""


class FeedDetailResponse(SealedModel):
    """Response for `POST /api/v1/feeds`."""

    feed: FeedSummary
    created: bool


class DeleteFeedRequest(InputModel):
    """Request body for `DELETE /api/v1/feeds`."""

    feed_id: int = Field(gt=0)


class DeleteFeedResponse(SealedModel):
    """Response for `DELETE /api/v1/feeds`."""

    removed: bool
    feed_id: int
    items_removed: int


class FeedItemDocumentRef(SealedModel):
    """Document reference attached to a feed item."""

    kb_number: str | None = None
    document_id: str


class FeedItemSummary(SealedModel):
    """Feed reader item entry."""

    id: int
    feed_id: int
    feed_title: str | None = None
    feed_folder: str | None = None
    url: str | None = None
    title: str | None = None
    author: str | None = None
    summary: str | None = None
    categories: list[str] = Field(default_factory=list)
    published_at: float | None = None
    modified_at: float | None = None
    fetched_at: float | None = None
    unread: bool
    starred: bool
    text: str
    content: str | None = None
    linked: FeedItemDocumentRef | None = None
    timeline_item_id: str | None = None


class FeedItemListResponse(SealedModel):
    """Paginated response for `GET /api/v1/feeds/items`."""

    items: list[FeedItemSummary]
    next_cursor: str | None


class UpdateFeedItemsRequest(InputModel):
    """Request body for `PATCH /api/v1/feeds/items`."""

    item_ids: list[int] | None = None
    feed_id: int | None = None
    unread: bool | None = None
    starred: bool | None = None


class UpdateFeedItemsResponse(SealedModel):
    """Response for `PATCH /api/v1/feeds/items`."""

    changed: int


class FeedItemIngestRequest(InputModel):
    """Request body for `POST /api/v1/feeds/items/{item_id}/ingest`."""

    extraction_mode: Literal["provider", "client"]


class FeedItemIngestResponse(SealedModel):
    """Response for `POST /api/v1/feeds/items/{item_id}/ingest`."""

    report: IngestResponse
    linked: FeedItemDocumentRef | None = None


class OAuthGrantSummary(SealedModel):
    """Active OAuth 2.1 grant entry."""

    grant_id: str
    client_id: str
    client_name: str
    redirect_uris: list[str]
    scope: str
    granted_scope: str
    created_at: float
    expires_at: float
    last_used_at: float | None = None
    revoked_at: float | None = None


class OAuthGrantListResponse(SealedModel):
    """Paginated response for `GET /api/v1/me/oauth/grants`."""

    items: list[OAuthGrantSummary]
    next_cursor: str | None
    total: int


class RevokeOAuthGrantsRequest(InputModel):
    """Request body for `DELETE /api/v1/me/oauth/grants`."""

    grant_id: str | None = None
    client_id: str | None = None
    revoke_all: bool = False


class RevokeOAuthGrantsResponse(SealedModel):
    """Response for `DELETE /api/v1/me/oauth/grants`."""

    revoked: int


class SupportBundleSummary(SealedModel):
    """Diagnostic support bundle metadata."""

    bundle_id: str
    scope: Literal["account", "server"]
    link: str | None = None
    days_covered: int | None = None
    target_kind: str | None = None
    target_link: str | None = None
    target_resolution_status: str | None = None
    size_bytes: int | None = None
    created_at: str | None = None
    expires_at: str | float | None = None


class SupportBundleListResponse(SealedModel):
    """Paginated response for `GET /api/v1/support/bundles`."""

    items: list[SupportBundleSummary]
    next_cursor: str | None
    total: int
    max_active: int
    scopes: list[Literal["account", "server"]]


class CreateSupportBundleRequest(InputModel):
    """Request body for `POST /api/v1/support/bundles`."""

    scope: Literal["account", "server"]
    days: int = 1
    target: str | None = None


class SupportBundleDetailResponse(SealedModel):
    """Response for `POST /api/v1/support/bundles`."""

    bundle: SupportBundleSummary


class DeleteSupportBundleResponse(SealedModel):
    """Response for `DELETE /api/v1/support/bundles/{bundle_id}`."""

    bundle_id: str
    deleted: bool

