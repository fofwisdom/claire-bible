"""명시적 DB 마이그레이션과 읽기 전용 liveness CLI."""

from __future__ import annotations

import hashlib
import json

import pytest

from claire import cli
from claire.config import Settings
from claire.store import db as dbm
from claire.store.theme import ThemeManager


def _settings(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAIRE_DB_PATH", str(tmp_path / "m.db"))
    monkeypatch.setenv("CLAIRE_VAULT_PATH", str(tmp_path / "vault"))
    monkeypatch.setenv("CLAIRE_PROVIDER", "mock")
    return Settings()


def _create_retired_support_v12(db_path, *, with_rows=True):
    conn = dbm.connect(db_path)
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
    if with_rows:
        conn.execute(
            "INSERT INTO ingest_attempts"
            "(inbox_id,attempt_number,status,error,recorded_at) "
            "VALUES (?,?,?,?,?)",
            (3287, 1, "error", "blocked by challenge", 1.0),
        )
        conn.execute(
            "INSERT INTO fetch_attempts"
            "(trace_id,inbox_id,stage_order,stage,status,metadata) "
            "VALUES (?,?,?,?,?,?)",
            ("ft_test", 3287, 1, "cdp", "exception", "{}"),
        )
    conn.execute("UPDATE meta SET value='12' WHERE key='schema_version'")
    conn.execute("DELETE FROM meta WHERE key='schema_lineage'")
    conn.commit()
    conn.close()


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
        assert dbm.stored_schema_lineage(conn) == dbm.SCHEMA_LINEAGE
    finally:
        conn.close()


def test_migrate_updates_all_registered_theme_databases(monkeypatch, tmp_path, capsys):
    s = _settings(monkeypatch, tmp_path).model_copy(update={"multi_theme": True})
    conn = dbm.connect(s.db_file)
    dbm.init_db(conn)
    conn.close()
    manager = ThemeManager(s)
    theme = manager.define_theme("추가 테마")
    themed = manager.get_settings_for_theme(theme.id, s)
    for path in (s.db_file, themed.db_file):
        conn = dbm.connect(path)
        conn.execute(
            "UPDATE meta SET value=? WHERE key='schema_version'",
            (str(dbm._RETIRED_SUPPORT_RECOVERY_TARGET),),
        )
        conn.commit()
        conn.close()
    monkeypatch.setattr(cli, "get_settings", lambda: s)

    assert cli.main(["migrate"]) == 0
    output = capsys.readouterr().out
    assert "theme#0" in output
    assert "theme#1" in output
    assert "themes=2 succeeded=2 failed=0" in output
    for path in (s.db_file, themed.db_file):
        conn = dbm.connect(path)
        try:
            assert dbm.stored_schema_version(conn) == dbm.SCHEMA_VERSION
            assert dbm.stored_schema_lineage(conn) == dbm.SCHEMA_LINEAGE
        finally:
            conn.close()


def test_migrate_collects_theme_failures_and_continues(monkeypatch, tmp_path, capsys):
    s = _settings(monkeypatch, tmp_path).model_copy(update={"multi_theme": True})
    conn = dbm.connect(s.db_file)
    dbm.init_db(conn)
    conn.execute(
        "UPDATE meta SET value=? WHERE key='schema_version'",
        (str(dbm._RETIRED_SUPPORT_RECOVERY_TARGET),),
    )
    conn.commit()
    conn.close()
    manager = ThemeManager(s)
    theme = manager.define_theme("미래 스키마")
    themed = manager.get_settings_for_theme(theme.id, s)
    conn = dbm.connect(themed.db_file)
    conn.execute(
        "UPDATE meta SET value=? WHERE key='schema_version'",
        (str(dbm.SCHEMA_VERSION + 1),),
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(cli, "get_settings", lambda: s)

    assert cli.main(["migrate"]) == 1
    captured = capsys.readouterr()
    assert "theme#0" in captured.out
    assert "failed=1" in captured.out
    assert "theme#1" in captured.err
    conn = dbm.connect(s.db_file)
    try:
        assert dbm.stored_schema_version(conn) == dbm.SCHEMA_VERSION
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
    assert report["schema_lineage"] == dbm.SCHEMA_LINEAGE
    assert report["expected_schema_lineage"] == dbm.SCHEMA_LINEAGE
    assert report["databases"][0]["schema_lineage"] == dbm.SCHEMA_LINEAGE


def test_liveness_rejects_stale_schema_without_migrating(
    monkeypatch, tmp_path, capsys
):
    s = _settings(monkeypatch, tmp_path)
    conn = dbm.connect(s.db_file)
    dbm.init_db(conn)
    stale = dbm._RETIRED_SUPPORT_RECOVERY_TARGET
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


def test_migrate_withdraws_local_support_v12_before_v13(
    monkeypatch, tmp_path, capsys
):
    s = _settings(monkeypatch, tmp_path)
    _create_retired_support_v12(s.db_file)
    monkeypatch.setattr(cli, "get_settings", lambda: s)

    assert cli.main(["migrate"]) == 0
    output = capsys.readouterr().out
    assert "schema_version=13 expected=13" in output
    assert "lineage=claire-bible/common" in output

    conn = dbm.connect(s.db_file)
    try:
        assert dbm.stored_schema_version(conn) == 13
        assert dbm.stored_schema_lineage(conn) == dbm.SCHEMA_LINEAGE
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
    ingest_payload = (exports[0] / "ingest_attempts.jsonl").read_bytes()
    fetch_payload = (exports[0] / "fetch_attempts.jsonl").read_bytes()
    assert b"blocked by challenge" in ingest_payload
    assert b'"stage": "cdp"' in fetch_payload
    assert (exports[0].stat().st_mode & 0o777) == 0o700
    manifest_path = exports[0] / "manifest.json"
    assert (manifest_path.stat().st_mode & 0o777) == 0o600
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["from_schema_version"] == 12
    assert manifest["to_schema_version"] == 11
    assert manifest["row_counts"] == {
        "ingest_attempts": 1,
        "fetch_attempts": 1,
    }
    for filename, payload in {
        "ingest_attempts.jsonl": ingest_payload,
        "fetch_attempts.jsonl": fetch_payload,
    }.items():
        assert manifest["files"][filename] == {
            "rows": 1,
            "bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        }
        assert ((exports[0] / filename).stat().st_mode & 0o777) == 0o600

    assert cli.main(["migrate"]) == 0
    assert len(
        list(
            (tmp_path / "raw" / "migrations").glob(
                "support-diagnostics-v12-to-v11-*"
            )
        )
    ) == 1


def test_retired_v12_always_recovers_to_v11(monkeypatch, tmp_path):
    db_path = tmp_path / "retired.db"
    _create_retired_support_v12(db_path, with_rows=False)
    monkeypatch.setattr(dbm, "SCHEMA_VERSION", 14)

    conn = dbm.connect(db_path)
    export_dir = dbm._restore_local_support_v12_to_v11(conn)
    try:
        assert dbm.stored_schema_version(conn) == 11
    finally:
        conn.close()

    manifest = json.loads(
        (export_dir / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["to_schema_version"] == 11


def test_retired_v12_export_failure_preserves_database(
    monkeypatch, tmp_path
):
    db_path = tmp_path / "export-failure.db"
    _create_retired_support_v12(db_path)

    def fail_export(path, payload):
        raise OSError("simulated export failure")

    monkeypatch.setattr(dbm, "_write_durable_export_file", fail_export)
    conn = dbm.connect(db_path)
    with pytest.raises(OSError, match="simulated export failure"):
        dbm.init_db(conn)
    try:
        assert dbm.stored_schema_version(conn) == 12
        assert conn.execute("SELECT count(*) FROM ingest_attempts").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM fetch_attempts").fetchone()[0] == 1
    finally:
        conn.close()
    assert not list(
        (tmp_path / "raw" / "migrations").glob(
            "support-diagnostics-v12-to-v11-*"
        )
    )


def test_retired_v12_unknown_signature_is_not_modified(tmp_path):
    db_path = tmp_path / "unknown-v12.db"
    _create_retired_support_v12(db_path)
    conn = dbm.connect(db_path)
    conn.execute("ALTER TABLE fetch_attempts ADD COLUMN unknown_extension TEXT")
    conn.commit()

    with pytest.raises(RuntimeError, match="does not match the retired"):
        dbm.init_db(conn)
    try:
        assert dbm.stored_schema_version(conn) == 12
        columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(fetch_attempts)")
        }
        assert "unknown_extension" in columns
        assert conn.execute("SELECT count(*) FROM fetch_attempts").fetchone()[0] == 1
    finally:
        conn.close()
    assert not (tmp_path / "raw" / "migrations").exists()


def test_migrate_restores_v12_for_every_active_theme(
    monkeypatch, tmp_path, capsys
):
    s = _settings(monkeypatch, tmp_path).model_copy(update={"multi_theme": True})
    conn = dbm.connect(s.db_file)
    dbm.init_db(conn)
    conn.close()
    manager = ThemeManager(s)
    theme = manager.define_theme("v12 테마")
    themed = manager.get_settings_for_theme(theme.id, s)
    for path in (s.db_file, themed.db_file):
        _create_retired_support_v12(path)
    monkeypatch.setattr(cli, "get_settings", lambda: s)

    assert cli.main(["migrate"]) == 0
    assert "themes=2 succeeded=2 failed=0" in capsys.readouterr().out
    for path in (s.db_file, themed.db_file):
        conn = dbm.connect(path)
        try:
            assert dbm.stored_schema_version(conn) == 13
            assert dbm.stored_schema_lineage(conn) == dbm.SCHEMA_LINEAGE
        finally:
            conn.close()
        exports = list(
            (path.parent / "raw" / "migrations").glob(
                "support-diagnostics-v12-to-v11-*"
            )
        )
        assert len(exports) == 1


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
