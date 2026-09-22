"""Tests for OAuth 2.1 authorization server, discovery, and MCP client integration."""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

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
    allowed_user_ids: set[int] = None  # type: ignore
    site_name: str = "Claire Bible"

    def __post_init__(self) -> None:
        if self.allowed_user_ids is None:
            self.allowed_user_ids = set()


class StubService:
    def __init__(self) -> None:
        self.provider = SimpleNamespace(name="stub")


def _settings(tmp_path: Path, site_name: str = "Claire Bible") -> StubSettings:
    db_file = tmp_path / "test.db"
    conn = dbm.connect(db_file)
    dbm.init_db(conn)
    conn.close()
    return StubSettings(
        db_file=db_file,
        data_dir=tmp_path / "data",
        site_name=site_name,
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
        assert data["resource_name"] == "Claire Bible"
        assert data["resource_description"] == "Claire Bible Personal Knowledge Base"
        assert "http://127.0.0.1:8765" in data["authorization_servers"]
        assert data["scopes_supported"] == ["readonly"]
        assert "header" in data["bearer_methods_supported"]


def test_oauth_authorization_server_metadata(tmp_path: Path) -> None:
    s = _settings(tmp_path)
    app = _app(s)
    with TestClient(app, base_url=s.public_url) as client:
        resp = client.get("/.well-known/oauth-authorization-server")
        assert resp.status_code == 200
        data = resp.json()
        assert data["issuer"] == "http://127.0.0.1:8765"
        assert data["service_name"] == "Claire Bible"
        assert data["client_name"] == "Claire Bible"
        assert data["authorization_endpoint"] == "http://127.0.0.1:8765/oauth/authorize"
        assert data["token_endpoint"] == "http://127.0.0.1:8765/oauth/token"
        assert data["registration_endpoint"] == "http://127.0.0.1:8765/oauth/register"
        assert "code" in data["response_types_supported"]
        assert "authorization_code" in data["grant_types_supported"]
        assert "refresh_token" in data["grant_types_supported"]
        assert "S256" in data["code_challenge_methods_supported"]


def test_custom_site_name_metadata(tmp_path: Path) -> None:
    s = _settings(tmp_path, site_name="CustomKnowledgeBase")
    app = _app(s)
    with TestClient(app, base_url=s.public_url) as client:
        res_resp = client.get("/.well-known/oauth-protected-resource")
        assert res_resp.status_code == 200
        assert res_resp.json()["resource_name"] == "CustomKnowledgeBase"
        assert res_resp.json()["resource_description"] == "CustomKnowledgeBase Personal Knowledge Base"

        as_resp = client.get("/.well-known/oauth-authorization-server")
        assert as_resp.status_code == 200
        assert as_resp.json()["service_name"] == "CustomKnowledgeBase"
        assert as_resp.json()["client_name"] == "CustomKnowledgeBase"


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

        # 3. GET /oauth/authorize (HTML page)
        auth_url = (
            f"/oauth/authorize?response_type=code&client_id={client_id}"
            f"&redirect_uri=https://example.com/callback&state=mystate"
            f"&code_challenge={challenge}&code_challenge_method=S256"
        )
        get_auth = client.get(auth_url)
        assert get_auth.status_code == 200
        assert "Claire Bible 지식베이스 연결" in get_auth.text

        # 4. Mint a temporary session token to approve
        conn = dbm.connect(s.db_file)
        session_token = dbm.create_session(conn, scope="owner")
        conn.close()

        # 5. POST /oauth/authorize (Approve with session token)
        post_auth = client.post(
            "/oauth/authorize",
            data={
                "client_id": client_id,
                "redirect_uri": "https://example.com/callback",
                "state": "mystate",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "scope": "readonly",
                "action": "approve",
                "session_token": session_token,
            },
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

        # 7. Access /mcp with the OAuth access_token
        mcp_resp = client.post(
            "/mcp",
            headers={"Authorization": f"Bearer {access_token}"},
            json={"jsonrpc": "2.0", "id": 10, "method": "tools/list"},
        )
        assert mcp_resp.status_code == 200
        mcp_data = mcp_resp.json()
        assert "tools" in mcp_data["result"]
        tool_names = [t["name"] for t in mcp_data["result"]["tools"]]
        assert "search" in tool_names

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
    app = _app(s)

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
