"""+1 marks of renditions at their origin (DOCUMENT_RENDITIONS_AND_PUBLICATION_DESIGN.md Section 5.12; claire.db version 55).

A +1 is a mark of an account for a rendition object of this server. It is not a ``Like``. One row for each actor and rendition object; the counts come from the rows. Who gave a +1 is not public: the collection ``claire:plusOnes`` has only the counts.
"""

from __future__ import annotations

import sqlite3

__all__ = ["DOMAIN_HOURLY_LIMIT", "SERVER_LIMIT", "add", "counts", "delete_for_object", "has_mark", "recent_from_host", "recent_rate_events", "record_rate_event", "remove"]

# The marks of the accounts of one server count at most this number in the score (Section 5.12 rule 9, owner decision O15). ``vocabulary.PLUS_ONE_SERVER_LIMIT`` is the same number on the wire.
SERVER_LIMIT = 1000
# The marks that this server takes from one domain in one hour (Section 5.12 rule 12).
DOMAIN_HOURLY_LIMIT = 600

SCHEMA = """
CREATE TABLE IF NOT EXISTS rendition_plus_ones (
    object_public_id INTEGER NOT NULL,
    actor_iri TEXT NOT NULL,
    actor_host TEXT NOT NULL,
    activity_iri TEXT NOT NULL,
    created_at REAL NOT NULL,
    PRIMARY KEY (object_public_id, actor_iri)
);
CREATE INDEX IF NOT EXISTS idx_rendition_plus_ones_host ON rendition_plus_ones(actor_host, created_at);
CREATE TABLE IF NOT EXISTS plus_one_rate_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope TEXT NOT NULL,
    key TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_plus_one_rate_log ON plus_one_rate_log(scope, key, created_at)
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Make the table when it is not there. Idempotent; the migration of ``store.db`` calls it at each start."""
    for statement in SCHEMA.split(";"):
        if statement.strip():
            conn.execute(statement)


def add(conn: sqlite3.Connection, object_public_id: int, *, actor_iri: str, actor_host: str, activity_iri: str, now: float) -> bool:
    """Store the +1 of an actor for a rendition object. True when this call made the row: a second +1 of the same actor changes nothing."""
    return conn.execute(
        "INSERT INTO rendition_plus_ones (object_public_id, actor_iri, actor_host, activity_iri, created_at) VALUES (?, ?, ?, ?, ?) ON CONFLICT(object_public_id, actor_iri) DO NOTHING",
        (object_public_id, actor_iri, actor_host, activity_iri, now),
    ).rowcount > 0


def remove(conn: sqlite3.Connection, object_public_id: int, *, actor_iri: str) -> bool:
    """Take the +1 of an actor back. True when a row was deleted."""
    return conn.execute("DELETE FROM rendition_plus_ones WHERE object_public_id=? AND actor_iri=?", (object_public_id, actor_iri)).rowcount > 0


def has_mark(conn: sqlite3.Connection, object_public_id: int, *, actor_iri: str) -> bool:
    return conn.execute("SELECT 1 FROM rendition_plus_ones WHERE object_public_id=? AND actor_iri=?", (object_public_id, actor_iri)).fetchone() is not None


def counts(conn: sqlite3.Connection, object_public_id: int) -> dict[str, int]:
    """The counts of one rendition object: ``total`` (all marks), ``servers`` (the servers of the actors), and ``score`` (the marks, where one server counts at most ``SERVER_LIMIT``)."""
    by_host = [int(row[0]) for row in conn.execute("SELECT COUNT(*) FROM rendition_plus_ones WHERE object_public_id=? GROUP BY actor_host", (object_public_id,))]
    return {"total": sum(by_host), "servers": len(by_host), "score": sum(min(count, SERVER_LIMIT) for count in by_host)}


def record_rate_event(conn: sqlite3.Connection, scope: str, key: str, *, now: float) -> None:
    """Record one +1 rate-limit event and prune rows older than two hours."""
    conn.execute("INSERT INTO plus_one_rate_log (scope, key, created_at) VALUES (?, ?, ?)", (scope, key, now))
    conn.execute("DELETE FROM plus_one_rate_log WHERE created_at < ?", (now - 7200.0,))


def recent_rate_events(conn: sqlite3.Connection, scope: str, key: str, *, since: float) -> int:
    """Return the count of +1 rate-limit events for one scope and key after ``since``."""
    return int(conn.execute("SELECT COUNT(*) FROM plus_one_rate_log WHERE scope=? AND key=? AND created_at>?", (scope, key, since)).fetchone()[0])


def recent_from_host(conn: sqlite3.Connection, actor_host: str, *, since: float) -> int:
    """The marks of the actors of one host that this server stored after ``since`` (the rate limit of the receiver)."""
    live_count = int(conn.execute("SELECT COUNT(*) FROM rendition_plus_ones WHERE actor_host=? AND created_at>?", (actor_host, since)).fetchone()[0])
    return max(live_count, recent_rate_events(conn, "domain", actor_host, since=since))


def delete_for_object(conn: sqlite3.Connection, object_public_id: int) -> int:
    """Delete the marks of a rendition object that is gone (Section 5.12 rule 14)."""
    return conn.execute("DELETE FROM rendition_plus_ones WHERE object_public_id=?", (object_public_id,)).rowcount
