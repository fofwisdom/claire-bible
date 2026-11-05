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
from starlette.testclient import TestClient

from claire.api import server
from claire.config import Settings
from claire.store import accounts as accounts_store
from claire.store import db as dbm
from claire.store.secrets import reset_secret_service
from claire.store.telemetry import record_mcp_telemetry
from claire.store.theme import ThemeInfo, ThemeManager
from claire.store.token_store import get_token_store, reset_token_stores

PUBLIC_URL = "http://127.0.0.1:8765"
SECRET_ADMIN_USERNAME = "hidden_operator_p1"
MEMBER_USERNAME = "alice_member"
OTHER_USERNAME = "bob_member"

NEW_ADMIN_PATHS: tuple[str, ...] = (
    "/admin",
    "/admin/providers",
    "/admin/limits",
    "/admin/themes",
    "/admin/tokens",
    "/admin/mcp",
    "/api/admin/mcp/telemetry",
)

ALL_SETTINGS_SECTION_PATHS: tuple[str, ...] = (
    "/settings/account",
    "/settings/keys",
    "/settings/themes",
    "/settings/tokens",
    "/settings/reader",
    "/settings/connections",
    "/settings/feeds",
    "/settings/tags",
    "/settings/rules",
    "/settings/outbound",
    "/settings/opml",
    "/settings/mcp",
    "/settings/webmcp",
)


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch):
    reset_secret_service()
    reset_token_stores()
    for key in list(os.environ):
        if key.startswith("CLAIRE_"):
            monkeypatch.delenv(key, raising=False)
    yield
    reset_secret_service()
    reset_token_stores()


def _upsert_account(conn, account_id: int, public_id: int, username: str, role: str) -> None:
    now = time.time()
    conn.execute(
        "INSERT INTO accounts (id, public_id, username, role, status, display_name, profile_public, created_at, activated_at, updated_at) VALUES (?, ?, ?, ?, 'active', ?, 1, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET public_id=excluded.public_id, username=excluded.username, role=excluded.role, status='active', profile_public=1, updated_at=excluded.updated_at",
        (account_id, public_id, username, role, username.capitalize(), now, now, now),
    )
    conn.commit()


def _insert_theme(tm: ThemeManager, settings: Settings, *, theme_id: int, seq: int, number: int, label: str, account_id: int, is_default: bool) -> ThemeInfo:
    conn = dbm.connect(settings.db_file)
    now = time.time()
    try:
        conn.execute(
            "INSERT INTO themes (id, seq, number, label, description, icon, is_default, is_public, is_collaborator_accessible, account_id, db_path, default_focus, created_at, updated_at) VALUES (?, ?, ?, ?, ?, '📁', ?, 1, 1, ?, ?, '', ?, ?)",
            (theme_id, seq, number, label, f"Desc {label}", 1 if is_default else 0, account_id, str(settings.db_file), now, now),
        )
        conn.commit()
    finally:
        conn.close()
    tm.reload()
    tm._save_registry()
    return tm.get_theme(theme_id)


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
        CLAIRE_DATA_DIR=str(data_dir),
        CLAIRE_PROVIDER="mock",
        CLAIRE_ENVIRONMENT="development",
        CLAIRE_PUBLIC_URL=PUBLIC_URL,
        CLAIRE_ANONYMOUS_READONLY=True,
        CLAIRE_MULTI_THEME=True,
    )
    tm = ThemeManager(settings)
    admin_extra = _insert_theme(tm, settings, theme_id=11, seq=1, number=(1 << 56) | 11, label="Admin Research", account_id=1, is_default=False)
    alice_def = _insert_theme(tm, settings, theme_id=20, seq=2, number=(2 << 56) | 20, label="Alice Default", account_id=2, is_default=True)
    bob_def = _insert_theme(tm, settings, theme_id=30, seq=3, number=(3 << 56) | 30, label="Bob Default", account_id=3, is_default=True)

    tok_store = get_token_store(data_dir=data_dir)
    admin_owner_tok = tok_store.create_token(name="admin-owner", scope="owner", account_id=1)["token"]
    admin_collab_tok = tok_store.create_token(name="admin-collab", scope="collaborator", account_id=1)["token"]
    admin_ro_tok = tok_store.create_token(name="admin-ro", scope="readonly", account_id=1)["token"]
    alice_owner_tok = tok_store.create_token(name="alice-owner", scope="owner", account_id=2)["token"]
    alice_collab_tok = tok_store.create_token(name="alice-collab", scope="collaborator", account_id=2)["token"]

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
        admin_owner={"Authorization": f"Bearer {admin_owner_tok}"},
        admin_collab={"Authorization": f"Bearer {admin_collab_tok}"},
        admin_ro={"Authorization": f"Bearer {admin_ro_tok}"},
        alice_owner={"Authorization": f"Bearer {alice_owner_tok}"},
        alice_collab={"Authorization": f"Bearer {alice_collab_tok}"},
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
    for path in ("/settings", *ALL_SETTINGS_SECTION_PATHS):
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
        f"/@{MEMBER_USERNAME}/settings/account",
        f"/@{MEMBER_USERNAME}/settings/reader",
        f"/themes/{console_env.alice_def.number}/settings/reader",
        f"/@{SECRET_ADMIN_USERNAME}/admin",
        f"/@{SECRET_ADMIN_USERNAME}/admin/providers",
        f"/themes/{console_env.admin_extra.number}/admin/limits",
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
    assert r_admin.headers["location"] == "/admin/instance"
    assert r_admin.headers["referrer-policy"] == "same-origin"

    r_settings = client.get("/settings", headers=console_env.alice_owner, follow_redirects=False)
    assert r_settings.status_code == 302
    assert r_settings.headers["location"] == "/settings/account"

    r_from_synd = client.get(f"/settings?from=syndication&theme={console_env.alice_def.number}", headers=console_env.alice_owner, follow_redirects=False)
    assert r_from_synd.status_code == 302
    assert r_from_synd.headers["location"] == f"/settings/reader?theme={console_env.alice_def.number}"

    # D3: Admin accessing /settings/themes or /settings/tokens redirects 302 to /admin/themes or /admin/tokens
    for slug in ("themes", "tokens"):
        adm_redirect = client.get(f"/settings/{slug}", headers=console_env.admin_owner, follow_redirects=False)
        assert adm_redirect.status_code == 302
        assert adm_redirect.headers["location"] == f"/admin/{slug}"

        adm_page = client.get(f"/admin/{slug}", headers=console_env.admin_owner, follow_redirects=False)
        assert adm_page.status_code == 200
        assert adm_page.headers["referrer-policy"] == "same-origin"

        # Regular member gets 200 on /settings/themes and /settings/tokens
        mem_page = client.get(f"/settings/{slug}", headers=console_env.alice_owner, follow_redirects=False)
        assert mem_page.status_code == 200
        assert mem_page.headers["referrer-policy"] == "same-origin"


def test_strict_theme_query_validation_rejects_unknown_or_cross_account_theme(console_env):
    _set_mode(console_env.db_file, "network")
    client = console_env.client

    # Unknown theme ref -> 404 before any redirect or page render
    assert client.get("/settings?theme=nonexistent", headers=console_env.alice_owner, follow_redirects=False).status_code == 404
    assert client.get("/settings?theme=", headers=console_env.alice_owner, follow_redirects=False).status_code == 404
    assert client.get("/settings/reader?theme=999999", headers=console_env.alice_owner, follow_redirects=False).status_code == 404
    assert client.get("/settings/themes?theme=nonexistent", headers=console_env.admin_owner, follow_redirects=False).status_code == 404
    assert client.get("/admin/providers?theme=nonexistent", headers=console_env.admin_owner, follow_redirects=False).status_code == 404

    # Cross-account theme ref (Alice requesting Bob's theme or Admin's theme) -> 404
    assert client.get(f"/settings/reader?theme={console_env.bob_def.number}", headers=console_env.alice_owner, follow_redirects=False).status_code == 404
    assert client.get(f"/settings/reader?theme={console_env.admin_extra.number}", headers=console_env.alice_owner, follow_redirects=False).status_code == 404

    # Own theme ref -> 200 and selector reflects theme
    ok_resp = client.get(f"/settings/reader?theme={console_env.alice_def.number}", headers=console_env.alice_owner, follow_redirects=False)
    assert ok_resp.status_code == 200
    assert str(console_env.alice_def.number) in ok_resp.text


def test_unified_sidebar_and_strict_server_side_admin_dom_stripping(console_env):
    _set_mode(console_env.db_file, "network")
    client = console_env.client

    # Member response on /settings/reader must never contain /admin/* links or admin sidebar labels
    member_resp = client.get("/settings/reader", headers=console_env.alice_owner)
    assert member_resp.status_code == 200
    assert 'href="/settings/account"' in member_resp.text
    assert 'href="/settings/themes"' in member_resp.text
    assert 'href="/settings/tokens"' in member_resp.text
    assert 'href="/settings/feeds"' in member_resp.text
    assert "/admin/" not in member_resp.text
    assert "시스템 관리 및 설정 (관리자)" not in member_resp.text
    assert "인스턴스 · 계정 · 인증 (관리자)" not in member_resp.text

    # Admin response on both /settings/reader and /admin/providers has unified sidebar without duplicate /settings/themes or /settings/tokens
    for path in ("/settings/reader", "/admin/providers", "/admin/limits", "/admin/mcp"):
        admin_resp = client.get(path, headers=console_env.admin_owner)
        assert admin_resp.status_code == 200, path
        assert 'href="/settings/account"' in admin_resp.text
        assert 'href="/settings/feeds"' in admin_resp.text
        assert 'href="/admin/instance"' in admin_resp.text
        assert 'href="/admin/accounts"' in admin_resp.text
        assert 'href="/admin/idp"' in admin_resp.text
        assert 'href="/admin/providers"' in admin_resp.text
        assert 'href="/admin/limits"' in admin_resp.text
        assert 'href="/admin/themes"' in admin_resp.text
        assert 'href="/admin/tokens"' in admin_resp.text
        assert 'href="/admin/mcp"' in admin_resp.text
        assert 'href="/settings/themes"' not in admin_resp.text
        assert 'href="/settings/tokens"' not in admin_resp.text


def test_single_mode_conceals_admin_username_on_console_and_whoami(console_env):
    _set_mode(console_env.db_file, "single")
    client = console_env.client

    for path in ("/admin/providers", "/admin/themes", "/settings/account", "/settings/reader"):
        resp = client.get(path, headers=console_env.admin_owner, follow_redirects=False)
        assert resp.status_code == 200, path
        assert SECRET_ADMIN_USERNAME not in resp.text
        assert all(SECRET_ADMIN_USERNAME not in v for v in resp.headers.values())
        assert 'name="claire-storage-key" content="a' in resp.text

    who_admin = client.get("/whoami", headers=console_env.admin_owner)
    assert who_admin.status_code == 200
    assert who_admin.json() == {"scope": "owner", "instance": {"mode": "single"}, "relation": "owner", "is_admin": True}
    assert SECRET_ADMIN_USERNAME not in who_admin.text

    _set_mode(console_env.db_file, "network")
    who_alice = client.get(f"/@{MEMBER_USERNAME}/whoami", headers=console_env.alice_owner)
    assert who_alice.status_code == 200
    assert who_alice.json() == {"scope": "owner", "instance": {"mode": "network"}, "relation": "owner", "is_admin": False}


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


def test_all_eight_admin_sections_render_unified_shell_and_preserve_member_theme_privacy(console_env):
    _set_mode(console_env.db_file, "network")
    client = console_env.client

    expected_anchors = {
        "instance": ('/admin/instance', 'id="instance-mode"', 'id="server-tier"'),
        "accounts": ('/admin/accounts', 'id="admin-members-section"', 'id="pref-userdb-admin"'),
        "idp": ('/admin/idp', 'id="pref-idp-tbody"', 'id="pref-idp-add-form"'),
        "providers": ('/admin/providers', 'id="admin-providers-host"', 'data-admin-panel-host="providers"'),
        "limits": ('/admin/limits', 'id="admin-limits-host"', 'data-admin-panel-host="limits"'),
        "themes": ('/admin/themes', 'id="admin-themes-host"', 'id="admin-themes-summary"'),
        "tokens": ('/admin/tokens', 'id="admin-tokens-host"', 'id="pref-mcp-clients-tbody"'),
        "mcp": ('/admin/mcp', 'id="pref-mcp-audit-tbody"', 'id="pref-mcp-rbac-tbody"'),
    }

    for slug, (path, anchor_a, anchor_b) in expected_anchors.items():
        resp = client.get(path, headers=console_env.admin_owner)
        assert resp.status_code == 200, path
        assert resp.headers.get("referrer-policy") == "same-origin", path
        body = resp.text
        assert 'data-active-area="admin"' in body, path
        assert f'data-active-section="{slug}"' in body, path
        assert '<script src="/static/js/manage_panels.js" defer></script>' in body, path
        assert '<script src="/static/js/console_admin.js" defer></script>' in body, path
        assert anchor_a in body, (path, anchor_a)
        assert anchor_b in body, (path, anchor_b)
        if slug == "themes":
            # D5/K5: /admin/themes 요약 카드는 집계 COUNT(*)만 표시하며 멤버 테마 라벨을 절대 노출하지 않는다.
            assert "Alice Default" not in body
            assert "Bob Default" not in body
        if slug == "mcp":
            assert 'id="pref-webmcp-enabled"' in body
            assert "data-support-bundle-form" in body

