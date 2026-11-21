"""서버 모드 설정·전환, 준비 검사, 일몰 시계 관리 API (설계 §4.2, §5.3, §8.6, §17.2, O6, O7).

- ``GET /admin/server``: 관리자 전용 HTML 페이지 (모드, G1-G9 준비 검사, 일몰 시계 상태)
- ``GET /api/admin/server``: JSON API (현재 모드, readiness 검사 결과, network_mode_available, legacy_host_policy)
- ``POST /api/admin/server/mode``: 모드 전환 API (단계 인증 필수)
- ``POST /api/admin/server/invites``: 비관리자 초대 코드 발급 허용 설정 API (단계 인증 필수)
- ``POST /api/admin/server/legacy-hosts/sunset``: 일몰 시계 관리 API (단계 인증 필수)
- ``GET/HEAD/PUT /api/admin/server-tier``: 서버 등급 조회·저장 (설계 §10.3, P4 L4-c). PUT은 단계 인증, 검증 실패는 422, ``admin_audit`` 기록
"""

from __future__ import annotations

import asyncio
from typing import Any

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Route

from ...accounts import readiness
from ...accounts import server_tier
from ...accounts.provider_policy import EFFORT_ORDER, TIER_FEATURES, TIER_PROVIDERS, ServerTier
from ...i18n import t
from ...store import accounts as accounts_store
from ...store import auth_db
from ...store import legacy_hosts
from ...store import usage_ledger
from .. import clients_host
from ..host_router import HostRoles, LegacyHost, LegacyHostPolicy
from ..principal import request_principal
from ..security import read_json_body
from .admin import is_admin
from .common import (
    AccountsContext,
    escape,
    format_ts,
    json_error,
    not_found,
    open_auth_db,
    open_db,
    require_recent_auth,
    same_origin,
    t_code,
)
from .console import ADMIN_SPEC_BY_SLUG, _render_console_section

ADMIN_SERVER_PATH = "/admin/server"
ADMIN_SERVER_API_PATH = "/api/admin/server"
ADMIN_SERVER_MODE_PATH = "/api/admin/server/mode"
ADMIN_SERVER_CLIENT_DELIVERY_PATH = "/api/admin/server/client-delivery"
ADMIN_SERVER_INVITES_PATH = "/api/admin/server/invites"
ADMIN_SERVER_SUNSET_PATH = "/api/admin/server/legacy-hosts/sunset"
ADMIN_SERVER_TIER_PATH = "/api/admin/server-tier"

_CLIENT_DELIVERY_ALLOWED_FIELDS = frozenset({"agent_client_delivery_mode"})

_LABELLED_FEATURES = ("expand", "stt", "vlm", "presentation", "synthesize")


def _feature_label(feature: str) -> str:
    """The name of a server tier feature in the active locale (catalog key ``console.admin.tier.feature.<feature>``)."""
    return t(f"console.admin.tier.feature.{feature}") if feature in _LABELLED_FEATURES else feature


def _server_tier_state(conn: Any) -> dict[str, Any]:
    """저장 상태: ``configured``(값 있음), ``valid``(``from_dict`` 통과), ``tier``(실행 중 집행에 쓰는 값, 깨졌으면 기본 none)."""
    raw = server_tier.read_stored_server_tier(conn)
    valid = False
    if raw is not None:
        try:
            server_tier.parse_server_tier(raw)
            valid = True
        except ValueError:
            valid = False
    return {
        "configured": raw is not None,
        "valid": valid,
        "tier": server_tier.load_server_tier(conn).to_dict(),
    }


def _usage_summary(conn: Any, auth_conn: Any) -> list[dict[str, Any]]:
    """이번 달(UTC) 계정별 사용량. 계정은 ``public_id``(문자열)로만 내보낸다."""
    snapshots = usage_ledger.list_usage(auth_conn)
    if not snapshots:
        return []
    public_ids = {int(r[0]): str(r[1]) for r in conn.execute("SELECT id, public_id FROM accounts")}
    return [
        {
            "public_id": public_ids.get(u.account_id),
            "tokens_in": u.tokens_in,
            "tokens_out": u.tokens_out,
            "ingests": u.ingests,
            "daily_ingests": u.daily_ingests,
        }
        for u in snapshots
    ]


def _render_server_tier_section(state: dict[str, Any]) -> str:
    tier = ServerTier.from_dict(state["tier"])
    if not state["configured"]:
        status = f'<span style="color:#c53030;font-weight:bold;">{escape(t("console.admin.tier.state.unset"))}</span> {t_code("console.admin.tier.state.unset_hint", none="none")}'
    elif not state["valid"]:
        status = f'<span style="color:#c53030;font-weight:bold;">{escape(t("console.admin.tier.state.invalid"))}</span> {t_code("console.admin.tier.state.invalid_hint", none="none")}'
    else:
        status = f'<span style="color:#2f855a;font-weight:bold;">{escape(t("console.admin.tier.state.set"))}</span>'
    provider_options = "".join(
        f'<option value="{escape(p)}"{" selected" if p == tier.provider else ""}>{escape(p)}</option>'
        for p in TIER_PROVIDERS
    )
    effort_options = "".join(
        f'<option value="{escape(e)}"{" selected" if e == tier.max_effort else ""}>{escape(e)}</option>'
        for e in EFFORT_ORDER
    )
    feature_boxes = "".join(
        f'<label class="check"><input type="checkbox" name="features" value="{escape(f)}"'
        f'{" checked" if f in tier.features else ""}> {escape(_feature_label(f))}</label>'
        for f in TIER_FEATURES
    )

    def num(name: str, label: str, value: int, minimum: int, hint: str = "") -> str:
        hint_html = f'<span class="hint">{escape(hint)}</span>' if hint else ""
        return (
            f'<label>{escape(label)}<input type="number" name="{name}" min="{minimum}" step="1" '
            f'value="{value}" required>{hint_html}</label>'
        )

    feat_summary = ", ".join(_feature_label(f) for f in tier.features) if tier.features else t("console.admin.tier.features_none")
    prov_summary = "Google AI Studio (gemini)" if tier.provider == "gemini" else t("console.admin.tier.provider_none")

    return f"""
      <section class="card" aria-label="{escape(t("console.admin.tier.label"))}" id="server-tier">
        <h2>{escape(t("console.admin.tier.title"))}</h2>
        <p class="hint">{escape(t("console.common.status"))}: {status}</p>
        <p class="hint">{escape(t("console.admin.tier.hint"))} <a href="/admin/providers"><strong>{escape(t("console.admin.providers.label"))}</strong></a></p>
        <dl>
          <dt>{escape(t("console.admin.tier.provider_model"))}</dt><dd>{escape(prov_summary)}{f" · {escape(tier.model)}" if tier.model else ""} ({escape(t("console.admin.tier.max_effort_value", effort=tier.max_effort))})</dd>
          <dt>{escape(t("console.tier.features"))}</dt><dd>{escape(feat_summary)}</dd>
        </dl>
        <form class="tier-form" data-server-tier-form>
          <div hidden>
            <label>{escape(t("console.settings.keys.provider"))}<select name="provider">{provider_options}</select></label>
            <label>{escape(t("console.tier.model"))}<input type="text" name="model" value="{escape(tier.model)}" maxlength="128" autocomplete="off"></label>
            <label>{escape(t("console.admin.tier.max_effort"))}<select name="max_effort">{effort_options}</select></label>
            <fieldset><legend>{escape(t("console.tier.features"))}</legend>{feature_boxes}</fieldset>
          </div>
          {num("monthly_tokens", t("console.admin.tier.monthly_tokens"), tier.monthly_tokens, 0, t("console.admin.tier.monthly_tokens_hint"))}
          {num("daily_ingests", t("console.admin.tier.daily_ingests"), tier.daily_ingests, 0)}
          {num("storage_mb", t("console.admin.accounts.userdb.quota_storage"), tier.storage_mb, 0)}
          {num("max_documents", t("console.admin.tier.max_documents"), tier.max_documents, 0)}
          {num("byok_max_concurrency", t("console.admin.tier.byok_concurrency"), tier.byok_max_concurrency, 1)}
          <button type="submit">{escape(t("console.admin.tier.submit"))}</button>
          <p class="result" role="status" aria-live="polite" data-server-tier-result></p>
        </form>
        <form class="tier-form" data-server-tier-reauth hidden>
          <label>{escape(t("console.admin.tier.reauth"))}<input type="password" name="password" autocomplete="current-password" required></label>
          <button type="submit">{escape(t("console.reauth.submit"))}</button>
          <p class="result" role="status" aria-live="polite" data-server-tier-reauth-result></p>
        </form>
      </section>"""


def _legacy_host_to_dict(h: LegacyHost) -> dict[str, Any]:
    return {
        "host": h.host,
        "kb_id": h.kb_id,
        "created_at": h.created_at,
        "activated_at": h.activated_at,
        "sunset_at": h.sunset_at,
    }


def _resolve_roles(ctx: AccountsContext, settings: Any) -> HostRoles | None:
    cfg = ctx.config
    roles = getattr(cfg, "host_roles", None)
    if roles is not None:
        return roles
    if hasattr(cfg, "environment") and hasattr(cfg, "expected_authority"):
        try:
            return HostRoles.from_settings(settings, environment=cfg.environment, app_authority=cfg.expected_authority)
        except Exception:
            return None
    return None


def _render_client_delivery_section(delivery: dict[str, Any] | None) -> str:
    if delivery is None:
        return ""
    mode = str(delivery.get("mode") or clients_host.DEFAULT_AGENT_CLIENT_DELIVERY_MODE)
    fqdn_available = bool(delivery.get("fqdn_available"))
    active_base = str(delivery.get("active_base_url") or "/clients")
    marketplace_url = str(delivery.get("marketplace_url") or f"{active_base.rstrip('/')}/.claude-plugin/marketplace.json")
    uri_base = str(delivery.get("uri_path_base_url") or "/clients")
    fqdn_origin = delivery.get("fqdn_origin")
    fqdn_display = f"<code>{escape(str(fqdn_origin))}</code>" if fqdn_origin else f'<span class="hint">{escape(t("console.admin.server.client_delivery.unavailable"))}</span>'
    uri_selected = " selected" if mode == "uri_path" else ""
    fqdn_selected = " selected" if mode == "fqdn" else ""
    fqdn_disabled = "" if fqdn_available else " disabled"
    title = escape(t("console.admin.server.client_delivery.title"))
    return f"""
      <section class="card" aria-label="{title}" id="agent-client-delivery">
        <h2>{title}</h2>
        <p class="hint">{escape(t("console.admin.server.client_delivery.hint"))}</p>
        <dl>
          <dt>{escape(t("console.admin.server.client_delivery.active_mode"))}</dt><dd><span class="badge">{escape(mode)}</span></dd>
          <dt>{escape(t("console.admin.server.client_delivery.resolved_base"))}</dt><dd><code>{escape(active_base)}</code></dd>
          <dt>{escape(t("console.admin.server.client_delivery.marketplace_url"))}</dt><dd><code>{escape(marketplace_url)}</code></dd>
          <dt>{escape(t("console.admin.server.client_delivery.uri_base"))}</dt><dd><code>{escape(uri_base)}</code></dd>
          <dt>{escape(t("console.admin.server.client_delivery.fqdn_origin"))}</dt><dd>{fqdn_display}</dd>
        </dl>
        <form class="mode-form" method="post" action="{ADMIN_SERVER_PATH}" data-client-delivery-form>
          <label for="agent-client-delivery-mode">{escape(t("console.admin.server.client_delivery.mode_label"))}</label>
          <select id="agent-client-delivery-mode" name="agent_client_delivery_mode">
            <option value="uri_path"{uri_selected}>uri_path</option>
            <option value="fqdn"{fqdn_selected}{fqdn_disabled}>fqdn</option>
          </select>
          <button type="submit">{escape(t("console.admin.server.client_delivery.submit"))}</button>
        </form>
      </section>"""


def _render_invites_policy_section(*, member_invites_enabled: bool) -> str:
    checked_attr = " checked" if member_invites_enabled else ""
    title = escape(t("console.admin.server.invites.title"))
    label = escape(t("console.admin.server.invites.label"))
    return f"""
      <section class="card" aria-label="{label}" id="server-invites-policy">
        <h2><span data-icon="mail"></span> {title}</h2>
        <p class="hint">{escape(t("console.admin.server.invites.hint"))}</p>
        <div class="pref-row">
          <div class="pref-row-label">
            <label for="admin-member-invites-toggle"><strong>{escape(t("console.admin.server.invites.toggle_title"))}</strong></label>
            <span>{escape(t("console.admin.server.invites.toggle_desc"))}</span>
          </div>
          <input id="admin-member-invites-toggle" type="checkbox" switch{checked_attr} aria-label="{escape(t("console.admin.server.invites.toggle_title"))}"/>
        </div>
        <p class="result" id="server-invites-policy-result" role="status" aria-live="polite"></p>
        <a class="pref-btn" href="/settings/invites">{escape(t("console.admin.server.invites.manage_link"))}</a>
      </section>"""


def _render_server_html(
    *,
    mode: str,
    readiness_report: readiness.ReadinessReport,
    network_mode_available: bool,
    policy: LegacyHostPolicy,
    hosts: list[LegacyHost],
    tier_state: dict[str, Any],
    client_delivery: dict[str, Any] | None = None,
    member_invites_enabled: bool = False,
) -> str:
    tier_section = _render_server_tier_section(tier_state)
    client_delivery_section = _render_client_delivery_section(client_delivery)
    invites_policy_section = _render_invites_policy_section(member_invites_enabled=member_invites_enabled)
    mode_text = t("console.admin.server.mode_single") if mode == "single" else t("console.admin.server.mode_network")
    target_mode = "network" if mode == "single" else "single"

    if mode == "single":
        if not network_mode_available:
            mode_notice = f'<p class="hint" style="color:var(--error);font-weight:600;">{escape(t("console.admin.server.notice.limited"))}</p>'
            can_switch = False
        elif not readiness_report.passed:
            failed_ids = [item.id for item in readiness_report.items if not item.passed]
            mode_notice = f'<p class="hint" style="color:var(--error);font-weight:600;">{escape(t("console.admin.server.notice.not_ready", items=", ".join(failed_ids)))}</p>'
            can_switch = False
        else:
            mode_notice = f'<p class="hint" style="color:#2f855a;font-weight:600;">{escape(t("console.admin.server.notice.ready"))}</p>'
            can_switch = True

        mode_desc = f"""
          <div class="notice">
            <strong>{escape(t("console.admin.server.to_network.title"))}</strong>
            <ul>
              <li>{t_code("console.admin.server.to_network.home", path="/@{admin_username}/")}</li>
              <li>{t_code("console.admin.server.to_network.redirect", doc="/?doc=…", theme="/themes/{number}/…")}</li>
              <li>{escape(t("console.admin.server.to_network.sunset"))}</li>
            </ul>
          </div>
          <label class="check">
            <input type="checkbox" name="confirm" required>
            {escape(t("console.admin.server.to_network.confirm"))}
          </label>
        """
        btn_class = ""
        btn_text = t("console.admin.server.switch_to_network")
    else:
        can_switch = True
        mode_notice = f'<p class="hint">{escape(t("console.admin.server.notice.network"))}</p>'
        mode_desc = f"""
          <div class="notice danger">
            <strong>{escape(t("console.admin.server.to_single.title"))}</strong>
            <ul>
              <li>{escape(t("console.admin.server.to_single.members"))}</li>
              <li>{escape(t("console.admin.server.to_single.username"))}</li>
              <li>{t_code("console.admin.server.to_single.redirect", namespace="/@{admin}/…", root="/…")}</li>
            </ul>
          </div>
          <label class="check">
            <input type="checkbox" name="confirm" required>
            {escape(t("console.admin.server.to_single.confirm"))}
          </label>
        """
        btn_class = ' class="btn-danger"'
        btn_text = t("console.admin.server.switch_to_single")

    check_rows = []
    for item in readiness_report.items:
        status_tag = f'<span style="color:#2f855a;font-weight:bold;">{escape(t("console.readiness.passed"))}</span>' if item.passed else f'<span style="color:#c53030;font-weight:bold;">{escape(t("console.readiness.failed"))}</span>'
        check_rows.append(f"<tr><td><code>{escape(item.id)}</code></td><td>{escape(item.name)}</td><td>{status_tag}</td><td>{escape(item.detail)}</td></tr>")
    checks_tbody = "".join(check_rows)

    hosts_rows = []
    for h in hosts:
        activated = format_ts(h.activated_at) if h.activated_at is not None else t("console.admin.server.legacy.not_started")
        sunset = format_ts(h.sunset_at) if h.sunset_at is not None else t("console.admin.server.legacy.no_limit")
        hosts_rows.append(f"<tr><td><code>{escape(h.host)}</code></td><td>{h.kb_id}</td><td>{escape(activated)}</td><td>{escape(sunset)}</td></tr>")
    hosts_tbody = "".join(hosts_rows) if hosts_rows else f'<tr><td colspan="4" style="text-align:center;color:var(--muted);">{escape(t("console.admin.server.legacy.empty"))}</td></tr>'

    readiness_summary = f'<span style="color:#2f855a;font-weight:bold;">{escape(t("console.admin.server.readiness_all"))}</span>' if readiness_report.passed else f'<span style="color:#c53030;font-weight:bold;">{escape(t("console.admin.server.readiness_not_passed"))}</span>'

    return f"""
      <section class="card" aria-label="{escape(t("console.mode.title"))}" id="server-mode">
        <h2>{escape(t("console.mode.title"))}</h2>
        <dl>
          <dt>{escape(t("console.admin.server.current_mode"))}</dt><dd><span class="badge">{escape(mode_text)}</span></dd>
          <dt>{escape(t("console.admin.server.network_availability"))}</dt><dd><strong>{escape(t("console.admin.server.network_available") if network_mode_available else t("console.admin.server.network_limited"))}</strong></dd>
          <dt>{escape(t("console.admin.server.readiness"))}</dt><dd>{readiness_summary}</dd>
        </dl>
        {mode_notice}
        <form class="mode-form" data-mode-switch-form>
          <input type="hidden" name="target_mode" value="{target_mode}">
          {mode_desc}
          <button type="submit"{btn_class}{"" if can_switch else " disabled"}>{escape(btn_text)}</button>
          <p class="result" role="status" aria-live="polite" data-mode-switch-result></p>
        </form>
        <form class="mode-form" data-mode-switch-reauth hidden>
          <p class="hint">{escape(t("console.admin.server.reauth_hint"))}</p>
          <label>{escape(t("console.settings.account.password"))}<input type="password" name="password" autocomplete="current-password" required></label>
          <button type="submit">{escape(t("console.admin.server.reauth_submit"))}</button>
          <p class="result" role="status" aria-live="polite" data-mode-switch-reauth-result></p>
        </form>
      </section>

      <section class="card" aria-label="{escape(t("console.admin.server.readiness_label"))}">
        <h2>{escape(t("console.admin.server.readiness_title"))}</h2>
        <table>
          <thead>
            <tr><th>{escape(t("console.readiness.col_item"))}</th><th>{escape(t("console.readiness.col_name"))}</th><th>{escape(t("console.common.status"))}</th><th>{escape(t("console.readiness.col_detail"))}</th></tr>
          </thead>
          <tbody>
            {checks_tbody}
          </tbody>
        </table>
      </section>

      <section class="card" aria-label="{escape(t("console.admin.server.legacy.label"))}">
        <h2>{escape(t("console.admin.server.legacy.title"))}</h2>
        <dl>
          <dt>{escape(t("console.admin.server.legacy.after_status"))}</dt><dd>HTTP {policy.after_sunset_status}</dd>
          <dt>{escape(t("console.admin.server.legacy.grace"))}</dt><dd>{escape(t("console.admin.server.legacy.days", days=policy.sunset_days))}</dd>
        </dl>
        <table>
          <thead>
            <tr><th>{escape(t("console.admin.server.legacy.col_host"))}</th><th>{escape(t("console.admin.server.legacy.col_theme"))}</th><th>{escape(t("console.admin.server.legacy.col_start"))}</th><th>{escape(t("console.admin.server.legacy.col_sunset"))}</th></tr>
          </thead>
          <tbody>
            {hosts_tbody}
          </tbody>
        </table>
      </section>
{tier_section}
{invites_policy_section}
{client_delivery_section}"""


def routes(ctx: AccountsContext) -> list[Route]:
    async def admin_server_page(request: Request) -> Response:
        principal = request_principal(request)
        if not is_admin(principal):
            return not_found()

        settings = getattr(request.app.state, "settings", None) or ctx.config
        roles = _resolve_roles(ctx, settings)
        raw_theme = request.query_params.get("kb") if "kb" in request.query_params else None

        def _render() -> Response:
            conn = open_db(ctx)
            try:
                store = accounts_store.AccountStore(conn)
                mode = store.server_mode()
                member_invites_enabled = store.member_invites_enabled()
                report = readiness.check_readiness(conn, settings)
                policy = legacy_hosts.read_legacy_host_policy(conn)
                hosts = legacy_hosts.list_legacy_hosts(conn)
                tier_state = _server_tier_state(conn)
                client_delivery = clients_host.delivery_summary(conn, settings, roles, anonymous_readonly=bool(getattr(ctx.config, "anonymous_readonly", getattr(settings, "anonymous_readonly", False))))
            finally:
                conn.close()

            body_html = _render_server_html(
                mode=mode,
                readiness_report=report,
                network_mode_available=readiness.NETWORK_MODE_AVAILABLE,
                policy=policy,
                hosts=hosts,
                tier_state=tier_state,
                client_delivery=client_delivery,
                member_invites_enabled=member_invites_enabled,
            )
            extra_scripts = (
                '<script src="/static/js/server_mode.js" defer></script>\n'
                '<script src="/static/js/server_tier.js" defer></script>'
            )
            return _render_console_section(
                ctx,
                spec=ADMIN_SPEC_BY_SLUG["server"],
                principal=principal,
                raw_theme=raw_theme,
                custom_body_html=body_html,
                extra_scripts_html=extra_scripts,
            )

        return await asyncio.to_thread(_render)

    async def admin_server_api(request: Request) -> Response:
        principal = request_principal(request)
        if not is_admin(principal):
            return not_found()

        settings = getattr(request.app.state, "settings", None) or ctx.config
        roles = _resolve_roles(ctx, settings)

        def _fetch_api_data() -> dict[str, Any]:
            conn = open_db(ctx)
            try:
                store = accounts_store.AccountStore(conn)
                mode = store.server_mode()
                report = readiness.check_readiness(conn, settings)
                policy = legacy_hosts.read_legacy_host_policy(conn)
                hosts = legacy_hosts.list_legacy_hosts(conn)
                client_delivery = clients_host.delivery_summary(conn, settings, roles, anonymous_readonly=bool(getattr(ctx.config, "anonymous_readonly", getattr(settings, "anonymous_readonly", False))))
                return {
                    "mode": mode,
                    "readiness": report.to_dict(),
                    "network_mode_available": readiness.NETWORK_MODE_AVAILABLE,
                    "legacy_host_policy": {
                        "after_sunset_status": policy.after_sunset_status,
                        "sunset_days": policy.sunset_days,
                    },
                    "legacy_hosts": [_legacy_host_to_dict(h) for h in hosts],
                    "agent_client_delivery_mode": client_delivery["mode"],
                    "agent_client_delivery": client_delivery,
                    "member_invites_enabled": store.member_invites_enabled(),
                }
            finally:
                conn.close()

        data = await asyncio.to_thread(_fetch_api_data)
        return JSONResponse(data)

    async def admin_server_mode_api(request: Request) -> Response:
        principal = request_principal(request)
        if not is_admin(principal):
            return not_found()
        if not same_origin(request):
            return json_error(403, "forbidden")
        denied = require_recent_auth(principal)
        if denied is not None:
            return denied

        body = await read_json_body(request)
        target_mode = body.get("mode")
        if target_mode not in accounts_store.SERVER_MODES:
            return json_error(400, "invalid_mode", detail=f"mode must be one of {sorted(accounts_store.SERVER_MODES)}")

        settings = getattr(request.app.state, "settings", None) or ctx.config

        def _execute_mode_change() -> tuple[int, dict[str, Any]]:
            conn = open_db(ctx)
            try:
                store = accounts_store.AccountStore(conn)
                current_mode = store.server_mode()
                if target_mode == current_mode:
                    return 200, {"mode": target_mode, "changed": False}

                if target_mode == "network":
                    if not readiness.NETWORK_MODE_AVAILABLE:
                        return 400, {
                            "error": "network_mode_not_available",
                            "detail": "Network mode is not available in this release (NETWORK_MODE_AVAILABLE=False)",
                        }
                    report = readiness.check_readiness(conn, settings)
                    if not report.passed:
                        return 400, {
                            "error": "readiness_failed",
                            "detail": "Server readiness checks did not pass",
                            "failures": [item.to_dict() for item in report.items if not item.passed],
                        }
                    multi_theme = getattr(settings, "multi_theme", True)
                    if not multi_theme:
                        return 400, {
                            "error": "multi_theme_required",
                            "detail": "CLAIRE_MULTI_THEME=1 is required for network mode",
                        }

                    store.set_setting("mode", "network", updated_by=principal.account_id)
                    legacy_hosts.activate_sunset_clock(conn)
                    return 200, {"mode": "network", "changed": True}

                # network -> single:
                # The switch ``federation`` is no condition: the server actor and the federation of shared knowledge work in single mode (KNOWLEDGE_FEDERATION_DESIGN.md Section 15 rules 5 and 6). A change to single mode still needs no members.
                # invited/active/suspended 상태의 멤버가 없어야 한다
                rows = conn.execute(
                    "SELECT id, username, status, role FROM accounts WHERE role != 'admin' AND status IN ('invited', 'active', 'suspended')"
                ).fetchall()
                if rows:
                    return 400, {
                        "error": "members_exist",
                        "detail": "Cannot switch to single mode while non-admin members exist",
                        "members": [{"id": r[0], "username": r[1], "status": r[2], "role": r[3]} for r in rows],
                    }

                store.set_setting("mode", "single", updated_by=principal.account_id)
                # The USERNAME of the admin stays public: single mode serves ``/@{username}/`` (KNOWLEDGE_FEDERATION_DESIGN.md Section 15). The old marker ``admin_username_published`` (O15) is withdrawn with rule S9; a stored row stays and has no effect.
                return 200, {"mode": "single", "changed": True}
            finally:
                conn.close()

        status_code, resp_data = await asyncio.to_thread(_execute_mode_change)
        return JSONResponse(resp_data, status_code=status_code)

    async def admin_server_sunset_api(request: Request) -> Response:
        principal = request_principal(request)
        if not is_admin(principal):
            return not_found()
        if not same_origin(request):
            return json_error(403, "forbidden")
        denied = require_recent_auth(principal)
        if denied is not None:
            return denied

        body = await read_json_body(request)
        action = body.get("action")

        def _execute_sunset_action() -> tuple[int, dict[str, Any]]:
            conn = open_db(ctx)
            try:
                if action == "activate":
                    raw_hosts = body.get("hosts")
                    hosts_arg = [str(h) for h in raw_hosts] if isinstance(raw_hosts, (list, tuple)) else None
                    days_arg = body.get("days")
                    if days_arg is not None and not isinstance(days_arg, int):
                        return 400, {"error": "invalid_days", "detail": "days must be an integer or null"}
                    try:
                        changed = legacy_hosts.activate_sunset_clock(conn, hosts=hosts_arg, days=days_arg)
                    except ValueError as exc:
                        return 400, {"error": "invalid_days", "detail": str(exc)}
                    return 200, {"action": "activate", "changed": changed}

                if action == "extend":
                    host = body.get("host")
                    days = body.get("days")
                    if not isinstance(host, str) or not host.strip():
                        return 400, {"error": "host_required", "detail": "host string is required"}
                    if not isinstance(days, int):
                        return 400, {"error": "days_required", "detail": "integer days is required"}
                    try:
                        res = legacy_hosts.extend_sunset(conn, host.strip(), days=days)
                    except KeyError:
                        return 404, {"error": "host_not_found", "detail": f"Legacy host not found: {host}"}
                    except ValueError as exc:
                        return 400, {"error": "invalid_sunset_days", "detail": str(exc)}
                    return 200, {"action": "extend", "host": _legacy_host_to_dict(res)}

                if action == "keep_indefinitely":
                    host = body.get("host")
                    if not isinstance(host, str) or not host.strip():
                        return 400, {"error": "host_required", "detail": "host string is required"}
                    try:
                        res = legacy_hosts.keep_indefinitely(conn, host.strip())
                    except KeyError:
                        return 404, {"error": "host_not_found", "detail": f"Legacy host not found: {host}"}
                    return 200, {"action": "keep_indefinitely", "host": _legacy_host_to_dict(res)}

                if action == "update_policy":
                    status = body.get("after_sunset_status")
                    days = body.get("sunset_days")
                    current = legacy_hosts.read_legacy_host_policy(conn)
                    target_status = status if status is not None else current.after_sunset_status
                    target_days = days if days is not None else current.sunset_days
                    if target_status not in (410, 421):
                        return 400, {"error": "invalid_status", "detail": "after_sunset_status must be 410 or 421"}
                    if not isinstance(target_days, int) or target_days < 1:
                        return 400, {"error": "invalid_days", "detail": "sunset_days must be a positive integer"}
                    new_policy = LegacyHostPolicy(after_sunset_status=target_status, sunset_days=target_days)
                    try:
                        legacy_hosts.write_legacy_host_policy(conn, new_policy, updated_by=principal.account_id)
                    except ValueError as exc:
                        return 400, {"error": "invalid_policy", "detail": str(exc)}
                    return 200, {
                        "action": "update_policy",
                        "policy": {
                            "after_sunset_status": new_policy.after_sunset_status,
                            "sunset_days": new_policy.sunset_days,
                        },
                    }

                return 400, {
                    "error": "invalid_action",
                    "detail": "action must be one of 'activate', 'extend', 'keep_indefinitely', 'update_policy'",
                }
            finally:
                conn.close()

        status_code, resp_data = await asyncio.to_thread(_execute_sunset_action)
        return JSONResponse(resp_data, status_code=status_code)

    async def admin_server_tier_get(request: Request) -> Response:
        principal = request_principal(request)
        if not is_admin(principal):
            return not_found()

        def _fetch() -> dict[str, Any]:
            conn = open_db(ctx)
            try:
                auth_conn = open_auth_db(ctx)
                try:
                    usage = _usage_summary(conn, auth_conn)
                finally:
                    auth_conn.close()
                return {
                    **_server_tier_state(conn),
                    "period": usage_ledger.utc_period(),
                    "day": usage_ledger.utc_day(),
                    "usage": usage,
                }
            finally:
                conn.close()

        data = await asyncio.to_thread(_fetch)
        return JSONResponse(data, headers={"Cache-Control": "no-store"})

    async def admin_server_tier_put(request: Request) -> Response:
        principal = request_principal(request)
        if not is_admin(principal):
            return not_found()
        if not same_origin(request):
            return json_error(403, "forbidden")
        denied = require_recent_auth(principal)
        if denied is not None:
            return denied

        body = await read_json_body(request)
        payload = body.get("tier") if set(body) == {"tier"} else body
        try:
            tier = ServerTier.from_dict(payload)
        except ValueError as exc:
            return json_error(422, "invalid_server_tier", detail=str(exc))

        def _save() -> dict[str, Any]:
            conn = open_db(ctx)
            try:
                auth_conn = open_auth_db(ctx)
                try:
                    server_tier.save_server_tier(conn, tier, updated_by=principal.account_id)
                    auth_db.record_admin_audit(
                        auth_conn,
                        actor_account_id=principal.account_id,
                        action="server_tier.update",
                        detail={"tier": tier.to_dict()},
                    )
                finally:
                    auth_conn.close()
                return {"ok": True, **_server_tier_state(conn)}
            finally:
                conn.close()

        data = await asyncio.to_thread(_save)
        return JSONResponse(data, headers={"Cache-Control": "no-store"})

    def _apply_client_delivery_update(principal: Any, settings: Any, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        unknown = sorted(set(payload) - _CLIENT_DELIVERY_ALLOWED_FIELDS)
        if unknown:
            return 400, {"error": "custom_host_or_path_disallowed", "detail": f"Unsupported fields: {', '.join(unknown)}"}
        target_mode = payload.get("agent_client_delivery_mode")
        if not isinstance(target_mode, str) or target_mode not in clients_host.AGENT_CLIENT_DELIVERY_MODES:
            return 400, {"error": "invalid_delivery_mode", "detail": f"agent_client_delivery_mode must be one of {clients_host.AGENT_CLIENT_DELIVERY_MODES}"}
        roles = _resolve_roles(ctx, settings)
        if target_mode == "fqdn" and (roles is None or roles.clients is None):
            return 400, {"error": "clients_host_unavailable", "detail": "Isolated clients host is unavailable in this environment"}

        conn = open_db(ctx)
        try:
            clients_host.write_delivery_mode(conn, target_mode, updated_by=principal.account_id, roles=roles)
            auth_conn = open_auth_db(ctx)
            try:
                auth_db.record_admin_audit(
                    auth_conn,
                    actor_account_id=principal.account_id,
                    action="server.client_delivery.update",
                    detail={"agent_client_delivery_mode": target_mode},
                )
            finally:
                auth_conn.close()
            summary = clients_host.delivery_summary(conn, settings, roles, anonymous_readonly=bool(getattr(ctx.config, "anonymous_readonly", getattr(settings, "anonymous_readonly", False))))
            return 200, {"agent_client_delivery_mode": target_mode, "agent_client_delivery": summary}
        except ValueError as exc:
            return 400, {"error": "invalid_delivery_mode", "detail": str(exc)}
        finally:
            conn.close()

    async def admin_server_client_delivery_api(request: Request) -> Response:
        principal = request_principal(request)
        if not is_admin(principal):
            return not_found()
        if not same_origin(request):
            return json_error(403, "forbidden")
        denied = require_recent_auth(principal)
        if denied is not None:
            return denied

        body = await read_json_body(request)
        settings = getattr(request.app.state, "settings", None) or ctx.config
        status_code, resp_data = await asyncio.to_thread(_apply_client_delivery_update, principal, settings, body)
        return JSONResponse(resp_data, status_code=status_code, headers={"Cache-Control": "no-store"})

    async def admin_server_invites_api(request: Request) -> Response:
        principal = request_principal(request)
        if not is_admin(principal):
            return not_found()
        if not same_origin(request):
            return json_error(403, "forbidden")
        denied = require_recent_auth(principal)
        if denied is not None:
            return denied

        body = await read_json_body(request)
        unknown = sorted(set(body) - {"member_invites_enabled"})
        if unknown:
            return JSONResponse(
                {"error": "invalid_request", "detail": f"Unsupported fields: {', '.join(unknown)}"},
                status_code=400,
                headers={"Cache-Control": "no-store"},
            )
        enabled = body.get("member_invites_enabled")
        if not isinstance(enabled, bool):
            return JSONResponse(
                {"error": "invalid_member_invites_enabled", "detail": "member_invites_enabled must be a boolean"},
                status_code=400,
                headers={"Cache-Control": "no-store"},
            )

        def _save_invites_policy() -> dict[str, Any]:
            conn = open_db(ctx)
            try:
                store = accounts_store.AccountStore(conn)
                store.set_member_invites_enabled(enabled, updated_by=principal.account_id)
                auth_conn = open_auth_db(ctx)
                try:
                    auth_db.record_admin_audit(
                        auth_conn,
                        actor_account_id=principal.account_id,
                        action="server.invites.update",
                        detail={"member_invites_enabled": enabled},
                    )
                finally:
                    auth_conn.close()
                return {"member_invites_enabled": store.member_invites_enabled()}
            finally:
                conn.close()

        resp_data = await asyncio.to_thread(_save_invites_policy)
        return JSONResponse(resp_data, status_code=200, headers={"Cache-Control": "no-store"})

    async def admin_server_page_post(request: Request) -> Response:
        principal = request_principal(request)
        if not is_admin(principal):
            return not_found()
        if not same_origin(request):
            return json_error(403, "forbidden")
        denied = require_recent_auth(principal)
        if denied is not None:
            return denied

        form = await request.form()
        payload = {k: str(v) for k, v in form.multi_items()} if len(form.multi_items()) == len(form.keys()) else {"__duplicate__": "1"}
        settings = getattr(request.app.state, "settings", None) or ctx.config
        status_code, resp_data = await asyncio.to_thread(_apply_client_delivery_update, principal, settings, payload)
        if status_code != 200:
            return JSONResponse(resp_data, status_code=status_code, headers={"Cache-Control": "no-store"})
        return RedirectResponse(ADMIN_SERVER_PATH, status_code=303)

    return [
        Route(ADMIN_SERVER_TIER_PATH, admin_server_tier_get, methods=["GET"]),
        Route(ADMIN_SERVER_TIER_PATH, admin_server_tier_put, methods=["PUT"]),
        Route(ADMIN_SERVER_PATH, admin_server_page, methods=["GET"]),
        Route(ADMIN_SERVER_PATH, admin_server_page_post, methods=["POST"]),
        Route(ADMIN_SERVER_API_PATH, admin_server_api, methods=["GET"]),
        Route(ADMIN_SERVER_MODE_PATH, admin_server_mode_api, methods=["POST"]),
        Route(ADMIN_SERVER_CLIENT_DELIVERY_PATH, admin_server_client_delivery_api, methods=["POST"]),
        Route(ADMIN_SERVER_INVITES_PATH, admin_server_invites_api, methods=["POST"]),
        Route(ADMIN_SERVER_SUNSET_PATH, admin_server_sunset_api, methods=["POST"]),
    ]
