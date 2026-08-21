"""익명 HTTP 읽기 경로가 영속 상태를 바꾸지 않는다는 통합 계약."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from claire.api.security import ROUTE_POLICY, wrap_web_app
from claire.api.server import create_app
from claire.ontology.base import Document, Entity
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


def _logical_dump(db_file: Path) -> tuple[str, ...]:
    conn = dbm.connect_existing(db_file, readonly=True)
    try:
        return tuple(conn.iterdump())
    finally:
        conn.close()


def _tree_hash(root: Path, *, excluded: frozenset[Path] = frozenset()) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        if path in excluded:
            continue
        relative = path.relative_to(root).as_posix().encode()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        content = path.read_bytes()
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def test_all_anonymous_reads_preserve_database_data_and_vault(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    vault_dir = tmp_path / "vault"
    data_dir.mkdir()
    vault_dir.mkdir()
    db_file = data_dir / "claire.db"
    (data_dir / "raw.txt").write_text("immutable raw artifact", encoding="utf-8")
    image_dir = data_dir / "images"
    image_dir.mkdir()
    (image_dir / "sample.png").write_bytes(b"\x89PNG\r\n\x1a\nimmutable")
    (vault_dir / "faith.md").write_text("# Faith\nimmutable vault", encoding="utf-8")

    conn = dbm.connect(db_file)
    dbm.init_db(conn)
    dbm.insert_document(
        conn,
        Document(
            id="doc-1",
            url="https://example.test/faith",
            title="Faith document",
            raw_text="Faith is trust and confidence.",
            source_type="web",
            content_hash="faith-hash",
        ),
    )
    dbm.insert_document(
        conn,
        Document(
            id="doc-2",
            url="https://example.test/grace",
            title="Grace document",
            raw_text="Grace is unmerited favor.",
            source_type="web",
            content_hash="grace-hash",
        ),
    )
    dbm.upsert_entity(
        conn,
        Entity(
            id="entity-1",
            type="Concept",
            name="Faith",
            observations=["trust and confidence"],
            sources=["doc-1"],
        ),
    )
    dbm.upsert_entity(
        conn,
        Entity(
            id="entity-2",
            type="Concept",
            name="Grace",
            observations=["unmerited favor"],
            sources=["doc-2"],
        ),
    )
    assert dbm.set_document_hidden(conn, "doc-1", True)
    share_token = dbm.create_doc_share(conn, "doc-1")
    conn.close()

    settings = _Settings(
        db_file=db_file,
        data_dir=data_dir,
        vault_dir=vault_dir,
    )
    app = create_app(settings)
    sqlite_files = frozenset(
        {
            db_file,
            db_file.with_name(db_file.name + "-shm"),
            db_file.with_name(db_file.name + "-wal"),
        }
    )

    with TestClient(app, base_url=settings.public_url) as client:
        before_dump = _logical_dump(db_file)
        before_data = _tree_hash(data_dir, excluded=sqlite_files)
        before_vault = _tree_hash(vault_dir)

        requests = [
            ("GET", "/health", None),
            ("HEAD", "/health", None),
            ("GET", "/p", {"s": share_token}),
            ("HEAD", "/p", {"s": share_token}),
            ("GET", "/image", {"p": "images/sample.png"}),
            ("HEAD", "/image", {"p": "images/sample.png"}),
            ("GET", "/", None),
            ("HEAD", "/", None),
            ("GET", "/whoami", None),
            ("HEAD", "/whoami", None),
            ("GET", "/stats", None),
            ("HEAD", "/stats", None),
            ("GET", "/graph", None),
            ("HEAD", "/graph", None),
            ("GET", "/node", {"id": "entity-2"}),
            ("HEAD", "/node", {"id": "entity-2"}),
            ("GET", "/documents", None),
            ("HEAD", "/documents", None),
            ("GET", "/document", {"id": "doc-2"}),
            ("HEAD", "/document", {"id": "doc-2"}),
        ]
        responses = [
            client.request(method, path, params=params)
            for method, path, params in requests
        ]
        responses.append(
            client.post(
                "/search",
                json={
                    "query": "Grace",
                    "limit": 999,
                    "summarize": True,
                    "mode": "hybrid",
                },
            )
        )
        # 익명 요청 시 숨김 문서/엔티티는 목록 제외 및 404
        hidden_documents = client.get("/documents")
        hidden_detail = client.get("/document", params={"id": "doc-1"})
        hidden_node = client.get("/node", params={"id": "entity-1"})
        hidden_search = client.post("/search", json={"query": "Faith", "limit": 10})

        assert all(response.status_code == 200 for response in responses)
        assert all("set-cookie" not in response.headers for response in responses)
        assert responses[-1].json()["mode"] == "fts"

        # 익명 읽기에서 숨김 문서는 목록에서 제외되고 상세는 404
        assert all(doc["id"] != "doc-1" for doc in hidden_documents.json()["documents"])
        assert any(doc["id"] == "doc-2" for doc in hidden_documents.json()["documents"])
        assert hidden_detail.status_code == 404
        assert hidden_node.status_code == 404
        assert all(h["id"] != "entity-1" for h in hidden_search.json()["hits"])

        # 소유자(owner) 토큰으로 요청 시에는 숨김 문서/엔티티 정상 접근
        owner_headers = {"Authorization": f"Bearer {OWNER_TOKEN}"}
        owner_docs = client.get("/documents", headers=owner_headers)
        assert owner_docs.status_code == 200
        assert any(doc["id"] == "doc-1" and doc["hidden"] == 1 for doc in owner_docs.json()["documents"])
        owner_detail = client.get("/document", params={"id": "doc-1"}, headers=owner_headers)
        assert owner_detail.status_code == 200
        assert owner_detail.json()["hidden"] is True
        owner_node = client.get("/node", params={"id": "entity-1"}, headers=owner_headers)
        assert owner_node.status_code == 200

        assert _logical_dump(db_file) == before_dump
        assert _tree_hash(data_dir, excluded=sqlite_files) == before_data
        assert _tree_hash(vault_dir) == before_vault


def test_all_owner_routes_are_rejected_before_handler_for_anonymous(
    tmp_path: Path,
) -> None:
    called: list[str] = []

    async def owner_handler(request: Request) -> JSONResponse:
        called.append(request.url.path)
        return JSONResponse({"unexpected": True})

    owner_paths = sorted(
        path
        for (method, path), rule in ROUTE_POLICY.items()
        if method == "POST" and rule.access == "owner"
    )
    inner = Starlette(
        routes=[
            Route(path, owner_handler, methods=["POST"])
            for path in owner_paths
        ]
    )
    settings = _Settings(
        db_file=tmp_path / "claire.db",
        data_dir=tmp_path,
        vault_dir=tmp_path / "vault",
    )
    app = wrap_web_app(inner, settings)

    with TestClient(app, base_url=settings.public_url) as client:
        responses = [client.post(path) for path in owner_paths]

    assert owner_paths
    assert all(response.status_code == 404 for response in responses)
    assert called == []
