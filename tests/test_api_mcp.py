"""API 서버(server.py) MCP 엔드포인트(/mcp) 통합 테스트.

- 표준 MCP HTTP 사양: 미인증/무효 토큰 요청 시 401 Unauthorized + WWW-Authenticate: Bearer
- X-Session 헤더 및 Bearer 토큰을 통한 단일 활성 세션 인증
- tools/list 10종 툴 노출 및 tools/call JSON-RPC 통신
- 세션 재발급 시 이전 세션 토큰 즉시 무효화
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from starlette.testclient import TestClient

from claire.api import server
from claire.store import db as dbm

OWNER_TOKEN = "owner-" + ("o" * 32)
READONLY_TOKEN = "readonly-" + ("r" * 32)


@dataclass
class StubSettings:
    db_file: Path
    data_dir: Path
    environment: str = "development"
    public_url: str = "http://127.0.0.1:8765"
    inject_host: str = "127.0.0.1"
    inject_port: int = 8765
    inject_token: str = OWNER_TOKEN
    readonly_token: str = READONLY_TOKEN
    cors_allowed_origins: str = ""
    anonymous_readonly: bool = False
    effective_provider: str = "mock"


class StubService:
    def __init__(self) -> None:
        self.provider = SimpleNamespace(name="stub")


def _settings(tmp_path: Path) -> StubSettings:
    return StubSettings(
        db_file=tmp_path / "test.db",
        data_dir=tmp_path / "data",
    )


def _app(s: StubSettings):
    return server.create_app(s, StubService())


def _mint_session(s: Any, scope: str) -> str:
    conn = dbm.connect(s.db_file)
    dbm.init_db(conn)
    try:
        return dbm.create_session(conn, scope=scope)
    finally:
        conn.close()


def _rpc(method: str, params: dict | None = None, req_id: int = 1) -> dict:
    body = {"jsonrpc": "2.0", "id": req_id, "method": method}
    if params is not None:
        body["params"] = params
    return body


def test_mcp_no_session_returns_401(tmp_path: Path) -> None:
    s = _settings(tmp_path)
    app = _app(s)
    with TestClient(app, base_url=s.public_url) as client:
        resp = client.post(
            "/mcp",
            json=_rpc(
                "initialize",
                {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "1.0"},
                },
            ),
        )
        assert resp.status_code == 401
        assert "WWW-Authenticate" in resp.headers
        assert resp.headers["WWW-Authenticate"].startswith("Bearer")
        assert resp.headers["content-type"].startswith("application/json")
        body = resp.json()
        assert body["error"] == "invalid_token"


def test_mcp_invalid_session_returns_401(tmp_path: Path) -> None:
    s = _settings(tmp_path)
    app = _app(s)
    with TestClient(app, base_url=s.public_url) as client:
        resp = client.post(
            "/mcp",
            json=_rpc("tools/list"),
            headers={"X-Session": "not-a-real-session-token"},
        )
        assert resp.status_code == 401
        assert "WWW-Authenticate" in resp.headers
        assert resp.headers["WWW-Authenticate"].startswith("Bearer")
        assert resp.headers["content-type"].startswith("application/json")
        body = resp.json()
        assert body["error"] == "invalid_token"


def test_mcp_invalid_bearer_token_returns_401(tmp_path: Path) -> None:
    s = _settings(tmp_path)
    app = _app(s)
    with TestClient(app, base_url=s.public_url) as client:
        resp = client.post(
            "/mcp",
            json=_rpc("tools/list"),
            headers={"Authorization": "Bearer invalid-token"},
        )
        assert resp.status_code == 401
        assert "WWW-Authenticate" in resp.headers
        assert resp.headers["WWW-Authenticate"].startswith("Bearer")


def test_mcp_readonly_session_tools_list_and_call(tmp_path: Path) -> None:
    s = _settings(tmp_path)
    tok = _mint_session(s, "readonly")
    app = _app(s)
    with TestClient(app, base_url=s.public_url) as client:
        resp = client.post(
            "/mcp",
            json=_rpc("tools/list"),
            headers={"X-Session": tok},
        )
        assert resp.status_code == 200
        body = resp.json()
        names = {t["name"] for t in body["result"]["tools"]}
        assert {
            "resolve_entity",
            "neighbors",
            "search",
            "context",
            "overview",
            "path",
            "node",
            "documents",
            "document",
            "stats",
        } <= names

        resp2 = client.post(
            "/mcp",
            json=_rpc("tools/call", {"name": "stats", "arguments": {}}, req_id=2),
            headers={"X-Session": tok},
        )
        assert resp2.status_code == 200
        body2 = resp2.json()
        payload = json.loads(body2["result"]["content"][0]["text"])
        assert "documents" in payload and "entities" in payload


def test_mcp_owner_bearer_tools_list_and_call(tmp_path: Path) -> None:
    s = _settings(tmp_path)
    app = _app(s)
    with TestClient(app, base_url=s.public_url) as client:
        resp = client.post(
            "/mcp",
            json=_rpc("tools/list"),
            headers={"Authorization": f"Bearer {OWNER_TOKEN}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        names = {t["name"] for t in body["result"]["tools"]}
        assert len(names) == 10


def test_mcp_session_regeneration_invalidates_previous_token(tmp_path: Path) -> None:
    s = _settings(tmp_path)
    tok1 = _mint_session(s, "readonly")
    app = _app(s)
    with TestClient(app, base_url=s.public_url) as client:
        resp1 = client.post(
            "/mcp",
            json=_rpc("tools/list"),
            headers={"X-Session": tok1},
        )
        assert resp1.status_code == 200

        # 같은 scope로 새 세션 발급 시 이전 토큰 무효화
        _mint_session(s, "readonly")

        resp2 = client.post(
            "/mcp",
            json=_rpc("tools/list"),
            headers={"X-Session": tok1},
        )
        assert resp2.status_code == 401
        assert "WWW-Authenticate" in resp2.headers

