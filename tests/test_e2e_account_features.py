"""유저 단위 기능 E2E: 멤버가 WebUI로 텔레그램 봇·서포트 번들·데이터 번들·백업을 구성·제어하고, 운영자가 백업을 복원한다.

실제 ``server.create_app``과 실제 ``Settings``(네트워크 모드, 다중 테마)를 쓴다. 외부 호출은 없다:
텔레그램 ``getMe``는 monkeypatch하고, 봇 러너는 가짜 Application 팩토리로 돌린다.
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.kb_paths import kb_number
from starlette.testclient import TestClient

from claire.telegram import api as telegram_api
from claire.core import secret_scrub
from claire.api import server
from claire.core.config import Settings
from claire.ontology.base import Document, Entity
from claire.store import accounts as accounts_store
from claire.store import auth_db
from claire.store import db as dbm
from claire.store.secrets import reset_secret_service
from claire.store.knowledge_base import KnowledgeBaseManager
from claire.telegram.supervisor import BotSupervisor

PUBLIC_URL = "http://127.0.0.1:8765"
ORIGIN = PUBLIC_URL
MEMBER_PUBLIC_ID = 223456789099
BOT_TOKEN = "7200000001:" + "Q" * 35
ADMIN_PASSWORD = "Correct-Horse-Battery-123!"


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch):
    for key in list(os.environ):
        if key.startswith("CLAIRE_") or key.startswith("TELEGRAM_") or key.startswith("GEMINI_"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("CLAIRE_SECRET_KEY", "44" * 32)
    reset_secret_service()
    secret_scrub._reset_for_tests()
    monkeypatch.setattr(
        telegram_api,
        "verify_bot_token",
        lambda token, **_kw: telegram_api.BotIdentity(bot_id=int(token.split(":", 1)[0]), username="alice_notes_bot"),
    )
    yield
    reset_secret_service()
    secret_scrub._reset_for_tests()


class _App:
    """봇 러너가 시작·정지하는 Application 대역."""

    def __init__(self, profile) -> None:
        self.profile = profile
        self.updater = SimpleNamespace(running=False, start_polling=self._poll, stop=self._stop_poll)
        self.running = False
        self.post_init = None

    async def _poll(self):
        self.updater.running = True

    async def _stop_poll(self):
        self.updater.running = False

    async def initialize(self):
        return None

    async def start(self):
        self.running = True

    async def stop(self):
        self.running = False

    async def shutdown(self):
        return None


@pytest.fixture
def journey(tmp_path: Path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    settings = Settings(
        _env_file=None,
        CLAIRE_DB_PATH=str(data_dir / "claire.db"),
        CLAIRE_PROVIDER="mock",
        CLAIRE_ENVIRONMENT="development",
        CLAIRE_FQDN=PUBLIC_URL,
    )
    conn = dbm.connect(settings.db_file)
    try:
        dbm.init_db(conn)
        store = accounts_store.AccountStore(conn)
        store.complete_setup(1, username="operator_e2e", display_name="Operator")
        store.set_setting("mode", "network", updated_by=1)
        member = int(
            conn.execute(
                "INSERT INTO accounts (public_id, username, role, status, display_name, created_at, updated_at) "
                "VALUES (?, 'alice_e2e', 'member', 'active', 'Alice', 0, 0)",
                (MEMBER_PUBLIC_ID,),
            ).lastrowid
        )
        conn.commit()
    finally:
        conn.close()
    auth = auth_db.connect_auth_db(auth_db.auth_db_path(data_dir))
    try:
        auth_db.set_password(auth, 1, ADMIN_PASSWORD, username="operator_e2e")
    finally:
        auth.close()
    theme = KnowledgeBaseManager(settings).define_kb("Alice Garden", account_id=member, is_personal=True)
    conn = dbm.connect_existing(settings.db_file)
    try:
        dbm.insert_document(conn, Document(id="alice-doc", url="https://example.org/a", raw_text="garden body", source_type="web", content_hash="ha"), kb_id=theme.id)
        dbm.upsert_entity(conn, Entity(id="alice-ent", type="Tool", name="Spade", sources=["alice-doc"]), kb_id=theme.id)
        conn.commit()
    finally:
        conn.close()

    def session(account_id: int) -> dict[str, str]:
        c = dbm.connect_existing(settings.db_file)
        try:
            token = accounts_store.create_account_session(c, account_id=account_id, scope="owner", now=time.time())
        finally:
            c.close()
        return {"Cookie": f"claire_session={token}", "Origin": ORIGIN}

    app = server.create_app(settings, SimpleNamespace(provider=SimpleNamespace(name="mock")))
    with TestClient(app, base_url=PUBLIC_URL) as client:
        yield SimpleNamespace(client=client, settings=settings, member=member, theme=theme, alice=session(member), admin=session(1))


def _call(env, method: str, path: str, headers: dict[str, str], **kwargs):
    env.client.cookies.clear()
    response = env.client.request(method, path, headers=headers, follow_redirects=False, **kwargs)
    env.client.cookies.clear()
    return response


def test_member_configures_and_controls_every_feature_from_webui(journey):
    env = journey
    alice = env.alice

    # 1. 설정 화면: 세 기능 화면이 사이드바에 있고, 관리자 화면 링크는 없다. The bundles page is /support/bundles.
    feature_paths = {"telegram": "/account/telegram", "bundles": "/support/bundles", "backups": "/account/backups"}
    for slug, path in feature_paths.items():
        page = _call(env, "GET", path, alice)
        assert page.status_code == 200 and f'data-feature-section="{slug}"' in page.text
        for other in feature_paths.values():
            assert f'href="{other}"' in page.text
        assert "/admin/" not in page.text
    assert _call(env, "GET", "/admin/backups", alice).status_code == 404

    # 2. 텔레그램 봇: 등록 → 러너가 시작 → 화면에 실행 중 → 끄기 → 러너가 정지.
    r = _call(env, "PUT", "/api/me/telegram", alice, json={"bot_token": BOT_TOKEN, "allowed_user_ids": "555001"})
    assert r.status_code == 200, r.text
    supervisor = BotSupervisor(env.settings, app_factory=lambda _s, *, profile: _App(profile))
    asyncio.run(supervisor.sync())
    assert set(supervisor.running) == {("account", env.member)}
    assert supervisor.running[("account", env.member)].profile.allowed_user_ids == (555001,)
    page = _call(env, "GET", "/account/telegram", alice).text
    assert "@alice_notes_bot" in page and "Running" in page and BOT_TOKEN not in page
    assert _call(env, "PATCH", "/api/me/telegram", alice, json={"enabled": False}).status_code == 200
    asyncio.run(supervisor.sync())
    assert supervisor.running == {}

    # 3. 서포트 번들과 데이터 번들 (/support/bundles): 만들기 → 목록 → 링크 → 지우기. The page gives a link, not a download.
    r = _call(env, "POST", "/api/support/bundles", alice, json={"scope": "account", "days": 2})
    assert r.status_code == 200, r.text
    support_id = r.json()["bundle"]["bundle_id"]
    support_url = r.json()["bundle"]["link"]
    r = _call(env, "POST", "/api/me/data-bundles", alice, json={"kb": kb_number(env.settings, env.theme.id)})
    assert r.status_code == 200, r.text
    data_id = r.json()["bundle"]["bundle_id"]
    assert r.json()["bundle"]["entities_count"] == 1
    data_url = r.json()["bundle"]["download_url"]
    # The link from the create response has an unpredictable token and works without a session.
    for url, kind, bundle_id in ((support_url, "support", support_id), (data_url, "data", data_id)):
        assert url.startswith(PUBLIC_URL + "/") and ("/support/bundle/" in url or "/support/graph-bundle/" in url) and bundle_id not in url
        shared = env.client.get(url[len(PUBLIC_URL):])
        assert shared.status_code == 200 and shared.content[:4] == b"\x28\xb5\x2f\xfd", (kind, url, shared.text[:200])
    # The list gives the same link to the owner at each time: the copy link of the row stays after a reload.
    listed = _call(env, "GET", "/api/support/bundles", alice).json()["bundles"]
    assert [(b["bundle_id"], b["link"]) for b in listed] == [(support_id, support_url)]
    listed = _call(env, "GET", "/api/me/data-bundles", alice).json()["bundles"]
    assert [(b["bundle_id"], b["download_url"]) for b in listed] == [(data_id, data_url)]
    page = _call(env, "GET", "/support/bundles", alice).text
    assert 'data-feature-form="support-bundle-create"' in page and 'data-feature-form="data-bundle-create"' in page
    assert _call(env, "DELETE", f"/api/support/bundles/{support_id}", alice).status_code == 200
    assert _call(env, "DELETE", f"/api/me/data-bundles?bundle_id={data_id}", alice).status_code == 200
    assert _call(env, "GET", "/api/support/bundles", alice).json()["bundles"] == []
    assert _call(env, "GET", "/api/me/data-bundles", alice).json()["bundles"] == []

    # 4. 백업: 자동 백업 주기 설정 → 수동 백업 → 내려받기.
    r = _call(env, "PUT", "/api/me/backups/policy", alice, json={"schedule": "weekly", "keep": 4})
    assert r.status_code == 200 and r.json()["policy"]["schedule"] == "weekly"
    r = _call(env, "POST", "/api/me/backups", alice, json={})
    assert r.status_code == 200, r.text
    backup_id = r.json()["backup"]["backup_id"]
    listing = _call(env, "GET", "/api/me/backups", alice).json()
    assert [b["backup_id"] for b in listing["backups"]] == [backup_id] and listing["policy"]["keep"] == 4
    assert _call(env, "GET", f"/api/me/backups/download?backup_id={backup_id}", alice).status_code == 200

    # 5. 데이터가 사라진 뒤: 멤버는 복원하지 못하고 운영자가 복원한다.
    conn = sqlite3.connect(env.settings.db_file)
    try:
        conn.execute("DELETE FROM entities WHERE kb_id=?", (env.theme.id,))
        conn.execute("DELETE FROM documents WHERE kb_id=?", (env.theme.id,))
        conn.commit()
    finally:
        conn.close()
    payload = {"account": str(MEMBER_PUBLIC_ID), "backup_id": backup_id}
    assert _call(env, "POST", "/api/admin/account-backups/restore", alice, json=payload).status_code == 404
    admin_page = _call(env, "GET", "/admin/backups", env.admin)
    assert admin_page.status_code == 200 and 'data-feature-section="admin-backups"' in admin_page.text
    r = _call(env, "POST", "/api/admin/account-backups/restore", env.admin, json=payload)
    assert r.status_code == 200, r.text
    conn = sqlite3.connect(env.settings.db_file)
    try:
        assert conn.execute("SELECT COUNT(*) FROM documents WHERE kb_id=?", (env.theme.id,)).fetchone()[0] == 1
        assert conn.execute("SELECT name FROM entities WHERE kb_id=?", (env.theme.id,)).fetchone()[0] == "Spade"
        # 검색 색인(FTS)도 함께 되돌린다.
        assert conn.execute("SELECT COUNT(*) FROM entities_fts WHERE entity_id='alice-ent'").fetchone()[0] == 1
    finally:
        conn.close()
