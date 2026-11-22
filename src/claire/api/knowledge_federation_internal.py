"""Internal federation API of the federation of shared knowledge (KNOWLEDGE_FEDERATION_DESIGN.md Sections 6.2, 6.3, 13, and 14; unit KF1).

``federation_routes.federation_internal_routes`` adds these routes to its table. The sidecar is the only caller: it verifies the signature of each request of another server, and Claire applies the controls and stores the data.

- ``POST /knowledge-activities``: one verified inbound activity of the knowledge federation. Claire writes it to the inbox journal and processes it (``knowledge_federation.journal``).
- ``GET /knowledge-collections/{name}``: one read of a collection of shared knowledge (``kb`` or ``themes``) by another server. ``signer`` is the actor whose signature the sidecar verified. A request that is not permitted gets 404 (``knowledge_federation.access``). The answer has the ActivityStreams document that the sidecar gives to the other server without a change (``knowledge_federation.collections``, unit KF2).
- ``GET /knowledge-server-reads``: the known servers that are due for a read of NodeInfo and of the server actor. The request gives a lease on each one.
- ``POST /knowledge-server-reads``: the result of one read.
- ``GET /knowledge-kb-reads``: the known servers whose ``/kb`` collection is due for a read (unit KF2, design Section 8.1 rule 2). The request gives a lease on each one.
- ``POST /knowledge-kb-reads``: one batch of the items of a read, the end of a read, or its error (``knowledge_federation.remote_kbs``).

Unit KF3 (design Sections 9.2, 9.3, 10, and 11):

- ``GET /knowledge-kb-actors/{number}``: the actor document of a shared knowledge base of this server, with its public key. It needs no signer: another server reads it to verify a signature.
- ``GET /knowledge-kb-actors/{number}/keys``: the key pairs of that actor, for the signature of its activities. The first request makes them.
- ``GET /knowledge-kb-actors/{number}/{collection}``: the collection ``followers``, ``outbox``, or ``documents`` of that actor for another server (``signer`` as for ``knowledge-collections``; ``documents`` has pages).
- ``GET /knowledge-kb-actors/{number}/documents/{document}``: one visible document with its extraction, for a curation on another server.
- ``GET /knowledge-outbox-tasks`` and ``POST /knowledge-outbox-tasks/{id}/ack``: the outbound activities (``knowledge_federation.outbox``). Each task has the activity JSON as it must go out, the actor whose key signs it, and the actor that gets it.
- ``GET /knowledge-remote-reads`` and ``POST /knowledge-remote-reads``: the reads of documents of another server for a curation (``knowledge_federation.remote_documents``).

The models are sealed, as in ``federation_routes``. The contract version is 1.11: no shape that existed changed. Unit KF4 added one value of ``signer`` of an outbox task: ``account:`` and the public ID of an account actor.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any, Literal

from pydantic import Field, ValidationError
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import BaseRoute, Route

from pathlib import Path

from ..core import ids
from ..federation.knowledge import access, actors, journal, known_servers, outbox, remote_documents, remote_kbs, vocabulary
from ..federation.knowledge import collections as kf_collections
from ..federation.knowledge import handlers as kf_handlers  # noqa: F401 - the import installs the handlers of unit KF3 in the journal
from ..store import actor_keys, auth_db, kb_collab
from ..store import db as dbm
from ..notifications import events as notification_events
from ..store.secrets import get_secret_service
from .federation_routes import _MAX_IRI_LENGTH, ActorKey, ActorKeysResponse, _error, _json, _not_found, _Sealed

__all__ = [
    "COLLECTIONS",
    "KbActorDocument",
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


class KbActorDocument(_Sealed):
    """``document`` is an ActivityStreams document of a knowledge base actor of this server: the actor document, one of its collections, a page, or one of its documents. The sidecar gives it to the other server without a change."""

    document: dict[str, Any]


class KnowledgeOutboxTaskItem(_Sealed):
    """One outbound activity. ``activity`` is the JSON that must go out without a change. ``signer`` is ``server`` (the server actor), the number of a knowledge base actor of this server, or ``account:`` and the public ID of an account actor of this server (the ``Offer`` of a contribution, unit KF4): the key of that actor signs the request. ``recipient`` is the actor that gets the activity; the sidecar reads its inbox from its actor document."""

    id: int
    kind: str
    signer: str
    recipient: str
    activity: dict[str, Any]


class KnowledgeOutboxTasks(_Sealed):
    tasks: list[KnowledgeOutboxTaskItem]


class KnowledgeOutboxAck(_Sealed):
    """``delivered``: the inbox answered with success. ``retry``: the delivery failed and can succeed later. ``rejected``: the delivery is not possible. ``error_code`` is ``None`` only for ``delivered``."""

    result: Literal["delivered", "retry", "rejected"]
    error_code: str | None = Field(pattern=r"^[a-z0-9_]{1,64}$")


class KnowledgeOutboxAckResponse(_Sealed):
    id: int
    status: Literal["pending", "delivered", "failed", "refused"]


class RemoteReadItem(_Sealed):
    """One read of a document of another server. The sidecar reads ``url`` with a GET that the server actor of this server signs."""

    id: int
    url: str


class RemoteReads(_Sealed):
    reads: list[RemoteReadItem]


class RemoteReadResult(_Sealed):
    """The answer of one read: ``document`` is the JSON as it came and ``error`` is ``None``, or ``document`` is ``None`` and ``error`` is a code."""

    id: int
    document: dict[str, Any] | None
    error: str | None = Field(pattern=r"^[a-z0-9_]{1,64}$")


class RemoteReadRecorded(_Sealed):
    """``status``: ``pending`` (a next page of the collection follows), ``done``, or ``failed`` with ``error_code``."""

    id: int
    status: Literal["pending", "done", "failed"]
    error_code: str | None


KB_ACTOR_COLLECTIONS: tuple[str, ...] = ("followers", "outbox", "documents")
OUTBOX_TASKS_DEFAULT = 10
OUTBOX_TASKS_MAX = 50


def _limit(request: Request, *, default: int, maximum: int) -> int | None:
    """The query ``limit`` of a lease request, or None for a query that is not valid."""
    raw = request.query_params.get("limit")
    if set(request.query_params) - {"limit"}:
        return None
    if raw is None:
        return default
    return int(raw) if raw.isascii() and raw.isdigit() and 1 <= int(raw) <= maximum else None


def _valid_read_result(result: ServerReadResult, origin: str) -> bool:
    if result.ok:
        if result.server_actor is None or result.software_name is None or result.software_version is None or result.error is not None:
            return False
        # The server actor must be on the domain that was read.
        return access.remote_server_domain(result.server_actor, origin) == result.domain
    return result.error is not None and result.server_actor is None and result.software_name is None and result.software_version is None and result.shared_kb_count is None


def knowledge_internal_routes(settings: Any, *, now: Callable[[], float], origin: Callable[[], str]) -> list[BaseRoute]:
    """The routes of this module. ``origin`` gives the canonical origin of this server."""

    def _run_merge_jobs() -> None:
        from ..ingest.service import run_waiting_merge_jobs

        run_waiting_merge_jobs(settings)

    def _after_answer(response: Response) -> Response:
        """Run the merge jobs that wait after the answer went to the sidecar (KS2): a job can make many embedding calls, and the sidecar does not wait for them. A failure of a job is in its row and in the log."""
        response.background = BackgroundTask(_run_merge_jobs)
        return response

    def _drop_gone_kb_actor_keys(conn: Any) -> None:
        """Delete the keys of each knowledge base actor whose knowledge base is gone and that has no task that waits (its last ``Delete`` was delivered or ended). A number is never used again, so these keys are of no use."""
        data_dir = Path(settings.data_dir)
        auth = auth_db.connect_auth_db(auth_db.auth_db_path(data_dir))
        try:
            for number in actor_keys.kb_actor_key_numbers(auth):
                if actors.shared_kb(conn, number) is None and not outbox.has_open_task(conn, str(number)):
                    actor_keys.delete_kb_actor_keys(auth, number)
        finally:
            auth.close()

    def _kb_number(request: Request) -> int | None:
        try:
            return ids.parse_public_id(request.path_params["number"])
        except ValueError:
            return None

    def _kb_public_key(number: int) -> str:
        """The PEM text of the RSA public key of a knowledge base actor. The first call makes the keys of the actor."""
        data_dir = Path(settings.data_dir)
        secret_service = get_secret_service(data_dir / "auth" / "master.key")
        auth = auth_db.connect_auth_db(auth_db.auth_db_path(data_dir))
        try:
            actor_keys.ensure_kb_actor_keys(auth, number, secret_service, now=now())
            pairs = actor_keys.load_kb_actor_keys(auth, number, secret_service)
        finally:
            auth.close()
        return actor_keys.public_key_pem(next(pair.public_jwk for pair in pairs if pair.alg == "rsa2048"))

    def kb_actor(request: Request) -> Response:
        number = _kb_number(request)
        if number is None or request.query_params:
            return _not_found()
        canonical = origin()
        conn = dbm.connect_existing(settings.db_file, readonly=True)
        try:
            exists = actors.shared_kb(conn, number) is not None
        finally:
            conn.close()
        if not exists:
            return _not_found()
        pem = _kb_public_key(number)
        conn = dbm.connect_existing(settings.db_file, readonly=True)
        try:
            document = actors.actor_document(conn, number, origin=canonical, public_key_pem=pem)
        finally:
            conn.close()
        return _not_found() if document is None else _json(KbActorDocument(document=document))

    def kb_actor_keys(request: Request) -> Response:
        number = _kb_number(request)
        if number is None:
            return _not_found()
        conn = dbm.connect_existing(settings.db_file, readonly=True)
        try:
            # The keys of an actor whose knowledge base is gone are given while its last activity (the ``Delete`` of its record) waits, as for a purged account actor (FEDERATION.md Section 8.5).
            allowed = actors.shared_kb(conn, number) is not None or outbox.has_open_task(conn, str(number))
        finally:
            conn.close()
        if not allowed:
            return _not_found()
        data_dir = Path(settings.data_dir)
        secret_service = get_secret_service(data_dir / "auth" / "master.key")
        auth = auth_db.connect_auth_db(auth_db.auth_db_path(data_dir))
        try:
            actor_keys.ensure_kb_actor_keys(auth, number, secret_service, now=now())
            pairs = actor_keys.load_kb_actor_keys(auth, number, secret_service)
        finally:
            auth.close()
        return _json(ActorKeysResponse(public_id=ids.format_public_id(number), keys=[ActorKey(alg=p.alg, public_jwk=p.public_jwk, private_jwk=p.private_jwk) for p in pairs]))

    def _signed_read(request: Request, extra: set[str]) -> tuple[Response | None, str | None]:
        """The signer of a read of another server, after the check of the query keys. The first value is the answer for a query that is not valid."""
        params = request.query_params
        signer = params.get("signer")
        if set(params) - {"signer", *extra} or (signer is not None and not 0 < len(signer) <= _MAX_IRI_LENGTH):
            return _error(400, "invalid_request"), None
        return None, signer

    def kb_actor_collection(request: Request) -> Response:
        number, name = _kb_number(request), request.path_params["name"]
        if number is None or name not in KB_ACTOR_COLLECTIONS:
            return _not_found()
        bad, signer = _signed_read(request, {"page", "max_id"})
        if bad is not None:
            return bad
        params = request.query_params
        page = params.get("page")
        max_id: int | None = None
        if (page is not None and (page != "true" or name != "documents")) or ("max_id" in params and page is None):
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
            kb = actors.shared_kb(conn, number) if decision.allowed else None
            if kb is None:
                # Each refusal is the same 404 (KS1).
                return _not_found()
            if name == "followers":
                document = actors.followers_collection(conn, number, origin=canonical)
            elif name == "outbox":
                document = actors.outbox_collection(conn, number, origin=canonical, total=outbox.count_activities(conn, str(number)))
            elif page is None:
                document = actors.documents_root(conn, kb, origin=canonical)
            else:
                document = actors.documents_page(conn, kb, origin=canonical, max_id=max_id)
        finally:
            conn.close()
        return _json(KbActorDocument(document=document))

    def kb_actor_document(request: Request) -> Response:
        number = _kb_number(request)
        try:
            public_id = ids.parse_public_id(request.path_params["document"])
        except ValueError:
            return _not_found()
        if number is None:
            return _not_found()
        bad, signer = _signed_read(request, set())
        if bad is not None:
            return bad
        canonical = origin()
        conn = dbm.connect_existing(settings.db_file, readonly=True)
        try:
            decision = access.collection_access(conn, signer=signer, origin=canonical)
            kb = actors.shared_kb(conn, number) if decision.allowed else None
            contribution = kb_collab.get_contribution(conn, public_id) if kb is not None else None
            if kb is None or contribution is None or int(contribution["kb_id"]) != int(kb["id"]):
                return _not_found()
            document = actors.document_object(conn, kb, contribution, origin=canonical)
        finally:
            conn.close()
        if document is None:
            return _not_found()
        return _json(KbActorDocument(document={"@context": list(vocabulary.WIRE_CONTEXT), **document}))

    def outbox_tasks(request: Request) -> Response:
        limit = _limit(request, default=OUTBOX_TASKS_DEFAULT, maximum=OUTBOX_TASKS_MAX)
        if limit is None:
            return _error(400, "invalid_request")
        canonical = origin()
        conn = dbm.connect_existing(settings.db_file)
        try:
            # The request changes data (the tasks that the state needs, and the leases), although the method is GET, as ``outbox-tasks`` does.
            tasks = outbox.lease(conn, origin=canonical, now=now(), limit=limit)
            conn.commit()
            _drop_gone_kb_actor_keys(conn)
        finally:
            conn.close()
        return _json(KnowledgeOutboxTasks(tasks=[KnowledgeOutboxTaskItem(id=t.id, kind=t.kind, signer=t.signer, recipient=t.recipient, activity=json.loads(t.payload)) for t in tasks]))

    async def outbox_task_ack(request: Request) -> Response:
        try:
            task_id = int(request.path_params["task_id"])
            body = KnowledgeOutboxAck.model_validate_json(await request.body())
        except (ValueError, ValidationError):
            return _error(400, "invalid_request")
        if (body.result == "delivered") != (body.error_code is None):
            return _error(400, "invalid_request")
        conn = dbm.connect_existing(settings.db_file)
        try:
            task = outbox.ack(conn, task_id, result=body.result, error_code=body.error_code, now=now())
            conn.commit()
        finally:
            conn.close()
        return _not_found() if task is None else _json(KnowledgeOutboxAckResponse(id=task.id, status=task.status))

    def remote_reads(request: Request) -> Response:
        limit = _limit(request, default=SERVER_READS_DEFAULT, maximum=SERVER_READS_MAX)
        if limit is None:
            return _error(400, "invalid_request")
        conn = dbm.connect_existing(settings.db_file)
        try:
            reads = remote_documents.lease(conn, now=now(), limit=limit)
            conn.commit()
        finally:
            conn.close()
        return _json(RemoteReads(reads=[RemoteReadItem(id=int(read["id"]), url=str(read["url"])) for read in reads]))

    async def remote_read_result(request: Request) -> Response:
        try:
            result = RemoteReadResult.model_validate_json(await request.body())
        except ValidationError:
            return _error(400, "invalid_request")
        if (result.document is None) == (result.error is None):
            return _error(400, "invalid_request")
        canonical = origin()

        def _apply() -> dict[str, Any] | None:
            conn = dbm.connect_existing(settings.db_file)
            try:
                read = remote_documents.record_result(conn, result.id, document=result.document, error=result.error, origin=canonical, now=now())
                conn.commit()
                return read
            finally:
                conn.close()

        read = await run_in_threadpool(_apply)
        if read is None:
            return _not_found()
        _log.info("knowledge remote read: %s%s", read["status"], "" if read["error_code"] is None else f" ({read['error_code']})")
        answer = _json(RemoteReadRecorded(id=int(read["id"]), status=read["status"], error_code=read["error_code"]))
        if read["kind"] in ("document", "recheck") and read["status"] == "done":
            # The copy of a curation (or a revision after a read again) is a merge job: it builds the graph from the extraction that came, without an LLM call, after the answer.
            return _after_answer(answer)
        return answer

    async def knowledge_activity(request: Request) -> Response:
        try:
            body = KnowledgeActivityRequest.model_validate_json(await request.body())
        except ValidationError:
            return _error(400, "invalid_request")
        canonical = origin()

        def _apply() -> journal.Receipt:
            conn = dbm.connect_existing(settings.db_file)
            try:
                # The handlers call notification events in a savepoint; the events run after the journal committed (PWA_NOTIFICATION_DESIGN.md PN2 rule 2).
                with notification_events.after_commit():
                    return journal.receive(conn, activity=body.activity, signer=body.signer, verified_by=body.verified_by, recipient=body.recipient, origin=canonical, now=now())
            finally:
                conn.close()

        receipt = await run_in_threadpool(_apply)
        # The line has no address.
        _log.info("knowledge activity: %s%s", receipt.decision, "" if receipt.reason is None else f" ({receipt.reason})")
        answer = _json(KnowledgeActivityResponse(decision=receipt.decision, reason=receipt.reason, journal_id=None if receipt.entry is None else receipt.entry.id))
        # A revision of a copy (unit KF3) is a merge job: it builds the graph after the journal committed, and after the answer to the sidecar.
        return _after_answer(answer) if receipt.decision == "processed" else answer

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
            # The request changes data (the leases), although the method is GET, as ``outbox-tasks`` does.
            due = known_servers.lease_reads(conn, now=now(), limit=limit)
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
            # The request changes data (the leases), although the method is GET, as ``knowledge-server-reads`` does.
            due = remote_kbs.lease_reads(conn, now=now(), limit=limit)
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
                result = remote_kbs.record_batch(conn, batch.domain, items=batch.items, origin=canonical, now=when)
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
        Route("/knowledge-kb-actors/{number}", kb_actor, methods=["GET"]),
        Route("/knowledge-kb-actors/{number}/keys", kb_actor_keys, methods=["GET"]),
        Route("/knowledge-kb-actors/{number}/documents/{document}", kb_actor_document, methods=["GET"]),
        Route("/knowledge-kb-actors/{number}/{name}", kb_actor_collection, methods=["GET"]),
        Route("/knowledge-outbox-tasks", outbox_tasks, methods=["GET"]),
        Route("/knowledge-outbox-tasks/{task_id}/ack", outbox_task_ack, methods=["POST"]),
        Route("/knowledge-remote-reads", remote_reads, methods=["GET"]),
        Route("/knowledge-remote-reads", remote_read_result, methods=["POST"]),
        Route("/knowledge-kb-reads", kb_reads, methods=["GET"]),
        Route("/knowledge-kb-reads", kb_read_batch, methods=["POST"]),
        Route("/knowledge-activities", knowledge_activity, methods=["POST"]),
        Route("/knowledge-collections/{name}", knowledge_collection, methods=["GET"]),
        Route("/knowledge-server-reads", server_reads, methods=["GET"]),
        Route("/knowledge-server-reads", server_read_result, methods=["POST"]),
    ]
