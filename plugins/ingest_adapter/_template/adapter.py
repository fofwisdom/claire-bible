"""Ingest adapter __ID__.

Import only from ``claire.plugin_sdk``. Use the network only through ``ctx.http_client()`` or ``ctx.safe_get()``.
"""

from __future__ import annotations

from typing import Any

from claire.plugin_sdk import BaseWebAdapter, PluginContext, WebAdapterResult, extract_html

HOSTS = ("example.com", "*.example.com")

_ctx: PluginContext | None = None


def register(ctx: PluginContext) -> None:
    """Called once when the server administrator switches the plugin on."""
    global _ctx
    _ctx = ctx
    ctx.web_adapter(PageAdapter, name="__NAME__", priority=50, hosts=HOSTS)


class PageAdapter(BaseWebAdapter):
    @classmethod
    def name(cls) -> str:
        return "__NAME__"

    @classmethod
    def try_fetch(cls, url: str, **kwargs: Any) -> WebAdapterResult | None:
        """Return ``None`` to give the URL to the next step of the web chain."""
        assert _ctx is not None
        response = _ctx.safe_get(url)
        if response.status_code != 200:
            return None
        title, text, links, anchors, _error, images = extract_html(response.text, url)
        if not text:
            return None
        return WebAdapterResult(title=title, text=text, links=links, anchors=anchors, images=images)
