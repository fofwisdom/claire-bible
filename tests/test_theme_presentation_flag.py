"""Unit tests for presentation feature gating flag on themes.

Tests cover:
- Serialization and I/O persistence of ThemeInfo and ThemeManager.
- Module interfaces between ThemeManager, Settings, and API endpoints.
- Access control verification on presentation API routes.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from claire.api.server import create_app
from claire.config import Settings
from claire.ontology.base import Document
from claire.store import db as dbm
from claire.store.theme import ThemeInfo, ThemeManager


def test_theme_info_presentation_flag_serialization_io(tmp_path: Path):
    """Verify ThemeInfo default, serialization, and backward compatibility."""
    # 1. Default value must be False
    info_default = ThemeInfo(id=1, seq=1, label="Default Test")
    assert info_default.presentation_enabled is False
    data = info_default.to_dict()
    assert data["presentation_enabled"] is False

    # 2. Explicit True value
    info_enabled = ThemeInfo(id=2, seq=2, label="Enabled Test", presentation_enabled=True)
    assert info_enabled.presentation_enabled is True
    data_enabled = info_enabled.to_dict()
    assert data_enabled["presentation_enabled"] is True

    # 3. Deserialization from dictionary
    restored = ThemeInfo.from_dict(data_enabled)
    assert restored.presentation_enabled is True

    # 4. Backward compatibility: missing key in legacy dictionary defaults to False
    legacy_data = {
        "id": 3,
        "seq": 3,
        "label": "Legacy Theme",
        "is_public": True,
    }
    legacy_info = ThemeInfo.from_dict(legacy_data)
    assert legacy_info.presentation_enabled is False


def test_theme_manager_presentation_flag_persistence_io(tmp_path: Path):
    """Verify ThemeManager persists presentation flag to themes.json on disk."""
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)

    s = Settings(
        CLAIRE_DATA_DIR=str(data_dir),
        CLAIRE_DB_PATH=str(data_dir / "claire.db"),
        CLAIRE_MULTI_THEME=True,
    )
    manager = ThemeManager(base_settings=s)

    # 1. Default theme (id 0) must have presentation_enabled=False
    def_theme = manager.get_theme(0)
    assert def_theme is not None
    assert def_theme.presentation_enabled is False

    # 2. Define theme with presentation_enabled=True
    t1 = manager.define_theme(label="AI Slides", presentation_enabled=True)
    assert t1.presentation_enabled is True

    # 3. Define theme without presentation flag (defaults to False)
    t2 = manager.define_theme(label="Plain Docs")
    assert t2.presentation_enabled is False

    # 4. Check on-disk file content
    themes_file = data_dir / "themes.json"
    assert themes_file.exists()
    disk_data = json.loads(themes_file.read_text(encoding="utf-8"))
    t1_disk = next(item for item in disk_data["themes"] if item["id"] == 1)
    t2_disk = next(item for item in disk_data["themes"] if item["id"] == 2)
    assert t1_disk["presentation_enabled"] is True
    assert t2_disk["presentation_enabled"] is False

    # 5. Reload manager from disk
    reloaded_manager = ThemeManager(base_settings=s)
    assert reloaded_manager.get_theme(1).presentation_enabled is True
    assert reloaded_manager.get_theme(2).presentation_enabled is False

    # 6. Update theme presentation flag and verify persistence
    updated_t2 = reloaded_manager.update_theme(2, presentation_enabled=True)
    assert updated_t2.presentation_enabled is True
    updated_t1 = reloaded_manager.update_theme(1, presentation_enabled=False)
    assert updated_t1.presentation_enabled is False

    # 7. Reload again to verify update persistence
    manager3 = ThemeManager(base_settings=s)
    assert manager3.get_theme(1).presentation_enabled is False
    assert manager3.get_theme(2).presentation_enabled is True


def test_theme_api_presentation_flag_contract(tmp_path: Path):
    """Verify GET, POST, and PATCH /themes routes expose and update presentation_enabled."""
    data_dir = tmp_path / "data"
    vault_dir = tmp_path / "vault"
    data_dir.mkdir(parents=True)
    vault_dir.mkdir(parents=True)

    owner_token = "owner-" + ("k" * 32)
    owner_headers = {"Authorization": f"Bearer {owner_token}"}

    s = Settings(
        CLAIRE_DATA_DIR=str(data_dir),
        CLAIRE_DB_PATH=str(data_dir / "claire.db"),
        CLAIRE_VAULT_PATH=str(vault_dir),
        CLAIRE_PROVIDER="mock",
        CLAIRE_ENVIRONMENT="development",
        CLAIRE_PUBLIC_URL="http://127.0.0.1:8765",
        CLAIRE_INJECT_TOKEN=owner_token,
        CLAIRE_MULTI_THEME=True,
        CLAIRE_ANONYMOUS_READONLY=True,
    )
    conn = dbm.connect(s.db_file)
    dbm.init_db(conn)
    conn.close()

    app = create_app(s)
    with TestClient(app, base_url=s.public_url, raise_server_exceptions=False) as client:
        # 1. GET /themes: default theme has presentation_enabled=False
        res = client.get("/themes")
        assert res.status_code == 200
        themes = res.json()["themes"]
        assert len(themes) == 1
        assert themes[0]["presentation_enabled"] is False

        # 2. POST /themes with presentation_enabled=True
        post_res = client.post(
            "/themes",
            json={"label": "Pitch Decks", "presentation_enabled": True},
            headers=owner_headers,
        )
        assert post_res.status_code == 201
        new_theme = post_res.json()["theme"]
        assert new_theme["presentation_enabled"] is True
        theme_id = new_theme["id"]

        # 3. PATCH /themes: toggle presentation_enabled to False
        patch_res = client.patch(
            "/themes",
            json={"id": theme_id, "presentation_enabled": False},
            headers=owner_headers,
        )
        assert patch_res.status_code == 200
        updated = patch_res.json()["theme"]
        assert updated["presentation_enabled"] is False

        # 4. PATCH /themes: toggle presentation_enabled back to True
        patch_res2 = client.patch(
            "/themes",
            json={"id": theme_id, "presentation_enabled": True},
            headers=owner_headers,
        )
        assert patch_res2.status_code == 200
        updated2 = patch_res2.json()["theme"]
        assert updated2["presentation_enabled"] is True


def test_presentation_endpoints_gated_by_theme_flag(tmp_path: Path):
    """Verify presentation API endpoints return 403 Forbidden when presentation_enabled is False."""
    data_dir = tmp_path / "data"
    vault_dir = tmp_path / "vault"
    data_dir.mkdir(parents=True)
    vault_dir.mkdir(parents=True)

    owner_token = "owner-" + ("m" * 32)
    owner_headers = {"Authorization": f"Bearer {owner_token}"}

    s = Settings(
        CLAIRE_DATA_DIR=str(data_dir),
        CLAIRE_DB_PATH=str(data_dir / "claire.db"),
        CLAIRE_VAULT_PATH=str(vault_dir),
        CLAIRE_PROVIDER="mock",
        CLAIRE_ENVIRONMENT="development",
        CLAIRE_PUBLIC_URL="http://127.0.0.1:8765",
        CLAIRE_INJECT_TOKEN=owner_token,
        CLAIRE_MULTI_THEME=True,
        CLAIRE_ANONYMOUS_READONLY=True,
        CLAIRE_PRESENTATION_ENABLED=False,
    )
    conn = dbm.connect(s.db_file)
    dbm.init_db(conn)
    doc_id = "doc-gate-test"
    doc = Document(id=doc_id, url="https://example.com/gate", title="Gate Test Document")
    dbm.insert_document(conn, doc)
    dbm.set_document_detail(conn, doc_id, detail="= Slide One\n== Title\nHello world", format="adoc")
    conn.close()

    app = create_app(s)
    with TestClient(app, base_url=s.public_url, raise_server_exceptions=False) as client:
        # 1. When presentation_enabled is False (Theme 0)
        # GET /p/presentation?id=...
        res_page = client.get(f"/p/presentation?id={doc_id}")
        assert res_page.status_code == 403
        assert "기능 비활성" in res_page.text
        assert "프레젠테이션 기능이 비활성화" in res_page.text

        # GET /document/presentation?id=...
        res_get = client.get(f"/document/presentation?id={doc_id}", headers=owner_headers)
        assert res_get.status_code == 403
        assert "비활성화" in res_get.json()["detail"]

        # POST /document/presentation/generate?id=...
        res_gen = client.post(f"/document/presentation/generate?id={doc_id}", headers=owner_headers)
        assert res_gen.status_code == 403

        # POST /document/presentation/compose
        res_comp = client.post(
            "/document/presentation/compose",
            json={"id": doc_id},
            headers=owner_headers,
        )
        assert res_comp.status_code == 403

        # GET /document/presentation/adoc?id=...
        res_adoc = client.get(f"/document/presentation/adoc?id={doc_id}", headers=owner_headers)
        assert res_adoc.status_code == 403

        # 2. Enable presentation on Theme 0 via PATCH /themes
        patch_res = client.patch(
            "/themes",
            json={"id": 0, "presentation_enabled": True},
            headers=owner_headers,
        )
        assert patch_res.status_code == 200
        assert patch_res.json()["theme"]["presentation_enabled"] is True

        # Now presentation endpoints are no longer 403
        res_get_enabled = client.get(f"/document/presentation?id={doc_id}", headers=owner_headers)
        assert res_get_enabled.status_code == 200
        assert res_get_enabled.json()["status"] == "not_created"

        res_page_enabled = client.get(f"/p/presentation?id={doc_id}")
        # Uncreated presentation returns 404 guidance HTML (Option A) rather than 403
        assert res_page_enabled.status_code == 404
        assert "프레젠테이션이 아직 생성되지 않았습니다" in res_page_enabled.text
