"""Group 2–3 ``/settings/*`` 섹션(설계 P3-b) 테스트.

모듈 인터페이스(``console_syndication_panels.build_syndication_section``)와 HTTP I/O(``/settings/<slug>`` 응답 본문)를 검증한다.
모든 상태는 tmp_path에만 기록하고 CLAIRE_* 환경변수를 격리한다.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from claire.api import server
from claire.api.accounts_routes import console_syndication_panels as panels
from claire.config import Settings
from claire.store import accounts as accounts_store
from claire.store import db as dbm
from claire.store.secrets import reset_secret_service
from claire.store.theme import ThemeInfo, ThemeManager
from claire.store.token_store import get_token_store, reset_token_stores

PUBLIC_URL = "http://127.0.0.1:8765"
SECRET_ADMIN_USERNAME = "hidden_operator_p3b"
MEMBER_USERNAME = "alice_member"
OTHER_USERNAME = "bob_member"
JS_BUNDLE = Path(__file__).resolve().parent.parent / "src" / "claire" / "static" / "js" / "console_syndication.js"

SECTION_PANEL_IDS: dict[str, str] = {
    "reader": "panel-reader",
    "connections": "panel-accounts",
    "feeds": "panel-feeds",
    "tags": "panel-tags",
    "rules": "panel-rules",
    "outbound": "panel-outbound",
    "opml": "panel-opml",
    "mcp": "panel-mcp",
    "webmcp": "panel-webmcp",
}

OPERATOR_ONLY_MARKERS: tuple[str, ...] = (
    "mcp_telemetry",
    "pref-mcp-audit-tbody",
    "MCP_TELEMETRY_SEED",
    "panel-idp",
    "panel-userdb",
    'id="pref-idp-',
    'id="pref-userdb-',
    "data-idp-",
    "data-user-act",
    "IDP_SEED",
    "USERS_SEED",
    "console_admin.js",
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


def _insert_theme(tm: ThemeManager, settings: Settings, *, theme_id: int, number: int, label: str, account_id: int, is_default: bool, is_public: bool = True) -> ThemeInfo:
    conn = dbm.connect(settings.db_file)
    now = time.time()
    try:
        conn.execute(
            "INSERT INTO themes (id, seq, number, label, description, icon, is_default, is_public, is_collaborator_accessible, account_id, db_path, default_focus, created_at, updated_at) VALUES (?, ?, ?, ?, ?, '📁', ?, ?, 1, ?, ?, '', ?, ?)",
            (theme_id, theme_id, number, label, f"Desc {label}", 1 if is_default else 0, 1 if is_public else 0, account_id, str(settings.db_file), now, now),
        )
        conn.commit()
    finally:
        conn.close()
    tm.reload()
    tm._save_registry()
    return tm.get_theme(theme_id)


@pytest.fixture
def env(tmp_path: Path):
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
    admin_theme = _insert_theme(tm, settings, theme_id=11, number=(1 << 56) | 11, label="Admin Research", account_id=1, is_default=False)
    alice_def = _insert_theme(tm, settings, theme_id=20, number=(2 << 56) | 20, label="Alice Default", account_id=2, is_default=True)
    alice_private = _insert_theme(tm, settings, theme_id=21, number=(2 << 56) | 21, label="Alice <Private>", account_id=2, is_default=False, is_public=False)
    bob_def = _insert_theme(tm, settings, theme_id=30, number=(3 << 56) | 30, label="Bob Default", account_id=3, is_default=True)

    tok_store = get_token_store(data_dir=data_dir)
    admin_tok = tok_store.create_token(name="admin-owner", scope="owner", account_id=1)["token"]
    alice_tok = tok_store.create_token(name="alice-owner", scope="owner", account_id=2)["token"]
    app = server.create_app(settings, SimpleNamespace(provider=SimpleNamespace(name="mock")))
    return SimpleNamespace(
        db_file=db_file,
        client=TestClient(app, base_url=PUBLIC_URL),
        admin_theme=admin_theme,
        alice_def=alice_def,
        alice_private=alice_private,
        bob_def=bob_def,
        admin_owner={"Authorization": f"Bearer {admin_tok}"},
        alice_owner={"Authorization": f"Bearer {alice_tok}"},
    )


def _set_mode(db_file: Path, mode: str) -> None:
    conn = dbm.connect(db_file)
    try:
        accounts_store.AccountStore(conn).set_setting("mode", mode, updated_by=1)
    finally:
        conn.close()


def _bootstrap(html: str) -> dict:
    match = re.search(r'<script id="console-bootstrap" type="application/json">(.*?)</script>', html, re.S)
    assert match is not None
    return json.loads(match.group(1))


def test_every_group_2_3_section_renders_its_panel_and_script(env):
    _set_mode(env.db_file, "network")
    for slug, panel_id in SECTION_PANEL_IDS.items():
        resp = env.client.get(f"/settings/{slug}", headers=env.alice_owner)
        assert resp.status_code == 200, slug
        body = resp.text
        assert f'id="{panel_id}"' in body, slug
        assert f'data-settings-panel="{slug}"' in body, slug
        assert 'id="settings-syndication-root"' in body, slug
        assert 'id="pref-toast"' in body, slug
        assert '<script src="/static/js/console_syndication.js" defer></script>' in body, slug
        assert 'id="console-theme-select"' in body, slug
        assert "/admin/" not in body, slug
        for marker in OPERATOR_ONLY_MARKERS:
            assert marker not in body, (slug, marker)
        boot = _bootstrap(body)
        assert boot["syndication"]["section"] == slug
        assert boot["syndication"]["theme_ref"] == str(env.alice_def.number)


def test_ported_panels_preserve_control_ids_and_capability_metadata(env):
    _set_mode(env.db_file, "network")
    get = lambda slug: env.client.get(f"/settings/{slug}", headers=env.alice_owner).text  # noqa: E731
    reader = get("reader")
    for control in ("pref-app-tab", "pref-list-style", "pref-layout-mode", "pref-show-excerpt", "pref-default-content-tab", "pref-reader-font", "pref-reader-fontsize", "pref-auto-read"):
        assert f'id="{control}"' in reader, control
    assert '<option value="list">목록 — 한 줄 행</option>' in reader

    connections = get("connections")
    assert "ActivityPub 클라이언트 기능 기준: Mastodon 5.0 이상" in connections
    assert "Mastodon 5.0 기준 클라이언트 기능" in connections
    assert 'id="pref-accounts-tbody"' in connections
    assert f'href="/syndication?tab=client&amp;theme={env.alice_def.number}"' in connections
    assert f"/@{MEMBER_USERNAME}/syndication" not in connections

    feeds = get("feeds")
    for control in ("pref-feeds-tbody", "pref-add-feed-form", "new-feed-proto", "new-feed-via", "new-feed-interval"):
        assert f'id="{control}"' in feeds, control

    outbound = get("outbound")
    for control in ("pref-peer-listed", "pref-peer-quote", "pref-peer-import", "pref-peer-license", "pref-rss-pubmode", "pref-quote-template"):
        assert f'id="{control}"' in outbound, control

    mcp = get("mcp")
    for control in ("pref-mcp-endpoint", "pref-mcp-theme-example", "pref-mcp-snippet-cli", "pref-mcp-snippet-json", "pref-mcp-tools-tbody", "pref-mcp-proposed-tbody", "pref-mcp-write", "pref-mcp-toolslist"):
        assert f'id="{control}"' in mcp, control
    assert "/.well-known/oauth-protected-resource" in mcp
    assert "syndication:draft" in mcp
    assert 'href="/settings/tokens" id="pref-mcp-tokens-link"' in mcp
    for removed in ("pref-mcp-apitokens-tbody", "pref-mcp-clients-tbody", "pref-mcp-rbac-tbody", "pref-mcp-gaps"):
        assert removed not in mcp, removed

    webmcp = get("webmcp")
    for control in ("pref-webmcp-enabled", "pref-webmcp-exposed", "pref-webmcp-mode", "pref-webmcp-confirm", "pref-webmcp-activity", "pref-webmcp-tools-tbody", "pref-webmcp-sim-tool", "btn-webmcp-sim-run", "pref-webmcp-sim-input"):
        assert f'id="{control}"' in webmcp, control
    assert "document.modelContext" in webmcp
    assert "consequentialHint" in webmcp
    assert "requestUserInteraction" not in webmcp


def test_admin_viewer_mcp_section_links_to_admin_tokens(env):
    _set_mode(env.db_file, "network")
    resp = env.client.get("/settings/mcp", headers=env.admin_owner)
    assert resp.status_code == 200
    assert 'href="/admin/tokens" id="pref-mcp-tokens-link"' in resp.text
    assert 'href="/settings/tokens" id="pref-mcp-tokens-link"' not in resp.text


def test_theme_query_scopes_section_to_selected_owned_theme(env):
    _set_mode(env.db_file, "network")
    ref = str(env.alice_private.number)
    resp = env.client.get(f"/settings/webmcp?theme={ref}", headers=env.alice_owner)
    assert resp.status_code == 200
    body = resp.text
    assert f'data-theme-ref="{ref}"' in body
    assert 'data-theme-public="false"' in body
    # 시드 순번은 소유 테마 목록 안의 위치다. 두 테마가 서로 다른 슬롯을 받아야 한다.
    other = env.client.get(f"/settings/webmcp?theme={env.alice_def.number}", headers=env.alice_owner).text
    seeds = {re.search(r'data-theme-seed="(\d+)"', html).group(1) for html in (body, other)}
    assert len(seeds) == 2
    # 테마 라벨은 이스케이프된 data 속성으로만 들어간다.
    assert 'data-theme-label="Alice &lt;Private&gt;"' in body
    boot = _bootstrap(body)
    assert boot["syndication"]["theme_ref"] == ref
    assert sorted(boot["syndication"]["owned_theme_refs"]) == sorted([str(env.alice_def.number), ref])
    assert str(env.bob_def.number) not in body
    assert str(env.admin_theme.number) not in body
    assert env.client.get(f"/settings/webmcp?theme={env.bob_def.number}", headers=env.alice_owner).status_code == 404


def test_single_mode_never_emits_admin_username_on_group_2_3_sections(env):
    _set_mode(env.db_file, "single")
    for slug in SECTION_PANEL_IDS:
        resp = env.client.get(f"/settings/{slug}", headers=env.admin_owner, follow_redirects=False)
        assert resp.status_code == 200, slug
        assert SECRET_ADMIN_USERNAME not in resp.text, slug
        assert "/@" not in resp.text, slug
        assert all(SECRET_ADMIN_USERNAME not in v for v in resp.headers.values()), slug
    connections = env.client.get("/settings/connections", headers=env.admin_owner).text
    admin_ref = _bootstrap(connections)["syndication"]["theme_ref"]
    assert f'href="/syndication?tab=client&amp;theme={admin_ref}"' in connections


def test_build_syndication_section_interface(env, tmp_path: Path):
    ctx = SimpleNamespace(db_file=env.db_file, data_dir=tmp_path)
    member = accounts_store.Account(
        id=2, public_id=(1 << 57) + 202, username=MEMBER_USERNAME, role="member", status="active",
        display_name="Alice", profile_public=True, created_at=0.0, activated_at=0.0, delete_after=None, updated_at=0.0,
    )
    with pytest.raises(KeyError):
        panels.build_syndication_section(ctx, slug="idp", path="/settings/idp", mode="single", account=member, selected_theme=None, owned_themes=[], theme_query=None)

    # C1: 소유 테마가 없으면 다른 테마로 대체하지 않고 안내만 그린다. 스크립트도 싣지 않는다.
    empty = panels.build_syndication_section(ctx, slug="reader", path="/settings/reader", mode="single", account=member, selected_theme=None, owned_themes=[], theme_query=None)
    assert 'id="settings-syndication-no-theme"' in empty.body_html
    assert 'href="/settings/themes"' in empty.body_html
    assert empty.extra_scripts_html == ""
    assert empty.extra_bootstrap == {"syndication": {"section": "reader", "theme_ref": None}}

    with pytest.raises(ValueError):
        panels.build_syndication_section(ctx, slug="reader", path="/settings/reader", mode="single", account=member, selected_theme=env.bob_def, owned_themes=[env.alice_def], theme_query=None)

    rendered = panels.build_syndication_section(ctx, slug="opml", path="/settings/opml", mode="single", account=member, selected_theme=env.alice_def, owned_themes=[env.alice_def], theme_query=None)
    assert isinstance(rendered, panels.SettingsSectionRender)
    assert 'id="panel-opml"' in rendered.body_html
    assert rendered.extra_scripts_html == panels.CONSOLE_SYNDICATION_SCRIPT_HTML
    assert MEMBER_USERNAME not in rendered.body_html


def test_js_bundle_scopes_storage_and_excludes_operator_data():
    src = JS_BUNDLE.read_text(encoding="utf-8")
    assert '"claire_synd_prefs_v2:" + storageKey + ":t" + THEME_REF' in src
    assert 'meta[name="claire-storage-key"]' in src
    assert 'byId("console-theme-select")' in src
    assert "a[data-nav-section]" in src
    assert "document.modelContext" in src
    assert "consequentialHint" in src
    assert "requestUserInteraction" not in src
    for marker in (*OPERATOR_ONLY_MARKERS, "pref-idp-", "pref-userdb-"):
        assert marker not in src, marker
    # 패널 슬러그마다 초기화 함수가 있어야 한다.
    for slug in SECTION_PANEL_IDS:
        assert re.search(rf"\b{slug}: init\w+", src), slug
