"""Tests for the network-mode root page ``/`` sharing ``/about`` UI and look-and-feel, KD15 isolation, and ETag/header contracts."""

from __future__ import annotations

import os
import re
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from claire.accounts import languages
from claire.api import server
from claire.core import ids
from claire.core.config import Settings
from claire.federation.knowledge import known_servers
from claire.store import accounts as accounts_store
from claire.store import auth_db, db as dbm, provider_keys, themes as themes_store
from claire.store.secrets import reset_secret_service

PUBLIC_URL = "http://127.0.0.1:8765"
SERVER_API_KEY = "AIzaTestServerSecretKey0000000000000000"
MEMBER_BYOK_KEY = "AIzaTestMemberByokKey0000000000000000"


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch):
    reset_secret_service()
    for key in list(os.environ):
        if key.startswith(("CLAIRE_", "GEMINI_")):
            monkeypatch.delenv(key, raising=False)
    yield
    reset_secret_service()


def _setup_home_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, server_langs: list[str] | None = None, with_content: bool = True, **setting_overrides: object) -> SimpleNamespace:
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    db_file = data_dir / "claire.db"
    auth_file = auth_db.auth_db_path(data_dir)
    monkeypatch.setenv("CLAIRE_DB_PATH", str(db_file))
    monkeypatch.setenv("CLAIRE_DATA_DIR", str(data_dir))

    conn = dbm.connect(db_file)
    try:
        dbm.init_db(conn)
        now = time.time()
        salt = dbm.read_id_salt(conn)
        store = accounts_store.AccountStore(conn)
        store.complete_setup(1, username="curator_ada", display_name="Curator Ada")
        store.set_setting("mode", "network", updated_by=1)

        if with_content:
            conn.execute("UPDATE accounts SET profile_public = 1 WHERE id = 1")
            alice_pub = ids.timestamp_id("accounts", salt=salt, unix_ms=int(now * 1000) + 1)
            conn.execute("INSERT INTO accounts (id, public_id, username, role, status, display_name, profile_public, created_at, activated_at, updated_at) VALUES (2, ?, 'public_alice', 'member', 'active', 'Public Alice', 1, ?, ?, ?)", (alice_pub, now, now, now))
        else:
            conn.execute("UPDATE accounts SET profile_public = 0 WHERE id = 1")

        bob_pub = ids.timestamp_id("accounts", salt=salt, unix_ms=int(now * 1000) + 2)
        conn.execute("INSERT INTO accounts (id, public_id, username, role, status, display_name, profile_public, created_at, activated_at, updated_at) VALUES (3, ?, 'secret_bob', 'member', 'active', 'Secret Bob', 0, ?, ?, ?)", (bob_pub, now, now, now))
        carol_pub = ids.timestamp_id("accounts", salt=salt, unix_ms=int(now * 1000) + 3)
        conn.execute("INSERT INTO accounts (id, public_id, username, role, status, display_name, profile_public, created_at, activated_at, updated_at) VALUES (4, ?, 'suspended_carol', 'member', 'suspended', 'Suspended Carol', 1, ?, ?, ?)", (carol_pub, now, now, now))

        if with_content:
            conn.execute("INSERT INTO knowledge_bases (id, seq, number, label, account_id, is_personal, is_public, knowledge_type, created_at, updated_at) VALUES (10, 10, 1001, 'Public Shared KB', 1, 0, 1, 'real', ?, ?)", (now, now))
            conn.execute("INSERT INTO themes (id, number, label, knowledge_type, state, top_entities, last_activity_at, created_at, updated_at) VALUES (1, 2001, 'Public Theme', 'real', 'active', '[\"Ada Lovelace\"]', ?, ?, ?)", (now, now, now))
            themes_store.add_theme_member(conn, theme_id=1, kb_id=10, source="computed", now=now)
            conn.execute("INSERT INTO documents (id, kb_id, url, title, raw_text, hidden, extraction_status) VALUES ('doc-pub-1', 10, 'https://example.com/p1', 'Public Extracted Doc', 'text', 0, 'extracted')")
            conn.execute("INSERT INTO documents (id, kb_id, url, title, raw_text, hidden, extraction_status) VALUES ('doc-pend-1', 10, 'https://example.com/p2', 'Pending Secret Doc', 'text', 0, 'pending')")
            conn.execute("INSERT INTO documents (id, kb_id, url, title, raw_text, hidden, extraction_status) VALUES ('doc-hid-1', 10, 'https://example.com/p3', 'Hidden Secret Doc', 'text', 1, 'extracted')")
            conn.execute("INSERT INTO entities (id, kb_id, type, name, norm_name, provisional, created_at, updated_at) VALUES ('ent-pub-1', 10, 'Concept', 'Public Concept', 'public concept', 0, ?, ?)", (now, now))
            conn.execute("INSERT INTO entities (id, kb_id, type, name, norm_name, provisional, created_at, updated_at) VALUES ('ent-prov-1', 10, 'Concept', 'Provisional Concept', 'provisional concept', 1, ?, ?)", (now, now))

        conn.execute("INSERT INTO knowledge_bases (id, seq, number, label, account_id, is_personal, is_public, created_at, updated_at) VALUES (11, 11, 1002, 'Top Secret KB', 3, 1, 0, ?, ?)", (now, now))
        conn.execute("INSERT INTO themes (id, number, label, knowledge_type, state, created_at, updated_at) VALUES (2, 2002, 'Empty Theme', 'real', 'empty', ?, ?)", (now, now))
        conn.execute("INSERT INTO documents (id, kb_id, url, title, raw_text, hidden, extraction_status) VALUES ('doc-priv-1', 11, 'https://example.com/p4', 'Private KB Doc', 'text', 0, 'extracted')")
        conn.execute("INSERT INTO entities (id, kb_id, type, name, norm_name, provisional, created_at, updated_at) VALUES ('ent-priv-1', 11, 'Concept', 'Secret Entity', 'secret entity', 0, ?, ?)", (now, now))

        known_servers.ensure_schema(conn)
        known_servers.note_server(conn, domain="secret-peer.example", source="admin", now=now, added_by=1)
        alice_token = accounts_store.create_account_session(conn, account_id=2, scope="owner") if with_content else None
        conn.commit()
        if server_langs is not None:
            languages.set_server_languages(conn, server_langs, updated_by=1)
    finally:
        conn.close()

    auth_conn = auth_db.connect_auth_db(auth_file)
    try:
        provider_keys.put_key(auth_conn, account_id=3, provider="gemini", api_key=MEMBER_BYOK_KEY, status="active", model_prefs={"model": "gemini-secret-member-model"})
    finally:
        auth_conn.close()

    base_settings: dict[str, object] = {
        "_env_file": None,
        "CLAIRE_DB_PATH": str(db_file),
        "CLAIRE_DATA_DIR": str(data_dir),
        "CLAIRE_PROVIDER": "gemini",
        "CLAIRE_GEMINI_API_KEY": SERVER_API_KEY,
        "CLAIRE_GEMINI_MODEL": "gemini-secret-server-model",
        "CLAIRE_ENVIRONMENT": "development",
        "CLAIRE_FQDN": PUBLIC_URL,
        "CLAIRE_ENABLE_JEV": True,
        "CLAIRE_JEV_MODEL": "jev-latest",
    }
    base_settings.update(setting_overrides)
    settings = Settings(**base_settings)
    app = server.create_app(settings, SimpleNamespace(provider=SimpleNamespace(name="mock")))
    return SimpleNamespace(settings=settings, db_file=db_file, alice_token=alice_token, client=TestClient(app, base_url=PUBLIC_URL))


def test_public_home_get_head_etag_and_shared_about_look_and_feel(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAIRE_BUILD_COMMIT", "7a3f9c2deadbeef")
    env = _setup_home_env(tmp_path, monkeypatch, server_langs=["ja", "ko"], github_repository="owner/repo", agent_marketplace_repo="owner/marketplace")

    resp = env.client.get("/", headers={"Accept-Language": "ko"})
    assert resp.status_code == 200
    assert "text/html" in resp.headers.get("content-type", "")
    assert "Accept-Language" in resp.headers.get("vary", "")
    etag = resp.headers.get("etag")
    assert etag is not None

    cond = env.client.get("/", headers={"Accept-Language": "ko", "If-None-Match": etag})
    assert cond.status_code == 304

    head = env.client.head("/", headers={"Accept-Language": "ko"})
    assert head.status_code == 200
    assert head.text == ""
    assert head.headers.get("etag") == etag

    html = resp.text
    assert 'id="home-top"' in html
    assert "about-card p4-hero-neon" in html
    assert 'class="p4-emblem"' in html
    assert "build 7a3f9c2" in html
    assert "7a3f9c2deadbeef" not in html
    assert html.count('<div class="about-kpi">') == 4
    assert "2명 · 1개 · 1개" in html
    assert "1편 · 1개" in html
    assert "네트워크 · 초대 전용" in html

    # Shared public appbar with / active and /about linked, /reference excluded from header.
    appbar_match = re.search(r'<header class="appbar".*?</header>', html, re.S)
    assert appbar_match is not None
    appbar_html = appbar_match.group(0)
    assert 'href="/" aria-current="page"' in appbar_html
    assert 'href="/about"' in appbar_html
    assert 'href="/reference"' not in appbar_html
    assert 'href="/login"' in appbar_html

    # Curator in hero meta strip only, not repeated in footer.
    meta_match = re.search(r'<div class="about-meta-strip">.*?</div>', html, re.S)
    footer_match = re.search(r'<footer class="about-footer">.*?</footer>', html, re.S)
    assert meta_match is not None and footer_match is not None
    assert "지식 관리자" in meta_match.group(0)
    assert "Curator Ada" in meta_match.group(0)
    assert "@curator_ada@127.0.0.1:8765" in meta_match.group(0)
    assert "지식 관리자" not in footer_match.group(0)
    assert "Curator Ada" not in footer_match.group(0)
    assert "@curator_ada@" not in footer_match.group(0)
    assert 'href="/reference"' in footer_match.group(0)
    assert 'href="https://github.com/owner/repo"' in footer_match.group(0)
    assert 'href="https://github.com/owner/marketplace"' in footer_match.group(0)

    # Dynamic primary language interpolation in ko and en.
    assert "일본어 초록·AsciiDoc 정리본" in html
    en_html = env.client.get("/", headers={"Accept-Language": "en"}).text
    assert "Japanese summaries, AsciiDoc renditions" in en_html

    # Zero raw i18n keys outside <script>.
    html_no_scripts = re.sub(r"<script\b[^>]*>.*?</script>", "", html, flags=re.S)
    assert re.search(r"\b(?:about|home_root|directory|themes|kb\.pub|lang\.name)\.[a-z0-9_]+", html_no_scripts) is None

    # Unset or unknown CLAIRE_BUILD_COMMIT omits Box 4 cleanly.
    monkeypatch.setenv("CLAIRE_BUILD_COMMIT", "unknown")
    env_unknown = _setup_home_env(tmp_path / "unknown", monkeypatch)
    assert env_unknown.client.get("/").text.count('<div class="about-kpi">') == 3
    monkeypatch.delenv("CLAIRE_BUILD_COMMIT", raising=False)
    env_unset = _setup_home_env(tmp_path / "unset", monkeypatch)
    assert env_unset.client.get("/").text.count('<div class="about-kpi">') == 3


def test_public_home_kd15_privacy_isolation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = _setup_home_env(tmp_path, monkeypatch, server_langs=["ko", "en"])
    html = env.client.get("/", headers={"Accept-Language": "ko"}).text

    for forbidden in (
        "Secret Bob",
        "secret_bob",
        "Suspended Carol",
        "suspended_carol",
        "Top Secret KB",
        "Pending Secret Doc",
        "Hidden Secret Doc",
        "Private KB Doc",
        "Provisional Concept",
        "Secret Entity",
        "secret-peer.example",
        SERVER_API_KEY,
        MEMBER_BYOK_KEY,
        "gemini-secret-server-model",
        "gemini-secret-member-model",
        "jev-latest",
        "gemini-decision-1",
        "/admin/",
    ):
        assert forbidden not in html, f"Forbidden secret/private content leaked on /: {forbidden}"


def test_public_home_authenticated_workspace_cta_and_empty_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = _setup_home_env(tmp_path / "auth", monkeypatch, with_content=True)
    env.client.cookies.set("claire_session", env.alice_token)
    auth_html = env.client.get("/", headers={"Accept-Language": "en"}).text
    assert 'href="/@public_alice/"' in auth_html
    assert "My workspace" in auth_html
    assert 'action="/logout"' in auth_html

    empty_env = _setup_home_env(tmp_path / "empty", monkeypatch, with_content=False)
    empty_html = empty_env.client.get("/", headers={"Accept-Language": "en"}).text
    assert "There are no public accounts" in empty_html
    assert 'class="home-empty-state"' in empty_html
    assert 'id="home-themes"' not in empty_html
    assert 'id="home-accounts"' in empty_html
