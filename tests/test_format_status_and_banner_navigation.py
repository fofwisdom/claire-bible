"""Unit and End-to-End tests for format status diagnostics, sample resolution, and banner navigation healing."""

from __future__ import annotations

import time
from pathlib import Path
from starlette.testclient import TestClient

from claire.api.server import create_app
from claire.config import get_settings
from claire.extract.provider import MockProvider
from claire.ontology.base import Document
from claire.store import db as dbm
from claire.store import queries


OWNER_TOKEN = "owner-" + ("o" * 32)
OWNER_HEADERS = {"Authorization": f"Bearer {OWNER_TOKEN}"}


def test_format_status_samples_unit(tmp_path: Path):
    """Unit test for module interface between db.get_format_status, documents_timeline, and queries.documents_list."""
    db_file = tmp_path / "claire.db"
    conn = dbm.connect(db_file)
    dbm.init_db(conn)

    now = time.time()
    # 1. Document with matching target format (adoc)
    conn.execute(
        """
        INSERT INTO documents (id, title, url, raw_text, content_hash, fetched_at, source_type, detail, detail_format)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("doc_match", "Adoc Document", "https://example.com/adoc", "raw content 1", "hash1", now - 20, "web", "== Detail", "adoc"),
    )
    # 2. Document with mismatched format (md when target is adoc)
    conn.execute(
        """
        INSERT INTO documents (id, title, url, raw_text, content_hash, fetched_at, source_type, detail, detail_format)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("doc_mismatch", "Markdown Document", "https://example.com/md", "raw content 2", "hash2", now - 10, "web", "## Detail", "md"),
    )
    # 3. Document missing detail
    conn.execute(
        """
        INSERT INTO documents (id, title, url, raw_text, content_hash, fetched_at, source_type, detail, detail_format)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("doc_missing", "Missing Detail Document", "https://example.com/missing", "raw content 3", "hash3", now, "web", None, None),
    )
    conn.commit()

    # Verify db.get_format_status returns samples
    status = dbm.get_format_status(conn, "adoc")
    assert status["total_docs"] == 3
    assert status["matching_docs"] == 1
    assert status["mismatched_docs"] == 1
    assert status["missing_detail_docs"] == 1
    assert status["needs_migration"] is True

    assert len(status["missing_docs_sample"]) == 1
    assert status["missing_docs_sample"][0]["id"] == "doc_missing"
    assert status["missing_docs_sample"][0]["title"] == "Missing Detail Document"

    assert len(status["mismatched_docs_sample"]) == 1
    assert status["mismatched_docs_sample"][0]["id"] == "doc_mismatch"
    assert status["mismatched_docs_sample"][0]["title"] == "Markdown Document"
    assert status["mismatched_docs_sample"][0]["detail_format"] == "md"

    # Verify queries.documents_list contains has_detail and detail_format
    docs = queries.documents_list(conn)
    doc_map = {d["id"]: d for d in docs}
    assert doc_map["doc_match"]["has_detail"] is True
    assert doc_map["doc_match"]["detail_format"] == "adoc"
    assert doc_map["doc_mismatch"]["has_detail"] is True
    assert doc_map["doc_mismatch"]["detail_format"] == "md"
    assert doc_map["doc_missing"]["has_detail"] is False

    conn.close()


def test_e2e_format_status_banner_navigation_and_healing(tmp_path: Path, monkeypatch):
    """E2E test: Verify /documents exposes format status samples for UI navigation and healing via /document/regenerate."""
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)
    db_file = data_dir / "claire.db"
    vault_dir = tmp_path / "vault"
    vault_dir.mkdir(parents=True)

    monkeypatch.setenv("CLAIRE_DATA_DIR", str(data_dir))
    monkeypatch.setenv("CLAIRE_DB_PATH", str(db_file))
    monkeypatch.setenv("CLAIRE_VAULT_DIR", str(vault_dir))
    monkeypatch.setenv("CLAIRE_RENDER_FORMAT", "adoc")
    monkeypatch.setenv("CLAIRE_ALLOW_PURGE", "1")
    monkeypatch.setenv("CLAIRE_INJECT_TOKEN", OWNER_TOKEN)

    get_settings.cache_clear()
    settings = get_settings()

    conn = dbm.connect(db_file)
    dbm.init_db(conn)

    now = time.time()
    # 1. Complete document with adoc detail
    conn.execute(
        """
        INSERT INTO documents (id, title, url, raw_text, content_hash, fetched_at, source_type, detail, detail_format)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("doc_ready", "Complete Document", "https://example.com/ready", "Valid raw body content", "hash_ready", now - 100, "web", "== Detail", "adoc"),
    )
    # 2. Document missing detail (triggers format_missing banner)
    conn.execute(
        """
        INSERT INTO documents (id, title, url, raw_text, content_hash, fetched_at, source_type, detail, detail_format)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("doc_target", "Target Unrendered Doc", "https://example.com/target", "Another valid raw body content for detail render", "hash_target", now, "web", None, None),
    )
    conn.commit()
    conn.close()

    app = create_app(settings)
    client = TestClient(app, base_url=settings.public_url)

    # 1. Initial GET /documents - verify banner triggering contract
    res = client.get("/documents", headers=OWNER_HEADERS)
    assert res.status_code == 200
    data = res.json()

    fs = data.get("format_status", {})
    assert fs.get("needs_migration") is True
    assert fs.get("missing_detail_docs") == 1
    assert fs.get("mismatched_docs") == 0

    samples = fs.get("missing_docs_sample", [])
    assert len(samples) == 1
    assert samples[0]["id"] == "doc_target"
    assert samples[0]["title"] == "Target Unrendered Doc"

    docs = data.get("documents", [])
    assert len(docs) == 2
    doc_map = {d["id"]: d for d in docs}
    assert doc_map["doc_target"]["has_detail"] is False
    assert doc_map["doc_ready"]["has_detail"] is True

    # 2. Trigger in-place detail healing via /document/regenerate (simulates user clicking '가독 렌더링 생성')
    regen_res = client.post(
        "/document/regenerate",
        json={"target": "doc_target", "component": "detail", "dry_run": False},
        headers=OWNER_HEADERS,
    )
    assert regen_res.status_code == 200
    regen_data = regen_res.json()
    assert regen_data.get("count") == 1

    # 3. Subsequent GET /documents - verify banner is cleared (needs_migration is False)
    res_healed = client.get("/documents", headers=OWNER_HEADERS)
    assert res_healed.status_code == 200
    data_healed = res_healed.json()

    fs_healed = data_healed.get("format_status", {})
    assert fs_healed.get("needs_migration") is False
    assert fs_healed.get("missing_detail_docs") == 0
    assert len(fs_healed.get("missing_docs_sample", [])) == 0

    docs_healed = data_healed.get("documents", [])
    doc_map_healed = {d["id"]: d for d in docs_healed}
    assert doc_map_healed["doc_target"]["has_detail"] is True
    assert doc_map_healed["doc_target"]["detail_format"] == "adoc"
