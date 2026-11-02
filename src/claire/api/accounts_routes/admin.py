"""``/admin/accounts`` 싱글 모드 화면과 ``GET /api/admin/accounts`` (설계 §8.6).

관리자(``instance_role == "admin"``, ``owner`` 범위)가 아니면 404다. 싱글 화면은 관리자 계정 카드만 보인다. 모드 전환 카드(P2)와 IdP 카드(P5)는 아직 없다. 다른 계정의 콘텐츠를 읽는 API는 두지 않는다(K5, D5).
"""

from __future__ import annotations

import asyncio
import sqlite3
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from ...store import accounts as accounts_store
from ..principal import Principal, request_principal
from .common import AccountsContext, escape, format_ts, not_found, open_db, render_template

ADMIN_ACCOUNTS_PATH = "/admin/accounts"
ADMIN_ACCOUNTS_API_PATH = "/api/admin/accounts"

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


def account_summary(account: accounts_store.Account, theme_count: int) -> dict[str, Any]:
    """§8.6 목록 항목 중 P1에서 있는 값. 번호는 JS 정밀도 손실을 막기 위해 문자열이다(S13)."""
    return {
        "public_id": str(account.public_id),
        "username": account.username,
        "status": account.status,
        "role": account.role,
        "created_at": account.created_at,
        "activated_at": account.activated_at,
        "theme_count": theme_count,
    }


def list_account_summaries(ctx: AccountsContext) -> list[dict[str, Any]]:
    conn = open_db(ctx)
    try:
        counts = _theme_counts(conn)
        return [
            account_summary(a, counts.get(a.id, 0))
            for a in accounts_store.AccountStore(conn).list_accounts()
        ]
    finally:
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


def routes(ctx: AccountsContext) -> list[Route]:
    async def admin_accounts_page(request: Request) -> Response:
        if not is_admin(request_principal(request)):
            return not_found()
        items = await asyncio.to_thread(list_account_summaries, ctx)
        admin_items = [i for i in items if i["role"] == "admin"]
        cards = "".join(_admin_card(i) for i in admin_items)
        return render_template("admin_accounts.html", markup={"cards": cards})

    async def admin_accounts_api(request: Request) -> Response:
        if not is_admin(request_principal(request)):
            return not_found()
        items = await asyncio.to_thread(list_account_summaries, ctx)
        return JSONResponse({"accounts": items})

    return [
        Route(ADMIN_ACCOUNTS_PATH, admin_accounts_page, methods=["GET"]),
        Route(ADMIN_ACCOUNTS_API_PATH, admin_accounts_api, methods=["GET"]),
    ]
