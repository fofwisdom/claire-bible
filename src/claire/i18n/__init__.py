"""Interface internationalization (design: ``docs/design/I18N_DESIGN.md``).

This package translates interface text only. It has no relation to the content language settings (``CLAIRE_PREFERRED_LANGUAGES``).
"""

from __future__ import annotations

from .catalog import browser_catalog, browser_catalog_json, interpolate, load_catalog, t, tn
from .context import get_locale, set_locale, use_locale
from .locales import DEFAULT_LOCALE, LOCALE_COOKIE_MAX_AGE, LOCALE_COOKIE_NAME, SUPPORTED_LOCALE_SET, SUPPORTED_LOCALES, InvalidLocale, LocaleCode, negotiate_locale, parse_explicit_locale
from .resolver import parse_accept_language, resolve_bot_locale, resolve_cli_locale, resolve_cli_locale_with_source, resolve_web_locale, resolve_web_locale_with_source

__all__ = [
    "DEFAULT_LOCALE",
    "LOCALE_COOKIE_MAX_AGE",
    "LOCALE_COOKIE_NAME",
    "SUPPORTED_LOCALES",
    "SUPPORTED_LOCALE_SET",
    "InvalidLocale",
    "LocaleCode",
    "browser_catalog",
    "browser_catalog_json",
    "get_locale",
    "interpolate",
    "load_catalog",
    "negotiate_locale",
    "parse_accept_language",
    "parse_explicit_locale",
    "resolve_bot_locale",
    "resolve_cli_locale",
    "resolve_cli_locale_with_source",
    "resolve_web_locale",
    "resolve_web_locale_with_source",
    "set_locale",
    "t",
    "tn",
    "use_locale",
]
