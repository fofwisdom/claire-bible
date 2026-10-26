"""Unit and integration tests for Graph Data Bundle API routes.

Covers:
- POST /graph/bundle requires owner authorization (raises 401 on non-owner scope, blocks unauthenticated/readonly).
- POST /graph/bundle creates bundle successfully with owner token.
- GET /graph/bundle?token=... downloads the .tar.zst file with proper media type and headers.
- Invalid token returns 404 and expired token returns 410.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.testclient import TestClient

from claire.api import server
from claire.config import Settings
from claire.graph_bundle import (
    GRAPH_BUNDLE_TTL_SECONDS,
    get_graph_bundles_dir,
    lookup_graph_bundle_by_token,
)
from claire.store import db as dbm

OWNER_TOKEN = "owner-" + ("o" * 32)
READONLY_TOKEN = "readonly-" + ("r" * 32)
OWNER_HEADERS = {"Authorization": f"Bearer {OWNER_TOKEN}"}
READONLY_HEADERS = {"Authorization": f"Bearer {READONLY_TOKEN}"}


def _seed_db(db_path: Path) -> None:
    conn = dbm.connect(db_path)
    dbm.init_db(conn)
    conn.execute(
        """
        INSERT INTO documents (
            id, url, canonical_url, title, author, published_at, fetched_at, raw_text, source_type
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "doc_1",
            "https://example.com/item1",
            "https://example.com/item1",
            "Item 1",
            "Author",
            "2026-10-26T00:00:00Z",
            1761436800.0,
            "HEAVY_RAW_TEXT_SHOULD_BE_REMOVED",
            "web",
        ),
    )
    conn.execute(
        """
        INSERT INTO entities (
            id, type, name, norm_name, aliases, props, observations, sources, provisional, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "ent_1",
            "Node",
            "Alpha Node",
            "alpha node",
            json.dumps(["Alpha"]),
            json.dumps({"category": "core"}),
            json.dumps(["Observed"]),
            json.dumps(["doc_1"]),
            0,
            1761436800.0,
            1761436800.0,
        ),
    )
    conn.execute(
        """
        INSERT INTO entities (
            id, type, name, norm_name, aliases, props, observations, sources, provisional, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "ent_2",
            "Node",
            "Beta Node",
            "beta node",
            json.dumps(["Beta"]),
            json.dumps({"category": "edge"}),
            json.dumps(["Observed"]),
            json.dumps(["doc_1"]),
            0,
            1761436800.0,
            1761436800.0,
        ),
    )
    conn.execute(
        """
        INSERT INTO relations (
            id, type, source_id, target_id, confidence, props, sources, provisional, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "rel_1",
            "CONNECTS_TO",
            "ent_1",
            "ent_2",
            0.95,
            json.dumps({"weight": 1}),
            json.dumps(["doc_1"]),
            0,
            1761436800.0,
        ),
    )
    conn.commit()
    conn.close()


def _build_settings(tmp_path: Path) -> Settings:
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    db_file = data_dir / "claire.db"
    _seed_db(db_file)
    return Settings(
        CLAIRE_DB_PATH=str(db_file),
        CLAIRE_DATA_DIR=str(data_dir),
        CLAIRE_VAULT_PATH=str(tmp_path / "vault"),
        CLAIRE_PROVIDER="mock",
        CLAIRE_ENVIRONMENT="development",
        CLAIRE_PUBLIC_URL="http://127.0.0.1:8765",
        CLAIRE_INJECT_TOKEN=OWNER_TOKEN,
        CLAIRE_READONLY_TOKEN=READONLY_TOKEN,
        CLAIRE_ANONYMOUS_READONLY=True,
    )


@pytest.mark.asyncio
async def test_create_graph_bundle_route_direct_handler_scope_enforcement(tmp_path: Path):
    """Direct invocation of create_graph_bundle_route enforces scope == 'owner'."""
    s = _build_settings(tmp_path)
    app = server.create_app(s)

    # Find the route handler
    create_handler = None
    for r in app.inner.router.routes:
        if getattr(r, "path", None) == "/graph/bundle" and "POST" in getattr(r, "methods", []):
            create_handler = r.endpoint
            break
    assert create_handler is not None

    # 1. Non-owner scope (readonly) raises 401
    mock_req_readonly = MagicMock(spec=Request)
    mock_req_readonly.state = MagicMock()
    mock_req_readonly.state.claire_auth_scope = "readonly"
    with pytest.raises(HTTPException) as exc:
        await create_handler(mock_req_readonly)
    assert exc.value.status_code == 401
    assert "owner authorization required" in exc.value.detail

    # 2. Non-owner scope (anonymous) raises 401
    mock_req_anon = MagicMock(spec=Request)
    mock_req_anon.state = MagicMock()
    mock_req_anon.state.claire_auth_scope = "anonymous"
    with pytest.raises(HTTPException) as exc:
        await create_handler(mock_req_anon)
    assert exc.value.status_code == 401
    assert "owner authorization required" in exc.value.detail

    # 3. None scope raises 401
    mock_req_none = MagicMock(spec=Request)
    mock_req_none.state = MagicMock()
    mock_req_none.state.claire_auth_scope = None
    with pytest.raises(HTTPException) as exc:
        await create_handler(mock_req_none)
    assert exc.value.status_code == 401


def test_api_graph_bundle_permissions_and_creation(tmp_path: Path):
    """HTTP Client tests verifying authorization, generation, and downloading."""
    s = _build_settings(tmp_path)
    app = server.create_app(s)

    with TestClient(app, base_url=s.public_url) as client:
        # 1. POST without token is rejected (401 from route or 404 from gate middleware)
        resp_unauth = client.post("/graph/bundle", json={"theme_id": 0})
        assert resp_unauth.status_code in (401, 404)

        # 2. POST with readonly token is rejected (401 from route or 404 from gate middleware)
        resp_ro = client.post(
            "/graph/bundle",
            headers=READONLY_HEADERS,
            json={"theme_id": 0},
        )
        assert resp_ro.status_code in (401, 404)

        # 3. POST with owner token succeeds (200 OK)
        resp_owner = client.post(
            "/graph/bundle",
            headers=OWNER_HEADERS,
            json={"theme_id": 0},
        )
        assert resp_owner.status_code == 200
        data = resp_owner.json()
        assert "bundle_id" in data
        assert "token" in data
        assert "download_url" in data
        assert data["entities_count"] == 2
        assert data["relations_count"] == 1
        assert data["documents_count"] == 1
        assert data["size_bytes"] > 0
        token = data["token"]

        # Verify sidecar registry is readable
        record = lookup_graph_bundle_by_token(s.data_dir, token)
        assert record is not None
        assert record["bundle_id"] == data["bundle_id"]

        # 4. GET with valid token downloads the .tar.zst archive
        resp_dl = client.get(f"/graph/bundle?token={token}")
        assert resp_dl.status_code == 200
        assert resp_dl.headers["content-type"] == "application/zstd"
        assert f'attachment; filename="{data["filename"]}"' in resp_dl.headers["content-disposition"]
        assert resp_dl.headers["cache-control"] == "no-store, private"
        assert len(resp_dl.content) == data["size_bytes"]

        # 5. GET with invalid token returns 404
        resp_invalid = client.get("/graph/bundle?token=gb3_invalidtoken000000000000000000")
        assert resp_invalid.status_code == 404

        # 6. GET without token returns 404
        resp_missing = client.get("/graph/bundle")
        assert resp_missing.status_code == 404


def test_api_graph_bundle_expired_token_returns_410(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Expired bundle token returns HTTP 410 Gone and cleans up the bundle."""
    s = _build_settings(tmp_path)
    app = server.create_app(s)

    with TestClient(app, base_url=s.public_url) as client:
        # Create bundle
        resp_create = client.post("/graph/bundle", headers=OWNER_HEADERS, json={"theme_id": 0})
        assert resp_create.status_code == 200
        token = resp_create.json()["token"]

        # Advance time beyond 24-hour TTL
        now = time.time()
        future_time = now + GRAPH_BUNDLE_TTL_SECONDS + 3600
        monkeypatch.setattr(time, "time", lambda: future_time)

        # GET should return 410 Gone
        resp_get = client.get(f"/graph/bundle?token={token}")
        assert resp_get.status_code == 410
        assert "expired and was purged" in resp_get.json()["error"]
