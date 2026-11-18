"""Exact host rules of ingest (INGEST_ADAPTER_PLUGIN_DESIGN.md §1 item 4, unit PL0)."""

import pytest

from claire.ingest.hosts import host_matches, patterns_overlap, url_host
from claire.ingest.registry import registry


@pytest.mark.parametrize(
    ("url", "kind"),
    [
        ("https://www.dropbox.com/s/abc/file.pdf", "web"),
        ("https://www.fox.com/news/a", "web"),
        ("https://notyoutube.com/x", "web"),
        ("https://x.com/user/status/1", "xcom"),
        ("https://mobile.twitter.com/user/status/1", "xcom"),
        ("https://www.youtube.com/watch?v=abc", "youtube"),
        ("https://youtu.be/abc", "youtube"),
    ],
)
def test_classify_uses_exact_hosts(url, kind):
    assert registry.classify(url) == kind


@pytest.mark.parametrize(
    "url",
    ["https://www.dropbox.com/s/abc/file.pdf", "https://www.fox.com/news/a", "https://notyoutube.com/x", "https://example.com/a?ref=naver.me"],
)
def test_no_site_fetcher_for_other_hosts(url):
    assert registry.get_fetcher(url) is None


def test_web_adapter_subdomain_pattern_does_not_match_other_domain():
    names = {cls.name() for cls in registry.get_matching_web_adapters("https://nothoyolab.com/a", domain_only=True)}
    assert names == set()
    names = {cls.name() for cls in registry.get_matching_web_adapters("https://www.hoyolab.com/article/1", domain_only=True)}
    assert {"hoyolab", "hoyowiki"} <= names


def test_host_patterns():
    assert url_host("https://WWW.Example.COM./a") == "www.example.com"
    assert host_matches("a.example.com", "*.example.com")
    assert not host_matches("example.com", "*.example.com")
    assert not host_matches("badexample.com", "*.example.com")
    assert host_matches("example.com", "example.com")
    assert host_matches("anything.test", "*")
    assert not host_matches(None, "*")


def test_pattern_overlap():
    assert patterns_overlap("a.example.com", "*.example.com")
    assert patterns_overlap("*.example.com", "*.b.example.com")
    assert not patterns_overlap("example.com", "*.example.com")
    assert not patterns_overlap("x.com", "dropbox.com")
    assert not patterns_overlap("*", "example.com")
