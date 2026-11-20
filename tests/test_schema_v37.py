"""Schema v37: ``accounts.personal_source_public`` (docs/implementation/SCHEMA_VERSIONING.md "v37")."""

from __future__ import annotations

import time
from pathlib import Path

from claire.store import accounts as accounts_store
from claire.store import db as dbm


def _db(tmp_path: Path):
    conn = dbm.connect(tmp_path / "claire.db")
    dbm.init_db(conn)
    return conn


def test_a_v36_database_gets_the_column_with_public_sources(tmp_path: Path):
    """A database of v36 (accounts without the column) becomes v37 at the next start. Each account gets 1: each reader sees the sources of its personal knowledge base, as before."""
    conn = _db(tmp_path)
    now = time.time()
    conn.execute("INSERT INTO accounts (id, public_id, username, role, status, display_name, profile_public, created_at, updated_at) VALUES (2, ?, 'alice', 'member', 'active', 'Alice', 1, ?, ?)", ((1 << 57) + 2, now, now))
    conn.execute("ALTER TABLE accounts DROP COLUMN personal_source_public")
    conn.execute("UPDATE meta SET value='36' WHERE key='schema_version'")
    conn.commit()
    conn.close()

    conn = _db(tmp_path)
    try:
        assert int(conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0]) == dbm.SCHEMA_VERSION
        assert [tuple(r) for r in conn.execute("SELECT id, personal_source_public FROM accounts ORDER BY id")] == [(1, 1), (2, 1)]
        store = accounts_store.AccountStore(conn)
        assert store.get(2).personal_source_public is True
        assert store.update_personal_source_public(2, False).personal_source_public is False
        try:
            store.update_personal_source_public(2, 1)  # type: ignore[arg-type]
        except ValueError as exc:
            assert str(exc) == "invalid_personal_source_public"
        else:
            raise AssertionError("a value that is not a boolean must be refused")
    finally:
        conn.close()
