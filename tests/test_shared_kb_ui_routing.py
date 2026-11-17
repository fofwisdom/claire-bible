"""Interface contract tests for Shared KB frontend routes, console isolation, and account/admin summaries."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from claire import ids
from claire.api import server
from claire.api.accounts_routes import admin as admin_routes
from claire.config import Settings
from claire.store import accounts as accounts_store
from claire.store import db as dbm
from claire.store.secrets import reset_secret_service
from claire.store.token_store import get_token_store, reset_token_stores


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


@pytest.fixture
def ui_env(tmp_path: Path):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)
    db_file = data_dir / "claire.db"
    conn = dbm.connect(db_file)
    dbm.init_db(conn)
    salt = dbm.read_id_salt(conn)
    now = time.time()
    admin_pub = ids.timestamp_id("accounts", salt=salt, unix_ms=int(now * 1000) + 1)
    alice_pub = ids.timestamp_id("accounts", salt=salt, unix_ms=int(now * 1000) + 2)
    conn.execute(
        "UPDATE accounts SET public_id=?, username='opadmin', role='admin', status='active', display_name='Op Admin', profile_public=1 WHERE id=1",
        (admin_pub,),
    )
    conn.execute(
        "INSERT INTO accounts (id, public_id, username, role, status, display_name, profile_public, created_at, activated_at, updated_at) VALUES (2, ?, 'alice', 'member', 'active', 'Alice', 1, ?, ?, ?)",
        (alice_pub, now, now, now),
    )
    alice_theme_pub = ids.timestamp_id("themes", salt=salt, unix_ms=int(now * 1000) + 3)
    conn.execute(
        "INSERT INTO themes (id, seq, number, label, description, icon, is_default, is_public, is_collaborator_accessible, account_id, db_path, default_focus, created_at, updated_at) VALUES (10, 10, ?, 'Alice Personal', '', '', 1, 0, 0, 2, ?, '', ?, ?)",
        (alice_theme_pub, str(db_file), now, now),
    )
    accounts_store.AccountStore(conn).set_setting("mode", "network", updated_by=1)
    conn.commit()
    conn.close()

    settings = Settings(
        db_path=str(db_file),
        data_dir=str(data_dir),
        vault_path=str(tmp_path / "vault"),
        public_url="http://127.0.0.1:8765",
        environment="development",
        CLAIRE_MULTI_THEME=True,
    )
    app = server.create_app(settings)
    store = get_token_store(data_dir=data_dir)
    admin_tok = store.create_token("admin-owner", "owner", account_id=1)["token"]
    alice_tok = store.create_token("alice-owner", "owner", account_id=2)["token"]
    client = TestClient(app, base_url=settings.public_url, follow_redirects=False)
    return client, {"Authorization": f"Bearer {admin_tok}"}, {"Authorization": f"Bearer {alice_tok}"}


def test_shared_kb_console_routes_and_admin_isolation(ui_env):
    client, admin_hdr, alice_hdr = ui_env

    # Settings KB routes work for active owners and isolate admin bundles/links.
    for path, marker in (
        ("/settings/kb", 'id="settings-kb-host"'),
        ("/settings/kb/manage", 'id="settings-kb-manage-host"'),
        ("/settings/kb/42", 'id="settings-kb-manage-host"'),
    ):
        resp = client.get(path, headers=alice_hdr)
        assert resp.status_code == 200, path
        assert marker in resp.text
        assert "console_kb.js" in resp.text
        assert "console_admin.js" not in resp.text
        assert "/admin/" not in resp.text
        assert client.get(path).status_code == 404

    # Prefixed bypasses must stay 404.
    assert client.get("/@alice/settings/kb", headers=alice_hdr).status_code == 404
    assert client.get("/@alice/admin/kb", headers=admin_hdr).status_code == 404

    # /admin/kb is admin-only.
    assert client.get("/admin/kb", headers=alice_hdr).status_code == 404
    assert client.get("/admin/kb").status_code == 404
    adm = client.get("/admin/kb", headers=admin_hdr)
    assert adm.status_code == 200
    assert 'id="admin-kb-host"' in adm.text
    assert "console_admin.js" in adm.text


def test_public_kb_and_themes_routes_render_unified_appbar_and_shell(ui_env):
    client, _admin_hdr, alice_hdr = ui_env

    for path, view in (
        ("/kb", 'data-public-view="kb_list"'),
        ("/kb/123/", 'data-public-view="kb_detail"'),
        ("/explore/themes", 'data-public-view="themes_list"'),
        ("/explore/themes/456", 'data-public-view="theme_detail"'),
    ):
        resp = client.get(path, headers={"Accept": "text/html"})
        assert resp.status_code == 200, path
        assert view in resp.text
        assert 'id="public-bar"' in resp.text
        assert 'id="palettetrigger"' in resp.text
        assert 'id="hamburger-btn"' in resp.text
        assert "shared_ui.js" in resp.text and "public_kb.js" in resp.text
        assert "console_admin.js" not in resp.text

    # Trailing slash canonical redirect on /kb/{number}
    redir = client.get("/kb/123", headers={"Accept": "text/html"})
    assert redir.status_code == 308 and redir.headers["location"] == "/kb/123/"

    # Signed-in HTML request to /themes renders the new public themes view
    themes_html = client.get("/themes", headers={**alice_hdr, "Accept": "text/html"})
    assert themes_html.status_code == 200
    assert 'data-public-view="themes_list"' in themes_html.text

    # /settings/account delete activity toggle & /settings/mcp kb notice
    acc_html = client.get("/settings/account", headers=alice_hdr).text
    assert 'id="pref-delete-activity"' in acc_html
    assert 'id="pref-delete-preview-btn"' in acc_html

    mcp_html = client.get("/settings/mcp", headers=alice_hdr).text
    assert "400 kb_required" in mcp_html

    fed_html = client.get("/admin/federation", headers=_admin_hdr).text
    assert 'id="admin-federation-tabs"' in fed_html
    assert 'id="admin-fed-auto-contrib-toggle"' in fed_html
    assert 'id="admin-federation-signed-get"' in fed_html
    assert 'id="admin-federation-known-host"' in fed_html
    assert 'id="admin-federation-journal-host"' in fed_html

    prov_html = client.get("/admin/providers", headers=_admin_hdr).text
    assert 'id="admin-providers-embed-host"' in prov_html


def test_admin_accounts_html_renders_reputation_mod_count_and_sole_moderator_warning():
    html = admin_routes._render_admin_accounts_html(
        mode="network",
        items=[
            {
                "public_id": "10",
                "username": "opadmin",
                "role": "admin",
                "status": "active",
                "created_at": 1700000000.0,
                "activated_at": 1700000000.0,
                "theme_count": 1,
            },
            {
                "public_id": "20",
                "username": "mod_suspended",
                "role": "member",
                "status": "suspended",
                "created_at": 1700000000.0,
                "activated_at": 1700000000.0,
                "theme_count": 1,
                "has_provider_key": False,
                "reputation": "limited",
                "moderated_kb_count": 2,
                "sole_moderator": True,
            },
        ],
        retired=[],
        tier_dict={},
    )
    assert "limited" in html
    assert "Suspended sole moderator" in html
    assert ">2<" in html
