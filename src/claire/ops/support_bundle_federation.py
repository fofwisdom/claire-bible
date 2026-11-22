"""Collectors of the Support Bundles for ActivityPub federation state (docs/implementation/TELEMETRY.md).

Both server-wide and account-scope Support Bundles use these helpers to include sidecar self-check results, traffic counters, outbox task errors, note and reaction counts, and quote states without exposing private content, remote actor addresses, or secrets.

All database reads use read-only connections and never create absent files.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from ..store import db as dbm

__all__ = ["collect_account_federation", "sidecar_findings"]

_HEARTBEAT_STALE_SECONDS = 120.0
_RECENT_LIMIT = 20


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    return datetime.fromtimestamp(float(value), timezone.utc).isoformat()


def _finding(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


def _connect(path: Path) -> sqlite3.Connection:
    return dbm.connect_existing(path, readonly=True)


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {str(r[0]) for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _origin_and_authority(settings: Any) -> tuple[str | None, str | None]:
    try:
        from ..api.security import WebRuntimeConfig

        cfg = WebRuntimeConfig.from_settings(settings)
        return cfg.public_origin, cfg.expected_authority
    except Exception:  # noqa: BLE001
        raw = getattr(settings, "public_url", None)
        if not isinstance(raw, str) or not raw.strip():
            return None, None
        parsed = urlsplit(raw.strip().rstrip("/"))
        if not parsed.scheme or not parsed.netloc:
            return None, None
        return f"{parsed.scheme}://{parsed.netloc}", parsed.netloc


def sidecar_findings(heartbeat: dict[str, Any] | None, *, now: float) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Summarize the Fedify sidecar heartbeat without secrets and return diagnostic findings."""
    findings: list[dict[str, str]] = []
    if not isinstance(heartbeat, dict):
        findings.append(_finding("SIDECAR_HEARTBEAT_ABSENT", "The Fedify sidecar has not written a heartbeat record."))
        return {"state": "absent"}, findings

    received_raw = heartbeat.get("received_at")
    age_seconds = (now - float(received_raw)) if isinstance(received_raw, (int, float)) else None
    state = "stale" if age_seconds is None or age_seconds > _HEARTBEAT_STALE_SECONDS else "recent"
    if state == "stale":
        age_text = f"{int(age_seconds)} s" if isinstance(age_seconds, (int, float)) else "unknown"
        findings.append(_finding("SIDECAR_HEARTBEAT_STALE", f"The latest sidecar heartbeat is stale (age {age_text})."))

    self_check = _as_dict(heartbeat.get("self_check"))
    self_check_ok_raw = self_check.get("ok")
    self_check_ok = self_check_ok_raw if isinstance(self_check_ok_raw, bool) else None

    knowledge = _as_dict(heartbeat.get("knowledge_self_check"))
    failed_paths_raw = knowledge.get("failed_paths")
    failed_paths = [str(p) for p in failed_paths_raw] if isinstance(failed_paths_raw, list) else []

    if self_check_ok is False or failed_paths:
        parts: list[str] = []
        if self_check_ok is False:
            ans = _as_dict(self_check.get("answer"))
            parts.append(f"webfinger ({ans.get('problem') or 'failed'})")
        if failed_paths:
            parts.append(f"knowledge paths {failed_paths}")
        findings.append(_finding("SIDECAR_SELF_CHECK_FAILED", f"Sidecar public path self-check failed: {'; '.join(parts)}."))

    signed_raw = _as_dict(knowledge.get("signed"))
    signed_checks: dict[str, dict[str, Any]] = {}
    failed_forms: list[str] = []
    for form in ("draft_cavage", "rfc9421"):
        entry = signed_raw.get(form)
        if not isinstance(entry, dict):
            continue
        result = entry.get("result")
        answer = _as_dict(entry.get("answer"))
        problem = answer.get("problem")
        status = answer.get("status")
        signed_checks[form] = {"result": result, "problem": problem, "status": status}
        if result not in (None, "ok"):
            detail = f"{form} ({problem or result})"
            failed_forms.append(detail)
    if failed_forms:
        findings.append(_finding("SIDECAR_SIGNED_CHECK_FAILED", f"Signed self-check failed through the public origin: {', '.join(failed_forms)}."))

    traffic_raw = heartbeat.get("traffic")
    traffic: dict[str, Any] | None = None
    if isinstance(traffic_raw, dict):
        iv = _as_dict(traffic_raw.get("inbound_verified"))
        ir = _as_dict(traffic_raw.get("inbound_rejected"))
        dl = _as_dict(traffic_raw.get("deliveries"))
        failed_map = _as_dict(dl.get("failed"))
        failed_total = sum(int(v or 0) for v in failed_map.values())
        rejected_count = int(ir.get("count") or 0)
        traffic = {
            "window_seconds": traffic_raw.get("window_seconds"),
            "inbound_verified_count": int(iv.get("count") or 0),
            "inbound_verified_hosts": int(iv.get("hosts") or 0),
            "inbound_rejected_count": rejected_count,
            "deliveries_succeeded": int(dl.get("succeeded") or 0),
            "deliveries_failed_total": failed_total,
            "deliveries_failed_by_class": {str(k): int(v or 0) for k, v in failed_map.items() if int(v or 0) > 0},
        }
        if rejected_count > 0:
            findings.append(_finding("INBOUND_SIGNATURES_REJECTED", f"{rejected_count} inbound signed requests failed verification in the last 24 hours."))
        if failed_total > 0:
            findings.append(_finding("DELIVERIES_FAILED", f"{failed_total} outbound federation delivery attempts failed in the last 24 hours."))

    summary = {
        "state": state,
        "received_at": _iso(received_raw),
        "age_seconds": round(float(age_seconds), 1) if isinstance(age_seconds, (int, float)) else None,
        "contract_version": heartbeat.get("contract_version"),
        "sidecar_version": heartbeat.get("sidecar_version"),
        "queue_depth": heartbeat.get("queue_depth"),
        "self_check_ok": self_check_ok,
        "knowledge_self_check_failed_paths": failed_paths,
        "signed_checks": signed_checks,
        "traffic": traffic,
    }
    return summary, findings


def _group_status(conn: sqlite3.Connection, table: str, account_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        f"SELECT kind, status, COUNT(*), MAX(created_at) FROM {table} WHERE account_id=? GROUP BY kind, status ORDER BY kind, status",
        (account_id,),
    ).fetchall()
    return [{"kind": str(r[0]), "status": str(r[1]), "count": int(r[2]), "newest_at": _iso(r[3])} for r in rows]


def _recent_errors(conn: sqlite3.Connection, table: str, account_id: int, since: float) -> list[dict[str, Any]]:
    rows = conn.execute(
        f"SELECT kind, error_code, COUNT(*), MAX(created_at) FROM {table} WHERE account_id=? AND status='rejected' AND created_at>=? GROUP BY kind, error_code ORDER BY MAX(created_at) DESC LIMIT ?",
        (account_id, since, _RECENT_LIMIT),
    ).fetchall()
    return [{"kind": str(r[0]), "error_code": r[1], "count": int(r[2]), "last_at": _iso(r[3])} for r in rows]


def collect_account_federation(settings: Any, *, account_id: int, now: float, since: float) -> dict[str, Any]:
    """Collect ActivityPub actor, sidecar health, outbox errors, and note/reaction counts for one account."""
    from ..api.accounts_routes.console_federation_panels import actor_state
    from ..api.federation_routes import read_heartbeat
    from ..core import ids
    from ..store import accounts as accounts_store
    from ..store import federation_client as client_store
    from ..store import federation_quotes as quotes_store
    from ..store.client_db import client_db_path

    data_dir = Path(settings.data_dir)
    sidecar_summary, findings = sidecar_findings(read_heartbeat(data_dir), now=now)
    origin, authority = _origin_and_authority(settings)

    report: dict[str, Any] = {
        "findings": findings,
        "actor": {},
        "sidecar": sidecar_summary,
        "followers": {"total": 0},
        "published_objects": {"total": 0},
        "outbox_tasks": {"by_kind_status": [], "recent_errors": []},
        "client_db": "absent",
    }

    claire = _connect(Path(settings.db_file))
    try:
        account = accounts_store.AccountStore(claire).get(account_id)
        a_state = actor_state(account)
        is_actor = a_state == "active"
        public_id = str(account.public_id) if account.public_id is not None else None
        actor_id = f"{origin}/ap/actors/{public_id}" if (origin and public_id) else None
        handle = f"@{account.username}@{authority}" if (account.username and authority) else None
        report["actor"] = {
            "public_id": public_id,
            "is_actor": is_actor,
            "actor_state": a_state,
            "discoverable": bool(account.profile_public),
            "handle": handle,
            "actor_id": actor_id,
        }
        if not is_actor:
            findings.append(_finding("ACCOUNT_NOT_ACTOR", f"Account is not an active federation actor (state: {a_state})."))

        tables = _tables(claire)
        if "federation_followers" in tables:
            report["followers"] = {"total": int(claire.execute("SELECT COUNT(*) FROM federation_followers WHERE account_id=?", (account_id,)).fetchone()[0])}
        if "federation_objects" in tables:
            report["published_objects"] = {"total": int(claire.execute("SELECT COUNT(*) FROM federation_objects WHERE account_id=?", (account_id,)).fetchone()[0])}
        if "federation_outbox" in tables:
            report["outbox_tasks"] = {
                "by_kind_status": _group_status(claire, "federation_outbox", account_id),
                "recent_errors": _recent_errors(claire, "federation_outbox", account_id, since),
            }
            rejected_outbox = int(claire.execute("SELECT COUNT(*) FROM federation_outbox WHERE account_id=? AND status='rejected'", (account_id,)).fetchone()[0])
            if rejected_outbox > 0:
                findings.append(_finding("OUTBOX_TASKS_REJECTED", f"{rejected_outbox} account outbox tasks were rejected."))
    finally:
        claire.close()

    cpath = client_db_path(settings.db_file)
    if not cpath.is_file():
        return report

    client = _connect(cpath)
    try:
        ctables = _tables(client)
        if not ({"federation_following", "federation_client_outbox", "federation_timeline_items"} & ctables):
            report["client_db"] = "no_federation_tables"
            return report
        report["client_db"] = "present"

        if "federation_following" in ctables:
            frows = client.execute("SELECT state, COUNT(*) FROM federation_following WHERE account_id=? GROUP BY state ORDER BY state", (account_id,)).fetchall()
            report["following"] = {"by_state": {str(r[0]): int(r[1]) for r in frows}}

        if "federation_client_outbox" in ctables:
            report["client_outbox_tasks"] = {
                "by_kind_status": _group_status(client, "federation_client_outbox", account_id),
                "recent_errors": _recent_errors(client, "federation_client_outbox", account_id, since),
            }
            rejected_client = int(client.execute("SELECT COUNT(*) FROM federation_client_outbox WHERE account_id=? AND status='rejected'", (account_id,)).fetchone()[0])
            if rejected_client > 0:
                findings.append(_finding("CLIENT_OUTBOX_TASKS_REJECTED", f"{rejected_client} client outbox tasks were rejected."))

        if "federation_timeline_items" in ctables:
            trow = client.execute(
                "SELECT COUNT(*), SUM(CASE WHEN received_at>=? THEN 1 ELSE 0 END), SUM(mention) FROM federation_timeline_items WHERE account_id=?",
                (since, account_id),
            ).fetchone()
            report["timeline"] = {"total": int(trow[0]), "received_in_period": int(trow[1] or 0), "mentions": int(trow[2] or 0)}

        if "federation_notes" in ctables:
            nrow = client.execute(
                "SELECT COUNT(*), SUM(CASE WHEN deleted_at IS NULL THEN 1 ELSE 0 END), SUM(CASE WHEN deleted_at IS NOT NULL THEN 1 ELSE 0 END), SUM(CASE WHEN published_at>=? THEN 1 ELSE 0 END) FROM federation_notes WHERE account_id=?",
                (since, account_id),
            ).fetchone()
            recent_rows = client.execute(
                "SELECT public_id, published_at, updated_at, edited_at, deleted_at, quote_policy FROM federation_notes WHERE account_id=? ORDER BY published_at DESC, public_id DESC LIMIT ?",
                (account_id, _RECENT_LIMIT),
            ).fetchall()
            note_ids = [int(r[0]) for r in recent_rows]
            reactions = client_store.count_object_reactions(client, note_ids) if "federation_object_reactions" in ctables else {}
            quotes = quotes_store.count_approved_quotes(client, note_ids) if "federation_quote_authorizations" in ctables else {}
            report["notes"] = {
                "total": int(nrow[0]),
                "active": int(nrow[1] or 0),
                "deleted": int(nrow[2] or 0),
                "published_in_period": int(nrow[3] or 0),
                "recent": [
                    {
                        "public_id": ids.format_public_id(int(r[0])),
                        "published_at": _iso(r[1]),
                        "updated_at": _iso(r[2]),
                        "edited_at": _iso(r[3]),
                        "deleted_at": _iso(r[4]),
                        "quote_policy": str(r[5]),
                        "likes": int((reactions.get(int(r[0])) or {}).get("like", 0)),
                        "announces": int((reactions.get(int(r[0])) or {}).get("announce", 0)),
                        "quotes": int(quotes.get(int(r[0]), 0)),
                    }
                    for r in recent_rows
                ],
            }

        if "federation_object_reactions" in ctables:
            rrows = client.execute("SELECT kind, COUNT(*), MAX(received_at) FROM federation_object_reactions WHERE account_id=? GROUP BY kind ORDER BY kind", (account_id,)).fetchall()
            rin = client.execute("SELECT COUNT(*), MAX(received_at) FROM federation_object_reactions WHERE account_id=? AND received_at>=?", (account_id, since)).fetchone()
            report["reactions"] = {
                "by_kind": {str(r[0]): int(r[1]) for r in rrows},
                "received_in_period": int(rin[0]),
                "newest_at": _iso(rin[1]),
            }

        if "federation_quote_authorizations" in ctables:
            qrows = client.execute("SELECT state, COUNT(*) FROM federation_quote_authorizations WHERE account_id=? GROUP BY state ORDER BY state", (account_id,)).fetchall()
            report["quotes"] = {"by_state": {str(r[0]): int(r[1]) for r in qrows}}
    finally:
        client.close()
    return report
