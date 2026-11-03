"""``/me`` 최소판 (설계 §8.7): 본인 계정 조회, 표시 이름 변경, 비밀번호 변경.

비밀번호 변경은 단계 인증(`common.require_recent_auth`)과 현재 비밀번호를 함께 요구한다. 비밀번호가 아직 없으면(설정 중 저장 실패, §8.4 복구) 단계 인증만 요구한다.

비밀번호를 바꾸면 그 계정의 다른 세션(부트스트랩 링크 포함)을 모두 지우고 지금 세션만 남긴다. 비밀번호가 새어 누군가 세션을 얻었다면 비밀번호를 바꾸는 순간 끊어야 하기 때문이다. API·MCP 토큰과 OAuth 토큰은 비밀번호에서 나온 자격증명이 아니므로 그대로 둔다(사용자가 ``/tokens``에서 따로 관리한다).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Literal

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from ...store import accounts as accounts_store
from ...store import auth_db
from ..principal import Principal, request_principal
from ..security import read_json_body
from . import keys as keys_routes
from .admin import ROLE_TEXT, STATUS_TEXT
from .common import (
    AccountsContext,
    client_ip,
    escape,
    json_error,
    open_auth_db,
    open_db,
    render_template,
    request_session_token,
    require_recent_auth,
    retry_after_seconds,
    validate_display_name,
)
from .setup import PASSWORD_REASON_TEXT

ME_PATH = "/me"
ME_API_PATH = "/api/me"
ME_PASSWORD_PATH = "/api/me/password"

NOTICE_TEXT = {
    "password_not_saved": "계정 설정은 확정됐지만 비밀번호를 저장하지 못했습니다. 아래에서 비밀번호를 설정하세요.",
}


def me_payload(ctx: AccountsContext, principal: Principal) -> dict[str, Any]:
    if principal.account_id is None:
        raise RuntimeError("/me requires an account principal")
    conn = open_db(ctx)
    auth = open_auth_db(ctx)
    try:
        account = accounts_store.AccountStore(conn).get(principal.account_id)
        has_pw = auth_db.has_password(auth, account.id)
    finally:
        auth.close()
        conn.close()
    return {
        "public_id": str(account.public_id),
        "username": account.username,
        "display_name": account.display_name,
        "role": account.role,
        "scope": principal.scope,
        "status": account.status,
        "has_password": has_pw,
    }


def update_display_name(ctx: AccountsContext, account_id: int, display_name: str) -> None:
    conn = open_db(ctx)
    try:
        accounts_store.AccountStore(conn).update_display_name(account_id, display_name)
    finally:
        conn.close()


@dataclass(frozen=True, slots=True)
class PasswordChangeOutcome:
    kind: Literal["ok", "throttled", "invalid_password", "rejected", "not_active"]
    reason: str | None = None
    retry_after: float | None = None
    other_sessions_revoked: int = 0


def perform_password_change(
    ctx: AccountsContext,
    *,
    account_id: int,
    keep_token: str | None,
    current_password: str | None,
    new_password: str,
    ip: str,
) -> PasswordChangeOutcome:
    conn = open_db(ctx)
    auth = open_auth_db(ctx)
    try:
        account = accounts_store.AccountStore(conn).get(account_id)
        if account.status != "active":
            return PasswordChangeOutcome("not_active")
        key_name = account.username if account.username is not None else f"#account:{account.id}"
        if auth_db.has_password(auth, account_id):
            decision = auth_db.begin_login_attempt(auth, username=key_name, ip=ip)
            if not decision.allowed:
                return PasswordChangeOutcome("throttled", retry_after=decision.retry_after)
            if current_password is None or not auth_db.verify_password(auth, account_id, current_password):
                return PasswordChangeOutcome("invalid_password")
            auth_db.settle_login_success(auth, username=key_name, ip=ip)
        try:
            auth_db.set_password(auth, account_id, new_password, username=account.username)
        except auth_db.PasswordRejected as exc:
            return PasswordChangeOutcome("rejected", reason=exc.reason)
        revoked = accounts_store.revoke_other_sessions(conn, account_id, keep_token=keep_token)
        auth_db.record_admin_audit(
            auth,
            actor_account_id=account_id,
            action="account.password_change",
            target_account_id=account_id,
            detail={"other_sessions_revoked": revoked},
        )
        return PasswordChangeOutcome("ok", other_sessions_revoked=revoked)
    finally:
        auth.close()
        conn.close()


def _me_page(payload: dict[str, Any], notice: str | None, keys_html: str = "") -> Response:
    notice_text = NOTICE_TEXT.get(notice or "")
    notice_html = f'<p class="notice" role="status">{escape(notice_text)}</p>' if notice_text else ""
    return render_template(
        "me.html",
        text={
            "public_id": payload["public_id"],
            "username": payload["username"] if payload["username"] is not None else "(설정 전)",
            "display_name": payload["display_name"],
            "role": ROLE_TEXT.get(payload["role"], payload["role"]),
            "status": STATUS_TEXT.get(payload["status"], payload["status"]),
            "password_state": "설정됨" if payload["has_password"] else "설정 안 됨",
            "has_password": "1" if payload["has_password"] else "0",
            "min_password_length": auth_db.MIN_PASSWORD_LENGTH,
        },
        markup={"notice": notice_html, "provider_keys": keys_html},
    )


def routes(ctx: AccountsContext) -> list[Route]:
    async def me_page(request: Request) -> Response:
        principal = request_principal(request)
        payload = await asyncio.to_thread(me_payload, ctx, principal)
        keys_html = await asyncio.to_thread(keys_routes.keys_section_html, ctx, principal)
        return _me_page(payload, request.query_params.get("notice"), keys_html)

    async def me_get(request: Request) -> Response:
        return JSONResponse(await asyncio.to_thread(me_payload, ctx, request_principal(request)))

    async def me_patch(request: Request) -> Response:
        principal = request_principal(request)
        body = await read_json_body(request)
        if set(body) != {"display_name"}:
            # 지금 바꿀 수 있는 항목은 표시 이름뿐이다. 모르는 키를 조용히 버리지 않는다(C1).
            return json_error(400, "invalid_request", fields=["display_name"])
        display_name = validate_display_name(body["display_name"])
        if display_name is None:
            return json_error(400, "invalid_display_name")
        if principal.account_id is None:
            raise RuntimeError("/api/me requires an account principal")
        await asyncio.to_thread(update_display_name, ctx, principal.account_id, display_name)
        return JSONResponse(await asyncio.to_thread(me_payload, ctx, principal))

    async def me_password(request: Request) -> Response:
        principal = request_principal(request)
        denied = require_recent_auth(principal)
        if denied is not None:
            return denied
        body = await read_json_body(request)
        if not set(body) <= {"current_password", "new_password"} or "new_password" not in body:
            return json_error(400, "invalid_request")
        current = body.get("current_password")
        new = body["new_password"]
        if not isinstance(new, str) or (current is not None and not isinstance(current, str)):
            return json_error(400, "invalid_request")
        if principal.account_id is None:
            raise RuntimeError("/api/me/password requires an account principal")
        outcome = await asyncio.to_thread(
            perform_password_change,
            ctx,
            account_id=principal.account_id,
            keep_token=request_session_token(request, principal),
            current_password=current,
            new_password=new,
            ip=client_ip(request, ctx),
        )
        if outcome.kind == "throttled":
            return JSONResponse(
                {"error": "too_many_attempts"},
                status_code=429,
                headers={"Retry-After": str(retry_after_seconds(outcome.retry_after))},
            )
        if outcome.kind == "invalid_password":
            return json_error(403, "invalid_password")
        if outcome.kind == "rejected":
            return json_error(
                400, "password_rejected", reason=outcome.reason,
                message=PASSWORD_REASON_TEXT.get(outcome.reason or "", ""),
            )
        if outcome.kind == "not_active":
            return json_error(409, "account_not_active")
        return JSONResponse({"ok": True, "other_sessions_revoked": outcome.other_sessions_revoked})

    return [
        Route(ME_PATH, me_page, methods=["GET"]),
        Route(ME_API_PATH, me_get, methods=["GET"]),
        Route(ME_API_PATH, me_patch, methods=["PATCH"]),
        Route(ME_PASSWORD_PATH, me_password, methods=["POST"]),
    ]
