"""Schema v48 migration tests: retire CLAIRE_ANONYMOUS_READONLY into personal KB visibility."""

from __future__ import annotations

import logging
import os
from pathlib import Path

import pytest

from claire.store import db as dbm


def _setup_v47_db(db_path: Path) -> None:
    conn = dbm.connect(db_path)
    dbm.init_db(conn)
    conn.execute("DELETE FROM server_settings WHERE key = ?", (dbm._ANONYMOUS_READONLY_V48_KEY,))
    conn.commit()
    conn.close()


def test_v48_migrates_zero_to_private_personal_kb_and_comments_out_dotenv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    monkeypatch.delenv("CLAIRE_ANONYMOUS_READONLY", raising=False)
    db_path = tmp_path / "claire.db"
    _setup_v47_db(db_path)

    conn = dbm.connect(db_path)
    initial_epoch = int(conn.execute("SELECT visibility_epoch FROM knowledge_bases WHERE is_personal = 1").fetchone()[0])
    conn.execute(
        "INSERT INTO knowledge_bases (id, label, is_personal, is_public, created_at, updated_at, visibility_epoch) VALUES (2, 'Shared KB', 0, 1, 1.0, 1.0, 0)"
    )
    conn.commit()

    env_file = tmp_path / ".env"
    env_file.write_text("CLAIRE_ENVIRONMENT=production\nCLAIRE_ANONYMOUS_READONLY=0\n", encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        dbm._migrate_v48_anonymous_readonly(conn, env_files=[env_file])

    personal_row = conn.execute("SELECT is_public, visibility_epoch FROM knowledge_bases WHERE is_personal = 1").fetchone()
    shared_row = conn.execute("SELECT is_public, visibility_epoch FROM knowledge_bases WHERE id = 2").fetchone()
    marker = conn.execute("SELECT value FROM server_settings WHERE key = ?", (dbm._ANONYMOUS_READONLY_V48_KEY,)).fetchone()
    conn.close()

    assert tuple(personal_row) == (0, initial_epoch + 1)
    assert tuple(shared_row) == (1, 0)
    assert marker is not None and marker[0] == "done"
    assert "# CLAIRE_ANONYMOUS_READONLY=0\n" in env_file.read_text(encoding="utf-8")
    assert any("shared knowledge base" in rec.message for rec in caplog.records)


def test_v48_precedence_process_over_dev_over_env_and_idempotency(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CLAIRE_ANONYMOUS_READONLY", raising=False)
    db_path = tmp_path / "claire.db"
    _setup_v47_db(db_path)

    env_file = tmp_path / ".env"
    dev_env_file = tmp_path / ".env.dev"
    env_file.write_text("CLAIRE_ANONYMOUS_READONLY=1\n", encoding="utf-8")
    dev_env_file.write_text("CLAIRE_ANONYMOUS_READONLY=0\n", encoding="utf-8")

    conn = dbm.connect(db_path)
    initial_epoch = int(conn.execute("SELECT visibility_epoch FROM knowledge_bases WHERE is_personal = 1").fetchone()[0])
    dbm._migrate_v48_anonymous_readonly(conn, env_files=[env_file, dev_env_file])
    personal = conn.execute("SELECT is_public, visibility_epoch FROM knowledge_bases WHERE is_personal = 1").fetchone()
    assert tuple(personal) == (0, initial_epoch + 1)
    assert "# CLAIRE_ANONYMOUS_READONLY=1\n" in env_file.read_text(encoding="utf-8")
    assert "# CLAIRE_ANONYMOUS_READONLY=0\n" in dev_env_file.read_text(encoding="utf-8")

    # Admin re-enables public personal KB; running v48 migration again must be a no-op.
    conn.execute("UPDATE knowledge_bases SET is_public = 1 WHERE is_personal = 1")
    conn.commit()
    monkeypatch.setenv("CLAIRE_ANONYMOUS_READONLY", "0")
    dbm._migrate_v48_anonymous_readonly(conn, env_files=[env_file, dev_env_file])
    personal_after = conn.execute("SELECT is_public, visibility_epoch FROM knowledge_bases WHERE is_personal = 1").fetchone()
    conn.close()

    assert tuple(personal_after) == (1, initial_epoch + 1)


def test_v48_one_or_absent_leaves_kbs_public_and_warns_on_readonly_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    monkeypatch.setenv("CLAIRE_ANONYMOUS_READONLY", "1")
    db_path = tmp_path / "claire.db"
    _setup_v47_db(db_path)

    env_file = tmp_path / ".env"
    env_file.write_text("CLAIRE_ANONYMOUS_READONLY=0\n", encoding="utf-8")
    os.chmod(env_file, 0o444)

    conn = dbm.connect(db_path)
    initial_epoch = int(conn.execute("SELECT visibility_epoch FROM knowledge_bases WHERE is_personal = 1").fetchone()[0])
    try:
        with caplog.at_level(logging.WARNING):
            dbm._migrate_v48_anonymous_readonly(conn, env_files=[env_file])
        personal = conn.execute("SELECT is_public, visibility_epoch FROM knowledge_bases WHERE is_personal = 1").fetchone()
        assert tuple(personal) == (1, initial_epoch)
        assert any("remove it from process environment" in rec.message for rec in caplog.records)
        if os.geteuid() != 0:
            assert any("please remove it from" in rec.message for rec in caplog.records)
    finally:
        os.chmod(env_file, 0o644)
        conn.close()
