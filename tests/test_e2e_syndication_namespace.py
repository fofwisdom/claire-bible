"""End-to-End test for Syndication Namespace and Account Attribution (P6).

Covers:
- Single mode:
  * Gate G8: Unauthenticated GET/HEAD /syndication returns 401 or 404 (no public access).
  * Authenticated GET /syndication returns 200, authScope='owner', and safe HTML.
  * GET /syndication/preferences is a 302 compatibility redirect (D4): reader → /settings/reader; for the admin, ?tab=idp → /admin/idp and ?tab=userdb → /admin/accounts.
  * GET /me is a 302 to /settings/account.
  * Dispatch records dispatched_by_account_id=1.
- Network mode:
  * Member legacy /@{username}/syndication/preferences?tab= redirects to root-mounted /settings/* (?tab=idp|userdb → /settings/account), never with /@{username} in Location.
  * Namespaced access /@{username}/syndication and /@{username}/themes/{theme_number}/syndication.
  * Caller's account_id is recorded in syndication_dispatches.dispatched_by_account_id.
  * Cross-account write protection: member cannot dispatch into another member's theme.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from claire.accounts import readiness
from claire.api import security, server
from claire.config import Settings
from claire.ontology.base import Document
from claire.store import accounts as accounts_store
from claire.store import db as dbm
from claire.store.secrets import reset_secret_service
from claire.store.theme import ThemeManager
from claire.store.token_store import TokenStore, get_token_store, reset_token_stores

ADMIN_TOKEN = "adm-" + ("a" * 32)
ALICE_TOKEN = "ali-" + ("a" * 32)
BOB_TOKEN = "bob-" + ("b" * 32)


def _setup_theme(
    tm: ThemeManager,
    settings: Settings,
    *,
    theme_id: int,
    seq: int,
    label: str,
    account_id: int,
    is_default: bool = False,
    is_public: bool = True,
    is_collaborator_accessible: bool = True,
) -> ThemeInfo:
    conn = dbm.connect(settings.db_file)
    now = time.time()
    try:
        conn.execute(
            "INSERT INTO themes (id, seq, number, label, description, icon, is_default, is_public, is_collaborator_accessible, account_id, db_path, default_focus, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, '📁', ?, ?, ?, ?, ?, '', ?, ?)",
            (
                theme_id,
                seq,
                (account_id << 56) | seq,
                label,
                f"Description for {label}",
                1 if is_default else 0,
                1 if is_public else 0,
                1 if is_collaborator_accessible else 0,
                account_id,
                str(settings.db_file),
                now,
                now,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    tm.reload()
    tm._save_registry()
    return tm.get_theme(theme_id)


def _add_account(conn: sqlite3.Connection, account_id: int, username: str, role: str = "member") -> None:
    now = time.time()
    conn.execute(
        "INSERT INTO accounts (id, public_id, username, role, status, display_name, profile_public, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, 'active', ?, 1, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET username=excluded.username, role=excluded.role, status='active', updated_at=excluded.updated_at",
        (account_id, (1 << 57) + account_id, username, role, username.capitalize(), now, now),
    )
    conn.commit()


@pytest.fixture(autouse=True)
def _cleanup_env(monkeypatch: pytest.MonkeyPatch):
    reset_secret_service()
    reset_token_stores()
    for key in list(os.environ):
        if key.startswith("CLAIRE_"):
            monkeypatch.delenv(key, raising=False)
    yield
    reset_secret_service()
    reset_token_stores()


@pytest.fixture
def p6_env(tmp_path: Path):
    data_dir = tmp_path / "data"
    vault_dir = tmp_path / "vault"
    data_dir.mkdir(parents=True)
    vault_dir.mkdir(parents=True)

    db_file = data_dir / "claire.db"
    settings = Settings(
        CLAIRE_DATA_DIR=str(data_dir),
        CLAIRE_DB_PATH=str(db_file),
        CLAIRE_VAULT_PATH=str(vault_dir),
        CLAIRE_PROVIDER="mock",
        CLAIRE_ENVIRONMENT="development",
        CLAIRE_PUBLIC_URL="http://127.0.0.1:8765",
        CLAIRE_INJECT_TOKEN=ADMIN_TOKEN,
        CLAIRE_ANONYMOUS_READONLY=False,
        CLAIRE_MULTI_THEME=True,
    )

    conn = dbm.connect(db_file)
    dbm.init_db(conn)

    _add_account(conn, 1, "admin", role="admin")
    _add_account(conn, 2, "alice", role="member")
    _add_account(conn, 3, "bob", role="member")

    token_store = get_token_store(data_dir=data_dir)
    token_store.ensure_default_owner_token(fallback_token=ADMIN_TOKEN)
    alice_token = token_store.create_token(name="alice-owner", scope="owner", account_id=2)["token"]
    bob_token = token_store.create_token(name="bob-collab", scope="collaborator", account_id=3)["token"]

    conn.close()
    return settings, tmp_path, alice_token, bob_token


def test_e2e_syndication_single_mode(p6_env):
    settings, tmp_path, alice_token, bob_token = p6_env
    app = server.create_app(settings=settings)

    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        # 1. Gate G8 check: unauthenticated GET and HEAD to /syndication are refused
        res_unauth = client.get("/syndication")
        assert res_unauth.status_code in (401, 404)
        res_head_unauth = client.head("/syndication")
        assert res_head_unauth.status_code in (401, 404)

        # 2. Authenticated access succeeds with owner token
        headers = {"Authorization": f"Bearer {ADMIN_TOKEN}"}
        res_auth = client.get("/syndication", headers=headers)
        assert res_auth.status_code == 200
        assert "authScope: 'owner'" in res_auth.text
        assert "Claire Bible — Syndication" in res_auth.text

        # 3. Legacy preferences URLs are 302 redirects into the console (D4, §2.2)
        assert 'data-settings-path="/settings/reader"' in res_auth.text
        res_prefs = client.get("/syndication/preferences", headers=headers, follow_redirects=False)
        assert res_prefs.status_code == 302
        assert res_prefs.headers["location"] == "/settings/reader"
        for tab, target in (("idp", "/admin/idp"), ("userdb", "/admin/accounts"), ("accounts", "/settings/connections")):
            res_tab = client.get(f"/syndication/preferences?tab={tab}", headers=headers, follow_redirects=False)
            assert res_tab.status_code == 302, tab
            assert res_tab.headers["location"] == target, tab
        for target in ("/settings/reader", "/admin/idp", "/admin/accounts"):
            assert client.get(target, headers=headers, follow_redirects=False).status_code == 200, target
        res_me = client.get("/me", headers=headers, follow_redirects=False)
        assert res_me.status_code == 302
        assert res_me.headers["location"] == "/settings/account"
        # Single mode: no @username anywhere in redirect targets (S9).
        assert "@" not in res_prefs.headers["location"] + res_me.headers["location"]

        # 4. Readiness gate G8 in single mode
        conn = dbm.connect(settings.db_file)
        try:
            report = readiness.check_readiness(conn, settings)
            item_g8 = next(i for i in report.items if i.id == "G8")
            assert item_g8.passed is True
        finally:
            conn.close()


def test_e2e_syndication_network_mode_isolation(p6_env):
    settings, tmp_path, alice_token, bob_token = p6_env

    # Configure network mode and themes
    conn = dbm.connect(settings.db_file)
    try:
        store = accounts_store.AccountStore(conn)
        store.set_setting("mode", "network", updated_by=1)
        conn.close()

        tm = ThemeManager(settings)
        alice_theme = _setup_theme(
            tm,
            settings,
            theme_id=20,
            seq=1,
            label="Alice Theme",
            account_id=2,
            is_default=True,
            is_public=False,
            is_collaborator_accessible=True,
        )
        bob_theme = _setup_theme(
            tm,
            settings,
            theme_id=30,
            seq=2,
            label="Bob Theme",
            account_id=3,
            is_default=True,
            is_public=False,
            is_collaborator_accessible=True,
        )

        conn = dbm.connect(settings.db_file)
        # Seed a source document in Alice's theme
        conn.execute(
            "INSERT INTO documents (id, title, raw_text, detail, theme_id, fetched_at) "
            "VALUES ('doc_alice_1', 'Alice Post', 'Test content from Alice', 'Detail', ?, ?)",
            (alice_theme.id, time.time()),
        )
        conn.commit()
    finally:
        conn.close()

    app = server.create_app(settings=settings)

    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        # 1. Network mode: /syndication and /prototype/syndication are root-mounted _system routes.
        # Prefixed paths under /@{username}/... or /themes/{n}/... fail closed with 404 (rule N5).
        alice_headers = {"Authorization": f"Bearer {alice_token}"}
        res_root = client.get("/syndication", headers=alice_headers)
        assert res_root.status_code == 200
        assert "Alice Theme" in res_root.text
        assert "Bob Theme" not in res_root.text

        res_proto = client.get("/prototype/syndication", headers=alice_headers)
        assert res_proto.status_code == 200
        assert "Claire Bible — Syndication Prototype" in res_proto.text

        for bad_path in (
            "/@alice/syndication",
            "/@alice/prototype/syndication",
            "/@alice/syndication/preferences",
            f"/@alice/themes/{alice_theme.number}/syndication",
            f"/themes/{alice_theme.number}/syndication",
        ):
            assert client.get(bad_path, headers=alice_headers, follow_redirects=False).status_code == 404, bad_path

        # Unauthenticated access to root syndication and prototype is rejected
        assert client.get("/syndication").status_code in (401, 404)
        assert client.get("/prototype/syndication").status_code in (401, 404)

        # Entry links and legacy preferences redirects target root-mounted /settings/* (D1), never /@alice/settings.
        assert 'data-settings-path="/settings/reader"' in res_root.text
        assert "/@alice/settings" not in res_root.text
        assert "/@alice/syndication" not in res_root.text
        alice_ref = str(alice_theme.number)
        for tab, target in (
            (None, "/settings/reader"),
            ("reader", "/settings/reader"),
            ("accounts", "/settings/connections"),
            ("feeds", "/settings/feeds"),
            ("tags", "/settings/tags"),
            ("rules", "/settings/rules"),
            ("outbound", "/settings/outbound"),
            ("opml", "/settings/opml"),
            ("mcp", "/settings/mcp"),
            ("webmcp", "/settings/webmcp"),
            ("idp", "/settings/account"),
            ("userdb", "/settings/account"),
        ):
            query = f"?tab={tab}" if tab is not None else ""
            res_tab = client.get(f"/syndication/preferences{query}", headers=alice_headers, follow_redirects=False)
            assert res_tab.status_code == 302, tab
            assert res_tab.headers["location"] == target, tab
            sep = "&" if query else "?"
            res_theme = client.get(f"/syndication/preferences{query}{sep}theme={alice_ref}", headers=alice_headers, follow_redirects=False)
            assert res_theme.status_code == 302, tab
            expected = target if target == "/settings/account" else f"{target}?theme={alice_ref}"
            assert res_theme.headers["location"] == expected, tab
        res_member_settings = client.get(f"/settings/feeds?theme={alice_ref}", headers=alice_headers, follow_redirects=False)
        assert res_member_settings.status_code == 200
        assert "/admin/" not in res_member_settings.text
        assert "/@alice/syndication" not in res_member_settings.text
        assert 'href="/syndication?theme=' in res_member_settings.text
        # Unknown tab and a theme of another account fail closed with 404 (C1).
        assert client.get("/syndication/preferences?tab=nope", headers=alice_headers, follow_redirects=False).status_code == 404
        assert client.get(f"/syndication/preferences?theme={bob_theme.number}", headers=alice_headers, follow_redirects=False).status_code == 404
        # A collaborator (Bob) and anonymous callers never see a redirect target.
        bob_prefs = client.get("/syndication/preferences", headers={"Authorization": f"Bearer {bob_token}"}, follow_redirects=False)
        assert bob_prefs.status_code in (401, 403, 404)
        assert "location" not in bob_prefs.headers
        assert client.get("/syndication/preferences", follow_redirects=False).status_code in (401, 404)
        res_me = client.get("/me", headers=alice_headers, follow_redirects=False)
        assert res_me.status_code == 302 and res_me.headers["location"] == "/settings/account"

        # 2. Alice dispatches within her own theme at root /api/syndication/dispatch -> records dispatched_by_account_id=2
        dispatch_payload = {
            "document_id": "doc_alice_1",
            "source_theme_id": alice_theme.id,
            "target_theme_id": alice_theme.id,
        }
        assert client.post("/@alice/api/syndication/dispatch", json=dispatch_payload, headers=alice_headers).status_code == 404
        res_dispatch = client.post(
            "/api/syndication/dispatch",
            json=dispatch_payload,
            headers=alice_headers,
        )
        assert res_dispatch.status_code == 200, res_dispatch.text
        data = res_dispatch.json()
        assert data["dispatched_by_account_id"] == 2

        # 3. Bob attempts to dispatch into Alice's theme -> 403/404 Forbidden
        bob_headers = {"Authorization": f"Bearer {bob_token}"}
        res_cross_forbidden = client.post(
            "/api/syndication/dispatch",
            json={
                "document_id": "doc_alice_1",
                "source_theme_id": alice_theme.id,
                "target_theme_id": alice_theme.id,
            },
            headers=bob_headers,
        )
        assert res_cross_forbidden.status_code in (403, 404)

        # 4. Verify dispatches list at root /api/syndication/dispatches
        assert client.get("/@alice/api/syndication/dispatches?theme_id=" + str(alice_theme.id), headers=alice_headers).status_code == 404
        res_dispatches = client.get(
            "/api/syndication/dispatches?theme_id=" + str(alice_theme.id),
            headers=alice_headers,
        )
        assert res_dispatches.status_code == 200
        dispatches = res_dispatches.json().get("dispatches", [])
        assert len(dispatches) >= 1
        assert dispatches[0]["dispatched_by_account_id"] == 2
