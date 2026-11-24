"""Preferred languages (ACCOUNT_PREFERRED_LANGUAGES_DESIGN.md): the server default language and the list of each account.

A list is ordered language tags (``ko``, ``en``, ``ja``, ``zh-hant``). The server default language (``server_settings.default_languages``, when the administrator selected one) is the list of each account that has no own list (``accounts.preferred_languages`` NULL) and the list of the jobs without an account. The interface language is the first item of the list that the interface supports. The jobs use the list for the caption order of a video, the STT hints, and the VLM language.
"""

from __future__ import annotations

import re
import sqlite3
import time
from collections.abc import Iterable
from typing import Any, Final

from ..i18n.locales import SUPPORTED_LOCALES, LocaleCode

# ``server_settings`` key of the server default language.
SERVER_LANGUAGES_KEY: Final = "default_languages"
# Each list has English (P4): content without a track in a preferred language keeps its English original, and a person can select the English interface.
ALWAYS_LANGUAGE: Final = "en"
MAX_LANGUAGES: Final = 10
# A primary language subtag of 2 or 3 letters, then subtags of 2 to 8 letters or digits (BCP 47, lowercase).
_TAG_RE: Final = re.compile(r"^[a-z]{2,3}(?:-[a-z0-9]{2,8})*$")


class InvalidLanguages(ValueError):
    """A list from a person is not valid (HTTP 400 ``invalid_languages``)."""


def normalize_languages(items: Iterable[Any]) -> tuple[str, ...]:
    """A valid list: lowercase tags (``_`` becomes ``-``), no empty item, no duplicate, ``en`` at the end when the list has no ``en``. A tag that is not valid raises ``InvalidLanguages`` (C1: an explicit value is strict)."""
    out: list[str] = []
    for raw in items:
        tag = str(raw or "").strip().replace("_", "-").lower()
        if not tag:
            continue
        if not _TAG_RE.fullmatch(tag):
            raise InvalidLanguages(f"not a language tag: {raw!r}")
        if tag not in out:
            out.append(tag)
    if ALWAYS_LANGUAGE not in out:
        out.append(ALWAYS_LANGUAGE)
    if len(out) > MAX_LANGUAGES:
        raise InvalidLanguages(f"more than {MAX_LANGUAGES} languages")
    return tuple(out)


def parse_stored(value: str | None) -> tuple[str, ...] | None:
    """A stored list (comma-separated) or None. A stored value is already valid; an item that is not valid is dropped (the start never stops on data, operator rule)."""
    if value is None:
        return None
    items = [part for part in str(value).split(",") if _TAG_RE.fullmatch(part.strip().lower())]
    return normalize_languages(items)


def to_stored(languages: Iterable[str]) -> str:
    return ",".join(languages)


def interface_locale(languages: Iterable[str]) -> LocaleCode | None:
    """The first item that the interface supports, by its primary subtag (``en-gb`` gives ``en``). None when no item is supported."""
    for tag in languages:
        primary = str(tag).split("-", 1)[0]
        if primary in SUPPORTED_LOCALES:
            return primary  # type: ignore[return-value]  # one of SUPPORTED_LOCALES
    return None


def server_languages(conn: sqlite3.Connection) -> tuple[str, ...]:
    """The server default language, or an empty tuple when the administrator has not selected one (P1: it applies only when it is selected). Then an account without an own list has the automatic interface language (cookie, browser, ``CLAIRE_LANG``) and its jobs use ``en`` only."""
    row = conn.execute("SELECT value FROM server_settings WHERE key=?", (SERVER_LANGUAGES_KEY,)).fetchone()
    return () if row is None else (parse_stored(str(row[0])) or ())


def set_server_languages(conn: sqlite3.Connection, languages: Iterable[Any] | None, *, updated_by: int | None) -> tuple[str, ...]:
    """Save the server default language, or remove it (None: not selected)."""
    if languages is None:
        conn.execute("DELETE FROM server_settings WHERE key=?", (SERVER_LANGUAGES_KEY,))
        conn.commit()
        return ()
    value = normalize_languages(languages)
    conn.execute(
        "INSERT INTO server_settings (key, value, updated_at, updated_by) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at, updated_by=excluded.updated_by",
        (SERVER_LANGUAGES_KEY, to_stored(value), time.time(), updated_by),
    )
    conn.commit()
    return value


def account_languages(conn: sqlite3.Connection, account_id: int) -> tuple[str, ...]:
    """The list that applies to the jobs of an account: its own list, else the server default language (empty when not selected)."""
    row = conn.execute("SELECT preferred_languages FROM accounts WHERE id=?", (int(account_id),)).fetchone()
    own = parse_stored(None if row is None or row[0] is None else str(row[0]))
    return own if own is not None else server_languages(conn)


def read_server_languages(db_file: Any) -> tuple[str, ...]:
    """The server default language from the database file of the settings (read only), at the time of the call: a change on ``/admin/server`` applies at once also to settings that the process made earlier. An empty tuple when the database does not exist."""
    from pathlib import Path

    path = Path(db_file)
    if not path.is_file():
        return ()
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2.0)
    try:
        return server_languages(conn)
    except sqlite3.OperationalError:
        return ()
    finally:
        conn.close()


def with_server_languages(settings: Any) -> Any:
    """A copy of ``settings`` with the current server default language (the views of ``/admin/providers`` and ``/support/pipeline``)."""
    return settings.model_copy(update={"preferred_languages": to_stored(read_server_languages(settings.db_file))})
