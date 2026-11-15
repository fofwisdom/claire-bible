"""Feed discovery for the subscribe dialog (FEDERATION_REMOTE_READING_DESIGN.md Section 1.3 rule 7, unit FP4-F3).

The address that the owner gives is a feed, or a page of a site with ``<link rel="alternate">`` elements for its feeds. The fetch is the fetch of the poller (``FeedFetcher``): the private address check on each hop, the redirect limit, and the size limit.
"""

from __future__ import annotations

import urllib.parse
from typing import Any

from lxml import etree

from .fetch import FeedFetcher
from .parse import FeedParseError, parse_feed

FEED_TYPES = {"application/rss+xml": "rss", "application/atom+xml": "atom", "application/feed+json": "json", "application/json": "json"}
CANDIDATE_MAX = 20
TITLE_MAX_CHARS = 300


def _html_candidates(body: bytes, base_url: str) -> list[dict[str, Any]]:
    try:
        root = etree.fromstring(body, parser=etree.HTMLParser(no_network=True, recover=True, remove_comments=True))
    except (etree.XMLSyntaxError, ValueError):
        return []
    if root is None:
        return []
    found: list[dict[str, Any]] = []
    seen: set[str] = set()
    for link in root.iter("link"):
        rel = str(link.get("rel") or "").lower().split()
        kind = FEED_TYPES.get(str(link.get("type") or "").split(";")[0].strip().lower())
        href = str(link.get("href") or "").strip()
        if "alternate" not in rel or kind is None or not href:
            continue
        try:
            url = urllib.parse.urljoin(base_url, href)
        except ValueError:
            continue
        if not url.startswith(("http://", "https://")) or url in seen:
            continue
        seen.add(url)
        found.append({"url": url, "title": " ".join(str(link.get("title") or "").split())[:TITLE_MAX_CHARS], "format": kind})
        if len(found) >= CANDIDATE_MAX:
            break
    return found


def discover(fetcher: FeedFetcher, url: str, *, now: float = 0.0) -> dict[str, Any]:
    """Find the feeds at ``url``. The result has ``feeds`` (``url``, ``title``, ``format``) and ``error`` (``None``, or the reason why the address gave no answer)."""
    fetched = fetcher.fetch(url, now=now)
    if fetched.body is None:
        return {"feeds": [], "error": fetched.error or f"http_{fetched.status}"}
    try:
        document = parse_feed(fetched.body, content_type=fetched.content_type, base_url=fetched.final_url)
    except FeedParseError:
        return {"feeds": _html_candidates(fetched.body, fetched.final_url), "error": None}
    return {"feeds": [{"url": fetched.final_url, "title": document.title[:TITLE_MAX_CHARS], "format": document.format}], "error": None}
