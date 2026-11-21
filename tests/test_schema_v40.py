"""Schema v40 tests: OAuth 2.1 scope persistence, token family rotation, pending tx, and device codes (AE2a/AE2b)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from claire.store import db as dbm


def _connect(db_file: Path) -> sqlite3.Connection:
    return dbm.connect(db_file)


def test_v40_fresh_schema_has_oauth_v21_columns_and_tables(tmp_path: Path) -> None:
    conn = _connect(tmp_path / "claire.db")
    dbm.init_db(conn)
    try:
        assert int(conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0]) == dbm.SCHEMA_VERSION == 40
        code_cols = {r["name"] for r in conn.execute("PRAGMA table_info(oauth_codes)").fetchall()}
        assert "granted_scope" in code_cols

        tok_cols = {r["name"] for r in conn.execute("PRAGMA table_info(oauth_tokens)").fetchall()}
        assert {"granted_scope", "token_family_id", "refresh_expires_at", "rotated_at", "revoked_at", "last_used_at"} <= tok_cols

        tables = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        assert {"oauth_pending_requests", "oauth_device_codes"} <= tables

        indexes = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()}
        assert "idx_oauth_tokens_family" in indexes
    finally:
        conn.close()


def test_v39_to_v40_migration_backfills_existing_oauth_tokens(tmp_path: Path) -> None:
    db_file = tmp_path / "v39.db"
    conn = _connect(db_file)
    dbm.init_db(conn)
    conn.execute("DROP INDEX IF EXISTS idx_oauth_tokens_family")
    conn.execute("DROP INDEX IF EXISTS idx_oauth_tokens_refresh")
    conn.execute("DROP TABLE oauth_tokens")
    conn.execute("DROP TABLE oauth_codes")
    conn.execute("DROP TABLE oauth_pending_requests")
    conn.execute("DROP TABLE oauth_device_codes")
    conn.executescript(
        """
        CREATE TABLE oauth_codes (
            code TEXT PRIMARY KEY,
            client_id TEXT NOT NULL,
            redirect_uri TEXT NOT NULL,
            code_challenge TEXT NOT NULL,
            code_challenge_method TEXT DEFAULT 'S256',
            scope TEXT DEFAULT 'readonly',
            expires_at REAL NOT NULL,
            account_id INTEGER DEFAULT 1
        );
        CREATE TABLE oauth_tokens (
            token TEXT PRIMARY KEY,
            refresh_token TEXT UNIQUE,
            client_id TEXT NOT NULL,
            scope TEXT DEFAULT 'readonly',
            created_at REAL NOT NULL,
            expires_at REAL NOT NULL,
            account_id INTEGER DEFAULT 1
        );
        CREATE INDEX IF NOT EXISTS idx_oauth_tokens_refresh ON oauth_tokens(refresh_token);
        """
    )
    conn.execute(
        "INSERT INTO oauth_clients (client_id, client_secret, client_name, redirect_uris, created_at) VALUES (?, ?, ?, ?, 1.0)",
        ("legacy_client", "sec", "Legacy", '["https://example.com/cb"]'),
    )
    conn.execute(
        "INSERT INTO oauth_codes (code, client_id, redirect_uri, code_challenge, code_challenge_method, scope, expires_at, account_id) "
        "VALUES ('legacy_code_1', 'legacy_client', 'https://example.com/cb', 'challenge', 'S256', 'readonly', 9999999999.0, 1)"
    )
    conn.execute(
        "INSERT INTO oauth_tokens (token, refresh_token, client_id, scope, created_at, expires_at, account_id) "
        "VALUES ('legacy_access_token_value_123456', 'legacy_refresh_token_value_12345', 'legacy_client', 'readonly', 1.0, 9999999999.0, 1)"
    )
    conn.execute("UPDATE meta SET value='39' WHERE key='schema_version'")
    conn.commit()
    conn.close()

    conn = _connect(db_file)
    dbm.init_db(conn)
    try:
        assert int(conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0]) == 40
        code_cols = {r["name"] for r in conn.execute("PRAGMA table_info(oauth_codes)").fetchall()}
        assert "granted_scope" in code_cols
        code_row = conn.execute("SELECT granted_scope FROM oauth_codes WHERE code='legacy_code_1'").fetchone()
        assert code_row is not None
        assert code_row["granted_scope"] == "readonly"

        tok_cols = {r["name"] for r in conn.execute("PRAGMA table_info(oauth_tokens)").fetchall()}
        assert {"granted_scope", "token_family_id", "refresh_expires_at", "rotated_at", "revoked_at", "last_used_at"} <= tok_cols
        row = conn.execute("SELECT granted_scope, token_family_id FROM oauth_tokens WHERE token='legacy_access_token_value_123456'").fetchone()
        assert row is not None
        assert row["granted_scope"] == "readonly"
        assert row["token_family_id"]

        tables = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        assert {"oauth_pending_requests", "oauth_device_codes"} <= tables

        indexes = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()}
        assert "idx_oauth_tokens_family" in indexes
    finally:
        conn.close()


def test_v40_fresh_schema_has_client_staging_columns_and_index(tmp_path: Path) -> None:
    """Verify v40 schema DDL creates client-staging columns, checks, and index."""
    import pytest

    conn = _connect(tmp_path / "claire_ae4.db")
    dbm.init_db(conn)
    try:
        acct_cols = {r["name"] for r in conn.execute("PRAGMA table_info(accounts)").fetchall()}
        assert "extraction_mode" in acct_cols
        admin_row = conn.execute("SELECT extraction_mode FROM accounts WHERE id=1").fetchone()
        assert admin_row is not None
        assert admin_row["extraction_mode"] == "provider"

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE accounts SET extraction_mode='invalid' WHERE id=1")

        doc_cols = {r["name"] for r in conn.execute("PRAGMA table_info(documents)").fetchall()}
        assert {"content_hash", "extraction_status", "claim_token", "lease_expires_at", "claimed_by_client_id", "raw_chars"} <= doc_cols

        conn.execute("INSERT INTO documents (id, kb_id, title, raw_text, fetched_at) VALUES ('doc_default_1', 0, 'T', 'hello', 1.0)")
        doc_row = conn.execute("SELECT extraction_status, claim_token, lease_expires_at, claimed_by_client_id, raw_chars FROM documents WHERE id='doc_default_1'").fetchone()
        assert doc_row is not None
        assert doc_row["extraction_status"] == "extracted"
        assert doc_row["claim_token"] is None

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO documents (id, kb_id, title, raw_text, extraction_status, fetched_at) VALUES ('doc_bad_1', 0, 'T', 'hello', 'bogus', 1.0)")

        indexes = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()}
        assert "idx_documents_kb_extraction_status" in indexes
    finally:
        conn.close()


def test_v39_to_v40_migration_adds_client_staging_columns_idempotently(tmp_path: Path) -> None:
    """Verify v39-to-v40 upgrade adds staging columns without modifying existing rows."""
    db_file = tmp_path / "v39_staging.db"
    conn = _connect(db_file)
    dbm.init_db(conn)
    conn.execute("ALTER TABLE accounts DROP COLUMN extraction_mode")
    conn.execute("DROP INDEX IF EXISTS idx_documents_kb_extraction_status")
    for col in ("extraction_status", "claim_token", "lease_expires_at", "claimed_by_client_id", "raw_chars"):
        conn.execute(f"ALTER TABLE documents DROP COLUMN {col}")
    conn.execute("INSERT INTO documents (id, kb_id, title, raw_text, content_hash, fetched_at) VALUES ('legacy_doc_1', 0, 'Legacy', 'body text', 'hash123', 10.0)")
    conn.execute("UPDATE meta SET value='39' WHERE key='schema_version'")
    conn.commit()
    conn.close()

    conn = _connect(db_file)
    dbm.init_db(conn)
    dbm.init_db(conn)
    try:
        assert int(conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0]) == 40
        acct_row = conn.execute("SELECT extraction_mode FROM accounts WHERE id=1").fetchone()
        assert acct_row is not None
        assert acct_row["extraction_mode"] == "provider"

        doc_cols = {r["name"] for r in conn.execute("PRAGMA table_info(documents)").fetchall()}
        assert {"content_hash", "extraction_status", "claim_token", "lease_expires_at", "claimed_by_client_id", "raw_chars"} <= doc_cols
        doc_row = conn.execute("SELECT extraction_status FROM documents WHERE id='legacy_doc_1'").fetchone()
        assert doc_row is not None
        assert doc_row["extraction_status"] == "extracted"

        indexes = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()}
        assert "idx_documents_kb_extraction_status" in indexes
    finally:
        conn.close()


def test_v40_staged_document_lifecycle_io(tmp_path: Path) -> None:
    """Verify list, claim, renew, reclaim, and chunked get SQLite operations across lease boundaries."""
    from claire.ontology.base import Document

    conn = _connect(tmp_path / "staging_io.db")
    dbm.init_db(conn)
    try:
        conn.execute("INSERT INTO accounts (public_id, username, role, status, display_name, profile_public, created_at, activated_at, updated_at, extraction_mode) VALUES (9001, 'alice_member', 'member', 'active', 'Alice', 0, 1.0, 1.0, 1.0, 'client')")
        alice_id = int(conn.execute("SELECT id FROM accounts WHERE username='alice_member'").fetchone()["id"])
        conn.execute("INSERT INTO knowledge_bases (id, seq, label, is_personal, is_public, created_at, updated_at, account_id) VALUES (10, 10, 'Alice Personal', 1, 0, 1.0, 1.0, ?)", (alice_id,))
        conn.execute("INSERT INTO accounts (public_id, username, role, status, display_name, profile_public, created_at, activated_at, updated_at, extraction_mode) VALUES (9002, 'bob_member', 'member', 'active', 'Bob', 0, 1.0, 1.0, 1.0, 'client')")
        bob_id = int(conn.execute("SELECT id FROM accounts WHERE username='bob_member'").fetchone()["id"])
        conn.execute("INSERT INTO knowledge_bases (id, seq, label, is_personal, is_public, created_at, updated_at, account_id) VALUES (20, 20, 'Bob Personal', 1, 0, 1.0, 1.0, ?)", (bob_id,))

        raw_payload = "ABCDEFGHIJ0123456789"
        dbm.insert_document(conn, Document(id="doc_a1", url="https://example.com/a1", canonical_url="https://example.com/a1", title="A1", raw_text=raw_payload, fetched_at=100.0, content_hash="h_a1"), kb_id=10, extraction_status="pending", raw_chars=len(raw_payload))
        dbm.insert_document(conn, Document(id="doc_b1", url="https://example.com/b1", canonical_url="https://example.com/b1", title="B1", raw_text="bob", fetched_at=101.0, content_hash="h_b1"), kb_id=20, extraction_status="pending", raw_chars=3)

        assert [d["id"] for d in dbm.list_staged_documents(conn, account_id=alice_id)] == ["doc_a1"]
        assert dbm.claim_staged_document(conn, "doc_a1", account_id=bob_id, client_id="bob-cli", now=200.0) is None

        claim1 = dbm.claim_staged_document(conn, "doc_a1", account_id=alice_id, client_id="cli-1", lease_seconds=60.0, now=200.0)
        assert claim1 is not None
        assert claim1["claimed_by_client_id"] == "cli-1"
        assert claim1["lease_expires_at"] == 260.0
        token1 = claim1["claim_token"]

        assert dbm.claim_staged_document(conn, "doc_a1", account_id=alice_id, client_id="cli-2", lease_seconds=60.0, now=210.0) is None
        assert dbm.renew_staged_document_claim(conn, "doc_a1", "wrong-token", account_id=alice_id, lease_seconds=120.0, now=220.0) is None

        renewed = dbm.renew_staged_document_claim(conn, "doc_a1", token1, account_id=alice_id, lease_seconds=120.0, now=220.0)
        assert renewed is not None
        assert renewed["lease_expires_at"] == 340.0

        assert dbm.get_staged_document(conn, "doc_a1", account_id=alice_id, claim_token="wrong", now=230.0) is None
        chunk1 = dbm.get_staged_document(conn, "doc_a1", account_id=alice_id, claim_token=token1, offset=0, max_chars=10, now=230.0)
        chunk2 = dbm.get_staged_document(conn, "doc_a1", account_id=alice_id, claim_token=token1, offset=10, max_chars=15, now=230.0)
        assert chunk1 is not None and chunk1["raw_text"] == "ABCDEFGHIJ" and chunk1["has_more"] is True and chunk1["next_offset"] == 10
        assert chunk2 is not None and chunk2["raw_text"] == "0123456789" and chunk2["has_more"] is False and chunk2["next_offset"] is None

        claim2 = dbm.claim_staged_document(conn, "doc_a1", account_id=alice_id, client_id="cli-2", lease_seconds=30.0, now=341.0)
        assert claim2 is not None
        assert claim2["claim_token"] != token1
        assert claim2["claimed_by_client_id"] == "cli-2"
    finally:
        conn.close()
