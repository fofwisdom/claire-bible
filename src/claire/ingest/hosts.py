"""Host patterns of ingest rules (docs/implementation/INGESTION.md §2.2).

A pattern is one of:

- ``example.com``: only this host.
- ``*.example.com``: each subdomain of ``example.com``, not ``example.com`` itself.
- ``*``: every host.

There is no substring match. ``x.com`` does not match ``dropbox.com``.
"""

from __future__ import annotations

from collections.abc import Iterable
from urllib.parse import urlsplit


def url_host(url: str) -> str | None:
    """The host of ``url`` in lowercase, without a trailing dot. ``None`` when the URL has no host."""
    try:
        host = urlsplit((url or "").strip()).hostname
    except ValueError:
        return None
    if not host:
        return None
    host = host.lower().rstrip(".")
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError:
        pass
    return host or None


def is_valid_pattern(pattern: str) -> bool:
    """True for ``*``, an exact host, or ``*.`` and an exact host. Lowercase only."""
    if pattern == "*":
        return True
    name = pattern[2:] if pattern.startswith("*.") else pattern
    if not name or name != name.lower() or "*" in name or "/" in name or ":" in name:
        return False
    labels = name.split(".")
    return all(label and all(ch.isalnum() or ch == "-" for ch in label) for label in labels)


def host_matches(host: str | None, pattern: str) -> bool:
    """True when ``host`` matches ``pattern``."""
    if not host:
        return False
    if pattern == "*":
        return True
    if pattern.startswith("*."):
        return host.endswith("." + pattern[2:])
    return host == pattern


def host_matches_any(host: str | None, patterns: Iterable[str]) -> bool:
    return any(host_matches(host, pattern) for pattern in patterns)


def url_matches_any(url: str, patterns: Iterable[str]) -> bool:
    return host_matches_any(url_host(url), patterns)


def patterns_overlap(a: str, b: str) -> bool:
    """True when one host can match both patterns. ``*`` is not an overlap (§6.4)."""
    if a == "*" or b == "*":
        return False
    a_wild, b_wild = a.startswith("*."), b.startswith("*.")
    a_name = a[2:] if a_wild else a
    b_name = b[2:] if b_wild else b
    if not a_wild and not b_wild:
        return a_name == b_name
    if a_wild and not b_wild:
        return host_matches(b_name, a)
    if b_wild and not a_wild:
        return host_matches(a_name, b)
    return a_name == b_name or a_name.endswith("." + b_name) or b_name.endswith("." + a_name)
