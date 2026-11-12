"""Active locale of the current request, update or command (I18N design Section 3.4). A ``ContextVar`` isolates each asyncio task and each ``asyncio.to_thread`` call."""

from __future__ import annotations

import contextvars
from collections.abc import Iterator
from contextlib import contextmanager

from .locales import DEFAULT_LOCALE, SUPPORTED_LOCALE_SET, LocaleCode

_current_locale: contextvars.ContextVar[LocaleCode] = contextvars.ContextVar("claire_current_locale", default=DEFAULT_LOCALE)


def _resolved(locale: str | None) -> LocaleCode:
    norm = (locale or "").strip().lower()
    return norm if norm in SUPPORTED_LOCALE_SET else DEFAULT_LOCALE  # type: ignore[return-value]


def get_locale() -> LocaleCode:
    return _current_locale.get()


def set_locale(locale: str | None) -> LocaleCode:
    resolved = _resolved(locale)
    _current_locale.set(resolved)
    return resolved


@contextmanager
def use_locale(locale: str | None) -> Iterator[LocaleCode]:
    token = _current_locale.set(_resolved(locale))
    try:
        yield _current_locale.get()
    finally:
        _current_locale.reset(token)
