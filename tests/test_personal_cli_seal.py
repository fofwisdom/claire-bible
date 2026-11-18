"""The personal CLI clients of the administrator are sealed.

No entry point reads the ``personal_cli`` block of ``providers.json``. The block stays in the file as data. The provider routing of the administrator uses only the server providers and the server feature routing.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import claire.provider_manager as pm_mod
from claire.accounts.provider_policy import admin_context
from claire.config import Settings
from claire.extract.provider import _has_feature_routing_override, _resolve_slot_spec
from claire.provider_manager import ProviderManager

PERSONAL_CLI_BLOCK = {
    "enabled": True,
    "active_cli": "codex",
    "feature_routing": {"extract_graph": {"provider": "antigravity", "model": "gemini-3-flash-preview", "effort": "high"}},
}


@pytest.fixture()
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    # The test must not start a host CLI.
    monkeypatch.setattr(pm_mod, "find_agy_executable", lambda _bin="agy": None)
    monkeypatch.setattr(pm_mod, "find_codex_executable", lambda _bin="codex": None)
    out = tmp_path / "data"
    out.mkdir()
    (out / "providers.json").write_text(json.dumps({"version": 1, "active_provider": "mock", "personal_cli": PERSONAL_CLI_BLOCK}), encoding="utf-8")
    return out


def test_provider_manager_keeps_the_block_as_data_and_does_not_give_it(data_dir: Path) -> None:
    pm = ProviderManager(data_dir=data_dir, auto_migrate=False)
    assert "personal_cli" not in pm.get_settings_dict()
    assert "personal_cli" not in pm.get_sanitized_config()
    assert not hasattr(pm, "get_personal_cli_config")
    assert not hasattr(pm, "update_personal_cli_config")

    pm.update_config({"active_provider": "mock", "feature_routing": {"summarize": {"provider": "mock"}}}, server_scope=True)
    stored = json.loads((data_dir / "providers.json").read_text(encoding="utf-8"))
    assert stored["personal_cli"] == PERSONAL_CLI_BLOCK
    assert stored["feature_routing"]["summarize"]["provider"] == "mock"


def test_a_new_providers_file_has_no_personal_cli_block(tmp_path: Path) -> None:
    pm = ProviderManager(data_dir=tmp_path / "fresh", auto_migrate=False)
    pm.update_config({"active_provider": "mock"}, server_scope=True)
    stored = json.loads((tmp_path / "fresh" / "providers.json").read_text(encoding="utf-8"))
    assert "personal_cli" not in stored


def test_settings_from_providers_file_route_admin_jobs_to_the_server_provider(data_dir: Path) -> None:
    settings = Settings(_env_file=None, db_path=str(data_dir / "claire.db"), environment="development")
    assert "personal_cli" not in settings.model_dump()
    assert admin_context(settings).provider == settings.provider
    assert settings.provider not in ("antigravity", "codex")
    for slot in ("extract_graph", "summarize", "render_detail"):
        assert _resolve_slot_spec(settings, slot)[0] not in ("antigravity", "codex")


def test_routing_ignores_a_personal_cli_attribute() -> None:
    settings = SimpleNamespace(provider="gemini", effective_provider="gemini", feature_routing={}, personal_cli=PERSONAL_CLI_BLOCK, gemini_model="gemini-3.1-flash-lite-medium", gemini_effort="medium")
    assert admin_context(settings).provider == "gemini"
    assert _resolve_slot_spec(settings, "extract_graph")[0] == "gemini"
    assert _resolve_slot_spec(settings, "summarize")[0] == "gemini"
    assert _has_feature_routing_override(settings) is False
