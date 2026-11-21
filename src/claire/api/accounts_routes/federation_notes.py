"""Routes of the outbound interactions of the federation client (FEDERATION_REMOTE_READING_DESIGN.md Sections 5.3 and 5.4, unit FP4-9).

For the owner of the account, in both server modes (KNOWLEDGE_FEDERATION_DESIGN.md Section 15 rule 5). A note, an edit, a favourite, and a boost need an account that is an actor (409 ``not_actor``).

- ``POST /api/federation/notes``: a new public note, or a reply to an item of the timeline (``in_reply_to`` is the item ID). ``quote_of`` is the ID of an item that the note quotes (FEP-044f, Section 5.4 rule 5, unit FP4-9Q); ``quote_policy`` is the quote policy of the note (rule 6).
- ``PATCH /api/federation/notes/{note_id}``: change the text, the content warning, the language (Section 5.4 rule 12), or the quote policy.
- ``DELETE /api/federation/notes/{note_id}``: delete an own note. The object path gives 410 from then on.
- ``POST``, ``DELETE /api/federation/timeline/{item_id}/favourite`` and ``.../boost``: ``Like`` and ``Announce`` of the object of an item, and their ``Undo``.

The owner gives plain text. ``federation_notes.render_note`` makes the HTML and sanitizes it (RS10). The note row, its timeline item, and its task are in one transaction of ``client.db``.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from ...federation import notes as note_text
from ...core import ids
from ...store import accounts as accounts_store
from ...store import federation as federation_store
from ...store import federation_client as client_store
from ...store import federation_notes as notes_store
from ...store import federation_quotes as quotes_store
from ..principal import request_principal
from ..security import read_json_body
from .common import AccountsContext, json_error, not_found, open_client_db, open_db
from .console_federation_panels import actor_state

FEDERATION_NOTES_API_PATH = "/api/federation/notes"
FEDERATION_TIMELINE_API_PATH = "/api/federation/timeline"
_NO_STORE = {"Cache-Control": "no-store"}
_NOTE_FIELDS = {"content", "in_reply_to", "summary", "sensitive", "language", "quote_of", "quote_policy"}
# The fields that only a new note can have.
_CREATE_ONLY_FIELDS = {"in_reply_to", "quote_of"}
# Route name: the item column, the task kind, and the task kind of the Undo (Section 5.4 rule 4).
_INTERACTIONS = {"favourite": ("favourited", "like", "undo_like"), "boost": ("boosted", "announce", "undo_announce")}


class _Refused(Exception):
    def __init__(self, status: int, error: str, **extra: Any):
        super().__init__(error)
        self.status, self.body = status, {"error": error, **extra}


def _digits(raw: str, length: int = 18) -> int | None:
    return int(raw) if raw.isascii() and raw.isdigit() and raw[0] != "0" and len(raw) <= length else None


def note_view(note: notes_store.Note, *, origin: str, reactions: dict[str, int] | None = None, quotes: int = 0) -> dict[str, Any]:
    """The view of an own note. ``quote_policy`` is the quote policy of the note, ``quotes`` is the number of approved quotes of it, and ``quote`` is the object that the note quotes with the state of the quote (``pending``, ``approved``, ``refused``, ``revoked``), or ``None`` (unit FP4-9Q)."""
    counters = reactions or {"like": 0, "announce": 0}
    return {
        "quote_policy": note.quote_policy, "quotes": quotes, "quote": None if note.quote_of_iri is None else {"iri": note.quote_of_iri, "state": note.quote_state},
        "id": str(note.public_id), "url": f"{origin}/ap/objects/{note.public_id}", "in_reply_to": note.in_reply_to_iri, "content": note.content_text, "summary": note.summary, "sensitive": note.sensitive, "language": note.language,
        "tags": note.tags, "mentions": [m.get("handle") or m.get("iri") for m in note.mentions], "published_at": note.published_at, "updated_at": note.updated_at, "edited_at": note.edited_at, "likes": counters["like"], "announces": counters["announce"],
    }


def item_extras(client: Any, account_id: int, item_ids: list[int], *, origin: str) -> dict[int, dict[str, Any]]:
    """The keys that the outbound interactions add to an item of the timeline: ``favourited``, ``boosted``, ``note`` (the own note of the item with its counters, or ``None``), and ``quote`` (unit FP4-9Q). ``client`` is a ``client.db`` connection.

    ``quote``: ``None``, or the object that the item quotes: ``iri``, ``item_id`` (the item of the account with that object, or ``None``), and ``state`` (the quote state of an own note; ``None`` for an item of a remote actor, whose quote Claire does not verify).
    """
    flags = notes_store.item_flags(client, account_id, item_ids)
    prefix = f"{origin}/ap/objects/"
    own: dict[int, notes_store.Note] = {}
    for item_id, flag in flags.items():
        if flag["object_iri"].startswith(prefix):
            try:
                note = notes_store.get_note(client, ids.parse_public_id(flag["object_iri"][len(prefix) :]))
            except ValueError:
                note = None
            if note is not None and note.account_id == account_id and note.deleted_at is None:
                own[item_id] = note
    reactions = client_store.count_object_reactions(client, [note.public_id for note in own.values()])
    quotes = quotes_store.count_approved_quotes(client, [note.public_id for note in own.values()])
    quoted: dict[int, str] = {}
    if flags:
        marks = ",".join("?" for _ in flags)
        quoted = {int(r[0]): str(r[1]) for r in client.execute(f"SELECT id, quote_of_iri FROM federation_timeline_items WHERE account_id=? AND quote_of_iri IS NOT NULL AND id IN ({marks})", (account_id, *flags))}
    targets: dict[str, str] = {}
    if quoted:
        iris = sorted(set(quoted.values()))
        marks = ",".join("?" for _ in iris)
        targets = {str(r[0]): str(r[1]) for r in client.execute(f"SELECT object_iri, id FROM federation_timeline_items WHERE account_id=? AND object_iri IN ({marks})", (account_id, *iris))}

    def _quote(item_id: int) -> dict[str, Any] | None:
        if item_id not in quoted:
            return None
        return {"iri": quoted[item_id], "item_id": targets.get(quoted[item_id]), "state": own[item_id].quote_state if item_id in own else None}

    return {
        item_id: {"favourited": flag["favourited"], "boosted": flag["boosted"], "quote": _quote(item_id), "note": None if item_id not in own else note_view(own[item_id], origin=origin, reactions=reactions[own[item_id].public_id], quotes=quotes[own[item_id].public_id])}
        for item_id, flag in flags.items()
    }


def routes(ctx: AccountsContext) -> list[Route]:
    origin = ctx.config.public_origin

    def _owner_account_id(request: Request) -> int | None:
        principal = request_principal(request)
        return principal.account_id if principal.scope == "owner" and principal.account_id is not None else None

    def _actor(conn: Any, account_id: int, *, need_actor: bool = True) -> tuple[accounts_store.AccountStore, accounts_store.Account] | None:
        """The account of the caller. Raises 409 ``not_actor`` when the route needs an actor (Section 5.4 rule 11)."""
        store = accounts_store.AccountStore(conn)
        account = store.get(account_id)
        if need_actor and actor_state(account, federation=store.federation_state()) != "active":
            raise _Refused(409, "not_actor")
        return store, account

    def _blocked(conn: Any, host: str | None) -> bool:
        return host is not None and federation_store.blocking_domain(conn, host) is not None

    def _compose(conn: Any, client: Any, account: accounts_store.Account, *, text: str, parent_author: tuple[str, str | None] | None, old_mentions: list[dict[str, Any]], quote_url: str | None = None) -> tuple[note_text.RenderedNote, list[dict[str, Any]]]:
        """Make the HTML and the mentions of a note. ``parent_author`` is the actor IRI and the handle of the author of the parent of a reply: it is a mention also when the text does not have its handle (Section 5.4 rule 1)."""
        own_actor = f"{origin}/ap/actors/{account.public_id}"
        own_handle = f"@{account.username}@{ctx.config.expected_authority}".lower()
        try:
            first = note_text.render_note(text, origin=origin)
        except note_text.NoteTextError as exc:
            raise _Refused(400, exc.code) from exc
        handles = [handle for handle in first.mentions if handle.lower() != own_handle]
        known = notes_store.known_actor_iris(client, account.id, handles)
        for old in old_mentions:
            if old.get("handle") in known and old.get("iri") and known[old["handle"]] is None:
                known[old["handle"]] = old["iri"]
        if parent_author is not None and parent_author[0] != own_actor:
            parent_handle = note_text.normalize_handle(parent_author[1] or "")
            if parent_handle is not None and parent_handle in known:
                known[parent_handle] = parent_author[0]
        mentions: list[dict[str, Any]] = [{"handle": handle, "iri": known[handle]} for handle in handles]
        if parent_author is not None and parent_author[0] != own_actor and all(m["iri"] != parent_author[0] for m in mentions):
            mentions.insert(0, {"handle": note_text.normalize_handle(parent_author[1] or ""), "iri": parent_author[0]})
        if len(mentions) > note_text.NOTE_MAX_MENTIONS:
            raise _Refused(400, "too_many_mentions")
        for mention in mentions:
            host = federation_store.iri_host(mention["iri"]) if mention["iri"] else None
            try:
                handle_host = federation_store.normalize_domain(mention["handle"].rsplit("@", 1)[1]) if mention["handle"] else None
            except ValueError:
                raise _Refused(400, "invalid_mention") from None
            if _blocked(conn, host) or _blocked(conn, handle_host):
                raise _Refused(400, "blocked_domain")
        rendered = note_text.render_note(text, origin=origin, mention_iris={m["handle"]: m["iri"] for m in mentions if m["handle"]}, quote_url=quote_url)
        return rendered, mentions

    def _fields(body: dict[str, Any], *, conn: Any, account: accounts_store.Account, current: notes_store.Note | None) -> dict[str, Any]:
        """Check the composition fields of a request (Section 5.4 rule 12). A field that is absent keeps the value of ``current``, or gets the default of a new note."""
        limit = int(client_store.get_reading_settings(conn)["note_max_chars"])
        content = body.get("content", None if current is None else current.content_text)
        if not isinstance(content, str):
            raise _Refused(400, "invalid_request", fields=["content"])
        content = note_text.normalize_text(content)
        if len(content) > limit:
            raise _Refused(400, "content_too_long", limit=limit)
        summary = body.get("summary", None if current is None else current.summary)
        if summary is not None:
            if not isinstance(summary, str):
                raise _Refused(400, "invalid_request", fields=["summary"])
            summary = " ".join(note_text.normalize_text(summary).split()) or None
            if summary is not None and len(summary) > note_text.NOTE_SUMMARY_MAX_CHARS:
                raise _Refused(400, "summary_too_long", limit=note_text.NOTE_SUMMARY_MAX_CHARS)
        sensitive = body.get("sensitive", bool(summary) if current is None or "summary" in body else current.sensitive)
        if not isinstance(sensitive, bool):
            raise _Refused(400, "invalid_request", fields=["sensitive"])
        # Language (rule 12): the value of the request, else the locale of the account, else the server default, else English.
        default_language = (None if current is None else current.language) or account.locale or getattr(ctx.settings, "ui_lang", "") or "en"
        raw_language = body.get("language") or default_language
        language = note_text.normalize_language(raw_language) if isinstance(raw_language, str) else None
        if language is None:
            raise _Refused(400, "invalid_language")
        # The quote policy (Section 5.4 rule 6): the default of a note is ``public``.
        policy = body.get("quote_policy", "public" if current is None else current.quote_policy)
        if policy not in quotes_store.QUOTE_POLICIES:
            raise _Refused(400, "invalid_quote_policy")
        # A content warning makes the note sensitive (Section 5.4 rule 12).
        return {"content": content, "summary": summary, "sensitive": bool(sensitive or summary), "language": language, "quote_policy": policy}

    def _author_profile(account: accounts_store.Account) -> dict[str, Any]:
        return {"iri": f"{origin}/ap/actors/{account.public_id}", "name": account.display_name or None, "handle": f"@{account.username}@{ctx.config.expected_authority}"}

    async def _run(execute: Any) -> Response:
        try:
            result = await asyncio.to_thread(execute)
        except _Refused as refused:
            return JSONResponse(refused.body, status_code=refused.status, headers=_NO_STORE)
        if result is None:
            return not_found()
        return JSONResponse(result[1], status_code=result[0], headers=_NO_STORE)

    async def notes_post(request: Request) -> Response:
        # Same-origin rule for a cookie request: ``AuthenticationMiddleware``.
        account_id = _owner_account_id(request)
        if account_id is None:
            return not_found()
        body = await read_json_body(request)
        if "content" not in body or set(body) - _NOTE_FIELDS:
            return json_error(400, "invalid_request", fields=sorted(_NOTE_FIELDS))
        parent_id = None
        if body.get("in_reply_to") is not None:
            parent_id = _digits(body["in_reply_to"]) if isinstance(body["in_reply_to"], str) else None
            if parent_id is None:
                return json_error(404, "item_not_found")
        quoted_id = None
        if body.get("quote_of") is not None:
            quoted_id = _digits(body["quote_of"]) if isinstance(body["quote_of"], str) else None
            if quoted_id is None:
                return json_error(404, "item_not_found")

        def _execute() -> tuple[int, dict[str, Any]] | None:
            conn = open_db(ctx)
            client = open_client_db(ctx)
            try:
                found = _actor(conn, account_id)
                if found is None:
                    return None
                store, account = found
                fields = _fields(body, conn=conn, account=account, current=None)
                # Section 5.4 rule 5: a note can quote one object, an item of the timeline of the account.
                quoted = None if quoted_id is None else client_store.get_timeline_item(client, account_id, quoted_id)
                if quoted_id is not None and quoted is None:
                    raise _Refused(404, "item_not_found")
                if quoted is not None:
                    # A quote of an own note needs no request to a remote server, and this unit has no local approval.
                    if quoted.author_iri == f"{origin}/ap/actors/{account.public_id}":
                        raise _Refused(409, "own_item")
                    if _blocked(conn, federation_store.iri_host(quoted.object_iri)) or _blocked(conn, federation_store.iri_host(quoted.author_iri)):
                        raise _Refused(400, "blocked_domain")
                parent = None if parent_id is None else client_store.get_timeline_item(client, account_id, parent_id)
                if parent_id is not None and parent is None:
                    raise _Refused(404, "item_not_found")
                if parent is not None and (_blocked(conn, federation_store.iri_host(parent.object_iri)) or _blocked(conn, federation_store.iri_host(parent.author_iri))):
                    raise _Refused(400, "blocked_domain")
                rendered, mentions = _compose(conn, client, account, text=fields["content"], parent_author=None if parent is None else (parent.author_iri, parent.author.get("handle")), old_mentions=[], quote_url=None if quoted is None else quoted.object_iri)
                when = time.time()

                def _taken(number: int) -> bool:
                    # RT7: the number space of the published documents and their tombstones (claire.db).
                    return conn.execute("SELECT 1 FROM federation_objects WHERE public_id=? UNION ALL SELECT 1 FROM federation_tombstones WHERE public_id=?", (number, number)).fetchone() is not None

                note = notes_store.add_note(
                    client, account_id=account_id, salt=store.id_salt(), taken=_taken, in_reply_to_iri=None if parent is None else parent.object_iri,
                    # A reply to a thread of a group also goes to the group (FEP-1b12, Section 5.4 rule 1).
                    audience_iri=None if parent is None else notes_store.group_audience(client, account_id, parent.announced_by_iri),
                    content_text=fields["content"], content_html=rendered.html, mentions=mentions, summary=fields["summary"], sensitive=fields["sensitive"], language=fields["language"], tags=list(rendered.tags), now=when,
                    quote_of_iri=None if quoted is None else quoted.object_iri, quote_author_iri=None if quoted is None else quoted.author_iri, quote_policy=fields["quote_policy"],
                )
                # Section 5.4 rule 9: the own note is an item of the own timeline, so that the thread view shows it. It is read.
                object_iri = f"{origin}/ap/objects/{note.public_id}"
                client_store.upsert_timeline_item(client, account_id=account_id, item=notes_store.note_timeline_item(note, object_iri=object_iri, author=_author_profile(account), context_iri=None if parent is None else parent.context_iri), announced_by=None, in_timeline=True, now=when)
                client.execute("UPDATE federation_timeline_items SET unread=0 WHERE account_id=? AND object_iri=?", (account_id, object_iri))
                item_id = int(client.execute("SELECT id FROM federation_timeline_items WHERE account_id=? AND object_iri=?", (account_id, object_iri)).fetchone()[0])
                # The note and its task are in one transaction (DATA_STORE_LAYOUT_DESIGN.md Section 3 rule 2).
                # A note with a quote has the task ``quote_request``: the sidecar sends QuoteRequest to the author of the quoted object and then Create(Note) (Section 5.2).
                client_store.add_client_task(client, account_id=account_id, kind="note" if quoted is None else "quote_request", target_iri=None if quoted is None else quoted.object_iri, ref_id=note.public_id, now=when)
                client.commit()
                return 201, {"note": note_view(note, origin=origin), "item_id": str(item_id)}
            finally:
                client.close()
                conn.close()

        return await _run(_execute)

    def _own_note(client: Any, account_id: int, raw_id: str) -> notes_store.Note:
        """The note of the caller that is not deleted. A note of another account gives the same answer as no note."""
        try:
            note = notes_store.get_note(client, ids.parse_public_id(raw_id))
        except ValueError:
            note = None
        if note is None or note.account_id != account_id or note.deleted_at is not None:
            raise _Refused(404, "note_not_found")
        return note

    async def notes_patch(request: Request) -> Response:
        account_id = _owner_account_id(request)
        if account_id is None:
            return not_found()
        body = await read_json_body(request)
        if not body or set(body) - (_NOTE_FIELDS - _CREATE_ONLY_FIELDS):
            return json_error(400, "invalid_request", fields=sorted(_NOTE_FIELDS - _CREATE_ONLY_FIELDS))

        def _execute() -> tuple[int, dict[str, Any]] | None:
            conn = open_db(ctx)
            client = open_client_db(ctx)
            try:
                found = _actor(conn, account_id)
                if found is None:
                    return None
                _store, account = found
                note = _own_note(client, account_id, request.path_params["note_id"])
                fields = _fields(body, conn=conn, account=account, current=note)
                when = time.time()
                # Section 5.4 rule 6: a change of the quote policy is not an edit of the note. ``edited_at`` stays, and the followers get Update(Note) with the new policy.
                policy_changed = quotes_store.set_note_quote_policy(client, note.public_id, fields["quote_policy"], now=when)
                if set(body) == {"quote_policy"}:
                    if policy_changed:
                        client_store.add_client_task(client, account_id=account_id, kind="update_note", target_iri=None, ref_id=note.public_id, now=when)
                    same_text = notes_store.get_note(client, note.public_id)
                    assert same_text is not None
                    view = note_view(same_text, origin=origin, reactions=client_store.count_object_reactions(client, [note.public_id])[note.public_id], quotes=quotes_store.count_approved_quotes(client, [note.public_id])[note.public_id])
                    client.commit()
                    return 200, {"note": view}
                # The author of the parent stays a mention of a reply.
                parent_iri = None if note.in_reply_to_iri is None else client_store.timeline_item_author(client, account_id, note.in_reply_to_iri)
                parent_author = None
                if parent_iri is not None:
                    parent_author = (parent_iri, next((m.get("handle") for m in note.mentions if m.get("iri") == parent_iri), None))
                else:
                    kept = next((m for m in note.mentions if m.get("handle") is None and m.get("iri")), None)
                    parent_author = None if kept is None else (kept["iri"], None)
                rendered, mentions = _compose(conn, client, account, text=fields["content"], parent_author=parent_author, old_mentions=note.mentions, quote_url=note.quote_of_iri)
                changed = notes_store.update_note(client, note.public_id, content_text=fields["content"], content_html=rendered.html, mentions=mentions, summary=fields["summary"], sensitive=fields["sensitive"], language=fields["language"], tags=list(rendered.tags), now=when)
                assert changed is not None
                object_iri = f"{origin}/ap/objects/{note.public_id}"
                client_store.update_timeline_items(client, item=notes_store.note_timeline_item(changed, object_iri=object_iri, author=_author_profile(account), context_iri=None), now=when)
                client_store.add_client_task(client, account_id=account_id, kind="update_note", target_iri=None, ref_id=note.public_id, now=when)
                reactions = client_store.count_object_reactions(client, [note.public_id])[note.public_id]
                quotes = quotes_store.count_approved_quotes(client, [note.public_id])[note.public_id]
                client.commit()
                return 200, {"note": note_view(changed, origin=origin, reactions=reactions, quotes=quotes)}
            finally:
                client.close()
                conn.close()

        return await _run(_execute)

    async def notes_delete(request: Request) -> Response:
        account_id = _owner_account_id(request)
        if account_id is None:
            return not_found()

        def _execute() -> tuple[int, dict[str, Any]] | None:
            conn = open_db(ctx)
            client = open_client_db(ctx)
            try:
                # An account that is not an actor now can delete its note: the task waits until the account is an actor again.
                found = _actor(conn, account_id, need_actor=False)
                if found is None:
                    return None
                _store, account = found
                note = _own_note(client, account_id, request.path_params["note_id"])
                # Section 5.4 rule 7: the row becomes the tombstone, the item goes, and the task sends Delete. One transaction.
                notes_store.delete_note(client, note, actor_iri=f"{origin}/ap/actors/{account.public_id}", object_iri=f"{origin}/ap/objects/{note.public_id}", now=time.time())
                client.commit()
                return 200, {"deleted": True, "id": str(note.public_id)}
            finally:
                client.close()
                conn.close()

        return await _run(_execute)

    def _interaction(name: str, value: bool) -> Any:
        flag, kind, undo_kind = _INTERACTIONS[name]

        async def handler(request: Request) -> Response:
            account_id = _owner_account_id(request)
            if account_id is None:
                return not_found()
            item_id = _digits(request.path_params["item_id"])
            if item_id is None:
                return json_error(404, "item_not_found")

            def _execute() -> tuple[int, dict[str, Any]] | None:
                conn = open_db(ctx)
                client = open_client_db(ctx)
                try:
                    found = _actor(conn, account_id)
                    if found is None:
                        return None
                    _store, account = found
                    item = notes_store.item_flags(client, account_id, [item_id]).get(item_id)
                    if item is None:
                        raise _Refused(404, "item_not_found")
                    # An own note has no favourite and no boost of its own account.
                    if item["author_iri"] == f"{origin}/ap/actors/{account.public_id}":
                        raise _Refused(409, "own_item")
                    if value and (_blocked(conn, federation_store.iri_host(item["object_iri"])) or _blocked(conn, federation_store.iri_host(item["author_iri"]))):
                        raise _Refused(400, "blocked_domain")
                    # Section 5.4 rule 4: the item records the state, and the task refers to the object. A repeated request changes nothing and makes no task.
                    if notes_store.set_item_flag(client, account_id, item_id, flag, value):  # type: ignore[arg-type]
                        client_store.add_client_task(client, account_id=account_id, kind=kind if value else undo_kind, target_iri=item["object_iri"], ref_id=item_id, now=time.time())
                    client.commit()
                    item[flag] = value
                    return 200, {"item_id": str(item_id), "favourited": item["favourited"], "boosted": item["boosted"]}
                finally:
                    client.close()
                    conn.close()

            return await _run(_execute)

        return handler

    return [
        Route(FEDERATION_NOTES_API_PATH, notes_post, methods=["POST"]),
        Route(FEDERATION_NOTES_API_PATH + "/{note_id}", notes_patch, methods=["PATCH"]),
        Route(FEDERATION_NOTES_API_PATH + "/{note_id}", notes_delete, methods=["DELETE"]),
        Route(FEDERATION_TIMELINE_API_PATH + "/{item_id}/favourite", _interaction("favourite", True), methods=["POST"]),
        Route(FEDERATION_TIMELINE_API_PATH + "/{item_id}/favourite", _interaction("favourite", False), methods=["DELETE"]),
        Route(FEDERATION_TIMELINE_API_PATH + "/{item_id}/boost", _interaction("boost", True), methods=["POST"]),
        Route(FEDERATION_TIMELINE_API_PATH + "/{item_id}/boost", _interaction("boost", False), methods=["DELETE"]),
    ]
