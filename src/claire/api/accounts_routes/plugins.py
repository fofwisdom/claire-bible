"""Plugins of the server: the console section ``/admin/plugins`` and ``/api/admin/plugins`` (docs/implementation/INGESTION.md §2.2).

- ``GET /api/admin/plugins``: the list of the console as JSON.
- ``PUT /api/admin/plugins/{id}``: ``{"enabled": true, "digest": "sha256:…", "replace": [...]}`` or ``{"enabled": false}``. Switching on needs a recent authentication (S2); switching off is the emergency stop and needs only the administrator session.
- ``DELETE /api/admin/plugins/{id}``: removes the record of a ``removed`` plugin.

All routes are for the administrator; other callers get 404 (``api.security.ROUTE_POLICY``). The checks, the store, and the audit are in ``plugin_host.service`` (the same as ``claire plugin``).
"""

from __future__ import annotations

import asyncio
import json
import os
from html import escape
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from ...i18n import get_locale, t
from ...plugin_host import STATES
from ...plugin_host.service import SWITCHABLE_ON, PluginServiceError, delete_record, list_plugins, switch_plugin
from ..principal import request_principal
from ..security import read_json_body
from .admin import is_admin
from .common import AccountsContext, json_error, not_found, require_recent_auth, same_origin
from .console_ui import console_actions, console_card, console_notice, console_result

ADMIN_PLUGINS_API_PATH = "/api/admin/plugins"
ADMIN_PLUGIN_API_PATH = "/api/admin/plugins/{id}"

_NO_STORE = {"Cache-Control": "no-store"}
_PUT_KEYS = {"enabled", "digest", "replace"}


def _settings(ctx: AccountsContext) -> Any:
    current = ctx.current_settings()
    if current is not None:
        return current
    from ...core.config import get_settings

    return get_settings()


def _state_class(state: str) -> str:
    if state == "enabled":
        return "badge-enabled"
    if state in ("invalid", "unlocked", "incompatible", "missing_requirement", "changed", "overlap", "failed", "removed"):
        return "badge-disabled"
    return ""


def _version(item: dict[str, Any]) -> str:
    if item.get("revision") is None:
        return "-"
    return f"r{item['revision']} @ {(item.get('core_commit') or '')[:8]}"


def _list_html(values: list[str]) -> str:
    if not values:
        return f'<span class="hint">{escape(t("console.admin.plugins.none"))}</span>'
    return ", ".join(f"<code>{escape(v)}</code>" for v in values)


def _row_html(item: dict[str, Any], reports: dict[str, Any]) -> str:
    plugin_id = item["id"]
    state = item["state"]
    record = item.get("record") or {}
    record_on = bool(record.get("enabled"))
    can_switch = record_on or state in SWITCHABLE_ON
    name = item.get("name") or plugin_id
    reason_id = f"plugin-reason-{plugin_id.replace('.', '-')}"
    provides = item.get("provides") or []
    permissions = item.get("permissions") or {}
    requires = item.get("requires") or {}
    any_host = any("*" in p.get("hosts", []) for p in provides) or "*" in (permissions.get("network") or [])
    badges = []
    if any_host:
        badges.append(f'<span class="badge pref-pill-warn" data-plugin-badge="any-host">{escape(t("console.admin.plugins.badge_any_host"))}</span>')
    if permissions.get("network_modules"):
        badges.append(f'<span class="badge pref-pill-warn" data-plugin-badge="direct-network">{escape(t("console.admin.plugins.badge_direct_network"))}</span>')
    overlap_enabled = [o["id"] for o in item.get("overlap") or [] if o.get("enabled")]
    overlap_html = "".join(
        f'<li>{escape(t("console.admin.plugins.overlap", id=o["id"], hosts=", ".join(o.get("hosts") or [])))}</li>' for o in item.get("overlap") or []
    )
    provides_html = "".join(
        f"<li><code>{escape(p['kind'])}</code> <code>{escape(p['name'])}</code> ({escape(str(p['priority']))}): {_list_html(p.get('hosts') or [])}</li>" for p in provides
    )
    process_lines = []
    for process, report in sorted(reports.items()):
        for entry in report.get("plugins") or []:
            if entry.get("id") == plugin_id:
                restart = plugin_id in (report.get("restart_needed") or [])
                text = f"{process}: {entry.get('state')}" + (f" · {t('console.admin.plugins.restart_needed_short')}" if restart else "")
                process_lines.append(f"<li>{escape(text)}</li>")
    reason = item.get("reason")
    dialog_body = (
        f'<p>{escape(t("console.admin.plugins.dialog.intro"))}</p>'
        f'<dl class="plugin-dialog-facts">'
        f'<dt>{escape(t("console.admin.plugins.network"))}</dt><dd>{_list_html(permissions.get("network") or [])}</dd>'
        f'<dt>{escape(t("console.admin.plugins.network_modules"))}</dt><dd>{_list_html(permissions.get("network_modules") or [])}</dd>'
        f'<dt>{escape(t("console.admin.plugins.executables"))}</dt><dd>{_list_html(permissions.get("executables") or [])}</dd>'
        f'<dt>{escape(t("console.admin.plugins.providers"))}</dt><dd>{_list_html(permissions.get("providers") or [])}</dd>'
        f'<dt>{escape(t("console.admin.plugins.provides"))}</dt><dd><ul>{provides_html}</ul></dd>'
        f'<dt>{escape(t("console.admin.plugins.digest"))}</dt><dd><code>{escape(item.get("digest") or "-")}</code></dd>'
        "</dl>"
        + (f'<p class="plugin-dialog-replace" data-plugin-replace="{escape(json.dumps(overlap_enabled))}">{escape(t("console.admin.plugins.dialog.replace", ids=", ".join(overlap_enabled)))}</p>' if overlap_enabled else "")
    )
    confirm_text = (
        t("console.admin.plugins.dialog.confirm_replace", others=", ".join(overlap_enabled), name=name)
        if overlap_enabled
        else t("console.admin.plugins.dialog.confirm")
    )
    return (
        f'<tr data-plugin-id="{escape(plugin_id)}" data-plugin-state="{escape(state)}">'
        f'<td data-label="{escape(t("console.admin.plugins.col_name"))}"><strong>{escape(name)}</strong><br/><code class="pref-mono">{escape(plugin_id)}</code> {"".join(badges)}</td>'
        f'<td data-label="{escape(t("console.admin.plugins.col_version"))}"><code class="pref-mono" title="{escape(item.get("core_commit") or "")}">{escape(_version(item))}</code></td>'
        f'<td data-label="{escape(t("console.admin.plugins.col_origin"))}">{escape(t("console.admin.plugins.origin." + (item.get("origin") or "none")))}</td>'
        f'<td data-label="{escape(t("console.admin.plugins.col_maturity"))}">{escape(t("console.admin.plugins.maturity." + item["maturity"]) if item.get("maturity") else "-")}</td>'
        f'<td data-label="{escape(t("console.admin.plugins.col_state"))}"><span class="badge {_state_class(state)}" data-plugin-state-badge="{escape(state)}">{escape(t("console.admin.plugins.state." + state))}</span>'
        + (f'<br/><span class="hint" id="{reason_id}">{escape(reason)}</span>' if reason else "")
        + "</td>"
        f'<td data-label="{escape(t("console.admin.plugins.col_switch"))}">'
        f'<input type="checkbox" switch data-plugin-switch="{escape(plugin_id)}" data-plugin-digest="{escape(item.get("digest") or "")}"'
        f' data-plugin-name="{escape(name)}" data-plugin-confirm="{escape(confirm_text)}"'
        f' aria-label="{escape(t("console.admin.plugins.switch_label", name=name))}"'
        + (" checked" if record_on else "")
        + ("" if can_switch else f' disabled aria-describedby="{reason_id}"')
        + "/>"
        f'<template data-plugin-dialog-body="{escape(plugin_id)}">{dialog_body}</template>'
        "</td>"
        "</tr>"
        f'<tr class="plugin-details-row" data-plugin-details="{escape(plugin_id)}"><td colspan="6"><details><summary>{escape(t("console.admin.plugins.details"))}</summary>'
        f'<dl class="plugin-details">'
        f'<dt>{escape(t("console.admin.plugins.description"))}</dt><dd>{escape(item.get("description") or "-")}</dd>'
        f'<dt>{escape(t("console.admin.plugins.provides"))}</dt><dd><ul>{provides_html}</ul></dd>'
        f'<dt>{escape(t("console.admin.plugins.network"))}</dt><dd>{_list_html(permissions.get("network") or [])}</dd>'
        f'<dt>{escape(t("console.admin.plugins.private_network"))}</dt><dd>{escape(t("console.admin.plugins.yes") if permissions.get("private_network") else t("console.admin.plugins.no"))}</dd>'
        f'<dt>{escape(t("console.admin.plugins.network_modules"))}</dt><dd>{_list_html(permissions.get("network_modules") or [])}</dd>'
        f'<dt>{escape(t("console.admin.plugins.executables"))}</dt><dd>{_list_html(permissions.get("executables") or [])}</dd>'
        f'<dt>{escape(t("console.admin.plugins.providers"))}</dt><dd>{_list_html(permissions.get("providers") or [])}</dd>'
        f'<dt>{escape(t("console.admin.plugins.distributions"))}</dt><dd>{_list_html(requires.get("distributions") or [])}</dd>'
        f'<dt>{escape(t("console.admin.plugins.core_commit_full"))}</dt><dd><code>{escape(item.get("core_commit") or "-")}</code></dd>'
        f'<dt>{escape(t("console.admin.plugins.digest"))}</dt><dd><code>{escape(item.get("digest") or "-")}</code></dd>'
        f'<dt>{escape(t("console.admin.plugins.approved_digest"))}</dt><dd><code>{escape(record.get("approved_digest") or "-")}</code></dd>'
        f'<dt>{escape(t("console.admin.plugins.license"))}</dt><dd>{escape(item.get("license") or "-")}</dd>'
        f'<dt>{escape(t("console.admin.plugins.maintainers"))}</dt><dd>{_list_html(item.get("maintainers") or [])}</dd>'
        + (f'<dt>{escape(t("console.admin.plugins.overlap_title"))}</dt><dd><ul>{overlap_html}</ul></dd>' if overlap_html else "")
        + f'<dt>{escape(t("console.admin.plugins.reports"))}</dt><dd><ul>{"".join(process_lines) or escape(t("console.admin.plugins.none"))}</ul></dd>'
        "</dl></details></td></tr>"
    )


def admin_plugins_body(ctx: AccountsContext) -> tuple[str, dict[str, Any]]:
    """The body of ``/admin/plugins``: folders, warnings, the table, and one dialog (§10.1)."""
    data = list_plugins(_settings(ctx), locale=get_locale())
    reports = data.get("process_reports") or {}
    notices: list[str] = []
    if not data.get("initialized"):
        notices.append(t("console.admin.plugins.store_not_started"))
    for folder in data.get("unknown_folders") or []:
        notices.append(t("console.admin.plugins.unknown_folder", path=folder))
    restart = sorted({process for process, report in reports.items() if report.get("restart_needed")})
    if restart:
        notices.append(t("console.admin.plugins.restart_needed", processes=", ".join(restart)))
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        notices.append(t("console.admin.plugins.root_warning"))
    rows = "".join(_row_html(item, reports) for item in data["plugins"])
    if not rows:
        rows = f'<tr><td colspan="6" class="hint">{escape(t("console.admin.plugins.empty"))}</td></tr>'
    messages = {
        "failed": t("console.admin.plugins.msg.failed"),
        "plugin_changed": t("console.admin.plugins.msg.plugin_changed"),
        "plugin_overlap": t("console.admin.plugins.msg.plugin_overlap"),
        "plugin_not_loadable": t("console.admin.plugins.msg.plugin_not_loadable"),
        "step_up_requires_session": t("console.admin.plugins.msg.step_up_requires_session"),
        "reauth": t("console.admin.plugins.msg.reauth"),
        "network": t("console.admin.plugins.msg.network"),
    }
    msg_attrs = {f"data-msg-{k.replace('_', '-')}": v for k, v in messages.items()}
    notices_html = "".join(console_notice(escape(n), tone="warn", attrs={"data-plugin-notice": True}) for n in notices)
    folders_card = console_card(
        escape(t("console.admin.plugins.folder_title")),
        f'<p>{escape(t("console.admin.plugins.folder_text", container=data["plugin_folder"], host="CB_PLUGINS_DIR"))}</p>'
        f'<p class="hint">{escape(t("console.admin.plugins.project_text", path=data["project_plugin_folder"]))}</p>'
        f'<p class="hint">{escape(t("console.admin.plugins.core_commit", commit=data.get("core_commit") or "-"))}</p>'
        + notices_html,
        id="admin-plugins-folders",
        icon="folder",
        aria_label=t("console.admin.plugins.folder_title"),
    )
    plugins_table = (
        '<div class="pref-table-wrap">'
        '<table class="pref-table claire-table" id="admin-plugins-table">'
        f'<caption>{escape(t("console.admin.plugins.caption"))}</caption>'
        f'<thead><tr><th scope="col">{escape(t("console.admin.plugins.col_name"))}</th><th scope="col">{escape(t("console.admin.plugins.col_version"))}</th>'
        f'<th scope="col">{escape(t("console.admin.plugins.col_origin"))}</th><th scope="col">{escape(t("console.admin.plugins.col_maturity"))}</th>'
        f'<th scope="col">{escape(t("console.admin.plugins.col_state"))}</th><th scope="col">{escape(t("console.admin.plugins.col_switch"))}</th></tr></thead>'
        f"<tbody>{rows}</tbody>"
        "</table></div>"
    )
    plugins_card = console_card(
        escape(t("console.admin.plugins.label")),
        plugins_table + console_result(id="admin-plugins-result"),
        id="admin-plugins-card",
        icon="puzzle",
        aria_label=t("console.admin.plugins.label"),
        attrs=msg_attrs,
    )
    dialog_actions = console_actions(
        f'<button type="submit" value="cancel" class="pref-btn" autofocus>{escape(t("console.admin.plugins.dialog.cancel"))}</button>'
        '<button type="submit" value="confirm" class="pref-btn pref-btn-accent" id="admin-plugin-dialog-confirm"></button>',
        extra_class="console-actions-end",
    )
    dialog_html = (
        f'<dialog id="admin-plugin-dialog" aria-labelledby="admin-plugin-dialog-title" data-title-template="{escape(t("console.admin.plugins.dialog.title", name="{name}"))}" style="max-width:40rem;border:1px solid var(--border);border-radius:0.75rem;padding:1rem;background:var(--bg);color:var(--fg)">'
        '<form method="dialog" style="display:flex;flex-direction:column;gap:10px">'
        '<h2 id="admin-plugin-dialog-title" style="margin:0;font-size:15px"></h2>'
        '<div id="admin-plugin-dialog-body"></div>'
        f"{dialog_actions}"
        "</form></dialog>"
    )
    return folders_card + plugins_card + dialog_html, {"title_template": t("console.admin.plugins.dialog.title", name="{name}")}


def _validate_put(body: Any) -> tuple[bool, str | None, list[str]]:
    if not isinstance(body, dict) or set(body) - _PUT_KEYS or "enabled" not in body:
        raise ValueError("body must be {enabled, digest?, replace?}")
    enabled = body["enabled"]
    if not isinstance(enabled, bool):
        raise ValueError("enabled must be true or false")
    digest = body.get("digest")
    if digest is not None and not isinstance(digest, str):
        raise ValueError("digest must be a text")
    replace = body.get("replace", [])
    if not isinstance(replace, list) or not all(isinstance(x, str) for x in replace):
        raise ValueError("replace must be a list of plugin ids")
    return enabled, digest, replace


def routes(ctx: AccountsContext) -> list[Route]:
    async def plugins_get(request: Request) -> Response:
        if not is_admin(request_principal(request)):
            return not_found()
        data = await asyncio.to_thread(list_plugins, _settings(ctx), locale=get_locale())
        return JSONResponse(data, headers=_NO_STORE)

    async def plugin_put(request: Request) -> Response:
        principal = request_principal(request)
        if not is_admin(principal):
            return not_found()
        if not same_origin(request):
            return json_error(403, "forbidden")
        body = await read_json_body(request)
        try:
            enabled, digest, replace = _validate_put(body)
        except ValueError as exc:
            return json_error(400, "invalid_request", detail=str(exc))
        if enabled:
            refused = require_recent_auth(principal)
            if refused is not None:
                return refused
        plugin_id = request.path_params["id"]
        try:
            result = await asyncio.to_thread(
                switch_plugin, plugin_id, enabled=enabled, digest=digest, replace=replace, actor=principal.account_id, via="console", settings=_settings(ctx)
            )
        except PluginServiceError as exc:
            return json_error(exc.status, exc.code, detail=str(exc), **exc.detail)
        return JSONResponse({"id": result.plugin_id, "enabled": result.enabled, "replaced": list(result.replaced)}, headers=_NO_STORE)

    async def plugin_delete(request: Request) -> Response:
        principal = request_principal(request)
        if not is_admin(principal):
            return not_found()
        if not same_origin(request):
            return json_error(403, "forbidden")
        plugin_id = request.path_params["id"]
        try:
            await asyncio.to_thread(delete_record, plugin_id, actor=principal.account_id, via="console", settings=_settings(ctx))
        except PluginServiceError as exc:
            return json_error(exc.status, exc.code, detail=str(exc), **exc.detail)
        return JSONResponse({"id": plugin_id, "deleted": True}, headers=_NO_STORE)

    return [
        Route(ADMIN_PLUGINS_API_PATH, plugins_get, methods=["GET"]),
        Route(ADMIN_PLUGIN_API_PATH, plugin_put, methods=["PUT"]),
        Route(ADMIN_PLUGIN_API_PATH, plugin_delete, methods=["DELETE"]),
    ]


__all__ = ["ADMIN_PLUGINS_API_PATH", "ADMIN_PLUGIN_API_PATH", "STATES", "admin_plugins_body", "routes"]
