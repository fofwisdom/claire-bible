"""Automatic contribution and mutual collaboration (SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md Sections 9.9 and 9.10, CT31, CT37; unit CT-13).

An account sets one rule. The rule sends the documents of the personal knowledge base that fit a target shared knowledge base to that knowledge base, as contributions with the origin ``auto``. The server makes no LLM call for the selection: the score is the document match of Section 8.5.

- The function works only while the server switch ``kb_auto_contribution`` is ``on`` (the default is ``off``).
- Targets of a rule: the shared knowledge bases where the account is an active member (``memberships``), the ones that a mutual collaborator founded (``mutual``), and the ones of the themes of the rule (``themes``).
- A run makes the contributions and their merge jobs. The caller runs the merge jobs.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from typing import Any

from . import contributions, ids, reputation, theme_judgement
from .store import db as dbm
from .store import kb_collab
from .store import themes as themes_store

log = logging.getLogger("claire.auto_contribution")

SWITCH_KEY = "kb_auto_contribution"
# Admin settings in ``server_settings``. ``auto_match_threshold`` is not calibrated (see ``theme_judgement``).
SETTINGS: dict[str, float] = {"auto_match_threshold": 0.5, "auto_run_documents": 50}
# ``documents.source_type`` of a pasted text. Such a document never goes by the rule.
PASTED_TEXT = "text"


def switch_on(conn: sqlite3.Connection) -> bool:
    """The server switch. No row is ``off`` (the default of the design); a value that is not ``on`` or ``off`` is a defect of the data: ``RuntimeError``."""
    row = conn.execute("SELECT value FROM server_settings WHERE key=?", (SWITCH_KEY,)).fetchone()
    if row is None:
        return False
    if row[0] not in ("on", "off"):
        raise RuntimeError(f"server_settings.{SWITCH_KEY} is not valid: {row[0]!r}")
    return row[0] == "on"


def set_switch(conn: sqlite3.Connection, on: bool, *, updated_by: int | None) -> None:
    _write(conn, SWITCH_KEY, "on" if on else "off", updated_by)


def _write(conn: sqlite3.Connection, key: str, value: str, updated_by: int | None) -> None:
    conn.execute(
        "INSERT INTO server_settings (key, value, updated_at, updated_by) VALUES (?, ?, ?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at, updated_by=excluded.updated_by",
        (key, value, time.time(), updated_by),
    )


def _checked(key: str, value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{key} must be a number")
    if key == "auto_match_threshold":
        if not 0.0 <= float(value) <= 1.0:
            raise ValueError(f"{key} must be a number from 0 to 1")
        return float(value)
    if float(value) != int(value) or int(value) < 1:
        raise ValueError(f"{key} must be an integer of 1 or more")
    return int(value)


def read_settings(conn: sqlite3.Connection) -> dict[str, float]:
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
    if key not in SETTINGS:
        raise ValueError(f"unknown automatic contribution setting: {key!r}")
    _write(conn, key, repr(_checked(key, value)), updated_by)


# --- targets and candidates ------------------------------------------------------------------------------------------


def _shared_kb(conn: sqlite3.Connection, kb_id: int) -> sqlite3.Row | None:
    row = conn.execute("SELECT id, state, seed_entities, created_by_account_id FROM knowledge_bases WHERE id=? AND COALESCE(is_personal, 0)=0 AND account_id IS NULL", (int(kb_id),)).fetchone()
    return row if row is not None and row["state"] == "active" else None


def targets(conn: sqlite3.Connection, account_id: int, rule: dict[str, Any]) -> list[int]:
    """The active shared knowledge bases that the rule selects (Section 9.9 rule 1). A knowledge base where the account is banned is no target."""
    found: set[int] = set()
    if rule["to_memberships"]:
        found.update(kb_collab.account_memberships(conn, account_id, state="active"))
    if rule["to_mutual"]:
        for other in kb_collab.mutual_collaborators(conn, account_id):
            found.update(int(r[0]) for r in conn.execute("SELECT id FROM knowledge_bases WHERE created_by_account_id=? AND COALESCE(is_personal, 0)=0 AND account_id IS NULL", (other,)))
    for theme_id in rule["theme_ids"]:
        theme = themes_store.get_theme(conn, int(theme_id))
        if theme is not None and theme["state"] == "active":
            found.update(int(m["kb_id"]) for m in themes_store.theme_members(conn, int(theme_id)))
    banned = set(kb_collab.account_memberships(conn, account_id, state="banned"))
    return sorted(kb_id for kb_id in found if kb_id not in banned and _shared_kb(conn, kb_id) is not None)


def _signature_keys(raw: Any) -> set[tuple[str, str]]:
    import json

    try:
        entries = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return set()
    return {(str(e[0]), str(e[1])) for e in entries if isinstance(e, list) and len(e) >= 2}


def _scorer(conn: sqlite3.Connection, matcher: theme_judgement.DocumentMatcher, kb_id: int):
    """The score function of a target K (Section 8.5): the signature of K, else the signature of a theme of K, else the seed entities of K. None when K has none of them."""
    signature = next((sig for sig in themes_store.load_signatures(conn) if int(sig["kb_id"]) == int(kb_id)), None)
    keys = _signature_keys(signature["entities"]) if signature is not None else set()
    if keys:
        return lambda document_keys: matcher.score(document_keys, keys)
    theme_keys = [theme["keys"] for theme in matcher.themes if theme["id"] in set(themes_store.themes_of_kb(conn, kb_id))]
    if theme_keys:
        return lambda document_keys: max(matcher.score(document_keys, k) for k in theme_keys)
    kb = _shared_kb(conn, kb_id)
    seeds = _seed_names(kb["seed_entities"]) if kb is not None else []
    if seeds:
        return lambda document_keys: matcher.seed_score(document_keys, seeds)
    return None


def _seed_names(raw: Any) -> list[str]:
    import json

    try:
        names = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    return [str(n) for n in names if isinstance(n, str) and n.strip()] if isinstance(names, list) else []


def candidates(conn: sqlite3.Connection, *, account_id: int, personal_kb_id: int, kb_id: int, include_uploads: bool, documents: list[str] | None = None, matcher: theme_judgement.DocumentMatcher | None = None, settings: dict[str, float] | None = None) -> list[dict[str, Any]]:
    """The documents of the personal knowledge base that fit K, with the highest score first (Section 9.9 rule 2): ``{"document", "title", "score"}``.

    Not candidates: a hidden document; a document with the flag "never contribute"; a pasted text; an uploaded file unless ``include_uploads``; a document with a contribution in K in any state; a document with a duplicate or a tombstone in K.
    ``documents``: only these documents are checked (after the extraction of a new document).
    """
    matcher = matcher or theme_judgement.DocumentMatcher.load(conn)
    settings = settings or read_settings(conn)
    score = _scorer(conn, matcher, kb_id)
    if score is None:
        return []
    blocked = kb_collab.contribution_blocks(conn, account_id)
    done = kb_collab.documents_contributed_to(conn, kb_id, account_id)
    sql = "SELECT id, title, url, canonical_url, content_hash, source_type FROM documents WHERE kb_id=? AND hidden=0"
    args: list[Any] = [int(personal_kb_id)]
    if documents is not None:
        if not documents:
            return []
        sql += f" AND id IN ({', '.join('?' for _ in documents)})"
        args.extend(documents)
    out: list[dict[str, Any]] = []
    for row in conn.execute(sql, args).fetchall():
        document_id = str(row["id"])
        if document_id in blocked or document_id in done or row["source_type"] == PASTED_TEXT:
            continue
        if not str(row["url"] or "").strip() and not include_uploads:
            continue  # a document with no URL comes from an uploaded file
        if not conn.execute("SELECT 1 FROM extractions WHERE document_id=? AND kb_id=? LIMIT 1", (document_id, int(personal_kb_id))).fetchone():
            continue
        if contributions.find_duplicate(conn, kb_id, canonical_url=row["canonical_url"], content_hash=row["content_hash"]) is not None:
            continue
        by_url = bool(row["canonical_url"])
        if dbm.is_tombstoned(conn, canonical_url=row["canonical_url"] if by_url else None, content_hash=None if by_url else row["content_hash"], kb_id=int(kb_id)):
            continue
        value = score(theme_judgement.document_keys(conn, int(personal_kb_id), document_id))
        if value >= settings["auto_match_threshold"]:
            out.append({"document": document_id, "title": row["title"], "score": value})
    out.sort(key=lambda c: (-c["score"], c["document"]))
    return out


# --- run -------------------------------------------------------------------------------------------------------------


def plan(conn: sqlite3.Connection, account_id: int, rule: dict[str, Any], *, documents: list[str] | None = None, now: float | None = None) -> list[dict[str, Any]]:
    """What a run of the rule sends now: for each target ``{"kb_id", "mode", "documents": [...]}`` (also the dry run of Section 9.9 rule 6). It writes nothing.

    ``mode`` (rule 3): ``approved`` for a collaborator or a moderator, ``direct`` for a member, ``pending`` for each other account. The reputation applies (Section 8.6): with ``low`` the rule is paused, and with ``limited`` a run sends fewer documents. The pending limit of the level also limits a ``pending`` target.
    """
    personal = conn.execute("SELECT id FROM knowledge_bases WHERE is_personal=1 AND account_id=?", (int(account_id),)).fetchone()
    if personal is None:
        return []
    level = reputation.level(conn, account_id, now=now)
    reputation_settings = reputation.read_settings(conn)
    limit = reputation.auto_contribution_limit(reputation_settings, level)
    settings = read_settings(conn)
    run_limit = int(settings["auto_run_documents"]) if limit is None else min(int(settings["auto_run_documents"]), limit)
    if run_limit <= 0:
        return []
    matcher = theme_judgement.DocumentMatcher.load(conn)
    out = []
    for kb_id in targets(conn, account_id, rule):
        membership = kb_collab.get_membership(conn, kb_id, account_id)
        active = membership is not None and membership["state"] == "active"
        mode = "approved" if active and membership["role"] in ("collaborator", "moderator") else ("direct" if active else "pending")
        found = candidates(conn, account_id=account_id, personal_kb_id=int(personal[0]), kb_id=kb_id, include_uploads=bool(rule["include_uploads"]), documents=documents, matcher=matcher, settings=settings)[:run_limit]
        if mode == "pending":
            room = reputation.pending_limit(reputation_settings, level) - kb_collab.pending_count(conn, kb_id=kb_id, account_id=account_id)
            found = found[: max(room, 0)]
        if found:
            out.append({"kb_id": kb_id, "mode": mode, "documents": found})
    return out


def run_account(conn: sqlite3.Connection, account_id: int, *, documents: list[str] | None = None, now: float | None = None) -> int:
    """One run of the rule of an account. Return the count of new contributions. Each contribution is its own transaction (a commit after each one); the caller runs the merge jobs.

    Nothing runs while the server switch is off, without an active rule, or for an account that is not active.
    """
    if not switch_on(conn):
        return 0
    rule = kb_collab.get_auto_rule(conn, account_id)
    if rule is None or rule["state"] != "active":
        return 0
    status = conn.execute("SELECT status FROM accounts WHERE id=?", (int(account_id),)).fetchone()
    if status is None or status[0] != "active":
        return 0
    ts = time.time() if now is None else now
    personal = conn.execute("SELECT id FROM knowledge_bases WHERE is_personal=1 AND account_id=?", (int(account_id),)).fetchone()
    made = 0
    for target in plan(conn, account_id, rule, documents=documents, now=ts):
        for candidate in target["documents"]:
            try:
                contributions.contribute_direct(
                    conn, kb_id=target["kb_id"], source_kb_id=int(personal[0]), source_document_id=candidate["document"], contributor_account_id=int(account_id),
                    approved=target["mode"] == "approved", pending=target["mode"] == "pending", origin="auto", now=ts,
                )
                conn.commit()
                made += 1
            except contributions.ContributionError as exc:
                conn.rollback()
                log.info("automatic contribution of %s to knowledge base %s did not go: %s", candidate["document"], target["kb_id"], exc.code)
    return made


def run_all(conn: sqlite3.Connection, *, now: float | None = None) -> int:
    """A run for each account with an active rule (after a judgement run, and when a shared knowledge base gets a signature or seed entities). A failure of one account does not stop the others."""
    if not switch_on(conn):
        return 0
    made = 0
    for account_id in kb_collab.accounts_with_active_rule(conn):
        try:
            made += run_account(conn, account_id, now=now)
        except Exception as exc:  # noqa: BLE001
            conn.rollback()
            log.warning("automatic contribution of account %s failed: %s", account_id, type(exc).__name__)
    return made


# --- mutual collaboration (Section 9.10) -----------------------------------------------------------------------------


def grant_mutual_collaborators(conn: sqlite3.Connection, *, kb_id: int, founder_account_id: int, now: float | None = None) -> list[int]:
    """A founded the shared knowledge base: each mutual collaborator of A becomes a collaborator in it at once (Section 9.10 rule 2). Return the accounts. No commit.

    The role is ``collaborator`` only: a mutual collaboration never gives ``people``, ``settings``, ``remove``, or ``moderator`` (rule 4). A membership that is there (also a banned one) stays as it is.
    """
    ts = time.time() if now is None else now
    granted = []
    for other in kb_collab.mutual_collaborators(conn, founder_account_id):
        if kb_collab.get_membership(conn, kb_id, other) is not None:
            continue
        kb_collab.add_membership(conn, kb_id=kb_id, account_id=other, role="collaborator", state="active", decided_by_account_id=founder_account_id, now=ts)
        public_id = conn.execute("SELECT public_id FROM accounts WHERE id=?", (other,)).fetchone()[0]
        kb_collab.log_moderation(conn, kb_id=kb_id, actor_account_id=founder_account_id, action="grant_collaborator", target=ids.format_public_id(int(public_id)), reason="mutual_collaboration", now=ts)
        granted.append(other)
    return granted
