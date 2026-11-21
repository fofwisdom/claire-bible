"""Schema v41 migration tests: case-preserving usernames, CI uniqueness, and 7-day rename holds."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from claire.store import db as dbm


def test_v41_fresh_schema_objects(tmp_path: Path) -> None:
    """Verify a fresh database creates v41 triggers, indexes, and tombstone columns."""
    conn = dbm.connect(tmp_path / "claire.db")
    try:
        dbm.init_db(conn)
        assert int(conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0]) == dbm.SCHEMA_VERSION == 41

        triggers = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='trigger'").fetchall()}
        assert "accounts_username_not_null" in triggers
        assert "accounts_username_immutable" not in triggers

        indexes = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()}
        assert {"idx_accounts_username_ci", "idx_username_tombstones_lower"} <= indexes

        tombstone_cols = {r["name"] for r in conn.execute("PRAGMA table_info(username_tombstones)").fetchall()}
        assert "reusable_after" in tombstone_cols
    finally:
        conn.close()


def test_v40_to_v41_migration_and_idempotency(tmp_path: Path) -> None:
    """Verify upgrading from v40 drops the immutability trigger, preserves data, and stays idempotent."""
    db_file = tmp_path / "upgrade.db"
    conn = dbm.connect(db_file)
    try:
        dbm.init_db(conn)
        conn.execute("DROP TRIGGER IF EXISTS accounts_username_not_null")
        conn.execute("DROP INDEX IF EXISTS idx_accounts_username_ci")
        conn.execute("DROP INDEX IF EXISTS idx_username_tombstones_lower")
        conn.execute("DROP TABLE username_tombstones")
        conn.executescript(
            """
            CREATE TRIGGER accounts_username_immutable
            BEFORE UPDATE OF username ON accounts
            WHEN OLD.username IS NOT NULL AND NEW.username IS NOT OLD.username
            BEGIN SELECT RAISE(ABORT, 'username is immutable'); END;

            CREATE TABLE username_tombstones (
                username TEXT PRIMARY KEY,
                account_id INTEGER NOT NULL,
                was_public INTEGER NOT NULL DEFAULT 0,
                retired_at REAL NOT NULL,
                released_at REAL,
                released_by INTEGER
            );
            """
        )
        conn.execute("UPDATE accounts SET username='Alice', status='active' WHERE id=1")
        conn.execute("INSERT INTO username_tombstones (username, account_id, was_public, retired_at) VALUES ('zed', 99, 1, 100.0)")
        conn.execute("INSERT INTO meta (key, value) VALUES ('schema_version', '40') ON CONFLICT(key) DO UPDATE SET value='40'")
        conn.commit()
    finally:
        conn.close()

    upgraded = dbm.connect(db_file)
    try:
        dbm.init_db(upgraded)
        dbm.init_db(upgraded)
        assert int(upgraded.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0]) == 41

        triggers = {r["name"] for r in upgraded.execute("SELECT name FROM sqlite_master WHERE type='trigger'").fetchall()}
        assert "accounts_username_not_null" in triggers
        assert "accounts_username_immutable" not in triggers

        row = upgraded.execute("SELECT username FROM accounts WHERE id=1").fetchone()
        assert row is not None and row[0] == "Alice"
        tomb = upgraded.execute("SELECT was_public, reusable_after FROM username_tombstones WHERE username='zed'").fetchone()
        assert tomb is not None and tomb[0] == 1 and tomb[1] is None

        upgraded.execute("UPDATE accounts SET username='AliceNew' WHERE id=1")
        upgraded.execute("INSERT INTO accounts (id, public_id, username, role, status, created_at, updated_at) VALUES (2, 2002, 'Bob', 'member', 'active', 1.0, 1.0)")
        with pytest.raises(sqlite3.IntegrityError):
            upgraded.execute("UPDATE accounts SET username='alicenew' WHERE id=2")
        with pytest.raises(sqlite3.IntegrityError, match="username cannot be cleared"):
            upgraded.execute("UPDATE accounts SET username=NULL WHERE id=1")
    finally:
        upgraded.close()
