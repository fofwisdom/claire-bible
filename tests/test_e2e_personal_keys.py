"""E2E: the server administrator registers a personal key on /settings/keys, and the jobs of the administrator account use only that key.

Owner decision 2026-11-19: the personal key pays for the jobs of the administrator account; the server providers pay for the server jobs.
The real ``GeminiProvider`` constructor selects the key. ``google.genai.Client`` is a fake that records the key, and the provider methods give the results of ``MockProvider`` (no network call).
"""

from __future__ import annotations

import inspect
import os
import time
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from claire import secret_scrub
from claire.api import server
from claire.config import Settings
from claire.extract import gemini_provider
from claire.extract.provider import MockProvider
from claire.store import accounts as accounts_store
from claire.store import auth_db, provider_keys, usage_ledger
from claire.store import db as dbm
from claire.store.knowledge_base import KnowledgeBaseManager
from claire.store.secrets import BYOK_SEAL_COOKIE, SPLIT_KEY_VERSION_OFFSET, clear_volatile_user_seals, reset_secret_service

PUBLIC_URL = "http://127.0.0.1:8765"
SERVER_KEY = "canary-server-key-5d6e7f8091a2b3c4"
PERSONAL_KEY = "canary-admin-personal-key-0a1b2c3d4e5f"
TEST_MASTER_KEY_HEX = "44" * 32
PROVIDER_METHODS = ("extract", "embed", "judge_same_entity", "judge_relationship", "summarize_document", "render_detail", "classify_watch", "classify_paper")


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch):
    for key in list(os.environ):
        if key.startswith(("CLAIRE_", "GEMINI_", "TELEGRAM_")):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("CLAIRE_SECRET_KEY", TEST_MASTER_KEY_HEX)
    reset_secret_service()
    secret_scrub._reset_for_tests()
    monkeypatch.setattr(provider_keys, "verify_key", lambda provider, api_key: (True, "ok"))
    yield
    reset_secret_service()
    secret_scrub._reset_for_tests()


@pytest.fixture
def gemini_calls(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """Each call of a Gemini method records (API key of the client, credential key) and reports 5 + 5 tokens to the usage hooks."""
    import google.genai as genai

    calls: list[tuple[str, str]] = []

    class _FakeClient:
        def __init__(self, api_key=None, **_kwargs):
            self.api_key = api_key

    monkeypatch.setattr(genai, "Client", _FakeClient)
    mock = MockProvider()

    def _wrap(name: str):
        def method(self, *args, **kwargs):
            calls.append((self.client.api_key, self.credential_key))
            hooks = getattr(self, "usage_hooks", None)
            if hooks is not None:
                hooks.before_call()
                hooks.after_call(5, 5)
            target = getattr(mock, name)
            params = inspect.signature(target).parameters
            return target(*args, **{k: v for k, v in kwargs.items() if k in params})

        return method

    for name in PROVIDER_METHODS:
        monkeypatch.setattr(gemini_provider.GeminiProvider, name, _wrap(name))
    return calls


def _session(db_file: Path, account_id: int) -> str:
    conn = dbm.connect_existing(db_file)
    try:
        return accounts_store.create_account_session(conn, account_id=account_id, scope="owner", now=time.time())
    finally:
        conn.close()


def _key_versions(data_dir: Path) -> list[int]:
    auth = auth_db.connect_auth_db(auth_db.auth_db_path(data_dir))
    try:
        return [int(r[0]) for r in auth.execute("SELECT key_version FROM account_provider_keys WHERE account_id=1").fetchall()]
    finally:
        auth.close()


def _setup(tmp_path: Path) -> tuple[Path, Path, Settings]:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    db_file = data_dir / "claire.db"
    conn = dbm.connect(db_file)
    try:
        dbm.init_db(conn)
        accounts_store.AccountStore(conn).complete_setup(1, username="admin_e2e", display_name="Admin")
    finally:
        conn.close()
    settings = Settings(
        _env_file=None,
        CLAIRE_DB_PATH=str(db_file),
        CLAIRE_DATA_DIR=str(data_dir),
        CLAIRE_PROVIDER="mock",
        CLAIRE_ENVIRONMENT="development",
        CLAIRE_PUBLIC_URL=PUBLIC_URL,
        CLAIRE_MULTI_THEME=True,
        GEMINI_API_KEY=SERVER_KEY,
        CLAIRE_GEMINI_MODEL="gemini-3.8-flash",
    )
    KnowledgeBaseManager(settings)
    return data_dir, db_file, settings


def test_e2e_sealed_key_is_unsealed_on_the_next_visit(tmp_path: Path, gemini_calls) -> None:
    """A key stored with a browser seal (old rule) becomes usable without the seal after the next visit of /settings/keys."""
    data_dir, db_file, settings = _setup(tmp_path)
    auth = auth_db.connect_auth_db(auth_db.auth_db_path(data_dir))
    try:
        provider_keys.put_key(auth, account_id=1, provider="gemini", api_key=PERSONAL_KEY, status="active", user_seal="browser-seal-1", sealed=True)
    finally:
        auth.close()
    assert _key_versions(data_dir)[0] >= SPLIT_KEY_VERSION_OFFSET
    clear_volatile_user_seals()
    app = server.create_app(settings=settings)
    with TestClient(app, base_url=PUBLIC_URL) as client:
        session = _session(db_file, 1)
        client.cookies.clear()
        listed = client.get("/api/me/keys", headers={"Cookie": f"claire_session={session}; {BYOK_SEAL_COOKIE}=browser-seal-1", "Origin": PUBLIC_URL})
        assert listed.status_code == 200 and len(listed.json()["keys"]) == 1
        assert _key_versions(data_dir) == [1]
        clear_volatile_user_seals()
        client.cookies.clear()
        ingested = client.post("/ingest", json={"payload": "Document after the unseal."}, headers={"Cookie": f"claire_session={session}", "Origin": PUBLIC_URL})
        assert ingested.status_code == 200, ingested.text
        assert gemini_calls and {key for key, _ in gemini_calls} == {PERSONAL_KEY}


def test_e2e_admin_personal_key_pays_for_admin_jobs(tmp_path: Path, gemini_calls) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    db_file = data_dir / "claire.db"
    conn = dbm.connect(db_file)
    try:
        dbm.init_db(conn)
        accounts_store.AccountStore(conn).complete_setup(1, username="admin_e2e", display_name="Admin")
    finally:
        conn.close()
    auth = auth_db.connect_auth_db(auth_db.auth_db_path(data_dir))
    try:
        auth_db.set_password(auth, 1, "Correct-Horse-Battery-123!", username="admin_e2e")
    finally:
        auth.close()
    settings = Settings(
        _env_file=None,
        CLAIRE_DB_PATH=str(db_file),
        CLAIRE_DATA_DIR=str(data_dir),
        CLAIRE_PROVIDER="mock",
        CLAIRE_ENVIRONMENT="development",
        CLAIRE_PUBLIC_URL=PUBLIC_URL,
        CLAIRE_MULTI_THEME=True,
        GEMINI_API_KEY=SERVER_KEY,
        CLAIRE_GEMINI_MODEL="gemini-3.8-flash",
    )
    KnowledgeBaseManager(settings)
    app = server.create_app(settings=settings)

    with TestClient(app, base_url=PUBLIC_URL) as client:
        session = _session(db_file, 1)

        def call(method: str, path: str, **kwargs):
            # The server can renew the session cookie. Send the session in a header and keep the cookie jar empty.
            client.cookies.clear()
            return client.request(method, path, headers={"Cookie": f"claire_session={session}", "Origin": PUBLIC_URL}, **kwargs)

        # 1. Single mode: the administrator page has the personal key forms and the link to the server providers.
        page = call("GET", "/settings/keys")
        assert page.status_code == 200
        assert 'data-settings-form="provider-key-put"' in page.text and 'href="/admin/providers"' in page.text

        # 2. Without a personal key, a job of the administrator account uses the server provider (mock): no Gemini call.
        first = call("POST", "/ingest", json={"payload": "Server provider document about Jerusalem."})
        assert first.status_code == 200, first.text
        assert gemini_calls == []

        # 3. The administrator registers a personal key. The response has only metadata.
        put = call("PUT", "/api/me/keys", json={"provider": "gemini", "api_key": PERSONAL_KEY})
        assert put.status_code == 200, put.text
        assert PERSONAL_KEY not in put.text

        assert BYOK_SEAL_COOKIE not in put.cookies
        assert _key_versions(data_dir) == [1]
        # The Telegram bot process and a restarted server have no browser seal.
        clear_volatile_user_seals()

        # 4. A job of the administrator account now uses only the personal key, and the ledger records its tokens.
        second = call("POST", "/ingest", json={"payload": "Personal key document about Galilee."})
        assert second.status_code == 200, second.text
        assert gemini_calls, "the job made no Gemini call"
        assert {key for key, _ in gemini_calls} == {PERSONAL_KEY}
        assert {cred for _, cred in gemini_calls} == {f"byok:{put.json()['key']['fingerprint']}"}
        auth = auth_db.connect_auth_db(auth_db.auth_db_path(data_dir))
        try:
            assert usage_ledger.get_usage(auth, account_id=1).tokens_total == 10 * len(gemini_calls)
        finally:
            auth.close()
        page = call("GET", "/settings/keys")
        assert put.json()["key"]["fingerprint"] in page.text and 'data-usage-stat="tokens"' in page.text
        # The decryption audit of the job is on the page.
        assert "data-key-audit-row" in page.text and "Preparation of a request" in page.text

        # 5. After the deletion, the jobs use the server provider again.
        assert call("DELETE", "/api/me/keys").json() == {"ok": True, "deleted": 1}
        before = len(gemini_calls)
        third = call("POST", "/ingest", json={"payload": "Server provider document about Nazareth."})
        assert third.status_code == 200, third.text
        assert len(gemini_calls) == before
        assert SERVER_KEY not in {key for key, _ in gemini_calls}


MEMBER_KEY = "canary-member-personal-key-6a7b8c9d0e1f"


def test_e2e_member_key_works_in_the_bot_process_and_not_in_a_loop_process(tmp_path: Path, gemini_calls, monkeypatch: pytest.MonkeyPatch) -> None:
    """A member registers a key on the web. A process without a web request (as the Telegram bot process) ingests with that key. A worker loop process does not use the key."""
    from claire.accounts import provider_policy
    from claire.ingest.service import IngestServicePool

    data_dir, db_file, settings = _setup(tmp_path)
    conn = dbm.connect_existing(db_file)
    try:
        now = time.time()
        conn.execute(
            "INSERT INTO accounts (id, public_id, username, role, status, display_name, created_at, activated_at, updated_at) VALUES (2, 223456789077, 'member_e2e', 'member', 'active', 'Member', ?, ?, ?)",
            (now, now, now),
        )
        accounts_store.AccountStore(conn).set_setting("mode", "network", updated_by=1)
        conn.commit()
    finally:
        conn.close()
    tm = KnowledgeBaseManager(settings)
    member_kb = tm.define_kb("Member personal", is_personal=True, account_id=2)
    app = server.create_app(settings=settings)
    with TestClient(app, base_url=PUBLIC_URL) as client:
        client.cookies.clear()
        put = client.put("/api/me/keys", json={"provider": "gemini", "api_key": MEMBER_KEY}, headers={"Cookie": f"claire_session={_session(db_file, 2)}", "Origin": PUBLIC_URL})
        assert put.status_code == 200, put.text
        assert BYOK_SEAL_COOKIE not in put.cookies

    # The bot process: no web request of the member, no seal in memory. It builds its own service pool (telegram_bot.py).
    clear_volatile_user_seals()
    monkeypatch.setattr(provider_keys, "_PROCESS_LABEL", ["bot"])
    pool = IngestServicePool(settings, None, theme_manager=KnowledgeBaseManager(settings), account_id=2)
    report = pool.get_service(member_kb.id).ingest("Bot message about the Sea of Galilee.", source="telegram", expand_max=0)
    assert not getattr(report, "error", None), report
    assert gemini_calls and {key for key, _ in gemini_calls} == {MEMBER_KEY}
    auth = auth_db.connect_auth_db(auth_db.auth_db_path(data_dir))
    try:
        records = provider_keys.list_key_decryptions(auth, account_id=2, limit=50)
    finally:
        auth.close()
    assert any(r.process == "bot" and r.purpose == "request" for r in records)
    bot_count = sum(r.count for r in records)

    # A worker loop process (recover, refresh, expand) refuses the personal key and does not change to the server key.
    monkeypatch.setattr(provider_policy, "_PERSONAL_KEYS_IN_PROCESS", [True])
    provider_policy.refuse_personal_keys_in_this_process()
    before = len(gemini_calls)
    loop_pool = IngestServicePool(settings, None, theme_manager=KnowledgeBaseManager(settings), account_id=2)
    loop_svc = loop_pool.get_service(member_kb.id)
    assert loop_svc.provider_error is not None and loop_svc.provider_error.code == "provider_not_allowed"
    loop_report = loop_svc.ingest("Loop document about Capernaum.", source="refresh", expand_max=0)
    assert getattr(loop_report, "error", None)
    assert len(gemini_calls) == before
    # The loop refused the key before a decryption: no new record.
    auth = auth_db.connect_auth_db(auth_db.auth_db_path(data_dir))
    try:
        assert sum(r.count for r in provider_keys.list_key_decryptions(auth, account_id=2, limit=50)) == bot_count
    finally:
        auth.close()
