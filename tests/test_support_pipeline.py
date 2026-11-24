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
from claire.store.token_store import get_token_store, reset_token_stores

PUBLIC_URL = "http://127.0.0.1:8765"
ADMIN_DOC_TITLE = "Lineage record of another account"


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch):
    reset_secret_service()
    reset_token_stores()
    for key in list(os.environ):
        if key.startswith(("CLAIRE_", "GEMINI_")):
            monkeypatch.delenv(key, raising=False)
    yield
    reset_secret_service()
    reset_token_stores()


def _env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
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
    settings = Settings(
        _env_file=None,
        CLAIRE_DB_PATH=str(db_file),
        CLAIRE_DATA_DIR=str(data_dir),
        CLAIRE_PROVIDER="mock",
        CLAIRE_ENVIRONMENT="development",
        CLAIRE_PUBLIC_URL=PUBLIC_URL,
    )
    tokens = get_token_store(data_dir=data_dir)
    admin = tokens.create_token(name="admin-owner", scope="owner", account_id=1)["token"]
    member = tokens.create_token(name="member-owner", scope="owner", account_id=2)["token"]
    app = server.create_app(settings, SimpleNamespace(provider=SimpleNamespace(name="mock")))
    return SimpleNamespace(settings=settings, db_file=db_file, client=TestClient(app, base_url=PUBLIC_URL), admin={"Authorization": f"Bearer {admin}"}, member={"Authorization": f"Bearer {member}"})


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


def test_pipeline_page_shows_the_steps_in_order_and_the_server_records_only_to_the_administrator(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = _env(tmp_path, monkeypatch)
    _add_document_with_lineage(env.db_file)

    admin = env.client.get("/support/pipeline", headers=env.admin)
    assert admin.status_code == 200, admin.text
    html = admin.text
    steps = re.findall(r'data-flow-step="([a-z]+)"', html)
    assert steps == ["input", "check", "effort", "extract", "summary", "detail", "resolve", "record", "relations", "links", "lineage"]
    # Jev is a note beside the resolution step, not a step of the flow.
    assert "data-jev-state" in html and 'data-flow-step="jev"' not in html
    assert re.findall(r'data-flow-gate="(\d)"', html) == ["1", "2", "3", "4", "5"]
    assert 'data-client-role="client"' in html and 'data-client-role="api_only"' in html
    # The test provider uses one model for extraction and summary: the separate summary does not run.
    assert 'data-summary-state="skipped"' in html
    # The video order: publisher captions, then automatic captions, in the preferred order with en last; STT only after them.
    order = re.findall(r'data-stt-order="([a-z]+)"', html)
    assert order == ["manual", "automatic", "stt", "none"]
    assert re.search(r"ko</code> → <code[^>]*>ja</code> → <code[^>]*>en</code>", html)
    assert 'href="/admin/server#server-languages"' in html
    # The administrator sees the lineage of the documents of all accounts and the removed fallbacks with their old behavior.
    assert 'id="pipeline-recent"' in html and ADMIN_DOC_TITLE in html
    assert 'data-fallback="F2"' in html and "Interactions schema failure fell back" in html

    member = env.client.get("/support/pipeline", headers=env.member)
    assert member.status_code == 200, member.text
    page = member.text
    assert re.findall(r'data-flow-step="([a-z]+)"', page) == steps
    assert 'id="pipeline-stt-order"' in page
    assert ADMIN_DOC_TITLE not in page
    assert 'id="pipeline-recent"' not in page and 'id="pipeline-fallbacks"' not in page and "data-jev-state" not in page
    assert "/admin/" not in page and 'href="/account/profile#settings-languages"' in page
    # The member has no API: the AI rows show the reason, not a model.
    assert 'data-flow-models' in page


def test_account_providers_card_links_the_video_order(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = _env(tmp_path, monkeypatch)
    page = env.client.get("/account/providers", headers=env.member)
    assert page.status_code == 200, page.text
    line = re.search(r"<p class=\"hint\" data-effective-stt-order>(.*?)</p>", page.text, re.S)
    assert line is not None
    assert re.search(r"ko</code> → <code[^>]*>ja</code> → <code[^>]*>en</code>", line.group(1))
    assert 'href="/support/pipeline#pipeline-stt-order"' in line.group(1)
