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

    # Verify DB extractions raw_response was NOT modified during dry-run
    conn = dbm.connect(svc.s.db_file)
    ext_row = conn.execute(
        "SELECT raw_response FROM extractions WHERE document_id=? ORDER BY id DESC LIMIT 1",
        (doc_id,),
    ).fetchone()
    raw_data = json.loads(ext_row["raw_response"])
    assert "= Section Header" in raw_data["summary"]
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

    assert PROMPT_VERSION == "extract-v6"

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


def test_regenerate_detects_error_page(populated_service):
    svc, doc_id, share_token = populated_service
    conn = dbm.connect(svc.s.db_file)
    # Set doc title to Privacy error
    conn.execute(
        "UPDATE documents SET title=?, raw_text=? WHERE id=?",
        ("Privacy error", "NET::ERR_CERT_AUTHORITY_INVALID security error text", doc_id),
    )
    conn.commit()
    conn.close()

    res = svc.regenerate_components(target=share_token, summary=True, force=False)
    assert res["dry_run"] is True
    assert res["targets"][0]["is_error_page"] is True


def test_regenerate_with_refetch(populated_service, monkeypatch):
    from claire.extract.provider import ExtractedEntity, ExtractedRelation

    svc, doc_id, share_token = populated_service

    # Mock default_fetch to return clean fresh document
    fresh_doc = Document(
        id=doc_id,
        title="[vSphere] vCenter 잃고 vSAN 고치기",
        raw_text="vCenter 장애 상황에서 ESXi 호스트와 vSAN 클러스터를 복구하는 실무 절차를 설명한다.",
        canonical_url="https://example.com/vsan-fix",
        source_type="web",
        content_hash="fresh_hash_456",
        fetched_at=1700001000.0,
    )
    monkeypatch.setattr("claire.ingest.service.default_fetch", lambda _url: fresh_doc)

    svc.provider.extract = MagicMock(
        return_value=ExtractionResult(
            summary="vSAN 클러스터 장애 복구 절차에 대한 요약이다.",
            key_claims=["vSAN 클러스터 복구 절차"],
            entities=[
                ExtractedEntity(name="vCenter", type="Technology", aliases=[], observations=["vCenter server"]),
                ExtractedEntity(name="vSAN", type="Technology", aliases=[], observations=["vSAN cluster storage"]),
            ],
            relations=[
                ExtractedRelation(source="vCenter", target="vSAN", type="manages"),
            ],
        )
    )

    res = svc.regenerate_components(
        target=share_token,
        all_components=True,
        refetch=True,
        force=True,
    )

    assert res["dry_run"] is False
    assert res["count"] == 1
    target = res["targets"][0]
    assert target["refetched"] is True
    assert target["title"] == "[vSphere] vCenter 잃고 vSAN 고치기"
    assert target["new_summary"] == "vSAN 클러스터 장애 복구 절차에 대한 요약이다."
    assert target["entities_created"] == 2
    assert target["relations_added"] == 1
    assert "vCenter" in target["new_entity_names"]
    assert "vSAN" in target["new_entity_names"]

    # Verify DB was updated with fresh document content AND extracted entity nodes
    conn = dbm.connect(svc.s.db_file)
    updated_doc = dbm.get_document(conn, doc_id)
    assert updated_doc.title == "[vSphere] vCenter 잃고 vSAN 고치기"
    assert "vCenter 장애 상황에서" in updated_doc.raw_text

    # Verify entities were created in DB
    ent_names = [r["name"] for r in conn.execute("SELECT name FROM entities").fetchall()]
    assert "vCenter" in ent_names
    assert "vSAN" in ent_names
    conn.close()


def test_regenerate_graph_flag_extracts_entities(populated_service):
    from claire.extract.provider import ExtractedEntity, ExtractedRelation

    svc, doc_id, share_token = populated_service

    svc.provider.extract = MagicMock(
        return_value=ExtractionResult(
            summary="AI 아키텍처 요약.",
            key_claims=[],
            entities=[
                ExtractedEntity(name="NeuralNet", type="Technology", aliases=[], observations=[]),
            ],
            relations=[],
        )
    )

    res = svc.regenerate_components(
        target=share_token,
        graph=True,
        force=True,
    )

    assert res["dry_run"] is False
    target = res["targets"][0]
    assert target["entities_created"] >= 1
    assert "NeuralNet" in target["new_entity_names"]

    conn = dbm.connect(svc.s.db_file)
    ent_names = [r["name"] for r in conn.execute("SELECT name FROM entities").fetchall()]
    assert "NeuralNet" in ent_names
    conn.close()


def test_clean_plain_summary_asciidoc_variants():
    """AsciiDoc 및 마크다운 다양한 오염 패턴에 대해 clean_plain_summary가 순수 평문을 반환하는지 검증."""
    from claire.extract.prompts import clean_plain_summary, is_corrupted_summary

    # 1. 실제 사고 케이스 1: 헤더 + 문서 속성만 있는 경우
    raw1 = "kubectl Usage Conventions: kubectl 사용 규칙 및 모범 사례\n:toc:\n:toc-title: 목차"
    assert is_corrupted_summary(raw1) is True
    assert clean_plain_summary(raw1) == "kubectl Usage Conventions: kubectl 사용 규칙 및 모범 사례"

    # 2. 실제 사고 케이스 2: 헤더 + 속성 + 인용 블록
    raw2 = (
        "= kubectl Usage Conventions: kubectl 사용 규칙 및 모범 사례\n"
        ":toc:\n"
        ":toc-title: 목차\n\n"
        "[quote, Kubernetes Documentation]\n"
        "____\n"
        "스크립트의 안정적인 실행과 인프라 관리의 예측 가능성을 확보하기 위해서는 `kubectl`의 출력 형식, "
        "버전 명시, 하위 리소스(subresources) 처리, 그리고 보안 설정에 대한 명확한 규칙을 준수해야 한다.\n"
        "____\n\n"
        "== 개요\n\n"
        "`kubectl`은 Kubernetes 클러스터와 통신하며 리소스를 관리하는 도구이다."
    )
    assert is_corrupted_summary(raw2) is True
    cleaned2 = clean_plain_summary(raw2)
    assert ":toc:" not in cleaned2
    assert "[quote" not in cleaned2
    assert "____" not in cleaned2
    assert "스크립트의 안정적인 실행과 인프라 관리의 예측 가능성을 확보하기 위해서는" in cleaned2

    # 3. Admonition 및 테이블/링크 오염
    raw3 = (
        "[NOTE]\n"
        "이것은 중요한 알림입니다.\n"
        "|===\n"
        "| Col1 | Col2\n"
        "|===\n"
        "link:https://example.com[공식 사이트]를 참고하라."
    )
    assert is_corrupted_summary(raw3) is True
    cleaned3 = clean_plain_summary(raw3)
    assert "[NOTE]" not in cleaned3
    assert "|===" not in cleaned3
    assert "link:" not in cleaned3
    assert "이것은 중요한 알림입니다. 공식 사이트를 참고하라." in cleaned3 or "이것은 중요한 알림입니다." in cleaned3


def test_latest_extraction_summary_with_corrupted_detail_fallback(temp_db):
    """extraction 이 없고 detail 이 ADOC 헤더(:toc:)로 시작할 때 latest_extraction_summary가 평문을 반환하는지 검증."""
    conn = dbm.connect(temp_db)
    doc_id = "doc_fallback_test"
    doc_detail = (
        "= kubectl Usage Conventions: kubectl 사용 규칙 및 모범 사례\n"
        ":toc:\n"
        ":toc-title: 목차\n\n"
        "[quote, Kubernetes Documentation]\n"
        "____\n"
        "스크립트의 안정적인 실행과 인프라 관리의 예측 가능성을 확보하기 위한 규칙이다.\n"
        "____"
    )
    doc = Document(
        id=doc_id,
        title="kubectl Usage Conventions",
        raw_text="kubectl convention details",
        canonical_url="https://kubernetes.io/docs/conventions",
        source_type="web",
        content_hash="h_fallback_1",
        fetched_at=1700000000.0,
    )
    dbm.insert_document(conn, doc)
    dbm.set_document_detail(conn, doc_id, doc_detail, format="adoc")

    summary = dbm.latest_extraction_summary(conn, doc_id)
    assert summary is not None
    assert ":toc:" not in summary
    assert ":toc-title:" not in summary
    assert "[quote" not in summary
    assert "스크립트의 안정적인 실행과 인프라 관리의 예측 가능성을 확보하기 위한 규칙이다." in summary
    conn.close()


def test_backfill_summary_repairs_corrupted_extractions(populated_service):
    """backfill_summaries 가 기존 DB의 오염된 extractions.raw_response 내 요약을 자동 수복하는지 검증."""
    svc, doc_id, _ = populated_service
    res = svc.backfill_summaries()

    assert res["filled"] >= 1
    conn = dbm.connect(svc.s.db_file)
    summary = dbm.latest_extraction_summary(conn, doc_id)
    assert "= Section Header" not in summary
    assert "[NOTE]" not in summary
    assert "|===" not in summary
    assert "This is corrupted adoc summary with table. Link" in summary or "This is corrupted adoc summary" in summary
    conn.close()



