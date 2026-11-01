"""End-to-End test for Syndication Cross-Theme Dispatch."""

from __future__ import annotations

from pathlib import Path

import pytest
from starlette.testclient import TestClient

from claire.api.server import create_app
from claire.config import Settings
from claire.store import db as dbm

OWNER_TOKEN = "owner-" + ("o" * 32)
READONLY_TOKEN = "readonly-" + ("r" * 32)
COLLAB_TOKEN = "collab-" + ("c" * 32)
OWNER_HEADERS = {"Authorization": f"Bearer {OWNER_TOKEN}"}
READONLY_HEADERS = {"Authorization": f"Bearer {READONLY_TOKEN}"}
COLLAB_HEADERS = {"Authorization": f"Bearer {COLLAB_TOKEN}"}


@pytest.fixture
def e2e_dispatch_client(tmp_path: Path):
    data_dir = tmp_path / "data"
    vault_dir = tmp_path / "vault"
    data_dir.mkdir(parents=True)
    vault_dir.mkdir(parents=True)

    db_path = str(data_dir / "claire.db")
    vault_path = str(vault_dir)

    settings = Settings(
        CLAIRE_DATA_DIR=str(data_dir),
        CLAIRE_DB_PATH=db_path,
        CLAIRE_VAULT_PATH=vault_path,
        CLAIRE_PROVIDER="mock",
        CLAIRE_ENVIRONMENT="development",
        CLAIRE_PUBLIC_URL="http://127.0.0.1:8765",
        CLAIRE_INJECT_TOKEN=OWNER_TOKEN,
        CLAIRE_READONLY_TOKEN=READONLY_TOKEN,
        CLAIRE_COLLABORATOR_TOKEN=COLLAB_TOKEN,
        CLAIRE_ANONYMOUS_READONLY=False,
        CLAIRE_MULTI_THEME=True,
    )

    conn = dbm.connect(settings.db_file)
    dbm.init_db(conn)
    conn.close()

    app = create_app(settings=settings)
    client = TestClient(app, base_url=settings.public_url)
    return client, settings


def test_e2e_syndication_cross_theme_dispatch(e2e_dispatch_client):
    client, settings = e2e_dispatch_client

    # 1. Create Theme 1: AI Research
    res_t1 = client.post(
        "/themes",
        json={"label": "인공지능 연구", "description": "AI 모델 연구 테마", "icon": "🧠"},
        headers=OWNER_HEADERS,
    )
    assert res_t1.status_code == 201
    theme1_id = res_t1.json()["theme"]["id"]
    assert theme1_id == 1

    # 2. Create Theme 2: Information Security
    res_t2 = client.post(
        "/themes",
        json={
            "label": "정보보안",
            "description": "보안 위협 및 취약점 분석",
            "icon": "🛡️",
            "default_focus": "보안 취약점 및 위협 요인",
        },
        headers=OWNER_HEADERS,
    )
    assert res_t2.status_code == 201
    theme2_id = res_t2.json()["theme"]["id"]
    assert theme2_id == 2

    # 3. Ingest document into Theme 1
    ingest_resp = client.post(
        "/ingest",
        json={
            "payload": "대규모 언어 모델의 프롬프트 인젝션 취약점 분석 및 방어 기법 연구 보고서입니다.",
            "theme": theme1_id,
            "format": "md",
        },
        headers=OWNER_HEADERS,
    )
    assert ingest_resp.status_code == 200
    doc_id = ingest_resp.json()["document_id"]
    assert doc_id

    # 4. Verify initial isolation: Document is in Theme 1, not in Theme 2
    docs_t1 = client.get(f"/documents?theme={theme1_id}", headers=OWNER_HEADERS).json()
    assert len(docs_t1["documents"]) == 1
    assert docs_t1["documents"][0]["id"] == doc_id

    docs_t2 = client.get(f"/documents?theme={theme2_id}", headers=OWNER_HEADERS).json()
    assert len(docs_t2["documents"]) == 0

    # 5. Cross-theme dispatch from Theme 1 to Theme 2 via POST /api/syndication/dispatch
    applied_focus = "적대적 공격 탐지 관점 분석"
    dispatch_resp = client.post(
        "/api/syndication/dispatch",
        json={
            "document_id": doc_id,
            "source_theme_id": theme1_id,
            "target_theme_id": theme2_id,
            "focus": applied_focus,
        },
        headers=OWNER_HEADERS,
    )
    assert dispatch_resp.status_code == 200
    d_data = dispatch_resp.json()
    assert d_data["ok"] is True
    assert d_data["status"] == "done"
    assert d_data["source_theme_id"] == theme1_id
    assert d_data["target_theme_id"] == theme2_id
    assert d_data["document_id"] == doc_id
    assert d_data["applied_focus"] == applied_focus
    dispatch_id = d_data["dispatch_id"]
    target_doc_id = d_data["target_document_id"]
    assert target_doc_id
    assert target_doc_id != doc_id

    # 6. Verify target document in Theme 2
    docs_t2_after = client.get(f"/documents?theme={theme2_id}", headers=OWNER_HEADERS).json()
    assert len(docs_t2_after["documents"]) == 1
    assert docs_t2_after["documents"][0]["id"] == target_doc_id

    detail_resp = client.get(f"/document?id={target_doc_id}&theme={theme2_id}", headers=OWNER_HEADERS)
    assert detail_resp.status_code == 200
    detail_data = detail_resp.json()
    assert detail_data["id"] == target_doc_id
    assert detail_data["theme_id"] == theme2_id
    assert detail_data["meta"]["focus"] == applied_focus
    assert detail_data["meta"]["syndication"]["source_theme_id"] == theme1_id
    assert detail_data["meta"]["syndication"]["source_document_id"] == doc_id

    # 7. Verify source document in Theme 1 is intact
    docs_t1_after = client.get(f"/documents?theme={theme1_id}", headers=OWNER_HEADERS).json()
    assert len(docs_t1_after["documents"]) == 1
    assert docs_t1_after["documents"][0]["id"] == doc_id

    # 8. Verify database partition in unified claire.db
    conn = dbm.connect_existing(settings.db_file)
    try:
        rows = conn.execute(
            "SELECT id, theme_id FROM documents WHERE id IN (?, ?) ORDER BY theme_id ASC",
            (doc_id, target_doc_id),
        ).fetchall()
        assert len(rows) == 2
        assert rows[0]["id"] == doc_id
        assert rows[0]["theme_id"] == theme1_id
        assert rows[1]["id"] == target_doc_id
        assert rows[1]["theme_id"] == theme2_id

        # Verify syndication_dispatches row in database
        disp_row = dbm.get_syndication_dispatch(conn, dispatch_id)
        assert disp_row is not None
        assert disp_row["theme_id"] == theme1_id
        assert disp_row["target_theme_id"] == theme2_id
        assert disp_row["document_id"] == doc_id
        assert disp_row["remote_ref"] == target_doc_id
        assert disp_row["mode"] == "cross_theme"
        assert disp_row["status"] == "done"
        assert disp_row["applied_focus"] == applied_focus
    finally:
        conn.close()

    # 9. Verify GET /api/syndication/dispatches
    dispatches_resp = client.get(
        f"/api/syndication/dispatches?theme_id={theme1_id}",
        headers=OWNER_HEADERS,
    )
    assert dispatches_resp.status_code == 200
    disp_list = dispatches_resp.json()
    assert disp_list["ok"] is True
    assert disp_list["count"] >= 1
    matched = [d for d in disp_list["dispatches"] if d["id"] == dispatch_id]
    assert len(matched) == 1
    assert matched[0]["target_theme_id"] == theme2_id

    # 10. Verify alternate paths: POST /syndication/dispatch with auto-resolution of source theme
    dispatch2_resp = client.post(
        "/syndication/dispatch",
        json={
            "doc_id": doc_id,
            "target_theme": theme2_id,
        },
        headers=OWNER_HEADERS,
    )
    assert dispatch2_resp.status_code == 200
    d2_data = dispatch2_resp.json()
    assert d2_data["ok"] is True
    assert d2_data["source_theme_id"] == theme1_id
    assert d2_data["target_theme_id"] == theme2_id

    # Verify alternate paths: GET /syndication/dispatches
    list2_resp = client.get(
        f"/syndication/dispatches?target_theme_id={theme2_id}",
        headers=OWNER_HEADERS,
    )
    assert list2_resp.status_code == 200
    assert list2_resp.json()["count"] >= 2


def test_syndication_dispatch_access_controls_and_errors(e2e_dispatch_client):
    client, settings = e2e_dispatch_client

    # 1. Anonymous request to POST /api/syndication/dispatch is rejected
    anon_post = client.post(
        "/api/syndication/dispatch",
        json={"document_id": "doc_test", "target_theme_id": 1},
    )
    assert anon_post.status_code in (403, 404)

    # 2. Anonymous request to GET /api/syndication/dispatches is rejected
    anon_get = client.get("/api/syndication/dispatches")
    assert anon_get.status_code in (403, 404)

    # 3. Missing document_id returns 400
    err_doc = client.post(
        "/api/syndication/dispatch",
        json={"target_theme_id": 1},
        headers=OWNER_HEADERS,
    )
    assert err_doc.status_code == 400

    # 4. Missing target_theme_id returns 400
    err_target = client.post(
        "/api/syndication/dispatch",
        json={"document_id": "doc_xyz"},
        headers=OWNER_HEADERS,
    )
    assert err_target.status_code == 400

    # 5. Non-existent document returns 404
    err_nonexistent = client.post(
        "/api/syndication/dispatch",
        json={"document_id": "doc_does_not_exist", "target_theme_id": 0},
        headers=OWNER_HEADERS,
    )
    assert err_nonexistent.status_code == 404
