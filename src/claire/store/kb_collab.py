"""Store of the collaboration in shared knowledge bases (SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md Section 11.2, unit CT-1).

Only this module reads and writes the tables ``kb_memberships``, ``kb_contributions``, ``kb_contribution_revisions``, ``kb_submissions``, ``kb_submission_items``, ``kb_auto_rules``, ``kb_auto_rule_themes``, ``kb_contribution_blocks``, ``kb_mutual_collaborations``, ``kb_moderation_log``, and ``account_reputation`` (store boundary RD27 R2).

The functions do not commit: the caller sets the transaction boundary.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from typing import Any

from .. import ids
from . import db as dbm

ROLES = ("member", "collaborator", "moderator")
MEMBERSHIP_STATES = ("pending", "active", "banned")
CONTRIBUTION_ORIGINS = ("direct", "bulk", "auto", "curation", "fork", "migration")
CONTRIBUTION_STATES = ("pending", "merging", "visible", "hidden", "failed", "rejected", "removed")
KNOWLEDGE_TYPES = ("real", "fictional")


@dataclass(frozen=True, slots=True)
class KindViolation:
    """A row of ``knowledge_bases`` that is neither a personal nor a shared knowledge base (Section 11.2 table)."""

    kb_id: int
    reason: str


def kind_violations(conn: sqlite3.Connection) -> list[KindViolation]:
    """The rows that break the kinds. The store treats such a row as unavailable (404); ``/admin/kb`` shows it with a warning. No code gives it a meaning by a fallback (Section 12.2 step 3)."""
    out: list[KindViolation] = []
    for row in conn.execute(
        "SELECT id, is_personal, account_id, is_public, knowledge_type FROM knowledge_bases ORDER BY id"
    ).fetchall():
        reason = kind_violation_reason(row[1], row[2], row[3], row[4])
        if reason:
            out.append(KindViolation(int(row[0]), reason))
    return out


def kind_violation_reason(is_personal: Any, account_id: Any, is_public: Any, knowledge_type: Any) -> str:
    """Empty when the values are one of the two kinds; else the reason."""
    if int(is_personal or 0) == 1:
        if account_id is None:
            return "personal_without_account"
        if knowledge_type is not None:
            return "personal_with_knowledge_type"
        return ""
    if account_id is not None:
        return "shared_with_owner"
    if int(is_public or 0) != 1:
        return "shared_not_public"
    if knowledge_type not in KNOWLEDGE_TYPES:
        return "shared_without_knowledge_type"
    return ""


def _new_public_id(conn: sqlite3.Connection, table: str, column: str, insert: Any, *, now_ms: int | None = None) -> int:
    salt = dbm.read_id_salt(conn)
    clock = (lambda: now_ms) if now_ms is not None else None
    return ids.insert_with_timestamp_id(conn, table=table, column=column, salt=salt, insert=insert, now_ms=clock)


# --- memberships ---------------------------------------------------------------------------------


def add_membership(
    conn: sqlite3.Connection,
    *,
    kb_id: int,
    account_id: int,
    role: str,
    state: str,
    decided_by_account_id: int | None = None,
    now: float | None = None,
) -> int:
    """Insert one membership and return its public ID. A second membership of the same account in the same knowledge base raises ``sqlite3.IntegrityError``."""
    if role not in ROLES:
        raise ValueError(f"invalid role: {role!r}")
    if state not in MEMBERSHIP_STATES:
        raise ValueError(f"invalid membership state: {state!r}")
    ts = time.time() if now is None else now
    decided_at = ts if state != "pending" else None

    def _insert(number: int) -> None:
        conn.execute(
            "INSERT INTO kb_memberships (kb_id, account_id, public_id, role, state, requested_at, decided_at, decided_by_account_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (int(kb_id), int(account_id), number, role, state, ts, decided_at, decided_by_account_id),
        )

    return _new_public_id(conn, "kb_memberships", "public_id", _insert)


def get_membership(conn: sqlite3.Connection, kb_id: int, account_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT kb_id, account_id, public_id, role, state, requested_at, decided_at, decided_by_account_id FROM kb_memberships WHERE kb_id=? AND account_id=?",
        (int(kb_id), int(account_id)),
    ).fetchone()
    return None if row is None else dict(zip(("kb_id", "account_id", "public_id", "role", "state", "requested_at", "decided_at", "decided_by_account_id"), tuple(row)))


def list_memberships(conn: sqlite3.Connection, kb_id: int, *, state: str | None = None) -> list[dict[str, Any]]:
    sql = "SELECT kb_id, account_id, public_id, role, state, requested_at, decided_at, decided_by_account_id FROM kb_memberships WHERE kb_id=?"
    args: list[Any] = [int(kb_id)]
    if state is not None:
        sql += " AND state=?"
        args.append(state)
    sql += " ORDER BY requested_at, account_id"
    keys = ("kb_id", "account_id", "public_id", "role", "state", "requested_at", "decided_at", "decided_by_account_id")
    return [dict(zip(keys, tuple(r))) for r in conn.execute(sql, args).fetchall()]


# --- contributions -------------------------------------------------------------------------------


def add_contribution(
    conn: sqlite3.Connection,
    *,
    kb_id: int,
    document_id: str | None,
    origin: str,
    state: str,
    contributor_account_id: int | None,
    curator_account_id: int | None = None,
    source_kb_id: int | None = None,
    source_document_id: str | None = None,
    root_kb_id: int | None = None,
    root_document_id: str | None = None,
    submission_item_id: int | None = None,
    approved_at: float | None = None,
    approved_by_account_id: int | None = None,
    now: float | None = None,
) -> int:
    """Insert one contribution and return its public ID. One document has at most one contribution in a knowledge base (``UNIQUE (kb_id, document_id)``)."""
    if origin not in CONTRIBUTION_ORIGINS:
        raise ValueError(f"invalid contribution origin: {origin!r}")
    if state not in CONTRIBUTION_STATES:
        raise ValueError(f"invalid contribution state: {state!r}")
    ts = time.time() if now is None else now

    def _insert(number: int) -> None:
        conn.execute(
            "INSERT INTO kb_contributions (public_id, kb_id, document_id, origin, contributor_account_id, curator_account_id, source_kb_id, source_document_id, "
            "root_kb_id, root_document_id, submission_item_id, state, approved_at, approved_by_account_id, contributed_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                number, int(kb_id), document_id, origin, contributor_account_id, curator_account_id, source_kb_id, source_document_id,
                root_kb_id, root_document_id, submission_item_id, state, approved_at, approved_by_account_id, ts, ts,
            ),
        )

    return _new_public_id(conn, "kb_contributions", "public_id", _insert)


_CONTRIBUTION_KEYS = (
    "public_id", "kb_id", "document_id", "origin", "contributor_account_id", "curator_account_id", "source_kb_id", "source_document_id",
    "root_kb_id", "root_document_id", "submission_item_id", "state", "decision_reason", "approved_at", "approved_by_account_id",
    "updates_frozen", "revised_at", "decided_at", "contributed_at", "updated_at",
)


def list_contributions(conn: sqlite3.Connection, kb_id: int, *, state: str | None = None) -> list[dict[str, Any]]:
    sql = f"SELECT {', '.join(_CONTRIBUTION_KEYS)} FROM kb_contributions WHERE kb_id=?"
    args: list[Any] = [int(kb_id)]
    if state is not None:
        sql += " AND state=?"
        args.append(state)
    sql += " ORDER BY public_id"
    return [dict(zip(_CONTRIBUTION_KEYS, tuple(r))) for r in conn.execute(sql, args).fetchall()]


def get_contribution_for_document(conn: sqlite3.Connection, kb_id: int, document_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        f"SELECT {', '.join(_CONTRIBUTION_KEYS)} FROM kb_contributions WHERE kb_id=? AND document_id=?",
        (int(kb_id), document_id),
    ).fetchone()
    return None if row is None else dict(zip(_CONTRIBUTION_KEYS, tuple(row)))


# --- moderation log ------------------------------------------------------------------------------


def log_moderation(
    conn: sqlite3.Connection,
    *,
    kb_id: int,
    actor_account_id: int | None,
    action: str,
    target: str = "",
    reason: str = "",
    now: float | None = None,
) -> int:
    """Add one row to the moderation log. ``actor_account_id`` None is the server (automatic actions)."""
    ts = time.time() if now is None else now
    cur = conn.execute(
        "INSERT INTO kb_moderation_log (kb_id, actor_account_id, action, target, reason, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (int(kb_id), actor_account_id, action, target, reason, ts),
    )
    return int(cur.lastrowid)


def list_moderation_log(conn: sqlite3.Connection, kb_id: int) -> list[dict[str, Any]]:
    keys = ("id", "kb_id", "actor_account_id", "action", "target", "reason", "created_at")
    return [
        dict(zip(keys, tuple(r)))
        for r in conn.execute(
            "SELECT id, kb_id, actor_account_id, action, target, reason, created_at FROM kb_moderation_log WHERE kb_id=? ORDER BY id",
            (int(kb_id),),
        ).fetchall()
    ]
