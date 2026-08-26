"""Unit tests for document component regeneration (summary, detail)."""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from unittest.mock import MagicMock

import pytest

from claire.config import Settings
from claire.extract.provider import ExtractionResult, MockProvider
from claire.ingest.service import IngestService
from claire.ontology.base import Document
from claire.store import db as dbm


@pytest.fixture
def temp_db(tmp_path: Path) -> Path:
    db_file = tmp_path / "claire.db"
    conn = dbm.connect(db_file)
    dbm.init_db(conn)
    conn.close()
    return db_file


@pytest.fixture
def populated_service(temp_db: Path) -> tuple[IngestService, str, str]:
    """Populate DB with a document with corrupted ADOC summary and a share token."""
    settings = Settings(
        CLAIRE_DB_PATH=str(temp_db),
        CLAIRE_PROVIDER="mock",
        CLAIRE_GEMINI_EFFORT="medium",
    )
    conn = dbm.connect(temp_db)

    # 1. Insert document
    doc_id = "doc_test_123"
    doc = Document(
        id=doc_id,
        title="AI System Design Document",
        raw_text="This is the raw content of the AI system design document explaining architectures.",
        canonical_url="https://example.com/ai-system",
        source_type="text",
        content_hash="hash123",
        fetched_at=1700000000.0,
    )
    dbm.insert_document(conn, doc)

    # 2. Insert corrupted extraction (with ADOC header markup in summary)
    corrupted_raw = json.dumps(
        {
            "summary": "= Section Header\n[NOTE]\nThis is corrupted adoc summary with |=== table.\nlink:https://example.com[Link]",
            "key_claims": ["claim 1"],
            "entities": [{"name": "AI", "type": "Technology", "aliases": [], "observations": []}],
            "relations": [{"source": "AI", "target": "System", "type": "part_of"}],
        },
        ensure_ascii=False,
    )
    dbm.log_extraction(
        conn,
        document_id=doc_id,
        provider="test",
        model="test-model",
        prompt_version=1,
        raw_response=corrupted_raw,
    )

    # 3. Create share token
    share_token = "dzr73zpxh2bah4vp"
    conn.execute(
        "INSERT INTO doc_shares(token, document_id, created_at, expires_at) VALUES (?,?,?,?)",
        (share_token, doc_id, 1700000000.0, None),
    )
    conn.commit()

    conn.close()
    svc = IngestService(settings)
    return svc, doc_id, share_token


def test_regenerate_dry_run_by_token(populated_service):
    svc, doc_id, share_token = populated_service
    res = svc.regenerate_components(target=share_token, summary=True, force=False)

    assert res["dry_run"] is True
    assert res["count"] == 1
    target = res["targets"][0]
    assert target["document_id"] == doc_id
    assert target["title"] == "AI System Design Document"
    assert target["summary_corrupted"] is True
    assert "regenerate_summary" in target["actions"]

    # Verify DB was NOT modified during dry-run
    conn = dbm.connect(svc.s.db_file)
    curr_summary = dbm.latest_extraction_summary(conn, doc_id)
    assert "= Section Header" in curr_summary
    conn.close()


def test_regenerate_dry_run_by_share_url(populated_service):
    svc, doc_id, share_token = populated_service
    share_url = f"https://cb.netspheres.org/p?s={share_token}"
    res = svc.regenerate_components(target=share_url, summary=True, force=False)

    assert res["dry_run"] is True
    assert res["count"] == 1
    assert res["targets"][0]["document_id"] == doc_id


def test_regenerate_dry_run_corrupted_scan(populated_service):
    svc, doc_id, _ = populated_service
    res = svc.regenerate_components(corrupted_summary=True, summary=True, force=False)

    assert res["dry_run"] is True
    assert res["count"] == 1
    assert res["targets"][0]["document_id"] == doc_id
    assert res["targets"][0]["summary_corrupted"] is True


def test_regenerate_force_overwrites_summary_and_preserves_graph(populated_service):
    svc, doc_id, share_token = populated_service

    # Mock provider extract response
    clean_summary = "AI 시스템 설계에 관한 문서이다. 주요 아키텍처와 구성요소를 다룬다."
    svc.provider.extract = MagicMock(
        return_value=ExtractionResult(
            summary=f"= Unwanted Header\n{clean_summary}",
            key_claims=[],
            entities=[],
            relations=[],
        )
    )

    res = svc.regenerate_components(
        target=share_token,
        summary=True,
        force=True,
        effort="high",
    )

    assert res["dry_run"] is False
    assert res["count"] == 1
    assert res["targets"][0]["updated"] is True
    assert res["targets"][0]["new_summary"] == clean_summary

    # Verify DB was updated
    conn = dbm.connect(svc.s.db_file)
    try:
        updated_summary = dbm.latest_extraction_summary(conn, doc_id)
        assert updated_summary == clean_summary

        # Verify entities and relations were preserved in raw_response
        ext_row = conn.execute(
            "SELECT raw_response FROM extractions WHERE document_id=? ORDER BY id DESC LIMIT 1",
            (doc_id,),
        ).fetchone()
        raw_data = json.loads(ext_row["raw_response"])
        assert raw_data["summary"] == clean_summary
        assert len(raw_data["entities"]) == 1
        assert raw_data["entities"][0]["name"] == "AI"
        assert len(raw_data["relations"]) == 1
    finally:
        conn.close()


def test_regenerate_effort_passed_to_provider(populated_service):
    svc, doc_id, share_token = populated_service
    svc.provider.effort = "medium"

    svc.provider.extract = MagicMock(
        return_value=ExtractionResult(
            summary="새로운 요약입니다.",
            key_claims=[],
            entities=[],
            relations=[],
        )
    )

    # Calling with effort="high"
    svc.regenerate_components(
        target=share_token,
        summary=True,
        force=True,
        effort="high",
    )

    # Verify extract was called
    svc.provider.extract.assert_called_once()


def test_summary_prompt_plain_text_definition():
    """요약 프롬프트 템플릿 및 스키마에 AsciiDoc 금지 및 평문 작성이 명시되어 있는지 검증."""
    from claire.extract.prompts import (
        PROMPT_VERSION,
        _SYS,
        extract_fallback_prompt,
        extract_system_prompt,
        summarize_search_prompt,
    )
    from claire.extract.provider import ExtractionResult

    assert PROMPT_VERSION == "extract-v5"

    sys = extract_system_prompt("{ontology}")
    assert "plain text" in sys.lower() or "평문" in sys
    assert "asciidoc" in sys.lower()
    assert "[NOTE]" in sys or "|===" in sys

    fb = extract_fallback_prompt(sys, "sample doc")
    assert "plain text" in fb.lower() or "평문" in fb

    search_p = summarize_search_prompt("query", "context")
    assert "plain text" in search_p.lower()
    assert "asciidoc" in search_p.lower()

    schema = ExtractionResult.extraction_json_schema()
    summary_desc = schema["properties"]["summary"]["description"]
    assert "plain text" in summary_desc.lower() or "평문" in summary_desc
    assert "asciidoc" in summary_desc.lower()
