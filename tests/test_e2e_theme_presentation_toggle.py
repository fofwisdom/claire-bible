"""End-to-End lifecycle test for database-specific presentation toggle and gating."""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from claire import cli
from claire.api.server import create_app
from claire.config import Settings
from claire.ontology.base import Document
from claire.store import db as dbm
from claire.store.theme import get_theme_manager


def test_e2e_theme_presentation_toggle_lifecycle(tmp_path: Path, monkeypatch):
    """E2E Test verifying:
    1. Multi-theme setup with Theme 0 (disabled), Theme 1 (enabled), Theme 2 (disabled).
    2. Document ingestion across themes.
    3. Document auto-resolution routes presentation checks to the correct theme database.
    4. Gating blocks presentation on Themes 0 and 2 with 403, while Theme 1 is accessible.
    5. Share tokens auto-resolve to their theme database and respect theme presentation gating.
    6. Runtime toggle via API dynamically updates access without restarting the server.
    7. CLI theme update toggles presentation flag and restores access.
    """
    data_dir = tmp_path / "data"
    vault_dir = tmp_path / "vault"
    data_dir.mkdir(parents=True)
    vault_dir.mkdir(parents=True)

    owner_token = "owner-" + ("e" * 32)
    owner_headers = {"Authorization": f"Bearer {owner_token}"}
    public_url = "http://127.0.0.1:8765"

    s = Settings(
        CLAIRE_DATA_DIR=str(data_dir),
        CLAIRE_DB_PATH=str(data_dir / "claire.db"),
        CLAIRE_VAULT_PATH=str(vault_dir),
        CLAIRE_PROVIDER="mock",
        CLAIRE_ENVIRONMENT="development",
        CLAIRE_PUBLIC_URL=public_url,
        CLAIRE_INJECT_TOKEN=owner_token,
        CLAIRE_MULTI_THEME=True,
        CLAIRE_ANONYMOUS_READONLY=True,
        CLAIRE_DATA_LIFECYCLE="purgeable",
        CLAIRE_PRESENTATION_ENABLED=False,
    )

    # 1. Initialize Theme 0 database (default)
    conn0 = dbm.connect(s.db_file)
    dbm.init_db(conn0)
    doc0 = Document(id="doc-0", url="https://example.com/doc0", title="Theme 0 Doc")
    dbm.insert_document(conn0, doc0)
    dbm.set_document_detail(conn0, "doc-0", detail="= Title 0\n== Slide\nBody 0", format="adoc")
    conn0.commit()
    conn0.close()

    # 2. Define Theme 1 (presentation_enabled=True) and Theme 2 (presentation_enabled=False)
    mgr = get_theme_manager(s)
    t1 = mgr.define_theme(label="Theme One Slides", presentation_enabled=True)
    t2 = mgr.define_theme(label="Theme Two Docs", presentation_enabled=False)

    conn1 = dbm.connect(t1.db_path)
    dbm.init_db(conn1)
    doc1 = Document(id="doc-1", url="https://example.com/doc1", title="Theme 1 Doc")
    dbm.insert_document(conn1, doc1)
    dbm.set_document_detail(conn1, "doc-1", detail="= Title 1\n== Slide\nBody 1", format="adoc")
    conn1.commit()
    conn1.close()

    conn2 = dbm.connect(t2.db_path)
    dbm.init_db(conn2)
    doc2 = Document(id="doc-2", url="https://example.com/doc2", title="Theme 2 Doc")
    dbm.insert_document(conn2, doc2)
    dbm.set_document_detail(conn2, "doc-2", detail="= Title 2\n== Slide\nBody 2", format="adoc")
    conn2.commit()
    conn2.close()

    app = create_app(s)
    with TestClient(app, base_url=s.public_url, raise_server_exceptions=False) as client:
        # Step 3: Verify initial presentation gating per theme
        # Theme 0 -> 403 Forbidden
        res0 = client.get("/document/presentation?id=doc-0", headers=owner_headers)
        assert res0.status_code == 403
        assert "비활성화" in res0.json()["detail"]

        # Theme 1 -> 200 OK (auto-resolved to Theme 1 database)
        res1 = client.get("/document/presentation?id=doc-1", headers=owner_headers)
        assert res1.status_code == 200
        assert res1.json()["status"] == "not_created"

        # Theme 2 -> 403 Forbidden (auto-resolved to Theme 2 database)
        res2 = client.get("/document/presentation?id=doc-2", headers=owner_headers)
        assert res2.status_code == 403
        assert "비활성화" in res2.json()["detail"]

        # Step 4: Verify HTML guidance pages (/p/presentation)
        page0 = client.get("/p/presentation?id=doc-0")
        assert page0.status_code == 403
        assert "기능 비활성" in page0.text

        page1 = client.get("/p/presentation?id=doc-1")
        assert page1.status_code == 404
        assert "프레젠테이션이 아직 생성되지 않았습니다" in page1.text

        # Step 5: Test share tokens and presentation gating
        share_res0 = client.post("/share", json={"doc_id": "doc-0"}, headers=owner_headers)
        assert share_res0.status_code == 200
        token0 = share_res0.json()["token"]

        share_res1 = client.post("/share", json={"doc_id": "doc-1"}, headers=owner_headers)
        assert share_res1.status_code == 200
        token1 = share_res1.json()["token"]

        # Token 0 is for Theme 0 -> disabled -> 403
        share_page0 = client.get(f"/p/presentation?s={token0}")
        assert share_page0.status_code == 403
        assert "기능 비활성" in share_page0.text

        # Token 1 is for Theme 1 -> enabled -> 404 (unready guidance)
        share_page1 = client.get(f"/p/presentation?s={token1}")
        assert share_page1.status_code == 404
        assert "프레젠테이션이 아직 생성되지 않았습니다" in share_page1.text

        # Step 6: Runtime toggle via API
        # Enable Theme 0
        patch0 = client.patch(
            "/themes",
            json={"id": 0, "presentation_enabled": True},
            headers=owner_headers,
        )
        assert patch0.status_code == 200
        assert patch0.json()["theme"]["presentation_enabled"] is True

        # Disable Theme 1
        patch1 = client.patch(
            "/themes",
            json={"id": t1.id, "presentation_enabled": False},
            headers=owner_headers,
        )
        assert patch1.status_code == 200
        assert patch1.json()["theme"]["presentation_enabled"] is False

        # Verify inverted access
        res0_after = client.get("/document/presentation?id=doc-0", headers=owner_headers)
        assert res0_after.status_code == 200
        assert res0_after.json()["status"] == "not_created"

        res1_after = client.get("/document/presentation?id=doc-1", headers=owner_headers)
        assert res1_after.status_code == 403

        share_page1_after = client.get(f"/p/presentation?s={token1}")
        assert share_page1_after.status_code == 403

        # Step 7: CLI command verification
        # Re-enable Theme 1 via CLI
        monkeypatch.setattr("claire.cli.get_settings", lambda: s)
        monkeypatch.setattr("claire.store.theme.get_theme_manager", lambda _s=None: mgr)
        rc = cli.main(["theme", "update", str(t1.id), "--presentation"])
        assert rc == 0

        # Verify access restored via client
        res1_restored = client.get("/document/presentation?id=doc-1", headers=owner_headers)
        assert res1_restored.status_code == 200
        assert res1_restored.json()["status"] == "not_created"
