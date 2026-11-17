"""라우트 표면 계약 (설계 §9.1, §18.6).

- ``ROUTE_POLICY``의 모든 값은 표면별 생성 헬퍼(``_root``·``_system``·``_namespaced``·``_any``·``_collection``)로 만든다(기본값에 기대지 않는다). Each value gives the access level and the capability as literals (SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md Section 6.9 rule 3).
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

SURFACE_HELPERS = {"_root": "root", "_system": "system", "_namespaced": "namespaced", "_any": "any", "_collection": "collection"}
# SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md Section 7: ``/kb`` and ``/themes`` are collections of the server at the root and of the account under ``/@{u}/``.
COLLECTION_KEYS = {("GET", "/kb"), ("HEAD", "/kb"), ("GET", "/themes"), ("HEAD", "/themes")}

ROOT = {"/"}
ANY = {"/providers", "/providers/test", "/providers/check", "/limits", "/ingest-limits", "/tokens", "/tokens/revoke",
       "/me", "/api/me", "/api/me/password", "/api/invites", "/api/me/keys", "/api/me/keys/verify",
       "/api/me/personal-cli", "/api/me/personal-cli/test",
       "/api/me/identities", "/api/me/identities/oidc/{id}/start", "/api/me/identities/oidc/{id}",
       # 유저 단위 기능(텔레그램 봇·서포트 번들·데이터 번들·백업)
       "/api/me/telegram", "/api/me/support-bundles", "/api/me/support-bundles/download",
       "/api/me/data-bundles", "/api/me/data-bundles/download",
       "/api/me/backups", "/api/me/backups/download", "/api/me/backups/policy",
       "/api/federation/status", "/api/federation/followers", "/api/federation/settings", "/api/federation/following", "/api/federation/following/{following_id}", "/api/federation/publish", "/api/federation/retract",
       # Federation client (FP4-5)
       "/api/federation/timeline", "/api/federation/timeline/{item_id}", "/api/federation/timeline/{item_id}/ingest", "/api/federation/timeline/{item_id}/context", "/render/remote/{item_id}", "/media/remote/{url_hash}", "/api/federation/objects",
       # Outbound interactions (FP4-9)
       "/api/federation/notes", "/api/federation/notes/{note_id}", "/api/federation/timeline/{item_id}/favourite", "/api/federation/timeline/{item_id}/boost",
       # Quotes (FP4-9Q)
       "/api/federation/quotes", "/api/federation/quotes/{quote_id}", "/api/federation/objects/{object_id}",
       # Account feeds (FP4-F1, FP4-F2)
       "/api/feeds", "/api/feeds/poll", "/api/feeds/opml", "/api/feeds/items",
       # Feed view (FP4-F3)
       "/api/feeds/items/{item_id}", "/api/feeds/items/{item_id}/ingest", "/api/feeds/discover", "/render/feed/{item_id}",
       # Contribution to a theme (SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md Section 9.2)
       "/submissions", "/submissions/{number}"}
SYSTEM = {
    "/health", "/robots.txt", "/sitemap.xml", "/favicon.ico", "/favicon.svg", "/apple-touch-icon.png",
    "/apple-touch-icon-precomposed.png", "/manifest.json", "/site.webmanifest", "/browserconfig.xml",
    "/sw.js",
    "/icon", "/font", "/fonts/NotoSansKR-Regular.woff2", "/fonts/NotoSansKR-Bold.woff2",
    "/fonts/NotoSerifKR-Regular.woff2", "/fonts/NotoSerifKR-Bold.woff2", "/fonts/D2Coding.woff2",
    "/fonts/D2CodingBold.woff2", "/p", "/p/presentation", "/image", "/images/{filename:path}",
    "/p/images/{filename:path}", "/static/{path:path}", "/reference", "/openapi.yaml",
    "/.well-known/oauth-protected-resource", "/.well-known/oauth-authorization-server",
    "/oauth/register", "/oauth/authorize", "/oauth/authorize/telegram-push", "/oauth/authorize/poll",
    "/oauth/token", "/mcp", "/support/bundle", "/setup", "/login", "/logout", "/api/auth/reauth", "/api/locale",
    "/admin/accounts", "/api/admin/accounts",
    "/api/admin/usernames/retired", "/api/admin/usernames/release",
    "/invite/{code}",
    "/explore/accounts",
    "/explore/themes",
    "/explore/themes/{theme_number:int}",
    "/kb",
    "/kb/{kb_number:int}",
    "/kb/{kb_number:int}/",
    "/admin/server", "/api/admin/server", "/api/admin/server/mode", "/api/admin/server/legacy-hosts/sunset",
    "/api/admin/server-tier", "/api/admin/federation", "/api/admin/federation/domain-blocks", "/api/admin/federation/reading", "/admin/federation", "/admin/kb",
    "/auth/oidc/{id}/start", "/auth/oidc/{id}/callback", "/invite/complete", "/admin/idp", "/api/admin/idp",
    "/admin", "/admin/providers", "/admin/limits", "/admin/themes", "/admin/tokens", "/admin/mcp",
    "/api/admin/mcp/telemetry",
    "/admin/backups", "/api/admin/account-backups", "/api/admin/account-backups/restore",
    "/admin/storage", "/api/admin/storage", "/api/admin/storage/test",
    "/settings/telegram", "/settings/bundles", "/settings/backups", "/settings/federation",
    "/settings", "/settings/account", "/settings/keys", "/settings/kb", "/settings/kb/manage", "/settings/kb/{kb_number:int}", "/settings/themes", "/settings/tokens",
    "/settings/reader", "/settings/connections", "/settings/feeds", "/settings/tags", "/settings/rules",
    "/settings/outbound", "/settings/opml", "/settings/mcp", "/settings/webmcp",
    "/syndication", "/syndication/preferences",
    "/api/syndication/dispatch", "/syndication/dispatch",
    "/api/syndication/dispatches", "/syndication/dispatches",
    # Reputation of the account of the credential (SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md Section 8.6)
    "/api/reputation",
    # Automatic contribution and mutual collaboration (Sections 9.9, 9.10)
    "/api/auto-rule", "/api/auto-rule/dry-run", "/api/auto-contributions", "/api/contribution-blocks", "/api/contribution-blocks/{document}",
    "/api/mutual-collaborations", "/api/mutual-collaborations/{account}", "/api/admin/auto-contribution",
}
# The admin gives and removes the role ``moderator`` of a shared knowledge base (design Section 10.2).
SYSTEM_KEYS = {*((m, "/api/admin/reputations") for m in ("GET", "HEAD")), ("PUT", "/api/admin/reputations/settings"), ("PUT", "/api/admin/kb/{number}/moderators/{account}"), ("DELETE", "/api/admin/kb/{number}/moderators/{account}"), *((m, "/api/admin/themes") for m in ("GET", "HEAD", "POST")), ("PUT", "/api/admin/themes/settings"), ("POST", "/api/admin/themes/judge"), ("PATCH", "/api/admin/themes/{number}"), ("PUT", "/api/admin/themes/{number}/knowledge-bases/{kb}"), ("POST", "/api/admin/themes/{number}/merge")}
ADMIN_KEYS = {
    *((m, "/api/admin/auto-contribution") for m in ("GET", "HEAD", "PUT")),
    *((m, p) for p in ("/providers", "/limits", "/ingest-limits") for m in ("GET", "HEAD", "POST", "PATCH")),
    ("POST", "/providers/test"),
    ("POST", "/providers/check"),
    *((m, "/api/me/personal-cli") for m in ("GET", "HEAD", "PATCH")),
    ("POST", "/api/me/personal-cli/test"),
    ("POST", "/support/bundle"),
    *SYSTEM_KEYS,
    *((m, p) for p in ("/admin/accounts", "/api/admin/accounts", "/admin/server", "/api/admin/server") for m in ("GET", "HEAD")),
    ("PATCH", "/api/admin/accounts"),
    ("GET", "/api/admin/usernames/retired"),
    ("HEAD", "/api/admin/usernames/retired"),
    ("POST", "/api/admin/usernames/release"),
    *((m, "/api/invites") for m in ("GET", "HEAD", "POST", "PATCH")),
    ("POST", "/api/admin/server/mode"),
    ("POST", "/api/admin/server/legacy-hosts/sunset"),
    *((m, "/api/admin/server-tier") for m in ("GET", "HEAD", "PUT")),
    *((m, "/api/admin/federation") for m in ("GET", "HEAD", "POST")),
    *((m, "/api/admin/federation/domain-blocks") for m in ("GET", "HEAD", "POST", "DELETE")),
    *((m, "/api/admin/federation/reading") for m in ("GET", "HEAD", "PUT")),
    *((m, "/admin/federation") for m in ("GET", "HEAD")),
    *((m, "/admin/kb") for m in ("GET", "HEAD")),
    *((m, "/admin/idp") for m in ("GET", "HEAD")),
    *((m, "/api/admin/idp") for m in ("GET", "HEAD", "PUT")),
    *((m, p) for p in ("/admin", "/admin/providers", "/admin/limits", "/admin/themes", "/admin/tokens", "/admin/mcp", "/api/admin/mcp/telemetry") for m in ("GET", "HEAD")),
    *((m, p) for p in ("/admin/backups", "/api/admin/account-backups") for m in ("GET", "HEAD")),
    ("POST", "/api/admin/account-backups/restore"),
    *((m, p) for p in ("/admin/storage", "/api/admin/storage") for m in ("GET", "HEAD")),
    ("PUT", "/api/admin/storage"),
    ("POST", "/api/admin/storage/test"),
}
# 네트워크 전용 경로는 P3에서 생긴다(/invite/{code}, /explore/*, /api/invites).
NETWORK_ONLY_KEYS: set[tuple[str, str]] = {
    *((m, "/invite/{code}") for m in ("GET", "HEAD", "POST")),
    *((m, "/explore/accounts") for m in ("GET", "HEAD")),
    *((m, "/api/invites") for m in ("GET", "HEAD", "POST", "PATCH")),
    *((m, "/api/me/keys") for m in ("GET", "HEAD", "PUT", "DELETE")),
    ("POST", "/api/me/keys/verify"),
    *((m, "/invite/complete") for m in ("GET", "HEAD", "POST")),
    *((m, "/api/federation/status") for m in ("GET", "HEAD")),
    *((m, "/api/federation/followers") for m in ("GET", "HEAD", "DELETE")),
    *((m, "/api/federation/settings") for m in ("GET", "HEAD", "PUT")),
    *((m, "/api/federation/following") for m in ("GET", "HEAD", "POST")),
    ("DELETE", "/api/federation/following/{following_id}"),
    *((m, "/api/federation/timeline") for m in ("GET", "HEAD")),
    *((m, "/api/federation/timeline/{item_id}") for m in ("GET", "HEAD", "PATCH")),
    ("POST", "/api/federation/timeline/{item_id}/ingest"),
    ("POST", "/api/federation/timeline/{item_id}/context"),
    *((m, "/render/remote/{item_id}") for m in ("GET", "HEAD")),
    *((m, "/api/federation/objects") for m in ("GET", "HEAD")),
    # Outbound interactions (FP4-9)
    ("POST", "/api/federation/notes"),
    *((m, "/api/federation/notes/{note_id}") for m in ("PATCH", "DELETE")),
    *((m, "/api/federation/timeline/{item_id}/favourite") for m in ("POST", "DELETE")),
    *((m, "/api/federation/timeline/{item_id}/boost") for m in ("POST", "DELETE")),
    # Quotes (FP4-9Q)
    *((m, "/api/federation/quotes") for m in ("GET", "HEAD")),
    ("PATCH", "/api/federation/quotes/{quote_id}"),
    ("PATCH", "/api/federation/objects/{object_id}"),
    ("POST", "/api/federation/publish"),
    ("POST", "/api/federation/retract"),
}


# The download of a data bundle uses only the token. Thus the address at the root works in each mode. POST /graph/bundle stays namespaced.
# ``POST /kb`` makes a shared knowledge base for the account of the credential (design Section 10.1); the prefix is ignored.
ANY_KEYS = {("GET", "/graph/bundle"), ("HEAD", "/graph/bundle"), ("POST", "/kb")}
# ``PATCH /`` and ``DELETE /`` act on the knowledge base that the path selects; only ``GET /`` and ``HEAD /`` have the surface ``root``.
NAMESPACED_ROOT_KEYS = {("PATCH", "/"), ("DELETE", "/")}


def _expected_surface(path: str, method: str = "") -> str:
    if (method, path) in COLLECTION_KEYS:
        return "collection"
    if (method, path) in ANY_KEYS:
        return "any"
    if (method, path) in NAMESPACED_ROOT_KEYS:
        return "namespaced"
    if (method, path) in SYSTEM_KEYS:
        return "system"
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
        assert len(value.args) == 2 and all(isinstance(arg, ast.Constant) for arg in value.args), label
        for keyword in value.keywords:
            if keyword.arg == "step_up":
                assert value.func.id in ("_namespaced", "_system") and ast.literal_eval(keyword.value) is True, label
                continue
            assert keyword.arg == "modes" and ast.unparse(keyword.value) in {"ALL_MODES", "NETWORK_ONLY"}, label


@pytest.mark.parametrize("helper", sorted(SURFACE_HELPERS))
def test_surface_helpers_pass_surface_and_modes_explicitly(helper):
    func = getattr(security, helper)
    tree = ast.parse(inspect.getsource(func))
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "RouteRule"]
    assert len(calls) == 1
    keywords = {k.arg: k.value for k in calls[0].keywords}
    assert set(keywords) - {"step_up"} == {"access", "capability", "surface", "modes"}
    assert ast.literal_eval(keywords["surface"]) == SURFACE_HELPERS[helper]
    rule = func("read", "read")
    assert rule.surface == SURFACE_HELPERS[helper] and rule.modes == security.ALL_MODES and rule.step_up is False
    assert func("read", "read", modes=security.NETWORK_ONLY).modes == security.NETWORK_ONLY
    with pytest.raises(ValueError):
        func("read", "unknown-capability")


def test_surfaces_modes_and_admin_access_match_design_table():
    surfaces = {key: rule.surface for key, rule in security.ROUTE_POLICY.items()}
    assert surfaces == {key: _expected_surface(key[1], key[0]) for key in security.ROUTE_POLICY}
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
