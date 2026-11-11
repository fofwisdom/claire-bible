"""Account federation routes, domain block routes, and the console sections ``/settings/federation`` and ``/admin/federation`` (Fedify sidecar design Sections 9.2 and 11, unit FP2-4).

The tests examine the interfaces between the routes, ``ROUTE_POLICY``, the stores, and the audit table. The last test uses a real browser.
"""

from __future__ import annotations

import json
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
from claire.store import federation as federation_store
from claire.store.secrets import reset_secret_service

PUBLIC_URL = "http://127.0.0.1:8765"
OWNER_TOKEN = "owner-" + "o" * 32
PASSWORD = "Correct-Horse-Battery-123!"
ALPHA_PUBLIC_ID = 223456789012
BETA_PUBLIC_ID = 223456789099
STALE = 16 * 60.0
BOB = "https://remote.example/users/bob"
CAROL = "https://other.example/users/carol"
DAVE = "https://third.example/users/dave"
BLOCKS_PATH = "/api/admin/federation/domain-blocks"
FOLLOWERS_PATH = "/api/federation/followers"
STATUS_PATH = "/api/federation/status"


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


def _prepare(tmp_path: Path, **overrides: Any) -> tuple[_Settings, int, int]:
    """A network-mode instance with the admin and two members. The admin and the first member have a password. The members have a private profile."""
    settings = _Settings(db_file=tmp_path / "claire.db", data_dir=tmp_path, **overrides)
    conn = dbm.connect(settings.db_file)
    try:
        dbm.init_db(conn)
        store = accounts_store.AccountStore(conn)
        store.complete_setup(1, username="admin_kim", display_name="Admin Kim")
        store.set_setting("mode", "network", updated_by=1)
        members = []
        for public_id, username in ((ALPHA_PUBLIC_ID, "member_alpha"), (BETA_PUBLIC_ID, "member_beta")):
            cur = conn.execute(
                "INSERT INTO accounts (public_id, username, role, status, display_name, created_at, activated_at, updated_at) VALUES (?, ?, 'member', 'active', ?, 0, 0, 0)",
                (public_id, username, username),
            )
            members.append(int(cur.lastrowid))
        conn.commit()
    finally:
        conn.close()
    auth = auth_db.connect_auth_db(auth_db.auth_db_path(tmp_path))
    try:
        auth_db.set_password(auth, 1, PASSWORD, username="admin_kim")
        auth_db.set_password(auth, members[0], PASSWORD, username="member_alpha")
    finally:
        auth.close()
    return settings, members[0], members[1]


def _session(settings: _Settings, account_id: int, *, authenticated_ago: float = 0.0, scope: str = "owner") -> str:
    conn = dbm.connect_existing(settings.db_file)
    try:
        return accounts_store.create_account_session(conn, account_id=account_id, scope=scope, now=time.time() - authenticated_ago)
    finally:
        conn.close()


def _db(settings: _Settings, fn):
    conn = dbm.connect_existing(settings.db_file)
    try:
        result = fn(conn)
        conn.commit()
        return result
    finally:
        conn.close()


def _set_federation(settings: _Settings, state: str) -> None:
    _db(settings, lambda conn: accounts_store.AccountStore(conn).set_federation_state(state, updated_by=1))


def _set_public(settings: _Settings, account_id: int, value: int) -> None:
    _db(settings, lambda conn: conn.execute("UPDATE accounts SET profile_public=? WHERE id=?", (value, account_id)))


def _follow(settings: _Settings, account_id: int, actor_iri: str, *, at: float) -> int:
    """Store one follower and return its number."""

    def _add(conn) -> int:
        row, _made = federation_store.upsert_follower(conn, account_id=account_id, actor_iri=actor_iri, inbox_iri=actor_iri + "/inbox", shared_inbox_iri=None, salt=accounts_store.AccountStore(conn).id_salt(), now=at)
        return row.public_id

    return _db(settings, _add)


def _followers(settings: _Settings, account_id: int) -> list[str]:
    return _db(settings, lambda conn: [row.actor_iri for row in federation_store.list_followers(conn, account_id, limit=80)])


def _blocks(settings: _Settings) -> list[str]:
    return _db(settings, lambda conn: [block.domain for block in federation_store.list_domain_blocks(conn)])


def _audit_rows(settings: _Settings) -> list[tuple[int, str, dict[str, Any] | None]]:
    conn = auth_db.connect_auth_db(auth_db.auth_db_path(settings.data_dir))
    try:
        return [(int(row[0]), str(row[1]), None if row[2] is None else json.loads(row[2])) for row in conn.execute("SELECT actor_account_id, action, detail_json FROM admin_audit ORDER BY id").fetchall()]
    finally:
        conn.close()


class _Client:
    def __init__(self, client: TestClient) -> None:
        self.client = client

    def request(self, method: str, path: str, *, session: str | None = None, headers: dict[str, str] | None = None, origin: str | None = PUBLIC_URL, **kwargs: Any):
        """Send one request. A request that changes data has the ``Origin`` header of the instance, unless ``origin`` is ``None``."""
        headers = dict(headers or {})
        if session is not None:
            headers["Cookie"] = f"claire_session={session}"
        if method not in {"GET", "HEAD"} and origin is not None:
            headers["Origin"] = origin
        self.client.cookies.clear()
        response = self.client.request(method, path, headers=headers, follow_redirects=False, **kwargs)
        self.client.cookies.clear()
        return response


@pytest.fixture
def env(tmp_path):
    settings, alpha, beta = _prepare(tmp_path)
    app = server.create_app(settings, SimpleNamespace(provider=SimpleNamespace(name="stub")))
    with TestClient(app, base_url=PUBLIC_URL) as tc:
        yield SimpleNamespace(client=_Client(tc), settings=settings, alpha=alpha, beta=beta, admin=_session(settings, 1), alpha_session=_session(settings, alpha), beta_session=_session(settings, beta))


# --- GET /api/federation/status ---


def test_status_shows_the_handle_the_actor_id_and_the_condition_that_fails(env):
    _follow(env.settings, env.alpha, BOB, at=100.0)

    def status() -> dict[str, Any]:
        response = env.client.request("GET", STATUS_PATH, session=env.alpha_session)
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        return response.json()

    off = status()
    _set_federation(env.settings, "on")
    private = status()
    _set_public(env.settings, env.alpha, 1)
    active = status()

    assert off == {
        "federation": "off",
        "handle": "@member_alpha@127.0.0.1:8765",
        "actor_id": f"{PUBLIC_URL}/ap/actors/{ALPHA_PUBLIC_ID}",
        "is_actor": False,
        "actor_state": "federation_off",
        "followers": {"total": 1},
    }
    assert (private["federation"], private["is_actor"], private["actor_state"]) == ("on", False, "profile_not_public")
    assert (active["is_actor"], active["actor_state"]) == (True, "active")


def test_status_counts_only_the_followers_of_the_caller(env):
    _follow(env.settings, env.alpha, BOB, at=100.0)
    _follow(env.settings, env.alpha, CAROL, at=101.0)
    _follow(env.settings, env.beta, BOB, at=102.0)

    alpha = env.client.request("GET", STATUS_PATH, session=env.alpha_session).json()
    beta = env.client.request("GET", STATUS_PATH, session=env.beta_session).json()

    assert alpha["followers"] == {"total": 2}
    assert beta["followers"] == {"total": 1}
    assert beta["handle"] == "@member_beta@127.0.0.1:8765"


def test_account_routes_refuse_a_principal_that_is_not_an_account_owner(env):
    _follow(env.settings, env.alpha, BOB, at=100.0)
    collaborator = _session(env.settings, env.alpha, scope="collaborator")

    for session in (None, collaborator):
        for method, path, kwargs in (("GET", STATUS_PATH, {}), ("GET", FOLLOWERS_PATH, {}), ("DELETE", FOLLOWERS_PATH, {"json": {"actor_iri": BOB}})):
            response = env.client.request(method, path, session=session, **kwargs)
            assert response.status_code in (401, 403, 404), (method, path, response.status_code)
    assert _followers(env.settings, env.alpha) == [BOB]


# --- GET /api/federation/followers ---


def test_follower_list_has_cursor_pages_of_the_caller_only(env):
    numbers = [_follow(env.settings, env.alpha, iri, at=100.0 + index) for index, iri in enumerate((BOB, CAROL, DAVE))]
    _follow(env.settings, env.beta, "https://remote.example/users/erin", at=200.0)

    first = env.client.request("GET", FOLLOWERS_PATH + "?limit=2", session=env.alpha_session)
    assert first.status_code == 200
    page = first.json()
    second = env.client.request("GET", f"{FOLLOWERS_PATH}?limit=2&max_id={page['next_max_id']}", session=env.alpha_session).json()

    assert page["total"] == 3
    assert page["items"] == [
        {"id": str(numbers[2]), "actor_iri": DAVE, "created_at": 102.0},
        {"id": str(numbers[1]), "actor_iri": CAROL, "created_at": 101.0},
    ]
    assert page["next_max_id"] == str(numbers[1])
    assert second["items"] == [{"id": str(numbers[0]), "actor_iri": BOB, "created_at": 100.0}]
    assert second["next_max_id"] is None


@pytest.mark.parametrize("query", ["limit=0", "limit=81", "max_id=abc", "max_id=1&min_id=2"])
def test_follower_list_refuses_a_query_that_is_not_valid(env, query):
    response = env.client.request("GET", f"{FOLLOWERS_PATH}?{query}", session=env.alpha_session)

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_request"


# --- DELETE /api/federation/followers ---


def test_owner_removes_a_follower_of_the_own_account_only(env):
    _follow(env.settings, env.alpha, BOB, at=100.0)
    _follow(env.settings, env.alpha, CAROL, at=101.0)
    _follow(env.settings, env.beta, BOB, at=102.0)

    removed = env.client.request("DELETE", FOLLOWERS_PATH, session=env.alpha_session, json={"actor_iri": BOB})
    # The second member has no follower with this address. The follower of the first member must stay.
    foreign = env.client.request("DELETE", FOLLOWERS_PATH, session=env.beta_session, json={"actor_iri": CAROL})

    assert removed.status_code == 200
    assert removed.json() == {"removed": True, "followers": {"total": 1}}
    assert foreign.status_code == 404
    assert foreign.json()["error"] == "follower_not_found"
    assert _followers(env.settings, env.alpha) == [CAROL]
    assert _followers(env.settings, env.beta) == [BOB]


def test_removal_of_an_unknown_follower_gives_404_and_changes_nothing(env):
    _follow(env.settings, env.alpha, BOB, at=100.0)

    response = env.client.request("DELETE", FOLLOWERS_PATH, session=env.alpha_session, json={"actor_iri": "https://remote.example/users/nobody"})

    assert response.status_code == 404
    assert response.json()["error"] == "follower_not_found"
    assert _followers(env.settings, env.alpha) == [BOB]


@pytest.mark.parametrize("payload", [{}, {"actor_iri": ""}, {"actor_iri": 5}, {"actor_iri": BOB, "account_id": 2}, {"id": "1"}, {"actor_iri": "https://remote.example/" + "a" * 2048}])
def test_removal_refuses_a_body_outside_the_model(env, payload):
    _follow(env.settings, env.alpha, BOB, at=100.0)

    response = env.client.request("DELETE", FOLLOWERS_PATH, session=env.alpha_session, json=payload)

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_request"
    assert _followers(env.settings, env.alpha) == [BOB]


def test_removal_with_a_cookie_needs_the_same_origin(env):
    _follow(env.settings, env.alpha, BOB, at=100.0)

    no_origin = env.client.request("DELETE", FOLLOWERS_PATH, session=env.alpha_session, origin=None, json={"actor_iri": BOB})
    cross_origin = env.client.request("DELETE", FOLLOWERS_PATH, session=env.alpha_session, origin="https://attacker.example", json={"actor_iri": BOB})

    # ``AuthenticationMiddleware`` refuses the two requests before the handler runs.
    assert no_origin.status_code == 403
    assert cross_origin.status_code == 403
    assert _followers(env.settings, env.alpha) == [BOB]


def test_removal_does_not_need_recent_authentication(env):
    _follow(env.settings, env.alpha, BOB, at=100.0)
    stale = _session(env.settings, env.alpha, authenticated_ago=STALE)

    response = env.client.request("DELETE", FOLLOWERS_PATH, session=stale, json={"actor_iri": BOB})

    assert response.status_code == 200
    assert _followers(env.settings, env.alpha) == []


# --- Single mode ---


def test_single_mode_gives_404_for_the_account_routes_and_shows_no_username(tmp_path):
    settings, _alpha, _beta = _prepare(tmp_path)
    _db(settings, lambda conn: conn.execute("DELETE FROM accounts WHERE id != 1"))
    _follow(settings, 1, BOB, at=100.0)
    _db(settings, lambda conn: accounts_store.AccountStore(conn).set_setting("mode", "single", updated_by=1))
    admin = _session(settings, 1)
    app = server.create_app(settings, SimpleNamespace(provider=SimpleNamespace(name="stub")))
    with TestClient(app, base_url=PUBLIC_URL) as tc:
        client = _Client(tc)
        status = client.request("GET", STATUS_PATH, session=admin)
        listed = client.request("GET", FOLLOWERS_PATH, session=admin)
        removed = client.request("DELETE", FOLLOWERS_PATH, session=admin, json={"actor_iri": BOB})
        settings_page = client.request("GET", "/settings/federation", session=admin)
        admin_page = client.request("GET", "/admin/federation", session=admin)

    assert (status.status_code, listed.status_code, removed.status_code) == (404, 404, 404)
    assert _followers(settings, 1) == [BOB]
    assert settings_page.status_code == 200
    assert 'data-federation-mode="unavailable"' in settings_page.text
    assert "admin_kim" not in settings_page.text
    assert admin_page.status_code == 200
    assert "admin_kim" not in admin_page.text


# --- /api/admin/federation/domain-blocks ---


def test_admin_adds_lists_and_removes_a_domain_block_with_audit_records(env):
    added = env.client.request("POST", BLOCKS_PATH, session=env.admin, json={"domain": "Spam.Example.", "note": "  spam wave  "})
    repeated = env.client.request("POST", BLOCKS_PATH, session=env.admin, json={"domain": "spam.example", "note": "a different note"})
    listed = env.client.request("GET", BLOCKS_PATH, session=env.admin)
    blocked_in_store = _db(env.settings, lambda conn: federation_store.blocking_domain(conn, "sub.spam.example"))
    removed = env.client.request("DELETE", BLOCKS_PATH, session=env.admin, json={"domain": "SPAM.example"})
    removed_again = env.client.request("DELETE", BLOCKS_PATH, session=env.admin, json={"domain": "spam.example"})

    assert added.status_code == 201
    assert added.json()["created"] is True
    assert {key: added.json()["domain_block"][key] for key in ("domain", "note")} == {"domain": "spam.example", "note": "spam wave"}
    assert repeated.status_code == 200
    assert repeated.json()["created"] is False
    assert repeated.json()["domain_block"]["note"] == "spam wave"
    assert listed.status_code == 200
    assert listed.headers["cache-control"] == "no-store"
    assert [block["domain"] for block in listed.json()["domain_blocks"]] == ["spam.example"]
    assert set(listed.json()["domain_blocks"][0]) == {"domain", "note", "created_at"}
    assert blocked_in_store == "spam.example"
    assert removed.status_code == 200
    assert removed.json() == {"removed": True, "domain": "spam.example"}
    assert removed_again.status_code == 404
    assert removed_again.json()["error"] == "domain_block_not_found"
    assert _blocks(env.settings) == []
    assert _audit_rows(env.settings) == [
        (1, "federation.domain_block_add", {"domain": "spam.example"}),
        (1, "federation.domain_block_remove", {"domain": "spam.example"}),
    ]


@pytest.mark.parametrize("domain", ["https://spam.example", "spam.example:8443", "spam.example/path", "spam example", "", ".", "a..b", 5, None])
def test_domain_block_refuses_a_domain_that_is_not_valid(env, domain):
    added = env.client.request("POST", BLOCKS_PATH, session=env.admin, json={"domain": domain})
    removed = env.client.request("DELETE", BLOCKS_PATH, session=env.admin, json={"domain": domain})

    assert added.status_code == 400
    assert added.json()["error"] == "invalid_domain"
    assert removed.status_code == 400
    assert removed.json()["error"] == "invalid_domain"
    assert _blocks(env.settings) == []
    assert _audit_rows(env.settings) == []


@pytest.mark.parametrize(
    "method,payload,error",
    [
        ("POST", {}, "invalid_request"),
        ("POST", {"note": "no domain"}, "invalid_request"),
        ("POST", {"domain": "spam.example", "created_by": 2}, "invalid_request"),
        ("POST", {"domain": "spam.example", "note": "n" * 201}, "invalid_note"),
        ("POST", {"domain": "spam.example", "note": 5}, "invalid_note"),
        ("POST", {"domain": "spam.example", "note": "line\nbreak"}, "invalid_note"),
        ("DELETE", {}, "invalid_request"),
        ("DELETE", {"domain": "spam.example", "note": "x"}, "invalid_request"),
    ],
)
def test_domain_block_refuses_a_body_outside_the_model(env, method, payload, error):
    response = env.client.request(method, BLOCKS_PATH, session=env.admin, json=payload)

    assert response.status_code == 400
    assert response.json()["error"] == error
    assert _blocks(env.settings) == []


@pytest.mark.parametrize("method,payload", [("GET", None), ("POST", {"domain": "spam.example"}), ("DELETE", {"domain": "kept.example"})])
def test_member_and_anonymous_principal_cannot_read_or_change_domain_blocks(env, method, payload):
    _db(env.settings, lambda conn: federation_store.add_domain_block(conn, "kept.example", created_by=1, now=50.0))
    kwargs = {} if payload is None else {"json": payload}

    for session in (env.alpha_session, None):
        response = env.client.request(method, BLOCKS_PATH, session=session, **kwargs)
        # The admin routes hide their existence: the answer is the same 404 as for an unknown path.
        assert response.status_code == 404
        assert response.text == "Not Found"
    assert _blocks(env.settings) == ["kept.example"]
    assert _audit_rows(env.settings) == []


def test_bearer_credential_of_the_admin_cannot_change_domain_blocks(env):
    _db(env.settings, lambda conn: federation_store.add_domain_block(conn, "kept.example", created_by=1, now=50.0))
    bearer = {"Authorization": f"Bearer {OWNER_TOKEN}"}

    added = env.client.request("POST", BLOCKS_PATH, headers=bearer, json={"domain": "spam.example"})
    removed = env.client.request("DELETE", BLOCKS_PATH, headers=bearer, json={"domain": "kept.example"})

    assert added.status_code == 403
    assert added.json()["error"] == "step_up_requires_session"
    assert removed.status_code == 403
    assert removed.json()["error"] == "step_up_requires_session"
    assert _blocks(env.settings) == ["kept.example"]
    assert _audit_rows(env.settings) == []


def test_domain_block_change_needs_recent_authentication_and_the_same_origin(env):
    _db(env.settings, lambda conn: federation_store.add_domain_block(conn, "kept.example", created_by=1, now=50.0))
    stale = _session(env.settings, 1, authenticated_ago=STALE)

    for method, payload in (("POST", {"domain": "spam.example"}), ("DELETE", {"domain": "kept.example"})):
        no_step_up = env.client.request(method, BLOCKS_PATH, session=stale, json=payload)
        cross_origin = env.client.request(method, BLOCKS_PATH, session=env.admin, origin="https://attacker.example", json=payload)
        no_origin = env.client.request(method, BLOCKS_PATH, session=env.admin, origin=None, json=payload)

        assert no_step_up.status_code == 401
        assert no_step_up.json() == {"error": "reauth_required", "reauth": "/api/auth/reauth"}
        assert cross_origin.status_code == 403
        assert no_origin.status_code == 403
    # The list has no step-up condition.
    assert env.client.request("GET", BLOCKS_PATH, session=stale).status_code == 200
    assert _blocks(env.settings) == ["kept.example"]
    assert _audit_rows(env.settings) == []


# --- Console sections ---


def test_settings_section_shows_the_handle_and_the_follower_count_and_no_admin_markup(env):
    _set_federation(env.settings, "on")
    _set_public(env.settings, env.alpha, 1)
    _follow(env.settings, env.alpha, BOB, at=100.0)
    _follow(env.settings, env.alpha, CAROL, at=101.0)

    page = env.client.request("GET", "/settings/federation", session=env.alpha_session)

    assert page.status_code == 200
    body = page.text
    assert 'data-active-section="federation"' in body
    assert '<dd data-federation-field="handle">@member_alpha@127.0.0.1:8765</dd>' in body
    assert f'<dd data-federation-field="actor_id">{PUBLIC_URL}/ap/actors/{ALPHA_PUBLIC_ID}</dd>' in body
    assert '<dd data-federation-field="followers_total">2</dd>' in body
    assert 'data-actor-state="active"' in body
    assert "data-followers-tbody" in body
    assert '<script src="/static/js/console_account.js" defer></script>' in body
    assert 'href="/settings/federation"' in body
    # A member response has no admin link and no admin script.
    assert "/admin/" not in body
    assert "console_admin.js" not in body
    # The page has no inline event handler (CSP).
    assert "onclick=" not in body


def test_admin_section_shows_the_switch_and_the_blocks_and_is_hidden_from_a_member(env):
    _db(env.settings, lambda conn: federation_store.add_domain_block(conn, "spam.example", created_by=1, note="<b>wave</b>", now=50.0))

    admin_page = env.client.request("GET", "/admin/federation", session=env.admin)
    member_page = env.client.request("GET", "/admin/federation", session=env.alpha_session)
    anonymous_page = env.client.request("GET", "/admin/federation")
    admin_settings = env.client.request("GET", "/settings/federation", session=env.admin)

    assert admin_page.status_code == 200
    body = admin_page.text
    assert 'data-active-area="admin"' in body
    assert 'data-active-section="federation"' in body
    assert 'id="admin-federation-switch" data-federation-state="off"' in body
    assert 'data-federation-switch="on"' in body
    assert 'id="admin-federation-readiness-tbody"' in body
    assert 'id="admin-federation-heartbeat"' in body
    assert '<tr data-domain-block="spam.example">' in body
    assert "&lt;b&gt;wave&lt;/b&gt;" in body
    assert "<b>wave</b>" not in body
    assert '<script src="/static/js/console_admin.js" defer></script>' in body
    assert "onclick=" not in body
    assert member_page.status_code == 404
    assert anonymous_page.status_code == 404
    # The admin has the two sections in the navigation.
    assert 'href="/admin/federation"' in admin_settings.text
    assert 'href="/settings/federation"' in admin_settings.text


# --- End-to-end: the two console sections in a real browser ---


def test_e2e_owner_removes_a_follower_and_admin_changes_a_domain_block(tmp_path):
    sync_api = pytest.importorskip("playwright.sync_api")
    uvicorn = pytest.importorskip("uvicorn")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = int(probe.getsockname()[1])
    base = f"http://127.0.0.1:{port}"
    settings, alpha, _beta = _prepare(tmp_path, public_url=base, fqdn=f"127.0.0.1:{port}")
    _set_federation(settings, "on")
    _set_public(settings, alpha, 1)
    _follow(settings, alpha, BOB, at=100.0)
    _follow(settings, alpha, CAROL, at=101.0)
    owner = _session(settings, alpha)
    # The admin session is old. Thus the first change opens the step-up dialog.
    stale_admin = _session(settings, 1, authenticated_ago=STALE)
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
                # The owner sees the handle and the follower count, and removes one follower.
                ctx = browser.new_context()
                ctx.add_cookies([{"name": "claire_session", "value": owner, "url": base}])
                page = ctx.new_page()
                page.on("dialog", lambda dialog: dialog.accept())
                page.goto(f"{base}/settings/federation")
                assert page.locator("[data-federation-field='handle']").inner_text() == f"@member_alpha@127.0.0.1:{port}"
                assert page.locator("[data-federation-field='actor_state']").inner_text().startswith("연합 중")
                assert page.locator("[data-federation-field='followers_total']").inner_text() == "2"
                page.locator("tr[data-follower]").nth(1).wait_for()
                assert page.locator("tr[data-follower]").count() == 2
                with page.expect_response(lambda r: r.url.endswith(FOLLOWERS_PATH) and r.request.method == "DELETE") as deleted:
                    page.locator(f"[data-follower-remove='{BOB}']").click()
                assert deleted.value.status == 200
                page.locator(f"[data-follower-remove='{BOB}']").wait_for(state="detached")
                page.locator("[data-federation-field='followers_total']:text-is('1')").wait_for()
                assert page.locator("tr[data-follower]").count() == 1
                assert _followers(settings, alpha) == [CAROL]
                ctx.close()

                # The admin adds a domain block after the step-up dialog, and removes it.
                ctx = browser.new_context()
                ctx.add_cookies([{"name": "claire_session", "value": stale_admin, "url": base}])
                page = ctx.new_page()
                page.on("dialog", lambda dialog: dialog.accept())
                page.goto(f"{base}/admin/federation")
                page.locator("tr[data-readiness-item='GF1']").wait_for()
                assert page.locator("tr[data-readiness-item='GF1']").get_attribute("data-readiness-passed") == "1"
                assert page.locator("#admin-federation-heartbeat").get_attribute("data-heartbeat") == "absent"
                form = page.locator("#admin-federation-domain-block-form")
                form.locator("input[name='domain']").fill("Spam.Example")
                form.locator("input[name='note']").fill("spam wave")
                form.locator("button[type='submit']").click()
                modal = page.locator("#console-reauth-modal")
                modal.wait_for(state="visible")
                assert _blocks(settings) == []
                modal.locator("#console-reauth-password").fill(PASSWORD)
                with page.expect_response(lambda r: r.url.endswith(BLOCKS_PATH) and r.request.method == "POST") as posted:
                    modal.locator("#console-reauth-form button[type='submit']").click()
                assert posted.value.status == 201
                row = page.locator("tr[data-domain-block='spam.example']")
                row.wait_for()
                assert "spam wave" in row.inner_text()
                assert _blocks(settings) == ["spam.example"]
                with page.expect_response(lambda r: r.url.endswith(BLOCKS_PATH) and r.request.method == "DELETE") as deleted:
                    row.locator("[data-domain-block-remove]").click()
                assert deleted.value.status == 200
                row.wait_for(state="detached")
                page.locator("tr[data-domain-block-empty]").wait_for()
                assert _blocks(settings) == []
                ctx.close()
            finally:
                browser.close()
    finally:
        srv.should_exit = True
        thread.join(timeout=15)
    assert [action for _actor, action, _detail in _audit_rows(settings) if action.startswith("federation.")] == ["federation.domain_block_add", "federation.domain_block_remove"]
