"""Unit tests for outbound renderer port contracts and configuration registry."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

import bible
from bible.adoc import aot, markdown_export, media_transform
from bible.slides import converter, hud, preprocessor, views
from bible.web import chrome, favicon
from claire.core import (
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
from claire_bible.web import pages


class _FakeDocumentRenderer:
    renderer_revision: int = 2

    def render_to_html(self, raw: str, format: str = "adoc") -> str:
        return f"<p>{raw}</p>"

    def document_markdown(self, document: dict[str, Any]) -> str:
        return f"# {document.get('title', '')}"

    def transform_media_urls_in_html(self, html_content: str, viewer: Any | None = None, *, is_public: bool = True, account_id: int = 1, kb_id: int = 0, visibility_epoch: int = 1, roles: Any | None = None, token_signer: Callable[..., str] | None = None, expires_in: int = 3600) -> str:
        return html_content


class _FakeSlideRenderer:
    def prepare_presentation_adoc_for_compile(self, adoc: str, *, theme: str = "night", transition: str = "slide", customcss: str | None = "/static/css/reveal-claire.css", revealjsdir: str | None = "/static/vendor/reveal.js") -> str:
        return adoc

    def compile_presentation_html(self, adoc_content: str, output_path: Path, *, timeout_sec: float = 30.0, doc_id: str | None = None) -> Any:
        return (output_path, 0)

    def find_asciidoctor_executable(self) -> list[str] | None:
        return None

    def inject_hud_toolbar(self, html_content: str) -> str:
        return html_content

    def render_unready_presentation_page(self, *, title: str, status: str, back_url: str | None = None, error_message: str | None = None) -> str:
        return f"<html>{title}:{status}</html>"


class _FakeWebUIRenderer:
    def __init__(self, root: Path) -> None:
        self.static_dir = root / "static"
        self.templates_dir = root / "templates"

    def render_favicon_head_tags(self, *, base_url: str = "", include_pwa_register: bool = True) -> str:
        return ""

    def inject_favicon_head_tags(self, html_content: str, *, base_url: str = "", include_pwa_register: bool = True) -> str:
        return html_content

    def get_scalar_favicon_config(self, *, base_url: str = "") -> dict[str, Any]:
        return {"favicon": "/favicon.svg"}

    def render_appbar_html(self, *, bar_id: str = "bar", brand_id: str = "appbar-brand", brand_href: str = "/", brand_onclick: str = "", brand_title_key: str = "nav.brand_home", primary_nav_active: str = "", show_primary_nav: bool = True, brand_extra_html: str = "", center_id: str = "appbar-center", kb_picker_html: str = "", center_extra_html: str = "", end_id: str = "appbar-end", ingest_enabled: bool = False, ingest_hidden: bool = False, ingest_btn_class: str = "", end_extra_html: str = "", home_href: str = "/", home_onclick: str = "", home_current: bool = False, synd_href: str = "/syndication", synd_current: bool = False, synd_hidden: bool = False, synd_extra_attrs: str = "", hamburger_extra_html: str = "", moderation_href: str = "/moderation", moderation_label_key: str = "nav.topbar.moderation", moderation_icon: str = "shield", moderation_current: bool = False, moderation_hidden: bool = False, console_href: str = "/settings", console_label_key: str = "nav.topbar.console", console_icon: str = "settings", console_current: bool = False, console_hidden: bool = False, show_logout: bool = True, logout_hidden: bool = False) -> str:
        return "<header/>"

    def render_cmdpalette_html(self) -> str:
        return "<dialog/>"

    def render_ingest_modals_html(self) -> str:
        return "<div/>"

    def render_graph_html(self, settings: Any = None, *, include_private: bool = True, theme: Any | None = None, host: str | None = None, request: Any | None = None, bootstrap: dict[str, str] | None = None, account_id: int | None = None) -> Any:
        return "<html/>"

    def render_syndication_html(self, settings: Any = None, *, include_private: bool = True, theme: Any | None = None, host: str | None = None, auth_scope: str = "owner", request: Any | None = None) -> Any:
        return "<html/>"

    def shared_html(self, doc: dict[str, Any], settings: Any = None, *, base_url: str = "", share_token: str = "", can_access_raw: bool = False) -> Any:
        return "<html/>"


@pytest.fixture(autouse=True)
def _clean_ports() -> Any:
    reset_ports()
    yield
    reset_ports()


def test_unconfigured_ports_raise_renderer_not_configured_error() -> None:
    """Verify unconfigured ports report False and raise RendererNotConfiguredError on read."""
    assert issubclass(RendererNotConfiguredError, RuntimeError)
    assert ports_configured() is False

    for getter in (get_document_renderer, get_slide_renderer, get_web_ui_renderer):
        with pytest.raises(RendererNotConfiguredError):
            getter()


def test_configure_ports_and_reset_ports(tmp_path: Path) -> None:
    """Verify configure_ports binds all ports and reset_ports clears them."""
    doc = _FakeDocumentRenderer()
    slides = _FakeSlideRenderer()
    web_ui = _FakeWebUIRenderer(tmp_path)

    assert isinstance(doc, DocumentRendererPort)
    assert isinstance(slides, SlideRendererPort)
    assert isinstance(web_ui, WebUIRendererPort)

    configure_ports(document=doc, slides=slides, web_ui=web_ui)
    assert ports_configured() is True
    assert get_document_renderer() is doc
    assert get_slide_renderer() is slides
    assert get_web_ui_renderer() is web_ui

    reset_ports()
    assert ports_configured() is False
    for getter in (get_document_renderer, get_slide_renderer, get_web_ui_renderer):
        with pytest.raises(RendererNotConfiguredError):
            getter()


def test_configure_ports_rejects_none_without_partial_mutation(tmp_path: Path) -> None:
    """Verify configure_ports rejects None and keeps existing configuration unchanged."""
    doc = _FakeDocumentRenderer()
    slides = _FakeSlideRenderer()
    web_ui = _FakeWebUIRenderer(tmp_path)
    configure_ports(document=doc, slides=slides, web_ui=web_ui)

    with pytest.raises(ValueError):
        configure_ports(document=None, slides=slides, web_ui=web_ui)  # type: ignore[arg-type]

    assert ports_configured() is True
    assert get_document_renderer() is doc
    assert get_slide_renderer() is slides
    assert get_web_ui_renderer() is web_ui


def test_real_render_modules_satisfy_port_protocols() -> None:
    """Verify that bible and claire_bible modules match the port protocols."""

    class _RealDocumentAdapter:
        renderer_revision = aot.RENDERER_REVISION
        render_to_html = staticmethod(aot.render_to_html)
        document_markdown = staticmethod(markdown_export.document_markdown)
        transform_media_urls_in_html = staticmethod(media_transform.transform_media_urls_in_html)

    class _RealSlideAdapter:
        prepare_presentation_adoc_for_compile = staticmethod(preprocessor.prepare_presentation_adoc_for_compile)
        compile_presentation_html = staticmethod(converter.compile_presentation_html)
        find_asciidoctor_executable = staticmethod(converter.find_asciidoctor_executable)
        inject_hud_toolbar = staticmethod(hud.inject_hud_toolbar)
        render_unready_presentation_page = staticmethod(views.render_unready_presentation_page)

    class _RealWebUIAdapter:
        static_dir = bible.STATIC_DIR
        templates_dir = bible.TEMPLATES_DIR
        render_favicon_head_tags = staticmethod(favicon.render_favicon_head_tags)
        inject_favicon_head_tags = staticmethod(favicon.inject_favicon_head_tags)
        get_scalar_favicon_config = staticmethod(favicon.get_scalar_favicon_config)
        render_appbar_html = staticmethod(chrome.render_appbar_html)
        render_cmdpalette_html = staticmethod(chrome.render_cmdpalette_html)
        render_ingest_modals_html = staticmethod(chrome.render_ingest_modals_html)
        render_graph_html = staticmethod(pages.render_graph_html)
        render_syndication_html = staticmethod(pages.render_syndication_html)
        shared_html = staticmethod(pages.shared_html)

    doc_adapter = _RealDocumentAdapter()
    slide_adapter = _RealSlideAdapter()
    web_adapter = _RealWebUIAdapter()

    assert isinstance(doc_adapter, DocumentRendererPort)
    assert isinstance(slide_adapter, SlideRendererPort)
    assert isinstance(web_adapter, WebUIRendererPort)

    configure_ports(document=doc_adapter, slides=slide_adapter, web_ui=web_adapter)
    assert get_document_renderer().renderer_revision == aot.RENDERER_REVISION
    assert "<h1>" in get_document_renderer().render_to_html("= Title")
    assert "/static/vendor/reveal.js" in get_slide_renderer().prepare_presentation_adoc_for_compile("= Deck\n\n== Slide 1")
    assert get_web_ui_renderer().get_scalar_favicon_config() == {"favicon": "/favicon.svg"}
