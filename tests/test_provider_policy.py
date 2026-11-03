"""프로바이더 정책 코어와 팩토리 (설계 §10.1, §10.2, §11.3, §15 S10).

멤버 컨텍스트는 어떤 팩토리로도 host-cli(agy·codex)나 관리자 키에 닿지 않고, 키 거부·예산 소진은 폴백 없이 실패한다.
네트워크 호출은 하지 않는다(genai.Client는 가짜). 환경변수는 CLAIRE_*·GEMINI_*를 지우고 tmp_path만 쓴다(§18.2-6).
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from claire.accounts import provider_policy as pp
from claire.accounts.provider_policy import (
    ProviderBudgetExceeded,
    ProviderContext,
    ProviderKeyRejected,
    ProviderNotAllowed,
    ProviderNotConfigured,
    ServerTier,
)
from claire.config import Settings
from claire.store.provider_keys import ActiveKey, ProviderKeyError

ADMIN_KEY = "AIzaADMINadminADMINadminADMINadmin0001"
MEMBER_KEY = "AIzaMEMBERmemberMEMBERmemberMEMBER0002"
ADMIN_ID, MEMBER_ID, SUSPENDED_ID, DELETING_ID, PURGED_ID = 1, 2, 3, 4, 5


# --- 격리와 가짜 ---------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch, tmp_path):
    for name in list(os.environ):
        if name.startswith(("CLAIRE_", "GEMINI_", "STT_")) or name in ("CB_DATA_DIR",):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)


class _FakeClient:
    """genai.Client 대역. 만든 키를 기록하고, 호출 응답은 테스트가 정한다."""

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
def host_cli_present(monkeypatch, tmp_path):
    """agy·codex 바이너리가 호스트에 있는 상태."""
    agy = tmp_path / "bin" / "agy"
    codex = tmp_path / "bin" / "codex"
    agy.parent.mkdir(exist_ok=True)
    for b in (agy, codex):
        b.write_text("#!/bin/sh\nexit 0\n")
        b.chmod(0o755)
    import claire.config as config
    import claire.extract.classifier as classifier

    for mod in (config, classifier):
        monkeypatch.setattr(mod, "find_agy_executable", lambda *_a, **_k: str(agy))
        monkeypatch.setattr(mod, "find_codex_executable", lambda *_a, **_k: str(codex))
    return agy, codex


def _settings(tmp_path, **kwargs) -> Settings:
    base = dict(
        _env_file=None,
        db_path=str(tmp_path / "claire.db"),
        environment="development",
        provider="codex",
        gemini_api_key=ADMIN_KEY,
        gemini_min_interval=0.0,
        gemini_max_retries=2,
        agy_effort="none",  # 관리자 경로라면 agy가 최저 effort 후보가 될 상태
        codex_effort="none",
    )
    base.update(kwargs)
    return Settings(**base)


def _ctx(source="byok", *, features=(), max_effort="low", api_key=MEMBER_KEY, provider="gemini",
         credential_key=None, effort="medium") -> ProviderContext:
    if source == "none":
        provider, api_key = "none", ""
    return ProviderContext(
        account_id=MEMBER_ID,
        is_admin=False,
        source=source,
        provider=provider,
        model="gemini-test",
        embed_model="embed-test",
        effort=effort,
        credential_key=credential_key or ("byok:abcd1234" if source == "byok" else "tier"),
        tier=ServerTier(provider="gemini" if source == "tier" else "none", features=frozenset(features),
                        max_effort=max_effort),
        api_key=api_key,
    )


def _member_settings(tmp_path, ctx=None, **kwargs) -> Settings:
    return pp.settings_for_context(_settings(tmp_path, **kwargs), ctx or _ctx())


@pytest.fixture
def account_db(tmp_path):
    db = tmp_path / "claire.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE accounts (id INTEGER PRIMARY KEY, role TEXT NOT NULL, status TEXT NOT NULL)")
    conn.executemany(
        "INSERT INTO accounts (id, role, status) VALUES (?, ?, ?)",
        [(ADMIN_ID, "admin", "active"), (MEMBER_ID, "member", "active"), (SUSPENDED_ID, "member", "suspended"),
         (DELETING_ID, "member", "deleting"), (PURGED_ID, "member", "purged")],
    )
    conn.commit()
    conn.close()
    from claire.store import auth_db

    auth_path = auth_db.auth_db_path(tmp_path)
    auth_db.connect_auth_db(auth_path).close()
    return db, auth_path


@pytest.fixture
def keys(monkeypatch):
    """store.provider_keys(L4-b)와 server_tier(L4-c)를 시그니처대로 대역한다."""
    from claire.accounts import server_tier
    from claire.store import provider_keys

    state = SimpleNamespace(active=None, registered=False, error=None, tier=ServerTier(), tier_reads=0)

    def load_active_key(conn, *, account_id, secret_service=None):
        if state.error is not None:
            raise state.error
        return state.active

    def has_key(conn, *, account_id):
        return state.registered or state.active is not None

    def load_server_tier(conn):
        state.tier_reads += 1
        return state.tier

    monkeypatch.setattr(provider_keys, "load_active_key", load_active_key)
    monkeypatch.setattr(provider_keys, "has_key", has_key)
    monkeypatch.setattr(server_tier, "load_server_tier", load_server_tier)
    return state


def _resolve(tmp_path, account_db, account_id, **kwargs):
    db, auth_path = account_db
    return pp.resolve_provider_context(
        account_id, settings=_settings(tmp_path, **kwargs), db_path=db, auth_db_path=auth_path
    )


class _Err(Exception):
    def __init__(self, code, msg=""):
        super().__init__(msg or f"{code} error")
        self.code = code


class _Hooks:
    def __init__(self, budget=True):
        self.events: list[tuple] = []
        self.budget = budget

    def before_call(self):
        self.events.append(("before",))
        if not self.budget:
            raise ProviderBudgetExceeded("monthly tokens exhausted")

    def after_call(self, tokens_in, tokens_out):
        self.events.append(("after", tokens_in, tokens_out))


# --- 분류와 컨텍스트 ------------------------------------------------------------


@pytest.mark.parametrize("name", ["", None, "brand-new-cli", "openrouter", "unavailable", "none", "AGY "])
def test_unknown_or_empty_provider_is_host_cli(name):
    assert pp.classify_provider(name) == "host-cli"


def test_known_classes():
    assert pp.classify_provider("Gemini") == "api-key"
    assert pp.classify_provider("codex") == "host-cli"
    assert pp.classify_provider("antigravity") == "host-cli"
    assert pp.classify_provider("mock") == "mock"


def test_require_provider_allowed_blocks_host_cli_for_members_only():
    member = _ctx()
    for name in ("codex", "antigravity", "agy", "something-new", ""):
        with pytest.raises(ProviderNotAllowed):
            pp.require_provider_allowed(member, name)
    pp.require_provider_allowed(member, "gemini")
    pp.require_provider_allowed(None, "codex")
    pp.require_provider_allowed(pp.admin_context(_settings_noop()), "codex")


def _settings_noop() -> Settings:
    return Settings(_env_file=None, provider="codex", gemini_api_key=ADMIN_KEY)


def test_require_feature():
    pp.require_feature(None, "research")
    with pytest.raises(ProviderNotAllowed):
        pp.require_feature(_ctx("byok"), "research")
    pp.require_feature(_ctx("byok"), "stt")
    with pytest.raises(ProviderNotAllowed):
        pp.require_feature(_ctx("tier", features=()), "expand")
    pp.require_feature(_ctx("tier", features=("expand",)), "expand")
    with pytest.raises(ProviderNotAllowed):
        pp.require_feature(_ctx("none"), "expand")


@pytest.mark.parametrize(
    ("effort", "expected"),
    [("high", "low"), ("medium", "low"), ("low", "low"), ("minimal", "minimal"), ("none", "none"),
     ("", "low"), ("xhigh", "low"), ("8192", "low")],
)
def test_clamp_effort_tier(effort, expected):
    assert pp.clamp_effort(_ctx("tier", max_effort="low"), effort) == expected


def test_clamp_effort_leaves_admin_and_byok():
    assert pp.clamp_effort(None, "high") == "high"
    assert pp.clamp_effort(_ctx("byok"), "high") == "high"


def test_admin_context_matches_settings(tmp_path):
    ctx = pp.admin_context(_settings(tmp_path, provider="gemini", gemini_model="gm", gemini_effort="high"))
    assert (ctx.account_id, ctx.is_admin, ctx.source, ctx.provider) == (None, True, "admin", "gemini")
    assert (ctx.model, ctx.effort, ctx.credential_key) == ("gm", "high", "admin")
    assert ctx.allows_host_cli


def test_resolve_none_account_is_admin(tmp_path, account_db, keys):
    assert _resolve(tmp_path, account_db, None).source == "admin"


def test_resolve_admin_account(tmp_path, account_db, keys):
    ctx = _resolve(tmp_path, account_db, ADMIN_ID)
    assert ctx.is_admin and ctx.account_id == ADMIN_ID and ctx.credential_key == "admin"
    assert keys.tier_reads == 0


@pytest.mark.parametrize("account_id", [SUSPENDED_ID, DELETING_ID, PURGED_ID, 999])
def test_resolve_rejects_unusable_accounts(tmp_path, account_db, keys, account_id):
    keys.active = ActiveKey(provider="gemini", fingerprint="abcd1234", api_key=MEMBER_KEY)
    with pytest.raises(ProviderNotAllowed):
        _resolve(tmp_path, account_db, account_id)


def test_resolve_member_byok(tmp_path, account_db, keys):
    keys.active = ActiveKey(provider="gemini", fingerprint="abcd1234", api_key=MEMBER_KEY)
    ctx = _resolve(tmp_path, account_db, MEMBER_ID, gemini_effort="high")
    assert (ctx.source, ctx.provider, ctx.credential_key) == ("byok", "gemini", "byok:abcd1234")
    assert ctx.api_key == MEMBER_KEY and not ctx.is_admin
    assert ctx.effort == "high"  # BYOK는 effort를 낮추지 않는다


def test_resolve_member_without_key_uses_tier_none_by_default(tmp_path, account_db, keys):
    ctx = _resolve(tmp_path, account_db, MEMBER_ID)
    assert (ctx.source, ctx.provider, ctx.api_key, ctx.credential_key) == ("none", "none", "", "tier")


def test_resolve_member_tier_gemini_clamps_effort(tmp_path, account_db, keys):
    keys.tier = ServerTier(provider="gemini", model="tier-model", max_effort="low", features=frozenset({"stt"}))
    ctx = _resolve(tmp_path, account_db, MEMBER_ID, gemini_effort="high")
    assert (ctx.source, ctx.provider, ctx.model, ctx.effort) == ("tier", "gemini", "tier-model", "low")
    assert ctx.credential_key == "tier"


def test_resolve_registered_but_inactive_key_does_not_fall_back(tmp_path, account_db, keys):
    keys.registered = True  # rejected/unverified: load_active_key는 None
    keys.tier = ServerTier(provider="gemini")
    with pytest.raises(ProviderKeyRejected) as exc:
        _resolve(tmp_path, account_db, MEMBER_ID)
    assert "다시 검증하거나 교체" in exc.value.remedy


def test_resolve_undecryptable_key_does_not_fall_back(tmp_path, account_db, keys):
    keys.error = ProviderKeyError(f"decrypt failed near {MEMBER_KEY}")
    keys.tier = ServerTier(provider="gemini")
    with pytest.raises(ProviderKeyRejected) as exc:
        _resolve(tmp_path, account_db, MEMBER_ID)
    assert MEMBER_KEY not in str(exc.value)


# --- Settings 사본과 비밀 노출 ---------------------------------------------------


def test_context_repr_and_settings_dump_have_no_key(tmp_path):
    ctx = _ctx()
    assert MEMBER_KEY not in repr(ctx) and MEMBER_KEY not in str(ctx)
    base = _settings(tmp_path)
    member_copy = pp.settings_for_context(base, ctx)
    assert pp.context_of(member_copy) is ctx and pp.context_of(base) is None  # 원본은 바뀌지 않는다
    for dump in (member_copy.model_dump(), member_copy.model_dump(by_alias=True)):
        assert "provider_context" not in dump and "_provider_context" not in dump
    assert "provider_context" not in member_copy.model_dump_json()
    assert MEMBER_KEY not in base.model_dump_json() and base.gemini_api_key == ADMIN_KEY
    assert "provider_context" not in Settings.model_fields
    # 관리자 컨텍스트 사본은 컨텍스트만 붙인다.
    admin_ctx = pp.admin_context(base)
    admin_copy = pp.settings_for_context(base, admin_ctx)
    assert admin_copy.model_dump() == base.model_dump() and pp.context_of(admin_copy) is admin_ctx


def test_provider_context_is_not_read_from_env(tmp_path, monkeypatch):
    monkeypatch.setenv("PROVIDER_CONTEXT", "attacker")
    monkeypatch.setenv("CLAIRE_PROVIDER_CONTEXT", "attacker")
    assert _settings(tmp_path).provider_context is None


def test_member_settings_copy(tmp_path):
    s = _member_settings(tmp_path, ctx=_ctx("tier", features=("vlm",), max_effort="low", api_key=ADMIN_KEY),
                         enable_jev=True, jev_api_key="jev-secret-123456", pdf_paper_effort="high")
    assert s.provider == "gemini" and s.gemini_model == "gemini-test" and s.gemini_embed_model == "embed-test"
    assert s.gemini_effort == "low" and s.pdf_paper_effort == "low"
    assert s.enable_jev is False and not s.jev_api_key
    assert s.stt_provider == "none" and s.vlm_provider == "gemini"
    assert s.effective_stt_provider == "unavailable" and s.effective_vlm_provider == "gemini"
    assert s.effective_provider == "gemini"


def test_member_none_settings_never_become_mock(tmp_path):
    s = _member_settings(tmp_path, ctx=_ctx("none"), provider="mock", environment="development")
    assert s.effective_provider == "unavailable"
    assert s.effective_stt_provider == "unavailable" and s.effective_vlm_provider == "unavailable"


# --- 팩토리: 멤버는 host-cli·관리자 키에 닿지 않는다 -------------------------------


def test_admin_path_still_picks_host_cli(tmp_path, host_cli_present, fake_genai):
    from claire.extract.classifier import get_lowest_effort_provider
    from claire.extract.codex_provider import CodexProvider
    from claire.extract.provider import get_provider

    s = _settings(tmp_path)
    assert isinstance(get_provider(s), CodexProvider)
    assert get_lowest_effort_provider(s).name in ("codex", "antigravity")


def test_member_factories_use_only_member_key(tmp_path, host_cli_present, fake_genai):
    from claire.extract.classifier import get_lowest_effort_provider
    from claire.extract.gemini_provider import GeminiProvider
    from claire.extract.provider import get_provider
    from claire.extract.transcript.factory import get_transcript_provider
    from claire.extract.transcript.gemini_stt import GeminiTranscriptProvider
    from claire.extract.vision.factory import get_vision_provider
    from claire.extract.vision.gemini_vision import GeminiVisionProvider

    s = _member_settings(tmp_path, ctx=_ctx("byok"))
    for prov in (get_provider(s), get_lowest_effort_provider(s)):
        assert isinstance(prov, GeminiProvider)
        assert prov.client.api_key == MEMBER_KEY and prov.credential_key == "byok:abcd1234"
    stt = get_transcript_provider(s)
    assert isinstance(stt, GeminiTranscriptProvider)
    assert stt._get_genai_client().api_key == MEMBER_KEY
    vlm = get_vision_provider(s)
    assert isinstance(vlm, GeminiVisionProvider) and vlm.api_key == MEMBER_KEY
    assert ADMIN_KEY not in fake_genai.created


def test_member_with_tampered_provider_never_builds_host_cli(tmp_path, host_cli_present, fake_genai):
    from claire.extract.antigravity_provider import AntigravityProvider
    from claire.extract.classifier import get_lowest_effort_provider
    from claire.extract.codex_provider import CodexProvider
    from claire.extract.provider import get_provider

    base = _member_settings(tmp_path, ctx=_ctx("byok"))
    for name in ("antigravity", "agy", "codex"):
        s = base.model_copy(update={"provider": name})
        for factory in (get_provider, get_lowest_effort_provider):
            try:
                prov = factory(s)
            except ProviderNotAllowed:
                continue
            assert not isinstance(prov, (AntigravityProvider, CodexProvider))
    with pytest.raises(ProviderNotAllowed):
        AntigravityProvider(base)
    with pytest.raises(ProviderNotAllowed):
        CodexProvider(base)


def test_member_without_key_never_borrows_admin_key(tmp_path, host_cli_present, fake_genai):
    from claire.extract.classifier import get_lowest_effort_provider
    from claire.extract.provider import get_provider

    ctx = _ctx("byok", api_key="")
    s = pp.settings_for_context(_settings(tmp_path), ctx)
    s = s.model_copy(update={"gemini_api_key": ADMIN_KEY})  # 관리자 키가 섞여 들어와도
    for factory in (get_provider, get_lowest_effort_provider):
        prov = factory(s)
        assert prov.name == "unavailable"
        with pytest.raises(ProviderNotConfigured):
            prov.extract(None, "")
    assert fake_genai.created == []


def test_tier_none_is_not_configured(tmp_path, host_cli_present, fake_genai):
    from claire.extract.classifier import get_lowest_effort_provider
    from claire.extract.provider import get_provider
    from claire.extract.transcript.factory import get_transcript_provider
    from claire.extract.vision.factory import get_vision_provider

    s = _member_settings(tmp_path, ctx=_ctx("none"))
    for prov in (get_provider(s), get_lowest_effort_provider(s)):
        assert prov.name == "unavailable"
        with pytest.raises(ProviderNotConfigured) as exc:
            prov.extract(None, "")
        assert exc.value.remedy
        with pytest.raises(ProviderNotConfigured):
            prov.embed("text")
    with pytest.raises(ProviderNotConfigured):
        get_transcript_provider(s).transcribe(tmp_path / "a.mp3")
    with pytest.raises(ProviderNotConfigured):
        get_vision_provider(s).transcribe_images(["x.png"])
    assert fake_genai.created == []


def test_tier_feature_off_blocks_stt_and_vlm(tmp_path, fake_genai):
    from claire.extract.transcript.factory import get_transcript_provider
    from claire.extract.vision.factory import get_vision_provider

    s = _member_settings(tmp_path, ctx=_ctx("tier", features=(), api_key=ADMIN_KEY))
    with pytest.raises(ProviderNotAllowed):
        get_transcript_provider(s).transcribe(tmp_path / "a.mp3")
    with pytest.raises(ProviderNotAllowed):
        get_vision_provider(s).transcribe_images(["x.png"])


def test_jev_shadow_is_admin_only(tmp_path):
    from claire.extract.jev import make_jev_shadow

    s = _member_settings(tmp_path).model_copy(update={"enable_jev": True, "jev_api_key": "jev-key-123456"})
    assert make_jev_shadow(s) is None


# --- 자격별 호출 락 ---------------------------------------------------------------


def _gemini(tmp_path, ctx, fake_genai, **kwargs):
    from claire.extract.gemini_provider import GeminiProvider

    return GeminiProvider(pp.settings_for_context(_settings(tmp_path, **kwargs), ctx))


def test_backoff_on_one_credential_does_not_block_another(tmp_path, fake_genai, monkeypatch):
    import claire.extract.gemini_provider as gp

    a = _gemini(tmp_path, _ctx("byok", credential_key="byok:key-a"), fake_genai)
    b = _gemini(tmp_path, _ctx("byok", credential_key="byok:key-b"), fake_genai, gemini_min_interval=30.0)
    a.min_interval = 30.0
    release = threading.Event()
    a_in_backoff = threading.Event()
    real_sleep = time.sleep
    sleeps_main: list[float] = []

    def fake_sleep(seconds):
        if threading.current_thread().name == "key-a":
            a_in_backoff.set()
            release.wait(5)
        else:
            sleeps_main.append(seconds)
            real_sleep(0)

    monkeypatch.setattr(gp._time, "sleep", fake_sleep)
    calls = {"a": 0}

    def a_fn():
        calls["a"] += 1
        if calls["a"] == 1:
            raise _Err(429, "429 rate limit retryDelay: 5s")
        return "a-ok"

    gp._call_state("byok:key-a").last[0] = 0.0
    gp._call_state("byok:key-b").last[0] = 0.0
    result = {}
    t = threading.Thread(target=lambda: result.setdefault("a", a._call(a_fn)), name="key-a")
    t.start()
    try:
        assert a_in_backoff.wait(5)
        started = time.monotonic()
        assert b._call(lambda: "b-ok") == "b-ok"
        assert time.monotonic() - started < 1.0
        assert sleeps_main == []  # 키 A의 직전 호출이 키 B의 간격을 만들지 않는다
    finally:
        release.set()
        t.join(5)
    assert result.get("a") == "a-ok"


def test_throttle_holding_lock_on_one_credential_does_not_block_another(tmp_path, fake_genai, monkeypatch):
    import claire.extract.gemini_provider as gp

    a = _gemini(tmp_path, _ctx("byok", credential_key="byok:hold-a"), fake_genai)
    b = _gemini(tmp_path, _ctx("byok", credential_key="byok:hold-b"), fake_genai)
    a.min_interval = 30.0
    gp._call_state("byok:hold-a").last[0] = time.monotonic()  # A는 throttle 대기(락 보유) 상태가 된다
    release = threading.Event()
    a_waiting = threading.Event()

    def fake_sleep(seconds):
        if threading.current_thread().name == "hold-a":
            a_waiting.set()
            release.wait(5)

    monkeypatch.setattr(gp._time, "sleep", fake_sleep)
    t = threading.Thread(target=lambda: a._call(lambda: "a"), name="hold-a")
    t.start()
    try:
        assert a_waiting.wait(5)
        assert gp._call_state("byok:hold-a").lock.locked()
        done = threading.Event()
        threading.Thread(target=lambda: (b._call(lambda: "b"), done.set())).start()
        assert done.wait(2), "키 B 호출이 키 A의 락에 막혔다"
    finally:
        release.set()
        t.join(5)


def test_admin_alias_state_is_admin_credential():
    import claire.extract.gemini_provider as gp

    assert gp._LAST_CALL is gp._call_state("admin").last
    assert gp._CALL_LOCK is gp._call_state("admin").lock


# --- 폴백 없음·훅·스크럽 ----------------------------------------------------------


def test_member_key_rejection_raises_without_fallback(tmp_path, fake_genai, monkeypatch):
    import claire.extract.gemini_provider as gp
    from claire.ontology.base import Document

    monkeypatch.setattr(gp._time, "sleep", lambda *_: None)
    p = _gemini(tmp_path, _ctx("byok"), fake_genai)
    p.client.interactions.create.side_effect = _Err(400, f"API key not valid. key={MEMBER_KEY}")
    doc = Document(id="d", title="t", raw_text="body", source_type="text")
    with pytest.raises(ProviderKeyRejected) as exc:
        p.extract(doc)
    assert p.client.interactions.create.call_count == 1  # JSON 폴백·재시도 없음
    assert MEMBER_KEY not in str(exc.value)
    for call in (lambda: p.classify_paper(doc), lambda: p.judge_relationship(MagicMock()),
                 lambda: p.select_followups("ctx", [{"url": "u"}])):
        p.client.interactions.create.side_effect = _Err(403, "PERMISSION_DENIED")
        with pytest.raises(ProviderKeyRejected):
            call()
    p.client.models.embed_content.side_effect = _Err(401, "UNAUTHENTICATED")
    with pytest.raises(ProviderKeyRejected):
        p.embed("text")
    assert p.client.models.embed_content.call_count == 1


def test_member_quota_exhaustion_is_rejected_not_retried(tmp_path, fake_genai, monkeypatch):
    import claire.extract.gemini_provider as gp

    monkeypatch.setattr(gp._time, "sleep", lambda *_: None)
    p = _gemini(tmp_path, _ctx("byok"), fake_genai)
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        raise _Err(429, "RESOURCE_EXHAUSTED prepayment credits are depleted")

    with pytest.raises(ProviderKeyRejected):
        p._call(fn)
    assert calls["n"] == 1


def test_admin_path_keeps_json_fallback(tmp_path, fake_genai):
    from claire.extract.gemini_provider import GeminiProvider
    from claire.ontology.base import Document

    p = GeminiProvider(_settings(tmp_path, provider="gemini"))
    assert p.client.api_key == ADMIN_KEY and p.credential_key == "admin" and p.usage_hooks is None
    p.client.interactions.create.side_effect = [
        _Err(400, "Invalid schema"),
        SimpleNamespace(output_text='{"summary":"폴백 요약","key_claims":[],"entities":[],"relations":[]}'),
    ]
    res = p.extract(Document(id="d", title="t", raw_text="body", source_type="text"))
    assert res.summary == "폴백 요약"


def test_error_messages_are_scrubbed(tmp_path, fake_genai, monkeypatch):
    import claire.extract.gemini_provider as gp

    monkeypatch.setattr(gp._time, "sleep", lambda *_: None)
    p = _gemini(tmp_path, _ctx("byok", api_key="member-plain-secret-value"), fake_genai)
    with pytest.raises(_Err) as exc:
        p._call(lambda: (_ for _ in ()).throw(_Err(400, "bad request with member-plain-secret-value")))
    assert "member-plain-secret-value" not in str(exc.value)
    assert "[REDACTED]" in str(exc.value)


def test_usage_hooks_wrap_llm_and_embedding_calls(tmp_path, fake_genai):
    p = _gemini(tmp_path, _ctx("tier", api_key=ADMIN_KEY), fake_genai)
    hooks = _Hooks()
    p.usage_hooks = hooks
    p.client.interactions.create.return_value = SimpleNamespace(
        output_text="answer", usage=SimpleNamespace(total_input_tokens=11, total_output_tokens=7,
                                                    total_thought_tokens=2))
    assert p.summarize_search("q", "ctx") == "answer"
    p.client.models.embed_content.return_value = SimpleNamespace(embeddings=[SimpleNamespace(values=[0.1])])
    assert p.embed("text") == [0.1]
    assert hooks.events == [("before",), ("after", 11, 9), ("before",), ("after", 0, 0)]


def test_budget_exhaustion_blocks_call_before_api(tmp_path, fake_genai):
    p = _gemini(tmp_path, _ctx("tier", api_key=ADMIN_KEY), fake_genai)
    p.usage_hooks = _Hooks(budget=False)
    with pytest.raises(ProviderBudgetExceeded):
        p.judge_same_entity(MagicMock())
    assert p.client.interactions.create.call_count == 0


def test_tier_effort_is_clamped_at_call_time(tmp_path, fake_genai):
    p = _gemini(tmp_path, _ctx("tier", max_effort="low", api_key=ADMIN_KEY), fake_genai)
    cfg = p._build_generation_config(effort="high")
    assert cfg["thinking_config"] == {"thinking_level": "LOW"}


def test_vision_hooks_and_member_rejection(tmp_path, fake_genai):
    from claire.extract.vision.factory import get_vision_provider

    img = tmp_path / "card.png"
    img.write_bytes(b"\x89PNG\r\n")
    s = _member_settings(tmp_path, ctx=_ctx("byok"))
    vlm = get_vision_provider(s)
    hooks = _Hooks()
    vlm.usage_hooks = hooks
    client = _FakeClient(api_key=MEMBER_KEY)
    client.models.generate_content.return_value = SimpleNamespace(
        text="본문", usage_metadata=SimpleNamespace(prompt_token_count=5, candidates_token_count=3,
                                                     thoughts_token_count=None))
    vlm._get_client = lambda: client
    assert vlm.transcribe_images([str(img)]).text == "본문"
    assert hooks.events == [("before",), ("after", 5, 3)]
    client.models.generate_content.side_effect = _Err(401, f"bad key {MEMBER_KEY}")
    with pytest.raises(ProviderKeyRejected) as exc:
        vlm.transcribe_images([str(img)])
    assert MEMBER_KEY not in str(exc.value)


def test_stt_member_never_uses_env_key(tmp_path, fake_genai, monkeypatch):
    from claire.extract.transcript.gemini_stt import GeminiTranscriptProvider

    monkeypatch.setenv("GEMINI_API_KEY", ADMIN_KEY)
    s = _member_settings(tmp_path, ctx=_ctx("byok", api_key=""))
    with pytest.raises(ProviderNotConfigured):
        GeminiTranscriptProvider(s)._get_genai_client()
    assert fake_genai.created == []


# --- host-cli 하위 프로세스 환경 ---------------------------------------------------


def test_antigravity_subprocess_env_has_no_secrets(tmp_path, host_cli_present, monkeypatch):
    import claire.extract.antigravity_provider as ap

    monkeypatch.setenv("GEMINI_API_KEY", ADMIN_KEY)
    monkeypatch.setenv("CLAIRE_INJECT_TOKEN", "inject-token-secret")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "telegram-secret")
    monkeypatch.setenv("HOME", str(tmp_path))
    seen: list[dict] = []

    def fake_run(cmd, **kwargs):
        seen.append(kwargs.get("env"))
        return subprocess.CompletedProcess(cmd, 0, stdout='{"result": "ok"}', stderr="")

    monkeypatch.setattr(ap.subprocess, "run", fake_run)
    prov = ap.AntigravityProvider(_settings(tmp_path, provider="antigravity"))
    try:
        prov._run_cli("hello", output_format="text")
    except Exception:  # noqa: BLE001 — 출력 해석은 이 테스트의 관심사가 아니다
        pass
    assert seen, "agy subprocess was not invoked"
    env = seen[0]
    assert env is not None
    assert "GEMINI_API_KEY" not in env and "CLAIRE_INJECT_TOKEN" not in env and "TELEGRAM_BOT_TOKEN" not in env
    assert ADMIN_KEY not in " ".join(env.values())
    assert env.get("HOME") == str(tmp_path) and "PATH" in env
