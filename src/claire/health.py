"""시스템 건강 상태를 구조화 dict 로 산출 — /health 엔드포인트와 CLI `health` 공유.

`ok`  = 서비스가 DB 에 접근 가능한 살아있는 상태인가(헬스체크 liveness).
`degraded` = 살아는 있으나 사람이 봐야 할 신호(error/failed inbox 누적)가 있는가.
조회만 하고 아무것도 변경하지 않는다.
"""

from __future__ import annotations

from .config import Settings
from .store import db as dbm

RECOVER_MAX_ATTEMPTS = 5  # recover-loop 기본값과 동일(due 계산용)


def health_report(s: Settings, provider_name: str) -> dict:
    out: dict = {"ok": True, "provider": provider_name}
    try:
        conn = dbm.connect(s.db_file)
        dbm.init_db(conn)
        try:
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
