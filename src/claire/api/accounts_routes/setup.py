"""최초 관리자 설정 ``/setup`` (설계 §8.4)와 ``GET /``의 설정 리다이렉트.

열리는 조건: 주체가 관리자 계정(1)의 ``owner`` 범위이고 채널이 cookie 또는 x-session이며, 계정 상태가 ``setup_required``. 그 밖(미인증, bearer, 이미 active)은 모두 404다. 존재를 숨기고 재설정을 막는다.

두 DB 순서(claire.db의 ``complete_setup`` → auth.db의 ``set_password``):

- 입력 검사(USERNAME 규칙·퇴역·중복, 비밀번호 정책·확인)를 쓰기 전에 모두 끝낸다. 그래서 두 번째 쓰기에 남는 실패는 auth.db 입출력 오류뿐이다.
- ``complete_setup``은 조건부 UPDATE 한 번이라 동시 확정 중 하나만 이긴다. 이긴 요청만 비밀번호를 쓴다. 순서를 거꾸로 하면 진 요청의 비밀번호가 이긴 요청의 USERNAME에 덮어써질 수 있다(계정은 a, 비밀번호는 B).
- 비밀번호 저장이 실패하면 계정은 ``active``이고 비밀번호가 없다. 이 상태는 로그인만 막고(로그인은 비밀번호 행이 없으면 실패한다), 관리자는 세션으로 ``/me``에서 비밀번호를 설정한다(비밀번호가 없으면 현재 비밀번호 없이 단계 인증만 요구한다). 세션을 잃어도 텔레그램 승인·호스트 CLI 매직 링크(§8.5 복구 수단)로 새 세션을 얻는다. 확정을 되돌리는 보정 코드는 두지 않는다(C1).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Literal

from starlette.requests import Request
from starlette.responses import RedirectResponse, Response
from starlette.routing import Route

from ...accounts.username_policy import UsernameRejected, validate_username
from ...store import accounts as accounts_store
from ...store import auth_db
from ..namespace import account_home_path
from ..principal import Principal, request_principal
from .common import (
    AccountsContext,
    error_list,
    not_found,
    open_auth_db,
    open_db,
    render_template,
    request_session_token,
    set_session_cookie,
    validate_display_name,
)

log = logging.getLogger("claire.api.accounts")

SETUP_PATH = "/setup"

USERNAME_REASON_TEXT = {
    "syntax": "USERNAME 형식이 맞지 않습니다. 소문자로 시작하는 3–30자의 영문 소문자·숫자·밑줄만 쓸 수 있고, 밑줄을 연속으로 쓰거나 밑줄로 끝낼 수 없습니다.",
    "reserved": "예약된 USERNAME입니다.",
    "retired": "퇴역한 USERNAME이라 쓸 수 없습니다.",
    "taken": "이미 쓰고 있는 USERNAME입니다.",
}
PASSWORD_REASON_TEXT = {
    "invalid_characters": "비밀번호에는 영문 대소문자, 숫자, 표준 특수문자(ASCII 문장부호)만 쓸 수 있습니다. 공백·한글 등 그 밖의 문자는 쓸 수 없습니다.",
    "too_short": f"비밀번호는 {auth_db.MIN_PASSWORD_LENGTH}자 이상이어야 합니다.",
    "contains_username": "비밀번호에 USERNAME을 넣을 수 없습니다.",
    "mismatch": "비밀번호 확인이 일치하지 않습니다.",
}
FORM_REASON_TEXT = {
    "display_name": "표시 이름은 64자 이하이고 제어 문자를 넣을 수 없습니다.",
    "ack": "확정 안내를 읽고 확인란을 선택해야 합니다.",
}


def _is_setup_principal(principal: Principal) -> bool:
    return (
        principal.account_id == accounts_store.ADMIN_ACCOUNT_ID
        and principal.instance_role == "admin"
        and principal.scope == "owner"
        and principal.channel in ("cookie", "x-session")
    )


def _account_status(ctx: AccountsContext, account_id: int) -> str | None:
    conn = open_db(ctx)
    try:
        return accounts_store.AccountStore(conn).get(account_id).status
    except accounts_store.AccountNotFound:
        return None
    finally:
        conn.close()


async def setup_pending(request: Request, ctx: AccountsContext) -> bool:
    """이 요청이 ``/setup``을 열 수 있는가(§8.4-2·3)."""
    principal = request_principal(request)
    if not _is_setup_principal(principal):
        return False
    status = await asyncio.to_thread(_account_status, ctx, principal.account_id)  # type: ignore[arg-type]
    return status == "setup_required"


async def setup_redirect(request: Request, ctx: AccountsContext) -> Response | None:
    """``GET /``: 관리자 owner 세션이고 상태가 ``setup_required``면 ``/setup``으로 302. 그 밖은 None(기존 동작 그대로, E1)."""
    if await setup_pending(request, ctx):
        response = RedirectResponse(SETUP_PATH, status_code=302)
        response.headers["Cache-Control"] = "no-store"
        return response
    return None


@dataclass(frozen=True, slots=True)
class SetupOutcome:
    kind: Literal["ok", "rejected", "closed", "password_failed"]
    session_token: str | None = None
    reasons: tuple[str, ...] = ()
    redirect_path: str = "/"


def validate_setup_input(
    ctx: AccountsContext,
    *,
    raw_username: str,
    display_name: str,
    password: str,
    password_confirm: str,
    acknowledged: bool,
) -> tuple[str | None, list[str]]:
    """DB를 쓰기 전 검사. (정규화한 USERNAME 또는 None, 사유 코드 목록)."""
    reasons: list[str] = []
    username: str | None = None
    try:
        username = validate_username(raw_username, instance_reserved=ctx.instance_reserved)
    except UsernameRejected as exc:
        reasons.append(f"username:{exc.reason}")
    if username is not None:
        conn = open_db(ctx)
        try:
            store = accounts_store.AccountStore(conn)
            if store.is_username_retired(username):
                reasons.append("username:retired")
            else:
                taken = store.find_by_username(username)
                if taken is not None and taken.id != accounts_store.ADMIN_ACCOUNT_ID:
                    reasons.append("username:taken")
        finally:
            conn.close()
    if validate_display_name(display_name) is None:
        reasons.append("form:display_name")
    if password != password_confirm:
        reasons.append("password:mismatch")
    else:
        try:
            auth_db.check_password_policy(password, username=username or raw_username)
        except auth_db.PasswordRejected as exc:
            reasons.append(f"password:{exc.reason}")
    if not acknowledged:
        reasons.append("form:ack")
    return username, reasons


def perform_setup(
    ctx: AccountsContext,
    *,
    principal: Principal,
    replace_token: str | None,
    raw_username: str,
    display_name: str,
    password: str,
    password_confirm: str,
    acknowledged: bool,
) -> SetupOutcome:
    """검사 → ``complete_setup`` → ``set_password`` → 세션 교체 → 감사 기록. 순서의 근거는 모듈 설명에 있다."""
    username, reasons = validate_setup_input(
        ctx,
        raw_username=raw_username,
        display_name=display_name,
        password=password,
        password_confirm=password_confirm,
        acknowledged=acknowledged,
    )
    if reasons or username is None:
        return SetupOutcome("rejected", reasons=tuple(reasons))
    account_id = accounts_store.ADMIN_ACCOUNT_ID

    conn = open_db(ctx)
    try:
        try:
            accounts_store.AccountStore(conn).complete_setup(
                account_id, username=username, display_name=display_name
            )
        except UsernameRejected as exc:
            # 검사와 확정 사이에 다른 요청이 이름을 가져갔거나 퇴역시켰다.
            return SetupOutcome("rejected", reasons=(f"username:{exc.reason}",))
        except accounts_store.AccountStateError:
            # 동시 확정에서 졌거나 이미 active다. 존재를 숨기는 404와 같다.
            return SetupOutcome("closed")

        password_saved = True
        auth = open_auth_db(ctx)
        try:
            try:
                auth_db.set_password(auth, account_id, password, username=username)
            except Exception:  # noqa: BLE001  # 입출력 오류. 확정은 되돌리지 않는다(모듈 설명)
                log.exception("setup: account %s activated but password could not be stored", account_id)
                password_saved = False
            # 세션 고정 방지(§8.4-5): 확정 전 세션을 지우고 새로 발급한다.
            token = accounts_store.create_account_session(
                conn, account_id=account_id, scope="owner", replace_token=replace_token
            )
            auth_db.record_admin_audit(
                auth,
                actor_account_id=account_id,
                action="account.setup",
                target_account_id=account_id,
                detail={
                    "channel": principal.channel,
                    "credential_id": principal.credential_id,
                    "password_saved": password_saved,
                },
            )
            mode = accounts_store.AccountStore(conn).instance_mode()
            redirect_path = account_home_path(mode, username)
        finally:
            auth.close()
    finally:
        conn.close()
    return SetupOutcome("ok" if password_saved else "password_failed", session_token=token, redirect_path=redirect_path)


def _reason_messages(reasons: tuple[str, ...] | list[str]) -> list[str]:
    messages = []
    for code in reasons:
        kind, _, reason = code.partition(":")
        table = {"username": USERNAME_REASON_TEXT, "password": PASSWORD_REASON_TEXT, "form": FORM_REASON_TEXT}[kind]
        messages.append(f"{table[reason]} ({reason})")
    return messages


def _setup_page(
    *,
    username: str = "",
    display_name: str = "",
    reasons: tuple[str, ...] | list[str] = (),
    status_code: int = 200,
) -> Response:
    return render_template(
        "setup.html",
        text={
            "username": username,
            "display_name": display_name,
            "min_password_length": auth_db.MIN_PASSWORD_LENGTH,
        },
        markup={"errors": error_list(_reason_messages(reasons))},
        status_code=status_code,
    )


def routes(ctx: AccountsContext) -> list[Route]:
    async def setup_get(request: Request) -> Response:
        if not await setup_pending(request, ctx):
            return not_found()
        return _setup_page()

    async def setup_post(request: Request) -> Response:
        if not await setup_pending(request, ctx):
            return not_found()
        principal = request_principal(request)
        form = await request.form()
        fields = {k: form.get(k) for k in ("username", "display_name", "password", "password_confirm", "ack")}
        if any(v is not None and not isinstance(v, str) for v in fields.values()):
            return _setup_page(status_code=400)
        raw_username = fields["username"] or ""
        display_name = fields["display_name"] or ""
        outcome = await asyncio.to_thread(
            perform_setup,
            ctx,
            principal=principal,
            replace_token=request_session_token(request, principal),
            raw_username=raw_username,
            display_name=display_name,
            password=fields["password"] or "",
            password_confirm=fields["password_confirm"] or "",
            acknowledged=fields["ack"] == "1",
        )
        if outcome.kind == "rejected":
            return _setup_page(
                username=raw_username, display_name=display_name, reasons=outcome.reasons, status_code=400
            )
        if outcome.kind == "closed":
            return not_found()
        if outcome.session_token is None:
            raise RuntimeError("setup completed without a session token")
        # 비밀번호 저장만 실패했으면 확정은 끝났으므로 /settings/account로 보내 그 사실과 설정 양식을 보여 준다.
        location = outcome.redirect_path if outcome.kind == "ok" else "/settings/account?notice=password_not_saved"
        response = Response(status_code=303, headers={"Location": location, "Cache-Control": "no-store"})
        set_session_cookie(response, ctx, outcome.session_token)
        return response

    return [
        Route(SETUP_PATH, setup_get, methods=["GET"]),
        Route(SETUP_PATH, setup_post, methods=["POST"]),
    ]
