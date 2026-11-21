"""로컬 로그인 ``/login``, 로그아웃 ``/logout``, 단계 인증 ``/api/auth/reauth`` (설계 §8.5).

``/login``·``/logout``은 공개 라우트다(미들웨어가 자격증명을 검증하지 않는다). 로그인 CSRF와 강제 로그아웃을 막기 위해 핸들러가 동일 origin만 받는다(`common.same_origin`).

로그인 실패 응답은 계정 유무, 상태(정지, ``setup_required``), 비밀번호 행 유무를 구분하지 않는다. 계정이 없거나 active가 아니면 ``verify_password(None)``으로 같은 비용의 더미 계산을 한다.
"""

from __future__ import annotations

import asyncio
import hmac
import time
from dataclasses import dataclass
from typing import Literal
from urllib.parse import quote, urlsplit

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from ...accounts.username_policy import UsernameRejected, normalize_username
from ...i18n import t
from ...store import accounts as accounts_store
from ...store import auth_db
from ...store import db as dbm
from ..namespace import account_home_path
from ..principal import request_principal
from ..security import clear_oauth_tx_cookie, read_json_body, read_oauth_tx_cookie
from .common import (
    SESSION_CHANNELS,
    AccountsContext,
    clear_session_cookie,
    client_ip,
    cookie_session_token,
    error_list,
    escape,
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
# Catalog keys of the login messages (I18N design Section 4.2).
LOGIN_FAILED_KEY = "auth.login.failed"
LOGIN_THROTTLED_KEY = "auth.login.throttled"


@dataclass(frozen=True, slots=True)
class LoginOutcome:
    kind: Literal["ok", "failed", "throttled"]
    session_token: str | None = None
    retry_after: float | None = None
    redirect_path: str = "/"
    account_id: int | None = None


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
            mode = accounts_store.AccountStore(conn).server_mode()
            redirect_path = account_home_path(mode, account.username if account is not None else None)
        finally:
            conn.close()
    finally:
        auth.close()
    return LoginOutcome("ok", session_token=token, redirect_path=redirect_path, account_id=candidate)


def _enabled_idp_links_html(ctx: AccountsContext, *, tx_id: str = "", next_path: str = "") -> str:
    """Render zero-JS sign-in links for enabled external identity providers while keeping tx or device next state."""
    data_dir = ctx.data_dir or ctx.auth_db_file.parent.parent
    providers = [p for p in auth_db.load_idp_providers(data_dir, include_secrets=False) if p.enabled]
    if not providers:
        return ""
    suffix = ""
    if tx_id:
        suffix = f"?tx={quote(tx_id, safe='')}"
    elif next_path:
        suffix = f"?next={quote(next_path, safe='')}"
    links = "".join(
        f'<a class="idp-btn" href="/auth/oidc/{quote(p.id, safe="")}/start{suffix}">{escape(t("console.js.admin.idp.continue_with", name=p.display_name))}</a>'
        for p in providers
    )
    return f'<div class="card idp-links">{links}</div>'


def _login_page(*, username: str = "", message: str | None = None, status_code: int = 200,
                headers: dict[str, str] | None = None, tx_id: str = "", oauth_client_name: str = "", next_path: str = "", idp_links_html: str = "") -> Response:
    banner_html = ""
    if oauth_client_name:
        banner_html = f'<p class="notice" role="status">{escape(t("auth.login.oauth_banner", client=oauth_client_name))}</p>'
    hidden_parts: list[str] = []
    if tx_id:
        hidden_parts.append(f'<input type="hidden" name="tx" value="{escape(tx_id)}">')
    if next_path:
        hidden_parts.append(f'<input type="hidden" name="next" value="{escape(next_path)}">')
    return render_template(
        "login.html",
        text={"username": username},
        markup={"errors": error_list([message] if message else []), "oauth_tx_banner": banner_html, "oauth_tx_input": "".join(hidden_parts), "idp_links": idp_links_html},
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


def safe_device_next(raw_next: str | None) -> str:
    """Validate optional /device post-login redirect target."""
    val = str(raw_next or "").strip()
    if not val or len(val) > 512:
        return ""
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in val):
        return ""
    parsed = urlsplit(val)
    if parsed.scheme or parsed.netloc or parsed.path != "/device":
        return ""
    return val


def _resolve_oauth_client_name(ctx: AccountsContext, request: Request, tx: str) -> str:
    """Resolve active OAuth client label only when tx matches the browser oauth_tx cookie."""
    if not tx:
        return ""
    cookie_tx = read_oauth_tx_cookie(request.scope)
    if not cookie_tx or not hmac.compare_digest(cookie_tx, tx):
        return ""
    conn = open_db(ctx)
    try:
        pending = dbm.get_oauth_pending_request(conn, tx)
    finally:
        conn.close()
    if pending is None:
        return ""
    return str(pending.get("client_name") or pending.get("client_id") or "")


def routes(ctx: AccountsContext) -> list[Route]:
    async def login_get(request: Request) -> Response:
        tx = request.query_params.get("tx", "").strip()
        next_path = safe_device_next(request.query_params.get("next"))
        if not tx:
            idp_html = await asyncio.to_thread(_enabled_idp_links_html, ctx, next_path=next_path)
            return _login_page(next_path=next_path, idp_links_html=idp_html)
        cookie_tx = read_oauth_tx_cookie(request.scope)
        if not cookie_tx or not hmac.compare_digest(cookie_tx, tx):
            idp_html = await asyncio.to_thread(_enabled_idp_links_html, ctx)
            return _login_page(message=t("auth.login.oauth_tx_invalid"), status_code=400, idp_links_html=idp_html)

        def _load_pending() -> dict | None:
            conn = open_db(ctx)
            try:
                return dbm.get_oauth_pending_request(conn, tx)
            finally:
                conn.close()

        pending = await asyncio.to_thread(_load_pending)
        if pending is None:
            idp_html = await asyncio.to_thread(_enabled_idp_links_html, ctx)
            return _login_page(message=t("auth.login.oauth_tx_invalid"), status_code=400, idp_links_html=idp_html)
        client_label = str(pending.get("client_name") or pending.get("client_id") or "")
        idp_html = await asyncio.to_thread(_enabled_idp_links_html, ctx, tx_id=tx, next_path=next_path)
        return _login_page(tx_id=tx, oauth_client_name=client_label, next_path=next_path, idp_links_html=idp_html)

    async def login_post(request: Request) -> Response:
        if not same_origin(request):
            return forbidden()
        form = await request.form()
        raw_username = form.get("username")
        password = form.get("password")
        raw_tx = form.get("tx")
        tx = raw_tx.strip() if isinstance(raw_tx, str) else request.query_params.get("tx", "").strip()
        raw_next = form.get("next")
        next_path = safe_device_next(raw_next if isinstance(raw_next, str) else request.query_params.get("next"))
        if tx:
            cookie_tx = read_oauth_tx_cookie(request.scope)
            if not cookie_tx or not hmac.compare_digest(cookie_tx, tx):
                idp_html = await asyncio.to_thread(_enabled_idp_links_html, ctx)
                return _login_page(username=raw_username if isinstance(raw_username, str) else "", message=t("auth.login.oauth_tx_invalid"), status_code=403, idp_links_html=idp_html)
        if not isinstance(raw_username, str) or not isinstance(password, str):
            client_label = await asyncio.to_thread(_resolve_oauth_client_name, ctx, request, tx)
            idp_html = await asyncio.to_thread(_enabled_idp_links_html, ctx, tx_id=tx, next_path=next_path)
            return _login_page(message=t(LOGIN_FAILED_KEY), status_code=400, tx_id=tx, oauth_client_name=client_label, next_path=next_path, idp_links_html=idp_html)
        outcome = await asyncio.to_thread(
            perform_login,
            ctx,
            raw_username=raw_username,
            password=password,
            ip=client_ip(request, ctx),
            replace_token=cookie_session_token(request),
        )
        if outcome.kind == "throttled":
            client_label = await asyncio.to_thread(_resolve_oauth_client_name, ctx, request, tx)
            idp_html = await asyncio.to_thread(_enabled_idp_links_html, ctx, tx_id=tx, next_path=next_path)
            return _login_page(
                username=raw_username,
                message=t(LOGIN_THROTTLED_KEY),
                status_code=429,
                headers={"Retry-After": str(retry_after_seconds(outcome.retry_after))},
                tx_id=tx,
                oauth_client_name=client_label,
                next_path=next_path,
                idp_links_html=idp_html,
            )
        if outcome.kind == "failed":
            client_label = await asyncio.to_thread(_resolve_oauth_client_name, ctx, request, tx)
            idp_html = await asyncio.to_thread(_enabled_idp_links_html, ctx, tx_id=tx, next_path=next_path)
            return _login_page(username=raw_username, message=t(LOGIN_FAILED_KEY), status_code=401, tx_id=tx, oauth_client_name=client_label, next_path=next_path, idp_links_html=idp_html)
        if outcome.session_token is None or outcome.account_id is None:
            raise RuntimeError("login succeeded without a session token")
        location = next_path or outcome.redirect_path
        if tx:
            cookie_tx = read_oauth_tx_cookie(request.scope)
            if not cookie_tx or not hmac.compare_digest(cookie_tx, tx):
                idp_html = await asyncio.to_thread(_enabled_idp_links_html, ctx)
                return _login_page(username=raw_username, message=t("auth.login.oauth_tx_invalid"), status_code=403, idp_links_html=idp_html)

            def _bind_tx() -> bool:
                conn = open_db(ctx)
                try:
                    return dbm.bind_oauth_pending_request_account(conn, tx, outcome.account_id)  # type: ignore[arg-type]
                finally:
                    conn.close()

            bound = await asyncio.to_thread(_bind_tx)
            if not bound:
                idp_html = await asyncio.to_thread(_enabled_idp_links_html, ctx)
                return _login_page(username=raw_username, message=t("auth.login.oauth_tx_invalid"), status_code=400, idp_links_html=idp_html)
            location = f"/oauth/authorize?tx={quote(tx, safe='')}"
        response = Response(status_code=303, headers={"Location": location, "Cache-Control": "no-store"})
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
        response = Response(status_code=303, headers={"Location": LOGIN_PATH, "Cache-Control": "no-store", "Clear-Site-Data": '"cookies", "storage", "cache"'})
        clear_session_cookie(response, ctx)
        clear_oauth_tx_cookie(response, ctx.config)
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
