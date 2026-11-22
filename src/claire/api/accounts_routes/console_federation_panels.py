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
from .console_ui import (
    console_actions,
    console_card,
    console_field,
    console_host,
    console_kv,
    console_result,
    console_table,
    console_toggle_row,
)

__all__ = [
    "ACTOR_STATES",
    "ActorState",
    "account_federation_status",
    "actor_state",
    "admin_federation_body",
    "domain_block_view",
    "settings_federation_body",
]

ActorState = Literal["active", "account_not_active", "no_username"]

# The text of each actor state is the catalog key ``console.federation.actor_state.<state>``. ``console_account.js`` reads the same keys.
ACTOR_STATES = ("active", "account_not_active", "no_username")


def actor_state(account: accounts_store.Account) -> ActorState:
    """Return ``active`` when the account is a federation actor. If not, return the first condition of the actor rule that fails. The rule is the same in the two server modes (KNOWLEDGE_FEDERATION_DESIGN.md Section 15 rule 5).

    The sequence is: account status, USERNAME. Federation has no server switch, and ``profile_public`` is not a condition of the actor (FEDERATION_GO_LIVE_DESIGN.md decisions GD4 and GD5): ``content_public`` tells if the content of the account is public.
    """
    if account.status != "active":
        return "account_not_active"
    if not account.username:
        return "no_username"
    return "active"


def content_public(account: accounts_store.Account) -> bool:
    """True when the personal knowledge base content and published documents of the account are public on other servers: the account is an actor and its profile is public (decisions GD5 and GD7). With a private profile, other servers find the account with ``discoverable`` false and can follow it, and notes, favourites, and boosts work for each actor, while published documents and shared knowledge base offers stay private."""
    return actor_state(account) == "active" and bool(account.profile_public)


def account_federation_status(ctx: AccountsContext, conn: sqlite3.Connection, account_id: int) -> dict[str, Any]:
    """Return the federation state of one account.

    ``handle`` and ``actor_id`` are ``None`` when the account has no USERNAME. They are the future addresses when the account is not an actor now.
    """
    store = accounts_store.AccountStore(conn)
    account = store.get(account_id)
    state = actor_state(account)
    return {
        "handle": f"@{account.username}@{ctx.config.expected_authority}" if account.username else None,
        "actor_id": f"{ctx.config.public_origin}/ap/actors/{account.public_id}" if account.username else None,
        "is_actor": state == "active",
        "actor_state": state,
        "content_public": content_public(account),
        "followers": {"total": federation_store.count_followers(conn, account.id)},
        # Outbox tasks that the sidecar did not take or did not acknowledge.
        "pending_tasks": federation_store.count_pending_tasks(conn, account.id),
    }


def domain_block_view(block: federation_store.DomainBlock) -> dict[str, Any]:
    """Return the API form of one blocked domain. The account that made the block is not in the result."""
    return {"domain": block.domain, "note": block.note, "created_at": block.created_at}


# --- /settings/federation -------------------------------------------------------


def settings_federation_body(ctx: AccountsContext, *, account: accounts_store.Account) -> tuple[str, dict[str, Any]]:
    """Return the body and the bootstrap data of ``/settings/federation``. The body is the same in the two server modes (KNOWLEDGE_FEDERATION_DESIGN.md Section 15 rule 5)."""
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
        ("content", t("console.federation.actor.content"), t("console.federation.content.public" if status["content_public"] else "console.federation.content.private")),
        ("followers_total", t("console.federation.actor.followers"), f"{status['followers']['total']:,}"),
    )
    info_html = console_kv([{"label": label, "value": value, "attrs": {"data-federation-field": key}} for key, label, value in rows])
    status_card = console_card(
        escape(t("console.federation.actor.title")),
        f'<p class="hint">{escape(t("console.federation.actor.hint"))} <a href="/settings/account">{escape(t("console.federation.actor.hint_link"))}</a></p>' + info_html,
        id="settings-federation-status",
        icon="globe",
        aria_label=t("console.federation.actor.label"),
        attrs={"data-actor-state": state},
    )
    followers_table = (
        '<div class="pref-table-wrap"><table class="pref-table">'
        f"<thead><tr><th>{escape(t('console.federation.followers.col_actor'))}</th><th>{escape(t('console.federation.followers.col_time'))}</th><th></th></tr></thead>"
        f'<tbody data-followers-tbody><tr><td colspan="3" style="color:var(--muted);text-align:center;">{escape(t("console.federation.followers.loading"))}</td></tr></tbody>'
        "</table></div>"
    )
    followers_card = console_card(
        escape(t("console.federation.followers.title")),
        followers_table
        + console_actions(f'<button type="button" class="pref-btn" data-followers-more hidden>{escape(t("console.common.more"))}</button>')
        + console_result(attrs={"data-settings-result": True}),
        id="settings-federation-followers",
        icon="users",
        hint_html=t_code("console.federation.followers.hint", reject="Reject"),
        aria_label=t("console.federation.followers.title"),
        attrs={"data-federation-followers": True},
    )
    return status_card + followers_card, {"federation_status": status}


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
        label = t(f"console.federation.reading.{key}")
        if key == "context_fetch":
            options = "".join(f'<option value="{value}"{" selected" if values[key] == value else ""}>{escape(t(f"console.federation.reading.context_fetch_{value}"))}</option>' for value in ("on", "off"))
            fields.append(console_field(label, f'<select name="{key}" class="pref-input" data-reading-initial="{escape(str(values[key]))}">{options}</select>'))
            continue
        low, high = federation_client_store.READING_RANGES[key]
        scale = _MIB if key in _READING_BYTE_KEYS else 1
        shown, low, high = int(values[key]) // scale, low // scale, high // scale
        fields.append(
            console_field(
                label,
                f'<input type="number" name="{key}" class="pref-input" value="{shown}" min="{low}" max="{high}" step="1" required inputmode="numeric" autocomplete="off" data-reading-scale="{scale}" data-reading-initial="{shown}"/>',
                hint_html=escape(t("console.federation.reading.range", low=f"{low:,}", high=f"{high:,}")),
            )
        )
    form_html = (
        f'<form id="admin-federation-reading-form" data-msg-saved="{escape(t("console.federation.reading.saved"))}" data-msg-nochange="{escape(t("console.federation.reading.nochange"))}" data-msg-failed="{escape(t("console.federation.reading.failed"))}">'
        f'<div class="console-form-grid" style="margin-bottom:12px">{"".join(fields)}</div>'
        + console_actions(f'<button type="submit" class="pref-btn pref-btn-accent">{escape(t("common.action.save"))}</button>')
        + "</form>"
        + console_result(id="admin-federation-reading-result")
    )
    return console_card(
        escape(t("console.federation.reading.title")),
        form_html,
        id="admin-federation-reading",
        icon="inbox",
        hint_html=escape(t("console.federation.reading.hint")),
        aria_label=t("console.federation.reading.title"),
    )


def admin_federation_body(ctx: AccountsContext) -> tuple[str, dict[str, Any]]:
    """Return the body and the bootstrap data of ``/admin/federation``.

    The body has the state card and the blocked domains. Federation has no switch (FEDERATION_GO_LIVE_DESIGN.md decision GD4). The body does not do the readiness checks: ``console_admin.js`` gets them from ``GET /api/admin/federation``, because check GF7 sends a network request.
    """
    conn = open_db(ctx)
    try:
        store = accounts_store.AccountStore(conn)
        mode = store.server_mode()
        blocks = federation_store.list_domain_blocks(conn)
        reading = federation_client_store.get_reading_settings(conn)
    finally:
        conn.close()
    reading_html = _reading_settings_html(reading)
    switch_kv = console_kv(
        [
            {"label": t("console.federation.switch.state"), "value_html": f'<span class="badge badge-enabled" data-federation-field="federation">{escape(t("console.federation.switch.always_on"))}</span>'},
            {"label": t("console.mode.title"), "value": t("console.mode.network") if mode == "network" else t("console.mode.single"), "attrs": {"data-federation-field": "mode"}},
        ]
    )
    auto_contrib_row = (
        '<div id="admin-federation-auto-contrib">'
        + console_toggle_row(
            t("console.admin.federation.auto_contrib_title"),
            "admin-fed-auto-contrib-toggle",
            desc_html=escape(t("console.admin.federation.auto_contrib_desc")),
            attrs={"data-fed-auto-contrib": True},
        )
        + "</div>"
    )
    signed_get_row = (
        '<div class="pref-row" id="admin-federation-signed-get">'
        '<div class="pref-row-label">'
        f'<strong>{escape(t("console.admin.federation.signed_get_title"))}</strong>'
        f'<span>{escape(t("console.admin.federation.signed_get_desc"))}</span>'
        "</div>"
        f'<span class="claire-tag ok">{escape(t("console.admin.federation.signed_get_badge"))}</span>'
        "</div>"
    )
    switch_card = console_card(
        escape(t("console.federation.switch.title")),
        switch_kv
        + f'<p class="hint">{escape(t("console.federation.switch.hint"))}</p>'
        + auto_contrib_row
        + signed_get_row,
        id="admin-federation-switch",
        icon="globe",
        attrs={"aria-label": t("console.federation.switch.title")},
    )
    readiness_card = console_card(
        escape(t("console.federation.readiness.title")),
        console_result(id="admin-federation-readiness-result")
        + console_table(
            [
                t("console.readiness.col_item"),
                t("console.readiness.col_name"),
                t("console.readiness.col_level"),
                t("console.common.status"),
                t("console.readiness.col_detail"),
            ],
            f'<tr><td colspan="5" style="color:var(--muted);text-align:center;">{escape(t("console.federation.readiness.loading"))}</td></tr>',
            tbody_id="admin-federation-readiness-tbody",
        ),
        id="admin-federation-readiness",
        icon="check-circle",
        actions_html=f'<button type="button" class="pref-btn" id="admin-federation-refresh">{escape(t("console.federation.readiness.refresh"))}</button>',
        aria_label=t("console.federation.readiness.label"),
    )
    sidecar_card = console_card(
        escape(t("console.federation.sidecar.title")),
        console_kv([(t("console.federation.sidecar.heartbeat"), t("console.common.loading"))], attrs={"id": "admin-federation-heartbeat"}),
        id="admin-federation-sidecar",
        icon="pulse",
        hint_html=escape(t("console.federation.sidecar.hint")),
        aria_label=t("console.federation.sidecar.label"),
    )
    sw_panel = switch_card + readiness_card + sidecar_card + reading_html
    blocks_table = console_table(
        [
            t("console.federation.blocks.domain"),
            t("console.admin.accounts.invites.col_note"),
            t("console.federation.blocks.col_time"),
            "",
        ],
        _domain_block_rows_html(blocks),
        tbody_id="admin-federation-domain-blocks-tbody",
    )
    blocks_form = (
        '<form id="admin-federation-domain-block-form" class="console-form-row">'
        + console_field(
            t("console.federation.blocks.domain"),
            f'<input type="text" name="domain" class="pref-input" placeholder="{escape(t("console.federation.blocks.domain_placeholder"))}" maxlength="253" required autocomplete="off" spellcheck="false"/>',
        )
        + console_field(t("console.admin.accounts.invites.note"), '<input type="text" name="note" class="pref-input" maxlength="200" autocomplete="off"/>')
        + f'<button type="submit" class="pref-btn pref-btn-accent">{escape(t("console.federation.blocks.add"))}</button>'
        + "</form>"
        + console_result(id="admin-federation-domain-blocks-result")
    )
    blocks_panel = console_card(
        escape(t("console.federation.blocks.title")),
        blocks_table + blocks_form,
        id="admin-federation-domain-blocks",
        icon="block",
        hint_html=escape(t("console.federation.blocks.hint")),
        aria_label=t("console.federation.blocks.title"),
    )
    body = (
        '<div class="claire-tabs" id="admin-federation-tabs">'
        '<div class="claire-tablist-wrap"><div class="claire-tablist" role="tablist" focusgroup="tablist nomemory" aria-label="Federation">'
        f'<button type="button" class="claire-tab" role="tab" data-tab="sw" aria-selected="true" focusgroupstart><span data-icon="globe"></span> <span>{escape(t("console.admin.federation.tab_switches"))}</span></button>'
        f'<button type="button" class="claire-tab" role="tab" data-tab="known" aria-selected="false"><span data-icon="server"></span> <span>{escape(t("console.admin.federation.tab_known"))}</span></button>'
        f'<button type="button" class="claire-tab" role="tab" data-tab="blocks" aria-selected="false"><span data-icon="block"></span> <span>{escape(t("console.admin.federation.tab_blocks"))}</span></button>'
        f'<button type="button" class="claire-tab" role="tab" data-tab="journal" aria-selected="false"><span data-icon="list"></span> <span>{escape(t("console.admin.federation.tab_journal"))}</span></button>'
        "</div></div>"
        '<div class="claire-tabpanels">'
        f'<div role="tabpanel" data-tabpanel="sw"><div class="claire-panel-inner">{sw_panel}</div></div>'
        f'<div role="tabpanel" data-tabpanel="known" hidden="until-found"><div class="claire-panel-inner">{console_host("admin-federation-known-host")}</div></div>'
        f'<div role="tabpanel" data-tabpanel="blocks" hidden="until-found"><div class="claire-panel-inner">{blocks_panel}</div></div>'
        f'<div role="tabpanel" data-tabpanel="journal" hidden="until-found"><div class="claire-panel-inner">{console_host("admin-federation-journal-host")}</div></div>'
        "</div></div>"
    )
    return body, {"domain_blocks": [domain_block_view(block) for block in blocks]}
