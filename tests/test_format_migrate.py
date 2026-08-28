"""Tests for claire format-migrate CLI subcommand."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from claire.cli import cmd_format_migrate
from claire.config import Settings, get_settings
from claire.ontology.base import Document
from claire.store import db as dbm


@pytest.fixture
def temp_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db_file = tmp_path / "claire.db"
    conn = dbm.connect(db_file)
    dbm.init_db(conn)
    conn.close()
    monkeypatch.setenv("CLAIRE_DB_PATH", str(db_file))
    monkeypatch.setenv("CLAIRE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CLAIRE_RENDER_FORMAT", "adoc")
    monkeypatch.setenv("CLAIRE_PROVIDER", "mock")
    get_settings.cache_clear()
    return db_file


def test_format_migrate_empty_db_dry_run(temp_db: Path, capsys: pytest.CaptureFixture[str]):
    args = SimpleNamespace(format=None, apply=False, dry_run=False, yes=False, json=False)
    code = cmd_format_migrate(args)
    assert code == 0
    out = capsys.readouterr().out
    assert "포맷 마이그레이션 진단 현황" in out
    assert "전체 문서 수             : 0 건" in out


def test_format_migrate_json_output(temp_db: Path, capsys: pytest.CaptureFixture[str]):
    conn = dbm.connect(temp_db)
    doc1 = Document(id="doc1", title="Doc 1", raw_text="text", source_type="text")
    doc2 = Document(id="doc2", title="Doc 2", raw_text="text", source_type="text")
    dbm.insert_document(conn, doc1)
    dbm.insert_document(conn, doc2)
    dbm.set_document_detail(conn, "doc1", "= Title", format="adoc")
    dbm.set_document_detail(conn, "doc2", "# Title", format="md")
    conn.close()

    args = SimpleNamespace(format=None, apply=False, dry_run=False, yes=False, json=True)
    code = cmd_format_migrate(args)
    assert code == 0
    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["target_format"] == "adoc"
    assert data["total_docs"] == 2
    assert data["matching_docs"] == 1
    assert data["mismatched_docs"] == 1
    assert data["target_docs"] == 1


def test_format_migrate_apply_no_targets(temp_db: Path, capsys: pytest.CaptureFixture[str]):
    conn = dbm.connect(temp_db)
    doc1 = Document(id="doc1", title="Doc 1", raw_text="text", source_type="text")
    dbm.insert_document(conn, doc1)
    dbm.set_document_detail(conn, "doc1", "= Title", format="adoc")
    conn.close()

    args = SimpleNamespace(format=None, apply=True, dry_run=False, yes=False, json=False)
    code = cmd_format_migrate(args)
    assert code == 0
    out = capsys.readouterr().out
    assert "마이그레이션이 필요하지 않습니다" in out


def test_format_migrate_apply_non_tty_requires_yes(temp_db: Path, capsys: pytest.CaptureFixture[str]):
    conn = dbm.connect(temp_db)
    doc1 = Document(id="doc1", title="Doc 1", raw_text="text", source_type="text")
    dbm.insert_document(conn, doc1)
    dbm.set_document_detail(conn, "doc1", "# Title", format="md")
    conn.close()

    args = SimpleNamespace(format=None, apply=True, dry_run=False, yes=False, json=False)
    with patch("sys.stdin.isatty", return_value=False):
        code = cmd_format_migrate(args)
        assert code == 2
        out = capsys.readouterr().out
        assert "--yes" in out


def test_format_migrate_apply_interactive_abort(temp_db: Path, capsys: pytest.CaptureFixture[str]):
    conn = dbm.connect(temp_db)
    doc1 = Document(id="doc1", title="Doc 1", raw_text="text", source_type="text")
    dbm.insert_document(conn, doc1)
    dbm.set_document_detail(conn, "doc1", "# Title", format="md")
    conn.close()

    args = SimpleNamespace(format=None, apply=True, dry_run=False, yes=False, json=False)
    with patch("sys.stdin.isatty", return_value=True), patch("builtins.input", return_value="n"):
        code = cmd_format_migrate(args)
        assert code == 0
        out = capsys.readouterr().out
        assert "취소되었습니다" in out


def test_format_migrate_apply_with_yes(temp_db: Path, capsys: pytest.CaptureFixture[str]):
    conn = dbm.connect(temp_db)
    doc1 = Document(id="doc1", title="Doc 1", raw_text="Sample text", source_type="text")
    dbm.insert_document(conn, doc1)
    dbm.set_document_detail(conn, "doc1", "# Title", format="md")
    conn.close()

    args = SimpleNamespace(format="adoc", apply=True, dry_run=False, yes=True, json=False)
    code = cmd_format_migrate(args)
    assert code == 0
    out = capsys.readouterr().out
    assert "포맷 마이그레이션 완료" in out

    conn = dbm.connect(temp_db)
    fmt = dbm.get_document_detail_format(conn, "doc1")
    conn.close()
    assert fmt == "adoc"
