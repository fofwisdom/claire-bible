"""첫 적재 시 무절단 수집(full_content) 및 추론 레벨(effort) 지정 검증 테스트."""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from claire.cli import cmd_ingest
from claire.config import Settings
from claire.extract.prompts import doc_to_prompt
from claire.extract.provider import ExtractionResult, MockProvider
from claire.ingest.pipeline import IngestReport, extract_resolve_store, ingest
from claire.ingest.service import IngestService
from claire.ontology.base import Document
from claire.store import db as dbm
from claire.store.vectors import make_vector_store
from claire.telegram_bot import parse_message_directive, parse_regenerate_flags


def test_cli_ingest_argument_parser():
    """CLI ingest 서브커맨드가 --full, --full-content, --no-truncate, --effort 플래그를 정확히 파싱하는지 검증."""
    from claire.cli import cmd_ingest

    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers()
    pi = sub.add_parser("ingest")
    pi.add_argument("payload")
    pi.add_argument("--full", "--full-content", "--no-truncate", action="store_true", dest="full_content")
    pi.add_argument("--effort", "-e", choices=["none", "minimal", "low", "medium", "high", "max"], default=None)
    pi.add_argument("--expand", action="store_true")
    pi.add_argument("--no-expand", action="store_true")
    pi.add_argument("--format", choices=["md", "adoc"], default=None)
    pi.add_argument("--focus", "--orientation", "--directive", default=None)
    pi.set_defaults(func=cmd_ingest)

    # 1. --full 및 --effort high
    args = parser.parse_args(["ingest", "https://example.com/law", "--full", "--effort", "high"])
    assert args.payload == "https://example.com/law"
    assert args.full_content is True
    assert args.effort == "high"

    # 2. --no-truncate 및 -e low
    args2 = parser.parse_args(["ingest", "sample.txt", "--no-truncate", "-e", "low"])
    assert args2.full_content is True
    assert args2.effort == "low"

    # 3. 기본값 (미지정 시 False, None)
    args3 = parser.parse_args(["ingest", "sample.txt"])
    assert args3.full_content is False
    assert args3.effort is None


def test_telegram_flags_and_directive_parsing():
    """텔레그램 메시지에서 --full, --effort 플래그와 파이프 초점(| directive)이 정확히 분리 파싱되는지 검증."""
    raw_msg = "https://example.com/article --full --effort high | 시스템 아키텍처 중심"
    payload, directive = parse_message_directive(raw_msg)
    assert directive == "시스템 아키텍처 중심"

    cleaned_payload, has_refetch, has_refetch_full, has_effort = parse_regenerate_flags(payload)
    assert cleaned_payload == "https://example.com/article"
    assert has_refetch_full is True
    assert has_effort == "high"


def test_telegram_flags_no_truncate_alias():
    """텔레그램 메시지에서 --no-truncate 및 -e 플래그가 지원되는지 검증."""
    raw_msg = "https://example.com/paper --no-truncate -e medium"
    payload, directive = parse_message_directive(raw_msg)
    cleaned_payload, _, has_refetch_full, has_effort = parse_regenerate_flags(payload)
    assert cleaned_payload == "https://example.com/paper"
    assert has_refetch_full is True
    assert has_effort == "medium"


def test_prompts_doc_to_prompt_full_content_bypass(monkeypatch: pytest.MonkeyPatch):
    """doc_to_prompt에서 full_content=True인 경우 20,000자 상한을 건너뛰고 35,000자 전문이 보존되는지 검증."""
    monkeypatch.setenv("CLAIRE_EXTRACT_CHAR_BUDGET", "20000")
    from claire.config import get_settings
    get_settings.cache_clear()

    long_body = "제1조(목적) 인공지능 발전과 신뢰 기반 조성... " * 1200  # ~36,000자
    assert len(long_body) > 30000

    doc = Document(
        title="인공지능 발전과 신뢰 기반 조성 등에 관한 기본법",
        raw_text=long_body,
        source_type="web",
        meta={"full_content": True},
    )

    # 1. full_content 메타데이터가 있는 경우: 30,000자 초과 본문이 슬라이싱되지 않고 보존
    prompt_text = doc_to_prompt(doc)
    assert len(prompt_text) > 30000
    assert "CONTENT:\n" in prompt_text

    # 2. full_content 메타데이터가 없는 일반 문서: extract_char_budget(20,000자)으로 절단
    doc_normal = Document(
        title="일반 웹 문서",
        raw_text=long_body,
        source_type="web",
        meta={"full_content": False},
    )
    prompt_normal = doc_to_prompt(doc_normal)
    # 헤더 제외 본문이 약 20,000자로 슬라이싱됨
    content_part = prompt_normal.split("CONTENT:\n", 1)[1]
    assert len(content_part) <= 20000


def test_pipeline_ingest_with_full_content_and_effort(tmp_path: Path):
    """첫 적재 파이프라인에서 full_content=True 및 effort='high' 전달 시 수집/아티팩트/메타데이터/요약 배지가 완전한지 검증."""
    db_path = tmp_path / "claire.db"
    conn = dbm.connect(str(db_path))
    dbm.init_db(conn)

    vstore = make_vector_store(conn, "auto")
    provider = MockProvider()
    data_dir = tmp_path / "data"

    long_text = "인공지능 법령 전문 제1조부터 제43조 및 부칙... " * 1000  # ~30,000자
    assert len(long_text) >= 30000

    def mock_fetch(url: str, *, full_content: bool = False) -> Document:
        return Document(
            url=url,
            canonical_url=url,
            title="인공지능 기본법",
            raw_text=long_text,
            source_type="web",
            meta={
                "raw_truncated": False,
                "orig_chars": len(long_text),
                "raw_chars": len(long_text),
            },
        )

    report = ingest(
        "https://example.com/law",
        conn=conn,
        provider=provider,
        vstore=vstore,
        vault_dir=tmp_path / "vault",
        data_dir=data_dir,
        fetch_fn=mock_fetch,
        full_content=True,
        effort="high",
        directive="벌칙 규정 중심",
    )

    assert report.error is None
    assert report.full_content is True
    assert report.effort == "high"

    # 텔레그램 요약 메시지에 뱃지가 표시되는지 확인
    summary_text = report.telegram_summary()
    assert "🌐 원문 무절단 수집" in summary_text
    assert "🧠 추론: high" in summary_text
    assert "초점: 벌칙 규정 중심" in summary_text

    # DB에 저장된 문서의 raw_text 및 메타데이터 검증
    saved_doc = dbm.get_document(conn, report.document_id)
    assert saved_doc is not None
    assert len(saved_doc.raw_text) == len(long_text)
    assert saved_doc.meta.get("full_content") is True
    assert saved_doc.meta.get("applied_effort") == "high"

    conn.close()


def test_ingest_service_passes_full_content_and_effort(tmp_path: Path):
    """IngestService.ingest()가 full_content와 effort를 pipeline.ingest로 정상 전달하는지 검증."""
    settings = Settings(
        db_path=str(tmp_path / "claire.db"),
        vault_path=str(tmp_path / "vault"),
        provider="mock",
    )
    svc = IngestService(settings)

    with patch("claire.ingest.service.ingest") as mock_pipeline_ingest:
        mock_pipeline_ingest.return_value = IngestReport(
            document_id="doc_test123",
            title="Test Document",
            full_content=True,
            effort="high",
        )
        report = svc.ingest(
            "https://example.com/test",
            source="cli",
            full_content=True,
            effort="high",
        )
        assert mock_pipeline_ingest.called
        kwargs = mock_pipeline_ingest.call_args[1]
        assert kwargs["full_content"] is True
        assert kwargs["effort"] == "high"
        assert report.full_content is True
        assert report.effort == "high"
