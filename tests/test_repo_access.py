"""Tests for repository access across CLI, status reporting, and Telegram bot."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from claire.cli import cmd_preflight, cmd_repo
from claire.config import Settings, get_settings
from claire.status import build_status_text


def test_status_output_contains_repo_info():
    settings = Settings(
        GITHUB_REPOSITORY="fofwisdom/claire-bible",
        SOURCE_BASE_URL="https://github.com/fofwisdom/claire-bible",
        _env_file=None,
    )
    status_text = build_status_text(settings, full=False)
    assert "repo     : fofwisdom/claire-bible (https://github.com/fofwisdom/claire-bible)" in status_text


def test_cli_repo_command_prints_info(capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", "testorg/testrepo")
    monkeypatch.setenv("SOURCE_BASE_URL", "https://github.com/testorg/testrepo")
    get_settings.cache_clear()
    try:
        rc = cmd_repo(SimpleNamespace())
        assert rc == 0

        out = capsys.readouterr().out
        assert "Repository : testorg/testrepo" in out
        assert "Source URL : https://github.com/testorg/testrepo" in out
    finally:
        get_settings.cache_clear()


def test_cli_preflight_prints_repo_info(capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", "testorg/testrepo")
    monkeypatch.delenv("SOURCE_BASE_URL", raising=False)
    get_settings.cache_clear()
    try:
        rc = cmd_preflight(SimpleNamespace())
        assert rc == 0

        out = capsys.readouterr().out
        assert "github repository : testorg/testrepo" in out
        assert "source base url   : https://github.com/testorg/testrepo" in out
    finally:
        get_settings.cache_clear()
