"""One HTTP fetch of a feed: conditional ``GET``, the private address check on each hop, a redirect limit, and a size limit.

The private address check is the check of the other fetchers of Claire (``ingest.fetchers.http.check_ip_safety`` with the settings ``allow_private_networks`` and ``private_network_allowlist``). The fetch follows redirects itself, so that each hop gets the check.
"""

from __future__ import annotations

import datetime as _dt
import email.utils
import socket
import urllib.parse
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

import httpx

from ..ingest.fetchers.http import check_ip_safety

USER_AGENT = "ClaireBible-FeedPoller/1.0"
ACCEPT = "application/rss+xml, application/atom+xml, application/feed+json, application/json;q=0.9, application/xml;q=0.8, text/xml;q=0.8, */*;q=0.1"
MAX_BODY_BYTES = 5 * 1024 * 1024
MAX_REDIRECTS = 5
TIMEOUT_SECONDS = 20.0
RETRY_AFTER_MAX_SECONDS = 7 * 24 * 3600
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})

Resolver = Callable[[str], set[str]]


@dataclass(frozen=True)
class FetchResult:
    """The result of one fetch. ``status`` is the HTTP status of the last hop, or 0 when there was no HTTP response (``error`` has the reason)."""

    status: int
    final_url: str
    body: bytes | None = None
    content_type: str = ""
    etag: str | None = None
    last_modified: str | None = None
    retry_after: float | None = None
    error: str | None = None


def _system_resolver(hostname: str) -> set[str]:
    return {item[4][0] for item in socket.getaddrinfo(hostname, None) if item and len(item) > 4 and item[4]}


def parse_retry_after(value: str | None, *, now: float) -> float | None:
    """``Retry-After`` as seconds from ``now``: a number of seconds or an HTTP date. At most seven days. An unknown form gives ``None``."""
    if not value:
        return None
    raw = value.strip()
    if raw.isdigit():
        return float(min(int(raw), RETRY_AFTER_MAX_SECONDS))
    try:
        when = email.utils.parsedate_to_datetime(raw)
    except (TypeError, ValueError, IndexError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=_dt.timezone.utc)
    return float(min(max(when.timestamp() - now, 0.0), RETRY_AFTER_MAX_SECONDS))


class FeedFetcher:
    """HTTP client for feeds. Make one with ``from_settings``; tests can give a resolver and a transport."""

    def __init__(self, *, allow_private: bool = False, allowlist: Sequence[str] = (), timeout: float = TIMEOUT_SECONDS, max_bytes: int = MAX_BODY_BYTES, resolver: Resolver | None = None, transport: httpx.BaseTransport | None = None) -> None:
        self.allow_private = allow_private
        self.allowlist = [item.strip() for item in allowlist if item and item.strip()]
        self.timeout = timeout
        self.max_bytes = max_bytes
        self.resolver = resolver or _system_resolver
        self.transport = transport

    @classmethod
    def from_settings(cls, settings: Any, **kwargs: Any) -> "FeedFetcher":
        raw_allowlist = getattr(settings, "private_network_allowlist", "") or ""
        allowlist = [item.strip() for item in raw_allowlist.split(",")] if isinstance(raw_allowlist, str) else list(raw_allowlist)
        return cls(allow_private=bool(getattr(settings, "allow_private_networks", False)), allowlist=allowlist, **kwargs)

    def check_address(self, url: str) -> str | None:
        """Return ``None`` when all addresses of the host of ``url`` are allowed, else an error code."""
        try:
            parts = urllib.parse.urlsplit(url)
        except ValueError:
            return "invalid_url"
        if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
            return "invalid_url"
        try:
            addresses = self.resolver(parts.hostname)
        except (OSError, UnicodeError):
            return "dns_failure"
        if not addresses:
            return "dns_failure"
        for address in addresses:
            safe, _reason = check_ip_safety(address, allow_private=self.allow_private, allowlist=self.allowlist, hostname=parts.hostname)
            if not safe:
                return "blocked_address"
        return None

    def fetch(self, url: str, *, etag: str | None = None, last_modified: str | None = None, now: float = 0.0) -> FetchResult:
        headers = {"User-Agent": USER_AGENT, "Accept": ACCEPT}
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified
        current = url
        try:
            with httpx.Client(follow_redirects=False, timeout=self.timeout, transport=self.transport) as client:
                for _hop in range(MAX_REDIRECTS + 1):
                    blocked = self.check_address(current)
                    if blocked is not None:
                        return FetchResult(status=0, final_url=current, error=blocked)
                    with client.stream("GET", current, headers=headers) as response:
                        if response.status_code in _REDIRECT_STATUSES and response.headers.get("location"):
                            try:
                                current = urllib.parse.urljoin(current, response.headers["location"])
                            except ValueError:
                                return FetchResult(status=response.status_code, final_url=current, error="invalid_redirect")
                            continue
                        return self._result(response, current, now=now)
                return FetchResult(status=0, final_url=current, error="too_many_redirects")
        except httpx.TimeoutException:
            return FetchResult(status=0, final_url=current, error="timeout")
        except (httpx.HTTPError, ValueError) as exc:
            return FetchResult(status=0, final_url=current, error=f"request_failed:{type(exc).__name__}")

    def _result(self, response: httpx.Response, url: str, *, now: float) -> FetchResult:
        common = {
            "status": response.status_code,
            "final_url": url,
            "content_type": response.headers.get("content-type", ""),
            "etag": response.headers.get("etag"),
            "last_modified": response.headers.get("last-modified"),
            "retry_after": parse_retry_after(response.headers.get("retry-after"), now=now),
        }
        if response.status_code != 200:
            return FetchResult(**common)
        declared = response.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > self.max_bytes:
            return FetchResult(**common, error="too_large")
        body = bytearray()
        for chunk in response.iter_bytes():
            body.extend(chunk)
            if len(body) > self.max_bytes:
                return FetchResult(**common, error="too_large")
        return FetchResult(**common, body=bytes(body))
