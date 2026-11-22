"""The ``profile_public`` switch needs no confirmation of identity, and the profile page has the alternate link of the actor (FEDERATION_GO_LIVE_DESIGN.md decisions GD5 and GD6; Fedify sidecar design Section 5.1; FD11 is withdrawn)."""

from __future__ import annotations

import os
import re
import socket
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from starlette.testclient import TestClient

from claire.api import server
from claire.core.config import Settings
from claire.store import accounts as accounts_store
from claire.store import auth_db
from claire.store import db as dbm
from claire.store.secrets import reset_secret_service
from claire.store.knowledge_base import get_kb_manager

PUBLIC_URL = "http://127.0.0.1:8765"
OWNER_TOKEN = "owner-" + "o" * 32
PASSWORD = "Correct-Horse-Battery-123!"
MEMBER_PUBLIC_ID = 223456789012
STALE = 16 * 60.0
ALTERNATE = f'<link rel="alternate" type="application/activity+json" href="{PUBLIC_URL}/ap/actors/{MEMBER_PUBLIC_ID}">'


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    for key in list(os.environ):
        if key.startswith(("CLAIRE_", "CB_")):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("CLAIRE_SECRET_KEY", "44" * 32)
    reset_secret_service()
    yield
    reset_secret_service()


def _prepare(tmp_path: Path, **overrides: Any) -> tuple[Settings, int]:
    """A network-mode server with the admin and one member. The member has a password and a private profile."""
    public_url = str(overrides.pop("public_url", PUBLIC_URL))
    fqdn = str(overrides.pop("fqdn", "127.0.0.1:8765"))
    settings = Settings(
        CLAIRE_DB_PATH=str(tmp_path / "claire.db"),
        CLAIRE_VAULT_PATH=str(tmp_path / "vault"),
        CLAIRE_PROVIDER="mock",
        CLAIRE_ENVIRONMENT="development",
        CLAIRE_PUBLIC_URL=public_url,
        CLAIRE_FQDN=fqdn,
        CLAIRE_INJECT_TOKEN=OWNER_TOKEN,
        CLAIRE_READONLY_TOKEN="readonly-" + "r" * 32,
        CLAIRE_ANONYMOUS_READONLY=True,
        CLAIRE_MULTI_THEME=True,
        **overrides,
    )
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
        assert cur.lastrowid is not None
        member = int(cur.lastrowid)
    finally:
        conn.close()
    auth = auth_db.connect_auth_db(auth_db.auth_db_path(tmp_path))
    try:
        auth_db.set_password(auth, member, PASSWORD, username="member_alpha")
    finally:
        auth.close()
    # The profile page shows a theme. A member without a theme has no page.
    get_kb_manager(settings).define_kb("Alpha notes", account_id=member, is_personal=True)
    return settings, member


def _session(settings: Settings, account_id: int, *, authenticated_ago: float = 0.0) -> str:
    conn = dbm.connect_existing(settings.db_file)
    try:
        return accounts_store.create_account_session(conn, account_id=account_id, scope="owner", now=time.time() - authenticated_ago)
    finally:
        conn.close()


def _store_call(settings: Settings, fn):
    conn = dbm.connect_existing(settings.db_file)
    try:
        return fn(accounts_store.AccountStore(conn))
    finally:
        conn.close()


def _is_public(settings: Settings, account_id: int) -> bool:
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


# --- PATCH /api/me (module interface: route and store) ---


def test_profile_switch_needs_no_recent_authentication(env):
    """Decision GD6: a session with an old sign-in changes the switch in the two directions, and the other fields of the same request are written."""
    stale = _session(env.settings, env.member, authenticated_ago=STALE)

    accepted = env.client.request("PATCH", "/api/me", session=stale, json={"display_name": "Alpha Two", "profile_public": True})
    assert accepted.status_code == 200 and accepted.json()["profile_public"] == 1
    assert _is_public(env.settings, env.member) is True
    assert _store_call(env.settings, lambda store: store.get(env.member).display_name) == "Alpha Two"

    # 1 -> 1 and 1 -> 0 are the same.
    assert env.client.request("PATCH", "/api/me", session=stale, json={"profile_public": 1}).status_code == 200
    private = env.client.request("PATCH", "/api/me", session=stale, json={"profile_public": 0})
    assert private.status_code == 200 and private.json()["profile_public"] == 0
    assert _is_public(env.settings, env.member) is False


def test_bearer_credential_can_make_the_change(env):
    bearer = {"Authorization": f"Bearer {OWNER_TOKEN}"}
    accepted = env.client.request("PATCH", "/api/me", headers=bearer, json={"profile_public": 1})
    assert accepted.status_code == 200 and accepted.json()["profile_public"] == 1
    assert _is_public(env.settings, 1) is True


def test_a_value_that_is_not_0_or_1_is_refused(env):
    stale = _session(env.settings, env.member, authenticated_ago=STALE)
    assert env.client.request("PATCH", "/api/me", session=stale, json={"profile_public": 2}).status_code == 400


# --- /settings/account switch ---


def test_switch_markup_has_no_step_up(env):
    session = _session(env.settings, env.member)
    page = env.client.request("GET", "/settings/account", session=session).text
    switch = re.search(r'<input id="pref-profile-public"[^>]*>', page)
    assert switch is not None and 'data-settings-toggle="profile-public"' in switch.group(0) and "data-step-up" not in switch.group(0)
    assert "data-federation-hint" in page and "federation features work in both states" in page


# --- Alternate link on the profile page ---


def test_profile_page_has_the_alternate_link_of_the_actor(env):
    session = _session(env.settings, env.member)
    actor_link = f'<{PUBLIC_URL}/ap/actors/{MEMBER_PUBLIC_ID}>; rel="alternate"; type="application/activity+json"'

    def profile(**kwargs):
        return env.client.request("GET", "/@member_alpha/", **kwargs)

    # Seed one document in the public personal KB so sitemap filtering can also be verified.
    conn = dbm.connect_existing(env.settings.db_file)
    try:
        kb = get_kb_manager(env.settings).personal_kb_for(env.member)
        conn.execute(
            "INSERT INTO documents (id, kb_id, title, detail, detail_format, hidden, fetched_at) VALUES ('doc-pub-sitemap', ?, 'Alpha Doc', 'body', 'md', 0, 1.0)",
            (kb.id,),
        )
        conn.commit()
    finally:
        conn.close()

    # 1. Public profile + public personal KB: workspace renders with index, follow and document appears in sitemap.xml.
    assert env.client.request("PATCH", "/api/me", session=session, json={"profile_public": 1}).status_code == 200
    page = profile()
    assert page.status_code == 200
    assert 'data-profile-state="private"' not in page.text
    assert '<meta name="robots" content="index, follow"/>' in page.text
    assert "x-robots-tag" not in page.headers
    assert page.text.count(ALTERNATE) == 1 and page.text.index(ALTERNATE) < page.text.index("</head>")
    assert page.headers["link"] == actor_link
    negotiated = profile(headers={"Accept": "application/activity+json"})
    assert negotiated.headers["content-type"].startswith("text/html")
    assert env.client.request("GET", "/@member_alpha/graph").status_code == 200
    kb_pub = env.client.request("GET", "/@member_alpha/kb", headers={"Accept": "text/html"})
    assert kb_pub.status_code == 200 and '<meta name="robots" content="index, follow"/>' in kb_pub.text and "x-robots-tag" not in kb_pub.headers
    server._sitemap_cache.clear()
    sitemap_pub = env.client.request("GET", "/sitemap.xml")
    assert sitemap_pub.status_code == 200 and "/p?s=" in sitemap_pub.text

    # 2. Private profile + public personal KB (Model 1 / GD8): personal KB remains readable, while noindex + X-Robots-Tag apply and sitemap excludes the personal KB.
    assert env.client.request("PATCH", "/api/me", session=session, json={"profile_public": 0}).status_code == 200
    priv_ws = profile()
    assert priv_ws.status_code == 200 and 'data-profile-state="private"' not in priv_ws.text
    assert '<meta name="robots" content="noindex, nofollow"/>' in priv_ws.text
    assert priv_ws.headers["x-robots-tag"] == "noindex, nofollow"
    assert priv_ws.text.count(ALTERNATE) == 1 and priv_ws.headers["link"] == actor_link
    assert env.client.request("GET", "/@member_alpha/graph").status_code == 200
    kb_priv = env.client.request("GET", "/@member_alpha/kb", headers={"Accept": "text/html"})
    assert kb_priv.status_code == 200 and '<meta name="robots" content="noindex, nofollow"/>' in kb_priv.text and kb_priv.headers["x-robots-tag"] == "noindex, nofollow"
    sitemap_priv = env.client.request("GET", "/sitemap.xml")
    assert sitemap_priv.status_code == 200 and "/p?s=" not in sitemap_priv.text

    # 3. Private personal KB (is_public=0): owner sees workspace; non-owners get 200 basic landing page on GET/HEAD /@{username}/ while personal KB subpaths return 404.
    conn = dbm.connect_existing(env.settings.db_file)
    try:
        conn.execute("UPDATE knowledge_bases SET is_public=0 WHERE id=?", (kb.id,))
        conn.commit()
    finally:
        conn.close()
    own_page = profile(session=session)
    assert own_page.status_code == 200 and 'data-profile-state="private"' not in own_page.text and own_page.text.count(ALTERNATE) == 1 and own_page.headers["link"] == actor_link

    anon = profile()
    assert anon.status_code == 200
    assert anon.headers["content-type"].startswith("text/html")
    assert anon.headers["x-robots-tag"] == "noindex, nofollow"
    assert anon.headers["link"] == actor_link
    assert 'data-profile-state="private"' in anon.text
    assert '<meta name="robots" content="noindex, nofollow">' in anon.text
    assert anon.text.count(ALTERNATE) == 1 and anon.text.index(ALTERNATE) < anon.text.index("</head>")
    assert "@member_alpha@" in anon.text
    assert "personal knowledge base is private" in anon.text
    assert "Knowledge graph" not in anon.text

    anon_ap = profile(headers={"Accept": "application/activity+json"})
    assert anon_ap.status_code == 200 and anon_ap.headers["content-type"].startswith("text/html")

    head = env.client.request("HEAD", "/@member_alpha/")
    assert head.status_code == 200 and head.headers["link"] == actor_link and head.headers["x-robots-tag"] == "noindex, nofollow"

    assert env.client.request("GET", "/@member_alpha/?doc=secret").status_code == 200 and 'data-profile-state="private"' in env.client.request("GET", "/@member_alpha/?doc=secret").text
    assert env.client.request("GET", "/@member_alpha/graph").status_code == 404
    assert env.client.request("GET", "/@member_alpha/documents").status_code == 404

    # When profile_public=1 and personal KB is_public=0, the basic landing page is indexable (no noindex / X-Robots-Tag).
    assert env.client.request("PATCH", "/api/me", session=session, json={"profile_public": 1}).status_code == 200
    anon_discoverable = profile()
    assert anon_discoverable.status_code == 200 and 'data-profile-state="private"' in anon_discoverable.text
    assert '<meta name="robots" content="index, follow">' in anon_discoverable.text
    assert "x-robots-tag" not in anon_discoverable.headers
    assert env.client.request("GET", "/@member_alpha/graph").status_code == 404


# --- End-to-end: the switch in a real browser ---


def test_e2e_switch_changes_the_profile_without_a_dialog(tmp_path):
    sync_api = pytest.importorskip("playwright.sync_api")
    uvicorn = pytest.importorskip("uvicorn")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = int(probe.getsockname()[1])
    base = f"http://127.0.0.1:{port}"
    settings, member = _prepare(tmp_path, public_url=base, fqdn=f"127.0.0.1:{port}")
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
                # Start with a private personal KB (is_public=0) and private profile (profile_public=0).
                conn = dbm.connect_existing(settings.db_file)
                try:
                    kb = get_kb_manager(settings).personal_kb_for(member)
                    conn.execute("UPDATE knowledge_bases SET is_public=0 WHERE id=?", (kb.id,))
                    conn.commit()
                finally:
                    conn.close()

                anon_ctx = browser.new_context()
                anon_page = anon_ctx.new_page()
                priv_res = anon_page.goto(f"{base}/@member_alpha/", wait_until="domcontentloaded")
                assert priv_res is not None and priv_res.status == 200
                assert anon_page.locator('[data-profile-state="private"]').is_visible()
                assert f"@member_alpha@127.0.0.1:{port}" in anon_page.locator(".profile-handle").inner_text()
                assert "noindex" in (anon_page.locator('meta[name="robots"]').get_attribute("content") or "")
                assert anon_page.locator(f'link[rel="alternate"][type="application/activity+json"][href="{base}/ap/actors/{MEMBER_PUBLIC_ID}"]').count() == 1
                assert anon_ctx.request.get(f"{base}/@member_alpha/graph").status == 404

                # Make personal KB public: anonymous visitor immediately sees workspace even while profile_public=0, with noindex metadata.
                conn = dbm.connect_existing(settings.db_file)
                try:
                    conn.execute("UPDATE knowledge_bases SET is_public=1 WHERE id=?", (kb.id,))
                    conn.commit()
                finally:
                    conn.close()
                anon_page.goto(f"{base}/@member_alpha/", wait_until="domcontentloaded")
                assert anon_page.locator('[data-profile-state="private"]').count() == 0
                assert "noindex" in (anon_page.locator('meta[name="robots"]').get_attribute("content") or "")
                assert anon_ctx.request.get(f"{base}/@member_alpha/graph").status == 200

                ctx = browser.new_context()
                ctx.add_cookies([{"name": "claire_session", "value": stale, "url": base}])
                page = ctx.new_page()
                page.goto(f"{base}/settings/account")
                toggle = page.locator("#pref-profile-public")
                assert toggle.is_checked() is False

                # Toggling profile_public sends PATCH without step-up dialog and updates search indexing to index, follow.
                with page.expect_response(lambda r: r.url.endswith("/api/me") and r.request.method == "PATCH") as patched:
                    toggle.click()
                assert patched.value.status == 200
                assert page.locator("dialog[data-step-up-dialog]").count() == 0
                assert toggle.is_checked() is True
                assert _is_public(settings, member) is True

                anon_page.goto(f"{base}/@member_alpha/", wait_until="domcontentloaded")
                assert anon_page.locator('meta[name="robots"]').get_attribute("content") == "index, follow"

                with page.expect_response(lambda r: r.url.endswith("/api/me") and r.request.method == "PATCH") as patched:
                    toggle.click()
                assert patched.value.status == 200
                assert page.locator("dialog[data-step-up-dialog]").count() == 0
                assert toggle.is_checked() is False
                assert _is_public(settings, member) is False

                anon_page.goto(f"{base}/@member_alpha/", wait_until="domcontentloaded")
                assert anon_page.locator('meta[name="robots"]').get_attribute("content") == "noindex, nofollow"
            finally:
                browser.close()
    finally:
        srv.should_exit = True
        thread.join(timeout=15)
