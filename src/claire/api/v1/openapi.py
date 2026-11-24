"""Code-first OpenAPI 3.1 generator and deterministic YAML serializer for `/api/v1/*` (AR7, AR8, AR9)."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel
from pydantic.json_schema import models_json_schema

from ..security import RouteKey, RouteRule
from .router import V1_ROUTE_POLICY, V1_ROUTE_SPECS, V1ParamSpec, V1RouteSpec
from .schemas import ErrorEnvelope

OPENAPI_VERSION = "3.1.0"
API_TITLE = "Claire Bible Public API"
API_VERSION = "1.0.0"

RETIRED_PARAMETER_NAMES = frozenset({"theme"})

MCP_DESCRIPTION = (
    "Versioned public REST API (`/api/v1/*`) for Claire Bible knowledge bases, documents, "
    "personal KB ingestion, and canonical graph exploration.\n\n"
    "## Authentication\n"
    "- **OAuth 2.1 (`OAuth2`)**: Authorization Code flow with PKCE (`S256`) via `/oauth/authorize` and `/oauth/token` "
    "using scopes `readonly` and `write`.\n"
    "- **Bearer Token (`BearerAuth`)**: OAuth 2.1 access token in `Authorization: Bearer <token>`.\n"
    "- **Same-Origin Browser Session (`SessionCookie`)**: `__Host-claire_session` cookie.\n\n"
    "## Model Context Protocol (`/mcp`)\n"
    "Connect MCP clients to `POST /mcp` using OAuth 2.1 Bearer authentication. "
    "Available read (`readonly`) and write (`write`) tools include `kb_list`, `kb_overview`, "
    "`entity_search`, `entity_get`, `graph_neighbors`, `graph_path`, `graph_context`, "
    "`doc_list`, `doc_get`, `contrib_candidates`, `contrib_submit`, and client-extraction tools "
    "(`extract_spec`, `extract_stage`, `extract_sources`, `extract_claim`, `extract_renew`, `extract_get_source`, `extract_submit`)."
)

TAG_DESCRIPTIONS: dict[str, str] = {
    "System & Identity": "Health checks and caller authentication metadata.",
    "Knowledge Bases": "Knowledge base discovery, metadata, lifecycle, and themes.",
    "Documents & Ingest": "Document listing, details, raw/Markdown export, curation, and personal KB ingestion.",
    "Graph & Search": "Canonical entity/relation graph queries, neighborhood expansion, shortest path, and search.",
}

_SECURITY_SCHEMES: dict[str, Any] = {
    "OAuth2": {
        "type": "oauth2",
        "description": "OAuth 2.1 Authorization Code flow with PKCE (S256).",
        "flows": {
            "authorizationCode": {
                "authorizationUrl": "/oauth/authorize",
                "tokenUrl": "/oauth/token",
                "scopes": {
                    "readonly": "Read knowledge bases, documents, and graph data.",
                    "write": "Ingest documents into the personal knowledge base and perform write actions.",
                },
                "x-usePkce": "SHA-256",
            }
        },
    },
    "BearerAuth": {
        "type": "http",
        "scheme": "bearer",
        "description": "OAuth 2.1 access token passed in the Authorization header.",
    },
    "SessionCookie": {
        "type": "apiKey",
        "in": "cookie",
        "name": "__Host-claire_session",
        "description": "Same-origin authenticated browser session cookie.",
    },
}

_YAML_RESERVED_LOWER = frozenset(
    {"true", "false", "yes", "no", "on", "off", "y", "n", "null", "none", "~"}
)
_YAML_PLAIN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_./-]*$")


def _format_yaml_scalar(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("Cannot serialize non-finite float to YAML")
        return repr(value)
    text = str(value)
    if _YAML_PLAIN_RE.match(text) and text.lower() not in _YAML_RESERVED_LOWER:
        return text
    return json.dumps(text, ensure_ascii=False)


def _format_yaml_key(key: Any) -> str:
    if not isinstance(key, str):
        key = str(key)
    if _YAML_PLAIN_RE.match(key) and key.lower() not in _YAML_RESERVED_LOWER:
        return key
    return json.dumps(key, ensure_ascii=False)


def _dump_yaml_lines(value: Any, indent: int) -> list[str]:
    prefix = " " * indent
    if isinstance(value, Mapping):
        if not value:
            return [f"{prefix}{{}}"]
        lines: list[str] = []
        for k, v in value.items():
            key_str = _format_yaml_key(k)
            if isinstance(v, Mapping):
                if not v:
                    lines.append(f"{prefix}{key_str}: {{}}")
                else:
                    lines.append(f"{prefix}{key_str}:")
                    lines.extend(_dump_yaml_lines(v, indent + 2))
            elif isinstance(v, list):
                if not v:
                    lines.append(f"{prefix}{key_str}: []")
                else:
                    lines.append(f"{prefix}{key_str}:")
                    lines.extend(_dump_yaml_lines(v, indent + 2))
            else:
                lines.append(f"{prefix}{key_str}: {_format_yaml_scalar(v)}")
        return lines
    if isinstance(value, list):
        if not value:
            return [f"{prefix}[]"]
        lines = []
        for item in value:
            if isinstance(item, Mapping):
                if not item:
                    lines.append(f"{prefix}- {{}}")
                else:
                    first = True
                    for k, v in item.items():
                        key_str = _format_yaml_key(k)
                        lead = f"{prefix}- " if first else f"{prefix}  "
                        first = False
                        if isinstance(v, Mapping):
                            if not v:
                                lines.append(f"{lead}{key_str}: {{}}")
                            else:
                                lines.append(f"{lead}{key_str}:")
                                lines.extend(_dump_yaml_lines(v, indent + 4))
                        elif isinstance(v, list):
                            if not v:
                                lines.append(f"{lead}{key_str}: []")
                            else:
                                lines.append(f"{lead}{key_str}:")
                                lines.extend(_dump_yaml_lines(v, indent + 4))
                        else:
                            lines.append(f"{lead}{key_str}: {_format_yaml_scalar(v)}")
            elif isinstance(item, list):
                if not item:
                    lines.append(f"{prefix}- []")
                else:
                    lines.append(f"{prefix}-")
                    lines.extend(_dump_yaml_lines(item, indent + 2))
            else:
                lines.append(f"{prefix}- {_format_yaml_scalar(item)}")
        return lines
    return [f"{prefix}{_format_yaml_scalar(value)}"]


def to_yaml(data: Any) -> str:
    """Deterministically serialize a JSON-compatible Python structure into valid YAML 1.2."""
    return "\n".join(_dump_yaml_lines(data, 0)) + "\n"


def _security_requirements(rule: RouteRule) -> list[dict[str, list[str]]]:
    if rule.access == "public":
        return []
    if rule.access == "read":
        return [{"OAuth2": ["readonly"]}, {"BearerAuth": []}, {"SessionCookie": []}]
    if rule.access == "write":
        return [{"OAuth2": ["write"]}, {"BearerAuth": []}, {"SessionCookie": []}]
    if rule.access == "admin" and rule.step_up:
        return [{"SessionCookie": []}]
    return [{"BearerAuth": []}, {"SessionCookie": []}]


def _param_schema(param: V1ParamSpec) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": param.schema_type}
    if param.enum:
        schema["enum"] = list(param.enum)
    if param.default is not None:
        schema["default"] = param.default
    return schema


def _parameter_object(param: V1ParamSpec) -> dict[str, Any]:
    if param.name in RETIRED_PARAMETER_NAMES:
        raise ValueError(f"Retired parameter {param.name!r} cannot be emitted in OpenAPI spec")
    obj: dict[str, Any] = {
        "name": param.name,
        "in": param.location,
        "required": True if param.location == "path" else param.required,
        "schema": _param_schema(param),
    }
    if param.description:
        obj["description"] = param.description
    return obj


def _schema_ref(model: type[BaseModel]) -> dict[str, str]:
    return {"$ref": f"#/components/schemas/{model.__name__}"}


def _error_response_entry(status_code: int) -> dict[str, Any]:
    descriptions = {
        400: "Invalid request parameters or JSON body.",
        401: "Authentication required or invalid credential.",
        403: "Insufficient scope or knowledge base capability.",
        404: "Resource not found or inaccessible.",
        409: "Resource state conflict.",
        422: "Request payload failed schema validation.",
        429: "Rate or concurrency limit exceeded.",
        503: "Service unavailable.",
    }
    return {
        "description": descriptions.get(status_code, "Error response."),
        "content": {
            "application/json": {
                "schema": _schema_ref(ErrorEnvelope),
            }
        },
    }


def _collect_component_schemas(specs: tuple[V1RouteSpec, ...]) -> dict[str, Any]:
    models: list[tuple[type[BaseModel], Literal["validation", "serialization"]]] = [(ErrorEnvelope, "validation")]
    seen: set[type[BaseModel]] = {ErrorEnvelope}
    for spec in specs:
        for model in (spec.request_model, spec.response_model):
            if model is not None and model not in seen:
                seen.add(model)
                models.append((model, "validation"))
    _, top = models_json_schema(models, ref_template="#/components/schemas/{model}")
    defs = dict(top.get("$defs") or {})
    return {name: defs[name] for name in sorted(defs)}


def generate_v1_openapi_spec(
    *,
    route_specs: tuple[V1RouteSpec, ...] = V1_ROUTE_SPECS,
    route_policy: Mapping[RouteKey, RouteRule] = V1_ROUTE_POLICY,
) -> dict[str, Any]:
    """Generate the OpenAPI 3.1.0 specification dictionary from v1 route metadata and ROUTE_POLICY."""
    paths: dict[str, dict[str, Any]] = {}
    used_tags: set[str] = set()

    for spec in route_specs:
        if not spec.tag:
            raise ValueError(f"Operation {spec.method} {spec.path} has no tag")
        rule = route_policy.get((spec.method, spec.path))
        if rule is None:
            raise ValueError(f"Route ({spec.method!r}, {spec.path!r}) missing from route_policy")
        if rule.access != spec.access or rule.capability != spec.capability or rule.step_up != spec.step_up:
            raise ValueError(f"ROUTE_POLICY drift on {spec.method} {spec.path}")

        used_tags.add(spec.tag)
        responses: dict[str, Any] = {}
        success_content: dict[str, Any]
        if spec.response_model is not None:
            success_content = {
                spec.response_media_type: {
                    "schema": _schema_ref(spec.response_model),
                }
            }
        elif spec.response_media_type == "application/octet-stream":
            success_content = {
                "application/octet-stream": {
                    "schema": {"type": "string", "format": "binary"},
                }
            }
        else:
            success_content = {
                spec.response_media_type: {
                    "schema": {"type": "string"},
                }
            }
        responses[str(spec.response_status)] = {
            "description": spec.summary,
            "content": success_content,
        }
        for err_status in spec.extra_statuses:
            responses[str(err_status)] = _error_response_entry(err_status)

        operation: dict[str, Any] = {
            "tags": [spec.tag],
            "summary": spec.summary,
            "description": spec.description,
            "operationId": spec.operation_id,
            "x-claire-access": rule.access,
            "x-claire-surface": rule.surface,
            "x-claire-capability": rule.capability,
            "security": _security_requirements(rule),
            "responses": responses,
        }
        if rule.step_up:
            operation["x-claire-step-up"] = True
        if spec.params:
            operation["parameters"] = [_parameter_object(p) for p in spec.params]
        if spec.request_model is not None:
            operation["requestBody"] = {
                "required": True,
                "content": {
                    "application/json": {
                        "schema": _schema_ref(spec.request_model),
                    }
                },
            }
        paths.setdefault(spec.path, {})[spec.method.lower()] = operation

    tags = [
        {"name": name, "description": TAG_DESCRIPTIONS.get(name, name)}
        for name in TAG_DESCRIPTIONS
        if name in used_tags
    ]
    for extra_tag in sorted(used_tags - set(TAG_DESCRIPTIONS)):
        tags.append({"name": extra_tag, "description": extra_tag})

    return {
        "openapi": OPENAPI_VERSION,
        "info": {
            "title": API_TITLE,
            "version": API_VERSION,
            "description": MCP_DESCRIPTION,
            "license": {
                "name": "MIT",
                "url": "https://opensource.org/licenses/MIT",
            },
        },
        "servers": [
            {
                "url": "/",
                "description": "Current host root",
            }
        ],
        "tags": tags,
        "paths": paths,
        "components": {
            "schemas": _collect_component_schemas(route_specs),
            "securitySchemes": _SECURITY_SCHEMES,
        },
    }


def generate_v1_openapi_json(
    *,
    route_specs: tuple[V1RouteSpec, ...] = V1_ROUTE_SPECS,
    route_policy: Mapping[RouteKey, RouteRule] = V1_ROUTE_POLICY,
    indent: int = 2,
) -> str:
    """Serialize the generated OpenAPI 3.1.0 spec to deterministic JSON."""
    spec = generate_v1_openapi_spec(route_specs=route_specs, route_policy=route_policy)
    return json.dumps(spec, indent=indent, ensure_ascii=False) + "\n"


def generate_v1_openapi_yaml(
    *,
    route_specs: tuple[V1RouteSpec, ...] = V1_ROUTE_SPECS,
    route_policy: Mapping[RouteKey, RouteRule] = V1_ROUTE_POLICY,
) -> str:
    """Serialize the generated OpenAPI 3.1.0 spec to deterministic YAML."""
    spec = generate_v1_openapi_spec(route_specs=route_specs, route_policy=route_policy)
    return to_yaml(spec)
