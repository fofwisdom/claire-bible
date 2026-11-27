"""Ingest queue routes for the account owner.

| Path | Method | Function |
| :--- | :--- | :--- |
| ``/api/me/ingest-queue`` | GET, HEAD | Return active slots, queued items, staged items, and recent error or done rows |
| ``/api/me/ingest-queue/{inbox_id}/retry`` | POST | Put one failed inbox row back into the queue and start retry in the background |
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import sqlite3
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response
from starlette.routing import Route

from ...core.secret_scrub import scrub_text
from ...ingest.active import _MAX_EXPENSIVE_JOBS, get_active_jobs
from ...ingest.service import IngestService
from ...store import db as dbm
from ...store import kb_collab, permalinks
from ...store.knowledge_base import KnowledgeBaseInfo, KnowledgeBaseNotFound, get_kb_manager
from ..principal import Principal, request_principal
from .common import AccountsContext, json_error, open_db

ME_INGEST_QUEUE_PATH = "/api/me/ingest-queue"
ME_INGEST_QUEUE_RETRY_PATH = "/api/me/ingest-queue/{inbox_id}/retry"

NO_STORE = {"Cache-Control": "no-store"}
_HISTORY_LIMIT = 12
_TITLE_MAX_LEN = 120
_STAGE_INDEX_MAP: dict[str, int] = {
    "init": 0,
    "fetch": 0,
    "classify_watch": 0,
    "extract": 1,
    "heatmap_matrix": 1,
    "resolve": 2,
    "detail": 3,
    "render_detail": 3,
    "summarize": 3,
    "done": 4,
}
_BACKGROUND_TASKS: set[asyncio.Task[Any]] = set()
_log = logging.getLogger(__name__)


def _settings(ctx: AccountsContext) -> Any:
    current = ctx.current_settings()
    if current is not None:
        return current
    from ...core.config import get_settings

    return get_settings()


def _not_found_no_store() -> Response:
    return PlainTextResponse("Not Found", status_code=404, headers=NO_STORE)


def _owner(request: Request) -> tuple[Principal, Response | None]:
    principal = request_principal(request)
    if principal.account_id is None or principal.scope != "owner":
        return principal, _not_found_no_store()
    return principal, None


def _is_admin(principal: Principal) -> bool:
    return principal.account_id is not None and principal.server_role == "admin" and principal.scope == "owner"


def _positive_int(raw: Any) -> int | None:
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw if raw > 0 else None
    if isinstance(raw, str) and raw.isascii() and raw.isdigit() and len(raw) <= 18:
        value = int(raw)
        return value if value > 0 else None
    return None


def _float_or_none(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _short_title(raw: Any) -> str:
    clean = re.sub(r"\s+", " ", str(raw or "")).strip()
    if len(clean) > _TITLE_MAX_LEN:
        clean = clean[: _TITLE_MAX_LEN - 1].rstrip() + "…"
    return scrub_text(clean)


def _stage_index(stage: str, *, fallback: int = 0) -> int:
    return _STAGE_INDEX_MAP.get(stage.strip().lower(), fallback)


def _inbox_mode(row: sqlite3.Row) -> str:
    raw_mode = row["extraction_mode"] if "extraction_mode" in row.keys() else None
    return raw_mode if raw_mode in ("provider", "client") else "provider"


def _job_mode(job: dict[str, Any]) -> str:
    raw_mode = job.get("mode") or job.get("extraction_mode")
    return raw_mode if raw_mode in ("provider", "client") else "provider"


def _inbox_fallback_title(row: sqlite3.Row) -> str:
    if row["file_name"]:
        return _short_title(row["file_name"])
    if row["kind"] == "composite" and isinstance(row["payload"], str) and row["payload"]:
        try:
            meta = json.loads(row["payload"])
        except Exception:
            meta = None
        if isinstance(meta, dict):
            candidate = meta.get("url") or meta.get("attachment_name")
            if candidate:
                return _short_title(candidate)
    candidate = row["payload"] or row["file_ref"] or f"inbox#{row['id']}"
    return _short_title(candidate)


def _visible_kbs(conn: sqlite3.Connection | None, theme_mgr: Any, *, account_id: int, is_admin: bool) -> tuple[dict[int, KnowledgeBaseInfo], set[int]]:
    all_kbs = list(theme_mgr.list_kbs(include_private=True))
    if is_admin:
        by_id = {kb.id: kb for kb in all_kbs}
        return by_id, set(by_id)
    active_shared: set[int] = set()
    if conn is not None:
        active_shared = set(kb_collab.account_memberships(conn, account_id, state="active").keys())
    visible: dict[int, KnowledgeBaseInfo] = {}
    owned: set[int] = set()
    for kb in all_kbs:
        is_owner = kb.account_id is not None and int(kb.account_id) == int(account_id)
        if is_owner:
            visible[kb.id] = kb
            owned.add(kb.id)
        elif kb.is_shared and kb.id in active_shared:
            visible[kb.id] = kb
    return visible, owned


def _fetch_inbox_rows(conn: sqlite3.Connection, visible_ids: list[int], *, statuses: tuple[str, ...], order_sql: str, limit: int | None = None) -> list[sqlite3.Row]:
    if not visible_ids or not statuses:
        return []
    kb_marks = ",".join("?" for _ in visible_ids)
    status_marks = ",".join("?" for _ in statuses)
    sql = f"SELECT * FROM raw_inbox WHERE kb_id IN ({kb_marks}) AND status IN ({status_marks}) ORDER BY {order_sql}"
    params: list[Any] = [*visible_ids, *statuses]
    if limit is not None:
        sql += " LIMIT ?"
        params.append(int(limit))
    return conn.execute(sql, tuple(params)).fetchall()


def _resolve_document_metadata(conn: sqlite3.Connection, doc_targets: dict[str, int], *, ensure_tokens: bool) -> tuple[dict[str, str], dict[str, str]]:
    if not doc_targets:
        return {}, {}
    doc_ids = list(doc_targets.keys())
    titles: dict[str, str] = {}
    existing_docs: dict[str, str] = {}
    for start in range(0, len(doc_ids), 500):
        chunk = doc_ids[start : start + 500]
        marks = ",".join("?" for _ in chunk)
        for row in conn.execute(f"SELECT id, title, extraction_status FROM documents WHERE id IN ({marks})", tuple(chunk)).fetchall():
            did = str(row["id"])
            existing_docs[did] = str(row["extraction_status"] or "extracted")
            if row["title"]:
                titles[did] = _short_title(row["title"])
    existing_doc_ids = list(existing_docs.keys())
    tokens = permalinks.tokens_of(conn, "document", existing_doc_ids) if existing_doc_ids else {}
    urls: dict[str, str] = {}
    created_token = False
    for did in existing_doc_ids:
        token = tokens.get(did)
        if token is None and ensure_tokens and existing_docs.get(did) != "pending":
            token = permalinks.ensure_document(conn, did, doc_targets[did])
            created_token = True
        if token:
            urls[did] = permalinks.permalink_path(token)
        elif existing_docs.get(did) != "pending":
            urls[did] = f"/?id={quote(did, safe='')}"
    if created_token:
        conn.commit()
    return titles, urls


def _build_job_item(job: dict[str, Any], *, status: str, kb: KnowledgeBaseInfo | None, doc_titles: dict[str, str], doc_urls: dict[str, str], fallback_index: int) -> dict[str, Any]:
    kb_id = int(job.get("kb_id") or 0)
    raw_doc_id = job.get("doc_id") or (job.get("result") or {}).get("document_id")
    doc_id = str(raw_doc_id) if raw_doc_id else None
    raw_stage = str(job.get("stage") or ("done" if status == "done" else ("error" if status == "error" else "init")))
    stage_idx = 4 if status == "done" else _stage_index(raw_stage, fallback=0)
    title = doc_titles.get(doc_id, "") if doc_id else ""
    if not title:
        title = _short_title(job.get("title") or job.get("payload") or job.get("job_id") or "")
    job_id = str(job.get("job_id") or f"job:{kb_id}:{fallback_index}")
    kb_label = scrub_text(kb.label if kb is not None else (job.get("theme_label") or f"KB #{kb_id}"))
    created_at = _float_or_none(job.get("started_at"))
    updated_at = _float_or_none(job.get("updated_at") or job.get("completed_at") or job.get("started_at"))
    return {
        "id": job_id,
        "inbox_id": None,
        "status": status,
        "order": None,
        "title": title,
        "source": scrub_text(str(job.get("source") or "web")),
        "kb_id": kb_id,
        "kb_label": kb_label,
        "mode": _job_mode(job),
        "stage": raw_stage,
        "stage_index": stage_idx,
        "msg": scrub_text(str(job.get("msg") or "")),
        "created_at": created_at,
        "updated_at": updated_at,
        "document_id": doc_id,
        "doc_url": doc_urls.get(doc_id) if doc_id else None,
        "can_retry": False,
    }


def _build_inbox_item(row: sqlite3.Row, *, status: str, order: int | None, kb: KnowledgeBaseInfo | None, owned_kb_ids: set[int], doc_titles: dict[str, str], doc_urls: dict[str, str]) -> dict[str, Any]:
    inbox_id = int(row["id"])
    kb_id = int(row["kb_id"])
    doc_id = str(row["document_id"]) if row["document_id"] else None
    if status == "done":
        stage, stage_idx = "done", 4
    elif status == "staged":
        stage, stage_idx = "staged", 1
    elif status == "error":
        stage, stage_idx = "error", 0
    else:
        stage, stage_idx = "queued", 0
    title = (doc_titles.get(doc_id, "") if doc_id else "") or _inbox_fallback_title(row)
    mode = "client" if status == "staged" else _inbox_mode(row)
    raw_status = str(row["status"] or "")
    can_retry = raw_status in ("error", "failed") and kb_id in owned_kb_ids
    msg = scrub_text(str(row["error"] or "")) if status == "error" else ""
    created_at = _float_or_none(row["received_at"])
    updated_at = _float_or_none(row["last_attempt"] if row["last_attempt"] is not None else row["received_at"])
    return {
        "id": f"inbox:{inbox_id}",
        "inbox_id": inbox_id,
        "status": status,
        "order": order,
        "title": title,
        "source": scrub_text(str(row["source"] or "web")),
        "kb_id": kb_id,
        "kb_label": scrub_text(kb.label if kb is not None else f"KB #{kb_id}"),
        "mode": mode,
        "stage": stage,
        "stage_index": stage_idx,
        "msg": msg,
        "created_at": created_at,
        "updated_at": updated_at,
        "document_id": doc_id,
        "doc_url": doc_urls.get(doc_id) if (doc_id and status != "staged") else None,
        "can_retry": can_retry,
    }


def _collect_queue_state(ctx: AccountsContext, principal: Principal) -> dict[str, Any]:
    assert principal.account_id is not None
    settings = _settings(ctx)
    theme_mgr = get_kb_manager(settings)
    is_admin = _is_admin(principal)
    conn = open_db(ctx) if ctx.db_file.is_file() else None
    try:
        visible_kbs, owned_kb_ids = _visible_kbs(conn, theme_mgr, account_id=int(principal.account_id), is_admin=is_admin)
        visible_ids = sorted(visible_kbs.keys())
        all_jobs = get_active_jobs(settings.data_dir)
        running_jobs = [j for j in all_jobs if j.get("active") and int(j.get("kb_id") or 0) in visible_kbs]
        completed_jobs = [j for j in all_jobs if not j.get("active") and int(j.get("kb_id") or 0) in visible_kbs]

        running_payloads: dict[int, set[str]] = {}
        running_doc_ids: dict[int, set[str]] = {}
        for job in running_jobs:
            jid_kb = int(job.get("kb_id") or 0)
            payload_val = job.get("payload")
            if isinstance(payload_val, str) and payload_val:
                bucket = running_payloads.setdefault(jid_kb, set())
                bucket.add(payload_val)
                norm = dbm._normalize_inbox_match_value(payload_val)
                if norm:
                    bucket.add(norm)
            doc_val = job.get("doc_id") or (job.get("result") or {}).get("document_id")
            if doc_val:
                running_doc_ids.setdefault(jid_kb, set()).add(str(doc_val))

        queued_rows: list[sqlite3.Row] = []
        staged_rows: list[sqlite3.Row] = []
        error_rows: list[sqlite3.Row] = []
        done_rows: list[sqlite3.Row] = []
        if conn is not None and visible_ids:
            raw_received = _fetch_inbox_rows(conn, visible_ids, statuses=("received",), order_sql="received_at ASC, id ASC")
            for row in raw_received:
                rkb = int(row["kb_id"])
                active_set = running_payloads.get(rkb, set())
                if active_set and (dbm._inbox_row_active_candidates(row) & active_set):
                    continue
                if row["document_id"] and str(row["document_id"]) in running_doc_ids.get(rkb, set()):
                    continue
                queued_rows.append(row)
            staged_rows = _fetch_inbox_rows(conn, visible_ids, statuses=("staged",), order_sql="received_at ASC, id ASC")
            error_rows = _fetch_inbox_rows(conn, visible_ids, statuses=("error", "failed"), order_sql="COALESCE(last_attempt, received_at) DESC, id DESC", limit=_HISTORY_LIMIT)
            done_rows = _fetch_inbox_rows(conn, visible_ids, statuses=("done",), order_sql="COALESCE(last_attempt, received_at) DESC, id DESC", limit=_HISTORY_LIMIT)

        history_rows = [*error_rows, *done_rows]
        history_doc_keys = {(int(r["kb_id"]), str(r["document_id"])) for r in history_rows if r["document_id"]}
        history_payloads: dict[int, set[str]] = {}
        for row in history_rows:
            history_payloads.setdefault(int(row["kb_id"]), set()).update(dbm._inbox_row_active_candidates(row))

        dedup_completed_jobs: list[dict[str, Any]] = []
        for job in completed_jobs:
            jkb = int(job.get("kb_id") or 0)
            jdoc = job.get("doc_id") or (job.get("result") or {}).get("document_id")
            if jdoc and (jkb, str(jdoc)) in history_doc_keys:
                continue
            jpayload = job.get("payload")
            jnorm = dbm._normalize_inbox_match_value(jpayload) if isinstance(jpayload, str) else ""
            kb_hist_payloads = history_payloads.get(jkb, set())
            if kb_hist_payloads and ((isinstance(jpayload, str) and jpayload in kb_hist_payloads) or (jnorm and jnorm in kb_hist_payloads)):
                continue
            dedup_completed_jobs.append(job)

        doc_targets: dict[str, int] = {}
        for job in (*running_jobs, *dedup_completed_jobs):
            did = job.get("doc_id") or (job.get("result") or {}).get("document_id")
            if did:
                doc_targets[str(did)] = int(job.get("kb_id") or 0)
        for row in (*queued_rows, *staged_rows, *history_rows):
            if row["document_id"]:
                doc_targets[str(row["document_id"])] = int(row["kb_id"])

        doc_titles, doc_urls = _resolve_document_metadata(conn, doc_targets, ensure_tokens=True) if conn is not None else ({}, {})

        running_items = [_build_job_item(job, status="running", kb=visible_kbs.get(int(job.get("kb_id") or 0)), doc_titles=doc_titles, doc_urls=doc_urls, fallback_index=idx) for idx, job in enumerate(running_jobs)]
        queued_items = [_build_inbox_item(row, status="queued", order=idx + 1, kb=visible_kbs.get(int(row["kb_id"])), owned_kb_ids=owned_kb_ids, doc_titles=doc_titles, doc_urls=doc_urls) for idx, row in enumerate(queued_rows)]
        staged_items = [_build_inbox_item(row, status="staged", order=None, kb=visible_kbs.get(int(row["kb_id"])), owned_kb_ids=owned_kb_ids, doc_titles=doc_titles, doc_urls=doc_urls) for row in staged_rows]

        history_items: list[dict[str, Any]] = []
        for row in error_rows:
            history_items.append(_build_inbox_item(row, status="error", order=None, kb=visible_kbs.get(int(row["kb_id"])), owned_kb_ids=owned_kb_ids, doc_titles=doc_titles, doc_urls=doc_urls))
        for row in done_rows:
            history_items.append(_build_inbox_item(row, status="done", order=None, kb=visible_kbs.get(int(row["kb_id"])), owned_kb_ids=owned_kb_ids, doc_titles=doc_titles, doc_urls=doc_urls))
        for idx, job in enumerate(dedup_completed_jobs):
            jstatus = "error" if str(job.get("stage") or "").lower() == "error" else "done"
            history_items.append(_build_job_item(job, status=jstatus, kb=visible_kbs.get(int(job.get("kb_id") or 0)), doc_titles=doc_titles, doc_urls=doc_urls, fallback_index=idx))
        history_items.sort(key=lambda item: (float(item["updated_at"] or 0.0), item["inbox_id"] or 0), reverse=True)

        error_count = sum(1 for item in history_items if item["status"] == "error")
        done_count = sum(1 for item in history_items if item["status"] == "done")
        items = [*running_items, *queued_items, *staged_items, *history_items]
        return {
            "max_slots": _MAX_EXPENSIVE_JOBS,
            "busy_slots": len(running_items),
            "counts": {
                "running": len(running_items),
                "queued": len(queued_items),
                "staged": len(staged_items),
                "error": error_count,
                "done": done_count,
            },
            "items": items,
            "server_time": time.time(),
        }
    finally:
        if conn is not None:
            conn.close()


def _prepare_retry(ctx: AccountsContext, principal: Principal, inbox_id: int) -> tuple[str, Any, KnowledgeBaseInfo] | tuple[str, int, str | None]:
    """Check ownership and status, then mark the inbox row as received before background execution."""
    if not ctx.db_file.is_file():
        return ("not_found", 404, None)
    assert principal.account_id is not None
    settings = _settings(ctx)
    theme_mgr = get_kb_manager(settings)
    is_admin = _is_admin(principal)
    conn = open_db(ctx)
    try:
        row = dbm.get_inbox(conn, inbox_id)
        if row is None:
            return ("not_found", 404, None)
        kb_id = int(row["kb_id"])
        try:
            kb = theme_mgr.get_kb(kb_id)
        except KnowledgeBaseNotFound:
            return ("not_found", 404, None)
        owns_kb = kb.account_id is not None and int(kb.account_id) == int(principal.account_id)
        if not (is_admin or owns_kb):
            return ("not_found", 404, None)
        row_status = str(row["status"] or "")
        if row_status not in ("error", "failed"):
            return ("conflict", 409, row_status)
        cur = conn.execute("UPDATE raw_inbox SET status='received', error=NULL, next_retry_at=NULL WHERE id=? AND status IN ('error', 'failed')", (inbox_id,))
        conn.commit()
        if cur.rowcount == 0:
            return ("conflict", 409, row_status)
        kb_settings = theme_mgr.get_settings_for_kb(kb.id, settings)
        return ("ok", kb_settings, kb)
    finally:
        conn.close()


def _run_retry_inbox(kb_settings: Any, kb_id: int, inbox_id: int) -> None:
    IngestService(kb_settings, kb_id=kb_id).retry_inbox(inbox_id)


def _mark_retry_error(db_file: Path, inbox_id: int, error_message: str) -> None:
    if not db_file.is_file():
        return
    conn = dbm.connect_existing(db_file)
    try:
        conn.execute("UPDATE raw_inbox SET status='error', error=? WHERE id=?", (scrub_text(error_message), inbox_id))
        conn.commit()
    finally:
        conn.close()


def routes(ctx: AccountsContext) -> list[Route]:
    async def ingest_queue_get(request: Request) -> Response:
        principal, denied = _owner(request)
        if denied is not None:
            return denied
        state = await asyncio.to_thread(_collect_queue_state, ctx, principal)
        return JSONResponse(state, headers=NO_STORE)

    async def ingest_queue_retry_post(request: Request) -> Response:
        principal, denied = _owner(request)
        if denied is not None:
            return denied
        inbox_id = _positive_int(request.path_params.get("inbox_id"))
        if inbox_id is None:
            return _not_found_no_store()
        outcome = await asyncio.to_thread(_prepare_retry, ctx, principal, inbox_id)
        kind = outcome[0]
        if kind == "not_found":
            return _not_found_no_store()
        if kind == "conflict":
            return JSONResponse({"error": "inbox_not_retryable", "status": outcome[2]}, status_code=409, headers=NO_STORE)
        _, kb_settings, kb = outcome

        async def _background_retry() -> None:
            try:
                await asyncio.to_thread(_run_retry_inbox, kb_settings, kb.id, inbox_id)
            except Exception as exc:  # noqa: BLE001
                safe_msg = scrub_text(exc)
                _log.error("Background retry failed for inbox#%s (kb=%s): %s", inbox_id, kb.id, safe_msg)
                try:
                    await asyncio.to_thread(_mark_retry_error, Path(kb_settings.db_file), inbox_id, safe_msg)
                except Exception:  # noqa: BLE001
                    _log.warning("Failed to record retry error for inbox#%s", inbox_id)

        task = asyncio.create_task(_background_retry())
        _BACKGROUND_TASKS.add(task)
        task.add_done_callback(_BACKGROUND_TASKS.discard)
        return JSONResponse({"ok": True, "inbox_id": inbox_id, "status": "queued"}, status_code=202, headers=NO_STORE)

    return [
        Route(ME_INGEST_QUEUE_PATH, ingest_queue_get, methods=["GET", "HEAD"]),
        Route(ME_INGEST_QUEUE_RETRY_PATH, ingest_queue_retry_post, methods=["POST"]),
    ]
