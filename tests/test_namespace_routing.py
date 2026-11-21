"""2단계 네임스페이스 미들웨어 (설계 §6.1–§6.4, §6.6, §6.7, §9.1, O8·O9·O10·O15).

``wrap_web_app``이 조립한 실제 경계(PrefixParser → CORS → … → Authentication → NamespaceResolver)를
작은 Starlette 앱에 씌우고 ASGI로 직접 부른다(``raw_path``를 그대로 넣기 위해서다). 계정·테마·모드는
``tmp_path``의 ``claire.db``에 직접 쓴다. 네트워크 모드는 O7대로 ``server_settings.mode``를 직접 써서 켠다.
"""

from __future__ import annotations

import dataclasses
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from claire.api import namespace as ns_mod
from claire.api import security
from claire.api.namespace import (
    NamespaceMissing,
    PathRedirect,
    PathRejected,
    SqliteNamespaceDirectory,
    account_home_path,
    namespace_link,
    parse_request_path,
    request_namespace,
    shell_bootstrap,
    theme_base_path,
)
from claire.api.principal import request_principal
from claire.store import accounts as accounts_store
from claire.store import db as dbm
from claire.store.token_store import reset_token_stores

OWNER_TOKEN = "owner-" + "o" * 32
DEV_HOST = "192.0.2.10:8766"
DEV_ORIGIN = "http://192.0.2.10:8766"

ADMIN_NAME = "boss"
BASE = 1 << 56
N_ADMIN_DEFAULT = BASE + 100
N_ADMIN_PRIVATE = BASE + 101  # after version 28: a shared knowledge base that the admin made
N_ALICE_PUBLIC = BASE + 200
N_ALICE_PRIVATE = BASE + 201  # after version 28: a shared knowledge base that Alice made
N_CAROL_PUBLIC = BASE + 300

# 계정별 자격증명 대역: 토큰 → (계정, 범위)
ALICE_OWNER = "alice-owner-" + "a" * 30
ALICE_READONLY = "alice-readonly-" + "a" * 30
CAROL_OWNER = "carol-owner-" + "c" * 30
ADMIN_COLLAB = "admin-collab-" + "b" * 30


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    for name in list(os.environ):
        if name.startswith("CLAIRE_"):
            monkeypatch.delenv(name, raising=False)
    reset_token_stores()
    yield
    reset_token_stores()


@dataclass
class World:
    db_file: Path
    alice: int
    carol: int
    dave: int
    alice_public_id: int
    admin_public_id: int

    def set_mode(self, mode: str) -> None:
        conn = dbm.connect(self.db_file)
        try:
            accounts_store.AccountStore(conn).set_setting("mode", mode, updated_by=1)
        finally:
            conn.close()

    def execute(self, sql: str, params: tuple = ()) -> None:
        conn = dbm.connect(self.db_file)
        try:
            conn.execute(sql, params)
            conn.commit()
        finally:
            conn.close()


def _insert_account(conn, public_id: int, username: str, *, status: str = "active", public: bool) -> int:
    now = time.time()
    cur = conn.execute(
        "INSERT INTO accounts (public_id, username, role, status, display_name, profile_public, "
        "created_at, activated_at, updated_at) VALUES (?, ?, 'member', ?, '', ?, ?, ?, ?)",
        (public_id, username, status, int(public), now, now, now),
    )
    return int(cur.lastrowid)


def _insert_theme(conn, kb_id: int, account_id: int, number: int, *, public: bool,
                  default: bool = False, ga: str = "") -> None:
    """``default=True``: the personal knowledge base of the account. Else a shared knowledge base (no owner, public, type ``real``) that the account made."""
    now = time.time()
    if default:
        conn.execute(
            "INSERT INTO knowledge_bases (id, seq, label, is_personal, is_public, "
            "ga_measurement_id, created_at, updated_at, account_id, number) "
            "VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?, ?)",
            (kb_id, kb_id, f"t{kb_id}", int(public), ga, now, now, account_id, number),
        )
        return
    conn.execute(
        "INSERT INTO knowledge_bases (id, seq, label, is_personal, is_public, "
        "ga_measurement_id, created_at, updated_at, account_id, number, created_by_account_id, knowledge_type) "
        "VALUES (?, ?, ?, 0, 1, ?, ?, ?, NULL, ?, ?, 'real')",
        (kb_id, kb_id, f"t{kb_id}", ga, now, now, number, account_id),
    )


@pytest.fixture
def world(tmp_path: Path) -> World:
    db_file = tmp_path / "claire.db"
    conn = dbm.connect(db_file)
    try:
        dbm.init_db(conn)
        conn.execute(
            "UPDATE accounts SET username=?, status='active', activated_at=? WHERE id=1",
            (ADMIN_NAME, time.time()),
        )
        alice = _insert_account(conn, BASE + 7001, "alice", public=True)
        carol = _insert_account(conn, BASE + 7002, "carol", public=False)
        dave = _insert_account(conn, BASE + 7003, "dave", status="suspended", public=True)
        conn.execute(
            "INSERT INTO username_tombstones (username, account_id, was_public, retired_at) VALUES "
            "('zed', 90, 1, 0), ('yan', 91, 0, 0)"
        )
        conn.execute("DELETE FROM knowledge_bases")
        _insert_theme(conn, 0, 1, N_ADMIN_DEFAULT, public=True, default=True)
        _insert_theme(conn, 5, 1, N_ADMIN_PRIVATE, public=False, ga="G-PINNED")
        _insert_theme(conn, 10, alice, N_ALICE_PUBLIC, public=True, default=True)
        _insert_theme(conn, 11, alice, N_ALICE_PRIVATE, public=False)
        _insert_theme(conn, 20, carol, N_CAROL_PUBLIC, public=True, default=True)
        conn.commit()
        admin_public_id = accounts_store.AccountStore(conn).admin().public_id
    finally:
        conn.close()
    return World(db_file, alice, carol, dave, BASE + 7001, admin_public_id)


def _credentials(world: World) -> dict[str, security._Credential]:
    def cred(account_id: int, scope: str, role: str = "member") -> security._Credential:
        return security._Credential(
            scope=scope, account_id=account_id, server_role=role,  # type: ignore[arg-type]
            credential_id=f"test:{account_id}:{scope}", authenticated_at=0.0,
        )

    return {
        ALICE_OWNER: cred(world.alice, "owner"),
        ALICE_READONLY: cred(world.alice, "readonly"),
        CAROL_OWNER: cred(world.carol, "owner"),
        ADMIN_COLLAB: cred(1, "write", "admin"),
    }


@pytest.fixture
def fake_sessions(monkeypatch, world):
    table = _credentials(world)

    async def validate(_config, token):
        return table.get(token)

    monkeypatch.setattr(security, "_validate_session", validate)
    return table


async def _echo(request: Request) -> JSONResponse:
    namespace = request_namespace(request)
    return JSONResponse(
        {
            "path": request.url.path,
            "ns": dataclasses.asdict(namespace),
            "principal": request_principal(request).account_id,
            "bootstrap": shell_bootstrap(request),
            "link": namespace_link(request, "/graph"),
        }
    )


def _inner(static_dir: Path) -> Starlette:
    return Starlette(
        routes=[
            Route("/", _echo, methods=["GET"]),
            Route("/whoami", _echo, methods=["GET"]),
            Route("/graph", _echo, methods=["GET"]),
            Route("/themes", _echo, methods=["GET"]),
            Route("/tokens", _echo, methods=["GET"]),
            Route("/health", _echo, methods=["GET"]),
            Route("/syndication", _echo, methods=["GET"]),
            Route("/admin/accounts", _echo, methods=["GET"]),
            Route("/providers", _echo, methods=["GET"]),
            Mount("/static", StaticFiles(directory=str(static_dir)), name="static"),
        ]
    )


def _settings(world: World, *, anonymous_readonly: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        environment="development",
        public_url=DEV_ORIGIN,
        cors_allowed_origins="",
        inject_token=OWNER_TOKEN,
        readonly_token="",
        anonymous_readonly=anonymous_readonly,
        db_file=world.db_file,
        data_dir=world.db_file.parent,
    )


@pytest.fixture
def app_factory(world: World, tmp_path: Path):
    static_dir = tmp_path / "static"
    static_dir.mkdir()
    (static_dir / "app.css").write_text("body{}", encoding="utf-8")

    def make(*, anonymous_readonly: bool = False):
        return security.wrap_web_app(_inner(static_dir), _settings(world, anonymous_readonly=anonymous_readonly))

    return make


@dataclass
class _Result:
    status: int
    headers: dict[str, str]
    body: bytes

    def json(self) -> dict:
        return json.loads(self.body)


async def _call(app, path: str, *, raw_path: bytes | None = None, query: str = "",
                token: str | None = None, method: str = "GET",
                cookie: str | None = None) -> _Result:
    headers = [(b"host", DEV_HOST.encode())]
    if token:
        headers.append((b"authorization", f"Bearer {token}".encode()))
    if cookie:
        headers.append((b"cookie", cookie.encode()))
    messages: list[dict] = []

    async def receive() -> dict:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict) -> None:
        messages.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.4"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": raw_path if raw_path is not None else path.encode("utf-8"),
        "query_string": query.encode("latin-1"),
        "root_path": "",
        "headers": headers,
        "client": ("198.51.100.20", 50000),
        "server": ("192.0.2.10", 8766),
        "state": {},
    }
    await app(scope, receive, send)
    start = next(m for m in messages if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
    hdrs = {k.decode().lower(): v.decode("latin-1") for k, v in start.get("headers", [])}
    return _Result(start["status"], hdrs, body)


# --- 1단계: 정규화와 구문 분리 (§6.1, §6.4) ---------------------------------


@pytest.mark.parametrize(
    ("path", "raw"),
    [
        ("/a/b", b"/a%2Fb"),
        ("/a/b", b"/a%2fb"),
        ("/@alice/../admin/accounts", None),
        ("/./graph", None),
        ("//graph", None),
        ("/@alice//graph", None),
        ("/graph/..", b"/graph/%2e%2e"),
    ],
)
def test_normalization_rejects_with_400(path, raw):
    with pytest.raises(PathRejected) as exc:
        parse_request_path(path, raw)
    assert exc.value.status == 400


def test_raw_path_query_bytes_are_split_off_before_checks():
    # 쿼리의 ``?url=https://…``(``//``·``%2F``)를 경로로 오인하지 않는다.
    parsed = parse_request_path("/graph", b"/graph?url=https://x.test/a%2Fb/../c")
    assert parsed.inner_path == "/graph" and parsed.prefix == ""


def test_two_stage_prefix_parsing():
    parsed = parse_request_path(f"/@alice/themes/{N_ALICE_PUBLIC}/graph", None)
    assert parsed.prefix == f"/@alice/themes/{N_ALICE_PUBLIC}"
    assert (parsed.username, parsed.theme_number, parsed.inner_path) == ("alice", N_ALICE_PUBLIC, "/graph")
    assert parse_request_path("/@alice/themes", None).inner_path == "/themes"
    assert parse_request_path("/themes", None).prefix == ""
    root_pin = parse_request_path(f"/themes/{N_ADMIN_DEFAULT}/", None)
    assert (root_pin.prefix, root_pin.inner_path) == (f"/themes/{N_ADMIN_DEFAULT}", "/")
    # ``%40alice``는 디코딩된 path가 ``@alice``이므로 같은 규칙을 탄다.
    assert parse_request_path("/@alice/graph", b"/%40alice/graph").username == "alice"


@pytest.mark.parametrize(
    ("path", "query", "location"),
    [
        ("/@Alice/graph", b"", "/@alice/graph"),
        ("/@ALICE", b"doc=1", "/@alice/?doc=1"),
        ("/@alice", b"", "/@alice/"),
        (f"/@alice/themes/{N_ALICE_PUBLIC}", b"x=1&y=2", f"/@alice/themes/{N_ALICE_PUBLIC}/?x=1&y=2"),
        (f"/themes/{N_ADMIN_DEFAULT}", b"", f"/themes/{N_ADMIN_DEFAULT}/"),
    ],
)
def test_canonical_redirects(path, query, location):
    with pytest.raises(PathRedirect) as exc:
        parse_request_path(path, None, query)
    assert exc.value.location == location


@pytest.mark.parametrize(
    "path",
    ["/@", "/@a/", "/@1abc/", "/@ab__c/", "/@abc_/", "/@Kelvin/", "/themes/0123/", "/themes/abc/",
     "/@alice/themes/abc", f"/themes/{1 << 63}/", "/@alice/themes/0/"],
)
def test_grammar_violations_are_404(path):
    with pytest.raises(PathRejected) as exc:
        parse_request_path(path, None)
    assert exc.value.status == 404


@pytest.mark.asyncio
async def test_middleware_maps_rejections_and_redirects(app_factory):
    app = app_factory()
    assert (await _call(app, "/a/b", raw_path=b"/a%2Fb")).status == 400
    assert (await _call(app, f"/@{ADMIN_NAME}/graph", raw_path=f"/@{ADMIN_NAME}/graph?url=https://x.test/a".encode(), token=OWNER_TOKEN)).status == 200
    # Single mode: a root path of the personal knowledge base gets 308 to ``/@{username}/…`` (KNOWLEDGE_FEDERATION_DESIGN.md Section 15 rule 2).
    root_path = await _call(app, "/graph", raw_path=b"/graph?url=https://x.test/a", token=OWNER_TOKEN)
    assert root_path.status == 308 and root_path.headers["location"] == f"/@{ADMIN_NAME}/graph"
    redirect = await _call(app, "/@Alice/", query="doc=1")
    assert redirect.status == 308 and redirect.headers["location"] == "/@alice/?doc=1"
    assert (await _call(app, "/themes/0123/")).status == 404


# --- 정적 자산과 표면 (N5, §9.1) ---------------------------------------------


@pytest.mark.asyncio
async def test_static_mount_is_untouched_and_system_under_prefix_is_404(app_factory, world):
    app = app_factory()
    ok = await _call(app, "/static/app.css")
    assert ok.status == 200 and ok.body == b"body{}"
    assert "no-store" not in ok.headers.get("cache-control", "")
    pinned = await _call(app, f"/themes/{N_ADMIN_DEFAULT}/static/app.css")
    assert pinned.status == 404  # system 표면은 접두 아래에서 404(N5)
    world.set_mode("network")
    assert (await _call(app, "/static/app.css")).status == 200
    assert (await _call(app, "/@alice/static/app.css")).status == 404
    assert (await _call(app, "/@alice/health")).status == 404
    assert (await _call(app, "/health")).status == 200


@pytest.mark.asyncio
async def test_access_log_and_router_see_original_and_stripped_paths(app_factory, caplog):
    app = app_factory()
    caplog.set_level("INFO", logger="claire.api.access")
    res = await _call(app, f"/kb/{N_ADMIN_PRIVATE}/graph", token=OWNER_TOKEN)
    assert res.status == 200 and res.json()["path"] == "/graph"
    assert f"path=/kb/{N_ADMIN_PRIVATE}/graph" in caplog.text


# --- 싱글 모드 -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_single_mode_admin_namespace_is_at_username(app_factory, world):
    """KNOWLEDGE_FEDERATION_DESIGN.md Section 15 rules 2 and 4: single mode serves the admin namespace at ``/@{username}/``; ``/`` gives 302 and the other root paths give 308 to it. Rule S9 is withdrawn."""
    app = app_factory(anonymous_readonly=True)
    res = await _call(app, f"/@{ADMIN_NAME}/graph")
    body = res.json()
    assert res.status == 200
    assert body["ns"] == {
        "mode": "single", "account_id": 1, "username": ADMIN_NAME, "pinned_kb_id": None,
        "prefix": f"/@{ADMIN_NAME}", "via_prefix": True,
    }
    assert body["bootstrap"] == {"api-base": f"/@{ADMIN_NAME}/", "root-base": "/", "storage-key": f"a{world.admin_public_id}"}
    assert body["link"] == f"{DEV_ORIGIN}/@{ADMIN_NAME}/graph"
    home = await _call(app, f"/@{ADMIN_NAME}/")
    assert home.status == 200 and home.json()["ns"]["account_id"] == 1
    # ``/``: a temporary redirect with the query, because the meaning of ``/`` depends on the server mode.
    root = await _call(app, "/", query="doc=1")
    assert root.status == 302 and root.headers["location"] == f"/@{ADMIN_NAME}/?doc=1"
    assert root.headers["cache-control"] == "no-store"
    # Other root paths: a permanent redirect, because the target stays valid in network mode.
    other = await _call(app, "/graph", query="a=1")
    assert other.status == 308 and other.headers["location"] == f"/@{ADMIN_NAME}/graph?a=1"
    head = await _call(app, "/graph", method="HEAD")
    assert head.status == 308 and head.headers["location"] == f"/@{ADMIN_NAME}/graph"


@pytest.mark.asyncio
async def test_single_mode_before_setup_keeps_the_root_paths(app_factory, world):
    """KNOWLEDGE_FEDERATION_DESIGN.md Section 15 rule 3: before the first setup the admin has no USERNAME, so there is no redirect. ``/`` comes to the route, which gives ``/setup`` or the page "setup not complete" (``tests/test_setup_flow.py``)."""
    # The USERNAME column cannot change (a trigger); the state alone decides: only an ``active`` admin has the namespace ``/@{username}/``.
    world.execute("UPDATE accounts SET status='setup_required' WHERE id=1")
    app = app_factory(anonymous_readonly=True)
    res = await _call(app, "/graph")
    assert res.status == 200
    assert res.json()["ns"] == {
        "mode": "single", "account_id": 1, "username": None, "pinned_kb_id": None,
        "prefix": "", "via_prefix": False,
    }
    assert res.json()["link"] == f"{DEV_ORIGIN}/graph"
    home = await _call(app, "/")
    assert home.status == 200 and home.json()["ns"]["account_id"] == 1
    assert (await _call(app, f"/@{ADMIN_NAME}/")).status == 404


@pytest.mark.asyncio
async def test_single_mode_account_prefix_is_404_for_other_names(app_factory):
    """In single mode only the USERNAME of the admin has a namespace. A member name and an unknown name get 404, also for the admin."""
    app = app_factory()
    assert (await _call(app, f"/@{ADMIN_NAME}/graph", token=OWNER_TOKEN)).status == 200
    for path in ("/@alice/", "/@alice/graph", "/@nobody/"):
        res = await _call(app, path, token=OWNER_TOKEN)
        assert res.status == 404
        assert ADMIN_NAME.encode() not in res.body


@pytest.mark.asyncio
async def test_single_mode_old_admin_theme_link_goes_to_username(app_factory, world):
    """An old link ``/@{admin}/themes/{number}/…`` of the personal knowledge base of the admin gets 308 to ``/@{admin}/…``. The old O15 redirect to ``/…`` is withdrawn with S9."""
    app = app_factory(anonymous_readonly=True)
    res = await _call(app, f"/@{ADMIN_NAME}/themes/{N_ADMIN_DEFAULT}/graph", query="a=1")
    assert res.status == 308 and res.headers["location"] == f"/@{ADMIN_NAME}/graph?a=1"
    assert (await _call(app, "/@alice/")).status == 404


@pytest.mark.asyncio
async def test_single_mode_kb_path_pins_a_shared_knowledge_base(app_factory, world):
    """Design Section 7: ``/kb/{number}/`` pins a shared knowledge base with no namespace account. Old ``/themes/{number}/`` links get 308."""
    app = app_factory()
    res = await _call(app, f"/kb/{N_ADMIN_PRIVATE}/graph", token=OWNER_TOKEN)
    assert res.status == 200
    body = res.json()
    assert body["ns"]["pinned_kb_id"] == 5 and body["ns"]["prefix"] == f"/kb/{N_ADMIN_PRIVATE}"
    assert body["ns"]["username"] is None and body["ns"]["account_id"] is None
    assert body["bootstrap"]["api-base"] == f"/kb/{N_ADMIN_PRIVATE}/"
    assert body["link"] == f"{DEV_ORIGIN}/kb/{N_ADMIN_PRIVATE}/graph"
    # A shared knowledge base that another account made has the same path.
    assert (await _call(app, f"/kb/{N_ALICE_PRIVATE}/graph", token=OWNER_TOKEN)).status == 200
    # Old links: shared -> 308 /kb/{number}/…; the personal knowledge base of the admin -> 308 /@{username}/… (single mode, KNOWLEDGE_FEDERATION_DESIGN.md Section 15).
    old = await _call(app, f"/themes/{N_ADMIN_PRIVATE}/graph", token=OWNER_TOKEN, query="a=1")
    assert old.status == 308 and old.headers["location"] == f"/kb/{N_ADMIN_PRIVATE}/graph?a=1"
    personal = await _call(app, f"/themes/{N_ADMIN_DEFAULT}/graph", token=OWNER_TOKEN)
    assert personal.status == 308 and personal.headers["location"] == f"/@{ADMIN_NAME}/graph"
    assert (await _call(app, f"/kb/{N_ADMIN_DEFAULT}/graph", token=OWNER_TOKEN)).headers["location"] == f"/@{ADMIN_NAME}/graph"
    # The personal knowledge base of another account has no path in single mode; an unknown number is 404.
    assert (await _call(app, f"/kb/{N_ALICE_PUBLIC}/graph", token=OWNER_TOKEN)).status == 404
    assert (await _call(app, f"/themes/{N_ALICE_PUBLIC}/graph", token=OWNER_TOKEN)).status == 404
    assert (await _call(app, f"/kb/{BASE + 999}/graph", token=OWNER_TOKEN)).status == 404
    assert (await _call(app, f"/themes/{BASE + 999}/graph", token=OWNER_TOKEN)).status == 404


@pytest.mark.asyncio
async def test_csp_follows_pinned_theme_ga(app_factory):
    app = app_factory()
    pinned = await _call(app, f"/kb/{N_ADMIN_PRIVATE}/graph", token=OWNER_TOKEN)
    assert "googletagmanager" in pinned.headers["content-security-policy"]
    no_ga = await _call(app, "/graph", token=OWNER_TOKEN)
    assert "googletagmanager" not in no_ga.headers["content-security-policy"]


# --- admin 접근 수준 (§9.1) ---------------------------------------------------


@pytest.mark.asyncio
async def test_admin_access_requires_owner_scope_of_admin_account(app_factory, world, fake_sessions):
    app = app_factory()
    assert (await _call(app, "/admin/accounts", token=OWNER_TOKEN)).status == 200
    assert (await _call(app, "/providers", token=OWNER_TOKEN)).status == 200
    assert (await _call(app, "/providers", token=ALICE_OWNER)).status == 404
    assert (await _call(app, "/admin/accounts", token=ALICE_OWNER)).status == 404
    assert (await _call(app, "/providers", token=ADMIN_COLLAB)).status == 404
    # any 표면: 접두를 무시하고 자격증명 계정을 쓴다.
    tokens = await _call(app, "/tokens", token=ALICE_OWNER)
    assert tokens.status == 200 and tokens.json()["ns"]["account_id"] == world.alice


# --- 네트워크 모드 -------------------------------------------------------------


@pytest.mark.asyncio
async def test_network_root_redirects_spa_query_or_directory(app_factory, world):
    world.set_mode("network")
    app = app_factory()
    # P3 D4: SPA 쿼리가 없으면 공개 디렉터리 렌더링(200)
    assert (await _call(app, "/")).status == 200
    assert (await _call(app, "/", token=OWNER_TOKEN)).status == 200
    res = await _call(app, "/", query="doc=abc&x=1")
    assert res.status == 308 and res.headers["location"] == f"/@{ADMIN_NAME}/?doc=abc&x=1"
    assert (await _call(app, "/", query="node=n1")).headers["location"] == f"/@{ADMIN_NAME}/?node=n1"


@pytest.mark.asyncio
async def test_network_account_prefix_resolution(app_factory, world, fake_sessions):
    world.set_mode("network")
    app = app_factory()
    res = await _call(app, "/@alice/graph")
    assert res.status == 404  # namespaced read: 익명은 인증 단계에서 404(ANONYMOUS_READONLY=0)
    res = await _call(app, "/@alice/graph", token=CAROL_OWNER)
    body = res.json()
    assert res.status == 200
    # N4: 네임스페이스는 접두가 정한다(자격증명 계정 carol이 아니다).
    assert body["ns"]["account_id"] == world.alice and body["principal"] == world.carol
    assert body["ns"]["prefix"] == "/@alice" and body["ns"]["username"] == "alice"
    assert body["bootstrap"] == {"api-base": "/@alice/", "root-base": "/", "storage-key": f"a{world.alice_public_id}"}
    assert body["link"] == f"{DEV_ORIGIN}/@alice/graph"
    # Old links under an account (design Section 7): a shared knowledge base -> 308 /kb/{number}/…
    old = await _call(app, f"/@alice/themes/{N_ALICE_PRIVATE}/", token=ALICE_OWNER)
    assert old.status == 308 and old.headers["location"] == f"/kb/{N_ALICE_PRIVATE}/"
    pinned = await _call(app, f"/kb/{N_ALICE_PRIVATE}/", token=ALICE_OWNER)
    assert pinned.status == 200 and pinned.json()["ns"]["pinned_kb_id"] == 11 and pinned.json()["ns"]["account_id"] is None
    # The personal knowledge base of an account with a private profile: no redirect that shows the USERNAME.
    assert (await _call(app, f"/@alice/themes/{N_CAROL_PUBLIC}/", token=ALICE_OWNER)).status == 404


@pytest.mark.asyncio
async def test_network_n1_private_profile_suspended_and_tombstones(app_factory, world, fake_sessions):
    world.set_mode("network")
    app = app_factory()
    # 비공개 프로필: 다른 계정·관리자 모두 404, 본인 200
    assert (await _call(app, "/@carol/", token=ALICE_OWNER)).status == 404
    assert (await _call(app, "/@carol/", token=OWNER_TOKEN)).status == 404
    assert (await _call(app, "/@carol/", token=CAROL_OWNER)).status == 200
    assert (await _call(app, "/@nobody_here/", token=OWNER_TOKEN)).status == 404
    assert (await _call(app, "/@dave/", token=OWNER_TOKEN)).status == 404  # active가 아님
    assert (await _call(app, "/@zed/", token=OWNER_TOKEN)).status == 410  # 공개였던 퇴역 이름
    assert (await _call(app, "/@yan/", token=OWNER_TOKEN)).status == 404  # 비공개였던 퇴역 이름


@pytest.mark.asyncio
async def test_network_n3_unprefixed_uses_principal_and_anonymous_is_404(app_factory, world, fake_sessions):
    world.set_mode("network")
    app = app_factory(anonymous_readonly=True)
    assert (await _call(app, "/graph")).status == 404
    # ``/themes`` at the root is the collection of themes of the server (SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md Section 7), not a namespaced route.
    assert (await _call(app, "/themes")).status == 200
    res = await _call(app, "/graph", token=ALICE_READONLY)
    body = res.json()
    assert body["ns"]["account_id"] == world.alice and body["ns"]["prefix"] == "/@alice"
    assert body["ns"]["via_prefix"] is False
    # 익명도 공개 프로필의 접두 경로는 읽는다(ANONYMOUS_READONLY=1).
    assert (await _call(app, "/@alice/graph")).status == 200


@pytest.mark.asyncio
async def test_network_root_theme_resolver(app_factory, world, fake_sessions):
    world.set_mode("network")
    app = app_factory(anonymous_readonly=True)
    # A public personal knowledge base: each requester (also anonymous) gets 308 to the path of the owner (query kept).
    res = await _call(app, f"/themes/{N_ALICE_PUBLIC}/graph", query="doc=1")
    assert res.status == 308
    assert res.headers["location"] == "/@alice/graph?doc=1"
    assert (await _call(app, f"/kb/{N_ALICE_PUBLIC}/graph", query="doc=1")).headers["location"] == "/@alice/graph?doc=1"
    # A shared knowledge base: each requester gets 308 to /kb/{number}/ from the old link, and the /kb path serves it.
    for token in (ALICE_OWNER, CAROL_OWNER, OWNER_TOKEN):
        old = await _call(app, f"/themes/{N_ALICE_PRIVATE}/", token=token)
        assert old.status == 308 and old.headers["location"] == f"/kb/{N_ALICE_PRIVATE}/"
    assert (await _call(app, f"/kb/{N_ALICE_PRIVATE}/graph", token=CAROL_OWNER)).status == 200
    # 공개 테마라도 소유자 프로필이 비공개면 USERNAME을 드러내지 않는다.
    assert (await _call(app, f"/themes/{N_CAROL_PUBLIC}/", token=ALICE_OWNER)).status == 404
    assert (await _call(app, f"/themes/{BASE + 999}/")).status == 404


@pytest.mark.asyncio
async def test_modes_404_for_network_only_rule(app_factory, world, monkeypatch):
    rule = security.RouteRule(access="public", capability="none", surface="system", modes=security.NETWORK_ONLY)
    monkeypatch.setitem(security._EXACT_ROUTE_POLICY, ("GET", "/syndication"), rule)
    app = app_factory()
    assert (await _call(app, "/syndication")).status == 404
    world.set_mode("network")
    assert (await _call(app, "/syndication")).status == 200


@pytest.mark.asyncio
async def test_directory_cache_reloads_after_db_change(app_factory, world, fake_sessions):
    app = app_factory()
    assert (await _call(app, "/@alice/", token=ALICE_OWNER)).status == 404
    world.set_mode("network")
    assert (await _call(app, "/@alice/", token=ALICE_OWNER)).status == 200
    world.execute("UPDATE accounts SET status='suspended' WHERE id=?", (world.alice,))
    assert (await _call(app, "/@alice/", token=CAROL_OWNER)).status == 404


def test_unknown_mode_is_an_error(world):
    world.set_mode("galaxy")
    with pytest.raises(RuntimeError):
        SqliteNamespaceDirectory(world.db_file).mode()


@pytest.mark.asyncio
async def test_db_errors_inside_resolver_are_sanitized(app_factory, world):
    world.set_mode("galaxy")
    res = await _call(app_factory(), "/graph", token=OWNER_TOKEN)
    assert res.status == 500 and res.json() == {"error": "internal server error"}


# --- /?t= 매직 링크 이동지 (§6.6) ---------------------------------------------


def _bootstrap_token(world: World, account_id: int) -> str:
    conn = dbm.connect(world.db_file)
    try:
        return accounts_store.create_account_session(conn, account_id=account_id, scope="owner")
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_magic_link_destination_by_mode(app_factory, world):
    app = app_factory()
    single = await _call(app, "/", query=f"t={_bootstrap_token(world, 1)}")
    assert single.status == 302 and single.headers["location"] == "/"
    world.set_mode("network")
    network = await _call(app, "/", query=f"t={_bootstrap_token(world, world.alice)}")
    assert network.status == 302 and network.headers["location"] == "/@alice/"
    assert "set-cookie" in network.headers


# --- 헬퍼 ------------------------------------------------------------------------


def test_path_helpers():
    assert account_home_path("single", None) == "/"
    assert account_home_path("network", "alice") == "/@alice/"
    with pytest.raises(ValueError):
        account_home_path("network", None)
    assert theme_base_path("single", "alice", N_ALICE_PUBLIC) == f"/themes/{N_ALICE_PUBLIC}/"
    assert theme_base_path("network", "alice", N_ALICE_PUBLIC) == f"/@alice/themes/{N_ALICE_PUBLIC}/"


def test_request_namespace_without_middleware_is_wiring_error():
    request = Request({"type": "http", "method": "GET", "path": "/", "headers": [], "state": {}})
    with pytest.raises(NamespaceMissing):
        request_namespace(request)
    assert ns_mod.NAMESPACE_STATE_KEY == "claire_namespace"


# --- create_app 배선 -------------------------------------------------------------


@pytest.fixture
def real_app(tmp_path: Path):
    from starlette.testclient import TestClient

    from claire.api.server import create_app
    from claire.core.config import Settings

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
        CLAIRE_MULTI_THEME=True,
    )
    client = TestClient(create_app(settings=settings), base_url=settings.public_url)
    return client, settings


def test_create_app_wires_namespace_whoami_static_and_legacy_location(real_app):
    client, settings = real_app
    owner = {"Authorization": f"Bearer {OWNER_TOKEN}"}
    assert client.get("/whoami").json() == {
        "scope": "anonymous", "server": {"mode": "single"}, "relation": "visitor", "is_admin": False,
    }
    assert client.get("/static/css/theme.css").status_code == 200
    created = client.post("/kb", json={"knowledge_type": "real", "label": "옛 호스트", "is_public": True}, headers=owner)
    assert created.status_code == 201, created.text
    kb_id = created.json()["knowledge_base"]["id"]
    conn = dbm.connect(settings.db_file)
    try:
        conn.execute("DELETE FROM legacy_hosts WHERE kb_id=?", (kb_id,))
        conn.execute("UPDATE knowledge_bases SET fqdn='old.example.com', number=NULL WHERE id=?", (kb_id,))
        conn.commit()
        dbm.init_db(conn)
        conn.execute("UPDATE accounts SET username=?, status='active' WHERE id=1", (ADMIN_NAME,))
        conn.commit()
        number = int(conn.execute("SELECT number FROM knowledge_bases WHERE id=?", (kb_id,)).fetchone()[0])
    finally:
        conn.close()
    single = client.get("/graph", headers={"Host": "old.example.com"}, follow_redirects=False)
    assert single.status_code == 308
    assert single.headers["location"] == f"http://127.0.0.1:8765/kb/{number}/graph"
    assert client.get(f"/kb/{number}/", follow_redirects=False).status_code == 200
    World(settings.db_file, 0, 0, 0, 0, 0).set_mode("network")
    network = client.get("/graph", headers={"Host": "old.example.com"}, follow_redirects=False)
    assert network.status_code == 308
    assert network.headers["location"] == f"http://127.0.0.1:8765/kb/{number}/graph"  # a shared knowledge base has the same path in each mode
    whoami = client.get("/whoami", headers=owner)
    assert whoami.json() == {"scope": "owner", "server": {"mode": "network"}, "relation": "owner", "is_admin": True}
