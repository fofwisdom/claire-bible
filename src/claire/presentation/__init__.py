"""Claire Bible Presentation Package.

Provides Asciidoctor reveal.js preprocessing, compilation, and AOT caching services.
"""

from __future__ import annotations

from .converter import compile_presentation_html, find_asciidoctor_executable
from .preprocessor import preprocess_adoc_to_slides
from .service import PresentationService
from .views import render_unready_presentation_page

__all__ = [
    "PresentationService",
    "compile_presentation_html",
    "find_asciidoctor_executable",
    "preprocess_adoc_to_slides",
    "render_unready_presentation_page",
]
