"""Renditions of other Claire servers: the reads and the cache (DOCUMENT_RENDITIONS_AND_PUBLICATION_DESIGN.md Section 5.4 rules 3 to 6, phase E5b; claire.db version 54).

A reader of a document with a source key sees the published renditions of other Claire servers for the same source. This server gets them with reads that its server actor signs. The reads use the queue of the knowledge federation (``kf_remote_reads``, ``knowledge.remote_documents``): Claire adds a read, the sidecar does the signed GET, and Claire gets the JSON as it came. The kinds of this module:

| Kind | Address | The answer |
| :--- | :--- | :--- |
| ``rendition_index`` | ``{server}/ap/sources/{source key}`` | The object addresses of that server for the key. Each one gets a ``rendition_object`` read. |
| ``rendition_object`` | The object address | The fields of the rendition. A new ``claire:bodyHash`` gets a ``rendition_body`` read. |
| ``rendition_body`` | ``claire:body`` of the object | The body. Its hash must be the hash of the object. |
| ``rendition_actor`` | ``attributedTo`` of the object | The handle and the name of the account. |

Rules:

- A server gets at most one index read for each key in ``LIFETIME_SECONDS``; a refusal or an error stays for the same time (Section 5.4 rule 6).
- The servers that get a read: the known servers of the knowledge federation, and the servers of the renditions that the cache has. A blocked domain gets no read, and its rows are not shown.
- A rendition is shown only with its body, while it is not older than its lifetime. A 404 or 410 of its address, an index answer without it, or a ``Delete`` of its origin (``delete_rendition``) removes the copy at once.
- A document of another server is not trusted: each address must be on the server that was asked, the source key must be the key that was asked, the hash of the body must be the hash that the object names, and the HTML goes through ``nh3`` again.
- The body has no ``ETag``: the reader compares ``claire:bodyHash`` of the object and gets the body only when it changed.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from dataclasses import dataclass
from typing import Any

import nh3

from ..store import federation as federation_store
from .knowledge import access, known_servers, vocabulary

__all__ = [
    "INDEX_ITEMS_MAX",
    "KINDS",
    "LIFETIME_SECONDS",
    "RemoteRendition",
    "ensure_schema",
    "forget",
    "purge",
    "record_result",
    "renditions_of",
    "request_set",
]

KINDS = ("rendition_index", "rendition_object", "rendition_body", "rendition_actor")
# The lifetime of a copy and of an index answer (Section 5.4 rule 4).
LIFETIME_SECONDS = 86400.0
# The objects of one index answer that this server reads, and the servers that one request asks.
INDEX_ITEMS_MAX = 60
SERVERS_MAX = 20
# A copy that is not refreshed goes away after this time.
_PURGE_SECONDS = 7 * 86400.0
_TITLE_MAX = 500
_NAME_MAX = 200
_HEX64 = re.compile(r"[0-9a-f]{64}")
_USERNAME = re.compile(r"[A-Za-z0-9_.-]{1,64}")

SCHEMA = """
CREATE TABLE IF NOT EXISTS remote_renditions (
    iri TEXT PRIMARY KEY,
    domain TEXT NOT NULL,
    source_key TEXT NOT NULL,
    template TEXT NOT NULL,
    template_version TEXT,
    rendition_of TEXT,
    account_iri TEXT NOT NULL,
    account_handle TEXT,
    account_name TEXT,
    name TEXT NOT NULL,
    url TEXT,
    updated TEXT,
    body_iri TEXT NOT NULL,
    body_hash TEXT NOT NULL,
    cached_body_hash TEXT,
    body_html TEXT,
    body_source TEXT,
    fetched_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    plus_total INTEGER NOT NULL DEFAULT 0,
    plus_score INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_remote_renditions_key ON remote_renditions(source_key, domain);
CREATE TABLE IF NOT EXISTS remote_rendition_indexes (
    domain TEXT NOT NULL,
    source_key TEXT NOT NULL,
    asked_at REAL NOT NULL,
    status TEXT NOT NULL,
    error_code TEXT,
    PRIMARY KEY (domain, source_key)
)
"""

_COLUMNS = "iri, domain, source_key, template, template_version, rendition_of, account_iri, account_handle, account_name, name, url, updated, body_hash, body_html, body_source, fetched_at, expires_at, plus_total, plus_score"


@dataclass(frozen=True, slots=True)
class RemoteRendition:
    """One rendition of another server with its body."""

    iri: str
    domain: str
    source_key: str
    template: str
    template_version: str | None
    rendition_of: str | None
    account_iri: str
    account_handle: str | None
    account_name: str | None
    name: str
    url: str | None
    updated: str | None
    body_hash: str
    body_html: str
    body_source: str
    fetched_at: float
    expires_at: float
    # The +1 counts that the origin gave at the last read of the object (Section 5.12 rules 10 and 11). The reader shows them as they are.
    plus_total: int
    plus_score: int

    @property
    def key(self) -> str:
        """The short key of the rendition in a page address (``?rendition=``): the start of the SHA-256 of its address."""
        return hashlib.sha256(self.iri.encode("utf-8")).hexdigest()[:16]


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Make the tables when they are not there. Idempotent; the migration of ``store.db`` calls it at each start."""
    for statement in SCHEMA.split(";"):
        if statement.strip():
            conn.execute(statement)


def _add_read(conn: sqlite3.Connection, kind: str, *, domain: str, url: str, target: str, now: float) -> bool:
    """Add one read to the queue. A read of the same kind and address that waits is not added again."""
    if conn.execute("SELECT 1 FROM kf_remote_reads WHERE kind=? AND url=? AND status='pending'", (kind, url)).fetchone() is not None:
        return False
    conn.execute("INSERT INTO kf_remote_reads (kind, kb_iri, domain, url, target_iri, status, started_at) VALUES (?, '', ?, ?, ?, 'pending', ?)", (kind, domain, url, target, now))
    return True


def request_set(conn: sqlite3.Connection, source_key: str, *, origin: str, now: float) -> int:
    """Ask the other servers for the renditions of one source key, when the last answer of a server is older than its lifetime. Return the number of new reads. The caller commits."""
    if not _HEX64.fullmatch(source_key):
        raise ValueError("invalid source key")
    scheme = origin.split("://", 1)[0]
    own = federation_store.iri_host(origin)
    domains = {server.domain for server in known_servers.list_servers(conn)}
    domains |= {str(row[0]) for row in conn.execute("SELECT DISTINCT domain FROM remote_renditions WHERE source_key=?", (source_key,))}
    added = 0
    for domain in sorted(domains)[:SERVERS_MAX]:
        if domain == own or federation_store.blocking_domain(conn, domain) is not None:
            continue
        asked = conn.execute("SELECT asked_at FROM remote_rendition_indexes WHERE domain=? AND source_key=?", (domain, source_key)).fetchone()
        if asked is not None and float(asked[0]) > now - LIFETIME_SECONDS:
            continue
        conn.execute("INSERT INTO remote_rendition_indexes (domain, source_key, asked_at, status, error_code) VALUES (?, ?, ?, 'pending', NULL) ON CONFLICT(domain, source_key) DO UPDATE SET asked_at=excluded.asked_at, status='pending', error_code=NULL", (domain, source_key, now))
        added += int(_add_read(conn, "rendition_index", domain=domain, url=f"{scheme}://{domain}/ap/sources/{source_key}", target=source_key, now=now))
    return added


def _finish(conn: sqlite3.Connection, read: dict[str, Any], *, status: str, error_code: str | None, now: float) -> None:
    conn.execute("UPDATE kf_remote_reads SET status=?, error_code=?, lease_until=NULL, finished_at=? WHERE id=?", (status, error_code, now, int(read["id"])))


def _on_server(iri: Any, read: dict[str, Any], origin: str) -> bool:
    """True when ``iri`` is a permitted address on the server of the read."""
    return isinstance(iri, str) and len(iri) <= vocabulary.MAX_IRI_LENGTH and access.remote_server_domain(iri, origin) == read["domain"]


def forget(conn: sqlite3.Connection, iri: str) -> bool:
    """Delete the copy of one rendition. True when a row was deleted."""
    return conn.execute("DELETE FROM remote_renditions WHERE iri=?", (iri,)).rowcount > 0


def _record_index(conn: sqlite3.Connection, read: dict[str, Any], document: dict[str, Any], *, origin: str, now: float) -> str | None:
    key = str(read["target_iri"])
    items = document.get("orderedItems")
    if document.get("type") != "OrderedCollection" or not isinstance(items, list):
        return "document_invalid"
    listed = [item for item in items if _on_server(item, read, origin)][:INDEX_ITEMS_MAX]
    # A copy that the index does not list now is gone at its origin.
    for (iri,) in conn.execute("SELECT iri FROM remote_renditions WHERE domain=? AND source_key=?", (read["domain"], key)).fetchall():
        if iri not in listed:
            forget(conn, str(iri))
    for iri in listed:
        _add_read(conn, "rendition_object", domain=read["domain"], url=iri, target=key, now=now)
    return None


def _count(value: Any) -> int:
    """A count of the wire, or 0 for a value that is not a count."""
    return value if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= vocabulary.MAX_COUNT else 0


def _text(value: Any, limit: int) -> str | None:
    return value.strip()[:limit] if isinstance(value, str) and value.strip() else None


def _record_object(conn: sqlite3.Connection, read: dict[str, Any], document: dict[str, Any], *, origin: str, now: float) -> str | None:
    iri, key = str(read["url"]), str(read["target_iri"])
    template = document.get(vocabulary.TEMPLATE)
    body_iri, body_hash = document.get(vocabulary.BODY), document.get(vocabulary.BODY_HASH)
    account_iri = document.get("attributedTo")
    rendition_of = document.get(vocabulary.RENDITION_OF)
    if (
        document.get("id") != iri
        or document.get("type") != "Article"
        or document.get(vocabulary.SOURCE_KEY) != key
        or template not in vocabulary.TEMPLATES
        or not _on_server(body_iri, read, origin)
        or not isinstance(body_hash, str)
        or not _HEX64.fullmatch(body_hash)
        or not _on_server(account_iri, read, origin)
        or (rendition_of is not None and not _on_server(rendition_of, read, origin))
    ):
        forget(conn, iri)
        return "document_invalid"
    url = document.get("url")
    conn.execute(
        "INSERT INTO remote_renditions (iri, domain, source_key, template, template_version, rendition_of, account_iri, name, url, updated, body_iri, body_hash, fetched_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(iri) DO UPDATE SET template=excluded.template, template_version=excluded.template_version, rendition_of=excluded.rendition_of, account_iri=excluded.account_iri, name=excluded.name, url=excluded.url, updated=excluded.updated, body_iri=excluded.body_iri, body_hash=excluded.body_hash, fetched_at=excluded.fetched_at, expires_at=excluded.expires_at",
        (iri, read["domain"], key, template, _text(document.get(vocabulary.TEMPLATE_VERSION), 64), rendition_of, account_iri, _text(document.get("name"), _TITLE_MAX) or "", url if _on_server(url, read, origin) else None, _text(document.get("updated") or document.get("published"), 40), body_iri, body_hash, now, now + LIFETIME_SECONDS),
    )
    marks = document.get(vocabulary.PLUS_ONES)
    total, score = (marks.get("totalItems"), marks.get(vocabulary.SCORE)) if isinstance(marks, dict) else (0, 0)
    conn.execute("UPDATE remote_renditions SET plus_total=?, plus_score=? WHERE iri=?", (_count(total), _count(score), iri))
    row = conn.execute("SELECT cached_body_hash, account_handle FROM remote_renditions WHERE iri=?", (iri,)).fetchone()
    if row[0] != body_hash:
        _add_read(conn, "rendition_body", domain=read["domain"], url=str(body_iri), target=iri, now=now)
    known = conn.execute("SELECT account_handle, account_name FROM remote_renditions WHERE account_iri=? AND account_handle IS NOT NULL LIMIT 1", (account_iri,)).fetchone()
    if known is not None:
        conn.execute("UPDATE remote_renditions SET account_handle=?, account_name=? WHERE iri=?", (known[0], known[1], iri))
    else:
        _add_read(conn, "rendition_actor", domain=read["domain"], url=str(account_iri), target=str(account_iri), now=now)
    return None


def _record_body(conn: sqlite3.Connection, read: dict[str, Any], document: dict[str, Any], *, now: float) -> str | None:
    iri = str(read["target_iri"])
    row = conn.execute("SELECT body_hash FROM remote_renditions WHERE iri=? AND body_iri=?", (iri, read["url"])).fetchone()
    if row is None:
        return "rendition_not_found"
    source = document.get("source")
    text = source.get("content") if isinstance(source, dict) else None
    html = document.get("content")
    if document.get("type") != vocabulary.RENDITION_BODY or document.get(vocabulary.RENDITION) != iri or not isinstance(text, str) or not text.strip() or not isinstance(html, str):
        return "document_invalid"
    # The hash of the body must be the hash that the object names: another body is a body of another version, and the next read of the object asks again.
    if hashlib.sha256(text.encode("utf-8")).hexdigest() != row[0] or document.get(vocabulary.BODY_HASH) != row[0]:
        return "hash_mismatch"
    conn.execute("UPDATE remote_renditions SET body_html=?, body_source=?, cached_body_hash=? WHERE iri=?", (nh3.clean(html), text, row[0], iri))
    return None


def _record_actor(conn: sqlite3.Connection, read: dict[str, Any], document: dict[str, Any]) -> str | None:
    username = document.get("preferredUsername")
    if document.get("id") != read["url"] or not isinstance(username, str) or not _USERNAME.fullmatch(username):
        return "document_invalid"
    conn.execute("UPDATE remote_renditions SET account_handle=?, account_name=? WHERE account_iri=?", (f"@{username}@{read['domain']}", _text(document.get("name"), _NAME_MAX), read["url"]))
    return None


def record_result(conn: sqlite3.Connection, read: dict[str, Any], *, document: dict[str, Any] | None, error: str | None, origin: str, now: float) -> None:
    """Store the answer of the sidecar for one read of this module (``knowledge.remote_documents.record_result`` calls it). The caller commits."""
    kind = str(read["kind"])
    if kind not in KINDS:
        raise ValueError(f"not a rendition read: {kind!r}")
    if error is None and document is not None:
        if kind == "rendition_index":
            error = _record_index(conn, read, document, origin=origin, now=now)
        elif kind == "rendition_object":
            error = _record_object(conn, read, document, origin=origin, now=now)
        elif kind == "rendition_body":
            error = _record_body(conn, read, document, now=now)
        else:
            error = _record_actor(conn, read, document)
    elif error == "origin_removed" and kind == "rendition_object":
        # The object is gone at its origin (404 or 410): the copy goes away at once.
        forget(conn, str(read["url"]))
    elif error == "origin_removed" and kind == "rendition_body":
        forget(conn, str(read["target_iri"]))
    if kind == "rendition_index":
        conn.execute("UPDATE remote_rendition_indexes SET status=?, error_code=? WHERE domain=? AND source_key=?", ("ok" if error is None else "failed", error, read["domain"], read["target_iri"]))
    _finish(conn, read, status="done" if error is None else "failed", error_code=error, now=now)


def renditions_of(conn: sqlite3.Connection, source_key: str, *, now: float) -> list[RemoteRendition]:
    """The renditions of other servers for one source key that this server can show now: with a body of the hash of the object, inside the lifetime, and not on a blocked domain. The order is the template order, then the newest first."""
    rows = conn.execute(f"SELECT {_COLUMNS} FROM remote_renditions WHERE source_key=? AND expires_at>? AND body_html IS NOT NULL AND cached_body_hash=body_hash", (source_key, now)).fetchall()
    blocked: dict[str, bool] = {}
    out = []
    for row in rows:
        rendition = RemoteRendition(*tuple(row))
        if rendition.domain not in blocked:
            blocked[rendition.domain] = federation_store.blocking_domain(conn, rendition.domain) is not None
        if not blocked[rendition.domain] and rendition.template in vocabulary.TEMPLATES:
            out.append(rendition)
    out.sort(key=lambda r: r.updated or "", reverse=True)
    out.sort(key=lambda r: vocabulary.TEMPLATES.index(r.template))
    return out


def purge(conn: sqlite3.Connection, *, now: float) -> int:
    """Delete the copies and the index answers that no request refreshed for a long time. Return the number of deleted copies."""
    conn.execute("DELETE FROM remote_rendition_indexes WHERE asked_at<?", (now - _PURGE_SECONDS,))
    return conn.execute("DELETE FROM remote_renditions WHERE expires_at<?", (now - _PURGE_SECONDS,)).rowcount
