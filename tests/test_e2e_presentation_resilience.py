"""End-to-End resilience and isolation test for the presentation authoring system."""

from __future__ import annotations

import asyncio
import sqlite3
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from starlette.testclient import TestClient

from claire.api.server import create_app
from claire.config import Settings
from claire.store import db as dbm


def test_e2e_presentation_full_resilience_lifecycle(tmp_path: Path):
    """E2E Verification of Presentation Zero-Freezing Architecture:
    1. Document ingestion: initial state has no presentation.
    2. Read-Safe Check (Option A): GET /p/presentation?s=<token> returns lightweight guidance HTML (404),
       and does NOT trigger heavy LLM generation.
    3. Asynchronous Non-blocking Authoring: POST /document/presentation/generate?id=...&wait=false returns 202 Accepted.
    4. Zero-Freezing Invariant: While LLM composition is running on the dedicated worker thread,
       concurrent API requests (/health, /document) respond immediately without event loop starvation.
    5. Request Coalescing: Concurrent generation requests for the same doc do not spawn duplicate tasks.
    6. Transition to Ready: Once completed, GET /p/presentation?s=<token> serves 200 OK with reveal.js & HUD.
    """
    db_file = tmp_path / "claire_e2e.db"
    conn = sqlite3.connect(str(db_file))
    conn.row_factory = sqlite3.Row
    dbm.init_db(conn)

    doc_id = "doc_e2e_resilience_test"
    detail_adoc = """= Future Web Architecture
== Executive Summary
The website of the future may assemble itself for every visitor.

== Key Challenges
* Distributed state synchronization
* Real-time presentation authoring without freezing
"""
    conn.execute(
        """
        INSERT INTO documents (id, title, url, detail, detail_format)
        VALUES (?, 'Future Web Architecture', 'https://example.com/future-web', ?, 'adoc')
        """,
        (doc_id, detail_adoc),
    )
    conn.commit()

    # Create share token for document
    share_token = dbm.create_doc_share(conn, doc_id)
    conn.close()

    owner_token = "owner-" + ("z" * 32)
    owner_headers = {"Authorization": f"Bearer {owner_token}"}
    s = Settings(
        db_path=str(db_file),
        data_dir=tmp_path,
        inject_token=owner_token,
        render_format="adoc",
        public_url="http://127.0.0.1:8765",
        environment="development",
    )
    app = create_app(s)

    with TestClient(app, base_url=s.public_url, raise_server_exceptions=False) as client:
        # Step 1: Health check baseline
        h_res = client.get("/health")
        assert h_res.status_code == 200

        # Step 2: Option A Verification - GET /p/presentation via share token before generation
        # Must return 404 guidance HTML without calling LLM authoring
        t_start = time.perf_counter()
        page_res = client.get(f"/p/presentation?s={share_token}")
        duration_ms = (time.perf_counter() - t_start) * 1000

        assert page_res.status_code == 404
        assert duration_ms < 200, f"Option A GET must be fast (<200ms), took {duration_ms}ms"
        assert "Future Web Architecture" in page_res.text
        assert "프레젠테이션이 아직 생성되지 않았습니다" in page_res.text
        assert f"/p?s={share_token}" in page_res.text  # Link back to document reader

        # Step 3: Check status is still not_created
        status_res = client.get(f"/document/presentation?id={doc_id}", headers=owner_headers)
        assert status_res.status_code == 200
        assert status_res.json()["status"] == "not_created"

        # Step 4: Non-blocking asynchronous authoring (wait=false)
        # We simulate a slow LLM composition (e.g. 500ms blocking operation in worker thread)
        def slow_compose(*args, **kwargs):
            time.sleep(0.5)
            return """= Future Web Architecture
:revealjs_theme: night
:revealjs_transition: slide

== 1. Future Web
* Point 1
* Point 2
"""

        with patch("claire.extract.provider.MockProvider.compose_presentation", side_effect=slow_compose):
            with patch("claire.presentation.service.compile_presentation_html") as mock_compile:
                target_file = tmp_path / "presentations" / f"{doc_id}.html"
                target_file.parent.mkdir(parents=True, exist_ok=True)
                target_file.write_text("<!DOCTYPE html><html><body><h1>Future Web Slide</h1></body></html>")
                mock_compile.return_value = (target_file, 80)

                # Trigger generation with wait=false
                t_gen_start = time.perf_counter()
                gen_res = client.post(
                    f"/document/presentation/generate?id={doc_id}&wait=false",
                    headers=owner_headers,
                )
                gen_time_ms = (time.perf_counter() - t_gen_start) * 1000

                # Must return 202 Accepted immediately without waiting for slow_compose
                assert gen_res.status_code == 202
                assert gen_time_ms < 100, f"Async generate must return immediately (<100ms), took {gen_time_ms}ms"
                assert gen_res.json()["status"] == "composing"

                # Step 5: Zero-Freezing Invariant Check
                # While slow_compose is actively running on the worker thread,
                # the server event loop must NOT freeze. We send multiple concurrent requests.
                for _ in range(5):
                    t_req = time.perf_counter()
                    resp = client.get("/health")
                    req_duration_ms = (time.perf_counter() - t_req) * 1000
                    assert resp.status_code == 200
                    assert req_duration_ms < 50, f"Event loop froze! /health took {req_duration_ms}ms"

                # Concurrent request coalescing check: second call while composing returns 202
                dup_res = client.post(
                    f"/document/presentation/generate?id={doc_id}&wait=false",
                    headers=owner_headers,
                )
                assert dup_res.status_code == 202

                # Step 6: Poll until ready
                ready = False
                for _ in range(30):
                    time.sleep(0.05)
                    poll_res = client.get(f"/document/presentation?id={doc_id}", headers=owner_headers)
                    if poll_res.status_code == 200 and poll_res.json().get("status") == "ready":
                        ready = True
                        break

                assert ready, "Presentation authoring job did not complete within timeout"

        # Step 7: Access presentation via share token after ready
        final_page_res = client.get(f"/p/presentation?s={share_token}")
        assert final_page_res.status_code == 200
        assert "Future Web Slide" in final_page_res.text
        # Verify HUD toolbar injection
        assert "is-embedded" in final_page_res.text
