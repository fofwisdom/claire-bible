"""Schema v20: removal of the theme-scoped ``syndication_*`` tables and the rebuild of ``syndication_dispatches`` (FEDERATION_REMOTE_READING_DESIGN.md Section 4.3, P5)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from claire.store import db as dbm

DROPPED_TABLES = ("syndication_accounts", "syndication_targets", "syndication_items")
DROPPED_INDEXES = ("idx_synd_items_theme_collected", "idx_synd_items_theme_canon", "idx_synd_items_theme_doc")
V20_DISPATCH_COLUMNS = [
    "id", "theme_id", "target_theme_id", "document_id", "target_url", "mode", "status", "remote_ref", "payload", "error",
    "dispatched_by_user_id", "dispatched_by_account_id", "applied_focus", "dispatched_at", "created_at", "updated_at",
]

# The four tables as schema v19 declared them.
V19_SYNDICATION_DDL = """
CREATE TABLE syndication_accounts (
    id TEXT PRIMARY KEY,
    theme_id INTEGER NOT NULL DEFAULT 0 REFERENCES themes(id) ON DELETE CASCADE,
    protocol TEXT NOT NULL,
    server_url TEXT NOT NULL,
    software TEXT,
    server_version TEXT,
    missing_features_json TEXT DEFAULT '[]',
    handle TEXT NOT NULL,
    remote_account_id TEXT,
    auth_kind TEXT NOT NULL,
    scopes TEXT NOT NULL,
    token_enc BLOB,
    token_nonce BLOB,
    token_key_version INTEGER DEFAULT 1,
    client_registration_json TEXT,
    publish_enabled INTEGER DEFAULT 0,
    max_chars INTEGER,
    streaming_url TEXT,
    health_status TEXT DEFAULT 'ok',
    consecutive_failures INTEGER DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    UNIQUE(theme_id, protocol, server_url, remote_account_id)
);
CREATE TABLE syndication_targets (
    id TEXT PRIMARY KEY,
    theme_id INTEGER NOT NULL DEFAULT 0 REFERENCES themes(id) ON DELETE CASCADE,
    protocol TEXT NOT NULL,
    account_id TEXT REFERENCES syndication_accounts(id) ON DELETE CASCADE,
    source_kind TEXT NOT NULL,
    source_ref TEXT NOT NULL,
    title TEXT NOT NULL,
    folder TEXT DEFAULT '',
    poll_interval_sec INTEGER DEFAULT 900,
    auto_ingest_threshold INTEGER,
    default_focus TEXT DEFAULT '',
    etag TEXT,
    last_modified TEXT,
    cursor TEXT,
    health_status TEXT DEFAULT 'ok',
    consecutive_failures INTEGER DEFAULT 0,
    last_polled_at REAL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    UNIQUE(theme_id, protocol, account_id, source_kind, source_ref)
);
CREATE TABLE syndication_items (
    id TEXT PRIMARY KEY,
    theme_id INTEGER NOT NULL DEFAULT 0 REFERENCES themes(id) ON DELETE CASCADE,
    target_id TEXT REFERENCES syndication_targets(id) ON DELETE SET NULL,
    via_account_id TEXT REFERENCES syndication_accounts(id) ON DELETE SET NULL,
    protocol TEXT NOT NULL,
    external_id TEXT NOT NULL,
    remote_status_id TEXT,
    at_cid TEXT,
    canonical_url TEXT,
    title TEXT,
    author TEXT,
    raw_content TEXT,
    source_tags_json TEXT DEFAULT '[]',
    content_hash TEXT,
    published_at TEXT,
    collected_at REAL NOT NULL,
    unread INTEGER DEFAULT 1,
    starred INTEGER DEFAULT 0,
    affinity_score REAL DEFAULT 0.0,
    ingest_state TEXT DEFAULT 'unlinked',
    linked_doc_id TEXT,
    outbound_state TEXT DEFAULT 'none',
    UNIQUE(theme_id, protocol, external_id)
);
CREATE INDEX idx_synd_items_theme_collected ON syndication_items(theme_id, collected_at DESC);
CREATE INDEX idx_synd_items_theme_canon ON syndication_items(theme_id, canonical_url);
CREATE INDEX idx_synd_items_theme_doc ON syndication_items(theme_id, linked_doc_id);
DROP TABLE syndication_dispatches;
CREATE TABLE syndication_dispatches (
    id TEXT PRIMARY KEY,
    theme_id INTEGER NOT NULL DEFAULT 0 REFERENCES themes(id) ON DELETE CASCADE,
    target_theme_id INTEGER DEFAULT 0,
    item_id TEXT REFERENCES syndication_items(id) ON DELETE SET NULL,
    account_id TEXT REFERENCES syndication_accounts(id) ON DELETE CASCADE,
    document_id TEXT REFERENCES documents(id) ON DELETE CASCADE,
    target_url TEXT,
    mode TEXT NOT NULL DEFAULT 'post',
    status TEXT NOT NULL DEFAULT 'pending',
    remote_ref TEXT,
    payload TEXT,
    error TEXT,
    dispatched_by_user_id INTEGER,
    dispatched_by_account_id INTEGER REFERENCES accounts(id),
    applied_focus TEXT,
    dispatched_at REAL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX idx_synd_dispatches_theme ON syndication_dispatches(theme_id, status);
"""

SEED_ROWS = {
    "syndication_accounts": "INSERT INTO syndication_accounts (id, theme_id, protocol, server_url, handle, auth_kind, scopes, created_at, updated_at) VALUES ('acc-1', 0, 'activitypub', 'https://remote.example', '@a@remote.example', 'oauth', 'read', 1.0, 1.0)",
    "syndication_targets": "INSERT INTO syndication_targets (id, theme_id, protocol, source_kind, source_ref, title, created_at, updated_at) VALUES ('tgt-1', 0, 'rss', 'feed', 'https://remote.example/feed', 'Feed', 1.0, 1.0)",
    "syndication_items": "INSERT INTO syndication_items (id, theme_id, protocol, external_id, collected_at) VALUES ('item-1', 0, 'rss', 'ext-1', 1.0)",
}


def _make_v19_database(path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Initialize with the current code without the v20 step, then restore the v19 shape of the four ``syndication_*`` tables."""
    with monkeypatch.context() as m:
        m.setattr(dbm, "SCHEMA_VERSION", 19)
        m.setattr(dbm, "_migrate_v20_syndication_cleanup", lambda conn: None)
        conn = dbm.connect(path)
        try:
            dbm.init_db(conn)
            conn.executescript(V19_SYNDICATION_DDL)
            assert dbm.stored_schema_version(conn) == 19
        finally:
            conn.close()


def _dispatch_rows(conn: sqlite3.Connection) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM syndication_dispatches ORDER BY id")]


def _seed_dispatches(conn: sqlite3.Connection) -> None:
    now = 1_800_000_000.0
    conn.execute("INSERT INTO themes (id, seq, label, is_default, created_at, updated_at, account_id) VALUES (7, 7, 'seven', 0, ?, ?, 1)", (now, now))
    conn.execute("INSERT INTO documents (id, theme_id, title) VALUES ('doc-1', 0, 'Doc')")
    conn.execute(
        "INSERT INTO syndication_dispatches (id, theme_id, target_theme_id, item_id, account_id, document_id, target_url, mode, status, remote_ref, payload, error, dispatched_by_user_id, dispatched_by_account_id, applied_focus, dispatched_at, created_at, updated_at) "
        "VALUES ('disp_a', 0, 7, NULL, NULL, 'doc-1', NULL, 'cross_theme', 'done', 'doc-x', 'https://example.com/a', NULL, NULL, 1, 'focus a', ?, ?, ?)",
        (now + 1, now, now + 1),
    )
    conn.execute(
        "INSERT INTO syndication_dispatches (id, theme_id, target_theme_id, document_id, mode, status, error, dispatched_by_account_id, created_at, updated_at) "
        "VALUES ('disp_b', 7, 0, NULL, 'cross_theme', 'error', 'boom', 1, ?, ?)",
        (now + 2, now + 2),
    )
    conn.commit()
    # A theme deletion without purge leaves its dispatch rows (store/theme.py). The rebuild must keep such a row too.
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("DELETE FROM themes WHERE id = 7")
    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON")


def test_v19_database_with_empty_tables_upgrades_and_keeps_dispatch_rows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "claire.db"
    _make_v19_database(path, monkeypatch)
    conn = dbm.connect(path)
    try:
        _seed_dispatches(conn)
        before = _dispatch_rows(conn)
        assert {"item_id", "account_id"} <= set(before[0])

        dbm.init_db(conn)

        assert dbm.SCHEMA_VERSION == 21 and dbm.require_current_schema(conn) == 21
        names = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master")}
        assert not set(DROPPED_TABLES) & names
        assert not set(DROPPED_INDEXES) & names
        assert "syndication_dispatches_v20" not in names
        assert "idx_synd_dispatches_theme" in names
        assert [r["name"] for r in conn.execute("PRAGMA table_info(syndication_dispatches)")] == V20_DISPATCH_COLUMNS
        expected = [{k: v for k, v in row.items() if k not in ("item_id", "account_id")} for row in before]
        assert _dispatch_rows(conn) == expected
        # The rebuild turns the foreign key check on again.
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1

        # The dispatch store functions work on the rebuilt table.
        did = dbm.create_syndication_dispatch(conn, theme_id=0, target_theme_id=0, document_id="doc-1", status="done", dispatched_by_account_id=1)
        assert dbm.get_syndication_dispatch(conn, did)["status"] == "done"
        assert [r["id"] for r in dbm.list_syndication_dispatches(conn, theme_id=7)] == ["disp_b"]
    finally:
        conn.close()


def test_v20_migration_runs_twice_without_change(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "claire.db"
    _make_v19_database(path, monkeypatch)
    conn = dbm.connect(path)
    try:
        _seed_dispatches(conn)
        dbm.init_db(conn)
        first = list(conn.iterdump())
        dbm.init_db(conn)
        dbm._migrate_v20_syndication_cleanup(conn)
        conn.commit()
        assert list(conn.iterdump()) == first
    finally:
        conn.close()


def test_fresh_database_has_no_dropped_tables() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    try:
        dbm.init_db(conn)
        names = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master")}
        assert not (set(DROPPED_TABLES) | set(DROPPED_INDEXES)) & names
        assert [r["name"] for r in conn.execute("PRAGMA table_info(syndication_dispatches)")] == V20_DISPATCH_COLUMNS
    finally:
        conn.close()


@pytest.mark.parametrize("table", DROPPED_TABLES)
def test_v19_database_with_a_row_in_a_dropped_table_keeps_it_under_a_retired_name(table: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # The operator does not manage the database: the start never stops, and no row is deleted.
    path = tmp_path / "claire.db"
    _make_v19_database(path, monkeypatch)
    conn = dbm.connect(path)
    try:
        _seed_dispatches(conn)
        conn.execute(SEED_ROWS[table])
        conn.commit()
        before = conn.execute(f"SELECT * FROM {table}").fetchall()

        dbm.init_db(conn)
        dbm.init_db(conn)

        assert dbm.stored_schema_version(conn) == 21
        retired = f"{table}_retired_v20"
        assert retired in dbm.V20_RETIRED_SYNDICATION_TABLES
        assert [tuple(r) for r in conn.execute(f"SELECT * FROM {retired}").fetchall()] == [tuple(r) for r in before]
        names = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert not set(DROPPED_TABLES) & names
        assert names & set(dbm.V20_RETIRED_SYNDICATION_TABLES) == {retired}
        assert [r["name"] for r in conn.execute("PRAGMA table_info(syndication_dispatches)")] == V20_DISPATCH_COLUMNS
    finally:
        conn.close()
