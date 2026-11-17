"""Outbound activities of the federation of shared knowledge (KNOWLEDGE_FEDERATION_DESIGN.md Sections 9.2, 9.3, 10.2, 10.3, and 11; unit KF3).

Control goes in one direction: the sidecar calls Claire (FEDERATION.md Section 2). Thus Claire puts each activity that must go to another server in the table ``kf_outbox``; the sidecar takes the tasks with a lease, signs each one with the key of its actor, delivers it to the inbox of its recipient, and acknowledges it. This is the pattern of the publish outbox (FEDERATION.md Section 5.8) with one difference: the task has the activity JSON of ``vocabulary`` as it must go out, because Fedify does not keep the wire form (``docs/implementation/KNOWLEDGE_FEDERATION.md`` Section 7.2).

This module owns the tables ``kf_outbox`` (one row for each activity and recipient) and ``kf_follows`` (the knowledge base actors of other servers that the server actor of this server follows). No function here commits.

Who sends what:

| Activity | Signer | Recipient | When |
| :--- | :--- | :--- | :--- |
| ``Follow`` | The server actor | A knowledge base actor of another server | This server holds a copy of one of its documents, or has it in an active local theme (design Section 9.2 rule 2) |
| ``Undo`` of the ``Follow`` | The server actor | That actor | Neither of the two conditions is true now |
| ``Accept`` of a ``Follow`` | The knowledge base actor | The server actor that follows | The handler of ``Follow`` (``handlers``) |
| ``Update`` of the record | The knowledge base actor | Each follower | The label, the description, or the knowledge type changed |
| ``Delete`` of the record | The knowledge base actor | Each follower | The shared knowledge base is gone |
| ``Update`` of a document | The knowledge base actor | Each follower | A revision replaced the copy of a visible contribution (design Section 10.2) |
| ``Delete`` of a document | The knowledge base actor | Each follower | The contribution got the state ``removed`` (design Section 10.3) |

``sync`` compares the state of ``claire.db`` with what was sent and adds the tasks. It runs when the sidecar asks for tasks, so no route and no job of another module must call it. An activity IRI has no random part, except the ``Follow`` (a new follow after an ``Undo`` is a new activity); the pair of activity IRI and recipient is unique, so a second ``sync`` adds no second task.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
from dataclasses import dataclass
from typing import Any

from ..store import federation as federation_store
from ..store import kb_collab
from ..store import themes as themes_store
from . import access, actors, collections, known_servers, remote_kbs, vocabulary

__all__ = [
    "LEASE_SECONDS",
    "MAX_ATTEMPTS",
    "RESULTS",
    "RETENTION_SECONDS",
    "SERVER_SIGNER",
    "STATUSES",
    "TABLES",
    "OutboxTask",
    "ack",
    "count_activities",
    "enqueue",
    "ensure_schema",
    "follow_of",
    "follow_states",
    "lease",
    "sync",
]

# The signer of a task: the server actor, or the number of a knowledge base actor as a decimal text.
SERVER_SIGNER = "server"
STATUSES: tuple[str, ...] = ("pending", "delivered", "failed", "refused")
# The results that the sidecar gives: ``delivered`` (the inbox answered 2xx), ``retry`` (no answer, or an answer that can change: the task comes again later), ``rejected`` (the delivery is not possible: the task ends as ``failed``).
RESULTS: tuple[str, ...] = ("delivered", "retry", "rejected")
LEASE_SECONDS = 300.0
MAX_ATTEMPTS = 8
_RETRY_BASE_SECONDS = 60.0
_RETRY_MAX_SECONDS = 6 * 3600.0
# A task that ended stays this long (the same time as a processed row of the inbox journal).
RETENTION_SECONDS = 30 * 86400.0

SCHEMA = """
CREATE TABLE IF NOT EXISTS kf_outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    activity_iri TEXT NOT NULL,
    kind TEXT NOT NULL,
    signer TEXT NOT NULL,
    recipient TEXT NOT NULL,
    recipient_domain TEXT NOT NULL,
    payload TEXT NOT NULL,
    status TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    error_code TEXT,
    lease_until REAL,
    next_attempt_at REAL NOT NULL,
    created_at REAL NOT NULL,
    finished_at REAL,
    UNIQUE (activity_iri, recipient)
);
CREATE INDEX IF NOT EXISTS idx_kf_outbox_ready ON kf_outbox(status, next_attempt_at);
CREATE TABLE IF NOT EXISTS kf_follows (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kb_iri TEXT NOT NULL UNIQUE,
    domain TEXT NOT NULL,
    follow_iri TEXT NOT NULL,
    state TEXT NOT NULL,
    created_at REAL NOT NULL,
    accepted_at REAL
)
"""

_TASK_COLUMNS = "id, activity_iri, kind, signer, recipient, recipient_domain, payload, status, attempts, error_code, lease_until, next_attempt_at, created_at, finished_at"
_FOLLOW_COLUMNS = "id, kb_iri, domain, follow_iri, state, created_at, accepted_at"
# The columns that the code of this module reads, for each table. The migration (``store.db``) gives a table of a different form a new name and makes this one again.
TABLES: dict[str, tuple[str, ...]] = {"kf_outbox": tuple(_TASK_COLUMNS.split(", ")), "kf_follows": tuple(_FOLLOW_COLUMNS.split(", "))}


@dataclass(frozen=True, slots=True)
class OutboxTask:
    id: int
    activity_iri: str
    kind: str
    signer: str
    recipient: str
    recipient_domain: str
    payload: str
    status: str
    attempts: int
    error_code: str | None
    lease_until: float | None
    next_attempt_at: float
    created_at: float
    finished_at: float | None


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Make the tables and the index when they are not there. Idempotent; the migration of ``store.db`` calls it at each start."""
    for statement in SCHEMA.split(";"):
        if statement.strip():
            conn.execute(statement)


def _task(row: sqlite3.Row | tuple) -> OutboxTask:
    return OutboxTask(*tuple(row))


def get_task(conn: sqlite3.Connection, task_id: int) -> OutboxTask | None:
    row = conn.execute(f"SELECT {_TASK_COLUMNS} FROM kf_outbox WHERE id=?", (int(task_id),)).fetchone()
    return None if row is None else _task(row)


def enqueue(conn: sqlite3.Connection, activity: dict[str, Any], *, signer: str, recipients: list[str], origin: str, now: float) -> int:
    """Add one task for each recipient of a built activity. Return the number of new tasks. A pair of activity IRI and recipient that has a task gets no second one. A recipient that is not an address of another server is an error of the caller: ``ValueError``."""
    kind = vocabulary.validate_activity(activity)
    payload = json.dumps(activity, ensure_ascii=False, separators=(",", ":"))
    added = 0
    for recipient in recipients:
        domain = access.remote_server_domain(recipient, origin)
        if domain is None:
            raise ValueError(f"the recipient is not an actor of another server: {recipient!r}")
        added += conn.execute(
            "INSERT INTO kf_outbox (activity_iri, kind, signer, recipient, recipient_domain, payload, status, next_attempt_at, created_at) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?) ON CONFLICT(activity_iri, recipient) DO NOTHING",
            (activity["id"], kind, signer, recipient, domain, payload, now, now),
        ).rowcount
    return added


def count_activities(conn: sqlite3.Connection, signer: str) -> int:
    """The number of activities of one signer that the table keeps."""
    return int(conn.execute("SELECT COUNT(DISTINCT activity_iri) FROM kf_outbox WHERE signer=?", (signer,)).fetchone()[0])


def has_open_task(conn: sqlite3.Connection, signer: str) -> bool:
    """True when a task of the signer waits for its delivery. The keys of a knowledge base actor that is gone are given only for such a task."""
    return conn.execute("SELECT 1 FROM kf_outbox WHERE signer=? AND status='pending' LIMIT 1", (signer,)).fetchone() is not None


def lease(conn: sqlite3.Connection, *, origin: str, now: float, limit: int) -> list[OutboxTask]:
    """Return the oldest tasks that are ready, and give a lease on each one. ``sync`` runs first. A task for a blocked domain ends as ``refused`` (``blocked_domain``): there is no exchange with that domain (design Section 6.3). The caller makes sure that the switch ``federation`` is on."""
    sync(conn, origin=origin, now=now)
    conn.execute("DELETE FROM kf_outbox WHERE status!='pending' AND finished_at < ?", (now - RETENTION_SECONDS,))
    tasks: list[OutboxTask] = []
    rows = conn.execute(f"SELECT {_TASK_COLUMNS} FROM kf_outbox WHERE status='pending' AND next_attempt_at<=? AND (lease_until IS NULL OR lease_until<=?) ORDER BY id", (now, now)).fetchall()
    for row in rows:
        task = _task(row)
        if federation_store.blocking_domain(conn, task.recipient_domain) is not None:
            conn.execute("UPDATE kf_outbox SET status='refused', error_code='blocked_domain', finished_at=? WHERE id=?", (now, task.id))
            continue
        if len(tasks) >= limit:
            break
        conn.execute("UPDATE kf_outbox SET lease_until=? WHERE id=?", (now + LEASE_SECONDS, task.id))
        tasks.append(get_task(conn, task.id) or task)
    return tasks


def ack(conn: sqlite3.Connection, task_id: int, *, result: str, error_code: str | None, now: float) -> OutboxTask | None:
    """Store the result of one delivery. ``retry`` gives the task again after a wait that grows with each attempt; after ``MAX_ATTEMPTS`` attempts the task ends as ``failed``. A task that ended does not change. Return the task, or None when there is no such task."""
    if result not in RESULTS:
        raise ValueError(f"unknown result: {result!r}")
    task = get_task(conn, task_id)
    if task is None or task.status != "pending":
        return task
    attempts = task.attempts + 1
    if result == "delivered":
        conn.execute("UPDATE kf_outbox SET status='delivered', attempts=?, error_code=NULL, lease_until=NULL, finished_at=? WHERE id=?", (attempts, now, task.id))
    elif result == "rejected" or attempts >= MAX_ATTEMPTS:
        conn.execute("UPDATE kf_outbox SET status='failed', attempts=?, error_code=?, lease_until=NULL, finished_at=? WHERE id=?", (attempts, error_code, now, task.id))
    else:
        wait = min(_RETRY_BASE_SECONDS * 2 ** (attempts - 1), _RETRY_MAX_SECONDS)
        conn.execute("UPDATE kf_outbox SET attempts=?, error_code=?, lease_until=NULL, next_attempt_at=? WHERE id=?", (attempts, error_code, now + wait, task.id))
    return get_task(conn, task.id)


# --- follows of this server ------------------------------------------------------------------------


def follow_of(conn: sqlite3.Connection, kb_iri: str) -> dict[str, Any] | None:
    row = conn.execute(f"SELECT {_FOLLOW_COLUMNS} FROM kf_follows WHERE kb_iri=?", (kb_iri,)).fetchone()
    return None if row is None else dict(zip(TABLES["kf_follows"], tuple(row)))


def follow_states(conn: sqlite3.Connection) -> dict[str, str]:
    """The state of the follow of each knowledge base actor that this server follows: ``{actor IRI: "pending" | "accepted"}``."""
    return {str(row[0]): str(row[1]) for row in conn.execute("SELECT kb_iri, state FROM kf_follows").fetchall()}


def accept_follow(conn: sqlite3.Connection, kb_iri: str, *, follow_iri: str, now: float) -> bool:
    """The knowledge base actor accepted the ``Follow`` with that IRI. False when this server has no such follow."""
    return conn.execute("UPDATE kf_follows SET state='accepted', accepted_at=COALESCE(accepted_at, ?) WHERE kb_iri=? AND follow_iri=?", (now, kb_iri, follow_iri)).rowcount > 0


def drop_follow(conn: sqlite3.Connection, kb_iri: str) -> bool:
    """Delete the follow of an actor without an ``Undo`` (the actor is gone, or its domain is blocked)."""
    return conn.execute("DELETE FROM kf_follows WHERE kb_iri=?", (kb_iri,)).rowcount > 0


def _server_actor(origin: str) -> str:
    return f"{origin}{access.SERVER_ACTOR_PATH}"


def _follow_activity(row: dict[str, Any], origin: str) -> dict[str, Any]:
    return vocabulary.build_follow(activity_id=row["follow_iri"], server_actor=_server_actor(origin), knowledge_base=row["kb_iri"])


def _wanted_follows(conn: sqlite3.Connection) -> dict[str, str]:
    """The knowledge base actors that this server must follow, with the source of the known server: a remote knowledge base that is a member of an active local theme (``local_follow``), and one that this server holds a copy from (``local_curation``)."""
    wanted: dict[str, str] = {}
    active = {int(theme["id"]) for theme in themes_store.list_themes(conn) if theme["state"] == "active"}
    for member in themes_store.remote_members(conn):
        if int(member["theme_id"]) not in active:
            continue
        record = remote_kbs.get(conn, int(member["remote_kb_id"]))
        if record is not None:
            wanted[record.iri] = "local_follow"
    for iri in kb_collab.remote_source_kbs_with_copies(conn):
        wanted[iri] = "local_curation"
    return wanted


def _sync_follows(conn: sqlite3.Connection, *, origin: str, now: float) -> None:
    wanted = _wanted_follows(conn)
    current = {str(row["kb_iri"]): row for row in (dict(zip(TABLES["kf_follows"], tuple(found))) for found in conn.execute(f"SELECT {_FOLLOW_COLUMNS} FROM kf_follows").fetchall())}
    for iri, row in current.items():
        if federation_store.blocking_domain(conn, row["domain"]) is not None:
            # No exchange with a blocked domain: the follow ends on this server, and no ``Undo`` goes out. The revisions stop; the copies stay (design Section 10.1 rule 3).
            drop_follow(conn, iri)
        elif iri not in wanted:
            follow = _follow_activity(row, origin)
            enqueue(conn, vocabulary.build_undo_follow(activity_id=f"{row['follow_iri']}/undo", follow=follow), signer=SERVER_SIGNER, recipients=[iri], origin=origin, now=now)
            drop_follow(conn, iri)
    for iri, source in sorted(wanted.items()):
        domain = access.remote_server_domain(iri, origin)
        if iri in current or domain is None or federation_store.blocking_domain(conn, domain) is not None:
            continue
        row = {"kb_iri": iri, "follow_iri": f"{_server_actor(origin)}#follows/{secrets.token_hex(12)}"}
        conn.execute("INSERT INTO kf_follows (kb_iri, domain, follow_iri, state, created_at) VALUES (?, ?, ?, 'pending', ?)", (iri, domain, row["follow_iri"], now))
        known_servers.note_server(conn, domain, source=source, now=now)
        enqueue(conn, _follow_activity(row, origin), signer=SERVER_SIGNER, recipients=[iri], origin=origin, now=now)


def _sync_actor(conn: sqlite3.Connection, number: int, *, origin: str, now: float) -> None:
    """Add the tasks of one knowledge base actor of this server that has followers."""
    all_followers = actors.followers(conn, number)
    recipients = [row["actor_iri"] for row in actors.followers(conn, number, deliverable_only=True)]
    iris = collections.knowledge_base_iris(origin, number)
    signer = str(number)
    kb = actors.shared_kb(conn, number)
    if kb is None:
        # The shared knowledge base is gone: the followers get the ``Delete`` of its record, and the actor has no followers after that.
        if recipients:
            enqueue(conn, vocabulary.build_delete_knowledge_base(activity_id=f"{iris['id']}#delete", knowledge_base=iris["id"], followers=iris["followers"]), signer=signer, recipients=recipients, origin=origin, now=now)
        actors.drop_actor(conn, number)
        return
    obj = actors.knowledge_base_object(conn, number, origin=origin)
    assert obj is not None
    digest = actors.record_digest(obj)
    state = conn.execute("SELECT record_digest FROM kf_kb_actor_state WHERE kb_number=?", (int(number),)).fetchone()
    if state is None or str(state[0]) != digest:
        if state is not None and recipients:
            enqueue(conn, vocabulary.build_update_knowledge_base(activity_id=f"{iris['id']}#updates/{int(now * 1000)}", knowledge_base=obj), signer=signer, recipients=recipients, origin=origin, now=now)
        conn.execute("INSERT INTO kf_kb_actor_state (kb_number, record_digest, updated_at) VALUES (?, ?, ?) ON CONFLICT(kb_number) DO UPDATE SET record_digest=excluded.record_digest, updated_at=excluded.updated_at", (int(number), digest, now))
    since = min(float(row["created_at"]) for row in all_followers)
    for contribution in kb_collab.changed_contributions(conn, int(kb["id"]), since=since):
        public_id = int(contribution["public_id"])
        sent = conn.execute("SELECT revised_at, removed FROM kf_document_announcements WHERE contribution_public_id=?", (public_id,)).fetchone()
        iri = actors.document_iri(origin, number, public_id)
        if contribution["state"] == "removed":
            if sent is not None and sent["removed"]:
                continue
            reason = contribution["decision_reason"] if contribution["decision_reason"] in vocabulary.REASON_CODES else "other"
            if recipients:
                enqueue(conn, vocabulary.build_delete_document(activity_id=f"{iri}#delete", knowledge_base=iris["id"], followers=iris["followers"], document=iri, reason_code=reason), signer=signer, recipients=recipients, origin=origin, now=now)
            conn.execute("INSERT INTO kf_document_announcements (contribution_public_id, removed) VALUES (?, 1) ON CONFLICT(contribution_public_id) DO UPDATE SET removed=1", (public_id,))
            continue
        revised_at = float(contribution["revised_at"])
        if sent is not None and sent["revised_at"] is not None and float(sent["revised_at"]) >= revised_at:
            continue
        document = actors.document_object(conn, kb, contribution, origin=origin)
        if document is None:
            continue  # the document does not leave the server now (hidden, or no extraction); a later revision is examined again
        if recipients:
            enqueue(conn, vocabulary.build_update_document(activity_id=f"{iri}#updates/{int(revised_at * 1000)}", knowledge_base=iris["id"], followers=iris["followers"], document=document), signer=signer, recipients=recipients, origin=origin, now=now)
        conn.execute("INSERT INTO kf_document_announcements (contribution_public_id, revised_at) VALUES (?, ?) ON CONFLICT(contribution_public_id) DO UPDATE SET revised_at=excluded.revised_at", (public_id, revised_at))


def sync(conn: sqlite3.Connection, *, origin: str, now: float) -> None:
    """Add the tasks that the state of ``claire.db`` needs: the follows of this server, and for each knowledge base actor with followers the changes of its record and of its documents. Idempotent."""
    _sync_follows(conn, origin=origin, now=now)
    for number in actors.followed_numbers(conn):
        _sync_actor(conn, number, origin=origin, now=now)


def accept_activity_iri(kb_iri: str, follow_iri: str) -> str:
    """The IRI of the ``Accept`` of one ``Follow``: the same ``Follow`` gives the same IRI."""
    return f"{kb_iri}#accepts/{hashlib.sha256(follow_iri.encode('utf-8')).hexdigest()[:32]}"
