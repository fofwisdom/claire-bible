"""Claire Bible Presentation Package.

Provides Asciidoctor reveal.js preprocessing, compilation, and AOT caching services.
"""

from __future__ import annotations

from .converter import compile_presentation_html, find_asciidoctor_executable
from .favicon import (
    FAVICON_TAGS_HTML,
    get_scalar_favicon_config,
    inject_favicon_head_tags,
    render_favicon_head_tags,
)
from .preprocessor import prepare_presentation_adoc_for_compile
from .service import PresentationService
from .views import render_unready_presentation_page

__all__ = [
    "FAVICON_TAGS_HTML",
    "PresentationService",
    "compile_presentation_html",
    "find_asciidoctor_executable",
    "get_scalar_favicon_config",
    "inject_favicon_head_tags",
    "prepare_presentation_adoc_for_compile",
    "render_favicon_head_tags",
    "render_unready_presentation_page",
]
