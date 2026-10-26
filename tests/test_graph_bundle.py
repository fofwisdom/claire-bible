"""Tests for graph bundle export, import, table filtering, 24-hour TTL, and CLI commands."""

from __future__ import annotations

import io
import json
import sqlite3
import tarfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import zstandard as zstd

from claire.cli import build_parser
from claire.graph_bundle import (
    GRAPH_BUNDLE_TTL_SECONDS,
    _GRAPH_BUNDLE_TOKEN_PREFIX,
    create_graph_bundle,
    get_graph_bundle,
    import_graph_bundle,
    list_active_graph_bundles,
    lookup_graph_bundle_by_token,
    purge_expired_graph_bundles,
)
from claire.store import db as dbm


@dataclass
class StubSettings:
    db_file: Path
    data_dir: Path
    environment: str = "development"
    public_url: str = "http://127.0.0.1:8765"
    inject_token: str = "owner-token"
    multi_theme: bool = False

    @property
    def effective_provider(self) -> str:
        return "mock"

    def model_dump(self) -> dict[str, Any]:
        return {
            "environment": self.environment,
            "public_url": self.public_url,
            "inject_token": self.inject_token,
        }


def _seed_source_database(db_path: Path) -> None:
    """Create test tables and data including sensitive and heavy tables."""
    conn = dbm.connect(db_path)
    dbm.init_db(conn)

    # 1. Documents
    conn.execute(
        """
        INSERT INTO documents (
            id, url, canonical_url, title, author, published_at, fetched_at, raw_text, source_type
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "doc_test_1",
            "https://example.com/doc1",
            "https://example.com/doc1",
            "Test Document 1",
            "Author One",
            "2026-10-26T00:00:00Z",
            1761436800.0,
            "SENSITIVE_HEAVY_RAW_TEXT_THAT_MUST_BE_EXCLUDED",
            "web",
        ),
    )

    # 2. Entities
    conn.execute(
        """
        INSERT INTO entities (
            id, type, name, norm_name, aliases, props, observations, sources, provisional, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "ent_1",
            "Concept",
            "Quantum Engine",
            "quantum engine",
            json.dumps(["Q-Engine"]),
            json.dumps({"domain": "physics"}),
            json.dumps(["Observed in test lab"]),
            json.dumps(["doc_test_1"]),
            0,
            1761436800.0,
            1761436800.0,
        ),
    )
    conn.execute(
        """
        INSERT INTO entities (
            id, type, name, norm_name, aliases, props, observations, sources, provisional, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "ent_2",
            "Device",
            "Flux Capacitor",
            "flux capacitor",
            json.dumps(["Capacitor"]),
            json.dumps({"voltage": "1.21GW"}),
            json.dumps(["Found in vehicle"]),
            json.dumps(["doc_test_1"]),
            0,
            1761436800.0,
            1761436800.0,
        ),
    )

    # 3. Relations
    conn.execute(
        """
        INSERT INTO relations (
            id, type, source_id, target_id, confidence, props, sources, provisional, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "rel_1",
            "powers",
            "ent_1",
            "ent_2",
            0.95,
            json.dumps({"notes": "verified connection"}),
            json.dumps(["doc_test_1"]),
            0,
            1761436800.0,
        ),
    )

    # 4. Embeddings (MUST BE EXCLUDED)
    conn.execute(
        """
        INSERT INTO embeddings (owner_id, dim, vector, model, updated_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        ("ent_1", 3, b"\x00\x01\x02\x03\x04\x05", "test-embedding-model", 1761436800.0),
    )

    # 5. OAuth and Raw Inbox (MUST BE EXCLUDED)
    conn.execute(
        """
        INSERT INTO oauth_tokens (token, refresh_token, client_id, scope, created_at, expires_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        ("secret_token_123", "refresh_secret_456", "client_xyz", "owner", 1761436800.0, 1761440400.0),
    )

    conn.execute(
        """
        INSERT INTO raw_inbox (received_at, source, payload)
        VALUES (?, ?, ?)
        """,
        (1761436800.0, "telegram", "secret raw webhook body"),
    )

    conn.commit()
    conn.close()


def test_non_blocking_readonly_export(tmp_path: Path) -> None:
    """Test that export operates non-blocking via read-only SQLite snapshot."""
    db_file = tmp_path / "claire.db"
    _seed_source_database(db_file)

    settings = StubSettings(db_file=db_file, data_dir=tmp_path)

    # Open active writer connection in WAL mode
    write_conn = sqlite3.connect(str(db_file), timeout=5.0)
    write_conn.execute("PRAGMA journal_mode=WAL;")

    # Export while source db writer connection is open
    info = create_graph_bundle(settings, theme_id=0)

    assert info.bundle_id.startswith("gb_")
    assert info.entities_count == 2
    assert info.relations_count == 1
    assert info.documents_count == 1
    assert info.filepath.is_file()

    # Verify writer can continue writing without lock issues
    write_conn.execute(
        "INSERT INTO documents (id, title, url, raw_text, source_type) VALUES (?, ?, ?, ?, ?)",
        ("doc_concurrent", "Concurrent Doc", "https://example.com/c", "raw", "web"),
    )
    write_conn.commit()
    write_conn.close()


def test_table_filtering_and_sanitization(tmp_path: Path) -> None:
    """Test that only entities, relations, and sanitized documents metadata are exported."""
    db_file = tmp_path / "claire.db"
    _seed_source_database(db_file)

    settings = StubSettings(db_file=db_file, data_dir=tmp_path)
    info = create_graph_bundle(settings, theme_id=0)

    # Decompress bundle archive
    dctx = zstd.ZstdDecompressor()
    extracted_db_path = tmp_path / "extracted_graph_export.db"
    manifest_data = None

    with open(info.filepath, "rb") as f_in:
        with dctx.stream_reader(f_in) as zstd_reader:
            with tarfile.open(fileobj=zstd_reader, mode="r|") as tar:
                for member in tar:
                    if member.name.endswith("manifest.json"):
                        f = tar.extractfile(member)
                        assert f is not None
                        manifest_data = json.loads(f.read().decode("utf-8"))
                    elif member.name.endswith("graph_export.db"):
                        f = tar.extractfile(member)
                        assert f is not None
                        extracted_db_path.write_bytes(f.read())

    assert manifest_data is not None
    assert manifest_data["entities_count"] == 2
    assert manifest_data["relations_count"] == 1
    assert manifest_data["documents_count"] == 1
    assert "graph_export_sha256" in manifest_data

    # Inspect the exported SQLite database
    conn = sqlite3.connect(str(extracted_db_path))
    conn.row_factory = sqlite3.Row

    # 1. Check existing tables: MUST ONLY be entities, relations, documents
    tables = {
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert "entities" in tables
    assert "relations" in tables
    assert "documents" in tables

    # Prohibited tables must NOT exist
    assert "embeddings" not in tables
    assert "oauth_tokens" not in tables
    assert "oauth_clients" not in tables
    assert "raw_inbox" not in tables
    assert "vault.db" not in tables
    assert "telemetry.db" not in tables

    # 2. Check documents table structure: MUST OMIT raw_text
    doc_cols = {r["name"] for r in conn.execute("PRAGMA table_info(documents)").fetchall()}
    assert "id" in doc_cols
    assert "title" in doc_cols
    assert "url" in doc_cols
    assert "source_type" in doc_cols
    assert "created_at" in doc_cols
    assert "raw_text" not in doc_cols

    # Verify document rows
    doc_rows = conn.execute("SELECT * FROM documents").fetchall()
    assert len(doc_rows) == 1
    assert doc_rows[0]["id"] == "doc_test_1"
    assert doc_rows[0]["title"] == "Test Document 1"

    # 3. Check entities rows
    ent_rows = conn.execute("SELECT * FROM entities ORDER BY id").fetchall()
    assert len(ent_rows) == 2
    assert ent_rows[0]["id"] == "ent_1"
    assert ent_rows[0]["name"] == "Quantum Engine"
    assert ent_rows[1]["id"] == "ent_2"
    assert ent_rows[1]["name"] == "Flux Capacitor"

    # 4. Check relations rows
    rel_rows = conn.execute("SELECT * FROM relations").fetchall()
    assert len(rel_rows) == 1
    assert rel_rows[0]["id"] == "rel_1"
    assert rel_rows[0]["source_id"] == "ent_1"
    assert rel_rows[0]["target_id"] == "ent_2"
    assert rel_rows[0]["type"] == "powers"

    # 5. Check indices
    indices = {
        r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()
    }
    assert "idx_entities_id_type_name" in indices
    assert "idx_relations_id_src_tgt_type" in indices

    conn.close()


def test_ttl_and_token_verification(tmp_path: Path) -> None:
    """Test 24-hour TTL lifecycle and cryptographic token sidecar validation."""
    db_file = tmp_path / "claire.db"
    _seed_source_database(db_file)

    t0 = 1761400000.0
    settings = StubSettings(db_file=db_file, data_dir=tmp_path)

    # 1. Create bundle
    info = create_graph_bundle(settings, theme_id=0, now_epoch=t0)
    token = info.token

    assert token.startswith(_GRAPH_BUNDLE_TOKEN_PREFIX)
    assert info.expires_at == t0 + GRAPH_BUNDLE_TTL_SECONDS
    assert info.filepath.is_file()

    # 2. Lookup before expiration
    rec = lookup_graph_bundle_by_token(tmp_path, token)
    assert rec is not None
    assert rec["bundle_id"] == info.bundle_id
    assert rec["entities_count"] == 2

    # 3. Active bundle list
    active = list_active_graph_bundles(tmp_path, now_epoch=t0 + 3600)
    assert len(active) == 1
    assert active[0]["bundle_id"] == info.bundle_id

    # 4. Lookup with invalid token returns None
    assert lookup_graph_bundle_by_token(tmp_path, "invalid_token_123") is None
    assert lookup_graph_bundle_by_token(tmp_path, f"{_GRAPH_BUNDLE_TOKEN_PREFIX}tampered") is None

    # 5. Access right before expiration (23h 59m)
    assert get_graph_bundle(tmp_path, token, now_epoch=t0 + GRAPH_BUNDLE_TTL_SECONDS - 60) is not None

    # 6. Access at or after expiration (24h + 1s) -> auto-purged
    expired_record = get_graph_bundle(tmp_path, token, now_epoch=t0 + GRAPH_BUNDLE_TTL_SECONDS + 1)
    assert expired_record is None

    # Verify file and sidecar were deleted
    assert not info.filepath.exists()
    assert lookup_graph_bundle_by_token(tmp_path, token) is None

    # 7. Purge test with older orphan bundle
    orphan_archive = tmp_path / "graph_bundles" / "graph_bundle_gb_orphan.tar.zst"
    orphan_archive.write_bytes(b"dummy")
    # Set mtime to 48 hours ago
    past = t0 - (48 * 3600)
    import os
    os.utime(orphan_archive, (past, past))

    purged = purge_expired_graph_bundles(tmp_path, now_epoch=t0)
    assert purged >= 1
    assert not orphan_archive.exists()


def test_import_graph_bundle_restoration(tmp_path: Path) -> None:
    """Test importing a bundle archive restores entities, relations, and documents."""
    src_db = tmp_path / "source.db"
    _seed_source_database(src_db)

    settings = StubSettings(db_file=src_db, data_dir=tmp_path)
    info = create_graph_bundle(settings, theme_id=0)

    # Prepare fresh target database
    target_db = tmp_path / "target.db"
    assert not target_db.exists()

    # Import bundle into target database
    result = import_graph_bundle(target_db_path=target_db, bundle_path=info.filepath)

    assert result["entities_count"] == 2
    assert result["relations_count"] == 1
    assert result["documents_count"] == 1
    assert result["bundle_id"] == info.bundle_id

    # Verify content in target database
    conn = sqlite3.connect(str(target_db))
    conn.row_factory = sqlite3.Row

    ents = conn.execute("SELECT * FROM entities ORDER BY id").fetchall()
    assert len(ents) == 2
    assert ents[0]["name"] == "Quantum Engine"
    assert ents[0]["norm_name"] == "quantum engine"
    assert ents[1]["name"] == "Flux Capacitor"

    rels = conn.execute("SELECT * FROM relations").fetchall()
    assert len(rels) == 1
    assert rels[0]["type"] == "powers"
    assert rels[0]["source_id"] == "ent_1"
    assert rels[0]["target_id"] == "ent_2"
    assert rels[0]["confidence"] == 0.95

    docs = conn.execute("SELECT * FROM documents").fetchall()
    assert len(docs) == 1
    assert docs[0]["id"] == "doc_test_1"
    assert docs[0]["title"] == "Test Document 1"
    assert docs[0]["url"] == "https://example.com/doc1"

    conn.close()


def test_e2e_cli_export_and_import(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """E2E test: run CLI export-graph and import-graph subcommands end-to-end."""
    src_db = tmp_path / "e2e_source.db"
    _seed_source_database(src_db)

    settings = StubSettings(db_file=src_db, data_dir=tmp_path)
    monkeypatch.setattr("claire.cli.get_settings", lambda: settings)

    parser = build_parser()

    # 1. Run export-graph CLI with --output and --json
    output_bundle = tmp_path / "custom_bundle.tar.zst"
    args_export = parser.parse_args(["export-graph", "--theme", "0", "--output", str(output_bundle), "--json"])
    ret_export = args_export.func(args_export)
    assert ret_export == 0

    out_export = capsys.readouterr().out
    export_json = json.loads(out_export)
    assert export_json["entities_count"] == 2
    assert export_json["relations_count"] == 1
    assert export_json["documents_count"] == 1
    assert output_bundle.is_file()

    # 2. Run import-graph CLI with --target-db and --json
    target_db = tmp_path / "e2e_target.db"
    args_import = parser.parse_args(["import-graph", str(output_bundle), "--target-db", str(target_db), "--json"])
    ret_import = args_import.func(args_import)
    assert ret_import == 0

    out_import = capsys.readouterr().out
    import_json = json.loads(out_import)
    assert import_json["entities_count"] == 2
    assert import_json["relations_count"] == 1
    assert import_json["documents_count"] == 1

    # Verify target database content directly
    conn = sqlite3.connect(str(target_db))
    conn.row_factory = sqlite3.Row
    assert len(conn.execute("SELECT * FROM entities").fetchall()) == 2
    assert len(conn.execute("SELECT * FROM relations").fetchall()) == 1
    assert len(conn.execute("SELECT * FROM documents").fetchall()) == 1
    conn.close()
