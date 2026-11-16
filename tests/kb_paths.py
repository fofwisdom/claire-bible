"""Test helpers for the selection of a knowledge base by path (design Section 5.2, CT23).

Tests know the internal ID of a knowledge base. The HTTP API takes only paths and numbers, so these helpers give the number, the pinned path, and a seed through the service (HTTP ingest writes only to the personal knowledge base).
"""

from __future__ import annotations

from typing import Any

from claire import ids
from claire.store import db as dbm


def kb_number(settings: Any, kb_id: int) -> str:
    conn = dbm.connect_existing(settings.db_file)
    try:
        row = conn.execute("SELECT number FROM knowledge_bases WHERE id=?", (int(kb_id),)).fetchone()
        if row is not None and row[0] is None:
            # A test row without a number gets one, as the server does at the start.
            dbm.backfill_theme_numbers(conn)
            conn.commit()
            row = conn.execute("SELECT number FROM knowledge_bases WHERE id=?", (int(kb_id),)).fetchone()
    finally:
        conn.close()
    assert row is not None and row[0] is not None, f"knowledge base {kb_id} has no number"
    return ids.format_public_id(int(row[0]))


def kb_url(settings: Any, kb_id: int, route: str) -> str:
    """The pinned path of a route (``/themes/{number}{route}``). The server sends 308 to the path of the owner in network mode."""
    return f"/themes/{kb_number(settings, kb_id)}{route}"


def seed(settings: Any, kb_id: int, payload: str, **kwargs: Any) -> Any:
    """Ingest into a knowledge base with its service and return the report."""
    from claire.ingest.service import IngestService
    from claire.store.knowledge_base import KnowledgeBaseManager

    tm = KnowledgeBaseManager(settings)
    return IngestService(tm.get_settings_for_kb(int(kb_id), settings), kb_id=int(kb_id)).ingest(payload, source="cli", expand_max=0, **kwargs)
