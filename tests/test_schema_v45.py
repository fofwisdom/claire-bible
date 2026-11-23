"""Schema v45 (SCHEMA_VERSIONING.md "v45"): the account setting ``accounts.extraction_mode`` of v40 is dropped, because each ingest request selects its extraction mode (AED4-rev)."""

from __future__ import annotations

from pathlib import Path

from claire.store import accounts as accounts_store
from claire.store import db as dbm


def _account_columns(conn) -> set[str]:
    return {r["name"] for r in conn.execute("PRAGMA table_info(accounts)").fetchall()}


def test_v44_to_v45_drops_the_account_extraction_mode_and_keeps_the_rows(tmp_path: Path) -> None:
    db_file = tmp_path / "v44.db"
    conn = dbm.connect(db_file)
    dbm.init_db(conn)
    # A v44 database: the v40 column with a stored value.
    conn.execute("ALTER TABLE accounts ADD COLUMN extraction_mode TEXT NOT NULL DEFAULT 'provider' CHECK (extraction_mode IN ('provider', 'client'))")
    conn.execute("INSERT INTO accounts (public_id, username, role, status, display_name, profile_public, created_at, activated_at, updated_at, extraction_mode) VALUES (9101, 'carol', 'member', 'active', 'Carol', 0, 1.0, 1.0, 1.0, 'client')")
    conn.execute("UPDATE meta SET value='44' WHERE key='schema_version'")
    conn.commit()
    conn.close()

    conn = dbm.connect(db_file)
    try:
        dbm.init_db(conn)
        dbm.init_db(conn)
        assert int(conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0]) == dbm.SCHEMA_VERSION
        assert "extraction_mode" not in _account_columns(conn)
        carol = accounts_store.AccountStore(conn).find_by_username("carol")
        assert carol is not None
        assert (carol.display_name, carol.status) == ("Carol", "active")
    finally:
        conn.close()


def test_fresh_v45_schema_has_no_account_extraction_mode(tmp_path: Path) -> None:
    conn = dbm.connect(tmp_path / "fresh.db")
    try:
        dbm.init_db(conn)
        assert "extraction_mode" not in _account_columns(conn)
    finally:
        conn.close()
