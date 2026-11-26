"""The scheduled work of the opinions of published objects (DOCUMENT_RENDITIONS_AND_PUBLICATION_DESIGN.md Section 5.11 C4 and E, phases E7c and E8).

- Each run deletes the opinions and the fetch of each own object that is gone. A retract, a gone rendition, a private knowledge base, and an account purge all end in a gone object, so one rule covers all of them. Until the run, the routes give nothing for a gone object: the opinions route and the ``replies`` collection need a published object.
- One time each day (the time of the last refresh is ``opinions_refreshed_at`` in ``client_meta``), the run asks for a fetch of the opinions of each own object with activity in the last 7 days (``opinions.daily_refresh``). Mastodon fetches only when a person opens a post; this refresh keeps the 의견 tab current before the next visit.

The task runs in the ``api`` process, as the media cleanup does.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any

from ..store import client_db
from ..store import db as dbm
from .opinions import daily_refresh, purge_gone_objects

__all__ = ["INTERVAL_SECONDS", "REFRESH_SECONDS", "START_DELAY_SECONDS", "run_once", "run_scheduler"]

log = logging.getLogger(__name__)

START_DELAY_SECONDS = 120.0
INTERVAL_SECONDS = 3600.0
REFRESH_SECONDS = 24 * 3600.0
_REFRESH_KEY = "opinions_refreshed_at"


def run_once(settings: Any, *, now: float | None = None) -> dict[str, int]:
    """One run: ``removed`` opinions of gone objects, and ``refreshed`` objects whose fetch waits after the daily refresh (0 when the last refresh is less than one day old). No client database or no public URL gives zero."""
    origin = str(getattr(settings, "public_url", "") or "").rstrip("/")
    path = client_db.client_db_path(settings.db_file)
    if not origin or not path.exists():
        return {"removed": 0, "refreshed": 0}
    at = time.time() if now is None else now
    conn = dbm.connect_existing(Path(settings.db_file), readonly=True)
    client = client_db.connect_client_existing(path)
    try:
        removed = purge_gone_objects(conn, client, origin=origin)
        refreshed = 0
        row = client.execute("SELECT value FROM client_meta WHERE key=?", (_REFRESH_KEY,)).fetchone()
        try:
            last = float(row[0]) if row is not None else None
        except ValueError:
            last = None
        if last is None or at - last >= REFRESH_SECONDS or at < last:
            refreshed = daily_refresh(conn, client, origin=origin, now=at)
            client.execute("INSERT INTO client_meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (_REFRESH_KEY, repr(at)))
        client.commit()
        return {"removed": removed, "refreshed": refreshed}
    finally:
        client.close()
        conn.close()


async def run_scheduler(settings: Any) -> None:
    """Run ``run_once`` each ``INTERVAL_SECONDS``; the first run is ``START_DELAY_SECONDS`` after the start. Cancel the task to stop."""
    await asyncio.sleep(START_DELAY_SECONDS)
    while True:
        try:
            result = await asyncio.to_thread(run_once, settings)
            if result["removed"] or result["refreshed"]:
                log.info("opinions: %d opinion(s) of gone objects deleted, %d object(s) to fetch", result["removed"], result["refreshed"])
        except Exception as exc:  # noqa: BLE001
            log.warning("opinion run failed: %s", type(exc).__name__)
        await asyncio.sleep(INTERVAL_SECONDS)
