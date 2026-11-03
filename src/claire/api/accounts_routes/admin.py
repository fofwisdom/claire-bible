"""``/admin/accounts`` 싱글 모드 화면과 계정 관리 및 퇴역 USERNAME API (설계 §8.6, §8.9).

관리자(``instance_role == "admin"``, ``owner`` 범위)가 아니면 404다.
수정(PATCH) 및 정보 초기화(POST /api/admin/usernames/release)는 관리자 단계 인증(최근 15분 내 authenticated_at)이 필수다.
다른 계정의 콘텐츠를 읽는 API는 두지 않는다(K5, D5).
"""

from __future__ import annotations

import asyncio
import secrets
import sqlite3
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from ...store import accounts as accounts_store
from ...store import auth_db
from ...store import provider_keys
from ..principal import Principal, request_principal
from .common import (
    AccountsContext,
    escape,
    format_ts,
    json_error,
    not_found,
    open_auth_db,
    open_db,
    render_template,
    require_recent_auth,
)

ADMIN_ACCOUNTS_PATH = "/admin/accounts"
ADMIN_ACCOUNTS_API_PATH = "/api/admin/accounts"
ADMIN_USERNAMES_RETIRED_PATH = "/api/admin/usernames/retired"
ADMIN_USERNAMES_RELEASE_PATH = "/api/admin/usernames/release"

STATUS_TEXT = {
    "setup_required": "설정 필요",
    "active": "활성",
    "suspended": "정지",
    "deleting": "삭제 예약",
    "purged": "삭제됨",
}
ROLE_TEXT = {"admin": "관리자", "member": "멤버"}


def is_admin(principal: Principal) -> bool:
    return principal.instance_role == "admin" and principal.scope == "owner" and principal.account_id is not None


def _theme_counts(conn: sqlite3.Connection) -> dict[int, int]:
    rows = conn.execute(
        "SELECT account_id, COUNT(*) FROM themes WHERE account_id IS NOT NULL GROUP BY account_id"
    ).fetchall()
    return {int(r[0]): int(r[1]) for r in rows}


def account_summary(
    account: accounts_store.Account, theme_count: int, *, has_provider_key: bool | None = None
) -> dict[str, Any]:
    """§8.6 목록 항목 중 P1에서 있는 값. 번호는 JS 정밀도 손실을 막기 위해 문자열이다(S13).

    ``has_provider_key``는 멤버 항목에만 싣는 등록 여부다(§11.2). 키의 fingerprint·상태는 싣지 않는다.
    """
    res = {
        "public_id": str(account.public_id),
        "username": account.username,
        "status": account.status,
        "role": account.role,
        "created_at": account.created_at,
        "activated_at": account.activated_at,
        "theme_count": theme_count,
    }
    if account.delete_after is not None:
        res["delete_after"] = account.delete_after
    if has_provider_key is not None:
        res["has_provider_key"] = has_provider_key
    return res


def list_account_summaries(ctx: AccountsContext) -> list[dict[str, Any]]:
    conn = open_db(ctx)
    auth = open_auth_db(ctx)
    try:
        counts = _theme_counts(conn)
        return [
            account_summary(
                a,
                counts.get(a.id, 0),
                # 관리자는 providers.json을 쓰므로 멤버 항목에만 키 등록 여부를 싣는다(§11.2).
                has_provider_key=provider_keys.has_key(auth, account_id=a.id) if a.role == "member" else None,
            )
            for a in accounts_store.AccountStore(conn).list_accounts()
        ]
    finally:
        auth.close()
        conn.close()


def _admin_card(item: dict[str, Any]) -> str:
    rows = [
        ("번호", item["public_id"]),
        ("USERNAME", item["username"] if item["username"] is not None else "(설정 전)"),
        ("상태", STATUS_TEXT.get(item["status"], item["status"])),
        ("역할", ROLE_TEXT.get(item["role"], item["role"])),
        ("생성", format_ts(item["created_at"])),
        ("활성", format_ts(item["activated_at"])),
        ("테마 수", item["theme_count"]),
    ]
    body = "".join(f"<dt>{escape(k)}</dt><dd>{escape(v)}</dd>" for k, v in rows)
    return f'<section class="card" aria-label="관리자 계정"><h2>관리자 계정</h2><dl>{body}</dl></section>'


def routes(ctx: AccountsContext) -> list[Route]:
    async def admin_accounts_page(request: Request) -> Response:
        if not is_admin(request_principal(request)):
            return not_found()
        items = await asyncio.to_thread(list_account_summaries, ctx)
        admin_items = [i for i in items if i["role"] == "admin"]
        cards = "".join(_admin_card(i) for i in admin_items)
        return render_template("admin_accounts.html", markup={"cards": cards})

    async def admin_accounts_api(request: Request) -> Response:
        if not is_admin(request_principal(request)):
            return not_found()
        items = await asyncio.to_thread(list_account_summaries, ctx)
        return JSONResponse({"accounts": items})

    async def admin_accounts_patch(request: Request) -> Response:
        principal = request_principal(request)
        if not is_admin(principal):
            return not_found()
        auth_denied = require_recent_auth(principal)
        if auth_denied is not None:
            return auth_denied

        try:
            body = await request.json()
        except Exception:
            return json_error(400, "invalid_json")

        if not isinstance(body, dict):
            return json_error(400, "invalid_request")

        target_id = body.get("account_id")
        username = body.get("username")
        public_id = body.get("public_id")
        action = str(body.get("action") or "").lower().strip()
        status = str(body.get("status") or "").lower().strip()
        mode = str(body.get("mode") or "").lower().strip()
        immediate = bool(body.get("immediate", False))
        cancel = bool(body.get("cancel", False))

        conn = open_db(ctx)
        auth_conn = open_auth_db(ctx)
        try:
            store = accounts_store.AccountStore(conn)
            target = None
            if target_id is not None:
                try:
                    target = store.get(int(target_id))
                except (accounts_store.AccountNotFound, ValueError, TypeError):
                    return json_error(404, "account_not_found")
            elif username is not None:
                target = store.find_by_username(str(username).strip())
            elif public_id is not None:
                try:
                    row = conn.execute("SELECT id FROM accounts WHERE public_id=?", (int(public_id),)).fetchone()
                    if row is not None:
                        target = store.get(int(row[0]))
                except Exception:
                    pass

            if target is None:
                return json_error(404, "account_not_found")

            if target.role == "admin" and (action or status):
                return json_error(400, "cannot_modify_admin")

            if action == "suspend" or status == "suspended":
                try:
                    updated = store.suspend(target.id)
                except accounts_store.AccountStateError as exc:
                    return json_error(400, "invalid_state_transition", detail=str(exc))
                auth_db.record_admin_audit(
                    auth_conn,
                    actor_account_id=principal.account_id,
                    action="account.suspend",
                    target_account_id=target.id,
                )
                counts = _theme_counts(conn)
                return JSONResponse({"ok": True, "account": account_summary(updated, counts.get(updated.id, 0))})

            if action == "resume" or (status == "active" and target.status == "suspended"):
                try:
                    updated = store.resume(target.id)
                except accounts_store.AccountStateError as exc:
                    return json_error(400, "invalid_state_transition", detail=str(exc))
                auth_db.record_admin_audit(
                    auth_conn,
                    actor_account_id=principal.account_id,
                    action="account.resume",
                    target_account_id=target.id,
                )
                counts = _theme_counts(conn)
                return JSONResponse({"ok": True, "account": account_summary(updated, counts.get(updated.id, 0))})

            if action in ("delete", "schedule_delete", "cancel_delete", "purge") or status in ("deleting", "purged"):
                if action == "cancel_delete" or cancel or mode == "cancel" or (action == "delete" and status == "active"):
                    try:
                        updated = store.cancel_delete(target.id)
                    except accounts_store.AccountStateError as exc:
                        return json_error(400, "invalid_state_transition", detail=str(exc))
                    auth_db.record_admin_audit(
                        auth_conn,
                        actor_account_id=principal.account_id,
                        action="account.cancel_delete",
                        target_account_id=target.id,
                    )
                    counts = _theme_counts(conn)
                    return JSONResponse({"ok": True, "account": account_summary(updated, counts.get(updated.id, 0))})

                if action == "purge" or mode in ("immediate", "purge") or immediate or status == "purged":
                    data_dir = getattr(ctx.config, "data_dir", None)
                    try:
                        updated = store.purge(target.id, data_dir=data_dir)
                    except accounts_store.AccountStateError as exc:
                        return json_error(400, "invalid_state_transition", detail=str(exc))
                    auth_db.record_admin_audit(
                        auth_conn,
                        actor_account_id=principal.account_id,
                        action="account.purge",
                        target_account_id=target.id,
                    )
                    return JSONResponse({"ok": True, "account": account_summary(updated, 0)})

                delete_after = body.get("delete_after")
                try:
                    updated = store.schedule_delete(target.id, delete_after=delete_after)
                except accounts_store.AccountStateError as exc:
                    return json_error(400, "invalid_state_transition", detail=str(exc))
                auth_db.record_admin_audit(
                    auth_conn,
                    actor_account_id=principal.account_id,
                    action="account.schedule_delete",
                    target_account_id=target.id,
                )
                counts = _theme_counts(conn)
                return JSONResponse({"ok": True, "account": account_summary(updated, counts.get(updated.id, 0))})

            if action in ("reset_password", "reset_password_link"):
                reset_token = secrets.token_urlsafe(32)
                reset_link = f"/reset-password?token={reset_token}"
                auth_db.record_admin_audit(
                    auth_conn,
                    actor_account_id=principal.account_id,
                    action="account.reset_password_link",
                    target_account_id=target.id,
                )
                return JSONResponse({
                    "ok": True,
                    "account_id": target.id,
                    "reset_token": reset_token,
                    "reset_link": reset_link,
                })

            return json_error(400, "unknown_action")
        finally:
            auth_conn.close()
            conn.close()

    async def admin_retired_usernames_api(request: Request) -> Response:
        principal = request_principal(request)
        if not is_admin(principal):
            return not_found()
        conn = open_db(ctx)
        try:
            store = accounts_store.AccountStore(conn)
            retired = store.list_retired_usernames()
            return JSONResponse({"retired": retired, "usernames": retired})
        finally:
            conn.close()

    async def admin_release_username_api(request: Request) -> Response:
        principal = request_principal(request)
        if not is_admin(principal):
            return not_found()
        auth_denied = require_recent_auth(principal)
        if auth_denied is not None:
            return auth_denied

        body: dict[str, Any] = {}
        try:
            body = await request.json()
        except Exception:
            pass

        raw_username = body.get("username") or request.query_params.get("username")
        if not raw_username or not isinstance(raw_username, str):
            return json_error(400, "username_required")

        username = raw_username.strip().lower()
        apply_val = body.get("apply", request.query_params.get("apply", False))
        apply = apply_val is True or str(apply_val).lower() == "true"

        conn = open_db(ctx)
        auth_conn = open_auth_db(ctx)
        try:
            store = accounts_store.AccountStore(conn)
            if not apply:
                try:
                    preview = store.preview_release_username(username, auth_conn=auth_conn)
                    return JSONResponse({"ok": True, "apply": False, "preview": preview, **preview})
                except LookupError:
                    return json_error(404, "username_not_retired")

            try:
                success = store.release_username(
                    username,
                    released_by=principal.account_id,
                    auth_conn=auth_conn,
                )
                if not success:
                    return json_error(404, "username_not_retired")
                auth_db.record_admin_audit(
                    auth_conn,
                    actor_account_id=principal.account_id,
                    action="username.release",
                    detail={"released": True},
                )
                return JSONResponse({"ok": True, "apply": True, "released": True, "username": username})
            except LookupError:
                return json_error(404, "username_not_retired")
        finally:
            auth_conn.close()
            conn.close()

    return [
        Route(ADMIN_ACCOUNTS_PATH, admin_accounts_page, methods=["GET"]),
        Route(ADMIN_ACCOUNTS_API_PATH, admin_accounts_api, methods=["GET"]),
        Route(ADMIN_ACCOUNTS_API_PATH, admin_accounts_patch, methods=["PATCH"]),
        Route(ADMIN_USERNAMES_RETIRED_PATH, admin_retired_usernames_api, methods=["GET"]),
        Route(ADMIN_USERNAMES_RELEASE_PATH, admin_release_username_api, methods=["POST"]),
    ]
