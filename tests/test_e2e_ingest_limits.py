"""End-to-end (E2E) test for situational load restriction WebUI/API management and ingest pipeline."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from claire.api.server import create_app
from claire.config import Settings, get_settings
from claire.ingest.pipeline import ingest
from claire.store import db as dbm


def test_e2e_ingest_limits_lifecycle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """E2E test: update limits via API and verify the ingestion pipeline respects new limits."""
    owner_token = "e2e-owner-token-12345678901234567890"
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    db_file = data_dir / "claire.db"
    vault_dir = tmp_path / "vault"
    vault_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("CLAIRE_DB_PATH", str(db_file))
    monkeypatch.setenv("CB_DATA_DIR", str(data_dir))

    # Initialize SQLite database
    conn = dbm.connect(db_file)
    dbm.init_db(conn)
    conn.close()

    settings = Settings(
        CLAIRE_ENVIRONMENT="development",
        CLAIRE_INJECT_TOKEN=owner_token,
        CLAIRE_DB_PATH=str(db_file),
        CLAIRE_VAULT_PATH=str(vault_dir),
        CLAIRE_PUBLIC_URL="http://127.0.0.1:8765",
        provider="mock",
        anonymous_readonly=False,
    )

    app = create_app(settings)
    client = TestClient(app, base_url="http://127.0.0.1:8765")
    headers = {"Authorization": f"Bearer {owner_token}"}

    # Step 1: Verify initial default limit (raw_char_budget = 20,000)
    resp = client.get("/limits", headers=headers)
    assert resp.status_code == 200
    cfg = resp.json()
    assert cfg["limits"]["raw_char_budget"] == 20000

    # Step 2: Ingest a 10,000 character file under default 20,000 budget
    sample_file1 = tmp_path / "doc1.txt"
    sample_file1.write_text("A" * 10000, encoding="utf-8")

    ingest_resp1 = client.post(
        "/ingest",
        headers=headers,
        json={"payload": str(sample_file1)},
    )
    assert ingest_resp1.status_code == 200
    doc_id1 = ingest_resp1.json()["document_id"]

    conn = dbm.connect(db_file)
    doc1 = dbm.get_document(conn, doc_id1)
    conn.close()

    assert doc1 is not None
    assert len(doc1.raw_text) == 10000
    assert doc1.meta.get("raw_truncated") is False
    assert doc1.meta.get("raw_chars") == 10000

    # Step 3: Update load restriction via API: set raw_char_budget = 4,000
    patch_resp = client.patch(
        "/limits",
        headers=headers,
        json={
            "limits": {
                "raw_char_budget": 4000,
                "slicing_strategy": "strict",
            }
        },
    )
    assert patch_resp.status_code == 200
    assert patch_resp.json()["config"]["limits"]["raw_char_budget"] == 4000

    # Verify disk persistence in data/ingest_limits.json
    limits_path = data_dir / "ingest_limits.json"
    assert limits_path.is_file()
    saved = json.loads(limits_path.read_text(encoding="utf-8"))
    assert saved["limits"]["raw_char_budget"] == 4000

    # Step 4: Ingest another 10,000 character file under the new 4,000 budget
    sample_file2 = tmp_path / "doc2.txt"
    sample_file2.write_text("B" * 10000, encoding="utf-8")

    ingest_resp2 = client.post(
        "/ingest",
        headers=headers,
        json={"payload": str(sample_file2)},
    )
    assert ingest_resp2.status_code == 200
    doc_id2 = ingest_resp2.json()["document_id"]

    conn = dbm.connect(db_file)
    doc2 = dbm.get_document(conn, doc_id2)
    conn.close()

    assert doc2 is not None
    # Content must now be truncated to 4,000 characters by the new limit
    assert len(doc2.raw_text) == 4000
    assert doc2.meta.get("raw_truncated") is True
    assert doc2.meta.get("orig_chars") == 10000
    assert doc2.meta.get("raw_chars") == 4000
