"""Console section text from the catalogs (I18N design unit IP2-2): the interface between ``ConsoleSectionSpec`` and ``claire.i18n``."""

from __future__ import annotations

import re

from claire.api.accounts_routes import console
from claire.i18n import load_catalog
from claire.i18n.context import use_locale

HANGUL = re.compile("[가-힣]")


def _all_specs() -> list[console.ConsoleSectionSpec]:
    specs: dict[tuple[str, str], console.ConsoleSectionSpec] = {}
    for value in vars(console).values():
        if isinstance(value, tuple) and value and isinstance(value[0], console.ConsoleSectionSpec):
            for spec in value:
                specs[(spec.area, spec.slug)] = spec
    return list(specs.values())


def test_each_console_section_has_its_text_in_the_two_catalogs() -> None:
    specs = _all_specs()
    assert len(specs) >= 28
    for locale in ("en", "ko"):
        catalog = load_catalog(locale)
        for spec in specs:
            assert f"console.group.{spec.group_id}" in catalog, (locale, spec.group_id)
            for field in ("nav", "title", "badge", "desc"):
                assert f"console.section.{spec.area}.{spec.slug}.{field}" in catalog, (locale, spec.slug, field)


def test_console_section_text_follows_the_active_locale() -> None:
    for spec in _all_specs():
        with use_locale("en"):
            english = (spec.group_label, spec.nav_label, spec.title, spec.badge, spec.description)
        with use_locale("ko"):
            korean = (spec.group_label, spec.nav_label, spec.title, spec.badge, spec.description)
        assert not any(HANGUL.search(text) for text in english), spec.slug
        assert any(HANGUL.search(text) for text in korean), spec.slug


def test_sidebar_has_no_korean_text_for_the_english_locale() -> None:
    with use_locale("en"):
        html = console._build_sidebar_html(active_path="/settings/account", admin_viewer=True, theme_query=None)
    assert not HANGUL.search(html)
    assert "My account · Profile · Security" in html
    with use_locale("ko"):
        html = console._build_sidebar_html(active_path="/settings/account", admin_viewer=True, theme_query=None)
    assert "내 계정 · 프로필 · 보안" in html
