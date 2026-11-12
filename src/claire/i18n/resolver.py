"""Locale resolution chains for the WebUI, the Telegram bot and the CLI (I18N design Sections 3.2 and 3.3). Each function is pure: the caller supplies all inputs."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Literal

from .locales import DEFAULT_LOCALE, SUPPORTED_LOCALE_SET, LocaleCode, negotiate_locale

LocaleSource = Literal["account", "cookie", "accept-language", "flag", "config", "env", "telegram", "default"]

_QVALUE = re.compile(r"^(?:0(?:\.[0-9]{0,3})?|1(?:\.0{0,3})?)$")
_LANGUAGE_RANGE = re.compile(r"^[A-Za-z]{1,8}(?:-[A-Za-z0-9]{1,8})*$")
_POSIX_VARIABLES = ("LC_ALL", "LC_MESSAGES", "LANG")


def _supported(value: str | None) -> LocaleCode | None:
    """A stored value (account column, configuration) that is already canonical. Other values count as unset."""
    return value if value in SUPPORTED_LOCALE_SET else None  # type: ignore[return-value]


def parse_accept_language(header: str | None) -> LocaleCode | None:
    """RFC 9110 ``Accept-Language``: the supported primary subtag with the highest q-value. Ties keep the header order. A range with ``q=0`` is excluded and a malformed range is skipped."""
    if not header:
        return None
    ranges: list[tuple[float, int, str]] = []
    for index, part in enumerate(header.split(",")):
        pieces = [p.strip() for p in part.split(";")]
        tag = pieces[0]
        if not _LANGUAGE_RANGE.match(tag):
            continue
        quality = 1.0
        malformed = False
        for param in pieces[1:]:
            name, sep, raw = param.partition("=")
            if name.strip().lower() != "q" or not sep or not _QVALUE.match(raw.strip()):
                malformed = True
                break
            quality = float(raw.strip())
        if malformed or quality <= 0:
            continue
        ranges.append((-quality, index, tag))
    for _quality, _index, tag in sorted(ranges):
        primary = tag.split("-")[0].lower()
        if primary in SUPPORTED_LOCALE_SET:
            return primary  # type: ignore[return-value]
    return None


def resolve_web_locale_with_source(*, account_locale: str | None, cookie_locale: str | None, accept_language: str | None, settings_lang: str | None) -> tuple[LocaleCode, LocaleSource]:
    """WebUI chain: account, cookie, ``Accept-Language``, ``CLAIRE_LANG``, default."""
    found = _supported(account_locale)
    if found is not None:
        return found, "account"
    found = negotiate_locale(cookie_locale)
    if found is not None:
        return found, "cookie"
    found = parse_accept_language(accept_language)
    if found is not None:
        return found, "accept-language"
    found = _supported(settings_lang)
    if found is not None:
        return found, "config"
    return DEFAULT_LOCALE, "default"


def resolve_web_locale(*, account_locale: str | None, cookie_locale: str | None, accept_language: str | None, settings_lang: str | None) -> LocaleCode:
    return resolve_web_locale_with_source(account_locale=account_locale, cookie_locale=cookie_locale, accept_language=accept_language, settings_lang=settings_lang)[0]


def resolve_bot_locale(*, owner_locale: str | None, language_code: str | None, settings_lang: str | None) -> LocaleCode:
    """Telegram chain: bot owner account, Telegram user ``language_code``, ``CLAIRE_LANG``, default."""
    return _supported(owner_locale) or negotiate_locale(language_code) or _supported(settings_lang) or DEFAULT_LOCALE


def resolve_cli_locale_with_source(*, cli_lang: str | None, settings_lang: str | None, environ: Mapping[str, str]) -> tuple[LocaleCode, LocaleSource]:
    """CLI chain: ``--lang`` (argparse validates it), ``CLAIRE_LANG``, POSIX ``LC_ALL`` then ``LC_MESSAGES`` then ``LANG``, default. As in POSIX, only the first variable that is set and not empty is read."""
    found = _supported(cli_lang)
    if found is not None:
        return found, "flag"
    found = _supported(settings_lang)
    if found is not None:
        return found, "config"
    for name in _POSIX_VARIABLES:
        value = environ.get(name, "")
        if value:
            found = negotiate_locale(value)
            if found is not None:
                return found, "env"
            break
    return DEFAULT_LOCALE, "default"


def resolve_cli_locale(*, cli_lang: str | None, settings_lang: str | None, environ: Mapping[str, str]) -> LocaleCode:
    return resolve_cli_locale_with_source(cli_lang=cli_lang, settings_lang=settings_lang, environ=environ)[0]
