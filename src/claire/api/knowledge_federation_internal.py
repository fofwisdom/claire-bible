"""Internal federation API of the federation of shared knowledge (KNOWLEDGE_FEDERATION_DESIGN.md Sections 6.2, 6.3, 13, and 14; unit KF1).

``federation_routes.federation_internal_routes`` adds these routes to its table. The sidecar is the only caller: it verifies the signature of each request of another server, and Claire applies the controls and stores the data.

- ``POST /knowledge-activities``: one verified inbound activity of the knowledge federation. Claire writes it to the inbox journal and processes it (``knowledge_federation.journal``).
- ``GET /knowledge-collections/{name}``: one read of a collection of shared knowledge (``kb`` or ``themes``) by another server. ``signer`` is the actor whose signature the sidecar verified. A request that is not permitted gets 404 (``knowledge_federation.access``).
- ``GET /knowledge-server-reads``: the known servers that are due for a read of NodeInfo and of the server actor. The request gives a lease on each one.
- ``POST /knowledge-server-reads``: the result of one read.

The models are sealed, as in ``federation_routes``. No shape that existed changed, so the contract version did not change.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any, Literal

from pydantic import Field, ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import BaseRoute, Route

from .. import ids
from ..knowledge_federation import access, journal, known_servers
from ..store import db as dbm
from ..store import themes as themes_store
from .federation_routes import _MAX_IRI_LENGTH, _error, _json, _not_found, _Sealed

__all__ = [
    "COLLECTIONS",
    "KnowledgeActivityRequest",
    "KnowledgeActivityResponse",
    "KnowledgeCollectionResponse",
    "ServerReadResult",
    "ServerReadsResponse",
    "knowledge_internal_routes",
]

_log = logging.getLogger("claire.knowledge_federation.internal")

# The collections of shared knowledge of the public listener: ``GET /kb`` and ``GET /themes``.
COLLECTIONS: tuple[str, ...] = ("kb", "themes")
SERVER_READS_DEFAULT = 5
SERVER_READS_MAX = 20


class KnowledgeActivityRequest(_Sealed):
    """One activity that the sidecar verified. ``activity`` is the JSON of the activity as it came: Claire reads the compact names of the wire contract, so the sidecar must not compact it again. ``signer`` is the actor whose signature is valid. ``recipient`` is the actor of the inbox that got the activity, or ``None`` for the shared inbox."""

    activity: dict[str, Any]
    signer: str = Field(min_length=1, max_length=_MAX_IRI_LENGTH)
    verified_by: Literal["http-signature", "ld-signature", "object-integrity-proof"]
    recipient: str | None = Field(min_length=1, max_length=_MAX_IRI_LENGTH)


class KnowledgeActivityResponse(_Sealed):
    """``decision``: ``rejected`` (no journal row), ``duplicate`` (the row exists; ``reason`` is its state), or the state of the row: ``processed``, ``failed``, ``refused`` (``reason`` is the error code). ``journal_id`` is ``None`` only for ``rejected``."""

    decision: Literal["processed", "failed", "refused", "duplicate", "rejected"]
    reason: str | None
    journal_id: int | None


class CollectionItem(_Sealed):
    number: str
    label: str
    description: str
    knowledge_type: Literal["real", "fictional"]
    state: str
    url: str


class KnowledgeCollectionResponse(_Sealed):
    """``server`` is the domain of the server that signed the request. ``items`` are newest first."""

    collection: Literal["kb", "themes"]
    server: str
    items: list[CollectionItem]


class ServerReadItem(_Sealed):
    domain: str


class ServerReadsResponse(_Sealed):
    servers: list[ServerReadItem]


class ServerReadResult(_Sealed):
    """The result of one read of a known server. ``ok`` true: ``server_actor``, ``software_name``, and ``software_version`` are necessary, and ``error`` is ``None``. ``ok`` false: ``error`` is a code, and the other fields are ``None``. ``shared_kb_count`` is ``None`` when the read did not give the number."""

    domain: str = Field(min_length=1, max_length=253)
    ok: bool
    server_actor: str | None = Field(min_length=1, max_length=_MAX_IRI_LENGTH)
    software_name: str | None = Field(min_length=1, max_length=100)
    software_version: str | None = Field(min_length=1, max_length=100)
    shared_kb_count: int | None = Field(ge=0)
    error: str | None = Field(pattern=r"^[a-z0-9_]{1,64}$")


class ServerReadRecorded(_Sealed):
    domain: str
    recorded: bool


def _valid_read_result(result: ServerReadResult, origin: str) -> bool:
    if result.ok:
        if result.server_actor is None or result.software_name is None or result.software_version is None or result.error is not None:
            return False
        # The server actor must be on the domain that was read.
        return access.remote_server_domain(result.server_actor, origin) == result.domain
    return result.error is not None and result.server_actor is None and result.software_name is None and result.software_version is None and result.shared_kb_count is None


def knowledge_internal_routes(settings: Any, *, now: Callable[[], float], origin: Callable[[], str]) -> list[BaseRoute]:
    """The routes of this module. ``origin`` gives the canonical origin of this server."""

    async def knowledge_activity(request: Request) -> Response:
        try:
            body = KnowledgeActivityRequest.model_validate_json(await request.body())
        except ValidationError:
            return _error(400, "invalid_request")
        canonical = origin()

        def _apply() -> journal.Receipt:
            conn = dbm.connect_existing(settings.db_file)
            try:
                return journal.receive(conn, activity=body.activity, signer=body.signer, verified_by=body.verified_by, recipient=body.recipient, origin=canonical, now=now())
            finally:
                conn.close()

        receipt = await run_in_threadpool(_apply)
        # The line has no address.
        _log.info("knowledge activity: %s%s", receipt.decision, "" if receipt.reason is None else f" ({receipt.reason})")
        return _json(KnowledgeActivityResponse(decision=receipt.decision, reason=receipt.reason, journal_id=None if receipt.entry is None else receipt.entry.id))

    def _kb_items(conn: Any, canonical: str) -> list[CollectionItem]:
        # The shared knowledge bases: not personal, without an owner, public, with a knowledge type (shared knowledge base design Section 11.2). Personal knowledge bases are never in the collection (KD3).
        rows = conn.execute("SELECT number, label, description, knowledge_type, state FROM knowledge_bases WHERE is_personal=0 AND account_id IS NULL AND is_public=1 AND knowledge_type IN ('real', 'fictional') AND number IS NOT NULL ORDER BY number DESC").fetchall()
        return [CollectionItem(number=ids.format_public_id(int(row[0])), label=str(row[1] or ""), description=str(row[2] or ""), knowledge_type=row[3], state=str(row[4]), url=f"{canonical}/kb/{ids.format_public_id(int(row[0]))}/") for row in rows]

    def _theme_items(conn: Any, canonical: str) -> list[CollectionItem]:
        return [
            CollectionItem(number=ids.format_public_id(int(row["number"])), label=str(row["label"] or ""), description=str(row["description"] or ""), knowledge_type=row["knowledge_type"], state=str(row["state"]), url=f"{canonical}/themes/{ids.format_public_id(int(row['number']))}/")
            for row in themes_store.list_themes(conn)
        ]

    def knowledge_collection(request: Request) -> Response:
        name = request.path_params["name"]
        if name not in COLLECTIONS:
            return _not_found()
        # No ``signer``: the request had no valid signature.
        signer = request.query_params.get("signer")
        if set(request.query_params) - {"signer"} or (signer is not None and not 0 < len(signer) <= _MAX_IRI_LENGTH):
            return _error(400, "invalid_request")
        canonical = origin()
        conn = dbm.connect_existing(settings.db_file, readonly=True)
        try:
            decision = access.collection_access(conn, signer=signer, origin=canonical)
            if not decision.allowed:
                # Each refusal is the same 404: a blocked server learns nothing (KS1).
                _log.info("knowledge collection %s: refused (%s)", name, decision.reason)
                return _not_found()
            assert decision.domain is not None
            items = _kb_items(conn, canonical) if name == "kb" else _theme_items(conn, canonical)
        finally:
            conn.close()
        return _json(KnowledgeCollectionResponse(collection=name, server=decision.domain, items=items))

    def server_reads(request: Request) -> Response:
        limit = SERVER_READS_DEFAULT
        raw = request.query_params.get("limit")
        if set(request.query_params) - {"limit"}:
            return _error(400, "invalid_request")
        if raw is not None:
            if not (raw.isascii() and raw.isdigit() and 1 <= int(raw) <= SERVER_READS_MAX):
                return _error(400, "invalid_request")
            limit = int(raw)
        conn = dbm.connect_existing(settings.db_file)
        try:
            # No server is read while the switch is off. The request changes data (the leases), although the method is GET, as ``outbox-tasks`` does.
            due = known_servers.lease_reads(conn, now=now(), limit=limit) if access.federation_on(conn) else []
            conn.commit()
        finally:
            conn.close()
        return _json(ServerReadsResponse(servers=[ServerReadItem(domain=server.domain) for server in due]))

    async def server_read_result(request: Request) -> Response:
        try:
            result = ServerReadResult.model_validate_json(await request.body())
        except ValidationError:
            return _error(400, "invalid_request")
        if not _valid_read_result(result, origin()):
            return _error(400, "invalid_request")

        def _apply() -> bool:
            conn = dbm.connect_existing(settings.db_file)
            try:
                if result.ok:
                    assert result.server_actor is not None and result.software_name is not None and result.software_version is not None
                    recorded = known_servers.record_read(conn, result.domain, server_actor_iri=result.server_actor, software_name=result.software_name, software_version=result.software_version, shared_kb_count=result.shared_kb_count, now=now())
                else:
                    assert result.error is not None
                    recorded = known_servers.record_read_error(conn, result.domain, error=result.error, now=now())
                conn.commit()
                return recorded
            finally:
                conn.close()

        if not await run_in_threadpool(_apply):
            return _not_found()
        return _json(ServerReadRecorded(domain=result.domain, recorded=True))

    return [
        Route("/knowledge-activities", knowledge_activity, methods=["POST"]),
        Route("/knowledge-collections/{name}", knowledge_collection, methods=["GET"]),
        Route("/knowledge-server-reads", server_reads, methods=["GET"]),
        Route("/knowledge-server-reads", server_read_result, methods=["POST"]),
    ]
