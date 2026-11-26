"""The page ``/support/pipeline`` (``api.accounts_routes.pipeline``): Proposal 4 Gemini Neon shared component, ``build_pipeline_view_data`` role isolation, media rules, and administrator records."""

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
from claire.accounts.provider_view import describe_account_providers, describe_server_providers, media_language_order
from claire.api import server
from claire.api.accounts_routes.pipeline import build_pipeline_view_data, render_pipeline_mount_html
from claire.core.config import Settings
from claire.store import accounts as accounts_store
from claire.store import auth_db, db as dbm, provider_keys
from claire.store.secrets import reset_secret_service
from tests.auth_helpers import bearer_headers

PUBLIC_URL = "http://127.0.0.1:8765"
ADMIN_DOC_TITLE = "Lineage record of another account"
MEMBER_BYOK_KEY = "AIzaTestMemberPipelineKey00000000000000"


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch):
    reset_secret_service()
    for key in list(os.environ):
        if key.startswith(("CLAIRE_", "GEMINI_")):
            monkeypatch.delenv(key, raising=False)
    yield
    reset_secret_service()


def _env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **setting_overrides: object) -> SimpleNamespace:
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    db_file = data_dir / "claire.db"
    conn = dbm.connect(db_file)
    try:
        dbm.init_db(conn)
        store = accounts_store.AccountStore(conn)
        store.complete_setup(1, username="admin_pipe", display_name="Admin")
        now = time.time()
        conn.execute("INSERT INTO accounts (id, public_id, username, role, status, display_name, profile_public, created_at, activated_at, updated_at) VALUES (2, 223456789077, 'member_pipe', 'member', 'active', 'Member', 0, ?, ?, ?)", (now, now, now))
        store.set_setting("mode", "network", updated_by=1)
        conn.commit()
        languages.set_server_languages(conn, ["ko", "ja"], updated_by=1)
    finally:
        conn.close()
    monkeypatch.setenv("CLAIRE_DB_PATH", str(db_file))
    monkeypatch.setenv("CLAIRE_DATA_DIR", str(data_dir))
    base_settings: dict[str, Any] = {
        "_env_file": None,
        "CLAIRE_DB_PATH": str(db_file),
        "CLAIRE_DATA_DIR": str(data_dir),
        "CLAIRE_PROVIDER": "mock",
        "CLAIRE_ENVIRONMENT": "development",
        "CLAIRE_FQDN": PUBLIC_URL,
    }
    base_settings.update(setting_overrides)
    settings = Settings(**base_settings)
    app = server.create_app(settings, SimpleNamespace(provider=SimpleNamespace(name="mock")))
    return SimpleNamespace(settings=settings, db_file=db_file, data_dir=data_dir, client=TestClient(app, base_url=PUBLIC_URL), admin=bearer_headers(db_file, account_id=1, scope="owner"), member=bearer_headers(db_file, account_id=2, scope="owner"))


def _add_document_with_lineage(db_file: Path) -> None:
    conn = dbm.connect(db_file)
    try:
        lineage = {"final_output_models": {"extract_graph": {"provider": "gemini", "model": "gemini-3.1-flash-lite-medium"}}, "jev": {"status": "disabled"}, "fallbacks_triggered": []}
        conn.execute("INSERT INTO documents (id, title, url, source_type, raw_text, meta, fetched_at) VALUES ('doc-lineage-1', ?, 'https://example.com/a', 'web', 'text', ?, ?)", (ADMIN_DOC_TITLE, json.dumps({"model_lineage": lineage}), time.time()))
        conn.commit()
    finally:
        conn.close()


def _extract_pipeline_json(html: str) -> dict[str, Any]:
    match = re.search(r'<script id="claire-pipeline-data" type="application/json">(.*?)</script>', html, re.S)
    assert match is not None, "Missing #claire-pipeline-data JSON script"
    return json.loads(match.group(1))


def test_media_language_order_follows_the_preferred_languages_and_the_stt_language(tmp_path: Path) -> None:
    base = Settings(_env_file=None, CLAIRE_DB_PATH=str(tmp_path / "none.db"), preferred_languages="ko,ja", CLAIRE_ENVIRONMENT="development")
    order = media_language_order(base)
    assert order["caption_languages"] == ["ko", "ja", "en"]
    # The default STT language "auto" sends the preferred languages as hints.
    assert (order["stt_language"], order["stt_hints"], order["vlm_language"]) == ("auto", ["ko", "ja", "en"], "ko")
    forced = media_language_order(base.model_copy(update={"stt_language": "ja"}))
    assert (forced["stt_language"], forced["stt_hints"]) == ("ja", ["ja"])
    # The /admin/providers view carries the same order.
    assert describe_server_providers(base)["stt"]["order"] == order


def test_build_pipeline_view_data_role_isolation_and_escaping(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = _env(tmp_path, monkeypatch, enable_jev=True, jev_model="jev-latest")
    auth_conn = auth_db.connect_auth_db(auth_db.auth_db_path(env.data_dir))
    try:
        provider_keys.put_key(auth_conn, account_id=2, provider="gemini", api_key=MEMBER_BYOK_KEY, status="active", model_prefs={"model": "gemini-3.1-flash-lite-medium"})
    finally:
        auth_conn.close()

    server_view = describe_server_providers(env.settings)
    member_view = describe_account_providers(env.settings, account_id=2, db_path=env.db_file, auth_db_path=auth_db.auth_db_path(env.data_dir))

    pub = build_pipeline_view_data(viewer="public", settings=env.settings, locale="ko")
    mem = build_pipeline_view_data(viewer="member", settings=env.settings, view=member_view, locale="ko")
    adm = build_pipeline_view_data(viewer="admin", settings=env.settings, view=server_view, locale="ko")

    required_keys = {"viewer", "sourceLabel", "primaryLangName", "langOrder", "autoMerge", "borderline", "showJev", "showSimulators", "models", "i18n"}
    required_models = {"classify", "extract_graph", "summarize", "render_detail", "entity_judge", "relation_judge", "research", "embed", "stt", "vlm", "client_llm", "jev"}
    for payload in (pub, mem, adm):
        assert required_keys <= set(payload)
        assert required_models <= set(payload["models"])
        assert payload["showSimulators"] is True
        assert payload["primaryLangName"] == "한국어"

    assert pub["viewer"] == "public" and pub["showJev"] is False and pub["models"]["jev"] == ""
    assert "gemini-3.1-flash-lite" not in json.dumps(pub["models"])
    assert mem["viewer"] == "member" and mem["showJev"] is False and mem["models"]["jev"] == ""
    assert "gemini-3.1-flash-lite" in mem["models"]["extract_graph"]
    assert adm["viewer"] == "admin" and adm["showJev"] is True and "jev-latest" in adm["models"]["jev"]

    mount_html = render_pipeline_mount_html({"viewer": "public", "tag": "<script>&</script>"})
    assert "\\u003cscript\\u003e\\u0026\\u003c/script\\u003e" in mount_html
    assert "<script>&</script>" not in mount_html


def test_pipeline_page_renders_gemini_neon_mount_and_strict_member_isolation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = _env(tmp_path, monkeypatch, enable_jev=True, jev_model="jev-latest")
    _add_document_with_lineage(env.db_file)

    admin = env.client.get("/support/pipeline", headers=env.admin)
    assert admin.status_code == 200, admin.text
    html = admin.text

    # Tabs + ToC host + shared Gemini Neon mount inside #pipeline-graph + Media + Records
    assert 'data-toc-tabs="1"' in html
    assert 'id="pipeline-tabs"' in html
    assert re.findall(r'data-tab="([a-z]+)"', html) == ["flow", "media", "records"]
    assert "/static/css/pipeline_flow.css" in html
    assert "/static/js/pipeline_flow.js" in html
    assert 'id="pipeline-graph"' in html
    assert 'class="claire-pipeline-mount" data-pipeline-mount data-pipeline-json-id="claire-pipeline-data"' in html

    # Q4: Legacy prose flow cards & static SVG are gone
    for legacy_id in ("pipeline-api-flow", "pipeline-client-flow", "pipeline-other", "pipeline-lineage-svg"):
        assert f'id="{legacy_id}"' not in html

    admin_data = _extract_pipeline_json(html)
    assert admin_data["viewer"] == "admin"
    assert admin_data["showJev"] is True
    assert admin_data["showSimulators"] is True
    assert "jev-latest" in admin_data["models"]["jev"]
    assert admin_data["primaryLangName"] == "한국어"

    # Media tab: STT order + VLM + Fixed values
    order = re.findall(r'data-stt-order="([a-z]+)"', html)
    assert order == ["manual", "automatic", "stt", "none"]
    assert re.search(r"ko</code> → <code[^>]*>ja</code> → <code[^>]*>en</code>", html)
    assert 'href="/admin/server#server-languages"' in html
    assert 'id="pipeline-vlm"' in html and 'id="pipeline-fixed"' in html
    assert "gemini-decision-1" in html

    # Records tab: administrator only
    assert 'id="pipeline-recent"' in html and ADMIN_DOC_TITLE in html
    assert 'data-fallback="F2"' in html and "Interactions schema failure fell back" in html
    assert "console.support.pipeline." not in html

    # Member view with BYOK key: Q5 simulator access + strict privacy isolation
    auth_conn = auth_db.connect_auth_db(auth_db.auth_db_path(env.data_dir))
    try:
        provider_keys.put_key(auth_conn, account_id=2, provider="gemini", api_key=MEMBER_BYOK_KEY, status="active", model_prefs={"model": "gemini-3.1-flash-lite-medium"})
    finally:
        auth_conn.close()

    member = env.client.get("/support/pipeline", headers=env.member)
    assert member.status_code == 200, member.text
    page = member.text
    assert 'data-toc-tabs="1"' in page
    assert re.findall(r'data-tab="([a-z]+)"', page) == ["flow", "media"]
    assert 'id="pipeline-graph"' in page
    assert 'data-pipeline-mount' in page
    assert 'id="pipeline-stt-order"' in page and 'id="pipeline-vlm"' in page and 'id="pipeline-fixed"' in page
    assert 'href="/account/profile#settings-languages"' in page

    member_data = _extract_pipeline_json(page)
    assert member_data["viewer"] == "member"
    assert member_data["showJev"] is False
    assert member_data["showSimulators"] is True
    assert member_data["models"]["jev"] == ""
    assert "gemini-3.1-flash-lite" in member_data["models"]["extract_graph"]

    page_no_scripts = re.sub(r"<script\b[^>]*>.*?</script>", "", page, flags=re.S)
    for forbidden in (ADMIN_DOC_TITLE, "pipeline-recent", "pipeline-fallbacks", 'data-tab="records"', "jev-latest", "gemini-decision-1", "/admin/", MEMBER_BYOK_KEY, "console.support.pipeline.", "pipeline.neon."):
        assert forbidden not in page_no_scripts, f"Forbidden content in member /support/pipeline: {forbidden}"


def test_pipeline_page_reflects_fixed_effort_models_and_strips_vlm_effort_suffix(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from claire.core.provider_manager import ProviderManager

    env = _env(tmp_path, monkeypatch, vlm_model="gemini-3.1-flash-lite-medium")
    pm = ProviderManager(data_dir=env.db_file.parent, auto_migrate=False)
    cfg = pm.get_config()
    cfg["active_provider"] = "gemini"
    cfg["providers"]["gemini"]["api_key"] = "AIzaTestDummyKey000000000000000000000"
    cfg["providers"]["gemini"]["model"] = "gemini-3.1-flash-lite-medium"
    cfg["providers"]["gemini"]["effort"] = "medium"
    cfg["feature_routing"]["classify"] = {"provider": "gemini", "model": "gemini-3.1-flash-lite-low", "effort": ""}
    cfg["feature_routing"]["extract_graph"] = {"provider": "gemini", "model": "gemini-3.1-flash-lite-medium", "effort": ""}
    pm.save_config(cfg)

    resp = env.client.get("/support/pipeline", headers=env.admin)
    assert resp.status_code == 200, resp.text
    html = resp.text

    data = _extract_pipeline_json(html)
    assert data["models"]["classify"] == "gemini-3.1-flash-lite-low"
    assert data["models"]["extract_graph"] == "gemini-3.1-flash-lite-medium"
    assert data["models"]["vlm"] == "gemini-3.1-flash-lite"

    # VLM card displays clean API model identifier without the embedded effort suffix.
    vlm_card = re.search(r'id="pipeline-vlm".*?id="pipeline-fixed"', html, re.S)
    assert vlm_card is not None
    assert "gemini-3.1-flash-lite" in vlm_card.group(0)
    assert "gemini-3.1-flash-lite-medium" not in vlm_card.group(0)
    assert "console.support.pipeline." not in html


def test_account_providers_card_links_the_video_order(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = _env(tmp_path, monkeypatch)
    page = env.client.get("/account/providers", headers=env.member)
    assert page.status_code == 200, page.text
    line = re.search(r'<p class="hint" data-effective-stt-order>(.*?)</p>', page.text, re.S)
    assert line is not None
    assert re.search(r"ko</code> → <code[^>]*>ja</code> → <code[^>]*>en</code>", line.group(1))
    assert 'href="/support/pipeline#pipeline-stt-order"' in line.group(1)
