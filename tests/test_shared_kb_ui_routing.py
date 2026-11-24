"""Interface contract tests for Shared KB frontend routes, console isolation, and account/admin summaries."""

from __future__ import annotations

import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from claire.core import ids
from claire.api import server
from claire.api.accounts_routes import admin as admin_routes
from claire.core.config import Settings
from claire.store import accounts as accounts_store
from claire.store import db as dbm
from claire.store import themes as themes_store
from claire.store.knowledge_base import KnowledgeBaseManager
from claire.store.secrets import reset_secret_service
from tests.auth_helpers import bearer_headers


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch):
    reset_secret_service()
    for key in list(os.environ):
        if key.startswith("CLAIRE_"):
            monkeypatch.delenv(key, raising=False)
    yield
    reset_secret_service()


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
    accounts_store.AccountStore(conn).set_setting("mode", "network", updated_by=1)
    conn.commit()
    conn.close()

    settings = Settings(
        db_path=str(db_file),
        fqdn="127.0.0.1:8765",
        environment="development",
    )
    manager = KnowledgeBaseManager(settings)
    manager.define_kb("Alice Personal", is_personal=True, is_public=False, account_id=2)
    shared = manager.define_kb("Team Korea", account_id=2, knowledge_type="real")
    conn = dbm.connect(db_file)
    theme = themes_store.create_theme(conn, label="Korean geography", knowledge_type="real")
    themes_store.update_theme(conn, int(theme["id"]), state="active")
    conn.execute("INSERT INTO documents (id, kb_id, title, url, fetched_at) VALUES (?, ?, ?, ?, ?)", ("doc_1", shared.id, "Shared Doc", "https://example.com/doc_1", now))
    conn.commit()
    conn.close()
    app = server.create_app(settings)
    admin_hdr = bearer_headers(settings, account_id=1, scope="owner")
    alice_hdr = bearer_headers(settings, account_id=2, scope="owner")
    client = TestClient(app, base_url=settings.public_url, follow_redirects=False)
    numbers = SimpleNamespace(kb=ids.format_public_id(shared.number), personal=ids.format_public_id(manager.personal_kb_for(2).number), theme=ids.format_public_id(int(theme["number"])))
    return client, admin_hdr, alice_hdr, numbers


def test_shared_kb_console_routes_and_admin_isolation(ui_env):
    client, admin_hdr, alice_hdr, numbers = ui_env

    # Settings KB and moderation KB routes work for active owners and isolate admin bundles, scripts, catalog keys, and links.
    for path, marker, area, section in (
        ("/settings/kb", 'id="settings-kb-host"', "settings", "kb"),
        ("/moderation/kb", 'id="settings-kb-manage-host"', "moderation", "kb_manage"),
        (f"/moderation/kb/{numbers.kb}", 'id="settings-kb-manage-host"', "moderation", "kb_manage"),
    ):
        resp = client.get(path, headers=alice_hdr)
        assert resp.status_code == 200, path
        assert marker in resp.text
        assert f'data-active-area="{area}"' in resp.text
        assert f'data-active-section="{section}"' in resp.text
        assert "console_kb.js" in resp.text
        assert "console_admin.js" not in resp.text
        assert "/admin/" not in resp.text
        assert "console.js.admin." not in resp.text
        assert 'href="/moderation/oversight"' not in resp.text
        assert 'href="/moderation/themes"' not in resp.text
        assert client.get(path).status_code == 404

    # /moderation redirects owners to /moderation/kb (preserving valid personal ?kb=), and maps shared ?kb= to /moderation/kb/{number}.
    mod_index = client.get("/moderation", headers=alice_hdr)
    assert (mod_index.status_code, mod_index.headers["location"]) == (302, "/moderation/kb")
    mod_index_kb = client.get(f"/moderation?kb={numbers.personal}", headers=alice_hdr)
    assert (mod_index_kb.status_code, mod_index_kb.headers["location"]) == (302, f"/moderation/kb?kb={numbers.personal}")
    mod_index_shared = client.get(f"/moderation?kb={numbers.kb}", headers=alice_hdr)
    assert (mod_index_shared.status_code, mod_index_shared.headers["location"]) == (302, f"/moderation/kb/{numbers.kb}")
    mod_kb_shared = client.get(f"/moderation/kb?kb={numbers.kb}", headers=alice_hdr)
    assert mod_kb_shared.status_code == 200
    assert f'"kb_number": "{numbers.kb}"' in mod_kb_shared.text or f'"kb_number":"{numbers.kb}"' in mod_kb_shared.text.replace(" ", "")
    assert client.get("/moderation?kb=nonexistent", headers=alice_hdr).status_code == 404
    assert client.get("/moderation").status_code == 404

    # The page of a reviewer is for a shared knowledge base only: an unknown number and the number of a personal knowledge base get 404.
    assert client.get("/moderation/kb/42", headers=alice_hdr).status_code == 404
    assert client.get(f"/moderation/kb/{numbers.personal}", headers=alice_hdr).status_code == 404
    assert client.get("/settings/kb/42", headers=alice_hdr).status_code == 404
    assert client.get(f"/settings/kb/{numbers.personal}", headers=alice_hdr).status_code == 404

    # Prefixed bypasses must stay 404.
    for path in (
        "/@alice/settings/kb",
        "/@alice/admin/kb",
        "/@alice/moderation",
        "/@alice/moderation/kb",
        "/@alice/moderation/oversight",
        "/@alice/moderation/themes",
        f"/kb/{numbers.kb}/moderation/oversight",
    ):
        assert client.get(path, headers=admin_hdr).status_code == 404, path
        assert client.get(path, headers=alice_hdr).status_code == 404, path

    # /moderation/oversight and /moderation/themes are admin-only.
    for path, host_marker, section in (
        ("/moderation/oversight", 'id="admin-kb-host"', "kb"),
        ("/moderation/themes", 'id="admin-themes-host"', "themes"),
    ):
        assert client.get(path, headers=alice_hdr).status_code == 404, path
        assert client.get(path).status_code == 404, path
        adm = client.get(path, headers=admin_hdr)
        assert adm.status_code == 200, path
        assert 'data-active-area="moderation"' in adm.text, path
        assert f'data-active-section="{section}"' in adm.text, path
        assert host_marker in adm.text, path
        assert "console_admin.js" in adm.text, path

    # Legacy 308 compatibility redirects preserve owner/admin gates before redirect.
    for method in ("GET", "HEAD"):
        legacy_manage = client.request(method, "/settings/kb/manage", headers=alice_hdr)
        assert (legacy_manage.status_code, legacy_manage.headers["location"]) == (308, "/moderation/kb")
        legacy_kb = client.request(method, f"/settings/kb/{numbers.kb}", headers=alice_hdr)
        assert (legacy_kb.status_code, legacy_kb.headers["location"]) == (308, f"/moderation/kb/{numbers.kb}")
        assert client.request(method, "/settings/kb/manage").status_code == 404
        assert client.request(method, "/admin/kb", headers=alice_hdr).status_code == 404
        assert client.request(method, "/admin/kb").status_code == 404
        assert client.request(method, "/admin/themes", headers=alice_hdr).status_code == 404
        assert client.request(method, "/admin/themes").status_code == 404
        adm_kb = client.request(method, "/admin/kb", headers=admin_hdr)
        assert (adm_kb.status_code, adm_kb.headers["location"]) == (308, "/moderation/oversight")
        adm_themes = client.request(method, "/admin/themes", headers=admin_hdr)
        assert (adm_themes.status_code, adm_themes.headers["location"]) == (308, "/moderation/themes")
        set_themes_adm = client.request(method, "/settings/themes", headers=admin_hdr)
        assert (set_themes_adm.status_code, set_themes_adm.headers["location"]) == (308, "/moderation/themes")
        set_themes_mem = client.request(method, "/settings/themes", headers=alice_hdr)
        assert (set_themes_mem.status_code, set_themes_mem.headers["location"]) == (308, "/settings/kb")


def test_public_kb_and_themes_paths_give_html_to_a_browser_and_json_to_an_api_client(ui_env):
    """One path has two forms (namespace design Section 6.5): the HTML page of the public views for a navigation, the JSON of the backend for an API client."""
    client, _admin_hdr, alice_hdr, numbers = ui_env
    html_hdr, json_hdr = {"Accept": "text/html"}, {"Accept": "application/json"}

    for path, view in (
        ("/kb", 'data-public-view="kb_list"'),
        (f"/kb/{numbers.kb}/", 'data-public-view="kb_detail"'),
        ("/themes", 'data-public-view="themes_list"'),
        (f"/themes/{numbers.theme}/", 'data-public-view="theme_detail"'),
    ):
        resp = client.get(path, headers=html_hdr)
        assert resp.status_code == 200, path
        assert resp.headers["content-type"].startswith("text/html") and "Accept" in resp.headers["vary"]
        assert view in resp.text
        assert 'id="public-bar"' in resp.text
        assert 'id="palettetrigger"' in resp.text
        assert 'id="hamburger-btn"' in resp.text
        assert "shared_ui.js" in resp.text and "public_kb.js" in resp.text
        assert "console_admin.js" not in resp.text

    # The JSON form of the same paths.
    kb_list = client.get("/kb", headers=json_hdr).json()
    assert [kb["number"] for kb in kb_list["knowledge_bases"]] == [numbers.kb]
    kb_item = client.get(f"/kb/{numbers.kb}/", headers={**alice_hdr, **json_hdr}).json()["knowledge_base"]
    assert (kb_item["number"], kb_item["label"], kb_item["role"]) == (numbers.kb, "Team Korea", "moderator")
    assert kb_item == next(kb for kb in client.get("/kb", headers={**alice_hdr, **json_hdr}).json()["knowledge_bases"] if kb["number"] == numbers.kb)
    assert [t["number"] for t in client.get("/themes", headers=json_hdr).json()["themes"]] == [numbers.theme]
    assert client.get(f"/themes/{numbers.theme}/", headers=json_hdr).json()["label"] == "Korean geography"

    # The page shows the number and the label of the knowledge base; the script gets the rest from the JSON form.
    detail = client.get(f"/kb/{numbers.kb}/", headers=html_hdr).text
    assert "<title>Team Korea" in detail and f'"ref": "{numbers.kb}"' in detail
    # A link into the content of the shared knowledge base redirects to its permalink workspace, and a missing document returns 404.
    link_res = client.get(f"/kb/{numbers.kb}/?doc=doc_1", headers=html_hdr)
    assert link_res.status_code == 308 and link_res.headers["location"].startswith("/silvercord/")
    workspace = client.get(link_res.headers["location"], headers=html_hdr)
    assert workspace.status_code == 200 and "data-public-view" not in workspace.text
    assert client.get(f"/kb/{numbers.kb}/?doc=doc_missing", headers=html_hdr).status_code == 404

    # The path without the last slash, an unknown number, and the paths that are not mounted any more.
    redir = client.get(f"/kb/{numbers.kb}", headers=html_hdr)
    assert redir.status_code == 308 and redir.headers["location"] == f"/kb/{numbers.kb}/"
    for path in ("/kb/123/", "/themes/456/", "/explore/themes", f"/explore/themes/{numbers.theme}"):
        assert client.get(path, headers=html_hdr).status_code == 404, path

    # A signed-in navigation gets the same page.
    themes_html = client.get("/themes", headers={**alice_hdr, **html_hdr})
    assert themes_html.status_code == 200
    assert 'data-public-view="themes_list"' in themes_html.text

    # /account/profile has no control for the deletion of the account: an account cannot ask for its own deletion, thus the option "Delete my activity records" is only a field of the purge action of the admin. /account/mcp has the kb notice.
    acc_html = client.get("/account/profile", headers=alice_hdr).text
    assert 'id="pref-delete-activity"' not in acc_html
    assert 'id="pref-delete-preview-btn"' not in acc_html

    mcp_html = client.get("/account/mcp", headers=alice_hdr).text
    assert "kb_required" in mcp_html

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
