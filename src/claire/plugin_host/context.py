"""``PluginContext``: the object that ``register(ctx)`` gets (docs/implementation/INGESTION.md §2.2).

It records the registry entries and the commands of one plugin, and gives the network client of the plugin.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..ingest.hosts import host_matches_any, url_host
from .manifest import Manifest


@dataclass(frozen=True)
class RegisteredEntry:
    kind: str  # "fetcher" | "web_adapter"
    name: str
    priority: int
    hosts: tuple[str, ...]
    cls: type
    plugin_id: str | None

    def key(self) -> tuple[str, str, int, tuple[str, ...]]:
        return (self.kind, self.name, self.priority, tuple(sorted(self.hosts)))


@dataclass(frozen=True)
class PluginCommand:
    name: str
    build_parser: Callable[[Any], None]
    run: Callable[[Any], int]
    plugin_id: str


class PluginContext:
    """Given to ``register(ctx)``. A plugin keeps it to use its network client and its logger."""

    def __init__(self, manifest: Manifest, folder: Path, origin: str) -> None:
        self.plugin_id = manifest.id
        self.folder = folder
        self.origin = origin
        self.manifest = manifest
        self.logger = logging.LoggerAdapter(logging.getLogger(f"claire.plugin.{manifest.id}"), {"plugin_id": manifest.id})
        self.entries: list[RegisteredEntry] = []
        self.commands: dict[str, PluginCommand] = {}

    # --- registration ----------------------------------------------------------------

    def _add(self, kind: str, cls: type, name: str, priority: int, hosts: tuple[str, ...] | list[str]) -> None:
        hosts_t = tuple(hosts)
        if not hosts_t:
            raise ValueError(f"{self.plugin_id}: entry {name!r} needs hosts")
        if any(e.kind == kind and e.name == name for e in self.entries):
            raise ValueError(f"{self.plugin_id}: {kind} {name!r} is registered two times")
        self.entries.append(RegisteredEntry(kind=kind, name=name, priority=int(priority), hosts=hosts_t, cls=cls, plugin_id=self.plugin_id))

    def fetcher(self, cls: type, *, name: str, priority: int, hosts: tuple[str, ...] | list[str]) -> None:
        """Register a ``BaseFetcher`` subclass. It must agree with an item of ``[[provides]]`` (§6.1 rule 5)."""
        self._add("fetcher", cls, name, priority, hosts)

    def web_adapter(self, cls: type, *, name: str, priority: int, hosts: tuple[str, ...] | list[str]) -> None:
        """Register a ``BaseWebAdapter`` subclass. ``hosts=("*",)`` makes a general adapter that runs after the static fetch."""
        self._add("web_adapter", cls, name, priority, hosts)

    def cli_command(self, name: str, build_parser: Callable[[Any], None], run: Callable[[Any], int]) -> None:
        """A command for ``claire plugin run <id> <name>`` (§7.4). ``build_parser(parser)`` adds the arguments."""
        if name in self.commands:
            raise ValueError(f"{self.plugin_id}: command {name!r} is registered two times")
        self.commands[name] = PluginCommand(name=name, build_parser=build_parser, run=run, plugin_id=self.plugin_id)

    # --- network ------------------------------------------------------------------

    def _host_check(self) -> Callable[[Any], None]:
        allowed = self.manifest.permissions.network
        plugin_id = self.plugin_id

        def _check(request: Any) -> None:
            from ..ingest.fetchers.base import FetchError

            host = url_host(str(request.url))
            if not host_matches_any(host, allowed):
                raise FetchError(f"plugin {plugin_id}: the host {host!r} is not in permissions.network")

        return _check

    def _private_policy(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        if not self.manifest.permissions.private_network:
            kwargs["allow_private_networks"] = False
            kwargs["private_network_allowlist"] = []
        return kwargs

    def http_client(self, **kwargs: Any) -> Any:
        """An ``httpx.Client`` with the private network guard and the host list of ``permissions.network`` on each request and each redirect hop."""
        from ..ingest.fetchers.http import guarded_client

        hooks = dict(kwargs.pop("event_hooks", None) or {})
        hooks["request"] = [self._host_check(), *hooks.get("request", [])]
        return guarded_client(event_hooks=hooks, **self._private_policy(kwargs))

    def safe_get(self, url: str, *, timeout: float = 30.0, max_size: int = 10 * 1024 * 1024, **kwargs: Any) -> Any:
        """``SafeHttpClient.get`` (size limit, media response check) with the host check of the plugin."""
        from ..ingest.fetchers.http import SafeHttpClient

        policy = self._private_policy({})
        client = SafeHttpClient(timeout=timeout, max_size=max_size, **policy)
        hooks = dict(kwargs.pop("event_hooks", None) or {})
        hooks["request"] = [self._host_check(), *hooks.get("request", [])]
        self._host_check()(type("R", (), {"url": url})())
        return client.get(url, event_hooks=hooks, **kwargs)
