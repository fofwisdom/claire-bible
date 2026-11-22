"""Collectors of the Support Bundles for the notifications and Web Push (PWA_NOTIFICATION_DESIGN.md; docs/implementation/NOTIFICATIONS.md).

A person who does not get a notification cannot see why: the records are in ``client.db``, the key pair is in ``auth.db``, the sessions are in ``claire.db``, and the task that sends is a task of the ``api`` process. These collectors read the four places and give one member with the answer to these questions: can the server send, does the task run, what waits in the queue, which devices fail, and which notifications were made.

| Bundle | Member | Function |
| :--- | :--- | :--- |
| Server-wide | ``notifications/summary.json`` | ``collect_notifications`` |
| Account scope | ``notifications/summary.json`` | ``collect_account_notifications`` |

The collectors read with read-only connections and do not make an absent file. They give counts, kinds, states, times, result codes, the host names of push services, and account numbers. They do not give an endpoint (it is a capability URL), a subscription key, the parameters of a notification (names of accounts and knowledge bases), or an entry address.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from ..store import db as dbm

__all__ = ["collect_account_notifications", "collect_notifications"]

_LIST_LIMIT = 100
_ACCOUNT_LIMIT = 20
_RECENT_LIMIT = 50
# The task looks at the queue every 5 seconds. A message whose time came 2 minutes ago is not a normal wait.
_QUEUE_OVERDUE_SECONDS = 120.0
_TABLES = ("notifications", "push_subscriptions", "push_queue", "notification_push_off", "notification_scan_seen")


def _iso(value: Any) -> str | None:
    """A time of the account-scope bundle: ISO 8601 in UTC, as in its other members."""
    if value is None:
        return None
    return datetime.fromtimestamp(float(value), timezone.utc).isoformat()


def _finding(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


def _connect(path: Path) -> sqlite3.Connection:
    return dbm.connect_existing(path, readonly=True)


def _push_service(endpoint: Any) -> str:
    """The host name of a push service. The path of an endpoint is the capability of the subscription and stays out."""
    try:
        return urlsplit(str(endpoint)).hostname or "unknown"
    except ValueError:
        return "unknown"


def _readiness(settings: Any) -> dict[str, Any]:
    """Can the server send: a canonical origin for the VAPID subject (PN3 rule 8), and the VAPID key pair."""
    from ..notifications import sender
    from ..store.auth_db import auth_db_path
    from ..store.notifications import VAPID_KEY_NAME

    out: dict[str, Any] = {"public_origin": False, "origin_scheme": None, "vapid_pair": {"state": "absent", "created_at": None}}
    try:
        origin = sender.vapid_subject(settings)
    except Exception as exc:  # noqa: BLE001 - settings that cannot give an origin are a result, not a failure of the collector.
        origin = None
        out["origin_error"] = type(exc).__name__
    if origin:
        out.update(public_origin=True, origin_scheme=urlsplit(origin).scheme)
    path = auth_db_path(settings.data_dir)
    if path.is_file():
        conn = _connect(path)
        try:
            if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='server_key_pairs'").fetchone() is not None:
                row = conn.execute("SELECT created_at FROM server_key_pairs WHERE name=?", (VAPID_KEY_NAME,)).fetchone()
                if row is not None:
                    out["vapid_pair"] = {"state": "present", "created_at": row[0]}
        finally:
            conn.close()
    return out


def _task(settings: Any, now: float) -> dict[str, Any]:
    from ..notifications import sender

    return sender.read_task_state(settings.data_dir, now=now)


def _live_session_refs(claire: sqlite3.Connection, now: float) -> dict[int, set[str]]:
    """For each active account: the credential identifiers of its sessions that are in effect (the rule of the sender, PN3 rule 7)."""
    from ..store.accounts import _credential_id

    active = {int(r[0]) for r in claire.execute("SELECT id FROM accounts WHERE status='active'")}
    refs: dict[int, set[str]] = {account_id: set() for account_id in active}
    for account_id, nonce in claire.execute("SELECT account_id, nonce FROM auth_sessions WHERE approved=1 AND expires_at>=? AND account_id IS NOT NULL", (now,)):
        if int(account_id) in refs:
            refs[int(account_id)].add(_credential_id(str(nonce)))
    return refs


def _kind_rows(conn: sqlite3.Connection, since: float, where: str = "", args: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    from ..notifications import KINDS

    rows = conn.execute(
        "SELECT kind, COUNT(*), SUM(read_at IS NULL), SUM(count), SUM(created_at>=?), SUM(read_at IS NULL AND pushed_at IS NULL), MAX(updated_at), MIN(CASE WHEN read_at IS NULL THEN updated_at END) "
        f"FROM notifications {where} GROUP BY kind ORDER BY kind",
        (since, *args),
    ).fetchall()
    out = []
    for kind, total, unread, events, in_period, never_pushed, newest, oldest_unread in rows:
        spec = KINDS.get(str(kind))
        out.append({
            "kind": kind,
            "channel": spec.channel if spec else None,
            "class": spec.klass if spec else None,
            "known": spec is not None,
            "notifications": int(total),
            "unread": int(unread or 0),
            "events": int(events or 0),
            "made_in_period": int(in_period or 0),
            "unread_never_sent": int(never_pushed or 0),
            "newest_at": newest,
            "oldest_unread_at": oldest_unread,
        })
    return out


def _queue(conn: sqlite3.Connection, now: float, where: str = "", args: tuple[Any, ...] = ()) -> dict[str, Any]:
    base = f"FROM push_queue q JOIN notifications n ON n.id=q.notification_id {where}"
    total, due, retrying, oldest, max_attempts = conn.execute(f"SELECT COUNT(*), SUM(q.next_attempt_at<=?), SUM(q.attempts>0), MIN(q.next_attempt_at), MAX(q.attempts) {base}", (now, *args)).fetchone()
    by_kind = [{"kind": r[0], "messages": int(r[1]), "max_attempts": int(r[2] or 0)} for r in conn.execute(f"SELECT n.kind, COUNT(*), MAX(q.attempts) {base} GROUP BY n.kind ORDER BY COUNT(*) DESC LIMIT ?", (*args, _LIST_LIMIT))]
    return {
        "messages": int(total or 0),
        "due_now": int(due or 0),
        "retrying": int(retrying or 0),
        "max_attempts": int(max_attempts or 0),
        "oldest_due_at": oldest,
        "oldest_due_age_seconds": (now - float(oldest)) if oldest is not None and float(oldest) <= now else None,
        "by_kind": by_kind,
    }


def collect_notifications(settings: Any, *, now: float, since: float) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """The notifications and Web Push of the server: readiness, the task that sends, the notifications by kind, the queue, the subscriptions by push service and result, the kinds that accounts turned off, the scan, and the accounts with the most unread notifications."""
    from ..store.client_db import client_db_path

    warnings: list[dict[str, Any]] = []
    findings: list[dict[str, str]] = []
    report: dict[str, Any] = {"findings": findings}
    code = "NOTIFICATIONS_COLLECTOR_FAILED"

    def guard(key: str, fn: Any) -> Any:
        try:
            report[key] = fn()
        except Exception as exc:  # noqa: BLE001 - a failure of one part does not stop the other parts.
            report[key] = {"state": "error", "error": f"{type(exc).__name__}: {exc}"}
            warnings.append({"code": code, "message": f"{key}: {type(exc).__name__}: {exc}"})
        return report[key]

    readiness = guard("readiness", lambda: _readiness(settings))
    task = guard("sender_task", lambda: _task(settings, now))

    path = client_db_path(settings.db_file)
    client_state: dict[str, Any] = {"path": str(path), "state": "present" if path.is_file() else "absent"}
    report["client_db"] = client_state
    if not path.is_file():
        return report, warnings
    try:
        conn = _connect(path)
    except Exception as exc:  # noqa: BLE001
        client_state.update(state="error", error=f"{type(exc).__name__}: {exc}")
        warnings.append({"code": "NOTIFICATIONS_UNREADABLE", "message": client_state["error"]})
        return report, warnings
    claire: sqlite3.Connection | None = None
    try:
        tables = {str(r[0]) for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        missing = [name for name in _TABLES if name not in tables]
        if missing:
            # client.db is older than version 9: the api process of this version did not start yet.
            client_state.update(state="no_notification_tables", missing_tables=missing)
            findings.append(_finding("NOTIFICATION_TABLES_ABSENT", f"client.db has no tables {missing}: the migration to version 9 did not run."))
            return report, warnings

        kinds = guard("notifications_by_kind", lambda: _kind_rows(conn, since))
        guard("notifications", lambda: {
            "total": int(conn.execute("SELECT COUNT(*) FROM notifications").fetchone()[0]),
            "unread": int(conn.execute("SELECT COUNT(*) FROM notifications WHERE read_at IS NULL").fetchone()[0]),
            "accounts_with_unread": int(conn.execute("SELECT COUNT(DISTINCT account_id) FROM notifications WHERE read_at IS NULL").fetchone()[0]),
            "accounts_with_unread_and_no_device": int(conn.execute("SELECT COUNT(DISTINCT account_id) FROM notifications WHERE read_at IS NULL AND account_id NOT IN (SELECT account_id FROM push_subscriptions)").fetchone()[0]),
        })
        queue = guard("push_queue", lambda: _queue(conn, now))
        guard("push_off", lambda: [{"kind": r[0], "accounts": int(r[1])} for r in conn.execute("SELECT kind, COUNT(*) FROM notification_push_off GROUP BY kind ORDER BY kind")])

        try:
            claire = _connect(Path(settings.db_file))
        except Exception as exc:  # noqa: BLE001
            warnings.append({"code": code, "message": f"claire.db: {type(exc).__name__}: {exc}"})

        def subscriptions() -> dict[str, Any]:
            rows = conn.execute("SELECT account_id, endpoint, session_ref, created_at, last_success_at, failure_count, last_error FROM push_subscriptions").fetchall()
            live = _live_session_refs(claire, now) if claire is not None else None
            services: dict[str, dict[str, Any]] = {}
            errors: dict[str, int] = {}
            ended = 0
            for account_id, endpoint, session_ref, _created, last_success, failures, last_error in rows:
                item = services.setdefault(_push_service(endpoint), {"devices": 0, "failing": 0, "never_sent": 0, "last_success_at": None})
                item["devices"] += 1
                item["failing"] += 1 if int(failures or 0) > 0 else 0
                item["never_sent"] += 1 if last_success is None else 0
                if last_success is not None and (item["last_success_at"] is None or float(last_success) > float(item["last_success_at"])):
                    item["last_success_at"] = last_success
                if last_error:
                    errors[str(last_error)] = errors.get(str(last_error), 0) + 1
                if live is not None and str(session_ref) not in live.get(int(account_id), set()):
                    ended += 1
            return {
                "devices": len(rows),
                "accounts": len({int(r[0]) for r in rows}),
                "failing": sum(1 for r in rows if int(r[5] or 0) > 0),
                "max_failures_in_sequence": max((int(r[5] or 0) for r in rows), default=0),
                # The sender deletes these at its next send: the session ended, or the account is not active.
                "without_session_in_effect": ended if live is not None else None,
                "oldest_created_at": min((r[3] for r in rows), default=None),
                "by_push_service": [{"push_service": host, **item} for host, item in sorted(services.items())][:_LIST_LIMIT],
                "last_errors": [{"error": error, "devices": count} for error, count in sorted(errors.items(), key=lambda pair: (-pair[1], pair[0]))][:_LIST_LIMIT],
            }

        subs = guard("push_subscriptions", subscriptions)

        def scan() -> dict[str, Any]:
            first = conn.execute("SELECT value FROM client_meta WHERE key='notification_scan:contribution'").fetchone()
            seen = {int(r[0]) for r in conn.execute("SELECT item_id FROM notification_scan_seen WHERE scope='contribution'")}
            pending = {int(r[0]) for r in claire.execute("SELECT public_id FROM kb_contributions WHERE state='pending'")} if claire is not None else None
            return {
                "contribution": {
                    "first_scan_at": None if first is None else float(first[0]),
                    "told": len(seen),
                    "pending_contributions": None if pending is None else len(pending),
                    # Contributions that wait for review and that no scan told about. More than 0 for longer than some seconds means that the task does not run.
                    "pending_not_told": None if pending is None else len(pending - seen),
                }
            }

        scanned = guard("scan", scan)

        def accounts() -> list[dict[str, Any]]:
            rows = conn.execute(
                "SELECT n.account_id, COUNT(*), MIN(n.updated_at), (SELECT COUNT(*) FROM push_subscriptions s WHERE s.account_id=n.account_id), "
                "(SELECT COUNT(*) FROM push_queue q JOIN notifications m ON m.id=q.notification_id WHERE m.account_id=n.account_id) "
                "FROM notifications n WHERE n.read_at IS NULL GROUP BY n.account_id ORDER BY COUNT(*) DESC, n.account_id LIMIT ?",
                (_ACCOUNT_LIMIT,),
            ).fetchall()
            numbers = {int(r[0]): str(r[1]) for r in claire.execute("SELECT id, public_id FROM accounts")} if claire is not None else {}
            return [{"account": numbers.get(int(r[0])), "unread": int(r[1]), "oldest_unread_at": r[2], "devices": int(r[3]), "queued_messages": int(r[4])} for r in rows]

        guard("accounts_with_most_unread", accounts)

        # The findings read the parts above. A part that failed is a dict with the state "error" and gives no finding.
        devices = subs.get("devices", 0) if isinstance(subs, dict) else 0
        queued = queue.get("messages", 0) if isinstance(queue, dict) else 0
        if isinstance(readiness, dict) and "public_origin" in readiness:
            if not readiness["public_origin"] and (devices or queued):
                findings.append(_finding("NO_PUBLIC_ORIGIN", f"The settings give no canonical origin: the sender drops each message ({devices} devices, {queued} queued messages). Set the public URL of the server."))
            elif readiness["origin_scheme"] == "http" and devices:
                findings.append(_finding("ORIGIN_NOT_HTTPS", "The canonical origin is not https: browsers give Web Push only to a secure origin, and a push service can refuse the VAPID subject."))
            if readiness["vapid_pair"]["state"] != "present" and devices:
                findings.append(_finding("VAPID_PAIR_ABSENT", f"auth.db has no VAPID key pair, but {devices} devices are subscribed: a new pair will be made, and each device must turn notifications on again."))
        if isinstance(task, dict) and task.get("state") in ("absent", "overdue", "unreadable"):
            age = task.get("age_seconds")
            findings.append(_finding("SENDER_TASK_NOT_RUNNING", f"The state of the task that sends is {task['state']}" + (f" (last cycle {int(age)} s ago)" if isinstance(age, (int, float)) else "") + ": no message goes out and no contribution scan runs. The task starts with the api process."))
        elif isinstance(task, dict) and task.get("last_cycle_ok") is False:
            findings.append(_finding("SENDER_TASK_CYCLE_FAILED", f"The last cycle of the task that sends failed: {task.get('last_error')}."))
        overdue = queue.get("oldest_due_age_seconds") if isinstance(queue, dict) else None
        if isinstance(overdue, (int, float)) and overdue > _QUEUE_OVERDUE_SECONDS:
            findings.append(_finding("QUEUE_OVERDUE", f"The oldest due message waits for {int(overdue)} s; the task sends a due message in 5 s."))
        if isinstance(subs, dict) and subs.get("failing"):
            top = ", ".join(f"{e['error']} x{e['devices']}" for e in subs["last_errors"][:5])
            findings.append(_finding("DEVICES_FAILING", f"{subs['failing']} of {devices} devices have failed sends (last results: {top}). A device with 5 failures in sequence is deleted."))
        if isinstance(subs, dict) and subs.get("without_session_in_effect"):
            findings.append(_finding("DEVICES_WITHOUT_SESSION", f"{subs['without_session_in_effect']} devices have no sign-in session in effect: the sender deletes them at its next send, and the person must turn notifications on again."))
        unknown = [k["kind"] for k in kinds if not k["known"]] if isinstance(kinds, list) else []
        if unknown:
            findings.append(_finding("UNKNOWN_KINDS", f"Notifications of kinds that this code does not know: {unknown}. They show the general text."))
        contribution = scanned.get("contribution") if isinstance(scanned, dict) else None
        if isinstance(contribution, dict) and contribution.get("pending_contributions"):
            if contribution["first_scan_at"] is None:
                findings.append(_finding("SCAN_NEVER_RAN", f"{contribution['pending_contributions']} contributions wait for review and the contribution scan never ran: reviewers get no notification."))
            elif contribution["pending_not_told"]:
                findings.append(_finding("SCAN_BEHIND", f"{contribution['pending_not_told']} contributions wait for review and no scan told about them."))
    finally:
        conn.close()
        if claire is not None:
            claire.close()
    return report, warnings


def collect_account_notifications(settings: Any, *, account_id: int, now: float, since: float) -> dict[str, Any]:
    """The notifications and the devices of one account, for the account-scope bundle: only rows of that account. The device rows have the push service host, not the endpoint."""
    from ..store.client_db import client_db_path

    findings: list[dict[str, str]] = []
    report: dict[str, Any] = {"findings": findings}
    try:
        readiness = _readiness(settings)
    except Exception as exc:  # noqa: BLE001
        readiness = {"state": "error", "error": type(exc).__name__}
    # The account sees only if the server can send. The sender makes the key pair at its first send, thus an absent pair does not stop a send.
    report["server_can_send"] = bool(readiness["public_origin"]) if "public_origin" in readiness else None
    # Only the state word of the task: its counts are data of the server.
    try:
        report["server_sender_task"] = _task(settings, now).get("state")
    except Exception:  # noqa: BLE001
        report["server_sender_task"] = "unreadable"
    path = client_db_path(settings.db_file)
    if not path.is_file():
        report["state"] = "absent"
        return report
    conn = _connect(path)
    try:
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='notifications'").fetchone() is None:
            report["state"] = "absent"
            return report
        claire = _connect(Path(settings.db_file))
        try:
            live = _live_session_refs(claire, now).get(int(account_id), set())
        finally:
            claire.close()
        devices = [
            {
                "id": int(r[0]),
                "label": r[1],
                "push_service": _push_service(r[2]),
                "session": "in_effect" if str(r[3]) in live else "ended",
                "created_at": _iso(r[4]),
                "last_success_at": _iso(r[5]),
                "failures_in_sequence": int(r[6] or 0),
                "last_error": r[7],
            }
            for r in conn.execute("SELECT id, label, endpoint, session_ref, created_at, last_success_at, failure_count, last_error FROM push_subscriptions WHERE account_id=? ORDER BY id", (account_id,))
        ]
        report["devices"] = devices
        report["kinds_without_push"] = sorted(str(r[0]) for r in conn.execute("SELECT kind FROM notification_push_off WHERE account_id=?", (account_id,)))
        kind_rows = _kind_rows(conn, since, "WHERE account_id=?", (account_id,))
        unread = sum(int(row["unread"]) for row in kind_rows)
        report["notifications_by_kind"] = [{**row, "newest_at": _iso(row["newest_at"]), "oldest_unread_at": _iso(row["oldest_unread_at"])} for row in kind_rows]
        queue = _queue(conn, now, "WHERE n.account_id=?", (account_id,))
        report["push_queue"] = {**queue, "oldest_due_at": _iso(queue["oldest_due_at"])}
        report["recent"] = [
            {"id": int(r[0]), "kind": r[1], "events": int(r[2]), "created_at": _iso(r[3]), "updated_at": _iso(r[4]), "read_at": _iso(r[5]), "sent_at": _iso(r[6])}
            for r in conn.execute("SELECT id, kind, count, created_at, updated_at, read_at, pushed_at FROM notifications WHERE account_id=? ORDER BY updated_at DESC, id DESC LIMIT ?", (account_id, _RECENT_LIMIT))
        ]
    finally:
        conn.close()

    if report["server_can_send"] is False:
        findings.append(_finding("SERVER_CANNOT_SEND", "The server cannot send Web Push now: its settings have no public address. The notifications are in the list of the bell. Tell the server administrator."))
    if report["server_sender_task"] != "recent":
        findings.append(_finding("SERVER_SENDER_NOT_RUNNING", f"The task of the server that sends notifications does not run now (state: {report['server_sender_task']}). Tell the server administrator."))
    if not devices and unread:
        findings.append(_finding("NO_DEVICE", f"{unread} notifications are not read and no device gets notifications. Turn notifications on at /account/notifications."))
    ended = [d["id"] for d in devices if d["session"] == "ended"]
    if ended:
        findings.append(_finding("DEVICE_SESSION_ENDED", f"The sign-in session of the devices {ended} ended: they get no notification and go from the list at the next send. Sign in on the device and turn notifications on again."))
    failing = [d for d in devices if d["failures_in_sequence"]]
    if failing:
        findings.append(_finding("DEVICE_FAILING", "Sends to these devices failed: " + ", ".join(f"{d['id']} ({d['last_error']})" for d in failing) + ". Turn notifications off and on again on the device."))
    return report
