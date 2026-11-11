"""Publishing routes and the outbox store (Fedify sidecar design Sections 8.3, 9.1 to 9.3, 10.1, unit FP3-1)."""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from starlette.testclient import TestClient

from claire.api import server
from claire.store import accounts as accounts_store
from claire.store import db as dbm
from claire.store import federation as fed
from claire.store.secrets import reset_secret_service

PUBLIC_URL = "http://127.0.0.1:8765"
ALPHA_PUBLIC_ID = 116412345678901234
BETA_PUBLIC_ID = 216412345678901234
ALPHA_THEME = 316412345678901234
ALPHA_PRIVATE_THEME = 316412345678901235
ALPHA_OWNER_ONLY_THEME = 316412345678901236
BETA_THEME = 416412345678901234
SRC = Path(__file__).resolve().parents[1] / "src" / "claire"


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
    fqdn: str = "127.0.0.1:8765"
    multi_theme: bool = True
    telemetry_retention_days: int = 30


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    for key in list(os.environ):
        if key.startswith(("CLAIRE_", "CB_")):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("CLAIRE_SECRET_KEY", "55" * 32)
    reset_secret_service()
    yield
    reset_secret_service()


def _add_theme(conn, theme_id: int, number: int, account_id: int, *, is_public: int = 1, collaborator: int = 1) -> None:
    conn.execute(
        "INSERT INTO themes (id, seq, label, is_default, is_public, is_collaborator_accessible, created_at, updated_at, account_id, number) VALUES (?, ?, ?, 0, ?, ?, 1, 1, ?, ?)",
        (theme_id, theme_id, f"theme-{theme_id}", is_public, collaborator, account_id, number),
    )


def _add_document(conn, document_id: str, theme_id: int, *, detail: str = "# Title\n\nBody text.", hidden: int = 0) -> None:
    conn.execute("INSERT INTO documents (id, theme_id, title, detail, detail_format, hidden, fetched_at) VALUES (?, ?, ?, ?, 'md', ?, 1)", (document_id, theme_id, f"Title of {document_id}", detail, hidden))


@pytest.fixture
def env(tmp_path):
    """A network-mode instance with federation on. ``alpha`` is an actor with a public theme, a private theme, and a theme that a collaborator cannot use. ``beta`` is an actor with one public theme."""
    settings = _Settings(db_file=tmp_path / "claire.db", data_dir=tmp_path)
    conn = dbm.connect(settings.db_file)
    try:
        dbm.init_db(conn)
        store = accounts_store.AccountStore(conn)
        store.complete_setup(1, username="admin_kim", display_name="Admin Kim")
        store.set_setting("mode", "network", updated_by=1)
        store.set_federation_state("on", updated_by=1)
        members = []
        for public_id, username in ((ALPHA_PUBLIC_ID, "member_alpha"), (BETA_PUBLIC_ID, "member_beta")):
            cur = conn.execute("INSERT INTO accounts (public_id, username, role, status, display_name, profile_public, created_at, activated_at, updated_at) VALUES (?, ?, 'member', 'active', ?, 1, 0, 0, 0)", (public_id, username, username))
            members.append(int(cur.lastrowid))
        alpha, beta = members
        _add_theme(conn, 11, ALPHA_THEME, alpha)
        _add_theme(conn, 12, ALPHA_PRIVATE_THEME, alpha, is_public=0)
        _add_theme(conn, 13, ALPHA_OWNER_ONLY_THEME, alpha, collaborator=0)
        _add_theme(conn, 21, BETA_THEME, beta)
        _add_document(conn, "doc-a", 11)
        _add_document(conn, "doc-hidden", 11, hidden=1)
        _add_document(conn, "doc-empty", 11, detail="  ")
        _add_document(conn, "doc-private", 12)
        _add_document(conn, "doc-owner-only", 13)
        _add_document(conn, "doc-b", 21)
        conn.commit()
    finally:
        conn.close()

    def session(account_id: int, scope: str = "owner") -> str:
        c = dbm.connect_existing(settings.db_file)
        try:
            return accounts_store.create_account_session(c, account_id=account_id, scope=scope, now=time.time())
        finally:
            c.close()

    app = server.create_app(settings, SimpleNamespace(provider=SimpleNamespace(name="stub")))
    with TestClient(app, base_url=PUBLIC_URL) as tc:

        def post(path: str, body: Any, *, session_token: str | None, origin: str | None = PUBLIC_URL, headers: dict[str, str] | None = None):
            headers = dict(headers or {})
            if session_token is not None:
                headers["Cookie"] = f"claire_session={session_token}"
            if origin is not None:
                headers["Origin"] = origin
            tc.cookies.clear()
            response = tc.post(path, json=body, headers=headers, follow_redirects=False)
            tc.cookies.clear()
            return response

        yield SimpleNamespace(settings=settings, post=post, session=session, alpha=alpha, beta=beta, alpha_session=session(alpha), beta_session=session(beta), admin_session=session(1), client=tc)


def _rows(settings: _Settings, sql: str, params: tuple = ()) -> list[tuple]:
    conn = dbm.connect_existing(settings.db_file, readonly=True)
    try:
        return [tuple(r) for r in conn.execute(sql, params)]
    finally:
        conn.close()


def _objects(settings: _Settings) -> list[tuple]:
    return _rows(settings, "SELECT account_id, theme_id, document_id, published_by_account_id FROM federation_objects ORDER BY public_id")


def _tasks(settings: _Settings) -> list[tuple]:
    return _rows(settings, "SELECT account_id, kind, object_public_id, status FROM federation_outbox ORDER BY id")


def _publish(env, document_id: str = "doc-a", theme: int = ALPHA_THEME, **kwargs):
    kwargs.setdefault("session_token", env.alpha_session)
    return env.post("/api/federation/publish", {"theme": str(theme), "document_id": document_id}, **kwargs)


# --- Schema (I/O) ---


def test_v17_database_migrates_and_the_migration_is_idempotent(tmp_path, monkeypatch):
    path = tmp_path / "claire.db"
    with monkeypatch.context() as m:
        m.setattr(dbm, "SCHEMA_VERSION", 17)
        m.setattr(dbm, "_migrate_v18_federation_publishing", lambda conn: None)
        conn = dbm.connect(path)
        dbm.init_db(conn)
        assert dbm.stored_schema_version(conn) == 17
        conn.close()
    conn = dbm.connect(path)
    try:
        tables = lambda: {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}  # noqa: E731
        assert not {"federation_objects", "federation_outbox"} & tables()
        dbm.init_db(conn)
        assert dbm.SCHEMA_VERSION == 18 and dbm.require_current_schema(conn) == 18
        assert {"federation_objects", "federation_outbox"} <= tables()
        salt = accounts_store.AccountStore(conn).id_salt()
        _add_theme(conn, 11, ALPHA_THEME, 1)
        obj, _task, _made = fed.publish_document(conn, account_id=1, theme_id=11, document_id="d", published_by_account_id=1, salt=salt, now=100.0)
        fed.retract_object(conn, obj, now=101.0)
        fed.publish_document(conn, account_id=1, theme_id=11, document_id="e", published_by_account_id=1, salt=salt, now=102.0)
        conn.commit()
        first = list(conn.iterdump())
        dbm.init_db(conn)
        dbm.init_db(conn)
        assert list(conn.iterdump()) == first
        # The outbox has no default account and no default status.
        columns = {r["name"]: r for r in conn.execute("PRAGMA table_info(federation_outbox)")}
        assert columns["account_id"]["notnull"] == 1 and columns["account_id"]["dflt_value"] is None
        for sql in ("INSERT INTO federation_outbox (kind, status, created_at) VALUES ('create', 'pending', 0)", "INSERT INTO federation_outbox (account_id, kind, status, created_at) VALUES (1, 'announce', 'pending', 0)", "INSERT INTO federation_outbox (account_id, kind, status, created_at) VALUES (1, 'create', 'done', 0)"):
            with pytest.raises(Exception, match="constraint"):
                conn.execute(sql)
    finally:
        conn.close()


def test_no_federation_module_uses_the_admin_account_as_a_default():
    """Design Section 8.3 item 5: no federation function has a default account."""
    modules = [SRC / "store" / "federation.py", SRC / "api" / "federation_routes.py", SRC / "api" / "accounts_routes" / "federation.py", SRC / "api" / "accounts_routes" / "console_federation_panels.py"]
    for module in modules:
        text = module.read_text(encoding="utf-8")
        assert "ADMIN_ACCOUNT_ID" not in text, module.name
        assert not re.search(r"account_id\s*(:\s*[\w| ]+)?\s*=\s*1\b", text), module.name
    with pytest.raises(TypeError):
        fed.add_outbox_task(None, kind="create", object_public_id=None, now=0.0)  # type: ignore[call-arg]


# --- Outbox store (I/O) ---


def test_lease_gives_ready_tasks_one_time_until_the_lease_ends_and_ack_is_final(tmp_path):
    conn = dbm.connect(tmp_path / "claire.db")
    try:
        dbm.init_db(conn)
        first = fed.add_outbox_task(conn, account_id=5, kind="create", object_public_id=70, now=100.0)
        second = fed.add_outbox_task(conn, account_id=5, kind="update", object_public_id=70, now=101.0)
        other = fed.add_outbox_task(conn, account_id=6, kind="create", object_public_id=71, now=102.0)
        lease = lambda now, accounts=(5, 6), limit=10: [t.id for t in fed.lease_outbox_tasks(conn, account_ids=list(accounts), now=now, lease_seconds=60.0, limit=limit)]  # noqa: E731

        assert lease(200.0, limit=1) == [first.id]
        assert lease(200.0, accounts=(5,)) == [second.id]
        assert lease(210.0, accounts=()) == []
        assert lease(210.0) == [other.id]
        assert lease(259.0) == []
        assert fed.count_pending_tasks(conn, 5) == 2

        # A task that was not acknowledged is ready again after its lease.
        assert fed.ack_outbox_task(conn, first.id, result="handed_off", error_code=None, now=230.0).status == "handed_off"
        assert lease(260.0) == [second.id]
        assert lease(270.0) == [other.id]

        done = fed.ack_outbox_task(conn, first.id, result="rejected", error_code="late", now=300.0)
        assert (done.status, done.error_code, done.handed_off_at, done.lease_until) == ("handed_off", None, 230.0, None)
        refused = fed.ack_outbox_task(conn, second.id, result="rejected", error_code="object_gone", now=300.0)
        assert (refused.status, refused.error_code, refused.handed_off_at) == ("rejected", "object_gone", None)
        assert fed.ack_outbox_task(conn, 9999, result="handed_off", error_code=None, now=300.0) is None
        assert fed.count_pending_tasks(conn, 5) == 0 and lease(1000.0) == [other.id]
    finally:
        conn.close()


# --- POST /api/federation/publish ---


def test_publish_makes_the_object_and_a_create_task_and_a_second_publish_makes_an_update_task(env):
    first = _publish(env)
    assert first.status_code == 202, first.text
    body = first.json()
    assert body["created"] is True and body["task"]["kind"] == "create"
    assert body["object"]["url"] == f"{PUBLIC_URL}/ap/objects/{body['object']['id']}"
    object_id = int(body["object"]["id"])
    assert _objects(env.settings) == [(env.alpha, 11, "doc-a", env.alpha)]
    assert _tasks(env.settings) == [(env.alpha, "create", object_id, "pending")]
    # The document has a public share page for the ``url`` of the object.
    assert len(_rows(env.settings, "SELECT token FROM doc_shares WHERE document_id='doc-a' AND theme_id=11")) == 1

    second = _publish(env)
    assert second.status_code == 202 and second.json()["created"] is False
    assert (second.json()["object"]["id"], second.json()["task"]["kind"]) == (str(object_id), "update")
    assert second.json()["object"]["updated_at"] >= second.json()["object"]["published_at"]
    assert _tasks(env.settings) == [(env.alpha, "create", object_id, "pending"), (env.alpha, "update", object_id, "pending")]
    assert len(_objects(env.settings)) == 1

    status = env.client.get("/api/federation/status", headers={"Cookie": f"claire_session={env.alpha_session}"})
    assert status.json()["pending_tasks"] == 2


def test_object_row_and_task_row_are_in_one_transaction(env, monkeypatch):
    def fail(*_args, **_kwargs):
        raise RuntimeError("task insert failed")

    with monkeypatch.context() as m:
        m.setattr(fed, "add_outbox_task", fail)
        assert _publish(env).status_code == 500
    assert _objects(env.settings) == [] and _tasks(env.settings) == []

    object_id = _publish(env).json()["object"]["id"]
    with monkeypatch.context() as m:
        m.setattr(fed, "add_outbox_task", fail)
        assert env.post("/api/federation/retract", {"object_id": object_id}, session_token=env.alpha_session).status_code == 500
    # The retract changed nothing: the object stays and there is no tombstone.
    assert len(_objects(env.settings)) == 1
    assert _rows(env.settings, "SELECT COUNT(*) FROM federation_tombstones") == [(0,)]


@pytest.mark.parametrize(
    "document_id, theme, status, error",
    [
        ("doc-private", ALPHA_PRIVATE_THEME, 409, "theme_not_public"),
        ("doc-hidden", ALPHA_THEME, 409, "document_not_public"),
        ("doc-empty", ALPHA_THEME, 409, "document_has_no_body"),
        ("no-such-doc", ALPHA_THEME, 404, "document_not_found"),
        ("doc-b", ALPHA_THEME, 404, "document_not_found"),
        ("doc-b", BETA_THEME, 404, "theme_not_found"),
        ("doc-a", 999999999999999999, 404, "theme_not_found"),
    ],
)
def test_only_a_document_of_a_public_theme_of_the_own_account_can_be_published(env, document_id, theme, status, error):
    response = _publish(env, document_id, theme)
    assert (response.status_code, response.json()["error"]) == (status, error)
    assert _objects(env.settings) == [] and _tasks(env.settings) == []


@pytest.mark.parametrize("change, state", [("UPDATE accounts SET profile_public=0 WHERE username='member_alpha'", "profile_not_public"), ("UPDATE accounts SET status='suspended' WHERE username='member_alpha'", "account_not_active"), ("UPDATE instance_settings SET value='off' WHERE key='federation'", "federation_off")])
def test_an_account_that_is_not_an_actor_cannot_publish(env, change, state):
    conn = dbm.connect_existing(env.settings.db_file)
    conn.execute(change)
    conn.commit()
    conn.close()
    session = env.alpha_session
    response = _publish(env, session_token=session)
    if state == "account_not_active":
        # A suspended account has no valid session.
        assert response.status_code in (401, 403, 404, 409)
    else:
        assert (response.status_code, response.json()) == (409, {"error": "not_an_actor", "actor_state": state})
    assert _objects(env.settings) == [] and _tasks(env.settings) == []


def test_each_task_has_the_account_of_the_caller_and_the_admin_cannot_publish_for_a_member(env):
    assert _publish(env, "doc-b", BETA_THEME, session_token=env.beta_session).status_code == 202
    assert _publish(env).status_code == 202
    # K5: the admin is not a content superuser.
    admin = _publish(env, session_token=env.admin_session)
    assert (admin.status_code, admin.json()) == (404, {"error": "theme_not_found"})
    assert [row[0] for row in _tasks(env.settings)] == [env.beta, env.alpha]
    assert [(row[0], row[3]) for row in _objects(env.settings)] == [(env.beta, env.beta), (env.alpha, env.alpha)]


def test_collaborator_can_publish_only_in_a_theme_that_it_can_use_and_readonly_cannot_publish(env):
    collaborator = env.session(env.alpha, "collaborator")
    assert _publish(env, session_token=collaborator).status_code == 202
    hidden = _publish(env, "doc-owner-only", ALPHA_OWNER_ONLY_THEME, session_token=collaborator)
    assert (hidden.status_code, hidden.json()) == (404, {"error": "theme_not_found"})
    assert _publish(env, "doc-owner-only", ALPHA_OWNER_ONLY_THEME).status_code == 202

    readonly = env.session(env.alpha, "readonly")
    for token in (readonly, None):
        refused = _publish(env, session_token=token)
        assert refused.status_code in (401, 403, 404), refused.status_code
    assert len(_tasks(env.settings)) == 2


def test_publish_with_a_cookie_needs_the_same_origin(env):
    assert _publish(env, origin="https://evil.example").status_code == 403
    assert _objects(env.settings) == []


@pytest.mark.parametrize("body", [{}, {"theme": str(ALPHA_THEME)}, {"theme": ALPHA_THEME, "document_id": "doc-a"}, {"theme": "0316", "document_id": "doc-a"}, {"theme": str(ALPHA_THEME), "document_id": ""}, {"theme": str(ALPHA_THEME), "document_id": "x" * 201}, {"theme": str(ALPHA_THEME), "document_id": "doc-a", "account_id": 1}])
def test_publish_refuses_a_body_outside_the_model(env, body):
    response = env.post("/api/federation/publish", body, session_token=env.alpha_session)
    assert (response.status_code, response.json()["error"]) == (400, "invalid_request")
    assert _tasks(env.settings) == []


# --- POST /api/federation/retract ---


def test_retract_deletes_the_object_writes_a_tombstone_and_makes_a_delete_task(env):
    object_id = _publish(env).json()["object"]["id"]
    other = _publish(env, "doc-b", BETA_THEME, session_token=env.beta_session).json()["object"]["id"]

    # An object of a different account gives the same answer as no object.
    for body in ({"object_id": other}, {"object_id": "999999999999999999"}):
        refused = env.post("/api/federation/retract", body, session_token=env.alpha_session)
        assert (refused.status_code, refused.json()) == (404, {"error": "object_not_found"})
    assert env.post("/api/federation/retract", {"object_id": object_id}, session_token=env.admin_session).status_code == 404
    assert env.post("/api/federation/retract", {"object_id": object_id}, session_token=env.session(env.alpha, "readonly")).status_code in (401, 403, 404)
    assert len(_objects(env.settings)) == 2

    done = env.post("/api/federation/retract", {"object_id": object_id}, session_token=env.alpha_session)
    assert done.status_code == 202, done.text
    assert (done.json()["retracted"], done.json()["object_id"], done.json()["task"]["kind"]) == (True, object_id, "delete")
    assert _objects(env.settings) == [(env.beta, 21, "doc-b", env.beta)]
    assert _rows(env.settings, "SELECT public_id, kind FROM federation_tombstones") == [(int(object_id), "object")]
    assert _tasks(env.settings)[-1] == (env.alpha, "delete", int(object_id), "pending")
    assert env.post("/api/federation/retract", {"object_id": object_id}, session_token=env.alpha_session).status_code == 404

    # A new publish of the same document makes a new object ID. The old ID stays a tombstone.
    again = _publish(env).json()
    assert again["created"] is True and again["object"]["id"] != object_id


@pytest.mark.parametrize("body", [{}, {"object_id": 5}, {"object_id": "abc"}, {"object_id": "01"}, {"object_id": "5", "theme": "1"}])
def test_retract_refuses_a_body_outside_the_model(env, body):
    response = env.post("/api/federation/retract", body, session_token=env.alpha_session)
    assert (response.status_code, response.json()["error"]) == (400, "invalid_request")


def test_single_mode_has_no_publishing_routes(env):
    conn = dbm.connect_existing(env.settings.db_file)
    store = accounts_store.AccountStore(conn)
    store.set_federation_state("off", updated_by=1)
    store.set_setting("mode", "single", updated_by=1)
    conn.close()
    for path, body in (("/api/federation/publish", {"theme": str(ALPHA_THEME), "document_id": "doc-a"}), ("/api/federation/retract", {"object_id": "5"})):
        assert env.post(path, body, session_token=env.admin_session).status_code == 404
    assert _tasks(env.settings) == []


# --- End to end: the native actor in the dialog of /syndication, in a real browser (unit FP3-5) ---


def test_e2e_owner_publishes_a_document_from_the_syndication_dialog(tmp_path):
    import socket
    import threading

    sync_api = pytest.importorskip("playwright.sync_api")
    uvicorn = pytest.importorskip("uvicorn")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = int(probe.getsockname()[1])
    base = f"http://127.0.0.1:{port}"
    from claire.config import Settings

    (tmp_path / "vault").mkdir()
    # The page reads the document list, so this test needs the full settings object.
    settings = Settings(CLAIRE_DATA_DIR=str(tmp_path), CLAIRE_DB_PATH=str(tmp_path / "claire.db"), CLAIRE_VAULT_PATH=str(tmp_path / "vault"), CLAIRE_PROVIDER="mock", CLAIRE_ENVIRONMENT="development", CLAIRE_PUBLIC_URL=base, CLAIRE_FQDN=f"127.0.0.1:{port}", CLAIRE_INJECT_TOKEN="owner-" + "o" * 32, CLAIRE_ANONYMOUS_READONLY=False, CLAIRE_MULTI_THEME=True)
    conn = dbm.connect(settings.db_file)
    try:
        dbm.init_db(conn)
        store = accounts_store.AccountStore(conn)
        store.complete_setup(1, username="admin_kim", display_name="Admin Kim")
        store.set_setting("mode", "network", updated_by=1)
        store.set_federation_state("on", updated_by=1)
        alpha = int(conn.execute("INSERT INTO accounts (public_id, username, role, status, display_name, profile_public, created_at, activated_at, updated_at) VALUES (?, 'member_alpha', 'member', 'active', 'Alpha', 1, 0, 0, 0)", (ALPHA_PUBLIC_ID,)).lastrowid)
        conn.execute("INSERT INTO themes (id, seq, label, is_default, is_public, is_collaborator_accessible, created_at, updated_at, account_id, number) VALUES (11, 11, 'alpha public', 1, 1, 0, 1, 1, ?, ?)", (alpha, ALPHA_THEME))
        conn.execute("INSERT INTO themes (id, seq, label, is_default, is_public, is_collaborator_accessible, created_at, updated_at, account_id, number) VALUES (12, 12, 'alpha private', 0, 0, 0, 1, 1, ?, ?)", (alpha, ALPHA_PRIVATE_THEME))
        _add_document(conn, "doc-a", 11)
        _add_document(conn, "doc-private", 12)
        conn.commit()
        owner = accounts_store.create_account_session(conn, account_id=alpha, scope="owner", now=time.time())
    finally:
        conn.close()

    def rows(sql: str) -> list[tuple]:
        c = dbm.connect_existing(settings.db_file, readonly=True)
        try:
            return [tuple(r) for r in c.execute(sql)]
        finally:
            c.close()

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
                ctx.add_cookies([{"name": "claire_session", "value": owner, "url": base}])
                page = ctx.new_page()
                page.goto(f"{base}/syndication?theme={ALPHA_THEME}")
                page.locator("[data-quick-dispatch='doc-a']").first.click()
                native = page.locator("#dispatch-native")
                native.wait_for(state="visible")
                # The native actor is one more target of the dialog, with the handle of the account and the notice about remote copies.
                assert native.locator("[data-native-handle]").inner_text() == f"@member_alpha@127.0.0.1:{port}"
                assert "보장되지 않습니다" in native.inner_text()
                with page.expect_response(lambda r: r.url.endswith("/api/federation/publish")) as published:
                    native.locator("#btn-native-publish").click()
                assert published.value.status == 202
                assert published.value.json()["created"] is True
                page.locator("#modal-dispatch").wait_for(state="hidden")
                assert rows("SELECT account_id, theme_id, document_id FROM federation_objects") == [(alpha, 11, "doc-a")]
                assert rows("SELECT account_id, kind, status FROM federation_outbox") == [(alpha, "create", "pending")]

                # A second publish from the dialog is an update of the same object.
                page.locator("[data-quick-dispatch='doc-a']").first.click()
                with page.expect_response(lambda r: r.url.endswith("/api/federation/publish")) as published:
                    page.locator("#btn-native-publish").click()
                assert published.value.json()["created"] is False
                assert [r[1] for r in rows("SELECT account_id, kind, status FROM federation_outbox")] == ["create", "update"]

                # A document of a private theme cannot go to the native actor.
                page.goto(f"{base}/syndication?theme={ALPHA_PRIVATE_THEME}")
                page.locator("[data-quick-dispatch='doc-private']").first.click()
                page.locator("#dispatch-native").wait_for(state="visible")
                assert page.locator("#btn-native-publish").is_disabled()
                ctx.close()

                # An account that is not an actor sees no native target.
                c = dbm.connect_existing(settings.db_file)
                try:
                    accounts_store.AccountStore(c).update_profile_public(alpha, 0)
                finally:
                    c.close()
                ctx = browser.new_context()
                ctx.add_cookies([{"name": "claire_session", "value": owner, "url": base}])
                page = ctx.new_page()
                page.goto(f"{base}/syndication?theme={ALPHA_THEME}")
                page.locator("[data-quick-dispatch='doc-a']").first.click()
                page.locator("#btn-confirm-dispatch").wait_for(state="visible")
                assert page.locator("#dispatch-native").count() == 0
                ctx.close()
            finally:
                browser.close()
    finally:
        srv.should_exit = True
        thread.join(timeout=10)
