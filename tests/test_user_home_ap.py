"""Tests for User Home ActivityPub profile, account summary, always-public KB summary, and conditional Proposal B layout."""

from __future__ import annotations

import os
import secrets
import socket
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from starlette.testclient import TestClient

from claire.accounts.user_home import build_user_home_profile, build_user_home_timeline
from claire.api import federation_routes, server
from claire.api.federation_internal import INTERNAL_PREFIX, SIGNATURE_HEADER, TIMESTAMP_HEADER, create_internal_federation_app, ensure_service_key, load_service_key, service_key_path, sign_request
from claire.core.config import Settings
from claire.store import accounts as accounts_store
from claire.store import auth_db, client_db
from claire.store import db as dbm
from claire.store import federation as federation_store
from claire.store import federation_notes as notes_store
from claire.store.knowledge_base import get_kb_manager
from claire.store.secrets import reset_secret_service

PUBLIC_URL = "http://127.0.0.1:8766"
HANDLE_HOST = "127.0.0.1:8766"
OWNER_TOKEN = "owner-" + "o" * 32
PASSWORD = "Correct-Horse-Battery-123!"
MEMBER_PUBLIC_ID = 323456789012


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    for key in list(os.environ):
        if key.startswith(("CLAIRE_", "CB_")):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("CLAIRE_SECRET_KEY", "55" * 32)
    reset_secret_service()
    yield
    reset_secret_service()


def _prepare(tmp_path: Path, *, is_public: bool = True, public_url: str = PUBLIC_URL, fqdn: str = HANDLE_HOST) -> tuple[Settings, int]:
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
    )
    conn = dbm.connect(settings.db_file)
    try:
        dbm.init_db(conn)
        store = accounts_store.AccountStore(conn)
        store.complete_setup(1, username="admin_kim", display_name="Admin Kim")
        store.set_setting("mode", "network", updated_by=1)
        cur = conn.execute(
            "INSERT INTO accounts (public_id, username, role, status, display_name, summary, profile_public, created_at, activated_at, updated_at) VALUES (?, 'alice_home', 'member', 'active', 'Alice Home', 'Exploring knowledge graphs.', 1, 100.0, 100.0, 100.0)",
            (MEMBER_PUBLIC_ID,),
        )
        conn.commit()
        assert cur.lastrowid is not None
        member_id = int(cur.lastrowid)
    finally:
        conn.close()

    auth = auth_db.connect_auth_db(auth_db.auth_db_path(tmp_path))
    try:
        auth_db.set_password(auth, member_id, PASSWORD, username="alice_home")
    finally:
        auth.close()

    kb = get_kb_manager(settings).define_kb("Alice Knowledge Base", account_id=member_id, is_personal=True)
    conn = dbm.connect_existing(settings.db_file)
    try:
        conn.execute("UPDATE knowledge_bases SET is_public=? WHERE id=?", (1 if is_public else 0, kb.id))
        conn.execute(
            "INSERT INTO documents (id, kb_id, title, detail, detail_format, source_type, hidden, fetched_at) VALUES ('doc-uh-1', ?, 'Alpha Graph Note', 'Body paragraph.', 'adoc', 'web', 0, 1700000100.0)",
            (kb.id,),
        )
        conn.execute(
            "INSERT INTO documents (id, kb_id, title, detail, detail_format, source_type, hidden, fetched_at) VALUES ('doc-uh-2', ?, 'Beta Bridge Study', 'Second body.', 'adoc', 'note', 0, 1700000200.0)",
            (kb.id,),
        )
        conn.execute(
            "INSERT INTO entities (id, kb_id, type, name, norm_name, sources, created_at) VALUES ('ent-hub', ?, 'Concept', 'Hub Entity', 'hub entity', '[\"doc-uh-1\"]', 1700000150.0), ('ent-bridge', ?, 'Concept', 'Bridge Entity', 'bridge entity', '[\"doc-uh-1\",\"doc-uh-2\"]', 1700000250.0)",
            (kb.id, kb.id),
        )
        conn.execute(
            "INSERT INTO relations (id, kb_id, type, source_id, target_id, sources, confidence, created_at) VALUES ('rel-1', ?, 'related_to', 'ent-hub', 'ent-bridge', '[\"doc-uh-1\"]', 0.9, 1700000300.0)",
            (kb.id,),
        )
        federation_store.publish_document(conn, account_id=member_id, kb_id=kb.id, document_id="doc-uh-1", published_by_account_id=member_id, salt=b"salt", now=1700000500.0)
        conn.commit()
    finally:
        conn.close()

    client_conn = client_db.connect_client_db(client_db.client_db_path(settings.db_file))
    try:
        client_db.init_client_db(client_conn)
        notes_store.add_note(
            client_conn,
            account_id=member_id,
            salt=b"notesalt",
            taken=lambda _n: False,
            in_reply_to_iri=None,
            audience_iri=None,
            content_text="Hello from ActivityPub Note!",
            content_html="<p>Hello from ActivityPub Note!</p>",
            mentions=[],
            summary=None,
            sensitive=False,
            language="en",
            tags=["claire"],
            now=1700000600.0,
        )
        client_conn.commit()
    finally:
        client_conn.close()
    return settings, member_id


def _session(settings: Settings, account_id: int) -> str:
    conn = dbm.connect_existing(settings.db_file)
    try:
        return accounts_store.create_account_session(conn, account_id=account_id, scope="owner", now=time.time())
    finally:
        conn.close()


# --- Unit tests: Store + API interface + Internal federation contract 1.16 ---


def test_account_store_update_summary_queues_update_actor(tmp_path: Path) -> None:
    """Updating summary on an active actor persists the bio and enqueues update_actor once per change."""
    settings, member_id = _prepare(tmp_path)
    conn = dbm.connect_existing(settings.db_file)
    try:
        store = accounts_store.AccountStore(conn)
        updated = store.update_summary(member_id, "Updated bio text", now=1700001000.0)
        assert updated.summary == "Updated bio text"
        tasks = conn.execute("SELECT kind FROM federation_outbox WHERE account_id=? AND kind='update_actor'", (member_id,)).fetchall()
        assert len(tasks) == 1

        # Writing the exact same summary does not queue a redundant outbox task.
        store.update_summary(member_id, "Updated bio text", now=1700001010.0)
        tasks_after = conn.execute("SELECT kind FROM federation_outbox WHERE account_id=? AND kind='update_actor'", (member_id,)).fetchall()
        assert len(tasks_after) == 1
    finally:
        conn.close()


def test_patch_me_validates_and_updates_summary_and_internal_actor(tmp_path: Path) -> None:
    """PATCH /api/me validates summary up to 500 chars and exposes it on internal ActorRecord 1.16."""
    settings, member_id = _prepare(tmp_path)
    session = _session(settings, member_id)
    app = server.create_app(settings, SimpleNamespace(provider=SimpleNamespace(name="stub")))

    with TestClient(app, base_url=PUBLIC_URL) as tc:
        me = tc.get("/api/me", headers={"Cookie": f"claire_session={session}"})
        assert me.status_code == 200
        assert me.json()["summary"] == "Exploring knowledge graphs."

        too_long = "x" * 501
        bad = tc.patch("/api/me", headers={"Cookie": f"claire_session={session}", "Origin": PUBLIC_URL}, json={"summary": too_long})
        assert bad.status_code == 400
        assert bad.json()["error"] == "invalid_summary"

        non_str = tc.patch("/api/me", headers={"Cookie": f"claire_session={session}", "Origin": PUBLIC_URL}, json={"summary": 123})
        assert non_str.status_code == 400
        assert non_str.json()["error"] == "invalid_summary"

        ok = tc.patch("/api/me", headers={"Cookie": f"claire_session={session}", "Origin": PUBLIC_URL}, json={"summary": "Line one\nLine two"})
        assert ok.status_code == 200
        assert ok.json()["summary"] == "Line one\nLine two"

    ensure_service_key(service_key_path(settings.data_dir))
    key = load_service_key(service_key_path(settings.data_dir))
    routes = federation_routes.federation_internal_routes(settings)
    itc = TestClient(create_internal_federation_app(settings, routes=routes))
    path = f"{INTERNAL_PREFIX}/actors/{MEMBER_PUBLIC_ID}"
    ts = str(int(time.time()))
    hdrs = {TIMESTAMP_HEADER: ts, SIGNATURE_HEADER: sign_request(key, "GET", path, ts, b"")}
    res = itc.get(path, headers=hdrs)
    assert res.status_code == 200
    actor_payload = res.json()
    assert actor_payload["summary"] == "Line one\nLine two"
    federation_routes.ActorRecord.model_validate(actor_payload)


def test_user_home_routes_and_private_kb_always_public_summary(tmp_path: Path) -> None:
    """Personal User Home exposes profile + always-public kb_summary even when personal KB is private, and Shared KBs return 404."""
    settings, member_id = _prepare(tmp_path, is_public=False)
    shared_kb = get_kb_manager(settings).define_kb("Shared KB", account_id=1, is_personal=False, knowledge_type="real")
    app = server.create_app(settings, SimpleNamespace(provider=SimpleNamespace(name="stub")))

    conn = dbm.connect_existing(settings.db_file, readonly=True)
    client_conn = client_db.connect_client_existing(client_db.client_db_path(settings.db_file), readonly=True)
    try:
        account = accounts_store.AccountStore(conn).get(member_id)
        personal_kb = get_kb_manager(settings).personal_kb_for(member_id)
        prof_unit = build_user_home_profile(conn, client_conn, account=account, personal_kb=personal_kb, origin=PUBLIC_URL, handle_host=HANDLE_HOST)
        assert prof_unit["actor"]["summary"] == "Exploring knowledge graphs."
        assert prof_unit["kb_summary"]["is_public"] is False
        assert prof_unit["kb_summary"]["documents"] == 2
        assert prof_unit["kb_summary"]["entities"] == 2
        assert prof_unit["kb_summary"]["relations"] == 1
        tl_unit = build_user_home_timeline(conn, client_conn, account=account, origin=PUBLIC_URL, limit=1)
        assert len(tl_unit["items"]) == 1
        assert tl_unit["items"][0]["kind"] == "Note"
    finally:
        client_conn.close()
        conn.close()

    with TestClient(app, base_url=PUBLIC_URL) as tc:
        prof = tc.get("/@alice_home/home-profile")
        assert prof.status_code == 200
        assert prof.headers["cache-control"] == "no-store"
        pdata = prof.json()
        assert pdata["actor"]["handle"] == f"@alice_home@{HANDLE_HOST}"
        assert pdata["kb_summary"]["label"] == "Alice Knowledge Base"
        assert pdata["kb_summary"]["is_public"] is False
        assert pdata["kb_summary"]["documents"] == 2

        tl = tc.get("/@alice_home/home-timeline")
        assert tl.status_code == 200
        items = tl.json()["items"]
        assert [it["kind"] for it in items] == ["Note"]
        assert "Hello from ActivityPub Note!" in items[0]["content_html"]

        # Shared KB must not expose personal User Home endpoints.
        assert tc.get(f"/kb/{shared_kb.number}/home-profile").status_code == 404
        assert tc.get(f"/kb/{shared_kb.number}/home-timeline").status_code == 404

        # Visitor HTML on private personal KB includes profile summary, public Note, and always-public KB summary while keeping graph/documents 404.
        visitor_html = tc.get("/@alice_home/")
        assert visitor_html.status_code == 200
        assert 'data-profile-state="private"' in visitor_html.text
        assert "Exploring knowledge graphs." in visitor_html.text
        assert "Hello from ActivityPub Note!" in visitor_html.text
        assert "Alice Knowledge Base" in visitor_html.text
        assert "Knowledge graph" not in visitor_html.text
        assert tc.get("/@alice_home/graph").status_code == 404
        assert tc.get("/@alice_home/documents").status_code == 404


# --- E2E browser test ---


def test_e2e_user_home_proposal_b_mobile_tabs_note_compose_and_private_visitor(tmp_path: Path) -> None:
    """Browser E2E: desktop Proposal B + Hubs/Bridges, mobile <820px tabs, Note compose, summary edit on /account/profile, and private-KB visitor."""
    sync_api = pytest.importorskip("playwright.sync_api")
    uvicorn = pytest.importorskip("uvicorn")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = int(probe.getsockname()[1])
    base = f"http://127.0.0.1:{port}"
    settings, member_id = _prepare(tmp_path, is_public=True, public_url=base, fqdn=f"127.0.0.1:{port}")
    session = _session(settings, member_id)
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
                page_errors: list[str] = []
                console_errors: list[str] = []
                local_failures: list[str] = []

                def _attach(pg) -> None:
                    pg.on("pageerror", lambda err: page_errors.append(str(err)))
                    pg.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" and "Failed to load resource: net::ERR_" not in msg.text else None)
                    pg.on("requestfailed", lambda req: local_failures.append(f"{req.url}: {req.failure}") if req.url.startswith(base) else None)
                    pg.on("response", lambda res: local_failures.append(f"{res.status} {res.url}") if res.url.startswith(base) and res.status >= 500 else None)

                owner_ctx = browser.new_context(viewport={"width": 1280, "height": 900})
                owner_ctx.add_cookies([{"name": "claire_session", "value": session, "url": base}])
                owner_page = owner_ctx.new_page()
                _attach(owner_page)

                # 1. Edit summary in /account/profile via #settings-summary.
                owner_page.goto(f"{base}/account/profile", wait_until="domcontentloaded")
                summary_box = owner_page.locator('#settings-summary [name="summary"]')
                summary_box.fill("Bio edited in E2E.")
                with owner_page.expect_response(lambda r: r.url.endswith("/api/me") and r.request.method == "PATCH") as me_patch:
                    owner_page.locator('#settings-summary button[type="submit"]').click()
                assert me_patch.value.status == 200

                # 2. Visit desktop User Home (1280x900): Proposal B grid, Profile, Timeline, Hubs & Bridges, mobile tabs hidden.
                owner_page.goto(f"{base}/@alice_home/", wait_until="domcontentloaded")
                owner_page.locator("#home-ap-profile .uh-bio").wait_for(state="visible", timeout=10000)
                assert "Bio edited in E2E." in owner_page.locator("#home-ap-profile .uh-bio").inner_text()
                assert owner_page.locator("#home-ap-timeline .uh-tl-title").is_visible()
                assert owner_page.locator("#home-mobile-tabs").is_hidden()
                assert owner_page.locator("#home-map").is_visible()
                assert owner_page.locator("#home-map-col-hubs").is_visible()
                assert owner_page.locator("#home-map-col-bridges").is_visible()

                # 3. Compose a public Note from User Home modal (#uh-new-note-btn -> #modal-home-note).
                owner_page.locator("#uh-new-note-btn").click()
                owner_page.locator("#modal-home-note").wait_for(state="visible", timeout=5000)
                owner_page.locator("#uh-note-content").fill("Fresh E2E User Home Note!")
                with owner_page.expect_response(lambda r: r.url.endswith("/api/federation/notes") and r.request.method == "POST") as note_post:
                    owner_page.locator("#uh-note-submit").click()
                assert note_post.value.status in (200, 201)
                owner_page.get_by_text("Fresh E2E User Home Note!").first.wait_for(state="visible", timeout=10000)

                # 4. Mobile (< 820px) shows Profile + mobile-only tabs (Timeline | Knowledge Base).
                mob_ctx = browser.new_context(viewport={"width": 390, "height": 844})
                mob_ctx.add_cookies([{"name": "claire_session", "value": session, "url": base}])
                mob_page = mob_ctx.new_page()
                _attach(mob_page)
                mob_page.goto(f"{base}/@alice_home/", wait_until="domcontentloaded")
                mob_page.locator("#botnav-home").wait_for(state="visible", timeout=10000)
                mob_page.locator("#botnav-home").click()
                mob_page.locator("#home-mobile-tabs").wait_for(state="visible", timeout=10000)
                mob_page.locator("#home-ap-timeline").wait_for(state="visible", timeout=10000)
                mob_page.locator("#home-flow-publish").wait_for(state="visible", timeout=10000)
                flow_metrics = mob_page.evaluate(
                    """(() => {
                      const nav = document.getElementById('home-flow');
                      const ids = ['home-flow-collect', 'home-flow-ingest', 'home-flow-publish'];
                      const rects = ids.map(id => {
                        const r = document.getElementById(id).getBoundingClientRect();
                        return { x: r.x, y: r.y, width: r.width, height: r.height };
                      });
                      return { overflow: nav.scrollWidth <= nav.clientWidth, rects };
                    })()"""
                )
                b_collect, b_ingest, b_publish = flow_metrics["rects"]
                assert flow_metrics["overflow"] is True
                assert b_collect["width"] > 0 and b_ingest["width"] > 0 and b_publish["width"] > 0
                assert abs(b_collect["y"] - b_ingest["y"]) <= 2 and abs(b_ingest["y"] - b_publish["y"]) <= 2
                assert b_collect["x"] < b_ingest["x"] < b_publish["x"]
                assert mob_page.locator("#home-ap-timeline").is_visible()
                assert mob_page.locator("#home-facts").is_hidden()
                mob_page.locator("#home-tab-kb").click()
                assert mob_page.locator("#home-tab-kb").get_attribute("aria-selected") == "true"
                assert mob_page.locator("#home-facts").is_visible()
                assert mob_page.locator("#home-ap-timeline").is_hidden()
                mob_ctx.close()

                # 5. Private personal KB (is_public=0): anonymous visitor sees Profile + summary + Note timeline + always-public KB summary.
                conn = dbm.connect_existing(settings.db_file)
                try:
                    kb = get_kb_manager(settings).personal_kb_for(member_id)
                    conn.execute("UPDATE knowledge_bases SET is_public=0 WHERE id=?", (kb.id,))
                    conn.commit()
                finally:
                    conn.close()

                anon_ctx = browser.new_context(viewport={"width": 1280, "height": 900})
                anon_page = anon_ctx.new_page()
                _attach(anon_page)
                anon_page.goto(f"{base}/@alice_home/", wait_until="domcontentloaded")
                anon_page.locator("#home-ap-profile .uh-bio").wait_for(state="visible", timeout=10000)
                anon_page.locator("#uh-private-kb-summary").wait_for(state="visible", timeout=10000)
                assert anon_page.locator('[data-profile-state="private"]').is_visible()
                assert "Bio edited in E2E." in anon_page.locator("#home-ap-profile").inner_text()
                assert anon_page.locator("#uh-private-kb-summary").is_visible()
                assert "Alice Knowledge Base" in anon_page.locator("#uh-private-kb-summary").inner_text()
                assert "Fresh E2E User Home Note!" in anon_page.locator("#home-ap-timeline").inner_text()
                assert "Alpha Graph Note" not in anon_page.content()
                anon_ctx.close()
                owner_ctx.close()

                assert page_errors == []
                assert console_errors == []
                assert local_failures == []
            finally:
                browser.close()
    finally:
        srv.should_exit = True
        thread.join(timeout=15)
