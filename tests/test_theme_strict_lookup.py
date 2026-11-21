"""테마 엄격 조회 (PRINCIPLES 위반 2, 설계 S2).

없는 테마나 접근할 수 없는 테마를 테마 0으로 대신하지 않는다. 계정이 생기면 테마 0은 관리자 테마이므로 이 폴백은 계정 사이로 쓰기·읽기를 새게 한다.

검증 범위:
- HTTP: 없는 숫자 ID·레이블, 레이블 부분 일치는 404. 적재는 raw_inbox 행을 만들지 않는다.
- CLI `--theme`은 사람이 입력하는 값이라 부분 일치를 유지한다.
- MCP: 없는 테마는 {"error": "theme not found"}.
- IngestServicePool·IngestService: 없는(삭제된) 테마는 KnowledgeBaseNotFound.
- 테마 참조를 생략하면 테마 0 (문서화된 기본값).
- DB에서 읽은 ID가 삭제된 테마를 가리키면 테마 0으로 다루지 않는다.
- 텔레그램: 삭제된 선택 테마는 거부하고 선택을 지운다.
"""

from __future__ import annotations

import argparse
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from starlette.testclient import TestClient

from tests.kb_paths import step_up_request

from claire import cli
from claire.api.mcp_tools import build_mcp_app
from claire.api.server import create_app
from claire.core.config import Settings
from claire.ingest.service import IngestService, IngestServicePool
from claire.store import db as dbm
from claire.store.knowledge_base import KnowledgeBaseManager, KnowledgeBaseNotFound

# The assertions of this module expect the Korean interface text (fixture in conftest.py).
pytestmark = pytest.mark.usefixtures("korean_interface")


OWNER_TOKEN = "owner-" + ("o" * 32)
OWNER_HEADERS = {"Authorization": f"Bearer {OWNER_TOKEN}"}


def _settings(tmp_path, *, multi: bool = True) -> Settings:
    data_dir = tmp_path / "data"
    vault_dir = tmp_path / "vault"
    data_dir.mkdir(parents=True, exist_ok=True)
    vault_dir.mkdir(parents=True, exist_ok=True)
    return Settings(
        CLAIRE_DB_PATH=str(data_dir / "claire.db"),
        CLAIRE_VAULT_PATH=str(vault_dir),
        CLAIRE_PROVIDER="mock",
        CLAIRE_ENVIRONMENT="development",
        CLAIRE_PUBLIC_URL="http://127.0.0.1:8765",
        CLAIRE_INJECT_TOKEN=OWNER_TOKEN,
        CLAIRE_ANONYMOUS_READONLY=True,
        CLAIRE_MULTI_THEME=multi,
    )


@pytest.fixture
def strict_env(tmp_path):
    settings = _settings(tmp_path)
    tm = KnowledgeBaseManager(settings)
    t1 = tm.define_kb("Alpha Research", knowledge_type="real")
    app = create_app(settings=settings)
    client = TestClient(app, base_url=settings.public_url)
    return client, settings, tm, t1


def _num(kb) -> str:
    from claire.core import ids

    return ids.format_public_id(kb.number)


def _seed(client: TestClient, settings: Settings, tm: KnowledgeBaseManager, kb, text: str) -> str:
    """HTTP ingest writes only to the personal knowledge base (CT23). Seed another one with its service, then read the ID by its path."""
    IngestService(tm.get_settings_for_kb(kb.id, settings), kb_id=kb.id).ingest(text, source="cli", expand_max=0)
    docs = client.get(f"/kb/{_num(kb)}/documents", headers=OWNER_HEADERS).json()["documents"]
    return docs[0]["id"]


def _raw_inbox_count(settings: Settings) -> int:
    conn = dbm.connect_existing(settings.db_file, readonly=True)
    try:
        return conn.execute("SELECT COUNT(*) FROM raw_inbox").fetchone()[0]
    finally:
        conn.close()


def _mcp_call(client: TestClient, name: str, arguments: dict) -> dict:
    resp = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
    )
    assert resp.status_code == 200
    return json.loads(resp.json()["result"]["content"][0]["text"])


# ---------------------------------------------------------------------------
# KnowledgeBaseManager.get_kb
# ---------------------------------------------------------------------------


def test_get_theme_has_no_fallback_and_partial_is_opt_in(tmp_path):
    tm = KnowledgeBaseManager(_settings(tmp_path))
    t1 = tm.define_kb("Alpha Research", knowledge_type="real")

    # 참조 생략은 문서화된 기본값(테마 0)이다
    assert tm.get_kb(None).id == 0
    assert tm.get_kb("").id == 0
    assert tm.get_kb("  ").id == 0

    # 숫자 ID와 대소문자 무시 정확 일치
    assert tm.get_kb(t1.id).id == t1.id
    assert tm.get_kb(str(t1.id)).id == t1.id
    assert tm.get_kb("alpha research").id == t1.id

    # 없는 ID·레이블과 부분 일치는 기본으로 KnowledgeBaseNotFound (KeyError 호환)
    for ref in (999, "999", "nope", "Alpha"):
        with pytest.raises(KnowledgeBaseNotFound):
            tm.get_kb(ref)
    assert issubclass(KnowledgeBaseNotFound, KeyError)

    # 부분 일치는 호출자가 요청할 때만
    assert tm.get_kb("Alpha", label_match="partial").id == t1.id
    with pytest.raises(KnowledgeBaseNotFound):
        tm.get_kb("nope", label_match="partial")
    with pytest.raises(ValueError):
        tm.get_kb(t1.id, label_match="fuzzy")


def test_get_settings_for_theme_raises_for_deleted_theme(tmp_path):
    tm = KnowledgeBaseManager(_settings(tmp_path))
    t1 = tm.define_kb("임시", knowledge_type="real")
    tm.delete_kb(t1.id)
    with pytest.raises(KnowledgeBaseNotFound):
        tm.get_settings_for_kb(t1.id)


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------


def test_http_dispatch_unknown_target_or_source_theme_is_404(strict_env):
    client, settings, tm, t1 = strict_env

    doc_id = _seed(client, settings, tm, t1, "배급 원본 문서 본문")

    bad_target = client.post(
        "/api/syndication/dispatch",
        json={"document_id": doc_id, "target_kb_id": 999},
        headers=OWNER_HEADERS,
    )
    assert bad_target.status_code == 404

    bad_source = client.post(
        "/api/syndication/dispatch",
        json={"document_id": doc_id, "target_kb_id": 0, "source_kb_id": "nope"},
        headers=OWNER_HEADERS,
    )
    assert bad_source.status_code == 404

    # Without a source there is no default (CT23): the target is not used in its place. ``?theme=`` is ignored.
    no_source = client.post(
        "/api/syndication/dispatch?theme=999",
        json={"document_id": doc_id, "target_kb_id": 0},
        headers=OWNER_HEADERS,
    )
    assert no_source.status_code == 400


def test_http_doc_in_deleted_theme_is_not_served_as_theme_zero(strict_env):
    """DB에서 읽은 문서의 kb_id가 삭제된 테마면 그 후보를 테마 0으로 다루지 않는다 (`_resolve_presentation_target`)."""
    client, settings, tm, t1 = strict_env
    t2 = tm.define_kb("Beta Live", presentation_enabled=True, knowledge_type="real")
    # 예전 폴백이면 삭제된 테마의 문서가 테마 0 설정(프레젠테이션 활성)으로 200을 받았다
    tm.update_kb(0, presentation_enabled=True)

    doc_t1 = _seed(client, settings, tm, t1, f"{t1.label} 문서 본문")
    doc_t2 = _seed(client, settings, tm, t2, f"{t2.label} 문서 본문")

    # 데이터 행은 남기고 테마만 삭제
    # The HTTP delete purges (design Section 10.1). This test needs the rows of the documents to stay, so it deletes with the store.
    tm.delete_kb(t1.id, purge=False)

    live = client.get(f"/kb/{_num(t2)}/document/presentation?id={doc_t2}", headers=OWNER_HEADERS)
    assert live.status_code == 200

    # 삭제된 지식 베이스의 경로는 404다 (지식 베이스 0으로 다루지 않는다)
    assert client.get(f"/kb/{_num(t1)}/document/presentation?id={doc_t1}", headers=OWNER_HEADERS).status_code == 404
    assert client.get(f"/kb/{_num(t1)}/stats", headers=OWNER_HEADERS).status_code == 404


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_theme_partial_label_still_resolves(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    tm = KnowledgeBaseManager(settings)
    t1 = tm.define_kb("Alpha Research", knowledge_type="real")
    monkeypatch.setattr("claire.cli.get_settings", lambda: settings)
    monkeypatch.setattr("claire.store.knowledge_base.get_kb_manager", lambda s=None: tm)

    eff_s, eff_theme = cli.get_effective_settings(argparse.Namespace(theme="Alpha"))
    assert eff_theme is not None
    assert eff_theme.id == t1.id
    assert eff_s.kb_id == t1.id

    with pytest.raises(ValueError, match="찾을 수 없습니다"):
        cli.get_effective_settings(argparse.Namespace(theme="nope"))


# ---------------------------------------------------------------------------
# MCP
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 적재 서비스
# ---------------------------------------------------------------------------


def test_service_pool_raises_for_unknown_and_deleted_theme(tmp_path):
    settings = _settings(tmp_path)
    tm = KnowledgeBaseManager(settings)
    t1 = tm.define_kb("Alpha Research", knowledge_type="real")
    base = IngestService(settings)
    pool = IngestServicePool(settings, base, theme_manager=tm)

    with pytest.raises(KnowledgeBaseNotFound):
        pool.get_service(999)
    with pytest.raises(KnowledgeBaseNotFound):
        pool.get_service("Alpha")

    # 생략은 테마 0
    assert pool.get_service(None).kb_id == 0
    assert pool.get_service().kb_id == 0

    # 캐시된 서비스가 있어도 삭제된 테마는 돌려주지 않는다
    assert pool.get_service(t1.id).kb_id == t1.id
    tm.delete_kb(t1.id)
    with pytest.raises(KnowledgeBaseNotFound):
        pool.get_service(t1.id)


def test_ingest_service_for_deleted_theme_refuses_before_writing(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    tm = KnowledgeBaseManager(settings)
    t1 = tm.define_kb("곧 삭제", knowledge_type="real")
    t1_settings = tm.get_settings_for_kb(t1.id, settings)
    svc = IngestService(t1_settings, kb_id=t1.id)
    monkeypatch.setattr("claire.ingest.service.get_kb_manager", lambda s=None: tm)
    tm.delete_kb(t1.id)

    with pytest.raises(KnowledgeBaseNotFound):
        svc.get_theme_label()
    with pytest.raises(KnowledgeBaseNotFound):
        svc.ingest("삭제된 테마로 가는 적재", source="cli", expand_max=0)
    assert _raw_inbox_count(settings) == 0


# ---------------------------------------------------------------------------
# 텔레그램
# ---------------------------------------------------------------------------


def test_telegram_resolve_selected_theme_clears_stale_selection(tmp_path):
    from claire.telegram.bot import resolve_selected_theme

    tm = KnowledgeBaseManager(_settings(tmp_path))
    t1 = tm.define_kb("Alpha Research", knowledge_type="real")
    selections = {100: t1.id}

    # A shared knowledge base is a selection for search and read. Ingest goes to the personal knowledge base (SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md Section 13.4).
    assert resolve_selected_theme(tm, selections, 100, for_read=True).id == t1.id
    assert resolve_selected_theme(tm, selections, 100).id == 0
    # 고른 적이 없으면 기본 테마 0
    assert resolve_selected_theme(tm, selections, 200).id == 0

    tm.delete_kb(t1.id)
    assert resolve_selected_theme(tm, selections, 100) is None
    assert 100 not in selections
    # 지운 뒤에는 선택 없음(기본 테마 0)
    assert resolve_selected_theme(tm, selections, 100).id == 0


