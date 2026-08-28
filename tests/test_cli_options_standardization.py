"""Unit tests for CLI Option Orthogonality & Standardization.

Validates that:
1. --apply is the sole execution trigger for dry-run-by-default commands.
2. --dry-run is available for inspection.
3. --force / -f is reserved strictly for overwriting/bypassing existing output skips.
4. -y / --yes is strictly a non-interactive confirmation prompt bypass, not an execution trigger.
5. Deprecated/confusing options (--repair, purge --force) are removed.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from claire import cli
from claire.config import get_settings
from claire.ontology.base import Document
from claire.store import db as dbm
import ops.cb_manuscript as cb


def test_doctor_option_standardization():
    parser = cli.build_parser()

    # Default: heal is False
    args_default = parser.parse_args(["doctor"])
    assert args_default.heal is False

    # --apply sets heal to True
    args_apply = parser.parse_args(["doctor", "--apply"])
    assert args_apply.heal is True

    # --heal sets heal to True
    args_heal = parser.parse_args(["doctor", "--heal"])
    assert args_heal.heal is True

    # -y and --yes
    args_y = parser.parse_args(["doctor", "--apply", "-y"])
    assert args_y.yes is True

    args_yes = parser.parse_args(["doctor", "--apply", "--yes"])
    assert args_yes.yes is True

    # Removed: --repair
    with pytest.raises(SystemExit):
        parser.parse_args(["doctor", "--repair"])


def test_regenerate_option_standardization():
    parser = cli.build_parser()

    # Default: apply is False (dry-run by default)
    args_default = parser.parse_args(["regenerate", "doc_123"])
    assert args_default.apply is False
    assert args_default.force is False

    # --apply
    args_apply = parser.parse_args(["regenerate", "doc_123", "--apply"])
    assert args_apply.apply is True
    assert args_apply.force is False

    # --force / -f
    args_force = parser.parse_args(["regenerate", "doc_123", "--apply", "--force"])
    assert args_force.apply is True
    assert args_force.force is True

    args_force_short = parser.parse_args(["regenerate", "doc_123", "--apply", "-f"])
    assert args_force_short.force is True

    # summary-regenerate alias
    args_sum = parser.parse_args(["summary-regenerate", "doc_123", "--apply", "-f"])
    assert args_sum.apply is True
    assert args_sum.force is True


def test_purge_option_standardization(tmp_path, monkeypatch, capsys):
    get_settings.cache_clear()
    db_file = tmp_path / "claire.db"
    monkeypatch.setenv("CLAIRE_DB_PATH", str(db_file))
    monkeypatch.setenv("CLAIRE_DATA_LIFECYCLE", "purgeable")
    get_settings.cache_clear()

    conn = dbm.connect(db_file)
    dbm.init_db(conn)

    # Insert a sample doc
    doc_id = "doc_test_std_purge"
    doc = Document(
        id=doc_id,
        url="https://example.com/test",
        canonical_url="https://example.com/test",
        title="Test Title",
        raw_text="Sample text",
        content_hash="hash123",
        source_type="web",
    )
    dbm.insert_document(conn, doc)
    conn.commit()
    conn.close()

    parser = cli.build_parser()

    # 1. Removed: --force in purge
    with pytest.raises(SystemExit):
        parser.parse_args(["purge", doc_id, "--force"])

    # 2. Passing -y without --apply: remains in dry-run mode, document is NOT deleted!
    args_y_only = parser.parse_args(["purge", doc_id, "-y"])
    assert args_y_only.apply is False
    assert args_y_only.yes is True
    rc = cli.cmd_purge(args_y_only)
    assert rc == 0
    cap = capsys.readouterr()
    assert "[Dry-Run]" in cap.out

    conn2 = dbm.connect(db_file)
    assert dbm.get_document_row(conn2, doc_id) is not None
    conn2.close()

    # 3. Passing --apply with -y: document is actually purged
    args_apply_y = parser.parse_args(["purge", doc_id, "--apply", "-y"])
    assert args_apply_y.apply is True
    assert args_apply_y.yes is True
    rc = cli.cmd_purge(args_apply_y)
    assert rc == 0
    cap = capsys.readouterr()
    assert "소각된 문서 수 (DB)" in cap.out

    conn3 = dbm.connect(db_file)
    assert dbm.get_document_row(conn3, doc_id) is None
    conn3.close()


def test_recanonicalize_option_standardization():
    parser = cli.build_parser()

    # Default is dry-run
    args_default = parser.parse_args(["recanonicalize"])
    assert args_default.apply is False

    # --apply
    args_apply = parser.parse_args(["recanonicalize", "--apply"])
    assert args_apply.apply is True


def test_dedup_merge_option_standardization():
    parser = cli.build_parser()

    # Default is dry-run
    args_default = parser.parse_args(["dedup-merge"])
    assert args_default.apply is False
    assert args_default.yes is False

    # --apply -y
    args_apply_y = parser.parse_args(["dedup-merge", "--apply", "-y"])
    assert args_apply_y.apply is True
    assert args_apply_y.yes is True


def test_cb_manuscript_backup_restore_flags(tmp_path):
    parser = cb.build_parser()

    # Backup flags: --replace, --force, -f
    args_b1 = parser.parse_args(["backup", "--replace"])
    assert args_b1.replace is True

    args_b2 = parser.parse_args(["backup", "--force"])
    assert args_b2.replace is True

    args_b3 = parser.parse_args(["backup", "-f"])
    assert args_b3.replace is True

    # Restore flags: --yes, -y
    args_r1 = parser.parse_args(["restore", "backup_dir", "--yes"])
    assert args_r1.yes is True

    args_r2 = parser.parse_args(["restore", "backup_dir", "-y"])
    assert args_r2.yes is True
