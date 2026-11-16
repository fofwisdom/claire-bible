"""External Identity Provider (IdP) authentication and account routes (design §12, §13.3).

This module implements OpenID Connect and GitHub OAuth authentication flows:
- GET /auth/oidc/{id}/start: Initiate login or invite-based signup.
- GET /auth/oidc/{id}/callback: Exchange code, verify ID token, link or login.
- POST /api/me/identities/oidc/{id}/start: Initiate account linking with step-up authentication.
- GET /api/me/identities: List linked external identities for the current account.
- DELETE /api/me/identities/oidc/{id}: Unlink an external identity.
- GET /invite/complete: Render form to complete invite signup with immutable username.
- POST /invite/complete: Atomically redeem invite, create account, and link identity.
- GET /admin/idp & GET/PUT /api/admin/idp: Administrator IdP configuration management.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import secrets
import time
from typing import Any

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Route

from ...accounts.invites import (
    InviteInactiveError,
    InviteNotFoundError,
    normalize_invite_code,
    validate_invite_code_syntax,
)
from ...accounts.username_policy import UsernameRejected, validate_username
from ...api.principal import Principal, request_principal
from ...api.security import read_json_body
from ...store import accounts as accounts_store
from ...store import auth_db
from ...store import db as dbm
from ...store.secrets import get_secret_service
from ... import ids
from ..namespace import account_home_path
from ..oidc import (
    ExternalIdentity,
    GitHubAdapter,
    IdpProviderMeta,
    OidcClient,
    OidcError,
    OidcVerificationError,
    generate_pkce_pair,
)
from ...i18n import get_locale, t
from .common import (
    AccountsContext,
    client_ip,
    cookie_session_token,
    escape,
    forbidden,
    json_error,
    not_found,
    open_auth_db,
    open_db,
    prototype_badge,
    prototype_note,
    render_template,
    t_code,
    require_recent_auth,
    retry_after_seconds,
    same_origin,
    set_session_cookie,
    validate_display_name,
)
from .console import ADMIN_SPEC_BY_SLUG, _render_console_section

log = logging.getLogger("claire.api.idp")

OIDC_COOKIE_NAME = "claire_oidc_state"
OIDC_COOKIE_PATH = "/auth/oidc"
OIDC_STATE_TTL = 600.0  # 10 minutes
SIGNUP_TOKEN_AAD = b"claire:signup_token"
SIGNUP_TOKEN_TTL = 600.0  # 10 minutes


def _is_admin(principal: Principal) -> bool:
    return principal.server_role == "admin" and principal.scope == "owner" and principal.account_id is not None


def _is_network_mode(ctx: AccountsContext) -> bool:
    conn = open_db(ctx)
    try:
        return accounts_store.AccountStore(conn).server_mode() == "network"
    finally:
        conn.close()


def _wants_json(request: Request) -> bool:
    if request.query_params.get("json") == "1":
        return True
    accept = request.headers.get("accept", "").lower()
    return "application/json" in accept and "text/html" not in accept


def callback_url(request: Request, ctx: AccountsContext, provider_id: str) -> str:
    origin = ctx.config.public_origin.rstrip("/") if ctx.config.public_origin else str(request.base_url).rstrip("/")
    return f"{origin}/auth/oidc/{provider_id}/callback"


def find_provider(ctx: AccountsContext, provider_id: str, *, include_secrets: bool = True) -> IdpProviderMeta | None:
    data_dir = ctx.data_dir or ctx.auth_db_file.parent.parent
    providers = auth_db.load_idp_providers(data_dir, include_secrets=include_secrets)
    for p in providers:
        if p.id == provider_id:
            return p
    return None


def get_auth_client(provider: IdpProviderMeta) -> OidcClient | GitHubAdapter:
    if provider.type == "github":
        return GitHubAdapter(provider, client_secret=provider.client_secret or "")
    return OidcClient(provider, client_secret=provider.client_secret or "")


def create_signup_token(
    ctx: AccountsContext,
    *,
    invitation_id: int,
    provider_id: str,
    iss: str,
    sub: str,
    email: str | None,
    display_name: str | None,
    now: float | None = None,
) -> str:
    current_ts = time.time() if now is None else now
    payload = {
        "invitation_id": invitation_id,
        "provider_id": provider_id,
        "iss": iss,
        "sub": sub,
        "email": email,
        "display_name": display_name,
        "exp": current_ts + SIGNUP_TOKEN_TTL,
    }
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    data_dir = ctx.data_dir or ctx.auth_db_file.parent.parent
    sec = get_secret_service(data_dir / "auth" / "master.key")
    encrypted = sec.encrypt(raw, aad=SIGNUP_TOKEN_AAD)
    token_bytes = encrypted.nonce + encrypted.ciphertext
    return base64.urlsafe_b64encode(token_bytes).decode("ascii").rstrip("=")


def verify_signup_token(
    ctx: AccountsContext,
    token: str,
    now: float | None = None,
) -> dict[str, Any] | None:
    if not token or not isinstance(token, str):
        return None
    try:
        padded = token + "=" * ((4 - len(token) % 4) % 4)
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
        if len(raw) < 13:
            return None
        nonce = raw[:12]
        ciphertext = raw[12:]
        data_dir = ctx.data_dir or ctx.auth_db_file.parent.parent
        sec = get_secret_service(data_dir / "auth" / "master.key")
        decrypted = sec.decrypt(ciphertext, nonce, aad=SIGNUP_TOKEN_AAD)
        payload = json.loads(decrypted.decode("utf-8"))
        if not isinstance(payload, dict):
            return None
        current_ts = time.time() if now is None else now
        if float(payload.get("exp", 0)) < current_ts:
            return None
        return payload
    except Exception:
        return None


def _render_admin_idp_cards(providers: list[IdpProviderMeta]) -> str:
    if not providers:
        return f'<p class="hint">{escape(t("console.admin.idp.cards_empty"))}</p>'
    cards = []
    for p in providers:
        status_badge = f'<span class="badge badge-enabled">{escape(t("console.admin.idp.badge.enabled"))}</span>' if p.enabled else f'<span class="badge badge-disabled">{escape(t("console.admin.idp.badge.disabled"))}</span>'
        signup_badge = f'<span class="badge badge-enabled">{escape(t("console.admin.idp.badge.signup"))}</span>' if p.allow_signup else f'<span class="badge badge-disabled">{escape(t("console.admin.idp.badge.no_signup"))}</span>'
        secret_badge = f'<span class="badge badge-enabled">{escape(t("console.admin.idp.badge.secret"))}</span>' if p.secret_configured else f'<span class="badge badge-disabled">{escape(t("console.admin.idp.badge.no_secret"))}</span>'
        rows = [
            (t("console.admin.idp.field.type"), p.type.upper()),
            (t("console.settings.account.display_name"), p.display_name),
            (t("console.admin.idp.field.client_id"), p.client_id),
            (t("console.common.status"), f"{status_badge} {signup_badge} {secret_badge}"),
            (t("console.admin.idp.field.issuer"), p.issuer or "-"),
            (t("console.admin.idp.field.scopes"), ", ".join(p.scopes)),
            (t("console.admin.idp.field.hosted_domain"), p.hosted_domain or "-"),
            (t("console.admin.idp.field.tenant"), p.tenant_id or "-"),
        ]
        body = "".join(f"<dt>{escape(k)}</dt><dd>{escape(v) if not '<span' in v else v}</dd>" for k, v in rows)
        cards.append(f'<section class="card" aria-label="{escape(p.display_name)}"><h2>{escape(p.display_name)} ({escape(p.id)})</h2><dl>{body}</dl></section>')
    return "".join(cards)


def _render_admin_idp_section_html(providers: list[IdpProviderMeta], public_origin: str) -> str:
    cards = _render_admin_idp_cards(providers)
    trs: list[str] = []
    for p in providers:
        callback = f"{public_origin.rstrip('/')}/auth/oidc/{p.id}/callback"
        trs.append(
            f'<tr data-idp-id="{escape(p.id)}">'
            f'<td><input type="checkbox" switch data-idp-toggle="{escape(p.id)}"{" checked" if p.enabled else ""} aria-label="{escape(t("console.admin.idp.toggle_label", name=p.display_name))}"/></td>'
            f"<td><strong>{escape(p.display_name)}</strong> (<code>{escape(p.id)}</code>)</td>"
            f'<td><span class="pref-mono">{escape(p.issuer or "https://github.com")}</span><br/><span style="color:var(--muted);font-size:11px">{escape(p.type.upper())}</span></td>'
            f'<td><code class="pref-mono">{escape(p.client_id)}</code></td>'
            f'<td><code class="pref-mono" style="white-space:normal">{escape(callback)}</code></td>'
            f'<td><input type="checkbox" switch data-idp-signup="{escape(p.id)}"{" checked" if p.allow_signup else ""} aria-label="{escape(t("console.admin.idp.signup_label", name=p.display_name))}"/></td>'
            f'<td><button type="button" class="pref-btn" data-idp-remove="{escape(p.id)}">{escape(t("common.action.delete"))}</button></td>'
            f"</tr>"
        )
    tbody = (
        "".join(trs)
        if trs
        else f'<tr><td colspan="7" style="color:var(--muted);text-align:center;">{escape(t("console.admin.idp.table_empty"))}</td></tr>'
    )

    return f"""
      <section class="card" aria-label="{escape(t("console.admin.idp.summary_label"))}">
        <h2>{escape(t("console.admin.idp.summary_title"))}</h2>
        <p class="hint">{t_code("console.admin.idp.summary_hint", variable="CLAIRE_OIDC_<ID>_CLIENT_SECRET")}</p>
      </section>
      <div id="admin-idp-cards">{cards}</div>
      <section class="card" aria-label="{escape(t("console.admin.idp.registry_label"))}">
        <h2><span data-icon="link"></span> {t_code("console.admin.idp.registry_title", endpoint="/api/admin/idp")}</h2>
        <div class="pref-table-wrap">
          <table class="pref-table">
            <thead>
              <tr><th style="width:52px">{escape(t("console.admin.idp.col_use"))}</th><th>{escape(t("console.settings.account.identities_col_provider"))}</th><th>{escape(t("console.admin.idp.col_issuer"))}</th><th>client_id</th><th>{escape(t("console.admin.idp.col_redirect"))}</th><th>{escape(t("console.admin.idp.badge.signup"))}</th><th>{escape(t("console.common.manage"))}</th></tr>
            </thead>
            <tbody id="pref-idp-tbody">{tbody}</tbody>
          </table>
        </div>
        <form id="pref-idp-add-form" class="pref-idp-form">
          <label style="display:flex;flex-direction:column;gap:4px;font-size:12px;font-weight:700">{escape(t("console.admin.idp.new_id"))}
            <input id="new-idp-id" name="id" type="text" class="pref-input" placeholder="{escape(t("console.admin.idp.new_id_placeholder"))}" required/>
          </label>
          <label style="display:flex;flex-direction:column;gap:4px;font-size:12px;font-weight:700">{escape(t("console.admin.idp.field.type"))}
            <select id="new-idp-type" name="type" class="pref-select">
              <option value="oidc">OIDC (Google · Keycloak · Entra)</option>
              <option value="github">GitHub OAuth 2.0</option>
            </select>
          </label>
          <label style="display:flex;flex-direction:column;gap:4px;font-size:12px;font-weight:700">{escape(t("console.settings.account.display_name"))}
            <input id="new-idp-name" name="display_name" type="text" class="pref-input" placeholder="{escape(t("console.admin.idp.new_name_placeholder"))}"/>
          </label>
          <label style="display:flex;flex-direction:column;gap:4px;font-size:12px;font-weight:700">issuer URL
            <input id="new-idp-issuer" name="issuer" type="text" class="pref-input" placeholder="https://sso.example.org/realms/claire"/>
          </label>
          <label style="display:flex;flex-direction:column;gap:4px;font-size:12px;font-weight:700">client_id
            <input id="new-idp-client" name="client_id" type="text" class="pref-input" placeholder="claire-bible" required/>
          </label>
          <button type="submit" class="pref-btn pref-btn-accent">{escape(t("console.admin.idp.new_submit"))}</button>
        </form>
        <p class="result" id="admin-idp-result" data-idp-result role="status" aria-live="polite"></p>
        <pre id="pref-idp-discovery" class="pref-code" hidden></pre>
      </section>
      <section class="card" aria-label="{escape(t("console.admin.idp.policy_label"))}">
        <h2><span data-icon="clipboard"></span> {escape(t("console.admin.idp.policy_title"))} {prototype_badge("admin-idp-login-policy", "prototype")}</h2>
        {prototype_note("admin-idp-login-policy", t("console.admin.idp.policy_note"))}
        <div class="pref-row">
          <div class="pref-row-label">
            <strong>{escape(t("console.admin.idp.policy_mode"))}</strong>
            <span>{t_code("console.admin.idp.policy_mode_desc", pair="(iss, sub)")}</span>
          </div>
          <select id="pref-idp-mode" class="pref-select" aria-label="{escape(t("console.admin.idp.policy_mode"))}">
            <option value="telegram_only">{escape(t("console.admin.idp.policy_mode_local"))}</option>
            <option value="telegram_and_idp" selected>{escape(t("console.admin.idp.policy_mode_idp"))}</option>
          </select>
        </div>
        <div class="pref-row">
          <div class="pref-row-label">
            <strong>{escape(t("console.admin.idp.policy_provisioning"))}</strong>
            <span>{escape(t("console.admin.idp.policy_provisioning_desc"))}</span>
          </div>
          <select id="pref-idp-provisioning" class="pref-select" aria-label="{escape(t("console.admin.idp.policy_provisioning_label"))}">
            <option value="invite" selected>{escape(t("console.admin.idp.policy_provisioning_invite"))}</option>
            <option value="approval">{escape(t("console.admin.idp.policy_provisioning_approval"))}</option>
            <option value="auto">{escape(t("console.admin.idp.policy_provisioning_auto"))}</option>
          </select>
        </div>
        <div class="pref-row">
          <div class="pref-row-label">
            <strong>{escape(t("console.admin.idp.policy_domains"))}</strong>
            <span>{t_code("console.admin.idp.policy_domains_desc", claim="hd")}</span>
          </div>
          <input id="pref-idp-domains" type="text" class="pref-input" style="min-width:260px" value="example.ac.kr, example.org" aria-label="{escape(t("console.admin.idp.policy_domains"))}"/>
        </div>
        <div class="pref-row">
          <div class="pref-row-label">
            <strong>{t_code("console.admin.idp.policy_verified", claim="email_verified=true")}</strong>
            <span>{escape(t("console.admin.idp.policy_verified_desc"))}</span>
          </div>
          <input id="pref-idp-verified" type="checkbox" switch checked aria-label="{escape(t("console.admin.idp.policy_verified_label"))}"/>
        </div>
        <div class="pref-row">
          <div class="pref-row-label">
            <strong>{escape(t("console.admin.idp.policy_backchannel"))}</strong>
            <span>{escape(t("console.admin.idp.policy_backchannel_desc"))}</span>
          </div>
          <input id="pref-idp-bcl" type="checkbox" switch checked aria-label="Back-Channel Logout"/>
        </div>
      </section>
      <section class="card" aria-label="{escape(t("console.admin.idp.sim_label"))}">
        <h2><span data-icon="play"></span> {escape(t("console.admin.idp.sim_title"))} {prototype_badge("admin-idp-login-simulator", "simulation")}</h2>
        {prototype_note("admin-idp-login-simulator", t("console.admin.idp.sim_note"))}
        <p class="hint">{escape(t("console.admin.idp.sim_hint"))}</p>
        <div class="pref-sim">
          <div id="pref-idp-login-preview" class="pref-login"></div>
          <div style="display:flex;flex-direction:column;gap:8px">
            <label for="pref-idp-persona" style="font-size:12px;font-weight:700">{escape(t("console.admin.idp.sim_persona"))}</label>
            <select id="pref-idp-persona" class="pref-select"></select>
            <button type="button" id="btn-idp-sim-run" class="pref-btn pref-btn-accent" style="align-self:flex-start"><span data-icon="play"></span> {escape(t("console.admin.idp.sim_run"))}</button>
            <div id="pref-idp-sim-output" style="display:flex;flex-direction:column;gap:6px"></div>
          </div>
        </div>
      </section>"""


def _render_invite_complete_html(token: str, prefilled_name: str) -> str:
    val_name = escape(prefilled_name)
    val_token = escape(token)
    return (
        "<!doctype html>"
        f"<html lang=\"{get_locale()}\">"
        "<head>"
        "<meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        "<meta name=\"robots\" content=\"noindex, nofollow\">"
        f"<title>{escape(t('auth.signup.complete_page_title'))}</title>"
        "<style>"
        "body { font-family: system-ui, -apple-system, sans-serif; max-width: 440px; margin: 40px auto; padding: 20px; line-height: 1.5; color: #222; }"
        "h1 { font-size: 1.4rem; margin-bottom: 0.5rem; }"
        "label { display: block; margin-top: 1rem; font-weight: 600; font-size: 0.9rem; }"
        "input[type=\"text\"] { width: 100%; box-sizing: border-box; padding: 8px 10px; margin-top: 4px; font-size: 1rem; border: 1px solid #ccc; border-radius: 4px; }"
        "button { width: 100%; margin-top: 1.5rem; padding: 10px; font-size: 1rem; font-weight: 600; background: #0056b3; color: #fff; border: 0; border-radius: 4px; cursor: pointer; }"
        ".hint { font-size: 0.85rem; color: #666; margin-top: 4px; }"
        "</style>"
        "</head>"
        "<body>"
        f"<h1>{escape(t('auth.signup.complete_title'))}</h1>"
        f"<p>{escape(t('auth.signup.complete_intro'))}</p>"
        "<form method=\"post\" action=\"/invite/complete\">"
        f"<input type=\"hidden\" name=\"token\" value=\"{val_token}\">"
        f"<label for=\"username\">{escape(t('auth.signup.username'))}</label>"
        "<input id=\"username\" name=\"username\" type=\"text\" required autofocus autocomplete=\"username\">"
        f"<p class=\"hint\">{escape(t('auth.signup.username_hint'))}</p>"
        f"<label for=\"display_name\">{escape(t('console.settings.account.display_name'))}</label>"
        f"<input id=\"display_name\" name=\"display_name\" type=\"text\" value=\"{val_name}\" autocomplete=\"name\">"
        f"<button type=\"submit\">{escape(t('auth.signup.complete_submit'))}</button>"
        "</form>"
        "</body>"
        "</html>"
    )


def routes(ctx: AccountsContext) -> list[Route]:
    async def oidc_start(request: Request) -> Response:
        provider_id = request.path_params.get("id", "")
        provider = await asyncio.to_thread(find_provider, ctx, provider_id, include_secrets=True)
        if provider is None or not provider.enabled:
            return not_found()

        raw_invite = request.query_params.get("invite")
        purpose = "login"
        invitation_id: int | None = None

        if raw_invite:
            is_net = await asyncio.to_thread(_is_network_mode, ctx)
            if not is_net:
                return not_found()
            if not provider.allow_signup:
                return json_error(403, "signup_disabled_for_provider", detail="Signup is not permitted for this identity provider")

            ip = client_ip(request, ctx)
            norm_code = normalize_invite_code(raw_invite)
            if not validate_invite_code_syntax(norm_code):
                auth_conn = open_auth_db(ctx)
                try:
                    auth_db.record_invite_failure(auth_conn, ip=ip)
                finally:
                    auth_conn.close()
                return not_found()

            auth_conn = open_auth_db(ctx)
            try:
                decision = auth_db.check_invite_throttle(auth_conn, ip=ip)
                if not decision.allowed:
                    delay = retry_after_seconds(decision.retry_after)
                    return Response("Too Many Requests", status_code=429, headers={"Retry-After": str(delay)})
                inv = auth_db.get_invitation_by_code(auth_conn, norm_code, check_active=True)
                purpose = "signup"
                invitation_id = inv.id
            except InviteNotFoundError:
                auth_db.record_invite_failure(auth_conn, ip=ip)
                return not_found()
            except InviteInactiveError as exc:
                return json_error(410, f"invitation_{exc.reason}", reason=exc.reason)
            finally:
                auth_conn.close()

        verifier, challenge = generate_pkce_pair()
        state = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(32)
        cookie_key = secrets.token_urlsafe(32)

        auth_conn = open_auth_db(ctx)
        try:
            auth_db.save_oidc_state(
                auth_conn,
                key=cookie_key,
                provider_id=provider.id,
                state=state,
                nonce=nonce,
                code_verifier=verifier,
                purpose=purpose,  # type: ignore[arg-type]
                invitation_id=invitation_id,
                account_id=None,
                expires_at=time.time() + OIDC_STATE_TTL,
            )
        finally:
            auth_conn.close()

        redirect_uri = callback_url(request, ctx, provider.id)
        client = get_auth_client(provider)
        if isinstance(client, GitHubAdapter):
            auth_url = client.create_authorization_url(redirect_uri=redirect_uri, state=state)
        else:
            auth_url = await client.create_authorization_url(
                redirect_uri=redirect_uri,
                state=state,
                nonce=nonce,
                code_challenge=challenge,
            )

        resp = RedirectResponse(auth_url, status_code=302)
        resp.set_cookie(
            OIDC_COOKIE_NAME,
            cookie_key,
            max_age=int(OIDC_STATE_TTL),
            path=OIDC_COOKIE_PATH,
            httponly=True,
            samesite="lax",
            secure=ctx.config.secure_cookie,
        )
        return resp

    async def me_link_start(request: Request) -> Response:
        principal = request_principal(request)
        denied = require_recent_auth(principal)
        if denied is not None:
            return denied
        if principal.account_id is None:
            return json_error(401, "unauthorized")

        provider_id = request.path_params.get("id", "")
        provider = await asyncio.to_thread(find_provider, ctx, provider_id, include_secrets=True)
        if provider is None or not provider.enabled:
            return not_found()

        verifier, challenge = generate_pkce_pair()
        state = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(32)
        cookie_key = secrets.token_urlsafe(32)

        auth_conn = open_auth_db(ctx)
        try:
            auth_db.save_oidc_state(
                auth_conn,
                key=cookie_key,
                provider_id=provider.id,
                state=state,
                nonce=nonce,
                code_verifier=verifier,
                purpose="link",
                invitation_id=None,
                account_id=principal.account_id,
                expires_at=time.time() + OIDC_STATE_TTL,
            )
        finally:
            auth_conn.close()

        redirect_uri = callback_url(request, ctx, provider.id)
        client = get_auth_client(provider)
        if isinstance(client, GitHubAdapter):
            auth_url = client.create_authorization_url(redirect_uri=redirect_uri, state=state)
        else:
            auth_url = await client.create_authorization_url(
                redirect_uri=redirect_uri,
                state=state,
                nonce=nonce,
                code_challenge=challenge,
            )

        resp = JSONResponse({"authorization_url": auth_url})
        resp.set_cookie(
            OIDC_COOKIE_NAME,
            cookie_key,
            max_age=int(OIDC_STATE_TTL),
            path=OIDC_COOKIE_PATH,
            httponly=True,
            samesite="lax",
            secure=ctx.config.secure_cookie,
        )
        return resp

    async def oidc_callback(request: Request) -> Response:
        provider_id = request.path_params.get("id", "")
        provider = await asyncio.to_thread(find_provider, ctx, provider_id, include_secrets=True)
        if provider is None or not provider.enabled:
            return not_found()

        cookie_key = request.cookies.get(OIDC_COOKIE_NAME)
        query_state = request.query_params.get("state")
        query_code = request.query_params.get("code")

        if not cookie_key or not query_state or not query_code:
            resp = json_error(400, "invalid_callback_parameters", detail="Missing state, code, or state cookie")
            resp.delete_cookie(OIDC_COOKIE_NAME, path=OIDC_COOKIE_PATH)
            return resp

        auth_conn = open_auth_db(ctx)
        try:
            try:
                state_record = auth_db.consume_oidc_state(auth_conn, key=cookie_key, state=query_state)
            except auth_db.OidcStateError as exc:
                resp = json_error(400, "invalid_oidc_state", detail=str(exc))
                resp.delete_cookie(OIDC_COOKIE_NAME, path=OIDC_COOKIE_PATH)
                return resp

            if state_record.provider_id != provider.id:
                resp = json_error(400, "provider_mismatch")
                resp.delete_cookie(OIDC_COOKIE_NAME, path=OIDC_COOKIE_PATH)
                return resp

            redirect_uri = callback_url(request, ctx, provider.id)
            client = get_auth_client(provider)

            try:
                if isinstance(client, GitHubAdapter):
                    identity = await client.exchange_code_and_get_identity(code=query_code, redirect_uri=redirect_uri)
                else:
                    token_data = await client.exchange_code(
                        code=query_code,
                        redirect_uri=redirect_uri,
                        code_verifier=state_record.code_verifier,
                    )
                    id_token = token_data.get("id_token")
                    if not id_token:
                        raise OidcVerificationError("Missing id_token in token response")
                    identity = await client.verify_id_token(str(id_token), expected_nonce=state_record.nonce)
            except OidcError as exc:
                log.warning("OIDC authentication failure: %s", exc)
                resp = json_error(400, "oidc_verification_failed", detail=str(exc))
                resp.delete_cookie(OIDC_COOKIE_NAME, path=OIDC_COOKIE_PATH)
                return resp

            if state_record.purpose == "link":
                if state_record.account_id is None:
                    resp = json_error(400, "missing_account_binding")
                    resp.delete_cookie(OIDC_COOKIE_NAME, path=OIDC_COOKIE_PATH)
                    return resp

                existing = auth_db.find_account_identity(auth_conn, iss=identity.iss, sub=identity.sub)
                if existing is not None:
                    if existing.account_id != state_record.account_id:
                        resp = json_error(409, "identity_already_linked", detail="This identity is linked to another account")
                        resp.delete_cookie(OIDC_COOKIE_NAME, path=OIDC_COOKIE_PATH)
                        return resp
                    # Already linked to this account
                    resp = RedirectResponse("/settings/account?notice=already_linked", status_code=302)
                    resp.delete_cookie(OIDC_COOKIE_NAME, path=OIDC_COOKIE_PATH)
                    return resp

                auth_db.link_account_identity(
                    auth_conn,
                    iss=identity.iss,
                    sub=identity.sub,
                    account_id=state_record.account_id,
                    provider_id=provider.id,
                )
                auth_db.record_admin_audit(
                    auth_conn,
                    actor_account_id=state_record.account_id,
                    action="identity.link",
                    detail={"iss": identity.iss, "provider": provider.id},
                )
                resp = RedirectResponse("/settings/account?notice=identity_linked", status_code=302)
                resp.delete_cookie(OIDC_COOKIE_NAME, path=OIDC_COOKIE_PATH)
                return resp

            elif state_record.purpose == "login":
                existing = auth_db.find_account_identity(auth_conn, iss=identity.iss, sub=identity.sub)
                if existing is None:
                    if _wants_json(request):
                        resp = json_error(403, "invite_required", message=t("auth.invite.required"))
                    else:
                        resp = HTMLResponse(
                            f"<!doctype html><html lang=\"{get_locale()}\"><head><meta charset=\"utf-8\"><title>{escape(t('auth.invite.required_title'))}</title></head>"
                            f"<body><h1>{escape(t('auth.invite.required'))}</h1><p>{escape(t('auth.invite.required_detail'))}</p></body></html>",
                            status_code=403,
                        )
                    resp.delete_cookie(OIDC_COOKIE_NAME, path=OIDC_COOKIE_PATH)
                    return resp

                conn = open_db(ctx)
                try:
                    store = accounts_store.AccountStore(conn)
                    try:
                        account = store.get(existing.account_id)
                    except Exception:
                        account = None
                    if account is None or account.status != "active":
                        resp = forbidden()
                        resp.delete_cookie(OIDC_COOKIE_NAME, path=OIDC_COOKIE_PATH)
                        return resp

                    auth_db.record_identity_login(auth_conn, iss=identity.iss, sub=identity.sub)
                    session_token = accounts_store.create_account_session(
                        conn,
                        account_id=account.id,
                        scope="owner",
                        replace_token=cookie_session_token(request),
                    )
                    mode = store.server_mode()
                    redirect_path = account_home_path(mode, account.username)
                finally:
                    conn.close()

                resp = RedirectResponse(redirect_path, status_code=302)
                set_session_cookie(resp, ctx, session_token)
                resp.delete_cookie(OIDC_COOKIE_NAME, path=OIDC_COOKIE_PATH)
                return resp

            elif state_record.purpose == "signup":
                if state_record.invitation_id is None:
                    resp = json_error(400, "missing_invitation_binding")
                    resp.delete_cookie(OIDC_COOKIE_NAME, path=OIDC_COOKIE_PATH)
                    return resp

                existing = auth_db.find_account_identity(auth_conn, iss=identity.iss, sub=identity.sub)
                if existing is not None:
                    resp = json_error(409, "identity_already_linked", detail="This identity is already linked to an account")
                    resp.delete_cookie(OIDC_COOKIE_NAME, path=OIDC_COOKIE_PATH)
                    return resp

                inv = auth_db.get_invitation_by_id(auth_conn, state_record.invitation_id)
                if inv is None:
                    resp = not_found()
                    resp.delete_cookie(OIDC_COOKIE_NAME, path=OIDC_COOKIE_PATH)
                    return resp
                if inv.status != "active":
                    resp = json_error(410, f"invitation_{inv.status}", reason=inv.status)
                    resp.delete_cookie(OIDC_COOKIE_NAME, path=OIDC_COOKIE_PATH)
                    return resp

                signup_token = create_signup_token(
                    ctx,
                    invitation_id=state_record.invitation_id,
                    provider_id=provider.id,
                    iss=identity.iss,
                    sub=identity.sub,
                    email=identity.email,
                    display_name=identity.display_name,
                )
                resp = RedirectResponse(f"/invite/complete?token={signup_token}", status_code=302)
                resp.delete_cookie(OIDC_COOKIE_NAME, path=OIDC_COOKIE_PATH)
                return resp

            else:
                resp = json_error(400, "unsupported_purpose")
                resp.delete_cookie(OIDC_COOKIE_NAME, path=OIDC_COOKIE_PATH)
                return resp

        finally:
            auth_conn.close()

    async def invite_complete_get(request: Request) -> Response:
        is_net = await asyncio.to_thread(_is_network_mode, ctx)
        if not is_net:
            return not_found()

        token = request.query_params.get("token", "")
        payload = verify_signup_token(ctx, token)
        if payload is None:
            return HTMLResponse(
                f"<!doctype html><html lang=\"{get_locale()}\"><head><meta charset=\"utf-8\"><title>{escape(t('auth.signup.error_title'))}</title></head>"
                f"<body><h1>{escape(t('auth.signup.token_invalid'))}</h1><p>{escape(t('auth.signup.token_invalid_detail'))}</p></body></html>",
                status_code=400,
            )
        prefilled = payload.get("display_name") or ""
        return HTMLResponse(_render_invite_complete_html(token, prefilled))

    async def invite_complete_post(request: Request) -> Response:
        is_net = await asyncio.to_thread(_is_network_mode, ctx)
        if not is_net:
            return not_found()

        content_type = request.headers.get("content-type", "")
        if "application/json" in content_type:
            try:
                body = await request.json()
            except Exception:
                return json_error(400, "invalid_json_body")
        else:
            if not same_origin(request):
                return forbidden()
            form = await request.form()
            body = dict(form)

        token = str(body.get("token", "")).strip()
        payload = verify_signup_token(ctx, token)
        if payload is None:
            return json_error(400, "invalid_or_expired_signup_token")

        username = str(body.get("username", "")).strip()
        display_name = str(body.get("display_name", "")).strip()
        if not display_name and payload.get("display_name"):
            display_name = str(payload["display_name"])

        if validate_display_name(display_name) is None:
            return json_error(400, "invalid_display_name", detail="Display name is invalid")

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
            username = validate_username(username, server_reserved=ctx.server_reserved)
        except UsernameRejected as exc:
            return json_error(400, f"username_{exc.reason}", detail=str(exc))

        def _create_account_and_link() -> int:
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

                    theme_seq_row = c_conn.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM knowledge_bases").fetchone()
                    kb_id = int(theme_seq_row[0])
                    theme_num = ids.timestamp_id("knowledge_bases", salt=salt, unix_ms=int(now_ts * 1000) + 1)
                    c_conn.execute(
                        "INSERT INTO knowledge_bases (id, seq, number, label, account_id, is_personal, is_public, created_at, updated_at, db_path) "
                        "VALUES (?, ?, ?, 'General', ?, 1, 0, ?, ?, ?)",
                        (kb_id, kb_id, theme_num, new_acc_id, now_ts, now_ts, str(ctx.db_file)),
                    )
            finally:
                c_conn.close()

            auth_db.link_account_identity(
                auth_conn,
                iss=payload["iss"],
                sub=payload["sub"],
                account_id=new_acc_id,
                provider_id=payload["provider_id"],
            )
            return new_acc_id

        auth_conn = open_auth_db(ctx)
        try:
            try:
                account_id = auth_db.redeem_invitation_atomic(
                    auth_conn,
                    payload["invitation_id"],
                    method="oidc",
                    account_callback=_create_account_and_link,
                )
            except auth_db.IdentityAlreadyLinkedError:
                return json_error(409, "identity_already_linked")
            except InviteInactiveError as exc:
                return json_error(410, f"invitation_{exc.reason}", reason=exc.reason)
            except InviteNotFoundError:
                return not_found()
        finally:
            auth_conn.close()

        conn = open_db(ctx)
        try:
            session_token = accounts_store.create_account_session(
                conn,
                account_id=account_id,
                scope="owner",
                replace_token=cookie_session_token(request),
            )
        finally:
            conn.close()

        redirect_url = f"/@{username}/"
        if _wants_json(request):
            resp = JSONResponse({"ok": True, "account_id": account_id, "username": username, "redirect_url": redirect_url})
        else:
            resp = RedirectResponse(redirect_url, status_code=302)
        set_session_cookie(resp, ctx, session_token)
        return resp

    async def me_identities_get(request: Request) -> Response:
        principal = request_principal(request)
        if principal.account_id is None:
            return json_error(401, "unauthorized")

        auth_conn = open_auth_db(ctx)
        try:
            identities = auth_db.list_account_identities(auth_conn, account_id=principal.account_id)
        finally:
            auth_conn.close()

        return JSONResponse([
            {
                "provider_id": ident.provider_id,
                "iss": ident.iss,
                "sub": ident.sub,
                "linked_at": ident.linked_at,
                "last_login_at": ident.last_login_at,
            }
            for ident in identities
        ])

    async def me_identities_delete(request: Request) -> Response:
        principal = request_principal(request)
        denied = require_recent_auth(principal)
        if denied is not None:
            return denied
        if principal.account_id is None:
            return json_error(401, "unauthorized")

        provider_id = request.path_params.get("id", "")
        auth_conn = open_auth_db(ctx)
        try:
            identities = auth_db.list_account_identities(auth_conn, account_id=principal.account_id)
            matching = [i for i in identities if i.provider_id == provider_id]
            if not matching:
                return not_found()

            sub_param = request.query_params.get("sub")
            if sub_param:
                target = next((i for i in matching if i.sub == sub_param), None)
                if target is None:
                    return not_found()
            else:
                target = matching[0]

            try:
                auth_db.unlink_account_identity(
                    auth_conn,
                    account_id=principal.account_id,
                    provider_id=target.provider_id,
                    iss=target.iss,
                    sub=target.sub,
                )
            except auth_db.IdentityUnlinkForbiddenError as exc:
                return json_error(400, "cannot_unlink_last_credential", detail=str(exc))
        finally:
            auth_conn.close()

        return JSONResponse({"ok": True})

    async def admin_idp_page(request: Request) -> Response:
        principal = request_principal(request)
        if not _is_admin(principal):
            return not_found()
        raw_theme = request.query_params.get("theme") if "theme" in request.query_params else None
        data_dir = ctx.data_dir or ctx.auth_db_file.parent.parent
        providers = await asyncio.to_thread(auth_db.load_idp_providers, data_dir, include_secrets=False)
        origin = ctx.config.public_origin.rstrip("/") if ctx.config.public_origin else str(request.base_url).rstrip("/")
        body_html = _render_admin_idp_section_html(providers, origin)
        providers_payload = [
            {
                "id": p.id,
                "type": p.type,
                "display_name": p.display_name,
                "client_id": p.client_id,
                "enabled": p.enabled,
                "allow_signup": p.allow_signup,
                "issuer": p.issuer,
                "scopes": list(p.scopes),
                "hosted_domain": p.hosted_domain,
                "tenant_id": p.tenant_id,
                "secret_configured": p.secret_configured,
            }
            for p in providers
        ]
        return await asyncio.to_thread(
            _render_console_section,
            ctx,
            spec=ADMIN_SPEC_BY_SLUG["idp"],
            principal=principal,
            raw_theme=raw_theme,
            custom_body_html=body_html,
            extra_bootstrap={"idp_providers": providers_payload, "origin": origin},
        )

    async def admin_idp_get(request: Request) -> Response:
        principal = request_principal(request)
        if not _is_admin(principal):
            return not_found()
        data_dir = ctx.data_dir or ctx.auth_db_file.parent.parent
        providers = await asyncio.to_thread(auth_db.load_idp_providers, data_dir, include_secrets=False)
        return JSONResponse({
            "providers": [
                {
                    "id": p.id,
                    "type": p.type,
                    "display_name": p.display_name,
                    "client_id": p.client_id,
                    "enabled": p.enabled,
                    "allow_signup": p.allow_signup,
                    "issuer": p.issuer,
                    "scopes": list(p.scopes),
                    "hosted_domain": p.hosted_domain,
                    "tenant_id": p.tenant_id,
                    "secret_configured": p.secret_configured,
                }
                for p in providers
            ]
        })

    async def admin_idp_put(request: Request) -> Response:
        principal = request_principal(request)
        if not _is_admin(principal):
            return not_found()
        body = await read_json_body(request)
        items = body.get("providers") if isinstance(body, dict) and "providers" in body else (body if isinstance(body, list) else None)
        if items is None or not isinstance(items, list):
            return json_error(400, "invalid_request", detail="Expected a list of providers or {'providers': [...]}")

        parsed_providers: list[IdpProviderMeta] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            pid = str(item.get("id", "")).strip()
            if not pid:
                continue
            parsed_providers.append(
                IdpProviderMeta(
                    id=pid,
                    type=item.get("type", "oidc"),
                    display_name=str(item.get("display_name", pid)),
                    client_id=str(item.get("client_id", "")),
                    enabled=bool(item.get("enabled", True)),
                    allow_signup=bool(item.get("allow_signup", True)),
                    issuer=str(item.get("issuer", "")),
                    scopes=tuple(item.get("scopes", ["openid", "email", "profile"])),
                    hosted_domain=item.get("hosted_domain"),
                    tenant_id=item.get("tenant_id"),
                )
            )

        data_dir = ctx.data_dir or ctx.auth_db_file.parent.parent
        await asyncio.to_thread(auth_db.save_idp_providers, data_dir, parsed_providers)

        auth_conn = open_auth_db(ctx)
        try:
            auth_db.record_admin_audit(
                auth_conn,
                actor_account_id=principal.account_id,
                action="idp.update",
                detail={"count": len(parsed_providers)},
            )
        finally:
            auth_conn.close()

        return JSONResponse({"ok": True, "count": len(parsed_providers)})

    return [
        Route("/auth/oidc/{id}/start", oidc_start, methods=["GET"]),
        Route("/auth/oidc/{id}/callback", oidc_callback, methods=["GET"]),
        Route("/api/me/identities/oidc/{id}/start", me_link_start, methods=["POST"]),
        Route("/api/me/identities", me_identities_get, methods=["GET"]),
        Route("/api/me/identities/oidc/{id}", me_identities_delete, methods=["DELETE"]),
        Route("/invite/complete", invite_complete_get, methods=["GET"]),
        Route("/invite/complete", invite_complete_post, methods=["POST"]),
        Route("/admin/idp", admin_idp_page, methods=["GET"]),
        Route("/api/admin/idp", admin_idp_get, methods=["GET"]),
        Route("/api/admin/idp", admin_idp_put, methods=["PUT"]),
    ]
