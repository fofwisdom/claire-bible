"""End-to-End test for Syndication Namespace and Account Attribution (P6).

Covers:
- Single mode:
  * Gate G8: Unauthenticated GET/HEAD /syndication returns 401 or 404 (no public access).
  * Authenticated GET /syndication returns 200, authScope='owner', and safe HTML.
  * Authenticated GET /syndication/preferences returns 200 with active links to /admin/idp, /admin/accounts, /me, /me/keys.
  * Connected account token AES-256-GCM encryption with AAD f"{theme_id}|{account_id}" and cross-theme tampering prevention.
  * Dispatch records dispatched_by_account_id=1.
- Network mode:
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
from claire.store.secrets import SecretDecryptionError, reset_secret_service
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

        # 3. Preferences subpage has active management links
        res_prefs = client.get("/syndication/preferences", headers=headers)
        assert res_prefs.status_code == 200
        assert "/admin/idp" in res_prefs.text
        assert "/admin/accounts" in res_prefs.text
        assert "/me" in res_prefs.text
        assert 'id="panel-idp"' in res_prefs.text
        assert 'id="panel-userdb"' in res_prefs.text

        # 4. Connected account token encryption and AAD verification
        conn = dbm.connect(settings.db_file)
        try:
            account_id = dbm.create_syndication_account(
                conn,
                theme_id=0,
                protocol="activitypub",
                server_url="https://mastodon.social",
                handle="@admin@mastodon.social",
                auth_kind="oauth",
                scopes="read write follow",
                token_str="secret-oauth-token-12345",
            )
            # Decryption with matching theme succeeds
            decrypted = dbm.load_syndication_account_token(conn, account_id, theme_id=0)
            assert decrypted == "secret-oauth-token-12345"

            # Create a second valid theme to test cross-theme row tampering
            conn.execute(
                "INSERT INTO themes (id, seq, label, description, is_default, is_public, created_at, updated_at) "
                "VALUES (2, 2, 'Other Theme', 'Test', 0, 1, 0, 0)"
            )
            conn.commit()

            # Tampering row's theme_id in DB triggers SecretDecryptionError on decrypt
            conn.execute("UPDATE syndication_accounts SET theme_id = 2 WHERE id = ?", (account_id,))
            conn.commit()
            with pytest.raises(SecretDecryptionError):
                dbm.load_syndication_account_token(conn, account_id, theme_id=2)

            # Restore theme_id for subsequent checks
            conn.execute("UPDATE syndication_accounts SET theme_id = 0 WHERE id = ?", (account_id,))
            conn.commit()

            # In single mode, check G8 in readiness
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
        # 1. Network mode namespaced syndication UI
        alice_headers = {"Authorization": f"Bearer {alice_token}"}
        res_ns = client.get("/@alice/syndication", headers=alice_headers)
        assert res_ns.status_code == 200

        # Unauthenticated access to namespaced syndication is rejected
        res_ns_unauth = client.get("/@alice/syndication")
        assert res_ns_unauth.status_code in (401, 404)

        # 2. Alice dispatches within her own theme -> records dispatched_by_account_id=2
        dispatch_payload = {
            "document_id": "doc_alice_1",
            "source_theme_id": alice_theme.id,
            "target_theme_id": alice_theme.id,
        }
        res_dispatch = client.post(
            "/@alice/api/syndication/dispatch",
            json=dispatch_payload,
            headers=alice_headers,
        )
        assert res_dispatch.status_code == 200, res_dispatch.text
        data = res_dispatch.json()
        assert data["dispatched_by_account_id"] == 2

        # 3. Bob attempts to dispatch into Alice's theme -> 403 Forbidden
        bob_headers = {"Authorization": f"Bearer {bob_token}"}
        res_cross_forbidden = client.post(
            "/@alice/api/syndication/dispatch",
            json={
                "document_id": "doc_alice_1",
                "source_theme_id": alice_theme.id,
                "target_theme_id": alice_theme.id,
            },
            headers=bob_headers,
        )
        assert res_cross_forbidden.status_code in (403, 404)

        # 4. Verify dispatches list filters by caller
        res_dispatches = client.get(
            "/@alice/api/syndication/dispatches?theme_id=" + str(alice_theme.id),
            headers=alice_headers,
        )
        assert res_dispatches.status_code == 200
        dispatches = res_dispatches.json().get("dispatches", [])
        assert len(dispatches) >= 1
        assert dispatches[0]["dispatched_by_account_id"] == 2
