"""End-to-End (E2E) test for tombstone lifecycle, untombstone recovery, and CLI argument guard safety."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import MagicMock, patch

import pytest

from claire.cli import main
from claire.config import Settings
from claire.ingest.service import IngestService
from claire.store import db as dbm


def test_e2e_tombstone_lifecycle_untombstone_and_cli_guard(tmp_path: Path, monkeypatch):
    """E2E test verifying complete tombstone lifecycle: ingest -> purge with tombstone -> blocked re-ingest -> untombstone -> successful re-ingest -> CLI unknown option rejection."""
    db_file = tmp_path / "claire_e2e.db"
    vault_dir = tmp_path / "vault"
    vault_dir.mkdir(parents=True)

    settings = Settings(
        db_path=str(db_file),
        vault_path=str(vault_dir),
        provider="mock",
        data_lifecycle="purgeable",
        allow_purge=True,
    )
    monkeypatch.setenv("CLAIRE_DB_PATH", str(db_file))
    monkeypatch.setenv("CLAIRE_DATA_LIFECYCLE", "purgeable")
    monkeypatch.setenv("CLAIRE_ALLOW_PURGE", "1")

    from claire.config import get_settings
    get_settings.cache_clear()

    article_url = "https://www.hoyolab.com/article/46898605"
    mock_post_payload = {
        "post": {
            "post_id": "46898605",
            "subject": "Aha's Answer",
            "content": "Official answers and information regarding Aha.",
            "structured_content": json.dumps([{"insert": "Official answers and information regarding Aha.\n"}]),
            "created_at": 1727500000,
        },
        "user": {"nickname": "Honkai: Star Rail"},
        "topics": [{"name": "Honkai: Star Rail"}],
        "image_list": [],
    }

    mock_client = MagicMock()
    mock_client.get_post.return_value = mock_post_payload

    with patch("claire.ingest.fetchers.hoyolab.HoYoLabClient", return_value=mock_client):
        service = IngestService(settings=settings)

        # Step 1: Initial ingest
        rep1 = service.ingest(article_url, source="e2e-test")
        assert rep1.error is None
        assert rep1.document_id is not None
        doc_id = rep1.document_id

        # Step 2: Purge document with tombstone via CLI
        rc_purge = main(["purge", doc_id, "--apply", "--yes"])
        assert rc_purge == 0

        # Verify document is removed from database
        conn = dbm.connect(db_file)
        assert dbm.get_document(conn, doc_id) is None
        # Verify tombstone is recorded
        assert dbm.is_tombstoned(conn, url=article_url) is True
        conn.close()

        # Step 3: Attempt to re-ingest - must be blocked by tombstone
        rep_blocked = service.ingest(article_url, source="e2e-test")
        assert rep_blocked.error is not None
        assert "tombstoned: document was previously purged" in rep_blocked.error

        # Verify raw_inbox recorded the error
        conn = dbm.connect(db_file)
        err_row = conn.execute(
            "SELECT id, status, error FROM raw_inbox WHERE (payload=? OR payload LIKE ?) ORDER BY id DESC LIMIT 1",
            (article_url, f"%{article_url}%"),
        ).fetchone()
        assert err_row is not None
        assert err_row["status"] == "error"
        assert "tombstoned" in err_row["error"]
        conn.close()

        # Step 4: Lift tombstone via untombstone CLI
        rc_untomb = main(["untombstone", article_url])
        assert rc_untomb == 0

        # Verify tombstone is deleted and inbox status reset to pending
        conn = dbm.connect(db_file)
        assert dbm.is_tombstoned(conn, url=article_url) is False
        inbox_row = conn.execute(
            "SELECT id, status, error, attempts FROM raw_inbox WHERE id=?",
            (err_row["id"],),
        ).fetchone()
        assert inbox_row["status"] == "pending"
        assert inbox_row["error"] is None
        assert inbox_row["attempts"] == 0
        conn.close()

        # Step 5: Re-ingest the document - must now succeed
        rep_recovered = service.ingest(article_url, source="e2e-test")
        assert rep_recovered.error is None
        assert rep_recovered.document_id is not None
        new_doc_id = rep_recovered.document_id

        # Step 6: CLI Guard Safety Test - typo option (-no-tombstone) must fail and NOT execute purge
        res = subprocess.run(
            [sys.executable, "-m", "claire.cli", "purge", "-no-tombstone", new_doc_id, "--apply", "--yes"],
            capture_output=True,
            text=True,
            env={"CLAIRE_DB_PATH": str(db_file), "CLAIRE_DATA_LIFECYCLE": "purgeable", "CLAIRE_ALLOW_PURGE": "1"},
        )
        assert res.returncode == 2
        assert "unrecognized arguments: -no-tombstone" in res.stderr

        # Verify document was NOT purged
        conn = dbm.connect(db_file)
        assert dbm.get_document(conn, new_doc_id) is not None
        conn.close()
