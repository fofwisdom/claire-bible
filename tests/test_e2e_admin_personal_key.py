"""E2E: the server administrator registers a personal key on /settings/keys, and the jobs of the administrator account use only that key.

Owner decision 2026-11-19: the personal key pays for the jobs of the administrator account; the server providers pay for the server jobs.
The real ``GeminiProvider`` constructor selects the key. ``google.genai.Client`` is a fake that records the key, and the provider methods give the results of ``MockProvider`` (no network call).
"""

from __future__ import annotations

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
from claire.store.secrets import reset_secret_service

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
            return getattr(mock, name)(*args, **kwargs)

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

        # 5. After the deletion, the jobs use the server provider again.
        assert call("DELETE", "/api/me/keys").json() == {"ok": True, "deleted": 1}
        before = len(gemini_calls)
        third = call("POST", "/ingest", json={"payload": "Server provider document about Nazareth."})
        assert third.status_code == 200, third.text
        assert len(gemini_calls) == before
        assert SERVER_KEY not in {key for key, _ in gemini_calls}
