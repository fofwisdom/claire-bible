"""Unit tests for `API-4a` multi-document OpenAPI 3.1 specs and role-expanded Scalar reference renderer."""

from __future__ import annotations

import html as html_lib
import json
from pathlib import Path
import re
import subprocess
from types import SimpleNamespace
from typing import Any

import pytest
from starlette.testclient import TestClient

from claire.api import security, server
from claire.api.security import ROUTE_POLICY
from claire.api.v1 import (
    V1_ROUTE_POLICY,
    build_account_openapi_spec,
    build_admin_openapi_spec,
    build_scalar_sources,
    build_v1_openapi_json,
    build_v1_openapi_spec,
    build_v1_openapi_yaml,
    render_reference_html,
)
from claire.core.config import Settings
from claire.ingest.service import IngestService
from claire.store import accounts as accounts_store, db as dbm
from claire.store.knowledge_base import KnowledgeBaseManager
from tests.auth_helpers import ensure_test_account, issue_oauth_token, issue_session_token

_PATH_PARAM_RE = re.compile(r"\{([A-Za-z0-9_]+)\}")


def _assert_valid_openapi_3_1_structure(spec: dict[str, Any], *, policy_lookup: dict[tuple[str, str], Any]) -> None:
    assert spec["openapi"] == "3.1.0"
    assert spec["info"]["title"]
    assert spec["info"]["version"]
    assert spec["paths"]
    schemas = spec["components"]["schemas"]
    assert "ErrorEnvelope" in schemas

    untagged = 0
    for path, path_item in spec["paths"].items():
        declared_path_params = set(_PATH_PARAM_RE.findall(path))
        for method, op in path_item.items():
            if not op.get("tags"):
                untagged += 1
            op_params = op.get("parameters", [])
            assert all(param["name"] != "theme" for param in op_params)
            op_path_param_names = {param["name"] for param in op_params if param.get("in") == "path"}
            assert declared_path_params.issubset(op_path_param_names), f"Missing path params on {method} {path}"

            rule = policy_lookup.get((method.upper(), path))
            if rule is None and path == "/api/admin/account-backups" or rule is None:
                # Try FastAPI typed parameter path variant if needed (e.g. {kb_number:int}).
                candidates = [
                    r
                    for (pm, pp), r in policy_lookup.items()
                    if pm == method.upper() and re.sub(r"\{([A-Za-z0-9_]+):[^}]+\}", r"{\1}", pp) == path
                ]
                assert candidates, f"Missing ROUTE_POLICY entry for {method.upper()} {path}"
                rule = candidates[0]
            assert op["x-claire-access"] == rule.access
            assert op["x-claire-surface"] == rule.surface
            assert op["x-claire-capability"] == rule.capability
            if rule.step_up:
                assert op.get("x-claire-step-up") is True

            for resp in op["responses"].values():
                for media in resp.get("content", {}).values():
                    ref = media.get("schema", {}).get("$ref")
                    if ref:
                        schema_name = ref.rsplit("/", 1)[-1]
                        assert schema_name in schemas, f"Broken $ref {ref} on {method} {path}"

    assert untagged == 0


def test_build_scalar_sources_filters_privileged_specs_by_role() -> None:
    for scope, is_admin in [(None, False), ("anonymous", False), ("readonly", False), ("write", False), ("readonly", True), ("write", True)]:
        sources = build_scalar_sources(auth_scope=scope, is_admin=is_admin)
        assert sources == [{"title": "Public API v1", "slug": "v1", "url": "/openapi/v1.json", "default": True}]

    owner_sources = build_scalar_sources(auth_scope="owner", is_admin=False)
    assert [entry["slug"] for entry in owner_sources] == ["v1", "account"]
    assert owner_sources[0]["default"] is True
    assert owner_sources[1]["url"] == "/openapi/account.json"

    admin_sources = build_scalar_sources(auth_scope="owner", is_admin=True)
    assert [entry["slug"] for entry in admin_sources] == ["v1", "account", "admin"]
    assert admin_sources[2]["url"] == "/openapi/admin.json"


def test_build_v1_openapi_spec_includes_oauth_mcp_and_valid_yaml() -> None:
    spec = build_v1_openapi_spec()
    combined_policy = dict(ROUTE_POLICY)
    combined_policy.update(V1_ROUTE_POLICY)
    _assert_valid_openapi_3_1_structure(spec, policy_lookup=combined_policy)

    expected_oauth_paths = {
        "/.well-known/oauth-protected-resource",
        "/.well-known/oauth-authorization-server",
        "/oauth/register",
        "/oauth/authorize",
        "/oauth/token",
        "/oauth/revoke",
        "/oauth/device_authorization",
    }
    assert expected_oauth_paths.issubset(set(spec["paths"].keys()))
    assert "/api/v1/health" in spec["paths"]
    assert "/api/v1/kbs" in spec["paths"]
    assert "/mcp" in spec["info"]["description"]
    assert "extract_submit" in spec["info"]["description"]

    # Ensure zero HTML console/workspace pages leak into the public v1 spec.
    for path in spec["paths"]:
        assert not path.startswith(("/account", "/settings", "/admin", "/moderation", "/laboratory"))
        assert not path.startswith("/tokens")

    oauth_scheme = spec["components"]["securitySchemes"]["OAuth2"]["flows"]["authorizationCode"]
    assert oauth_scheme["authorizationUrl"] == "/oauth/authorize"
    assert oauth_scheme["tokenUrl"] == "/oauth/token"
    assert oauth_scheme["x-usePkce"] == "SHA-256"

    json_text = build_v1_openapi_json()
    yaml_text = build_v1_openapi_yaml()
    repo_root = str(Path(__file__).resolve().parents[1])
    proc = subprocess.run(
        [
            "python3",
            "-c",
            "import json, sys, yaml; y = yaml.safe_load(sys.stdin.read()); assert y['openapi'] == '3.1.0'; print(json.dumps(y, sort_keys=True))",
        ],
        input=yaml_text,
        text=True,
        capture_output=True,
        check=True,
        cwd=repo_root,
    )
    assert json.loads(proc.stdout) == json.loads(json_text)


def test_build_account_openapi_spec_matches_route_policy_and_excludes_html_and_tokens() -> None:
    spec = build_account_openapi_spec()
    _assert_valid_openapi_3_1_structure(spec, policy_lookup=dict(ROUTE_POLICY))

    paths = set(spec["paths"].keys())
    expected_included = {
        "/api/me",
        "/api/me/oauth/grants",
        "/api/me/provider-keys",
        "/api/feeds",
        "/api/feeds/items",
        "/api/federation/status",
        "/api/federation/publish",
        "/api/invites",
        "/api/auto-rule",
        "/api/contributions",
        "/api/contribution-blocks",
        "/api/mutual-collaborations",
        "/api/reputation",
        "/api/support/bundles",
        "/api/support/bundles/{bundle_id}",
        "/api/syndication/dispatch",
        "/api/syndication/dispatches",
        "/kb",
        "/documents",
        "/graph",
        "/ingest",
        "/",
    }
    assert expected_included.issubset(paths)
    # Only PATCH and DELETE on "/" belong in workspace BFF; GET "/" is HTML SPA and must be excluded.
    assert set(spec["paths"]["/"].keys()) == {"patch", "delete"}
    assert spec["paths"]["/"]["delete"]["x-claire-step-up"] is True

    for path in paths:
        assert not path.startswith(("/api/v1/", "/api/admin/", "/account", "/settings", "/admin", "/moderation", "/laboratory", "/tokens"))
        assert path not in {"/me", "/setup", "/login", "/logout", "/support/bundles", "/support/pipeline", "/syndication", "/syndication/preferences"}


def test_build_admin_openapi_spec_matches_route_policy_and_excludes_ssr_html() -> None:
    spec = build_admin_openapi_spec()
    _assert_valid_openapi_3_1_structure(spec, policy_lookup=dict(ROUTE_POLICY))

    paths = set(spec["paths"].keys())
    expected_admin_paths = {
        "/api/admin/accounts",
        "/api/admin/server",
        "/api/admin/themes",
        "/api/admin/federation",
        "/api/admin/laboratory/runs",
        "/api/admin/kb/{number}/moderators/{account}",
        "/providers",
        "/providers/test",
        "/limits",
        "/ingest-limits",
        "/support/bundle",
    }
    assert expected_admin_paths.issubset(paths)
    assert spec["paths"]["/api/admin/kb/{number}/moderators/{account}"]["put"]["x-claire-step-up"] is True
    assert spec["paths"]["/api/admin/kb/{number}/moderators/{account}"]["delete"]["x-claire-step-up"] is True

    for path, path_item in spec["paths"].items():
        assert not path.startswith(("/admin", "/laboratory"))
        assert path not in {"/moderation/oversight", "/moderation/themes"}
        for op in path_item.values():
            assert op["x-claire-access"] == "admin"


def test_render_reference_html_respects_locale_role_filtering_and_offline_scalar_config() -> None:
    anon_html = render_reference_html(auth_scope=None, is_admin=False, locale="en", base_url="https://claire.example.com")
    assert '<html lang="en" data-theme="dark">' in anon_html
    assert 'src="/static/docs/scalar.standalone.js"' in anon_html
    assert "https://cdn." not in anon_html
    assert "/openapi/v1.json" in anon_html
    assert "/openapi/account.json" not in anon_html
    assert "/openapi/admin.json" not in anon_html

    match = re.search(r"data-configuration='([^']+)'", anon_html)
    assert match is not None
    anon_cfg = json.loads(html_lib.unescape(match.group(1)))
    assert anon_cfg["withDefaultFonts"] is False
    assert anon_cfg["oauth2RedirectUri"] == "https://claire.example.com/reference"
    assert anon_cfg["authentication"]["preferredSecurityScheme"] == "OAuth2"
    assert [src["slug"] for src in anon_cfg["sources"]] == ["v1"]

    owner_html = render_reference_html(auth_scope="owner", is_admin=False, locale="ko")
    assert '<html lang="ko" data-theme="dark">' in owner_html
    assert "/openapi/account.json" in owner_html
    assert "/openapi/admin.json" not in owner_html

    admin_html = render_reference_html(auth_scope="owner", is_admin=True, locale="ko")
    assert "/openapi/v1.json" in admin_html
    assert "/openapi/account.json" in admin_html
    assert "/openapi/admin.json" in admin_html


def test_generated_openapi_yaml_matches_implementation_doc_with_zero_diff() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    assert not (repo_root / "src" / "bible" / "static" / "docs" / "openapi.yaml").exists()
    doc_path = repo_root / "docs" / "implementation" / "openapi.yaml"
    assert doc_path.read_text(encoding="utf-8") == build_v1_openapi_yaml()
    assert V1_ROUTE_POLICY.items() <= ROUTE_POLICY.items()


def test_e2e_reference_and_openapi_endpoints_in_live_app(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir(parents=True, exist_ok=True)
    settings = Settings(
        _env_file=None,
        CLAIRE_ENVIRONMENT="development",
        CLAIRE_FQDN="http://127.0.0.1:8765",
        CLAIRE_DB_PATH=str(data_dir / "claire.db"),
        CLAIRE_PLUGINS_DIR=str(plugins_dir),
        CLAIRE_CORS_ALLOWED_ORIGINS="",
        CLAIRE_DATA_LIFECYCLE="purgeable",
        CLAIRE_PROVIDER="mock",
    )
    app = server.create_app(settings, SimpleNamespace(provider=SimpleNamespace(name="mock", embed_model="mock-embed")))
    conn = dbm.connect_existing(settings.db_file)
    try:
        accounts_store.AccountStore(conn).complete_setup(1, username="adminuser", display_name="Admin")
        accounts_store.AccountStore(conn).set_setting("mode", "network", updated_by=1)
    finally:
        conn.close()
    alice_id = ensure_test_account(settings, account_id=2, username="alice", role="member")
    mgr = KnowledgeBaseManager(settings)
    mgr.define_kb("Alice Personal", is_personal=True, is_public=True, account_id=alice_id)

    admin_session = issue_session_token(settings, account_id=1, scope="owner")
    alice_session = issue_session_token(settings, account_id=alice_id, scope="owner")
    alice_write = issue_oauth_token(settings, account_id=alice_id, scope="write", client_id="ref-write-client")
    alice_ro = issue_oauth_token(settings, account_id=alice_id, scope="readonly", client_id="ref-ro-client")

    client = TestClient(app, base_url="http://127.0.0.1:8765")

    # Public specs and live /api/v1/health work without credentials.
    assert client.get("/api/v1/health").status_code == 200
    r_v1_json = client.get("/openapi/v1.json")
    assert r_v1_json.status_code == 200
    assert "/api/v1/health" in r_v1_json.json()["paths"]
    assert client.get("/openapi.yaml").text == build_v1_openapi_yaml()

    # Unauthorized callers receive 404 on account/admin OpenAPI specs.
    for hdrs in ({}, {"Authorization": f"Bearer {alice_ro}"}, {"Authorization": f"Bearer {alice_write}"}):
        assert client.get("/openapi/account.json", headers=hdrs).status_code == 404
        assert client.get("/openapi/admin.json", headers=hdrs).status_code == 404
    assert client.get("/openapi/admin.json", headers={"Cookie": f"claire_session={alice_session}"}).status_code == 404

    # Authorized owner receives account.json with strict isolation headers; admin receives admin.json.
    r_acc = client.get("/openapi/account.json", headers={"Cookie": f"claire_session={alice_session}"})
    assert r_acc.status_code == 200
    assert r_acc.headers["cache-control"] == "private, no-store"
    assert r_acc.headers["vary"] == "Cookie, Authorization"
    assert r_acc.headers["referrer-policy"] == "same-origin"

    r_adm = client.get("/openapi/admin.json", headers={"Cookie": f"claire_session={admin_session}"})
    assert r_adm.status_code == 200
    assert r_adm.headers["cache-control"] == "private, no-store"

    # /reference filters tabs per caller role and renders dynamic locale + isolation headers.
    client.cookies.clear()
    r_ref_anon = client.get("/reference")
    assert r_ref_anon.status_code == 200
    assert r_ref_anon.headers["cache-control"] == "private, no-store"
    assert r_ref_anon.headers["vary"] == "Cookie, Authorization"
    assert "/openapi/v1.json" in r_ref_anon.text
    assert "/openapi/account.json" not in r_ref_anon.text
    assert "/openapi/admin.json" not in r_ref_anon.text

    client.cookies.clear()
    r_ref_owner = client.get("/reference", headers={"Cookie": f"claire_session={alice_session}", "Accept-Language": "en"})
    assert r_ref_owner.status_code == 200
    assert '<html lang="en" data-theme="dark">' in r_ref_owner.text
    assert "/openapi/account.json" in r_ref_owner.text
    assert "/openapi/admin.json" not in r_ref_owner.text

    client.cookies.clear()
    r_ref_admin = client.get("/reference", headers={"Cookie": f"claire_session={admin_session}"})
    assert r_ref_admin.status_code == 200
    assert "/openapi/v1.json" in r_ref_admin.text
    assert "/openapi/account.json" in r_ref_admin.text
    assert "/openapi/admin.json" in r_ref_admin.text

    # Console /account/mcp and /admin/mcp include /reference discoverability links.
    client.cookies.clear()
    r_mcp_owner = client.get("/account/mcp", headers={"Cookie": f"claire_session={alice_session}"})
    assert r_mcp_owner.status_code == 200
    assert 'href="/reference"' in r_mcp_owner.text

    client.cookies.clear()
    r_mcp_admin = client.get("/admin/mcp", headers={"Cookie": f"claire_session={admin_session}"})
    assert r_mcp_admin.status_code == 200
    assert 'href="/reference"' in r_mcp_admin.text
    assert security.ROUTE_POLICY[("GET", "/reference")].access == "read"


def test_legacy_alias_routes_emit_deprecation_and_successor_headers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(IngestService, "regenerate_components", lambda self, **kw: {"ok": True, "dry_run": True})
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir(parents=True, exist_ok=True)
    settings = Settings(
        _env_file=None,
        CLAIRE_ENVIRONMENT="development",
        CLAIRE_FQDN="http://127.0.0.1:8765",
        CLAIRE_DB_PATH=str(data_dir / "claire.db"),
        CLAIRE_PLUGINS_DIR=str(plugins_dir),
        CLAIRE_CORS_ALLOWED_ORIGINS="",
        CLAIRE_DATA_LIFECYCLE="purgeable",
        CLAIRE_PROVIDER="mock",
    )
    app = server.create_app(settings, SimpleNamespace(provider=SimpleNamespace(name="mock", embed_model="mock-embed")))
    conn = dbm.connect_existing(settings.db_file)
    try:
        accounts_store.AccountStore(conn).complete_setup(1, username="adminuser", display_name="Admin")
        accounts_store.AccountStore(conn).set_setting("mode", "network", updated_by=1)
    finally:
        conn.close()

    alice_id = ensure_test_account(settings, account_id=2, username="alice", role="member")
    mgr = KnowledgeBaseManager(settings)
    alice_personal = mgr.define_kb("Alice Personal", is_personal=True, is_public=False, account_id=alice_id)
    shared_src = mgr.define_kb("Public Source", is_personal=False, is_public=True, account_id=1, knowledge_type="real")

    conn = dbm.connect_existing(settings.db_file)
    try:
        from claire.ontology.base import Document

        doc = Document(
            id="doc_alias_src",
            url="https://example.com/alias-src",
            title="Alias Source Doc",
            raw_text="Sample content for regeneration and syndication alias header tests.",
        )
        dbm.insert_document(conn, doc, kb_id=shared_src.id)
        doc_personal = Document(
            id="doc_alias_personal",
            url="https://example.com/alias-personal",
            title="Alias Personal Doc",
            raw_text="Personal content for dry-run regeneration.",
        )
        dbm.insert_document(conn, doc_personal, kb_id=alice_personal.id)
    finally:
        conn.close()

    admin_session = issue_session_token(settings, account_id=1, scope="owner")
    alice_session = issue_session_token(settings, account_id=alice_id, scope="owner")
    client = TestClient(app, base_url="http://127.0.0.1:8765")

    admin_hdrs = {"Cookie": f"claire_session={admin_session}", "Origin": "http://127.0.0.1:8765"}
    alice_hdrs = {"Cookie": f"claire_session={alice_session}", "Origin": "http://127.0.0.1:8765"}

    def _assert_deprecated(resp: Any, successor: str) -> None:
        assert resp.status_code < 400, f"unexpected {resp.status_code}: {resp.text}"
        assert resp.headers.get("Deprecation") == "true"
        assert resp.headers.get("Link") == f'<{successor}>; rel="successor-version"'

    _assert_deprecated(client.get("/ingest-limits", headers=admin_hdrs), "/limits")
    _assert_deprecated(client.head("/ingest-limits", headers=admin_hdrs), "/limits")
    _assert_deprecated(client.patch("/ingest-limits", headers=admin_hdrs, json={}), "/limits")
    _assert_deprecated(client.post("/ingest-limits", headers=admin_hdrs, json={}), "/limits")

    _assert_deprecated(client.get("/syndication/dispatches", headers=alice_hdrs), "/api/syndication/dispatches")
    _assert_deprecated(client.head("/syndication/dispatches", headers=alice_hdrs), "/api/syndication/dispatches")
    _assert_deprecated(
        client.post(
            "/syndication/dispatch",
            headers=alice_hdrs,
                json={
                    "document_id": "doc_alias_src",
                    "source_kb_id": shared_src.id,
                    "target_kb_id": alice_personal.id,
                },
            ),
            "/api/syndication/dispatch",
        )

    _assert_deprecated(
        client.post(
            "/document/regenerate",
            headers=alice_hdrs,
            json={"doc_id": "doc_alias_personal", "kb_id": alice_personal.id, "dry_run": True},
        ),
        "/regenerate",
    )

    _assert_deprecated(
        client.post(
            "/purge",
            headers=alice_hdrs,
            json={"target": "doc_alias_personal", "kb_id": alice_personal.id, "dry_run": True},
        ),
        "/api/v1/kbs/personal/documents",
    )

    _assert_deprecated(client.post("/support/bundle", headers=admin_hdrs, json={}), "/api/v1/support/bundles")

