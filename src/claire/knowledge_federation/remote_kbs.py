"""Store of remote knowledge bases (KNOWLEDGE_FEDERATION_DESIGN.md Section 8.1, unit KF2).

A remote knowledge base is the record of a shared knowledge base of another Claire server: its label, description, knowledge type, counts, signature, and IRI. It is not a partition of ``claire.db`` and has no documents (Section 8.1 rule 3). The records come from the ``/kb`` collection of a known server (``knowledge_federation.known_servers``): one full read at the first contact, and a new read each day (rule 2). Only the sidecar makes requests to other servers, so this module gives the reads that are due and takes their results.

This module owns the tables ``kf_remote_knowledge_bases`` and ``kf_remote_kb_reads`` of ``claire.db``. No function here commits: the caller owns the transaction.

Rules:

- A server is read only after a good read of its NodeInfo and server actor (``known_servers.record_read``), and never while a domain block applies to it.
- One read is a sequence of batches (``record_batch``) and one end (``finish_read``). Each item of a batch is checked with ``vocabulary.validate_object``; an item that is not valid is refused and counted, and the other items are stored.
- A record that a complete read does not contain is deleted: the knowledge base is not in the collection of its server now. A failed read deletes nothing.
- When a domain block starts, the records of that domain are deleted (``purge_blocked``): there is no exchange with a blocked server (Section 6.3). A record has no document, so no copy is lost.
- The memberships of a deleted record in local themes are deleted with it (``store.themes``).
- The record keeps ``updated`` of the object (the time of the change of the record on its server). An ``Update`` whose ``updated`` is not after the stored one is refused (``stale_update``): an old ``Update`` that came late does not set an older label. A read whose item is older than the stored record keeps the stored record.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any

from ..store import federation as federation_store
from ..store import themes as themes_store
from . import access, known_servers, vocabulary

__all__ = [
    "COLLECTION_PATH",
    "LABEL_MAX",
    "DESCRIPTION_MAX",
    "PER_SERVER_MAX",
    "READ_INTERVAL_SECONDS",
    "READ_LEASE_SECONDS",
    "TABLES",
    "BatchResult",
    "RemoteKnowledgeBase",
    "accepts_automatic",
    "apply_update",
    "collection_url",
    "delete_record",
    "documents_url",
    "count_by_domain",
    "ensure_schema",
    "finish_read",
    "get",
    "get_by_iri",
    "lease_reads",
    "list_all",
    "note_automatic_not_accepted",
    "list_of_domain",
    "purge_blocked",
    "read_state",
    "record_batch",
    "record_read_error",
    "remove_domain",
    "signature_rows",
    "view",
]

# The path of the ActivityStreams form of the ``/kb`` collection on each Claire server. It is below ``/ap/``, so the reverse proxy sends it to the sidecar, which verifies the signature of the request (FEDERATION.md Section 10).
COLLECTION_PATH = "/ap/kb"
# A server is read again one time each day (design Section 8.1 rule 2).
READ_INTERVAL_SECONDS = 86400.0
# A read that the sidecar took and did not end is ready again after this time.
READ_LEASE_SECONDS = 600.0
# Limits for the data of another server (KS2).
PER_SERVER_MAX = 1000
LABEL_MAX = 200
DESCRIPTION_MAX = 2000
# The length of one IRI of a record, of the type and the name of one row of the signature, and of the stored record (the JSON of ``payload``).
IRI_MAX = 2048
SIGNATURE_TYPE_MAX = 64
SIGNATURE_NAME_MAX = 200
RECORD_MAX_BYTES = 128 * 1024
# The keys of a ``claire:KnowledgeBase`` that the record keeps (the wire contract, ``vocabulary``). Each other key of the object that came is not stored.
RECORD_KEYS: tuple[str, ...] = (
    "id", "type", "name", "summary", "url", "inbox", "outbox", "followers", vocabulary.DOCUMENTS,
    vocabulary.KNOWLEDGE_TYPE, vocabulary.DOCUMENT_COUNT, vocabulary.ENTITY_COUNT, vocabulary.RELATION_COUNT, vocabulary.SIGNATURE, vocabulary.ACCEPTS_AUTOMATIC, "updated",
)
_MAX_ERROR_LENGTH = 200

SCHEMA = """
CREATE TABLE IF NOT EXISTS kf_remote_knowledge_bases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    iri TEXT NOT NULL UNIQUE,
    domain TEXT NOT NULL,
    url TEXT NOT NULL,
    label TEXT NOT NULL,
    description TEXT NOT NULL,
    knowledge_type TEXT NOT NULL,
    document_count INTEGER NOT NULL,
    entity_count INTEGER NOT NULL,
    relation_count INTEGER NOT NULL,
    signature TEXT,
    payload TEXT NOT NULL,
    first_seen_at REAL NOT NULL,
    seen_at REAL NOT NULL,
    changed_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_kf_remote_knowledge_bases_domain ON kf_remote_knowledge_bases(domain, seen_at);
CREATE TABLE IF NOT EXISTS kf_remote_kb_reads (
    domain TEXT PRIMARY KEY,
    read_started_at REAL,
    lease_until REAL,
    last_read_at REAL,
    stored_count INTEGER NOT NULL DEFAULT 0,
    refused_count INTEGER NOT NULL DEFAULT 0,
    refused_code TEXT,
    error_count INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    last_error_at REAL
)
"""

_KB_COLUMNS = "id, iri, domain, url, label, description, knowledge_type, document_count, entity_count, relation_count, signature, payload, first_seen_at, seen_at, changed_at"
_READ_COLUMNS = "domain, read_started_at, lease_until, last_read_at, stored_count, refused_count, refused_code, error_count, last_error, last_error_at"
# The columns that the code of this module reads, for each table. The migration (``store.db``) gives a table of a different form a new name and makes this one again.
TABLES: dict[str, tuple[str, ...]] = {"kf_remote_knowledge_bases": tuple(_KB_COLUMNS.split(", ")), "kf_remote_kb_reads": tuple(_READ_COLUMNS.split(", "))}


@dataclass(frozen=True, slots=True)
class RemoteKnowledgeBase:
    id: int
    iri: str
    domain: str
    url: str
    label: str
    description: str
    knowledge_type: str
    document_count: int
    entity_count: int
    relation_count: int
    signature: str | None  # the JSON of ``claire:signature`` as it came, or None for a knowledge base with no signature
    payload: str | None  # the JSON of the keys ``RECORD_KEYS`` of the ``claire:KnowledgeBase``; None in the result of ``list_all``, which does not read it
    first_seen_at: float
    seen_at: float
    changed_at: float


@dataclass(frozen=True, slots=True)
class ReadState:
    domain: str
    read_started_at: float | None
    lease_until: float | None
    last_read_at: float | None
    stored_count: int
    refused_count: int
    refused_code: str | None
    error_count: int
    last_error: str | None
    last_error_at: float | None


@dataclass(frozen=True, slots=True)
class BatchResult:
    """The result of one batch: the number of items that were stored and refused, and the code of the first refusal (``None`` when no item was refused)."""

    stored: int
    refused: int
    refused_code: str | None


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Make the tables when they are not there. Idempotent; the migration of ``store.db`` calls it at each start."""
    for statement in SCHEMA.split(";"):
        if statement.strip():
            conn.execute(statement)


def collection_url(domain: str, origin: str) -> str:
    """The address of the ``/kb`` collection of another server. The scheme is ``https``; it is ``http`` only when the canonical origin of this server uses ``http`` (development), the rule of ``access.remote_server_domain``."""
    scheme = "http" if origin.startswith("http://") else "https"
    return f"{scheme}://{domain}{COLLECTION_PATH}"


def _kb(row: sqlite3.Row | tuple) -> RemoteKnowledgeBase:
    return RemoteKnowledgeBase(*tuple(row))


def get(conn: sqlite3.Connection, remote_kb_id: int) -> RemoteKnowledgeBase | None:
    row = conn.execute(f"SELECT {_KB_COLUMNS} FROM kf_remote_knowledge_bases WHERE id=?", (int(remote_kb_id),)).fetchone()
    return None if row is None else _kb(row)


def get_by_iri(conn: sqlite3.Connection, iri: str) -> RemoteKnowledgeBase | None:
    row = conn.execute(f"SELECT {_KB_COLUMNS} FROM kf_remote_knowledge_bases WHERE iri=?", (iri,)).fetchone()
    return None if row is None else _kb(row)


# The columns of ``list_all``: ``payload`` is not read (a list of all records is for the public pages and the judgement, which do not need it).
_LIST_COLUMNS = _KB_COLUMNS.replace("payload", "NULL AS payload")


def list_all(conn: sqlite3.Connection, *, knowledge_type: str | None = None) -> list[RemoteKnowledgeBase]:
    """Each remote knowledge base, the newest record first, without ``payload`` (None). A record of a blocked domain is not in the result (``purge_blocked`` deletes it; this check covers a block that another code path made)."""
    sql, args = f"SELECT {_LIST_COLUMNS} FROM kf_remote_knowledge_bases", []
    if knowledge_type is not None:
        if knowledge_type not in vocabulary.KNOWLEDGE_TYPES:
            raise ValueError(f"invalid knowledge type: {knowledge_type!r}")
        sql += " WHERE knowledge_type=?"
        args.append(knowledge_type)
    rows = [_kb(row) for row in conn.execute(sql + " ORDER BY first_seen_at DESC, id DESC", args).fetchall()]
    blocked = {domain: federation_store.blocking_domain(conn, domain) is not None for domain in {row.domain for row in rows}}
    return [row for row in rows if not blocked[row.domain]]


def list_of_domain(conn: sqlite3.Connection, domain: str) -> list[RemoteKnowledgeBase]:
    return [_kb(row) for row in conn.execute(f"SELECT {_KB_COLUMNS} FROM kf_remote_knowledge_bases WHERE domain=? ORDER BY label, id", (domain,)).fetchall()]


def count_by_domain(conn: sqlite3.Connection) -> dict[str, int]:
    return {str(row[0]): int(row[1]) for row in conn.execute("SELECT domain, COUNT(*) FROM kf_remote_knowledge_bases GROUP BY domain").fetchall()}


def signature_rows(kb: RemoteKnowledgeBase) -> list[tuple[str, str, float]]:
    """The rows ``(type, norm_name, weight)`` of the signature of a remote knowledge base. Empty for a knowledge base with no signature. The stored value passed ``vocabulary.validate_object``; a value of another form is a defect of the data: ``ValueError``."""
    if kb.signature is None:
        return []
    value = json.loads(kb.signature)
    return [(str(row[0]), str(row[1]), float(row[2])) for row in value["entities"]]


def accepts_automatic(kb: RemoteKnowledgeBase) -> bool | None:
    """What the record says about the server of the knowledge base (``claire:acceptsAutomatic``, unit KF5): True when it accepts automatic contributions, False when it rejects them, None when the record does not tell it (a record of ``list_all`` has no payload, and a record of a read before unit KF5 has no such key). With None the other server decides when the offer comes."""
    if kb.payload is None:
        return None
    value = json.loads(kb.payload).get(vocabulary.ACCEPTS_AUTOMATIC)
    return value if isinstance(value, bool) else None


def note_automatic_not_accepted(conn: sqlite3.Connection, domain: str) -> int:
    """A knowledge base actor of ``domain`` rejected an automatic contribution with ``vocabulary.AUTOMATIC_NOT_ACCEPTED``: the switch of that server is off. Each record of the domain gets ``claire:acceptsAutomatic`` false, so that the next run of an automatic rule sends no offer there; the next read of the ``/kb`` collection (or an ``Update`` of a record) gives the state of that server again. ``changed_at`` does not change: the content of the knowledge base is the same. Return the number of records."""
    changed = 0
    for row in conn.execute("SELECT id, payload FROM kf_remote_knowledge_bases WHERE domain=?", (domain,)).fetchall():
        payload = json.loads(row[1])
        if payload.get(vocabulary.ACCEPTS_AUTOMATIC) is False:
            continue
        payload[vocabulary.ACCEPTS_AUTOMATIC] = False
        conn.execute("UPDATE kf_remote_knowledge_bases SET payload=? WHERE id=?", (json.dumps(payload, ensure_ascii=False, sort_keys=True), int(row[0])))
        changed += 1
    return changed


def read_state(conn: sqlite3.Connection, domain: str) -> ReadState | None:
    row = conn.execute(f"SELECT {_READ_COLUMNS} FROM kf_remote_kb_reads WHERE domain=?", (domain,)).fetchone()
    return None if row is None else ReadState(*tuple(row))


def _delete(conn: sqlite3.Connection, where: str, args: tuple[Any, ...]) -> int:
    """Delete the records of the condition and their memberships in local themes. Return the number of records."""
    ids = [int(row[0]) for row in conn.execute(f"SELECT id FROM kf_remote_knowledge_bases WHERE {where}", args).fetchall()]
    if ids:
        themes_store.delete_remote_kb_rows(conn, ids)
        conn.execute(f"DELETE FROM kf_remote_knowledge_bases WHERE {where}", args)
    return len(ids)


def purge_blocked(conn: sqlite3.Connection) -> int:
    """Delete the records of each domain that a domain block applies to, and the read state of that domain. Return the number of records. The code that makes a domain block calls this in its transaction; each judgement run and each lease call it again."""
    removed = 0
    domains = {str(row[0]) for row in conn.execute("SELECT domain FROM kf_remote_knowledge_bases UNION SELECT domain FROM kf_remote_kb_reads").fetchall()}
    for domain in sorted(domains):
        if federation_store.blocking_domain(conn, domain) is None:
            continue
        removed += _delete(conn, "domain=?", (domain,))
        conn.execute("DELETE FROM kf_remote_kb_reads WHERE domain=?", (domain,))
    return removed


def remove_domain(conn: sqlite3.Connection, domain: str) -> int:
    """Delete the records of one domain, their memberships in local themes, and the read state of the domain (the admin removes a known server). Return the number of records."""
    removed = _delete(conn, "domain=?", (domain,))
    conn.execute("DELETE FROM kf_remote_kb_reads WHERE domain=?", (domain,))
    return removed


def lease_reads(conn: sqlite3.Connection, *, now: float, limit: int, lease_seconds: float = READ_LEASE_SECONDS) -> list[str]:
    """Return the domains whose ``/kb`` collection is due for a read, and give a lease on each one.

    A known server is due when it has a good read of its server actor, no domain block applies to it, no lease is in effect, and it has no read or its last read or error is ``READ_INTERVAL_SECONDS`` old. A server with no read is first (the first contact, design Section 8.1 rule 2). The lease starts a new read: ``read_started_at`` is ``now``, and the counts of the read are zero.
    """
    purge_blocked(conn)
    candidates: list[tuple[float, str]] = []
    for server in known_servers.list_servers(conn):
        if server.last_read_at is None or server.server_actor_iri is None or federation_store.blocking_domain(conn, server.domain) is not None:
            continue
        state = read_state(conn, server.domain)
        if state is not None:
            if state.lease_until is not None and state.lease_until > now:
                continue
            if max(state.last_read_at or 0.0, state.last_error_at or 0.0) > now - READ_INTERVAL_SECONDS:
                continue
        candidates.append((0.0 if state is None else max(state.last_read_at or 0.0, state.last_error_at or 0.0), server.domain))
    due = [domain for _last, domain in sorted(candidates)[:limit]]
    for domain in due:
        conn.execute(
            "INSERT INTO kf_remote_kb_reads (domain, read_started_at, lease_until) VALUES (?, ?, ?) ON CONFLICT(domain) DO UPDATE SET read_started_at=excluded.read_started_at, lease_until=excluded.lease_until, stored_count=0, refused_count=0, refused_code=NULL",
            (domain, now, now + lease_seconds),
        )
    return due


def _open_read(conn: sqlite3.Connection, domain: str) -> ReadState | None:
    """The state of the read of ``domain`` that a lease started and that did not end, or ``None``. A blocked domain has no read."""
    state = read_state(conn, domain)
    if state is None or state.read_started_at is None or state.lease_until is None or federation_store.blocking_domain(conn, domain) is not None:
        return None
    return state


class _Refusal(Exception):
    """An item of a batch that this server does not store. ``code`` is the code of ``vocabulary.ContractError``, or a limit of this module: ``not_a_knowledge_base``, ``other_server``, ``label_too_long``, ``description_too_long``, ``iri_too_long``, ``signature_too_large``, ``record_too_large``, ``too_many_knowledge_bases``."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _check_item(item: Any, domain: str, origin: str) -> dict[str, Any]:
    """Return the item when it is a ``claire:KnowledgeBase`` of ``domain`` inside the limits. Raise ``_Refusal`` when it is not."""
    try:
        kind = vocabulary.validate_object(item, top_level=False)
    except vocabulary.ContractError as exc:
        raise _Refusal(exc.code) from None
    if kind != "knowledge_base":
        raise _Refusal("not_a_knowledge_base")
    # The origin server is the authority for its objects (design Section 6.4): the IRI and the page are on the server that was read.
    for key in ("id", "url"):
        if access.remote_server_domain(item[key], origin) != domain:
            raise _Refusal("other_server")
    if len(item["name"]) > LABEL_MAX:
        raise _Refusal("label_too_long")
    if len(item["summary"]) > DESCRIPTION_MAX:
        raise _Refusal("description_too_long")
    if any(len(item[key]) > IRI_MAX for key in ("id", "url", "inbox", "outbox", "followers", vocabulary.DOCUMENTS)):
        raise _Refusal("iri_too_long")
    signature = item.get(vocabulary.SIGNATURE)
    if signature is not None and any(len(row[0]) > SIGNATURE_TYPE_MAX or len(row[1]) > SIGNATURE_NAME_MAX for row in signature["entities"]):
        raise _Refusal("signature_too_large")
    record = _record(item)
    if len(json.dumps(record, ensure_ascii=False, sort_keys=True).encode("utf-8")) > RECORD_MAX_BYTES:
        raise _Refusal("record_too_large")
    return record


def _record(obj: dict[str, Any]) -> dict[str, Any]:
    """The keys of ``RECORD_KEYS`` of a checked ``claire:KnowledgeBase``: the record keeps no other key of another server (KS2)."""
    return {key: obj[key] for key in RECORD_KEYS if key in obj}


def _content(payload: str | None) -> dict[str, Any] | None:
    if payload is None:
        return None
    value = json.loads(payload)
    value.pop(vocabulary.ACCEPTS_AUTOMATIC, None)
    value.pop("updated", None)
    return value


def _updated(obj: dict[str, Any] | None) -> float | None:
    """The seconds of ``updated`` of a checked object or of a stored payload, or None when it has none."""
    if obj is None or "updated" not in obj:
        return None
    return vocabulary.time_seconds(obj["updated"])


def _stored_updated(kb: RemoteKnowledgeBase | None) -> float | None:
    return None if kb is None or kb.payload is None else _updated(json.loads(kb.payload))


def _store(conn: sqlite3.Connection, obj: dict[str, Any], domain: str, current: RemoteKnowledgeBase | None, *, now: float) -> None:
    """Insert or change the record of one checked ``claire:KnowledgeBase`` (the result of ``_check_item``). ``changed_at`` changes only when the content changes."""
    obj = _record(obj)
    payload = json.dumps(obj, ensure_ascii=False, sort_keys=True)
    signature = json.dumps(obj[vocabulary.SIGNATURE], ensure_ascii=False, sort_keys=True) if vocabulary.SIGNATURE in obj else None
    values = (domain, obj["url"], obj["name"], obj["summary"], obj[vocabulary.KNOWLEDGE_TYPE], int(obj[vocabulary.DOCUMENT_COUNT]), int(obj[vocabulary.ENTITY_COUNT]), int(obj[vocabulary.RELATION_COUNT]), signature, payload)
    if current is None:
        conn.execute(
            "INSERT INTO kf_remote_knowledge_bases (iri, domain, url, label, description, knowledge_type, document_count, entity_count, relation_count, signature, payload, first_seen_at, seen_at, changed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (obj["id"], *values, now, now, now),
        )
        return
    # ``claire:acceptsAutomatic`` is a state of the server, not of the content: its change is no reason for a new judgement run (``theme_judgement.run_is_due`` reads ``changed_at``).
    changed = now if _content(current.payload) != _content(payload) else current.changed_at
    conn.execute(
        "UPDATE kf_remote_knowledge_bases SET domain=?, url=?, label=?, description=?, knowledge_type=?, document_count=?, entity_count=?, relation_count=?, signature=?, payload=?, seen_at=?, changed_at=? WHERE id=?",
        (*values, now, changed, current.id),
    )
    if current.knowledge_type != obj[vocabulary.KNOWLEDGE_TYPE]:
        # A theme has one knowledge type: a record with a new type leaves its themes, as a local knowledge base does.
        themes_store.delete_remote_kb_rows(conn, [current.id])


def apply_update(conn: sqlite3.Connection, obj: Any, *, domain: str, origin: str, now: float) -> str | None:
    """Store the record of an ``Update`` that the knowledge base actor sent (design Section 9.3, unit KF3). Return ``None``, or the code of the refusal: the codes of an item of a read (``_check_item``), ``too_many_knowledge_bases`` for a new record of a server that has ``PER_SERVER_MAX`` records, or ``stale_update`` when ``updated`` of the object is not after ``updated`` of the stored record (an old ``Update`` that came late, or the same one again)."""
    try:
        checked = _check_item({key: value for key, value in obj.items() if key != "@context"} if isinstance(obj, dict) else obj, domain, origin)
    except _Refusal as exc:
        return exc.code
    current = get_by_iri(conn, checked["id"])
    if current is None and count_by_domain(conn).get(domain, 0) >= PER_SERVER_MAX:
        return "too_many_knowledge_bases"
    stored, received = _stored_updated(current), _updated(checked)
    if stored is not None and (received is None or received <= stored):
        return "stale_update"
    _store(conn, checked, domain, current, now=now)
    return None


def delete_record(conn: sqlite3.Connection, iri: str) -> bool:
    """Delete the record of one remote knowledge base and its memberships in local themes (a ``Delete`` of its actor, design Section 9.3). Copies of its documents stay (design Section 10.1 rule 3). Return False when there was no record."""
    return _delete(conn, "iri=?", (iri,)) > 0


def documents_url(kb: RemoteKnowledgeBase) -> str:
    """The address of the documents collection of a remote knowledge base (``claire:documents``), from the stored record. A record of ``list_all`` has no payload: ``ValueError``; the caller reads the record with ``get``."""
    if kb.payload is None:
        raise ValueError("the record has no payload: read it with get()")
    return str(json.loads(kb.payload)[vocabulary.DOCUMENTS])


def record_batch(conn: sqlite3.Connection, domain: str, *, items: list[Any], origin: str, now: float) -> BatchResult | None:
    """Store the items of one batch of the read of ``domain``. Return ``None`` when the domain has no read that a lease started.

    An item that is not valid, that is not on ``domain``, or that is over a limit is refused; the other items are stored. An item after ``PER_SERVER_MAX`` records of the read is refused with ``too_many_knowledge_bases``. ``changed_at`` changes only when the content of a record changes.
    """
    state = _open_read(conn, domain)
    if state is None:
        return None
    started = float(state.read_started_at or 0.0)
    seen = int(conn.execute("SELECT COUNT(*) FROM kf_remote_knowledge_bases WHERE domain=? AND seen_at>=?", (domain, started)).fetchone()[0])
    stored = refused = 0
    refused_code: str | None = None
    for item in items:
        try:
            obj = _check_item(item, domain, origin)
            current = get_by_iri(conn, obj["id"])
            if (current is None or current.seen_at < started) and seen >= PER_SERVER_MAX:
                raise _Refusal("too_many_knowledge_bases")
        except _Refusal as exc:
            refused += 1
            refused_code = refused_code or exc.code
            continue
        if current is None or current.seen_at < started:
            seen += 1
        stored_time, read_time = _stored_updated(current), _updated(obj)
        if current is not None and stored_time is not None and read_time is not None and read_time < stored_time:
            # An ``Update`` that is newer than this read came first: the stored record stays, and the read counts it as seen.
            conn.execute("UPDATE kf_remote_knowledge_bases SET seen_at=? WHERE id=?", (now, current.id))
        else:
            _store(conn, obj, domain, current, now=now)
        stored += 1
    conn.execute(
        "UPDATE kf_remote_kb_reads SET stored_count=stored_count+?, refused_count=refused_count+?, refused_code=COALESCE(refused_code, ?) WHERE domain=?",
        (stored, refused, refused_code, domain),
    )
    return BatchResult(stored, refused, refused_code)


def finish_read(conn: sqlite3.Connection, domain: str, *, now: float) -> int | None:
    """End a complete read of ``domain``: delete each record of the domain that the read did not contain, and remove the lease and the error state. Return the number of deleted records, or ``None`` when the domain has no read that a lease started."""
    state = _open_read(conn, domain)
    if state is None:
        return None
    removed = _delete(conn, "domain=? AND seen_at<?", (domain, float(state.read_started_at or 0.0)))
    conn.execute("UPDATE kf_remote_kb_reads SET read_started_at=NULL, lease_until=NULL, last_read_at=?, error_count=0, last_error=NULL, last_error_at=NULL WHERE domain=?", (now, domain))
    return removed


def record_read_error(conn: sqlite3.Connection, domain: str, *, error: str, now: float) -> bool:
    """Store a failed read: the error code, its time, and the number of failed reads in sequence. Return ``False`` when the domain has no read that a lease started. The records of the domain stay, also the ones that the failed read stored."""
    if _open_read(conn, domain) is None:
        return False
    conn.execute("UPDATE kf_remote_kb_reads SET read_started_at=NULL, lease_until=NULL, error_count=error_count+1, last_error=?, last_error_at=? WHERE domain=?", (error[:_MAX_ERROR_LENGTH], now, domain))
    return True


def view(kb: RemoteKnowledgeBase) -> dict[str, Any]:
    """The API form of one remote knowledge base. ``url`` is the page on its own server (the origin link, design Section 8.2 rule 3); ``id`` is the number of the record on this server and never leaves it."""
    return {
        "id": kb.id,
        "iri": kb.iri,
        "server": kb.domain,
        "url": kb.url,
        "label": kb.label,
        "description": kb.description,
        "knowledge_type": kb.knowledge_type,
        "documents": kb.document_count,
        "entities": kb.entity_count,
        "relations": kb.relation_count,
        "first_seen_at": kb.first_seen_at,
    }
