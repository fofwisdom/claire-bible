"""Known servers of the federation of shared knowledge (KNOWLEDGE_FEDERATION_DESIGN.md Section 6.2, unit KF1).

A known server is a Claire server that this server exchanges shared knowledge with. No agreement between admins is necessary (Section 6.1). A server becomes known when:

1. an account of this server follows, reads, joins, curates from, or contributes to something on it (the sources ``local_*``);
2. an account of that server contributes to or curates from a shared knowledge base of this server (the sources ``remote_*``);
3. the admin adds its domain (the source ``admin``).

This module owns the table ``kf_known_servers`` of ``claire.db``. No function here commits: the caller owns the transaction.

- The state of a server is not a column. It is ``blocked`` when a domain block applies to its domain (``store.federation``), else ``active``. Thus the block list stays the one source of that decision.
- ``source`` keeps how the server became known the first time. A later contact changes ``last_contact_at`` only.
- The data of a read (server actor, software, version, number of shared knowledge bases) comes from the sidecar, because only the sidecar makes requests to other servers.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any

from ..store import federation as federation_store

__all__ = [
    "COLUMNS",
    "LOCAL_SOURCES",
    "READ_INTERVAL_SECONDS",
    "READ_LEASE_SECONDS",
    "REMOTE_SOURCES",
    "SCHEMA",
    "SOURCES",
    "TABLE",
    "KnownServer",
    "ensure_schema",
    "get_server",
    "lease_reads",
    "list_servers",
    "note_server",
    "record_read",
    "record_read_error",
    "remove_server",
    "server_view",
    "touch_contact",
]

# How a server became known (design Section 6.2). The column has no CHECK, so that a later unit can add a source without a migration; ``note_server`` refuses a value that is not in this list.
LOCAL_SOURCES: tuple[str, ...] = ("local_follow", "local_read", "local_join", "local_curation", "local_contribution")
REMOTE_SOURCES: tuple[str, ...] = ("remote_contribution", "remote_curation")
SOURCES: tuple[str, ...] = ("admin", *LOCAL_SOURCES, *REMOTE_SOURCES)

# A known server is read again one time each day (design Section 8.1 rule 2).
READ_INTERVAL_SECONDS = 86400.0
# A read that the sidecar took and did not report is ready again after this time.
READ_LEASE_SECONDS = 300.0
_MAX_ERROR_LENGTH = 200

SCHEMA = """
CREATE TABLE IF NOT EXISTS kf_known_servers (
    domain TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    added_by INTEGER,
    created_at REAL NOT NULL,
    last_contact_at REAL,
    server_actor_iri TEXT,
    software_name TEXT,
    software_version TEXT,
    shared_kb_count INTEGER,
    last_read_at REAL,
    read_lease_until REAL,
    error_count INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    last_error_at REAL
)
"""

_COLUMNS = "domain, source, added_by, created_at, last_contact_at, server_actor_iri, software_name, software_version, shared_kb_count, last_read_at, read_lease_until, error_count, last_error, last_error_at"
# The columns that the code of this module reads. The migration (``store.db``) gives a table of a different form a new name and makes this one again.
TABLE = "kf_known_servers"
COLUMNS: tuple[str, ...] = tuple(_COLUMNS.split(", "))


@dataclass(frozen=True, slots=True)
class KnownServer:
    domain: str
    source: str
    added_by: int | None
    created_at: float
    last_contact_at: float | None
    server_actor_iri: str | None
    software_name: str | None
    software_version: str | None
    shared_kb_count: int | None
    last_read_at: float | None
    read_lease_until: float | None
    error_count: int
    last_error: str | None
    last_error_at: float | None


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Make the table when it is not there. Idempotent; the migration of ``store.db`` calls it at each start."""
    conn.execute(SCHEMA)


def _server(row: sqlite3.Row | tuple) -> KnownServer:
    return KnownServer(*tuple(row))


def get_server(conn: sqlite3.Connection, domain: str) -> KnownServer | None:
    """Return the known server with this normalized domain, or ``None``."""
    row = conn.execute(f"SELECT {_COLUMNS} FROM kf_known_servers WHERE domain=?", (domain,)).fetchone()
    return None if row is None else _server(row)


def list_servers(conn: sqlite3.Connection) -> list[KnownServer]:
    """Return all known servers in the order of the domain."""
    return [_server(row) for row in conn.execute(f"SELECT {_COLUMNS} FROM kf_known_servers ORDER BY domain").fetchall()]


def note_server(conn: sqlite3.Connection, domain: str, *, source: str, now: float, added_by: int | None = None) -> tuple[KnownServer, bool]:
    """Record that this server knows ``domain``. Return the row and ``True`` when this call made the row.

    - ``domain`` is a host name; ``federation_store.normalize_domain`` gives its stored form and raises ``ValueError`` for a value that is not a host name.
    - A row that exists keeps its ``source`` and ``added_by``: they tell how the server became known.
    - A source that is not ``admin`` is a contact: it sets ``last_contact_at``.
    - A blocked domain can be a known server. Its state is ``blocked`` and there is no exchange with it.
    """
    if source not in SOURCES:
        raise ValueError(f"unknown source of a known server: {source!r}")
    if (source == "admin") != (added_by is not None):
        raise ValueError("added_by is the admin account of the source 'admin' and of no other source")
    normalized = federation_store.normalize_domain(domain)
    contact = None if source == "admin" else now
    made = conn.execute("INSERT INTO kf_known_servers (domain, source, added_by, created_at, last_contact_at) VALUES (?, ?, ?, ?, ?) ON CONFLICT(domain) DO NOTHING", (normalized, source, added_by, now, contact)).rowcount > 0
    if not made and contact is not None:
        conn.execute("UPDATE kf_known_servers SET last_contact_at=? WHERE domain=?", (contact, normalized))
    server = get_server(conn, normalized)
    assert server is not None
    return server, made


def touch_contact(conn: sqlite3.Connection, domain: str, *, now: float) -> bool:
    """Set the time of the last contact of a known server. Return ``False`` when the domain is not a known server: a contact alone does not make a server known."""
    return conn.execute("UPDATE kf_known_servers SET last_contact_at=? WHERE domain=?", (now, domain)).rowcount > 0


def lease_reads(conn: sqlite3.Connection, *, now: float, limit: int, lease_seconds: float = READ_LEASE_SECONDS) -> list[KnownServer]:
    """Return the known servers that are due for a read, oldest read first, and give a lease on each one.

    A server is due when it has no read and no error, or when its last read or error is ``READ_INTERVAL_SECONDS`` old. A server with a lease that did not end is not due. A blocked server is never read.
    """
    rows = conn.execute(
        f"SELECT {_COLUMNS} FROM kf_known_servers WHERE (read_lease_until IS NULL OR read_lease_until <= ?) AND MAX(COALESCE(last_read_at, 0), COALESCE(last_error_at, 0)) <= ? ORDER BY COALESCE(last_read_at, 0), domain",
        (now, now - READ_INTERVAL_SECONDS),
    ).fetchall()
    due: list[KnownServer] = []
    for row in rows:
        server = _server(row)
        if federation_store.blocking_domain(conn, server.domain) is not None:
            continue
        conn.execute("UPDATE kf_known_servers SET read_lease_until=? WHERE domain=?", (now + lease_seconds, server.domain))
        due.append(server)
        if len(due) >= limit:
            break
    return due


def record_read(conn: sqlite3.Connection, domain: str, *, server_actor_iri: str, software_name: str, software_version: str, shared_kb_count: int | None, now: float) -> bool:
    """Store the result of a read of NodeInfo and of the server actor. Return ``False`` when the domain is not a known server.

    ``shared_kb_count`` is ``None`` when the read did not give the number. A good read removes the error state.
    """
    return conn.execute(
        "UPDATE kf_known_servers SET server_actor_iri=?, software_name=?, software_version=?, shared_kb_count=?, last_read_at=?, read_lease_until=NULL, error_count=0, last_error=NULL, last_error_at=NULL WHERE domain=?",
        (server_actor_iri, software_name, software_version, shared_kb_count, now, domain),
    ).rowcount > 0


def record_read_error(conn: sqlite3.Connection, domain: str, *, error: str, now: float) -> bool:
    """Store a failed read: the error code, its time, and the number of failed reads in sequence. Return ``False`` when the domain is not a known server. The data of the last good read stays."""
    return conn.execute(
        "UPDATE kf_known_servers SET read_lease_until=NULL, error_count=error_count+1, last_error=?, last_error_at=? WHERE domain=?",
        (error[:_MAX_ERROR_LENGTH], now, domain),
    ).rowcount > 0


def remove_server(conn: sqlite3.Connection, domain: str) -> bool:
    """Delete one known server. Return ``False`` when the domain is not a known server. A later contact or an admin entry makes it known again (design Section 6.2)."""
    return conn.execute("DELETE FROM kf_known_servers WHERE domain=?", (domain,)).rowcount > 0


def server_view(conn: sqlite3.Connection, server: KnownServer) -> dict[str, Any]:
    """Return the API form of one known server (design Section 6.5: domain, software, version, how it became known, number of shared knowledge bases, last read, errors)."""
    blocked_by = federation_store.blocking_domain(conn, server.domain)
    return {
        "domain": server.domain,
        "status": "active" if blocked_by is None else "blocked",
        "blocked_by": blocked_by,
        "source": server.source,
        "created_at": server.created_at,
        "last_contact_at": server.last_contact_at,
        "server_actor": server.server_actor_iri,
        "software_name": server.software_name,
        "software_version": server.software_version,
        "kb_count": server.shared_kb_count,
        "last_read_at": server.last_read_at,
        "error_count": server.error_count,
        "last_error": server.last_error,
        "last_error_at": server.last_error_at,
    }
