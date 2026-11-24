from __future__ import annotations

import json
from pathlib import Path
import time
import pytest
from starlette.testclient import TestClient

from claire.api.server import create_app
from claire.core.config import Settings
from claire.ontology.base import Document
from claire.store import db as dbm
from claire.store.queries import document_detail
from tests.auth_helpers import bearer_headers


@pytest.fixture
def raw_test_context(tmp_path: Path):
    data_dir = tmp_path / "data"
    vault_dir = tmp_path / "vault"
    data_dir.mkdir(parents=True)
    vault_dir.mkdir(parents=True)

    db_path = str(data_dir / "claire.db")
    vault_path = str(vault_dir)

    settings = Settings(
        CLAIRE_DB_PATH=db_path,
        CLAIRE_PROVIDER="mock",
        CLAIRE_ENVIRONMENT="development",
        CLAIRE_FQDN="127.0.0.1:8765",
    )

    conn = dbm.connect(Path(db_path))
    dbm.init_db(conn)

    # 1. Document with raw_inbox file
    doc_id = "doc-raw-001"
    raw_files_dir = data_dir / "raw" / "files"
    raw_files_dir.mkdir(parents=True, exist_ok=True)
    raw_pdf_path = raw_files_dir / "1_secret_research.pdf"
    raw_pdf_content = b"%PDF-1.4 sample raw pdf document bytes"
    raw_pdf_path.write_bytes(raw_pdf_content)

    doc_obj = Document(
        id=doc_id,
        title="기밀 연구 보고서",
        url="file:///server/private/secret_research.pdf",
        raw_text="비밀 연구 내용 본문입니다.",
        source_type="file",
    )
    dbm.insert_document(conn, doc_obj)

    conn.execute(
        "INSERT INTO raw_inbox(received_at, source, kind, payload, file_name, file_ref, document_id, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (time.time(), "cli", "document", "", "secret_research.pdf", str(raw_pdf_path), doc_id, "done"),
    )

    # 2. Document with meta presentation_pdf artifact
    doc_id_meta = "doc-meta-002"
    artifacts_dir = data_dir / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    art_pdf_path = artifacts_dir / "briefing.pdf"
    art_pdf_content = b"%PDF-1.4 briefing presentation artifact bytes"
    art_pdf_path.write_bytes(art_pdf_content)

    doc_obj_meta = Document(
        id=doc_id_meta,
        title="브리핑 프레젠테이션",
        raw_text="브리핑 요약입니다.",
        source_type="pdf",
        meta={"presentation_pdf": {"artifact_path": str(art_pdf_path), "file_name": "briefing.pdf"}},
    )
    dbm.insert_document(conn, doc_obj_meta)

    # 3. Malicious document for path traversal tests
    doc_id_evil = "doc-evil-traversal"
    doc_obj_evil = Document(
        id=doc_id_evil,
        title="악의적 조작 문서",
        raw_text="악의적 탈취 시도 본문",
        source_type="file",
    )
    dbm.insert_document(conn, doc_obj_evil)
    conn.execute(
        "INSERT INTO raw_inbox(received_at, source, kind, payload, file_name, file_ref, document_id, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (time.time(), "cli", "document", "", "passwd", "/etc/passwd", doc_id_evil, "done"),
    )

    conn.commit()
    conn.close()

    headers = {
        "owner": bearer_headers(settings, scope="owner"),
        "write": bearer_headers(settings, scope="write"),
        "readonly": bearer_headers(settings, scope="readonly"),
    }

    app = create_app(settings=settings)
    client = TestClient(app, base_url=settings.public_url)
    return client, settings, doc_id, doc_id_meta, doc_id_evil, raw_pdf_content, art_pdf_content, headers


def test_document_detail_has_raw_file_and_no_path_leak(raw_test_context):
    client, settings, doc_id, doc_id_meta, _, _, _, _ = raw_test_context
    conn = dbm.connect_existing(settings.db_file, readonly=True)
    try:
        detail = document_detail(conn, doc_id)
        assert detail is not None
        assert detail["has_raw_file"] is True
        assert detail["raw_file_name"] == "secret_research.pdf"
        assert "file_ref" not in detail
        json_dump = json.dumps(detail)
        assert "file://" not in json_dump
        assert "/raw/files/" not in json_dump

        detail_meta = document_detail(conn, doc_id_meta)
        assert detail_meta is not None
        assert detail_meta["has_raw_file"] is True
        assert detail_meta["raw_file_name"] == "briefing.pdf"
        json_meta_dump = json.dumps(detail_meta)
        assert "/artifacts/" not in json_meta_dump
    finally:
        conn.close()


def test_document_raw_route_owner_access(raw_test_context):
    client, _, doc_id, _, _, raw_pdf_content, _, headers = raw_test_context
    resp = client.get(f"/document/raw?id={doc_id}", headers=headers["owner"])
    assert resp.status_code == 200
    assert resp.content == raw_pdf_content
    assert resp.headers["content-type"] == "application/pdf"
    assert 'inline; filename="secret_research.pdf"' in resp.headers["content-disposition"]
    assert "private" in resp.headers["cache-control"]
    assert "no-store" in resp.headers["cache-control"]


def test_document_raw_route_write_scope_access(raw_test_context):
    client, _, doc_id, _, _, raw_pdf_content, _, headers = raw_test_context
    resp = client.get(f"/document/raw?id={doc_id}", headers=headers["write"])
    assert resp.status_code == 200
    assert resp.content == raw_pdf_content
    assert resp.headers["content-type"] == "application/pdf"


def test_document_raw_route_anonymous_blocked(raw_test_context):
    client, _, doc_id, _, _, _, _, _ = raw_test_context
    resp = client.get(f"/document/raw?id={doc_id}")
    assert resp.status_code == 404


def test_document_raw_route_readonly_blocked(raw_test_context):
    client, _, doc_id, _, _, _, _, headers = raw_test_context
    resp = client.get(f"/document/raw?id={doc_id}", headers=headers["readonly"])
    # A credential of the account whose scope is too small gets 403 (SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md Section 6.6).
    assert (resp.status_code, resp.json()) == (403, {"error": "scope_insufficient", "scope": "readonly", "required": "write"})


def test_document_raw_route_path_traversal_blocked(raw_test_context):
    client, _, _, _, doc_id_evil, _, _, headers = raw_test_context
    resp = client.get(f"/document/raw?id={doc_id_evil}", headers=headers["owner"])
    assert resp.status_code == 404


def test_document_raw_missing_id(raw_test_context):
    client, _, _, _, _, _, _, headers = raw_test_context
    resp = client.get("/document/raw", headers=headers["owner"])
    assert resp.status_code == 400


def test_document_raw_route_head_request(raw_test_context):
    client, _, doc_id, _, _, _, _, headers = raw_test_context
    resp = client.head(f"/document/raw?id={doc_id}", headers=headers["write"])
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert "secret_research.pdf" in resp.headers["content-disposition"]


def test_robots_txt_contains_disallow_raw(raw_test_context):
    client, _, _, _, _, _, _, _ = raw_test_context
    resp = client.get("/robots.txt")
    assert resp.status_code == 200
    assert "Disallow: /document/raw" in resp.text


def test_document_raw_from_presentation_pdf_meta(raw_test_context):
    client, _, _, doc_id_meta, _, _, art_pdf_content, headers = raw_test_context
    resp = client.get(f"/document/raw?id={doc_id_meta}", headers=headers["write"])
    assert resp.status_code == 200
    assert resp.content == art_pdf_content


def test_e2e_role_gated_raw_document_access(raw_test_context):
    """End-to-end test verifying document detail, share page gating, and raw file download access control."""
    client, settings, doc_id, _, _, raw_pdf_content, _, headers = raw_test_context

    # Step 1: Create a share token for the document
    share_resp = client.post("/share", json={"doc_id": doc_id}, headers=headers["owner"])
    assert share_resp.status_code == 200
    share_token = share_resp.json()["token"]

    # Step 2: Anonymous user accesses shared doc page
    old_link = client.get(f"/p?s={share_token}", follow_redirects=False)
    assert old_link.status_code == 308 and old_link.headers["location"] == f"/silvercord/{share_token}"
    anon_page_resp = client.get(f"/silvercord/{share_token}")
    assert anon_page_resp.status_code == 200
    # Embedded JSON must have can_access_raw False
    assert '"can_access_raw": false' in anon_page_resp.text or '"can_access_raw":false' in anon_page_resp.text
    # Server path must not leak
    assert "/raw/files/" not in anon_page_resp.text
    assert "file://" not in anon_page_resp.text

    # Step 3: The item view never has the raw file link (PERMALINK_UNIFICATION_DESIGN.md Section 6): a member gets it in the workspace. The account of this server has no USERNAME, thus no workspace path: each caller gets the item view.
    collab_page_resp = client.get(f"/silvercord/{share_token}", headers=headers["write"])
    assert collab_page_resp.status_code == 200
    assert '"can_access_raw": false' in collab_page_resp.text or '"can_access_raw":false' in collab_page_resp.text

    # Step 4: Access raw file endpoint with different roles
    # Anonymous -> 404
    anon_raw_resp = client.get(f"/document/raw?id={doc_id}")
    assert anon_raw_resp.status_code == 404

    # Readonly -> 403
    readonly_raw_resp = client.get(f"/document/raw?id={doc_id}", headers=headers["readonly"])
    assert readonly_raw_resp.status_code == 403

    # Write scope -> 200 with raw binary content
    collab_raw_resp = client.get(f"/document/raw?id={doc_id}", headers=headers["write"])
    assert collab_raw_resp.status_code == 200
    assert collab_raw_resp.content == raw_pdf_content

    # Owner -> 200 with raw binary content
    owner_raw_resp = client.get(f"/document/raw?id={doc_id}", headers=headers["owner"])
    assert owner_raw_resp.status_code == 200
    assert owner_raw_resp.content == raw_pdf_content
