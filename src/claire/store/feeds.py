"""Store functions of the account feeds (``claire.db`` version 21, FEDERATION_REMOTE_READING_DESIGN.md Section 1.3 rule 7, units FP4-F1 and FP4-F2).

Each function takes the account of the caller and uses it in each query, so that a call cannot read or change a feed of a different account. The functions do not commit, except where the name says so: the caller commits.

Ingested documents are shared assets (design Section 10 rule 5): no function in this module deletes a row of ``documents``.
"""

from __future__ import annotations

import json
import sqlite3
import unicodedata
import urllib.parse
from dataclasses import dataclass
from typing import Any

FEED_LIMIT_PER_ACCOUNT = 1000
URL_MAX_CHARS = 2048
TITLE_MAX_CHARS = 300
FOLDER_MAX_CHARS = 300
FOLDER_SEPARATOR = " › "
ITEM_PAGE_MAX = 200
MARK_MAX_ITEMS = 500
ERROR_MAX_CHARS = 200

FEED_COLUMNS = (
    "id", "account_id", "url", "site_url", "title", "folder", "format", "etag", "last_modified", "websub_hub", "websub_topic",
    "last_polled_at", "last_success_at", "next_poll_at", "last_status", "last_error", "failure_count", "created_at", "updated_at",
)
ITEM_COLUMNS = (
    "id", "account_id", "feed_id", "item_key", "url", "title", "author", "summary", "content", "categories", "published_at", "modified_at",
    "fetched_at", "unread", "starred", "linked_kb_id", "linked_doc_id",
)


class FeedLimitReached(Exception):
    """The account has ``FEED_LIMIT_PER_ACCOUNT`` feeds."""


@dataclass(frozen=True)
class StoredItems:
    added: int
    updated: int


def clean_text(value: Any, limit: int) -> str:
    """One line of text without control characters, at most ``limit`` characters."""
    if not isinstance(value, str):
        return ""
    text = "".join(" " if unicodedata.category(ch) in {"Cc", "Cf", "Zl", "Zp"} else ch for ch in value)
    return " ".join(text.split())[:limit]


def clean_folder(value: Any) -> str:
    """A folder name. A list of names is one level: the names are joined with `` › `` (design Section 1.3 rule 7)."""
    if isinstance(value, (list, tuple)):
        parts = [clean_text(part, FOLDER_MAX_CHARS) for part in value]
        return FOLDER_SEPARATOR.join(part for part in parts if part)[:FOLDER_MAX_CHARS]
    return clean_text(value, FOLDER_MAX_CHARS)


def normalize_feed_url(value: Any) -> str:
    """The stored form of a feed URL: ``http`` or ``https``, a host, the scheme and the host in lower case, no fragment. Raises ``ValueError``."""
    if not isinstance(value, str):
        raise ValueError("invalid_url")
    raw = value.strip()
    if not raw or len(raw) > URL_MAX_CHARS or any(ch.isspace() or unicodedata.category(ch) in {"Cc", "Cf"} for ch in raw):
        raise ValueError("invalid_url")
    try:
        parts = urllib.parse.urlsplit(raw)
        hostname = parts.hostname
        port = parts.port
    except ValueError as exc:
        raise ValueError("invalid_url") from exc
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https") or not hostname or parts.username is not None or parts.password is not None:
        raise ValueError("invalid_url")
    host = hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    netloc = host if port is None or (scheme, port) in (("http", 80), ("https", 443)) else f"{host}:{port}"
    return urllib.parse.urlunsplit((scheme, netloc, parts.path or "/", parts.query, ""))


def _feed_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {name: row[name] for name in FEED_COLUMNS}


def _item_dict(row: sqlite3.Row) -> dict[str, Any]:
    item = {name: row[name] for name in ITEM_COLUMNS}
    try:
        item["categories"] = json.loads(row["categories"] or "[]")
    except (TypeError, ValueError):
        item["categories"] = []
    item["unread"] = bool(row["unread"])
    item["starred"] = bool(row["starred"])
    return item


# --- Subscriptions -------------------------------------------------------------------


def count_feeds(conn: sqlite3.Connection, account_id: int) -> int:
    return int(conn.execute("SELECT COUNT(*) FROM feed_subscriptions WHERE account_id=?", (account_id,)).fetchone()[0])


def list_feeds(conn: sqlite3.Connection, account_id: int) -> list[dict[str, Any]]:
    """The feeds of the account in the order folder, title, ID. Each feed has the numbers ``items`` and ``unread``."""
    rows = conn.execute(
        "SELECT f.*, (SELECT COUNT(*) FROM feed_items i WHERE i.feed_id=f.id) AS item_count, (SELECT COUNT(*) FROM feed_items i WHERE i.feed_id=f.id AND i.unread=1) AS unread_count "
        "FROM feed_subscriptions f WHERE f.account_id=? ORDER BY f.folder, lower(f.title), f.id",
        (account_id,),
    ).fetchall()
    result = []
    for row in rows:
        feed = _feed_dict(row)
        feed["items"] = int(row["item_count"])
        feed["unread"] = int(row["unread_count"])
        result.append(feed)
    return result


def get_feed(conn: sqlite3.Connection, account_id: int, feed_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM feed_subscriptions WHERE id=? AND account_id=?", (feed_id, account_id)).fetchone()
    return None if row is None else _feed_dict(row)


def find_feed_by_url(conn: sqlite3.Connection, account_id: int, url: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM feed_subscriptions WHERE account_id=? AND url=?", (account_id, url)).fetchone()
    return None if row is None else _feed_dict(row)


def add_feed(conn: sqlite3.Connection, account_id: int, url: str, *, title: str = "", folder: str = "", now: float) -> tuple[dict[str, Any], bool]:
    """Add a feed with a normalized URL (``normalize_feed_url``). A feed with the same URL is not added again: the result is that feed and ``False``. Raises ``FeedLimitReached``."""
    existing = find_feed_by_url(conn, account_id, url)
    if existing is not None:
        return existing, False
    if count_feeds(conn, account_id) >= FEED_LIMIT_PER_ACCOUNT:
        raise FeedLimitReached()
    cur = conn.execute(
        "INSERT INTO feed_subscriptions (account_id, url, title, folder, next_poll_at, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (account_id, url, clean_text(title, TITLE_MAX_CHARS), clean_folder(folder), now, now, now),
    )
    feed = get_feed(conn, account_id, int(cur.lastrowid))
    assert feed is not None
    return feed, True


def remove_feed(conn: sqlite3.Connection, account_id: int, feed_id: int) -> int | None:
    """Delete a feed and its items. Return the number of deleted items, or ``None`` when the account has no such feed. A linked document stays (design Section 10 rule 5)."""
    if get_feed(conn, account_id, feed_id) is None:
        return None
    items = conn.execute("DELETE FROM feed_items WHERE feed_id=? AND account_id=?", (feed_id, account_id)).rowcount
    conn.execute("DELETE FROM feed_subscriptions WHERE id=? AND account_id=?", (feed_id, account_id))
    return int(items)


def delete_account_feed_data(conn: sqlite3.Connection, account_id: int) -> dict[str, int]:
    """Delete all feed subscriptions and feed items of the account: a part of the client data of the account. Documents stay (design Section 10 rule 5). The caller commits."""
    items = conn.execute("DELETE FROM feed_items WHERE account_id=?", (account_id,)).rowcount
    feeds = conn.execute("DELETE FROM feed_subscriptions WHERE account_id=?", (account_id,)).rowcount
    return {"feed_subscriptions": int(feeds), "feed_items": int(items)}


def due_feeds(conn: sqlite3.Connection, *, now: float, limit: int, account_id: int | None = None, active_account_ids: list[int] | None = None) -> list[dict[str, Any]]:
    """Feeds with ``next_poll_at`` at or before ``now`` (or not set), oldest poll first. Without ``account_id``: only feeds of ``active_account_ids``, the accounts that can sign in. The accounts are in ``claire.db`` and the feeds in ``client.db`` (RD27), so the caller reads the accounts."""
    if account_id is not None:
        rows = conn.execute(
            "SELECT * FROM feed_subscriptions WHERE account_id=? AND (next_poll_at IS NULL OR next_poll_at<=?) ORDER BY COALESCE(last_polled_at, 0), id LIMIT ?",
            (account_id, now, limit),
        ).fetchall()
    else:
        ids = list(active_account_ids or [])
        if not ids:
            return []
        marks = ", ".join("?" for _ in ids)
        rows = conn.execute(
            f"SELECT * FROM feed_subscriptions WHERE account_id IN ({marks}) AND (next_poll_at IS NULL OR next_poll_at<=?) "
            "ORDER BY COALESCE(last_polled_at, 0), id LIMIT ?",
            (*ids, now, limit),
        ).fetchall()
    return [_feed_dict(row) for row in rows]


def account_feeds_for_poll(conn: sqlite3.Connection, account_id: int, *, limit: int, feed_id: int | None = None) -> list[dict[str, Any]]:
    """Feeds of the account for a poll on demand, oldest poll first."""
    if feed_id is not None:
        feed = get_feed(conn, account_id, feed_id)
        return [] if feed is None else [feed]
    rows = conn.execute("SELECT * FROM feed_subscriptions WHERE account_id=? ORDER BY COALESCE(last_polled_at, 0), id LIMIT ?", (account_id, limit)).fetchall()
    return [_feed_dict(row) for row in rows]


def record_poll(
    conn: sqlite3.Connection,
    feed_id: int,
    *,
    now: float,
    next_poll_at: float,
    status: int,
    error: str | None,
    failure_count: int,
    success: bool,
    etag: str | None = None,
    last_modified: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Record the result of one poll. ``etag`` and ``last_modified`` replace the stored values only on success with a body (``metadata`` set). ``metadata`` keys: ``format``, ``site_url``, ``websub_hub``, ``websub_topic``, ``title`` (the title replaces only an empty title)."""
    sets = ["last_polled_at=?", "next_poll_at=?", "last_status=?", "last_error=?", "failure_count=?", "updated_at=?"]
    params: list[Any] = [now, next_poll_at, status or None, (error or None) and str(error)[:ERROR_MAX_CHARS], failure_count, now]
    if success:
        sets.append("last_success_at=?")
        params.append(now)
    if metadata is not None:
        sets += ["etag=?", "last_modified=?", "format=?", "site_url=?", "websub_hub=?", "websub_topic=?", "title=CASE WHEN title='' THEN ? ELSE title END"]
        params += [etag, last_modified, metadata.get("format"), metadata.get("site_url"), metadata.get("websub_hub"), metadata.get("websub_topic"), clean_text(metadata.get("title") or "", TITLE_MAX_CHARS)]
    params.append(feed_id)
    conn.execute(f"UPDATE feed_subscriptions SET {', '.join(sets)} WHERE id=?", params)


# --- Items ---------------------------------------------------------------------------


def store_items(conn: sqlite3.Connection, *, account_id: int, feed_id: int, items: list[Any], now: float) -> StoredItems:
    """Insert new items and update the changed fields of known items (same ``item_key`` in the feed). The read and star state of a known item stays. ``items`` are ``claire.feeds.parse.FeedItem`` objects."""
    added = updated = 0
    for item in items:
        values = (item.url, item.title, item.author, item.summary, item.content, json.dumps(list(item.categories), ensure_ascii=False), item.published_at, item.modified_at)
        row = conn.execute("SELECT id, url, title, author, summary, content, categories, published_at, modified_at FROM feed_items WHERE feed_id=? AND item_key=?", (feed_id, item.key)).fetchone()
        if row is None:
            cur = conn.execute(
                "INSERT INTO feed_items (account_id, feed_id, item_key, url, title, author, summary, content, categories, published_at, modified_at, fetched_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(feed_id, item_key) DO NOTHING",
                (account_id, feed_id, item.key, *values, now),
            )
            added += max(cur.rowcount, 0)
        elif tuple(row)[1:] != values:
            conn.execute("UPDATE feed_items SET url=?, title=?, author=?, summary=?, content=?, categories=?, published_at=?, modified_at=? WHERE id=?", (*values, row["id"]))
            updated += 1
    return StoredItems(added=added, updated=updated)


def count_items(conn: sqlite3.Connection, account_id: int) -> int:
    return int(conn.execute("SELECT COUNT(*) FROM feed_items WHERE account_id=?", (account_id,)).fetchone()[0])


def list_items(
    conn: sqlite3.Connection,
    account_id: int,
    *,
    limit: int,
    before_id: int | None = None,
    feed_id: int | None = None,
    folder: str | None = None,
    unread: bool | None = None,
    starred: bool | None = None,
) -> list[dict[str, Any]]:
    """One page of items of the account, newest row first (``id`` descending). ``before_id`` is the cursor: the ``id`` of the last item of the previous page."""
    where = ["i.account_id=?"]
    params: list[Any] = [account_id]
    if before_id is not None:
        where.append("i.id<?")
        params.append(before_id)
    if feed_id is not None:
        where.append("i.feed_id=?")
        params.append(feed_id)
    if folder is not None:
        where.append("f.folder=?")
        params.append(folder)
    if unread is not None:
        where.append("i.unread=?")
        params.append(1 if unread else 0)
    if starred is not None:
        where.append("i.starred=?")
        params.append(1 if starred else 0)
    params.append(min(max(limit, 1), ITEM_PAGE_MAX))
    rows = conn.execute(
        f"SELECT i.*, f.title AS feed_title, f.folder AS feed_folder FROM feed_items i JOIN feed_subscriptions f ON f.id=i.feed_id WHERE {' AND '.join(where)} ORDER BY i.id DESC LIMIT ?",
        params,
    ).fetchall()
    result = []
    for row in rows:
        item = _item_dict(row)
        item["feed_title"] = row["feed_title"]
        item["feed_folder"] = row["feed_folder"]
        result.append(item)
    return result


def mark_items(conn: sqlite3.Connection, account_id: int, item_ids: list[int], *, unread: bool | None = None, starred: bool | None = None) -> int:
    """Set ``unread`` and or ``starred`` on items of the account. Return the number of matched items. IDs of other accounts do not match."""
    sets, params = _mark_sets(unread, starred)
    if not sets or not item_ids:
        return 0
    marks = ",".join("?" for _ in item_ids)
    return int(conn.execute(f"UPDATE feed_items SET {', '.join(sets)} WHERE account_id=? AND id IN ({marks})", (*params, account_id, *item_ids)).rowcount)


def mark_feed_items(conn: sqlite3.Connection, account_id: int, feed_id: int, *, unread: bool) -> int:
    """Set ``unread`` on all items of one feed of the account ("mark all as read")."""
    return int(conn.execute("UPDATE feed_items SET unread=? WHERE account_id=? AND feed_id=?", (1 if unread else 0, account_id, feed_id)).rowcount)


def _mark_sets(unread: bool | None, starred: bool | None) -> tuple[list[str], list[int]]:
    sets: list[str] = []
    params: list[int] = []
    if unread is not None:
        sets.append("unread=?")
        params.append(1 if unread else 0)
    if starred is not None:
        sets.append("starred=?")
        params.append(1 if starred else 0)
    return sets, params


# --- One item, its document link, and the merge with the timeline by URL (unit FP4-F3) ---


def get_item(conn: sqlite3.Connection, account_id: int, item_id: int) -> dict[str, Any] | None:
    """One item of the account with ``feed_title`` and ``feed_folder``, or ``None``."""
    row = conn.execute("SELECT i.*, f.title AS feed_title, f.folder AS feed_folder FROM feed_items i JOIN feed_subscriptions f ON f.id=i.feed_id WHERE i.id=? AND i.account_id=?", (item_id, account_id)).fetchone()
    if row is None:
        return None
    item = _item_dict(row)
    item["feed_title"] = row["feed_title"]
    item["feed_folder"] = row["feed_folder"]
    return item


def link_item(conn: sqlite3.Connection, account_id: int, item_id: int, *, kb_id: int, doc_id: str) -> bool:
    """Record the document of an ingested item. The values are not foreign keys: a purge of the document does not change the item."""
    return conn.execute("UPDATE feed_items SET linked_kb_id=?, linked_doc_id=? WHERE id=? AND account_id=?", (kb_id, doc_id, item_id, account_id)).rowcount == 1


def _urls(values: Any) -> list[str]:
    return sorted({value for value in values if isinstance(value, str) and value})


def timeline_matches(conn: sqlite3.Connection, account_id: int, urls: Any) -> dict[str, int]:
    """The merge by item URL (design Section 1.3 rule 7): for each URL, the ID of the timeline item of the account with that ``url`` or ``object_iri``. An item that shows in the timeline comes before a thread-only item; then the newest."""
    wanted = _urls(urls)
    if not wanted:
        return {}
    marks = ",".join("?" for _ in wanted)
    rows = conn.execute(
        f"SELECT id, url, object_iri FROM federation_timeline_items WHERE account_id=? AND (url IN ({marks}) OR object_iri IN ({marks})) ORDER BY in_timeline, id",
        (account_id, *wanted, *wanted),
    ).fetchall()
    found: dict[str, int] = {}
    for row in rows:
        for value in (row[1], row[2]):
            if value in wanted:
                found[value] = int(row[0])
    return found


def item_urls(conn: sqlite3.Connection, account_id: int, item_ids: list[int]) -> list[str]:
    """The URLs of the given items of the account."""
    if not item_ids:
        return []
    marks = ",".join("?" for _ in item_ids)
    return _urls(row[0] for row in conn.execute(f"SELECT url FROM feed_items WHERE account_id=? AND id IN ({marks})", (account_id, *item_ids)))


def mark_timeline_by_url(conn: sqlite3.Connection, account_id: int, urls: Any, *, unread: bool | None = None, starred: bool | None = None) -> int:
    """Set the read and star state of the timeline items of the account that have one of the URLs: a merged item has one state in the two lists."""
    sets, params = _mark_sets(unread, starred)
    wanted = _urls(urls)
    if not sets or not wanted:
        return 0
    marks = ",".join("?" for _ in wanted)
    return int(conn.execute(f"UPDATE federation_timeline_items SET {', '.join(sets)} WHERE account_id=? AND (url IN ({marks}) OR object_iri IN ({marks}))", (*params, account_id, *wanted, *wanted)).rowcount)


def mark_timeline_of_feed(conn: sqlite3.Connection, account_id: int, feed_id: int, *, unread: bool) -> int:
    """``mark_timeline_by_url`` for all items of one feed ("mark all as read")."""
    urls = "SELECT url FROM feed_items WHERE account_id=? AND feed_id=? AND url IS NOT NULL"
    return int(conn.execute(f"UPDATE federation_timeline_items SET unread=? WHERE account_id=? AND (url IN ({urls}) OR object_iri IN ({urls}))", (1 if unread else 0, account_id, account_id, feed_id, account_id, feed_id)).rowcount)


def mark_items_by_url(conn: sqlite3.Connection, account_id: int, urls: Any, *, unread: bool | None = None, starred: bool | None = None) -> int:
    """Set the read and star state of the feed items of the account that have one of the URLs (the reverse direction of ``mark_timeline_by_url``)."""
    sets, params = _mark_sets(unread, starred)
    wanted = _urls(urls)
    if not sets or not wanted:
        return 0
    marks = ",".join("?" for _ in wanted)
    return int(conn.execute(f"UPDATE feed_items SET {', '.join(sets)} WHERE account_id=? AND url IN ({marks})", (*params, account_id, *wanted)).rowcount)


def link_by_url(conn: sqlite3.Connection, account_id: int, urls: Any, *, kb_id: int, doc_id: str, timeline: bool) -> int:
    """Give the document link of an ingested item to the rows of the other list with the same URL, where they have no document yet. ``timeline`` selects the target: the timeline items, or the feed items."""
    wanted = _urls(urls)
    if not wanted:
        return 0
    marks = ",".join("?" for _ in wanted)
    if timeline:
        return int(conn.execute(f"UPDATE federation_timeline_items SET linked_kb_id=?, linked_doc_id=? WHERE account_id=? AND linked_doc_id IS NULL AND (url IN ({marks}) OR object_iri IN ({marks}))", (kb_id, doc_id, account_id, *wanted, *wanted)).rowcount)
    return int(conn.execute(f"UPDATE feed_items SET linked_kb_id=?, linked_doc_id=? WHERE account_id=? AND linked_doc_id IS NULL AND url IN ({marks})", (kb_id, doc_id, account_id, *wanted)).rowcount)
