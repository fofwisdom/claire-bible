"""Multi-document OpenAPI 3.1 builders and role-expanded Scalar reference renderer (`AR6`-`AR9`)."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass
import html as html_lib
import json
import re
from typing import Any

from pydantic.json_schema import models_json_schema

from ..security import ROUTE_POLICY, RouteKey, RouteRule
from .openapi import (
    API_TITLE,
    API_VERSION,
    MCP_DESCRIPTION,
    OPENAPI_VERSION,
    RETIRED_PARAMETER_NAMES,
    _SECURITY_SCHEMES,
    generate_v1_openapi_spec,
    to_yaml,
)
from .router import V1_ROUTE_POLICY, V1_ROUTE_SPECS, V1RouteSpec
from .schemas import ErrorEnvelope

_PATH_PARAM_RE = re.compile(r"\{([A-Za-z0-9_]+)(?::[^}]+)?\}")
_LOCALE_RE = re.compile(r"^[a-z]{2}(?:-[A-Za-z0-9]{2,8})?$")

OAUTH_TAG = "OAuth 2.1"
OAUTH_TAG_DESCRIPTION = "OAuth 2.1 metadata, dynamic client registration, PKCE authorization, token exchange, revocation, and device flow."

ACCOUNT_TAG_DESCRIPTIONS: dict[str, str] = {
    "Account": "Authenticated account profile, keys, notifications, backups, and security endpoints.",
    "Feeds": "Personal feed subscriptions, OPML import/export, polling, and feed item ingestion.",
    "Federation": "ActivityPub status, remote following, timeline interactions, quotes, and outbound publishing.",
    "Collaboration": "Account-level contributions, blocks, mutual collaborations, reputation, and theme submissions.",
    "Invites": "Network-mode invitation management.",
    "Syndication": "Cross-posting and syndication dispatch endpoints.",
    "Support": "Account support bundles and knowledge base graph export bundles.",
    "Workspace": "Knowledge base workspace BFF JSON endpoints for settings, documents, curation, and graph actions.",
}

ADMIN_TAG_DESCRIPTIONS: dict[str, str] = {
    "Admin Accounts": "Server administrator account management, signup trace, and retired username controls.",
    "Admin Federation": "Domain blocks, known federation servers, reading settings, and inbox journal replay.",
    "Admin Themes": "Theme lifecycle, thresholds, membership rules, and merge operations.",
    "Admin Server": "Server mode, default languages, tier, storage, IdP, plugins, and telemetry.",
    "Admin Laboratory": "Laboratory benchmark runs and speech-to-text hint jobs.",
    "Admin Moderation": "Shared knowledge base moderator assignments and oversight APIs.",
    "Providers & Limits": "LLM provider configuration tests, rate limits, and ingest limits.",
    "Support": "Server-wide diagnostic support bundle generation.",
}


@dataclass(frozen=True, slots=True)
class _ProtocolRouteSpec:
    method: str
    path: str
    operation_id: str
    summary: str
    description: str
    query_params: tuple[tuple[str, bool, str], ...] = ()
    request_content_type: str | None = None
    request_description: str | None = None
    response_status: int = 200
    response_media_type: str = "application/json"


_OAUTH_PROTOCOL_SPECS: tuple[_ProtocolRouteSpec, ...] = (
    _ProtocolRouteSpec(
        method="GET",
        path="/.well-known/oauth-protected-resource",
        operation_id="oauth_protected_resource_metadata",
        summary="Get OAuth 2.1 protected resource metadata",
        description="Return RFC 9728 protected resource metadata for MCP and /api/v1/* endpoints.",
    ),
    _ProtocolRouteSpec(
        method="GET",
        path="/.well-known/oauth-authorization-server",
        operation_id="oauth_authorization_server_metadata",
        summary="Get OAuth 2.1 authorization server metadata",
        description="Return RFC 8414 authorization server endpoints, supported scopes, and PKCE S256 capabilities.",
    ),
    _ProtocolRouteSpec(
        method="POST",
        path="/oauth/register",
        operation_id="oauth_register_client",
        summary="Register OAuth 2.1 dynamic client",
        description="Register a public or confidential OAuth 2.1 client under RFC 7591.",
        request_content_type="application/json",
        request_description="Dynamic client registration metadata including client_name and redirect_uris.",
        response_status=201,
    ),
    _ProtocolRouteSpec(
        method="GET",
        path="/oauth/authorize",
        operation_id="oauth_authorize_start",
        summary="Start OAuth 2.1 PKCE authorization",
        description="Validate PKCE S256 authorization code parameters and present user consent or redirect with code.",
        query_params=(
            ("response_type", True, "Must be 'code'."),
            ("client_id", True, "Registered OAuth 2.1 client identifier."),
            ("redirect_uri", True, "Registered callback URI."),
            ("code_challenge", True, "Base64url SHA-256 PKCE code challenge."),
            ("code_challenge_method", True, "Must be 'S256'."),
            ("scope", False, "Space-separated scopes: readonly or write."),
            ("state", False, "Opaque CSRF protection state value."),
        ),
        response_status=200,
        response_media_type="text/html",
    ),
    _ProtocolRouteSpec(
        method="POST",
        path="/oauth/token",
        operation_id="oauth_exchange_token",
        summary="Exchange authorization code or refresh token",
        description="Issue Bearer access and refresh tokens via authorization_code (PKCE S256), refresh_token, device_code, or token-exchange.",
        request_content_type="application/x-www-form-urlencoded",
        request_description="OAuth 2.1 token grant parameters.",
    ),
    _ProtocolRouteSpec(
        method="POST",
        path="/oauth/revoke",
        operation_id="oauth_revoke_token",
        summary="Revoke OAuth 2.1 access or refresh token",
        description="Revoke an issued access or refresh token under RFC 7009.",
        request_content_type="application/x-www-form-urlencoded",
        request_description="Token revocation payload containing token and optional token_type_hint.",
    ),
    _ProtocolRouteSpec(
        method="POST",
        path="/oauth/device_authorization",
        operation_id="oauth_device_authorization",
        summary="Start OAuth 2.1 device authorization grant",
        description="Issue device_code and user_code under RFC 8628 for headless CLI or device clients.",
        request_content_type="application/x-www-form-urlencoded",
        request_description="Device authorization parameters including client_id and optional scope.",
    ),
)

_ACCOUNT_HTML_PATHS: frozenset[str] = frozenset(
    {
        "/me",
        "/setup",
        "/login",
        "/logout",
        "/syndication",
        "/syndication/preferences",
        "/support/bundles",
        "/support/pipeline",
        "/directory",
        "/accounts",
        "/device",
        "/reference",
        "/openapi.yaml",
        "/health",
        "/robots.txt",
        "/ns",
        "/ns/v1",
        "/sitemap.xml",
        "/favicon.ico",
        "/favicon.svg",
        "/apple-touch-icon.png",
        "/apple-touch-icon-precomposed.png",
        "/manifest.json",
        "/site.webmanifest",
        "/browserconfig.xml",
        "/sw.js",
        "/icon",
        "/font",
        "/p",
        "/p/presentation",
        "/image",
        "/home-profile",
        "/home-timeline",
        "/api/locale",
        "/mcp",
    }
)

_ACCOUNT_EXCLUDED_PREFIXES: tuple[str, ...] = (
    "/api/v1/",
    "/api/admin/",
    "/api/client-extraction/",
    "/account",
    "/settings",
    "/moderation",
    "/admin",
    "/laboratory",
    "/tokens",
    "/providers",
    "/limits",
    "/ingest-limits",
    "/render/",
    "/media/",
    "/static/",
    "/fonts/",
    "/images/",
    "/p/images/",
    "/silvercord/",
    "/entity/",
    "/support/bundle",
    "/support/graph-bundle/",
    "/invite/",
    "/auth/",
    "/.well-known/",
    "/oauth/",
)

_ADMIN_EXCLUDED_PREFIXES: tuple[str, ...] = (
    "/admin",
    "/laboratory",
)

_ADMIN_EXCLUDED_EXACT: frozenset[str] = frozenset(
    {
        "/moderation/oversight",
        "/moderation/themes",
    }
)


def _normalize_openapi_path(path: str) -> str:
    return _PATH_PARAM_RE.sub(lambda match: f"{{{match.group(1)}}}", path)


def _extract_path_parameters(path: str) -> list[dict[str, Any]]:
    params: list[dict[str, Any]] = []
    seen: set[str] = set()
    for match in _PATH_PARAM_RE.finditer(path):
        name = match.group(1)
        if name in RETIRED_PARAMETER_NAMES:
            raise ValueError(f"Retired parameter {name!r} cannot be emitted in OpenAPI spec")
        if name in seen:
            continue
        seen.add(name)
        params.append(
            {
                "name": name,
                "in": "path",
                "required": True,
                "schema": {"type": "string"},
            }
        )
    return params


def _rule_security_requirements(rule: RouteRule) -> list[dict[str, list[str]]]:
    if rule.access == "public":
        return []
    if rule.access == "read":
        return [{"SessionCookie": []}, {"BearerAuth": []}]
    if rule.access == "write":
        return [{"BearerAuth": []}, {"SessionCookie": []}]
    if rule.access == "admin" and rule.step_up:
        return [{"SessionCookie": []}]
    return [{"SessionCookie": []}, {"BearerAuth": []}]


def _error_schema_components() -> dict[str, Any]:
    _, top = models_json_schema([(ErrorEnvelope, "validation")], ref_template="#/components/schemas/{model}")
    defs = dict(top.get("$defs") or {})
    return {name: defs[name] for name in sorted(defs)}


def _is_account_bff_route(method: str, path: str, rule: RouteRule) -> bool:
    if method == "HEAD":
        return False
    if rule.access in {"admin", "public"}:
        return False
    if path == "/" and method == "GET":
        return False
    if path in _ACCOUNT_HTML_PATHS:
        return False
    if any(path == prefix or path.startswith(prefix) for prefix in _ACCOUNT_EXCLUDED_PREFIXES):
        return False
    return True


def _is_admin_api_route(method: str, path: str, rule: RouteRule) -> bool:
    if method == "HEAD":
        return False
    if rule.access != "admin":
        return False
    if path.startswith("/api/v1/"):
        return False
    if path in _ADMIN_EXCLUDED_EXACT:
        return False
    if any(path == prefix or path.startswith(prefix + "/") for prefix in _ADMIN_EXCLUDED_PREFIXES):
        return False
    return True


def _tag_for_account_route(path: str) -> str:
    if path.startswith("/api/me"):
        return "Account"
    if path.startswith("/api/feeds"):
        return "Feeds"
    if path.startswith("/api/federation"):
        return "Federation"
    if path in {"/api/invites", "/api/auth/reauth"}:
        return "Invites" if path == "/api/invites" else "Account"
    if path.startswith("/api/syndication") or path.startswith("/syndication/dispatch"):
        return "Syndication"
    if path.startswith("/api/support/") or path == "/support/graph-bundle":
        return "Support"
    if path.startswith(("/api/auto-rule", "/api/contributions", "/api/contribution-blocks", "/api/mutual-collaborations", "/api/reputation")):
        return "Collaboration"
    return "Workspace"


def _tag_for_admin_route(path: str) -> str:
    if path.startswith("/api/admin/accounts") or path.startswith("/api/admin/usernames") or path == "/api/admin/account-backups" or path.startswith("/api/admin/account-backups/"):
        return "Admin Accounts"
    if path.startswith("/api/admin/federation"):
        return "Admin Federation"
    if path.startswith("/api/admin/themes"):
        return "Admin Themes"
    if path.startswith("/api/admin/laboratory"):
        return "Admin Laboratory"
    if path.startswith("/api/admin/kb/"):
        return "Admin Moderation"
    if path.startswith("/api/admin/expansion") or path.startswith("/api/admin/pair-review") or path.startswith("/api/admin/reputations") or path == "/api/admin/auto-contribution":
        return "Admin Moderation"
    if path.startswith(("/providers", "/limits", "/ingest-limits")):
        return "Providers & Limits"
    if path == "/support/bundle":
        return "Support"
    return "Admin Server"


def _make_operation_id(method: str, path: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", path).strip("_") or "root"
    return f"{method.lower()}_{slug}"


def _build_policy_spec(
    *,
    title: str,
    description: str,
    route_policy: Mapping[RouteKey, RouteRule],
    include_fn: Callable[[str, str, RouteRule], bool],
    tag_fn: Callable[[str], str],
    tag_descriptions: Mapping[str, str],
    security_schemes: Mapping[str, Any],
) -> dict[str, Any]:
    paths: dict[str, dict[str, Any]] = {}
    used_tags: set[str] = set()

    sorted_keys = sorted(route_policy.keys(), key=lambda item: (item[1], item[0]))
    for method, raw_path in sorted_keys:
        rule = route_policy[(method, raw_path)]
        if not include_fn(method, raw_path, rule):
            continue
        openapi_path = _normalize_openapi_path(raw_path)
        tag = tag_fn(raw_path)
        if not tag:
            raise ValueError(f"Operation {method} {raw_path} has no tag")
        used_tags.add(tag)

        params = _extract_path_parameters(raw_path)
        operation: dict[str, Any] = {
            "tags": [tag],
            "summary": f"{method} {openapi_path}",
            "description": f"Surface `{rule.surface}` route requiring `{rule.access}` access (capability `{rule.capability}`).",
            "operationId": _make_operation_id(method, openapi_path),
            "x-claire-access": rule.access,
            "x-claire-surface": rule.surface,
            "x-claire-capability": rule.capability,
            "security": _rule_security_requirements(rule),
            "responses": {
                "200": {
                    "description": "Operation succeeded.",
                    "content": {
                        "application/json": {
                            "schema": {"type": "object"},
                        }
                    },
                },
                "401": {
                    "description": "Authentication required or invalid credential.",
                    "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ErrorEnvelope"}}},
                },
                "403": {
                    "description": "Insufficient scope or capability.",
                    "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ErrorEnvelope"}}},
                },
                "404": {
                    "description": "Resource not found or hidden.",
                    "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ErrorEnvelope"}}},
                },
            },
        }
        if rule.step_up:
            operation["x-claire-step-up"] = True
        if params:
            operation["parameters"] = params
        if method in {"POST", "PUT", "PATCH"}:
            operation["requestBody"] = {
                "required": False,
                "content": {
                    "application/json": {
                        "schema": {"type": "object"},
                    }
                },
            }
        paths.setdefault(openapi_path, {})[method.lower()] = operation

    tags = [
        {"name": name, "description": tag_descriptions.get(name, name)}
        for name in tag_descriptions
        if name in used_tags
    ]
    for extra in sorted(used_tags - set(tag_descriptions)):
        tags.append({"name": extra, "description": extra})

    return {
        "openapi": OPENAPI_VERSION,
        "info": {
            "title": title,
            "version": API_VERSION,
            "description": description,
            "license": {
                "name": "MIT",
                "url": "https://opensource.org/licenses/MIT",
            },
        },
        "servers": [{"url": "/", "description": "Current host root"}],
        "tags": tags,
        "paths": paths,
        "components": {
            "schemas": _error_schema_components(),
            "securitySchemes": deepcopy(dict(security_schemes)),
        },
    }


def build_v1_openapi_spec(
    *,
    route_specs: tuple[V1RouteSpec, ...] = V1_ROUTE_SPECS,
    v1_route_policy: Mapping[RouteKey, RouteRule] = V1_ROUTE_POLICY,
    server_route_policy: Mapping[RouteKey, RouteRule] = ROUTE_POLICY,
) -> dict[str, Any]:
    """Build the public OpenAPI 3.1.0 spec for `/api/v1/*`, OAuth 2.1 endpoints, and MCP docs."""
    spec = generate_v1_openapi_spec(route_specs=route_specs, route_policy=v1_route_policy)
    paths: dict[str, dict[str, Any]] = dict(spec["paths"])

    for proto in _OAUTH_PROTOCOL_SPECS:
        rule = server_route_policy.get((proto.method, proto.path))
        if rule is None:
            raise ValueError(f"OAuth protocol route ({proto.method!r}, {proto.path!r}) missing from ROUTE_POLICY")

        operation: dict[str, Any] = {
            "tags": [OAUTH_TAG],
            "summary": proto.summary,
            "description": proto.description,
            "operationId": proto.operation_id,
            "x-claire-access": rule.access,
            "x-claire-surface": rule.surface,
            "x-claire-capability": rule.capability,
            "security": [],
            "responses": {
                str(proto.response_status): {
                    "description": proto.summary,
                    "content": {
                        proto.response_media_type: {
                            "schema": {"type": "object"} if proto.response_media_type == "application/json" else {"type": "string"},
                        }
                    },
                },
                "400": {
                    "description": "Invalid OAuth 2.1 request parameters.",
                    "content": {"application/json": {"schema": {"type": "object"}}},
                },
            },
        }
        if proto.query_params:
            params: list[dict[str, Any]] = []
            for p_name, p_req, p_desc in proto.query_params:
                if p_name in RETIRED_PARAMETER_NAMES:
                    raise ValueError(f"Retired parameter {p_name!r} cannot be emitted in OpenAPI spec")
                params.append(
                    {
                        "name": p_name,
                        "in": "query",
                        "required": p_req,
                        "description": p_desc,
                        "schema": {"type": "string"},
                    }
                )
            operation["parameters"] = params
        if proto.request_content_type is not None:
            operation["requestBody"] = {
                "required": True,
                "description": proto.request_description or proto.summary,
                "content": {
                    proto.request_content_type: {
                        "schema": {"type": "object"},
                    }
                },
            }
        paths.setdefault(proto.path, {})[proto.method.lower()] = operation

    tags = list(spec["tags"])
    if not any(tag["name"] == OAUTH_TAG for tag in tags):
        tags.append({"name": OAUTH_TAG, "description": OAUTH_TAG_DESCRIPTION})

    spec["tags"] = tags
    spec["paths"] = paths
    spec["info"]["description"] = MCP_DESCRIPTION
    return spec


def build_v1_openapi_json(
    *,
    route_specs: tuple[V1RouteSpec, ...] = V1_ROUTE_SPECS,
    v1_route_policy: Mapping[RouteKey, RouteRule] = V1_ROUTE_POLICY,
    server_route_policy: Mapping[RouteKey, RouteRule] = ROUTE_POLICY,
    indent: int = 2,
) -> str:
    """Serialize the public v1 + OAuth 2.1 OpenAPI 3.1.0 spec to JSON."""
    spec = build_v1_openapi_spec(route_specs=route_specs, v1_route_policy=v1_route_policy, server_route_policy=server_route_policy)
    return json.dumps(spec, indent=indent, ensure_ascii=False) + "\n"


def build_v1_openapi_yaml(
    *,
    route_specs: tuple[V1RouteSpec, ...] = V1_ROUTE_SPECS,
    v1_route_policy: Mapping[RouteKey, RouteRule] = V1_ROUTE_POLICY,
    server_route_policy: Mapping[RouteKey, RouteRule] = ROUTE_POLICY,
) -> str:
    """Serialize the public v1 + OAuth 2.1 OpenAPI 3.1.0 spec to deterministic YAML."""
    spec = build_v1_openapi_spec(route_specs=route_specs, v1_route_policy=v1_route_policy, server_route_policy=server_route_policy)
    return to_yaml(spec)


def build_account_openapi_spec(
    *,
    route_policy: Mapping[RouteKey, RouteRule] = ROUTE_POLICY,
) -> dict[str, Any]:
    """Build the OpenAPI 3.1.0 spec for authenticated owner account and workspace BFF routes."""
    return _build_policy_spec(
        title=f"{API_TITLE} - Account & Workspace BFF",
        description="Authenticated owner account management (`/api/me/*`, `/api/feeds/*`, `/api/federation/*`, `/api/invites`, `/api/auto-rule*`, `/api/support/bundles*`) and workspace BFF JSON endpoints. SSR HTML pages and retired `/tokens*` endpoints are excluded.",
        route_policy=route_policy,
        include_fn=_is_account_bff_route,
        tag_fn=_tag_for_account_route,
        tag_descriptions=ACCOUNT_TAG_DESCRIPTIONS,
        security_schemes={
            "SessionCookie": _SECURITY_SCHEMES["SessionCookie"],
            "BearerAuth": _SECURITY_SCHEMES["BearerAuth"],
        },
    )


def build_admin_openapi_spec(
    *,
    route_policy: Mapping[RouteKey, RouteRule] = ROUTE_POLICY,
) -> dict[str, Any]:
    """Build the OpenAPI 3.1.0 spec for server administrator operational routes."""
    return _build_policy_spec(
        title=f"{API_TITLE} - Admin & Operations",
        description="Server administrator API endpoints (`/api/admin/*`, `/providers*`, `/limits`, `/ingest-limits`, `/support/bundle`). SSR HTML pages under `/admin/*`, `/laboratory/*`, and `/moderation/*` are excluded.",
        route_policy=route_policy,
        include_fn=_is_admin_api_route,
        tag_fn=_tag_for_admin_route,
        tag_descriptions=ADMIN_TAG_DESCRIPTIONS,
        security_schemes={
            "SessionCookie": _SECURITY_SCHEMES["SessionCookie"],
            "BearerAuth": _SECURITY_SCHEMES["BearerAuth"],
        },
    )


def build_scalar_sources(*, auth_scope: str | None = None, is_admin: bool = False) -> list[dict[str, Any]]:
    """Return Scalar multi-document `sources` entries permitted for the caller role (`AR6`)."""
    sources: list[dict[str, Any]] = [
        {
            "title": "Public API v1",
            "slug": "v1",
            "url": "/openapi/v1.json",
            "default": True,
        }
    ]
    norm_scope = (auth_scope or "").strip().lower()
    if norm_scope in {"owner", "admin"}:
        sources.append(
            {
                "title": "Account & Workspace BFF",
                "slug": "account",
                "url": "/openapi/account.json",
            }
        )
        if is_admin or norm_scope == "admin":
            sources.append(
                {
                    "title": "Admin & Operations",
                    "slug": "admin",
                    "url": "/openapi/admin.json",
                }
            )
    return sources


def _normalize_locale(locale: str | None) -> str:
    cleaned = (locale or "").strip().replace("_", "-")
    if cleaned and _LOCALE_RE.match(cleaned):
        return cleaned.lower()
    return "ko"


def render_reference_html(
    *,
    auth_scope: str | None = None,
    is_admin: bool = False,
    locale: str = "ko",
    oauth2_redirect_uri: str | None = None,
    base_url: str = "",
) -> str:
    """Render the offline Scalar `/reference` HTML page with role-filtered `sources` and OAuth 2.1 PKCE config (`AR6`, `AR9`)."""
    safe_locale = _normalize_locale(locale)
    sources = build_scalar_sources(auth_scope=auth_scope, is_admin=is_admin)
    resolved_redirect = (oauth2_redirect_uri or "").strip()
    if not resolved_redirect:
        clean_base = base_url.strip().rstrip("/")
        resolved_redirect = f"{clean_base}/reference" if clean_base else "/reference"

    config: dict[str, Any] = {
        "theme": "kepler",
        "darkMode": True,
        "favicon": "/favicon.svg",
        "withDefaultFonts": False,
        "proxyUrl": "",
        "searchHotKey": "k",
        "showSidebar": True,
        "layout": "modern",
        "metaData": {
            "title": "Claire API Reference",
            "description": "Claire Knowledge Graph & Analysis Engine HTTP API",
        },
        "defaultHttpClient": {
            "targetKey": "python",
            "clientKey": "httpx",
        },
        "oauth2RedirectUri": resolved_redirect,
        "authentication": {
            "preferredSecurityScheme": "OAuth2",
        },
        "servers": [
            {
                "url": "/",
                "description": "Current Claire Server",
            }
        ],
        "sources": sources,
    }
    config_json = html_lib.escape(json.dumps(config, ensure_ascii=False), quote=True)

    return f"""<!doctype html>
<html lang="{html_lib.escape(safe_locale, quote=True)}" data-theme="dark">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Claire API Reference</title>
  <meta name="mobile-web-app-capable" content="yes" />
  <meta name="apple-mobile-web-app-capable" content="yes" />
  <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent" />
  <meta name="apple-mobile-web-app-title" content="Claire Bible" />
  <meta name="application-name" content="Claire Bible" />
  <link rel="icon" type="image/svg+xml" href="/favicon.svg" />
  <link rel="icon" type="image/png" sizes="192x192" href="/icon?p=android-chrome-192x192.png" />
  <link rel="icon" type="image/png" sizes="512x512" href="/icon?p=android-chrome-512x512.png" />
  <link rel="alternate icon" href="/favicon.ico" />
  <link rel="apple-touch-icon" sizes="180x180" href="/apple-touch-icon.png" />
  <link rel="manifest" href="/manifest.json" />
  <link rel="mask-icon" href="/favicon.svg" color="#00ffaa" />
  <meta name="theme-color" content="#0e1116" />
  <style>
    :root, [data-theme="dark"] {{
      --scalar-font: 'Noto Sans KR', 'Noto Sans Korean', system-ui, -apple-system, sans-serif;
      --scalar-font-code: 'D2Coding', ui-monospace, SFMono-Regular, monospace;
      --scalar-background-1: #0e1116;
      --scalar-background-2: #161b22;
      --scalar-background-3: #1f2937;
      --scalar-border-color: #2a2f37;
      --scalar-color-1: #d7dbe0;
      --scalar-color-2: #8b949e;
      --scalar-color-3: #656d76;
      --scalar-color-accent: #238636;
      --scalar-color-accent-hover: #2ea043;
      --scalar-button-1: #238636;
      --scalar-button-1-color: #ffffff;
      --scalar-button-1-hover: #2ea043;
    }}
    html, body {{
      margin: 0;
      padding: 0;
      height: 100%;
      background-color: var(--scalar-background-1);
      color: var(--scalar-color-1);
      font-family: var(--scalar-font);
      -webkit-font-smoothing: antialiased;
      -moz-osx-font-smoothing: grayscale;
    }}
    #api-reference {{
      min-height: 100vh;
      display: flex;
      flex-direction: column;
    }}
  </style>
</head>
<body>
  <script
    id="api-reference"
    type="application/json"
    data-configuration="{config_json}"></script>
  <script src="/static/docs/scalar.standalone.js"></script>
</body>
</html>
"""
