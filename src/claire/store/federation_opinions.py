"""Opinions of published objects: the public replies and quote posts of the whole fediverse about an object (DOCUMENT_RENDITIONS_AND_PUBLICATION_DESIGN.md Section 5.11 B and C, phase E7a; client.db version 11; the attachments since version 12, phase E7e).

The rows are of the server, not of an account: the 의견 tab of a public page shows them. Two tables:

- ``federation_opinion_fetches``: one fetch of the opinions of one object at a time, the queue of the sidecar and the refresh state of the page (the Async Refreshes of Mastodon).
- ``federation_opinions``: one row for each opinion of an object, with its parent in the reply tree.

An opinion is a reply to the object, a reply to another opinion of the object, or a post that quotes the object (FEP-044f). Its author is on the origin of its address. The HTML of the remote server stays as it came; the page converts it to text, and the frame of one opinion cleans it.
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

__all__ = [
    "AGE_SECONDS",
    "FETCH_INTERVAL_SECONDS",
    "LEASE_SECONDS",
    "MAX_ATTEMPTS",
    "MAX_OPINIONS",
    "Fetch",
    "Opinion",
    "add_attachments_column",
    "count_visible_opinions",
    "delete_for_object",
    "delete_opinion",
    "ensure_schema",
    "finish",
    "forget",
    "get_fetch",
    "hide",
    "lease",
    "object_iris",
    "opinion_key",
    "opinions_of",
    "request_fetch",
    "store_objects",
]

# The conditions of a fetch (Section 5.11 B2): the last fetch is older than 15 minutes, and the object is older than 5 minutes.
FETCH_INTERVAL_SECONDS = 15 * 60.0
AGE_SECONDS = 5 * 60.0
# The limits of one object (Section 5.11 B5).
MAX_OPINIONS = 1000
LEASE_SECONDS = 300.0
MAX_ATTEMPTS = 3

SCHEMA = """
CREATE TABLE IF NOT EXISTS federation_opinion_fetches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    object_iri TEXT NOT NULL UNIQUE,
    own INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'finished', 'failed')),
    requested_at REAL NOT NULL,
    lease_until REAL,
    attempts INTEGER NOT NULL DEFAULT 0,
    finished_at REAL,
    result_count INTEGER,
    error_code TEXT
);
CREATE TABLE IF NOT EXISTS federation_opinions (
    object_iri TEXT NOT NULL,
    opinion_iri TEXT NOT NULL,
    parent_iri TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('reply', 'quote')),
    source TEXT NOT NULL CHECK (source IN ('delivered', 'fetched')),
    author_iri TEXT NOT NULL,
    author_name TEXT,
    author_handle TEXT,
    url TEXT,
    content_html TEXT NOT NULL,
    attachments_json TEXT NOT NULL DEFAULT '[]',
    language TEXT,
    summary TEXT,
    sensitive INTEGER NOT NULL DEFAULT 0,
    published REAL,
    updated REAL,
    fetched_at REAL NOT NULL,
    hidden_at REAL,
    hidden_by INTEGER,
    PRIMARY KEY (object_iri, opinion_iri)
);
CREATE INDEX IF NOT EXISTS idx_federation_opinions_parent ON federation_opinions(object_iri, parent_iri)
"""

_FETCH_COLUMNS = "id, object_iri, own, status, requested_at, lease_until, attempts, finished_at, result_count, error_code"
_COLUMNS = "object_iri, opinion_iri, parent_iri, kind, source, author_iri, author_name, author_handle, url, content_html, language, summary, sensitive, published, updated, fetched_at, hidden_at, attachments_json"


@dataclass(frozen=True, slots=True)
class Fetch:
    id: int
    object_iri: str
    own: bool
    status: str
    requested_at: float
    lease_until: float | None
    attempts: int
    finished_at: float | None
    result_count: int | None
    error_code: str | None


@dataclass(frozen=True, slots=True)
class Opinion:
    object_iri: str
    opinion_iri: str
    parent_iri: str
    kind: str
    source: str
    author_iri: str
    author_name: str | None
    author_handle: str | None
    url: str | None
    content_html: str
    language: str | None
    summary: str | None
    sensitive: bool
    published: float | None
    updated: float | None
    fetched_at: float
    hidden_at: float | None
    attachments: list[dict[str, Any]]


def add_attachments_column(conn: sqlite3.Connection) -> None:
    """Add ``attachments_json`` to a table of version 11 (version 12). Idempotent."""
    columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(federation_opinions)")}
    if columns and "attachments_json" not in columns:
        conn.execute("ALTER TABLE federation_opinions ADD COLUMN attachments_json TEXT NOT NULL DEFAULT '[]'")


def _attachments(value: Any) -> list[dict[str, Any]]:
    """The attachments of a post that the frame shows: ``http``/``https`` addresses with their media type and name, 16 at most."""
    out: list[dict[str, Any]] = []
    for item in value if isinstance(value, list) else []:
        url = str(item.get("url") or "") if isinstance(item, dict) else ""
        if url.startswith(("https://", "http://")) and len(out) < 16:
            out.append({"url": url, "media_type": item.get("media_type"), "name": item.get("name")})
    return out


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Make the tables when they are not there. Idempotent; the migration of ``client.db`` calls it."""
    for statement in SCHEMA.split(";"):
        if statement.strip():
            conn.execute(statement)


def _fetch(row: Any) -> Fetch:
    i, iri, own, status, requested, lease, attempts, finished, count, error = tuple(row)
    return Fetch(int(i), str(iri), bool(own), str(status), float(requested), None if lease is None else float(lease), int(attempts), None if finished is None else float(finished), None if count is None else int(count), error)


def get_fetch(conn: sqlite3.Connection, object_iri: str) -> Fetch | None:
    row = conn.execute(f"SELECT {_FETCH_COLUMNS} FROM federation_opinion_fetches WHERE object_iri=?", (object_iri,)).fetchone()
    return None if row is None else _fetch(row)


def request_fetch(conn: sqlite3.Connection, object_iri: str, *, own: bool, object_published: float, now: float) -> Fetch | None:
    """Ask for a fetch of the opinions of an object when the conditions of Section 5.11 B2 hold. Return the fetch that runs now (a new one or one that waits), or ``None`` when no fetch is due. The caller commits."""
    current = get_fetch(conn, object_iri)
    if current is not None and current.status == "pending":
        return current
    if now - object_published < AGE_SECONDS:
        return None
    if current is not None and now - current.requested_at < FETCH_INTERVAL_SECONDS:
        return None
    conn.execute(
        "INSERT INTO federation_opinion_fetches (object_iri, own, status, requested_at, attempts) VALUES (?, ?, 'pending', ?, 0) "
        "ON CONFLICT(object_iri) DO UPDATE SET own=excluded.own, status='pending', requested_at=excluded.requested_at, lease_until=NULL, attempts=0, finished_at=NULL, result_count=NULL, error_code=NULL",
        (object_iri, int(own), now),
    )
    return get_fetch(conn, object_iri)


def lease(conn: sqlite3.Connection, *, now: float, limit: int) -> list[Fetch]:
    """The fetches that wait, each with a lease. A fetch with ``MAX_ATTEMPTS`` leases and no end becomes ``failed``. The caller commits."""
    out: list[Fetch] = []
    for row in conn.execute(f"SELECT {_FETCH_COLUMNS} FROM federation_opinion_fetches WHERE status='pending' AND (lease_until IS NULL OR lease_until<=?) ORDER BY requested_at", (now,)).fetchall():
        fetch = _fetch(row)
        if fetch.attempts >= MAX_ATTEMPTS:
            conn.execute("UPDATE federation_opinion_fetches SET status='failed', error_code='fetch_timeout', lease_until=NULL, finished_at=? WHERE id=?", (now, fetch.id))
            continue
        if len(out) >= limit:
            break
        conn.execute("UPDATE federation_opinion_fetches SET attempts=attempts+1, lease_until=? WHERE id=?", (now + LEASE_SECONDS, fetch.id))
        found = get_fetch(conn, fetch.object_iri)
        assert found is not None
        out.append(found)
    return out


def finish(conn: sqlite3.Connection, fetch_id: int, *, result_count: int, now: float) -> None:
    conn.execute("UPDATE federation_opinion_fetches SET status='finished', lease_until=NULL, finished_at=?, result_count=? WHERE id=? AND status='pending'", (now, result_count, fetch_id))


def _opinion(row: Any) -> Opinion:
    (obj, iri, parent, kind, source, author, name, handle, url, content, language, summary, sensitive, published, updated, fetched, hidden, attachments) = tuple(row)
    try:
        listed = _attachments(json.loads(attachments or "[]"))
    except ValueError:
        listed = []
    return Opinion(str(obj), str(iri), str(parent), str(kind), str(source), str(author), name, handle, url, str(content), language, summary, bool(sensitive), published, updated, float(fetched), hidden, listed)


def opinions_of(conn: sqlite3.Connection, object_iri: str, *, include_hidden: bool = False) -> list[Opinion]:
    """The opinions of one object by time (the oldest first)."""
    hidden = "" if include_hidden else " AND hidden_at IS NULL"
    rows = conn.execute(f"SELECT {_COLUMNS} FROM federation_opinions WHERE object_iri=?{hidden} ORDER BY COALESCE(published, fetched_at), opinion_iri", (object_iri,)).fetchall()
    return [_opinion(row) for row in rows]


def count_visible_opinions(conn: sqlite3.Connection, object_public_ids: Sequence[int], *, origin: str | None = None) -> dict[int, int]:
    """Return the count of non-hidden opinions for each object public ID."""
    ids = [int(public_id) for public_id in object_public_ids]
    counts = {public_id: 0 for public_id in ids}
    if not ids:
        return counts
    if origin is not None:
        prefix = f"{origin.rstrip('/')}/ap/objects/"
        by_iri = {f"{prefix}{public_id}": public_id for public_id in counts}
        iri_list = list(by_iri)
        for start in range(0, len(iri_list), 500):
            part = iri_list[start : start + 500]
            marks = ",".join("?" for _ in part)
            for object_iri, number in conn.execute(f"SELECT object_iri, COUNT(*) FROM federation_opinions WHERE hidden_at IS NULL AND object_iri IN ({marks}) GROUP BY object_iri", part):
                counts[by_iri[str(object_iri)]] = int(number)
        return counts
    for object_iri, number in conn.execute("SELECT object_iri, COUNT(*) FROM federation_opinions WHERE hidden_at IS NULL AND object_iri LIKE '%/ap/objects/%' GROUP BY object_iri"):
        tail = str(object_iri).rsplit("/ap/objects/", 1)[-1]
        if tail.isascii() and tail.isdigit():
            public_id = int(tail)
            if public_id in counts:
                counts[public_id] += int(number)
    return counts


def store_objects(conn: sqlite3.Connection, object_iri: str, objects: list[dict[str, Any]], *, quoting: set[str], delivered: set[str], now: float) -> int:
    """Store the posts of one fetch that are opinions of the object, and return the number of new rows. A post is an opinion when it replies to the object or to a reply opinion of the object (also one of the same list), or when it quotes the object (``quote``, or an address of ``quoting``: the quoting objects of the approved stamps). A reply to a quote post is not an opinion of the object (Section 5.11 B4). The other posts are not stored. The caller commits."""
    rows = conn.execute("SELECT opinion_iri, kind FROM federation_opinions WHERE object_iri=?", (object_iri,)).fetchall()
    known = {str(row[0]) for row in rows}
    replies = {str(row[0]) for row in rows if row[1] == "reply"}
    count = len(known)
    pending = {str(obj["object_iri"]): obj for obj in objects}
    new = 0
    changed = True
    while changed and pending:
        changed = False
        for iri, obj in list(pending.items()):
            parent = obj.get("in_reply_to")
            if obj.get("quote") == object_iri or iri in quoting:
                kind, parent_iri = "quote", object_iri
            elif parent == object_iri or parent in replies:
                kind, parent_iri = "reply", str(parent)
            else:
                continue
            del pending[iri]
            changed = True
            if iri not in known and count >= MAX_OPINIONS:
                continue
            author = obj.get("author") or {}
            made = conn.execute(
                "INSERT INTO federation_opinions (object_iri, opinion_iri, parent_iri, kind, source, author_iri, author_name, author_handle, url, content_html, attachments_json, language, summary, sensitive, published, updated, fetched_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(object_iri, opinion_iri) DO UPDATE SET parent_iri=excluded.parent_iri, kind=excluded.kind, author_name=excluded.author_name, author_handle=excluded.author_handle, url=excluded.url, content_html=excluded.content_html, attachments_json=excluded.attachments_json, language=excluded.language, summary=excluded.summary, sensitive=excluded.sensitive, updated=excluded.updated, fetched_at=excluded.fetched_at",
                (object_iri, iri, parent_iri, kind, "delivered" if iri in delivered else "fetched", str(author.get("iri") or ""), author.get("name"), author.get("handle"), obj.get("url"), str(obj.get("content_html") or ""), json.dumps(_attachments(obj.get("attachments")), ensure_ascii=False), obj.get("language"), obj.get("summary"), int(bool(obj.get("sensitive"))), obj.get("published_at"), obj.get("updated_at"), now),
            ).rowcount
            if kind == "reply":
                replies.add(iri)
            else:
                replies.discard(iri)
            if iri not in known:
                known.add(iri)
                count += 1
                new += int(bool(made))
    return new


def forget(conn: sqlite3.Connection, object_iri: str, opinion_iris: list[str]) -> int:
    """Delete opinions that are gone at their origin (a 404 or 410, Section 5.11 B7). Their replies stay in the tree under the object."""
    removed = 0
    for iri in opinion_iris:
        removed += conn.execute("DELETE FROM federation_opinions WHERE object_iri=? AND opinion_iri=?", (object_iri, iri)).rowcount
        conn.execute("UPDATE federation_opinions SET parent_iri=? WHERE object_iri=? AND parent_iri=?", (object_iri, object_iri, iri))
    return removed


def hide(conn: sqlite3.Connection, object_iri: str, opinion_iri: str, *, hidden_by: int | None, hidden: bool, now: float | None = None) -> bool:
    """Hide an opinion from the page and from the ``replies`` collection, or show it again (Section 5.11 D5). True when a row changed."""
    when = (time.time() if now is None else now) if hidden else None
    return conn.execute("UPDATE federation_opinions SET hidden_at=?, hidden_by=? WHERE object_iri=? AND opinion_iri=?", (when, hidden_by if hidden else None, object_iri, opinion_iri)).rowcount > 0


def delete_for_object(conn: sqlite3.Connection, object_iri: str) -> int:
    """Delete the opinions and the fetch of an object that is gone (Section 5.11 C4)."""
    conn.execute("DELETE FROM federation_opinion_fetches WHERE object_iri=?", (object_iri,))
    return conn.execute("DELETE FROM federation_opinions WHERE object_iri=?", (object_iri,)).rowcount


def delete_opinion(conn: sqlite3.Connection, opinion_iri: str, author_iri: str) -> int:
    """A remote ``Delete`` of a post (Section 5.11 C4): delete the opinion rows of that post for each object, when the actor of the ``Delete`` is the author. Its replies move under the object. The caller commits."""
    removed = 0
    for (object_iri,) in conn.execute("SELECT object_iri FROM federation_opinions WHERE opinion_iri=? AND author_iri=?", (opinion_iri, author_iri)).fetchall():
        removed += forget(conn, str(object_iri), [opinion_iri])
    return removed


def object_iris(conn: sqlite3.Connection) -> list[str]:
    """The objects that have opinions or a fetch."""
    rows = conn.execute("SELECT object_iri FROM federation_opinions UNION SELECT object_iri FROM federation_opinion_fetches ORDER BY 1").fetchall()
    return [str(row[0]) for row in rows]


def opinion_key(opinion_iri: str) -> str:
    """The key of an opinion in the address of its frame: the first 16 hexadecimal characters of the SHA-256 of its address."""
    import hashlib

    return hashlib.sha256(opinion_iri.encode("utf-8")).hexdigest()[:16]
