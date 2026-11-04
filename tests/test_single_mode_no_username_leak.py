"""S9: 싱글 모드에서 관리자 USERNAME 비노출 검증 (설계 §4.2, §6.2, §8.5, §17.2, §18.6).

싱글 모드에서는 관리자 USERNAME이 익명·방문자 응답(헤더·본문) 어디에도 노출되지 않아야 한다(S9).
HTML 셸의 부트스트랩 메타태그 ``storage-key``는 계정의 public_id 기반(``a{public_id}``)이어야 한다(O9).
인스턴스 모드 설정·전환, 준비 검사 G1–G9, 레거시 호스트 일몰 시계 API, 로그인·설정 리다이렉트를 함께 검증한다.
모든 테스트는 tmp_path를 사용하고 CLAIRE_* 환경변수로부터 완전히 격리된다(§18.2-6).
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from starlette.requests import Request
from starlette.testclient import TestClient

from claire.accounts import readiness
from claire.accounts.provider_policy import ServerTier
from claire.api import security, server
from claire.api.host_router import LegacyHostPolicy
from claire.api.namespace import (
    ADMIN_USERNAME_PUBLISHED_KEY,
    NAMESPACE_EXTRA_STATE_KEY,
    NAMESPACE_STATE_KEY,
    ResolvedNamespace,
    account_home_path,
    shell_bootstrap,
)
from claire.store import accounts as accounts_store
from claire.store import auth_db
from claire.store import db as dbm
from claire.store import legacy_hosts
from claire.store.token_store import reset_token_stores

SECRET_ADMIN_USERNAME = "classified_adm_x99"
ADMIN_DISPLAY_NAME = "Super Admin"
ADMIN_PASSWORD = "correct-horse-battery-admin-1234"
ADMIN_PUBLIC_ID = 1164999888777666555
READONLY_TOKEN = "readonly-" + "r" * 32
OWNER_TOKEN = "owner-" + "o" * 32
PUBLIC_URL = "http://127.0.0.1:8765"


@dataclass
class _Settings:
    db_file: Path
    data_dir: Path
    environment: str = "development"
    public_url: str = PUBLIC_URL
    inject_token: str = OWNER_TOKEN
    readonly_token: str = READONLY_TOKEN
    cors_allowed_origins: str = ""
    anonymous_readonly: bool = True
    multi_theme: bool = True
    usercontent_fqdn: str = ""
    media_fqdn: str = ""


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for key in list(os.environ):
        if key.startswith("CLAIRE_"):
            monkeypatch.delenv(key, raising=False)
    reset_token_stores()
    yield
    reset_token_stores()


@pytest.fixture
def app_env(tmp_path: Path):
    db_file = tmp_path / "claire.db"
    settings = _Settings(db_file=db_file, data_dir=tmp_path)

    # Initialize SQLite database
    conn = dbm.connect(db_file)
    try:
        dbm.init_db(conn)
        # Update admin account with distinct secret username and specific public_id
        conn.execute(
            "UPDATE accounts SET username=?, display_name=?, status='active', public_id=? WHERE id=1",
            (SECRET_ADMIN_USERNAME, ADMIN_DISPLAY_NAME, ADMIN_PUBLIC_ID),
        )
        conn.commit()
    finally:
        conn.close()

    # Initialize auth.db with admin password
    auth_path = auth_db.auth_db_path(tmp_path)
    auth_conn = auth_db.connect_auth_db(auth_path)
    try:
        auth_db.set_password(auth_conn, 1, ADMIN_PASSWORD, username=SECRET_ADMIN_USERNAME)
    finally:
        auth_conn.close()

    service = SimpleNamespace(provider=SimpleNamespace(name="stub"))
    app = server.create_app(settings, service)
    client = TestClient(app, base_url=PUBLIC_URL)
    return SimpleNamespace(settings=settings, client=client, db_file=db_file, data_dir=tmp_path)


def _login_admin(app_env) -> str:
    conn = dbm.connect_existing(app_env.db_file)
    try:
        return accounts_store.create_account_session(conn, account_id=1, scope="owner", now=time.time())
    finally:
        conn.close()


def _admin_headers(token: str) -> dict[str, str]:
    return {"Cookie": f"claire_session={token}", "Origin": PUBLIC_URL}


# ==============================================================================
# S9: No Admin USERNAME Leaked in Single Mode Responses
# ==============================================================================


def test_whoami_anonymous_in_single_mode_does_not_leak_admin_username(app_env):
    """S9: /whoami anonymously returns visitor scope without exposing admin username."""
    resp = app_env.client.get("/whoami")
    assert resp.status_code == 200
    data = resp.json()
    assert data["scope"] == "anonymous"
    assert data["instance"]["mode"] == "single"
    assert data["relation"] == "visitor"
    assert SECRET_ADMIN_USERNAME not in resp.text
    assert all(SECRET_ADMIN_USERNAME not in v for v in resp.headers.values())


def test_whoami_readonly_in_single_mode_does_not_leak_admin_username(app_env):
    """S9: /whoami with readonly bearer does not expose admin username."""
    resp = app_env.client.get("/whoami", headers={"Authorization": f"Bearer {READONLY_TOKEN}"})
    assert resp.status_code == 200
    assert SECRET_ADMIN_USERNAME not in resp.text


def test_root_html_shell_in_single_mode_does_not_leak_admin_username(app_env):
    """S9 & O9: Root GET / returns HTML shell and does not leak admin username."""
    resp = app_env.client.get("/")
    assert resp.status_code == 200
    assert "claire-storage-key" in resp.text
    assert SECRET_ADMIN_USERNAME not in resp.text
    assert all(SECRET_ADMIN_USERNAME not in v for v in resp.headers.values())


def test_themes_collection_in_single_mode_does_not_leak_admin_username(app_env):
    """S9: GET /themes collection uses /themes/{n}/ and does not leak admin username."""
    resp = app_env.client.get("/themes", headers={"Accept": "application/json"})
    assert resp.status_code == 200
    assert SECRET_ADMIN_USERNAME not in resp.text
    assert all(SECRET_ADMIN_USERNAME not in v for v in resp.headers.values())


def test_public_system_routes_do_not_leak_admin_username(app_env):
    """S9: Public endpoints (/health, /robots.txt, /sitemap.xml) do not leak admin username."""
    for path in ("/health", "/robots.txt", "/sitemap.xml"):
        resp = app_env.client.get(path)
        assert resp.status_code == 200
        assert SECRET_ADMIN_USERNAME not in resp.text
        assert all(SECRET_ADMIN_USERNAME not in v for v in resp.headers.values())


def test_shell_bootstrap_helper_uses_storage_key_public_id(app_env):
    """O9: shell_bootstrap helper generates a{public_id} for storage-key, never username."""
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [],
        "state": {
            NAMESPACE_STATE_KEY: ResolvedNamespace(
                mode="single",
                account_id=1,
                username=None,
                pinned_theme_id=0,
                prefix="",
                via_prefix=False,
            ),
            NAMESPACE_EXTRA_STATE_KEY: {"public_id": ADMIN_PUBLIC_ID},
        },
    }
    req = Request(scope)
    bs = shell_bootstrap(req)
    assert bs["storage-key"] == f"a{ADMIN_PUBLIC_ID}"
    assert bs["api-base"] == "/"
    assert bs["root-base"] == "/"
    assert SECRET_ADMIN_USERNAME not in bs.values()


def test_single_mode_at_username_returns_404_without_publishing(app_env):
    """N1: In single mode before admin_username_published, /@{admin} returns 404."""
    resp = app_env.client.get(f"/@{SECRET_ADMIN_USERNAME}/", follow_redirects=False)
    assert resp.status_code == 404
    assert SECRET_ADMIN_USERNAME not in resp.text


def test_single_mode_at_username_redirects_308_when_published(app_env):
    """O15: In single mode after admin_username_published='1', /@{admin}/ 308 redirects to /."""
    conn = dbm.connect_existing(app_env.db_file)
    try:
        conn.execute(
            f"INSERT INTO instance_settings (key, value, updated_at) VALUES ('{ADMIN_USERNAME_PUBLISHED_KEY}', '1', ?)",
            (time.time(),),
        )
        conn.commit()
    finally:
        conn.close()

    resp = app_env.client.get(f"/@{SECRET_ADMIN_USERNAME}/", follow_redirects=False)
    assert resp.status_code == 308
    assert resp.headers["Location"].endswith("/")


# ==============================================================================
# Readiness Checks (G1–G9)
# ==============================================================================


def test_readiness_constant_network_mode_available():
    """O7: NETWORK_MODE_AVAILABLE must be False in P2."""
    assert readiness.NETWORK_MODE_AVAILABLE is False


def test_readiness_checks_g1_to_g9(app_env, monkeypatch):
    """G1–G9 inspection implementation (§17.2)."""
    conn = dbm.connect_existing(app_env.db_file)
    try:
        report = readiness.check_readiness(conn, app_env.settings)
        items = {item.id: item for item in report.items}

        assert set(items.keys()) == {f"G{i}" for i in range(1, 10)}
        assert report.passed is False

        # In P2 with NETWORK_MODE_AVAILABLE=False:
        # G1, G2, G3, G6 are CI gates
        assert items["G1"].passed is False
        assert items["G2"].passed is False
        assert items["G3"].passed is False
        assert items["G6"].passed is False

        # G4: In development, usercontent_fqdn is optional and passes
        assert items["G4"].passed is True

        # G5: server_tier not set yet -> False
        assert items["G5"].passed is False

        # G7: Admin username and password configured -> True
        assert items["G7"].passed is True

        # G8: /syndication public access released -> True
        assert items["G8"].passed is True

        # G9: Legacy cookie fallback not removed in P2 -> False
        assert items["G9"].passed is False
    finally:
        conn.close()


def test_readiness_g4_production_behavior(app_env):
    """G4 fails in production when usercontent_fqdn is missing, passes when set."""
    conn = dbm.connect_existing(app_env.db_file)
    try:
        prod_settings_no_uc = SimpleNamespace(
            environment="production",
            usercontent_fqdn="",
            data_dir=app_env.data_dir,
        )
        report_fail = readiness.check_readiness(conn, prod_settings_no_uc)
        item_g4_fail = next(i for i in report_fail.items if i.id == "G4")
        assert item_g4_fail.passed is False

        prod_settings_with_uc = SimpleNamespace(
            environment="production",
            usercontent_fqdn="usercontent.example.com",
            data_dir=app_env.data_dir,
        )
        report_pass = readiness.check_readiness(conn, prod_settings_with_uc)
        item_g4_pass = next(i for i in report_pass.items if i.id == "G4")
        assert item_g4_pass.passed is True
    finally:
        conn.close()


def test_readiness_g5_server_tier_setting(app_env):
    """G5 passes when server_tier is configured in instance_settings."""
    conn = dbm.connect_existing(app_env.db_file)
    try:
        conn.execute(
            "INSERT INTO instance_settings (key, value, updated_at) VALUES ('server_tier', 'none', ?)",
            (time.time(),),
        )
        conn.commit()
        report = readiness.check_readiness(conn, app_env.settings)
        item_g5 = next(i for i in report.items if i.id == "G5")
        # A bare 'none' string is not a valid ServerTier JSON object (P4 L4-c).
        assert item_g5.passed is False
        conn.execute(
            "UPDATE instance_settings SET value=? WHERE key='server_tier'",
            (json.dumps(ServerTier().to_dict()),),
        )
        conn.commit()
        report = readiness.check_readiness(conn, app_env.settings)
        item_g5 = next(i for i in report.items if i.id == "G5")
        assert item_g5.passed is True
    finally:
        conn.close()


def test_readiness_g7_fails_when_no_password(app_env):
    """G7 fails when admin has no password in auth.db."""
    empty_auth_dir = app_env.data_dir / "empty_auth"
    empty_auth_dir.mkdir(parents=True, exist_ok=True)
    custom_settings = SimpleNamespace(
        environment="development",
        data_dir=empty_auth_dir,
    )
    conn = dbm.connect_existing(app_env.db_file)
    try:
        report = readiness.check_readiness(conn, custom_settings)
        item_g7 = next(i for i in report.items if i.id == "G7")
        assert item_g7.passed is False
    finally:
        conn.close()


def test_readiness_g8_passes_when_syndication_requires_auth(app_env, monkeypatch):
    """G8 passes when /syndication route access is not public."""
    rule = security._namespaced("read")
    monkeypatch.setitem(security.ROUTE_POLICY, ("GET", "/syndication"), rule)
    conn = dbm.connect_existing(app_env.db_file)
    try:
        report = readiness.check_readiness(conn, app_env.settings)
        item_g8 = next(i for i in report.items if i.id == "G8")
        assert item_g8.passed is True
    finally:
        conn.close()


def test_readiness_g9_passes_when_fallback_removed(app_env, monkeypatch):
    """G9 passes when LEGACY_COOKIE_FALLBACK_REMOVED is True."""
    monkeypatch.setattr(security, "LEGACY_COOKIE_FALLBACK_REMOVED", True)
    conn = dbm.connect_existing(app_env.db_file)
    try:
        report = readiness.check_readiness(conn, app_env.settings)
        item_g9 = next(i for i in report.items if i.id == "G9")
        assert item_g9.passed is True
    finally:
        conn.close()


# ==============================================================================
# Instance Administration Routes: GET /admin/instance & /api/admin/instance
# ==============================================================================


def test_get_admin_instance_page_requires_admin(app_env):
    """GET /admin/instance returns 404 for anonymous and 200 for admin."""
    # Anonymous: 404
    anon_resp = app_env.client.get("/admin/instance")
    assert anon_resp.status_code == 404

    # Admin session: 200
    token = _login_admin(app_env)
    admin_resp = app_env.client.get("/admin/instance", headers=_admin_headers(token))
    assert admin_resp.status_code == 200
    assert "인스턴스 설정" in admin_resp.text
    assert "준비 검사 (G1–G9)" in admin_resp.text
    assert "레거시 호스트 일몰 시계" in admin_resp.text


def test_get_api_admin_instance_json(app_env):
    """GET /api/admin/instance returns current mode, readiness report, and policy."""
    token = _login_admin(app_env)
    resp = app_env.client.get("/api/admin/instance", headers=_admin_headers(token))
    assert resp.status_code == 200
    data = resp.json()
    assert data["mode"] == "single"
    assert data["network_mode_available"] is False
    assert "readiness" in data
    assert len(data["readiness"]["items"]) == 9
    assert data["legacy_host_policy"]["after_sunset_status"] == 410
    assert data["legacy_host_policy"]["sunset_days"] == 30


# ==============================================================================
# Instance Administration Routes: POST /api/admin/instance/mode
# ==============================================================================


def test_post_api_admin_instance_mode_single_to_network_rejected(app_env):
    """Switching single -> network is rejected with 400 because NETWORK_MODE_AVAILABLE=False."""
    token = _login_admin(app_env)
    resp = app_env.client.post(
        "/api/admin/instance/mode",
        json={"mode": "network"},
        headers=_admin_headers(token),
    )
    assert resp.status_code == 400
    data = resp.json()
    assert data["error"] == "network_mode_not_available"


def test_post_api_admin_instance_mode_requires_step_up_auth(app_env):
    """Mode switching requires step-up authentication within STEP_UP_WINDOW_SECONDS."""
    # Create an old session where authenticated_at is older than 15 minutes
    conn = dbm.connect_existing(app_env.db_file)
    try:
        old_token = accounts_store.create_account_session(
            conn, account_id=1, scope="owner", now=time.time() - 1000
        )
    finally:
        conn.close()

    resp = app_env.client.post(
        "/api/admin/instance/mode",
        json={"mode": "network"},
        headers=_admin_headers(old_token),
    )
    assert resp.status_code == 401
    assert resp.json()["error"] == "reauth_required"


def test_post_api_admin_instance_mode_network_to_single_when_members_exist(app_env):
    """Switching network -> single is rejected when non-admin members exist."""
    # Manually put instance into network mode and add an active member
    conn = dbm.connect_existing(app_env.db_file)
    try:
        conn.execute("INSERT OR REPLACE INTO instance_settings (key, value, updated_at) VALUES ('mode', 'network', ?)", (time.time(),))
        conn.execute(
            "INSERT INTO accounts (id, public_id, username, role, status, display_name, profile_public, created_at, updated_at) "
            "VALUES (2, 222222222222222222, 'member_bob', 'member', 'active', 'Bob', 1, ?, ?)",
            (time.time(), time.time()),
        )
        conn.commit()
    finally:
        conn.close()

    token = _login_admin(app_env)
    resp = app_env.client.post(
        "/api/admin/instance/mode",
        json={"mode": "single"},
        headers=_admin_headers(token),
    )
    assert resp.status_code == 400
    data = resp.json()
    assert data["error"] == "members_exist"
    assert len(data["members"]) == 1
    assert data["members"][0]["username"] == "member_bob"


def test_post_api_admin_instance_mode_network_to_single_success(app_env):
    """Switching network -> single succeeds without members and sets admin_username_published='1' (O15)."""
    # Put instance into network mode without non-admin members
    conn = dbm.connect_existing(app_env.db_file)
    try:
        conn.execute("INSERT OR REPLACE INTO instance_settings (key, value, updated_at) VALUES ('mode', 'network', ?)", (time.time(),))
        conn.commit()
    finally:
        conn.close()

    token = _login_admin(app_env)
    resp = app_env.client.post(
        "/api/admin/instance/mode",
        json={"mode": "single"},
        headers=_admin_headers(token),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["mode"] == "single"
    assert data["changed"] is True
    assert data["admin_username_published"] is True

    # Verify DB state: mode='single', admin_username_published='1'
    conn = dbm.connect_existing(app_env.db_file)
    try:
        store = accounts_store.AccountStore(conn)
        assert store.instance_mode() == "single"
        assert store.get_setting(ADMIN_USERNAME_PUBLISHED_KEY) == "1"
    finally:
        conn.close()


# ==============================================================================
# Sunset Clock API: POST /api/admin/instance/legacy-hosts/sunset
# ==============================================================================


def test_post_api_admin_instance_legacy_hosts_sunset_actions(app_env):
    """O6: activate, extend, keep_indefinitely, and update_policy operations."""
    token = _login_admin(app_env)

    # Add a legacy host to the DB
    conn = dbm.connect_existing(app_env.db_file)
    try:
        conn.execute(
            "INSERT INTO legacy_hosts (host, theme_id, created_at, activated_at, sunset_at) VALUES ('old.example.com', 0, ?, NULL, NULL)",
            (time.time(),),
        )
        conn.commit()
    finally:
        conn.close()

    # 1. Activate sunset clock
    resp_act = app_env.client.post(
        "/api/admin/instance/legacy-hosts/sunset",
        json={"action": "activate", "days": 45},
        headers=_admin_headers(token),
    )
    assert resp_act.status_code == 200
    assert resp_act.json()["action"] == "activate"
    assert resp_act.json()["changed"] == 1

    # 2. Extend sunset clock
    resp_ext = app_env.client.post(
        "/api/admin/instance/legacy-hosts/sunset",
        json={"action": "extend", "host": "old.example.com", "days": 15},
        headers=_admin_headers(token),
    )
    assert resp_ext.status_code == 200
    assert resp_ext.json()["action"] == "extend"
    assert resp_ext.json()["host"]["sunset_at"] is not None

    # 3. Keep indefinitely
    resp_keep = app_env.client.post(
        "/api/admin/instance/legacy-hosts/sunset",
        json={"action": "keep_indefinitely", "host": "old.example.com"},
        headers=_admin_headers(token),
    )
    assert resp_keep.status_code == 200
    assert resp_keep.json()["action"] == "keep_indefinitely"
    assert resp_keep.json()["host"]["sunset_at"] is None

    # 4. Update policy
    resp_pol = app_env.client.post(
        "/api/admin/instance/legacy-hosts/sunset",
        json={"action": "update_policy", "after_sunset_status": 421, "sunset_days": 60},
        headers=_admin_headers(token),
    )
    assert resp_pol.status_code == 200
    assert resp_pol.json()["policy"]["after_sunset_status"] == 421
    assert resp_pol.json()["policy"]["sunset_days"] == 60


def test_post_api_admin_instance_sunset_error_cases(app_env):
    """Sunset API returns 404 for unknown host and 400 for invalid inputs."""
    token = _login_admin(app_env)

    # Extend nonexistent host -> 404
    resp_404 = app_env.client.post(
        "/api/admin/instance/legacy-hosts/sunset",
        json={"action": "extend", "host": "nonexistent.example.com", "days": 10},
        headers=_admin_headers(token),
    )
    assert resp_404.status_code == 404

    # Invalid action -> 400
    resp_inv = app_env.client.post(
        "/api/admin/instance/legacy-hosts/sunset",
        json={"action": "unknown_action"},
        headers=_admin_headers(token),
    )
    assert resp_inv.status_code == 400


# ==============================================================================
# Login and Setup Redirects: single -> /, network -> /@{username}/
# ==============================================================================


def test_login_redirect_in_single_and_network_modes(app_env):
    """Login redirects to / in single mode and /@{username}/ in network mode."""
    headers = {"Origin": PUBLIC_URL}

    # 1. Single mode login -> redirect to /
    resp_single = app_env.client.post(
        "/login",
        data={"username": SECRET_ADMIN_USERNAME, "password": ADMIN_PASSWORD},
        headers=headers,
        follow_redirects=False,
    )
    assert resp_single.status_code == 303
    assert resp_single.headers["Location"] == "/"

    # Switch to network mode
    conn = dbm.connect_existing(app_env.db_file)
    try:
        conn.execute("INSERT OR REPLACE INTO instance_settings (key, value, updated_at) VALUES ('mode', 'network', ?)", (time.time(),))
        conn.commit()
    finally:
        conn.close()

    # 2. Network mode login -> redirect to /@{username}/
    resp_net = app_env.client.post(
        "/login",
        data={"username": SECRET_ADMIN_USERNAME, "password": ADMIN_PASSWORD},
        headers=headers,
        follow_redirects=False,
    )
    assert resp_net.status_code == 303
    assert resp_net.headers["Location"] == f"/@{SECRET_ADMIN_USERNAME}/"


def test_setup_redirect_in_single_mode(tmp_path: Path):
    """Setup in single mode redirects to /."""
    db_file = tmp_path / "claire.db"
    settings = _Settings(db_file=db_file, data_dir=tmp_path)

    # Initialize SQLite database with admin in setup_required (username=NULL)
    conn = dbm.connect(db_file)
    try:
        dbm.init_db(conn)
        setup_token = accounts_store.create_account_session(conn, account_id=1, scope="owner", now=time.time())
    finally:
        conn.close()

    auth_path = auth_db.auth_db_path(tmp_path)
    auth_conn = auth_db.connect_auth_db(auth_path)
    auth_conn.close()

    service = SimpleNamespace(provider=SimpleNamespace(name="stub"))
    app = server.create_app(settings, service)
    client = TestClient(app, base_url=PUBLIC_URL)

    headers = {"Origin": PUBLIC_URL, "Cookie": f"claire_session={setup_token}"}
    resp_setup = client.post(
        "/setup",
        data={
            "username": "kim_lee",
            "display_name": "Kim Lee",
            "password": "new-valid-passphrase-4321",
            "password_confirm": "new-valid-passphrase-4321",
            "ack": "1",
        },
        headers=headers,
        follow_redirects=False,
    )
    assert resp_setup.status_code == 303
    assert resp_setup.headers["Location"] == "/"


def test_setup_redirect_in_network_mode(tmp_path: Path):
    """Setup in network mode redirects to /@{username}/."""
    db_file = tmp_path / "claire.db"
    settings = _Settings(db_file=db_file, data_dir=tmp_path)

    # Initialize SQLite database with admin in setup_required and mode=network
    conn = dbm.connect(db_file)
    try:
        dbm.init_db(conn)
        conn.execute("INSERT OR REPLACE INTO instance_settings (key, value, updated_at) VALUES ('mode', 'network', ?)", (time.time(),))
        conn.commit()
        setup_token = accounts_store.create_account_session(conn, account_id=1, scope="owner", now=time.time())
    finally:
        conn.close()

    auth_path = auth_db.auth_db_path(tmp_path)
    auth_conn = auth_db.connect_auth_db(auth_path)
    auth_conn.close()

    service = SimpleNamespace(provider=SimpleNamespace(name="stub"))
    app = server.create_app(settings, service)
    client = TestClient(app, base_url=PUBLIC_URL)

    headers = {"Origin": PUBLIC_URL, "Cookie": f"claire_session={setup_token}"}
    resp_setup = client.post(
        "/setup",
        data={
            "username": "kim_lee",
            "display_name": "Kim Lee",
            "password": "new-valid-passphrase-4321",
            "password_confirm": "new-valid-passphrase-4321",
            "ack": "1",
        },
        headers=headers,
        follow_redirects=False,
    )
    assert resp_setup.status_code == 303
    assert resp_setup.headers["Location"] == "/@kim_lee/"


def test_account_home_path_helper():
    """Verify account_home_path behavior for single and network modes."""
    assert account_home_path("single", None) == "/"
    assert account_home_path("single", "any_user") == "/"
    assert account_home_path("network", "alice") == "/@alice/"
    with pytest.raises(ValueError):
        account_home_path("network", None)
    with pytest.raises(ValueError):
        account_home_path("other", "bob")  # type: ignore[arg-type]
