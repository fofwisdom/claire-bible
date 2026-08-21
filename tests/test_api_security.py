"""Starlette 앱 바깥의 웹 보안 계약."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from http.cookies import SimpleCookie
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from claire.api import security
from claire.store import db as dbm

OWNER_TOKEN = "owner-" + "o" * 32
READONLY_TOKEN = "readonly-" + "r" * 32
DEV_HOST = "192.0.2.10:8766"
DEV_ORIGIN = "http://192.0.2.10:8766"
CROSS_ORIGIN = "http://ui.example.test:5173"


def _settings(
    tmp_path: Path,
    *,
    environment: str = "development",
    public_url: str = DEV_ORIGIN,
    cors_allowed_origins: str = CROSS_ORIGIN,
    inject_token: str = OWNER_TOKEN,
    readonly_token: str = READONLY_TOKEN,
    anonymous_readonly: bool = False,
) -> SimpleNamespace:
    return SimpleNamespace(
        environment=environment,
        public_url=public_url,
        cors_allowed_origins=cors_allowed_origins,
        inject_token=inject_token,
        readonly_token=readonly_token,
        anonymous_readonly=anonymous_readonly,
        db_file=tmp_path / "claire.db",
    )


async def _endpoint(request: Request) -> JSONResponse:
    payload = None
    if request.url.path == "/search":
        payload = await security.read_json_body(request)
    return JSONResponse(
        {
            "scope": security.request_auth_scope(request),
            "path": request.url.path,
            "payload": payload,
        }
    )


def _inner_app() -> Starlette:
    return Starlette(
        routes=[
            Route("/", _endpoint, methods=["GET"]),
            Route("/health", _endpoint, methods=["GET"]),
            Route("/p", _endpoint, methods=["GET"]),
            Route("/image", _endpoint, methods=["GET"]),
            Route("/graph", _endpoint, methods=["GET"]),
            Route("/whoami", _endpoint, methods=["GET"]),
            Route("/search", _endpoint, methods=["POST"]),
            Route("/ingest", _endpoint, methods=["POST"]),
            Route("/document/seen", _endpoint, methods=["POST"]),
            Route("/dedup/scan", _endpoint, methods=["POST"]),
        ]
    )


@dataclass
class _Result:
    status: int
    headers: list[tuple[bytes, bytes]]
    body: bytes

    def header_values(self, name: str) -> list[str]:
        wanted = name.lower().encode("ascii")
        return [
            value.decode("latin-1")
            for key, value in self.headers
            if key.lower() == wanted
        ]

    def header(self, name: str) -> str | None:
        values = self.header_values(name)
        return values[-1] if values else None

    def json(self) -> dict:
        return json.loads(self.body)


async def _call(
    app,
    path: str,
    *,
    method: str = "GET",
    query: str = "",
    headers: Iterable[tuple[str, str]] = (),
    body: bytes = b"",
    default_host: bool = True,
) -> _Result:
    raw_headers = [
        (name.lower().encode("ascii"), value.encode("latin-1"))
        for name, value in headers
    ]
    if default_host and not any(name == b"host" for name, _ in raw_headers):
        raw_headers.insert(0, (b"host", DEV_HOST.encode("ascii")))
    messages: list[dict] = []
    received = False

    async def receive() -> dict:
        nonlocal received
        if not received:
            received = True
            return {"type": "http.request", "body": body, "more_body": False}
        return {"type": "http.disconnect"}

    async def send(message: dict) -> None:
        messages.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.4"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": query.encode("ascii"),
        "root_path": "",
        "headers": raw_headers,
        "client": ("198.51.100.20", 50000),
        "server": ("192.0.2.10", 8766),
        "state": {},
    }
    await app(scope, receive, send)
    start = next(message for message in messages if message["type"] == "http.response.start")
    response_body = b"".join(
        message.get("body", b"")
        for message in messages
        if message["type"] == "http.response.body"
    )
    return _Result(start["status"], list(start.get("headers", [])), response_body)


def test_runtime_config_validates_environment_url_origins_and_cookie_mode(tmp_path):
    dev = security.WebRuntimeConfig.from_settings(_settings(tmp_path))
    assert dev.environment == "development"
    assert dev.expected_authority == DEV_HOST
    assert dev.public_origin == DEV_ORIGIN
    assert dev.cors_allowed_origins == {CROSS_ORIGIN}
    assert dev.secure_cookie is False
    assert dev.anonymous_readonly is False

    anonymous = security.WebRuntimeConfig.from_settings(
        _settings(tmp_path, anonymous_readonly=True)
    )
    assert anonymous.anonymous_readonly is True

    legacy_settings = _settings(tmp_path)
    del legacy_settings.anonymous_readonly
    assert (
        security.WebRuntimeConfig.from_settings(legacy_settings).anonymous_readonly
        is False
    )

    prod = security.WebRuntimeConfig.from_settings(
        _settings(
            tmp_path,
            environment="production",
            public_url="https://claire.example.test",
            cors_allowed_origins="https://client.example.test",
        )
    )
    assert prod.expected_authority == "claire.example.test"
    assert prod.secure_cookie is True

    default_port = security.WebRuntimeConfig.from_settings(
        _settings(
            tmp_path,
            environment="production",
            public_url="https://claire.example.test:443/",
            cors_allowed_origins="https://client.example.test:443",
        )
    )
    assert default_port.expected_authority == "claire.example.test"
    assert default_port.public_origin == "https://claire.example.test"
    assert default_port.cors_allowed_origins == {"https://client.example.test"}


@pytest.mark.parametrize(
    ("change", "match"),
    [
        ({"environment": ""}, "CLAIRE_ENVIRONMENT"),
        ({"environment": "staging"}, "CLAIRE_ENVIRONMENT"),
        ({"environment": " Development "}, "canonical lowercase"),
        ({"public_url": "https://192.0.2.10"}, "development public URL"),
        ({"public_url": "http://192.0.2.10:0"}, "port"),
        (
            {
                "environment": "production",
                "public_url": "http://claire.example.test",
                "cors_allowed_origins": "",
            },
            "must use https",
        ),
        ({"inject_token": ""}, "CLAIRE_INJECT_TOKEN"),
        ({"inject_token": "short"}, "32-128"),
        ({"inject_token": "x" * 31 + "!"}, "URL-safe"),
        ({"inject_token": f" {OWNER_TOKEN}"}, "CLAIRE_INJECT_TOKEN"),
        ({"readonly_token": "short"}, "32-128"),
        ({"readonly_token": f"{READONLY_TOKEN} "}, "CLAIRE_READONLY_TOKEN"),
        ({"readonly_token": OWNER_TOKEN}, "must be different"),
        ({"anonymous_readonly": "true"}, "CLAIRE_ANONYMOUS_READONLY"),
        ({"cors_allowed_origins": "*"}, "wildcard"),
        ({"cors_allowed_origins": f"{CROSS_ORIGIN}/"}, "trailing slash"),
    ],
)
def test_runtime_config_rejects_unsafe_values(tmp_path, change, match):
    values = vars(_settings(tmp_path)).copy()
    values.update(change)
    with pytest.raises(ValueError, match=match):
        security.WebRuntimeConfig.from_settings(SimpleNamespace(**values))


def test_route_policy_is_exact_method_path_matrix_with_explicit_head():
    public_get = {
        "/health",
        "/favicon.ico",
        "/favicon.svg",
        "/apple-touch-icon.png",
        "/apple-touch-icon-precomposed.png",
        "/manifest.json",
        "/site.webmanifest",
        "/browserconfig.xml",
        "/icon",
        "/p",
        "/image",
    }
    read_get = {"/", "/whoami", "/stats", "/graph", "/node", "/documents", "/document", "/mcp"}
    read_post = {"/search", "/mcp"}
    owner_post = {
        "/ingest",
        "/ingest-stream",
        "/document/seen",
        "/document/pin",
        "/document/hide",
        "/document/title",
        "/synthesize",
        "/research",
        "/dedup/scan",
        "/dedup/merge",
        "/share",
    }
    expected = {
        **{
            (method, path): "public"
            for path in public_get
            for method in ("GET", "HEAD")
        },
        **{
            (method, path): "read"
            for path in read_get
            for method in ("GET", "HEAD")
        },
        **{("POST", path): "read" for path in read_post},
        **{("POST", path): "owner" for path in owner_post},
    }

    assert {
        key: rule.access for key, rule in security.ROUTE_POLICY.items()
    } == expected
    assert len(security.ROUTE_POLICY) == len(expected)


@pytest.mark.asyncio
async def test_host_is_exact_and_forwarded_headers_are_ignored(tmp_path):
    app = security.wrap_web_app(_inner_app(), _settings(tmp_path))

    assert (await _call(app, "/health")).status == 200
    assert (
        await _call(app, "/health", headers=[("Host", "192.0.2.11:8766")])
    ).status == 421
    assert (
        await _call(
            app,
            "/health",
            headers=[
                ("Host", "192.0.2.11:8766"),
                ("X-Forwarded-Host", DEV_HOST),
            ],
        )
    ).status == 421


@pytest.mark.asyncio
async def test_missing_duplicate_and_malformed_host_are_400(tmp_path):
    app = security.wrap_web_app(_inner_app(), _settings(tmp_path))

    assert (await _call(app, "/health", default_host=False)).status == 400
    duplicate = await _call(
        app,
        "/health",
        headers=[("Host", DEV_HOST), ("Host", DEV_HOST)],
        default_host=False,
    )
    assert duplicate.status == 400
    for host in ("bad host", "user@example.test", "192.0.2.10:70000", "example.test."):
        assert (
            await _call(app, "/health", headers=[("Host", host)], default_host=False)
        ).status == 400


@pytest.mark.asyncio
async def test_public_read_and_owner_route_policy(tmp_path):
    app = security.wrap_web_app(_inner_app(), _settings(tmp_path))

    public = await _call(app, "/health")
    assert public.status == 200 and public.json()["scope"] == "public"
    unauthenticated = await _call(app, "/graph")
    assert unauthenticated.status == 404
    assert unauthenticated.header("cache-control") == "no-store"

    owner_headers = [("Authorization", f"Bearer {OWNER_TOKEN}")]
    readonly_headers = [("Authorization", f"Bearer {READONLY_TOKEN}")]
    owner_read = await _call(app, "/graph", headers=owner_headers)
    readonly_read = await _call(app, "/graph", headers=readonly_headers)
    assert owner_read.status == 200 and owner_read.json()["scope"] == "owner"
    assert readonly_read.status == 200 and readonly_read.json()["scope"] == "readonly"
    assert owner_read.header("cache-control") == "no-store"
    assert readonly_read.header("cache-control") == "no-store"
    assert public.header("cache-control") is None
    assert (await _call(app, "/ingest", method="POST", headers=readonly_headers)).status == 404
    assert (await _call(app, "/ingest", method="POST", headers=owner_headers)).status == 200
    assert (
        await _call(app, "/document/seen", method="POST", headers=owner_headers)
    ).status == 200
    assert (
        await _call(app, "/dedup/scan", method="POST", headers=owner_headers)
    ).status == 200
    assert (
        await _call(app, "/graph", headers=[("X-Token", OWNER_TOKEN)])
    ).status == 404
    assert all(path != "/auth/request" for _method, path in security.ROUTE_POLICY)
    assert all(path != "/auth/poll" for _method, path in security.ROUTE_POLICY)


@pytest.mark.asyncio
async def test_anonymous_readonly_allows_only_exact_read_pairs_same_or_no_origin(
    tmp_path,
):
    app = security.wrap_web_app(
        _inner_app(),
        _settings(tmp_path, anonymous_readonly=True),
    )

    no_origin = await _call(app, "/graph")
    same_origin = await _call(
        app,
        "/whoami",
        headers=[("Origin", DEV_ORIGIN)],
    )
    head = await _call(app, "/graph", method="HEAD")
    search = await _call(
        app,
        "/search",
        method="POST",
        headers=[("Content-Type", "application/json")],
        body=b'{"query":"anonymous"}',
    )
    unrelated_cookie = await _call(
        app,
        "/graph",
        headers=[("Cookie", "theme=dark")],
    )

    assert no_origin.status == 200
    assert no_origin.json()["scope"] == "anonymous"
    assert no_origin.header("cache-control") == "no-store"
    assert same_origin.status == 200
    assert same_origin.json()["scope"] == "anonymous"
    assert head.status == 200
    assert head.header("cache-control") == "no-store"
    assert search.status == 200
    assert search.json()["scope"] == "anonymous"
    assert search.json()["payload"] == {"query": "anonymous"}
    assert unrelated_cookie.status == 200
    assert unrelated_cookie.json()["scope"] == "anonymous"

    assert (await _call(app, "/ingest", method="POST")).status == 404
    assert (
        await _call(app, "/document/seen", method="POST")
    ).status == 404


@pytest.mark.asyncio
async def test_anonymous_readonly_never_replaces_valid_owner_or_readonly_scope(
    tmp_path,
):
    app = security.wrap_web_app(
        _inner_app(),
        _settings(tmp_path, anonymous_readonly=True),
    )
    owner_headers = [("Authorization", f"Bearer {OWNER_TOKEN}")]
    readonly_headers = [("Authorization", f"Bearer {READONLY_TOKEN}")]

    owner = await _call(app, "/graph", headers=owner_headers)
    readonly = await _call(app, "/graph", headers=readonly_headers)
    owner_write = await _call(
        app,
        "/ingest",
        method="POST",
        headers=owner_headers,
    )

    assert owner.status == 200 and owner.json()["scope"] == "owner"
    assert readonly.status == 200 and readonly.json()["scope"] == "readonly"
    assert owner_write.status == 200
    assert owner_write.json()["scope"] == "owner"


@pytest.mark.asyncio
async def test_anonymous_readonly_cross_origin_still_requires_valid_bearer(tmp_path):
    app = security.wrap_web_app(
        _inner_app(),
        _settings(tmp_path, anonymous_readonly=True),
    )
    origin = [("Origin", CROSS_ORIGIN)]

    assert (await _call(app, "/graph", headers=origin)).status == 403
    assert (
        await _call(
            app,
            "/search",
            method="POST",
            headers=[*origin, ("Content-Type", "application/json")],
            body=b'{"query":"cross"}',
        )
    ).status == 403

    bearer = await _call(
        app,
        "/graph",
        headers=[
            *origin,
            ("Authorization", f"Bearer {READONLY_TOKEN}"),
        ],
    )
    assert bearer.status == 200
    assert bearer.json()["scope"] == "readonly"
    assert bearer.header("access-control-allow-origin") == CROSS_ORIGIN

    head_preflight = await _call(
        app,
        "/graph",
        method="OPTIONS",
        headers=[
            *origin,
            ("Access-Control-Request-Method", "HEAD"),
            ("Access-Control-Request-Headers", "Authorization"),
        ],
    )
    assert head_preflight.status == 204
    assert head_preflight.header("access-control-allow-methods") == "GET, HEAD, POST"

    bearer_head = await _call(
        app,
        "/graph",
        method="HEAD",
        headers=[
            *origin,
            ("Authorization", f"Bearer {READONLY_TOKEN}"),
        ],
    )
    assert bearer_head.status == 200
    assert bearer_head.header("access-control-allow-origin") == CROSS_ORIGIN


@pytest.mark.asyncio
async def test_anonymous_readonly_does_not_fallback_from_credential_material(
    tmp_path,
    monkeypatch,
):
    async def reject_session(_config, _token):
        return None

    monkeypatch.setattr(security, "_validate_session", reject_session)
    app = security.wrap_web_app(
        _inner_app(),
        _settings(tmp_path, anonymous_readonly=True),
    )
    unknown_bearer = "unknown-" + ("u" * 32)
    unknown_session = "s" * 43
    cases = [
        [("Authorization", f"Bearer {unknown_bearer}")],
        [("Authorization", "Basic abc")],
        [
            ("Authorization", f"Bearer {OWNER_TOKEN}"),
            ("Authorization", f"Bearer {OWNER_TOKEN}"),
        ],
        [("Cookie", "claire_session=")],
        [("Cookie", f"claire_session={unknown_session}")],
        [("Cookie", "claire_session=a; claire_session=b")],
        [
            ("Authorization", f"Bearer {OWNER_TOKEN}"),
            ("Cookie", f"claire_session={unknown_session}"),
        ],
        [("X-Token", OWNER_TOKEN)],
        [("X-Session", unknown_session)],
    ]

    for credential_headers in cases:
        no_origin = await _call(app, "/graph", headers=credential_headers)
        same_origin = await _call(
            app,
            "/graph",
            headers=[*credential_headers, ("Origin", DEV_ORIGIN)],
        )
        cross_origin = await _call(
            app,
            "/graph",
            headers=[*credential_headers, ("Origin", CROSS_ORIGIN)],
        )
        assert no_origin.status == 404
        assert same_origin.status == 404
        assert cross_origin.status == 403


@pytest.mark.asyncio
async def test_complete_scope_origin_read_and_owner_permission_matrix(
    tmp_path,
    monkeypatch,
):
    owner_session = "s" * 43
    readonly_session = "r" * 43

    async def validate(_config, token):
        return {
            owner_session: "owner",
            readonly_session: "readonly",
        }.get(token)

    monkeypatch.setattr(security, "_validate_session", validate)
    app = security.wrap_web_app(
        _inner_app(),
        _settings(tmp_path, anonymous_readonly=True),
    )
    credential_headers = {
        "owner_bearer": [("Authorization", f"Bearer {OWNER_TOKEN}")],
        "readonly_bearer": [("Authorization", f"Bearer {READONLY_TOKEN}")],
        "owner_session": [("Cookie", f"claire_session={owner_session}")],
        "readonly_session": [("Cookie", f"claire_session={readonly_session}")],
        "anonymous": [],
        "invalid": [("Authorization", f"Bearer {'u' * 43}")],
    }
    origin_headers = {
        "none": [],
        "same": [("Origin", DEV_ORIGIN)],
        "cross": [("Origin", CROSS_ORIGIN)],
    }
    expected_read = {
        "owner_bearer": {"none": 200, "same": 200, "cross": 200},
        "readonly_bearer": {"none": 200, "same": 200, "cross": 200},
        "owner_session": {"none": 200, "same": 200, "cross": 403},
        "readonly_session": {"none": 200, "same": 200, "cross": 403},
        "anonymous": {"none": 200, "same": 200, "cross": 403},
        "invalid": {"none": 404, "same": 404, "cross": 403},
    }
    expected_owner = {
        "owner_bearer": {"none": 200, "same": 200, "cross": 200},
        "readonly_bearer": {"none": 404, "same": 404, "cross": 404},
        "owner_session": {"none": 403, "same": 200, "cross": 403},
        "readonly_session": {"none": 403, "same": 404, "cross": 403},
        "anonymous": {"none": 404, "same": 404, "cross": 403},
        "invalid": {"none": 404, "same": 404, "cross": 403},
    }
    expected_scope = {
        "owner_bearer": "owner",
        "readonly_bearer": "readonly",
        "owner_session": "owner",
        "readonly_session": "readonly",
        "anonymous": "anonymous",
    }

    for identity, credentials in credential_headers.items():
        for origin_kind, origin in origin_headers.items():
            headers = [*credentials, *origin]
            read = await _call(app, "/graph", headers=headers)
            owner = await _call(app, "/ingest", method="POST", headers=headers)
            assert read.status == expected_read[identity][origin_kind], (
                identity,
                origin_kind,
                "read",
            )
            assert owner.status == expected_owner[identity][origin_kind], (
                identity,
                origin_kind,
                "owner",
            )
            if read.status == 200:
                assert read.json()["scope"] == expected_scope[identity]
            if owner.status == 200:
                assert owner.json()["scope"] == "owner"


@pytest.mark.asyncio
async def test_public_routes_still_ignore_credential_material(tmp_path):
    app = security.wrap_web_app(
        _inner_app(),
        _settings(tmp_path, anonymous_readonly=True),
    )
    response = await _call(
        app,
        "/health",
        headers=[
            ("Authorization", "Basic invalid"),
            ("Cookie", "claire_session=invalid"),
            ("X-Token", "legacy"),
            ("X-Session", "legacy"),
        ],
    )

    assert response.status == 200
    assert response.json()["scope"] == "public"
    assert response.header("cache-control") is None


@pytest.mark.asyncio
async def test_unknown_method_path_pairs_fail_closed_before_routing(tmp_path):
    app = security.wrap_web_app(
        _inner_app(),
        _settings(tmp_path, anonymous_readonly=True),
    )
    owner = [("Authorization", f"Bearer {OWNER_TOKEN}")]

    assert (await _call(app, "/health", method="POST")).status == 404
    assert (await _call(app, "/ingest", method="GET", headers=owner)).status == 404
    assert (await _call(app, "/search", method="HEAD", headers=owner)).status == 404
    assert (await _call(app, "/not-registered")).status == 404


@pytest.mark.asyncio
async def test_malformed_duplicate_and_mixed_credentials_are_rejected(
    tmp_path, monkeypatch
):
    full_session = "s" * 43

    async def validate(_config, token):
        return "owner" if token == full_session else None

    monkeypatch.setattr(security, "_validate_session", validate)
    app = security.wrap_web_app(_inner_app(), _settings(tmp_path))
    cookie = ("Cookie", f"claire_session={full_session}")

    malformed = await _call(
        app,
        "/graph",
        headers=[("Authorization", "Basic abc"), cookie],
    )
    assert malformed.status == 404
    duplicate = await _call(
        app,
        "/graph",
        headers=[
            ("Authorization", f"Bearer {OWNER_TOKEN}"),
            ("Authorization", f"Bearer {OWNER_TOKEN}"),
            cookie,
        ],
    )
    assert duplicate.status == 404
    mixed = await _call(
        app,
        "/graph",
        headers=[("Authorization", f"Bearer {OWNER_TOKEN}"), cookie],
    )
    assert mixed.status == 404


@pytest.mark.asyncio
async def test_cors_preflight_and_bearer_only_actual_request(tmp_path):
    app = security.wrap_web_app(_inner_app(), _settings(tmp_path))
    preflight = await _call(
        app,
        "/graph",
        method="OPTIONS",
        headers=[
            ("Origin", CROSS_ORIGIN),
            ("Access-Control-Request-Method", "GET"),
            ("Access-Control-Request-Headers", "Authorization, Content-Type"),
        ],
    )
    assert preflight.status == 204
    assert preflight.header("access-control-allow-origin") == CROSS_ORIGIN
    assert preflight.header("access-control-allow-methods") == "GET, HEAD, POST"
    assert preflight.header("access-control-max-age") == "600"
    assert preflight.header("access-control-allow-credentials") is None

    actual = await _call(
        app,
        "/graph",
        headers=[
            ("Origin", CROSS_ORIGIN),
            ("Authorization", f"Bearer {READONLY_TOKEN}"),
        ],
    )
    assert actual.status == 200
    assert actual.header("access-control-allow-origin") == CROSS_ORIGIN
    assert "Origin" in actual.header_values("vary")
    assert actual.header("access-control-allow-credentials") is None

    mixed = await _call(
        app,
        "/graph",
        headers=[
            ("Origin", CROSS_ORIGIN),
            ("Authorization", f"Bearer {OWNER_TOKEN}"),
            ("Cookie", "claire_session=ambient"),
        ],
    )
    assert mixed.status == 403
    assert mixed.header("access-control-allow-origin") == CROSS_ORIGIN


@pytest.mark.asyncio
async def test_cors_rejects_unknown_origin_method_and_headers(tmp_path):
    app = security.wrap_web_app(_inner_app(), _settings(tmp_path))
    assert (
        await _call(
            app,
            "/graph",
            headers=[
                ("Origin", "http://evil.example.test"),
                ("Authorization", f"Bearer {OWNER_TOKEN}"),
            ],
        )
    ).status == 403
    assert (
        await _call(
            app,
            "/graph",
            method="OPTIONS",
            headers=[
                ("Origin", CROSS_ORIGIN),
                ("Access-Control-Request-Method", "DELETE"),
            ],
        )
    ).status == 403
    assert (
        await _call(
            app,
            "/graph",
            method="OPTIONS",
            headers=[
                ("Origin", CROSS_ORIGIN),
                ("Access-Control-Request-Method", "GET"),
                ("Access-Control-Request-Headers", "X-Token"),
            ],
        )
    ).status == 403


@pytest.mark.asyncio
async def test_public_route_can_be_read_from_allowed_cross_origin_without_bearer(tmp_path):
    app = security.wrap_web_app(_inner_app(), _settings(tmp_path))
    response = await _call(app, "/health", headers=[("Origin", CROSS_ORIGIN)])
    assert response.status == 200
    assert response.header("access-control-allow-origin") == CROSS_ORIGIN


@pytest.mark.asyncio
async def test_cookie_post_requires_exact_same_origin(tmp_path, monkeypatch):
    full_session = "s" * 43

    async def validate(_config, token):
        return "owner" if token == full_session else None

    monkeypatch.setattr(security, "_validate_session", validate)
    app = security.wrap_web_app(_inner_app(), _settings(tmp_path))
    cookie = [("Cookie", f"claire_session={full_session}")]

    assert (
        await _call(app, "/document/seen", method="POST", headers=cookie)
    ).status == 403
    same_origin = await _call(
        app,
        "/document/seen",
        method="POST",
        headers=[*cookie, ("Origin", DEV_ORIGIN)],
    )
    assert same_origin.status == 200
    assert same_origin.json()["scope"] == "owner"
    assert "Max-Age=604800" in same_origin.header("set-cookie")
    assert (await _call(app, "/graph", headers=cookie)).status == 200


@pytest.mark.asyncio
async def test_bootstrap_requires_full_session_and_sets_environment_cookie(
    tmp_path,
):
    dev_settings = _settings(tmp_path)
    conn = dbm.connect(dev_settings.db_file)
    dbm.init_db(conn)
    full_session = dbm.create_session(conn)
    conn.close()
    dev_app = security.wrap_web_app(_inner_app(), dev_settings)

    redirect = await _call(dev_app, "/", query=f"t={full_session}")
    assert redirect.status == 302
    assert redirect.header("location") == "/"
    assert redirect.header_values("cache-control") == ["no-store"]
    assert redirect.header_values("referrer-policy") == ["no-referrer"]
    cookie = redirect.header("set-cookie")
    assert cookie is not None
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie and "Path=/" in cookie
    assert "Secure" not in cookie
    parsed_cookie = SimpleCookie()
    parsed_cookie.load(cookie)
    rotated = parsed_cookie["claire_session"].value
    assert rotated != full_session
    assert dbm.plausible_session_token(rotated)
    refreshed = await _call(
        dev_app,
        "/graph",
        headers=[("Cookie", f"claire_session={rotated}")],
    )
    assert refreshed.status == 200
    assert "Max-Age=604800" in refreshed.header("set-cookie")
    assert (await _call(dev_app, "/", query=f"t={full_session}")).status == 404
    assert (
        await _call(dev_app, "/", query=f"t={full_session[:7]}")
    ).status == 404
    assert (
        await _call(
            dev_app,
            "/",
            query=f"t={full_session}",
            headers=[("Origin", CROSS_ORIGIN)],
        )
    ).status == 403

    prod_settings = _settings(
        tmp_path,
        environment="production",
        public_url="https://claire.example.test",
        cors_allowed_origins="https://client.example.test",
    )
    conn = dbm.connect(prod_settings.db_file)
    prod_session = dbm.create_session(conn)
    conn.close()
    prod_app = security.wrap_web_app(_inner_app(), prod_settings)
    prod_redirect = await _call(
        prod_app,
        "/",
        query=f"t={prod_session}",
        headers=[("Host", "claire.example.test")],
        default_host=False,
    )
    assert prod_redirect.status == 302
    assert "Secure" in prod_redirect.header("set-cookie")

    conn = dbm.connect(dev_settings.db_file)
    readonly_session = dbm.create_session(conn, scope="readonly")
    conn.close()
    first_readonly = await _call(dev_app, "/", query=f"t={readonly_session}")
    second_readonly = await _call(dev_app, "/", query=f"t={readonly_session}")
    assert first_readonly.status == second_readonly.status == 302
    assert readonly_session in first_readonly.header("set-cookie")


@pytest.mark.asyncio
async def test_anonymous_head_bootstrap_is_404_and_does_not_consume_token(tmp_path):
    settings = _settings(tmp_path, anonymous_readonly=True)
    conn = dbm.connect(settings.db_file)
    dbm.init_db(conn)
    bootstrap = dbm.create_session(conn, scope="owner")
    conn.close()
    app = security.wrap_web_app(_inner_app(), settings)

    head = await _call(app, "/", method="HEAD", query=f"t={bootstrap}")
    exchange = await _call(app, "/", query=f"t={bootstrap}")

    assert head.status == 404
    assert head.header("set-cookie") is None
    assert exchange.status == 302
    assert exchange.header("set-cookie") is not None


@pytest.mark.asyncio
async def test_json_body_helper_distinguishes_400_and_413(tmp_path):
    app = security.wrap_web_app(_inner_app(), _settings(tmp_path))
    auth = [("Authorization", f"Bearer {OWNER_TOKEN}")]

    valid = await _call(
        app,
        "/search",
        method="POST",
        headers=[*auth, ("Content-Type", "application/json")],
        body=b'{"query":"x"}',
    )
    assert valid.status == 200 and valid.json()["payload"] == {"query": "x"}
    invalid = await _call(
        app,
        "/search",
        method="POST",
        headers=[*auth, ("Content-Type", "application/json")],
        body=b"[]",
    )
    assert invalid.status == 400
    too_large = await _call(
        app,
        "/search",
        method="POST",
        headers=[
            *auth,
            ("Content-Type", "application/json"),
            ("Content-Length", str(security.MAX_REQUEST_BODY + 1)),
        ],
        body=b"{}",
    )
    assert too_large.status == 413


@pytest.mark.asyncio
async def test_413_keeps_allowed_cors_headers(tmp_path):
    app = security.wrap_web_app(_inner_app(), _settings(tmp_path))
    response = await _call(
        app,
        "/search",
        method="POST",
        headers=[
            ("Origin", CROSS_ORIGIN),
            ("Authorization", f"Bearer {OWNER_TOKEN}"),
            ("Content-Type", "application/json"),
            ("Content-Length", str(security.MAX_REQUEST_BODY + 1)),
        ],
        body=b"{}",
    )
    assert response.status == 413
    assert response.header("access-control-allow-origin") == CROSS_ORIGIN


@pytest.mark.asyncio
async def test_chunked_body_limit_and_bodyless_route_policy(tmp_path):
    app = security.wrap_web_app(_inner_app(), _settings(tmp_path))
    auth = [("Authorization", f"Bearer {OWNER_TOKEN}")]

    bodyless = await _call(
        app,
        "/health",
        headers=[("Transfer-Encoding", "chunked")],
        body=b"x",
    )
    assert bodyless.status == 400

    dedup_body = await _call(
        app,
        "/dedup/scan",
        method="POST",
        headers=[*auth, ("Content-Length", "2")],
        body=b"{}",
    )
    assert dedup_body.status == 400

    chunked = await _call(
        app,
        "/search",
        method="POST",
        headers=[
            *auth,
            ("Content-Type", "application/json"),
            ("Transfer-Encoding", "chunked"),
        ],
        body=b'{"query":"' + (b"x" * security.MAX_REQUEST_BODY) + b'"}',
    )
    assert chunked.status == 413


@pytest.mark.asyncio
async def test_authentication_failure_is_generic_500_without_secret_logs(
    tmp_path,
    monkeypatch,
    caplog,
):
    secret = "private-session-diagnostic"

    async def fail_validation(_config, _token):
        raise RuntimeError(secret)

    monkeypatch.setattr(security, "_validate_session", fail_validation)
    caplog.set_level(logging.WARNING, logger="claire.api.access")
    app = security.wrap_web_app(_inner_app(), _settings(tmp_path))
    response = await _call(
        app,
        "/graph",
        headers=[("Cookie", f"claire_session={'s' * 43}")],
    )

    assert response.status == 500
    assert response.json() == {"error": "internal server error"}
    assert response.header("x-request-id")
    assert response.header("content-security-policy")
    assert secret not in caplog.text
    assert "error_type=RuntimeError" in caplog.text


@pytest.mark.asyncio
async def test_access_log_and_security_headers_never_include_secrets(
    tmp_path, caplog
):
    app = security.wrap_web_app(_inner_app(), _settings(tmp_path))
    query_secret = "share-secret-value"
    referer_secret = "referer-secret-value"
    bearer_secret = "bearer-secret-value"
    caplog.set_level(logging.INFO, logger="claire.api.access")

    response = await _call(
        app,
        "/p",
        query=f"s={query_secret}",
        headers=[
            ("Referer", f"https://outside.example/?t={referer_secret}"),
            ("Authorization", f"Bearer {bearer_secret}"),
        ],
    )
    assert response.status == 200
    text = caplog.text
    assert "path=/p" in text
    assert query_secret not in text
    assert referer_secret not in text
    assert bearer_secret not in text
    assert response.header("x-request-id")
    assert response.header("referrer-policy") == "no-referrer"
    assert response.header("x-content-type-options") == "nosniff"
