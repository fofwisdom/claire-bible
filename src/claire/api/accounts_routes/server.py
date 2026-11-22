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
from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.routing import Route

from ...accounts import readiness
from ...accounts import server_tier
from ...accounts.provider_policy import EFFORT_ORDER, TIER_FEATURES, TIER_PROVIDERS, ServerTier
from ...i18n import t
from ...store import accounts as accounts_store
from ...store import auth_db
from ...store import legacy_hosts
from ...store import usage_ledger
from ..host_router import LegacyHost, LegacyHostPolicy
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
from .console_ui import (
    console_actions,
    console_card,
    console_field,
    console_kv,
    console_notice,
    console_pill,
    console_result,
    console_table,
    console_toggle_row,
)

ADMIN_SERVER_PATH = "/admin/server"
ADMIN_SERVER_API_PATH = "/api/admin/server"
ADMIN_SERVER_MODE_PATH = "/api/admin/server/mode"
ADMIN_SERVER_INVITES_PATH = "/api/admin/server/invites"
ADMIN_SERVER_SUNSET_PATH = "/api/admin/server/legacy-hosts/sunset"
ADMIN_SERVER_TIER_PATH = "/api/admin/server-tier"

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
        status = f'{console_pill(t("console.admin.tier.state.unset"), tone="danger")} {t_code("console.admin.tier.state.unset_hint", none="none")}'
    elif not state["valid"]:
        status = f'{console_pill(t("console.admin.tier.state.invalid"), tone="danger")} {t_code("console.admin.tier.state.invalid_hint", none="none")}'
    else:
        status = console_pill(t("console.admin.tier.state.set"), tone="ok")
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
        return console_field(
            label,
            f'<input type="number" name="{name}" class="pref-input" min="{minimum}" step="1" value="{value}" required>',
            hint_html=escape(hint) if hint else "",
        )

    feat_summary = ", ".join(_feature_label(f) for f in tier.features) if tier.features else t("console.admin.tier.features_none")
    prov_summary = "Google AI Studio (gemini)" if tier.provider == "gemini" else t("console.admin.tier.provider_none")
    summary_kv = console_kv(
        [
            (
                t("console.admin.tier.provider_model"),
                f"{prov_summary}{f' · {tier.model}' if tier.model else ''} ({t('console.admin.tier.max_effort_value', effort=tier.max_effort)})",
            ),
            (t("console.tier.features"), feat_summary),
        ]
    )
    hidden_fields = (
        '<div hidden>'
        + console_field(t("console.settings.keys.provider"), f'<select name="provider" class="pref-select">{provider_options}</select>')
        + console_field(t("console.tier.model"), f'<input type="text" name="model" class="pref-input" value="{escape(tier.model)}" maxlength="128" autocomplete="off">')
        + console_field(t("console.admin.tier.max_effort"), f'<select name="max_effort" class="pref-select">{effort_options}</select>')
        + f'<fieldset><legend>{escape(t("console.tier.features"))}</legend>{feature_boxes}</fieldset>'
        + "</div>"
    )
    tier_form = (
        '<form class="tier-form" data-server-tier-form>'
        + hidden_fields
        + num("monthly_tokens", t("console.admin.tier.monthly_tokens"), tier.monthly_tokens, 0, t("console.admin.tier.monthly_tokens_hint"))
        + num("daily_ingests", t("console.admin.tier.daily_ingests"), tier.daily_ingests, 0)
        + num("storage_mb", t("console.admin.accounts.userdb.quota_storage"), tier.storage_mb, 0)
        + num("max_documents", t("console.admin.tier.max_documents"), tier.max_documents, 0)
        + num("byok_max_concurrency", t("console.admin.tier.byok_concurrency"), tier.byok_max_concurrency, 1)
        + console_actions(f'<button type="submit" class="pref-btn pref-btn-accent">{escape(t("console.admin.tier.submit"))}</button>')
        + console_result(attrs={"data-server-tier-result": True})
        + "</form>"
    )
    reauth_form = (
        '<form class="tier-form" data-server-tier-reauth hidden>'
        + console_field(t("console.admin.tier.reauth"), '<input type="password" name="password" class="pref-input" autocomplete="current-password" required>')
        + console_actions(f'<button type="submit" class="pref-btn pref-btn-accent">{escape(t("console.reauth.submit"))}</button>')
        + console_result(attrs={"data-server-tier-reauth-result": True})
        + "</form>"
    )
    body = (
        f'<p class="hint">{escape(t("console.common.status"))}: {status}</p>'
        + f'<p class="hint">{escape(t("console.admin.tier.hint"))} <a href="/admin/providers"><strong>{escape(t("console.admin.providers.label"))}</strong></a></p>'
        + summary_kv
        + tier_form
        + reauth_form
    )
    return console_card(
        escape(t("console.admin.tier.title")),
        body,
        id="server-tier",
        icon="bolt",
        aria_label=t("console.admin.tier.label"),
    )


def _legacy_host_to_dict(h: LegacyHost) -> dict[str, Any]:
    return {
        "host": h.host,
        "kb_id": h.kb_id,
        "created_at": h.created_at,
        "activated_at": h.activated_at,
        "sunset_at": h.sunset_at,
    }


def _render_invites_policy_section(*, member_invites_enabled: bool) -> str:
    title = t("console.admin.server.invites.title")
    label = t("console.admin.server.invites.label")
    toggle_html = console_toggle_row(
        t("console.admin.server.invites.toggle_title"),
        "admin-member-invites-toggle",
        desc_html=escape(t("console.admin.server.invites.toggle_desc")),
        checked=member_invites_enabled,
        attrs={"aria-label": t("console.admin.server.invites.toggle_title")},
    )
    body = (
        toggle_html
        + console_result(id="server-invites-policy-result")
        + console_actions(f'<a class="pref-btn" href="/account/invites">{escape(t("console.admin.server.invites.manage_link"))}</a>')
    )
    return console_card(
        escape(title),
        body,
        id="server-invites-policy",
        icon="mail",
        hint_html=escape(t("console.admin.server.invites.hint")),
        aria_label=label,
    )


def _render_server_html(
    *,
    mode: str,
    readiness_report: readiness.ReadinessReport,
    network_mode_available: bool,
    policy: LegacyHostPolicy,
    hosts: list[LegacyHost],
    tier_state: dict[str, Any],
    member_invites_enabled: bool = False,
) -> str:
    tier_section = _render_server_tier_section(tier_state)
    invites_policy_section = _render_invites_policy_section(member_invites_enabled=member_invites_enabled)
    mode_text = t("console.admin.server.mode_single") if mode == "single" else t("console.admin.server.mode_network")
    target_mode = "network" if mode == "single" else "single"

    if mode == "single":
        if not network_mode_available:
            mode_notice = console_notice(escape(t("console.admin.server.notice.limited")), tone="danger")
            can_switch = False
        elif not readiness_report.passed:
            failed_ids = [item.id for item in readiness_report.items if not item.passed]
            mode_notice = console_notice(escape(t("console.admin.server.notice.not_ready", items=", ".join(failed_ids))), tone="danger")
            can_switch = False
        else:
            mode_notice = console_notice(escape(t("console.admin.server.notice.ready")), tone="ok")
            can_switch = True

        mode_desc = (
            console_notice(
                f'<strong>{escape(t("console.admin.server.to_network.title"))}</strong>'
                "<ul>"
                f'<li>{t_code("console.admin.server.to_network.home", path="/@{admin_username}/")}</li>'
                f'<li>{t_code("console.admin.server.to_network.redirect", doc="/?doc=…", theme="/themes/{number}/…")}</li>'
                f'<li>{escape(t("console.admin.server.to_network.sunset"))}</li>'
                "</ul>",
                tone="info",
            )
            + f'<label class="check"><input type="checkbox" name="confirm" required> {escape(t("console.admin.server.to_network.confirm"))}</label>'
        )
        btn_class = ' class="pref-btn pref-btn-accent"'
        btn_text = t("console.admin.server.switch_to_network")
    else:
        can_switch = True
        mode_notice = f'<p class="hint">{escape(t("console.admin.server.notice.network"))}</p>'
        mode_desc = (
            console_notice(
                f'<strong>{escape(t("console.admin.server.to_single.title"))}</strong>'
                "<ul>"
                f'<li>{escape(t("console.admin.server.to_single.members"))}</li>'
                f'<li>{escape(t("console.admin.server.to_single.username"))}</li>'
                f'<li>{t_code("console.admin.server.to_single.redirect", namespace="/@{admin}/…", root="/…")}</li>'
                "</ul>",
                tone="danger",
            )
            + f'<label class="check"><input type="checkbox" name="confirm" required> {escape(t("console.admin.server.to_single.confirm"))}</label>'
        )
        btn_class = ' class="pref-btn pref-btn-danger btn-danger"'
        btn_text = t("console.admin.server.switch_to_single")

    check_rows = []
    for item in readiness_report.items:
        status_tag = console_pill(t("console.readiness.passed"), tone="ok") if item.passed else console_pill(t("console.readiness.failed"), tone="danger")
        check_rows.append(f"<tr><td><code>{escape(item.id)}</code></td><td>{escape(item.name)}</td><td>{status_tag}</td><td>{escape(item.detail)}</td></tr>")
    checks_tbody = "".join(check_rows)

    hosts_rows = []
    for h in hosts:
        activated = format_ts(h.activated_at) if h.activated_at is not None else t("console.admin.server.legacy.not_started")
        sunset = format_ts(h.sunset_at) if h.sunset_at is not None else t("console.admin.server.legacy.no_limit")
        hosts_rows.append(f"<tr><td><code>{escape(h.host)}</code></td><td>{h.kb_id}</td><td>{escape(activated)}</td><td>{escape(sunset)}</td></tr>")
    hosts_tbody = "".join(hosts_rows) if hosts_rows else f'<tr><td colspan="4" style="text-align:center;color:var(--muted);">{escape(t("console.admin.server.legacy.empty"))}</td></tr>'

    readiness_summary = console_pill(t("console.admin.server.readiness_all"), tone="ok") if readiness_report.passed else console_pill(t("console.admin.server.readiness_not_passed"), tone="danger")
    mode_kv = console_kv(
        [
            {"label": t("console.admin.server.current_mode"), "value_html": f'<span class="badge">{escape(mode_text)}</span>'},
            {"label": t("console.admin.server.network_availability"), "value_html": f'<strong>{escape(t("console.admin.server.network_available") if network_mode_available else t("console.admin.server.network_limited"))}</strong>'},
            {"label": t("console.admin.server.readiness"), "value_html": readiness_summary},
        ]
    )
    mode_switch_form = (
        '<form class="mode-form" data-mode-switch-form>'
        + f'<input type="hidden" name="target_mode" value="{target_mode}">'
        + mode_desc
        + f'<button type="submit"{btn_class}{"" if can_switch else " disabled"}>{escape(btn_text)}</button>'
        + console_result(attrs={"data-mode-switch-result": True})
        + "</form>"
    )
    mode_reauth_form = (
        '<form class="mode-form" data-mode-switch-reauth hidden>'
        + f'<p class="hint">{escape(t("console.admin.server.reauth_hint"))}</p>'
        + console_field(t("console.settings.account.password"), '<input type="password" name="password" class="pref-input" autocomplete="current-password" required>')
        + f'<button type="submit" class="pref-btn pref-btn-accent">{escape(t("console.admin.server.reauth_submit"))}</button>'
        + console_result(attrs={"data-mode-switch-reauth-result": True})
        + "</form>"
    )
    # Keep exact opening tag of #server-mode for tests checking '<section class="card" aria-label="Server mode" id="server-mode">'.
    mode_section = (
        f'<section class="card" aria-label="{escape(t("console.mode.title"))}" id="server-mode">'
        f'<h2><span data-icon="server"></span> {escape(t("console.mode.title"))}</h2>'
        f"{mode_kv}{mode_notice}{mode_switch_form}{mode_reauth_form}"
        "</section>"
    )
    readiness_section = console_card(
        escape(t("console.admin.server.readiness_title")),
        console_table(
            [
                t("console.readiness.col_item"),
                t("console.readiness.col_name"),
                t("console.common.status"),
                t("console.readiness.col_detail"),
            ],
            checks_tbody,
        ),
        icon="check-circle",
        aria_label=t("console.admin.server.readiness_label"),
    )
    legacy_kv = console_kv(
        [
            (t("console.admin.server.legacy.after_status"), f"HTTP {policy.after_sunset_status}"),
            (t("console.admin.server.legacy.grace"), t("console.admin.server.legacy.days", days=policy.sunset_days)),
        ]
    )
    legacy_table = console_table(
        [
            t("console.admin.server.legacy.col_host"),
            t("console.admin.server.legacy.col_theme"),
            t("console.admin.server.legacy.col_start"),
            t("console.admin.server.legacy.col_sunset"),
        ],
        hosts_tbody,
    )
    legacy_section = console_card(
        escape(t("console.admin.server.legacy.title")),
        legacy_kv + legacy_table,
        icon="clock",
        aria_label=t("console.admin.server.legacy.label"),
    )
    return f"{mode_section}{readiness_section}{legacy_section}{tier_section}{invites_policy_section}"


def routes(ctx: AccountsContext) -> list[Route]:
    async def admin_server_page(request: Request) -> Response:
        principal = request_principal(request)
        if not is_admin(principal):
            return not_found()

        settings = getattr(request.app.state, "settings", None) or ctx.config
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
            finally:
                conn.close()

            body_html = _render_server_html(
                mode=mode,
                readiness_report=report,
                network_mode_available=readiness.NETWORK_MODE_AVAILABLE,
                policy=policy,
                hosts=hosts,
                tier_state=tier_state,
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

        def _fetch_api_data() -> dict[str, Any]:
            conn = open_db(ctx)
            try:
                store = accounts_store.AccountStore(conn)
                mode = store.server_mode()
                report = readiness.check_readiness(conn, settings)
                policy = legacy_hosts.read_legacy_host_policy(conn)
                hosts = legacy_hosts.list_legacy_hosts(conn)
                return {
                    "mode": mode,
                    "readiness": report.to_dict(),
                    "network_mode_available": readiness.NETWORK_MODE_AVAILABLE,
                    "legacy_host_policy": {
                        "after_sunset_status": policy.after_sunset_status,
                        "sunset_days": policy.sunset_days,
                    },
                    "legacy_hosts": [_legacy_host_to_dict(h) for h in hosts],
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

    return [
        Route(ADMIN_SERVER_TIER_PATH, admin_server_tier_get, methods=["GET"]),
        Route(ADMIN_SERVER_TIER_PATH, admin_server_tier_put, methods=["PUT"]),
        Route(ADMIN_SERVER_PATH, admin_server_page, methods=["GET"]),
        Route(ADMIN_SERVER_API_PATH, admin_server_api, methods=["GET"]),
        Route(ADMIN_SERVER_MODE_PATH, admin_server_mode_api, methods=["POST"]),
        Route(ADMIN_SERVER_INVITES_PATH, admin_server_invites_api, methods=["POST"]),
        Route(ADMIN_SERVER_SUNSET_PATH, admin_server_sunset_api, methods=["POST"]),
    ]
