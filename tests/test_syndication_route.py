from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from claire.api.server import create_app
from claire.store import db as dbm

OWNER_TOKEN = "o" * 32
COLLAB_TOKEN = "c" * 32
READONLY_TOKEN = "r" * 32


@pytest.fixture
def syndication_env(tmp_path: Path):
    db_file = tmp_path / "claire.db"
    vault_dir = tmp_path / "vault"
    vault_dir.mkdir(parents=True, exist_ok=True)
    conn = dbm.connect(db_file)
    dbm.init_db(conn)
    conn.close()

    settings = SimpleNamespace(
        db_file=db_file,
        data_dir=tmp_path,
        vault_dir=vault_dir,
        db_path=str(db_file),
        vault_path=str(vault_dir),
        environment="development",
        public_url="http://127.0.0.1:8765/",
        inject_token=OWNER_TOKEN,
        collaborator_token=COLLAB_TOKEN,
        readonly_token=READONLY_TOKEN,
        anonymous_readonly=False,
        cors_allowed_origins="",
        inject_host="127.0.0.1",
        inject_port=8765,
        multi_theme=False,
    )

    class DummyService:
        def __init__(self, s):
            self.s = s
            self.provider = SimpleNamespace()

    app = create_app(settings, service=DummyService(settings))
    return app


def test_syndication_and_preferences_routes_io_interface(syndication_env) -> None:
    with TestClient(syndication_env, base_url="http://127.0.0.1:8765") as client:
        # Gate G8: Unauthenticated access is forbidden or not found
        res_unauthed = client.get("/syndication")
        assert res_unauthed.status_code in {401, 404}
        res_proto_unauthed = client.get("/prototype/syndication")
        assert res_proto_unauthed.status_code in {401, 404}
        res_prefs_unauthed = client.get("/syndication/preferences")
        assert res_prefs_unauthed.status_code in {401, 404}

        headers = {"Authorization": f"Bearer {OWNER_TOKEN}"}
        res_public = client.get("/syndication", headers=headers)
        assert res_public.status_code == 200
        assert "Claire Bible — Syndication" in res_public.text
        assert "authScope: 'owner'" in res_public.text
        assert 'data-layout="three-panel"' in res_public.text
        assert 'data-list-style="list"' in res_public.text
        assert 'id="btn-toggle-list-style"' in res_public.text
        assert 'id="btn-toggle-layout"' in res_public.text
        assert 'id="headlines-container"' in res_public.text
        # Entry links point straight to the root-mounted /settings/* console (D1, P4).
        assert "/syndication/preferences" not in res_public.text
        for path in ("/settings/reader", "/settings/connections", "/settings/tags", "/settings/outbound"):
            assert f'data-settings-path="{path}"' in res_public.text, path
        assert 'name="claire-storage-key" content="a' in res_public.text
        assert "claireSyndicationPrefs_v2" not in res_public.text
        assert 'PREFS_SLOT_PREFIX: "claire_synd_prefs_v2:"' in res_public.text
        assert "수집 원문" in res_public.text
        assert "적재 본문" in res_public.text
        assert "생성된 엔티티" in res_public.text
        assert 'href="/prototype/syndication"' in res_public.text

        # Live /syndication does not ship fake seeds or simulated federation prototypes.
        assert "CLAIRE_SYND_SEEDS" not in res_public.text
        assert "CLAIRE_SYND_CLIENT_FACTORY" not in res_public.text
        assert "CLAIRE_SYND_PEERS_FACTORY" not in res_public.text
        assert "/api/peers/publications" not in res_public.text
        assert "/api/syndication/dispatch" in res_public.text
        assert "/api/syndication/dispatches" in res_public.text

        # Four list styles: list / compact / card / roomy.
        for style in ("list", "compact", "card", "roomy"):
            assert f'data-set-list-style="{style}"' in res_public.text
        for label in ("☰ 목록<", "≡ 조밀하게<", "▤ 일반<", "▭ 넓게<"):
            assert label in res_public.text
        for paren in ("(리스트)", "(컴팩트)", "(카드)", "(루미)"):
            assert paren not in res_public.text
        assert 'en: "Compact"' in res_public.text
        assert 'en: "Roomy"' in res_public.text

        res_head = client.head("/syndication", headers=headers)
        assert res_head.status_code == 200

        res_prefs = client.get("/syndication/preferences", headers=headers, follow_redirects=False)
        assert res_prefs.status_code == 302
        assert res_prefs.headers["location"] == "/settings/reader"

        res_prefs_head = client.head("/syndication/preferences", headers=headers, follow_redirects=False)
        assert res_prefs_head.status_code == 302
        assert res_prefs_head.headers["location"] == "/settings/reader"


def test_prototype_syndication_route_preserves_full_prototype(syndication_env) -> None:
    with TestClient(syndication_env, base_url="http://127.0.0.1:8765") as client:
        headers = {"Authorization": f"Bearer {OWNER_TOKEN}"}
        res_proto = client.get("/prototype/syndication", headers=headers)
        assert res_proto.status_code == 200
        assert "Claire Bible — Syndication Prototype" in res_proto.text
        assert 'href="/syndication"' in res_proto.text
        assert "수집 원문" in res_proto.text
        assert "적재 본문" in res_proto.text
        assert "부스트 / 인용 발행" in res_proto.text
        assert 'data-app-tab="client"' in res_proto.text
        assert 'id="app-tablist"' in res_proto.text
        assert 'id="panel-client"' in res_proto.text
        assert 'id="panel-kb"' in res_proto.text
        assert "연합 클라이언트" in res_proto.text
        assert "CLAIRE_SYND_CLIENT_FACTORY" in res_proto.text
        assert "/api/v1/statuses" in res_proto.text
        assert "com.atproto.repo.createRecord" in res_proto.text
        assert 'id="modal-connect"' in res_proto.text
        assert "CLAIRE_SYND_AP_FEATURES" in res_proto.text
        assert "accountSupports" in res_proto.text
        assert "checkMastodonBaseline" not in res_proto.text
        assert 'missingFeatures: ["trends", "quote", "translate", "notifRequests", "scheduled"]' in res_proto.text
        for endpoint in (
            "/api/v2/notifications",
            "/api/v1/notifications/requests",
            "/api/v1/polls/",
            "/api/v1/trends/",
            "/api/v2/search",
            "/api/v1/reports",
            "/api/v2/filters",
            "/api/v1/scheduled_statuses",
            "/api/v1/conversations",
            "quote_approval_policy",
            "scheduled_at",
            "spoiler_text",
        ):
            assert endpoint in res_proto.text, endpoint
        assert 'id="pop-post-actions"' in res_proto.text
        assert 'id="modal-report"' in res_proto.text
        assert "CLAIRE_SYND_PEERS_FACTORY" in res_proto.text
        assert "window.CLAIRE_SYND_PEERS = {" in res_proto.text
        assert 'id="tree-peers"' in res_proto.text
        assert 'id="modal-reshare"' in res_proto.text
        assert 'data-peer-view="current"' in res_proto.text
        assert "GET /api/peers/publications" in res_proto.text
        for mode in ("boost", "quote", "link", "import_publish"):
            assert f"{mode}: {{ icon:" in res_proto.text, mode
        assert res_proto.text.index("CLAIRE_SYND_PEERS_FACTORY = function") < res_proto.text.index("window.CLAIRE_SYND_PEERS_FACTORY({")
        assert client.head("/prototype/syndication", headers=headers).status_code == 200


# D4 / design §2.2: legacy ?tab= → /settings/* (or /admin/* for the admin).
LEGACY_TAB_TABLE = [
    (None, "/settings/reader", "/settings/reader"),
    ("reader", "/settings/reader", "/settings/reader"),
    ("accounts", "/settings/connections", "/settings/connections"),
    ("feeds", "/settings/feeds", "/settings/feeds"),
    ("tags", "/settings/tags", "/settings/tags"),
    ("rules", "/settings/rules", "/settings/rules"),
    ("outbound", "/settings/outbound", "/settings/outbound"),
    ("opml", "/settings/opml", "/settings/opml"),
    ("mcp", "/settings/mcp", "/settings/mcp"),
    ("webmcp", "/settings/webmcp", "/settings/webmcp"),
    ("idp", "/admin/idp", "/settings/account"),
    ("userdb", "/admin/accounts", "/settings/account"),
]


def _legacy_prefs_url(tab: str | None, theme: str | None = None) -> str:
    params = []
    if tab is not None:
        params.append(f"tab={tab}")
    if theme is not None:
        params.append(f"theme={theme}")
    return "/syndication/preferences" + ("?" + "&".join(params) if params else "")


@pytest.mark.parametrize("tab, admin_target, _member_target", LEGACY_TAB_TABLE)
def test_legacy_preferences_tab_redirects_admin(syndication_env, tab, admin_target, _member_target) -> None:
    with TestClient(syndication_env, base_url="http://127.0.0.1:8765") as client:
        headers = {"Authorization": f"Bearer {OWNER_TOKEN}"}
        for method in ("GET", "HEAD"):
            res = client.request(method, _legacy_prefs_url(tab), headers=headers, follow_redirects=False)
            assert res.status_code == 302, (method, tab)
            assert res.headers["location"] == admin_target, (method, tab)
            assert res.headers["cache-control"] == "no-store"
            assert "@" not in res.headers["location"]


def test_legacy_preferences_preserves_valid_theme_for_settings_targets(syndication_env) -> None:
    with TestClient(syndication_env, base_url="http://127.0.0.1:8765") as client:
        headers = {"Authorization": f"Bearer {OWNER_TOKEN}"}
        base = client.get("/syndication/preferences?tab=feeds&theme=0", headers=headers, follow_redirects=False)
        assert base.status_code == 302
        location = base.headers["location"]
        assert location.startswith("/settings/feeds?theme=")
        theme_ref = location.split("?theme=", 1)[1]
        assert theme_ref
        # The console accepts the canonical ref it gets.
        console = client.get(location, headers=headers, follow_redirects=False)
        assert console.status_code == 200
        # Admin-only targets are not theme-scoped: ?theme= is dropped.
        idp = client.get("/syndication/preferences?tab=idp&theme=0", headers=headers, follow_redirects=False)
        assert idp.status_code == 302 and idp.headers["location"] == "/admin/idp"


@pytest.mark.parametrize("url", [
    "/syndication/preferences?tab=nope",
    "/syndication/preferences?tab=",
    "/syndication/preferences?tab=READER",
    "/syndication/preferences?theme=nope",
    "/syndication/preferences?tab=reader&theme=nope",
    "/syndication/preferences?tab=nope&theme=0",
])
def test_legacy_preferences_unknown_tab_or_theme_is_404(syndication_env, url) -> None:
    with TestClient(syndication_env, base_url="http://127.0.0.1:8765") as client:
        headers = {"Authorization": f"Bearer {OWNER_TOKEN}"}
        res = client.get(url, headers=headers, follow_redirects=False)
        assert res.status_code == 404, url
        assert "location" not in res.headers


@pytest.mark.parametrize("token", [COLLAB_TOKEN, READONLY_TOKEN, None])
def test_legacy_preferences_requires_owner(syndication_env, token) -> None:
    with TestClient(syndication_env, base_url="http://127.0.0.1:8765") as client:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        for tab in ("reader", "idp", "nope"):
            res = client.get(f"/syndication/preferences?tab={tab}", headers=headers, follow_redirects=False)
            assert res.status_code in {401, 403, 404}, (token, tab)
            assert "location" not in res.headers
