"""Renditions of a document in ``claire.db`` (DOCUMENT_RENDITIONS_AND_PUBLICATION_DESIGN.md Sections 5.1 and 6, version 50).

Input and output of the store: the migration of the bodies of before the templates, the row of the 대표 정리본 that each body run writes, and the bodies of the other renditions.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from claire.extract.provider import MockProvider
from claire.extract.rendition_templates import PRE_TEMPLATE_VERSION, rendition_template
from claire.ingest.pipeline import ensure_document_detail
from claire.ontology.base import Document
from claire.store import db as dbm
from claire.store import document_renditions as renditions_store


def _doc(conn, doc_id: str, *, detail: str | None, detail_format: str = "adoc") -> Document:
    doc = Document(id=doc_id, url=f"https://example.org/{doc_id}", title=doc_id, raw_text="원문 본문 " * 60, source_type="web", content_hash=f"h-{doc_id}")
    dbm.insert_document(conn, doc)
    conn.execute("UPDATE documents SET detail=?, detail_format=? WHERE id=?", (detail, detail_format, doc_id))
    return doc


def test_version_50_records_the_bodies_of_before_the_templates_as_the_rendition_standard(tmp_path: Path) -> None:
    """Owner decision O21: a body of before phase E3 is the rendition 기본, with the version ``pre-template``. Later starts add no such row."""
    path = tmp_path / "claire.db"
    conn = dbm.connect(path)
    dbm.init_db(conn)
    conn.execute("DROP TABLE document_renditions")
    _doc(conn, "with-body", detail="= 제목\n\n== 개요\n본문")
    _doc(conn, "no-body", detail=None)
    _doc(conn, "empty-body", detail="  ")
    conn.commit()
    conn.close()
    for start in range(2):
        conn = dbm.connect(path)
        try:
            dbm.init_db(conn)
            if start == 0:
                # A body that a later version writes without its row is not labelled at the next start.
                _doc(conn, "later", detail="= 나중\n\n본문")
            conn.commit()
            assert dbm.stored_schema_version(conn) == dbm.SCHEMA_VERSION
            rows = [tuple(r) for r in conn.execute("SELECT document_id, template, template_version, detail FROM document_renditions ORDER BY document_id")]
            assert rows == [("with-body", "standard", PRE_TEMPLATE_VERSION, None)]
            assert [r.template for r in renditions_store.list_renditions(conn, "with-body", 0)] == ["standard"]
            assert renditions_store.list_renditions(conn, "no-body", 0) == []
        finally:
            conn.close()


def test_the_primary_body_run_uses_the_template_standard_and_records_its_version(tmp_path: Path) -> None:
    conn = dbm.connect(tmp_path / "claire.db")
    dbm.init_db(conn)
    try:
        doc = _doc(conn, "doc-1", detail=None)
        conn.execute("INSERT INTO document_renditions (document_id, kb_id, template, template_version, model_lineage, created_at, updated_at) VALUES ('doc-1', 0, 'standard', ?, '{}', 1, 1)", (PRE_TEMPLATE_VERSION,))
        assert ensure_document_detail(conn, MockProvider(), doc, force=True) is True
        assert dbm.get_document_detail(conn, "doc-1").startswith("[mock-detail-adoc] [template: standard]")
        primary = renditions_store.get_rendition(conn, "doc-1", 0, "standard")
        assert (primary.template_version, primary.detail, primary.model_lineage["provider"], primary.created_at) == (rendition_template("standard").version, None, "mock", 1.0)
        assert primary.updated_at > 1.0
    finally:
        conn.close()


def test_the_other_renditions_keep_their_bodies_in_the_template_order(tmp_path: Path) -> None:
    conn = dbm.connect(tmp_path / "claire.db")
    dbm.init_db(conn)
    try:
        _doc(conn, "doc-1", detail="= 기본\n\n본문")
        renditions_store.record_primary(conn, "doc-1", 0, lineage={"provider": "mock", "model": "m"})
        renditions_store.store_additional(conn, "doc-1", 0, template="academic", detail="= 학술\n\n== 개요\n본문", lineage={"provider": "mock", "model": "m"})
        renditions_store.store_additional(conn, "doc-1", 0, template="concise", detail="= 간결\n\n== 핵심\n* 하나", lineage={"provider": "mock", "model": "m"})
        listed = renditions_store.list_renditions(conn, "doc-1", 0)
        assert [(r.template, r.template_version, r.primary) for r in listed] == [("standard", "standard-v1", True), ("concise", "concise-v1", False), ("academic", "academic-v1", False)]
        assert "핵심" in listed[1].detail_html
        with pytest.raises(ValueError):
            renditions_store.store_additional(conn, "doc-1", 0, template="standard", detail="x", lineage={})
        # The deletion of the document deletes its renditions.
        conn.execute("DELETE FROM documents WHERE id='doc-1'")
        assert conn.execute("SELECT COUNT(*) FROM document_renditions").fetchone()[0] == 0
    finally:
        conn.close()
