"""Unit tests for `/api/v1/*` foundation (`API-1`): schemas, errors, pagination, and OpenAPI 3.1."""

from __future__ import annotations

import base64
import json
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import BaseModel, ValidationError
from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from claire.accounts import provider_policy as policy
from claire.api import security
from claire.api.v1 import (
    V1_ROUTE_POLICY,
    V1_ROUTE_SPECS,
    generate_v1_openapi_json,
    generate_v1_openapi_spec,
    generate_v1_openapi_yaml,
    to_yaml,
)
from claire.api.v1.errors import V1Error, error_payload, exception_to_v1_response, not_found_error, v1_endpoint
from claire.api.v1.pagination import (
    decode_cursor,
    decode_document_cursor,
    decode_number_cursor,
    encode_cursor,
    encode_document_cursor,
    encode_number_cursor,
    paginated_payload,
    parse_limit,
)
from claire.api.v1.schemas import (
    CreateKnowledgeBaseRequest,
    ErrorEnvelope,
    GraphResponse,
    HealthResponse,
    IngestRequest,
    SearchRequest,
)


def test_schemas_enforce_sealed_strict_config_and_canonical_terms() -> None:
    health = HealthResponse(ok=True, database="ok")
    assert health.model_dump() == {"ok": True, "api_version": "v1", "database": "ok"}

    with pytest.raises(ValidationError):
        HealthResponse.model_validate({"ok": "yes", "database": "ok"})

    with pytest.raises(ValidationError):
        HealthResponse.model_validate({"ok": True, "database": "ok", "extra_field": 1})

    with pytest.raises(ValidationError):
        CreateKnowledgeBaseRequest.model_validate({"label": "Test", "knowledge_type": "real", "theme": "legacy"})

    with pytest.raises(ValidationError):
        SearchRequest.model_validate({"query": "vSAN", "limit": True})

    with pytest.raises(ValidationError):
        IngestRequest.model_validate({"payload": "https://example.test", "extraction_mode": "invalid"})

    graph = GraphResponse.model_validate(
        {
            "kb_number": None,
            "entities": [{"id": "ent_1", "name": "vSAN", "type": "Tech", "sources": ["doc_1"], "observation_preview": "obs", "created_at": 1.0, "degree": 1}],
            "relations": [{"id": "e0", "source_id": "ent_1", "target_id": "ent_2", "type": "uses", "provisional": False}],
            "stats": {"entities": 1, "relations": 1, "max_degree": 1},
        }
    )
    dumped = graph.model_dump()
    assert "entities" in dumped and "relations" in dumped
    assert "nodes" not in dumped and "edges" not in dumped and "links" not in dumped


def test_error_envelope_and_starlette_boundary_interface() -> None:
    payload = error_payload("not_found", "Missing item", request_id="req_123")
    validated = ErrorEnvelope.model_validate(payload)
    assert validated.error.code == "not_found"
    assert "details" not in payload["error"]

    @v1_endpoint
    async def _fail_v1(_request: Request) -> JSONResponse:
        raise not_found_error("Hidden resource")

    @v1_endpoint
    async def _fail_http(_request: Request) -> JSONResponse:
        raise HTTPException(status_code=404, detail="secret internal hint")

    @v1_endpoint
    async def _fail_policy(_request: Request) -> JSONResponse:
        raise policy.ProviderNotConfigured("missing key", remedy="Add key")

    @v1_endpoint
    async def _fail_json(_request: Request) -> JSONResponse:
        raise security.InvalidJSONBody("content-type must be application/json")

    @v1_endpoint
    async def _fail_validation(_request: Request) -> JSONResponse:
        class _Probe(BaseModel):
            count: int

        _Probe.model_validate({"count": "bad"})
        return JSONResponse({})

    app = Starlette(
        routes=[
            Route("/v1", _fail_v1),
            Route("/http", _fail_http),
            Route("/policy", _fail_policy),
            Route("/json", _fail_json),
            Route("/validation", _fail_validation),
        ]
    )
    client = TestClient(app)

    r_v1 = client.get("/v1")
    assert r_v1.status_code == 404
    assert r_v1.json() == {"error": {"code": "not_found", "message": "Hidden resource", "request_id": "-"}}

    r_http = client.get("/http")
    assert r_http.status_code == 404
    assert r_http.json()["error"]["code"] == "not_found"
    assert "secret internal hint" not in r_http.text

    r_policy = client.get("/policy")
    assert r_policy.status_code == 409
    assert r_policy.json()["error"]["code"] == policy.ProviderNotConfigured.code

    r_json = client.get("/json")
    assert r_json.status_code == 400
    assert r_json.json()["error"]["code"] == "invalid_json"

    r_val = client.get("/validation")
    assert r_val.status_code == 422
    body = r_val.json()
    assert body["error"]["code"] == "validation_error"
    assert isinstance(body["error"]["details"]["errors"], list)

    fake_req = Request({"type": "http", "method": "GET", "path": "/", "headers": [], "state": {"claire_request_id": "rid-9"}})
    resp = exception_to_v1_response(fake_req, V1Error(400, "invalid_kb", "Bad KB"))
    assert resp.status_code == 400
    assert json.loads(bytes(resp.body))["error"]["request_id"] == "rid-9"


def test_pagination_cursor_io_round_trip() -> None:
    raw_tok = encode_cursor({"k": "v", "n": 42})
    assert "=" not in raw_tok
    assert decode_cursor(raw_tok) == {"k": "v", "n": 42}

    num_tok = encode_number_cursor(72057594037927936)
    assert decode_number_cursor(num_tok) == 72057594037927936

    doc_tok = encode_document_cursor(1732412345.25, "doc_abc:1")
    assert decode_document_cursor(doc_tok) == (1732412345.25, "doc_abc:1")

    doc_none_tok = encode_document_cursor(None, "doc_null")
    assert decode_document_cursor(doc_none_tok) == (None, "doc_null")

    list_cursor = base64.urlsafe_b64encode(b"[]").decode("ascii").rstrip("=")
    for bad in ("", "!!!", list_cursor):
        with pytest.raises(ValueError, match="invalid_cursor"):
            decode_cursor(bad)
    for bad_num in ("e30", encode_cursor({"n": "bad"})):
        with pytest.raises(ValueError, match="invalid_cursor"):
            decode_number_cursor(bad_num)
    for bad_doc in ("e30", encode_cursor({"ts": True, "id": "doc_1"})):
        with pytest.raises(ValueError, match="invalid_cursor"):
            decode_document_cursor(bad_doc)

    assert parse_limit(None, default=50, maximum=100) == 50
    assert parse_limit("25", default=50, maximum=100) == 25
    for bad_limit in ("0", "-1", "101", "abc", "1.5"):
        with pytest.raises(ValueError, match="invalid_limit"):
            parse_limit(bad_limit, default=50, minimum=1, maximum=100)

    page = paginated_payload([{"id": "1"}], next_cursor=doc_tok, total=5)
    assert page == {"items": [{"id": "1"}], "next_cursor": doc_tok, "total": 5}


def test_openapi_3_1_generation_parity_and_system_pyyaml_validity() -> None:
    spec = generate_v1_openapi_spec()
    assert spec["openapi"] == "3.1.0"
    assert "/mcp" in spec["info"]["description"]

    schemes = spec["components"]["securitySchemes"]
    assert set(schemes.keys()) == {"OAuth2", "BearerAuth", "SessionCookie"}
    oauth_flow = schemes["OAuth2"]["flows"]["authorizationCode"]
    assert oauth_flow["authorizationUrl"] == "/oauth/authorize"
    assert oauth_flow["tokenUrl"] == "/oauth/token"
    assert oauth_flow["x-usePkce"] == "SHA-256"
    assert set(oauth_flow["scopes"].keys()) == {"readonly", "write"}

    untagged = 0
    retired_params_found: list[str] = []
    for path, path_item in spec["paths"].items():
        for method, op in path_item.items():
            if not op.get("tags"):
                untagged += 1
            rule = V1_ROUTE_POLICY[(method.upper(), path)]
            assert op["x-claire-access"] == rule.access
            assert op["x-claire-surface"] == rule.surface
            assert op["x-claire-capability"] == rule.capability
            if rule.step_up:
                assert op["x-claire-step-up"] is True
            for param in op.get("parameters", []):
                if param["name"] == "theme":
                    retired_params_found.append(f"{method} {path}")

    assert untagged == 0
    assert retired_params_found == []
    assert len(spec["paths"]) == len({s.path for s in V1_ROUTE_SPECS})

    # Verify canonical terminology across generated schemas.
    schema_names = set(spec["components"]["schemas"].keys())
    assert "GraphResponse" in schema_names
    assert not any("Node" in name or "Link" in name for name in schema_names)
    graph_props = set(spec["components"]["schemas"]["GraphResponse"]["properties"].keys())
    assert graph_props == {"kb_number", "entities", "relations", "stats"}

    # Verify zero-dependency YAML serializer produces valid YAML matching JSON using system PyYAML.
    json_text = generate_v1_openapi_json()
    yaml_text = generate_v1_openapi_yaml()
    assert to_yaml({"a": "true", "b": 1, "c": ["x: y", None, False]})

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
    assert sys.version_info >= (3, 12)
