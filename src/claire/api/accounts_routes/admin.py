"""``/admin/accounts`` 싱글 모드 화면과 계정 관리 및 퇴역 USERNAME API (설계 §8.6, §8.9).

관리자(``server_role == "admin"``, ``owner`` 범위)가 아니면 404다.
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

from ...collaboration import reputation
from ...i18n import t
from ...store import accounts as accounts_store
from ...store import auth_db
from ...store import kb_collab
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
    t_code,
)

ADMIN_ACCOUNTS_PATH = "/admin/accounts"
ADMIN_ACCOUNTS_API_PATH = "/api/admin/accounts"
ADMIN_ACCOUNTS_SIGNUP_TRACE_API_PATH = "/api/admin/accounts/signup-trace"
ADMIN_USERNAMES_RETIRED_PATH = "/api/admin/usernames/retired"
ADMIN_USERNAMES_RELEASE_PATH = "/api/admin/usernames/release"

ACCOUNT_STATUSES = ("setup_required", "active", "suspended", "deleting", "purged")
ACCOUNT_ROLES = ("admin", "member")


def account_status_text(status: str) -> str:
    """The name of an account status in the active locale. A status that is not known shows as it is."""
    return t(f"console.account_status.{status}") if status in ACCOUNT_STATUSES else status


def account_role_text(role: str) -> str:
    """The name of an account role in the active locale."""
    return t(f"console.account_role.{role}") if role in ACCOUNT_ROLES else role


def is_admin(principal: Principal) -> bool:
    return principal.server_role == "admin" and principal.scope == "owner" and principal.account_id is not None


def _theme_counts(conn: sqlite3.Connection) -> dict[int, int]:
    rows = conn.execute(
        "SELECT account_id, COUNT(*) FROM knowledge_bases WHERE account_id IS NOT NULL GROUP BY account_id"
    ).fetchall()
    return {int(r[0]): int(r[1]) for r in rows}


def _account_kb_fields(conn: sqlite3.Connection, account: accounts_store.Account) -> dict[str, Any]:
    """The reputation level and the moderator data of one account (SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md Sections 8.6 and 10.2).

    - ``reputation``: the level from the data now; ``None`` for an account that is not active (an account that is not active has no reputation).
    - ``moderated_kb_count``: the shared knowledge bases where the account has an active membership with the role ``moderator``.
    - ``sole_moderator``: true when one of these knowledge bases has no other active moderator. When such an account is suspended, the knowledge base has only the admin as its moderator.
    """
    moderated = [int(r[0]) for r in conn.execute("SELECT kb_id FROM kb_memberships WHERE account_id=? AND role='moderator' AND state='active'", (account.id,))]
    return {
        "reputation": reputation.level(conn, account.id) if account.status == "active" else None,
        "moderated_kb_count": len(moderated),
        "sole_moderator": any(not [a for a in kb_collab.active_moderators(conn, kb_id) if a != account.id] for kb_id in moderated),
    }


def account_summary(conn: sqlite3.Connection, account: accounts_store.Account, *, has_provider_key: bool | None = None) -> dict[str, Any]:
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
        "theme_count": _theme_counts(conn).get(account.id, 0),
        **_account_kb_fields(conn, account),
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
        return [
            # 관리자는 providers.json을 쓰므로 멤버 항목에만 키 등록 여부를 싣는다(§11.2).
            account_summary(conn, a, has_provider_key=provider_keys.has_key(auth, account_id=a.id) if a.role == "member" else None)
            for a in accounts_store.AccountStore(conn).list_accounts()
        ]
    finally:
        auth.close()
        conn.close()


def build_signup_trace(ctx: AccountsContext) -> dict[str, Any]:
    """Build the full signup origin and multi-hop invitation lineage graph for every account."""
    conn = open_db(ctx)
    auth = open_auth_db(ctx)
    try:
        store = accounts_store.AccountStore(conn)
        accounts = store.list_accounts()
        redemptions = auth_db.list_all_completed_redemptions(auth)
        acc_by_id = {a.id: a for a in accounts}
        all_ids = sorted(set(acc_by_id) | {int(r["account_id"]) for r in redemptions} | {int(r["issuer_account_id"]) for r in redemptions})
        retired_names = store.retired_usernames_by_account_ids([aid for aid in all_ids if aid not in acc_by_id or acc_by_id[aid].username is None])
    finally:
        auth.close()
        conn.close()

    redemption_by_account: dict[int, dict[str, Any]] = {}
    for r in redemptions:
        aid = int(r["account_id"])
        if aid not in redemption_by_account:
            redemption_by_account[aid] = r

    all_id_set = set(all_ids)

    def _ref(aid: int) -> dict[str, Any]:
        acc = acc_by_id.get(aid)
        if acc is not None:
            return {
                "public_id": str(acc.public_id),
                "username": acc.username,
                "retired_username": retired_names.get(aid) if acc.username is None else None,
                "display_name": acc.display_name,
                "status": acc.status,
                "role": acc.role,
            }
        return {
            "public_id": f"deleted:{aid}",
            "username": None,
            "retired_username": retired_names.get(aid),
            "display_name": "",
            "status": "released",
            "role": "member",
        }

    parent_by_id: dict[int, int | None] = {}
    children_by_parent: dict[int | None, list[int]] = {}
    for aid in all_ids:
        r = redemption_by_account.get(aid)
        issuer_id = int(r["issuer_account_id"]) if r is not None else None
        parent_id = issuer_id if (issuer_id is not None and issuer_id in all_id_set and issuer_id != aid) else None
        parent_by_id[aid] = parent_id
        children_by_parent.setdefault(parent_id, []).append(aid)

    def _sort_key(aid: int) -> tuple[int, float, int]:
        acc = acc_by_id.get(aid)
        role_rank = 0 if (acc is not None and acc.role == "admin") else 1
        joined = float(redemption_by_account[aid]["joined_at"]) if aid in redemption_by_account else (float(acc.created_at) if acc is not None else 0.0)
        return (role_rank, joined, aid)

    for siblings in children_by_parent.values():
        siblings.sort(key=_sort_key)

    def _compute_path(aid: int) -> list[dict[str, Any]]:
        chain_ids: list[int] = []
        seen: set[int] = set()
        curr: int | None = aid
        while curr is not None and curr not in seen and curr in all_id_set:
            seen.add(curr)
            chain_ids.append(curr)
            curr = parent_by_id.get(curr)
        chain_ids.reverse()
        steps: list[dict[str, Any]] = []
        for step_id in chain_ids:
            ref = _ref(step_id)
            r = redemption_by_account.get(step_id)
            steps.append({
                "public_id": ref["public_id"],
                "username": ref["username"],
                "retired_username": ref["retired_username"],
                "status": ref["status"],
                "role": ref["role"],
                "invite_code": r["invite_code"] if r is not None else None,
                "signup_method": r["method"] if r is not None else None,
            })
        return steps

    flat_nodes: list[dict[str, Any]] = []

    def _build_node(aid: int, visiting: set[int]) -> dict[str, Any]:
        acc = acc_by_id.get(aid)
        r = redemption_by_account.get(aid)
        parent_id = parent_by_id.get(aid)
        path = _compute_path(aid)
        origin = "invite" if r is not None else ("initial_setup" if (acc is not None and acc.role == "admin") else "direct")
        child_nodes: list[dict[str, Any]] = []
        next_visiting = visiting | {aid}
        for child_id in children_by_parent.get(aid, []):
            if child_id not in next_visiting:
                child_nodes.append(_build_node(child_id, next_visiting))
        descendant_count = sum(1 + int(c["descendant_count"]) for c in child_nodes)
        joined_ts = float(r["joined_at"]) if r is not None else (float(acc.activated_at or acc.created_at) if acc is not None else 0.0)
        base = {
            **_ref(aid),
            "origin": origin,
            "depth": len(path) - 1,
            "invited_by": _ref(parent_id) if parent_id is not None else None,
            "invite_code": r["invite_code"] if r is not None else None,
            "invite_public_id": r["invitation_public_id"] if r is not None else None,
            "invite_note": r["note"] if r is not None else "",
            "signup_method": r["method"] if r is not None else None,
            "joined_at": joined_ts,
            "invitee_count": len(child_nodes),
            "descendant_count": descendant_count,
            "path": path,
        }
        flat_nodes.append(dict(base))
        return {**base, "children": child_nodes}

    roots = [_build_node(rid, set()) for rid in children_by_parent.get(None, [])]
    seen_flat = {n["public_id"] for n in flat_nodes}
    for aid in all_ids:
        pub = str(acc_by_id[aid].public_id) if aid in acc_by_id else f"deleted:{aid}"
        if pub not in seen_flat:
            roots.append(_build_node(aid, set()))

    return {
        "summary": {
            "total_accounts": len(accounts),
            "invited_accounts": sum(1 for n in flat_nodes if n["origin"] == "invite"),
            "max_depth": max((int(n["depth"]) for n in flat_nodes), default=0),
            "root_count": len(roots),
        },
        "roots": roots,
        "accounts": flat_nodes,
    }


def _admin_card(item: dict[str, Any]) -> str:
    rows = [
        (t("console.admin.accounts.field.number"), item["public_id"]),
        ("USERNAME", item["username"] if item["username"] is not None else t("console.settings.account.username_unset")),
        (t("console.common.status"), account_status_text(item["status"])),
        (t("console.settings.account.field.role"), account_role_text(item["role"])),
        (t("console.admin.accounts.field.created"), format_ts(item["created_at"])),
        (t("console.admin.accounts.field.activated"), format_ts(item["activated_at"])),
        (t("console.admin.accounts.field.themes"), item["theme_count"]),
    ]
    body = "".join(f"<dt>{escape(k)}</dt><dd>{escape(v)}</dd>" for k, v in rows)
    return f'<section class="card" aria-label="{escape(t("console.admin.accounts.admin_card"))}"><h2>{escape(t("console.admin.accounts.admin_card"))}</h2><dl>{body}</dl></section>'


def _trace_handle(step: dict[str, Any]) -> str:
    if step.get("username"):
        return f"@{step['username']}"
    if step.get("retired_username"):
        return f"@{step['retired_username']}"
    return f"#{step.get('public_id', '?')}"


def _render_trace_nodes_html(nodes: list[dict[str, Any]], *, is_root_list: bool = True) -> str:
    if not nodes:
        return ""
    ul_attrs = ' id="admin-signup-trace-tree" class="signup-trace-tree" style="list-style:none;margin:12px 0 0;padding-left:0"' if is_root_list else ' style="list-style:none;margin:8px 0 0 18px;padding-left:12px;border-left:1px solid var(--border)"'
    items_html: list[str] = []
    for node in nodes:
        handle = _trace_handle(node)
        path_labels = [_trace_handle(step) for step in node.get("path", [])]
        path_text = " → ".join(path_labels)
        invite_code = str(node.get("invite_code") or "")
        method = str(node.get("signup_method") or "")
        origin = str(node.get("origin") or "direct")
        status = str(node.get("status") or "")
        role = str(node.get("role") or "member")

        badges = f'<span class="badge">{escape(account_role_text(role))}</span>'
        if status != "active":
            badges += f' <span class="badge">{escape(account_status_text(status))}</span>'

        if origin == "invite" and invite_code:
            method_key = "console.admin.accounts.trace.method_oidc" if method == "oidc" else "console.admin.accounts.trace.method_password"
            origin_html = f'<span class="claire-tag ok">{escape(t("console.admin.accounts.trace.origin_invite"))}</span> <code class="pref-mono">{escape(invite_code)}</code> <span style="color:var(--muted);font-size:12px">({escape(t(method_key))})</span>'
        elif origin == "initial_setup":
            origin_html = f'<span class="claire-tag">{escape(t("console.admin.accounts.trace.origin_initial_setup"))}</span>'
        else:
            origin_html = f'<span class="claire-tag">{escape(t("console.admin.accounts.trace.origin_direct"))}</span>'

        counts_html = ""
        invitee_count = int(node.get("invitee_count", 0))
        descendant_count = int(node.get("descendant_count", 0))
        if invitee_count > 0 or descendant_count > 0:
            counts_html = f' <span style="color:var(--muted);font-size:12px">· {escape(t("console.admin.accounts.trace.invitees"))}: {invitee_count}, {escape(t("console.admin.accounts.trace.descendants"))}: {descendant_count}</span>'

        children_html = _render_trace_nodes_html(node.get("children", []), is_root_list=False)
        items_html.append(
            f'<li data-trace-account-id="{escape(str(node.get("public_id", "")))}" data-trace-username="{escape(handle.lstrip("@"))}" data-trace-invite-code="{escape(invite_code)}" data-trace-depth="{int(node.get("depth", 0))}" data-trace-path="{escape(path_text)}" style="margin:6px 0">'
            f'<div style="padding:8px 10px;border:1px solid var(--border);border-radius:6px;background:var(--bg)">'
            f'<div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">'
            f'<strong>{escape(handle)}</strong> <code class="pref-mono">{escape(str(node.get("public_id", "")))}</code> {badges} {origin_html}{counts_html}'
            f"</div>"
            f'<div style="color:var(--muted);font-size:12px;margin-top:4px">{escape(t("console.admin.accounts.trace.path_label"))}: {escape(path_text)}</div>'
            f"</div>"
            f"{children_html}"
            f"</li>"
        )
    return f"<ul{ul_attrs}>{''.join(items_html)}</ul>"


def _render_signup_trace_section(signup_trace: dict[str, Any]) -> str:
    summary = signup_trace.get("summary") or {}
    roots = signup_trace.get("roots") or []
    tree_html = _render_trace_nodes_html(roots, is_root_list=True) if roots else f'<p id="admin-signup-trace-tree" style="color:var(--muted)">{escape(t("console.admin.accounts.trace.empty"))}</p>'
    return f"""
      <section class="card" id="admin-signup-trace-section" aria-label="{escape(t("console.admin.accounts.trace.label"))}">
        <h2><span data-icon="users"></span> {t_code("console.admin.accounts.trace.title", endpoint="/api/admin/accounts/signup-trace")}</h2>
        <p class="hint">{escape(t("console.admin.accounts.trace.hint"))}</p>
        <div class="pref-stat-row" id="admin-signup-trace-stats">
          <div class="pref-stat"><b>{int(summary.get("total_accounts", 0))}</b><span>{escape(t("console.admin.accounts.trace.stat_total"))}</span></div>
          <div class="pref-stat"><b>{int(summary.get("invited_accounts", 0))}</b><span>{escape(t("console.admin.accounts.trace.stat_invited"))}</span></div>
          <div class="pref-stat"><b>{int(summary.get("max_depth", 0))}</b><span>{escape(t("console.admin.accounts.trace.stat_max_depth"))}</span></div>
        </div>
        <div style="margin-top:10px">
          <label style="display:flex;flex-direction:column;gap:4px;font-size:12px;font-weight:600;max-width:340px">{escape(t("console.admin.accounts.trace.filter_label"))}
            <input type="search" id="admin-signup-trace-filter" class="pref-input" placeholder="{escape(t("console.admin.accounts.trace.filter_placeholder"))}"/>
          </label>
        </div>
        {tree_html}
        <p id="admin-signup-trace-empty" style="color:var(--muted);margin-top:8px" hidden>{escape(t("console.admin.accounts.trace.no_matches"))}</p>
      </section>"""


def _render_admin_accounts_html(
    *,
    mode: str,
    items: list[dict[str, Any]],
    retired: list[dict[str, Any]],
    tier_dict: dict[str, Any],
    signup_trace: dict[str, Any],
) -> str:
    admin_items = [i for i in items if i["role"] == "admin"]
    member_items = [i for i in items if i["role"] != "admin"]
    cards = "".join(_admin_card(i) for i in admin_items)

    member_trs: list[str] = []
    for m in member_items:
        pub_id = str(m["public_id"])
        uname = m.get("username") or t("console.admin.accounts.username_unset")
        st = m.get("status", "")
        st_label = account_status_text(st)
        rep = m["reputation"]
        rep_html = f'<span class="claire-tag {"ok" if rep == "good" else "warn"}">{escape(rep)}</span>' if rep is not None else escape(t("console.common.none"))
        mod_kbs = int(m["moderated_kb_count"])
        sole_warn = f' <span class="claire-tag warn">{escape(t("console.admin.accounts.sole_mod_warn"))}</span>' if st == "suspended" and m["sole_moderator"] else ""
        key_tag = t("console.admin.accounts.key_registered") if m.get("has_provider_key") else t("console.common.none")
        member_trs.append(
            f'<tr data-account-public-id="{escape(pub_id)}" data-account-status="{escape(st)}">'
            f'<td><code class="pref-mono">{escape(pub_id)}</code></td>'
            f"<td><strong>{escape(uname)}</strong></td>"
            f'<td><span class="badge">{escape(st_label)}</span>{sole_warn}</td>'
            f'<td>{int(m.get("theme_count", 0))}</td>'
            f"<td>{rep_html}</td>"
            f'<td>{mod_kbs}</td>'
            f"<td>{escape(key_tag)}</td>"
            f'<td data-member-actions="{escape(pub_id)}">'
            f'<button type="button" class="pref-btn" data-account-action="suspend" data-public-id="{escape(pub_id)}">{escape(t("console.admin.accounts.action.suspend"))}</button> '
            f'<button type="button" class="pref-btn" data-account-action="resume" data-public-id="{escape(pub_id)}">{escape(t("console.admin.accounts.action.resume"))}</button> '
            f'<button type="button" class="pref-btn" data-account-action="schedule_delete" data-public-id="{escape(pub_id)}">{escape(t("console.admin.accounts.action.schedule_delete"))}</button> '
            f'<button type="button" class="pref-btn" data-account-action="cancel_delete" data-public-id="{escape(pub_id)}">{escape(t("console.admin.accounts.action.cancel_delete"))}</button> '
            f'<button type="button" class="pref-btn pref-btn-danger" data-account-action="purge" data-public-id="{escape(pub_id)}">{escape(t("console.admin.accounts.action.purge"))}</button> '
            f'<button type="button" class="pref-btn" data-account-action="reset_password_link" data-public-id="{escape(pub_id)}">{escape(t("console.admin.accounts.action.reset_password_link"))}</button>'
            f"</td>"
            f"</tr>"
        )
    members_tbody = (
        "".join(member_trs)
        if member_trs
        else f'<tr><td colspan="8" style="color:var(--muted);text-align:center;">{escape(t("console.admin.accounts.members_empty"))}</td></tr>'
    )

    invites_link_section = (
        f'<section class="card" id="admin-invites-section" aria-label="{escape(t("console.admin.accounts.invites.label"))}">'
        f'<h2><span data-icon="mail"></span> {escape(t("console.admin.accounts.invites.title"))}</h2>'
        f'<p class="hint">{escape(t("console.admin.accounts.invites.moved_hint"))}</p>'
        f'<div style="display:flex;gap:8px;flex-wrap:wrap">'
        f'<a class="pref-btn pref-btn-accent" href="/settings/invites">{escape(t("console.admin.accounts.invites.open_settings"))}</a>'
        f'<a class="pref-btn" href="/admin/server#server-invites-policy">{escape(t("console.admin.accounts.invites.open_server_policy"))}</a>'
        f"</div>"
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
            f'<button type="button" class="pref-btn" data-retired-preview="{escape(uname)}">{escape(t("console.admin.accounts.retired.preview_row"))}</button> '
            f'<button type="button" class="pref-btn pref-btn-danger" data-retired-release="{escape(uname)}">{escape(t("console.admin.accounts.retired.release_row"))}</button>'
            f"</td>"
            f"</tr>"
        )
    retired_tbody = (
        "".join(retired_trs)
        if retired_trs
        else f'<tr><td colspan="4" style="color:var(--muted);text-align:center;">{escape(t("console.admin.accounts.retired.empty"))}</td></tr>'
    )

    storage_mb = int(tier_dict.get("storage_mb", 0))
    max_docs = int(tier_dict.get("max_documents", 0))
    monthly_tokens = int(tier_dict.get("monthly_tokens", 0))
    daily_ingests = int(tier_dict.get("daily_ingests", 0))
    active_count = sum(1 for i in items if i.get("status") == "active")
    suspended_count = sum(1 for i in items if i.get("status") in {"suspended", "deleting"})
    total_themes_count = sum(int(i.get("theme_count", 0)) for i in items)

    return f"""
      {cards}
      <section class="card" id="admin-members-section" aria-label="{escape(t("console.admin.accounts.members.label"))}">
        <h2><span data-icon="users"></span> {t_code("console.admin.accounts.members.title", endpoint="/api/admin/accounts")}</h2>
        <p class="hint">{escape(t("console.admin.accounts.members.hint"))}</p>
        <p class="result" id="admin-accounts-result" data-accounts-result role="status" aria-live="polite"></p>
        <div class="pref-table-wrap">
          <table class="pref-table">
            <thead>
              <tr><th>{escape(t("console.admin.accounts.members.col_number"))}</th><th>USERNAME</th><th>{escape(t("console.common.status"))}</th><th>{escape(t("console.admin.accounts.field.themes"))}</th><th>{escape(t("console.admin.accounts.col_reputation"))}</th><th>{escape(t("console.admin.accounts.col_mod_kbs"))}</th><th>{escape(t("console.admin.accounts.members.col_key"))}</th><th>{escape(t("console.admin.accounts.members.col_actions"))}</th></tr>
            </thead>
            <tbody id="admin-accounts-tbody">{members_tbody}</tbody>
          </table>
        </div>
      </section>
      {_render_signup_trace_section(signup_trace)}
      <section class="card" id="admin-retired-usernames-section" aria-label="{escape(t("console.admin.accounts.retired.label"))}">
        <h2><span data-icon="restore"></span> {t_code("console.admin.accounts.retired.title", endpoint="/api/admin/usernames/*")}</h2>
        <p class="hint">{escape(t("console.admin.accounts.retired.hint"))}</p>
        <form id="admin-retired-release-form" data-retired-release-form style="display:flex;gap:8px;align-items:flex-end;flex-wrap:wrap">
          <label style="display:flex;flex-direction:column;gap:4px;font-size:12px;font-weight:600">{escape(t("console.admin.accounts.retired.target"))}
            <input type="text" name="username" id="admin-retired-username-input" class="pref-input" placeholder="{escape(t("console.admin.accounts.retired.target_placeholder"))}" style="width:180px" required/>
          </label>
          <button type="button" class="pref-btn" id="admin-retired-preview-btn">{escape(t("console.admin.accounts.retired.preview"))}</button>
          <button type="submit" class="pref-btn pref-btn-danger" id="admin-retired-apply-btn">{escape(t("console.admin.accounts.retired.release"))}</button>
        </form>
        <p class="result" id="admin-retired-result" data-retired-result role="status" aria-live="polite"></p>
        <div class="pref-table-wrap">
          <table class="pref-table">
            <thead>
              <tr><th>{escape(t("console.admin.accounts.retired.col_username"))}</th><th>{escape(t("console.admin.accounts.retired.col_time"))}</th><th>{escape(t("console.admin.accounts.retired.col_reason"))}</th><th>{escape(t("console.common.manage"))}</th></tr>
            </thead>
            <tbody id="admin-retired-tbody">{retired_tbody}</tbody>
          </table>
        </div>
      </section>
      <section class="card" id="pref-userdb-admin" data-userdb-view="admin" aria-label="{escape(t("console.admin.accounts.userdb.label"))}">
        <h2><span data-icon="database"></span> {escape(t("console.admin.accounts.userdb.title"))}</h2>
        <p class="hint">{escape(t("console.admin.accounts.userdb.hint"))} <a href="/admin/server"><span data-icon="server"></span> {escape(t("console.admin.accounts.userdb.hint_link"))}</a></p>
        <div class="pref-stat-row" id="pref-userdb-stats">
          <div class="pref-stat"><b>{active_count}</b><span>{escape(t("console.admin.accounts.userdb.active"))}</span></div>
          <div class="pref-stat"><b>{suspended_count}</b><span>{escape(t("console.admin.accounts.userdb.suspended"))}</span></div>
          <div class="pref-stat"><b>{len(retired)}</b><span>{escape(t("console.admin.accounts.retired.col_username"))}</span></div>
          <div class="pref-stat"><b>{total_themes_count}</b><span>{escape(t("console.admin.accounts.userdb.total_themes"))}</span></div>
        </div>
        <div class="pref-quota-grid">
          <label>{escape(t("console.admin.accounts.userdb.quota_storage"))}<input id="pref-quota-storage" type="number" class="pref-input" value="{storage_mb}" readonly/></label>
          <label>{escape(t("console.admin.accounts.userdb.quota_docs"))}<input id="pref-quota-docs" type="number" class="pref-input" value="{max_docs}" readonly/></label>
          <label>{escape(t("console.admin.accounts.userdb.quota_tokens"))}<input id="pref-quota-llm" type="number" class="pref-input" value="{monthly_tokens}" readonly/></label>
          <label>{escape(t("console.admin.accounts.userdb.quota_ingests"))}<input id="pref-quota-feeds" type="number" class="pref-input" value="{daily_ingests}" readonly/></label>
          <label class="pref-quota-switch"><span>{escape(t("console.admin.accounts.userdb.byok_switch"))} {prototype_badge("admin-accounts-byok-switch", "prototype")}</span><input id="pref-quota-byo" type="checkbox" switch checked disabled/></label>
        </div>
        {prototype_note("admin-accounts-byok-switch", t("console.admin.accounts.userdb.byok_note"))}
        <div class="pref-table-wrap">
          <table class="pref-table">
            <caption style="caption-side:top;text-align:left;padding:0 0 6px">{prototype_badge("admin-accounts-isolation-table", "static")} <span class="proto-note-inline">{escape(t("console.admin.accounts.userdb.table_note"))}</span></caption>
            <thead><tr><th>{escape(t("console.admin.accounts.userdb.col_kind"))}</th><th>{escape(t("console.admin.accounts.userdb.col_path"))}</th><th>{escape(t("console.admin.accounts.userdb.col_access"))}</th><th>{escape(t("console.admin.accounts.userdb.col_quota"))}</th></tr></thead>
            <tbody id="pref-userdb-tbody">
              <tr><td><strong>{escape(t("console.admin.accounts.userdb.row_admin"))}</strong></td><td><code class="pref-mono">data/claire.db · themes/</code></td><td>{escape(t("console.admin.accounts.userdb.row_admin_access"))}</td><td>{escape(t("console.admin.accounts.userdb.row_admin_quota"))}</td></tr>
              <tr><td><strong>{escape(t("console.admin.accounts.userdb.row_member"))}</strong></td><td><code class="pref-mono">{escape(t("console.admin.accounts.userdb.row_member_path"))}</code></td><td>{escape(t("console.admin.accounts.userdb.row_member_access"))}</td><td>{escape(t("console.admin.accounts.userdb.row_member_quota"))}</td></tr>
            </tbody>
          </table>
        </div>
      </section>
      {invites_link_section}"""


def routes(ctx: AccountsContext) -> list[Route]:
    async def admin_accounts_page(request: Request) -> Response:
        principal = request_principal(request)
        if not is_admin(principal):
            return not_found()
        raw_theme = request.query_params.get("kb") if "kb" in request.query_params else None

        def _render() -> Response:
            from ...accounts import server_tier
            from .console import ADMIN_SPEC_BY_SLUG, _render_console_section

            items = list_account_summaries(ctx)
            signup_trace = build_signup_trace(ctx)
            conn = open_db(ctx)
            try:
                store = accounts_store.AccountStore(conn)
                mode = store.server_mode()
                retired = store.list_retired_usernames()
                tier_dict = server_tier.load_server_tier(conn).to_dict()
            finally:
                conn.close()

            body_html = _render_admin_accounts_html(
                mode=mode,
                items=items,
                retired=retired,
                tier_dict=tier_dict,
                signup_trace=signup_trace,
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

    async def admin_accounts_signup_trace_api(request: Request) -> Response:
        if not is_admin(request_principal(request)):
            return not_found()
        trace = await asyncio.to_thread(build_signup_trace, ctx)
        return JSONResponse(trace, headers={"Cache-Control": "no-store"})

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
                return JSONResponse({"ok": True, "account": account_summary(conn, updated)})

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
                return JSONResponse({"ok": True, "account": account_summary(conn, updated)})

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
                    return JSONResponse({"ok": True, "account": account_summary(conn, updated)})

                if action == "purge" or mode in ("immediate", "purge") or immediate or status == "purged":
                    data_dir = getattr(ctx.config, "data_dir", None)
                    # Fedify sidecar design Section 8.1: the purge of an actor with followers sends Delete(Person) through the sidecar.
                    # If the sidecar is not available, the purge waits: the account keeps its status, and the admin can continue with the explicit option that skips the federated delete.
                    from ...accounts import federation_readiness
                    from ...store import actor_keys
                    from ...store import federation as federation_store
                    from .. import federation_routes

                    skip_federated_delete = body.get("skip_federated_delete") is True
                    needs_federated_delete = store.federation_state() == "on" and bool(target.username) and federation_store.count_followers(conn, target.id) > 0
                    if needs_federated_delete and not skip_federated_delete:
                        heartbeat = federation_routes.read_heartbeat(ctx.data_dir or data_dir) if (ctx.data_dir or data_dir) else None
                        if not federation_readiness.heartbeat_is_recent(heartbeat):
                            return json_error(409, "federation_sidecar_unavailable", detail="The federation sidecar is not available, so the followers of this actor cannot get the Delete activity. Start the sidecar and try again, or send skip_federated_delete: true.")
                    try:
                        # The option "Delete my activity records" of the purge confirmation (SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md Section 13.2, CT19).
                        updated = store.purge(target.id, data_dir=data_dir, skip_federated_delete=skip_federated_delete, delete_activity_records=body.get("delete_activity_records") is True)
                    except accounts_store.AccountStateError as exc:
                        return json_error(400, "invalid_state_transition", detail=str(exc))
                    # The keys stay only while a delete_actor task waits for the sidecar. The acknowledgement of that task deletes them.
                    if not federation_store.has_pending_delete_actor(conn, target.id):
                        actor_keys.delete_actor_keys(auth_conn, target.id)
                    if needs_federated_delete and skip_federated_delete:
                        auth_db.record_admin_audit(auth_conn, actor_account_id=principal.account_id, action="federation.purge_skip_delete", target_account_id=target.id)
                    # 유저 단위 기능의 계정 자료(봇 토큰·백업 정책·계정 백업·계정 번들)도 지운다.
                    from .features import purge_account_features

                    purge_account_features(auth_conn, target.id, data_dir=ctx.data_dir or data_dir)
                    auth_db.record_admin_audit(
                        auth_conn,
                        actor_account_id=principal.account_id,
                        action="account.purge",
                        target_account_id=target.id,
                    )
                    return JSONResponse({"ok": True, "account": account_summary(conn, updated)})

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
                return JSONResponse({"ok": True, "account": account_summary(conn, updated)})

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
        Route(ADMIN_ACCOUNTS_SIGNUP_TRACE_API_PATH, admin_accounts_signup_trace_api, methods=["GET"]),
        Route(ADMIN_USERNAMES_RETIRED_PATH, admin_retired_usernames_api, methods=["GET"]),
        Route(ADMIN_USERNAMES_RELEASE_PATH, admin_release_username_api, methods=["POST"]),
    ]
