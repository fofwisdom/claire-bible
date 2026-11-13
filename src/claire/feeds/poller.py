"""Feed poller: conditional ``GET``, item storage, and backoff (FEDERATION_REMOTE_READING_DESIGN.md Section 1.3 rule 7, unit FP4-F1).

Rules:

- A poll sends ``If-None-Match`` and ``If-Modified-Since`` with the stored ``ETag`` and ``Last-Modified``. ``304`` stores nothing.
- A success sets the next poll to ``POLL_INTERVAL_SECONDS`` later and the failure count to 0.
- A failure (``429``, ``5xx``, another HTTP error, a network error, a blocked address, a body that is not a feed) adds 1 to the failure count. The next poll is ``POLL_INTERVAL_SECONDS * 2**failures`` later, at most ``BACKOFF_MAX_SECONDS``, and not before ``Retry-After``.
- The network part runs without a database connection. The results go into the database in one short transaction.
- WebSub: the poll stores the hub and the topic of the feed. This unit does not subscribe to the hub.
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ..store import db as dbm
from ..store import feeds as feeds_store
from .fetch import FeedFetcher, FetchResult
from .parse import FeedParseError, parse_feed

log = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 3600.0
BACKOFF_MAX_SECONDS = 24 * 3600.0
ON_DEMAND_LIMIT = 50
SCHEDULED_BATCH = 100
WORKERS = 4
SCHEDULER_START_DELAY = 120.0
SCHEDULER_INTERVAL = 300.0


@dataclass(frozen=True)
class PollOutcome:
    feed_id: int
    result: str  # 'updated', 'not_modified', 'error', or 'backoff'
    http_status: int | None
    added: int
    updated: int
    error: str | None
    next_poll_at: float | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def backoff_delay(failure_count: int, retry_after: float | None) -> float:
    """Seconds to the next poll after ``failure_count`` failures in sequence (1 or more)."""
    delay = min(POLL_INTERVAL_SECONDS * (2 ** min(max(failure_count, 1), 16)), BACKOFF_MAX_SECONDS)
    if retry_after is not None:
        delay = max(delay, retry_after)
    return delay


def apply_fetch_result(conn: Any, feed: dict[str, Any], fetched: FetchResult, *, now: float) -> PollOutcome:
    """Store the result of one fetch for ``feed`` (a row of ``feed_subscriptions``). The caller commits."""
    feed_id, account_id = int(feed["id"]), int(feed["account_id"])
    status = fetched.status or None
    if fetched.error is None and fetched.status == 304:
        next_at = now + POLL_INTERVAL_SECONDS
        feeds_store.record_poll(conn, feed_id, now=now, next_poll_at=next_at, status=304, error=None, failure_count=0, success=True)
        return PollOutcome(feed_id, "not_modified", 304, 0, 0, None, next_at)
    error = fetched.error
    document = None
    if error is None and fetched.status == 200 and fetched.body is not None:
        try:
            document = parse_feed(fetched.body, content_type=fetched.content_type, base_url=fetched.final_url)
        except FeedParseError as exc:
            error = f"parse:{exc.code}"
    elif error is None:
        error = f"http_{fetched.status}"
    if document is None:
        failures = int(feed.get("failure_count") or 0) + 1
        next_at = now + backoff_delay(failures, fetched.retry_after)
        feeds_store.record_poll(conn, feed_id, now=now, next_poll_at=next_at, status=fetched.status, error=error, failure_count=failures, success=False)
        return PollOutcome(feed_id, "error", status, 0, 0, error, next_at)
    stored = feeds_store.store_items(conn, account_id=account_id, feed_id=feed_id, items=list(document.items), now=now)
    next_at = now + POLL_INTERVAL_SECONDS
    metadata = {"format": document.format, "site_url": document.site_url, "websub_hub": document.websub_hub, "websub_topic": document.websub_topic, "title": document.title}
    feeds_store.record_poll(conn, feed_id, now=now, next_poll_at=next_at, status=200, error=None, failure_count=0, success=True, etag=fetched.etag, last_modified=fetched.last_modified, metadata=metadata)
    return PollOutcome(feed_id, "updated", 200, stored.added, stored.updated, None, next_at)


def poll_feeds(db_file: Path | str, feeds: list[dict[str, Any]], *, fetcher: FeedFetcher, now: float | None = None, workers: int = WORKERS) -> list[PollOutcome]:
    """Fetch the feeds in parallel, then store all results in one transaction."""
    if not feeds:
        return []
    when = time.time() if now is None else now

    def _fetch(feed: dict[str, Any]) -> FetchResult:
        return fetcher.fetch(str(feed["url"]), etag=feed.get("etag"), last_modified=feed.get("last_modified"), now=when)

    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(feeds)))) as pool:
        results = list(pool.map(_fetch, feeds))
    conn = dbm.connect_existing(db_file)
    try:
        outcomes = []
        for feed, fetched in zip(feeds, results, strict=True):
            # The feed can be removed during the fetch. Then the result is not stored.
            if conn.execute("SELECT 1 FROM feed_subscriptions WHERE id=?", (feed["id"],)).fetchone() is None:
                continue
            outcomes.append(apply_fetch_result(conn, feed, fetched, now=when))
        conn.commit()
        return outcomes
    finally:
        conn.close()


def poll_account(db_file: Path | str, account_id: int, *, fetcher: FeedFetcher, feed_id: int | None = None, now: float | None = None, limit: int = ON_DEMAND_LIMIT) -> dict[str, Any]:
    """Poll on demand: the feeds of the account (at most ``limit``, oldest poll first) or one feed. A feed in backoff (a failure and a next poll in the future) is not fetched; its outcome is ``backoff``. An unknown ``feed_id`` gives ``{"found": False}``."""
    when = time.time() if now is None else now
    conn = dbm.connect_existing(db_file, readonly=True)
    try:
        feeds = feeds_store.account_feeds_for_poll(conn, account_id, limit=limit, feed_id=feed_id)
        total = feeds_store.count_feeds(conn, account_id)
    finally:
        conn.close()
    if feed_id is not None and not feeds:
        return {"found": False}
    waiting = [f for f in feeds if int(f["failure_count"] or 0) > 0 and f["next_poll_at"] is not None and float(f["next_poll_at"]) > when]
    ready = [f for f in feeds if f not in waiting]
    outcomes = poll_feeds(db_file, ready, fetcher=fetcher, now=when)
    outcomes += [PollOutcome(int(f["id"]), "backoff", f["last_status"], 0, 0, f["last_error"], f["next_poll_at"]) for f in waiting]
    return {"found": True, "polled": len(ready), "not_polled": max(total - len(feeds), 0) if feed_id is None else 0, "outcomes": [o.to_dict() for o in outcomes]}


def poll_due(db_file: Path | str, *, fetcher: FeedFetcher, now: float | None = None, limit: int = SCHEDULED_BATCH, account_id: int | None = None) -> list[PollOutcome]:
    """Poll the feeds that are due (``next_poll_at`` at or before now). The scheduler of the ``api`` process and ``claire feeds poll`` use this function."""
    when = time.time() if now is None else now
    conn = dbm.connect_existing(db_file, readonly=True)
    try:
        feeds = feeds_store.due_feeds(conn, now=when, limit=limit, account_id=account_id)
    finally:
        conn.close()
    return poll_feeds(db_file, feeds, fetcher=fetcher, now=when)


async def run_scheduler(settings: Any) -> None:
    """Poll due feeds in the ``api`` process. The first run is ``SCHEDULER_START_DELAY`` seconds after the start. Cancel the task to stop."""
    import asyncio

    await asyncio.sleep(SCHEDULER_START_DELAY)
    while True:
        try:
            outcomes = await asyncio.to_thread(poll_due, settings.db_file, fetcher=FeedFetcher.from_settings(settings))
            if outcomes:
                log.info("feed poll: %d feed(s), %d new item(s), %d error(s)", len(outcomes), sum(o.added for o in outcomes), sum(1 for o in outcomes if o.result == "error"))
        except Exception as exc:  # noqa: BLE001
            log.warning("feed poll cycle failed: %s", type(exc).__name__)
        await asyncio.sleep(SCHEDULER_INTERVAL)
