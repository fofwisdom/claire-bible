"""Contributions across servers: the offers that go out and the offers that come in (KNOWLEDGE_FEDERATION_DESIGN.md Sections 9.3, 12.1, and 12.2; unit KF4).

An account of server A contributes a document of its personal knowledge base to a shared knowledge base of server B: the account actor of A sends ``Offer`` with a ``claire:Contribution`` (the ``Article`` with its ``claire:Extraction``) to the knowledge base actor of B. B makes a contribution (pending, or without approval when the offer has a grant in effect); the knowledge base actor of B answers with ``Accept`` (``result`` is the IRI of the contribution on B) or ``Reject`` (with the reason code).

This module owns two tables. No function here commits.

| Table | Rows |
| :--- | :--- |
| ``kf_outbound_offers`` | One row for each remote target of a contribution of an account of this server: an offer that went to the outbox, with the decision of the other server, or a target that was skipped (no ``offer_iri``; ``reason_code`` tells why). The submission views read it. |
| ``kf_inbound_offers`` | One row for each ``Offer`` that a knowledge base actor of this server got: the actor, the contribution that it made, the capability, and the answer of this server. |

An outbound row of an automatic contribution (unit KF5, design Section 12.3) has ``automatic`` = 1 and no submission item: the automatic rule of the account sent it (``auto_contribution.run_account``).

States of an outbound row: ``pending`` (the offer waits for the decision of the other server), ``accepted``, ``rejected``, ``removed`` (the other server removed the contribution after it accepted it; this server learns it only when it follows that knowledge base actor), ``skipped`` (no offer went out).

States of an inbound row: ``pending`` (the contribution waits for the reviewers, and no answer went out), ``accepted`` (the ``Accept`` is in the outbox), ``rejected`` (the ``Reject`` is in the outbox), ``closed`` (no answer goes out: the contribution or its knowledge base is gone without a decision).

``sync`` compares the inbound rows with the contributions and puts the answers in the outbox. ``outbox.sync`` calls it when the sidecar takes tasks, so no review route must send an answer.

Other servers fill ``kf_inbound_offers``. The handler of the inbox journal limits the rows of one domain in 24 hours (``contributions.LIMITS``), and an inbound row without a contribution (a ``Reject`` at once, or ``closed``) is deleted ``INBOUND_RETENTION_SECONDS`` after its answer. A row with a contribution stays while the contribution stays: the views read it.
"""

from __future__ import annotations

import hashlib
import secrets
import sqlite3
from typing import Any

from ...core import ids
from ...store import federation as federation_store
from ...store import kb_collab
from . import access, actors, collections, grants, known_servers, outbox, remote_kbs, vocabulary

__all__ = [
    "ACCOUNT_SIGNER_PREFIX",
    "INBOUND_RETENTION_SECONDS",
    "INBOUND_STATES",
    "OUTBOUND_STATES",
    "SKIP_REASONS",
    "TABLES",
    "account_signer",
    "actor_deleted",
    "answer_refusal",
    "automatic_of_account",
    "automatic_offered",
    "daily_offers_of_domain",
    "ensure_automatic_column",
    "ensure_schema",
    "inbound_of_contribution",
    "inbound_of_iri",
    "outbound_of_account",
    "outbound_of_item",
    "outbound_view",
    "pending_contributions_of_domain",
    "pending_outbound",
    "purge_account",
    "recent_accepted",
    "record_decision",
    "record_inbound",
    "result_removed",
    "send",
    "skip",
    "sync",
]

# The signer of an outbox task of an account actor: this prefix and the public ID of the account.
ACCOUNT_SIGNER_PREFIX = "account:"
OUTBOUND_STATES: tuple[str, ...] = ("pending", "accepted", "rejected", "removed", "skipped")
INBOUND_STATES: tuple[str, ...] = ("pending", "accepted", "rejected", "closed")
# The time that an inbound row without a contribution stays after its answer.
INBOUND_RETENTION_SECONDS = 30 * 86400.0
# The reasons that a remote target gets no offer.
SKIP_REASONS: tuple[str, ...] = ("not_a_federation_actor", "profile_private", "blocked_domain", "document_not_extracted", "document_too_large", "title_too_long", "extraction_too_large", "contribution_duplicate", "pending_limit", "knowledge_base_not_found", vocabulary.AUTOMATIC_NOT_ACCEPTED)
# The reasons of a skipped target that can change before the next run of the automatic rule: such a row does not keep the document from that knowledge base. A ``Reject`` with ``vocabulary.AUTOMATIC_NOT_ACCEPTED`` or ``vocabulary.LIMIT_REACHED`` is the same: the other server can accept automatic contributions later, and its pending limits can have room later.
RETRY_REASONS: tuple[str, ...] = ("not_a_federation_actor", "profile_private", "blocked_domain", "pending_limit", "knowledge_base_not_found", vocabulary.AUTOMATIC_NOT_ACCEPTED, vocabulary.LIMIT_REACHED)

SCHEMA = """
CREATE TABLE IF NOT EXISTS kf_outbound_offers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    offer_iri TEXT UNIQUE,
    account_id INTEGER,
    actor_iri TEXT,
    kb_iri TEXT NOT NULL,
    domain TEXT NOT NULL,
    source_kb_id INTEGER,
    source_document_id TEXT NOT NULL,
    submission_item_id INTEGER,
    capability_iri TEXT,
    automatic INTEGER NOT NULL DEFAULT 0,
    state TEXT NOT NULL,
    result_iri TEXT,
    reason_code TEXT,
    created_at REAL NOT NULL,
    decided_at REAL
);
CREATE INDEX IF NOT EXISTS idx_kf_outbound_offers_item ON kf_outbound_offers(submission_item_id);
CREATE INDEX IF NOT EXISTS idx_kf_outbound_offers_account ON kf_outbound_offers(account_id, kb_iri);
CREATE TABLE IF NOT EXISTS kf_inbound_offers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    offer_iri TEXT NOT NULL UNIQUE,
    actor_iri TEXT NOT NULL,
    domain TEXT NOT NULL,
    kb_number INTEGER NOT NULL,
    contribution_public_id INTEGER,
    capability_iri TEXT,
    grant_valid INTEGER NOT NULL DEFAULT 0,
    state TEXT NOT NULL,
    reason_code TEXT,
    detail TEXT,
    received_at REAL NOT NULL,
    answered_at REAL
);
CREATE INDEX IF NOT EXISTS idx_kf_inbound_offers_contribution ON kf_inbound_offers(contribution_public_id);
CREATE INDEX IF NOT EXISTS idx_kf_inbound_offers_domain ON kf_inbound_offers(domain, received_at)
"""

_OUTBOUND = "id, offer_iri, account_id, actor_iri, kb_iri, domain, source_kb_id, source_document_id, submission_item_id, capability_iri, automatic, state, result_iri, reason_code, created_at, decided_at"
_INBOUND = "id, offer_iri, actor_iri, domain, kb_number, contribution_public_id, capability_iri, grant_valid, state, reason_code, detail, received_at, answered_at"
# The columns that the code of this module reads, for each table. The migration (``store.db``) gives a table of a different form a new name and makes this one again.
TABLES: dict[str, tuple[str, ...]] = {"kf_outbound_offers": tuple(_OUTBOUND.split(", ")), "kf_inbound_offers": tuple(_INBOUND.split(", "))}


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Make the tables and the indexes when they are not there. Idempotent; the migration of ``store.db`` calls it at each start."""
    for statement in SCHEMA.split(";"):
        if statement.strip():
            conn.execute(statement)


def ensure_automatic_column(conn: sqlite3.Connection) -> None:
    """Add the column ``automatic`` (unit KF5) to a ``kf_outbound_offers`` of schema version 32 or 33. Idempotent; the migration of ``store.db`` calls it at each start, before the step v32 compares the columns. A row of an earlier version gets 0: before unit KF5 each offer came from a submission."""
    columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(kf_outbound_offers)").fetchall()}
    if columns and "automatic" not in columns:
        conn.execute("ALTER TABLE kf_outbound_offers ADD COLUMN automatic INTEGER NOT NULL DEFAULT 0")


def _outbound(row: sqlite3.Row | tuple) -> dict[str, Any]:
    return dict(zip(TABLES["kf_outbound_offers"], tuple(row)))


def _inbound(row: sqlite3.Row | tuple) -> dict[str, Any]:
    return dict(zip(TABLES["kf_inbound_offers"], tuple(row)))


def account_signer(public_id: int) -> str:
    """The signer of an outbox task that the account actor signs (``outbox.enqueue``)."""
    return f"{ACCOUNT_SIGNER_PREFIX}{ids.format_public_id(int(public_id))}"


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


# --- offers that go out (design Section 12.1 rules 1 and 2) -----------------------------------------------


def skip(conn: sqlite3.Connection, *, account_id: int, kb_iri: str, domain: str, source_kb_id: int, source_document_id: str, submission_item_id: int | None, automatic: bool, reason: str, now: float) -> None:
    """Record a remote target that gets no offer, with the reason (the submission view, or the list of automatic contributions, shows it)."""
    if reason not in SKIP_REASONS:
        raise ValueError(f"unknown skip reason: {reason!r}")
    conn.execute(
        "INSERT INTO kf_outbound_offers (account_id, kb_iri, domain, source_kb_id, source_document_id, submission_item_id, automatic, state, reason_code, created_at, decided_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'skipped', ?, ?, ?)",
        (int(account_id), kb_iri, domain, int(source_kb_id), source_document_id, submission_item_id, 1 if automatic else 0, reason, now, now),
    )


def pending_outbound(conn: sqlite3.Connection, *, account_id: int, kb_iri: str) -> int:
    """The offers of one account to one knowledge base that wait for a decision (the pending limit on the sender, shared knowledge base design Section 9.2 rule 5)."""
    return int(conn.execute("SELECT COUNT(*) FROM kf_outbound_offers WHERE account_id=? AND kb_iri=? AND state='pending'", (int(account_id), kb_iri)).fetchone()[0])


def send(conn: sqlite3.Connection, *, account_id: int, source_kb_id: int, source_document_id: str, remote_kb: remote_kbs.RemoteKnowledgeBase, submission_item_id: int | None, pending_limit: int, automatic: bool, origin: str, now: float) -> dict[str, Any]:
    """Offer one document of the personal knowledge base of the account to a shared knowledge base of another server: the ``Offer`` goes to the outbox with the account actor as the signer, and the row is ``pending``. A target that cannot get the offer is recorded as ``skipped`` with the reason. Return the row. No commit.

    The checks of the sender, in this sequence: a domain block (``blocked_domain``); the account is a federation actor (``not_a_federation_actor``: an account that is not ``active`` or has no USERNAME has no actor that can sign, design Section 12.1 rule 2); the content of the account is public (``profile_private``: an offer sends a document of the account to another server with its name, and the content of an account with a private profile is private, FEDERATION_GO_LIVE_DESIGN.md decision GD5); an offer of this document to this knowledge base that waits or was accepted (``contribution_duplicate``); the pending limit of the account (``pending_limit``); the document has an extraction (``document_not_extracted``); the limits of one document (KS2: the other server has the same limits). The offer carries the grant of the account for the knowledge base when it has one in its term (design Section 12.2 rule 2); the other server decides if the grant is in effect.
    ``automatic`` (unit KF5, design Section 12.3): the automatic rule of the account sends the offer. The ``claire:Contribution`` has ``claire:automatic``, and a target whose record says that its server accepts no automatic contributions is skipped first (``automatic_not_accepted``).
    """
    from ...collaboration import contributions  # the import is here: ``contributions`` loads this module for a submission

    def _skipped(reason: str) -> dict[str, Any]:
        skip(conn, account_id=account_id, kb_iri=remote_kb.iri, domain=remote_kb.domain, source_kb_id=source_kb_id, source_document_id=source_document_id, submission_item_id=submission_item_id, automatic=automatic, reason=reason, now=now)
        return _outbound(conn.execute(f"SELECT {_OUTBOUND} FROM kf_outbound_offers WHERE id=last_insert_rowid()").fetchone())

    if access.remote_server_domain(remote_kb.iri, origin) is None or federation_store.blocking_domain(conn, remote_kb.domain) is not None:
        return _skipped("blocked_domain")
    if automatic and remote_kbs.accepts_automatic(remote_kb) is False:
        return _skipped(vocabulary.AUTOMATIC_NOT_ACCEPTED)
    actor_iri = actors.account_actor_iri(conn, account_id, origin)
    if actor_iri is None:
        return _skipped("not_a_federation_actor")
    if not actors.account_content_public(conn, account_id):
        return _skipped("profile_private")
    if conn.execute("SELECT 1 FROM kf_outbound_offers WHERE account_id=? AND kb_iri=? AND source_document_id=? AND state IN ('pending', 'accepted') LIMIT 1", (int(account_id), remote_kb.iri, source_document_id)).fetchone() is not None:
        return _skipped("contribution_duplicate")
    capability = grants.capability_for(conn, account_id=account_id, kb_iri=remote_kb.iri, now=now)
    if capability is None and pending_outbound(conn, account_id=account_id, kb_iri=remote_kb.iri) >= pending_limit:
        return _skipped("pending_limit")
    article = actors.personal_article(conn, kb_id=source_kb_id, document_id=source_document_id, actor_iri=actor_iri, origin=origin)
    if article is None:
        return _skipped("document_not_extracted")
    refusal = contributions.remote_article_refusal(article)
    if refusal is not None:
        return _skipped(refusal)
    offer_iri = f"{actor_iri}#offers/{secrets.token_hex(12)}"
    contribution = vocabulary.build_contribution(document=article, attributed_to=actor_iri, knowledge_base=remote_kb.iri, contribution_id=None, automatic=automatic)
    offer = vocabulary.build_offer(activity_id=offer_iri, contribution=contribution, capability=capability)
    public_id = int(conn.execute("SELECT public_id FROM accounts WHERE id=?", (int(account_id),)).fetchone()[0])
    outbox.enqueue(conn, offer, signer=account_signer(public_id), recipients=[remote_kb.iri], origin=origin, now=now)
    # The server of the target is a known server (design Section 6.2 rule 1).
    known_servers.note_server(conn, remote_kb.domain, source="local_contribution", now=now)
    cursor = conn.execute(
        "INSERT INTO kf_outbound_offers (offer_iri, account_id, actor_iri, kb_iri, domain, source_kb_id, source_document_id, submission_item_id, capability_iri, automatic, state, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)",
        (offer_iri, int(account_id), actor_iri, remote_kb.iri, remote_kb.domain, int(source_kb_id), source_document_id, submission_item_id, capability, 1 if automatic else 0, now),
    )
    return _outbound(conn.execute(f"SELECT {_OUTBOUND} FROM kf_outbound_offers WHERE id=?", (int(cursor.lastrowid),)).fetchone())


def record_decision(conn: sqlite3.Connection, *, offer_iri: str, kb_iri: str, accepted: bool, result_iri: str | None, reason_code: str | None, now: float) -> dict[str, Any] | None:
    """The knowledge base actor ``kb_iri`` answered an offer of this server (``Accept`` with ``result``, or ``Reject`` with the reason code). Return the row after the answer, or None when this server has no such offer to that actor. An offer that has a decision does not change: the same answer again, or a second answer, changes nothing."""
    row = conn.execute("SELECT id, state FROM kf_outbound_offers WHERE offer_iri=? AND kb_iri=?", (offer_iri, kb_iri)).fetchone()
    if row is None:
        return None
    if str(row[1]) == "pending":
        conn.execute("UPDATE kf_outbound_offers SET state=?, result_iri=?, reason_code=?, decided_at=? WHERE id=?", ("accepted" if accepted else "rejected", result_iri, reason_code, now, int(row[0])))
    return _outbound(conn.execute(f"SELECT {_OUTBOUND} FROM kf_outbound_offers WHERE id=?", (int(row[0]),)).fetchone())


def result_removed(conn: sqlite3.Connection, *, kb_iri: str, result_iri: str, reason_code: str, now: float) -> int:
    """The knowledge base actor removed a document (``Delete``, design Section 10.3): an offer of this server that it accepted with that ``result`` gets the state ``removed`` with the reason code. Return the number of offers."""
    return conn.execute("UPDATE kf_outbound_offers SET state='removed', reason_code=?, decided_at=? WHERE kb_iri=? AND result_iri=? AND state='accepted'", (reason_code, now, kb_iri, result_iri)).rowcount


def outbound_of_item(conn: sqlite3.Connection, submission_item_id: int) -> list[dict[str, Any]]:
    return [_outbound(row) for row in conn.execute(f"SELECT {_OUTBOUND} FROM kf_outbound_offers WHERE submission_item_id=? ORDER BY id", (int(submission_item_id),)).fetchall()]


def outbound_of_account(conn: sqlite3.Connection, account_id: int) -> list[dict[str, Any]]:
    return [_outbound(row) for row in conn.execute(f"SELECT {_OUTBOUND} FROM kf_outbound_offers WHERE account_id=? ORDER BY id DESC", (int(account_id),)).fetchall()]


def automatic_of_account(conn: sqlite3.Connection, account_id: int) -> list[dict[str, Any]]:
    """The rows of the automatic contributions of one account to other servers, the newest first, in each state (the list of automatic contributions in ``/settings/kb``)."""
    return [_outbound(row) for row in conn.execute(f"SELECT {_OUTBOUND} FROM kf_outbound_offers WHERE account_id=? AND automatic=1 ORDER BY id DESC", (int(account_id),)).fetchall()]


def automatic_offered(conn: sqlite3.Connection, *, account_id: int, kb_iri: str) -> set[str]:
    """The documents of the account that the automatic rule does not send again to the knowledge base ``kb_iri``: each document with a row for it in any state (offered, accepted, rejected, removed, or skipped for a reason of the document), also from a submission. A row with a reason of ``RETRY_REASONS`` does not count (design Section 9.9 rule 2: "a rejected or removed document never goes again to the same K")."""
    marks = ", ".join("?" for _ in RETRY_REASONS)
    return {str(row[0]) for row in conn.execute(
        f"SELECT DISTINCT source_document_id FROM kf_outbound_offers WHERE account_id=? AND kb_iri=? AND NOT (state IN ('skipped', 'rejected') AND COALESCE(reason_code, '') IN ({marks}))",
        (int(account_id), kb_iri, *RETRY_REASONS),
    ).fetchall()}


def recent_accepted(conn: sqlite3.Connection, *, limit: int) -> list[dict[str, Any]]:
    """The newest decisions of other servers that approved offers of accounts of this server, for the recent activity of the root page (design Section 8.3 rule 3): one row for each pair of account and knowledge base, with the time of the newest approval, the newest first. A row has ``account_id`` (None after a purge with records kept), ``kb_iri``, ``domain``, and ``at``. A domain that a block applies to now is not in the result."""
    rows = conn.execute(
        "SELECT account_id, kb_iri, domain, MAX(decided_at) AS at FROM kf_outbound_offers WHERE state='accepted' AND decided_at IS NOT NULL GROUP BY account_id, kb_iri ORDER BY at DESC, kb_iri LIMIT ?",
        (int(limit) * 2,),
    ).fetchall()
    out = [{"account_id": None if row[0] is None else int(row[0]), "kb_iri": str(row[1]), "domain": str(row[2]), "at": float(row[3])} for row in rows]
    return [row for row in out if federation_store.blocking_domain(conn, row["domain"]) is None][: int(limit)]


def outbound_view(conn: sqlite3.Connection, row: dict[str, Any]) -> dict[str, Any]:
    """One remote target for the submission views: the knowledge base with its server and its origin link, the state, the reason code, the ``result`` of an ``Accept``, and the state of the delivery of the ``Offer`` (``pending``, ``delivered``, ``failed``; None for a target without an offer)."""
    record = remote_kbs.get_by_iri(conn, str(row["kb_iri"]))
    delivery = None
    if row["offer_iri"]:
        task = conn.execute("SELECT status FROM kf_outbox WHERE activity_iri=? ORDER BY id LIMIT 1", (row["offer_iri"],)).fetchone()
        # A task that ended is deleted after its retention time: the offer was delivered or failed then, and its state tells the rest.
        delivery = None if task is None else ("delivered" if task[0] == "delivered" else "pending" if task[0] == "pending" else "failed")
    return {
        "knowledge_base": None,
        "label": None if record is None else record.label,
        "remote": True,
        "remote_id": None if record is None else record.id,
        "server": row["domain"],
        "url": None if record is None else record.url,
        "actor": row["kb_iri"],
        "state": row["state"],
        "approved": row["state"] == "accepted",
        "reason": row["reason_code"],
        "result": row["result_iri"],
        "with_grant": bool(row["capability_iri"]),
        "automatic": bool(row["automatic"]),
        "delivery": delivery,
        "decided_at": row["decided_at"],
    }


# --- offers that come in (design Section 12.1 rules 3 to 5) -----------------------------------------------


def inbound_of_iri(conn: sqlite3.Connection, offer_iri: str) -> dict[str, Any] | None:
    row = conn.execute(f"SELECT {_INBOUND} FROM kf_inbound_offers WHERE offer_iri=?", (offer_iri,)).fetchone()
    return None if row is None else _inbound(row)


def inbound_of_contribution(conn: sqlite3.Connection, contribution_public_id: int) -> dict[str, Any] | None:
    row = conn.execute(f"SELECT {_INBOUND} FROM kf_inbound_offers WHERE contribution_public_id=? ORDER BY id DESC LIMIT 1", (int(contribution_public_id),)).fetchone()
    return None if row is None else _inbound(row)


def _expire_inbound(conn: sqlite3.Connection, now: float) -> None:
    conn.execute("DELETE FROM kf_inbound_offers WHERE state IN ('rejected', 'closed') AND (contribution_public_id IS NULL OR state='closed') AND answered_at<?", (now - INBOUND_RETENTION_SECONDS,))


def record_inbound(conn: sqlite3.Connection, *, offer_iri: str, actor_iri: str, domain: str, kb_number: int, contribution_public_id: int | None, capability_iri: str | None, grant_valid: bool, now: float) -> None:
    """Store one ``Offer`` that made a contribution (state ``pending``: the answer goes out when the contribution has a decision, ``sync``)."""
    _expire_inbound(conn, now)
    conn.execute(
        "INSERT INTO kf_inbound_offers (offer_iri, actor_iri, domain, kb_number, contribution_public_id, capability_iri, grant_valid, state, received_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)",
        (offer_iri, actor_iri, domain, int(kb_number), contribution_public_id, capability_iri, 1 if grant_valid else 0, now),
    )


def answer_refusal(conn: sqlite3.Connection, *, offer_iri: str, actor_iri: str, domain: str, kb_number: int, capability_iri: str | None, reason_code: str, detail: str, origin: str, now: float) -> None:
    """This server makes no contribution from an ``Offer`` for a reason that is a decision about it (a duplicate, a tombstone, a limit, an archived knowledge base): the row is ``rejected`` and the ``Reject`` with the reason code goes to the outbox, so that the submission page of the sender shows it. ``detail`` is the code of this server; the wire has only the reason codes of the vocabulary."""
    _expire_inbound(conn, now)
    conn.execute(
        "INSERT INTO kf_inbound_offers (offer_iri, actor_iri, domain, kb_number, capability_iri, state, reason_code, detail, received_at, answered_at) VALUES (?, ?, ?, ?, ?, 'rejected', ?, ?, ?, ?)",
        (offer_iri, actor_iri, domain, int(kb_number), capability_iri, reason_code, detail, now, now),
    )
    _enqueue_reject(conn, kb_number=kb_number, offer_iri=offer_iri, actor_iri=actor_iri, reason_code=reason_code, origin=origin, now=now)


def _enqueue_reject(conn: sqlite3.Connection, *, kb_number: int, offer_iri: str, actor_iri: str, reason_code: str, origin: str, now: float) -> None:
    kb_iri = collections.knowledge_base_iris(origin, int(kb_number))["id"]
    reject = vocabulary.build_reject_offer(activity_id=f"{kb_iri}#rejects/{_digest(offer_iri)}", knowledge_base=kb_iri, offer=offer_iri, offer_actor=actor_iri, reason_code=reason_code)
    _enqueue_answer(conn, reject, kb_number=kb_number, actor_iri=actor_iri, origin=origin, now=now)


def _enqueue_answer(conn: sqlite3.Connection, activity: dict[str, Any], *, kb_number: int, actor_iri: str, origin: str, now: float) -> None:
    # An actor whose address is not one of another server now (for example after a change of the origin of this server) gets no answer.
    if access.remote_server_domain(actor_iri, origin) is not None:
        outbox.enqueue(conn, activity, signer=str(int(kb_number)), recipients=[actor_iri], origin=origin, now=now)


def _domains_of_quota(conn: sqlite3.Connection, quota: str, where: str, args: tuple[Any, ...]) -> list[str]:
    """The domains of the rows of ``where`` whose quota key (``access.quota_domain``) is ``quota``: the subdomains of one registrable domain share one limit."""
    return [str(row[0]) for row in conn.execute(f"SELECT DISTINCT domain FROM kf_inbound_offers WHERE {where}", args).fetchall() if access.quota_domain(str(row[0])) == quota]


def pending_contributions_of_domain(conn: sqlite3.Connection, quota: str) -> list[int]:
    """The contributions that offers of one registrable domain (``access.quota_domain``) made and that have no answer (the limit for each domain, KS3). The caller counts those that are ``pending`` now."""
    out: list[int] = []
    for domain in _domains_of_quota(conn, quota, "state='pending' AND contribution_public_id IS NOT NULL", ()):
        out += [int(row[0]) for row in conn.execute("SELECT contribution_public_id FROM kf_inbound_offers WHERE domain=? AND state='pending' AND contribution_public_id IS NOT NULL", (domain,)).fetchall()]
    return out


def daily_offers_of_domain(conn: sqlite3.Connection, quota: str, *, now: float) -> int:
    """The offers of one registrable domain (``access.quota_domain``) in the last 24 hours, in each state (the limit for each domain and each day, KS2)."""
    since = now - 86400.0
    return sum(int(conn.execute("SELECT COUNT(*) FROM kf_inbound_offers WHERE domain=? AND received_at>?", (domain, since)).fetchone()[0]) for domain in _domains_of_quota(conn, quota, "received_at>?", (since,)))


def sync(conn: sqlite3.Connection, *, origin: str, now: float) -> int:
    """Put the answer of each inbound offer whose contribution has a decision in the outbox (design Section 12.1 rule 3). Return the number of answers. Idempotent: an activity IRI has no random part.

    | Contribution | Answer |
    | :--- | :--- |
    | Approved (by a reviewer, or by a grant in effect when the offer came) | ``Accept`` with ``result`` = the IRI of the document of the contribution on this server |
    | ``rejected``, or ``removed`` before an approval | ``Reject`` with the reason code of the decision (``other`` for a code that the vocabulary does not have) |
    | Gone (the pending contributions of an actor that was deleted), or its knowledge base is gone | None: the row is ``closed`` |
    """
    answers = 0
    for row in [_inbound(found) for found in conn.execute(f"SELECT {_INBOUND} FROM kf_inbound_offers WHERE state='pending' ORDER BY id").fetchall()]:
        contribution = None if row["contribution_public_id"] is None else kb_collab.get_contribution(conn, int(row["contribution_public_id"]))
        kb = actors.shared_kb(conn, int(row["kb_number"]))
        if contribution is None or kb is None:
            conn.execute("UPDATE kf_inbound_offers SET state='closed', answered_at=? WHERE id=?", (now, row["id"]))
            continue
        kb_iri = collections.knowledge_base_iris(origin, int(row["kb_number"]))["id"]
        if contribution["approved_at"] is not None:
            result = actors.document_iri(origin, int(row["kb_number"]), int(contribution["public_id"]))
            accept = vocabulary.build_accept_offer(activity_id=f"{kb_iri}#accepts/{_digest(row['offer_iri'])}", knowledge_base=kb_iri, offer=row["offer_iri"], offer_actor=row["actor_iri"], result=result)
            _enqueue_answer(conn, accept, kb_number=int(row["kb_number"]), actor_iri=row["actor_iri"], origin=origin, now=now)
            conn.execute("UPDATE kf_inbound_offers SET state='accepted', answered_at=? WHERE id=?", (now, row["id"]))
        elif contribution["state"] in ("rejected", "removed"):
            reason = contribution["decision_reason"] if contribution["decision_reason"] in vocabulary.REASON_CODES else "other"
            _enqueue_reject(conn, kb_number=int(row["kb_number"]), offer_iri=row["offer_iri"], actor_iri=row["actor_iri"], reason_code=reason, origin=origin, now=now)
            conn.execute("UPDATE kf_inbound_offers SET state='rejected', reason_code=?, answered_at=? WHERE id=?", (reason, now, row["id"]))
        else:
            continue
        answers += 1
    return answers


def actor_deleted(conn: sqlite3.Connection, actor_iri: str, *, now: float) -> int:
    """The account of another server is gone (``Delete`` of its actor): its offers without an answer are ``closed``, because the actor has no inbox. Return the number of rows."""
    return conn.execute("UPDATE kf_inbound_offers SET state='closed', answered_at=? WHERE actor_iri=? AND state='pending'", (now, actor_iri)).rowcount


def purge_account(conn: sqlite3.Connection, account_id: int, *, delete_activity_records: bool) -> None:
    """The purge of an account of this server: its offers stay as records without the account, or are deleted with the option of the purge confirmation (as its submissions, shared knowledge base design Section 13.2)."""
    if delete_activity_records:
        conn.execute("DELETE FROM kf_outbound_offers WHERE account_id=?", (int(account_id),))
    else:
        conn.execute("UPDATE kf_outbound_offers SET account_id=NULL WHERE account_id=?", (int(account_id),))
