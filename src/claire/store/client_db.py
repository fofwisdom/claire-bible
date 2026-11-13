"""``client.db``: the communication data of accounts (DATA_STORE_LAYOUT_DESIGN.md Section 3, RD27).

The file is ``client.db`` in the folder of ``claire.db`` (``client_db_path``), one file for all accounts. The folder of ``claire.db`` is the rule, not ``data_dir``, so that a changed ``CLAIRE_DB_PATH`` keeps both files together. It has its own schema version in ``client_meta`` (``CLIENT_SCHEMA_VERSION``); a change of this file does not change the version of ``claire.db``.

Rules:

- No transaction crosses files. A client action and its task are both in this file (``federation_client_outbox``).
- ``account_id`` and other IDs of ``claire.db`` are values, not foreign keys. The account purge deletes the rows of an account here (``purge_account``).
- ``connect_client_db`` makes the file and runs the migration, as ``auth_db.connect_auth_db`` does. Request paths use ``connect_client_existing``, which never makes a file.

Versions:

- 1 (FP4-2, 2026-11-13): feed tables (moved from ``claire.db`` version 21), ``federation_following``, ``federation_account_settings``, ``federation_client_outbox``.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

__all__ = [
    "CLIENT_DB_FILENAME",
    "CLIENT_OUTBOX_KINDS",
    "CLIENT_SCHEMA_LINEAGE",
    "CLIENT_SCHEMA_VERSION",
    "client_db_path",
    "connect_client_db",
    "connect_client_existing",
    "init_client_db",
    "purge_account",
    "purge_account_of_claire_db",
    "stored_client_version",
]

CLIENT_DB_FILENAME = "client.db"
CLIENT_SCHEMA_VERSION = 1
CLIENT_SCHEMA_LINEAGE = "claire-client"
# FEDERATION_REMOTE_READING_DESIGN.md Section 5.2: the kinds of the client outbox. The publish kinds (create, update, delete, update_actor, delete_actor) stay in the publish outbox of claire.db.
CLIENT_OUTBOX_KINDS: tuple[str, ...] = (
    "follow", "undo_follow", "reject_follow", "fetch_context",
    "note", "update_note", "auto_reply", "delete_note",
    "like", "undo_like", "announce", "undo_announce",
    "quote_request", "quote_authorize", "quote_reject", "quote_revoke",
)


def client_db_path(claire_db_file: Path | str) -> Path:
    """``client.db`` next to ``claire.db``."""
    return Path(claire_db_file).parent / CLIENT_DB_FILENAME


def _configure(conn: sqlite3.Connection) -> None:
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.execute("PRAGMA busy_timeout=5000;")


def connect_client_db(path: Path | str) -> sqlite3.Connection:
    """Open ``client.db`` (make the file when it is absent) and bring the schema to ``CLIENT_SCHEMA_VERSION``. A newer schema raises ``RuntimeError`` (no downgrade)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p), timeout=5.0)
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        _configure(conn)
        init_client_db(conn)
    except BaseException:
        conn.close()
        raise
    return conn


def connect_client_existing(path: Path | str, *, readonly: bool = False) -> sqlite3.Connection:
    """Open a prepared ``client.db``. A file that is absent is an error: the start of the process prepares it."""
    mode = "ro" if readonly else "rw"
    conn = sqlite3.connect(Path(path).resolve().as_uri() + f"?mode={mode}", uri=True, timeout=5.0)
    _configure(conn)
    if readonly:
        conn.execute("PRAGMA query_only=ON;")
    return conn


def stored_client_version(conn: sqlite3.Connection) -> int | None:
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='client_meta'").fetchone() is None:
        return None
    row = conn.execute("SELECT value FROM client_meta WHERE key='schema_version'").fetchone()
    return None if row is None else int(row[0])


def init_client_db(conn: sqlite3.Connection) -> None:
    """Idempotent (A3). Each version step adds tables only."""
    version = stored_client_version(conn)
    if version is not None and version > CLIENT_SCHEMA_VERSION:
        raise RuntimeError(f"client.db schema is newer than this code: actual={version}, expected<={CLIENT_SCHEMA_VERSION}")
    conn.execute("CREATE TABLE IF NOT EXISTS client_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    _migrate_v1(conn)
    conn.execute("INSERT INTO client_meta (key, value) VALUES ('schema_version', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(CLIENT_SCHEMA_VERSION),))
    conn.execute("INSERT INTO client_meta (key, value) VALUES ('schema_lineage', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (CLIENT_SCHEMA_LINEAGE,))
    conn.commit()


def _migrate_v1(conn: sqlite3.Connection) -> None:
    # Feeds (FEDERATION_REMOTE_READING_DESIGN.md Section 1.3 rule 7). Same columns and IDs as claire.db version 21; account_id is a value.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS feed_subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER NOT NULL,
            url TEXT NOT NULL,
            site_url TEXT,
            title TEXT NOT NULL DEFAULT '',
            folder TEXT NOT NULL DEFAULT '',
            format TEXT CHECK (format IS NULL OR format IN ('rss', 'atom', 'json')),
            etag TEXT,
            last_modified TEXT,
            websub_hub TEXT,
            websub_topic TEXT,
            last_polled_at REAL,
            last_success_at REAL,
            next_poll_at REAL,
            last_status INTEGER,
            last_error TEXT,
            failure_count INTEGER NOT NULL DEFAULT 0,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            UNIQUE (account_id, url)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_feed_subscriptions_next_poll ON feed_subscriptions(next_poll_at)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS feed_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER NOT NULL,
            feed_id INTEGER NOT NULL REFERENCES feed_subscriptions(id) ON DELETE CASCADE,
            item_key TEXT NOT NULL,
            url TEXT,
            title TEXT NOT NULL DEFAULT '',
            author TEXT,
            summary TEXT,
            content TEXT,
            categories TEXT NOT NULL DEFAULT '[]',
            published_at REAL,
            modified_at REAL,
            fetched_at REAL NOT NULL,
            unread INTEGER NOT NULL DEFAULT 1 CHECK (unread IN (0, 1)),
            starred INTEGER NOT NULL DEFAULT 0 CHECK (starred IN (0, 1)),
            linked_theme_id INTEGER,
            linked_doc_id TEXT,
            UNIQUE (feed_id, item_key)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_feed_items_account ON feed_items(account_id, id)")
    # Follows (Section 7.1).
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS federation_following (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER NOT NULL,
            request TEXT NOT NULL,
            actor_iri TEXT,
            actor_type TEXT,
            inbox_iri TEXT,
            shared_inbox_iri TEXT,
            display_name TEXT,
            state TEXT NOT NULL CHECK (state IN ('pending', 'accepted', 'rejected')),
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            UNIQUE (account_id, actor_iri)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_federation_following_actor ON federation_following(actor_iri)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_federation_following_account ON federation_following(account_id, id)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS federation_account_settings (
            account_id INTEGER PRIMARY KEY,
            retention_days INTEGER CHECK (retention_days IS NULL OR retention_days >= 1),
            updated_at REAL NOT NULL
        )
        """
    )
    kinds = ", ".join(f"'{k}'" for k in CLIENT_OUTBOX_KINDS)
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS federation_client_outbox (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER NOT NULL,
            kind TEXT NOT NULL CHECK (kind IN ({kinds})),
            target_iri TEXT,
            ref_id INTEGER,
            status TEXT NOT NULL CHECK (status IN ('pending', 'handed_off', 'rejected')),
            lease_until REAL,
            error_code TEXT,
            created_at REAL NOT NULL,
            handed_off_at REAL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_federation_client_outbox_ready ON federation_client_outbox(status, id)")


def purge_account(conn: sqlite3.Connection, account_id: int) -> None:
    """Delete all client data of the account (Section 10 rule 5). No ingested document is in this file. The caller commits."""
    conn.execute("DELETE FROM feed_items WHERE account_id=?", (account_id,))
    conn.execute("DELETE FROM feed_subscriptions WHERE account_id=?", (account_id,))
    conn.execute("DELETE FROM federation_following WHERE account_id=?", (account_id,))
    conn.execute("DELETE FROM federation_account_settings WHERE account_id=?", (account_id,))
    conn.execute("DELETE FROM federation_client_outbox WHERE account_id=?", (account_id,))


def purge_account_of_claire_db(claire_conn: sqlite3.Connection, account_id: int) -> bool:
    """Delete the client data of the account in the ``client.db`` next to the file of ``claire_conn``, and commit. Return ``False`` when ``claire_conn`` has no file (an in-memory database) or there is no ``client.db``."""
    main = next((Path(r[2]) for r in claire_conn.execute("PRAGMA database_list").fetchall() if r[1] == "main" and r[2]), None)
    if main is None or not client_db_path(main).exists():
        return False
    conn = connect_client_db(client_db_path(main))
    try:
        purge_account(conn, account_id)
        conn.commit()
    finally:
        conn.close()
    return True
