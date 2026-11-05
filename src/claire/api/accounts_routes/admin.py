"""``/admin/accounts`` 싱글 모드 화면과 계정 관리 및 퇴역 USERNAME API (설계 §8.6, §8.9).

관리자(``instance_role == "admin"``, ``owner`` 범위)가 아니면 404다.
수정(PATCH) 및 정보 초기화(POST /api/admin/usernames/release)는 관리자 단계 인증(최근 15분 내 authenticated_at)이 필수다.
다른 계정의 콘텐츠를 읽는 API는 두지 않는다(K5, D5).
"""

from __future__ import annotations

import asyncio
import secrets
import sqlite3
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from ...store import accounts as accounts_store
from ...store import auth_db
from ...store import provider_keys
from ..principal import Principal, request_principal
from .common import (
    AccountsContext,
    escape,
    format_ts,
    json_error,
    not_found,
    open_auth_db,
    open_db,
    prototype_badge,
    prototype_note,
    render_template,
    require_recent_auth,
)

ADMIN_ACCOUNTS_PATH = "/admin/accounts"
ADMIN_ACCOUNTS_API_PATH = "/api/admin/accounts"
ADMIN_USERNAMES_RETIRED_PATH = "/api/admin/usernames/retired"
ADMIN_USERNAMES_RELEASE_PATH = "/api/admin/usernames/release"

STATUS_TEXT = {
    "setup_required": "설정 필요",
    "active": "활성",
    "suspended": "정지",
    "deleting": "삭제 예약",
    "purged": "삭제됨",
}
ROLE_TEXT = {"admin": "관리자", "member": "멤버"}


def is_admin(principal: Principal) -> bool:
    return principal.instance_role == "admin" and principal.scope == "owner" and principal.account_id is not None


def _theme_counts(conn: sqlite3.Connection) -> dict[int, int]:
    rows = conn.execute(
        "SELECT account_id, COUNT(*) FROM themes WHERE account_id IS NOT NULL GROUP BY account_id"
    ).fetchall()
    return {int(r[0]): int(r[1]) for r in rows}


def account_summary(
    account: accounts_store.Account, theme_count: int, *, has_provider_key: bool | None = None
) -> dict[str, Any]:
    """§8.6 목록 항목 중 P1에서 있는 값. 번호는 JS 정밀도 손실을 막기 위해 문자열이다(S13).

    ``has_provider_key``는 멤버 항목에만 싣는 등록 여부다(§11.2). 키의 fingerprint·상태는 싣지 않는다.
    """
    res = {
        "public_id": str(account.public_id),
        "username": account.username,
        "status": account.status,
        "role": account.role,
        "created_at": account.created_at,
        "activated_at": account.activated_at,
        "theme_count": theme_count,
    }
    if account.delete_after is not None:
        res["delete_after"] = account.delete_after
    if has_provider_key is not None:
        res["has_provider_key"] = has_provider_key
    return res


def list_account_summaries(ctx: AccountsContext) -> list[dict[str, Any]]:
    conn = open_db(ctx)
    auth = open_auth_db(ctx)
    try:
        counts = _theme_counts(conn)
        return [
            account_summary(
                a,
                counts.get(a.id, 0),
                # 관리자는 providers.json을 쓰므로 멤버 항목에만 키 등록 여부를 싣는다(§11.2).
                has_provider_key=provider_keys.has_key(auth, account_id=a.id) if a.role == "member" else None,
            )
            for a in accounts_store.AccountStore(conn).list_accounts()
        ]
    finally:
        auth.close()
        conn.close()


def _admin_card(item: dict[str, Any]) -> str:
    rows = [
        ("번호", item["public_id"]),
        ("USERNAME", item["username"] if item["username"] is not None else "(설정 전)"),
        ("상태", STATUS_TEXT.get(item["status"], item["status"])),
        ("역할", ROLE_TEXT.get(item["role"], item["role"])),
        ("생성", format_ts(item["created_at"])),
        ("활성", format_ts(item["activated_at"])),
        ("테마 수", item["theme_count"]),
    ]
    body = "".join(f"<dt>{escape(k)}</dt><dd>{escape(v)}</dd>" for k, v in rows)
    return f'<section class="card" aria-label="관리자 계정"><h2>관리자 계정</h2><dl>{body}</dl></section>'


def _render_admin_accounts_html(
    *,
    mode: str,
    items: list[dict[str, Any]],
    retired: list[dict[str, Any]],
    tier_dict: dict[str, Any],
) -> str:
    admin_items = [i for i in items if i["role"] == "admin"]
    member_items = [i for i in items if i["role"] != "admin"]
    cards = "".join(_admin_card(i) for i in admin_items)

    member_trs: list[str] = []
    for m in member_items:
        pub_id = str(m["public_id"])
        uname = m.get("username") or "(미설정)"
        st = m.get("status", "")
        st_label = STATUS_TEXT.get(st, st)
        key_tag = "등록됨" if m.get("has_provider_key") else "없음"
        member_trs.append(
            f'<tr data-account-public-id="{escape(pub_id)}" data-account-status="{escape(st)}">'
            f'<td><code class="pref-mono">{escape(pub_id)}</code></td>'
            f"<td><strong>{escape(uname)}</strong></td>"
            f'<td><span class="badge">{escape(st_label)}</span></td>'
            f'<td>{int(m.get("theme_count", 0))}</td>'
            f"<td>{escape(key_tag)}</td>"
            f'<td data-member-actions="{escape(pub_id)}">'
            f'<button type="button" class="pref-btn" data-account-action="suspend" data-public-id="{escape(pub_id)}">정지</button> '
            f'<button type="button" class="pref-btn" data-account-action="resume" data-public-id="{escape(pub_id)}">재개</button> '
            f'<button type="button" class="pref-btn" data-account-action="schedule_delete" data-public-id="{escape(pub_id)}">삭제 예약</button> '
            f'<button type="button" class="pref-btn" data-account-action="cancel_delete" data-public-id="{escape(pub_id)}">예약 취소</button> '
            f'<button type="button" class="pref-btn pref-btn-danger" data-account-action="purge" data-public-id="{escape(pub_id)}">즉시 소각</button> '
            f'<button type="button" class="pref-btn" data-account-action="reset_password_link" data-public-id="{escape(pub_id)}">비밀번호 재설정 링크</button>'
            f"</td>"
            f"</tr>"
        )
    members_tbody = (
        "".join(member_trs)
        if member_trs
        else '<tr><td colspan="6" style="color:var(--muted);text-align:center;">등록된 일반 멤버 계정이 없습니다.</td></tr>'
    )

    if mode == "network":
        invites_section = (
            '<section class="card" id="admin-invites-section" aria-label="가입 초대 관리">'
            '<h2>✉️ 가입 초대 코드 관리 (<code>/api/invites</code>)</h2>'
            '<p class="hint">네트워크 모드에서 신규 멤버 가입을 위한 일회용·다회용 초대 코드를 발급하고 비활성화합니다.</p>'
            '<form id="admin-invite-create-form" data-invite-create-form style="display:flex;gap:8px;align-items:flex-end;flex-wrap:wrap">'
            '<label style="display:flex;flex-direction:column;gap:4px;font-size:12px;font-weight:600">최대 사용 횟수 (1~5)'
            '<input type="number" name="max_uses" class="pref-input" min="1" max="5" value="1" style="width:90px" required/>'
            "</label>"
            '<label style="display:flex;flex-direction:column;gap:4px;font-size:12px;font-weight:600">예약 USERNAME (선택)'
            '<input type="text" name="reserved_username" class="pref-input" placeholder="예: alice" style="width:140px"/>'
            "</label>"
            '<label style="display:flex;flex-direction:column;gap:4px;font-size:12px;font-weight:600">메모 (선택)'
            '<input type="text" name="note" class="pref-input" placeholder="초대 대상 메모" style="width:180px"/>'
            "</label>"
            '<button type="submit" class="pref-btn pref-btn-accent">+ 초대 코드 발급</button>'
            "</form>"
            '<p class="result" id="admin-invite-result" data-invite-result role="status" aria-live="polite"></p>'
            '<div class="pref-table-wrap">'
            '<table class="pref-table">'
            '<thead><tr><th>번호</th><th>코드 · 링크</th><th>상태</th><th>사용량</th><th>예약 이름</th><th>메모</th><th>관리</th></tr></thead>'
            '<tbody id="admin-invites-tbody"><tr><td colspan="7" style="color:var(--muted);text-align:center;">초대 목록을 불러오는 중입니다...</td></tr></tbody>'
            "</table>"
            "</div>"
            "</section>"
        )
    else:
        invites_section = (
            '<section class="card" id="admin-invites-section" aria-label="가입 초대 관리">'
            '<h2>✉️ 가입 초대 코드 관리</h2>'
            '<div class="notice">싱글 모드에서는 가입 초대 경로(<code>/api/invites</code>)가 비활성화(404)되어 있습니다. 네트워크 모드로 전환한 뒤 초대를 발급할 수 있습니다.</div>'
            "</section>"
        )

    retired_trs: list[str] = []
    for r in retired:
        uname = str(r.get("username", ""))
        ret_at = format_ts(r.get("retired_at"))
        reason = str(r.get("reason") or "-")
        retired_trs.append(
            f'<tr data-retired-username="{escape(uname)}">'
            f'<td><code class="pref-mono">{escape(uname)}</code></td>'
            f"<td>{escape(ret_at)}</td>"
            f"<td>{escape(reason)}</td>"
            f"<td>"
            f'<button type="button" class="pref-btn" data-retired-preview="{escape(uname)}">초기화 미리보기</button> '
            f'<button type="button" class="pref-btn pref-btn-danger" data-retired-release="{escape(uname)}">퇴역 해제 실행</button>'
            f"</td>"
            f"</tr>"
        )
    retired_tbody = (
        "".join(retired_trs)
        if retired_trs
        else '<tr><td colspan="4" style="color:var(--muted);text-align:center;">퇴역 처리된 USERNAME이 없습니다.</td></tr>'
    )

    storage_mb = int(tier_dict.get("storage_mb", 0))
    max_docs = int(tier_dict.get("max_documents", 0))
    max_themes = int(tier_dict.get("max_themes", 1))
    monthly_tokens = int(tier_dict.get("monthly_tokens", 0))
    daily_ingests = int(tier_dict.get("daily_ingests", 0))
    active_count = sum(1 for i in items if i.get("status") == "active")
    suspended_count = sum(1 for i in items if i.get("status") in {"suspended", "deleting"})
    total_themes_count = sum(int(i.get("theme_count", 0)) for i in items)

    return f"""
      {cards}
      <section class="card" id="admin-members-section" aria-label="멤버 계정 목록 및 수명주기">
        <h2>👥 멤버 계정 디렉터리 및 수명주기 관리 (<code>/api/admin/accounts</code>)</h2>
        <p class="hint">계정 상태 전환(정지·재개·삭제 예약·즉시 소각) 및 비밀번호 재설정 링크 발급은 관리자 단계 인증(15분)을 거칩니다. 멤버의 비공개 콘텐츠는 조회하지 않습니다(D5, K5).</p>
        <p class="result" id="admin-accounts-result" data-accounts-result role="status" aria-live="polite"></p>
        <div class="pref-table-wrap">
          <table class="pref-table">
            <thead>
              <tr><th>번호 (public_id)</th><th>USERNAME</th><th>상태</th><th>테마 수</th><th>BYOK 키</th><th>수명주기 작업</th></tr>
            </thead>
            <tbody id="admin-accounts-tbody">{members_tbody}</tbody>
          </table>
        </div>
      </section>
      {invites_section}
      <section class="card" id="admin-retired-usernames-section" aria-label="퇴역 USERNAME 관리">
        <h2>♻️ 퇴역 USERNAME 조회 및 해제 (<code>/api/admin/usernames/*</code>)</h2>
        <p class="hint">삭제된 계정의 USERNAME은 사칭 방지를 위해 퇴역 목록에 잠깁니다. 미리보기 확인 후 단계 인증을 거쳐 잠금을 해제할 수 있습니다.</p>
        <form id="admin-retired-release-form" data-retired-release-form style="display:flex;gap:8px;align-items:flex-end;flex-wrap:wrap">
          <label style="display:flex;flex-direction:column;gap:4px;font-size:12px;font-weight:600">대상 USERNAME
            <input type="text" name="username" id="admin-retired-username-input" class="pref-input" placeholder="퇴역 username 입력" style="width:180px" required/>
          </label>
          <button type="button" class="pref-btn" id="admin-retired-preview-btn">영향 미리보기 (apply=false)</button>
          <button type="submit" class="pref-btn pref-btn-danger" id="admin-retired-apply-btn">퇴역 해제 실행 (apply=true)</button>
        </form>
        <p class="result" id="admin-retired-result" data-retired-result role="status" aria-live="polite"></p>
        <div class="pref-table-wrap">
          <table class="pref-table">
            <thead>
              <tr><th>퇴역 USERNAME</th><th>퇴역 시각</th><th>사유</th><th>관리</th></tr>
            </thead>
            <tbody id="admin-retired-tbody">{retired_tbody}</tbody>
          </table>
        </div>
      </section>
      <section class="card" id="pref-userdb-admin" data-userdb-view="admin" aria-label="사용자별 워크스페이스 운영자 보기">
        <h2>🗄️ 사용자별 워크스페이스 · 디렉터리 격리 구조 (운영자 보기)</h2>
        <p class="hint">계정별 테마 레지스트리와 데이터베이스 격리 구조 및 기본 할당량 요약입니다. 세부 할당량 수정은 <a href="/admin/instance">🖥️ 인스턴스 모드 · 서버 등급</a>에서 수행합니다.</p>
        <div class="pref-stat-row" id="pref-userdb-stats">
          <div class="pref-stat"><b>{active_count}</b><span>활성 계정</span></div>
          <div class="pref-stat"><b>{suspended_count}</b><span>정지 · 삭제 예약</span></div>
          <div class="pref-stat"><b>{len(retired)}</b><span>퇴역 USERNAME</span></div>
          <div class="pref-stat"><b>{total_themes_count}</b><span>전체 테마 슬롯 합계</span></div>
        </div>
        <div class="pref-quota-grid">
          <label>저장 용량 (MB)<input id="pref-quota-storage" type="number" class="pref-input" value="{storage_mb}" readonly/></label>
          <label>문서 수<input id="pref-quota-docs" type="number" class="pref-input" value="{max_docs}" readonly/></label>
          <label>테마 수<input id="pref-quota-themes" type="number" class="pref-input" value="{max_themes}" readonly/></label>
          <label>월 LLM 토큰<input id="pref-quota-llm" type="number" class="pref-input" value="{monthly_tokens}" readonly/></label>
          <label>일 적재 건수<input id="pref-quota-feeds" type="number" class="pref-input" value="{daily_ingests}" readonly/></label>
          <label class="pref-quota-switch"><span>멤버 BYOK 외부 키 허용 {prototype_badge("admin-accounts-byok-switch", "prototype")}</span><input id="pref-quota-byo" type="checkbox" switch checked disabled/></label>
        </div>
        {prototype_note("admin-accounts-byok-switch", "'멤버 BYOK 외부 키 허용' 스위치는 항상 켜짐으로 고정된 표시입니다. 서버 등급에 이 설정이 없으며 서버 설정을 읽거나 바꾸지 않습니다. 위 한도 값은 실제 서버 등급에서 읽은 값입니다.")}
        <div class="pref-table-wrap">
          <table class="pref-table">
            <caption style="caption-side:top;text-align:left;padding:0 0 6px">{prototype_badge("admin-accounts-isolation-table", "static")} <span class="proto-note-inline">고정 문구로 그린 설명표이며 실제 디렉터리·DB 상태를 읽어 오지 않습니다.</span></caption>
            <thead><tr><th>구분</th><th>격리 경로</th><th>테마 · 콘텐츠 접근</th><th>할당량 적용</th></tr></thead>
            <tbody id="pref-userdb-tbody">
              <tr><td><strong>관리자 워크스페이스</strong></td><td><code class="pref-mono">data/claire.db · themes/</code></td><td>관리자 전용 소유</td><td>제한 없음 (providers.json 직접 사용)</td></tr>
              <tr><td><strong>일반 멤버 계정</strong></td><td><code class="pref-mono">account_id 귀속 테마 격리</code></td><td>소유자만 열람 (운영자 내용 열람 불가 · D5/K5)</td><td>서버 등급(ServerTier) 및 BYOK 정책 적용</td></tr>
            </tbody>
          </table>
        </div>
      </section>"""


def routes(ctx: AccountsContext) -> list[Route]:
    async def admin_accounts_page(request: Request) -> Response:
        principal = request_principal(request)
        if not is_admin(principal):
            return not_found()
        raw_theme = request.query_params.get("theme") if "theme" in request.query_params else None

        def _render() -> Response:
            from ...accounts import server_tier
            from .console import ADMIN_SPEC_BY_SLUG, _render_console_section

            items = list_account_summaries(ctx)
            conn = open_db(ctx)
            try:
                store = accounts_store.AccountStore(conn)
                mode = store.instance_mode()
                retired = store.list_retired_usernames()
                tier_dict = server_tier.load_server_tier(conn).to_dict()
            finally:
                conn.close()

            body_html = _render_admin_accounts_html(
                mode=mode,
                items=items,
                retired=retired,
                tier_dict=tier_dict,
            )
            return _render_console_section(
                ctx,
                spec=ADMIN_SPEC_BY_SLUG["accounts"],
                principal=principal,
                raw_theme=raw_theme,
                custom_body_html=body_html,
            )

        return await asyncio.to_thread(_render)

    async def admin_accounts_api(request: Request) -> Response:
        if not is_admin(request_principal(request)):
            return not_found()
        items = await asyncio.to_thread(list_account_summaries, ctx)
        return JSONResponse({"accounts": items})

    async def admin_accounts_patch(request: Request) -> Response:
        principal = request_principal(request)
        if not is_admin(principal):
            return not_found()
        auth_denied = require_recent_auth(principal)
        if auth_denied is not None:
            return auth_denied

        try:
            body = await request.json()
        except Exception:
            return json_error(400, "invalid_json")

        if not isinstance(body, dict):
            return json_error(400, "invalid_request")

        target_id = body.get("account_id")
        username = body.get("username")
        public_id = body.get("public_id")
        action = str(body.get("action") or "").lower().strip()
        status = str(body.get("status") or "").lower().strip()
        mode = str(body.get("mode") or "").lower().strip()
        immediate = bool(body.get("immediate", False))
        cancel = bool(body.get("cancel", False))

        conn = open_db(ctx)
        auth_conn = open_auth_db(ctx)
        try:
            store = accounts_store.AccountStore(conn)
            target = None
            if target_id is not None:
                try:
                    target = store.get(int(target_id))
                except (accounts_store.AccountNotFound, ValueError, TypeError):
                    return json_error(404, "account_not_found")
            elif username is not None:
                target = store.find_by_username(str(username).strip())
            elif public_id is not None:
                try:
                    row = conn.execute("SELECT id FROM accounts WHERE public_id=?", (int(public_id),)).fetchone()
                    if row is not None:
                        target = store.get(int(row[0]))
                except Exception:
                    pass

            if target is None:
                return json_error(404, "account_not_found")

            if target.role == "admin" and (action or status):
                return json_error(400, "cannot_modify_admin")

            if action == "suspend" or status == "suspended":
                try:
                    updated = store.suspend(target.id)
                except accounts_store.AccountStateError as exc:
                    return json_error(400, "invalid_state_transition", detail=str(exc))
                auth_db.record_admin_audit(
                    auth_conn,
                    actor_account_id=principal.account_id,
                    action="account.suspend",
                    target_account_id=target.id,
                )
                counts = _theme_counts(conn)
                return JSONResponse({"ok": True, "account": account_summary(updated, counts.get(updated.id, 0))})

            if action == "resume" or (status == "active" and target.status == "suspended"):
                try:
                    updated = store.resume(target.id)
                except accounts_store.AccountStateError as exc:
                    return json_error(400, "invalid_state_transition", detail=str(exc))
                auth_db.record_admin_audit(
                    auth_conn,
                    actor_account_id=principal.account_id,
                    action="account.resume",
                    target_account_id=target.id,
                )
                counts = _theme_counts(conn)
                return JSONResponse({"ok": True, "account": account_summary(updated, counts.get(updated.id, 0))})

            if action in ("delete", "schedule_delete", "cancel_delete", "purge") or status in ("deleting", "purged"):
                if action == "cancel_delete" or cancel or mode == "cancel" or (action == "delete" and status == "active"):
                    try:
                        updated = store.cancel_delete(target.id)
                    except accounts_store.AccountStateError as exc:
                        return json_error(400, "invalid_state_transition", detail=str(exc))
                    auth_db.record_admin_audit(
                        auth_conn,
                        actor_account_id=principal.account_id,
                        action="account.cancel_delete",
                        target_account_id=target.id,
                    )
                    counts = _theme_counts(conn)
                    return JSONResponse({"ok": True, "account": account_summary(updated, counts.get(updated.id, 0))})

                if action == "purge" or mode in ("immediate", "purge") or immediate or status == "purged":
                    data_dir = getattr(ctx.config, "data_dir", None)
                    try:
                        updated = store.purge(target.id, data_dir=data_dir)
                    except accounts_store.AccountStateError as exc:
                        return json_error(400, "invalid_state_transition", detail=str(exc))
                    auth_db.record_admin_audit(
                        auth_conn,
                        actor_account_id=principal.account_id,
                        action="account.purge",
                        target_account_id=target.id,
                    )
                    return JSONResponse({"ok": True, "account": account_summary(updated, 0)})

                delete_after = body.get("delete_after")
                try:
                    updated = store.schedule_delete(target.id, delete_after=delete_after)
                except accounts_store.AccountStateError as exc:
                    return json_error(400, "invalid_state_transition", detail=str(exc))
                auth_db.record_admin_audit(
                    auth_conn,
                    actor_account_id=principal.account_id,
                    action="account.schedule_delete",
                    target_account_id=target.id,
                )
                counts = _theme_counts(conn)
                return JSONResponse({"ok": True, "account": account_summary(updated, counts.get(updated.id, 0))})

            if action in ("reset_password", "reset_password_link"):
                reset_token = secrets.token_urlsafe(32)
                reset_link = f"/reset-password?token={reset_token}"
                auth_db.record_admin_audit(
                    auth_conn,
                    actor_account_id=principal.account_id,
                    action="account.reset_password_link",
                    target_account_id=target.id,
                )
                return JSONResponse({
                    "ok": True,
                    "account_id": target.id,
                    "reset_token": reset_token,
                    "reset_link": reset_link,
                })

            return json_error(400, "unknown_action")
        finally:
            auth_conn.close()
            conn.close()

    async def admin_retired_usernames_api(request: Request) -> Response:
        principal = request_principal(request)
        if not is_admin(principal):
            return not_found()
        conn = open_db(ctx)
        try:
            store = accounts_store.AccountStore(conn)
            retired = store.list_retired_usernames()
            return JSONResponse({"retired": retired, "usernames": retired})
        finally:
            conn.close()

    async def admin_release_username_api(request: Request) -> Response:
        principal = request_principal(request)
        if not is_admin(principal):
            return not_found()
        auth_denied = require_recent_auth(principal)
        if auth_denied is not None:
            return auth_denied

        body: dict[str, Any] = {}
        try:
            body = await request.json()
        except Exception:
            pass

        raw_username = body.get("username") or request.query_params.get("username")
        if not raw_username or not isinstance(raw_username, str):
            return json_error(400, "username_required")

        username = raw_username.strip().lower()
        apply_val = body.get("apply", request.query_params.get("apply", False))
        apply = apply_val is True or str(apply_val).lower() == "true"

        conn = open_db(ctx)
        auth_conn = open_auth_db(ctx)
        try:
            store = accounts_store.AccountStore(conn)
            if not apply:
                try:
                    preview = store.preview_release_username(username, auth_conn=auth_conn)
                    return JSONResponse({"ok": True, "apply": False, "preview": preview, **preview})
                except LookupError:
                    return json_error(404, "username_not_retired")

            try:
                success = store.release_username(
                    username,
                    released_by=principal.account_id,
                    auth_conn=auth_conn,
                )
                if not success:
                    return json_error(404, "username_not_retired")
                auth_db.record_admin_audit(
                    auth_conn,
                    actor_account_id=principal.account_id,
                    action="username.release",
                    detail={"released": True},
                )
                return JSONResponse({"ok": True, "apply": True, "released": True, "username": username})
            except LookupError:
                return json_error(404, "username_not_retired")
        finally:
            auth_conn.close()
            conn.close()

    return [
        Route(ADMIN_ACCOUNTS_PATH, admin_accounts_page, methods=["GET"]),
        Route(ADMIN_ACCOUNTS_API_PATH, admin_accounts_api, methods=["GET"]),
        Route(ADMIN_ACCOUNTS_API_PATH, admin_accounts_patch, methods=["PATCH"]),
        Route(ADMIN_USERNAMES_RETIRED_PATH, admin_retired_usernames_api, methods=["GET"]),
        Route(ADMIN_USERNAMES_RELEASE_PATH, admin_release_username_api, methods=["POST"]),
    ]
