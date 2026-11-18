"""Schema v36: the columns of the human verdict in ``same_subject_shadow`` (docs/implementation/SCHEMA_VERSIONING.md "v36", EXPANSION.md Section 3.1)."""

from __future__ import annotations

from pathlib import Path

from claire.store import db as dbm


def _db(tmp_path: Path):
    conn = dbm.connect(tmp_path / "claire.db")
    dbm.init_db(conn)
    return conn


def test_a_v35_database_gets_the_verdict_columns_and_keeps_its_records(tmp_path: Path):
    """A database of v35 (the table without the verdict columns) becomes v36 at the next start. The records stay, with no verdict."""
    conn = _db(tmp_path)
    record = dbm.record_same_subject_shadow(conn, kb_id=0, parent_document_id="p", child_url="https://c.example/", child_document_id=None, same_subject=True, relevance=0.9, quality=0.8)
    for column in ("verdict", "verdict_note", "verdict_at", "verdict_by"):
        conn.execute(f"ALTER TABLE same_subject_shadow DROP COLUMN {column}")
    conn.execute("UPDATE meta SET value='35' WHERE key='schema_version'")
    conn.commit()
    conn.close()

    conn = _db(tmp_path)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(same_subject_shadow)")}
    assert {"verdict", "verdict_note", "verdict_at", "verdict_by"} <= cols
    assert [tuple(r) for r in conn.execute("SELECT id, verdict FROM same_subject_shadow")] == [(record, None)]
    assert int(conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0]) == 36
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
        assert dbm.set_same_subject_verdict(conn, ids[name], verdict, verdict_by=1, note=f"note {name}")
    assert not dbm.set_same_subject_verdict(conn, 9999, "same", verdict_by=1)
    with pytest.raises(ValueError):
        dbm.set_same_subject_verdict(conn, ids["tp"], "maybe", verdict_by=1)
    assert conn.execute("SELECT verdict_by FROM same_subject_shadow WHERE id=?", (ids["tp"],)).fetchone()[0] == 1
    report = dbm.same_subject_shadow_report(conn)
    assert (report["tp"], report["fp"], report["fn"], report["tn"]) == (1, 1, 1, 1)
    assert (report["judged"], report["pending"]) == (4, 1)
    assert report["precision"] == 0.5 and report["recall"] == 0.5 and report["accuracy"] == 0.5
    assert [r["id"] for r in dbm.list_same_subject_shadow(conn, pending=True)] == [ids["open"]]

    # Removing a verdict removes its note and time.
    assert dbm.set_same_subject_verdict(conn, ids["tp"], None, verdict_by=1)
    row = conn.execute("SELECT verdict, verdict_note, verdict_at, verdict_by FROM same_subject_shadow WHERE id=?", (ids["tp"],)).fetchone()
    assert tuple(row) == (None, None, None, None)
    conn.close()


def test_account_scope_of_the_records(tmp_path: Path):
    """The WebUI of an account sees and judges only the records of its own knowledge bases (a knowledge base without an owner belongs to the admin account)."""
    conn = _db(tmp_path)
    conn.commit()
    conn.execute("PRAGMA foreign_keys=OFF")  # the account 5 is not needed for the scope; the default knowledge base 0 belongs to the admin account
    conn.execute("INSERT INTO knowledge_bases(id, seq, label, account_id, created_at, updated_at) VALUES (77, 77, 'member', 5, 1, 1)")
    conn.commit()
    admin_record = dbm.record_same_subject_shadow(conn, kb_id=0, parent_document_id="p", child_url="https://a.example/", child_document_id=None, same_subject=True, relevance=0.9, quality=0.9)
    member_record = dbm.record_same_subject_shadow(conn, kb_id=77, parent_document_id="q", child_url="https://m.example/", child_document_id=None, same_subject=True, relevance=0.9, quality=0.9)
    assert [r["id"] for r in dbm.list_same_subject_shadow(conn, account_id=1)] == [admin_record]
    assert dbm.same_subject_shadow_report(conn, account_id=1)["records"] == 1
    assert not dbm.set_same_subject_verdict(conn, member_record, "same", verdict_by=1, account_id=1)
    assert dbm.set_same_subject_verdict(conn, member_record, "same", verdict_by=5, account_id=5)
    conn.close()
