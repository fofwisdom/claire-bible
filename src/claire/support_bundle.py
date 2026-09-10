"""Support Bundle 생성 및 관리 모듈.

근본 원인 분석(RCA) 및 트러블슈팅을 위해 지정한 날짜(기본 1일)만큼의
텔레메트리, 프로바이더 로그, 인제스트 파이프라인 진단 및 공유 링크 추적 정보를
zstd로 압축한 번들을 생성하고, 6시간 후 자동 파기한다.
"""

from __future__ import annotations

import io
import importlib.metadata
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

from .config import ROOT, Settings, diagnose_agy_environment, get_settings
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
_URL_SECRET_RE = re.compile(
    r"([?&](?:s|token|access_token|refresh_token|api_key|key|auth|code|state|"
    r"signature|sig|session|password|share)=)[^&#\s]+",
    re.IGNORECASE,
)
BUNDLE_FORMAT_VERSION = 2


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
    target_theme_id: int | None = None
    target_resolution_status: str | None = None

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
            "target_theme_id": self.target_theme_id,
            "target_resolution_status": self.target_resolution_status,
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
            if isinstance(k, str) and _SENSITIVE_KEY_RE.search(k) and not isinstance(v, bool):
                sanitized[k] = "***REDACTED***"
            else:
                sanitized[k] = sanitize_sensitive_data(v)
        return sanitized
    if isinstance(obj, list):
        return [sanitize_sensitive_data(item) for item in obj]
    if isinstance(obj, tuple):
        return tuple(sanitize_sensitive_data(item) for item in obj)
    if isinstance(obj, str):
        text = _BEARER_TOKEN_RE.sub("Bearer ***REDACTED***", obj)
        return _URL_SECRET_RE.sub(r"\1***REDACTED***", text)
    return obj


def _sanitize_share_index(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """공유 토큰 원문을 노출하지 않으면서 동일 토큰 여부는 해시로 대조 가능하게 한다."""
    safe_rows: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        raw_token = str(item.get("token") or "")
        if raw_token:
            import hashlib

            item["token_sha256"] = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
            item["token"] = "***REDACTED***"
        safe_rows.append(item)
    return safe_rows


def _get_git_commit() -> str | None:
    """빌드 주입값을 우선하고 개발 checkout에서는 Git을 보조 수단으로 사용한다."""
    build_commit = os.environ.get("CLAIRE_BUILD_COMMIT", "").strip()
    if build_commit and build_commit.lower() not in {"unknown", "null", "none"}:
        return build_commit
    try:
        res = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
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


def _get_build_identity() -> dict[str, Any]:
    commit = _get_git_commit()
    try:
        package_version = importlib.metadata.version("claire")
    except importlib.metadata.PackageNotFoundError:
        package_version = "unknown"
    return {
        "git_commit": commit,
        "revision_source": (
            "build_arg"
            if os.environ.get("CLAIRE_BUILD_COMMIT", "").strip().lower()
            not in {"", "unknown", "null", "none"}
            else ("git" if commit else "unknown")
        ),
        "package_version": package_version,
        "schema_version": dbm.SCHEMA_VERSION,
        "schema_lineage": dbm.SCHEMA_LINEAGE,
        "image_tag": os.environ.get("CLAIRE_IMAGE_TAG", "").strip() or None,
    }


def _theme_databases(settings: Settings) -> list[tuple[int, str, Path]]:
    from .store.theme import get_theme_manager

    tm = get_theme_manager(settings)
    themes = (
        tm.list_themes(include_private=True)
        if getattr(settings, "multi_theme", False)
        else [tm.get_theme(0)]
    )
    return [
        (theme.id, theme.label, Path(tm.get_settings_for_theme(theme.id).db_file))
        for theme in themes
    ]


def _find_exact_inbox_matches(settings: Settings, target: str) -> list[dict[str, Any]]:
    """문서 생성 전 실패한 URL도 찾을 수 있도록 raw_inbox를 정확 일치로 역추적한다."""
    from .ingest.normalize import canonicalize_url

    raw_target = target.strip()
    is_url = raw_target.startswith(("http://", "https://"))
    target_canonical = canonicalize_url(raw_target) if is_url else None
    matches: list[dict[str, Any]] = []
    for theme_id, theme_label, db_file in _theme_databases(settings):
        if not db_file.is_file():
            continue
        try:
            conn = dbm.connect_existing(db_file, readonly=True)
            try:
                rows = conn.execute(
                    "SELECT * FROM raw_inbox WHERE payload=? OR file_ref=? ORDER BY id",
                    (raw_target, raw_target),
                ).fetchall()
                if is_url:
                    candidates = conn.execute(
                        "SELECT * FROM raw_inbox WHERE kind='url' AND payload IS NOT NULL ORDER BY id"
                    ).fetchall()
                    seen = {int(row["id"]) for row in rows}
                    for row in candidates:
                        payload = str(row["payload"] or "").strip()
                        if (
                            int(row["id"]) not in seen
                            and payload.startswith(("http://", "https://"))
                            and canonicalize_url(payload) == target_canonical
                        ):
                            rows.append(row)
                for row in rows:
                    item = dict(row)
                    item["theme_id"] = theme_id
                    item["theme_label"] = theme_label
                    item["_db_file"] = str(db_file)
                    matches.append(item)
            finally:
                conn.close()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to search target inbox in %s: %s", db_file, exc)
    return matches


def _resolve_support_target(
    settings: Settings, target: str
) -> tuple[dict[str, Any], dict[str, Any] | None, list[dict[str, Any]]]:
    """진단용 strict resolver: 모호하거나 미적재인 대상을 임의 문서로 치환하지 않는다."""
    from .store.theme import get_theme_manager

    tm = get_theme_manager(settings)
    candidates = tm.resolve_document_targets(target=target)
    exact_candidates = [
        candidate
        for candidate in candidates
        if candidate.get("matched_by") != "pattern"
    ]
    inbox_matches = _find_exact_inbox_matches(settings, target)
    selected = exact_candidates[0] if len(exact_candidates) == 1 else None
    if selected is not None:
        status = "resolved_document"
    elif len(exact_candidates) > 1:
        status = "ambiguous"
    elif inbox_matches:
        status = (
            "failed_inbox"
            if any(item.get("status") in {"error", "failed", "stalled"} for item in inbox_matches)
            else "inbox_only"
        )
    elif candidates:
        status = "ambiguous_pattern"
    else:
        status = "not_observed"

    public_candidates = []
    for candidate in candidates:
        public_candidates.append(
            {key: value for key, value in candidate.items() if key != "db_file"}
        )
    resolution: dict[str, Any] = {
        "input_target": target,
        "requested_target": target,
        "resolution_status": status,
        "candidate_count": len(candidates),
        "candidate_documents": public_candidates,
        "matching_inbox_ids": [
            {"theme_id": item["theme_id"], "inbox_id": item["id"]}
            for item in inbox_matches
        ],
    }
    if selected is not None:
        resolution.update(
            {
                "matched_by": selected.get("matched_by", "unknown"),
                "theme_id": selected.get("theme_id", 0),
                "theme_label": selected.get("theme_label", "기본 지식베이스"),
                "document_id": selected["id"],
                "url": selected.get("url"),
                "canonical_url": selected.get("canonical_url"),
                "title": selected.get("title"),
                "content_hash": selected.get("content_hash"),
                "is_from_share_token": selected.get("is_from_share_token", False),
            }
        )
    return resolution, selected, inbox_matches


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
    request_context: dict[str, Any] | None = None,
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
    collector_warnings: list[dict[str, Any]] = []

    # 대상 특정 및 추적 (공유 링크 또는 doc_id)
    target_info: dict[str, Any] | None = None
    target_doc_id: str | None = None
    target_matched_by: str | None = None
    target_theme_id: int | None = None
    target_db_file: Path = Path(s.db_file)
    target_inbox_matches: list[dict[str, Any]] = []
    target_resolution_status: str | None = None

    if target:
        target_info, primary, target_inbox_matches = _resolve_support_target(s, target)
        target_resolution_status = target_info["resolution_status"]
        if primary is not None:
            target_doc_id = primary["id"]
            target_matched_by = primary.get("matched_by", "unknown")
            target_theme_id = primary.get("theme_id", 0)
            target_db_file = Path(primary.get("db_file", s.db_file))
        elif target_inbox_matches:
            target_theme_id = target_inbox_matches[0].get("theme_id", 0)
            target_db_file = Path(target_inbox_matches[0].get("_db_file", s.db_file))
            target_matched_by = "raw_inbox"

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

                from .store.theme import get_theme_manager

                tm = get_theme_manager(s)
                themes_to_check = (
                    tm.list_themes(include_private=True)
                    if getattr(s, "multi_theme", False)
                    else [tm.get_theme(0)]
                )

                for t in themes_to_check:
                    theme_db = Path(tm.get_settings_for_theme(t.id).db_file)
                    if not theme_db.is_file():
                        continue
                    try:
                        conn = dbm.connect_existing(theme_db, readonly=True)
                        try:
                            # 상태별 카운트 합산
                            rows = conn.execute(
                                "SELECT status, count(*) as cnt FROM raw_inbox WHERE received_at >= ? GROUP BY status",
                                (cutoff_epoch,),
                            ).fetchall()
                            for r in rows:
                                st = r["status"]
                                inbox_summary[st] = inbox_summary.get(st, 0) + r["cnt"]

                            # 실패/에러 항목 수집
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
                            for er in err_rows:
                                item = dict(er)
                                if getattr(s, "multi_theme", False):
                                    item["theme_id"] = t.id
                                    item["theme_label"] = t.label
                                failed_items.append(item)

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
                            for sr in share_rows:
                                s_dict = dict(sr)
                                if getattr(s, "multi_theme", False):
                                    s_dict["theme_id"] = t.id
                                    s_dict["theme_label"] = t.label
                                shares_index.append(s_dict)

                            # DB 정합성 빠른 검사
                            check_row = conn.execute("PRAGMA quick_check;").fetchone()
                            chk_res = check_row[0] if check_row else "unknown"
                            cnts = dbm.counts(conn)
                            if t.id == 0:
                                db_integrity["claire_db_quick_check"] = chk_res
                                db_integrity["counts"] = cnts
                            if getattr(s, "multi_theme", False):
                                if "themes" not in db_integrity:
                                    db_integrity["themes"] = {}
                                db_integrity["themes"][str(t.id)] = {
                                    "label": t.label,
                                    "quick_check": chk_res,
                                    "counts": cnts,
                                }
                        finally:
                            conn.close()
                    except Exception as e:  # noqa: BLE001
                        db_integrity[f"error_theme_{t.id}"] = str(e)

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
                    json.dumps(_sanitize_share_index(shares_index), ensure_ascii=False, indent=2).encode("utf-8"),
                )
                _add_tar_bytes(
                    tar,
                    f"{root_arcname}/pipeline/db_integrity.json",
                    json.dumps(db_integrity, ensure_ascii=False, indent=2).encode("utf-8"),
                )

                # 6. 로그 수집 (agy.log, telegram.log 및 data/logs/*.log)
                logs_dir = Path(data_dir) / "logs"
                collected_logs: set[str] = set()

                def _add_log_file(src_path: Path, arc_name: str, max_lines: int = 5000) -> None:
                    if src_path.is_file() and arc_name not in collected_logs:
                        try:
                            log_text = src_path.read_text(encoding="utf-8", errors="replace")
                            lines = log_text.splitlines()[-max_lines:]
                            sanitized_log = sanitize_sensitive_data("\n".join(lines))
                            _add_tar_bytes(
                                tar,
                                f"{root_arcname}/logs/{arc_name}",
                                sanitized_log.encode("utf-8"),
                            )
                            collected_logs.add(arc_name)
                        except Exception as e:  # noqa: BLE001
                            logger.warning("Failed to read log %s: %s", src_path, e)

                # agy.log
                _add_log_file(logs_dir / "agy.log", "agy.log")
                if "agy.log" not in collected_logs:
                    _add_log_file(Path(data_dir) / "agy.log", "agy.log")

                # telegram.log
                _add_log_file(logs_dir / "telegram.log", "telegram.log")
                if "telegram.log" not in collected_logs:
                    _add_log_file(Path(data_dir) / "telegram.log", "telegram.log")

                # Any other *.log in data/logs
                if logs_dir.is_dir():
                    for extra_log in logs_dir.glob("*.log"):
                        if extra_log.name not in collected_logs:
                            _add_log_file(extra_log, extra_log.name)

                # 7. 특정 요청/문서 추적 패키징. 문서화 전 실패 URL도 raw_inbox 기준으로 포함한다.
                if target_info is not None:
                    doc_row = None
                    doc_shares: list[Any] = []
                    extractions: list[Any] = []
                    latest_summary = None
                    doc_entities: list[Any] = []
                    doc_relations: list[Any] = []
                    doc_proposals: list[dict[str, Any]] = []
                    inbox_map: dict[tuple[str, int], dict[str, Any]] = {}

                    for matched in target_inbox_matches:
                        db_file = str(matched["_db_file"])
                        public = {key: value for key, value in matched.items() if key != "_db_file"}
                        inbox_map[(db_file, int(public["id"]))] = public

                    if target_doc_id:
                        conn = dbm.connect_existing(target_db_file, readonly=True)
                        try:
                            doc_row = conn.execute(
                                "SELECT * FROM documents WHERE id = ?", (target_doc_id,)
                            ).fetchone()
                            document_inbox_rows = conn.execute(
                                "SELECT * FROM raw_inbox WHERE document_id = ? ORDER BY id",
                                (target_doc_id,),
                            ).fetchall()
                            for row in document_inbox_rows:
                                item = dict(row)
                                item["theme_id"] = target_theme_id or 0
                                item["theme_label"] = target_info.get("theme_label")
                                inbox_map[(str(target_db_file), int(item["id"]))] = item
                            doc_shares = conn.execute(
                                "SELECT * FROM doc_shares WHERE document_id = ?", (target_doc_id,)
                            ).fetchall()
                            extractions = conn.execute(
                                "SELECT * FROM extractions WHERE document_id = ? ORDER BY id DESC",
                                (target_doc_id,),
                            ).fetchall()
                            latest_summary = dbm.latest_extraction_summary(conn, target_doc_id)
                            doc_entities = dbm.document_entities(conn, target_doc_id)
                            doc_relations = dbm.document_relations(conn, target_doc_id)
                            doc_proposals = dbm.document_proposals(conn, target_doc_id)
                        finally:
                            conn.close()

                    inbox_records = list(inbox_map.values())
                    inbox_records.sort(key=lambda item: (float(item.get("received_at") or 0), int(item["id"])))

                    doc_telemetry = (
                        query_telemetry(data_dir, document_id=target_doc_id, limit=200)
                        if target_doc_id
                        else []
                    )
                    target_info["latest_summary"] = latest_summary
                    if target_info.get("resolution_status") not in {"resolved_document", "inbox_only"}:
                        collector_warnings.append(
                            {
                                "code": "TARGET_" + str(target_info["resolution_status"]).upper(),
                                "message": "The requested target was not uniquely resolved to a stored document",
                            }
                        )

                    extractions_data = [dict(e) for e in extractions]
                    graph_fragment = {
                        "document_id": target_doc_id,
                        "entities_count": len(doc_entities),
                        "relations_count": len(doc_relations),
                        "proposals_count": len(doc_proposals),
                        "entities": [
                            {
                                "id": ent.id,
                                "type": ent.type,
                                "name": ent.name,
                                "aliases": ent.aliases or [],
                                "props": ent.props or {},
                                "observations": ent.observations or [],
                                "sources": ent.sources or [],
                                "provisional": ent.provisional,
                                "created_at": ent.created_at,
                                "updated_at": ent.updated_at,
                            }
                            for ent in doc_entities
                        ],
                        "relations": [
                            {
                                "id": rel.id,
                                "type": rel.type,
                                "source_id": rel.source_id,
                                "target_id": rel.target_id,
                                "confidence": rel.confidence,
                                "props": rel.props or {},
                                "sources": rel.sources or [],
                                "provisional": rel.provisional,
                                "created_at": rel.created_at,
                            }
                            for rel in doc_relations
                        ],
                        "proposals": doc_proposals,
                    }
                    tracked_doc_detail = {
                        "resolution": target_info,
                        "document": dict(doc_row) if doc_row else None,
                        "shares": [dict(s) for s in doc_shares],
                        "extractions": extractions_data,
                        "latest_summary": latest_summary,
                        "graph_stats": {
                            "entities_count": len(doc_entities),
                            "relations_count": len(doc_relations),
                            "proposals_count": len(doc_proposals),
                        },
                    }
                    _add_tar_bytes(
                        tar,
                        f"{root_arcname}/tracked_document/target_resolution.json",
                        json.dumps(sanitize_sensitive_data(target_info), ensure_ascii=False, indent=2).encode("utf-8"),
                    )
                    _add_tar_bytes(
                        tar,
                        f"{root_arcname}/tracked_document/document_detail.json",
                        json.dumps(sanitize_sensitive_data(tracked_doc_detail), ensure_ascii=False, indent=2).encode("utf-8"),
                    )
                    _add_tar_bytes(
                        tar,
                        f"{root_arcname}/tracked_document/extractions.json",
                        json.dumps(sanitize_sensitive_data(extractions_data), ensure_ascii=False, indent=2).encode("utf-8"),
                    )
                    _add_tar_bytes(
                        tar,
                        f"{root_arcname}/tracked_document/graph_fragment.json",
                        json.dumps(sanitize_sensitive_data(graph_fragment), ensure_ascii=False, indent=2).encode("utf-8"),
                    )
                    _add_tar_bytes(
                        tar,
                        f"{root_arcname}/tracked_document/inbox_record.json",
                        json.dumps(sanitize_sensitive_data(inbox_records[-1]) if inbox_records else {}, ensure_ascii=False, indent=2).encode("utf-8"),
                    )
                    _add_tar_bytes(
                        tar,
                        f"{root_arcname}/tracked_document/inbox_records.jsonl",
                        ("\n".join(json.dumps(sanitize_sensitive_data(row), ensure_ascii=False) for row in inbox_records)
                         + ("\n" if inbox_records else "")).encode("utf-8"),
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
                build_identity = _get_build_identity()
                if build_identity["git_commit"] is None:
                    collector_warnings.append(
                        {
                            "code": "BUILD_REVISION_UNKNOWN",
                            "message": "CLAIRE_BUILD_COMMIT was not embedded and Git metadata was unavailable",
                        }
                    )
                _add_tar_bytes(
                    tar,
                    f"{root_arcname}/diagnostics/collector_warnings.json",
                    json.dumps(collector_warnings, ensure_ascii=False, indent=2).encode("utf-8"),
                )
                _add_tar_bytes(
                    tar,
                    f"{root_arcname}/diagnostics/build.json",
                    json.dumps(build_identity, ensure_ascii=False, indent=2).encode("utf-8"),
                )
                manifest = {
                    "bundle_format_version": BUNDLE_FORMAT_VERSION,
                    "bundle_id": bundle_id,
                    "filename": filename,
                    "created_at": datetime.fromtimestamp(now, timezone.utc).isoformat(),
                    "expires_at": datetime.fromtimestamp(expires_at, timezone.utc).isoformat(),
                    "ttl_hours": SUPPORT_BUNDLE_TTL_SECONDS / 3600.0,
                    "days_covered": validated_days,
                    "cutoff_timestamp": cutoff_iso,
                    "git_commit": build_identity["git_commit"],
                    "build": build_identity,
                    "target": sanitize_sensitive_data(target_info) if target_info else None,
                    "request_context": sanitize_sensitive_data(request_context or {}),
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
        target_theme_id=target_theme_id,
        target_resolution_status=target_resolution_status,
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
