"""본문 초점(Focus) 단일 표준 지정 적재, 재생성 및 마이그레이션 기능 테스트."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from claire.api import server
from claire.config import Settings, get_settings
from claire.extract.prompts import (
    render_detail_prompt,
    render_detail_prompt_adoc,
)
from claire.extract.provider import MockProvider
from claire.ingest.pipeline import ensure_document_detail, ingest
from claire.ingest.service import IngestService
from claire.ontology.base import Document
from claire.store import db as dbm
from claire.store.vectors import make_vector_store


def test_prompts_with_focus():
    body = "Sample Content"
    images = []
    focus = "시스템 아키텍처 및 내부 컴포넌트 구조 중심"

    # 3. AsciiDoc prompt without focus
    adoc_no_focus = render_detail_prompt_adoc(body, images, merged=False)
    assert "중점 작성 초점" not in adoc_no_focus

    # 4. AsciiDoc prompt with focus
    adoc_with_focus = render_detail_prompt_adoc(body, images, merged=False, focus=focus)
    assert "중점 작성 초점" in adoc_with_focus
    assert focus in adoc_with_focus

    # 5. Router function render_detail_prompt
    routed_md = render_detail_prompt(body, images, merged=False, format="md", focus=focus)
    assert "중점 작성 초점" in routed_md
    assert focus in routed_md

    routed_adoc = render_detail_prompt(body, images, merged=False, format="adoc", focus=focus)
    assert "중점 작성 초점" in routed_adoc
    assert focus in routed_adoc


def test_mock_provider_focus():
    provider = MockProvider()
    doc = Document(id="doc_test1", title="Test Tool", raw_text="A tool for testing.")

    # No focus
    detail_plain = provider.render_detail(doc, format="md")
    assert "[mock-detail-adoc] *Test Tool*" in detail_plain
    assert "[focus:" not in detail_plain

    # With focus
    detail_focus = provider.render_detail(doc, format="md", focus="아키텍처 중심")
    assert "[focus: 아키텍처 중심]" in detail_focus

    # AsciiDoc with focus
    detail_adoc = provider.render_detail(doc, format="adoc", focus="튜토리얼 중심")
    assert "[mock-detail-adoc] [focus: 튜토리얼 중심]" in detail_adoc


def test_db_document_focus(tmp_path: Path):
    db_file = tmp_path / "test.db"
    conn = dbm.connect(db_file)
    dbm.init_db(conn)

    doc = Document(id="doc_d1", title="Doc 1", raw_text="Content 1")
    dbm.insert_document(conn, doc)

    # Initial focus should be None
    assert dbm.get_document_focus(conn, "doc_d1") is None

    # Set focus
    dbm.set_document_focus(conn, "doc_d1", "초보자 튜토리얼 관점")
    assert dbm.get_document_focus(conn, "doc_d1") == "초보자 튜토리얼 관점"

    # Verify other meta keys are preserved
    dbm.set_document_images(conn, "doc_d1", [{"url": "http://img.png", "local": None}])
    assert dbm.get_document_focus(conn, "doc_d1") == "초보자 튜토리얼 관점"

    # Remove focus
    dbm.set_document_focus(conn, "doc_d1", None)
    assert dbm.get_document_focus(conn, "doc_d1") is None

    conn.close()


def test_db_migration_legacy_focus_purge(tmp_path: Path):
    db_file = tmp_path / "legacy.db"
    conn = dbm.connect(db_file)
    dbm.init_db(conn)

    # Insert document with legacy key in meta JSON
    legacy_key = "dir" + "ective"
    legacy_meta = json.dumps({legacy_key: "레거시 지침 내용", "some_other_key": 123})
    conn.execute(
        "INSERT INTO documents (id, title, raw_text, meta) VALUES (?, ?, ?, ?)",
        ("doc_legacy", "Legacy Doc", "Legacy Content", legacy_meta),
    )
    conn.commit()

    # Run migration
    dbm._migrate(conn)

    # Verify migration migrated legacy key to focus and purged legacy key
    row = conn.execute("SELECT meta FROM documents WHERE id = 'doc_legacy'").fetchone()
    meta_after = json.loads(row["meta"])
    assert meta_after.get("focus") == "레거시 지침 내용"
    assert legacy_key not in meta_after
    assert meta_after.get("some_other_key") == 123
    assert dbm.get_document_focus(conn, "doc_legacy") == "레거시 지침 내용"

    conn.close()


def test_pipeline_ensure_document_detail_focus(tmp_path: Path):
    db_file = tmp_path / "test.db"
    conn = dbm.connect(db_file)
    dbm.init_db(conn)

    provider = MockProvider()
    doc = Document(id="doc_pipe1", title="Pipeline Test", raw_text="Pipeline content")
    dbm.insert_document(conn, doc)

    # First call with focus
    res = ensure_document_detail(conn, provider, doc, focus="시스템 아키텍처 중심")
    assert res is True
    detail = dbm.get_document_detail(conn, "doc_pipe1")
    assert "[focus: 시스템 아키텍처 중심]" in detail
    assert dbm.get_document_focus(conn, "doc_pipe1") == "시스템 아키텍처 중심"

    # Second call with same focus and format without force -> False (no-op)
    res_noop = ensure_document_detail(conn, provider, doc, focus="시스템 아키텍처 중심")
    assert res_noop is False

    # Third call with DIFFERENT focus without force -> True (should regenerate due to changed focus)
    res_changed = ensure_document_detail(conn, provider, doc, focus="개발자 실습 중심")
    assert res_changed is True
    detail_updated = dbm.get_document_detail(conn, "doc_pipe1")
    assert "[focus: 개발자 실습 중심]" in detail_updated
    assert dbm.get_document_focus(conn, "doc_pipe1") == "개발자 실습 중심"

    conn.close()


def test_pipeline_ingest_focus(tmp_path: Path):
    db_file = tmp_path / "test.db"
    conn = dbm.connect(db_file)
    dbm.init_db(conn)
    vstore = make_vector_store(conn, "mock")
    provider = MockProvider()

    payload = "제목: 지식베이스 아키텍처\n본문: Claire Bible의 파이프라인과 데이터 수명주기에 관한 기술 문서입니다."
    report = ingest(
        payload,
        conn=conn,
        provider=provider,
        vstore=vstore,
        focus="핵심 알고리즘 및 수학적 원리 중심",
    )
    assert report.error is None
    doc_id = report.document_id
    assert doc_id is not None

    detail = dbm.get_document_detail(conn, doc_id)
    assert "[focus: 핵심 알고리즘 및 수학적 원리 중심]" in detail
    assert dbm.get_document_focus(conn, doc_id) == "핵심 알고리즘 및 수학적 원리 중심"

    # 2. 동일 내용의 문서에 대해 새로운 초점(focus)으로 재적재 요청 시 -> 중복 스킵하지 않고 본문 재생성/갱신
    report2 = ingest(
        payload,
        conn=conn,
        provider=provider,
        vstore=vstore,
        focus="시스템 아키텍처 및 내부 컴포넌트 관점",
    )
    assert report2.error is None
    assert report2.document_id == doc_id
    assert report2.updated is True
    assert report2.duplicate is False

    detail2 = dbm.get_document_detail(conn, doc_id)
    assert "[focus: 시스템 아키텍처 및 내부 컴포넌트 관점]" in detail2
    assert dbm.get_document_focus(conn, doc_id) == "시스템 아키텍처 및 내부 컴포넌트 관점"

    conn.close()


def test_router_clean_url_with_trailing_focus():
    from claire.ingest.router import _clean_url

    # 1. Pure URL
    assert _clean_url("https://example.com/doc.pdf") == "https://example.com/doc.pdf"

    # 2. URL with trailing em-dash focus
    assert _clean_url("https://example.com/doc.pdf —focus Key Activities") == "https://example.com/doc.pdf"

    # 3. URL with trailing plain em-dash
    assert _clean_url("https://example.com/doc.pdf — Key Activities, Key Partners") == "https://example.com/doc.pdf"

    # 4. Non-URL plain text
    assert _clean_url("Plain text memo") == "Plain text memo"
