"""Supported locales and the two validators (I18N design Section 3.1).

Explicit selection (API field, CLI flag, configuration) accepts only the exact codes. Passive negotiation (header, cookie, POSIX environment, Telegram ``language_code``) reads the primary subtag and ignores a value that is not supported.
"""

from __future__ import annotations

import re
from typing import Final, Literal

LocaleCode = Literal["en", "ko"]
SUPPORTED_LOCALES: Final[tuple[LocaleCode, ...]] = ("en", "ko")
SUPPORTED_LOCALE_SET: Final[frozenset[str]] = frozenset(SUPPORTED_LOCALES)
DEFAULT_LOCALE: Final[LocaleCode] = "en"
LOCALE_COOKIE_NAME: Final[str] = "claire_locale"
LOCALE_COOKIE_MAX_AGE: Final[int] = 31536000

_PRIMARY_SUBTAG = re.compile(r"^([A-Za-z]{2,8})(?:[-_.@].*)?$")


class InvalidLocale(ValueError):
    """An explicit locale value is not one of the supported codes (PRINCIPLES C1)."""

    def __init__(self) -> None:
        super().__init__("invalid_locale")


def parse_explicit_locale(value: object) -> LocaleCode | None:
    """Validate an explicit selection. ``None`` and the empty string mean automatic negotiation (returns ``None``). All other values that are not an exact supported code raise ``InvalidLocale``."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise InvalidLocale()
    cleaned = value.strip().lower()
    if cleaned == "":
        return None
    if cleaned not in SUPPORTED_LOCALE_SET:
        raise InvalidLocale()
    return cleaned  # type: ignore[return-value]


def negotiate_locale(value: object) -> LocaleCode | None:
    """Passive negotiation of one value such as ``ko-KR``, ``ko_KR.UTF-8`` or ``en``. Returns ``None`` when the value is absent, malformed or not supported."""
    if not isinstance(value, str):
        return None
    match = _PRIMARY_SUBTAG.match(value.strip())
    if match is None:
        return None
    primary = match.group(1).lower()
    if primary not in SUPPORTED_LOCALE_SET:
        return None
    return primary  # type: ignore[return-value]
