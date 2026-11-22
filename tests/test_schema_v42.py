"""Schema v42 migration tests: accounts.summary column."""

from __future__ import annotations

from pathlib import Path

from claire.store import db as dbm


def test_v42_fresh_schema_has_account_summary_column(tmp_path: Path) -> None:
    """Verify a fresh database creates accounts.summary with an empty string default."""
    conn = dbm.connect(tmp_path / "claire.db")
    try:
        dbm.init_db(conn)
        assert int(conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0]) == dbm.SCHEMA_VERSION >= 42
        cols = {r["name"]: r for r in conn.execute("PRAGMA table_info(accounts)").fetchall()}
        assert "summary" in cols
        assert cols["summary"]["type"] == "TEXT"
        row = conn.execute("SELECT summary FROM accounts WHERE id=1").fetchone()
        assert row is not None and row[0] == ""
    finally:
        conn.close()


def test_v41_to_v42_migration_and_idempotency(tmp_path: Path) -> None:
    """Verify upgrading from v41 adds accounts.summary safely and stays idempotent."""
    db_file = tmp_path / "upgrade_v42.db"
    conn = dbm.connect(db_file)
    try:
        dbm.init_db(conn)
        conn.execute("ALTER TABLE accounts DROP COLUMN summary")
        conn.execute("INSERT INTO meta (key, value) VALUES ('schema_version', '41') ON CONFLICT(key) DO UPDATE SET value='41'")
        conn.commit()
    finally:
        conn.close()

    upgraded = dbm.connect(db_file)
    try:
        dbm.init_db(upgraded)
        dbm.init_db(upgraded)
        assert int(upgraded.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0]) >= 42
        cols = {r["name"] for r in upgraded.execute("PRAGMA table_info(accounts)").fetchall()}
        assert "summary" in cols
        row = upgraded.execute("SELECT summary FROM accounts WHERE id=1").fetchone()
        assert row is not None and row[0] == ""
    finally:
        upgraded.close()
