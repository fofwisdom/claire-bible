"""Comprehensive test suite for OIDC and GitHub IdP authentication flows (design §12, §13.3).

Tests:
1. Login with linked identity succeeds.
2. Login with unlinked identity fails with "invite required" / 403 without creating account.
3. Account linking via POST /api/me/identities/oidc/{id}/start with step-up auth -> identity is linked and can log in.
4. Unlink identity -> verify safety check prevents removing last credential.
5. Invite signup: /auth/oidc/{id}/start?invite={code} -> callback validates invite without decrementing uses_count -> /invite/complete final submission atomically decrements uses_count, creates account, links identity, and logs in.
6. Simultaneous invite completion: ensure atomic protection works.
7. Admin IdP management: GET/PUT /api/admin/idp and HTML page.
8. End-to-end (E2E) lifecycle test: invite signup, login, secondary IdP link with step-up, logout, and login via secondary IdP.
"""

from __future__ import annotations

import concurrent.futures
import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from starlette.testclient import TestClient

from claire.api import security, server
from claire.api.oidc import ExternalIdentity, GitHubAdapter, IdpProviderMeta, OidcClient
from claire.store import accounts as accounts_store
from claire.store import auth_db
from claire.store import db as dbm
from claire.store.token_store import reset_token_stores

PUBLIC_URL = "http://127.0.0.1:8765"
ADMIN_PASSWORD = "AdminPassword123!"
MEMBER_PASSWORD = "MemberPassword123!"


@dataclass
class _Settings:
    db_file: Path
    data_dir: Path
    environment: str = "development"
    public_url: str = PUBLIC_URL
    inject_token: str = "owner-" + "o" * 32
    readonly_token: str = "readonly-" + "r" * 32
    cors_allowed_origins: str = ""
    anonymous_readonly: bool = False


@pytest.fixture(autouse=True)
def reset_tokens():
    reset_token_stores()
    yield
    reset_token_stores()


@pytest.fixture
def test_env(tmp_path: Path):
    settings = _Settings(db_file=tmp_path / "claire.db", data_dir=tmp_path)

    # Initialize claire.db
    conn = dbm.connect(settings.db_file)
    try:
        dbm.init_db(conn)
        store = accounts_store.AccountStore(conn)
        store.complete_setup(1, username="admin_master", display_name="Admin Master")
        store.set_setting("mode", "network", updated_by=1)
    finally:
        conn.close()

    # Initialize auth.db
    auth_conn = auth_db.connect_auth_db(auth_db.auth_db_path(settings.data_dir))
    try:
        auth_db.set_password(auth_conn, 1, ADMIN_PASSWORD, username="admin_master")
    finally:
        auth_conn.close()

    # Save IdP providers
    auth_db.save_idp_providers(
        settings.data_dir,
        [
            IdpProviderMeta(
                id="google",
                type="oidc",
                display_name="Google Workspace",
                client_id="mock-google-client-id",
                enabled=True,
                allow_signup=True,
                issuer="https://accounts.google.com",
                scopes=("openid", "email", "profile"),
                client_secret="mock-google-secret",
            ),
            IdpProviderMeta(
                id="github",
                type="github",
                display_name="GitHub Org",
                client_id="mock-github-client-id",
                enabled=True,
                allow_signup=True,
                client_secret="mock-github-secret",
            ),
        ],
    )

    app = server.create_app(settings, SimpleNamespace(provider=SimpleNamespace(name="stub")))
    client = TestClient(app, base_url=PUBLIC_URL, cookies={})
    return SimpleNamespace(settings=settings, app=app, client=client)


def _login_admin(env) -> str:
    resp = env.client.post(
        "/login",
        data={"username": "admin_master", "password": ADMIN_PASSWORD},
        headers={"Origin": PUBLIC_URL, "Sec-Fetch-Site": "same-origin"},
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303)
    return env.client.cookies.get("claire_session", "")


# 1. Login with linked identity succeeds -----------------------------------------


def test_login_with_linked_identity_succeeds(test_env):
    auth_conn = auth_db.connect_auth_db(auth_db.auth_db_path(test_env.settings.data_dir))
    try:
        auth_db.link_account_identity(
            auth_conn,
            iss="https://accounts.google.com",
            sub="google_sub_101",
            account_id=1,
            provider_id="google",
        )
    finally:
        auth_conn.close()

    with patch.object(OidcClient, "create_authorization_url", new_callable=AsyncMock) as mock_auth_url, \
         patch.object(OidcClient, "exchange_code", new_callable=AsyncMock) as mock_exchange, \
         patch.object(OidcClient, "verify_id_token", new_callable=AsyncMock) as mock_verify:

        mock_auth_url.return_value = "https://accounts.google.com/o/oauth2/v2/auth?state=mock_state"
        mock_exchange.return_value = {"id_token": "mock.jwt.token"}
        mock_verify.return_value = ExternalIdentity(
            iss="https://accounts.google.com",
            sub="google_sub_101",
            email="admin@example.com",
            email_verified=True,
            display_name="Admin Master",
        )

        # 1. Start login
        resp_start = test_env.client.get("/auth/oidc/google/start", follow_redirects=False)
        assert resp_start.status_code == 302
        assert resp_start.headers["location"] == "https://accounts.google.com/o/oauth2/v2/auth?state=mock_state"
        state_cookie = test_env.client.cookies.get("claire_oidc_state")
        assert state_cookie is not None

        # Extract state parameter from auth_db
        auth_conn = auth_db.connect_auth_db(auth_db.auth_db_path(test_env.settings.data_dir))
        row = auth_conn.execute("SELECT state FROM oidc_states WHERE key = ?", (state_cookie,)).fetchone()
        auth_conn.close()
        saved_state = row["state"]

        # 2. Callback
        resp_cb = test_env.client.get(
            f"/auth/oidc/google/callback?state={saved_state}&code=mock_code",
            follow_redirects=False,
        )
        assert resp_cb.status_code == 302
        assert resp_cb.headers["location"] == "/@admin_master/"
        session_cookie = test_env.client.cookies.get("claire_session")
        assert session_cookie is not None

        # 3. Verify logged-in principal
        resp_me = test_env.client.get("/api/me")
        assert resp_me.status_code == 200
        assert resp_me.json()["username"] == "admin_master"


# 2. Login with unlinked identity fails with invite required ---------------------


def test_login_with_unlinked_identity_fails_with_invite_required(test_env):
    with patch.object(OidcClient, "create_authorization_url", new_callable=AsyncMock) as mock_auth_url, \
         patch.object(OidcClient, "exchange_code", new_callable=AsyncMock) as mock_exchange, \
         patch.object(OidcClient, "verify_id_token", new_callable=AsyncMock) as mock_verify:

        mock_auth_url.return_value = "https://accounts.google.com/o/oauth2/v2/auth?state=mock_state"
        mock_exchange.return_value = {"id_token": "mock.jwt.token"}
        mock_verify.return_value = ExternalIdentity(
            iss="https://accounts.google.com",
            sub="unlinked_sub_999",
            email="stranger@example.com",
            email_verified=True,
            display_name="Stranger",
        )

        resp_start = test_env.client.get("/auth/oidc/google/start", follow_redirects=False)
        assert resp_start.status_code == 302
        state_cookie = test_env.client.cookies.get("claire_oidc_state")

        auth_conn = auth_db.connect_auth_db(auth_db.auth_db_path(test_env.settings.data_dir))
        row = auth_conn.execute("SELECT state FROM oidc_states WHERE key = ?", (state_cookie,)).fetchone()
        auth_conn.close()
        saved_state = row["state"]

        resp_cb = test_env.client.get(
            f"/auth/oidc/google/callback?state={saved_state}&code=mock_code",
            follow_redirects=False,
        )
        assert resp_cb.status_code == 403
        assert "초대가 필요합니다" in resp_cb.text or "invite required" in resp_cb.text

        # Verify no account created
        conn = dbm.connect_existing(test_env.settings.db_file)
        store = accounts_store.AccountStore(conn)
        accounts = store.list_accounts()
        conn.close()
        assert len(accounts) == 1  # Only admin_master


# 3. Account linking with step-up auth -------------------------------------------


def test_account_linking_with_step_up_auth(test_env):
    _login_admin(test_env)

    # 1. Step-up auth required: reauth first
    resp_reauth = test_env.client.post(
        "/api/auth/reauth",
        json={"password": ADMIN_PASSWORD},
        headers={"Origin": PUBLIC_URL, "Sec-Fetch-Site": "same-origin"},
    )
    assert resp_reauth.status_code == 200

    with patch.object(OidcClient, "create_authorization_url", new_callable=AsyncMock) as mock_auth_url, \
         patch.object(OidcClient, "exchange_code", new_callable=AsyncMock) as mock_exchange, \
         patch.object(OidcClient, "verify_id_token", new_callable=AsyncMock) as mock_verify:

        mock_auth_url.return_value = "https://accounts.google.com/o/oauth2/v2/auth?state=mock_state"
        mock_exchange.return_value = {"id_token": "mock.jwt.token"}
        mock_verify.return_value = ExternalIdentity(
            iss="https://accounts.google.com",
            sub="google_sub_linked_202",
            email="admin@google.test",
            email_verified=True,
            display_name="Admin Linked",
        )

        resp_start = test_env.client.post(
            "/api/me/identities/oidc/google/start",
            headers={"Origin": PUBLIC_URL, "Sec-Fetch-Site": "same-origin"},
        )
        assert resp_start.status_code == 200
        data = resp_start.json()
        assert "authorization_url" in data
        state_cookie = test_env.client.cookies.get("claire_oidc_state")
        assert state_cookie is not None

        auth_conn = auth_db.connect_auth_db(auth_db.auth_db_path(test_env.settings.data_dir))
        row = auth_conn.execute("SELECT state, purpose, account_id FROM oidc_states WHERE key = ?", (state_cookie,)).fetchone()
        auth_conn.close()
        assert row["purpose"] == "link"
        assert row["account_id"] == 1
        saved_state = row["state"]

        # Callback executes linking
        resp_cb = test_env.client.get(
            f"/auth/oidc/google/callback?state={saved_state}&code=mock_code",
            follow_redirects=False,
        )
        assert resp_cb.status_code == 302
        assert "/settings/account?notice=identity_linked" in resp_cb.headers["location"]

        # Check listed identities
        resp_identities = test_env.client.get("/api/me/identities")
        assert resp_identities.status_code == 200
        identities = resp_identities.json()
        assert len(identities) == 1
        assert identities[0]["iss"] == "https://accounts.google.com"
        assert identities[0]["sub"] == "google_sub_linked_202"

        # Logout
        test_env.client.post("/logout", headers={"Origin": PUBLIC_URL, "Sec-Fetch-Site": "same-origin"})

        # Login via newly linked Google IdP
        resp_login_start = test_env.client.get("/auth/oidc/google/start", follow_redirects=False)
        new_state_cookie = test_env.client.cookies.get("claire_oidc_state")
        auth_conn = auth_db.connect_auth_db(auth_db.auth_db_path(test_env.settings.data_dir))
        row2 = auth_conn.execute("SELECT state FROM oidc_states WHERE key = ?", (new_state_cookie,)).fetchone()
        auth_conn.close()

        resp_login_cb = test_env.client.get(
            f"/auth/oidc/google/callback?state={row2['state']}&code=mock_code",
            follow_redirects=False,
        )
        assert resp_login_cb.status_code == 302
        assert resp_login_cb.headers["location"] == "/@admin_master/"


# 4. Unlink identity and last credential safety ----------------------------------


def test_unlink_identity_and_last_credential_safety(test_env):
    _login_admin(test_env)
    test_env.client.post("/api/auth/reauth", json={"password": ADMIN_PASSWORD}, headers={"Origin": PUBLIC_URL, "Sec-Fetch-Site": "same-origin"})

    # Link an identity
    auth_conn = auth_db.connect_auth_db(auth_db.auth_db_path(test_env.settings.data_dir))
    try:
        auth_db.link_account_identity(
            auth_conn,
            iss="https://accounts.google.com",
            sub="sub_to_unlink",
            account_id=1,
            provider_id="google",
        )
    finally:
        auth_conn.close()

    # Admin has password, so unlinking should succeed
    resp_del = test_env.client.delete(
        "/api/me/identities/oidc/google",
        headers={"Origin": PUBLIC_URL, "Sec-Fetch-Site": "same-origin"},
    )
    assert resp_del.status_code == 200
    assert resp_del.json()["ok"] is True

    # Now create a member account with NO password and ONE linked identity
    conn = dbm.connect_existing(test_env.settings.db_file)
    try:
        cur = conn.execute(
            "INSERT INTO accounts (public_id, username, role, status, display_name, profile_public, created_at, activated_at, updated_at) "
            "VALUES (123456789, 'nopw_user', 'member', 'active', 'No PW', 0, 1000, 1000, 1000)"
        )
        member_id = int(cur.lastrowid)
        session_token = accounts_store.create_account_session(conn, account_id=member_id, scope="owner")
    finally:
        conn.close()

    auth_conn = auth_db.connect_auth_db(auth_db.auth_db_path(test_env.settings.data_dir))
    try:
        auth_db.link_account_identity(
            auth_conn,
            iss="https://accounts.google.com",
            sub="only_credential_sub",
            account_id=member_id,
            provider_id="google",
        )
    finally:
        auth_conn.close()

    # Member client with session cookie
    member_client = TestClient(test_env.app, base_url=PUBLIC_URL, cookies={"claire_session": session_token})

    # Member tries to unlink the only authentication credential
    resp_del_only = member_client.delete(
        "/api/me/identities/oidc/google",
        headers={"Origin": PUBLIC_URL, "Sec-Fetch-Site": "same-origin"},
    )
    assert resp_del_only.status_code == 400
    assert resp_del_only.json()["error"] == "cannot_unlink_last_credential"


# 5. Invite signup flow ----------------------------------------------------------


def test_invite_signup_flow(test_env):
    auth_conn = auth_db.connect_auth_db(auth_db.auth_db_path(test_env.settings.data_dir))
    try:
        inv = auth_db.create_invitation(auth_conn, code="TESTCODE", max_uses=1, expires_in=3600, created_by=1)
    finally:
        auth_conn.close()

    with patch.object(OidcClient, "create_authorization_url", new_callable=AsyncMock) as mock_auth_url, \
         patch.object(OidcClient, "exchange_code", new_callable=AsyncMock) as mock_exchange, \
         patch.object(OidcClient, "verify_id_token", new_callable=AsyncMock) as mock_verify:

        mock_auth_url.return_value = "https://accounts.google.com/o/oauth2/v2/auth?state=mock_state"
        mock_exchange.return_value = {"id_token": "mock.jwt.token"}
        mock_verify.return_value = ExternalIdentity(
            iss="https://accounts.google.com",
            sub="new_google_sub_303",
            email="newbie@example.com",
            email_verified=True,
            display_name="Newbie User",
        )

        # 1. Start signup with invite code
        resp_start = test_env.client.get("/auth/oidc/google/start?invite=TESTCODE", follow_redirects=False)
        assert resp_start.status_code == 302
        state_cookie = test_env.client.cookies.get("claire_oidc_state")
        assert state_cookie is not None

        # Verify invitation uses_count is still 0
        auth_conn = auth_db.connect_auth_db(auth_db.auth_db_path(test_env.settings.data_dir))
        check_inv = auth_db.get_invitation_by_id(auth_conn, inv.id)
        assert check_inv.uses_count == 0

        row = auth_conn.execute("SELECT state, purpose, invitation_id FROM oidc_states WHERE key = ?", (state_cookie,)).fetchone()
        assert row["purpose"] == "signup"
        assert row["invitation_id"] == inv.id
        saved_state = row["state"]
        auth_conn.close()

        # 2. Callback
        resp_cb = test_env.client.get(
            f"/auth/oidc/google/callback?state={saved_state}&code=mock_code",
            follow_redirects=False,
        )
        assert resp_cb.status_code == 302
        loc = resp_cb.headers["location"]
        assert loc.startswith("/invite/complete?token=")
        signup_token = loc.split("token=")[1]

        # Verify uses_count is STILL 0 (Rev. 2.3 safety)
        auth_conn = auth_db.connect_auth_db(auth_db.auth_db_path(test_env.settings.data_dir))
        check_inv = auth_db.get_invitation_by_id(auth_conn, inv.id)
        assert check_inv.uses_count == 0
        auth_conn.close()

        # 3. GET /invite/complete renders form
        resp_form = test_env.client.get(f"/invite/complete?token={signup_token}")
        assert resp_form.status_code == 200
        assert "Newbie User" in resp_form.text

        # 4. POST /invite/complete submits username
        resp_submit = test_env.client.post(
            "/invite/complete",
            json={"token": signup_token, "username": "memberone", "display_name": "Newbie User"},
            headers={"Origin": PUBLIC_URL, "Sec-Fetch-Site": "same-origin"},
            follow_redirects=False,
        )
        assert resp_submit.status_code == 200 or resp_submit.status_code == 302
        session_cookie = test_env.client.cookies.get("claire_session")
        assert session_cookie is not None

        # Verify invitation uses_count is now 1 (consumed)
        auth_conn = auth_db.connect_auth_db(auth_db.auth_db_path(test_env.settings.data_dir))
        check_inv = auth_db.get_invitation_by_id(auth_conn, inv.id)
        assert check_inv.uses_count == 1

        # Verify identity linked
        ident = auth_db.find_account_identity(auth_conn, iss="https://accounts.google.com", sub="new_google_sub_303")
        assert ident is not None
        assert ident.provider_id == "google"
        auth_conn.close()

        # Verify user logged in
        resp_me = test_env.client.get("/api/me")
        assert resp_me.status_code == 200
        assert resp_me.json()["username"] == "memberone"


# 6. Simultaneous invite completion atomic protection ----------------------------


def test_simultaneous_invite_completion_atomic_protection(test_env):
    auth_conn = auth_db.connect_auth_db(auth_db.auth_db_path(test_env.settings.data_dir))
    try:
        inv = auth_db.create_invitation(auth_conn, code="7KQ2M9XA", max_uses=1, expires_in=3600, created_by=1)
    finally:
        auth_conn.close()

    # Generate two valid tokens for the same invitation
    from claire.api.accounts_routes.idp import create_signup_token
    from claire.api.accounts_routes.common import build_context
    from claire.api.security import WebRuntimeConfig

    config = WebRuntimeConfig.from_settings(test_env.settings)
    ctx = build_context(test_env.settings, config)

    token_a = create_signup_token(
        ctx,
        invitation_id=inv.id,
        provider_id="google",
        iss="https://accounts.google.com",
        sub="concurrent_sub_a",
        email="a@example.com",
        display_name="User A",
    )
    token_b = create_signup_token(
        ctx,
        invitation_id=inv.id,
        provider_id="google",
        iss="https://accounts.google.com",
        sub="concurrent_sub_b",
        email="b@example.com",
        display_name="User B",
    )

    def _submit(token: str, username: str):
        c = TestClient(test_env.app, base_url=PUBLIC_URL)
        return c.post(
            "/invite/complete",
            json={"token": token, "username": username, "display_name": username},
            headers={"Origin": PUBLIC_URL, "Sec-Fetch-Site": "same-origin"},
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        f_a = executor.submit(_submit, token_a, "concur_alpha")
        f_b = executor.submit(_submit, token_b, "concur_beta")
        resp_a = f_a.result()
        resp_b = f_b.result()

    statuses = [resp_a.status_code, resp_b.status_code]
    # Exactly one must succeed (200 or 302), other must fail with 410 (exhausted)
    assert 200 in statuses or 302 in statuses
    assert 410 in statuses

    # Verify invitation uses_count is exactly 1
    auth_conn = auth_db.connect_auth_db(auth_db.auth_db_path(test_env.settings.data_dir))
    check_inv = auth_db.get_invitation_by_id(auth_conn, inv.id)
    assert check_inv.uses_count == 1
    auth_conn.close()


# 7. Admin IdP management --------------------------------------------------------


def test_admin_idp_management(test_env):
    # Non-admin rejected
    resp_anon = test_env.client.get("/api/admin/idp")
    assert resp_anon.status_code in (403, 404)

    _login_admin(test_env)

    # Admin GET HTML
    resp_html = test_env.client.get("/admin/idp")
    assert resp_html.status_code == 200
    assert "외부 IdP 관리" in resp_html.text
    assert "Google Workspace" in resp_html.text
    assert 'data-active-area="admin"' in resp_html.text
    assert 'data-active-section="idp"' in resp_html.text
    assert 'id="pref-idp-tbody"' in resp_html.text
    assert 'id="pref-idp-add-form"' in resp_html.text

    # Admin GET JSON
    resp_json = test_env.client.get("/api/admin/idp")
    assert resp_json.status_code == 200
    providers = resp_json.json()["providers"]
    assert len(providers) == 2
    assert providers[0]["id"] == "google"

    # Admin PUT updates IdP list
    updated_payload = [
        {
            "id": "keycloak",
            "type": "oidc",
            "display_name": "Company Keycloak",
            "client_id": "kc-client",
            "enabled": True,
            "allow_signup": True,
            "issuer": "https://auth.company.test/realms/master",
            "scopes": ["openid", "email"],
        }
    ]
    resp_put = test_env.client.put(
        "/api/admin/idp",
        json={"providers": updated_payload},
        headers={"Origin": PUBLIC_URL, "Sec-Fetch-Site": "same-origin"},
    )
    assert resp_put.status_code == 200
    assert resp_put.json()["ok"] is True

    # Re-fetch and check
    resp_get2 = test_env.client.get("/api/admin/idp")
    providers2 = resp_get2.json()["providers"]
    assert len(providers2) == 1
    assert providers2[0]["id"] == "keycloak"


# 8. E2E complete user lifecycle ------------------------------------------------


def test_e2e_oidc_invite_signup_and_login_lifecycle(test_env):
    # 1. Admin creates invite
    auth_conn = auth_db.connect_auth_db(auth_db.auth_db_path(test_env.settings.data_dir))
    inv = auth_db.create_invitation(auth_conn, code="E2ECODE1", max_uses=1, expires_in=3600, created_by=1)
    auth_conn.close()

    # 2. User signs up via Google IdP
    with patch.object(OidcClient, "create_authorization_url", new_callable=AsyncMock) as mock_auth_url, \
         patch.object(OidcClient, "exchange_code", new_callable=AsyncMock) as mock_exchange, \
         patch.object(OidcClient, "verify_id_token", new_callable=AsyncMock) as mock_verify:

        mock_auth_url.return_value = "https://accounts.google.com/o/oauth2/v2/auth?state=mock_state"
        mock_exchange.return_value = {"id_token": "mock.jwt.token"}
        mock_verify.return_value = ExternalIdentity(
            iss="https://accounts.google.com",
            sub="e2e_google_sub_888",
            email="e2e_user@example.com",
            email_verified=True,
            display_name="E2E User",
        )

        user_client = TestClient(test_env.app, base_url=PUBLIC_URL, cookies={})
        resp_start = user_client.get("/auth/oidc/google/start?invite=E2ECODE1", follow_redirects=False)
        assert resp_start.status_code == 302
        state_cookie = user_client.cookies.get("claire_oidc_state")

        auth_conn = auth_db.connect_auth_db(auth_db.auth_db_path(test_env.settings.data_dir))
        row = auth_conn.execute("SELECT state FROM oidc_states WHERE key = ?", (state_cookie,)).fetchone()
        saved_state = row["state"]
        auth_conn.close()

        resp_cb = user_client.get(
            f"/auth/oidc/google/callback?state={saved_state}&code=mock_code",
            follow_redirects=False,
        )
        assert resp_cb.status_code == 302
        signup_token = resp_cb.headers["location"].split("token=")[1]

        resp_finish = user_client.post(
            "/invite/complete",
            json={"token": signup_token, "username": "e2e_member", "display_name": "E2E Member"},
            headers={"Origin": PUBLIC_URL, "Sec-Fetch-Site": "same-origin"},
            follow_redirects=False,
        )
        assert resp_finish.status_code in (200, 302)
        assert user_client.cookies.get("claire_session") is not None

        # 3. User links secondary GitHub IdP
        with patch.object(GitHubAdapter, "create_authorization_url") as mock_gh_url, \
             patch.object(GitHubAdapter, "exchange_code_and_get_identity", new_callable=AsyncMock) as mock_gh_identity:

            mock_gh_url.return_value = "https://github.com/login/oauth/authorize?state=mock_gh_state"
            mock_gh_identity.return_value = ExternalIdentity(
                iss="https://github.com",
                sub="e2e_github_sub_777",
                email="e2e_user@github.test",
                email_verified=True,
                display_name="E2E GitHub",
            )

            # Start GitHub link
            resp_link_start = user_client.post(
                "/api/me/identities/oidc/github/start",
                headers={"Origin": PUBLIC_URL, "Sec-Fetch-Site": "same-origin"},
            )
            assert resp_link_start.status_code == 200
            gh_state_cookie = user_client.cookies.get("claire_oidc_state")

            auth_conn = auth_db.connect_auth_db(auth_db.auth_db_path(test_env.settings.data_dir))
            gh_row = auth_conn.execute("SELECT state FROM oidc_states WHERE key = ?", (gh_state_cookie,)).fetchone()
            gh_state = gh_row["state"]
            auth_conn.close()

            # Callback links GitHub
            resp_gh_cb = user_client.get(
                f"/auth/oidc/github/callback?state={gh_state}&code=mock_gh_code",
                follow_redirects=False,
            )
            assert resp_gh_cb.status_code == 302
            assert "/settings/account?notice=identity_linked" in resp_gh_cb.headers["location"]

            # User logs out
            user_client.post("/logout", headers={"Origin": PUBLIC_URL, "Sec-Fetch-Site": "same-origin"})

            # User logs in with GitHub
            resp_gh_login_start = user_client.get("/auth/oidc/github/start", follow_redirects=False)
            assert resp_gh_login_start.status_code == 302
            gh_login_cookie = user_client.cookies.get("claire_oidc_state")

            auth_conn = auth_db.connect_auth_db(auth_db.auth_db_path(test_env.settings.data_dir))
            gh_login_row = auth_conn.execute("SELECT state FROM oidc_states WHERE key = ?", (gh_login_cookie,)).fetchone()
            gh_login_state = gh_login_row["state"]
            auth_conn.close()

            resp_gh_login_cb = user_client.get(
                f"/auth/oidc/github/callback?state={gh_login_state}&code=mock_gh_code",
                follow_redirects=False,
            )
            assert resp_gh_login_cb.status_code == 302
            assert resp_gh_login_cb.headers["location"] == "/@e2e_member/"

            # Verify authenticated as e2e_member
            resp_me = user_client.get("/api/me")
            assert resp_me.status_code == 200
            assert resp_me.json()["username"] == "e2e_member"
