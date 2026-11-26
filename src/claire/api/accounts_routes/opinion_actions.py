"""Actions of a signed-in account in the 의견 tab (DOCUMENT_RENDITIONS_AND_PUBLICATION_DESIGN.md Section 5.6 rule 4 and Section 5.11 D3, D4, and D9, phase E7d).

The caller is an account with the scope ``owner`` that is a federation actor (else 409 ``not_actor``).

- ``POST /api/federation/opinions/item`` with ``{"object", "opinion"}``: the opinion as an item of the timeline of the account, so that the functions of the federation client work on it (favourite, boost, a reply with ``in_reply_to``, and a quote with ``quote_of``; Sections 25 and 26 of ``FEDERATION.md``). The item is not in the home stream (``in_timeline`` 0). The answer is ``{"item_id", "favourited", "boosted"}``. Refusals: 404 ``opinion_not_found`` (also a hidden opinion, and an object that is not an own published object), 400 ``blocked_domain``.
- ``POST /api/federation/objects/{object_id}/opinions``: the view of a published object of the caller in ``/syndication`` asks for a fetch of its opinions (Section 5.11 B1), with the conditions of rule B2. The answer is ``{"refresh"}``: the state of the fetch, or ``null`` when no fetch was due and none exists. It needs no actor: the server actor signs the fetch. Refusal: 404 ``object_not_found`` for an object that is not a published document object of the caller.
- ``POST /api/federation/opinions/like`` with ``{"object", "on"}``: the like of the post (Section 5.6 rule 4). The post is a published document; its object is on this server, so the like is a row of the reactions of the object, with the actor of the account and no activity. The answer is ``{"liked", "likes"}``. Refusals: 404 ``post_not_found`` (also a rendition object: it is not a post, Section 5.6 rule 2), 409 ``own_post`` (Section 5.6 rule 3).
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from ...store import accounts as accounts_store
from ...store import federation as federation_store
from ...store import federation_client as client_store
from ...store import federation_opinions as opinions_store
from ..principal import request_principal
from ..security import read_json_body
from .common import AccountsContext, json_error, not_found, open_client_db, open_db
from .console_federation_panels import actor_state

__all__ = ["OBJECT_OPINIONS_PATH", "OPINION_ITEM_PATH", "POST_LIKE_PATH", "item_states", "local_like", "routes"]

OPINION_ITEM_PATH = "/api/federation/opinions/item"
POST_LIKE_PATH = "/api/federation/opinions/like"
OBJECT_OPINIONS_PATH = "/api/federation/objects/{object_id}/opinions"
_NO_STORE = {"Cache-Control": "no-store"}
_MAX_IRI = 2048


def local_like(client: Any, *, object_public_id: int, actor_iri: str) -> bool:
    """True when the actor of an account of this server likes the object."""
    return client.execute("SELECT 1 FROM federation_object_reactions WHERE object_public_id=? AND kind='like' AND actor_iri=?", (object_public_id, actor_iri)).fetchone() is not None


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
        account = accounts_store.AccountStore(conn).get(account_id)
        return account if account is not None and actor_state(account) == "active" else None

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
                if federation_store.owning_object(conn, number) is None:
                    return 404, {"error": "opinion_not_found"}
                opinion = next((o for o in opinions_store.opinions_of(client, object_iri) if o.opinion_iri == opinion_iri), None)
                if opinion is None:
                    return 404, {"error": "opinion_not_found"}
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

    async def like_post(request: Request) -> Response:
        principal = request_principal(request)
        if principal.scope != "owner" or principal.account_id is None:
            return not_found()
        account_id = principal.account_id
        body = await read_json_body(request)
        if not isinstance(body, dict) or set(body) != {"object", "on"} or not isinstance(body.get("on"), bool):
            return json_error(400, "invalid_request")
        number = _number(body.get("object"))
        if number is None:
            return json_error(404, "post_not_found")
        on = bool(body["on"])

        def _execute() -> tuple[int, dict[str, Any]]:
            conn = open_db(ctx)
            client = open_client_db(ctx)
            try:
                account = _actor(conn, account_id)
                if account is None:
                    return 409, {"error": "not_actor"}
                # A rendition object is not a post: only a document object has a like (Section 5.6 rule 2).
                post = federation_store.get_object(conn, number)
                if post is None:
                    return 404, {"error": "post_not_found"}
                if post.account_id == account_id:
                    return 409, {"error": "own_post"}
                actor_iri = f"{origin}/ap/actors/{account.public_id}"
                if on:
                    client.execute(
                        "INSERT INTO federation_object_reactions (object_public_id, account_id, kind, actor_iri, activity_iri, received_at) VALUES (?, ?, 'like', ?, ?, ?) ON CONFLICT(object_public_id, kind, actor_iri) DO NOTHING",
                        (number, post.account_id, actor_iri, f"{actor_iri}#likes/local-{number}", time.time()),
                    )
                else:
                    client.execute("DELETE FROM federation_object_reactions WHERE object_public_id=? AND kind='like' AND actor_iri=?", (number, actor_iri))
                client.commit()
                likes = client_store.count_object_reactions(client, [number])[number]["like"]
                return 200, {"liked": on, "likes": likes}
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

    return [Route(OPINION_ITEM_PATH, item_post, methods=["POST"]), Route(POST_LIKE_PATH, like_post, methods=["POST"]), Route(OBJECT_OPINIONS_PATH, object_opinions_post, methods=["POST"])]
