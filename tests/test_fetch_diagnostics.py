"""수집 단계 trace와 정제 DOM 스냅샷 보존."""

from __future__ import annotations

import json
from pathlib import Path

import zstandard

from claire.ingest.fetch_diagnostics import (
    annotate_fetch_stage,
    capture_fetch_html,
    fetch_trace,
    record_fetch_stage,
    sanitize_html_snapshot,
)
from claire.store import db as dbm


def test_sanitize_html_snapshot_preserves_structure_and_redacts_secrets():
    raw = """
    <html><head>
      <meta name="csrf-token" content="csrf-secret">
      <script>window.secret = 'embedded-secret'</script>
    </head><body>
      <form action="https://example.com/post?token=query-secret">
        <input name="password" value="form-secret">
        <textarea>private text</textarea>
      </form>
    </body></html>
    """
    sanitized = sanitize_html_snapshot(raw)
    assert "<form" in sanitized
    assert "csrf-secret" not in sanitized
    assert "embedded-secret" not in sanitized
    assert "query-secret" not in sanitized
    assert "form-secret" not in sanitized
    assert "private text" not in sanitized
    assert "***REDACTED***" in sanitized


def test_fetch_trace_persists_stage_and_compressed_snapshot(tmp_path: Path):
    db_file = tmp_path / "claire.db"
    conn = dbm.connect(db_file)
    dbm.init_db(conn)
    inbox_id = dbm.log_inbox(
        conn,
        source="test",
        payload="https://example.com/entry/3287",
        kind="url",
    )

    with fetch_trace(tmp_path, inbox_id) as trace:
        record_fetch_stage(
            "static",
            input_url="https://example.com/entry/3287",
            status="response",
            http_status=200,
            content_type="text/html",
            metadata={"redirect_count": 0},
        )
        capture_fetch_html(
            "static",
            '<html><body><input value="secret"><article>본문</article></body></html>',
        )
        annotate_fetch_stage("static", usable=True)
    trace.persist(conn, status="fetched", document_id="doc_trace")

    rows = dbm.fetch_attempts_for_inbox(conn, [inbox_id])
    assert len(rows) == 1
    row = dict(rows[0])
    assert row["trace_id"].startswith("ft_")
    assert row["stage"] == "static"
    assert row["http_status"] == 200
    assert row["usable"] == 1
    assert json.loads(row["metadata"])["redirect_count"] == 0

    snapshot = tmp_path / row["snapshot_path"]
    assert snapshot.is_file()
    html = zstandard.ZstdDecompressor().decompress(snapshot.read_bytes()).decode()
    assert "본문" in html
    assert "secret" not in html
    assert "***REDACTED***" in html
    conn.close()


def test_ingest_attempt_ledger_keeps_status_transitions(tmp_path: Path):
    conn = dbm.connect(tmp_path / "claire.db")
    dbm.init_db(conn)
    inbox_id = dbm.log_inbox(
        conn,
        source="test",
        payload="https://example.com/fail",
        kind="url",
    )
    dbm.update_inbox(conn, inbox_id, status="error", error="first failure")
    dbm.update_inbox(conn, inbox_id, status="done", document_id="doc_done")

    attempts = [dict(row) for row in dbm.ingest_attempts_for_inbox(conn, [inbox_id])]
    assert [row["status"] for row in attempts] == ["received", "error", "done"]
    assert [row["attempt_number"] for row in attempts] == [0, 1, 2]
    assert attempts[1]["error"] == "first failure"
    assert attempts[2]["document_id"] == "doc_done"
    conn.close()
