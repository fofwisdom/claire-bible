"""Actions of a signed-in account in the 의견 tab (DOCUMENT_RENDITIONS_AND_PUBLICATION_DESIGN.md Section 5.6 rule 4 and Section 5.11 B1, D3, D4, and D9; owner decision RD30 of FEDERATION_REMOTE_READING_DESIGN.md Section 6.7).

The caller is an account with the scope ``owner``. Since RD30 an account of this server is a peer as an account of another server: each action uses the routes and the activities of the federation client.

- ``POST /api/federation/opinions/item`` with ``{"object", "opinion"}``: an opinion of an own published object, a note of this server in its thread, or the object itself (the post of the page), as an item of the timeline of the account, so that the favourite, the boost, a reply with ``in_reply_to``, and a quote with ``quote_of`` work on it (Sections 25 and 26 of ``FEDERATION.md``). The item is not in the home stream (``in_timeline`` 0). The account must be an actor (409 ``not_actor``). The answer is ``{"item_id", "favourited", "boosted"}``. Refusals: 404 ``opinion_not_found`` (also a hidden opinion, and an object that is not an own published object), 400 ``blocked_domain``. The like of the post (Section 5.6 rule 4) is the favourite of the item of the object.
- ``POST /api/federation/objects/{object_id}/opinions``: the view of a published object of the caller in ``/syndication`` asks for a fetch of its opinions (Section 5.11 B1), with the conditions of rule B2. The answer is ``{"refresh"}``: the state of the fetch, or ``null`` when no fetch was due and none exists. It needs no actor: the server actor signs the fetch. Refusal: 404 ``object_not_found`` for an object that is not a published document object of the caller.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from ...store import accounts as accounts_store
from ...store import db as dbm
from ...store import federation as federation_store
from ...store import federation_client as client_store
from ...store import federation_notes as notes_store
from ...store import federation_opinions as opinions_store
from ...store import permalinks
from ..principal import request_principal
from ..security import read_json_body
from .common import AccountsContext, json_error, not_found, open_client_db, open_db
from .console_federation_panels import actor_state

__all__ = ["OBJECT_OPINIONS_PATH", "OPINION_ITEM_PATH", "item_states", "routes"]

OPINION_ITEM_PATH = "/api/federation/opinions/item"
OBJECT_OPINIONS_PATH = "/api/federation/objects/{object_id}/opinions"
_NO_STORE = {"Cache-Control": "no-store"}
_MAX_IRI = 2048


def item_states(client: Any, account_id: int, iris: list[str]) -> dict[str, dict[str, bool]]:
    """The favourite and the boost of the account for each address that is an item of its timeline (the page shows the pressed state of its controls)."""
    out: dict[str, dict[str, bool]] = {}
    for start in range(0, len(iris), 500):
        part = iris[start:start + 500]
        for iri, favourited, boosted in client.execute(f"SELECT object_iri, favourited, boosted FROM federation_timeline_items WHERE account_id=? AND object_iri IN ({', '.join('?' for _ in part)})", (account_id, *part)).fetchall():
            out[str(iri)] = {"favourited": bool(favourited), "boosted": bool(boosted)}
    return out


def routes(ctx: AccountsContext) -> list[Route]:
    origin = ctx.config.public_origin.rstrip("/")
    prefix = f"{origin}/ap/objects/"

    def _number(object_iri: Any) -> int | None:
        if not isinstance(object_iri, str) or len(object_iri) > _MAX_IRI or not object_iri.startswith(prefix):
            return None
        number = object_iri[len(prefix):]
        return int(number) if number.isascii() and number.isdigit() else None

    def _actor(conn: Any, account_id: int) -> accounts_store.Account | None:
        try:
            account = accounts_store.AccountStore(conn).get(account_id)
        except accounts_store.AccountNotFound:
            return None
        return account if account is not None and actor_state(account) == "active" else None

    def _public_document_object(conn: Any, number: int) -> tuple[federation_store.FederationObject, federation_store.RenditionObject | None, accounts_store.Account] | None:
        """Return the published document object, optional rendition object, and active actor owner when public now."""
        owning = federation_store.owning_object(conn, number)
        if owning is None:
            return None
        obj, rendition = owning
        author = _actor(conn, obj.account_id)
        if author is None:
            return None
        row = conn.execute("SELECT 1 FROM knowledge_bases k JOIN documents d ON d.kb_id=k.id WHERE k.id=? AND k.is_public=1 AND d.id=? AND COALESCE(d.hidden, 0)=0", (obj.kb_id, obj.document_id)).fetchone()
        return None if row is None else (obj, rendition, author)

    def _note_in_thread(client: Any, note: notes_store.Note, note_iri: str, *, visible_targets: set[str], hidden_targets: set[str]) -> bool:
        """Return true when ``note`` reaches ``visible_targets`` within 16 parent hops without cycles or hidden opinions."""
        seen = {note_iri}
        parent_iri = note.in_reply_to_iri
        for _ in range(16):
            if parent_iri is None or parent_iri in hidden_targets:
                return False
            if parent_iri in visible_targets:
                return True
            if parent_iri in seen:
                return False
            seen.add(parent_iri)
            parent_num = _number(parent_iri)
            if parent_num is None:
                return False
            parent_note = notes_store.get_note(client, parent_num)
            if parent_note is None or parent_note.deleted_at is not None:
                return False
            parent_iri = parent_note.in_reply_to_iri
        return False

    def _local_item(conn: Any, client: Any, *, object_iri: str, target: tuple[federation_store.FederationObject, federation_store.RenditionObject | None, accounts_store.Account], iri: str, visible_opinions: set[str], hidden_opinions: set[str]) -> dict[str, Any] | None:
        """Build a timeline item only for the public target object or a non-deleted local note in its visible thread."""
        number = _number(iri)
        if number is None:
            return None
        if iri == object_iri:
            from ..federation_routes import _published_object_content

            obj, rendition, author = target
            document = conn.execute("SELECT title FROM documents WHERE id=? AND kb_id=?", (obj.document_id, obj.kb_id)).fetchone()
            token = permalinks.token_of(conn, "document", obj.document_id)
            url = None if token is None else permalinks.permalink_url(origin, token) + ("" if rendition is None else f"/renditions/{rendition.template}")
            title = str(document[0] or "") if document is not None else ""
            published_at = obj.published_at if rendition is None else rendition.created_at
            updated_at = obj.updated_at if rendition is None else rendition.updated_at
            item: dict[str, Any] = {"object_iri": iri, "url": url, "type": "Article", "name": title, "summary": None, "sensitive": False, "content_html": _published_object_content(dbm.latest_extraction_summary(conn, obj.document_id, kb_id=obj.kb_id), title, url), "language": None, "attachments": [], "context": None, "in_reply_to": None, "quote": None, "published_at": published_at, "updated_at": updated_at}
        else:
            note = notes_store.get_note(client, number)
            if note is None or note.deleted_at is not None:
                return None
            author = _actor(conn, note.account_id)
            if author is None or not _note_in_thread(client, note, iri, visible_targets={object_iri, *visible_opinions}, hidden_targets=hidden_opinions):
                return None
            item = notes_store.note_timeline_item(note, object_iri=iri, author={}, context_iri=None)
        item["author"] = {"iri": f"{origin}/ap/actors/{author.public_id}", "name": author.display_name or None, "handle": f"@{author.username}@{ctx.config.expected_authority}"}
        return item

    async def item_post(request: Request) -> Response:
        principal = request_principal(request)
        if principal.scope != "owner" or principal.account_id is None:
            return not_found()
        account_id = principal.account_id
        body = await read_json_body(request)
        if not isinstance(body, dict) or set(body) != {"object", "opinion"} or not isinstance(body.get("opinion"), str) or len(body["opinion"]) > _MAX_IRI:
            return json_error(400, "invalid_request")
        number = _number(body.get("object"))
        if number is None:
            return json_error(404, "opinion_not_found")
        object_iri, opinion_iri = str(body["object"]), str(body["opinion"])

        def _execute() -> tuple[int, dict[str, Any]]:
            conn = open_db(ctx)
            client = open_client_db(ctx)
            try:
                if _actor(conn, account_id) is None:
                    return 409, {"error": "not_actor"}
                target = _public_document_object(conn, number)
                if target is None:
                    return 404, {"error": "opinion_not_found"}
                all_opinions = opinions_store.opinions_of(client, object_iri, include_hidden=True)
                opinion = next((o for o in all_opinions if o.opinion_iri == opinion_iri), None)
                if opinion is not None and opinion.hidden_at is not None:
                    return 404, {"error": "opinion_not_found"}
                if opinion is None:
                    # RD30: a note of this server in the thread, or the post of the page, is an item as a post of another server.
                    visible_opinions = {o.opinion_iri for o in all_opinions if o.hidden_at is None}
                    hidden_opinions = {o.opinion_iri for o in all_opinions if o.hidden_at is not None}
                    local = _local_item(conn, client, object_iri=object_iri, target=target, iri=opinion_iri, visible_opinions=visible_opinions, hidden_opinions=hidden_opinions)
                    if local is None:
                        return 404, {"error": "opinion_not_found"}
                    client_store.upsert_timeline_item(client, account_id=account_id, item=local, announced_by=None, in_timeline=False, now=time.time())
                    row = client.execute("SELECT id, favourited, boosted FROM federation_timeline_items WHERE account_id=? AND object_iri=?", (account_id, opinion_iri)).fetchone()
                    client.commit()
                    return 200, {"item_id": str(row[0]), "favourited": bool(row[1]), "boosted": bool(row[2])}
                hosts = [federation_store.iri_host(iri) for iri in (opinion.opinion_iri, opinion.author_iri)]
                if any(host is None or federation_store.blocking_domain(conn, host) is not None for host in hosts):
                    return 400, {"error": "blocked_domain"}
                item = {
                    "object_iri": opinion.opinion_iri, "url": opinion.url, "type": "Note",
                    "author": {"iri": opinion.author_iri, "name": opinion.author_name, "handle": opinion.author_handle},
                    "name": None, "summary": opinion.summary, "sensitive": opinion.sensitive, "content_html": opinion.content_html, "language": opinion.language,
                    "attachments": opinion.attachments, "context": None, "in_reply_to": None if opinion.kind == "quote" else opinion.parent_iri,
                    "quote": object_iri if opinion.kind == "quote" else None, "published_at": opinion.published, "updated_at": opinion.updated,
                }
                client_store.upsert_timeline_item(client, account_id=account_id, item=item, announced_by=None, in_timeline=False, now=time.time())
                row = client.execute("SELECT id, favourited, boosted FROM federation_timeline_items WHERE account_id=? AND object_iri=?", (account_id, opinion.opinion_iri)).fetchone()
                client.commit()
                return 200, {"item_id": str(row[0]), "favourited": bool(row[1]), "boosted": bool(row[2])}
            finally:
                client.close()
                conn.close()

        status, payload = await asyncio.to_thread(_execute)
        return JSONResponse(payload, status_code=status, headers=_NO_STORE)

    async def object_opinions_post(request: Request) -> Response:
        principal = request_principal(request)
        if principal.scope != "owner" or principal.account_id is None:
            return not_found()
        account_id = principal.account_id
        raw = str(request.path_params.get("object_id", ""))
        if not (raw.isascii() and raw.isdigit() and len(raw) <= 20):
            return json_error(404, "object_not_found")

        def _execute() -> tuple[int, dict[str, Any]]:
            conn = open_db(ctx)
            try:
                obj = federation_store.get_object(conn, int(raw))
            finally:
                conn.close()
            if obj is None or obj.account_id != account_id:
                return 404, {"error": "object_not_found"}
            from ...federation import opinions as opinions_mod

            client = open_client_db(ctx)
            try:
                opinions_store.request_fetch(client, f"{prefix}{obj.public_id}", own=True, object_published=obj.published_at, now=time.time())
                client.commit()
                fetch = opinions_store.get_fetch(client, f"{prefix}{obj.public_id}")
            finally:
                client.close()
            if fetch is None:
                return 200, {"refresh": None}
            status = "running" if fetch.status == "pending" else fetch.status
            return 200, {"refresh": {"id": str(fetch.id), "status": status, "retry": opinions_mod.REFRESH_RETRY_SECONDS if status == "running" else 0, "result_count": fetch.result_count}}

        status, payload = await asyncio.to_thread(_execute)
        return JSONResponse(payload, status_code=status, headers=_NO_STORE)

    return [Route(OPINION_ITEM_PATH, item_post, methods=["POST"]), Route(OBJECT_OPINIONS_PATH, object_opinions_post, methods=["POST"])]
