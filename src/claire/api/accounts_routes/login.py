"""로컬 로그인 ``/login``, 로그아웃 ``/logout``, 단계 인증 ``/api/auth/reauth`` (설계 §8.5).

``/login``·``/logout``은 공개 라우트다(미들웨어가 자격증명을 검증하지 않는다). 로그인 CSRF와 강제 로그아웃을 막기 위해 핸들러가 동일 origin만 받는다(`common.same_origin`).

로그인 실패 응답은 계정 유무, 상태(정지, ``setup_required``), 비밀번호 행 유무를 구분하지 않는다. 계정이 없거나 active가 아니면 ``verify_password(None)``으로 같은 비용의 더미 계산을 한다.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Literal

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from ...accounts.username_policy import UsernameRejected, normalize_username
from ...store import accounts as accounts_store
from ...store import auth_db
from ..principal import request_principal
from ..security import read_json_body
from .common import (
    SESSION_CHANNELS,
    AccountsContext,
    clear_session_cookie,
    client_ip,
    cookie_session_token,
    error_list,
    forbidden,
    json_error,
    open_auth_db,
    open_db,
    render_template,
    request_session_token,
    retry_after_seconds,
    same_origin,
    set_session_cookie,
)

LOGIN_PATH = "/login"
LOGOUT_PATH = "/logout"
REAUTH_PATH = "/api/auth/reauth"

# 실패 제한 키에 넣을 USERNAME 원문 길이 상한. 규칙을 벗어난 입력도 같은 방식으로 센다.
_THROTTLE_NAME_MAX = 64
LOGIN_FAILED_TEXT = "USERNAME 또는 비밀번호가 맞지 않습니다."
LOGIN_THROTTLED_TEXT = "로그인 시도가 너무 많습니다. 잠시 뒤에 다시 시도하세요."


@dataclass(frozen=True, slots=True)
class LoginOutcome:
    kind: Literal["ok", "failed", "throttled"]
    session_token: str | None = None
    retry_after: float | None = None


def throttle_name(raw: str) -> str:
    """실패 제한 키의 USERNAME 부분. 정규화가 거부한 입력도 계정이 없는 이름과 똑같이 센다."""
    try:
        return normalize_username(raw)
    except UsernameRejected:
        return raw[:_THROTTLE_NAME_MAX]


def perform_login(
    ctx: AccountsContext,
    *,
    raw_username: str,
    password: str,
    ip: str,
    replace_token: str | None,
) -> LoginOutcome:
    """§8.5 순서: 정규화 → 실패 제한 확인과 시도 기록(한 트랜잭션) → 계정 조회 → 상태 → 비밀번호 → 성공이면 기록 정리 → 세션 발급."""
    try:
        username: str | None = normalize_username(raw_username)
    except UsernameRejected:
        username = None
    key_name = username if username is not None else throttle_name(raw_username)

    auth = open_auth_db(ctx)
    try:
        # 확인과 기록을 검증 전에 원자적으로 한다. 이 시도는 실패로 미리 세고, 성공하면 되돌린다.
        decision = auth_db.begin_login_attempt(auth, username=key_name, ip=ip)
        if not decision.allowed:
            # 거부한 시도는 실패로 세지 않는다.
            return LoginOutcome("throttled", retry_after=decision.retry_after)

        conn = open_db(ctx)
        try:
            account = None
            if username is not None:
                account = accounts_store.AccountStore(conn).find_by_username(username)
            candidate = account.id if account is not None and account.status == "active" else None
            # 계정이 없거나 active가 아니면 None으로 같은 비용의 더미 계산을 한다.
            ok = auth_db.verify_password(auth, candidate, password)
            if not ok or candidate is None:
                return LoginOutcome("failed")
            auth_db.settle_login_success(auth, username=key_name, ip=ip)
            # 세션 고정 방지: 브라우저가 보낸 기존 세션은 지우고 새로 발급한다.
            token = accounts_store.create_account_session(
                conn, account_id=candidate, scope="owner", replace_token=replace_token
            )
        finally:
            conn.close()
    finally:
        auth.close()
    return LoginOutcome("ok", session_token=token)


def _login_page(*, username: str = "", message: str | None = None, status_code: int = 200,
                headers: dict[str, str] | None = None) -> Response:
    return render_template(
        "login.html",
        text={"username": username},
        markup={"errors": error_list([message] if message else [])},
        status_code=status_code,
        headers=headers,
    )


@dataclass(frozen=True, slots=True)
class ReauthOutcome:
    kind: Literal["ok", "failed", "throttled", "no_password", "no_session"]
    authenticated_at: float | None = None
    retry_after: float | None = None


def perform_reauth(
    ctx: AccountsContext, *, account_id: int, token: str, password: str, ip: str
) -> ReauthOutcome:
    """세션 주인의 비밀번호를 다시 확인하고 ``authenticated_at``을 갱신한다. 로그인과 같은 실패 제한을 쓴다."""
    conn = open_db(ctx)
    auth = open_auth_db(ctx)
    try:
        account = accounts_store.AccountStore(conn).get(account_id)
        key_name = account.username if account.username is not None else f"#account:{account.id}"
        if not auth_db.has_password(auth, account_id):
            # 검증할 것이 없으므로 시도로 세지 않는다. 세션 주인 자신의 계정이라 숨길 것도 없다.
            return ReauthOutcome("no_password")
        decision = auth_db.begin_login_attempt(auth, username=key_name, ip=ip)
        if not decision.allowed:
            return ReauthOutcome("throttled", retry_after=decision.retry_after)
        if not auth_db.verify_password(auth, account_id, password):
            return ReauthOutcome("failed")
        auth_db.settle_login_success(auth, username=key_name, ip=ip)
        now = time.time()
        if not accounts_store.mark_session_authenticated(conn, token, now=now):
            return ReauthOutcome("no_session")
        return ReauthOutcome("ok", authenticated_at=now)
    finally:
        auth.close()
        conn.close()


def routes(ctx: AccountsContext) -> list[Route]:
    async def login_get(_request: Request) -> Response:
        return _login_page()

    async def login_post(request: Request) -> Response:
        if not same_origin(request):
            return forbidden()
        form = await request.form()
        raw_username = form.get("username")
        password = form.get("password")
        if not isinstance(raw_username, str) or not isinstance(password, str):
            return _login_page(message=LOGIN_FAILED_TEXT, status_code=400)
        outcome = await asyncio.to_thread(
            perform_login,
            ctx,
            raw_username=raw_username,
            password=password,
            ip=client_ip(request, ctx),
            replace_token=cookie_session_token(request),
        )
        if outcome.kind == "throttled":
            return _login_page(
                username=raw_username,
                message=LOGIN_THROTTLED_TEXT,
                status_code=429,
                headers={"Retry-After": str(retry_after_seconds(outcome.retry_after))},
            )
        if outcome.kind == "failed":
            return _login_page(username=raw_username, message=LOGIN_FAILED_TEXT, status_code=401)
        if outcome.session_token is None:
            raise RuntimeError("login succeeded without a session token")
        response = Response(status_code=303, headers={"Location": "/", "Cache-Control": "no-store"})
        set_session_cookie(response, ctx, outcome.session_token)
        return response

    async def logout_post(request: Request) -> Response:
        if not same_origin(request):
            return forbidden()
        token = cookie_session_token(request)
        if token is not None:

            def _delete() -> None:
                conn = open_db(ctx)
                try:
                    accounts_store.delete_session(conn, token)
                finally:
                    conn.close()

            await asyncio.to_thread(_delete)
        response = Response(status_code=303, headers={"Location": LOGIN_PATH, "Cache-Control": "no-store"})
        clear_session_cookie(response, ctx)
        return response

    async def reauth_post(request: Request) -> Response:
        principal = request_principal(request)
        if principal.channel not in SESSION_CHANNELS or principal.account_id is None:
            return json_error(403, "step_up_requires_session")
        token = request_session_token(request, principal)
        if token is None:
            return json_error(403, "step_up_requires_session")
        body = await read_json_body(request)
        password = body.get("password")
        if not isinstance(password, str) or set(body) != {"password"}:
            return json_error(400, "invalid_request")
        outcome = await asyncio.to_thread(
            perform_reauth,
            ctx,
            account_id=principal.account_id,
            token=token,
            password=password,
            ip=client_ip(request, ctx),
        )
        if outcome.kind == "throttled":
            return JSONResponse(
                {"error": "too_many_attempts"},
                status_code=429,
                headers={"Retry-After": str(retry_after_seconds(outcome.retry_after))},
            )
        if outcome.kind == "no_password":
            return json_error(409, "password_not_set")
        if outcome.kind == "failed":
            return json_error(403, "invalid_password")
        if outcome.kind == "no_session":
            return json_error(403, "step_up_requires_session")
        return JSONResponse({"ok": True, "authenticated_at": outcome.authenticated_at})

    return [
        Route(LOGIN_PATH, login_get, methods=["GET"]),
        Route(LOGIN_PATH, login_post, methods=["POST"]),
        Route(LOGOUT_PATH, logout_post, methods=["POST"]),
        Route(REAUTH_PATH, reauth_post, methods=["POST"]),
    ]
