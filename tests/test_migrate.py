"""명시적 DB 마이그레이션과 읽기 전용 liveness CLI."""

from __future__ import annotations

import json

from claire import cli
from claire.config import Settings
from claire.store import db as dbm


def _settings(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAIRE_DB_PATH", str(tmp_path / "m.db"))
    monkeypatch.setenv("CLAIRE_VAULT_PATH", str(tmp_path / "vault"))
    monkeypatch.setenv("CLAIRE_PROVIDER", "mock")
    return Settings()


def test_migrate_creates_and_validates_current_schema(monkeypatch, tmp_path, capsys):
    s = _settings(monkeypatch, tmp_path)
    monkeypatch.setattr(cli, "get_settings", lambda: s)

    assert cli.main(["migrate"]) == 0
    assert (
        f"schema_version={dbm.SCHEMA_VERSION} expected={dbm.SCHEMA_VERSION}"
        in capsys.readouterr().out
    )

    conn = dbm.connect(s.db_file)
    try:
        row = conn.execute(
            "SELECT value FROM meta WHERE key='schema_version'"
        ).fetchone()
        assert int(row["value"]) == dbm.SCHEMA_VERSION
    finally:
        conn.close()


def test_liveness_checks_only_database_and_schema(monkeypatch, tmp_path, capsys):
    s = _settings(monkeypatch, tmp_path)
    conn = dbm.connect(s.db_file)
    dbm.init_db(conn)
    conn.execute("INSERT INTO raw_inbox(status,payload) VALUES ('error','a')")
    conn.commit()
    conn.close()
    monkeypatch.setattr(cli, "get_settings", lambda: s)

    assert cli.main(["liveness"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is True
    assert "degraded" not in report
    assert "inbox" not in report
    assert report["schema_version"] == dbm.SCHEMA_VERSION


def test_liveness_rejects_stale_schema_without_migrating(
    monkeypatch, tmp_path, capsys
):
    s = _settings(monkeypatch, tmp_path)
    conn = dbm.connect(s.db_file)
    dbm.init_db(conn)
    stale = dbm.SCHEMA_VERSION - 1
    conn.execute(
        "UPDATE meta SET value=? WHERE key='schema_version'", (str(stale),)
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(cli, "get_settings", lambda: s)

    assert cli.main(["liveness"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is False
    assert "schema_version mismatch" in report["db"]

    conn = dbm.connect(s.db_file)
    try:
        row = conn.execute(
            "SELECT value FROM meta WHERE key='schema_version'"
        ).fetchone()
        assert int(row["value"]) == stale
    finally:
        conn.close()


def test_migrate_rejects_newer_schema_without_rewriting_version(
    monkeypatch, tmp_path, capsys
):
    s = _settings(monkeypatch, tmp_path)
    conn = dbm.connect(s.db_file)
    dbm.init_db(conn)
    newer = dbm.SCHEMA_VERSION + 1
    conn.execute(
        "UPDATE meta SET value=? WHERE key='schema_version'", (str(newer),)
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(cli, "get_settings", lambda: s)

    assert cli.main(["migrate"]) == 1
    assert "database schema is newer than this code" in capsys.readouterr().err

    conn = dbm.connect(s.db_file)
    try:
        assert dbm.stored_schema_version(conn) == newer
    finally:
        conn.close()


def test_liveness_missing_database_is_read_only(
    monkeypatch, tmp_path, capsys
):
    s = _settings(monkeypatch, tmp_path)
    monkeypatch.setattr(cli, "get_settings", lambda: s)

    assert not s.db_file.exists()
    assert cli.main(["liveness"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is False
    assert not s.db_file.exists()


def test_doc_title_cli_updates_title_and_recomputes_minhash(
    monkeypatch, tmp_path, capsys
):
    from claire.ontology.base import Document

    s = _settings(monkeypatch, tmp_path)
    conn = dbm.connect(s.db_file)
    dbm.init_db(conn)
    doc = Document(id="doc-test-1", title="Old Title", raw_text="some test raw content")
    dbm.insert_document(conn, doc)
    conn.close()

    monkeypatch.setattr(cli, "get_settings", lambda: s)

    # 1) Non-existing document
    assert cli.main(["doc-title", "doc-non-existent", "New Title"]) == 1
    assert "문서 없음: doc-non-existent" in capsys.readouterr().out

    # 2) Existing document update
    assert cli.main(["doc-title", "doc-test-1", "New Updated Title"]) == 0
    assert "제목 갱신 완료: doc-test-1 → 'New Updated Title'" in capsys.readouterr().out

    conn = dbm.connect(s.db_file)
    try:
        row = dbm.get_document_row(conn, "doc-test-1")
        assert row["title"] == "New Updated Title"
        assert row["minhash"] is not None
    finally:
        conn.close()
