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

import hashlib
import re
import time

import nh3

from ..core import ids
from ..extract.rendition_templates import PRIMARY_RENDITION_TEMPLATE
from ..federation import object_collections
from ..federation.knowledge import access as knowledge_access
from ..store import db as dbm
from ..store import document_renditions as renditions_store
from ..store import federation as federation_store
from ..store import federation_client as client_store
from ..store import federation_notes as notes_store
from .federation_quote_routes import quoted_object
from .federation_routes import _error, _json, _not_found, _remote_host, _Sealed, rendition_body_text

__all__ = ["ObjectCollectionResponse", "collection_internal_routes", "known_replies"]

# The source index (Section 5.4 rule 3): a source key, and the documents of one answer (the newest publications first).
_SOURCE_KEY_RE = re.compile(r"[0-9a-f]{64}")
SOURCE_INDEX_DOCUMENTS_MAX = 50

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


def collection_internal_routes(*, origin: Callable[[], str], open_store: Callable[[], Any], open_client: Callable[[], Any], actor_account_ids: Callable[[Any], list[int]], open_writer: Callable[[], Any]) -> list[BaseRoute]:
    """The routes of this module. The callables are the helpers of ``federation_internal_routes``; ``open_writer`` gives a connection that can write (the readers of a body)."""

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
            if name == "renditions":
                # Only a document object has the collection of its renditions (phase E4b).
                obj = federation_store.get_object(store.conn, public_id)
                if obj is None:
                    return _not_found()
                base = f"{origin()}/ap/objects/"
                items = [f"{base}{public_id}"] + [f"{base}{r.public_id}" for r in federation_store.rendition_objects_of(store.conn, public_id)]
                return _json(ObjectCollectionResponse(document=object_collections.renditions_collection(iri, items)))
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

    def rendition_body(request: Request) -> Response:
        """``GET /objects/{public_id}/body?signer=...``: the body of a rendition for the server actor of another Claire server (DOCUMENT_RENDITIONS_AND_PUBLICATION_DESIGN.md Section 5.5, KS1). ``signer`` is the actor whose HTTP signature the sidecar verified; each refusal is the same 404."""
        try:
            public_id = ids.parse_public_id(request.path_params["public_id"])
        except ValueError:
            return _not_found()
        query = request.query_params
        signer = query.get("signer")
        if set(query) - {"signer"} or (signer is not None and not 0 < len(signer) <= 2048):
            return _error(400, "invalid_request")
        canonical = origin()
        store = open_store()
        client = open_client()
        try:
            decision = knowledge_access.collection_access(store.conn, signer=signer, origin=canonical)
            if not decision.allowed:
                return _not_found()
            if quoted_object(store.conn, client, public_id, actor_ids=actor_account_ids(store)) is None:
                return _not_found()
            owning = federation_store.owning_object(store.conn, public_id)
            if owning is None:
                return _not_found()
            obj, rendition_object = owning
            template = PRIMARY_RENDITION_TEMPLATE if rendition_object is None else rendition_object.template
            rendition = renditions_store.get_rendition(store.conn, obj.document_id, obj.kb_id, template)
            document = dbm.get_document_row(store.conn, obj.document_id, kb_id=obj.kb_id)
            source = rendition_body_text(document, rendition)
            if not source:
                return _not_found()
            compiled = (document["detail_html"] if rendition is None or rendition.primary else rendition.detail_html) or ""
        finally:
            client.close()
            store.conn.close()
        # The reader goes to the exact delete list of the object (Section 5.4 rule 4).
        assert signer is not None
        writer = open_writer()
        try:
            federation_store.record_body_reader(writer, public_id, signer, now=time.time())
            writer.commit()
        finally:
            writer.close()
        iri = f"{canonical}/ap/objects/{public_id}"
        body = object_collections.rendition_body_document(f"{iri}/body", iri, html=nh3.clean(compiled), source=source, body_hash=hashlib.sha256(source.encode("utf-8")).hexdigest())
        return _json(ObjectCollectionResponse(document=body))

    def source_index(request: Request) -> Response:
        """``GET /sources/{source_key}?signer=...``: the source index of this server for the server actor of another Claire server (DOCUMENT_RENDITIONS_AND_PUBLICATION_DESIGN.md Section 5.4 rule 3, KS1): an ``OrderedCollection`` of the published rendition objects (document objects and rendition objects) of the documents with this source key. Each refusal is the same 404."""
        key = request.path_params["source_key"]
        query = request.query_params
        signer = query.get("signer")
        if set(query) - {"signer"} or (signer is not None and not 0 < len(signer) <= 2048):
            return _error(400, "invalid_request")
        if not _SOURCE_KEY_RE.fullmatch(key):
            return _not_found()
        canonical = origin()
        store = open_store()
        client = open_client()
        try:
            if not knowledge_access.collection_access(store.conn, signer=signer, origin=canonical).allowed:
                return _not_found()
            actor_ids = actor_account_ids(store)
            rows = store.conn.execute(
                "SELECT o.public_id FROM federation_objects o JOIN documents d ON d.id = o.document_id AND d.kb_id = o.kb_id WHERE d.source_key=? ORDER BY o.published_at DESC LIMIT ?",
                (key, SOURCE_INDEX_DOCUMENTS_MAX),
            ).fetchall()
            items: list[str] = []
            for (public_id,) in rows:
                if quoted_object(store.conn, client, int(public_id), actor_ids=actor_ids) is None:
                    continue
                items.append(f"{canonical}/ap/objects/{public_id}")
                items += [f"{canonical}/ap/objects/{r.public_id}" for r in federation_store.rendition_objects_of(store.conn, int(public_id))]
        finally:
            client.close()
            store.conn.close()
        return _json(ObjectCollectionResponse(document=object_collections.source_index(f"{canonical}/ap/sources/{key}", items)))

    return [Route("/objects/{public_id}/collections/{name}", object_collection, methods=["GET"]), Route("/objects/{public_id}/body", rendition_body, methods=["GET"]), Route("/sources/{source_key}", source_index, methods=["GET"])]
