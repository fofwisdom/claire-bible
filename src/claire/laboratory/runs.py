"""The records of the laboratory runs (``lab_runs``, schema v47): one row for each run with its parameters and its result (JSON)."""

from __future__ import annotations

import json
import secrets
import sqlite3
import time
from dataclasses import dataclass
from typing import Any, Final

# A run that is still "running" after this time stopped with the process (a restart): it shows as failed.
STALE_AFTER_SECONDS: Final = 3600.0
LIST_LIMIT: Final = 20


class RunInProgress(RuntimeError):
    """One run at a time (LABORATORY_DESIGN.md Section 3): HTTP 409 ``run_in_progress``."""


@dataclass(frozen=True, slots=True)
class LabRun:
    id: str
    kind: str
    status: str
    created_at: float
    finished_at: float | None
    created_by: int | None
    params: dict[str, Any]
    result: dict[str, Any] | None
    error: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "status": self.status,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
            "params": self.params,
            "result": self.result,
            "error": self.error,
        }


def _row(row: Any) -> LabRun:
    return LabRun(
        id=str(row[0]),
        kind=str(row[1]),
        status=str(row[2]),
        created_at=float(row[3]),
        finished_at=None if row[4] is None else float(row[4]),
        created_by=None if row[5] is None else int(row[5]),
        params=json.loads(row[6] or "{}"),
        result=None if row[7] is None else json.loads(row[7]),
        error=None if row[8] is None else str(row[8]),
    )


_COLUMNS: Final = "id, kind, status, created_at, finished_at, created_by, params, result, error"


def _close_stale(conn: sqlite3.Connection, *, now: float) -> None:
    conn.execute(
        "UPDATE lab_runs SET status='failed', finished_at=?, error='interrupted' WHERE status='running' AND created_at < ?",
        (now, now - STALE_AFTER_SECONDS),
    )
    conn.commit()


def start(conn: sqlite3.Connection, *, kind: str, params: dict[str, Any], created_by: int | None, now: float | None = None) -> LabRun:
    """Write a new run with the status "running". ``RunInProgress`` when another run is running."""
    when = time.time() if now is None else now
    _close_stale(conn, now=when)
    if conn.execute("SELECT 1 FROM lab_runs WHERE status='running'").fetchone() is not None:
        raise RunInProgress("a laboratory run is running")
    run_id = secrets.token_hex(8)
    conn.execute(
        f"INSERT INTO lab_runs ({_COLUMNS}) VALUES (?, ?, 'running', ?, NULL, ?, ?, NULL, NULL)",
        (run_id, kind, when, created_by, json.dumps(params, ensure_ascii=False)),
    )
    conn.commit()
    return get(conn, run_id)


def finish(conn: sqlite3.Connection, run_id: str, *, result: dict[str, Any], now: float | None = None) -> None:
    conn.execute(
        "UPDATE lab_runs SET status='done', finished_at=?, result=? WHERE id=?",
        (time.time() if now is None else now, json.dumps(result, ensure_ascii=False), run_id),
    )
    conn.commit()


def fail(conn: sqlite3.Connection, run_id: str, *, error: str, now: float | None = None) -> None:
    conn.execute(
        "UPDATE lab_runs SET status='failed', finished_at=?, error=? WHERE id=?",
        (time.time() if now is None else now, error[:500], run_id),
    )
    conn.commit()


def get(conn: sqlite3.Connection, run_id: str) -> LabRun:
    row = conn.execute(f"SELECT {_COLUMNS} FROM lab_runs WHERE id=?", (run_id,)).fetchone()
    if row is None:
        raise LookupError(run_id)
    return _row(row)


def recent(conn: sqlite3.Connection, *, limit: int = LIST_LIMIT, now: float | None = None) -> list[LabRun]:
    _close_stale(conn, now=time.time() if now is None else now)
    return [_row(r) for r in conn.execute(f"SELECT {_COLUMNS} FROM lab_runs ORDER BY created_at DESC LIMIT ?", (int(limit),))]


def delete(conn: sqlite3.Connection, run_id: str) -> bool:
    """Delete a finished run. A running run is not deleted (False)."""
    cur = conn.execute("DELETE FROM lab_runs WHERE id=? AND status != 'running'", (run_id,))
    conn.commit()
    return cur.rowcount == 1
