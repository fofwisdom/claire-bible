"""Unit tests for table-only batch re-extraction and regeneration."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from claire.config import Settings
from claire.extract.provider import ExtractedEntity, ExtractedRelation, ExtractionResult
from claire.extract.table_budget import count_tables, has_tables
from claire.ingest.service import IngestService
from claire.ontology.base import Document
from claire.store import db as dbm


def test_has_tables_and_count_tables():
    md_table = (
        "Some introduction.\n\n"
        "| Name | Version | Status |\n"
        "|---|---|---|\n"
        "| Alpha | 1.0 | Stable |\n"
        "| Beta | 2.0 | Beta |\n\n"
        "Some conclusion."
    )
    assert has_tables(md_table) is True
    assert count_tables(md_table) == 1

    adoc_table = (
        "= Document Title\n\n"
        "[cols=\"1,1\"]\n"
        "|===\n"
        "| Col1 | Col2\n"
        "| Val1 | Val2\n"
        "|===\n"
    )
    assert has_tables(adoc_table) is True
    assert count_tables(adoc_table) == 1

    html_table = "<p>Text</p><table><tr><th>H</th></tr><tr><td>V</td></tr></table><p>End</p>"
    assert has_tables(html_table) is True
    assert count_tables(html_table) == 1

    no_table = "Just plain prose text without any table markdown or asciidoc."
    assert has_tables(no_table) is False
    assert count_tables(no_table) == 0


@pytest.fixture
def table_test_env(tmp_path: Path) -> tuple[IngestService, str, str]:
    db_file = tmp_path / "claire.db"
    conn = dbm.connect(db_file)
    dbm.init_db(conn)

    # Doc 1: Has table in raw_text
    doc_table = Document(
        id="doc_with_table",
        title="Benchmark Report with Tables",
        raw_text=(
            "Detailed benchmark analysis.\n\n"
            "| Model | Score | Latency |\n"
            "| --- | --- | --- |\n"
            "| Model-X | 98.5 | 110ms |\n"
            "| Model-Y | 94.2 | 140ms |\n\n"
            "Summary remarks."
        ),
        canonical_url="https://example.com/benchmark",
        source_type="web",
        content_hash="hash_table_1",
        fetched_at=1700000000.0,
    )
    dbm.insert_document(conn, doc_table)
    dbm.log_extraction(
        conn,
        document_id="doc_with_table",
        provider="test",
        model="test-model",
        prompt_version=1,
        raw_response=json.dumps(
            {
                "summary": "벤치마크 보고서 요약.",
                "key_claims": [],
                "entities": [{"name": "Model-X", "type": "Technology", "aliases": [], "observations": []}],
                "relations": [],
            },
            ensure_ascii=False,
        ),
    )

    # Doc 2: Plain text without table
    doc_plain = Document(
        id="doc_plain_text",
        title="Plain Article without Tables",
        raw_text="This is a simple blog post without any tables or structured matrices.",
        canonical_url="https://example.com/plain",
        source_type="text",
        content_hash="hash_plain_2",
        fetched_at=1700000100.0,
    )
    dbm.insert_document(conn, doc_plain)
    dbm.log_extraction(
        conn,
        document_id="doc_plain_text",
        provider="test",
        model="test-model",
        prompt_version=1,
        raw_response=json.dumps(
            {
                "summary": "일반 기사 요약.",
                "key_claims": [],
                "entities": [{"name": "Article", "type": "Topic", "aliases": [], "observations": []}],
                "relations": [],
            },
            ensure_ascii=False,
        ),
    )

    conn.close()

    settings = Settings(
        CLAIRE_DB_PATH=str(db_file),
        CLAIRE_PROVIDER="mock",
    )
    svc = IngestService(settings)
    return svc, "doc_with_table", "doc_plain_text"


def test_documents_with_tables_db_query(table_test_env):
    svc, doc_table_id, doc_plain_id = table_test_env
    conn = dbm.connect(svc.s.db_file)
    try:
        results = dbm.documents_with_tables(conn)
        assert len(results) == 1
        assert results[0]["id"] == doc_table_id
        assert results[0]["total_tables"] >= 1
        assert "Model-X" in results[0]["table_preview"]
    finally:
        conn.close()


def test_regenerate_tables_dry_run(table_test_env):
    svc, doc_table_id, _ = table_test_env
    res = svc.regenerate_components(tables=True, summary=True, force=False)

    assert res["dry_run"] is True
    assert res["count"] == 1
    target = res["targets"][0]
    assert target["document_id"] == doc_table_id
    assert target["total_tables"] >= 1
    assert "Model-X" in target["table_preview"]


def test_regenerate_tables_force_executes(table_test_env):
    svc, doc_table_id, _ = table_test_env

    svc.provider.extract = MagicMock(
        return_value=ExtractionResult(
            summary="표 기반 벤치마크 점수가 반영된 새로운 요약.",
            key_claims=["Model-X 성능 1위"],
            entities=[
                ExtractedEntity(name="Model-X", type="Technology", aliases=[], observations=["98.5 score"]),
                ExtractedEntity(name="Model-Y", type="Technology", aliases=[], observations=["94.2 score"]),
            ],
            relations=[
                ExtractedRelation(source="Model-X", target="Model-Y", type="outperforms"),
            ],
        )
    )

    res = svc.regenerate_components(
        tables=True,
        all_components=True,
        force=True,
    )

    assert res["dry_run"] is False
    assert res["count"] == 1
    target = res["targets"][0]
    assert target["document_id"] == doc_table_id
    assert target["new_summary"] == "표 기반 벤치마크 점수가 반영된 새로운 요약."
    assert target["entities_created"] >= 1


def test_backfill_details_tables_only(table_test_env):
    svc, doc_table_id, doc_plain_id = table_test_env
    svc.provider.render_detail = MagicMock(return_value="## 벤치마크 표 렌더링 본문")

    res = svc.backfill_details(tables_only=True)
    assert res["docs"] == 1
    assert res["ok"] == 1

    conn = dbm.connect(svc.s.db_file)
    detail_table = dbm.get_document_detail(conn, doc_table_id)
    detail_plain = dbm.get_document_detail(conn, doc_plain_id)
    conn.close()

    assert detail_table == "## 벤치마크 표 렌더링 본문"
    assert detail_plain is None


def test_reextract_all_tables_only(table_test_env):
    svc, doc_table_id, doc_plain_id = table_test_env

    svc.provider.extract = MagicMock(
        return_value=ExtractionResult(
            summary="재추출된 벤치마크 요약.",
            key_claims=[],
            entities=[ExtractedEntity(name="Model-X", type="Technology", aliases=[], observations=[])],
            relations=[],
        )
    )

    res = svc.reextract_all(rebuild=False, tables_only=True)
    assert res["docs"] == 1
    assert res["ok"] == 1


def test_cli_parser_table_flags():
    from claire.cli import build_parser

    parser = build_parser()

    # 1. regenerate --tables
    args1 = parser.parse_args(["regenerate", "--tables", "--all"])
    assert args1.tables is True
    assert args1.all is True

    # 2. regenerate --has-tables
    args2 = parser.parse_args(["regenerate", "--has-tables", "--detail"])
    assert args2.tables is True
    assert args2.detail is True

    # 3. backfill-detail --tables
    args3 = parser.parse_args(["backfill-detail", "--tables"])
    assert args3.tables is True

    # 4. reextract --tables
    args4 = parser.parse_args(["reextract", "--tables"])
    assert args4.tables is True

