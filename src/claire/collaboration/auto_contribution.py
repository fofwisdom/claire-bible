"""Automatic contribution and mutual collaboration (SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md Sections 9.9 and 9.10, CT31, CT37; unit CT-13).

An account sets one rule. The rule sends the documents of the personal knowledge base that fit a target shared knowledge base to that knowledge base, as contributions with the origin ``auto``. The server makes no LLM call for the selection: the score is the document match of Section 8.5.

- The function works only while the server switch ``kb_auto_contribution`` is ``on`` (the default is ``off``).
- Targets of a rule: the shared knowledge bases where the account is an active member (``memberships``), the ones that a mutual collaborator founded (``mutual``), and the ones of the themes of the rule (``themes``).
- A run makes the contributions and their merge jobs. The caller runs the merge jobs.

Targets on other servers (KNOWLEDGE_FEDERATION_DESIGN.md Section 12.3, unit KF5). The rule can include shared knowledge bases of other servers; each one is a record of ``knowledge_federation.remote_kbs``:

- ``themes``: the remote knowledge bases that are members (not excluded) of a theme of the rule.
- ``memberships``: the remote knowledge bases that gave the account the role collaborator with a grant in its term (federation design Section 12.2). A remote account is never a member, so the grant is its membership.
- ``mutual``: no remote target. A mutual collaboration between accounts of two servers needs an agreement of the two accounts, and the vocabulary has no activity for it (federation design Section 9.3).

A candidate for a remote target has a score with the signature of the record (else with the signature of the local theme that has the record as a member); the duplicate and tombstone checks are on the other server. A run sends one ``Offer`` with ``claire:automatic`` for each candidate (``knowledge_federation.offers.send``), with the limits of a local target: ``auto_run_documents`` for each run and target, the reputation level of the account on this server, and the pending limit of the level (the offers that wait for an answer). A target whose record says that its server accepts no automatic contributions now gets no offer (mode ``closed``). The sender needs the switch ``kb_auto_contribution`` (``switch_on``), the switch ``federation``, the canonical origin of this server, and an account that is a federation actor.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from typing import Any

from . import contributions, reputation, theme_judgement
from ..core import ids
from ..store import db as dbm
from ..store import kb_collab
from ..store import themes as themes_store

log = logging.getLogger("claire.auto_contribution")

SWITCH_KEY = "kb_auto_contribution"
# Admin settings in ``server_settings``. ``auto_match_threshold`` is not calibrated (see ``theme_judgement``).
SETTINGS: dict[str, float] = {"auto_match_threshold": 0.5, "auto_run_documents": 50}
# ``documents.source_type`` of a pasted text. Such a document never goes by the rule.
PASTED_TEXT = "text"
# Can a rule have targets on other servers now (``remote_state``)? ``on``, or the reason that it cannot.
REMOTE_STATES = ("on", "no_origin", "not_a_federation_actor")
# The modes of a target (rule 3): ``approved`` (a collaborator or a moderator; on another server, an account with a grant in its term, and the other server decides at the end), ``direct`` (a member), ``pending``; and ``closed`` for a target on a server that accepts no automatic contributions now.
MODES = ("approved", "direct", "pending", "closed")


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
    for theme_id in {_live_theme(conn, int(t)) for t in rule["theme_ids"]} - {None}:
        found.update(int(m["kb_id"]) for m in themes_store.theme_members(conn, theme_id))
    banned = set(kb_collab.account_memberships(conn, account_id, state="banned"))
    return sorted(kb_id for kb_id in found if kb_id not in banned and _shared_kb(conn, kb_id) is not None)


def _live_theme(conn: sqlite3.Connection, theme_id: int) -> int | None:
    """The active theme that a theme of a rule means now: the theme itself, or the target of its merges (design Section 11.2: "a merged theme is replaced by its target at each run"). None when there is no active theme at the end, or when the merges make a loop."""
    seen: set[int] = set()
    while theme_id not in seen:
        seen.add(theme_id)
        theme = themes_store.get_theme(conn, theme_id)
        if theme is None:
            return None
        if theme["state"] == "active":
            return theme_id
        if theme["state"] != "merged" or theme["merged_into_theme_id"] is None:
            return None
        theme_id = int(theme["merged_into_theme_id"])
    return None


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


def personal_document_keys(conn: sqlite3.Connection, personal_kb_id: int) -> dict[str, set[tuple[str, str]]]:
    """The keys of each visible document of the personal knowledge base, from one read of its entities. A run computes this one time and uses it for each target."""
    return theme_judgement._document_keys(conn, int(personal_kb_id))


def candidates(conn: sqlite3.Connection, *, account_id: int, personal_kb_id: int, kb_id: int, include_uploads: bool, documents: list[str] | None = None, matcher: theme_judgement.DocumentMatcher | None = None, settings: dict[str, float] | None = None, document_keys: dict[str, set[tuple[str, str]]] | None = None) -> list[dict[str, Any]]:
    """The documents of the personal knowledge base that fit K, with the highest score first (Section 9.9 rule 2): ``{"document", "title", "score"}``.

    Not candidates: a hidden document; a document with the flag "never contribute"; a pasted text; an uploaded file unless ``include_uploads``; a document with a contribution in K in any state; a document with a duplicate or a tombstone in K.
    ``documents``: only these documents are checked (after the extraction of a new document).
    ``document_keys``: the result of ``personal_document_keys`` for this personal knowledge base, when the caller has it (a run with more than one target); else this call reads it. The score comes first; the checks that read the database for each document (extraction, duplicate, tombstone) run only for a document at or over the threshold.
    """
    matcher = matcher or theme_judgement.DocumentMatcher.load(conn)
    settings = settings or read_settings(conn)
    score = _scorer(conn, matcher, kb_id)
    if score is None:
        return []
    if document_keys is None:
        document_keys = personal_document_keys(conn, personal_kb_id)
    done = kb_collab.documents_contributed_to(conn, kb_id, account_id)
    out: list[dict[str, Any]] = []
    for row, value in _over_threshold(conn, account_id=account_id, personal_kb_id=personal_kb_id, done=done, include_uploads=include_uploads, documents=documents, score=score, settings=settings, document_keys=document_keys):
        if contributions.find_duplicate(conn, kb_id, canonical_url=row["canonical_url"], content_hash=row["content_hash"]) is not None:
            continue
        by_url = bool(row["canonical_url"])
        if dbm.is_tombstoned(conn, canonical_url=row["canonical_url"] if by_url else None, content_hash=None if by_url else row["content_hash"], kb_id=int(kb_id)):
            continue
        out.append({"document": str(row["id"]), "title": row["title"], "score": value})
    out.sort(key=lambda c: (-c["score"], c["document"]))
    return out


def _over_threshold(conn: sqlite3.Connection, *, account_id: int, personal_kb_id: int, done: set[str], include_uploads: bool, documents: list[str] | None, score: Any, settings: dict[str, float], document_keys: dict[str, set[tuple[str, str]]]) -> list[tuple[sqlite3.Row, float]]:
    """The visible documents of the personal knowledge base with a score at or over ``auto_match_threshold`` and a stored extraction, without the documents of ``done``, the documents with the flag "never contribute", the pasted texts, and (unless ``include_uploads``) the uploaded files. The checks of the target come after this step."""
    blocked = kb_collab.contribution_blocks(conn, account_id)
    sql = "SELECT id, title, url, canonical_url, content_hash, source_type FROM documents WHERE kb_id=? AND hidden=0 AND coalesce(extraction_status, 'extracted') != 'pending'"
    args: list[Any] = [int(personal_kb_id)]
    if documents is not None:
        if not documents:
            return []
        sql += f" AND id IN ({', '.join('?' for _ in documents)})"
        args.extend(documents)
    out: list[tuple[sqlite3.Row, float]] = []
    for row in conn.execute(sql, args).fetchall():
        document_id = str(row["id"])
        if document_id in blocked or document_id in done or row["source_type"] == PASTED_TEXT:
            continue
        if not str(row["url"] or "").strip() and not include_uploads:
            continue  # a document with no URL comes from an uploaded file
        value = score(document_keys.get(document_id, set()))
        if value < settings["auto_match_threshold"]:
            continue
        if not conn.execute("SELECT 1 FROM extractions WHERE document_id=? AND kb_id=? LIMIT 1", (document_id, int(personal_kb_id))).fetchone():
            continue
        out.append((row, value))
    return out


# --- targets on other servers (federation design Section 12.3, unit KF5) ------------------------------------------


def remote_state(conn: sqlite3.Connection, account_id: int, origin: str | None) -> str:
    """``on`` when the rule of the account can have targets on other servers now, else the reason (one of ``REMOTE_STATES``): this server has no canonical origin (``no_origin``), or the account has no actor that can sign an ``Offer`` (``not_a_federation_actor``: a public profile and a USERNAME, federation design Section 12.1 rule 2)."""
    from ..federation.knowledge import actors

    if origin is None:
        return "no_origin"
    if actors.account_actor_iri(conn, account_id, origin) is None:
        return "not_a_federation_actor"
    return "on"


def remote_targets(conn: sqlite3.Connection, account_id: int, rule: dict[str, Any], *, now: float) -> list[Any]:
    """The records of the remote knowledge bases that the rule selects (federation design Section 12.3 rule 2), by the number of the record: with ``memberships`` the ones that gave the account a grant in its term, and the members (not excluded) of the themes of the rule. A record of a blocked domain is no target. The caller checks ``remote_state`` first."""
    from ..federation.knowledge import grants, remote_kbs
    from ..store import federation as federation_store

    found: dict[int, Any] = {}
    if rule["to_memberships"]:
        for iri in grants.kb_iris_in_effect(conn, account_id=account_id, now=now):
            record = remote_kbs.get_by_iri(conn, iri)
            if record is not None:
                found[record.id] = record
    for theme_id in {_live_theme(conn, int(t)) for t in rule["theme_ids"]} - {None}:
        for member in themes_store.remote_members(conn, theme_id=theme_id):
            if int(member["remote_kb_id"]) not in found:
                record = remote_kbs.get(conn, int(member["remote_kb_id"]))
                if record is not None:
                    found[record.id] = record
    return [found[key] for key in sorted(found) if federation_store.blocking_domain(conn, found[key].domain) is None]


def _remote_scorer(conn: sqlite3.Connection, matcher: theme_judgement.DocumentMatcher, record: Any):
    """The score function of a remote target: the signature of the record (federation design Section 12.3 rule 2), else the signature of a local theme that has the record as a member (Section 8.5). None when it has none of them: the seed entities of a remote knowledge base do not come to this server."""
    from ..federation.knowledge import remote_kbs

    keys = {(etype, norm) for etype, norm, _weight in remote_kbs.signature_rows(record)}
    if keys:
        return lambda document_keys: matcher.score(document_keys, keys)
    theme_ids = {int(m["theme_id"]) for m in themes_store.remote_members(conn) if int(m["remote_kb_id"]) == int(record.id)}
    theme_keys = [theme["keys"] for theme in matcher.themes if theme["id"] in theme_ids]
    if theme_keys:
        return lambda document_keys: max(matcher.score(document_keys, k) for k in theme_keys)
    return None


def remote_candidates(conn: sqlite3.Connection, *, account_id: int, personal_kb_id: int, record: Any, include_uploads: bool, documents: list[str] | None = None, matcher: theme_judgement.DocumentMatcher | None = None, settings: dict[str, float] | None = None, document_keys: dict[str, set[tuple[str, str]]] | None = None) -> list[dict[str, Any]]:
    """The documents of the personal knowledge base that fit a remote knowledge base, with the highest score first: ``{"document", "title", "score"}`` (federation design Section 12.3 rule 2, shared knowledge base design Section 9.9 rule 2).

    Not candidates: the documents of ``candidates`` that are not candidates (hidden, "never contribute", pasted text, uploaded file unless ``include_uploads``, no extraction), and each document with a row of ``kf_outbound_offers`` for that knowledge base (``offers.automatic_offered``: offered, accepted, rejected, removed, or skipped for a reason of the document). The duplicate and tombstone checks are on the other server: it rejects such an offer with ``duplicate``.
    """
    from ..federation.knowledge import offers

    matcher = matcher or theme_judgement.DocumentMatcher.load(conn)
    settings = settings or read_settings(conn)
    score = _remote_scorer(conn, matcher, record)
    if score is None:
        return []
    if document_keys is None:
        document_keys = personal_document_keys(conn, personal_kb_id)
    done = offers.automatic_offered(conn, account_id=account_id, kb_iri=record.iri)
    out = [{"document": str(row["id"]), "title": row["title"], "score": value} for row, value in _over_threshold(conn, account_id=account_id, personal_kb_id=personal_kb_id, done=done, include_uploads=include_uploads, documents=documents, score=score, settings=settings, document_keys=document_keys)]
    out.sort(key=lambda c: (-c["score"], c["document"]))
    return out


# --- run -------------------------------------------------------------------------------------------------------------


def plan(conn: sqlite3.Connection, account_id: int, rule: dict[str, Any], *, origin: str | None, documents: list[str] | None = None, now: float | None = None) -> list[dict[str, Any]]:
    """What a run of the rule sends now (also the dry run of Section 9.9 rule 6). It writes nothing.

    A target of this server: ``{"kb_id", "mode", "documents": [...]}``. A target on another server (federation design Section 12.3): ``{"remote_id", "kb_iri", "server", "label", "url", "mode", "documents": [...]}``; ``origin`` is the canonical origin of this server, or None when it has none (then no remote target).
    ``mode`` (rule 3): ``approved`` for a collaborator or a moderator (on another server: a grant in its term), ``direct`` for a member, ``pending`` for each other account, and ``closed`` for a remote target whose server accepts no automatic contributions now (a ``closed`` target is in the result with no documents, so that the dry run shows it). The reputation applies (Section 8.6): with ``low`` the rule is paused, and with ``limited`` a run sends fewer documents. The pending limit of the level also limits a ``pending`` target.
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
    document_keys: dict[str, set[tuple[str, str]]] | None = None
    out = []
    for kb_id in targets(conn, account_id, rule):
        if document_keys is None:
            document_keys = personal_document_keys(conn, int(personal[0]))  # one read for all targets of the run
        membership = kb_collab.get_membership(conn, kb_id, account_id)
        active = membership is not None and membership["state"] == "active"
        mode = "approved" if active and membership["role"] in ("collaborator", "moderator") else ("direct" if active else "pending")
        found = candidates(conn, account_id=account_id, personal_kb_id=int(personal[0]), kb_id=kb_id, include_uploads=bool(rule["include_uploads"]), documents=documents, matcher=matcher, settings=settings, document_keys=document_keys)[:run_limit]
        if mode == "pending":
            room = reputation.pending_limit(reputation_settings, level) - kb_collab.pending_count(conn, kb_id=kb_id, account_id=account_id)
            found = found[: max(room, 0)]
        if found:
            out.append({"kb_id": kb_id, "mode": mode, "documents": found})
    if remote_state(conn, account_id, origin) != "on":
        return out
    from ..federation.knowledge import grants, offers, remote_kbs

    ts = time.time() if now is None else now
    for record in remote_targets(conn, account_id, rule, now=ts):
        target = {"remote_id": record.id, "kb_iri": record.iri, "server": record.domain, "label": record.label, "url": record.url}
        if remote_kbs.accepts_automatic(record) is False:
            out.append({**target, "mode": "closed", "documents": []})
            continue
        if document_keys is None:
            document_keys = personal_document_keys(conn, int(personal[0]))
        mode = "approved" if grants.capability_for(conn, account_id=account_id, kb_iri=record.iri, now=ts) is not None else "pending"
        found = remote_candidates(conn, account_id=account_id, personal_kb_id=int(personal[0]), record=record, include_uploads=bool(rule["include_uploads"]), documents=documents, matcher=matcher, settings=settings, document_keys=document_keys)[:run_limit]
        if mode == "pending":
            # The pending limit of the sender counts the offers to that knowledge base that wait for an answer (federation implementation Section 13.8 item 4).
            room = reputation.pending_limit(reputation_settings, level) - offers.pending_outbound(conn, account_id=account_id, kb_iri=record.iri)
            found = found[: max(room, 0)]
        if found:
            out.append({**target, "mode": mode, "documents": found})
    return out


def run_account(conn: sqlite3.Connection, account_id: int, *, origin: str | None, documents: list[str] | None = None, now: float | None = None) -> int:
    """One run of the rule of an account. Return the count of new contributions and of the offers that went to other servers. Each contribution and each offer is its own transaction (a commit after each one); the caller runs the merge jobs.

    Nothing runs while the server switch is off, without an active rule, or for an account that is not active. ``origin``: the canonical origin of this server (``contributions.canonical_origin``), or None when it has none; then the run has no target on another server. A remote target gets one ``Offer`` with ``claire:automatic`` for each candidate (``offers.send`` with no submission item); a ``closed`` target gets none.
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
    for target in plan(conn, account_id, rule, origin=origin, documents=documents, now=ts):
        if "remote_id" in target:
            made += _send_remote(conn, account_id=int(account_id), personal_kb_id=int(personal[0]), target=target, origin=origin, now=ts)
            continue
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


def _send_remote(conn: sqlite3.Connection, *, account_id: int, personal_kb_id: int, target: dict[str, Any], origin: str | None, now: float) -> int:
    """Send the ``Offer`` of each document of one remote target of ``plan``. Return the number of offers that went to the outbox. A target that was skipped (``offers.send`` checks the account, the domain, the pending limit, and the limits of one document again) is a row with its reason."""
    from ..federation.knowledge import offers, remote_kbs

    if target["mode"] == "closed" or origin is None:
        return 0
    record = remote_kbs.get(conn, int(target["remote_id"]))
    if record is None:
        return 0
    pending_limit = reputation.pending_limit(reputation.read_settings(conn), reputation.level(conn, account_id, now=now))
    sent = 0
    for candidate in target["documents"]:
        try:
            row = offers.send(conn, account_id=account_id, source_kb_id=personal_kb_id, source_document_id=candidate["document"], remote_kb=record, submission_item_id=None, pending_limit=pending_limit, automatic=True, origin=origin, now=now)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        if row["state"] == "pending":
            sent += 1
        else:
            log.info("automatic offer of %s to %s did not go: %s", candidate["document"], record.iri, row["reason_code"])
    return sent


def run_all(conn: sqlite3.Connection, *, origin: str | None, now: float | None = None) -> int:
    """A run for each account with an active rule (after a judgement run, and when a shared knowledge base gets a signature or seed entities). A failure of one account does not stop the others. ``origin`` as in ``run_account``."""
    if not switch_on(conn):
        return 0
    made = 0
    for account_id in kb_collab.accounts_with_active_rule(conn):
        try:
            made += run_account(conn, account_id, origin=origin, now=now)
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
