"""inject API(server.py) 게이트/인증 경계 — 특히 /mcp 라우트의 존재-은폐 원칙과
세션 스코프 동작(docs/MCP_SUPPORT.md §9). 아이오홉프 TestClient로 실제 HTTP
왕복을 검증한다(pytest-asyncio auto 모드, aiohttp.test_utils).

TestServer는 임의 포트에 바인드되는데 mcp_tools.build_mcp_app의
transport_security.allowed_hosts는 Host 헤더 완전일치(포트 포함)라 기본
"127.0.0.1:<임의포트>"는 통과 못 한다(운영에서는 nginx가 고정 호스트명을
보내므로 문제 없음, docs/MCP_SUPPORT.md §4). 테스트에서는 allowed_hosts에
포함된 "localhost"(포트 무관하게 그 문자열 자체가 들어있음)를 Host 헤더로
명시해 우회한다."""

from __future__ import annotations

import json

import pytest
from aiohttp.test_utils import TestClient, TestServer

from claire.api.server import build_app
from claire.config import Settings
from claire.store import db as dbm

_HOST_HEADER = {"Host": "localhost"}


def _settings(tmp_path):
    return Settings(
        db_path=str(tmp_path / "test.db"),
        inject_token="test-owner-bearer",
        provider="mock",
    )


def _mint_session(s, scope: str) -> str:
    conn = dbm.connect(s.db_file)
    dbm.init_db(conn)
    try:
        return dbm.create_session(conn, scope=scope)
    finally:
        conn.close()


async def _client(s) -> TestClient:
    app = build_app(s)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


def _headers(session: str | None = None) -> dict:
    h = dict(_HOST_HEADER)
    if session:
        h["X-Session"] = session
    return h


def _rpc(method: str, params: dict | None = None, req_id: int = 1) -> dict:
    body = {"jsonrpc": "2.0", "id": req_id, "method": method}
    if params is not None:
        body["params"] = params
    return body


@pytest.mark.asyncio
async def test_mcp_no_session_returns_404(tmp_path):
    s = _settings(tmp_path)
    client = await _client(s)
    try:
        resp = await client.post(
            "/mcp",
            json=_rpc("initialize", {
                "protocolVersion": "2026-07-28", "capabilities": {},
                "clientInfo": {"name": "t", "version": "0"}}),
            headers=_headers())
        assert resp.status == 404  # 401 아님 — 존재 자체를 숨김
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_mcp_invalid_session_returns_404(tmp_path):
    s = _settings(tmp_path)
    client = await _client(s)
    try:
        resp = await client.post(
            "/mcp", json=_rpc("tools/list"),
            headers=_headers("not-a-real-token"))
        assert resp.status == 404
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_mcp_readonly_session_tools_list_and_call(tmp_path):
    s = _settings(tmp_path)
    tok = _mint_session(s, "readonly")
    client = await _client(s)
    try:
        resp = await client.post(
            "/mcp", json=_rpc("tools/list"), headers=_headers(tok))
        assert resp.status == 200
        body = await resp.json()
        names = {t["name"] for t in body["result"]["tools"]}
        assert {"resolve_entity", "neighbors", "search", "context",
                "overview", "path", "node", "documents", "document",
                "stats"} <= names

        resp2 = await client.post(
            "/mcp", json=_rpc("tools/call", {"name": "stats", "arguments": {}}, req_id=2),
            headers=_headers(tok))
        assert resp2.status == 200
        body2 = await resp2.json()
        payload = json.loads(body2["result"]["content"][0]["text"])
        assert "documents" in payload and "entities" in payload
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_mcp_owner_session_same_readonly_toolset_v1(tmp_path):
    # v1은 쓰기 툴이 없어 owner/readonly 둘 다 동일 read 툴 세트를 봐야 한다
    # (docs/MCP_SUPPORT.md §2 — 스코프 구분은 M2 쓰기 툴 도입 시).
    s = _settings(tmp_path)
    tok = _mint_session(s, "owner")
    client = await _client(s)
    try:
        resp = await client.post(
            "/mcp", json=_rpc("tools/list"), headers=_headers(tok))
        assert resp.status == 200
        body = await resp.json()
        names = {t["name"] for t in body["result"]["tools"]}
        assert len(names) == 10
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_mcp_unknown_tool_is_dispatcher_error_not_500(tmp_path):
    s = _settings(tmp_path)
    tok = _mint_session(s, "readonly")
    client = await _client(s)
    try:
        resp = await client.post(
            "/mcp",
            json=_rpc("tools/call", {"name": "delete_everything", "arguments": {}}),
            headers=_headers(tok))
        assert resp.status == 200  # JSON-RPC 레벨 에러(isError), HTTP 레벨 장애 아님
        body = await resp.json()
        assert body["result"]["isError"] is True
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_mcp_get_unauthenticated_returns_404(tmp_path):
    # 존재-은폐 원칙 검증 대상은 "무인증 GET"이다 — 인증된 GET은 아이오홉프
    # 라우터가 경로는 맞지만 메서드가 없다고 정상적으로 405를 주는데(POST만
    # 등록했으므로), 이건 이미 유효한 세션을 가진 클라이언트에게만 보이는
    # 정보라 존재-은폐와 무관(공격자는 애초에 유효 세션이 없어 여기 못 옴).
    s = _settings(tmp_path)
    client = await _client(s)
    try:
        resp = await client.get("/mcp", headers=_headers())
        assert resp.status == 404
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_mcp_session_regeneration_invalidates_previous_token(tmp_path):
    # docs/MCP_SUPPORT.md §5 트레이드오프 회귀 테스트 — 같은 scope로 새
    # 세션을 발급하면 이전 토큰은 즉시 무효화된다(사용자 결정: 격리 낮추는
    # 다중세션 허용은 하지 않음).
    s = _settings(tmp_path)
    tok1 = _mint_session(s, "owner")
    client = await _client(s)
    try:
        resp1 = await client.post(
            "/mcp", json=_rpc("tools/list"), headers=_headers(tok1))
        assert resp1.status == 200

        _mint_session(s, "owner")  # 재발급 -> tok1 무효화

        resp2 = await client.post(
            "/mcp", json=_rpc("tools/list"), headers=_headers(tok1))
        assert resp2.status == 404
    finally:
        await client.close()
