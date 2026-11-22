"""Reads of the documents of a remote knowledge base for a curation (KNOWLEDGE_FEDERATION_DESIGN.md Section 11; unit KF3).

A reviewer of a local shared knowledge base K opens a remote shared knowledge base S of a theme of K, selects documents, and this server copies them (Section 11 rules 1 and 2). Only the sidecar makes requests to other servers, and the sidecar calls Claire (FEDERATION.md Section 2). Thus each read is a row of ``kf_remote_reads`` that the sidecar takes with a lease: it reads the address with a GET that the server actor signs and gives the JSON document to Claire.

| Kind | What the sidecar reads | What Claire does with the answer |
| :--- | :--- | :--- |
| ``documents`` | The documents collection of S (``claire:documents``), page after page | The list of ``kf_remote_documents``: the metadata of each visible document of S |
| ``document`` | One document of S, with its extraction | The copy in K: ``contributions.curate_remote`` |
| ``recheck`` | One document of S that this server holds a copy of, after S accepted the follow of this server | A 404 or 410: the origin removed it (``contributions.origin_removed``); a newer version: a revision (``contributions.start_remote_revisions``) |

This module owns the tables ``kf_remote_reads`` and ``kf_remote_documents``. No function here commits.

Rules:

- S must be a member of an active theme that K is a member of (``source_of``), its domain must not be blocked, and the switch ``federation`` must be on.
- The list of a knowledge base is read again when it is older than ``LISTING_MAX_AGE_SECONDS``. A complete read deletes each row that it did not contain.
- A document for a curation must be in the list of S: the reviewer selected it there. Its address and each page are on the server of S.
- A read that the sidecar does not answer comes again after its lease; after ``MAX_ATTEMPTS`` leases it ends as ``failed`` with ``read_timeout``.
- The list of one knowledge base has at most ``MAX_LISTED_DOCUMENTS`` rows, also after reads that failed: a new row takes the place of the oldest row that the current read did not see, or is not stored.
- An answer of the sidecar with the error ``origin_removed`` is a 404 or 410 of the origin: the document is gone there (design Section 10.3).
"""

from __future__ import annotations

import sqlite3
from typing import Any

from ...store import federation as federation_store
from ...store import kb_collab
from ...store import themes as themes_store
from . import access, known_servers, remote_kbs, vocabulary

__all__ = [
    "LEASE_SECONDS",
    "LISTING_MAX_AGE_SECONDS",
    "MAX_ATTEMPTS",
    "MAX_PAGES",
    "TABLES",
    "SourceError",
    "curation_reads",
    "ensure_schema",
    "lease",
    "listing",
    "listing_state",
    "purge_blocked",
    "record_result",
    "remove_listings_of_domain",
    "request_rechecks",
    "request_document",
    "request_listing",
    "source_of",
    "sources_of",
]

LISTING_MAX_AGE_SECONDS = 600.0
# A list whose last read failed is not read again before this time.
FAILED_LISTING_WAIT_SECONDS = 60.0
LEASE_SECONDS = 120.0
MAX_ATTEMPTS = 5
MAX_PAGES = 50
# The rows of one knowledge base in the list (KS2).
MAX_LISTED_DOCUMENTS = 1000
_TITLE_MAX = 500
_IRI_MAX = 2048
# The error code of the sidecar for a 404 or 410 of the origin.
ORIGIN_REMOVED = "origin_removed"
_READ_RETENTION_SECONDS = 30 * 86400.0

SCHEMA = """
CREATE TABLE IF NOT EXISTS kf_remote_documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kb_iri TEXT NOT NULL,
    iri TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    url TEXT,
    content_hash TEXT NOT NULL,
    published TEXT NOT NULL,
    updated TEXT,
    seen_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_kf_remote_documents_kb ON kf_remote_documents(kb_iri, id);
CREATE TABLE IF NOT EXISTS kf_remote_reads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    kb_iri TEXT NOT NULL,
    domain TEXT NOT NULL,
    url TEXT NOT NULL,
    target_iri TEXT,
    kb_id INTEGER,
    account_id INTEGER,
    status TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    pages INTEGER NOT NULL DEFAULT 0,
    items INTEGER NOT NULL DEFAULT 0,
    error_code TEXT,
    contribution_public_id INTEGER,
    lease_until REAL,
    started_at REAL NOT NULL,
    finished_at REAL
);
CREATE INDEX IF NOT EXISTS idx_kf_remote_reads_status ON kf_remote_reads(status, id)
"""

_DOCUMENT_COLUMNS = "id, kb_iri, iri, title, url, content_hash, published, updated, seen_at"
_READ_COLUMNS = "id, kind, kb_iri, domain, url, target_iri, kb_id, account_id, status, attempts, pages, items, error_code, contribution_public_id, lease_until, started_at, finished_at"
# The columns that the code of this module reads, for each table. The migration (``store.db``) gives a table of a different form a new name and makes this one again.
TABLES: dict[str, tuple[str, ...]] = {"kf_remote_documents": tuple(_DOCUMENT_COLUMNS.split(", ")), "kf_remote_reads": tuple(_READ_COLUMNS.split(", "))}


class SourceError(Exception):
    """A remote knowledge base is not a curation source of K, or a document is not in its list. ``status`` and ``code`` are those of the HTTP answer."""

    def __init__(self, status: int, code: str) -> None:
        super().__init__(code)
        self.status = status
        self.code = code


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Make the tables and the indexes when they are not there. Idempotent; the migration of ``store.db`` calls it at each start."""
    for statement in SCHEMA.split(";"):
        if statement.strip():
            conn.execute(statement)


def _read(row: sqlite3.Row | tuple) -> dict[str, Any]:
    return dict(zip(TABLES["kf_remote_reads"], tuple(row)))


def get_read(conn: sqlite3.Connection, read_id: int) -> dict[str, Any] | None:
    row = conn.execute(f"SELECT {_READ_COLUMNS} FROM kf_remote_reads WHERE id=?", (int(read_id),)).fetchone()
    return None if row is None else _read(row)


# --- sources ---------------------------------------------------------------------------------------


def sources_of(conn: sqlite3.Connection, kb_id: int) -> list[remote_kbs.RemoteKnowledgeBase]:
    """The remote knowledge bases that a reviewer of K can curate from: the members (not excluded) of each active theme that K is a member of (design Section 11 rule 1). A record of a blocked domain is not a source."""
    themes = set(themes_store.themes_of_kb(conn, int(kb_id)))
    wanted = {int(member["remote_kb_id"]) for member in themes_store.remote_members(conn) if int(member["theme_id"]) in themes}
    return [record for record in remote_kbs.list_all(conn) if record.id in wanted]


def source_of(conn: sqlite3.Connection, *, kb_id: int, remote_kb_id: int) -> remote_kbs.RemoteKnowledgeBase:
    """The remote knowledge base S that a reviewer of K curates from. ``SourceError(404, "source_not_found")`` for no record (or a record of a blocked domain); ``SourceError(409, "source_not_in_theme")`` when S shares no active theme with K."""
    record = remote_kbs.get(conn, int(remote_kb_id))
    if record is None or federation_store.blocking_domain(conn, record.domain) is not None:
        raise SourceError(404, "source_not_found")
    if record.id not in {source.id for source in sources_of(conn, kb_id)}:
        raise SourceError(409, "source_not_in_theme")
    return record


# --- requests --------------------------------------------------------------------------------------


def _last_listing(conn: sqlite3.Connection, kb_iri: str) -> dict[str, Any] | None:
    row = conn.execute(f"SELECT {_READ_COLUMNS} FROM kf_remote_reads WHERE kind='documents' AND kb_iri=? ORDER BY id DESC LIMIT 1", (kb_iri,)).fetchone()
    return None if row is None else _read(row)


def request_listing(conn: sqlite3.Connection, source: remote_kbs.RemoteKnowledgeBase, *, now: float) -> dict[str, Any]:
    """Make sure that the list of the documents of S is not older than ``LISTING_MAX_AGE_SECONDS``: add a read when there is none that waits and the last one is too old (a failed read: ``FAILED_LISTING_WAIT_SECONDS``). Return the last read."""
    last = _last_listing(conn, source.iri)
    if last is not None and (last["status"] == "pending" or float(last["finished_at"] or 0.0) > now - (LISTING_MAX_AGE_SECONDS if last["status"] == "done" else FAILED_LISTING_WAIT_SECONDS)):
        return last
    conn.execute("DELETE FROM kf_remote_reads WHERE status!='pending' AND finished_at < ?", (now - _READ_RETENTION_SECONDS,))
    cur = conn.execute("INSERT INTO kf_remote_reads (kind, kb_iri, domain, url, status, started_at) VALUES ('documents', ?, ?, ?, 'pending', ?)", (source.iri, source.domain, remote_kbs.documents_url(source), now))
    known_servers.note_server(conn, source.domain, source="local_read", now=now)
    read = get_read(conn, int(cur.lastrowid))
    assert read is not None
    return read


def listing_state(conn: sqlite3.Connection, source: remote_kbs.RemoteKnowledgeBase) -> dict[str, Any]:
    """The state of the list of S for the WebUI: ``state`` is ``reading`` (a read waits), ``ready`` (the last read is complete), ``failed`` (the last read failed; ``error`` has its code), or ``none`` (no read). ``read_at`` is the time of the last complete read."""
    last = _last_listing(conn, source.iri)
    done = conn.execute("SELECT MAX(finished_at) FROM kf_remote_reads WHERE kind='documents' AND kb_iri=? AND status='done'", (source.iri,)).fetchone()[0]
    state = "none" if last is None else {"pending": "reading", "done": "ready", "failed": "failed"}[last["status"]]
    return {"state": state, "error": None if last is None or last["status"] != "failed" else last["error_code"], "read_at": done}


def listing(conn: sqlite3.Connection, kb_iri: str) -> list[dict[str, Any]]:
    """The documents of S that the last reads gave, in the sequence of the collection (the newest first)."""
    return [dict(zip(TABLES["kf_remote_documents"], tuple(row))) for row in conn.execute(f"SELECT {_DOCUMENT_COLUMNS} FROM kf_remote_documents WHERE kb_iri=? ORDER BY published DESC, id", (kb_iri,)).fetchall()]


def listed_document(conn: sqlite3.Connection, kb_iri: str, iri: str) -> dict[str, Any] | None:
    row = conn.execute(f"SELECT {_DOCUMENT_COLUMNS} FROM kf_remote_documents WHERE kb_iri=? AND iri=?", (kb_iri, iri)).fetchone()
    return None if row is None else dict(zip(TABLES["kf_remote_documents"], tuple(row)))


def request_document(conn: sqlite3.Connection, source: remote_kbs.RemoteKnowledgeBase, *, document_iri: str, kb_id: int, account_id: int, now: float) -> dict[str, Any]:
    """Add the read of one document of S for a curation into K. ``SourceError(404, "document_not_found")`` when the document is not in the list of S. A read of the same document for the same K that waits is returned and no second one is added."""
    if listed_document(conn, source.iri, document_iri) is None:
        raise SourceError(404, "document_not_found")
    waiting = conn.execute(f"SELECT {_READ_COLUMNS} FROM kf_remote_reads WHERE kind='document' AND status='pending' AND kb_id=? AND target_iri=?", (int(kb_id), document_iri)).fetchone()
    if waiting is not None:
        return _read(waiting)
    cur = conn.execute(
        "INSERT INTO kf_remote_reads (kind, kb_iri, domain, url, target_iri, kb_id, account_id, status, started_at) VALUES ('document', ?, ?, ?, ?, ?, ?, 'pending', ?)",
        (source.iri, source.domain, document_iri, document_iri, int(kb_id), int(account_id), now),
    )
    known_servers.note_server(conn, source.domain, source="local_curation", now=now)
    read = get_read(conn, int(cur.lastrowid))
    assert read is not None
    return read


def request_rechecks(conn: sqlite3.Connection, *, kb_iri: str, domain: str, now: float) -> int:
    """Add a read (``kind`` ``recheck``) of each document of the actor ``kb_iri`` that this server holds an open copy of, unless such a read waits (design Section 10.3: a removal or a revision at the origin between the read of a curation and the follow of this server reached no follower). Return the number of new reads. No commit."""
    added = 0
    for source_iri in kb_collab.open_remote_sources_of_kb_actor(conn, kb_iri):
        if conn.execute("SELECT 1 FROM kf_remote_reads WHERE kind='recheck' AND status='pending' AND target_iri=?", (source_iri,)).fetchone() is not None:
            continue
        conn.execute("INSERT INTO kf_remote_reads (kind, kb_iri, domain, url, target_iri, status, started_at) VALUES ('recheck', ?, ?, ?, ?, 'pending', ?)", (kb_iri, domain, source_iri, source_iri, now))
        added += 1
    return added


def purge_blocked(conn: sqlite3.Connection) -> int:
    """Delete the list rows of each knowledge base whose domain a domain block applies to (design Section 6.3). Return the number of deleted rows."""
    removed = 0
    for (kb_iri,) in conn.execute("SELECT DISTINCT kb_iri FROM kf_remote_documents").fetchall():
        host = federation_store.iri_host(str(kb_iri))
        if host is not None and federation_store.blocking_domain(conn, host) is not None:
            removed += conn.execute("DELETE FROM kf_remote_documents WHERE kb_iri=?", (str(kb_iri),)).rowcount
    return removed


def remove_listings_of_domain(conn: sqlite3.Connection, domain: str) -> int:
    """Delete the list rows of each knowledge base on one domain, and the reads of that domain that ended (the admin removes a known server). Return the number of deleted list rows."""
    removed = 0
    for (kb_iri,) in conn.execute("SELECT DISTINCT kb_iri FROM kf_remote_documents").fetchall():
        if federation_store.iri_host(str(kb_iri)) == domain:
            removed += conn.execute("DELETE FROM kf_remote_documents WHERE kb_iri=?", (str(kb_iri),)).rowcount
    conn.execute("DELETE FROM kf_remote_reads WHERE domain=? AND status!='pending'", (domain,))
    return removed


def curation_reads(conn: sqlite3.Connection, kb_id: int, *, limit: int = 50) -> list[dict[str, Any]]:
    """The newest reads of documents for curations into K, with the title of the list."""
    rows = [_read(row) for row in conn.execute(f"SELECT {_READ_COLUMNS} FROM kf_remote_reads WHERE kind='document' AND kb_id=? ORDER BY id DESC LIMIT ?", (int(kb_id), int(limit))).fetchall()]
    for row in rows:
        listed = listed_document(conn, row["kb_iri"], row["target_iri"])
        row["title"] = None if listed is None else listed["title"]
    return rows


# --- the sidecar takes and answers -----------------------------------------------------------------


def _finish(conn: sqlite3.Connection, read_id: int, *, status: str, error_code: str | None, now: float, contribution: int | None = None) -> None:
    conn.execute("UPDATE kf_remote_reads SET status=?, error_code=?, contribution_public_id=?, lease_until=NULL, finished_at=? WHERE id=?", (status, error_code, contribution, now, int(read_id)))


def lease(conn: sqlite3.Connection, *, now: float, limit: int) -> list[dict[str, Any]]:
    """Return the oldest reads that are ready, and give a lease on each one. A read of a blocked domain ends as ``failed`` (``blocked_domain``); a read with ``MAX_ATTEMPTS`` leases and no answer ends as ``failed`` (``read_timeout``). The caller makes sure that the switch ``federation`` is on."""
    out: list[dict[str, Any]] = []
    for row in conn.execute(f"SELECT {_READ_COLUMNS} FROM kf_remote_reads WHERE status='pending' AND (lease_until IS NULL OR lease_until<=?) ORDER BY id", (now,)).fetchall():
        read = _read(row)
        if federation_store.blocking_domain(conn, read["domain"]) is not None:
            _finish(conn, read["id"], status="failed", error_code="blocked_domain", now=now)
            continue
        if read["attempts"] >= MAX_ATTEMPTS:
            _finish(conn, read["id"], status="failed", error_code="read_timeout", now=now)
            continue
        if len(out) >= limit:
            break
        conn.execute("UPDATE kf_remote_reads SET attempts=attempts+1, lease_until=? WHERE id=?", (now + LEASE_SECONDS, read["id"]))
        out.append(read)
    return out


def _listed_item(item: Any, read: dict[str, Any], origin: str) -> tuple[str, str, str | None, str, str, str | None] | None:
    """The row of one item of a page of the documents collection, or None for an item that is not the metadata of a document of the server of S."""
    if not isinstance(item, dict) or item.get("type") != "Article":
        return None
    iri, title, url, content_hash, published, updated = (item.get(key) for key in ("id", "name", "url", vocabulary.CONTENT_HASH, "published", "updated"))
    if not vocabulary.is_iri(iri) or access.remote_server_domain(iri, origin) != read["domain"]:
        return None
    if len(iri) > _IRI_MAX or not isinstance(title, str) or not title.strip() or len(title) > _TITLE_MAX or not isinstance(content_hash, str) or not vocabulary.is_content_hash(content_hash):
        return None
    if (url is not None and (not vocabulary.is_iri(url) or len(url) > _IRI_MAX)) or not _is_time(published) or (updated is not None and not _is_time(updated)):
        return None
    return iri, title, url, content_hash, published, updated


def _is_time(value: Any) -> bool:
    """True for an ISO 8601 text with a time zone (the form of the wire contract)."""
    try:
        vocabulary.time_seconds(value)
    except vocabulary.ContractError:
        return False
    return isinstance(value, str) and len(value) <= 64


def _store_listed(conn: sqlite3.Connection, read: dict[str, Any], row: tuple[Any, ...], *, now: float) -> bool:
    """Store one row of the list of S. A new row needs a place: the list of the knowledge base has at most ``MAX_LISTED_DOCUMENTS`` rows, and a full list gives the place of its oldest row that this read did not see. False when the row has no place."""
    iri = row[0]
    if conn.execute("SELECT 1 FROM kf_remote_documents WHERE iri=? AND kb_iri=?", (iri, read["kb_iri"])).fetchone() is None:
        if int(conn.execute("SELECT COUNT(*) FROM kf_remote_documents WHERE kb_iri=?", (read["kb_iri"],)).fetchone()[0]) >= MAX_LISTED_DOCUMENTS:
            old = conn.execute("SELECT id FROM kf_remote_documents WHERE kb_iri=? AND seen_at<? ORDER BY seen_at, id LIMIT 1", (read["kb_iri"], float(read["started_at"]))).fetchone()
            if old is None:
                return False
            conn.execute("DELETE FROM kf_remote_documents WHERE id=?", (int(old[0]),))
    conn.execute(
        "INSERT INTO kf_remote_documents (kb_iri, iri, title, url, content_hash, published, updated, seen_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(iri) DO UPDATE SET kb_iri=excluded.kb_iri, title=excluded.title, url=excluded.url, content_hash=excluded.content_hash, published=excluded.published, updated=excluded.updated, seen_at=excluded.seen_at",
        (read["kb_iri"], *row, now),
    )
    return True


def _record_page(conn: sqlite3.Connection, read: dict[str, Any], document: dict[str, Any], *, origin: str, now: float) -> str:
    """Store one page of the documents collection of S. Return the state of the read after it: ``pending`` (a next page follows), ``done``, or ``failed``."""
    kind = document.get("type")
    if kind not in ("OrderedCollection", "OrderedCollectionPage"):
        _finish(conn, read["id"], status="failed", error_code="collection_invalid", now=now)
        return "failed"
    items = document.get("orderedItems")
    stored = 0
    if items is not None:
        if not isinstance(items, list):
            _finish(conn, read["id"], status="failed", error_code="collection_invalid", now=now)
            return "failed"
        for item in items:
            row = _listed_item(item, read, origin)
            if row is None or read["items"] + stored >= MAX_LISTED_DOCUMENTS:
                continue
            if _store_listed(conn, read, row, now=now):
                stored += 1
    following = document.get("first") if kind == "OrderedCollection" and items is None else document.get("next")
    if following is not None:
        if not vocabulary.is_iri(following) or access.remote_server_domain(following, origin) != read["domain"]:
            _finish(conn, read["id"], status="failed", error_code="page_on_other_server", now=now)
            return "failed"
        if read["pages"] + 1 >= MAX_PAGES:
            _finish(conn, read["id"], status="failed", error_code="too_many_pages", now=now)
            return "failed"
        # The next page is a new request of the same read: the attempts start again.
        conn.execute("UPDATE kf_remote_reads SET url=?, pages=pages+1, items=items+?, attempts=0, lease_until=NULL WHERE id=?", (following, stored, read["id"]))
        return "pending"
    conn.execute("UPDATE kf_remote_reads SET pages=pages+1, items=items+? WHERE id=?", (stored, read["id"]))
    # The end of a complete read: a document that the read did not contain is not in the collection of S now.
    conn.execute("DELETE FROM kf_remote_documents WHERE kb_iri=? AND seen_at<?", (read["kb_iri"], float(read["started_at"])))
    _finish(conn, read["id"], status="done", error_code=None, now=now)
    return "done"


def _record_document(conn: sqlite3.Connection, read: dict[str, Any], document: dict[str, Any], *, now: float) -> str:
    """Make the copy of a curation from the document that the sidecar read. Return ``done`` or ``failed``; the row has the code of the refusal or the contribution."""
    from ...collaboration import contributions  # the import is here so that the store modules of this package do not load the contribution code

    try:
        kind = vocabulary.validate_object(document, top_level=True)
    except vocabulary.ContractError as exc:
        _finish(conn, read["id"], status="failed", error_code=exc.code, now=now)
        return "failed"
    if kind != "document" or vocabulary.EXTRACTION_OF not in document:
        _finish(conn, read["id"], status="failed", error_code="not_a_document", now=now)
        return "failed"
    # The origin server is the authority for its objects (design Section 6.4): the document is the one that was asked for.
    if document["id"] != read["target_iri"]:
        _finish(conn, read["id"], status="failed", error_code="other_document", now=now)
        return "failed"
    refusal = contributions.remote_article_refusal(document)
    if refusal is not None:
        _finish(conn, read["id"], status="failed", error_code=refusal, now=now)
        return "failed"
    if conn.execute("SELECT 1 FROM knowledge_bases WHERE id=? AND is_personal=0 AND account_id IS NULL AND state='active'", (read["kb_id"],)).fetchone() is None:
        _finish(conn, read["id"], status="failed", error_code="kb_not_available", now=now)
        return "failed"
    conn.execute("SAVEPOINT kf_remote_curation")
    try:
        row = contributions.curate_remote(conn, kb_id=int(read["kb_id"]), source_kb_iri=read["kb_iri"], article=document, curator_account_id=read["account_id"], now=now)
    except contributions.ContributionError as exc:
        conn.execute("ROLLBACK TO SAVEPOINT kf_remote_curation")
        conn.execute("RELEASE SAVEPOINT kf_remote_curation")
        _finish(conn, read["id"], status="failed", error_code=exc.code, now=now)
        return "failed"
    conn.execute("RELEASE SAVEPOINT kf_remote_curation")
    _finish(conn, read["id"], status="done", error_code=None, now=now, contribution=int(row["public_id"]))
    return "done"


def _record_recheck(conn: sqlite3.Connection, read: dict[str, Any], document: dict[str, Any] | None, error: str | None, *, now: float) -> str:
    """Apply the read again of a document that this server holds a copy of (``request_rechecks``). ``origin_removed`` (a 404 or 410 of the origin): each copy is hidden and waits in the review queue, as after a ``Delete`` (reason ``other``). A valid document: the revision rules of an ``Update`` (only a newer version changes a copy). Return ``done`` or ``failed``."""
    from ...collaboration import contributions  # the import is here so that the store modules of this package do not load the contribution code

    if error == ORIGIN_REMOVED:
        contributions.origin_removed(conn, source_kb_iri=read["kb_iri"], source_iri=read["target_iri"], reason="other", now=now)
        _finish(conn, read["id"], status="done", error_code=ORIGIN_REMOVED, now=now)
        return "done"
    if error is not None or document is None:
        _finish(conn, read["id"], status="failed", error_code=error or "document_unavailable", now=now)
        return "failed"
    try:
        kind = vocabulary.validate_object(document, top_level=True)
    except vocabulary.ContractError as exc:
        _finish(conn, read["id"], status="failed", error_code=exc.code, now=now)
        return "failed"
    if kind != "document" or vocabulary.EXTRACTION_OF not in document or document["id"] != read["target_iri"]:
        _finish(conn, read["id"], status="failed", error_code="other_document", now=now)
        return "failed"
    refusal = contributions.remote_article_refusal(document)
    if refusal is None and contributions.remote_time(document) > now + contributions.REMOTE_FUTURE_SECONDS:
        refusal = "invalid_time"
    if refusal is not None:
        _finish(conn, read["id"], status="failed", error_code=refusal, now=now)
        return "failed"
    contributions.start_remote_revisions(conn, source_kb_iri=read["kb_iri"], article=document, now=now)
    _finish(conn, read["id"], status="done", error_code=None, now=now)
    return "done"


def record_result(conn: sqlite3.Connection, read_id: int, *, document: dict[str, Any] | None, error: str | None, origin: str, now: float) -> dict[str, Any] | None:
    """Store the answer of the sidecar for one read: the JSON document as it came, or an error code. Return the read after it, or None when there is no read that waits with that ID. A read of a blocked domain ends as ``failed``."""
    read = get_read(conn, read_id)
    if read is None or read["status"] != "pending":
        return None
    if federation_store.blocking_domain(conn, read["domain"]) is not None:
        _finish(conn, read["id"], status="failed", error_code="blocked_domain", now=now)
    elif read["kind"] == "recheck":
        _record_recheck(conn, read, document, error, now=now)
    elif error is not None or document is None:
        _finish(conn, read["id"], status="failed", error_code=error or "document_unavailable", now=now)
    elif read["kind"] == "documents":
        _record_page(conn, read, document, origin=origin, now=now)
    else:
        _record_document(conn, read, document, now=now)
    return get_read(conn, read_id)
