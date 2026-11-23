"""``/admin/server`` 통합 콘솔 셸 렌더링 및 서버 설정 라우트 테스트."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from claire.api import server
from claire.core.config import Settings
from claire.store import accounts as accounts_store
from claire.store import auth_db, db as dbm
from claire.store.secrets import reset_secret_service
from claire.store.token_store import get_token_store, reset_token_stores

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
        assert 'id="server-invites-policy"' in body
        assert 'id="admin-member-invites-toggle"' in body
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
        # The console sidebar contains /admin/* links; moderation links are separated into /moderation/* via #moderationlinkbtn.
        sidebar = body.split('<nav class="console-sidebar"', 1)[1].split("</nav>", 1)[0]
        for path in (
            "/admin/server",
            "/admin/accounts",
            "/admin/idp",
            "/admin/providers",
            "/admin/limits",
            "/admin/tokens",
            "/admin/audit",
            "/admin/mcp",
        ):
            assert f'href="{path}"' in sidebar
        for mod_path in ("/moderation/kb", "/moderation/oversight", "/moderation/themes"):
            assert f'href="{mod_path}"' not in sidebar
        assert 'id="moderationlinkbtn"' in body and 'href="/moderation"' in body
        assert 'href="/admin/kb"' not in body
        assert 'href="/admin/themes"' not in body


def test_admin_server_denies_non_admin_and_anonymous(settings: Settings) -> None:
    _create_admin_session(settings, mode="network")
    member_token = _create_member_session(settings, "bobmember")
    app = server.create_app(settings)
    with TestClient(app, base_url="http://127.0.0.1:8765") as tc:
        assert tc.get("/admin/server").status_code == 404
        assert tc.get("/api/admin/server").status_code == 404
        assert tc.get("/admin/server", headers={"Cookie": f"claire_session={member_token}"}).status_code == 404
        assert tc.get("/api/admin/server", headers={"Cookie": f"claire_session={member_token}"}).status_code == 404


def test_admin_server_api_exposes_member_invites_enabled_default_false(settings: Settings) -> None:
    token = _create_admin_session(settings, mode="network")
    app = server.create_app(settings)
    with TestClient(app, base_url="http://127.0.0.1:8765") as tc:
        resp = tc.get("/api/admin/server", headers={"Cookie": f"claire_session={token}"})
        assert resp.status_code == 200
        assert resp.json()["member_invites_enabled"] is False


def test_admin_server_invites_policy_guards_validation_and_audit(settings: Settings) -> None:
    token = _create_admin_session(settings, mode="network")
    member_token = _create_member_session(settings, "bobmember2")
    conn = dbm.connect_existing(Path(settings.db_file))
    try:
        stale_token = accounts_store.create_account_session(conn, account_id=1, scope="owner", now=time.time() - 3600)
    finally:
        conn.close()

    origin = "http://127.0.0.1:8765"
    app = server.create_app(settings)
    with TestClient(app, base_url=origin) as tc:
        # Anonymous and non-admin member -> 404
        assert tc.post("/api/admin/server/invites", json={"member_invites_enabled": True}, headers={"Origin": origin}).status_code == 404
        assert tc.post("/api/admin/server/invites", json={"member_invites_enabled": True}, headers={"Cookie": f"claire_session={member_token}", "Origin": origin}).status_code == 404

        # Cross-origin -> 403
        assert tc.post("/api/admin/server/invites", json={"member_invites_enabled": True}, headers={"Cookie": f"claire_session={token}", "Origin": "https://evil.example"}).status_code == 403

        # Stale session -> 401 reauth_required
        reauth_resp = tc.post("/api/admin/server/invites", json={"member_invites_enabled": True}, headers={"Cookie": f"claire_session={stale_token}", "Origin": origin})
        assert reauth_resp.status_code == 401
        assert reauth_resp.json()["error"] == "reauth_required"

        # Bearer owner token with same-origin Origin -> 403 step_up_requires_session
        tc.cookies.clear()
        bearer_owner = get_token_store(data_dir=Path(settings.data_dir)).create_token(name="admin-bearer", scope="owner", account_id=1)["token"]
        bearer_resp = tc.post("/api/admin/server/invites", json={"member_invites_enabled": True}, headers={"Authorization": f"Bearer {bearer_owner}", "Origin": origin})
        assert bearer_resp.status_code == 403
        assert bearer_resp.json()["error"] == "step_up_requires_session"

        # Invalid values & unknown keys -> 400
        for bad_body in ({"member_invites_enabled": "true"}, {"member_invites_enabled": 1}, {"member_invites_enabled": None}, {}, {"member_invites_enabled": True, "extra": 1}):
            bad_resp = tc.post("/api/admin/server/invites", json=bad_body, headers={"Cookie": f"claire_session={token}", "Origin": origin})
            assert bad_resp.status_code == 400

        # Valid enable -> 200, Cache-Control: no-store, persisted, reflected in GET, audited
        ok_enable = tc.post("/api/admin/server/invites", json={"member_invites_enabled": True}, headers={"Cookie": f"claire_session={token}", "Origin": origin})
        assert ok_enable.status_code == 200
        assert ok_enable.headers.get("cache-control") == "no-store"
        assert ok_enable.json() == {"member_invites_enabled": True}

        get_after = tc.get("/api/admin/server", headers={"Cookie": f"claire_session={token}"})
        assert get_after.status_code == 200
        assert get_after.json()["member_invites_enabled"] is True

        # Disable again -> 200
        ok_disable = tc.post("/api/admin/server/invites", json={"member_invites_enabled": False}, headers={"Cookie": f"claire_session={token}", "Origin": origin})
        assert ok_disable.status_code == 200
        assert ok_disable.json() == {"member_invites_enabled": False}

    conn = dbm.connect_existing(Path(settings.db_file))
    auth = auth_db.connect_auth_db(auth_db.auth_db_path(Path(settings.data_dir)))
    try:
        assert accounts_store.AccountStore(conn).member_invites_enabled() is False
        audits = [a for a in auth_db.list_admin_audit(auth) if a["action"] == "server.invites.update"]
        assert len(audits) == 2
        assert audits[0]["detail"]["member_invites_enabled"] is False
        assert audits[1]["detail"]["member_invites_enabled"] is True
    finally:
        auth.close()
        conn.close()


def test_admin_server_invites_policy_card_reflects_state_and_locale(settings: Settings) -> None:
    token = _create_admin_session(settings, mode="network")
    app = server.create_app(settings)
    with TestClient(app, base_url="http://127.0.0.1:8765") as tc:
        en_off = tc.get("/admin/server", headers={"Cookie": f"claire_session={token}", "Accept-Language": "en"})
        assert en_off.status_code == 200
        assert 'id="server-invites-policy"' in en_off.text
        assert 'id="admin-member-invites-toggle" type="checkbox" switch aria-label=' in en_off.text
        assert "Invitation issuance policy" in en_off.text

        ko_off = tc.get("/admin/server", headers={"Cookie": f"claire_session={token}", "Accept-Language": "ko"})
        assert ko_off.status_code == 200
        assert "초대 코드 발급 정책" in ko_off.text

        conn = dbm.connect_existing(Path(settings.db_file))
        try:
            accounts_store.AccountStore(conn).set_member_invites_enabled(True, updated_by=1)
        finally:
            conn.close()

        en_on = tc.get("/admin/server", headers={"Cookie": f"claire_session={token}", "Accept-Language": "en"})
        assert en_on.status_code == 200
        assert 'id="admin-member-invites-toggle" type="checkbox" switch checked' in en_on.text

