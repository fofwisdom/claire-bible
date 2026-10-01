"""Asciidoctor reveal.js presentation pipeline unit & integration tests."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from starlette.testclient import TestClient

from claire.config import Settings
from claire.presentation import (
    PresentationService,
    compile_presentation_html,
    find_asciidoctor_executable,
    preprocess_adoc_to_slides,
)
from claire.store import db as dbm


def _memory_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    dbm.init_db(conn)
    return conn


def test_preprocess_adoc_to_slides_basic():
    raw_adoc = """= Sample Document
:toc: left

== Section 1
This is a sample paragraph.

=== Sub-section 1.1
* Point 1
* Point 2
"""
    result = preprocess_adoc_to_slides(
        raw_adoc,
        title="Sample Presentation",
        author="Claire Tester",
        summary="A brief executive summary of this topic.",
        theme="night",
        transition="slide",
    )

    # Check reveal.js headers
    assert "= Sample Presentation" in result
    assert ":author: Claire Tester" in result
    assert ":revealjs_theme: night" in result
    assert ":revealjs_transition: slide" in result
    assert ":revealjs_slideNumber: c/t" in result

    # Check executive summary slide insertion
    assert "== 핵심 요약 (Executive Summary)" in result
    assert "A brief executive summary of this topic." in result

    # Check 2D hierarchy preservation
    assert "== Section 1" in result
    assert "=== Sub-section 1.1" in result


def test_preprocess_adoc_to_slides_chunking():
    # Long text in a single section to trigger <<< chunking
    long_paragraphs = "\n\n".join([f"Paragraph {i}: " + ("content line " * 8) for i in range(12)])
    raw_adoc = f"""== Long Section
{long_paragraphs}
"""
    result = preprocess_adoc_to_slides(raw_adoc, title="Chunk Test")
    assert "<<<" in result


def test_presentation_db_lifecycle():
    conn = _memory_db()

    # 1. Insert dummy document
    doc_id = "doc_test123"
    conn.execute(
        """
        INSERT INTO documents (id, title, url, detail, detail_format)
        VALUES (?, 'Test Title', 'https://example.com/test', '= Doc Detail\n\n== S1\nBody', 'adoc')
        """,
        (doc_id,),
    )
    conn.commit()

    # 2. Save presentation record
    dbm.save_document_presentation(
        conn,
        document_id=doc_id,
        content_hash="hash123",
        cache_key="cache123",
        file_path="/tmp/test123.html",
        file_size=1024,
        theme="night",
        transition="slide",
        slide_count=5,
        status="ready",
        compile_duration_ms=250,
    )

    record = dbm.get_document_presentation(conn, doc_id)
    assert record is not None
    assert record["document_id"] == doc_id
    assert record["content_hash"] == "hash123"
    assert record["theme"] == "night"
    assert record["slide_count"] == 5
    assert record["status"] == "ready"
    assert record["compile_duration_ms"] == 250

    # 3. Test status update
    dbm.update_document_presentation_status(conn, doc_id, "compiling")
    updated = dbm.get_document_presentation(conn, doc_id)
    assert updated["status"] == "compiling"

    # 4. Invalidate when set_document_detail is called
    dbm.set_document_detail(conn, doc_id, "New Detail Text", format="adoc")
    after_detail_update = dbm.get_document_presentation(conn, doc_id)
    assert after_detail_update["status"] == "stale"

    # 5. Delete
    assert dbm.delete_document_presentation(conn, doc_id) is True
    assert dbm.get_document_presentation(conn, doc_id) is None


@pytest.mark.asyncio
async def test_presentation_service_with_real_or_mock_compiler(tmp_path: Path):
    conn = _memory_db()
    doc_id = "doc_presentation_service"

    detail_adoc = """= Service Test
== Chapter 1: Introduction
Welcome to Claire Bible Presentation.

=== Technical Deep Dive
* Key concept 1
* Key concept 2
"""
    conn.execute(
        """
        INSERT INTO documents (id, title, url, detail, detail_format)
        VALUES (?, 'Service Test', 'https://example.com', ?, 'adoc')
        """,
        (doc_id, detail_adoc),
    )
    conn.commit()

    svc = PresentationService(data_dir=tmp_path)

    # If asciidoctor is installed on host, test real compilation
    engine_available = PresentationService.is_engine_available()
    if engine_available:
        result = await svc.get_or_create_presentation(conn, doc_id)
        assert result["status"] == "ready"
        assert result["slide_count"] >= 2
        file_path = Path(result["file_path"])
        assert file_path.exists()
        assert file_path.stat().st_size > 500

        # Cached call should return immediately
        cached = await svc.get_or_create_presentation(conn, doc_id)
        assert cached["content_hash"] == result["content_hash"]
    else:
        # Test with mock compiler
        with patch("claire.presentation.service.compile_presentation_html") as mock_compile:
            dummy_file = svc.get_presentation_file_path(doc_id)
            dummy_file.write_text("<html>Mock reveal.js</html>")
            mock_compile.return_value = (dummy_file, 120)

            result = await svc.get_or_create_presentation(conn, doc_id)
            assert result["status"] == "ready"
            assert result["file_path"] == str(dummy_file)


def test_api_presentation_endpoints(tmp_path: Path):
    from claire.api.server import create_app

    # Create temporary database with test document
    db_file = tmp_path / "claire_test.db"
    conn = sqlite3.connect(str(db_file))
    conn.row_factory = sqlite3.Row
    dbm.init_db(conn)

    doc_id = "doc_api_test"
    detail_adoc = """= API Test
== Overview
A test slide for API routes.
"""
    conn.execute(
        """
        INSERT INTO documents (id, title, url, detail, detail_format)
        VALUES (?, 'API Test', 'https://example.com/api', ?, 'adoc')
        """,
        (doc_id, detail_adoc),
    )
    conn.commit()
    conn.close()

    owner_token = "owner-" + ("o" * 32)
    owner_headers = {"Authorization": f"Bearer {owner_token}"}

    s = Settings(
        db_path=str(db_file),
        data_dir=tmp_path,
        inject_token=owner_token,
        render_format="adoc",
    )
    app = create_app(s)
    with TestClient(app, base_url=s.public_url, raise_server_exceptions=False) as client:
        # 1. Check status before creation
        res = client.get(f"/document/presentation?id={doc_id}", headers=owner_headers)
        assert res.status_code == 200
        assert res.json()["status"] == "not_created"

        # 2. Trigger generation
        with patch("claire.presentation.service.compile_presentation_html") as mock_compile:
            target_file = tmp_path / "presentations" / f"{doc_id}.html"
            target_file.parent.mkdir(parents=True, exist_ok=True)
            target_file.write_text("<!DOCTYPE html><html><body><h1>Slide Test</h1></body></html>")
            mock_compile.return_value = (target_file, 150)

            gen_res = client.post(
                f"/document/presentation/generate?id={doc_id}",
                headers=owner_headers,
            )
            assert gen_res.status_code == 200
            assert gen_res.json()["status"] == "ready"

            # 3. Check status after creation
            status_res = client.get(
                f"/document/presentation?id={doc_id}",
                headers=owner_headers,
            )
            assert status_res.status_code == 200
            assert status_res.json()["status"] == "ready"

            # 4. Access presentation page (public route)
            page_res = client.get(f"/p/presentation?id={doc_id}")
            assert page_res.status_code == 200
            assert "Slide Test" in page_res.text
