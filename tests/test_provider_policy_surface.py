"""HTTP 표면·텔레그램의 프로바이더 정책 (설계 §10.1–§10.4, §11.3, P4 L4-a2s).

- 네트워크 모드 멤버: ``/research``는 항상 403(관리자 전용), ``/synthesize``·프레젠테이션 저작은 등급 기능 또는 BYOK일 때만.
- 정책 오류 응답은 ``{"error": code, "remedy": ...}``: not allowed 403, 예산·한도 429, 프로바이더 없음·키 거부 409.
- 멤버는 ``max_themes``를 넘겨 테마를 만들 수 없다. 관리자 경로는 그대로다.
- 오류 응답과 로그에 카나리아 키가 남지 않는다.
- 테마 목록의 ``reembed_required``는 owner 요청에만 실린다.
- 텔레그램 봇은 관리자 계정 소유 테마만 고른다.

외부 호출 없음: 프로바이더·연구·합성·프레젠테이션 저작은 가짜로 바꾼다. 상태는 ``tmp_path``에만 쓴다.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from starlette.testclient import TestClient

from claire import graphview, secret_scrub
from claire.accounts import provider_policy as policy
from claire.accounts import server_tier
from claire.api import server
from claire.config import Settings
from claire.store import accounts as accounts_store
from claire.store import auth_db
from claire.store import db as dbm
from claire.store import provider_keys
from claire.store.secrets import reset_secret_service
from claire.store.theme import ThemeInfo, ThemeManager, ThemeNotFound
from claire.store.token_store import get_token_store, reset_token_stores

PUBLIC_URL = "http://127.0.0.1:8765"
TEST_MASTER_KEY_HEX = "22" * 32
# 키 모양 패턴에 걸리지 않는 카나리아(등록 값 치환을 확인한다)와 키 모양 카나리아(패턴 치환을 확인한다).
CANARY = "canary-surface-key-5e4d3c2b1a09f8e7"
CANARY_SHAPED = "AIza" + "SyCanaryShapedKey0123456789abcdefg"
SERVER_KEY = "canary-server-tier-key-0011223344"


@pytest.fixture(autouse=True)
def _isolate_env_and_secrets(monkeypatch: pytest.MonkeyPatch):
    for key in list(os.environ):
        if key.startswith(("CLAIRE_", "GEMINI_", "GOOGLE_API")):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("CLAIRE_SECRET_KEY", TEST_MASTER_KEY_HEX)
    reset_secret_service()
    reset_token_stores()
    secret_scrub._reset_for_tests()
    yield
    reset_secret_service()
    reset_token_stores()
    secret_scrub._reset_for_tests()


class _FakeProvider:
    """외부 호출을 하지 않는 프로바이더. 호출되면 기록한다."""

    def __init__(self, settings: Any) -> None:
        self.settings = settings
        self.name = str(getattr(settings, "provider", "") or "fake")
        self.embed_model = str(getattr(settings, "gemini_embed_model", "") or "")
        self.calls: list[str] = []

    def research(self, *args: Any, **kwargs: Any) -> dict:  # pragma: no cover - 닿으면 실패
        raise AssertionError("member reached provider.research")


@pytest.fixture(autouse=True)
def _fake_member_providers(monkeypatch: pytest.MonkeyPatch):
    """멤버 컨텍스트 프로바이더(BYOK·서버 등급)를 가짜로 만든다(Gemini 클라이언트를 만들지 않는다)."""
    import claire.ingest.service as ingest_service

    real = ingest_service.get_provider

    def _get_provider(settings: Any) -> Any:
        ctx = policy.context_of(settings)
        if ctx is not None and not ctx.is_admin and str(getattr(settings, "provider", "")) == "gemini":
            return _FakeProvider(settings)
        return real(settings)

    monkeypatch.setattr(ingest_service, "get_provider", _get_provider)


def _add_account(conn: sqlite3.Connection, account_id: int, username: str, role: str) -> None:
    now = time.time()
    conn.execute(
        "INSERT INTO accounts (id, public_id, username, role, status, display_name, profile_public, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, 'active', ?, 1, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET username=excluded.username, role=excluded.role, status=excluded.status",
        (account_id, (1 << 57) + account_id, username, role, username.capitalize(), now, now),
    )
    conn.commit()


def _setup_theme(tm: ThemeManager, db_file: Path, *, theme_id: int, seq: int, label: str, account_id: int,
                 is_default: bool = False, presentation: bool = False) -> ThemeInfo:
    conn = dbm.connect(db_file)
    now = time.time()
    try:
        conn.execute(
            "INSERT INTO themes (id, seq, number, label, description, icon, is_default, is_public, "
            "is_collaborator_accessible, account_id, db_path, default_focus, presentation_enabled, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, '', '📁', ?, 1, 1, ?, ?, '', ?, ?, ?)",
            (theme_id, seq, (account_id << 56) | seq, label, 1 if is_default else 0, account_id,
             str(db_file), 1 if presentation else 0, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    tm.reload()
    tm._save_registry()
    return tm.get_theme(theme_id)


def _build_env(tmp_path: Path, *, gemini_key: str = "") -> SimpleNamespace:
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)
    db_file = data_dir / "claire.db"
    conn = dbm.connect(db_file)
    dbm.init_db(conn)
    _add_account(conn, 1, "admin", "admin")
    _add_account(conn, 2, "alice", "member")
    _add_account(conn, 3, "bob", "member")
    accounts_store.AccountStore(conn).set_setting("mode", "network", updated_by=1)
    conn.close()

    kwargs: dict[str, Any] = dict(
        CLAIRE_DB_PATH=str(db_file),
        CLAIRE_DATA_DIR=str(data_dir),
        CLAIRE_PROVIDER="mock",
        CLAIRE_ENVIRONMENT="development",
        CLAIRE_PUBLIC_URL=PUBLIC_URL,
        CLAIRE_ANONYMOUS_READONLY=True,
        CLAIRE_MULTI_THEME=True,
    )
    if gemini_key:
        kwargs["GEMINI_API_KEY"] = gemini_key
    settings = Settings(**kwargs)
    tm = ThemeManager(settings)
    admin_theme = _setup_theme(tm, db_file, theme_id=10, seq=1, label="Admin Notes", account_id=1, presentation=True)
    alice_def = _setup_theme(tm, db_file, theme_id=20, seq=2, label="Alice Default", account_id=2, is_default=True)
    alice_pub = _setup_theme(tm, db_file, theme_id=21, seq=3, label="Alice Talks", account_id=2, presentation=True)
    bob_def = _setup_theme(tm, db_file, theme_id=30, seq=4, label="Bob Default", account_id=3, is_default=True)

    toks = get_token_store(data_dir=data_dir)
    admin_tok = toks.create_token(name="admin-owner", scope="owner", account_id=1)["token"]
    alice_tok = toks.create_token(name="alice-owner", scope="owner", account_id=2)["token"]
    bob_tok = toks.create_token(name="bob-owner", scope="owner", account_id=3)["token"]

    app = server.create_app(settings, SimpleNamespace(provider=SimpleNamespace(name="mock")))
    client = TestClient(app, base_url=PUBLIC_URL)
    return SimpleNamespace(
        settings=settings, db_file=db_file, data_dir=data_dir, client=client, tm=tm,
        admin_theme=admin_theme, alice_def=alice_def, alice_pub=alice_pub, bob_def=bob_def,
        admin=admin_tok, alice=alice_tok, bob=bob_tok, responses=[],
    )


@pytest.fixture
def env(tmp_path: Path):
    e = _build_env(tmp_path)
    with e.client:
        yield e


def _h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _post(e: SimpleNamespace, path: str, token: str, **kwargs: Any):
    r = e.client.post(path, headers=_h(token), **kwargs)
    e.responses.append(r)
    return r


def _set_tier(e: SimpleNamespace, **fields: Any) -> None:
    conn = dbm.connect_existing(e.db_file)
    try:
        server_tier.save_server_tier(conn, policy.ServerTier(**fields), updated_by=1)
    finally:
        conn.close()


def _register_byok(e: SimpleNamespace, account_id: int = 2, api_key: str = CANARY) -> None:
    conn = auth_db.connect_auth_db(auth_db.auth_db_path(e.data_dir))
    try:
        provider_keys.put_key(conn, account_id=account_id, provider="gemini", api_key=api_key, status="active")
    finally:
        conn.close()


def _assert_policy(r, status: int, code: str) -> None:
    assert r.status_code == status, r.text
    body = r.json()
    assert set(body) == {"error", "remedy"}
    assert body["error"] == code and body["remedy"]


@pytest.fixture
def research_calls(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    import claire.expand.research as research_mod
    from claire.extract.antigravity_provider import AntigravityProvider

    calls: list[Any] = []

    def _fake(settings: Any, provider: Any, **kwargs: Any) -> dict:
        calls.append((settings, provider, kwargs))
        return {"ok": True, "answer": "fake"}

    def _never(*_a: Any, **_k: Any) -> dict:
        raise AssertionError("AntigravityProvider.research reached")

    monkeypatch.setattr(research_mod, "contextual_research", _fake)
    monkeypatch.setattr(AntigravityProvider, "research", _never)
    return calls


@pytest.fixture
def synth_calls(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    calls: list[Any] = []

    def _fake(conn: Any, provider: Any, entity_ids: list, query: Any, theme_id: int = 0) -> dict:
        calls.append((provider, entity_ids, theme_id))
        return {"answer": "synthesized"}

    monkeypatch.setattr(graphview, "synthesize", _fake, raising=False)
    return calls


# --- /research: 관리자 전용 (§10.1) -------------------------------------------------


def test_member_research_is_403_even_with_byok(env, research_calls):
    for setup in (lambda: None, lambda: _register_byok(env)):
        setup()
        r = _post(env, f"/@alice/research?theme={env.alice_pub.id}", env.alice, json={"query": "why"})
        _assert_policy(r, 403, "provider_not_allowed")
    # 세션(쿠키) 경로도 같다.
    conn = dbm.connect_existing(env.db_file)
    try:
        sess = accounts_store.create_account_session(conn, account_id=2, scope="owner", now=time.time())
    finally:
        conn.close()
    env.client.cookies.clear()
    r = env.client.post(
        f"/@alice/research?theme={env.alice_pub.id}",
        headers={"Cookie": f"claire_session={sess}", "Origin": PUBLIC_URL},
        json={"query": "why"},
    )
    env.client.cookies.clear()
    _assert_policy(r, 403, "provider_not_allowed")
    assert research_calls == []


def test_admin_research_path_unchanged(env, research_calls):
    r = _post(env, f"/@admin/research?theme={env.admin_theme.id}", env.admin, json={"query": "why"})
    assert r.status_code == 200, r.text
    assert '"done": true' in r.text and "fake" in r.text
    assert len(research_calls) == 1


# --- /synthesize·프레젠테이션: 등급 기능 또는 BYOK (§10.3) ---------------------------


def test_synthesize_denied_when_tier_feature_off(env, synth_calls):
    r = _post(env, f"/@alice/synthesize?theme={env.alice_pub.id}", env.alice, json={"node_ids": ["e1"]})
    _assert_policy(r, 403, "provider_not_allowed")
    assert synth_calls == []


def test_synthesize_allowed_with_byok_and_uses_member_provider(env, synth_calls):
    _register_byok(env)
    r = _post(env, f"/@alice/synthesize?theme={env.alice_pub.id}", env.alice, json={"node_ids": ["e1"]})
    assert r.status_code == 200, r.text
    assert r.json()["answer"] == "synthesized"
    provider = synth_calls[0][0]
    assert isinstance(provider, _FakeProvider)  # 관리자 공유 프로바이더(mock)가 아니다
    assert CANARY not in r.text


def test_synthesize_allowed_when_tier_feature_on(tmp_path, synth_calls):
    e = _build_env(tmp_path, gemini_key=SERVER_KEY)
    with e.client:
        _set_tier(e, provider="gemini", model="gemini-x", monthly_tokens=1000, features=frozenset({"synthesize"}))
        r = _post(e, f"/@alice/synthesize?theme={e.alice_pub.id}", e.alice, json={"node_ids": ["e1"]})
        assert r.status_code == 200, r.text
        # 같은 등급에서 presentation은 꺼져 있다.
        r = _post(e, f"/@alice/document/presentation/compose?theme={e.alice_pub.id}", e.alice, json={"id": "doc_x"})
        _assert_policy(r, 403, "provider_not_allowed")
        assert SERVER_KEY not in "".join(x.text for x in e.responses)


def test_presentation_authoring_denied_for_member_without_feature(env, monkeypatch):
    from claire.presentation import PresentationService

    async def _never(*_a: Any, **_k: Any) -> dict:
        raise AssertionError("presentation authoring reached")

    monkeypatch.setattr(PresentationService, "compose_presentation", _never)
    monkeypatch.setattr(PresentationService, "compile_presentation", _never)
    monkeypatch.setattr(PresentationService, "get_or_create_presentation", _never)
    for route in ("generate", "compose", "compile"):
        r = _post(env, f"/@alice/document/presentation/{route}?theme={env.alice_pub.id}", env.alice, json={"id": "doc_x"})
        _assert_policy(r, 403, "provider_not_allowed")
    r = env.client.put(
        f"/@alice/document/presentation/adoc?theme={env.alice_pub.id}&id=doc_x",
        headers=_h(env.alice), json={"presentation_adoc": "= x"},
    )
    _assert_policy(r, 403, "provider_not_allowed")


# --- 테마 한도 (§10.3) --------------------------------------------------------------


def test_member_theme_quota_and_admin_unchanged(env):
    _set_tier(env, max_themes=2)  # alice는 이미 2개
    r = _post(env, "/@alice/themes", env.alice, json={"label": "Third"})
    _assert_policy(r, 429, "provider_budget_exceeded")
    _set_tier(env, max_themes=3)
    assert _post(env, "/@alice/themes", env.alice, json={"label": "Third"}).status_code == 201
    # 관리자는 한도를 받지 않는다.
    _set_tier(env, max_themes=1)
    assert _post(env, "/@admin/themes", env.admin, json={"label": "Admin Two"}).status_code == 201


# --- 정책 오류 형식 -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("exc_type", "status"),
    [
        (policy.ProviderNotAllowed, 403),
        (policy.ProviderBudgetExceeded, 429),
        (policy.ProviderNotConfigured, 409),
        (policy.ProviderKeyRejected, 409),
    ],
)
def test_policy_error_response_format(exc_type, status):
    r = server.policy_error_response(exc_type(f"account 2 detail {CANARY}", remedy=f"fix it {CANARY_SHAPED}"))
    assert r.status_code == status
    import json

    body = json.loads(r.body)
    assert body["error"] == exc_type.code and set(body) == {"error", "remedy"}
    assert CANARY not in r.body.decode() and CANARY_SHAPED not in r.body.decode()


# --- 카나리아 스크럽 (§11.3) --------------------------------------------------------


def test_error_responses_and_logs_never_carry_keys(env, monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    secret_scrub.register_secret(CANARY)
    from claire.presentation import PresentationService

    async def _boom(*_a: Any, **_k: Any) -> dict:
        raise RuntimeError(f"upstream rejected key {CANARY} and {CANARY_SHAPED}")

    monkeypatch.setattr(PresentationService, "compose_presentation", _boom)
    monkeypatch.setattr(PresentationService, "compile_presentation", _boom)
    monkeypatch.setattr(PresentationService, "get_or_create_presentation", _boom)
    for route in ("generate", "compose", "compile"):
        r = _post(env, f"/@admin/document/presentation/{route}?theme={env.admin_theme.id}", env.admin, json={"id": "doc_x"})
        assert r.status_code == 500
        assert "[REDACTED]" in r.text

    def _ingest_boom(*_a: Any, **_k: Any):
        raise RuntimeError(f"bad key {CANARY}")

    import claire.ingest.service as ingest_service

    monkeypatch.setattr(ingest_service.IngestService, "regenerate_components", _ingest_boom)
    r = _post(env, f"/@admin/regenerate?theme={env.admin_theme.id}", env.admin, json={"doc_id": "doc_x"})
    assert r.status_code == 500 and "[REDACTED]" in r.text

    logging.getLogger("claire.provider.test").warning("provider said %s / %s", CANARY, CANARY_SHAPED)
    blob = "".join(x.text for x in env.responses)
    for value in (CANARY, CANARY_SHAPED):
        assert value not in blob
        assert value not in caplog.text
        assert all(value not in rec.getMessage() for rec in caplog.records)


# --- re-embed 표시 (§10.4) ----------------------------------------------------------


def _pin(e: SimpleNamespace, theme_id: int, provider: str, model: str) -> None:
    conn = dbm.connect_existing(e.db_file)
    try:
        conn.execute("UPDATE themes SET embed_provider=?, embed_model=? WHERE id=?", (provider, model, theme_id))
        conn.commit()
    finally:
        conn.close()


def _themes_by_id(r) -> dict[int, dict]:
    assert r.status_code == 200, r.text
    return {int(t["id"]): t for t in r.json()["themes"]}


def test_reembed_required_only_for_owner(env):
    _register_byok(env)
    _pin(env, env.alice_pub.id, "gemini", "old-embedding-model")
    owner = _themes_by_id(env.client.get("/@alice/themes", headers=_h(env.alice)))
    assert owner[env.alice_pub.id]["reembed_required"] is True
    assert owner[env.alice_def.id]["reembed_required"] is False  # 아직 고정되지 않음

    for headers in (_h(env.bob), {}):
        visitor = _themes_by_id(env.client.get("/@alice/themes", headers=headers))
        assert all("reembed_required" not in t for t in visitor.values())

    # 관리자 테마: mock 임베딩으로 고정되어 있으면 불일치 없음, 다른 모델이면 표시.
    _pin(env, env.admin_theme.id, "mock", "mock-sha256")
    admin = _themes_by_id(env.client.get("/@admin/themes", headers=_h(env.admin)))
    assert admin[env.admin_theme.id]["reembed_required"] is False
    _pin(env, env.admin_theme.id, "gemini", "gemini-embedding-001")
    admin = _themes_by_id(env.client.get("/@admin/themes", headers=_h(env.admin)))
    assert admin[env.admin_theme.id]["reembed_required"] is True


# --- 텔레그램: 관리자 테마만 (§10.1) -------------------------------------------------


def test_telegram_theme_selection_limited_to_admin_themes(env):
    from claire.telegram_bot import AdminThemeScope, parse_message_theme

    scoped = AdminThemeScope(env.tm)
    assert parse_message_theme("hello #Alice", scoped) == ("hello #Alice", None)
    assert parse_message_theme(f"hello #{env.alice_pub.id}", scoped)[1] is None
    assert parse_message_theme("note #Admin", scoped) == ("note", env.admin_theme.id)
    # 감싸지 않은 관리자에서도 account_id를 주면 같다.
    assert parse_message_theme("hello #Alice", env.tm, account_id=1)[1] is None

    with pytest.raises(ThemeNotFound):
        scoped.get_theme(env.alice_pub.id)
    assert scoped.get_theme(env.admin_theme.id).id == env.admin_theme.id
    assert {t.account_id for t in scoped.list_themes()} == {1}

    from claire.ingest.service import IngestServicePool

    pool = IngestServicePool(env.settings, theme_manager=scoped, account_id=1)
    with pytest.raises(ThemeNotFound):
        pool.get_service(env.alice_pub.id)
    svc = pool.get_service(env.admin_theme.id)
    assert svc.provider_context.is_admin
