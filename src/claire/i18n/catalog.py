"""JSON catalog loader and interpolation (I18N design Section 4).

The catalogs in ``src/claire/static/locales/`` are the single source for Python and for browser JavaScript. A catalog is a flat object: dot-delimited key to string template. The placeholder syntax is ``{name}``.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from .context import get_locale
from .locales import DEFAULT_LOCALE, SUPPORTED_LOCALE_SET, LocaleCode

LOCALES_DIR = Path(__file__).resolve().parent.parent / "static" / "locales"
PLACEHOLDER = re.compile(r"\{([a-zA-Z0-9_]+)\}")
_KEY = re.compile(r"^[a-z0-9_]+(?:\.[a-z0-9_]+)+$")


def read_catalog_file(path: Path) -> dict[str, str]:
    """Read and validate one catalog file. A catalog that is not a flat string map, or that has a malformed key, raises ``ValueError`` (PRINCIPLES C1)."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path.name}: catalog must be a JSON object")
    for key, value in data.items():
        if not _KEY.match(key):
            raise ValueError(f"{path.name}: malformed catalog key {key!r}")
        if not isinstance(value, str):
            raise ValueError(f"{path.name}: value of {key!r} must be a string")
    return data


@lru_cache(maxsize=None)
def load_catalog(locale: str) -> dict[str, str]:
    """The catalog of one supported locale. Each file is read one time for each process."""
    if locale not in SUPPORTED_LOCALE_SET:
        raise ValueError(f"unsupported locale {locale!r}")
    return read_catalog_file(LOCALES_DIR / f"{locale}.json")


# Key domains that only the server renders: the auth pages have no script, and the bot and the CLI are not in a browser. The inline catalog of a page does not contain them.
SERVER_ONLY_KEY_PREFIXES = ("auth.", "bot.", "cli.")


def browser_catalog(locale: LocaleCode | None = None) -> dict[str, str]:
    """The catalog that a browser page gets: the keys of ``locale`` (default: the active locale) over the ``en`` keys, without the server-only domains."""
    active = locale if locale is not None else get_locale()
    merged = {**load_catalog(DEFAULT_LOCALE), **load_catalog(active)}
    return {key: value for key, value in merged.items() if not key.startswith(SERVER_ONLY_KEY_PREFIXES)}


def browser_catalog_json(locale: LocaleCode | None = None) -> str:
    """``browser_catalog`` as JSON for ``<script id="claire-i18n-catalog" type="application/json">``. The characters ``<``, ``>`` and ``&`` are Unicode escapes, thus the text cannot close the script element."""
    return json.dumps(browser_catalog(locale), ensure_ascii=False, sort_keys=True).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def interpolate(template: str, params: dict[str, Any]) -> str:
    """Replace ``{name}`` with ``str(params[name])``. A placeholder without a parameter stays as it is. ``str.format`` is not used, thus literal braces and attribute access are safe."""
    if not params:
        return template

    def _sub(match: re.Match[str]) -> str:
        name = match.group(1)
        value = params.get(name)
        return match.group(0) if value is None else str(value)

    return PLACEHOLDER.sub(_sub, template)


def t(key: str, *, locale: LocaleCode | None = None, **params: Any) -> str:
    """Translate ``key`` for ``locale`` (default: the active locale). Lookup order: the locale catalog, the ``en`` catalog, then the key itself."""
    active = locale if locale is not None else get_locale()
    template = load_catalog(active).get(key)
    if template is None and active != DEFAULT_LOCALE:
        template = load_catalog(DEFAULT_LOCALE).get(key)
    if template is None:
        return key
    return interpolate(template, params)


def tn(base_key: str, count: int, *, locale: LocaleCode | None = None, **params: Any) -> str:
    """Plural form: ``<base_key>.one`` when ``count == 1`` and the key exists, else ``<base_key>.other``. ``count`` is always a parameter."""
    active = locale if locale is not None else get_locale()
    params = {"count": count, **params}
    if count == 1:
        one_key = f"{base_key}.one"
        if one_key in load_catalog(active) or one_key in load_catalog(DEFAULT_LOCALE):
            return t(one_key, locale=active, **params)
    return t(f"{base_key}.other", locale=active, **params)
