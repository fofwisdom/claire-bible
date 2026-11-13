"""Feed parsers with ``lxml`` and ``json`` (external library calls): RSS 2.0, RSS 1.0, Atom 1.0, JSON Feed 1.1, and unsafe XML."""

from __future__ import annotations

import json

import pytest

from claire.feeds.parse import FeedParseError, parse_feed

BASE = "https://site.example/feed.xml"


def test_rss2_items_keys_dates_categories_and_websub() -> None:
    body = b"""<?xml version="1.0"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom" xmlns:dc="http://purl.org/dc/elements/1.1/">
<channel><title>  Site\x09News </title><link>https://site.example/</link>
<atom:link rel="hub" href="https://pubsubhubbub.example/"/><atom:link rel="self" href="https://site.example/feed.xml"/>
<item><title>One</title><link>/one</link><guid isPermaLink="false">id-1</guid><pubDate>Tue, 10 Nov 2026 09:00:00 +0900</pubDate><dc:creator>Kim</dc:creator><category>a</category><category>a</category><category> b </category></item>
<item><title>Two</title><guid>https://site.example/two</guid></item>
<item><title>Three</title><description>no id</description></item>
</channel></rss>"""
    doc = parse_feed(body, base_url=BASE)
    assert (doc.format, doc.title, doc.site_url, doc.websub_hub, doc.websub_topic) == ("rss", "Site News", "https://site.example/", "https://pubsubhubbub.example/", "https://site.example/feed.xml")
    one, two, three = doc.items
    assert (one.key, one.url, one.author, one.categories) == ("id-1", "https://site.example/one", "Kim", ("a", "b"))
    assert one.published_at == 1794268800.0
    # A permalink GUID is the URL; an item without GUID and link gets a hash key that stays the same.
    assert (two.key, two.url) == ("https://site.example/two", "https://site.example/two")
    assert three.key.startswith("sha256:") and three.key == parse_feed(body, base_url=BASE).items[2].key


def test_rss1_rdf_items() -> None:
    body = b"""<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns="http://purl.org/rss/1.0/" xmlns:dc="http://purl.org/dc/elements/1.1/">
<channel rdf:about="https://site.example/"><title>RDF</title><link>https://site.example/</link></channel>
<item rdf:about="https://site.example/a"><title>A</title><link>https://site.example/a</link><dc:date>2026-11-10T00:00:00Z</dc:date><dc:subject>topic</dc:subject></item>
</rdf:RDF>"""
    doc = parse_feed(body)
    assert doc.format == "rss" and doc.title == "RDF"
    assert [(i.key, i.url, i.categories, i.published_at) for i in doc.items] == [("https://site.example/a", "https://site.example/a", ("topic",), 1794268800.0)]


def test_atom_text_constructs_and_relative_links() -> None:
    body = b"""<feed xmlns="http://www.w3.org/2005/Atom" xml:base="https://blog.example/posts/">
<title type="html">Blog &amp;amp; Notes</title><link href="https://blog.example/"/><link rel="hub" href="https://hub.example/"/>
<entry><id>tag:blog,2026:1</id><title>Hello</title><link rel="alternate" type="text/html" href="hello"/><link rel="enclosure" href="x.mp3"/>
<published>2026-11-10T00:00:00Z</published><updated>2026-11-11T00:00:00Z</updated><author><name>Lee</name></author>
<category term="t1" label="Label"/><category label="only-label"/>
<summary type="text">a &lt; b</summary><content type="html">&lt;p&gt;x&lt;/p&gt;</content></entry>
</feed>"""
    doc = parse_feed(body, base_url="https://blog.example/atom")
    assert (doc.format, doc.site_url, doc.websub_hub) == ("atom", "https://blog.example/", "https://hub.example/")
    (entry,) = doc.items
    assert (entry.key, entry.url, entry.author, entry.categories) == ("tag:blog,2026:1", "https://blog.example/posts/hello", "Lee", ("t1", "only-label"))
    assert (entry.summary, entry.content) == ("a &lt; b", "<p>x</p>")
    assert (entry.published_at, entry.modified_at) == (1794268800.0, 1794355200.0)


def test_json_feed_1_1() -> None:
    body = json.dumps({
        "version": "https://jsonfeed.org/version/1.1",
        "title": "JSON",
        "home_page_url": "https://j.example/",
        "feed_url": "https://j.example/feed.json",
        "hubs": [{"type": "rssCloud", "url": "https://cloud.example/"}, {"type": "WebSub", "url": "https://hub.example/"}],
        "items": [
            {"id": 7, "url": "https://j.example/7", "title": "Seven", "content_text": "a<b", "tags": ["x", 1, "y"], "date_published": "2026-11-10T00:00:00+00:00", "authors": [{"name": "A"}, {"name": "B"}]},
            {"id": "8", "content_html": "<i>8</i>", "summary": "s"},
            "not an object",
        ],
    }).encode()
    doc = parse_feed(body, content_type="application/feed+json")
    assert (doc.format, doc.title, doc.websub_hub, doc.websub_topic) == ("json", "JSON", "https://hub.example/", "https://j.example/feed.json")
    seven, eight = doc.items
    assert (seven.key, seven.content, seven.categories, seven.author, seven.published_at) == ("7", "a&lt;b", ("x", "y"), "A, B", 1794268800.0)
    assert (eight.key, eight.url, eight.content, eight.summary) == ("8", None, "<i>8</i>", "s")


def test_external_entities_are_not_resolved(tmp_path) -> None:
    secret = tmp_path / "secret.txt"
    secret.write_text("TOPSECRET", encoding="utf-8")
    body = f"""<?xml version="1.0"?><!DOCTYPE rss [<!ENTITY x SYSTEM "file://{secret}">]>
<rss version="2.0"><channel><title>t</title><item><title>&x;</title><guid>g</guid></item></channel></rss>""".encode()
    doc = parse_feed(body)
    assert all("TOPSECRET" not in (i.title or "") for i in doc.items)


@pytest.mark.parametrize(
    ("body", "code"),
    [
        (b"", "empty_body"),
        (b"<html><body>page</body></html>", "not_a_feed"),
        (b'{"version": "1"}', "not_a_feed"),
        (b"{broken", "invalid_json"),
    ],
)
def test_bodies_that_are_not_feeds(body: bytes, code: str) -> None:
    with pytest.raises(FeedParseError) as info:
        parse_feed(body)
    assert info.value.code == code
