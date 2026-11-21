"""``/settings`` Group 1 (내 계정 · 워크스페이스) 화면 테스트 (설계 P3-a).

모듈 인터페이스(``accounts_routes.console`` → ``console_shell.html``)와 SQLite I/O(claire.db, auth.db)를 검증한다.
모든 상태는 tmp_path에만 기록하고 CLAIRE_* 환경변수를 격리한다.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from claire import secret_scrub
from claire.accounts import server_tier
from claire.accounts.provider_policy import ServerTier
from claire.api import server
from claire.config import Settings
from claire.store import accounts as accounts_store
from claire.store import auth_db
from claire.store import db as dbm
from claire.store import provider_keys
from claire.store import usage_ledger
from claire.store.secrets import reset_secret_service
from claire.store.knowledge_base import KnowledgeBaseInfo, KnowledgeBaseManager
from claire.store.token_store import get_token_store, reset_token_stores

PUBLIC_URL = "http://127.0.0.1:8765"
ADMIN_USERNAME = "hidden_operator_p3a"
MEMBER_USERNAME = "alice_member"
OTHER_USERNAME = "bob_member"
TEST_MASTER_KEY_HEX = "22" * 32
KEY_CANARY = "canary-settings-key-5e6f7a8b9c0d1e2f"
ALICE_OAUTH_TOKEN = "oauth-alice-token-" + "a" * 24
BOB_OAUTH_TOKEN = "oauth-bob-token-" + "b" * 24


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


def _upsert_account(conn, account_id: int, public_id: int, username: str, role: str, *, profile_public: bool) -> None:
    now = time.time()
    conn.execute(
        "INSERT INTO accounts (id, public_id, username, role, status, display_name, profile_public, created_at, activated_at, updated_at) VALUES (?, ?, ?, ?, 'active', ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET public_id=excluded.public_id, username=excluded.username, role=excluded.role, status='active', display_name=excluded.display_name, profile_public=excluded.profile_public, updated_at=excluded.updated_at",
        (account_id, public_id, username, role, f"Display {account_id}", 1 if profile_public else 0, now, now, now),
    )
    conn.commit()


def _insert_theme(tm: KnowledgeBaseManager, settings: Settings, *, kb_id: int, seq: int, number: int, label: str, account_id: int, is_personal: bool = True) -> KnowledgeBaseInfo:
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
    tm.reload()
    return tm.get_kb(kb_id)


def _set_mode(db_file: Path, mode: str) -> None:
    conn = dbm.connect(db_file)
    try:
        accounts_store.AccountStore(conn).set_setting("mode", mode, updated_by=1)
    finally:
        conn.close()


@pytest.fixture
def env(tmp_path: Path):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)
    db_file = data_dir / "claire.db"
    conn = dbm.connect(db_file)
    try:
        dbm.init_db(conn)
        _upsert_account(conn, 1, (1 << 57) + 111, ADMIN_USERNAME, "admin", profile_public=False)
        _upsert_account(conn, 2, (1 << 57) + 222, MEMBER_USERNAME, "member", profile_public=True)
        _upsert_account(conn, 3, (1 << 57) + 333, OTHER_USERNAME, "member", profile_public=False)
    finally:
        conn.close()

    settings = Settings(
        CLAIRE_DB_PATH=str(db_file),
        CLAIRE_DATA_DIR=str(data_dir),
        CLAIRE_PROVIDER="mock",
        CLAIRE_ENVIRONMENT="development",
        CLAIRE_PUBLIC_URL=PUBLIC_URL,
        CLAIRE_MULTI_THEME=True,
    )
    tm = KnowledgeBaseManager(settings)
    admin_theme = _insert_theme(tm, settings, kb_id=11, seq=1, number=(1 << 56) | 11, label="Admin Vault", account_id=1, is_personal=False)
    alice_theme = _insert_theme(tm, settings, kb_id=20, seq=2, number=(2 << 56) | 20, label="Alice Garden", account_id=2)
    bob_theme = _insert_theme(tm, settings, kb_id=30, seq=3, number=(3 << 56) | 30, label="Bob Secret Lab", account_id=3)

    tok_store = get_token_store(data_dir=data_dir)
    admin_tok = tok_store.create_token(name="admin-owner", scope="owner", account_id=1)["token"]
    alice_tok = tok_store.create_token(name="alice-owner", scope="owner", account_id=2)["token"]

    app = server.create_app(settings, SimpleNamespace(provider=SimpleNamespace(name="mock")))
    client = TestClient(app, base_url=PUBLIC_URL)
    return SimpleNamespace(
        data_dir=data_dir,
        db_file=db_file,
        client=client,
        admin_theme=admin_theme,
        alice_theme=alice_theme,
        bob_theme=bob_theme,
        admin={"Authorization": f"Bearer {admin_tok}"},
        alice={"Authorization": f"Bearer {alice_tok}"},
    )


def _auth(env) -> object:
    return auth_db.connect_auth_db(auth_db.auth_db_path(env.data_dir))


def _get(env, path: str, headers: dict[str, str]):
    resp = env.client.get(path, headers=headers, follow_redirects=False)
    assert resp.status_code == 200, (path, resp.status_code)
    return resp


def _bootstrap(html: str) -> dict:
    start = html.index('<script id="console-bootstrap" type="application/json">') + len('<script id="console-bootstrap" type="application/json">')
    end = html.index("</script>", start)
    return json.loads(html[start:end])


def test_account_page_network_member_renders_controls_without_admin_markup(env):
    _set_mode(env.db_file, "network")
    conn = dbm.connect(env.db_file)
    try:
        server_tier.save_server_tier(conn, ServerTier(provider="none", max_documents=900, storage_mb=64, daily_ingests=12, monthly_tokens=5000), updated_by=1)
    finally:
        conn.close()
    auth = _auth(env)
    try:
        usage_ledger.record_tokens(auth, account_id=2, tokens_in=1200, tokens_out=34)
        usage_ledger.record_tokens(auth, account_id=3, tokens_in=777777, tokens_out=0)
    finally:
        auth.close()

    html = _get(env, "/settings/account", env.alice).text
    assert f"@{MEMBER_USERNAME}" in html
    assert 'data-account-field="profile_public"' in html
    assert 'data-settings-form="display-name"' in html
    assert 'data-settings-form="password"' in html
    assert 'data-settings-identities' in html
    assert 'data-settings-form="bundle-export"' in html
    assert '<script src="/static/js/console_account.js" defer></script>' in html
    # 불변식 3: 비관리자 응답에는 /admin/* 링크와 관리자 번들이 없다.
    assert "/admin/" not in html
    assert "console_admin.js" not in html
    # 할당량: 호출자 자신의 사용량과 서버 등급 상한만 나온다.
    assert "1,234 / 5,000" in html
    assert "777,777" not in html
    # 번들 내보내기 대상은 호출자 소유 테마뿐이다.
    assert "Alice Garden" in html
    assert "Bob Secret Lab" not in html
    assert "Admin Vault" not in html
    assert OTHER_USERNAME not in html
    assert ADMIN_USERNAME not in html
    assert 'data-lifecycle="member"' in html
    boot = _bootstrap(html)
    assert boot["section"] == "account"
    assert boot["settings_account"]["has_password"] is False
    assert boot["settings_account"]["identity_providers"] == []


def test_account_page_single_mode_admin_shows_username_and_profile_public(env):
    # Rule S9 is withdrawn and the account actor of the admin federates in single mode (KNOWLEDGE_FEDERATION_DESIGN.md Section 15 rules 4 and 5).
    _set_mode(env.db_file, "single")
    resp = _get(env, "/settings/account", env.admin)
    html = resp.text
    assert f'<dd data-account-field="username">@{ADMIN_USERNAME}</dd>' in html
    assert 'data-account-field="profile_public"' in html
    assert 'data-settings-toggle="profile-public"' in html
    assert 'data-lifecycle="admin"' in html
    # "Admin Vault" is a shared knowledge base after version 28: the account page lists only the personal knowledge base of the account.
    assert "Admin Vault" not in html


def test_account_page_lists_only_enabled_idp_providers_for_linking(env):
    _set_mode(env.db_file, "network")
    idp_file = env.data_dir / "auth" / "idp_providers.json"
    idp_file.parent.mkdir(parents=True, exist_ok=True)
    idp_file.write_text(
        json.dumps(
            {
                "providers": [
                    {"id": "corp-oidc", "type": "oidc", "display_name": "Corp SSO", "client_id": "cid-1", "enabled": True, "issuer": "https://idp.example.test"},
                    {"id": "off-gh", "type": "github", "display_name": "Disabled GitHub", "client_id": "cid-2", "enabled": False},
                ]
            }
        ),
        encoding="utf-8",
    )
    html = _get(env, "/settings/account", env.alice).text
    assert 'data-identity-link="corp-oidc"' in html
    assert "Corp SSO" in html
    assert "off-gh" not in html
    assert "cid-1" not in html
    assert _bootstrap(html)["settings_account"]["identity_providers"] == [{"id": "corp-oidc", "display_name": "Corp SSO", "type": "oidc"}]


def test_keys_page_network_member_is_write_only_and_shows_server_tier(env):
    _set_mode(env.db_file, "network")
    auth = _auth(env)
    try:
        meta = provider_keys.put_key(auth, account_id=2, provider="gemini", api_key=KEY_CANARY, status="active")
    finally:
        auth.close()
    html = _get(env, "/settings/keys", env.alice).text
    assert 'data-keys-mode="byok"' in html
    assert 'data-settings-form="provider-key-put"' in html
    assert 'data-settings-form="provider-key-verify"' in html
    assert 'data-settings-form="provider-key-delete"' in html
    assert "data-server-tier" in html
    assert meta.fingerprint in html
    assert KEY_CANARY not in html
    assert KEY_CANARY[:12] not in html
    assert "/admin/" not in html
    assert "console_admin.js" not in html


@pytest.mark.parametrize("mode", ["single", "network"])
def test_keys_page_admin_gets_personal_key_forms_and_provider_settings_link(env, mode: str):
    _set_mode(env.db_file, mode)
    html = _get(env, "/settings/keys", env.admin).text
    assert 'data-keys-mode="byok"' in html and _bootstrap(html)["settings_account"]["keys_mode"] == "byok"
    assert 'href="/admin/providers"' in html and "data-keys-server-providers" in html
    for form in ("provider-key-put", "provider-key-verify", "provider-key-delete"):
        assert f'data-settings-form="{form}"' in html
    # The server tier does not apply to the administrator account: the page shows own usage, not the tier.
    assert "data-server-tier" not in html and 'data-usage-stat="tokens"' in html
    if mode == "single":
        assert ADMIN_USERNAME not in html


def test_keys_page_single_mode_member_gets_neutral_notice_without_admin_link(env):
    _set_mode(env.db_file, "single")
    html = _get(env, "/settings/keys", env.alice).text
    assert 'data-keys-mode="unavailable"' in html
    assert 'data-settings-form="provider-key-put"' not in html
    assert "/admin/" not in html
    assert MEMBER_USERNAME not in html


def test_tokens_page_lists_only_callers_own_oauth_clients(env):
    _set_mode(env.db_file, "network")
    now = time.time()
    conn = dbm.connect(env.db_file)
    try:
        conn.execute("INSERT INTO oauth_clients (client_id, client_secret, client_name, redirect_uris, created_at) VALUES (?, NULL, ?, ?, ?)", ("cli-alice", "Alice Desktop Agent", json.dumps(["http://127.0.0.1:9001/cb"]), now))
        conn.execute("INSERT INTO oauth_clients (client_id, client_secret, client_name, redirect_uris, created_at) VALUES (?, NULL, ?, ?, ?)", ("cli-bob", "Bob Private Agent", json.dumps(["http://127.0.0.1:9002/cb"]), now))
        conn.execute("INSERT INTO oauth_tokens (token, refresh_token, client_id, scope, created_at, expires_at, account_id) VALUES (?, ?, 'cli-alice', 'readonly', ?, ?, 2)", (ALICE_OAUTH_TOKEN, ALICE_OAUTH_TOKEN + "-r", now, now + 3600))
        conn.execute("INSERT INTO oauth_tokens (token, refresh_token, client_id, scope, created_at, expires_at, account_id) VALUES (?, ?, 'cli-bob', 'readonly', ?, ?, 3)", (BOB_OAUTH_TOKEN, BOB_OAUTH_TOKEN + "-r", now, now + 3600))
        conn.commit()
    finally:
        conn.close()

    html = _get(env, "/settings/tokens", env.alice).text
    assert 'id="settings-tokens-host"' in html
    assert "Alice Desktop Agent" in html
    assert "cli-alice" in html
    assert "Bob Private Agent" not in html
    assert "cli-bob" not in html
    assert ALICE_OAUTH_TOKEN not in html
    assert BOB_OAUTH_TOKEN not in html
    assert "/admin/" not in html
    clients = _bootstrap(html)["settings_account"]["oauth_clients"]
    assert [c["client_id"] for c in clients] == ["cli-alice"]
    assert clients[0]["token_count"] == 1


def test_kb_page_member_hosts_kb_console_without_admin_markup(env):
    """``/settings/kb`` has the function of the old ``/settings/themes`` (SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md Section 10.3)."""
    _set_mode(env.db_file, "network")
    old = env.client.get("/settings/themes", headers=env.alice, follow_redirects=False)
    assert (old.status_code, old.headers["location"]) == (308, "/settings/kb")
    html = _get(env, "/settings/kb", env.alice).text
    assert 'id="settings-kb-host"' in html
    # No slot count: an account has one personal knowledge base and makes shared ones without a limit (design Section 10.1).
    assert "My themes:" not in html and "settings-themes-quota" not in html
    assert "/admin/" not in html
    assert 'href="/moderation/oversight"' not in html
    assert 'href="/moderation/themes"' not in html
    assert 'href="/moderation/kb"' not in html
    assert 'href="/moderation"' in html
    assert "admin-themes-summary" not in html
    assert "Bob Secret Lab" not in html
    bootstrap = _bootstrap(html)
    assert "theme_slots" not in bootstrap and bootstrap["personal_kb_number"].isdigit()


def test_admin_theme_and_token_settings_still_redirect(env):
    _set_mode(env.db_file, "network")
    # The retired page ``/settings/themes`` is an old web link: 308 to ``/moderation/themes``. ``/settings/tokens`` is a section that the admin has in ``/admin``: 302.
    for slug, status, target in (("themes", 308, "/moderation/themes"), ("tokens", 302, "/admin/tokens")):
        resp = env.client.get(f"/settings/{slug}", headers=env.admin, follow_redirects=False)
        assert resp.status_code == status
        assert resp.headers["location"] == target


def test_moderation_routes_and_legacy_redirects(env):
    _set_mode(env.db_file, "network")
    mod_index = env.client.get("/moderation", headers=env.alice, follow_redirects=False)
    assert (mod_index.status_code, mod_index.headers["location"]) == (302, "/moderation/kb")
    mod_index_kb = env.client.get(f"/moderation?kb={env.alice_theme.number}", headers=env.alice, follow_redirects=False)
    assert (mod_index_kb.status_code, mod_index_kb.headers["location"]) == (302, f"/moderation/kb?kb={env.alice_theme.number}")
    assert env.client.get("/moderation?kb=nonexistent", headers=env.alice, follow_redirects=False).status_code == 404
    assert env.client.get("/moderation", follow_redirects=False).status_code == 404

    for path in ("/moderation/kb", f"/moderation/kb/{env.admin_theme.number}"):
        html = _get(env, path, env.alice).text
        assert 'id="settings-kb-manage-host"' in html
        assert 'data-active-area="moderation"' in html
        assert 'data-active-section="kb_manage"' in html
        assert "/admin/" not in html
        assert "console_admin.js" not in html
        assert "console.js.admin." not in html
        assert 'href="/moderation/oversight"' not in html
        assert 'href="/moderation/themes"' not in html

    assert env.client.get(f"/moderation/kb/{env.alice_theme.number}", headers=env.alice, follow_redirects=False).status_code == 404
    assert env.client.get("/moderation/kb/999999", headers=env.alice, follow_redirects=False).status_code == 404

    for path, marker, section in (("/moderation/oversight", 'id="admin-kb-host"', "kb"), ("/moderation/themes", 'id="admin-themes-host"', "themes")):
        assert env.client.get(path, headers=env.alice, follow_redirects=False).status_code == 404
        assert env.client.get(path, follow_redirects=False).status_code == 404
        adm_html = _get(env, path, env.admin).text
        assert marker in adm_html
        assert 'data-active-area="moderation"' in adm_html
        assert f'data-active-section="{section}"' in adm_html
        assert "console_admin.js" in adm_html

    for legacy_path, target in (("/settings/kb/manage", "/moderation/kb"), (f"/settings/kb/{env.admin_theme.number}", f"/moderation/kb/{env.admin_theme.number}")):
        redir = env.client.get(legacy_path, headers=env.alice, follow_redirects=False)
        assert (redir.status_code, redir.headers["location"]) == (308, target)
    assert env.client.get(f"/settings/kb/{env.alice_theme.number}", headers=env.alice, follow_redirects=False).status_code == 404

    for legacy_path, target in (("/admin/kb", "/moderation/oversight"), ("/admin/themes", "/moderation/themes")):
        assert env.client.get(legacy_path, headers=env.alice, follow_redirects=False).status_code == 404
        redir = env.client.get(legacy_path, headers=env.admin, follow_redirects=False)
        assert (redir.status_code, redir.headers["location"]) == (308, target)


def test_account_and_keys_pages_render_extraction_mode_controls_and_guides(env):
    _set_mode(env.db_file, "network")
    account_html = _get(env, "/settings/account", env.alice).text
    assert 'id="settings-extraction-mode"' in account_html
    assert 'id="pref-account-extraction-mode"' in account_html
    assert 'data-settings-extraction-mode' in account_html
    assert 'data-account-field="extraction_mode"' in account_html
    assert '<option value="provider" selected>' in account_html
    assert '<option value="client">' in account_html
    assert 'data-extraction-client-guide hidden' in account_html
    assert 'href="/settings/mcp#pref-mcp-agent-guide"' in account_html
    assert "/admin/" not in account_html

    for server_mode in ("network", "single"):
        _set_mode(env.db_file, server_mode)
        keys_html = _get(env, "/settings/keys", env.alice).text
        assert 'id="settings-keys-extraction-guide"' in keys_html
        assert 'data-extraction-mode="provider"' in keys_html
        assert 'href="/settings/account#settings-extraction-mode"' in keys_html
        assert 'href="/settings/mcp#pref-mcp-agent-guide"' in keys_html
        assert "/admin/" not in keys_html
        assert ADMIN_USERNAME not in keys_html
        if server_mode == "single":
            assert MEMBER_USERNAME not in keys_html


def test_e2e_extraction_mode_patch_updates_account_keys_and_mcp_guides(env):
    _set_mode(env.db_file, "network")
    patch_resp = env.client.patch("/api/me", headers=env.alice, json={"extraction_mode": "client"})
    assert patch_resp.status_code == 200
    assert patch_resp.json()["extraction_mode"] == "client"

    account_html = _get(env, "/settings/account", env.alice).text
    assert '<option value="client" selected>' in account_html
    assert 'data-extraction-client-guide style=' in account_html
    assert 'data-extraction-client-guide hidden' not in account_html

    keys_html = _get(env, "/settings/keys", env.alice).text
    assert 'id="settings-keys-extraction-guide" data-extraction-mode="client"' in keys_html
    assert "/admin/" not in keys_html

    mcp_html = _get(env, "/settings/mcp", env.alice).text
    assert 'id="pref-mcp-agent-guide" data-extraction-mode="client"' in mcp_html
    assert '<code id="pref-mcp-extraction-mode" class="pref-mono">client</code>' in mcp_html
    assert "/admin/" not in mcp_html
