"""Routes of the quote requests of the federation client (FEP-044f; FEDERATION_REMOTE_READING_DESIGN.md Sections 5.4 rule 6 and 6.6 rule 5; unit FP4-9Q).

For the owner of the account, in both server modes (KNOWLEDGE_FEDERATION_DESIGN.md Section 15 rule 5).

- ``GET /api/federation/quotes``: the requests of remote actors to quote an own object: those that wait for the owner (``requests``) and the approved ones (``authorizations``).
- ``PATCH /api/federation/quotes/{quote_id}``: the decision of the owner. ``approved`` or ``refused`` for a request that waits, ``revoked`` for an approved one. The sidecar sends ``Accept`` with the ``QuoteAuthorization``, ``Reject``, or ``Delete`` of the authorization.
- ``PATCH /api/federation/objects/{object_id}``: the quote policy of a published document. A change sends ``Update`` of the object.

The quote policy of an own note is a field of ``/api/federation/notes`` (``federation_notes``).

The view of a request has addresses of remote servers only (the quoting object and its actor), and no content of them: Claire does not fetch the quoting object.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from ...core import ids
from ...store import accounts as accounts_store
from ...store import federation as federation_store
from ...store import federation_notes as notes_store
from ...store import federation_quotes as quotes_store
from ..principal import request_principal
from ..security import read_json_body
from .common import AccountsContext, json_error, not_found, open_client_db, open_db
from .console_federation_panels import actor_state, content_public

FEDERATION_QUOTES_API_PATH = "/api/federation/quotes"
FEDERATION_OBJECTS_API_PATH = "/api/federation/objects"
_NO_STORE = {"Cache-Control": "no-store"}
# The largest number of requests of each group in one answer.
QUOTES_LIST_MAX = 100
# The longest text of the quoted object in the view of a request.
_TEXT_MAX = 200
_OWNER_STATES = ("approved", "refused", "revoked")


def routes(ctx: AccountsContext) -> list[Route]:
    origin = ctx.config.public_origin

    def _owner_account_id(request: Request) -> int | None:
        principal = request_principal(request)
        return principal.account_id if principal.scope == "owner" and principal.account_id is not None else None

    def _object_view(conn: Any, client: Any, public_id: int) -> dict[str, Any]:
        """The own object of a request: a published document (its title), a note (the start of its text), or an object that is gone."""
        view: dict[str, Any] = {"id": str(public_id), "url": f"{origin}/ap/objects/{public_id}", "kind": "gone", "text": ""}
        obj = federation_store.get_object(conn, public_id)
        if obj is not None:
            title = conn.execute("SELECT title FROM documents WHERE id=? AND kb_id=?", (obj.document_id, obj.kb_id)).fetchone()
            view.update(kind="document", text=str((title[0] if title else "") or "")[:_TEXT_MAX], kb_id=obj.kb_id, document_id=obj.document_id)
            return view
        note = notes_store.get_note(client, public_id)
        if note is not None and note.deleted_at is None:
            view.update(kind="note", text=note.content_text[:_TEXT_MAX])
        return view

    def _quote_view(conn: Any, client: Any, row: quotes_store.QuoteAuthorization) -> dict[str, Any]:
        return {
            "id": str(row.id), "state": row.state, "object": _object_view(conn, client, row.object_public_id), "quoting_object": row.quoting_object_iri, "quoting_actor": row.quoting_actor_iri,
            # The public address of the authorization of an approved request.
            "authorization": f"{origin}/ap/quote-authorizations/{row.id}" if row.state == "approved" else None, "created_at": row.created_at, "updated_at": row.updated_at,
        }

    async def _run(execute: Any) -> Response:
        result = await asyncio.to_thread(execute)
        if result is None:
            return not_found()
        return JSONResponse(result[1], status_code=result[0], headers=_NO_STORE)

    async def quotes_get(request: Request) -> Response:
        account_id = _owner_account_id(request)
        if account_id is None:
            return not_found()

        def _execute() -> tuple[int, dict[str, Any]] | None:
            conn = open_db(ctx)
            client = open_client_db(ctx)
            try:
                pending = quotes_store.list_authorizations(client, account_id, states=("pending",), limit=QUOTES_LIST_MAX)
                approved = quotes_store.list_authorizations(client, account_id, states=("approved",), limit=QUOTES_LIST_MAX)
                return 200, {
                    "requests": [_quote_view(conn, client, row) for row in pending],
                    "authorizations": [_quote_view(conn, client, row) for row in approved],
                    # The numbers of all rows, also above the list limit.
                    "counts": {"pending": quotes_store.count_authorizations(client, account_id, "pending"), "approved": quotes_store.count_authorizations(client, account_id, "approved")},
                }
            finally:
                client.close()
                conn.close()

        return await _run(_execute)

    async def quotes_patch(request: Request) -> Response:
        # Same-origin rule for a cookie request: ``AuthenticationMiddleware``.
        account_id = _owner_account_id(request)
        if account_id is None:
            return not_found()
        body = await read_json_body(request)
        if set(body) != {"state"} or body["state"] not in _OWNER_STATES:
            return json_error(400, "invalid_request", fields=["state"])
        raw_id = request.path_params["quote_id"]
        if not (raw_id.isascii() and raw_id.isdigit() and raw_id[0] != "0" and len(raw_id) <= 18):
            return json_error(404, "quote_not_found")

        def _execute() -> tuple[int, dict[str, Any]] | None:
            conn = open_db(ctx)
            client = open_client_db(ctx)
            try:
                store = accounts_store.AccountStore(conn)
                # The answer goes out with the key of the actor (Section 5.4 rule 11).
                account = store.get(account_id)
                if actor_state(account) != "active":
                    return 409, {"error": "not_actor"}
                row = quotes_store.get_authorization(client, int(raw_id))
                # A request of another account gives the same answer as no request.
                if row is None or row.account_id != account_id:
                    return 404, {"error": "quote_not_found"}
                # An approval of a published document makes a public authorization for private knowledge base content (decisions GD5 and GD7). Own notes work for each actor, and refusals or revocations remove authorizations.
                if body["state"] == "approved" and federation_store.get_object(conn, row.object_public_id) is not None and not content_public(account):
                    return 409, {"error": "profile_private"}
                # The state and the task of the answer are in one transaction.
                changed = quotes_store.set_authorization_state(client, row.id, body["state"], now=time.time())
                if changed is None:
                    return 409, {"error": "invalid_state", "state": row.state}
                client.commit()
                return 200, {"quote": _quote_view(conn, client, changed)}
            finally:
                client.close()
                conn.close()

        return await _run(_execute)

    async def object_patch(request: Request) -> Response:
        account_id = _owner_account_id(request)
        if account_id is None:
            return not_found()
        body = await read_json_body(request)
        if set(body) != {"quote_policy"}:
            return json_error(400, "invalid_request", fields=["quote_policy"])
        if body["quote_policy"] not in quotes_store.QUOTE_POLICIES:
            return json_error(400, "invalid_quote_policy")

        def _execute() -> tuple[int, dict[str, Any]] | None:
            conn = open_db(ctx)
            try:
                try:
                    obj = federation_store.get_object(conn, ids.parse_public_id(request.path_params["object_id"]))
                except ValueError:
                    obj = None
                if obj is None or obj.account_id != account_id:
                    return 404, {"error": "object_not_found"}
                # The policy and the ``update`` task are in one transaction of claire.db (Section 5.4 rule 6). The task of an account that is not an actor waits.
                federation_store.set_object_quote_policy(conn, obj, body["quote_policy"], now=time.time())
                conn.commit()
                return 200, {"object": {"id": str(obj.public_id), "kb_id": obj.kb_id, "document_id": obj.document_id, "quote_policy": body["quote_policy"]}}
            finally:
                conn.close()

        return await _run(_execute)

    return [
        Route(FEDERATION_QUOTES_API_PATH, quotes_get, methods=["GET"]),
        Route(FEDERATION_QUOTES_API_PATH + "/{quote_id}", quotes_patch, methods=["PATCH"]),
        Route(FEDERATION_OBJECTS_API_PATH + "/{object_id}", object_patch, methods=["PATCH"]),
    ]
