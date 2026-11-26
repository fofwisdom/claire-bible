"""Additional renditions of an ingest request (DOCUMENT_RENDITIONS_AND_PUBLICATION_DESIGN.md Section 5.3 rule 1).

Each ingest makes the 대표 정리본 (template ``standard``). The request selects the other templates with the required field ``renditions`` (a list, which can be empty: no hidden default). The web routes and API v1 use this module.
"""

from __future__ import annotations

from typing import Any

from ..extract.rendition_templates import ADDITIONAL_RENDITION_TEMPLATES
from ..store import db as dbm
from . import client_extraction as ce

__all__ = ["parse_rendition_selection", "run_ingest_renditions"]


def parse_rendition_selection(raw: Any) -> list[str]:
    """The field ``renditions`` of an ingest request: a list of distinct values of ``concise`` and ``academic``. ``ValueError`` for a missing field or another value."""
    if not isinstance(raw, list) or any(not isinstance(t, str) or t not in ADDITIONAL_RENDITION_TEMPLATES for t in raw) or len(set(raw)) != len(raw):
        raise ValueError(f"renditions must be a list of distinct values of {list(ADDITIONAL_RENDITION_TEMPLATES)}")
    return list(raw)


def run_ingest_renditions(*, kb_svc: Any, db_file: Any, kb_id: int, report: Any, templates: list[str], extraction_mode: str, effort: str | None) -> list[dict[str, str]]:
    """The additional renditions after the ingest made the document and its 대표 정리본: one item ``{"template", "state", "error"}`` for each template.

    With the server API (``provider``) they run now (``IngestService.add_renditions``). With an agent client (``client``) they go into the staged task (state ``queued``), and the client writes them with the extraction. An ingest without a document gives ``failed`` with ``no_document``.
    """
    if not templates:
        return []
    doc_id = getattr(report, "document_id", None)
    if not doc_id or getattr(report, "error", None):
        return [{"template": t, "state": "failed", "error": "no_document"} for t in templates]
    if extraction_mode == "client":
        conn = dbm.connect_existing(db_file)
        try:
            ce.request_client_renditions(conn, kb_id=kb_id, document_id=doc_id, templates=templates)
            conn.commit()
        except ce.ClientExtractionError as exc:
            return [{"template": t, "state": "failed", "error": exc.code} for t in templates]
        finally:
            conn.close()
        return [{"template": t, "state": "queued", "error": ""} for t in templates]
    return kb_svc.add_renditions(doc_id, templates, effort=effort)
