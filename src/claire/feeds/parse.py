"""Feed parsers: RSS 2.0 (and RSS 1.0), Atom 1.0, and JSON Feed 1.0 and 1.1 to one normal form.

The XML parser of ``lxml`` does not resolve entities, does not load a DTD, and does not use the network. The parsers keep the HTML of an item as it is: the reader renders it on the ``usercontent`` origin only (K10). Category values stay raw tags until ingestion (design Section 1.3 rule 7).
"""

from __future__ import annotations

import copy
import datetime as _dt
import email.utils
import hashlib
import html
import json
import unicodedata
import urllib.parse
from dataclasses import dataclass, field
from typing import Any

from lxml import etree

ATOM_NS = "http://www.w3.org/2005/Atom"
RSS1_NS = "http://purl.org/rss/1.0/"
RDF_NS = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
CONTENT_NS = "http://purl.org/rss/1.0/modules/content/"
DC_NS = "http://purl.org/dc/elements/1.1/"
XHTML_NS = "http://www.w3.org/1999/xhtml"

MAX_ITEMS = 500
TITLE_MAX_CHARS = 1000
AUTHOR_MAX_CHARS = 300
URL_MAX_CHARS = 2048
KEY_MAX_CHARS = 512
SUMMARY_MAX_CHARS = 64 * 1024
CONTENT_MAX_CHARS = 1024 * 1024
CATEGORY_MAX_CHARS = 200
MAX_CATEGORIES = 50

_SAFE_XML_PARSER = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False, dtd_validation=False, load_dtd=False, recover=True)


class FeedParseError(ValueError):
    """The body is not a feed in a known format. ``code`` is a short error code for the store and the API."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(detail or code)
        self.code = code


@dataclass(frozen=True)
class FeedItem:
    key: str
    url: str | None
    title: str
    author: str | None
    summary: str | None
    content: str | None
    categories: tuple[str, ...]
    published_at: float | None
    modified_at: float | None


@dataclass(frozen=True)
class FeedDocument:
    format: str  # 'rss', 'atom', or 'json'
    title: str
    site_url: str | None
    websub_hub: str | None
    websub_topic: str | None
    items: tuple[FeedItem, ...] = field(default_factory=tuple)


# --- Helpers -------------------------------------------------------------------------


def _clean_line(value: Any, limit: int) -> str:
    """One line of text: no control characters, spaces joined, at most ``limit`` characters."""
    if value is None:
        return ""
    text = "".join(" " if unicodedata.category(ch) in {"Cc", "Zl", "Zp"} else ch for ch in str(value))
    return " ".join(text.split())[:limit]


def _clean_block(value: Any, limit: int) -> str | None:
    if value is None:
        return None
    text = str(value).replace("\x00", "").strip()
    return text[:limit] if text else None


def _http_url(value: Any, base: str | None) -> str | None:
    """An absolute ``http`` or ``https`` URL, resolved against ``base``. Other schemes give ``None``."""
    if not isinstance(value, str):
        return None
    raw = value.strip()
    if not raw:
        return None
    try:
        absolute = urllib.parse.urljoin(base, raw) if base else raw
        parts = urllib.parse.urlsplit(absolute)
    except ValueError:
        return None
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        return None
    return absolute[:URL_MAX_CHARS]


def parse_date(value: Any) -> float | None:
    """RFC 822 (RSS) or RFC 3339 (Atom, JSON Feed) to UNIX seconds. A time without a zone is UTC. An unknown form gives ``None``."""
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    parsed: _dt.datetime | None = None
    try:
        parsed = email.utils.parsedate_to_datetime(raw)
    except (TypeError, ValueError, IndexError):
        parsed = None
    if parsed is None:
        iso = raw[:-1] + "+00:00" if raw.endswith(("Z", "z")) else raw
        try:
            parsed = _dt.datetime.fromisoformat(iso)
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_dt.timezone.utc)
    try:
        return parsed.timestamp()
    except (OverflowError, OSError, ValueError):
        return None


def _item_key(raw_id: Any, url: str | None, title: str, published: float | None, summary: str | None) -> str:
    """The stable key of an item in its feed: the ID of the feed, else the URL, else a hash of the title, the time, and the summary."""
    candidate = _clean_line(raw_id, 100_000) if raw_id is not None else ""
    if not candidate and url:
        candidate = url
    if candidate:
        if len(candidate) <= KEY_MAX_CHARS:
            return candidate
        return "sha256:" + hashlib.sha256(candidate.encode("utf-8")).hexdigest()
    basis = json.dumps([title, published, summary or ""], ensure_ascii=False)
    return "sha256:" + hashlib.sha256(basis.encode("utf-8")).hexdigest()


def _categories(values: list[Any]) -> tuple[str, ...]:
    seen: list[str] = []
    for value in values:
        text = _clean_line(value, CATEGORY_MAX_CHARS)
        if text and text not in seen:
            seen.append(text)
        if len(seen) >= MAX_CATEGORIES:
            break
    return tuple(seen)


def _text(element: Any) -> str | None:
    if element is None:
        return None
    return "".join(element.itertext())


# --- XML -----------------------------------------------------------------------------


def _local(tag: Any) -> str:
    return etree.QName(tag).localname if isinstance(tag, str) else ""


def _ns(tag: Any) -> str | None:
    return etree.QName(tag).namespace if isinstance(tag, str) else None


def _children(element: Any, name: str, namespace: str | None) -> list[Any]:
    """The child elements with this local name and exactly this namespace (``None``: no namespace)."""
    return [child for child in element if isinstance(child.tag, str) and _local(child.tag) == name and _ns(child.tag) == namespace]


def _first(element: Any, name: str, namespace: str | None) -> Any:
    """The first child element, or ``None``. Compare the result with ``None``: an element without children is false in ``lxml``."""
    found = _children(element, name, namespace)
    return found[0] if found else None


def _hub_links(element: Any, base: str | None) -> tuple[str | None, str | None]:
    """WebSub discovery: ``<link rel="hub">`` and ``<link rel="self">`` (Atom namespace, also in RSS)."""
    hub = topic = None
    for link in _children(element, "link", ATOM_NS):
        rel = (link.get("rel") or "").strip().lower()
        if rel == "hub" and hub is None:
            hub = _http_url(link.get("href"), base)
        elif rel == "self" and topic is None:
            topic = _http_url(link.get("href"), base)
    return hub, topic


def _parse_rss(root: Any, base: str | None) -> FeedDocument:
    channel = _first(root, "channel", None)
    if channel is None:
        raise FeedParseError("not_a_feed", "RSS document without a channel")
    hub, topic = _hub_links(channel, base)
    items = [_rss_item(element, base) for element in _children(channel, "item", None)[:MAX_ITEMS]]
    return FeedDocument(format="rss", title=_clean_line(_text(_first(channel, "title", None)), TITLE_MAX_CHARS), site_url=_http_url(_text(_first(channel, "link", None)), base), websub_hub=hub, websub_topic=topic, items=tuple(items))


def _rss_item(element: Any, base: str | None) -> FeedItem:
    """One RSS 2.0 item (no namespace) or RSS 1.0 item (namespace ``RSS1_NS``)."""
    namespace = _ns(element.tag)
    guid_element = _first(element, "guid", namespace)
    guid = _text(guid_element)
    url = _http_url(_text(_first(element, "link", namespace)), base)
    if url is None and guid_element is not None and (guid_element.get("isPermaLink") or "true").strip().lower() == "true":
        url = _http_url(guid, base)
    title = _clean_line(_text(_first(element, "title", namespace)), TITLE_MAX_CHARS)
    summary = _clean_block(_text(_first(element, "description", namespace)), SUMMARY_MAX_CHARS)
    content = _clean_block(_text(_first(element, "encoded", CONTENT_NS)), CONTENT_MAX_CHARS)
    author_element = _first(element, "creator", DC_NS)
    if author_element is None:
        author_element = _first(element, "author", namespace)
    published = parse_date(_text(_first(element, "pubDate", namespace))) or parse_date(_text(_first(element, "date", DC_NS)))
    categories = _categories([_text(c) for c in _children(element, "category", namespace)] + [_text(c) for c in _children(element, "subject", DC_NS)])
    raw_id = guid if guid_element is not None else element.get(f"{{{RDF_NS}}}about")
    return FeedItem(
        key=_item_key(raw_id, url, title, published, summary),
        url=url,
        title=title,
        author=_clean_line(_text(author_element), AUTHOR_MAX_CHARS) or None,
        summary=summary,
        content=content,
        categories=categories,
        published_at=published,
        modified_at=None,
    )


def _parse_rdf(root: Any, base: str | None) -> FeedDocument:
    """RSS 1.0: ``rdf:RDF`` with ``channel`` and ``item`` elements as siblings."""
    channel = _first(root, "channel", RSS1_NS)
    if channel is None:
        raise FeedParseError("not_a_feed", "RDF document without an RSS 1.0 channel")
    hub, topic = _hub_links(channel, base)
    items = [_rss_item(element, base) for element in _children(root, "item", RSS1_NS)[:MAX_ITEMS]]
    return FeedDocument(format="rss", title=_clean_line(_text(_first(channel, "title", RSS1_NS)), TITLE_MAX_CHARS), site_url=_http_url(_text(_first(channel, "link", RSS1_NS)), base), websub_hub=hub, websub_topic=topic, items=tuple(items))


def _atom_text(element: Any, *, as_html: bool) -> str | None:
    """An Atom text construct. ``as_html``: the result is HTML (``text`` is escaped, ``xhtml`` is serialized). If not, the result is plain text."""
    if element is None:
        return None
    kind = (element.get("type") or "text").strip().lower()
    if kind == "xhtml":
        div = _first(element, "div", XHTML_NS)
        if div is None:
            return _text(element)
        if not as_html:
            return _text(div)
        div = copy.deepcopy(div)
        # Serialize as plain HTML: remove the XHTML namespace from the tags.
        for node in div.iter():
            if isinstance(node.tag, str) and _ns(node.tag) == XHTML_NS:
                node.tag = _local(node.tag)
        etree.cleanup_namespaces(div)
        inner = html.escape(div.text or "", quote=False)
        for child in div:
            inner += etree.tostring(child, encoding="unicode", method="html", with_tail=True)
        return inner
    text = _text(element)
    if text is None:
        return None
    if as_html and kind in ("text", "text/plain"):
        return html.escape(text, quote=False)
    return text


def _atom_alternate(element: Any, base: str | None) -> str | None:
    fallback = None
    for link in _children(element, "link", ATOM_NS):
        rel = (link.get("rel") or "alternate").strip().lower()
        href = _http_url(link.get("href"), base)
        if href is None:
            continue
        if rel == "alternate" and (link.get("type") or "text/html").lower() in ("text/html", "application/xhtml+xml"):
            return href
        if rel == "alternate" and fallback is None:
            fallback = href
    return fallback


def _parse_atom(root: Any, base: str | None) -> FeedDocument:
    base = _http_url(root.get("{http://www.w3.org/XML/1998/namespace}base"), base) or base
    hub, topic = _hub_links(root, base)
    items = []
    for entry in _children(root, "entry", ATOM_NS)[:MAX_ITEMS]:
        url = _atom_alternate(entry, base)
        title = _clean_line(_atom_text(_first(entry, "title", ATOM_NS), as_html=False), TITLE_MAX_CHARS)
        summary = _clean_block(_atom_text(_first(entry, "summary", ATOM_NS), as_html=True), SUMMARY_MAX_CHARS)
        content = _clean_block(_atom_text(_first(entry, "content", ATOM_NS), as_html=True), CONTENT_MAX_CHARS)
        published = parse_date(_text(_first(entry, "published", ATOM_NS)))
        modified = parse_date(_text(_first(entry, "updated", ATOM_NS)))
        author = _first(entry, "author", ATOM_NS)
        author_name = _text(_first(author, "name", ATOM_NS)) if author is not None else None
        categories = _categories([c.get("term") or c.get("label") for c in _children(entry, "category", ATOM_NS)])
        items.append(
            FeedItem(
                key=_item_key(_text(_first(entry, "id", ATOM_NS)), url, title, published or modified, summary),
                url=url,
                title=title,
                author=_clean_line(author_name, AUTHOR_MAX_CHARS) or None,
                summary=summary,
                content=content,
                categories=categories,
                published_at=published or modified,
                modified_at=modified,
            )
        )
    return FeedDocument(format="atom", title=_clean_line(_atom_text(_first(root, "title", ATOM_NS), as_html=False), TITLE_MAX_CHARS), site_url=_atom_alternate(root, base), websub_hub=hub, websub_topic=topic, items=tuple(items))


# --- JSON Feed -----------------------------------------------------------------------


def _parse_json_feed(data: Any, base: str | None) -> FeedDocument:
    if not isinstance(data, dict) or not str(data.get("version") or "").startswith("https://jsonfeed.org/version/1"):
        raise FeedParseError("not_a_feed", "JSON document is not a JSON Feed")
    hub = None
    for entry in data.get("hubs") or []:
        if isinstance(entry, dict) and str(entry.get("type") or "").strip().lower() == "websub":
            hub = _http_url(entry.get("url"), base)
            if hub:
                break
    items = []
    raw_items = data.get("items")
    for raw in (raw_items if isinstance(raw_items, list) else [])[:MAX_ITEMS]:
        if not isinstance(raw, dict):
            continue
        url = _http_url(raw.get("url"), base) or _http_url(raw.get("external_url"), base)
        title = _clean_line(raw.get("title") if isinstance(raw.get("title"), str) else "", TITLE_MAX_CHARS)
        content_html = raw.get("content_html") if isinstance(raw.get("content_html"), str) else None
        content_text = raw.get("content_text") if isinstance(raw.get("content_text"), str) else None
        content = content_html if content_html is not None else (html.escape(content_text, quote=False) if content_text is not None else None)
        summary = raw.get("summary") if isinstance(raw.get("summary"), str) else None
        published = parse_date(raw.get("date_published"))
        modified = parse_date(raw.get("date_modified"))
        authors = raw.get("authors") if isinstance(raw.get("authors"), list) else ([raw["author"]] if isinstance(raw.get("author"), dict) else [])
        names = [a.get("name") for a in authors if isinstance(a, dict) and isinstance(a.get("name"), str)]
        tags = raw.get("tags") if isinstance(raw.get("tags"), list) else []
        raw_id = raw.get("id")
        items.append(
            FeedItem(
                key=_item_key(str(raw_id) if isinstance(raw_id, (str, int)) and not isinstance(raw_id, bool) else None, url, title, published or modified, summary),
                url=url,
                title=title,
                author=_clean_line(", ".join(names), AUTHOR_MAX_CHARS) or None,
                summary=_clean_block(summary, SUMMARY_MAX_CHARS),
                content=_clean_block(content, CONTENT_MAX_CHARS),
                categories=_categories([t for t in tags if isinstance(t, str)]),
                published_at=published or modified,
                modified_at=modified,
            )
        )
    return FeedDocument(
        format="json",
        title=_clean_line(data.get("title") if isinstance(data.get("title"), str) else "", TITLE_MAX_CHARS),
        site_url=_http_url(data.get("home_page_url"), base),
        websub_hub=hub,
        websub_topic=_http_url(data.get("feed_url"), base) if hub else None,
        items=tuple(items),
    )


# --- Entry point ---------------------------------------------------------------------


def parse_feed(body: bytes, *, content_type: str = "", base_url: str | None = None) -> FeedDocument:
    """Parse a feed body. The format comes from the content, not from the content type: a JSON body is a JSON Feed, an XML body is RSS or Atom. Raises ``FeedParseError``."""
    stripped = body.lstrip(b"\xef\xbb\xbf \t\r\n")
    if not stripped:
        raise FeedParseError("empty_body")
    if stripped[:1] in (b"{", b"["):
        try:
            data = json.loads(stripped.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise FeedParseError("invalid_json", str(exc)) from exc
        return _parse_json_feed(data, base_url)
    try:
        root = etree.fromstring(stripped, parser=_SAFE_XML_PARSER)
    except (etree.XMLSyntaxError, ValueError) as exc:
        raise FeedParseError("invalid_xml", str(exc)) from exc
    if root is None:
        raise FeedParseError("invalid_xml")
    name, namespace = _local(root.tag).lower(), _ns(root.tag)
    if name == "rss":
        return _parse_rss(root, base_url)
    if name == "feed" and namespace == ATOM_NS:
        return _parse_atom(root, base_url)
    if name == "rdf" and namespace == RDF_NS:
        return _parse_rdf(root, base_url)
    raise FeedParseError("not_a_feed", f"unknown root element {name!r}")
