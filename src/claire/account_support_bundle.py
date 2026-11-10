"""계정 범위 Support Bundle (유저 단위 기능).

관리자의 인스턴스 전체 번들(``support_bundle.create_support_bundle``)과 달리 호출 계정의 데이터만 담는다.

- 담는 것: 계정 상태 요약(비밀 없음), 소유 테마 목록과 집계, 소유 테마의 적재 대기열 요약·실패 항목, 소유 테마 문서의 프로바이더 텔레메트리,
  선택한 대상 문서의 추적 정보, 빌드 정보.
- 담지 않는 것: 호스트·마운트·저장소 진단, 설정 값, 로그 파일, OAuth·MCP 기록, 다른 계정의 테마·문서, 문서 본문(``raw_text``·``detail``),
  비밀(토큰·키 원문).
- 등록부와 다운로드 경로는 인스턴스 번들과 같다(``support_bundles``와 sidecar). ``account_id`` 열로 소유 계정을 구분한다.
"""

from __future__ import annotations

import io
import json
import secrets
import sqlite3
import tarfile
import time
from datetime import datetime, timezone
from typing import Any

import zstandard as zstd

from . import ids
from .config import Settings, get_settings
from .store import db as dbm
from .store.telemetry import (
    DEFAULT_TELEMETRY_RETENTION_DAYS,
    delete_support_bundle,
    get_telemetry_db_path,
    list_active_support_bundles,
    register_support_bundle,
)
from .support_bundle import (
    BUNDLE_FORMAT_VERSION,
    SUPPORT_BUNDLE_TTL_SECONDS,
    SupportBundleInfo,
    _BUNDLE_TOKEN_PREFIX,
    _delete_bundle_sidecar,
    _get_build_identity,
    _validated_archive_path,
    _write_bundle_sidecar,
    format_download_url,
    get_support_bundle,
    get_support_bundles_dir,
    purge_expired_bundles,
    sanitize_sensitive_data,
    validate_bundle_days,
)

ACCOUNT_BUNDLE_SCOPE = "account"
MAX_FAILED_ITEMS = 200
MAX_TELEMETRY_RECORDS = 2_000
MAX_ACTIVE_ACCOUNT_BUNDLES = 5


class AccountSupportBundleError(RuntimeError):
    """계정 번들을 만들 수 없다. 메시지는 사용자에게 보여 줘도 된다."""


def _add_json(tar: tarfile.TarFile, arcname: str, value: Any) -> None:
    data = json.dumps(value, ensure_ascii=False, indent=2, default=str).encode("utf-8")
    info = tarfile.TarInfo(name=arcname)
    info.size = len(data)
    info.mtime = int(time.time())
    info.mode = 0o644
    tar.addfile(info, io.BytesIO(data))


def _iso(ts: float | None) -> str | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(float(ts), timezone.utc).isoformat()


def _owned_themes(settings: Settings, account_id: int) -> list[Any]:
    from .store.theme import get_theme_manager

    return list(get_theme_manager(settings).list_account_themes(account_id))


def _theme_ref(theme: Any) -> str:
    number = getattr(theme, "number", None)
    return ids.format_public_id(number) if number is not None else str(theme.id)


def _account_summary(settings: Settings, account_id: int) -> dict[str, Any]:
    from .store import accounts as accounts_store
    from .store import auth_db, provider_keys
    from .store import telegram_bots

    summary: dict[str, Any] = {"account_id_public": None}
    conn = dbm.connect_existing(settings.db_file, readonly=True)
    try:
        store = accounts_store.AccountStore(conn)
        account = store.get(account_id)
        summary.update(
            {
                "account_id_public": str(account.public_id),
                "role": account.role,
                "status": account.status,
                "instance_mode": store.instance_mode(),
                "profile_public": bool(account.profile_public),
            }
        )
    finally:
        conn.close()
    auth_path = auth_db.auth_db_path(settings.data_dir)
    if auth_path.is_file():
        auth = auth_db.connect_auth_db(auth_path)
        try:
            summary["provider_keys"] = [
                {
                    "provider": m.provider,
                    "fingerprint": m.fingerprint,
                    "status": m.status,
                    "created_at": _iso(m.created_at),
                    "last_verified_at": _iso(m.last_verified_at),
                    "last_used_at": _iso(m.last_used_at),
                }
                for m in provider_keys.get_key_meta(auth, account_id=account_id)
            ]
            bot = telegram_bots.get_meta(auth, account_id=account_id)
            summary["telegram_bot"] = (
                None
                if bot is None
                else {
                    "bot_username": bot.bot_username,
                    "fingerprint": bot.fingerprint,
                    "enabled": bot.enabled,
                    "status": bot.status,
                    "last_error": bot.last_error,
                    "status_at": _iso(bot.status_at),
                    "allowed_user_count": len(bot.allowed_user_ids),
                }
            )
        finally:
            auth.close()
    return summary


def _theme_inventory(settings: Settings, themes: list[Any]) -> list[dict[str, Any]]:
    from .store.theme import get_theme_manager

    manager = get_theme_manager(settings)
    out = []
    for theme in themes:
        try:
            counts = manager.theme_data_counts(theme.id)
        except Exception as exc:  # noqa: BLE001
            counts = {"error": type(exc).__name__}
        out.append(
            {
                "theme": _theme_ref(theme),
                "label": theme.label,
                "is_default": bool(theme.is_default),
                "is_public": bool(theme.is_public),
                "counts": counts,
            }
        )
    return out


def _placeholders(values: list[int]) -> str:
    return ",".join("?" for _ in values)


def _pipeline(conn: sqlite3.Connection, theme_ids: list[int], cutoff: float) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if not theme_ids:
        return {}, []
    marks = _placeholders(theme_ids)
    summary: dict[str, Any] = {}
    for row in conn.execute(
        f"SELECT theme_id, status, COUNT(*) FROM raw_inbox WHERE theme_id IN ({marks}) GROUP BY theme_id, status",
        theme_ids,
    ):
        summary.setdefault(str(row[0]), {})[str(row[1])] = int(row[2])
    failed = []
    for row in conn.execute(
        f"SELECT id, theme_id, received_at, source, kind, payload, file_name, document_id, status, error, attempts, last_attempt "
        f"FROM raw_inbox WHERE theme_id IN ({marks}) AND status IN ('error', 'failed') AND COALESCE(last_attempt, received_at, 0) >= ? "
        f"ORDER BY COALESCE(last_attempt, received_at) DESC LIMIT ?",
        (*theme_ids, cutoff, MAX_FAILED_ITEMS),
    ):
        failed.append(
            sanitize_sensitive_data(
                {
                    "inbox_id": row[0],
                    "theme_id": row[1],
                    "received_at": _iso(row[2]),
                    "source": row[3],
                    "kind": row[4],
                    "payload": (row[5] or "")[:500],
                    "file_name": row[6],
                    "document_id": row[7],
                    "status": row[8],
                    "error": (row[9] or "")[:1000],
                    "attempts": row[10],
                    "last_attempt": _iso(row[11]),
                }
            )
        )
    return summary, failed


def _owned_document_ids(conn: sqlite3.Connection, theme_ids: list[int]) -> set[str]:
    if not theme_ids:
        return set()
    rows = conn.execute(f"SELECT id FROM documents WHERE theme_id IN ({_placeholders(theme_ids)})", theme_ids)
    return {str(r[0]) for r in rows}


def _telemetry(settings: Settings, doc_ids: set[str], cutoff: float) -> list[dict[str, Any]]:
    path = get_telemetry_db_path(settings.data_dir)
    if not path.is_file() or not doc_ids:
        return []
    conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=2.0)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT * FROM provider_telemetry WHERE timestamp >= ? AND document_id IS NOT NULL ORDER BY timestamp DESC",
            (cutoff,),
        )
        out = []
        for row in rows:
            if str(row["document_id"]) not in doc_ids:
                continue
            record = {k: row[k] for k in row.keys() if k != "output_snippet"}
            out.append(sanitize_sensitive_data(record))
            if len(out) >= MAX_TELEMETRY_RECORDS:
                break
        return out
    finally:
        conn.close()


def _tracked_document(conn: sqlite3.Connection, theme_ids: list[int], target: str) -> dict[str, Any]:
    """대상 문서 추적. 소유 테마 안의 문서 ID 또는 공유 토큰만 찾는다. 못 찾으면 ``not_found``."""
    if not theme_ids:
        return {"input_target": target, "resolution_status": "not_found"}
    marks = _placeholders(theme_ids)
    raw = target.strip()
    candidate = raw.rstrip("/").rsplit("/", 1)[-1].split("?", 1)[0]
    doc_row = conn.execute(
        f"SELECT id, theme_id, url, canonical_url, title, source_type, fetched_at, partial, lang, hidden "
        f"FROM documents WHERE theme_id IN ({marks}) AND (id = ? OR url = ? OR canonical_url = ?) LIMIT 1",
        (*theme_ids, candidate, raw, raw),
    ).fetchone()
    matched_by = "document_id_or_url"
    if doc_row is None:
        share = conn.execute(
            f"SELECT document_id FROM doc_shares WHERE theme_id IN ({marks}) AND token = ? LIMIT 1",
            (*theme_ids, candidate),
        ).fetchone()
        if share is not None:
            doc_row = conn.execute(
                f"SELECT id, theme_id, url, canonical_url, title, source_type, fetched_at, partial, lang, hidden "
                f"FROM documents WHERE theme_id IN ({marks}) AND id = ? LIMIT 1",
                (*theme_ids, share[0]),
            ).fetchone()
            matched_by = "share_token"
    if doc_row is None:
        return {"input_target": target, "resolution_status": "not_found"}
    doc_id = str(doc_row[0])
    inbox = [
        sanitize_sensitive_data(
            {"inbox_id": r[0], "status": r[1], "error": (r[2] or "")[:1000], "attempts": r[3], "received_at": _iso(r[4])}
        )
        for r in conn.execute(
            f"SELECT id, status, error, attempts, received_at FROM raw_inbox WHERE theme_id IN ({marks}) AND document_id = ? "
            f"ORDER BY received_at DESC LIMIT 20",
            (*theme_ids, doc_id),
        )
    ]
    return {
        "input_target": target,
        "resolution_status": "resolved",
        "matched_by": matched_by,
        "document": sanitize_sensitive_data(
            {
                "id": doc_id,
                "theme_id": doc_row[1],
                "url": doc_row[2],
                "canonical_url": doc_row[3],
                "title": doc_row[4],
                "source_type": doc_row[5],
                "fetched_at": _iso(doc_row[6]),
                "partial": bool(doc_row[7]),
                "lang": doc_row[8],
                "hidden": bool(doc_row[9]),
            }
        ),
        "inbox_records": inbox,
    }


def create_account_support_bundle(
    settings: Settings | None = None,
    *,
    account_id: int,
    days: int = 1,
    target: str | None = None,
    request_context: dict[str, Any] | None = None,
    now_epoch: float | None = None,
) -> SupportBundleInfo:
    """계정 범위 번들을 만든다. 활성 번들이 ``MAX_ACTIVE_ACCOUNT_BUNDLES``개면 ``AccountSupportBundleError``."""
    s = settings or get_settings()
    data_dir = s.data_dir
    max_retention = getattr(s, "telemetry_retention_days", DEFAULT_TELEMETRY_RETENTION_DAYS)
    validated_days = validate_bundle_days(int(days), max_retention)
    now = now_epoch if now_epoch is not None else time.time()
    cutoff = now - validated_days * 86400.0
    purge_expired_bundles(data_dir, now_epoch=now)
    if len(list_active_support_bundles(data_dir, now_epoch=now, account_id=account_id)) >= MAX_ACTIVE_ACCOUNT_BUNDLES:
        raise AccountSupportBundleError(
            f"active account support bundles limit reached ({MAX_ACTIVE_ACCOUNT_BUNDLES}); delete one first"
        )

    stamp = datetime.fromtimestamp(now, timezone.utc).strftime("%Y%m%d_%H%M%S")
    bundle_id = f"sb_{stamp}_{secrets.token_hex(4)}"
    token = _BUNDLE_TOKEN_PREFIX + secrets.token_urlsafe(32)
    filename = f"support_bundle_{stamp}_{bundle_id[-8:]}.tar.zst"
    archive_path = get_support_bundles_dir(data_dir) / filename
    expires_at = now + SUPPORT_BUNDLE_TTL_SECONDS
    root = f"support_bundle_{bundle_id[-8:]}"

    themes = _owned_themes(s, account_id)
    theme_ids = [int(t.id) for t in themes]
    summary = _account_summary(s, account_id)
    conn = dbm.connect_existing(s.db_file, readonly=True)
    try:
        inbox_summary, failed_items = _pipeline(conn, theme_ids, cutoff)
        doc_ids = _owned_document_ids(conn, theme_ids)
        tracked = _tracked_document(conn, theme_ids, target) if target else None
    finally:
        conn.close()
    telemetry = _telemetry(s, doc_ids, cutoff)
    build = _get_build_identity()

    tmp_path = archive_path.with_name(f".{archive_path.name}.tmp")
    try:
        with open(tmp_path, "wb") as raw_out:
            cctx = zstd.ZstdCompressor(level=10)
            with cctx.stream_writer(raw_out) as compressed:
                with tarfile.open(fileobj=compressed, mode="w|") as tar:
                    _add_json(tar, f"{root}/account/summary.json", summary)
                    _add_json(tar, f"{root}/account/themes.json", _theme_inventory(s, themes))
                    _add_json(tar, f"{root}/pipeline/inbox_summary.json", inbox_summary)
                    _add_json(tar, f"{root}/pipeline/failed_items.json", failed_items)
                    _add_json(tar, f"{root}/telemetry/provider_records.json", telemetry)
                    if tracked is not None:
                        _add_json(tar, f"{root}/tracked_document/target_resolution.json", tracked)
                    _add_json(tar, f"{root}/diagnostics/build.json", build)
                    _add_json(
                        tar,
                        f"{root}/manifest.json",
                        {
                            "bundle_format_version": BUNDLE_FORMAT_VERSION,
                            "scope": ACCOUNT_BUNDLE_SCOPE,
                            "bundle_id": bundle_id,
                            "filename": filename,
                            "account": summary.get("account_id_public"),
                            "created_at": _iso(now),
                            "expires_at": _iso(expires_at),
                            "ttl_hours": SUPPORT_BUNDLE_TTL_SECONDS / 3600.0,
                            "days_covered": validated_days,
                            "themes": [_theme_ref(t) for t in themes],
                            "counts": {
                                "failed_items": len(failed_items),
                                "telemetry_records": len(telemetry),
                            },
                            "git_commit": build.get("git_commit"),
                            "request_context": sanitize_sensitive_data(request_context or {}),
                        },
                    )
        tmp_path.chmod(0o600)
        tmp_path.replace(archive_path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise

    size_bytes = archive_path.stat().st_size
    target_doc_id = (tracked or {}).get("document", {}).get("id") if tracked else None
    record = {
        "bundle_id": bundle_id,
        "token": token,
        "filename": filename,
        "filepath": str(archive_path),
        "days_covered": validated_days,
        "target_doc_id": target_doc_id,
        "size_bytes": size_bytes,
        "created_at": now,
        "expires_at": expires_at,
        "account_id": int(account_id),
    }
    _write_bundle_sidecar(data_dir, token=token, record=record)
    register_support_bundle(data_dir, **record)
    if get_support_bundle(data_dir, token, now_epoch=now) is None:
        delete_account_support_bundle(s, account_id=account_id, bundle_id=bundle_id, token=token)
        raise RuntimeError("Support Bundle download registration failed verification")
    return SupportBundleInfo(
        bundle_id=bundle_id,
        token=token,
        filename=filename,
        filepath=archive_path,
        days_covered=validated_days,
        size_bytes=size_bytes,
        created_at=now,
        expires_at=expires_at,
        download_url=format_download_url(s, token),
        target_doc_id=target_doc_id,
        target_matched_by=(tracked or {}).get("matched_by") if tracked else None,
        target_theme_id=(tracked or {}).get("document", {}).get("theme_id") if tracked else None,
        target_resolution_status=(tracked or {}).get("resolution_status") if tracked else None,
    )


def _public_record(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "bundle_id": record["bundle_id"],
        "filename": record["filename"],
        "days_covered": record.get("days_covered"),
        "target_doc_id": record.get("target_doc_id"),
        "size_bytes": record.get("size_bytes"),
        "created_at": _iso(record.get("created_at")),
        "expires_at": _iso(record.get("expires_at")),
    }


def list_account_support_bundles(settings: Settings, *, account_id: int, now_epoch: float | None = None) -> list[dict[str, Any]]:
    """계정의 활성 번들 메타데이터. 다운로드 토큰은 싣지 않는다."""
    return [_public_record(r) for r in list_active_support_bundles(settings.data_dir, now_epoch=now_epoch, account_id=account_id)]


def find_account_support_bundle(
    settings: Settings, *, account_id: int, bundle_id: str, now_epoch: float | None = None
) -> dict[str, Any] | None:
    """계정 소유 활성 번들 하나. 파일 경로를 검증한 레코드(``filepath`` 포함) 또는 None."""
    for record in list_active_support_bundles(settings.data_dir, now_epoch=now_epoch, account_id=account_id):
        if record.get("bundle_id") != bundle_id:
            continue
        path = _validated_archive_path(settings.data_dir, record)
        if path is None or not path.is_file():
            return None
        return {**record, "filepath": str(path)}
    return None


def delete_account_support_bundle(
    settings: Settings, *, account_id: int, bundle_id: str, token: str | None = None
) -> bool:
    """계정 소유 번들을 지운다. 없으면 False."""
    record = None
    for item in list_active_support_bundles(settings.data_dir, now_epoch=0.0, account_id=account_id):
        if item.get("bundle_id") == bundle_id:
            record = item
            break
    if record is None:
        return False
    path = _validated_archive_path(settings.data_dir, record)
    if path is not None:
        path.unlink(missing_ok=True)
    delete_support_bundle(settings.data_dir, bundle_id)
    _delete_bundle_sidecar(settings.data_dir, token or str(record.get("token") or ""))
    return True


__all__ = [
    "AccountSupportBundleError",
    "MAX_ACTIVE_ACCOUNT_BUNDLES",
    "create_account_support_bundle",
    "delete_account_support_bundle",
    "find_account_support_bundle",
    "list_account_support_bundles",
]
