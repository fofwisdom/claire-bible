"""Web metadata, favicon tags, UI chrome, and page bundle builders."""

from __future__ import annotations

from .chrome import render_appbar_html, render_cmdpalette_html, render_ingest_modals_html
from .favicon import (
    FAVICON_HEAD_TAGS,
    FAVICON_PLACEHOLDER,
    FAVICON_TAGS_HTML,
    get_scalar_favicon_config,
    inject_favicon_head_tags,
    render_favicon_head_tags,
)
from .metadata import (
    extract_knowledge_node_tags,
    render_ga_tag,
    render_open_graph_tags,
    resolve_preview_image,
    sanitize_preview_text,
)
from .pages import (
    GRAPH_HTML,
    SYNDICATION_HTML,
    _SHARED_HTML,
    localize_page,
    render_graph_page,
    render_shared_page,
    render_syndication_page,
)

__all__ = [
    "FAVICON_HEAD_TAGS",
    "FAVICON_PLACEHOLDER",
    "FAVICON_TAGS_HTML",
    "GRAPH_HTML",
    "SYNDICATION_HTML",
    "_SHARED_HTML",
    "extract_knowledge_node_tags",
    "get_scalar_favicon_config",
    "inject_favicon_head_tags",
    "localize_page",
    "render_appbar_html",
    "render_cmdpalette_html",
    "render_favicon_head_tags",
    "render_ga_tag",
    "render_graph_page",
    "render_ingest_modals_html",
    "render_open_graph_tags",
    "render_shared_page",
    "render_syndication_page",
    "resolve_preview_image",
    "sanitize_preview_text",
]
