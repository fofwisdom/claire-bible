"""Empirical Challenge Test Suite for Requirement R5 (Syndication Prototype Sealing and Cleanup).

Executed by Challenger 2 for Milestone M2.
Tests:
1. Unauthenticated access enforcement on /prototype/syndication
2. Authenticated access on /prototype/syndication (HTML bundle validation)
3. Prototype seed isolation on live /syndication
4. Prefix/namespaced isolation (N5 system route contract)
5. Method constraint enforcement (POST/PUT/DELETE on GET/HEAD routes)
6. Account status verification (suspended/disabled accounts blocked)
7. Dangling prototype route discovery
"""

from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from claire.api import security, server
from claire.config import Settings
from claire.store import accounts as accounts_store
from claire.store import db as dbm
from claire.store.token_store import get_token_store, reset_token_stores

OWNER_TOKEN = "own-" + ("o" * 32)
COLLAB_TOKEN = "col-" + ("c" * 32)
READONLY_TOKEN = "rea-" + ("r" * 32)
MEMBER_TOKEN = "mem-" + ("m" * 32)
SUSPENDED_TOKEN = "sus-" + ("s" * 32)


@pytest.fixture
def test_env(tmp_path: Path):
    reset_token_stores()
    for k in list(os.environ):
        if k.startswith("CLAIRE_"):
            os.environ.pop(k, None)

    data_dir = tmp_path / "data"
    vault_dir = tmp_path / "vault"
    data_dir.mkdir(parents=True, exist_ok=True)
    vault_dir.mkdir(parents=True, exist_ok=True)

    db_file = data_dir / "claire.db"
    conn = dbm.connect(db_file)
    dbm.init_db(conn)

    now = time.time()
    # Account 1: Admin / Owner is created by init_db; update display_name / public_id
    conn.execute(
        "UPDATE accounts SET username='admin', role='admin', status='active', display_name='Admin', profile_public=1, updated_at=? WHERE id=1",
        (now,),
    )
    # Account 2: Active Member
    conn.execute(
        "INSERT INTO accounts (id, public_id, username, role, status, display_name, profile_public, created_at, updated_at) "
        "VALUES (2, 1002, 'alice', 'member', 'active', 'Alice', 1, ?, ?)",
        (now, now),
    )
    # Account 3: Suspended Member
    conn.execute(
        "INSERT INTO accounts (id, public_id, username, role, status, display_name, profile_public, created_at, updated_at) "
        "VALUES (3, 1003, 'mallory', 'member', 'suspended', 'Mallory', 0, ?, ?)",
        (now, now),
    )
    conn.commit()

    token_store = get_token_store(data_dir=data_dir)
    token_store.ensure_default_owner_token(fallback_token=OWNER_TOKEN)
    token_store.create_token(name="collab", account_id=1, scope="collaborator", token=COLLAB_TOKEN)
    token_store.create_token(name="read", account_id=1, scope="readonly", token=READONLY_TOKEN)
    token_store.create_token(name="alice-token", account_id=2, scope="owner", token=MEMBER_TOKEN)
    token_store.create_token(name="suspended-token", account_id=3, scope="owner", token=SUSPENDED_TOKEN)

    conn.close()

    settings = Settings(
        CLAIRE_DATA_DIR=str(data_dir),
        CLAIRE_DB_PATH=str(db_file),
        CLAIRE_VAULT_PATH=str(vault_dir),
        CLAIRE_PROVIDER="mock",
        CLAIRE_ENVIRONMENT="development",
        CLAIRE_PUBLIC_URL="http://127.0.0.1:8765",
        CLAIRE_INJECT_TOKEN=OWNER_TOKEN,
        CLAIRE_ANONYMOUS_READONLY=False,
        CLAIRE_MULTI_THEME=True,
    )

    app = server.create_app(settings=settings)
    yield settings, app, db_file
    reset_token_stores()


def test_prototype_unauthenticated_access_is_blocked(test_env):
    """Challenge: Verify unauthenticated requests never access /prototype/syndication."""
    _, app, _ = test_env
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        # GET without auth
        res_get = client.get("/prototype/syndication")
        assert res_get.status_code in {401, 403, 404}, f"Unexpected GET status: {res_get.status_code}"
        assert "Claire Bible — Syndication Prototype" not in res_get.text

        # HEAD without auth
        res_head = client.head("/prototype/syndication")
        assert res_head.status_code in {401, 403, 404}, f"Unexpected HEAD status: {res_head.status_code}"
        assert len(res_head.content) == 0

        # Invalid token
        res_bad_tok = client.get(
            "/prototype/syndication",
            headers={"Authorization": "Bearer completely-invalid-token-value-1234567890"},
        )
        assert res_bad_tok.status_code in {401, 403, 404}
        assert "Claire Bible — Syndication Prototype" not in res_bad_tok.text

        # Empty bearer
        res_empty = client.get("/prototype/syndication", headers={"Authorization": "Bearer "})
        assert res_empty.status_code in {401, 403, 404}

        # Malformed scheme
        res_basic = client.get("/prototype/syndication", headers={"Authorization": "Basic dXNlcjpwYXNz"})
        assert res_basic.status_code in {401, 403, 404}


def test_prototype_authenticated_access_contract(test_env):
    """Challenge: Verify authenticated requests return the expected prototype bundle."""
    _, app, _ = test_env
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        headers_owner = {"Authorization": f"Bearer {OWNER_TOKEN}"}
        res_proto = client.get("/prototype/syndication", headers=headers_owner)
        assert res_proto.status_code == 200
        assert "text/html" in res_proto.headers.get("content-type", "")

        # Verify prototype-specific identity and navigation
        assert "<title>Claire Bible — Syndication Prototype (/prototype/syndication)</title>" in res_proto.text
        assert 'data-prototype-view="syndication"' in res_proto.text
        assert 'href="/syndication"' in res_proto.text
        assert "📡 실제 Syndication" in res_proto.text

        # Verify HEAD works with auth
        res_head = client.head("/prototype/syndication", headers=headers_owner)
        assert res_head.status_code == 200
        assert len(res_head.content) == 0

        # Collaborator access (read permission is sufficient for _system('read'))
        headers_collab = {"Authorization": f"Bearer {COLLAB_TOKEN}"}
        res_collab = client.get("/prototype/syndication", headers=headers_collab)
        assert res_collab.status_code == 200
        assert 'data-auth-scope="collaborator"' in res_collab.text

        # Reader access
        headers_reader = {"Authorization": f"Bearer {READONLY_TOKEN}"}
        res_reader = client.get("/prototype/syndication", headers=headers_reader)
        assert res_reader.status_code == 200
        assert 'data-auth-scope="readonly"' in res_reader.text


def test_prototype_rejected_for_suspended_account(test_env):
    """Challenge: Verify suspended account cannot access /prototype/syndication."""
    _, app, _ = test_env
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        headers_suspended = {"Authorization": f"Bearer {SUSPENDED_TOKEN}"}
        res = client.get("/prototype/syndication", headers=headers_suspended)
        # Suspended account tokens must be rejected
        assert res.status_code in {401, 403, 404}, f"Suspended account allowed: {res.status_code}"


def test_live_syndication_strictly_isolates_prototype_artifacts(test_env):
    """Challenge: Verify live /syndication NEVER leaks prototype mock/seed data."""
    _, app, _ = test_env
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        headers_owner = {"Authorization": f"Bearer {OWNER_TOKEN}"}
        res_live = client.get("/syndication", headers=headers_owner)
        assert res_live.status_code == 200

        text = res_live.text

        # 1. Prototype mock seed globals must NOT exist in live syndication
        assert "CLAIRE_SYND_SEEDS" not in text
        assert "CLAIRE_SYND_CLIENT_FACTORY" not in text
        assert "CLAIRE_SYND_PEERS_FACTORY" not in text

        # 2. Prototype simulated federation endpoints must NOT exist in live syndication
        assert "/api/peers/publications" not in text
        assert "data-prototype-view" not in text

        # 3. Prototype client-mock buttons and inputs must NOT exist in live syndication
        assert 'id="btn-connect"' not in text
        assert 'id="btn-compose"' not in text
        assert 'id="client-search"' not in text
        assert '<input' in text
        assert '<input id="client-search"' not in text
        assert '<button type="button" id="btn-connect"' not in text
        assert '<button type="button" id="btn-compose"' not in text

        # 4. Canonical live endpoints and components MUST exist
        assert "/api/syndication/dispatch" in text
        assert "/api/syndication/dispatches" in text
        assert 'href="/prototype/syndication"' in text
        assert '<title>Claire Bible — Syndication (/syndication)</title>' in text


def test_system_surface_namespaced_isolation_n5(test_env):
    """Challenge: Verify N5 rule that system surface routes (/prototype/syndication, /syndication) return 404 under namespace prefixes."""
    _, app, _ = test_env
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        headers_owner = {"Authorization": f"Bearer {OWNER_TOKEN}"}
        headers_alice = {"Authorization": f"Bearer {MEMBER_TOKEN}"}

        # /@alice/prototype/syndication must be 404 (N5 fail-closed for system surface)
        res1 = client.get("/@alice/prototype/syndication", headers=headers_alice)
        assert res1.status_code == 404, f"Expected 404 for namespaced prototype, got {res1.status_code}"

        res2 = client.get("/@alice/prototype/syndication", headers=headers_owner)
        assert res2.status_code == 404, f"Expected 404 for admin accessing namespaced prototype, got {res2.status_code}"

        res3 = client.get("/@admin/prototype/syndication", headers=headers_owner)
        assert res3.status_code == 404, f"Expected 404 for admin prefix prototype, got {res3.status_code}"

        # /@alice/syndication is also a system surface route, so it must return 404
        res4 = client.get("/@alice/syndication", headers=headers_alice)
        assert res4.status_code == 404, f"Expected 404 for namespaced syndication root, got {res4.status_code}"


def test_method_not_allowed_on_prototype_and_syndication(test_env):
    """Challenge: Verify POST, PUT, DELETE, PATCH are rejected on /prototype/syndication and /syndication."""
    _, app, _ = test_env
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        headers = {"Authorization": f"Bearer {OWNER_TOKEN}"}

        for path in ("/prototype/syndication", "/syndication"):
            # POST
            res_post = client.post(path, headers=headers, json={"action": "test"})
            assert res_post.status_code in {404, 405}, f"POST {path} returned {res_post.status_code}"

            # PUT
            res_put = client.put(path, headers=headers, content=b"data")
            assert res_put.status_code in {404, 405}, f"PUT {path} returned {res_put.status_code}"

            # DELETE
            res_del = client.delete(path, headers=headers)
            assert res_del.status_code in {404, 405}, f"DELETE {path} returned {res_del.status_code}"

            # PATCH
            res_patch = client.patch(path, headers=headers, json={"prop": "val"})
            assert res_patch.status_code in {404, 405}, f"PATCH {path} returned {res_patch.status_code}"


def test_no_unprotected_prototype_routes_in_route_table(test_env):
    """Challenge: Discover all routes containing 'prototype' and ensure all are explicitly protected in ROUTE_POLICY."""
    from starlette.applications import Starlette

    _, app, _ = test_env
    raw_app = app
    while not isinstance(raw_app, Starlette):
        raw_app = raw_app.app

    # Check all mounted routes
    prototype_routes = []
    for route in raw_app.routes:
        path = getattr(route, "path", None)
        if path and "prototype" in path:
            prototype_routes.append((route.methods, path))

    # Expect exactly GET / HEAD for /prototype/syndication
    assert len(prototype_routes) == 1, f"Found unexpected prototype routes: {prototype_routes}"
    methods, path = prototype_routes[0]
    assert path == "/prototype/syndication"
    assert methods == {"GET", "HEAD"}

    # Verify security policy lookup for all standard HTTP methods
    rule_get = security.lookup_route_rule("GET", "/prototype/syndication")
    assert rule_get is not None
    assert rule_get.surface == "system"
    assert rule_get.access == "read"

    rule_head = security.lookup_route_rule("HEAD", "/prototype/syndication")
    assert rule_head is not None
    assert rule_head.surface == "system"
    assert rule_head.access == "read"

    # POST/PUT/DELETE should have no matching rule (fail-closed)
    for m in ("POST", "PUT", "DELETE", "PATCH", "OPTIONS"):
        assert security.lookup_route_rule(m, "/prototype/syndication") is None


def test_prototype_and_syndication_query_fuzzing(test_env):
    """Challenge: Verify that malicious or invalid query parameters do not crash or inject into prototype or syndication bundles."""
    _, app, _ = test_env
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        headers = {"Authorization": f"Bearer {OWNER_TOKEN}"}
        fuzz_params = [
            {"theme": "9999999"},
            {"theme": "-1"},
            {"theme": "<script>alert('xss')</script>"},
            {"theme": "../../../etc/passwd"},
            {"tab": "unknown_tab_name"},
            {"layout": "'\";><img src=x onerror=alert(1)>"},
        ]
        for params in fuzz_params:
            for endpoint in ("/prototype/syndication", "/syndication"):
                res = client.get(endpoint, headers=headers, params=params)
                # Must either succeed safely (200) with escaped content, or return 404/400.
                # Must never return 500 internal server error.
                assert res.status_code in {200, 400, 404}, f"Endpoint {endpoint} with {params} failed with {res.status_code}"
                if res.status_code == 200:
                    assert "<script>alert('xss')</script>" not in res.text
                    assert "<img src=x onerror=alert(1)>" not in res.text


def test_anonymous_readonly_mode_behavior(tmp_path: Path):
    """Challenge: Verify behavior when CLAIRE_ANONYMOUS_READONLY is True."""
    data_dir = tmp_path / "data_anon"
    vault_dir = tmp_path / "vault_anon"
    data_dir.mkdir(parents=True, exist_ok=True)
    vault_dir.mkdir(parents=True, exist_ok=True)

    db_file = data_dir / "claire.db"
    conn = dbm.connect(db_file)
    dbm.init_db(conn)
    conn.close()

    token_store = get_token_store(data_dir=data_dir)
    token_store.ensure_default_owner_token(fallback_token=OWNER_TOKEN)

    settings = Settings(
        CLAIRE_DATA_DIR=str(data_dir),
        CLAIRE_DB_PATH=str(db_file),
        CLAIRE_VAULT_PATH=str(vault_dir),
        CLAIRE_PROVIDER="mock",
        CLAIRE_ENVIRONMENT="development",
        CLAIRE_PUBLIC_URL="http://127.0.0.1:8765",
        CLAIRE_INJECT_TOKEN=OWNER_TOKEN,
        CLAIRE_ANONYMOUS_READONLY=True,
        CLAIRE_MULTI_THEME=False,
    )

    app = server.create_app(settings=settings)
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        # Without credentials:
        # In anonymous_readonly mode, /prototype/syndication redirects or serves readonly view
        res = client.get("/prototype/syndication", follow_redirects=False)
        assert res.status_code in {200, 302, 401, 404}
        if res.status_code == 302:
            assert res.headers["location"] in {"/", "http://127.0.0.1:8765/"}
        elif res.status_code == 200:
            # If served, it must be anonymous / readonly scope
            assert 'data-auth-scope="anonymous"' in res.text or 'data-auth-scope="readonly"' in res.text

