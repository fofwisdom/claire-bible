"""인스턴스 모드 설정·전환, 준비 검사, 일몰 시계 관리 API (설계 §4.2, §5.3, §8.6, §17.2, O6, O7, O15).

- ``GET /admin/instance``: 관리자 전용 HTML 페이지 (모드, G1-G9 준비 검사, 일몰 시계 상태)
- ``GET /api/admin/instance``: JSON API (현재 모드, readiness 검사 결과, network_mode_available, legacy_host_policy)
- ``POST /api/admin/instance/mode``: 모드 전환 API (단계 인증 필수)
- ``POST /api/admin/instance/legacy-hosts/sunset``: 일몰 시계 관리 API (단계 인증 필수)
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
from ...store import accounts as accounts_store
from ...store import auth_db
from ...store import legacy_hosts
from ...store import usage_ledger
from ..host_router import LegacyHost, LegacyHostPolicy
from ..namespace import ADMIN_USERNAME_PUBLISHED_KEY
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
)

ADMIN_INSTANCE_PATH = "/admin/instance"
ADMIN_INSTANCE_API_PATH = "/api/admin/instance"
ADMIN_INSTANCE_MODE_PATH = "/api/admin/instance/mode"
ADMIN_INSTANCE_SUNSET_PATH = "/api/admin/instance/legacy-hosts/sunset"
ADMIN_SERVER_TIER_PATH = "/api/admin/server-tier"

_FEATURE_LABELS = {
    "expand": "확장(expand)",
    "stt": "음성 전사(stt)",
    "vlm": "이미지 해석(vlm)",
    "presentation": "프레젠테이션",
    "synthesize": "종합(synthesize)",
}


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
        status = '<span style="color:#c53030;font-weight:bold;">미설정</span> (저장하면 G5가 통과합니다. <code>none</code>도 유효한 설정입니다)'
    elif not state["valid"]:
        status = '<span style="color:#c53030;font-weight:bold;">저장값 손상</span> (실행 중에는 <code>none</code>으로 동작합니다. 다시 저장하세요)'
    else:
        status = '<span style="color:#2f855a;font-weight:bold;">설정됨</span>'
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
        f'{" checked" if f in tier.features else ""}> {escape(_FEATURE_LABELS.get(f, f))}</label>'
        for f in TIER_FEATURES
    )

    def num(name: str, label: str, value: int, minimum: int, hint: str = "") -> str:
        hint_html = f'<span class="hint">{escape(hint)}</span>' if hint else ""
        return (
            f'<label>{escape(label)}<input type="number" name="{name}" min="{minimum}" step="1" '
            f'value="{value}" required>{hint_html}</label>'
        )

    return f"""
      <section class="card" aria-label="서버 등급" id="server-tier">
        <h2>서버 등급 (§10.3)</h2>
        <p class="hint">상태: {status}</p>
        <p class="hint">자기 API 키가 없는 멤버가 쓰는 프로바이더와 한도입니다. 테마·문서·저장 용량·일 적재 한도는 자기 키가 있는 멤버에게도 적용합니다. 사용량은 UTC 기준으로 셉니다. 관리자는 한도를 받지 않습니다.</p>
        <form class="tier-form" data-server-tier-form>
          <label>프로바이더<select name="provider">{provider_options}</select></label>
          <label>모델<input type="text" name="model" value="{escape(tier.model)}" maxlength="128" autocomplete="off"><span class="hint">provider가 none이면 무시합니다</span></label>
          {num("monthly_tokens", "월 토큰 상한 (UTC 달)", tier.monthly_tokens, 0, "서버 등급 사용 멤버에게만 적용")}
          {num("daily_ingests", "일 적재 건수 (UTC 날)", tier.daily_ingests, 0)}
          <label>최대 effort<select name="max_effort">{effort_options}</select></label>
          {num("storage_mb", "저장 용량 (MB)", tier.storage_mb, 0)}
          {num("max_themes", "최대 테마 수", tier.max_themes, 1)}
          {num("max_documents", "최대 문서 수", tier.max_documents, 0)}
          {num("byok_max_concurrency", "자기 키 동시 작업 수", tier.byok_max_concurrency, 1)}
          <fieldset><legend>허용 기능</legend>{feature_boxes}</fieldset>
          <button type="submit">서버 등급 저장</button>
          <p class="result" role="status" aria-live="polite" data-server-tier-result></p>
        </form>
        <form class="tier-form" data-server-tier-reauth hidden>
          <label>저장하려면 비밀번호로 본인 확인을 하세요<input type="password" name="password" autocomplete="current-password" required></label>
          <button type="submit">본인 확인</button>
          <p class="result" role="status" aria-live="polite" data-server-tier-reauth-result></p>
        </form>
      </section>"""


def _legacy_host_to_dict(h: LegacyHost) -> dict[str, Any]:
    return {
        "host": h.host,
        "theme_id": h.theme_id,
        "created_at": h.created_at,
        "activated_at": h.activated_at,
        "sunset_at": h.sunset_at,
    }


def _render_instance_html(
    *,
    mode: str,
    readiness_report: readiness.ReadinessReport,
    network_mode_available: bool,
    policy: LegacyHostPolicy,
    hosts: list[LegacyHost],
    tier_state: dict[str, Any],
) -> str:
    tier_section = _render_server_tier_section(tier_state)
    mode_text = "싱글 모드 (Single Mode)" if mode == "single" else "네트워크 모드 (Network Mode)"

    check_rows = []
    for item in readiness_report.items:
        status_tag = '<span style="color:#2f855a;font-weight:bold;">통과</span>' if item.passed else '<span style="color:#c53030;font-weight:bold;">실패</span>'
        check_rows.append(f"<tr><td><code>{escape(item.id)}</code></td><td>{escape(item.name)}</td><td>{status_tag}</td><td>{escape(item.detail)}</td></tr>")
    checks_tbody = "".join(check_rows)

    hosts_rows = []
    for h in hosts:
        activated = format_ts(h.activated_at) if h.activated_at is not None else "(미시작)"
        sunset = format_ts(h.sunset_at) if h.sunset_at is not None else "(무기한)"
        hosts_rows.append(f"<tr><td><code>{escape(h.host)}</code></td><td>{h.theme_id}</td><td>{activated}</td><td>{sunset}</td></tr>")
    hosts_tbody = "".join(hosts_rows) if hosts_rows else '<tr><td colspan="4" style="text-align:center;color:var(--muted);">(등록된 레거시 호스트가 없습니다)</td></tr>'

    return f"""<!doctype html>
<html lang="ko">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <meta name="robots" content="noindex, nofollow">
    <title>인스턴스 설정 · Claire</title>
    <style>
      :root {{ color-scheme: light dark; --bg: #f7f7f5; --fg: #1d1d1b; --muted: #5d5d58; --card: #ffffff; --line: #d9d9d4; --accent: #2f5d8a; --error: #a3261d; }}
      @media (prefers-color-scheme: dark) {{ :root {{ --bg: #161615; --fg: #ececea; --muted: #a5a5a0; --card: #20201e; --line: #3a3a37; --accent: #8db6e0; --error: #f08a80; }} }}
      * {{ box-sizing: border-box; }}
      body {{ margin: 0; background: var(--bg); color: var(--fg); font: 16px/1.6 system-ui, -apple-system, "Noto Sans KR", sans-serif; }}
      main {{ max-width: 48rem; margin: 0 auto; padding: 2rem 1rem 3rem; }}
      h1 {{ font-size: 1.5rem; margin: 0 0 1rem; }}
      h2 {{ font-size: 1.15rem; margin: 0 0 .75rem; }}
      .card {{ background: var(--card); border: 1px solid var(--line); border-radius: 8px; padding: 1.25rem; margin: 0 0 1.25rem; }}
      table {{ width: 100%; border-collapse: collapse; margin-top: .5rem; font-size: .9rem; }}
      th, td {{ border-bottom: 1px solid var(--line); padding: .5rem .625rem; text-align: left; vertical-align: top; }}
      th {{ color: var(--muted); font-weight: 600; }}
      nav {{ display: flex; gap: 1rem; align-items: center; margin: 0 0 1.5rem; font-size: .9rem; }}
      nav a {{ color: var(--accent); }}
      nav form {{ margin: 0 0 0 auto; }}
      button.link {{ background: none; color: var(--accent); padding: 0; margin: 0; border: 0; cursor: pointer; text-decoration: underline; font: inherit; }}
      .badge {{ display: inline-block; padding: .2rem .5rem; border-radius: 4px; font-weight: 600; font-size: .85rem; background: var(--line); }}
      .hint {{ color: var(--muted); font-size: .875rem; margin-top: .25rem; }}
      dl {{ display: grid; grid-template-columns: max-content 1fr; gap: .25rem 1rem; margin: 0; }}
      dt {{ color: var(--muted); }}
      dd {{ margin: 0; }}
      .tier-form {{ display: grid; gap: .75rem; margin-top: .75rem; }}
      .tier-form label {{ display: grid; gap: .25rem; font-size: .9rem; }}
      .tier-form label.check {{ display: inline-flex; gap: .4rem; align-items: center; margin-right: 1rem; }}
      .tier-form input[type=text], .tier-form input[type=number], .tier-form input[type=password], .tier-form select {{ font: inherit; padding: .4rem .5rem; border: 1px solid var(--line); border-radius: 6px; background: var(--bg); color: var(--fg); max-width: 100%; }}
      .tier-form fieldset {{ border: 1px solid var(--line); border-radius: 6px; padding: .5rem .75rem; }}
      .tier-form button {{ justify-self: start; font: inherit; padding: .45rem .9rem; border-radius: 6px; border: 1px solid var(--accent); background: var(--accent); color: var(--card); cursor: pointer; }}
      .result:empty {{ display: none; }}
      .result[data-state=error] {{ color: var(--error); }}
    </style>
  </head>
  <body>
    <main>
      <nav aria-label="네비게이션">
        <a href="/">홈</a>
        <a href="/admin/accounts">계정 관리</a>
        <a href="/me">내 계정</a>
        <form method="post" action="/logout"><button type="submit" class="link">로그아웃</button></form>
      </nav>
      <h1>인스턴스 설정</h1>

      <section class="card" aria-label="현재 모드">
        <h2>현재 모드</h2>
        <p><span class="badge">{escape(mode_text)}</span></p>
        <p class="hint">네트워크 모드 가용 상태: <strong>{"사용 가능" if network_mode_available else "릴리스 제한 (NETWORK_MODE_AVAILABLE=False)"}</strong></p>
      </section>

      <section class="card" aria-label="준비 검사 G1–G9">
        <h2>네트워크 모드 준비 검사 (G1–G9)</h2>
        <table>
          <thead>
            <tr><th>항목</th><th>이름</th><th>상태</th><th>세부 내용</th></tr>
          </thead>
          <tbody>
            {checks_tbody}
          </tbody>
        </table>
      </section>

      <section class="card" aria-label="레거시 호스트 일몰 시계">
        <h2>레거시 호스트 일몰 시계 (§5.3)</h2>
        <dl>
          <dt>일몰 후 응답 상태</dt><dd>HTTP {policy.after_sunset_status}</dd>
          <dt>기본 일몰 유예 기간</dt><dd>{policy.sunset_days}일</dd>
        </dl>
        <table>
          <thead>
            <tr><th>호스트</th><th>테마 ID</th><th>일몰 기산</th><th>일몰 예정 시각</th></tr>
          </thead>
          <tbody>
            {hosts_tbody}
          </tbody>
        </table>
      </section>
{tier_section}
    </main>
    <script src="/static/js/server_tier.js" defer></script>
  </body>
</html>"""


def routes(ctx: AccountsContext) -> list[Route]:
    async def admin_instance_page(request: Request) -> Response:
        principal = request_principal(request)
        if not is_admin(principal):
            return not_found()

        settings = getattr(request.app.state, "settings", None) or ctx.config

        def _load_data() -> tuple[str, readiness.ReadinessReport, bool, LegacyHostPolicy, list[LegacyHost], dict[str, Any]]:
            conn = open_db(ctx)
            try:
                store = accounts_store.AccountStore(conn)
                mode = store.instance_mode()
                report = readiness.check_readiness(conn, settings)
                policy = legacy_hosts.read_legacy_host_policy(conn)
                hosts = legacy_hosts.list_legacy_hosts(conn)
                tier_state = _server_tier_state(conn)
                return mode, report, readiness.NETWORK_MODE_AVAILABLE, policy, hosts, tier_state
            finally:
                conn.close()

        mode, report, avail, policy, hosts, tier_state = await asyncio.to_thread(_load_data)
        content = _render_instance_html(
            mode=mode,
            readiness_report=report,
            network_mode_available=avail,
            policy=policy,
            hosts=hosts,
            tier_state=tier_state,
        )
        return HTMLResponse(content, headers={"Cache-Control": "no-store", "Referrer-Policy": "same-origin"})

    async def admin_instance_api(request: Request) -> Response:
        principal = request_principal(request)
        if not is_admin(principal):
            return not_found()

        settings = getattr(request.app.state, "settings", None) or ctx.config

        def _fetch_api_data() -> dict[str, Any]:
            conn = open_db(ctx)
            try:
                store = accounts_store.AccountStore(conn)
                mode = store.instance_mode()
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
                }
            finally:
                conn.close()

        data = await asyncio.to_thread(_fetch_api_data)
        return JSONResponse(data)

    async def admin_instance_mode_api(request: Request) -> Response:
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
        if target_mode not in accounts_store.INSTANCE_MODES:
            return json_error(400, "invalid_mode", detail=f"mode must be one of {sorted(accounts_store.INSTANCE_MODES)}")

        settings = getattr(request.app.state, "settings", None) or ctx.config

        def _execute_mode_change() -> tuple[int, dict[str, Any]]:
            conn = open_db(ctx)
            try:
                store = accounts_store.AccountStore(conn)
                current_mode = store.instance_mode()
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
                            "detail": "Instance readiness checks did not pass",
                            "failures": [item.to_dict() for item in report.items if not item.passed],
                        }
                    multi_theme = getattr(settings, "multi_theme", True)
                    if not multi_theme:
                        return 400, {
                            "error": "multi_theme_required",
                            "detail": "CLAIRE_MULTI_THEME=1 is required for network mode",
                        }

                    store.set_setting("mode", "network", updated_by=principal.account_id)
                    store.set_setting(ADMIN_USERNAME_PUBLISHED_KEY, "1", updated_by=principal.account_id)
                    legacy_hosts.activate_sunset_clock(conn)
                    return 200, {"mode": "network", "changed": True}

                # network -> single:
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
                # O15: 네트워크 복귀 시 admin_username_published='1' 기록
                store.set_setting(ADMIN_USERNAME_PUBLISHED_KEY, "1", updated_by=principal.account_id)
                return 200, {"mode": "single", "changed": True, "admin_username_published": True}
            finally:
                conn.close()

        status_code, resp_data = await asyncio.to_thread(_execute_mode_change)
        return JSONResponse(resp_data, status_code=status_code)

    async def admin_instance_sunset_api(request: Request) -> Response:
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

    return [
        Route(ADMIN_SERVER_TIER_PATH, admin_server_tier_get, methods=["GET"]),
        Route(ADMIN_SERVER_TIER_PATH, admin_server_tier_put, methods=["PUT"]),
        Route(ADMIN_INSTANCE_PATH, admin_instance_page, methods=["GET"]),
        Route(ADMIN_INSTANCE_API_PATH, admin_instance_api, methods=["GET"]),
        Route(ADMIN_INSTANCE_MODE_PATH, admin_instance_mode_api, methods=["POST"]),
        Route(ADMIN_INSTANCE_SUNSET_PATH, admin_instance_sunset_api, methods=["POST"]),
    ]
