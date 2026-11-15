"""Tests for ProviderManager and WebUI provider management."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from claire.api.server import create_app
from claire.config import Settings
from claire.provider_manager import (
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
    assert cfg["providers"]["antigravity"]["bin"] == "custom_agy"

    # Verify .env was commented
    env_content = env_file.read_text(encoding="utf-8")
    assert "# CLAIRE_PROVIDER=gemini" in env_content
    assert "# GEMINI_API_KEY=AIzaSyTestKey999" in env_content
    assert "CB_API_PORT=8765" in env_content


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
        json={"providers": {"antigravity": {"model": "gemini-4-hydrogen"}}},
    )
    assert bad_resp.status_code == 400

    # 5. POST test connection
    test_resp = client.post(
        "/providers/test",
        headers={"Authorization": "Bearer owner-secret-token-123456789012345678"},
        json={"provider": "mock"}
    )
    assert test_resp.status_code == 200
    assert test_resp.json()["ok"] is True

    # 6. POST capability check (owner-only, enforces capability locks)
    anon_check = client.post("/providers/check", json={"active_provider": "mock"})
    assert anon_check.status_code == 404

    check_resp = client.post(
        "/providers/check",
        headers={"Authorization": "Bearer owner-secret-token-123456789012345678"},
        json={"active_provider": "openai"},
    )
    assert check_resp.status_code == 200
    caps = check_resp.json()
    assert caps["can_save"] is False
    assert caps["providers"]["openai"]["selectable"] is False
    assert caps["providers"]["mock"]["selectable"] is True


def test_providers_cli(capsys):
    from claire.cli import build_parser

    parser = build_parser()

    # 1. list
    args = parser.parse_args(["providers", "list"])
    rc = args.func(args)
    assert rc == 0
    captured = capsys.readouterr().out
    assert "Claire Bible Providers" in captured

    # 2. test mock
    args = parser.parse_args(["providers", "test", "mock"])
    rc = args.func(args)
    assert rc == 0
    captured = capsys.readouterr().out
    assert "Connection or detection succeeded" in captured

    # 3. migrate
    args = parser.parse_args(["providers", "migrate"])
    rc = args.func(args)
    assert rc == 0


def test_agy_models_parsing_and_effort_inference(tmp_path: Path, monkeypatch):
    from types import SimpleNamespace
    import claire.provider_manager as pm_mod
    from claire.provider_manager import _parse_agy_models_output

    raw = (
        "gemini-4-hydrogen-high\tGemini 4 Hydrogen (High)\n"
        "gemini-4-hydrogen-xhigh\tGemini 4 Hydrogen (XHigh)\n"
        "gemini-4-hydrogen-medium\tGemini 4 Hydrogen (Medium)\n"
        "gemini-4-hydrogen-low\tGemini 4 Hydrogen (Low)\n"
        "gemini-3.8-flash-medium\tGemini 3.8 Flash (Medium)\n"
        "gemini-pro-orchestrator-g1-pro\tOrchestrator (Argon)\n"
    )
    parsed = _parse_agy_models_output(raw)
    assert [m["id"] for m in parsed] == [
        "gemini-4-hydrogen-high",
        "gemini-4-hydrogen-xhigh",
        "gemini-4-hydrogen-medium",
        "gemini-4-hydrogen-low",
        "gemini-3.8-flash-medium",
        "gemini-pro-orchestrator-g1-pro",
    ]

    pm_mod._AGY_MODELS_CACHE.clear()
    pm_mod._AGY_VERSION_CACHE.clear()
    monkeypatch.setattr(pm_mod, "is_running_in_container", lambda: False)
    monkeypatch.setattr(pm_mod, "find_agy_executable", lambda _bin="agy": "/host-bin/agy")
    monkeypatch.setattr(pm_mod, "diagnose_agy_environment", lambda _bin="agy": {"found_path": "/host-bin/agy", "is_executable": True})

    def fake_run(cmd, **_kwargs):
        if cmd[1:] == ["--version"]:
            return SimpleNamespace(returncode=0, stdout="1.2.0\n", stderr="")
        if cmd[1:] == ["models"]:
            return SimpleNamespace(returncode=0, stdout=raw, stderr="")
        return SimpleNamespace(returncode=1, stdout="", stderr="unexpected")

    monkeypatch.setattr(pm_mod.subprocess, "run", fake_run)

    pm = ProviderManager(data_dir=tmp_path / "data", auto_migrate=False)
    updated = pm.update_config({
        "active_provider": "antigravity",
        "providers": {
            "antigravity": {
                "bin": "agy",
                "model": "gemini-4-hydrogen-xhigh",
            }
        },
    })
    assert updated["providers"]["antigravity"]["model"] == "gemini-4-hydrogen-xhigh"
    assert updated["providers"]["antigravity"]["effort"] == "xhigh"
    assert isinstance(updated["providers"]["antigravity"]["available_models"], list)
    assert any(m["id"] == "gemini-4-hydrogen-xhigh" for m in updated["providers"]["antigravity"]["available_models"])
    assert any(m["id"] == "gemini-3.8-flash" for m in updated["providers"]["gemini"]["available_models"])
    assert any(m["id"] == "gemini-embedding-001" for m in updated["providers"]["gemini"]["available_embed_models"])
    assert any(m["id"] == "gpt-5.4" for m in updated["providers"]["codex"]["available_models"])
    assert any(m["id"] == "gpt-4o-mini" for m in updated["providers"]["openai"]["available_models"])

    with pytest.raises(ValueError):
        pm.update_config({"providers": {"gemini": {"model": "unsupported-gemini-model"}}})
    with pytest.raises(ValueError):
        pm.update_config({"providers": {"antigravity": {"model": "gemini-4-hydrogen"}}})
    with pytest.raises(ValueError):
        pm.update_config({"providers": {"codex": {"model": "unsupported-codex-model"}}})
    with pytest.raises(ValueError):
        pm.update_config({"providers": {"openai": {"model": "unsupported-openai-model"}}})

    pm_mod._AGY_MODELS_CACHE.clear()
    pm_mod._AGY_VERSION_CACHE.clear()


def test_agy_unavailable_locks_activation_without_fallback(tmp_path: Path, monkeypatch):
    from types import SimpleNamespace
    import claire.provider_manager as pm_mod

    pm_mod._AGY_MODELS_CACHE.clear()
    pm_mod._AGY_VERSION_CACHE.clear()
    monkeypatch.setattr(pm_mod, "is_running_in_container", lambda: False)
    monkeypatch.setattr(pm_mod, "find_agy_executable", lambda _bin="agy": "/host-bin/agy")
    monkeypatch.setattr(pm_mod, "diagnose_agy_environment", lambda _bin="agy": {"found_path": "/host-bin/agy", "is_executable": True})

    def fake_run(cmd, **_kwargs):
        if cmd[1:] == ["--version"]:
            return SimpleNamespace(returncode=0, stdout="1.1.14\n", stderr="")
        return SimpleNamespace(returncode=1, stdout="", stderr="unknown command: models\n")

    monkeypatch.setattr(pm_mod.subprocess, "run", fake_run)

    disc = pm_mod.discover_agy_models("agy", force_refresh=True)
    assert disc["source"] == "unavailable"
    assert disc["cli_version"] == "1.1.14"
    assert disc["models"] == []

    pm = ProviderManager(data_dir=tmp_path / "data", auto_migrate=False)
    sanitized = pm.get_sanitized_config()
    assert sanitized["providers"]["antigravity"]["models_source"] == "unavailable"
    assert sanitized["providers"]["antigravity"]["cli_version"] == "1.1.14"
    assert sanitized["providers"]["antigravity"]["available_models"] == []
    assert sanitized["capabilities"]["providers"]["antigravity"]["selectable"] is False

    # No fallback: neither an invalid model nor activating antigravity is allowed
    with pytest.raises(ValueError):
        pm.update_config({"providers": {"antigravity": {"model": "gemini-4-hydrogen-medium"}}})
    with pytest.raises(ValueError):
        pm.update_config({"active_provider": "antigravity", "providers": {"antigravity": {"model": "gemini-3.8-flash-medium"}}})

    caps = pm.check_capabilities({"active_provider": "antigravity"})
    assert caps["can_save"] is False
    assert caps["lock_reasons"]

    conn = pm.test_connection("antigravity")
    assert conn["ok"] is False
    assert "v1.1.14" in conn["error"]
    assert conn["details"]["models_source"] == "unavailable"

    pm_mod._AGY_MODELS_CACHE.clear()
    pm_mod._AGY_VERSION_CACHE.clear()


def test_provider_capability_checks_and_locks(tmp_path: Path, monkeypatch):
    import claire.provider_manager as pm_mod

    pm = ProviderManager(data_dir=tmp_path / "data", auto_migrate=False)

    # 1. OpenAI backend is always locked until implemented
    caps_oai = pm.check_capabilities({"active_provider": "openai"})
    assert caps_oai["providers"]["openai"]["selectable"] is False
    assert caps_oai["can_save"] is False
    with pytest.raises(ValueError):
        pm.update_config({"active_provider": "openai"})

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

    # 3. Codex CLI is locked inside containers
    monkeypatch.setattr(pm_mod, "is_running_in_container", lambda: True)
    caps_cdx_docker = pm.check_capabilities({"active_provider": "codex"})
    assert caps_cdx_docker["providers"]["codex"]["selectable"] is False
    assert caps_cdx_docker["can_save"] is False
    with pytest.raises(ValueError):
        pm.update_config({"active_provider": "codex"})
