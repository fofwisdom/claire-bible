"""Bootstrap wiring that binds Bible and Claire-Bible adapters into Claire ports."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import bible
from bible import adoc, slides
from bible.web import chrome, favicon
from claire.core.ports import configure_ports, ports_configured

from .web import pages

__all__ = ["configure"]


class _DocumentRendererAdapter:
    """Adapter from ``bible.adoc`` to ``claire.core.ports.DocumentRendererPort``."""

    @property
    def renderer_revision(self) -> int:
        return adoc.RENDERER_REVISION

    def render_to_html(self, raw: str, format: str = "adoc") -> str:
        return adoc.render_to_html(raw, format=format)

    def document_markdown(self, document: dict[str, Any]) -> str:
        return adoc.document_markdown(document)

    def transform_media_urls_in_html(self, html_content: str, viewer: Any | None = None, *, is_public: bool = True, account_id: int = 1, kb_id: int = 0, visibility_epoch: int = 1, roles: Any | None = None, token_signer: Callable[..., str] | None = None, expires_in: int = 3600) -> str:
        return adoc.transform_media_urls_in_html(html_content, viewer, is_public=is_public, account_id=account_id, kb_id=kb_id, visibility_epoch=visibility_epoch, roles=roles, token_signer=token_signer, expires_in=expires_in)


class _SlideRendererAdapter:
    """Adapter from ``bible.slides`` to ``claire.core.ports.SlideRendererPort``."""

    def prepare_presentation_adoc_for_compile(self, adoc: str, *, theme: str = "night", transition: str = "slide", customcss: str | None = "/static/css/reveal-claire.css", revealjsdir: str | None = "/static/vendor/reveal.js") -> str:
        return slides.prepare_presentation_adoc_for_compile(adoc, theme=theme, transition=transition, customcss=customcss, revealjsdir=revealjsdir)

    def compile_presentation_html(self, adoc_content: str, output_path: Path, *, timeout_sec: float = 30.0, doc_id: str | None = None) -> Any:
        return slides.compile_presentation_html(adoc_content, output_path, timeout_sec=timeout_sec, doc_id=doc_id)

    def find_asciidoctor_executable(self) -> list[str] | None:
        return slides.find_asciidoctor_executable()

    def inject_hud_toolbar(self, html_content: str) -> str:
        return slides.inject_hud_toolbar(html_content)

    def render_unready_presentation_page(self, *, title: str, status: str, back_url: str | None = None, error_message: str | None = None) -> str:
        return slides.render_unready_presentation_page(title=title, status=status, back_url=back_url, error_message=error_message)


class _WebUIRendererAdapter:
    """Adapter from ``bible.web`` and ``claire_bible.web.pages`` to ``WebUIRendererPort``."""

    @property
    def static_dir(self) -> Path:
        return bible.STATIC_DIR

    @property
    def templates_dir(self) -> Path:
        return bible.TEMPLATES_DIR

    def render_favicon_head_tags(self, *, base_url: str = "", include_pwa_register: bool = True) -> str:
        return favicon.render_favicon_head_tags(base_url=base_url, include_pwa_register=include_pwa_register)

    def inject_favicon_head_tags(self, html_content: str, *, base_url: str = "", include_pwa_register: bool = True) -> str:
        return favicon.inject_favicon_head_tags(html_content, base_url=base_url, include_pwa_register=include_pwa_register)

    def get_scalar_favicon_config(self, *, base_url: str = "") -> dict[str, Any]:
        return favicon.get_scalar_favicon_config(base_url=base_url)

    def render_appbar_html(self, *, bar_id: str = "bar", brand_id: str = "appbar-brand", brand_href: str = "/", brand_onclick: str = "", brand_title_key: str = "nav.brand_home", brand_extra_html: str = "", center_id: str = "appbar-center", center_extra_html: str = "", end_id: str = "appbar-end", ingest_enabled: bool = False, ingest_hidden: bool = False, ingest_btn_class: str = "", end_extra_html: str = "", home_href: str = "/", home_onclick: str = "", home_current: bool = False, synd_href: str = "/syndication", synd_current: bool = False, synd_hidden: bool = False, synd_extra_attrs: str = "", hamburger_extra_html: str = "", moderation_href: str = "/moderation", moderation_label_key: str = "nav.topbar.moderation", moderation_icon: str = "shield", moderation_current: bool = False, moderation_hidden: bool = False, console_href: str = "/settings", console_label_key: str = "nav.topbar.console", console_icon: str = "settings", console_current: bool = False, console_hidden: bool = False, show_logout: bool = True, logout_hidden: bool = False) -> str:
        return chrome.render_appbar_html(
            bar_id=bar_id,
            brand_id=brand_id,
            brand_href=brand_href,
            brand_onclick=brand_onclick,
            brand_title_key=brand_title_key,
            brand_extra_html=brand_extra_html,
            center_id=center_id,
            center_extra_html=center_extra_html,
            end_id=end_id,
            ingest_enabled=ingest_enabled,
            ingest_hidden=ingest_hidden,
            ingest_btn_class=ingest_btn_class,
            end_extra_html=end_extra_html,
            home_href=home_href,
            home_onclick=home_onclick,
            home_current=home_current,
            synd_href=synd_href,
            synd_current=synd_current,
            synd_hidden=synd_hidden,
            synd_extra_attrs=synd_extra_attrs,
            hamburger_extra_html=hamburger_extra_html,
            moderation_href=moderation_href,
            moderation_label_key=moderation_label_key,
            moderation_icon=moderation_icon,
            moderation_current=moderation_current,
            moderation_hidden=moderation_hidden,
            console_href=console_href,
            console_label_key=console_label_key,
            console_icon=console_icon,
            console_current=console_current,
            console_hidden=console_hidden,
            show_logout=show_logout,
            logout_hidden=logout_hidden,
        )

    def render_cmdpalette_html(self) -> str:
        return chrome.render_cmdpalette_html()

    def render_ingest_modals_html(self) -> str:
        return chrome.render_ingest_modals_html()

    def render_graph_html(self, settings: Any = None, *, include_private: bool = True, theme: Any | None = None, host: str | None = None, request: Any | None = None, bootstrap: dict[str, str] | None = None, account_id: int | None = None) -> Any:
        return pages.render_graph_html(settings, include_private=include_private, theme=theme, host=host, request=request, bootstrap=bootstrap, account_id=account_id)

    def render_syndication_html(self, settings: Any = None, *, include_private: bool = True, theme: Any | None = None, host: str | None = None, auth_scope: str = "owner", request: Any | None = None) -> Any:
        return pages.render_syndication_html(settings, include_private=include_private, theme=theme, host=host, auth_scope=auth_scope, request=request)

    def shared_html(self, doc: dict[str, Any], settings: Any = None, *, base_url: str = "", share_token: str = "", can_access_raw: bool = False) -> Any:
        return pages.shared_html(doc, settings, base_url=base_url, share_token=share_token, can_access_raw=can_access_raw)


def configure(*, force: bool = False) -> None:
    """Bind all Bible and Claire-Bible adapters into Claire outbound ports."""
    if not force and ports_configured():
        return
    configure_ports(
        document=_DocumentRendererAdapter(),
        slides=_SlideRendererAdapter(),
        web_ui=_WebUIRendererAdapter(),
    )
