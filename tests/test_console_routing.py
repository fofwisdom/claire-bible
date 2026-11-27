"""통합 콘솔 라우팅, 권한 경계(D1–D3), whoami.is_admin 및 mcp_telemetry API 테스트 (설계 P1).

모듈 간 인터페이스(ROUTE_POLICY·NamespaceResolverMiddleware·accounts_routes.console)와 SQLite I/O를 검증한다.
모든 상태는 tmp_path에만 기록하고 CLAIRE_* 환경변수를 격리한다.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.kb_paths import kb_number, kb_url, seed  # noqa: F401
from starlette.testclient import TestClient

from claire.api import server
from claire.core.config import Settings
from claire.store import accounts as accounts_store
from claire.store import db as dbm
from claire.store.secrets import reset_secret_service
from claire.store.telemetry import record_mcp_telemetry
from claire.store.knowledge_base import KnowledgeBaseInfo, KnowledgeBaseManager
from tests.auth_helpers import bearer_headers

PUBLIC_URL = "http://127.0.0.1:8765"
SECRET_ADMIN_USERNAME = "hidden_operator_p1"
MEMBER_USERNAME = "alice_member"
OTHER_USERNAME = "bob_member"

NEW_ADMIN_PATHS: tuple[str, ...] = (
    "/admin",
    "/admin/kb",
    "/admin/providers",
    "/admin/limits",
    "/admin/themes",
    "/admin/audit",
    "/admin/mcp",
    "/admin/webmcp",
    "/api/admin/mcp/telemetry",
    "/admin/federation",
    "/moderation/oversight",
    "/moderation/themes",
)

ALL_SETTINGS_SECTION_PATHS: tuple[str, ...] = (
    "/account/profile",
    "/account/security",
    "/account/invites",
    "/account/federation",
    "/account/providers",
    "/settings/kb",
    "/settings/reader",
    "/settings/feeds",
    "/settings/tags",
    "/settings/rules",
    "/settings/opml",
    "/account/mcp",
    "/account/webmcp",
)

MODERATION_OWNER_SECTION_PATHS: tuple[str, ...] = ("/moderation/kb",)


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch):
    reset_secret_service()
    for key in list(os.environ):
        if key.startswith("CLAIRE_"):
            monkeypatch.delenv(key, raising=False)
    yield
    reset_secret_service()


def _upsert_account(conn, account_id: int, public_id: int, username: str, role: str) -> None:
    now = time.time()
    conn.execute(
        "INSERT INTO accounts (id, public_id, username, role, status, display_name, profile_public, created_at, activated_at, updated_at) VALUES (?, ?, ?, ?, 'active', ?, 1, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET public_id=excluded.public_id, username=excluded.username, role=excluded.role, status='active', profile_public=1, updated_at=excluded.updated_at",
        (account_id, public_id, username, role, username.capitalize(), now, now, now),
    )
    conn.commit()


def _insert_theme(tm: KnowledgeBaseManager, settings: Settings, *, kb_id: int, seq: int, number: int, label: str, account_id: int, is_personal: bool) -> KnowledgeBaseInfo:
    conn = dbm.connect(settings.db_file)
    now = time.time()
    try:
        conn.execute(
            "INSERT INTO knowledge_bases (id, seq, number, label, description, icon, is_personal, is_public, account_id, db_path, default_focus, created_at, updated_at) VALUES (?, ?, ?, ?, ?, '📁', ?, 1, ?, ?, '', ?, ?)",
            (kb_id, seq, number, label, f"Desc {label}", 1 if is_personal else 0, account_id, str(settings.db_file), now, now),
        )
        conn.commit()
    finally:
        conn.close()
    tm.reload()
    tm.reload()
    return tm.get_kb(kb_id)


@pytest.fixture
def console_env(tmp_path: Path):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)
    db_file = data_dir / "claire.db"

    conn = dbm.connect(db_file)
    try:
        dbm.init_db(conn)
        _upsert_account(conn, 1, (1 << 57) + 101, SECRET_ADMIN_USERNAME, "admin")
        _upsert_account(conn, 2, (1 << 57) + 202, MEMBER_USERNAME, "member")
        _upsert_account(conn, 3, (1 << 57) + 303, OTHER_USERNAME, "member")
    finally:
        conn.close()

    settings = Settings(
        CLAIRE_DB_PATH=str(db_file),
        CLAIRE_PROVIDER="mock",
        CLAIRE_ENVIRONMENT="development",
        CLAIRE_FQDN=PUBLIC_URL,
    )
    tm = KnowledgeBaseManager(settings)
    admin_extra = _insert_theme(tm, settings, kb_id=11, seq=1, number=(1 << 56) | 11, label="Admin Research", account_id=1, is_personal=False)
    alice_def = _insert_theme(tm, settings, kb_id=20, seq=2, number=(2 << 56) | 20, label="Alice Default", account_id=2, is_personal=True)
    bob_def = _insert_theme(tm, settings, kb_id=30, seq=3, number=(3 << 56) | 30, label="Bob Default", account_id=3, is_personal=True)

    app = server.create_app(settings, SimpleNamespace(provider=SimpleNamespace(name="mock")))
    client = TestClient(app, base_url=PUBLIC_URL)
    return SimpleNamespace(
        data_dir=data_dir,
        db_file=db_file,
        settings=settings,
        client=client,
        admin_extra=admin_extra,
        alice_def=alice_def,
        bob_def=bob_def,
        admin_owner=bearer_headers(db_file, account_id=1, scope="owner"),
        admin_collab=bearer_headers(db_file, account_id=1, scope="write"),
        admin_ro=bearer_headers(db_file, account_id=1, scope="readonly"),
        alice_owner=bearer_headers(db_file, account_id=2, scope="owner"),
        alice_collab=bearer_headers(db_file, account_id=2, scope="write"),
    )


def _set_mode(db_file: Path, mode: str) -> None:
    conn = dbm.connect(db_file)
    try:
        accounts_store.AccountStore(conn).set_setting("mode", mode, updated_by=1)
    finally:
        conn.close()


def test_non_admin_callers_get_404_on_all_admin_console_routes(console_env):
    _set_mode(console_env.db_file, "network")
    client = console_env.client
    for headers in (
        {},
        console_env.admin_ro,
        console_env.admin_collab,
        console_env.alice_owner,
        console_env.alice_collab,
    ):
        for path in NEW_ADMIN_PATHS:
            resp = client.get(path, headers=headers, follow_redirects=False)
            assert resp.status_code == 404, (path, headers, resp.status_code)
            head_resp = client.head(path, headers=headers, follow_redirects=False)
            assert head_resp.status_code == 404, (path, headers, head_resp.status_code)


def test_non_owner_callers_get_404_on_settings_routes(console_env):
    _set_mode(console_env.db_file, "network")
    client = console_env.client
    for path in ("/settings", "/moderation", "/settings/kb/manage", "/settings/themes", *ALL_SETTINGS_SECTION_PATHS, *MODERATION_OWNER_SECTION_PATHS):
        anon = client.get(path, follow_redirects=False)
        assert anon.status_code == 404, path
        for headers in (console_env.admin_ro, console_env.admin_collab, console_env.alice_collab):
            resp = client.get(path, headers=headers, follow_redirects=False)
            assert resp.status_code == 404, (path, resp.status_code)


def test_prefixed_paths_return_404_for_settings_and_admin(console_env):
    _set_mode(console_env.db_file, "network")
    client = console_env.client
    prefixed_paths = (
        f"/@{MEMBER_USERNAME}/settings",
        f"/@{MEMBER_USERNAME}/account/profile",
        f"/@{MEMBER_USERNAME}/settings/reader",
        f"/kb/{console_env.alice_def.number}/settings/reader",
        f"/@{SECRET_ADMIN_USERNAME}/admin",
        f"/@{SECRET_ADMIN_USERNAME}/admin/providers",
        f"/kb/{console_env.admin_extra.number}/admin/limits",
        f"/@{MEMBER_USERNAME}/moderation",
        f"/@{MEMBER_USERNAME}/moderation/kb",
        f"/@{MEMBER_USERNAME}/moderation/kb/{console_env.admin_extra.number}",
        f"/@{SECRET_ADMIN_USERNAME}/moderation/oversight",
        f"/@{SECRET_ADMIN_USERNAME}/moderation/themes",
        f"/kb/{console_env.admin_extra.number}/moderation/oversight",
        f"/kb/{console_env.alice_def.number}/moderation/kb",
    )
    for path in prefixed_paths:
        for headers in (console_env.admin_owner, console_env.alice_owner):
            resp = client.get(path, headers=headers, follow_redirects=False)
            assert resp.status_code == 404, (path, resp.status_code)


def test_index_redirects_and_d3_admin_theme_token_redirects(console_env):
    _set_mode(console_env.db_file, "network")
    client = console_env.client

    r_admin = client.get("/admin", headers=console_env.admin_owner, follow_redirects=False)
    assert r_admin.status_code == 302
    assert r_admin.headers["location"] == "/admin/server"
    assert r_admin.headers["referrer-policy"] == "same-origin"

    r_account = client.get("/account", headers=console_env.alice_owner, follow_redirects=False)
    assert (r_account.status_code, r_account.headers["location"]) == (302, "/account/profile")
    assert client.head("/account", headers=console_env.alice_owner, follow_redirects=False).status_code == 302

    r_settings = client.get("/settings", headers=console_env.alice_owner, follow_redirects=False)
    assert r_settings.status_code == 302
    assert r_settings.headers["location"] == "/settings/reader"

    r_moderation = client.get("/moderation", headers=console_env.alice_owner, follow_redirects=False)
    assert (r_moderation.status_code, r_moderation.headers["location"]) == (302, "/moderation/kb")
    r_moderation_kb = client.get(f"/moderation?kb={console_env.alice_def.number}", headers=console_env.alice_owner, follow_redirects=False)
    assert (r_moderation_kb.status_code, r_moderation_kb.headers["location"]) == (302, f"/moderation/kb?kb={console_env.alice_def.number}")
    r_moderation_shared = client.get(f"/moderation?kb={console_env.admin_extra.number}", headers=console_env.alice_owner, follow_redirects=False)
    assert (r_moderation_shared.status_code, r_moderation_shared.headers["location"]) == (302, f"/moderation/kb/{console_env.admin_extra.number}")
    assert client.head("/moderation", headers=console_env.alice_owner, follow_redirects=False).status_code == 302

    r_settings_kb = client.get(f"/settings?kb={console_env.alice_def.number}", headers=console_env.alice_owner, follow_redirects=False)
    assert r_settings_kb.status_code == 302
    assert r_settings_kb.headers["location"] == f"/settings/reader?kb={console_env.alice_def.number}"

    # Legacy /settings/themes redirects admin 308 to /moderation/themes; retired token routes return 404.
    for path, status, target in (
        ("/settings/themes", 308, "/moderation/themes"),
        ("/admin/kb", 308, "/moderation/oversight"),
        ("/admin/themes", 308, "/moderation/themes"),
    ):
        adm_redirect = client.get(path, headers=console_env.admin_owner, follow_redirects=False)
        assert (adm_redirect.status_code, adm_redirect.headers["location"]) == (status, target), path
        assert client.head(path, headers=console_env.admin_owner, follow_redirects=False).status_code == status, path
        adm_page = client.get(target, headers=console_env.admin_owner, follow_redirects=False)
        assert adm_page.status_code == 200, target
        assert adm_page.headers["referrer-policy"] == "same-origin"

    for retired in ("/account/tokens", "/admin/tokens", "/settings/tokens"):
        assert client.get(retired, headers=console_env.admin_owner, follow_redirects=False).status_code == 404, retired
        assert client.get(retired, headers=console_env.alice_owner, follow_redirects=False).status_code == 404, retired

    # Legacy /settings/themes redirects regular member to /settings/kb, while /settings/kb and /moderation/kb return 200.
    mem_themes_redirect = client.get("/settings/themes", headers=console_env.alice_owner, follow_redirects=False)
    assert (mem_themes_redirect.status_code, mem_themes_redirect.headers["location"]) == (308, "/settings/kb")
    for legacy_path, target in (("/settings/kb/manage", "/moderation/kb"), (f"/settings/kb/{console_env.admin_extra.number}", f"/moderation/kb/{console_env.admin_extra.number}")):
        for method in ("GET", "HEAD"):
            redir = client.request(method, legacy_path, headers=console_env.alice_owner, follow_redirects=False)
            assert (redir.status_code, redir.headers["location"]) == (308, target), (method, legacy_path)
    assert client.get(f"/settings/kb/{console_env.alice_def.number}", headers=console_env.alice_owner, follow_redirects=False).status_code == 404
    assert client.get("/settings/kb/999999", headers=console_env.alice_owner, follow_redirects=False).status_code == 404
    assert client.get(f"/moderation/kb/{console_env.alice_def.number}", headers=console_env.alice_owner, follow_redirects=False).status_code == 404
    assert client.get("/moderation/kb/999999", headers=console_env.alice_owner, follow_redirects=False).status_code == 404
    for path in ("/settings/kb", "/moderation/kb", f"/moderation/kb/{console_env.admin_extra.number}"):
        mem_page = client.get(path, headers=console_env.alice_owner, follow_redirects=False)
        assert mem_page.status_code == 200, path
        assert mem_page.headers["referrer-policy"] == "same-origin"


def test_old_settings_paths_of_the_account_pages_redirect_with_308(console_env):
    """The old ``/settings/*`` path of each ``/account/*`` page answers an owner with a 308 redirect and keeps the query; a caller that is not an owner gets 404."""
    from claire.api.accounts_routes import console as console_routes

    _set_mode(console_env.db_file, "network")
    client = console_env.client
    redirects = console_routes.LEGACY_SETTINGS_ACCOUNT_REDIRECTS
    assert redirects["/settings/account"] == "/account/profile" and redirects["/settings/keys"] == "/account/providers" and len(redirects) == 11
    # The bundles page moved to /support/bundles: its two old paths go there.
    assert redirects["/settings/bundles"] == redirects["/account/bundles"] == "/support/bundles"
    assert set(redirects.values()) <= {spec.path for spec in (*console_routes.ALL_ACCOUNT_SPECS, *console_routes.SUPPORT_SPECS)}
    for old, new in redirects.items():
        for method in ("GET", "HEAD"):
            redir = client.request(method, old, headers=console_env.alice_owner, follow_redirects=False)
            assert (redir.status_code, redir.headers["location"]) == (308, new), (method, old)
        assert client.get(old, headers=console_env.alice_collab, follow_redirects=False).status_code == 404, old
    with_query = client.get(f"/settings/mcp?kb={console_env.alice_def.number}", headers=console_env.alice_owner, follow_redirects=False)
    assert (with_query.status_code, with_query.headers["location"]) == (308, f"/account/mcp?kb={console_env.alice_def.number}")
    assert client.get("/settings/tokens", headers=console_env.admin_owner, follow_redirects=False).status_code == 404


def test_sidebar_groups_put_the_account_pages_before_the_knowledge_base_pages(console_env):
    """The sidebar has the two account groups (workspace, ai) first, then the groups of ``/settings/*``; the AI group has the provider, MCP, and WebMCP pages."""
    import re

    _set_mode(console_env.db_file, "network")
    html = console_env.client.get("/account/providers", headers=console_env.alice_owner).text
    sidebar = html.split('<nav class="console-sidebar"', 1)[1].split("</nav>", 1)[0]
    assert re.findall(r'data-nav-group="([a-z_]+)"', sidebar) == ["workspace", "ai", "kb", "syndication", "support"]
    groups = {m.group(1): re.findall(r'href="([^"?]+)', m.group(2)) for m in re.finditer(r'data-nav-group="([a-z_]+)">(.*?)</ul>', sidebar, re.S)}
    assert groups["workspace"] == ["/account/profile", "/account/security", "/account/invites", "/account/federation", "/account/telegram", "/account/notifications", "/account/backups"]
    assert groups["support"] == ["/support/bundles", "/support/pipeline"]
    assert groups["ai"] == ["/account/providers", "/account/mcp", "/account/webmcp"]
    assert groups["kb"] == ["/settings/kb"] and groups["syndication"][0] == "/settings/reader" and len(groups["syndication"]) == 5
    assert 'data-active-area="account"' in html and 'data-nav-section="providers" aria-current="page"' in html


def test_strict_theme_query_validation_rejects_unknown_or_cross_account_theme(console_env):
    _set_mode(console_env.db_file, "network")
    client = console_env.client

    # Unknown theme ref -> 404 before any redirect or page render
    assert client.get("/settings?kb=nonexistent", headers=console_env.alice_owner, follow_redirects=False).status_code == 404
    assert client.get("/settings?kb=", headers=console_env.alice_owner, follow_redirects=False).status_code == 404
    assert client.get("/settings/reader?kb=999999", headers=console_env.alice_owner, follow_redirects=False).status_code == 404
    assert client.get("/settings/themes?kb=nonexistent", headers=console_env.admin_owner, follow_redirects=False).status_code == 404
    assert client.get("/moderation?kb=nonexistent", headers=console_env.alice_owner, follow_redirects=False).status_code == 404
    assert client.get("/moderation/kb?kb=nonexistent", headers=console_env.alice_owner, follow_redirects=False).status_code == 404
    assert client.get("/moderation/oversight?kb=nonexistent", headers=console_env.admin_owner, follow_redirects=False).status_code == 404
    assert client.get("/admin/providers?kb=nonexistent", headers=console_env.admin_owner, follow_redirects=False).status_code == 404

    # Cross-account theme ref (Alice requesting Bob's theme or Admin's theme) -> 404
    assert client.get(f"/settings/reader?kb={console_env.bob_def.number}", headers=console_env.alice_owner, follow_redirects=False).status_code == 404
    assert client.get(f"/settings/reader?kb={console_env.admin_extra.number}", headers=console_env.alice_owner, follow_redirects=False).status_code == 404

    # Own theme ref -> 200 and selector reflects theme
    ok_resp = client.get(f"/settings/reader?kb={console_env.alice_def.number}", headers=console_env.alice_owner, follow_redirects=False)
    assert ok_resp.status_code == 200
    assert str(console_env.alice_def.number) in ok_resp.text


def test_unified_sidebar_and_strict_server_side_admin_dom_stripping(console_env):
    _set_mode(console_env.db_file, "network")
    client = console_env.client

    # Member response on /settings/reader keeps settings sidebar without /moderation/* links; /moderation/kb keeps only moderation sidebar.
    member_settings = client.get("/settings/reader", headers=console_env.alice_owner)
    assert member_settings.status_code == 200
    assert 'href="/account/profile"' in member_settings.text
    assert 'href="/settings/kb"' in member_settings.text
    assert 'href="/account/tokens"' not in member_settings.text
    assert 'href="/settings/feeds"' in member_settings.text
    assert 'href="/moderation/kb"' not in member_settings.text
    assert 'href="/moderation/oversight"' not in member_settings.text
    assert 'href="/moderation/themes"' not in member_settings.text
    assert "/admin/" not in member_settings.text
    assert "console_admin.js" not in member_settings.text
    assert "console.js.admin." not in member_settings.text

    member_mod = client.get("/moderation/kb", headers=console_env.alice_owner)
    assert member_mod.status_code == 200
    mod_sidebar = member_mod.text.split('<nav class="console-sidebar"', 1)[1].split("</nav>", 1)[0]
    assert 'href="/moderation/kb"' in mod_sidebar
    assert 'href="/account/profile"' not in mod_sidebar
    assert 'href="/settings/kb"' not in mod_sidebar
    assert "/admin/" not in member_mod.text
    assert 'href="/moderation/oversight"' not in member_mod.text
    assert 'href="/moderation/themes"' not in member_mod.text
    assert 'href="/settings/kb/manage"' not in member_mod.text
    assert "console_admin.js" not in member_mod.text
    assert "console.js.admin." not in member_mod.text
    assert "System administration and settings (administrator)" not in member_mod.text
    assert "Server · Accounts · Authentication (administrator)" not in member_mod.text

    # Active highlighting for /moderation/kb and /moderation/kb/{kb_number}
    for active_mod_path in ("/moderation/kb", f"/moderation/kb/{console_env.admin_extra.number}"):
        mod_html = client.get(active_mod_path, headers=console_env.alice_owner).text
        assert 'data-active-area="moderation"' in mod_html
        assert 'data-active-section="kb_manage"' in mod_html
        assert 'id="settings-kb-manage-host"' in mod_html
        assert 'data-nav-section="kb_manage" aria-current="page"' in mod_html

    # Common bottom hamburger section order across workspace, syndication, settings, admin, and moderation.
    for path in ("/settings/reader", "/admin/providers", "/moderation/kb", "/moderation/oversight"):
        resp = client.get(path, headers=console_env.admin_owner)
        assert resp.status_code == 200, path
        html = resp.text
        pos_synd = html.index('id="menusyndlink"')
        pos_mod = html.index('id="moderationlinkbtn"')
        pos_con = html.index('id="consolelinkbtn"')
        pos_out = html.index('id="menu-logout-form"')
        assert pos_synd < pos_mod < pos_con < pos_out, path

    # Admin response separates /settings/* & /admin/* console sidebar from /moderation/* moderation sidebar.
    for path in ("/settings/reader", "/admin/providers", "/admin/limits", "/admin/mcp"):
        admin_resp = client.get(path, headers=console_env.admin_owner)
        assert admin_resp.status_code == 200, path
        sidebar = admin_resp.text.split('<nav class="console-sidebar"', 1)[1].split("</nav>", 1)[0]
        assert 'href="/account/profile"' in sidebar
        assert 'href="/settings/feeds"' in sidebar
        assert 'href="/admin/server"' in sidebar
        assert 'href="/admin/accounts"' in sidebar
        assert 'href="/admin/idp"' in sidebar
        assert 'href="/admin/providers"' in sidebar
        assert 'href="/admin/limits"' in sidebar
        assert 'href="/admin/tokens"' not in sidebar
        assert 'href="/admin/audit"' in sidebar
        assert 'href="/admin/mcp"' in sidebar
        assert 'href="/admin/webmcp"' in sidebar
        assert 'href="/moderation/kb"' not in sidebar
        assert 'href="/moderation/oversight"' not in sidebar
        assert 'href="/moderation/themes"' not in sidebar
        assert 'href="/admin/kb"' not in admin_resp.text
        assert 'href="/admin/themes"' not in admin_resp.text
        assert 'href="/settings/kb/manage"' not in admin_resp.text
        assert 'href="/settings/themes"' not in admin_resp.text
        assert 'href="/account/tokens"' not in admin_resp.text

    for path in ("/moderation/kb", "/moderation/oversight", "/moderation/themes"):
        admin_mod = client.get(path, headers=console_env.admin_owner)
        assert admin_mod.status_code == 200, path
        mod_sb = admin_mod.text.split('<nav class="console-sidebar"', 1)[1].split("</nav>", 1)[0]
        assert 'href="/moderation/kb"' in mod_sb
        assert 'href="/moderation/oversight"' in mod_sb
        assert 'href="/moderation/themes"' in mod_sb
        assert 'href="/account/profile"' not in mod_sb
        assert 'href="/admin/server"' not in mod_sb
        if path == "/moderation/kb":
            assert "console_admin.js" not in admin_mod.text
            assert "console.js.admin." not in admin_mod.text


def test_single_mode_shows_the_admin_username_only_on_the_account_page_and_not_in_whoami(console_env):
    _set_mode(console_env.db_file, "single")
    client = console_env.client

    # Rule S9 is withdrawn (KNOWLEDGE_FEDERATION_DESIGN.md Section 15 rule 4): the account page shows the USERNAME in both modes.
    account_page = client.get("/account/profile", headers=console_env.admin_owner, follow_redirects=False)
    assert account_page.status_code == 200
    assert f"@{SECRET_ADMIN_USERNAME}" in account_page.text

    for path in ("/admin/providers", "/moderation/themes", "/moderation/oversight", "/settings/reader"):
        resp = client.get(path, headers=console_env.admin_owner, follow_redirects=False)
        assert resp.status_code == 200, path
        assert SECRET_ADMIN_USERNAME not in resp.text
        assert all(SECRET_ADMIN_USERNAME not in v for v in resp.headers.values())
        assert 'name="claire-storage-key" content="a' in resp.text

    who_admin = client.get("/whoami", headers=console_env.admin_owner)
    assert who_admin.status_code == 200
    assert who_admin.json() == {"scope": "owner", "server": {"mode": "single"}, "relation": "owner", "is_admin": True}
    assert SECRET_ADMIN_USERNAME not in who_admin.text

    _set_mode(console_env.db_file, "network")
    who_alice = client.get(f"/@{MEMBER_USERNAME}/whoami", headers=console_env.alice_owner)
    assert who_alice.status_code == 200
    assert who_alice.json() == {"scope": "owner", "server": {"mode": "network"}, "relation": "owner", "is_admin": False}


def test_admin_mcp_telemetry_api_io_and_validation(console_env):
    record_mcp_telemetry(
        console_env.data_dir,
        call_type="tool_call",
        tool_name="search_knowledge",
        status="success",
        duration_ms=12.5,
        response_bytes=512,
        client_id="claude-code",
    )
    record_mcp_telemetry(
        console_env.data_dir,
        call_type="auth_check",
        tool_name=None,
        status="auth_error",
        duration_ms=1.2,
        error_code="invalid_token",
    )

    client = console_env.client
    res = client.get("/api/admin/mcp/telemetry?limit=10", headers=console_env.admin_owner)
    assert res.status_code == 200
    body = res.json()
    assert body["count"] == 2
    assert len(body["records"]) == 2
    assert {r["status"] for r in body["records"]} == {"success", "auth_error"}

    res_filtered = client.get("/api/admin/mcp/telemetry?status=success&tool_name=search_knowledge", headers=console_env.admin_owner)
    assert res_filtered.status_code == 200
    assert res_filtered.json()["count"] == 1
    assert res_filtered.json()["records"][0]["tool_name"] == "search_knowledge"

    for bad_limit in ("0", "-5", "201", "abc", ""):
        bad = client.get(f"/api/admin/mcp/telemetry?limit={bad_limit}", headers=console_env.admin_owner)
        assert bad.status_code == 400
        assert bad.json()["error"] == "invalid_limit"

    # C1: 해석하지 못한 since는 조건을 빼고 전체를 돌려주지 않고 400이다.
    for bad_since in ("yesterday", "", "1700000000"):
        bad = client.get("/api/admin/mcp/telemetry", params={"since": bad_since}, headers=console_env.admin_owner)
        assert bad.status_code == 400, bad_since
        assert bad.json()["error"] == "invalid_since"
    future = client.get("/api/admin/mcp/telemetry", params={"since": "2999-01-01T00:00:00Z"}, headers=console_env.admin_owner)
    assert future.status_code == 200
    assert future.json()["count"] == 0
    past = client.get("/api/admin/mcp/telemetry", params={"since": "2000-01-01T00:00:00+00:00"}, headers=console_env.admin_owner)
    assert past.status_code == 200
    assert past.json()["count"] == 2


def test_admin_audit_page_shows_admin_audit_rows_and_the_mcp_tab(console_env):
    """/admin/audit reads admin_audit from auth.db and shows the current USERNAME of each account; the MCP call records are the second tab."""
    from claire.store import auth_db

    client = console_env.client
    empty = client.get("/admin/audit", headers=console_env.admin_owner)
    assert empty.status_code == 200
    assert "data-admin-audit-row" not in empty.text
    auth = auth_db.connect_auth_db(auth_db.auth_db_path(console_env.data_dir))
    try:
        auth_db.record_admin_audit(auth, actor_account_id=1, action="test.audit_action", target_account_id=987654, detail={"domain": "example.org"})
    finally:
        auth.close()
    body = client.get("/admin/audit", headers=console_env.admin_owner).text
    rows = body.split('id="admin-audit-actions-tbody"', 1)[1].split("</tbody>", 1)[0]
    assert rows.count("data-admin-audit-row") == 1
    assert "test.audit_action" in rows
    assert "(#1)" in rows
    assert "#987654 (no account)" in rows
    assert "example.org" in rows
    tabs = body.split('id="admin-audit-tabs"', 1)[1]
    assert 'data-tab="admin" aria-selected="true"' in tabs
    assert 'data-tab="mcp" aria-selected="false"' in tabs
    assert '<div role="tabpanel" data-tabpanel="mcp" hidden="until-found">' in tabs
    assert 'id="pref-mcp-audit-tbody"' in tabs


def test_all_eight_admin_sections_render_unified_shell_and_preserve_member_theme_privacy(console_env):
    _set_mode(console_env.db_file, "network")
    client = console_env.client

    expected_anchors = {
        "server": ("admin", "/admin/server", 'id="server-mode"', 'id="server-tier"'),
        "accounts": ("admin", "/admin/accounts", 'id="admin-members-section"', 'id="pref-userdb-admin"'),
        "idp": ("admin", "/admin/idp", 'id="pref-idp-tbody"', 'id="pref-idp-add-form"'),
        "providers": ("admin", "/admin/providers", 'id="admin-providers-host"', 'data-admin-panel-host="providers"'),
        "limits": ("admin", "/admin/limits", 'id="admin-limits-host"', 'data-admin-panel-host="limits"'),
        "kb": ("moderation", "/moderation/oversight", 'id="admin-kb-host"', 'id="admin-kb-card"'),
        "themes": ("moderation", "/moderation/themes", 'id="admin-themes-host"', 'data-admin-panel-host="themes"'),
        "audit": ("admin", "/admin/audit", 'id="admin-audit-actions-tbody"', 'id="pref-mcp-audit-tbody"'),
        "mcp": ("admin", "/admin/mcp", 'id="pref-mcp-rbac-tbody"', 'id="admin-mcp-rbac-card"'),
        "webmcp": ("admin", "/admin/webmcp", 'id="admin-webmcp-policy-card"', 'id="admin-webmcp-enabled"'),
    }

    for slug, (area, path, anchor_a, anchor_b) in expected_anchors.items():
        resp = client.get(path, headers=console_env.admin_owner)
        assert resp.status_code == 200, path
        assert resp.headers.get("referrer-policy") == "same-origin", path
        body = resp.text
        assert f'data-active-area="{area}"' in body, path
        assert f'data-active-section="{slug}"' in body, path
        assert '<script src="/static/js/manage_panels.js" defer></script>' in body, path
        assert '<script src="/static/js/console_admin.js" defer></script>' in body, path
        assert anchor_a in body, (path, anchor_a)
        assert anchor_b in body, (path, anchor_b)
        if slug == "themes":
            # D5/K5: /moderation/themes 요약 카드는 집계 COUNT(*)만 표시하며 멤버 테마 라벨을 절대 노출하지 않는다.
            assert "Alice Default" not in body
            assert "Bob Default" not in body
        if slug == "mcp":
            # The WebMCP server policy is on /admin/webmcp only. The MCP call audit records are on /admin/audit only.
            assert "admin-webmcp" not in body
            assert "pref-mcp-audit-tbody" not in body
            # The support bundles of the server are on /support/bundles (one page for all bundles).
            assert "data-support-bundle-form" not in body
        if slug == "webmcp":
            # The page has only the server policy. The account settings of WebMCP (id pref-webmcp-*) are on /account/webmcp.
            assert "pref-webmcp-" not in body and "/account/webmcp" in body



ADMIN_PROTOTYPE_MARKERS: dict[str, tuple[tuple[str, str], ...]] = {
    "/admin/accounts": (("admin-accounts-byok-switch", "prototype"), ("admin-accounts-isolation-table", "static")),
    "/admin/idp": (("admin-idp-login-policy", "prototype"), ("admin-idp-login-simulator", "simulation")),
    "/admin/mcp": (("admin-mcp-rbac-table", "static"),),
    "/admin/webmcp": (("admin-webmcp-global-switch", "client_only"),),
}


def test_admin_prototype_elements_carry_visible_server_rendered_labels(console_env):
    """실제 서버 설정에 닿지 않는 /admin/* 요소는 서버가 그린 표식(data-prototype)과 안내문을 단다. /settings/*에는 표식이 없다."""
    _set_mode(console_env.db_file, "network")
    client = console_env.client
    labels = {"prototype": "Prototype", "simulation": "Simulation", "client_only": "Stored in the browser only", "static": "Fixed text"}
    assert ".proto-badge{" in client.get("/static/css/console_ui.css").text
    for path, markers in ADMIN_PROTOTYPE_MARKERS.items():
        body = client.get(path, headers=console_env.admin_owner).text
        assert "/static/css/console_ui.css" in body, path
        for marker, kind in markers:
            badge = f'<span class="proto-badge" data-prototype="{marker}" data-prototype-kind="{kind}">{labels[kind]}</span>'
            assert badge in body, (path, marker)
            # 격리 설명표는 표 caption 안에 안내문을 함께 둔다. 나머지는 별도 안내문(무엇을 하지 않는지)을 단다.
            if marker == "admin-accounts-isolation-table":
                assert "It does not read the real state of the directories and the databases." in body
            else:
                assert f'data-prototype-note="{marker}"' in body, (path, marker)
    # 실제로 동작하는 화면에는 표식을 달지 않는다.
    for path in ("/admin/server", "/admin/providers", "/admin/limits", "/moderation/themes", "/moderation/oversight"):
        assert "data-prototype=" not in client.get(path, headers=console_env.admin_owner).text, path
    for path in (*ALL_SETTINGS_SECTION_PATHS, *MODERATION_OWNER_SECTION_PATHS):
        resp = client.get(path, headers=console_env.alice_owner, follow_redirects=False)
        assert resp.status_code == 200, path
        assert "data-prototype" not in resp.text, path
        assert 'data-active-area="admin"' not in resp.text, path
        assert "console_admin.js" not in resp.text, path
        assert "console.js.admin." not in resp.text, path


def test_each_console_page_follows_the_interface_locale(console_env):
    """I18N design unit IP2-2 (end to end): the server renders each console page, administrator and member, in the locale of the request. An English page has no Korean text."""
    import re

    from claire.api.accounts_routes import console as console_routes

    _set_mode(console_env.db_file, "network")
    client = console_env.client
    hangul = re.compile("[가-힣]+")
    catalog = re.compile(r'<script id="claire-i18n-catalog" type="application/json">.*?</script>', re.S)
    # Not interface text: the name of the Korean locale in the language selector, and the label of the default theme (data of the account).
    permitted = {"한국어", "기본", "지식베이스"}
    admin_paths = (
        [spec.path for spec in console_routes.ALL_ADMIN_SPECS]
        + [spec.path for spec in console_routes.MODERATION_ADMIN_SPECS]
        + [spec.path for spec in console_routes.MODERATION_OWNER_SPECS]
        + [spec.path for spec in (*console_routes.ALL_ACCOUNT_SPECS, *console_routes.ALL_SETTINGS_SPECS) if spec.slug not in {"themes", "tokens"}]
    )
    member_paths = [spec.path for spec in (*console_routes.ALL_ACCOUNT_SPECS, *console_routes.ALL_SETTINGS_SPECS)] + [spec.path for spec in console_routes.MODERATION_OWNER_SPECS]
    assert len(admin_paths) >= 20 and len(member_paths) >= 15
    for headers, paths in ((console_env.admin_owner, admin_paths), (console_env.alice_owner, member_paths)):
        for path in paths:
            english = client.get(path, headers={**headers, "Accept-Language": "en"})
            assert english.status_code == 200, path
            assert '<html lang="en">' in english.text, path
            left = set(hangul.findall(catalog.sub("", english.text))) - permitted
            assert not left, (path, sorted(left)[:8])
            korean = client.get(path, headers={**headers, "Accept-Language": "ko"})
            assert korean.status_code == 200 and '<html lang="ko">' in korean.text and hangul.search(catalog.sub("", korean.text)), path


def test_modern_console_shell_markup_and_security_boundaries(console_env):
    """Console shell emits collapsible groups, command palette, drawer, and preserves member/admin stripping."""
    _set_mode(console_env.db_file, "network")
    client = console_env.client

    member_html = client.get("/account/profile", headers=console_env.alice_owner).text
    assert '<meta name="text-scale" content="scale"/>' in member_html
    assert 'data-nav-group="workspace"' in member_html
    assert 'data-nav-group-toggle="workspace"' in member_html
    assert 'id="console-grp-workspace"' in member_html
    assert 'id="console-nav-drawer"' in member_html
    assert 'id="palettetrigger"' in member_html
    assert 'id="cmdpalette"' in member_html
    assert '/static/js/appbar.js' in member_html
    assert '/static/js/command_palette.js' in member_html
    assert '/static/js/console_shell.js' in member_html
    assert 'id="authstate"' not in member_html
    # Common top-right icon order (left to right: user home, syndication, ingest queue, theme, hamburger) and hamburger menu links.
    assert member_html.index('id="homelinkbtn"') < member_html.index('id="syndlink"') < member_html.index('id="queuebtn"') < member_html.index('id="themebtn"') < member_html.index('id="hamburger-btn"')
    assert 'data-icon="rss"' in member_html[member_html.index('id="syndlink"'):member_html.index('id="queuebtn"')]
    assert 'data-icon="layers"' in member_html[member_html.index('id="queuebtn"'):member_html.index('id="themebtn"')]
    assert 'data-icon="rss"' in member_html[member_html.index('id="menusyndlink"'):member_html.index('id="moderationlinkbtn"')]
    assert member_html.index('id="pop-hamburger-menu"') < member_html.index('id="consolelinkbtn"') < member_html.index('action="/logout"')
    assert '/admin/' not in member_html
    assert 'data-nav-group="admin_server"' not in member_html
    assert 'console_admin.js' not in member_html

    admin_html = client.get("/admin/server", headers=console_env.admin_owner).text
    assert 'data-nav-group="admin_server"' in admin_html
    assert 'data-nav-group-toggle="admin_system"' in admin_html
    assert 'console_admin.js' in admin_html
    assert 'id="palettetrigger"' in admin_html
    assert admin_html.index('id="homelinkbtn"') < admin_html.index('id="syndlink"') < admin_html.index('id="queuebtn"') < admin_html.index('id="themebtn"') < admin_html.index('id="hamburger-btn"')
    assert 'data-icon="rss"' in admin_html[admin_html.index('id="syndlink"'):admin_html.index('id="queuebtn"')]
    assert 'data-icon="layers"' in admin_html[admin_html.index('id="queuebtn"'):admin_html.index('id="themebtn"')]
    assert 'data-icon="rss"' in admin_html[admin_html.index('id="menusyndlink"'):admin_html.index('id="moderationlinkbtn"')]


def test_settings_invites_bootstrap_isolation_and_bottom_list(console_env):
    import json as _json
    import re as _re

    def _read_bootstrap(html_text: str) -> dict:
        m = _re.search(r'<script id="console-bootstrap" type="application/json">(.*?)</script>', html_text, _re.S)
        assert m is not None
        return _json.loads(m.group(1))

    client = console_env.client

    # Single mode: 200 notice, no issuance form, no console_admin.js
    _set_mode(console_env.db_file, "single")
    single_resp = client.get("/account/invites", headers=console_env.admin_owner)
    assert single_resp.status_code == 200
    assert 'id="invites-issue-section"' in single_resp.text
    assert 'id="invites-create-form"' not in single_resp.text
    assert 'id="invites-list-section"' in single_resp.text
    assert 'src="/static/js/console_invites.js"' in single_resp.text
    assert "console_admin.js" not in single_resp.text
    single_boot = _read_bootstrap(single_resp.text)
    assert single_boot["mode"] == "single"
    assert single_boot["can_issue_invites"] is False
    assert single_boot["member_invites_enabled"] is False

    # Network mode, member_invites_enabled=False: member sees disabled notice + isolation; admin sees form
    _set_mode(console_env.db_file, "network")
    mem_off = client.get("/account/invites", headers=console_env.alice_owner)
    assert mem_off.status_code == 200
    assert 'id="invites-create-form"' not in mem_off.text
    assert 'class="pref-notice"' in mem_off.text
    assert "/admin/" not in mem_off.text
    assert "console_admin.js" not in mem_off.text
    assert "console.js.admin." not in mem_off.text
    assert "console.js.invites." in mem_off.text
    assert mem_off.text.index('id="invites-result"') < mem_off.text.index('id="invites-list-section"')
    mem_off_boot = _read_bootstrap(mem_off.text)
    assert mem_off_boot["is_admin"] is False
    assert mem_off_boot["member_invites_enabled"] is False
    assert mem_off_boot["can_issue_invites"] is False

    adm_on = client.get("/account/invites", headers=console_env.admin_owner)
    assert adm_on.status_code == 200
    assert 'id="invites-create-form"' in adm_on.text
    assert '<select name="max_uses"' in adm_on.text
    assert '<option value="1" selected>1</option>' in adm_on.text
    assert '<option value="3">3</option>' in adm_on.text
    assert '<option value="5">5</option>' in adm_on.text
    assert '<select name="expires_in"' in adm_on.text
    for exp_val in ("1800", "3600", "21600", "86400", "604800"):
        assert f'<option value="{exp_val}"' in adm_on.text
    assert 'name="reserved_username"' not in adm_on.text
    assert adm_on.text.index('id="invites-result"') < adm_on.text.index('id="invites-list-section"')
    adm_boot = _read_bootstrap(adm_on.text)
    assert adm_boot["is_admin"] is True
    assert adm_boot["can_issue_invites"] is True

    # Enable member_invites_enabled=True -> member sees issuance form while staying isolated from /admin
    conn = dbm.connect_existing(console_env.db_file)
    try:
        accounts_store.AccountStore(conn).set_member_invites_enabled(True, updated_by=1)
    finally:
        conn.close()

    mem_on = client.get("/account/invites", headers=console_env.alice_owner)
    assert mem_on.status_code == 200
    assert 'id="invites-create-form"' in mem_on.text
    assert "/admin/" not in mem_on.text
    assert "console_admin.js" not in mem_on.text
    assert "console.js.admin." not in mem_on.text
    mem_on_boot = _read_bootstrap(mem_on.text)
    assert mem_on_boot["member_invites_enabled"] is True
    assert mem_on_boot["can_issue_invites"] is True

