"""Operations on plugins for the console, the HTTP API, and the CLI (docs/implementation/INGESTION.md §2.2).

The HTTP routes and the CLI call these functions, so both paths have the same checks, store, and audit (§10.3).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import (
    ORIGIN_PROJECT,
    STATE_CHANGED,
    STATE_DISABLED,
    STATE_ENABLED,
    STATE_NEW,
    STATE_OVERLAP,
    STATE_REMOVED,
)
from .discovery import PluginStatus, overlap_hosts
from . import runtime as rt
from .runtime import Snapshot, read_process_reports, runtime

# States from which the administrator can switch a plugin on (§10.2: else 409 ``plugin_not_loadable``).
SWITCHABLE_ON = (STATE_NEW, STATE_DISABLED, STATE_CHANGED, STATE_OVERLAP, STATE_ENABLED)


class PluginServiceError(Exception):
    """A refused operation. ``code`` is the HTTP error code (§10.2), ``status`` the HTTP status."""

    def __init__(self, status: int, code: str, message: str, **detail: Any) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class SwitchResult:
    plugin_id: str
    enabled: bool
    replaced: tuple[str, ...]


def _settings(settings: Any) -> Any:
    if settings is None:
        from ..config import get_settings

        return get_settings()
    return settings


def snapshot(settings: Any = None, *, fresh: bool = True) -> Snapshot:
    if fresh:
        runtime().invalidate()
    return runtime().current(settings=settings)


def running_core_commit() -> str | None:
    """The commit of the running core, as the support bundle finds it (§10.1)."""
    from ..support_bundle import _get_git_commit_and_source

    commit, _source = _get_git_commit_and_source()
    return commit


def list_plugins(settings: Any = None, *, locale: str = "en") -> dict[str, Any]:
    """The list of §10.1: plugins with states, overlap groups, unknown folders, folders, and process reports."""
    s = _settings(settings)
    snap = snapshot(s)
    by_id = {status.id: status for status in snap.statuses}
    plugins = []
    for status in snap.statuses:
        item = status.to_dict(locale=locale)
        item["overlap"] = [
            {"id": other, "hosts": overlap_hosts(status.manifest, by_id[other].manifest), "enabled": bool(by_id[other].record and by_id[other].record.get("enabled"))}
            for other in status.overlap_group
            if other != status.id and status.manifest is not None and other in by_id and by_id[other].manifest is not None
        ]
        plugins.append(item)
    return {
        "core_commit": running_core_commit(),
        "project_plugin_folder": str(rt.project_plugin_folder()),
        "plugin_folder": str(rt.plugin_folder(s)),
        "initialized": snap.initialized,
        "unknown_folders": [str(p) for p in snap.unknown_folders],
        "plugins": plugins,
        "process_reports": read_process_reports(Path(s.data_dir)),
    }


def get_status(plugin_id: str, settings: Any = None) -> PluginStatus:
    status = snapshot(settings).status(plugin_id)
    if status is None:
        raise PluginServiceError(404, "not_found", f"plugin {plugin_id!r} is not found")
    return status


def get_agent_client(client_id: str, settings: Any = None, *, fresh: bool = False) -> PluginStatus | None:
    """Return the enabled ``agent_client`` plugin for ``client_id``, or ``None``."""
    return snapshot(settings, fresh=fresh).agent_client(client_id)


def _audit(settings: Any, *, actor: int | None, action: str, detail: dict[str, Any]) -> None:
    from ..store import auth_db
    from ..store import db as dbm

    conn = auth_db.connect_auth_db(auth_db.auth_db_path(settings.data_dir))
    try:
        auth_db.record_admin_audit(conn, actor_account_id=actor if actor is not None else dbm.ADMIN_ACCOUNT_ID, action=action, detail=detail)
    finally:
        conn.close()


def _audit_detail(status: PluginStatus, via: str) -> dict[str, Any]:
    m = status.manifest
    return {
        "plugin_id": status.id,
        "revision": m.revision if m else None,
        "core_commit": m.core_commit if m else None,
        "digest": status.digest,
        "origin": status.origin,
        "via": via,
    }


def switch_plugin(
    plugin_id: str,
    *,
    enabled: bool,
    digest: str | None = None,
    replace: list[str] | tuple[str, ...] | None = None,
    actor: int | None,
    via: str,
    settings: Any = None,
) -> SwitchResult:
    """Switch a plugin on (with the digest that the administrator saw) or off (§10.2). Raise ``PluginServiceError``."""
    from ..store import db as dbm
    from ..store import plugins as store

    s = _settings(settings)
    snap = snapshot(s)
    status = snap.status(plugin_id)
    if status is None:
        raise PluginServiceError(404, "not_found", f"plugin {plugin_id!r} is not found")
    replace_t = tuple(sorted(set(replace or ())))
    if enabled:
        if not digest:
            raise PluginServiceError(400, "digest_required", "the digest that the administrator saw is required to switch a plugin on")
        if status.state not in SWITCHABLE_ON:
            raise PluginServiceError(409, "plugin_not_loadable", f"plugin {plugin_id!r} cannot load: {status.state}", state=status.state, reason=status.reason)
        if digest != status.digest:
            raise PluginServiceError(409, "plugin_changed", "the files of the plugin changed after the page was read", digest=status.digest)
        by_id = {st.id: st for st in snap.statuses}
        enabled_others = tuple(
            sorted(other for other in status.overlap_group if other != plugin_id and by_id.get(other) is not None and by_id[other].record and by_id[other].record.get("enabled"))
        )
        if replace_t != enabled_others:
            hosts = {other: overlap_hosts(status.manifest, by_id[other].manifest) for other in enabled_others if status.manifest and by_id[other].manifest}
            raise PluginServiceError(409, "plugin_overlap", "replace must list exactly the enabled plugins of the overlap group", enabled=list(enabled_others), hosts=hosts)
    else:
        if replace_t:
            raise PluginServiceError(400, "replace_not_allowed", "replace is only for switching a plugin on")
        if status.state == STATE_REMOVED and not (status.record and status.record.get("enabled")):
            raise PluginServiceError(409, "plugin_not_loadable", "the record of a removed plugin can only be deleted", state=status.state)

    conn = dbm.connect_existing(rt.server_db_file(s))
    try:
        if store.read_records(conn) is None:
            raise PluginServiceError(409, "plugin_store_not_started", "the plugin store has not started on this server; run `claire migrate`")
        store.switch(
            conn,
            plugin_id,
            enabled=enabled,
            digest=status.digest if enabled else None,
            revision=status.manifest.revision if (enabled and status.manifest) else None,
            replace=replace_t,
            changed_by=actor,
        )
    finally:
        conn.close()
    by_id = {st.id: st for st in snap.statuses}
    for other in replace_t:
        detail = _audit_detail(by_id[other], via)
        detail["replaced_by"] = plugin_id
        _audit(s, actor=actor, action="plugin.disable", detail=detail)
    _audit(s, actor=actor, action="plugin.enable" if enabled else "plugin.disable", detail=_audit_detail(status, via))
    runtime().invalidate()
    return SwitchResult(plugin_id=plugin_id, enabled=enabled, replaced=replace_t)


def delete_record(plugin_id: str, *, actor: int | None, via: str, settings: Any = None) -> None:
    """Remove the record of a ``removed`` plugin (§10.2 DELETE)."""
    from ..store import db as dbm
    from ..store import plugins as store

    s = _settings(settings)
    status = get_status(plugin_id, s)
    if status.state != STATE_REMOVED:
        raise PluginServiceError(409, "plugin_folder_exists", "the folder of the plugin exists; only the record of a removed plugin can be deleted")
    conn = dbm.connect_existing(rt.server_db_file(s))
    try:
        store.delete_record(conn, plugin_id, changed_by=actor)
    finally:
        conn.close()
    _audit(s, actor=actor, action="plugin.delete_record", detail={"plugin_id": plugin_id, "via": via})
    runtime().invalidate()


def start_store(settings: Any = None) -> int | None:
    """The first start of the plugin store (§11 item 1), for ``claire migrate``. Return the count of enabled project plugins, or ``None`` when the store has started before or no project plugin exists."""
    from ..store import db as dbm
    from ..store import plugins as store
    from .discovery import discover

    s = _settings(settings)
    discovery = discover(rt.project_plugin_folder(), rt.plugin_folder(s))
    project_plugins = [
        (item.id, item.manifest.revision, item.digest)
        for item in discovery.plugins
        if item.origin == ORIGIN_PROJECT and item.problem is None and item.manifest is not None and item.digest is not None
    ]
    if not project_plugins:
        return None
    conn = dbm.connect_existing(rt.server_db_file(s))
    try:
        count = store.initialize(conn, project_plugins)
    finally:
        conn.close()
    runtime().invalidate()
    return count


__all__ = [
    "PluginServiceError",
    "STATE_ENABLED",
    "SwitchResult",
    "delete_record",
    "get_agent_client",
    "get_status",
    "list_plugins",
    "running_core_commit",
    "snapshot",
    "start_store",
    "switch_plugin",
]
