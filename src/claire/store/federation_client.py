"""Store functions of the federation client (FEDERATION_REMOTE_READING_DESIGN.md Sections 5.1, 7.1, 7.3, and 10, unit FP4-2).

Two files (DATA_STORE_LAYOUT_DESIGN.md, RD27):

- ``client.db`` (``store.client_db``): ``federation_following``, ``federation_client_outbox``. The functions with ``conn`` for these tables take a ``client.db`` connection.
- ``claire.db``: the reading settings of the server, one JSON value in ``server_settings`` (key ``federation_reading``). ``get_reading_settings`` and ``set_reading_settings`` take a ``claire.db`` connection.

- ``federation_timeline_items`` (``client.db`` version 2, unit FP4-4) has the received posts of an account. The key is ``(account_id, object_iri)``. ``content_html`` is the raw HTML of the remote server: only the render route sanitizes it (Section 8).
- A follow starts as ``pending`` with the handle or IRI that the owner gave (``request``). ``actor_iri`` is ``None`` until the sidecar resolves it (FP4-3).
- The retention of received posts is the server value for each account (Section 10 rule 1). It is a right of the server admin only (owner decision 2026-11-18): an account has no retention value.

No function in this module commits, except ``set_reading_settings``. The caller owns the transaction.
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from typing import Any, Literal

__all__ = [
    "FOLLOWING_STATES",
    "READING_DEFAULTS",
    "READING_RANGES",
    "READING_SETTING_KEY",
    "Following",
    "ReadingSettingsError",
    "add_following",
    "count_following",
    "expired_item_ids",
    "get_following",
    "get_reading_settings",
    "list_following",
    "CONTRACT_CLIENT_KINDS",
    "ClientTask",
    "ack_client_task",
    "add_client_task",
    "count_accepted_following",
    "follows_on_domain",
    "following_for_response",
    "get_client_task",
    "lease_client_tasks",
    "resolve_following",
    "remove_following",
    "retention_run",
    "set_following_state",
    "set_reading_settings",
    "validate_reading_update",
    "TIMELINE_CONTENT_MAX_BYTES",
    "TIMELINE_MAX_ATTACHMENTS",
    "TimelineItem",
    "accounts_following_actor",
    "count_timeline",
    "get_timeline_item",
    "link_timeline_item",
    "list_timeline",
    "set_timeline_flags",
    "timeline_thread",
    "delete_announced_items",
    "delete_timeline_items",
    "is_following",
    "timeline_item_author",
    "update_timeline_items",
    "upsert_timeline_item",
    "add_object_reaction",
    "count_object_reactions",
    "count_private_messages",
    "recent_mention_authors",
    "record_private_message",
    "remove_object_reaction",
    "THREAD_FETCH_KIND",
    "last_thread_fetch",
    "request_thread_fetch",
    "store_context_objects",
]

FollowingState = Literal["pending", "accepted", "rejected"]
FOLLOWING_STATES: tuple[str, ...] = ("pending", "accepted", "rejected")
READING_SETTING_KEY = "federation_reading"
_MIB = 1024 * 1024

# Section 10: default and range of each server value. Each value is an int, except ``context_fetch``.
READING_DEFAULTS: dict[str, Any] = {
    "retention_days": 90,
    "following_limit": 1000,
    "media_image_max_bytes": 20 * _MIB,
    "media_av_max_bytes": 100 * _MIB,
    "media_evict_days": 30,
    "context_fetch": "on",
    "context_depth": 5,
    "context_max_items": 100,
    "context_refetch_minutes": 15,
    "note_max_chars": 5000,
    "mention_rate_per_hour": 100,
    "auto_reply_hours": 24,
}
_READING_RANGES: dict[str, tuple[int, int]] = {
    "retention_days": (1, 3650),
    "following_limit": (1, 100000),
    "media_image_max_bytes": (1 * _MIB, 100 * _MIB),
    "media_av_max_bytes": (1 * _MIB, 1024 * _MIB),
    "media_evict_days": (1, 365),
    "context_depth": (0, 20),
    "context_max_items": (0, 1000),
    "context_refetch_minutes": (1, 1440),
    "note_max_chars": (500, 100000),
    "mention_rate_per_hour": (1, 10000),
    "auto_reply_hours": (1, 720),
}
# The ranges for a form (the console fields of FP4-5). ``validate_reading_update`` is the check.
READING_RANGES: dict[str, tuple[int, int]] = dict(_READING_RANGES)
_CONTEXT_FETCH_VALUES = ("on", "off")
_FOLLOWING_COLUMNS = "id, account_id, request, actor_iri, actor_type, inbox_iri, shared_inbox_iri, display_name, state, created_at, updated_at"
_MAX_REQUEST_LENGTH = 2048


class ReadingSettingsError(ValueError):
    """A value of the reading settings is not valid. ``field`` names the first value that is not valid."""

    def __init__(self, field: str, message: str):
        super().__init__(message)
        self.field = field


@dataclass(frozen=True, slots=True)
class Following:
    id: int
    account_id: int
    request: str
    actor_iri: str | None
    actor_type: str | None
    inbox_iri: str | None
    shared_inbox_iri: str | None
    display_name: str | None
    state: FollowingState
    created_at: float
    updated_at: float


def _following(row: sqlite3.Row | tuple) -> Following:
    values = tuple(row)
    return Following(int(values[0]), int(values[1]), str(values[2]), *values[3:8], values[8], float(values[9]), float(values[10]))


# --- Follows ---------------------------------------------------------------


def add_following(conn: sqlite3.Connection, *, account_id: int, request: str, now: float) -> tuple[Following, bool]:
    """Add a ``pending`` follow for the handle or IRI ``request``. Return the follow and ``True`` when it is new. A follow of the same account with the same request that is not resolved yet is returned again (``False``)."""
    text = (request or "").strip()
    if not text or len(text) > _MAX_REQUEST_LENGTH or any(ord(c) < 32 for c in text):
        raise ValueError("request must be a handle or an IRI without control characters")
    row = conn.execute(f"SELECT {_FOLLOWING_COLUMNS} FROM federation_following WHERE account_id=? AND request=? AND actor_iri IS NULL", (account_id, text)).fetchone()
    if row is not None:
        return _following(row), False
    cur = conn.execute("INSERT INTO federation_following (account_id, request, state, created_at, updated_at) VALUES (?, ?, 'pending', ?, ?)", (account_id, text, now, now))
    found = get_following(conn, account_id, int(cur.lastrowid))
    assert found is not None
    return found, True


def get_following(conn: sqlite3.Connection, account_id: int, following_id: int) -> Following | None:
    row = conn.execute(f"SELECT {_FOLLOWING_COLUMNS} FROM federation_following WHERE id=? AND account_id=?", (following_id, account_id)).fetchone()
    return None if row is None else _following(row)


def count_following(conn: sqlite3.Connection, account_id: int) -> int:
    """All follows of the account that are not ``rejected``. The following limit (Section 10 rule 3) counts these."""
    return int(conn.execute("SELECT COUNT(*) FROM federation_following WHERE account_id=? AND state!='rejected'", (account_id,)).fetchone()[0])


def list_following(conn: sqlite3.Connection, account_id: int, *, limit: int, max_id: int | None = None) -> list[Following]:
    """Follows of the account, newest first. ``max_id`` gives the follows with a lower ID (cursor)."""
    if limit < 1:
        raise ValueError("limit must be more than 0")
    if max_id is None:
        rows = conn.execute(f"SELECT {_FOLLOWING_COLUMNS} FROM federation_following WHERE account_id=? ORDER BY id DESC LIMIT ?", (account_id, limit)).fetchall()
    else:
        rows = conn.execute(f"SELECT {_FOLLOWING_COLUMNS} FROM federation_following WHERE account_id=? AND id<? ORDER BY id DESC LIMIT ?", (account_id, max_id, limit)).fetchall()
    return [_following(r) for r in rows]


def set_following_state(conn: sqlite3.Connection, following_id: int, state: FollowingState, *, now: float) -> bool:
    if state not in FOLLOWING_STATES:
        raise ValueError(f"unknown following state: {state!r}")
    return conn.execute("UPDATE federation_following SET state=?, updated_at=? WHERE id=?", (state, now, following_id)).rowcount == 1


def remove_following(conn: sqlite3.Connection, account_id: int, following_id: int) -> Following | None:
    """Delete the follow and return it, or ``None`` when the account has no such follow. The caller adds the ``undo_follow`` task (FP4-3). The timeline items of the follow stay (Section 5.1)."""
    found = get_following(conn, account_id, following_id)
    if found is not None:
        conn.execute("DELETE FROM federation_following WHERE id=?", (following_id,))
    return found


# --- Reading settings of the server (Section 10) -------------------------


def validate_reading_update(body: Any) -> dict[str, Any]:
    """Check a partial update of the reading settings. Each key must be known, and each value in its range (C1). Return the checked values."""
    if not isinstance(body, dict) or not body:
        raise ReadingSettingsError("", "The body must be an object with one or more settings")
    checked: dict[str, Any] = {}
    for key, value in body.items():
        if key not in READING_DEFAULTS:
            raise ReadingSettingsError(key, f"Unknown setting: {key}")
        if key == "context_fetch":
            if value not in _CONTEXT_FETCH_VALUES:
                raise ReadingSettingsError(key, "context_fetch must be on or off")
            checked[key] = value
            continue
        low, high = _READING_RANGES[key]
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            raise ReadingSettingsError(key, f"{key} must be an integer from {low} to {high}")
        checked[key] = value
    return checked


def get_reading_settings(conn: sqlite3.Connection) -> dict[str, Any]:
    """All reading settings: the stored values over the defaults. A stored value that is not valid raises ``RuntimeError`` (C1): only ``set_reading_settings`` writes the value."""
    row = conn.execute("SELECT value FROM server_settings WHERE key=?", (READING_SETTING_KEY,)).fetchone()
    values = dict(READING_DEFAULTS)
    if row is None:
        return values
    try:
        stored = json.loads(str(row[0]))
        values.update(validate_reading_update(stored) if stored else {})
    except (ValueError, TypeError) as exc:
        raise RuntimeError(f"server_settings.{READING_SETTING_KEY} is not valid: {exc}") from exc
    return values


def set_reading_settings(conn: sqlite3.Connection, update: dict[str, Any], *, updated_by: int | None, now: float | None = None) -> dict[str, Any]:
    """Apply a checked partial update and commit. Only values that differ from the default are stored. Return all settings."""
    values = get_reading_settings(conn)
    values.update(validate_reading_update(update))
    stored = {k: v for k, v in values.items() if v != READING_DEFAULTS[k]}
    conn.execute(
        "INSERT INTO server_settings (key, value, updated_at, updated_by) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at, updated_by=excluded.updated_by",
        (READING_SETTING_KEY, json.dumps(stored, sort_keys=True), time.time() if now is None else now, updated_by),
    )
    conn.commit()
    return values


# --- Retention run (Section 10 rules 1, 2, and 4) ---------------------------


def expired_item_ids(conn: sqlite3.Connection, *, server_days: int, now: float) -> list[int]:
    """The timeline items that the retention run deletes (``client.db``).

    An item goes when it is older than the retention of the server by ``received_at``, is not starred, and has no ingested document. An own note that is not deleted keeps its item (the item has the controls of the note). An item with ``in_timeline`` = 0 that is the parent of an item that stays also stays (rule 2), through all levels.
    """
    has_notes = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='federation_notes'").fetchone() is not None
    own_note = "AND NOT EXISTS (SELECT 1 FROM federation_notes n WHERE n.account_id=i.account_id AND n.deleted_at IS NULL AND i.object_iri LIKE '%/ap/objects/' || n.public_id)" if has_notes else ""
    expired: list[int] = []
    for (account_id,) in conn.execute("SELECT DISTINCT account_id FROM federation_timeline_items").fetchall():
        rows = conn.execute(f"SELECT i.id, i.object_iri, i.in_timeline FROM federation_timeline_items i WHERE i.account_id=? AND i.received_at < ? AND i.starred=0 AND i.linked_doc_id IS NULL {own_note}", (account_id, now - server_days * 86400.0)).fetchall()
        candidates = {int(item_id): (str(object_iri), int(in_timeline)) for item_id, object_iri, in_timeline in rows}
        while True:
            # The parents (``in_timeline`` = 0) of the items that stay.
            marks = ",".join("?" * len(candidates)) or "NULL"
            parents = {str(row[0]) for row in conn.execute(f"SELECT DISTINCT in_reply_to_iri FROM federation_timeline_items WHERE account_id=? AND in_reply_to_iri IS NOT NULL AND id NOT IN ({marks})", (account_id, *candidates))}
            kept = [item_id for item_id, (object_iri, in_timeline) in candidates.items() if in_timeline == 0 and object_iri in parents]
            if not kept:
                break
            for item_id in kept:
                del candidates[item_id]
        expired.extend(sorted(candidates))
    return expired


def retention_run(conn: sqlite3.Connection, *, server_days: int, now: float, apply: bool) -> dict[str, int]:
    """Count, and with ``apply`` delete, the timeline items that are older than the retention. The caller commits. The media cleanup deletes the media entries that no item uses after this run."""
    ids = expired_item_ids(conn, server_days=server_days, now=now)
    if apply:
        for start in range(0, len(ids), 500):
            part = ids[start : start + 500]
            conn.execute(f"DELETE FROM federation_timeline_items WHERE id IN ({','.join('?' * len(part))})", part)
    return {"retention_days": server_days, "expired_items": len(ids)}


# --- Client outbox (Section 5.2) -------------------------------------------


def add_client_task(conn: sqlite3.Connection, *, account_id: int, kind: str, target_iri: str | None, ref_id: int | None, now: float) -> int:
    """Add one ``pending`` task to the client outbox of ``client.db`` and return its ID. ``ref_id`` is the row that the task is for (for example the follow). The caller commits, in the same transaction as the row."""
    from .client_db import CLIENT_OUTBOX_KINDS

    if kind not in CLIENT_OUTBOX_KINDS:
        raise ValueError(f"unknown client task kind: {kind!r}")
    cur = conn.execute("INSERT INTO federation_client_outbox (account_id, kind, target_iri, ref_id, status, created_at) VALUES (?, ?, ?, ?, 'pending', ?)", (account_id, kind, target_iri, ref_id, now))
    return int(cur.lastrowid)


# --- Client outbox: lease and acknowledgement (FP4-3) ----------------------

# The kinds that the internal API contract 1.3 gives to the sidecar. Other kinds wait as pending until a later unit adds them.
CONTRACT_CLIENT_KINDS: tuple[str, ...] = ("follow", "undo_follow", "reject_follow")
_CLIENT_TASK_COLUMNS = "id, account_id, kind, target_iri, ref_id, status, lease_until, error_code, created_at, handed_off_at"


@dataclass(frozen=True, slots=True)
class ClientTask:
    id: int
    account_id: int
    kind: str
    target_iri: str | None
    ref_id: int | None
    status: str
    lease_until: float | None
    error_code: str | None
    created_at: float
    handed_off_at: float | None


def _client_task(row: sqlite3.Row | tuple) -> ClientTask:
    v = tuple(row)
    return ClientTask(int(v[0]), int(v[1]), str(v[2]), v[3], None if v[4] is None else int(v[4]), str(v[5]), None if v[6] is None else float(v[6]), v[7], float(v[8]), None if v[9] is None else float(v[9]))


def get_client_task(conn: sqlite3.Connection, task_id: int) -> ClientTask | None:
    row = conn.execute(f"SELECT {_CLIENT_TASK_COLUMNS} FROM federation_client_outbox WHERE id=?", (task_id,)).fetchone()
    return None if row is None else _client_task(row)


def lease_client_tasks(conn: sqlite3.Connection, *, account_ids: list[int], now: float, lease_seconds: float, limit: int, only_kinds: tuple[str, ...] | None = None) -> list[ClientTask]:
    """Give a lease on the oldest ready tasks of the accounts in ``account_ids`` (the accounts that are actors now) and return them. Only the kinds of the contract (``CONTRACT_CLIENT_KINDS``), or the kinds in ``only_kinds`` (the thread fetch, unit FP4-6). The caller commits."""
    if limit < 1 or lease_seconds <= 0:
        raise ValueError("limit and lease_seconds must be more than 0")
    if not account_ids:
        return []
    wanted = CONTRACT_CLIENT_KINDS if only_kinds is None else only_kinds
    marks = ",".join("?" for _ in account_ids)
    kinds = ",".join("?" for _ in wanted)
    rows = conn.execute(
        f"SELECT id FROM federation_client_outbox WHERE status='pending' AND (lease_until IS NULL OR lease_until <= ?) AND account_id IN ({marks}) AND kind IN ({kinds}) ORDER BY id LIMIT ?",
        (now, *account_ids, *wanted, limit),
    ).fetchall()
    leased = []
    for row in rows:
        if conn.execute("UPDATE federation_client_outbox SET lease_until=? WHERE id=? AND status='pending' AND (lease_until IS NULL OR lease_until <= ?)", (now + lease_seconds, int(row[0]), now)).rowcount:
            task = get_client_task(conn, int(row[0]))
            assert task is not None
            leased.append(task)
    return leased


def ack_client_task(conn: sqlite3.Connection, task_id: int, *, result: Literal["handed_off", "rejected"], error_code: str | None, now: float) -> ClientTask | None:
    """Record the result of the handoff. A second acknowledgement keeps the first result. The caller commits."""
    task = get_client_task(conn, task_id)
    if task is None or task.status != "pending":
        return task
    conn.execute("UPDATE federation_client_outbox SET status=?, error_code=?, handed_off_at=?, lease_until=NULL WHERE id=?", (result, error_code, now, task_id))
    return get_client_task(conn, task_id)


# --- Follows: resolution and state (FP4-3) ---------------------------------


def resolve_following(conn: sqlite3.Connection, following_id: int, *, actor_iri: str, actor_type: str | None, inbox_iri: str, shared_inbox_iri: str | None, display_name: str | None, now: float) -> bool:
    """Record the actor that the sidecar resolved for a follow. Return ``False`` when the account already follows that actor in another row: then the caller rejects this row (the key is ``(account_id, actor_iri)``)."""
    row = conn.execute("SELECT account_id FROM federation_following WHERE id=?", (following_id,)).fetchone()
    if row is None:
        return False
    if conn.execute("SELECT 1 FROM federation_following WHERE account_id=? AND actor_iri=? AND id!=?", (int(row[0]), actor_iri, following_id)).fetchone() is not None:
        return False
    conn.execute(
        "UPDATE federation_following SET actor_iri=?, actor_type=?, inbox_iri=?, shared_inbox_iri=?, display_name=?, updated_at=? WHERE id=?",
        (actor_iri, actor_type, inbox_iri, shared_inbox_iri, display_name, now, following_id),
    )
    return True


def following_for_response(conn: sqlite3.Connection, account_id: int, following_id: int, actor_iri: str) -> Following | None:
    """The follow that an ``Accept`` or a ``Reject`` refers to: the account, the number, and the remote actor must agree."""
    found = get_following(conn, account_id, following_id)
    return found if found is not None and found.actor_iri == actor_iri else None


def count_accepted_following(conn: sqlite3.Connection, account_id: int) -> int:
    return int(conn.execute("SELECT COUNT(*) FROM federation_following WHERE account_id=? AND state='accepted'", (account_id,)).fetchone()[0])


def follows_on_domain(conn: sqlite3.Connection, domain: str) -> list[Following]:
    """The follows whose actor is on ``domain`` or on a subdomain of it (Section 5.1 rule 4)."""
    from urllib.parse import urlsplit

    found = []
    for row in conn.execute(f"SELECT {_FOLLOWING_COLUMNS} FROM federation_following WHERE actor_iri IS NOT NULL AND state!='rejected'").fetchall():
        f = _following(row)
        host = (urlsplit(f.actor_iri or "").hostname or "").lower()
        if host == domain or host.endswith("." + domain):
            found.append(f)
    return found


# --- Timeline items (Sections 6 and 7.1, unit FP4-4) -----------------------

# Section 6.3: the limits of one object. A larger event is refused (``too_large``).
TIMELINE_CONTENT_MAX_BYTES = 64 * 1024
TIMELINE_MAX_ATTACHMENTS = 8


def accounts_following_actor(conn: sqlite3.Connection, actor_iri: str, account_ids: list[int]) -> list[int]:
    """The accounts of ``account_ids`` (the accounts that are actors now) that have an accepted follow of ``actor_iri`` (Section 6.4 rule 1)."""
    if not account_ids:
        return []
    marks = ",".join("?" for _ in account_ids)
    rows = conn.execute(f"SELECT DISTINCT account_id FROM federation_following WHERE actor_iri=? AND state='accepted' AND account_id IN ({marks}) ORDER BY account_id", (actor_iri, *account_ids)).fetchall()
    return [int(r[0]) for r in rows]


def is_following(conn: sqlite3.Connection, account_id: int, actor_iri: str) -> bool:
    return conn.execute("SELECT 1 FROM federation_following WHERE account_id=? AND actor_iri=? AND state='accepted'", (account_id, actor_iri)).fetchone() is not None


def timeline_item_author(conn: sqlite3.Connection, account_id: int, object_iri: str) -> str | None:
    """The author of an item that the account has, or ``None``."""
    row = conn.execute("SELECT author_iri FROM federation_timeline_items WHERE account_id=? AND object_iri=?", (account_id, object_iri)).fetchone()
    return None if row is None else str(row[0])


def _item_values(item: dict[str, Any]) -> dict[str, Any]:
    """The content columns of an item from the normal form of the sidecar (Section 6.3)."""
    return {
        "url": item.get("url"),
        "object_type": item["type"],
        "author_json": json.dumps(item["author"], ensure_ascii=False, sort_keys=True),
        "name": item.get("name"),
        "summary": item.get("summary"),
        "sensitive": 1 if item.get("sensitive") else 0,
        "content_html": item.get("content_html") or "",
        "language": item.get("language"),
        "attachments_json": json.dumps(item.get("attachments") or [], ensure_ascii=False, sort_keys=True),
        "context_iri": item.get("context"),
        "in_reply_to_iri": item.get("in_reply_to"),
        # The object that this object quotes (FEP-044f, unit FP4-9Q), or None.
        "quote_of_iri": item.get("quote"),
        "published_at": item.get("published_at"),
        "updated_at": item.get("updated_at"),
    }


def upsert_timeline_item(conn: sqlite3.Connection, *, account_id: int, item: dict[str, Any], announced_by: dict[str, Any] | None, in_timeline: bool, now: float, mention: bool = False) -> bool:
    """Store one object for the account. Return ``True`` when the item is new.

    The event is idempotent (Section 6.4 rule 3): an item that exists keeps its content, its announcer, and its owner state (``unread``, ``starred``). Only ``in_timeline`` and ``mention`` can change, from 0 to 1: an item that came first as the parent of a reply shows in the timeline when it comes again by the timeline rule.

    ``mention``: the object mentions the account or is a reply to an object of the account (Section 6.6 rule 1).
    """
    values = _item_values(item)
    columns = ["account_id", "object_iri", "author_iri", "announced_by_iri", "announced_by_json", "in_timeline", "mention", "received_at", *values]
    params = [account_id, item["object_iri"], item["author"]["iri"], None if announced_by is None else announced_by["iri"], None if announced_by is None else json.dumps(announced_by, ensure_ascii=False, sort_keys=True), 1 if in_timeline else 0, 1 if mention else 0, now, *values.values()]
    cur = conn.execute(f"INSERT OR IGNORE INTO federation_timeline_items ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})", params)
    if cur.rowcount == 1:
        return True
    if in_timeline:
        conn.execute("UPDATE federation_timeline_items SET in_timeline=1 WHERE account_id=? AND object_iri=? AND in_timeline=0", (account_id, item["object_iri"]))
    if mention:
        conn.execute("UPDATE federation_timeline_items SET mention=1 WHERE account_id=? AND object_iri=? AND mention=0", (account_id, item["object_iri"]))
    return False


def update_timeline_items(conn: sqlite3.Connection, *, item: dict[str, Any], now: float) -> int:
    """Change the content of the items of all accounts that have the object with the same author (Section 6.1, ``Update``). Return the number of items. The reply target and the thread do not change. ``updated_at`` is the time of the object, or ``now`` when the object has none."""
    values = _item_values(item)
    for fixed in ("in_reply_to_iri", "context_iri", "published_at"):
        values.pop(fixed)
    if values["updated_at"] is None:
        values["updated_at"] = now
    assignments = ", ".join(f"{column}=?" for column in values)
    return conn.execute(f"UPDATE federation_timeline_items SET {assignments} WHERE object_iri=? AND author_iri=?", (*values.values(), item["object_iri"], item["author"]["iri"])).rowcount


def delete_timeline_items(conn: sqlite3.Connection, object_iri: str, author_iri: str) -> int:
    """Delete the items of all accounts that have the object with this author (Section 6.1, ``Delete``). Return the number of items."""
    return conn.execute("DELETE FROM federation_timeline_items WHERE object_iri=? AND author_iri=?", (object_iri, author_iri)).rowcount


def delete_announced_items(conn: sqlite3.Connection, object_iri: str, announcer_iri: str) -> int:
    """``Undo(Announce)``: delete the items that this announcer brought, for each account that does not follow the author (Section 6.1). An item that the owner starred or took into a theme stays. Return the number of items."""
    return conn.execute(
        "DELETE FROM federation_timeline_items WHERE object_iri=? AND announced_by_iri=? AND starred=0 AND linked_doc_id IS NULL "
        "AND NOT EXISTS (SELECT 1 FROM federation_following f WHERE f.account_id=federation_timeline_items.account_id AND f.actor_iri=federation_timeline_items.author_iri AND f.state='accepted')",
        (object_iri, announcer_iri),
    ).rowcount


# --- Inbound interactions with the account (Sections 6.2 rule 4 and 6.6, unit FP4-8) ---


def recent_mention_authors(conn: sqlite3.Connection, account_id: int, *, since: float) -> list[str]:
    """The authors of the mention items that the account got after ``since``, one entry for each item. The caller counts the items of one remote domain for the mention rate limit (Section 6.6 rule 6)."""
    return [str(r[0]) for r in conn.execute("SELECT author_iri FROM federation_timeline_items WHERE account_id=? AND mention=1 AND received_at>?", (account_id, since))]


def add_object_reaction(conn: sqlite3.Connection, *, object_public_id: int, account_id: int, kind: Literal["like", "announce"], actor_iri: str, activity_iri: str, now: float) -> bool:
    """Store a ``Like`` or an ``Announce`` of an object of the account (Section 6.6 rule 3). One row for each actor and kind: a repeated activity changes nothing. Return ``True`` for a new row."""
    cur = conn.execute("INSERT OR IGNORE INTO federation_object_reactions (object_public_id, account_id, kind, actor_iri, activity_iri, received_at) VALUES (?, ?, ?, ?, ?, ?)", (object_public_id, account_id, kind, actor_iri, activity_iri, now))
    return cur.rowcount == 1


def remove_object_reaction(conn: sqlite3.Connection, *, object_public_id: int, kind: Literal["like", "announce"], actor_iri: str) -> bool:
    """``Undo`` of a reaction: delete the row of this actor. Return ``True`` when a row was there."""
    return conn.execute("DELETE FROM federation_object_reactions WHERE object_public_id=? AND kind=? AND actor_iri=?", (object_public_id, kind, actor_iri)).rowcount == 1


def received_reply_iris(conn: sqlite3.Connection, object_iri: str) -> list[str]:
    """The addresses of the received posts that are replies to ``object_iri``, each one time, in the order of receipt (phase E6, the ``replies`` collection of an own object). Each stored post is public (Section 6.2); more than one account can have the same post."""
    return [str(row[0]) for row in conn.execute("SELECT object_iri FROM federation_timeline_items WHERE in_reply_to_iri=? GROUP BY object_iri ORDER BY MIN(id)", (object_iri,)).fetchall()]


def count_object_reactions(conn: sqlite3.Connection, object_public_ids: list[int]) -> dict[int, dict[str, int]]:
    """The counters of the given objects: ``{public_id: {"like": n, "announce": n}}``. An object without a reaction has zeros."""
    counts = {public_id: {"like": 0, "announce": 0} for public_id in object_public_ids}
    if not object_public_ids:
        return counts
    marks = ",".join("?" for _ in object_public_ids)
    for public_id, kind, number in conn.execute(f"SELECT object_public_id, kind, COUNT(*) FROM federation_object_reactions WHERE kind IN ('like', 'announce') AND object_public_id IN ({marks}) GROUP BY object_public_id, kind", object_public_ids):
        counts[int(public_id)][str(kind)] = int(number)
    return counts


def record_private_message(conn: sqlite3.Connection, *, account_id: int, sender_iri: str, now: float, window_seconds: float) -> bool:
    """Count one non-public message of the sender to the account (Section 6.2 rule 4). Return ``True`` when Claire must send the automatic notice: the first message of the sender, or a message after ``window_seconds`` from the last notice. No content is stored."""
    row = conn.execute("SELECT last_sent_at FROM federation_auto_replies WHERE account_id=? AND sender_iri=?", (account_id, sender_iri)).fetchone()
    if row is None:
        conn.execute("INSERT INTO federation_auto_replies (account_id, sender_iri, last_sent_at, count) VALUES (?, ?, ?, 1)", (account_id, sender_iri, now))
        return True
    send = now - float(row[0]) >= window_seconds
    conn.execute("UPDATE federation_auto_replies SET count=count+1, last_sent_at=? WHERE account_id=? AND sender_iri=?", (now if send else float(row[0]), account_id, sender_iri))
    return send


def count_private_messages(conn: sqlite3.Connection, account_id: int) -> int:
    """The number of non-public messages that the account got an automatic notice for. The client shows the number only."""
    return int(conn.execute("SELECT COALESCE(SUM(count), 0) FROM federation_auto_replies WHERE account_id=?", (account_id,)).fetchone()[0])


# --- Timeline of the owner (Sections 5.3 and 8, unit FP4-5) ----------------

_TIMELINE_COLUMNS = "id, account_id, object_iri, url, object_type, author_iri, author_json, announced_by_iri, announced_by_json, name, summary, sensitive, content_html, language, attachments_json, context_iri, in_reply_to_iri, in_timeline, published_at, updated_at, received_at, unread, starred, linked_kb_id, linked_doc_id, mention, quote_of_iri"
TimelineFilter = Literal["unread", "starred", "mention"]
# The thread view shows this number of items at most. The thread fetch of FP4-6 has its own limit (``context_max_items``).
TIMELINE_THREAD_MAX = 200


@dataclass(frozen=True, slots=True)
class TimelineItem:
    id: int
    account_id: int
    object_iri: str
    url: str | None
    object_type: str
    author_iri: str
    author: dict[str, Any]
    announced_by_iri: str | None
    announced_by: dict[str, Any] | None
    name: str | None
    summary: str | None
    sensitive: bool
    content_html: str
    language: str | None
    attachments: list[dict[str, Any]]
    context_iri: str | None
    in_reply_to_iri: str | None
    in_timeline: bool
    published_at: float | None
    updated_at: float | None
    received_at: float
    unread: bool
    starred: bool
    linked_kb_id: int | None
    linked_doc_id: str | None
    mention: bool
    quote_of_iri: str | None = None


def _timeline_item(row: sqlite3.Row | tuple) -> TimelineItem:
    v = tuple(row)
    return TimelineItem(
        id=int(v[0]), account_id=int(v[1]), object_iri=str(v[2]), url=v[3], object_type=str(v[4]), author_iri=str(v[5]), author=json.loads(v[6]), announced_by_iri=v[7], announced_by=None if v[8] is None else json.loads(v[8]),
        name=v[9], summary=v[10], sensitive=bool(v[11]), content_html=str(v[12]), language=v[13], attachments=json.loads(v[14]), context_iri=v[15], in_reply_to_iri=v[16], in_timeline=bool(v[17]),
        published_at=v[18], updated_at=v[19], received_at=float(v[20]), unread=bool(v[21]), starred=bool(v[22]), linked_kb_id=v[23], linked_doc_id=v[24], mention=bool(v[25]), quote_of_iri=v[26],
    )


def _filter_sql(filters: tuple[str, ...]) -> str:
    for name in filters:
        if name not in ("unread", "starred", "mention"):
            raise ValueError(f"unknown timeline filter: {name!r}")
    return "".join(f" AND {name}=1" for name in filters)


def list_timeline(conn: sqlite3.Connection, account_id: int, *, limit: int, max_id: int | None = None, filters: tuple[str, ...] = ()) -> list[TimelineItem]:
    """The items of the timeline of the account (``in_timeline`` = 1), newest first by ID. ``max_id`` gives the items with a lower ID (cursor, RD18)."""
    if limit < 1:
        raise ValueError("limit must be more than 0")
    cursor = "" if max_id is None else " AND id<?"
    params: tuple[Any, ...] = (account_id,) if max_id is None else (account_id, max_id)
    rows = conn.execute(f"SELECT {_TIMELINE_COLUMNS} FROM federation_timeline_items WHERE account_id=? AND in_timeline=1{_filter_sql(filters)}{cursor} ORDER BY id DESC LIMIT ?", (*params, limit)).fetchall()
    return [_timeline_item(r) for r in rows]


def count_timeline(conn: sqlite3.Connection, account_id: int) -> dict[str, int]:
    """The numbers of the stage bar: the items of the timeline, the unread ones, the starred ones, the mentions, the items that the owner took into a theme, and the non-public messages that got an automatic notice (FP4-8)."""
    row = conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(unread), 0), COALESCE(SUM(starred), 0), COALESCE(SUM(mention), 0), COALESCE(SUM(mention AND unread), 0), COALESCE(SUM(linked_doc_id IS NOT NULL), 0) FROM federation_timeline_items WHERE account_id=? AND in_timeline=1",
        (account_id,),
    ).fetchone()
    return {"total": int(row[0]), "unread": int(row[1]), "starred": int(row[2]), "mentions": int(row[3]), "unread_mentions": int(row[4]), "ingested": int(row[5]), "private_messages": count_private_messages(conn, account_id)}


def get_timeline_item(conn: sqlite3.Connection, account_id: int, item_id: int) -> TimelineItem | None:
    row = conn.execute(f"SELECT {_TIMELINE_COLUMNS} FROM federation_timeline_items WHERE id=? AND account_id=?", (item_id, account_id)).fetchone()
    return None if row is None else _timeline_item(row)


def set_timeline_flags(conn: sqlite3.Connection, account_id: int, item_id: int, *, unread: bool | None = None, starred: bool | None = None) -> TimelineItem | None:
    """Set ``unread`` and ``starred`` of an item of the account. ``None`` keeps a value. Return the item, or ``None`` when the account has no such item. The caller commits."""
    if unread is not None:
        conn.execute("UPDATE federation_timeline_items SET unread=? WHERE id=? AND account_id=?", (1 if unread else 0, item_id, account_id))
    if starred is not None:
        conn.execute("UPDATE federation_timeline_items SET starred=? WHERE id=? AND account_id=?", (1 if starred else 0, item_id, account_id))
    return get_timeline_item(conn, account_id, item_id)


def link_timeline_item(conn: sqlite3.Connection, account_id: int, item_id: int, *, kb_id: int, doc_id: str) -> bool:
    """Record the document that the owner made from the item (Section 4.1, P2). The item stays in the timeline. The caller commits."""
    return conn.execute("UPDATE federation_timeline_items SET linked_kb_id=?, linked_doc_id=? WHERE id=? AND account_id=?", (kb_id, doc_id, item_id, account_id)).rowcount == 1


def timeline_thread(conn: sqlite3.Connection, account_id: int, item_id: int) -> list[TimelineItem]:
    """All items of the thread of an item that the account has, oldest first (Section 6.5 rule 3: the thread view shows the items with ``in_timeline`` = 0 too).

    The thread is the set of items that the reply links (``in_reply_to_iri``) and the FEP-7888 ``context`` join to the item. Only stored items are in it: the fetch of a thread from the remote server is unit FP4-6.
    """
    start = get_timeline_item(conn, account_id, item_id)
    if start is None:
        return []
    found: dict[str, TimelineItem] = {start.object_iri: start}
    queue = [start]
    contexts: set[str] = set()
    while queue and len(found) < TIMELINE_THREAD_MAX:
        current = queue.pop()
        clauses, params = ["in_reply_to_iri=?"], [current.object_iri]
        if current.in_reply_to_iri is not None:
            clauses.append("object_iri=?")
            params.append(current.in_reply_to_iri)
        if current.context_iri is not None and current.context_iri not in contexts:
            contexts.add(current.context_iri)
            clauses.append("context_iri=?")
            params.append(current.context_iri)
        rows = conn.execute(f"SELECT {_TIMELINE_COLUMNS} FROM federation_timeline_items WHERE account_id=? AND ({' OR '.join(clauses)}) LIMIT ?", (account_id, *params, TIMELINE_THREAD_MAX)).fetchall()
        for row in rows:
            item = _timeline_item(row)
            if item.object_iri not in found and len(found) < TIMELINE_THREAD_MAX:
                found[item.object_iri] = item
                queue.append(item)
    return sorted(found.values(), key=lambda i: (i.published_at if i.published_at is not None else i.received_at, i.id))


# --- Thread fetch (Section 6.5 rules 4 to 6, unit FP4-6) --------------------

# The kind of the client outbox for a thread fetch. The sidecar takes these tasks with the internal endpoint ``context-tasks``, not with ``client-tasks``.
THREAD_FETCH_KIND = "fetch_context"


def last_thread_fetch(conn: sqlite3.Connection, account_id: int, object_iris: list[str]) -> ClientTask | None:
    """The newest thread fetch task of the account whose start object is one of ``object_iris`` (the stored items of one thread), or ``None``. No column has the time of the last fetch: the task rows of the client outbox have it (``target_iri``, ``created_at``)."""
    if not object_iris:
        return None
    marks = ",".join("?" for _ in object_iris)
    row = conn.execute(f"SELECT {_CLIENT_TASK_COLUMNS} FROM federation_client_outbox WHERE account_id=? AND kind=? AND target_iri IN ({marks}) ORDER BY id DESC LIMIT 1", (account_id, THREAD_FETCH_KIND, *object_iris)).fetchone()
    return None if row is None else _client_task(row)


def request_thread_fetch(conn: sqlite3.Connection, *, account_id: int, item_id: int, refetch_seconds: float, now: float) -> tuple[Literal["queued", "recent"], bool] | None:
    """Make the ``fetch_context`` task for the thread of an item (Section 6.5 rule 4). Return the state and ``True`` while a task of the thread waits for the sidecar, or ``None`` when the account has no such item. The caller commits.

    ``recent``: a task of the same thread waits, or one was made less than ``refetch_seconds`` ago; then no task is made. The result of the earlier task (handed off or rejected) makes no difference: a server that failed gets no second request in that time.

    The start object of the task is the top stored item of the reply chain of the item: the sidecar reads the ``context`` or the ``replies`` of that object. ``ref_id`` is the item.
    """
    thread = timeline_thread(conn, account_id, item_id)
    if not thread:
        return None
    by_iri = {entry.object_iri: entry for entry in thread}
    top = next(entry for entry in thread if entry.id == item_id)
    visited = {top.object_iri}
    while top.in_reply_to_iri in by_iri and top.in_reply_to_iri not in visited:
        top = by_iri[top.in_reply_to_iri]
        visited.add(top.object_iri)
    last = last_thread_fetch(conn, account_id, list(by_iri))
    if last is not None and (last.status == "pending" or now - last.created_at < refetch_seconds):
        return "recent", last.status == "pending"
    add_client_task(conn, account_id=account_id, kind=THREAD_FETCH_KIND, target_iri=top.object_iri, ref_id=item_id, now=now)
    return "queued", True


def store_context_objects(conn: sqlite3.Connection, *, account_id: int, item_id: int, objects: list[dict[str, Any]], now: float) -> tuple[int, int]:
    """Store the objects of a thread fetch for the account with ``in_timeline`` = 0 (Section 6.5 rule 5). Return the number of new items and the number of objects that are in the thread. The caller commits.

    Only an object of the thread of the item is stored: it has the ``context`` of a stored item of the thread, it is a reply to an object of the thread, or an object of the thread is a reply to it. An object without such a link cannot show in the thread view, and it is not stored (RS4). An item that exists keeps its content and its state.
    """
    thread = timeline_thread(conn, account_id, item_id)
    if not thread:
        return 0, 0
    known = {entry.object_iri for entry in thread}
    contexts = {entry.context_iri for entry in thread if entry.context_iri is not None}
    targets = {entry.in_reply_to_iri for entry in thread if entry.in_reply_to_iri is not None}
    waiting = [obj for obj in objects if obj["object_iri"] not in known]
    linked: list[dict[str, Any]] = []
    changed = True
    while changed and waiting:
        changed = False
        for obj in list(waiting):
            if obj.get("context") in contexts or obj.get("in_reply_to") in known or obj["object_iri"] in targets:
                waiting.remove(obj)
                if obj["object_iri"] in known:
                    continue
                linked.append(obj)
                known.add(obj["object_iri"])
                if obj.get("in_reply_to") is not None:
                    targets.add(obj["in_reply_to"])
                changed = True
    made = sum(upsert_timeline_item(conn, account_id=account_id, item=obj, announced_by=None, in_timeline=False, now=now) for obj in linked)
    return made, len(linked)
