"""Internal federation API: the fetches of opinions (DOCUMENT_RENDITIONS_AND_PUBLICATION_DESIGN.md Section 5.11 B, phase E7a; contract 1.23).

- ``GET /opinion-tasks?limit=``: the fetches that wait, each with a lease. ``own`` is true for an object of this server: the sidecar cannot read its own origin, so it starts from ``start`` (the replies that this server knows) and walks the reply tree of each one, and it reads each post of ``quotes`` (the quoting objects that this server knows) without a walk: the replies of a quote post are not opinions of the object (Section 5.11 B4). For an object of another server, ``start`` and ``quotes`` are empty and the sidecar walks from the object.
- ``POST /opinion-objects``: posts of one fetch (``objects``, the normal form of the timeline), the start addresses that are gone at their origin (``gone``), and ``done`` with the last call. Claire stores the posts that are opinions of the object (``store.federation_opinions.store_objects``) with the rules of a ``Create`` for each post (a permitted remote address, no blocked domain, the size limits).

The server actor signs the fetches: the result belongs to the object, not to an account (Section 5.11 B8).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pydantic import Field, ValidationError
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import BaseRoute, Route

from ..store import federation_client as client_store
from ..store import federation_opinions as opinions_store
from ..store import federation_quotes as quotes_store
from .federation_routes import TimelineObject, _error, _json, _remote_host, _Sealed

__all__ = ["OPINION_MAX_DEPTH", "OPINION_TASKS_DEFAULT", "opinion_internal_routes"]

OPINION_TASKS_DEFAULT = 2
OPINION_TASKS_MAX = 10
# The levels of the reply tree under one start post. Mastodon limits the count (500 under one reply); the level limit stops a chain without an end.
OPINION_MAX_DEPTH = 16
_START_MAX = 200


class OpinionTaskItem(_Sealed):
    id: int
    object_iri: str
    own: bool
    start: list[str]
    quotes: list[str]
    max_items: int = Field(ge=1)
    max_depth: int = Field(ge=1)
    lease_until: float


class OpinionTasksResponse(_Sealed):
    tasks: list[OpinionTaskItem]


class OpinionObjectsRequest(_Sealed):
    task_id: int
    objects: list[TimelineObject] = Field(max_length=100)
    gone: list[str] = Field(max_length=2 * _START_MAX)
    done: bool


class OpinionObjectsResponse(_Sealed):
    decision: str
    stored: int


def _remote_unique(iris: list[str], canonical: str, skip: set[str]) -> list[str]:
    """The addresses of ``iris`` in their order, each one time, without the addresses of ``skip``: posts of other servers, and since RD30 posts of this server (the sidecar reads them through its loopback, so that they become opinions as the posts of another server)."""
    out: list[str] = []
    for iri in iris:
        if iri in skip or iri in out or (_remote_host(iri, canonical) is None and not iri.startswith(f"{canonical}/ap/objects/")):
            continue
        out.append(iri)
    return out


def opinion_internal_routes(*, origin: Callable[[], str], now: Callable[[], float], open_conn: Callable[[], Any], open_client: Callable[[], Any], object_refusal: Callable[[Any, str, TimelineObject], str | None], own_object_public_id: Callable[[str], int | None]) -> list[BaseRoute]:
    """The routes of this module. ``open_conn`` gives a connection of ``claire.db`` that can write; ``object_refusal`` is the check of a remote post of the timeline; ``own_object_public_id`` gives the number of an address of an own object."""

    def _approved(client: Any, object_iri: str) -> set[str]:
        """The quoting objects of the approved stamps of an own object."""
        public_id = own_object_public_id(object_iri)
        return set() if public_id is None else set(quotes_store.approved_quoting_objects(client, public_id))

    def _start(client: Any, object_iri: str) -> tuple[list[str], list[str]]:
        """The start set of an own object (Section 5.11 B3): the replies to walk (the delivered replies and the known reply opinions) and the quote posts to read (the quoting objects of the approved stamps and the known quote opinions). Each list has only remote addresses, each one time."""
        known = opinions_store.opinions_of(client, object_iri, include_hidden=True)
        canonical = origin()
        quotes = _remote_unique([*sorted(_approved(client, object_iri)), *(o.opinion_iri for o in known if o.kind == "quote")], canonical, set())
        start = _remote_unique([*client_store.received_reply_iris(client, object_iri), *(o.opinion_iri for o in known if o.kind == "reply")], canonical, set(quotes))
        return start[:_START_MAX], quotes[:_START_MAX]

    def opinion_tasks(request: Request) -> Response:
        raw = request.query_params.get("limit", str(OPINION_TASKS_DEFAULT))
        if set(request.query_params) - {"limit"} or not (raw.isascii() and raw.isdigit() and 1 <= int(raw) <= OPINION_TASKS_MAX):
            return _error(400, "invalid_request")
        client = open_client()
        try:
            items = []
            for fetch in opinions_store.lease(client, now=now(), limit=int(raw)):
                start, quotes = _start(client, fetch.object_iri) if fetch.own else ([], [])
                items.append(OpinionTaskItem(id=fetch.id, object_iri=fetch.object_iri, own=fetch.own, start=start, quotes=quotes, max_items=opinions_store.MAX_OPINIONS, max_depth=OPINION_MAX_DEPTH, lease_until=float(fetch.lease_until or 0.0)))
            client.commit()
        finally:
            client.close()
        return _json(OpinionTasksResponse(tasks=items))

    async def opinion_objects(request: Request) -> Response:
        try:
            body = OpinionObjectsRequest.model_validate_json(await request.body())
        except ValidationError:
            return _error(400, "invalid_request")
        conn = open_conn()
        client = open_client()
        try:
            row = client.execute("SELECT object_iri, own FROM federation_opinion_fetches WHERE id=? AND status='pending'", (body.task_id,)).fetchone()
            if row is None:
                return _error(404, "not_found")
            object_iri = str(row[0])
            canonical = origin()
            quoting = _approved(client, object_iri) if bool(row[1]) else set()
            delivered = set(client_store.received_reply_iris(client, object_iri))
            permitted = [obj.model_dump(mode="json") for obj in body.objects if object_refusal(conn, canonical, obj) is None]
            stored = opinions_store.store_objects(client, object_iri, permitted, quoting=quoting, delivered=delivered, now=now())
            opinions_store.forget(client, object_iri, [iri for iri in body.gone if isinstance(iri, str)])
            if body.done:
                fetch = opinions_store.get_fetch(client, object_iri)
                previous = 0 if fetch is None or fetch.result_count is None else fetch.result_count
                opinions_store.finish(client, body.task_id, result_count=previous + stored, now=now())
            elif stored:
                client.execute("UPDATE federation_opinion_fetches SET result_count=COALESCE(result_count, 0)+? WHERE id=?", (stored, body.task_id))
            client.commit()
        finally:
            client.close()
            conn.close()
        return _json(OpinionObjectsResponse(decision="accepted", stored=stored))

    return [Route("/opinion-tasks", opinion_tasks, methods=["GET"]), Route("/opinion-objects", opinion_objects, methods=["POST"])]
