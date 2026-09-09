"""Unit tests for isolated provider telemetry and Google policy diagnostics."""

from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from claire.cli import cmd_telemetry
from claire.store.telemetry import (
    connect_telemetry,
    diagnose_google_block,
    evaluate_summary_verdict,
    prune_old_telemetry,
    query_telemetry,
    record_telemetry,
    telemetry_summary_stats,
)


def test_telemetry_schema_and_isolation(tmp_path):
    db_file = tmp_path / "telemetry.db"
    conn = connect_telemetry(db_file)
    try:
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        assert "provider_telemetry" in tables
        # Verify knowledge base tables are NOT present in telemetry.db
        assert "documents" not in tables
        assert "entities" not in tables
        assert "relations" not in tables
    finally:
        conn.close()


def test_record_and_query_telemetry(tmp_path):
    record_telemetry(
        tmp_path,
        document_id="doc_test_1",
        provider="antigravity",
        model="gemini-3.7-flash",
        call_type="extract_json",
        input_chars=1200,
        input_bytes=2400,
        delivery_mode="argv",
        exit_code=0,
        duration_ms=450,
        status="SUCCESS",
        google_block_reason="NONE",
        output_snippet="{'summary': '정상 요약'}",
        summary_verdict="REAL_LLM",
    )

    record_telemetry(
        tmp_path,
        document_id="doc_test_2",
        provider="antigravity",
        model="gemini-3.7-flash",
        call_type="extract_json",
        input_chars=3500,
        input_bytes=7000,
        delivery_mode="stdin",
        exit_code=1,
        duration_ms=300,
        status="CLI_ERROR",
        google_block_reason="RECITATION",
        error_message="finishReason: RECITATION detected in output",
        summary_verdict="RAW_SLICE_200",
    )

    all_records = query_telemetry(tmp_path)
    assert len(all_records) == 2
    assert all_records[0]["document_id"] == "doc_test_2"
    assert all_records[0]["google_block_reason"] == "RECITATION"
    assert all_records[1]["document_id"] == "doc_test_1"
    assert all_records[1]["status"] == "SUCCESS"

    failed_records = query_telemetry(tmp_path, failed_only=True)
    assert len(failed_records) == 1
    assert failed_records[0]["document_id"] == "doc_test_2"

    doc1_records = query_telemetry(tmp_path, document_id="doc_test_1")
    assert len(doc1_records) == 1
    assert doc1_records[0]["duration_ms"] == 450


def test_telemetry_summary_stats(tmp_path):
    record_telemetry(
        tmp_path,
        provider="antigravity",
        call_type="extract_json",
        status="SUCCESS",
        google_block_reason="NONE",
        summary_verdict="REAL_LLM",
    )
    record_telemetry(
        tmp_path,
        provider="antigravity",
        call_type="extract_json",
        status="CLI_ERROR",
        google_block_reason="RATE_LIMIT_429",
        summary_verdict="RAW_SLICE_200",
    )

    stats = telemetry_summary_stats(tmp_path)
    assert stats["total_calls"] == 2
    assert stats["success_calls"] == 1
    assert stats["failed_calls"] == 1
    assert stats["success_rate"] == 0.5
    assert stats["by_status"]["CLI_ERROR"] == 1
    assert stats["by_block_reason"]["RATE_LIMIT_429"] == 1
    assert stats["by_verdict"]["RAW_SLICE_200"] == 1


def test_diagnose_google_block():
    assert diagnose_google_block(0, "", "{'status': 'SUCCESS'}") == "NONE"
    assert diagnose_google_block(1, "Blocked due to RECITATION policy", "") == "RECITATION"
    assert diagnose_google_block(1, "Candidate blocked by SAFETY filters", "") == "SAFETY"
    assert diagnose_google_block(1, "ResourceExhausted: 429 Too Many Requests", "") == "RATE_LIMIT_429"
    assert diagnose_google_block(1, "Daily quota exceeded", "") == "QUOTA_EXCEEDED"
    assert diagnose_google_block(1, "finishReason: MAX_TOKENS while thinking", "") == "MAX_TOKENS"
    assert diagnose_google_block(1, "Invalid JSON schema argument", "") == "INVALID_SCHEMA"
    assert diagnose_google_block(1, "Invocation timed out after 120s", "") == "TIMEOUT"
    assert diagnose_google_block(1, "agy: command not found in PATH", "") == "ENV_MISSING"
    assert diagnose_google_block(1, "Generic process crash", "") == "CLI_ERROR"


def test_evaluate_summary_verdict():
    assert evaluate_summary_verdict("", "Sample raw text") == "EMPTY"
    assert evaluate_summary_verdict(None, "Sample raw text") == "EMPTY"
    assert evaluate_summary_verdict("[mock] Title", "Sample raw text") == "MOCK_PREFIX"
    assert evaluate_summary_verdict("Sample summary", "Sample raw text", provider_name="mock") == "MOCK_PREFIX"

    raw_text = "This is a long academic paper introduction about machine learning and software engineering. " * 3
    sliced_summary = raw_text[:120] + "…"
    assert evaluate_summary_verdict(sliced_summary, raw_text) == "RAW_SLICE_200"

    template_summary = "Tool1, Tool2 등에 관한 자료이다."
    assert evaluate_summary_verdict(template_summary, raw_text) == "TEMPLATE_FALLBACK"

    corrupted_summary = "= Title\n== Section\n[NOTE]\nCorrupted content"
    assert evaluate_summary_verdict(corrupted_summary, raw_text) == "CORRUPTED_ADOC"

    clean_summary = "인공지능 도구의 아키텍처와 최적화 기법을 다룬 연구 논문이다."
    assert evaluate_summary_verdict(clean_summary, raw_text) == "REAL_LLM"


def test_prune_old_telemetry(tmp_path):
    now = time.time()
    # 20 days ago
    record_telemetry(
        tmp_path,
        provider="antigravity",
        call_type="test",
        timestamp=now - (20 * 86400),
    )
    # 5 days ago
    record_telemetry(
        tmp_path,
        provider="antigravity",
        call_type="test",
        timestamp=now - (5 * 86400),
    )

    assert len(query_telemetry(tmp_path)) == 2
    deleted = prune_old_telemetry(tmp_path, retention_days=14)
    assert deleted == 1

    remaining = query_telemetry(tmp_path)
    assert len(remaining) == 1


def test_record_telemetry_fail_safe():
    # Should not raise exception even with invalid or unwriteable directory
    record_telemetry(
        "/proc/nonexistent/read_only_dir",
        provider="antigravity",
        call_type="test",
    )


def test_cli_telemetry_commands(tmp_path, capsys):
    record_telemetry(
        tmp_path,
        document_id="doc_cli_1",
        provider="antigravity",
        call_type="extract_json",
        status="SUCCESS",
        google_block_reason="NONE",
        summary_verdict="REAL_LLM",
    )
    record_telemetry(
        tmp_path,
        document_id="doc_cli_2",
        provider="antigravity",
        call_type="extract_json",
        status="CLI_ERROR",
        google_block_reason="RECITATION",
        summary_verdict="RAW_SLICE_200",
        error_message="Recitation detected",
    )

    with patch("claire.cli.get_settings") as mock_settings:
        mock_settings.return_value = SimpleNamespace(data_dir=tmp_path)

        # 1. Standard table listing
        args = SimpleNamespace(limit=10, failed=False, doc=None, provider=None, stats=False, json=False, prune=None)
        rc = cmd_telemetry(args)
        assert rc == 0
        captured = capsys.readouterr().out
        assert "antigravity" in captured
        assert "doc_cli_1" not in captured or "extract_json" in captured

        # 2. Failed only filter
        args_failed = SimpleNamespace(limit=10, failed=True, doc=None, provider=None, stats=False, json=False, prune=None)
        rc = cmd_telemetry(args_failed)
        assert rc == 0
        captured = capsys.readouterr().out
        assert "RECITATION" in captured
        assert "Recitation detected" in captured

        # 3. Stats report
        args_stats = SimpleNamespace(limit=10, failed=False, doc=None, provider=None, stats=True, json=False, prune=None)
        rc = cmd_telemetry(args_stats)
        assert rc == 0
        captured = capsys.readouterr().out
        assert "프로바이더 텔레메트리 집계 통계" in captured
        assert "총 호출 횟수" in captured

        # 4. Prune command
        args_prune = SimpleNamespace(limit=10, failed=False, doc=None, provider=None, stats=False, json=False, prune=30)
        rc = cmd_telemetry(args_prune)
        assert rc == 0
        captured = capsys.readouterr().out
        assert "삭제 완료" in captured
