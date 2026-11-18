"""Registry of fetchers and web adapters (INGEST_ADAPTER_PLUGIN_DESIGN.md §7.2, §7.3).

The entries come from two places: the core (format routes) and the enabled plugins (``plugin_host.runtime``). Each entry declares exact host patterns; the registry calls ``can_handle`` only for a URL whose host matches (PL-D12). The order is deterministic: priority (higher first), then plugin id, then name.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Callable, Iterable
from urllib.parse import urlsplit

from ..plugin_host.context import RegisteredEntry
from .hosts import host_matches_any, url_host

log = logging.getLogger(__name__)

Entry = RegisteredEntry

_URL_RE = re.compile(r"https?://[^\s)\]\}<>\"']+")


def _core_entries() -> list[Entry]:
    """Format routes of the core and the site adapters that are not moved to plugins yet (unit PL2 moves them)."""
    from .fetchers import hoyolab, hoyowiki, naver_clip, video, xcom, youtube

    hoyolab_hosts = ("hoyolab.com", "*.hoyolab.com")
    return [
        Entry("fetcher", "naver_clip", 120, naver_clip.NAVER_CLIP_HOSTS, naver_clip.NaverClipFetcher, None),
        Entry("fetcher", "hoyowiki", 120, hoyolab_hosts, hoyowiki.HoyowikiFetcher, None),
        Entry("fetcher", "hoyolab", 115, hoyolab_hosts, hoyolab.HoYoLabFetcher, None),
        Entry("fetcher", "youtube", 90, youtube.YOUTUBE_HOSTS, youtube.YouTubeFetcher, None),
        Entry("fetcher", "xcom", 90, xcom.XCOM_HOSTS, xcom.XComFetcher, None),
        Entry("fetcher", "video", 80, ("*",), video.VideoFetcher, None),
        Entry("web_adapter", "hoyowiki", 40, ("wiki.hoyolab.com", "*.hoyolab.com"), hoyowiki.HoyowikiAdapter, None),
        Entry("web_adapter", "hoyolab", 38, hoyolab_hosts, hoyolab.HoYoLabAdapter, None),
    ]


def _plugin_entries() -> list[Entry]:
    from ..plugin_host.runtime import runtime

    return list(runtime().current().entries)


def _order(entries: Iterable[Entry]) -> list[Entry]:
    return sorted(entries, key=lambda e: (-e.priority, e.plugin_id or "", e.name))


class FetcherRegistry:
    def __init__(
        self,
        *,
        core_entries: Callable[[], list[Entry]] = _core_entries,
        plugin_entries: Callable[[], list[Entry]] = _plugin_entries,
    ) -> None:
        self._core_entries_fn = core_entries
        self._plugin_entries_fn = plugin_entries
        self._core: list[Entry] | None = None

    def entries(self) -> list[Entry]:
        if self._core is None:
            self._core = list(self._core_entries_fn())
        return _order([*self._core, *self._plugin_entries_fn()])

    def fetchers(self) -> list[Entry]:
        return [e for e in self.entries() if e.kind == "fetcher"]

    def web_adapters(self) -> list[Entry]:
        return [e for e in self.entries() if e.kind == "web_adapter"]

    def entry_for(self, cls: type) -> Entry | None:
        for entry in self.entries():
            if entry.cls is cls:
                return entry
        return None

    def _fetcher_entry(self, payload: str) -> Entry | None:
        host = url_host(payload)
        if host is None:
            return None
        for entry in self.fetchers():
            if not host_matches_any(host, entry.hosts):
                continue
            try:
                if entry.cls.can_handle(payload):
                    return entry
            except Exception as exc:  # noqa: BLE001 - a fault of one entry is "no match" (§7.3 item 2)
                log.warning("can_handle of %s/%s raised %s: %s", entry.plugin_id or "core", entry.name, type(exc).__name__, exc)
                _count_error(entry)
        return None

    def classify(self, payload: str) -> str:
        """Payload → routing kind: the name of a fetcher entry, ``redirect``, ``web``, ``file``, or ``text``."""
        t = (payload or "").strip()
        if not t:
            return "text"
        entry = self._fetcher_entry(t)
        if entry is not None:
            return entry.name

        t_lower = t.lower()
        if not t_lower.startswith(("http://", "https://")):
            tokens = t.split()
            if tokens:
                last = tokens[-1].rstrip(".,;)。")
                m = _URL_RE.fullmatch(last)
                if m:
                    t = m.group(0)
                    t_lower = t.lower()
                    entry = self._fetcher_entry(t)
                    if entry is not None:
                        return entry.name

        if t_lower.startswith(("http://", "https://")):
            host = url_host(t) or ""
            path = urlsplit(t_lower).path
            if host == "share.google" or host.startswith("share."):
                return "redirect"
            if host == "wiki.hoyolab.com" or (host_matches_any(host, ("hoyolab.com", "*.hoyolab.com")) and ("/entry/" in path or "/character/" in path or "/entry_preview/" in path)):
                return "hoyowiki"
            if host_matches_any(host, ("hoyolab.com", "*.hoyolab.com")) and "/article/" in path:
                return "hoyolab"
            return "web"

        if (os.path.sep in t or t.lower().endswith((".pdf", ".odt", ".md", ".txt", ".markdown", ".rst"))) and os.path.exists(t):
            return "file"
        if t.startswith("file://"):
            return "file"
        return "text"

    def get_fetcher(self, payload: str) -> type | None:
        entry = self._fetcher_entry((payload or "").strip())
        return entry.cls if entry else None

    def web_adapter_entries(self, url: str, *, domain_only: bool = False) -> list[Entry]:
        """Web adapters whose hosts match. An entry with ``*`` is general and comes only when ``domain_only`` is false."""
        host = url_host(url)
        result = []
        for entry in self.web_adapters():
            general = "*" in entry.hosts
            if general:
                if not domain_only:
                    result.append(entry)
                continue
            if host_matches_any(host, entry.hosts):
                result.append(entry)
        return result


def _count_error(entry: Entry) -> None:
    from ..plugin_host.runtime import runtime

    runtime().count_error(entry.plugin_id, entry.name)


def report_entry_error(entry: Entry, exc: BaseException) -> None:
    """An exception of a web adapter (§7.3 item 4): a log line with the plugin id and the entry, and a count in the process report."""
    log.warning("web adapter %s/%s raised %s: %s", entry.plugin_id or "core", entry.name, type(exc).__name__, exc)
    _count_error(entry)


registry = FetcherRegistry()
