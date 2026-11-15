"""Records of the media cache of the federation client (FEDERATION_REMOTE_READING_DESIGN.md Sections 7.2 and 9.1, unit FP4-7).

The table ``federation_media_cache`` is in ``client.db``. One row is one remote URL, for all accounts. The file of a row is in the media store under the key ``remote/{url_hash}``. All functions take a ``client.db`` connection; the caller commits.

An entry is in use when a timeline item refers to its URL: as the URL of an attachment, or as the ``src`` of an ``img`` in the content. ``orphan_hashes`` gives the entries that no item uses (Section 9.1 rule 6).
"""

from __future__ import annotations

import hashlib
import html
import json
import re
import sqlite3
from dataclasses import dataclass
from typing import Any

__all__ = [
    "MediaEntry",
    "delete_entries",
    "evictable_hashes",
    "get_entry",
    "item_media_urls",
    "media_kind",
    "orphan_hashes",
    "register_urls",
    "set_result",
    "store_key",
    "touch",
    "url_hash",
]

REMOTE_URL_MAX_LENGTH = 2048
_IMG_SRC_RE = re.compile(r"""<img\b[^>]*?\bsrc\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))""", re.IGNORECASE)
_HASH_RE = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True, slots=True)
class MediaEntry:
    url_hash: str
    remote_url: str
    store_key: str | None
    media_type: str | None
    byte_size: int | None
    state: str
    fetched_at: float | None
    last_access_at: float | None


_AV_TYPES = frozenset({"video/mp4", "video/webm"})


def media_kind(media_type: str) -> str | None:
    """``image``, ``video``, or ``audio`` for an allowed media type (Section 9.1 rule 4), else ``None``."""
    if media_type.startswith("image/"):
        return None if media_type == "image/svg+xml" else "image"
    if media_type in _AV_TYPES:
        return "video"
    return "audio" if media_type.startswith("audio/") else None


def attachment_kind(attachment: dict[str, Any]) -> str | None:
    """The media kind of an attachment by the type that the remote server declared, or ``None`` for an attachment that the render shows as a link only."""
    return media_kind(str(attachment.get("media_type") or "").split(";", 1)[0].strip().lower())


def url_hash(url: str) -> str:
    """The SHA-256 of the remote URL, in hexadecimal."""
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def is_url_hash(value: str) -> bool:
    return isinstance(value, str) and _HASH_RE.fullmatch(value) is not None


def store_key(hash_value: str) -> str:
    """The key of the file in the media store (Section 9.1.1 rule 6)."""
    return f"remote/{hash_value}"


def is_media_url(value: Any) -> bool:
    return isinstance(value, str) and value.startswith(("https://", "http://")) and len(value) <= REMOTE_URL_MAX_LENGTH and not any(ord(ch) <= 32 or ord(ch) == 127 for ch in value)


def content_image_urls(content_html: str) -> list[str]:
    """The ``src`` values of the ``img`` elements of remote HTML, as the URLs that the HTML text has (entities decoded)."""
    if not content_html or "<img" not in content_html.lower():
        return []
    urls = []
    for match in _IMG_SRC_RE.finditer(content_html):
        url = html.unescape(next(group for group in match.groups() if group is not None)).strip()
        if is_media_url(url):
            urls.append(url)
    return urls


def item_media_urls(content_html: str, attachments: list[dict[str, Any]]) -> list[str]:
    """All media URLs of one item, without duplicates: the attachments with an allowed media type first, then the images of the content."""
    urls = [str(a.get("url")) for a in attachments if isinstance(a, dict) and is_media_url(a.get("url")) and attachment_kind(a) is not None]
    urls.extend(content_image_urls(content_html))
    return list(dict.fromkeys(urls))


def register_urls(conn: sqlite3.Connection, urls: list[str], *, now: float) -> dict[str, MediaEntry]:
    """Make the entry of each URL when it is absent (state ``new``) and return the entries by URL. A new entry gets ``last_access_at`` = ``now``, so that the eviction has a time for it."""
    entries: dict[str, MediaEntry] = {}
    for url in urls:
        if not is_media_url(url):
            continue
        hash_value = url_hash(url)
        conn.execute("INSERT OR IGNORE INTO federation_media_cache (url_hash, remote_url, state, last_access_at) VALUES (?, ?, 'new', ?)", (hash_value, url, now))
        entry = get_entry(conn, hash_value)
        if entry is not None:
            entries[url] = entry
    return entries


def _entry(row: sqlite3.Row | tuple) -> MediaEntry:
    v = tuple(row)
    return MediaEntry(url_hash=str(v[0]), remote_url=str(v[1]), store_key=v[2], media_type=v[3], byte_size=v[4], state=str(v[5]), fetched_at=v[6], last_access_at=v[7])


def get_entry(conn: sqlite3.Connection, hash_value: str) -> MediaEntry | None:
    row = conn.execute("SELECT url_hash, remote_url, store_key, media_type, byte_size, state, fetched_at, last_access_at FROM federation_media_cache WHERE url_hash=?", (hash_value,)).fetchone()
    return None if row is None else _entry(row)


def set_result(conn: sqlite3.Connection, hash_value: str, *, state: str, media_type: str | None, byte_size: int | None, now: float) -> None:
    """Record the result of a fetch. Only the state ``stored`` has a store key."""
    conn.execute(
        "UPDATE federation_media_cache SET state=?, store_key=?, media_type=?, byte_size=?, fetched_at=?, last_access_at=? WHERE url_hash=?",
        (state, store_key(hash_value) if state == "stored" else None, media_type, byte_size, now, now, hash_value),
    )


def touch(conn: sqlite3.Connection, hash_value: str, *, now: float) -> None:
    conn.execute("UPDATE federation_media_cache SET last_access_at=? WHERE url_hash=?", (now, hash_value))


def evictable_hashes(conn: sqlite3.Connection, *, before: float) -> list[str]:
    """The entries that had no access after ``before`` (Section 9.1 rule 6)."""
    return [str(row[0]) for row in conn.execute("SELECT url_hash FROM federation_media_cache WHERE COALESCE(last_access_at, fetched_at, 0) < ? ORDER BY url_hash", (before,)).fetchall()]


def orphan_hashes(conn: sqlite3.Connection) -> list[str]:
    """The entries that no timeline item uses. The function reads all items one time."""
    cached = {str(row[0]) for row in conn.execute("SELECT url_hash FROM federation_media_cache").fetchall()}
    if not cached:
        return []
    used: set[str] = set()
    for content_html, attachments_json in conn.execute("SELECT content_html, attachments_json FROM federation_timeline_items"):
        try:
            attachments = json.loads(attachments_json or "[]")
        except ValueError:
            attachments = []
        for url in item_media_urls(str(content_html or ""), attachments if isinstance(attachments, list) else []):
            used.add(url_hash(url))
    return sorted(cached - used)


def delete_entries(conn: sqlite3.Connection, hashes: list[str]) -> list[str]:
    """Delete the entries and return the store keys of their files. The caller deletes the files in the media store."""
    keys = []
    for hash_value in hashes:
        if conn.execute("DELETE FROM federation_media_cache WHERE url_hash=?", (hash_value,)).rowcount:
            keys.append(store_key(hash_value))
    return keys
