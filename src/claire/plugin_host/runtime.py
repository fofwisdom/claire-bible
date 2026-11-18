"""Plugins in one process (INGEST_ADAPTER_PLUGIN_DESIGN.md §8.3, §8.4).

``current()`` gives a snapshot: the state of each plugin and the registry entries of the enabled plugins. A snapshot is at most ``REFRESH_SECONDS`` old; a switch in this process makes the next call read again. A failure of one plugin changes only the state of that plugin and never stops the process.
"""

from __future__ import annotations

import importlib.util
import json
import logging
import os
import sqlite3
import sys
import threading
import time
import traceback
import types
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import ORIGIN_PROJECT, STATE_ENABLED, STATE_FAILED, STATE_INVALID
from .context import PluginCommand, PluginContext, RegisteredEntry
from .discovery import PluginStatus, compute_states, discover
from .manifest import python_name

log = logging.getLogger(__name__)

REFRESH_SECONDS = 1.0
PACKAGE_ROOT = "claire_plugins"


def project_plugin_folder() -> Path:
    """``<app root>/plugins`` (§5)."""
    from ..config import ROOT

    return Path(ROOT) / "plugins"


def plugin_folder(settings: Any = None) -> Path:
    """``CLAIRE_PLUGINS_DIR`` (§5)."""
    if settings is None:
        from ..config import get_settings

        settings = get_settings()
    return Path(settings.plugins_dir)


def server_db_file(settings: Any = None) -> Path:
    if settings is None:
        from ..config import get_settings

        settings = get_settings()
    return Path(settings.db_file)


@dataclass
class LoadedPlugin:
    id: str
    digest: str
    revision: int
    module_name: str
    context: PluginContext
    loaded_at: float


@dataclass
class Snapshot:
    statuses: list[PluginStatus]
    entries: list[RegisteredEntry]
    commands: dict[str, dict[str, PluginCommand]]
    unknown_folders: list[Path]
    initialized: bool
    restart_needed: list[str] = field(default_factory=list)

    def status(self, plugin_id: str) -> PluginStatus | None:
        for status in self.statuses:
            if status.id == plugin_id:
                return status
        return None


def _ensure_namespace(name: str) -> None:
    if name in sys.modules:
        return
    module = types.ModuleType(name)
    module.__path__ = []  # a namespace for the packages of plugins
    sys.modules[name] = module


def import_plugin(status: PluginStatus) -> tuple[str, Any]:
    """Import the folder of the plugin as ``claire_plugins.<type>.<Python name>`` and return the entry function. No change to ``sys.path``."""
    manifest = status.manifest
    assert manifest is not None and status.path is not None
    type_package = f"{PACKAGE_ROOT}.{manifest.type}"
    _ensure_namespace(PACKAGE_ROOT)
    _ensure_namespace(type_package)
    package_name = f"{type_package}.{python_name(manifest.id)}"
    for name in [n for n in sys.modules if n == package_name or n.startswith(package_name + ".")]:
        del sys.modules[name]
    init = status.path / "__init__.py"
    spec = importlib.util.spec_from_file_location(package_name, init, submodule_search_locations=[str(status.path)])
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot make an import spec for {status.path}")
    package = importlib.util.module_from_spec(spec)
    sys.modules[package_name] = package
    spec.loader.exec_module(package)
    module = importlib.import_module(f"{package_name}.{manifest.entry_module}")
    function = getattr(module, manifest.entry_function, None)
    if not callable(function):
        raise ImportError(f"entry {manifest.entry!r} is not a function")
    return package_name, function


def register_plugin(status: PluginStatus) -> tuple[str, PluginContext]:
    """Import and call ``register(ctx)``. Raise ``PluginRegistrationMismatch`` when the entries differ from ``provides``."""
    manifest = status.manifest
    assert manifest is not None and status.path is not None and status.origin is not None
    module_name, function = import_plugin(status)
    ctx = PluginContext(manifest, status.path, status.origin)
    function(ctx)
    declared = sorted(p.key() for p in manifest.provides)
    registered = sorted(e.key() for e in ctx.entries)
    if declared != registered:
        raise PluginRegistrationMismatch(f"the entries of register(ctx) {registered} differ from [[provides]] {declared}")
    return module_name, ctx


class PluginRegistrationMismatch(Exception):
    pass


class PluginRuntime:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._snapshot: Snapshot | None = None
        self._snapshot_key: tuple[str, str, str] | None = None
        self._taken_at = 0.0
        self._loaded: dict[str, LoadedPlugin] = {}
        self._failed: dict[str, tuple[str, str]] = {}  # id -> (digest, error text)
        self._invalid: dict[str, tuple[str, str]] = {}  # id -> (digest, reason) found at load
        self._errors: dict[str, int] = {}  # "<plugin id>/<entry>" -> count (§7.3 item 4)
        self._process: str | None = None
        self._last_report: str | None = None

    # --- process -----------------------------------------------------------------------

    def set_process_name(self, name: str) -> None:
        """A long-running process (``api``, ``bot``, a worker loop) writes ``data/run/plugins_<name>.json``."""
        self._process = name

    def invalidate(self) -> None:
        with self._lock:
            self._taken_at = 0.0

    def count_error(self, plugin_id: str | None, entry: str) -> None:
        key = f"{plugin_id or 'core'}/{entry}"
        with self._lock:
            self._errors[key] = self._errors.get(key, 0) + 1

    # --- snapshot ----------------------------------------------------------------------

    def current(self, *, settings: Any = None) -> Snapshot:
        with self._lock:
            project = project_plugin_folder()
            folder = plugin_folder(settings)
            db_file = server_db_file(settings)
            key = (str(project), str(folder), str(db_file))
            if self._snapshot is not None and self._snapshot_key == key and time.monotonic() - self._taken_at < REFRESH_SECONDS:
                return self._snapshot
            snapshot = self._refresh(project, folder, db_file)
            self._snapshot, self._snapshot_key, self._taken_at = snapshot, key, time.monotonic()
            return snapshot

    def _read_records(self, db_file: Path, discovery: Any) -> tuple[Any, bool]:
        from ..store import plugins as store

        if not db_file.is_file():
            return None, False
        try:
            records = store.read_records_file(db_file)
            if records is not None:
                return records, True
            from ..store.db import connect_existing

            conn = connect_existing(db_file)
            try:
                project_plugins = [
                    (item.id, item.manifest.revision, item.digest)
                    for item in discovery.plugins
                    if item.origin == ORIGIN_PROJECT and item.problem is None and item.manifest is not None and item.digest is not None
                ]
                if not project_plugins:
                    # A core without project plugins does not start the store: the first release with project plugins must still enable them (O7).
                    return None, False
                count = store.initialize(conn, project_plugins)
                if count is not None:
                    log.info("plugin store started on this server: %d project plugins enabled", count)
                return store.read_records(conn), True
            finally:
                conn.close()
        except sqlite3.OperationalError as exc:
            log.warning("plugin records cannot be read from %s: %s", db_file, exc)
            return None, False

    def _refresh(self, project: Path, folder: Path, db_file: Path) -> Snapshot:
        discovery = discover(project, folder)
        records, initialized = self._read_records(db_file, discovery)
        statuses = compute_states(discovery, records)
        entries: list[RegisteredEntry] = []
        commands: dict[str, dict[str, PluginCommand]] = {}
        restart_needed: list[str] = []
        for status in statuses:
            if status.state != STATE_ENABLED or status.digest is None or status.manifest is None:
                continue
            failed = self._failed.get(status.id)
            if failed and failed[0] == status.digest:
                status.state, status.reason = STATE_FAILED, failed[1]
                continue
            invalid = self._invalid.get(status.id)
            if invalid and invalid[0] == status.digest:
                status.state, status.reason = STATE_INVALID, invalid[1]
                continue
            loaded = self._loaded.get(status.id)
            if loaded is None:
                try:
                    module_name, ctx = register_plugin(status)
                except PluginRegistrationMismatch as exc:
                    self._invalid[status.id] = (status.digest, str(exc))
                    status.state, status.reason = STATE_INVALID, str(exc)
                    log.warning("plugin %s is invalid: %s", status.id, exc)
                    continue
                except BaseException as exc:  # noqa: BLE001 - a plugin must not stop the process
                    if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                        raise
                    text = "".join(traceback.format_exception_only(type(exc), exc)).strip()
                    self._failed[status.id] = (status.digest, text)
                    status.state, status.reason = STATE_FAILED, text
                    log.warning("plugin %s failed to load: %s", status.id, text)
                    continue
                loaded = LoadedPlugin(status.id, status.digest, status.manifest.revision, module_name, ctx, time.time())
                self._loaded[status.id] = loaded
                log.info("plugin %s r%d loaded", status.id, status.manifest.revision)
            elif loaded.digest != status.digest:
                restart_needed.append(status.id)
            entries.extend(loaded.context.entries)
            if loaded.context.commands:
                commands[status.id] = dict(loaded.context.commands)
        snapshot = Snapshot(statuses=statuses, entries=entries, commands=commands, unknown_folders=list(discovery.unknown_folders), initialized=initialized, restart_needed=restart_needed)
        self._write_report(snapshot)
        return snapshot

    # --- process report (§8.3 item 4) ---------------------------------------------------

    def report(self, snapshot: Snapshot) -> dict[str, Any]:
        loaded = {
            plugin_id: {"digest": lp.digest, "revision": lp.revision, "loaded_at": lp.loaded_at}
            for plugin_id, lp in sorted(self._loaded.items())
        }
        return {
            "process": self._process,
            "pid": os.getpid(),
            "plugins": [
                {
                    "id": s.id,
                    "state": s.state,
                    "revision": s.manifest.revision if s.manifest else None,
                    "core_commit": s.manifest.core_commit if s.manifest else None,
                    "digest": s.digest,
                    "reason": s.reason,
                }
                for s in snapshot.statuses
            ],
            "loaded": loaded,
            "restart_needed": snapshot.restart_needed,
            "errors": dict(sorted(self._errors.items())),
        }

    def _write_report(self, snapshot: Snapshot) -> None:
        if not self._process:
            return
        report = self.report(snapshot)
        text = json.dumps({k: v for k, v in report.items() if k != "pid"}, sort_keys=True)
        if text == self._last_report:
            return
        try:
            from ..config import get_settings

            run_dir = Path(get_settings().data_dir) / "run"
            run_dir.mkdir(parents=True, exist_ok=True)
            report["written_at"] = time.time()
            target = run_dir / f"plugins_{self._process}.json"
            tmp = target.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
            os.replace(tmp, target)
            self._last_report = text
        except OSError as exc:
            log.warning("plugin process report cannot be written: %s", exc)


_RUNTIME = PluginRuntime()


def runtime() -> PluginRuntime:
    return _RUNTIME


def read_process_reports(data_dir: Path) -> dict[str, dict[str, Any]]:
    """The reports of the long-running processes, by process name."""
    reports: dict[str, dict[str, Any]] = {}
    run_dir = Path(data_dir) / "run"
    if not run_dir.is_dir():
        return reports
    for path in sorted(run_dir.glob("plugins_*.json")):
        try:
            reports[path.stem.removeprefix("plugins_")] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
    return reports
