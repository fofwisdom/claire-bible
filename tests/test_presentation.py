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
    assert ":revealjs_center: false" in result
    assert ":revealjs_width: 1280" in result
    assert ":revealjs_height: 720" in result
    assert ":revealjs_margin: 0.04" in result

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

            # 5. Access presentation page via share token
            share_res = client.post("/share", json={"doc_id": doc_id}, headers=owner_headers)
            assert share_res.status_code == 200
            share_token = share_res.json()["token"]
            token_page_res = client.get(f"/p/presentation?s={share_token}")
            assert token_page_res.status_code == 200
            assert "Slide Test" in token_page_res.text


def test_presentation_cli_generate_and_status(tmp_path: Path):
    from claire.cli import cmd_presentation
    from argparse import Namespace

    db_file = tmp_path / "test_cli.db"
    conn = dbm.connect(db_file)
    dbm.init_db(conn)
    doc_id = "doc_cli_pres"
    conn.execute(
        "INSERT INTO documents (id, title, url, canonical_url, detail, fetched_at) VALUES (?, ?, ?, ?, ?, ?)",
        (doc_id, "CLI Presentation Title", "https://example.com/pres", "https://example.com/pres", "= Sample Title\n== Section 1\nContent", 1700000000),
    )
    conn.commit()
    conn.close()

    with patch("claire.cli.get_effective_settings") as mock_settings:
        s = Settings(db_path=str(db_file), data_dir=tmp_path, render_format="adoc")
        mock_settings.return_value = (s, None)

        with patch("claire.presentation.service.compile_presentation_html") as mock_compile:
            target_file = tmp_path / "presentations" / f"{doc_id}.html"
            target_file.parent.mkdir(parents=True, exist_ok=True)
            target_file.write_text("<!DOCTYPE html><html><body><h1>Slide Test</h1></body></html>")
            mock_compile.return_value = (target_file, 120)

            # 1. Generate via doc_id
            args_gen = Namespace(action="generate", target=doc_id, doc_id_flag="", all=False, theme="night", transition="slide", force=False, json=True)
            ret_gen = cmd_presentation(args_gen)
            assert ret_gen == 0

            # 2. Check status via status subcommand
            args_status = Namespace(action="status", target=doc_id, doc_id_flag="", json=True)
            ret_status = cmd_presentation(args_status)
            assert ret_status == 0

            # 3. Generate via keyword search
            args_kw = Namespace(action="generate", target="CLI Presentation", doc_id_flag="", all=False, theme="night", transition="slide", force=False, json=True)
            ret_kw = cmd_presentation(args_kw)
            assert ret_kw == 0


def test_compose_presentation_prompt_adoc():
    from claire.extract.prompts import PRESENTATION_PROMPT_VERSION, compose_presentation_prompt_adoc

    assert PRESENTATION_PROMPT_VERSION == "pres-v1"
    prompt = compose_presentation_prompt_adoc(
        title="KV-Cache Architecture",
        detail="= KV-Cache\n\n== Overview\nDetails about paged attention...",
        summary="Paged attention manages KV cache blocks.",
        author="Claire Architect",
        published_at="2026-10-01",
        focus="Memory optimization",
        slide_budget=8,
        theme="night",
        transition="slide",
    )

    assert "Asciidoctor reveal.js 전용 프레젠테이션 슬라이드 덱" in prompt
    assert "KV-Cache Architecture" in prompt
    assert "Memory optimization" in prompt
    assert "[.notes]" in prompt
    assert ":revealjs_theme: night" in prompt
    assert ":revealjs_transition: slide" in prompt


def test_mock_provider_compose_presentation():
    from claire.extract.provider import MockProvider
    from claire.ontology.base import Document

    provider = MockProvider()
    doc = Document(
        id="doc_mock_pres",
        title="Modern Vector Databases",
        url="https://example.com/vector-db",
        raw_text="Vector search involves HNSW graphs, PQ quantization, and IVF indexes.",
        author="Claire Team",
        published_at="2026-10-01",
    )

    deck = provider.compose_presentation(
        doc,
        summary="A comprehensive survey on vector indexing.",
        focus="HNSW scaling",
        slide_budget=10,
        theme="night",
        transition="slide",
    )

    assert "= Modern Vector Databases [초점: HNSW scaling]" in deck
    assert ":revealjs_theme: night" in deck
    assert ":revealjs_transition: slide" in deck
    assert "== 1. 아젠다 및 개요 (Agenda & Overview)" in deck
    assert "== 2. 시스템 아키텍처 (Architecture)" in deck
    assert "=== 2.1 핵심 파이프라인 구조" in deck
    assert "=== 2.2 메커니즘 심층 분석" in deck
    assert "== 3. 비교 분석 및 지표 (Evaluation)" in deck
    assert "|===" in deck
    assert "== 4. 주요 결론 및 질의응답 (Conclusion & Q&A)" in deck
    assert "[.notes]" in deck
    assert "[quote, Claire Team]" in deck


def test_presentation_db_authoring_lifecycle():
    conn = _memory_db()
    doc_id = "doc_authoring_1"
    conn.execute(
        "INSERT INTO documents (id, title, url, detail, fetched_at) VALUES (?, ?, ?, ?, ?)",
        (doc_id, "Authoring Test", "https://example.com/auth", "= Doc Detail\nContent", 1700000000),
    )
    conn.commit()

    # 1. Save authored presentation adoc
    test_adoc = "= Title\n:revealjs_theme: night\n\n== Slide 1\n* Bullet 1\n\n[.notes]\n--\n* Note 1\n--"
    dbm.save_presentation_adoc(
        conn,
        document_id=doc_id,
        presentation_adoc=test_adoc,
        content_hash="content_hash_123",
        adoc_hash="adoc_hash_abc",
        authoring_provider="gemini",
        authoring_model="gemini-2.5-flash",
        prompt_version="pres-v1",
        compose_duration_ms=1850,
        slide_count=5,
        theme="night",
        transition="slide",
        cache_key="key123",
        file_path="/tmp/test_pres.html",
        status="authored",
    )

    # 2. Verify retrieval of presentation adoc and metadata
    adoc_retrieved = dbm.get_presentation_adoc(conn, doc_id)
    assert adoc_retrieved == test_adoc

    meta = dbm.get_document_presentation(conn, doc_id)
    assert meta is not None
    assert meta["presentation_adoc"] == test_adoc
    assert meta["adoc_hash"] == "adoc_hash_abc"
    assert meta["authoring_provider"] == "gemini"
    assert meta["authoring_model"] == "gemini-2.5-flash"
    assert meta["compose_duration_ms"] == 1850
    assert meta["status"] == "authored"

    # 3. Update with compiled output
    dbm.save_document_presentation(
        conn,
        document_id=doc_id,
        content_hash="content_hash_123",
        cache_key="key123",
        file_path="/tmp/test_pres.html",
        file_size=2048,
        theme="night",
        transition="slide",
        slide_count=5,
        status="ready",
        compile_duration_ms=120,
    )

    meta_compiled = dbm.get_document_presentation(conn, doc_id)
    assert meta_compiled is not None
    assert meta_compiled["status"] == "ready"
    assert meta_compiled["file_size"] == 2048
    assert meta_compiled["compile_duration_ms"] == 120
    # Preserved adoc and authoring fields
    assert meta_compiled["presentation_adoc"] == test_adoc
    assert meta_compiled["authoring_provider"] == "gemini"
    assert meta_compiled["compose_duration_ms"] == 1850


def test_presentation_cli_compose_and_compile(tmp_path: Path):
    from claire.cli import cmd_presentation
    from argparse import Namespace

    db_file = tmp_path / "test_cli2.db"
    conn = dbm.connect(db_file)
    dbm.init_db(conn)
    doc_id = "doc_cli_compose"
    conn.execute(
        "INSERT INTO documents (id, title, url, canonical_url, detail, fetched_at) VALUES (?, ?, ?, ?, ?, ?)",
        (doc_id, "CLI Compose Title", "https://example.com/comp", "https://example.com/comp", "= Doc Title\n== Chapter 1\nDetail content", 1700000000),
    )
    conn.commit()
    conn.close()

    with patch("claire.cli.get_effective_settings") as mock_settings:
        s = Settings(db_path=str(db_file), data_dir=tmp_path, render_format="adoc")
        mock_settings.return_value = (s, None)

        with patch("claire.presentation.service.compile_presentation_html") as mock_compile:
            target_file = tmp_path / "presentations" / f"{doc_id}.html"
            target_file.parent.mkdir(parents=True, exist_ok=True)
            target_file.write_text("<!DOCTYPE html><html><body><h1>Slide Deck</h1></body></html>")
            mock_compile.return_value = (target_file, 110)

            # 1. Compose presentation
            args_comp = Namespace(
                action="compose",
                target=doc_id,
                doc_id_flag="",
                focus="Speed optimization",
                slide_budget=8,
                theme="night",
                transition="slide",
                no_compile=False,
                json=True,
            )
            ret_comp = cmd_presentation(args_comp)
            assert ret_comp == 0

            # 2. Check adoc file exists on disk
            adoc_file = tmp_path / "presentations" / f"{doc_id}.adoc"
            assert adoc_file.exists()
            assert "= CLI Compose Title" in adoc_file.read_text(encoding="utf-8")

            # 3. View adoc via show-adoc command
            args_show = Namespace(action="show-adoc", target=doc_id, doc_id_flag="", json=True)
            ret_show = cmd_presentation(args_show)
            assert ret_show == 0

            # 4. Compile again with different theme
            args_compile = Namespace(action="compile", target=doc_id, doc_id_flag="", theme="white", transition="fade", force=True, json=True)
            ret_compile = cmd_presentation(args_compile)
            assert ret_compile == 0


def test_api_presentation_compose_and_adoc(tmp_path: Path):
    from claire.api.server import create_app

    db_file = tmp_path / "claire_api2.db"
    conn = sqlite3.connect(str(db_file))
    conn.row_factory = sqlite3.Row
    dbm.init_db(conn)

    doc_id = "doc_api_compose"
    detail_adoc = "= API Compose Test\n== Section 1\nContent for API presentation."
    conn.execute(
        "INSERT INTO documents (id, title, url, detail, detail_format) VALUES (?, ?, ?, ?, 'adoc')",
        (doc_id, "API Compose Test", "https://example.com/api2", detail_adoc),
    )
    conn.commit()
    conn.close()

    owner_token = "owner-" + ("z" * 32)
    owner_headers = {"Authorization": f"Bearer {owner_token}"}

    s = Settings(
        db_path=str(db_file),
        data_dir=tmp_path,
        inject_token=owner_token,
        render_format="adoc",
    )
    app = create_app(s)
    with TestClient(app, base_url=s.public_url, raise_server_exceptions=False) as client:
        with patch("claire.presentation.service.compile_presentation_html") as mock_compile:
            target_file = tmp_path / "presentations" / f"{doc_id}.html"
            target_file.parent.mkdir(parents=True, exist_ok=True)
            target_file.write_text("<!DOCTYPE html><html><body><h1>API Slide</h1></body></html>")
            mock_compile.return_value = (target_file, 130)

            # 1. Compose via POST /document/presentation/compose
            res = client.post(
                "/document/presentation/compose",
                json={"id": doc_id, "focus": "Latency", "slide_budget": 6},
                headers=owner_headers,
            )
            assert res.status_code == 200
            data = res.json()
            assert data["status"] == "ready"
            assert data["document_id"] == doc_id

            # 2. Get adoc via GET /document/presentation/adoc
            res_adoc = client.get(f"/document/presentation/adoc?id={doc_id}", headers=owner_headers)
            assert res_adoc.status_code == 200
            adoc_json = res_adoc.json()
            assert "presentation_adoc" in adoc_json
            assert "= API Compose Test" in adoc_json["presentation_adoc"]

            # 3. Update adoc via PUT /document/presentation/adoc
            new_adoc = "= Edited Title\n:revealjs_theme: league\n\n== New Slide\n* New Point\n\n[.notes]\n--\n* Edited note\n--"
            res_put = client.put(
                "/document/presentation/adoc",
                json={"id": doc_id, "presentation_adoc": new_adoc, "recompile": True},
                headers=owner_headers,
            )
            assert res_put.status_code == 200
            assert res_put.json()["status"] == "ready"

            # 4. Verify updated adoc is returned
            res_adoc_updated = client.get(f"/document/presentation/adoc?id={doc_id}", headers=owner_headers)
            assert "= Edited Title" in res_adoc_updated.json()["presentation_adoc"]


def test_document_detail_has_presentation(tmp_path):
    from claire.store import queries

    conn = _memory_db()
    doc_id = "doc_presentation_check"
    conn.execute(
        """
        INSERT INTO documents (id, title, url, detail, detail_format)
        VALUES (?, 'Check Pres Title', 'https://example.com/check', '= Doc Detail\n\n== S1\nBody', 'adoc')
        """,
        (doc_id,),
    )
    conn.commit()

    # 1. No presentation yet -> has_presentation must be False
    detail = queries.document_detail(conn, doc_id)
    assert detail["has_presentation"] is False
    assert detail["presentation_status"] is None

    # 2. Presentation saved with non-existent file -> has_presentation must be False
    fake_path = tmp_path / "non_existent.html"
    dbm.save_document_presentation(
        conn,
        document_id=doc_id,
        content_hash="h1",
        cache_key="k1",
        file_path=str(fake_path),
        file_size=100,
        theme="night",
        transition="slide",
        slide_count=3,
        status="ready",
    )
    detail2 = queries.document_detail(conn, doc_id)
    assert detail2["has_presentation"] is False
    assert detail2["presentation_status"] == "ready"

    # 3. File actually created on disk -> has_presentation must be True
    fake_path.write_text("<html>slide</html>")
    detail3 = queries.document_detail(conn, doc_id)
    assert detail3["has_presentation"] is True
    assert detail3["presentation_status"] == "ready"


def test_presentation_hud_and_layout():
    from claire.presentation.hud import inject_hud_toolbar
    from claire.presentation.preprocessor import prepare_presentation_adoc_for_compile

    # 1. Test HUD injection
    sample_html = "<html><head><title>Test Deck</title></head><body><div class='reveal'></div></body></html>"
    injected = inject_hud_toolbar(sample_html)
    assert "cbReturnToDoc" not in injected
    assert "cb-hud-sharebox" in injected
    assert "fitCodeBlocks" in injected
    assert "cbCopyHudShareInput" in injected

    # 2. Test compiler attributes
    raw_deck = "= Title\n\n== S1\n* Point 1\n"
    compiled_adoc = prepare_presentation_adoc_for_compile(raw_deck)
    assert ":revealjs_center: false" in compiled_adoc
    assert ":revealjs_width: 1280" in compiled_adoc
    assert ":revealjs_height: 720" in compiled_adoc
    assert ":revealjs_margin: 0.04" in compiled_adoc
    assert ":revealjs_pdfseparatefragments: false" in compiled_adoc
    assert ":revealjs_pdfmaxpagesperslide: 1" in compiled_adoc

    # HUD PDF download & print media rules
    assert "cbDownloadPdf" in injected
    assert "@media print" in injected
    assert "size: landscape" in injected

    # 3. Test index.html header tabs and panes layout
    index_path = Path("src/claire/templates/index.html")
    assert index_path.exists()
    index_html = index_path.read_text(encoding="utf-8")

    # Header tabs order: reader -> presentation -> graph -> stream
    idx_reader = index_html.index('id="centertab-reader"')
    idx_presentation = index_html.index('id="centertab-presentation"')
    idx_graph = index_html.index('id="centertab-graph"')
    idx_stream = index_html.index('id="centertab-stream"')
    assert idx_reader < idx_presentation < idx_graph < idx_stream

    # presentationwrap container exists
    assert 'id="presentationwrap"' in index_html
    assert 'id="presentation-frame"' in index_html
    assert 'id="presentation-download-btn"' in index_html
    assert 'presentationDownloadPdf()' in index_html

    # rpresentationbtn removed from reader tools
    assert 'id="rpresentationbtn"' not in index_html

    # 4. Check workspace.css hides download button for anonymous / non-owner
    workspace_css = Path("src/claire/static/css/workspace.css").read_text(encoding="utf-8")
    assert 'body:not([data-auth-scope="owner"]) #presentation-download-btn' in workspace_css


def test_presentation_option_a_and_async_generation(tmp_path: Path):
    """Test Option A (no LLM generation on GET, returns guidance HTML) and async wait=false generation."""
    from claire.api.server import create_app

    db_file = tmp_path / "claire_option_a.db"
    conn = sqlite3.connect(str(db_file))
    conn.row_factory = sqlite3.Row
    dbm.init_db(conn)

    doc_id = "doc_option_a_test"
    detail_adoc = "= Option A Test\n== Section 1\nContent for slide.\n"
    conn.execute(
        """
        INSERT INTO documents (id, title, url, detail, detail_format)
        VALUES (?, 'Option A Test Doc', 'https://example.com/opt-a', ?, 'adoc')
        """,
        (doc_id, detail_adoc),
    )
    conn.commit()
    conn.close()

    owner_token = "owner-" + ("a" * 32)
    owner_headers = {"Authorization": f"Bearer {owner_token}"}
    s = Settings(
        db_path=str(db_file),
        data_dir=tmp_path,
        inject_token=owner_token,
        render_format="adoc",
    )
    app = create_app(s)
    with TestClient(app, base_url=s.public_url, raise_server_exceptions=False) as client:
        # 1. Option A: GET /p/presentation on ungenerated document MUST return 404 guidance HTML
        # and MUST NOT trigger LLM authoring or file generation
        page_res = client.get(f"/p/presentation?id={doc_id}")
        assert page_res.status_code == 404
        assert "프레젠테이션이 아직 생성되지 않았습니다" in page_res.text
        assert "Option A Test Doc" in page_res.text
        assert "문서 본문 보기" in page_res.text

        # 2. Check metadata: status should be not_created
        status_res = client.get(f"/document/presentation?id={doc_id}", headers=owner_headers)
        assert status_res.status_code == 200
        assert status_res.json()["status"] == "not_created"

        # 3. Test async generation: POST /document/presentation/generate?wait=false
        with patch("claire.presentation.service.compile_presentation_html") as mock_compile:
            target_file = tmp_path / "presentations" / f"{doc_id}.html"
            target_file.parent.mkdir(parents=True, exist_ok=True)
            target_file.write_text("<!DOCTYPE html><html><body><h1>Async Option A</h1></body></html>")
            mock_compile.return_value = (target_file, 120)

            async_res = client.post(
                f"/document/presentation/generate?id={doc_id}&wait=false",
                headers=owner_headers,
            )
            assert async_res.status_code == 202
            assert async_res.json()["status"] == "composing"
            assert async_res.json()["document_id"] == doc_id


def test_prepare_presentation_adoc_header_attributes():
    """Ensure reveal.js attributes are placed in the document header before blank lines."""
    from claire.presentation.preprocessor import prepare_presentation_adoc_for_compile
    from claire.presentation.hud import sanitize_presentation_assets

    raw_adoc = """= Future Web Test
:icons: font

[.notes]
--
* Speaker note before slide
--

== Slide 1
* Point A
"""
    prepared = prepare_presentation_adoc_for_compile(raw_adoc)
    lines = prepared.splitlines()

    # Find position of attributes and the first blank line
    attr_idx = next(i for i, l in enumerate(lines) if l.startswith(":revealjsdir:"))
    first_blank = next(i for i, l in enumerate(lines) if not l.strip())

    # All attributes must precede the first blank line (end of header)
    assert attr_idx < first_blank
    assert ":revealjsdir: /static/vendor/reveal.js" in prepared
    assert ":customcss: /static/css/reveal-claire.css" in prepared

    # Test asset sanitization
    legacy_html = '<link rel="stylesheet" href="reveal.js/dist/reset.css"><script src="reveal.js/dist/reveal.js"></script>'
    sanitized = sanitize_presentation_assets(legacy_html)
    assert 'href="/static/vendor/reveal.js/dist/reset.css"' in sanitized
    assert 'src="/static/vendor/reveal.js/dist/reveal.js"' in sanitized


def test_presentation_metadata_is_ready(tmp_path: Path):
    """Verify get_presentation_metadata populates is_ready properly."""
    conn = _memory_db()
    doc_id = "doc_meta_test"
    conn.execute(
        "INSERT INTO documents (id, title, url, detail, detail_format) VALUES (?, 'T', 'U', 'D', 'adoc')",
        (doc_id,),
    )
    conn.commit()

    svc = PresentationService(data_dir=tmp_path)

    # 1. not created
    assert svc.get_presentation_metadata(conn, doc_id) is None

    # 2. composing -> is_ready is False
    dbm.save_document_presentation(
        conn,
        document_id=doc_id,
        content_hash="h",
        cache_key="k",
        file_path="/tmp/f.html",
        file_size=0,
        status="composing",
    )
    meta = svc.get_presentation_metadata(conn, doc_id)
    assert meta["status"] == "composing"
    assert meta["is_ready"] is False

    # 3. ready -> is_ready is True
    dbm.save_document_presentation(
        conn,
        document_id=doc_id,
        content_hash="h",
        cache_key="k",
        file_path="/tmp/f.html",
        file_size=100,
        status="ready",
    )
    meta = svc.get_presentation_metadata(conn, doc_id)
    assert meta["status"] == "ready"
    assert meta["is_ready"] is True

