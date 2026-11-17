"""Reputation of an account (SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md Section 8.6, CT18; unit CT-11).

The reputation controls how far the personal knowledge base and the contributions of an account go in the exchange. It never controls reading, ingest into the personal knowledge base, publication, or membership.

Two signals:
- Fiction share: the fiction-like documents (the theme match is a ``fictional`` theme) divided by the visible documents of the personal knowledge base. A fiction-like document does not count when the account contributed it to a shared knowledge base of the type ``fictional`` and that contribution is visible or approved.
- Approval rate: in the window, the approved contributions divided by the decided contributions of the account. Only the contributions that the account made itself count (origins ``direct``, ``bulk``, ``auto``), and only the decisions of another account. A rejection or a removal counts only with a reason code of ``COUNTED_REASONS``. The decisions of one shared knowledge base count for at most one third of the decisions. There is no value with fewer decisions than the minimum.

The level is computed from the data at each use, so a decision takes effect at once. ``refresh`` also stores it in ``account_reputation`` for the lists.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from typing import Any

from .store import kb_collab
from .store import themes as themes_store

LEVELS = ("good", "limited", "low")
COUNTED_REASONS = ("off_subject", "wrong_knowledge_type", "low_quality", "spam")
OWN_ORIGINS = ("direct", "bulk", "auto")

# Admin settings in ``server_settings``. The first values are the ones of the design; they are not calibrated (the development data has no decisions).
SHARE_SETTINGS = {"reputation_fiction_share_limited": 0.2, "reputation_fiction_share_low": 0.5, "reputation_approval_rate_limited": 0.5, "reputation_approval_rate_low": 0.2}
COUNT_SETTINGS = {
    "reputation_window_days": 180,
    "reputation_min_decisions": 10,
    "pending_limit_good": 100,
    "pending_limit_limited": 20,
    "pending_limit_low": 5,
    "auto_contribution_limited_documents": 10,
}
SETTINGS: dict[str, float] = {**SHARE_SETTINGS, **COUNT_SETTINGS}


def _checked(key: str, value: Any) -> float:
    """The value of one setting as a number, or ``ValueError``. A share is in [0, 1]; a count is an integer of 1 or more."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{key} must be a number")
    if key in SHARE_SETTINGS:
        if not 0.0 <= float(value) <= 1.0:
            raise ValueError(f"{key} must be a number from 0 to 1")
        return float(value)
    if float(value) != int(value) or int(value) < 1:
        raise ValueError(f"{key} must be an integer of 1 or more")
    return int(value)


def read_settings(conn: sqlite3.Connection) -> dict[str, float]:
    """The settings of the reputation. A stored value that is not valid is a defect of the data: ``RuntimeError``."""
    values = dict(SETTINGS)
    for key in SETTINGS:
        row = conn.execute("SELECT value FROM server_settings WHERE key=?", (key,)).fetchone()
        if row is None:
            continue
        try:
            values[key] = _checked(key, float(row[0]))
        except (TypeError, ValueError):
            raise RuntimeError(f"server_settings.{key} is not valid: {row[0]!r}") from None
    return values


def write_setting(conn: sqlite3.Connection, key: str, value: Any, *, updated_by: int | None) -> None:
    """Set one setting. ``ValueError`` for an unknown key or a value that is not valid. No commit."""
    if key not in SETTINGS:
        raise ValueError(f"unknown reputation setting: {key!r}")
    number = _checked(key, value)
    conn.execute(
        "INSERT INTO server_settings (key, value, updated_at, updated_by) VALUES (?, ?, ?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at, updated_by=excluded.updated_by",
        (key, repr(number), time.time(), updated_by),
    )


@dataclass(frozen=True, slots=True)
class Reputation:
    account_id: int
    level: str
    fiction_share: float
    fiction_like_documents: tuple[str, ...]  # the documents that count
    visible_documents: int
    approval_rate: float | None
    decisions: int

    def to_dict(self) -> dict[str, Any]:
        return {"level": self.level, "fiction_share": self.fiction_share, "fiction_like_documents": len(self.fiction_like_documents), "visible_documents": self.visible_documents, "approval_rate": self.approval_rate, "decisions": self.decisions}


def approval_rate(decisions: list[tuple[int, bool]], *, min_decisions: int) -> tuple[float | None, int]:
    """The approval rate from ``(knowledge base, approved)`` decisions, and the count of decisions.

    The decisions of one knowledge base count for at most one third of all decisions: a knowledge base with more decisions keeps its own rate and gets the weight of one third. None with fewer than ``min_decisions`` decisions.
    """
    total = len(decisions)
    if total < min_decisions or total == 0:
        return None, total
    by_kb: dict[int, list[int]] = {}
    for kb_id, approved in decisions:
        entry = by_kb.setdefault(int(kb_id), [0, 0])
        entry[0] += 1
        entry[1] += 1 if approved else 0
    cap = total / 3.0
    weight = sum(min(count, cap) for count, _ in by_kb.values())
    approved_weight = sum(min(count, cap) * (approved / count) for count, approved in by_kb.values())
    return approved_weight / weight, total


def level_of(fiction_share: float, rate: float | None, settings: dict[str, float]) -> str:
    if fiction_share >= settings["reputation_fiction_share_low"] or (rate is not None and rate < settings["reputation_approval_rate_low"]):
        return "low"
    if fiction_share >= settings["reputation_fiction_share_limited"] or (rate is not None and rate < settings["reputation_approval_rate_limited"]):
        return "limited"
    return "good"


def compute(conn: sqlite3.Connection, account_id: int, *, now: float | None = None, settings: dict[str, float] | None = None) -> Reputation:
    """The reputation of an account from the data now. It writes nothing."""
    ts = time.time() if now is None else now
    settings = read_settings(conn) if settings is None else settings
    personal = conn.execute("SELECT id FROM knowledge_bases WHERE is_personal=1 AND account_id=?", (int(account_id),)).fetchone()
    visible: set[str] = set()
    fiction_like: list[str] = []
    if personal is not None:
        kb_id = int(personal[0])
        visible = {str(r[0]) for r in conn.execute("SELECT id FROM documents WHERE kb_id=? AND hidden=0", (kb_id,))}
        contributed = kb_collab.roots_in_fictional_kbs(conn, int(account_id))
        fiction_like = sorted(d for d in themes_store.fiction_like_documents(conn, kb_id) if d in visible and d not in contributed)
    share = len(fiction_like) / len(visible) if visible else 0.0
    since = ts - settings["reputation_window_days"] * 24 * 60 * 60
    decisions = [(row["kb_id"], row["approved"]) for row in kb_collab.decisions_of_account(conn, int(account_id), since=since, origins=OWN_ORIGINS, reasons=COUNTED_REASONS)]
    rate, count = approval_rate(decisions, min_decisions=int(settings["reputation_min_decisions"]))
    return Reputation(int(account_id), level_of(share, rate, settings), share, tuple(fiction_like), len(visible), rate, count)


def compute_remote(conn: sqlite3.Connection, actor_iri: str, *, now: float | None = None, settings: dict[str, float] | None = None) -> tuple[str, float | None, int]:
    """The reputation of an account of another server on this server: ``(level, approval rate, decisions)`` (federation design Sections 12.1 rule 5 and 12.2 rule 2).

    It comes only from the decisions of the reviewers of this server on the contributions that the account offered; no value of another server is read (KS4). The account has no personal knowledge base on this server, so the fiction share is 0 and only the approval rate can lower the level.
    """
    ts = time.time() if now is None else now
    settings = read_settings(conn) if settings is None else settings
    since = ts - settings["reputation_window_days"] * 24 * 60 * 60
    decisions = [(row["kb_id"], row["approved"]) for row in kb_collab.decisions_of_remote_actor(conn, actor_iri, since=since, reasons=COUNTED_REASONS)]
    rate, count = approval_rate(decisions, min_decisions=int(settings["reputation_min_decisions"]))
    return level_of(0.0, rate, settings), rate, count


def refresh(conn: sqlite3.Connection, account_id: int, *, now: float | None = None) -> Reputation:
    """Compute the reputation and store it. No commit."""
    ts = time.time() if now is None else now
    reputation = compute(conn, account_id, now=ts)
    kb_collab.save_reputation(
        conn, account_id=reputation.account_id, level=reputation.level, fiction_share=reputation.fiction_share, fiction_like_documents=len(reputation.fiction_like_documents),
        approval_rate=reputation.approval_rate, decisions=reputation.decisions, now=ts,
    )
    return reputation


def level(conn: sqlite3.Connection, account_id: int, *, now: float | None = None) -> str:
    return compute(conn, account_id, now=now).level


# --- effects (the table of Section 8.6) ------------------------------------------------------------------------------


def needs_approval(level_name: str) -> bool:
    """With the level ``low`` each contribution waits for approval, also the one of a member, a collaborator, or a moderator."""
    return level_name == "low"


def attaches_to_themes(level_name: str) -> bool:
    """With the level ``low`` the public personal knowledge base is not attached to a theme; thus it is no curation source."""
    return level_name != "low"


def pending_limit(settings: dict[str, float], level_name: str) -> int:
    """The pending contributions of one account in one shared knowledge base."""
    return int(settings[f"pending_limit_{level_name}"])


def queue_rank(level_name: str | None) -> int:
    """The order in the review queue: ``good`` first, ``low`` last. A contribution with no account (a deleted account) is last."""
    return LEVELS.index(level_name) if level_name in LEVELS else len(LEVELS)


def auto_contribution_limit(settings: dict[str, float], level_name: str) -> int | None:
    """The documents of one run and target of the automatic contribution (Section 9.9): None is no limit, 0 is paused."""
    if level_name == "low":
        return 0
    return int(settings["auto_contribution_limited_documents"]) if level_name == "limited" else None
