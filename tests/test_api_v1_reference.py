"""Unit tests for `API-4a` multi-document OpenAPI 3.1 specs and role-expanded Scalar reference renderer."""

from __future__ import annotations

import html as html_lib
import json
from pathlib import Path
import re
import subprocess
from typing import Any

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

    match = re.search(r'data-configuration="([^"]+)"', anon_html)
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
