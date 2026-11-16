"""claire.db version 27: names only (SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md Section 5.1, 12.1).

A version 26 database has the table ``themes``, the column ``is_default``, the columns ``theme_id`` and ``target_theme_id``, and indexes with ``theme`` in the name. The step renames them with all rows, and a second start changes nothing.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from claire.store import client_db
from claire.store import db as dbm


def _make_v26_database(path: Path) -> None:
    """Make a current database with rows, then give it the names of version 26."""
    conn = dbm.connect(path)
    dbm.init_db(conn)
    now = 1.0
    for kb_id, personal in ((1, 0), (2, 0), (3, 0)):
        conn.execute(
            "INSERT INTO knowledge_bases (id, seq, label, is_personal, created_at, updated_at, account_id) VALUES (?, ?, ?, ?, ?, ?, 1)",
            (kb_id, kb_id, f"kb {kb_id}", personal, now, now),
        )
    for i, kb_id in enumerate((0, 1, 1, 2, 3)):
        conn.execute("INSERT INTO documents (id, kb_id, title, fetched_at) VALUES (?, ?, ?, 1)", (f"doc-{i}", kb_id, f"title {i}"))
    conn.execute(
        "INSERT INTO syndication_dispatches (kb_id, target_kb_id, document_id, mode, status, created_at, updated_at) VALUES (1, 2, 'doc-1', 'cross_theme', 'done', 1, 1)"
    )
    conn.commit()
    # Back to the names of version 26.
    new_indexes = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%' AND (name LIKE '%\\_kb%' ESCAPE '\\' OR name LIKE 'idx\\_knowledge\\_bases%' ESCAPE '\\')")]
    for name in new_indexes:
        conn.execute(f'DROP INDEX "{name}"')
    conn.execute("ALTER TABLE knowledge_bases RENAME TO themes")
    conn.execute("ALTER TABLE themes RENAME COLUMN is_personal TO is_default")
    for table in [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]:
        cols = {r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')}
        if "kb_id" in cols:
            conn.execute(f'ALTER TABLE "{table}" RENAME COLUMN kb_id TO theme_id')
        if "target_kb_id" in cols:
            conn.execute(f'ALTER TABLE "{table}" RENAME COLUMN target_kb_id TO target_theme_id')
    conn.execute("CREATE INDEX idx_documents_theme_hash ON documents(theme_id, content_hash)")
    conn.execute("CREATE UNIQUE INDEX idx_themes_number ON themes(number)")
    conn.execute("CREATE UNIQUE INDEX idx_themes_account_default ON themes(account_id) WHERE is_default = 1")
    conn.execute("UPDATE meta SET value='26' WHERE key='schema_version'")
    conn.commit()
    conn.close()


def _snapshot(conn: sqlite3.Connection) -> tuple[list[tuple], dict[str, int]]:
    objects = sorted(tuple(r) for r in conn.execute("SELECT type, name, tbl_name, sql FROM sqlite_master"))
    counts = {t: conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in ("knowledge_bases", "documents", "syndication_dispatches")}
    return objects, counts


def test_v26_database_starts_as_v27_with_the_same_rows(tmp_path: Path) -> None:
    db_file = tmp_path / "claire.db"
    _make_v26_database(db_file)
    jobs = tmp_path / "active_jobs"
    jobs.mkdir()
    (jobs / "job-1.json").write_text(json.dumps({"job_id": "job-1", "theme_id": 2, "nested": [{"theme_id": 3}]}), encoding="utf-8")
    (tmp_path / "active_ingest.json").write_text(json.dumps({"theme_id": 1}), encoding="utf-8")

    conn = dbm.connect(db_file)
    try:
        dbm.init_db(conn)
        assert dbm.stored_schema_version(conn) == 27 == dbm.SCHEMA_VERSION
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "knowledge_bases" in tables and "themes" not in tables
        assert "is_personal" in dbm._column_names(conn, "knowledge_bases")
        assert "is_default" not in dbm._column_names(conn, "knowledge_bases")
        assert [r[0] for r in conn.execute("SELECT id FROM knowledge_bases ORDER BY id")] == [0, 1, 2, 3]
        assert conn.execute("SELECT is_personal FROM knowledge_bases WHERE id=0").fetchone()[0] == 1
        assert sorted(tuple(r) for r in conn.execute("SELECT id, kb_id FROM documents")) == [("doc-0", 0), ("doc-1", 1), ("doc-2", 1), ("doc-3", 2), ("doc-4", 3)]
        assert tuple(conn.execute("SELECT kb_id, target_kb_id FROM syndication_dispatches").fetchone()) == (1, 2)
        for table in [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]:
            assert not {"theme_id", "target_theme_id"} & dbm._column_names(conn, table), table
        index_names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
        assert not [n for n in index_names if "theme" in n and not n.startswith("sqlite_")]
        assert {"idx_knowledge_bases_number", "idx_knowledge_bases_account_personal", "idx_documents_kb_hash"} <= index_names
        first = _snapshot(conn)
    finally:
        conn.close()

    assert json.loads((jobs / "job-1.json").read_text(encoding="utf-8")) == {"job_id": "job-1", "kb_id": 2, "nested": [{"kb_id": 3}]}
    assert json.loads((tmp_path / "active_ingest.json").read_text(encoding="utf-8")) == {"kb_id": 1}

    # A second start changes nothing.
    conn = dbm.connect(db_file)
    try:
        dbm.init_db(conn)
        assert _snapshot(conn) == first
    finally:
        conn.close()


def test_client_db_v7_renames_linked_theme_id(tmp_path: Path) -> None:
    path = tmp_path / "client.db"
    conn = client_db.connect_client_db(path)
    conn.execute("ALTER TABLE feed_items RENAME COLUMN linked_kb_id TO linked_theme_id")
    conn.execute("UPDATE client_meta SET value='6' WHERE key='schema_version'")
    conn.commit()
    conn.close()

    conn = client_db.connect_client_db(path)
    try:
        assert client_db.stored_client_version(conn) == 7
        cols = {r[1] for r in conn.execute("PRAGMA table_info(feed_items)")}
        assert "linked_kb_id" in cols and "linked_theme_id" not in cols
    finally:
        conn.close()


def test_account_backup_format_1_snapshot_is_upgraded(tmp_path: Path) -> None:
    from claire import account_backup

    snap = tmp_path / "5.db"
    conn = sqlite3.connect(snap)
    conn.execute("CREATE TABLE themes (id INTEGER PRIMARY KEY, label TEXT, is_default INTEGER)")
    conn.execute("CREATE TABLE documents (id TEXT PRIMARY KEY, theme_id INTEGER)")
    conn.execute("INSERT INTO themes VALUES (5, 'old', 0)")
    conn.execute("INSERT INTO documents VALUES ('d', 5)")
    conn.commit()
    conn.close()
    manifest = {"format_version": 1, "themes": [{"theme_id": 5, "theme": "x", "label": "old"}]}

    account_backup._upgrade_v1_snapshots(manifest, tmp_path)

    assert manifest["themes"][0]["kb_id"] == 5 and "theme_id" not in manifest["themes"][0]
    conn = sqlite3.connect(snap)
    try:
        assert conn.execute("SELECT id, label, is_personal FROM knowledge_bases").fetchone() == (5, "old", 0)
        assert conn.execute("SELECT kb_id FROM documents").fetchone() == (5,)
    finally:
        conn.close()
