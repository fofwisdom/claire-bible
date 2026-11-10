"""유저 단위 기능: 텔레그램 봇·서포트 번들·데이터 번들·백업 (api.accounts_routes.features).

- 계정 소유자는 자기 기능만 구성·제어한다. 다른 계정의 번들·백업은 404다.
- 봇 토큰은 쓰기 전용이다. 응답·DB 평문 어디에도 없다. 텔레그램 시험 호출은 monkeypatch한다(외부 호출 없음).
- 복원은 운영자 전용이며 단계 인증을 요구한다.

모든 상태는 ``tmp_path``에만 쓴다.
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import sqlite3
import tarfile
import time
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import zstandard as zstd
from starlette.testclient import TestClient

from claire import account_backup, secret_scrub, telegram_api
from claire.api import server
from claire.ontology.base import Document, Entity
from claire.store import accounts as accounts_store
from claire.store import auth_db
from claire.store import db as dbm
from claire.store import telegram_bots
from claire.store.secrets import reset_secret_service
from claire.store.theme import get_theme_manager

PUBLIC_URL = "http://127.0.0.1:8765"
ORIGIN = PUBLIC_URL
OWNER_TOKEN = "owner-" + "o" * 32
ADMIN_PASSWORD = "Correct-Horse-Battery-123!"
TEST_MASTER_KEY_HEX = "22" * 32
BOT_TOKEN_A = "7100000001:" + "A" * 35
BOT_TOKEN_B = "7100000002:" + "B" * 35
INSTANCE_BOT_TOKEN = "7100000009:" + "I" * 35


@dataclass
class _Settings:
    db_file: Path
    data_dir: Path
    environment: str = "development"
    public_url: str = PUBLIC_URL
    inject_token: str = OWNER_TOKEN
    readonly_token: str = "readonly-" + "r" * 32
    cors_allowed_origins: str = ""
    anonymous_readonly: bool = False
    fqdn: str = "notes.kimhome.net"
    multi_theme: bool = True
    telegram_bot_token: str = INSTANCE_BOT_TOKEN
    telemetry_retention_days: int = 30


class _Client:
    def __init__(self, client: TestClient) -> None:
        self.client = client
        self.responses: list[Any] = []

    def request(self, method: str, path: str, *, session: str | None = None, **kwargs: Any):
        headers = dict(kwargs.pop("headers", {}) or {})
        if session is not None:
            headers["Cookie"] = f"claire_session={session}"
        if method not in {"GET", "HEAD"}:
            headers["Origin"] = ORIGIN
        self.client.cookies.clear()
        response = self.client.request(method, path, headers=headers, follow_redirects=False, **kwargs)
        self.client.cookies.clear()
        self.responses.append(response)
        return response

    def assert_never_leaked(self, *values: str) -> None:
        for response in self.responses:
            blob = response.content.decode("utf-8", errors="replace") + repr(list(response.headers.items()))
            for value in values:
                assert value not in blob


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch):
    for key in list(os.environ):
        if key.startswith("CLAIRE_") or key.startswith("TELEGRAM_"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("CLAIRE_SECRET_KEY", TEST_MASTER_KEY_HEX)
    reset_secret_service()
    secret_scrub._reset_for_tests()
    yield
    reset_secret_service()
    secret_scrub._reset_for_tests()


@pytest.fixture
def fake_telegram(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """``verify_bot_token`` 대역. 토큰 앞 숫자를 봇 ID로 돌려준다. ``Z``가 든 토큰은 거부한다."""
    calls: list[str] = []

    def _fake(token: str, **_kw: Any) -> telegram_api.BotIdentity:
        calls.append(token)
        if "Z" in token:
            raise telegram_api.TelegramVerifyRejected("rejected")
        return telegram_api.BotIdentity(bot_id=int(token.split(":", 1)[0]), username=f"bot{token[-3:].lower()}")

    monkeypatch.setattr(telegram_api, "verify_bot_token", _fake)
    return calls


def _session(settings: _Settings, account_id: int, *, authenticated_ago: float = 0.0, scope: str = "owner") -> str:
    conn = dbm.connect_existing(settings.db_file)
    try:
        return accounts_store.create_account_session(conn, account_id=account_id, scope=scope, now=time.time() - authenticated_ago)
    finally:
        conn.close()


def _add_member(settings: _Settings, username: str, public_id: int) -> int:
    conn = dbm.connect_existing(settings.db_file)
    try:
        cur = conn.execute(
            "INSERT INTO accounts (public_id, username, role, status, created_at, updated_at) VALUES (?, ?, 'member', 'active', 0, 0)",
            (public_id, username),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def _seed_theme(settings: _Settings, account_id: int, label: str, *, prefix: str) -> int:
    theme = get_theme_manager(settings).define_theme(label, account_id=account_id, is_default=True)
    conn = dbm.connect_existing(settings.db_file)
    try:
        dbm.insert_document(conn, Document(id=f"{prefix}-d1", url=f"https://x/{prefix}/1", raw_text=f"body {prefix}", source_type="web", content_hash=f"h-{prefix}"), theme_id=theme.id)
        dbm.upsert_entity(conn, Entity(id=f"{prefix}-e1", type="Tool", name=f"Tool {prefix}", sources=[f"{prefix}-d1"]), theme_id=theme.id)
        conn.execute(
            "INSERT INTO raw_inbox (theme_id, received_at, source, kind, payload, status, error, attempts, last_attempt) VALUES (?, ?, 'test', 'url', ?, 'failed', 'boom', 3, ?)",
            (theme.id, time.time(), f"https://x/{prefix}/fail", time.time()),
        )
        conn.commit()
    finally:
        conn.close()
    return theme.id


@pytest.fixture
def env(tmp_path: Path):
    settings = _Settings(db_file=tmp_path / "claire.db", data_dir=tmp_path)
    conn = dbm.connect(settings.db_file)
    try:
        dbm.init_db(conn)
        store = accounts_store.AccountStore(conn)
        store.complete_setup(1, username="admin_kim", display_name="Admin Kim")
        store.set_setting("mode", "network", updated_by=1)
    finally:
        conn.close()
    auth = auth_db.connect_auth_db(auth_db.auth_db_path(tmp_path))
    try:
        auth_db.set_password(auth, 1, ADMIN_PASSWORD, username="admin_kim")
    finally:
        auth.close()
    member_a = _add_member(settings, "member_alpha", 223456789012)
    member_b = _add_member(settings, "member_bravo", 323456789012)
    theme_a = _seed_theme(settings, member_a, "Alpha notes", prefix="alpha")
    theme_b = _seed_theme(settings, member_b, "Bravo notes", prefix="bravo")
    app = server.create_app(settings, SimpleNamespace(provider=SimpleNamespace(name="stub")))
    with TestClient(app, base_url=PUBLIC_URL) as tc:
        yield SimpleNamespace(
            client=_Client(tc), settings=settings, a=member_a, b=member_b, theme_a=theme_a, theme_b=theme_b,
            sa=_session(settings, member_a), sb=_session(settings, member_b), admin=_session(settings, 1),
        )


# --- 텔레그램 봇 저장소 (I/O) ---------------------------------------------------------------


def test_bot_store_encrypts_token_and_enforces_unique_bot(tmp_path: Path):
    auth = auth_db.connect_auth_db(auth_db.auth_db_path(tmp_path))
    try:
        meta = telegram_bots.put_bot(auth, account_id=5, token=BOT_TOKEN_A, bot_username="alpha_bot", allowed_user_ids=[11, 12])
        assert meta.bot_id == 7100000001 and meta.status == "pending" and meta.allowed_user_ids == (11, 12)
        raw = b"".join(bytes(v) if isinstance(v, (bytes, memoryview)) else str(v).encode() for v in auth.execute("SELECT * FROM account_telegram_bots").fetchone())
        assert BOT_TOKEN_A.encode() not in raw
        with pytest.raises(telegram_bots.TelegramBotConflict):
            telegram_bots.put_bot(auth, account_id=6, token=BOT_TOKEN_A, bot_username="x", allowed_user_ids=[1])
        with pytest.raises(telegram_bots.TelegramBotConflict):
            telegram_bots.put_bot(auth, account_id=6, token=BOT_TOKEN_B, bot_username="x", allowed_user_ids=[1], reserved_bot_ids=[7100000002])
        profiles, errors = telegram_bots.load_enabled_bots(auth)
        assert errors == {} and [p.token for p in profiles] == [BOT_TOKEN_A]
        assert BOT_TOKEN_A not in repr(profiles[0])
        telegram_bots.update_settings(auth, account_id=5, enabled=False)
        assert telegram_bots.load_enabled_bots(auth)[0] == []
        assert telegram_bots.get_meta(auth, account_id=5).status == "disabled"
        # AAD: 암호문을 다른 계정 행으로 옮기면 복호화하지 못한다.
        auth.execute("UPDATE account_telegram_bots SET enabled=1, account_id=9 WHERE account_id=5")
        auth.commit()
        profiles, errors = telegram_bots.load_enabled_bots(auth)
        assert profiles == [] and 9 in errors
    finally:
        auth.close()


def test_allowed_user_ids_parser():
    assert telegram_bots.parse_allowed_user_ids("12, 34 34") == (12, 34)
    assert telegram_bots.parse_allowed_user_ids([5]) == (5,)
    for bad in ("", [], "abc", [0], [True], list(range(1, 22))):
        assert telegram_bots.parse_allowed_user_ids(bad) is None
    assert telegram_bots.parse_bot_token("123:short") is None
    assert telegram_bots.parse_bot_token(f"  {BOT_TOKEN_A} ") == (7100000001, BOT_TOKEN_A)


# --- 텔레그램 API ------------------------------------------------------------------------------


def test_telegram_register_update_delete_and_isolation(env, fake_telegram):
    c = env.client
    # 단계 인증이 지난 세션은 등록하지 못한다.
    stale = _session(env.settings, env.a, authenticated_ago=3600)
    r = c.request("PUT", "/api/me/telegram", session=stale, json={"bot_token": BOT_TOKEN_A, "allowed_user_ids": "11"})
    assert r.status_code == 401 and r.json()["error"] == "reauth_required"
    # 허용 사용자가 없으면 거부한다.
    r = c.request("PUT", "/api/me/telegram", session=env.sa, json={"bot_token": BOT_TOKEN_A, "allowed_user_ids": ""})
    assert r.status_code == 400 and r.json()["error"] == "invalid_allowed_user_ids"
    # 인스턴스 봇과 같은 봇은 거부한다(텔레그램 호출 전).
    r = c.request("PUT", "/api/me/telegram", session=env.sa, json={"bot_token": INSTANCE_BOT_TOKEN, "allowed_user_ids": "11"})
    assert r.status_code == 409 and r.json()["error"] == "bot_in_use"
    # 텔레그램이 거부한 토큰은 저장하지 않는다.
    r = c.request("PUT", "/api/me/telegram", session=env.sa, json={"bot_token": "7100000003:" + "Z" * 35, "allowed_user_ids": "11"})
    assert r.status_code == 422
    r = c.request("PUT", "/api/me/telegram", session=env.sa, json={"bot_token": BOT_TOKEN_A, "allowed_user_ids": "11, 12"})
    assert r.status_code == 200, r.text
    bot = r.json()["bot"]
    assert bot["bot_username"] == "botaaa" and bot["allowed_user_ids"] == [11, 12] and bot["status"] == "pending"
    # 다른 계정은 같은 봇을 등록할 수 없고, 내 봇 정보를 보지 못한다.
    r = c.request("PUT", "/api/me/telegram", session=env.sb, json={"bot_token": BOT_TOKEN_A, "allowed_user_ids": "13"})
    assert r.status_code == 409
    assert c.request("GET", "/api/me/telegram", session=env.sb).json() == {"bot": None}
    # 끄기는 단계 인증 없이, 허용 사용자 변경은 단계 인증을 요구한다.
    r = c.request("PATCH", "/api/me/telegram", session=stale, json={"enabled": False})
    assert r.status_code == 200 and r.json()["bot"]["enabled"] is False and r.json()["bot"]["status"] == "disabled"
    r = c.request("PATCH", "/api/me/telegram", session=stale, json={"allowed_user_ids": [99]})
    assert r.status_code == 401
    r = c.request("PATCH", "/api/me/telegram", session=env.sa, json={"allowed_user_ids": [99]})
    assert r.status_code == 200 and r.json()["bot"]["allowed_user_ids"] == [99]
    # 설정 화면은 상태를 보여 주고 토큰을 싣지 않는다.
    page = c.request("GET", "/settings/telegram", session=env.sa)
    assert page.status_code == 200 and "data-telegram-meta" in page.text and "console_features.js" in page.text
    assert "/admin/" not in page.text
    r = c.request("DELETE", "/api/me/telegram", session=env.sa)
    assert r.status_code == 200 and r.json()["deleted"] == 1
    assert c.request("GET", "/api/me/telegram", session=env.sa).json() == {"bot": None}
    # 익명은 404.
    assert c.request("GET", "/api/me/telegram").status_code == 404
    c.assert_never_leaked(BOT_TOKEN_A, BOT_TOKEN_A.split(":", 1)[1])


# --- 서포트 번들 ---------------------------------------------------------------------------------


def _read_tar_zst(data: bytes) -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    with zstd.ZstdDecompressor().stream_reader(io.BytesIO(data)) as reader, tarfile.open(fileobj=reader, mode="r|") as tar:
        for member in tar:
            f = tar.extractfile(member)
            if f is not None:
                out[member.name] = f.read()
    return out


def test_account_support_bundle_is_scoped_to_caller(env):
    c = env.client
    r = c.request("POST", "/api/me/support-bundles", session=env.sa, json={"days": 1, "target": "alpha-d1"})
    assert r.status_code == 200, r.text
    bundle = r.json()["bundle"]
    assert bundle["target_doc_id"] == "alpha-d1"
    listed = c.request("GET", "/api/me/support-bundles", session=env.sa).json()["bundles"]
    assert [b["bundle_id"] for b in listed] == [bundle["bundle_id"]]
    assert "token" not in json.dumps(listed)
    # 다른 계정은 목록에서 보지 못하고 내려받지 못한다.
    assert c.request("GET", "/api/me/support-bundles", session=env.sb).json()["bundles"] == []
    assert c.request("GET", f"/api/me/support-bundles/download?bundle_id={bundle['bundle_id']}", session=env.sb).status_code == 404
    dl = c.request("GET", f"/api/me/support-bundles/download?bundle_id={bundle['bundle_id']}", session=env.sa)
    assert dl.status_code == 200 and dl.headers["cache-control"].startswith("no-store")
    files = _read_tar_zst(dl.content)
    blob = b"".join(files.values()).decode("utf-8")
    manifest = json.loads(next(v for k, v in files.items() if k.endswith("manifest.json")))
    assert manifest["scope"] == "account"
    assert "https://x/alpha/fail" in blob  # 내 실패 항목
    assert "bravo" not in blob  # 다른 계정 정보 없음
    assert "body alpha" not in blob  # 문서 본문 없음
    assert not any("diagnostics/storage" in k or "/logs/" in k for k in files)
    # 다른 계정 문서를 대상으로 지정해도 찾지 못한다.
    r = c.request("POST", "/api/me/support-bundles", session=env.sa, json={"days": 1, "target": "bravo-d1"})
    assert r.status_code == 200 and r.json()["bundle"]["target_resolution_status"] == "not_found"
    assert c.request("DELETE", f"/api/me/support-bundles?bundle_id={bundle['bundle_id']}", session=env.sb).status_code == 404
    assert c.request("DELETE", f"/api/me/support-bundles?bundle_id={bundle['bundle_id']}", session=env.sa).status_code == 200
    assert len(c.request("GET", "/api/me/support-bundles", session=env.sa).json()["bundles"]) == 1


# --- 데이터 번들 -----------------------------------------------------------------------------------


def test_data_bundle_lifecycle_and_isolation(env):
    c = env.client
    listing = c.request("GET", "/api/me/data-bundles", session=env.sa).json()
    assert [t["theme_id"] for t in listing["themes"]] == [env.theme_a]
    r = c.request("POST", "/api/me/data-bundles", session=env.sa, json={"theme": str(env.theme_b)})
    assert r.status_code == 404
    r = c.request("POST", "/api/me/data-bundles", session=env.sa, json={})
    assert r.status_code == 200, r.text
    created = r.json()["bundle"]
    assert created["entities_count"] == 1 and created["documents_count"] == 1
    assert [b["bundle_id"] for b in c.request("GET", "/api/me/data-bundles", session=env.sa).json()["bundles"]] == [created["bundle_id"]]
    assert c.request("GET", "/api/me/data-bundles", session=env.sb).json()["bundles"] == []
    assert c.request("GET", f"/api/me/data-bundles/download?bundle_id={created['bundle_id']}", session=env.sb).status_code == 404
    dl = c.request("GET", f"/api/me/data-bundles/download?bundle_id={created['bundle_id']}", session=env.sa)
    assert dl.status_code == 200 and any(k.endswith("graph_export.db") for k in _read_tar_zst(dl.content))
    assert c.request("DELETE", f"/api/me/data-bundles?bundle_id={created['bundle_id']}", session=env.sa).status_code == 200
    assert c.request("GET", "/api/me/data-bundles", session=env.sa).json()["bundles"] == []


# --- 백업 -------------------------------------------------------------------------------------------


def _doc_count(settings: _Settings, theme_id: int) -> int:
    conn = sqlite3.connect(settings.db_file)
    try:
        return conn.execute("SELECT COUNT(*) FROM documents WHERE theme_id=?", (theme_id,)).fetchone()[0]
    finally:
        conn.close()


def test_backup_policy_and_scheduled_run(env):
    s = env.settings
    policy = account_backup.save_policy(s, account_id=env.a, schedule="daily", keep=2, now=1000.0)
    assert policy.next_run_at() == 1000.0
    with pytest.raises(ValueError):
        account_backup.save_policy(s, account_id=env.a, schedule="hourly", keep=2)
    results = account_backup.run_due_backups(s, now_epoch=2000.0)
    assert [r["status"] for r in results] == ["ok"] and results[0]["account_id"] == env.a
    assert account_backup.run_due_backups(s, now_epoch=2000.0 + 3600) == []
    account_backup.run_due_backups(s, now_epoch=2000.0 + 86400)
    account_backup.run_due_backups(s, now_epoch=2000.0 + 2 * 86400)
    # 보존 개수(2)를 넘는 백업은 지운다.
    assert len(account_backup.list_account_backups(s, account_id=env.a)) == 2
    assert account_backup.list_account_backups(s, account_id=env.b) == []


def test_backup_restore_round_trip_requires_admin(env):
    c = env.client
    s = env.settings
    r = c.request("POST", "/api/me/backups", session=env.sa, json={})
    assert r.status_code == 200, r.text
    backup_id = r.json()["backup"]["backup_id"]
    assert c.request("POST", "/api/me/backups", session=env.sa, json={}).status_code == 409  # 1분 간격
    assert c.request("GET", f"/api/me/backups/download?backup_id={backup_id}", session=env.sb).status_code == 404
    dl = c.request("GET", f"/api/me/backups/download?backup_id={backup_id}", session=env.sa)
    files = _read_tar_zst(dl.content)
    assert set(files) == {"manifest.json", f"themes/{env.theme_a}.db"}

    # 백업 뒤 데이터가 사라졌다고 하자.
    conn = dbm.connect_existing(s.db_file)
    try:
        conn.execute("DELETE FROM documents WHERE theme_id=?", (env.theme_a,))
        conn.commit()
    finally:
        conn.close()
    assert _doc_count(s, env.theme_a) == 0

    payload = {"account": "223456789012", "backup_id": backup_id}
    # 멤버는 복원할 수 없다(존재를 숨긴다).
    assert c.request("POST", "/api/admin/account-backups/restore", session=env.sa, json=payload).status_code == 404
    assert c.request("GET", "/admin/backups", session=env.sa).status_code == 404
    # 운영자도 단계 인증이 필요하다.
    stale_admin = _session(s, 1, authenticated_ago=3600)
    assert c.request("POST", "/api/admin/account-backups/restore", session=stale_admin, json=payload).status_code == 401
    listing = c.request("GET", "/api/admin/account-backups", session=env.admin).json()["accounts"]
    assert [a["account"]["public_id"] for a in listing] == ["223456789012"]
    r = c.request("POST", "/api/admin/account-backups/restore", session=env.admin, json=payload)
    assert r.status_code == 200, r.text
    assert r.json()["restored_themes"][0]["counts"]["documents"] == 1
    assert _doc_count(s, env.theme_a) == 1
    assert _doc_count(s, env.theme_b) == 1  # 다른 계정은 그대로
    assert (Path(s.data_dir) / "checkpoints" / r.json()["checkpoint"]).is_file()
    page = c.request("GET", "/admin/backups", session=env.admin)
    assert page.status_code == 200 and "console_features.js" in page.text

    # 백업 삭제는 단계 인증을 요구한다.
    stale = _session(s, env.a, authenticated_ago=3600)
    assert c.request("DELETE", f"/api/me/backups?backup_id={backup_id}", session=stale).status_code == 401
    assert c.request("DELETE", f"/api/me/backups?backup_id={backup_id}", session=env.sa).status_code == 200
    assert c.request("GET", "/api/me/backups", session=env.sa).json()["backups"] == []


def test_restore_refuses_theme_now_owned_by_other_account(env):
    s = env.settings
    record = account_backup.create_account_backup(s, account_id=env.a)
    conn = dbm.connect_existing(s.db_file)
    try:
        conn.execute("UPDATE themes SET account_id=?, is_default=0 WHERE id=?", (env.b, env.theme_a))
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(account_backup.AccountBackupConflict):
        account_backup.restore_account_backup(s, account_id=env.a, backup_id=record["backup_id"], actor_account_id=1)
    assert _doc_count(s, env.theme_a) == 1  # 아무것도 바꾸지 않았다


def test_purge_removes_account_features(env, fake_telegram):
    c = env.client
    assert c.request("PUT", "/api/me/telegram", session=env.sa, json={"bot_token": BOT_TOKEN_A, "allowed_user_ids": "11"}).status_code == 200
    account_backup.create_account_backup(env.settings, account_id=env.a)
    from claire.api.accounts_routes.features import purge_account_features

    auth = auth_db.connect_auth_db(auth_db.auth_db_path(env.settings.data_dir))
    try:
        stats = purge_account_features(auth, env.a, data_dir=env.settings.data_dir)
        assert stats["telegram_bots"] == 1 and stats["backups"] == 1
        assert telegram_bots.get_meta(auth, account_id=env.a) is None
    finally:
        auth.close()


# --- 봇 러너 (모듈 사이 인터페이스) ---------------------------------------------------------------------


class _FakeUpdater:
    def __init__(self) -> None:
        self.running = False

    async def start_polling(self) -> None:
        self.running = True

    async def stop(self) -> None:
        self.running = False


class _FakeApp:
    def __init__(self, profile: Any, fail: bool) -> None:
        self.profile = profile
        self.fail = fail
        self.updater = _FakeUpdater()
        self.running = False
        self.post_init = None
        self.shutdown_called = False

    async def initialize(self) -> None:
        if self.fail:
            raise RuntimeError(f"cannot start {self.profile.token}")

    async def start(self) -> None:
        self.running = True

    async def stop(self) -> None:
        self.running = False

    async def shutdown(self) -> None:
        self.shutdown_called = True


def test_supervisor_follows_webui_configuration(env, fake_telegram):
    from claire.telegram_supervisor import BotSupervisor

    c = env.client
    apps: list[_FakeApp] = []
    fail_accounts: set[int] = set()

    def factory(_settings: Any, *, profile: Any) -> _FakeApp:
        app = _FakeApp(profile, profile.account_id in fail_accounts)
        apps.append(app)
        return app

    sup = BotSupervisor(env.settings, app_factory=factory)
    asyncio.run(sup.sync())
    assert set(sup.running) == {("instance", 1)}

    assert c.request("PUT", "/api/me/telegram", session=env.sa, json={"bot_token": BOT_TOKEN_A, "allowed_user_ids": "11"}).status_code == 200
    fail_accounts.add(env.b)
    assert c.request("PUT", "/api/me/telegram", session=env.sb, json={"bot_token": BOT_TOKEN_B, "allowed_user_ids": "12"}).status_code == 200
    asyncio.run(sup.sync())
    assert set(sup.running) == {("instance", 1), ("account", env.a)}
    assert c.request("GET", "/api/me/telegram", session=env.sa).json()["bot"]["status"] == "running"
    failed = c.request("GET", "/api/me/telegram", session=env.sb).json()["bot"]
    assert failed["status"] == "error" and BOT_TOKEN_B not in (failed["last_error"] or "")

    # 허용 사용자를 바꾸면 다시 시작하고, 끄면 멈춘다.
    first = sup.running[("account", env.a)].app
    assert c.request("PATCH", "/api/me/telegram", session=env.sa, json={"allowed_user_ids": [11, 13]}).status_code == 200
    asyncio.run(sup.sync())
    assert first.shutdown_called and sup.running[("account", env.a)].app is not first
    assert c.request("PATCH", "/api/me/telegram", session=env.sa, json={"enabled": False}).status_code == 200
    asyncio.run(sup.sync())
    assert set(sup.running) == {("instance", 1)}
    # 정지된 계정의 봇은 돌리지 않는다.
    assert c.request("PATCH", "/api/me/telegram", session=env.sa, json={"enabled": True}).status_code == 200
    conn = dbm.connect_existing(env.settings.db_file)
    try:
        conn.execute("UPDATE accounts SET status='suspended' WHERE id=?", (env.a,))
        conn.commit()
    finally:
        conn.close()
    asyncio.run(sup.sync())
    assert ("account", env.a) not in sup.running
    asyncio.run(sup.shutdown())
    assert sup.running == {}
