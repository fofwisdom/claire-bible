"""OPML import and export of the account feeds (FEDERATION_REMOTE_READING_DESIGN.md Section 1.3 rule 7, unit FP4-F2).

Import rules:

1. Each ``outline`` with ``xmlUrl`` is a feed. Its title is ``title``, else ``text``.
2. An ``outline`` without ``xmlUrl`` that contains a feed is a category. Nested categories give one folder level: the names are joined with `` › ``.
3. An ``outline`` without ``xmlUrl`` that contains no feed is not a feed (for example the settings, labels, and filters of a reader application). It is skipped as one entry and listed in the report.
4. A feed URL that the account has (or that comes again in the same file) is a duplicate and is not added again.
5. A feed URL that is not a valid ``http`` or ``https`` URL, or a feed above the feed limit of the account, is failed.

The import does not poll. The next scheduled poll or a poll on demand reads the new feeds.
"""

from __future__ import annotations

import datetime as _dt
import email.utils
import sqlite3
from dataclasses import dataclass, field
from typing import Any

from lxml import etree

from ..store import feeds as feeds_store

MAX_OPML_BYTES = 1024 * 1024
MAX_OUTLINES = 5000
REPORT_LIST_MAX = 500

_SAFE_XML_PARSER = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False, dtd_validation=False, load_dtd=False)


class OpmlError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class OpmlFeed:
    url: str  # as written in the file
    title: str
    folder: str
    html_url: str | None


@dataclass
class OpmlEntries:
    feeds: list[OpmlFeed] = field(default_factory=list)
    skipped: list[dict[str, Any]] = field(default_factory=list)


def _attr(element: Any, name: str) -> str | None:
    """An attribute without regard to case (``xmlUrl``, ``xmlurl``, ``XMLURL``)."""
    lowered = name.lower()
    for key, value in element.attrib.items():
        if isinstance(key, str) and key.lower() == lowered:
            return value
    return None


def _outline_name(element: Any) -> str:
    return feeds_store.clean_text(_attr(element, "title") or _attr(element, "text") or "", feeds_store.TITLE_MAX_CHARS)


def _outlines(element: Any) -> list[Any]:
    return [child for child in element if isinstance(child.tag, str) and etree.QName(child.tag).localname == "outline"]


def _has_feed(element: Any) -> bool:
    return any((_attr(node, "xmlUrl") or "").strip() for node in element.iter() if isinstance(node.tag, str) and etree.QName(node.tag).localname == "outline")


def parse_opml(data: bytes | str) -> OpmlEntries:
    """Read the feeds and the skipped outlines of an OPML document. Raises ``OpmlError`` (``too_large``, ``invalid_xml``, ``not_opml``, ``too_many_outlines``)."""
    raw = data.encode("utf-8") if isinstance(data, str) else data
    if len(raw) > MAX_OPML_BYTES:
        raise OpmlError("too_large")
    try:
        root = etree.fromstring(raw.lstrip(b"\xef\xbb\xbf \t\r\n"), parser=_SAFE_XML_PARSER)
    except (etree.XMLSyntaxError, ValueError) as exc:
        raise OpmlError("invalid_xml") from exc
    if root is None or etree.QName(root.tag).localname.lower() != "opml":
        raise OpmlError("not_opml")
    body = next((child for child in root if isinstance(child.tag, str) and etree.QName(child.tag).localname == "body"), None)
    if body is None:
        raise OpmlError("not_opml")
    if sum(1 for _ in body.iter("outline", "{*}outline")) > MAX_OUTLINES:
        raise OpmlError("too_many_outlines")
    entries = OpmlEntries()

    def walk(element: Any, path: list[str]) -> None:
        for outline in _outlines(element):
            name = _outline_name(outline)
            url = (_attr(outline, "xmlUrl") or "").strip()
            if url:
                entries.feeds.append(OpmlFeed(url=url, title=name, folder=feeds_store.clean_folder(path), html_url=(_attr(outline, "htmlUrl") or "").strip() or None))
            elif _has_feed(outline):
                walk(outline, [*path, name] if name else path)
            else:
                entries.skipped.append({"title": name, "folder": feeds_store.clean_folder(path), "outlines": sum(1 for _ in outline.iter("outline", "{*}outline")), "reason": "not_a_feed"})

    walk(body, [])
    return entries


def _limited(values: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return values[:REPORT_LIST_MAX]


def import_opml(conn: sqlite3.Connection, account_id: int, data: bytes | str, *, now: float) -> dict[str, Any]:
    """Add the feeds of an OPML document to the account and return the report. The caller commits. Raises ``OpmlError``."""
    entries = parse_opml(data)
    added: list[dict[str, Any]] = []
    duplicate: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    for entry in entries.feeds:
        try:
            url = feeds_store.normalize_feed_url(entry.url)
        except ValueError:
            failed.append({"url": entry.url[: feeds_store.URL_MAX_CHARS], "title": entry.title, "reason": "invalid_url"})
            continue
        try:
            feed, created = feeds_store.add_feed(conn, account_id, url, title=entry.title, folder=entry.folder, now=now)
        except feeds_store.FeedLimitReached:
            failed.append({"url": url, "title": entry.title, "reason": "feed_limit"})
            continue
        if created:
            added.append({"id": feed["id"], "url": url, "title": feed["title"], "folder": feed["folder"]})
        else:
            duplicate.append({"id": feed["id"], "url": url, "title": entry.title})
    return {
        "counts": {"added": len(added), "skipped": len(entries.skipped), "duplicate": len(duplicate), "failed": len(failed)},
        "added": _limited(added),
        "skipped": _limited(entries.skipped),
        "duplicate": _limited(duplicate),
        "failed": _limited(failed),
    }


def export_opml(feeds: list[dict[str, Any]], *, title: str = "Claire Bible feeds", now: float | None = None) -> bytes:
    """OPML 2.0 of the feeds (rows of ``feeds_store.list_feeds``). A feed with a folder is in a category outline of that name; the other feeds are at the top level."""
    root = etree.Element("opml", version="2.0")
    head = etree.SubElement(root, "head")
    etree.SubElement(head, "title").text = title
    when = _dt.datetime.now(_dt.timezone.utc) if now is None else _dt.datetime.fromtimestamp(now, _dt.timezone.utc)
    etree.SubElement(head, "dateCreated").text = email.utils.format_datetime(when, usegmt=True)
    body = etree.SubElement(root, "body")
    folders: dict[str, Any] = {}
    for feed in feeds:
        parent = body
        folder = feed.get("folder") or ""
        if folder:
            parent = folders.get(folder)
            if parent is None:
                parent = folders[folder] = etree.SubElement(body, "outline", text=folder, title=folder)
        name = feed.get("title") or feed["url"]
        attrs = {"type": "rss", "text": name, "title": name, "xmlUrl": feed["url"]}
        if feed.get("site_url"):
            attrs["htmlUrl"] = feed["site_url"]
        etree.SubElement(parent, "outline", **attrs)
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", pretty_print=True)
