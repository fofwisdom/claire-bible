"""The page ``/support/pipeline`` (``api.accounts_routes.pipeline``): the interface between the provider views (``accounts.provider_view``), the lineage summary (``core.provider_manager``, reads claire.db), and the console page, for the administrator and for a member."""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from claire.accounts import languages
from claire.accounts.provider_view import describe_server_providers, media_language_order
from claire.api import server
from claire.core.config import Settings
from claire.store import accounts as accounts_store
from claire.store import db as dbm
from claire.store.secrets import reset_secret_service
from tests.auth_helpers import bearer_headers

PUBLIC_URL = "http://127.0.0.1:8765"
ADMIN_DOC_TITLE = "Lineage record of another account"
EXPECTED_API_STEPS = [
    "input",
    "classify",
    "check",
    "effort",
    "extract",
    "summary",
    "detail",
    "resolve",
    "record",
    "relations",
    "links",
    "lineage",
    "post",
]


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
    data_dir.mkdir()
    db_file = data_dir / "claire.db"
    conn = dbm.connect(db_file)
    try:
        dbm.init_db(conn)
        store = accounts_store.AccountStore(conn)
        store.complete_setup(1, username="admin_pipe", display_name="Admin")
        now = time.time()
        conn.execute(
            "INSERT INTO accounts (id, public_id, username, role, status, display_name, profile_public, created_at, activated_at, updated_at) VALUES (2, 223456789077, 'member_pipe', 'member', 'active', 'Member', 0, ?, ?, ?)",
            (now, now, now),
        )
        store.set_setting("mode", "network", updated_by=1)
        conn.commit()
        languages.set_server_languages(conn, ["ko", "ja"], updated_by=1)
    finally:
        conn.close()
    # The lineage summary reads the documents of claire.db (CLAIRE_DB_PATH first).
    monkeypatch.setenv("CLAIRE_DB_PATH", str(db_file))
    base_settings: dict[str, object] = {
        "_env_file": None,
        "CLAIRE_DB_PATH": str(db_file),
        "CLAIRE_PROVIDER": "mock",
        "CLAIRE_ENVIRONMENT": "development",
        "CLAIRE_FQDN": PUBLIC_URL,
    }
    base_settings.update(setting_overrides)
    settings = Settings(**base_settings)
    app = server.create_app(settings, SimpleNamespace(provider=SimpleNamespace(name="mock")))
    return SimpleNamespace(settings=settings, db_file=db_file, client=TestClient(app, base_url=PUBLIC_URL), admin=bearer_headers(db_file, account_id=1, scope="owner"), member=bearer_headers(db_file, account_id=2, scope="owner"))


def _add_document_with_lineage(db_file: Path) -> None:
    conn = dbm.connect(db_file)
    try:
        lineage = {"final_output_models": {"extract_graph": {"provider": "gemini", "model": "gemini-3.1-flash-lite-medium"}}, "jev": {"status": "disabled"}, "fallbacks_triggered": []}
        conn.execute(
            "INSERT INTO documents (id, title, url, source_type, raw_text, meta, fetched_at) VALUES ('doc-lineage-1', ?, 'https://example.com/a', 'web', 'text', ?, ?)",
            (ADMIN_DOC_TITLE, json.dumps({"model_lineage": lineage}), time.time()),
        )
        conn.commit()
    finally:
        conn.close()


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


def test_pipeline_page_renders_tabs_svg_accurate_flow_and_strict_member_isolation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = _env(tmp_path, monkeypatch)
    _add_document_with_lineage(env.db_file)

    admin = env.client.get("/support/pipeline", headers=env.admin)
    assert admin.status_code == 200, admin.text
    html = admin.text

    # Tabs + ToC host + SVG graph + 4 cards in Flow tab + 3 cards in Media tab + 2 cards in Records tab
    assert 'data-toc-tabs="1"' in html
    assert 'id="pipeline-tabs"' in html
    assert re.findall(r'data-tab="([a-z]+)"', html) == ["flow", "media", "records"]
    assert 'id="pipeline-lineage-svg"' in html
    assert 'class="pipeline-graph-wrap"' in html
    assert 'class="pipeline-graph-legend"' in html
    assert "pg-lane" in html and "pg-node-shadow" in html

    # Accurate 13-step server API flow with conditional/branch kinds and no client mark on summary/detail
    steps = re.findall(r'data-flow-step="([a-z]+)"', html)
    assert steps == EXPECTED_API_STEPS
    assert 'data-flow-step="summary" data-flow-kind="conditional"' in html
    assert 'data-flow-step="detail" data-flow-kind="branch"' in html
    summary_block = re.search(r'data-flow-step="summary".*?data-flow-step="detail"', html, re.S)
    detail_block = re.search(r'data-flow-step="detail".*?data-flow-step="resolve"', html, re.S)
    assert summary_block is not None and 'data-client-role="client"' not in summary_block.group(0)
    assert detail_block is not None and 'data-client-role="client"' not in detail_block.group(0)
    assert 'data-summary-state="skipped"' in html
    assert 'data-effort-mode="adaptive"' in html
    assert "data-jev-state" in html and 'data-flow-step="jev"' not in html
    assert re.findall(r'data-flow-gate="(\d)"', html) == ["1", "2", "3", "4", "5"]

    # Client flow card + Other AI table
    assert 'id="pipeline-client-flow"' in html
    assert re.findall(r'data-client-step="(\d)"', html) == ["1", "2", "3", "4", "5"]
    assert "stage_raw" in html and "extract_claim" in html and "extract_submit" in html
    assert 'id="pipeline-other"' in html
    for token in ("RETRIEVAL_QUERY", "summarize_search", "select_followups", "compose_presentation", "gemini-decision-1", "gemini-3.1-flash-lite"):
        assert token in html

    # Media tab: STT order + VLM + Fixed values
    order = re.findall(r'data-stt-order="([a-z]+)"', html)
    assert order == ["manual", "automatic", "stt", "none"]
    assert re.search(r"ko</code> → <code[^>]*>ja</code> → <code[^>]*>en</code>", html)
    assert 'href="/admin/server#server-languages"' in html
    assert 'id="pipeline-vlm"' in html and 'id="pipeline-fixed"' in html

    # Records tab: administrator only
    assert 'id="pipeline-recent"' in html and ADMIN_DOC_TITLE in html
    assert 'data-fallback="F2"' in html and "Interactions schema failure fell back" in html
    assert "console.support.pipeline." not in html

    # Member view: strict privacy and isolation
    member = env.client.get("/support/pipeline", headers=env.member)
    assert member.status_code == 200, member.text
    page = member.text
    assert 'data-toc-tabs="1"' in page
    assert re.findall(r'data-tab="([a-z]+)"', page) == ["flow", "media"]
    assert re.findall(r'data-flow-step="([a-z]+)"', page) == EXPECTED_API_STEPS
    assert 'id="pipeline-lineage-svg"' in page and "pg-node-shadow" not in page
    assert 'id="pipeline-client-flow"' in page and 'id="pipeline-other"' in page
    assert 'id="pipeline-stt-order"' in page and 'id="pipeline-vlm"' in page and 'id="pipeline-fixed"' in page
    assert ADMIN_DOC_TITLE not in page
    assert 'id="pipeline-recent"' not in page and 'id="pipeline-fallbacks"' not in page
    assert 'data-tab="records"' not in page and "data-jev-state" not in page
    assert "gemini-decision-1" not in page
    assert "/admin/" not in page and 'href="/account/profile#settings-languages"' in page
    assert 'data-flow-models' in page
    assert "console.support.pipeline." not in page


def test_pipeline_page_reflects_fixed_effort_models_and_strips_vlm_effort_suffix(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from claire.core.provider_manager import ProviderManager

    env = _env(
        tmp_path,
        monkeypatch,
        vlm_model="gemini-3.1-flash-lite-medium",
    )
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

    assert 'data-classify-effort="fixed"' in html
    assert 'data-effort-mode="fixed"' in html
    # VLM displays clean API model identifier without the embedded effort suffix.
    vlm_card = re.search(r'id="pipeline-vlm".*?id="pipeline-fixed"', html, re.S)
    assert vlm_card is not None
    assert "gemini-3.1-flash-lite" in vlm_card.group(0)
    assert "gemini-3.1-flash-lite-medium" not in vlm_card.group(0)
    assert "console.support.pipeline." not in html


def test_account_providers_card_links_the_video_order(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = _env(tmp_path, monkeypatch)
    page = env.client.get("/account/providers", headers=env.member)
    assert page.status_code == 200, page.text
    line = re.search(r"<p class=\"hint\" data-effective-stt-order>(.*?)</p>", page.text, re.S)
    assert line is not None
    assert re.search(r"ko</code> → <code[^>]*>ja</code> → <code[^>]*>en</code>", line.group(1))
    assert 'href="/support/pipeline#pipeline-stt-order"' in line.group(1)
