"""테마 엄격 조회 (PRINCIPLES 위반 2, 설계 S2).

없는 테마나 접근할 수 없는 테마를 테마 0으로 대신하지 않는다. 계정이 생기면 테마 0은 관리자 테마이므로 이 폴백은 계정 사이로 쓰기·읽기를 새게 한다.

검증 범위:
- HTTP: 없는 숫자 ID·레이블, 레이블 부분 일치는 404. 적재는 raw_inbox 행을 만들지 않는다.
- CLI `--theme`은 사람이 입력하는 값이라 부분 일치를 유지한다.
- MCP: 없는 테마는 {"error": "theme not found"}.
- IngestServicePool·IngestService: 없는(삭제된) 테마는 ThemeNotFound.
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

from claire import cli
from claire.api.mcp_tools import build_mcp_app
from claire.api.server import create_app
from claire.config import Settings
from claire.ingest.service import IngestService, IngestServicePool
from claire.store import db as dbm
from claire.store.theme import ThemeManager, ThemeNotFound

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
    tm = ThemeManager(settings)
    t1 = tm.define_theme("Alpha Research")
    app = create_app(settings=settings)
    client = TestClient(app, base_url=settings.public_url)
    return client, settings, tm, t1


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
# ThemeManager.get_theme
# ---------------------------------------------------------------------------


def test_get_theme_has_no_fallback_and_partial_is_opt_in(tmp_path):
    tm = ThemeManager(_settings(tmp_path))
    t1 = tm.define_theme("Alpha Research")

    # 참조 생략은 문서화된 기본값(테마 0)이다
    assert tm.get_theme(None).id == 0
    assert tm.get_theme("").id == 0
    assert tm.get_theme("  ").id == 0

    # 숫자 ID와 대소문자 무시 정확 일치
    assert tm.get_theme(t1.id).id == t1.id
    assert tm.get_theme(str(t1.id)).id == t1.id
    assert tm.get_theme("alpha research").id == t1.id

    # 없는 ID·레이블과 부분 일치는 기본으로 ThemeNotFound (KeyError 호환)
    for ref in (999, "999", "nope", "Alpha"):
        with pytest.raises(ThemeNotFound):
            tm.get_theme(ref)
    assert issubclass(ThemeNotFound, KeyError)

    # 부분 일치는 호출자가 요청할 때만
    assert tm.get_theme("Alpha", label_match="partial").id == t1.id
    with pytest.raises(ThemeNotFound):
        tm.get_theme("nope", label_match="partial")
    with pytest.raises(ValueError):
        tm.get_theme(t1.id, label_match="fuzzy")


def test_get_settings_for_theme_raises_for_deleted_theme(tmp_path):
    tm = ThemeManager(_settings(tmp_path))
    t1 = tm.define_theme("임시")
    tm.delete_theme(t1.id)
    with pytest.raises(ThemeNotFound):
        tm.get_settings_for_theme(t1.id)


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------


def test_http_unknown_theme_is_404(strict_env):
    client, settings, _, _ = strict_env

    for path in ("/stats?theme=999", "/graph?theme=nope", "/documents?theme=999"):
        resp = client.get(path, headers=OWNER_HEADERS)
        assert resp.status_code == 404, path
        assert resp.json()["error"] == "theme not found"

    # 헤더로 지정해도 같다
    resp = client.get("/stats", headers={**OWNER_HEADERS, "X-Claire-Theme": "999"})
    assert resp.status_code == 404


def test_http_ingest_unknown_theme_is_404_without_inbox_row(strict_env):
    client, settings, _, _ = strict_env
    before = _raw_inbox_count(settings)

    for theme_ref in (999, "nope", "Alpha"):
        resp = client.post(
            "/ingest",
            json={"payload": "테마를 잘못 적은 적재 요청", "theme": theme_ref},
            headers=OWNER_HEADERS,
        )
        assert resp.status_code == 404, theme_ref
        assert resp.json()["error"] == "theme not found"

    assert _raw_inbox_count(settings) == before
    conn = dbm.connect_existing(settings.db_file, readonly=True)
    try:
        assert conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 0
    finally:
        conn.close()


def test_http_partial_label_is_404_but_exact_label_resolves(strict_env):
    client, _, _, t1 = strict_env

    assert client.get("/stats?theme=Alpha", headers=OWNER_HEADERS).status_code == 404

    exact = client.get("/stats?theme=alpha%20research", headers=OWNER_HEADERS)
    assert exact.status_code == 200
    assert exact.json()["theme_id"] == t1.id


def test_http_private_theme_and_missing_theme_look_the_same(tmp_path):
    settings = _settings(tmp_path)
    tm = ThemeManager(settings)
    private = tm.define_theme("비공개 연구", is_public=False)
    client = TestClient(create_app(settings=settings), base_url=settings.public_url)

    hidden = client.get(f"/stats?theme={private.id}")
    missing = client.get("/stats?theme=999")
    assert hidden.status_code == missing.status_code == 404
    assert hidden.json() == missing.json() == {"error": "theme not found"}


def test_http_omitted_theme_is_theme_zero(strict_env):
    client, settings, _, _ = strict_env

    resp = client.get("/stats", headers=OWNER_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["theme_id"] == 0

    ingest = client.post("/ingest", json={"payload": "테마 생략 적재 문서"}, headers=OWNER_HEADERS)
    assert ingest.status_code == 200
    assert ingest.json().get("theme_id") == 0


def test_http_dispatch_unknown_target_or_source_theme_is_404(strict_env):
    client, _, _, t1 = strict_env

    ingest = client.post(
        "/ingest", json={"payload": "배급 원본 문서 본문", "theme": t1.id}, headers=OWNER_HEADERS
    )
    assert ingest.status_code == 200
    doc_id = client.get(f"/documents?theme={t1.id}", headers=OWNER_HEADERS).json()["documents"][0]["id"]

    bad_target = client.post(
        "/api/syndication/dispatch",
        json={"document_id": doc_id, "target_theme_id": 999},
        headers=OWNER_HEADERS,
    )
    assert bad_target.status_code == 404

    bad_source = client.post(
        "/api/syndication/dispatch",
        json={"document_id": doc_id, "target_theme_id": 0, "source_theme_id": "nope"},
        headers=OWNER_HEADERS,
    )
    assert bad_source.status_code == 404

    # 소스 테마를 본문 대신 ?theme= 로 명시했는데 없으면 대상 테마로 바꾸지 않고 404
    bad_ctx = client.post(
        "/api/syndication/dispatch?theme=999",
        json={"document_id": doc_id, "target_theme_id": 0},
        headers=OWNER_HEADERS,
    )
    assert bad_ctx.status_code == 404


def test_http_syndication_ui_unknown_theme_is_404(strict_env):
    client, _, _, t1 = strict_env
    assert client.get("/syndication?theme=999", headers=OWNER_HEADERS).status_code == 404
    assert client.get("/syndication/preferences?theme=nope", headers=OWNER_HEADERS).status_code == 404
    assert client.get(f"/syndication?theme={t1.id}", headers=OWNER_HEADERS).status_code == 200


def test_http_doc_in_deleted_theme_is_not_served_as_theme_zero(strict_env):
    """DB에서 읽은 문서의 theme_id가 삭제된 테마면 그 후보를 테마 0으로 다루지 않는다 (`_resolve_presentation_target`)."""
    client, settings, tm, t1 = strict_env
    t2 = tm.define_theme("Beta Live", presentation_enabled=True)
    # 예전 폴백이면 삭제된 테마의 문서가 테마 0 설정(프레젠테이션 활성)으로 200을 받았다
    tm.update_theme(0, presentation_enabled=True)

    for theme in (t1, t2):
        resp = client.post(
            "/ingest", json={"payload": f"{theme.label} 문서 본문", "theme": theme.id}, headers=OWNER_HEADERS
        )
        assert resp.status_code == 200
    doc_t1 = client.get(f"/documents?theme={t1.id}", headers=OWNER_HEADERS).json()["documents"][0]["id"]
    doc_t2 = client.get(f"/documents?theme={t2.id}", headers=OWNER_HEADERS).json()["documents"][0]["id"]

    # 데이터 행은 남기고 테마만 삭제
    assert client.delete(f"/themes?id={t1.id}", headers=OWNER_HEADERS).status_code == 200

    live = client.get(f"/document/presentation?id={doc_t2}", headers=OWNER_HEADERS)
    assert live.status_code == 200

    orphan = client.get(f"/document/presentation?id={doc_t1}", headers=OWNER_HEADERS)
    assert orphan.status_code == 404

    # 삭제된 테마를 직접 가리켜도 404
    assert client.get(f"/stats?theme={t1.id}", headers=OWNER_HEADERS).status_code == 404


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_theme_partial_label_still_resolves(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    tm = ThemeManager(settings)
    t1 = tm.define_theme("Alpha Research")
    monkeypatch.setattr("claire.cli.get_settings", lambda: settings)
    monkeypatch.setattr("claire.store.theme.get_theme_manager", lambda s=None: tm)

    eff_s, eff_theme = cli.get_effective_settings(argparse.Namespace(theme="Alpha"))
    assert eff_theme is not None
    assert eff_theme.id == t1.id
    assert eff_s.theme_id == t1.id

    with pytest.raises(ValueError, match="찾을 수 없습니다"):
        cli.get_effective_settings(argparse.Namespace(theme="nope"))


# ---------------------------------------------------------------------------
# MCP
# ---------------------------------------------------------------------------


def test_mcp_unknown_theme_returns_theme_not_found(tmp_path):
    settings = _settings(tmp_path)
    tm = ThemeManager(settings)
    tm.define_theme("Alpha Research")
    app = build_mcp_app(settings, theme_mgr=tm)

    calls = {
        "resolve_entity": {"name": "x"},
        "search": {"query": "x"},
        "neighbors": {"entity_ids": ["e1"]},
        "path": {"from_id": "e1", "to_id": "e2"},
        "context": {"entity_ids": ["e1"]},
        "overview": {},
        "node": {"entity_id": "e1"},
        "documents": {},
        "document": {"document_id": "doc_x"},
        "stats": {},
    }
    with TestClient(app, base_url="http://localhost:8765") as client:
        for name, args in calls.items():
            for theme_ref in (999, "nope", "Alpha"):
                data = _mcp_call(client, name, {**args, "theme": theme_ref})
                assert data == {"error": "theme not found"}, (name, theme_ref)

        # 참조 생략과 정확한 레이블은 정상 처리
        assert "error" not in _mcp_call(client, "stats", {})
        assert "error" not in _mcp_call(client, "stats", {"theme": "alpha research"})


def test_mcp_document_cross_theme_skips_deleted_theme(tmp_path):
    settings = _settings(tmp_path)
    tm = ThemeManager(settings)
    t1 = tm.define_theme("곧 삭제")
    conn = dbm.connect(settings.db_file)
    try:
        dbm.init_db(conn)
        from claire.ontology.base import Document

        dbm.insert_document(
            conn,
            Document(
                id="doc_orphan",
                url="https://example.com/orphan",
                title="Orphan Doc",
                raw_text="orphan text",
                source_type="web",
                content_hash="h_orphan",
            ),
            theme_id=t1.id,
        )
    finally:
        conn.close()
    tm.delete_theme(t1.id)

    app = build_mcp_app(settings, theme_mgr=tm)
    with TestClient(app, base_url="http://localhost:8765") as client:
        assert _mcp_call(client, "document", {"document_id": "doc_orphan"}) == {"error": "not found"}
        assert _mcp_call(client, "document", {"document_id": "doc_orphan", "theme": t1.id}) == {
            "error": "theme not found"
        }


def test_mcp_single_theme_without_manager_only_knows_theme_zero(tmp_path):
    settings = _settings(tmp_path, multi=False)
    conn = dbm.connect(settings.db_file)
    dbm.init_db(conn)
    conn.close()
    app = build_mcp_app(settings)
    with TestClient(app, base_url="http://localhost:8765") as client:
        assert "error" not in _mcp_call(client, "stats", {"theme": 0})
        assert _mcp_call(client, "stats", {"theme": 3}) == {"error": "theme not found"}


# ---------------------------------------------------------------------------
# 적재 서비스
# ---------------------------------------------------------------------------


def test_service_pool_raises_for_unknown_and_deleted_theme(tmp_path):
    settings = _settings(tmp_path)
    tm = ThemeManager(settings)
    t1 = tm.define_theme("Alpha Research")
    base = IngestService(settings)
    pool = IngestServicePool(settings, base, theme_manager=tm)

    with pytest.raises(ThemeNotFound):
        pool.get_service(999)
    with pytest.raises(ThemeNotFound):
        pool.get_service("Alpha")

    # 생략은 테마 0
    assert pool.get_service(None).theme_id == 0
    assert pool.get_service().theme_id == 0

    # 캐시된 서비스가 있어도 삭제된 테마는 돌려주지 않는다
    assert pool.get_service(t1.id).theme_id == t1.id
    tm.delete_theme(t1.id)
    with pytest.raises(ThemeNotFound):
        pool.get_service(t1.id)


def test_ingest_service_for_deleted_theme_refuses_before_writing(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    tm = ThemeManager(settings)
    t1 = tm.define_theme("곧 삭제", default_focus="삭제될 초점")
    t1_settings = tm.get_settings_for_theme(t1.id, settings)
    svc = IngestService(t1_settings.model_copy(update={"default_focus": ""}), theme_id=t1.id)
    monkeypatch.setattr("claire.ingest.service.get_theme_manager", lambda s=None: tm)
    tm.delete_theme(t1.id)

    with pytest.raises(ThemeNotFound):
        svc.get_theme_label()
    with pytest.raises(ThemeNotFound):
        svc.get_effective_default_focus()
    with pytest.raises(ThemeNotFound):
        svc.ingest("삭제된 테마로 가는 적재", source="cli", expand_max=0, focus="명시 초점")
    assert _raw_inbox_count(settings) == 0


# ---------------------------------------------------------------------------
# 텔레그램
# ---------------------------------------------------------------------------


def test_telegram_resolve_selected_theme_clears_stale_selection(tmp_path):
    from claire.telegram_bot import resolve_selected_theme

    tm = ThemeManager(_settings(tmp_path))
    t1 = tm.define_theme("Alpha Research")
    selections = {100: t1.id}

    assert resolve_selected_theme(tm, selections, 100).id == t1.id
    # 고른 적이 없으면 기본 테마 0
    assert resolve_selected_theme(tm, selections, 200).id == 0

    tm.delete_theme(t1.id)
    assert resolve_selected_theme(tm, selections, 100) is None
    assert 100 not in selections
    # 지운 뒤에는 선택 없음(기본 테마 0)
    assert resolve_selected_theme(tm, selections, 100).id == 0


async def test_telegram_stale_selection_refuses_ingest_and_search(tmp_path, monkeypatch):
    from claire.telegram_bot import STALE_THEME_MESSAGE, build_app

    settings = _settings(tmp_path).model_copy(
        update={"telegram_bot_token": "12345:fake_token_for_test", "allowed_user_ids": []}
    )
    tm = ThemeManager(settings)
    t1 = tm.define_theme("Alpha Research")

    ingested: list[tuple] = []
    searched: list[tuple] = []
    monkeypatch.setattr(
        "claire.ingest.service.IngestService.ingest",
        lambda self, *a, **kw: ingested.append((self.theme_id, a)) or None,
    )
    monkeypatch.setattr(
        "claire.ingest.service.IngestService.search",
        lambda self, *a, **kw: searched.append((self.theme_id, a)) or SimpleNamespace(telegram_text=lambda: "결과"),
    )

    app = build_app(settings)
    handlers = {getattr(h.callback, "__name__", ""): h.callback for h in app.handlers[0]}

    def _update(text: str | None = None):
        msg = AsyncMock()
        msg.text = text
        msg.reply_text = AsyncMock(return_value=AsyncMock())
        update = MagicMock()
        update.effective_user = SimpleNamespace(id=100)
        update.effective_chat = SimpleNamespace(id=200)
        update.message = msg
        update.effective_message = msg
        update.update_id = 7
        return update, msg

    # /theme Alpha: 사람이 입력하는 값이라 부분 일치로 선택된다
    upd, msg = _update()
    await handlers["on_theme"](upd, SimpleNamespace(args=["Alpha"]))
    assert f"(#{t1.id})" in msg.reply_text.call_args[0][0]

    tm.delete_theme(t1.id)

    # 삭제된 선택 테마로는 검색하지 않는다
    upd, msg = _update()
    await handlers["on_search"](upd, SimpleNamespace(args=["키워드"]))
    assert msg.reply_text.call_args[0][0] == STALE_THEME_MESSAGE
    assert searched == []

    # 선택은 지워졌으므로 다음 요청은 기본 테마 0으로 처리된다
    upd, msg = _update()
    await handlers["on_search"](upd, SimpleNamespace(args=["키워드"]))
    assert searched and searched[-1][0] == 0

    # 다시 고른 뒤 삭제하면 일반 메시지도 적재하지 않고 거부한다
    t2 = tm.define_theme("Gamma Notes")
    upd, msg = _update()
    await handlers["on_theme"](upd, SimpleNamespace(args=[str(t2.id)]))
    tm.delete_theme(t2.id)
    upd, msg = _update("https://example.com/should-not-ingest")
    await handlers["on_message"](upd, None)
    assert msg.reply_text.call_args[0][0] == STALE_THEME_MESSAGE
    assert ingested == []
