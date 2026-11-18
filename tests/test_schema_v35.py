"""Schema v35: the source names of link expansion and the table of shadow records (docs/implementation/SCHEMA_VERSIONING.md "v35", GLOSSARY.md)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from claire.store import db as dbm
from claire.store import telemetry


def _db(tmp_path: Path):
    conn = dbm.connect(tmp_path / "claire.db")
    dbm.init_db(conn)
    return conn


def test_old_source_names_get_the_new_names_at_start(tmp_path: Path):
    conn = _db(tmp_path)
    old = dbm.log_inbox(conn, source="onehop:doc_parent1", payload="https://a.example/x", kind="url")
    proposal = dbm.log_inbox(conn, source="telegram-expand", payload="https://b.example/y", kind="url")
    other = dbm.log_inbox(conn, source="cli", payload="https://c.example/z", kind="url")
    conn.close()

    conn = _db(tmp_path)  # a new start runs the step again (idempotent)
    sources = {r["id"]: r["source"] for r in conn.execute("SELECT id, source FROM raw_inbox")}
    assert sources == {old: "expand:doc_parent1", proposal: "expand-proposal", other: "cli"}
    assert int(conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0]) == 35
    conn.close()


def test_shadow_records_follow_the_documents(tmp_path: Path):
    conn = _db(tmp_path)
    for doc_id in ("doc_p", "doc_c", "doc_keep"):
        conn.execute("INSERT INTO documents(id, kb_id, title, content_hash) VALUES (?, 0, ?, ?)", (doc_id, doc_id, doc_id))
    conn.commit()
    first = dbm.record_same_subject_shadow(conn, kb_id=0, parent_document_id="doc_p", child_url="https://c.example/", child_document_id="doc_c", same_subject=True, relevance=0.9, quality=0.8)
    dbm.record_same_subject_shadow(conn, kb_id=0, parent_document_id="doc_keep", child_url="https://d.example/", child_document_id=None, same_subject=False, relevance=0.8, quality=0.7)
    rows = dbm.list_same_subject_shadow(conn)
    assert [r["child_url"] for r in rows] == ["https://d.example/", "https://c.example/"]  # the newest first
    assert dbm.list_same_subject_shadow(conn, kb_id=0, limit=1)[0]["same_subject"] == 0

    # A purge of the child removes its record. The other record stays.
    dbm.purge_document_cascade(conn, tmp_path, ["doc_c"], heal=False, vacuum=False)
    left = [r["id"] for r in dbm.list_same_subject_shadow(conn)]
    assert first not in left and len(left) == 1
    conn.close()


def test_telemetry_rows_get_the_gate_call_type(tmp_path: Path):
    path = tmp_path / "telemetry.db"
    conn = telemetry.connect_telemetry(path)
    conn.execute("INSERT INTO provider_telemetry(timestamp, provider, call_type, status) VALUES (1, 'antigravity', 'judge_research', 'ok')")
    conn.commit()
    conn.close()
    conn = telemetry.connect_telemetry(path)
    assert [r[0] for r in conn.execute("SELECT call_type FROM provider_telemetry")] == ["judge_gate"]
    conn.close()


def test_verdict_columns_are_added_to_a_table_of_an_earlier_v35_build(tmp_path: Path):
    conn = dbm.connect(tmp_path / "claire.db")
    conn.execute("CREATE TABLE same_subject_shadow (id INTEGER PRIMARY KEY AUTOINCREMENT, kb_id INTEGER NOT NULL DEFAULT 0, parent_document_id TEXT NOT NULL, child_url TEXT NOT NULL, child_document_id TEXT, same_subject INTEGER NOT NULL, relevance REAL, quality REAL, interpretation TEXT, reason TEXT, created_at REAL NOT NULL)")
    conn.execute("INSERT INTO same_subject_shadow(parent_document_id, child_url, same_subject, created_at) VALUES ('p', 'https://c.example/', 1, 1)")
    conn.commit()
    dbm.init_db(conn)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(same_subject_shadow)")}
    assert {"verdict", "verdict_note", "verdict_at"} <= cols
    assert conn.execute("SELECT COUNT(*) FROM same_subject_shadow WHERE verdict IS NULL").fetchone()[0] == 1
    conn.close()


def test_verdict_and_report(tmp_path: Path):
    import pytest

    conn = _db(tmp_path)
    ids = {}
    for name, model_same in (("tp", True), ("fp", True), ("fn", False), ("tn", False), ("open", True)):
        ids[name] = dbm.record_same_subject_shadow(conn, kb_id=0, parent_document_id="p", child_url=f"https://{name}.example/", child_document_id=None, same_subject=model_same, relevance=0.9, quality=0.9)
    empty = dbm.same_subject_shadow_report(conn)
    assert empty["records"] == 5 and empty["judged"] == 0
    assert empty["precision"] is None and empty["recall"] is None and empty["accuracy"] is None  # no data, no estimate

    for name, verdict in (("tp", "same"), ("fp", "different"), ("fn", "same"), ("tn", "different")):
        assert dbm.set_same_subject_verdict(conn, ids[name], verdict, note=f"note {name}")
    assert not dbm.set_same_subject_verdict(conn, 9999, "same")
    with pytest.raises(ValueError):
        dbm.set_same_subject_verdict(conn, ids["tp"], "maybe")
    report = dbm.same_subject_shadow_report(conn)
    assert (report["tp"], report["fp"], report["fn"], report["tn"]) == (1, 1, 1, 1)
    assert (report["judged"], report["pending"]) == (4, 1)
    assert report["precision"] == 0.5 and report["recall"] == 0.5 and report["accuracy"] == 0.5
    assert [r["id"] for r in dbm.list_same_subject_shadow(conn, pending=True)] == [ids["open"]]

    # Removing a verdict removes its note and time.
    assert dbm.set_same_subject_verdict(conn, ids["tp"], None)
    row = conn.execute("SELECT verdict, verdict_note, verdict_at FROM same_subject_shadow WHERE id=?", (ids["tp"],)).fetchone()
    assert tuple(row) == (None, None, None)
    conn.close()
