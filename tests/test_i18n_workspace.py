"""Workspace, share page, and public directory in the interface language (I18N design unit IP2-3): the module interface between ``claire_bible.web.pages`` and ``claire.i18n``."""

from __future__ import annotations

import json
import re

from claire.i18n import load_catalog
from claire.i18n.context import use_locale
from claire_bible.web import pages

SLOT = re.compile(r"\$\{(?:t|tj):[a-z0-9_.]+\}|\$\{html_lang\}")


def test_each_page_slot_has_a_catalog_key() -> None:
    catalog = load_catalog("en")
    for page in (pages.GRAPH_HTML, pages.SYNDICATION_HTML, pages._SHARED_HTML):
        for key in re.findall(r"\$\{t[j]?:([a-z0-9_.]+)\}", page):
            assert key in catalog, key


def test_the_workspace_is_rendered_in_the_locale_of_the_request() -> None:
    with use_locale("en"):
        english = pages.render_graph_html()
    with use_locale("ko"):
        korean = pages.render_graph_html()
    for page in (english, korean):
        assert not SLOT.search(page)
    assert '<html lang="en">' in english and "<title>Claire Bible — Home</title>" in english
    assert 'aria-label="Document text"' in english and 'aria-label="문서 본문"' in korean
    assert '<html lang="ko">' in korean and "<title>Claire Bible — 홈</title>" in korean
    # The watchdog of the loading state compares the text with the same translation (a JSON string in the inline script), and the shared top bar omits #authstate.
    assert 'docs: "Loading documents…"' in english and '<button id="palettetrigger" type="button" class="palette-trigger"' in english
    assert 'id="authstate"' not in english and 'title="Open command palette (/)"' in english


def test_the_share_page_gets_only_the_keys_of_the_document_view() -> None:
    doc = {"id": "doc_1", "title": "Doc", "summary": "S", "detail": "D", "url": "https://example.com/a"}
    with use_locale("en"):
        page = pages.shared_html(doc)
    assert '<html lang="en" class="shared-page">' in page and not SLOT.search(page)
    catalog = json.loads(re.search(r'<script id="claire-i18n-catalog" type="application/json">(.*?)</script>', page, re.S).group(1))
    assert catalog and all(key.startswith(("reader.", "share.")) for key in catalog)
    assert catalog["share.brand"] == "Claire Bible · Shared document"
    with use_locale("ko"):
        page = pages.shared_html(doc)
    catalog = json.loads(re.search(r'<script id="claire-i18n-catalog" type="application/json">(.*?)</script>', page, re.S).group(1))
    assert catalog["share.brand"] == "Claire Bible · 공유 문서"
