"""Store of the collaboration in shared knowledge bases (SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md Section 11.2, unit CT-1).

Only this module reads and writes the tables ``kb_memberships``, ``kb_contributions``, ``kb_contribution_revisions``, ``kb_submissions``, ``kb_submission_items``, ``kb_auto_rules``, ``kb_auto_rule_themes``, ``kb_contribution_blocks``, ``kb_mutual_collaborations``, ``kb_moderation_log``, and ``account_reputation`` (store boundary RD27 R2).

The functions do not commit: the caller sets the transaction boundary.
"""

from __future__ import annotations

import json
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
    now_ms: int | None = None,
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

    return _new_public_id(conn, "kb_memberships", "public_id", _insert, now_ms=now_ms)


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


def update_membership(
    conn: sqlite3.Connection,
    *,
    kb_id: int,
    account_id: int,
    role: str | None = None,
    state: str | None = None,
    decided_by_account_id: int | None = None,
    now: float | None = None,
) -> None:
    """Change the role, the state, or the two of one membership (design Section 10.2). ``KeyError`` when there is no such membership."""
    if role is not None and role not in ROLES:
        raise ValueError(f"invalid role: {role!r}")
    if state is not None and state not in MEMBERSHIP_STATES:
        raise ValueError(f"invalid membership state: {state!r}")
    current = get_membership(conn, kb_id, account_id)
    if current is None:
        raise KeyError(f"no membership of account {account_id} in knowledge base {kb_id}")
    ts = time.time() if now is None else now
    conn.execute(
        "UPDATE kb_memberships SET role=?, state=?, decided_at=?, decided_by_account_id=? WHERE kb_id=? AND account_id=?",
        (role or current["role"], state or current["state"], ts, decided_by_account_id, int(kb_id), int(account_id)),
    )


def delete_membership(conn: sqlite3.Connection, kb_id: int, account_id: int) -> bool:
    """Delete one membership (leave, or remove). Return False when there was none."""
    return conn.execute("DELETE FROM kb_memberships WHERE kb_id=? AND account_id=?", (int(kb_id), int(account_id))).rowcount > 0


def active_moderators(conn: sqlite3.Connection, kb_id: int) -> list[int]:
    """The accounts that moderate the knowledge base now: an active membership with the role ``moderator`` of an account that is ``active`` (a suspended account keeps its role, but the role gives no right; design Section 6.7 rule 5)."""
    rows = conn.execute(
        "SELECT m.account_id FROM kb_memberships m JOIN accounts a ON a.id = m.account_id WHERE m.kb_id=? AND m.role='moderator' AND m.state='active' AND a.status='active' ORDER BY m.account_id",
        (int(kb_id),),
    ).fetchall()
    return [int(r[0]) for r in rows]


def members_with_accounts(conn: sqlite3.Connection, kb_id: int, *, states: tuple[str, ...]) -> list[dict[str, Any]]:
    """The memberships of a knowledge base in the given states, with the public data of each account (the collection ``GET /kb/{number}/members``)."""
    for state in states:
        if state not in MEMBERSHIP_STATES:
            raise ValueError(f"invalid membership state: {state!r}")
    if not states:
        return []
    marks = ",".join("?" for _ in states)
    keys = ("account_id", "account_public_id", "username", "display_name", "profile_public", "account_status", "membership_public_id", "role", "state", "requested_at", "decided_at")
    rows = conn.execute(
        "SELECT a.id, a.public_id, a.username, a.display_name, a.profile_public, a.status, m.public_id, m.role, m.state, m.requested_at, m.decided_at "
        f"FROM kb_memberships m JOIN accounts a ON a.id = m.account_id WHERE m.kb_id=? AND m.state IN ({marks}) ORDER BY m.public_id DESC",
        (int(kb_id), *states),
    ).fetchall()
    return [dict(zip(keys, tuple(r))) for r in rows]


def account_memberships(conn: sqlite3.Connection, account_id: int, *, state: str) -> dict[int, str]:
    """The memberships of one account in the given state: knowledge base id → role (the collections ``/kb?joined=true`` and ``/@{u}/kb``, design Section 7)."""
    if state not in MEMBERSHIP_STATES:
        raise ValueError(f"invalid membership state: {state!r}")
    rows = conn.execute("SELECT kb_id, role FROM kb_memberships WHERE account_id=? AND state=?", (int(account_id), state)).fetchall()
    return {int(r[0]): str(r[1]) for r in rows}


def account_membership_states(conn: sqlite3.Connection, account_id: int) -> dict[int, str]:
    """The state of each membership of one account: ``{kb_id: state}`` (``pending``, ``active``, or ``banned``)."""
    return {int(r[0]): str(r[1]) for r in conn.execute("SELECT kb_id, state FROM kb_memberships WHERE account_id=?", (int(account_id),))}


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
    now_ms: int | None = None,
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

    return _new_public_id(conn, "kb_contributions", "public_id", _insert, now_ms=now_ms)


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


def get_contribution(conn: sqlite3.Connection, public_id: int) -> dict[str, Any] | None:
    row = conn.execute(f"SELECT {', '.join(_CONTRIBUTION_KEYS)} FROM kb_contributions WHERE public_id=?", (int(public_id),)).fetchone()
    return None if row is None else dict(zip(_CONTRIBUTION_KEYS, tuple(row)))


# The columns that ``update_contribution`` changes. The identity of a contribution (its knowledge base, origin, credit, and root) does not change.
_CONTRIBUTION_UPDATES = frozenset({"document_id", "state", "decision_reason", "approved_at", "approved_by_account_id", "updates_frozen", "revised_at", "decided_at"})


def update_contribution(conn: sqlite3.Connection, public_id: int, *, now: float | None = None, **fields: Any) -> None:
    """Change columns of one contribution. ``KeyError`` when there is no such contribution; ``ValueError`` for a column that does not change or a state that is not valid."""
    unknown = set(fields) - _CONTRIBUTION_UPDATES
    if unknown:
        raise ValueError(f"columns of a contribution that do not change: {sorted(unknown)}")
    if "state" in fields and fields["state"] not in CONTRIBUTION_STATES:
        raise ValueError(f"invalid contribution state: {fields['state']!r}")
    ts = time.time() if now is None else now
    names = sorted(fields)
    assignments = ", ".join(f"{name}=?" for name in names)
    cur = conn.execute(
        f"UPDATE kb_contributions SET {assignments}{', ' if names else ''}updated_at=? WHERE public_id=?",
        (*(fields[name] for name in names), ts, int(public_id)),
    )
    if cur.rowcount == 0:
        raise KeyError(f"no contribution {public_id}")


def finish_merge(conn: sqlite3.Connection, public_id: int, *, from_state: str, document_id: str, state: str, decision_reason: str | None = None, now: float | None = None) -> bool:
    """Set the state of a contribution at the end of its merge job, only when the contribution is still in ``from_state`` with the same copy (one statement). False when no row matches: the contribution changed while the job ran (withdrawn, removed, or a new copy)."""
    if state not in ("visible", "failed"):
        raise ValueError(f"invalid state after a merge: {state!r}")
    ts = time.time() if now is None else now
    if decision_reason is None:
        cur = conn.execute("UPDATE kb_contributions SET state=?, updated_at=? WHERE public_id=? AND state=? AND document_id=?", (state, ts, int(public_id), from_state, document_id))
    else:
        cur = conn.execute("UPDATE kb_contributions SET state=?, decision_reason=?, updated_at=? WHERE public_id=? AND state=? AND document_id=?", (state, decision_reason, ts, int(public_id), from_state, document_id))
    return cur.rowcount == 1


def approved_by_another(row: Any) -> bool:
    """True when another account approved the contribution (``contributions.approve``): that action sets ``decided_at`` with ``approved_at``. The approval that a contribution gets when it is made (a reviewer contributes, a curation, a fork) sets no ``decided_at``.

    The rule does not use ``approved_by_account_id`` alone, because an account purge sets that column to NULL. The admin can approve its own contribution with ``approve``: that is no decision of another account.
    """
    if row["approved_at"] is None or row["decided_at"] is None:
        return False
    approver, contributor = row["approved_by_account_id"], row["contributor_account_id"]
    return approver is None or contributor is None or int(approver) != int(contributor)


def delete_contribution(conn: sqlite3.Connection, public_id: int) -> bool:
    """Delete one contribution row (a withdrawal, design Section 9.4). Its revisions go with it."""
    return conn.execute("DELETE FROM kb_contributions WHERE public_id=?", (int(public_id),)).rowcount > 0


def contributions_of_root(conn: sqlite3.Connection, root_document_id: str) -> list[dict[str, Any]]:
    """The contributions whose root is the document, in each origin and each knowledge base (design Section 9.5 rule 2)."""
    rows = conn.execute(f"SELECT {', '.join(_CONTRIBUTION_KEYS)} FROM kb_contributions WHERE root_document_id=? ORDER BY public_id", (root_document_id,)).fetchall()
    return [dict(zip(_CONTRIBUTION_KEYS, tuple(r))) for r in rows]


_REVISION_KEYS = ("id", "contribution_public_id", "previous_document_id", "document_id", "content_hash", "state", "created_at", "completed_at")


def add_revision(conn: sqlite3.Connection, *, contribution_public_id: int, previous_document_id: str | None, document_id: str, content_hash: str, now: float | None = None) -> int:
    """Record one revision of a contribution in the state ``merging`` (design Section 9.5 rule 3)."""
    ts = time.time() if now is None else now
    cur = conn.execute(
        "INSERT INTO kb_contribution_revisions (contribution_public_id, previous_document_id, document_id, content_hash, state, created_at) VALUES (?, ?, ?, ?, 'merging', ?)",
        (int(contribution_public_id), previous_document_id, document_id, content_hash, ts),
    )
    return int(cur.lastrowid)


def complete_revision(conn: sqlite3.Connection, revision_id: int, *, state: str, now: float | None = None) -> None:
    if state not in ("done", "failed"):
        raise ValueError(f"invalid revision state: {state!r}")
    conn.execute("UPDATE kb_contribution_revisions SET state=?, completed_at=? WHERE id=?", (state, time.time() if now is None else now, int(revision_id)))


def apply_revision(conn: sqlite3.Connection, revision_id: int, *, now: float | None = None) -> bool:
    """Point the contribution of a revision to the new copy and complete the revision, only when the revision is still ``merging``, the contribution is ``visible`` or ``hidden`` with updates on, and its copy is still the previous copy of the revision (one statement for the condition). False when no row matches."""
    ts = time.time() if now is None else now
    cur = conn.execute(
        "UPDATE kb_contributions SET document_id=(SELECT document_id FROM kb_contribution_revisions WHERE id=?), revised_at=?, updated_at=? "
        "WHERE state IN ('visible', 'hidden') AND updates_frozen=0 AND EXISTS (SELECT 1 FROM kb_contribution_revisions r WHERE r.id=? AND r.state='merging' AND r.contribution_public_id=kb_contributions.public_id AND r.previous_document_id=kb_contributions.document_id)",
        (int(revision_id), ts, ts, int(revision_id)),
    )
    if cur.rowcount != 1:
        return False
    conn.execute("UPDATE kb_contribution_revisions SET state='done', completed_at=? WHERE id=?", (ts, int(revision_id)))
    return True


def get_revision(conn: sqlite3.Connection, revision_id: int) -> dict[str, Any] | None:
    row = conn.execute(f"SELECT {', '.join(_REVISION_KEYS)} FROM kb_contribution_revisions WHERE id=?", (int(revision_id),)).fetchone()
    return None if row is None else dict(zip(_REVISION_KEYS, tuple(row)))


def list_revisions(conn: sqlite3.Connection, contribution_public_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(f"SELECT {', '.join(_REVISION_KEYS)} FROM kb_contribution_revisions WHERE contribution_public_id=? ORDER BY id", (int(contribution_public_id),)).fetchall()
    return [dict(zip(_REVISION_KEYS, tuple(r))) for r in rows]


# --- submissions (design Section 9.2) --------------------------------------------------------------

_ITEM_KEYS = ("id", "submission_public_id", "source_document_id", "theme_id", "routed_by", "match_score", "target_kb_ids", "skipped")


def add_submission(conn: sqlite3.Connection, *, account_id: int, now: float | None = None, now_ms: int | None = None) -> int:
    """Insert one submission (a request to contribute documents to themes) and return its public ID."""
    ts = time.time() if now is None else now

    def _insert(number: int) -> None:
        conn.execute("INSERT INTO kb_submissions (public_id, account_id, created_at) VALUES (?, ?, ?)", (number, int(account_id), ts))

    return _new_public_id(conn, "kb_submissions", "public_id", _insert, now_ms=now_ms)


def add_submission_item(conn: sqlite3.Connection, *, submission_public_id: int, source_document_id: str, theme_id: int | None, routed_by: str, match_score: float | None) -> int:
    if routed_by not in ("server", "account"):
        raise ValueError(f"invalid routed_by: {routed_by!r}")
    cur = conn.execute(
        "INSERT INTO kb_submission_items (submission_public_id, source_document_id, theme_id, routed_by, match_score) VALUES (?, ?, ?, ?, ?)",
        (int(submission_public_id), source_document_id, theme_id, routed_by, match_score),
    )
    return int(cur.lastrowid)


def set_submission_item_targets(conn: sqlite3.Connection, item_id: int, *, target_kb_ids: list[int], skipped: list[dict[str, Any]]) -> None:
    """Store the target list of an item and the targets that were skipped with their reasons (Section 9.2 rules 4 and 5)."""
    conn.execute("UPDATE kb_submission_items SET target_kb_ids=?, skipped=? WHERE id=?", (json.dumps(target_kb_ids), json.dumps(skipped), int(item_id)))


def get_submission(conn: sqlite3.Connection, public_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT public_id, account_id, created_at FROM kb_submissions WHERE public_id=?", (int(public_id),)).fetchone()
    return None if row is None else dict(zip(("public_id", "account_id", "created_at"), tuple(row)))


def list_submissions(conn: sqlite3.Connection, account_id: int) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT public_id, account_id, created_at FROM kb_submissions WHERE account_id=? ORDER BY public_id DESC", (int(account_id),)).fetchall()
    return [dict(zip(("public_id", "account_id", "created_at"), tuple(r))) for r in rows]


def submission_items(conn: sqlite3.Connection, submission_public_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(f"SELECT {', '.join(_ITEM_KEYS)} FROM kb_submission_items WHERE submission_public_id=? ORDER BY id", (int(submission_public_id),)).fetchall()
    items = []
    for row in rows:
        item = dict(zip(_ITEM_KEYS, tuple(row)))
        item["target_kb_ids"] = json.loads(item["target_kb_ids"] or "[]")
        item["skipped"] = json.loads(item["skipped"] or "[]")
        items.append(item)
    return items


def contributions_of_item(conn: sqlite3.Connection, submission_item_id: int) -> list[dict[str, Any]]:
    """The contributions that one submission item made, one for each target knowledge base (the decisions of each reviewer, Section 9.3 rule 2)."""
    rows = conn.execute(f"SELECT {', '.join(_CONTRIBUTION_KEYS)} FROM kb_contributions WHERE submission_item_id=? ORDER BY kb_id", (int(submission_item_id),)).fetchall()
    return [dict(zip(_CONTRIBUTION_KEYS, tuple(r))) for r in rows]


def pending_count(conn: sqlite3.Connection, *, kb_id: int, account_id: int) -> int:
    """The pending contributions of one account in one knowledge base (the pending limit, Section 9.2 rule 5)."""
    return int(conn.execute("SELECT COUNT(*) FROM kb_contributions WHERE kb_id=? AND contributor_account_id=? AND state='pending'", (int(kb_id), int(account_id))).fetchone()[0])


# --- automatic rule, contribution blocks, mutual collaborations (design Sections 9.9, 9.10) -------


def get_auto_rule(conn: sqlite3.Connection, account_id: int) -> dict[str, Any] | None:
    """The automatic rule of an account, with the IDs of its themes, or None."""
    keys = ("account_id", "state", "to_memberships", "to_mutual", "include_uploads", "activated_at", "updated_at")
    row = conn.execute(f"SELECT {', '.join(keys)} FROM kb_auto_rules WHERE account_id=?", (int(account_id),)).fetchone()
    if row is None:
        return None
    rule = dict(zip(keys, tuple(row)))
    rule["theme_ids"] = [int(r[0]) for r in conn.execute("SELECT theme_id FROM kb_auto_rule_themes WHERE account_id=? ORDER BY theme_id", (int(account_id),))]
    return rule


def save_auto_rule(conn: sqlite3.Connection, *, account_id: int, state: str, to_memberships: bool, to_mutual: bool, include_uploads: bool, theme_ids: list[int], now: float | None = None) -> None:
    """Set the one automatic rule of an account (CT31). ``activated_at`` is the time of the last change to ``active``. No commit."""
    if state not in ("active", "paused"):
        raise ValueError(f"invalid rule state: {state!r}")
    ts = time.time() if now is None else now
    current = conn.execute("SELECT state, activated_at FROM kb_auto_rules WHERE account_id=?", (int(account_id),)).fetchone()
    activated_at = ts if state == "active" and (current is None or current[0] != "active") else (None if current is None else current[1])
    conn.execute(
        "INSERT INTO kb_auto_rules (account_id, state, to_memberships, to_mutual, include_uploads, activated_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(account_id) DO UPDATE SET state=excluded.state, to_memberships=excluded.to_memberships, to_mutual=excluded.to_mutual, include_uploads=excluded.include_uploads, activated_at=excluded.activated_at, updated_at=excluded.updated_at",
        (int(account_id), state, 1 if to_memberships else 0, 1 if to_mutual else 0, 1 if include_uploads else 0, activated_at, ts),
    )
    conn.execute("DELETE FROM kb_auto_rule_themes WHERE account_id=?", (int(account_id),))
    conn.executemany("INSERT INTO kb_auto_rule_themes (account_id, theme_id) VALUES (?, ?)", [(int(account_id), int(t)) for t in sorted(set(theme_ids))])


def accounts_with_active_rule(conn: sqlite3.Connection) -> list[int]:
    return [int(r[0]) for r in conn.execute("SELECT r.account_id FROM kb_auto_rules r JOIN accounts a ON a.id = r.account_id WHERE r.state='active' AND a.status='active' ORDER BY r.account_id")]


def set_contribution_block(conn: sqlite3.Connection, *, account_id: int, document_id: str, blocked: bool, now: float | None = None) -> None:
    """The flag "never contribute" of a document of the personal knowledge base of the account."""
    if blocked:
        conn.execute("INSERT INTO kb_contribution_blocks (document_id, account_id, created_at) VALUES (?, ?, ?) ON CONFLICT(document_id) DO NOTHING", (document_id, int(account_id), time.time() if now is None else now))
    else:
        conn.execute("DELETE FROM kb_contribution_blocks WHERE document_id=? AND account_id=?", (document_id, int(account_id)))


def contribution_blocks(conn: sqlite3.Connection, account_id: int) -> set[str]:
    return {str(r[0]) for r in conn.execute("SELECT document_id FROM kb_contribution_blocks WHERE account_id=?", (int(account_id),))}


def documents_contributed_to(conn: sqlite3.Connection, kb_id: int, account_id: int) -> set[str]:
    """The documents of the account that have a contribution in K in any state (the source or the root): such a document never goes again to K by the automatic rule."""
    out: set[str] = set()
    for row in conn.execute("SELECT source_document_id, root_document_id FROM kb_contributions WHERE kb_id=? AND contributor_account_id=?", (int(kb_id), int(account_id))):
        out.update(str(v) for v in row if v)
    return out


def contributions_of_account(conn: sqlite3.Connection, account_id: int, *, origin: str | None = None) -> list[dict[str, Any]]:
    """The contributions with the credit of one account, the newest first. ``origin`` selects one origin (the list of automatic contributions, Section 9.9 rule 6); None gives each origin."""
    where, params = "contributor_account_id=?", [int(account_id)]
    if origin is not None:
        where += " AND origin=?"
        params.append(origin)
    rows = conn.execute(f"SELECT {', '.join(_CONTRIBUTION_KEYS)} FROM kb_contributions WHERE {where} ORDER BY contributed_at DESC, public_id DESC", params).fetchall()
    return [dict(zip(_CONTRIBUTION_KEYS, tuple(r))) for r in rows]


def _pair(a: int, b: int) -> tuple[int, int]:
    if int(a) == int(b):
        raise ValueError("a mutual collaboration needs two accounts")
    return (int(a), int(b)) if int(a) < int(b) else (int(b), int(a))


def get_mutual(conn: sqlite3.Connection, a: int, b: int) -> dict[str, Any] | None:
    keys = ("account_low", "account_high", "requested_by_account_id", "state", "created_at", "decided_at")
    row = conn.execute(f"SELECT {', '.join(keys)} FROM kb_mutual_collaborations WHERE account_low=? AND account_high=?", _pair(a, b)).fetchone()
    return None if row is None else dict(zip(keys, tuple(row)))


def request_mutual(conn: sqlite3.Connection, *, requester: int, other: int, now: float | None = None) -> None:
    """A requests a mutual collaboration with B (state ``pending``). ``ValueError`` when the pair has a row."""
    if get_mutual(conn, requester, other) is not None:
        raise ValueError("mutual_exists")
    low, high = _pair(requester, other)
    conn.execute("INSERT INTO kb_mutual_collaborations (account_low, account_high, requested_by_account_id, state, created_at) VALUES (?, ?, ?, 'pending', ?)", (low, high, int(requester), time.time() if now is None else now))


def accept_mutual(conn: sqlite3.Connection, *, account_id: int, other: int, now: float | None = None) -> bool:
    """B accepts the request of A. False when there is no pending request of the other account."""
    low, high = _pair(account_id, other)
    cur = conn.execute(
        "UPDATE kb_mutual_collaborations SET state='active', decided_at=? WHERE account_low=? AND account_high=? AND state='pending' AND requested_by_account_id=?",
        (time.time() if now is None else now, low, high, int(other)),
    )
    return cur.rowcount == 1


def end_mutual(conn: sqlite3.Connection, a: int, b: int) -> bool:
    """Either account ends the mutual collaboration, or removes the request."""
    return conn.execute("DELETE FROM kb_mutual_collaborations WHERE account_low=? AND account_high=?", _pair(a, b)).rowcount == 1


def mutual_rows(conn: sqlite3.Connection, account_id: int) -> list[dict[str, Any]]:
    """The mutual collaborations and requests of an account: ``{"account_id" (the other account), "state", "requested_by_me", "created_at", "decided_at"}``."""
    rows = conn.execute(
        "SELECT account_low, account_high, requested_by_account_id, state, created_at, decided_at FROM kb_mutual_collaborations WHERE account_low=? OR account_high=? ORDER BY created_at",
        (int(account_id), int(account_id)),
    ).fetchall()
    return [
        {"account_id": int(r[1]) if int(r[0]) == int(account_id) else int(r[0]), "state": r[3], "requested_by_me": int(r[2]) == int(account_id), "created_at": r[4], "decided_at": r[5]}
        for r in rows
    ]


def mutual_collaborators(conn: sqlite3.Connection, account_id: int) -> list[int]:
    """The accounts with an active mutual collaboration with the account. A suspended account is paused: only accounts with the status ``active`` count (Section 9.10 rule 5)."""
    rows = conn.execute(
        "SELECT CASE WHEN m.account_low=? THEN m.account_high ELSE m.account_low END AS other FROM kb_mutual_collaborations m "
        "JOIN accounts a ON a.id = (CASE WHEN m.account_low=? THEN m.account_high ELSE m.account_low END) "
        "WHERE (m.account_low=? OR m.account_high=?) AND m.state='active' AND a.status='active' ORDER BY other",
        (int(account_id),) * 4,
    ).fetchall()
    return [int(r[0]) for r in rows]


# --- account purge (design Section 13.2) ---------------------------------------------------------


def purge_account(conn: sqlite3.Connection, account_id: int, *, delete_activity_records: bool) -> dict[str, Any]:
    """The rows of the collaboration tables at the purge of an account (design Section 13.2, CT7, CT19). No commit.

    - Memberships, the automatic rule, the contribution blocks, the mutual collaborations, and the stored reputation of the account are deleted.
    - Pending contributions of the account are deleted: they are not yet content of a knowledge base. The result gives their hidden copies (``pending_copies``: ``(kb_id, document_id)``); the caller deletes those documents.
    - Each other contribution stays, without the credit (``contributor_account_id = NULL``). The account goes also from ``curator_account_id``, ``approved_by_account_id``, the decisions of memberships, the founder of a knowledge base, and the actor of the moderation log.
    - ``delete_activity_records`` (the option of the purge confirmation): also the submissions with their items are deleted, and the reason texts that the account wrote in the moderation log are cleared. The option never changes a document, a graph, or a contribution.
    """
    account_id = int(account_id)
    pending = conn.execute("SELECT public_id, kb_id, document_id FROM kb_contributions WHERE contributor_account_id=? AND state='pending'", (account_id,)).fetchall()
    copies = [(int(r["kb_id"]), str(r["document_id"])) for r in pending if r["document_id"]]
    conn.execute("DELETE FROM kb_contributions WHERE contributor_account_id=? AND state='pending'", (account_id,))
    credited = conn.execute("UPDATE kb_contributions SET contributor_account_id=NULL WHERE contributor_account_id=?", (account_id,)).rowcount
    conn.execute("UPDATE kb_contributions SET curator_account_id=NULL WHERE curator_account_id=?", (account_id,))
    conn.execute("UPDATE kb_contributions SET approved_by_account_id=NULL WHERE approved_by_account_id=?", (account_id,))
    conn.execute("DELETE FROM kb_memberships WHERE account_id=?", (account_id,))
    conn.execute("UPDATE kb_memberships SET decided_by_account_id=NULL WHERE decided_by_account_id=?", (account_id,))
    conn.execute("DELETE FROM kb_auto_rule_themes WHERE account_id=?", (account_id,))
    conn.execute("DELETE FROM kb_auto_rules WHERE account_id=?", (account_id,))
    conn.execute("DELETE FROM kb_contribution_blocks WHERE account_id=?", (account_id,))
    conn.execute("DELETE FROM kb_mutual_collaborations WHERE account_low=? OR account_high=?", (account_id, account_id))
    conn.execute("DELETE FROM account_reputation WHERE account_id=?", (account_id,))
    submissions = 0
    if delete_activity_records:
        conn.execute("UPDATE kb_moderation_log SET reason='' WHERE actor_account_id=?", (account_id,))
        numbers = [int(r[0]) for r in conn.execute("SELECT public_id FROM kb_submissions WHERE account_id=?", (account_id,))]
        for number in numbers:
            conn.execute("UPDATE kb_contributions SET submission_item_id=NULL WHERE submission_item_id IN (SELECT id FROM kb_submission_items WHERE submission_public_id=?)", (number,))
            conn.execute("DELETE FROM kb_submission_items WHERE submission_public_id=?", (number,))
        conn.execute("DELETE FROM kb_submissions WHERE account_id=?", (account_id,))
        submissions = len(numbers)
    else:
        # Without the option the submissions stay as records without an account (design Section 11.2: ``kb_submissions.account_id`` is NULL after an account purge).
        conn.execute("UPDATE kb_submissions SET account_id=NULL WHERE account_id=?", (account_id,))
    conn.execute("UPDATE kb_moderation_log SET actor_account_id=NULL WHERE actor_account_id=?", (account_id,))
    return {"pending_copies": copies, "credit_removed": int(credited), "submissions_deleted": submissions}


# --- reputation (design Section 8.6) -------------------------------------------------------------

_REPUTATION_KEYS = ("account_id", "level", "fiction_share", "fiction_like_documents", "approval_rate", "decisions", "computed_at", "campaign_day")


def save_reputation(conn: sqlite3.Connection, *, account_id: int, level: str, fiction_share: float, fiction_like_documents: int, approval_rate: float | None, decisions: int, now: float | None = None) -> None:
    ts = time.time() if now is None else now
    conn.execute(
        "INSERT INTO account_reputation (account_id, level, fiction_share, fiction_like_documents, approval_rate, decisions, computed_at) VALUES (?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(account_id) DO UPDATE SET level=excluded.level, fiction_share=excluded.fiction_share, fiction_like_documents=excluded.fiction_like_documents, "
        "approval_rate=excluded.approval_rate, decisions=excluded.decisions, computed_at=excluded.computed_at",
        (int(account_id), level, float(fiction_share), int(fiction_like_documents), approval_rate, int(decisions), ts),
    )


def get_reputation(conn: sqlite3.Connection, account_id: int) -> dict[str, Any] | None:
    row = conn.execute(f"SELECT {', '.join(_REPUTATION_KEYS)} FROM account_reputation WHERE account_id=?", (int(account_id),)).fetchone()
    return None if row is None else dict(zip(_REPUTATION_KEYS, tuple(row)))


def take_campaign_day(conn: sqlite3.Connection, account_id: int, day: str) -> bool:
    """True one time for each account and day: the standing message of the campaign shows with the first ingest of the day (design Section 8.7 rule 3). The account must have a stored reputation. No commit."""
    cur = conn.execute("UPDATE account_reputation SET campaign_day=? WHERE account_id=? AND (campaign_day IS NULL OR campaign_day != ?)", (day, int(account_id), day))
    return cur.rowcount == 1


def roots_in_fictional_kbs(conn: sqlite3.Connection, account_id: int) -> set[str]:
    """The root documents that the account contributed to a shared knowledge base of the type ``fictional``, where the contribution is visible or approved."""
    rows = conn.execute(
        "SELECT c.root_document_id FROM kb_contributions c JOIN knowledge_bases k ON k.id = c.kb_id "
        "WHERE c.contributor_account_id=? AND k.knowledge_type='fictional' AND c.root_document_id IS NOT NULL AND (c.state='visible' OR c.approved_at IS NOT NULL) AND c.state NOT IN ('rejected', 'removed')",
        (int(account_id),),
    ).fetchall()
    return {str(r[0]) for r in rows}


def decisions_of_account(conn: sqlite3.Connection, account_id: int, *, since: float, origins: tuple[str, ...], reasons: tuple[str, ...]) -> list[dict[str, Any]]:
    """The decisions of other accounts on the contributions of the account since ``since``: ``{"kb_id", "approved"}``. A rejection or a removal counts only with one of ``reasons``; an approval that a later removal replaced is not an approval."""
    marks = ", ".join("?" for _ in origins)
    rows = conn.execute(
        f"SELECT kb_id, state, decision_reason, approved_at, approved_by_account_id, contributor_account_id, decided_at FROM kb_contributions WHERE contributor_account_id=? AND origin IN ({marks})",
        (int(account_id), *origins),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        if row["state"] in ("rejected", "removed"):
            if row["decided_at"] is not None and row["decided_at"] >= since and row["decision_reason"] in reasons:
                out.append({"kb_id": int(row["kb_id"]), "approved": False})
        elif row["approved_at"] is not None and row["approved_at"] >= since and approved_by_another(row):
            # The approver can be a purged account (``approved_by_account_id`` NULL): the approval still counts.
            out.append({"kb_id": int(row["kb_id"]), "approved": True})
    return out


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
