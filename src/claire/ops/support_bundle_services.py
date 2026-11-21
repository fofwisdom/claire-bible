"""Collectors of the server-wide Support Bundle for the subsystems that came after format 3.

The collectors read with read-only connections (``mode=ro`` and ``query_only``). They give counts, states, codes, times, public server domains, and account numbers. They do not give document text, payloads, URLs of feeds or posts, keys, ciphertexts, or tokens. A short error text is masked; the caller also applies ``sanitize_sensitive_data``.

Archive members (see docs/implementation/TELEMETRY.md Section 3.3):

| Member | Function |
| :--- | :--- |
| ``diagnostics/services.json`` | ``collect_services`` |
| ``diagnostics/runtime_config.json`` | ``collect_runtime_config`` |
| ``federation/server_queues.json`` | ``collect_server_federation`` |
| ``federation/client_db.json`` | ``collect_client_db`` |
| ``accounts/auth_db.json`` | ``collect_auth_db`` |
| ``pipeline/worker_queues.json`` | ``collect_worker_queues`` |
| ``pipeline/shared_kb.json`` | ``collect_shared_kb`` |

Each function returns ``(report, warnings)``. A missing database or table is a value in the report (``absent``), not an error.
"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any, Iterable

from ..store import db as dbm

__all__ = [
    "collect_auth_db",
    "collect_client_db",
    "collect_runtime_config",
    "collect_server_federation",
    "collect_services",
    "collect_shared_kb",
    "collect_worker_queues",
]

_TEXT_MAX_CHARS = 300
_LIST_LIMIT = 100
# A Telegram bot token in an error text (for example in a request URL): "<bot id>:<secret>".
_TELEGRAM_TOKEN_RE = re.compile(r"\b\d{6,}:[A-Za-z0-9_-]{30,}\b")
_URL_RE = re.compile(r"https?://[^\s'\"<>]+")
# server_settings values that are secret material and never go into a bundle.
_SECRET_SERVER_SETTINGS = frozenset({"id_salt"})


def _short_text(value: Any, *, keep_urls: bool = False) -> str | None:
    if value is None:
        return None
    text = _TELEGRAM_TOKEN_RE.sub("***REDACTED***", str(value))
    if not keep_urls:
        text = _URL_RE.sub("[URL]", text)
    return text[:_TEXT_MAX_CHARS]


def _connect(path: Path) -> sqlite3.Connection:
    return dbm.connect_existing(path, readonly=True)


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {str(r[0]) for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(r[1]) for r in conn.execute(f"PRAGMA table_info({table})")}


def _row_counts(conn: sqlite3.Connection) -> dict[str, int]:
    out: dict[str, int] = {}
    for table in sorted(_tables(conn)):
        if table.startswith("sqlite_") or "_fts" in table:
            continue
        out[table] = int(conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
    return out


def _quick_check(conn: sqlite3.Connection) -> str:
    row = conn.execute("PRAGMA quick_check").fetchone()
    return str(row[0]) if row else "unknown"


def _status_summary(
    conn: sqlite3.Connection,
    table: str,
    *,
    status: str,
    time_column: str | None,
    group: Iterable[str] = (),
) -> dict[str, Any]:
    """Count by the status column (and other columns), with the oldest and the newest time of each group."""
    if table not in _tables(conn):
        return {"state": "absent"}
    columns = _columns(conn, table)
    keys = [status, *[g for g in group if g in columns]]
    select = ", ".join(keys)
    times = f", MIN({time_column}), MAX({time_column})" if time_column and time_column in columns else ", NULL, NULL"
    rows = conn.execute(f"SELECT {select}, COUNT(*){times} FROM {table} GROUP BY {select} ORDER BY {select}").fetchall()
    groups = []
    for row in rows:
        item = {key: row[i] for i, key in enumerate(keys)}
        item.update(count=int(row[len(keys)]), oldest_at=row[len(keys) + 1], newest_at=row[len(keys) + 2])
        groups.append(item)
    return {"total": sum(g["count"] for g in groups), "groups": groups}


def _code_counts(
    conn: sqlite3.Connection,
    table: str,
    *,
    code: str,
    time_column: str,
    since: float,
    group: Iterable[str] = (),
) -> list[dict[str, Any]] | dict[str, str]:
    """Count the rows of the period that have a code (an error code or a reason code)."""
    if table not in _tables(conn):
        return {"state": "absent"}
    columns = _columns(conn, table)
    keys = [code, *[g for g in group if g in columns]]
    select = ", ".join(keys)
    rows = conn.execute(
        f"SELECT {select}, COUNT(*), MAX({time_column}) FROM {table} WHERE {code} IS NOT NULL AND {code} != '' AND {time_column} >= ? GROUP BY {select} ORDER BY COUNT(*) DESC LIMIT ?",
        (since, _LIST_LIMIT),
    ).fetchall()
    return [{**{key: row[i] for i, key in enumerate(keys)}, "count": int(row[len(keys)]), "last_at": row[len(keys) + 1]} for row in rows]


def _open_report(path: Path, report: dict[str, Any], warnings: list[dict[str, Any]], code: str) -> sqlite3.Connection | None:
    report["path"] = str(path)
    if not path.is_file():
        report["state"] = "absent"
        return None
    try:
        return _connect(path)
    except Exception as exc:  # noqa: BLE001
        report["state"] = "error"
        report["error"] = f"{type(exc).__name__}: {exc}"
        warnings.append({"code": code, "message": report["error"]})
        return None


def _guard(report: dict[str, Any], key: str, warnings: list[dict[str, Any]], code: str, fn: Any) -> None:
    """Run one part of a collector. A failure of one part does not stop the other parts."""
    try:
        report[key] = fn()
    except Exception as exc:  # noqa: BLE001
        report[key] = {"state": "error", "error": f"{type(exc).__name__}: {exc}"}
        warnings.append({"code": code, "message": f"{key}: {type(exc).__name__}: {exc}"})


# --- services -----------------------------------------------------------------------------------


def collect_services(settings: Any, *, now: float) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Liveness of the services that are not the API: the worker loops and the Fedify sidecar."""
    from .loop_runtime import read_loop_states

    data_dir = Path(settings.data_dir)
    warnings: list[dict[str, Any]] = []
    report: dict[str, Any] = {}
    _guard(report, "worker_loops", warnings, "SERVICES_COLLECTOR_FAILED", lambda: read_loop_states(data_dir, now=now))

    def _sidecar() -> dict[str, Any]:
        from ..api.federation_routes import read_heartbeat

        heartbeat = read_heartbeat(data_dir)
        received = heartbeat.get("received_at") if isinstance(heartbeat, dict) else None
        fedify_db = data_dir / "fedify" / "fedify.db"
        return {
            "heartbeat": heartbeat,
            "heartbeat_age_seconds": (now - float(received)) if isinstance(received, (int, float)) else None,
            "socket_present": (data_dir / "run" / "federation.sock").exists(),
            # Only the file identity: the sidecar database is the sidecar's data (OPERATIONS.md).
            "fedify_db": {"present": fedify_db.is_file(), "size_bytes": fedify_db.stat().st_size if fedify_db.is_file() else None, "mtime": fedify_db.stat().st_mtime if fedify_db.is_file() else None},
        }

    _guard(report, "fedify_sidecar", warnings, "SERVICES_COLLECTOR_FAILED", _sidecar)
    return report, warnings


# --- runtime configuration ----------------------------------------------------------------------


def collect_runtime_config(settings: Any) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Settings that the admin changes at run time and that the environment settings do not show."""
    data_dir = Path(settings.data_dir)
    warnings: list[dict[str, Any]] = []
    report: dict[str, Any] = {}

    def _json_file(name: str) -> Any:
        path = data_dir / name
        if not path.is_file():
            return {"state": "absent"}
        return json.loads(path.read_text(encoding="utf-8"))

    _guard(report, "providers_json", warnings, "RUNTIME_CONFIG_FAILED", lambda: _json_file("providers.json"))
    _guard(report, "ingest_limits_json", warnings, "RUNTIME_CONFIG_FAILED", lambda: _json_file("ingest_limits.json"))

    def _server_settings() -> Any:
        path = Path(settings.db_file)
        if not path.is_file():
            return {"state": "absent"}
        conn = _connect(path)
        try:
            if "server_settings" not in _tables(conn):
                return {"state": "absent"}
            out: dict[str, Any] = {}
            for row in conn.execute("SELECT key, value, updated_at, updated_by FROM server_settings ORDER BY key"):
                key = str(row["key"])
                if key in _SECRET_SERVER_SETTINGS:
                    out[key] = {"value": "***REDACTED***", "updated_at": row["updated_at"]}
                    continue
                try:
                    value: Any = json.loads(row["value"])
                except (TypeError, ValueError):
                    value = row["value"]
                out[key] = {"value": value, "updated_at": row["updated_at"], "updated_by": row["updated_by"]}
            return out
        finally:
            conn.close()

    _guard(report, "server_settings", warnings, "RUNTIME_CONFIG_FAILED", _server_settings)
    return report, warnings


# --- claire.db: federation and knowledge federation ----------------------------------------------


def collect_server_federation(settings: Any, *, now: float, since: float) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Queues of the federation of accounts and of the knowledge federation in ``claire.db``."""
    warnings: list[dict[str, Any]] = []
    report: dict[str, Any] = {}
    conn = _open_report(Path(settings.db_file), report, warnings, "SERVER_FEDERATION_UNREADABLE")
    if conn is None:
        return report, warnings
    code = "SERVER_FEDERATION_COLLECTOR_FAILED"
    try:
        _guard(report, "accounts", warnings, code, lambda: _status_summary(conn, "accounts", status="status", time_column="created_at", group=("role",)))
        _guard(report, "federation_outbox", warnings, code, lambda: _status_summary(conn, "federation_outbox", status="status", time_column="created_at", group=("kind",)))
        _guard(report, "federation_outbox_errors", warnings, code, lambda: _code_counts(conn, "federation_outbox", code="error_code", time_column="created_at", since=since, group=("kind",)))
        _guard(report, "federation_followers", warnings, code, lambda: {"total": _count(conn, "federation_followers")})
        _guard(report, "federation_domain_blocks", warnings, code, lambda: {"total": _count(conn, "federation_domain_blocks")})
        _guard(report, "kf_outbox", warnings, code, lambda: _status_summary(conn, "kf_outbox", status="status", time_column="created_at", group=("kind",)))
        _guard(report, "kf_outbox_errors", warnings, code, lambda: _code_counts(conn, "kf_outbox", code="error_code", time_column="created_at", since=since, group=("recipient_domain",)))
        _guard(report, "kf_outbox_retries", warnings, code, lambda: _retries(conn, now))
        _guard(report, "kf_inbox_journal", warnings, code, lambda: _status_summary(conn, "kf_inbox_journal", status="status", time_column="received_at", group=("kind",)))
        _guard(report, "kf_inbox_journal_errors", warnings, code, lambda: _code_counts(conn, "kf_inbox_journal", code="error_code", time_column="received_at", since=since, group=("actor_domain",)))
        _guard(report, "kf_remote_reads", warnings, code, lambda: _status_summary(conn, "kf_remote_reads", status="status", time_column="started_at", group=("kind",)))
        _guard(report, "kf_remote_reads_errors", warnings, code, lambda: _code_counts(conn, "kf_remote_reads", code="error_code", time_column="started_at", since=since, group=("domain",)))
        _guard(report, "kf_outbound_offers", warnings, code, lambda: _status_summary(conn, "kf_outbound_offers", status="state", time_column="created_at", group=("automatic",)))
        _guard(report, "kf_outbound_offer_reasons", warnings, code, lambda: _code_counts(conn, "kf_outbound_offers", code="reason_code", time_column="created_at", since=since, group=("domain",)))
        _guard(report, "kf_inbound_offers", warnings, code, lambda: _status_summary(conn, "kf_inbound_offers", status="state", time_column="received_at"))
        _guard(report, "kf_inbound_offer_reasons", warnings, code, lambda: _code_counts(conn, "kf_inbound_offers", code="reason_code", time_column="received_at", since=since, group=("domain",)))
        _guard(report, "kf_known_servers", warnings, code, lambda: _known_servers(conn))
        _guard(report, "kf_remote_kb_reads", warnings, code, lambda: _remote_kb_reads(conn))
    finally:
        conn.close()
    return report, warnings


def _count(conn: sqlite3.Connection, table: str) -> int | str:
    if table not in _tables(conn):
        return "absent"
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def _retries(conn: sqlite3.Connection, now: float) -> dict[str, Any]:
    if "kf_outbox" not in _tables(conn):
        return {"state": "absent"}
    row = conn.execute(
        "SELECT COUNT(*), MAX(attempts), MIN(next_attempt_at) FROM kf_outbox WHERE status='pending' AND attempts > 0"
    ).fetchone()
    overdue = conn.execute("SELECT COUNT(*) FROM kf_outbox WHERE status='pending' AND next_attempt_at < ?", (now - 3600,)).fetchone()[0]
    return {"pending_with_attempts": int(row[0]), "max_attempts": row[1], "earliest_next_attempt_at": row[2], "pending_overdue_1h": int(overdue)}


def _known_servers(conn: sqlite3.Connection) -> Any:
    if "kf_known_servers" not in _tables(conn):
        return {"state": "absent"}
    rows = conn.execute(
        "SELECT domain, source, created_at, last_contact_at, software_name, software_version, shared_kb_count, last_read_at, error_count, last_error, last_error_at "
        "FROM kf_known_servers ORDER BY error_count DESC, domain LIMIT ?",
        (_LIST_LIMIT,),
    ).fetchall()
    return [{**dict(r), "last_error": _short_text(r["last_error"])} for r in rows]


def _remote_kb_reads(conn: sqlite3.Connection) -> Any:
    if "kf_remote_kb_reads" not in _tables(conn):
        return {"state": "absent"}
    rows = conn.execute(
        "SELECT domain, read_started_at, last_read_at, stored_count, refused_count, refused_code, error_count, last_error, last_error_at "
        "FROM kf_remote_kb_reads ORDER BY error_count DESC, domain LIMIT ?",
        (_LIST_LIMIT,),
    ).fetchall()
    return [{**dict(r), "last_error": _short_text(r["last_error"])} for r in rows]


# --- claire.db: shared knowledge bases ------------------------------------------------------------


def collect_shared_kb(settings: Any, *, since: float) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """States of contributions, revisions, mutual collaborations, and automatic contribution rules."""
    warnings: list[dict[str, Any]] = []
    report: dict[str, Any] = {}
    conn = _open_report(Path(settings.db_file), report, warnings, "SHARED_KB_UNREADABLE")
    if conn is None:
        return report, warnings
    code = "SHARED_KB_COLLECTOR_FAILED"
    try:
        for table in ("kb_contributions", "kb_contribution_revisions", "kb_mutual_collaborations", "kb_auto_rules", "kb_submissions", "kb_memberships"):
            def _one(table: str = table) -> Any:
                if table not in _tables(conn):
                    return {"state": "absent"}
                columns = _columns(conn, table)
                state = "state" if "state" in columns else ("status" if "status" in columns else None)
                if state is None:
                    return {"total": _count(conn, table)}
                time_column = next((c for c in ("created_at", "contributed_at", "requested_at", "submitted_at") if c in columns), None)
                return _status_summary(conn, table, status=state, time_column=time_column)

            _guard(report, table, warnings, code, _one)
    finally:
        conn.close()
    return report, warnings


# --- worker queues of each knowledge base database -----------------------------------------------


_WORKER_TABLES = (
    # table, status column, time column, error column
    ("expand_queue", "status", "updated_at", "error"),
    ("refresh_queue", "status", "updated_at", "error"),
    ("jobs", "status", "updated_at", "error"),
    ("syndication_dispatches", "status", "updated_at", "error"),
    ("document_presentations", "status", "updated_at", "error_message"),
)


def collect_worker_queues(
    databases: list[tuple[int, str, Path]],
    *,
    since: float,
    admin_kb_ids: set[int],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Queues of the refresh, recover (``raw_inbox``, already in ``pipeline/inbox_summary.json``), and expand services, the jobs, the dispatches, and the presentations.

    Error texts are only for the knowledge bases of the admin account, like ``failed_items.json``. Other knowledge bases give counts only.
    """
    warnings: list[dict[str, Any]] = []
    report: dict[str, Any] = {}
    seen: set[Path] = set()
    for _kb_id, _label, path in databases:
        resolved = path.resolve(strict=False)
        if resolved in seen:
            continue
        seen.add(resolved)
        entry: dict[str, Any] = {}
        report[str(path)] = entry
        conn = _open_report(path, entry, warnings, "WORKER_QUEUES_UNREADABLE")
        if conn is None:
            continue
        try:
            for table, status, time_column, error in _WORKER_TABLES:
                def _one(table: str = table, status: str = status, time_column: str = time_column, error: str = error) -> Any:
                    summary = _status_summary(conn, table, status=status, time_column=time_column, group=("kb_id",))
                    if summary.get("state") == "absent":
                        return summary
                    columns = _columns(conn, table)
                    if error in columns and "kb_id" in columns and admin_kb_ids:
                        marks = ",".join("?" for _ in admin_kb_ids)
                        rows = conn.execute(
                            f"SELECT kb_id, substr({error}, 1, 400) AS text, COUNT(*), MAX({time_column}) FROM {table} "
                            f"WHERE {error} IS NOT NULL AND {error} != '' AND {time_column} >= ? AND kb_id IN ({marks}) "
                            f"GROUP BY kb_id, text ORDER BY COUNT(*) DESC LIMIT 20",
                            (since, *sorted(admin_kb_ids)),
                        ).fetchall()
                        summary["admin_error_samples"] = [
                            {"kb_id": r[0], "error": _short_text(r[1], keep_urls=True), "count": int(r[2]), "last_at": r[3]} for r in rows
                        ]
                    if "attempts" in columns:
                        summary["max_attempts"] = conn.execute(f"SELECT MAX(attempts) FROM {table}").fetchone()[0]
                    return summary

                _guard(entry, table, warnings, "WORKER_QUEUES_COLLECTOR_FAILED", _one)
        finally:
            conn.close()
    return report, warnings


# --- client.db ------------------------------------------------------------------------------------


def collect_client_db(settings: Any, *, now: float, since: float) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """The communication data of the accounts (client.db): versions, queues, follows, timelines, media cache, and feeds."""
    from ..store.client_db import client_db_path

    warnings: list[dict[str, Any]] = []
    report: dict[str, Any] = {}
    conn = _open_report(client_db_path(settings.db_file), report, warnings, "CLIENT_DB_UNREADABLE")
    if conn is None:
        return report, warnings
    code = "CLIENT_DB_COLLECTOR_FAILED"
    try:
        _guard(report, "client_meta", warnings, code, lambda: {str(r[0]): r[1] for r in conn.execute("SELECT key, value FROM client_meta")})
        _guard(report, "quick_check", warnings, code, lambda: _quick_check(conn))
        _guard(report, "row_counts", warnings, code, lambda: _row_counts(conn))
        _guard(report, "federation_client_outbox", warnings, code, lambda: _status_summary(conn, "federation_client_outbox", status="status", time_column="created_at", group=("kind",)))
        _guard(report, "federation_client_outbox_errors", warnings, code, lambda: _code_counts(conn, "federation_client_outbox", code="error_code", time_column="created_at", since=since, group=("kind",)))
        _guard(report, "federation_following", warnings, code, lambda: _status_summary(conn, "federation_following", status="state", time_column="updated_at"))
        _guard(report, "federation_timeline_items", warnings, code, lambda: _timeline(conn, since))
        _guard(report, "federation_media_cache", warnings, code, lambda: _media_cache(conn))
        _guard(report, "feeds", warnings, code, lambda: _feeds(conn, now, since))
    finally:
        conn.close()
    return report, warnings


def _timeline(conn: sqlite3.Connection, since: float) -> Any:
    if "federation_timeline_items" not in _tables(conn):
        return {"state": "absent"}
    row = conn.execute(
        "SELECT COUNT(*), COUNT(DISTINCT account_id), MIN(received_at), MAX(received_at), SUM(CASE WHEN received_at >= ? THEN 1 ELSE 0 END), SUM(mention) FROM federation_timeline_items",
        (since,),
    ).fetchone()
    return {"total": int(row[0]), "accounts": int(row[1]), "oldest_received_at": row[2], "newest_received_at": row[3], "received_in_period": int(row[4] or 0), "mentions": int(row[5] or 0)}


def _media_cache(conn: sqlite3.Connection) -> Any:
    if "federation_media_cache" not in _tables(conn):
        return {"state": "absent"}
    rows = conn.execute("SELECT state, COUNT(*), SUM(byte_size), MAX(fetched_at) FROM federation_media_cache GROUP BY state ORDER BY state").fetchall()
    return [{"state": r[0], "count": int(r[1]), "bytes": int(r[2] or 0), "last_fetched_at": r[3]} for r in rows]


def _feeds(conn: sqlite3.Connection, now: float, since: float) -> Any:
    if "feed_subscriptions" not in _tables(conn):
        return {"state": "absent"}
    row = conn.execute(
        "SELECT COUNT(*), COUNT(DISTINCT account_id), SUM(CASE WHEN failure_count > 0 THEN 1 ELSE 0 END), MAX(failure_count), "
        "SUM(CASE WHEN next_poll_at IS NOT NULL AND next_poll_at < ? THEN 1 ELSE 0 END), SUM(CASE WHEN last_polled_at >= ? THEN 1 ELSE 0 END), MAX(last_polled_at), MAX(last_success_at) "
        "FROM feed_subscriptions",
        (now - 3600, since),
    ).fetchone()
    by_status = conn.execute(
        "SELECT last_status, COUNT(*), SUM(CASE WHEN failure_count > 0 THEN 1 ELSE 0 END) FROM feed_subscriptions GROUP BY last_status ORDER BY COUNT(*) DESC"
    ).fetchall()
    # The error text of a feed can have the feed address of an account: only the masked text and the count go into the bundle.
    errors = conn.execute(
        "SELECT substr(last_error, 1, 400) AS text, COUNT(*) FROM feed_subscriptions WHERE failure_count > 0 AND last_error IS NOT NULL GROUP BY text ORDER BY COUNT(*) DESC LIMIT 20"
    ).fetchall()
    items = None
    if "feed_items" in _tables(conn):
        item_row = conn.execute("SELECT COUNT(*), SUM(CASE WHEN fetched_at >= ? THEN 1 ELSE 0 END) FROM feed_items", (since,)).fetchone()
        items = {"total": int(item_row[0]), "fetched_in_period": int(item_row[1] or 0)}
    return {
        "subscriptions": int(row[0]),
        "accounts": int(row[1]),
        "failing": int(row[2] or 0),
        "max_failure_count": row[3],
        "poll_overdue_1h": int(row[4] or 0),
        "polled_in_period": int(row[5] or 0),
        "last_polled_at": row[6],
        "last_success_at": row[7],
        "by_last_status": [{"last_status": r[0], "count": int(r[1]), "failing": int(r[2] or 0)} for r in by_status],
        "error_samples": [{"error": _short_text(r[0]), "count": int(r[1])} for r in errors],
        "items": items,
    }


# --- auth.db --------------------------------------------------------------------------------------


def collect_auth_db(settings: Any, *, now: float, since: float) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """The account database (auth.db): version, integrity, row counts, the Telegram bots and the backup policies of the accounts, invitations, and the login throttle. No password hash, key, ciphertext, invitation code, or bot token."""
    from ..store.auth_db import auth_db_path

    warnings: list[dict[str, Any]] = []
    report: dict[str, Any] = {}
    conn = _open_report(auth_db_path(settings.data_dir), report, warnings, "AUTH_DB_UNREADABLE")
    if conn is None:
        return report, warnings
    code = "AUTH_DB_COLLECTOR_FAILED"
    try:
        _guard(report, "meta", warnings, code, lambda: {str(r[0]): r[1] for r in conn.execute("SELECT key, value FROM meta")})
        _guard(report, "quick_check", warnings, code, lambda: _quick_check(conn))
        _guard(report, "row_counts", warnings, code, lambda: _row_counts(conn))
        _guard(report, "telegram_bots", warnings, code, lambda: _telegram_bots(conn))
        _guard(report, "backup_policies", warnings, code, lambda: _backup_policies(conn))
        _guard(report, "invitation_redemptions", warnings, code, lambda: _status_summary(conn, "invitation_redemptions", status="status", time_column="started_at", group=("method",)))
        _guard(report, "login_throttle", warnings, code, lambda: _login_throttle(conn, now))
        _guard(report, "admin_audit_in_period", warnings, code, lambda: _admin_audit(conn, since))
    finally:
        conn.close()
    return report, warnings


def _telegram_bots(conn: sqlite3.Connection) -> Any:
    if "account_telegram_bots" not in _tables(conn):
        return {"state": "absent"}
    rows = conn.execute(
        "SELECT account_id, bot_id, bot_username, enabled, status, last_error, status_at, created_at, updated_at FROM account_telegram_bots ORDER BY account_id LIMIT ?",
        (_LIST_LIMIT,),
    ).fetchall()
    return [{**dict(r), "last_error": _short_text(r["last_error"])} for r in rows]


def _backup_policies(conn: sqlite3.Connection) -> Any:
    if "account_backup_policies" not in _tables(conn):
        return {"state": "absent"}
    rows = conn.execute(
        "SELECT account_id, schedule, keep, last_run_at, last_status, last_error, updated_at FROM account_backup_policies ORDER BY account_id LIMIT ?",
        (_LIST_LIMIT,),
    ).fetchall()
    return [{**dict(r), "last_error": _short_text(r["last_error"])} for r in rows]


def _login_throttle(conn: sqlite3.Connection, now: float) -> Any:
    if "login_throttle" not in _tables(conn):
        return {"state": "absent"}
    # The key of a row is an address or a user name: only counts.
    row = conn.execute("SELECT COUNT(*), SUM(CASE WHEN retry_after > ? THEN 1 ELSE 0 END), MAX(failures) FROM login_throttle", (now,)).fetchone()
    return {"rows": int(row[0]), "locked_now": int(row[1] or 0), "max_failures": row[2]}


def _admin_audit(conn: sqlite3.Connection, since: float) -> Any:
    if "admin_audit" not in _tables(conn):
        return {"state": "absent"}
    rows = conn.execute("SELECT action, COUNT(*), MAX(at) FROM admin_audit WHERE at >= ? GROUP BY action ORDER BY COUNT(*) DESC", (since,)).fetchall()
    return [{"action": r[0], "count": int(r[1]), "last_at": r[2]} for r in rows]


def collect_plugins(settings: Any) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """``diagnostics/plugins.json`` (docs/implementation/INGESTION.md §2.2): states, versions, digests, process reports, and load error texts. No plugin files."""
    from ..plugin_host.service import list_plugins

    data = list_plugins(settings)
    keep = ("id", "origin", "type", "state", "reason", "revision", "core_commit", "api", "maturity", "digest", "overlap_group", "record")
    report = {
        "core_commit": data.get("core_commit"),
        "initialized": data.get("initialized"),
        "plugin_folder": data.get("plugin_folder"),
        "project_plugin_folder": data.get("project_plugin_folder"),
        "unknown_folders": data.get("unknown_folders"),
        "plugins": [{key: item.get(key) for key in keep} for item in data.get("plugins") or []],
        "process_reports": data.get("process_reports"),
    }
    warnings = [
        {"code": "PLUGIN_NOT_LOADED", "message": f"{item['id']}: {item['state']}: {item.get('reason') or ''}"}
        for item in data.get("plugins") or []
        if item.get("state") in ("invalid", "unlocked", "failed", "overlap", "changed", "missing_requirement", "incompatible")
    ]
    return report, warnings

