"""The task that sends Web Push messages (PWA_NOTIFICATION_DESIGN.md PN3).

- ``run_scheduler`` is a task of the ``api`` process. ``wake`` (called by ``emit`` in the same process) starts a send at once; the task also runs every ``SCHEDULER_INTERVAL`` seconds: it scans the contributions that wait for review (``events.scan_pending_contributions``) and reads the queue for the emits of other processes.
- ``send_due`` reads the queue rows whose time came and sends each message. The text of a message is made at the send, in the locale of the account.
- A subscription whose session is gone, or whose account is not active, is deleted before a send (PN3 rule 7).
- Each request goes to an ``https`` endpoint whose addresses are all public, and follows no redirect (PN3 rule 6).
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Final

import httpx

from ..ingest.fetchers.base import FetchError
from ..ingest.fetchers.http import guarded_client
from ..ops.notify import push_locale
from ..store import accounts as accounts_store
from ..store import auth_db, client_db
from ..store import db as dbm
from ..store import notifications as store
from ..store.secrets import get_secret_service
from . import KINDS, render, webpush

__all__ = ["SendReport", "run_scheduler", "send_due", "vapid_subject", "wake"]

log = logging.getLogger(__name__)

SCHEDULER_INTERVAL: Final[float] = 5.0
CLEANUP_INTERVAL: Final[float] = 3600.0
REQUEST_TIMEOUT: Final[float] = 10.0
PUSH_TTL_SECONDS: Final[int] = 86400
# The waits before the second, third, and fourth attempt of one message. After the fourth attempt the message is given up.
RETRY_DELAYS: Final[tuple[float, ...]] = (30.0, 120.0, 600.0)
# A subscription with this count of failures in sequence is deleted.
MAX_SUBSCRIPTION_FAILURES: Final[int] = 5
PAYLOAD_MAX_BYTES: Final[int] = 3000
_BATCH: Final[int] = 100

Post = Callable[[webpush.PushRequest], int]

_loop: asyncio.AbstractEventLoop | None = None
_wake_event: asyncio.Event | None = None


@dataclass(slots=True)
class SendReport:
    sent: int = 0
    retried: int = 0
    dropped: int = 0
    removed_subscriptions: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.sent + self.retried + self.dropped + self.removed_subscriptions


def wake() -> None:
    """Start a send at once. Safe from each thread. Without a running scheduler (another process, a test) it does nothing: the scheduler of the ``api`` process reads the queue at its next interval."""
    loop, event = _loop, _wake_event
    if loop is None or event is None or loop.is_closed():
        return
    try:
        loop.call_soon_threadsafe(event.set)
    except RuntimeError:
        pass


def vapid_subject(settings: Any) -> str | None:
    """The ``sub`` claim of the VAPID token: the canonical origin of the server, or ``None`` when the settings give none (PN3 rule 8)."""
    from ..collaboration.contributions import canonical_origin

    return canonical_origin(settings)


def _default_post(request: webpush.PushRequest) -> int:
    # allow_private_networks False and an empty allowlist: the private network settings of the fetchers do not apply to a push service.
    with guarded_client(allow_private_networks=False, private_network_allowlist=[], follow_redirects=False, timeout=REQUEST_TIMEOUT) as client:
        return client.post(request.endpoint, content=request.body, headers=request.headers).status_code


def _live_session_refs(claire_conn: sqlite3.Connection, account_id: int, now: float) -> set[str] | None:
    """The credential identifiers of the sessions of an active account that are in effect. ``None`` when the account is not active."""
    row = claire_conn.execute("SELECT status FROM accounts WHERE id=?", (account_id,)).fetchone()
    if row is None or str(row[0]) != "active":
        return None
    rows = claire_conn.execute("SELECT nonce FROM auth_sessions WHERE account_id=? AND approved=1 AND expires_at>=?", (account_id, now)).fetchall()
    return {accounts_store._credential_id(str(r[0])) for r in rows}


def _payload(notification: store.Notification, *, locale: Any, unread: int) -> bytes:
    title, body = render(notification, locale)
    data = {"id": notification.id, "kind": notification.kind, "title": title, "body": body, "url": notification.target_path, "tag": f"claire-{notification.id}", "unread": unread}
    raw = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(raw) > PAYLOAD_MAX_BYTES:
        data["body"] = ""
        raw = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return raw


def _vapid_private_key(settings: Any) -> str:
    conn = auth_db.connect_auth_db(auth_db.auth_db_path(settings.data_dir))
    try:
        return store.ensure_vapid_private_key(conn, get_secret_service())
    finally:
        conn.close()


def send_due(settings: Any, *, post: Post | None = None, now: float | None = None) -> SendReport:
    """Send the messages of the queue whose time came. ``post`` sends one request and returns the status code (a test gives it; the default uses the guarded HTTP client)."""
    report = SendReport()
    client_file = client_db.client_db_path(settings.db_file)
    if not client_file.exists():
        return report
    ts = time.time() if now is None else now
    send = post or _default_post
    conn = client_db.connect_client_existing(client_file)
    try:
        jobs = store.due_push_jobs(conn, now=ts, limit=_BATCH)
        conn.commit()
        if not jobs:
            return report
        subject = vapid_subject(settings)
        if subject is None:
            for job in jobs:
                store.finish_push_job(conn, job.notification.id, job.subscription.id)
            conn.commit()
            report.dropped = len(jobs)
            report.errors.append("no_public_origin")
            return report
        private_key = _vapid_private_key(settings)
        claire_conn = dbm.connect_existing(settings.db_file, readonly=True)
        try:
            accounts = {job.subscription.account_id for job in jobs}
            live = {account_id: _live_session_refs(claire_conn, account_id, ts) for account_id in accounts}
            locales = {int(r[0]): r[1] for r in claire_conn.execute(f"SELECT id, locale FROM accounts WHERE id IN ({','.join('?' for _ in accounts)})", sorted(accounts))}
        finally:
            claire_conn.close()
        removed: set[int] = set()
        for job in jobs:
            sub, notification = job.subscription, job.notification
            if sub.id in removed:
                continue
            refs = live.get(sub.account_id)
            if refs is None or sub.session_ref not in refs:
                store.delete_subscriptions(conn, [sub.id])
                conn.commit()
                removed.add(sub.id)
                report.removed_subscriptions += 1
                continue
            if notification.read_at is not None or notification.account_id != sub.account_id:
                store.finish_push_job(conn, notification.id, sub.id)
                conn.commit()
                report.dropped += 1
                continue
            outcome, detail = _send_one(send, sub, notification, private_key=private_key, subject=subject, locale=push_locale(locales.get(sub.account_id), settings), unread=store.unread_count(conn, sub.account_id), now=ts)
            if outcome == "sent":
                store.finish_push_job(conn, notification.id, sub.id)
                store.record_push_success(conn, sub.id, now=ts)
                store.mark_pushed(conn, notification.id, now=ts)
                report.sent += 1
            elif outcome == "gone":
                store.delete_subscriptions(conn, [sub.id])
                removed.add(sub.id)
                report.removed_subscriptions += 1
            elif outcome == "retry" and job.attempts < len(RETRY_DELAYS):
                store.retry_push_job(conn, notification.id, sub.id, next_attempt_at=ts + RETRY_DELAYS[job.attempts])
                report.retried += 1
            else:
                store.finish_push_job(conn, notification.id, sub.id)
                report.dropped += 1
                report.errors.append(detail)
                if store.record_push_failure(conn, sub.id, detail) >= MAX_SUBSCRIPTION_FAILURES:
                    store.delete_subscriptions(conn, [sub.id])
                    removed.add(sub.id)
                    report.removed_subscriptions += 1
            conn.commit()
    finally:
        conn.close()
    return report


def _send_one(send: Post, sub: store.PushSubscription, notification: store.Notification, *, private_key: str, subject: str, locale: Any, unread: int, now: float) -> tuple[str, str]:
    """Send one message. Return the outcome (``sent``, ``gone``, ``retry``, ``failed``) and a short reason that has no endpoint and no key."""
    spec = KINDS.get(notification.kind)
    try:
        request = webpush.build_request(
            endpoint=sub.endpoint,
            p256dh=sub.p256dh,
            auth=sub.auth,
            payload=_payload(notification, locale=locale, unread=unread),
            vapid_private_key=private_key,
            subject=subject,
            ttl=PUSH_TTL_SECONDS,
            urgency="high" if spec is not None and spec.klass == "request" else "normal",
            topic=f"claire-{notification.id}",
            now=now,
        )
    except webpush.WebPushError:
        # The keys or the endpoint of the subscription cannot make a message: the subscription is of no use.
        return "gone", "invalid_subscription"
    try:
        status = send(request)
    except FetchError as exc:
        # An address that is not public never becomes correct; a host that did not resolve can be a temporary fault.
        return ("gone", "endpoint_not_public") if "SSRF policy violation" in str(exc) else ("retry", "resolve_failed")
    except httpx.HTTPError as exc:
        return "retry", type(exc).__name__
    if 200 <= status < 300:
        return "sent", ""
    if status in (404, 410):
        return "gone", f"http_{status}"
    if status == 429 or status >= 500:
        return "retry", f"http_{status}"
    return "failed", f"http_{status}"


def cleanup(settings: Any, *, now: float | None = None) -> int:
    """Delete the notifications that are older than the retention (PN1 rule 4)."""
    client_file = client_db.client_db_path(settings.db_file)
    if not client_file.exists():
        return 0
    conn = client_db.connect_client_existing(client_file)
    try:
        count = store.delete_expired(conn, now=now)
        conn.commit()
        return count
    finally:
        conn.close()


async def run_scheduler(settings: Any) -> None:
    """Send due messages in the ``api`` process until the task is cancelled."""
    global _loop, _wake_event
    _loop = asyncio.get_running_loop()
    _wake_event = asyncio.Event()
    event = _wake_event
    last_cleanup = 0.0
    try:
        while True:
            try:
                await asyncio.wait_for(event.wait(), timeout=SCHEDULER_INTERVAL)
            except asyncio.TimeoutError:
                pass
            event.clear()
            try:
                from . import events

                await asyncio.to_thread(events.scan_pending_contributions, settings.db_file)
                report = await asyncio.to_thread(send_due, settings)
                if report.total:
                    log.info("web push: sent=%d retried=%d dropped=%d removed_subscriptions=%d errors=%s", report.sent, report.retried, report.dropped, report.removed_subscriptions, sorted(set(report.errors)))
                if time.monotonic() - last_cleanup >= CLEANUP_INTERVAL:
                    last_cleanup = time.monotonic()
                    await asyncio.to_thread(cleanup, settings)
            except Exception as exc:  # noqa: BLE001 - one bad cycle must not stop the task.
                log.warning("web push cycle failed: %s", type(exc).__name__)
    finally:
        _loop = None
        _wake_event = None
