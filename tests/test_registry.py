"""Registry of fetchers and web adapters: entries with hosts and plugin ids (docs/implementation/INGESTION.md §2.2)."""

from claire.ingest.fetchers.base import BaseFetcher, BaseWebAdapter
from claire.ingest.registry import Entry, FetcherRegistry


class _Fetcher(BaseFetcher):
    @classmethod
    def can_handle(cls, url):
        return "/item/" in url

    @classmethod
    def fetch(cls, url, **kwargs):
        raise NotImplementedError


class _Broken(BaseFetcher):
    @classmethod
    def can_handle(cls, url):
        raise RuntimeError("bad")

    @classmethod
    def fetch(cls, url, **kwargs):
        raise NotImplementedError


class _Adapter(BaseWebAdapter):
    @classmethod
    def name(cls):
        return "a"

    @classmethod
    def try_fetch(cls, url, **kwargs):
        return None


def _registry(*entries):
    return FetcherRegistry(core_entries=lambda: [], plugin_entries=lambda: list(entries))


def test_can_handle_runs_only_for_declared_hosts():
    r = _registry(Entry("fetcher", "item", 50, ("example.test",), _Fetcher, "p.one"))
    assert r.classify("https://example.test/item/1") == "item"
    assert r.classify("https://other.test/item/1") == "web"
    assert r.classify("https://example.test/page") == "web"
    assert r.get_fetcher("https://example.test/item/1") is _Fetcher


def test_order_is_priority_then_plugin_id():
    a = Entry("fetcher", "a", 10, ("*",), _Fetcher, "p.b")
    b = Entry("fetcher", "b", 10, ("*",), _Fetcher, "p.a")
    c = Entry("fetcher", "c", 20, ("*",), _Fetcher, "p.c")
    assert [e.name for e in _registry(a, b, c).entries()] == ["c", "b", "a"]


def test_can_handle_error_is_no_match():
    r = _registry(Entry("fetcher", "broken", 99, ("*",), _Broken, "p.x"), Entry("fetcher", "item", 50, ("*",), _Fetcher, "p.y"))
    assert r.classify("https://example.test/item/1") == "item"


def test_general_web_adapter_only_without_domain_only():
    general = Entry("web_adapter", "general", 30, ("*",), _Adapter, "p.g")
    site = Entry("web_adapter", "site", 40, ("example.test", "*.example.test"), _Adapter, "p.s")
    r = _registry(general, site)
    assert [e.name for e in r.web_adapter_entries("https://www.example.test/a", domain_only=True)] == ["site"]
    assert [e.name for e in r.web_adapter_entries("https://www.example.test/a")] == ["site", "general"]
    assert [e.name for e in r.web_adapter_entries("https://other.test/a", domain_only=True)] == []


def test_shared_url_text_uses_the_last_token():
    r = _registry(Entry("fetcher", "item", 50, ("example.test",), _Fetcher, "p.one"))
    assert r.classify("A title https://example.test/item/2") == "item"
    assert r.classify("just a note") == "text"
