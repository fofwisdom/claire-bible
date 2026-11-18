"""The network client of the plugin: ``register(ctx)`` sets the context; each module asks here for a client."""

from __future__ import annotations

from typing import Any

from claire.plugin_sdk import PluginContext

_ctx: PluginContext | None = None


def set_context(ctx: PluginContext) -> None:
    global _ctx
    _ctx = ctx


def client(**kwargs: Any) -> Any:
    """``ctx.http_client()``: the private network guard and the hosts of ``permissions.network``."""
    if _ctx is None:
        raise RuntimeError("register(ctx) of claire.hoyolab was not called")
    return _ctx.http_client(**kwargs)
