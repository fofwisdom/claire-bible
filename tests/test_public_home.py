"""Tests for ``public_observatory.build_federation_observatory_snapshot`` and network-mode ``/`` Calm Federated Observatory routes."""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from starlette.testclient import TestClient

from claire.accounts import languages
from claire.api import server
from claire.api.routes import public_observatory
from claire.core import ids
from claire.core.config import Settings
from claire.federation.knowledge import known_servers
from claire.store import accounts as accounts_store, auth_db, db as dbm, federation as federation_store, kb_collab, provider_keys, themes as themes_store
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


def _setup_home_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, server_langs: list[str] | None = None, with_content: bool = True, with_peers: bool | None = None, **setting_overrides: Any) -> SimpleNamespace:
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    db_file = data_dir / "claire.db"
    auth_file = auth_db.auth_db_path(data_dir)
    monkeypatch.setenv("CLAIRE_DB_PATH", str(db_file))
    monkeypatch.setenv("CLAIRE_DATA_DIR", str(data_dir))
    seed_peers = with_content if with_peers is None else with_peers

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
        if seed_peers:
            known_servers.note_server(conn, domain="peer-a.example", source="admin", now=now, added_by=1)
            known_servers.record_read(conn, "peer-a.example", server_actor_iri="https://peer-a.example/ap/server", software_name="claire-bible", software_version="9.9.9-secret-ver", shared_kb_count=2, now=now)
            cur = conn.execute("INSERT INTO kf_remote_knowledge_bases (iri, domain, url, label, description, knowledge_type, document_count, entity_count, relation_count, signature, payload, first_seen_at, seen_at, changed_at) VALUES ('https://peer-a.example/ap/kb/501', 'peer-a.example', 'https://peer-a.example/kb/501/', 'Peer A Atlas', 'Remote atlas', 'real', 12, 40, 15, NULL, '{}', ?, ?, ?)", (now, now, now))
            remote_kb_id = int(cur.lastrowid)
            if with_content:
                themes_store.set_computed_remote_member(conn, theme_id=1, remote_kb_id=remote_kb_id, score=0.92)
                kb_collab.add_contribution(conn, kb_id=10, document_id="doc-pub-1", origin="curation", contributor_account_id=None, curator_account_id=1, state="visible", approved_at=now, approved_by_account_id=1, remote={"source_kb_iri": "https://peer-a.example/ap/kb/501", "source_iri": "https://peer-a.example/doc/1", "root_iri": "https://peer-a.example/doc/1", "credit_iri": None}, now=now)

            known_servers.note_server(conn, domain="blocked-peer.example", source="admin", now=now, added_by=1)
            known_servers.record_read(conn, "blocked-peer.example", server_actor_iri="https://blocked-peer.example/ap/server", software_name="claire-bible", software_version="8.8.8-blocked-ver", shared_kb_count=4, now=now)
            federation_store.add_domain_block(conn, "blocked-peer.example", created_by=1, note="blocked", now=now)

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

    base_settings: dict[str, Any] = {
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


def test_build_federation_observatory_snapshot_interface_and_db_boundary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = _setup_home_env(tmp_path, monkeypatch, with_content=True, with_peers=True)
    theme_views = [
        {
            "number": "2001",
            "label": "Public Theme",
            "knowledge_type": "real",
            "shared_knowledge_bases": 1,
            "remote_knowledge_bases": 1,
            "visible_contributions": 2,
            "top_entities": ["Ada Lovelace"],
            "knowledge_bases": [
                {"remote": False, "server": None, "label": "Public Shared KB"},
                {"remote": True, "server": "peer-a.example", "label": "Peer A Atlas"},
                {"remote": True, "server": "blocked-peer.example", "label": "Blocked KB"},
            ],
        }
    ]
    recent = [
        {"kind": "curation", "at": 1700000300.0, "server": "peer-a.example", "label": "Peer A Atlas"},
        {"kind": "accepted_contribution", "at": 1700000200.0, "server": "peer-a.example", "label": "Peer A Atlas"},
        {"kind": "curation", "at": 1700000100.0, "server": "blocked-peer.example", "label": "Blocked KB"},
    ]

    snap = public_observatory.build_federation_observatory_snapshot(env.db_file, settings=env.settings, origin=PUBLIC_URL, theme_views=theme_views, recent=recent)

    assert snap["instance_domain"] == "127.0.0.1:8765"
    assert snap["mode"] == "network"
    assert snap["public_profiles"] == 2
    assert snap["local_shared_kbs"] == 1
    assert snap["local_docs"] == 1
    assert len(snap["peers"]) == 1

    peer = snap["peers"][0]
    assert peer["domain"] == "peer-a.example"
    assert peer["shared_kb_count"] == 2
    assert peer["curations_in"] == 1
    assert peer["contributions_out"] == 1
    assert peer["bridged_themes"] == [{"number": "2001", "label": "Public Theme"}]
    assert peer["remote_kbs"] == [{"label": "Peer A Atlas", "url": "https://peer-a.example/kb/501/"}]
    assert peer["primary_url"] == "/themes/2001/"

    assert snap["cycle"] == {
        "peer_count": 1,
        "active_themes": 1,
        "remote_kb_total": 1,
        "curations_count": 1,
        "contributions_count": 1,
        "recent_exchange_count": 3,
        "cold_start": False,
    }
    assert snap["bridge_themes"][0]["peer_domains"] == ["peer-a.example"]

    serialized = json.dumps(snap, ensure_ascii=False)
    for forbidden in (
        "blocked-peer.example",
        "9.9.9-secret-ver",
        "8.8.8-blocked-ver",
        "claire-bible",
        "https://peer-a.example/ap/server",
        "Secret Bob",
        "secret_bob",
        "Top Secret KB",
        "Pending Secret Doc",
        "Hidden Secret Doc",
        "Private KB Doc",
        SERVER_API_KEY,
        MEMBER_BYOK_KEY,
        "jev-latest",
    ):
        assert forbidden not in serialized


def test_build_federation_observatory_snapshot_cold_start_and_missing_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = _setup_home_env(tmp_path / "cold", monkeypatch, with_content=False, with_peers=False)
    snap = public_observatory.build_federation_observatory_snapshot(env.db_file, settings=env.settings, origin=PUBLIC_URL, theme_views=[], recent=[])
    assert snap["peers"] == []
    assert snap["bridge_themes"] == []
    assert snap["public_profiles"] == 0
    assert snap["local_shared_kbs"] == 0
    assert snap["local_docs"] == 0
    assert snap["cycle"]["peer_count"] == 0
    assert snap["cycle"]["cold_start"] is True

    missing_snap = public_observatory.build_federation_observatory_snapshot(tmp_path / "missing.db", settings=env.settings, origin=PUBLIC_URL, theme_views=[], recent=[])
    assert missing_snap["peers"] == []
    assert missing_snap["cycle"]["cold_start"] is True


def test_public_home_get_head_etag_and_calm_observatory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = _setup_home_env(tmp_path, monkeypatch, server_langs=["ja", "ko"], github_repository="owner/repo", agent_marketplace_repo="owner/marketplace")

    resp = env.client.get("/", headers={"Accept-Language": "ko"})
    assert resp.status_code == 200
    assert "text/html" in resp.headers.get("content-type", "")
    assert "Accept-Language" in resp.headers.get("vary", "")
    assert "referrer-policy" in {k.lower() for k in resp.headers}
    etag = resp.headers.get("etag")
    assert etag is not None

    cond = env.client.get("/", headers={"Accept-Language": "ko", "If-None-Match": etag})
    assert cond.status_code == 304

    head = env.client.head("/", headers={"Accept-Language": "ko"})
    assert head.status_code == 200
    assert head.text == ""
    assert head.headers.get("etag") == etag

    html = resp.text
    assert 'id="home-observatory"' in html
    assert 'class="obs-flow-grid"' in html
    assert 'class="obs-reflux"' in html
    assert 'popover="hint"' in html
    assert 'interestfor="obs-hc-peer-0"' in html
    assert 'id="obs-hc-peer-0"' in html
    assert 'id="home-themes"' in html
    assert 'id="public-home-recent"' in html
    assert 'id="home-directory-link"' in html
    assert "peer-a.example" in html
    assert "Peer A Atlas" in html
    assert "blocked-peer.example" not in html
    assert "p4-theatre" not in html
    assert "p4-hero-neon" not in html
    assert 'id="home-top"' not in html
    assert "claire-pubnav" not in html

    # Shared 4-item public appbar with / active and /themes, /directory, /about linked; /kb and /reference excluded.
    appbar_match = re.search(r'<header class="appbar".*?</header>', html, re.S)
    assert appbar_match is not None
    appbar_html = appbar_match.group(0)
    assert 'href="/" aria-current="page"' in appbar_html
    assert 'href="/themes"' in appbar_html
    assert 'href="/directory"' in appbar_html
    assert 'href="/about"' in appbar_html
    assert 'href="/login"' in appbar_html
    assert 'href="/kb"' not in appbar_html
    assert 'href="/reference"' not in appbar_html

    footer_match = re.search(r'<footer class="about-footer">.*?</footer>', html, re.S)
    assert footer_match is not None
    assert 'href="/reference"' in footer_match.group(0)
    assert 'href="https://github.com/owner/repo"' in footer_match.group(0)
    assert 'href="https://github.com/owner/marketplace"' in footer_match.group(0)

    # Zero raw i18n keys in ko and en outside <script>.
    en_resp = env.client.get("/", headers={"Accept-Language": "en"})
    assert en_resp.status_code == 200
    assert "Federated Knowledge Observatory" in en_resp.text
    for body_html in (html, en_resp.text):
        html_no_scripts = re.sub(r"<script\b[^>]*>.*?</script>", "", body_html, flags=re.S)
        assert re.search(r"\b(?:about|home_obs|home_root|directory|themes|kb\.pub|lang\.name)\.[a-z0-9_]+", html_no_scripts) is None


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
        "blocked-peer.example",
        "9.9.9-secret-ver",
        "8.8.8-blocked-ver",
        "https://peer-a.example/ap/server",
        SERVER_API_KEY,
        MEMBER_BYOK_KEY,
        "gemini-secret-server-model",
        "gemini-secret-member-model",
        "jev-latest",
        "gemini-decision-1",
        "/admin/",
    ):
        assert forbidden not in html, f"Forbidden secret/private content leaked on /: {forbidden}"


def test_public_home_authenticated_workspace_cta_and_cold_start_empty_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = _setup_home_env(tmp_path / "auth", monkeypatch, with_content=True)
    env.client.cookies.set("claire_session", env.alice_token)
    auth_html = env.client.get("/", headers={"Accept-Language": "en"}).text
    assert 'href="/@public_alice/"' in auth_html
    assert "My workspace" in auth_html
    assert 'action="/logout"' in auth_html
    assert 'href="/login"' not in auth_html

    empty_env = _setup_home_env(tmp_path / "empty", monkeypatch, with_content=False, with_peers=False)
    empty_html = empty_env.client.get("/", headers={"Accept-Language": "en"}).text
    assert 'id="home-observatory"' in empty_html
    assert 'data-obs-standby="true"' in empty_html
    assert 'data-state="standby"' in empty_html
    assert "There are no public accounts" in empty_html
    assert "Public directory" in empty_html
    assert 'id="home-themes"' not in empty_html
    assert 'id="home-directory-link"' in empty_html


def test_public_themes_observatory_alignment_and_privacy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = _setup_home_env(tmp_path / "themes", monkeypatch, server_langs=["ko", "en"])

    json_resp = env.client.get("/themes", headers={"Accept": "application/json"})
    assert json_resp.status_code == 200
    payload = json_resp.json()
    active_themes = [th for th in payload["themes"] if th["state"] == "active"]
    assert len(active_themes) == 1
    theme_item = active_themes[0]
    assert theme_item["number"] == "2001"
    assert theme_item["label"] == "Public Theme"
    assert theme_item["knowledge_bases"] == 1
    assert theme_item["remote_knowledge_bases"] == 1
    assert theme_item["top_entities"] == ["Ada Lovelace"]
    assert theme_item["peer_servers"] == ["peer-a.example"]
    assert isinstance(theme_item["last_activity_at"], (int, float))

    serialized = json.dumps(payload, ensure_ascii=False)
    for forbidden in ("blocked-peer.example", "9.9.9-secret-ver", "8.8.8-blocked-ver", "Secret Bob", "Top Secret KB", SERVER_API_KEY, MEMBER_BYOK_KEY, "jev-latest"):
        assert forbidden not in serialized

    for lang_code in ("ko", "en"):
        html_resp = env.client.get("/themes", headers={"Accept": "text/html", "Accept-Language": lang_code})
        assert html_resp.status_code == 200
        html = html_resp.text
        assert 'class="obs-main obs-themes-main"' in html
        assert '<meta name="text-scale" content="scale"/>' in html
        assert '/static/css/public_observatory.css' in html
        assert '/static/js/command_palette.js' in html
        assert '<footer class="about-footer">' in html
        assert 'href="/themes" aria-current="page"' in html
        assert 'href="/kb"' not in html
        assert "obs-view-switch" not in html
        assert 'href="/reference"' not in html
        html_no_scripts = re.sub(r"<script\b[^>]*>.*?</script>", "", html, flags=re.S)
        assert re.search(r"\b(?:about|home_obs|home_root|directory|themes|kb\.pub|lang\.name)\.[a-z0-9_]+", html_no_scripts) is None

    kb_html_redir = env.client.get("/kb", headers={"Accept": "text/html"}, follow_redirects=False)
    assert (kb_html_redir.status_code, kb_html_redir.headers.get("location")) == (308, "/themes")
    kb_html_redir_q = env.client.get("/kb?knowledge_type=fictional", headers={"Accept": "text/html"}, follow_redirects=False)
    assert (kb_html_redir_q.status_code, kb_html_redir_q.headers.get("location")) == (308, "/themes?knowledge_type=fictional")
