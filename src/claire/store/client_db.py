"""``client.db``: the communication data of accounts (DATA_STORE_LAYOUT_DESIGN.md Section 3, RD27).

The file is ``client.db`` in the folder of ``claire.db`` (``client_db_path``), one file for all accounts. The folder of ``claire.db`` is the rule, not ``data_dir``, so that a changed ``CLAIRE_DB_PATH`` keeps both files together. It has its own schema version in ``client_meta`` (``CLIENT_SCHEMA_VERSION``); a change of this file does not change the version of ``claire.db``.

Rules:

- No transaction crosses files. A client action and its task are both in this file (``federation_client_outbox``).
- ``account_id`` and other IDs of ``claire.db`` are values, not foreign keys. The account purge deletes the rows of an account here (``purge_account``).
- ``connect_client_db`` makes the file and runs the migration, as ``auth_db.connect_auth_db`` does. Request paths use ``connect_client_existing``, which never makes a file.

Versions:

- 1 (FP4-2, 2026-11-13): feed tables (moved from ``claire.db`` version 21), ``federation_following``, ``federation_account_settings`` (removed in version 8), ``federation_client_outbox``.
- 2 (FP4-4, 2026-11-15): ``federation_timeline_items`` (FEDERATION_REMOTE_READING_DESIGN.md Section 7.1).
- 3 (FP4-7, 2026-11-15): ``federation_media_cache`` (FEDERATION_REMOTE_READING_DESIGN.md Section 7.2).
- 4 (FP4-8, 2026-11-15): ``federation_object_reactions`` and ``federation_auto_replies`` (FEDERATION_REMOTE_READING_DESIGN.md Section 7.1).
- 5 (FP4-9, 2026-11-15): ``federation_notes`` (FEDERATION_REMOTE_READING_DESIGN.md Sections 5.4 and 7.1). The quote columns of the design come with unit FP4-9Q (``ALTER TABLE ... ADD COLUMN``, no rebuild).
- 6 (FP4-9Q, 2026-11-15): the quote columns of ``federation_notes`` and ``federation_quote_authorizations`` (FEDERATION_REMOTE_READING_DESIGN.md Sections 5.4 rules 5 and 6, 6.6 rule 5, and 7.1).
- 7: the column ``linked_theme_id`` becomes ``linked_kb_id`` (SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md Section 5.1).
- 8 (2026-11-18): ``federation_account_settings`` goes. The retention of received posts is a right of the server admin only (owner decision); the retention run uses the server value for each account.
- 9 (PN1, 2026-11-22): ``notifications``, ``push_subscriptions``, ``push_queue``, ``notification_push_off``, and ``notification_scan_seen`` (PWA_NOTIFICATION_DESIGN.md PN1; functions in ``store/notifications.py``).
"""

from __future__ import annotations

import logging
import sqlite3
import time
from pathlib import Path

__all__ = [
    "CLIENT_DB_FILENAME",
    "CLIENT_OUTBOX_KINDS",
    "CLIENT_SCHEMA_LINEAGE",
    "CLIENT_SCHEMA_VERSION",
    "QUOTE_POLICIES",
    "QUOTE_STATES",
    "client_db_path",
    "connect_client_db",
    "connect_client_existing",
    "init_client_db",
    "purge_account",
    "purge_account_of_claire_db",
    "stored_client_version",
]

logger = logging.getLogger(__name__)

CLIENT_DB_FILENAME = "client.db"
CLIENT_SCHEMA_VERSION = 11
CLIENT_SCHEMA_LINEAGE = "claire-client"
# FEDERATION_REMOTE_READING_DESIGN.md Section 5.2: the kinds of the client outbox. The publish kinds (create, update, delete, update_actor, delete_actor) stay in the publish outbox of claire.db.
CLIENT_OUTBOX_KINDS: tuple[str, ...] = (
    "follow", "undo_follow", "reject_follow", "fetch_context",
    "note", "update_note", "auto_reply", "delete_note",
    "like", "undo_like", "announce", "undo_announce",
    "quote_request", "quote_authorize", "quote_reject", "quote_revoke",
)
# FEDERATION_REMOTE_READING_DESIGN.md Section 5.4 rules 5 and 6: the states of a quote and the quote policies of an own object.
QUOTE_STATES: tuple[str, ...] = ("pending", "approved", "refused", "revoked")
QUOTE_POLICIES: tuple[str, ...] = ("public", "followers", "manual", "nobody")


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
    """Idempotent (A3). Each version step adds tables or columns, except version 8, which removes one table."""
    version = stored_client_version(conn)
    if version is not None and version > CLIENT_SCHEMA_VERSION:
        raise RuntimeError(f"client.db schema is newer than this code: actual={version}, expected<={CLIENT_SCHEMA_VERSION}")
    conn.execute("CREATE TABLE IF NOT EXISTS client_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    _migrate_v7_linked_kb_id(conn)
    _migrate_v1(conn)
    _migrate_v2(conn)
    _migrate_v3(conn)
    _migrate_v4(conn)
    _migrate_v5(conn)
    _migrate_v6(conn)
    _migrate_v8_drop_account_settings(conn)
    _migrate_v9(conn)
    _migrate_v10(conn)
    _migrate_v11(conn)
    conn.execute("INSERT INTO client_meta (key, value) VALUES ('schema_version', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(CLIENT_SCHEMA_VERSION),))
    conn.execute("INSERT INTO client_meta (key, value) VALUES ('schema_lineage', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (CLIENT_SCHEMA_LINEAGE,))
    conn.commit()


def _migrate_v7_linked_kb_id(conn: sqlite3.Connection) -> None:
    """v7 (SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md Section 5.1): the column ``linked_theme_id`` becomes ``linked_kb_id`` with its values. Idempotent (A3).

    The step runs first, because the later steps already use the new name.
    """
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()]
    for table in tables:
        cols = {r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')}
        if "linked_theme_id" in cols and "linked_kb_id" not in cols:
            conn.execute(f'ALTER TABLE "{table}" RENAME COLUMN linked_theme_id TO linked_kb_id')


def _migrate_v8_drop_account_settings(conn: sqlite3.Connection) -> None:
    """v8: remove ``federation_account_settings`` (the retention of an account). Idempotent (A3).

    The retention of received posts is a right of the server admin only (owner decision 2026-11-18). A row of the table was only a shorter retention that an account chose; the server value replaces it, thus the step removes the table and logs the count of the values that it removes.
    """
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='federation_account_settings'").fetchone() is None:
        return
    count = int(conn.execute("SELECT COUNT(*) FROM federation_account_settings WHERE retention_days IS NOT NULL").fetchone()[0])
    conn.execute("DROP TABLE federation_account_settings")
    if count:
        logger.warning("client.db v8: removed %d account retention values; the server retention applies to each account", count)


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
            linked_kb_id INTEGER,
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


def _migrate_v2(conn: sqlite3.Connection) -> None:
    # Received posts of an account (Section 7.1). The key is (account_id, object_iri) (Section 6.4 rule 3). content_html is raw: only the render route sanitizes it (Section 8).
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS federation_timeline_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER NOT NULL,
            object_iri TEXT NOT NULL,
            url TEXT,
            object_type TEXT NOT NULL,
            author_iri TEXT NOT NULL,
            author_json TEXT NOT NULL,
            announced_by_iri TEXT,
            announced_by_json TEXT,
            name TEXT,
            summary TEXT,
            sensitive INTEGER NOT NULL DEFAULT 0,
            content_html TEXT NOT NULL,
            language TEXT,
            attachments_json TEXT NOT NULL DEFAULT '[]',
            context_iri TEXT,
            in_reply_to_iri TEXT,
            in_timeline INTEGER NOT NULL DEFAULT 1,
            published_at REAL,
            updated_at REAL,
            received_at REAL NOT NULL,
            unread INTEGER NOT NULL DEFAULT 1,
            starred INTEGER NOT NULL DEFAULT 0,
            linked_kb_id INTEGER,
            linked_doc_id TEXT,
            mention INTEGER NOT NULL DEFAULT 0,
            quote_of_iri TEXT,
            favourited INTEGER NOT NULL DEFAULT 0,
            boosted INTEGER NOT NULL DEFAULT 0,
            UNIQUE (account_id, object_iri)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_federation_timeline_account ON federation_timeline_items(account_id, received_at DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_federation_timeline_object ON federation_timeline_items(object_iri)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_federation_timeline_thread ON federation_timeline_items(account_id, context_iri)")


def _migrate_v3(conn: sqlite3.Connection) -> None:
    # Records of the media cache (Section 7.2, unit FP4-7). One row for each remote URL, for all accounts: the media of one post goes to many accounts. The file is in the media store under ``remote/{url_hash}``.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS federation_media_cache (
            url_hash TEXT PRIMARY KEY,
            remote_url TEXT NOT NULL,
            store_key TEXT,
            media_type TEXT,
            byte_size INTEGER,
            state TEXT NOT NULL CHECK (state IN ('new', 'stored', 'failed', 'too_large')),
            fetched_at REAL,
            last_access_at REAL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_federation_media_cache_access ON federation_media_cache(last_access_at)")


def _migrate_v4(conn: sqlite3.Connection) -> None:
    # Reactions of remote actors to an object of an account (Section 6.6 rule 3, unit FP4-8). One row for each actor and kind, so that an Undo removes it. The counter of an object is the number of its rows.
    # account_id is the owner of the object: the account purge deletes by it, and the object tables are in claire.db.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS federation_object_reactions (
            object_public_id INTEGER NOT NULL,
            account_id INTEGER NOT NULL,
            kind TEXT NOT NULL CHECK (kind IN ('like', 'announce', 'quote')),
            actor_iri TEXT NOT NULL,
            activity_iri TEXT NOT NULL,
            received_at REAL NOT NULL,
            PRIMARY KEY (object_public_id, kind, actor_iri)
        )
        """
    )
    # Automatic notices for non-public messages (Section 6.2 rule 4). Claire keeps the sender and the time of the last notice only, and no content. count is the number of non-public messages of the sender.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS federation_auto_replies (
            account_id INTEGER NOT NULL,
            sender_iri TEXT NOT NULL,
            last_sent_at REAL NOT NULL,
            count INTEGER NOT NULL DEFAULT 1,
            PRIMARY KEY (account_id, sender_iri)
        )
        """
    )


def _migrate_v5(conn: sqlite3.Connection) -> None:
    # Own notes of an account (Section 5.4, unit FP4-9). public_id is in the number space of the published documents of claire.db (RT7): the object path is /ap/objects/{public_id} for the two.
    # content_html is made and sanitized by Claire from content_text. deleted_at is the tombstone of the note: the row stays without content, and the object path gives 410.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS federation_notes (
            public_id INTEGER PRIMARY KEY,
            account_id INTEGER NOT NULL,
            in_reply_to_iri TEXT,
            audience_iri TEXT,
            content_text TEXT NOT NULL,
            content_html TEXT NOT NULL,
            mentions_json TEXT NOT NULL DEFAULT '[]',
            summary TEXT,
            sensitive INTEGER NOT NULL DEFAULT 0,
            language TEXT,
            tags_json TEXT NOT NULL DEFAULT '[]',
            edited_at REAL,
            published_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            deleted_at REAL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_federation_notes_account ON federation_notes(account_id, public_id)")


def _migrate_v6(conn: sqlite3.Connection) -> None:
    # Quotes (FEP-044f, Section 5.4 rules 5 and 6, unit FP4-9Q). Each column is added only when it is absent, so that a second run changes nothing (A3). The rows of version 5 get no quote and the policy ``public``.
    # quote_of_iri, quote_author_iri: the quoted object and its author (the only actor whose answer changes quote_state). quote_authorization_iri: the QuoteAuthorization of an approved quote; a Delete of it by the author makes the quote ``revoked``.
    states = ", ".join(f"'{s}'" for s in QUOTE_STATES)
    policies = ", ".join(f"'{p}'" for p in QUOTE_POLICIES)
    present = {row[1] for row in conn.execute("PRAGMA table_info(federation_notes)")}
    for column, definition in (
        ("quote_of_iri", "TEXT"),
        ("quote_author_iri", "TEXT"),
        ("quote_state", f"TEXT CHECK (quote_state IS NULL OR quote_state IN ({states}))"),
        ("quote_authorization_iri", "TEXT"),
        ("quote_policy", f"TEXT NOT NULL DEFAULT 'public' CHECK (quote_policy IN ({policies}))"),
    ):
        if column not in present:
            conn.execute(f"ALTER TABLE federation_notes ADD COLUMN {column} {definition}")
    # Requests of remote actors to quote an own object (a note or a published document), and their results (Section 6.6 rule 5). The row of an approved request is the QuoteAuthorization at /ap/quote-authorizations/{id}.
    # account_id is the owner of the object: the account purge deletes by it. request_iri is the ID of the QuoteRequest: the Accept or the Reject refers to it.
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS federation_quote_authorizations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER NOT NULL,
            object_public_id INTEGER NOT NULL,
            quoting_object_iri TEXT NOT NULL,
            quoting_actor_iri TEXT NOT NULL,
            request_iri TEXT NOT NULL,
            state TEXT NOT NULL CHECK (state IN ({states})),
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            UNIQUE (object_public_id, quoting_object_iri)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_federation_quote_authorizations_account ON federation_quote_authorizations(account_id, state, id)")


def _migrate_v11(conn: sqlite3.Connection) -> None:
    # The opinions of published objects and their fetches (DOCUMENT_RENDITIONS_AND_PUBLICATION_DESIGN.md Section 5.11 C, phase E7a). The rows are of the server; the module ``federation_opinions`` owns the tables.
    from . import federation_opinions

    federation_opinions.ensure_schema(conn)


def _migrate_v10(conn: sqlite3.Connection) -> None:
    # The ``replies`` collection of an own object (DOCUMENT_RENDITIONS_AND_PUBLICATION_DESIGN.md Section 5.11 A, phase E6) reads the posts and the notes by their reply target.
    # An index is made only on a table that exists: the steps of versions 2 and 5 make the two tables before this step.
    for index, table in (("idx_federation_timeline_reply", "federation_timeline_items"), ("idx_federation_notes_reply", "federation_notes")):
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None:
            conn.execute(f"CREATE INDEX IF NOT EXISTS {index} ON {table}(in_reply_to_iri)")


def _migrate_v9(conn: sqlite3.Connection) -> None:
    # Notifications and Web Push (PWA_NOTIFICATION_DESIGN.md PN1). A notification stores the kind and its parameters; the text comes from the catalog at the read or the send.
    # group_key: while a row of the same account, kind, and group is not read, a new event changes that row (count, params_json, updated_at) and makes no second row. The partial unique index holds this rule.
    # pushed_at: the last time that the sender sent the row to a push service (the 60 s rule of PN3).
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER NOT NULL,
            kind TEXT NOT NULL,
            params_json TEXT NOT NULL DEFAULT '{}',
            target_path TEXT NOT NULL,
            group_key TEXT,
            count INTEGER NOT NULL DEFAULT 1,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            read_at REAL,
            pushed_at REAL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_notifications_account ON notifications(account_id, id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_notifications_unread ON notifications(account_id) WHERE read_at IS NULL")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_notifications_group ON notifications(account_id, kind, group_key) WHERE read_at IS NULL AND group_key IS NOT NULL")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_notifications_updated ON notifications(updated_at)")
    # One browser of one account (RFC 8030). endpoint is the capability URL of the push service; p256dh and auth are the keys of the browser for the message encryption (RFC 8291).
    # session_ref is the credential identifier of the session that made the subscription: the sender deletes a subscription whose session is gone (PN3 rule 7).
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS push_subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER NOT NULL,
            endpoint TEXT NOT NULL UNIQUE,
            p256dh TEXT NOT NULL,
            auth TEXT NOT NULL,
            label TEXT NOT NULL DEFAULT '',
            session_ref TEXT NOT NULL,
            created_at REAL NOT NULL,
            last_success_at REAL,
            failure_count INTEGER NOT NULL DEFAULT 0,
            last_error TEXT
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_push_subscriptions_account ON push_subscriptions(account_id, id)")
    # One message that waits for one subscription. A row goes when the send is done or is given up.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS push_queue (
            notification_id INTEGER NOT NULL,
            subscription_id INTEGER NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            next_attempt_at REAL NOT NULL,
            PRIMARY KEY (notification_id, subscription_id)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_push_queue_due ON push_queue(next_attempt_at)")
    # The kinds for which the account turned Web Push off. A kind without a row sends Web Push (PN1 rule 3).
    conn.execute("CREATE TABLE IF NOT EXISTS notification_push_off (account_id INTEGER NOT NULL, kind TEXT NOT NULL, PRIMARY KEY (account_id, kind))")
    # The items of claire.db that a scan told about (PN2 rule 5). scope 'contribution': the contributions that wait for review. The first scan of a scope records the items that exist and writes the key ``notification_scan:<scope>`` to client_meta.
    conn.execute("CREATE TABLE IF NOT EXISTS notification_scan_seen (scope TEXT NOT NULL, item_id INTEGER NOT NULL, PRIMARY KEY (scope, item_id))")


def purge_account(conn: sqlite3.Connection, account_id: int) -> None:
    """Delete all client data of the account (Section 10 rule 5). No ingested document is in this file. The caller commits.

    The media cache has no account: the entries that no item uses after this deletion go at the next media cleanup (``media_proxy.remove_orphans``).
    """
    conn.execute("DELETE FROM feed_items WHERE account_id=?", (account_id,))
    conn.execute("DELETE FROM feed_subscriptions WHERE account_id=?", (account_id,))
    conn.execute("DELETE FROM federation_following WHERE account_id=?", (account_id,))
    conn.execute("DELETE FROM federation_timeline_items WHERE account_id=?", (account_id,))
    conn.execute("DELETE FROM federation_client_outbox WHERE account_id=?", (account_id,))
    conn.execute("DELETE FROM federation_object_reactions WHERE account_id=?", (account_id,))
    conn.execute("DELETE FROM federation_auto_replies WHERE account_id=?", (account_id,))
    conn.execute("DELETE FROM federation_notes WHERE account_id=?", (account_id,))
    conn.execute("DELETE FROM federation_quote_authorizations WHERE account_id=?", (account_id,))
    conn.execute("DELETE FROM push_queue WHERE notification_id IN (SELECT id FROM notifications WHERE account_id=?) OR subscription_id IN (SELECT id FROM push_subscriptions WHERE account_id=?)", (account_id, account_id))
    conn.execute("DELETE FROM notifications WHERE account_id=?", (account_id,))
    conn.execute("DELETE FROM push_subscriptions WHERE account_id=?", (account_id,))
    conn.execute("DELETE FROM notification_push_off WHERE account_id=?", (account_id,))


def purge_account_of_claire_db(claire_conn: sqlite3.Connection, account_id: int) -> bool:
    """Delete the client data of the account in the ``client.db`` next to the file of ``claire_conn``, and commit. Return ``False`` when ``claire_conn`` has no file (an in-memory database) or there is no ``client.db``."""
    main = next((Path(r[2]) for r in claire_conn.execute("PRAGMA database_list").fetchall() if r[1] == "main" and r[2]), None)
    if main is None or not client_db_path(main).exists():
        return False
    conn = connect_client_db(client_db_path(main))
    try:
        # Each note of the account gets a tombstone in claire.db, as each published document does (unit FP4-9): the object path gives 410 after the purge. The caller commits claire.db.
        for public_id, deleted_at in conn.execute("SELECT public_id, deleted_at FROM federation_notes WHERE account_id=?", (account_id,)).fetchall():
            claire_conn.execute("INSERT INTO federation_tombstones (public_id, kind, gone_at) VALUES (?, 'object', ?) ON CONFLICT(public_id) DO NOTHING", (int(public_id), float(deleted_at) if deleted_at is not None else time.time()))
        purge_account(conn, account_id)
        conn.commit()
    finally:
        conn.close()
    return True
