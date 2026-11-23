"""The console section body of ``/admin/audit``: the audit records of the server in tabs.

- Tab ``admin``: the administrator actions (``admin_audit`` in the authentication database, ``auth_db.list_admin_audit``). The server renders the latest rows.
- Tab ``mcp``: the MCP calls (``mcp_telemetry``). ``console_admin.js`` reads them from ``GET /api/admin/mcp/telemetry``.

The tab set is the same component as ``/admin/federation`` (``console_ui.console_tabs``).
"""

from __future__ import annotations

import json
from typing import Any

from ...i18n import t
from ...store import accounts as accounts_store
from ...store import auth_db
from .common import AccountsContext, escape, format_ts, open_auth_db, open_db, t_code
from .console_ui import console_card, console_field, console_result, console_table, console_tabs

__all__ = ["ADMIN_AUDIT_LIMIT", "admin_audit_body"]

# The number of administrator action rows on the page (newest first).
ADMIN_AUDIT_LIMIT = 100


def _account_text(store: accounts_store.AccountStore, account_id: int | None, cache: dict[int, str]) -> str:
    """The text of an account of a record. A record keeps the account number only (K4), thus the page shows the current USERNAME of the account."""
    if account_id is None:
        return "—"
    if account_id not in cache:
        try:
            account = store.get(account_id)
        except accounts_store.AccountNotFound:
            cache[account_id] = t("console.admin.audit.account_missing", id=account_id)
        else:
            cache[account_id] = f"@{account.username} (#{account_id})" if account.username else f"#{account_id}"
    return cache[account_id]


def _admin_rows_html(ctx: AccountsContext) -> tuple[str, int]:
    auth = open_auth_db(ctx)
    try:
        rows = auth_db.list_admin_audit(auth, limit=ADMIN_AUDIT_LIMIT)
    finally:
        auth.close()
    if not rows:
        return f'<tr><td colspan="5" style="color:var(--muted);text-align:center;">{escape(t("console.admin.audit.admin_empty"))}</td></tr>', 0
    conn = open_db(ctx)
    try:
        store = accounts_store.AccountStore(conn)
        cache: dict[int, str] = {}
        html_rows = []
        for row in rows:
            detail = row["detail"]
            detail_html = f'<code class="pref-mono">{escape(json.dumps(detail, ensure_ascii=False, sort_keys=True))}</code>' if detail is not None else "—"
            html_rows.append(
                "<tr data-admin-audit-row>"
                f"<td>{escape(format_ts(row['at']))}</td>"
                f"<td>{escape(_account_text(store, row['actor_account_id'], cache))}</td>"
                f'<td><code class="pref-mono">{escape(row["action"])}</code></td>'
                f"<td>{escape(_account_text(store, row['target_account_id'], cache))}</td>"
                f"<td>{detail_html}</td>"
                "</tr>"
            )
    finally:
        conn.close()
    return "".join(html_rows), len(rows)


def _admin_card(ctx: AccountsContext) -> str:
    rows_html, _count = _admin_rows_html(ctx)
    table = console_table(
        [
            t("console.admin.audit.col_time"),
            t("console.admin.audit.col_actor"),
            t("console.admin.audit.col_action"),
            t("console.admin.audit.col_target"),
            t("console.admin.audit.col_detail"),
        ],
        rows_html,
        tbody_id="admin-audit-actions-tbody",
    )
    return console_card(
        t_code("console.admin.audit.admin_title", table="admin_audit"),
        table,
        id="admin-audit-actions-card",
        icon="shield",
        hint_html=escape(t("console.admin.audit.admin_hint", limit=ADMIN_AUDIT_LIMIT)),
        aria_label=t("console.admin.audit.admin_label"),
    )


def _mcp_card() -> str:
    controls = (
        '<div class="console-form-row">'
        + console_field(
            t("console.common.status"),
            f'<select id="admin-mcp-filter-status" class="pref-select"><option value="">{escape(t("console.admin.mcp.filter_all"))}</option><option value="SUCCESS">SUCCESS</option><option value="success">success</option><option value="ERROR">ERROR</option><option value="UNAUTHORIZED">UNAUTHORIZED</option><option value="auth_error">auth_error</option></select>',
            for_id="admin-mcp-filter-status",
        )
        + console_field(
            t("console.admin.mcp.filter_tool"),
            f'<input type="text" id="admin-mcp-filter-tool" class="pref-input" placeholder="{escape(t("console.admin.mcp.filter_tool_placeholder"))}" style="width:130px"/>',
            for_id="admin-mcp-filter-tool",
        )
        + console_field(
            t("console.admin.mcp.filter_limit"),
            f'<select id="admin-mcp-filter-limit" class="pref-select"><option value="25">{escape(t("console.admin.mcp.limit_25"))}</option><option value="50" selected>{escape(t("console.admin.mcp.limit_50"))}</option><option value="100">{escape(t("console.admin.mcp.limit_100"))}</option><option value="200">{escape(t("console.admin.mcp.limit_200"))}</option></select>',
            for_id="admin-mcp-filter-limit",
        )
        + f'<button type="button" id="admin-mcp-refresh-btn" class="pref-btn">{escape(t("console.common.refresh"))}</button>'
        + "</div>"
    )
    table = console_table(
        [t("console.admin.mcp.col_time"), "call_type", "tool_name", "status", "duration_ms", "response_bytes", "client_id"],
        f'<tr><td colspan="7" style="color:var(--muted);text-align:center;">{escape(t("console.admin.mcp.audit_loading"))}</td></tr>',
        tbody_id="pref-mcp-audit-tbody",
    )
    return console_card(
        t_code("console.admin.mcp.audit_title", table="mcp_telemetry"),
        console_result(id="admin-mcp-telemetry-status") + table,
        id="admin-mcp-telemetry-card",
        icon="list",
        hint_html=t_code("console.admin.mcp.audit_hint", endpoint="GET /api/admin/mcp/telemetry"),
        actions_html=controls,
        aria_label=t("console.admin.mcp.audit_label"),
    )


def admin_audit_body(ctx: AccountsContext) -> tuple[str, dict[str, Any]]:
    """Return the body and the bootstrap data of ``/admin/audit``."""
    body = console_tabs(
        [
            {"id": "admin", "label": t("console.admin.audit.tab_admin"), "icon": "shield", "html": _admin_card(ctx)},
            {"id": "mcp", "label": t("console.admin.audit.tab_mcp"), "icon": "pulse", "html": _mcp_card()},
        ],
        id="admin-audit-tabs",
        aria_label=t("console.admin.audit.tabs_label"),
    )
    return body, {}
