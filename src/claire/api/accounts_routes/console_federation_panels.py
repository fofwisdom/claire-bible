"""Federation state of one account and the console section bodies for ``/settings/federation`` and ``/admin/federation`` (Fedify sidecar design Sections 9.2 and 11, unit FP2-4).

This module does not import ``console``. Thus ``console`` (the settings section) and ``federation`` (the API routes and the admin section) can use it.

- ``account_federation_status`` gives the data of ``GET /api/federation/status``.
- The section bodies show server data only. ``console_account.js`` and ``console_admin.js`` do the changes through the API.
- All dynamic text goes through ``escape``.
"""

from __future__ import annotations

import sqlite3
from typing import Any, Literal

from ...i18n import t
from ...store import accounts as accounts_store
from ...store import federation as federation_store
from ...store import federation_client as federation_client_store
from .common import AccountsContext, escape, format_ts, open_db, t_code

__all__ = [
    "ACTOR_STATES",
    "ActorState",
    "account_federation_status",
    "actor_state",
    "admin_federation_body",
    "domain_block_view",
    "settings_federation_body",
]

ActorState = Literal["active", "account_not_active", "no_username", "federation_off", "profile_not_public"]

# The text of each actor state is the catalog key ``console.federation.actor_state.<state>``. ``console_account.js`` reads the same keys.
ACTOR_STATES = ("active", "account_not_active", "no_username", "federation_off", "profile_not_public")


def actor_state(account: accounts_store.Account, *, federation: str) -> ActorState:
    """Return ``active`` when the account is a federation actor in network mode. If not, return the first condition of the actor rule that fails.

    The sequence is: account status, USERNAME, server switch, ``profile_public``. The caller makes sure that the mode is ``network``.
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


def settings_federation_body(ctx: AccountsContext, *, mode: accounts_store.ServerMode, account: accounts_store.Account) -> tuple[str, dict[str, Any]]:
    """Return the body and the bootstrap data of ``/settings/federation``. In single mode the body has a notice only and shows no USERNAME (S9)."""
    if mode != "network":
        body = (
            f'<section class="console-subcard" id="settings-federation-unavailable" data-federation-mode="unavailable" aria-label="{escape(t("console.federation.unavailable_label"))}">'
            f"<h2><span data-icon='globe'></span> {escape(t('console.federation.unavailable_title'))}</h2>"
            f'<p class="hint">{escape(t("console.federation.unavailable_hint"))}</p>'
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
        ("handle", t("console.federation.actor.handle"), status["handle"] or t("console.federation.actor.handle_unset")),
        ("actor_id", t("console.federation.actor.id"), status["actor_id"] or "-"),
        ("actor_state", t("console.federation.actor.state"), t(f"console.federation.actor_state.{state}")),
        ("followers_total", t("console.federation.actor.followers"), f"{status['followers']['total']:,}"),
    )
    info_html = "".join(f'<dt>{escape(label)}</dt><dd data-federation-field="{escape(key)}">{escape(value)}</dd>' for key, label, value in rows)
    body = (
        f'<section class="console-subcard" id="settings-federation-status" data-federation-mode="network" data-actor-state="{escape(state)}" aria-label="{escape(t("console.federation.actor.label"))}">'
        f"<h2><span data-icon='globe'></span> {escape(t('console.federation.actor.title'))}</h2>"
        f'<p class="hint">{escape(t("console.federation.actor.hint"))} <a href="/settings/account">{escape(t("console.federation.actor.hint_link"))}</a></p>'
        f"<dl>{info_html}</dl>"
        "</section>"
        f'<section class="console-subcard" id="settings-federation-followers" data-federation-followers aria-label="{escape(t("console.federation.followers.title"))}">'
        f"<h2><span data-icon='users'></span> {escape(t('console.federation.followers.title'))}</h2>"
        f'<p class="hint">{t_code("console.federation.followers.hint", reject="Reject")}</p>'
        '<div class="pref-table-wrap"><table class="pref-table">'
        f"<thead><tr><th>{escape(t('console.federation.followers.col_actor'))}</th><th>{escape(t('console.federation.followers.col_time'))}</th><th></th></tr></thead>"
        f'<tbody data-followers-tbody><tr><td colspan="3" style="color:var(--muted);text-align:center;">{escape(t("console.federation.followers.loading"))}</td></tr></tbody>'
        "</table></div>"
        f'<div><button type="button" class="pref-btn" data-followers-more hidden>{escape(t("console.common.more"))}</button></div>'
        '<p class="result" data-settings-result role="status" aria-live="polite"></p>'
        "</section>"
        # FP4-5 (FEDERATION_REMOTE_READING_DESIGN.md Section 10): the retention of the received posts of this account. console_account.js reads and saves the value with /api/federation/settings.
        f'<section class="console-subcard" id="settings-federation-reading" data-federation-reading aria-label="{escape(t("console.federation.retention.title"))}">'
        f'<h2><span data-icon="folder"></span> {escape(t("console.federation.retention.title"))}</h2>'
        f'<p class="hint">{escape(t("console.federation.retention.hint"))}</p>'
        f'<form data-retention-form data-msg-saved="{escape(t("console.federation.reading.saved"))}" data-msg-failed="{escape(t("console.federation.reading.failed"))}" data-msg-server="{escape(t("console.federation.retention.use_server"))}" style="display:flex;gap:14px;align-items:flex-end;flex-wrap:wrap">'
        '<label style="display:flex;gap:6px;align-items:center;font-size:13px"><input type="checkbox" name="use_server"/> <span data-retention-server-label></span></label>'
        f'<label style="display:flex;flex-direction:column;gap:4px;font-size:12px;font-weight:600">{escape(t("console.federation.reading.retention_days"))}<input type="number" name="retention_days" class="pref-input" min="1" step="1" inputmode="numeric" autocomplete="off"/></label>'
        f'<button type="submit" class="pref-btn pref-btn-accent">{escape(t("common.action.save"))}</button>'
        "</form>"
        '<p class="result" data-retention-result role="status" aria-live="polite"></p>'
        "</section>"
    )
    return body, {"federation_mode": "network", "federation_status": status}


# --- /admin/federation ----------------------------------------------------------


def _domain_block_rows_html(blocks: list[federation_store.DomainBlock]) -> str:
    if not blocks:
        return f'<tr data-domain-block-empty><td colspan="4" style="color:var(--muted);text-align:center;">{escape(t("console.federation.blocks.empty"))}</td></tr>'
    return "".join(
        f'<tr data-domain-block="{escape(block.domain)}">'
        f'<td><code class="pref-mono">{escape(block.domain)}</code></td>'
        f"<td>{escape(block.note)}</td>"
        f"<td>{escape(format_ts(block.created_at))}</td>"
        f'<td><button type="button" class="pref-btn pref-btn-danger" data-domain-block-remove="{escape(block.domain)}">{escape(t("console.federation.blocks.remove"))}</button></td>'
        "</tr>"
        for block in blocks
    )


# The settings whose stored unit is bytes. The form shows them in MiB.
_READING_BYTE_KEYS = ("media_image_max_bytes", "media_av_max_bytes")
_MIB = 1024 * 1024


def _reading_settings_html(values: dict[str, Any]) -> str:
    """The form of the reading settings of the server (FEDERATION_REMOTE_READING_DESIGN.md Section 10, console fields of unit FP4-5). ``console_admin.js`` sends the changed values to ``PUT /api/admin/federation/reading``."""
    fields = []
    for key in federation_client_store.READING_DEFAULTS:
        label = escape(t(f"console.federation.reading.{key}"))
        if key == "context_fetch":
            options = "".join(f'<option value="{value}"{" selected" if values[key] == value else ""}>{escape(t(f"console.federation.reading.context_fetch_{value}"))}</option>' for value in ("on", "off"))
            fields.append(f'<label style="display:flex;flex-direction:column;gap:4px;font-size:12px;font-weight:600">{label}<select name="{key}" class="pref-input" data-reading-initial="{escape(str(values[key]))}">{options}</select></label>')
            continue
        low, high = federation_client_store.READING_RANGES[key]
        scale = _MIB if key in _READING_BYTE_KEYS else 1
        shown, low, high = int(values[key]) // scale, low // scale, high // scale
        fields.append(
            f'<label style="display:flex;flex-direction:column;gap:4px;font-size:12px;font-weight:600">{label}'
            f'<input type="number" name="{key}" class="pref-input" value="{shown}" min="{low}" max="{high}" step="1" required inputmode="numeric" autocomplete="off" data-reading-scale="{scale}" data-reading-initial="{shown}"/>'
            f'<small style="font-weight:400;color:var(--muted)">{escape(t("console.federation.reading.range", low=f"{low:,}", high=f"{high:,}"))}</small></label>'
        )
    return (
        f'<section class="card" id="admin-federation-reading" aria-label="{escape(t("console.federation.reading.title"))}">'
        f'<h2><span data-icon="inbox"></span> {escape(t("console.federation.reading.title"))}</h2>'
        f'<p class="hint">{escape(t("console.federation.reading.hint"))}</p>'
        f'<form id="admin-federation-reading-form" data-msg-saved="{escape(t("console.federation.reading.saved"))}" data-msg-nochange="{escape(t("console.federation.reading.nochange"))}" data-msg-failed="{escape(t("console.federation.reading.failed"))}">'
        f'<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:12px 16px;margin-bottom:12px;align-items:start">{"".join(fields)}</div>'
        f'<button type="submit" class="pref-btn pref-btn-accent">{escape(t("common.action.save"))}</button>'
        "</form>"
        '<p class="result" id="admin-federation-reading-result" role="status" aria-live="polite"></p>'
        "</section>"
    )


def admin_federation_body(ctx: AccountsContext) -> tuple[str, dict[str, Any]]:
    """Return the body and the bootstrap data of ``/admin/federation``.

    The body has the switch state and the blocked domains. It does not do the readiness checks: ``console_admin.js`` gets them from ``GET /api/admin/federation``, because check GF7 sends a network request.
    """
    conn = open_db(ctx)
    try:
        store = accounts_store.AccountStore(conn)
        federation = store.federation_state()
        mode = store.server_mode()
        blocks = federation_store.list_domain_blocks(conn)
        reading = federation_client_store.get_reading_settings(conn)
    finally:
        conn.close()
    reading_html = _reading_settings_html(reading)
    target = "off" if federation == "on" else "on"
    switch_label = t("console.federation.switch.turn_off") if federation == "on" else t("console.federation.switch.turn_on")
    switch_class = "pref-btn pref-btn-danger" if federation == "on" else "pref-btn pref-btn-accent"
    single_note = (
        f'<div class="claire-note warn" id="admin-federation-single-note"><span>{escape(t("console.admin.federation.single_mode_note"))}</span></div>'
        if mode != "network"
        else ""
    )
    sw_panel = (
        f'{single_note}'
        f'<section class="card" id="admin-federation-switch" data-federation-state="{escape(federation)}" aria-label="{escape(t("console.federation.switch.title"))}">'
        f"<h2><span data-icon='globe'></span> {escape(t('console.federation.switch.title'))}</h2>"
        "<dl>"
        f'<dt>{escape(t("console.federation.switch.state"))}</dt><dd><span class="badge {"badge-enabled" if federation == "on" else "badge-disabled"}" data-federation-field="federation">{escape(t("console.federation.switch.on") if federation == "on" else t("console.federation.switch.off"))}</span></dd>'
        f'<dt>{escape(t("console.mode.title"))}</dt><dd data-federation-field="mode">{escape(t("console.mode.network") if mode == "network" else t("console.mode.single"))}</dd>'
        "</dl>"
        f'<p class="hint">{escape(t("console.federation.switch.hint"))}</p>'
        f'<div><button type="button" class="{switch_class}" data-federation-switch="{target}">{escape(switch_label)}</button></div>'
        '<p class="result" id="admin-federation-switch-result" role="status" aria-live="polite"></p>'
        '<div class="claire-srow" id="admin-federation-auto-contrib">'
        '<div class="claire-srow-info">'
        f'<label for="admin-fed-auto-contrib-toggle">{escape(t("console.admin.federation.auto_contrib_title"))}</label>'
        f'<span>{escape(t("console.admin.federation.auto_contrib_desc"))}</span>'
        '</div>'
        '<input id="admin-fed-auto-contrib-toggle" type="checkbox" class="claire-switch" data-fed-auto-contrib/>'
        '</div>'
        '<div class="claire-srow" id="admin-federation-signed-get">'
        '<div class="claire-srow-info">'
        f'<strong>{escape(t("console.admin.federation.signed_get_title"))}</strong>'
        f'<span>{escape(t("console.admin.federation.signed_get_desc"))}</span>'
        '</div>'
        f'<span class="claire-tag ok">{escape(t("console.admin.federation.signed_get_badge"))}</span>'
        '</div>'
        "</section>"
        f'<section class="card" id="admin-federation-readiness" aria-label="{escape(t("console.federation.readiness.label"))}">'
        '<div style="display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap">'
        f"<h2><span data-icon='check-circle'></span> {escape(t('console.federation.readiness.title'))}</h2>"
        f'<button type="button" class="pref-btn" id="admin-federation-refresh">{escape(t("console.federation.readiness.refresh"))}</button>'
        "</div>"
        '<p class="result" id="admin-federation-readiness-result" role="status" aria-live="polite"></p>'
        '<div class="pref-table-wrap"><table class="pref-table">'
        f"<thead><tr><th>{escape(t('console.readiness.col_item'))}</th><th>{escape(t('console.readiness.col_name'))}</th><th>{escape(t('console.common.status'))}</th><th>{escape(t('console.readiness.col_detail'))}</th></tr></thead>"
        f'<tbody id="admin-federation-readiness-tbody"><tr><td colspan="4" style="color:var(--muted);text-align:center;">{escape(t("console.federation.readiness.loading"))}</td></tr></tbody>'
        "</table></div>"
        "</section>"
        f'<section class="card" id="admin-federation-sidecar" aria-label="{escape(t("console.federation.sidecar.label"))}">'
        f"<h2><span data-icon='pulse'></span> {escape(t('console.federation.sidecar.title'))}</h2>"
        f'<p class="hint">{escape(t("console.federation.sidecar.hint"))}</p>'
        f'<dl id="admin-federation-heartbeat"><dt>{escape(t("console.federation.sidecar.heartbeat"))}</dt><dd>{escape(t("console.common.loading"))}</dd></dl>'
        "</section>"
        f"{reading_html}"
    )
    blocks_panel = (
        f'<section class="card" id="admin-federation-domain-blocks" aria-label="{escape(t("console.federation.blocks.title"))}">'
        f"<h2><span data-icon='block'></span> {escape(t('console.federation.blocks.title'))}</h2>"
        f'<p class="hint">{escape(t("console.federation.blocks.hint"))}</p>'
        '<div class="pref-table-wrap"><table class="pref-table">'
        f"<thead><tr><th>{escape(t('console.federation.blocks.domain'))}</th><th>{escape(t('console.admin.accounts.invites.col_note'))}</th><th>{escape(t('console.federation.blocks.col_time'))}</th><th></th></tr></thead>"
        f'<tbody id="admin-federation-domain-blocks-tbody">{_domain_block_rows_html(blocks)}</tbody>'
        "</table></div>"
        '<form id="admin-federation-domain-block-form" style="display:flex;gap:10px;align-items:flex-end;flex-wrap:wrap">'
        f'<label style="display:flex;flex-direction:column;gap:4px;font-size:12px;font-weight:600">{escape(t("console.federation.blocks.domain"))}<input type="text" name="domain" class="pref-input" placeholder="{escape(t("console.federation.blocks.domain_placeholder"))}" maxlength="253" required autocomplete="off" spellcheck="false"/></label>'
        f'<label style="display:flex;flex-direction:column;gap:4px;font-size:12px;font-weight:600">{escape(t("console.admin.accounts.invites.note"))}<input type="text" name="note" class="pref-input" maxlength="200" autocomplete="off"/></label>'
        f'<button type="submit" class="pref-btn pref-btn-accent">{escape(t("console.federation.blocks.add"))}</button>'
        "</form>"
        '<p class="result" id="admin-federation-domain-blocks-result" role="status" aria-live="polite"></p>'
        "</section>"
        f'<div id="admin-federation-suggestions-host"></div>'
    )
    body = (
        '<div class="claire-tabs" id="admin-federation-tabs">'
        '<div class="claire-tablist-wrap"><div class="claire-tablist" role="tablist" focusgroup="tablist nomemory" aria-label="Federation">'
        f'<button type="button" class="claire-tab" role="tab" data-tab="sw" aria-selected="true" focusgroupstart><span data-icon="globe"></span> <span>{escape(t("console.admin.federation.tab_switches"))}</span></button>'
        f'<button type="button" class="claire-tab" role="tab" data-tab="known" aria-selected="false"><span data-icon="server"></span> <span>{escape(t("console.admin.federation.tab_known"))}</span></button>'
        f'<button type="button" class="claire-tab" role="tab" data-tab="blocks" aria-selected="false"><span data-icon="block"></span> <span>{escape(t("console.admin.federation.tab_blocks"))}</span></button>'
        f'<button type="button" class="claire-tab" role="tab" data-tab="journal" aria-selected="false"><span data-icon="list"></span> <span>{escape(t("console.admin.federation.tab_journal"))}</span></button>'
        '</div></div>'
        '<div class="claire-tabpanels">'
        f'<div role="tabpanel" data-tabpanel="sw"><div class="claire-panel-inner">{sw_panel}</div></div>'
        '<div role="tabpanel" data-tabpanel="known" hidden="until-found"><div class="claire-panel-inner"><div id="admin-federation-known-host"></div></div></div>'
        f'<div role="tabpanel" data-tabpanel="blocks" hidden="until-found"><div class="claire-panel-inner">{blocks_panel}</div></div>'
        '<div role="tabpanel" data-tabpanel="journal" hidden="until-found"><div class="claire-panel-inner"><div id="admin-federation-journal-host"></div></div></div>'
        '</div></div>'
    )
    return body, {"federation": federation, "domain_blocks": [domain_block_view(block) for block in blocks]}
