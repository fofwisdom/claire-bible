"""Tests for ProviderManager and WebUI provider management."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from claire.api.server import create_app
from claire.core.config import Settings
from claire.core.provider_manager import (
    DEFAULT_PROVIDERS_CONFIG,
    MIGRATED_ENV_VARS,
    ProviderManager,
    comment_out_env_file,
    get_provider_manager,
)


def test_comment_out_env_file(tmp_path: Path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "CLAIRE_ENVIRONMENT=production\n"
        "CB_API_PORT=8765\n"
        "CLAIRE_PROVIDER=gemini\n"
        "GEMINI_API_KEY=AIzaSyTestKey123\n"
        "CLAIRE_GEMINI_MODEL=gemini-3.1-flash-lite\n"
        "# Already commented\n"
        "# CLAIRE_AGY_BIN=agy\n"
        "CLAIRE_INJECT_TOKEN=test-token-12345678901234567890\n",
        encoding="utf-8",
    )

    count = comment_out_env_file(env_file, MIGRATED_ENV_VARS)
    assert count == 3

    lines = env_file.read_text(encoding="utf-8").splitlines()
    assert "CLAIRE_ENVIRONMENT=production" in lines
    assert "CB_API_PORT=8765" in lines
    assert "CLAIRE_INJECT_TOKEN=test-token-12345678901234567890" in lines
    assert "# Already commented" in lines

    # Verify commented lines
    commented_prov = [l for l in lines if "CLAIRE_PROVIDER=gemini" in l]
    assert len(commented_prov) == 1
    assert commented_prov[0].startswith("# CLAIRE_PROVIDER=gemini")

    commented_key = [l for l in lines if "GEMINI_API_KEY=AIzaSyTestKey123" in l]
    assert len(commented_key) == 1
    assert commented_key[0].startswith("# GEMINI_API_KEY=AIzaSyTestKey123")

    commented_model = [l for l in lines if "CLAIRE_GEMINI_MODEL=gemini-3.1-flash-lite" in l]
    assert len(commented_model) == 1
    assert commented_model[0].startswith("# CLAIRE_GEMINI_MODEL=gemini-3.1-flash-lite")


def test_provider_manager_auto_migration(tmp_path: Path):
    data_dir = tmp_path / "data"
    env_file = tmp_path / ".env"
    env_file.write_text(
        "CLAIRE_PROVIDER=gemini\n"
        "GEMINI_API_KEY=AIzaSyTestKey999\n"
        "CLAIRE_GEMINI_MODEL=gemini-2.5-flash\n"
        "CLAIRE_AGY_BIN=custom_agy\n"
        "CB_API_PORT=8765\n",
        encoding="utf-8",
    )

    pm = ProviderManager(
        data_dir=data_dir,
        auto_migrate=True,
        env_files=[env_file],
    )

    assert pm.registry_path.is_file()
    cfg = pm.get_config()
    assert cfg["active_provider"] == "gemini"
    assert cfg["providers"]["gemini"]["api_key"] == "AIzaSyTestKey999"
    assert cfg["providers"]["gemini"]["model"] == "gemini-2.5-flash"
    # The variables of the retired host-cli providers are not migrated: no provider block is made for them
    assert "antigravity" not in cfg["providers"]

    # Verify .env was commented
    env_content = env_file.read_text(encoding="utf-8")
    assert "# CLAIRE_PROVIDER=gemini" in env_content
    assert "# GEMINI_API_KEY=AIzaSyTestKey999" in env_content
    assert "CB_API_PORT=8765" in env_content
    assert "\nCLAIRE_AGY_BIN=custom_agy\n" in env_content


def test_provider_manager_sanitized_config(tmp_path: Path):
    data_dir = tmp_path / "data"
    pm = ProviderManager(data_dir=data_dir, auto_migrate=False)
    pm.save_config({
        "version": 1,
        "active_provider": "gemini",
        "providers": {
            "gemini": {
                "api_key": "AIzaSySecretKey",
                "model": "gemini-3.1-flash-lite",
            },
        },
    })

    sanitized = pm.get_sanitized_config()
    assert sanitized["providers"]["gemini"]["has_api_key"] is True
    assert sanitized["providers"]["gemini"]["api_key"] == "••••••••"


def test_retired_blocks_stay_in_the_file_and_never_reach_the_web_ui(tmp_path: Path):
    """An old providers.json can hold blocks of retired features (host-cli providers, the openai placeholder, the personal CLI block). They stay in the file as data; the Web UI does not get them, because they can hold old credentials."""
    data_dir = tmp_path / "data"
    pm = ProviderManager(data_dir=data_dir, auto_migrate=False)
    old_secret = "sk-old-openai-secret-0001"
    pm.save_config({
        "version": 1,
        "active_provider": "mock",
        "providers": {
            "gemini": {"api_key": "", "model": "gemini-3.1-flash-lite-medium"},
            "openai": {"api_key": old_secret, "model": "gpt-4o-mini"},
            "antigravity": {"bin": "agy", "model": "gemini-3.8-flash-medium"},
            "codex": {"bin": "codex"},
        },
        "personal_cli": {"provider": "antigravity", "token": old_secret},
    })

    sanitized = pm.get_sanitized_config()
    assert set(sanitized["providers"]) == set(DEFAULT_PROVIDERS_CONFIG["providers"])
    assert "personal_cli" not in sanitized
    assert old_secret not in json.dumps(sanitized)

    pm.update_config({"active_provider": "mock", "providers": {"openai": {"api_key": "sk-new"}}})
    stored = json.loads(pm.registry_path.read_text(encoding="utf-8"))
    assert stored["providers"]["openai"] == {"api_key": old_secret, "model": "gpt-4o-mini"}
    assert stored["providers"]["antigravity"]["bin"] == "agy"
    assert stored["personal_cli"] == {"provider": "antigravity", "token": old_secret}


def test_provider_manager_update_config_preserves_secret(tmp_path: Path):
    data_dir = tmp_path / "data"
    pm = ProviderManager(data_dir=data_dir, auto_migrate=False)
    pm.save_config({
        "version": 1,
        "active_provider": "gemini",
        "providers": {
            "gemini": {
                "api_key": "AIzaSyOriginalSecret",
                "model": "gemini-3.1-flash-lite",
            },
        },
    })

    # Update model without changing masked api_key
    updated = pm.update_config({
        "active_provider": "mock",
        "providers": {
            "gemini": {
                "api_key": "••••••••",
                "model": "gemini-2.5-pro",
            },
        },
    })
    assert updated["active_provider"] == "mock"

    # Raw config should still have original secret
    raw = pm.get_config()
    assert raw["providers"]["gemini"]["api_key"] == "AIzaSyOriginalSecret"
    assert raw["providers"]["gemini"]["model"] == "gemini-2.5-pro"


def test_provider_manager_test_connection_mock(tmp_path: Path):
    pm = ProviderManager(data_dir=tmp_path / "data", auto_migrate=False)
    res = pm.test_connection("mock")
    assert res["ok"] is True
    assert "Mock" in res["message"]


def test_provider_api_routes(tmp_path: Path):
    db_file = tmp_path / "claire.db"
    settings = Settings(
        CLAIRE_ENVIRONMENT="development",
        CLAIRE_INJECT_TOKEN="owner-secret-token-123456789012345678",
        CLAIRE_READONLY_TOKEN="readonly-secret-token-123456789012345",
        CLAIRE_DB_PATH=str(db_file),
        CLAIRE_VAULT_PATH=str(tmp_path / "vault"),
        CLAIRE_PUBLIC_URL="http://127.0.0.1:8765",
    )

    app = create_app(settings)
    client = TestClient(app, base_url="http://127.0.0.1:8765")

    # 1. Anonymous access -> 404 Not Found (stealth mode)
    resp = client.get("/providers")
    assert resp.status_code == 404

    # 2. Readonly access -> 404 Not Found (stealth mode)
    resp = client.get("/providers", headers={"Authorization": "Bearer readonly-secret-token-123456789012345"})
    assert resp.status_code == 404

    # 3. Owner access -> 200 OK
    resp = client.get("/providers", headers={"Authorization": "Bearer owner-secret-token-123456789012345678"})
    assert resp.status_code == 200
    data = resp.json()
    assert "active_provider" in data
    assert "providers" in data
    assert "gemini" in data["providers"]

    # 4. PATCH update providers
    patch_resp = client.patch(
        "/providers",
        headers={"Authorization": "Bearer owner-secret-token-123456789012345678"},
        json={
            "active_provider": "gemini",
            "providers": {
                "gemini": {
                    "api_key": "AIzaSyNewTestKey123",
                    "model": "gemini-3.8-flash",
                }
            }
        }
    )
    assert patch_resp.status_code == 200
    res_json = patch_resp.json()
    assert res_json["ok"] is True
    assert res_json["config"]["active_provider"] == "gemini"
    assert res_json["config"]["providers"]["gemini"]["model"] == "gemini-3.8-flash"
    assert res_json["config"]["providers"]["gemini"]["has_api_key"] is True
    assert res_json["config"]["providers"]["gemini"]["api_key"] == "••••••••"

    # 4b. PATCH with unsupported model -> 400
    bad_resp = client.patch(
        "/providers",
        headers={"Authorization": "Bearer owner-secret-token-123456789012345678"},
        json={"providers": {"gemini": {"model": "unsupported-gemini-model"}}},
    )
    assert bad_resp.status_code == 400

    # 4c. The block of a retired provider is ignored: nothing is stored for it
    retired_resp = client.patch(
        "/providers",
        headers={"Authorization": "Bearer owner-secret-token-123456789012345678"},
        json={"providers": {"antigravity": {"model": "gemini-4-hydrogen"}}},
    )
    assert retired_resp.status_code == 200
    assert "antigravity" not in retired_resp.json()["config"]["providers"]

    # 5. POST test connection: Bearer API is rejected (403); only admin browser cookie session + same-origin is allowed.
    bearer_test = client.post(
        "/providers/test",
        headers={"Authorization": "Bearer owner-secret-token-123456789012345678", "Origin": "http://127.0.0.1:8765"},
        json={"provider": "mock"},
    )
    assert bearer_test.status_code == 403

    # 6. POST capability check: anonymous -> 404, Bearer API -> 403
    anon_check = client.post("/providers/check", json={"active_provider": "mock"})
    assert anon_check.status_code == 404

    bearer_check = client.post(
        "/providers/check",
        headers={"Authorization": "Bearer owner-secret-token-123456789012345678", "Origin": "http://127.0.0.1:8765"},
        json={"active_provider": "openai"},
    )
    assert bearer_check.status_code == 403

    # 7. Admin browser cookie session + same-origin -> 200 OK
    import time
    from claire.store import accounts as accounts_store, db as dbm

    conn = dbm.connect(db_file)
    try:
        dbm.init_db(conn)
        sess_token = accounts_store.create_account_session(
            conn,
            account_id=dbm.ADMIN_ACCOUNT_ID,
            scope="owner",
            now=time.time(),
        )
    finally:
        conn.close()

    # Cookie without same-origin -> 403
    no_origin_test = client.post(
        "/providers/test",
        headers={"Cookie": f"claire_session={sess_token}"},
        json={"provider": "mock"},
    )
    assert no_origin_test.status_code == 403

    admin_cookie_headers = {"Cookie": f"claire_session={sess_token}", "Origin": "http://127.0.0.1:8765"}
    test_resp = client.post(
        "/providers/test",
        headers=admin_cookie_headers,
        json={"provider": "mock"},
    )
    assert test_resp.status_code == 200
    assert test_resp.json()["ok"] is True

    check_resp = client.post(
        "/providers/check",
        headers=admin_cookie_headers,
        json={"active_provider": "gemini", "providers": {"gemini": {"api_key": ""}}},
    )
    assert check_resp.status_code == 200
    caps = check_resp.json()
    assert set(caps["providers"]) == {"gemini", "mock"}
    assert caps["providers"]["mock"]["selectable"] is True

    # A retired provider is not a supported active provider -> 400
    retired_check = client.post(
        "/providers/check",
        headers=admin_cookie_headers,
        json={"active_provider": "openai"},
    )
    assert retired_check.status_code == 400


def test_providers_cli(capsys):
    from claire.cli import build_parser

    parser = build_parser()

    # 1. list
    args = parser.parse_args(["providers", "list"])
    rc = args.func(args)
    assert rc == 0
    captured = capsys.readouterr().out
    assert "Claire Bible Providers" in captured

    # 2. test subcommand is removed from CLI
    with pytest.raises(SystemExit):
        parser.parse_args(["providers", "test", "mock"])

    # 3. migrate
    args = parser.parse_args(["providers", "migrate"])
    rc = args.func(args)
    assert rc == 0


def test_provider_capability_checks_and_locks(tmp_path: Path, monkeypatch):
    import claire.core.provider_manager as pm_mod

    pm = ProviderManager(data_dir=tmp_path / "data", auto_migrate=False)

    # 1. The retired providers (host-cli antigravity and codex, the openai placeholder) cannot be selected
    for name in ("openai", "antigravity", "codex"):
        with pytest.raises(ValueError, match="active_provider"):
            pm.check_capabilities({"active_provider": name})
        with pytest.raises(ValueError, match="active_provider"):
            pm.update_config({"active_provider": name})
    assert set(pm.check_capabilities()["providers"]) == {"gemini", "mock"}

    # 2. Gemini without API key is locked; with key + live probe failure is locked; with passing probe is selectable
    caps_gem_nokey = pm.check_capabilities({"active_provider": "gemini"})
    assert caps_gem_nokey["providers"]["gemini"]["selectable"] is False
    assert caps_gem_nokey["can_save"] is False
    with pytest.raises(ValueError):
        pm.update_config({"active_provider": "gemini"})

    monkeypatch.setattr(pm_mod, "_probe_gemini_model", lambda _k, model: (False, 0, f"404 {model} not found"))
    caps_gem_bad_live = pm.check_capabilities(
        {"active_provider": "gemini", "providers": {"gemini": {"api_key": "AIzaLiveTestKey", "model": "gemini-3.8-flash"}}},
        live=True,
    )
    assert caps_gem_bad_live["providers"]["gemini"]["selectable"] is False
    assert "404" in caps_gem_bad_live["providers"]["gemini"]["locked_reason"]
    assert caps_gem_bad_live["can_save"] is False

    monkeypatch.setattr(pm_mod, "_probe_gemini_model", lambda _k, _m: (True, 1, None))
    caps_gem_ok = pm.check_capabilities(
        {"active_provider": "gemini", "providers": {"gemini": {"api_key": "AIzaLiveTestKey", "model": "gemini-3.8-flash"}}},
        live=True,
    )
    assert caps_gem_ok["providers"]["gemini"]["selectable"] is True
    assert caps_gem_ok["providers"]["gemini"]["features"]["embedding"] is True
    assert caps_gem_ok["can_save"] is True


def test_server_scope_locks_embedding_selection_and_documents_purpose(tmp_path: Path):
    """Verify server_scope defaults to gemini-embedding-2, allows active Gemini embed models, rejects retired/non-Gemini models, and exposes EOS lifecycle metadata."""
    pm = ProviderManager(data_dir=tmp_path / "data", auto_migrate=False)

    sanitized_default = pm.get_sanitized_config()
    assert sanitized_default["providers"]["gemini"]["embed_model"] == "gemini-embedding-2"
    avail_emb = [m["id"] for m in sanitized_default["providers"]["gemini"]["available_embed_models"]]
    assert "gemini-embedding-2" in avail_emb and "gemini-embedding-001" in avail_emb
    assert "gemini-embedding-2-preview" not in avail_emb

    lifecycle = sanitized_default["lineage_summary"]["embedding_lifecycle"]
    assert lifecycle["active_model"] == "gemini-embedding-2"
    assert lifecycle["output_dimensionality"] == 3072
    assert lifecycle["eos_date"] is None

    # Allow switching between active Gemini embedding models at server scope.
    updated_v1 = pm.update_config({"providers": {"gemini": {"embed_model": "gemini-embedding-001"}}}, server_scope=True)
    assert updated_v1["providers"]["gemini"]["embed_model"] == "gemini-embedding-001"
    assert updated_v1["lineage_summary"]["embedding_lifecycle"]["eos_date"] == "2028-05-14"
    assert "2028-05-14" in updated_v1["lineage_summary"]["embedding_lifecycle"]["eos_warning"]

    with pytest.raises(ValueError):
        pm.update_config({"providers": {"gemini": {"embed_model": "gemini-embedding-2-preview"}}}, server_scope=True)

    with pytest.raises(ValueError):
        pm.update_config({"feature_routing": {"embed": {"provider": "gemini", "model": "text-embedding-004"}}}, server_scope=True)

    with pytest.raises(ValueError):
        pm.update_config({"feature_routing": {"embed": {"provider": "mock", "model": ""}}}, server_scope=True)


def test_gemini_enumerated_effort_models_probe_and_sync(tmp_path: Path, monkeypatch):
    import sys
    from types import SimpleNamespace
    import claire.core.provider_manager as pm_mod

    pm = ProviderManager(data_dir=tmp_path / "data", auto_migrate=False)
    models = pm_mod.list_gemini_models()
    assert "gemini-3.8-flash-medium" in models
    assert "gemini-4-argon-high" in models
    assert "gemini-3.8-flash" not in models

    sanitized = pm.get_sanitized_config()
    avail = [m["id"] for m in sanitized["providers"]["gemini"]["available_models"]]
    assert "gemini-3.8-flash-medium" in avail
    assert "gemini-3.8-flash" not in avail

    updated = pm.update_config(
        {
            "active_provider": "gemini",
            "providers": {"gemini": {"api_key": "AIzaEnumeratedTestKey", "model": "gemini-4-argon-high"}},
            "feature_routing": {
                "summarize": {"provider": "gemini", "model": "gemini-3.8-flash-low"},
                "render_detail": {"provider": "gemini", "model": "gemini-4-argon-high"},
            },
        }
    )
    assert updated["providers"]["gemini"]["model"] == "gemini-4-argon-high"
    assert updated["providers"]["gemini"]["effort"] == "high"
    assert updated["feature_routing"]["summarize"]["effort"] == "low"
    assert updated["feature_routing"]["render_detail"]["model"] == "gemini-4-argon-high"
    assert updated["feature_routing"]["render_detail"]["effort"] == "high"
    assert updated["lineage_summary"]["effective_slots"]["extract_graph"]["effort"] == "high"
    assert updated["lineage_summary"]["effective_slots"]["summarize"]["effort"] == "low"
    assert updated["lineage_summary"]["effective_slots"]["render_detail"]["model"] == "gemini-4-argon-high"
    assert updated["lineage_summary"]["effective_slots"]["render_detail"]["effort"] == "high"

    # Verify FeatureRoutedProvider dispatches summarize_document and render_detail to separate slots.
    from claire.core.config import Settings
    from claire.extract.provider import FeatureRoutedProvider
    from claire.ontology.base import Document

    routed_settings = Settings(
        _env_file=None,
        CLAIRE_PROVIDER="mock",
        CLAIRE_ENVIRONMENT="development",
        feature_routing={
            "summarize": {"provider": "mock", "model": "mock-sum", "effort": "low"},
            "render_detail": {"provider": "mock", "model": "mock-detail", "effort": "high"},
        },
    )
    routed = FeatureRoutedProvider(routed_settings)
    assert routed.slot_spec("summarize")[2] == "low"
    assert routed.slot_spec("render_detail")[2] == "high"
    doc = Document(id="doc-slot", title="Slot Test", raw_text="Sample body for slot dispatch.", source_type="text")
    assert routed.summarize_document(doc)
    assert routed.render_detail(doc)
    stages = [ev["stage"] for ev in routed.lineage.events]
    assert "summarize" in stages and "render_detail" in stages

    seen_models: list[str] = []

    class _FakeClient:
        def __init__(self, api_key: str) -> None:
            self.models = SimpleNamespace(
                count_tokens=lambda *, model, contents: (seen_models.append(model), SimpleNamespace(total_tokens=7))[1]
            )

    monkeypatch.setitem(sys.modules, "google", SimpleNamespace(genai=SimpleNamespace(Client=_FakeClient)))
    ok, total_tokens, err = pm_mod._probe_gemini_model("AIzaEnumeratedTestKey", "gemini-3.8-flash-medium")
    assert ok is True and total_tokens == 7 and err is None
    assert seen_models == ["gemini-3.8-flash"]
