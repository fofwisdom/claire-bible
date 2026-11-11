"""Federation state of one account and the console section bodies for ``/settings/federation`` and ``/admin/federation`` (Fedify sidecar design Sections 9.2 and 11, unit FP2-4).

This module does not import ``console``. Thus ``console`` (the settings section) and ``federation`` (the API routes and the admin section) can use it.

- ``account_federation_status`` gives the data of ``GET /api/federation/status``.
- The section bodies show server data only. ``console_account.js`` and ``console_admin.js`` do the changes through the API.
- All dynamic text goes through ``escape``.
"""

from __future__ import annotations

import sqlite3
from typing import Any, Literal

from ...store import accounts as accounts_store
from ...store import federation as federation_store
from .common import AccountsContext, escape, format_ts, open_db

__all__ = [
    "ACTOR_STATE_TEXT",
    "ActorState",
    "account_federation_status",
    "actor_state",
    "admin_federation_body",
    "domain_block_view",
    "settings_federation_body",
]

ActorState = Literal["active", "account_not_active", "no_username", "federation_off", "profile_not_public"]

# Text of each actor state on the console. ``console_account.js`` has the same table.
ACTOR_STATE_TEXT: dict[str, str] = {
    "active": "연합 중",
    "account_not_active": "일시 중지 · 계정이 활성 상태가 아닙니다",
    "no_username": "일시 중지 · USERNAME이 아직 없습니다",
    "federation_off": "꺼짐 · 인스턴스 운영자가 연합을 켜지 않았습니다",
    "profile_not_public": "일시 중지 · 프로필이 비공개입니다",
}


def actor_state(account: accounts_store.Account, *, federation: str) -> ActorState:
    """Return ``active`` when the account is a federation actor in network mode. If not, return the first condition of the actor rule that fails.

    The sequence is: account status, USERNAME, instance switch, ``profile_public``. The caller makes sure that the mode is ``network``.
    """
    if account.status != "active":
        return "account_not_active"
    if not account.username:
        return "no_username"
    if federation != "on":
        return "federation_off"
    if not account.profile_public:
        return "profile_not_public"
    return "active"


def account_federation_status(ctx: AccountsContext, conn: sqlite3.Connection, account_id: int) -> dict[str, Any]:
    """Return the federation state of one account. The mode must be ``network``.

    ``handle`` and ``actor_id`` are ``None`` when the account has no USERNAME. They are the future addresses when the account is not an actor now.
    """
    store = accounts_store.AccountStore(conn)
    account = store.get(account_id)
    federation = store.federation_state()
    state = actor_state(account, federation=federation)
    return {
        "federation": federation,
        "handle": f"@{account.username}@{ctx.config.expected_authority}" if account.username else None,
        "actor_id": f"{ctx.config.public_origin}/ap/actors/{account.public_id}" if account.username else None,
        "is_actor": state == "active",
        "actor_state": state,
        "followers": {"total": federation_store.count_followers(conn, account.id)},
        # Outbox tasks that the sidecar did not take or did not acknowledge.
        "pending_tasks": federation_store.count_pending_tasks(conn, account.id),
    }


def domain_block_view(block: federation_store.DomainBlock) -> dict[str, Any]:
    """Return the API form of one blocked domain. The account that made the block is not in the result."""
    return {"domain": block.domain, "note": block.note, "created_at": block.created_at}


# --- /settings/federation -------------------------------------------------------


def settings_federation_body(ctx: AccountsContext, *, mode: accounts_store.InstanceMode, account: accounts_store.Account) -> tuple[str, dict[str, Any]]:
    """Return the body and the bootstrap data of ``/settings/federation``. In single mode the body has a notice only and shows no USERNAME (S9)."""
    if mode != "network":
        body = (
            '<section class="console-subcard" id="settings-federation-unavailable" data-federation-mode="unavailable" aria-label="연합 안내">'
            "<h2>🌐 연합 (ActivityPub)</h2>"
            '<p class="hint">이 인스턴스는 싱글 모드로 운영 중이므로 연합을 쓸 수 없습니다. 연합은 네트워크 모드에서만 동작합니다.</p>'
            "</section>"
        )
        return body, {"federation_mode": "unavailable"}

    conn = open_db(ctx)
    try:
        status = account_federation_status(ctx, conn, account.id)
    finally:
        conn.close()
    state = status["actor_state"]
    rows = (
        ("handle", "핸들", status["handle"] or "(USERNAME 설정 전)"),
        ("actor_id", "액터 ID", status["actor_id"] or "-"),
        ("actor_state", "액터 상태", ACTOR_STATE_TEXT[state]),
        ("followers_total", "팔로워 수", f"{status['followers']['total']:,}"),
    )
    info_html = "".join(f'<dt>{escape(label)}</dt><dd data-federation-field="{escape(key)}">{escape(value)}</dd>' for key, label, value in rows)
    body = (
        f'<section class="console-subcard" id="settings-federation-status" data-federation-mode="network" data-actor-state="{escape(state)}" aria-label="연합 상태">'
        "<h2>🌐 내 연합 액터</h2>"
        '<p class="hint">연합이 켜져 있고 프로필이 공개이면 다른 서버(Mastodon 등)에서 아래 핸들로 이 계정을 찾고 팔로우할 수 있습니다. 프로필 공개 여부는 <a href="/settings/account">내 계정</a>에서 바꿉니다. 비공개로 바꾸면 연합이 일시 중지되지만, 다른 서버에 이미 전달된 사본은 지워지지 않습니다.</p>'
        f"<dl>{info_html}</dl>"
        "</section>"
        '<section class="console-subcard" id="settings-federation-followers" data-federation-followers aria-label="팔로워">'
        "<h2>👥 팔로워</h2>"
        '<p class="hint">이 계정을 팔로우하는 다른 서버의 계정입니다. 제거하면 이 서버의 팔로워 목록에서 지웁니다. 상대 서버에 알림(<code>Reject</code>)을 보내는 기능은 아직 없으므로, 상대 서버 화면에는 팔로우 상태가 남을 수 있습니다.</p>'
        '<div class="pref-table-wrap"><table class="pref-table">'
        "<thead><tr><th>액터</th><th>팔로우 시각</th><th></th></tr></thead>"
        '<tbody data-followers-tbody><tr><td colspan="3" style="color:var(--muted);text-align:center;">팔로워 목록을 불러오는 중입니다...</td></tr></tbody>'
        "</table></div>"
        '<div><button type="button" class="pref-btn" data-followers-more hidden>더 보기</button></div>'
        '<p class="result" data-settings-result role="status" aria-live="polite"></p>'
        "</section>"
    )
    return body, {"federation_mode": "network", "federation_status": status}


# --- /admin/federation ----------------------------------------------------------


def _domain_block_rows_html(blocks: list[federation_store.DomainBlock]) -> str:
    if not blocks:
        return '<tr data-domain-block-empty><td colspan="4" style="color:var(--muted);text-align:center;">차단한 도메인이 없습니다.</td></tr>'
    return "".join(
        f'<tr data-domain-block="{escape(block.domain)}">'
        f'<td><code class="pref-mono">{escape(block.domain)}</code></td>'
        f"<td>{escape(block.note)}</td>"
        f"<td>{escape(format_ts(block.created_at))}</td>"
        f'<td><button type="button" class="pref-btn pref-btn-danger" data-domain-block-remove="{escape(block.domain)}">차단 해제</button></td>'
        "</tr>"
        for block in blocks
    )


def admin_federation_body(ctx: AccountsContext) -> tuple[str, dict[str, Any]]:
    """Return the body and the bootstrap data of ``/admin/federation``.

    The body has the switch state and the blocked domains. It does not do the readiness checks: ``console_admin.js`` gets them from ``GET /api/admin/federation``, because check GF7 sends a network request.
    """
    conn = open_db(ctx)
    try:
        store = accounts_store.AccountStore(conn)
        federation = store.federation_state()
        mode = store.instance_mode()
        blocks = federation_store.list_domain_blocks(conn)
    finally:
        conn.close()
    target = "off" if federation == "on" else "on"
    switch_label = "연합 끄기" if federation == "on" else "연합 켜기"
    switch_class = "pref-btn pref-btn-danger" if federation == "on" else "pref-btn pref-btn-accent"
    body = (
        f'<section class="card" id="admin-federation-switch" data-federation-state="{escape(federation)}" aria-label="연합 스위치">'
        "<h2>🌐 연합 스위치</h2>"
        "<dl>"
        f'<dt>현재 상태</dt><dd><span class="badge {"badge-enabled" if federation == "on" else "badge-disabled"}" data-federation-field="federation">{"켜짐 (on)" if federation == "on" else "꺼짐 (off)"}</span></dd>'
        f'<dt>인스턴스 모드</dt><dd data-federation-field="mode">{"네트워크 모드" if mode == "network" else "싱글 모드"}</dd>'
        "</dl>"
        '<p class="hint">연합을 켜면 프로필이 공개인 활성 계정이 다른 서버에서 찾고 팔로우할 수 있는 액터가 됩니다. 켜려면 아래 준비 검사(GF1–GF7)를 모두 통과해야 하고, 본인 확인이 필요합니다. 끄면 액터 경로가 404를 돌려주지만, 다른 서버에 이미 전달된 사본은 지워지지 않습니다.</p>'
        f'<div><button type="button" class="{switch_class}" data-federation-switch="{target}">{switch_label}</button></div>'
        '<p class="result" id="admin-federation-switch-result" role="status" aria-live="polite"></p>'
        "</section>"
        '<section class="card" id="admin-federation-readiness" aria-label="연합 준비 검사">'
        '<div style="display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap">'
        "<h2>✅ 연합 준비 검사 (GF1–GF7)</h2>"
        '<button type="button" class="pref-btn" id="admin-federation-refresh">↻ 다시 검사</button>'
        "</div>"
        '<p class="result" id="admin-federation-readiness-result" role="status" aria-live="polite"></p>'
        '<div class="pref-table-wrap"><table class="pref-table">'
        "<thead><tr><th>항목</th><th>이름</th><th>상태</th><th>세부 내용</th></tr></thead>"
        '<tbody id="admin-federation-readiness-tbody"><tr><td colspan="4" style="color:var(--muted);text-align:center;">준비 검사를 실행하는 중입니다...</td></tr></tbody>'
        "</table></div>"
        "</section>"
        '<section class="card" id="admin-federation-sidecar" aria-label="사이드카 상태">'
        "<h2>🩺 사이드카 상태 (Fedify)</h2>"
        '<p class="hint">사이드카가 30초마다 보내는 마지막 하트비트입니다.</p>'
        '<dl id="admin-federation-heartbeat"><dt>하트비트</dt><dd>불러오는 중입니다...</dd></dl>'
        "</section>"
        '<section class="card" id="admin-federation-domain-blocks" aria-label="차단 도메인">'
        "<h2>⛔ 차단 도메인</h2>"
        '<p class="hint">차단한 도메인과 그 하위 도메인에서 오는 활동은 거부합니다. 추가와 해제는 본인 확인이 필요합니다. 이미 있는 팔로워는 차단해도 자동으로 지워지지 않습니다.</p>'
        '<div class="pref-table-wrap"><table class="pref-table">'
        "<thead><tr><th>도메인</th><th>메모</th><th>차단 시각</th><th></th></tr></thead>"
        f'<tbody id="admin-federation-domain-blocks-tbody">{_domain_block_rows_html(blocks)}</tbody>'
        "</table></div>"
        '<form id="admin-federation-domain-block-form" style="display:flex;gap:10px;align-items:flex-end;flex-wrap:wrap">'
        '<label style="display:flex;flex-direction:column;gap:4px;font-size:12px;font-weight:600">도메인<input type="text" name="domain" class="pref-input" placeholder="예: spam.example" maxlength="253" required autocomplete="off" spellcheck="false"/></label>'
        '<label style="display:flex;flex-direction:column;gap:4px;font-size:12px;font-weight:600">메모 (선택)<input type="text" name="note" class="pref-input" maxlength="200" autocomplete="off"/></label>'
        '<button type="submit" class="pref-btn pref-btn-accent">차단 추가</button>'
        "</form>"
        '<p class="result" id="admin-federation-domain-blocks-result" role="status" aria-live="polite"></p>'
        "</section>"
    )
    return body, {"federation": federation, "domain_blocks": [domain_block_view(block) for block in blocks]}
