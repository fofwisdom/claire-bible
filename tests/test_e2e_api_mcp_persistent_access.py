"""End-to-End lifecycle test for OAuth 2.1 and account-bound session access over REST and MCP."""

from __future__ import annotations

import time
from pathlib import Path

from starlette.testclient import TestClient

from claire.api.server import create_app
from claire.core.config import Settings
from claire.ontology.base import Document
from claire.store import db as dbm
from tests.auth_helpers import bearer_headers, issue_oauth_token


def test_e2e_api_mcp_persistent_access_lifecycle(tmp_path: Path):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)
    db_file = data_dir / "claire.db"
    public_url = "http://127.0.0.1:8765"

    s = Settings(
        CLAIRE_DATA_DIR=str(data_dir),
        CLAIRE_DB_PATH=str(db_file),
        CLAIRE_PROVIDER="mock",
        CLAIRE_ENVIRONMENT="development",
        CLAIRE_FQDN=public_url,
    )

    conn0 = dbm.connect(db_file)
    dbm.init_db(conn0)
    test_doc = Document(id="doc-e2e-1", url="https://example.com/e2e", title="OAuth Token Protocol Guide")
    dbm.insert_document(conn0, test_doc)
    conn0.commit()
    conn0.close()

    app = create_app(s)
    cursor_token = issue_oauth_token(db_file, account_id=1, scope="readonly", client_id="cursor-agent", client_name="Cursor Agent")
    write_token = issue_oauth_token(db_file, account_id=1, scope="write", client_id="ci-writer", client_name="CI Writer")
    expired_token = issue_oauth_token(db_file, account_id=1, scope="readonly", client_id="expired-agent", ttl_seconds=-10.0)
    owner_headers = bearer_headers(db_file, account_id=1, scope="owner")

    with TestClient(app, base_url=public_url) as client:
        # Retired /tokens surfaces return 404 for all callers.
        assert client.get("/tokens").status_code == 404
        assert client.get("/tokens", headers=owner_headers).status_code == 404

        # External agent accesses MCP over Streamable HTTP (/mcp) with OAuth 2.1 token.
        mcp_list_resp = client.post(
            "/mcp",
            headers={"Authorization": f"Bearer {cursor_token}"},
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        )
        assert mcp_list_resp.status_code == 200
        tool_names = {t["name"] for t in mcp_list_resp.json()["result"]["tools"]}
        assert {"kb_overview", "doc_list", "entity_search"} <= tool_names

        mcp_call_resp = client.post(
            "/mcp",
            headers={"Authorization": f"Bearer {cursor_token}"},
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "kb_overview", "arguments": {}}},
        )
        assert mcp_call_resp.status_code == 200

        # REST API (/documents and /whoami).
        docs_resp = client.get("/documents", headers={"Authorization": f"Bearer {cursor_token}"})
        assert docs_resp.status_code == 200
        assert any(d["id"] == "doc-e2e-1" for d in docs_resp.json()["documents"])

        whoami_write = client.get("/whoami", headers={"Authorization": f"Bearer {write_token}"})
        assert whoami_write.status_code == 200
        assert whoami_write.json()["scope"] == "write"

        # Immediate OAuth revocation enforcement across MCP and REST.
        rev_resp = client.post("/oauth/revoke", data={"token": cursor_token})
        assert rev_resp.status_code == 200

        mcp_blocked = client.post(
            "/mcp",
            headers={"Authorization": f"Bearer {cursor_token}"},
            json={"jsonrpc": "2.0", "id": 3, "method": "tools/list"},
        )
        assert mcp_blocked.status_code == 401
        assert "Bearer" in mcp_blocked.headers.get("www-authenticate", "")
        assert client.get("/documents", headers={"Authorization": f"Bearer {cursor_token}"}).status_code == 404

        # Expired OAuth token is rejected on both MCP and REST.
        assert client.post(
            "/mcp",
            headers={"Authorization": f"Bearer {expired_token}"},
            json={"jsonrpc": "2.0", "id": 4, "method": "tools/list"},
        ).status_code == 401
        assert client.get("/documents", headers={"Authorization": f"Bearer {expired_token}"}).status_code == 404
        assert time.time() > 0
