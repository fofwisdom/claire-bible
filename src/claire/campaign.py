"""Campaign message at ingest (SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md Section 8.7, CT18; unit CT-11).

The result of an ingest job has ``notices``:
- ``{"code": "fiction_like_document", "document", "theme", "theme_label", "message"}`` when the theme match of the new document is a ``fictional`` theme (rule 2).
- ``{"code": "fiction_standing_message", "message"}`` with the first ingest of each UTC day for each account (rule 3). Telegram, MCP, and the CLI show it. The WebUI shows its standing message on each ingest form and does not use this notice.

``message`` is the English text (for MCP and the CLI). The WebUI and Telegram use the catalog keys ``campaign.*``.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from typing import Any

from .core import ids
from .i18n import t
from .store import kb_collab
from .store import themes as themes_store

log = logging.getLogger("claire.campaign")

FICTION_LIKE = "fiction_like_document"
STANDING = "fiction_standing_message"


def notice_text(notice: dict[str, Any], *, locale: str | None = "en") -> str:
    """The text of one notice. ``locale`` None: the active locale (Telegram)."""
    if notice.get("code") == FICTION_LIKE:
        return t("campaign.fiction_like", locale=locale, theme=notice.get("theme_label") or notice.get("theme") or "")
    return t("campaign.standing", locale=locale)


def notices_for_ingest(conn: sqlite3.Connection, *, kb_id: int, document_id: str, now: float | None = None) -> list[dict[str, Any]]:
    """The notices for a new document of a personal knowledge base, after its theme match. It stores the day of the standing message; the caller commits. A failure here never fails the ingest: the result is then an empty list."""
    try:
        row = conn.execute("SELECT is_personal, account_id FROM knowledge_bases WHERE id=?", (int(kb_id),)).fetchone()
        if row is None or not row["is_personal"] or row["account_id"] is None:
            return []
        from . import reputation

        ts = time.time() if now is None else now
        account_id = int(row["account_id"])
        notices: list[dict[str, Any]] = []
        match = themes_store.get_document_match(conn, document_id)
        if match is not None and match["theme_id"] is not None and match["knowledge_type"] == "fictional":
            theme = themes_store.get_theme(conn, int(match["theme_id"]))
            if theme is not None:
                notice = {"code": FICTION_LIKE, "document": document_id, "theme": ids.format_public_id(int(theme["number"])), "theme_label": theme["label"]}
                notices.append({**notice, "message": notice_text(notice)})
        reputation.refresh(conn, account_id, now=ts)  # the new document changes the fiction share
        if kb_collab.take_campaign_day(conn, account_id, time.strftime("%Y-%m-%d", time.gmtime(ts))):
            notices.append({"code": STANDING, "message": notice_text({"code": STANDING})})
        return notices
    except Exception as exc:  # noqa: BLE001
        log.warning("campaign notices of document %s failed: %s", document_id, type(exc).__name__)
        return []
