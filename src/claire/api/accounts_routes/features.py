"""유저 단위 기능 라우트: 텔레그램 봇, 서포트 번들, 데이터 번들, 백업.

계정 소유자(``scope == "owner"``)가 WebUI(``/settings/telegram``·``/settings/bundles``·``/settings/backups``)에서 자기 계정의 기능을 구성·제어한다.
복원만 운영자(관리자) 전용이다(``/admin/backups``, ``POST /api/admin/account-backups/restore``).

| 경로 | 메서드 | 기능 |
|------|--------|------|
| ``/api/me/telegram`` | GET, PUT, PATCH, DELETE | 내 봇 조회·등록(토큰 검증)·켜기/끄기·허용 사용자 변경·삭제 |
| ``/api/me/support-bundles`` | GET, POST, DELETE | 내 계정 범위 서포트 번들 목록·생성·삭제 |
| ``/api/me/support-bundles/download`` | GET | 내 서포트 번들 내려받기 |
| ``/api/me/data-bundles`` | GET, POST, DELETE | 내 테마 데이터(그래프) 번들 목록·생성·삭제 |
| ``/api/me/data-bundles/download`` | GET | 내 데이터 번들 내려받기 |
| ``/api/me/backups`` | GET, POST, DELETE | 내 계정 백업 목록·생성·삭제 |
| ``/api/me/backups/download`` | GET | 내 백업 내려받기 |
| ``/api/me/backups/policy`` | PUT | 자동 백업 주기·보존 개수 |
| ``/api/admin/account-backups`` | GET | 계정별 백업 목록(운영자) |
| ``/api/admin/account-backups/restore`` | POST | 계정 백업 복원(운영자) |

규칙:

- 봇 토큰은 쓰기 전용 비밀이다. 응답에는 메타데이터(봇 USERNAME, fingerprint, 상태)만 싣는다.
- 봇 등록·교체·삭제, 허용 사용자 변경, 백업 삭제, 복원은 단계 인증(`common.require_recent_auth`)을 요구한다.
- 다른 계정의 번들·백업은 없는 것과 같게 404로 다룬다.
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from typing import Any

from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Route

from ...secret_scrub import register_secret, scrub_text
from ...store import accounts as accounts_store
from ...store import auth_db
from ...store import telegram_bots
from ..principal import Principal, request_principal
from ..security import read_json_body
from .common import (
    AccountsContext,
    escape,
    format_ts,
    json_error,
    not_found,
    open_auth_db,
    open_db,
    require_recent_auth,
)

ME_TELEGRAM_PATH = "/api/me/telegram"
ME_SUPPORT_BUNDLES_PATH = "/api/me/support-bundles"
ME_SUPPORT_BUNDLES_DOWNLOAD_PATH = "/api/me/support-bundles/download"
ME_DATA_BUNDLES_PATH = "/api/me/data-bundles"
ME_DATA_BUNDLES_DOWNLOAD_PATH = "/api/me/data-bundles/download"
ME_BACKUPS_PATH = "/api/me/backups"
ME_BACKUPS_DOWNLOAD_PATH = "/api/me/backups/download"
ME_BACKUPS_POLICY_PATH = "/api/me/backups/policy"
ADMIN_ACCOUNT_BACKUPS_PATH = "/api/admin/account-backups"
ADMIN_ACCOUNT_BACKUPS_RESTORE_PATH = "/api/admin/account-backups/restore"

NO_STORE = {"Cache-Control": "no-store"}
BOT_STATUS_TEXT = {"pending": "시작 대기", "running": "실행 중", "error": "오류", "disabled": "꺼짐"}
SCHEDULE_TEXT = {"off": "끔", "daily": "매일", "weekly": "매주"}


# --- 공용 -----------------------------------------------------------------------------


def _settings(ctx: AccountsContext) -> Any:
    if ctx.settings is not None:
        return ctx.settings
    from ...config import get_settings

    return get_settings()


def _owner(request: Request) -> tuple[Principal, Response | None]:
    principal = request_principal(request)
    if principal.account_id is None or principal.scope != "owner":
        return principal, not_found()
    return principal, None


def _is_admin(principal: Principal) -> bool:
    return principal.account_id is not None and principal.instance_role == "admin" and principal.scope == "owner"


def _download(path: str, filename: str) -> Response:
    p = Path(path)
    if not p.is_file():
        return not_found()
    return FileResponse(
        str(p),
        media_type="application/zstd",
        filename=filename,
        headers={"Cache-Control": "no-store, private", "Content-Disposition": f'attachment; filename="{filename}"'},
    )


def purge_account_features(auth_conn: sqlite3.Connection, account_id: int, *, data_dir: Path | str | None) -> dict[str, int]:
    """계정 정보 초기화(purge) 때 유저 단위 기능 자료를 지운다. 지운 수."""
    stats = {"telegram_bots": telegram_bots.delete_bot(auth_conn, account_id=account_id), "backups": 0, "support_bundles": 0}
    with auth_conn:
        auth_conn.execute("DELETE FROM account_backup_policies WHERE account_id=?", (account_id,))
    if data_dir is None:
        return stats
    from ...account_backup import delete_all_account_backups
    from ...store.telemetry import delete_support_bundle, list_active_support_bundles

    stats["backups"] = delete_all_account_backups(data_dir, account_id)
    for record in list_active_support_bundles(data_dir, now_epoch=0.0, account_id=account_id):
        path = Path(str(record.get("filepath") or ""))
        if path.is_file() and path.parent.name == "support_bundles":
            path.unlink(missing_ok=True)
        delete_support_bundle(data_dir, str(record["bundle_id"]))
        stats["support_bundles"] += 1
    return stats


# --- 텔레그램 ---------------------------------------------------------------------------


def _instance_bot_id(ctx: AccountsContext) -> int | None:
    token = str(getattr(_settings(ctx), "telegram_bot_token", "") or "")
    return telegram_bots.bot_id_from_token(token) if token else None


def telegram_state(ctx: AccountsContext, account_id: int) -> dict[str, Any]:
    auth = open_auth_db(ctx)
    try:
        meta = telegram_bots.get_meta(auth, account_id=account_id)
    finally:
        auth.close()
    return {"bot": None if meta is None else meta.to_dict()}


def _audit(ctx: AccountsContext, account_id: int, action: str, detail: dict[str, Any] | None = None, *, target: int | None = None) -> None:
    auth = open_auth_db(ctx)
    try:
        auth_db.record_admin_audit(
            auth, actor_account_id=account_id, action=action, target_account_id=account_id if target is None else target, detail=detail
        )
    finally:
        auth.close()


def _store_bot(ctx: AccountsContext, account_id: int, token: str, username: str, allowed: tuple[int, ...]) -> dict[str, Any]:
    reserved = [i for i in (_instance_bot_id(ctx),) if i]
    auth = open_auth_db(ctx)
    try:
        meta = telegram_bots.put_bot(
            auth, account_id=account_id, token=token, bot_username=username, allowed_user_ids=allowed, reserved_bot_ids=reserved
        )
        auth_db.record_admin_audit(
            auth, actor_account_id=account_id, action="account.telegram_bot_put", target_account_id=account_id,
            detail={"bot_username": username, "allowed_user_count": len(allowed)},
        )
        return meta.to_dict()
    finally:
        auth.close()


def _update_bot(ctx: AccountsContext, account_id: int, *, enabled: bool | None, allowed: tuple[int, ...] | None) -> dict[str, Any] | None:
    auth = open_auth_db(ctx)
    try:
        meta = telegram_bots.update_settings(auth, account_id=account_id, enabled=enabled, allowed_user_ids=allowed)
        if meta is not None:
            auth_db.record_admin_audit(
                auth, actor_account_id=account_id, action="account.telegram_bot_update", target_account_id=account_id,
                detail={"enabled": enabled, "allowed_user_count": None if allowed is None else len(allowed)},
            )
        return None if meta is None else meta.to_dict()
    finally:
        auth.close()


def _delete_bot(ctx: AccountsContext, account_id: int) -> int:
    auth = open_auth_db(ctx)
    try:
        deleted = telegram_bots.delete_bot(auth, account_id=account_id)
        if deleted:
            auth_db.record_admin_audit(auth, actor_account_id=account_id, action="account.telegram_bot_delete", target_account_id=account_id)
        return deleted
    finally:
        auth.close()


# --- 테마 해석 -----------------------------------------------------------------------------


def _owned_theme(ctx: AccountsContext, account_id: int, raw: Any) -> Any | None:
    from ...store.theme import ThemeNotFound, get_theme_manager

    manager = get_theme_manager(_settings(ctx))
    try:
        if raw is None or str(raw).strip() == "":
            theme = manager.default_theme_for(account_id)
        else:
            theme = manager.resolve_theme_ref(str(raw).strip(), account_id=account_id).theme
    except (ThemeNotFound, ValueError, KeyError):
        return None
    return theme if int(getattr(theme, "account_id", -1)) == int(account_id) else None


def _owned_themes(ctx: AccountsContext, account_id: int) -> list[Any]:
    from ...store.theme import get_theme_manager

    return list(get_theme_manager(_settings(ctx)).list_account_themes(account_id))


def _theme_ref(theme: Any) -> str:
    from ... import ids

    number = getattr(theme, "number", None)
    return ids.format_public_id(number) if number is not None else str(theme.id)


# --- 라우트 ------------------------------------------------------------------------------


def routes(ctx: AccountsContext) -> list[Route]:
    # ---- 텔레그램 ----
    async def telegram_get(request: Request) -> Response:
        principal, denied = _owner(request)
        if denied is not None:
            return denied
        assert principal.account_id is not None
        state = await asyncio.to_thread(telegram_state, ctx, principal.account_id)
        return JSONResponse(state, headers=NO_STORE)

    async def telegram_put(request: Request) -> Response:
        principal, denied = _owner(request)
        if denied is not None:
            return denied
        assert principal.account_id is not None
        step_up = require_recent_auth(principal)
        if step_up is not None:
            return step_up
        body = await read_json_body(request)
        raw_token = body.pop("bot_token", None)
        if isinstance(raw_token, str):
            register_secret(raw_token)
            register_secret(raw_token.strip())
        if set(body) != {"allowed_user_ids"} or raw_token is None:
            return json_error(400, "invalid_request", fields=["bot_token", "allowed_user_ids"])
        parsed = telegram_bots.parse_bot_token(raw_token)
        del raw_token
        if parsed is None:
            return json_error(400, "invalid_bot_token")
        bot_id, token = parsed
        allowed = telegram_bots.parse_allowed_user_ids(body["allowed_user_ids"])
        if allowed is None:
            return json_error(400, "invalid_allowed_user_ids", max=telegram_bots.MAX_ALLOWED_USERS)
        if bot_id == _instance_bot_id(ctx):
            return json_error(409, "bot_in_use")
        from ...telegram_api import TelegramVerifyRejected, TelegramVerifyUnavailable, verify_bot_token

        try:
            identity = await asyncio.to_thread(verify_bot_token, token)
        except TelegramVerifyRejected:
            return json_error(422, "bot_token_rejected")
        except TelegramVerifyUnavailable as exc:
            return json_error(503, "bot_verification_unavailable", message=scrub_text(exc, extra=(token,)))
        if identity.bot_id != bot_id:
            return json_error(422, "bot_token_rejected")
        try:
            meta = await asyncio.to_thread(_store_bot, ctx, principal.account_id, token, identity.username, allowed)
        except telegram_bots.TelegramBotConflict:
            return json_error(409, "bot_in_use")
        except telegram_bots.TelegramBotError:
            return json_error(400, "invalid_bot_token")
        return JSONResponse({"bot": meta}, headers=NO_STORE)

    async def telegram_patch(request: Request) -> Response:
        principal, denied = _owner(request)
        if denied is not None:
            return denied
        assert principal.account_id is not None
        body = await read_json_body(request)
        if not body or set(body) - {"enabled", "allowed_user_ids"}:
            return json_error(400, "invalid_request", fields=["enabled", "allowed_user_ids"])
        enabled = body.get("enabled")
        if "enabled" in body and not isinstance(enabled, bool):
            return json_error(400, "invalid_enabled")
        allowed: tuple[int, ...] | None = None
        if "allowed_user_ids" in body:
            # 허용 사용자를 바꾸면 누가 내 계정으로 적재할 수 있는지가 바뀐다. 단계 인증을 요구한다.
            step_up = require_recent_auth(principal)
            if step_up is not None:
                return step_up
            allowed = telegram_bots.parse_allowed_user_ids(body["allowed_user_ids"])
            if allowed is None:
                return json_error(400, "invalid_allowed_user_ids", max=telegram_bots.MAX_ALLOWED_USERS)
        meta = await asyncio.to_thread(_update_bot, ctx, principal.account_id, enabled=enabled, allowed=allowed)
        if meta is None:
            return json_error(404, "bot_not_found")
        return JSONResponse({"bot": meta}, headers=NO_STORE)

    async def telegram_delete(request: Request) -> Response:
        principal, denied = _owner(request)
        if denied is not None:
            return denied
        assert principal.account_id is not None
        step_up = require_recent_auth(principal)
        if step_up is not None:
            return step_up
        deleted = await asyncio.to_thread(_delete_bot, ctx, principal.account_id)
        return JSONResponse({"ok": True, "deleted": deleted})

    # ---- 서포트 번들 ----
    async def support_list(request: Request) -> Response:
        principal, denied = _owner(request)
        if denied is not None:
            return denied
        from ...account_support_bundle import MAX_ACTIVE_ACCOUNT_BUNDLES, list_account_support_bundles

        items = await asyncio.to_thread(list_account_support_bundles, _settings(ctx), account_id=principal.account_id)
        return JSONResponse({"bundles": items, "max_active": MAX_ACTIVE_ACCOUNT_BUNDLES}, headers=NO_STORE)

    async def support_create(request: Request) -> Response:
        principal, denied = _owner(request)
        if denied is not None:
            return denied
        body = await read_json_body(request)
        if set(body) - {"days", "target"}:
            return json_error(400, "invalid_request", fields=["days", "target"])
        days = body.get("days", 1)
        if not isinstance(days, int) or isinstance(days, bool):
            return json_error(400, "invalid_days")
        target = body.get("target")
        if target is not None and (not isinstance(target, str) or len(target) > 2048):
            return json_error(400, "invalid_target")
        from ...account_support_bundle import AccountSupportBundleError, create_account_support_bundle

        def _create() -> dict[str, Any]:
            info = create_account_support_bundle(
                _settings(ctx), account_id=principal.account_id, days=days, target=(target or "").strip() or None,
                request_context={"channel": "webui"},
            )
            return {
                "bundle_id": info.bundle_id,
                "filename": info.filename,
                "days_covered": info.days_covered,
                "size_bytes": info.size_bytes,
                "target_doc_id": info.target_doc_id,
                "target_resolution_status": info.target_resolution_status,
            }

        try:
            created = await asyncio.to_thread(_create)
        except AccountSupportBundleError as exc:
            return json_error(409, "bundle_limit_reached", message=str(exc))
        except ValueError as exc:
            return json_error(400, "invalid_days", message=str(exc))
        return JSONResponse({"bundle": created}, headers=NO_STORE)

    async def support_delete(request: Request) -> Response:
        principal, denied = _owner(request)
        if denied is not None:
            return denied
        bundle_id = request.query_params.get("bundle_id", "")
        from ...account_support_bundle import delete_account_support_bundle

        ok = await asyncio.to_thread(delete_account_support_bundle, _settings(ctx), account_id=principal.account_id, bundle_id=bundle_id)
        if not ok:
            return json_error(404, "bundle_not_found")
        return JSONResponse({"ok": True})

    async def support_download(request: Request) -> Response:
        principal, denied = _owner(request)
        if denied is not None:
            return denied
        from ...account_support_bundle import find_account_support_bundle

        record = await asyncio.to_thread(
            find_account_support_bundle, _settings(ctx), account_id=principal.account_id, bundle_id=request.query_params.get("bundle_id", "")
        )
        if record is None:
            return not_found()
        return _download(record["filepath"], record["filename"])

    # ---- 데이터 번들 ----
    async def data_list(request: Request) -> Response:
        principal, denied = _owner(request)
        if denied is not None:
            return denied
        from ...graph_bundle import list_account_graph_bundles

        s = _settings(ctx)
        items = await asyncio.to_thread(list_account_graph_bundles, s.data_dir, principal.account_id)
        themes = await asyncio.to_thread(_owned_themes, ctx, principal.account_id)
        return JSONResponse(
            {
                "bundles": items,
                "themes": [{"theme": _theme_ref(t), "theme_id": t.id, "label": t.label, "icon": t.icon} for t in themes],
            },
            headers=NO_STORE,
        )

    async def data_create(request: Request) -> Response:
        principal, denied = _owner(request)
        if denied is not None:
            return denied
        body = await read_json_body(request)
        if set(body) - {"theme"}:
            return json_error(400, "invalid_request", fields=["theme"])
        theme = await asyncio.to_thread(_owned_theme, ctx, principal.account_id, body.get("theme"))
        if theme is None:
            return json_error(404, "theme_not_found")
        from ...graph_bundle import create_graph_bundle

        def _create() -> dict[str, Any]:
            info = create_graph_bundle(
                _settings(ctx), theme_id=theme.id, request_context={"channel": "webui"}, account_id=principal.account_id
            )
            return {
                "bundle_id": info.bundle_id,
                "filename": info.filename,
                "theme": _theme_ref(theme),
                "entities_count": info.entities_count,
                "relations_count": info.relations_count,
                "documents_count": info.documents_count,
                "size_bytes": info.size_bytes,
            }

        try:
            created = await asyncio.to_thread(_create)
        except FileNotFoundError:
            return json_error(404, "theme_not_found")
        return JSONResponse({"bundle": created}, headers=NO_STORE)

    async def data_delete(request: Request) -> Response:
        principal, denied = _owner(request)
        if denied is not None:
            return denied
        from ...graph_bundle import delete_account_graph_bundle

        ok = await asyncio.to_thread(
            delete_account_graph_bundle, _settings(ctx).data_dir, principal.account_id, request.query_params.get("bundle_id", "")
        )
        if not ok:
            return json_error(404, "bundle_not_found")
        return JSONResponse({"ok": True})

    async def data_download(request: Request) -> Response:
        principal, denied = _owner(request)
        if denied is not None:
            return denied
        from ...graph_bundle import find_account_graph_bundle

        record = await asyncio.to_thread(
            find_account_graph_bundle, _settings(ctx).data_dir, principal.account_id, request.query_params.get("bundle_id", "")
        )
        if record is None:
            return not_found()
        return _download(record["filepath"], str(record["filename"]))

    # ---- 백업 ----
    async def backups_list(request: Request) -> Response:
        principal, denied = _owner(request)
        if denied is not None:
            return denied
        from ...account_backup import list_account_backups, load_policy

        s = _settings(ctx)
        items = await asyncio.to_thread(list_account_backups, s, account_id=principal.account_id)
        policy = await asyncio.to_thread(load_policy, s, account_id=principal.account_id)
        return JSONResponse({"backups": items, "policy": policy.to_dict()}, headers=NO_STORE)

    async def backups_create(request: Request) -> Response:
        principal, denied = _owner(request)
        if denied is not None:
            return denied
        body = await read_json_body(request)
        if body:
            return json_error(400, "invalid_request", fields=[])
        from ...account_backup import AccountBackupError, create_account_backup

        try:
            record = await asyncio.to_thread(create_account_backup, _settings(ctx), account_id=principal.account_id, reason="manual")
        except AccountBackupError as exc:
            return json_error(409, "backup_failed", message=str(exc))
        return JSONResponse({"backup": record}, headers=NO_STORE)

    async def backups_delete(request: Request) -> Response:
        principal, denied = _owner(request)
        if denied is not None:
            return denied
        step_up = require_recent_auth(principal)
        if step_up is not None:
            return step_up
        from ...account_backup import delete_account_backup

        ok = await asyncio.to_thread(
            delete_account_backup, _settings(ctx), account_id=principal.account_id, backup_id=request.query_params.get("backup_id", "")
        )
        if not ok:
            return json_error(404, "backup_not_found")
        return JSONResponse({"ok": True})

    async def backups_download(request: Request) -> Response:
        principal, denied = _owner(request)
        if denied is not None:
            return denied
        from ...account_backup import find_account_backup

        record = await asyncio.to_thread(
            find_account_backup, _settings(ctx), account_id=principal.account_id, backup_id=request.query_params.get("backup_id", "")
        )
        if record is None:
            return not_found()
        return _download(record["filepath"], record["filename"])

    async def backups_policy(request: Request) -> Response:
        principal, denied = _owner(request)
        if denied is not None:
            return denied
        body = await read_json_body(request)
        if set(body) != {"schedule", "keep"}:
            return json_error(400, "invalid_request", fields=["schedule", "keep"])
        from ...account_backup import save_policy

        try:
            policy = await asyncio.to_thread(
                save_policy, _settings(ctx), account_id=principal.account_id, schedule=body["schedule"], keep=body["keep"]
            )
        except ValueError as exc:
            return json_error(400, "invalid_policy", message=str(exc))
        return JSONResponse({"policy": policy.to_dict()}, headers=NO_STORE)

    # ---- 운영자 복원 ----
    async def admin_backups_list(request: Request) -> Response:
        principal = request_principal(request)
        if not _is_admin(principal):
            return not_found()
        from ...account_backup import list_backups_for_admin

        items = await asyncio.to_thread(list_backups_for_admin, _settings(ctx))
        for item in items:
            item.pop("account_id", None)
        return JSONResponse({"accounts": items}, headers=NO_STORE)

    async def admin_backups_restore(request: Request) -> Response:
        principal = request_principal(request)
        if not _is_admin(principal):
            return not_found()
        assert principal.account_id is not None
        step_up = require_recent_auth(principal)
        if step_up is not None:
            return step_up
        body = await read_json_body(request)
        if set(body) != {"account", "backup_id"}:
            return json_error(400, "invalid_request", fields=["account", "backup_id"])
        account_ref = body["account"]
        backup_id = body["backup_id"]
        if not isinstance(account_ref, str) or not account_ref.isdigit() or not isinstance(backup_id, str):
            return json_error(400, "invalid_request", fields=["account", "backup_id"])

        def _resolve() -> int | None:
            conn = open_db(ctx)
            try:
                row = conn.execute("SELECT id FROM accounts WHERE public_id=?", (int(account_ref),)).fetchone()
                return None if row is None else int(row[0])
            finally:
                conn.close()

        account_id = await asyncio.to_thread(_resolve)
        if account_id is None:
            return json_error(404, "account_not_found")
        from ...account_backup import AccountBackupConflict, AccountBackupError, restore_account_backup

        try:
            result = await asyncio.to_thread(
                restore_account_backup, _settings(ctx), account_id=account_id, backup_id=backup_id, actor_account_id=principal.account_id
            )
        except AccountBackupConflict as exc:
            return json_error(409, "restore_conflict", message=str(exc))
        except AccountBackupError as exc:
            message = str(exc)
            if message == "backup not found":
                return json_error(404, "backup_not_found")
            return json_error(400, "restore_failed", message=message)
        return JSONResponse({"ok": True, **result}, headers=NO_STORE)

    return [
        Route(ME_TELEGRAM_PATH, telegram_get, methods=["GET", "HEAD"]),
        Route(ME_TELEGRAM_PATH, telegram_put, methods=["PUT"]),
        Route(ME_TELEGRAM_PATH, telegram_patch, methods=["PATCH"]),
        Route(ME_TELEGRAM_PATH, telegram_delete, methods=["DELETE"]),
        Route(ME_SUPPORT_BUNDLES_PATH, support_list, methods=["GET", "HEAD"]),
        Route(ME_SUPPORT_BUNDLES_PATH, support_create, methods=["POST"]),
        Route(ME_SUPPORT_BUNDLES_PATH, support_delete, methods=["DELETE"]),
        Route(ME_SUPPORT_BUNDLES_DOWNLOAD_PATH, support_download, methods=["GET", "HEAD"]),
        Route(ME_DATA_BUNDLES_PATH, data_list, methods=["GET", "HEAD"]),
        Route(ME_DATA_BUNDLES_PATH, data_create, methods=["POST"]),
        Route(ME_DATA_BUNDLES_PATH, data_delete, methods=["DELETE"]),
        Route(ME_DATA_BUNDLES_DOWNLOAD_PATH, data_download, methods=["GET", "HEAD"]),
        Route(ME_BACKUPS_PATH, backups_list, methods=["GET", "HEAD"]),
        Route(ME_BACKUPS_PATH, backups_create, methods=["POST"]),
        Route(ME_BACKUPS_PATH, backups_delete, methods=["DELETE"]),
        Route(ME_BACKUPS_DOWNLOAD_PATH, backups_download, methods=["GET", "HEAD"]),
        Route(ME_BACKUPS_POLICY_PATH, backups_policy, methods=["PUT"]),
        Route(ADMIN_ACCOUNT_BACKUPS_PATH, admin_backups_list, methods=["GET", "HEAD"]),
        Route(ADMIN_ACCOUNT_BACKUPS_RESTORE_PATH, admin_backups_restore, methods=["POST"]),
    ]


# --- 콘솔 화면 본문 ------------------------------------------------------------------------------


def _reauth_html(ctx: AccountsContext, account_id: int) -> str:
    auth = open_auth_db(ctx)
    try:
        has_pw = auth_db.has_password(auth, account_id)
    finally:
        auth.close()
    if not has_pw:
        return (
            '<section class="console-subcard" data-feature-reauth="none" aria-label="본인 확인">'
            "<h2>🔐 본인 확인</h2>"
            '<p class="hint">이 계정에는 비밀번호가 없습니다. 로그인한 뒤 15분 동안은 본인 확인을 대신합니다. 시간이 지났으면 다시 로그인하세요.</p>'
            "</section>"
        )
    return (
        '<form class="console-subcard" data-feature-form="reauth" aria-label="본인 확인">'
        "<h2>🔐 본인 확인 (단계 인증)</h2>"
        '<p class="hint">봇 등록·삭제, 허용 사용자 변경, 백업 삭제, 복원은 15분 안에 본인 확인을 해야 합니다.</p>'
        '<label class="feature-field">현재 비밀번호<input name="password" type="password" class="pref-input" required autocomplete="current-password"/></label>'
        '<button type="submit" class="pref-btn pref-btn-accent">본인 확인</button>'
        '<p class="result" data-feature-result role="status" aria-live="polite"></p>'
        "</form>"
    )


def settings_telegram_body(ctx: AccountsContext, *, account: accounts_store.Account) -> str:
    state = telegram_state(ctx, account.id)
    bot = state["bot"]
    if bot is None:
        status_html = '<p class="hint" data-telegram-empty>등록한 봇이 없습니다. 아래에서 BotFather 토큰을 등록하세요.</p>'
        allowed_value = ""
        enabled_checked = ""
        manage_disabled = " disabled"
    else:
        rows = [
            ("봇", f"@{bot['bot_username']}" if bot["bot_username"] else str(bot["bot_id"])),
            ("fingerprint", bot["fingerprint"]),
            ("상태", BOT_STATUS_TEXT.get(bot["status"], bot["status"])),
            ("마지막 오류", bot["last_error"] or "-"),
            ("상태 시각", format_ts(bot["status_at"])),
            ("허용 사용자 수", str(len(bot["allowed_user_ids"]))),
        ]
        status_html = "<dl data-telegram-meta>" + "".join(f"<dt>{escape(k)}</dt><dd>{escape(v)}</dd>" for k, v in rows) + "</dl>"
        allowed_value = ", ".join(str(i) for i in bot["allowed_user_ids"])
        enabled_checked = " checked" if bot["enabled"] else ""
        manage_disabled = ""
    return (
        '<div class="settings-features" data-feature-section="telegram">'
        f"{_reauth_html(ctx, account.id)}"
        '<section class="card" aria-label="내 텔레그램 봇 상태">'
        "<h2>🤖 내 텔레그램 봇</h2>"
        '<p class="hint">이 봇은 내 계정의 테마에만 적재·검색합니다. 봇 러너가 15초 안에 설정 변경을 반영합니다. 토큰은 암호화해 저장하고 다시 보여 주지 않습니다.</p>'
        f'<div data-telegram-status>{status_html}</div>'
        '<div class="pref-row"><div class="pref-row-label"><strong>봇 켜기</strong><span>끄면 봇이 메시지를 받지 않습니다.</span></div>'
        f'<input id="pref-telegram-enabled" type="checkbox" switch data-feature-toggle="telegram-enabled"{enabled_checked}{manage_disabled} aria-label="텔레그램 봇 켜기"/></div>'
        '<p class="result" data-feature-result role="status" aria-live="polite"></p>'
        "</section>"
        '<form class="card" data-feature-form="telegram-register" aria-label="봇 등록 또는 교체">'
        "<h2>봇 등록 · 교체</h2>"
        '<label class="feature-field">BotFather 토큰<input name="bot_token" type="password" class="pref-input" required autocomplete="off" spellcheck="false" placeholder="123456789:AA..."/></label>'
        f'<label class="feature-field">허용할 텔레그램 사용자 ID (쉼표로 구분, 최대 {telegram_bots.MAX_ALLOWED_USERS}개)<input name="allowed_user_ids" class="pref-input" required value="{escape(allowed_value)}" placeholder="12345678"/></label>'
        '<button type="submit" class="pref-btn pref-btn-accent">토큰 확인 후 저장</button>'
        '<p class="result" data-feature-result role="status" aria-live="polite"></p>'
        "</form>"
        f'<form class="card" data-feature-form="telegram-allowed" aria-label="허용 사용자 변경"{" hidden" if bot is None else ""}>'
        "<h2>허용 사용자 변경</h2>"
        f'<label class="feature-field">텔레그램 사용자 ID<input name="allowed_user_ids" class="pref-input" required value="{escape(allowed_value)}"/></label>'
        '<button type="submit" class="pref-btn">변경 저장</button>'
        '<p class="result" data-feature-result role="status" aria-live="polite"></p>'
        "</form>"
        f'<section class="card" aria-label="봇 삭제"{" hidden" if bot is None else ""}>'
        "<h2>봇 삭제</h2>"
        '<p class="hint">저장한 토큰과 설정을 지웁니다. 봇은 다음 주기에 멈춥니다.</p>'
        '<button type="button" class="pref-btn" data-feature-action="telegram-delete">🗑 봇 삭제</button>'
        '<p class="result" data-feature-result role="status" aria-live="polite"></p>'
        "</section>"
        "</div>"
    )


def settings_bundles_body(ctx: AccountsContext, *, account: accounts_store.Account, owned_themes: list[Any]) -> str:
    from ...account_support_bundle import MAX_ACTIVE_ACCOUNT_BUNDLES

    options = "".join(
        f'<option value="{escape(_theme_ref(t))}">{escape(t.icon)} {escape(t.label)}</option>' for t in owned_themes
    )
    data_form = (
        '<form class="feature-inline" data-feature-form="data-bundle-create">'
        f'<label class="feature-field">테마<select name="theme" class="pref-select" required>{options}</select></label>'
        '<button type="submit" class="pref-btn pref-btn-accent">⬇ 데이터 번들 만들기</button>'
        '<p class="result" data-feature-result role="status" aria-live="polite"></p>'
        "</form>"
        if options
        else '<p class="hint" data-bundle-empty>내 계정이 소유한 테마가 없어 내보낼 번들이 없습니다.</p>'
    )
    return (
        '<div class="settings-features" data-feature-section="bundles">'
        '<section class="card" aria-label="서포트 번들">'
        "<h2>🩺 서포트 번들 (내 계정 범위)</h2>"
        '<p class="hint">내 테마의 적재 실패 기록, 프로바이더 호출 기록, 계정 상태 요약을 묶습니다. 문서 본문, 비밀, 다른 계정 정보, 서버 진단은 넣지 않습니다. '
        f"6시간 뒤 자동으로 지워지며 동시에 {MAX_ACTIVE_ACCOUNT_BUNDLES}개까지 둘 수 있습니다.</p>"
        '<form class="feature-inline" data-feature-form="support-bundle-create">'
        '<label class="feature-field">수집 기간 (일)<input type="number" name="days" class="pref-input" min="1" max="30" value="1" required/></label>'
        '<label class="feature-field">추적 대상 (선택: 문서 ID·URL·공유 토큰)<input name="target" class="pref-input" maxlength="2048"/></label>'
        '<button type="submit" class="pref-btn pref-btn-accent">🩺 서포트 번들 만들기</button>'
        '<p class="result" data-feature-result role="status" aria-live="polite"></p>'
        "</form>"
        '<div data-feature-list="support-bundles" aria-live="polite"></div>'
        "</section>"
        '<section class="card" aria-label="데이터 번들">'
        "<h2>📦 데이터 번들 (테마 그래프 내보내기)</h2>"
        '<p class="hint">내 테마의 엔티티·관계·문서 메타데이터를 묶습니다. 임베딩과 비밀은 넣지 않습니다. 24시간 뒤 자동으로 지워집니다.</p>'
        f"{data_form}"
        '<div data-feature-list="data-bundles" aria-live="polite"></div>'
        "</section>"
        "</div>"
    )


def settings_backups_body(ctx: AccountsContext, *, account: accounts_store.Account) -> str:
    from ...account_backup import MAX_KEEP, MIN_KEEP, load_policy

    policy = load_policy(_settings(ctx), account_id=account.id)
    schedule_options = "".join(
        f'<option value="{key}"{" selected" if policy.schedule == key else ""}>{escape(text)}</option>' for key, text in SCHEDULE_TEXT.items()
    )
    return (
        '<div class="settings-features" data-feature-section="backups">'
        f"{_reauth_html(ctx, account.id)}"
        '<section class="card" aria-label="계정 백업">'
        "<h2>💾 내 계정 백업</h2>"
        '<p class="hint">내 계정이 소유한 모든 테마의 지식 데이터(문서·엔티티·관계·임베딩·공유 링크 등)를 한 파일로 백업합니다. 비밀번호·API 키·봇 토큰은 넣지 않습니다. '
        "복원은 운영자에게 요청하세요(운영자 전용 메뉴에서만 복원할 수 있습니다).</p>"
        '<button type="button" class="pref-btn pref-btn-accent" data-feature-action="backup-create">💾 지금 백업</button>'
        '<p class="result" data-feature-result role="status" aria-live="polite"></p>'
        '<div data-feature-list="backups" aria-live="polite"></div>'
        "</section>"
        '<form class="card" data-feature-form="backup-policy" aria-label="자동 백업">'
        "<h2>⏱ 자동 백업</h2>"
        '<p class="hint" data-backup-policy-status></p>'
        f'<label class="feature-field">주기<select name="schedule" class="pref-select">{schedule_options}</select></label>'
        f'<label class="feature-field">보존 개수 ({MIN_KEEP}–{MAX_KEEP}, 넘으면 오래된 백업부터 지움)<input type="number" name="keep" class="pref-input" min="{MIN_KEEP}" max="{MAX_KEEP}" value="{policy.keep}" required/></label>'
        '<button type="submit" class="pref-btn">저장</button>'
        '<p class="result" data-feature-result role="status" aria-live="polite"></p>'
        "</form>"
        "</div>"
    )


def admin_backups_body(ctx: AccountsContext, *, principal: Principal) -> str:
    assert principal.account_id is not None
    return (
        '<div class="settings-features" data-feature-section="admin-backups">'
        f"{_reauth_html(ctx, principal.account_id)}"
        '<section class="card" aria-label="계정 백업 복원">'
        "<h2>♻️ 계정 백업 복원 (운영자 전용)</h2>"
        '<p class="hint">계정 소유자가 만든 백업을 그 계정에 되돌립니다. 백업에 있는 테마만 백업 시점으로 바꾸고, 그 뒤에 만든 테마는 그대로 둡니다. '
        "복원 전에 지식 DB 전체 checkpoint를 <code>data/checkpoints/</code>에 만듭니다. 인스턴스 전체 복원은 <code>./cb-manuscript restore</code>를 쓰세요.</p>"
        '<div data-feature-list="admin-backups" aria-live="polite"><p class="hint">목록을 불러오는 중입니다...</p></div>'
        '<p class="result" data-feature-result role="status" aria-live="polite"></p>'
        "</section>"
        "</div>"
    )
