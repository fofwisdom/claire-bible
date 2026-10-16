"""Unit tests for API and MCP persistent access tokens.

Covers:
- Database operations (create, list, get, validate, revoke, migrate)
- ASGI Security boundary authentication with DB-backed tokens
- REST API /tokens endpoints (list, create, revoke)
- CLI claire token command
"""

from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from claire.api import security
from claire.api.server import create_app
from claire.cli import cmd_token
from claire.store import db as dbm


def test_api_token_db_lifecycle(tmp_path: Path):
    """Test full database lifecycle for API and MCP tokens."""
    db_file = tmp_path / "test_tokens.db"
    conn = dbm.connect(db_file)
    dbm.init_db(conn)

    # 1. Create tokens with different scopes
    tok_ro = dbm.create_api_token(conn, name="Claude Desktop", scope="readonly", expires_in_days=None)
    assert tok_ro["name"] == "Claude Desktop"
    assert tok_ro["scope"] == "readonly"
    assert tok_ro["token"].startswith("cb_")
    assert tok_ro["expires_at"] is None

    tok_collab = dbm.create_api_token(conn, name="Theme Worker", scope="collab", expires_in_days=30)
    assert tok_collab["scope"] == "collaborator"
    assert tok_collab["expires_at"] is not None
    assert tok_collab["expires_at"] > time.time()

    tok_owner = dbm.create_api_token(conn, name="CLI Admin", scope="owner", expires_in_days=None)
    assert tok_owner["scope"] == "owner"

    # 2. Validation
    val_ro = dbm.validate_api_token(conn, tok_ro["token"])
    assert val_ro is not None
    assert val_ro["scope"] == "readonly"
    assert val_ro["name"] == "Claude Desktop"

    # Verify last_used_at was updated
    checked_ro = dbm.get_api_token(conn, tok_ro["id"])
    assert checked_ro is not None
    assert checked_ro["last_used_at"] is not None

    # 3. Listing
    active_tokens = dbm.list_api_tokens(conn, include_revoked=False)
    assert len(active_tokens) == 3

    # 4. Lookup by prefix and id
    prefix = tok_ro["token"][:12]
    found_by_prefix = dbm.get_api_token(conn, prefix)
    assert found_by_prefix is not None
    assert found_by_prefix["id"] == tok_ro["id"]

    found_by_id = dbm.get_api_token(conn, tok_owner["id"])
    assert found_by_id is not None
    assert found_by_id["name"] == "CLI Admin"

    # 5. Revocation
    revoked = dbm.revoke_api_token(conn, tok_collab["id"])
    assert revoked is True
    # Second revocation returns False
    assert dbm.revoke_api_token(conn, tok_collab["id"]) is False

    # Validating revoked token returns None
    assert dbm.validate_api_token(conn, tok_collab["token"]) is None

    # Active listing excludes revoked
    active_now = dbm.list_api_tokens(conn, include_revoked=False)
    assert len(active_now) == 2
    all_tokens = dbm.list_api_tokens(conn, include_revoked=True)
    assert len(all_tokens) == 3

    # 6. Expiration check
    expired_tok = dbm.create_api_token(conn, name="Expired Agent", scope="readonly", expires_in_days=-1)
    assert dbm.validate_api_token(conn, expired_tok["token"]) is None

    conn.close()


def test_migrate_env_tokens_and_ensure_default_owner(tmp_path: Path):
    """Test importing legacy environment variable tokens and ensuring default owner."""
    db_file = tmp_path / "migrate.db"
    conn = dbm.connect(db_file)
    dbm.init_db(conn)

    legacy_inject = "legacy-owner-token-" + "1" * 32
    legacy_ro = "legacy-readonly-tok-" + "2" * 32

    migrated = dbm.migrate_env_tokens(
        conn,
        env_inject_token=legacy_inject,
        env_readonly_token=legacy_ro,
        env_collab_token="",
    )
    assert len(migrated) == 2
    assert any(m["token"] == legacy_inject and m["scope"] == "owner" for m in migrated)
    assert any(m["token"] == legacy_ro and m["scope"] == "readonly" for m in migrated)

    # Calling again is idempotent (no duplicates)
    migrated_again = dbm.migrate_env_tokens(conn, env_inject_token=legacy_inject)
    assert len(migrated_again) == 0

    # Ensure default owner token returns existing owner token
    owner = dbm.ensure_default_owner_token(conn)
    assert owner["token"] == legacy_inject

    conn.close()


def test_ensure_default_owner_token_generates_when_empty(tmp_path: Path):
    """Test ensure_default_owner_token creates new token when none exist."""
    db_file = tmp_path / "empty.db"
    conn = dbm.connect(db_file)
    dbm.init_db(conn)

    owner = dbm.ensure_default_owner_token(conn)
    assert owner["scope"] == "owner"
    assert owner["token"].startswith("cb_")
    assert dbm.validate_api_token(conn, owner["token"]) is not None

    conn.close()


def test_api_and_mcp_authentication_with_persistent_token(tmp_path: Path):
    """Test that requests to /mcp and REST endpoints succeed with persistent DB tokens."""
    db_file = tmp_path / "claire.db"
    conn = dbm.connect(db_file)
    dbm.init_db(conn)

    ro_token_info = dbm.create_api_token(conn, name="Claude MCP", scope="readonly")
    owner_token_info = dbm.create_api_token(conn, name="Admin Key", scope="owner")
    conn.close()

    settings = SimpleNamespace(
        environment="development",
        public_url="http://127.0.0.1:8765/",
        cors_allowed_origins="",
        inject_token=owner_token_info["token"],
        readonly_token="",
        collaborator_token="",
        anonymous_readonly=False,
        db_file=db_file,
        data_dir=tmp_path,
        inject_port=8765,
        effective_provider="mock",
    )

    app = create_app(settings)
    with TestClient(app, base_url=settings.public_url) as client:
        # 1. MCP endpoint with readonly DB token
        mcp_resp = client.post(
            "/mcp",
            headers={"Authorization": f"Bearer {ro_token_info['token']}"},
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        )
        assert mcp_resp.status_code == 200
        assert "result" in mcp_resp.json()
        assert "tools" in mcp_resp.json()["result"]

        # 2. REST API endpoint /whoami with readonly DB token
        who_ro = client.get(
            "/whoami",
            headers={"Authorization": f"Bearer {ro_token_info['token']}"},
        )
        assert who_ro.status_code == 200
        assert who_ro.json()["scope"] == "readonly"

        # 3. REST API endpoint /whoami with owner DB token
        who_owner = client.get(
            "/whoami",
            headers={"Authorization": f"Bearer {owner_token_info['token']}"},
        )
        assert who_owner.status_code == 200
        assert who_owner.json()["scope"] == "owner"

        # 4. Revoke token and verify immediate rejection
        c2 = dbm.connect(db_file)
        dbm.revoke_api_token(c2, ro_token_info["id"])
        c2.close()

        mcp_revoked = client.post(
            "/mcp",
            headers={"Authorization": f"Bearer {ro_token_info['token']}"},
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        )
        assert mcp_revoked.status_code == 401
        assert "WWW-Authenticate" in mcp_revoked.headers


def test_token_rest_endpoints(tmp_path: Path):
    """Test /tokens management REST API endpoints."""
    db_file = tmp_path / "rest_tokens.db"
    conn = dbm.connect(db_file)
    dbm.init_db(conn)
    owner = dbm.create_api_token(conn, name="Root Owner", scope="owner")
    ro = dbm.create_api_token(conn, name="Guest", scope="readonly")
    conn.close()

    settings = SimpleNamespace(
        environment="development",
        public_url="http://127.0.0.1:8765/",
        cors_allowed_origins="",
        inject_token=owner["token"],
        readonly_token="",
        collaborator_token="",
        anonymous_readonly=False,
        db_file=db_file,
        data_dir=tmp_path,
        inject_port=8765,
        effective_provider="mock",
    )

    app = create_app(settings)
    with TestClient(app, base_url=settings.public_url) as client:
        # 1. Readonly token cannot access /tokens
        forbidden_resp = client.get(
            "/tokens",
            headers={"Authorization": f"Bearer {ro['token']}"},
        )
        assert forbidden_resp.status_code == 404  # ROUTE_POLICY blocks unauthorized access as 404

        # 2. Owner lists tokens
        list_resp = client.get(
            "/tokens",
            headers={"Authorization": f"Bearer {owner['token']}"},
        )
        assert list_resp.status_code == 200
        tokens = list_resp.json()["tokens"]
        assert len(tokens) >= 2
        # Verify raw token is masked
        assert "token_masked" in tokens[0]
        assert "token" not in tokens[0]

        # 3. Owner creates new token
        create_resp = client.post(
            "/tokens",
            headers={"Authorization": f"Bearer {owner['token']}"},
            json={"name": "Cursor Agent", "scope": "readonly", "expires_in_days": 90},
        )
        assert create_resp.status_code == 201
        created_data = create_resp.json()["token"]
        assert created_data["name"] == "Cursor Agent"
        assert created_data["token"].startswith("cb_")

        # 4. Owner revokes token via POST /tokens/revoke
        revoke_resp = client.post(
            "/tokens/revoke",
            headers={"Authorization": f"Bearer {owner['token']}"},
            json={"id": created_data["id"]},
        )
        assert revoke_resp.status_code == 200
        assert revoke_resp.json()["ok"] is True


def test_cli_cmd_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture):
    """Test CLI claire token commands: list, create, revoke."""
    db_file = tmp_path / "cli_test.db"
    settings = SimpleNamespace(
        db_file=db_file,
        data_dir=tmp_path,
        public_url="http://127.0.0.1:8765",
        inject_port=8765,
    )
    monkeypatch.setattr("claire.cli.get_settings", lambda: settings)

    # 1. List initially empty
    res = cmd_token(SimpleNamespace(token_action="list", all=False, json=False))
    assert res == 0
    out = capsys.readouterr().out
    assert "등록된 API/MCP 토큰이 없습니다" in out

    # 2. Create token via CLI
    res = cmd_token(SimpleNamespace(
        token_action="create",
        name="CLI Test Agent",
        scope="readonly",
        expires_in=None,
        json=False,
    ))
    assert res == 0
    out = capsys.readouterr().out
    assert "발급 완료" in out
    assert "CLI Test Agent" in out
    assert "cb_" in out

    # 3. List active tokens
    res = cmd_token(SimpleNamespace(token_action="list", all=False, json=True))
    assert res == 0
    out = capsys.readouterr().out
    assert "CLI Test Agent" in out

    # 4. Revoke token
    res = cmd_token(SimpleNamespace(token_action="revoke", target="1", json=False))
    assert res == 0
    out = capsys.readouterr().out
    assert "폐기되었습니다" in out
