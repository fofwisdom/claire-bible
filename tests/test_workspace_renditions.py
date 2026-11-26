"""Workspace reader rendition API and HTTP E2E tests (DOCUMENT_RENDITIONS_AND_PUBLICATION_DESIGN.md Section 9.1)."""

from __future__ import annotations

from pathlib import Path

import pytest
from starlette.testclient import TestClient

from claire.api.server import create_app
from claire.core.config import Settings
from claire.extract.rendition_templates import PRE_TEMPLATE_VERSION
from claire.store import db as dbm
from claire.store import document_renditions as renditions_store
from tests.auth_helpers import bearer_headers, issue_session_token


@pytest.fixture
def workspace_env(tmp_path: Path):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    (tmp_path / "vault").mkdir(parents=True, exist_ok=True)
    settings = Settings(
        CLAIRE_DB_PATH=str(data_dir / "claire.db"),
        CLAIRE_PROVIDER="mock",
        CLAIRE_ENVIRONMENT="development",
        CLAIRE_FQDN="127.0.0.1:8765",
    )
    with TestClient(create_app(settings=settings), base_url=settings.public_url) as client:
        yield client, settings


def test_get_document_returns_renditions_and_strips_add_permissions(workspace_env) -> None:
    """GET /document returns ordered renditions and strips add flags for readonly/write/shared requests."""
    client, settings = workspace_env
    owner_headers = bearer_headers(settings, scope="owner")
    write_headers = bearer_headers(settings, scope="write")
    readonly_headers = bearer_headers(settings, scope="readonly")

    # 1. Ingest a document in the personal KB with no extra renditions.
    ingest_resp = client.post(
        "/ingest",
        json={"payload": "Workspace Renditions\nBody text for workspace rendition test. " * 4, "extraction_mode": "provider", "renditions": []},
        headers=owner_headers,
    )
    assert ingest_resp.status_code == 200, ingest_resp.text
    doc_id = ingest_resp.json()["document_id"]

    # Owner on personal KB sees primary standard rendition and missing additional templates.
    owner_doc = client.get(f"/document?id={doc_id}", headers=owner_headers)
    assert owner_doc.status_code == 200, owner_doc.text
    owner_body = owner_doc.json()
    assert owner_body["can_add_renditions"] is True
    assert owner_body["missing_renditions"] == ["concise", "academic"]
    assert len(owner_body["renditions"]) == 1
    primary = owner_body["renditions"][0]
    assert primary["template"] == "standard"
    assert primary["template_version"] == "standard-v1"
    assert primary["is_primary"] is True
    assert primary["detail"] == owner_body["detail"]
    assert primary["detail_format"] == owner_body["detail_format"]
    assert primary["detail_html"] == owner_body["detail_html"]
    assert primary["updated_at"] is not None

    # Readonly and write callers cannot add renditions via POST .../renditions, so flags are stripped.
    for hdrs in (readonly_headers, write_headers):
        doc_resp = client.get(f"/document?id={doc_id}", headers=hdrs)
        assert doc_resp.status_code == 200, doc_resp.text
        body = doc_resp.json()
        assert [r["template"] for r in body["renditions"]] == ["standard"]
        assert body["can_add_renditions"] is False
        assert body["missing_renditions"] == []

    # Shared KB documents never allow adding renditions, even for an owner caller.
    kb_resp = client.post("/kb", json={"knowledge_type": "real", "label": "Shared Research"}, headers=owner_headers)
    assert kb_resp.status_code == 201, kb_resp.text
    kb_info = kb_resp.json()["knowledge_base"]
    kb_id = int(kb_info["id"])
    kb_number = str(kb_info["number"])

    shared_ingest = client.post(
        "/ingest",
        json={"payload": "Shared Doc\nShared KB document text for rendition check. " * 4, "extraction_mode": "provider", "renditions": []},
        headers=owner_headers,
    )
    assert shared_ingest.status_code == 200, shared_ingest.text
    shared_doc_id = shared_ingest.json()["document_id"]
    contrib = client.post(
        f"/api/v1/kbs/{kb_number}/contributions",
        json={"document_id": shared_doc_id},
        headers=owner_headers,
    )
    assert contrib.status_code in (200, 201), contrib.text

    conn = dbm.connect_existing(settings.db_file, readonly=True)
    try:
        rows = conn.execute("SELECT id FROM documents WHERE kb_id=?", (kb_id,)).fetchall()
        assert rows
        target_shared_doc_id = str(rows[0]["id"])
    finally:
        conn.close()

    shared_doc = client.get(f"/kb/{kb_number}/document?id={target_shared_doc_id}", headers=owner_headers)
    assert shared_doc.status_code == 200, shared_doc.text
    shared_body = shared_doc.json()
    assert shared_body["can_add_renditions"] is False
    assert shared_body["missing_renditions"] == []
    assert [r["template"] for r in shared_body["renditions"]] == ["standard"]


def test_get_document_preserves_pre_template_version_and_template_order(workspace_env) -> None:
    """GET /document orders renditions (standard, concise, academic) and keeps pre-template version."""
    client, settings = workspace_env
    owner_headers = bearer_headers(settings, scope="owner")
    ingest_resp = client.post(
        "/ingest",
        json={"payload": "Pre Template\nDocument text for ordering check. " * 4, "extraction_mode": "provider", "renditions": []},
        headers=owner_headers,
    )
    assert ingest_resp.status_code == 200, ingest_resp.text
    doc_id = ingest_resp.json()["document_id"]

    conn = dbm.connect(settings.db_file)
    try:
        conn.execute("UPDATE document_renditions SET template_version=? WHERE document_id=? AND kb_id=0 AND template='standard'", (PRE_TEMPLATE_VERSION, doc_id))
        renditions_store.store_additional(conn, doc_id, 0, template="academic", detail="= Academic\n\n== Section\nBody", lineage={"provider": "mock"})
        renditions_store.store_additional(conn, doc_id, 0, template="concise", detail="= Concise\n\n== Summary\nShort", lineage={"provider": "mock"})
        conn.commit()
    finally:
        conn.close()

    doc_resp = client.get(f"/document?id={doc_id}", headers=owner_headers)
    assert doc_resp.status_code == 200, doc_resp.text
    body = doc_resp.json()
    assert [(r["template"], r["template_version"], r["is_primary"]) for r in body["renditions"]] == [
        ("standard", PRE_TEMPLATE_VERSION, True),
        ("concise", "concise-v1", False),
        ("academic", "academic-v1", False),
    ]
    assert body["can_add_renditions"] is True
    assert body["missing_renditions"] == []


def test_workspace_e2e_add_concise_rendition_flow(workspace_env) -> None:
    """E2E HTTP flow: owner creates concise rendition via API v1 and GET /document reflects both."""
    client, settings = workspace_env
    session_token = issue_session_token(settings.db_file, account_id=dbm.ADMIN_ACCOUNT_ID, scope="owner")
    session_cookie = {"Cookie": f"claire_session={session_token}"}
    session_post_headers = {**session_cookie, "Origin": settings.public_url.rstrip("/")}

    ingest_resp = client.post(
        "/ingest",
        json={"payload": "E2E Rendition\nEnd-to-end workspace rendition creation body. " * 4, "extraction_mode": "provider", "renditions": []},
        headers=session_post_headers,
    )
    assert ingest_resp.status_code == 200, ingest_resp.text
    doc_id = ingest_resp.json()["document_id"]

    before = client.get(f"/document?id={doc_id}", headers=session_cookie)
    assert before.status_code == 200, before.text
    before_body = before.json()
    assert [r["template"] for r in before_body["renditions"]] == ["standard"]
    assert before_body["can_add_renditions"] is True
    assert before_body["missing_renditions"] == ["concise", "academic"]
    primary_html_before = before_body["detail_html"]

    add_resp = client.post(
        f"/api/v1/kbs/personal/documents/{doc_id}/renditions",
        json={"template": "concise"},
        headers=session_post_headers,
    )
    assert add_resp.status_code == 200, add_resp.text
    assert add_resp.json() == {"document_id": doc_id, "rendition": {"template": "concise", "state": "made", "error": ""}}

    after = client.get(f"/document?id={doc_id}", headers=session_cookie)
    assert after.status_code == 200, after.text
    after_body = after.json()
    assert [r["template"] for r in after_body["renditions"]] == ["standard", "concise"]
    std_entry, concise_entry = after_body["renditions"]
    assert std_entry["is_primary"] is True
    assert std_entry["detail_html"] == primary_html_before
    assert concise_entry["is_primary"] is False
    assert concise_entry["template_version"] == "concise-v1"
    assert concise_entry["detail"].startswith("[mock-detail-adoc] [template: concise]")
    assert concise_entry["detail_format"] == "adoc"
    assert concise_entry["detail_html"]
    assert after_body["can_add_renditions"] is True
    assert after_body["missing_renditions"] == ["academic"]
