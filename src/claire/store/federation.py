"""Store functions for the federation tables of ``claire.db`` version 17 (Fedify sidecar design Section 10.1, unit FP2-1).

Tables: ``federation_followers``, ``federation_tombstones``, ``federation_domain_blocks``. The migration is in ``store.db``.

- The follower key is ``(account_id, actor_iri)``. ``public_id`` is a time-ordered number (``claire.ids``) and is the collection cursor.
- A tombstone has no USERNAME and no foreign key to ``accounts``, because the row must stay after the purge.
- A blocked domain is lowercase ASCII (punycode). A block of a domain also applies to its subdomains.

No function in this module commits. The caller owns the transaction.
"""

from __future__ import annotations

import hashlib
import ipaddress
import re
import sqlite3
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlsplit

from .. import ids

__all__ = [
    "DomainBlock",
    "Follower",
    "Tombstone",
    "TombstoneKind",
    "add_domain_block",
    "add_tombstone",
    "blocking_domain",
    "count_followers",
    "domain_blocks_etag",
    "get_follower",
    "get_tombstone",
    "iri_host",
    "list_domain_blocks",
    "list_followers",
    "normalize_domain",
    "remove_domain_block",
    "remove_follower",
    "remove_followers_of_actor",
    "update_follower_inboxes",
    "upsert_follower",
]

TombstoneKind = Literal["actor", "object"]

_FOLLOWERS_TABLE = "federation_followers"
_FOLLOWER_COLUMNS = "public_id, account_id, actor_iri, inbox_iri, shared_inbox_iri, created_at, updated_at"
_MAX_DOMAIN_LENGTH = 253
_DOMAIN_RE = re.compile(r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)*")


@dataclass(frozen=True, slots=True)
class Follower:
    public_id: int
    account_id: int
    actor_iri: str
    inbox_iri: str
    shared_inbox_iri: str | None
    created_at: float
    updated_at: float


@dataclass(frozen=True, slots=True)
class Tombstone:
    public_id: int
    kind: TombstoneKind
    gone_at: float


@dataclass(frozen=True, slots=True)
class DomainBlock:
    domain: str
    created_at: float
    created_by: int
    note: str


# --- Followers ---------------------------------------------------------------


def _follower(row: sqlite3.Row | tuple) -> Follower:
    public_id, account_id, actor_iri, inbox_iri, shared_inbox_iri, created_at, updated_at = tuple(row)
    return Follower(int(public_id), int(account_id), str(actor_iri), str(inbox_iri), None if shared_inbox_iri is None else str(shared_inbox_iri), float(created_at), float(updated_at))


def get_follower(conn: sqlite3.Connection, account_id: int, actor_iri: str) -> Follower | None:
    row = conn.execute(f"SELECT {_FOLLOWER_COLUMNS} FROM {_FOLLOWERS_TABLE} WHERE account_id=? AND actor_iri=?", (account_id, actor_iri)).fetchone()
    return None if row is None else _follower(row)


def upsert_follower(conn: sqlite3.Connection, *, account_id: int, actor_iri: str, inbox_iri: str, shared_inbox_iri: str | None, salt: bytes, now: float) -> tuple[Follower, bool]:
    """Store one follower. Return the row and ``True`` when this call made the row.

    A second call with the same ``(account_id, actor_iri)`` keeps the row and its ``public_id``. It replaces the inbox addresses if they are different.
    """
    existing = get_follower(conn, account_id, actor_iri)
    if existing is None:

        def _insert(public_id: int) -> None:
            conn.execute(
                f"INSERT INTO {_FOLLOWERS_TABLE} (public_id, account_id, actor_iri, inbox_iri, shared_inbox_iri, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (public_id, account_id, actor_iri, inbox_iri, shared_inbox_iri, now, now),
            )

        try:
            ids.insert_with_timestamp_id(conn, table=_FOLLOWERS_TABLE, column="public_id", salt=salt, insert=_insert, now_ms=lambda: int(now * 1000))
        except sqlite3.IntegrityError:
            # A different connection stored the same follower after the read above. A different constraint failure has no row here.
            existing = get_follower(conn, account_id, actor_iri)
            if existing is None:
                raise
        else:
            made = get_follower(conn, account_id, actor_iri)
            assert made is not None
            return made, True
    if (existing.inbox_iri, existing.shared_inbox_iri) != (inbox_iri, shared_inbox_iri):
        conn.execute(f"UPDATE {_FOLLOWERS_TABLE} SET inbox_iri=?, shared_inbox_iri=?, updated_at=? WHERE account_id=? AND actor_iri=?", (inbox_iri, shared_inbox_iri, now, account_id, actor_iri))
        updated = get_follower(conn, account_id, actor_iri)
        assert updated is not None
        return updated, False
    return existing, False


def remove_follower(conn: sqlite3.Connection, account_id: int, actor_iri: str) -> bool:
    """Delete one follower. Return ``True`` when a row was deleted."""
    return conn.execute(f"DELETE FROM {_FOLLOWERS_TABLE} WHERE account_id=? AND actor_iri=?", (account_id, actor_iri)).rowcount > 0


def remove_followers_of_actor(conn: sqlite3.Connection, actor_iri: str) -> int:
    """Delete a remote actor from the followers of all accounts. Return the number of deleted rows."""
    return int(conn.execute(f"DELETE FROM {_FOLLOWERS_TABLE} WHERE actor_iri=?", (actor_iri,)).rowcount)


def update_follower_inboxes(conn: sqlite3.Connection, actor_iri: str, *, inbox_iri: str, shared_inbox_iri: str | None, now: float) -> int:
    """Replace the inbox addresses of a remote actor in all rows that are different. Return the number of changed rows."""
    return int(
        conn.execute(
            f"UPDATE {_FOLLOWERS_TABLE} SET inbox_iri=?, shared_inbox_iri=?, updated_at=? WHERE actor_iri=? AND (inbox_iri IS NOT ? OR shared_inbox_iri IS NOT ?)",
            (inbox_iri, shared_inbox_iri, now, actor_iri, inbox_iri, shared_inbox_iri),
        ).rowcount
    )


def count_followers(conn: sqlite3.Connection, account_id: int) -> int:
    return int(conn.execute(f"SELECT COUNT(*) FROM {_FOLLOWERS_TABLE} WHERE account_id=?", (account_id,)).fetchone()[0])


def list_followers(conn: sqlite3.Connection, account_id: int, *, limit: int, max_id: int | None = None, min_id: int | None = None, since_id: int | None = None) -> list[Follower]:
    """Return one cursor page of followers, newest first.

    The cursors agree with the collection contract (multi-account design Section 7.4): ``max_id`` gives rows below the number, ``since_id`` gives the newest rows above the number, and ``min_id`` gives the rows immediately above the number.
    """
    if limit < 1:
        raise ValueError("limit must be 1 or more")
    sql = f"SELECT {_FOLLOWER_COLUMNS} FROM {_FOLLOWERS_TABLE} WHERE account_id=?"
    params: list[object] = [account_id]
    if max_id is not None:
        sql += " AND public_id < ?"
        params.append(max_id)
    if min_id is not None:
        sql += " AND public_id > ?"
        params.append(min_id)
    if since_id is not None:
        sql += " AND public_id > ?"
        params.append(since_id)
    sql += f" ORDER BY public_id {'ASC' if min_id is not None else 'DESC'} LIMIT ?"
    params.append(limit)
    rows = [_follower(row) for row in conn.execute(sql, params).fetchall()]
    if min_id is not None:
        rows.reverse()
    return rows


# --- Tombstones --------------------------------------------------------------


def add_tombstone(conn: sqlite3.Connection, public_id: int, kind: TombstoneKind, *, gone_at: float) -> bool:
    """Record that an actor or an object is gone. Return ``True`` when this call made the row. An existing row keeps its time."""
    return conn.execute("INSERT INTO federation_tombstones (public_id, kind, gone_at) VALUES (?, ?, ?) ON CONFLICT(public_id) DO NOTHING", (public_id, kind, gone_at)).rowcount > 0


def get_tombstone(conn: sqlite3.Connection, public_id: int) -> Tombstone | None:
    row = conn.execute("SELECT public_id, kind, gone_at FROM federation_tombstones WHERE public_id=?", (public_id,)).fetchone()
    return None if row is None else Tombstone(int(row[0]), row[1], float(row[2]))


# --- Domain blocks -----------------------------------------------------------


def _is_ip_address(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def normalize_domain(raw: str) -> str:
    """Return the stored form of a host name: lowercase ASCII (punycode), no trailing dot. An IPv4 address is permitted.

    Raise ``ValueError`` for a value that is not a host name (a scheme, a port, a path, a space, an empty label).
    """
    if not isinstance(raw, str):
        raise ValueError("domain must be a string")
    value = raw.strip().lower().removesuffix(".")
    if not value:
        raise ValueError("domain is empty")
    try:
        value = value.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ValueError(f"domain is not valid: {raw!r}") from exc
    if len(value) > _MAX_DOMAIN_LENGTH or _DOMAIN_RE.fullmatch(value) is None:
        raise ValueError(f"domain is not valid: {raw!r}")
    return value


def iri_host(iri: str) -> str | None:
    """Return the normalized host of an ``http`` or ``https`` IRI, or ``None`` when there is no valid host. The port is not part of the result."""
    try:
        parts = urlsplit(iri)
        host = parts.hostname
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not host:
        return None
    try:
        return normalize_domain(host)
    except ValueError:
        return None


def _block(row: sqlite3.Row | tuple) -> DomainBlock:
    domain, created_at, created_by, note = tuple(row)
    return DomainBlock(str(domain), float(created_at), int(created_by), str(note))


def add_domain_block(conn: sqlite3.Connection, domain: str, *, created_by: int, note: str = "", now: float) -> tuple[DomainBlock, bool]:
    """Block a domain. Return the row and ``True`` when this call made the row. An existing row is not changed."""
    normalized = normalize_domain(domain)
    made = conn.execute("INSERT INTO federation_domain_blocks (domain, created_at, created_by, note) VALUES (?, ?, ?, ?) ON CONFLICT(domain) DO NOTHING", (normalized, now, created_by, note)).rowcount > 0
    row = conn.execute("SELECT domain, created_at, created_by, note FROM federation_domain_blocks WHERE domain=?", (normalized,)).fetchone()
    return _block(row), made


def remove_domain_block(conn: sqlite3.Connection, domain: str) -> bool:
    """Remove a block. Return ``True`` when a row was deleted."""
    return conn.execute("DELETE FROM federation_domain_blocks WHERE domain=?", (normalize_domain(domain),)).rowcount > 0


def list_domain_blocks(conn: sqlite3.Connection) -> list[DomainBlock]:
    """Return all blocks in the order of the domain."""
    return [_block(row) for row in conn.execute("SELECT domain, created_at, created_by, note FROM federation_domain_blocks ORDER BY domain").fetchall()]


def blocking_domain(conn: sqlite3.Connection, host: str) -> str | None:
    """Return the blocked domain that applies to ``host``, or ``None``. ``host`` must be a normalized host (``iri_host``).

    A block applies to the domain and to its subdomains. An IP address has an exact comparison only.
    """
    candidates = [host]
    if not _is_ip_address(host):
        labels = host.split(".")
        candidates += [".".join(labels[i:]) for i in range(1, len(labels))]
    marks = ",".join("?" for _ in candidates)
    row = conn.execute(f"SELECT domain FROM federation_domain_blocks WHERE domain IN ({marks}) ORDER BY LENGTH(domain) DESC LIMIT 1", candidates).fetchone()
    return None if row is None else str(row[0])


def domain_blocks_etag(domains: list[str]) -> str:
    """Return a strong entity tag for a list of blocked domains. The order of the list has no effect."""
    digest = hashlib.sha256("\n".join(sorted(domains)).encode("ascii")).hexdigest()
    return f'"{digest[:32]}"'
