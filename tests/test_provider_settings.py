"""PROVIDER_SETTINGS_SIMPLIFICATION_DESIGN.md: what applies to an account (``accounts/provider_view.py``, reads claire.db, auth.db, and providers.json) and the save rules of ``PATCH /providers``."""

from __future__ import annotations

import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from claire.accounts import server_tier
from claire.accounts.provider_policy import ServerTier
from claire.accounts.provider_view import describe_account_providers, describe_server_providers
from claire.api import server
from claire.core import secret_scrub
from claire.core.config import Settings
from claire.store import auth_db, provider_keys
from claire.store import accounts as accounts_store
from claire.store import db as dbm
from claire.store.secrets import reset_secret_service

PUBLIC_URL = "http://127.0.0.1:8765"
OWNER_TOKEN = "owner-provider-settings-" + "x" * 16
TEST_MASTER_KEY_HEX = "5a" * 32
KEY_CANARY = "AIzaProviderSettingsCanary1234567890"


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch):
    for key in list(os.environ):
        if key.startswith(("CLAIRE_", "GEMINI_", "TELEGRAM_")):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("CLAIRE_SECRET_KEY", TEST_MASTER_KEY_HEX)
    reset_secret_service()
    secret_scrub._reset_for_tests()
    yield
    reset_secret_service()
    secret_scrub._reset_for_tests()


def _world(tmp_path: Path, *, gemini_key: str = "") -> SimpleNamespace:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    db_file = data_dir / "claire.db"
    conn = dbm.connect(db_file)
    try:
        dbm.init_db(conn)
        store = accounts_store.AccountStore(conn)
        store.complete_setup(1, username="admin_ps", display_name="Admin")
        now = time.time()
        conn.execute(
            "INSERT INTO accounts (id, public_id, username, role, status, display_name, profile_public, created_at, activated_at, updated_at) VALUES (2, 223456789099, 'member_ps', 'member', 'active', 'Member', 0, ?, ?, ?)",
            (now, now, now),
        )
        store.set_setting("mode", "network", updated_by=1)
        conn.commit()
    finally:
        conn.close()
    auth_db.connect_auth_db(auth_db.auth_db_path(data_dir)).close()
    settings = Settings(
        _env_file=None,
        CLAIRE_DB_PATH=str(db_file),
        CLAIRE_DATA_DIR=str(data_dir),
        CLAIRE_PROVIDER="gemini",
        GEMINI_API_KEY=gemini_key,
        CLAIRE_ENVIRONMENT="development",
        CLAIRE_FQDN=PUBLIC_URL,
        CLAIRE_INJECT_TOKEN=OWNER_TOKEN,
    )
    return SimpleNamespace(settings=settings, data_dir=data_dir, db_file=db_file, auth_file=auth_db.auth_db_path(data_dir))


def _save_tier(world: SimpleNamespace, tier: ServerTier) -> None:
    conn = dbm.connect_existing(world.db_file)
    try:
        server_tier.save_server_tier(conn, tier, updated_by=1)
    finally:
        conn.close()


def _put_key(world: SimpleNamespace, *, status: str, model: str = "") -> None:
    auth = auth_db.connect_auth_db(world.auth_file)
    try:
        provider_keys.put_key(auth, account_id=2, provider="gemini", api_key=KEY_CANARY, status=status, model_prefs={"model": model} if model else None)
    finally:
        auth.close()


def test_account_view_source_models_and_limits_follow_the_key_and_the_tier(tmp_path: Path) -> None:
    world = _world(tmp_path, gemini_key="AIzaServerKey1234567890abcdef")

    # No key, server tier "none": no API; the jobs do no AI work and the ingests wait for the agent client.
    none = describe_account_providers(world.settings, account_id=2)
    assert none["source"] == "none"
    assert all(not row["available"] for row in none["slots"])
    assert none["extraction_modes"] == ["client"]
    assert none["stt"]["available"] is False and none["stt"]["reason"] == "no_api"

    # The server tier with the server key: the model of the tier; the monthly tokens apply.
    _save_tier(world, ServerTier(provider="gemini", model="gemini-3.8-flash-low", max_effort="medium", features=frozenset({"stt"})))
    tier = describe_account_providers(world.settings, account_id=2)
    assert tier["source"] == "tier"
    assert {row["slot"]: row["model"] for row in tier["slots"] if row["available"]}["extract_graph"] == "gemini-3.8-flash-low"
    assert {row["slot"]: row.get("reason") for row in tier["slots"]}["research"] == "admin_only"
    assert tier["limits"]["monthly_tokens"] is not None and tier["limits"]["max_concurrency"] is None
    assert tier["extraction_modes"] == ["provider", "client"]

    # An active key with a model: the key and its model; no monthly token budget, the concurrency cap of a key. No decryption happened.
    _put_key(world, status="active", model="gemini-4-argon-high")
    byok = describe_account_providers(world.settings, account_id=2)
    assert byok["source"] == "byok"
    assert byok["key"]["fingerprint"] and KEY_CANARY not in repr(byok)
    text = {row["slot"]: row for row in byok["slots"] if row["available"]}
    assert text["summarize"]["model"] == "gemini-4-argon-high" and text["summarize"]["from"] == "key_model"
    assert byok["limits"]["monthly_tokens"] is None and byok["limits"]["max_concurrency"] == 1
    auth = auth_db.connect_auth_db(world.auth_file)
    try:
        assert provider_keys.list_key_decryptions(auth, account_id=2, limit=10) == []
    finally:
        auth.close()

    # A registered key that is not active: the jobs fail (no change to the server tier); the API choice stays.
    _put_key(world, status="rejected")
    problem = describe_account_providers(world.settings, account_id=2)
    assert problem["source"] == "key_problem"
    assert problem["extraction_modes"] == ["provider", "client"]

    # The administrator without a personal key: the server providers; no limit applies.
    admin = describe_account_providers(world.settings, account_id=1)
    assert admin["source"] == "server" and admin["limits"] == {"exempt": True}


def test_server_view_shows_fixed_stt_and_the_problem_of_a_missing_key(tmp_path: Path) -> None:
    world = _world(tmp_path)
    view = describe_server_providers(world.settings)
    assert view["stt"]["model"] == "gemini-3.5-transcribe"
    assert "no_api_key" in view["problems"]
    assert [row["slot"] for row in view["slots"]] == ["extract_graph", "summarize", "render_detail", "entity_judge", "relation_judge", "classify", "research"]


def test_providers_patch_saves_each_part_and_refuses_what_does_not_apply(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    world = _world(tmp_path)
    # After a save the route reads the settings again from the environment (get_settings): the environment must name this data folder, else the next save writes the providers.json of the repository.
    monkeypatch.setenv("CLAIRE_DB_PATH", str(world.db_file))
    monkeypatch.setenv("CLAIRE_DATA_DIR", str(world.data_dir))
    monkeypatch.setenv("CLAIRE_ENVIRONMENT", "development")
    monkeypatch.setenv("GEMINI_API_KEY", "")
    repo_providers = Path("data/providers.json")
    before = repo_providers.read_bytes() if repo_providers.is_file() else None
    app = server.create_app(world.settings, SimpleNamespace(provider=SimpleNamespace(name="mock")))
    owner = {"Authorization": f"Bearer {OWNER_TOKEN}", "Origin": PUBLIC_URL, "Accept": "application/json"}
    with TestClient(app, base_url=PUBLIC_URL) as client:
        # Google AI Studio becomes the main provider with a key; then the key is deleted, and a save of another part is still stored (only a change of the main provider is checked).
        keyed = client.patch("/providers", headers=owner, json={"active_provider": "gemini", "providers": {"gemini": {"api_key": "AIzaServerKey1234567890abcdef"}}})
        assert keyed.status_code == 200, keyed.text
        cleared = client.patch("/providers", headers=owner, json={"providers": {"gemini": {"api_key": "", "clear_api_key": True}}})
        assert cleared.status_code == 200, cleared.text
        assert cleared.json()["config"]["providers"]["gemini"]["has_api_key"] is False
        # STT keeps one provider and no model choice.
        stt = client.patch("/providers", headers=owner, json={"providers": {"stt": {"enabled": True, "language": "en", "provider": "mock", "model": "other"}}})
        assert stt.status_code == 200, stt.text
        cfg = stt.json()["config"]
        assert cfg["providers"]["stt"]["provider"] == "gemini" and cfg["providers"]["stt"]["language"] == "en"
        assert cfg["providers"]["stt"].get("model", "") == ""
        # The server tier is not saved here: /admin/server edits it.
        tier = client.patch("/providers", headers=owner, json={"server_tier": {"provider": "gemini"}})
        assert tier.status_code == 200 and tier.json()["config"]["server_tier"]["provider"] == "none"
        # STT and VLM are not slots of the feature routing.
        for slot in ("stt", "vlm"):
            bad = client.patch("/providers", headers=owner, json={"feature_routing": {slot: {"provider": "inherit", "model": "gemini-3.8-flash-low"}}})
            assert bad.status_code == 400, slot
        # A production server refuses the test provider.
        monkeypatch.setattr(Settings, "is_production", property(lambda self: True))
        refused = client.patch("/providers", headers=owner, json={"active_provider": "mock"})
        assert (refused.status_code, refused.json()) == (400, {"error": "mock_not_allowed_in_production"})
    assert (repo_providers.read_bytes() if repo_providers.is_file() else None) == before
