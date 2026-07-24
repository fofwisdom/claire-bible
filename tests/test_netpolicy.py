"""외부 URL 정책 — 네트워크 없이 주소/redirect 경계를 검증한다."""

from __future__ import annotations

import socket

import httpcore
import httpx
import pytest

from claire.ingest.fetchers.base import FetchError
from claire.ingest.fetchers.web import _validate_browser_request_url
from claire.ingest import netpolicy


def _dns(*addresses: str):
    return [
        (socket.AF_INET6 if ":" in address else socket.AF_INET,
         socket.SOCK_STREAM, 6, "", (address, 443, 0, 0)
         if ":" in address else (address, 443))
        for address in addresses
    ]


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/x",
    "http://10.0.0.1/x",
    "http://169.254.169.254/latest/meta-data",
    "http://[::1]/x",
    "http://[::ffff:127.0.0.1]/x",
    "file:///etc/passwd",
    "https://user:pass@example.com/x",
])
def test_rejects_non_public_literal_and_non_http_urls(url):
    with pytest.raises(FetchError):
        netpolicy.validate_outbound_url(url, resolve=False, allowed_cidrs="")


def test_dns_must_resolve_only_to_public_addresses(monkeypatch):
    monkeypatch.setattr(
        netpolicy.socket, "getaddrinfo",
        lambda *a, **k: _dns("93.184.216.34", "10.0.0.7"))
    with pytest.raises(FetchError, match="non-public"):
        netpolicy.validate_outbound_url("https://example.test/x", allowed_cidrs="")


def test_explicit_private_cidr_preserves_intentional_intranet_fetch(monkeypatch):
    monkeypatch.setattr(
        netpolicy.socket, "getaddrinfo",
        lambda *a, **k: _dns("10.20.30.40"))
    assert netpolicy.validate_outbound_url(
        "https://intranet.test/x", allowed_cidrs="10.20.0.0/16")


def test_browser_request_policy_allows_local_resources_and_blocks_private_ip():
    for url in ("about:blank", "data:text/plain,ok", "blob:https://example.com/id"):
        _validate_browser_request_url(url)
    with pytest.raises(FetchError, match="non-public"):
        _validate_browser_request_url("http://127.0.0.1/admin")


def test_public_to_private_redirect_is_rejected_before_second_request(monkeypatch):
    def fake_dns(host, *_args, **_kwargs):
        return _dns("10.0.0.5" if host == "internal.test" else "93.184.216.34")

    monkeypatch.setattr(netpolicy.socket, "getaddrinfo", fake_dns)
    seen = []

    def handler(request):
        netpolicy._validated_addresses(
            request.url.host,
            request.url.port or (443 if request.url.scheme == "https" else 80),
            (),
        )
        seen.append(str(request.url))
        return httpx.Response(
            302, headers={"location": "http://internal.test/admin"},
        )

    monkeypatch.setattr(
        netpolicy, "_make_pinned_transport",
        lambda _allowed: httpx.MockTransport(handler),
    )
    with pytest.raises(FetchError, match="non-public"):
        netpolicy.safe_httpx_get("https://public.test/start")
    assert seen == ["https://public.test/start"]


def test_network_backend_connects_only_to_the_validated_dns_result(monkeypatch):
    calls = 0

    def rebinding_dns(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return _dns("93.184.216.34" if calls == 1 else "127.0.0.1")

    monkeypatch.setattr(netpolicy.socket, "getaddrinfo", rebinding_dns)

    class RecordingBackend(httpcore.NetworkBackend):
        def __init__(self):
            self.hosts = []

        def connect_tcp(self, host, port, **_kwargs):
            self.hosts.append((host, port))
            return object()

        def connect_unix_socket(self, path, **_kwargs):
            raise AssertionError(path)

    delegate = RecordingBackend()
    backend = netpolicy._PinnedNetworkBackend("", backend=delegate)
    marker = backend.connect_tcp("rebind.test", 443)

    assert marker is not None
    assert calls == 1
    assert delegate.hosts == [("93.184.216.34", 443)]


def test_safe_httpx_keeps_logical_host_url_and_headers(monkeypatch):
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["host"] = request.headers["host"]
        return httpx.Response(200, content=b"ok")

    monkeypatch.setattr(
        netpolicy, "_make_pinned_transport",
        lambda _allowed: httpx.MockTransport(handler),
    )
    response = netpolicy.safe_httpx_get(
        "https://example.test/path", headers={"User-Agent": "claire-test"},
    )

    assert response.status_code == 200
    assert str(response.url) == "https://example.test/path"
    assert seen == {"url": "https://example.test/path", "host": "example.test"}


def test_safe_httpx_rejects_credentials_before_connect(monkeypatch):
    monkeypatch.setattr(
        netpolicy, "_make_pinned_transport",
        lambda _allowed: pytest.fail("transport must not be created"),
    )
    with pytest.raises(FetchError, match="credentials"):
        netpolicy.safe_httpx_get(
            "https://example.test/", headers={"Authorization": "Bearer secret"},
        )
