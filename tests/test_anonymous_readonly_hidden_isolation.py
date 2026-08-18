"""익명 읽기 모드(CLAIRE_ANONYMOUS_READONLY=1)에서 숨김 문서/엔티티 격리 검증."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from starlette.testclient import TestClient

from claire.api.server import create_app
from claire.ontology.base import Document, Entity, Relation
from claire.store import db as dbm


OWNER_TOKEN = "owner-" + ("o" * 32)
READONLY_TOKEN = "readonly-" + ("r" * 32)


@dataclass
class _Settings:
    db_file: Path
    data_dir: Path
    vault_dir: Path
    environment: str = "development"
    public_url: str = "http://127.0.0.1:8765"
    inject_token: str = OWNER_TOKEN
    readonly_token: str = READONLY_TOKEN
    cors_allowed_origins: str = ""
    anonymous_readonly: bool = True
    vector_backend: str = "brute"
    effective_provider: str = "mock"


def test_hidden_document_and_entity_isolation_for_anonymous(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    vault_dir = tmp_path / "vault"
    data_dir.mkdir()
    vault_dir.mkdir()
    db_file = data_dir / "claire.db"

    conn = dbm.connect(db_file)
    dbm.init_db(conn)

    # 1) 숨김 문서 doc-secret 및 비밀 엔티티
    dbm.insert_document(
        conn,
        Document(
            id="doc-secret",
            url="https://example.test/secret",
            title="Secret Memo",
            raw_text="Top secret internal password and notes.",
            source_type="web",
            content_hash="secret-hash",
        ),
    )
    # 2) 공개 문서 doc-public 및 공개 엔티티
    dbm.insert_document(
        conn,
        Document(
            id="doc-public",
            url="https://example.test/public",
            title="Public Guide",
            raw_text="Public release notes and guide.",
            source_type="web",
            content_hash="public-hash",
        ),
    )
    # 3) 엔티티들:
    # ent-secret: 오직 doc-secret에만 속함
    dbm.upsert_entity(
        conn,
        Entity(
            id="ent-secret",
            type="Secret",
            name="ConfidentialCode",
            observations=["very sensitive data"],
            sources=["doc-secret"],
        ),
    )
    # ent-public: 오직 doc-public에만 속함
    dbm.upsert_entity(
        conn,
        Entity(
            id="ent-public",
            type="Guide",
            name="OpenManual",
            observations=["public documentation"],
            sources=["doc-public"],
        ),
    )
    # ent-shared: 둘 다에 속함
    dbm.upsert_entity(
        conn,
        Entity(
            id="ent-shared",
            type="Topic",
            name="Architecture",
            observations=["shared topic notes"],
            sources=["doc-secret", "doc-public"],
        ),
    )
    # 4) 관계:
    dbm.upsert_relation(
        conn,
        Relation(
            id="rel-1",
            type="mentions",
            source_id="ent-public",
            target_id="ent-shared",
        ),
    )
    dbm.upsert_relation(
        conn,
        Relation(
            id="rel-2",
            type="depends_on",
            source_id="ent-secret",
            target_id="ent-shared",
        ),
    )

    assert dbm.set_document_hidden(conn, "doc-secret", True)
    conn.close()

    settings = _Settings(
        db_file=db_file,
        data_dir=data_dir,
        vault_dir=vault_dir,
    )
    app = create_app(settings)

    with TestClient(app, base_url=settings.public_url) as client:
        # === 1. 익명 (Anonymous) 사용자 요청 ===
        # 1-1. 문서 목록
        docs_res = client.get("/documents")
        assert docs_res.status_code == 200
        docs_data = docs_res.json()["documents"]
        doc_ids = [d["id"] for d in docs_data]
        assert "doc-public" in doc_ids
        assert "doc-secret" not in doc_ids

        # 1-2. 문서 상세
        assert client.get("/document", params={"id": "doc-public"}).status_code == 200
        assert client.get("/document", params={"id": "doc-secret"}).status_code == 404

        # 1-3. 그래프
        graph_res = client.get("/graph")
        assert graph_res.status_code == 200
        graph_data = graph_res.json()
        graph_node_ids = {n["id"] for n in graph_data["nodes"]}
        assert "ent-public" in graph_node_ids
        assert "ent-shared" in graph_node_ids
        assert "ent-secret" not in graph_node_ids  # 숨김 전용 엔티티는 그래프에서 제거됨

        # ent-shared의 sources에는 doc-secret이 제거되고 doc-public만 남아야 함
        shared_node = next(n for n in graph_data["nodes"] if n["id"] == "ent-shared")
        assert shared_node["sources"] == ["doc-public"]

        # 엣지 확인: ent-secret에 연결된 rel-2는 제거되어야 함
        edge_pairs = {(e["from"], e["to"]) for e in graph_data["edges"]}
        assert ("ent-public", "ent-shared") in edge_pairs
        assert ("ent-secret", "ent-shared") not in edge_pairs

        # 1-4. 노드 상세
        assert client.get("/node", params={"id": "ent-public"}).status_code == 200
        assert client.get("/node", params={"id": "ent-secret"}).status_code == 404
        shared_node_detail = client.get("/node", params={"id": "ent-shared"})
        assert shared_node_detail.status_code == 200
        shared_docs = [d["id"] for d in shared_node_detail.json()["documents"]]
        assert shared_docs == ["doc-public"]

        # 1-5. 검색
        search_secret = client.post("/search", json={"query": "ConfidentialCode", "limit": 10})
        assert search_secret.status_code == 200
        assert all(h["id"] != "ent-secret" for h in search_secret.json()["hits"])

        search_public = client.post("/search", json={"query": "OpenManual", "limit": 10})
        assert search_public.status_code == 200
        assert any(h["id"] == "ent-public" for h in search_public.json()["hits"])

        # 1-6. 통계
        stats_res = client.get("/stats")
        assert stats_res.status_code == 200
        assert stats_res.json()["documents"] == 1  # 공개 문서만 카운트

        # === 2. 소유자 (Owner) 토큰 요청 ===
        owner_headers = {"Authorization": f"Bearer {OWNER_TOKEN}"}
        owner_docs = client.get("/documents", headers=owner_headers)
        assert owner_docs.status_code == 200
        owner_doc_ids = [d["id"] for d in owner_docs.json()["documents"]]
        assert "doc-secret" in owner_doc_ids
        assert "doc-public" in owner_doc_ids

        owner_secret_doc = client.get("/document", params={"id": "doc-secret"}, headers=owner_headers)
        assert owner_secret_doc.status_code == 200
        assert owner_secret_doc.json()["hidden"] is True

        owner_graph = client.get("/graph", headers=owner_headers)
        assert owner_graph.status_code == 200
        owner_node_ids = {n["id"] for n in owner_graph.json()["nodes"]}
        assert "ent-secret" in owner_node_ids

        owner_node = client.get("/node", params={"id": "ent-secret"}, headers=owner_headers)
        assert owner_node.status_code == 200

        owner_stats = client.get("/stats", headers=owner_headers)
        assert owner_stats.status_code == 200
        assert owner_stats.json()["documents"] == 2
