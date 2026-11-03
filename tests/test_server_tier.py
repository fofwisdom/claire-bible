"""서버 등급과 UTC 월·일 사용량 원장 (설계 §10.3, §13.3 ``account_usage``, §17.2 G5, P4 L4-c).

모든 상태는 ``tmp_path``에만 쓰고, 호스트 셸의 ``CLAIRE_*`` 환경변수를 지운 뒤 돈다(§18.2-6).
"""

from __future__ import annotations

import calendar
import json
import logging
import os
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from starlette.testclient import TestClient

from claire.accounts import readiness
from claire.accounts import server_tier as st
from claire.accounts.provider_policy import (
    SERVER_TIER_SETTING_KEY,
    ProviderBudgetExceeded,
    ProviderContext,
    ServerTier,
)
from claire.api import server
from claire.api.principal import STEP_UP_WINDOW_SECONDS
from claire.store import accounts as accounts_store
from claire.store import auth_db
from claire.store import db as dbm
from claire.store import usage_ledger as ul

OWNER_TOKEN = "owner-" + "o" * 32
READONLY_TOKEN = "readonly-" + "r" * 32
PUBLIC_URL = "http://127.0.0.1:8765"
USERNAME = "kim_lee"
PASSWORD = "correct-horse-battery"
TIER_PATH = "/api/admin/server-tier"


def _utc(y: int, mo: int, d: int, h: int = 0, mi: int = 0, s: int = 0) -> float:
    return float(calendar.timegm((y, mo, d, h, mi, s, 0, 0, 0)))


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    for key in list(os.environ):
        if key.startswith("CLAIRE_"):
            monkeypatch.delenv(key, raising=False)


@pytest.fixture
def auth_path(tmp_path: Path) -> Path:
    path = auth_db.auth_db_path(tmp_path)
    auth_db.connect_auth_db(path).close()
    return path


@pytest.fixture
def ledger(auth_path: Path):
    c = auth_db.connect_auth_db(auth_path)
    yield c
    c.close()


@pytest.fixture
def kdb(tmp_path: Path):
    c = dbm.connect(tmp_path / "claire.db")
    dbm.init_db(c)
    yield c
    c.close()


def _ctx(account_id: int | None = 2, *, source: str = "tier", is_admin: bool = False, **tier: Any) -> ProviderContext:
    return ProviderContext(
        account_id=account_id,
        is_admin=is_admin,
        source=source,  # type: ignore[arg-type]
        provider="gemini" if source != "none" else "none",
        model="gemini-test",
        embed_model="embed-test",
        effort="low",
        credential_key={"byok": "byok:abc", "tier": "tier"}.get(source, "admin"),
        tier=ServerTier(**tier),
    )


def _add_member_row(conn: sqlite3.Connection, account_id: int, public_id: int) -> None:
    conn.execute(
        "INSERT INTO accounts (id, public_id, username, role, status, created_at, updated_at) "
        "VALUES (?, ?, ?, 'member', 'active', 0, 0)",
        (account_id, public_id, f"member_{account_id}"),
    )
    conn.commit()


def _add_theme(conn: sqlite3.Connection, theme_id: int, account_id: int) -> None:
    conn.execute(
        "INSERT INTO themes (id, seq, label, account_id, created_at, updated_at) VALUES (?, ?, ?, ?, 0, 0)",
        (theme_id, theme_id, f"t{theme_id}", account_id),
    )
    conn.commit()


def _add_doc(conn: sqlite3.Connection, doc_id: str, theme_id: int, raw_text: str = "x") -> None:
    conn.execute("INSERT INTO documents (id, theme_id, raw_text) VALUES (?, ?, ?)", (doc_id, theme_id, raw_text))
    conn.commit()


# --- auth.db v1 → v2 -------------------------------------------------------------------------


def _v1_schema_sql() -> str:
    sql = auth_db._SCHEMA_SQL
    v1 = sql.replace("    daily_day TEXT NOT NULL DEFAULT '',\n    daily_ingests INTEGER NOT NULL DEFAULT 0,\n", "")
    assert v1 != sql
    return v1


def test_auth_db_v1_to_v2_migration_is_idempotent(tmp_path):
    path = auth_db.auth_db_path(tmp_path)
    path.parent.mkdir(parents=True)
    raw = sqlite3.connect(path)
    raw.executescript(_v1_schema_sql())
    raw.execute("INSERT INTO meta(key, value) VALUES ('schema_version', '1')")
    raw.execute("INSERT INTO account_usage(account_id, period, tokens_in, tokens_out, ingests) VALUES (2, '2026-11', 5, 6, 7)")
    raw.commit()
    assert "daily_day" not in {r[1] for r in raw.execute("PRAGMA table_info(account_usage)")}
    raw.close()

    c = auth_db.connect_auth_db(path)
    try:
        cols = {r[1]: r for r in c.execute("PRAGMA table_info(account_usage)")}
        assert {"daily_day", "daily_ingests"} <= set(cols)
        assert c.execute("SELECT value FROM meta WHERE key='schema_version'").fetchall()[0][0] == str(auth_db.AUTH_DB_SCHEMA_VERSION)
        row = c.execute("SELECT tokens_in, tokens_out, ingests, daily_day, daily_ingests FROM account_usage").fetchone()
        assert tuple(row) == (5, 6, 7, "", 0)
        before = c.execute("SELECT sql FROM sqlite_master WHERE name='account_usage'").fetchone()[0]
        auth_db.init_auth_db(c)
        auth_db.init_auth_db(c)
        assert c.execute("SELECT sql FROM sqlite_master WHERE name='account_usage'").fetchone()[0] == before
        assert [r[0] for r in c.execute("SELECT value FROM meta WHERE key='schema_version'")] == [str(auth_db.AUTH_DB_SCHEMA_VERSION)]
        # 이관한 DB에서도 원장이 동작한다.
        assert ul.try_reserve_ingest(c, account_id=2, daily_limit=1, now=_utc(2026, 11, 4)) is True
    finally:
        c.close()


def test_fresh_auth_db_has_v2_columns(ledger):
    cols = {r[1] for r in ledger.execute("PRAGMA table_info(account_usage)")}
    assert {"daily_day", "daily_ingests"} <= cols
    assert auth_db.AUTH_DB_SCHEMA_VERSION >= 2


# --- UTC 경계 -----------------------------------------------------------------------------------


def test_utc_period_and_day_boundaries_ignore_local_timezone(monkeypatch):
    if hasattr(time, "tzset"):
        monkeypatch.setenv("TZ", "Asia/Seoul")
        time.tzset()
    try:
        last = _utc(2026, 1, 31, 23, 59, 59)
        first = _utc(2026, 2, 1)
        assert (ul.utc_period(last), ul.utc_day(last)) == ("2026-01", "2026-01-31")
        assert (ul.utc_period(first), ul.utc_day(first)) == ("2026-02", "2026-02-01")
        assert ul.utc_day(_utc(2026, 12, 31, 23, 59, 59)) == "2026-12-31"
        assert ul.utc_period(_utc(2027, 1, 1)) == "2027-01"
    finally:
        monkeypatch.delenv("TZ", raising=False)
        if hasattr(time, "tzset"):
            time.tzset()


def test_tokens_split_by_utc_month(ledger):
    ul.record_tokens(ledger, account_id=2, tokens_in=10, tokens_out=5, now=_utc(2026, 1, 31, 23, 59, 59))
    ul.record_tokens(ledger, account_id=2, tokens_in=1, tokens_out=1, now=_utc(2026, 2, 1))
    ul.record_tokens(ledger, account_id=2, tokens_in=-3, tokens_out=0, now=_utc(2026, 2, 1))  # 음수는 무시
    jan = ul.get_usage(ledger, account_id=2, now=_utc(2026, 1, 15))
    feb = ul.get_usage(ledger, account_id=2, now=_utc(2026, 2, 15))
    assert (jan.period, jan.tokens_in, jan.tokens_out) == ("2026-01", 10, 5)
    assert (feb.period, feb.tokens_in, feb.tokens_out) == ("2026-02", 1, 1)
    assert ul.tokens_remaining(ledger, account_id=2, monthly_limit=12, now=_utc(2026, 1, 2)) == 0
    assert ul.tokens_remaining(ledger, account_id=2, monthly_limit=12, now=_utc(2026, 2, 2)) == 10
    assert ul.get_usage(ledger, account_id=99, now=_utc(2026, 2, 2)).tokens_total == 0


# --- 일 적재 상한 ---------------------------------------------------------------------------------


def test_daily_ingest_limit_and_day_rollover(ledger):
    day1 = _utc(2026, 3, 10, 23, 0)
    assert [ul.try_reserve_ingest(ledger, account_id=2, daily_limit=3, now=day1) for _ in range(4)] == [True, True, True, False]
    snap = ul.get_usage(ledger, account_id=2, now=day1)
    assert (snap.daily_day, snap.daily_ingests, snap.ingests) == ("2026-03-10", 3, 3)
    # 다른 계정은 따로 센다.
    assert ul.try_reserve_ingest(ledger, account_id=3, daily_limit=3, now=day1) is True

    day2 = _utc(2026, 3, 11, 0, 0, 1)
    assert ul.get_usage(ledger, account_id=2, now=day2).daily_ingests == 0  # 날이 바뀌면 0으로 읽는다
    assert [ul.try_reserve_ingest(ledger, account_id=2, daily_limit=3, now=day2) for _ in range(4)] == [True, True, True, False]
    snap = ul.get_usage(ledger, account_id=2, now=day2)
    assert (snap.daily_day, snap.daily_ingests, snap.ingests) == ("2026-03-11", 3, 6)
    # 상한 0은 아무것도 허용하지 않고 기록하지도 않는다.
    assert ul.try_reserve_ingest(ledger, account_id=4, daily_limit=0, now=day2) is False
    assert ledger.execute("SELECT COUNT(*) FROM account_usage WHERE account_id=4").fetchone()[0] == 0


def test_daily_ingest_rolls_over_at_month_boundary(ledger):
    assert ul.try_reserve_ingest(ledger, account_id=2, daily_limit=1, now=_utc(2026, 4, 30, 23, 59)) is True
    assert ul.try_reserve_ingest(ledger, account_id=2, daily_limit=1, now=_utc(2026, 4, 30, 23, 59, 30)) is False
    assert ul.try_reserve_ingest(ledger, account_id=2, daily_limit=1, now=_utc(2026, 5, 1)) is True
    may = ul.get_usage(ledger, account_id=2, now=_utc(2026, 5, 1))
    assert (may.period, may.ingests, may.daily_ingests) == ("2026-05", 1, 1)


def test_concurrent_reservations_never_exceed_limit(auth_path):
    limit, workers = 7, 32
    now = _utc(2026, 6, 1, 12)
    barrier = threading.Barrier(workers)
    results: list[bool] = []
    lock = threading.Lock()

    def worker() -> None:
        c = auth_db.connect_auth_db(auth_path)
        try:
            barrier.wait()
            ok = ul.try_reserve_ingest(c, account_id=2, daily_limit=limit, now=now)
            with lock:
                results.append(ok)
        finally:
            c.close()

    threads = [threading.Thread(target=worker) for _ in range(workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(results) == workers
    assert results.count(True) == limit
    c = auth_db.connect_auth_db(auth_path)
    try:
        assert ul.get_usage(c, account_id=2, now=now).daily_ingests == limit
    finally:
        c.close()


# --- 집행: 적재·토큰 --------------------------------------------------------------------------------


def test_check_ingest_allowed_applies_to_members_including_byok(auth_path):
    now = _utc(2026, 7, 1)
    for source, account in (("tier", 2), ("byok", 3), ("none", 4)):
        ctx = _ctx(account, source=source, daily_ingests=2)
        st.check_ingest_allowed(ctx, auth_db_path=auth_path, now=now)
        st.check_ingest_allowed(ctx, auth_db_path=auth_path, now=now)
        with pytest.raises(ProviderBudgetExceeded) as exc:
            st.check_ingest_allowed(ctx, auth_db_path=auth_path, now=now)
        assert exc.value.code == "provider_budget_exceeded" and exc.value.remedy
        st.check_ingest_allowed(ctx, auth_db_path=auth_path, now=_utc(2026, 7, 2))


def test_admin_is_unlimited(auth_path, kdb):
    now = _utc(2026, 7, 1)
    for ctx in (_ctx(1, source="admin", is_admin=True, daily_ingests=0, max_themes=1, max_documents=0,
                     storage_mb=0, monthly_tokens=0),
                _ctx(None, source="admin", daily_ingests=0, monthly_tokens=0)):
        for _ in range(5):
            st.check_ingest_allowed(ctx, auth_db_path=auth_path, now=now)
        st.check_tokens_available(ctx, auth_db_path=auth_path, now=now)
        st.record_usage(ctx, auth_db_path=auth_path, tokens_in=1000, tokens_out=1000, now=now)
        st.check_theme_quota(kdb, ctx)
        st.check_document_quota(kdb, ctx)
        with st.job_slot(ctx), st.job_slot(ctx):
            pass
        assert st.usage_hooks_for(ctx, auth_db_path=auth_path) is None
    c = auth_db.connect_auth_db(auth_path)
    try:
        assert c.execute("SELECT COUNT(*) FROM account_usage").fetchone()[0] == 0
    finally:
        c.close()


def test_monthly_tokens_apply_to_tier_context_only(auth_path):
    now = _utc(2026, 8, 15)
    tier_ctx = _ctx(2, source="tier", monthly_tokens=100)
    st.check_tokens_available(tier_ctx, auth_db_path=auth_path, now=now)
    st.record_usage(tier_ctx, auth_db_path=auth_path, tokens_in=60, tokens_out=39, now=now)
    st.check_tokens_available(tier_ctx, auth_db_path=auth_path, now=now)
    st.record_usage(tier_ctx, auth_db_path=auth_path, tokens_in=1, tokens_out=0, now=now)
    with pytest.raises(ProviderBudgetExceeded):
        st.check_tokens_available(tier_ctx, auth_db_path=auth_path, now=now)
    # 다음 UTC 달에는 다시 쓸 수 있다.
    st.check_tokens_available(tier_ctx, auth_db_path=auth_path, now=_utc(2026, 9, 1))
    # 같은 계정이라도 자기 키(BYOK)면 월 토큰 상한을 받지 않는다. 사용량은 기록한다.
    byok_ctx = _ctx(2, source="byok", monthly_tokens=100)
    st.check_tokens_available(byok_ctx, auth_db_path=auth_path, now=now)
    st.record_usage(byok_ctx, auth_db_path=auth_path, tokens_in=5, tokens_out=5, now=now)
    c = auth_db.connect_auth_db(auth_path)
    try:
        assert ul.get_usage(c, account_id=2, now=now).tokens_total == 110
    finally:
        c.close()
    # monthly_tokens 0인 등급은 처음부터 허용하지 않는다.
    with pytest.raises(ProviderBudgetExceeded):
        st.check_tokens_available(_ctx(3, source="tier", monthly_tokens=0), auth_db_path=auth_path, now=now)


def test_usage_hooks_for_member(auth_path):
    ctx = _ctx(2, source="tier", monthly_tokens=10)
    hooks = st.usage_hooks_for(ctx, auth_db_path=auth_path)
    assert hooks is not None
    hooks.before_call()
    hooks.after_call(7, 3)
    with pytest.raises(ProviderBudgetExceeded):
        hooks.before_call()
    assert "api_key" not in repr(hooks)


def test_ledger_needs_prepared_auth_db(tmp_path):
    with pytest.raises(sqlite3.OperationalError):
        st.check_ingest_allowed(_ctx(2), auth_db_path=tmp_path / "missing" / "auth.db")
    assert not (tmp_path / "missing").exists()


# --- 집행: 자원 한도 (BYOK에도 적용) -------------------------------------------------------------------


def test_theme_quota_applies_to_byok(kdb):
    _add_member_row(kdb, 2, 111111111111)
    _add_theme(kdb, 20, 2)
    _add_theme(kdb, 21, 2)
    for source in ("tier", "byok", "none"):
        st.check_theme_quota(kdb, _ctx(2, source=source, max_themes=3))
        with pytest.raises(ProviderBudgetExceeded):
            st.check_theme_quota(kdb, _ctx(2, source=source, max_themes=2))


def test_document_count_and_storage_quota(kdb, tmp_path):
    _add_member_row(kdb, 2, 111111111111)
    _add_member_row(kdb, 3, 222222222222)
    _add_theme(kdb, 20, 2)
    _add_theme(kdb, 30, 3)
    _add_doc(kdb, "d1", 20, "가" * 10)   # 30 bytes UTF-8
    _add_doc(kdb, "d2", 20, "abc")
    _add_doc(kdb, "other", 30, "z" * (2 * st.MIB))  # 다른 계정
    assert st.account_document_count(kdb, 2) == 2
    original = tmp_path / "orig.pdf"
    original.write_bytes(b"p" * 100)
    kdb.execute("INSERT INTO raw_inbox (theme_id, payload, file_ref) VALUES (20, 'hello', ?)", (str(original),))
    kdb.execute("INSERT INTO raw_inbox (theme_id, payload, file_ref) VALUES (20, '', ?)", (str(original),))
    kdb.execute("INSERT INTO raw_inbox (theme_id, payload, file_ref) VALUES (20, '', 'relative/ignored.pdf')")
    kdb.commit()
    assert st.account_storage_bytes(kdb, 2) == 30 + 3 + 5 + 100

    for source in ("tier", "byok"):
        st.check_document_quota(kdb, _ctx(2, source=source, max_documents=3, storage_mb=1))
        with pytest.raises(ProviderBudgetExceeded, match="document"):
            st.check_document_quota(kdb, _ctx(2, source=source, max_documents=2, storage_mb=1))
    with pytest.raises(ProviderBudgetExceeded, match="storage"):
        st.check_document_quota(kdb, _ctx(3, source="byok", max_documents=10, storage_mb=2))
    st.check_document_quota(kdb, _ctx(3, source="byok", max_documents=10, storage_mb=3))


# --- BYOK 동시 작업 ---------------------------------------------------------------------------------


def test_job_slot_limits_byok_concurrency_without_waiting():
    ctx = _ctx(2, source="byok", byok_max_concurrency=2)
    other = _ctx(3, source="byok", byok_max_concurrency=1)
    with st.job_slot(ctx), st.job_slot(ctx):
        t0 = time.monotonic()
        with pytest.raises(ProviderBudgetExceeded):
            with st.job_slot(ctx):
                pass
        assert time.monotonic() - t0 < 0.5
        with st.job_slot(other):  # 계정별로 센다
            pass
    with st.job_slot(ctx):  # 끝나면 자리가 돌아온다
        pass
    # 서버 등급 컨텍스트는 묶지 않는다.
    tier_ctx = _ctx(2, source="tier", byok_max_concurrency=1)
    with st.job_slot(tier_ctx), st.job_slot(tier_ctx):
        pass


def test_job_slot_releases_on_error_and_bounds_threads():
    ctx = _ctx(5, source="byok", byok_max_concurrency=1)
    with pytest.raises(RuntimeError):
        with st.job_slot(ctx):
            raise RuntimeError("boom")
    with st.job_slot(ctx):
        pass

    ctx3 = _ctx(6, source="byok", byok_max_concurrency=3)
    active = peak = 0
    rejected = 0
    lock = threading.Lock()
    barrier = threading.Barrier(12)

    def worker() -> None:
        nonlocal active, peak, rejected
        barrier.wait()
        try:
            with st.job_slot(ctx3):
                with lock:
                    active += 1
                    peak = max(peak, active)
                time.sleep(0.05)
                with lock:
                    active -= 1
        except ProviderBudgetExceeded:
            with lock:
                rejected += 1

    threads = [threading.Thread(target=worker) for _ in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert 1 <= peak <= 3 and rejected >= 1


# --- 저장·fail-closed ------------------------------------------------------------------------------


def test_load_missing_tier_is_default_none(kdb):
    assert st.load_server_tier(kdb) == ServerTier()
    assert st.load_server_tier(kdb).provider == "none"


@pytest.mark.parametrize(
    "raw",
    ["not json", "none", '{"provider": "codex"}', '{"provider": "gemini", "extra": 1}', '{"daily_ingests": -1}', "[]"],
)
def test_broken_tier_fails_closed_to_none(kdb, caplog, raw):
    kdb.execute(
        "INSERT INTO instance_settings (key, value, updated_at) VALUES (?, ?, 0)", (SERVER_TIER_SETTING_KEY, raw)
    )
    kdb.commit()
    with caplog.at_level(logging.WARNING, logger="claire.accounts.server_tier"):
        tier = st.load_server_tier(kdb)
    assert tier == ServerTier() and tier.provider == "none"
    assert any("server_tier is invalid" in r.getMessage() for r in caplog.records)


def test_save_and_load_roundtrip(kdb):
    tier = ServerTier(provider="gemini", model="gemini-2.5-flash", monthly_tokens=1000, daily_ingests=3,
                      features=frozenset({"expand", "stt"}), byok_max_concurrency=2)
    st.save_server_tier(kdb, tier, updated_by=1)
    assert st.load_server_tier(kdb) == tier
    stored = json.loads(kdb.execute("SELECT value FROM instance_settings WHERE key='server_tier'").fetchone()[0])
    assert stored == tier.to_dict()
    with pytest.raises(ValueError):
        st.save_server_tier(kdb, ServerTier(provider="codex"), updated_by=1)
    assert st.load_server_tier(kdb) == tier


# --- G5 ------------------------------------------------------------------------------------------


def _g5(conn: sqlite3.Connection, tmp_path: Path) -> readiness.ReadinessItem:
    report = readiness.check_readiness(conn, SimpleNamespace(environment="development", data_dir=tmp_path))
    return next(i for i in report.items if i.id == "G5")


def test_g5_requires_a_valid_stored_tier(kdb, tmp_path):
    assert _g5(kdb, tmp_path).passed is False
    kdb.execute("INSERT INTO instance_settings (key, value, updated_at) VALUES ('server_tier', '{\"provider\": \"codex\"}', 0)")
    kdb.commit()
    item = _g5(kdb, tmp_path)
    assert item.passed is False and "유효하지 않" in item.detail
    st.save_server_tier(kdb, ServerTier(), updated_by=1)  # none도 유효한 설정이다
    item = _g5(kdb, tmp_path)
    assert item.passed is True and "provider=none" in item.detail


# --- 관리자 API ------------------------------------------------------------------------------------


@dataclass
class _Settings:
    db_file: Path
    data_dir: Path
    environment: str = "development"
    public_url: str = PUBLIC_URL
    inject_token: str = OWNER_TOKEN
    readonly_token: str = READONLY_TOKEN
    cors_allowed_origins: str = ""
    anonymous_readonly: bool = False


@pytest.fixture
def app_settings(tmp_path: Path) -> _Settings:
    return _Settings(db_file=tmp_path / "claire.db", data_dir=tmp_path)


@pytest.fixture
def client(app_settings: _Settings):
    app = server.create_app(app_settings, SimpleNamespace(provider=SimpleNamespace(name="stub")))
    with TestClient(app, base_url=PUBLIC_URL) as tc:
        yield tc


def _db(s: _Settings) -> sqlite3.Connection:
    return dbm.connect_existing(s.db_file)


def _activate_admin(s: _Settings) -> None:
    conn = _db(s)
    auth = auth_db.connect_auth_db(auth_db.auth_db_path(s.data_dir))
    try:
        accounts_store.AccountStore(conn).complete_setup(1, username=USERNAME, display_name="관리자")
        auth_db.set_password(auth, 1, PASSWORD, username=USERNAME)
    finally:
        auth.close()
        conn.close()


def _session(s: _Settings, account_id: int = 1, *, authenticated_ago: float = 0.0, scope: str = "owner") -> str:
    conn = _db(s)
    try:
        return accounts_store.create_account_session(
            conn, account_id=account_id, scope=scope, now=time.time() - authenticated_ago
        )
    finally:
        conn.close()


def _req(tc: TestClient, method: str, path: str, *, session: str | None = None, bearer: str | None = None,
         origin: str | None = PUBLIC_URL, **kw: Any):
    headers = dict(kw.pop("headers", {}) or {})
    if session is not None:
        headers["Cookie"] = f"claire_session={session}"
    if bearer is not None:
        headers["Authorization"] = f"Bearer {bearer}"
    if method not in {"GET", "HEAD"} and origin is not None:
        headers["Origin"] = origin
    tc.cookies.clear()
    r = tc.request(method, path, headers=headers, follow_redirects=False, **kw)
    tc.cookies.clear()
    return r


GOOD_TIER = {
    "provider": "gemini", "model": "gemini-2.5-flash", "monthly_tokens": 200000, "daily_ingests": 10,
    "max_effort": "low", "features": ["expand", "synthesize"], "storage_mb": 512, "max_themes": 3,
    "max_documents": 1000, "byok_caps": {"max_concurrency": 2},
}


def _audit_rows(s: _Settings) -> list[dict[str, Any]]:
    c = auth_db.connect_auth_db(auth_db.auth_db_path(s.data_dir))
    try:
        return [r for r in auth_db.list_admin_audit(c) if r["action"] == "server_tier.update"]
    finally:
        c.close()


def test_server_tier_api_is_admin_only(client, app_settings):
    _activate_admin(app_settings)
    conn = _db(app_settings)
    try:
        _add_member_row(conn, 2, 333333333333)
    finally:
        conn.close()
    member = _session(app_settings, 2)
    for method in ("GET", "HEAD", "PUT"):
        kw = {"json": GOOD_TIER} if method == "PUT" else {}
        assert _req(client, method, TIER_PATH, **kw).status_code == 404
        assert _req(client, method, TIER_PATH, bearer=READONLY_TOKEN, **kw).status_code == 404
        assert _req(client, method, TIER_PATH, session=member, **kw).status_code == 404
        assert _req(client, method, TIER_PATH, session=_session(app_settings, scope="readonly"), **kw).status_code == 404
    assert _audit_rows(app_settings) == []
    assert _req(client, "GET", "/admin/instance", session=member).status_code == 404


def test_server_tier_get_reports_default_and_usage(client, app_settings):
    _activate_admin(app_settings)
    token = _session(app_settings)
    r = _req(client, "GET", TIER_PATH, session=token)
    assert r.status_code == 200
    body = r.json()
    assert body["configured"] is False and body["valid"] is False
    assert body["tier"] == ServerTier().to_dict()
    assert body["usage"] == [] and len(body["period"]) == 7 and len(body["day"]) == 10
    assert _req(client, "HEAD", TIER_PATH, session=token).status_code == 200
    assert _req(client, "GET", TIER_PATH, bearer=OWNER_TOKEN).status_code == 200

    conn = _db(app_settings)
    try:
        _add_member_row(conn, 2, 444444444444)
    finally:
        conn.close()
    st.record_usage(_ctx(2), auth_db_path=auth_db.auth_db_path(app_settings.data_dir), tokens_in=3, tokens_out=4)
    usage = _req(client, "GET", TIER_PATH, session=token).json()["usage"]
    assert usage == [{"public_id": "444444444444", "tokens_in": 3, "tokens_out": 4, "ingests": 0, "daily_ingests": 0}]


def test_server_tier_put_requires_step_up(client, app_settings):
    _activate_admin(app_settings)
    stale = _session(app_settings, authenticated_ago=STEP_UP_WINDOW_SECONDS + 60)
    r = _req(client, "PUT", TIER_PATH, session=stale, json=GOOD_TIER)
    assert r.status_code == 401 and r.json()["error"] == "reauth_required"
    r = _req(client, "PUT", TIER_PATH, bearer=OWNER_TOKEN, json=GOOD_TIER)
    assert r.status_code == 403 and r.json()["error"] == "step_up_requires_session"
    r = _req(client, "PUT", TIER_PATH, session=_session(app_settings), json=GOOD_TIER, origin="https://evil.example")
    assert r.status_code == 403
    assert _audit_rows(app_settings) == []
    conn = _db(app_settings)
    try:
        assert st.read_stored_server_tier(conn) is None
    finally:
        conn.close()


@pytest.mark.parametrize(
    "bad",
    [
        {**GOOD_TIER, "provider": "codex"},
        {**GOOD_TIER, "features": ["research"]},
        {**GOOD_TIER, "daily_ingests": -1},
        {**GOOD_TIER, "max_themes": 0},
        {**GOOD_TIER, "unknown": 1},
        {**GOOD_TIER, "byok_caps": {"max_concurrency": 0}},
        {**GOOD_TIER, "monthly_tokens": "100"},
    ],
)
def test_server_tier_put_validation_is_422(client, app_settings, bad):
    _activate_admin(app_settings)
    r = _req(client, "PUT", TIER_PATH, session=_session(app_settings), json=bad)
    assert r.status_code == 422 and r.json()["error"] == "invalid_server_tier"
    assert _audit_rows(app_settings) == []


def test_server_tier_put_saves_audits_and_passes_g5(client, app_settings):
    _activate_admin(app_settings)
    token = _session(app_settings)
    before = _req(client, "GET", "/api/admin/instance", session=token).json()
    assert next(i for i in before["readiness"]["items"] if i["id"] == "G5")["passed"] is False

    r = _req(client, "PUT", TIER_PATH, session=token, json=GOOD_TIER)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True and body["configured"] is True and body["valid"] is True
    assert body["tier"] == ServerTier.from_dict(GOOD_TIER).to_dict()
    # {"tier": {...}}로 감싸 보내도 된다.
    r = _req(client, "PUT", TIER_PATH, session=token, json={"tier": {"provider": "none"}})
    assert r.status_code == 200 and r.json()["tier"] == ServerTier().to_dict()

    rows = _audit_rows(app_settings)
    assert len(rows) == 2
    assert rows[1]["actor_account_id"] == 1 and rows[1]["target_account_id"] is None
    assert rows[1]["detail"] == {"tier": ServerTier.from_dict(GOOD_TIER).to_dict()}
    raw = json.dumps(rows)
    assert USERNAME not in raw

    after = _req(client, "GET", "/api/admin/instance", session=token).json()
    assert next(i for i in after["readiness"]["items"] if i["id"] == "G5")["passed"] is True


def test_admin_instance_page_has_server_tier_section(client, app_settings):
    _activate_admin(app_settings)
    token = _session(app_settings)
    page = _req(client, "GET", "/admin/instance", session=token)
    assert page.status_code == 200
    assert 'id="server-tier"' in page.text and "data-server-tier-form" in page.text
    assert '<script src="/static/js/server_tier.js" defer></script>' in page.text
    for name in ("provider", "model", "monthly_tokens", "daily_ingests", "max_effort", "storage_mb",
                 "max_themes", "max_documents", "byok_max_concurrency"):
        assert f'name="{name}"' in page.text
    for feature in ("expand", "stt", "vlm", "presentation", "synthesize"):
        assert f'name="features" value="{feature}"' in page.text
    assert "미설정" in page.text
    js = _req(client, "GET", "/static/js/server_tier.js")
    assert js.status_code == 200 and "/api/admin/server-tier" in js.text
