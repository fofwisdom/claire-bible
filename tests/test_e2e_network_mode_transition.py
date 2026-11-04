"""End-to-end test for instance network mode transition lifecycle.

Verifies readiness check evaluation, server tier configuration, gate enforcement,
and clean transition from single mode to network mode and back.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import time
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from claire.accounts import readiness
from claire.accounts.provider_policy import ServerTier
from claire.accounts import server_tier as st
from claire.api import server
from claire.store import accounts as accounts_store
from claire.store import auth_db
from claire.store import db as dbm
from claire.store.token_store import reset_token_stores

PUBLIC_URL = "http://127.0.0.1:8765"
SECRET_ADMIN_USERNAME = "claire_admin"
ADMIN_PASSWORD = "correct-horse-battery-admin"
ADMIN_DISPLAY_NAME = "Claire Admin"
ADMIN_PUBLIC_ID = 100000000000000001


@dataclass
class _Settings:
    db_file: Path
    data_dir: Path
    environment: str = "development"
    public_url: str = PUBLIC_URL
    inject_token: str = "owner-" + "o" * 32
    readonly_token: str = "readonly-" + "r" * 32
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

    conn = dbm.connect(db_file)
    try:
        dbm.init_db(conn)
        conn.execute(
            "UPDATE accounts SET username=?, display_name=?, status='active', public_id=? WHERE id=1",
            (SECRET_ADMIN_USERNAME, ADMIN_DISPLAY_NAME, ADMIN_PUBLIC_ID),
        )
        conn.commit()
    finally:
        conn.close()

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


def test_e2e_network_mode_transition_lifecycle(app_env):
    """E2E flow: verify readiness gates, configure server tier, transition single->network, verify /whoami, transition network->single."""
    admin_token = _login_admin(app_env)
    headers = _admin_headers(admin_token)

    # 1. Initially in single mode
    init_resp = app_env.client.get("/api/admin/instance", headers=headers)
    assert init_resp.status_code == 200
    init_data = init_resp.json()
    assert init_data["mode"] == "single"
    assert init_data["network_mode_available"] is True

    readiness_items = {item["id"]: item for item in init_data["readiness"]["items"]}
    assert readiness_items["G1"]["passed"] is True
    assert readiness_items["G2"]["passed"] is True
    assert readiness_items["G3"]["passed"] is True
    assert readiness_items["G4"]["passed"] is True
    assert readiness_items["G5"]["passed"] is False  # server tier unset
    assert readiness_items["G6"]["passed"] is True
    assert readiness_items["G7"]["passed"] is True
    assert readiness_items["G8"]["passed"] is True
    assert readiness_items["G9"]["passed"] is True
    assert init_data["readiness"]["passed"] is False

    # 2. Attempt transition to network mode -> rejected due to G5 failure
    fail_switch = app_env.client.post("/api/admin/instance/mode", json={"mode": "network"}, headers=headers)
    assert fail_switch.status_code == 400
    fail_body = fail_switch.json()
    assert fail_body["error"] == "readiness_failed"
    assert any(f["id"] == "G5" for f in fail_body["failures"])

    # 3. Configure server tier via admin API (G5 resolution)
    tier_resp = app_env.client.put("/api/admin/server-tier", json={"tier": {"provider": "none"}}, headers=headers)
    assert tier_resp.status_code == 200
    assert tier_resp.json()["ok"] is True

    # 4. Check readiness report again -> all G1-G9 passed
    ready_resp = app_env.client.get("/api/admin/instance", headers=headers)
    assert ready_resp.status_code == 200
    ready_data = ready_resp.json()
    assert ready_data["readiness"]["passed"] is True
    assert all(item["passed"] for item in ready_data["readiness"]["items"])

    # 5. Transition single -> network mode -> succeeds
    switch_resp = app_env.client.post("/api/admin/instance/mode", json={"mode": "network"}, headers=headers)
    assert switch_resp.status_code == 200
    switch_body = switch_resp.json()
    assert switch_body["mode"] == "network"
    assert switch_body["changed"] is True

    # 6. Verify whoami reflects network mode
    whoami_resp = app_env.client.get("/whoami", headers=headers)
    assert whoami_resp.status_code == 200
    whoami_data = whoami_resp.json()
    assert whoami_data["instance"]["mode"] == "network"
    assert whoami_data["relation"] == "owner"

    # 7. Transition back network -> single mode -> succeeds when no non-admin members exist
    back_resp = app_env.client.post("/api/admin/instance/mode", json={"mode": "single"}, headers=headers)
    assert back_resp.status_code == 200
    back_body = back_resp.json()
    assert back_body["mode"] == "single"
    assert back_body["changed"] is True

    # 8. Verify whoami reflects single mode again
    whoami_back = app_env.client.get("/whoami", headers=headers)
    assert whoami_back.status_code == 200
    assert whoami_back.json()["instance"]["mode"] == "single"


def test_e2e_network_mode_web_interface_lifecycle(app_env):
    """E2E flow: verify web interface HTML components, static JS delivery, readiness feedback, and transition lifecycle."""
    admin_token = _login_admin(app_env)
    headers = _admin_headers(admin_token)

    # 1. Verify static JavaScript delivery for instance_mode.js
    js_resp = app_env.client.get("/static/js/instance_mode.js")
    assert js_resp.status_code == 200
    assert "data-mode-switch-form" in js_resp.text
    assert "data-mode-switch-reauth" in js_resp.text
    assert "/api/admin/instance/mode" in js_resp.text

    # 2. Verify admin navigation links to /admin/instance
    acc_page = app_env.client.get("/admin/accounts", headers=headers)
    assert acc_page.status_code == 200
    assert 'href="/admin/instance"' in acc_page.text

    idp_page = app_env.client.get("/admin/idp", headers=headers)
    assert idp_page.status_code == 200
    assert 'href="/admin/instance"' in idp_page.text

    # 3. Initial page in Single Mode with G5 unconfigured
    init_page = app_env.client.get("/admin/instance", headers=headers)
    assert init_page.status_code == 200
    assert '<section class="card" aria-label="인스턴스 모드" id="instance-mode">' in init_page.text
    assert 'data-mode-switch-form' in init_page.text
    assert 'data-mode-switch-reauth' in init_page.text
    assert 'name="target_mode" value="network"' in init_page.text
    assert '<script src="/static/js/instance_mode.js" defer></script>' in init_page.text
    assert "준비 검사 미통과" in init_page.text
    form_slice = init_page.text.split("data-mode-switch-form")[1].split("</form>")[0]
    assert "disabled" in form_slice

    # 4. Configure server tier to pass G5
    tier_resp = app_env.client.put("/api/admin/server-tier", json={"tier": {"provider": "none"}}, headers=headers)
    assert tier_resp.status_code == 200

    # 5. Page in Single Mode is now ready for transition
    ready_page = app_env.client.get("/admin/instance", headers=headers)
    assert ready_page.status_code == 200
    assert "9/9 통과" in ready_page.text
    assert "준비 검사 완료: 네트워크 모드로 전환할 수 있습니다." in ready_page.text
    ready_form_slice = ready_page.text.split("data-mode-switch-form")[1].split("</form>")[0]
    assert "disabled" not in ready_form_slice
    assert "네트워크 모드로 전환</button>" in ready_form_slice

    # 6. Execute transition to Network Mode
    switch_resp = app_env.client.post("/api/admin/instance/mode", json={"mode": "network"}, headers=headers)
    assert switch_resp.status_code == 200
    assert switch_resp.json()["mode"] == "network"

    # 7. Page now renders Network Mode controls
    net_page = app_env.client.get("/admin/instance", headers=headers)
    assert net_page.status_code == 200
    assert "네트워크 모드 (Network Mode)" in net_page.text
    assert 'name="target_mode" value="single"' in net_page.text
    assert "btn-danger" in net_page.text
    assert "싱글 모드로 전환</button>" in net_page.text
    assert "일반 멤버가 1명이라도 존재하면" in net_page.text

    # 8. Add a non-admin member and verify rollback rejection
    conn = dbm.connect_existing(app_env.db_file)
    try:
        conn.execute(
            "INSERT INTO accounts (id, public_id, username, role, status, display_name, profile_public, created_at, updated_at) "
            "VALUES (2, 222222222222222222, 'charlie', 'member', 'active', 'Charlie', 1, ?, ?)",
            (time.time(), time.time()),
        )
        conn.commit()
    finally:
        conn.close()

    blocked_resp = app_env.client.post("/api/admin/instance/mode", json={"mode": "single"}, headers=headers)
    assert blocked_resp.status_code == 400
    assert blocked_resp.json()["error"] == "members_exist"

    # Remove the non-admin member
    conn = dbm.connect_existing(app_env.db_file)
    try:
        conn.execute("DELETE FROM accounts WHERE id = 2")
        conn.commit()
    finally:
        conn.close()

    # 9. Rollback to Single Mode succeeds
    back_resp = app_env.client.post("/api/admin/instance/mode", json={"mode": "single"}, headers=headers)
    assert back_resp.status_code == 200
    assert back_resp.json()["mode"] == "single"

    # 10. Page is back in Single Mode
    final_page = app_env.client.get("/admin/instance", headers=headers)
    assert final_page.status_code == 200
    assert "싱글 모드 (Single Mode)" in final_page.text
    assert 'name="target_mode" value="network"' in final_page.text

