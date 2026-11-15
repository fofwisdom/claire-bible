"""Internal federation API of the outbound interactions (contract 1.6, FEDERATION_REMOTE_READING_DESIGN.md Sections 5.2 and 5.4, unit FP4-9).

``federation_routes.federation_internal_routes`` adds these routes to its table:

- ``GET /notes/{public_id}``: the canonical form of an own note. The sidecar makes the ``Note`` of the object path and of the delivery from it. A deleted note gives 410.
- ``GET /interaction-tasks``: the ready tasks of the kinds ``note``, ``update_note``, ``delete_note``, ``like``, ``undo_like``, ``announce``, ``undo_announce``, and of the quote kinds ``quote_request``, ``quote_authorize``, ``quote_reject``, ``quote_revoke`` (contract 1.7, unit FP4-9Q), each with a lease.
- ``POST /interaction-tasks/{id}/ack``: the result of a task, with the actors that the sidecar resolved for the mentions of a note.

The models are sealed, as in ``federation_routes``. The store functions are in ``store.federation_notes``.
"""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Callable
from typing import Any, Literal

from pydantic import Field, ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import BaseRoute, Route

from .. import federation_notes as note_text
from .. import ids
from ..store import federation as federation_store
from ..store import federation_client as client_store
from ..store import federation_notes as notes_store
from ..store import federation_quotes as quotes_store
from .federation_routes import _MAX_IRI_LENGTH, _NO_STORE, OUTBOX_LEASE_SECONDS, OUTBOX_TASKS_DEFAULT, OUTBOX_TASKS_MAX, OutboxAckResponse, _error, _json, _not_found, _remote_host, _Sealed

__all__ = ["InteractionAckRequest", "InteractionTasksResponse", "NoteRecord", "note_internal_routes", "note_record", "rendered_note_html"]

_log = logging.getLogger("claire.federation.notes")


class NoteMention(_Sealed):
    """One mention of a note. ``handle`` is ``@name@host`` from the text, or ``None`` for the author of the parent of a reply that has no handle. ``iri`` is the actor IRI, or ``None`` until the sidecar resolves the handle."""

    handle: str | None
    iri: str | None


class NoteTag(_Sealed):
    # ``#tag`` and its address. The address is the one in the HTML.
    name: str
    href: str


class NoteRecord(_Sealed):
    """The canonical form of one own note (Section 5.4). The sidecar makes the ``Note`` from it. ``content`` is HTML that Claire made from plain text and sanitized (RS10)."""

    public_id: str
    actor_public_id: str
    type: Literal["Note"]
    content: str
    summary: str | None
    sensitive: bool
    language: str | None
    in_reply_to: str | None
    # The group of the thread (FEP-1b12), or ``None``.
    audience: str | None
    mentions: list[NoteMention]
    tags: list[NoteTag]
    published_at: float
    updated_at: float
    # ``None`` for a note that the owner did not edit. The sidecar sets ``updated`` only for an edited note.
    edited_at: float | None
    # Contract 1.7 (unit FP4-9Q). ``quote_policy``: the sidecar makes ``canQuote`` from it. ``quote``: the quoted object while the quote is ``pending`` or ``approved``; ``None`` for a note without a quote and for a ``refused`` or ``revoked`` quote (the HTML keeps the link). ``quote_authorization``: the QuoteAuthorization of an ``approved`` quote.
    quote_policy: Literal["public", "followers", "manual", "nobody"]
    quote: str | None
    quote_authorization: str | None


class InteractionQuote(_Sealed):
    """The request of a remote actor to quote an own object: the ID of its ``QuoteRequest`` and the quoting object."""

    request_iri: str
    quoting_object_iri: str


class InteractionTaskItem(_Sealed):
    """One task of the outbound interactions (Section 5.2).

    ``note``, ``update_note``: ``ref_id`` is the number of the note; the sidecar reads ``/notes/{ref_id}``. ``delete_note``: ``ref_id`` is the number of the deleted note, and ``recipients`` has the actors that got the note in addition to the followers.
    ``like``, ``announce``: ``object_iri`` is the remote object, ``recipients`` has its author, and ``ref_id`` is the number in the activity ID (``{actor}#likes/{ref_id}``, ``{actor}#announces/{ref_id}``). ``undo_like``, ``undo_announce``: ``ref_id`` is the number of the activity that is undone.

    Quotes (contract 1.7, unit FP4-9Q). ``quote_request``: ``ref_id`` is the number of the quoting note, ``object_iri`` is the quoted object, and ``recipients`` has its author; the sidecar sends ``QuoteRequest`` and then ``Create`` of the note. Without ``object_iri`` (a blocked domain applies to the quoted object now) the sidecar sends the ``Create`` only. ``quote_authorize``, ``quote_reject``, ``quote_revoke``: ``ref_id`` is the number of the authorization (``/ap/quote-authorizations/{ref_id}``), ``object_iri`` is the own object that is quoted, ``recipients`` has the quoting actor, and ``quote`` has the request and the quoting object.
    """

    id: int
    kind: Literal["note", "update_note", "delete_note", "like", "undo_like", "announce", "undo_announce", "quote_request", "quote_authorize", "quote_reject", "quote_revoke"]
    actor_public_id: str
    ref_id: str
    object_iri: str | None
    recipients: list[str]
    quote: InteractionQuote | None
    created_at: float
    lease_until: float


class InteractionTasksResponse(_Sealed):
    # Oldest first. Each task has a lease until ``lease_until``.
    tasks: list[InteractionTaskItem]


class ResolvedMention(_Sealed):
    handle: str = Field(min_length=3, max_length=512)
    iri: str = Field(min_length=1, max_length=_MAX_IRI_LENGTH)


class InteractionAckRequest(_Sealed):
    result: Literal["handed_off", "rejected"]
    error_code: str | None = Field(pattern=r"^[a-z0-9_]{1,64}$")
    # The actors that the sidecar resolved for the handles of a ``note`` or ``update_note`` task. Empty for the other kinds.
    mentions: list[ResolvedMention] = Field(max_length=note_text.NOTE_MAX_MENTIONS)


def note_record(note: notes_store.Note, *, actor_public_id: str, origin: str) -> NoteRecord:
    return NoteRecord(
        public_id=str(note.public_id), actor_public_id=actor_public_id, type="Note", content=note.content_html, summary=note.summary, sensitive=note.sensitive, language=note.language, in_reply_to=note.in_reply_to_iri, audience=note.audience_iri,
        mentions=[NoteMention(handle=m.get("handle"), iri=m.get("iri")) for m in note.mentions], tags=[NoteTag(name=f"#{tag}", href=note_text.hashtag_href(origin, tag)) for tag in note.tags],
        published_at=note.published_at, updated_at=note.updated_at, edited_at=note.edited_at,
        quote_policy=note.quote_policy, quote=note.quote_of_iri if note.quote_state in ("pending", "approved") else None, quote_authorization=note.quote_authorization_iri if note.quote_state == "approved" else None,
    )


def rendered_note_html(note: notes_store.Note, *, origin: str) -> str:
    """The HTML of a note from its text, with a link for each mention that has an actor IRI."""
    known = {str(m["handle"]): m.get("iri") for m in note.mentions if m.get("handle")}
    return note_text.render_note(note.content_text, origin=origin, mention_iris=known, quote_url=note.quote_of_iri).html


def note_internal_routes(settings: Any, *, now: Callable[[], float], origin: Callable[[], str], open_store: Callable[[], Any], open_client: Callable[[], Any], actor_account_ids: Callable[[Any], list[int]]) -> list[BaseRoute]:
    """The routes of this module. The callables are the helpers of ``federation_internal_routes``: the canonical origin, a read connection of ``claire.db`` as an ``AccountStore``, a connection of ``client.db``, and the accounts that are actors now."""

    def _actors() -> dict[int, str]:
        """The accounts that are actors now, with their public IDs."""
        store = open_store()
        try:
            return {account_id: str(store.get(account_id).public_id) for account_id in actor_account_ids(store)}
        finally:
            store.conn.close()

    def note(request: Request) -> Response:
        try:
            public_id = ids.parse_public_id(request.path_params["public_id"])
        except ValueError:
            return _not_found()
        client = open_client()
        try:
            found = notes_store.get_note(client, public_id)
        finally:
            client.close()
        if found is None:
            return _not_found()
        # A deleted note is gone in all states of the federation switch, the same as a deleted object.
        if found.deleted_at is not None:
            return JSONResponse({"error": "gone", "gone_at": found.deleted_at}, status_code=410, headers=_NO_STORE)
        actor_public_id = _actors().get(found.account_id)
        # The note is public only while its account is an actor.
        if actor_public_id is None:
            return _not_found()
        return _json(note_record(found, actor_public_id=actor_public_id, origin=origin()))

    def _blocked(conn: Any, iri: str) -> bool:
        host = _remote_host(iri, origin())
        return host is None or federation_store.blocking_domain(conn, host) is not None

    def interaction_tasks(request: Request) -> Response:
        raw_limit = request.query_params.get("limit", str(OUTBOX_TASKS_DEFAULT))
        if not (raw_limit.isascii() and raw_limit.isdigit() and 1 <= int(raw_limit) <= OUTBOX_TASKS_MAX):
            return _error(400, "invalid_request")
        actors = _actors()
        store = open_store()
        client = open_client()
        try:
            # Only a task of an account that is an actor now is given (Section 5.4 rule 11). The task of a paused account waits.
            leased = notes_store.lease_interaction_tasks(client, account_ids=list(actors), now=now(), lease_seconds=OUTBOX_LEASE_SECONDS, limit=int(raw_limit))
            items = []

            def refuse(task: client_store.ClientTask, code: str) -> None:
                client_store.ack_client_task(client, task.id, result="rejected", error_code=code, now=now())
                if task.kind in ("like", "announce") and task.ref_id is not None:
                    notes_store.set_item_flag(client, task.account_id, task.ref_id, "favourited" if task.kind == "like" else "boosted", False)

            for task in leased:
                ref_id, object_iri, recipients, quote = task.ref_id, None, [], None
                if task.kind in quotes_store.QUOTE_TASK_KINDS and task.kind != "quote_request":
                    # The answer to a request of a remote actor. The state of the request can change before the sidecar takes the task (the owner revokes an approval): then the task of the new state follows.
                    row = None if task.ref_id is None else quotes_store.get_authorization(client, task.ref_id)
                    wanted = {"quote_authorize": ("approved",), "quote_reject": ("refused", "revoked"), "quote_revoke": ("revoked",)}[task.kind]
                    if row is None or row.account_id != task.account_id or row.state not in wanted:
                        refuse(task, "state_changed")
                        continue
                    if _blocked(store.conn, row.quoting_actor_iri) or _blocked(store.conn, row.quoting_object_iri):
                        refuse(task, "blocked_domain")
                        continue
                    object_iri, recipients = f"{origin()}/ap/objects/{row.object_public_id}", [row.quoting_actor_iri]
                    quote = InteractionQuote(request_iri=row.request_iri, quoting_object_iri=row.quoting_object_iri)
                elif task.kind in ("note", "update_note", "delete_note", "quote_request"):
                    found = None if task.ref_id is None else notes_store.get_note(client, task.ref_id)
                    if found is None or found.account_id != task.account_id or (found.deleted_at is None) == (task.kind == "delete_note"):
                        # The owner deleted the note before the sidecar took the task: the delete_note task follows.
                        refuse(task, "note_removed")
                        continue
                    if task.kind == "delete_note":
                        # The author of a quoted object got the note too.
                        recipients = list(dict.fromkeys(iri for iri in [*(m.get("iri") for m in found.mentions), found.audience_iri, found.quote_author_iri] if iri and not _blocked(store.conn, iri)))
                    if task.kind == "quote_request":
                        if found.quote_of_iri is None or found.quote_author_iri is None:
                            refuse(task, "no_quote")
                            continue
                        if _blocked(store.conn, found.quote_of_iri) or _blocked(store.conn, found.quote_author_iri):
                            # A domain block came after the owner wrote the note: no request goes out, the quote is refused, and the note is sent with the link only.
                            client.execute("UPDATE federation_notes SET quote_state='refused' WHERE public_id=?", (found.public_id,))
                        else:
                            object_iri, recipients = found.quote_of_iri, [found.quote_author_iri]
                else:
                    flags = {} if task.ref_id is None else notes_store.item_flags(client, task.account_id, [task.ref_id])
                    item = flags.get(task.ref_id) if task.ref_id is not None else None
                    if item is None or item["object_iri"] != task.target_iri:
                        # The item is gone (for example, the author deleted the post).
                        refuse(task, "item_removed")
                        continue
                    if _blocked(store.conn, item["object_iri"]) or _blocked(store.conn, item["author_iri"]):
                        refuse(task, "blocked_domain")
                        continue
                    object_iri, recipients = item["object_iri"], [item["author_iri"]]
                    # The number of the activity ID: this task, or for an Undo the task of the activity that is undone.
                    ref_id = task.id if task.kind in ("like", "announce") else notes_store.original_task_id(client, task)
                if ref_id is None:
                    refuse(task, "no_activity")
                    continue
                items.append(InteractionTaskItem(id=task.id, kind=task.kind, actor_public_id=actors[task.account_id], ref_id=str(ref_id), object_iri=object_iri, recipients=recipients, quote=quote, created_at=task.created_at, lease_until=float(task.lease_until or 0.0)))
            client.commit()
        finally:
            client.close()
            store.conn.close()
        return _json(InteractionTasksResponse(tasks=items))

    async def interaction_task_ack(request: Request) -> Response:
        raw_id = request.path_params["task_id"]
        try:
            body = InteractionAckRequest.model_validate_json(await request.body())
        except ValidationError:
            return _error(400, "invalid_request")
        if not (raw_id.isascii() and raw_id.isdigit() and raw_id[0] != "0" and len(raw_id) <= 18):
            return _not_found()
        canonical = origin()

        def _apply() -> Any:
            store = open_store()
            try:
                # Claire makes the final check of the domain blocks: a resolved actor on a blocked domain is not stored.
                resolved = {m.handle.lower(): m.iri for m in body.mentions if not _blocked(store.conn, m.iri)}
                public_ids = {account_id: str(store.get(account_id).public_id) for account_id in actor_account_ids(store)}
            finally:
                store.conn.close()
            client = open_client()
            try:
                task = client_store.get_client_task(client, int(raw_id))
                if task is None or task.kind not in notes_store.INTERACTION_KINDS:
                    return None
                if task.status == "pending":
                    if body.result == "handed_off" and task.kind in ("note", "update_note", "quote_request") and resolved and task.ref_id is not None:
                        found = notes_store.get_note(client, task.ref_id)
                        if found is not None and found.deleted_at is None:
                            mentions = [{**m, "iri": m.get("iri") or resolved.get(str(m.get("handle") or "").lower())} for m in found.mentions]
                            if mentions != found.mentions:
                                # The HTML gets the links of the resolved mentions, in the note and in its item of the timeline.
                                html = rendered_note_html(dataclasses.replace(found, mentions=mentions), origin=canonical)
                                notes_store.set_note_mentions(client, found.public_id, mentions=mentions, content_html=html)
                                public_id = public_ids.get(found.account_id)
                                if public_id is not None:
                                    client.execute("UPDATE federation_timeline_items SET content_html=? WHERE account_id=? AND object_iri=? AND author_iri=?", (html, found.account_id, f"{canonical}/ap/objects/{found.public_id}", f"{canonical}/ap/actors/{public_id}"))
                    if body.result == "rejected" and task.kind in ("like", "announce") and task.ref_id is not None:
                        # The remote server did not get the activity: the item shows no favourite or boost.
                        notes_store.set_item_flag(client, task.account_id, task.ref_id, "favourited" if task.kind == "like" else "boosted", False)
                task = client_store.ack_client_task(client, task.id, result=body.result, error_code=body.error_code, now=now())
                client.commit()
                return task
            finally:
                client.close()

        task = await run_in_threadpool(_apply)
        if task is None:
            return _not_found()
        _log.info("interaction task %s (%s): %s%s", task.id, task.kind, task.status, "" if task.error_code is None else f" ({task.error_code})")
        return _json(OutboxAckResponse(id=task.id, status=task.status))

    return [
        Route("/notes/{public_id}", note, methods=["GET"]),
        Route("/interaction-tasks", interaction_tasks, methods=["GET"]),
        Route("/interaction-tasks/{task_id}/ack", interaction_task_ack, methods=["POST"]),
    ]
