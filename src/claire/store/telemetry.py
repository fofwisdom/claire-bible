"""격리된 관측성 데이터베이스 (data/telemetry.db) 및 프로바이더 텔레메트리 스토어.

정본 지식 DB(claire.db)의 쓰기 락 경합 및 용량 오염을 원천 차단하기 위해,
프로바이더 호출 계측, 종료 코드, stderr, Google 정책 차단 진단 데이터는
물리적으로 분리된 telemetry.db에만 기록된다.
텔레메트리 기록 실패는 메인 파이프라인의 성공 여부에 절대 영향을 미치지 않는다(Fire-and-Forget).
"""

from __future__ import annotations

import logging
import sqlite3
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_TELEMETRY_RETENTION_DAYS = 30

TELEMETRY_SCHEMA = """
CREATE TABLE IF NOT EXISTS provider_telemetry (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    document_id TEXT,
    provider TEXT NOT NULL,
    model TEXT,
    call_type TEXT NOT NULL,
    input_chars INTEGER DEFAULT 0,
    input_bytes INTEGER DEFAULT 0,
    delivery_mode TEXT,
    exit_code INTEGER,
    duration_ms INTEGER,
    status TEXT NOT NULL,
    google_block_reason TEXT,
    error_message TEXT,
    output_snippet TEXT,
    summary_verdict TEXT
);
CREATE INDEX IF NOT EXISTS idx_telemetry_doc ON provider_telemetry(document_id);
CREATE INDEX IF NOT EXISTS idx_telemetry_status ON provider_telemetry(status);
CREATE INDEX IF NOT EXISTS idx_telemetry_reason ON provider_telemetry(google_block_reason);
CREATE INDEX IF NOT EXISTS idx_telemetry_time ON provider_telemetry(timestamp);

CREATE TABLE IF NOT EXISTS support_bundles (
    bundle_id TEXT PRIMARY KEY,
    token TEXT NOT NULL UNIQUE,
    filename TEXT NOT NULL,
    filepath TEXT NOT NULL,
    days_covered INTEGER NOT NULL,
    target_doc_id TEXT,
    size_bytes INTEGER NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_support_bundles_token ON support_bundles(token);
CREATE INDEX IF NOT EXISTS idx_support_bundles_expires ON support_bundles(expires_at);
"""



def get_telemetry_db_path(data_dir: Path | str | None) -> Path:
    """데이터 루트 디렉터리 내의 telemetry.db 경로 반환."""
    if data_dir is None:
        return Path("data") / "telemetry.db"
    p = Path(data_dir)
    return p if p.name.endswith(".db") else p / "telemetry.db"


def connect_telemetry(db_path: Path | str) -> sqlite3.Connection:
    """격리된 telemetry.db 전용 연결 생성 및 스키마 초기화."""
    p = Path(db_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p), timeout=1.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=1000;")
    conn.executescript(TELEMETRY_SCHEMA)
    return conn


def diagnose_google_block(
    returncode: int | None,
    stderr: str | None,
    stdout: str | None,
    status: str | None = None,
) -> str:
    """stderr 및 응답 텍스트에서 Google API 및 CLI 가이드라인/정책 차단 코드를 정밀 분류."""
    combined = f"{stderr or ''}\n{stdout or ''}".lower()

    if "recitation" in combined:
        return "RECITATION"
    if any(k in combined for k in ("safety", "harmful content", "safety_violations", "prohibited_content")):
        return "SAFETY"
    if any(k in combined for k in ("429", "rate_limit", "too many requests", "resourceexhausted")):
        return "RATE_LIMIT_429"
    if any(k in combined for k in ("quota_exceeded", "daily quota", "quota exceeded")):
        return "QUOTA_EXCEEDED"
    if any(k in combined for k in ("max_tokens", "thinking tokens", "token limit", "finishreason_max_tokens")):
        return "MAX_TOKENS"
    if any(k in combined for k in ("invalid_argument", "schema is invalid", "invalid schema", "json_schema", "json schema")) or ("schema" in combined and "invalid" in combined):
        return "INVALID_SCHEMA"
    if any(k in combined for k in ("timeout", "timed out", "deadline_exceeded")):
        return "TIMEOUT"
    if any(k in combined for k in ("env_missing", "not found in path", "permission denied")):
        return "ENV_MISSING"

    if returncode == 0 and (not status or status == "SUCCESS"):
        return "NONE"
    if returncode is not None and returncode != 0:
        return "CLI_ERROR"
    return "NONE"


def evaluate_summary_verdict(
    summary: str | None,
    raw_text: str | None,
    provider_name: str | None = None,
) -> str:
    """추출된 요약문의 내용 실체성 및 결손(Fallback) 형태 판정."""
    from ..extract.prompts import is_corrupted_summary

    if not summary or not summary.strip():
        return "EMPTY"

    s = summary.strip()
    if s.startswith("[mock]") or provider_name == "mock":
        return "MOCK_PREFIX"

    if raw_text and len(raw_text) > 50:
        clean_s = s.rstrip("…").strip()
        clean_raw = raw_text.strip()
        if clean_raw.startswith(clean_s) and len(clean_s) <= 210:
            return "RAW_SLICE_200"

    if s.endswith("등에 관한 자료이다.") and len(s) < 100:
        return "TEMPLATE_FALLBACK"

    if is_corrupted_summary(s):
        return "CORRUPTED_ADOC"

    return "REAL_LLM"


def record_telemetry(
    data_dir: Path | str | None,
    *,
    document_id: str | None = None,
    provider: str,
    model: str | None = None,
    call_type: str,
    input_chars: int = 0,
    input_bytes: int = 0,
    delivery_mode: str = "argv",
    exit_code: int | None = None,
    duration_ms: int = 0,
    status: str = "SUCCESS",
    google_block_reason: str = "NONE",
    error_message: str | None = None,
    output_snippet: str | None = None,
    summary_verdict: str = "N/A",
    timestamp: float | None = None,
) -> None:
    """격리된 telemetry.db에 텔레메트리 1건을 안전하게 기록 (Fire-and-Forget)."""
    ts = timestamp if timestamp is not None else time.time()
    db_path = get_telemetry_db_path(data_dir)
    try:
        conn = connect_telemetry(db_path)
        try:
            conn.execute(
                """INSERT INTO provider_telemetry (
                    timestamp, document_id, provider, model, call_type,
                    input_chars, input_bytes, delivery_mode, exit_code,
                    duration_ms, status, google_block_reason, error_message,
                    output_snippet, summary_verdict
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    ts,
                    document_id,
                    provider,
                    model or "",
                    call_type,
                    input_chars,
                    input_bytes,
                    delivery_mode,
                    exit_code,
                    duration_ms,
                    status,
                    google_block_reason,
                    (error_message[:1000] if error_message else None),
                    (output_snippet[:500] if output_snippet else None),
                    summary_verdict,
                ),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001
        logger.warning("Failed to record provider telemetry in %s (non-fatal): %s", db_path, e)


def query_telemetry(
    data_dir: Path | str | None,
    *,
    limit: int = 50,
    failed_only: bool = False,
    document_id: str | None = None,
    provider: str | None = None,
    since: float | str | None = None,
) -> list[dict[str, Any]]:
    """격리된 telemetry.db에서 최근 텔레메트리 레코드 조회."""
    db_path = get_telemetry_db_path(data_dir)
    if not db_path.is_file():
        return []

    conditions = []
    params: list[Any] = []

    if failed_only:
        conditions.append(
            "(status != 'SUCCESS' OR google_block_reason != 'NONE' OR summary_verdict IN ('RAW_SLICE_200', 'MOCK_PREFIX', 'EMPTY', 'TEMPLATE_FALLBACK'))"
        )
    if document_id:
        conditions.append("document_id = ?")
        params.append(document_id)
    if provider:
        conditions.append("provider = ?")
        params.append(provider)
    if since is not None:
        try:
            val = float(since)
        except (ValueError, TypeError):
            # ISO format
            try:
                from datetime import datetime
                val = datetime.fromisoformat(str(since)).timestamp()
            except Exception:
                val = time.time()
        conditions.append("timestamp >= ?")
        params.append(val)

    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    query = f"SELECT * FROM provider_telemetry {where_clause} ORDER BY id DESC LIMIT ?"
    params.append(max(1, limit))

    try:
        conn = connect_telemetry(db_path)
        try:
            cur = conn.execute(query, params)
            return [dict(r) for r in cur.fetchall()]
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001
        logger.warning("Failed to query telemetry from %s: %s", db_path, e)
        return []


def telemetry_summary_stats(
    data_dir: Path | str | None,
    *,
    since: float | str | None = None,
) -> dict[str, Any]:
    """텔레메트리 집계 통계 반환."""
    db_path = get_telemetry_db_path(data_dir)
    if not db_path.is_file():
        return {
            "total_calls": 0,
            "success_calls": 0,
            "failed_calls": 0,
            "success_rate": 0.0,
            "by_provider": {},
            "by_status": {},
            "by_block_reason": {},
            "by_verdict": {},
        }

    where = ""
    params: list[Any] = []
    if since is not None:
        try:
            val = float(since)
        except (ValueError, TypeError):
            try:
                from datetime import datetime
                val = datetime.fromisoformat(str(since)).timestamp()
            except Exception:
                val = time.time()
        where = "WHERE timestamp >= ?"
        params.append(val)

    try:
        conn = connect_telemetry(db_path)
        try:
            total = conn.execute(f"SELECT COUNT(*) as cnt FROM provider_telemetry {where}", params).fetchone()["cnt"]
            if total == 0:
                return {
                    "total_calls": 0,
                    "success_calls": 0,
                    "failed_calls": 0,
                    "success_rate": 0.0,
                    "by_provider": {},
                    "by_status": {},
                    "by_block_reason": {},
                    "by_verdict": {},
                }

            succ_where = f"{where} AND status='SUCCESS'" if where else "WHERE status='SUCCESS'"
            succ = conn.execute(f"SELECT COUNT(*) as cnt FROM provider_telemetry {succ_where}", params).fetchone()["cnt"]

            def _group_counts(col: str) -> dict[str, int]:
                q = f"SELECT {col}, COUNT(*) as cnt FROM provider_telemetry {where} GROUP BY {col}"
                rows = conn.execute(q, params).fetchall()
                return {str(r[col] or "unknown"): r["cnt"] for r in rows}

            return {
                "total_calls": total,
                "success_calls": succ,
                "failed_calls": total - succ,
                "success_rate": round(succ / total, 4) if total > 0 else 0.0,
                "by_provider": _group_counts("provider"),
                "by_status": _group_counts("status"),
                "by_block_reason": _group_counts("google_block_reason"),
                "by_verdict": _group_counts("summary_verdict"),
            }
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001
        logger.warning("Failed to get telemetry stats: %s", e)
        return {
            "total_calls": 0,
            "error": str(e),
        }


def prune_old_telemetry(data_dir: Path | str | None, retention_days: int = 14) -> int:
    """보존 기한(기본 14일)이 지난 구 텔레메트리 레코드 자동 삭제."""
    db_path = get_telemetry_db_path(data_dir)
    if not db_path.is_file():
        return 0

    cutoff = time.time() - (retention_days * 86400.0)
    try:
        conn = connect_telemetry(db_path)
        try:
            cur = conn.execute("DELETE FROM provider_telemetry WHERE timestamp < ?", (cutoff,))
            deleted = cur.rowcount
            conn.commit()
            return deleted
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001
        logger.warning("Failed to prune telemetry in %s: %s", db_path, e)
        return 0


def register_support_bundle(
    data_dir: Path | str | None,
    *,
    bundle_id: str,
    token: str,
    filename: str,
    filepath: str,
    days_covered: int,
    size_bytes: int,
    created_at: float,
    expires_at: float,
    target_doc_id: str | None = None,
) -> None:
    """생성된 Support Bundle 메타데이터를 telemetry.db에 등록."""
    db_path = get_telemetry_db_path(data_dir)
    conn = connect_telemetry(db_path)
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO support_bundles (
                bundle_id, token, filename, filepath, days_covered,
                target_doc_id, size_bytes, created_at, expires_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                bundle_id,
                token,
                filename,
                str(filepath),
                days_covered,
                target_doc_id,
                size_bytes,
                created_at,
                expires_at,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def lookup_support_bundle_by_token(
    data_dir: Path | str | None,
    token: str,
) -> dict[str, Any] | None:
    """다운로드 토큰으로 Support Bundle 레코드 조회."""
    db_path = get_telemetry_db_path(data_dir)
    if not db_path.is_file():
        return None
    conn = connect_telemetry(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM support_bundles WHERE token = ?",
            (token,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def delete_support_bundle(
    data_dir: Path | str | None,
    bundle_id: str,
) -> None:
    """Support Bundle 레코드 삭제."""
    db_path = get_telemetry_db_path(data_dir)
    if not db_path.is_file():
        return
    conn = connect_telemetry(db_path)
    try:
        conn.execute("DELETE FROM support_bundles WHERE bundle_id = ?", (bundle_id,))
        conn.commit()
    finally:
        conn.close()


def list_active_support_bundles(
    data_dir: Path | str | None,
    now_epoch: float | None = None,
) -> list[dict[str, Any]]:
    """만료되지 않은 활성 Support Bundle 목록 조회."""
    db_path = get_telemetry_db_path(data_dir)
    if not db_path.is_file():
        return []
    now = now_epoch if now_epoch is not None else time.time()
    conn = connect_telemetry(db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM support_bundles WHERE expires_at > ? ORDER BY created_at DESC",
            (now,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def clean_expired_support_bundles(
    data_dir: Path | str | None,
    now_epoch: float | None = None,
) -> list[str]:
    """만료된(기본 6시간 초과) Support Bundle 레코드 정리 및 대상 파일 경로 목록 반환."""
    db_path = get_telemetry_db_path(data_dir)
    if not db_path.is_file():
        return []
    now = now_epoch if now_epoch is not None else time.time()
    conn = connect_telemetry(db_path)
    try:
        rows = conn.execute(
            "SELECT filepath FROM support_bundles WHERE expires_at <= ?",
            (now,),
        ).fetchall()
        expired_paths = [r["filepath"] for r in rows]
        conn.execute("DELETE FROM support_bundles WHERE expires_at <= ?", (now,))
        conn.commit()
        return expired_paths
    finally:
        conn.close()

