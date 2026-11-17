"""Internal federation API of the federation of shared knowledge (KNOWLEDGE_FEDERATION_DESIGN.md Sections 6.2, 6.3, 13, and 14; unit KF1).

``federation_routes.federation_internal_routes`` adds these routes to its table. The sidecar is the only caller: it verifies the signature of each request of another server, and Claire applies the controls and stores the data.

- ``POST /knowledge-activities``: one verified inbound activity of the knowledge federation. Claire writes it to the inbox journal and processes it (``knowledge_federation.journal``).
- ``GET /knowledge-collections/{name}``: one read of a collection of shared knowledge (``kb`` or ``themes``) by another server. ``signer`` is the actor whose signature the sidecar verified. A request that is not permitted gets 404 (``knowledge_federation.access``). The answer has the ActivityStreams document that the sidecar gives to the other server without a change (``knowledge_federation.collections``, unit KF2).
- ``GET /knowledge-server-reads``: the known servers that are due for a read of NodeInfo and of the server actor. The request gives a lease on each one.
- ``POST /knowledge-server-reads``: the result of one read.
- ``GET /knowledge-kb-reads``: the known servers whose ``/kb`` collection is due for a read (unit KF2, design Section 8.1 rule 2). The request gives a lease on each one.
- ``POST /knowledge-kb-reads``: one batch of the items of a read, the end of a read, or its error (``knowledge_federation.remote_kbs``).

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
from ..knowledge_federation import access, journal, known_servers, remote_kbs
from ..knowledge_federation import collections as kf_collections
from ..store import db as dbm
from .federation_routes import _MAX_IRI_LENGTH, _error, _json, _not_found, _Sealed

__all__ = [
    "COLLECTIONS",
    "KnowledgeActivityRequest",
    "KnowledgeActivityResponse",
    "KbReadBatch",
    "KbReadsResponse",
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
# The largest number of items in one batch of a read of a ``/kb`` collection. The body limit of the internal API also applies.
KB_READ_BATCH_MAX = 100


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


class KnowledgeCollectionResponse(_Sealed):
    """``server`` is the domain of the server that signed the request. ``document`` is the ActivityStreams document of the collection or of one of its pages: the sidecar gives it to the other server without a change, with the media type ``application/activity+json``."""

    collection: Literal["kb", "themes"]
    server: str
    document: dict[str, Any]


class KbReadItem(_Sealed):
    """One read that is due. ``url`` is the address of the ``/kb`` collection of the server; the sidecar reads it with a GET that the server actor of this server signs."""

    domain: str
    url: str


class KbReadsResponse(_Sealed):
    reads: list[KbReadItem]


class KbReadBatch(_Sealed):
    """One step of the read of the ``/kb`` collection of ``domain``.

    - ``error`` is ``None``: ``items`` are ``claire:KnowledgeBase`` objects as they came (an empty list is permitted). ``complete`` true ends the read: each record of the server that the read did not contain is deleted.
    - ``error`` is a code: the read failed. ``items`` is empty and ``complete`` is false. No record is deleted.
    """

    domain: str = Field(min_length=1, max_length=253)
    items: list[dict[str, Any]] = Field(max_length=KB_READ_BATCH_MAX)
    complete: bool
    error: str | None = Field(pattern=r"^[a-z0-9_]{1,64}$")


class KbReadBatchResponse(_Sealed):
    """``stored`` and ``refused`` are the numbers of items of this batch; ``refused_code`` is the code of the first refusal. ``removed`` is the number of records that the end of the read deleted (``None`` for a batch that does not end the read)."""

    domain: str
    stored: int
    refused: int
    refused_code: str | None
    removed: int | None


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

    def knowledge_collection(request: Request) -> Response:
        name = request.path_params["name"]
        if name not in COLLECTIONS:
            return _not_found()
        params = request.query_params
        # No ``signer``: the request had no valid signature.
        signer = params.get("signer")
        if set(params) - {"signer", "page", "max_id"} or (signer is not None and not 0 < len(signer) <= _MAX_IRI_LENGTH):
            return _error(400, "invalid_request")
        # ``page=true`` selects a page of the ``kb`` collection; ``max_id`` is the number of the last knowledge base of the page before. The themes collection has no pages.
        page = params.get("page")
        max_id: int | None = None
        if (page is not None and (page != "true" or name != "kb")) or ("max_id" in params and page is None):
            return _error(400, "invalid_request")
        if "max_id" in params:
            try:
                max_id = ids.parse_public_id(params["max_id"])
            except ValueError:
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
            if name == "themes":
                document = kf_collections.themes_collection(conn, origin=canonical)
            elif page is None:
                document = kf_collections.kb_collection_root(conn, origin=canonical)
            else:
                document = kf_collections.kb_collection_page(conn, origin=canonical, max_id=max_id)
        finally:
            conn.close()
        return _json(KnowledgeCollectionResponse(collection=name, server=decision.domain, document=document))

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

    def kb_reads(request: Request) -> Response:
        limit = SERVER_READS_DEFAULT
        raw = request.query_params.get("limit")
        if set(request.query_params) - {"limit"}:
            return _error(400, "invalid_request")
        if raw is not None:
            if not (raw.isascii() and raw.isdigit() and 1 <= int(raw) <= SERVER_READS_MAX):
                return _error(400, "invalid_request")
            limit = int(raw)
        canonical = origin()
        conn = dbm.connect_existing(settings.db_file)
        try:
            # No collection is read while the switch is off. The request changes data (the leases), although the method is GET, as ``knowledge-server-reads`` does.
            due = remote_kbs.lease_reads(conn, now=now(), limit=limit) if access.federation_on(conn) else []
            conn.commit()
        finally:
            conn.close()
        return _json(KbReadsResponse(reads=[KbReadItem(domain=domain, url=remote_kbs.collection_url(domain, canonical)) for domain in due]))

    async def kb_read_batch(request: Request) -> Response:
        try:
            batch = KbReadBatch.model_validate_json(await request.body())
        except ValidationError:
            return _error(400, "invalid_request")
        if batch.error is not None and (batch.items or batch.complete):
            return _error(400, "invalid_request")
        canonical = origin()

        def _apply() -> KbReadBatchResponse | None:
            conn = dbm.connect_existing(settings.db_file)
            try:
                when = now()
                if batch.error is not None:
                    if not remote_kbs.record_read_error(conn, batch.domain, error=batch.error, now=when):
                        return None
                    conn.commit()
                    return KbReadBatchResponse(domain=batch.domain, stored=0, refused=0, refused_code=None, removed=None)
                # While the switch is off there is no exchange: the batch is not stored, and the read stays open until its lease ends.
                result = remote_kbs.record_batch(conn, batch.domain, items=batch.items, origin=canonical, now=when) if access.federation_on(conn) else None
                if result is None:
                    return None
                removed = remote_kbs.finish_read(conn, batch.domain, now=when) if batch.complete else None
                conn.commit()
                return KbReadBatchResponse(domain=batch.domain, stored=result.stored, refused=result.refused, refused_code=result.refused_code, removed=removed)
            finally:
                conn.close()

        answer = await run_in_threadpool(_apply)
        if answer is None:
            # The domain has no read that a lease started (or a block or the switch stopped it).
            return _not_found()
        # The line has no address.
        _log.info("knowledge kb read: %d stored, %d refused%s", answer.stored, answer.refused, "" if answer.refused_code is None else f" ({answer.refused_code})")
        return _json(answer)

    return [
        Route("/knowledge-kb-reads", kb_reads, methods=["GET"]),
        Route("/knowledge-kb-reads", kb_read_batch, methods=["POST"]),
        Route("/knowledge-activities", knowledge_activity, methods=["POST"]),
        Route("/knowledge-collections/{name}", knowledge_collection, methods=["GET"]),
        Route("/knowledge-server-reads", server_reads, methods=["GET"]),
        Route("/knowledge-server-reads", server_read_result, methods=["POST"]),
    ]
