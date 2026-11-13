"""``client.db`` (DATA_STORE_LAYOUT_DESIGN.md Section 3, RD27) and the move of the feed tables from ``claire.db`` version 21 (Section 3.2).

I/O tests: the file, its own version, the move with the same IDs, and the purge of an account in both files.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from claire.store import client_db
from claire.store import db as dbm


def _feed_table_sql() -> list[str]:
    """The DDL of the feed tables, as ``claire.db`` version 21 had them (same columns as ``client.db``)."""
    scratch = sqlite3.connect(":memory:")
    client_db.init_client_db(scratch)
    rows = scratch.execute("SELECT sql FROM sqlite_master WHERE name IN ('feed_subscriptions', 'feed_items', 'idx_feed_subscriptions_next_poll', 'idx_feed_items_account') ORDER BY type DESC, name DESC").fetchall()
    scratch.close()
    return [r[0] for r in rows]


def _make_v21_database_with_feeds(path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with monkeypatch.context() as m:
        m.setattr(dbm, "SCHEMA_VERSION", 21)
        m.setattr(dbm, "_migrate_v22_move_feed_tables", lambda conn: None)
        conn = dbm.connect(path)
        try:
            dbm.init_db(conn)
            for sql in _feed_table_sql():
                conn.execute(sql)
            for feed_id, account_id, url in ((3, 1, "https://a.example/feed"), (7, 2, "https://b.example/feed")):
                conn.execute("INSERT INTO feed_subscriptions (id, account_id, url, created_at, updated_at) VALUES (?, ?, ?, 1, 1)", (feed_id, account_id, url))
            conn.execute("INSERT INTO feed_items (id, account_id, feed_id, item_key, title, fetched_at, starred) VALUES (11, 1, 3, 'k1', 'One', 1, 1)")
            conn.execute("INSERT INTO feed_items (id, account_id, feed_id, item_key, title, fetched_at) VALUES (12, 2, 7, 'k2', 'Two', 1)")
            conn.commit()
            assert dbm.stored_schema_version(conn) == 21
        finally:
            conn.close()


def _tables(conn) -> set[str]:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def test_v21_feeds_move_to_client_db_with_the_same_ids_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "claire.db"
    _make_v21_database_with_feeds(path, monkeypatch)
    conn = dbm.connect(path)
    try:
        dbm.init_db(conn)
        assert dbm.require_current_schema(conn) == dbm.SCHEMA_VERSION
        assert not {"feed_subscriptions", "feed_items"} & _tables(conn)
    finally:
        conn.close()
    client = client_db.connect_client_existing(client_db.client_db_path(path))
    try:
        assert client_db.stored_client_version(client) == client_db.CLIENT_SCHEMA_VERSION
        assert [tuple(r) for r in client.execute("SELECT id, account_id, url FROM feed_subscriptions ORDER BY id")] == [(3, 1, "https://a.example/feed"), (7, 2, "https://b.example/feed")]
        assert [tuple(r) for r in client.execute("SELECT id, feed_id, title, starred FROM feed_items ORDER BY id")] == [(11, 3, "One", 1), (12, 7, "Two", 0)]
        # New rows get IDs after the moved ones.
        client.execute("INSERT INTO feed_subscriptions (account_id, url, created_at, updated_at) VALUES (1, 'https://c.example/feed', 2, 2)")
        assert client.execute("SELECT max(id) FROM feed_subscriptions").fetchone()[0] == 8
        client.commit()
    finally:
        client.close()
    # A second start changes nothing.
    conn = dbm.connect(path)
    try:
        dbm.init_db(conn)
    finally:
        conn.close()
    client = client_db.connect_client_existing(client_db.client_db_path(path))
    try:
        assert client.execute("SELECT COUNT(*) FROM feed_subscriptions").fetchone()[0] == 3
    finally:
        client.close()


def test_a_new_database_has_no_feed_tables_in_claire_db(tmp_path: Path) -> None:
    conn = dbm.connect(tmp_path / "claire.db")
    try:
        dbm.init_db(conn)
        assert not {"feed_subscriptions", "feed_items"} & _tables(conn)
    finally:
        conn.close()


def test_client_db_newer_than_the_code_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "client.db"
    client_db.connect_client_db(path).close()
    conn = sqlite3.connect(path)
    conn.execute("UPDATE client_meta SET value=? WHERE key='schema_version'", (str(client_db.CLIENT_SCHEMA_VERSION + 1),))
    conn.commit()
    conn.close()
    with pytest.raises(RuntimeError, match="client.db schema is newer"):
        client_db.connect_client_db(path)


def test_purge_of_an_account_deletes_its_client_rows_only(tmp_path: Path) -> None:
    path = tmp_path / "claire.db"
    conn = dbm.connect(path)
    dbm.init_db(conn)
    client = client_db.connect_client_db(client_db.client_db_path(path))
    for account_id in (1, 2):
        client.execute("INSERT INTO feed_subscriptions (account_id, url, created_at, updated_at) VALUES (?, ?, 1, 1)", (account_id, f"https://{account_id}.example/feed"))
        client.execute("INSERT INTO federation_following (account_id, request, state, created_at, updated_at) VALUES (?, '@a@remote.example', 'pending', 1, 1)", (account_id,))
        client.execute("INSERT INTO federation_client_outbox (account_id, kind, status, created_at) VALUES (?, 'follow', 'pending', 1)", (account_id,))
    client.commit()
    client.close()
    assert client_db.purge_account_of_claire_db(conn, 2) is True
    conn.close()
    client = client_db.connect_client_existing(client_db.client_db_path(path))
    try:
        for table in ("feed_subscriptions", "federation_following", "federation_client_outbox"):
            assert [r[0] for r in client.execute(f"SELECT account_id FROM {table}")] == [1]
    finally:
        client.close()
