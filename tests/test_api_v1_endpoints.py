"""Integration and E2E tests for `/api/v1/*` (`API-2`) via Starlette + `wrap_web_app`."""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from claire.api import security
from claire.api.accounts_routes import prepare_auth_db, prepare_client_db
from claire.api.v1 import build_v1_routes
from claire.api.v1.errors import v1_exception_handlers
from claire.core import ids
from claire.core.config import Settings
from claire.ingest.pipeline import IngestReport
from claire.ontology.base import Document, Entity, Relation
from claire.retrieval.query import SearchHit, SearchResult
from claire.store import accounts as accounts_store, db as dbm, kb_collab, permalinks, themes as themes_store
from claire.store.knowledge_base import KnowledgeBaseManager
from tests.auth_helpers import issue_oauth_token, issue_session_token


class _FakeIngestService:
    """Deterministic fake IngestService for testing API-2 without LLM/network calls."""

    def __init__(self, settings: Settings) -> None:
        self.s = settings
        self.provider = SimpleNamespace(name="mock", embed_model="mock-embed")
        self.provider_context = None
        self.provider_error = None
        self.ingest_calls: list[tuple[str, dict[str, Any]]] = []
        self.search_calls: list[tuple[str, dict[str, Any]]] = []

    def ingest(self, payload: str, **kwargs: Any) -> IngestReport:
        self.ingest_calls.append((payload, dict(kwargs)))
        mode = kwargs.get("extraction_mode", "provider")
        return IngestReport(
            document_id="doc_ingested01",
            title=f"Ingested: {payload[:32]}",
            source_type="url" if payload.startswith("http") else "note",
            summary="Synthetic summary",
            entities_created=1,
            staged=(mode == "client"),
            extraction_status="pending" if mode == "client" else "extracted",
            raw_chars=len(payload),
        )

    def search(self, query: str, **kwargs: Any) -> SearchResult:
        self.search_calls.append((query, dict(kwargs)))
        mode = kwargs.get("mode", "fts")
        limit = int(kwargs.get("limit", 8))
        summarize = bool(kwargs.get("summarize", False))
        hits = [
            SearchHit(
                entity=Entity(
                    id=f"ent_{idx:012d}",
                    name=f"Entity {idx} ({query})",
                    type="Concept",
                    observations=["obs"],
                    sources=["doc_000000000001"],
                ),
                score=1.0 / (idx + 1),
                via=["fts"] if mode == "fts" else ["fts", "vec"],
            )
            for idx in range(min(limit, 30))
        ]
        return SearchResult(
            query=query,
            hits=hits,
            answer="Synthetic answer" if summarize else None,
        )


class _FakeServicePool:
    def __init__(self, settings: Settings, svc: _FakeIngestService) -> None:
        self._svc = svc
        self.shared_provider = svc.provider
        self.removed: list[int] = []

    def get_service(self, _kb_id: int, *, acting_account_id: int | None = None) -> _FakeIngestService:
        del acting_account_id
        return self._svc

    def remove_service(self, kb_id: int) -> None:
        self.removed.append(kb_id)


@dataclass
class _World:
    client: TestClient
    settings: Settings
    fake_svc: _FakeIngestService
    admin_session: str
    alice_session: str
    alice_oauth_write: str
    alice_oauth_readonly: str
    bob_oauth_write: str
    alice_personal_number: str
    shared_public_number: str
    theme_number: str
    doc_personal_id: str
    doc_shared_id: str
    ent_a_id: str
    ent_b_id: str
    ent_c_id: str


def _make_settings(tmp_path: Path) -> Settings:
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir(parents=True, exist_ok=True)
    return Settings(
        _env_file=None,
        CLAIRE_ENVIRONMENT="development",
        CLAIRE_FQDN="http://127.0.0.1:8765",
        CLAIRE_DB_PATH=str(data_dir / "claire.db"),
        CLAIRE_PLUGINS_DIR=str(plugins_dir),
        CLAIRE_CORS_ALLOWED_ORIGINS="",
        CLAIRE_DATA_LIFECYCLE="purgeable",
        CLAIRE_PROVIDER="mock",
    )


@pytest.fixture()
def v1_world(tmp_path: Path) -> Iterator[_World]:
    s = _make_settings(tmp_path)

    conn = dbm.connect(s.db_file)
    try:
        dbm.init_db(conn)
    finally:
        conn.close()
    prepare_auth_db(s.data_dir)
    prepare_client_db(s.db_file)

    mgr = KnowledgeBaseManager(s)
    conn = dbm.connect_existing(s.db_file)
    try:
        acct_store = accounts_store.AccountStore(conn)
        acct_store.complete_setup(1, username="adminuser", display_name="Admin")
        admin = acct_store.admin()
        alice = acct_store.create_member(username="alice", display_name="Alice")
        bob = acct_store.create_member(username="bob", display_name="Bob")
        acct_store.set_setting("mode", "network", updated_by=admin.id)

        admin_id = admin.id
        alice_id = alice.id
        bob_id = bob.id

        raw_dir = s.data_dir / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        raw_file = raw_dir / "hello.txt"
        raw_file.write_text("Original source content", encoding="utf-8")

        alice_kb = mgr.define_kb("Alice Personal", is_personal=True, is_public=False, account_id=alice.id)
        mgr.define_kb("Bob Personal", is_personal=True, is_public=False, account_id=bob.id)
        shared_kb = mgr.define_kb("Shared Cloud", description="Cloud KB", knowledge_type="real", is_public=True, account_id=alice.id)
        theme_row = themes_store.create_theme(conn, label="Cloud Theme", description="Cloud", knowledge_type="real")
        themes_store.add_theme_member(conn, theme_id=int(theme_row["id"]), kb_id=shared_kb.id, source="admin")

        doc_p = "doc_000000000001"
        doc_p2 = "doc_000000000002"
        doc_s = "doc_000000000003"
        for did, title, kb_id, ts in (
            (doc_p, "Alice First Doc", alice_kb.id, 100.0),
            (doc_p2, "Alice Second Doc", alice_kb.id, 200.0),
            (doc_s, "Shared Doc", shared_kb.id, 300.0),
        ):
            dbm.insert_document(
                conn,
                Document(
                    id=did,
                    title=title,
                    url=f"https://example.test/{did}",
                    canonical_url=f"https://example.test/{did}",
                    source_type="web",
                    raw_text=f"Body for {title}",
                    fetched_at=ts,
                ),
                kb_id=kb_id,
            )
            dbm.set_document_detail(conn, did, f"= {title}\n\nDetailed text.", format="adoc", html=f"<p>{title}</p>", kb_id=kb_id)
            dbm.log_extraction(
                conn,
                document_id=did,
                provider="mock",
                model="mock",
                prompt_version="v1",
                raw_response=json.dumps({"summary": f"Summary of {title}"}),
                kb_id=kb_id,
            )
            permalinks.ensure_document(conn, did, kb_id)

        conn.execute(
            "INSERT INTO raw_inbox (kb_id, document_id, source, kind, payload, file_ref, file_name, received_at, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (alice_kb.id, doc_p, "test", "document", "Original source content", str(raw_file), "hello.txt", 100.0, "done"),
        )

        ent_a = Entity(id="ent_aaaaaaaaaaaa", name="vSAN", type="Tech", observations=["Storage platform"], sources=[doc_p])
        ent_b = Entity(id="ent_bbbbbbbbbbbb", name="ESXi", type="Tech", observations=["Hypervisor"], sources=[doc_p2])
        ent_c = Entity(id="ent_cccccccccccc", name="VMware", type="Org", observations=["Vendor"], sources=[doc_p])
        for ent in (ent_a, ent_b, ent_c):
            dbm.upsert_entity(conn, ent, kb_id=alice_kb.id)
        dbm.upsert_relation(conn, Relation(type="runs_on", source_id=ent_a.id, target_id=ent_b.id, sources=[doc_p]), kb_id=alice_kb.id)
        dbm.upsert_relation(conn, Relation(type="made_by", source_id=ent_a.id, target_id=ent_c.id, sources=[doc_p]), kb_id=alice_kb.id)
        dbm.upsert_relation(conn, Relation(type="uses", source_id=ent_b.id, target_id=ent_c.id, sources=[doc_p2]), kb_id=alice_kb.id)

        ent_shared = Entity(id="ent_dddddddddddd", name="SharedConcept", type="Concept", observations=["Public"], sources=[doc_s])
        dbm.upsert_entity(conn, ent_shared, kb_id=shared_kb.id)
        conn.commit()
    finally:
        conn.close()

    admin_session = issue_session_token(s, account_id=admin_id, scope="owner")
    alice_session = issue_session_token(s, account_id=alice_id, scope="owner")
    alice_oauth_write = issue_oauth_token(s, account_id=alice_id, scope="write", client_id="agent-client-1")
    alice_oauth_readonly = issue_oauth_token(s, account_id=alice_id, scope="readonly", client_id="reader-client-1")
    bob_oauth_write = issue_oauth_token(s, account_id=bob_id, scope="write", client_id="agent-client-2")

    fake_svc = _FakeIngestService(s)
    fake_pool = _FakeServicePool(s, fake_svc)
    v1_routes = build_v1_routes(s, service=fake_svc, theme_manager=mgr, service_pool=fake_pool)
    inner = Starlette(routes=v1_routes, exception_handlers=v1_exception_handlers())
    wrapped = security.wrap_web_app(inner, s, theme_manager=mgr)
    client = TestClient(wrapped, base_url="http://127.0.0.1:8765")
    yield _World(
        client=client,
        settings=s,
        fake_svc=fake_svc,
        admin_session=admin_session,
        alice_session=alice_session,
        alice_oauth_write=alice_oauth_write,
        alice_oauth_readonly=alice_oauth_readonly,
        bob_oauth_write=bob_oauth_write,
        alice_personal_number=ids.format_public_id(int(alice_kb.number or 0)),
        shared_public_number=ids.format_public_id(int(shared_kb.number or 0)),
        theme_number=ids.format_public_id(int(theme_row["number"])),
        doc_personal_id=doc_p,
        doc_shared_id=doc_s,
        ent_a_id=ent_a.id,
        ent_b_id=ent_b.id,
        ent_c_id=ent_c.id,
    )


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _cookie(token: str) -> dict[str, str]:
    return {"Cookie": f"claire_session={token}"}


def test_health_me_and_auth_boundaries(v1_world: _World) -> None:
    r_health = v1_world.client.get("/api/v1/health")
    assert r_health.status_code == 200
    assert r_health.json() == {"ok": True, "api_version": "v1", "database": "ok"}

    # Anonymous caller on /api/v1/me is rejected with v1 error envelope.
    r_anon_me = v1_world.client.get("/api/v1/me")
    assert r_anon_me.status_code == 401
    assert r_anon_me.json()["error"]["code"] == "authentication_required"

    # Unknown / retired manual-style bearer token is refused at AuthenticationMiddleware (ENV-4 / AR5).
    r_manual = v1_world.client.get("/api/v1/me", headers=_bearer("owner-" + "a" * 32))
    assert r_manual.status_code == 404

    # OAuth 2.1 readonly bearer succeeds without exposing username.
    r_oauth = v1_world.client.get("/api/v1/me", headers=_bearer(v1_world.alice_oauth_readonly))
    assert r_oauth.status_code == 200
    assert r_oauth.json() == {
        "scope": "readonly",
        "server": {"mode": "network"},
        "relation": "delegate",
        "is_admin": False,
    }
    assert "username" not in r_oauth.json()

    # Admin session returns is_admin=True.
    r_admin = v1_world.client.get("/api/v1/me", headers=_cookie(v1_world.admin_session))
    assert r_admin.status_code == 200
    assert r_admin.json()["is_admin"] is True


def test_ct23_kb_resolution_and_retired_theme_rejection(v1_world: _World) -> None:
    # Anonymous cannot resolve "personal" (404 existence-hiding).
    r_anon = v1_world.client.get("/api/v1/kbs/personal")
    assert r_anon.status_code == 404
    assert r_anon.json()["error"]["code"] == "not_found"

    # Integer kb_id and theme labels return 400 invalid_kb.
    for bad_kb in ("0", "1", "general", "00123"):
        resp = v1_world.client.get(f"/api/v1/kbs/{bad_kb}", headers=_bearer(v1_world.alice_oauth_readonly))
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "invalid_kb"

    # Retired ?theme= parameter is rejected.
    r_theme = v1_world.client.get("/api/v1/kbs/personal?theme=0", headers=_bearer(v1_world.alice_oauth_readonly))
    assert r_theme.status_code == 400
    assert r_theme.json()["error"]["code"] == "invalid_request"

    # Bob cannot read Alice's private personal KB by its external number (404 existence-hiding).
    r_cross = v1_world.client.get(f"/api/v1/kbs/{v1_world.alice_personal_number}", headers=_bearer(v1_world.bob_oauth_write))
    assert r_cross.status_code == 404
    assert r_cross.json()["error"]["code"] == "not_found"

    # Alice resolves her own personal KB and sees extraction_modes with write token.
    r_personal = v1_world.client.get("/api/v1/kbs/personal", headers=_bearer(v1_world.alice_oauth_write))
    assert r_personal.status_code == 200
    body = r_personal.json()
    assert body["knowledge_base"]["is_personal"] is True
    assert body["extraction_modes"] == ["provider", "client"]


def test_kbs_themes_and_documents_crud_and_pagination(v1_world: _World) -> None:
    # List KBs & themes with keyset pagination.
    r_kbs = v1_world.client.get("/api/v1/kbs?knowledge_type=real&limit=1", headers=_bearer(v1_world.alice_oauth_readonly))
    assert r_kbs.status_code == 200
    kbs_data = r_kbs.json()
    assert kbs_data["total"] == 1
    assert kbs_data["items"][0]["number"] == v1_world.shared_public_number
    assert kbs_data["next_cursor"] is None

    r_themes = v1_world.client.get("/api/v1/themes?limit=1", headers=_bearer(v1_world.alice_oauth_readonly))
    assert r_themes.status_code == 200
    assert r_themes.json()["items"][0]["number"] == v1_world.theme_number

    # Paginate Alice's personal documents with limit=1 using opaque keyset cursor.
    r_p1 = v1_world.client.get("/api/v1/kbs/personal/documents?limit=1", headers=_bearer(v1_world.alice_oauth_readonly))
    assert r_p1.status_code == 200
    p1 = r_p1.json()
    assert p1["total"] == 2
    assert len(p1["items"]) == 1
    assert p1["items"][0]["id"] == "doc_000000000002"
    assert p1["next_cursor"] is not None

    r_p2 = v1_world.client.get(
        f"/api/v1/kbs/personal/documents?limit=1&cursor={p1['next_cursor']}",
        headers=_bearer(v1_world.alice_oauth_readonly),
    )
    assert r_p2.status_code == 200
    p2 = r_p2.json()
    assert len(p2["items"]) == 1
    assert p2["items"][0]["id"] == "doc_000000000001"
    assert p2["next_cursor"] is None

    # Document detail uses canonical `entities[]` and `/silvercord/` permalink.
    r_doc = v1_world.client.get(f"/api/v1/kbs/personal/documents/{v1_world.doc_personal_id}", headers=_bearer(v1_world.alice_oauth_readonly))
    assert r_doc.status_code == 200
    doc_data = r_doc.json()
    assert doc_data["permalink"].startswith("/silvercord/")
    assert "entities" in doc_data and "nodes" not in doc_data
    assert {e["name"] for e in doc_data["entities"]} == {"vSAN", "VMware"}

    # Raw file endpoint returns sandbox headers and file content.
    r_raw = v1_world.client.get(f"/api/v1/kbs/personal/documents/{v1_world.doc_personal_id}/raw", headers=_bearer(v1_world.alice_oauth_readonly))
    assert r_raw.status_code == 200
    assert r_raw.text == "Original source content"
    assert "sandbox" in r_raw.headers["content-security-policy"]

    # Export endpoint returns Markdown attachment.
    r_exp = v1_world.client.get(f"/api/v1/kbs/personal/documents/{v1_world.doc_personal_id}/export", headers=_bearer(v1_world.alice_oauth_readonly))
    assert r_exp.status_code == 200
    assert r_exp.headers["content-type"].startswith("text/markdown")
    assert "Alice First Doc" in r_exp.text

    # Strict application/json validation and PATCH document flags.
    r_bad_ct = v1_world.client.patch(
        f"/api/v1/kbs/personal/documents/{v1_world.doc_personal_id}",
        content='{"pinned": true}',
        headers={**_cookie(v1_world.alice_session), "Content-Type": "text/plain", "Origin": "http://127.0.0.1:8765"},
    )
    assert r_bad_ct.status_code == 400
    assert r_bad_ct.json()["error"]["code"] == "invalid_json"

    r_patch = v1_world.client.patch(
        f"/api/v1/kbs/personal/documents/{v1_world.doc_personal_id}",
        json={"pinned": True, "seen": True, "title": "Renamed Doc"},
        headers={**_cookie(v1_world.alice_session), "Origin": "http://127.0.0.1:8765"},
    )
    assert r_patch.status_code == 200
    assert r_patch.json()["pinned"] is True
    assert r_patch.json()["title"] == "Renamed Doc"

    # Shared KB DELETE document refuses with 403 kb_remove_by_contribution.
    r_del_shared = v1_world.client.delete(
        f"/api/v1/kbs/{v1_world.shared_public_number}/documents/{v1_world.doc_shared_id}",
        headers={**_cookie(v1_world.alice_session), "Origin": "http://127.0.0.1:8765"},
    )
    assert r_del_shared.status_code == 403
    assert r_del_shared.json()["error"]["code"] == "kb_remove_by_contribution"


def test_ingest_aed4_rev_and_stream(v1_world: _World) -> None:
    # Missing extraction_mode -> 400 invalid_extraction_mode.
    r_missing = v1_world.client.post(
        "/api/v1/kbs/personal/ingest",
        json={"payload": "Note text"},
        headers=_bearer(v1_world.alice_oauth_write),
    )
    assert r_missing.status_code == 400
    assert r_missing.json()["error"]["code"] == "invalid_extraction_mode"

    # Agent client OAuth token with extraction_mode="provider" -> 403 agent_client_provider_refused.
    r_refused = v1_world.client.post(
        "/api/v1/kbs/personal/ingest",
        json={"payload": "Note text", "extraction_mode": "provider", "renditions": []},
        headers=_bearer(v1_world.alice_oauth_write),
    )
    assert r_refused.status_code == 403
    assert r_refused.json()["error"]["code"] == "agent_client_provider_refused"

    # Agent client OAuth token with extraction_mode="client" succeeds (staged=True).
    r_client = v1_world.client.post(
        "/api/v1/kbs/personal/ingest",
        json={"payload": "https://example.test/article", "extraction_mode": "client", "renditions": []},
        headers=_bearer(v1_world.alice_oauth_write),
    )
    assert r_client.status_code == 200
    assert r_client.json()["staged"] is True
    assert r_client.json()["extraction_status"] == "pending"

    # Same-origin owner browser session can use extraction_mode="provider" on stream endpoint.
    r_stream = v1_world.client.post(
        "/api/v1/kbs/personal/ingest/stream",
        json={"payload": "Stream note", "extraction_mode": "provider", "renditions": []},
        headers={**_cookie(v1_world.alice_session), "Origin": "http://127.0.0.1:8765"},
    )
    assert r_stream.status_code == 200
    assert r_stream.headers["content-type"].startswith("application/x-ndjson")
    lines = [json.loads(line) for line in r_stream.text.strip().splitlines()]
    assert lines[-1]["done"] is True
    assert lines[-1]["result"]["document_id"] == "doc_ingested01"


def test_graph_entities_neighbors_path_and_anonymous_search_cap(v1_world: _World) -> None:
    # Anonymous search on shared public KB enforces FTS-only, no summary, and max 20 hits.
    r_anon_search = v1_world.client.post(
        f"/api/v1/kbs/{v1_world.shared_public_number}/search",
        json={"query": "Shared", "limit": 50, "mode": "hybrid", "summarize": True},
    )
    assert r_anon_search.status_code == 200
    anon_body = r_anon_search.json()
    assert anon_body["mode"] == "fts"
    assert anon_body["answer"] is None
    assert len(anon_body["hits"]) == 20
    assert v1_world.fake_svc.search_calls[-1][1]["mode"] == "fts"
    assert v1_world.fake_svc.search_calls[-1][1]["limit"] == 20
    assert v1_world.fake_svc.search_calls[-1][1]["summarize"] is False

    # Canonical graph response (entities[], relations[]).
    r_graph = v1_world.client.get("/api/v1/kbs/personal/graph", headers=_bearer(v1_world.alice_oauth_readonly))
    assert r_graph.status_code == 200
    g = r_graph.json()
    assert len(g["entities"]) == 3
    assert len(g["relations"]) == 3
    assert g["stats"] == {"entities": 3, "relations": 3, "max_degree": 2}

    # Entity detail includes `/entity/` permalink and canonical `relations[]`.
    r_ent = v1_world.client.get(
        f"/api/v1/kbs/personal/entities/{v1_world.ent_a_id}?docs=brief",
        headers=_bearer(v1_world.alice_oauth_readonly),
    )
    assert r_ent.status_code == 200
    ent_body = r_ent.json()
    assert ent_body["permalink"].startswith("/entity/")
    assert len(ent_body["relations"]) == 2

    # 1-hop neighbors with exclude_ids.
    r_nbrs = v1_world.client.get(
        f"/api/v1/kbs/personal/entities/{v1_world.ent_a_id}/neighbors?exclude_ids={v1_world.ent_c_id}&limit=10",
        headers=_bearer(v1_world.alice_oauth_readonly),
    )
    assert r_nbrs.status_code == 200
    nbrs = r_nbrs.json()
    assert [item["id"] for item in nbrs["items"]] == [v1_world.ent_b_id]

    # Shortest path BFS.
    r_path = v1_world.client.get(
        f"/api/v1/kbs/personal/graph/path?from_entity_id={v1_world.ent_a_id}&to_entity_id={v1_world.ent_c_id}&max_hops=2",
        headers=_bearer(v1_world.alice_oauth_readonly),
    )
    assert r_path.status_code == 200
    path_body = r_path.json()
    assert path_body["found"] is True
    assert [step["id"] for step in path_body["path"]] == [v1_world.ent_a_id, v1_world.ent_c_id]


def test_e2e_v1_kb_and_document_lifecycle_round_trip(v1_world: _World) -> None:
    session_hdrs = {**_cookie(v1_world.alice_session), "Origin": "http://127.0.0.1:8765"}
    admin_hdrs = {**_cookie(v1_world.admin_session), "Origin": "http://127.0.0.1:8765"}

    # 1. Create a shared KB as Alice (owner session).
    r_create = v1_world.client.post(
        "/api/v1/kbs",
        json={"label": "E2E KB", "knowledge_type": "real", "description": "Round trip"},
        headers=session_hdrs,
    )
    assert r_create.status_code == 201
    kb_num = r_create.json()["knowledge_base"]["number"]
    assert kb_num is not None

    # 2. Update shared KB settings; non-admin cannot change presentation_enabled (403).
    r_forbidden_patch = v1_world.client.patch(
        f"/api/v1/kbs/{kb_num}",
        json={"presentation_enabled": True},
        headers=session_hdrs,
    )
    assert r_forbidden_patch.status_code == 403
    assert r_forbidden_patch.json()["error"]["code"] == "presentation_enabled_admin_only"

    r_patch = v1_world.client.patch(
        f"/api/v1/kbs/{kb_num}",
        json={"description": "Updated round trip"},
        headers=session_hdrs,
    )
    assert r_patch.status_code == 200
    assert r_patch.json()["knowledge_base"]["description"] == "Updated round trip"

    # 3. Non-admin DELETE is hidden as 404; admin dry-run previews; admin ?apply=true deletes.
    r_non_admin_del = v1_world.client.delete(f"/api/v1/kbs/{kb_num}", headers=session_hdrs)
    assert r_non_admin_del.status_code == 404

    r_dry_del = v1_world.client.delete(f"/api/v1/kbs/{kb_num}", headers=admin_hdrs)
    assert r_dry_del.status_code == 200
    assert r_dry_del.json()["applied"] is False

    r_exec_del = v1_world.client.delete(f"/api/v1/kbs/{kb_num}?apply=true", headers=admin_hdrs)
    assert r_exec_del.status_code == 200
    assert r_exec_del.json()["applied"] is True

    # Deleted KB now returns 404.
    v1_world.client.cookies.clear()
    r_after = v1_world.client.get(f"/api/v1/kbs/{kb_num}", headers=_bearer(v1_world.alice_oauth_readonly))
    assert r_after.status_code == 404
    assert r_after.json()["error"]["code"] == "not_found"

    # 4. Delete personal document via dry_run then real delete.
    r_doc_dry = v1_world.client.delete(
        f"/api/v1/kbs/personal/documents/{v1_world.doc_personal_id}?dry_run=true",
        headers=session_hdrs,
    )
    assert r_doc_dry.status_code == 200
    assert r_doc_dry.json()["deleted"] is False

    r_doc_del = v1_world.client.delete(
        f"/api/v1/kbs/personal/documents/{v1_world.doc_personal_id}",
        headers=session_hdrs,
    )
    assert r_doc_del.status_code == 200
    assert r_doc_del.json()["deleted"] is True

    r_doc_after = v1_world.client.get(
        f"/api/v1/kbs/personal/documents/{v1_world.doc_personal_id}",
        headers=_bearer(v1_world.alice_oauth_readonly),
    )
    assert r_doc_after.status_code == 404
