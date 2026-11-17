"""Credential scopes, route capabilities, step-up, and collections (SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md Sections 6.6 to 6.9, 7, 11.3; unit CT-2).

- Each route declares a capability and a minimum scope (Section 6.9 rule 3).
- The scope ``write`` replaces ``collaborator``: it ingests into the personal knowledge base and gets 403 for the decisions of the person.
- A bearer credential never passes a step-up route (Section 6.8).
- No IdP claim gives a right (Section 6.7).
- Old ``collaborator`` sessions end and old ``collaborator`` tokens only read (Section 6.6 rule 2).
- The collections ``/kb``, ``/@{u}/kb``, and ``/themes`` (Section 7).
"""

from __future__ import annotations

import dataclasses
import json
import sqlite3
import time
from pathlib import Path

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from starlette.applications import Starlette
from starlette.routing import Mount, Route
from starlette.testclient import TestClient

from tests.kb_paths import kb_list, step_up_request
from tests.test_oidc_core import _make_jwt, _rsa_to_jwk

from claire import kb_access
from claire.api import security
from claire.api.oidc import ExternalIdentity, IdpProviderMeta, OidcClient
from claire.api.server import create_app
from claire.config import Settings
from claire.store import accounts as accounts_store
from claire.store import db as dbm
from claire.store import kb_collab
from claire.store import themes as themes_store
from claire.store.knowledge_base import KnowledgeBaseManager
from claire.store.token_store import SCOPE_NOTICE_COLLABORATOR, TokenStore, get_token_store, reset_token_stores

OWNER_TOKEN = "owner-" + ("s" * 32)
OWNER = {"Authorization": f"Bearer {OWNER_TOKEN}"}
PUBLIC_URL = "http://127.0.0.1:8765"


@pytest.fixture(autouse=True)
def _fresh_token_stores():
    reset_token_stores()
    yield
    reset_token_stores()


def _settings(tmp_path: Path) -> Settings:
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    return Settings(
        CLAIRE_DB_PATH=str(tmp_path / "data" / "claire.db"),
        CLAIRE_VAULT_PATH=str(tmp_path / "vault"),
        CLAIRE_PROVIDER="mock",
        CLAIRE_ENVIRONMENT="development",
        CLAIRE_PUBLIC_URL=PUBLIC_URL,
        CLAIRE_INJECT_TOKEN=OWNER_TOKEN,
        CLAIRE_ANONYMOUS_READONLY=True,
        CLAIRE_MULTI_THEME=True,
    )


def _bearer(settings: Settings, scope: str, *, account_id: int = 1) -> dict[str, str]:
    token = get_token_store(data_dir=settings.data_dir).create_api_token(name=f"{scope}-{account_id}", scope=scope, account_id=account_id)["token"]
    return {"Authorization": f"Bearer {token}"}


# --- Section 6.9 rule 3: each route has a capability and a minimum scope ------------------------------------------

# (methods, path, minimum scope, capability) of each route that acts on a knowledge base. Each other route declares ``none``.
KB_ROUTES = [
    ("POST", "/document/pin", "owner", "curate"),
    ("POST", "/document/title", "owner", "curate"),
    ("DELETE", "/", "owner", "delete"),
    ("POST", "/dedup/merge", "owner", "maintain"),
    ("POST", "/dedup/scan", "owner", "maintain"),
    ("PUT", "/document/presentation/adoc", "owner", "maintain"),
    ("POST", "/document/presentation/compile", "owner", "maintain"),
    ("POST", "/document/presentation/compose", "owner", "maintain"),
    ("POST", "/document/presentation/generate", "owner", "maintain"),
    ("POST", "/entity/primary-label", "owner", "maintain"),
    ("POST", "/graph/bundle", "owner", "maintain"),
    ("POST", "/resolution/rollback", "owner", "maintain"),
    ("POST", "/document/regenerate", "write", "maintain"),
    ("POST", "/regenerate", "write", "maintain"),
    ("POST", "/regenerate-stream", "write", "maintain"),
    ("GET|HEAD", "/p/presentation", "none", "read"),
    ("POST", "/research", "owner", "read"),
    ("GET|HEAD", "/syndication/preferences", "owner", "read"),
    ("POST", "/synthesize", "owner", "read"),
    ("GET|HEAD", "/", "readonly", "read"),
    ("GET|HEAD", "/document", "readonly", "read"),
    ("GET|HEAD", "/document/export", "readonly", "read"),
    ("GET|HEAD", "/document/presentation", "readonly", "read"),
    ("GET|HEAD", "/document/presentation/adoc", "readonly", "read"),
    ("GET|HEAD", "/documents", "readonly", "read"),
    ("GET|HEAD", "/graph", "readonly", "read"),
    ("GET|HEAD", "/ingest/active", "readonly", "read"),
    ("GET|HEAD|POST", "/mcp", "readonly", "read"),
    ("GET|HEAD", "/node", "readonly", "read"),
    ("GET|HEAD", "/resolution/decisions", "readonly", "read"),
    ("POST", "/search", "readonly", "read"),
    ("GET|HEAD", "/stats", "readonly", "read"),
    ("GET|HEAD", "/syndication", "readonly", "read"),
    ("GET|HEAD", "/whoami", "readonly", "read"),
    ("GET|HEAD", "/document/raw", "write", "read"),
    ("POST", "/document/matrix/purge", "owner", "remove"),
    ("POST", "/document/purge", "owner", "remove"),
    ("POST", "/purge", "owner", "remove"),
    ("POST", "/document/hide", "owner", "review"),
    ("POST", "/document/seen", "owner", "review"),
    ("PATCH", "/", "owner", "settings"),
    ("DELETE|POST", "/membership", "owner", "join"),
    ("GET|HEAD", "/members", "readonly", "read"),
    ("PATCH", "/members/{account}", "owner", "people"),
    ("DELETE|PUT", "/api/admin/kb/{number}/moderators/{account}", "owner", "moderators"),
    ("POST", "/share", "readonly", "share"),
    ("POST", "/api/federation/timeline/{item_id}/ingest", "owner", "write"),
    ("POST", "/api/feeds/items/{item_id}/ingest", "owner", "write"),
    ("POST", "/api/federation/publish", "write", "write"),
    ("POST", "/api/federation/retract", "write", "write"),
    ("POST", "/api/syndication/dispatch", "write", "write"),
    ("POST", "/ingest", "write", "write"),
    ("POST", "/ingest-stream", "write", "write"),
    ("POST", "/reingest", "write", "write"),
    ("POST", "/reingest-stream", "write", "write"),
    ("POST", "/syndication/dispatch", "write", "write"),
]
STEP_UP_ROUTES = {("DELETE", "/"), ("PUT", "/api/admin/kb/{number}/moderators/{account}"), ("DELETE", "/api/admin/kb/{number}/moderators/{account}")}


def _mounted_keys(app) -> set[tuple[str, str]]:
    while not isinstance(app, Starlette):
        app = app.app
    keys: set[tuple[str, str]] = set()
    for route in app.routes:
        if isinstance(route, Route):
            keys.update((method, route.path) for method in route.methods or ())
        elif isinstance(route, Mount):
            keys.update((method, f"{route.path}/{{path:path}}") for method in ("GET", "HEAD"))
    return keys


def test_each_route_declares_a_capability_and_a_minimum_scope(tmp_path: Path) -> None:
    mounted = _mounted_keys(create_app(settings=_settings(tmp_path)))
    assert mounted, "no route is mounted"
    for key in sorted(mounted):
        rule = security.lookup_route_rule(*key) if "{" not in key[1] else security.ROUTE_POLICY.get(key)
        assert rule is not None, f"{key}: no route rule"
        assert rule.capability in kb_access.ROUTE_CAPABILITIES, key
        assert security.minimum_scope(rule) in ("none", "readonly", "write", "owner"), key

    declared = {
        (method, path): (security.minimum_scope(rule), rule.capability)
        for (method, path), rule in security.ROUTE_POLICY.items()
        if rule.capability != kb_access.NO_KB
    }
    expected = {(method, path): (scope, capability) for methods, path, scope, capability in KB_ROUTES for method in methods.split("|")}
    assert declared == expected
    assert {key for key, rule in security.ROUTE_POLICY.items() if rule.step_up} == STEP_UP_ROUTES


def test_route_review_of_the_old_collaborator_level() -> None:
    """Section 6.6 route review."""
    policy = security.ROUTE_POLICY
    assert not any(rule.access == "collaborator" for rule in policy.values())
    for path in ("/ingest", "/ingest-stream", "/reingest", "/reingest-stream"):
        assert (policy[("POST", path)].access, policy[("POST", path)].capability) == ("write", "write"), path
    for path in ("/regenerate", "/regenerate-stream", "/document/regenerate"):
        assert (policy[("POST", path)].access, policy[("POST", path)].capability) == ("write", "maintain"), path
    for key in (("POST", "/document/presentation/generate"), ("POST", "/document/presentation/compose"), ("POST", "/document/presentation/compile"), ("PUT", "/document/presentation/adoc")):
        assert (policy[key].access, policy[key].capability) == ("owner", "maintain"), key
    assert (policy[("GET", "/document/raw")].access, policy[("GET", "/document/raw")].capability) == ("write", "read")
    for path in ("/api/federation/publish", "/api/federation/retract", "/api/syndication/dispatch"):
        assert (policy[("POST", path)].access, policy[("POST", path)].capability) == ("write", "write"), path
    with pytest.raises(ValueError):
        security._namespaced("owner", "not-a-capability")


# --- Section 6.6: the scope ``write`` ------------------------------------------------------------------------------


def test_e2e_write_token_ingests_into_the_personal_knowledge_base_and_gets_403_for_the_decisions_of_the_person(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    with TestClient(create_app(settings=settings), base_url=PUBLIC_URL, follow_redirects=False) as client:
        made = client.post("/kb", json={"label": "Cities", "knowledge_type": "real"}, headers=OWNER)
        assert made.status_code == 201, made.text
        shared = made.json()["knowledge_base"]
        write = _bearer(settings, "write")
        readonly = _bearer(settings, "readonly")
        assert client.get("/whoami", headers=write).json()["scope"] == "write"

        # Ingest goes to the personal knowledge base of the account of the token.
        ingested = client.post("/ingest", json={"payload": "A note about the harbor of Busan."}, headers=write)
        assert ingested.status_code == 200, ingested.text
        document_id = ingested.json()["document_id"]
        conn = dbm.connect_existing(settings.db_file, readonly=True)
        try:
            row = conn.execute("SELECT d.kb_id, k.is_personal, k.account_id FROM documents d JOIN knowledge_bases k ON k.id = d.kb_id WHERE d.id=?", (document_id,)).fetchone()
        finally:
            conn.close()
        assert tuple(row) == (0, 1, 1)
        # It reads, and it reads the raw file route (404: the document has no raw file, not a refusal of the scope).
        assert client.get("/documents", headers=write).status_code == 200
        assert client.get(f"/document/raw?id={document_id}", headers=write).status_code == 404
        # A path that pins a shared knowledge base: 403 (CT4).
        pinned = client.post(f"/kb/{shared['number']}/ingest", json={"payload": "text"}, headers=write)
        assert (pinned.status_code, pinned.json()) == (403, {"error": "kb_ingest_personal_only"})

        # Review, curation, maintenance, settings, remove, and delete are decisions of the person: 403.
        refused = {"error": "scope_insufficient", "scope": "write", "required": "owner"}
        for method, path, body in (
            ("POST", f"/kb/{shared['number']}/document/hide", {"id": document_id, "hidden": True}),
            ("POST", f"/kb/{shared['number']}/document/pin", {"id": document_id, "pinned": True}),
            ("POST", f"/kb/{shared['number']}/dedup/merge", {"keep": "a", "merge": "b"}),
            ("POST", f"/kb/{shared['number']}/document/purge", {"id": document_id}),
            ("PATCH", f"/kb/{shared['number']}/", {"label": "x"}),
            ("DELETE", f"/kb/{shared['number']}/", None),
            ("POST", f"/kb/{shared['number']}/membership", None),
            ("PATCH", f"/kb/{shared['number']}/members/1", {"action": "ban"}),
            ("POST", "/document/hide", {"id": document_id, "hidden": True}),
            ("POST", "/document/presentation/generate", {"id": document_id}),
        ):
            response = client.request(method, path, json=body, headers=write)
            assert (response.status_code, response.json()) == (403, refused), (method, path)
        # In a shared knowledge base the scope ``write`` has no ``maintain``, also for a moderator (regeneration by hand, Section 13.1 rule 5).
        regenerate = client.post(f"/kb/{shared['number']}/regenerate", json={"doc_id": document_id}, headers=write)
        assert (regenerate.status_code, regenerate.json()) == (403, {"error": "kb_capability_required"})
        # Account and credential routes stay hidden for a smaller scope (404), as before.
        assert client.get("/tokens", headers=write).status_code == 404

        # ``readonly`` reads and gets 403 for each write.
        assert client.get("/documents", headers=readonly).status_code == 200
        assert client.post("/ingest", json={"payload": "x"}, headers=readonly).json() == {"error": "scope_insufficient", "scope": "readonly", "required": "write"}


def test_write_scope_contributes_and_has_no_role_or_review_capability() -> None:
    """Section 6.6 table: in a shared knowledge base ``write`` has ``read`` and the contributions only."""
    for membership in (("member", "active"), ("collaborator", "active"), ("moderator", "active")):
        actor = kb_access.Actor(account_id=5, scope="write", membership=membership)
        assert kb_access.shared_capabilities(actor) == {"read", "contribute_direct", "contribute_theme", "share"}
    admin = kb_access.Actor(account_id=1, scope="write", is_admin=True)
    for capability in ("join", "fork", "review", "curate", "maintain", "people", "settings", "remove", "moderators", "delete"):
        assert kb_access.decide(is_personal=False, owner_account_id=None, is_public=True, capability=capability, actor=admin) == "forbidden"
    # A non-member with ``write`` contributes to a theme, not directly (Section 6.2).
    outsider = kb_access.Actor(account_id=5, scope="write")
    assert kb_access.shared_capabilities(outsider) == {"read", "contribute_theme", "share"}


def test_token_api_takes_the_three_scopes_only(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    with TestClient(create_app(settings=settings), base_url=PUBLIC_URL) as client:
        for scope in ("owner", "write", "readonly"):
            assert client.post("/tokens", json={"name": scope, "scope": scope}, headers=OWNER).status_code == 201
        for scope in ("collaborator", "collab", "admin"):
            assert client.post("/tokens", json={"name": scope, "scope": scope}, headers=OWNER).status_code == 400, scope


# --- Section 6.8: step-up ------------------------------------------------------------------------------------------


def test_bearer_credential_never_passes_a_step_up_route(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    with TestClient(create_app(settings=settings), base_url=PUBLIC_URL, follow_redirects=False) as client:
        number = client.post("/kb", json={"label": "Cities", "knowledge_type": "real"}, headers=OWNER).json()["knowledge_base"]["number"]
        # The owner token of the admin has each capability, but it is a bearer credential: 403.
        for headers in (OWNER, _bearer(settings, "owner")):
            refused = client.request("DELETE", f"/kb/{number}/?apply=true", headers=headers)
            assert (refused.status_code, refused.json()) == (403, {"error": "step_up_requires_session"})
        # A session without a recent sign-in: 401 with the place to sign in again.
        conn = dbm.connect_existing(settings.db_file)
        try:
            old = accounts_store.create_account_session(conn, account_id=1, scope="owner", now=time.time() - 3600)
        finally:
            conn.close()
        client.cookies.clear()
        stale = client.request("DELETE", f"/kb/{number}/?apply=true", headers={"Cookie": f"claire_session={old}", "Origin": PUBLIC_URL})
        client.cookies.clear()
        assert (stale.status_code, stale.json()) == (401, {"error": "reauth_required", "reauth": "/api/auth/reauth"})
        assert [kb["number"] for kb in kb_list(client, headers=OWNER) if not kb["is_personal"]] == [number]
        # A session with a recent sign-in passes.
        # PRINCIPLES D3: without ``apply=true`` the answer is the plan only.
        plan = step_up_request(client, settings, "DELETE", f"/kb/{number}/")
        assert (plan.status_code, plan.json()["applied"], plan.json()["would_delete"]["members"]) == (200, False, 1)
        assert [kb["number"] for kb in kb_list(client, headers=OWNER) if not kb["is_personal"]] == [number]
        assert step_up_request(client, settings, "DELETE", f"/kb/{number}/?apply=true").json()["applied"] is True
        assert [kb for kb in kb_list(client, headers=OWNER) if not kb["is_personal"]] == []


# --- Section 6.7: no IdP claim gives a right -----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_idp_group_claim_changes_no_right() -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = _rsa_to_jwk(key.public_key(), kid="k1")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/.well-known/openid-configuration":
            return httpx.Response(200, json={"issuer": "https://idp.example.com", "authorization_endpoint": "https://idp.example.com/auth", "token_endpoint": "https://idp.example.com/token", "jwks_uri": "https://idp.example.com/jwks"})
        if request.url.path == "/jwks":
            return httpx.Response(200, json={"keys": [jwk]})
        return httpx.Response(404)

    provider = IdpProviderMeta(id="idp", type="oidc", display_name="IdP", client_id="app", issuer="https://idp.example.com")
    client = OidcClient(provider=provider, client_secret="secret", http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    now = time.time()
    claims = {
        "iss": "https://idp.example.com", "sub": "user-1", "aud": "app", "exp": now + 300, "iat": now - 10, "nonce": "n1",
        "email": "user@example.com", "email_verified": True, "name": "User One",
        # Claims that an IdP administrator controls. None of them is a right in Claire.
        "groups": ["claire-admins", "kb-moderators"], "roles": ["admin", "moderator"], "role": "admin", "scope": "owner", "is_admin": True, "tenant": "ops",
    }
    identity = await client.verify_id_token(_make_jwt(claims, key, alg="RS256", kid="k1"), expected_nonce="n1")
    # The identity has only the subject and the profile: there is no field that could carry a group, a role, or a scope into Claire.
    assert {f.name for f in dataclasses.fields(ExternalIdentity)} == {"iss", "sub", "email", "email_verified", "display_name"}
    assert dataclasses.asdict(identity) == {"iss": "https://idp.example.com", "sub": "user-1", "email": "user@example.com", "email_verified": True, "display_name": "User One"}


# --- Sections 6.6 rule 2 and 11.3: migration of the credential stores -----------------------------------------------


def test_collaborator_sessions_end_and_the_collaborator_column_goes(tmp_path: Path) -> None:
    db_file = tmp_path / "claire.db"
    conn = dbm.connect(db_file)
    try:
        dbm.init_db(conn)
        # The state before this version: sessions from ``/webco`` and the column for the collaborator access.
        conn.execute("ALTER TABLE knowledge_bases ADD COLUMN is_collaborator_accessible INTEGER DEFAULT 1")
        now = time.time()
        for scope in ("owner", "collaborator", "readonly"):
            conn.execute("INSERT INTO auth_sessions (nonce, session_token, approved, created_at, expires_at, scope, account_id) VALUES (?, ?, 1, ?, ?, ?, 1)", (f"n-{scope}", f"{scope[0]}" * 43, now, now + 3600, scope))
        conn.commit()
        dbm.init_db(conn)
        conn.commit()
        assert [r[0] for r in conn.execute("SELECT scope FROM auth_sessions ORDER BY scope")] == ["owner", "readonly"]
        assert "is_collaborator_accessible" not in {r[1] for r in conn.execute("PRAGMA table_info(knowledge_bases)")}
        # A second start changes nothing.
        dbm.init_db(conn)
        assert conn.execute("SELECT COUNT(*) FROM auth_sessions").fetchone()[0] == 2
        # No code makes a session with the old scope.
        with pytest.raises(ValueError):
            accounts_store.create_account_session(conn, account_id=1, scope="collaborator")
        assert accounts_store.validate_session_binding(conn, "c" * 43) is None
    finally:
        conn.close()


def test_collaborator_tokens_become_readonly_and_the_notice_shows_once(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    auth_dir = Path(settings.data_dir) / "auth"
    auth_dir.mkdir(parents=True, exist_ok=True)
    old_token = "cb_" + "c" * 40
    (auth_dir / "api_tokens.json").write_text(json.dumps({"version": 1, "tokens": [
        {"id": 1, "token": old_token, "name": "team agent", "scope": "collaborator", "account_id": 1, "created_at": 1.0},
        {"id": 2, "token": "cb_" + "r" * 40, "name": "reader", "scope": "readonly", "account_id": 1, "created_at": 1.0},
    ]}), encoding="utf-8")

    store = TokenStore(data_dir=settings.data_dir)
    assert store.validate_api_token(old_token)["scope"] == "readonly"
    # The file has no ``collaborator`` scope after the first load.
    on_disk = json.loads((auth_dir / "api_tokens.json").read_text(encoding="utf-8"))["tokens"]
    assert [(t["id"], t["scope"], t.get("scope_notice")) for t in on_disk] == [(1, "readonly", SCOPE_NOTICE_COLLABORATOR), (2, "readonly", None)]
    with pytest.raises(ValueError):
        store.create_api_token(name="x", scope="collaborator")

    with TestClient(create_app(settings=settings), base_url=PUBLIC_URL) as client:
        old = {"Authorization": f"Bearer {old_token}"}
        # The old token only reads.
        assert client.get("/whoami", headers=old).json()["scope"] == "readonly"
        assert client.get("/documents", headers=old).status_code == 200
        assert client.post("/ingest", json={"payload": "x"}, headers=old).status_code == 403
        # The console lists the changed token once.
        first = client.get("/tokens", headers=OWNER).json()
        assert first["scope_notices"] == [{"id": 1, "name": "team agent", "notice": SCOPE_NOTICE_COLLABORATOR}]
        assert all("scope_notice" not in t for t in first["tokens"])
        assert client.get("/tokens", headers=OWNER).json()["scope_notices"] == []


def test_environment_collaborator_token_is_not_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_token = "collab-" + ("c" * 32)
    monkeypatch.setenv("CLAIRE_COLLABORATOR_TOKEN", env_token)
    settings = _settings(tmp_path)
    assert not hasattr(settings, "collaborator_token")
    with TestClient(create_app(settings=settings), base_url=PUBLIC_URL) as client:
        headers = {"Authorization": f"Bearer {env_token}"}
        assert client.get("/whoami", headers=headers).status_code == 404
        assert client.post("/ingest", json={"payload": "x"}, headers=headers).status_code == 404
    assert get_token_store(data_dir=settings.data_dir).validate_api_token(env_token) is None


# --- Section 7: collections and old links ---------------------------------------------------------------------------


def test_kb_and_themes_collections_in_single_mode(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    with TestClient(create_app(settings=settings), base_url=PUBLIC_URL, follow_redirects=False) as client:
        real = client.post("/kb", json={"label": "Cities", "knowledge_type": "real"}, headers=OWNER).json()["knowledge_base"]
        fiction = client.post("/kb", json={"label": "Middle-earth", "knowledge_type": "fictional"}, headers=OWNER).json()["knowledge_base"]

        # /kb: the shared knowledge bases, newest first, for each reader.
        anonymous = client.get("/kb").json()
        assert (anonymous["type"], anonymous["totalItems"], anonymous["personal_kb"]) == ("OrderedCollection", 2, None)
        assert [kb["number"] for kb in anonymous["knowledge_bases"]] == [fiction["number"], real["number"]]
        assert all("role" not in kb and "reembed_required" not in kb and "legacy_hosts" not in kb for kb in anonymous["knowledge_bases"])
        assert anonymous["knowledge_bases"][0]["url"] == f"{PUBLIC_URL}/kb/{fiction['number']}/"
        # The founder sees its role and the settings view; ``personal_kb`` is the personal knowledge base of the requester.
        owner = client.get("/kb", headers=OWNER).json()
        assert {kb["role"] for kb in owner["knowledge_bases"]} == {"moderator"} and all("reembed_required" in kb for kb in owner["knowledge_bases"])
        assert (owner["personal_kb"]["id"], owner["personal_kb"]["is_personal"]) == (0, True)

        # ?type selects one knowledge type; ?joined=true selects the memberships of the requester.
        assert [kb["number"] for kb in client.get("/kb?type=real").json()["knowledge_bases"]] == [real["number"]]
        assert [kb["number"] for kb in client.get("/kb?type=fictional").json()["knowledge_bases"]] == [fiction["number"]]
        assert client.get("/kb?joined=true").json()["knowledge_bases"] == []
        assert len(client.get("/kb?joined=true", headers=OWNER).json()["knowledge_bases"]) == 2
        for bad in ("/kb?type=other", "/kb?joined=yes", "/themes?type=other"):
            assert client.get(bad).status_code == 400, bad
        # Pages of the collection.
        page = client.get("/kb?page=true&limit=1").json()
        assert (page["type"], [kb["number"] for kb in page["orderedItems"]]) == ("OrderedCollectionPage", [fiction["number"]])

        # /themes: the collection of themes (the themes of Section 8, not knowledge bases).
        assert client.get("/themes").json()["themes"] == []
        conn = dbm.connect(settings.db_file)
        try:
            theme = themes_store.create_theme(conn, label="Geography", knowledge_type="real")
            themes_store.add_theme_member(conn, theme_id=theme["id"], kb_id=real["id"], source="computed")
            conn.commit()
        finally:
            conn.close()
        listed = client.get("/themes").json()
        assert listed["totalItems"] == 1
        item = listed["themes"][0]
        assert (item["label"], item["knowledge_type"], item["knowledge_bases"], item["url"]) == ("Geography", "real", 1, f"{PUBLIC_URL}/themes/{item['number']}")
        assert client.get("/themes?type=fictional").json()["themes"] == []

        # Old links: /themes/{number}/… with the number of a shared knowledge base gets 308 to /kb/{number}/….
        old = client.get(f"/themes/{real['number']}/documents?x=1")
        assert (old.status_code, old.headers["location"]) == (308, f"/kb/{real['number']}/documents?x=1")
        # A pinned knowledge base has no collection.
        assert client.get(f"/kb/{real['number']}/kb").status_code == 404


def _network_world(tmp_path: Path) -> tuple[Settings, dict[str, int]]:
    settings = _settings(tmp_path)
    conn = dbm.connect(settings.db_file)
    try:
        dbm.init_db(conn)
        now = time.time()
        for account_id, username, role, public in ((1, "admin", "admin", 1), (2, "alice", "member", 1), (3, "bob", "member", 1), (4, "carol", "member", 0)):
            conn.execute(
                "INSERT INTO accounts (id, public_id, username, role, status, display_name, profile_public, created_at, updated_at) VALUES (?, ?, ?, ?, 'active', ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET username=excluded.username, role=excluded.role, status=excluded.status, profile_public=excluded.profile_public",
                (account_id, (1 << 57) + account_id, username, role, username, public, now, now),
            )
        accounts_store.AccountStore(conn).set_setting("mode", "network", updated_by=1)
        conn.commit()
    finally:
        conn.close()
    manager = KnowledgeBaseManager(settings)
    for account_id, label in ((2, "Alice home"), (3, "Bob home")):
        manager.define_kb(label, account_id=account_id, is_personal=True, is_public=account_id == 2)
    ids_ = {
        "cities": manager.define_kb("Cities", account_id=2, knowledge_type="real").id,
        "rivers": manager.define_kb("Rivers", account_id=3, knowledge_type="real").id,
    }
    conn = dbm.connect(settings.db_file)
    try:
        kb_collab.add_membership(conn, kb_id=ids_["rivers"], account_id=2, role="member", state="active")
        kb_collab.add_membership(conn, kb_id=ids_["cities"], account_id=3, role="member", state="pending")
        conn.commit()
    finally:
        conn.close()
    return settings, ids_


def test_account_kb_collection_and_old_account_themes_link_in_network_mode(tmp_path: Path) -> None:
    settings, _ids = _network_world(tmp_path)
    with TestClient(create_app(settings=settings), base_url=PUBLIC_URL, follow_redirects=False) as client:
        alice = _bearer(settings, "readonly", account_id=2)
        bob = _bearer(settings, "owner", account_id=3)
        # /@{u}/kb: the shared knowledge bases where ``u`` is an active member, with the role of ``u`` (CT9).
        for headers in ({}, alice, bob):
            data = client.get("/@alice/kb", headers={**headers, "Accept": "application/json"}).json()
            assert sorted((kb["label"], kb["role"]) for kb in data["knowledge_bases"]) == [("Cities", "moderator"), ("Rivers", "member")]
            assert data["id"] == f"{PUBLIC_URL}/@alice/kb"
            # The personal knowledge base of alice is public: each reader gets it.
            assert data["personal_kb"]["label"] == "Alice home"
        # A pending membership is not in the collection. The private personal knowledge base of bob only goes to bob.
        visitor = client.get("/@bob/kb", headers={**alice, "Accept": "application/json"}).json()
        assert ([kb["label"] for kb in visitor["knowledge_bases"]], visitor["personal_kb"]) == (["Rivers"], None)
        own = client.get("/@bob/kb", headers={**bob, "Accept": "application/json"}).json()
        assert own["personal_kb"]["label"] == "Bob home"
        # /kb?joined=true at the root: the memberships of the requester.
        assert [kb["label"] for kb in client.get("/kb?joined=true", headers=bob).json()["knowledge_bases"]] == ["Rivers"]
        assert len(client.get("/kb", headers=bob).json()["knowledge_bases"]) == 2
        # N1: a profile that is not public hides its collection from each other account.
        assert client.get("/@carol/kb", headers=bob).status_code == 404
        # Old link /@{u}/themes: 308 to /@{u}/kb, with the query.
        old = client.get("/@alice/themes?page=true", headers=bob)
        assert (old.status_code, old.headers["location"]) == (308, "/@alice/kb?page=true")
        # /themes at the root is the collection of themes, also for an anonymous reader.
        assert client.get("/themes").json()["type"] == "OrderedCollection"
