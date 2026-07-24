"""외부 fetch 공통 보안 경계.

사용자·페이지가 제공한 URL이 loopback, 사설망, link-local 또는 cloud metadata 같은
비공개 주소로 연결되지 않도록 DNS 결과와 모든 HTTP redirect hop을 검사한다.
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Iterable
from urllib.parse import SplitResult, urljoin, urlsplit

import httpcore
import httpx

from .fetchers.base import FetchError

_REDIRECT_STATUSES = {301, 302, 303, 307, 308}
_MAX_REDIRECTS = 5


def _configured_cidrs() -> str:
    # config가 netpolicy를 import하지 않으므로 이 lazy import는 순환하지 않는다.
    from ..config import get_settings

    return get_settings().fetch_allowed_cidrs


def _allowed_networks(raw: str | None = None) -> tuple[ipaddress._BaseNetwork, ...]:
    value = _configured_cidrs() if raw is None else raw
    networks = []
    for token in (value or "").split(","):
        token = token.strip()
        if not token:
            continue
        try:
            networks.append(ipaddress.ip_network(token, strict=False))
        except ValueError as exc:
            raise FetchError("invalid CLAIRE_FETCH_ALLOWED_CIDRS entry") from exc
    return tuple(networks)


def _resolve_addresses(host: str, port: int) -> tuple[ipaddress._BaseAddress, ...]:
    try:
        rows = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise FetchError(f"unable to resolve outbound host: {host}") from exc
    addresses = []
    seen = set()
    for row in rows:
        try:
            address = ipaddress.ip_address(row[4][0].split("%", 1)[0])
        except ValueError as exc:
            raise FetchError(f"invalid resolved address for outbound host: {host}") from exc
        if address not in seen:
            seen.add(address)
            addresses.append(address)
    if not addresses:
        raise FetchError(f"outbound host resolved to no addresses: {host}")
    return tuple(addresses)


def _address_allowed(
    address: ipaddress._BaseAddress,
    allowed: Iterable[ipaddress._BaseNetwork],
) -> bool:
    if address.is_global:
        return True
    return any(address.version == network.version and address in network for network in allowed)


def _validated_target(
    url: str,
    *,
    resolve: bool = True,
    allowed_cidrs: str | None = None,
) -> tuple[SplitResult, tuple[ipaddress._BaseAddress, ...]]:
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as exc:
        raise FetchError("invalid outbound URL") from exc
    if parts.scheme.lower() not in {"http", "https"}:
        raise FetchError("outbound URL must use http or https")
    if not parts.hostname:
        raise FetchError("outbound URL requires a host")
    if parts.username is not None or parts.password is not None:
        raise FetchError("outbound URL must not contain credentials")

    host = parts.hostname.rstrip(".")
    if "%" in host:
        raise FetchError("scoped IP addresses are not allowed")
    allowed = _allowed_networks(allowed_cidrs)
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    addresses = (literal,) if literal is not None else ()
    if resolve and literal is None:
        addresses = _resolve_addresses(host, port or (443 if parts.scheme.lower() == "https" else 80))
    if addresses and any(not _address_allowed(address, allowed) for address in addresses):
        raise FetchError("outbound URL resolves to a non-public address")
    return parts, addresses


def validate_outbound_url(
    url: str,
    *,
    resolve: bool = True,
    allowed_cidrs: str | None = None,
) -> str:
    """HTTP(S) URL을 검증하고 안전하지 않은 주소면 FetchError를 발생시킨다."""
    _validated_target(url, resolve=resolve, allowed_cidrs=allowed_cidrs)
    return url


def _validated_addresses(
    host: str,
    port: int,
    allowed: Iterable[ipaddress._BaseNetwork],
) -> tuple[ipaddress._BaseAddress, ...]:
    """연결 직전에 host를 한 번만 해석하고 모든 결과를 정책으로 검사한다."""
    normalized = host.rstrip(".")
    if "%" in normalized:
        raise FetchError("scoped IP addresses are not allowed")
    try:
        literal = ipaddress.ip_address(normalized)
    except ValueError:
        literal = None
    addresses = (literal,) if literal is not None else _resolve_addresses(normalized, port)
    if any(not _address_allowed(address, allowed) for address in addresses):
        raise FetchError("outbound URL resolves to a non-public address")
    return addresses


class _PinnedNetworkBackend(httpcore.NetworkBackend):
    """원래 HTTP origin을 유지한 채 실제 TCP 목적지만 검증된 IP로 고정한다."""

    def __init__(
        self,
        allowed_cidrs: str | None = None,
        *,
        backend: httpcore.NetworkBackend | None = None,
    ):
        self._allowed = _allowed_networks(allowed_cidrs)
        self._backend = backend or httpcore.SyncBackend()

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options=None,
    ):
        addresses = _validated_addresses(host, port, self._allowed)
        last_error = None
        for address in addresses:
            try:
                return self._backend.connect_tcp(
                    str(address),
                    port,
                    timeout=timeout,
                    local_address=local_address,
                    socket_options=socket_options,
                )
            except (httpcore.ConnectError, httpcore.ConnectTimeout) as exc:
                last_error = exc
        assert last_error is not None
        raise last_error

    def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options=None,
    ):
        raise FetchError("unix sockets are not allowed for outbound web fetches")


class _CoreResponseStream(httpx.SyncByteStream):
    def __init__(self, stream):
        self._stream = stream

    def __iter__(self):
        yield from self._stream

    def close(self) -> None:
        self._stream.close()


class _PinnedHTTPTransport(httpx.BaseTransport):
    """HTTPX URL/Host/SNI/cookie 의미를 보존하는 httpcore 전송 어댑터."""

    def __init__(self, allowed_cidrs: str | None = None):
        self._pool = httpcore.ConnectionPool(
            ssl_context=httpx.create_ssl_context(verify=True, trust_env=False),
            http1=True,
            http2=False,
            network_backend=_PinnedNetworkBackend(allowed_cidrs),
        )

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        if not isinstance(request.stream, httpx.SyncByteStream):
            raise TypeError("outbound request must use a synchronous byte stream")
        core_request = httpcore.Request(
            method=request.method,
            url=httpcore.URL(
                scheme=request.url.raw_scheme,
                host=request.url.raw_host,
                port=request.url.port,
                target=request.url.raw_path,
            ),
            headers=request.headers.raw,
            content=request.stream,
            extensions=request.extensions,
        )
        response = self._pool.handle_request(core_request)
        return httpx.Response(
            status_code=response.status,
            headers=response.headers,
            stream=_CoreResponseStream(response.stream),
            extensions=response.extensions,
        )

    def close(self) -> None:
        self._pool.close()


def _make_pinned_transport(allowed_cidrs: str | None) -> httpx.BaseTransport:
    return _PinnedHTTPTransport(allowed_cidrs)


def safe_httpx_get(
    url: str,
    *,
    headers=None,
    timeout: float = 30,
    max_redirects: int = _MAX_REDIRECTS,
    allowed_cidrs: str | None = None,
    **kwargs,
):
    """논리 URL은 유지하고 실제 TCP 연결만 검증한 IP에 고정해 GET한다."""
    supplied_headers = httpx.Headers(headers)
    if any(name.lower() in {b"authorization", b"cookie", b"proxy-authorization"}
           for name, _value in supplied_headers.raw):
        raise FetchError("credentials are not allowed on outbound fetches")
    transport = _make_pinned_transport(allowed_cidrs)
    current = url
    with httpx.Client(
        transport=transport,
        follow_redirects=False,
        timeout=timeout,
        headers=supplied_headers,
        trust_env=False,
    ) as client:
        for hop in range(max_redirects + 1):
            # scheme/userinfo/private literal을 연결 계층 진입 전에 차단한다. hostname
            # DNS는 _PinnedNetworkBackend.connect_tcp에서 단 한 번 해석·검사·고정한다.
            validate_outbound_url(
                current, resolve=False, allowed_cidrs=allowed_cidrs,
            )
            response = client.get(current, **kwargs)
            if response.status_code not in _REDIRECT_STATUSES:
                return response
            location = response.headers.get("location")
            if not location:
                return response
            if hop >= max_redirects:
                raise FetchError("too many redirects")
            current = urljoin(str(response.url), location)
    raise FetchError("too many redirects")
