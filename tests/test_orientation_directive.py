"""본문 방향성(Orientation / Directive) 지정 적재 및 재생성 기능 테스트."""

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
    render_detail_prompt_md,
)
from claire.extract.provider import MockProvider
from claire.ingest.pipeline import ensure_document_detail, ingest
from claire.ingest.service import IngestService
from claire.ontology.base import Document
from claire.store import db as dbm
from claire.store.vectors import make_vector_store


def test_prompts_with_directive():
    body = "Sample Content"
    images = []
    directive = "시스템 아키텍처 및 내부 컴포넌트 구조 중심"

    # 1. Markdown prompt without directive
    md_no_dir = render_detail_prompt_md(body, images, merged=False)
    assert "중점 작성 방향성/초점" not in md_no_dir

    # 2. Markdown prompt with directive
    md_with_dir = render_detail_prompt_md(body, images, merged=False, directive=directive)
    assert "중점 작성 방향성/초점" in md_with_dir
    assert directive in md_with_dir

    # 3. AsciiDoc prompt without directive
    adoc_no_dir = render_detail_prompt_adoc(body, images, merged=False)
    assert "중점 작성 방향성/초점" not in adoc_no_dir

    # 4. AsciiDoc prompt with directive
    adoc_with_dir = render_detail_prompt_adoc(body, images, merged=False, directive=directive)
    assert "중점 작성 방향성/초점" in adoc_with_dir
    assert directive in adoc_with_dir

    # 5. Router function render_detail_prompt
    routed_md = render_detail_prompt(body, images, merged=False, format="md", directive=directive)
    assert "중점 작성 방향성/초점" in routed_md
    assert directive in routed_md

    routed_adoc = render_detail_prompt(body, images, merged=False, format="adoc", directive=directive)
    assert "중점 작성 방향성/초점" in routed_adoc
    assert directive in routed_adoc


def test_mock_provider_directive():
    provider = MockProvider()
    doc = Document(id="doc_test1", title="Test Tool", raw_text="A tool for testing.")

    # No directive
    detail_plain = provider.render_detail(doc, format="md")
    assert "[mock-detail] **Test Tool**" in detail_plain
    assert "[directive:" not in detail_plain

    # With directive
    detail_dir = provider.render_detail(doc, format="md", directive="아키텍처 중심")
    assert "[directive: 아키텍처 중심]" in detail_dir

    # AsciiDoc with directive
    detail_adoc = provider.render_detail(doc, format="adoc", directive="튜토리얼 중심")
    assert "[mock-detail-adoc] [directive: 튜토리얼 중심]" in detail_adoc


def test_db_document_directive(tmp_path: Path):
    db_file = tmp_path / "test.db"
    conn = dbm.connect(db_file)
    dbm.init_db(conn)

    doc = Document(id="doc_d1", title="Doc 1", raw_text="Content 1")
    dbm.insert_document(conn, doc)

    # Initial directive should be None
    assert dbm.get_document_directive(conn, "doc_d1") is None

    # Set directive
    dbm.set_document_directive(conn, "doc_d1", "초보자 튜토리얼 관점")
    assert dbm.get_document_directive(conn, "doc_d1") == "초보자 튜토리얼 관점"

    # Verify other meta keys are preserved
    dbm.set_document_images(conn, "doc_d1", [{"url": "http://img.png", "local": None}])
    assert dbm.get_document_directive(conn, "doc_d1") == "초보자 튜토리얼 관점"

    # Remove directive
    dbm.set_document_directive(conn, "doc_d1", None)
    assert dbm.get_document_directive(conn, "doc_d1") is None

    conn.close()


def test_pipeline_ensure_document_detail_directive(tmp_path: Path):
    db_file = tmp_path / "test.db"
    conn = dbm.connect(db_file)
    dbm.init_db(conn)

    provider = MockProvider()
    doc = Document(id="doc_pipe1", title="Pipeline Test", raw_text="Pipeline content")
    dbm.insert_document(conn, doc)

    # First call with directive
    res = ensure_document_detail(conn, provider, doc, directive="시스템 아키텍처 중심")
    assert res is True
    detail = dbm.get_document_detail(conn, "doc_pipe1")
    assert "[directive: 시스템 아키텍처 중심]" in detail
    assert dbm.get_document_directive(conn, "doc_pipe1") == "시스템 아키텍처 중심"

    # Second call with same directive and format without force -> False (no-op)
    res_noop = ensure_document_detail(conn, provider, doc, directive="시스템 아키텍처 중심")
    assert res_noop is False

    # Third call with DIFFERENT directive without force -> True (should regenerate due to changed directive)
    res_changed = ensure_document_detail(conn, provider, doc, directive="개발자 실습 중심")
    assert res_changed is True
    detail_updated = dbm.get_document_detail(conn, "doc_pipe1")
    assert "[directive: 개발자 실습 중심]" in detail_updated
    assert dbm.get_document_directive(conn, "doc_pipe1") == "개발자 실습 중심"

    conn.close()


def test_pipeline_ingest_directive(tmp_path: Path):
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
        directive="핵심 알고리즘 및 수학적 원리 중심",
    )
    assert report.error is None
    doc_id = report.document_id
    assert doc_id is not None

    detail = dbm.get_document_detail(conn, doc_id)
    assert "[directive: 핵심 알고리즘 및 수학적 원리 중심]" in detail
    assert dbm.get_document_directive(conn, doc_id) == "핵심 알고리즘 및 수학적 원리 중심"

    # 2. 동일 내용의 문서에 대해 새로운 방향성(directive)으로 재적재 요청 시 -> 중복 스킵하지 않고 본문 재생성/갱신
    report2 = ingest(
        payload,
        conn=conn,
        provider=provider,
        vstore=vstore,
        directive="시스템 아키텍처 및 내부 컴포넌트 관점",
    )
    assert report2.error is None
    assert report2.document_id == doc_id
    assert report2.updated is True
    assert report2.duplicate is False

    detail2 = dbm.get_document_detail(conn, doc_id)
    assert "[directive: 시스템 아키텍처 및 내부 컴포넌트 관점]" in detail2
    assert dbm.get_document_directive(conn, doc_id) == "시스템 아키텍처 및 내부 컴포넌트 관점"

    conn.close()


def test_service_ingest_and_regenerate_directive(tmp_path: Path):
    db_file = tmp_path / "test.db"
    vault_dir = tmp_path / "vault"
    settings = Settings(
        db_path=str(db_file),
        vault_path=str(vault_dir),
        provider="mock",
        vector_backend="mock",
    )
    svc = IngestService(settings)

    # 1. Ingest with orientation
    payload = "제목: 서비스 테스트\n본문: 서비스 계층의 적재와 컴포넌트 재생성을 테스트하는 본문입니다."
    report = svc.ingest(
        payload,
        source="cli",
        directive="비즈니스 모델 및 시장 포지셔닝 관점",
    )
    assert report.error is None
    doc_id = report.document_id

    conn = dbm.connect(db_file)
    detail = dbm.get_document_detail(conn, doc_id)
    assert "[directive: 비즈니스 모델 및 시장 포지셔닝 관점]" in detail
    assert dbm.get_document_directive(conn, doc_id) == "비즈니스 모델 및 시장 포지셔닝 관점"

    # 2. Regenerate with new directive
    res = svc.regenerate_components(
        doc_id=doc_id,
        detail=True,
        directive="보안 및 취약점 분석 관점",
        force=True,
    )
    assert res.get("count") == 1
    target_info = res["targets"][0]
    assert target_info.get("directive") == "보안 및 취약점 분석 관점"

    detail_regen = dbm.get_document_detail(conn, doc_id)
    assert "[directive: 보안 및 취약점 분석 관점]" in detail_regen
    assert dbm.get_document_directive(conn, doc_id) == "보안 및 취약점 분석 관점"

    # 3. Backfill details with directive
    res_bf = svc.backfill_details(
        force=True,
        directive="전체 요약 및 결론 중심",
    )
    assert res_bf["ok"] >= 1
    detail_bf = dbm.get_document_detail(conn, doc_id)
    assert "[directive: 전체 요약 및 결론 중심]" in detail_bf
    assert dbm.get_document_directive(conn, doc_id) == "전체 요약 및 결론 중심"

    conn.close()


def test_cli_orientation_parsing(tmp_path: Path, monkeypatch):
    import argparse
    from claire import cli

    db_file = tmp_path / "test.db"
    vault_dir = tmp_path / "vault"
    monkeypatch.setenv("CLAIRE_DB_PATH", str(db_file))
    monkeypatch.setenv("CLAIRE_VAULT_PATH", str(vault_dir))
    monkeypatch.setenv("CLAIRE_PROVIDER", "mock")
    monkeypatch.setenv("CLAIRE_VECTOR_BACKEND", "mock")
    get_settings.cache_clear()

    # CLI Ingest with --orientation
    args = argparse.Namespace(
        payload="제목: CLI 고유 테스트\n본문: CLI 명령어를 통한 적재를 검증합니다.",
        expand=False,
        no_expand=True,
        format="md",
        orientation="CLI 방향성 테스트",
        directive=None,
    )
    ret = cli.cmd_ingest(args)
    assert ret == 0

    conn = dbm.connect(db_file)
    row = conn.execute("SELECT id FROM documents LIMIT 1").fetchone()
    assert row is not None
    doc_id = row["id"]
    assert dbm.get_document_directive(conn, doc_id) == "CLI 방향성 테스트"

    # CLI Regenerate with --directive alias
    args_regen = argparse.Namespace(
        target=doc_id,
        token=None,
        doc_id=doc_id,
        summary=False,
        detail=True,
        graph=False,
        all=False,
        corrupted=False,
        refetch=False,
        apply=True,
        force=True,
        effort=None,
        format="adoc",
        orientation=None,
        directive="CLI 재생성 방향성",
        json=False,
    )
    ret_regen = cli.cmd_regenerate(args_regen)
    assert ret_regen == 0

    detail_adoc = dbm.get_document_detail(conn, doc_id)
    assert "[mock-detail-adoc] [directive: CLI 재생성 방향성]" in detail_adoc
    assert dbm.get_document_directive(conn, doc_id) == "CLI 재생성 방향성"

    conn.close()
    get_settings.cache_clear()


def test_api_server_orientation(tmp_path: Path):
    db_file = tmp_path / "test.db"
    vault_dir = tmp_path / "vault"
    settings = Settings(
        db_path=str(db_file),
        vault_path=str(vault_dir),
        environment="development",
        provider="mock",
        vector_backend="mock",
        public_url="http://127.0.0.1:8765",
        inject_token="owner-test-token-12345678901234567890",
        anonymous_readonly=False,
    )
    app = server.create_app(settings)
    with TestClient(app, base_url=settings.public_url) as client:
        # 1. Ingest via API with orientation
        resp = client.post(
            "/ingest",
            json={
                "payload": "제목: API 테스트\n본문: REST API를 통한 방향성 적재를 테스트합니다.",
                "orientation": "API 방향성 전달 테스트",
            },
            headers={"Authorization": f"Bearer {settings.inject_token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        doc_id = data.get("document_id")
        assert doc_id is not None

        conn = dbm.connect(db_file)
        detail = dbm.get_document_detail(conn, doc_id)
        assert "[directive: API 방향성 전달 테스트]" in detail
        assert dbm.get_document_directive(conn, doc_id) == "API 방향성 전달 테스트"
        conn.close()

        # 2. Ingest stream via API with directive
        resp_stream = client.post(
            "/ingest-stream",
            json={
                "payload": "제목: 스트림 테스트\n본문: 스트리밍 적재 시 방향성 전달을 검증합니다.",
                "directive": "스트림 방향성 전달 테스트",
            },
            headers={"Authorization": f"Bearer {settings.inject_token}"},
        )
        assert resp_stream.status_code == 200
        lines = resp_stream.text.strip().split("\n")
        final_event = json.loads(lines[-1])
        assert final_event.get("done") is True
        stream_doc_id = final_event["result"]["document_id"]

        conn = dbm.connect(db_file)
        stream_detail = dbm.get_document_detail(conn, stream_doc_id)
        assert "[directive: 스트림 방향성 전달 테스트]" in stream_detail
        assert dbm.get_document_directive(conn, stream_doc_id) == "스트림 방향성 전달 테스트"

        # 3. Ingest stream via API with payload containing double-newline directive
        resp_double_nl = client.post(
            "/ingest-stream",
            json={
                "payload": "제목: 웹 브라우저 적재 테스트\n본문: 본문 내용입니다.\n\n[방향성] 더블 줄바꿈 자동 분리 테스트",
            },
            headers={"Authorization": f"Bearer {settings.inject_token}"},
        )
        assert resp_double_nl.status_code == 200
        lines2 = resp_double_nl.text.strip().split("\n")
        final_event2 = json.loads(lines2[-1])
        assert final_event2.get("done") is True
        doc_id2 = final_event2["result"]["document_id"]

        detail2 = dbm.get_document_detail(conn, doc_id2)
        assert "[directive: 더블 줄바꿈 자동 분리 테스트]" in detail2
        assert dbm.get_document_directive(conn, doc_id2) == "더블 줄바꿈 자동 분리 테스트"
        conn.close()


def test_router_clean_url_with_trailing_directive():
    from claire.ingest.router import _clean_url

    # 1. Pure URL
    assert _clean_url("https://example.com/doc.pdf") == "https://example.com/doc.pdf"

    # 2. URL with trailing em-dash directive
    assert _clean_url("https://example.com/doc.pdf —orientation Key Activities") == "https://example.com/doc.pdf"

    # 3. URL with trailing plain directive
    assert _clean_url("https://example.com/doc.pdf — Key Activities, Key Partners") == "https://example.com/doc.pdf"

    # 4. Non-URL plain text
    assert _clean_url("Plain text memo") == "Plain text memo"

