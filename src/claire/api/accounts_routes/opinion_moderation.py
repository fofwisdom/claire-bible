"""Moderation of the opinions of a published object (DOCUMENT_RENDITIONS_AND_PUBLICATION_DESIGN.md Section 5.11 D5, phase E7c).

``POST /api/federation/opinions/hide`` with the JSON body ``{"object", "opinion", "hidden"}``:

- ``object``: the address of an own published object (a document object or a rendition object).
- ``opinion``: the address of one opinion of that object.
- ``hidden``: ``true`` hides the opinion from the page and from the ``replies`` collection; ``false`` shows it again.

The caller is an account with the scope ``owner``: the author of the object or a server admin. This is moderation of the page, not a refusal of a quote: the stamp of a quote stays valid (Section 5.7). The answer is ``{"hidden"}``. Refusals: 400 ``invalid_request``; 404 ``opinion_not_found`` for an object that is not an own published object, for a caller who is not the author or an admin, and for an opinion that the object does not have.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from ...store import federation as federation_store
from ...store import federation_opinions as opinions_store
from ..principal import request_principal
from ..security import read_json_body
from .common import AccountsContext, json_error, not_found, open_client_db, open_db

__all__ = ["OPINION_HIDE_PATH", "routes"]

OPINION_HIDE_PATH = "/api/federation/opinions/hide"
_NO_STORE = {"Cache-Control": "no-store"}
_MAX_IRI = 2048


def routes(ctx: AccountsContext) -> list[Route]:
    origin = ctx.config.public_origin

    async def hide_post(request: Request) -> Response:
        principal = request_principal(request)
        if principal.scope != "owner" or principal.account_id is None:
            return not_found()
        body = await read_json_body(request)
        object_iri, opinion_iri, hidden = (body.get("object"), body.get("opinion"), body.get("hidden")) if isinstance(body, dict) else (None, None, None)
        if not isinstance(object_iri, str) or not isinstance(opinion_iri, str) or not isinstance(hidden, bool) or len(object_iri) > _MAX_IRI or len(opinion_iri) > _MAX_IRI or set(body) - {"object", "opinion", "hidden"}:
            return json_error(400, "invalid_request")
        prefix = f"{origin.rstrip('/')}/ap/objects/"
        number = object_iri[len(prefix):] if object_iri.startswith(prefix) else ""
        if not (number.isascii() and number.isdigit()):
            return json_error(404, "opinion_not_found")

        def _execute() -> tuple[int, dict[str, Any]]:
            conn = open_db(ctx)
            try:
                owning = federation_store.owning_object(conn, int(number))
            finally:
                conn.close()
            if owning is None or (owning[0].account_id != principal.account_id and principal.server_role != "admin"):
                return 404, {"error": "opinion_not_found"}
            client = open_client_db(ctx)
            try:
                if not opinions_store.hide(client, object_iri, opinion_iri, hidden_by=principal.account_id, hidden=hidden, now=time.time()):
                    return 404, {"error": "opinion_not_found"}
                client.commit()
                return 200, {"hidden": hidden}
            finally:
                client.close()

        status, payload = await asyncio.to_thread(_execute)
        return JSONResponse(payload, status_code=status, headers=_NO_STORE)

    return [Route(OPINION_HIDE_PATH, hide_post, methods=["POST"])]
