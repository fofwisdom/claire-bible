"""Grants of the role ``collaborator`` across servers (KNOWLEDGE_FEDERATION_DESIGN.md Section 12.2, KD6, KS8; unit KF4).

A moderator of a shared knowledge base of this server gives an account of another server the role ``collaborator`` for contributions. The knowledge base actor sends ``Grant``; the IRI of that activity is the capability that each later ``Offer`` of the account has. ``Revoke`` ends the grant at once.

This module owns two tables. No function here commits.

| Table | Rows |
| :--- | :--- |
| ``kf_grants`` | The grants that this server gave: one row for each ``Grant`` activity of a knowledge base actor of this server. This server is the authority for them: each use of a capability reads this table (``valid``). |
| ``kf_received_grants`` | The grants that accounts of this server got from knowledge base actors of other servers. The table tells only which capability an ``Offer`` carries; the other server decides if it is in effect. |

Rules:

- The term is ``GRANT_SECONDS`` (6 months). It is a rule of the server that gives the grant, not of the vocabulary.
- An actor has at most one grant in effect for one knowledge base. A new grant for the same actor (a renewal) revokes the old one.
- A grant gives only the capabilities ``contribute_direct`` and ``contribute_theme`` across servers. It gives no membership row and no other capability (design Section 12.2 rules 4 and 5).
- Another server fills ``kf_received_grants``, so the table has limits: at most ``RECEIVED_PER_DOMAIN_MAX`` rows for one account and one registrable domain (refusal ``grant_limit``), and a grant that ended or was revoked is deleted ``RECEIVED_RETENTION_SECONDS`` after its end, or at once when a newer grant of the same knowledge base actor comes for the same account.
"""

from __future__ import annotations

import secrets
import sqlite3
from typing import Any

from . import access

__all__ = [
    "GRANT_SECONDS",
    "RECEIVED_PER_DOMAIN_MAX",
    "RECEIVED_RETENTION_SECONDS",
    "TABLES",
    "actor_deleted",
    "capability_for",
    "ensure_schema",
    "get_issued",
    "give",
    "grant_iri",
    "issued_state",
    "list_issued",
    "purge_account",
    "receive",
    "receive_revoke",
    "received_of_account",
    "revoke",
    "valid",
]

# The term of a grant: 6 months as 183 days (design Section 12.2 rule 1, KS8).
GRANT_SECONDS = 183 * 86400.0
# The limits of the grants that other servers give to the accounts of this server: the rows of one account from one domain, and the time that a grant stays after it ended or was revoked.
RECEIVED_PER_DOMAIN_MAX = 50
RECEIVED_RETENTION_SECONDS = 30 * 86400.0

SCHEMA = """
CREATE TABLE IF NOT EXISTS kf_grants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    grant_iri TEXT NOT NULL UNIQUE,
    kb_number INTEGER NOT NULL,
    actor_iri TEXT NOT NULL,
    domain TEXT NOT NULL,
    granted_by_account_id INTEGER,
    published_at REAL NOT NULL,
    end_time REAL NOT NULL,
    revoked_at REAL,
    revoked_by_account_id INTEGER
);
CREATE INDEX IF NOT EXISTS idx_kf_grants_kb ON kf_grants(kb_number, actor_iri);
CREATE TABLE IF NOT EXISTS kf_received_grants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    grant_iri TEXT NOT NULL UNIQUE,
    kb_iri TEXT NOT NULL,
    domain TEXT NOT NULL,
    account_id INTEGER NOT NULL,
    published_at REAL NOT NULL,
    end_time REAL NOT NULL,
    revoked_at REAL,
    received_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_kf_received_grants_account ON kf_received_grants(account_id, kb_iri)
"""

_ISSUED = "id, grant_iri, kb_number, actor_iri, domain, granted_by_account_id, published_at, end_time, revoked_at, revoked_by_account_id"
_RECEIVED = "id, grant_iri, kb_iri, domain, account_id, published_at, end_time, revoked_at, received_at"
# The columns that the code of this module reads, for each table. The migration (``store.db``) gives a table of a different form a new name and makes this one again.
TABLES: dict[str, tuple[str, ...]] = {"kf_grants": tuple(_ISSUED.split(", ")), "kf_received_grants": tuple(_RECEIVED.split(", "))}


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Make the tables and the indexes when they are not there. Idempotent; the migration of ``store.db`` calls it at each start."""
    for statement in SCHEMA.split(";"):
        if statement.strip():
            conn.execute(statement)


def _issued(row: sqlite3.Row | tuple) -> dict[str, Any]:
    return dict(zip(TABLES["kf_grants"], tuple(row)))


def _received(row: sqlite3.Row | tuple) -> dict[str, Any]:
    return dict(zip(TABLES["kf_received_grants"], tuple(row)))


def grant_iri(kb_iri: str) -> str:
    """A new IRI for a ``Grant`` of the knowledge base actor. It has a random part: a renewal is a new activity and a new capability."""
    return f"{kb_iri}#grants/{secrets.token_hex(12)}"


def issued_state(row: dict[str, Any], now: float) -> str:
    """``revoked``, ``ended`` (the end time is past), or ``active``."""
    if row["revoked_at"] is not None:
        return "revoked"
    return "ended" if float(row["end_time"]) <= now else "active"


# --- grants that this server gave ----------------------------------------------------------------------


def get_issued(conn: sqlite3.Connection, grant_id: int) -> dict[str, Any] | None:
    row = conn.execute(f"SELECT {_ISSUED} FROM kf_grants WHERE id=?", (int(grant_id),)).fetchone()
    return None if row is None else _issued(row)


def list_issued(conn: sqlite3.Connection, kb_number: int) -> list[dict[str, Any]]:
    """The grants of one knowledge base of this server, the newest first, in each state."""
    return [_issued(row) for row in conn.execute(f"SELECT {_ISSUED} FROM kf_grants WHERE kb_number=? ORDER BY id DESC", (int(kb_number),)).fetchall()]


def give(conn: sqlite3.Connection, *, kb_number: int, iri: str, actor_iri: str, domain: str, account_id: int | None, now: float) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Record one new grant with the term ``GRANT_SECONDS``. Return the grant and the grants of the same actor and knowledge base that were in effect: each of them is revoked now (a renewal), and the caller sends a ``Revoke`` for each one."""
    replaced = [row for row in list_issued(conn, kb_number) if row["actor_iri"] == actor_iri and issued_state(row, now) == "active"]
    for row in replaced:
        conn.execute("UPDATE kf_grants SET revoked_at=?, revoked_by_account_id=? WHERE id=?", (now, account_id, row["id"]))
    cursor = conn.execute(
        "INSERT INTO kf_grants (grant_iri, kb_number, actor_iri, domain, granted_by_account_id, published_at, end_time) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (iri, int(kb_number), actor_iri, domain, account_id, now, now + GRANT_SECONDS),
    )
    grant = get_issued(conn, int(cursor.lastrowid))
    assert grant is not None
    return grant, replaced


def revoke(conn: sqlite3.Connection, grant_id: int, *, account_id: int | None, now: float) -> bool:
    """End one grant at once (design Section 12.2 rule 3). False when the grant was revoked before."""
    return conn.execute("UPDATE kf_grants SET revoked_at=?, revoked_by_account_id=? WHERE id=? AND revoked_at IS NULL", (now, account_id, int(grant_id))).rowcount > 0


def valid(conn: sqlite3.Connection, capability: str, *, kb_number: int, actor_iri: str, now: float) -> bool:
    """The check of each use of a capability (design Section 12.2 rule 2, KS8): the grant is one of this server, for this knowledge base and this actor, in its term, and not revoked."""
    row = conn.execute("SELECT kb_number, actor_iri, published_at, end_time, revoked_at FROM kf_grants WHERE grant_iri=?", (capability,)).fetchone()
    if row is None:
        return False
    return int(row[0]) == int(kb_number) and str(row[1]) == actor_iri and row[4] is None and float(row[2]) <= now < float(row[3])


def actor_deleted(conn: sqlite3.Connection, actor_iri: str, *, now: float) -> int:
    """The account of another server is gone (``Delete`` of its actor): each grant of that actor ends. No ``Revoke`` goes out: the actor has no inbox. Return the number of grants that ended."""
    return conn.execute("UPDATE kf_grants SET revoked_at=? WHERE actor_iri=? AND revoked_at IS NULL", (now, actor_iri)).rowcount


# --- grants that accounts of this server got ------------------------------------------------------------


def receive(conn: sqlite3.Connection, *, iri: str, kb_iri: str, domain: str, account_id: int, published_at: float, end_time: float, now: float) -> str:
    """Store one ``Grant`` of a knowledge base actor of another server for an account of this server. Return ``stored``, ``known`` (the same activity again changes nothing), or ``limit`` (the account has ``RECEIVED_PER_DOMAIN_MAX`` grants from the domain; nothing is stored).

    Before the insert: the grants of each account that ended or were revoked more than ``RECEIVED_RETENTION_SECONDS`` ago are deleted, and so are the grants of the same actor for the same account that are not in effect now.
    """
    if conn.execute("SELECT 1 FROM kf_received_grants WHERE grant_iri=?", (iri,)).fetchone() is not None:
        return "known"
    limit = now - RECEIVED_RETENTION_SECONDS
    conn.execute("DELETE FROM kf_received_grants WHERE (revoked_at IS NOT NULL AND revoked_at<?) OR end_time<?", (limit, limit))
    conn.execute("DELETE FROM kf_received_grants WHERE account_id=? AND kb_iri=? AND (revoked_at IS NOT NULL OR end_time<=?)", (int(account_id), kb_iri, now))
    # The subdomains of one registrable domain share the limit (``access.quota_domain``).
    quota = access.quota_domain(domain)
    rows = conn.execute("SELECT domain, COUNT(*) FROM kf_received_grants WHERE account_id=? GROUP BY domain", (int(account_id),)).fetchall()
    if sum(int(row[1]) for row in rows if access.quota_domain(str(row[0])) == quota) >= RECEIVED_PER_DOMAIN_MAX:
        return "limit"
    conn.execute(
        "INSERT INTO kf_received_grants (grant_iri, kb_iri, domain, account_id, published_at, end_time, received_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (iri, kb_iri, domain, int(account_id), float(published_at), float(end_time), now),
    )
    return "stored"


def receive_revoke(conn: sqlite3.Connection, *, iri: str, kb_iri: str, now: float) -> bool:
    """The knowledge base actor ``kb_iri`` revoked its grant. False when this server has no such grant of that actor."""
    if conn.execute("SELECT 1 FROM kf_received_grants WHERE grant_iri=? AND kb_iri=?", (iri, kb_iri)).fetchone() is None:
        return False
    conn.execute("UPDATE kf_received_grants SET revoked_at=COALESCE(revoked_at, ?) WHERE grant_iri=? AND kb_iri=?", (now, iri, kb_iri))
    return True


def received_of_account(conn: sqlite3.Connection, account_id: int) -> list[dict[str, Any]]:
    """The grants that one account got, the newest first, in each state."""
    return [_received(row) for row in conn.execute(f"SELECT {_RECEIVED} FROM kf_received_grants WHERE account_id=? ORDER BY id DESC", (int(account_id),)).fetchall()]


def capability_for(conn: sqlite3.Connection, *, account_id: int, kb_iri: str, now: float) -> str | None:
    """The IRI of the grant that an ``Offer`` of the account to the knowledge base carries: the newest grant that is in its term and that the giver did not revoke, or None."""
    row = conn.execute(
        "SELECT grant_iri FROM kf_received_grants WHERE account_id=? AND kb_iri=? AND revoked_at IS NULL AND published_at<=? AND end_time>? ORDER BY id DESC LIMIT 1",
        (int(account_id), kb_iri, now, now),
    ).fetchone()
    return None if row is None else str(row[0])


def purge_account(conn: sqlite3.Connection, account_id: int) -> None:
    """The purge of an account of this server: the grants that it got are deleted, and the account goes from the grants that it gave or revoked as a moderator."""
    conn.execute("DELETE FROM kf_received_grants WHERE account_id=?", (int(account_id),))
    conn.execute("UPDATE kf_grants SET granted_by_account_id=NULL WHERE granted_by_account_id=?", (int(account_id),))
    conn.execute("UPDATE kf_grants SET revoked_by_account_id=NULL WHERE revoked_by_account_id=?", (int(account_id),))
