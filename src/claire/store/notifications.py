"""Notifications and Web Push subscriptions (PWA_NOTIFICATION_DESIGN.md PN1): the tables of ``client.db`` version 9 and the VAPID key pair of the server in ``auth.db`` (``server_key_pairs``).

- A function that takes ``conn`` does not commit; the caller commits. ``account_id`` is a value, not a foreign key (RD27).
- A notification stores the kind and its parameters. ``claire.notifications`` makes the text from the catalog.
- The VAPID private key is stored as a ciphertext of ``SecretService`` with the additional data ``server_key|webpush_vapid``. This module does not write the key to a log.
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final, Iterable

if TYPE_CHECKING:
    from .secrets import SecretService

__all__ = [
    "MAX_SUBSCRIPTIONS",
    "NOTIFICATION_RETENTION_SECONDS",
    "Notification",
    "PushJob",
    "PushSubscription",
    "SubscriptionLimit",
    "add_notification",
    "delete_expired",
    "delete_subscription",
    "delete_subscription_by_endpoint",
    "delete_subscriptions",
    "due_push_jobs",
    "enqueue_push",
    "ensure_vapid_private_key",
    "finish_push_job",
    "get_notification",
    "list_notifications",
    "list_subscriptions",
    "mark_pushed",
    "mark_read",
    "next_push_time",
    "push_off_kinds",
    "record_push_failure",
    "record_push_success",
    "retry_push_job",
    "scan_seen",
    "set_push_off",
    "set_scan_seen",
    "unread_count",
    "upsert_subscription",
]

MAX_SUBSCRIPTIONS: Final[int] = 10
NOTIFICATION_RETENTION_SECONDS: Final[float] = 90 * 86400.0
VAPID_KEY_NAME: Final[str] = "webpush_vapid"
_LABEL_MAX: Final[int] = 80
_ERROR_MAX: Final[int] = 120
_ENDPOINT_MAX: Final[int] = 2048


class SubscriptionLimit(ValueError):
    """The account has ``MAX_SUBSCRIPTIONS`` subscriptions."""


@dataclass(frozen=True, slots=True)
class Notification:
    id: int
    account_id: int
    kind: str
    params: dict[str, Any]
    target_path: str
    group_key: str | None
    count: int
    created_at: float
    updated_at: float
    read_at: float | None
    pushed_at: float | None


@dataclass(frozen=True, slots=True)
class PushSubscription:
    id: int
    account_id: int
    endpoint: str
    p256dh: str
    auth: str
    label: str
    session_ref: str
    created_at: float
    last_success_at: float | None
    failure_count: int
    last_error: str | None


@dataclass(frozen=True, slots=True)
class PushJob:
    """One queue row with its notification and its subscription."""

    notification: Notification
    subscription: PushSubscription
    attempts: int


def _now(now: float | None) -> float:
    return time.time() if now is None else now


def _notification(row: sqlite3.Row) -> Notification:
    try:
        params = json.loads(row["params_json"])
    except (TypeError, ValueError):
        params = {}
    return Notification(
        id=int(row["id"]),
        account_id=int(row["account_id"]),
        kind=str(row["kind"]),
        params=params if isinstance(params, dict) else {},
        target_path=str(row["target_path"]),
        group_key=row["group_key"],
        count=int(row["count"]),
        created_at=float(row["created_at"]),
        updated_at=float(row["updated_at"]),
        read_at=None if row["read_at"] is None else float(row["read_at"]),
        pushed_at=None if row["pushed_at"] is None else float(row["pushed_at"]),
    )


def _subscription(row: sqlite3.Row) -> PushSubscription:
    return PushSubscription(
        id=int(row["id"]),
        account_id=int(row["account_id"]),
        endpoint=str(row["endpoint"]),
        p256dh=str(row["p256dh"]),
        auth=str(row["auth"]),
        label=str(row["label"]),
        session_ref=str(row["session_ref"]),
        created_at=float(row["created_at"]),
        last_success_at=None if row["last_success_at"] is None else float(row["last_success_at"]),
        failure_count=int(row["failure_count"]),
        last_error=row["last_error"],
    )


_NOTIFICATION_COLUMNS: Final[str] = "id, account_id, kind, params_json, target_path, group_key, count, created_at, updated_at, read_at, pushed_at"
_SUBSCRIPTION_COLUMNS: Final[str] = "id, account_id, endpoint, p256dh, auth, label, session_ref, created_at, last_success_at, failure_count, last_error"


# --- Notifications ---


def add_notification(conn: sqlite3.Connection, *, account_id: int, kind: str, params: dict[str, Any], target_path: str, group_key: str | None = None, events: int = 1, now: float | None = None) -> tuple[int, bool]:
    """Record ``events`` events of one subject for one account. Return the notification number and ``True`` for a new row.

    With ``group_key``: when a row of the same account, kind, and group is not read, the events change that row (``count`` + ``events``, new parameters and target) and the result is ``(id, False)``.
    """
    if events < 1:
        raise ValueError("events must be 1 or more")
    ts = _now(now)
    payload = json.dumps(params, ensure_ascii=False, sort_keys=True)
    if group_key is not None:
        row = conn.execute("SELECT id FROM notifications WHERE account_id=? AND kind=? AND group_key=? AND read_at IS NULL", (account_id, kind, group_key)).fetchone()
        if row is not None:
            conn.execute("UPDATE notifications SET count=count+?, params_json=?, target_path=?, updated_at=? WHERE id=?", (events, payload, target_path, ts, int(row[0])))
            return int(row[0]), False
    cur = conn.execute(
        "INSERT INTO notifications (account_id, kind, params_json, target_path, group_key, count, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (account_id, kind, payload, target_path, group_key, events, ts, ts),
    )
    return int(cur.lastrowid or 0), True


def get_notification(conn: sqlite3.Connection, notification_id: int) -> Notification | None:
    row = conn.execute(f"SELECT {_NOTIFICATION_COLUMNS} FROM notifications WHERE id=?", (notification_id,)).fetchone()
    return None if row is None else _notification(row)


def list_notifications(conn: sqlite3.Connection, account_id: int, *, limit: int, before_id: int | None = None) -> list[Notification]:
    """The notifications of the account, the newest change first. ``before_id`` gives the rows with a lower number (the next page of a list that is sorted by number)."""
    if before_id is None:
        rows = conn.execute(f"SELECT {_NOTIFICATION_COLUMNS} FROM notifications WHERE account_id=? ORDER BY updated_at DESC, id DESC LIMIT ?", (account_id, limit)).fetchall()
    else:
        rows = conn.execute(f"SELECT {_NOTIFICATION_COLUMNS} FROM notifications WHERE account_id=? AND id<? ORDER BY id DESC LIMIT ?", (account_id, before_id, limit)).fetchall()
    return [_notification(r) for r in rows]


def unread_count(conn: sqlite3.Connection, account_id: int) -> int:
    return int(conn.execute("SELECT COUNT(*) FROM notifications WHERE account_id=? AND read_at IS NULL", (account_id,)).fetchone()[0])


def mark_read(conn: sqlite3.Connection, account_id: int, *, ids: Iterable[int] | None, now: float | None = None) -> int:
    """Mark notifications of the account as read: the given numbers, or all when ``ids`` is ``None``. The messages of these notifications that wait in the queue go. Return the count of changed rows."""
    ts = _now(now)
    if ids is None:
        conn.execute("DELETE FROM push_queue WHERE notification_id IN (SELECT id FROM notifications WHERE account_id=? AND read_at IS NULL)", (account_id,))
        return int(conn.execute("UPDATE notifications SET read_at=? WHERE account_id=? AND read_at IS NULL", (ts, account_id)).rowcount)
    wanted = sorted({int(i) for i in ids})
    if not wanted:
        return 0
    marks = ",".join("?" for _ in wanted)
    conn.execute(f"DELETE FROM push_queue WHERE notification_id IN (SELECT id FROM notifications WHERE account_id=? AND read_at IS NULL AND id IN ({marks}))", (account_id, *wanted))
    return int(conn.execute(f"UPDATE notifications SET read_at=? WHERE account_id=? AND read_at IS NULL AND id IN ({marks})", (ts, account_id, *wanted)).rowcount)


def mark_pushed(conn: sqlite3.Connection, notification_id: int, *, now: float | None = None) -> None:
    conn.execute("UPDATE notifications SET pushed_at=? WHERE id=?", (_now(now), notification_id))


def delete_expired(conn: sqlite3.Connection, *, now: float | None = None) -> int:
    """Delete the notifications whose last change is older than the retention, with their queue rows, and the queue rows without a notification or a subscription."""
    limit = _now(now) - NOTIFICATION_RETENTION_SECONDS
    count = int(conn.execute("DELETE FROM notifications WHERE updated_at<?", (limit,)).rowcount)
    conn.execute("DELETE FROM push_queue WHERE notification_id NOT IN (SELECT id FROM notifications) OR subscription_id NOT IN (SELECT id FROM push_subscriptions)")
    return count


# --- Kinds without Web Push ---


def push_off_kinds(conn: sqlite3.Connection, account_id: int) -> set[str]:
    return {str(r[0]) for r in conn.execute("SELECT kind FROM notification_push_off WHERE account_id=?", (account_id,))}


def set_push_off(conn: sqlite3.Connection, account_id: int, kind: str, off: bool) -> None:
    if off:
        conn.execute("INSERT INTO notification_push_off (account_id, kind) VALUES (?, ?) ON CONFLICT(account_id, kind) DO NOTHING", (account_id, kind))
    else:
        conn.execute("DELETE FROM notification_push_off WHERE account_id=? AND kind=?", (account_id, kind))


# --- Subscriptions ---


def upsert_subscription(conn: sqlite3.Connection, *, account_id: int, endpoint: str, p256dh: str, auth: str, label: str, session_ref: str, now: float | None = None) -> PushSubscription:
    """Add the subscription of a browser, or renew the row of its endpoint. An endpoint belongs to one browser: when another account had the row, the row moves to this account (the browser is now signed in as this account)."""
    if not endpoint or len(endpoint) > _ENDPOINT_MAX:
        raise ValueError("endpoint length")
    ts = _now(now)
    existing = conn.execute("SELECT id, account_id FROM push_subscriptions WHERE endpoint=?", (endpoint,)).fetchone()
    if existing is None or int(existing["account_id"]) != account_id:
        if int(conn.execute("SELECT COUNT(*) FROM push_subscriptions WHERE account_id=?", (account_id,)).fetchone()[0]) >= MAX_SUBSCRIPTIONS:
            raise SubscriptionLimit(f"an account has {MAX_SUBSCRIPTIONS} subscriptions at most")
    if existing is not None:
        if int(existing["account_id"]) != account_id:
            conn.execute("DELETE FROM push_queue WHERE subscription_id=?", (int(existing["id"]),))
        conn.execute(
            "UPDATE push_subscriptions SET account_id=?, p256dh=?, auth=?, label=?, session_ref=?, failure_count=0, last_error=NULL WHERE id=?",
            (account_id, p256dh, auth, label[:_LABEL_MAX], session_ref, int(existing["id"])),
        )
        sub_id = int(existing["id"])
    else:
        cur = conn.execute(
            "INSERT INTO push_subscriptions (account_id, endpoint, p256dh, auth, label, session_ref, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (account_id, endpoint, p256dh, auth, label[:_LABEL_MAX], session_ref, ts),
        )
        sub_id = int(cur.lastrowid or 0)
    row = conn.execute(f"SELECT {_SUBSCRIPTION_COLUMNS} FROM push_subscriptions WHERE id=?", (sub_id,)).fetchone()
    return _subscription(row)


def list_subscriptions(conn: sqlite3.Connection, account_id: int) -> list[PushSubscription]:
    return [_subscription(r) for r in conn.execute(f"SELECT {_SUBSCRIPTION_COLUMNS} FROM push_subscriptions WHERE account_id=? ORDER BY id", (account_id,))]


def delete_subscriptions(conn: sqlite3.Connection, subscription_ids: Iterable[int]) -> int:
    ids = sorted({int(i) for i in subscription_ids})
    if not ids:
        return 0
    marks = ",".join("?" for _ in ids)
    conn.execute(f"DELETE FROM push_queue WHERE subscription_id IN ({marks})", ids)
    return int(conn.execute(f"DELETE FROM push_subscriptions WHERE id IN ({marks})", ids).rowcount)


def delete_subscription(conn: sqlite3.Connection, account_id: int, subscription_id: int) -> bool:
    """Delete one subscription of the account. ``False`` when the account has no such row."""
    row = conn.execute("SELECT id FROM push_subscriptions WHERE id=? AND account_id=?", (subscription_id, account_id)).fetchone()
    return row is not None and delete_subscriptions(conn, [int(row[0])]) == 1


def delete_subscription_by_endpoint(conn: sqlite3.Connection, account_id: int, endpoint: str) -> bool:
    row = conn.execute("SELECT id FROM push_subscriptions WHERE endpoint=? AND account_id=?", (endpoint, account_id)).fetchone()
    return row is not None and delete_subscriptions(conn, [int(row[0])]) == 1


# --- Queue ---


def enqueue_push(conn: sqlite3.Connection, *, notification_id: int, account_id: int, not_before: float) -> int:
    """Put one queue row for each subscription of the account. A row that waits already stays as it is: the sender reads the notification at the send, thus the waiting message gets the new count. Return the count of new rows."""
    cur = conn.execute(
        "INSERT INTO push_queue (notification_id, subscription_id, attempts, next_attempt_at) SELECT ?, id, 0, ? FROM push_subscriptions WHERE account_id=? ON CONFLICT(notification_id, subscription_id) DO NOTHING",
        (notification_id, not_before, account_id),
    )
    return int(cur.rowcount)


def due_push_jobs(conn: sqlite3.Connection, *, now: float | None = None, limit: int = 100) -> list[PushJob]:
    """The queue rows whose time came, the oldest first, with their notification and subscription."""
    rows = conn.execute(
        "SELECT q.notification_id, q.subscription_id, q.attempts FROM push_queue q WHERE q.next_attempt_at<=? ORDER BY q.next_attempt_at, q.notification_id LIMIT ?",
        (_now(now), limit),
    ).fetchall()
    jobs: list[PushJob] = []
    for row in rows:
        notification = get_notification(conn, int(row["notification_id"]))
        sub_row = conn.execute(f"SELECT {_SUBSCRIPTION_COLUMNS} FROM push_subscriptions WHERE id=?", (int(row["subscription_id"]),)).fetchone()
        if notification is None or sub_row is None:
            conn.execute("DELETE FROM push_queue WHERE notification_id=? AND subscription_id=?", (int(row["notification_id"]), int(row["subscription_id"])))
            continue
        jobs.append(PushJob(notification=notification, subscription=_subscription(sub_row), attempts=int(row["attempts"])))
    return jobs


def next_push_time(conn: sqlite3.Connection) -> float | None:
    """The earliest time of a queue row, or ``None`` for an empty queue."""
    row = conn.execute("SELECT MIN(next_attempt_at) FROM push_queue").fetchone()
    return None if row is None or row[0] is None else float(row[0])


def finish_push_job(conn: sqlite3.Connection, notification_id: int, subscription_id: int) -> None:
    conn.execute("DELETE FROM push_queue WHERE notification_id=? AND subscription_id=?", (notification_id, subscription_id))


def retry_push_job(conn: sqlite3.Connection, notification_id: int, subscription_id: int, *, next_attempt_at: float) -> None:
    conn.execute("UPDATE push_queue SET attempts=attempts+1, next_attempt_at=? WHERE notification_id=? AND subscription_id=?", (next_attempt_at, notification_id, subscription_id))


def record_push_success(conn: sqlite3.Connection, subscription_id: int, *, now: float | None = None) -> None:
    conn.execute("UPDATE push_subscriptions SET last_success_at=?, failure_count=0, last_error=NULL WHERE id=?", (_now(now), subscription_id))


def record_push_failure(conn: sqlite3.Connection, subscription_id: int, error: str) -> int:
    """Count one failure of the subscription and return the count of failures in sequence."""
    conn.execute("UPDATE push_subscriptions SET failure_count=failure_count+1, last_error=? WHERE id=?", (error[:_ERROR_MAX], subscription_id))
    row = conn.execute("SELECT failure_count FROM push_subscriptions WHERE id=?", (subscription_id,)).fetchone()
    return 0 if row is None else int(row[0])


# --- Items that a scan told about ---


def scan_seen(conn: sqlite3.Connection, scope: str) -> set[int] | None:
    """The items of ``scope`` that a scan knows. ``None`` before the first scan of the scope: that scan records the items that exist and tells about none of them."""
    if conn.execute("SELECT 1 FROM client_meta WHERE key=?", (f"notification_scan:{scope}",)).fetchone() is None:
        return None
    return {int(r[0]) for r in conn.execute("SELECT item_id FROM notification_scan_seen WHERE scope=?", (scope,))}


def set_scan_seen(conn: sqlite3.Connection, scope: str, *, add: Iterable[int], remove: Iterable[int], now: float | None = None) -> None:
    """Record the items of ``scope`` that the scan knows now, and that the scope had its first scan."""
    conn.executemany("INSERT INTO notification_scan_seen (scope, item_id) VALUES (?, ?) ON CONFLICT(scope, item_id) DO NOTHING", [(scope, int(i)) for i in add])
    conn.executemany("DELETE FROM notification_scan_seen WHERE scope=? AND item_id=?", [(scope, int(i)) for i in remove])
    conn.execute("INSERT INTO client_meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO NOTHING", (f"notification_scan:{scope}", str(_now(now))))


# --- VAPID key pair (auth.db) ---


def _vapid_aad() -> str:
    return f"server_key|{VAPID_KEY_NAME}"


def ensure_vapid_private_key(auth_conn: sqlite3.Connection, secret_service: SecretService, *, now: float | None = None) -> str:
    """The VAPID private key of the server (base64url text). The first call makes the pair and commits; a second process that makes a pair at the same time loses, and the two read the same row."""
    from ..notifications import webpush

    row = auth_conn.execute("SELECT private_ciphertext, private_nonce, key_version FROM server_key_pairs WHERE name=?", (VAPID_KEY_NAME,)).fetchone()
    if row is None:
        private_key = webpush.generate_vapid_private_key()
        sealed = secret_service.encrypt(private_key, _vapid_aad())
        auth_conn.execute(
            "INSERT INTO server_key_pairs (name, public_key, private_ciphertext, private_nonce, key_version, created_at) VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(name) DO NOTHING",
            (VAPID_KEY_NAME, webpush.vapid_public_key(private_key), sealed.ciphertext, sealed.nonce, sealed.key_version, _now(now)),
        )
        auth_conn.commit()
        row = auth_conn.execute("SELECT private_ciphertext, private_nonce, key_version FROM server_key_pairs WHERE name=?", (VAPID_KEY_NAME,)).fetchone()
    return secret_service.decrypt(bytes(row["private_ciphertext"]), bytes(row["private_nonce"]), _vapid_aad(), int(row["key_version"])).decode("ascii")
