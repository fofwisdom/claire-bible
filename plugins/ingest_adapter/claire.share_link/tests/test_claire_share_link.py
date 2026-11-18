"""share.google 등 JS 리다이렉트 타겟 추출 (project plugin claire.share_link, 네트워크 없이)."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from claire.plugin_sdk.testing import load_plugin

FOLDER = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def plugin(monkeypatch):
    loaded = load_plugin(FOLDER)
    module = loaded.module("adapter")
    for name in ("_extract_target", "_is_share_host", "resolve_redirect"):
        monkeypatch.setitem(globals(), name, getattr(module, name))
    return loaded


def test_is_share_host():
    assert _is_share_host("share.google")
    assert _is_share_host("share.example.com")
    assert not _is_share_host("discuss.pytorch.kr")
    assert not _is_share_host("github.com")


def test_extract_target_prefers_canonical():
    html = (
        '<link rel="canonical" href="https://discuss.pytorch.kr/t/foo/10">'
        '<a href="https://play.google.com/x">app</a>'
    )
    assert _extract_target(html) == "https://discuss.pytorch.kr/t/foo/10"


def test_extract_target_first_external_when_no_canonical():
    # google 인프라/스토어 도메인은 건너뛰고 첫 실제 외부 URL
    html = (
        'blah https://www.google.com/search?q=x '
        'https://play.google.com/store '
        'https://discuss.pytorch.kr/t/bar/20 '
        'https://apps.apple.com/app'
    )
    assert _extract_target(html) == "https://discuss.pytorch.kr/t/bar/20"


def test_extract_target_none_when_only_infra():
    html = "https://www.google.com/a https://gstatic.com/b"
    assert _extract_target(html) is None


def test_extract_target_strips_trailing_punct():
    html = 'see "https://example.com/page".'
    assert _extract_target(html) == "https://example.com/page"


def test_is_share_host_is_exact():
    assert not _is_share_host("notnaver.me")
    assert not _is_share_host("myshare.google")


def test_share_link_routes_the_destination(plugin, monkeypatch):
    """A share link resolves to its destination, and the router takes the destination again."""
    from claire.ingest import router
    from claire.ontology.base import Document

    plugin.install(monkeypatch)
    page = '<html><link rel="canonical" href="https://example.com/article"></html>'
    plugin.mock_network(monkeypatch, lambda request: httpx.Response(200, text=page))
    seen = []

    def fake_web(url, *, full_content=False):
        seen.append(url)
        return Document(url=url, canonical_url=url, raw_text="ok", source_type="web", content_hash="h")

    monkeypatch.setattr("claire.ingest.fetchers.web.fetch_web", fake_web)
    assert router.classify("https://share.google/abc") == "share_link"
    doc = router.fetch("https://share.google/abc")
    assert seen == ["https://example.com/article"] and doc.url == "https://example.com/article"
