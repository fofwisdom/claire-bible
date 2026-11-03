"""Schema v14 test suite: theme partitioning and database consolidation.

This module verifies the schema version 14 database requirements.
It validates the new tables, the theme partition isolation, and
the multi-database migration to a single database file.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from claire.store import db as dbm
from claire.store import queries as qm


def _memory_database() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    dbm.init_db(conn)
    return conn


def test_fresh_database_declares_schema_v14() -> None:
    """Verify that a fresh database initializes schema version 14."""
    conn = _memory_database()

    assert dbm.require_current_schema(conn) == 16
    assert dbm.stored_schema_lineage(conn) == "claire-bible/common"

    # Verify default theme 0 exists
    theme_zero = conn.execute("SELECT * FROM themes WHERE id = 0").fetchone()
    assert theme_zero is not None
    assert theme_zero["seq"] == 0
    assert theme_zero["is_default"] == 1


def test_schema_v14_contains_required_tables_and_columns() -> None:
    """Verify that all new v14 tables and theme_id columns exist."""
    conn = _memory_database()

    table_names = {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    required_tables = {
        "users",
        "themes",
        "theme_members",
        "syndication_accounts",
        "syndication_targets",
        "syndication_items",
        "syndication_dispatches",
    }
    assert required_tables <= table_names

    partitioned_tables = (
        "documents",
        "entities",
        "relations",
        "embeddings",
        "raw_inbox",
        "extractions",
        "proposals",
        "jobs",
        "refresh_queue",
        "expand_queue",
        "document_snapshots",
        "document_presentations",
        "doc_shares",
        "purged_tombstones",
    )
    for table in partitioned_tables:
        columns = {
            row["name"] for row in conn.execute(f"PRAGMA table_info({table})")
        }
        assert "theme_id" in columns, f"Column theme_id missing in table {table}"


def test_theme_partition_data_isolation() -> None:
    """Verify that records in different themes do not leak across queries."""
    conn = _memory_database()

    # Create theme 1
    conn.execute(
        "INSERT INTO themes (id, seq, label, is_default, created_at, updated_at) "
        "VALUES (1, 1, 'Theme 1', 0, 1.0, 1.0)"
    )
    conn.commit()

    # Insert document into theme 0 and theme 1
    dbm.insert_document(
        conn,
        dbm.Document(
            id="doc-theme-0",
            url="https://example.com/0",
            canonical_url="https://example.com/item",
            title="Doc 0",
            raw_text="Content 0",
        ),
        theme_id=0,
    )
    dbm.insert_document(
        conn,
        dbm.Document(
            id="doc-theme-1",
            url="https://example.com/1",
            canonical_url="https://example.com/item",  # same canonical_url in different theme
            title="Doc 1",
            raw_text="Content 1",
        ),
        theme_id=1,
    )

    # Insert entity and relation in theme 0
    dbm.upsert_entity(conn, dbm.Entity(id="ent-0", type="Concept", name="Entity Zero"), theme_id=0)
    dbm.upsert_entity(conn, dbm.Entity(id="ent-0-b", type="Concept", name="Entity Zero B"), theme_id=0)
    dbm.upsert_relation(conn, dbm.Relation(id="rel-0", type="relates_to", source_id="ent-0", target_id="ent-0-b"), theme_id=0)

    # Insert entity and relation in theme 1
    dbm.upsert_entity(conn, dbm.Entity(id="ent-1", type="Concept", name="Entity One"), theme_id=1)
    dbm.upsert_entity(conn, dbm.Entity(id="ent-1-b", type="Concept", name="Entity One B"), theme_id=1)
    dbm.upsert_relation(conn, dbm.Relation(id="rel-1", type="relates_to", source_id="ent-1", target_id="ent-1-b"), theme_id=1)

    # Check document isolation
    doc_t0 = dbm.find_document_by_canonical_url(conn, "https://example.com/item", theme_id=0)
    assert doc_t0 == "doc-theme-0"

    doc_t1 = dbm.find_document_by_canonical_url(conn, "https://example.com/item", theme_id=1)
    assert doc_t1 == "doc-theme-1"

    # Check timeline query isolation
    timeline_0 = dbm.documents_timeline(conn, 10, theme_id=0)
    assert len(timeline_0) == 1
    assert timeline_0[0]["id"] == "doc-theme-0"

    timeline_1 = dbm.documents_timeline(conn, 10, theme_id=1)
    assert len(timeline_1) == 1
    assert timeline_1[0]["id"] == "doc-theme-1"

    # Check graph_json isolation
    graph_0 = qm.graph_json(conn, theme_id=0)
    assert len(graph_0["nodes"]) == 2
    assert {n["id"] for n in graph_0["nodes"]} == {"ent-0", "ent-0-b"}

    graph_1 = qm.graph_json(conn, theme_id=1)
    assert len(graph_1["nodes"]) == 2
    assert {n["id"] for n in graph_1["nodes"]} == {"ent-1", "ent-1-b"}

    # Check theme_summary isolation
    summary_0 = qm.theme_summary(conn, theme_id=0)
    assert summary_0["documents"] == 1
    assert summary_0["entities"] == 2
    assert summary_0["relations"] == 1

    summary_1 = qm.theme_summary(conn, theme_id=1)
    assert summary_1["documents"] == 1
    assert summary_1["entities"] == 2
    assert summary_1["relations"] == 1

    # Check reset_graph isolation
    dbm.reset_graph(conn, theme_id=1)
    assert len(dbm.all_entities(conn, theme_id=1)) == 0
    assert len(dbm.all_relations(conn, theme_id=1)) == 0
    assert len(dbm.all_entities(conn, theme_id=0)) == 2
    assert len(dbm.all_relations(conn, theme_id=0)) == 1


def test_migrate_multi_db_to_single_db(tmp_path: Path) -> None:
    """Verify consolidation of physical theme databases into a unified database."""
    # Set up source theme databases
    theme_dir = tmp_path / "themes" / "research"
    theme_dir.mkdir(parents=True)
    source_db_path = theme_dir / "claire.db"

    # Create themes.json
    themes_data = {
        "version": 1,
        "themes": [
            {
                "id": 2,
                "seq": 2,
                "label": "Research Theme",
                "db_path": "themes/research/claire.db",
                "description": "Scientific research materials",
                "icon": "🔬",
                "is_default": False,
                "is_public": True,
            }
        ]
    }
    (tmp_path / "themes.json").write_text(json.dumps(themes_data, ensure_ascii=False), encoding="utf-8")

    # Populate source database with v13 schema data
    src_conn = sqlite3.connect(source_db_path)
    src_conn.row_factory = sqlite3.Row
    dbm.init_db(src_conn)

    dbm.insert_document(
        src_conn,
        dbm.Document(
            id="doc-research-1",
            url="https://research.org/paper1",
            title="Research Paper 1",
            raw_text="Important findings",
        ),
        theme_id=0,
    )
    dbm.upsert_entity(src_conn, dbm.Entity(id="ent-paper-1", type="Paper", name="Research Paper 1"), theme_id=0)
    dbm.log_inbox(src_conn, source="test", user_id=1, kind="text", payload="source inbox payload", theme_id=0)
    src_conn.close()

    # Destination single database
    dest_conn = sqlite3.connect(":memory:")
    dest_conn.row_factory = sqlite3.Row
    dbm.init_db(dest_conn)
    # Pre-populate dest_conn with a theme 0 inbox row (will get id=1)
    dbm.log_inbox(dest_conn, source="test", user_id=1, kind="text", payload="dest inbox payload", theme_id=0)

    # Perform multi-db migration
    report = dbm.migrate_multi_db_to_single_db(dest_conn, tmp_path)

    # Verify migration report
    assert 2 in report["migrated_themes"]
    assert "documents" in report["migrated_tables"][2]
    assert "entities" in report["migrated_tables"][2]
    assert "raw_inbox" in report["migrated_tables"][2]

    # Verify rows in dest_conn have theme_id=2
    doc = dbm.get_document_row(dest_conn, "doc-research-1", theme_id=2)
    assert doc is not None
    assert doc["title"] == "Research Paper 1"

    ent = dbm.get_entity(dest_conn, "ent-paper-1", theme_id=2)
    assert ent is not None
    assert ent.name == "Research Paper 1"

    # Verify autoincrement raw_inbox rows were both preserved across themes
    inbox_0 = dbm.inbox_status_counts(dest_conn, theme_id=0)
    inbox_2 = dbm.inbox_status_counts(dest_conn, theme_id=2)
    assert inbox_0.get("received", 0) == 1
    assert inbox_2.get("received", 0) == 1

    # Verify source database file was renamed to .migrated
    assert not source_db_path.exists()
    assert (theme_dir / "claire.db.migrated").exists()
