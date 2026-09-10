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


def test_migrate_restores_local_support_v12_to_v11(
    monkeypatch, tmp_path, capsys
):
    s = _settings(monkeypatch, tmp_path)
    conn = dbm.connect(s.db_file)
    dbm.init_db(conn)
    conn.executescript(
        """
        CREATE TABLE ingest_attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            inbox_id INTEGER NOT NULL,
            attempt_number INTEGER NOT NULL,
            status TEXT NOT NULL,
            document_id TEXT,
            error TEXT,
            recorded_at REAL NOT NULL
        );
        CREATE INDEX idx_ingest_attempts_inbox ON ingest_attempts(inbox_id, id);
        CREATE TABLE fetch_attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trace_id TEXT NOT NULL,
            inbox_id INTEGER NOT NULL,
            document_id TEXT,
            stage_order INTEGER NOT NULL,
            stage TEXT NOT NULL,
            started_at REAL,
            duration_ms INTEGER,
            status TEXT,
            http_status INTEGER,
            input_url TEXT,
            effective_url TEXT,
            content_type TEXT,
            response_bytes INTEGER,
            usable INTEGER,
            guard_error TEXT,
            error_type TEXT,
            error_message TEXT,
            metadata TEXT,
            snapshot_path TEXT,
            snapshot_sha256 TEXT,
            snapshot_original_bytes INTEGER,
            snapshot_stored_bytes INTEGER,
            snapshot_truncated INTEGER DEFAULT 0
        );
        CREATE INDEX idx_fetch_attempts_inbox ON fetch_attempts(inbox_id, id);
        CREATE INDEX idx_fetch_attempts_document ON fetch_attempts(document_id, id);
        """
    )
    conn.execute(
        "INSERT INTO ingest_attempts(inbox_id,attempt_number,status,error,recorded_at) "
        "VALUES (?,?,?,?,?)",
        (3287, 1, "error", "blocked by challenge", 1.0),
    )
    conn.execute(
        "INSERT INTO fetch_attempts(trace_id,inbox_id,stage_order,stage,status,metadata) "
        "VALUES (?,?,?,?,?,?)",
        ("ft_test", 3287, 1, "cdp", "exception", "{}"),
    )
    conn.execute(
        "UPDATE meta SET value='12' WHERE key='schema_version'"
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(cli, "get_settings", lambda: s)

    assert cli.main(["migrate"]) == 0
    assert "schema_version=11 expected=11" in capsys.readouterr().out

    conn = dbm.connect(s.db_file)
    try:
        assert dbm.stored_schema_version(conn) == 11
        tables = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert "ingest_attempts" not in tables
        assert "fetch_attempts" not in tables
    finally:
        conn.close()

    exports = list(
        (tmp_path / "raw" / "migrations").glob(
            "support-diagnostics-v12-to-v11-*"
        )
    )
    assert len(exports) == 1
    assert "blocked by challenge" in (
        exports[0] / "ingest_attempts.jsonl"
    ).read_text(encoding="utf-8")
    assert '"stage": "cdp"' in (
        exports[0] / "fetch_attempts.jsonl"
    ).read_text(encoding="utf-8")
    assert (exports[0].stat().st_mode & 0o777) == 0o700
    assert (
        (exports[0] / "manifest.json").stat().st_mode & 0o777
    ) == 0o600

    assert cli.main(["migrate"]) == 0
    assert len(
        list(
            (tmp_path / "raw" / "migrations").glob(
                "support-diagnostics-v12-to-v11-*"
            )
        )
    ) == 1


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
