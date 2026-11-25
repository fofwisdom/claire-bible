"""Internal federation API: the collections of an own published object (DOCUMENT_RENDITIONS_AND_PUBLICATION_DESIGN.md Section 5.11 A, phase E6; contract 1.19).

``GET /objects/{public_id}/collections/{name}``: ``name`` is ``replies``, ``likes``, or ``shares``. The answer has the ActivityStreams document; the sidecar gives it at ``/ap/objects/{public_id}/{name}`` as it is. Only an object that is public now has collections (a published document or an own note, the rule of ``quoted_object``); each other case gives the same 404.

``replies`` lists the public replies that this server knows: the notes of the accounts of this server (``federation_notes``) and the received posts (``federation_timeline_items``) whose reply target is the object. The replies of the author of the object are first (the form of Mastodon). A reply on a blocked domain is not listed. ``?page=true`` with an optional ``cursor`` gives a page after the first page.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import BaseRoute, Route

from ..core import ids
from ..federation import object_collections
from ..store import federation as federation_store
from ..store import federation_client as client_store
from ..store import federation_notes as notes_store
from .federation_quote_routes import quoted_object
from .federation_routes import _error, _json, _not_found, _remote_host, _Sealed

__all__ = ["ObjectCollectionResponse", "collection_internal_routes", "known_replies"]

# The largest cursor of a page: the number of replies before the page.
_CURSOR_MAX_DIGITS = 6


class ObjectCollectionResponse(_Sealed):
    """One collection document, or one page of ``replies``."""

    document: dict[str, Any]


def known_replies(conn: Any, client: Any, *, origin: str, public_id: int, author_account_id: int, actor_ids: list[int]) -> tuple[list[str], list[str]]:
    """The public replies to the own object ``public_id`` that this server knows: (replies of the author of the object, replies of other actors).

    A note of this server is a reply while it is not deleted and its account is an actor now. A received post is a reply while its host is a permitted remote address that is not blocked.
    """
    object_iri = f"{origin}/ap/objects/{public_id}"
    own: list[str] = []
    others: list[str] = []
    for note_id, account_id in notes_store.reply_notes(client, object_iri):
        if account_id in actor_ids:
            (own if account_id == author_account_id else others).append(f"{origin}/ap/objects/{note_id}")
    for iri in client_store.received_reply_iris(client, object_iri):
        host = _remote_host(iri, origin)
        if host is not None and federation_store.blocking_domain(conn, host) is None:
            others.append(iri)
    return own, others


def collection_internal_routes(*, origin: Callable[[], str], open_store: Callable[[], Any], open_client: Callable[[], Any], actor_account_ids: Callable[[Any], list[int]]) -> list[BaseRoute]:
    """The routes of this module. The callables are the helpers of ``federation_internal_routes``."""

    def object_collection(request: Request) -> Response:
        name = request.path_params["name"]
        if name not in object_collections.COLLECTION_NAMES:
            return _not_found()
        try:
            public_id = ids.parse_public_id(request.path_params["public_id"])
        except ValueError:
            return _not_found()
        query = request.query_params
        page, cursor = query.get("page"), query.get("cursor")
        if set(query) - {"page", "cursor"} or (page is not None and page != "true") or (cursor is not None and (page is None or not (cursor.isascii() and cursor.isdigit() and len(cursor) <= _CURSOR_MAX_DIGITS))):
            return _error(400, "invalid_request")
        if page is not None and name != "replies":
            return _not_found()
        store = open_store()
        client = open_client()
        try:
            actor_ids = actor_account_ids(store)
            target = quoted_object(store.conn, client, public_id, actor_ids=actor_ids)
            if target is None:
                return _not_found()
            iri = f"{origin()}/ap/objects/{public_id}/{name}"
            if name != "replies":
                counts = client_store.count_object_reactions(client, [public_id])[public_id]
                return _json(ObjectCollectionResponse(document=object_collections.count_collection(iri, counts["like" if name == "likes" else "announce"])))
            own, others = known_replies(store.conn, client, origin=origin(), public_id=public_id, author_account_id=target[0], actor_ids=actor_ids)
        finally:
            client.close()
            store.conn.close()
        if page is None:
            return _json(ObjectCollectionResponse(document=object_collections.replies_collection(iri, own, others)))
        document = object_collections.replies_page(iri, own, others, int(cursor or "0"))
        return _not_found() if document is None else _json(ObjectCollectionResponse(document=document))

    return [Route("/objects/{public_id}/collections/{name}", object_collection, methods=["GET"])]
