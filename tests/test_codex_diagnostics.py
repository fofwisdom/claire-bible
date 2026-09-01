"""Codex provider의 status/doctor 진단 출력 계약."""

from __future__ import annotations

from types import SimpleNamespace

from claire.cli import cmd_doctor, cmd_preflight
from claire.config import Settings
from claire.extract.codex_provider import probe_codex_cli
from claire.status import build_status_text


def _settings(tmp_path, **overrides) -> Settings:
    values = {
        "provider": "codex",
        "codex_bin": "/opt/bin/codex",
        "codex_model": "",
        "codex_effort": "medium",
        "gemini_api_key": "",
        "db_path": str(tmp_path / "claire.db"),
        "vault_path": str(tmp_path / "vault"),
        "_env_file": None,
    }
    values.update(overrides)
    return Settings(**values)


def test_probe_codex_cli_discards_account_identity(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        if args[-1] == "--version":
            return SimpleNamespace(
                returncode=0, stdout="codex-cli 0.151.0\n", stderr=""
            )
        return SimpleNamespace(
            returncode=0,
            stdout="Logged in as private-account@example.com\n",
            stderr="",
        )

    monkeypatch.setattr(
        "claire.config.find_codex_executable", lambda _raw: "/opt/bin/codex"
    )
    monkeypatch.setattr("claire.extract.codex_provider.subprocess.run", fake_run)

    result = probe_codex_cli(settings)

    assert result == {
        "binary": "/opt/bin/codex",
        "version": "codex-cli 0.151.0",
        "login": "authenticated",
    }
    assert "private-account@example.com" not in str(result)
    assert calls[1][0] == ["/opt/bin/codex", "login", "status"]


def test_status_displays_codex_runtime_without_identity(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    monkeypatch.setattr(
        "claire.config.find_codex_executable", lambda _raw: "/opt/bin/codex"
    )
    monkeypatch.setattr(
        "claire.extract.codex_provider.probe_codex_cli",
        lambda _settings: {
            "binary": "/opt/bin/codex",
            "version": "codex-cli 0.151.0",
            "login": "authenticated",
        },
    )

    text = build_status_text(settings, full=True)

    assert "provider : codex" in text
    assert "model=codex-cli-default" in text
    assert "effort=medium" in text
    assert "/opt/bin/codex · codex-cli 0.151.0 · login=authenticated" in text
    assert "embedding: unavailable · search=fts-only" in text


def test_preflight_and_doctor_display_codex_runtime(monkeypatch, tmp_path, capsys):
    settings = _settings(tmp_path, gemini_api_key="gemini-key")
    probe = {
        "binary": "/opt/bin/codex",
        "version": "codex-cli 0.151.0",
        "login": "authenticated",
    }
    monkeypatch.setattr("claire.cli.get_settings", lambda: settings)
    monkeypatch.setattr("claire.cli.probe_sqlite_vec", lambda: (False, "not loaded"))
    monkeypatch.setattr(
        "claire.extract.codex_provider.probe_codex_cli", lambda _settings: probe
    )

    assert cmd_preflight(SimpleNamespace()) == 0
    preflight = capsys.readouterr().out
    assert "codex binary      : /opt/bin/codex" in preflight
    assert "codex version     : codex-cli 0.151.0" in preflight
    assert "codex login       : authenticated" in preflight
    assert "codex-cli-default (effort=medium)" in preflight
    assert "codex embedding   : gemini" in preflight

    args = SimpleNamespace(heal=False, apply=False, repair=False, json=False)
    assert cmd_doctor(args) == 0
    doctor = capsys.readouterr().out
    assert "Codex: binary=/opt/bin/codex" in doctor
    assert "login=authenticated" in doctor
    assert "Codex model=codex-cli-default · effort=medium" in doctor
    assert "Codex embedding=gemini" in doctor
