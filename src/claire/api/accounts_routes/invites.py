"""Invitation HTTP routes (설계 §8.8, §13.3, §15 S12, P3 L3-b).

- 싱글 모드에서는 모든 초대 경로 404 반환. 네트워크 모드에서만 활성화.
- GET /invite/{code}: public, X-Robots-Tag: noindex, nofollow, 외부 링크 rel="noreferrer noopener".
  GET/HEAD는 일절 uses_count 차감 및 실패 카운트 증가 없음(무부작용).
- POST /invite/{code}: public 로컬 가입 제출 (username, display_name, password).
  원자적 초대 차감 및 계정 생성.
- GET /api/invites: owner 전용 컬렉션 목록 (관리자는 전체, 비관리자는 본인 발급분만).
- POST /api/invites: owner 전용 초대 생성 (비관리자는 server_settings.member_invites_enabled 설정 적용).
- PATCH /api/invites: owner 전용 초대 비활성화 (관리자는 전체, 비관리자는 본인 발급분만).
- IP당 잘못된 코드 조회 제한 (분당 10회 초과 시 429 / 지연).
"""

from __future__ import annotations

import asyncio
import math
import sqlite3
import time
from typing import Any

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Route

from ...accounts.invites import (
    InvitationInfo,
    InviteError,
    InviteInactiveError,
    InviteNotFoundError,
    InviteValidationError,
    mask_invite_code,
    normalize_invite_code,
    validate_invite_code_syntax,
)
from ...accounts.username_policy import UsernameRejected, validate_username
from ...api.collections import (
    collection_cache_headers,
    collection_etag,
    collection_page,
    collection_root,
    parse_collection_query,
)
from ...api.principal import Principal, request_principal
from ...store import accounts as accounts_store
from ...store import auth_db
from ...store import db as dbm
from ...store import themes as themes_store
from ...core import ids
from ...i18n import get_locale, t
from .common import (
    FORM_PAGE_REFERRER_POLICY,
    AccountsContext,
    client_ip,
    error_list,
    escape,
    forbidden,
    json_error,
    not_found,
    open_auth_db,
    open_db,
    retry_after_seconds,
    same_origin,
    set_session_cookie,
    t_code,
    validate_display_name,
)


def _is_network_mode(ctx: AccountsContext) -> bool:
    conn = open_db(ctx)
    try:
        return accounts_store.AccountStore(conn).server_mode() == "network"
    finally:
        conn.close()


def _is_admin(principal: Principal) -> bool:
    return principal.server_role == "admin" and principal.scope == "owner" and principal.account_id is not None


def _is_owner(principal: Principal) -> bool:
    return principal.scope == "owner" and principal.account_id is not None


def _wants_json(request: Request) -> bool:
    if request.query_params.get("json") == "1":
        return True
    accept = request.headers.get("accept", "").lower()
    return "application/json" in accept and "text/html" not in accept


def _render_invite_html(
    info: InvitationInfo,
    server_name: str,
    code: str,
    *,
    error_message: str | None = None,
    username_value: str | None = None,
    display_name_value: str | None = None,
) -> str:
    reserved_note = f"<p>{escape(t('auth.invite.reserved', username='@' + info.reserved_username))}</p>" if info.reserved_username else ""
    readonly_attr = ' readonly' if info.reserved_username else ''
    val_username = escape(info.reserved_username) if info.reserved_username else escape(username_value or '')
    val_display_name = escape(display_name_value or '')
    errors_html = error_list([error_message]) if error_message else ""
    return (
        "<!doctype html>"
        f"<html lang=\"{get_locale()}\">"
        "<head>"
        "<meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        "<meta name=\"robots\" content=\"noindex, nofollow\">"
        f"<title>{escape(t('auth.invite.page_title', server=server_name))}</title>"
        "<style>"
        "body { font-family: system-ui, -apple-system, sans-serif; max-width: 440px; margin: 40px auto; padding: 20px; line-height: 1.5; color: #222; }"
        "h1 { font-size: 1.4rem; margin-bottom: 0.5rem; }"
        "label { display: block; margin-top: 1rem; font-weight: 600; font-size: 0.9rem; }"
        "input[type=\"text\"], input[type=\"password\"] { width: 100%; box-sizing: border-box; padding: 8px 10px; margin-top: 4px; font-size: 1rem; border: 1px solid #ccc; border-radius: 4px; }"
        "button { width: 100%; margin-top: 1.5rem; padding: 10px; font-size: 1rem; font-weight: 600; background: #0056b3; color: #fff; border: 0; border-radius: 4px; cursor: pointer; }"
        ".errors { background: #fdf2f2; color: #b91c1c; border: 1px solid #f5c2c2; border-radius: 4px; padding: 10px 14px; margin: 12px 0; list-style: disc inside; font-size: 0.92rem; }"
        ".footer { margin-top: 2rem; font-size: 0.8rem; color: #666; text-align: center; }"
        ".footer a { color: #0056b3; }"
        "</style>"
        "</head>"
        "<body>"
        f"<h1>{escape(t('auth.invite.title', server=server_name))}</h1>"
        f"<p>{t_code('auth.invite.code', code=code)}</p>"
        f"{reserved_note}"
        f"{errors_html}"
        f"<form method=\"POST\" action=\"/invite/{escape(code)}\">"
        f"<label for=\"username\">USERNAME</label>"
        f"<input id=\"username\" name=\"username\" type=\"text\" value=\"{val_username}\" required autocomplete=\"username\"{readonly_attr}>"
        f"<label for=\"display_name\">{escape(t('auth.invite.display_name'))}</label>"
        f"<input id=\"display_name\" name=\"display_name\" type=\"text\" value=\"{val_display_name}\" placeholder=\"{escape(t('auth.invite.display_name_placeholder'))}\" required autocomplete=\"name\">"
        f"<label for=\"password\">{escape(t('auth.invite.password'))}</label>"
        "<input id=\"password\" name=\"password\" type=\"password\" required autocomplete=\"new-password\" minlength=\"12\">"
        f"<button type=\"submit\">{escape(t('auth.invite.submit'))}</button>"
        "</form>"
        "<div class=\"footer\">"
        "<a href=\"https://github.com/kim-home/claire-bible\" target=\"_blank\" rel=\"noreferrer noopener\">Claire Bible</a>"
        "</div>"
        "</body>"
        "</html>"
    )


def _render_inactive_html(reason: str) -> str:
    msg = t(f"auth.invite.inactive.{reason}") if reason in ("expired", "exhausted", "revoked") else t("auth.invite.inactive.other")
    return (
        "<!doctype html>"
        f"<html lang=\"{get_locale()}\">"
        "<head>"
        "<meta charset=\"utf-8\">"
        "<meta name=\"robots\" content=\"noindex, nofollow\">"
        f"<title>{escape(t('auth.invite.inactive.page_title'))}</title>"
        "<style>body { font-family: system-ui, sans-serif; max-width: 440px; margin: 40px auto; padding: 20px; line-height: 1.5; color: #444; }</style>"
        "</head>"
        "<body>"
        f"<h1>{escape(t('auth.invite.inactive.title'))}</h1>"
        f"<p>{escape(msg)}</p>"
        f"<p><a href=\"/\" rel=\"noreferrer noopener\">{escape(t('auth.invite.inactive.home'))}</a></p>"
        "</body>"
        "</html>"
    )


def routes(ctx: AccountsContext) -> list[Route]:
    async def invite_get(request: Request) -> Response:
        if not await asyncio.to_thread(_is_network_mode, ctx):
            return not_found()

        ip = client_ip(request, ctx)
        auth_conn = open_auth_db(ctx)
        try:
            decision = auth_db.check_invite_throttle(auth_conn, ip=ip)
            if not decision.allowed:
                delay = retry_after_seconds(decision.retry_after)
                return Response("Too Many Requests", status_code=429, headers={"Retry-After": str(delay)})
        finally:
            auth_conn.close()

        raw_code = request.path_params.get("code", "")
        norm_code = normalize_invite_code(raw_code)
        if not validate_invite_code_syntax(norm_code):
            auth_conn = open_auth_db(ctx)
            try:
                auth_db.record_invite_failure(auth_conn, ip=ip)
            finally:
                auth_conn.close()
            return not_found()

        json_headers = {"X-Robots-Tag": "noindex, nofollow", "Cache-Control": "no-store"}
        html_headers = {"X-Robots-Tag": "noindex, nofollow", "Cache-Control": "no-store", "Referrer-Policy": FORM_PAGE_REFERRER_POLICY}

        auth_conn = open_auth_db(ctx)
        try:
            info = auth_db.get_invitation_by_code(auth_conn, norm_code, check_active=True)
        except InviteNotFoundError:
            auth_db.record_invite_failure(auth_conn, ip=ip)
            return not_found()
        except InviteInactiveError as exc:
            if _wants_json(request):
                return JSONResponse({"error": f"invitation_{exc.reason}", "reason": exc.reason}, status_code=410, headers=json_headers)
            return HTMLResponse(_render_inactive_html(exc.reason), status_code=410, headers=html_headers)
        finally:
            auth_conn.close()

        if request.method == "HEAD":
            return Response(status_code=200, headers=json_headers)

        if _wants_json(request):
            return JSONResponse(
                {
                    "code": info.code,
                    "status": "active",
                    "expires_at": info.expires_at,
                    "reserved_username": info.reserved_username,
                    "max_uses": info.max_uses,
                    "uses_count": info.uses_count,
                },
                headers=json_headers,
            )

        server_name = ctx.config.expected_authority or "Claire"
        return HTMLResponse(_render_invite_html(info, server_name, norm_code), headers=html_headers)

    async def invite_post(request: Request) -> Response:
        if not await asyncio.to_thread(_is_network_mode, ctx):
            return not_found()

        if not same_origin(request):
            return forbidden()

        content_type = request.headers.get("content-type", "")
        is_json_request = "application/json" in content_type.lower()
        wants_json = is_json_request or _wants_json(request)
        html_headers = {"X-Robots-Tag": "noindex, nofollow", "Cache-Control": "no-store", "Referrer-Policy": FORM_PAGE_REFERRER_POLICY}

        ip = client_ip(request, ctx)
        auth_conn = open_auth_db(ctx)
        try:
            decision = auth_db.check_invite_throttle(auth_conn, ip=ip)
            if not decision.allowed:
                delay = retry_after_seconds(decision.retry_after)
                return Response("Too Many Requests", status_code=429, headers={"Retry-After": str(delay)})
        finally:
            auth_conn.close()

        raw_code = request.path_params.get("code", "")
        norm_code = normalize_invite_code(raw_code)
        if not validate_invite_code_syntax(norm_code):
            auth_conn = open_auth_db(ctx)
            try:
                auth_db.record_invite_failure(auth_conn, ip=ip)
            finally:
                auth_conn.close()
            return not_found()

        auth_conn = open_auth_db(ctx)
        try:
            info = auth_db.get_invitation_by_code(auth_conn, norm_code, check_active=True)
        except InviteNotFoundError:
            auth_db.record_invite_failure(auth_conn, ip=ip)
            return not_found()
        except InviteInactiveError as exc:
            if wants_json:
                return json_error(410, f"invitation_{exc.reason}", reason=exc.reason)
            return HTMLResponse(_render_inactive_html(exc.reason), status_code=410, headers=html_headers)
        finally:
            auth_conn.close()

        if is_json_request:
            try:
                body = await request.json()
            except Exception:
                return json_error(400, "invalid_json_body")
            if not isinstance(body, dict):
                return json_error(400, "invalid_json_body")
        else:
            form = await request.form()
            body = dict(form)

        raw_username = body.get("username")
        raw_display_name = body.get("display_name")
        raw_password = body.get("password")
        username = ("" if raw_username is None else str(raw_username)).strip()
        display_name = ("" if raw_display_name is None else str(raw_display_name)).strip()
        password = "" if raw_password is None else str(raw_password)

        def _validation_error(code_str: str, detail: str, html_msg: str) -> Response:
            if wants_json:
                return json_error(400, code_str, detail=detail)
            server_name = ctx.config.expected_authority or "Claire"
            return HTMLResponse(
                _render_invite_html(
                    info,
                    server_name,
                    norm_code,
                    error_message=html_msg,
                    username_value=username,
                    display_name_value=display_name,
                ),
                status_code=400,
                headers=html_headers,
            )

        if info.reserved_username and username.lower() != info.reserved_username.lower():
            return _validation_error(
                "reserved_username_mismatch",
                f"This invitation is reserved for username '{info.reserved_username}'",
                t("auth.invite.reserved", username="@" + info.reserved_username),
            )

        conn = open_db(ctx)
        try:
            store = accounts_store.AccountStore(conn)
            taken_usernames = {a.username.lower() for a in store.list_accounts() if a.username}
            if store.is_username_retired(username):
                return _validation_error("username_retired", "Username is retired", t("auth.username.retired"))
        finally:
            conn.close()

        if username.lower() in taken_usernames:
            return _validation_error("username_taken", "Username is already taken", t("auth.username.taken"))

        try:
            username = validate_username(username, server_reserved=ctx.server_reserved)
        except UsernameRejected as exc:
            html_msg = t(f"auth.username.{exc.reason}") if exc.reason in ("syntax", "reserved", "retired", "taken") else str(exc)
            return _validation_error(f"username_{exc.reason}", str(exc), html_msg)

        if validate_display_name(display_name) is None:
            return _validation_error("invalid_display_name", "Display name is invalid", t("auth.form.display_name"))

        try:
            auth_db.check_password_policy(password, username=username)
        except auth_db.PasswordRejected as exc:
            html_msg = t(f"auth.password.{exc.reason}", min_password_length=auth_db.MIN_PASSWORD_LENGTH) if exc.reason in ("invalid_characters", "too_short", "contains_username") else str(exc)
            return _validation_error(f"password_{exc.reason}", str(exc), html_msg)

        def _create_account_and_theme() -> int:
            c_conn = open_db(ctx)
            try:
                c_conn.execute("BEGIN IMMEDIATE")
                try:
                    salt = dbm.read_id_salt(c_conn)
                    now_ts = time.time()
                    now_ms_val = int(now_ts * 1000)
                    account_holder: dict[str, int] = {}

                    def _insert_account(candidate_public_id: int) -> None:
                        cur = c_conn.execute(
                            "INSERT INTO accounts (public_id, username, role, status, display_name, profile_public, created_at, activated_at, updated_at) "
                            "VALUES (?, ?, 'member', 'active', ?, 0, ?, ?, ?)",
                            (candidate_public_id, username, display_name, now_ts, now_ts, now_ts),
                        )
                        account_holder["id"] = int(cur.lastrowid)

                    ids.insert_with_timestamp_id(c_conn, table="accounts", column="public_id", salt=salt, insert=_insert_account, now_ms=lambda: now_ms_val)
                    new_acc_id = account_holder["id"]

                    theme_seq_row = c_conn.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM knowledge_bases").fetchone()
                    kb_id = int(theme_seq_row[0])

                    def _insert_kb(candidate_number: int) -> None:
                        c_conn.execute(
                            "INSERT INTO knowledge_bases (id, seq, number, label, account_id, is_personal, is_public, created_at, updated_at, db_path) "
                            "VALUES (?, ?, ?, 'General', ?, 1, 0, ?, ?, ?)",
                            (kb_id, kb_id, candidate_number, new_acc_id, now_ts, now_ts, str(ctx.db_file)),
                        )

                    ids.insert_with_timestamp_id(c_conn, table="knowledge_bases", column="number", salt=salt, insert=_insert_kb, now_ms=lambda: now_ms_val + 1, is_taken=lambda n: themes_store.number_is_theme_number(c_conn, n))
                    c_conn.commit()
                    return new_acc_id
                except BaseException:
                    if c_conn.in_transaction:
                        c_conn.rollback()
                    raise
            finally:
                c_conn.close()

        auth_conn = open_auth_db(ctx)
        try:
            try:
                account_id = auth_db.redeem_invitation_atomic(
                    auth_conn,
                    info.id,
                    method="local",
                    account_callback=_create_account_and_theme,
                )
            except InviteNotFoundError:
                return not_found()
            except InviteInactiveError as exc:
                if wants_json:
                    return JSONResponse({"error": f"invitation_{exc.reason}", "reason": exc.reason}, status_code=410, headers={"Cache-Control": "no-store"})
                return HTMLResponse(_render_inactive_html(exc.reason), status_code=410, headers=html_headers)
            except sqlite3.IntegrityError as exc:
                if "accounts.username" in str(exc):
                    return _validation_error("username_taken", "Username is already taken", t("auth.username.taken"))
                raise
            auth_db.set_password(auth_conn, account_id, password, username=username)
        finally:
            auth_conn.close()

        c_conn = open_db(ctx)
        try:
            session_token = accounts_store.create_account_session(c_conn, account_id=account_id, scope="owner")
        finally:
            c_conn.close()

        if wants_json:
            response = JSONResponse(
                {"account_id": account_id, "username": username, "home": f"/@{username}/"},
                status_code=201,
            )
        else:
            response = RedirectResponse(f"/@{username}/", status_code=303)

        set_session_cookie(response, ctx, session_token)
        response.headers["Cache-Control"] = "no-store"
        return response

    async def api_invites_get(request: Request) -> Response:
        if not await asyncio.to_thread(_is_network_mode, ctx):
            return not_found()

        principal = request_principal(request)
        if not _is_owner(principal):
            return not_found()

        caller_is_admin = _is_admin(principal)
        created_by_filter = None if caller_is_admin else principal.account_id
        query = parse_collection_query(request.query_params)
        auth_conn = open_auth_db(ctx)
        try:
            items, total_count = auth_db.list_invitations(auth_conn, query, created_by=created_by_filter)
        finally:
            auth_conn.close()

        c_conn = open_db(ctx)
        try:
            store = accounts_store.AccountStore(c_conn)
            member_invites_enabled = store.member_invites_enabled()
            account_ids = {int(it["created_by"]) for it in items if it.get("created_by") is not None}
            usernames_by_id = store.usernames_by_ids(account_ids)
        finally:
            c_conn.close()

        for it in items:
            it["url"] = f"{ctx.config.public_origin}/invite/{it['code']}"
            is_mine = it["created_by"] == principal.account_id
            it["is_mine"] = is_mine
            it["can_revoke"] = it["status"] == "active" and (caller_is_admin or is_mine)
            it["issuer_username"] = usernames_by_id.get(int(it["created_by"])) if it.get("created_by") is not None else None

        base_url = f"{ctx.config.public_origin}/api/invites"
        if not query.page:
            data = collection_root(collection_url=base_url, total_items=total_count)
        else:
            is_first = (query.max_id is None and query.min_id is None and query.since_id is None)
            data = collection_page(collection_url=base_url, query=query, items=items, item_number="public_id", is_first_page=is_first)

        data["member_invites_enabled"] = member_invites_enabled
        data["can_issue"] = caller_is_admin or member_invites_enabled

        etag = collection_etag([f"total:{total_count}", f"policy:{int(member_invites_enabled)}:{int(data['can_issue'])}", *(f"{it['public_id']}:{it.get('status')}:{it.get('uses_count')}" for it in items)])
        headers = collection_cache_headers(anonymous=False, etag=etag)
        headers["Cache-Control"] = "no-store"
        return JSONResponse(data, headers=headers)

    async def api_invites_post(request: Request) -> Response:
        if not await asyncio.to_thread(_is_network_mode, ctx):
            return not_found()

        principal = request_principal(request)
        if not _is_owner(principal):
            return not_found()

        caller_is_admin = _is_admin(principal)
        c_conn = open_db(ctx)
        try:
            store = accounts_store.AccountStore(c_conn)
            if not caller_is_admin and not store.member_invites_enabled():
                return json_error(403, "invite_issuance_disabled", detail="Member invite issuance is disabled by the administrator")
            salt = dbm.read_id_salt(c_conn)
        finally:
            c_conn.close()

        content_type = request.headers.get("content-type", "")
        is_json_request = "application/json" in content_type.lower()
        if is_json_request:
            try:
                body = await request.json()
            except Exception:
                return json_error(400, "invalid_json_body")
            if not isinstance(body, dict):
                return json_error(400, "invalid_json_body")
        else:
            form = await request.form()
            body = dict(form)

        raw_max_uses = body.get("max_uses", 1)
        if isinstance(raw_max_uses, (bool, float)):
            return json_error(400, "invalid_max_uses", detail="max_uses must be an integer between 1 and 5")
        try:
            max_uses = int(raw_max_uses)
        except (ValueError, TypeError):
            return json_error(400, "invalid_max_uses", detail="max_uses must be an integer between 1 and 5")

        expires_in = None
        if body.get("expires_in") is not None:
            raw_expires_in = body["expires_in"]
            if isinstance(raw_expires_in, bool):
                return json_error(400, "invalid_expires_in", detail="expires_in must be a number")
            try:
                expires_in = float(raw_expires_in)
            except (ValueError, TypeError):
                return json_error(400, "invalid_expires_in", detail="expires_in must be a number")
            if not math.isfinite(expires_in):
                return json_error(400, "invalid_expires_in", detail="expires_in must be a number")

        expires_at = None
        if body.get("expires_at") is not None:
            raw_expires_at = body["expires_at"]
            if isinstance(raw_expires_at, bool):
                return json_error(400, "invalid_expires_at", detail="expires_at must be a number")
            try:
                expires_at = float(raw_expires_at)
            except (ValueError, TypeError):
                return json_error(400, "invalid_expires_at", detail="expires_at must be a number")
            if not math.isfinite(expires_at):
                return json_error(400, "invalid_expires_at", detail="expires_at must be a number")

        raw_reserved = body.get("reserved_username")
        if raw_reserved is not None and not isinstance(raw_reserved, str):
            return json_error(400, "invalid_invite_params", detail="reserved_username must be a string")
        reserved_username = raw_reserved.strip() or None if isinstance(raw_reserved, str) else None

        raw_note = body.get("note")
        note = "" if raw_note is None else str(raw_note)

        auth_conn = open_auth_db(ctx)
        try:
            info = auth_db.create_invitation(
                auth_conn,
                created_by=principal.account_id,
                max_uses=max_uses,
                expires_in=expires_in,
                expires_at=expires_at,
                reserved_username=reserved_username,
                note=note,
                id_salt=salt,
            )
            auth_db.record_admin_audit(
                auth_conn,
                actor_account_id=principal.account_id,
                action="create_invitation",
                detail={"public_id": ids.format_public_id(info.public_id), "max_uses": info.max_uses},
            )
        except InviteValidationError as exc:
            return json_error(400, "invalid_invite_params", detail=str(exc))
        finally:
            auth_conn.close()

        return JSONResponse(
            {
                "public_id": ids.format_public_id(info.public_id),
                "code": info.code,
                "url": f"{ctx.config.public_origin}/invite/{info.code}",
                "max_uses": info.max_uses,
                "uses_count": info.uses_count,
                "expires_at": info.expires_at,
                "created_at": info.created_at,
                "reserved_username": info.reserved_username,
                "note": info.note,
                "status": info.status,
                "created_by": info.created_by,
                "is_mine": True,
                "can_revoke": True,
            },
            status_code=201,
            headers={"Cache-Control": "no-store"},
        )

    async def api_invites_patch(request: Request) -> Response:
        if not await asyncio.to_thread(_is_network_mode, ctx):
            return not_found()

        principal = request_principal(request)
        if not _is_owner(principal):
            return not_found()

        content_type = request.headers.get("content-type", "")
        is_json_request = "application/json" in content_type.lower()
        if is_json_request:
            try:
                body = await request.json()
            except Exception:
                return json_error(400, "invalid_json_body")
            if not isinstance(body, dict):
                return json_error(400, "invalid_json_body")
        else:
            form = await request.form()
            body = dict(form)

        if "action" in body:
            if body["action"] != "revoke":
                return json_error(400, "invalid_action", detail="action must be 'revoke'")

        public_id = body.get("public_id")
        code = body.get("code")
        inv_id = body.get("id")

        auth_conn = open_auth_db(ctx)
        try:
            item: InvitationInfo | None = None
            if code is not None and code != "":
                norm = normalize_invite_code(str(code))
                try:
                    item = auth_db.get_invitation_by_code(auth_conn, norm, check_active=False)
                except InviteNotFoundError:
                    return json_error(404, "invitation_not_found")
            elif public_id is not None and public_id != "":
                if isinstance(public_id, bool):
                    return json_error(400, "invalid_public_id")
                try:
                    parsed_pub_id = ids.parse_public_id(public_id if isinstance(public_id, int) else str(public_id))
                except ValueError:
                    return json_error(400, "invalid_public_id")
                item = auth_db.get_invitation_by_id(auth_conn, parsed_pub_id)
            elif inv_id is not None and inv_id != "":
                if isinstance(inv_id, (bool, float)):
                    return json_error(400, "invalid_id")
                try:
                    parsed_id = int(inv_id)
                except (ValueError, TypeError):
                    return json_error(400, "invalid_id")
                if parsed_id < 1 or parsed_id > ids.MAX_NUMBER:
                    return json_error(400, "invalid_id")
                item = auth_db.get_invitation_by_id(auth_conn, parsed_id)
            else:
                return json_error(400, "missing_identifier", detail="public_id, id, or code is required")

            if item is None or (not _is_admin(principal) and item.created_by != principal.account_id):
                return json_error(404, "invitation_not_found")

            revoked = auth_db.revoke_invitation(auth_conn, item.id, revoked_by=principal.account_id)
            if not revoked:
                return json_error(404, "invitation_not_found", detail="Invitation not found or already revoked")

            formatted_pub_id = ids.format_public_id(item.public_id)
            auth_db.record_admin_audit(
                auth_conn,
                actor_account_id=principal.account_id,
                action="revoke_invitation",
                detail={"public_id": formatted_pub_id},
            )
        finally:
            auth_conn.close()

        return JSONResponse(
            {
                "revoked": True,
                "id": str(item.id),
                "public_id": formatted_pub_id,
                "status": "revoked",
            },
            headers={"Cache-Control": "no-store"},
        )

    return [
        Route("/invite/{code}", invite_get, methods=["GET", "HEAD"]),
        Route("/invite/{code}", invite_post, methods=["POST"]),
        Route("/api/invites", api_invites_get, methods=["GET"]),
        Route("/api/invites", api_invites_post, methods=["POST"]),
        Route("/api/invites", api_invites_patch, methods=["PATCH"]),
    ]
