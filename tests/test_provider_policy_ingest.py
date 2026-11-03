"""적재 경로의 프로바이더 정책 적용 (설계 §10.2, §10.3, §11.3, P4 L4-a2i).

멤버 테마 적재는 풀·IngestService·상주 루프·파이프라인 분류기·fetcher STT/VLM·presentation 어디서도 host-cli(agy·codex)나 관리자
gemini 키에 닿지 않는다. 등급 none 멤버는 원문을 보존하고 raw_inbox에 오류 코드를 남긴다. 일 적재 상한·BYOK 동시 작업 상한은
실패로 끝나고, 계정이 다르면 풀 캐시와 프로바이더가 분리된다. 관리자 테마는 지금과 같다.

외부 호출 없음: genai.Client는 가짜, agy·codex 바이너리는 tmp_path의 빈 스크립트, 생성자는 기록만 한다. CLAIRE_*·GEMINI_*를 지운다.
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from claire import cli
from claire.accounts import server_tier
from claire.accounts.provider_policy import (
    ProviderBudgetExceeded,
    ProviderNotAllowed,
    ServerTier,
)
from claire.config import Settings
from claire.extract.provider import MockProvider, UnavailableProvider
from claire.ingest.service import IngestService, IngestServicePool
from claire.ontology.base import Document
from claire.secret_scrub import _reset_for_tests, register_secret
from claire.store import auth_db
from claire.store import db as dbm
from claire.store.provider_keys import ActiveKey
from claire.store.theme import ThemeInfo, ThemeManager

ADMIN_KEY = "AIzaADMINadminADMINadminADMINadmin0001"
MEMBER_KEY = "AIzaMEMBERmemberMEMBERmemberMEMBER0002"
OTHER_KEY = "AIzaOTHERotherOTHERotherOTHERother00003"
CANARY = "canary-member-secret-7f3a9c1e5b"

ADMIN_ID, MEMBER_ID, OTHER_ID = 1, 2, 3
ADMIN_THEME, ADMIN_THEME2, MEMBER_THEME, MEMBER_THEME2, OTHER_THEME = 0, 10, 20, 21, 30

LONG_TEXT = (
    "프로바이더 정책 적재 시험 문서다. 멤버 테마의 원문은 LLM 단계가 실패해도 보존되어야 한다. "
    "이 문단은 추출 가능한 본문 길이를 넘기기 위한 충분한 내용을 담는다. "
) * 6


# --- 격리와 가짜 ---------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch, tmp_path):
    for name in list(os.environ):
        if name.startswith(("CLAIRE_", "GEMINI_", "STT_")) or name in ("CB_DATA_DIR",):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    _reset_for_tests()
    server_tier._ACTIVE_SLOTS.clear()
    yield
    _reset_for_tests()
    server_tier._ACTIVE_SLOTS.clear()


class _FakeClient:
    created: list[str] = []

    def __init__(self, api_key=None, **_kwargs):
        self.api_key = api_key
        _FakeClient.created.append(api_key)
        self.interactions = MagicMock()
        self.models = MagicMock()
        self.files = MagicMock()


@pytest.fixture
def fake_genai(monkeypatch):
    import google.genai as genai

    _FakeClient.created = []
    monkeypatch.setattr(genai, "Client", _FakeClient)
    return _FakeClient


@pytest.fixture
def host_cli(monkeypatch, tmp_path):
    """agy·codex 바이너리가 있는 호스트. CLI 프로바이더 생성은 기록만 하고 실제로 실행하지 않는다."""
    agy = tmp_path / "bin" / "agy"
    codex = tmp_path / "bin" / "codex"
    agy.parent.mkdir(exist_ok=True)
    for b in (agy, codex):
        b.write_text("#!/bin/sh\nexit 0\n")
        b.chmod(0o755)
    import claire.config as config
    import claire.extract.classifier as classifier
    from claire.extract import antigravity_provider, codex_provider

    for mod in (config, classifier):
        monkeypatch.setattr(mod, "find_agy_executable", lambda *_a, **_k: str(agy))
        monkeypatch.setattr(mod, "find_codex_executable", lambda *_a, **_k: str(codex))

    built: list[tuple[str, object]] = []

    def _record(name):
        def __init__(self, settings, *a, **k):
            built.append((name, getattr(settings, "provider_context", None)))
            self.settings = settings
            self.name = name
            self.effort = "medium"

        return __init__

    monkeypatch.setattr(codex_provider.CodexProvider, "__init__", _record("codex"))
    monkeypatch.setattr(antigravity_provider.AntigravityProvider, "__init__", _record("antigravity"))
    return built


@pytest.fixture
def member_keys(monkeypatch):
    """계정별 활성 키(store.provider_keys 대역). 값이 없으면 키 없음 → 서버 등급."""
    from claire.store import provider_keys

    keys: dict[int, ActiveKey] = {}

    def load_active_key(conn, *, account_id, secret_service=None):
        return keys.get(int(account_id))

    def has_key(conn, *, account_id):
        return int(account_id) in keys

    monkeypatch.setattr(provider_keys, "load_active_key", load_active_key)
    monkeypatch.setattr(provider_keys, "has_key", has_key)
    return keys


def _byok(fingerprint: str, key: str) -> ActiveKey:
    return ActiveKey(provider="gemini", fingerprint=fingerprint, api_key=key)


def _insert_account(conn, account_id, username, role="member", status="active"):
    now = time.time()
    conn.execute(
        "INSERT OR REPLACE INTO accounts (id, public_id, username, role, status, display_name, profile_public, "
        "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)",
        (account_id, (1 << 57) + account_id, username, role, status, username, now, now),
    )


@pytest.fixture
def world(tmp_path):
    """claire.db(계정 3·테마 5), auth.db, 테마 레지스트리. 관리자 설정은 codex + 관리자 gemini 키."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    db_file = data_dir / "claire.db"
    conn = dbm.connect(db_file)
    dbm.init_db(conn)
    _insert_account(conn, ADMIN_ID, "admin", role="admin")
    _insert_account(conn, MEMBER_ID, "member")
    _insert_account(conn, OTHER_ID, "other")
    now = time.time()
    themes = [
        ThemeInfo(id=ADMIN_THEME, seq=0, label="관리자 기본", db_path=str(db_file), is_default=True,
                  account_id=ADMIN_ID, number=1 << 56, created_at=now, updated_at=now),
        ThemeInfo(id=ADMIN_THEME2, seq=1, label="관리자 둘", db_path=str(db_file), is_default=False,
                  account_id=ADMIN_ID, number=(1 << 56) + 10, created_at=now, updated_at=now),
        ThemeInfo(id=MEMBER_THEME, seq=2, label="멤버 하나", db_path=str(db_file), is_default=True,
                  account_id=MEMBER_ID, number=(1 << 56) + 20, created_at=now, updated_at=now),
        ThemeInfo(id=MEMBER_THEME2, seq=3, label="멤버 둘", db_path=str(db_file), is_default=False,
                  account_id=MEMBER_ID, number=(1 << 56) + 21, created_at=now, updated_at=now),
        ThemeInfo(id=OTHER_THEME, seq=4, label="다른 멤버", db_path=str(db_file), is_default=True,
                  account_id=OTHER_ID, number=(1 << 56) + 30, created_at=now, updated_at=now),
    ]
    for t in themes:
        conn.execute(
            "INSERT OR REPLACE INTO themes (id, seq, label, description, icon, db_path, is_default, is_public, "
            "is_collaborator_accessible, default_focus, created_at, updated_at, account_id, number) "
            "VALUES (?, ?, ?, '', '📁', ?, ?, 1, 1, ?, ?, ?, ?, ?)",
            (t.id, t.seq, t.label, t.db_path, 1 if t.is_default else 0, t.default_focus, t.created_at,
             t.updated_at, t.account_id, t.number),
        )
    conn.commit()
    conn.close()
    (data_dir / "themes.json").write_text(
        json.dumps({"version": 1, "next_seq": 5, "default_theme_id": 0, "themes": [t.to_dict() for t in themes]}),
        encoding="utf-8",
    )
    auth_path = auth_db.auth_db_path(data_dir)
    auth_db.connect_auth_db(auth_path).close()
    settings = Settings(
        _env_file=None,
        CLAIRE_DATA_DIR=str(data_dir),
        CLAIRE_DB_PATH=str(db_file),
        CLAIRE_MULTI_THEME=True,
        environment="development",
        provider="codex",
        gemini_api_key=ADMIN_KEY,
        gemini_min_interval=0.0,
        gemini_max_retries=0,
        agy_effort="none",
        codex_effort="none",
        stt_provider="gemini",
        vlm_provider="gemini",
        enable_video_transcription=True,
        enable_vlm_transcription=True,
        presentation_enabled=True,
        auto_expand=False,
    )
    return SimpleNamespace(settings=settings, db_file=db_file, data_dir=data_dir, auth_path=auth_path)


def _save_tier(world, tier: ServerTier) -> None:
    conn = dbm.connect(world.db_file)
    try:
        server_tier.save_server_tier(conn, tier, updated_by=ADMIN_ID)
    finally:
        conn.close()


def _theme_settings(world, theme_id):
    return ThemeManager(world.settings).get_settings_for_theme(theme_id, world.settings)


def _inbox_rows(world, theme_id):
    conn = dbm.connect_existing(world.db_file, readonly=True)
    try:
        return [dict(r) for r in conn.execute(
            "SELECT id, status, error, payload, document_id FROM raw_inbox WHERE theme_id=? ORDER BY id", (theme_id,)
        ).fetchall()]
    finally:
        conn.close()


def _doc_count(world, theme_id):
    conn = dbm.connect_existing(world.db_file, readonly=True)
    try:
        return conn.execute("SELECT COUNT(*) FROM documents WHERE theme_id=?", (theme_id,)).fetchone()[0]
    finally:
        conn.close()


def _no_admin_secret(fake_genai, built):
    assert ADMIN_KEY not in fake_genai.created
    assert built == []


# --- 풀 -------------------------------------------------------------------------


def test_pool_member_theme_uses_member_key_never_host_cli(world, host_cli, fake_genai, member_keys):
    member_keys[MEMBER_ID] = _byok("fp000001", MEMBER_KEY)
    pool = IngestServicePool(world.settings, theme_manager=ThemeManager(world.settings))

    svc = pool.get_service(MEMBER_THEME)

    from claire.extract.gemini_provider import GeminiProvider

    assert isinstance(svc.provider, GeminiProvider)
    assert svc.provider._api_key == MEMBER_KEY
    assert svc.provider.credential_key == "byok:fp000001"
    assert svc.provider.usage_hooks is not None
    assert svc.provider_context.source == "byok" and svc.provider_context.account_id == MEMBER_ID
    assert svc.s.gemini_api_key == MEMBER_KEY and svc.s.provider == "gemini"
    assert svc.s.enable_jev is False
    _no_admin_secret(fake_genai, host_cli)

    # 관리자 테마는 지금과 같다: 공유 프로바이더(codex) 하나, 설정 사본 없음, 훅 없음
    admin_svc = pool.get_service(ADMIN_THEME)
    admin_svc2 = pool.get_service(ADMIN_THEME2)
    assert admin_svc.provider is pool.shared_provider is admin_svc2.provider
    assert admin_svc.provider_context.is_admin
    assert getattr(admin_svc.provider, "name", "") == "codex"
    assert [name for name, _ in host_cli] == ["codex"]
    assert host_cli[0][1] is None  # 관리자 경로는 컨텍스트를 입히지 않은 원래 설정
    assert admin_svc.s.gemini_api_key == ADMIN_KEY


def test_pool_cache_separates_accounts_and_tracks_credentials(world, host_cli, fake_genai, member_keys):
    member_keys[MEMBER_ID] = _byok("fp000001", MEMBER_KEY)
    member_keys[OTHER_ID] = _byok("fp000002", OTHER_KEY)
    pool = IngestServicePool(world.settings, theme_manager=ThemeManager(world.settings))

    a1 = pool.get_service(MEMBER_THEME)
    a2 = pool.get_service(MEMBER_THEME2)
    b = pool.get_service(OTHER_THEME)
    assert a1 is pool.get_service(MEMBER_THEME)  # 같은 컨텍스트는 캐시
    assert a1.provider is a2.provider  # 같은 계정·자격의 테마는 프로바이더를 함께 쓴다
    assert b.provider is not a1.provider
    assert b.provider._api_key == OTHER_KEY and a1.provider._api_key == MEMBER_KEY
    assert a1.provider.credential_key != b.provider.credential_key

    # 키 교체(지문 변경) → 새 서비스·새 프로바이더
    member_keys[MEMBER_ID] = _byok("fp000009", OTHER_KEY.replace("OTHER", "NEWKY"))
    a1_new = pool.get_service(MEMBER_THEME)
    assert a1_new is not a1 and a1_new.provider is not a1.provider
    assert a1_new.provider_context.credential_key == "byok:fp000009"

    # 키 삭제 → 서버 등급(none)으로 출처가 바뀌면 다시 만든다. 관리자 키를 빌리지 않는다.
    del member_keys[MEMBER_ID]
    a1_none = pool.get_service(MEMBER_THEME)
    assert a1_none.provider_context.source == "none"
    assert isinstance(a1_none.provider, UnavailableProvider)
    assert a1_none.s.gemini_api_key == ""

    pool.clear()
    assert pool.get_service(OTHER_THEME) is not b
    _no_admin_secret(fake_genai, host_cli)


# --- IngestService·파이프라인 ---------------------------------------------------


def test_tier_none_member_ingest_keeps_raw_and_records_code(world, host_cli, fake_genai, member_keys):
    svc = IngestService(_theme_settings(world, MEMBER_THEME), theme_id=MEMBER_THEME)
    assert svc.provider_context.source == "none"

    rep = svc.ingest(LONG_TEXT, source="cli")

    assert rep.error and rep.error.startswith("provider_not_configured")
    rows = _inbox_rows(world, MEMBER_THEME)
    assert len(rows) == 1 and rows[0]["status"] == "error"
    assert rows[0]["error"].startswith("provider_not_configured")
    assert rows[0]["payload"] == LONG_TEXT  # 원문 보존(A1)
    assert _doc_count(world, MEMBER_THEME) == 1  # 원문 문서는 저장, FTS 동작
    _no_admin_secret(fake_genai, host_cli)


def test_member_pdf_classifier_never_uses_admin_or_host_cli(world, host_cli, fake_genai, member_keys, monkeypatch):
    from claire.extract import classifier

    seen = []
    real = classifier.classify_paper

    def spy(doc, settings=None, *, provider=None):
        seen.append((getattr(settings, "provider_context", None), provider))
        return real(doc, settings, provider=provider)

    monkeypatch.setattr(classifier, "classify_paper", spy)
    svc = IngestService(_theme_settings(world, MEMBER_THEME), theme_id=MEMBER_THEME)
    pdf = Document(title="paper.pdf", raw_text=LONG_TEXT, source_type="pdf", content_hash="h-pdf-1",
                   url="https://example.test/p.pdf", canonical_url="https://example.test/p.pdf")

    rep = svc.ingest("https://example.test/p.pdf", source="cli", prefetched=pdf)

    assert rep.error and rep.error.startswith("provider_not_configured")
    assert seen and all(ctx is not None and not ctx.is_admin for ctx, _ in seen)
    assert all(isinstance(p, UnavailableProvider) for _, p in seen)
    assert _doc_count(world, MEMBER_THEME) == 1
    _no_admin_secret(fake_genai, host_cli)


def test_daily_ingest_limit_fails_and_preserves_raw(world, host_cli, fake_genai, member_keys):
    _save_tier(world, ServerTier(daily_ingests=1))
    svc = IngestService(_theme_settings(world, MEMBER_THEME), theme_id=MEMBER_THEME)

    first = svc.ingest(LONG_TEXT, source="cli")
    second = svc.ingest(LONG_TEXT + " 두 번째", source="cli")

    assert first.error.startswith("provider_not_configured")
    assert second.error.startswith("provider_budget_exceeded")
    rows = _inbox_rows(world, MEMBER_THEME)
    assert [r["status"] for r in rows] == ["error", "error"]
    assert rows[1]["error"].startswith("provider_budget_exceeded")
    assert rows[1]["payload"].endswith("두 번째")
    assert _doc_count(world, MEMBER_THEME) == 1  # 상한 초과분은 문서를 만들지 않는다

    conn = sqlite3.connect(world.auth_path)
    try:
        assert conn.execute("SELECT daily_ingests FROM account_usage WHERE account_id=?", (MEMBER_ID,)).fetchone()[0] == 1
    finally:
        conn.close()


def test_document_quota_blocks_before_insert(world, host_cli, fake_genai, member_keys):
    _save_tier(world, ServerTier(max_documents=0))
    svc = IngestService(_theme_settings(world, MEMBER_THEME), theme_id=MEMBER_THEME)

    rep = svc.ingest(LONG_TEXT, source="cli")

    assert rep.error.startswith("provider_budget_exceeded")
    assert _doc_count(world, MEMBER_THEME) == 0
    assert _inbox_rows(world, MEMBER_THEME)[0]["payload"] == LONG_TEXT


def test_byok_job_slot_limits_concurrency_but_allows_nesting(world, host_cli, fake_genai, member_keys):
    member_keys[MEMBER_ID] = _byok("fp000001", MEMBER_KEY)
    svc = IngestService(_theme_settings(world, MEMBER_THEME), provider=MockProvider(), theme_id=MEMBER_THEME)
    assert svc.provider_context.source == "byok"

    # 다른 작업이 자리를 잡고 있으면 실패(기다리지 않음), 원문은 남는다
    with server_tier.job_slot(svc.provider_context):
        busy = svc.ingest(LONG_TEXT, source="cli")
    assert busy.error.startswith("provider_budget_exceeded")
    assert "concurrent" in busy.error
    assert _inbox_rows(world, MEMBER_THEME)[0]["payload"] == LONG_TEXT

    # 같은 작업 안의 중첩 호출(확장 → 자식 적재)은 한 자리로 본다
    with svc.job_scope():
        ok = svc.ingest(LONG_TEXT + " 중첩", source="cli")
    assert ok.error is None
    assert server_tier._ACTIVE_SLOTS == {}

    # 관리자·서버 등급은 자리 제한이 없다
    admin = IngestService(_theme_settings(world, ADMIN_THEME), provider=MockProvider(), theme_id=ADMIN_THEME)
    with server_tier.job_slot(svc.provider_context):
        assert admin.ingest(LONG_TEXT + " 관리자", source="cli").error is None


def test_expand_feature_gate_records_scrubbed_error(world, host_cli, fake_genai, member_keys):
    _save_tier(world, ServerTier(provider="gemini", features=frozenset()))  # 서버 등급, expand 꺼짐
    svc = IngestService(_theme_settings(world, MEMBER_THEME), provider=MockProvider(), theme_id=MEMBER_THEME)
    assert svc.provider_context.source == "tier"
    conn = dbm.connect(world.db_file)
    try:
        dbm.enqueue_expand(conn, "doc-x", theme_id=MEMBER_THEME)
    finally:
        conn.close()

    out = svc.run_expand_queue(limit=1)

    assert out and out[0]["error"].startswith("provider_not_allowed")
    with pytest.raises(ProviderNotAllowed):
        svc.expand_document("doc-x")


def test_raw_inbox_error_has_no_canary_key(world, member_keys):
    member_keys[MEMBER_ID] = _byok("fp000001", CANARY)
    register_secret(CANARY)  # GeminiProvider가 멤버 키를 등록하는 것과 같다

    class LeakyProvider(MockProvider):
        def extract(self, doc, *_a, **_k):
            raise RuntimeError(f"upstream rejected key={CANARY} Authorization: Bearer {CANARY}")

    svc = IngestService(_theme_settings(world, MEMBER_THEME), provider=LeakyProvider(), theme_id=MEMBER_THEME)
    rep = svc.ingest(LONG_TEXT, source="cli")

    assert rep.error and CANARY not in rep.error
    rows = _inbox_rows(world, MEMBER_THEME)
    assert rows and all(CANARY not in (r["error"] or "") for r in rows)
    jobs_dir = world.data_dir / "active_jobs"
    if jobs_dir.exists():
        for f in jobs_dir.glob("*.json"):
            assert CANARY not in f.read_text(encoding="utf-8")


# --- fetcher STT·VLM ----------------------------------------------------------


def test_fetcher_stt_vlm_use_member_context(world, host_cli, fake_genai, member_keys, monkeypatch):
    member_keys[MEMBER_ID] = _byok("fp000001", MEMBER_KEY)
    from claire.extract.transcript.factory import get_transcript_provider
    from claire.extract.vision.factory import get_vision_provider
    from claire.ingest import pipeline
    from claire.ingest.fetchers.base import attach_usage_hooks, fetch_settings

    captured = {}

    def fake_fetch(payload, full_content=False):
        s = fetch_settings(None, lambda: pytest.fail("fetcher fell back to global settings"))
        stt = attach_usage_hooks(get_transcript_provider(s), s)
        vlm = attach_usage_hooks(get_vision_provider(s), s)
        captured.update(settings=s, stt=stt, vlm=vlm)
        return Document(title="video", raw_text=LONG_TEXT, source_type="text", content_hash="h-v-1")

    monkeypatch.setattr(pipeline, "default_fetch", fake_fetch)
    svc = IngestService(_theme_settings(world, MEMBER_THEME), provider=MockProvider(), theme_id=MEMBER_THEME)
    svc.ingest("https://example.test/video", source="cli")

    s, stt, vlm = captured["settings"], captured["stt"], captured["vlm"]
    assert s.provider_context.account_id == MEMBER_ID and s.gemini_api_key == MEMBER_KEY
    assert stt.provider_context.api_key == MEMBER_KEY and stt.usage_hooks is not None
    assert vlm.provider_context.api_key == MEMBER_KEY and vlm.usage_hooks is not None
    _no_admin_secret(fake_genai, host_cli)

    # 서버 등급 none: STT·VLM은 자리표시자, 관리자 키·mock으로 대체되지 않는다
    captured.clear()
    del member_keys[MEMBER_ID]
    svc_none = IngestService(_theme_settings(world, MEMBER_THEME2), provider=MockProvider(), theme_id=MEMBER_THEME2)
    svc_none.ingest("https://example.test/video2", source="cli")
    assert captured["settings"].gemini_api_key == ""
    assert type(captured["stt"]).__name__ == "UnavailableTranscriptProvider"
    assert type(captured["vlm"]).__name__ == "UnavailableVisionProvider"
    _no_admin_secret(fake_genai, host_cli)


def test_fetch_settings_outside_job_is_host_operator(world):
    from claire.ingest.fetchers.base import bound_fetch_settings, fetch_settings

    assert fetch_settings(None, lambda: "global") == "global"
    with bound_fetch_settings("bound"):
        assert fetch_settings(None, lambda: "global") == "bound"
        assert fetch_settings("explicit", lambda: "global") == "explicit"


# --- 상주 루프 ------------------------------------------------------------------


def test_loop_services_carry_owner_context(world, host_cli, fake_genai, member_keys):
    state = cli._ThemeLoopState()

    services = dict((t.id, svc) for t, svc in cli._theme_services_for_cycle(world.settings, state, IngestService))
    assert set(services) == {ADMIN_THEME, ADMIN_THEME2, MEMBER_THEME, MEMBER_THEME2, OTHER_THEME}
    member = services[MEMBER_THEME]
    assert member.provider_context.account_id == MEMBER_ID and member.provider_context.source == "none"
    assert isinstance(member.provider, UnavailableProvider)
    assert services[ADMIN_THEME].provider_context.is_admin
    assert [name for name, ctx in host_cli if ctx is not None and not ctx.is_admin] == []

    # 멤버가 키를 등록하면 다음 cycle에 서비스를 새로 만든다
    member_keys[MEMBER_ID] = _byok("fp000001", MEMBER_KEY)
    again = dict((t.id, svc) for t, svc in cli._theme_services_for_cycle(world.settings, state, IngestService))
    assert again[MEMBER_THEME] is not member
    assert again[MEMBER_THEME].provider._api_key == MEMBER_KEY
    assert again[ADMIN_THEME] is services[ADMIN_THEME]
    assert ADMIN_KEY not in fake_genai.created


def test_recover_cycle_member_failure_is_coded_and_admin_unchanged(world, monkeypatch):
    settings = world.settings.model_copy(update={"provider": "mock"})
    conn = dbm.connect(world.db_file)
    for tid in (ADMIN_THEME2, MEMBER_THEME):
        iid = dbm.log_inbox(conn, source="cli", payload=LONG_TEXT + f" {tid}", kind="text", theme_id=tid)
        dbm.update_inbox(conn, iid, status="error", error="temporary", theme_id=tid)
    conn.execute("UPDATE raw_inbox SET next_retry_at=?", (time.time() - 100,))
    conn.commit()
    conn.close()

    args = SimpleNamespace(batch=10, max_attempts=5, base_delay=1.0)
    cycle = cli._run_recover_cycle(settings, args, cli._ThemeLoopState(), IngestService)

    by_theme = {r["_theme_id"]: r for r in cycle["results"]}
    assert by_theme[ADMIN_THEME2]["status"] in ("done", "duplicate")
    assert by_theme[MEMBER_THEME]["status"] == "error"
    assert by_theme[MEMBER_THEME]["error"].startswith("provider_not_configured")


# --- presentation --------------------------------------------------------------


def test_presentation_uses_theme_owner_context(world, host_cli, fake_genai, member_keys):
    from claire.presentation.service import PresentationService

    conn = dbm.connect(world.db_file)
    conn.execute(
        "INSERT INTO documents (id, theme_id, title, raw_text, content_hash, source_type, fetched_at) "
        "VALUES ('d1', ?, 't', ?, 'h', 'text', 0)",
        (MEMBER_THEME, LONG_TEXT),
    )
    conn.commit()
    try:
        # 서버 등급 none(기능 꺼짐) → 기능 게이트에서 실패, 관리자 프로바이더를 만들지 않는다
        svc = PresentationService(settings=world.settings, theme_id=MEMBER_THEME)
        with pytest.raises(ProviderNotAllowed):
            asyncio.run(svc.compose_presentation(conn, "d1"))
        _no_admin_secret(fake_genai, host_cli)

        # BYOK → 멤버 키의 Gemini, 원장 훅
        member_keys[MEMBER_ID] = _byok("fp000001", MEMBER_KEY)
        ctx = svc.provider_context(conn)
        prov = svc._provider_for_context(ctx)
        assert prov._api_key == MEMBER_KEY and prov.usage_hooks is not None
        _no_admin_secret(fake_genai, host_cli)

        # 관리자 테마는 지금과 같다(codex)
        admin_svc = PresentationService(settings=world.settings, theme_id=ADMIN_THEME)
        admin_prov = admin_svc._provider_for_context(admin_svc.provider_context(conn))
        assert getattr(admin_prov, "name", "") == "codex"
    finally:
        conn.close()


def test_admin_theme_ingest_unchanged_without_ledger(world, tmp_path):
    """관리자 테마는 원장·등급과 무관하게 지금처럼 적재한다(auth.db가 없어도)."""
    world.auth_path.unlink()
    settings = world.settings.model_copy(update={"provider": "mock"})
    svc = IngestService(ThemeManager(settings).get_settings_for_theme(ADMIN_THEME2, settings), theme_id=ADMIN_THEME2)
    assert svc.provider_context.is_admin
    assert svc.s.provider_context is None  # 관리자 설정은 사본을 만들지 않는다

    rep = svc.ingest(LONG_TEXT, source="cli", effort="high")

    assert rep.error is None and rep.document_id
    assert rep.effort == "high"


def test_budget_error_type_exposed(world, member_keys):
    """ProviderBudgetExceeded가 정책 오류 계층으로 기록된다(코드 접두)."""
    from claire.ingest.pipeline import error_text

    assert error_text(ProviderBudgetExceeded("x", remedy="r")) == "provider_budget_exceeded: x (복구: r)"
    assert error_text(RuntimeError(f"boom {MEMBER_KEY}")) == "boom [REDACTED]"
