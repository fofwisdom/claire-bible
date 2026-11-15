"""Store functions of the outbound interactions (FEDERATION_REMOTE_READING_DESIGN.md Sections 5.2, 5.4, and 7.1, unit FP4-9).

All tables are in ``client.db`` (``store.client_db``, version 5; the quote columns of a note are version 6, and their functions are in ``store.federation_quotes``): ``federation_notes``, the columns ``favourited`` and ``boosted`` of ``federation_timeline_items``, and the kinds ``note``, ``update_note``, ``delete_note``, ``like``, ``undo_like``, ``announce``, ``undo_announce`` of ``federation_client_outbox``.

- The number of a note is in the number space of the published documents (RT7): the caller gives ``taken``, which reads ``federation_objects`` and ``federation_tombstones`` of ``claire.db``.
- A deleted note keeps its row without content (``deleted_at``). The row is the tombstone of the note: the object path gives 410, and the ``delete_note`` task reads the recipients from it.
- An own note is also an item of the timeline of its account (Section 5.4 rule 9), so that the thread view shows it.

No function in this module commits. The caller owns the transaction.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from .. import ids
from . import federation_client as client_store

__all__ = [
    "INTERACTION_KINDS",
    "Note",
    "add_note",
    "count_notes",
    "delete_note",
    "get_note",
    "group_audience",
    "item_flags",
    "known_actor_iris",
    "lease_interaction_tasks",
    "list_notes",
    "mark_note_deleted",
    "note_timeline_item",
    "original_task_id",
    "set_item_flag",
    "set_note_mentions",
    "update_note",
]

# The kinds of the client outbox that the internal endpoint ``interaction-tasks`` gives to the sidecar (contract 1.6).
# Contract 1.7 (unit FP4-9Q) adds the four quote kinds (``store.federation_quotes.QUOTE_TASK_KINDS``).
INTERACTION_KINDS: tuple[str, ...] = ("note", "update_note", "delete_note", "like", "undo_like", "announce", "undo_announce", "quote_request", "quote_authorize", "quote_reject", "quote_revoke")
# The HMAC label of a note number. The label of a published document is ``federation_objects``: two numbers of the same millisecond differ, except for one case in 65536, and ``taken`` finds that case.
_NUMBER_LABEL = "federation_notes"
_NOTE_COLUMNS = "public_id, account_id, in_reply_to_iri, audience_iri, content_text, content_html, mentions_json, summary, sensitive, language, tags_json, edited_at, published_at, updated_at, deleted_at, quote_of_iri, quote_author_iri, quote_state, quote_authorization_iri, quote_policy"
ItemFlag = Literal["favourited", "boosted"]


@dataclass(frozen=True, slots=True)
class Note:
    public_id: int
    account_id: int
    in_reply_to_iri: str | None
    audience_iri: str | None
    content_text: str
    content_html: str
    # Each mention: ``{"handle": "@name@host" or None, "iri": actor IRI or None}``. One of the two is given.
    mentions: list[dict[str, Any]]
    summary: str | None
    sensitive: bool
    language: str | None
    # The hashtags without ``#``.
    tags: list[str]
    edited_at: float | None
    published_at: float
    updated_at: float
    deleted_at: float | None
    # Quote (FEP-044f, Section 5.4 rules 5 and 6, unit FP4-9Q): the quoted object and its author, the state (``None`` for a note without a quote), the QuoteAuthorization of an approved quote, and the quote policy of this note.
    quote_of_iri: str | None = None
    quote_author_iri: str | None = None
    quote_state: str | None = None
    quote_authorization_iri: str | None = None
    quote_policy: str = "public"


def _note(row: sqlite3.Row | tuple) -> Note:
    v = tuple(row)
    return Note(
        public_id=int(v[0]), account_id=int(v[1]), in_reply_to_iri=v[2], audience_iri=v[3], content_text=str(v[4]), content_html=str(v[5]), mentions=json.loads(v[6]), summary=v[7], sensitive=bool(v[8]), language=v[9],
        tags=json.loads(v[10]), edited_at=None if v[11] is None else float(v[11]), published_at=float(v[12]), updated_at=float(v[13]), deleted_at=None if v[14] is None else float(v[14]),
        quote_of_iri=v[15], quote_author_iri=v[16], quote_state=v[17], quote_authorization_iri=v[18], quote_policy=str(v[19]),
    )


def get_note(conn: sqlite3.Connection, public_id: int) -> Note | None:
    """The note with this number, also when it is deleted (``deleted_at``)."""
    row = conn.execute(f"SELECT {_NOTE_COLUMNS} FROM federation_notes WHERE public_id=?", (public_id,)).fetchone()
    return None if row is None else _note(row)


def add_note(conn: sqlite3.Connection, *, account_id: int, salt: bytes, taken: Callable[[int], bool], in_reply_to_iri: str | None, audience_iri: str | None, content_text: str, content_html: str, mentions: list[dict[str, Any]], summary: str | None, sensitive: bool, language: str | None, tags: list[str], now: float, quote_of_iri: str | None = None, quote_author_iri: str | None = None, quote_policy: str = "public") -> Note:
    """Store a new note and return it. A note with ``quote_of_iri`` starts with the quote state ``pending`` (Section 5.4 rule 5). ``taken(number)`` is true when a published document or a tombstone of ``claire.db`` has the number (RT7); then the next number of the same millisecond is tried."""
    unix_ms = int(now * 1000)
    for seq in range(ids.MAX_INSERT_ATTEMPTS):
        number = ids.timestamp_id(_NUMBER_LABEL, salt=salt, unix_ms=unix_ms, seq=seq)
        if taken(number) or conn.execute("SELECT 1 FROM federation_notes WHERE public_id=?", (number,)).fetchone() is not None:
            continue
        conn.execute(
            "INSERT INTO federation_notes (public_id, account_id, in_reply_to_iri, audience_iri, content_text, content_html, mentions_json, summary, sensitive, language, tags_json, published_at, updated_at, quote_of_iri, quote_author_iri, quote_state, quote_policy) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (number, account_id, in_reply_to_iri, audience_iri, content_text, content_html, json.dumps(mentions, ensure_ascii=False), summary, 1 if sensitive else 0, language, json.dumps(tags, ensure_ascii=False), now, now, quote_of_iri, quote_author_iri, None if quote_of_iri is None else "pending", quote_policy),
        )
        made = get_note(conn, number)
        assert made is not None
        return made
    raise ids.TimestampIdExhausted(f"federation_notes.public_id: {ids.MAX_INSERT_ATTEMPTS} collisions at unix_ms={unix_ms}")


def update_note(conn: sqlite3.Connection, public_id: int, *, content_text: str, content_html: str, mentions: list[dict[str, Any]], summary: str | None, sensitive: bool, language: str | None, tags: list[str], now: float) -> Note | None:
    """Change the composition fields of a note that is not deleted (Section 5.4 rule 12) and set ``edited_at``. Return the note, or ``None``."""
    changed = conn.execute(
        "UPDATE federation_notes SET content_text=?, content_html=?, mentions_json=?, summary=?, sensitive=?, language=?, tags_json=?, edited_at=?, updated_at=? WHERE public_id=? AND deleted_at IS NULL",
        (content_text, content_html, json.dumps(mentions, ensure_ascii=False), summary, 1 if sensitive else 0, language, json.dumps(tags, ensure_ascii=False), now, now, public_id),
    ).rowcount
    return get_note(conn, public_id) if changed else None


def set_note_mentions(conn: sqlite3.Connection, public_id: int, *, mentions: list[dict[str, Any]], content_html: str) -> bool:
    """Record the actors that the sidecar resolved for the mentions of a note, with the HTML that has their links. ``updated_at`` does not change: the note has no new content."""
    return conn.execute("UPDATE federation_notes SET mentions_json=?, content_html=? WHERE public_id=? AND deleted_at IS NULL", (json.dumps(mentions, ensure_ascii=False), content_html, public_id)).rowcount == 1


def mark_note_deleted(conn: sqlite3.Connection, public_id: int, *, now: float) -> bool:
    """Delete a note (Section 5.4 rule 7): the row stays as the tombstone, without the text, the HTML, the content warning, and the tags. The reply target, the audience, and the mentions stay: the ``delete_note`` task sends ``Delete`` to them."""
    return conn.execute("UPDATE federation_notes SET content_text='', content_html='', summary=NULL, sensitive=0, tags_json='[]', deleted_at=?, updated_at=? WHERE public_id=? AND deleted_at IS NULL", (now, now, public_id)).rowcount == 1


def delete_note(conn: sqlite3.Connection, note: Note, *, actor_iri: str, object_iri: str, now: float) -> int:
    """Delete an own note with its timeline item and make the ``delete_note`` task. Return the task ID. The three changes are in one transaction of the caller."""
    mark_note_deleted(conn, note.public_id, now=now)
    client_store.delete_timeline_items(conn, object_iri, actor_iri)
    return client_store.add_client_task(conn, account_id=note.account_id, kind="delete_note", target_iri=None, ref_id=note.public_id, now=now)


def count_notes(conn: sqlite3.Connection, account_id: int) -> int:
    """The notes of the account that are not deleted (the outbox collection counts them)."""
    return int(conn.execute("SELECT COUNT(*) FROM federation_notes WHERE account_id=? AND deleted_at IS NULL", (account_id,)).fetchone()[0])


def list_notes(conn: sqlite3.Connection, account_id: int, *, limit: int, max_id: int | None = None, min_id: int | None = None, since_id: int | None = None) -> list[Note]:
    """One cursor page of the notes of an account that are not deleted, newest first. The cursors are those of ``federation.list_objects``."""
    if limit < 1:
        raise ValueError("limit must be 1 or more")
    sql = f"SELECT {_NOTE_COLUMNS} FROM federation_notes WHERE account_id=? AND deleted_at IS NULL"
    params: list[object] = [account_id]
    if max_id is not None:
        sql += " AND public_id < ?"
        params.append(max_id)
    for value in (min_id, since_id):
        if value is not None:
            sql += " AND public_id > ?"
            params.append(value)
    sql += f" ORDER BY public_id {'ASC' if min_id is not None else 'DESC'} LIMIT ?"
    params.append(limit)
    rows = [_note(row) for row in conn.execute(sql, params).fetchall()]
    if min_id is not None:
        rows.reverse()
    return rows


# --- Own note as an item of the timeline (Section 5.4 rule 9) --------------


def note_timeline_item(note: Note, *, object_iri: str, author: dict[str, Any], context_iri: str | None) -> dict[str, Any]:
    """The normal form of an own note for ``federation_client.upsert_timeline_item`` and ``update_timeline_items``. The render route sanitizes ``content_html`` like the HTML of a remote server (Section 8)."""
    return {
        "object_iri": object_iri, "url": object_iri, "type": "Note", "author": author, "name": None, "summary": note.summary, "sensitive": note.sensitive, "content_html": note.content_html, "language": note.language,
        "attachments": [], "context": context_iri, "in_reply_to": note.in_reply_to_iri, "quote": note.quote_of_iri, "published_at": note.published_at, "updated_at": note.edited_at,
    }


# --- Mentions and audience -------------------------------------------------


def known_actor_iris(conn: sqlite3.Connection, account_id: int, handles: list[str]) -> dict[str, str | None]:
    """The actor IRI of each handle that Claire knows without a network request: a follow of the account that the owner gave as this handle, or the author of an item of the account. Other handles map to ``None``; the sidecar resolves them."""
    found: dict[str, str | None] = {handle: None for handle in handles}
    if not handles:
        return found
    wanted = {handle.lower(): handle for handle in handles}
    for request, actor_iri in conn.execute("SELECT request, actor_iri FROM federation_following WHERE account_id=? AND actor_iri IS NOT NULL", (account_id,)):
        key = str(request).strip().lower()
        key = key if key.startswith("@") else f"@{key}"
        if key in wanted:
            found[wanted[key]] = str(actor_iri)
    for handle in handles:
        if found[handle] is None:
            # author_json is canonical JSON of the sidecar profile; the handle is one of its values.
            row = conn.execute("SELECT author_iri, author_json FROM federation_timeline_items WHERE account_id=? AND author_json LIKE ? ESCAPE '\\' ORDER BY id DESC LIMIT 20", (account_id, "%" + handle.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")).fetchall()
            for author_iri, author_json in row:
                if str(json.loads(author_json).get("handle") or "").lower() == handle.lower():
                    found[handle] = str(author_iri)
                    break
    return found


def group_audience(conn: sqlite3.Connection, account_id: int, announced_by_iri: str | None) -> str | None:
    """The group of a thread (FEP-1b12): the announcer of the parent item, when the account follows it and it is a ``Group``. If not, ``None``."""
    if announced_by_iri is None:
        return None
    row = conn.execute("SELECT 1 FROM federation_following WHERE account_id=? AND actor_iri=? AND actor_type='Group' AND state='accepted'", (account_id, announced_by_iri)).fetchone()
    return announced_by_iri if row is not None else None


# --- Favourite and boost (Section 5.4 rule 4) ------------------------------


def item_flags(conn: sqlite3.Connection, account_id: int, item_ids: list[int]) -> dict[int, dict[str, Any]]:
    """``favourited``, ``boosted``, the object IRI, and the author IRI of the given items of the account."""
    if not item_ids:
        return {}
    marks = ",".join("?" for _ in item_ids)
    rows = conn.execute(f"SELECT id, favourited, boosted, object_iri, author_iri FROM federation_timeline_items WHERE account_id=? AND id IN ({marks})", (account_id, *item_ids)).fetchall()
    return {int(r[0]): {"favourited": bool(r[1]), "boosted": bool(r[2]), "object_iri": str(r[3]), "author_iri": str(r[4])} for r in rows}


def set_item_flag(conn: sqlite3.Connection, account_id: int, item_id: int, flag: ItemFlag, value: bool) -> bool:
    """Set ``favourited`` or ``boosted`` of an item. Return ``True`` when the value changed."""
    if flag not in ("favourited", "boosted"):
        raise ValueError(f"unknown item flag: {flag!r}")
    return conn.execute(f"UPDATE federation_timeline_items SET {flag}=? WHERE id=? AND account_id=? AND {flag}!=?", (1 if value else 0, item_id, account_id, 1 if value else 0)).rowcount == 1


# --- Tasks for the sidecar (Section 5.2) -----------------------------------


def lease_interaction_tasks(conn: sqlite3.Connection, *, account_ids: list[int], now: float, lease_seconds: float, limit: int) -> list[client_store.ClientTask]:
    """Give a lease on the oldest ready tasks of the kinds ``INTERACTION_KINDS`` for the accounts in ``account_ids`` (the accounts that are actors now) and return them. The rule of the lease is that of ``federation_client.lease_client_tasks``."""
    if limit < 1 or lease_seconds <= 0:
        raise ValueError("limit and lease_seconds must be more than 0")
    if not account_ids:
        return []
    marks = ",".join("?" for _ in account_ids)
    kinds = ",".join("?" for _ in INTERACTION_KINDS)
    rows = conn.execute(
        f"SELECT id FROM federation_client_outbox WHERE status='pending' AND (lease_until IS NULL OR lease_until <= ?) AND account_id IN ({marks}) AND kind IN ({kinds}) ORDER BY id LIMIT ?",
        (now, *account_ids, *INTERACTION_KINDS, limit),
    ).fetchall()
    leased = []
    for row in rows:
        if conn.execute("UPDATE federation_client_outbox SET lease_until=? WHERE id=? AND status='pending' AND (lease_until IS NULL OR lease_until <= ?)", (now + lease_seconds, int(row[0]), now)).rowcount:
            task = client_store.get_client_task(conn, int(row[0]))
            assert task is not None
            leased.append(task)
    return leased


def original_task_id(conn: sqlite3.Connection, task: client_store.ClientTask) -> int | None:
    """The task whose activity an ``undo_like`` or ``undo_announce`` task undoes: the last ``like`` or ``announce`` task of the same account and object before it. The ``Undo`` has the activity ID of that task (Section 5.4 rule 4)."""
    original = {"undo_like": "like", "undo_announce": "announce"}.get(task.kind)
    if original is None:
        return None
    row = conn.execute("SELECT MAX(id) FROM federation_client_outbox WHERE account_id=? AND kind=? AND target_iri=? AND id<?", (task.account_id, original, task.target_iri, task.id)).fetchone()
    return None if row is None or row[0] is None else int(row[0])
