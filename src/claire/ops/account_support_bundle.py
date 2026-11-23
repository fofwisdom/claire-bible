"""계정 범위 Support Bundle (유저 단위 기능).

관리자의 서버 전체 번들(``support_bundle.create_support_bundle``)과 달리 호출 계정의 데이터만 담는다.

- 담는 것: 계정 상태 요약(비밀 없음), 소유 테마 목록과 집계, 소유 테마의 적재 대기열 요약·실패 항목, 소유 테마 문서의 프로바이더 텔레메트리,
  선택한 대상 문서의 추적 정보, 빌드 정보, 그 계정의 알림 요약과 웹 푸시 기기(푸시 서비스 호스트 이름만. 엔드포인트·구독 키·알림 매개변수는 담지 않는다),
  그리고 그 계정의 연합 상태 요약(액터 상태·사이드카 점검·팔로워/발행 문서/노트/반응/아웃박스 오류 수만. 노트 본문·원격 액터 주소·키는 담지 않는다).
- 담지 않는 것: 호스트·마운트·저장소 진단, 설정 값, 로그 파일, OAuth·MCP 기록, 다른 계정의 테마·문서, 문서·노트 본문(``raw_text``·``detail``),
  비밀(토큰·키 원문).
- 등록부와 다운로드 경로는 서버 번들과 같다(``support_bundles``와 sidecar). ``account_id`` 열로 소유 계정을 구분한다.
"""

from __future__ import annotations

import io
import json
import secrets
import sqlite3
import tarfile
import time
from compression import zstd
from datetime import datetime, timezone
from typing import Any


from ..core import ids
from ..core.config import Settings, get_settings
from ..store import db as dbm
from ..store.telemetry import (
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
from .support_bundle_target import SupportTargetNotFound, document_trail, note_trail, resolve_owned_target

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
    from ..store.knowledge_base import get_kb_manager

    return list(get_kb_manager(settings).list_account_kbs(account_id))


def _theme_ref(theme: Any) -> str:
    number = getattr(theme, "number", None)
    return ids.format_public_id(number) if number is not None else str(theme.id)


def _account_summary(settings: Settings, account_id: int) -> dict[str, Any]:
    from ..store import accounts as accounts_store
    from ..store import auth_db, provider_keys
    from ..store import telegram_bots

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
                "server_mode": store.server_mode(),
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
    from ..store.knowledge_base import get_kb_manager

    manager = get_kb_manager(settings)
    out = []
    for theme in themes:
        try:
            counts = manager.kb_data_counts(theme.id)
        except Exception as exc:  # noqa: BLE001
            counts = {"error": type(exc).__name__}
        out.append(
            {
                "theme": _theme_ref(theme),
                "label": theme.label,
                "is_personal": bool(theme.is_personal),
                "is_public": bool(theme.is_public),
                "counts": counts,
            }
        )
    return out


def _placeholders(values: list[int]) -> str:
    return ",".join("?" for _ in values)


def _pipeline(conn: sqlite3.Connection, kb_ids: list[int], cutoff: float) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if not kb_ids:
        return {}, []
    marks = _placeholders(kb_ids)
    summary: dict[str, Any] = {}
    for row in conn.execute(
        f"SELECT kb_id, status, COUNT(*) FROM raw_inbox WHERE kb_id IN ({marks}) GROUP BY kb_id, status",
        kb_ids,
    ):
        summary.setdefault(str(row[0]), {})[str(row[1])] = int(row[2])
    failed = []
    for row in conn.execute(
        f"SELECT id, kb_id, received_at, source, kind, payload, file_name, document_id, status, error, attempts, last_attempt "
        f"FROM raw_inbox WHERE kb_id IN ({marks}) AND status IN ('error', 'failed') AND COALESCE(last_attempt, received_at, 0) >= ? "
        f"ORDER BY COALESCE(last_attempt, received_at) DESC LIMIT ?",
        (*kb_ids, cutoff, MAX_FAILED_ITEMS),
    ):
        failed.append(
            sanitize_sensitive_data(
                {
                    "inbox_id": row[0],
                    "kb_id": row[1],
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


def _owned_document_ids(conn: sqlite3.Connection, kb_ids: list[int]) -> set[str]:
    if not kb_ids:
        return set()
    rows = conn.execute(f"SELECT id FROM documents WHERE kb_id IN ({_placeholders(kb_ids)})", kb_ids)
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


def _tracked_document(conn: sqlite3.Connection, target: Any, input_target: str) -> dict[str, Any]:
    """The resolution member of a tracked document: the target resolution, the document identity (no body), and its ``raw_inbox`` rows."""
    doc_row = conn.execute(
        "SELECT id, kb_id, url, canonical_url, title, source_type, fetched_at, partial, lang, hidden FROM documents WHERE id = ? AND kb_id = ?",
        (target.document_id, int(target.kb_id)),
    ).fetchone()
    out = target.resolution(input_target)
    if doc_row is None:
        return out
    out["document"] = sanitize_sensitive_data(
        {
            "id": str(doc_row[0]),
            "kb_id": doc_row[1],
            "url": doc_row[2],
            "canonical_url": doc_row[3],
            "title": doc_row[4],
            "source_type": doc_row[5],
            "fetched_at": _iso(doc_row[6]),
            "partial": bool(doc_row[7]),
            "lang": doc_row[8],
            "hidden": bool(doc_row[9]),
        }
    )
    out["inbox_records"] = [
        sanitize_sensitive_data(
            {"inbox_id": r[0], "status": r[1], "error": (r[2] or "")[:1000], "attempts": r[3], "received_at": _iso(r[4])}
        )
        for r in conn.execute(
            "SELECT id, status, error, attempts, received_at FROM raw_inbox WHERE kb_id = ? AND document_id = ? ORDER BY received_at DESC LIMIT 20",
            (int(target.kb_id), str(target.document_id)),
        )
    ]
    return out


def _notifications(settings: Settings, account_id: int, cutoff: float, now: float) -> dict[str, Any]:
    """The notifications and the Web Push devices of the account (docs/implementation/NOTIFICATIONS.md). A failure of this part does not stop the bundle: the member then has the error type."""
    from .support_bundle_notifications import collect_account_notifications

    try:
        return collect_account_notifications(settings, account_id=account_id, now=now, since=cutoff)
    except Exception as exc:  # noqa: BLE001
        return {"state": "error", "error": type(exc).__name__, "findings": [], "devices": []}


def _federation(settings: Settings, account_id: int, cutoff: float, now: float) -> dict[str, Any]:
    """The federation status, sidecar checks, and note/reaction/outbox counts of the account. A failure does not stop the bundle."""
    from .support_bundle_federation import collect_account_federation

    try:
        return collect_account_federation(settings, account_id=account_id, now=now, since=cutoff)
    except Exception as exc:  # noqa: BLE001
        return {"state": "error", "error": type(exc).__name__, "findings": []}


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
    kb_ids = [int(t.id) for t in themes]
    summary = _account_summary(s, account_id)
    tracked_target = None
    tracked: dict[str, Any] | None = None
    trail: dict[str, Any] | None = None
    conn = dbm.connect_existing(s.db_file, readonly=True)
    try:
        if target:
            # The link must name a note or a document of this account (SupportTargetNotFound otherwise): a bundle never tracks an item of another account.
            tracked_target = resolve_owned_target(s, conn, target, account_id=int(account_id), kb_ids=kb_ids)
            if tracked_target.kind == "document":
                tracked = _tracked_document(conn, tracked_target, target)
                trail = document_trail(s, conn, kb_id=int(tracked_target.kb_id or 0), document_id=str(tracked_target.document_id))
        inbox_summary, failed_items = _pipeline(conn, kb_ids, cutoff)
        doc_ids = _owned_document_ids(conn, kb_ids)
    finally:
        conn.close()
    if tracked_target is not None and tracked_target.kind == "note":
        tracked = tracked_target.resolution(target or "")
        trail = note_trail(s, account_id=int(account_id), public_id=int(tracked_target.note_public_id or 0), now=now)
    telemetry = _telemetry(s, doc_ids, cutoff)
    notification_state = _notifications(s, account_id, cutoff, now)
    federation_state = _federation(s, account_id, cutoff, now)
    build = _get_build_identity()

    tmp_path = archive_path.with_name(f".{archive_path.name}.tmp")
    try:
        with open(tmp_path, "wb") as raw_out:
            with zstd.ZstdFile(raw_out, "wb", level=10) as compressed:
                with tarfile.open(fileobj=compressed, mode="w|") as tar:
                    _add_json(tar, f"{root}/account/summary.json", summary)
                    _add_json(tar, f"{root}/account/themes.json", _theme_inventory(s, themes))
                    _add_json(tar, f"{root}/pipeline/inbox_summary.json", inbox_summary)
                    _add_json(tar, f"{root}/pipeline/failed_items.json", failed_items)
                    _add_json(tar, f"{root}/telemetry/provider_records.json", telemetry)
                    _add_json(tar, f"{root}/notifications/summary.json", notification_state)
                    _add_json(tar, f"{root}/federation/summary.json", federation_state)
                    if tracked_target is not None and tracked_target.kind == "document":
                        _add_json(tar, f"{root}/tracked_document/target_resolution.json", tracked)
                        _add_json(tar, f"{root}/tracked_document/extraction_trail.json", trail)
                    elif tracked_target is not None:
                        _add_json(tar, f"{root}/tracked_note/target_resolution.json", tracked)
                        _add_json(tar, f"{root}/tracked_note/note_trail.json", trail)
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
                                "notification_devices": len(notification_state.get("devices", [])),
                                "notification_findings": len(notification_state.get("findings", [])),
                                "federation_findings": len(federation_state.get("findings", [])),
                                "federation_notes": int(((federation_state.get("notes") or {}).get("total")) or 0),
                            },
                            "target": None if tracked_target is None else {"kind": tracked_target.kind, "link": tracked_target.link, "findings": [f["code"] for f in (trail or {}).get("findings", [])]},
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
    target_doc_id = tracked_target.document_id if tracked_target is not None and tracked_target.kind == "document" else None
    target_link = (tracked_target.link or target) if tracked_target is not None else None
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
        "target_link": target_link,
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
        target_matched_by=tracked_target.matched_by if tracked_target is not None else None,
        target_kb_id=tracked_target.kb_id if tracked_target is not None else None,
        target_resolution_status="resolved" if tracked_target is not None else None,
        target_kind=tracked_target.kind if tracked_target is not None else None,
        target_link=target_link,
    )


def _public_record(settings: Settings, record: dict[str, Any]) -> dict[str, Any]:
    """Record for the owner of the bundle. ``download_url`` is the token address: the owner can copy it from the list at each time."""
    token = str(record.get("token") or "")
    return {
        "download_url": format_download_url(settings, token) if token else None,
        "bundle_id": record["bundle_id"],
        "filename": record["filename"],
        "days_covered": record.get("days_covered"),
        "target_doc_id": record.get("target_doc_id"),
        "target_link": record.get("target_link"),
        "size_bytes": record.get("size_bytes"),
        "created_at": _iso(record.get("created_at")),
        "expires_at": _iso(record.get("expires_at")),
    }


def list_account_support_bundles(settings: Settings, *, account_id: int, now_epoch: float | None = None) -> list[dict[str, Any]]:
    """Active bundles of the account, each with its token address (``download_url``) for the owner."""
    return [_public_record(settings, r) for r in list_active_support_bundles(settings.data_dir, now_epoch=now_epoch, account_id=account_id)]


def list_server_support_bundles(settings: Settings, *, now_epoch: float | None = None) -> list[dict[str, Any]]:
    """Active server-wide bundles (``account_id`` NULL), each with its token address, for the administrator."""
    return [_public_record(settings, r) for r in list_active_support_bundles(settings.data_dir, now_epoch=now_epoch, server_only=True)]


def delete_server_support_bundle(settings: Settings, *, bundle_id: str) -> bool:
    """Delete one server-wide bundle (archive, row, and sidecar). False when no such bundle exists."""
    record = next((item for item in list_active_support_bundles(settings.data_dir, now_epoch=0.0, server_only=True) if item.get("bundle_id") == bundle_id), None)
    if record is None:
        return False
    path = _validated_archive_path(settings.data_dir, record)
    if path is not None:
        path.unlink(missing_ok=True)
    delete_support_bundle(settings.data_dir, bundle_id)
    _delete_bundle_sidecar(settings.data_dir, str(record.get("token") or ""))
    return True


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
    "SupportTargetNotFound",
    "MAX_ACTIVE_ACCOUNT_BUNDLES",
    "create_account_support_bundle",
    "delete_account_support_bundle",
    "delete_server_support_bundle",
    "list_account_support_bundles",
    "list_server_support_bundles",
]
