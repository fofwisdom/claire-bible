from __future__ import annotations

import sqlite3
from pathlib import Path

from claire.ontology.base import Entity
from claire.store import db as dbm
from claire.store.vectors import pack_vector, read_kb_embedding_spaces


def _connect(db_file: Path) -> sqlite3.Connection:
    return dbm.connect(db_file)


def test_v39_fresh_schema_and_seeded_spaces(tmp_path: Path) -> None:
    """Verify fresh database initializes schema v39 with embedding_spaces registry and composite PK on embeddings."""
    conn = _connect(tmp_path / "claire.db")
    dbm.init_db(conn)
    assert int(conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0]) == dbm.SCHEMA_VERSION >= 39

    spaces = {r["space_id"]: dict(r) for r in conn.execute("SELECT * FROM embedding_spaces").fetchall()}
    assert "google:gemini-embedding-001:3072:v1_param" in spaces
    assert spaces["google:gemini-embedding-001:3072:v1_param"]["eos_date"] == "2028-05-14"
    assert "google:gemini-embedding-2:3072:v2_prompt" in spaces
    assert spaces["google:gemini-embedding-2:3072:v2_prompt"]["dim"] == 3072
    assert spaces["google:gemini-embedding-2:3072:v2_prompt"]["prompt_format_version"] == "v2_prompt"

    tinfo = {r["name"]: r for r in conn.execute("PRAGMA table_info(embeddings)").fetchall()}
    assert "space_id" in tinfo
    assert int(tinfo["owner_id"]["pk"]) > 0 and int(tinfo["kb_id"]["pk"]) > 0 and int(tinfo["space_id"]["pk"]) > 0

    kb_cols = {r["name"] for r in conn.execute("PRAGMA table_info(knowledge_bases)").fetchall()}
    assert "active_space_id" in kb_cols and "target_space_id" in kb_cols
    conn.close()


def test_v38_to_v39_migration_preserves_vectors_and_rebuilds_pk(tmp_path: Path) -> None:
    """Verify v38 database with legacy embeddings table rebuilds into composite PK (owner_id, kb_id, space_id) cleanly."""
    db_file = tmp_path / "v38.db"
    conn = _connect(db_file)
    dbm.init_db(conn)
    ent = Entity(type="Tool", name="Claire", observations=["kb engine"], sources=["d1"])
    dbm.upsert_entity(conn, ent, kb_id=0)
    conn.execute("UPDATE knowledge_bases SET embed_provider='gemini', embed_model='gemini-embedding-001', active_space_id=NULL WHERE id=0")
    conn.execute("DROP INDEX IF EXISTS idx_embeddings_kb_space")
    conn.execute("DROP TABLE embeddings")
    conn.execute("DROP TABLE embedding_spaces")
    conn.execute("CREATE TABLE embeddings (owner_id TEXT, kb_id INTEGER NOT NULL DEFAULT 0, dim INTEGER, vector BLOB, model TEXT, updated_at REAL, PRIMARY KEY (owner_id, kb_id))")
    conn.execute("INSERT INTO embeddings(owner_id, kb_id, dim, vector, model, updated_at) VALUES (?, 0, 3, ?, 'gemini-embedding-001', 1.0)", (ent.id, pack_vector([1.0, 0.0, 0.0])))
    conn.execute("UPDATE meta SET value='38' WHERE key='schema_version'")
    conn.commit()
    conn.close()

    conn = _connect(db_file)
    dbm.init_db(conn)
    assert int(conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0]) == dbm.SCHEMA_VERSION >= 39
    active_sid, target_sid = read_kb_embedding_spaces(conn, 0)
    assert active_sid == "google:gemini-embedding-001:3:v1_param"
    assert target_sid == ""

    row = conn.execute("SELECT owner_id, kb_id, space_id, model FROM embeddings WHERE owner_id=?", (ent.id,)).fetchone()
    assert row is not None and row["space_id"] == "google:gemini-embedding-001:3:v1_param" and row["model"] == "gemini-embedding-001"
    conn.close()
