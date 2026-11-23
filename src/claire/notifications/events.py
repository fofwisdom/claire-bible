"""The events that make notifications (PWA_NOTIFICATION_DESIGN.md Section 5): one function for each kind.

- A function finds the recipients, the names for the text, and the entry address, then calls ``emit``. Call it after the commit of the change (PN2 rule 2).
- A function never raises (PN2 rule 1).
- ``after_commit`` holds the calls of the current thread until its block ends without an exception. A handler that changes the database in a transaction of its caller puts its calls in such a block: a transaction that fails makes no notification.
- ``scan_pending_contributions`` reads the contributions that wait for review. Contributions come from many paths (pages, MCP tools, the CLI, the Telegram bot, automatic rules, offers of other servers); the scan of the committed state covers all of them (PN2 rule 5).
"""

from __future__ import annotations

import functools
import logging
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Generator, Iterable
from urllib.parse import urlsplit

from ..core import ids
from ..store import client_db
from ..store import db as dbm
from ..store import notifications as store
from . import emit, kb_people_managers, kb_reviewers

__all__ = [
    "actor_handle",
    "after_commit",
    "agent_extraction_done",
    "agent_source_staged",
    "agent_write_grant",
    "ap_follow_answered",
    "ap_follower",
    "ap_mention",
    "ap_private_message",
    "ap_quote_answered",
    "ap_quote_request",
    "kb_contribution_decided",
    "kb_join_approved",
    "kb_join_request",
    "kb_role_given",
    "kf_grant",
    "kf_offer_answered",
    "mutual_accepted",
    "mutual_requested",
    "scan_pending_contributions",
]

log = logging.getLogger(__name__)

SETTINGS_KB_MEMBERSHIP = "/settings/kb?tab=mem"
SETTINGS_KB_SUBMISSIONS = "/settings/kb?tab=subs"
ACCOUNT_MCP = "/account/mcp"
ACCOUNT_FEDERATION = "/account/federation"
SYNDICATION_CLIENT = "/syndication?stage=collect"
SCAN_SCOPE_CONTRIBUTION = "contribution"

_held = threading.local()


@contextmanager
def after_commit() -> Generator[None]:
    """Hold the event calls of this thread until the block ends. An exception in the block drops them. A block inside a block adds to the outer block."""
    if getattr(_held, "calls", None) is not None:
        yield
        return
    calls: list[Callable[[], None]] = []
    _held.calls = calls
    try:
        yield
    finally:
        _held.calls = None
    for call in calls:
        call()


def _event(func: Callable[..., None]) -> Callable[..., None]:
    """An event function: held in an ``after_commit`` block, and never an exception for the caller."""

    @functools.wraps(func)
    def run(*args: Any, **kwargs: Any) -> None:
        def call() -> None:
            try:
                func(*args, **kwargs)
            except Exception:  # noqa: BLE001 - PN2 rule 1: the caller continues.
                log.warning("notification event failed: %s", getattr(func, "__name__", "event"), exc_info=True)

        held = getattr(_held, "calls", None)
        if held is not None:
            held.append(call)
        else:
            call()

    return run


def actor_handle(iri: str | None) -> str:
    """A short text for an actor or a knowledge base of another server: the last path part and the host (``alice@social.example``), or the host. It is a label for a person, not an address that the code uses."""
    if not iri:
        return ""
    try:
        parts = urlsplit(iri)
    except ValueError:
        return ""
    host = parts.hostname or ""
    name = next((p for p in reversed(parts.path.split("/")) if p), "").lstrip("@")
    if not host:
        return ""
    return f"{name}@{host}" if name and not name.isdigit() else host


def _open(db_file: Path | str) -> sqlite3.Connection:
    return dbm.connect_existing(db_file, readonly=True)


def _kb(conn: sqlite3.Connection, kb_id: int) -> tuple[str, str]:
    """The label and the number text of a knowledge base."""
    row = conn.execute("SELECT label, number FROM knowledge_bases WHERE id=?", (int(kb_id),)).fetchone()
    if row is None or row[1] is None:
        raise LookupError(f"knowledge base {kb_id} has no number")
    return str(row[0]), ids.format_public_id(int(row[1]))


def _account_name(conn: sqlite3.Connection, account_id: int | None) -> str:
    if account_id is None:
        return ""
    row = conn.execute("SELECT display_name, username FROM accounts WHERE id=?", (int(account_id),)).fetchone()
    if row is None:
        return ""
    return str(row[0] or "").strip() or (f"@{row[1]}" if row[1] else "")


def _moderation_path(number: str, tab: str) -> str:
    return f"/moderation/kb/{number}?tab={tab}"


# --- Platform ---


@_event
def kb_join_request(db_file: Path | str, *, kb_id: int, actor_account_id: int) -> None:
    conn = _open(db_file)
    try:
        label, number = _kb(conn, kb_id)
        recipients = kb_people_managers(conn, kb_id)
        actor = _account_name(conn, actor_account_id)
    finally:
        conn.close()
    emit(db_file, kind="kb_join_request", account_ids=recipients, actor_account_id=actor_account_id, target=_moderation_path(number, "people"), params={"actor": actor, "kb": label}, group=f"kb:{kb_id}")


@_event
def kb_join_approved(db_file: Path | str, *, kb_id: int, account_id: int, actor_account_id: int) -> None:
    conn = _open(db_file)
    try:
        label, _number = _kb(conn, kb_id)
    finally:
        conn.close()
    emit(db_file, kind="kb_join_approved", account_ids=[account_id], actor_account_id=actor_account_id, target=SETTINGS_KB_MEMBERSHIP, params={"kb": label})


@_event
def kb_role_given(db_file: Path | str, *, kb_id: int, account_ids: Iterable[int], role: str, actor_account_id: int | None) -> None:
    conn = _open(db_file)
    try:
        label, _number = _kb(conn, kb_id)
    finally:
        conn.close()
    emit(db_file, kind="kb_role_given", account_ids=account_ids, actor_account_id=actor_account_id, target=SETTINGS_KB_MEMBERSHIP, params={"kb": label, "variant": role})


@_event
def kb_contribution_decided(db_file: Path | str, *, kb_id: int, contributor_account_id: int | None, decision: str, actor_account_id: int | None) -> None:
    """``decision``: ``approved``, ``rejected``, or ``removed``. A contribution of an account of another server has no contributor account: its server gets the answer as an activity."""
    if contributor_account_id is None:
        return
    conn = _open(db_file)
    try:
        label, _number = _kb(conn, kb_id)
    finally:
        conn.close()
    emit(db_file, kind="kb_contribution_decided", account_ids=[contributor_account_id], actor_account_id=actor_account_id, target=SETTINGS_KB_SUBMISSIONS, params={"kb": label, "variant": decision}, group=f"kb:{kb_id}:{decision}")


@_event
def mutual_requested(db_file: Path | str, *, requester_account_id: int, other_account_id: int) -> None:
    conn = _open(db_file)
    try:
        actor = _account_name(conn, requester_account_id)
    finally:
        conn.close()
    emit(db_file, kind="mutual_requested", account_ids=[other_account_id], actor_account_id=requester_account_id, target=SETTINGS_KB_MEMBERSHIP, params={"actor": actor})


@_event
def mutual_accepted(db_file: Path | str, *, accepter_account_id: int, requester_account_id: int) -> None:
    conn = _open(db_file)
    try:
        actor = _account_name(conn, accepter_account_id)
    finally:
        conn.close()
    emit(db_file, kind="mutual_accepted", account_ids=[requester_account_id], actor_account_id=accepter_account_id, target=SETTINGS_KB_MEMBERSHIP, params={"actor": actor})


# --- Agent ---


def _kb_owner(conn: sqlite3.Connection, kb_id: int) -> int | None:
    row = conn.execute("SELECT account_id FROM knowledge_bases WHERE id=? AND is_personal=1", (int(kb_id),)).fetchone()
    return None if row is None or row[0] is None else int(row[0])


@_event
def agent_source_staged(db_file: Path | str, *, kb_id: int) -> None:
    """A source of a personal knowledge base is staged and waits for an agent client. The caller is a path where a person, a feed, or the Telegram bot started the ingest; an agent that stages its own source needs no notification."""
    conn = _open(db_file)
    try:
        owner = _kb_owner(conn, kb_id)
    finally:
        conn.close()
    emit(db_file, kind="agent_source_staged", account_ids=[owner], target=ACCOUNT_MCP, group="staged")


@_event
def agent_extraction_done(db_file: Path | str, *, kb_id: int, document_id: str, agent: str) -> None:
    conn = _open(db_file)
    try:
        owner = _kb_owner(conn, kb_id)
        if owner is None:
            return
        row = conn.execute("SELECT username FROM accounts WHERE id=?", (owner,)).fetchone()
        mode_row = conn.execute("SELECT value FROM server_settings WHERE key='mode'").fetchone()
    finally:
        conn.close()
    username = None if row is None else row[0]
    # The home of the account (ADMIN_AND_SETTINGS_PATH_SEPARATION_DESIGN.md): "/" in single mode, "/@{username}/" in network mode.
    home = f"/@{username}/" if mode_row is not None and str(mode_row[0]) == "network" and username else "/"
    # The entry address is the permalink of the document (PERMALINK_UNIFICATION_DESIGN.md U5). Without an address token (the database cannot be written), it is the home of the account.
    from ..store import permalinks

    token = permalinks.ensure_document_token(db_file, str(document_id), int(kb_id))
    emit(db_file, kind="agent_extraction_done", account_ids=[owner], target=home if token is None else permalinks.permalink_path(token), params={"agent": agent or "agent"}, group="extracted")


@_event
def agent_write_grant(db_file: Path | str, *, account_id: int, client: str) -> None:
    emit(db_file, kind="agent_write_grant", account_ids=[account_id], target=ACCOUNT_MCP, params={"client": client})


# --- ActivityPub ---


@_event
def ap_follower(db_file: Path | str, *, account_id: int, actor_iri: str) -> None:
    emit(db_file, kind="ap_follower", account_ids=[account_id], target=ACCOUNT_FEDERATION, params={"actor": actor_handle(actor_iri)}, group="followers")


@_event
def ap_follow_answered(db_file: Path | str, *, account_id: int, actor_iri: str, accepted: bool) -> None:
    emit(db_file, kind="ap_follow_answered", account_ids=[account_id], target=SYNDICATION_CLIENT, params={"actor": actor_handle(actor_iri), "variant": "accepted" if accepted else "rejected"})


@_event
def ap_mention(db_file: Path | str, *, account_ids: Iterable[int], actor_iri: str) -> None:
    emit(db_file, kind="ap_mention", account_ids=account_ids, target=SYNDICATION_CLIENT, params={"actor": actor_handle(actor_iri)}, group="mentions")


@_event
def ap_quote_request(db_file: Path | str, *, account_id: int, actor_iri: str) -> None:
    emit(db_file, kind="ap_quote_request", account_ids=[account_id], target=SYNDICATION_CLIENT, params={"actor": actor_handle(actor_iri)}, group="quotes")


@_event
def ap_quote_answered(db_file: Path | str, *, account_id: int, actor_iri: str, approved: bool) -> None:
    emit(db_file, kind="ap_quote_answered", account_ids=[account_id], target=SYNDICATION_CLIENT, params={"actor": actor_handle(actor_iri), "variant": "approved" if approved else "refused"})


@_event
def ap_private_message(db_file: Path | str, *, account_ids: Iterable[int], actor_iri: str) -> None:
    emit(db_file, kind="ap_private_message", account_ids=account_ids, target=SYNDICATION_CLIENT, params={"actor": actor_handle(actor_iri)}, group="private")


@_event
def kf_offer_answered(db_file: Path | str, *, account_id: int | None, kb_iri: str, accepted: bool) -> None:
    variant = "accepted" if accepted else "rejected"
    emit(db_file, kind="kf_offer_answered", account_ids=[account_id], target=SETTINGS_KB_SUBMISSIONS, params={"kb": actor_handle(kb_iri), "variant": variant}, group=f"kb:{kb_iri}:{variant}")


@_event
def kf_grant(db_file: Path | str, *, account_id: int | None, kb_iri: str, given: bool) -> None:
    emit(db_file, kind="kf_grant", account_ids=[account_id], target=SETTINGS_KB_MEMBERSHIP, params={"kb": actor_handle(kb_iri), "variant": "given" if given else "revoked"})


# --- Scan ---


def scan_pending_contributions(db_file: Path | str) -> int:
    """Make the notifications of the contributions that wait for review and that no scan told about (kinds ``kb_contribution_pending`` and ``kf_contribution_pending``). Return the count of new contributions.

    The first scan records the contributions that wait and tells about none: an update of the server makes no notification for old items. A contribution without a contributor account came from another server.
    """
    db_path = Path(db_file)
    client_file = client_db.client_db_path(db_path)
    if not client_file.exists():
        return 0
    conn = _open(db_path)
    try:
        columns = {r[1] for r in conn.execute("PRAGMA table_info(kb_contributions)")}
        credit = "remote_credit_iri" if "remote_credit_iri" in columns else "NULL"
        rows = conn.execute(f"SELECT public_id, kb_id, contributor_account_id, {credit} FROM kb_contributions WHERE state='pending' ORDER BY public_id").fetchall()
        client = client_db.connect_client_existing(client_file)
        try:
            seen = store.scan_seen(client, SCAN_SCOPE_CONTRIBUTION)
            waiting = {int(r[0]) for r in rows}
            fresh = [r for r in rows if seen is None or int(r[0]) not in seen]
            if seen is None or fresh or seen - waiting:
                store.set_scan_seen(client, SCAN_SCOPE_CONTRIBUTION, add=[int(r[0]) for r in fresh], remove=(seen or set()) - waiting)
                client.commit()
        finally:
            client.close()
        if seen is None or not fresh:
            return 0
        # One emit for each knowledge base and source: the notification gets the count of its new contributions, and the name of the last contributor.
        groups: dict[tuple[int, bool], list[sqlite3.Row]] = {}
        for row in fresh:
            groups.setdefault((int(row[1]), row[2] is None), []).append(row)
        calls: list[dict[str, Any]] = []
        for (kb_id, remote), members in groups.items():
            try:
                label, number = _kb(conn, kb_id)
            except LookupError:
                continue
            last = members[-1]
            actor = actor_handle(last[3]) if remote else _account_name(conn, last[2])
            contributors = {int(m[2]) for m in members if m[2] is not None}
            calls.append({
                "kind": "kf_contribution_pending" if remote else "kb_contribution_pending",
                "account_ids": kb_reviewers(conn, kb_id),
                # A reviewer who sent all contributions of the group needs no notification about them.
                "actor_account_id": next(iter(contributors)) if len(contributors) == 1 else None,
                "target": _moderation_path(number, "queue"),
                "params": {"actor": actor, "kb": label},
                "group": f"kb:{kb_id}",
                "events": len(members),
            })
    finally:
        conn.close()
    for call in calls:
        emit(db_path, **call)
    return len(fresh)
