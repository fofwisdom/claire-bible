"""OAuth 2.1 authorization server and discovery endpoints for MCP clients.

Supports:
- RFC 9728: OAuth 2.0 Protected Resource Metadata (/.well-known/oauth-protected-resource)
- RFC 8414: OAuth 2.0 Authorization Server Metadata (/.well-known/oauth-authorization-server)
- RFC 7591: Dynamic Client Registration (/oauth/register)
- RFC 6749 / RFC 7636: Authorization Code Grant with PKCE (/oauth/authorize, /oauth/token)
- RFC 8693: OAuth 2.0 Token Exchange
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import secrets
import time
from typing import Any

import httpx
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from ..config import get_settings
from ..store import db as dbm
from .security import WebRuntimeConfig

log = logging.getLogger("claire.api.oauth")


def _verify_pkce(code_verifier: str, code_challenge: str, method: str = "S256") -> bool:
    """Verify PKCE code_verifier against code_challenge (RFC 7636)."""
    if method == "plain":
        return code_verifier == code_challenge
    if method != "S256":
        return False
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    computed = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return computed == code_challenge.rstrip("=")


async def handle_protected_resource_metadata(request: Request) -> Response:
    """GET /.well-known/oauth-protected-resource (RFC 9728)."""
    config: WebRuntimeConfig = request.app.state.runtime_config
    origin = config.public_origin.rstrip("/")
    payload = {
        "resource": f"{origin}/mcp",
        "authorization_servers": [origin],
        "scopes_supported": ["readonly"],
        "bearer_methods_supported": ["header"],
    }
    return JSONResponse(
        payload,
        headers={"Cache-Control": "public, max-age=3600"},
    )


async def handle_authorization_server_metadata(request: Request) -> Response:
    """GET /.well-known/oauth-authorization-server (RFC 8414)."""
    config: WebRuntimeConfig = request.app.state.runtime_config
    origin = config.public_origin.rstrip("/")
    payload = {
        "issuer": origin,
        "authorization_endpoint": f"{origin}/oauth/authorize",
        "token_endpoint": f"{origin}/oauth/token",
        "registration_endpoint": f"{origin}/oauth/register",
        "scopes_supported": ["readonly"],
        "response_types_supported": ["code"],
        "grant_types_supported": [
            "authorization_code",
            "refresh_token",
            "urn:ietf:params:oauth:grant-type:token-exchange",
        ],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": [
            "none",
            "client_secret_post",
            "client_secret_basic",
        ],
    }
    return JSONResponse(
        payload,
        headers={"Cache-Control": "public, max-age=3600"},
    )


async def handle_register(request: Request) -> Response:
    """POST /oauth/register (RFC 7591 Dynamic Client Registration)."""
    config: WebRuntimeConfig = request.app.state.runtime_config
    try:
        data = await request.json()
    except Exception:
        data = {}

    client_name = str(data.get("client_name") or "External MCP Client")
    redirect_uris = data.get("redirect_uris") or []
    if isinstance(redirect_uris, str):
        redirect_uris = [redirect_uris]
    elif not isinstance(redirect_uris, list):
        redirect_uris = []

    client_id = f"claire_mcp_{secrets.token_urlsafe(12)}"
    client_secret = secrets.token_urlsafe(32)

    conn = dbm.connect_existing(config.db_file)
    try:
        dbm.register_oauth_client(
            conn,
            client_id=client_id,
            client_secret=client_secret,
            client_name=client_name,
            redirect_uris=redirect_uris,
        )
    finally:
        conn.close()

    return JSONResponse(
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "client_name": client_name,
            "redirect_uris": redirect_uris,
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "client_secret_post",
        },
        status_code=201,
        headers={"Cache-Control": "no-store"},
    )


def _render_authorize_page(
    client_name: str,
    client_id: str,
    redirect_uri: str,
    state: str,
    code_challenge: str,
    code_challenge_method: str,
    scope: str,
    has_valid_cookie: bool,
    error_msg: str | None = None,
) -> str:
    """Render a clean, modern HTML authorization page."""
    error_html = (
        f'<div style="background:#fee2e2;color:#991b1b;padding:12px;border-radius:8px;margin-bottom:16px;font-size:14px;">⚠️ {error_msg}</div>'
        if error_msg
        else ""
    )

    action_buttons = ""
    if has_valid_cookie:
        action_buttons = f"""
        <form method="POST" action="/oauth/authorize" style="margin-top:20px;">
            <input type="hidden" name="client_id" value="{client_id}">
            <input type="hidden" name="redirect_uri" value="{redirect_uri}">
            <input type="hidden" name="state" value="{state}">
            <input type="hidden" name="code_challenge" value="{code_challenge}">
            <input type="hidden" name="code_challenge_method" value="{code_challenge_method}">
            <input type="hidden" name="scope" value="{scope}">
            <input type="hidden" name="action" value="approve">
            <button type="submit" style="width:100%;padding:14px;background:#2563eb;color:#ffffff;border:none;border-radius:8px;font-size:16px;font-weight:600;cursor:pointer;">
                ✅ 바로 승인하기 (Approve)
            </button>
        </form>
        <form method="POST" action="/oauth/authorize" style="margin-top:10px;">
            <input type="hidden" name="client_id" value="{client_id}">
            <input type="hidden" name="redirect_uri" value="{redirect_uri}">
            <input type="hidden" name="state" value="{state}">
            <input type="hidden" name="action" value="deny">
            <button type="submit" style="width:100%;padding:10px;background:#f3f4f6;color:#4b5563;border:1px solid #d1d5db;border-radius:8px;font-size:14px;cursor:pointer;">
                거절 (Deny)
            </button>
        </form>
        """
    else:
        action_buttons = f"""
        <div id="push-section">
            <button id="push-btn" onclick="requestTelegramPush()" style="width:100%;padding:14px;background:#0284c7;color:#ffffff;border:none;border-radius:8px;font-size:16px;font-weight:600;cursor:pointer;display:flex;align-items:center;justify-content:center;gap:8px;">
                <span>📱 텔레그램으로 승인 요청 보내기</span>
            </button>
            <div id="push-status" style="display:none;margin-top:14px;padding:12px;background:#f0f9ff;border:1px solid #bae6fd;border-radius:8px;font-size:14px;color:#0369a1;text-align:center;">
                <div style="font-weight:600;margin-bottom:4px;">텔레그램 알림 확인 중...</div>
                <div style="font-size:12px;color:#0284c7;">스마트폰의 Claire 텔레그램 봇에서 <b>[승인]</b> 버튼을 눌러주세요.</div>
            </div>
        </div>

        <div style="margin:24px 0 16px;text-align:center;position:relative;">
            <hr style="border:none;border-top:1px solid #e5e7eb;">
            <span style="position:absolute;top:-10px;left:50%;transform:translateX(-50%);background:#ffffff;padding:0 8px;font-size:12px;color:#9ca3af;">또는 세션 토큰으로 즉시 승인</span>
        </div>

        <form method="POST" action="/oauth/authorize">
            <input type="hidden" name="client_id" value="{client_id}">
            <input type="hidden" name="redirect_uri" value="{redirect_uri}">
            <input type="hidden" name="state" value="{state}">
            <input type="hidden" name="code_challenge" value="{code_challenge}">
            <input type="hidden" name="code_challenge_method" value="{code_challenge_method}">
            <input type="hidden" name="scope" value="{scope}">
            <input type="hidden" name="action" value="approve">
            <div style="margin-bottom:12px;">
                <input type="text" name="session_token" placeholder="텔레그램 /web 또는 /webro 토큰 입력" style="width:100%;box-sizing:border-box;padding:10px 12px;border:1px solid #d1d5db;border-radius:6px;font-size:14px;">
            </div>
            <button type="submit" style="width:100%;padding:12px;background:#4b5563;color:#ffffff;border:none;border-radius:6px;font-size:14px;font-weight:600;cursor:pointer;">
                토큰으로 승인
            </button>
        </form>
        """

    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Claire 지식베이스 연결 승인</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
            background: #f8fafc;
            color: #1e293b;
            display: flex;
            align-items: center;
            justify-content: center;
            min-height: 100vh;
            margin: 0;
            padding: 16px;
        }}
        .card {{
            background: #ffffff;
            border: 1px solid #e2e8f0;
            border-radius: 16px;
            box-shadow: 0 4px 6px -1px rgb(0 0 0 / 0.1), 0 2px 4px -2px rgb(0 0 0 / 0.1);
            max-width: 440px;
            width: 100%;
            padding: 32px;
            box-sizing: border-box;
        }}
    </style>
</head>
<body>
    <div class="card">
        <div style="text-align:center;margin-bottom:24px;">
            <div style="font-size:36px;margin-bottom:8px;">💎</div>
            <h2 style="margin:0 0 8px;font-size:20px;font-weight:700;">Claire 지식베이스 연결</h2>
            <p style="margin:0;color:#64748b;font-size:14px;">외부 애플리케이션이 접근 권한을 요청합니다.</p>
        </div>

        {error_html}

        <div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;padding:16px;margin-bottom:20px;">
            <div style="font-size:13px;color:#64748b;margin-bottom:4px;">요청 클라이언트</div>
            <div style="font-size:15px;font-weight:600;color:#0f172a;">{client_name}</div>
            
            <div style="margin-top:12px;font-size:13px;color:#64748b;margin-bottom:4px;">부여되는 권한</div>
            <div style="display:inline-flex;align-items:center;background:#e0f2fe;color:#0369a1;padding:4px 8px;border-radius:6px;font-size:13px;font-weight:600;">
                📖 읽기 전용 (Read-Only)
            </div>
            <div style="font-size:12px;color:#64748b;margin-top:6px;">
                • 지식베이스 검색 및 질문 답변<br>
                • 문서, 노드, 관계 그래프 조회<br>
                • ⚠️ 데이터 수정/삭제/적재 권한은 부여되지 않습니다.
            </div>
        </div>

        {action_buttons}
    </div>

    <script>
    let pollTimer = null;

    async function requestTelegramPush() {{
        const btn = document.getElementById('push-btn');
        const status = document.getElementById('push-status');
        btn.disabled = true;
        btn.style.opacity = '0.6';
        status.style.display = 'block';

        try {{
            const resp = await fetch('/oauth/authorize/telegram-push', {{
                method: 'POST',
                headers: {{ 'Content-Type': 'application/json' }},
                body: JSON.stringify({{
                    client_id: {json.dumps(client_id)},
                    redirect_uri: {json.dumps(redirect_uri)},
                    code_challenge: {json.dumps(code_challenge)},
                    code_challenge_method: {json.dumps(code_challenge_method)},
                    scope: {json.dumps(scope)},
                    state: {json.dumps(state)}
                }})
            }});
            const data = await resp.json();
            if (data.ok && data.nonce) {{
                pollStatus(data.nonce);
            }} else {{
                alert('텔레그램 알림 전송 실패: ' + (data.error || '알 수 없는 오류'));
                btn.disabled = false;
                btn.style.opacity = '1';
                status.style.display = 'none';
            }}
        }} catch (err) {{
            alert('요청 중 오류가 발생했습니다: ' + err);
            btn.disabled = false;
            btn.style.opacity = '1';
            status.style.display = 'none';
        }}
    }}

    function pollStatus(nonce) {{
        pollTimer = setInterval(async () => {{
            try {{
                const resp = await fetch('/oauth/authorize/poll?nonce=' + encodeURIComponent(nonce));
                const data = await resp.json();
                if (data.status === 'approved' && data.redirect_url) {{
                    clearInterval(pollTimer);
                    document.getElementById('push-status').innerHTML = '<div style="color:#16a34a;font-weight:600;">✅ 승인 완료! 연결 화면으로 이동합니다...</div>';
                    setTimeout(() => {{
                        window.location.href = data.redirect_url;
                    }}, 800);
                }} else if (data.status === 'denied') {{
                    clearInterval(pollTimer);
                    alert('승인 요청이 거절되었습니다.');
                    location.reload();
                }}
            }} catch (e) {{
                console.error('Polling error', e);
            }}
        }}, 2000);
    }}
    </script>
</body>
</html>
"""


async def handle_authorize(request: Request) -> Response:
    """GET / POST /oauth/authorize (RFC 6749 & RFC 7636 PKCE)."""
    config: WebRuntimeConfig = request.app.state.runtime_config

    if request.method == "GET":
        params = request.query_params
        client_id = params.get("client_id", "")
        redirect_uri = params.get("redirect_uri", "")
        response_type = params.get("response_type", "")
        code_challenge = params.get("code_challenge", "")
        code_challenge_method = params.get("code_challenge_method", "S256")
        state = params.get("state", "")
        scope = params.get("scope", "readonly")

        if response_type != "code":
            return JSONResponse({"error": "unsupported_response_type"}, status_code=400)
        if not client_id or not redirect_uri:
            return JSONResponse({"error": "invalid_request", "error_description": "Missing client_id or redirect_uri"}, status_code=400)

        conn = dbm.connect_existing(config.db_file)
        try:
            client = dbm.get_oauth_client(conn, client_id)
            client_name = client["client_name"] if client else "Google Gemini / MCP Client"

            cookie_token = request.cookies.get("claire_session")
            has_valid_cookie = False
            if cookie_token and dbm.plausible_session_token(cookie_token):
                v_scope = dbm.validate_session_scope(conn, cookie_token)
                has_valid_cookie = v_scope is not None
        finally:
            conn.close()

        html = _render_authorize_page(
            client_name=client_name,
            client_id=client_id,
            redirect_uri=redirect_uri,
            state=state,
            code_challenge=code_challenge,
            code_challenge_method=code_challenge_method,
            scope="readonly",
            has_valid_cookie=has_valid_cookie,
        )
        return HTMLResponse(html)

    # POST processing
    form = await request.form()
    action = form.get("action", "")
    client_id = form.get("client_id", "")
    redirect_uri = form.get("redirect_uri", "")
    state = form.get("state", "")
    code_challenge = form.get("code_challenge", "")
    code_challenge_method = form.get("code_challenge_method", "S256")
    session_token = form.get("session_token", "").strip()

    if action == "deny":
        delim = "&" if "?" in redirect_uri else "?"
        return RedirectResponse(f"{redirect_uri}{delim}error=access_denied&state={state}", status_code=302)

    # Check authentication
    conn = dbm.connect_existing(config.db_file)
    try:
        authed = False
        cookie_token = request.cookies.get("claire_session")
        if cookie_token and dbm.plausible_session_token(cookie_token):
            if dbm.validate_session_scope(conn, cookie_token) is not None:
                authed = True

        if not authed and session_token:
            if dbm.validate_session_scope(conn, session_token) is not None:
                authed = True

        if not authed:
            client = dbm.get_oauth_client(conn, client_id)
            client_name = client["client_name"] if client else "External Client"
            html = _render_authorize_page(
                client_name=client_name,
                client_id=client_id,
                redirect_uri=redirect_uri,
                state=state,
                code_challenge=code_challenge,
                code_challenge_method=code_challenge_method,
                scope="readonly",
                has_valid_cookie=False,
                error_msg="유효하지 않거나 만료된 세션 토큰입니다.",
            )
            return HTMLResponse(html, status_code=401)

        # Issue authorization code
        code = dbm.create_oauth_code(
            conn,
            client_id=client_id,
            redirect_uri=redirect_uri,
            code_challenge=code_challenge,
            code_challenge_method=code_challenge_method,
            scope="readonly",
        )
    finally:
        conn.close()

    delim = "&" if "?" in redirect_uri else "?"
    return RedirectResponse(f"{redirect_uri}{delim}code={code}&state={state}", status_code=302)


async def handle_telegram_push(request: Request) -> Response:
    """POST /oauth/authorize/telegram-push: Send inline approve button to telegram."""
    config: WebRuntimeConfig = request.app.state.runtime_config
    try:
        data = await request.json()
    except Exception:
        data = {}

    client_id = data.get("client_id", "")
    redirect_uri = data.get("redirect_uri", "")
    code_challenge = data.get("code_challenge", "")
    code_challenge_method = data.get("code_challenge_method", "S256")
    state = data.get("state", "")

    if not client_id or not redirect_uri:
        return JSONResponse({"ok": False, "error": "missing parameters"}, status_code=400)

    conn = dbm.connect_existing(config.db_file)
    try:
        client = dbm.get_oauth_client(conn, client_id)
        client_name = client["client_name"] if client else "Google Gemini / MCP Client"

        nonce = dbm.create_oauth_auth_request(
            conn,
            client_id=client_id,
            redirect_uri=redirect_uri,
            code_challenge=code_challenge,
            code_challenge_method=code_challenge_method,
            scope="readonly",
            state=state,
        )
    finally:
        conn.close()

    # Send telegram push message via Telegram Bot API
    settings = get_settings()
    token = settings.telegram_bot_token
    allowed_ids = settings.allowed_user_ids

    if token and allowed_ids:
        msg_text = (
            "🤖 <b>Claire MCP 연결 승인 요청</b>\n\n"
            f"• 클라이언트: <b>{client_name}</b>\n"
            "• 권한: <b>readonly</b> (지식 검색 및 조회)\n\n"
            "외부 에이전트의 연결을 승인하시겠습니까?"
        )
        keyboard = {
            "inline_keyboard": [
                [
                    {"text": "✅ 승인 (Approve)", "callback_data": f"oauth_appr:{nonce}"},
                    {"text": "❌ 거절 (Deny)", "callback_data": f"oauth_deny:{nonce}"},
                ]
            ]
        }
        async with httpx.AsyncClient(timeout=10.0) as client_http:
            for chat_id in allowed_ids:
                try:
                    await client_http.post(
                        f"https://api.telegram.org/bot{token}/sendMessage",
                        json={
                            "chat_id": chat_id,
                            "text": msg_text,
                            "parse_mode": "HTML",
                            "reply_markup": keyboard,
                        },
                    )
                except Exception as exc:
                    log.warning("Failed to send telegram oauth push to %s: %s", chat_id, exc)

    return JSONResponse({"ok": True, "nonce": nonce})


async def handle_authorize_poll(request: Request) -> Response:
    """GET /oauth/authorize/poll?nonce=..."""
    config: WebRuntimeConfig = request.app.state.runtime_config
    nonce = request.query_params.get("nonce", "")
    if not nonce:
        return JSONResponse({"status": "error", "message": "missing nonce"}, status_code=400)

    conn = dbm.connect_existing(config.db_file)
    try:
        req = dbm.get_oauth_auth_request(conn, nonce)
        if not req:
            return JSONResponse({"status": "denied"})
        if req["approved"] and req.get("code"):
            redirect_uri = req["redirect_uri"]
            delim = "&" if "?" in redirect_uri else "?"
            state_param = f"&state={req['state']}" if req.get("state") else ""
            return JSONResponse({
                "status": "approved",
                "redirect_url": f"{redirect_uri}{delim}code={req['code']}{state_param}",
            })
        return JSONResponse({"status": "pending"})
    finally:
        conn.close()


async def handle_token(request: Request) -> Response:
    """POST /oauth/token (RFC 6749, RFC 7636 PKCE, RFC 8693 Token Exchange)."""
    config: WebRuntimeConfig = request.app.state.runtime_config

    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            params = await request.json()
        except Exception:
            params = {}
    else:
        form = await request.form()
        params = dict(form)

    grant_type = params.get("grant_type", "")

    # 1. Authorization Code Grant
    if grant_type == "authorization_code":
        code = str(params.get("code") or "")
        client_id = str(params.get("client_id") or "")
        redirect_uri = str(params.get("redirect_uri") or "")
        code_verifier = str(params.get("code_verifier") or "")

        if not code or not client_id or not redirect_uri:
            return JSONResponse(
                {"error": "invalid_request", "error_description": "Missing required parameters"},
                status_code=400,
            )

        conn = dbm.connect_existing(config.db_file)
        try:
            code_data = dbm.consume_oauth_code(conn, code, client_id, redirect_uri)
            if not code_data:
                return JSONResponse(
                    {"error": "invalid_grant", "error_description": "Invalid, expired, or consumed code"},
                    status_code=400,
                )

            challenge = code_data["code_challenge"]
            method = code_data.get("code_challenge_method", "S256")
            if not _verify_pkce(code_verifier, challenge, method):
                return JSONResponse(
                    {"error": "invalid_grant", "error_description": "PKCE verification failed"},
                    status_code=400,
                )

            access_token, refresh_token = dbm.create_oauth_token(
                conn, client_id=client_id, scope="readonly"
            )
        finally:
            conn.close()

        return JSONResponse(
            {
                "access_token": access_token,
                "token_type": "Bearer",
                "expires_in": int(dbm.OAUTH_ACCESS_TOKEN_TTL),
                "refresh_token": refresh_token,
                "scope": "readonly",
            },
            headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
        )

    # 2. Refresh Token Grant
    if grant_type == "refresh_token":
        refresh_token = str(params.get("refresh_token") or "")
        client_id = str(params.get("client_id") or "")

        if not refresh_token:
            return JSONResponse(
                {"error": "invalid_request", "error_description": "Missing refresh_token"},
                status_code=400,
            )

        conn = dbm.connect_existing(config.db_file)
        try:
            res = dbm.refresh_oauth_token(conn, refresh_token, client_id if client_id else None)
            if not res:
                return JSONResponse(
                    {"error": "invalid_grant", "error_description": "Invalid or expired refresh_token"},
                    status_code=400,
                )
            new_access, new_refresh, scope = res
        finally:
            conn.close()

        return JSONResponse(
            {
                "access_token": new_access,
                "token_type": "Bearer",
                "expires_in": int(dbm.OAUTH_ACCESS_TOKEN_TTL),
                "refresh_token": new_refresh,
                "scope": "readonly",
            },
            headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
        )

    # 3. RFC 8693 Token Exchange
    if grant_type == "urn:ietf:params:oauth:grant-type:token-exchange":
        subject_token = str(params.get("subject_token") or "")
        subject_token_type = str(params.get("subject_token_type") or "")

        if not subject_token:
            return JSONResponse(
                {"error": "invalid_request", "error_description": "Missing subject_token"},
                status_code=400,
            )

        conn = dbm.connect_existing(config.db_file)
        try:
            # Check session or bootstrap
            exchanged = dbm.exchange_session_token(
                conn, subject_token, scopes=("owner", "collaborator", "readonly")
            )
            if not exchanged:
                # Also check if it's already an active session token
                v_scope = dbm.validate_session_scope(
                    conn, subject_token, scopes=("owner", "collaborator", "readonly")
                )
                if not v_scope:
                    return JSONResponse(
                        {"error": "invalid_grant", "error_description": "Subject token is invalid or expired"},
                        status_code=400,
                    )

            # Issue isolated oauth token
            access_token, refresh_token = dbm.create_oauth_token(
                conn, client_id="token_exchange_client", scope="readonly"
            )
        finally:
            conn.close()

        return JSONResponse(
            {
                "access_token": access_token,
                "issued_token_type": "urn:ietf:params:oauth:token-type:access_token",
                "token_type": "Bearer",
                "expires_in": int(dbm.OAUTH_ACCESS_TOKEN_TTL),
                "scope": "readonly",
                "refresh_token": refresh_token,
            },
            headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
        )

    return JSONResponse(
        {"error": "unsupported_grant_type", "error_description": f"Unsupported grant_type: {grant_type}"},
        status_code=400,
    )
