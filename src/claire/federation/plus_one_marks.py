"""+1 marks of the accounts of this server (DOCUMENT_RENDITIONS_AND_PUBLICATION_DESIGN.md Section 5.12 rules 2 to 4 and 10 to 12, phase E9b; claire.db version 56).

An account that is a federation actor gives a +1 to a published rendition, and takes it back. Two cases:

- **A rendition of this server.** The mark is a row of ``rendition_plus_ones`` (``store.plus_ones``) with the actor of the account. No activity goes to another server.
- **A rendition of another Claire server** (a copy of the cache, ``remote_renditions``). The mark is a row of ``account_plus_ones``, and the activity ``claire:PlusOne`` (or its ``Undo``) goes to the account of the rendition through the outbox of the knowledge federation, signed by the account actor. Only a rendition that this server read as a rendition gets a +1, so no other software gets one (rule 6).

The author of a rendition cannot give it a +1 (rule 3). An account gives at most ``ACCOUNT_HOURLY_LIMIT`` marks in one hour (rule 12). A mark makes no notification (rule 13).

The marks of the accounts are in ``claire.db``, as the cache of remote renditions and the outbox: the mark and its outbox task change in one transaction.
"""

from __future__ import annotations

import hashlib
import sqlite3
from typing import Any
from urllib.parse import urlsplit

from ..core import ids
from ..store import federation as federation_store
from ..store import plus_ones
from .knowledge import offers, outbox, vocabulary

__all__ = ["ACCOUNT_HOURLY_LIMIT", "MarkRefused", "ensure_schema", "give_local", "give_remote", "has_remote_mark", "purge_account", "remote_marks_since", "take_back_local", "take_back_remote"]

ACCOUNT_HOURLY_LIMIT = 60

SCHEMA = """
CREATE TABLE IF NOT EXISTS account_plus_ones (
    account_id INTEGER NOT NULL,
    rendition_iri TEXT NOT NULL,
    author_iri TEXT NOT NULL,
    activity_iri TEXT NOT NULL,
    created_at REAL NOT NULL,
    PRIMARY KEY (account_id, rendition_iri)
);
CREATE INDEX IF NOT EXISTS idx_account_plus_ones_time ON account_plus_ones(account_id, created_at)
"""


class MarkRefused(Exception):
    """The mark is not permitted. ``code``: ``own_rendition``, ``rate_limited``, or ``blocked_domain``."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Make the table when it is not there. Idempotent; the migration of ``store.db`` calls it at each start."""
    for statement in SCHEMA.split(";"):
        if statement.strip():
            conn.execute(statement)


def _actor(origin: str, account_public_id: int) -> str:
    return f"{origin}/ap/actors/{ids.format_public_id(int(account_public_id))}"


def _check_rate(conn: sqlite3.Connection, account_id: int, actor: str, *, now: float) -> None:
    since = now - 3600.0
    local = int(conn.execute("SELECT COUNT(*) FROM rendition_plus_ones WHERE actor_iri=? AND created_at>?", (actor, since)).fetchone()[0])
    remote = int(conn.execute("SELECT COUNT(*) FROM account_plus_ones WHERE account_id=? AND created_at>?", (account_id, since)).fetchone()[0])
    if local + remote >= ACCOUNT_HOURLY_LIMIT:
        raise MarkRefused("rate_limited")


def give_local(conn: sqlite3.Connection, *, account_id: int, account_public_id: int, object_public_id: int, author_account_id: int, origin: str, now: float) -> bool:
    """The +1 of an account for a rendition object of this server. True when this call made the mark. The caller commits."""
    if account_id == author_account_id:
        raise MarkRefused("own_rendition")
    actor = _actor(origin, account_public_id)
    if plus_ones.has_mark(conn, object_public_id, actor_iri=actor):
        return False
    _check_rate(conn, account_id, actor, now=now)
    host = federation_store.iri_host(origin) or ""
    return plus_ones.add(conn, object_public_id, actor_iri=actor, actor_host=host, activity_iri=f"{actor}#plus-ones/{ids.format_public_id(object_public_id)}", now=now)


def take_back_local(conn: sqlite3.Connection, *, account_public_id: int, object_public_id: int, origin: str) -> bool:
    return plus_ones.remove(conn, object_public_id, actor_iri=_actor(origin, account_public_id))


def has_remote_mark(conn: sqlite3.Connection, account_id: int, rendition_iri: str) -> float | None:
    """The time of the mark of the account for a rendition of another server, or ``None``."""
    row = conn.execute("SELECT created_at FROM account_plus_ones WHERE account_id=? AND rendition_iri=?", (account_id, rendition_iri)).fetchone()
    return None if row is None else float(row[0])


def remote_marks_since(conn: sqlite3.Connection, account_id: int, since: float) -> int:
    return int(conn.execute("SELECT COUNT(*) FROM account_plus_ones WHERE account_id=? AND created_at>?", (account_id, since)).fetchone()[0])


def _mark_activity(*, actor: str, rendition: Any, activity_iri: str, published: float) -> dict[str, Any]:
    return vocabulary.build_plus_one(activity_id=activity_iri, account_actor=actor, rendition=rendition.iri, author=rendition.account_iri, published=published)


def give_remote(conn: sqlite3.Connection, *, account_id: int, account_public_id: int, rendition: Any, origin: str, now: float) -> bool:
    """The +1 of an account for a cached rendition of another Claire server (``remote_renditions.RemoteRendition``): the row and the outbox task of the activity. True when this call made the mark. The caller commits."""
    if has_remote_mark(conn, account_id, rendition.iri) is not None:
        return False
    if federation_store.blocking_domain(conn, rendition.domain) is not None:
        raise MarkRefused("blocked_domain")
    actor = _actor(origin, account_public_id)
    _check_rate(conn, account_id, actor, now=now)
    # A new mark after a take back is a new activity: its address has the time.
    activity_iri = f"{actor}#plus-ones/{hashlib.sha256(rendition.iri.encode('utf-8')).hexdigest()[:16]}/{int(now * 1000)}"
    activity = _mark_activity(actor=actor, rendition=rendition, activity_iri=activity_iri, published=now)
    conn.execute("INSERT INTO account_plus_ones (account_id, rendition_iri, author_iri, activity_iri, created_at) VALUES (?, ?, ?, ?, ?)", (account_id, rendition.iri, rendition.account_iri, activity_iri, now))
    outbox.enqueue(conn, activity, signer=offers.account_signer(account_public_id), recipients=[rendition.account_iri], origin=origin, now=now)
    return True


def take_back_remote(conn: sqlite3.Connection, *, account_id: int, account_public_id: int, rendition_iri: str, origin: str, now: float) -> bool:
    """Take the +1 of an account for a rendition of another server back: the row goes away and the ``Undo`` goes to the account of the rendition. True when there was a mark. The caller commits."""
    row = conn.execute("SELECT author_iri, activity_iri, created_at FROM account_plus_ones WHERE account_id=? AND rendition_iri=?", (account_id, rendition_iri)).fetchone()
    if row is None:
        return False
    author, activity_iri, created_at = str(row[0]), str(row[1]), float(row[2])
    actor = _actor(origin, account_public_id)
    mark = vocabulary.build_plus_one(activity_id=activity_iri, account_actor=actor, rendition=rendition_iri, author=author, published=created_at)
    conn.execute("DELETE FROM account_plus_ones WHERE account_id=? AND rendition_iri=?", (account_id, rendition_iri))
    if federation_store.blocking_domain(conn, federation_store.iri_host(author) or "") is None:
        outbox.enqueue(conn, vocabulary.build_undo_plus_one(activity_id=f"{activity_iri}/undo", plus_one=mark), signer=offers.account_signer(account_public_id), recipients=[author], origin=origin, now=now)
    return True


def purge_account(conn: sqlite3.Connection, *, account_id: int, account_public_id: int, send_undo: bool, now: float) -> dict[str, int]:
    """The account purge (Section 5.12 rule 14): each mark of the account goes. For each mark of a rendition of another server, the ``Undo`` goes to the account of the rendition when ``send_undo`` (the purge informs other servers) and the domain is not blocked. The caller commits.

    The server actor signs each ``Undo``: the keys of a purged account are not available after the purge, and the journal of the receiver accepts a signer on the server of the actor of the activity. The actor of the activity stays the account actor; its address comes from the address of the mark, so the purge needs no origin from outside.
    """
    undone = 0
    for rendition_iri, author, activity_iri, created_at in conn.execute("SELECT rendition_iri, author_iri, activity_iri, created_at FROM account_plus_ones WHERE account_id=?", (account_id,)).fetchall():
        actor = str(activity_iri).split("#", 1)[0]
        parts = urlsplit(actor)
        if not send_undo or not parts.scheme or federation_store.blocking_domain(conn, federation_store.iri_host(str(author)) or "") is not None:
            continue
        mark = vocabulary.build_plus_one(activity_id=str(activity_iri), account_actor=actor, rendition=str(rendition_iri), author=str(author), published=float(created_at))
        outbox.enqueue(conn, vocabulary.build_undo_plus_one(activity_id=f"{activity_iri}/undo", plus_one=mark), signer=outbox.SERVER_SIGNER, recipients=[str(author)], origin=f"{parts.scheme}://{parts.netloc}", now=now)
        undone += 1
    remote = conn.execute("DELETE FROM account_plus_ones WHERE account_id=?", (account_id,)).rowcount
    # The marks of the account for renditions of this server need no activity.
    local = conn.execute("DELETE FROM rendition_plus_ones WHERE actor_iri LIKE ?", (f"%/ap/actors/{ids.format_public_id(int(account_public_id))}",)).rowcount
    return {"remote": remote, "undone": undone, "local": local}
