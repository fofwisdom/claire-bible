"""ACCOUNT_PREFERRED_LANGUAGES_DESIGN.md: the interfaces between the language store (``accounts.languages``, claire.db), the job context (``provider_policy.resolve_provider_context`` and ``settings_for_context``), the account view, and the route ``PUT /api/admin/server/languages``."""

from __future__ import annotations

import os
import re
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from claire.accounts import languages
from claire.accounts import provider_policy as pp
from claire.accounts.provider_view import describe_account_providers
from claire.api import server
from claire.core.config import Settings
from claire.store import accounts as accounts_store
from claire.store import auth_db
from claire.store import db as dbm
from claire.store.secrets import reset_secret_service
from tests.auth_helpers import bearer_headers

PUBLIC_URL = "http://127.0.0.1:8765"


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch):
    reset_secret_service()
    for key in list(os.environ):
        if key.startswith(("CLAIRE_", "GEMINI_")):
            monkeypatch.delenv(key, raising=False)
    yield
    reset_secret_service()


def _world(tmp_path: Path) -> SimpleNamespace:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    db_file = data_dir / "claire.db"
    conn = dbm.connect(db_file)
    try:
        dbm.init_db(conn)
        store = accounts_store.AccountStore(conn)
        store.complete_setup(1, username="admin_lang", display_name="Admin")
        now = time.time()
        conn.execute(
            "INSERT INTO accounts (id, public_id, username, role, status, display_name, profile_public, created_at, activated_at, updated_at) VALUES (2, 223456789055, 'member_lang', 'member', 'active', 'Member', 0, ?, ?, ?)",
            (now, now, now),
        )
        store.set_setting("mode", "network", updated_by=1)
        conn.commit()
    finally:
        conn.close()
    auth_db.connect_auth_db(auth_db.auth_db_path(data_dir)).close()
    settings = Settings(_env_file=None, CLAIRE_DB_PATH=str(db_file), CLAIRE_PROVIDER="mock", CLAIRE_ENVIRONMENT="development", CLAIRE_FQDN=PUBLIC_URL)
    return SimpleNamespace(settings=settings, db_file=db_file, data_dir=data_dir)


def _conn(world: SimpleNamespace):
    return dbm.connect_existing(world.db_file)


def test_jobs_of_an_account_get_its_list_and_the_others_the_server_default_language(tmp_path: Path) -> None:
    world = _world(tmp_path)
    conn = _conn(world)
    try:
        languages.set_server_languages(conn, ["ko", "ja"], updated_by=1)
        accounts_store.AccountStore(conn).update_preferred_languages(2, ["zh-hant", "en", "ko"])
    finally:
        conn.close()
    # Settings without an account (host operator, global settings) read the server default language from claire.db.
    base = Settings(_env_file=None, CLAIRE_DB_PATH=str(world.db_file), CLAIRE_PROVIDER="mock", CLAIRE_ENVIRONMENT="development")
    assert base.effective_preferred_languages == ["ko", "ja", "en"]
    # The member job: its own list, in its order (en is not moved).
    member = pp.resolve_provider_context(2, settings=base)
    assert member.languages == ("zh-hant", "en", "ko")
    job = pp.settings_for_context(base, member)
    assert job.effective_preferred_languages == ["zh-hant", "en", "ko"] and job.effective_vlm_language == "zh-hant"
    # The administrator account follows the server default language.
    admin = pp.resolve_provider_context(1, settings=base)
    assert admin.languages == ("ko", "ja", "en")
    assert pp.settings_for_context(base, admin).effective_preferred_languages == ["ko", "ja", "en"]
    # The account view (/account/providers) shows the order of the account.
    view = describe_account_providers(base, account_id=2)
    assert view["stt"]["order"]["caption_languages"] == ["zh-hant", "en", "ko"] and view["stt"]["order"]["stt_hints"] == ["zh-hant", "en", "ko"]


def test_server_default_language_route_is_for_the_administrator_and_records_an_audit(tmp_path: Path) -> None:
    world = _world(tmp_path)
    admin = {**bearer_headers(world.db_file, account_id=1, scope="owner"), "Origin": PUBLIC_URL}
    member = {**bearer_headers(world.db_file, account_id=2, scope="owner"), "Origin": PUBLIC_URL}
    app = server.create_app(world.settings, SimpleNamespace(provider=SimpleNamespace(name="mock")))
    with TestClient(app, base_url=PUBLIC_URL) as client:
        assert client.put("/api/admin/server/languages", headers=member, json={"languages": ["ko"]}).status_code == 404
        for bad in ({"languages": ["ko!"]}, {"languages": "ko"}, {"languages": ["ko"], "x": 1}, {}):
            response = client.put("/api/admin/server/languages", headers=admin, json=bad)
            assert response.status_code == 400, bad
        saved = client.put("/api/admin/server/languages", headers=admin, json={"languages": ["JA", "ko"]})
        assert saved.status_code == 200 and saved.json() == {"languages": ["ja", "ko", "en"]}
        page = client.get("/admin/server", headers=admin).text
        assert 'id="server-languages"' in page and 'data-server-languages-state="selected"' in page
        assert re.findall(r'<li data-lang="([a-z-]+)">', page.split('id="server-languages"', 1)[1].split("</form>", 1)[0]) == ["ja", "ko", "en"]
        assert "/static/js/console_languages.js" in page
        # The member follows it: the profile card shows the state and the list; the interface is the first supported item (ko).
        profile = client.get("/account/profile", headers=member)
        assert 'data-languages-state="server"' in profile.text and profile.headers["content-language"] == "ko"
        # null: not selected.
        cleared = client.put("/api/admin/server/languages", headers=admin, json={"languages": None})
        assert cleared.status_code == 200 and cleared.json() == {"languages": []}
        assert 'data-server-languages-state="none"' in client.get("/admin/server", headers=admin).text
    auth = auth_db.connect_auth_db(auth_db.auth_db_path(world.data_dir))
    try:
        actions = [row[0] for row in auth.execute("SELECT action FROM admin_audit ORDER BY id").fetchall()]
    finally:
        auth.close()
    assert actions.count("server.languages.update") == 2


def test_a_change_of_the_server_default_language_applies_to_settings_made_before_it(tmp_path: Path) -> None:
    """The app makes its settings at the start; a later change on /admin/server must reach the jobs without an account and the views (regression of 2026-11-24)."""
    from claire.accounts.languages import with_server_languages

    world = _world(tmp_path)
    started = Settings(_env_file=None, CLAIRE_DB_PATH=str(world.db_file), CLAIRE_PROVIDER="mock", CLAIRE_ENVIRONMENT="development")
    assert started.effective_preferred_languages == ["en"]
    conn = _conn(world)
    try:
        languages.set_server_languages(conn, ["ja"], updated_by=1)
    finally:
        conn.close()
    host_job = pp.settings_for_context(started, pp.admin_context(started))
    assert host_job.effective_preferred_languages == ["ja", "en"]
    assert with_server_languages(started).effective_preferred_languages == ["ja", "en"]
    # Removed again: the old list of the settings does not stay in the jobs.
    conn = _conn(world)
    try:
        languages.set_server_languages(conn, None, updated_by=1)
    finally:
        conn.close()
    assert pp.settings_for_context(host_job, pp.admin_context(host_job)).effective_preferred_languages == ["en"]
