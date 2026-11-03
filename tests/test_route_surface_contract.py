"""라우트 표면 계약 (설계 §9.1, §18.6).

- ``ROUTE_POLICY``의 모든 값은 표면별 생성 헬퍼(``_root``·``_system``·``_namespaced``·``_any``)로 만든다(기본값에 기대지 않는다).
- 표면·모드·접근 수준이 §9.1 표와 같다.
- 경로 매개변수 키는 Starlette ``compile_path``로 정확 키 다음에 찾는다.
- ``create_app``에 마운트된 라우트(Mount 포함) 하나에 정책 항목이 정확히 하나, 모든 정책 항목이 마운트된 라우트에 대응한다.
"""

from __future__ import annotations

import ast
import inspect
import os
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.applications import Starlette
from starlette.routing import Mount, Route, compile_path

from claire.api import security, server
from claire.store.token_store import reset_token_stores

SURFACE_HELPERS = {"_root": "root", "_system": "system", "_namespaced": "namespaced", "_any": "any"}

ROOT = {"/"}
ANY = {"/providers", "/providers/test", "/limits", "/ingest-limits", "/tokens", "/tokens/revoke",
       "/me", "/api/me", "/api/me/password", "/api/invites", "/api/me/keys", "/api/me/keys/verify"}
SYSTEM = {
    "/health", "/robots.txt", "/sitemap.xml", "/favicon.ico", "/favicon.svg", "/apple-touch-icon.png",
    "/apple-touch-icon-precomposed.png", "/manifest.json", "/site.webmanifest", "/browserconfig.xml",
    "/icon", "/font", "/fonts/NotoSansKR-Regular.woff2", "/fonts/NotoSansKR-Bold.woff2",
    "/fonts/NotoSerifKR-Regular.woff2", "/fonts/NotoSerifKR-Bold.woff2", "/fonts/D2Coding.woff2",
    "/fonts/D2CodingBold.woff2", "/p", "/p/presentation", "/image", "/images/{filename:path}",
    "/p/images/{filename:path}", "/static/{path:path}", "/reference", "/openapi.yaml",
    "/.well-known/oauth-protected-resource", "/.well-known/oauth-authorization-server",
    "/oauth/register", "/oauth/authorize", "/oauth/authorize/telegram-push", "/oauth/authorize/poll",
    "/oauth/token", "/mcp", "/support/bundle", "/setup", "/login", "/logout", "/api/auth/reauth",
    "/admin/accounts", "/api/admin/accounts",
    "/api/admin/usernames/retired", "/api/admin/usernames/release",
    "/invite/{code}",
    "/explore/accounts",
    "/admin/instance", "/api/admin/instance", "/api/admin/instance/mode", "/api/admin/instance/legacy-hosts/sunset",
    "/api/admin/server-tier",
}
ADMIN_KEYS = {
    *((m, p) for p in ("/providers", "/limits", "/ingest-limits") for m in ("GET", "HEAD", "POST", "PATCH")),
    ("POST", "/providers/test"),
    ("POST", "/support/bundle"),
    *((m, p) for p in ("/admin/accounts", "/api/admin/accounts", "/admin/instance", "/api/admin/instance") for m in ("GET", "HEAD")),
    ("PATCH", "/api/admin/accounts"),
    ("GET", "/api/admin/usernames/retired"),
    ("HEAD", "/api/admin/usernames/retired"),
    ("POST", "/api/admin/usernames/release"),
    *((m, "/api/invites") for m in ("GET", "HEAD", "POST", "PATCH")),
    ("POST", "/api/admin/instance/mode"),
    ("POST", "/api/admin/instance/legacy-hosts/sunset"),
    *((m, "/api/admin/server-tier") for m in ("GET", "HEAD", "PUT")),
}
# 네트워크 전용 경로는 P3에서 생긴다(/invite/{code}, /explore/*, /api/invites).
NETWORK_ONLY_KEYS: set[tuple[str, str]] = {
    *((m, "/invite/{code}") for m in ("GET", "HEAD", "POST")),
    *((m, "/explore/accounts") for m in ("GET", "HEAD")),
    *((m, "/api/invites") for m in ("GET", "HEAD", "POST", "PATCH")),
    *((m, "/api/me/keys") for m in ("GET", "HEAD", "PUT", "DELETE")),
    ("POST", "/api/me/keys/verify"),
}


def _expected_surface(path: str) -> str:
    if path in ROOT:
        return "root"
    if path in ANY:
        return "any"
    if path in SYSTEM:
        return "system"
    return "namespaced"


def _policy_dict_node() -> ast.Dict:
    tree = ast.parse(inspect.getsource(security))
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", None) == "ROUTE_POLICY":
            assert isinstance(node.value, ast.Dict)
            return node.value
    raise AssertionError("ROUTE_POLICY literal not found")


def test_every_policy_value_uses_an_explicit_surface_helper():
    node = _policy_dict_node()
    assert len(node.keys) == len(security.ROUTE_POLICY)
    for key, value in zip(node.keys, node.values, strict=True):
        label = ast.unparse(key)
        assert isinstance(value, ast.Call) and isinstance(value.func, ast.Name), label
        assert value.func.id in SURFACE_HELPERS, label
        assert len(value.args) == 1 and isinstance(value.args[0], ast.Constant), label
        for keyword in value.keywords:
            assert keyword.arg == "modes" and ast.unparse(keyword.value) in {"ALL_MODES", "NETWORK_ONLY"}, label


@pytest.mark.parametrize("helper", sorted(SURFACE_HELPERS))
def test_surface_helpers_pass_surface_and_modes_explicitly(helper):
    func = getattr(security, helper)
    tree = ast.parse(inspect.getsource(func))
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "RouteRule"]
    assert len(calls) == 1
    keywords = {k.arg: k.value for k in calls[0].keywords}
    assert set(keywords) == {"access", "surface", "modes"}
    assert ast.literal_eval(keywords["surface"]) == SURFACE_HELPERS[helper]
    rule = func("read")
    assert rule.surface == SURFACE_HELPERS[helper] and rule.modes == security.ALL_MODES
    assert func("read", modes=security.NETWORK_ONLY).modes == security.NETWORK_ONLY


def test_surfaces_modes_and_admin_access_match_design_table():
    surfaces = {key: rule.surface for key, rule in security.ROUTE_POLICY.items()}
    assert surfaces == {key: _expected_surface(key[1]) for key in security.ROUTE_POLICY}
    assert {key for key, rule in security.ROUTE_POLICY.items() if rule.modes != security.ALL_MODES} == NETWORK_ONLY_KEYS
    assert all(rule.modes <= security.ALL_MODES and rule.modes for rule in security.ROUTE_POLICY.values())
    assert {key for key, rule in security.ROUTE_POLICY.items() if rule.access == "admin"} == ADMIN_KEYS
    # /setup은 admin이 아니다(설정 전 관리자는 setup_required 상태로 owner 세션을 쓴다).
    assert security.ROUTE_POLICY[("GET", "/setup")].access == "owner"
    # 루트 GET /는 root 표면 하나뿐이다.
    assert {k for k, r in security.ROUTE_POLICY.items() if r.surface == "root"} == {("GET", "/"), ("HEAD", "/")}


@pytest.mark.parametrize(
    ("method", "path", "key"),
    [
        ("GET", "/images/a/b.png", ("GET", "/images/{filename:path}")),
        ("HEAD", "/images/x.png", ("HEAD", "/images/{filename:path}")),
        ("GET", "/p/images/x.png", ("GET", "/p/images/{filename:path}")),
        ("GET", "/static/css/app.css", ("GET", "/static/{path:path}")),
        ("head", "/static/app.js", ("HEAD", "/static/{path:path}")),
        ("GET", "/graph", ("GET", "/graph")),
    ],
)
def test_path_parameter_keys_are_compiled(method, path, key):
    assert security.lookup_route_rule(method, path) is security.ROUTE_POLICY[key]


def test_path_parameter_lookup_is_fail_closed():
    assert security.lookup_route_rule("POST", "/static/app.js") is None
    assert security.lookup_route_rule("GET", "/static") is None
    assert security.lookup_route_rule("GET", "/imagesx/a.png") is None
    assert security.lookup_route_rule("GET", "/nope") is None
    # 매개변수 키 문자열 자체는 정확 키 표에 없다(매개변수 표에서만 찾는다).
    assert ("GET", "/images/{filename:path}") not in security._EXACT_ROUTE_POLICY
    param_keys = {(m, p) for (m, p) in security.ROUTE_POLICY if "{" in p}
    assert {(m, r.pattern) for m, r, _ in security._PARAM_ROUTE_POLICY} == {
        (m, compile_path(p)[0].pattern) for m, p in param_keys
    }


# --- 마운트된 라우트와 1:1 -------------------------------------------------------


@dataclass
class _Settings:
    db_file: Path
    data_dir: Path
    environment: str = "development"
    public_url: str = "http://127.0.0.1:8765"
    inject_token: str = "owner-" + "o" * 32
    readonly_token: str = "readonly-" + "r" * 32
    cors_allowed_origins: str = ""
    anonymous_readonly: bool = False


@pytest.fixture
def mounted_app(tmp_path: Path, monkeypatch) -> Starlette:
    for name in list(os.environ):
        if name.startswith("CLAIRE_"):
            monkeypatch.delenv(name, raising=False)
    reset_token_stores()
    service = SimpleNamespace(provider=SimpleNamespace(name="stub"))
    app = server.create_app(_Settings(db_file=tmp_path / "claire.db", data_dir=tmp_path), service)
    while not isinstance(app, Starlette):
        app = app.app
    yield app
    reset_token_stores()


def _mounted_keys(app: Starlette) -> list[tuple[str, str]]:
    keys: list[tuple[str, str]] = []
    for route in app.routes:
        if isinstance(route, Route):
            keys.extend((method, route.path) for method in sorted(route.methods or ()))
        elif isinstance(route, Mount):
            keys.extend((method, f"{route.path}/{{path:path}}") for method in ("GET", "HEAD"))
        else:  # pragma: no cover - 새 라우트 종류가 생기면 계약을 다시 정한다
            raise AssertionError(f"unexpected route type: {route!r}")
    return keys


def test_each_mounted_route_has_exactly_one_policy_entry(mounted_app):
    keys = _mounted_keys(mounted_app)
    # 같은 (method, path)가 두 번 마운트되지 않는다(정책 항목 하나 = 라우트 하나).
    assert len(keys) == len(set(keys))
    assert set(keys) == set(security.ROUTE_POLICY)
    for route in mounted_app.routes:
        if isinstance(route, Mount):
            sample = f"{route.path}/x/y.css"
            assert security.lookup_route_rule("GET", sample) is security.ROUTE_POLICY[("GET", f"{route.path}/{{path:path}}")]
