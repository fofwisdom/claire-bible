"""공통 스키마 v13의 계보, 수렴, 거부 경계를 검증한다."""

from __future__ import annotations

import sqlite3

import pytest

from claire.store import db as dbm


def _memory_database() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    dbm.init_db(conn)
    return conn


def _set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta(key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    conn.commit()


def test_fresh_database_declares_common_v13():
    conn = _memory_database()

    assert dbm.require_current_schema(conn) == 13
    assert dbm.stored_schema_lineage(conn) == "claire-bible/common"
    document_columns = {
        row["name"] for row in conn.execute("PRAGMA table_info(documents)")
    }
    assert {
        "detail",
        "seen",
        "watch_enabled",
        "pinned",
        "hidden",
    } <= document_columns
    indexes = {
        row["name"]
        for row in conn.execute("PRAGMA index_list(documents)")
    }
    assert "idx_documents_hidden" in indexes


def test_legacy_v1_table_shape_converges_before_dependent_indexes():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
        INSERT INTO meta(key, value) VALUES ('schema_version', '1');
        CREATE TABLE documents (
            id TEXT PRIMARY KEY,
            url TEXT,
            canonical_url TEXT,
            title TEXT,
            author TEXT,
            published_at TEXT,
            fetched_at REAL,
            raw_text TEXT,
            source_type TEXT,
            content_hash TEXT,
            lang TEXT,
            partial INTEGER DEFAULT 0,
            meta TEXT
        );
        INSERT INTO documents(id, title) VALUES ('legacy-v1', 'preserved');
        """
    )

    dbm.init_db(conn)

    assert dbm.require_current_schema(conn) == 13
    assert conn.execute(
        "SELECT title FROM documents WHERE id='legacy-v1'"
    ).fetchone()[0] == "preserved"
    indexes = {
        row["name"]
        for row in conn.execute("PRAGMA index_list(documents)")
    }
    assert {"idx_documents_watch", "idx_documents_hidden"} <= indexes


@pytest.mark.parametrize("legacy_version", [9, 10, 11])
def test_supported_versions_converge_without_losing_extensions(
    legacy_version
):
    conn = _memory_database()
    conn.execute("DELETE FROM meta WHERE key='schema_lineage'")
    _set_meta(conn, "schema_version", str(legacy_version))
    conn.execute("CREATE TABLE implementation_extension (value TEXT)")
    conn.execute("INSERT INTO implementation_extension VALUES ('preserved')")
    conn.commit()

    dbm.init_db(conn)

    assert dbm.require_current_schema(conn) == 13
    assert (
        conn.execute("SELECT value FROM implementation_extension").fetchone()[0]
        == "preserved"
    )


def test_future_version_is_not_rewritten():
    conn = _memory_database()
    _set_meta(conn, "schema_version", "14")

    with pytest.raises(RuntimeError, match="newer than this code"):
        dbm.init_db(conn)

    assert dbm.stored_schema_version(conn) == 14
    assert dbm.stored_schema_lineage(conn) == dbm.SCHEMA_LINEAGE


def test_foreign_lineage_is_not_rewritten():
    conn = _memory_database()
    _set_meta(conn, "schema_lineage", "unrelated/root")

    with pytest.raises(RuntimeError, match="lineage does not match"):
        dbm.init_db(conn)

    assert dbm.stored_schema_version(conn) == 13
    assert dbm.stored_schema_lineage(conn) == "unrelated/root"


def test_v13_without_lineage_is_ambiguous_and_rejected():
    conn = _memory_database()
    conn.execute("DELETE FROM meta WHERE key='schema_lineage'")
    conn.commit()

    with pytest.raises(RuntimeError, match="missing its common lineage marker"):
        dbm.init_db(conn)

    assert dbm.stored_schema_version(conn) == 13
    assert dbm.stored_schema_lineage(conn) is None


def test_v11_migration_preserves_origin_schema_and_data():
    conn = _memory_database()
    conn.execute(
        "INSERT INTO documents"
        "(id,title,detail,detail_format,detail_html,fetched_at) "
        "VALUES ('doc-v11','title','source','adoc','<p>source</p>',1.0)"
    )
    conn.execute(
        "INSERT INTO purged_tombstones"
        "(id,reason,purged_at) VALUES ('purged-v11','test',1.0)"
    )
    conn.execute("DELETE FROM meta WHERE key='schema_lineage'")
    _set_meta(conn, "schema_version", "11")

    dbm.init_db(conn)

    document = conn.execute(
        "SELECT detail_format,detail_html FROM documents WHERE id='doc-v11'"
    ).fetchone()
    assert tuple(document) == ("adoc", "<p>source</p>")
    assert conn.execute(
        "SELECT reason FROM purged_tombstones WHERE id='purged-v11'"
    ).fetchone()[0] == "test"
