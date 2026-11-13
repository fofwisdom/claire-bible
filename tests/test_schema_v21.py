"""Schema v21: the account feed tables ``feed_subscriptions`` and ``feed_items`` (FEDERATION_REMOTE_READING_DESIGN.md Section 1.3 rule 7, unit FP4-F1)."""

from __future__ import annotations

from pathlib import Path

import pytest

from claire.store import db as dbm

FEED_TABLES = ("feed_subscriptions", "feed_items")
FEED_INDEXES = ("idx_feed_subscriptions_next_poll", "idx_feed_items_account")


def _make_v20_database(path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Initialize with the current code without the v21 step."""
    with monkeypatch.context() as m:
        m.setattr(dbm, "SCHEMA_VERSION", 20)
        m.setattr(dbm, "_migrate_v21_account_feeds", lambda conn: None)
        conn = dbm.connect(path)
        try:
            dbm.init_db(conn)
            assert dbm.stored_schema_version(conn) == 20
        finally:
            conn.close()


def _names(conn) -> set[str]:
    return {r["name"] for r in conn.execute("SELECT name FROM sqlite_master")}


def test_v20_database_upgrades_to_v21_and_keeps_rows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "claire.db"
    _make_v20_database(path, monkeypatch)
    conn = dbm.connect(path)
    try:
        assert not set(FEED_TABLES) & _names(conn)
        conn.execute("INSERT INTO documents (id, theme_id, title) VALUES ('doc-1', 0, 'Doc')")
        conn.commit()
        before = [tuple(r) for r in conn.execute("SELECT * FROM documents")]

        dbm.init_db(conn)

        assert dbm.SCHEMA_VERSION == 21 and dbm.require_current_schema(conn) == 21
        assert set(FEED_TABLES) | set(FEED_INDEXES) <= _names(conn)
        assert [tuple(r) for r in conn.execute("SELECT * FROM documents")] == before
        # The tables work: a feed of the admin account and one item, unique by (feed_id, item_key).
        conn.execute("INSERT INTO feed_subscriptions (account_id, url, created_at, updated_at) VALUES (1, 'https://a.example/feed', 1, 1)")
        conn.execute("INSERT INTO feed_items (account_id, feed_id, item_key, fetched_at) VALUES (1, 1, 'k', 1)")
        with pytest.raises(Exception, match="UNIQUE"):
            conn.execute("INSERT INTO feed_items (account_id, feed_id, item_key, fetched_at) VALUES (1, 1, 'k', 2)")
        with pytest.raises(Exception, match="UNIQUE"):
            conn.execute("INSERT INTO feed_subscriptions (account_id, url, created_at, updated_at) VALUES (1, 'https://a.example/feed', 1, 1)")
        with pytest.raises(Exception, match="CHECK"):
            conn.execute("INSERT INTO feed_subscriptions (account_id, url, format, created_at, updated_at) VALUES (1, 'https://b.example/feed', 'html', 1, 1)")
    finally:
        conn.close()


def test_v21_migration_runs_twice_without_change(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "claire.db"
    _make_v20_database(path, monkeypatch)
    conn = dbm.connect(path)
    try:
        dbm.init_db(conn)
        conn.execute("INSERT INTO feed_subscriptions (account_id, url, created_at, updated_at) VALUES (1, 'https://a.example/feed', 1, 1)")
        conn.commit()
        first = list(conn.iterdump())
        dbm.init_db(conn)
        dbm._migrate_v21_account_feeds(conn)
        conn.commit()
        assert list(conn.iterdump()) == first
    finally:
        conn.close()


def test_v21_database_is_rejected_by_v20_code(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "claire.db"
    conn = dbm.connect(path)
    try:
        dbm.init_db(conn)
    finally:
        conn.close()
    monkeypatch.setattr(dbm, "SCHEMA_VERSION", 20)
    conn = dbm.connect(path)
    try:
        with pytest.raises(RuntimeError, match="actual=21, expected<=20"):
            dbm.init_db(conn)
    finally:
        conn.close()
