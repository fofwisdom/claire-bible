"""End-to-End (E2E) test for inbound local PDF ingestion and zero-exposure URI sharing."""

from __future__ import annotations

import json
from pathlib import Path

from starlette.testclient import TestClient

from claire.api.server import create_app
from claire.config import Settings, get_settings
from claire.extract.provider import MockProvider
from claire.ingest.service import IngestService
from claire.store import db as dbm


OWNER_TOKEN = "owner-" + ("o" * 32)
OWNER_HEADERS = {"Authorization": f"Bearer {OWNER_TOKEN}"}


def test_e2e_local_pdf_ingest_and_share_journey(tmp_path: Path, monkeypatch):
    """E2E journey: Inbound Telegram PDF saved to raw/files -> Ingest -> Web API query -> Public share page.
    Verifies that original PDF is preserved in raw storage, but local URI is never exposed on web or public shares.
    """
    db_file = tmp_path / "claire_e2e.db"
    vault_dir = tmp_path / "vault"
    data_dir = tmp_path / "data"
    vault_dir.mkdir(parents=True)
    data_dir.mkdir(parents=True)

    monkeypatch.setenv("CLAIRE_DB_PATH", str(db_file))
    monkeypatch.setenv("CLAIRE_DATA_DIR", str(data_dir))
    monkeypatch.setenv("CLAIRE_VAULT_DIR", str(vault_dir))
    monkeypatch.setenv("CLAIRE_INJECT_TOKEN", OWNER_TOKEN)
    monkeypatch.setenv("CLAIRE_PUBLIC_URL", "https://cb.example.com")

    get_settings.cache_clear()
    settings = get_settings()

    conn = dbm.connect(db_file)
    dbm.init_db(conn)
    conn.close()

    mock_provider = MockProvider()
    svc = IngestService(settings, provider=mock_provider)

    # 1. Simulate inbound PDF from Telegram
    inbox_seq = 707518267
    pdf_filename = "브루탈리즘 건축법 연구 - 사례를 중심으로.pdf"
    raw_pdf_bytes = b"%PDF-1.4 mock binary content of brutalism architecture study"
    inbound_tmp = tmp_path / "telegram_tmp.pdf"
    inbound_tmp.write_bytes(raw_pdf_bytes)

    # Inbound file is permanently saved to raw/files
    kept_path = svc.save_inbound_file(inbox_seq, inbound_tmp, pdf_filename)
    assert Path(kept_path).is_file()
    assert Path(kept_path).read_bytes() == raw_pdf_bytes
    assert "raw/files" in kept_path

    # Ingest inbound file
    monkeypatch.setattr(
        "claire.ingest.fetchers.pdf.extract_pdf_bytes",
        lambda data, url=None, fallback_title=None: (
            "브루탈리즘 건축법 연구: 사례를 중심으로",
            "브루탈리즘 건축은 제2차 세계대전 이후 기능주의 원리로의 복귀를 추구하였다.",
            [],
            {},
            None,
            [],
        ),
    )

    report = svc.ingest(
        kept_path,
        source="telegram",
        inbox_kind="document",
        file_ref=kept_path,
        file_name=pdf_filename,
    )
    assert report.error is None
    doc_id = report.document_id
    assert doc_id is not None

    # 2. Check Database Layer: original file is preserved in raw_inbox, but documents.url is None
    conn2 = dbm.connect_existing(db_file)
    inbox_row = conn2.execute("SELECT * FROM raw_inbox WHERE document_id=?", (doc_id,)).fetchone()
    assert inbox_row is not None
    assert inbox_row["file_ref"] == kept_path
    assert inbox_row["file_name"] == pdf_filename

    doc_row = conn2.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
    assert doc_row is not None
    assert doc_row["url"] is None

    # Add a legacy document directly into DB to test backward-compatibility sanitization
    legacy_doc_id = "doc_legacy_in_db"
    legacy_file_path = str(data_dir / "raw" / "files" / "9999_legacy.pdf")
    legacy_file_uri = f"file://{legacy_file_path}"
    conn2.execute(
        "INSERT INTO documents (id, title, url, canonical_url, raw_text, source_type, fetched_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (legacy_doc_id, "레거시 로컬 PDF", legacy_file_uri, legacy_file_uri, "레거시 본문", "pdf", 1000.0),
    )
    conn2.commit()
    conn2.close()

    # 3. Test Web API endpoints
    app = create_app(settings)
    client = TestClient(app, base_url="https://cb.example.com")

    # A. GET /document for new ingested document
    resp_doc = client.get(f"/document?id={doc_id}")
    assert resp_doc.status_code == 200
    doc_data = resp_doc.json()
    assert doc_data["id"] == doc_id
    assert doc_data["url"] is None

    # B. GET /document for legacy document (with file:// URL in SQLite DB)
    resp_legacy = client.get(f"/document?id={legacy_doc_id}")
    assert resp_legacy.status_code == 200
    legacy_data = resp_legacy.json()
    assert legacy_data["id"] == legacy_doc_id
    assert legacy_data["url"] is None
    assert "file://" not in json.dumps(legacy_data)

    # C. Create shares and test public share pages (/p?s=...)
    resp_share = client.post("/share", json={"doc_id": doc_id}, headers=OWNER_HEADERS)
    assert resp_share.status_code == 200
    token = resp_share.json()["token"]

    resp_share_legacy = client.post("/share", json={"doc_id": legacy_doc_id}, headers=OWNER_HEADERS)
    assert resp_share_legacy.status_code == 200
    token_legacy = resp_share_legacy.json()["token"]

    # D. GET /p?s= for new ingested document
    resp_page = client.get(f"/p?s={token}")
    assert resp_page.status_code == 200
    page_html = resp_page.text
    assert "브루탈리즘 건축법 연구" in page_html
    assert "file://" not in page_html
    assert "telegram_tmp" not in page_html
    assert '<a href="file:' not in page_html

    # E. GET /p?s= for legacy document
    resp_legacy_page = client.get(f"/p?s={token_legacy}")
    assert resp_legacy_page.status_code == 200
    legacy_page_html = resp_legacy_page.text
    assert "레거시 로컬 PDF" in legacy_page_html
    assert "file://" not in legacy_page_html
    assert "9999_legacy.pdf" not in legacy_page_html
    assert '<a href="file:' not in legacy_page_html
