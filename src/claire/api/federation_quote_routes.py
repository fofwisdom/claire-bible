"""Internal federation API of quotes (FEP-044f; contract 1.7; FEDERATION_REMOTE_READING_DESIGN.md Sections 5.4 rules 5 and 6, 6.6 rule 5, and the result RT9; unit FP4-9Q).

``federation_routes.federation_internal_routes`` adds these routes to its table:

- ``POST /quote-events``: one verified inbound activity of the quote protocol. ``QuoteRequest`` for an own object: Claire applies the quote policy of the object. ``Accept`` or ``Reject`` of a quote request of an own note: Claire changes the quote state of the note.
- ``GET /quote-authorizations/{id}``: the record of an approved request. The sidecar makes the ``QuoteAuthorization`` of the public path from it. A revoked authorization gives 410.

All input of these routes comes from remote servers through the sidecar. The rules:

- The domain block applies to the actor and to the quoting object.
- A ``QuoteRequest`` must name a quoting object on the host of its actor.
- Only the author of the quoted object changes the quote state of a note (``Accept``, ``Reject``). An ``Accept`` must carry a ``QuoteAuthorization`` on the origin of that author, and the authorization must name the quoting note and the quoted object.
- A revoke is the ``Delete`` of the authorization by that author; it comes as a timeline event (``federation_routes``). Claire never changes a quote state from a lookup (RT9).

The models are sealed, as in ``federation_routes``. The store functions are in ``store.federation_quotes``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import Field, ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import BaseRoute, Route

from ..store import db as dbm
from ..store import federation as federation_store
from ..store import federation_client as client_store
from ..store import federation_notes as notes_store
from ..store import federation_quotes as quotes_store
from ..notifications import events as notification_events
from .federation_routes import _MAX_IRI_LENGTH, _NO_STORE, MENTION_RATE_WINDOW_SECONDS, InboxActor, _error, _json, _local_object_public_id, _not_found, _remote_host, _Sealed

__all__ = ["QUOTE_REQUEST_FRAGMENT", "QuoteAuthorizationRecord", "QuoteEventRequest", "QuoteEventResponse", "quote_internal_routes", "quoted_object"]

_log = logging.getLogger("claire.federation.quotes")

# The ID of the QuoteRequest of an own note is ``{object}#quote-request``. An ``Accept`` or a ``Reject`` refers to the request by this ID (RT9: the sidecar reads ``objectId`` and does not fetch the object).
QUOTE_REQUEST_FRAGMENT = "#quote-request"


class QuoteAuthorizationBody(_Sealed):
    """The ``QuoteAuthorization`` in ``result`` of an ``Accept``, as the sidecar read it. ``interacting_object`` is the quoting object, and ``interaction_target`` is the quoted object."""

    iri: str = Field(min_length=1, max_length=_MAX_IRI_LENGTH)
    interacting_object: str | None = Field(min_length=1, max_length=_MAX_IRI_LENGTH)
    interaction_target: str | None = Field(min_length=1, max_length=_MAX_IRI_LENGTH)
    attributed_to: str | None = Field(min_length=1, max_length=_MAX_IRI_LENGTH)


class QuoteEventRequest(_Sealed):
    """One verified inbound activity of the quote protocol.

    ``QuoteRequest``: ``object_iri`` is the quoted object (an object of this server), ``instrument_iri`` is the quoting object, and ``authorization`` is ``None``. ``Accept``, ``Reject``: ``object_iri`` is the ID of the quote request that the activity answers, ``instrument_iri`` is ``None``, and ``authorization`` is the ``result`` of an ``Accept``.
    """

    event_id: str = Field(min_length=1, max_length=_MAX_IRI_LENGTH)
    type: Literal["QuoteRequest", "Accept", "Reject"]
    recipient_public_id: str | None = Field(pattern=r"^[1-9][0-9]{0,18}$")
    actor: InboxActor
    object_iri: str = Field(min_length=1, max_length=_MAX_IRI_LENGTH)
    instrument_iri: str | None = Field(min_length=1, max_length=_MAX_IRI_LENGTH)
    authorization: QuoteAuthorizationBody | None
    verified_by: Literal["http-signature", "ld-signature", "object-integrity-proof"]


class QuoteEventResponse(_Sealed):
    decision: Literal["accepted", "rejected", "ignored"]
    # ``quote_refused``: the policy of the object refuses the request, and a ``quote_reject`` task sends the ``Reject``. ``no_quote``: the answer is for a note that has no quote now. ``not_the_author``: the answer does not come from the author of the quoted object.
    reason: Literal["invalid_actor", "blocked_domain", "no_object", "invalid_object", "recipient_mismatch", "rate_limited", "quote_refused", "no_quote", "not_the_author", "invalid_authorization", "unsupported_type"] | None
    # The state of the request or of the quote after the event, or ``None`` when the event changed nothing.
    state: Literal["pending", "approved", "refused", "revoked"] | None


class QuoteAuthorizationRecord(_Sealed):
    """The canonical form of one approved quote request. The sidecar makes the ``QuoteAuthorization`` from it: ``attributedTo`` is the actor, ``interactionTarget`` is the object ``object_public_id`` of this server, and ``interactingObject`` is ``quoting_object_iri``. The quoting object is not in the record (FEP-044f: the authorization does not embed it)."""

    id: str
    actor_public_id: str
    object_public_id: str
    quoting_object_iri: str


def quoted_object(conn: Any, client: Any, public_id: int, *, actor_ids: list[int]) -> tuple[int, str] | None:
    """The account and the quote policy of an own object that is public now: a published document (its account is in ``actor_ids``, its theme is public, and its document is not hidden) or a note that is not deleted (its account is in ``actor_ids``). If not, ``None``."""
    obj = federation_store.get_object(conn, public_id)
    if obj is not None:
        theme = conn.execute("SELECT is_public FROM knowledge_bases WHERE id=?", (obj.kb_id,)).fetchone()
        document = dbm.get_document_row(conn, obj.document_id, kb_id=obj.kb_id)
        if obj.account_id not in actor_ids or theme is None or not theme["is_public"] or document is None or document["hidden"]:
            return None
        return obj.account_id, federation_store.DOCUMENT_QUOTE_POLICY
    note = notes_store.get_note(client, public_id)
    if note is None or note.deleted_at is not None or note.account_id not in actor_ids:
        return None
    return note.account_id, note.quote_policy


def _same_origin(first: str, second: str) -> bool:
    try:
        a, b = urlsplit(first), urlsplit(second)
    except ValueError:
        return False
    return bool(a.netloc) and (a.scheme.lower(), a.netloc.lower()) == (b.scheme.lower(), b.netloc.lower())


def quote_internal_routes(settings: Any, *, now: Callable[[], float], origin: Callable[[], str], open_store: Callable[[], Any], open_client: Callable[[], Any], actor_account_ids: Callable[[Any], list[int]]) -> list[BaseRoute]:
    """The routes of this module. The callables are the helpers of ``federation_internal_routes``. ``actor_account_ids`` gives all actors (FEDERATION_GO_LIVE_DESIGN.md decision GD8)."""

    def answer(decision: Any, reason: Any = None, state: Any = None) -> QuoteEventResponse:
        return QuoteEventResponse(decision=decision, reason=reason, state=state)

    def _request(store: Any, client: Any, canonical: str, event: QuoteEventRequest) -> QuoteEventResponse:
        """A ``QuoteRequest`` for an own object (Section 6.6 rule 5): apply the quote policy of the object."""
        public_id = _local_object_public_id(canonical, event.object_iri)
        target = None if public_id is None else quoted_object(store.conn, client, public_id, actor_ids=actor_account_ids(store))
        if public_id is None or target is None:
            return answer("ignored", "no_object")
        account_id, policy = target
        # The quoting object must be on the host of the actor that asks. Claire does not fetch it.
        actor_host = _remote_host(event.actor.iri, canonical)
        if event.instrument_iri is None or _remote_host(event.instrument_iri, canonical) != actor_host:
            return answer("rejected", "invalid_object")
        if event.recipient_public_id is not None and event.recipient_public_id != str(store.get(account_id).public_id):
            return answer("rejected", "recipient_mismatch")
        new_request = quotes_store.find_authorization(client, public_id, event.instrument_iri) is None
        if new_request:
            # RS9: the requests of one remote domain for one account have the limit of the mentions (``mention_rate_per_hour``).
            rate = int(client_store.get_reading_settings(store.conn)["mention_rate_per_hour"])
            recent = quotes_store.recent_request_actors(client, account_id, since=now() - MENTION_RATE_WINDOW_SECONDS)
            if sum(1 for actor in recent if _remote_host(actor, canonical) == actor_host) >= rate:
                return answer("ignored", "rate_limited")
        follower = federation_store.get_follower(store.conn, account_id, event.actor.iri) is not None
        row = quotes_store.decide_request(client, account_id=account_id, object_public_id=public_id, quoting_object_iri=event.instrument_iri, quoting_actor_iri=event.actor.iri, request_iri=event.event_id, policy=policy, follower=follower, now=now())
        if row is None:
            return answer("rejected", "invalid_object")
        if new_request and row.state == "pending":
            # The request waits for the manual approval of the owner (PWA_NOTIFICATION_DESIGN.md kind ``ap_quote_request``).
            notification_events.ap_quote_request(settings.db_file, account_id=account_id, actor_iri=event.actor.iri)
        return answer("accepted", None, row.state) if row.state in ("approved", "pending") else answer("rejected", "quote_refused", row.state)

    def _answer(client: Any, canonical: str, event: QuoteEventRequest) -> QuoteEventResponse:
        """``Accept`` or ``Reject`` of the quote request of an own note (Section 5.4 rule 5)."""
        note_iri, _, fragment = event.object_iri.partition("#")
        public_id = _local_object_public_id(canonical, note_iri) if f"#{fragment}" == QUOTE_REQUEST_FRAGMENT else None
        if public_id is None:
            return answer("ignored", "unsupported_type")
        note = notes_store.get_note(client, public_id)
        if note is None or note.deleted_at is not None or note.quote_of_iri is None:
            return answer("ignored", "no_quote")
        if note.quote_author_iri != event.actor.iri:
            return answer("rejected", "not_the_author")
        authorization = None
        if event.type == "Accept":
            body = event.authorization
            # The authorization is on the origin of the author, names the quoting note and the quoted object, and has no other owner.
            if body is None or _remote_host(body.iri, canonical) is None or not _same_origin(body.iri, event.actor.iri) or body.interacting_object != note_iri or body.interaction_target != note.quote_of_iri or body.attributed_to not in (None, event.actor.iri):
                return answer("rejected", "invalid_authorization")
            authorization = body.iri
        result = quotes_store.apply_quote_answer(client, public_id, author_iri=event.actor.iri, approved=event.type == "Accept", authorization_iri=authorization, now=now())
        state = "approved" if event.type == "Accept" else "refused"
        if result == "changed":
            notification_events.ap_quote_answered(settings.db_file, account_id=note.account_id, actor_iri=event.actor.iri, approved=event.type == "Accept")
        return answer("accepted", None, state if result == "changed" else None)

    def _decide(store: Any, client: Any, canonical: str, event: QuoteEventRequest) -> QuoteEventResponse:
        hosts = [_remote_host(address, canonical) for address in (event.actor.iri, event.actor.inbox, event.actor.shared_inbox) if address is not None]
        if any(host is None for host in hosts):
            return answer("rejected", "invalid_actor")
        # Claire makes the final check of the domain blocks.
        if any(federation_store.blocking_domain(store.conn, host) is not None for host in hosts if host is not None):
            return answer("rejected", "blocked_domain")
        return _request(store, client, canonical, event) if event.type == "QuoteRequest" else _answer(client, canonical, event)

    async def quote_event(request: Request) -> Response:
        try:
            event = QuoteEventRequest.model_validate_json(await request.body())
        except ValidationError:
            return _error(400, "invalid_request")
        canonical = origin()

        def _apply() -> QuoteEventResponse:
            store = open_store()
            client = open_client()
            try:
                with notification_events.after_commit():
                    decision = _decide(store, client, canonical, event)
                    client.commit()
                return decision
            finally:
                client.close()
                store.conn.close()

        decision = await run_in_threadpool(_apply)
        _log.info("quote event %s: %s%s", event.type, decision.decision, "" if decision.reason is None else f" ({decision.reason})")
        return _json(decision)

    def quote_authorization(request: Request) -> Response:
        raw_id = request.path_params["authorization_id"]
        if not (raw_id.isascii() and raw_id.isdigit() and raw_id[0] != "0" and len(raw_id) <= 18):
            return _not_found()
        store = open_store()
        client = open_client()
        try:
            row = quotes_store.get_authorization(client, int(raw_id))
            if row is None:
                return _not_found()
            # A revoked authorization is gone in all states of the federation switch, the same as a deleted object (RT9).
            if row.state == "revoked":
                return JSONResponse({"error": "gone", "gone_at": row.updated_at}, status_code=410, headers=_NO_STORE)
            # A request that waits or that is refused has no public form. The authorization is public only while the quoted object is public.
            if row.state != "approved" or quoted_object(store.conn, client, row.object_public_id, actor_ids=actor_account_ids(store)) is None:
                return _not_found()
            actor_public_id = str(store.get(row.account_id).public_id)
        finally:
            client.close()
            store.conn.close()
        return _json(QuoteAuthorizationRecord(id=str(row.id), actor_public_id=actor_public_id, object_public_id=str(row.object_public_id), quoting_object_iri=row.quoting_object_iri))

    return [
        Route("/quote-events", quote_event, methods=["POST"]),
        Route("/quote-authorizations/{authorization_id}", quote_authorization, methods=["GET"]),
    ]
