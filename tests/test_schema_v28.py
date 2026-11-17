"""claire.db version 28, part (a): the schema of shared knowledge bases and themes (SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md Sections 5.4, 11.2, 11.4).

The conversion of the knowledge bases (Section 12.2) is a separate step and is not tested here.
"""

from __future__ import annotations

import sqlite3

import pytest

from claire import ids
from claire.store import db as dbm
from claire.store import kb_collab
from claire.store import themes as themes_store

V28_TABLES = {
    "kb_memberships", "kb_contributions", "kb_contribution_revisions", "kb_submissions", "kb_submission_items",
    "kb_auto_rules", "kb_auto_rule_themes", "kb_contribution_blocks", "kb_mutual_collaborations", "kb_moderation_log",
    "account_reputation", "themes", "theme_knowledge_bases", "kb_signatures", "kb_document_matches",
}


def _db() -> sqlite3.Connection:
    conn = dbm.connect(":memory:")
    dbm.init_db(conn)
    return conn


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def test_fresh_database_has_the_v28_schema() -> None:
    conn = _db()
    assert dbm.stored_schema_version(conn) == dbm.SCHEMA_VERSION >= 28  # version 29 adds the tables of the knowledge federation (tests/test_knowledge_federation_store.py)
    assert V28_TABLES <= _tables(conn)
    assert not {"users", "theme_members"} & _tables(conn)
    cols = dbm._column_names(conn, "knowledge_bases")
    assert {"created_by_account_id", "join_policy", "state", "archived_at", "knowledge_type", "seed_entities", "forked_from_kb_id", "forked_at"} <= cols
    row = conn.execute("SELECT join_policy, state, seed_entities, knowledge_type FROM knowledge_bases WHERE id=0").fetchone()
    assert tuple(row) == ("approval", "active", "[]", None)


def test_retired_tables_with_rows_are_kept_with_a_new_name(tmp_path) -> None:
    path = tmp_path / "claire.db"
    conn = dbm.connect(path)
    dbm.init_db(conn)
    conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT)")
    conn.execute("INSERT INTO users (id, email) VALUES (1, 'kept@example.org')")
    conn.execute("CREATE TABLE theme_members (id INTEGER PRIMARY KEY, kb_id INTEGER)")
    conn.commit()
    dbm.init_db(conn)
    tables = _tables(conn)
    assert "users" not in tables and "users_retired_v28" in tables
    assert "theme_members" not in tables  # no rows: dropped
    assert conn.execute("SELECT email FROM users_retired_v28").fetchone()[0] == "kept@example.org"
    # A second start changes nothing.
    before = sorted(tuple(r) for r in conn.execute("SELECT type, name, sql FROM sqlite_master"))
    dbm.init_db(conn)
    assert sorted(tuple(r) for r in conn.execute("SELECT type, name, sql FROM sqlite_master")) == before
    conn.close()


def test_kind_violations() -> None:
    assert kb_collab.kind_violation_reason(1, 5, 0, None) == ""
    assert kb_collab.kind_violation_reason(0, None, 1, "real") == ""
    assert kb_collab.kind_violation_reason(0, None, 1, "fictional") == ""
    assert kb_collab.kind_violation_reason(1, None, 1, None) == "personal_without_account"
    assert kb_collab.kind_violation_reason(1, 5, 1, "real") == "personal_with_knowledge_type"
    assert kb_collab.kind_violation_reason(0, 5, 1, "real") == "shared_with_owner"
    assert kb_collab.kind_violation_reason(0, None, 0, "real") == "shared_not_public"
    assert kb_collab.kind_violation_reason(0, None, 1, None) == "shared_without_knowledge_type"
    conn = _db()
    conn.execute("INSERT INTO knowledge_bases (id, seq, label, is_personal, is_public, created_at, updated_at, account_id) VALUES (7, 7, 'old owned theme', 0, 1, 1, 1, 1)")
    assert [(v.kb_id, v.reason) for v in kb_collab.kind_violations(conn)] == [(7, "shared_with_owner")]


def test_memberships_contributions_and_log() -> None:
    conn = _db()
    conn.execute("INSERT INTO knowledge_bases (id, seq, label, is_personal, is_public, knowledge_type, created_at, updated_at) VALUES (9, 9, 'shared', 0, 1, 'real', 1, 1)")
    m = kb_collab.add_membership(conn, kb_id=9, account_id=1, role="moderator", state="active", now=10.0)
    assert m >= ids.MIN_CURRENT_NUMBER
    assert kb_collab.get_membership(conn, 9, 1)["role"] == "moderator"
    with pytest.raises(sqlite3.IntegrityError):
        kb_collab.add_membership(conn, kb_id=9, account_id=1, role="member", state="pending")
    with pytest.raises(ValueError):
        kb_collab.add_membership(conn, kb_id=9, account_id=2, role="owner", state="active")

    c = kb_collab.add_contribution(conn, kb_id=9, document_id="d1", origin="direct", state="pending", contributor_account_id=1, root_kb_id=0, root_document_id="d0")
    assert kb_collab.get_contribution_for_document(conn, 9, "d1")["public_id"] == c
    with pytest.raises(sqlite3.IntegrityError):
        kb_collab.add_contribution(conn, kb_id=9, document_id="d1", origin="direct", state="pending", contributor_account_id=1)
    assert [x["state"] for x in kb_collab.list_contributions(conn, 9)] == ["pending"]

    kb_collab.log_moderation(conn, kb_id=9, actor_account_id=None, action="approve_contribution", target=str(c))
    assert [x["action"] for x in kb_collab.list_moderation_log(conn, 9)] == ["approve_contribution"]


def test_themes_store_and_type_rule() -> None:
    conn = _db()
    conn.execute("INSERT INTO knowledge_bases (id, seq, label, is_personal, is_public, knowledge_type, created_at, updated_at) VALUES (9, 9, 'novel', 0, 1, 'fictional', 1, 1)")
    real = themes_store.create_theme(conn, label="Cities", knowledge_type="real")
    assert real["state"] == "empty" and real["number"] >= ids.MIN_CURRENT_NUMBER
    themes_store.add_theme_member(conn, theme_id=real["id"], kb_id=0, source="computed", score=0.5)  # a personal KB counts as real
    assert themes_store.get_theme(conn, real["id"])["state"] == "active"
    with pytest.raises(ValueError, match="knowledge_type_mismatch"):
        themes_store.add_theme_member(conn, theme_id=real["id"], kb_id=9, source="computed")
    assert [t["label"] for t in themes_store.list_themes(conn, knowledge_type="real")] == ["Cities"]
    assert themes_store.get_theme_by_number(conn, real["number"])["id"] == real["id"]


def test_theme_and_knowledge_base_numbers_never_meet() -> None:
    """Section 5.4: the generator refuses a number that the other table has."""
    conn = _db()
    fixed_ms = 1_800_000_000_000
    salt = dbm.read_id_salt(conn)
    first = ids.timestamp_id("themes", salt=salt, unix_ms=fixed_ms, seq=0)
    conn.execute("UPDATE knowledge_bases SET number=? WHERE id=0", (first,))
    theme = themes_store.create_theme(conn, label="T", knowledge_type="real", now_ms=lambda: fixed_ms)
    assert theme["number"] != first
    assert theme["number"] == ids.timestamp_id("themes", salt=salt, unix_ms=fixed_ms, seq=1)
