"""인스턴스 모드 설정·전환, 준비 검사, 일몰 시계 관리 API (설계 §4.2, §5.3, §8.6, §17.2, O6, O7, O15).

- ``GET /admin/instance``: 관리자 전용 HTML 페이지 (모드, G1-G9 준비 검사, 일몰 시계 상태)
- ``GET /api/admin/instance``: JSON API (현재 모드, readiness 검사 결과, network_mode_available, legacy_host_policy)
- ``POST /api/admin/instance/mode``: 모드 전환 API (단계 인증 필수)
- ``POST /api/admin/instance/legacy-hosts/sunset``: 일몰 시계 관리 API (단계 인증 필수)
"""

from __future__ import annotations

import asyncio
from typing import Any

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.routing import Route

from ...accounts import readiness
from ...store import accounts as accounts_store
from ...store import legacy_hosts
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
    open_db,
    require_recent_auth,
    same_origin,
)

ADMIN_INSTANCE_PATH = "/admin/instance"
ADMIN_INSTANCE_API_PATH = "/api/admin/instance"
ADMIN_INSTANCE_MODE_PATH = "/api/admin/instance/mode"
ADMIN_INSTANCE_SUNSET_PATH = "/api/admin/instance/legacy-hosts/sunset"


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
) -> str:
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
    </main>
  </body>
</html>"""


def routes(ctx: AccountsContext) -> list[Route]:
    async def admin_instance_page(request: Request) -> Response:
        principal = request_principal(request)
        if not is_admin(principal):
            return not_found()

        settings = getattr(request.app.state, "settings", None) or ctx.config

        def _load_data() -> tuple[str, readiness.ReadinessReport, bool, LegacyHostPolicy, list[LegacyHost]]:
            conn = open_db(ctx)
            try:
                store = accounts_store.AccountStore(conn)
                mode = store.instance_mode()
                report = readiness.check_readiness(conn, settings)
                policy = legacy_hosts.read_legacy_host_policy(conn)
                hosts = legacy_hosts.list_legacy_hosts(conn)
                return mode, report, readiness.NETWORK_MODE_AVAILABLE, policy, hosts
            finally:
                conn.close()

        mode, report, avail, policy, hosts = await asyncio.to_thread(_load_data)
        content = _render_instance_html(
            mode=mode,
            readiness_report=report,
            network_mode_available=avail,
            policy=policy,
            hosts=hosts,
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

    return [
        Route(ADMIN_INSTANCE_PATH, admin_instance_page, methods=["GET"]),
        Route(ADMIN_INSTANCE_API_PATH, admin_instance_api, methods=["GET"]),
        Route(ADMIN_INSTANCE_MODE_PATH, admin_instance_mode_api, methods=["POST"]),
        Route(ADMIN_INSTANCE_SUNSET_PATH, admin_instance_sunset_api, methods=["POST"]),
    ]
