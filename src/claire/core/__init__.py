"""Core contracts and outbound ports for Claire."""

from __future__ import annotations

from .ports import (
    DocumentRendererPort,
    RendererNotConfiguredError,
    SlideRendererPort,
    WebUIRendererPort,
    configure_ports,
    get_document_renderer,
    get_slide_renderer,
    get_web_ui_renderer,
    ports_configured,
    reset_ports,
)

__all__ = [
    "DocumentRendererPort",
    "RendererNotConfiguredError",
    "SlideRendererPort",
    "WebUIRendererPort",
    "configure_ports",
    "get_document_renderer",
    "get_slide_renderer",
    "get_web_ui_renderer",
    "ports_configured",
    "reset_ports",
]
