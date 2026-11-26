"""Fill of recent posts after a follow (DOCUMENT_RENDITIONS_AND_PUBLICATION_DESIGN.md Section 5.13, phase E10a; client.db version 13).

An account that follows a remote actor sees some posts of that actor at once, not only the posts that come after the follow. The ``Accept`` of the follow asks for one fill (``request_fill``); the sidecar reads the first page of the ``featured`` collection and of the ``outbox`` of the actor with the key of the account and gives the posts to Claire (``store_objects``). A filled post is an item with ``in_timeline`` 0 and ``filled`` 1: the home stream orders by storage, and old posts must not go to its top.

The queue is this table, not the client outbox: the kinds of the outbox are a ``CHECK`` of its table, and a fill has its own state (the limits of a domain, the copy of a fill of another account, the time of the last fill).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from . import federation_client as client_store

__all__ = [
    "COPY_SECONDS",
    "DOMAIN_HOURLY_LIMIT",
    "LEASE_SECONDS",
    "MAX_ATTEMPTS",
    "MAX_POSTS",
    "REFILL_SECONDS",
    "Fill",
    "add_filled_column",
    "ensure_schema",
    "finish",
    "get_fill",
    "lease",
    "request_fill",
    "store_objects",
]

# Section 5.13 rule 7: 20 posts for one fill, 30 fills of one remote domain in one hour for the server, and the copy of a fill of another account of the last hour.
MAX_POSTS = 20
DOMAIN_HOURLY_LIMIT = 30
COPY_SECONDS = 3600.0
# Rule 1: a new follow of the same actor fills again only when the last fill is older than 24 hours.
REFILL_SECONDS = 24 * 3600.0
LEASE_SECONDS = 120.0
MAX_ATTEMPTS = 3

SCHEMA = """
CREATE TABLE IF NOT EXISTS federation_fills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL,
    actor_iri TEXT NOT NULL,
    domain TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'finished', 'copied', 'failed')),
    requested_at REAL NOT NULL,
    started_at REAL,
    lease_until REAL,
    attempts INTEGER NOT NULL DEFAULT 0,
    finished_at REAL,
    stored INTEGER,
    UNIQUE (account_id, actor_iri)
);
CREATE INDEX IF NOT EXISTS idx_federation_fills_ready ON federation_fills(status, requested_at);
CREATE INDEX IF NOT EXISTS idx_federation_fills_domain ON federation_fills(domain, started_at)
"""

_COLUMNS = "id, account_id, actor_iri, domain, status, requested_at, started_at, lease_until, attempts, finished_at, stored"


@dataclass(frozen=True, slots=True)
class Fill:
    id: int
    account_id: int
    actor_iri: str
    domain: str
    status: str
    requested_at: float
    started_at: float | None
    lease_until: float | None
    attempts: int
    finished_at: float | None
    stored: int | None


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Make the table when it is not there. Idempotent; the migration of ``client.db`` calls it."""
    for statement in SCHEMA.split(";"):
        if statement.strip():
            conn.execute(statement)


def add_filled_column(conn: sqlite3.Connection) -> None:
    """Add the mark ``filled`` to the timeline items (version 13). Idempotent."""
    columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(federation_timeline_items)")}
    if columns and "filled" not in columns:
        conn.execute("ALTER TABLE federation_timeline_items ADD COLUMN filled INTEGER NOT NULL DEFAULT 0")


def _fill(row: Any) -> Fill:
    v = tuple(row)
    return Fill(int(v[0]), int(v[1]), str(v[2]), str(v[3]), str(v[4]), float(v[5]), v[6], v[7], int(v[8]), v[9], v[10])


def get_fill(conn: sqlite3.Connection, fill_id: int) -> Fill | None:
    row = conn.execute(f"SELECT {_COLUMNS} FROM federation_fills WHERE id=?", (fill_id,)).fetchone()
    return None if row is None else _fill(row)


def _copy(conn: sqlite3.Connection, *, account_id: int, actor_iri: str, followed: set[str] | None = None, retention_days: int = 0, now: float) -> int | None:
    """Rule 7: when another account filled the same actor in the last hour, copy its filled items and read nothing. Return the number of copied items, or ``None`` when no such fill exists."""
    source = conn.execute("SELECT account_id FROM federation_fills WHERE actor_iri=? AND account_id<>? AND status IN ('finished', 'copied') AND finished_at>=? ORDER BY finished_at DESC LIMIT 1", (actor_iri, account_id, now - COPY_SECONDS)).fetchone()
    if source is None:
        return None
    copied = 0
    for object_iri, in_reply_to_iri, published_at in conn.execute("SELECT object_iri, in_reply_to_iri, published_at FROM federation_timeline_items WHERE account_id=? AND author_iri=? AND filled=1", (int(source[0]), actor_iri)).fetchall():
        if retention_days > 0 and (published_at is None or float(published_at) < now - retention_days * 86400.0):
            continue
        if followed is not None and isinstance(in_reply_to_iri, str) and not _reply_kept(conn, account_id, actor_iri, in_reply_to_iri, followed):
            continue
        made = conn.execute(
            "INSERT OR IGNORE INTO federation_timeline_items (account_id, object_iri, url, object_type, author_iri, author_json, name, summary, sensitive, content_html, language, attachments_json, context_iri, in_reply_to_iri, quote_of_iri, published_at, updated_at, in_timeline, mention, received_at, unread, filled) "
            "SELECT ?, object_iri, url, object_type, author_iri, author_json, name, summary, sensitive, content_html, language, attachments_json, context_iri, in_reply_to_iri, quote_of_iri, published_at, updated_at, 0, 0, ?, 0, 1 FROM federation_timeline_items WHERE account_id=? AND object_iri=?",
            (account_id, now, int(source[0]), str(object_iri)),
        ).rowcount
        copied += int(bool(made))
    return copied


def request_fill(conn: sqlite3.Connection, account_id: int, actor_iri: str, *, now: float, followed: set[str] | None = None, retention_days: int = 0) -> Fill | None:
    """Ask for the fill of a follow that became ``accepted`` (rule 1). Return the fill, or ``None`` when the last fill of the same account and actor is less than 24 hours old or the address has no host. A fill of another account of the last hour is copied at once (status ``copied``). The caller commits."""
    domain = (urlsplit(actor_iri).hostname or "").lower()
    if not domain:
        return None
    row = conn.execute(f"SELECT {_COLUMNS} FROM federation_fills WHERE account_id=? AND actor_iri=?", (account_id, actor_iri)).fetchone()
    if row is not None and now - float(row[5]) < REFILL_SECONDS:
        return None
    conn.execute(
        "INSERT INTO federation_fills (account_id, actor_iri, domain, status, requested_at) VALUES (?, ?, ?, 'pending', ?) "
        "ON CONFLICT(account_id, actor_iri) DO UPDATE SET status='pending', requested_at=excluded.requested_at, started_at=NULL, lease_until=NULL, attempts=0, finished_at=NULL, stored=NULL",
        (account_id, actor_iri, domain, now),
    )
    copied = _copy(conn, account_id=account_id, actor_iri=actor_iri, followed=followed, retention_days=retention_days, now=now)
    if copied is not None:
        conn.execute("UPDATE federation_fills SET status='copied', finished_at=?, stored=? WHERE account_id=? AND actor_iri=?", (now, copied, account_id, actor_iri))
    found = conn.execute(f"SELECT {_COLUMNS} FROM federation_fills WHERE account_id=? AND actor_iri=?", (account_id, actor_iri)).fetchone()
    return _fill(found)


def lease(conn: sqlite3.Connection, *, now: float, limit: int, account_ids: set[int]) -> list[Fill]:
    """The fills that wait, each with a lease. A fill of an account that is not an actor waits. A domain with 30 started fills in the last hour waits (rule 7). A fill with ``MAX_ATTEMPTS`` leases and no end becomes ``failed``. The caller commits."""
    out: list[Fill] = []
    for row in conn.execute(f"SELECT {_COLUMNS} FROM federation_fills WHERE status='pending' AND (lease_until IS NULL OR lease_until<=?) ORDER BY requested_at, id", (now,)).fetchall():
        fill = _fill(row)
        if fill.attempts >= MAX_ATTEMPTS:
            conn.execute("UPDATE federation_fills SET status='failed', lease_until=NULL, finished_at=? WHERE id=?", (now, fill.id))
            continue
        if fill.account_id not in account_ids:
            continue
        if len(out) >= limit:
            break
        started = int(conn.execute("SELECT COUNT(*) FROM federation_fills WHERE domain=? AND started_at>=? AND id<>?", (fill.domain, now - 3600.0, fill.id)).fetchone()[0])
        if fill.started_at is None and started >= DOMAIN_HOURLY_LIMIT:
            continue
        conn.execute("UPDATE federation_fills SET attempts=attempts+1, lease_until=?, started_at=COALESCE(started_at, ?) WHERE id=?", (now + LEASE_SECONDS, now, fill.id))
        found = get_fill(conn, fill.id)
        assert found is not None
        out.append(found)
    return out


def finish(conn: sqlite3.Connection, fill_id: int, *, now: float) -> None:
    """The last call of the sidecar for a fill. The caller commits."""
    conn.execute("UPDATE federation_fills SET status='finished', lease_until=NULL, finished_at=?, stored=COALESCE(stored, 0) WHERE id=? AND status='pending'", (now, fill_id))


def _reply_kept(conn: sqlite3.Connection, account_id: int, actor_iri: str, in_reply_to: str, followed: set[str]) -> bool:
    """Rule 3: a reply stays when it replies to the actor itself or to an actor that the account follows. The address of the reply target is a post of one of them when it is under the address of the actor, or when the account has it as an item of that author."""
    authors = {actor_iri, *followed}
    if any(in_reply_to.startswith(author.rstrip("/") + "/") for author in authors):
        return True
    row = conn.execute("SELECT author_iri FROM federation_timeline_items WHERE account_id=? AND object_iri=? LIMIT 1", (account_id, in_reply_to)).fetchone()
    return row is not None and str(row[0]) in authors


def store_objects(conn: sqlite3.Connection, fill: Fill, objects: list[dict[str, Any]], *, followed: set[str], retention_days: int, now: float) -> int:
    """Store the posts of one fill and return the number of new items (rules 3, 6, and 7). A post stays when its author is the actor of the fill, its ``published`` is in the retention of the timeline (0 days: no limit), and it is not a reply to an actor that the account does not follow. 20 posts at most for one fill. An item that the account has already keeps its place and gets no mark. The caller commits."""
    stored = int(fill.stored or 0)
    new = 0
    for obj in objects:
        if stored + new >= MAX_POSTS:
            break
        author = obj.get("author") or {}
        if author.get("iri") != fill.actor_iri:
            continue
        published = obj.get("published_at")
        if retention_days > 0 and (not isinstance(published, (int, float)) or published < now - retention_days * 86400.0):
            continue
        in_reply_to = obj.get("in_reply_to")
        if isinstance(in_reply_to, str) and not _reply_kept(conn, fill.account_id, fill.actor_iri, in_reply_to, followed):
            continue
        if client_store.upsert_timeline_item(conn, account_id=fill.account_id, item=obj, announced_by=None, in_timeline=False, now=now):
            conn.execute("UPDATE federation_timeline_items SET filled=1, unread=0 WHERE account_id=? AND object_iri=?", (fill.account_id, obj["object_iri"]))
            new += 1
    conn.execute("UPDATE federation_fills SET stored=? WHERE id=?", (stored + new, fill.id))
    return new
