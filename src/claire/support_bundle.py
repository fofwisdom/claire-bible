"""Support Bundle 생성 및 관리 모듈.

근본 원인 분석(RCA) 및 트러블슈팅을 위해 지정한 날짜(기본 1일)만큼의
텔레메트리, 프로바이더 로그, 인제스트 파이프라인 진단 및 공유 링크 추적 정보를
zstd로 압축한 번들을 생성하고, 6시간 후 자동 파기한다.
"""

from __future__ import annotations

import io
import json
import logging
import os
import platform
import re
import secrets
import shutil
import sqlite3
import subprocess
import tarfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import zstandard as zstd

from .config import Settings, diagnose_agy_environment, get_settings
from .store import db as dbm
from .store.telemetry import (
    DEFAULT_TELEMETRY_RETENTION_DAYS,
    clean_expired_support_bundles,
    connect_telemetry,
    delete_support_bundle,
    get_telemetry_db_path,
    list_active_support_bundles,
    lookup_support_bundle_by_token,
    query_telemetry,
    register_support_bundle,
    telemetry_summary_stats,
)

logger = logging.getLogger(__name__)

SUPPORT_BUNDLE_TTL_SECONDS = 6 * 3600  # 6시간
DEFAULT_SUPPORT_BUNDLE_DAYS = 1

_SENSITIVE_KEY_RE = re.compile(
    r"(token|secret|password|key|cookie|auth|credential|cert)", re.IGNORECASE
)
_BEARER_TOKEN_RE = re.compile(r"Bearer\s+[A-Za-z0-9_\-\.]+", re.IGNORECASE)


@dataclass(frozen=True)
class SupportBundleInfo:
    bundle_id: str
    token: str
    filename: str
    filepath: Path
    days_covered: int
    size_bytes: int
    created_at: float
    expires_at: float
    download_url: str
    target_doc_id: str | None = None
    target_matched_by: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "bundle_id": self.bundle_id,
            "token": self.token,
            "filename": self.filename,
            "filepath": str(self.filepath),
            "days_covered": self.days_covered,
            "size_bytes": self.size_bytes,
            "created_at": datetime.fromtimestamp(self.created_at, timezone.utc).isoformat(),
            "expires_at": datetime.fromtimestamp(self.expires_at, timezone.utc).isoformat(),
            "download_url": self.download_url,
            "target_doc_id": self.target_doc_id,
            "target_matched_by": self.target_matched_by,
        }


def get_support_bundles_dir(data_dir: Path | str | None) -> Path:
    """번들 저장소 디렉터리 경로 반환."""
    root = Path(data_dir) if data_dir else Path("data")
    bundles_dir = root / "support_bundles"
    bundles_dir.mkdir(parents=True, exist_ok=True)
    return bundles_dir


def validate_bundle_days(days: int, max_retention_days: int | None = None) -> int:
    """번들 기간 검증: 1일 이상이며 텔레메트리 보관 기한을 초과할 수 없음."""
    limit = (
        max_retention_days
        if max_retention_days is not None
        else DEFAULT_TELEMETRY_RETENTION_DAYS
    )
    if days < 1:
        raise ValueError(f"days must be at least 1 (got {days})")
    if days > limit:
        raise ValueError(
            f"days ({days}) cannot exceed telemetry retention limit of {limit} days"
        )
    return days


def sanitize_sensitive_data(obj: Any) -> Any:
    """비밀번호, 토큰, API 키 등 민감 정보를 ***REDACTED*** 처리."""
    if isinstance(obj, dict):
        sanitized = {}
        for k, v in obj.items():
            if isinstance(k, str) and _SENSITIVE_KEY_RE.search(k):
                sanitized[k] = "***REDACTED***"
            else:
                sanitized[k] = sanitize_sensitive_data(v)
        return sanitized
    if isinstance(obj, list):
        return [sanitize_sensitive_data(item) for item in obj]
    if isinstance(obj, tuple):
        return tuple(sanitize_sensitive_data(item) for item in obj)
    if isinstance(obj, str):
        return _BEARER_TOKEN_RE.sub("Bearer ***REDACTED***", obj)
    return obj


def _get_git_commit() -> str | None:
    """현재 레포지토리의 Git commit 해시 조회."""
    try:
        res = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=2.0,
            check=False,
        )
        if res.returncode == 0:
            return res.stdout.strip()
    except Exception:  # noqa: BLE001
        pass
    return None


def format_download_url(settings: Settings, token: str) -> str:
    """Support Bundle 다운로드 링크 구성."""
    pub = str(getattr(settings, "public_url", "") or "").rstrip("/")
    if pub:
        return f"{pub}/support/bundle?token={token}"
    host = getattr(settings, "inject_host", "127.0.0.1")
    port = getattr(settings, "inject_port", 8000)
    return f"http://{host}:{port}/support/bundle?token={token}"


def purge_expired_bundles(
    data_dir: Path | str | None,
    now_epoch: float | None = None,
) -> int:
    """6시간이 지난 Support Bundle 파일 및 DB 레코드 자동 파기."""
    now = now_epoch if now_epoch is not None else time.time()
    purged_count = 0

    # 1. DB 레코드에서 만료된 항목 삭제 및 파일 언링크
    expired_paths = clean_expired_support_bundles(data_dir, now_epoch=now)
    for path_str in expired_paths:
        try:
            p = Path(path_str)
            if p.is_file():
                p.unlink(missing_ok=True)
                purged_count += 1
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to unlink expired bundle %s: %s", path_str, e)

    # 2. 파일시스템 기준 6시간 초과 고아 파일 정리
    bundles_dir = get_support_bundles_dir(data_dir)
    if bundles_dir.is_dir():
        cutoff = now - SUPPORT_BUNDLE_TTL_SECONDS
        for item in bundles_dir.glob("support_bundle_*.tar.zst"):
            try:
                st = item.stat()
                if st.st_mtime <= cutoff:
                    item.unlink(missing_ok=True)
                    purged_count += 1
            except Exception as e:  # noqa: BLE001
                logger.warning("Failed to clean bundle file %s: %s", item, e)

    return purged_count


def _add_tar_bytes(tar: tarfile.TarFile, arcname: str, data: bytes) -> None:
    """메모리 버퍼 데이터를 tar archive 항목으로 추가."""
    ti = tarfile.TarInfo(name=arcname)
    ti.size = len(data)
    ti.mtime = int(time.time())
    ti.mode = 0o644
    tar.addfile(ti, io.BytesIO(data))


def create_support_bundle(
    settings: Settings | None = None,
    *,
    days: int = DEFAULT_SUPPORT_BUNDLE_DAYS,
    target: str | None = None,
    now_epoch: float | None = None,
) -> SupportBundleInfo:
    """지정된 기간(기본 1일) 및 특정 대상(공유 링크/문서 ID)에 대한 Support Bundle 생성.

    생성된 번들은 zstd로 압축되며, 6시간 후 자동 파기된다.
    """
    s = settings or get_settings()
    data_dir = s.data_dir
    max_retention = getattr(s, "telemetry_retention_days", DEFAULT_TELEMETRY_RETENTION_DAYS)
    validated_days = validate_bundle_days(days, max_retention)

    now = now_epoch if now_epoch is not None else time.time()
    cutoff_epoch = now - (validated_days * 86400.0)
    cutoff_iso = datetime.fromtimestamp(cutoff_epoch, timezone.utc).isoformat()

    # 사전 정리: 만료된 번들 파기
    purge_expired_bundles(data_dir, now_epoch=now)

    bundle_id = f"sb_{datetime.fromtimestamp(now, timezone.utc).strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(4)}"
    token = secrets.token_urlsafe(32)
    filename = f"support_bundle_{datetime.fromtimestamp(now, timezone.utc).strftime('%Y%m%d_%H%M%S')}_{bundle_id[-8:]}.tar.zst"
    bundles_dir = get_support_bundles_dir(data_dir)
    archive_path = bundles_dir / filename

    expires_at = now + SUPPORT_BUNDLE_TTL_SECONDS
    root_arcname = f"support_bundle_{bundle_id[-8:]}"

    # 대상 특정 및 추적 (공유 링크 또는 doc_id)
    target_info: dict[str, Any] | None = None
    target_doc_id: str | None = None
    target_matched_by: str | None = None

    if target:
        conn = dbm.connect_existing(s.db_file, readonly=True)
        try:
            matches = dbm.resolve_document_targets(conn, target=target)
            if not matches:
                raise ValueError(f"Target document not found for {target!r}")
            primary = matches[0]
            target_doc_id = primary["id"]
            target_matched_by = primary.get("matched_by", "unknown")
            target_info = {
                "input_target": target,
                "matched_by": target_matched_by,
                "document_id": target_doc_id,
                "url": primary.get("url"),
                "canonical_url": primary.get("canonical_url"),
                "title": primary.get("title"),
                "content_hash": primary.get("content_hash"),
                "is_from_share_token": primary.get("is_from_share_token", False),
            }
        finally:
            conn.close()

    # Zstandard 압축 스트림으로 tarfile 생성
    cctx = zstd.ZstdCompressor(level=3)
    with open(archive_path, "wb") as f_out:
        with cctx.stream_writer(f_out) as zstd_writer:
            with tarfile.open(fileobj=zstd_writer, mode="w|") as tar:
                # 1. 시스템 진단 정보
                sys_diag = {
                    "platform": platform.platform(),
                    "python_version": platform.python_version(),
                    "system": platform.system(),
                    "machine": platform.machine(),
                    "cpu_count": os.cpu_count(),
                    "sqlite_version": sqlite3.sqlite_version,
                    "zstd_version": zstd.ZSTD_VERSION,
                    "disk_usage": {},
                    "provider_env": diagnose_agy_environment(),
                }
                try:
                    usage = shutil.disk_usage(data_dir)
                    sys_diag["disk_usage"] = {
                        "total_bytes": usage.total,
                        "used_bytes": usage.used,
                        "free_bytes": usage.free,
                    }
                except Exception:  # noqa: BLE001
                    pass
                _add_tar_bytes(
                    tar,
                    f"{root_arcname}/diagnostics/system.json",
                    json.dumps(sys_diag, ensure_ascii=False, indent=2).encode("utf-8"),
                )

                # 2. 마스킹된 애플리케이션 설정
                try:
                    config_dict = sanitize_sensitive_data(s.model_dump())
                except Exception:
                    config_dict = {"environment": getattr(s, "environment", "unknown")}
                _add_tar_bytes(
                    tar,
                    f"{root_arcname}/diagnostics/config_sanitized.json",
                    json.dumps(config_dict, ensure_ascii=False, indent=2).encode("utf-8"),
                )

                # 3. 텔레메트리 레코드 (기간 내)
                records = query_telemetry(data_dir, since=cutoff_iso, limit=5000)
                records_lines = [
                    json.dumps(sanitize_sensitive_data(r), ensure_ascii=False) for r in records
                ]
                _add_tar_bytes(
                    tar,
                    f"{root_arcname}/telemetry/telemetry_records.jsonl",
                    ("\n".join(records_lines) + ("\n" if records_lines else "")).encode("utf-8"),
                )

                # 4. 텔레메트리 통계 요약
                stats = telemetry_summary_stats(data_dir, since=cutoff_iso)
                _add_tar_bytes(
                    tar,
                    f"{root_arcname}/telemetry/telemetry_stats.json",
                    json.dumps(stats, ensure_ascii=False, indent=2).encode("utf-8"),
                )

                # 5. 파이프라인 인박스 요약 및 에러/모의 항목, 공유 링크 인덱스
                inbox_summary: dict[str, int] = {}
                failed_items: list[dict[str, Any]] = []
                shares_index: list[dict[str, Any]] = []
                db_integrity: dict[str, Any] = {}

                if Path(s.db_file).is_file():
                    conn = dbm.connect_existing(s.db_file, readonly=True)
                    try:
                        # 상태별 카운트
                        rows = conn.execute(
                            "SELECT status, count(*) as cnt FROM raw_inbox WHERE received_at >= ? GROUP BY status",
                            (cutoff_epoch,),
                        ).fetchall()
                        inbox_summary = {r["status"]: r["cnt"] for r in rows}

                        # 실패/에러 항목
                        err_rows = conn.execute(
                            """
                            SELECT id, document_id, payload, status, attempts, error, received_at, last_attempt
                            FROM raw_inbox
                            WHERE status IN ('error', 'failed', 'stalled')
                              AND (received_at >= ? OR last_attempt >= ?)
                            ORDER BY received_at DESC LIMIT 100
                            """,
                            (cutoff_epoch, cutoff_epoch),
                        ).fetchall()
                        failed_items = [dict(r) for r in err_rows]

                        # 공유 링크 인덱스 (기간 내 생성되었거나 유효한 공유 링크 전체)
                        share_rows = conn.execute(
                            """
                            SELECT token, document_id, created_at, expires_at
                            FROM doc_shares
                            WHERE created_at >= ? OR expires_at IS NULL OR expires_at >= ?
                            ORDER BY created_at DESC
                            """,
                            (cutoff_epoch, now),
                        ).fetchall()
                        shares_index = [dict(r) for r in share_rows]

                        # DB 정합성 빠른 검사
                        check_row = conn.execute("PRAGMA quick_check;").fetchone()
                        db_integrity["claire_db_quick_check"] = check_row[0] if check_row else "unknown"
                        db_integrity["counts"] = dbm.counts(conn)
                    except Exception as e:  # noqa: BLE001
                        db_integrity["error"] = str(e)
                    finally:
                        conn.close()

                # telemetry.db 정합성 검사
                tel_path = get_telemetry_db_path(data_dir)
                if tel_path.is_file():
                    try:
                        tconn = connect_telemetry(tel_path)
                        try:
                            tcheck = tconn.execute("PRAGMA quick_check;").fetchone()
                            db_integrity["telemetry_db_quick_check"] = tcheck[0] if tcheck else "unknown"
                        finally:
                            tconn.close()
                    except Exception as e:  # noqa: BLE001
                        db_integrity["telemetry_db_check_error"] = str(e)

                _add_tar_bytes(
                    tar,
                    f"{root_arcname}/pipeline/inbox_summary.json",
                    json.dumps(inbox_summary, ensure_ascii=False, indent=2).encode("utf-8"),
                )
                _add_tar_bytes(
                    tar,
                    f"{root_arcname}/pipeline/failed_items.json",
                    json.dumps(sanitize_sensitive_data(failed_items), ensure_ascii=False, indent=2).encode("utf-8"),
                )
                _add_tar_bytes(
                    tar,
                    f"{root_arcname}/pipeline/shares_index.json",
                    json.dumps(shares_index, ensure_ascii=False, indent=2).encode("utf-8"),
                )
                _add_tar_bytes(
                    tar,
                    f"{root_arcname}/pipeline/db_integrity.json",
                    json.dumps(db_integrity, ensure_ascii=False, indent=2).encode("utf-8"),
                )

                # 6. 프로바이더 로그 (agy.log)
                log_file = Path(data_dir) / "logs" / "agy.log"
                if log_file.is_file():
                    try:
                        log_text = log_file.read_text(encoding="utf-8", errors="replace")
                        lines = log_text.splitlines()[-5000:]
                        sanitized_log = sanitize_sensitive_data("\n".join(lines))
                        _add_tar_bytes(
                            tar,
                            f"{root_arcname}/logs/agy.log",
                            sanitized_log.encode("utf-8"),
                        )
                    except Exception as e:  # noqa: BLE001
                        logger.warning("Failed to read agy.log: %s", e)

                # 7. 특정 대상(공유 링크/문서 ID) 추적 패키징
                if target_doc_id:
                    conn = dbm.connect_existing(s.db_file, readonly=True)
                    try:
                        doc_row = conn.execute(
                            "SELECT * FROM documents WHERE id = ?", (target_doc_id,)
                        ).fetchone()
                        inbox_row = conn.execute(
                            "SELECT * FROM raw_inbox WHERE document_id = ?", (target_doc_id,)
                        ).fetchone()
                        doc_shares = conn.execute(
                            "SELECT * FROM doc_shares WHERE document_id = ?", (target_doc_id,)
                        ).fetchall()
                    finally:
                        conn.close()

                    # 해당 문서에 특화된 텔레메트리 내역
                    doc_telemetry = query_telemetry(data_dir, document_id=target_doc_id, limit=200)

                    tracked_doc_detail = {
                        "resolution": target_info,
                        "document": dict(doc_row) if doc_row else None,
                        "shares": [dict(s) for s in doc_shares],
                    }
                    _add_tar_bytes(
                        tar,
                        f"{root_arcname}/tracked_document/target_resolution.json",
                        json.dumps(target_info, ensure_ascii=False, indent=2).encode("utf-8"),
                    )
                    _add_tar_bytes(
                        tar,
                        f"{root_arcname}/tracked_document/document_detail.json",
                        json.dumps(sanitize_sensitive_data(tracked_doc_detail), ensure_ascii=False, indent=2).encode("utf-8"),
                    )
                    _add_tar_bytes(
                        tar,
                        f"{root_arcname}/tracked_document/inbox_record.json",
                        json.dumps(sanitize_sensitive_data(dict(inbox_row)) if inbox_row else {}, ensure_ascii=False, indent=2).encode("utf-8"),
                    )
                    doc_tel_lines = [
                        json.dumps(sanitize_sensitive_data(r), ensure_ascii=False) for r in doc_telemetry
                    ]
                    _add_tar_bytes(
                        tar,
                        f"{root_arcname}/tracked_document/telemetry_history.jsonl",
                        ("\n".join(doc_tel_lines) + ("\n" if doc_tel_lines else "")).encode("utf-8"),
                    )

                # 8. Manifest 파일
                manifest = {
                    "bundle_id": bundle_id,
                    "token": token,
                    "filename": filename,
                    "created_at": datetime.fromtimestamp(now, timezone.utc).isoformat(),
                    "expires_at": datetime.fromtimestamp(expires_at, timezone.utc).isoformat(),
                    "ttl_hours": SUPPORT_BUNDLE_TTL_SECONDS / 3600.0,
                    "days_covered": validated_days,
                    "cutoff_timestamp": cutoff_iso,
                    "git_commit": _get_git_commit(),
                    "target": target_info,
                }
                _add_tar_bytes(
                    tar,
                    f"{root_arcname}/manifest.json",
                    json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"),
                )

    size_bytes = archive_path.stat().st_size
    download_url = format_download_url(s, token)

    # DB에 번들 메타데이터 등록
    register_support_bundle(
        data_dir,
        bundle_id=bundle_id,
        token=token,
        filename=filename,
        filepath=str(archive_path),
        days_covered=validated_days,
        size_bytes=size_bytes,
        created_at=now,
        expires_at=expires_at,
        target_doc_id=target_doc_id,
    )

    return SupportBundleInfo(
        bundle_id=bundle_id,
        token=token,
        filename=filename,
        filepath=archive_path,
        days_covered=validated_days,
        size_bytes=size_bytes,
        created_at=now,
        expires_at=expires_at,
        download_url=download_url,
        target_doc_id=target_doc_id,
        target_matched_by=target_matched_by,
    )


def get_support_bundle(
    data_dir: Path | str | None,
    token: str,
    now_epoch: float | None = None,
) -> dict[str, Any] | None:
    """토큰으로 활성 번들 조회. 6시간 만료 시 파일 파기 후 None 반환."""
    now = now_epoch if now_epoch is not None else time.time()
    record = lookup_support_bundle_by_token(data_dir, token)
    if not record:
        return None

    if record["expires_at"] <= now:
        # 이미 6시간 경과 만료됨 -> 파기
        try:
            Path(record["filepath"]).unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass
        delete_support_bundle(data_dir, record["bundle_id"])
        return None

    filepath = Path(record["filepath"])
    if not filepath.is_file():
        delete_support_bundle(data_dir, record["bundle_id"])
        return None

    return record
