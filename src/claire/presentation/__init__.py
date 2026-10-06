"""Claire Bible Presentation Package.

Provides Asciidoctor reveal.js preprocessing, compilation, and AOT caching services.
"""

from __future__ import annotations

from .converter import compile_presentation_html, find_asciidoctor_executable
from .preprocessor import prepare_presentation_adoc_for_compile
from .service import PresentationService
from .views import render_unready_presentation_page

__all__ = [
    "PresentationService",
    "compile_presentation_html",
    "find_asciidoctor_executable",
    "prepare_presentation_adoc_for_compile",
    "render_unready_presentation_page",
]
