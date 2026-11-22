"""End-to-end HTTP tests for case-preserving usernames and Fedify-compatible username changes."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from http.cookies import SimpleCookie
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from starlette.testclient import TestClient

from claire.api import federation_routes, server
from claire.api.federation_internal import INTERNAL_PREFIX, SIGNATURE_HEADER, TIMESTAMP_HEADER, create_internal_federation_app, ensure_service_key, load_service_key, service_key_path, sign_request
from claire.store import accounts as accounts_store
from claire.store import auth_db
from claire.store import db as dbm
from claire.store.secrets import reset_secret_service
from claire.store.token_store import reset_token_stores

PUBLIC_URL = "http://127.0.0.1:8765"
OWNER_TOKEN = "owner-" + "o" * 32
READONLY_TOKEN = "readonly-" + "r" * 32
PASSWORD = "correct-horse-battery-99"


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
    fqdn: str = "claire.example.com"
    multi_theme: bool = True
    trusted_proxies: str = ""
    telemetry_retention_days: int = 30


class _Client:
    def __init__(self, tc: TestClient) -> None:
        self.tc = tc

    def request(self, method: str, path: str, *, session: str | None = None, origin: str | None = PUBLIC_URL, **kwargs: Any):
        headers = dict(kwargs.pop("headers", {}) or {})
        if session is not None:
            headers["Cookie"] = f"claire_session={session}"
        if origin is not None and method not in {"GET", "HEAD"}:
            headers["Origin"] = origin
        self.tc.cookies.clear()
        response = self.tc.request(method, path, headers=headers, follow_redirects=False, **kwargs)
        self.tc.cookies.clear()
        return response


def _cookie_session(response) -> str:
    for raw in response.headers.get_list("set-cookie"):
        cookie = SimpleCookie()
        cookie.load(raw)
        morsel = cookie.get("claire_session")
        if morsel is not None and morsel.value:
            return morsel.value
    raise AssertionError(f"claire_session cookie missing from {response.headers.get_list('set-cookie')}")


def _sign_internal_call(settings: _Settings, internal_client: TestClient, path: str, now_ts: float):
    ensure_service_key(service_key_path(settings.data_dir))
    key = load_service_key(service_key_path(settings.data_dir))
    ts = str(int(now_ts))
    sig = sign_request(key, "GET", path, ts, b"")
    return internal_client.get(path, headers={TIMESTAMP_HEADER: ts, SIGNATURE_HEADER: sig})


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch):
    for key in list(os.environ):
        if key.startswith(("CLAIRE_", "CB_", "GEMINI_", "GOOGLE_API")):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("CLAIRE_SECRET_KEY", "44" * 32)
    reset_secret_service()
    reset_token_stores()
    yield
    reset_secret_service()
    reset_token_stores()


def test_e2e_case_preserving_usernames_and_rename_lifecycle(tmp_path: Path) -> None:
    """Exercise setup, login, invites, direct mixed-case routing, step-up rename, 7-day hold, early release, and federation outbox."""
    settings = _Settings(db_file=tmp_path / "claire.db", data_dir=tmp_path)
    app = server.create_app(settings, SimpleNamespace(provider=SimpleNamespace(name="mock")))

    with TestClient(app, base_url=PUBLIC_URL) as tc:
        client = _Client(tc)

        # 1. Mixed-case /setup preserves display case and redirects to / in single mode, while /@FounderUser/ resolves case-insensitively.
        conn = dbm.connect_existing(settings.db_file)
        try:
            setup_session = accounts_store.create_account_session(conn, account_id=1)
        finally:
            conn.close()

        setup_resp = client.request(
            "POST",
            "/setup",
            session=setup_session,
            data={"username": "FounderUser", "display_name": "Founder User", "password": PASSWORD, "password_confirm": PASSWORD, "ack": "1"},
        )
        assert setup_resp.status_code == 303
        assert setup_resp.headers["location"] == "/"
        admin_session = _cookie_session(setup_resp)
        for admin_path in ("/@FounderUser/", "/@founderuser/"):
            single_page = client.request("GET", admin_path, session=admin_session)
            assert single_page.status_code == 200
            assert 'content="/@FounderUser/"' in single_page.text

        # 2. Switch to network mode and turn federation on.
        conn = dbm.connect_existing(settings.db_file)
        try:
            store = accounts_store.AccountStore(conn)
            store.set_setting("mode", "network", updated_by=1)
        finally:
            conn.close()

        # 3. Case-insensitive /login in network mode accepts lowercase input and redirects to display-case home.
        login_resp = client.request("POST", "/login", data={"username": "founderuser", "password": PASSWORD})
        assert login_resp.status_code == 303
        assert login_resp.headers["location"] == "/@FounderUser/"
        admin_session = _cookie_session(login_resp)

        # 4. Issue mixed-case reserved invite ("NASA") and redeem case-insensitively with "NASA".
        inv_resp = client.request("POST", "/api/invites", session=admin_session, json={"max_uses": 1, "reserved_username": "NASA"})
        assert inv_resp.status_code == 201
        invite_code = inv_resp.json()["code"]

        mismatch = client.request(
            "POST",
            f"/invite/{invite_code}",
            json={"username": "OtherName", "display_name": "Space", "password": PASSWORD},
        )
        assert mismatch.status_code == 400 and mismatch.json()["error"] == "reserved_username_mismatch"

        signup = client.request(
            "POST",
            f"/invite/{invite_code}",
            json={"username": "NASA", "display_name": "NASA Space", "password": PASSWORD},
        )
        assert signup.status_code == 201
        assert signup.json()["username"] == "NASA"
        assert signup.json()["home"] == "/@NASA/"
        nasa_session = _cookie_session(signup)

        # 5. All case variants at /@{username}/ return 200 OK without case redirect; missing trailing slash preserves case in 308.
        for path in ("/@NASA/", "/@nasa/", "/@Nasa/"):
            page = client.request("GET", path, session=nasa_session)
            assert page.status_code == 200
            assert 'content="/@NASA/"' in page.text
        slash_redirect = client.request("GET", "/@nasa", session=nasa_session)
        assert slash_redirect.status_code == 308
        assert slash_redirect.headers["location"] == "/@nasa/"

        # Make NASA public so federation outbox queues update_actor on renames (requires fresh auth).
        pub_resp = client.request("PATCH", "/api/me", session=nasa_session, json={"profile_public": 1})
        assert pub_resp.status_code == 200 and pub_resp.json()["profile_public"] == 1

        # Settings UI exposes username change form with mixed-case value.
        settings_page = client.request("GET", "/settings/account", session=nasa_session)
        assert settings_page.status_code == 200
        assert 'data-settings-form="username"' in settings_page.text
        assert 'value="NASA"' in settings_page.text

        # 6. Stale step-up cannot change username until POST /api/auth/reauth runs.
        conn = dbm.connect_existing(settings.db_file)
        try:
            conn.execute("UPDATE auth_sessions SET authenticated_at=? WHERE session_token=?", (time.time() - 3600.0, nasa_session))
            conn.commit()
        finally:
            conn.close()

        stale_attempt = client.request("POST", "/api/me/username", session=nasa_session, json={"username": "Nasa"})
        assert stale_attempt.status_code == 401 and stale_attempt.json()["error"] == "reauth_required"

        assert client.request("POST", "/api/auth/reauth", session=nasa_session, json={"password": PASSWORD}).status_code == 200

        # 7. Case-only rename (NASA -> Nasa) updates display case without retiring "nasa".
        case_only = client.request("POST", "/api/me/username", session=nasa_session, json={"username": "Nasa"})
        assert case_only.status_code == 200
        case_body = case_only.json()
        assert case_body["ok"] is True and case_body["changed"] is True and case_body["case_only"] is True
        assert case_body["username"] == "Nasa" and case_body["previous_username"] == "NASA"
        assert case_body["retired_until"] is None
        assert case_body["federation_update_queued"] is True

        retired_before = client.request("GET", "/api/admin/usernames/retired", session=admin_session)
        assert retired_before.status_code == 200 and retired_before.json()["usernames"] == []

        # 8. Full rename (Nasa -> Apollo) retires "nasa" for 7 days and keeps active session working at /@Apollo/.
        renamed = client.request("POST", "/api/me/username", session=nasa_session, json={"username": "Apollo"})
        assert renamed.status_code == 200
        ren_body = renamed.json()
        assert ren_body["ok"] is True and ren_body["changed"] is True and ren_body["case_only"] is False
        assert ren_body["username"] == "Apollo" and ren_body["previous_username"] == "Nasa"
        assert ren_body["retired_until"] is not None
        assert ren_body["federation_update_queued"] is True
        assert ren_body["home_url"] == "/@Apollo/"

        assert client.request("GET", "/@Apollo/", session=nasa_session).status_code == 200
        assert client.request("GET", "/@apollo/", session=nasa_session).status_code == 200
        assert client.request("GET", "/@nasa/", session=admin_session).status_code == 410
        assert client.request("GET", "/@NASA/", session=admin_session).status_code == 410

        # Signup attempt using held username is rejected as retired.
        inv2 = client.request("POST", "/api/invites", session=admin_session, json={"max_uses": 1}).json()["code"]
        held_reject = client.request("POST", f"/invite/{inv2}", json={"username": "NASA", "display_name": "Impostor", "password": PASSWORD})
        assert held_reject.status_code == 400 and held_reject.json()["error"] == "username_retired"

        # Audit log records account.username_change without storing username strings (K4).
        auth_conn = auth_db.connect_auth_db(auth_db.auth_db_path(settings.data_dir))
        try:
            audits = auth_conn.execute("SELECT action, detail_json FROM admin_audit WHERE action='account.username_change' ORDER BY id").fetchall()
            assert len(audits) == 2
            for _act, raw_detail in audits:
                parsed = json.loads(raw_detail)
                assert "username" not in raw_detail.lower()
                assert set(parsed) == {"case_only", "federation_update_queued"}
        finally:
            auth_conn.close()

        # 9. Internal federation API resolves case-insensitively with display case and exposes update_actor outbox tasks.
        now_clock = time.time() + 10.0
        int_routes = federation_routes.federation_internal_routes(settings, now=lambda: now_clock)
        internal_tc = TestClient(create_internal_federation_app(settings, routes=int_routes, now=lambda: now_clock))
        by_name = _sign_internal_call(settings, internal_tc, f"{INTERNAL_PREFIX}/actors/by-username/apollo", now_clock)
        assert by_name.status_code == 200
        actor_json = by_name.json()
        assert actor_json["username"] == "Apollo"
        assert actor_json["profile_url"] == f"{PUBLIC_URL}/@Apollo/"

        outbox = _sign_internal_call(settings, internal_tc, f"{INTERNAL_PREFIX}/outbox-tasks?limit=10", now_clock)
        assert outbox.status_code == 200
        kinds = [t["kind"] for t in outbox.json()["tasks"]]
        assert "update_actor" in kinds

        # 10. Early admin release unlocks "nasa" without disrupting Apollo's active account or session.
        assert client.request("POST", "/api/auth/reauth", session=admin_session, json={"password": PASSWORD}).status_code == 200
        preview = client.request("POST", "/api/admin/usernames/release", session=admin_session, json={"username": "NASA", "apply": False})
        assert preview.status_code == 200
        assert preview.json()["reason"] == "rename" and preview.json()["residual_sessions"] == 0

        release = client.request("POST", "/api/admin/usernames/release", session=admin_session, json={"username": "NASA", "apply": True})
        assert release.status_code == 200 and release.json()["released"] is True

        # Apollo stays logged in and active; "nasa" stops returning 410 and can be claimed again.
        assert client.request("GET", "/api/me", session=nasa_session).json()["username"] == "Apollo"
        assert client.request("GET", "/@nasa/", session=admin_session).status_code == 404

        inv3 = client.request("POST", "/api/invites", session=admin_session, json={"max_uses": 1}).json()["code"]
        reused = client.request("POST", f"/invite/{inv3}", json={"username": "Bob", "display_name": "Bob", "password": PASSWORD})
        assert reused.status_code == 201
        bob_session = _cookie_session(reused)

        # Private profile rename returns 404 on old handle and auto-expires after 7 days. The account is an actor with a private profile too, so its followers get the new name.
        bob_rename = client.request("POST", "/api/me/username", session=bob_session, json={"username": "Bobby"})
        assert bob_rename.status_code == 200
        assert bob_rename.json()["federation_update_queued"] is True
        assert client.request("GET", "/@bob/", session=admin_session).status_code == 404

        conn = dbm.connect_existing(settings.db_file)
        try:
            conn.execute("UPDATE username_tombstones SET reusable_after=? WHERE username='bob'", (time.time() - 5.0,))
            conn.commit()
        finally:
            conn.close()

        claim_expired = client.request("POST", f"/invite/{inv2}", json={"username": "bob", "display_name": "New Bob", "password": PASSWORD})
        assert claim_expired.status_code == 201 and claim_expired.json()["username"] == "bob"
