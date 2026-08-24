"""Tests for Knowledge Graph diagnosis and auto-healing (doctor)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from claire.cli import cmd_doctor
from claire.config import Settings
from claire.ontology.base import Document, Entity, Relation
from claire.store import db as dbm


@pytest.fixture
def temp_db(tmp_path: Path) -> Path:
    db_file = tmp_path / "claire.db"
    conn = dbm.connect(db_file)
    dbm.init_db(conn)
    conn.close()
    return db_file


def test_diagnose_clean_db(temp_db: Path):
    conn = dbm.connect(temp_db)
    doc = Document(id="doc1", title="Doc 1", raw_text="text", source_type="text")
    dbm.insert_document(conn, doc)

    ent = Entity(id="ent1", type="Concept", name="AI", sources=["doc1"])
    dbm.upsert_entity(conn, ent)

    rel = Relation(id="rel1", type="related_to", source_id="ent1", target_id="ent1", sources=["doc1"])
    dbm.upsert_relation(conn, rel)

    report = dbm.diagnose_graph(conn)
    conn.close()

    assert report["is_healthy"] is True
    assert report["dangling_relations_count"] == 0
    assert report["stale_entity_sources_count"] == 0
    assert report["ghost_entities_count"] == 0
    assert report["orphan_embeddings_count"] == 0
    assert report["fts_desync"] is False


def test_diagnose_detects_and_heals_all_issues(temp_db: Path):
    conn = dbm.connect(temp_db)

    # 1. Insert 1 valid document
    doc = Document(id="doc_valid", title="Valid Doc", raw_text="text", source_type="text")
    dbm.insert_document(conn, doc)

    # 2. Insert valid entity
    ent1 = Entity(id="ent1", type="Concept", name="Claire", sources=["doc_valid"])
    dbm.upsert_entity(conn, ent1)

    # 3. Insert entity with stale source (pointing to deleted/nonexistent doc_deleted)
    ent2 = Entity(id="ent2", type="Concept", name="Bible", sources=["doc_valid", "doc_deleted"])
    dbm.upsert_entity(conn, ent2)

    # 4. Insert ghost entity (pointing only to deleted doc, 0 relations)
    ghost = Entity(id="ghost_ent", type="Ghost", name="Ghost Node", sources=["doc_deleted"])
    dbm.upsert_entity(conn, ghost)

    # 5. Insert dangling relation (target_id='nonexistent_node' does not exist in entities)
    conn.execute(
        "INSERT INTO relations (id, type, source_id, target_id, sources, confidence, provisional, created_at) "
        "VALUES ('rel_dangling', 'links_to', 'ent1', 'nonexistent_node', '[\"doc_valid\"]', 1.0, 0, 1700000000.0)"
    )

    # 6. Insert orphan embedding
    conn.execute(
        "INSERT INTO embeddings (owner_id, dim, vector, model, updated_at) "
        "VALUES ('orphan_owner', 3, X'010203', 'model', 1700000000.0)"
    )

    # 7. Corrupt FTS table by deleting an entry directly
    conn.execute("DELETE FROM entities_fts WHERE entity_id='ent1'")
    conn.commit()

    # --- Run Diagnosis ---
    diag_before = dbm.diagnose_graph(conn)
    assert diag_before["is_healthy"] is False
    assert diag_before["dangling_relations_count"] == 1
    assert diag_before["stale_entity_sources_count"] == 2  # ent2 and ghost_ent
    assert diag_before["ghost_entities_count"] == 1  # ghost_ent
    assert diag_before["orphan_embeddings_count"] == 1
    assert diag_before["fts_desync"] is True

    # --- Run Heal ---
    healed = dbm.heal_graph(conn)
    assert healed["dangling_relations_removed"] == 1
    assert healed["stale_entity_sources_cleaned"] >= 1
    assert healed["ghost_entities_pruned"] == 1
    assert healed["orphan_embeddings_removed"] == 1
    assert healed["fts_reindexed"] == 2  # ent1 and ent2

    # --- Run Diagnosis Again (Must be 100% Healthy) ---
    diag_after = dbm.diagnose_graph(conn)
    assert diag_after["is_healthy"] is True
    assert diag_after["dangling_relations_count"] == 0
    assert diag_after["stale_entity_sources_count"] == 0
    assert diag_after["ghost_entities_count"] == 0
    assert diag_after["orphan_embeddings_count"] == 0
    assert diag_after["fts_desync"] is False
    assert diag_after["total_entities"] == 2

    # Verify ent2 sources was cleaned
    ent2_row = conn.execute("SELECT sources FROM entities WHERE id='ent2'").fetchone()
    assert json.loads(ent2_row["sources"]) == ["doc_valid"]

    # Verify ghost_ent was completely deleted
    assert conn.execute("SELECT 1 FROM entities WHERE id='ghost_ent'").fetchone() is None
    assert conn.execute("SELECT 1 FROM entities_fts WHERE entity_id='ghost_ent'").fetchone() is None

    conn.close()


def test_cli_doctor_command_json_and_heal(temp_db: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CLAIRE_DB_PATH", str(temp_db))

    # 1. Run doctor (clean)
    rc = cmd_doctor(SimpleNamespace(heal=False, apply=False, repair=False, json=True))
    assert rc == 0
    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["is_healthy"] is True

    # 2. Run doctor --heal
    rc_heal = cmd_doctor(SimpleNamespace(heal=True, apply=False, repair=False, json=False))
    assert rc_heal == 0
    out_heal = capsys.readouterr().out
    assert "수복 완료" in out_heal
