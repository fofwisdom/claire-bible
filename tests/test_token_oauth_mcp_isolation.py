"""Tests for multi-account isolation across TokenStore, OAuth 2.1, and MCP tools (P3 L3-e).

Covers:
- TokenStore multi-account isolation: create_token requires account_id, list_tokens filtering, cross-account revocation prevention
- OAuth 2.1 isolation: account_id binding, inactive/suspended/purged account rejection on authorize/token/refresh, redirect_uri mismatch, same_origin CSRF defense, confidential client_secret check, telegram-push admin restriction
- MCP tools isolation: ContextVar[Principal] reference, account-owned theme scoping, collaborator visibility gating, readonly connection verification, port preservation in allowed_hosts
- End-to-end integration test of multi-account OAuth to MCP flow
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import sqlite3
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route
from starlette.testclient import TestClient

from claire.api.mcp_tools import build_mcp_app, current_principal
from claire.api.oauth import (
    handle_authorize,
    handle_register,
    handle_telegram_push,
    handle_token,
)
from claire.api.host_router import HostRoles
from claire.api.principal import PRINCIPAL_STATE_KEY, Principal
from claire.api.security import (
    CORSPolicyMiddleware,
    WebRuntimeConfig,
)
from claire.config import Settings
from claire.ontology.base import Document
from claire.store import accounts as accounts_store
from claire.store import db as dbm
from claire.store.theme import ThemeInfo, ThemeManager
from claire.store.token_store import TokenStore, reset_token_stores


@pytest.fixture(autouse=True)
def cleanup_tokens():
    reset_token_stores()
    yield
    reset_token_stores()


def _add_member_account(conn: sqlite3.Connection, account_id: int, username: str, status: str = "active") -> None:
    now = time.time()
    conn.execute(
        "INSERT INTO accounts (id, public_id, username, role, status, display_name, profile_public, created_at, updated_at) "
        "VALUES (?, ?, ?, 'member', ?, ?, 0, ?, ?)",
        (account_id, (1 << 57) + account_id, username, status, username.capitalize(), now, now),
    )
    conn.commit()


def _create_test_config(tmp_path: Path, public_origin: str = "http://127.0.0.1:8765") -> tuple[WebRuntimeConfig, Path]:
    db_file = tmp_path / "claire.db"
    conn = dbm.connect(db_file)
    dbm.init_db(conn)
    conn.close()

    host_roles = HostRoles(
        scheme="http",
        app="127.0.0.1:8765",
        usercontent=None,
        media=None,
        site_domain=None,
    )
    config = WebRuntimeConfig(
        environment="development",
        public_origin=public_origin,
        expected_authority="127.0.0.1:8765",
        cors_allowed_origins=frozenset(),
        secure_cookie=False,
        anonymous_readonly=False,
        owner_token="",
        readonly_token="",
        collaborator_token="",
        db_file=db_file,
        data_dir=tmp_path,
        trusted_proxies=(),
        host_roles=host_roles,
    )
    return config, db_file


def _create_test_theme(
    tm: ThemeManager,
    settings: Settings,
    label: str,
    *,
    account_id: int,
    is_default: bool = False,
    is_public: bool = True,
    is_collaborator_accessible: bool = True,
) -> ThemeInfo:
    theme = tm.define_theme(
        label,
        is_public=is_public,
        is_collaborator_accessible=is_collaborator_accessible,
    )
    conn = dbm.connect(settings.db_file)
    try:
        conn.execute(
            "UPDATE themes SET account_id = ?, is_default = ? WHERE id = ?",
            (account_id, 1 if is_default else 0, theme.id),
        )
        conn.commit()
    finally:
        conn.close()

    if tm.registry_path.is_file():
        data = json.loads(tm.registry_path.read_text(encoding="utf-8"))
        for entry in data.get("themes", []):
            if entry.get("id") == theme.id:
                entry["account_id"] = account_id
                entry["is_default"] = is_default
        tm.registry_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    tm.reload()
    return tm.get_theme(theme.id)


def _build_oauth_test_app(config: WebRuntimeConfig, settings: Any = None) -> Starlette:
    routes = [
        Route("/oauth/register", handle_register, methods=["POST"]),
        Route("/oauth/authorize", handle_authorize, methods=["GET", "POST"]),
        Route("/oauth/authorize/telegram-push", handle_telegram_push, methods=["POST"]),
        Route("/oauth/token", handle_token, methods=["POST"]),
    ]
    app = Starlette(routes=routes)
    app.state.runtime_config = config
    app.state.settings = settings
    return CORSPolicyMiddleware(app, config)


# --- 1. TokenStore Multi-Account Isolation Tests --------------------------------


def test_token_store_create_token_requires_account_id(tmp_path: Path) -> None:
    store = TokenStore(storage_path=tmp_path / "tokens.json")

    # Omitting account_id must raise TypeError
    with pytest.raises(TypeError, match="create_token missing required argument"):
        store.create_token(name="Missing Account")

    # Explicit account_id works
    tok = store.create_token(name="Member Token", account_id=2)
    assert tok["name"] == "Member Token"
    assert tok["account_id"] == 2
    assert tok["token"].startswith("cb_")


def test_token_store_list_and_revoke_isolation(tmp_path: Path) -> None:
    store = TokenStore(storage_path=tmp_path / "tokens.json")

    tok_admin = store.create_token(name="Admin Token", account_id=1, scope="owner")
    tok_member = store.create_token(name="Member Token", account_id=2, scope="readonly")

    # list_tokens filtering
    admin_tokens = store.list_tokens(account_id=1)
    member_tokens = store.list_tokens(account_id=2)
    all_tokens = store.list_tokens()

    assert len(admin_tokens) == 1
    assert admin_tokens[0]["token"] == tok_admin["token"]
    assert len(member_tokens) == 1
    assert member_tokens[0]["token"] == tok_member["token"]
    assert len(all_tokens) == 2

    # Cross-account get_token returns None
    assert store.get_token(tok_admin["token"], account_id=2) is None
    assert store.get_token(tok_admin["token"], account_id=1) is not None
    assert store.get_token(tok_member["token"], account_id=1) is None
    assert store.get_token(tok_member["token"], account_id=2) is not None

    # Cross-account revocation is rejected
    revoked = store.revoke_token(tok_admin["token"], account_id=2)
    assert revoked is False
    assert store.validate_api_token(tok_admin["token"]) is not None

    revoked_by_id = store.revoke_api_token(tok_admin["id"], account_id=2)
    assert revoked_by_id is False
    assert store.validate_api_token(tok_admin["token"]) is not None

    # Own revocation succeeds
    revoked_own = store.revoke_token(tok_admin["token"], account_id=1)
    assert revoked_own is True
    assert store.validate_api_token(tok_admin["token"]) is None


# --- 2. OAuth 2.1 Multi-Account & Security Isolation Tests -----------------------


def test_oauth_register_blocks_dangerous_schemes(tmp_path: Path) -> None:
    config, _ = _create_test_config(tmp_path)
    app = _build_oauth_test_app(config)

    with TestClient(app, base_url=config.public_origin) as client:
        # javascript: scheme is rejected
        resp_js = client.post(
            "/oauth/register",
            json={"client_name": "Evil", "redirect_uris": ["javascript:alert(1)"]},
        )
        assert resp_js.status_code == 400
        assert resp_js.json()["error"] == "invalid_redirect_uri"

        # data: scheme is rejected
        resp_data = client.post(
            "/oauth/register",
            json={"client_name": "Evil", "redirect_uris": ["data:text/html,evil"]},
        )
        assert resp_data.status_code == 400
        assert resp_data.json()["error"] == "invalid_redirect_uri"

        # Valid https scheme succeeds
        resp_ok = client.post(
            "/oauth/register",
            json={"client_name": "Safe App", "redirect_uris": ["https://app.example.com/oauth/callback"]},
        )
        assert resp_ok.status_code == 201
        assert resp_ok.json()["client_id"].startswith("claire_mcp_")


def test_oauth_authorize_unregistered_client_and_redirect_uri_mismatch(tmp_path: Path) -> None:
    config, _ = _create_test_config(tmp_path)
    app = _build_oauth_test_app(config)

    with TestClient(app, base_url=config.public_origin) as client:
        reg = client.post(
            "/oauth/register",
            json={"client_name": "Valid App", "redirect_uris": ["https://app.example.com/callback"]},
        ).json()
        client_id = reg["client_id"]

        # 1. Unregistered client_id -> 400 unauthorized_client
        resp_unreg = client.get(
            "/oauth/authorize?response_type=code&client_id=nonexistent&redirect_uri=https://app.example.com/callback"
        )
        assert resp_unreg.status_code == 400
        assert resp_unreg.json()["error"] == "unauthorized_client"

        # 2. Mismatched redirect_uri -> 400 invalid_request
        resp_mismatch = client.get(
            f"/oauth/authorize?response_type=code&client_id={client_id}&redirect_uri=https://evil.com/callback"
        )
        assert resp_mismatch.status_code == 400
        assert resp_mismatch.json()["error"] == "invalid_request"

        # 3. Dangerous javascript: redirect_uri -> 400 invalid_request
        resp_js = client.get(
            f"/oauth/authorize?response_type=code&client_id={client_id}&redirect_uri=javascript:evil()"
        )
        assert resp_js.status_code == 400
        assert resp_js.json()["error"] == "invalid_request"

        # 4. Valid GET authorize returns consent form with same-origin Referrer-Policy header
        resp_valid = client.get(
            f"/oauth/authorize?response_type=code&client_id={client_id}&redirect_uri=https://app.example.com/callback&code_challenge=xyz&code_challenge_method=S256"
        )
        assert resp_valid.status_code == 200
        assert resp_valid.headers.get("referrer-policy") == "same-origin"


def test_oauth_authorize_same_origin_subdomain_csrf_defense(tmp_path: Path) -> None:
    config, db_file = _create_test_config(tmp_path)
    app = _build_oauth_test_app(config)

    conn = dbm.connect(db_file)
    accounts_store.AccountStore(conn).complete_setup(1, username="adminuser", display_name="Admin")
    session_tok = accounts_store.create_account_session(conn, account_id=1, scope="owner")
    conn.close()

    with TestClient(app, base_url=config.public_origin) as client:
        reg = client.post(
            "/oauth/register",
            json={"client_name": "App", "redirect_uris": ["https://app.example.com/callback"]},
        ).json()
        client_id = reg["client_id"]

        form_data = {
            "client_id": client_id,
            "redirect_uri": "https://app.example.com/callback",
            "state": "st1",
            "code_challenge": "xyz",
            "code_challenge_method": "S256",
            "scope": "readonly",
            "action": "approve",
        }

        # Subdomain / cross-origin CSRF attempt with cookie is rejected with 403
        resp_csrf = client.post(
            "/oauth/authorize",
            data=form_data,
            cookies={"claire_session": session_tok},
            headers={"Origin": "https://subdomain.attacker.com"},
        )
        assert resp_csrf.status_code == 403
        assert resp_csrf.text == "Forbidden"

        # Sec-Fetch-Site: cross-site attempt is rejected with 403
        resp_cross_site = client.post(
            "/oauth/authorize",
            data=form_data,
            cookies={"claire_session": session_tok},
            headers={"Sec-Fetch-Site": "cross-site"},
        )
        assert resp_cross_site.status_code == 403
        assert resp_cross_site.json()["error"] == "access_denied"

        # Same origin post succeeds with 302 redirect
        resp_ok = client.post(
            "/oauth/authorize",
            data=form_data,
            cookies={"claire_session": session_tok},
            headers={"Origin": config.public_origin, "Sec-Fetch-Site": "same-origin"},
            follow_redirects=False,
        )
        assert resp_ok.status_code == 302
        assert resp_ok.headers["Location"].startswith("https://app.example.com/callback?code=")


def test_oauth_account_binding_and_inactive_account_rejection(tmp_path: Path) -> None:
    config, db_file = _create_test_config(tmp_path)
    app = _build_oauth_test_app(config)

    conn = dbm.connect(db_file)
    accounts_store.AccountStore(conn).complete_setup(1, username="adminuser", display_name="Admin")
    _add_member_account(conn, account_id=2, username="memberbob", status="active")
    member_session = accounts_store.create_account_session(conn, account_id=2, scope="owner")
    conn.close()

    with TestClient(app, base_url=config.public_origin) as client:
        reg = client.post(
            "/oauth/register",
            json={"client_name": "Member App", "redirect_uris": ["https://app.example.com/callback"]},
        ).json()
        client_id = reg["client_id"]
        client_secret = reg["client_secret"]

        verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
        digest = hashlib.sha256(verifier.encode("ascii")).digest()
        challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")

        # 1. Authorize with member account (account_id = 2)
        auth_resp = client.post(
            "/oauth/authorize",
            data={
                "client_id": client_id,
                "redirect_uri": "https://app.example.com/callback",
                "state": "state2",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "scope": "readonly",
                "action": "approve",
            },
            cookies={"claire_session": member_session},
            headers={"Origin": config.public_origin, "Sec-Fetch-Site": "same-origin"},
            follow_redirects=False,
        )
        assert auth_resp.status_code == 302
        loc = auth_resp.headers["Location"]
        code = loc.split("code=")[1].split("&")[0]

        # Verify authorization code is saved with account_id = 2
        conn = dbm.connect(db_file)
        code_row = conn.execute("SELECT account_id FROM oauth_codes WHERE code=?", (code,)).fetchone()
        assert code_row is not None
        assert code_row["account_id"] == 2
        conn.close()

        # 2. Exchange code for tokens
        token_resp = client.post(
            "/oauth/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": "https://app.example.com/callback",
                "code_verifier": verifier,
            },
        )
        assert token_resp.status_code == 200
        token_data = token_resp.json()
        access_tok = token_data["access_token"]
        refresh_tok = token_data["refresh_token"]

        # Verify access_token and refresh_token are bound to account_id = 2
        conn = dbm.connect(db_file)
        tok_row = conn.execute("SELECT account_id FROM oauth_tokens WHERE token=?", (access_tok,)).fetchone()
        assert tok_row is not None
        assert tok_row["account_id"] == 2
        conn.close()

        # 3. Test confidential client secret validation
        # Wrong client_secret returns 401
        ref_bad = client.post(
            "/oauth/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_tok,
                "client_id": client_id,
                "client_secret": "wrong_secret",
            },
        )
        assert ref_bad.status_code == 401
        assert ref_bad.json()["error"] == "invalid_client"

        # 4. Suspend member account -> refresh must be immediately rejected!
        conn = dbm.connect(db_file)
        conn.execute("UPDATE accounts SET status='suspended' WHERE id=2")
        conn.commit()
        conn.close()

        ref_suspended = client.post(
            "/oauth/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_tok,
                "client_id": client_id,
                "client_secret": client_secret,
            },
        )
        assert ref_suspended.status_code == 400
        assert ref_suspended.json()["error"] == "invalid_grant"
        assert "suspended" in ref_suspended.json()["error_description"]

        # Reactivate account -> refresh succeeds
        conn = dbm.connect(db_file)
        conn.execute("UPDATE accounts SET status='active' WHERE id=2")
        conn.commit()
        conn.close()

        ref_ok = client.post(
            "/oauth/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_tok,
                "client_id": client_id,
                "client_secret": client_secret,
            },
        )
        assert ref_ok.status_code == 200
        new_refresh = ref_ok.json()["refresh_token"]

        # Purge account -> refresh rejected
        conn = dbm.connect(db_file)
        conn.execute("UPDATE accounts SET status='purged' WHERE id=2")
        conn.commit()
        conn.close()

        ref_purged = client.post(
            "/oauth/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": new_refresh,
                "client_id": client_id,
                "client_secret": client_secret,
            },
        )
        assert ref_purged.status_code == 400
        assert ref_purged.json()["error"] == "invalid_grant"


def test_oauth_telegram_push_restricted_to_admin(tmp_path: Path) -> None:
    config, db_file = _create_test_config(tmp_path)
    settings = SimpleNamespace(
        telegram_bot_token="test_token",
        telegram_owner_chat_id=12345,
        allowed_user_ids={12345},
    )
    app = _build_oauth_test_app(config, settings=settings)

    conn = dbm.connect(db_file)
    accounts_store.AccountStore(conn).complete_setup(1, username="adminuser", display_name="Admin")
    admin_session = accounts_store.create_account_session(conn, account_id=1, scope="owner")
    _add_member_account(conn, account_id=2, username="memberbob", status="active")
    member_session = accounts_store.create_account_session(conn, account_id=2, scope="owner")
    dbm.register_oauth_client(
        conn,
        client_id="push_client",
        client_secret="sec",
        client_name="Push App",
        redirect_uris=["https://app.example.com/cb"],
    )
    conn.close()

    mock_resp = SimpleNamespace(status_code=200)
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_resp):
        with TestClient(app, base_url=config.public_origin) as client:
            # Member session (account_id = 2) is forbidden (403) from using telegram-push
            resp_member = client.post(
                "/oauth/authorize/telegram-push",
                json={"client_id": "push_client", "redirect_uri": "https://app.example.com/cb"},
                cookies={"claire_session": member_session},
            )
            assert resp_member.status_code == 403
            assert resp_member.json()["error"] == "forbidden"

            # Admin session (account_id = 1) is allowed
            resp_admin = client.post(
                "/oauth/authorize/telegram-push",
                json={"client_id": "push_client", "redirect_uri": "https://app.example.com/cb"},
                cookies={"claire_session": admin_session},
            )
            assert resp_admin.status_code == 200
            assert resp_admin.json()["ok"] is True
            nonce = resp_admin.json()["nonce"]

            # Verify oauth_auth_requests record is bound to account_id = 1
            conn = dbm.connect(db_file)
            req_row = conn.execute("SELECT account_id FROM oauth_auth_requests WHERE nonce=?", (nonce,)).fetchone()
            assert req_row is not None
            assert req_row["account_id"] == 1
            conn.close()


# --- 3. MCP Tools Isolation & Readonly Tests -----------------------------------


def test_mcp_tools_account_theme_isolation_and_collaborator_gating(tmp_path: Path) -> None:
    db_file = tmp_path / "claire.db"
    conn = dbm.connect(db_file)
    dbm.init_db(conn)

    # 1. Accounts setup
    accounts_store.AccountStore(conn).complete_setup(1, username="adminuser", display_name="Admin")
    _add_member_account(conn, account_id=2, username="memberbob", status="active")

    # 2. Themes setup
    # Theme 0 (Admin default)
    dbm.insert_document(
        conn,
        Document(id="doc_admin_0", url="https://ex.com/a0", title="Admin Default Doc", raw_text="Admin default content", source_type="web", content_hash="h_a0"),
        theme_id=0,
    )
    conn.close()

    settings = Settings(
        CLAIRE_DB_PATH=str(db_file),
        CLAIRE_DATA_DIR=str(tmp_path),
        CLAIRE_MULTI_THEME=True,
    )
    tm = ThemeManager(settings)

    # Theme 1: Admin private theme
    t1 = _create_test_theme(tm, settings, "Admin Private Theme", account_id=1, is_public=False, is_collaborator_accessible=False)
    t1_s = tm.get_settings_for_theme(t1.id, settings)
    c1 = dbm.connect(t1_s.db_file)
    dbm.init_db(c1)
    dbm.insert_document(c1, Document(id="doc_admin_1", url="https://ex.com/a1", title="Admin Priv Doc", raw_text="Admin private content", source_type="web", content_hash="h_a1"), theme_id=t1.id)
    c1.close()

    # Theme 2: Member default theme (account_id = 2, is_default = True, is_public = False)
    t2 = _create_test_theme(tm, settings, "Bob Default Theme", account_id=2, is_default=True, is_public=False, is_collaborator_accessible=False)
    t2_s = tm.get_settings_for_theme(t2.id, settings)
    c2 = dbm.connect(t2_s.db_file)
    dbm.init_db(c2)
    dbm.insert_document(c2, Document(id="doc_bob_default", url="https://ex.com/b0", title="Bob Default Doc", raw_text="Bob default content", source_type="web", content_hash="h_b0"), theme_id=t2.id)
    c2.close()

    # Theme 3: Member collab-accessible theme (account_id = 2, is_collaborator_accessible = True, is_public = False)
    t3 = _create_test_theme(tm, settings, "Bob Collab Theme", account_id=2, is_default=False, is_public=False, is_collaborator_accessible=True)
    t3_s = tm.get_settings_for_theme(t3.id, settings)
    c3 = dbm.connect(t3_s.db_file)
    dbm.init_db(c3)
    dbm.insert_document(c3, Document(id="doc_bob_collab", url="https://ex.com/b1", title="Bob Collab Doc", raw_text="Bob collab content", source_type="web", content_hash="h_b1"), theme_id=t3.id)
    c3.close()

    # Theme 4: Member public theme (account_id = 2, is_public = True)
    t4 = _create_test_theme(tm, settings, "Bob Public Theme", account_id=2, is_default=False, is_public=True, is_collaborator_accessible=False)
    t4_s = tm.get_settings_for_theme(t4.id, settings)
    c4 = dbm.connect(t4_s.db_file)
    dbm.init_db(c4)
    dbm.insert_document(c4, Document(id="doc_bob_public", url="https://ex.com/b2", title="Bob Public Doc", raw_text="Bob public content", source_type="web", content_hash="h_b2"), theme_id=t4.id)
    c4.close()

    mcp_app = build_mcp_app(settings, theme_mgr=tm)

    with TestClient(mcp_app, base_url="http://localhost:8765") as client:
        # A. Account 1 Principal (Admin owner)
        p_admin = Principal(account_id=1, instance_role="admin", scope="owner", channel="bearer", credential_id="cred1")
        tok = current_principal.set(p_admin)
        try:
            # Can read own Theme 0
            r0 = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "document", "arguments": {"document_id": "doc_admin_0"}}})
            assert r0.status_code == 200
            data0 = json.loads(r0.json()["result"]["content"][0]["text"])
            assert data0["id"] == "doc_admin_0"

            # Cannot read Member's Theme 2 (returns theme not found)
            r_cross = client.post("/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "document", "arguments": {"document_id": "doc_bob_default", "theme": t2.id}}})
            assert r_cross.status_code == 200
            data_cross = json.loads(r_cross.json()["result"]["content"][0]["text"])
            assert data_cross == {"error": "theme not found"}
        finally:
            current_principal.reset(tok)

        # B. Account 2 Principal (Member readonly)
        p_member = Principal(account_id=2, instance_role="member", scope="readonly", channel="bearer", credential_id="cred2")
        tok = current_principal.set(p_member)
        try:
            # Default theme (theme=None) resolves to Bob's default theme (Theme 2)
            r_def = client.post("/mcp", json={"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "document", "arguments": {"document_id": "doc_bob_default"}}})
            assert r_def.status_code == 200
            data_def = json.loads(r_def.json()["result"]["content"][0]["text"])
            assert data_def["id"] == "doc_bob_default"

            # Cannot read Admin's Theme 0 (returns theme not found)
            r_admin_0 = client.post("/mcp", json={"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "document", "arguments": {"document_id": "doc_admin_0", "theme": 0}}})
            assert r_admin_0.status_code == 200
            assert json.loads(r_admin_0.json()["result"]["content"][0]["text"]) == {"error": "theme not found"}

            # Cross-theme doc resolution does not leak admin documents
            r_leak = client.post("/mcp", json={"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "document", "arguments": {"document_id": "doc_admin_1"}}})
            assert r_leak.status_code == 200
            assert json.loads(r_leak.json()["result"]["content"][0]["text"]) == {"error": "not found"}
        finally:
            current_principal.reset(tok)

        # C. Collaborator Principal (Member collaborator scope)
        p_collab = Principal(account_id=2, instance_role="member", scope="collaborator", channel="bearer", credential_id="cred3")
        tok = current_principal.set(p_collab)
        try:
            # Theme 2 is not public and not collaborator_accessible -> theme not found
            r_t2 = client.post("/mcp", json={"jsonrpc": "2.0", "id": 6, "method": "tools/call", "params": {"name": "document", "arguments": {"document_id": "doc_bob_default", "theme": t2.id}}})
            assert r_t2.status_code == 200
            assert json.loads(r_t2.json()["result"]["content"][0]["text"]) == {"error": "theme not found"}

            # Theme 3 is collaborator_accessible -> allowed!
            r_t3 = client.post("/mcp", json={"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": "document", "arguments": {"document_id": "doc_bob_collab", "theme": t3.id}}})
            assert r_t3.status_code == 200
            data_t3 = json.loads(r_t3.json()["result"]["content"][0]["text"])
            assert data_t3["id"] == "doc_bob_collab"

            # Theme 4 is public -> allowed!
            r_t4 = client.post("/mcp", json={"jsonrpc": "2.0", "id": 8, "method": "tools/call", "params": {"name": "document", "arguments": {"document_id": "doc_bob_public", "theme": t4.id}}})
            assert r_t4.status_code == 200
            data_t4 = json.loads(r_t4.json()["result"]["content"][0]["text"])
            assert data_t4["id"] == "doc_bob_public"
        finally:
            current_principal.reset(tok)


def test_mcp_tools_readonly_connection_and_allowed_hosts_port(tmp_path: Path) -> None:
    db_file = tmp_path / "claire.db"
    conn = dbm.connect(db_file)
    dbm.init_db(conn)
    conn.close()

    settings = Settings(
        CLAIRE_DB_PATH=str(db_file),
        CLAIRE_DATA_DIR=str(tmp_path),
        CLAIRE_PUBLIC_URL="http://bible.example.com:8766",
        CLAIRE_INJECT_PORT=8766,
    )

    # 1. Test allowed_hosts preservation in TransportSecuritySettings
    mcp_app = build_mcp_app(settings)

    # Allowed hosts should contain port-preserved host "bible.example.com:8766"
    with TestClient(mcp_app, base_url="http://bible.example.com:8766") as client:
        resp = client.post(
            "/mcp",
            headers={"Host": "bible.example.com:8766"},
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        )
        assert resp.status_code == 200
        assert "tools" in resp.json()["result"]

    # 2. Verify readonly connection: MCP connection cannot perform writes
    # Connect existing with readonly=True
    ro_conn = dbm.connect_existing(db_file, readonly=True)
    try:
        # PRAGMA query_only must be 1
        query_only = ro_conn.execute("PRAGMA query_only").fetchone()[0]
        assert query_only == 1

        # Attempting write operation raises OperationalError
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            ro_conn.execute("INSERT INTO themes (id, seq, label, account_id) VALUES (999, 999, 'Write Test', 1)")
    finally:
        ro_conn.close()


# --- 4. End-to-End (E2E) Multi-Account OAuth to MCP Test -----------------------


def test_e2e_multi_account_oauth_to_mcp_flow(tmp_path: Path) -> None:
    """Full E2E test: Account creation -> OAuth authorization -> Token issuance -> Isolated MCP tool execution."""
    db_file = tmp_path / "claire.db"
    conn = dbm.connect(db_file)
    dbm.init_db(conn)

    # Complete admin setup and create member
    accounts_store.AccountStore(conn).complete_setup(1, username="adminalice", display_name="Alice Admin")
    _add_member_account(conn, account_id=2, username="membercharlie", status="active")
    member_session = accounts_store.create_account_session(conn, account_id=2, scope="owner")

    # Seed Admin theme and document
    dbm.insert_document(
        conn,
        Document(id="doc_top_secret_admin", url="https://admin.internal/doc", title="Admin Classified", raw_text="Secret Admin Document", source_type="web", content_hash="hash_adm"),
        theme_id=0,
    )
    conn.close()

    settings = Settings(
        CLAIRE_DB_PATH=str(db_file),
        CLAIRE_DATA_DIR=str(tmp_path),
        CLAIRE_PUBLIC_URL="http://127.0.0.1:8765",
        CLAIRE_MULTI_THEME=True,
    )
    tm = ThemeManager(settings)

    # Member's private default theme
    t_charlie = _create_test_theme(tm, settings, "Charlie Knowledge", account_id=2, is_default=True, is_public=False)
    t_charlie_settings = tm.get_settings_for_theme(t_charlie.id, settings)
    c_charlie = dbm.connect(t_charlie_settings.db_file)
    dbm.init_db(c_charlie)
    dbm.insert_document(
        c_charlie,
        Document(id="doc_charlie_notes", url="https://charlie.org/notes", title="Charlie Research Notes", raw_text="Charlie Quantum Computing Notes", source_type="notes", content_hash="hash_cha"),
        theme_id=t_charlie.id,
    )
    c_charlie.close()

    host_roles = HostRoles(
        scheme="http",
        app="127.0.0.1:8765",
        usercontent=None,
        media=None,
        site_domain=None,
    )
    config = WebRuntimeConfig(
        environment="development",
        public_origin="http://127.0.0.1:8765",
        expected_authority="127.0.0.1:8765",
        cors_allowed_origins=frozenset(),
        secure_cookie=False,
        anonymous_readonly=False,
        owner_token="",
        readonly_token="",
        collaborator_token="",
        db_file=db_file,
        data_dir=tmp_path,
        trusted_proxies=(),
        host_roles=host_roles,
    )

    mcp_subapp = build_mcp_app(settings, theme_mgr=tm)

    async def mcp_handler(request: Request) -> Response:
        # Simulate AuthenticationMiddleware placing Principal from oauth token in request.state
        auth_header = request.headers.get("authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:].strip()
            c = dbm.connect_existing(db_file, readonly=True)
            try:
                binding = accounts_store.validate_oauth_binding(c, token)
                if binding is not None:
                    request.state.claire_principal = Principal(
                        account_id=binding.account_id,
                        instance_role="member" if binding.account_id > 1 else "admin",
                        scope=binding.scope,  # type: ignore
                        channel="bearer",
                        credential_id=binding.credential_id,
                    )
            finally:
                c.close()

        res_meta: dict = {}
        chunks: list[bytes] = []

        async def send(message: dict) -> None:
            if message["type"] == "http.response.start":
                res_meta["status"] = message["status"]
            elif message["type"] == "http.response.body":
                chunks.append(message.get("body", b""))

        await mcp_subapp(request.scope, request.receive, send)
        return Response(content=b"".join(chunks), status_code=res_meta.get("status", 200))

    routes = [
        Route("/oauth/register", handle_register, methods=["POST"]),
        Route("/oauth/authorize", handle_authorize, methods=["GET", "POST"]),
        Route("/oauth/token", handle_token, methods=["POST"]),
        Route("/mcp", mcp_handler, methods=["GET", "POST"]),
    ]
    app = Starlette(routes=routes)
    app.state.runtime_config = config
    app.state.settings = settings
    app = CORSPolicyMiddleware(app, config)

    with TestClient(mcp_subapp), TestClient(app, base_url="http://127.0.0.1:8765") as client:
        # 1. Register Client (DCR)
        reg = client.post(
            "/oauth/register",
            json={"client_name": "Claude Desktop Integration", "redirect_uris": ["https://claude.ai/cb"]},
        ).json()
        client_id = reg["client_id"]
        client_secret = reg["client_secret"]

        # 2. Authorize while logged in as member Charlie (account_id = 2)
        verifier = "e2e_verifier_" + secrets.token_urlsafe(32)
        digest = hashlib.sha256(verifier.encode("ascii")).digest()
        challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")

        auth_resp = client.post(
            "/oauth/authorize",
            data={
                "client_id": client_id,
                "redirect_uri": "https://claude.ai/cb",
                "state": "e2e_state",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "scope": "readonly",
                "action": "approve",
            },
            cookies={"claire_session": member_session},
            headers={"Origin": "http://127.0.0.1:8765", "Sec-Fetch-Site": "same-origin"},
            follow_redirects=False,
        )
        assert auth_resp.status_code == 302
        code = auth_resp.headers["Location"].split("code=")[1].split("&")[0]

        # 3. Exchange code for access token
        token_resp = client.post(
            "/oauth/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": "https://claude.ai/cb",
                "code_verifier": verifier,
            },
        )
        assert token_resp.status_code == 200
        access_token = token_resp.json()["access_token"]

        # 4. Access MCP tool with the member's OAuth token
        # A. Query Charlie's document (theme=None resolves to Charlie's default theme) -> Found!
        mcp_res = client.post(
            "/mcp",
            headers={"Authorization": f"Bearer {access_token}"},
            json={"jsonrpc": "2.0", "id": 100, "method": "tools/call", "params": {"name": "document", "arguments": {"document_id": "doc_charlie_notes"}}},
        )
        assert mcp_res.status_code == 200
        doc_data = json.loads(mcp_res.json()["result"]["content"][0]["text"])
        assert doc_data["id"] == "doc_charlie_notes"
        assert doc_data["title"] == "Charlie Research Notes"

        # B. Attempt to access Admin's classified document (doc_top_secret_admin) -> Blocked!
        mcp_admin_res = client.post(
            "/mcp",
            headers={"Authorization": f"Bearer {access_token}"},
            json={"jsonrpc": "2.0", "id": 101, "method": "tools/call", "params": {"name": "document", "arguments": {"document_id": "doc_top_secret_admin"}}},
        )
        assert mcp_admin_res.status_code == 200
        admin_doc_data = json.loads(mcp_admin_res.json()["result"]["content"][0]["text"])
        assert admin_doc_data == {"error": "not found"}

        # C. Attempt to explicitly query Admin's Theme 0 -> Theme Not Found!
        mcp_theme0_res = client.post(
            "/mcp",
            headers={"Authorization": f"Bearer {access_token}"},
            json={"jsonrpc": "2.0", "id": 102, "method": "tools/call", "params": {"name": "document", "arguments": {"document_id": "doc_top_secret_admin", "theme": 0}}},
        )
        assert mcp_theme0_res.status_code == 200
        theme0_data = json.loads(mcp_theme0_res.json()["result"]["content"][0]["text"])
        assert theme0_data == {"error": "theme not found"}
