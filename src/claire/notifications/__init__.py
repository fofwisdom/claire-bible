"""Notifications for linkage requests (PWA_NOTIFICATION_DESIGN.md): the kinds, the emit, the recipients, and the text.

- ``emit`` records one event for each recipient in ``client.db`` and puts the Web Push messages in the queue. It never raises: a request must not fail because a notification failed (PN2 rule 1). Call it after the commit of the change that it tells about (PN2 rule 2).
- A notification stores no text. ``render`` makes the title and the body from the catalog keys ``notify.<kind>.*`` in a given locale.
- The Web Push protocol is in ``webpush.py``; the task that sends is in ``sender.py``; the tables are in ``store/notifications.py``.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Iterable, Literal

from ..i18n import LocaleCode
from ..i18n.catalog import load_catalog, t
from ..store import client_db
from ..store import db as dbm
from ..store import notifications as store

__all__ = [
    "CHANNELS",
    "KINDS",
    "Kind",
    "emit",
    "kb_people_managers",
    "kb_reviewers",
    "render",
]

log = logging.getLogger(__name__)

Channel = Literal["platform", "agent", "activitypub", "system"]
KindClass = Literal["request", "answer", "notice"]
# The order of the groups on /settings/notifications. ``system`` has only the test notification and no switch.
CHANNELS: Final[tuple[str, ...]] = ("platform", "agent", "activitypub")
# A notification is sent again to the push service 60 s after its last send at the earliest (PN3 rule 4).
PUSH_MIN_INTERVAL_SECONDS: Final[float] = 60.0
PARAM_MAX_LENGTH: Final[int] = 120


@dataclass(frozen=True, slots=True)
class Kind:
    """One kind of notification (design Section 5). ``variants`` are the values of the parameter ``variant`` that select the body text; a kind without variants has one body."""

    name: str
    channel: Channel
    klass: KindClass
    variants: tuple[str, ...] = ()


KINDS: Final[dict[str, Kind]] = {
    kind.name: kind
    for kind in (
        Kind("kb_join_request", "platform", "request"),
        Kind("kb_join_approved", "platform", "answer"),
        Kind("kb_contribution_pending", "platform", "request"),
        Kind("kb_contribution_decided", "platform", "answer", ("approved", "rejected", "removed")),
        Kind("kb_role_given", "platform", "notice", ("collaborator", "moderator")),
        Kind("mutual_requested", "platform", "request"),
        Kind("mutual_accepted", "platform", "answer"),
        Kind("agent_source_staged", "agent", "request"),
        Kind("agent_extraction_done", "agent", "answer"),
        Kind("agent_write_grant", "agent", "notice"),
        Kind("ap_follower", "activitypub", "notice"),
        Kind("ap_follow_answered", "activitypub", "answer", ("accepted", "rejected")),
        Kind("ap_mention", "activitypub", "request"),
        Kind("ap_quote_request", "activitypub", "request"),
        Kind("ap_quote_answered", "activitypub", "answer", ("approved", "refused")),
        Kind("ap_private_message", "activitypub", "notice"),
        Kind("kf_contribution_pending", "activitypub", "request"),
        Kind("kf_offer_answered", "activitypub", "answer", ("accepted", "rejected")),
        Kind("kf_grant", "activitypub", "notice", ("given", "revoked")),
        Kind("test", "system", "notice"),
    )
}


def _clean_params(params: dict[str, Any] | None) -> dict[str, str | int]:
    """Parameters are text or whole numbers. Text of another server is data: it is cut to ``PARAM_MAX_LENGTH`` characters and has no control characters."""
    cleaned: dict[str, str | int] = {}
    for key, value in (params or {}).items():
        if value is None:
            continue
        if isinstance(value, bool):
            raise TypeError(f"parameter {key!r} is a boolean")
        if isinstance(value, int):
            cleaned[str(key)] = value
            continue
        text = "".join(ch for ch in str(value) if ch.isprintable()).strip()
        cleaned[str(key)] = text[: PARAM_MAX_LENGTH - 1] + "…" if len(text) > PARAM_MAX_LENGTH else text
    return cleaned


def _active_accounts(db_file: Path | str, account_ids: set[int]) -> set[int]:
    if not account_ids:
        return set()
    conn = dbm.connect_existing(db_file, readonly=True)
    try:
        marks = ",".join("?" for _ in account_ids)
        return {int(r[0]) for r in conn.execute(f"SELECT id FROM accounts WHERE status='active' AND id IN ({marks})", sorted(account_ids))}
    finally:
        conn.close()


def emit(db_file: Path | str, *, kind: str, account_ids: Iterable[int | None], target: str, params: dict[str, Any] | None = None, group: str | None = None, actor_account_id: int | None = None, events: int = 1, now: float | None = None) -> list[int]:
    """Record the event ``kind`` for each account of ``account_ids`` and return the notification numbers.

    ``db_file`` is the file of ``claire.db``; ``client.db`` is next to it. ``target`` is the entry address (a path of this server). ``group`` joins the events of one subject while the notification is not read (PN1 rule 2). ``actor_account_id`` is the account that did the action: it gets no notification (PN2 rule 3). ``events`` is the count of events of the group that this call records.
    """
    try:
        return _emit(Path(db_file), kind=kind, account_ids=account_ids, target=target, params=params, group=group, actor_account_id=actor_account_id, events=events, now=now)
    except Exception:  # noqa: BLE001 - PN2 rule 1: the caller continues.
        log.warning("notification emit failed: kind=%s", kind, exc_info=True)
        return []


def _emit(db_file: Path, *, kind: str, account_ids: Iterable[int | None], target: str, params: dict[str, Any] | None, group: str | None, actor_account_id: int | None, events: int, now: float | None) -> list[int]:
    spec = KINDS[kind]
    if not target.startswith("/") or target.startswith("//") or "\\" in target:
        raise ValueError(f"the entry address must be a path of this server: {target!r}")
    cleaned = _clean_params(params)
    if spec.variants and cleaned.get("variant") not in spec.variants:
        raise ValueError(f"kind {kind} needs the parameter variant, one of {spec.variants}")
    wanted = {int(a) for a in account_ids if a is not None}
    if actor_account_id is not None:
        wanted.discard(int(actor_account_id))
    client_file = client_db.client_db_path(db_file)
    if not wanted or not client_file.exists():
        return []
    recipients = sorted(_active_accounts(db_file, wanted))
    if not recipients:
        return []
    ts = time.time() if now is None else now
    made: list[int] = []
    queued = 0
    conn = client_db.connect_client_existing(client_file)
    try:
        for account_id in recipients:
            notification_id, _created = store.add_notification(conn, account_id=account_id, kind=kind, params=cleaned, target_path=target, group_key=group, events=events, now=ts)
            made.append(notification_id)
            if kind in store.push_off_kinds(conn, account_id):
                continue
            row = conn.execute("SELECT pushed_at FROM notifications WHERE id=?", (notification_id,)).fetchone()
            pushed_at = None if row is None or row[0] is None else float(row[0])
            not_before = ts if pushed_at is None else max(ts, pushed_at + PUSH_MIN_INTERVAL_SECONDS)
            queued += store.enqueue_push(conn, notification_id=notification_id, account_id=account_id, not_before=not_before)
        conn.commit()
    finally:
        conn.close()
    if queued:
        from . import sender

        sender.wake()
    return made


def kb_people_managers(conn: sqlite3.Connection, kb_id: int) -> list[int]:
    """The accounts with the capability ``people`` in a shared knowledge base: its active moderators and the server administrator (``accounts/kb_access.py``). ``conn`` is a connection to ``claire.db``."""
    rows = conn.execute(
        "SELECT a.id FROM accounts a WHERE a.status='active' AND (a.role='admin' OR a.id IN (SELECT m.account_id FROM kb_memberships m WHERE m.kb_id=? AND m.state='active' AND m.role='moderator')) ORDER BY a.id",
        (int(kb_id),),
    ).fetchall()
    return [int(r[0]) for r in rows]


def kb_reviewers(conn: sqlite3.Connection, kb_id: int) -> list[int]:
    """The accounts with the capability ``review`` in a shared knowledge base: its active collaborators and moderators, and the server administrator. ``conn`` is a connection to ``claire.db``."""
    rows = conn.execute(
        "SELECT a.id FROM accounts a WHERE a.status='active' AND (a.role='admin' OR a.id IN (SELECT m.account_id FROM kb_memberships m WHERE m.kb_id=? AND m.state='active' AND m.role IN ('collaborator', 'moderator'))) ORDER BY a.id",
        (int(kb_id),),
    ).fetchall()
    return [int(r[0]) for r in rows]


def _body_key(notification: store.Notification) -> str:
    base = f"notify.{notification.kind}.body"
    spec = KINDS.get(notification.kind)
    if spec is not None and spec.variants:
        variant = str(notification.params.get("variant", ""))
        if variant in spec.variants:
            base = f"{base}.{variant}"
    many = f"{base}_many"
    if notification.count > 1 and many in load_catalog("en"):
        return many
    return base


def render(notification: store.Notification, locale: LocaleCode) -> tuple[str, str]:
    """The title and the body of a notification in ``locale``. A kind that this code does not know (a row of a newer version) gives the general text."""
    if notification.kind not in KINDS:
        return t("notify.unknown.title", locale=locale), t("notify.unknown.body", locale=locale)
    params = {**notification.params, "count": notification.count}
    return t(f"notify.{notification.kind}.title", locale=locale, **params), t(_body_key(notification), locale=locale, **params)
