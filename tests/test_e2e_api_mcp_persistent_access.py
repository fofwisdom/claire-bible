"""End-to-End lifecycle test for API and MCP persistent token management and access.

Verifies:
1. Starting up without environment variable tokens auto-generates default owner token.
2. CLI token management (list, create with scopes, revoke).
3. WebUI REST API token management (/tokens GET, POST, POST /tokens/revoke).
4. Full MCP protocol access over Streamable HTTP (/mcp) using RFC 6750 Bearer authentication.
5. Immediate revocation enforcement across both MCP and REST endpoints.
6. Expired token rejection across both MCP and REST endpoints.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from claire import cli
from claire.api.server import create_app
from claire.config import Settings
from claire.ontology.base import Document
from claire.store import db as dbm


def test_e2e_api_mcp_persistent_access_lifecycle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture):
    data_dir = tmp_path / "data"
    vault_dir = tmp_path / "vault"
    data_dir.mkdir(parents=True)
    vault_dir.mkdir(parents=True)

    db_file = data_dir / "claire.db"
    public_url = "http://127.0.0.1:8765"

    # Step 1: Ensure no environment variable tokens are passed
    s = Settings(
        CLAIRE_DATA_DIR=str(data_dir),
        CLAIRE_DB_PATH=str(db_file),
        CLAIRE_VAULT_PATH=str(vault_dir),
        CLAIRE_PROVIDER="mock",
        CLAIRE_ENVIRONMENT="development",
        CLAIRE_PUBLIC_URL=public_url,
        CLAIRE_INJECT_TOKEN="",
        CLAIRE_READONLY_TOKEN="",
        CLAIRE_COLLABORATOR_TOKEN="",
        CLAIRE_ANONYMOUS_READONLY=False,
    )

    # Insert a sample document so search/tools return content
    conn0 = dbm.connect(db_file)
    dbm.init_db(conn0)
    test_doc = Document(id="doc-e2e-1", url="https://example.com/e2e", title="Persistent Token Protocol Guide")
    dbm.insert_document(conn0, test_doc)
    conn0.commit()
    conn0.close()

    # Step 2: Server bootstrap ensures default owner token in DB
    app = create_app(s)
    assert bool(s.inject_token) is True
    assert s.inject_token.startswith("cb_")

    # Step 3: CLI token management
    monkeypatch.setattr("claire.cli.get_settings", lambda: s)

    # CLI list: default owner token exists
    res_list = cli.cmd_token(SimpleNamespace(token_action="list", all=False, json=True))
    assert res_list == 0
    out_list = capsys.readouterr().out
    import json
    tokens_json = json.loads(out_list)["tokens"]
    assert len(tokens_json) >= 1
    assert any(t["scope"] == "owner" for t in tokens_json)

    # CLI create: create a readonly token for Cursor Agent
    res_create_cursor = cli.cmd_token(SimpleNamespace(
        token_action="create",
        name="Cursor Agent",
        scope="readonly",
        expires_in=None,
        json=True,
    ))
    assert res_create_cursor == 0
    cursor_tok_payload = json.loads(capsys.readouterr().out)["token"]
    cursor_token = cursor_tok_payload["token"]
    cursor_id = cursor_tok_payload["id"]
    assert cursor_token.startswith("cb_")
    assert cursor_tok_payload["scope"] == "readonly"

    # CLI create: create a collaborator token
    res_create_collab = cli.cmd_token(SimpleNamespace(
        token_action="create",
        name="CI Collab",
        scope="collaborator",
        expires_in=30,
        json=True,
    ))
    assert res_create_collab == 0
    collab_tok_payload = json.loads(capsys.readouterr().out)["token"]
    collab_token = collab_tok_payload["token"]
    collab_id = collab_tok_payload["id"]
    assert collab_token.startswith("cb_")
    assert collab_tok_payload["scope"] == "collaborator"

    with TestClient(app, base_url=public_url) as client:
        # Step 4: WebUI REST API Token Management
        # 4a. Anonymous/unauthenticated requests cannot access /tokens (Route policy denies with 404)
        anon_resp = client.get("/tokens")
        assert anon_resp.status_code == 404

        # 4b. Readonly token cannot access /tokens (ROUTE_POLICY owner restriction hides as 404)
        ro_tokens_resp = client.get(
            "/tokens",
            headers={"Authorization": f"Bearer {cursor_token}"},
        )
        assert ro_tokens_resp.status_code == 404

        # 4c. Owner lists tokens
        owner_headers = {"Authorization": f"Bearer {s.inject_token}"}
        owner_list_resp = client.get("/tokens", headers=owner_headers)
        assert owner_list_resp.status_code == 200
        active_tokens = owner_list_resp.json()["tokens"]
        assert len(active_tokens) >= 3
        # Ensure secret tokens are masked in list view
        for t in active_tokens:
            assert "token_masked" in t
            assert "token" not in t

        # 4d. Owner creates a new token via Web API for Claude Desktop
        owner_create_resp = client.post(
            "/tokens",
            headers=owner_headers,
            json={"name": "Claude Desktop", "scope": "readonly", "expires_in_days": None},
        )
        assert owner_create_resp.status_code == 201
        claude_tok_data = owner_create_resp.json()["token"]
        claude_token = claude_tok_data["token"]
        claude_id = claude_tok_data["id"]
        assert claude_token.startswith("cb_")

        # Step 5: External agent accesses MCP over Streamable HTTP (/mcp)
        # 5a. Call tools/list with Cursor readonly token
        mcp_list_resp = client.post(
            "/mcp",
            headers={"Authorization": f"Bearer {cursor_token}"},
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        )
        assert mcp_list_resp.status_code == 200
        mcp_list_body = mcp_list_resp.json()
        assert "result" in mcp_list_body
        tool_names = {t["name"] for t in mcp_list_body["result"]["tools"]}
        assert "overview" in tool_names
        assert "documents" in tool_names
        assert "search" in tool_names

        # 5b. Call tools/call with Claude Desktop readonly token
        mcp_call_resp = client.post(
            "/mcp",
            headers={"Authorization": f"Bearer {claude_token}"},
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "overview", "arguments": {}},
            },
        )
        assert mcp_call_resp.status_code == 200
        call_result = mcp_call_resp.json()
        assert "result" in call_result

        # Step 6: External agent accesses REST API (/documents)
        docs_resp = client.get(
            "/documents",
            headers={"Authorization": f"Bearer {cursor_token}"},
        )
        assert docs_resp.status_code == 200
        assert any(d["id"] == "doc-e2e-1" for d in docs_resp.json()["documents"])

        # Collaborator can access whoami
        whoami_collab = client.get(
            "/whoami",
            headers={"Authorization": f"Bearer {collab_token}"},
        )
        assert whoami_collab.status_code == 200
        assert whoami_collab.json()["scope"] == "collaborator"

        # Step 7: Revocation enforcement across CLI and REST API
        # 7a. Revoke Cursor token via CLI
        res_revoke_cli = cli.cmd_token(SimpleNamespace(
            token_action="revoke",
            target=str(cursor_id),
            json=True,
        ))
        assert res_revoke_cli == 0

        # 7b. Revoke Claude Desktop token via Web API
        web_revoke_resp = client.post(
            "/tokens/revoke",
            headers=owner_headers,
            json={"id": claude_id},
        )
        assert web_revoke_resp.status_code == 200
        assert web_revoke_resp.json()["ok"] is True

        # 7c. Cursor token is immediately rejected on /mcp with 401
        mcp_cursor_blocked = client.post(
            "/mcp",
            headers={"Authorization": f"Bearer {cursor_token}"},
            json={"jsonrpc": "2.0", "id": 3, "method": "tools/list"},
        )
        assert mcp_cursor_blocked.status_code == 401
        assert "Bearer" in mcp_cursor_blocked.headers.get("www-authenticate", "")

        # 7d. Cursor token is immediately rejected on REST /documents (stealth mode returns 404)
        rest_cursor_blocked = client.get(
            "/documents",
            headers={"Authorization": f"Bearer {cursor_token}"},
        )
        assert rest_cursor_blocked.status_code == 404

        # 7e. Claude Desktop token is immediately rejected on /mcp with 401
        mcp_claude_blocked = client.post(
            "/mcp",
            headers={"Authorization": f"Bearer {claude_token}"},
            json={"jsonrpc": "2.0", "id": 4, "method": "tools/list"},
        )
        assert mcp_claude_blocked.status_code == 401

        # Step 8: Expired token verification
        conn_exp = dbm.connect(db_file)
        expired_token_record = dbm.create_api_token(conn_exp, name="Expired Agent", scope="readonly", expires_in_days=-1)
        conn_exp.close()

        expired_mcp_resp = client.post(
            "/mcp",
            headers={"Authorization": f"Bearer {expired_token_record['token']}"},
            json={"jsonrpc": "2.0", "id": 5, "method": "tools/list"},
        )
        assert expired_mcp_resp.status_code == 401

        expired_rest_resp = client.get(
            "/documents",
            headers={"Authorization": f"Bearer {expired_token_record['token']}"},
        )
        assert expired_rest_resp.status_code == 404
