"""Test helpers for plugins (INGEST_ADAPTER_PLUGIN_DESIGN.md, rules for code contribution, rule 2).

``load_plugin(folder)`` imports a plugin and calls ``register(ctx)`` without the records of a server. Tests never use the network: ``mock_network`` sends each request of the plugin to a handler.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, Callable

from ..plugin_host import ORIGIN_PLUGIN_FOLDER, ORIGIN_PROJECT, STATE_ENABLED
from ..plugin_host.context import PluginContext, RegisteredEntry
from ..plugin_host.discovery import PluginStatus
from ..plugin_host.files import folder_digest
from ..plugin_host.manifest import Manifest, read_manifest
from ..plugin_host import runtime as rt
from ..plugin_host.runtime import register_plugin


@dataclass
class LoadedTestPlugin:
    manifest: Manifest
    context: PluginContext
    package: str

    def module(self, name: str) -> ModuleType:
        """A module of the plugin, for example ``plugin.module("adapter")``."""
        full = f"{self.package}.{name}"
        if full not in sys.modules:
            __import__(full)
        return sys.modules[full]

    def entry(self, name: str) -> RegisteredEntry:
        for entry in self.context.entries:
            if entry.name == name:
                return entry
        raise KeyError(name)

    def install(self, monkeypatch: Any) -> None:
        """Use the entries of this loaded plugin in the registry of the core for the test (the router and the web chain). The entries of the same plugin id from the server runtime go out."""
        from ..ingest.registry import registry

        original = registry._plugin_entries_fn
        own = list(self.context.entries)
        plugin_id = self.manifest.id
        monkeypatch.setattr(registry, "_plugin_entries_fn", lambda: [e for e in original() if e.plugin_id != plugin_id] + own)

    def mock_network(self, monkeypatch: Any, handler: Callable[[Any], Any]) -> None:
        """Send each request of ``ctx.http_client()`` and ``ctx.safe_get()`` to ``handler`` (``httpx.MockTransport``). The host check of ``permissions.network`` stays; the private network check does not run."""
        import httpx

        from ..ingest.fetchers import http as http_module

        def _factory(**kwargs: Any) -> httpx.Client:
            for key in ("allow_private_networks", "private_network_allowlist", "transport"):
                kwargs.pop(key, None)
            return httpx.Client(transport=httpx.MockTransport(handler), **kwargs)

        monkeypatch.setattr(http_module, "guarded_client", _factory)
        monkeypatch.setattr(http_module, "check_url_safety", lambda *a, **k: None)


def load_plugin(folder: Path | str) -> LoadedTestPlugin:
    folder = Path(folder).resolve()
    manifest = read_manifest(folder)
    origin = ORIGIN_PROJECT if folder.is_relative_to(rt.project_plugin_folder().resolve()) else ORIGIN_PLUGIN_FOLDER
    status = PluginStatus(
        id=manifest.id,
        origin=origin,
        path=folder,
        plugin_type=manifest.type,
        manifest=manifest,
        digest=folder_digest(folder),
        state=STATE_ENABLED,
    )
    package, context = register_plugin(status, fresh=True)
    return LoadedTestPlugin(manifest=manifest, context=context, package=package)
