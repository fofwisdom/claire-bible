"""시스템 건강 상태를 구조화 dict 로 산출 — /health 엔드포인트와 CLI `health` 공유.

`ok`  = 서비스가 DB 에 접근 가능한 살아있는 상태인가(헬스체크 liveness).
`degraded` = 살아는 있으나 사람이 봐야 할 신호(error/failed inbox 누적)가 있는가.
조회만 하고 아무것도 변경하지 않는다.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from .config import Settings
from .store import db as dbm

RECOVER_MAX_ATTEMPTS = 5  # recover-loop 기본값과 동일(due 계산용)


def _connect_readonly(db_path: str | Path) -> sqlite3.Connection:
    """헬스체크용 읽기 전용 연결.

    dbm.connect()는 부모 디렉터리/DB를 만들고 WAL pragma를 설정하므로 상태 조회에는
    사용하지 않는다. DB가 없거나 열 수 없으면 그대로 실패해 liveness가 이를 보고한다.
    """
    uri = Path(db_path).resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON;")
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.execute("PRAGMA busy_timeout=5000;")
    return conn


def schema_version(conn: sqlite3.Connection) -> int:
    """DB에 기록된 스키마 버전을 읽고 형식을 검증한다."""
    row = conn.execute(
        "SELECT value FROM meta WHERE key='schema_version'"
    ).fetchone()
    if row is None:
        raise RuntimeError("schema_version metadata is missing")
    try:
        return int(row["value"])
    except (TypeError, ValueError) as e:
        raise RuntimeError(f"invalid schema_version: {row['value']!r}") from e


def require_current_schema(conn: sqlite3.Connection) -> int:
    """현재 코드가 기대하는 스키마인지 검증하고 실제 버전을 반환한다."""
    actual = schema_version(conn)
    if actual != dbm.SCHEMA_VERSION:
        raise RuntimeError(
            f"schema_version mismatch: actual={actual}, expected={dbm.SCHEMA_VERSION}"
        )
    return actual


def health_report(s: Settings, provider_name: str) -> dict:
    out: dict = {
        "ok": True,
        "provider": provider_name,
        "expected_schema_version": dbm.SCHEMA_VERSION,
    }
    try:
        conn = _connect_readonly(s.db_file)
        try:
            out["schema_version"] = require_current_schema(conn)
            inbox = dbm.inbox_status_counts(conn)
            refresh_pending = len(dbm.pending_refresh(conn))
            recover_due = len(dbm.due_for_recovery(conn, max_attempts=RECOVER_MAX_ATTEMPTS))
            expand_pending = len(dbm.pending_expand(conn))
            graph = dbm.counts(conn)
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001
        out["ok"] = False
        out["db"] = f"error: {e}"
        return out

    out["db"] = "ok"
    out["inbox"] = inbox
    out["refresh_pending"] = refresh_pending
    out["recover_due"] = recover_due
    out["expand_pending"] = expand_pending
    out["graph"] = {k: graph[k] for k in ("documents", "entities", "relations")}

    bdir = s.data_dir / "backups"
    backups = sorted(bdir.glob("claire-*.db")) if bdir.exists() else []
    out["last_backup"] = backups[-1].name if backups else None
    out["backup_count"] = len(backups)

    errors = inbox.get("error", 0)
    failed = inbox.get("failed", 0)
    out["degraded"] = (errors + failed) > 0
    if out["degraded"]:
        out["attention"] = {"error": errors, "failed": failed}
    return out
