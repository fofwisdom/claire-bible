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
       "/me", "/api/me", "/api/me/password", "/api/me/username", "/api/invites", "/api/me/provider-keys", "/api/me/provider-keys/verify",
       "/api/me/identities", "/api/me/identities/oidc/{id}/start", "/api/me/identities/oidc/{id}",
       # 유저 단위 기능(텔레그램 봇·서포트 번들·데이터 번들·백업)
       "/api/me/telegram", "/api/support/bundles", "/api/support/bundles/{bundle_id}",
       "/api/me/data-bundles",
       "/api/me/backups", "/api/me/backups/download", "/api/me/backups/policy",
       # Notifications and Web Push of the account (PWA_NOTIFICATION_DESIGN.md PN5)
       "/api/me/notifications", "/api/me/notifications/read", "/api/me/push", "/api/me/push/subscriptions", "/api/me/push/kinds/{kind}", "/api/me/push/test",
       "/api/federation/status", "/api/federation/followers", "/api/federation/following", "/api/federation/following/{following_id}", "/api/federation/publish", "/api/federation/retract",
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
       "/submissions", "/submissions/{number}",
       # Grants for shared knowledge bases of other servers and OAuth client grants
       "/api/kb/remote-grants", "/api/me/oauth/grants",
       # Agent-driven client extraction routes (AE5)
       "/api/client-extraction/stage", "/api/client-extraction/sources", "/api/client-extraction/sources/{document_id}/claim", "/api/client-extraction/sources/{document_id}/renew", "/api/client-extraction/spec", "/api/client-extraction/sources/{document_id}", "/api/client-extraction/sources/{document_id}/submit"}
SYSTEM = {
    "/health", "/robots.txt", "/ns", "/ns/v1", "/sitemap.xml", "/favicon.ico", "/favicon.svg", "/apple-touch-icon.png",
    "/apple-touch-icon-precomposed.png", "/manifest.json", "/site.webmanifest", "/browserconfig.xml",
    "/sw.js",
    "/icon", "/font", "/fonts/NotoSansKR-Regular.woff2", "/fonts/NotoSansKR-Bold.woff2",
    "/fonts/NotoSerifKR-Regular.woff2", "/fonts/NotoSerifKR-Bold.woff2", "/fonts/D2Coding.woff2",
    "/fonts/D2CodingBold.woff2", "/p", "/silvercord/{token}", "/entity/{token}", "/p/presentation", "/image", "/images/{filename:path}",
    "/p/images/{filename:path}", "/static/locales/{path:path}", "/static/{path:path}", "/reference", "/openapi.yaml",
    "/.well-known/oauth-protected-resource", "/.well-known/oauth-authorization-server",
    "/oauth/register", "/oauth/authorize", "/oauth/authorize/telegram-push", "/oauth/authorize/poll",
    "/oauth/token", "/oauth/revoke", "/oauth/device_authorization", "/device", "/mcp", "/support/bundle", "/support/bundle/{token}", "/support/graph-bundle/{token}", "/setup", "/login", "/logout", "/api/auth/reauth", "/api/locale",
    "/admin/accounts", "/api/admin/accounts", "/api/admin/accounts/signup-trace",
    "/api/admin/usernames/retired", "/api/admin/usernames/release",
    "/invite/{code}",
    # The public account directory, in both modes (KNOWLEDGE_FEDERATION_DESIGN.md Section 8.3 rule 2)
    "/directory", "/accounts",
    "/admin/server", "/api/admin/server", "/api/admin/server/mode", "/api/admin/server/invites", "/api/admin/server/languages", "/api/admin/server/legacy-hosts/sunset",
    "/api/admin/server-tier", "/api/admin/federation", "/api/admin/federation/domain-blocks", "/api/admin/federation/reading", "/admin/federation", "/admin/kb",
    # Knowledge federation (KNOWLEDGE_FEDERATION_DESIGN.md Sections 6.2 and 13, unit KF1)
    "/api/admin/federation/known-servers", "/api/admin/federation/journal", "/api/admin/federation/journal/{entry_id}", "/api/admin/federation/journal/{entry_id}/replay",
    "/auth/oidc/{id}/start", "/auth/oidc/{id}/callback", "/invite/complete", "/admin/idp", "/api/admin/idp",
    "/admin", "/admin/providers", "/admin/limits", "/admin/themes", "/admin/tokens", "/admin/audit", "/admin/mcp", "/admin/webmcp",
    "/api/admin/mcp/telemetry",
    "/admin/backups", "/api/admin/account-backups", "/api/admin/account-backups/restore",
    "/admin/storage", "/api/admin/storage", "/api/admin/storage/test",
    # The laboratory (LABORATORY_DESIGN.md): the server administrator only.
    "/laboratory", "/laboratory/measurements", "/laboratory/shadows", "/api/admin/laboratory/runs", "/api/admin/laboratory/runs/{run_id}", "/api/admin/laboratory/stt-hints",
    "/admin/plugins", "/api/admin/plugins", "/api/admin/plugins/{id}",
    "/account/telegram", "/account/notifications", "/account/bundles", "/account/backups", "/account/federation",
    # One page for the bundles of each account and of the server (the old /account/bundles is a 308 to it).
    "/support/bundles",
    # The steps of one document and the AI of each step, for each account.
    "/support/pipeline",
    # The old /settings/* paths of the /account/* pages: 308 redirects.
    "/settings/account", "/settings/keys", "/settings/invites", "/settings/federation", "/settings/telegram", "/settings/notifications", "/settings/bundles", "/settings/backups", "/settings/mcp", "/settings/webmcp", "/settings/tokens",
    "/settings", "/account", "/account/profile", "/account/security", "/account/invites", "/account/providers", "/settings/kb", "/settings/kb/manage", "/settings/kb/{kb_number:int}", "/settings/themes", "/account/tokens",
    "/moderation", "/moderation/kb", "/moderation/kb/{kb_number:int}", "/moderation/oversight", "/moderation/themes",
    "/settings/reader", "/settings/connections", "/settings/feeds", "/settings/tags", "/settings/rules",
    "/settings/outbound", "/settings/opml", "/account/mcp", "/account/webmcp",
    "/syndication", "/syndication/preferences",
    "/api/syndication/dispatch", "/syndication/dispatch",
    "/api/syndication/dispatches", "/syndication/dispatches",
    # Reputation of the account of the credential (SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md Section 8.6)
    "/api/reputation",
    # Automatic contribution and mutual collaboration (Sections 9.9, 9.10)
    "/api/kb/similar", "/api/auto-rule", "/api/auto-rule/dry-run", "/api/contributions", "/api/contribution-blocks", "/api/contribution-blocks/{document}",
    "/api/mutual-collaborations", "/api/mutual-collaborations/{account}", "/api/admin/auto-contribution",
    "/api/me/pair-review/items", "/api/me/pair-review/items/{item_id}/verdict", "/api/me/pair-review/stats",
}
# The admin gives and removes the role ``moderator`` of a shared knowledge base (design Section 10.2), and reviews the shadow records of the same-subject source merge (EXPANSION.md §3.1).
SYSTEM_KEYS = {*((m, "/api/admin/reputations") for m in ("GET", "HEAD")), ("PUT", "/api/admin/reputations/settings"), *((m, "/api/admin/expansion/shadow") for m in ("GET", "HEAD")), ("PUT", "/api/admin/expansion/shadow/{record_id}/verdict"), *((m, "/api/admin/pair-review/report") for m in ("GET", "HEAD")), ("PUT", "/api/admin/kb/{number}/moderators/{account}"), ("DELETE", "/api/admin/kb/{number}/moderators/{account}"), *((m, "/api/admin/themes") for m in ("GET", "HEAD", "POST")), ("PUT", "/api/admin/themes/settings"), ("POST", "/api/admin/themes/judge"), ("PATCH", "/api/admin/themes/{number}"), ("PUT", "/api/admin/themes/{number}/knowledge-bases/{kb}"), ("PUT", "/api/admin/themes/{number}/remote-knowledge-bases/{remote_id}"), ("POST", "/api/admin/themes/{number}/merge")}
ADMIN_KEYS = {
    *((m, "/api/admin/auto-contribution") for m in ("GET", "HEAD", "PUT")),
    *((m, p) for p in ("/providers", "/limits", "/ingest-limits") for m in ("GET", "HEAD", "POST", "PATCH")),
    ("POST", "/providers/test"),
    ("POST", "/providers/check"),
    ("POST", "/support/bundle"),
    *SYSTEM_KEYS,
    *((m, p) for p in ("/admin/accounts", "/api/admin/accounts", "/api/admin/accounts/signup-trace", "/admin/server", "/api/admin/server") for m in ("GET", "HEAD")),
    ("PATCH", "/api/admin/accounts"),
    ("GET", "/api/admin/usernames/retired"),
    ("HEAD", "/api/admin/usernames/retired"),
    ("POST", "/api/admin/usernames/release"),
    ("POST", "/api/admin/server/mode"),
    ("POST", "/api/admin/server/invites"),
    ("PUT", "/api/admin/server/languages"),
    # LABORATORY_DESIGN.md: the laboratory area and its API.
    *((m, p) for p in ("/laboratory", "/laboratory/measurements", "/laboratory/shadows", "/api/admin/laboratory/runs", "/api/admin/laboratory/runs/{run_id}") for m in ("GET", "HEAD")),
    ("DELETE", "/api/admin/laboratory/runs/{run_id}"),
    ("POST", "/api/admin/laboratory/stt-hints"),
    ("POST", "/api/admin/server/legacy-hosts/sunset"),
    *((m, "/api/admin/server-tier") for m in ("GET", "HEAD", "PUT")),
    *((m, "/api/admin/federation") for m in ("GET", "HEAD")),
    *((m, "/api/admin/federation/domain-blocks") for m in ("GET", "HEAD", "POST", "DELETE")),
    *((m, "/api/admin/federation/reading") for m in ("GET", "HEAD", "PUT")),
    *((m, "/api/admin/federation/known-servers") for m in ("GET", "HEAD", "POST", "DELETE")),
    *((m, p) for p in ("/api/admin/federation/journal", "/api/admin/federation/journal/{entry_id}") for m in ("GET", "HEAD")),
    ("POST", "/api/admin/federation/journal/{entry_id}/replay"),
    *((m, "/admin/federation") for m in ("GET", "HEAD")),
    *((m, "/admin/kb") for m in ("GET", "HEAD")),
    *((m, "/moderation/oversight") for m in ("GET", "HEAD")),
    *((m, "/moderation/themes") for m in ("GET", "HEAD")),
    *((m, "/admin/idp") for m in ("GET", "HEAD")),
    *((m, "/api/admin/idp") for m in ("GET", "HEAD", "PUT")),
    *((m, p) for p in ("/admin", "/admin/providers", "/admin/limits", "/admin/themes", "/admin/tokens", "/admin/audit", "/admin/mcp", "/admin/webmcp", "/api/admin/mcp/telemetry") for m in ("GET", "HEAD")),
    *((m, p) for p in ("/admin/backups", "/api/admin/account-backups") for m in ("GET", "HEAD")),
    ("POST", "/api/admin/account-backups/restore"),
    *((m, p) for p in ("/admin/storage", "/api/admin/storage") for m in ("GET", "HEAD")),
    ("PUT", "/api/admin/storage"),
    ("POST", "/api/admin/storage/test"),
    *((m, p) for p in ("/admin/plugins", "/api/admin/plugins") for m in ("GET", "HEAD")),
    ("PUT", "/api/admin/plugins/{id}"),
    ("DELETE", "/api/admin/plugins/{id}"),
}
# Network mode only: the routes for more than one account (invites). The key routes are in the two modes because the server administrator has a personal key. ActivityPub is the same in the two modes (KNOWLEDGE_FEDERATION_DESIGN.md Section 15 rule 5).
NETWORK_ONLY_KEYS: set[tuple[str, str]] = {
    *((m, "/invite/{code}") for m in ("GET", "HEAD", "POST")),
    *((m, "/api/invites") for m in ("GET", "HEAD", "POST", "PATCH")),
    *((m, "/invite/complete") for m in ("GET", "HEAD", "POST")),
}


# ``POST /kb`` makes a shared knowledge base for the account of the credential (design Section 10.1); the prefix is ignored.
ANY_KEYS = {("POST", "/kb")}
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
