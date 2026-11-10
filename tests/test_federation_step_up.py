"""Step-up for the ``profile_public`` change and the alternate link on the profile page (Fedify sidecar design Sections 5.1 and 8.4, FD11, unit FP1-4)."""

from __future__ import annotations

import os
import socket
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from starlette.testclient import TestClient

from claire.api import server
from claire.store import accounts as accounts_store
from claire.store import auth_db
from claire.store import db as dbm
from claire.store.secrets import reset_secret_service
from claire.store.theme import get_theme_manager

PUBLIC_URL = "http://127.0.0.1:8765"
OWNER_TOKEN = "owner-" + "o" * 32
PASSWORD = "Correct-Horse-Battery-123!"
MEMBER_PUBLIC_ID = 223456789012
STALE = 16 * 60.0
ALTERNATE = f'<link rel="alternate" type="application/activity+json" href="{PUBLIC_URL}/ap/actors/{MEMBER_PUBLIC_ID}">'


@dataclass
class _Settings:
    db_file: Path
    data_dir: Path
    environment: str = "development"
    public_url: str = PUBLIC_URL
    inject_token: str = OWNER_TOKEN
    readonly_token: str = "readonly-" + "r" * 32
    cors_allowed_origins: str = ""
    anonymous_readonly: bool = True
    fqdn: str = "127.0.0.1:8765"
    multi_theme: bool = True
    telemetry_retention_days: int = 30


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    for key in list(os.environ):
        if key.startswith(("CLAIRE_", "CB_")):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("CLAIRE_SECRET_KEY", "44" * 32)
    reset_secret_service()
    yield
    reset_secret_service()


def _prepare(tmp_path: Path, **overrides: Any) -> tuple[_Settings, int]:
    """A network-mode instance with the admin and one member. The member has a password and a private profile."""
    settings = _Settings(db_file=tmp_path / "claire.db", data_dir=tmp_path, **overrides)
    conn = dbm.connect(settings.db_file)
    try:
        dbm.init_db(conn)
        store = accounts_store.AccountStore(conn)
        store.complete_setup(1, username="admin_kim", display_name="Admin Kim")
        store.set_setting("mode", "network", updated_by=1)
        cur = conn.execute(
            "INSERT INTO accounts (public_id, username, role, status, display_name, created_at, activated_at, updated_at) VALUES (?, 'member_alpha', 'member', 'active', 'Alpha', 0, 0, 0)",
            (MEMBER_PUBLIC_ID,),
        )
        conn.commit()
        member = int(cur.lastrowid)
    finally:
        conn.close()
    auth = auth_db.connect_auth_db(auth_db.auth_db_path(tmp_path))
    try:
        auth_db.set_password(auth, member, PASSWORD, username="member_alpha")
    finally:
        auth.close()
    # The profile page shows a theme. A member without a theme has no page.
    get_theme_manager(settings).define_theme("Alpha notes", account_id=member, is_default=True)
    return settings, member


def _session(settings: _Settings, account_id: int, *, authenticated_ago: float = 0.0) -> str:
    conn = dbm.connect_existing(settings.db_file)
    try:
        return accounts_store.create_account_session(conn, account_id=account_id, scope="owner", now=time.time() - authenticated_ago)
    finally:
        conn.close()


def _store_call(settings: _Settings, fn):
    conn = dbm.connect_existing(settings.db_file)
    try:
        return fn(accounts_store.AccountStore(conn))
    finally:
        conn.close()


def _set_federation(settings: _Settings, state: str) -> None:
    _store_call(settings, lambda store: store.set_federation_state(state, updated_by=1))


def _is_public(settings: _Settings, account_id: int) -> bool:
    return _store_call(settings, lambda store: store.get(account_id).profile_public)


class _Client:
    def __init__(self, client: TestClient) -> None:
        self.client = client

    def request(self, method: str, path: str, *, session: str | None = None, headers: dict[str, str] | None = None, **kwargs: Any):
        headers = dict(headers or {})
        if session is not None:
            headers["Cookie"] = f"claire_session={session}"
        if method not in {"GET", "HEAD"}:
            headers["Origin"] = PUBLIC_URL
        self.client.cookies.clear()
        response = self.client.request(method, path, headers=headers, follow_redirects=False, **kwargs)
        self.client.cookies.clear()
        return response


@pytest.fixture
def env(tmp_path):
    settings, member = _prepare(tmp_path)
    app = server.create_app(settings, SimpleNamespace(provider=SimpleNamespace(name="stub")))
    with TestClient(app, base_url=PUBLIC_URL) as tc:
        yield SimpleNamespace(client=_Client(tc), settings=settings, member=member)


# --- PATCH /api/me (module interface: route, store, and step-up rule) ---


def test_change_to_public_needs_recent_authentication_when_federation_is_on(env):
    _set_federation(env.settings, "on")
    stale = _session(env.settings, env.member, authenticated_ago=STALE)

    refused = env.client.request("PATCH", "/api/me", session=stale, json={"profile_public": 1})
    assert refused.status_code == 401
    assert refused.json() == {"error": "reauth_required", "reauth": "/api/auth/reauth"}
    assert _is_public(env.settings, env.member) is False

    # No field of a refused request is written.
    refused = env.client.request("PATCH", "/api/me", session=stale, json={"display_name": "Changed", "profile_public": True})
    assert refused.status_code == 401
    assert _store_call(env.settings, lambda store: store.get(env.member).display_name) == "Alpha"

    # A change that is not 0 -> 1 has no new condition.
    assert env.client.request("PATCH", "/api/me", session=stale, json={"display_name": "Alpha Two"}).status_code == 200
    assert env.client.request("PATCH", "/api/me", session=stale, json={"profile_public": 0}).status_code == 200

    # Re-authentication on the same session permits the change.
    assert env.client.request("POST", "/api/auth/reauth", session=stale, json={"password": PASSWORD}).status_code == 200
    accepted = env.client.request("PATCH", "/api/me", session=stale, json={"profile_public": 1})
    assert accepted.status_code == 200 and accepted.json()["profile_public"] == 1
    assert _is_public(env.settings, env.member) is True


def test_pause_and_repeat_never_need_recent_authentication(env):
    _set_federation(env.settings, "on")
    fresh = _session(env.settings, env.member)
    assert env.client.request("PATCH", "/api/me", session=fresh, json={"profile_public": 1}).status_code == 200

    stale = _session(env.settings, env.member, authenticated_ago=STALE)
    # 1 -> 1 is not a change to public.
    assert env.client.request("PATCH", "/api/me", session=stale, json={"profile_public": 1}).status_code == 200
    # 1 -> 0: the user can always pause.
    paused = env.client.request("PATCH", "/api/me", session=stale, json={"profile_public": 0})
    assert paused.status_code == 200 and paused.json()["profile_public"] == 0
    # 0 -> 1 again needs the step-up.
    assert env.client.request("PATCH", "/api/me", session=stale, json={"profile_public": 1}).status_code == 401


def test_bearer_credential_cannot_make_the_change_when_federation_is_on(env):
    _set_federation(env.settings, "on")
    bearer = {"Authorization": f"Bearer {OWNER_TOKEN}"}
    refused = env.client.request("PATCH", "/api/me", headers=bearer, json={"profile_public": 1})
    assert refused.status_code == 403
    assert refused.json()["error"] == "step_up_requires_session"
    assert _is_public(env.settings, 1) is False


def test_contract_is_unchanged_when_federation_is_off(env):
    stale = _session(env.settings, env.member, authenticated_ago=STALE)
    accepted = env.client.request("PATCH", "/api/me", session=stale, json={"profile_public": 1})
    assert accepted.status_code == 200 and accepted.json()["profile_public"] == 1
    assert env.client.request("PATCH", "/api/me", session=stale, json={"profile_public": 0}).status_code == 200
    bearer = {"Authorization": f"Bearer {OWNER_TOKEN}"}
    assert env.client.request("PATCH", "/api/me", headers=bearer, json={"profile_public": 1}).status_code == 200
    assert env.client.request("PATCH", "/api/me", session=stale, json={"profile_public": 2}).status_code == 400


# --- /settings/account switch ---


def test_switch_markup_shows_the_step_up_only_when_federation_is_on(env):
    session = _session(env.settings, env.member)
    off = env.client.request("GET", "/settings/account", session=session).text
    assert 'data-settings-toggle="profile-public"' in off
    assert "data-step-up" not in off and "data-federation-hint" not in off

    _set_federation(env.settings, "on")
    on = env.client.request("GET", "/settings/account", session=session).text
    assert 'data-settings-toggle="profile-public" data-step-up="password"' in on
    assert "data-federation-hint" in on and "지워지지 않습니다" in on


# --- Alternate link on the profile page ---


def test_profile_page_has_the_alternate_link_for_a_federation_actor_only(env):
    session = _session(env.settings, env.member)
    actor_link = f'<{PUBLIC_URL}/ap/actors/{MEMBER_PUBLIC_ID}>; rel="alternate"; type="application/activity+json"'

    def profile(**kwargs):
        return env.client.request("GET", "/@member_alpha/", **kwargs)

    # Federation off, public profile: no link.
    assert env.client.request("PATCH", "/api/me", session=session, json={"profile_public": 1}).status_code == 200
    page = profile()
    assert page.status_code == 200 and ALTERNATE not in page.text and "link" not in page.headers

    # Federation on, public profile: link in the head and in the header, also for an anonymous reader.
    _set_federation(env.settings, "on")
    page = profile()
    assert page.status_code == 200
    assert page.text.count(ALTERNATE) == 1 and page.text.index(ALTERNATE) < page.text.index("</head>")
    assert page.headers["link"] == actor_link
    # The page keeps its HTML format. The path has no content negotiation.
    negotiated = profile(headers={"Accept": "application/activity+json"})
    assert negotiated.headers["content-type"].startswith("text/html")

    # Federation on, paused profile: the owner sees the page without the link.
    assert env.client.request("PATCH", "/api/me", session=session, json={"profile_public": 0}).status_code == 200
    page = profile(session=session)
    assert page.status_code == 200 and ALTERNATE not in page.text and "link" not in page.headers
    assert profile().status_code == 404


# --- End-to-end: the step-up dialog in a real browser ---


def test_e2e_step_up_dialog_makes_the_profile_public(tmp_path):
    sync_api = pytest.importorskip("playwright.sync_api")
    uvicorn = pytest.importorskip("uvicorn")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = int(probe.getsockname()[1])
    base = f"http://127.0.0.1:{port}"
    settings, member = _prepare(tmp_path, public_url=base, fqdn=f"127.0.0.1:{port}")
    _set_federation(settings, "on")
    stale = _session(settings, member, authenticated_ago=STALE)
    app = server.create_app(settings, SimpleNamespace(provider=SimpleNamespace(name="stub")))
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="on"))
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not srv.started:
        assert thread.is_alive() and time.monotonic() < deadline, "test server did not start"
        time.sleep(0.05)
    try:
        with sync_api.sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:  # noqa: BLE001
                pytest.skip(f"Playwright Chromium is not available: {exc}")
            try:
                ctx = browser.new_context()
                ctx.add_cookies([{"name": "claire_session", "value": stale, "url": base}])
                page = ctx.new_page()
                page.goto(f"{base}/settings/account")
                toggle = page.locator("#pref-profile-public")
                dialog = page.locator("dialog[data-step-up-dialog]")

                # Cancel: no change is sent and the switch goes back.
                toggle.click()
                dialog.wait_for(state="visible")
                dialog.locator("[data-step-up-cancel]").click()
                dialog.wait_for(state="detached")
                assert toggle.is_checked() is False
                assert _is_public(settings, member) is False

                # A wrong password keeps the dialog open.
                toggle.click()
                dialog.wait_for(state="visible")
                dialog.locator("input[name='password']").fill("wrong-password-000")
                dialog.locator("[data-step-up-confirm]").click()
                dialog.locator("[data-settings-result][data-state='error']").wait_for()
                assert _is_public(settings, member) is False

                # The correct password sends the change.
                dialog.locator("input[name='password']").fill(PASSWORD)
                with page.expect_response(lambda r: r.url.endswith("/api/me") and r.request.method == "PATCH") as patched:
                    dialog.locator("[data-step-up-confirm]").click()
                assert patched.value.status == 200
                dialog.wait_for(state="detached")
                assert toggle.is_checked() is True
                assert _is_public(settings, member) is True

                # The change to private has no dialog.
                with page.expect_response(lambda r: r.url.endswith("/api/me") and r.request.method == "PATCH") as patched:
                    toggle.click()
                assert patched.value.status == 200
                assert page.locator("dialog[data-step-up-dialog]").count() == 0
                assert _is_public(settings, member) is False
            finally:
                browser.close()
    finally:
        srv.should_exit = True
        thread.join(timeout=15)
