"""Internal federation API: the fills of recent posts after a follow (DOCUMENT_RENDITIONS_AND_PUBLICATION_DESIGN.md Section 5.13, phase E10a; contract 1.24).

- ``GET /fill-tasks?limit=``: the fills that wait, each with a lease: ``id``, ``actor_public_id`` (the account: its key signs the reads, rule 4), ``actor_iri`` (the followed actor), ``max_items``, ``lease_until``.
- ``POST /fill-objects``: posts of one fill (``objects``, the normal form of the timeline) and ``done`` with the last call. Claire applies the rules of a ``Create`` for each post (a permitted remote address, no blocked domain, the size limits) and the rules of the fill (``store.federation_fills.store_objects``).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pydantic import Field, ValidationError
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import BaseRoute, Route

from ..store import federation_client as client_store
from ..store import federation_fills as fills_store
from .federation_routes import TimelineObject, _error, _json, _Sealed

__all__ = ["FILL_TASKS_DEFAULT", "fill_internal_routes"]

FILL_TASKS_DEFAULT = 2
FILL_TASKS_MAX = 10


class FillTaskItem(_Sealed):
    id: int
    actor_public_id: str
    actor_iri: str
    max_items: int = Field(ge=1)
    lease_until: float


class FillTasksResponse(_Sealed):
    tasks: list[FillTaskItem]


class FillObjectsRequest(_Sealed):
    task_id: int
    objects: list[TimelineObject] = Field(max_length=100)
    done: bool


class FillObjectsResponse(_Sealed):
    decision: str
    stored: int


def fill_internal_routes(*, origin: Callable[[], str], now: Callable[[], float], open_conn: Callable[[], Any], open_client: Callable[[], Any], object_refusal: Callable[[Any, str, TimelineObject], str | None], actor_public_ids: Callable[[Any], dict[int, str]]) -> list[BaseRoute]:
    """The routes of this module. ``open_conn`` gives a connection of ``claire.db``; ``actor_public_ids`` gives the number of each account that is an actor; ``object_refusal`` is the check of a remote post of the timeline."""

    def fill_tasks(request: Request) -> Response:
        raw = request.query_params.get("limit", str(FILL_TASKS_DEFAULT))
        if set(request.query_params) - {"limit"} or not (raw.isascii() and raw.isdigit() and 1 <= int(raw) <= FILL_TASKS_MAX):
            return _error(400, "invalid_request")
        conn = open_conn()
        try:
            actors = actor_public_ids(conn)
        finally:
            conn.close()
        client = open_client()
        try:
            leased = fills_store.lease(client, now=now(), limit=int(raw), account_ids=set(actors))
            items = [FillTaskItem(id=fill.id, actor_public_id=actors[fill.account_id], actor_iri=fill.actor_iri, max_items=fills_store.MAX_POSTS, lease_until=float(fill.lease_until or 0.0)) for fill in leased]
            client.commit()
        finally:
            client.close()
        return _json(FillTasksResponse(tasks=items))

    async def fill_objects(request: Request) -> Response:
        try:
            body = FillObjectsRequest.model_validate_json(await request.body())
        except ValidationError:
            return _error(400, "invalid_request")
        conn = open_conn()
        client = open_client()
        try:
            fill = fills_store.get_fill(client, body.task_id)
            if fill is None or fill.status != "pending":
                return _error(404, "not_found")
            canonical = origin()
            permitted = [obj.model_dump(mode="json") for obj in body.objects if object_refusal(conn, canonical, obj) is None]
            followed = set(client_store.accepted_following_iris(client, fill.account_id))
            retention = int(client_store.get_reading_settings(conn)["retention_days"])
            stored = fills_store.store_objects(client, fill, permitted, followed=followed, retention_days=retention, now=now())
            if body.done:
                fills_store.finish(client, fill.id, now=now())
            client.commit()
        finally:
            client.close()
            conn.close()
        return _json(FillObjectsResponse(decision="accepted", stored=stored))

    return [Route("/fill-tasks", fill_tasks, methods=["GET"]), Route("/fill-objects", fill_objects, methods=["POST"])]
