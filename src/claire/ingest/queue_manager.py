"""Queue coordinator for deferred cancel/purge work and slot-capped retries."""

from __future__ import annotations

import json
import logging
import os
import secrets
import threading
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

from ..core.secret_scrub import scrub_text
from ..store import db as dbm
from . import active as active_mod

_T = TypeVar("_T")
_log = logging.getLogger(__name__)
_REGISTRY_LOCK = threading.Lock()
_COORDINATORS: dict[str, "IngestQueueCoordinator"] = {}


def _safe_raw_file_path(data_dir: Path, file_ref: Any) -> Path | None:
    if not isinstance(file_ref, str) or not file_ref.strip():
        return None
    raw_root = (data_dir / "raw").resolve()
    ref_path = Path(file_ref.strip())
    candidate = ref_path.resolve() if ref_path.is_absolute() else (data_dir / ref_path).resolve()
    if not candidate.is_relative_to(raw_root):
        candidate = ref_path.resolve()
        if not candidate.is_relative_to(raw_root):
            return None
    return candidate if candidate.is_file() else None


def unlink_unreferenced_raw_file(conn: Any, data_dir: Path, file_ref: Any) -> int:
    """Delete ``file_ref`` inside ``data_dir / 'raw'`` when no ``raw_inbox`` row still points to it."""
    safe_path = _safe_raw_file_path(data_dir, file_ref)
    if safe_path is None:
        return 0
    row = conn.execute("SELECT COUNT(*) FROM raw_inbox WHERE file_ref=?", (str(file_ref),)).fetchone()
    if row and int(row[0]) > 0:
        return 0
    try:
        safe_path.unlink(missing_ok=True)
        return 1
    except Exception:  # noqa: BLE001
        return 0


class IngestQueueCoordinator:
    """Serialize SQLite queue writes, defer document purges while ingest runs, and cap retry concurrency."""

    def __init__(self, data_dir: Path | str | None = None, *, poll_interval: float = 0.5) -> None:
        self.data_dir = Path(data_dir or "data").resolve()
        self.ops_dir = self.data_dir / "queue_ops"
        self.poll_interval = max(0.01, float(poll_interval))
        self._write_lock = threading.Lock()
        self._state_lock = threading.RLock()
        self._cond = threading.Condition(self._state_lock)
        self._retry_queue: deque[dict[str, Any]] = deque()
        self._queued_retry_ids: set[int] = set()
        self._in_flight_retries: set[int] = set()
        self._worker: threading.Thread | None = None
        self._stopped = False

    def has_active_ingest_jobs(self, data_dir: Path | None = None) -> bool:
        """Return True when any active ingest job or in-flight retry is running."""
        target_dir = Path(data_dir) if data_dir is not None else self.data_dir
        if any(j.get("active") for j in active_mod.get_active_jobs(target_dir)):
            return True
        with self._state_lock:
            return len(self._in_flight_retries) > 0

    def active_ingest_count(self) -> int:
        """Count active ingest jobs plus in-flight retries not yet tracked in ``active_jobs``."""
        active_jobs = [j for j in active_mod.get_active_jobs(self.data_dir) if j.get("active")]
        represented_ids: set[int] = set()
        for job in active_jobs:
            raw_id = job.get("inbox_id")
            if raw_id is not None:
                try:
                    represented_ids.add(int(raw_id))
                except (TypeError, ValueError):
                    pass
        with self._state_lock:
            unrepresented = sum(1 for iid in self._in_flight_retries if iid not in represented_ids)
        return len(active_jobs) + unrepresented

    def list_pending_cancel_ops(self) -> list[dict[str, Any]]:
        """Read valid ``queue_ops/cancel_*.json`` descriptors ordered by ``(queued_at, inbox_id)``."""
        if not self.ops_dir.is_dir():
            return []
        ops: list[dict[str, Any]] = []
        for path in self.ops_dir.glob("cancel_*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    continue
                if "inbox_id" not in data or "db_file" not in data:
                    continue
                ops.append(data)
            except Exception:  # noqa: BLE001
                continue
        ops.sort(key=lambda op: (float(op.get("queued_at") or 0.0), int(op.get("inbox_id") or 0)))
        return ops

    def run_serialized_write(self, fn: Callable[[], _T]) -> _T:
        """Run a DB write callback under the coordinator write lock with busy-lock retries."""
        with self._write_lock:
            return dbm.run_with_busy_retry(fn)

    def _op_path(self, inbox_id: int) -> Path:
        return self.ops_dir / f"cancel_{int(inbox_id)}.json"

    def _write_op_atomic(self, op: dict[str, Any]) -> None:
        self.ops_dir.mkdir(parents=True, exist_ok=True)
        target = self._op_path(int(op["inbox_id"]))
        tmp = target.with_name(f"{target.name}.tmp.{secrets.token_hex(4)}")
        tmp.write_text(json.dumps(op, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, target)

    def _remove_op(self, inbox_id: int) -> None:
        try:
            self._op_path(inbox_id).unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass

    def _ensure_worker_locked(self) -> None:
        if self._stopped:
            return
        if self._worker is None or not self._worker.is_alive():
            self._worker = threading.Thread(target=self._worker_loop, name="claire-ingest-queue", daemon=True)
            self._worker.start()

    def enqueue_cancel_purge(self, *, db_file: Path, inbox_id: int, kb_id: int, document_id: str | None, prev_status: str, tombstone: bool, force: bool, reason: str, file_ref: str | None) -> dict[str, Any]:
        """Persist a deferred cancel/purge operation and wake the background worker."""
        op = {
            "inbox_id": int(inbox_id),
            "kb_id": int(kb_id),
            "db_file": str(db_file),
            "document_id": document_id,
            "prev_status": prev_status,
            "tombstone": bool(tombstone),
            "force": bool(force),
            "reason": reason,
            "file_ref": file_ref,
            "queued_at": time.time(),
        }
        self._write_op_atomic(op)
        with self._cond:
            self._stopped = False
            self._ensure_worker_locked()
            self._cond.notify_all()
        return op

    def enqueue_retry(self, *, db_file: Path, kb_settings: Any, kb_id: int, inbox_id: int, retry_fn: Callable[[Any, int, int], Any]) -> None:
        """Append a deduplicated retry request to the FIFO queue and wake the worker."""
        iid = int(inbox_id)
        with self._cond:
            self._stopped = False
            if iid in self._queued_retry_ids or iid in self._in_flight_retries:
                return
            self._retry_queue.append({"db_file": Path(db_file), "kb_settings": kb_settings, "kb_id": int(kb_id), "inbox_id": iid, "retry_fn": retry_fn})
            self._queued_retry_ids.add(iid)
            self._ensure_worker_locked()
            self._cond.notify_all()

    def ensure_recovery(self, db_file: Path, settings: Any) -> None:
        """Ensure pending cancel op files and orphaned ``status='cancelling'`` DB rows are drained."""
        pending_ops = self.list_pending_cancel_ops()
        pending_ids = {int(op["inbox_id"]) for op in pending_ops if op.get("inbox_id") is not None}
        orphan_rows: list[tuple[int, int, str | None, str | None]] = []
        db_path = Path(db_file)
        if db_path.is_file():
            conn = dbm.connect_existing(db_path, readonly=True)
            try:
                rows = conn.execute("SELECT id, kb_id, document_id, file_ref FROM raw_inbox WHERE status='cancelling' ORDER BY received_at ASC, id ASC").fetchall()
                for row in rows:
                    iid = int(row["id"])
                    if iid not in pending_ids:
                        orphan_rows.append((iid, int(row["kb_id"]), str(row["document_id"]) if row["document_id"] else None, str(row["file_ref"]) if row["file_ref"] else None))
            finally:
                conn.close()
        tombstone_default = bool(getattr(settings, "is_purge_allowed", False))
        for iid, kb_id, doc_id, file_ref in orphan_rows:
            self.enqueue_cancel_purge(db_file=db_path, inbox_id=iid, kb_id=kb_id, document_id=doc_id, prev_status="done", tombstone=tombstone_default, force=False, reason="queue_recovery_cancel", file_ref=file_ref)
        if pending_ops:
            with self._cond:
                self._stopped = False
                self._ensure_worker_locked()
                self._cond.notify_all()

    def _run_cancel_op(self, op: dict[str, Any]) -> None:
        inbox_id = int(op["inbox_id"])
        kb_id = int(op["kb_id"])
        db_file = Path(str(op["db_file"]))
        if self.has_active_ingest_jobs():
            return
        try:
            def _execute() -> None:
                if not db_file.is_file():
                    return
                conn = dbm.connect_existing(db_file)
                try:
                    row = conn.execute("SELECT id, kb_id, document_id, status, file_ref FROM raw_inbox WHERE id=? AND kb_id=?", (inbox_id, kb_id)).fetchone()
                    if row is None or str(row["status"] or "") != "cancelling":
                        return
                    doc_id = op.get("document_id") or (str(row["document_id"]) if row["document_id"] else None)
                    if doc_id:
                        has_doc = conn.execute("SELECT 1 FROM documents WHERE id=? AND kb_id=?", (doc_id, kb_id)).fetchone() is not None
                        if has_doc:
                            dbm.purge_document_cascade(conn, self.data_dir, [doc_id], reason=str(op.get("reason") or "queue_panel_purge"), dry_run=False, tombstone=bool(op.get("tombstone", False)), kb_id=kb_id, heal=False, vacuum=False)
                            active_mod.purge_active_matrix(self.data_dir, doc_id)
                    conn.execute("DELETE FROM raw_inbox WHERE id=? AND kb_id=?", (inbox_id, kb_id))
                    conn.commit()
                    unlink_unreferenced_raw_file(conn, self.data_dir, row["file_ref"] or op.get("file_ref"))
                finally:
                    conn.close()

            self.run_serialized_write(_execute)
        except Exception as exc:  # noqa: BLE001
            safe_msg = scrub_text(exc)
            _log.error("Deferred cancel/purge failed for inbox#%s (kb=%s): %s", inbox_id, kb_id, safe_msg)
            def _restore_error() -> None:
                if not db_file.is_file():
                    return
                conn = dbm.connect_existing(db_file)
                try:
                    conn.execute("UPDATE raw_inbox SET status='error', error=? WHERE id=? AND kb_id=? AND status='cancelling'", (safe_msg, inbox_id, kb_id))
                    conn.commit()
                finally:
                    conn.close()

            try:
                self.run_serialized_write(_restore_error)
            except Exception:  # noqa: BLE001
                _log.warning("Failed to restore cancelling row inbox#%s to error", inbox_id)
        finally:
            self._remove_op(inbox_id)
            with self._cond:
                self._cond.notify_all()

    def _spawn_retry(self, item: dict[str, Any]) -> None:
        db_file = Path(item["db_file"])
        kb_settings = item["kb_settings"]
        kb_id = int(item["kb_id"])
        inbox_id = int(item["inbox_id"])
        retry_fn = item["retry_fn"]

        still_received = False
        if db_file.is_file():
            conn = dbm.connect_existing(db_file, readonly=True)
            try:
                row = conn.execute("SELECT status FROM raw_inbox WHERE id=? AND kb_id=?", (inbox_id, kb_id)).fetchone()
                still_received = row is not None and str(row["status"] or "") == "received"
            finally:
                conn.close()
        if not still_received:
            with self._cond:
                self._in_flight_retries.discard(inbox_id)
                self._cond.notify_all()
            return

        def _runner() -> None:
            try:
                retry_fn(kb_settings, kb_id, inbox_id)
            except Exception as exc:  # noqa: BLE001
                safe_msg = scrub_text(exc)
                _log.error("Background retry failed for inbox#%s (kb=%s): %s", inbox_id, kb_id, safe_msg)

                def _mark_err() -> None:
                    if not db_file.is_file():
                        return
                    conn_err = dbm.connect_existing(db_file)
                    try:
                        conn_err.execute("UPDATE raw_inbox SET status='error', error=? WHERE id=? AND status='received'", (safe_msg, inbox_id))
                        conn_err.commit()
                    finally:
                        conn_err.close()

                try:
                    self.run_serialized_write(_mark_err)
                except Exception:  # noqa: BLE001
                    _log.warning("Failed to record retry error for inbox#%s", inbox_id)
            finally:
                with self._cond:
                    self._in_flight_retries.discard(inbox_id)
                    if self._retry_queue or self.list_pending_cancel_ops():
                        self._ensure_worker_locked()
                    self._cond.notify_all()

        thread = threading.Thread(target=_runner, name=f"claire-retry-{inbox_id}", daemon=True)
        thread.start()

    def _worker_loop(self) -> None:
        while True:
            pending_ops = self.list_pending_cancel_ops()
            if pending_ops:
                if self.has_active_ingest_jobs():
                    with self._cond:
                        if self._stopped:
                            self._worker = None
                            return
                        self._cond.wait(timeout=self.poll_interval)
                    continue
                self._run_cancel_op(pending_ops[0])
                continue

            if self.active_ingest_count() >= active_mod._MAX_EXPENSIVE_JOBS:
                with self._cond:
                    if self._stopped:
                        self._worker = None
                        return
                    self._cond.wait(timeout=self.poll_interval)
                continue

            retry_item: dict[str, Any] | None = None
            with self._cond:
                if self._stopped:
                    self._worker = None
                    return
                if self.list_pending_cancel_ops():
                    continue
                if not self._retry_queue:
                    self._worker = None
                    return
                retry_item = self._retry_queue.popleft()
                iid = int(retry_item["inbox_id"])
                self._queued_retry_ids.discard(iid)
                self._in_flight_retries.add(iid)
            self._spawn_retry(retry_item)

    def stop(self, *, timeout: float = 2.0) -> None:
        """Stop the coordinator worker thread cleanly."""
        worker: threading.Thread | None
        with self._cond:
            self._stopped = True
            worker = self._worker
            self._retry_queue.clear()
            self._queued_retry_ids.clear()
            self._cond.notify_all()
        if worker is not None and worker is not threading.current_thread() and worker.is_alive():
            worker.join(timeout=timeout)


def get_queue_coordinator(data_dir: Path | str | None = None, *, poll_interval: float = 0.5) -> IngestQueueCoordinator:
    """Return the singleton ``IngestQueueCoordinator`` for ``data_dir``."""
    resolved = Path(data_dir or "data").resolve()
    key = str(resolved)
    with _REGISTRY_LOCK:
        coord = _COORDINATORS.get(key)
        if coord is None:
            coord = IngestQueueCoordinator(resolved, poll_interval=poll_interval)
            _COORDINATORS[key] = coord
        return coord


def reset_queue_coordinators() -> None:
    """Stop and clear all coordinator instances (for tests)."""
    with _REGISTRY_LOCK:
        coordinators = list(_COORDINATORS.values())
        _COORDINATORS.clear()
    for coord in coordinators:
        coord.stop()
