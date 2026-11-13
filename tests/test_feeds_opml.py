"""OPML files (I/O with ``lxml``): folders from nested categories, skipped outlines, errors, and export."""

from __future__ import annotations

import pytest
from lxml import etree

from claire.feeds import opml

NESTED = b"""<?xml version="1.0"?>
<opml version="2.0"><head><title>x</title></head><body>
  <outline title="A" text="ignored">
    <outline text="B">
      <outline text="C"><outline text="Deep" xmlurl="https://deep.example/feed"/></outline>
      <outline text="Empty category"/>
    </outline>
    <outline text="Feed A" xmlUrl="https://a.example/feed" htmlUrl="https://a.example/"/>
  </outline>
  <outline text="Top" XMLURL="https://top.example/feed"/>
</body></opml>"""


def test_nested_categories_give_one_folder_level() -> None:
    entries = opml.parse_opml(NESTED)
    assert [(f.url, f.title, f.folder, f.html_url) for f in entries.feeds] == [
        ("https://deep.example/feed", "Deep", "A › B › C", None),
        ("https://a.example/feed", "Feed A", "A", "https://a.example/"),
        ("https://top.example/feed", "Top", "", None),
    ]
    assert entries.skipped == [{"title": "Empty category", "folder": "A › B", "outlines": 1, "reason": "not_a_feed"}]


@pytest.mark.parametrize(
    ("data", "code"),
    [
        (b"<opml><body>", "invalid_xml"),
        (b"<rss version='2.0'/>", "not_opml"),
        (b"<opml version='2.0'><head/></opml>", "not_opml"),
        (b"<opml><body>" + b"x" * (opml.MAX_OPML_BYTES + 1) + b"</body></opml>", "too_large"),
    ],
)
def test_invalid_documents(data: bytes, code: str) -> None:
    with pytest.raises(opml.OpmlError) as info:
        opml.parse_opml(data)
    assert info.value.code == code


def test_export_round_trip() -> None:
    feeds = [
        {"url": "https://a.example/feed", "title": "A", "folder": "News", "site_url": "https://a.example/"},
        {"url": "https://b.example/feed", "title": "", "folder": "", "site_url": None},
        {"url": "https://c.example/feed", "title": "C", "folder": "News", "site_url": None},
    ]
    data = opml.export_opml(feeds, now=1_800_000_000.0)
    root = etree.fromstring(data)
    assert root.get("version") == "2.0" and root.findtext("head/dateCreated") == "Fri, 15 Jan 2027 08:00:00 GMT"
    entries = opml.parse_opml(data)
    assert [(f.url, f.title, f.folder) for f in entries.feeds] == [
        ("https://a.example/feed", "A", "News"),
        ("https://c.example/feed", "C", "News"),
        ("https://b.example/feed", "https://b.example/feed", ""),
    ]
    assert entries.skipped == []
