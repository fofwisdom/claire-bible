"""claire status 집계 헬퍼 검증."""

from __future__ import annotations

import sqlite3

from claire.ontology.base import Entity, Relation, Document
from claire.store import db as dbm


def _db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    dbm.init_db(conn)
    return conn


def test_inbox_status_counts():
    conn = _db()
    i1 = dbm.log_inbox(conn, source="t", payload="a", kind="url")
    dbm.update_inbox(conn, i1, status="done")
    i2 = dbm.log_inbox(conn, source="t", payload="b", kind="url")
    dbm.update_inbox(conn, i2, status="error", error="x")
    dbm.log_inbox(conn, source="t", payload="c", kind="text")  # received
    counts = dbm.inbox_status_counts(conn)
    assert counts == {"done": 1, "error": 1, "received": 1}


def test_entity_and_source_type_counts():
    conn = _db()
    for t in ("Tool", "Tool", "Framework"):
        dbm.upsert_entity(conn, Entity(type=t, name=f"{t}-{id(object())}"))
    dbm.insert_document(conn, Document(source_type="web", content_hash="h1", raw_text="x"))
    dbm.insert_document(conn, Document(source_type="pdf", content_hash="h2", raw_text="y"))
    et = dict(dbm.entity_type_counts(conn))
    assert et["Tool"] == 2 and et["Framework"] == 1
    st = dict(dbm.source_type_counts(conn))
    assert st["web"] == 1 and st["pdf"] == 1


def test_most_merged_and_top_connected():
    conn = _db()
    a = Entity(type="Tool", name="Claude Code", sources=["d1", "d2", "d3"])
    b = Entity(type="Org", name="Anthropic", sources=["d1"])
    dbm.upsert_entity(conn, a)
    dbm.upsert_entity(conn, b)
    dbm.upsert_relation(conn, Relation(type="authored_by", source_id=a.id, target_id=b.id))

    merged = dbm.most_merged_entities(conn)
    assert merged and merged[0][0] == "Claude Code" and merged[0][2] == 3
    # Anthropic 은 sources 1개라 수렴 목록에서 제외
    assert all(name != "Anthropic" for name, _, _ in merged)

    top = dbm.top_connected_entities(conn)
    degs = {name: deg for name, _, deg in top}
    assert degs["Claude Code"] == 1 and degs["Anthropic"] == 1


def test_last_inbox_activity():
    conn = _db()
    assert dbm.last_inbox_activity(conn) is None
    dbm.log_inbox(conn, source="t", payload="a", kind="url")
    assert dbm.last_inbox_activity(conn) is not None
