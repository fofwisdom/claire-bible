"""Tests for OAuth 2.1 authorization server, discovery, and MCP client integration."""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from starlette.testclient import TestClient

from claire.api import server
from claire.store import db as dbm


@dataclass
class StubSettings:
    db_file: Path
    data_dir: Path
    environment: str = "development"
    public_url: str = "http://127.0.0.1:8765"
    inject_host: str = "127.0.0.1"
    inject_port: int = 8765
    inject_token: str = "owner-" + ("o" * 32)
    readonly_token: str = "readonly-" + ("r" * 32)
    cors_allowed_origins: str = ""
    anonymous_readonly: bool = False
    effective_provider: str = "mock"
    telegram_bot_token: str = ""
    telegram_owner_chat_id: int = 0
    allowed_user_ids: set[int] = None  # type: ignore

    def __post_init__(self) -> None:
        if self.allowed_user_ids is None:
            self.allowed_user_ids = set()


class StubService:
    def __init__(self) -> None:
        self.provider = SimpleNamespace(name="stub")


def _settings(tmp_path: Path) -> StubSettings:
    db_file = tmp_path / "test.db"
    conn = dbm.connect(db_file)
    dbm.init_db(conn)
    conn.close()
    return StubSettings(
        db_file=db_file,
        data_dir=tmp_path / "data",
    )


def _app(s: StubSettings):
    return server.create_app(s, StubService())


def test_oauth_protected_resource_metadata(tmp_path: Path) -> None:
    s = _settings(tmp_path)
    app = _app(s)
    with TestClient(app, base_url=s.public_url) as client:
        resp = client.get("/.well-known/oauth-protected-resource")
        assert resp.status_code == 200
        data = resp.json()
        assert data["resource"] == "http://127.0.0.1:8765/mcp"
        assert "http://127.0.0.1:8765" in data["authorization_servers"]
        assert data["scopes_supported"] == ["readonly", "write"]
        assert "header" in data["bearer_methods_supported"]


def test_oauth_authorization_server_metadata(tmp_path: Path) -> None:
    s = _settings(tmp_path)
    app = _app(s)
    with TestClient(app, base_url=s.public_url) as client:
        resp = client.get("/.well-known/oauth-authorization-server")
        assert resp.status_code == 200
        data = resp.json()
        assert data["issuer"] == "http://127.0.0.1:8765"
        assert data["authorization_endpoint"] == "http://127.0.0.1:8765/oauth/authorize"
        assert data["token_endpoint"] == "http://127.0.0.1:8765/oauth/token"
        assert data["registration_endpoint"] == "http://127.0.0.1:8765/oauth/register"
        assert data["revocation_endpoint"] == "http://127.0.0.1:8765/oauth/revoke"
        assert data["device_authorization_endpoint"] == "http://127.0.0.1:8765/oauth/device_authorization"
        assert data["scopes_supported"] == ["readonly", "write"]
        assert "code" in data["response_types_supported"]
        assert "authorization_code" in data["grant_types_supported"]
        assert "refresh_token" in data["grant_types_supported"]
        assert "urn:ietf:params:oauth:grant-type:device_code" in data["grant_types_supported"]
        assert data["code_challenge_methods_supported"] == ["S256"]


def test_mcp_unauthenticated_includes_resource_metadata(tmp_path: Path) -> None:
    s = _settings(tmp_path)
    app = _app(s)
    with TestClient(app, base_url=s.public_url) as client:
        resp = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        assert resp.status_code == 401
        www_auth = resp.headers.get("WWW-Authenticate", "")
        assert 'resource_metadata="http://127.0.0.1:8765/.well-known/oauth-protected-resource"' in www_auth


def test_dynamic_client_registration(tmp_path: Path) -> None:
    s = _settings(tmp_path)
    app = _app(s)
    with TestClient(app, base_url=s.public_url) as client:
        resp = client.post(
            "/oauth/register",
            json={
                "client_name": "Google Gemini Spark",
                "redirect_uris": ["https://gemini.google.com/oauth/callback"],
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["client_id"].startswith("claire_mcp_")
        assert len(data["client_secret"]) >= 32
        assert data["client_name"] == "Google Gemini Spark"
        assert data["redirect_uris"] == ["https://gemini.google.com/oauth/callback"]


def test_authorization_code_flow_with_pkce_and_mcp_access(tmp_path: Path) -> None:
    s = _settings(tmp_path)
    app = _app(s)

    with TestClient(app, base_url=s.public_url) as client:
        # 1. Register Client (DCR)
        reg_resp = client.post(
            "/oauth/register",
            json={
                "client_name": "Gemini Test Client",
                "redirect_uris": ["https://example.com/callback"],
            },
        )
        assert reg_resp.status_code == 201
        client_id = reg_resp.json()["client_id"]

        # 2. PKCE code_verifier and code_challenge
        verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
        digest = hashlib.sha256(verifier.encode("ascii")).digest()
        challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")

        # 3. Unauthenticated GET /oauth/authorize -> 303 /login?tx=... (Telegram-free)
        auth_url = (
            f"/oauth/authorize?response_type=code&client_id={client_id}"
            f"&redirect_uri=https://example.com/callback&state=mystate"
            f"&code_challenge={challenge}&code_challenge_method=S256"
        )
        get_unauth = client.get(auth_url, follow_redirects=False)
        assert get_unauth.status_code == 303
        login_loc = get_unauth.headers["location"]
        assert login_loc.startswith("/login?tx=")
        tx_id = login_loc.split("tx=", 1)[1]

        # 4. Complete setup with username/password and sign in via /login?tx=...
        from claire.store import accounts as accounts_store
        from claire.store import auth_db

        conn = dbm.connect(s.db_file)
        accounts_store.AccountStore(conn).complete_setup(1, username="adminuser", display_name="Admin")
        conn.close()
        auth = auth_db.connect_auth_db(auth_db.auth_db_path(s.data_dir))
        auth_db.set_password(auth, 1, "Pass1234!secret", username="adminuser")
        auth.close()

        login_get = client.get(login_loc)
        assert login_get.status_code == 200
        assert 'name="tx"' in login_get.text
        assert 'autocomplete="username"' in login_get.text
        assert 'autocomplete="current-password"' in login_get.text

        login_post = client.post(
            "/login",
            data={"username": "adminuser", "password": "Pass1234!secret", "tx": tx_id},
            headers={"Origin": s.public_url},
            follow_redirects=False,
        )
        assert login_post.status_code == 303
        assert login_post.headers["location"] == f"/oauth/authorize?tx={tx_id}"

        consent_resp = client.get(login_post.headers["location"])
        assert consent_resp.status_code == 200
        assert "/favicon.svg" in consent_resp.text
        assert "<script" not in consent_resp.text.lower()
        assert "💎" not in consent_resp.text

        # 5. POST /oauth/authorize (Approve via session cookie + tx)
        post_auth = client.post(
            "/oauth/authorize",
            data={
                "tx": tx_id,
                "action": "approve",
            },
            headers={"Origin": s.public_url, "Sec-Fetch-Site": "same-origin"},
            follow_redirects=False,
        )
        assert post_auth.status_code == 302
        location = post_auth.headers["Location"]
        assert location.startswith("https://example.com/callback?")
        assert "state=mystate" in location
        assert "code=" in location

        # Extract code
        from urllib.parse import parse_qs, urlparse

        parsed = urlparse(location)
        qs = parse_qs(parsed.query)
        code = qs["code"][0]

        # 6. POST /oauth/token (Exchange code with PKCE verifier)
        token_resp = client.post(
            "/oauth/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": client_id,
                "redirect_uri": "https://example.com/callback",
                "code_verifier": verifier,
            },
        )
        assert token_resp.status_code == 200
        token_data = token_resp.json()
        assert "access_token" in token_data
        assert "refresh_token" in token_data
        assert token_data["token_type"] == "Bearer"
        assert token_data["scope"] == "readonly"
        access_token = token_data["access_token"]
        refresh_token = token_data["refresh_token"]

        # 7. Access /mcp as the external OAuth client (Bearer only, no browser session cookie)
        client.cookies.clear()
        mcp_resp = client.post(
            "/mcp",
            headers={"Authorization": f"Bearer {access_token}"},
            json={"jsonrpc": "2.0", "id": 10, "method": "tools/list"},
        )
        assert mcp_resp.status_code == 200
        mcp_data = mcp_resp.json()
        assert "tools" in mcp_data["result"]
        tool_names = [t["name"] for t in mcp_data["result"]["tools"]]
        assert "entity_search" in tool_names

        # 8. CRITICAL: Test Session Isolation!
        # When telegram /webro or /web is called, it deletes old auth_sessions.
        # But OAuth token is isolated in oauth_tokens table, so it MUST remain valid!
        conn = dbm.connect(s.db_file)
        dbm.create_session(conn, scope="readonly")  # Wipes auth_sessions for readonly
        dbm.create_session(conn, scope="owner")  # Wipes auth_sessions for owner
        conn.close()

        # MCP access MUST STILL WORK with the isolated OAuth access_token!
        mcp_resp2 = client.post(
            "/mcp",
            headers={"Authorization": f"Bearer {access_token}"},
            json={"jsonrpc": "2.0", "id": 11, "method": "tools/list"},
        )
        assert mcp_resp2.status_code == 200

        # 9. Test Refresh Token Rotation
        ref_resp = client.post(
            "/oauth/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": client_id,
            },
        )
        assert ref_resp.status_code == 200
        ref_data = ref_resp.json()
        new_access = ref_data["access_token"]
        assert new_access != access_token

        # New token works
        mcp_resp3 = client.post(
            "/mcp",
            headers={"Authorization": f"Bearer {new_access}"},
            json={"jsonrpc": "2.0", "id": 12, "method": "tools/list"},
        )
        assert mcp_resp3.status_code == 200


def test_telegram_push_authorization_poll(tmp_path: Path) -> None:
    s = _settings(tmp_path)
    s.telegram_bot_token = "fake_bot_token"
    s.allowed_user_ids = {12345}

    conn = dbm.connect(s.db_file)
    dbm.register_oauth_client(
        conn,
        client_id="test_client",
        client_secret="secret",
        client_name="Test Client",
        redirect_uris=["https://example.com/cb"],
    )
    conn.close()

    app = _app(s)

    mock_resp = SimpleNamespace(status_code=200)
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_resp):
        with TestClient(app, base_url=s.public_url) as client:
            # Request telegram push
            push_resp = client.post(
                "/oauth/authorize/telegram-push",
                json={
                    "client_id": "test_client",
                    "redirect_uri": "https://example.com/cb",
                    "code_challenge": "abc",
                    "code_challenge_method": "S256",
                    "state": "s123",
                },
            )
            assert push_resp.status_code == 200
            nonce = push_resp.json()["nonce"]

            # Poll status -> pending
            poll_resp = client.get(f"/oauth/authorize/poll?nonce={nonce}")
            assert poll_resp.status_code == 200
            assert poll_resp.json()["status"] == "pending"

            # Simulate owner clicking approve in Telegram bot
            conn = dbm.connect(s.db_file)
            code = dbm.approve_oauth_auth_request(conn, nonce)
            conn.close()
            assert code is not None

            # Poll status again -> approved
            poll_resp2 = client.get(f"/oauth/authorize/poll?nonce={nonce}")
            assert poll_resp2.status_code == 200
            data = poll_resp2.json()
            assert data["status"] == "approved"
            assert f"code={code}" in data["redirect_url"]
            assert "state=s123" in data["redirect_url"]


def test_telegram_push_security_error_masking(tmp_path: Path) -> None:
    s = _settings(tmp_path)
    app = _app(s)

    with TestClient(app, base_url=s.public_url) as client:
        # 1. Unregistered client_id -> 400 invalid_request (no internal info exposed)
        resp1 = client.post(
            "/oauth/authorize/telegram-push",
            json={
                "client_id": "unregistered_client",
                "redirect_uri": "https://example.com/cb",
            },
        )
        assert resp1.status_code == 400
        assert resp1.json() == {"ok": False, "error": "invalid_request"}

        # Register a valid client for subsequent tests
        conn = dbm.connect(s.db_file)
        dbm.register_oauth_client(
            conn,
            client_id="valid_client",
            client_secret="secret",
            client_name="Valid Client",
            redirect_uris=["https://example.com/cb"],
        )
        conn.close()

        # 2. Unconfigured telegram bot token or recipients -> 503 temporarily_unavailable
        resp2 = client.post(
            "/oauth/authorize/telegram-push",
            json={
                "client_id": "valid_client",
                "redirect_uri": "https://example.com/cb",
            },
        )
        assert resp2.status_code == 503
        assert resp2.json() == {"ok": False, "error": "temporarily_unavailable"}

        # Verify no orphan auth request record was created in DB
        conn = dbm.connect(s.db_file)
        count = conn.execute("SELECT COUNT(*) FROM oauth_auth_requests").fetchone()[0]
        conn.close()
        assert count == 0

        # 3. Telegram API delivery failure -> 503 temporarily_unavailable
        s.telegram_bot_token = "fake_bot_token"
        s.allowed_user_ids = {12345}
        mock_fail_resp = SimpleNamespace(status_code=400)
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_fail_resp):
            resp3 = client.post(
                "/oauth/authorize/telegram-push",
                json={
                    "client_id": "valid_client",
                    "redirect_uri": "https://example.com/cb",
                },
            )
            assert resp3.status_code == 503
            assert resp3.json() == {"ok": False, "error": "temporarily_unavailable"}

            # Verify no orphan auth request record was created on delivery failure
            conn = dbm.connect(s.db_file)
            count = conn.execute("SELECT COUNT(*) FROM oauth_auth_requests").fetchone()[0]
            conn.close()
            assert count == 0


def test_oauth_s256_only_redirect_uri_null_and_mcp_write_scope_unit(tmp_path: Path) -> None:
    from claire.api.mcp_tools import current_principal, require_mcp_write_scope
    from claire.api.oauth import _is_safe_redirect_uri, _verify_pkce
    from claire.api.principal import ANONYMOUS, Principal

    assert _verify_pkce("secret-verifier", "secret-verifier", "plain") is False
    assert _is_safe_redirect_uri("null") is False
    assert _is_safe_redirect_uri("https://null/cb") is False
    assert _is_safe_redirect_uri("https://example.com/cb") is True

    assert require_mcp_write_scope(ANONYMOUS) == {"error": "account_required"}
    ro = Principal(account_id=2, server_role="member", scope="readonly", channel="bearer", credential_id="c1")
    assert require_mcp_write_scope(ro) == {"error": "scope_insufficient", "required_scope": "write"}
    wr = Principal(account_id=2, server_role="member", scope="write", channel="bearer", credential_id="c2")
    assert require_mcp_write_scope(wr) is None
    token = current_principal.set(ro)
    try:
        assert require_mcp_write_scope() == {"error": "scope_insufficient", "required_scope": "write"}
    finally:
        current_principal.reset(token)

    s = _settings(tmp_path)
    with TestClient(_app(s), base_url=s.public_url) as client:
        reg = client.post("/oauth/register", json={"client_name": "PKCE App", "redirect_uris": ["https://example.com/cb"]}).json()
        resp = client.get(
            f"/oauth/authorize?response_type=code&client_id={reg['client_id']}&redirect_uri=https://example.com/cb&code_challenge=plainval&code_challenge_method=plain"
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_request"


def test_oauth_write_step_up_scope_rotation_reuse_and_revocation_e2e(tmp_path: Path) -> None:
    import time
    from urllib.parse import parse_qs, urlparse
    from claire.store import accounts as accounts_store
    from claire.store import auth_db

    s = _settings(tmp_path)
    app = _app(s)
    with TestClient(app, base_url=s.public_url) as client:
        conn = dbm.connect(s.db_file)
        accounts_store.AccountStore(conn).complete_setup(1, username="adminuser", display_name="Admin")
        member = accounts_store.AccountStore(conn).create_member(username="alice", display_name="Alice")
        stale_session = accounts_store.create_account_session(conn, account_id=member.id, scope="owner", now=time.time() - 3600)
        conn.close()
        auth = auth_db.connect_auth_db(auth_db.auth_db_path(s.data_dir))
        auth_db.set_password(auth, member.id, "StrongSecret#987!", username="alice")
        auth.close()

        reg = client.post("/oauth/register", json={"client_name": "Antigravity", "redirect_uris": ["http://127.0.0.1:9876/cb"]}).json()
        cid = reg["client_id"]
        verifier = "track-c-verifier-1234567890-abcdefghijk"
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).decode("ascii").rstrip("=")

        client.cookies.set("claire_session", stale_session, domain="127.0.0.1")
        consent = client.get(
            f"/oauth/authorize?response_type=code&client_id={cid}&redirect_uri=http://127.0.0.1:9876/cb&state=s1&code_challenge={challenge}&code_challenge_method=S256&scope=readonly%20write"
        )
        assert consent.status_code == 200
        assert 'name="step_up_password"' in consent.text
        assert "data-allow-credentials=" in consent.text

        # Stale session without step_up_password fails with 403
        denied_stepup = client.post(
            "/oauth/authorize",
            data={
                "client_id": cid,
                "redirect_uri": "http://127.0.0.1:9876/cb",
                "state": "s1",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "scope": "readonly write",
                "action": "approve",
            },
            headers={"Origin": s.public_url},
        )
        assert denied_stepup.status_code == 403

        # Valid inline password completes step-up and issues code
        ok_auth = client.post(
            "/oauth/authorize",
            data={
                "client_id": cid,
                "redirect_uri": "http://127.0.0.1:9876/cb",
                "state": "s1",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "scope": "readonly write",
                "action": "approve",
                "step_up_password": "StrongSecret#987!",
            },
            headers={"Origin": s.public_url},
            follow_redirects=False,
        )
        assert ok_auth.status_code == 302
        code = parse_qs(urlparse(ok_auth.headers["location"]).query)["code"][0]

        tok1 = client.post(
            "/oauth/token",
            data={"grant_type": "authorization_code", "code": code, "client_id": cid, "redirect_uri": "http://127.0.0.1:9876/cb", "code_verifier": verifier},
        )
        assert tok1.status_code == 200
        t1 = tok1.json()
        assert t1["scope"] == "readonly write"

        # Verify grant manager API sees the grant
        grants_resp = client.get("/api/me/oauth/grants")
        assert grants_resp.status_code == 200
        assert len(grants_resp.json()["grants"]) == 1
        assert grants_resp.json()["grants"][0]["granted_scope"] == "readonly write"

        # Downscope refresh (readonly write -> readonly), then confirm re-escalation is blocked
        tok2 = client.post(
            "/oauth/token",
            data={"grant_type": "refresh_token", "refresh_token": t1["refresh_token"], "client_id": cid, "scope": "readonly"},
        )
        assert tok2.status_code == 200
        t2 = tok2.json()
        assert t2["scope"] == "readonly"

        esc = client.post(
            "/oauth/token",
            data={"grant_type": "refresh_token", "refresh_token": t2["refresh_token"], "client_id": cid, "scope": "readonly write"},
        )
        assert esc.status_code == 400
        assert esc.json()["error"] == "invalid_scope"

        # Reuse detection: presenting t1["refresh_token"] (already rotated) revokes the whole family (t2 is now dead!)
        reuse = client.post(
            "/oauth/token",
            data={"grant_type": "refresh_token", "refresh_token": t1["refresh_token"], "client_id": cid},
        )
        assert reuse.status_code == 400
        assert reuse.json()["error"] == "invalid_grant"
        client.cookies.clear()
        assert client.post("/mcp", headers={"Authorization": f"Bearer {t2['access_token']}"}, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).status_code == 401

        # Fresh token + RFC 7009 revoke + grant manager DELETE
        conn = dbm.connect(s.db_file)
        a3, r3 = dbm.create_oauth_token(conn, client_id=cid, scope="readonly", account_id=member.id)
        conn.close()
        assert client.post("/oauth/revoke", data={"token": r3, "client_id": cid}).status_code == 200
        assert client.post("/mcp", headers={"Authorization": f"Bearer {a3}"}, json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"}).status_code == 401

        conn = dbm.connect(s.db_file)
        a4, _r4 = dbm.create_oauth_token(conn, client_id=cid, scope="readonly", account_id=member.id)
        conn.close()
        client.cookies.set("claire_session", stale_session, domain="127.0.0.1")
        g_list = client.get("/api/me/oauth/grants").json()["grants"]
        assert len(g_list) == 1
        del_resp = client.delete(f"/api/me/oauth/grants?grant_id={g_list[0]['grant_id']}", headers={"Origin": s.public_url})
        assert del_resp.status_code == 200 and del_resp.json()["revoked"] == 1
        client.cookies.clear()
        assert client.post("/mcp", headers={"Authorization": f"Bearer {a4}"}, json={"jsonrpc": "2.0", "id": 3, "method": "tools/list"}).status_code == 401


def test_oauth_device_authorization_grant_and_modern_web_headers_e2e(tmp_path: Path) -> None:
    from claire.store import accounts as accounts_store

    s = _settings(tmp_path)
    with TestClient(_app(s), base_url=s.public_url) as client:
        conn = dbm.connect(s.db_file)
        accounts_store.AccountStore(conn).complete_setup(1, username="adminuser", display_name="Admin")
        session_tok = accounts_store.create_account_session(conn, account_id=1, scope="owner")
        conn.close()

        reg = client.post("/oauth/register", json={"client_name": "Headless CLI", "redirect_uris": ["http://127.0.0.1:9000/cb"]}).json()
        cid = reg["client_id"]
        dev = client.post("/oauth/device_authorization", data={"client_id": cid, "scope": "readonly write"})
        assert dev.status_code == 200
        d = dev.json()
        assert d["verification_uri"] == f"{s.public_url}/device"
        assert d["user_code"] in d["verification_uri_complete"]

        pending1 = client.post("/oauth/token", data={"grant_type": "urn:ietf:params:oauth:grant-type:device_code", "device_code": d["device_code"], "client_id": cid})
        assert pending1.status_code == 400 and pending1.json()["error"] == "authorization_pending"
        pending2 = client.post("/oauth/token", data={"grant_type": "urn:ietf:params:oauth:grant-type:device_code", "device_code": d["device_code"], "client_id": cid})
        assert pending2.status_code == 400 and pending2.json()["error"] == "slow_down"

        client.cookies.set("claire_session", session_tok)
        page = client.get(f"/device?user_code={d['user_code']}")
        assert page.status_code == 200
        assert "<script" not in page.text.lower()
        assert d["user_code"] in page.text

        approve = client.post("/device", data={"user_code": d["user_code"], "action": "approve"}, headers={"Origin": s.public_url})
        assert approve.status_code == 200

        tok = client.post("/oauth/token", data={"grant_type": "urn:ietf:params:oauth:grant-type:device_code", "device_code": d["device_code"], "client_id": cid})
        assert tok.status_code == 200
        assert tok.json()["scope"] == "readonly write"

        # Modern Web Guidance: opaque null origin rejected, Sec-Fetch-Site: none with matching Origin allowed on logout + Clear-Site-Data
        assert client.get("/health", headers={"Origin": "null"}).status_code == 403
        logout = client.post("/logout", headers={"Origin": s.public_url, "Sec-Fetch-Site": "none"}, follow_redirects=False)
        assert logout.status_code == 303
        assert logout.headers.get("clear-site-data") == '"cookies", "storage", "cache"'


def test_ae3_manual_token_deprecation_headers_and_retirement_toggle(tmp_path: Path) -> None:
    from claire.store import accounts as accounts_store
    from claire.store.token_store import get_token_store

    s = _settings(tmp_path)
    with TestClient(_app(s), base_url=s.public_url) as client:
        conn = dbm.connect(s.db_file)
        accounts_store.AccountStore(conn).complete_setup(1, username="adminuser", display_name="Admin")
        session_tok = accounts_store.create_account_session(conn, account_id=1, scope="owner")
        conn.close()

        client.cookies.set("claire_session", session_tok)
        created = client.post("/tokens", json={"name": "legacy-script", "scope": "readonly"}, headers={"Origin": s.public_url})
        assert created.status_code == 201
        assert created.headers.get("deprecation") == "true"
        assert 'rel="successor-version"' in (created.headers.get("link") or "")
        assert created.json()["deprecated"] is True
        raw_tok = created.json()["token"]["token"]

        client.cookies.clear()
        api_resp = client.post("/mcp", headers={"Authorization": f"Bearer {raw_tok}"}, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        assert api_resp.status_code == 200
        assert api_resp.headers.get("deprecation") == "true"
        assert 'rel="successor-version"' in (api_resp.headers.get("link") or "")

        client.cookies.set("claire_session", session_tok)
        store = get_token_store(data_dir=s.data_dir)
        store.set_retired(True)
        assert store.validate_api_token(raw_tok) is None
        assert client.post("/tokens", json={"name": "blocked"}, headers={"Origin": s.public_url}).status_code == 410
        client.cookies.clear()
        assert client.post("/mcp", headers={"Authorization": f"Bearer {raw_tok}"}, json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"}).status_code == 401


def test_oauth_client_credentials_redirect_safety_and_token_store_reload(tmp_path: Path) -> None:
    """Verify redirect URI control-char/scheme checks, mismatched Basic/body credentials, and TokenStore disk reload."""
    from starlette.requests import Request
    from claire.api.oauth import ClientCredentialConflictError, _extract_client_credentials, _is_safe_redirect_uri
    from claire.store.token_store import TokenStore

    assert _is_safe_redirect_uri("https://example.com\nLocation: evil") is False
    assert _is_safe_redirect_uri("java\tscript:alert(1)") is False
    assert _is_safe_redirect_uri("blob:https://example.com/1") is False
    assert _is_safe_redirect_uri("about:blank") is False
    assert _is_safe_redirect_uri("file:///etc/passwd") is False
    assert _is_safe_redirect_uri("http://null:8080/cb") is False
    assert _is_safe_redirect_uri("antigravity://oauth/callback") is True

    basic_val = base64.b64encode(b"client_a:secret_a").decode("ascii")
    req = Request({"type": "http", "method": "POST", "path": "/oauth/token", "headers": [(b"authorization", f"Basic {basic_val}".encode("ascii"))]})
    with pytest.raises(ClientCredentialConflictError):
        _extract_client_credentials(req, {"client_id": "client_b"})
    with pytest.raises(ClientCredentialConflictError):
        _extract_client_credentials(req, {"client_id": "client_a", "client_secret": "other_secret"})
    bad_hdr_req = Request({"type": "http", "method": "POST", "path": "/oauth/token", "headers": [(b"authorization", b"Basic not-valid-base64!!!")]})
    with pytest.raises(ClientCredentialConflictError):
        _extract_client_credentials(bad_hdr_req, {})
    assert _extract_client_credentials(req, {"client_id": "client_a"}) == ("client_a", "secret_a")

    store = TokenStore(data_dir=tmp_path)
    store.set_retired(True)
    assert store.is_retired() is True
    store.storage_path.unlink()
    assert store.is_retired() is False


def test_oauth_tx_idp_handoff_csrf_and_device_next_e2e(tmp_path: Path) -> None:
    """Verify Telegram-free /login?tx= IdP handoff, Login-CSRF binding, stale tx non-hijack, and PKCE exchange."""
    from urllib.parse import parse_qs, urlparse
    from claire.api.oidc import ExternalIdentity, IdpProviderMeta, OidcClient
    from claire.store import accounts as accounts_store
    from claire.store import auth_db

    s = _settings(tmp_path)
    conn = dbm.connect(s.db_file)
    accounts_store.AccountStore(conn).complete_setup(1, username="adminuser", display_name="Admin")
    alice = accounts_store.AccountStore(conn).create_member(username="alice", display_name="Alice")
    conn.close()

    auth_db.save_idp_providers(
        s.data_dir,
        [
            IdpProviderMeta(
                id="google",
                type="oidc",
                display_name="Google",
                client_id="mock-google-client",
                enabled=True,
                allow_signup=False,
                issuer="https://accounts.google.com",
            )
        ],
    )
    auth_conn = auth_db.connect_auth_db(auth_db.auth_db_path(s.data_dir))
    auth_db.link_account_identity(auth_conn, iss="https://accounts.google.com", sub="alice-sub-1", account_id=alice.id, provider_id="google")
    auth_conn.close()

    with patch.object(OidcClient, "create_authorization_url", new_callable=AsyncMock) as mock_auth_url, \
         patch.object(OidcClient, "exchange_code", new_callable=AsyncMock) as mock_exchange, \
         patch.object(OidcClient, "verify_id_token", new_callable=AsyncMock) as mock_verify:
        mock_auth_url.return_value = "https://accounts.google.com/o/oauth2/v2/auth?state=mock"
        mock_exchange.return_value = {"id_token": "mock.jwt.token"}
        mock_verify.return_value = ExternalIdentity(iss="https://accounts.google.com", sub="alice-sub-1", email="alice@example.com", email_verified=True, display_name="Alice")

        with TestClient(_app(s), base_url=s.public_url) as client:
            reg = client.post("/oauth/register", json={"client_name": "Antigravity CLI", "redirect_uris": ["http://127.0.0.1:9911/cb"]}).json()
            cid = reg["client_id"]
            verifier = "idp-tx-verifier-1234567890-abcdefghijkl"
            challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).decode("ascii").rstrip("=")

            unauth = client.get(f"/oauth/authorize?response_type=code&client_id={cid}&redirect_uri=http://127.0.0.1:9911/cb&state=st-idp&code_challenge={challenge}&code_challenge_method=S256&scope=readonly%20write", follow_redirects=False)
            assert unauth.status_code == 303
            login_loc = unauth.headers["location"]
            assert login_loc.startswith("/login?tx=")
            tx_id = parse_qs(urlparse(login_loc).query)["tx"][0]

            login_page = client.get(login_loc)
            assert login_page.status_code == 200
            assert "<script" not in login_page.text.lower()
            assert f'/auth/oidc/google/start?tx={tx_id}' in login_page.text

            # Reject mismatched tx on /login POST and /auth/oidc/{id}/start.
            forged_login = client.post("/login", data={"username": "alice", "password": "wrong", "tx": "forged-tx"}, headers={"Origin": s.public_url})
            assert forged_login.status_code == 403
            forged_oidc = client.get("/auth/oidc/google/start?tx=forged-tx", follow_redirects=False)
            assert forged_oidc.status_code == 400

            # Normal IdP login without ?tx= must not be hijacked by a leftover claire_oauth_tx cookie.
            normal_start = client.get("/auth/oidc/google/start", follow_redirects=False)
            assert normal_start.status_code == 302
            normal_cookie_key = client.cookies.get("claire_oidc_state")
            auth_conn = auth_db.connect_auth_db(auth_db.auth_db_path(s.data_dir))
            normal_state = auth_conn.execute("SELECT state FROM oidc_states WHERE key=?", (normal_cookie_key,)).fetchone()["state"]
            auth_conn.close()
            normal_cb = client.get(f"/auth/oidc/google/callback?state={normal_state}&code=mock_code", follow_redirects=False)
            assert normal_cb.status_code == 302
            assert normal_cb.headers["location"] == "/"
            saved_tx_cookie = client.cookies.get("claire_oauth_tx")
            client.cookies.clear()
            client.cookies.set("claire_oauth_tx", saved_tx_cookie, domain="127.0.0.1")

            # Start IdP login with ?tx= and verify Login-CSRF mismatch rejection on callback.
            tx_start = client.get(f"/auth/oidc/google/start?tx={tx_id}", follow_redirects=False)
            assert tx_start.status_code == 302
            tx_cookie_key = client.cookies.get("claire_oidc_state")
            auth_conn = auth_db.connect_auth_db(auth_db.auth_db_path(s.data_dir))
            tx_state_bad = auth_conn.execute("SELECT state FROM oidc_states WHERE key=?", (tx_cookie_key,)).fetchone()["state"]
            auth_conn.close()
            client.cookies.set("claire_oauth_tx", "tampered-tx-cookie", domain="127.0.0.1")
            csrf_cb = client.get(f"/auth/oidc/google/callback?state={tx_state_bad}&code=mock_code", follow_redirects=False)
            assert csrf_cb.status_code == 403
            assert client.cookies.get("claire_session") is None

            # Complete valid IdP login with ?tx= and matching claire_oauth_tx cookie.
            client.cookies.set("claire_oauth_tx", saved_tx_cookie, domain="127.0.0.1")
            tx_start2 = client.get(f"/auth/oidc/google/start?tx={tx_id}", follow_redirects=False)
            assert tx_start2.status_code == 302
            tx_cookie_key2 = client.cookies.get("claire_oidc_state")
            auth_conn = auth_db.connect_auth_db(auth_db.auth_db_path(s.data_dir))
            tx_state_ok = auth_conn.execute("SELECT state FROM oidc_states WHERE key=?", (tx_cookie_key2,)).fetchone()["state"]
            auth_conn.close()
            ok_cb = client.get(f"/auth/oidc/google/callback?state={tx_state_ok}&code=mock_code", follow_redirects=False)
            assert ok_cb.status_code == 303
            assert ok_cb.headers["location"] == f"/oauth/authorize?tx={tx_id}"

            consent = client.get(ok_cb.headers["location"])
            assert consent.status_code == 200
            assert 'name="step_up_password"' not in consent.text

            approve = client.post("/oauth/authorize", data={"tx": tx_id, "action": "approve"}, headers={"Origin": s.public_url, "Sec-Fetch-Site": "same-origin"}, follow_redirects=False)
            assert approve.status_code == 302
            code = parse_qs(urlparse(approve.headers["location"]).query)["code"][0]
            tok = client.post("/oauth/token", data={"grant_type": "authorization_code", "code": code, "client_id": cid, "redirect_uri": "http://127.0.0.1:9911/cb", "code_verifier": verifier})
            assert tok.status_code == 200
            assert tok.json()["scope"] == "readonly write"


def test_oauth_authorize_and_grants_hardening_e2e(tmp_path: Path) -> None:
    """Verify S256 omission rejection, HEAD handling, invalid action rejection, tx account isolation, deny cleanup, and grant revocation guards."""
    from claire.store import accounts as accounts_store

    s = _settings(tmp_path)
    with TestClient(_app(s), base_url=s.public_url) as client:
        conn = dbm.connect(s.db_file)
        accounts_store.AccountStore(conn).complete_setup(1, username="adminuser", display_name="Admin")
        alice = accounts_store.AccountStore(conn).create_member(username="alice", display_name="Alice")
        admin_session = accounts_store.create_account_session(conn, account_id=1, scope="owner")
        alice_session = accounts_store.create_account_session(conn, account_id=alice.id, scope="owner")
        conn.close()

        reg = client.post("/oauth/register", json={"client_name": "Hardening App", "redirect_uris": ["https://example.com/cb"]}).json()
        cid = reg["client_id"]
        verifier = "hardening-verifier-1234567890-abcdefghijk"
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).decode("ascii").rstrip("=")

        # Omitting code_challenge_method on GET /oauth/authorize must fail as non-S256.
        missing_method = client.get(f"/oauth/authorize?response_type=code&client_id={cid}&redirect_uri=https://example.com/cb&code_challenge={challenge}")
        assert missing_method.status_code == 400
        assert missing_method.json()["error"] == "invalid_request"

        # HEAD /oauth/authorize must follow the GET path cleanly without form parsing error.
        head_unauth = client.head(f"/oauth/authorize?response_type=code&client_id={cid}&redirect_uri=https://example.com/cb&code_challenge={challenge}&code_challenge_method=S256", follow_redirects=False)
        assert head_unauth.status_code == 303

        # Unsupported POST action must not approve and mint a code.
        client.cookies.set("claire_session", admin_session, domain="127.0.0.1")
        bad_action = client.post(
            "/oauth/authorize",
            data={"client_id": cid, "redirect_uri": "https://example.com/cb", "code_challenge": challenge, "code_challenge_method": "S256", "action": "bogus"},
            headers={"Origin": s.public_url, "Sec-Fetch-Site": "same-origin"},
        )
        assert bad_action.status_code == 400
        assert bad_action.json()["error"] == "invalid_request"

        # tx bound to admin cannot be approved by alice; deny consumes tx.
        client.cookies.clear()
        unauth = client.get(f"/oauth/authorize?response_type=code&client_id={cid}&redirect_uri=https://example.com/cb&state=s9&code_challenge={challenge}&code_challenge_method=S256", follow_redirects=False)
        tx_id = unauth.headers["location"].split("tx=", 1)[1]
        conn = dbm.connect(s.db_file)
        dbm.bind_oauth_pending_request_account(conn, tx_id, 1)
        conn.close()
        # Unauthenticated POST deny must fail with 401 and leave tx unconsumed.
        unauth_deny = client.post("/oauth/authorize", data={"tx": tx_id, "action": "deny"}, headers={"Origin": s.public_url})
        assert unauth_deny.status_code == 401
        conn = dbm.connect(s.db_file)
        assert dbm.get_oauth_pending_request(conn, tx_id) is not None
        conn.close()

        client.cookies.set("claire_session", alice_session, domain="127.0.0.1")
        mismatch_post = client.post("/oauth/authorize", data={"tx": tx_id, "action": "approve"}, headers={"Origin": s.public_url})
        assert mismatch_post.status_code == 403
        assert mismatch_post.json()["error"] == "access_denied"

        client.cookies.clear()
        client.cookies.set("claire_session", admin_session, domain="127.0.0.1")
        client.cookies.set("claire_oauth_tx", tx_id, domain="127.0.0.1")
        deny_post = client.post("/oauth/authorize", data={"tx": tx_id, "action": "deny"}, headers={"Origin": s.public_url}, follow_redirects=False)
        assert deny_post.status_code == 302
        assert "error=access_denied" in deny_post.headers["location"]

        # Grant revocation rejects Sec-Fetch-Site: same-site and honors revoke_all="false".
        reg2 = client.post("/oauth/register", json={"client_name": "Second App", "redirect_uris": ["https://example.com/cb2"]}).json()
        cid2 = reg2["client_id"]
        conn = dbm.connect(s.db_file)
        a1, _r1 = dbm.create_oauth_token(conn, client_id=cid, scope="readonly", account_id=1)
        a2, r2 = dbm.create_oauth_token(conn, client_id=cid2, scope="readonly", account_id=1)
        conn.close()
        grants = client.get("/api/me/oauth/grants").json()["grants"]
        assert len(grants) == 2
        by_cid = {g["client_id"]: g["grant_id"] for g in grants}

        same_site = client.delete(f"/api/me/oauth/grants?grant_id={by_cid[cid]}", headers={"Origin": s.public_url, "Sec-Fetch-Site": "same-site"})
        assert same_site.status_code == 403

        # Conflicting HTTP Basic vs body client_id on refresh_token and /oauth/revoke must fail with 401 invalid_client and preserve the token.
        conflict_basic = base64.b64encode(f"{cid}:wrong".encode("ascii")).decode("ascii")
        conflict_refresh = client.post("/oauth/token", data={"grant_type": "refresh_token", "refresh_token": r2, "client_id": cid2}, headers={"Authorization": f"Basic {conflict_basic}"})
        assert conflict_refresh.status_code == 401
        assert conflict_refresh.json()["error"] == "invalid_client"
        conflict_revoke = client.post("/oauth/revoke", data={"token": r2, "client_id": cid2}, headers={"Authorization": f"Basic {conflict_basic}"})
        assert conflict_revoke.status_code == 401
        assert conflict_revoke.json()["error"] == "invalid_client"

        single_revoke = client.post("/api/me/oauth/grants", json={"grant_id": by_cid[cid], "revoke_all": "false"}, headers={"Origin": s.public_url, "Sec-Fetch-Site": "same-origin"})
        assert single_revoke.status_code == 200
        assert single_revoke.json()["revoked"] == 1
        assert [g["client_id"] for g in client.get("/api/me/oauth/grants").json()["grants"]] == [cid2]
        client.cookies.clear()
        assert client.post("/mcp", headers={"Authorization": f"Bearer {a1}"}, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).status_code == 401
        assert client.post("/mcp", headers={"Authorization": f"Bearer {a2}"}, json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"}).status_code == 200


def test_oauth_consent_write_choice_narrows_scope_to_readonly(tmp_path: Path) -> None:
    """The consent form lets the user clear the write scope; the token is then readonly and write tools refuse it (unit CL6)."""
    import json
    import time
    from urllib.parse import parse_qs, urlparse
    from claire.store import accounts as accounts_store

    s = _settings(tmp_path)
    app = _app(s)
    with TestClient(app, base_url=s.public_url) as client:
        conn = dbm.connect(s.db_file)
        accounts_store.AccountStore(conn).complete_setup(1, username="adminuser", display_name="Admin")
        member = accounts_store.AccountStore(conn).create_member(username="alice", display_name="Alice")
        stale_session = accounts_store.create_account_session(conn, account_id=member.id, scope="owner", now=time.time() - 3600)
        conn.close()

        reg = client.post("/oauth/register", json={"client_name": "Chat connector", "redirect_uris": ["http://127.0.0.1:9876/cb"]}).json()
        cid = reg["client_id"]
        verifier = "cl6-verifier-1234567890-abcdefghijklmnopqrs"
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).decode("ascii").rstrip("=")

        client.cookies.set("claire_session", stale_session, domain="127.0.0.1")
        consent = client.get(
            f"/oauth/authorize?response_type=code&client_id={cid}&redirect_uri=http://127.0.0.1:9876/cb&state=s1&code_challenge={challenge}&code_challenge_method=S256&scope=readonly%20write"
        )
        assert consent.status_code == 200
        assert 'name="write_choice" value="1"' in consent.text
        assert 'type="checkbox" name="grant_write" value="1" checked' in consent.text
        assert "Allow write access" in consent.text
        # A read-only approval must be possible with an empty password field.
        assert 'name="step_up_password" type="password" autocomplete' in consent.text

        readonly_consent = client.get(
            f"/oauth/authorize?response_type=code&client_id={cid}&redirect_uri=http://127.0.0.1:9876/cb&state=s0&code_challenge={challenge}&code_challenge_method=S256&scope=readonly"
        )
        assert 'name="grant_write"' not in readonly_consent.text

        form = {
            "client_id": cid,
            "redirect_uri": "http://127.0.0.1:9876/cb",
            "state": "s1",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "scope": "readonly write",
            "action": "approve",
            "write_choice": "1",
        }
        # The box stays selected and the session is stale: the step-up rule does not change.
        kept = client.post("/oauth/authorize", data={**form, "grant_write": "1"}, headers={"Origin": s.public_url})
        assert kept.status_code == 403

        # The box is cleared: no password is necessary and the code carries readonly only.
        narrowed = client.post("/oauth/authorize", data=form, headers={"Origin": s.public_url}, follow_redirects=False)
        assert narrowed.status_code == 302
        code = parse_qs(urlparse(narrowed.headers["location"]).query)["code"][0]
        tok = client.post(
            "/oauth/token",
            data={"grant_type": "authorization_code", "code": code, "client_id": cid, "redirect_uri": "http://127.0.0.1:9876/cb", "code_verifier": verifier},
        )
        assert tok.status_code == 200
        token = tok.json()
        assert token["scope"] == "readonly"

        escalate = client.post("/oauth/token", data={"grant_type": "refresh_token", "refresh_token": token["refresh_token"], "client_id": cid, "scope": "readonly write"})
        assert escalate.status_code == 400

        client.cookies.clear()
        call = client.post(
            "/mcp",
            headers={"Authorization": f"Bearer {token['access_token']}"},
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "extract_read", "arguments": {"document_id": "doc_missing"}}},
        )
        assert call.status_code == 200
        payload = json.loads(call.json()["result"]["content"][0]["text"])
        assert payload == {"error": "scope_insufficient", "required_scope": "write"}
