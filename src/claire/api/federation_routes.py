"""Route table of the internal federation API (Fedify sidecar design Section 6.2, unit FP1-3).

Paths are under ``federation_internal.INTERNAL_PREFIX``. ``SignedRequestMiddleware`` checks each request before a handler runs. The handlers do not read a ``Principal`` credential.

Phase FP1 has these endpoints: ``config``, ``heartbeat``, ``actors/by-username``, ``actors/{public_id}``, and ``actors/{public_id}/keys``. Unit FP2-2 adds ``inbox-events`` (Section 6.3), ``actors/{public_id}/followers``, and ``domain-blocks``. Unit FP3-2 adds ``outbox-tasks``, ``outbox-tasks/{id}/ack``, ``objects/{object_public_id}``, and ``actors/{public_id}/outbox`` (Section 9.3). Unit FP4-3 adds ``client-tasks``, ``client-tasks/{id}/ack``, and ``actors/{public_id}/following``. Unit FP4-4 adds ``timeline-events`` and ``timeline-interest`` (FEDERATION_REMOTE_READING_DESIGN.md Section 6). Unit FP4-8 (contract 1.4) adds the inbound interactions with the account: mentions, reactions to own objects, and the automatic notice for a non-public message (Sections 6.2 rule 4 and 6.6). Unit FP4-9Q (contract 1.7) adds quotes (FEP-044f): ``quote`` in the normal form of an object, ``quote_policy`` in the object record, and the routes of ``federation_quote_routes``.

Actor rule (FEDERATION_GO_LIVE_DESIGN.md decisions GD4, GD5, GD7, and GD8): each account with the status ``active`` and a USERNAME is an actor, in each server mode. ``profile_public`` controls only directory listing, ActivityPub ``discoverable``, and search indexing; published KB objects follow knowledge base ``is_public``. All other account states give the same 404. An actor record has no field from ``auth.db``.

Request and response models are sealed (``extra="forbid"``, ``strict=True``). The JSON fixtures in ``tests/fixtures/federation_contract/`` are the contract that the Python tests and the sidecar tests share.
"""

from __future__ import annotations

import importlib.metadata
import hashlib
import json
import logging
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

import html as _html

import nh3
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import BaseRoute, Route

from ..store import accounts as accounts_store
from ..store import actor_keys
from ..store import auth_db
from ..store import permalinks
from ..core import ids
from ..store import db as dbm
from ..store import document_renditions as renditions_store
from ..store import plus_ones as plus_ones_store
from ..extract.rendition_templates import PRIMARY_RENDITION_TEMPLATE
from ..federation import media_proxy
from ..store import client_db
from ..store import federation as federation_store
from ..store import federation_client as client_store
from ..store import federation_fills as fills_store
from ..store import federation_notes as notes_store
from ..store import federation_opinions as opinions_store
from ..store import federation_quotes as quotes_store
from ..store.secrets import get_secret_service
from ..i18n import catalog as i18n_catalog
from ..i18n.resolver import resolve_bot_locale
from ..notifications import events as notification_events
from .collections import parse_collection_query

__all__ = [
    "CONTRACT_VERSION",
    "HEARTBEAT_FILE",
    "INBOX_BODY_LIMIT_BYTES",
    "OUTBOX_LEASE_SECONDS",
    "ActorOutboxResponse",
    "ObjectRecord",
    "OutboxAckRequest",
    "OutboxAckResponse",
    "OutboxTasksResponse",
    "ActorKeysResponse",
    "ActorRecord",
    "ConfigResponse",
    "DomainBlocksResponse",
    "FollowersResponse",
    "HeartbeatLoops",
    "HeartbeatRequest",
    "HeartbeatResponse",
    "InboxEventRequest",
    "InboxEventResponse",
    "KnowledgeSelfCheck",
    "LoopState",
    "SignedSelfCheck",
    "TimelineEventRequest",
    "TimelineEventResponse",
    "TimelineInterestResponse",
    "federation_internal_routes",
    "read_heartbeat",
    "software_version",
]

# ``major.minor``. Claire refuses a sidecar that has a different major version.
CONTRACT_VERSION = "1.24"
SOFTWARE_NAME = "claire-bible"
HEARTBEAT_FILE = Path("run") / "federation_heartbeat.json"
SERVER_ACTOR_ID = "server"
# The largest body of a public inbox request that the sidecar reads (design Section 11 item 4). The sidecar gets the value from ``config``.
INBOX_BODY_LIMIT_BYTES = 1024 * 1024
_MAX_IRI_LENGTH = 2048
# A task that the sidecar took and did not acknowledge is ready again after this time (design Section 9.3 item 4).
OUTBOX_LEASE_SECONDS = 300.0
OUTBOX_TASKS_DEFAULT = 20
OUTBOX_TASKS_MAX = 50
# The largest number of actors of this server that one object can mention in an event (unit FP4-8). The sidecar keeps the first ones.
MENTIONED_MAX = 16
# The mention rate limit counts the items of one remote domain and account in this time (Section 6.6 rule 6).
MENTION_RATE_WINDOW_SECONDS = 3600.0
# The fragment of the ID of an automatic notice: ``{actor}#auto-replies/{digest}``. A reply to such an ID gets no notice (Section 6.2 rule 4).
AUTO_REPLY_FRAGMENT = "#auto-replies/"
# Thread fetch (contract 1.5, unit FP4-6): the number of tasks that one ``context-tasks`` call gives by default, and the largest number of objects in one ``context-objects`` request (the highest ``context_max_items``).
CONTEXT_TASKS_DEFAULT = 2
CONTEXT_OBJECTS_MAX = 1000

_log = logging.getLogger("claire.federation.routes")

_NO_STORE = {"Cache-Control": "no-store"}


class _Sealed(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class SoftwareInfo(_Sealed):
    name: str
    version: str


class Limits(_Sealed):
    inbox_body_bytes: int


class ConfigResponse(_Sealed):
    contract_version: str
    canonical_origin: str
    handle_host: str
    mode: Literal["single", "network"]
    software: SoftwareInfo
    trusted_proxies: list[str]
    # Number of federation actors. NodeInfo shows this number only (design Section 5.1 rule 6). It is 0 when federation is not active.
    federated_actors: int
    limits: Limits
    # Entity tag of the ``domain-blocks`` response. The sidecar reads the list again when this value changes.
    domain_blocks_etag: str
    # Development only: the sidecar can read documents of remote servers at private addresses (a test server on the same host or network). True only when the environment is ``development`` and ``CLAIRE_ALLOW_PRIVATE_NETWORKS`` is on; always false in production.
    allow_private_addresses: bool


AnswerParty = Literal["sidecar", "sidecar_other", "claire", "cdn_challenge", "cdn_block", "cdn_error", "intermediary", "none"]
AnswerError = Literal["dns", "tls", "connect", "timeout", "other"]
ProbeProblem = Literal["not_routed", "path_changed", "host_changed", "answer_not_live", "signature_invalid"]
_HeaderText = Annotated[str, Field(min_length=1, max_length=64)]
_ProbePath = Annotated[str, Field(min_length=1, max_length=256)]


class ProbeAnswer(_Sealed):
    """One failed self-check request of the sidecar: what went wrong and which party answered (FEDERATION_GO_LIVE_DESIGN.md Section 5, contract 1.13)."""

    path: _ProbePath
    problem: ProbeProblem
    party: AnswerParty
    status: Annotated[int, Field(ge=100, le=599)] | None
    error: AnswerError | None
    via_cdn: Literal["cloudflare"] | None
    server: _HeaderText | None
    media_type: _HeaderText | None
    mitigation: Annotated[str, Field(min_length=1, max_length=32)] | None
    # The path with the query (problem ``path_changed``) or the Host (problem ``host_changed``) that the sidecar got.
    received: _ProbePath | None


class SelfCheck(_Sealed):
    """Result of the public self-check of the sidecar (design Section 12, GF6). ``ok`` is ``None`` before the first check."""

    ok: bool | None
    checked_at: float | None
    answer: ProbeAnswer | None


class SignedSelfCheck(_Sealed):
    """Result of one signed POST self-check of the sidecar (KNOWLEDGE_FEDERATION_DESIGN.md Section 14.1.4, GF9)."""

    result: Literal["ok", "failed"] | None
    answer: ProbeAnswer | None


class SignedSelfChecks(_Sealed):
    """One signed POST for each signature form of a delivery (FEDERATION_GO_LIVE_DESIGN.md Section 6)."""

    draft_cavage: SignedSelfCheck
    rfc9421: SignedSelfCheck


class KnowledgeSelfCheck(_Sealed):
    """Result of the knowledge federation self-check of the sidecar (KNOWLEDGE_FEDERATION_DESIGN.md Section 14.1.4, GF8 and GF9)."""

    checked_at: float | None
    failed_paths: Annotated[list[Annotated[str, Field(min_length=1, max_length=128)]], Field(max_length=16)] | None
    answers: Annotated[list[ProbeAnswer], Field(max_length=8)]
    signed: SignedSelfChecks


class PublicHostAddress(_Sealed):
    address: Annotated[str, Field(min_length=2, max_length=64)]
    # The JSON name is ``class``.
    address_class: Literal["loopback", "private", "global"] = Field(alias="class")


class PublicHost(_Sealed):
    """The addresses that the host of the public origin has for the sidecar process (FEDERATION_GO_LIVE_DESIGN.md Section 7.1, GF18)."""

    host: Annotated[str, Field(max_length=255)]
    addresses: Annotated[list[PublicHostAddress], Field(max_length=16)]
    error: Literal["dns"] | None


class TrafficInboundVerified(_Sealed):
    count: int = Field(ge=0)
    hosts: int = Field(ge=0)
    last_at: float | None


class TrafficInboundRejected(_Sealed):
    count: int = Field(ge=0)
    last_at: float | None


class TrafficDeliveryFailures(_Sealed):
    dns: int = Field(ge=0)
    tls: int = Field(ge=0)
    timeout: int = Field(ge=0)
    http_4xx: int = Field(ge=0)
    http_5xx: int = Field(ge=0)
    other: int = Field(ge=0)


class TrafficDeliveries(_Sealed):
    succeeded: int = Field(ge=0)
    failed: TrafficDeliveryFailures
    last_success_at: float | None


class Traffic(_Sealed):
    """Requests of other servers and deliveries to them, from the memory of the sidecar process (FEDERATION_GO_LIVE_DESIGN.md Section 9.1, GF19 and GF20)."""

    window_seconds: int = Field(ge=1)
    inbound_verified: TrafficInboundVerified
    inbound_rejected: TrafficInboundRejected
    deliveries: TrafficDeliveries


class LoopState(_Sealed):
    """Progress and last error of one background loop of the sidecar (KNOWLEDGE_FEDERATION_DESIGN.md Section 14.1.4, GF11)."""

    running: bool
    progress_at: float | None
    error: Annotated[str, Field(pattern=r"^[a-z0-9_]{1,64}$")] | None
    error_at: float | None


class HeartbeatLoops(_Sealed):
    """State of the two knowledge federation loops of the sidecar (GF11)."""

    knowledge_reads: LoopState
    knowledge_exchange: LoopState


class HeartbeatRequest(_Sealed):
    contract_version: str = Field(pattern=r"^[0-9]{1,4}\.[0-9]{1,4}$")
    sidecar_version: str = Field(min_length=1, max_length=64)
    queue_depth: int = Field(ge=0)
    # The random value of the sidecar process. Each answer of the sidecar has it in the header ``X-Claire-Fedify-Server``.
    marker: str = Field(pattern=r"^[A-Za-z0-9_-]{8,64}$")
    public_host: PublicHost
    self_check: SelfCheck
    knowledge_self_check: KnowledgeSelfCheck
    loops: HeartbeatLoops
    traffic: Traffic


class HeartbeatResponse(_Sealed):
    contract_version: str


class ActorRecord(_Sealed):
    public_id: str
    username: str
    display_name: str
    activated_at: float | None
    updated_at: float
    profile_url: str
    discoverable: bool
    summary: str | None = None


class ActorKey(_Sealed):
    alg: Literal["rsa2048", "ed25519"]
    public_jwk: dict[str, str]
    private_jwk: dict[str, str]


class ActorKeysResponse(_Sealed):
    public_id: str
    keys: list[ActorKey]


class InboxActor(_Sealed):
    iri: str = Field(min_length=1, max_length=_MAX_IRI_LENGTH)
    inbox: str = Field(min_length=1, max_length=_MAX_IRI_LENGTH)
    shared_inbox: str | None = Field(min_length=1, max_length=_MAX_IRI_LENGTH)


class InboxEventRequest(_Sealed):
    """One inbound activity that the sidecar verified (design Section 6.3).

    ``type`` is the type of the activity. ``object_iri`` is the ID of its object. For an ``Undo``, ``object_type`` is the type of the activity that is undone and ``object_iri`` is the object of that activity. ``recipient_public_id`` is the actor of the inbox that got the activity, or ``None`` for the shared inbox.
    """

    event_id: str = Field(min_length=1, max_length=_MAX_IRI_LENGTH)
    type: str = Field(min_length=1, max_length=64)
    object_type: str | None = Field(min_length=1, max_length=64)
    recipient_public_id: str | None = Field(pattern=r"^[1-9][0-9]{0,18}$")
    actor: InboxActor
    object_iri: str | None = Field(min_length=1, max_length=_MAX_IRI_LENGTH)
    verified_by: Literal["http-signature", "ld-signature", "object-integrity-proof"]


class InboxEventResponse(_Sealed):
    decision: Literal["accepted", "rejected", "ignored"]
    # ``accept``: the sidecar sends ``Accept(Follow)``.
    reply: Literal["accept"] | None
    # A code for the log and the telemetry of the sidecar. ``None`` for ``accepted``.
    # ``no_object``: a ``Like``, an ``Announce``, or their ``Undo`` refers to an object path of this server that is not a public object now (unit FP4-8).
    reason: Literal["invalid_actor", "blocked_domain", "not_an_actor", "recipient_mismatch", "unsupported_type", "no_object"] | None


class FollowerItem(_Sealed):
    id: str
    actor_iri: str
    inbox_iri: str
    shared_inbox_iri: str | None


class FollowersResponse(_Sealed):
    public_id: str
    total: int
    # Newest first.
    items: list[FollowerItem]
    # The ``max_id`` of the next page, or ``None`` when this page is not full.
    next_max_id: str | None


class DomainBlocksResponse(_Sealed):
    etag: str
    # Lowercase ASCII, in order. A block of a domain also applies to its subdomains.
    domains: list[str]


class OutboxTaskItem(_Sealed):
    id: int
    kind: Literal["create", "update", "delete", "update_actor", "delete_actor"]
    # The actor that sends the activity.
    actor_public_id: str
    object_public_id: str | None
    created_at: float
    lease_until: float


class OutboxTasksResponse(_Sealed):
    # Oldest first. Each task has a lease until ``lease_until``.
    tasks: list[OutboxTaskItem]


class OutboxAckRequest(_Sealed):
    # ``handed_off``: the Fedify queue has the activity. ``rejected``: the sidecar cannot make the activity, and a new attempt has no use.
    result: Literal["handed_off", "rejected"]
    error_code: str | None = Field(pattern=r"^[a-z0-9_]{1,64}$")


class OutboxAckResponse(_Sealed):
    id: int
    status: Literal["pending", "handed_off", "rejected"]


class ClientTaskItem(_Sealed):
    """One task of the client outbox (contract 1.3, FEDERATION_REMOTE_READING_DESIGN.md Section 5.2, unit FP4-3).

    ``follow``: ``target`` is the handle or IRI that the owner gave; the sidecar resolves it and sends ``Follow`` with the ID ``{actor}#follows/{following_id}``. ``undo_follow``: ``target`` is the actor IRI; the sidecar sends ``Undo`` of that ``Follow``. ``reject_follow``: ``target`` is the actor IRI of a removed follower; the sidecar sends ``Reject(Follow)``.
    """

    id: int
    kind: Literal["follow", "undo_follow", "reject_follow"]
    actor_public_id: str
    target: str
    following_id: str | None
    created_at: float
    lease_until: float


class ClientTasksResponse(_Sealed):
    tasks: list[ClientTaskItem]


class ResolvedActor(_Sealed):
    iri: str = Field(min_length=1, max_length=_MAX_IRI_LENGTH)
    type: Literal["Person", "Group", "Service", "Application", "Organization"]
    inbox: str = Field(min_length=1, max_length=_MAX_IRI_LENGTH)
    shared_inbox: str | None = Field(min_length=1, max_length=_MAX_IRI_LENGTH)
    name: str | None = Field(max_length=512)


class ClientAckRequest(_Sealed):
    result: Literal["handed_off", "rejected"]
    error_code: str | None = Field(pattern=r"^[a-z0-9_]{1,64}$")
    # The actor of a ``follow`` task that the sidecar resolved. ``None`` for other kinds and for a rejected follow.
    actor: ResolvedActor | None


class FollowingResponse(_Sealed):
    public_id: str
    # Accepted follows only. The public collection shows the total only (RD9).
    total: int


class TimelineActor(_Sealed):
    """The author or the announcer of a remote object. ``handle`` is ``@name@host`` from the actor document, without a WebFinger check."""

    iri: str = Field(min_length=1, max_length=_MAX_IRI_LENGTH)
    name: str | None = Field(max_length=512)
    handle: str | None = Field(max_length=512)


class TimelineAttachment(_Sealed):
    url: str = Field(min_length=1, max_length=_MAX_IRI_LENGTH)
    media_type: str | None = Field(max_length=128)
    name: str | None = Field(max_length=4096)
    width: int | None = Field(ge=0)
    height: int | None = Field(ge=0)
    blurhash: str | None = Field(max_length=256)


class TimelineObject(_Sealed):
    """The normal form of one remote object (FEDERATION_REMOTE_READING_DESIGN.md Section 6.3). The times are Unix seconds, as in the other shapes of this contract. ``content_html`` is the raw HTML of the remote server."""

    object_iri: str = Field(min_length=1, max_length=_MAX_IRI_LENGTH)
    url: str | None = Field(min_length=1, max_length=_MAX_IRI_LENGTH)
    type: Literal["Note", "Article", "Page", "Question"]
    author: TimelineActor
    name: str | None = Field(max_length=4096)
    summary: str | None = Field(max_length=16384)
    sensitive: bool
    # The limit of Section 6.3 is in bytes and gives ``too_large``. This length only stops a body that no inbox request can have.
    content_html: str = Field(max_length=2 * 1024 * 1024)
    language: str | None = Field(max_length=64)
    published_at: float | None
    updated_at: float | None
    attachments: list[TimelineAttachment] = Field(max_length=64)
    context: str | None = Field(min_length=1, max_length=_MAX_IRI_LENGTH)
    in_reply_to: str | None = Field(min_length=1, max_length=_MAX_IRI_LENGTH)
    # Contract 1.7 (unit FP4-9Q): the object that this object quotes (FEP-044f ``quote``, else ``quoteUrl`` with its other keys), or ``None``. Claire shows a link to it and does not verify the authorization of the quote.
    quote: str | None = Field(min_length=1, max_length=_MAX_IRI_LENGTH)


class TimelineEventRequest(_Sealed):
    """One inbound activity for the timelines of the accounts (Sections 6.1 to 6.5, unit FP4-4).

    ``Create`` and ``Update``: ``object`` is the object of the activity, and its author is ``actor``. ``Announce``: ``object`` is the object that the sidecar fetched, and ``announced_by`` is the profile of ``actor``. ``Delete``: ``object_iri`` is the deleted object. ``Undo``: an ``Undo(Announce)``, and ``object_iri`` is the object of the ``Announce``.

    ``public`` is ``False`` for an object without the public collection in ``to`` or ``cc``; then ``object`` is ``None``, because Claire stores no such object. ``public`` is ``None`` for ``Delete`` and ``Undo``. ``parents`` is the reply chain of ``object``, nearest parent first (Section 6.5 rule 2).

    Contract 1.4 (unit FP4-8): ``actor_type`` is the type of ``actor`` (``None`` for a type outside the list). ``mentioned`` has the actors of this server that the object of a ``Create`` mentions (a ``Mention`` tag) or addresses (``to``, ``cc``). ``in_reply_to`` is the reply target of the object; it is also given when ``object`` is ``None``.
    """

    event_id: str = Field(min_length=1, max_length=_MAX_IRI_LENGTH)
    type: Literal["Create", "Update", "Delete", "Announce", "Undo"]
    recipient_public_id: str | None = Field(pattern=r"^[1-9][0-9]{0,18}$")
    actor: InboxActor
    announced_by: TimelineActor | None
    object_iri: str = Field(min_length=1, max_length=_MAX_IRI_LENGTH)
    public: bool | None
    object: TimelineObject | None
    parents: list[TimelineObject] = Field(max_length=20)
    actor_type: Literal["Person", "Group", "Service", "Application", "Organization"] | None
    mentioned: list[Annotated[str, Field(min_length=1, max_length=_MAX_IRI_LENGTH)]] = Field(max_length=MENTIONED_MAX)
    in_reply_to: str | None = Field(min_length=1, max_length=_MAX_IRI_LENGTH)
    verified_by: Literal["http-signature", "ld-signature", "object-integrity-proof"]


class TimelineNotice(_Sealed):
    """One automatic notice for a non-public message (Section 6.2 rule 4, unit FP4-8). The sidecar sends a ``Note`` of the actor ``actor_public_id`` to the sender of the event only, with ``inReplyTo`` = the object of the event and a ``Mention`` of the sender. ``content`` is HTML that Claire made from a fixed text in ``language`` (the locale of the account)."""

    actor_public_id: str
    content: str
    language: Literal["en", "ko"]


class TimelineEventResponse(_Sealed):
    decision: Literal["accepted", "rejected", "ignored"]
    reason: Literal["invalid_actor", "blocked_domain", "invalid_object", "too_large", "not_public", "not_followed", "no_item", "rate_limited"] | None
    # The number of items that the event made, changed, or deleted, for all accounts. The parents are not in this number.
    items: int
    # The automatic notices that the sidecar sends now. Empty for all events except a non-public ``Create`` that mentions or addresses an account.
    notices: list[TimelineNotice]


class TimelineInterestResponse(_Sealed):
    """The answer to "does an account that is an actor follow this remote actor". The sidecar asks before it fetches the object of an ``Announce`` or the parents of a reply (RS2, RS4)."""

    followed: bool
    context_fetch: Literal["on", "off"]
    context_depth: int


class ObjectRecord(_Sealed):
    """The canonical form of one published object (design Section 9.1). The sidecar makes the ``Article`` from it."""

    public_id: str
    actor_public_id: str
    type: Literal["Article"]
    name: str
    # HTML after the ``nh3`` allowlist sanitizer.
    content: str
    # The public page of the document, or ``None`` when the document has no share page.
    url: str | None
    published_at: float
    updated_at: float
    # Contract 1.19 (phase E6): the numbers of ``Like`` and ``Announce`` that the object got. The sidecar writes them as ``totalItems`` of ``likes`` and ``shares``.
    likes: int = Field(ge=0)
    shares: int = Field(ge=0)
    # Contract 1.20 (DOCUMENT_RENDITIONS_AND_PUBLICATION_DESIGN.md Section 5.5, phase E4b). A document object is also its 대표 정리본; a rendition object has ``rendition_of``, the number of its document object. ``template`` and ``template_version`` are ``None`` for a body without a rendition row; ``body_hash`` is the SHA-256 of the AsciiDoc body; the source key is ``None`` for a document without a source identifier.
    rendition_of: str | None
    template: Literal["standard", "concise", "academic"] | None
    template_version: str | None
    body_hash: str | None = Field(pattern=r"^[0-9a-f]{64}$")
    source_key: str | None = Field(pattern=r"^[0-9a-f]{64}$")
    source_id_version: int | None = Field(ge=1)
    # Contract 1.22 (Section 5.12, phase E9a): the +1 counts of the rendition. The sidecar writes them in ``claire:plusOnes``.
    plus_ones: int = Field(ge=0)
    plus_one_servers: int = Field(ge=0)
    plus_one_score: int = Field(ge=0)


class OutboxItem(_Sealed):
    id: str
    published_at: float
    updated_at: float


class ActorOutboxResponse(_Sealed):
    public_id: str
    total: int
    # Newest first.
    items: list[OutboxItem]
    next_max_id: str | None


class ContextTaskItem(_Sealed):
    """One thread fetch task (contract 1.5, FEDERATION_REMOTE_READING_DESIGN.md Section 6.5 rule 4, unit FP4-6). The sidecar reads the ``context`` collection of ``object_iri``, or its ``replies`` collections, with requests that the actor ``actor_public_id`` signs. ``max_items`` and ``max_depth`` are ``context_max_items`` and ``context_depth``."""

    id: int
    actor_public_id: str
    object_iri: str
    max_items: int
    max_depth: int
    created_at: float
    lease_until: float


class ContextTasksResponse(_Sealed):
    tasks: list[ContextTaskItem]


class ContextObjectsRequest(_Sealed):
    """Fetched objects of one thread fetch task (Section 6.5 rule 5). Each object is public; the sidecar gives no other object. One task can have more than one request: the body limit of the internal API applies to each one."""

    task_id: int = Field(ge=1)
    objects: list[TimelineObject] = Field(max_length=CONTEXT_OBJECTS_MAX)


class ContextObjectsResponse(_Sealed):
    decision: Literal["accepted", "rejected"]
    reason: Literal["not_an_actor", "context_fetch_off"] | None
    # The number of new items of the account. An object that the account has already is not in this number.
    stored: int
    # The number of objects that Claire did not take: above ``context_max_items``, not permitted (Section 6.2), or without a link to the thread.
    refused: int


def _remote_host(iri: str, origin: str) -> str | None:
    """Return the normalized host of the address of a remote server, or ``None`` when the address is not permitted.

    The scheme must be ``https``. ``http`` is permitted only when the canonical origin of this server uses ``http`` (development). An address with credentials, or on the origin of this server, is not permitted.
    """
    try:
        parts = urlsplit(iri)
        credentials = parts.username is not None or parts.password is not None
    except ValueError:
        return None
    if parts.scheme != "https" and not (parts.scheme == "http" and origin.startswith("http://")):
        return None
    if credentials or f"{parts.scheme}://{parts.netloc}".lower() == origin.lower():
        return None
    return federation_store.iri_host(iri)


def _event_kind(event: InboxEventRequest) -> Literal["follow", "undo_follow", "delete_actor", "update_actor"] | None:
    """Return the v1 operation of an event, or ``None`` for a type outside the v1 set (design Section 6.3)."""
    if event.type == "Follow":
        return "follow"
    if event.type == "Undo" and event.object_type == "Follow":
        return "undo_follow"
    if event.type == "Delete" and event.object_iri == event.actor.iri:
        return "delete_actor"
    if event.type == "Update" and event.object_iri == event.actor.iri:
        return "update_actor"
    return None


def _reaction_kind(event: InboxEventRequest) -> tuple[Literal["like", "announce"], bool] | None:
    """Return the reaction kind of an event and ``True`` for its ``Undo``, or ``None`` (Section 6.6 rule 3)."""
    kinds: dict[str | None, Literal["like", "announce"]] = {"Like": "like", "Announce": "announce"}
    if event.type in kinds:
        return kinds[event.type], False
    if event.type == "Undo" and event.object_type in kinds:
        return kinds[event.object_type], True
    return None


_SHARED_INBOX = "shared"


def _own_actor_address(conn: Any, iri: str, origin: str) -> str | None:
    """RD30 (FEDERATION_REMOTE_READING_DESIGN.md Section 6.7): the actor of this server that owns the address ``iri`` (the actor address itself or its inbox) when the actor is active and has a USERNAME, ``"shared"`` for the shared inbox of this server, else ``None``."""
    base = origin.rstrip("/")
    if iri == f"{base}/ap/inbox":
        return _SHARED_INBOX
    prefix = f"{base}/ap/actors/"
    if not iri.startswith(prefix):
        return None
    number, _sep, tail = iri[len(prefix):].partition("/")
    if tail not in ("", "inbox") or not (number.isascii() and number.isdigit()):
        return None
    row = conn.execute("SELECT 1 FROM accounts WHERE public_id=? AND status='active' AND username IS NOT NULL", (int(number),)).fetchone()
    return None if row is None else f"{prefix}{number}"


def _peer_host(conn: Any, iri: str, origin: str, *, actor_iri: str | None = None) -> str | None:
    """The host of an address of a peer actor: an address of another server (``_remote_host``), or since RD30 an address of an actor of this server. An address of this server is accepted only when it is the address of ``actor_iri`` (the actor itself or its inbox) or the shared inbox; another server cannot use an address of this server. ``None`` when the address is not permitted."""
    host = _remote_host(iri, origin)
    if host is not None:
        return host
    owner = _own_actor_address(conn, iri, origin)
    if owner is None or (owner != _SHARED_INBOX and actor_iri is not None and owner != actor_iri):
        return None
    if owner == _SHARED_INBOX and actor_iri is not None and _own_actor_address(conn, actor_iri, origin) in (None, _SHARED_INBOX):
        return None
    return federation_store.iri_host(origin)


def _local_object_public_id(origin: str, iri: str | None) -> int | None:
    """Return the public ID in an object ID of this server (``{origin}/ap/objects/{public_id}``), or ``None``."""
    prefix = f"{origin}/ap/objects/"
    if iri is None or not iri.startswith(prefix):
        return None
    try:
        return ids.parse_public_id(iri[len(prefix) :])
    except ValueError:
        return None


def _local_follow(origin: str, iri: str | None) -> tuple[str, int] | None:
    """Return the actor public ID and the follow number of a follow ID of this server (``{origin}/ap/actors/{public_id}#follows/{n}``), or ``None``. An ``Accept`` or a ``Reject`` refers to the follow by this ID (RT1)."""
    if iri is None or "#follows/" not in iri:
        return None
    actor_part, _, number = iri.partition("#follows/")
    public_id = _local_actor_public_id(origin, actor_part)
    if public_id is None or not (number.isascii() and number.isdigit() and number[0] != "0" and len(number) <= 18):
        return None
    return public_id, int(number)


def _local_actor_public_id(origin: str, iri: str | None) -> str | None:
    """Return the public ID in an actor ID of this server (``{origin}/ap/actors/{public_id}``), or ``None``."""
    prefix = f"{origin}/ap/actors/"
    if iri is None or not iri.startswith(prefix):
        return None
    raw = iri[len(prefix) :]
    try:
        ids.parse_public_id(raw)
    except ValueError:
        return None
    return raw


def software_version() -> str:
    try:
        return importlib.metadata.version(SOFTWARE_NAME)
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def read_heartbeat(data_dir: Path | str) -> dict[str, Any] | None:
    """Return the last heartbeat record, or ``None`` when there is no valid record. The readiness check GF2 reads this."""
    try:
        record = json.loads((Path(data_dir) / HEARTBEAT_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return record if isinstance(record, dict) else None


ARTICLE_SUMMARY_MAX_CHARS = 500


def rendition_body_text(document: Any, rendition: Any) -> str:
    """The AsciiDoc body of a rendition: ``documents.detail`` for the 대표 정리본, else the body of its row. Empty when there is none."""
    if rendition is None or rendition.primary:
        return str(document["detail"] or "").strip()
    return str(rendition.detail or "").strip()


def _published_object_content(summary: str | None, title: str, share_url: str | None) -> str:
    """Content of the object of a published document (RD21, FEDERATION_REMOTE_READING_DESIGN.md Section 5.5): the summary as one paragraph and the link to the share page. The body of the document is never in the object.

    A document without a summary gives its title. A summary longer than ``ARTICLE_SUMMARY_MAX_CHARS`` is cut at the end of a word and gets "…".
    """
    text = " ".join((summary or "").split()) or " ".join((title or "").split())
    if len(text) > ARTICLE_SUMMARY_MAX_CHARS:
        cut = text[:ARTICLE_SUMMARY_MAX_CHARS]
        space = cut.rfind(" ")
        text = (cut[:space] if space > ARTICLE_SUMMARY_MAX_CHARS // 2 else cut).rstrip() + "…"
    parts = [f"<p>{_html.escape(text, quote=False)}</p>"] if text else []
    if share_url:
        link = _html.escape(share_url)
        parts.append(f'<p><a href="{link}">{link}</a></p>')
    return "".join(parts)


def _json(model: BaseModel, status: int = 200) -> JSONResponse:
    return JSONResponse(model.model_dump(mode="json"), status_code=status, headers=_NO_STORE)


def _error(status: int, code: str) -> JSONResponse:
    return JSONResponse({"error": code}, status_code=status, headers=_NO_STORE)


def _not_found() -> JSONResponse:
    return _error(404, "not_found")


def allow_private_addresses(settings: Any) -> bool:
    """The value of ``allow_private_addresses`` of the config: the two conditions of the development switch."""
    return str(getattr(settings, "environment", "") or "").strip().lower() == "development" and bool(getattr(settings, "allow_private_networks", False))


def federation_internal_routes(settings: Any, *, now: Callable[[], float] = time.time) -> list[BaseRoute]:
    """Make the route table for ``create_internal_federation_app``."""
    from .federation_collection_routes import collection_internal_routes
    from .federation_fill_routes import fill_internal_routes
    from .federation_opinion_routes import opinion_internal_routes
    from .federation_note_routes import note_internal_routes
    from .federation_quote_routes import quote_internal_routes
    from .knowledge_federation_internal import knowledge_internal_routes
    from .security import WebRuntimeConfig

    data_dir = Path(settings.data_dir)

    def _runtime() -> WebRuntimeConfig:
        return WebRuntimeConfig.from_settings(settings)

    def _open_store() -> accounts_store.AccountStore:
        return accounts_store.AccountStore(dbm.connect_existing(settings.db_file, readonly=True))

    # Federation has no switch and the mode is not a condition: the server actor, the federation of shared knowledge, and the account actors work in each server (FEDERATION_GO_LIVE_DESIGN.md decision GD4; KNOWLEDGE_FEDERATION_DESIGN.md Section 15 rule 5, KD2). ``_is_actor`` is the same rule in the two modes.

    def _is_actor(account: accounts_store.Account | None) -> bool:
        return account is not None and account.status == "active" and bool(account.username)

    def _record(account: accounts_store.Account) -> ActorRecord:
        return ActorRecord(
            public_id=str(account.public_id),
            username=str(account.username),
            display_name=account.display_name,
            activated_at=account.activated_at,
            updated_at=account.updated_at,
            profile_url=f"{_runtime().public_origin}/@{account.username}/",
            discoverable=bool(account.profile_public),
            summary=account.summary or None,
        )

    def _domain_blocks(conn: Any) -> tuple[str, list[str]]:
        domains = [block.domain for block in federation_store.list_domain_blocks(conn)]
        return federation_store.domain_blocks_etag(domains), domains

    def _find_actor(store: accounts_store.AccountStore, *, username: str | None = None, public_id: str | None = None) -> accounts_store.Account | None:
        if username is not None:
            account = store.find_by_username(username)
        else:
            # A public ID is a positive decimal number without a sign, a space, or a leading zero.
            if not (public_id and public_id.isascii() and public_id.isdigit() and public_id[0] != "0" and len(public_id) <= 19):
                return None
            account = store.find_by_public_id(int(public_id))
        return account if _is_actor(account) else None

    def _parse_actor_id(public_id: str) -> int | None:
        return int(public_id) if public_id and public_id.isascii() and public_id.isdigit() and public_id[0] != "0" and len(public_id) <= 19 else None

    def _find_departing(store: accounts_store.AccountStore, public_id: str) -> accounts_store.Account | None:
        """Return a purged account whose ``delete_actor`` task waits for the sidecar, or ``None``. The sidecar needs the keys and the followers of this account for that one task (design Section 8.1)."""
        number = _parse_actor_id(public_id)
        if number is None:
            return None
        account = store.find_by_public_id(number)
        if account is None or account.status != "purged" or not federation_store.has_pending_delete_actor(store.conn, account.id):
            return None
        return account

    def config(_request: Request) -> Response:
        runtime = _runtime()
        store = _open_store()
        try:
            mode = store.server_mode()
            blocks_etag, _domains = _domain_blocks(store.conn)
            federated_actors = int(store.conn.execute("SELECT COUNT(*) FROM accounts WHERE status='active' AND username IS NOT NULL").fetchone()[0])
        finally:
            store.conn.close()
        return _json(
            ConfigResponse(
                contract_version=CONTRACT_VERSION,
                canonical_origin=runtime.public_origin,
                handle_host=runtime.expected_authority,
                mode=mode,
                software=SoftwareInfo(name=SOFTWARE_NAME, version=software_version()),
                trusted_proxies=[str(network) for network in runtime.trusted_proxies],
                federated_actors=federated_actors,
                limits=Limits(inbox_body_bytes=INBOX_BODY_LIMIT_BYTES),
                domain_blocks_etag=blocks_etag,
                allow_private_addresses=allow_private_addresses(settings),
            )
        )

    async def heartbeat(request: Request) -> Response:
        try:
            body = HeartbeatRequest.model_validate_json(await request.body())
        except ValidationError:
            return _error(400, "invalid_request")
        if body.contract_version.split(".")[0] != CONTRACT_VERSION.split(".")[0]:
            return _error(409, "contract_version_mismatch")
        record = {"received_at": now(), **body.model_dump(mode="json", by_alias=True)}
        path = data_dir / HEARTBEAT_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(json.dumps(record), encoding="utf-8")
        os.replace(temporary, path)
        return _json(HeartbeatResponse(contract_version=CONTRACT_VERSION))

    def actor_by_username(request: Request) -> Response:
        store = _open_store()
        try:
            account = _find_actor(store, username=request.path_params["username"])
        finally:
            store.conn.close()
        return _not_found() if account is None else _json(_record(account))

    def actor(request: Request) -> Response:
        store = _open_store()
        try:
            # A purged actor is gone (design Section 8.1). This check comes first, the same as for a deleted object.
            number = _parse_actor_id(request.path_params["public_id"])
            tombstone = None if number is None else federation_store.get_tombstone(store.conn, number)
            if tombstone is not None and tombstone.kind == "actor":
                return JSONResponse({"error": "gone", "gone_at": tombstone.gone_at}, status_code=410, headers=_NO_STORE)
            account = _find_actor(store, public_id=request.path_params["public_id"])
        finally:
            store.conn.close()
        return _not_found() if account is None else _json(_record(account))

    def keys(request: Request) -> Response:
        raw_id = request.path_params["public_id"]
        store = _open_store()
        try:
            departing = None
            if raw_id == SERVER_ACTOR_ID:
                key_owner = actor_keys.SERVER_ACTOR_ACCOUNT_ID
            else:
                account = _find_actor(store, public_id=raw_id)
                departing = _find_departing(store, raw_id) if account is None else None
                key_owner = departing.id if departing is not None else None if account is None else account.id
        finally:
            store.conn.close()
        if key_owner is None:
            return _not_found()
        # The first request for an actor generates its keys (design Section 7 item 1). A later request returns the same keys.
        secret_service = get_secret_service(data_dir / "auth" / "master.key")
        auth = auth_db.connect_auth_db(auth_db.auth_db_path(data_dir))
        try:
            if departing is None:
                actor_keys.ensure_actor_keys(auth, key_owner, secret_service, now=now())
            pairs = actor_keys.load_actor_keys(auth, key_owner, secret_service)
        finally:
            auth.close()
        return _json(ActorKeysResponse(public_id=raw_id, keys=[ActorKey(alg=p.alg, public_jwk=p.public_jwk, private_jwk=p.private_jwk) for p in pairs]))

    def _decide(store: accounts_store.AccountStore, origin: str, event: InboxEventRequest) -> InboxEventResponse:
        """Apply one event and return the decision (design Section 6.3). The caller commits."""

        def rejected(reason: Any) -> InboxEventResponse:
            return InboxEventResponse(decision="rejected", reply=None, reason=reason)

        conn = store.conn
        # RD30: an actor of this server is a peer as an actor of another server.
        hosts = [_peer_host(conn, address, origin, actor_iri=event.actor.iri) for address in (event.actor.iri, event.actor.inbox, event.actor.shared_inbox) if address is not None]
        if any(host is None for host in hosts):
            return rejected("invalid_actor")
        # Claire makes the final check of the domain blocks (design Section 11 item 1).
        if any(federation_store.blocking_domain(conn, host) is not None for host in hosts if host is not None):
            return rejected("blocked_domain")
        if event.type in ("Accept", "Reject"):
            return _follow_response(store, origin, event)
        reaction = _reaction_kind(event)
        if reaction is not None and _local_object_public_id(origin, event.object_iri) is not None:
            return _reaction(store, origin, event, *reaction)
        kind = _event_kind(event)
        if kind is None:
            return InboxEventResponse(decision="ignored", reply=None, reason="unsupported_type")
        if kind == "delete_actor":
            federation_store.remove_followers_of_actor(conn, event.actor.iri)
            # The federation of shared knowledge (KNOWLEDGE_FEDERATION_DESIGN.md Section 12.1 rule 4, CT7, CT19): the contributions of the account stay with the credit "deleted account", its pending contributions go, its offers without an answer are closed, and its grants end.
            from ..collaboration import contributions
            from ..federation.knowledge import grants as kf_grants
            from ..federation.knowledge import offers as kf_offers

            contributions.remote_actor_deleted(conn, event.actor.iri)
            kf_offers.actor_deleted(conn, event.actor.iri, now=now())
            kf_grants.actor_deleted(conn, event.actor.iri, now=now())
            return InboxEventResponse(decision="accepted", reply=None, reason=None)
        if kind == "update_actor":
            federation_store.update_follower_inboxes(conn, event.actor.iri, inbox_iri=event.actor.inbox, shared_inbox_iri=event.actor.shared_inbox, now=now())
            return InboxEventResponse(decision="accepted", reply=None, reason=None)

        target = _local_actor_public_id(origin, event.object_iri)
        if target is None:
            return rejected("not_an_actor")
        if event.recipient_public_id is not None and event.recipient_public_id != target:
            return rejected("recipient_mismatch")
        if kind == "follow":
            account = _find_actor(store, public_id=target)
            if account is None:
                return rejected("not_an_actor")
            # The follower key is (account_id, actor_iri). A repeated ``Follow`` keeps the row and gives the same decision.
            _follower, new_follower = federation_store.upsert_follower(conn, account_id=account.id, actor_iri=event.actor.iri, inbox_iri=event.actor.inbox, shared_inbox_iri=event.actor.shared_inbox, salt=store.id_salt(), now=now())
            if new_follower:
                notification_events.ap_follower(settings.db_file, account_id=account.id, actor_iri=event.actor.iri)
            return InboxEventResponse(decision="accepted", reply="accept", reason=None)
        # ``Undo(Follow)``: a paused account also loses the follower. Pause keeps followers, but it must not keep one that left.
        account = store.find_by_public_id(int(target))
        if account is None:
            return rejected("not_an_actor")
        federation_store.remove_follower(conn, account.id, event.actor.iri)
        return InboxEventResponse(decision="accepted", reply=None, reason=None)

    def _follow_response(store: accounts_store.AccountStore, origin: str, event: InboxEventRequest) -> InboxEventResponse:
        """``Accept`` or ``Reject`` of a follow of this server (FP4-3). The follow is found by the ID in ``object_iri`` (RT1: the sidecar does not fetch the object). An answer to another activity is ignored."""
        ignored = InboxEventResponse(decision="ignored", reply=None, reason="unsupported_type")
        local = _local_follow(origin, event.object_iri)
        if local is None:
            return ignored
        public_id, following_id = local
        account = store.find_by_public_id(int(public_id))
        if account is None:
            return InboxEventResponse(decision="rejected", reply=None, reason="not_an_actor")
        client = client_db.connect_client_existing(client_db.client_db_path(settings.db_file))
        try:
            found = client_store.following_for_response(client, account.id, following_id, event.actor.iri)
            if found is None:
                return ignored
            answer_state = "accepted" if event.type == "Accept" else "rejected"
            client_store.set_following_state(client, found.id, answer_state, now=now())
            # The fill of recent posts of the actor (DOCUMENT_RENDITIONS_AND_PUBLICATION_DESIGN.md Section 5.13 rule 1): one for each follow that becomes accepted.
            if answer_state == "accepted" and found.state != "accepted":
                fills_store.request_fill(client, account.id, event.actor.iri, now=now())
            client.commit()
            if found.state != answer_state:
                notification_events.ap_follow_answered(settings.db_file, account_id=account.id, actor_iri=event.actor.iri, accepted=event.type == "Accept")
        finally:
            client.close()
        return InboxEventResponse(decision="accepted", reply=None, reason=None)

    def _reaction(store: accounts_store.AccountStore, origin: str, event: InboxEventRequest, kind: Literal["like", "announce"], undo: bool) -> InboxEventResponse:
        """``Like`` or ``Announce`` of an object of an account, or the ``Undo`` of one (Section 6.6 rule 3, unit FP4-8). Claire stores the actor one time for each object and kind; the counter of the object is the number of rows. No item is made.

        A rendition object (DOCUMENT_RENDITIONS_AND_PUBLICATION_DESIGN.md Section 5.6 rule 2) is not a post, but a reaction to it is stored as for each object, with its own number: its document object gives the account and the access rules."""
        missing = InboxEventResponse(decision="ignored", reply=None, reason="no_object")
        public_id = _local_object_public_id(origin, event.object_iri)
        owning = None if public_id is None else federation_store.owning_object(store.conn, public_id)
        obj = None if owning is None else owning[0]
        if obj is None and public_id is not None:
            # An own note of an account (unit FP4-9): the same counters, in the same number space (RT7).
            return _note_reaction(store, public_id, event, kind, undo)
        if obj is None:
            return missing
        if not undo:
            # A new reaction needs an object that is public now: its account is an actor and its theme is public. An ``Undo`` removes the row in all states.
            theme = store.conn.execute("SELECT is_public FROM knowledge_bases WHERE id=?", (obj.kb_id,)).fetchone()
            try:
                owner = store.get(obj.account_id)
            except accounts_store.AccountNotFound:
                owner = None
            if not _is_actor(owner) or theme is None or not theme["is_public"]:
                return missing
        client = client_db.connect_client_existing(client_db.client_db_path(settings.db_file))
        try:
            if undo:
                client_store.remove_object_reaction(client, object_public_id=int(public_id), kind=kind, actor_iri=event.actor.iri)
            else:
                client_store.add_object_reaction(client, object_public_id=int(public_id), account_id=obj.account_id, kind=kind, actor_iri=event.actor.iri, activity_iri=event.event_id, now=now())
            client.commit()
        finally:
            client.close()
        return InboxEventResponse(decision="accepted", reply=None, reason=None)

    def _note_reaction(store: accounts_store.AccountStore, public_id: int, event: InboxEventRequest, kind: Literal["like", "announce"], undo: bool) -> InboxEventResponse:
        """``Like`` or ``Announce`` of an own note, or the ``Undo`` of one (Section 6.6 rule 3, unit FP4-9, decision GD7). A new reaction needs a note that is not deleted and whose account is an actor."""
        client = client_db.connect_client_existing(client_db.client_db_path(settings.db_file))
        try:
            note = notes_store.get_note(client, public_id)
            if note is None or (not undo and (note.deleted_at is not None or note.account_id not in _actor_account_ids(store))):
                return InboxEventResponse(decision="ignored", reply=None, reason="no_object")
            if undo:
                client_store.remove_object_reaction(client, object_public_id=public_id, kind=kind, actor_iri=event.actor.iri)
            else:
                client_store.add_object_reaction(client, object_public_id=public_id, account_id=note.account_id, kind=kind, actor_iri=event.actor.iri, activity_iri=event.event_id, now=now())
            client.commit()
        finally:
            client.close()
        return InboxEventResponse(decision="accepted", reply=None, reason=None)

    def _note_owner(public_id: int | None) -> int | None:
        """The account of an own note that is not deleted, or ``None`` (unit FP4-9)."""
        if public_id is None:
            return None
        client = client_db.connect_client_existing(client_db.client_db_path(settings.db_file), readonly=True)
        try:
            note = notes_store.get_note(client, public_id)
        finally:
            client.close()
        return None if note is None or note.deleted_at is not None else note.account_id

    async def inbox_event(request: Request) -> Response:
        try:
            event = InboxEventRequest.model_validate_json(await request.body())
        except ValidationError:
            return _error(400, "invalid_request")
        origin = _runtime().public_origin

        def _apply() -> InboxEventResponse:
            store = accounts_store.AccountStore(dbm.connect_existing(settings.db_file))
            try:
                # The events of the decision make their notifications after the commit (PWA_NOTIFICATION_DESIGN.md PN2 rule 2).
                with notification_events.after_commit():
                    decision = _decide(store, origin, event)
                    store.conn.commit()
                return decision
            finally:
                store.conn.close()

        decision = await run_in_threadpool(_apply)
        _log.info("inbox event %s: %s%s", event.type, decision.decision, "" if decision.reason is None else f" ({decision.reason})")
        return _json(decision)

    def _too_large(obj: TimelineObject) -> bool:
        return len(obj.content_html.encode("utf-8")) > client_store.TIMELINE_CONTENT_MAX_BYTES or len(obj.attachments) > client_store.TIMELINE_MAX_ATTACHMENTS

    def _own_object_author(conn: Any, origin: str, iri: str) -> str | None:
        """The actor of the account of an object of this server (a published document object, a rendition object, or a note that is not deleted), or ``None``."""
        public_id = _local_object_public_id(origin, iri)
        if public_id is None:
            return None
        owning = federation_store.owning_object(conn, public_id)
        account_id = int(owning[0].account_id) if owning is not None else _note_owner(public_id)
        if account_id is None:
            return None
        row = conn.execute("SELECT public_id FROM accounts WHERE id=? AND status='active' AND username IS NOT NULL", (account_id,)).fetchone()
        return None if row is None else f"{origin}/ap/actors/{ids.format_public_id(int(row[0]))}"

    def _object_refusal(conn: Any, origin: str, obj: TimelineObject) -> Literal["invalid_object", "blocked_domain", "too_large"] | None:
        """The reason why an object cannot be an item, or ``None``. The domain block applies to the author and to the host of the object IRI (Section 6.2 rule 3). Since RD30 an object of this server can be an item, as an object of another server, when it exists here and its author is the author that the object names: no other server can give an object of this server."""
        if obj.object_iri.startswith(f"{origin}/"):
            return "invalid_object" if _own_object_author(conn, origin, obj.object_iri) != obj.author.iri else ("too_large" if _too_large(obj) else None)
        hosts = [_remote_host(obj.object_iri, origin), _remote_host(obj.author.iri, origin)]
        if any(host is None for host in hosts):
            return "invalid_object"
        if any(federation_store.blocking_domain(conn, host) is not None for host in hosts if host is not None):
            return "blocked_domain"
        return "too_large" if _too_large(obj) else None

    def _own_object(store: accounts_store.AccountStore, origin: str, account_id: int, iri: str) -> bool:
        """True when ``iri`` is a published object of the account (``{origin}/ap/objects/{public_id}``)."""
        prefix = f"{origin}/ap/objects/"
        if not iri.startswith(prefix):
            return False
        try:
            found = federation_store.get_object(store.conn, ids.parse_public_id(iri[len(prefix) :]))
        except ValueError:
            return False
        if found is None:
            # An own note of the account (unit FP4-9).
            return _note_owner(_local_object_public_id(origin, iri)) == account_id
        return found.account_id == account_id

    def _mentioned_accounts(store: accounts_store.AccountStore, origin: str, event: TimelineEventRequest, obj: TimelineObject, actor_ids: list[int]) -> set[int]:
        """The accounts that are actors and that the object mentions, addresses, or answers (a reply to a published object of the account)."""
        found: set[int] = set()
        for iri in event.mentioned:
            public_id = _local_actor_public_id(origin, iri)
            account = None if public_id is None else store.find_by_public_id(int(public_id))
            if account is not None:
                found.add(account.id)
        parent_id = _local_object_public_id(origin, obj.in_reply_to)
        parent = None if parent_id is None else federation_store.get_object(store.conn, parent_id)
        if parent is not None:
            found.add(parent.account_id)
        elif (note_owner := _note_owner(parent_id)) is not None:
            # A reply to an own note of an account (unit FP4-9).
            found.add(note_owner)
        return found & set(actor_ids)

    def _private_notices(store: accounts_store.AccountStore, client: Any, origin: str, event: TimelineEventRequest) -> list[TimelineNotice]:
        """The automatic notices for a non-public ``Create`` that mentions or addresses accounts (Section 6.2 rule 4). Claire stores the sender and the time only.

        Rules that stop a loop of automatic systems: no notice to a ``Service`` or an ``Application`` (or an actor of an unknown type), and no notice for a reply to an automatic notice of this server. A blocked domain does not come here: the caller refused it. One notice at most for each sender and account in ``auto_reply_hours``.
        """
        if not event.mentioned or event.actor_type not in ("Person", "Group", "Organization"):
            return []
        if event.in_reply_to is not None and event.in_reply_to.startswith(f"{origin}/ap/actors/") and AUTO_REPLY_FRAGMENT in event.in_reply_to:
            return []
        window = float(client_store.get_reading_settings(store.conn)["auto_reply_hours"]) * 3600.0
        actor_ids = set(_actor_account_ids(store))
        notices: list[TimelineNotice] = []
        told: list[int] = []
        for iri in dict.fromkeys(event.mentioned):
            public_id = _local_actor_public_id(origin, iri)
            account = None if public_id is None else store.find_by_public_id(int(public_id))
            if account is None or account.id not in actor_ids:
                continue
            if client_store.record_private_message(client, account_id=account.id, sender_iri=event.actor.iri, now=now(), window_seconds=window):
                locale = resolve_bot_locale(owner_locale=account.locale, language_code=None, settings_lang=getattr(settings, "ui_lang", None))
                text = i18n_catalog.t("federation.auto_reply.text", locale=locale)
                notices.append(TimelineNotice(actor_public_id=str(account.public_id), content=f"<p>{_html.escape(text)}</p>", language=locale))
                told.append(account.id)
        if told:
            notification_events.ap_private_message(settings.db_file, account_ids=told, actor_iri=event.actor.iri)
        return notices

    def _decide_timeline(store: accounts_store.AccountStore, client: Any, origin: str, event: TimelineEventRequest) -> TimelineEventResponse:
        """Apply one timeline event to ``client.db`` and return the decision (Sections 6.1 to 6.5). The caller commits ``client``."""

        def answer(decision: Any, reason: Any, items: int = 0, notices: list[TimelineNotice] | None = None) -> TimelineEventResponse:
            return TimelineEventResponse(decision=decision, reason=reason, items=items, notices=notices or [])

        conn = store.conn
        hosts = [_peer_host(conn, address, origin, actor_iri=event.actor.iri) for address in (event.actor.iri, event.actor.inbox, event.actor.shared_inbox) if address is not None]
        if any(host is None for host in hosts):
            return answer("rejected", "invalid_actor")
        if any(federation_store.blocking_domain(conn, host) is not None for host in hosts if host is not None):
            return answer("rejected", "blocked_domain")
        actor_iri = event.actor.iri
        if event.type == "Delete":
            # Fedify verified that the activity comes from ``actor``. Only the author deletes an item.
            removed = client_store.delete_timeline_items(client, event.object_iri, actor_iri)
            # Quotes (unit FP4-9Q, RT9). The author of a quoted object deleted its QuoteAuthorization: the quote of each own note with that authorization becomes ``revoked``. This is the only way to that state: Claire never reads it from a lookup.
            revoked = quotes_store.revoke_note_quotes(client, event.object_iri, actor_iri, now=now())
            # The quoting actor deleted its object: its requests for own objects go.
            quotes_store.remove_requests_of_object(client, event.object_iri, actor_iri)
            # The author deleted a post that is an opinion of an own object (DOCUMENT_RENDITIONS_AND_PUBLICATION_DESIGN.md Section 5.11 C4).
            removed += opinions_store.delete_opinion(client, event.object_iri, actor_iri)
            return answer("accepted", None, removed) if removed or revoked else answer("ignored", "no_item")
        if event.type == "Undo":
            removed = client_store.delete_announced_items(client, event.object_iri, actor_iri)
            return answer("accepted", None, removed) if removed else answer("ignored", "no_item")
        if event.public is not True:
            # Claire has no visibility model for an object that is not public (Section 6.2 rule 2). An item that the author changed to not public goes.
            removed = client_store.delete_timeline_items(client, event.object_iri, actor_iri) if event.type == "Update" else 0
            return answer("ignored", "not_public", removed, _private_notices(store, client, origin, event) if event.type == "Create" else None)
        obj = event.object
        if obj is None or obj.object_iri != event.object_iri:
            return answer("rejected", "invalid_object")
        if event.type != "Announce" and obj.author.iri != actor_iri:
            return answer("rejected", "invalid_object")
        refusal = _object_refusal(conn, origin, obj)
        if refusal is not None:
            return answer("rejected", refusal)
        item = obj.model_dump(mode="json")
        if event.type == "Update":
            changed = client_store.update_timeline_items(client, item=item, now=now())
            return answer("accepted", None, changed) if changed else answer("ignored", "no_item")

        # ``Create`` and ``Announce``: one item for each account that is an actor and follows the author or the announcer (Section 6.4).
        actor_ids = _actor_account_ids(store)
        followers = set(client_store.accounts_following_actor(client, actor_iri, actor_ids))
        # Section 6.6 rule 1: a public ``Create`` that mentions an account, or that is a reply to an object of an account, is an item for that account, also when the author is not followed.
        mentioned = _mentioned_accounts(store, origin, event, obj, actor_ids) if event.type == "Create" else set()
        accounts = sorted(followers | mentioned)
        if not accounts:
            return answer("ignored", "not_followed")
        rate = int(client_store.get_reading_settings(conn)["mention_rate_per_hour"])
        author_host = _peer_host(conn, obj.author.iri, origin)
        announced_by = None
        if event.type == "Announce":
            announced_by = (event.announced_by or TimelineActor(iri=actor_iri, name=None, handle=None)).model_dump(mode="json")
            announced_by["iri"] = actor_iri
        # The parent chain stops at the first parent that cannot be an item: a later parent has no link to the thread that the owner can read.
        parents: list[TimelineObject] = []
        expected = obj.in_reply_to
        for parent in event.parents:
            if parent.object_iri != expected or _object_refusal(conn, origin, parent) is not None:
                break
            parents.append(parent)
            expected = parent.in_reply_to
        made = 0
        limited = 0
        new_mentions: list[int] = []
        for account_id in accounts:
            mention = account_id in mentioned
            if mention and account_id not in followers:
                # Section 6.6 rule 6: the limit applies to the items that only the mention brings, for each remote domain and account.
                recent = client_store.recent_mention_authors(client, account_id, since=now() - MENTION_RATE_WINDOW_SECONDS)
                if sum(1 for author in recent if _peer_host(conn, author, origin) == author_host) >= rate:
                    limited += 1
                    continue
            in_timeline = True
            if obj.in_reply_to is not None and not mention:
                # Timeline rule (Section 6.5 rule 3): the parent author is the author, the account itself, or a followed actor.
                parent_author = parents[0].author.iri if parents else client_store.timeline_item_author(client, account_id, obj.in_reply_to)
                own_actor = f"{origin}/ap/actors/{store.get(account_id).public_id}"
                in_timeline = _own_object(store, origin, account_id, obj.in_reply_to) or (parent_author is not None and (parent_author in (obj.author.iri, own_actor) or client_store.is_following(client, account_id, parent_author)))
            new_item = client_store.upsert_timeline_item(client, account_id=account_id, item=item, announced_by=announced_by, in_timeline=in_timeline, now=now(), mention=mention)
            made += new_item
            if new_item and mention:
                new_mentions.append(account_id)
            for parent in parents:
                client_store.upsert_timeline_item(client, account_id=account_id, item=parent.model_dump(mode="json"), announced_by=None, in_timeline=False, now=now())
        if new_mentions:
            # The label is the handle of the author when the object gives it: an actor of this server (RD30) has a number in its address, not a name.
            notification_events.ap_mention(settings.db_file, account_ids=new_mentions, actor_iri=obj.author.iri, actor_label=(obj.author.handle or "").lstrip("@") or None)
        if limited == len(accounts):
            return answer("ignored", "rate_limited")
        return answer("accepted", None, made)

    async def timeline_event(request: Request) -> Response:
        try:
            event = TimelineEventRequest.model_validate_json(await request.body())
        except ValidationError:
            return _error(400, "invalid_request")
        origin = _runtime().public_origin

        def _apply() -> TimelineEventResponse:
            store = _open_store()
            client = _client()
            try:
                with notification_events.after_commit():
                    decision = _decide_timeline(store, client, origin, event)
                    client.commit()
                return decision
            finally:
                client.close()
                store.conn.close()

        decision = await run_in_threadpool(_apply)
        _log.info("timeline event %s: %s%s, %d item(s), %d notice(s)", event.type, decision.decision, "" if decision.reason is None else f" ({decision.reason})", decision.items, len(decision.notices))
        if decision.items and (event.type in ("Delete", "Undo") or decision.reason == "not_public"):
            # Section 9.1 rule 6: a deleted item removes its media entries when no other item refers to the URL.
            try:
                removed = await media_proxy.cleanup_proxy(settings).remove_orphans()
                if removed:
                    _log.info("timeline event %s: %d media entry(ies) removed", event.type, removed)
            except Exception as exc:  # noqa: BLE001
                _log.warning("media cleanup after a timeline event failed: %s", type(exc).__name__)
        return _json(decision)

    def timeline_interest(request: Request) -> Response:
        actor_iri = request.query_params.get("actor", "")
        if not actor_iri or len(actor_iri) > _MAX_IRI_LENGTH:
            return _error(400, "invalid_request")
        store = _open_store()
        try:
            actor_ids = _actor_account_ids(store)
            reading = client_store.get_reading_settings(store.conn)
        finally:
            store.conn.close()
        client = _client()
        try:
            followed = bool(client_store.accounts_following_actor(client, actor_iri, actor_ids))
        finally:
            client.close()
        return _json(TimelineInterestResponse(followed=followed, context_fetch=reading["context_fetch"], context_depth=int(reading["context_depth"])))

    def followers(request: Request) -> Response:
        try:
            query = parse_collection_query(request.query_params)
        except HTTPException:
            return _error(400, "invalid_request")
        store = _open_store()
        try:
            account = _find_actor(store, public_id=request.path_params["public_id"]) or _find_departing(store, request.path_params["public_id"])
            if account is None:
                return _not_found()
            total = federation_store.count_followers(store.conn, account.id)
            rows = federation_store.list_followers(store.conn, account.id, limit=query.limit, max_id=query.max_id, min_id=query.min_id, since_id=query.since_id)
        finally:
            store.conn.close()
        return _json(
            FollowersResponse(
                public_id=str(account.public_id),
                total=total,
                items=[FollowerItem(id=str(row.public_id), actor_iri=row.actor_iri, inbox_iri=row.inbox_iri, shared_inbox_iri=row.shared_inbox_iri) for row in rows],
                next_max_id=str(rows[-1].public_id) if len(rows) == query.limit else None,
            )
        )

    def _actor_account_ids(store: accounts_store.AccountStore) -> list[int]:
        return [int(row[0]) for row in store.conn.execute("SELECT id FROM accounts WHERE status='active' AND username IS NOT NULL")]

    def outbox_tasks(request: Request) -> Response:
        raw_limit = request.query_params.get("limit", str(OUTBOX_TASKS_DEFAULT))
        if not (raw_limit.isascii() and raw_limit.isdigit() and 1 <= int(raw_limit) <= OUTBOX_TASKS_MAX):
            return _error(400, "invalid_request")
        store = accounts_store.AccountStore(dbm.connect_existing(settings.db_file))
        try:
            actor_ids = _actor_account_ids(store)
            leased = federation_store.lease_outbox_tasks(store.conn, account_ids=actor_ids, actor_account_ids=actor_ids, now=now(), lease_seconds=OUTBOX_LEASE_SECONDS, limit=int(raw_limit), include_delete_actor=True)
            items = []
            for task in leased:
                try:
                    actor_public_id = str(store.get(task.account_id).public_id)
                except accounts_store.AccountNotFound:
                    # The account row is gone (the USERNAME of a purged account was released). No actor can send this task.
                    federation_store.ack_outbox_task(store.conn, task.id, result="rejected", error_code="account_missing", now=now())
                    continue
                items.append(OutboxTaskItem(id=task.id, kind=task.kind, actor_public_id=actor_public_id, object_public_id=None if task.object_public_id is None else str(task.object_public_id), created_at=task.created_at, lease_until=float(task.lease_until or 0.0)))
            store.conn.commit()
        finally:
            store.conn.close()
        return _json(OutboxTasksResponse(tasks=items))

    async def outbox_task_ack(request: Request) -> Response:
        raw_id = request.path_params["task_id"]
        try:
            body = OutboxAckRequest.model_validate_json(await request.body())
        except ValidationError:
            return _error(400, "invalid_request")
        if not (raw_id.isascii() and raw_id.isdigit() and raw_id[0] != "0" and len(raw_id) <= 18):
            return _not_found()

        def _apply() -> federation_store.OutboxTask | None:
            conn = dbm.connect_existing(settings.db_file)
            try:
                task = federation_store.ack_outbox_task(conn, int(raw_id), result=body.result, error_code=body.error_code, now=now())
                finished = task is not None and task.kind == "delete_actor" and task.status != "pending"
                if finished:
                    # The last task of a purged actor has a result: its followers go now (design Section 8.1).
                    federation_store.finish_delete_actor(conn, task.account_id)
                conn.commit()
            finally:
                conn.close()
            if finished:
                # The queue of the sidecar has the signed deliveries. Claire needs the keys no more.
                auth = auth_db.connect_auth_db(auth_db.auth_db_path(data_dir))
                try:
                    actor_keys.delete_actor_keys(auth, task.account_id)
                finally:
                    auth.close()
            return task

        task = await run_in_threadpool(_apply)
        if task is None:
            return _not_found()
        _log.info("outbox task %s (%s): %s%s", task.id, task.kind, task.status, "" if task.error_code is None else f" ({task.error_code})")
        return _json(OutboxAckResponse(id=task.id, status=task.status))

    def _client() -> Any:
        return client_db.connect_client_existing(client_db.client_db_path(settings.db_file))

    def client_tasks(request: Request) -> Response:
        raw_limit = request.query_params.get("limit", str(OUTBOX_TASKS_DEFAULT))
        if not (raw_limit.isascii() and raw_limit.isdigit() and 1 <= int(raw_limit) <= OUTBOX_TASKS_MAX):
            return _error(400, "invalid_request")
        store = _open_store()
        try:
            actor_ids = _actor_account_ids(store)
            public_ids = {a: str(store.get(a).public_id) for a in actor_ids}
        finally:
            store.conn.close()
        client = _client()
        try:
            # Only a task of an account that is an actor now is given (Section 5.1 rule 1). The task of a paused account waits.
            leased = client_store.lease_client_tasks(client, account_ids=actor_ids, now=now(), lease_seconds=OUTBOX_LEASE_SECONDS, limit=int(raw_limit))
            items = []
            for task in leased:
                target = task.target_iri
                if task.kind == "follow":
                    following = client_store.get_following(client, task.account_id, task.ref_id) if task.ref_id is not None else None
                    if following is None:
                        # The owner removed the follow before the sidecar took the task.
                        client_store.ack_client_task(client, task.id, result="rejected", error_code="following_removed", now=now())
                        continue
                    target = following.actor_iri or following.request
                if not target:
                    client_store.ack_client_task(client, task.id, result="rejected", error_code="no_target", now=now())
                    continue
                items.append(ClientTaskItem(id=task.id, kind=task.kind, actor_public_id=public_ids[task.account_id], target=target, following_id=None if task.ref_id is None else str(task.ref_id), created_at=task.created_at, lease_until=float(task.lease_until or 0.0)))
            client.commit()
        finally:
            client.close()
        return _json(ClientTasksResponse(tasks=items))

    async def client_task_ack(request: Request) -> Response:
        raw_id = request.path_params["task_id"]
        try:
            body = ClientAckRequest.model_validate_json(await request.body())
        except ValidationError:
            return _error(400, "invalid_request")
        if not (raw_id.isascii() and raw_id.isdigit() and raw_id[0] != "0" and len(raw_id) <= 18):
            return _not_found()
        origin = _runtime().public_origin

        def _apply() -> Any:
            conn = dbm.connect_existing(settings.db_file, readonly=True)
            try:
                blocked = body.actor is not None and any(
                    (host := _peer_host(conn, address, origin, actor_iri=body.actor.iri)) is None or federation_store.blocking_domain(conn, host) is not None
                    for address in (body.actor.iri, body.actor.inbox, body.actor.shared_inbox) if address is not None
                )
            finally:
                conn.close()
            client = _client()
            try:
                task = client_store.get_client_task(client, int(raw_id))
                if task is None:
                    return None
                result, code = body.result, body.error_code
                if task.kind == "follow" and task.status == "pending" and task.ref_id is not None:
                    if result == "handed_off" and body.actor is not None and not blocked:
                        a = body.actor
                        if not client_store.resolve_following(client, task.ref_id, actor_iri=a.iri, actor_type=a.type, inbox_iri=a.inbox, shared_inbox_iri=a.shared_inbox, display_name=a.name, now=now()):
                            result, code = "rejected", "already_following"
                    elif result == "handed_off":
                        # Claire makes the final check of the domain blocks; a follow without a resolved actor is not valid.
                        result, code = "rejected", "blocked_domain" if blocked else "no_actor"
                    if result == "rejected":
                        client_store.set_following_state(client, task.ref_id, "rejected", now=now())
                task = client_store.ack_client_task(client, task.id, result=result, error_code=code, now=now())
                client.commit()
                return task
            finally:
                client.close()

        task = await run_in_threadpool(_apply)
        if task is None:
            return _not_found()
        _log.info("client task %s (%s): %s%s", task.id, task.kind, task.status, "" if task.error_code is None else f" ({task.error_code})")
        return _json(OutboxAckResponse(id=task.id, status=task.status))

    # --- Thread fetch (contract 1.5, Section 6.5 rules 4 to 6, unit FP4-6) ---

    def context_tasks(request: Request) -> Response:
        raw_limit = request.query_params.get("limit", str(CONTEXT_TASKS_DEFAULT))
        if not (raw_limit.isascii() and raw_limit.isdigit() and 1 <= int(raw_limit) <= OUTBOX_TASKS_MAX):
            return _error(400, "invalid_request")
        store = _open_store()
        try:
            actor_ids = _actor_account_ids(store)
            public_ids = {a: str(store.get(a).public_id) for a in actor_ids}
            reading = client_store.get_reading_settings(store.conn)
        finally:
            store.conn.close()
        max_items, max_depth = int(reading["context_max_items"]), int(reading["context_depth"])
        client = _client()
        try:
            leased = client_store.lease_client_tasks(client, account_ids=actor_ids, now=now(), lease_seconds=OUTBOX_LEASE_SECONDS, limit=int(raw_limit), only_kinds=(client_store.THREAD_FETCH_KIND,))
            items = []
            for task in leased:
                # Rule 6: the admin can set the fetch to off after the owner made the task.
                code = "context_fetch_off" if reading["context_fetch"] != "on" or max_items < 1 else None if task.target_iri else "no_target"
                if code is not None:
                    client_store.ack_client_task(client, task.id, result="rejected", error_code=code, now=now())
                    continue
                items.append(ContextTaskItem(id=task.id, actor_public_id=public_ids[task.account_id], object_iri=str(task.target_iri), max_items=max_items, max_depth=max_depth, created_at=task.created_at, lease_until=float(task.lease_until or 0.0)))
            client.commit()
        finally:
            client.close()
        return _json(ContextTasksResponse(tasks=items))

    async def context_objects(request: Request) -> Response:
        try:
            body = ContextObjectsRequest.model_validate_json(await request.body())
        except ValidationError:
            return _error(400, "invalid_request")
        origin = _runtime().public_origin

        def _apply() -> ContextObjectsResponse | None:
            def refuse(reason: Any) -> ContextObjectsResponse:
                return ContextObjectsResponse(decision="rejected", reason=reason, stored=0, refused=len(body.objects))

            store = _open_store()
            client = _client()
            try:
                task = client_store.get_client_task(client, body.task_id)
                if task is None or task.kind != client_store.THREAD_FETCH_KIND or task.status != "pending" or task.ref_id is None:
                    return None
                if task.account_id not in _actor_account_ids(store):
                    return refuse("not_an_actor")
                reading = client_store.get_reading_settings(store.conn)
                if reading["context_fetch"] != "on":
                    return refuse("context_fetch_off")
                # The same rules as for a ``Create`` (Section 6.2): a permitted remote address, no blocked domain for the author and the object host, and the size limits. The limit of one task applies to each request.
                permitted = [obj.model_dump(mode="json") for obj in body.objects[: int(reading["context_max_items"])] if _object_refusal(store.conn, origin, obj) is None]
                stored, linked = client_store.store_context_objects(client, account_id=task.account_id, item_id=task.ref_id, objects=permitted, now=now())
                client.commit()
                return ContextObjectsResponse(decision="accepted", reason=None, stored=stored, refused=len(body.objects) - linked)
            finally:
                client.close()
                store.conn.close()

        result = await run_in_threadpool(_apply)
        if result is None:
            return _not_found()
        _log.info("context objects of task %s: %s%s, %d stored, %d refused", body.task_id, result.decision, "" if result.reason is None else f" ({result.reason})", result.stored, result.refused)
        return _json(result)

    def following(request: Request) -> Response:
        store = _open_store()
        try:
            account = _find_actor(store, public_id=request.path_params["public_id"])
        finally:
            store.conn.close()
        if account is None:
            return _not_found()
        client = _client()
        try:
            total = client_store.count_accepted_following(client, account.id)
        finally:
            client.close()
        return _json(FollowingResponse(public_id=str(account.public_id), total=total))

    def published_object(request: Request) -> Response:
        raw_id = request.path_params["object_public_id"]
        try:
            public_id = ids.parse_public_id(raw_id)
        except ValueError:
            return _not_found()
        origin = _runtime().public_origin
        store = _open_store()
        try:
            conn = store.conn
            tombstone = federation_store.get_tombstone(conn, public_id)
            if tombstone is not None and tombstone.kind == "object":
                return JSONResponse({"error": "gone", "gone_at": tombstone.gone_at}, status_code=410, headers=_NO_STORE)
            owning = federation_store.owning_object(conn, public_id)
            if owning is None:
                return _not_found()
            obj, rendition_object = owning
            account = store.get(obj.account_id)
            # The object is public while its account is an actor, its theme is public, and its document is not hidden (decision GD8). A rendition object is public with its document object.
            theme = conn.execute("SELECT is_public FROM knowledge_bases WHERE id=?", (obj.kb_id,)).fetchone()
            document = dbm.get_document_row(conn, obj.document_id, kb_id=obj.kb_id)
            if not _is_actor(account) or theme is None or not theme["is_public"] or document is None or document["hidden"]:
                return _not_found()
            template = PRIMARY_RENDITION_TEMPLATE if rendition_object is None else rendition_object.template
            rendition = renditions_store.get_rendition(conn, obj.document_id, obj.kb_id, template)
            if rendition_object is not None and rendition is None:
                return _not_found()
            # The ``url`` of the object and the link of its content are the permalink of the document (PERMALINK_UNIFICATION_DESIGN.md U11); a rendition object has the sub-path of its rendition (DOCUMENT_RENDITIONS_AND_PUBLICATION_DESIGN.md Section 5.8).
            address_token = permalinks.token_of(conn, "document", obj.document_id)
            share_url = None if address_token is None else permalinks.permalink_url(origin, address_token) + ("" if rendition_object is None else f"/renditions/{template}")
            html = _published_object_content(dbm.latest_extraction_summary(conn, obj.document_id, kb_id=obj.kb_id), document["title"] or "", share_url)
            body = rendition_body_text(document, rendition)
            client = _client()
            try:
                reactions = client_store.count_object_reactions(client, [public_id])[public_id]
            finally:
                client.close()
            source_key = document["source_key"] if "source_key" in document.keys() else None
            marks = plus_ones_store.counts(conn, public_id)
            record = ObjectRecord(
                public_id=str(public_id),
                actor_public_id=str(account.public_id),
                type="Article",
                name=document["title"] or "",
                content=nh3.clean(html),
                url=share_url,
                published_at=obj.published_at if rendition_object is None else rendition_object.created_at,
                updated_at=obj.updated_at if rendition_object is None else rendition_object.updated_at,
                likes=reactions["like"],
                shares=reactions["announce"],
                rendition_of=None if rendition_object is None else str(obj.public_id),
                template=None if rendition is None else rendition.template,
                template_version=None if rendition is None else rendition.template_version,
                body_hash=None if not body else hashlib.sha256(body.encode("utf-8")).hexdigest(),
                source_key=source_key,
                source_id_version=document["source_id_version"] if source_key else None,
                plus_ones=marks["total"],
                plus_one_servers=marks["servers"],
                plus_one_score=marks["score"],
            )
        finally:
            store.conn.close()
        return _json(record)

    def actor_outbox(request: Request) -> Response:
        try:
            query = parse_collection_query(request.query_params)
        except HTTPException:
            return _error(400, "invalid_request")
        store = _open_store()
        try:
            account = _find_actor(store, public_id=request.path_params["public_id"])
            if account is None:
                return _not_found()
            total = federation_store.count_objects(store.conn, account.id, public_themes_only=True)
            rows = federation_store.list_objects(store.conn, account.id, limit=query.limit, max_id=query.max_id, min_id=query.min_id, since_id=query.since_id, public_themes_only=True)
        finally:
            store.conn.close()
        # The outbox collection is the union of the published documents and the own notes, in the sequence of the object number (RT7, unit FP4-9).
        client = _client()
        try:
            total += notes_store.count_notes(client, account.id)
            notes = notes_store.list_notes(client, account.id, limit=query.limit, max_id=query.max_id, min_id=query.min_id, since_id=query.since_id)
        finally:
            client.close()
        # With min_id the two pages are the oldest objects above the cursor; if not, they are the newest ones.
        merged = sorted([*rows, *notes], key=lambda row: row.public_id, reverse=True)
        rows = merged[-query.limit :] if query.min_id is not None else merged[: query.limit]
        return _json(
            ActorOutboxResponse(
                public_id=str(account.public_id),
                total=total,
                items=[OutboxItem(id=str(row.public_id), published_at=row.published_at, updated_at=row.updated_at) for row in rows],
                next_max_id=str(rows[-1].public_id) if len(rows) == query.limit else None,
            )
        )

    def domain_blocks(request: Request) -> Response:
        store = _open_store()
        try:
            etag, domains = _domain_blocks(store.conn)
        finally:
            store.conn.close()
        headers = {**_NO_STORE, "ETag": etag}
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers=headers)
        return JSONResponse(DomainBlocksResponse(etag=etag, domains=domains).model_dump(mode="json"), headers=headers)

    return [
        Route("/config", config, methods=["GET"]),
        Route("/heartbeat", heartbeat, methods=["POST"]),
        Route("/actors/by-username/{username}", actor_by_username, methods=["GET"]),
        Route("/actors/{public_id}", actor, methods=["GET"]),
        Route("/actors/{public_id}/keys", keys, methods=["GET"]),
        Route("/actors/{public_id}/followers", followers, methods=["GET"]),
        Route("/actors/{public_id}/following", following, methods=["GET"]),
        Route("/client-tasks", client_tasks, methods=["GET"]),
        Route("/client-tasks/{task_id}/ack", client_task_ack, methods=["POST"]),
        Route("/context-tasks", context_tasks, methods=["GET"]),
        Route("/context-objects", context_objects, methods=["POST"]),
        Route("/inbox-events", inbox_event, methods=["POST"]),
        Route("/timeline-events", timeline_event, methods=["POST"]),
        Route("/timeline-interest", timeline_interest, methods=["GET"]),
        Route("/domain-blocks", domain_blocks, methods=["GET"]),
        Route("/actors/{public_id}/outbox", actor_outbox, methods=["GET"]),
        Route("/outbox-tasks", outbox_tasks, methods=["GET"]),
        Route("/outbox-tasks/{task_id}/ack", outbox_task_ack, methods=["POST"]),
        Route("/objects/{object_public_id}", published_object, methods=["GET"]),
        # Outbound interactions (contract 1.6, unit FP4-9): own notes and the tasks of notes, favourites, and boosts.
        *note_internal_routes(settings, now=now, origin=lambda: _runtime().public_origin, open_store=_open_store, open_client=_client, actor_account_ids=_actor_account_ids),
        *collection_internal_routes(origin=lambda: _runtime().public_origin, open_store=_open_store, open_client=_client, actor_account_ids=_actor_account_ids, open_writer=lambda: dbm.connect_existing(settings.db_file)),
        *opinion_internal_routes(origin=lambda: _runtime().public_origin, now=now, open_conn=lambda: dbm.connect_existing(settings.db_file), open_client=lambda: client_db.connect_client_existing(client_db.client_db_path(settings.db_file)), object_refusal=_object_refusal, own_object_public_id=lambda iri: _local_object_public_id(_runtime().public_origin, iri)),
        # Fills of recent posts after a follow (contract 1.24, DOCUMENT_RENDITIONS_AND_PUBLICATION_DESIGN.md Section 5.13).
        *fill_internal_routes(origin=lambda: _runtime().public_origin, now=now, open_conn=lambda: dbm.connect_existing(settings.db_file), open_client=lambda: client_db.connect_client_existing(client_db.client_db_path(settings.db_file)), object_refusal=_object_refusal, actor_public_ids=lambda conn: {int(row[0]): str(row[1]) for row in conn.execute("SELECT id, public_id FROM accounts WHERE status='active' AND username IS NOT NULL")}),
        # Quotes (contract 1.7, unit FP4-9Q): the events of the quote protocol and the record of an authorization.
        *quote_internal_routes(settings, now=now, origin=lambda: _runtime().public_origin, open_store=_open_store, open_client=_client, actor_account_ids=_actor_account_ids),
        # Federation of shared knowledge (unit KF1): the inbox journal, the signed reads of the collections, and the reads of known servers. No shape that existed changed.
        *knowledge_internal_routes(settings, now=now, origin=lambda: _runtime().public_origin),
    ]
