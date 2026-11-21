"""Inbox journal of the federation of shared knowledge (KNOWLEDGE_FEDERATION_DESIGN.md Section 13, KS9; unit KF1).

Each activity of the knowledge federation that the sidecar hands to Claire gets one row in the table ``kf_inbox_journal`` of ``claire.db``, before Claire processes it. This module owns that table.

Why ``claire.db`` (design Section 13 rule 4 left the place to this unit): the admin starts a replay in Claire, and Claire does not call the sidecar; and the data that a handler changes is in ``claire.db``, so the state of the row and the effect of the handler are in one transaction.

States of a row:

| State | Meaning |
| :--- | :--- |
| ``received`` | The row is stored and the processing did not end (for example the process stopped). The next delivery of the same activity, or a replay, processes it. |
| ``processed`` | A handler applied the activity. The row never changes again. |
| ``failed`` | The processing failed; ``error_code`` tells why. ``no_handler``: no handler is registered for the kind. ``handler_error``: the handler raised an error. The admin can process the row again. |
| ``refused`` | Claire refused the activity; ``error_code`` is the code of ``vocabulary.ContractError``, the reason of a handler, or the control that stopped it at a replay. A replay is not possible. |

Rules:

- The activity IRI is the key. A second delivery of a ``processed``, ``failed``, or ``refused`` activity changes nothing (``duplicate``).
- An activity gets a row only when the switch ``federation`` is on, the domain of the signer is not blocked, the activity IRI is on the origin (scheme, host, and port) of the signer, the actor is the signer or the signer is the server actor of the origin of the actor, and the registrable domain of the signer is inside its quota of 24 hours (rows and bytes, ``contributions.LIMITS``). Thus a blocked server, a server that names the IRI of another server, and an actor that names another actor of its server cannot write rows.
- Unlike the other store modules, ``receive`` and ``process`` commit: the row must be in the database before the processing starts.
- ``process`` takes the write lock of the database (``BEGIN IMMEDIATE``) before it reads the state of the row: two deliveries of the same activity at the same time do not both run the handler.
- A handler runs in a savepoint. An error of the handler removes its changes and keeps the row.
- A ``refused`` row keeps no payload (``{}``): it cannot be processed again, and a refused activity of another server takes no space.
- A row does not make its server a known server: the handler that accepts the activity does that (design Section 6.2 rule 2).

Later units register one handler for each activity kind with ``register_handler``. A kind without a handler gets ``failed`` with ``no_handler``; after the unit with the handler is installed, a replay processes the row.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from . import access, known_servers, vocabulary

__all__ = [
    "COLUMNS",
    "FAILED_RETENTION_SECONDS",
    "HANDLERS",
    "PROCESSED_RETENTION_SECONDS",
    "QUOTA_COLUMNS",
    "REPLAY_STATES",
    "SCHEMA",
    "STATES",
    "TABLE",
    "VERIFIED_BY",
    "HandlerContext",
    "HandlerRefusal",
    "JournalEntry",
    "JournalError",
    "Receipt",
    "ensure_schema",
    "drop_payloads_of_domain",
    "ensure_quota_columns",
    "entry_view",
    "get_entry",
    "list_entries",
    "process",
    "prune",
    "quota_use",
    "receive",
    "register_handler",
    "replay",
]

log = logging.getLogger("claire.knowledge_federation.journal")

STATES: tuple[str, ...] = ("received", "processed", "failed", "refused")
# The states that a replay accepts.
REPLAY_STATES: tuple[str, ...] = ("failed", "received")
# The result of the signature check of the sidecar: the mechanism that verified the activity (the values of the inbox event contract, FEDERATION.md Section 5.7). An activity without a valid signature does not come to Claire.
VERIFIED_BY: tuple[str, ...] = ("http-signature", "ld-signature", "object-integrity-proof")
# Retention (design Section 13 rule 4): 30 days for processed and refused rows, 90 days for failed rows and rows whose processing did not end.
PROCESSED_RETENTION_SECONDS = 30 * 86400.0
FAILED_RETENTION_SECONDS = 90 * 86400.0
_MAX_IRI_LENGTH = 2048
_QUOTA_SECONDS = 86400.0
# The payload of a ``refused`` row.
_NO_PAYLOAD = "{}"
_MAX_DETAIL_LENGTH = 500

SCHEMA = """
CREATE TABLE IF NOT EXISTS kf_inbox_journal (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    activity_iri TEXT NOT NULL UNIQUE,
    activity_type TEXT NOT NULL,
    kind TEXT,
    actor TEXT NOT NULL,
    actor_domain TEXT NOT NULL,
    recipient TEXT,
    verified_by TEXT NOT NULL,
    payload TEXT NOT NULL,
    status TEXT NOT NULL,
    error_code TEXT,
    error_detail TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    received_at REAL NOT NULL,
    processed_at REAL,
    quota_domain TEXT,
    payload_bytes INTEGER
);
CREATE INDEX IF NOT EXISTS idx_kf_inbox_journal_status ON kf_inbox_journal(status, received_at)
"""
# The columns of the quota for each registrable domain (schema version 33): the key of the quota (``access.quota_domain`` of the signer) and the size of the activity as it came. ``ensure_quota_columns`` adds them to a table of version 29 to 32; a row of that time has NULL and does not count.
QUOTA_COLUMNS: tuple[tuple[str, str], ...] = (("quota_domain", "TEXT"), ("payload_bytes", "INTEGER"))

_COLUMNS = "id, activity_iri, activity_type, kind, actor, actor_domain, recipient, verified_by, payload, status, error_code, error_detail, attempts, received_at, processed_at"
# The columns that the code of this module reads. The migration (``store.db``) gives a table of a different form a new name and makes this one again.
TABLE = "kf_inbox_journal"
COLUMNS: tuple[str, ...] = tuple(_COLUMNS.split(", "))


@dataclass(frozen=True, slots=True)
class JournalEntry:
    id: int
    activity_iri: str
    activity_type: str
    kind: str | None
    actor: str
    actor_domain: str
    recipient: str | None
    verified_by: str
    payload: str
    status: str
    error_code: str | None
    error_detail: str | None
    attempts: int
    received_at: float
    processed_at: float | None


@dataclass(frozen=True, slots=True)
class Receipt:
    """The answer for one delivery. ``decision`` is ``rejected`` (no row was made; ``reason`` tells why), ``duplicate`` (the row exists and did not change), or the state of the row after the processing: ``processed``, ``failed``, ``refused``."""

    decision: str
    reason: str | None
    entry: JournalEntry | None


@dataclass(frozen=True, slots=True)
class HandlerContext:
    """What a handler gets: the connection of ``claire.db`` (inside a savepoint; the handler must not commit), the canonical origin of this server, and the time."""

    conn: sqlite3.Connection
    origin: str
    now: float


class HandlerRefusal(Exception):
    """A handler refuses the activity for a reason of this server (for example: the knowledge base does not exist, a grant is not in effect). ``code`` is a short code of ``a-z``, ``0-9``, and ``_``. The row gets the state ``refused``."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class JournalError(Exception):
    """A replay is not possible. ``code`` is ``not_found``, ``not_replayable``, or ``federation_off`` (the switch is off: the row does not change, and the admin can process it again after the switch is on)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


Handler = Callable[[HandlerContext, JournalEntry, dict[str, Any]], None]
# The dispatch table: activity kind (``vocabulary.ACTIVITY_KINDS``) to handler. Unit KF1 registers no handler.
HANDLERS: dict[str, Handler] = {}


def register_handler(kind: str, handler: Handler) -> None:
    """Register the handler of one activity kind. A kind has one handler: a second registration is an error.

    The handler gets the context, the journal row, and the validated activity. It returns nothing when it applied the activity. It raises ``HandlerRefusal`` to refuse the activity. Any other error gives the state ``failed`` and removes the changes of the handler. The handler must give the same result when the same activity comes again after a failure.
    """
    if kind not in vocabulary.ACTIVITY_KINDS:
        raise ValueError(f"unknown activity kind: {kind!r}")
    if kind in HANDLERS:
        raise ValueError(f"the activity kind {kind!r} has a handler")
    HANDLERS[kind] = handler


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Make the table and its index when they are not there. Idempotent; the migration of ``store.db`` calls it at each start."""
    for statement in SCHEMA.split(";"):
        if statement.strip():
            conn.execute(statement)


def ensure_quota_columns(conn: sqlite3.Connection) -> None:
    """Add the columns of ``QUOTA_COLUMNS`` when they are not there, and their index. Idempotent; the migration of ``store.db`` (step v33) calls it at each start. No row changes."""
    present = {str(row[1]) for row in conn.execute("PRAGMA table_info(kf_inbox_journal)")}
    if not present:
        return  # the table is not there: the step v29 makes it
    for name, kind in QUOTA_COLUMNS:
        if name not in present:
            conn.execute(f"ALTER TABLE kf_inbox_journal ADD COLUMN {name} {kind}")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_kf_inbox_journal_quota ON kf_inbox_journal(quota_domain, received_at)")


def quota_use(conn: sqlite3.Connection, quota: str, *, now: float) -> tuple[int, int]:
    """The rows and the bytes of the activities of one quota key in the last 24 hours."""
    row = conn.execute("SELECT COUNT(*), COALESCE(SUM(payload_bytes), 0) FROM kf_inbox_journal WHERE quota_domain=? AND received_at>?", (quota, now - _QUOTA_SECONDS)).fetchone()
    return int(row[0]), int(row[1])


def _entry(row: sqlite3.Row | tuple) -> JournalEntry:
    return JournalEntry(*tuple(row))


def get_entry(conn: sqlite3.Connection, entry_id: int) -> JournalEntry | None:
    row = conn.execute(f"SELECT {_COLUMNS} FROM kf_inbox_journal WHERE id=?", (int(entry_id),)).fetchone()
    return None if row is None else _entry(row)


def _entry_by_iri(conn: sqlite3.Connection, activity_iri: str) -> JournalEntry | None:
    row = conn.execute(f"SELECT {_COLUMNS} FROM kf_inbox_journal WHERE activity_iri=?", (activity_iri,)).fetchone()
    return None if row is None else _entry(row)


def list_entries(conn: sqlite3.Connection, *, status: str | None = None, limit: int, max_id: int | None = None) -> list[JournalEntry]:
    """Return the rows, newest first. ``status`` selects one state; ``max_id`` gives the rows with a smaller ID (the cursor of the next page)."""
    if status is not None and status not in STATES:
        raise ValueError(f"unknown journal state: {status!r}")
    sql = f"SELECT {_COLUMNS} FROM kf_inbox_journal WHERE 1=1"
    args: list[Any] = []
    if status is not None:
        sql += " AND status=?"
        args.append(status)
    if max_id is not None:
        sql += " AND id<?"
        args.append(int(max_id))
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(int(limit))
    return [_entry(row) for row in conn.execute(sql, args).fetchall()]


def _iso(value: float | None) -> str | None:
    return None if value is None else datetime.fromtimestamp(value, tz=timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def entry_view(entry: JournalEntry, *, with_payload: bool = False) -> dict[str, Any]:
    """Return the API form of one row. The times are ISO 8601 texts in UTC. ``payload`` (the activity as it came) is in the result only for the detail of one row."""
    view: dict[str, Any] = {
        "id": entry.id,
        "activity_iri": entry.activity_iri,
        "activity_type": entry.activity_type,
        "kind": entry.kind,
        "actor": entry.actor,
        "actor_domain": entry.actor_domain,
        "recipient": entry.recipient,
        "verified_by": entry.verified_by,
        "status": entry.status,
        "error_code": entry.error_code,
        "error_detail": entry.error_detail,
        "attempts": entry.attempts,
        "received_at": _iso(entry.received_at),
        "processed_at": _iso(entry.processed_at),
        "replayable": entry.status in REPLAY_STATES,
    }
    if with_payload:
        view["payload"] = json.loads(entry.payload)
    return view


def prune(conn: sqlite3.Connection, *, now: float) -> int:
    """Delete the rows that are older than their retention. Return the number of deleted rows. The caller commits."""
    deleted = conn.execute("DELETE FROM kf_inbox_journal WHERE status IN ('processed', 'refused') AND received_at < ?", (now - PROCESSED_RETENTION_SECONDS,)).rowcount
    deleted += conn.execute("DELETE FROM kf_inbox_journal WHERE status IN ('failed', 'received') AND received_at < ?", (now - FAILED_RETENTION_SECONDS,)).rowcount
    return deleted


def drop_payloads_of_domain(conn: sqlite3.Connection, domain: str) -> int:
    """Remove the payload of each ``processed`` or ``refused`` row of one domain (the admin removes a known server). A ``failed`` or ``received`` row keeps its payload: the admin can process it again. The rows stay until their retention ends. Return the number of rows that lost a payload. The caller commits."""
    return conn.execute("UPDATE kf_inbox_journal SET payload=? WHERE actor_domain=? AND status IN ('processed', 'refused') AND payload!=?", (_NO_PAYLOAD, domain, _NO_PAYLOAD)).rowcount


def _rejected(reason: str) -> Receipt:
    return Receipt("rejected", reason, None)


def receive(conn: sqlite3.Connection, *, activity: Any, signer: str, verified_by: str, recipient: str | None, origin: str, now: float) -> Receipt:
    """Store one activity that the sidecar verified, then process it. This function commits.

    ``signer`` is the IRI of the actor whose signature the sidecar verified; ``verified_by`` is the mechanism (``VERIFIED_BY``); ``recipient`` is the IRI of the inbox owner (a knowledge base actor or an account actor), or ``None`` for the shared inbox.

    No row is made (``rejected``) for these reasons, in this sequence: ``federation_off``, ``invalid_actor``, ``blocked_domain`` (the controls of ``access.exchange_refusal`` for the signer), ``invalid_activity`` (the activity is not a JSON object with an ``id`` that is an ``http`` or ``https`` IRI), ``signer_mismatch`` (the ``id`` is not on the origin of the signer, or the ``actor`` is not the signer and the signer is not the server actor of the origin of the actor), ``domain_quota`` (the registrable domain of the signer has ``remote_domain_daily_activities`` rows, or the activity would take its bytes over ``remote_domain_daily_bytes``, in the last 24 hours).
    """
    from ...collaboration import contributions  # the import is here: the limits are admin settings of ``contributions``

    if verified_by not in VERIFIED_BY:
        raise ValueError(f"unknown signature mechanism: {verified_by!r}")
    refusal = access.exchange_refusal(conn, actor_iri=signer, origin=origin)
    if refusal is not None:
        return _rejected(refusal)
    signer_domain = access.remote_server_domain(signer, origin)
    signer_origin = access.iri_origin(signer)
    assert signer_domain is not None
    if not isinstance(activity, dict):
        return _rejected("invalid_activity")
    activity_iri = activity.get("id")
    if not isinstance(activity_iri, str) or len(activity_iri) > _MAX_IRI_LENGTH or access.remote_server_domain(activity_iri, origin) is None:
        return _rejected("invalid_activity")
    # The origin is the scheme, the host, and the port: another port of the same host is another server (KS1).
    if signer_origin is None or access.iri_origin(activity_iri) != signer_origin:
        return _rejected("signer_mismatch")
    actor = activity.get("actor")
    # An activity without a text ``actor`` gets a row: the validator then records ``missing_field`` or ``invalid_iri``. The row has the signer as its actor.
    # The handlers trust the ``actor`` of the row: it is the signer, or an actor of the server whose server actor signed (that server answers for its actors).
    if isinstance(actor, str) and actor != signer and not (signer == access.server_actor_of(signer) and access.iri_origin(actor) == signer_origin):
        return _rejected("signer_mismatch")

    existing = _entry_by_iri(conn, activity_iri)
    if existing is not None:
        if existing.status != "received":
            return Receipt("duplicate", existing.status, existing)
        entry, ran = _process(conn, existing.id, origin=origin, now=now, states=("received",))
        return Receipt(entry.status, entry.error_code, entry) if ran else Receipt("duplicate", entry.status, entry)

    payload = json.dumps(activity, ensure_ascii=False, separators=(",", ":"))
    size = len(payload.encode("utf-8"))
    quota = access.quota_domain(signer_domain)
    limits = contributions.read_limits(conn)
    rows, used = quota_use(conn, quota, now=now)
    if rows >= limits["remote_domain_daily_activities"] or used + size > limits["remote_domain_daily_bytes"]:
        log.warning("knowledge federation journal: the quota of 24 hours of a domain is full")
        return _rejected("domain_quota")
    prune(conn, now=now)
    raw_type = activity.get("type")
    # Two deliveries of the same activity at the same time: the second insert does nothing, and that delivery is a duplicate.
    cursor = conn.execute(
        "INSERT INTO kf_inbox_journal (activity_iri, activity_type, actor, actor_domain, recipient, verified_by, payload, status, received_at, quota_domain, payload_bytes) VALUES (?, ?, ?, ?, ?, ?, ?, 'received', ?, ?, ?) ON CONFLICT(activity_iri) DO NOTHING",
        (activity_iri, raw_type if isinstance(raw_type, str) else "", actor if isinstance(actor, str) else signer, signer_domain, recipient, verified_by, payload, now, quota, size),
    )
    # The row is in the database before the processing starts (design Section 13 rule 1).
    conn.commit()
    if cursor.rowcount == 0:
        existing = _entry_by_iri(conn, activity_iri)
        assert existing is not None
        return Receipt("duplicate", existing.status, existing)
    entry, ran = _process(conn, int(cursor.lastrowid), origin=origin, now=now, states=("received",))
    return Receipt(entry.status, entry.error_code, entry) if ran else Receipt("duplicate", entry.status, entry)


def _finish(conn: sqlite3.Connection, entry_id: int, *, status: str, kind: str | None, error_code: str | None, error_detail: str | None, now: float) -> JournalEntry:
    conn.execute(
        "UPDATE kf_inbox_journal SET status=?, kind=?, error_code=?, error_detail=?, attempts=attempts+1, processed_at=? WHERE id=?",
        (status, kind, error_code, None if error_detail is None else error_detail[:_MAX_DETAIL_LENGTH], now if status == "processed" else None, entry_id),
    )
    if status == "refused":
        # A refused activity cannot be processed again: its payload is not kept (KS2).
        conn.execute("UPDATE kf_inbox_journal SET payload=? WHERE id=?", (_NO_PAYLOAD, entry_id))
    conn.commit()
    entry = get_entry(conn, entry_id)
    assert entry is not None
    return entry


def process(conn: sqlite3.Connection, entry_id: int, *, origin: str, now: float) -> JournalEntry:
    """Process one row and record the result. This function commits. A ``processed`` or ``refused`` row does not change (design Section 13 rule 2).

    Sequence: the controls of ``access.exchange_refusal`` for the actor (``refused`` with the reason); ``vocabulary.validate_activity`` (``refused`` with the code and the field of ``ContractError``); the time of the last contact of a known server; the handler of the kind (``failed`` with ``no_handler`` when there is none).
    """
    return _process(conn, entry_id, origin=origin, now=now, states=REPLAY_STATES)[0]


def _process(conn: sqlite3.Connection, entry_id: int, *, origin: str, now: float, states: tuple[str, ...]) -> tuple[JournalEntry, bool]:
    """Process one row when its state is in ``states``. Return the row and True when this call processed it. The write lock is taken before the state is read, so that only one caller processes a row; a second caller waits for the lock, then finds the new state."""
    if conn.in_transaction:
        conn.commit()
    conn.execute("BEGIN IMMEDIATE")
    entry = get_entry(conn, entry_id)
    if entry is None:
        conn.rollback()
        raise JournalError("not_found")
    if entry.status not in states:
        conn.rollback()
        return entry, False
    refusal = access.exchange_refusal(conn, actor_iri=entry.actor, origin=origin)
    if refusal is not None:
        return _finish(conn, entry.id, status="refused", kind=entry.kind, error_code=refusal, error_detail=None, now=now), True
    activity = json.loads(entry.payload)
    try:
        kind = vocabulary.validate_activity(activity)
    except vocabulary.ContractError as exc:
        return _finish(conn, entry.id, status="refused", kind=None, error_code=exc.code, error_detail=exc.path, now=now), True

    # A contact changes only the time of a known server. The handler that accepts the activity makes a new known server (design Section 6.2 rule 2).
    known_servers.touch_contact(conn, entry.actor_domain, now=now)

    handler = HANDLERS.get(kind)
    if handler is None:
        return _finish(conn, entry.id, status="failed", kind=kind, error_code="no_handler", error_detail=None, now=now), True
    conn.execute("SAVEPOINT kf_journal_handler")
    try:
        handler(HandlerContext(conn=conn, origin=origin, now=now), entry, activity)
    except HandlerRefusal as exc:
        conn.execute("ROLLBACK TO SAVEPOINT kf_journal_handler")
        conn.execute("RELEASE SAVEPOINT kf_journal_handler")
        return _finish(conn, entry.id, status="refused", kind=kind, error_code=exc.code, error_detail=exc.detail or None, now=now), True
    except Exception as exc:  # noqa: BLE001 - each error of a handler is recorded in the row; the admin can process the row again.
        conn.execute("ROLLBACK TO SAVEPOINT kf_journal_handler")
        conn.execute("RELEASE SAVEPOINT kf_journal_handler")
        log.exception("knowledge federation journal: the handler of %s failed for row %d", kind, entry.id)
        return _finish(conn, entry.id, status="failed", kind=kind, error_code="handler_error", error_detail=f"{type(exc).__name__}: {exc}", now=now), True
    conn.execute("RELEASE SAVEPOINT kf_journal_handler")
    return _finish(conn, entry.id, status="processed", kind=kind, error_code=None, error_detail=None, now=now), True


def replay(conn: sqlite3.Connection, entry_id: int, *, origin: str, now: float) -> JournalEntry:
    """Process a failed row again (design Section 13 rule 3). Raise ``JournalError``: ``not_found`` for an unknown row, ``not_replayable`` for a row whose state is not in ``REPLAY_STATES``, ``federation_off`` while the switch ``federation`` is off.

    The switch is a temporary condition, so it does not change the row: without this check the processing would record ``refused`` with ``federation_off``, and the row could not be processed again. A domain block does change the row to ``refused``: the admin decided to stop the exchange with that server.
    """
    entry = get_entry(conn, entry_id)
    if entry is None:
        raise JournalError("not_found")
    if entry.status not in REPLAY_STATES:
        raise JournalError("not_replayable")
    if not access.federation_on(conn):
        raise JournalError("federation_off")
    return process(conn, entry.id, origin=origin, now=now)
