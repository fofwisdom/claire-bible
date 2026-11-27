"""Tests for the public ``/about`` route, route/username policies, KD15 isolation, and content rules."""

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
from claire.accounts.username_policy import RESERVED_USERNAMES, UsernameRejected, validate_username
from claire.api import security, server
from claire.core import ids
from claire.core.config import Settings
from claire.federation.knowledge import known_servers
from claire.store import accounts as accounts_store
from claire.store import auth_db, db as dbm, provider_keys
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


def _setup_about_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, mode: str = "network", server_langs: list[str] | None = None, **setting_overrides: Any) -> SimpleNamespace:
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
        conn.execute("UPDATE accounts SET profile_public = 1 WHERE id = 1")
        store.set_setting("mode", mode, updated_by=1)

        alice_pub = ids.timestamp_id("accounts", salt=salt, unix_ms=int(now * 1000) + 1)
        conn.execute("INSERT INTO accounts (id, public_id, username, role, status, display_name, profile_public, created_at, activated_at, updated_at) VALUES (2, ?, 'public_alice', 'member', 'active', 'Public Alice', 1, ?, ?, ?)", (alice_pub, now, now, now))
        bob_pub = ids.timestamp_id("accounts", salt=salt, unix_ms=int(now * 1000) + 2)
        conn.execute("INSERT INTO accounts (id, public_id, username, role, status, display_name, profile_public, created_at, activated_at, updated_at) VALUES (3, ?, 'secret_bob', 'member', 'active', 'Secret Bob', 0, ?, ?, ?)", (bob_pub, now, now, now))
        carol_pub = ids.timestamp_id("accounts", salt=salt, unix_ms=int(now * 1000) + 3)
        conn.execute("INSERT INTO accounts (id, public_id, username, role, status, display_name, profile_public, created_at, activated_at, updated_at) VALUES (4, ?, 'suspended_carol', 'member', 'suspended', 'Suspended Carol', 1, ?, ?, ?)", (carol_pub, now, now, now))

        conn.execute("INSERT INTO knowledge_bases (id, seq, number, label, account_id, is_personal, is_public, created_at, updated_at) VALUES (10, 10, 1001, 'Public Shared KB', 1, 0, 1, ?, ?)", (now, now))
        conn.execute("INSERT INTO knowledge_bases (id, seq, number, label, account_id, is_personal, is_public, created_at, updated_at) VALUES (11, 11, 1002, 'Top Secret KB', 3, 1, 0, ?, ?)", (now, now))
        conn.execute("INSERT INTO themes (id, number, label, knowledge_type, state, created_at, updated_at) VALUES (1, 2001, 'Public Theme', 'real', 'active', ?, ?)", (now, now))
        conn.execute("INSERT INTO themes (id, number, label, knowledge_type, state, created_at, updated_at) VALUES (2, 2002, 'Empty Theme', 'real', 'empty', ?, ?)", (now, now))

        conn.execute("INSERT INTO documents (id, kb_id, url, title, raw_text, hidden, extraction_status) VALUES ('doc-pub-1', 10, 'https://example.com/p1', 'Public Extracted Doc', 'text', 0, 'extracted')")
        conn.execute("INSERT INTO documents (id, kb_id, url, title, raw_text, hidden, extraction_status) VALUES ('doc-pend-1', 10, 'https://example.com/p2', 'Pending Secret Doc', 'text', 0, 'pending')")
        conn.execute("INSERT INTO documents (id, kb_id, url, title, raw_text, hidden, extraction_status) VALUES ('doc-hid-1', 10, 'https://example.com/p3', 'Hidden Secret Doc', 'text', 1, 'extracted')")
        conn.execute("INSERT INTO documents (id, kb_id, url, title, raw_text, hidden, extraction_status) VALUES ('doc-priv-1', 11, 'https://example.com/p4', 'Private KB Doc', 'text', 0, 'extracted')")

        conn.execute("INSERT INTO entities (id, kb_id, type, name, norm_name, provisional, created_at, updated_at) VALUES ('ent-pub-1', 10, 'Concept', 'Public Concept', 'public concept', 0, ?, ?)", (now, now))
        conn.execute("INSERT INTO entities (id, kb_id, type, name, norm_name, provisional, created_at, updated_at) VALUES ('ent-prov-1', 10, 'Concept', 'Provisional Concept', 'provisional concept', 1, ?, ?)", (now, now))
        conn.execute("INSERT INTO entities (id, kb_id, type, name, norm_name, provisional, created_at, updated_at) VALUES ('ent-priv-1', 11, 'Concept', 'Secret Entity', 'secret entity', 0, ?, ?)", (now, now))

        known_servers.ensure_schema(conn)
        known_servers.note_server(conn, domain="secret-peer.example", source="admin", now=now, added_by=1)
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
    return SimpleNamespace(settings=settings, db_file=db_file, client=TestClient(app, base_url=PUBLIC_URL))


def _extract_pipeline_json(html: str) -> dict:
    match = re.search(r'<script id="claire-pipeline-data" type="application/json">(.*?)</script>', html, re.S)
    assert match is not None, "Missing #claire-pipeline-data JSON script"
    return json.loads(match.group(1))


def test_about_username_reservation_and_public_route_policy() -> None:
    assert "about" in RESERVED_USERNAMES
    with pytest.raises(UsernameRejected) as exc:
        validate_username("about")
    assert exc.value.reason == "reserved"

    for method in ("GET", "HEAD"):
        rule = security.ROUTE_POLICY[(method, "/about")]
        assert rule == security._system("public", "none")
        assert rule.surface == "system"
        assert rule.access == "public"
        assert rule.capability == "none"


def test_public_about_anonymous_get_head_and_static_assets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = _setup_about_env(tmp_path, monkeypatch)

    resp = env.client.get("/about")
    assert resp.status_code == 200
    assert "text/html" in resp.headers.get("content-type", "")
    assert "Accept-Language" in resp.headers.get("vary", "")

    head = env.client.head("/about")
    assert head.status_code == 200
    assert head.text == ""

    css_resp = env.client.get("/static/css/pipeline_flow.css")
    assert css_resp.status_code == 200
    js_resp = env.client.get("/static/js/pipeline_flow.js")
    assert js_resp.status_code == 200


def test_public_about_kd15_privacy_isolation_and_public_only_counts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = _setup_about_env(tmp_path, monkeypatch, mode="network", server_langs=["ko", "en"])
    html = env.client.get("/about", headers={"Accept-Language": "ko"}).text

    # Strict KD15 & secret/admin isolation.
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
        "pipeline-recent",
        "pipeline-fallbacks",
    ):
        assert forbidden not in html, f"Forbidden secret/private content leaked on /about: {forbidden}"

    # Only public counts appear in KPI boxes: 2 public active profiles, 1 public shared KB, 1 active theme, 1 public extracted doc, 1 non-provisional entity.
    assert "2명 · 1개 · 1개" in html
    assert "1편 · 1개" in html
    assert "네트워크 · 초대 전용" in html

    data = _extract_pipeline_json(html)
    assert data["viewer"] == "public"
    assert data["showJev"] is False
    assert data["showSimulators"] is True
    assert data["models"]["jev"] == ""
    for val in data["models"].values():
        assert "gemini-secret-" not in str(val)
        assert "jev-latest" not in str(val)

    # Zero raw i18n keys in rendered markup outside JSON script tags.
    html_no_scripts = re.sub(r"<script\b[^>]*>.*?</script>", "", html, flags=re.S)
    assert re.search(r"\b(?:about|pipeline\.neon|lang\.name)\.[a-z0-9_]+", html_no_scripts) is None


def test_public_about_content_rules_build_commit_curator_language_sources_and_footer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAIRE_BUILD_COMMIT", "7a3f9c2deadbeef")
    env_with_commit = _setup_about_env(tmp_path / "with_sha", monkeypatch, server_langs=["ja", "ko"], github_repository="owner/repo", agent_marketplace_repo="owner/marketplace")

    ko_html = env_with_commit.client.get("/about", headers={"Accept-Language": "ko"}).text
    assert "build 7a3f9c2" in ko_html
    assert "7a3f9c2deadbeef" not in ko_html
    assert ko_html.count('<div class="about-kpi">') == 4

    # Curator in hero meta strip only, never repeated in footer.
    meta_match = re.search(r'<div class="about-meta-strip">.*?</div>', ko_html, re.S)
    footer_match = re.search(r'<footer class="about-footer">.*?</footer>', ko_html, re.S)
    assert meta_match is not None and footer_match is not None
    assert "지식 관리자" in meta_match.group(0)
    assert "Curator Ada" in meta_match.group(0)
    assert "@curator_ada@127.0.0.1:8765" in meta_match.group(0)
    assert "지식 관리자" not in footer_match.group(0)
    assert "Curator Ada" not in footer_match.group(0)
    assert "@curator_ada@" not in footer_match.group(0)

    # Dynamic primary language name interpolated in hero lead across ko and en.
    assert "일본어 초록·AsciiDoc 정리본" in ko_html
    assert _extract_pipeline_json(ko_html)["primaryLangName"] == "일본어"

    en_html = env_with_commit.client.get("/about", headers={"Accept-Language": "en"}).text
    assert "Japanese summaries, AsciiDoc renditions" in en_html
    assert _extract_pipeline_json(en_html)["primaryLangName"] == "Japanese"

    # Always-visible supported sources table; no collapsed <details> outside pipeline theatre.
    outside_pipeline = re.sub(r'<section class="about-card" id="pipeline-graph".*?</section>', "", ko_html, flags=re.S)
    assert "<details" not in outside_pipeline
    assert 'id="about-sources"' in ko_html
    assert "지원하는 자료와 출처" in ko_html

    # Footer links & forbidden legacy strings.
    footer_html = footer_match.group(0)
    assert 'href="/reference"' in footer_html
    assert 'href="https://github.com/owner/repo"' in footer_html
    assert 'href="https://github.com/owner/marketplace"' in footer_html
    for removed in ("MIT", "v0.1.0", "robots.txt", "/mcp"):
        assert removed not in ko_html

    # Public header links include /about and exclude /reference.
    appbar_match = re.search(r'<header class="appbar".*?</header>', ko_html, re.S)
    assert appbar_match is not None
    assert 'href="/about"' in appbar_match.group(0)
    assert 'href="/reference"' not in appbar_match.group(0)

    # Option A: unset or "unknown" build commit hides Box 4 cleanly and empty marketplace repo omits its link.
    monkeypatch.setenv("CLAIRE_BUILD_COMMIT", "unknown")
    env_unknown = _setup_about_env(tmp_path / "unknown_sha", monkeypatch, agent_marketplace_repo="")
    unknown_html = env_unknown.client.get("/about").text
    assert unknown_html.count('<div class="about-kpi">') == 3
    kpi_grid = re.search(r'<div class="about-kpi-grid">.*?</div>\s*(?:<div class="about-meta-strip">|</section>)', unknown_html, re.S)
    assert kpi_grid is not None
    assert "unknown" not in kpi_grid.group(0).lower()
    assert "build unknown" not in unknown_html.lower()
    assert 'href="https://github.com/fofwisdom/claire-bible-agent-marketplace"' not in unknown_html

    monkeypatch.delenv("CLAIRE_BUILD_COMMIT", raising=False)
    env_unset = _setup_about_env(tmp_path / "unset_sha", monkeypatch)
    unset_html = env_unset.client.get("/about").text
    assert unset_html.count('<div class="about-kpi">') == 3


def test_public_headers_include_about_and_exclude_reference(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = _setup_about_env(tmp_path, monkeypatch, mode="network")
    for path in ("/directory", "/themes", "/@secret_bob/"):
        resp = env.client.get(path, headers={"Accept": "text/html"})
        assert resp.status_code == 200, path
        assert 'href="/about"' in resp.text, path
        assert 'href="/reference"' not in resp.text, path
