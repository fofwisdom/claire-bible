"""유저 단위 기능 라우트: 텔레그램 봇, 서포트 번들, 데이터 번들, 백업.

계정 소유자(``scope == "owner"``)가 WebUI(``/account/telegram``·``/support/bundles``·``/account/backups``)에서 자기 계정의 기능을 구성·제어한다.
복원만 운영자(관리자) 전용이다(``/admin/backups``, ``POST /api/admin/account-backups/restore``).

| 경로 | 메서드 | 기능 |
|------|--------|------|
| ``/api/me/telegram`` | GET, PUT, PATCH, DELETE | 내 봇 조회·등록(토큰 검증)·켜기/끄기·허용 사용자 변경·삭제 |
| ``/api/support/bundles`` | GET, POST | 서포트 번들 목록·생성(``scope`` ``account``: 모든 계정, ``server``: 관리자). 노트·문서 링크(``target``)로 추적 대상을 정한다 |
| ``/api/support/bundles/{bundle_id}`` | DELETE | 서포트 번들 삭제 |
| ``/api/me/data-bundles`` | GET, POST, DELETE | 내 테마 데이터(그래프) 번들 목록·생성·삭제 |
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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Route

from ...core.secret_scrub import register_secret, scrub_text
from ...store import accounts as accounts_store
from ...store import auth_db
from ...store import telegram_bots
from ..principal import Principal, request_principal
from ..security import read_json_body
from ...i18n import t
from .common import (
    AccountsContext,
    escape,
    format_ts,
    json_error,
    not_found,
    open_auth_db,
    open_db,
    require_recent_auth,
    t_code,
)
from .console_ui import (
    console_actions,
    console_card,
    console_field,
    console_form_card,
    console_kv,
    console_result,
    console_toggle_row,
)

ME_TELEGRAM_PATH = "/api/me/telegram"
# The bundles of the page /support/bundles: support bundles of the account (each account) and of the server (administrator), and data bundles of the knowledge bases of the account.
SUPPORT_BUNDLES_API_PATH = "/api/support/bundles"
ME_DATA_BUNDLES_PATH = "/api/me/data-bundles"
ME_BACKUPS_PATH = "/api/me/backups"
ME_BACKUPS_DOWNLOAD_PATH = "/api/me/backups/download"
ME_BACKUPS_POLICY_PATH = "/api/me/backups/policy"
ADMIN_ACCOUNT_BACKUPS_PATH = "/api/admin/account-backups"
ADMIN_ACCOUNT_BACKUPS_RESTORE_PATH = "/api/admin/account-backups/restore"

NO_STORE = {"Cache-Control": "no-store"}
# The text of each value is the catalog key ``console.telegram.status.<value>`` or ``console.backups.schedule.<value>``.
BOT_STATUSES = ("pending", "running", "error", "disabled")
BACKUP_SCHEDULES = ("off", "daily", "weekly")


# --- 공용 -----------------------------------------------------------------------------


def _settings(ctx: AccountsContext) -> Any:
    current = ctx.current_settings()
    if current is not None:
        return current
    from ...core.config import get_settings

    return get_settings()


def _owner(request: Request) -> tuple[Principal, Response | None]:
    principal = request_principal(request)
    if principal.account_id is None or principal.scope != "owner":
        return principal, not_found()
    return principal, None


def _is_admin(principal: Principal) -> bool:
    return principal.account_id is not None and principal.server_role == "admin" and principal.scope == "owner"


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
    from ...ops.account_backup import delete_all_account_backups
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


def _server_bot_id(ctx: AccountsContext) -> int | None:
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
    reserved = [i for i in (_server_bot_id(ctx),) if i]
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
    from ...store.knowledge_base import KnowledgeBaseNotFound, get_kb_manager

    manager = get_kb_manager(_settings(ctx))
    try:
        theme = manager.get_kb_by_reference(raw)
    except (KnowledgeBaseNotFound, ValueError, KeyError):
        return None
    return theme if int(getattr(theme, "account_id", -1)) == int(account_id) else None


def _owned_themes(ctx: AccountsContext, account_id: int) -> list[Any]:
    from ...store.knowledge_base import get_kb_manager

    return list(get_kb_manager(_settings(ctx)).list_account_kbs(account_id))


def _knowledge_bases_by_id(ctx: AccountsContext, kb_ids: list[int]) -> list[Any]:
    """The knowledge bases with these IDs that still exist. A deleted knowledge base is not in the result."""
    from ...store.knowledge_base import KnowledgeBaseNotFound, get_kb_manager

    manager = get_kb_manager(_settings(ctx))
    found = []
    for kb_id in kb_ids:
        try:
            found.append(manager.get_kb(kb_id))
        except KnowledgeBaseNotFound:
            continue
    return found


def _bundle_item(record: dict[str, Any]) -> dict[str, Any]:
    """One support bundle of the list: its link (the token address) and its facts. No download address: the page gives the link only."""
    return {
        "bundle_id": record["bundle_id"],
        "link": record.get("download_url"),
        "days_covered": record.get("days_covered"),
        "target_link": record.get("target_link"),
        "size_bytes": record.get("size_bytes"),
        "created_at": record.get("created_at"),
        "expires_at": record.get("expires_at"),
    }


def _theme_ref(theme: Any) -> str:
    from ...core import ids

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
        if bot_id == _server_bot_id(ctx):
            return json_error(409, "bot_in_use")
        from ...telegram.api import TelegramVerifyRejected, TelegramVerifyUnavailable, verify_bot_token

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

    # ---- 서포트 번들 (/support/bundles) ----
    async def support_list(request: Request) -> Response:
        principal, denied = _owner(request)
        if denied is not None:
            return denied
        from ...ops.account_support_bundle import MAX_ACTIVE_ACCOUNT_BUNDLES, list_account_support_bundles, list_server_support_bundles

        s = _settings(ctx)

        def _list() -> list[dict[str, Any]]:
            items = [{**_bundle_item(r), "scope": "account"} for r in list_account_support_bundles(s, account_id=principal.account_id)]
            if _is_admin(principal):
                items.extend({**_bundle_item(r), "scope": "server"} for r in list_server_support_bundles(s))
            items.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
            return items

        items = await asyncio.to_thread(_list)
        return JSONResponse({"bundles": items, "max_active": MAX_ACTIVE_ACCOUNT_BUNDLES, "scopes": ["account", "server"] if _is_admin(principal) else ["account"]}, headers=NO_STORE)

    async def support_create(request: Request) -> Response:
        principal, denied = _owner(request)
        if denied is not None:
            return denied
        body = await read_json_body(request)
        if set(body) - {"scope", "days", "target"}:
            return json_error(400, "invalid_request", fields=["scope", "days", "target"])
        # The scope is required (no default): "account" for every account, "server" for the administrator only.
        scope = body.get("scope")
        if scope not in ("account", "server"):
            return json_error(400, "invalid_scope")
        if scope == "server" and not _is_admin(principal):
            return not_found()
        days = body.get("days", 1)
        if not isinstance(days, int) or isinstance(days, bool):
            return json_error(400, "invalid_days")
        target = body.get("target")
        if target is not None and (not isinstance(target, str) or len(target) > 2048):
            return json_error(400, "invalid_target")
        clean_target = (target or "").strip() or None
        from ...ops.account_support_bundle import AccountSupportBundleError, SupportTargetNotFound, create_account_support_bundle
        from ...ops.support_bundle import create_support_bundle, validate_bundle_days

        s = _settings(ctx)

        def _create() -> dict[str, Any]:
            if scope == "server":
                validate_bundle_days(days, getattr(s, "telemetry_retention_days", 30))
                info = create_support_bundle(s, days=days, target=clean_target, request_context={"channel": "webui"})
            else:
                info = create_account_support_bundle(s, account_id=principal.account_id, days=days, target=clean_target, request_context={"channel": "webui"})
            return {
                "bundle_id": info.bundle_id,
                "scope": scope,
                "link": info.download_url,
                "days_covered": info.days_covered,
                "size_bytes": info.size_bytes,
                "target_kind": info.target_kind,
                "target_link": info.target_link,
                "target_resolution_status": info.target_resolution_status,
                "expires_at": datetime.fromtimestamp(info.expires_at, timezone.utc).isoformat(),
            }

        try:
            created = await asyncio.to_thread(_create)
        except SupportTargetNotFound as exc:
            return json_error(404, "target_not_found", message=str(exc))
        except AccountSupportBundleError as exc:
            return json_error(409, "bundle_limit_reached", message=str(exc))
        except ValueError as exc:
            return json_error(400, "invalid_days", message=str(exc))
        return JSONResponse({"bundle": created}, headers=NO_STORE)

    async def support_delete(request: Request) -> Response:
        principal, denied = _owner(request)
        if denied is not None:
            return denied
        bundle_id = str(request.path_params.get("bundle_id", ""))
        from ...ops.account_support_bundle import delete_account_support_bundle, delete_server_support_bundle

        s = _settings(ctx)

        def _delete() -> bool:
            if delete_account_support_bundle(s, account_id=principal.account_id, bundle_id=bundle_id):
                return True
            return _is_admin(principal) and delete_server_support_bundle(s, bundle_id=bundle_id)

        if not await asyncio.to_thread(_delete):
            return json_error(404, "bundle_not_found")
        return JSONResponse({"ok": True})

    # ---- 데이터 번들 ----
    async def data_list(request: Request) -> Response:
        principal, denied = _owner(request)
        if denied is not None:
            return denied
        from ...ops.graph_bundle import list_account_graph_bundles

        s = _settings(ctx)
        items = await asyncio.to_thread(lambda: list_account_graph_bundles(s.data_dir, principal.account_id, public_url=str(s.public_url or "")))
        themes = await asyncio.to_thread(_owned_themes, ctx, principal.account_id)
        listed = list(themes)
        owned_ids = {t.id for t in themes}
        # A moderator also has bundles of shared knowledge bases (shared knowledge base design Section 13.2): the list gives their labels too, so the page never shows an internal knowledge base ID.
        bundle_kb_ids = {int(b["kb_id"]) for b in items if b.get("kb_id") is not None} - owned_ids
        if bundle_kb_ids:
            listed.extend(await asyncio.to_thread(_knowledge_bases_by_id, ctx, sorted(bundle_kb_ids)))
        return JSONResponse(
            {
                "bundles": items,
                "themes": [{"theme": _theme_ref(t), "kb_id": t.id, "label": t.label, "icon": t.icon} for t in listed],
            },
            headers=NO_STORE,
        )

    async def data_create(request: Request) -> Response:
        principal, denied = _owner(request)
        if denied is not None:
            return denied
        body = await read_json_body(request)
        if set(body) - {"kb"}:
            return json_error(400, "invalid_request", fields=["kb"])
        # Design Section 5.2 rule 2: the parameter ``kb`` (number) is required; there is no default.
        if body.get("kb") is None or not str(body.get("kb")).strip():
            return json_error(400, "kb_required")
        theme = await asyncio.to_thread(_owned_theme, ctx, principal.account_id, body.get("kb"))
        if theme is None:
            return json_error(404, "theme_not_found")
        from ...ops.graph_bundle import create_graph_bundle

        def _create() -> dict[str, Any]:
            info = create_graph_bundle(
                _settings(ctx), kb_id=theme.id, request_context={"channel": "webui"}, account_id=principal.account_id
            )
            return {
                "bundle_id": info.bundle_id,
                "filename": info.filename,
                "theme": _theme_ref(theme),
                "entities_count": info.entities_count,
                "relations_count": info.relations_count,
                "documents_count": info.documents_count,
                "size_bytes": info.size_bytes,
                "expires_at": datetime.fromtimestamp(info.expires_at, timezone.utc).isoformat(),
                "download_url": info.download_url,
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
        from ...ops.graph_bundle import delete_account_graph_bundle

        ok = await asyncio.to_thread(
            delete_account_graph_bundle, _settings(ctx).data_dir, principal.account_id, request.query_params.get("bundle_id", "")
        )
        if not ok:
            return json_error(404, "bundle_not_found")
        return JSONResponse({"ok": True})

    # ---- 백업 ----
    async def backups_list(request: Request) -> Response:
        principal, denied = _owner(request)
        if denied is not None:
            return denied
        from ...ops.account_backup import list_account_backups, load_policy

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
        from ...ops.account_backup import AccountBackupError, create_account_backup

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
        from ...ops.account_backup import delete_account_backup

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
        from ...ops.account_backup import find_account_backup

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
        from ...ops.account_backup import save_policy

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
        from ...ops.account_backup import list_backups_for_admin

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
        from ...ops.account_backup import AccountBackupConflict, AccountBackupError, restore_account_backup

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
        Route(SUPPORT_BUNDLES_API_PATH, support_list, methods=["GET", "HEAD"]),
        Route(SUPPORT_BUNDLES_API_PATH, support_create, methods=["POST"]),
        Route(SUPPORT_BUNDLES_API_PATH + "/{bundle_id}", support_delete, methods=["DELETE"]),
        Route(ME_DATA_BUNDLES_PATH, data_list, methods=["GET", "HEAD"]),
        Route(ME_DATA_BUNDLES_PATH, data_create, methods=["POST"]),
        Route(ME_DATA_BUNDLES_PATH, data_delete, methods=["DELETE"]),
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
        return console_card(
            escape(t("console.reauth.submit")),
            "",
            icon="lock",
            hint_html=escape(t("console.reauth.feature_no_password_hint")),
            extra_class="card",
            aria_label=t("console.reauth.submit"),
            attrs={"data-feature-reauth": "none"},
        )
    body = (
        console_field(
            t("console.settings.account.current_password"),
            '<input name="password" type="password" class="pref-input" required autocomplete="current-password"/>',
            extra_class="feature-field",
        )
        + console_actions(f'<button type="submit" class="pref-btn pref-btn-accent">{escape(t("console.reauth.submit"))}</button>')
        + console_result(attrs={"data-feature-result": True})
    )
    return console_form_card(
        escape(t("console.reauth.form_title")),
        body,
        icon="lock",
        hint_html=escape(t("console.reauth.feature_hint")),
        extra_class="card",
        aria_label=t("console.reauth.submit"),
        attrs={"data-feature-form": "reauth"},
    )


def settings_telegram_body(ctx: AccountsContext, *, account: accounts_store.Account) -> str:
    state = telegram_state(ctx, account.id)
    bot = state["bot"]
    if bot is None:
        status_html = f'<p class="hint" data-telegram-empty>{escape(t("console.telegram.empty"))}</p>'
        allowed_value = ""
        enabled_checked = False
        manage_disabled = True
    else:
        rows = [
            (t("console.telegram.field.bot"), f"@{bot['bot_username']}" if bot["bot_username"] else str(bot["bot_id"])),
            ("fingerprint", str(bot["fingerprint"])),
            (t("console.common.status"), t(f"console.telegram.status.{bot['status']}") if bot["status"] in BOT_STATUSES else str(bot["status"])),
            (t("console.telegram.field.last_error"), bot["last_error"] or "-"),
            (t("console.telegram.field.status_at"), format_ts(bot["status_at"])),
            (t("console.telegram.field.allowed_count"), str(len(bot["allowed_user_ids"]))),
        ]
        status_html = console_kv(rows, attrs={"data-telegram-meta": True})
        allowed_value = ", ".join(str(i) for i in bot["allowed_user_ids"])
        enabled_checked = bool(bot["enabled"])
        manage_disabled = False
    state_card = console_card(
        escape(t("console.telegram.state_title")),
        f'<div data-telegram-status>{status_html}</div>'
        + console_toggle_row(
            t("console.telegram.enable"),
            "pref-telegram-enabled",
            desc_html=escape(t("console.telegram.enable_desc")),
            checked=enabled_checked,
            disabled=manage_disabled,
            attrs={"data-feature-toggle": "telegram-enabled", "aria-label": t("console.telegram.enable_label")},
        )
        + console_result(attrs={"data-feature-result": True}),
        icon="bot",
        hint_html=escape(t("console.telegram.state_hint")),
        extra_class="card",
        aria_label=t("console.telegram.state_label"),
    )
    register_body = (
        console_field(
            t("console.telegram.token"),
            '<input name="bot_token" type="password" class="pref-input" required autocomplete="off" spellcheck="false" placeholder="123456789:AA..."/>',
            extra_class="feature-field",
        )
        + console_field(
            t("console.telegram.allowed_ids_max", max=telegram_bots.MAX_ALLOWED_USERS),
            f'<input name="allowed_user_ids" class="pref-input" required value="{escape(allowed_value)}" placeholder="12345678"/>',
            extra_class="feature-field",
        )
        + console_actions(f'<button type="submit" class="pref-btn pref-btn-accent">{escape(t("console.telegram.register_submit"))}</button>')
        + console_result(attrs={"data-feature-result": True})
    )
    register_card = console_form_card(
        escape(t("console.telegram.register_title")),
        register_body,
        icon="plus",
        extra_class="card",
        aria_label=t("console.telegram.register_label"),
        attrs={"data-feature-form": "telegram-register"},
    )
    allowed_body = (
        console_field(
            t("console.telegram.allowed_ids"),
            f'<input name="allowed_user_ids" class="pref-input" required value="{escape(allowed_value)}"/>',
            extra_class="feature-field",
        )
        + console_actions(f'<button type="submit" class="pref-btn">{escape(t("console.telegram.allowed_submit"))}</button>')
        + console_result(attrs={"data-feature-result": True})
    )
    allowed_card = console_form_card(
        escape(t("console.telegram.allowed_title")),
        allowed_body,
        icon="users",
        extra_class="card",
        aria_label=t("console.telegram.allowed_title"),
        attrs={"data-feature-form": "telegram-allowed", "hidden": bot is None},
    )
    delete_card = console_card(
        escape(t("console.telegram.delete_title")),
        console_actions(f'<button type="button" class="pref-btn pref-btn-danger" data-feature-action="telegram-delete"><span data-icon="trash"></span> {escape(t("console.telegram.delete_title"))}</button>')
        + console_result(attrs={"data-feature-result": True}),
        icon="trash",
        hint_html=escape(t("console.telegram.delete_hint")),
        extra_class="card",
        aria_label=t("console.telegram.delete_title"),
        attrs={"hidden": bot is None},
    )
    return '<div class="settings-features" data-feature-section="telegram">' + _reauth_html(ctx, account.id) + state_card + register_card + allowed_card + delete_card + "</div>"


def settings_bundles_body(ctx: AccountsContext, *, account: accounts_store.Account, owned_themes: list[Any], admin_viewer: bool) -> str:
    """The body of ``/support/bundles``: support bundles (each account; the administrator also chooses the server scope) and data bundles. Each bundle gives a link to copy, not a download."""
    from ...ops.account_support_bundle import MAX_ACTIVE_ACCOUNT_BUNDLES

    options = "".join(
        f'<option value="{escape(_theme_ref(t))}">{escape(t.icon)} {escape(t.label)}</option>' for t in owned_themes
    )
    data_form = (
        '<form class="feature-inline" data-feature-form="data-bundle-create">'
        + console_field(t("console.common.theme"), f'<select name="theme" class="pref-select" required>{options}</select>', extra_class="feature-field")
        + f'<button type="submit" class="pref-btn pref-btn-accent"><span data-icon="package"></span> {escape(t("console.bundles.data.submit"))}</button>'
        + console_result(attrs={"data-feature-result": True})
        + '<div data-bundle-created="data" aria-live="polite"></div>'
        + "</form>"
        if options
        else f'<p class="hint" data-bundle-empty>{escape(t("console.bundles.data.empty_themes"))}</p>'
    )
    scope_field = (
        console_field(
            t("console.bundles.support.scope"),
            '<select name="scope" class="pref-select">'
            f'<option value="account">{escape(t("console.bundles.support.scope_account"))}</option>'
            f'<option value="server">{escape(t("console.bundles.support.scope_server"))}</option>'
            "</select>",
            extra_class="feature-field",
        )
        if admin_viewer
        else '<input type="hidden" name="scope" value="account"/>'
    )
    support_form = (
        '<form class="feature-inline" data-feature-form="support-bundle-create">'
        + console_field(
            t("console.bundles.support.target"),
            f'<input name="target" type="text" class="pref-input" maxlength="2048" inputmode="url" placeholder="{escape(t("console.bundles.support.target_placeholder"))}"/>',
            extra_class="feature-field",
        )
        + console_field(t("console.bundles.support.days"), '<input type="number" name="days" class="pref-input" min="1" max="30" value="1" required/>', extra_class="feature-field")
        + scope_field
        + f'<button type="submit" class="pref-btn pref-btn-accent"><span data-icon="pulse"></span> {escape(t("console.bundles.support.submit"))}</button>'
        + console_result(attrs={"data-feature-result": True})
        + '<div data-bundle-created="support" aria-live="polite"></div>'
        + "</form>"
    )
    hint_key = "console.bundles.support.hint_admin" if admin_viewer else "console.bundles.support.hint"
    support_card = console_card(
        escape(t("console.bundles.support.title")),
        support_form + '<div data-feature-list="support-bundles" aria-live="polite"></div>',
        icon="pulse",
        hint_html=escape(t(hint_key, max=MAX_ACTIVE_ACCOUNT_BUNDLES)),
        extra_class="card",
        aria_label=t("console.bundles.support.label"),
        id="support-bundles-card",
    )
    data_card = console_card(
        escape(t("console.bundles.data.title")),
        data_form + '<div data-feature-list="data-bundles" aria-live="polite"></div>',
        icon="package",
        hint_html=escape(t("console.bundles.data.hint")),
        extra_class="card",
        aria_label=t("console.bundles.data.label"),
        id="data-bundles-card",
    )
    return '<div class="settings-features" data-feature-section="bundles">' + support_card + data_card + "</div>"


def settings_backups_body(ctx: AccountsContext, *, account: accounts_store.Account) -> str:
    from ...ops.account_backup import MAX_KEEP, MIN_KEEP, load_policy

    policy = load_policy(_settings(ctx), account_id=account.id)
    schedule_options = "".join(
        f'<option value="{key}"{" selected" if policy.schedule == key else ""}>{escape(t(f"console.backups.schedule.{key}"))}</option>' for key in BACKUP_SCHEDULES
    )
    backup_card = console_card(
        escape(t("console.backups.title")),
        console_actions(f'<button type="button" class="pref-btn pref-btn-accent" data-feature-action="backup-create"><span data-icon="save"></span> {escape(t("console.backups.create"))}</button>')
        + console_result(attrs={"data-feature-result": True})
        + '<div data-feature-list="backups" aria-live="polite"></div>',
        icon="save",
        hint_html=escape(t("console.backups.hint")),
        extra_class="card",
        aria_label=t("console.backups.label"),
    )
    policy_body = (
        '<p class="hint" data-backup-policy-status></p>'
        + console_field(t("console.backups.schedule"), f'<select name="schedule" class="pref-select">{schedule_options}</select>', extra_class="feature-field")
        + console_field(t("console.backups.keep", min=MIN_KEEP, max=MAX_KEEP), f'<input type="number" name="keep" class="pref-input" min="{MIN_KEEP}" max="{MAX_KEEP}" value="{policy.keep}" required/>', extra_class="feature-field")
        + console_actions(f'<button type="submit" class="pref-btn">{escape(t("common.action.save"))}</button>')
        + console_result(attrs={"data-feature-result": True})
    )
    policy_card = console_form_card(
        escape(t("console.backups.policy_title")),
        policy_body,
        icon="clock",
        extra_class="card",
        aria_label=t("console.backups.policy_title"),
        attrs={"data-feature-form": "backup-policy"},
    )
    return '<div class="settings-features" data-feature-section="backups">' + _reauth_html(ctx, account.id) + backup_card + policy_card + "</div>"


def admin_backups_body(ctx: AccountsContext, *, principal: Principal) -> str:
    assert principal.account_id is not None
    restore_card = console_card(
        escape(t("console.backups.restore_title")),
        f'<div data-feature-list="admin-backups" aria-live="polite"><p class="hint">{escape(t("console.common.loading_list"))}</p></div>'
        + console_result(attrs={"data-feature-result": True}),
        icon="restore",
        hint_html=t_code("console.backups.restore_hint", checkpoints="data/checkpoints/", command="./cb-manuscript restore"),
        extra_class="card",
        aria_label=t("console.backups.restore_label"),
    )
    return '<div class="settings-features" data-feature-section="admin-backups">' + _reauth_html(ctx, principal.account_id) + restore_card + "</div>"
