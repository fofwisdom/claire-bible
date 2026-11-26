"""Renditions of a document, 정리본 (DOCUMENT_RENDITIONS_AND_PUBLICATION_DESIGN.md Sections 5.1 and 6; claire.db version 50).

One document has at most one rendition for each rendition template. The body of the 대표 정리본 (template ``standard``) stays in ``documents.detail``, so that the readers of the body do not change; its row here has no body and keeps its template version and its model. The rows of the other templates have their bodies.
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from typing import Any

from ..extract.rendition_templates import PRIMARY_RENDITION_TEMPLATE, RENDITION_TEMPLATE_IDS, rendition_template

__all__ = ["Rendition", "delete_additional", "get_rendition", "list_renditions", "record_primary", "store_additional"]

_COLUMNS = "document_id, kb_id, template, template_version, detail, detail_html, model_lineage, created_at, updated_at"


@dataclass(frozen=True, slots=True)
class Rendition:
    """One rendition. ``detail`` and ``detail_html`` are ``None`` for the 대표 정리본: its body is in ``documents``."""

    document_id: str
    kb_id: int
    template: str
    template_version: str
    detail: str | None
    detail_html: str | None
    model_lineage: dict[str, Any]
    created_at: float
    updated_at: float

    @property
    def primary(self) -> bool:
        return self.template == PRIMARY_RENDITION_TEMPLATE


def _row(row: Any) -> Rendition:
    try:
        lineage = json.loads(row[6] or "{}")
    except ValueError:
        lineage = {}
    return Rendition(str(row[0]), int(row[1]), str(row[2]), str(row[3]), row[4], row[5], lineage if isinstance(lineage, dict) else {}, float(row[7]), float(row[8]))


def _upsert(conn: sqlite3.Connection, document_id: str, kb_id: int, template: str, version: str, detail: str | None, detail_html: str | None, lineage: dict[str, Any], now: float) -> None:
    conn.execute(
        f"INSERT INTO document_renditions ({_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(document_id, template) DO UPDATE SET template_version=excluded.template_version, detail=excluded.detail, detail_html=excluded.detail_html, model_lineage=excluded.model_lineage, updated_at=excluded.updated_at",
        (document_id, int(kb_id), template, version, detail, detail_html, json.dumps(lineage, ensure_ascii=False, sort_keys=True), now, now),
    )


def record_primary(conn: sqlite3.Connection, document_id: str, kb_id: int, *, lineage: dict[str, Any], now: float | None = None) -> None:
    """Record that ``documents.detail`` is now a body of the template ``standard`` in its current version. The caller wrote the body."""
    _upsert(conn, document_id, kb_id, PRIMARY_RENDITION_TEMPLATE, rendition_template(PRIMARY_RENDITION_TEMPLATE).version, None, None, lineage, time.time() if now is None else now)


def store_additional(conn: sqlite3.Connection, document_id: str, kb_id: int, *, template: str, detail: str, lineage: dict[str, Any], now: float | None = None) -> None:
    """Store the body of a rendition that is not the 대표 정리본. The body gets the same cleaning and HTML as ``documents.detail``."""
    from ..core.ports import get_document_renderer
    from ..extract.prompts import remove_leading_original_link

    chosen = rendition_template(template)
    if chosen.id == PRIMARY_RENDITION_TEMPLATE:
        raise ValueError("the body of the 대표 정리본 is documents.detail")
    body = remove_leading_original_link(detail).strip()
    if not body:
        raise ValueError("empty rendition body")
    at = time.time() if now is None else now
    _upsert(conn, document_id, kb_id, chosen.id, chosen.version, body, get_document_renderer().render_to_html(body, format="adoc"), lineage, at)
    _sync_objects(conn, document_id, kb_id, at)


def _sync_objects(conn: sqlite3.Connection, document_id: str, kb_id: int, now: float) -> None:
    """A published document gets, changes, or loses the object of a rendition with its rendition (phase E4b)."""
    from . import federation as federation_store

    federation_store.sync_rendition_objects(conn, kb_id=kb_id, document_id=document_id, now=now)


def delete_additional(conn: sqlite3.Connection, document_id: str, kb_id: int, template: str) -> bool:
    """Delete one rendition that is not the 대표 정리본. True when a row was deleted."""
    if template == PRIMARY_RENDITION_TEMPLATE:
        raise ValueError("the 대표 정리본 is not deleted alone")
    deleted = conn.execute("DELETE FROM document_renditions WHERE document_id=? AND kb_id=? AND template=?", (document_id, int(kb_id), template)).rowcount > 0
    if deleted:
        _sync_objects(conn, document_id, kb_id, time.time())
    return deleted


def list_renditions(conn: sqlite3.Connection, document_id: str, kb_id: int) -> list[Rendition]:
    """The renditions of the document in the template order (기본, 간결, 학술). The 대표 정리본 is in the list only while ``documents.detail`` has a body."""
    rows = conn.execute(
        f"SELECT r.{_COLUMNS.replace(', ', ', r.')} FROM document_renditions r JOIN documents d ON d.id = r.document_id AND d.kb_id = r.kb_id "
        "WHERE r.document_id=? AND r.kb_id=? AND (r.template != ? OR (d.detail IS NOT NULL AND trim(d.detail) != ''))",
        (document_id, int(kb_id), PRIMARY_RENDITION_TEMPLATE),
    ).fetchall()
    order = {template: index for index, template in enumerate(RENDITION_TEMPLATE_IDS)}
    # A template that this version does not know stays in the table and is not shown (unknown data stays).
    return sorted((_row(r) for r in rows if str(r[2]) in order), key=lambda r: order[r.template])


def get_rendition(conn: sqlite3.Connection, document_id: str, kb_id: int, template: str) -> Rendition | None:
    """One rendition of the document, or ``None``."""
    return next((r for r in list_renditions(conn, document_id, kb_id) if r.template == template), None)
