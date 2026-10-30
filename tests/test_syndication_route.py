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
        res_public = client.get("/syndication")
        assert res_public.status_code == 200
        assert "Claire Bible — Syndication" in res_public.text
        assert "authScope: 'public'" in res_public.text
        assert 'data-layout="three-panel"' in res_public.text
        assert 'data-list-style="list"' in res_public.text
        assert 'id="btn-toggle-list-style"' in res_public.text
        assert 'id="btn-toggle-layout"' in res_public.text
        assert 'id="headlines-container"' in res_public.text
        assert "/syndication/preferences" in res_public.text
        assert "수집 원문" in res_public.text
        assert "적재 본문" in res_public.text
        assert "부스트 / 인용 발행" in res_public.text

        # Tab menu bar below the top bar: federated client (tab 1) and knowledge base (tab 2).
        assert 'data-app-tab="client"' in res_public.text
        assert 'id="app-tablist"' in res_public.text
        assert 'id="panel-client"' in res_public.text
        assert 'id="panel-kb"' in res_public.text
        assert "연합 클라이언트" in res_public.text
        assert "CLAIRE_SYND_CLIENT_FACTORY" in res_public.text

        # Four list styles: list / compact / card / roomy.
        for style in ("list", "compact", "card", "roomy"):
            assert f'data-set-list-style="{style}"' in res_public.text
        # Labels show only the localized name; English names live in the i18n table.
        for label in ("☰ 목록<", "≡ 조밀하게<", "▤ 일반<", "▭ 넓게<"):
            assert label in res_public.text
        for paren in ("(리스트)", "(컴팩트)", "(카드)", "(루미)"):
            assert paren not in res_public.text
        assert 'en: "Compact"' in res_public.text
        assert 'en: "Roomy"' in res_public.text

        # ActivityPub and AT go through connected server accounts, not a self-hosted actor.
        assert "/api/v1/statuses" in res_public.text
        assert "com.atproto.repo.createRecord" in res_public.text
        assert 'id="modal-connect"' in res_public.text

        # ActivityPub client features follow the Mastodon 5.0+ client feature set; servers are never
        # refused, and features a server lacks are disabled through capability flags.
        assert "CLAIRE_SYND_AP_FEATURES" in res_public.text
        assert "accountSupports" in res_public.text
        assert "checkMastodonBaseline" not in res_public.text
        assert 'missingFeatures: ["trends", "quote", "translate", "notifRequests", "scheduled"]' in res_public.text
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
            assert endpoint in res_public.text, endpoint
        assert 'id="pop-post-actions"' in res_public.text
        assert 'id="modal-report"' in res_public.text

        # Other users' published bodies (proposal): both tabs read the shared publication index and
        # republish through the viewer's own connected accounts.
        assert "CLAIRE_SYND_PEERS_FACTORY" in res_public.text
        assert "window.CLAIRE_SYND_PEERS = {" in res_public.text
        assert 'id="tree-peers"' in res_public.text
        assert 'id="modal-reshare"' in res_public.text
        assert 'data-peer-view="current"' in res_public.text
        assert "GET /api/peers/publications" in res_public.text
        for mode in ("boost", "quote", "link", "import_publish"):
            assert f"{mode}: {{ icon:" in res_public.text, mode
        assert res_public.text.index("CLAIRE_SYND_PEERS_FACTORY = function") < res_public.text.index("window.CLAIRE_SYND_PEERS_FACTORY({")

        res_head = client.head("/syndication")
        assert res_head.status_code == 200

        res_prefs = client.get("/syndication/preferences")
        assert res_prefs.status_code == 200
        assert "Syndication 환경설정" in res_prefs.text
        assert "태그 = 엔티티 적재 정책" in res_prefs.text
        assert '<option value="list">목록 — 한 줄 행</option>' in res_prefs.text
        assert '<option value="card">일반 — 요약 2줄 미리보기</option>' in res_prefs.text
        assert "(리스트)" not in res_prefs.text
        assert "ActivityPub 클라이언트 기능 기준: Mastodon 5.0 이상" in res_prefs.text
        assert "Mastodon 5.0 기준 클라이언트 기능" in res_prefs.text
        assert 'id="pref-list-style"' in res_prefs.text
        assert 'id="pref-layout-mode"' in res_prefs.text
        assert 'id="pref-app-tab"' in res_prefs.text
        assert 'id="panel-accounts"' in res_prefs.text
        assert "연결 서버 계정" in res_prefs.text
        assert 'id="panel-mcp"' in res_prefs.text
        assert 'id="panel-webmcp"' in res_prefs.text
        assert 'id="pref-mcp-tools-tbody"' in res_prefs.text
        assert 'id="pref-mcp-clients-tbody"' in res_prefs.text
        assert 'id="pref-webmcp-tools-tbody"' in res_prefs.text
        assert 'id="btn-webmcp-sim-run"' in res_prefs.text
        assert "document.modelContext" in res_prefs.text
        assert "requestUserInteraction" not in res_prefs.text
        assert "consequentialHint" in res_prefs.text
        assert "/.well-known/oauth-protected-resource" in res_prefs.text
        assert "syndication:draft" in res_prefs.text
        assert 'id="pref-mcp-rbac-tbody"' in res_prefs.text
        assert 'id="pref-mcp-apitokens-tbody"' in res_prefs.text
        assert 'id="panel-idp"' in res_prefs.text
        assert 'id="panel-userdb"' in res_prefs.text
        assert 'id="pref-idp-tbody"' in res_prefs.text
        assert 'id="btn-idp-sim-run"' in res_prefs.text
        assert 'id="pref-userdb-tbody"' in res_prefs.text
        assert 'id="pref-peer-listed"' in res_prefs.text
        assert 'id="pref-peer-import"' in res_prefs.text
        assert "peerSharePolicy" in res_prefs.text

        res_prefs_head = client.head("/syndication/preferences")
        assert res_prefs_head.status_code == 200
