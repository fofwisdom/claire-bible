"""The scheduled work of the opinions of published objects (DOCUMENT_RENDITIONS_AND_PUBLICATION_DESIGN.md Section 5.11 C4, phase E7c).

Each run deletes the opinions and the fetch of each own object that is gone. A retract, a gone rendition, a private knowledge base, and an account purge all end in a gone object, so one rule covers all of them. Until the run, the routes give nothing for a gone object: the opinions route and the ``replies`` collection need a published object.

The task runs in the ``api`` process, as the media cleanup does.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from ..store import client_db
from ..store import db as dbm
from .opinions import purge_gone_objects

__all__ = ["INTERVAL_SECONDS", "START_DELAY_SECONDS", "run_once", "run_scheduler"]

log = logging.getLogger(__name__)

START_DELAY_SECONDS = 120.0
INTERVAL_SECONDS = 3600.0


def run_once(settings: Any) -> dict[str, int]:
    """One run. No client database or no public URL gives zero."""
    origin = str(getattr(settings, "public_url", "") or "").rstrip("/")
    path = client_db.client_db_path(settings.db_file)
    if not origin or not path.exists():
        return {"removed": 0}
    conn = dbm.connect_existing(Path(settings.db_file), readonly=True)
    client = client_db.connect_client_existing(path)
    try:
        removed = purge_gone_objects(conn, client, origin=origin)
        client.commit()
        return {"removed": removed}
    finally:
        client.close()
        conn.close()


async def run_scheduler(settings: Any) -> None:
    """Run ``run_once`` each ``INTERVAL_SECONDS``; the first run is ``START_DELAY_SECONDS`` after the start. Cancel the task to stop."""
    await asyncio.sleep(START_DELAY_SECONDS)
    while True:
        try:
            result = await asyncio.to_thread(run_once, settings)
            if result["removed"]:
                log.info("opinions: %d opinion(s) of gone objects deleted", result["removed"])
        except Exception as exc:  # noqa: BLE001
            log.warning("opinion run failed: %s", type(exc).__name__)
        await asyncio.sleep(INTERVAL_SECONDS)
