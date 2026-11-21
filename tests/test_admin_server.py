"""``/admin/server`` 통합 콘솔 셸 렌더링 및 서버 설정 라우트 테스트."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from claire.api import server
from claire.config import Settings
from claire.store import accounts as accounts_store
from claire.store import auth_db, db as dbm
from claire.store.secrets import reset_secret_service
from claire.store.token_store import reset_token_stores

ADMIN_USERNAME = "instadmin"
ADMIN_PASSWORD = "correct-horse-battery-staple"


@pytest.fixture(autouse=True)
def _clean_claire_env(monkeypatch: pytest.MonkeyPatch):
    reset_secret_service()
    reset_token_stores()
    for key in list(os.environ):
        if key.startswith("CLAIRE_"):
            monkeypatch.delenv(key, raising=False)
    yield
    reset_secret_service()
    reset_token_stores()


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)
    db_file = data_dir / "claire.db"
    return Settings(
        CLAIRE_DB_PATH=str(db_file),
        CLAIRE_DATA_DIR=str(data_dir),
        CLAIRE_PROVIDER="mock",
        CLAIRE_ENVIRONMENT="development",
        CLAIRE_PUBLIC_URL="http://127.0.0.1:8765",
        CLAIRE_ANONYMOUS_READONLY=False,
        CLAIRE_MULTI_THEME=True,
    )


def _create_admin_session(s: Settings, *, mode: str = "single") -> str:
    app = server.create_app(s)
    assert app is not None
    conn = dbm.connect_existing(Path(s.db_file))
    auth = auth_db.connect_auth_db(auth_db.auth_db_path(Path(s.data_dir)))
    try:
        store = accounts_store.AccountStore(conn)
        admin = store.get(1)
        if admin.status == "setup_required":
            store.complete_setup(1, username=ADMIN_USERNAME, display_name="관리자")
        auth_db.set_password(auth, 1, ADMIN_PASSWORD, username=ADMIN_USERNAME)
        if mode == "network":
            store.set_setting("mode", "network", updated_by=1)
        return accounts_store.create_account_session(conn, account_id=1, scope="owner", now=time.time())
    finally:
        auth.close()
        conn.close()


def _create_member_session(s: Settings, username: str = "member1") -> str:
    conn = dbm.connect_existing(Path(s.db_file))
    try:
        now = time.time()
        cur = conn.execute(
            "INSERT INTO accounts (public_id, username, role, status, display_name, created_at, activated_at, updated_at) VALUES (?, ?, 'member', 'active', ?, ?, ?, ?)",
            ((1 << 57) + 777, username, username, now, now, now),
        )
        conn.commit()
        member_id = int(cur.lastrowid)
        return accounts_store.create_account_session(conn, account_id=member_id, scope="owner", now=now)
    finally:
        conn.close()


def test_admin_server_renders_inside_console_shell(settings: Settings) -> None:
    token = _create_admin_session(settings, mode="single")
    app = server.create_app(settings)
    with TestClient(app, base_url="http://127.0.0.1:8765") as tc:
        resp = tc.get("/admin/server", headers={"Cookie": f"claire_session={token}"})
        assert resp.status_code == 200
        assert resp.headers.get("referrer-policy") == "same-origin"
        body = resp.text
        assert 'data-active-area="admin"' in body
        assert 'data-active-section="server"' in body
        assert 'action="/logout"' in body
        assert '<section class="card" aria-label="Server mode" id="server-mode">' in body
        assert "Readiness checks for network mode (G1–G9)" in body
        assert "Sunset clock of legacy hosts" in body
        assert 'id="server-tier"' in body
        assert "data-server-tier-form" in body
        assert '<script src="/static/js/server_mode.js" defer></script>' in body
        assert '<script src="/static/js/server_tier.js" defer></script>' in body
        assert '<script src="/static/js/console_admin.js" defer></script>' in body
        # 싱글 모드에서는 상단 복귀 링크에 /@username 네임스페이스가 노출되지 않는다 (S9).
        assert f"/@{ADMIN_USERNAME}" not in body
        assert '<a class="brand" href="/" id="console-back-graph"' in body
        assert '<a id="syndlink" class="bar-icon-btn" href="/syndication"' in body
        assert '<button id="palettetrigger"' in body
        assert 'id="hamburger-btn"' in body and 'action="/logout"' in body
        assert 'id="authstate"' not in body
        # 통합 사이드바에 /admin/* 8개 경로가 모두 포함된다.
        for path in (
            "/admin/server",
            "/admin/accounts",
            "/admin/idp",
            "/admin/providers",
            "/admin/limits",
            "/admin/themes",
            "/admin/tokens",
            "/admin/mcp",
        ):
            assert f'href="{path}"' in body


def test_admin_server_denies_non_admin_and_anonymous(settings: Settings) -> None:
    _create_admin_session(settings, mode="network")
    member_token = _create_member_session(settings, "bobmember")
    app = server.create_app(settings)
    with TestClient(app, base_url="http://127.0.0.1:8765") as tc:
        assert tc.get("/admin/server").status_code == 404
        assert tc.get("/api/admin/server").status_code == 404
        assert tc.get("/admin/server", headers={"Cookie": f"claire_session={member_token}"}).status_code == 404
        assert tc.get("/api/admin/server", headers={"Cookie": f"claire_session={member_token}"}).status_code == 404


def test_admin_server_client_delivery_panel_is_localized(settings: Settings) -> None:
    token = _create_admin_session(settings, mode="single")
    app = server.create_app(settings)
    with TestClient(app, base_url="http://127.0.0.1:8765") as tc:
        en_resp = tc.get("/admin/server", headers={"Cookie": f"claire_session={token}", "Accept-Language": "en"})
        assert en_resp.status_code == 200
        assert "Agent Client Bundle Delivery" in en_resp.text
        assert "Save delivery mode" in en_resp.text

        ko_resp = tc.get("/admin/server", headers={"Cookie": f"claire_session={token}", "Accept-Language": "ko"})
        assert ko_resp.status_code == 200
        assert "에이전트 클라이언트 번들 배포" in ko_resp.text
        assert "배포 모드 저장" in ko_resp.text
