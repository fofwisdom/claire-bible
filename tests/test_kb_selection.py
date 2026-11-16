"""Selection of a knowledge base (SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md Section 5.2, CT23, unit CT-0).

1. A namespaced route: only the path selects. A pinned path selects that knowledge base; else the personal knowledge base.
2. A route without a namespace that acts on one knowledge base: the parameter ``kb`` (number) is required, else 400 ``kb_required``.
3. Ingest writes only to the personal knowledge base of the credential; a path that pins another one gets 403.
5. The old names (query and body ``theme``, ``X-Claire-Theme``, internal IDs) are unknown fields: a request with them gives the same result as without them.
"""

from __future__ import annotations

import json

import pytest
from starlette.testclient import TestClient

from claire import ids
from claire.api.server import create_app
from claire.config import Settings
from claire.ingest.service import IngestService
from claire.store.knowledge_base import KnowledgeBaseManager

OWNER_TOKEN = "owner-" + ("k" * 32)
OWNER_HEADERS = {"Authorization": f"Bearer {OWNER_TOKEN}"}


def _settings(tmp_path) -> Settings:
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    (tmp_path / "vault").mkdir(parents=True, exist_ok=True)
    return Settings(
        CLAIRE_DB_PATH=str(data_dir / "claire.db"),
        CLAIRE_VAULT_PATH=str(tmp_path / "vault"),
        CLAIRE_PROVIDER="mock",
        CLAIRE_ENVIRONMENT="development",
        CLAIRE_PUBLIC_URL="http://127.0.0.1:8765",
        CLAIRE_INJECT_TOKEN=OWNER_TOKEN,
        CLAIRE_ANONYMOUS_READONLY=False,
        CLAIRE_MULTI_THEME=True,
    )


@pytest.fixture
def kb_env(tmp_path):
    settings = _settings(tmp_path)
    tm = KnowledgeBaseManager(settings)
    other = tm.define_kb("Other knowledge base")
    # Seed one document in each knowledge base without the HTTP ingest (which writes only to the personal one).
    IngestService(tm.get_settings_for_kb(0, settings), kb_id=0).ingest("Personal\npersonal knowledge text.", source="cli", expand_max=0)
    IngestService(tm.get_settings_for_kb(other.id, settings), kb_id=other.id).ingest("Other\nother knowledge text.", source="cli", expand_max=0)
    with TestClient(create_app(settings=settings), base_url=settings.public_url) as client:
        yield client, settings, tm, other


def _titles(resp) -> list[str]:
    assert resp.status_code == 200, resp.text
    return sorted(d["title"] for d in resp.json()["documents"])


def _number(kb) -> str:
    return ids.format_public_id(kb.number)


def test_old_names_give_the_same_result_as_without_them(kb_env):
    client, _settings, _tm, other = kb_env
    plain = _titles(client.get("/documents", headers=OWNER_HEADERS))
    assert plain == ["Personal"]
    for variant in (
        client.get(f"/documents?theme={_number(other)}", headers=OWNER_HEADERS),
        client.get(f"/documents?theme={other.id}", headers=OWNER_HEADERS),
        client.get("/documents?theme=Other%20knowledge%20base", headers=OWNER_HEADERS),
        client.get("/documents", headers={**OWNER_HEADERS, "X-Claire-Theme": _number(other)}),
    ):
        assert _titles(variant) == plain
        assert "deprecation" not in variant.headers


def test_the_path_selects_the_knowledge_base(kb_env):
    client, _settings, _tm, other = kb_env
    assert _titles(client.get(f"/themes/{_number(other)}/documents", headers=OWNER_HEADERS)) == ["Other"]
    assert client.get(f"/themes/{(1 << 60) + 3}/documents", headers=OWNER_HEADERS).status_code == 404


def test_e2e_ingest_writes_only_to_the_personal_knowledge_base(kb_env):
    client, _settings, _tm, other = kb_env
    resp = client.post("/ingest", json={"payload": "New\na new text for the personal knowledge base.", "theme": _number(other)}, headers=OWNER_HEADERS)
    assert resp.status_code == 200, resp.text
    assert resp.json()["kb_id"] == 0
    assert _titles(client.get("/documents", headers=OWNER_HEADERS)) == ["New", "Personal"]
    assert _titles(client.get(f"/themes/{_number(other)}/documents", headers=OWNER_HEADERS)) == ["Other"]

    pinned = client.post(f"/themes/{_number(other)}/ingest", json={"payload": "Title: Pinned\nBody: text."}, headers=OWNER_HEADERS)
    assert pinned.status_code == 403
    assert pinned.json() == {"error": "kb_ingest_personal_only"}


def test_routes_without_a_namespace_need_kb(kb_env):
    client, _settings, _tm, other = kb_env
    missing = client.post("/api/me/data-bundles", json={}, headers=OWNER_HEADERS)
    assert missing.status_code == 400
    assert missing.json()["error"] == "kb_required"
    old_name = client.post("/api/me/data-bundles", json={"theme": _number(other)}, headers=OWNER_HEADERS)
    assert old_name.status_code == 400

    assert client.get("/syndication/preferences?kb=nope", headers=OWNER_HEADERS).status_code == 404


def _mcp(client: TestClient, name: str, arguments: dict) -> dict:
    resp = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments}},
        headers=OWNER_HEADERS,
    )
    assert resp.status_code == 200, resp.text
    return json.loads(resp.json()["result"]["content"][0]["text"])


def test_mcp_read_tools_need_kb_as_a_number(kb_env):
    client, _settings, _tm, other = kb_env
    assert _mcp(client, "stats", {}) == {"error": "kb_required"}
    assert _mcp(client, "stats", {"kb": str(other.id)}) == {"error": "theme not found"}
    assert _mcp(client, "stats", {"kb": "Other knowledge base"}) == {"error": "theme not found"}
    stats = _mcp(client, "stats", {"kb": _number(other)})
    assert stats.get("documents") == 1
