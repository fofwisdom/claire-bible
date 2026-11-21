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
import hmac
import html as _html
import json
import logging
import secrets
import sqlite3
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode, urlparse

import httpx
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from ..config import get_settings
from ..core.ports import get_web_ui_renderer
from ..i18n import get_locale, t
from ..notify import push_locale
from ..store import accounts as accounts_store
from ..store import auth_db
from ..store import db as dbm
from ..store import telegram_bots
from ..store.secrets import get_secret_service
from .accounts_routes.common import same_origin
from .principal import STEP_UP_WINDOW_SECONDS, request_principal
from .security import (
    WebRuntimeConfig,
    clear_oauth_tx_cookie,
    client_ip,
    read_oauth_tx_cookie,
    read_session_cookie,
    set_oauth_tx_cookie,
)

log = logging.getLogger("claire.api.oauth")


def _is_safe_redirect_uri(uri: str) -> bool:
    """Check that redirect_uri is non-empty, not an opaque null origin, and has a safe scheme."""
    if not uri or not isinstance(uri, str):
        return False
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in uri):
        return False
    cleaned = uri.strip()
    if not cleaned or cleaned.lower() == "null":
        return False
    parsed = urlparse(cleaned)
    if not parsed.scheme or parsed.scheme.lower() in ("javascript", "data", "vbscript", "file", "blob", "about", "filesystem"):
        return False
    host = (parsed.hostname or "").strip().lower()
    netloc = (parsed.netloc or "").strip().lower()
    if host == "null" or netloc == "null":
        return False
    return True


def _session_has_recent_auth(authenticated_at: float | None) -> bool:
    """Return True when session cookie authentication occurred within the step-up window."""
    if authenticated_at is None:
        return False
    elapsed = time.time() - float(authenticated_at)
    return 0 <= elapsed <= STEP_UP_WINDOW_SECONDS


def _svg_icon(name: str) -> str:
    """Render a small monochrome currentColor SVG icon (PRINCIPLES.md G1)."""
    paths = {
        "shield": '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>',
        "alert": '<path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>',
        "check": '<polyline points="20 6 9 17 4 12"/>',
        "book": '<path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z"/><path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z"/>',
        "device": '<rect x="2" y="3" width="20" height="14" rx="2" ry="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/>',
    }
    inner = paths.get(name, paths["shield"])
    return f'<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="flex-shrink:0;vertical-align:text-bottom;" aria-hidden="true">{inner}</svg>'


def _account_passkey_descriptors(config: WebRuntimeConfig, account_id: int) -> list[dict[str, str]]:
    """Return WebAuthn PublicKeyCredentialDescriptor records owned by an account, if registered."""
    data_dir = config.data_dir or (Path(config.db_file).parent if config.db_file else None)
    if data_dir is None:
        return []
    auth_file = auth_db.auth_db_path(data_dir)
    if not auth_file.is_file():
        return []
    conn = sqlite3.connect(auth_file, timeout=5.0)
    conn.row_factory = sqlite3.Row
    try:
        exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='webauthn_credentials'").fetchone()
        if exists is None:
            return []
        rows = conn.execute("SELECT credential_id FROM webauthn_credentials WHERE account_id=?", (int(account_id),)).fetchall()
        return [{"type": "public-key", "id": str(r["credential_id"])} for r in rows if r["credential_id"]]
    except sqlite3.Error:
        return []
    finally:
        conn.close()


def _verify_step_up_password(config: WebRuntimeConfig, conn: sqlite3.Connection, account_id: int, ip: str, password: str, session_token: str) -> str | None:
    """Verify inline current password for write-scope step-up and return None on success or an i18n error message."""
    if not password:
        return t("auth.oauth.step_up_missing")
    data_dir = config.data_dir or (Path(config.db_file).parent if config.db_file else None)
    if data_dir is None:
        return t("auth.oauth.step_up_invalid")
    auth_file = auth_db.auth_db_path(data_dir)
    if not auth_file.is_file():
        return t("auth.oauth.step_up_invalid")
    auth = sqlite3.connect(auth_file, timeout=5.0)
    auth.row_factory = sqlite3.Row
    try:
        acc = accounts_store.AccountStore(conn).get(int(account_id))
        key_name = acc.username if acc.username is not None else f"#account:{acc.id}"
        if not auth_db.has_password(auth, account_id):
            return t("auth.oauth.step_up_invalid")
        decision = auth_db.begin_login_attempt(auth, username=key_name, ip=ip)
        if not decision.allowed:
            return t("auth.login.throttled")
        if not auth_db.verify_password(auth, account_id, password):
            return t("auth.oauth.step_up_invalid")
        auth_db.settle_login_success(auth, username=key_name, ip=ip)
        accounts_store.mark_session_authenticated(conn, session_token)
        return None
    finally:
        auth.close()


class ClientCredentialConflictError(ValueError):
    """Raised when OAuth client credentials in Basic authorization and request parameters conflict or are malformed."""


def _extract_client_credentials(request: Request, params: dict[str, Any]) -> tuple[str | None, str | None]:
    """Extract client_id and client_secret from Basic Authorization header or body parameters."""
    req_client_id = str(params.get("client_id") or "").strip() or None
    req_secret = str(params.get("client_secret") or "").strip() or None
    auth_header = request.headers.get("authorization", "")
    if auth_header.lower().startswith("basic "):
        raw_b64 = auth_header[6:].strip()
        try:
            decoded = base64.b64decode(raw_b64.encode("ascii"), validate=True).decode("latin-1")
        except Exception as exc:
            raise ClientCredentialConflictError("Invalid HTTP Basic authorization header") from exc
        if ":" not in decoded:
            raise ClientCredentialConflictError("Invalid HTTP Basic authorization header")
        basic_id, basic_secret = decoded.split(":", 1)
        if not basic_id:
            raise ClientCredentialConflictError("Missing client_id in HTTP Basic authorization header")
        if req_client_id and req_client_id != basic_id:
            raise ClientCredentialConflictError("Conflicting client_id in Authorization header and request parameters")
        if req_secret and basic_secret and req_secret != basic_secret:
            raise ClientCredentialConflictError("Conflicting client_secret in Authorization header and request parameters")
        if not req_client_id:
            req_client_id = basic_id
        if not req_secret and basic_secret:
            req_secret = basic_secret
    return req_client_id, req_secret


def _resolve_client_credentials(request: Request, params: dict[str, Any]) -> tuple[str | None, str | None, JSONResponse | None]:
    """Resolve client credentials or return an invalid_client JSON response on conflicting Basic and body inputs."""
    try:
        req_cid, req_secret = _extract_client_credentials(request, params)
    except ClientCredentialConflictError as exc:
        return None, None, JSONResponse({"error": "invalid_client", "error_description": str(exc)}, status_code=401, headers={"Cache-Control": "no-store"})
    return req_cid, req_secret, None


def _extract_client_secret(request: Request, params: dict[str, Any]) -> str | None:
    """Extract client_secret from Authorization header or body parameters."""
    _cid, secret_val = _extract_client_credentials(request, params)
    return secret_val


def _h(value: Any) -> str:
    """Escape value for HTML text and quoted attributes."""
    return _html.escape("" if value is None else str(value), quote=True)


def _verify_pkce(code_verifier: str, code_challenge: str, method: str = "S256") -> bool:
    """Verify PKCE S256 code_verifier against code_challenge (OAuth 2.1 requires S256)."""
    if method != "S256" or not code_verifier or not code_challenge:
        return False
    try:
        digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    except UnicodeEncodeError:
        return False
    computed = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return computed == code_challenge.rstrip("=")


async def handle_protected_resource_metadata(request: Request) -> Response:
    """GET /.well-known/oauth-protected-resource (RFC 9728)."""
    config: WebRuntimeConfig = request.app.state.runtime_config
    origin = config.public_origin.rstrip("/")
    payload = {
        "resource": f"{origin}/mcp",
        "authorization_servers": [origin],
        "scopes_supported": ["readonly", "write"],
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
        "revocation_endpoint": f"{origin}/oauth/revoke",
        "device_authorization_endpoint": f"{origin}/oauth/device_authorization",
        "scopes_supported": ["readonly", "write"],
        "response_types_supported": ["code"],
        "grant_types_supported": [
            "authorization_code",
            "refresh_token",
            "urn:ietf:params:oauth:grant-type:device_code",
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

    # Scheme validation: block dangerous schemes like javascript:, data:, vbscript:
    for uri in redirect_uris:
        if not _is_safe_redirect_uri(str(uri)):
            return JSONResponse(
                {"error": "invalid_redirect_uri", "error_description": "Invalid or dangerous redirect_uri scheme"},
                status_code=400,
            )

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
            "grant_types": ["authorization_code", "refresh_token", "urn:ietf:params:oauth:grant-type:device_code"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "client_secret_post",
        },
        status_code=201,
        headers={"Cache-Control": "no-store"},
    )


_OAUTH_PAGE_CSS = """
:root { color-scheme: light dark; --bg: #f7f7f5; --fg: #1d1d1b; --muted: #5d5d58; --card: #ffffff; --line: #d9d9d4; --accent: #2f5d8a; --danger: #a3261d; --danger-bg: #fdeceb; }
@media (prefers-color-scheme: dark) { :root { --bg: #161615; --fg: #ececea; --muted: #a5a5a0; --card: #20201e; --line: #3a3a37; --accent: #8db6e0; --danger: #f08a80; --danger-bg: #3b1c1a; } }
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--fg); font: 16px/1.6 system-ui, -apple-system, "Noto Sans KR", sans-serif; display: flex; justify-content: center; padding: 2rem 1rem 3rem; }
main { max-width: 32rem; width: 100%; }
.card { background: var(--card); border: 1px solid var(--line); border-radius: 12px; padding: 1.5rem; margin-bottom: 1rem; }
.head { display: flex; align-items: center; gap: .75rem; margin-bottom: 1rem; }
.head h1 { margin: 0; font-size: 1.35rem; }
.hint { color: var(--muted); font-size: .9rem; margin: .25rem 0 .75rem; }
.meta { background: var(--bg); border: 1px solid var(--line); border-radius: 8px; padding: 1rem; margin: 1rem 0; }
.meta dt { font-size: .8rem; color: var(--muted); text-transform: uppercase; letter-spacing: .04em; margin-top: .5rem; }
.meta dt:first-child { margin-top: 0; }
.meta dd { margin: .15rem 0 0; font-weight: 600; overflow-wrap: anywhere; }
.scope-badge { display: inline-flex; align-items: center; gap: .4rem; border: 1px solid var(--line); border-radius: 6px; padding: .25rem .6rem; font-size: .85rem; font-weight: 600; margin: .35rem .35rem 0 0; }
.scope-write { border-color: var(--danger); color: var(--danger); background: var(--danger-bg); }
.callout { border-left: 3px solid var(--danger); background: var(--danger-bg); color: var(--danger); padding: .75rem; border-radius: 6px; font-size: .9rem; margin: .75rem 0; display: flex; gap: .5rem; align-items: flex-start; }
.errors { border-left: 3px solid var(--danger); background: var(--danger-bg); color: var(--danger); padding: .75rem; border-radius: 6px; font-size: .9rem; margin: 0 0 1rem; display: flex; gap: .5rem; align-items: center; }
label { display: block; font-weight: 600; margin: .75rem 0 .25rem; }
input[type=text], input[type=password] { width: 100%; padding: .55rem .65rem; font: inherit; color: inherit; background: var(--bg); border: 1px solid var(--line); border-radius: 6px; }
button, a.btn { display: inline-flex; align-items: center; justify-content: center; gap: .45rem; width: 100%; padding: .65rem 1rem; font: inherit; font-weight: 600; border-radius: 8px; cursor: pointer; text-decoration: none; border: 1px solid transparent; margin-top: .65rem; }
button.primary, a.btn.primary { background: var(--accent); color: #fff; }
button.secondary { background: transparent; color: var(--fg); border-color: var(--line); }
"""


def _scope_summary_html(scope: str) -> str:
    tokens = str(scope or "readonly").strip().split()
    badges: list[str] = []
    if "write" in tokens:
        badges.append(f'<span class="scope-badge scope-write">{_svg_icon("alert")} write</span>')
    if "readonly" in tokens or not badges:
        label = "readonly" if ("readonly" in tokens or not tokens) else _h(scope)
        badges.append(f'<span class="scope-badge">{_svg_icon("book")} {label}</span>')
    details = f'<p class="hint">{_h(t("auth.oauth.scope_readonly"))}</p>'
    if "write" in tokens:
        details += f'<div class="callout" role="alert">{_svg_icon("alert")}<span>{_h(t("auth.oauth.scope_write"))}</span></div>'
    return "".join(badges) + details


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
    *,
    tx_id: str = "",
    account_name: str = "",
    needs_step_up: bool = False,
    allow_credentials_json: str = "[]",
) -> str:
    """Render zero-JS HTML OAuth 2.1 consent page with monochrome SVG icons and i18n."""
    esc_client_name = _h(client_name)
    esc_client_id = _h(client_id)
    esc_redirect_uri = _h(redirect_uri)
    esc_state = _h(state)
    esc_challenge = _h(code_challenge)
    esc_method = _h(code_challenge_method)
    esc_scope = _h(scope)
    esc_tx = _h(tx_id)
    locale = _h(get_locale())
    error_html = f'<div class="errors" role="alert">{_svg_icon("alert")}<span>{_h(error_msg)}</span></div>' if error_msg else ""

    hidden_tx = f'<input type="hidden" name="tx" value="{esc_tx}">' if tx_id else ""
    subtitle = _h(t("auth.oauth.subtitle", account=account_name)) if (has_valid_cookie and account_name) else _h(t("auth.oauth.session_required"))
    step_up_html = ""
    if has_valid_cookie and needs_step_up:
        step_up_html = (
            f'<div class="callout" data-passkey-reauth="1" data-allow-credentials="{_h(allow_credentials_json)}">'
            f'{_svg_icon("shield")}<div><strong>{_h(t("auth.oauth.step_up_title"))}</strong><br><span>{_h(t("auth.oauth.step_up_hint"))}</span></div></div>'
            f'<label for="step_up_password">{_h(t("auth.field.password"))}</label>'
            f'<input id="step_up_password" name="step_up_password" type="password" required autocomplete="current-password">'
        )

    if has_valid_cookie:
        action_buttons = f"""
        <form method="post" action="/oauth/authorize">
            {hidden_tx}
            <input type="hidden" name="client_id" value="{esc_client_id}">
            <input type="hidden" name="redirect_uri" value="{esc_redirect_uri}">
            <input type="hidden" name="state" value="{esc_state}">
            <input type="hidden" name="code_challenge" value="{esc_challenge}">
            <input type="hidden" name="code_challenge_method" value="{esc_method}">
            <input type="hidden" name="scope" value="{esc_scope}">
            <input type="hidden" name="action" value="approve">
            {step_up_html}
            <button type="submit" class="primary">{_svg_icon("check")} {_h(t("auth.oauth.approve"))}</button>
        </form>
        <form method="post" action="/oauth/authorize">
            {hidden_tx}
            <input type="hidden" name="client_id" value="{esc_client_id}">
            <input type="hidden" name="redirect_uri" value="{esc_redirect_uri}">
            <input type="hidden" name="state" value="{esc_state}">
            <input type="hidden" name="action" value="deny">
            <button type="submit" class="secondary">{_h(t("auth.oauth.deny"))}</button>
        </form>
        """
    else:
        login_href = f"/login?tx={quote(tx_id, safe='')}" if tx_id else "/login"
        action_buttons = f'<a class="btn primary" href="{_h(login_href)}">{_svg_icon("shield")} {_h(t("auth.oauth.sign_in_continue"))}</a>'

    return f"""<!doctype html>
<html lang="{locale}">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <meta name="robots" content="noindex, nofollow">
    <title>{_h(t("auth.oauth.page_title"))}</title>
    {get_web_ui_renderer().render_favicon_head_tags(include_pwa_register=False)}
    <style>{_OAUTH_PAGE_CSS}</style>
</head>
<body>
    <main>
        <div class="card">
            <div class="head">
                <img src="/favicon.svg" alt="Claire" width="32" height="32">
                <h1>{_h(t("auth.oauth.title", client=client_name))}</h1>
            </div>
            <p class="hint">{subtitle}</p>
            {error_html}
            <dl class="meta">
                <dt>{_h(t("auth.oauth.client_label"))}</dt>
                <dd>{esc_client_name}</dd>
                <dt>{_h(t("auth.oauth.redirect_label"))}</dt>
                <dd>{esc_redirect_uri}</dd>
                <dt>{_h(t("auth.oauth.scope_label"))}</dt>
                <dd>{_scope_summary_html(scope)}</dd>
            </dl>
            {action_buttons}
        </div>
    </main>
</body>
</html>
"""


def _resolve_session_account(conn: Any, cookie_token: str | None) -> tuple[accounts_store.Account, Any] | tuple[None, None]:
    """Validate cookie session token and return active (account, binding) or (None, None)."""
    if not cookie_token or not dbm.plausible_session_token(cookie_token):
        return None, None
    binding = accounts_store.validate_session_binding(conn, cookie_token)
    if binding is None:
        return None, None
    try:
        acc = accounts_store.AccountStore(conn).get(binding.account_id)
    except accounts_store.AccountNotFound:
        return None, None
    if acc.status not in accounts_store.CREDENTIAL_ACCEPTING_STATUSES:
        return None, None
    return acc, binding


def _resolve_registered_client(conn: Any, client_id: str, redirect_uri: str | None = None) -> tuple[str | None, tuple[str, str, int] | None]:
    """Verify OAuth client registration and optional redirect_uri match; returns (client_name, error_tuple)."""
    client = dbm.get_oauth_client(conn, client_id)
    if not client:
        if client_id.startswith("claire_mcp_"):
            return "External MCP Client", None
        return None, ("unauthorized_client", "Unknown or unregistered client_id", 400)
    if redirect_uri is not None:
        client_redirect_uris = client.get("redirect_uris") or []
        if client_redirect_uris and redirect_uri not in client_redirect_uris:
            return None, ("invalid_request", "redirect_uri mismatch", 400)
    return str(client["client_name"] or "External MCP Client"), None


async def _parse_request_params(request: Request) -> dict[str, Any]:
    """Parse application/json or application/x-www-form-urlencoded request parameters."""
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            payload = await request.json()
            return payload if isinstance(payload, dict) else {}
        except Exception:
            return {}
    try:
        form = await request.form()
        return dict(form)
    except Exception:
        return {}


async def _maybe_notify_write_grant(request: Request, conn: Any, account_id: int, client_name: str) -> None:
    """Send optional non-blocking Telegram notification when an account approves a write OAuth grant."""
    try:
        settings = getattr(request.app.state, "settings", None) or get_settings()
        config = getattr(request.app.state, "runtime_config", None)
        account = accounts_store.AccountStore(conn).get(account_id)
        deliveries: list[tuple[str, int]] = []
        seen: set[tuple[str, int]] = set()

        def _add_delivery(token_val: str, chat_id: int) -> None:
            if not token_val or chat_id <= 0:
                return
            pair = (token_val, chat_id)
            if pair not in seen:
                seen.add(pair)
                deliveries.append(pair)

        data_dir = (getattr(config, "data_dir", None) if config else None) or getattr(settings, "data_dir", None) or (Path(config.db_file).parent if (config and getattr(config, "db_file", None)) else None)
        if data_dir is not None:
            auth_file = auth_db.auth_db_path(data_dir)
            if auth_file.is_file():
                auth_conn = sqlite3.connect(auth_file.resolve().as_uri() + "?mode=ro", uri=True, timeout=5.0)
                try:
                    has_table = auth_conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='account_telegram_bots'").fetchone()
                    if has_table is not None:
                        has_enabled = auth_conn.execute("SELECT 1 FROM account_telegram_bots WHERE account_id=? AND enabled=1", (account_id,)).fetchone()
                        if has_enabled is not None:
                            profiles, _errors = telegram_bots.load_enabled_bots(
                                auth_conn,
                                account_ids=[account_id],
                                secret_service=get_secret_service(Path(data_dir) / "auth" / "master.key"),
                            )
                            for profile in profiles:
                                for uid in profile.allowed_user_ids:
                                    _add_delivery(profile.token, int(uid))
                finally:
                    auth_conn.close()

        bot_token = getattr(settings, "telegram_bot_token", "") or ""
        if account.role == "admin" and bot_token:
            owner_chat_id = getattr(settings, "telegram_owner_chat_id", 0)
            if owner_chat_id:
                _add_delivery(bot_token, int(owner_chat_id))
            for uid in (getattr(settings, "allowed_user_ids", None) or ()):
                _add_delivery(bot_token, int(uid))

        if not deliveries:
            return
        target_locale = push_locale(account.locale, settings)
        msg_text = t("notify.oauth.write_grant", locale=target_locale, client=_html.escape(str(client_name), quote=False))
        async with httpx.AsyncClient(timeout=3.0) as client_http:
            for token_val, chat_id in deliveries:
                try:
                    await client_http.post(
                        f"https://api.telegram.org/bot{token_val}/sendMessage",
                        json={
                            "chat_id": chat_id,
                            "text": msg_text,
                            "parse_mode": "HTML",
                            "disable_web_page_preview": True,
                        },
                    )
                except Exception:
                    log.debug("Optional Telegram write-grant notice target failed", exc_info=True)
    except Exception:
        log.debug("Optional Telegram write-grant notice skipped", exc_info=True)


async def handle_authorize(request: Request) -> Response:
    """GET / POST /oauth/authorize (OAuth 2.1 with S256 PKCE and Telegram-free /login?tx= handoff)."""
    config: WebRuntimeConfig = request.app.state.runtime_config
    no_store_headers = {"Referrer-Policy": "same-origin", "Cache-Control": "no-store"}

    if request.method in ("GET", "HEAD"):
        params = request.query_params
        tx_param = params.get("tx", "").strip()
        _cookie_present, cookie_token = read_session_cookie(request.scope)

        conn = dbm.connect_existing(config.db_file)
        try:
            authed_acc, binding = _resolve_session_account(conn, cookie_token)
            if tx_param:
                cookie_tx = read_oauth_tx_cookie(request.scope)
                if not cookie_tx or not hmac.compare_digest(cookie_tx, tx_param):
                    return JSONResponse({"error": "invalid_request", "error_description": "Invalid OAuth transaction cookie"}, status_code=400)
                pending = dbm.get_oauth_pending_request(conn, tx_param)
                if pending is None:
                    return JSONResponse({"error": "invalid_request", "error_description": "Invalid or expired OAuth transaction"}, status_code=400)
                if pending.get("account_id") is not None and authed_acc is not None and int(pending["account_id"]) != int(authed_acc.id):
                    return JSONResponse({"error": "access_denied", "error_description": "Transaction belongs to another account"}, status_code=403)
                client_id = str(pending["client_id"])
                client_name = str(pending.get("client_name") or "External MCP Client")
                redirect_uri = str(pending["redirect_uri"])
                state = str(pending.get("state") or "")
                code_challenge = str(pending["code_challenge"])
                code_challenge_method = str(pending["code_challenge_method"])
                scope = str(pending["scope"])
            else:
                client_id = params.get("client_id", "")
                redirect_uri = params.get("redirect_uri", "")
                response_type = params.get("response_type", "")
                code_challenge = params.get("code_challenge", "")
                code_challenge_method = params.get("code_challenge_method", "")
                state = params.get("state", "")
                if response_type != "code":
                    return JSONResponse({"error": "unsupported_response_type"}, status_code=400)
                if not client_id or not redirect_uri:
                    return JSONResponse({"error": "invalid_request", "error_description": "Missing client_id or redirect_uri"}, status_code=400)
                if not _is_safe_redirect_uri(redirect_uri):
                    return JSONResponse({"error": "invalid_request", "error_description": "Invalid redirect_uri scheme or origin"}, status_code=400)
                resolved_name, err = _resolve_registered_client(conn, client_id, redirect_uri)
                if err is not None:
                    return JSONResponse({"error": err[0], "error_description": err[1]}, status_code=err[2])
                client_name = resolved_name or "External MCP Client"
                if code_challenge_method != "S256" or not code_challenge:
                    return JSONResponse({"error": "invalid_request", "error_description": "PKCE S256 code_challenge is required"}, status_code=400)
                try:
                    scope = dbm.parse_oauth_scope(params.get("scope"))
                except ValueError as exc:
                    return JSONResponse({"error": "invalid_scope", "error_description": str(exc)}, status_code=400)

            if authed_acc is None:
                tx_id = tx_param or dbm.create_oauth_pending_request(
                    conn,
                    client_id=client_id,
                    client_name=client_name,
                    redirect_uri=redirect_uri,
                    code_challenge=code_challenge,
                    code_challenge_method="S256",
                    scope=scope,
                    state=state or None,
                )
                resp = Response(status_code=303, headers={"Location": f"/login?tx={quote(tx_id, safe='')}", **no_store_headers})
                set_oauth_tx_cookie(resp, config, tx_id)
                return resp

            needs_step_up = ("write" in scope.split()) and not _session_has_recent_auth(binding.authenticated_at if binding is not None else None)
            allow_creds = _account_passkey_descriptors(config, authed_acc.id) if needs_step_up else []
            account_label = authed_acc.username or authed_acc.display_name or f"account:{authed_acc.id}"
            html = _render_authorize_page(
                client_name=client_name,
                client_id=client_id,
                redirect_uri=redirect_uri,
                state=state,
                code_challenge=code_challenge,
                code_challenge_method="S256",
                scope=scope,
                has_valid_cookie=True,
                tx_id=tx_param,
                account_name=account_label,
                needs_step_up=needs_step_up,
                allow_credentials_json=json.dumps(allow_creds),
            )
            return HTMLResponse(html, headers=no_store_headers)
        finally:
            conn.close()

    _cookie_present, cookie_token = read_session_cookie(request.scope)
    if _cookie_present or request.headers.get("origin") or request.headers.get("sec-fetch-site"):
        if not same_origin(request):
            return JSONResponse({"error": "access_denied", "error_description": "Cross-origin request rejected"}, status_code=403)

    form = await request.form()
    action = str(form.get("action") or "").strip().lower()
    tx_id = str(form.get("tx") or "").strip()

    conn = dbm.connect_existing(config.db_file)
    try:
        authed_acc, binding = _resolve_session_account(conn, cookie_token)
        if tx_id:
            cookie_tx = read_oauth_tx_cookie(request.scope)
            if not cookie_tx or not hmac.compare_digest(cookie_tx, tx_id):
                return JSONResponse({"error": "invalid_request", "error_description": "Invalid OAuth transaction cookie"}, status_code=400)
            pending = dbm.get_oauth_pending_request(conn, tx_id)
            if pending is None:
                return JSONResponse({"error": "invalid_request", "error_description": "Invalid or expired OAuth transaction"}, status_code=400)
            if pending.get("account_id") is not None and authed_acc is not None and int(pending["account_id"]) != int(authed_acc.id):
                return JSONResponse({"error": "access_denied", "error_description": "Transaction belongs to another account"}, status_code=403)
            client_id = str(pending["client_id"])
            client_name = str(pending.get("client_name") or "External MCP Client")
            redirect_uri = str(pending["redirect_uri"])
            state = str(pending.get("state") or "")
            code_challenge = str(pending["code_challenge"])
            code_challenge_method = str(pending["code_challenge_method"])
            scope = str(pending["scope"])
        else:
            client_id = str(form.get("client_id") or "")
            redirect_uri = str(form.get("redirect_uri") or "")
            state = str(form.get("state") or "")
            code_challenge = str(form.get("code_challenge") or "")
            code_challenge_method = str(form.get("code_challenge_method") or "")
            raw_scope = str(form.get("scope") or "readonly")
            if not _is_safe_redirect_uri(redirect_uri):
                return JSONResponse({"error": "invalid_request", "error_description": "Invalid redirect_uri scheme or origin"}, status_code=400)
            resolved_name, err = _resolve_registered_client(conn, client_id, redirect_uri)
            if err is not None:
                return JSONResponse({"error": err[0], "error_description": err[1]}, status_code=err[2])
            client_name = resolved_name or "External MCP Client"
            try:
                scope = dbm.parse_oauth_scope(raw_scope)
            except ValueError as exc:
                return JSONResponse({"error": "invalid_scope", "error_description": str(exc)}, status_code=400)

        if not _is_safe_redirect_uri(redirect_uri):
            return JSONResponse({"error": "invalid_request", "error_description": "Invalid redirect_uri scheme or origin"}, status_code=400)

        if action not in ("approve", "deny"):
            return JSONResponse({"error": "invalid_request", "error_description": "Unsupported authorize action"}, status_code=400)

        if authed_acc is None or binding is None or cookie_token is None:
            html = _render_authorize_page(
                client_name=client_name,
                client_id=client_id,
                redirect_uri=redirect_uri,
                state=state,
                code_challenge=code_challenge,
                code_challenge_method="S256",
                scope=scope,
                has_valid_cookie=False,
                tx_id=tx_id,
                error_msg=t("auth.oauth.session_required"),
            )
            return HTMLResponse(html, status_code=401, headers=no_store_headers)

        if action == "deny":
            if tx_id:
                consumed = dbm.consume_oauth_pending_request(conn, tx_id, account_id=authed_acc.id)
                if consumed is None:
                    return JSONResponse({"error": "invalid_request", "error_description": "Transaction already consumed or expired"}, status_code=400)
            delim = "&" if "?" in redirect_uri else "?"
            deny_qs = urlencode({"error": "access_denied", "state": state}) if state else "error=access_denied&state="
            resp = RedirectResponse(f"{redirect_uri}{delim}{deny_qs}", status_code=302)
            clear_oauth_tx_cookie(resp, config)
            return resp

        if code_challenge_method != "S256" or not code_challenge:
            return JSONResponse({"error": "invalid_request", "error_description": "PKCE S256 code_challenge is required"}, status_code=400)

        account_label = authed_acc.username or authed_acc.display_name or f"account:{authed_acc.id}"
        if "write" in scope.split() and not _session_has_recent_auth(binding.authenticated_at):
            step_up_password = str(form.get("step_up_password") or "")
            step_up_err = _verify_step_up_password(config, conn, authed_acc.id, client_ip(request.scope, config.trusted_proxies), step_up_password, cookie_token)
            if step_up_err is not None:
                allow_creds = _account_passkey_descriptors(config, authed_acc.id)
                html = _render_authorize_page(
                    client_name=client_name,
                    client_id=client_id,
                    redirect_uri=redirect_uri,
                    state=state,
                    code_challenge=code_challenge,
                    code_challenge_method="S256",
                    scope=scope,
                    has_valid_cookie=True,
                    tx_id=tx_id,
                    account_name=account_label,
                    needs_step_up=True,
                    allow_credentials_json=json.dumps(allow_creds),
                    error_msg=step_up_err,
                )
                return HTMLResponse(html, status_code=403, headers=no_store_headers)

        if tx_id:
            consumed = dbm.consume_oauth_pending_request(conn, tx_id, account_id=authed_acc.id)
            if consumed is None:
                return JSONResponse({"error": "invalid_request", "error_description": "Transaction already consumed or expired"}, status_code=400)

        code = dbm.create_oauth_code(
            conn,
            client_id=client_id,
            redirect_uri=redirect_uri,
            code_challenge=code_challenge,
            code_challenge_method="S256",
            scope=scope,
            account_id=authed_acc.id,
        )
        if "write" in scope.split():
            await _maybe_notify_write_grant(request, conn, authed_acc.id, client_name)
    finally:
        conn.close()

    delim = "&" if "?" in redirect_uri else "?"
    qs = urlencode({"code": code, "state": state}) if state else f"code={quote(code, safe='')}&state="
    resp = RedirectResponse(f"{redirect_uri}{delim}{qs}", status_code=302)
    clear_oauth_tx_cookie(resp, config)
    return resp


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
        return JSONResponse({"ok": False, "error": "invalid_request"}, status_code=400)

    if not _is_safe_redirect_uri(redirect_uri):
        return JSONResponse({"ok": False, "error": "invalid_request"}, status_code=400)

    conn = dbm.connect_existing(config.db_file)
    try:
        # Telegram push flow is strictly restricted to administrator session flow
        _cookie_present, cookie_token = read_session_cookie(request.scope)
        if _cookie_present and cookie_token and dbm.plausible_session_token(cookie_token):
            binding = accounts_store.validate_session_binding(conn, cookie_token)
            if binding is not None:
                if binding.account_id != 1:
                    return JSONResponse(
                        {"ok": False, "error": "forbidden", "error_description": "Telegram push is restricted to admin"},
                        status_code=403,
                    )
                try:
                    acc = accounts_store.AccountStore(conn).get(binding.account_id)
                    if acc.status not in accounts_store.CREDENTIAL_ACCEPTING_STATUSES:
                        return JSONResponse({"ok": False, "error": "forbidden", "error_description": "Admin account not active"}, status_code=403)
                except accounts_store.AccountNotFound:
                    return JSONResponse({"ok": False, "error": "forbidden"}, status_code=403)

        client = dbm.get_oauth_client(conn, client_id)
        if not client:
            return JSONResponse({"ok": False, "error": "invalid_request"}, status_code=400)
        client_redirect_uris = client.get("redirect_uris") or []
        if redirect_uri not in client_redirect_uris:
            return JSONResponse({"ok": False, "error": "invalid_request"}, status_code=400)

        client_name = client["client_name"] or "Google Gemini / MCP Client"

        settings = getattr(request.app.state, "settings", None) or get_settings()
        token = getattr(settings, "telegram_bot_token", "")
        owner_chat_id = getattr(settings, "telegram_owner_chat_id", 0)
        targets = getattr(settings, "allowed_user_ids", None) or (
            {owner_chat_id} if owner_chat_id else set()
        )

        if not token or not targets:
            log.warning("OAuth push aborted: telegram bot token or recipients not configured")
            return JSONResponse({"ok": False, "error": "temporarily_unavailable"}, status_code=503)

        nonce = secrets.token_urlsafe(24)
        # The push goes to the administrator account (I18N design Section 7.2): accounts.locale, then CLAIRE_LANG, then the default.
        try:
            admin_locale = accounts_store.AccountStore(conn).admin().locale
        except Exception:  # noqa: BLE001
            admin_locale = None
        target_locale = push_locale(admin_locale, settings)
        msg_text = t("notify.oauth.request", locale=target_locale, client=_html.escape(str(client_name), quote=False))
        keyboard = {
            "inline_keyboard": [
                [
                    {"text": t("notify.oauth.approve", locale=target_locale), "callback_data": f"oauth_appr:{nonce}"},
                    {"text": t("notify.oauth.deny", locale=target_locale), "callback_data": f"oauth_deny:{nonce}"},
                ]
            ]
        }
        sent_count = 0
        async with httpx.AsyncClient(timeout=10.0) as client_http:
            for chat_id in targets:
                try:
                    resp = await client_http.post(
                        f"https://api.telegram.org/bot{token}/sendMessage",
                        json={
                            "chat_id": chat_id,
                            "text": msg_text,
                            "parse_mode": "HTML",
                            "reply_markup": keyboard,
                        },
                    )
                    if resp.status_code == 200:
                        sent_count += 1
                except Exception as exc:
                    log.warning("Failed to send telegram oauth push to %s: %s", chat_id, exc)

        if sent_count == 0:
            log.warning("OAuth push aborted: failed to deliver telegram notification to any recipient")
            return JSONResponse({"ok": False, "error": "temporarily_unavailable"}, status_code=503)

        dbm.create_oauth_auth_request(
            conn,
            client_id=client_id,
            redirect_uri=redirect_uri,
            code_challenge=code_challenge,
            code_challenge_method=code_challenge_method,
            scope="readonly",
            state=state,
            nonce=nonce,
        )
        conn.execute("UPDATE oauth_auth_requests SET account_id=? WHERE nonce=?", (1, nonce))
        conn.commit()
    finally:
        conn.close()

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
    """POST /oauth/token (RFC 6749, RFC 7636 S256 PKCE, RFC 8628 Device Grant, RFC 8693 Token Exchange)."""
    config: WebRuntimeConfig = request.app.state.runtime_config
    params = await _parse_request_params(request)
    grant_type = str(params.get("grant_type") or "")

    # 1. Authorization Code Grant
    if grant_type == "authorization_code":
        code = str(params.get("code") or "")
        req_cid, req_secret, cred_err = _resolve_client_credentials(request, params)
        if cred_err is not None:
            return cred_err
        client_id = str(req_cid or "")
        redirect_uri = str(params.get("redirect_uri") or "")
        code_verifier = str(params.get("code_verifier") or "")

        if not code or not client_id or not redirect_uri:
            return JSONResponse({"error": "invalid_request", "error_description": "Missing required parameters"}, status_code=400)

        conn = dbm.connect_existing(config.db_file)
        try:
            client = dbm.get_oauth_client(conn, client_id)
            if not client:
                return JSONResponse({"error": "invalid_client", "error_description": "Unknown client_id"}, status_code=401)
            if redirect_uri not in client.get("redirect_uris", []):
                return JSONResponse({"error": "invalid_grant", "error_description": "Redirect URI mismatch"}, status_code=400)
            if req_secret and not secrets.compare_digest(str(req_secret), str(client.get("client_secret") or "")):
                return JSONResponse({"error": "invalid_client", "error_description": "Client authentication failed"}, status_code=401)

            code_data = dbm.consume_oauth_code(conn, code, client_id, redirect_uri)
            if not code_data:
                return JSONResponse({"error": "invalid_grant", "error_description": "Invalid, expired, or consumed code"}, status_code=400)

            challenge = str(code_data["code_challenge"])
            method = str(code_data.get("code_challenge_method") or "")
            if not _verify_pkce(code_verifier, challenge, method):
                return JSONResponse({"error": "invalid_grant", "error_description": "PKCE verification failed"}, status_code=400)

            code_account_id = code_data.get("account_id")
            if code_account_id is not None:
                try:
                    acc = accounts_store.AccountStore(conn).get(int(code_account_id))
                    if acc.status not in accounts_store.CREDENTIAL_ACCEPTING_STATUSES:
                        return JSONResponse({"error": "invalid_grant", "error_description": f"Account is {acc.status}"}, status_code=400)
                except accounts_store.AccountNotFound:
                    return JSONResponse({"error": "invalid_grant", "error_description": "Account not found"}, status_code=400)

            granted_scope = dbm.parse_oauth_scope(code_data.get("granted_scope") or code_data.get("scope") or "readonly")
            access_token, refresh_token = dbm.create_oauth_token(
                conn,
                client_id=client_id,
                scope=granted_scope,
                account_id=int(code_account_id) if code_account_id is not None else dbm.ADMIN_ACCOUNT_ID,
            )
        finally:
            conn.close()

        return JSONResponse(
            {
                "access_token": access_token,
                "token_type": "Bearer",
                "expires_in": int(dbm.OAUTH_ACCESS_TOKEN_TTL),
                "refresh_token": refresh_token,
                "scope": granted_scope,
            },
            headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
        )

    # 2. Refresh Token Grant
    if grant_type == "refresh_token":
        refresh_token = str(params.get("refresh_token") or "")
        req_cid, req_secret, cred_err = _resolve_client_credentials(request, params)
        if cred_err is not None:
            return cred_err
        client_id = str(req_cid or "")
        raw_req_scope = params.get("scope")

        if not refresh_token:
            return JSONResponse({"error": "invalid_request", "error_description": "Missing refresh_token"}, status_code=400)

        conn = dbm.connect_existing(config.db_file)
        try:
            row = conn.execute(
                "SELECT token, refresh_token, client_id, scope, granted_scope, token_family_id, expires_at, account_id FROM oauth_tokens WHERE refresh_token=?",
                (refresh_token,),
            ).fetchone()
            if row is None:
                return JSONResponse({"error": "invalid_grant", "error_description": "Invalid or expired refresh_token"}, status_code=400)
            if client_id and str(row["client_id"]) != client_id:
                return JSONResponse({"error": "invalid_grant", "error_description": "Client ID mismatch"}, status_code=400)

            client = dbm.get_oauth_client(conn, str(row["client_id"]))
            if client and req_secret and not secrets.compare_digest(str(req_secret), str(client.get("client_secret") or "")):
                return JSONResponse({"error": "invalid_client", "error_description": "Client authentication failed"}, status_code=401)

            token_account_id = row["account_id"]
            if token_account_id is not None:
                try:
                    acc = accounts_store.AccountStore(conn).get(int(token_account_id))
                    if acc.status not in accounts_store.CREDENTIAL_ACCEPTING_STATUSES:
                        return JSONResponse({"error": "invalid_grant", "error_description": f"Account is {acc.status}"}, status_code=400)
                except accounts_store.AccountNotFound:
                    return JSONResponse({"error": "invalid_grant", "error_description": "Account not found"}, status_code=400)

            try:
                res = dbm.refresh_oauth_token(
                    conn,
                    refresh_token,
                    client_id if client_id else None,
                    requested_scope=str(raw_req_scope) if raw_req_scope is not None else None,
                )
            except ValueError:
                return JSONResponse({"error": "invalid_scope", "error_description": "Requested scope exceeds granted scope or is unsupported"}, status_code=400)
            if not res:
                return JSONResponse({"error": "invalid_grant", "error_description": "Invalid, expired, revoked, or reused refresh_token"}, status_code=400)
            new_access, new_refresh, scope = res
        finally:
            conn.close()

        return JSONResponse(
            {
                "access_token": new_access,
                "token_type": "Bearer",
                "expires_in": int(dbm.OAUTH_ACCESS_TOKEN_TTL),
                "refresh_token": new_refresh,
                "scope": scope,
            },
            headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
        )

    # 3. RFC 8628 Device Code Grant
    if grant_type == "urn:ietf:params:oauth:grant-type:device_code":
        device_code = str(params.get("device_code") or "")
        req_cid, req_secret, cred_err = _resolve_client_credentials(request, params)
        if cred_err is not None:
            return cred_err
        client_id = str(req_cid or "")
        if not device_code or not client_id:
            return JSONResponse({"error": "invalid_request", "error_description": "Missing device_code or client_id"}, status_code=400)

        conn = dbm.connect_existing(config.db_file)
        try:
            client = dbm.get_oauth_client(conn, client_id)
            if not client and not client_id.startswith("claire_mcp_"):
                return JSONResponse({"error": "invalid_client", "error_description": "Unknown client_id"}, status_code=401)
            if client and req_secret and not secrets.compare_digest(str(req_secret), str(client.get("client_secret") or "")):
                return JSONResponse({"error": "invalid_client", "error_description": "Client authentication failed"}, status_code=401)

            record = dbm.get_oauth_device_code(conn, device_code)
            if record is None or str(record["client_id"]) != client_id:
                return JSONResponse({"error": "invalid_grant", "error_description": "Unknown device_code"}, status_code=400)
            now = time.time()
            if float(record["expires_at"]) < now:
                return JSONResponse({"error": "expired_token", "error_description": "Device code expired"}, status_code=400)
            status = str(record["status"])
            if status == "denied":
                return JSONResponse({"error": "access_denied", "error_description": "User denied device authorization"}, status_code=400)
            if status == "consumed":
                return JSONResponse({"error": "invalid_grant", "error_description": "Device code already consumed"}, status_code=400)
            if status == "pending":
                last_poll = record.get("last_poll_at")
                interval = int(record.get("interval") or 5)
                if last_poll is not None and (now - float(last_poll)) < float(interval):
                    new_interval = interval + 5
                    dbm.touch_oauth_device_code_poll(conn, device_code, bump_interval=new_interval)
                    return JSONResponse({"error": "slow_down", "interval": new_interval}, status_code=400)
                dbm.touch_oauth_device_code_poll(conn, device_code)
                return JSONResponse({"error": "authorization_pending"}, status_code=400)

            consumed = dbm.consume_oauth_device_code(conn, device_code)
            if consumed is None:
                return JSONResponse({"error": "invalid_grant", "error_description": "Device code already consumed or expired"}, status_code=400)
            acct_id = consumed.get("account_id")
            if acct_id is not None:
                try:
                    acc = accounts_store.AccountStore(conn).get(int(acct_id))
                    if acc.status not in accounts_store.CREDENTIAL_ACCEPTING_STATUSES:
                        return JSONResponse({"error": "invalid_grant", "error_description": f"Account is {acc.status}"}, status_code=400)
                except accounts_store.AccountNotFound:
                    return JSONResponse({"error": "invalid_grant", "error_description": "Account not found"}, status_code=400)
            granted_scope = dbm.parse_oauth_scope(consumed.get("scope") or "readonly")
            access_token, refresh_token = dbm.create_oauth_token(
                conn,
                client_id=client_id,
                scope=granted_scope,
                account_id=int(acct_id) if acct_id is not None else dbm.ADMIN_ACCOUNT_ID,
            )
        finally:
            conn.close()

        return JSONResponse(
            {
                "access_token": access_token,
                "token_type": "Bearer",
                "expires_in": int(dbm.OAUTH_ACCESS_TOKEN_TTL),
                "refresh_token": refresh_token,
                "scope": granted_scope,
            },
            headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
        )

    # 4. RFC 8693 Token Exchange
    if grant_type == "urn:ietf:params:oauth:grant-type:token-exchange":
        subject_token = str(params.get("subject_token") or "")
        if not subject_token:
            return JSONResponse({"error": "invalid_request", "error_description": "Missing subject_token"}, status_code=400)

        conn = dbm.connect_existing(config.db_file)
        try:
            binding = accounts_store.validate_session_binding(conn, subject_token)
            if binding is None or binding.account_id is None:
                return JSONResponse({"error": "invalid_grant", "error_description": "Subject token is invalid, expired, or unbound"}, status_code=400)
            try:
                acc = accounts_store.AccountStore(conn).get(binding.account_id)
                if acc.status not in accounts_store.CREDENTIAL_ACCEPTING_STATUSES:
                    return JSONResponse({"error": "invalid_grant", "error_description": f"Account is {acc.status}"}, status_code=400)
                exchange_account_id = int(binding.account_id)
            except accounts_store.AccountNotFound:
                return JSONResponse({"error": "invalid_grant", "error_description": "Account not found"}, status_code=400)

            access_token, refresh_token = dbm.create_oauth_token(
                conn,
                client_id="token_exchange_client",
                scope="readonly",
                account_id=exchange_account_id,
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

    return JSONResponse({"error": "unsupported_grant_type", "error_description": f"Unsupported grant_type: {grant_type}"}, status_code=400)


async def handle_revoke(request: Request) -> Response:
    """POST /oauth/revoke (RFC 7009 token revocation)."""
    config: WebRuntimeConfig = request.app.state.runtime_config
    params = await _parse_request_params(request)
    token_value = str(params.get("token") or "").strip()
    if not token_value:
        return JSONResponse({"error": "invalid_request", "error_description": "Missing token"}, status_code=400)
    req_cid, req_secret, cred_err = _resolve_client_credentials(request, params)
    if cred_err is not None:
        return cred_err
    if req_secret and not req_cid:
        return JSONResponse({"error": "invalid_client", "error_description": "client_id is required with client_secret"}, status_code=401, headers={"Cache-Control": "no-store"})

    conn = dbm.connect_existing(config.db_file)
    try:
        if req_cid:
            client = dbm.get_oauth_client(conn, req_cid)
            if client and client.get("client_secret") and req_secret:
                if not secrets.compare_digest(str(req_secret), str(client["client_secret"])):
                    return JSONResponse({"error": "invalid_client", "error_description": "Client authentication failed"}, status_code=401)
        dbm.revoke_oauth_token(conn, token_value, client_id=req_cid)
    finally:
        conn.close()
    return JSONResponse({"ok": True}, status_code=200, headers={"Cache-Control": "no-store"})


async def handle_device_authorization(request: Request) -> Response:
    """POST /oauth/device_authorization (RFC 8628 Device Authorization Grant)."""
    config: WebRuntimeConfig = request.app.state.runtime_config
    params = await _parse_request_params(request)
    req_cid, req_secret, cred_err = _resolve_client_credentials(request, params)
    if cred_err is not None:
        return cred_err
    client_id = str(req_cid or "")
    if not client_id:
        return JSONResponse({"error": "invalid_request", "error_description": "Missing client_id"}, status_code=400)

    try:
        scope = dbm.parse_oauth_scope(str(params.get("scope") or "readonly"))
    except ValueError as exc:
        return JSONResponse({"error": "invalid_scope", "error_description": str(exc)}, status_code=400)

    conn = dbm.connect_existing(config.db_file)
    try:
        client = dbm.get_oauth_client(conn, client_id)
        if not client and not client_id.startswith("claire_mcp_"):
            return JSONResponse({"error": "invalid_client", "error_description": "Unknown client_id"}, status_code=401)
        if client and client.get("client_secret") and req_secret:
            if not secrets.compare_digest(str(req_secret), str(client["client_secret"])):
                return JSONResponse({"error": "invalid_client", "error_description": "Client authentication failed"}, status_code=401)
        client_name = str(client["client_name"] if (client and client.get("client_name")) else "Headless MCP Client")
        device_code, user_code = dbm.create_oauth_device_code(
            conn,
            client_id=client_id,
            client_name=client_name,
            scope=scope,
            interval=5,
            ttl=600.0,
        )
    finally:
        conn.close()

    origin = config.public_origin.rstrip("/")
    verification_uri = f"{origin}/device"
    return JSONResponse(
        {
            "device_code": device_code,
            "user_code": user_code,
            "verification_uri": verification_uri,
            "verification_uri_complete": f"{verification_uri}?user_code={quote(user_code, safe='')}",
            "expires_in": 600,
            "interval": 5,
        },
        status_code=200,
        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
    )


def _render_device_page(
    *,
    user_code: str = "",
    record: dict[str, Any] | None = None,
    account_name: str = "",
    needs_step_up: bool = False,
    allow_credentials_json: str = "[]",
    error_msg: str | None = None,
    status_msg: str | None = None,
) -> str:
    """Render zero-JS RFC 8628 /device verification and consent HTML."""
    locale = _h(get_locale())
    error_html = f'<div class="errors" role="alert">{_svg_icon("alert")}<span>{_h(error_msg)}</span></div>' if error_msg else ""
    status_html = f'<div class="callout" style="border-color:var(--accent);color:var(--fg);background:var(--bg);" role="status">{_svg_icon("check")}<span>{_h(status_msg)}</span></div>' if status_msg else ""

    if status_msg:
        body_inner = status_html
    elif record is not None:
        client_name = str(record.get("client_name") or record["client_id"])
        scope = str(record.get("scope") or "readonly")
        esc_code = _h(record["user_code"])
        step_up_html = ""
        if needs_step_up:
            step_up_html = (
                f'<div class="callout" data-passkey-reauth="1" data-allow-credentials="{_h(allow_credentials_json)}">'
                f'{_svg_icon("shield")}<div><strong>{_h(t("auth.oauth.step_up_title"))}</strong><br><span>{_h(t("auth.oauth.step_up_hint"))}</span></div></div>'
                f'<label for="step_up_password">{_h(t("auth.field.password"))}</label>'
                f'<input id="step_up_password" name="step_up_password" type="password" required autocomplete="current-password">'
            )
        subtitle = _h(t("auth.oauth.subtitle", account=account_name)) if account_name else _h(t("auth.device.subtitle"))
        body_inner = f"""
        <p class="hint">{subtitle}</p>
        {error_html}
        <dl class="meta">
            <dt>{_h(t("auth.oauth.client_label"))}</dt>
            <dd>{_h(client_name)}</dd>
            <dt>{_h(t("auth.device.code_label"))}</dt>
            <dd><code>{esc_code}</code></dd>
            <dt>{_h(t("auth.oauth.scope_label"))}</dt>
            <dd>{_scope_summary_html(scope)}</dd>
        </dl>
        <form method="post" action="/device">
            <input type="hidden" name="user_code" value="{esc_code}">
            <input type="hidden" name="action" value="approve">
            {step_up_html}
            <button type="submit" class="primary">{_svg_icon("check")} {_h(t("auth.oauth.approve"))}</button>
        </form>
        <form method="post" action="/device">
            <input type="hidden" name="user_code" value="{esc_code}">
            <input type="hidden" name="action" value="deny">
            <button type="submit" class="secondary">{_h(t("auth.oauth.deny"))}</button>
        </form>
        """
    else:
        body_inner = f"""
        <p class="hint">{_h(t("auth.device.subtitle"))}</p>
        {error_html}
        <form method="post" action="/device">
            <input type="hidden" name="action" value="lookup">
            <label for="user_code">{_h(t("auth.device.code_label"))}</label>
            <input id="user_code" name="user_code" type="text" value="{_h(user_code)}" required autocomplete="off" autocapitalize="characters" spellcheck="false" placeholder="ABCD-EFGH">
            <p class="hint">{_h(t("auth.device.code_hint"))}</p>
            <button type="submit" class="primary">{_svg_icon("device")} {_h(t("auth.device.lookup"))}</button>
        </form>
        """

    return f"""<!doctype html>
<html lang="{locale}">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <meta name="robots" content="noindex, nofollow">
    <title>{_h(t("auth.device.page_title"))}</title>
    {get_web_ui_renderer().render_favicon_head_tags(include_pwa_register=False)}
    <style>{_OAUTH_PAGE_CSS}</style>
</head>
<body>
    <main>
        <div class="card">
            <div class="head">
                <img src="/favicon.svg" alt="Claire" width="32" height="32">
                <h1>{_h(t("auth.device.title"))}</h1>
            </div>
            {body_inner}
        </div>
    </main>
</body>
</html>
"""


async def handle_device_verification(request: Request) -> Response:
    """GET / HEAD / POST /device (RFC 8628 browser verification UI)."""
    config: WebRuntimeConfig = request.app.state.runtime_config
    no_store_headers = {"Referrer-Policy": "same-origin", "Cache-Control": "no-store"}
    _cookie_present, cookie_token = read_session_cookie(request.scope)

    if request.method in ("GET", "HEAD"):
        raw_code = "".join(c for c in request.query_params.get("user_code", "") if c.isalnum() or c == "-")[:16]
        conn = dbm.connect_existing(config.db_file)
        try:
            authed_acc, binding = _resolve_session_account(conn, cookie_token)
            if authed_acc is None:
                target = "/device" + (f"?user_code={quote(raw_code, safe='')}" if raw_code else "")
                return Response(status_code=303, headers={"Location": f"/login?next={quote(target, safe='')}", **no_store_headers})

            record = None
            error_msg = None
            needs_step_up = False
            allow_creds: list[dict[str, str]] = []
            if raw_code:
                cand = dbm.get_oauth_device_code_by_user_code(conn, raw_code)
                if cand is None or float(cand["expires_at"]) < time.time() or str(cand["status"]) != "pending":
                    error_msg = t("auth.device.not_found")
                else:
                    record = cand
                    scope_str = str(cand.get("scope") or "readonly")
                    needs_step_up = ("write" in scope_str.split()) and not _session_has_recent_auth(binding.authenticated_at if binding is not None else None)
                    if needs_step_up:
                        allow_creds = _account_passkey_descriptors(config, authed_acc.id)
            account_label = authed_acc.username or authed_acc.display_name or f"account:{authed_acc.id}"
            html = _render_device_page(
                user_code=raw_code,
                record=record,
                account_name=account_label,
                needs_step_up=needs_step_up,
                allow_credentials_json=json.dumps(allow_creds),
                error_msg=error_msg,
            )
            return HTMLResponse(html, headers=no_store_headers)
        finally:
            conn.close()

    if _cookie_present or request.headers.get("origin") or request.headers.get("sec-fetch-site"):
        if not same_origin(request):
            return JSONResponse({"error": "access_denied", "error_description": "Cross-origin request rejected"}, status_code=403)

    form = await request.form()
    user_code = str(form.get("user_code") or "").strip()
    action = str(form.get("action") or "lookup").strip().lower()

    conn = dbm.connect_existing(config.db_file)
    try:
        authed_acc, binding = _resolve_session_account(conn, cookie_token)
        if authed_acc is None or binding is None or cookie_token is None:
            return HTMLResponse(_render_device_page(user_code=user_code, error_msg=t("auth.oauth.session_required")), status_code=401, headers=no_store_headers)
        account_label = authed_acc.username or authed_acc.display_name or f"account:{authed_acc.id}"
        cand = dbm.get_oauth_device_code_by_user_code(conn, user_code) if user_code else None
        if cand is None or float(cand["expires_at"]) < time.time() or str(cand["status"]) != "pending":
            return HTMLResponse(_render_device_page(user_code=user_code, error_msg=t("auth.device.not_found")), status_code=400, headers=no_store_headers)

        scope_str = str(cand.get("scope") or "readonly")
        needs_step_up = ("write" in scope_str.split()) and not _session_has_recent_auth(binding.authenticated_at)
        allow_creds = _account_passkey_descriptors(config, authed_acc.id) if needs_step_up else []
        if action in ("", "lookup"):
            return HTMLResponse(
                _render_device_page(
                    user_code=cand["user_code"],
                    record=cand,
                    account_name=account_label,
                    needs_step_up=needs_step_up,
                    allow_credentials_json=json.dumps(allow_creds),
                ),
                headers=no_store_headers,
            )

        if action == "deny":
            dbm.decide_oauth_device_code(conn, cand["user_code"], account_id=authed_acc.id, decision="denied")
            return HTMLResponse(_render_device_page(status_msg=t("auth.device.denied")), headers=no_store_headers)

        if action == "approve":
            if needs_step_up:
                step_up_password = str(form.get("step_up_password") or "")
                step_up_err = _verify_step_up_password(config, conn, authed_acc.id, client_ip(request.scope, config.trusted_proxies), step_up_password, cookie_token)
                if step_up_err is not None:
                    return HTMLResponse(
                        _render_device_page(
                            user_code=cand["user_code"],
                            record=cand,
                            account_name=account_label,
                            needs_step_up=True,
                            allow_credentials_json=json.dumps(allow_creds),
                            error_msg=step_up_err,
                        ),
                        status_code=403,
                        headers=no_store_headers,
                    )
            decided = dbm.decide_oauth_device_code(conn, cand["user_code"], account_id=authed_acc.id, decision="approved")
            if not decided:
                return HTMLResponse(_render_device_page(user_code=user_code, error_msg=t("auth.device.not_found")), status_code=400, headers=no_store_headers)
            if "write" in scope_str.split():
                await _maybe_notify_write_grant(request, conn, authed_acc.id, str(cand.get("client_name") or cand["client_id"]))
            return HTMLResponse(_render_device_page(status_msg=t("auth.device.approved")), headers=no_store_headers)

        return HTMLResponse(_render_device_page(user_code=user_code, error_msg=t("auth.device.not_found")), status_code=400, headers=no_store_headers)
    finally:
        conn.close()


async def handle_list_oauth_grants(request: Request) -> Response:
    """GET / HEAD /api/me/oauth/grants: list active OAuth grants for caller account."""
    config: WebRuntimeConfig = request.app.state.runtime_config
    principal = request_principal(request)
    if principal.account_id is None:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    conn = dbm.connect_existing(config.db_file, readonly=True)
    try:
        grants = dbm.list_account_oauth_grants(conn, principal.account_id)
    finally:
        conn.close()
    return JSONResponse({"grants": grants}, headers={"Cache-Control": "no-store"})


async def handle_revoke_oauth_grants(request: Request) -> Response:
    """POST / DELETE /api/me/oauth/grants: revoke one grant, client grants, or all grants."""
    config: WebRuntimeConfig = request.app.state.runtime_config
    principal = request_principal(request)
    if principal.account_id is None:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    if principal.channel == "cookie" and not same_origin(request):
        return JSONResponse({"error": "access_denied", "error_description": "Cross-origin request rejected"}, status_code=403)
    params = await _parse_request_params(request)
    grant_id = str(params.get("grant_id") or request.query_params.get("grant_id") or "").strip() or None
    client_id = str(params.get("client_id") or request.query_params.get("client_id") or "").strip() or None
    raw_all = params.get("revoke_all")
    revoke_all = (
        (raw_all is True or (isinstance(raw_all, str) and raw_all.strip().lower() in ("1", "true", "yes")))
        or request.query_params.get("all", "").strip().lower() in ("1", "true")
    )
    if grant_id is None and client_id is None and not revoke_all:
        return JSONResponse({"error": "invalid_request", "error_description": "Specify grant_id, client_id, or revoke_all"}, status_code=400)

    conn = dbm.connect_existing(config.db_file)
    try:
        revoked = dbm.revoke_account_oauth_grants(
            conn,
            principal.account_id,
            grant_id=None if revoke_all else grant_id,
            client_id=None if revoke_all else client_id,
        )
    finally:
        conn.close()
    return JSONResponse({"ok": True, "revoked": revoked}, headers={"Cache-Control": "no-store"})
