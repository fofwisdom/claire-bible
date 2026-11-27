"""Outbound renderer port contracts and explicit configuration registry."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from threading import RLock
from typing import Any, Protocol, runtime_checkable

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


class RendererNotConfiguredError(RuntimeError):
    """Raised when code reads a renderer port before configuration."""


@runtime_checkable
class DocumentRendererPort(Protocol):
    """Port contract for document rendering, export, and media URL transformation."""

    renderer_revision: int

    def render_to_html(self, raw: str, format: str = "adoc") -> str:
        """Render document markup into HTML."""
        ...

    def document_markdown(self, document: dict[str, Any]) -> str:
        """Export one document record as Markdown."""
        ...

    def transform_media_urls_in_html(self, html_content: str, viewer: Any | None = None, *, is_public: bool = True, account_id: int = 1, kb_id: int = 0, visibility_epoch: int = 1, roles: Any | None = None, token_signer: Callable[..., str] | None = None, expires_in: int = 3600) -> str:
        """Rewrite media references in HTML for the target host and visibility."""
        ...


@runtime_checkable
class SlideRendererPort(Protocol):
    """Port contract for slide preprocessing, compilation, HUD injection, and views."""

    def prepare_presentation_adoc_for_compile(self, adoc: str, *, theme: str = "night", transition: str = "slide", customcss: str | None = "/static/css/reveal-claire.css", revealjsdir: str | None = "/static/vendor/reveal.js") -> str:
        """Prepare AsciiDoc slide source for reveal.js compilation."""
        ...

    def compile_presentation_html(self, adoc_content: str, output_path: Path, *, timeout_sec: float = 30.0, doc_id: str | None = None) -> Any:
        """Compile AsciiDoc slide source into a reveal.js HTML file."""
        ...

    def find_asciidoctor_executable(self) -> list[str] | None:
        """Find the asciidoctor command line, or return None if unavailable."""
        ...

    def inject_hud_toolbar(self, html_content: str) -> str:
        """Inject the presentation HUD toolbar into compiled reveal.js HTML."""
        ...

    def render_unready_presentation_page(self, *, title: str, status: str, back_url: str | None = None, error_message: str | None = None) -> str:
        """Render the guidance HTML page when a presentation is not ready."""
        ...


@runtime_checkable
class WebUIRendererPort(Protocol):
    """Port contract for web UI chrome, favicons, and page bundle rendering."""

    static_dir: Path
    templates_dir: Path

    def render_favicon_head_tags(self, *, base_url: str = "", include_pwa_register: bool = True) -> str:
        """Render favicon and PWA metadata tags."""
        ...

    def inject_favicon_head_tags(self, html_content: str, *, base_url: str = "", include_pwa_register: bool = True) -> str:
        """Insert favicon metadata tags into an HTML document."""
        ...

    def get_scalar_favicon_config(self, *, base_url: str = "") -> dict[str, Any]:
        """Return favicon settings for the Scalar API reference page."""
        ...

    def render_appbar_html(self, *, bar_id: str = "bar", brand_id: str = "appbar-brand", brand_href: str = "/", brand_onclick: str = "", brand_title_key: str = "nav.brand_home", primary_nav_active: str = "", show_primary_nav: bool = True, brand_extra_html: str = "", center_id: str = "appbar-center", kb_picker_html: str = "", center_extra_html: str = "", end_id: str = "appbar-end", ingest_enabled: bool = False, ingest_hidden: bool = False, ingest_btn_class: str = "", end_extra_html: str = "", home_href: str = "/", home_onclick: str = "", home_current: bool = False, synd_href: str = "/syndication", synd_current: bool = False, synd_hidden: bool = False, synd_extra_attrs: str = "", hamburger_extra_html: str = "", moderation_href: str = "/moderation", moderation_label_key: str = "nav.topbar.moderation", moderation_icon: str = "shield", moderation_current: bool = False, moderation_hidden: bool = False, console_href: str = "/account", console_label_key: str = "nav.topbar.console", console_icon: str = "settings", console_current: bool = False, console_hidden: bool = False, show_logout: bool = True, logout_hidden: bool = False) -> str:
        """Render the shared top application bar HTML."""
        ...

    def render_cmdpalette_html(self) -> str:
        """Render the command palette dialog HTML."""
        ...

    def render_ingest_modals_html(self) -> str:
        """Render the ingest, re-ingest, and regeneration modal dialogs HTML."""
        ...

    def render_graph_html(self, settings: Any = None, *, include_private: bool = True, theme: Any | None = None, host: str | None = None, request: Any | None = None, bootstrap: dict[str, str] | None = None, account_id: int | None = None) -> Any:
        """Render the main workspace graph HTML."""
        ...

    def render_syndication_html(self, settings: Any = None, *, include_private: bool = True, theme: Any | None = None, host: str | None = None, auth_scope: str = "owner", request: Any | None = None) -> Any:
        """Render the syndication workspace HTML."""
        ...

    def shared_html(self, doc: dict[str, Any], settings: Any = None, *, base_url: str = "", share_token: str = "", can_access_raw: bool = False) -> Any:
        """Render the public shared document HTML."""
        ...


_LOCK = RLock()
_document_renderer: DocumentRendererPort | None = None
_slide_renderer: SlideRendererPort | None = None
_web_ui_renderer: WebUIRendererPort | None = None


def configure_ports(*, document: DocumentRendererPort, slides: SlideRendererPort, web_ui: WebUIRendererPort) -> None:
    """Bind all three outbound renderer ports for the process."""
    if document is None or slides is None or web_ui is None:
        raise ValueError("document, slides, and web_ui ports must not be None")
    with _LOCK:
        global _document_renderer, _slide_renderer, _web_ui_renderer
        _document_renderer = document
        _slide_renderer = slides
        _web_ui_renderer = web_ui


def get_document_renderer() -> DocumentRendererPort:
    """Return the configured document renderer port or raise RendererNotConfiguredError."""
    with _LOCK:
        if _document_renderer is None:
            raise RendererNotConfiguredError("Document renderer port is not configured. Call configure_ports() first.")
        return _document_renderer


def get_slide_renderer() -> SlideRendererPort:
    """Return the configured slide renderer port or raise RendererNotConfiguredError."""
    with _LOCK:
        if _slide_renderer is None:
            raise RendererNotConfiguredError("Slide renderer port is not configured. Call configure_ports() first.")
        return _slide_renderer


def get_web_ui_renderer() -> WebUIRendererPort:
    """Return the configured web UI renderer port or raise RendererNotConfiguredError."""
    with _LOCK:
        if _web_ui_renderer is None:
            raise RendererNotConfiguredError("Web UI renderer port is not configured. Call configure_ports() first.")
        return _web_ui_renderer


def ports_configured() -> bool:
    """Return True when all three renderer ports are configured."""
    with _LOCK:
        return _document_renderer is not None and _slide_renderer is not None and _web_ui_renderer is not None


def reset_ports() -> None:
    """Clear all configured renderer ports."""
    with _LOCK:
        global _document_renderer, _slide_renderer, _web_ui_renderer
        _document_renderer = None
        _slide_renderer = None
        _web_ui_renderer = None
