"""Media proxy of the federation client: fetch of remote media, the cache in the media store, and its cleanup (FEDERATION_REMOTE_READING_DESIGN.md Section 9.1, unit FP4-7).

- ``MediaFetcher`` fetches one remote URL with the rules of Section 9.1 rule 3 (the private address check on each hop, a redirect limit of 3, a timeout of 15 seconds, no cookie and no credential) and rule 4 (allowed types and size limits).
- ``MediaProxy`` gives the response of ``/media/remote/{url_hash}``: the first request fetches the URL and puts the file in the media store under ``remote/{url_hash}``; later requests read the store. The records are in ``client.db`` (``store.federation_media``).
- ``remove_orphans`` and ``evict`` delete entries and their files (rule 6).
- The media token binds ``(account_id, url_hash, exp)`` (rule 1).
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import time
import urllib.parse
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from starlette.responses import PlainTextResponse, Response

from ..feeds.fetch import FeedFetcher, Resolver
from ..store import client_db
from ..store import db as dbm
from ..store import federation_client as client_store
from ..store import federation_media as media_records
from ..store.federation_media import media_kind
from ..store.media import MediaObject, MediaStore, MediaStoreError, RangeParseError
from ..store.secrets import SecretService, get_secret_service

log = logging.getLogger(__name__)

MAX_REDIRECTS = 3
TIMEOUT_SECONDS = 15.0
MEDIA_TOKEN_TTL = 3600
# A fetch that failed is tried again after this time; a file above the limit after the longer time (the admin can increase the limit).
RETRY_FAILED_SECONDS = 3600.0
RETRY_TOO_LARGE_SECONDS = 24 * 3600.0
# ``last_access_at`` changes at most one time in this period, so that a read does not write each time.
TOUCH_INTERVAL_SECONDS = 3600.0
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})

# Section 9.1 rule 5.
RESPONSE_HEADERS = {
    "x-content-type-options": "nosniff",
    "content-security-policy": "sandbox; default-src 'none'",
    "cross-origin-resource-policy": "cross-origin",
    "cache-control": "private",
    "accept-ranges": "bytes",
}


# --- Token (Section 9.1 rule 1) --------------------------------------------


def _token_signature(key: bytes, account_id: int, hash_value: str, exp: int) -> str:
    return hmac.new(key, f"media:{int(account_id)}:{hash_value}:{exp}".encode("utf-8"), hashlib.sha256).hexdigest()


def create_media_token(account_id: int, hash_value: str, expires_in: int = MEDIA_TOKEN_TTL, *, secret_service: SecretService | None = None, now: float | None = None) -> str:
    """A token that binds ``(account_id, url_hash, exp)``. The form is ``{account_id}.{exp}.{signature}``."""
    exp = int(now if now is not None else time.time()) + int(expires_in)
    key = (secret_service or get_secret_service()).derive_hmac_key("remote_media_token")
    return f"{int(account_id)}.{exp}.{_token_signature(key, account_id, hash_value, exp)}"


def verify_media_token(token: str, hash_value: str, *, secret_service: SecretService | None = None, now: float | None = None) -> int | None:
    """The account of a valid token for ``hash_value``, or ``None``."""
    if not isinstance(token, str) or token.count(".") != 2:
        return None
    account_text, exp_text, signature = token.split(".")
    if not all(part.isascii() and part.isdigit() and len(part) <= 12 for part in (account_text, exp_text)):
        return None
    account_id, exp = int(account_text), int(exp_text)
    if int(now if now is not None else time.time()) > exp:
        return None
    key = (secret_service or get_secret_service()).derive_hmac_key("remote_media_token")
    return account_id if hmac.compare_digest(signature, _token_signature(key, account_id, hash_value, exp)) else None


# --- Fetch (Section 9.1 rules 3 and 4) -------------------------------------


@dataclass(frozen=True)
class MediaFetchResult:
    """``state`` is ``stored`` (with ``body``), ``failed``, or ``too_large``. ``error`` is a short code for the log."""

    state: str
    media_type: str | None = None
    body: bytes | None = None
    error: str | None = None


class MediaFetcher:
    """HTTP client for remote media. The address check is that of the feed fetcher (``FeedFetcher.check_address``)."""

    def __init__(self, *, image_max_bytes: int, av_max_bytes: int, user_agent: str, allow_private: bool = False, allowlist: Sequence[str] = (), timeout: float = TIMEOUT_SECONDS, resolver: Resolver | None = None, transport: httpx.BaseTransport | None = None) -> None:
        self.image_max_bytes = image_max_bytes
        self.av_max_bytes = av_max_bytes
        self.user_agent = user_agent
        self.timeout = timeout
        self.transport = transport
        self._addresses = FeedFetcher(allow_private=allow_private, allowlist=allowlist, resolver=resolver)

    def _limit(self, kind: str) -> int:
        return self.image_max_bytes if kind == "image" else self.av_max_bytes

    def fetch(self, url: str) -> MediaFetchResult:
        headers = {"User-Agent": self.user_agent, "Accept": "image/*, video/mp4, video/webm, audio/*"}
        current = url
        try:
            # Each hop has a new client: no cookie of an earlier answer goes to a later request.
            for _hop in range(MAX_REDIRECTS + 1):
                blocked = self._addresses.check_address(current)
                if blocked is not None:
                    return MediaFetchResult(state="failed", error=blocked)
                with httpx.Client(follow_redirects=False, timeout=self.timeout, transport=self.transport) as client, client.stream("GET", current, headers=headers) as response:
                    if response.status_code in _REDIRECT_STATUSES and response.headers.get("location"):
                        current = urllib.parse.urljoin(current, response.headers["location"])
                        continue
                    return self._result(response)
            return MediaFetchResult(state="failed", error="too_many_redirects")
        except httpx.TimeoutException:
            return MediaFetchResult(state="failed", error="timeout")
        except (httpx.HTTPError, ValueError) as exc:
            return MediaFetchResult(state="failed", error=f"request_failed:{type(exc).__name__}")

    def _result(self, response: httpx.Response) -> MediaFetchResult:
        if response.status_code != 200:
            return MediaFetchResult(state="failed", error=f"http_{response.status_code}")
        media_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        kind = media_kind(media_type)
        if kind is None:
            return MediaFetchResult(state="failed", media_type=media_type[:128] or None, error="type_not_allowed")
        limit = self._limit(kind)
        declared = response.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > limit:
            return MediaFetchResult(state="too_large", media_type=media_type)
        body = bytearray()
        for chunk in response.iter_bytes():
            body.extend(chunk)
            if len(body) > limit:
                return MediaFetchResult(state="too_large", media_type=media_type)
        return MediaFetchResult(state="stored", media_type=media_type, body=bytes(body))


# --- Proxy -----------------------------------------------------------------


@dataclass(frozen=True)
class ProxyAnswer:
    """The answer of the proxy: an HTTP status and, for 200 and 206, the object."""

    status: int
    obj: MediaObject | None = None
    media_type: str | None = None


def _user_agent(settings: Any) -> str:
    public_url = str(getattr(settings, "public_url", "") or "").strip()
    return f"ClaireBible-MediaProxy/1.0 (+{public_url})" if public_url else "ClaireBible-MediaProxy/1.0"


def _allowlist(settings: Any) -> list[str]:
    raw = getattr(settings, "private_network_allowlist", "") or ""
    return [item.strip() for item in raw.split(",")] if isinstance(raw, str) else list(raw)


class MediaProxy:
    """The cache of remote media for one process. ``fetcher_factory`` is for tests."""

    def __init__(self, settings: Any, store: MediaStore, *, now: Callable[[], float] | None = None, fetcher_factory: Callable[[dict[str, Any]], MediaFetcher] | None = None) -> None:
        self.settings = settings
        self.store = store
        self.now = now or time.time
        self.fetcher_factory = fetcher_factory
        self._locks: dict[str, asyncio.Lock] = {}

    def _client_path(self) -> Path | None:
        db_file = getattr(self.settings, "db_file", None)
        if not db_file:
            return None
        path = client_db.client_db_path(db_file)
        return path if path.exists() else None

    def _fetcher(self) -> MediaFetcher:
        conn = dbm.connect_existing(Path(self.settings.db_file), readonly=True)
        try:
            reading = client_store.get_reading_settings(conn)
        finally:
            conn.close()
        if self.fetcher_factory is not None:
            return self.fetcher_factory(reading)
        return MediaFetcher(image_max_bytes=int(reading["media_image_max_bytes"]), av_max_bytes=int(reading["media_av_max_bytes"]), user_agent=_user_agent(self.settings), allow_private=bool(getattr(self.settings, "allow_private_networks", False)), allowlist=_allowlist(self.settings))

    def _entry(self, hash_value: str) -> media_records.MediaEntry | None:
        path = self._client_path()
        if path is None:
            return None
        conn = client_db.connect_client_existing(path, readonly=True)
        try:
            return media_records.get_entry(conn, hash_value)
        finally:
            conn.close()

    def _write(self, fn: Callable[[Any], None]) -> None:
        path = self._client_path()
        if path is None:
            return
        conn = client_db.connect_client_existing(path)
        try:
            fn(conn)
            conn.commit()
        finally:
            conn.close()

    def _fetch_due(self, entry: media_records.MediaEntry) -> bool:
        if entry.state == "new":
            return True
        age = self.now() - float(entry.fetched_at or 0.0)
        if entry.state == "failed":
            return age >= RETRY_FAILED_SECONDS
        return entry.state == "too_large" and age >= RETRY_TOO_LARGE_SECONDS

    async def _fetch_and_store(self, entry: media_records.MediaEntry) -> media_records.MediaEntry | None:
        result = await asyncio.to_thread(lambda: self._fetcher().fetch(entry.remote_url))
        state = result.state
        if state == "stored":
            assert result.body is not None and result.media_type is not None
            await self.store.put(media_records.store_key(entry.url_hash), result.body, result.media_type)
        else:
            log.info("remote media %s: %s%s", entry.url_hash[:12], state, "" if result.error is None else f" ({result.error})")
        size = len(result.body) if result.body is not None else None
        await asyncio.to_thread(self._write, lambda conn: media_records.set_result(conn, entry.url_hash, state=state, media_type=result.media_type, byte_size=size, now=self.now()))
        return await asyncio.to_thread(self._entry, entry.url_hash)

    async def serve(self, hash_value: str, range_header: str | None = None) -> ProxyAnswer:
        """The answer for one entry. 404: no entry. 502: the fetch failed. 413: the file is above the limit. 503: the media store is not available. 416: the range is not possible."""
        if not media_records.is_url_hash(hash_value):
            return ProxyAnswer(404)
        entry = await asyncio.to_thread(self._entry, hash_value)
        if entry is None:
            return ProxyAnswer(404)
        try:
            if self._fetch_due(entry):
                async with self._locks.setdefault(hash_value, asyncio.Lock()):
                    entry = await asyncio.to_thread(self._entry, hash_value)
                    if entry is not None and self._fetch_due(entry):
                        entry = await self._fetch_and_store(entry)
                self._locks.pop(hash_value, None)
                if entry is None:
                    return ProxyAnswer(404)
            if entry.state != "stored":
                return ProxyAnswer(413 if entry.state == "too_large" else 502)
            obj = await self.store.get(media_records.store_key(hash_value), range_header=range_header)
        except RangeParseError:
            return ProxyAnswer(416)
        except MediaStoreError as exc:
            log.error("The media store is not available for remote media %s: %s", hash_value[:12], exc)
            return ProxyAnswer(503)
        if obj is None:
            # The record says ``stored``, but the file is not in the store (for example a lost volume). The next request fetches again.
            await asyncio.to_thread(self._write, lambda conn: conn.execute("UPDATE federation_media_cache SET state='new', store_key=NULL WHERE url_hash=?", (hash_value,)))
            return ProxyAnswer(502)
        if self.now() - float(entry.last_access_at or 0.0) >= TOUCH_INTERVAL_SECONDS:
            await asyncio.to_thread(self._write, lambda conn: media_records.touch(conn, hash_value, now=self.now()))
        return ProxyAnswer(obj.status_code, obj=obj, media_type=entry.media_type)

    async def _delete(self, select: Callable[[Any], list[str]]) -> int:
        """Delete the entries that ``select`` gives, then their files. A file that cannot be deleted stays in the store without a record; the log has the key."""
        keys: list[str] = []

        def _rows(conn: Any) -> None:
            keys.extend(media_records.delete_entries(conn, select(conn)))

        await asyncio.to_thread(self._write, _rows)
        for key in keys:
            try:
                await self.store.delete(key)
            except (MediaStoreError, ValueError) as exc:
                log.warning("The file of a deleted media entry stays in the store: %s (%s)", key, exc)
        return len(keys)

    async def remove_orphans(self) -> int:
        """Delete the entries that no timeline item uses, and their files (Section 9.1 rule 6). Return the number of entries."""
        return await self._delete(media_records.orphan_hashes)

    async def evict(self, *, evict_days: int | None = None) -> dict[str, int]:
        """The media cleanup: delete the entries without an access in ``media_evict_days`` (the admin setting), then the entries that no item uses."""
        if evict_days is None:
            conn = dbm.connect_existing(Path(self.settings.db_file), readonly=True)
            try:
                evict_days = int(client_store.get_reading_settings(conn)["media_evict_days"])
            finally:
                conn.close()
        before = self.now() - evict_days * 86400.0
        evicted = await self._delete(lambda conn: media_records.evictable_hashes(conn, before=before))
        return {"evict_days": evict_days, "evicted": evicted, "orphans": await self.remove_orphans()}


def proxy_response_headers(answer: ProxyAnswer) -> dict[str, str]:
    """The headers of a 200 or 206 answer (Section 9.1 rule 5). The media type is the type of the record: the fetch checked it against the allowed types."""
    assert answer.obj is not None
    headers = {**RESPONSE_HEADERS, "content-type": answer.media_type or "application/octet-stream", "content-length": str(answer.obj.content_length)}
    if answer.obj.content_range:
        headers["content-range"] = answer.obj.content_range
    return headers


_STATUS_TEXT = {404: "Not Found", 413: "Content Too Large", 416: "Requested Range Not Satisfiable", 502: "Bad Gateway", 503: "Service Unavailable"}


def proxy_response(answer: ProxyAnswer) -> Response:
    """The HTTP response of a proxy answer. An answer without an object has no media headers and is not cached."""
    if answer.obj is None:
        headers = {"cache-control": "no-store", "x-content-type-options": "nosniff"}
        if answer.status == 503:
            headers["retry-after"] = "60"
        return PlainTextResponse(_STATUS_TEXT.get(answer.status, "Error"), status_code=answer.status, headers=headers)
    return Response(content=answer.obj.body, status_code=answer.status, headers=proxy_response_headers(answer))


# --- Cleanup in the ``api`` process and the command line (Section 10 rule 4) ---

CLEANUP_START_DELAY = 300.0
CLEANUP_INTERVAL = 86400.0


def cleanup_proxy(settings: Any) -> MediaProxy:
    """A proxy for a cleanup, with the media store of the settings."""
    from ..store.media import get_media_store

    return MediaProxy(settings, get_media_store(settings))


def run_retention(settings: Any, *, apply: bool, now: float | None = None) -> dict[str, int]:
    """The retention run of the timeline items (design Section 10 rules 1 and 2) with the server value of ``claire.db`` and the account values of ``client.db``. No client database gives zero items."""
    conn = dbm.connect_existing(Path(settings.db_file), readonly=True)
    try:
        server_days = int(client_store.get_reading_settings(conn)["retention_days"])
    finally:
        conn.close()
    path = client_db.client_db_path(settings.db_file)
    if not path.exists():
        return {"retention_days": server_days, "expired_items": 0}
    client = client_db.connect_client_existing(path, readonly=not apply)
    try:
        result = client_store.retention_run(client, server_days=server_days, now=time.time() if now is None else now, apply=apply)
        if apply:
            client.commit()
        return result
    finally:
        client.close()


async def run_cleanup_scheduler(settings: Any) -> None:
    """The retention run and the media eviction, one time each day in the ``api`` process. The first run is ``CLEANUP_START_DELAY`` seconds after the start. Cancel the task to stop."""
    await asyncio.sleep(CLEANUP_START_DELAY)
    while True:
        try:
            # The retention run comes first: the media cleanup then deletes the media of the items that went (design Section 10 rule 4).
            kept = await asyncio.to_thread(run_retention, settings, apply=True)
            if kept["expired_items"]:
                log.info("retention run: %d timeline item(s) older than the retention deleted", kept["expired_items"])
            result = await cleanup_proxy(settings).evict()
            if result["evicted"] or result["orphans"]:
                log.info("media cleanup: %d entry(ies) without an access in %d day(s), %d entry(ies) without an item", result["evicted"], result["evict_days"], result["orphans"])
        except Exception as exc:  # noqa: BLE001
            log.warning("media cleanup cycle failed: %s", type(exc).__name__)
        await asyncio.sleep(CLEANUP_INTERVAL)
