"""Invitation HTTP routes (설계 §8.8, §13.3, §15 S12, P3 L3-b).

- 싱글 모드에서는 모든 초대 경로 404 반환. 네트워크 모드에서만 활성화.
- GET /invite/{code}: public, X-Robots-Tag: noindex, nofollow, 외부 링크 rel="noreferrer noopener".
  GET/HEAD는 일절 uses_count 차감 및 실패 카운트 증가 없음(무부작용).
- POST /invite/{code}: public 로컬 가입 제출 (username, display_name, password).
  원자적 초대 차감 및 계정 생성.
- GET /api/invites: 관리자 전용 컬렉션 목록.
- POST /api/invites: 관리자 전용 초대 생성.
- PATCH /api/invites: 관리자 전용 초대 비활성화.
- IP당 잘못된 코드 조회 제한 (분당 10회 초과 시 429 / 지연).
"""

from __future__ import annotations

import asyncio
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
from ... import ids
from .common import (
    AccountsContext,
    client_ip,
    escape,
    forbidden,
    json_error,
    not_found,
    open_auth_db,
    open_db,
    retry_after_seconds,
    same_origin,
    set_session_cookie,
    validate_display_name,
)


def _is_network_mode(ctx: AccountsContext) -> bool:
    conn = open_db(ctx)
    try:
        return accounts_store.AccountStore(conn).instance_mode() == "network"
    finally:
        conn.close()


def _is_admin(principal: Principal) -> bool:
    return principal.instance_role == "admin" and principal.scope == "owner" and principal.account_id is not None


def _wants_json(request: Request) -> bool:
    if request.query_params.get("json") == "1":
        return True
    accept = request.headers.get("accept", "").lower()
    return "application/json" in accept and "text/html" not in accept


def _render_invite_html(info: InvitationInfo, instance_name: str, code: str) -> str:
    reserved_note = f"<p>이 초대는 <strong>@{escape(info.reserved_username)}</strong> 전용입니다.</p>" if info.reserved_username else ""
    readonly_attr = ' readonly' if info.reserved_username else ''
    val_username = escape(info.reserved_username or '')
    return (
        "<!doctype html>"
        "<html lang=\"ko\">"
        "<head>"
        "<meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        "<meta name=\"robots\" content=\"noindex, nofollow\">"
        f"<title>{escape(instance_name)} 초대 가입</title>"
        "<style>"
        "body { font-family: system-ui, -apple-system, sans-serif; max-width: 440px; margin: 40px auto; padding: 20px; line-height: 1.5; color: #222; }"
        "h1 { font-size: 1.4rem; margin-bottom: 0.5rem; }"
        "label { display: block; margin-top: 1rem; font-weight: 600; font-size: 0.9rem; }"
        "input[type=\"text\"], input[type=\"password\"] { width: 100%; box-sizing: border-box; padding: 8px 10px; margin-top: 4px; font-size: 1rem; border: 1px solid #ccc; border-radius: 4px; }"
        "button { width: 100%; margin-top: 1.5rem; padding: 10px; font-size: 1rem; font-weight: 600; background: #0056b3; color: #fff; border: 0; border-radius: 4px; cursor: pointer; }"
        ".footer { margin-top: 2rem; font-size: 0.8rem; color: #666; text-align: center; }"
        ".footer a { color: #0056b3; }"
        "</style>"
        "</head>"
        "<body>"
        f"<h1>{escape(instance_name)} 가입</h1>"
        f"<p>초대 코드: <code>{escape(code)}</code></p>"
        f"{reserved_note}"
        f"<form method=\"POST\" action=\"/invite/{escape(code)}\">"
        f"<label for=\"username\">USERNAME</label>"
        f"<input id=\"username\" name=\"username\" type=\"text\" value=\"{val_username}\" required autocomplete=\"username\"{readonly_attr}>"
        "<label for=\"display_name\">표시 이름</label>"
        "<input id=\"display_name\" name=\"display_name\" type=\"text\" placeholder=\"홍길동\" required autocomplete=\"name\">"
        "<label for=\"password\">비밀번호</label>"
        "<input id=\"password\" name=\"password\" type=\"password\" required autocomplete=\"new-password\" minlength=\"12\">"
        "<button type=\"submit\">계정 만들기</button>"
        "</form>"
        "<div class=\"footer\">"
        "<a href=\"https://github.com/kim-home/claire-bible\" target=\"_blank\" rel=\"noreferrer noopener\">Claire Bible</a>"
        "</div>"
        "</body>"
        "</html>"
    )


def _render_inactive_html(reason: str) -> str:
    messages = {
        "expired": "만료된 초대 링크입니다.",
        "exhausted": "모두 사용된 초대 링크입니다.",
        "revoked": "관리자에 의해 비활성화된 초대 링크입니다.",
    }
    msg = messages.get(reason, "사용할 수 없는 초대 링크입니다.")
    return (
        "<!doctype html>"
        "<html lang=\"ko\">"
        "<head>"
        "<meta charset=\"utf-8\">"
        "<meta name=\"robots\" content=\"noindex, nofollow\">"
        "<title>초대 링크 비활성</title>"
        "<style>body { font-family: system-ui, sans-serif; max-width: 440px; margin: 40px auto; padding: 20px; line-height: 1.5; color: #444; }</style>"
        "</head>"
        "<body>"
        "<h1>초대 링크를 사용할 수 없습니다</h1>"
        f"<p>{escape(msg)}</p>"
        "<p><a href=\"/\" rel=\"noreferrer noopener\">홈으로 이동</a></p>"
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

        auth_conn = open_auth_db(ctx)
        try:
            info = auth_db.get_invitation_by_code(auth_conn, norm_code, check_active=True)
        except InviteNotFoundError:
            auth_db.record_invite_failure(auth_conn, ip=ip)
            return not_found()
        except InviteInactiveError as exc:
            headers = {"X-Robots-Tag": "noindex, nofollow", "Cache-Control": "no-store"}
            if _wants_json(request):
                return JSONResponse({"error": f"invitation_{exc.reason}", "reason": exc.reason}, status_code=410, headers=headers)
            return HTMLResponse(_render_inactive_html(exc.reason), status_code=410, headers=headers)
        finally:
            auth_conn.close()

        headers = {"X-Robots-Tag": "noindex, nofollow", "Cache-Control": "no-store"}
        if request.method == "HEAD":
            return Response(status_code=200, headers=headers)

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
                headers=headers,
            )

        instance_name = ctx.config.expected_authority or "Claire"
        return HTMLResponse(_render_invite_html(info, instance_name, norm_code), headers=headers)

    async def invite_post(request: Request) -> Response:
        if not await asyncio.to_thread(_is_network_mode, ctx):
            return not_found()

        if not same_origin(request):
            return forbidden()

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
            return json_error(410, f"invitation_{exc.reason}", reason=exc.reason)
        finally:
            auth_conn.close()

        content_type = request.headers.get("content-type", "")
        is_json_request = "application/json" in content_type
        if is_json_request:
            try:
                body = await request.json()
            except Exception:
                return json_error(400, "invalid_json_body")
        else:
            form = await request.form()
            body = dict(form)

        username = str(body.get("username", "")).strip()
        display_name = str(body.get("display_name", "")).strip()
        password = str(body.get("password", ""))

        if info.reserved_username and username.lower() != info.reserved_username.lower():
            return json_error(400, "reserved_username_mismatch", detail=f"This invitation is reserved for username '{info.reserved_username}'")

        conn = open_db(ctx)
        try:
            store = accounts_store.AccountStore(conn)
            taken_usernames = {a.username.lower() for a in store.list_accounts() if a.username}
            if store.is_username_retired(username):
                return json_error(400, "username_retired", detail="Username is retired")
        finally:
            conn.close()

        if username.lower() in taken_usernames:
            return json_error(400, "username_taken", detail="Username is already taken")

        try:
            username = validate_username(username, instance_reserved=ctx.instance_reserved)
        except UsernameRejected as exc:
            return json_error(400, f"username_{exc.reason}", detail=str(exc))

        if validate_display_name(display_name) is None:
            return json_error(400, "invalid_display_name", detail="Display name is invalid")

        try:
            auth_db.check_password_policy(password, username=username)
        except auth_db.PasswordRejected as exc:
            return json_error(400, f"password_{exc.reason}", detail=str(exc))

        def _create_account_and_theme() -> int:
            c_conn = open_db(ctx)
            try:
                salt = dbm.read_id_salt(c_conn)
                now_ts = time.time()
                account_pub_id = ids.timestamp_id("accounts", salt=salt, unix_ms=int(now_ts * 1000))
                with c_conn:
                    cur = c_conn.execute(
                        "INSERT INTO accounts (public_id, username, role, status, display_name, profile_public, created_at, activated_at, updated_at) "
                        "VALUES (?, ?, 'member', 'active', ?, 0, ?, ?, ?)",
                        (account_pub_id, username, display_name, now_ts, now_ts, now_ts),
                    )
                    new_acc_id = int(cur.lastrowid)

                    theme_seq_row = c_conn.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM themes").fetchone()
                    theme_id = int(theme_seq_row[0])
                    theme_num = ids.timestamp_id("themes", salt=salt, unix_ms=int(now_ts * 1000) + 1)
                    c_conn.execute(
                        "INSERT INTO themes (id, seq, number, label, account_id, is_default, is_public, created_at, updated_at, db_path) "
                        "VALUES (?, ?, ?, 'General', ?, 1, 0, ?, ?, ?)",
                        (theme_id, theme_id, theme_num, new_acc_id, now_ts, now_ts, str(ctx.db_file)),
                    )
                return new_acc_id
            finally:
                c_conn.close()

        auth_conn = open_auth_db(ctx)
        try:
            account_id = auth_db.redeem_invitation_atomic(
                auth_conn,
                info.id,
                method="local",
                account_callback=_create_account_and_theme,
            )
            auth_db.set_password(auth_conn, account_id, password, username=username)
        finally:
            auth_conn.close()

        c_conn = open_db(ctx)
        try:
            session_token = accounts_store.create_account_session(c_conn, account_id=account_id, scope="owner")
        finally:
            c_conn.close()

        if is_json_request or _wants_json(request):
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
        if not _is_admin(principal):
            return not_found()

        query = parse_collection_query(request.query_params)
        auth_conn = open_auth_db(ctx)
        try:
            items, total_count = auth_db.list_invitations(auth_conn, query)
        finally:
            auth_conn.close()

        base_url = f"{ctx.config.public_origin}/api/invites"
        if not query.page:
            data = collection_root(collection_url=base_url, total_items=total_count)
        else:
            is_first = (query.max_id is None and query.min_id is None and query.since_id is None)
            data = collection_page(collection_url=base_url, query=query, items=items, item_number="public_id", is_first_page=is_first)

        etag = collection_etag([f"{it['public_id']}:{it.get('status')}:{it.get('uses_count')}" for it in items])
        headers = collection_cache_headers(anonymous=False, etag=etag)
        return JSONResponse(data, headers=headers)

    async def api_invites_post(request: Request) -> Response:
        if not await asyncio.to_thread(_is_network_mode, ctx):
            return not_found()

        principal = request_principal(request)
        if not _is_admin(principal):
            return not_found()

        content_type = request.headers.get("content-type", "")
        is_json_request = "application/json" in content_type
        if is_json_request:
            try:
                body = await request.json()
            except Exception:
                return json_error(400, "invalid_json_body")
        else:
            form = await request.form()
            body = dict(form)

        try:
            max_uses = int(body.get("max_uses", 1))
        except (ValueError, TypeError):
            return json_error(400, "invalid_max_uses", detail="max_uses must be an integer between 1 and 5")

        expires_in = None
        if body.get("expires_in") is not None:
            try:
                expires_in = float(body["expires_in"])
            except (ValueError, TypeError):
                return json_error(400, "invalid_expires_in", detail="expires_in must be a number")

        expires_at = None
        if body.get("expires_at") is not None:
            try:
                expires_at = float(body["expires_at"])
            except (ValueError, TypeError):
                return json_error(400, "invalid_expires_at", detail="expires_at must be a number")

        reserved_username = body.get("reserved_username")
        note = str(body.get("note", ""))

        c_conn = open_db(ctx)
        try:
            salt = dbm.read_id_salt(c_conn)
        finally:
            c_conn.close()

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
                detail={"public_id": str(info.public_id), "max_uses": info.max_uses},
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
            },
            status_code=201,
        )

    async def api_invites_patch(request: Request) -> Response:
        if not await asyncio.to_thread(_is_network_mode, ctx):
            return not_found()

        principal = request_principal(request)
        if not _is_admin(principal):
            return not_found()

        content_type = request.headers.get("content-type", "")
        is_json_request = "application/json" in content_type
        if is_json_request:
            try:
                body = await request.json()
            except Exception:
                return json_error(400, "invalid_json_body")
        else:
            form = await request.form()
            body = dict(form)

        public_id = body.get("public_id")
        code = body.get("code")
        inv_id = body.get("id")

        auth_conn = open_auth_db(ctx)
        try:
            target_id: int | None = None
            if code:
                norm = normalize_invite_code(str(code))
                try:
                    item = auth_db.get_invitation_by_code(auth_conn, norm, check_active=False)
                    target_id = item.id
                except InviteNotFoundError:
                    return json_error(404, "invitation_not_found")
            elif public_id:
                try:
                    target_id = ids.parse_public_id(str(public_id))
                except ValueError:
                    return json_error(400, "invalid_public_id")
            elif inv_id:
                try:
                    target_id = int(inv_id)
                except (ValueError, TypeError):
                    return json_error(400, "invalid_id")
            else:
                return json_error(400, "missing_identifier", detail="public_id, id, or code is required")

            revoked = auth_db.revoke_invitation(auth_conn, target_id, revoked_by=principal.account_id)
            if not revoked:
                return json_error(404, "invitation_not_found", detail="Invitation not found or already revoked")

            auth_db.record_admin_audit(
                auth_conn,
                actor_account_id=principal.account_id,
                action="revoke_invitation",
                detail={"public_id": str(target_id)},
            )
        finally:
            auth_conn.close()

        return JSONResponse({"revoked": True, "id": str(target_id)})

    return [
        Route("/invite/{code}", invite_get, methods=["GET", "HEAD"]),
        Route("/invite/{code}", invite_post, methods=["POST"]),
        Route("/api/invites", api_invites_get, methods=["GET"]),
        Route("/api/invites", api_invites_post, methods=["POST"]),
        Route("/api/invites", api_invites_patch, methods=["PATCH"]),
    ]
