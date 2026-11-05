"""관리자(`/admin/*`)·사용자(`/settings/*`) 통합 콘솔 E2E (설계 ADMIN_AND_SETTINGS_PATH_SEPARATION_DESIGN P4 단계 5).

실제 `server.create_app` 앱을 starlette TestClient로 구동해 다음 흐름을 끝까지 검증한다.

1. 관리자 E2E: `/` '관리 및 설정' 드로어(⚡ 프로바이더 · 📏 적재 제한 · 🛠️ 관리자 콘솔) → `/admin` 진입 → 통합 사이드바로 모든 `/admin/*`·`/settings/*` 섹션 이동 → D3 리다이렉트 → 텔레메트리 API.
2. 멤버 격리 E2E(network): `is_admin` false, `/settings/*`에 관리자 링크·DOM·번들 없음, `/settings/keys` BYOK 쓰기 전용 흐름, 모든 `/admin*`이 404(403·302 아님).
3. single 모드 `@username` 비노출(S9).
4. 레거시 `/me`·`/syndication/preferences?tab=` 302 호환 리다이렉트(D4).

모든 상태는 tmp_path에만 기록하고 CLAIRE_*·GEMINI_* 환경변수를 격리한다. 외부 네트워크 호출은 없다(BYOK 시험 호출은 monkeypatch).
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from claire import secret_scrub
from claire.api import server
from claire.config import Settings
from claire.store import accounts as accounts_store
from claire.store import db as dbm
from claire.store import provider_keys
from claire.store.secrets import reset_secret_service
from claire.store.theme import ThemeInfo, ThemeManager
from claire.store.token_store import get_token_store, reset_token_stores

PUBLIC_URL = "http://127.0.0.1:8765"
ORIGIN = PUBLIC_URL
ADMIN_USERNAME = "hidden_operator_p4e2e"
MEMBER_USERNAME = "alice_member_e2e"
OTHER_USERNAME = "bob_member_e2e"
ALL_USERNAMES = (ADMIN_USERNAME, MEMBER_USERNAME, OTHER_USERNAME)
TEST_MASTER_KEY_HEX = "33" * 32
# 키 모양 패턴(AIza…, sk-…)에 걸리지 않는 카나리아: 정규식 스크럽이 아니라 "싣지 않음"을 확인한다.
BYOK_CANARY = "canary-console-e2e-key-4d5e6f708192a3b4"
MODES = ("single", "network")

ADMIN_SECTIONS: tuple[str, ...] = ("instance", "accounts", "idp", "providers", "limits", "themes", "tokens", "mcp")
SETTINGS_SECTIONS: tuple[str, ...] = ("account", "reader", "connections", "feeds", "tags", "rules", "outbound", "opml", "mcp", "webmcp")
MEMBER_SETTINGS_SECTIONS: tuple[str, ...] = ("account", "keys", "themes", "tokens", *SETTINGS_SECTIONS[1:])
ADMIN_PROBE_PATHS: tuple[str, ...] = ("/admin", *(f"/admin/{slug}" for slug in ADMIN_SECTIONS), "/api/admin/mcp/telemetry")


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch):
    for key in list(os.environ):
        if key.startswith("CLAIRE_") or key.startswith("GEMINI_") or key.startswith("GOOGLE_API"):
            monkeypatch.delenv(key, raising=False)
    # 저장소 루트 data/auth/master.key를 만들지 않도록 마스터 키를 환경변수로 준다.
    monkeypatch.setenv("CLAIRE_SECRET_KEY", TEST_MASTER_KEY_HEX)
    reset_secret_service()
    reset_token_stores()
    secret_scrub._reset_for_tests()
    yield
    reset_secret_service()
    reset_token_stores()
    secret_scrub._reset_for_tests()


def _upsert_account(conn, account_id: int, public_id: int, username: str, role: str) -> None:
    now = time.time()
    conn.execute(
        "INSERT INTO accounts (id, public_id, username, role, status, display_name, profile_public, created_at, activated_at, updated_at) VALUES (?, ?, ?, ?, 'active', ?, 1, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET public_id=excluded.public_id, username=excluded.username, role=excluded.role, status='active', display_name=excluded.display_name, profile_public=1, updated_at=excluded.updated_at",
        (account_id, public_id, username, role, f"Display {account_id}", now, now, now),
    )
    conn.commit()


def _insert_theme(tm: ThemeManager, settings: Settings, *, theme_id: int, seq: int, number: int, label: str, account_id: int, is_default: bool) -> ThemeInfo:
    conn = dbm.connect(settings.db_file)
    now = time.time()
    try:
        conn.execute(
            "INSERT INTO themes (id, seq, number, label, description, icon, is_default, is_public, is_collaborator_accessible, account_id, db_path, default_focus, created_at, updated_at) VALUES (?, ?, ?, ?, ?, '📁', ?, 0, 0, ?, ?, '', ?, ?)",
            (theme_id, seq, number, label, f"Desc {label}", 1 if is_default else 0, account_id, str(settings.db_file), now, now),
        )
        conn.commit()
    finally:
        conn.close()
    tm.reload()
    tm._save_registry()
    return tm.get_theme(theme_id)


def _set_mode(db_file: Path, mode: str) -> None:
    conn = dbm.connect(db_file)
    try:
        accounts_store.AccountStore(conn).set_setting("mode", mode, updated_by=1)
    finally:
        conn.close()


@pytest.fixture
def console_e2e(tmp_path: Path):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)
    db_file = data_dir / "claire.db"
    conn = dbm.connect(db_file)
    try:
        dbm.init_db(conn)
        _upsert_account(conn, 1, (1 << 57) + 401, ADMIN_USERNAME, "admin")
        _upsert_account(conn, 2, (1 << 57) + 402, MEMBER_USERNAME, "member")
        _upsert_account(conn, 3, (1 << 57) + 403, OTHER_USERNAME, "member")
    finally:
        conn.close()

    settings = Settings(
        _env_file=None,
        CLAIRE_DB_PATH=str(db_file),
        CLAIRE_DATA_DIR=str(data_dir),
        CLAIRE_PROVIDER="mock",
        CLAIRE_ENVIRONMENT="development",
        CLAIRE_PUBLIC_URL=PUBLIC_URL,
        CLAIRE_ANONYMOUS_READONLY=True,
        CLAIRE_MULTI_THEME=True,
    )
    tm = ThemeManager(settings)
    admin_theme = _insert_theme(tm, settings, theme_id=11, seq=1, number=(1 << 56) | 11, label="Admin Console Vault", account_id=1, is_default=False)
    alice_theme = _insert_theme(tm, settings, theme_id=20, seq=2, number=(2 << 56) | 20, label="Alice Console Garden", account_id=2, is_default=True)
    bob_theme = _insert_theme(tm, settings, theme_id=30, seq=3, number=(3 << 56) | 30, label="Bob Console Secret", account_id=3, is_default=True)

    tok_store = get_token_store(data_dir=data_dir)
    admin_tok = tok_store.create_token(name="admin-owner-e2e", scope="owner", account_id=1)["token"]
    alice_tok = tok_store.create_token(name="alice-owner-e2e", scope="owner", account_id=2)["token"]
    alice_collab_tok = tok_store.create_token(name="alice-collab-e2e", scope="collaborator", account_id=2)["token"]

    app = server.create_app(settings, SimpleNamespace(provider=SimpleNamespace(name="mock")))
    with TestClient(app, base_url=PUBLIC_URL) as client:
        yield SimpleNamespace(
            data_dir=data_dir,
            db_file=db_file,
            settings=settings,
            client=client,
            admin_theme=admin_theme,
            alice_theme=alice_theme,
            bob_theme=bob_theme,
            admin={"Authorization": f"Bearer {admin_tok}"},
            alice={"Authorization": f"Bearer {alice_tok}"},
            alice_collab={"Authorization": f"Bearer {alice_collab_tok}"},
        )


def _member_session_headers(env, account_id: int) -> dict[str, str]:
    # 단계 인증(step-up)은 세션 쿠키 주체에만 허용되므로 BYOK 쓰기는 방금 인증한 owner 세션으로 한다.
    conn = dbm.connect(env.db_file)
    try:
        token = accounts_store.create_account_session(conn, account_id=account_id, scope="owner", now=time.time())
    finally:
        conn.close()
    return {"Cookie": f"claire_session={token}", "Origin": ORIGIN}


def _get(env, path: str, headers: dict[str, str] | None = None):
    return env.client.get(path, headers=headers or {}, follow_redirects=False)


def _assert_console_page(resp, path: str) -> str:
    assert resp.status_code == 200, (path, resp.status_code)
    assert resp.headers.get("referrer-policy") == "same-origin", path
    assert "no-store" in resp.headers.get("cache-control", ""), (path, resp.headers.get("cache-control"))
    assert resp.headers.get("content-type", "").startswith("text/html"), path
    return resp.text


def _assert_admin_sidebar(body: str, path: str) -> None:
    # 통합 사이드바: 사용자 그룹 1–3과 관리자 그룹 4–5가 모두 있고, D3 중복(/settings/themes·/settings/tokens)은 없다.
    for slug in SETTINGS_SECTIONS:
        assert f'href="/settings/{slug}"' in body, (path, slug)
    for slug in ADMIN_SECTIONS:
        assert f'href="/admin/{slug}"' in body, (path, slug)
    assert 'href="/settings/themes"' not in body, path
    assert 'href="/settings/tokens"' not in body, path


def _assert_no_username_leak(resp, label: str) -> None:
    # S9: single 모드에서는 HTML·JSON·리다이렉트 헤더 어디에도 `@username`이 없다.
    body = resp.text
    headers_blob = repr(list(resp.headers.items()))
    for username in ALL_USERNAMES:
        assert f"@{username}" not in body, (label, username)
        assert username not in headers_blob, (label, username)


# --- 1. 관리자 E2E ----------------------------------------------------------------------


@pytest.mark.parametrize("mode", MODES)
def test_admin_root_topbar_console_icon_and_no_drawer_manage_panels(console_e2e, mode: str):
    _set_mode(console_e2e.db_file, mode)
    env = console_e2e

    # network 모드에서 그래프 루트는 /@{username}/ 네임스페이스에 있다(single 모드는 /).
    root_path = "/" if mode == "single" else f"/@{ADMIN_USERNAME}/"
    root = _get(env, root_path, env.admin)
    assert root.status_code == 200, root.status_code
    html = root.text
    # 상세 메뉴의 '관리 및 설정' 패널은 없다. 관리·설정 기능은 콘솔에만 있다.
    assert 'id="manage-section"' not in html
    for gone in ("providermanagebtn", "limitmanagebtn", "thememanagebtn", "tokenmanagebtn", "openProviderManager", "openLimitManager", "openThemeManager", "openTokenManager", "ClaireManagePanels"):
        assert gone not in html, gone
    # 콘솔 단추는 상단바에서 라이트/다크 전환(#themebtn) 바로 왼쪽의 아이콘이다.
    topbar = html[html.index('<div id="viewoptions">'):html.index('</header>')]
    assert re.search(r'<a id="consolelinkbtn"[^>]*>⚙️</a>\s*<button id="themebtn"', topbar)

    who = _get(env, "/whoami", env.admin)
    assert who.status_code == 200
    assert who.json()["is_admin"] is True
    assert who.json()["scope"] == "owner"
    assert who.json()["instance"] == {"mode": mode}

    # 인라인된 app.js가 is_admin=true일 때 #consolelinkbtn을 🛠️ 아이콘(관리자 콘솔 → /admin)으로 바꾼다.
    assert "IS_ADMIN = d.is_admin === true" in html
    assert "clBtn.setAttribute('href', IS_ADMIN ? '/admin' : '/settings')" in html
    assert "clBtn.textContent = IS_ADMIN ? '🛠️' : '⚙️'" in html


@pytest.mark.parametrize("mode", MODES)
def test_admin_console_navigation_across_all_sections(console_e2e, mode: str):
    _set_mode(console_e2e.db_file, mode)
    env = console_e2e

    entry = _get(env, "/admin", env.admin)
    assert entry.status_code == 302
    assert entry.headers["location"] == "/admin/instance"

    landing = _assert_console_page(_get(env, entry.headers["location"], env.admin), "/admin/instance")
    _assert_admin_sidebar(landing, "/admin/instance")

    # 사이드바 링크를 실제로 따라가며 모든 관리자 섹션을 연다.
    for slug in ADMIN_SECTIONS:
        path = f"/admin/{slug}"
        assert f'href="{path}"' in landing
        body = _assert_console_page(_get(env, path, env.admin), path)
        _assert_admin_sidebar(body, path)
        assert 'data-active-area="admin"' in body, path
        assert f'data-active-section="{slug}"' in body, path
        assert '<script src="/static/js/console_admin.js" defer></script>' in body, path
        # D5/K5: 관리자 화면은 멤버 테마 라벨을 노출하지 않는다.
        assert "Alice Console Garden" not in body, path
        assert "Bob Console Secret" not in body, path

    for slug in SETTINGS_SECTIONS:
        path = f"/settings/{slug}"
        body = _assert_console_page(_get(env, path, env.admin), path)
        _assert_admin_sidebar(body, path)
        assert f'data-active-section="{slug}"' in body, path

    # D3: 관리자의 /settings/themes·/settings/tokens는 /admin/*로 302.
    for slug in ("themes", "tokens"):
        resp = _get(env, f"/settings/{slug}", env.admin)
        assert resp.status_code == 302, slug
        assert resp.headers["location"] == f"/admin/{slug}"
        _assert_console_page(_get(env, resp.headers["location"], env.admin), f"/admin/{slug}")

    telemetry = _get(env, "/api/admin/mcp/telemetry", env.admin)
    assert telemetry.status_code == 200
    body = telemetry.json()
    assert body["count"] == 0
    assert body["records"] == []


# --- 2. 멤버 격리 E2E (network) ---------------------------------------------------------


def test_member_isolation_in_network_mode(console_e2e):
    _set_mode(console_e2e.db_file, "network")
    env = console_e2e

    who = _get(env, f"/@{MEMBER_USERNAME}/whoami", env.alice)
    assert who.status_code == 200
    assert who.json() == {"scope": "owner", "instance": {"mode": "network"}, "relation": "owner", "is_admin": False}

    root = _get(env, f"/@{MEMBER_USERNAME}/", env.alice)
    assert root.status_code == 200, root.status_code
    # 멤버 루트에도 드로어 관리 패널이 없고, 상단바 아이콘은 기본값 /settings다.
    assert 'id="manage-section"' not in root.text
    assert "providermanagebtn" not in root.text
    assert "limitmanagebtn" not in root.text
    assert re.search(r'<a id="consolelinkbtn"[^>]*href="/settings"', root.text)

    entry = _get(env, "/settings", env.alice)
    assert entry.status_code == 302
    assert entry.headers["location"] == "/settings/account"

    for slug in MEMBER_SETTINGS_SECTIONS:
        path = f"/settings/{slug}"
        body = _assert_console_page(_get(env, path, env.alice), path)
        # 불변식 3: 서버 측에서 관리자 링크·섹션·번들을 제거한다.
        assert "/admin" not in body, path
        assert "console_admin.js" not in body, path
        assert 'data-active-area="admin"' not in body, path
        assert "data-admin-panel-host" not in body, path
        assert "admin-themes-summary" not in body, path
        assert "pref-mcp-audit-tbody" not in body, path
        assert "pref-idp-tbody" not in body, path
        assert "시스템 관리 및 설정 (관리자)" not in body, path
        assert "인스턴스 · 계정 · 인증 (관리자)" not in body, path
        for slug2 in MEMBER_SETTINGS_SECTIONS:
            assert f'href="/settings/{slug2}"' in body, (path, slug2)
        assert "Bob Console Secret" not in body, path
        assert "Admin Console Vault" not in body, path

    # B1/N5: 모든 /admin*과 텔레메트리 API는 404(403도 302도 아님).
    for headers in (env.alice, env.alice_collab, {}):
        for path in ADMIN_PROBE_PATHS:
            for method in ("GET", "HEAD"):
                resp = env.client.request(method, path, headers=headers, follow_redirects=False)
                assert resp.status_code == 404, (method, path, resp.status_code)
                assert "location" not in resp.headers, (method, path)


def test_member_byok_flow_at_settings_keys_is_write_only(console_e2e, monkeypatch: pytest.MonkeyPatch):
    _set_mode(console_e2e.db_file, "network")
    env = console_e2e
    verify_calls: list[tuple[str, str]] = []

    def _fake_verify(provider: str, api_key: str) -> tuple[bool, str]:
        verify_calls.append((provider, api_key))
        return True, "ok"

    monkeypatch.setattr(provider_keys, "verify_key", _fake_verify)

    page = _assert_console_page(_get(env, "/settings/keys", env.alice), "/settings/keys")
    assert 'data-keys-mode="byok"' in page
    assert 'data-settings-form="provider-key-put"' in page
    assert "/admin" not in page

    session_headers = _member_session_headers(env, 2)
    env.client.cookies.clear()
    before = env.client.get("/api/me/keys", headers=session_headers)
    assert before.status_code == 200
    assert before.json() == {"keys": []}

    env.client.cookies.clear()
    put = env.client.put("/api/me/keys", headers=session_headers, json={"provider": "gemini", "api_key": BYOK_CANARY})
    assert put.status_code == 200, put.text
    meta = put.json()["key"]
    assert meta["provider"] == "gemini"
    assert meta["status"] == "active"
    assert verify_calls == [("gemini", BYOK_CANARY)]

    env.client.cookies.clear()
    listed = env.client.get("/api/me/keys", headers=session_headers)
    env.client.cookies.clear()
    assert listed.status_code == 200
    assert listed.json() == {"keys": [meta]}

    page_after = _assert_console_page(_get(env, "/settings/keys", env.alice), "/settings/keys")
    assert meta["fingerprint"] in page_after

    for resp in (put, listed, page_after_resp := _get(env, "/settings/keys", env.alice)):
        blob = resp.text + repr(list(resp.headers.items()))
        assert BYOK_CANARY not in blob
        assert BYOK_CANARY[:16] not in blob
    assert page_after_resp.status_code == 200


# --- 3. single 모드 USERNAME 비노출 -----------------------------------------------------


def test_single_mode_never_emits_username_on_console_or_whoami(console_e2e):
    _set_mode(console_e2e.db_file, "single")
    env = console_e2e

    who = _get(env, "/whoami", env.admin)
    assert who.status_code == 200
    assert who.json() == {"scope": "owner", "instance": {"mode": "single"}, "relation": "owner", "is_admin": True}
    _assert_no_username_leak(who, "/whoami")

    for path in ("/admin", "/settings", "/settings/themes", "/settings/tokens"):
        resp = _get(env, path, env.admin)
        assert resp.status_code == 302, path
        _assert_no_username_leak(resp, path)

    for slug in ADMIN_SECTIONS:
        path = f"/admin/{slug}"
        resp = _get(env, path, env.admin)
        _assert_console_page(resp, path)
        _assert_no_username_leak(resp, path)

    for slug in ("keys", *SETTINGS_SECTIONS):
        path = f"/settings/{slug}"
        resp = _get(env, path, env.admin)
        _assert_console_page(resp, path)
        _assert_no_username_leak(resp, path)


# --- 4. 레거시 302 호환 리다이렉트 (D4) -------------------------------------------------


@pytest.mark.parametrize("mode", MODES)
def test_legacy_me_redirects_to_settings_account(console_e2e, mode: str):
    _set_mode(console_e2e.db_file, mode)
    env = console_e2e

    resp = _get(env, "/me", env.admin)
    assert resp.status_code == 302, resp.status_code
    assert resp.headers["location"] == "/settings/account"
    _assert_console_page(_get(env, resp.headers["location"], env.admin), "/settings/account")
    if mode == "single":
        _assert_no_username_leak(resp, "/me")
    else:
        member = _get(env, "/me", env.alice)
        assert member.status_code == 302, member.status_code
        assert member.headers["location"] == "/settings/account"


@pytest.mark.parametrize("mode", MODES)
def test_legacy_syndication_preferences_redirects_for_admin(console_e2e, mode: str):
    _set_mode(console_e2e.db_file, mode)
    env = console_e2e
    base = "/syndication/preferences"

    expected = {
        "": "/settings/reader",
        "?tab=reader": "/settings/reader",
        "?tab=accounts": "/settings/connections",
        "?tab=feeds": "/settings/feeds",
        "?tab=tags": "/settings/tags",
        "?tab=rules": "/settings/rules",
        "?tab=outbound": "/settings/outbound",
        "?tab=opml": "/settings/opml",
        "?tab=mcp": "/settings/mcp",
        "?tab=webmcp": "/settings/webmcp",
        "?tab=idp": "/admin/idp",
        "?tab=userdb": "/admin/accounts",
    }
    for query, target in expected.items():
        resp = _get(env, base + query, env.admin)
        assert resp.status_code == 302, (query, resp.status_code)
        assert resp.headers["location"] == target, (query, resp.headers.get("location"))
        if mode == "single":
            _assert_no_username_leak(resp, base + query)

    unknown = _get(env, base + "?tab=does-not-exist", env.admin)
    assert unknown.status_code == 404

    theme_num = env.admin_theme.number
    with_theme = _get(env, f"{base}?tab=feeds&theme={theme_num}", env.admin)
    assert with_theme.status_code == 302
    assert with_theme.headers["location"] == f"/settings/feeds?theme={theme_num}"

    # 잘못된 ?theme=는 리다이렉트 전에 404(C1).
    assert _get(env, f"{base}?tab=feeds&theme=nonexistent", env.admin).status_code == 404


def test_legacy_syndication_preferences_redirects_for_member_in_network_mode(console_e2e):
    _set_mode(console_e2e.db_file, "network")
    env = console_e2e
    base = f"/@{MEMBER_USERNAME}/syndication/preferences"

    feeds = _get(env, base + "?tab=feeds", env.alice)
    assert feeds.status_code == 302, feeds.status_code
    assert feeds.headers["location"] == "/settings/feeds"

    for tab in ("idp", "userdb"):
        resp = _get(env, f"{base}?tab={tab}", env.alice)
        assert resp.status_code == 302, (tab, resp.status_code)
        assert resp.headers["location"] == "/settings/account", tab

    assert _get(env, base + "?tab=does-not-exist", env.alice).status_code == 404

    # 비-owner 호출자는 리다이렉트 대상을 얻지 못한다(403/404).
    for headers in (env.alice_collab, {}):
        resp = _get(env, base + "?tab=feeds", headers)
        assert resp.status_code in {403, 404}, resp.status_code
        assert "location" not in resp.headers
