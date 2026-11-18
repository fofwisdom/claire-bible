"""``reroute(url)``: route a resolved URL again (INGEST_ADAPTER_PLUGIN_DESIGN.md §7.1, Routing)."""

from __future__ import annotations

from contextvars import ContextVar

from ..ingest.fetchers.base import FetchError
from ..ontology.base import Document

# The depth of the router of the job (the same limit as the present redirect route: at most two levels).
MAX_REROUTE_DEPTH = 2
_DEPTH: ContextVar[int] = ContextVar("claire_reroute_depth", default=0)


def reroute(url: str, *, full_content: bool = False) -> Document:
    """Route ``url`` through the router again: the core routes and the enabled plugins. Raise ``FetchError`` after two levels."""
    depth = _DEPTH.get()
    if depth >= MAX_REROUTE_DEPTH:
        raise FetchError("too many redirects")
    from ..ingest.router import fetch

    token = _DEPTH.set(depth + 1)
    try:
        return fetch(url, full_content=full_content, _depth=depth + 1)
    finally:
        _DEPTH.reset(token)
