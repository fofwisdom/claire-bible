"""Schema v25: the table ``instance_settings`` becomes ``server_settings`` with all its rows (terminology "server", docs/implementation/SCHEMA_VERSIONING.md "v25")."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from claire.store import db as dbm
from claire.store.accounts import AccountStore

OLD = "instance_settings"


def _tables(conn) -> set[str]:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _settings(conn, table: str) -> dict[str, str]:
    return {r[0]: r[1] for r in conn.execute(f"SELECT key, value FROM {table}")}


def _copy_as_server_settings(conn) -> None:
    """Make ``server_settings`` with the DDL of the v15 step and the rows of the old table."""
    conn.execute("CREATE TABLE server_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at REAL NOT NULL, updated_by INTEGER)")
    conn.execute(f"INSERT INTO server_settings SELECT key, value, updated_at, updated_by FROM {OLD}")


def _make_v24_database(path: Path, monkeypatch: pytest.MonkeyPatch, *, mode: str = "network") -> dict[str, str]:
    """Initialize with the current code without the v25 step, then give the settings table its v24 name. Return the v24 settings."""
    with monkeypatch.context() as m:
        m.setattr(dbm, "SCHEMA_VERSION", 24)
        m.setattr(dbm, "_migrate_v25_server_settings", lambda conn: None)
        conn = dbm.connect(path)
        try:
            dbm.init_db(conn)
            conn.execute("INSERT INTO server_settings (key, value, updated_at, updated_by) VALUES ('mode', ?, 1.0, 1)", (mode,))
            conn.execute(f"ALTER TABLE server_settings RENAME TO {OLD}")
            conn.commit()
            assert dbm.stored_schema_version(conn) == 24
            return _settings(conn, OLD)
        finally:
            conn.close()


def test_version_24_file_keeps_all_settings_under_the_new_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "claire.db"
    before = _make_v24_database(path, monkeypatch)
    conn = dbm.connect(path)
    try:
        admin_public_id = conn.execute("SELECT public_id FROM accounts WHERE id=?", (dbm.ADMIN_ACCOUNT_ID,)).fetchone()[0]
    finally:
        conn.close()
    assert {"id_salt", "mode"} <= set(before)
    for _ in range(2):
        conn = dbm.connect(path)
        try:
            dbm.init_db(conn)
            assert dbm.stored_schema_version(conn) == dbm.SCHEMA_VERSION >= 25
            tables = _tables(conn)
            assert "server_settings" in tables and OLD not in tables and f"{OLD}_retired_v25" not in tables
            # The same id_salt: the public IDs do not change.
            assert _settings(conn, "server_settings") == before
            assert conn.execute("SELECT public_id FROM accounts WHERE id=?", (dbm.ADMIN_ACCOUNT_ID,)).fetchone()[0] == admin_public_id
            assert AccountStore(conn).server_mode() == "network"
        finally:
            conn.close()


def test_two_tables_with_the_same_rows_leave_only_server_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "claire.db"
    before = _make_v24_database(path, monkeypatch)
    conn = dbm.connect(path)
    try:
        _copy_as_server_settings(conn)
        conn.commit()
        dbm.init_db(conn)
        assert OLD not in _tables(conn) and f"{OLD}_retired_v25" not in _tables(conn)
        assert _settings(conn, "server_settings") == before
    finally:
        conn.close()


def test_two_tables_with_different_rows_keep_the_old_rows_and_start(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    """Operator rule: the step loses no row and does not stop the start."""
    path = tmp_path / "claire.db"
    before = _make_v24_database(path, monkeypatch, mode="network")
    conn = dbm.connect(path)
    try:
        _copy_as_server_settings(conn)
        conn.execute("UPDATE server_settings SET value='single' WHERE key='mode'")
        conn.execute("DELETE FROM server_settings WHERE key='id_salt'")
        conn.commit()
        with caplog.at_level(logging.WARNING, logger=dbm.logger.name):
            dbm.init_db(conn)
        tables = _tables(conn)
        assert OLD not in tables and f"{OLD}_retired_v25" in tables
        assert _settings(conn, f"{OLD}_retired_v25") == before
        after = _settings(conn, "server_settings")
        # The missing row is copied; the row with another value keeps the value of server_settings.
        assert after["id_salt"] == before["id_salt"] and after["mode"] == "single"
        assert any("retired_v25" in r.getMessage() for r in caplog.records)
    finally:
        conn.close()
