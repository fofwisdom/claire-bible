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
        "active_provider": "antigravity",
        "providers": {
            "gemini": {
                "api_key": "••••••••",
                "model": "gemini-2.5-pro",
            },
        },
    })
    assert updated["active_provider"] == "antigravity"

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
                    "model": "gemini-3.7-flash",
                }
            }
        }
    )
    assert patch_resp.status_code == 200
    res_json = patch_resp.json()
    assert res_json["ok"] is True
    assert res_json["config"]["active_provider"] == "gemini"
    assert res_json["config"]["providers"]["gemini"]["model"] == "gemini-3.7-flash"
    assert res_json["config"]["providers"]["gemini"]["has_api_key"] is True
    assert res_json["config"]["providers"]["gemini"]["api_key"] == "••••••••"

    # 5. POST test connection
    test_resp = client.post(
        "/providers/test",
        headers={"Authorization": "Bearer owner-secret-token-123456789012345678"},
        json={"provider": "mock"}
    )
    assert test_resp.status_code == 200
    assert test_resp.json()["ok"] is True


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
    assert "연결/감지 성공" in captured

    # 3. migrate
    args = parser.parse_args(["providers", "migrate"])
    rc = args.func(args)
    assert rc == 0
