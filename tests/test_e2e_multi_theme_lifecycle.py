"""End-to-End lifecycle test for multi-theme database dispatch and auto-resolution."""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from claire.api.mcp_tools import build_mcp_app
from claire.api.server import create_app
from claire.cli import cmd_doc_title, cmd_purge, cmd_regenerate, get_effective_settings
from claire.config import Settings
from claire.ingest.service import IngestService, IngestServicePool
from claire.ontology.base import Document
from claire.store import db as dbm
from claire.store.theme import get_theme_manager
from claire.telegram_bot import _settle_status


@pytest.mark.asyncio
async def test_e2e_multi_theme_full_lifecycle(tmp_path: Path, monkeypatch):
    """E2E Test verifying:
    1. Multi-theme setup with Theme 0 and Theme 1.
    2. Document ingestion directly into Theme 1.
    3. Telegram bot _settle with theme_id=1 creates share token in Theme 1 DB (not Theme 0).
    4. HTTP API GET /p?s=<token> serves reader page from Theme 1 DB.
    5. HTTP API POST /share without theme param finds Theme 1 and creates share token.
    6. HTTP API GET /document?id=... without theme param returns Theme 1 document with theme metadata.
    7. MCP tools document(id) without theme resolves Theme 1 document.
    8. CLI commands (doc-title, regenerate, purge) auto-resolve target theme DB without --theme.
    """
    data_dir = tmp_path / "data"
    vault_dir = tmp_path / "vault"
    data_dir.mkdir(parents=True)
    vault_dir.mkdir(parents=True)

    owner_token = "owner-" + ("o" * 32)
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
    )

    # Initialize theme 0 (default)
    conn0 = dbm.connect(s.db_file)
    dbm.init_db(conn0)
    conn0.close()

    # Create theme 1 via ThemeManager
    tm = get_theme_manager(s)
    theme1 = tm.define_theme(
        label="Project X",
        description="Theme for Project X documents",
        icon="🚀",
        is_public=True,
    )
    s1 = tm.get_settings_for_theme(theme1.id, s)
    conn1 = dbm.connect(s1.db_file)
    dbm.init_db(conn1)
    conn1.close()

    # Step 1: Ingest document into Theme 1
    doc_id = "doc_e2e_project_x_001"
    doc_url = "https://example.com/project-x/guide"
    doc1 = Document(
        id=doc_id,
        url=doc_url,
        canonical_url=doc_url,
        title="Project X Guide",
        raw_text="Comprehensive documentation for Project X.",
        summary="Summary of Project X Guide.",
        source_type="web",
        content_hash="hash_e2e_x_1",
    )
    conn1 = dbm.connect(s1.db_file)
    dbm.insert_document(conn1, doc1)
    dbm.set_document_detail(conn1, doc_id, detail="## Overview\nProject X detailed guide content.", format="md")
    conn1.close()

    # Step 2: Telegram Bot _settle with theme_id=1
    class FakeStatus:
        def __init__(self):
            self.edited_text = None
            self.markup = None
            self.deleted = False

        async def edit_text(self, text, reply_markup=None):
            self.edited_text = text
            self.markup = reply_markup

    class FakeMsg:
        async def reply_text(self, text, reply_markup=None):
            pass

    status = FakeStatus()
    msg = FakeMsg()

    svc0 = IngestService(s)
    service_pool = IngestServicePool(s, svc0, theme_manager=tm)

    await _settle_status(
        status,
        msg,
        "✅ 적재 완료: Project X Guide",
        [],
        retry_doc_id=doc_id,
        theme_id=theme1.id,
        service_pool=service_pool,
        theme_mgr=tm,
    )

    assert status.edited_text == "✅ 적재 완료: Project X Guide"
    assert status.markup is not None
    btn = status.markup.inline_keyboard[0][0]
    assert btn.text == "📖 문서 열람 (Reader)"
    assert btn.url.startswith(f"{public_url}/p?s=")
    bot_token = btn.url.split("?s=")[1]

    # Verify: Token MUST exist in Theme 1 DB, and NOT in Theme 0 DB!
    conn1 = dbm.connect_existing(s1.db_file, readonly=True)
    assert dbm.resolve_doc_share(conn1, bot_token) == doc_id
    conn1.close()

    conn0 = dbm.connect_existing(s.db_file, readonly=True)
    assert dbm.resolve_doc_share(conn0, bot_token) is None
    conn0.close()

    # Step 3: HTTP API GET /p?s=<token>
    app = create_app(settings=s)
    client = TestClient(app, base_url=public_url)

    resp_reader = client.get(f"/p?s={bot_token}")
    assert resp_reader.status_code == 200
    assert "Project X Guide" in resp_reader.text
    assert "Project X detailed guide content" in resp_reader.text

    # Step 4: HTTP API POST /share without theme parameter (Auto-Resolution)
    resp_share = client.post(
        "/share",
        json={"doc_id": doc_id},
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    assert resp_share.status_code == 200
    share_json = resp_share.json()
    assert share_json["theme_id"] == theme1.id
    new_token = share_json["token"]

    # Verify new token resolves from Theme 1
    resp_reader2 = client.get(f"/p?s={new_token}")
    assert resp_reader2.status_code == 200
    assert "Project X Guide" in resp_reader2.text

    # Step 5: HTTP API GET /document?id=... without theme parameter
    resp_doc = client.get(f"/document?id={doc_id}")
    assert resp_doc.status_code == 200
    doc_json = resp_doc.json()
    assert doc_json["id"] == doc_id
    assert doc_json["theme_id"] == theme1.id
    assert doc_json["theme_label"] == "Project X"
    assert doc_json["title"] == "Project X Guide"

    # Step 6: MCP Tools document() lookup without theme
    import json
    mcp_app = build_mcp_app(s, theme_mgr=tm)
    with TestClient(mcp_app, base_url="http://127.0.0.1:8765") as mcp_client:
        r_mcp = mcp_client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "document", "arguments": {"document_id": doc_id}},
            },
        )
        assert r_mcp.status_code == 200
        mcp_doc = json.loads(r_mcp.json()["result"]["content"][0]["text"])
        assert "error" not in mcp_doc
        assert mcp_doc["id"] == doc_id
        assert mcp_doc["title"] == "Project X Guide"

    # Step 7: CLI Target Auto-Resolution across themes
    monkeypatch.setattr("claire.cli.get_settings", lambda: s)

    # 7a. get_effective_settings on doc_id
    args_doc = argparse.Namespace(target=doc_id, doc_id=None, theme=None)
    eff_s, eff_theme = get_effective_settings(args_doc)
    assert eff_theme is not None
    assert eff_theme.id == theme1.id
    assert str(eff_s.db_file) == str(s1.db_file)

    # 7b. get_effective_settings on share URL
    args_share = argparse.Namespace(target=f"{public_url}/p?s={bot_token}", doc_id=None, theme=None)
    eff_s_sh, eff_theme_sh = get_effective_settings(args_share)
    assert eff_theme_sh is not None
    assert eff_theme_sh.id == theme1.id
    assert str(eff_s_sh.db_file) == str(s1.db_file)

    # 7c. CLI cmd_doc_title updates document title in Theme 1 DB
    args_title = argparse.Namespace(
        target=doc_id,
        document_id=doc_id,
        title="Updated E2E Project X Title",
        theme=None,
    )
    rc_title = cmd_doc_title(args_title)
    assert rc_title == 0

    # Verify updated title in Theme 1 DB
    conn1 = dbm.connect_existing(s1.db_file, readonly=True)
    row = conn1.execute("SELECT title FROM documents WHERE id=?", (doc_id,)).fetchone()
    assert row["title"] == "Updated E2E Project X Title"
    conn1.close()

    # 7d. CLI cmd_regenerate dry-run auto-resolves Theme 1
    args_reg = argparse.Namespace(
        target=doc_id,
        token=None,
        doc_id=doc_id,
        summary=True,
        detail=False,
        graph=False,
        all=False,
        corrupted=False,
        tables=False,
        refetch=False,
        refetch_full=False,
        apply=False,
        force=False,
        dry_run=True,
        effort=None,
        format=None,
        focus=None,
        json=True,
        theme=None,
    )
    rc_reg = cmd_regenerate(args_reg)
    assert rc_reg == 0

    # 7e. CLI cmd_purge dry-run auto-resolves Theme 1
    args_purge = argparse.Namespace(
        target=doc_id,
        doc_id=doc_id,
        url=None,
        canonical_url=None,
        token=None,
        pattern=None,
        reason="e2e_test",
        no_tombstone=False,
        apply=False,
        yes=True,
        vacuum=False,
        json=True,
        theme=None,
    )
    rc_purge = cmd_purge(args_purge)
    assert rc_purge == 0
