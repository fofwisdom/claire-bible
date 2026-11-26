"""+1 of an account for a rendition (DOCUMENT_RENDITIONS_AND_PUBLICATION_DESIGN.md Section 5.12, phase E9b).

``POST /api/renditions/plus-one`` with the JSON body ``{"token", "rendition", "on"}``:

- ``token``: the address token of the document whose Silvercord page shows the rendition.
- ``rendition``: a template (``standard``, ``concise``, ``academic``) for a rendition of that document, or ``remote:{key}`` for a cached rendition of another Claire server with the same source.
- ``on``: ``true`` gives the +1, ``false`` takes it back.

The caller is an account with the scope ``owner`` that is a federation actor. The answer is ``{"on", "score"}``: the state of the mark and the score that the page shows. Refusals: 404 ``rendition_not_found`` (also a document that is not published), 409 ``not_actor``, 409 ``own_rendition``, 429 ``rate_limited``, 400 ``blocked_domain``.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from ...extract.rendition_templates import PRIMARY_RENDITION_TEMPLATE, RENDITION_TEMPLATE_IDS
from ...federation import plus_one_marks, remote_renditions
from ...store import accounts as accounts_store
from ...store import federation as federation_store
from ...store import permalinks
from ...store import plus_ones
from ..principal import request_principal
from ..security import read_json_body
from .common import AccountsContext, json_error, not_found, open_db
from .console_federation_panels import actor_state

__all__ = ["PLUS_ONE_API_PATH", "local_rendition_object", "remote_score", "routes"]

PLUS_ONE_API_PATH = "/api/renditions/plus-one"
_NO_STORE = {"Cache-Control": "no-store"}
_REFUSAL_STATUS = {"own_rendition": 409, "rate_limited": 429, "blocked_domain": 400}


def local_rendition_object(conn: Any, *, kb_id: int, document_id: str, template: str) -> tuple[int, int] | None:
    """The object of a rendition of a document of this server that is public now: (its number, the account of its author), or ``None``. The document object is the 대표 정리본."""
    obj = federation_store.get_object_for_document(conn, kb_id=kb_id, document_id=document_id)
    if obj is None:
        return None
    public = conn.execute(
        "SELECT 1 FROM documents d JOIN knowledge_bases k ON k.id = d.kb_id JOIN accounts a ON a.id = ? WHERE d.id=? AND d.kb_id=? AND COALESCE(d.hidden, 0)=0 AND k.is_public=1 AND a.status='active' AND a.username IS NOT NULL",
        (obj.account_id, document_id, kb_id),
    ).fetchone()
    if public is None:
        return None
    if template == PRIMARY_RENDITION_TEMPLATE:
        return obj.public_id, obj.account_id
    found = next((r for r in federation_store.rendition_objects_of(conn, obj.public_id) if r.template == template), None)
    return None if found is None else (found.public_id, obj.account_id)


def remote_score(conn: Any, rendition: Any, account_id: int | None) -> tuple[int, bool]:
    """The score of a cached rendition of another server for a reader, and if the account of the reader has a mark. The score is the count of the origin (Section 5.12 rule 10); a mark of the account that is newer than the last read of the object counts at once (rule 11)."""
    marked_at = None if account_id is None else plus_one_marks.has_remote_mark(conn, account_id, rendition.iri)
    return rendition.plus_score + (1 if marked_at is not None and marked_at > rendition.fetched_at else 0), marked_at is not None


def routes(ctx: AccountsContext) -> list[Route]:
    origin = ctx.config.public_origin

    async def plus_one_post(request: Request) -> Response:
        principal = request_principal(request)
        if principal.scope != "owner" or principal.account_id is None:
            return not_found()
        account_id = principal.account_id
        body = await read_json_body(request)
        token, rendition, on = (body.get("token"), body.get("rendition"), body.get("on")) if isinstance(body, dict) else (None, None, None)
        if not isinstance(token, str) or not permalinks.plausible_token(token) or not isinstance(rendition, str) or not isinstance(on, bool) or set(body) - {"token", "rendition", "on"}:
            return json_error(400, "invalid_request")

        def _execute() -> tuple[int, dict[str, Any]]:
            conn = open_db(ctx)
            try:
                account = accounts_store.AccountStore(conn).get(account_id)
                if actor_state(account) != "active":
                    return 409, {"error": "not_actor"}
                found = permalinks.resolve(conn, token)
                if found is None or found.kind != "document" or found.alias is not None or found.gone_at is not None:
                    return 404, {"error": "rendition_not_found"}
                kb_id, document_id = int(found.kb_id or 0), str(found.target)
                now = time.time()
                try:
                    if rendition in RENDITION_TEMPLATE_IDS:
                        target = local_rendition_object(conn, kb_id=kb_id, document_id=document_id, template=rendition)
                        if target is None:
                            return 404, {"error": "rendition_not_found"}
                        public_id, author_account_id = target
                        if on:
                            plus_one_marks.give_local(conn, account_id=account_id, account_public_id=account.public_id, object_public_id=public_id, author_account_id=author_account_id, origin=origin, now=now)
                        else:
                            plus_one_marks.take_back_local(conn, account_public_id=account.public_id, object_public_id=public_id, origin=origin)
                        conn.commit()
                        return 200, {"on": on, "score": plus_ones.counts(conn, public_id)["score"]}
                    if not rendition.startswith("remote:"):
                        return 400, {"error": "invalid_request"}
                    row = conn.execute("SELECT source_key FROM documents WHERE id=? AND kb_id=?", (document_id, kb_id)).fetchone()
                    copy = None if row is None or not row[0] else next((r for r in remote_renditions.renditions_of(conn, str(row[0]), now=now) if r.key == rendition[7:]), None)
                    if copy is None:
                        return 404, {"error": "rendition_not_found"}
                    if on:
                        plus_one_marks.give_remote(conn, account_id=account_id, account_public_id=account.public_id, rendition=copy, origin=origin, now=now)
                    else:
                        plus_one_marks.take_back_remote(conn, account_id=account_id, account_public_id=account.public_id, rendition_iri=copy.iri, origin=origin, now=now)
                    conn.commit()
                    return 200, {"on": on, "score": remote_score(conn, copy, account_id)[0]}
                except plus_one_marks.MarkRefused as refused:
                    conn.rollback()
                    return _REFUSAL_STATUS[refused.code], {"error": refused.code}
            finally:
                conn.close()

        status, payload = await asyncio.to_thread(_execute)
        return JSONResponse(payload, status_code=status, headers=_NO_STORE)

    return [Route(PLUS_ONE_API_PATH, plus_one_post, methods=["POST"])]
