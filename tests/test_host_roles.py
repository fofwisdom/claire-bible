"""호스트 역할, 세션 쿠키 경계, 레거시 호스트 (설계 §5.1–§5.3, §15 S11, §18.6).

모든 테스트는 ``tmp_path``만 쓰고 ``CLAIRE_*`` 환경변수를 지운 뒤 실행한다(§18.2-6).
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from http.cookies import SimpleCookie
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Route

from claire.api import security
from claire.api.host_router import (
    HostRoles,
    HostRouter,
    LegacyHost,
    LegacyHostPolicy,
    not_found_app,
)
from claire.store import db as dbm
from claire.store import legacy_hosts as lh

OWNER_TOKEN = "owner-" + "o" * 32
DEV_IP = "192.0.2.10"
DEV_HOST = f"{DEV_IP}:8766"
DEV_ORIGIN = f"http://{DEV_HOST}"
UC_HOST = f"{DEV_IP}:8767"
MEDIA_HOST = f"{DEV_IP}:8768"
PROD_HOST = "claire.example.com"
PROD_ORIGIN = f"https://{PROD_HOST}"

# §13.2. 테이블은 init_db(v16)가 만든다. 같은 DDL을 IF NOT EXISTS로 다시 적어 계약을 고정한다.
LEGACY_HOSTS_DDL = """
CREATE TABLE IF NOT EXISTS legacy_hosts (
    host TEXT PRIMARY KEY,
    theme_id INTEGER NOT NULL REFERENCES themes(id) ON DELETE CASCADE,
    created_at REAL NOT NULL,
    activated_at REAL,
    sunset_at REAL
)
"""


@pytest.fixture(autouse=True)
def _isolate_claire_env(monkeypatch):
    for name in list(os.environ):
        if name.startswith("CLAIRE_"):
            monkeypatch.delenv(name, raising=False)


def _settings(tmp_path: Path, **overrides) -> SimpleNamespace:
    values = dict(
        environment="development",
        public_url=DEV_ORIGIN,
        cors_allowed_origins="",
        inject_token=OWNER_TOKEN,
        readonly_token="",
        anonymous_readonly=False,
        db_file=tmp_path / "claire.db",
        site_domain="",
        usercontent_fqdn="",
        media_fqdn="",
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def _prod_settings(tmp_path: Path, **overrides) -> SimpleNamespace:
    base = dict(environment="production", public_url=PROD_ORIGIN, site_domain="example.com")
    base.update(overrides)
    return _settings(tmp_path, **base)


def _roles(settings: SimpleNamespace) -> HostRoles:
    config = security.WebRuntimeConfig.from_settings(settings)
    assert config.host_roles is not None
    return config.host_roles


# --- ASGI 호출 -------------------------------------------------------------------


@dataclass
class _Result:
    status: int
    headers: list[tuple[bytes, bytes]]
    body: bytes

    def values(self, name: str) -> list[str]:
        wanted = name.lower().encode("ascii")
        return [v.decode("latin-1") for k, v in self.headers if k.lower() == wanted]

    def header(self, name: str) -> str | None:
        found = self.values(name)
        return found[-1] if found else None


async def _call(
    app,
    path: str,
    *,
    host: str | None = DEV_HOST,
    method: str = "GET",
    query: str = "",
    headers: Iterable[tuple[str, str]] = (),
    raw_path: bytes | None = None,
) -> _Result:
    raw_headers = [(n.lower().encode("ascii"), v.encode("latin-1")) for n, v in headers]
    if host is not None:
        raw_headers.insert(0, (b"host", host.encode("ascii")))
    messages: list[dict] = []
    sent = False

    async def receive() -> dict:
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": b"", "more_body": False}
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
        "raw_path": raw_path if raw_path is not None else path.encode("ascii"),
        "query_string": query.encode("latin-1"),
        "root_path": "",
        "headers": raw_headers,
        "client": ("198.51.100.20", 50000),
        "server": (DEV_IP, 8766),
        "state": {},
    }
    await app(scope, receive, send)
    start = next(m for m in messages if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
    return _Result(start["status"], list(start.get("headers", [])), body)


async def _endpoint(request: Request) -> JSONResponse:
    return JSONResponse({"scope": security.request_auth_scope(request), "path": request.url.path})


async def _static_endpoint(request: Request) -> PlainTextResponse:
    return PlainTextResponse("static-body")


def _inner_app() -> Starlette:
    return Starlette(
        routes=[
            Route("/health", _endpoint, methods=["GET"]),
            Route("/graph", _endpoint, methods=["GET"]),
            Route("/admin/accounts", _endpoint, methods=["GET"]),
            Route("/static/js/app.js", _static_endpoint, methods=["GET", "POST"]),
        ]
    )


# --- 1. 역할 호스트 설정 검증 -------------------------------------------------------


def test_development_role_hosts_use_same_ipv4_and_distinct_ports(tmp_path):
    roles = _roles(_settings(tmp_path, usercontent_fqdn=UC_HOST, media_fqdn=MEDIA_HOST))
    assert roles.scheme == "http"
    assert roles.role_of(DEV_HOST) == "app"
    assert roles.role_of(UC_HOST) == "usercontent"
    assert roles.role_of(MEDIA_HOST) == "media"
    assert roles.role_of(f"{DEV_IP}:8769") is None
    assert roles.origin("app") == DEV_ORIGIN
    assert roles.origin("usercontent") == f"http://{UC_HOST}"
    assert roles.origin("media") == f"http://{MEDIA_HOST}"

    app_only = _roles(_settings(tmp_path))
    assert app_only.usercontent is None and app_only.media is None
    assert app_only.origin("usercontent") is None and app_only.origin("media") is None
    assert app_only.role_of(UC_HOST) is None

    for bad in (
        {"usercontent_fqdn": "192.0.2.11:8767"},  # 다른 IPv4
        {"usercontent_fqdn": DEV_HOST},  # app과 같은 포트
        {"usercontent_fqdn": DEV_IP},  # 포트 없음(기본 80)이 app과 다르더라도 형식 위반
        {"usercontent_fqdn": "usercontent.example.com"},  # 개발 환경은 IPv4:포트
        {"usercontent_fqdn": UC_HOST, "media_fqdn": UC_HOST},  # 역할끼리 같음
        {"usercontent_fqdn": f" {UC_HOST}"},
        {"media_fqdn": "0.0.0.0:8768"},
    ):
        with pytest.raises(ValueError):
            _roles(_settings(tmp_path, **bad))


def test_production_role_hosts_require_registered_site_domain(tmp_path):
    roles = _roles(
        _prod_settings(tmp_path, usercontent_fqdn="usercontent.example.com", media_fqdn="Media.Example.com")
    )
    assert roles.scheme == "https"
    assert roles.site_domain == "example.com"
    assert roles.media == "media.example.com"
    assert roles.origin("usercontent") == "https://usercontent.example.com"
    assert roles.role_of("usercontent.example.com") == "usercontent"
    assert roles.role_of("usercontent.example.com:443") is None  # 정확한 authority만

    # 대소문자는 무시한다.
    upper = _roles(_prod_settings(tmp_path, site_domain="Example.COM", usercontent_fqdn="UserContent.example.com"))
    assert (upper.site_domain, upper.usercontent) == ("example.com", "usercontent.example.com")

    # app만 있는 설치는 등록 도메인이 없어도 된다.
    assert _roles(_prod_settings(tmp_path, site_domain="")).usercontent is None

    for bad in (
        {"site_domain": "", "usercontent_fqdn": "usercontent.example.com"},  # 등록 도메인 없음
        {"usercontent_fqdn": "usercontent.example.net"},  # 등록 도메인 밖
        {"site_domain": "example.net"},  # app이 등록 도메인 밖
        {"usercontent_fqdn": PROD_HOST},  # app과 같음
        {"usercontent_fqdn": "example.com"},  # app의 상위 도메인
        {"usercontent_fqdn": "usercontent.example.com:8443"},  # 운영 환경은 포트 없는 host
        {"usercontent_fqdn": "203.0.113.5"},
        {"usercontent_fqdn": "u.example.com", "media_fqdn": "u.example.com"},
        {"site_domain": "203.0.113.5"},
        {"site_domain": "example.com."},
        {"usercontent_fqdn": "*.example.com"},  # 와일드카드
        {"usercontent_fqdn": "https://usercontent.example.com"},  # 스킴
        {"media_fqdn": "media.example.com/"},
    ):
        with pytest.raises(ValueError):
            _roles(_prod_settings(tmp_path, **bad))


def test_role_host_origin_in_cors_allow_list_refuses_startup(tmp_path):
    with pytest.raises(ValueError, match="role host origin"):
        security.WebRuntimeConfig.from_settings(
            _settings(tmp_path, usercontent_fqdn=UC_HOST, cors_allowed_origins=f"http://{UC_HOST}")
        )
    with pytest.raises(ValueError, match="role host origin"):
        security.wrap_web_app(
            _inner_app(),
            _prod_settings(
                tmp_path,
                media_fqdn="media.example.com",
                cors_allowed_origins="https://client.example.net, https://media.example.com:443",
            ),
        )
    # 역할 호스트가 아닌 origin은 그대로 허용된다.
    config = security.WebRuntimeConfig.from_settings(
        _settings(tmp_path, usercontent_fqdn=UC_HOST, cors_allowed_origins="http://ui.example.test:5173")
    )
    assert config.cors_allowed_origins == {"http://ui.example.test:5173"}


def test_config_reads_role_host_environment(tmp_path, monkeypatch):
    from claire.config import Settings

    monkeypatch.setenv("CLAIRE_SITE_DOMAIN", "example.com")
    monkeypatch.setenv("CLAIRE_USERCONTENT_FQDN", "usercontent.example.com")
    monkeypatch.setenv("CLAIRE_MEDIA_FQDN", "media.example.com")
    s = Settings(_env_file=None, CLAIRE_DB_PATH=str(tmp_path / "claire.db"))
    assert (s.site_domain, s.usercontent_fqdn, s.media_fqdn) == (
        "example.com",
        "usercontent.example.com",
        "media.example.com",
    )
    monkeypatch.delenv("CLAIRE_SITE_DOMAIN")
    monkeypatch.delenv("CLAIRE_USERCONTENT_FQDN")
    monkeypatch.delenv("CLAIRE_MEDIA_FQDN")
    empty = Settings(_env_file=None, CLAIRE_DB_PATH=str(tmp_path / "claire.db"))
    assert (empty.site_domain, empty.usercontent_fqdn, empty.media_fqdn) == ("", "", "")


# --- 2. HostRouter 역할 분기와 GateMiddleware 제거 ---------------------------------------


@pytest.mark.asyncio
async def test_host_header_rules_and_unregistered_hosts(tmp_path):
    app = security.wrap_web_app(_inner_app(), _settings(tmp_path, usercontent_fqdn=UC_HOST))
    assert (await _call(app, "/health")).status == 200
    assert (await _call(app, "/health", host=None)).status == 400
    duplicate = await _call(app, "/health", headers=[("Host", DEV_HOST)])
    assert duplicate.status == 400
    assert (await _call(app, "/health", host="bad host")).status == 400
    unregistered = await _call(app, "/health", host="other.example.com")
    assert unregistered.status == 421
    # 보안 헤더(SafeAccessLog)는 HostRouter 바깥에 있어 거부 응답에도 붙는다.
    assert unregistered.header("x-content-type-options") == "nosniff"
    assert unregistered.header("x-request-id")


@pytest.mark.asyncio
async def test_static_assets_pass_host_router_and_security_headers(tmp_path):
    from claire.api import server

    assert not hasattr(server, "GateMiddleware")
    assert not hasattr(server, "PUBLIC_PATHS")

    app = security.wrap_web_app(_inner_app(), _settings(tmp_path))
    ok = await _call(app, "/static/js/app.js")
    assert ok.status == 200 and ok.body == b"static-body"
    assert ok.header("x-content-type-options") == "nosniff"
    assert ok.header("cache-control") is None  # 공개 경로라 no-store를 붙이지 않는다
    head = await _call(app, "/static/js/app.js", method="HEAD")
    assert head.status == 200
    # 미등록 호스트에서 정적 자산이 열리던 우회가 없어졌다.
    assert (await _call(app, "/static/js/app.js", host="other.example.com")).status == 421
    # 공개 예외는 GET·HEAD만이다.
    assert (await _call(app, "/static/js/app.js", method="POST")).status == 404


@pytest.mark.asyncio
async def test_create_app_serves_static_only_through_host_router(tmp_path):
    from claire.api.server import create_app
    from claire.config import Settings

    data_dir = tmp_path / "data"
    vault_dir = tmp_path / "vault"
    data_dir.mkdir()
    vault_dir.mkdir()
    settings = Settings(
        _env_file=None,
        CLAIRE_DB_PATH=str(data_dir / "claire.db"),
        CLAIRE_VAULT_PATH=str(vault_dir),
        CLAIRE_PROVIDER="mock",
        CLAIRE_ENVIRONMENT="development",
        CLAIRE_PUBLIC_URL="http://127.0.0.1:8765",
        CLAIRE_INJECT_TOKEN=OWNER_TOKEN,
        CLAIRE_ANONYMOUS_READONLY=True,
    )
    app = create_app(settings=settings)
    ok = await _call(app, "/static/js/app.js", host="127.0.0.1:8765")
    assert ok.status == 200
    assert ok.header("x-request-id") and ok.header("content-security-policy")
    assert (await _call(app, "/static/js/app.js", host="127.0.0.1:8799")).status == 421
    assert (await _call(app, "/static/js/app.js", host="ai.example.com")).status == 421


@pytest.mark.asyncio
async def test_usercontent_and_media_are_cookieless_404_without_app_cors(tmp_path):
    settings = _settings(tmp_path, usercontent_fqdn=UC_HOST, media_fqdn=MEDIA_HOST)
    app = security.wrap_web_app(_inner_app(), settings)
    for host in (UC_HOST, MEDIA_HOST):
        for path in ("/", "/health", "/admin/accounts", "/static/js/app.js"):
            result = await _call(
                app,
                path,
                host=host,
                headers=[("Origin", "null"), ("Cookie", f"claire_session={'s' * 43}")],
            )
            # app의 CORS(`Origin: null` → 403)를 씌우지 않는다. P2 하위 앱은 모든 경로가 404다.
            assert result.status == 404, (host, path)
            assert result.values("set-cookie") == []
            assert result.header("access-control-allow-origin") is None
    # app 호스트에서는 `Origin: null`이 여전히 403이다.
    assert (await _call(app, "/health", headers=[("Origin", "null")])).status == 403


@pytest.mark.asyncio
async def test_sub_apps_never_see_cookies_and_cannot_set_them(tmp_path):
    seen: list[list[tuple[bytes, bytes]]] = []

    async def recorder(scope, receive, send):
        seen.append(list(scope["headers"]))
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"set-cookie", b"claire_session=x; Path=/"), (b"content-type", b"text/plain")],
            }
        )
        await send({"type": "http.response.body", "body": b"ok"})

    roles = _roles(_settings(tmp_path, usercontent_fqdn=UC_HOST, media_fqdn=MEDIA_HOST))
    router = HostRouter(
        app=not_found_app, roles=roles, legacy=_FakeLegacy(), usercontent=recorder, media=recorder
    )
    for host in (UC_HOST, MEDIA_HOST):
        result = await _call(router, "/x", host=host, headers=[("Cookie", "__Host-claire_session=a; claire_session=b")])
        assert result.status == 200
        assert result.values("set-cookie") == []
    assert all(name != b"cookie" for headers in seen for name, _ in headers)
    assert len(seen) == 2


# --- 3. __Host- 세션 쿠키 (§5.2 Rev. 2.3, S11) ----------------------------------------------


def _session(settings: SimpleNamespace) -> str:
    conn = dbm.connect(settings.db_file)
    try:
        dbm.init_db(conn)
        return dbm.create_session(conn)
    finally:
        conn.close()


def _set_cookies(result: _Result) -> dict[str, list[str]]:
    by_name: dict[str, list[str]] = {}
    for raw in result.values("set-cookie"):
        by_name.setdefault(raw.split("=", 1)[0], []).append(raw)
    return by_name


def test_production_cookie_uses_host_prefix_and_development_keeps_name(tmp_path):
    from starlette.responses import Response

    prod = security.WebRuntimeConfig.from_settings(_prod_settings(tmp_path))
    response = Response()
    security.set_session_cookie(response, prod, "t" * 43)
    (raw,) = [v.decode() for k, v in response.raw_headers if k == b"set-cookie"]
    assert raw.startswith("__Host-claire_session=" + "t" * 43 + ";")
    assert "Secure" in raw and "Path=/" in raw and "HttpOnly" in raw and "SameSite=lax" in raw
    assert "Domain" not in raw

    cleared = Response()
    security.clear_session_cookie(cleared, prod)
    names = sorted(v.decode().split("=", 1)[0] for k, v in cleared.raw_headers if k == b"set-cookie")
    assert names == ["__Host-claire_session", "claire_session"]

    dev = security.WebRuntimeConfig.from_settings(_settings(tmp_path))
    dev_response = Response()
    security.set_session_cookie(dev_response, dev, "t" * 43)
    (dev_raw,) = [v.decode() for k, v in dev_response.raw_headers if k == b"set-cookie"]
    assert dev_raw.startswith("claire_session=") and "Secure" not in dev_raw

    assert security._sets_session_cookie([(b"set-cookie", b"__Host-claire_session=a; Path=/")])
    assert security._sets_session_cookie([(b"set-cookie", b"claire_session=; Max-Age=0")])
    assert not security._sets_session_cookie([(b"set-cookie", b"other=1")])


def _cookie_scope(*values: str) -> dict:
    return {"headers": [(b"cookie", v.encode("latin-1")) for v in values]}


def test_read_session_cookie_prefers_host_cookie_and_rejects_ambiguity():
    read = security.read_session_cookie
    assert read(_cookie_scope()) == (False, None)
    assert read(_cookie_scope("other=1")) == (False, None)
    # __Host-가 정확히 1개면 함께 온 claire_session은 무시한다(어느 순서든, 여러 개여도).
    assert read(_cookie_scope("claire_session=tossed; __Host-claire_session=real")) == (True, "real")
    assert read(_cookie_scope("__Host-claire_session=real", "claire_session=a; claire_session=b")) == (True, "real")
    # __Host-가 2개 이상이면 무효 자격이다.
    assert read(_cookie_scope("__Host-claire_session=a; __Host-claire_session=b")) == (True, None)
    assert read(_cookie_scope("__Host-claire_session=a", "__Host-claire_session=b; claire_session=c")) == (True, None)
    # __Host-가 없을 때만 claire_session을 본다(정확히 1개).
    assert read(_cookie_scope("claire_session=only")) == (True, "only")
    assert read(_cookie_scope("claire_session=a; claire_session=b")) == (True, None)
    assert security._read_session_cookies(_cookie_scope("claire_session=only")).legacy is True
    assert security._read_session_cookies(_cookie_scope("__Host-claire_session=x")).legacy is False


@pytest.mark.asyncio
async def test_cookie_tossing_cannot_override_or_break_host_session(tmp_path):
    settings = _prod_settings(tmp_path)
    token = _session(settings)
    app = security.wrap_web_app(_inner_app(), settings)

    for cookie in (
        f"claire_session={'x' * 43}; __Host-claire_session={token}",
        f"__Host-claire_session={token}; claire_session={'x' * 43}",
    ):
        result = await _call(app, "/graph", host=PROD_HOST, headers=[("Cookie", cookie)])
        assert result.status == 200, cookie
        cookies = _set_cookies(result)
        assert len(cookies["__Host-claire_session"]) == 1
        assert cookies["__Host-claire_session"][0].startswith(f"__Host-claire_session={token};")
        assert "claire_session" not in cookies  # 토싱된 쿠키는 건드리지도 않는다

    # 무효한 __Host- 옆의 유효한 claire_session을 승격하지 않는다(세션 고정 방어).
    fixed = await _call(
        app, "/graph", host=PROD_HOST, headers=[("Cookie", f"__Host-claire_session={'x' * 43}; claire_session={token}")]
    )
    assert fixed.status == 404
    # __Host-가 둘이면 무효 자격이다.
    doubled = await _call(
        app, "/graph", host=PROD_HOST, headers=[("Cookie", f"__Host-claire_session={token}; __Host-claire_session={token}")]
    )
    assert doubled.status == 404


@pytest.mark.asyncio
async def test_legacy_cookie_alone_is_reissued_as_host_cookie_in_production(tmp_path):
    settings = _prod_settings(tmp_path)
    token = _session(settings)
    app = security.wrap_web_app(_inner_app(), settings)

    result = await _call(app, "/graph", host=PROD_HOST, headers=[("Cookie", f"claire_session={token}")])
    assert result.status == 200
    cookies = _set_cookies(result)
    (reissued,) = cookies["__Host-claire_session"]
    assert reissued.startswith(f"__Host-claire_session={token};") and "Secure" in reissued
    assert "Domain" not in reissued
    (deleted,) = cookies["claire_session"]
    parsed = SimpleCookie()
    parsed.load(deleted)
    assert parsed["claire_session"].value == ""
    assert parsed["claire_session"]["max-age"] == "0"
    assert not parsed["claire_session"]["domain"]

    # 이제 __Host-만 보내면 재발급·삭제 없이 갱신만 한다.
    follow = await _call(app, "/graph", host=PROD_HOST, headers=[("Cookie", f"__Host-claire_session={token}")])
    assert follow.status == 200
    assert set(_set_cookies(follow)) == {"__Host-claire_session"}


@pytest.mark.asyncio
async def test_development_keeps_claire_session_without_reissue(tmp_path):
    settings = _settings(tmp_path)
    token = _session(settings)
    app = security.wrap_web_app(_inner_app(), settings)
    result = await _call(app, "/graph", headers=[("Cookie", f"claire_session={token}")])
    assert result.status == 200
    cookies = _set_cookies(result)
    assert set(cookies) == {"claire_session"}
    assert cookies["claire_session"][0].startswith(f"claire_session={token};")


def test_direct_cookie_reads_go_through_read_session_cookie():
    root = Path(__file__).resolve().parent.parent / "src" / "claire" / "api"
    for name in ("oauth.py", "server.py"):
        text = (root / name).read_text(encoding="utf-8")
        assert "request.cookies" not in text, name
        assert '"claire_session"' not in text, name


# --- 4. 레거시 호스트 (§5.3) ---------------------------------------------------------------


@dataclass
class _FakeLegacy:
    rows: dict[str, LegacyHost] = field(default_factory=dict)
    policy_value: LegacyHostPolicy = field(default_factory=LegacyHostPolicy)
    location: str | None = "/themes/72057594037927937/"

    def lookup(self, host: str) -> LegacyHost | None:
        return self.rows.get(host)

    def policy(self) -> LegacyHostPolicy:
        return self.policy_value

    def theme_location(self, legacy: LegacyHost) -> str | None:
        return self.location


def _legacy_router(tmp_path, directory, *, now: float = 1000.0) -> HostRouter:
    roles = _roles(_prod_settings(tmp_path))
    app = security.wrap_web_app(_inner_app(), _prod_settings(tmp_path))
    return HostRouter(app=app, roles=roles, legacy=directory, now=lambda: now)


def _row(host="ai.example.com", *, activated_at=None, sunset_at=None) -> LegacyHost:
    return LegacyHost(host=host, theme_id=3, created_at=1.0, activated_at=activated_at, sunset_at=sunset_at)


@pytest.mark.asyncio
async def test_legacy_host_redirects_get_head_and_refuses_other_methods(tmp_path):
    directory = _FakeLegacy(rows={"ai.example.com": _row()})
    router = _legacy_router(tmp_path, directory)
    noisy = [("Cookie", "__Host-claire_session=abc"), ("Origin", "https://evil.example.net")]

    get = await _call(router, "/documents", host="ai.example.com", query="q=1&x=%20y", headers=noisy)
    assert get.status == 308
    assert get.header("location") == f"{PROD_ORIGIN}/themes/72057594037927937/documents?q=1&x=%20y"
    head = await _call(router, "/", host="ai.example.com:8443", method="HEAD")
    assert head.status == 308
    assert head.header("location") == f"{PROD_ORIGIN}/themes/72057594037927937/"
    for method in ("POST", "PUT", "PATCH", "DELETE", "OPTIONS"):
        assert (await _call(router, "/documents", host="ai.example.com", method=method, headers=noisy)).status == 421
    for result in (get, head):
        assert result.values("set-cookie") == []
        assert all(not k.lower().startswith(b"access-control-") for k, _ in result.headers)

    # 경로를 그대로 붙여도 정본 origin을 벗어나지 않는다.
    escaped = await _call(router, "//evil.example.net/a b", host="ai.example.com", raw_path=b"//evil.example.net/a%20b")
    assert escaped.header("location") == f"{PROD_ORIGIN}/themes/72057594037927937/evil.example.net/a%20b"

    directory.location = None  # 테마가 사라졌거나 번호가 아직 없다
    assert (await _call(router, "/", host="ai.example.com")).status == 421
    assert (await _call(router, "/", host="unknown.example.com")).status == 421


@pytest.mark.asyncio
async def test_legacy_sunset_status_follows_policy(tmp_path):
    directory = _FakeLegacy(rows={"ai.example.com": _row(activated_at=0.0, sunset_at=1000.0)})
    before = _legacy_router(tmp_path, directory, now=999.0)
    assert (await _call(before, "/", host="ai.example.com")).status == 308
    after = _legacy_router(tmp_path, directory, now=1000.0)
    gone = await _call(after, "/", host="ai.example.com")
    assert gone.status == 410 and gone.values("set-cookie") == []
    assert (await _call(after, "/", host="ai.example.com", method="POST")).status == 410
    directory.policy_value = LegacyHostPolicy(after_sunset_status=421)
    assert (await _call(after, "/", host="ai.example.com")).status == 421


@pytest.mark.asyncio
async def test_role_host_wins_over_legacy_row(tmp_path):
    directory = _FakeLegacy(rows={PROD_HOST: _row(PROD_HOST)})
    router = _legacy_router(tmp_path, directory)
    assert (await _call(router, "/health", host=PROD_HOST)).status == 200


def _legacy_db(tmp_path: Path) -> Path:
    db_file = tmp_path / "claire.db"
    conn = dbm.connect(db_file)
    try:
        dbm.init_db(conn)
        conn.execute(LEGACY_HOSTS_DDL)
        conn.execute("INSERT OR IGNORE INTO themes (id, seq, label) VALUES (3, 3, 'AI')")
        conn.commit()
    finally:
        conn.close()
    return db_file


def _insert(db_file: Path, host: str, *, theme_id: int = 3) -> None:
    conn = sqlite3.connect(db_file)
    try:
        conn.execute(
            "INSERT INTO legacy_hosts (host, theme_id, created_at, activated_at, sunset_at) VALUES (?, ?, ?, NULL, NULL)",
            (host, theme_id, time.time()),
        )
        conn.commit()
    finally:
        conn.close()


def test_sqlite_directory_caches_and_sees_external_changes(tmp_path):
    missing = lh.SqliteLegacyHostDirectory(tmp_path / "absent.db")
    assert missing.lookup("ai.example.com") is None
    assert missing.policy() == LegacyHostPolicy()

    # v16 이전 DB(테이블 없음)는 레거시 호스트가 없는 것으로 본다.
    old_db = tmp_path / "old" / "claire.db"
    old_db.parent.mkdir()
    conn = sqlite3.connect(old_db)
    conn.execute("CREATE TABLE themes (id INTEGER PRIMARY KEY)")
    conn.commit()
    conn.close()
    old_directory = lh.SqliteLegacyHostDirectory(old_db)
    assert old_directory.lookup("ai.example.com") is None
    assert old_directory.policy() == LegacyHostPolicy()

    db_file = _legacy_db(tmp_path)
    directory = lh.SqliteLegacyHostDirectory(db_file, theme_location=lambda row: f"/themes/{row.theme_id}/")
    assert directory.lookup("ai.example.com") is None
    _insert(db_file, "ai.example.com")  # 다른 연결(이관·다른 프로세스)이 쓴다
    found = directory.lookup("AI.Example.com")
    assert found is not None and found.theme_id == 3 and found.sunset_at is None
    assert directory.theme_location(found) == "/themes/3/"
    assert lh.SqliteLegacyHostDirectory(db_file).theme_location(found) is None


def test_sunset_clock_activation_extension_and_indefinite_hold(tmp_path):
    db_file = _legacy_db(tmp_path)
    _insert(db_file, "ai.example.com")
    _insert(db_file, "finance.example.com")
    directory = lh.SqliteLegacyHostDirectory(db_file)
    conn = dbm.connect(db_file)
    try:
        assert lh.read_legacy_host_policy(conn) == LegacyHostPolicy(after_sunset_status=410, sunset_days=30)
        lh.write_legacy_host_policy(conn, LegacyHostPolicy(after_sunset_status=421, sunset_days=10), updated_by=1)
        assert lh.read_legacy_host_policy(conn) == LegacyHostPolicy(after_sunset_status=421, sunset_days=10)
        assert directory.policy() == LegacyHostPolicy(after_sunset_status=421, sunset_days=10)
        for bad in (LegacyHostPolicy(after_sunset_status=404), LegacyHostPolicy(sunset_days=0)):  # type: ignore[arg-type]
            with pytest.raises(ValueError):
                lh.write_legacy_host_policy(conn, bad, updated_by=1)

        # 활성화 전에는 무기한 308이다.
        assert directory.lookup("ai.example.com").activated_at is None

        now = 1_000_000.0
        assert lh.activate_sunset_clock(conn, hosts=["AI.example.com"], now=now) == 1
        ai = directory.lookup("ai.example.com")
        assert (ai.activated_at, ai.sunset_at) == (now, now + 10 * 86400)
        assert directory.lookup("finance.example.com").sunset_at is None
        # 이미 켠 시계는 다시 켜지 않는다(전체 활성화는 남은 행만).
        assert lh.activate_sunset_clock(conn, now=now + 5, days=30) == 1
        assert directory.lookup("ai.example.com").sunset_at == now + 10 * 86400
        assert directory.lookup("finance.example.com").sunset_at == now + 5 + 30 * 86400

        extended = lh.extend_sunset(conn, "ai.example.com", days=7, now=now + 1)
        assert extended.sunset_at == now + 17 * 86400 and extended.activated_at == now
        # 이미 지난 일몰은 지금부터 연장한다.
        late = now + 100 * 86400
        assert lh.extend_sunset(conn, "ai.example.com", days=1, now=late).sunset_at == late + 86400

        held = lh.keep_indefinitely(conn, "ai.example.com")
        assert held.sunset_at is None and held.activated_at == now
        assert directory.lookup("ai.example.com").sunset_at is None
        assert [row.host for row in lh.list_legacy_hosts(conn, theme_id=3)] == ["ai.example.com", "finance.example.com"]

        with pytest.raises(KeyError):
            lh.extend_sunset(conn, "absent.example.com", days=1)
        with pytest.raises(KeyError):
            lh.keep_indefinitely(conn, "absent.example.com")

        conn.execute(
            "UPDATE instance_settings SET value=? WHERE key=?", (json.dumps({"after_sunset_status": 302}), "legacy_host_policy")
        )
        conn.commit()
        with pytest.raises(ValueError):
            lh.read_legacy_host_policy(conn)
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_legacy_router_end_to_end_with_sqlite_directory(tmp_path):
    db_file = _legacy_db(tmp_path)
    _insert(db_file, "ai.example.com")
    directory = lh.SqliteLegacyHostDirectory(db_file, theme_location=lambda row: "/themes/72057594037927939/")
    clock = {"now": 5_000_000.0}
    roles = _roles(_prod_settings(tmp_path))
    router = HostRouter(
        app=security.wrap_web_app(_inner_app(), _prod_settings(tmp_path)),
        roles=roles,
        legacy=directory,
        now=lambda: clock["now"],
    )

    async def status() -> int:
        return (await _call(router, "/graph", host="ai.example.com")).status

    clock["now"] += 365 * 86400
    assert await status() == 308  # 활성화 전: 무기한 308
    conn = dbm.connect(db_file)
    try:
        lh.activate_sunset_clock(conn, now=clock["now"])
        clock["now"] += 29 * 86400
        assert await status() == 308
        clock["now"] += 86400
        assert await status() == 410  # 기본 30일 뒤 일몰
        lh.extend_sunset(conn, "ai.example.com", days=14, now=clock["now"])
        assert await status() == 308
        clock["now"] += 14 * 86400
        assert await status() == 410
        lh.keep_indefinitely(conn, "ai.example.com")
        assert await status() == 308
    finally:
        conn.close()
