"""관리자(`/admin/*`)·사용자(`/settings/*`) 통합 콘솔 E2E (설계 ADMIN_AND_SETTINGS_PATH_SEPARATION_DESIGN P4 단계 5).

실제 `server.create_app` 앱을 starlette TestClient로 구동해 다음 흐름을 끝까지 검증한다.

1. 관리자 E2E: `/` '관리 및 설정' 드로어(⚡ 프로바이더 · 📏 적재 제한 · 🛠️ 관리자 콘솔) → `/admin` 진입 → 통합 사이드바로 모든 `/admin/*`·`/settings/*` 섹션 이동 → D3 리다이렉트 → 텔레메트리 API.
2. 멤버 격리 E2E(network): `is_admin` false, `/settings/*`에 관리자 링크·DOM·번들 없음, `/account/providers` BYOK 쓰기 전용 흐름, 모든 `/admin*`이 404(403·302 아님).
3. single mode: rule S9 is withdrawn (KNOWLEDGE_FEDERATION_DESIGN.md Section 15 rule 4). `/whoami` has no USERNAME; `/account/profile` shows it in both modes.
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

from claire.core import secret_scrub
from claire.api import server
from claire.core.config import Settings
from claire.store import accounts as accounts_store
from claire.store import db as dbm
from claire.store import provider_keys
from claire.store.secrets import reset_secret_service
from claire.store.knowledge_base import KnowledgeBaseInfo, KnowledgeBaseManager
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

ADMIN_SECTIONS: tuple[str, ...] = ("server", "federation", "accounts", "idp", "providers", "limits", "tokens", "mcp", "webmcp")
MODERATION_ADMIN_SECTIONS: tuple[tuple[str, str], ...] = (("kb", "/moderation/oversight"), ("themes", "/moderation/themes"))
# The owner sections as (slug, path): the account pages are in /account/*, the knowledge base pages are in /settings/*. The administrator has /admin/tokens in place of /account/tokens (D3).
OWNER_SECTIONS: tuple[tuple[str, str], ...] = (
    *((slug, f"/account/{slug}") for slug in ("profile", "security", "invites", "federation", "providers", "mcp", "webmcp")),
    *((slug, f"/settings/{slug}") for slug in ("kb", "reader", "connections", "feeds", "tags", "rules", "outbound", "opml")),
)
MEMBER_OWNER_SECTIONS: tuple[tuple[str, str], ...] = (*OWNER_SECTIONS, ("tokens", "/account/tokens"))
ADMIN_PROBE_PATHS: tuple[str, ...] = ("/admin", *(f"/admin/{slug}" for slug in ADMIN_SECTIONS), "/admin/kb", "/admin/themes", "/moderation/oversight", "/moderation/themes", "/api/admin/mcp/telemetry")


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


def _insert_theme(tm: KnowledgeBaseManager, settings: Settings, *, kb_id: int, seq: int, number: int, label: str, account_id: int, is_personal: bool) -> KnowledgeBaseInfo:
    conn = dbm.connect(settings.db_file)
    now = time.time()
    try:
        conn.execute(
            "INSERT INTO knowledge_bases (id, seq, number, label, description, icon, is_personal, is_public, account_id, db_path, default_focus, created_at, updated_at) VALUES (?, ?, ?, ?, ?, '📁', ?, 0, ?, ?, '', ?, ?)",
            (kb_id, seq, number, label, f"Desc {label}", 1 if is_personal else 0, account_id, str(settings.db_file), now, now),
        )
        conn.commit()
    finally:
        conn.close()
    tm.reload()
    return tm.get_kb(kb_id)


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
        CLAIRE_MULTI_THEME=True,
    )
    tm = KnowledgeBaseManager(settings)
    admin_theme = _insert_theme(tm, settings, kb_id=11, seq=1, number=(1 << 56) | 11, label="Admin Console Vault", account_id=1, is_personal=False)
    alice_theme = _insert_theme(tm, settings, kb_id=20, seq=2, number=(2 << 56) | 20, label="Alice Console Garden", account_id=2, is_personal=True)
    bob_theme = _insert_theme(tm, settings, kb_id=30, seq=3, number=(3 << 56) | 30, label="Bob Console Secret", account_id=3, is_personal=True)

    tok_store = get_token_store(data_dir=data_dir)
    admin_tok = tok_store.create_token(name="admin-owner-e2e", scope="owner", account_id=1)["token"]
    alice_tok = tok_store.create_token(name="alice-owner-e2e", scope="owner", account_id=2)["token"]
    alice_collab_tok = tok_store.create_token(name="alice-collab-e2e", scope="write", account_id=2)["token"]

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


def _sidebar_html(body: str) -> str:
    return body.split('<nav class="console-sidebar"', 1)[1].split("</nav>", 1)[0]


def _assert_admin_sidebar(body: str, path: str) -> None:
    # 설정·관리 콘솔 사이드바: 사용자 그룹 1–3, 관리자 그룹 4–5만 있고 중재 경로(/moderation/*)와 중복·구경로는 없다.
    sidebar = _sidebar_html(body)
    for slug, section_path in OWNER_SECTIONS:
        assert f'href="{section_path}"' in sidebar, (path, slug)
    for slug in ADMIN_SECTIONS:
        assert f'href="/admin/{slug}"' in sidebar, (path, slug)
    assert 'href="/moderation/kb"' not in sidebar, path
    assert 'href="/moderation/oversight"' not in sidebar, path
    assert 'href="/moderation/themes"' not in sidebar, path
    assert 'id="moderationlinkbtn"' in body and 'href="/moderation"' in body, path
    assert 'href="/admin/kb"' not in body, path
    assert 'href="/admin/themes"' not in body, path
    assert 'href="/settings/kb/manage"' not in body, path
    assert 'href="/settings/themes"' not in body, path
    assert 'href="/account/tokens"' not in body, path


def _assert_moderation_sidebar(body: str, path: str, *, is_admin: bool) -> None:
    sidebar = _sidebar_html(body)
    assert 'href="/moderation/kb"' in sidebar, path
    if is_admin:
        assert 'href="/moderation/oversight"' in sidebar, path
        assert 'href="/moderation/themes"' in sidebar, path
    else:
        assert 'href="/moderation/oversight"' not in sidebar, path
        assert 'href="/moderation/themes"' not in sidebar, path
    for slug, section_path in OWNER_SECTIONS:
        assert f'href="{section_path}"' not in sidebar, (path, slug)
    for slug in ADMIN_SECTIONS:
        assert f'href="/admin/{slug}"' not in sidebar, (path, slug)


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

    # The graph root is in the namespace /@{username}/ in both modes (KNOWLEDGE_FEDERATION_DESIGN.md Section 15 rule 2: single mode sends "/" to it).
    root_path = f"/@{ADMIN_USERNAME}/"
    root = _get(env, root_path, env.admin)
    assert root.status_code == 200, root.status_code
    html = root.text
    # 상세 메뉴의 '관리 및 설정' 패널은 없다. 관리·설정 기능은 콘솔에만 있다.
    assert 'id="manage-section"' not in html
    for gone in ("providermanagebtn", "limitmanagebtn", "thememanagebtn", "tokenmanagebtn", "openProviderManager", "openLimitManager", "openThemeManager", "openTokenManager", "ClaireManagePanels"):
        assert gone not in html, gone
    # 우측 상단 공통 아이콘 순서는 오른쪽 끝부터 햄버거, 신디케이션, 유저홈이며 콘솔 단추는 햄버거 메뉴 안에 있다.
    topbar = html[html.index('<div class="appbar-end" id="viewoptions">'):html.index('</header>')]
    assert 'id="authstate"' not in html
    assert topbar.index('id="homelinkbtn"') < topbar.index('id="syndlink"') < topbar.index('id="hamburger-btn"')
    assert topbar.index('id="pop-hamburger-menu"') < topbar.index('id="consolelinkbtn"') < topbar.index('action="/logout"')

    who = _get(env, f"/@{ADMIN_USERNAME}/whoami", env.admin)
    assert who.status_code == 200
    assert who.json()["is_admin"] is True
    assert who.json()["scope"] == "owner"
    assert who.json()["server"] == {"mode": mode}

    # 인라인된 app.js가 is_admin=true일 때 ClaireAppBar.setConsoleLink로 햄버거 내 #consolelinkbtn을 /admin + wrench 아이콘으로 승격한다.
    assert "IS_ADMIN = d.is_admin === true" in html
    assert "href: IS_ADMIN ? '/admin' : '/account'" in html
    assert "icon: IS_ADMIN ? 'wrench' : 'settings'" in html


@pytest.mark.parametrize("mode", MODES)
def test_admin_console_navigation_across_all_sections(console_e2e, mode: str):
    _set_mode(console_e2e.db_file, mode)
    env = console_e2e

    entry = _get(env, "/admin", env.admin)
    assert entry.status_code == 302
    assert entry.headers["location"] == "/admin/server"

    landing = _assert_console_page(_get(env, entry.headers["location"], env.admin), "/admin/server")
    _assert_admin_sidebar(landing, "/admin/server")

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

    assert 'href="/moderation"' in landing
    for slug, path in MODERATION_ADMIN_SECTIONS:
        body = _assert_console_page(_get(env, path, env.admin), path)
        _assert_moderation_sidebar(body, path, is_admin=True)
        assert 'data-active-area="moderation"' in body, path
        assert f'data-active-section="{slug}"' in body, path
        assert '<script src="/static/js/console_admin.js" defer></script>' in body, path
        assert "Alice Console Garden" not in body, path
        assert "Bob Console Secret" not in body, path

    mod_kb_body = _assert_console_page(_get(env, "/moderation/kb", env.admin), "/moderation/kb")
    _assert_moderation_sidebar(mod_kb_body, "/moderation/kb", is_admin=True)
    assert 'href="/moderation/oversight"' in mod_kb_body
    assert 'href="/moderation/themes"' in mod_kb_body
    assert 'data-active-area="moderation"' in mod_kb_body
    assert 'data-active-section="kb_manage"' in mod_kb_body
    assert 'id="settings-kb-manage-host"' in mod_kb_body
    assert "console_admin.js" not in mod_kb_body
    assert "console.js.admin." not in mod_kb_body

    _assert_console_page(_get(env, f"/moderation/kb/{env.admin_theme.number}", env.admin), f"/moderation/kb/{env.admin_theme.number}")
    assert _get(env, f"/moderation/kb/{env.alice_theme.number}", env.admin).status_code == 404

    for slug, path in OWNER_SECTIONS:
        body = _assert_console_page(_get(env, path, env.admin), path)
        _assert_admin_sidebar(body, path)
        assert f'data-active-section="{slug}"' in body, path

    # D3 & legacy compatibility redirects: /account/tokens -> 302 /admin/tokens; /moderation -> 302 /moderation/kb; legacy /settings/themes, /settings/kb/manage, /settings/kb/{n}, /admin/kb, /admin/themes -> 308.
    for path, status, target in (
        ("/moderation", 302, "/moderation/kb"),
        ("/account/tokens", 302, "/admin/tokens"),
        ("/settings/themes", 308, "/moderation/themes"),
        ("/settings/kb/manage", 308, "/moderation/kb"),
        (f"/settings/kb/{env.admin_theme.number}", 308, f"/moderation/kb/{env.admin_theme.number}"),
        ("/admin/kb", 308, "/moderation/oversight"),
        ("/admin/themes", 308, "/moderation/themes"),
    ):
        resp = _get(env, path, env.admin)
        assert resp.status_code == status, path
        assert resp.headers["location"] == target, path
        _assert_console_page(_get(env, resp.headers["location"], env.admin), target)

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
    assert who.json() == {"scope": "owner", "server": {"mode": "network"}, "relation": "owner", "is_admin": False}

    root = _get(env, f"/@{MEMBER_USERNAME}/", env.alice)
    assert root.status_code == 200, root.status_code
    # 멤버 루트에도 드로어 관리 패널이 없고, 상단바 아이콘은 기본값 /account다.
    assert 'id="manage-section"' not in root.text
    assert "providermanagebtn" not in root.text
    assert "limitmanagebtn" not in root.text
    assert re.search(r'<a id="consolelinkbtn"[^>]*href="/account"', root.text)

    entry = _get(env, "/account", env.alice)
    assert entry.status_code == 302
    assert entry.headers["location"] == "/account/profile"
    settings_entry = _get(env, "/settings", env.alice)
    assert (settings_entry.status_code, settings_entry.headers["location"]) == (302, "/settings/reader")
    # The old path of an account page goes to the new path with 308.
    old_keys = _get(env, "/settings/keys", env.alice)
    assert (old_keys.status_code, old_keys.headers["location"]) == (308, "/account/providers")

    for path in (*(section_path for _slug, section_path in MEMBER_OWNER_SECTIONS), "/moderation/kb"):
        body = _assert_console_page(_get(env, path, env.alice), path)
        # 불변식 3: 서버 측에서 관리자 링크·섹션·번들을 제거한다.
        assert "/admin" not in body, path
        assert "console_admin.js" not in body, path
        assert "console.js.admin." not in body, path
        assert 'data-active-area="admin"' not in body, path
        assert "data-admin-panel-host" not in body, path
        assert "admin-themes-summary" not in body, path
        assert "pref-mcp-audit-tbody" not in body, path
        assert "pref-idp-tbody" not in body, path
        assert 'href="/moderation/oversight"' not in body, path
        assert 'href="/moderation/themes"' not in body, path
        assert "System administration and settings (administrator)" not in body, path
        assert "Server · Accounts · Authentication (administrator)" not in body, path
        if path == "/moderation/kb":
            _assert_moderation_sidebar(body, path, is_admin=False)
        else:
            sidebar = _sidebar_html(body)
            assert 'href="/moderation/kb"' not in sidebar, path
            assert 'id="moderationlinkbtn"' in body and 'href="/moderation"' in body, path
            for slug2, section_path in MEMBER_OWNER_SECTIONS:
                assert f'href="{section_path}"' in sidebar, (path, slug2)
        assert "Bob Console Secret" not in body, path
        assert "Admin Console Vault" not in body, path

    for path, status, target in (
        ("/moderation", 302, "/moderation/kb"),
        ("/settings/kb/manage", 308, "/moderation/kb"),
        (f"/settings/kb/{env.admin_theme.number}", 308, f"/moderation/kb/{env.admin_theme.number}"),
        ("/settings/themes", 308, "/settings/kb"),
    ):
        redir = _get(env, path, env.alice)
        assert (redir.status_code, redir.headers["location"]) == (status, target), path
    assert _get(env, f"/settings/kb/{env.alice_theme.number}", env.alice).status_code == 404

    for prefixed in (f"/@{MEMBER_USERNAME}/moderation", f"/@{MEMBER_USERNAME}/moderation/kb", f"/kb/{env.admin_theme.number}/moderation/oversight"):
        assert _get(env, prefixed, env.alice).status_code == 404, prefixed
        assert _get(env, prefixed, env.admin).status_code == 404, prefixed

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

    page = _assert_console_page(_get(env, "/account/providers", env.alice), "/account/providers")
    assert 'data-keys-mode="byok"' in page
    assert 'data-settings-form="provider-key-put"' in page
    assert "/admin" not in page

    session_headers = _member_session_headers(env, 2)
    env.client.cookies.clear()
    before = env.client.get("/api/me/provider-keys", headers=session_headers)
    assert before.status_code == 200
    assert before.json() == {"keys": []}

    env.client.cookies.clear()
    put = env.client.put("/api/me/provider-keys", headers=session_headers, json={"provider": "gemini", "api_key": BYOK_CANARY})
    assert put.status_code == 200, put.text
    meta = put.json()["key"]
    assert meta["provider"] == "gemini"
    assert meta["status"] == "active"
    assert verify_calls == [("gemini", BYOK_CANARY)]

    env.client.cookies.clear()
    listed = env.client.get("/api/me/provider-keys", headers=session_headers)
    env.client.cookies.clear()
    assert listed.status_code == 200
    assert listed.json() == {"keys": [meta]}

    page_after = _assert_console_page(_get(env, "/account/providers", env.alice), "/account/providers")
    assert meta["fingerprint"] in page_after

    for resp in (put, listed, page_after_resp := _get(env, "/account/providers", env.alice)):
        blob = resp.text + repr(list(resp.headers.items()))
        assert BYOK_CANARY not in blob
        assert BYOK_CANARY[:16] not in blob
    assert page_after_resp.status_code == 200


# --- 3. single mode and the USERNAME (rule S9 is withdrawn) ------------------------------


def test_single_mode_whoami_has_no_username_and_the_account_page_shows_it(console_e2e):
    _set_mode(console_e2e.db_file, "single")
    env = console_e2e

    # Single mode sends the root paths to the namespace of the admin (Section 15 rule 2): "/whoami" gives 308, and the answer of the target has no USERNAME.
    assert _get(env, "/whoami", env.admin).status_code == 308
    who = _get(env, f"/@{ADMIN_USERNAME}/whoami", env.admin)
    assert who.status_code == 200
    assert who.json() == {"scope": "owner", "server": {"mode": "single"}, "relation": "owner", "is_admin": True}
    _assert_no_username_leak(who, "/whoami")

    for path, status in (("/admin", 302), ("/account", 302), ("/settings", 302), ("/moderation", 302), ("/settings/themes", 308), ("/account/tokens", 302), ("/settings/kb/manage", 308), ("/admin/kb", 308), ("/admin/themes", 308)):
        resp = _get(env, path, env.admin)
        assert resp.status_code == status, path

    for slug in ADMIN_SECTIONS:
        path = f"/admin/{slug}"
        _assert_console_page(_get(env, path, env.admin), path)

    for _slug, path in MODERATION_ADMIN_SECTIONS:
        _assert_console_page(_get(env, path, env.admin), path)
    _assert_console_page(_get(env, "/moderation/kb", env.admin), "/moderation/kb")

    for _slug, path in OWNER_SECTIONS:
        _assert_console_page(_get(env, path, env.admin), path)

    # The account actor of the admin federates in single mode (Section 15 rule 5): the account page shows the USERNAME and the switch of the public profile.
    account = _get(env, "/account/profile", env.admin)
    assert f'<dd data-account-field="username">@{ADMIN_USERNAME}</dd>' in account.text
    assert 'data-settings-toggle="profile-public"' in account.text


# --- 4. 레거시 302 호환 리다이렉트 (D4) -------------------------------------------------


@pytest.mark.parametrize("mode", MODES)
def test_legacy_me_redirects_to_settings_account(console_e2e, mode: str):
    _set_mode(console_e2e.db_file, mode)
    env = console_e2e

    resp = _get(env, "/me", env.admin)
    assert resp.status_code == 302, resp.status_code
    assert resp.headers["location"] == "/account/profile"
    _assert_console_page(_get(env, resp.headers["location"], env.admin), "/account/profile")
    if mode == "single":
        _assert_no_username_leak(resp, "/me")
    else:
        member = _get(env, "/me", env.alice)
        assert member.status_code == 302, member.status_code
        assert member.headers["location"] == "/account/profile"


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
        "?tab=mcp": "/account/mcp",
        "?tab=webmcp": "/account/webmcp",
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

    # ``?kb=`` of the settings takes only the personal knowledge base of the caller (a shared knowledge base gets 404).
    theme_num = KnowledgeBaseManager(env.settings).personal_kb_for(1).number
    with_theme = _get(env, f"{base}?tab=feeds&kb={theme_num}", env.admin)
    assert with_theme.status_code == 302
    assert with_theme.headers["location"] == f"/settings/feeds?kb={theme_num}"

    # 잘못된 ?kb=는 리다이렉트 전에 404(C1).
    assert _get(env, f"{base}?tab=feeds&kb=nonexistent", env.admin).status_code == 404


def test_legacy_syndication_preferences_redirects_for_member_in_network_mode(console_e2e):
    _set_mode(console_e2e.db_file, "network")
    env = console_e2e
    base = "/syndication/preferences"

    # Prefixed path under /@username is rejected with 404 (rule N5).
    assert _get(env, f"/@{MEMBER_USERNAME}/syndication/preferences?tab=feeds", env.alice).status_code == 404

    feeds = _get(env, base + "?tab=feeds", env.alice)
    assert feeds.status_code == 302, feeds.status_code
    assert feeds.headers["location"] == "/settings/feeds"

    for tab in ("idp", "userdb"):
        resp = _get(env, f"{base}?tab={tab}", env.alice)
        assert resp.status_code == 302, (tab, resp.status_code)
        assert resp.headers["location"] == "/account/profile", tab

    assert _get(env, base + "?tab=does-not-exist", env.alice).status_code == 404

    # 비-owner 호출자는 리다이렉트 대상을 얻지 못한다(403/404).
    for headers in (env.alice_collab, {}):
        resp = _get(env, base + "?tab=feeds", headers)
        assert resp.status_code in {403, 404}, resp.status_code
        assert "location" not in resp.headers
