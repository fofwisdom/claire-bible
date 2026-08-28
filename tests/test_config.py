"""Focused tests for security-sensitive environment parsing."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from claire.config import Settings


def test_anonymous_readonly_defaults_enabled(monkeypatch):
    monkeypatch.delenv("CLAIRE_ANONYMOUS_READONLY", raising=False)

    settings = Settings(_env_file=None)

    assert settings.anonymous_readonly is True


@pytest.mark.parametrize(
    ("raw", "expected"),
    (
        ("0", False),
        ("1", True),
    ),
)
def test_anonymous_readonly_parses_exact_environment_values(
    monkeypatch,
    raw,
    expected,
):
    monkeypatch.setenv("CLAIRE_ANONYMOUS_READONLY", raw)

    settings = Settings(_env_file=None)

    assert settings.anonymous_readonly is expected
    assert isinstance(settings.anonymous_readonly, bool)


@pytest.mark.parametrize(
    "raw",
    ("", "true", "false", "yes", "2", "01", " 1", "1 ", "\t0", "0\t"),
)
def test_anonymous_readonly_rejects_noncanonical_environment_values(
    monkeypatch,
    raw,
):
    monkeypatch.setenv("CLAIRE_ANONYMOUS_READONLY", raw)

    with pytest.raises(
        ValidationError,
        match="CLAIRE_ANONYMOUS_READONLY must be exactly 0 or 1",
    ):
        Settings(_env_file=None)


@pytest.mark.parametrize(
    ("raw", "expected"),
    (("0", False), ("1", True)),
)
def test_anonymous_readonly_parses_exact_dotenv_values(
    tmp_path: Path,
    monkeypatch,
    raw,
    expected,
):
    monkeypatch.delenv("CLAIRE_ANONYMOUS_READONLY", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        f"CLAIRE_ANONYMOUS_READONLY={raw}\n",
        encoding="utf-8",
    )

    assert Settings(_env_file=env_file).anonymous_readonly is expected


@pytest.mark.parametrize(
    "raw",
    ('"1"', "'0'", " 1", "1 ", "\t0", "0\t", "1 # public"),
)
def test_anonymous_readonly_rejects_normalized_dotenv_syntax(
    tmp_path: Path,
    monkeypatch,
    raw,
):
    monkeypatch.delenv("CLAIRE_ANONYMOUS_READONLY", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        f"CLAIRE_ANONYMOUS_READONLY={raw}\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="without quotes or outer whitespace"):
        Settings(_env_file=env_file)


def test_github_repository_and_source_base_url_defaults(monkeypatch):
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    monkeypatch.delenv("SOURCE_BASE_URL", raising=False)

    settings = Settings(_env_file=None)

    assert settings.github_repository == "fofwisdom/claire-bible"
    assert settings.effective_github_repository == "fofwisdom/claire-bible"
    assert settings.effective_source_base_url == "https://github.com/fofwisdom/claire-bible"


def test_github_repository_custom_affects_default_source_base_url(monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", "custom-org/custom-repo")
    monkeypatch.delenv("SOURCE_BASE_URL", raising=False)

    settings = Settings(_env_file=None)

    assert settings.github_repository == "custom-org/custom-repo"
    assert settings.effective_github_repository == "custom-org/custom-repo"
    assert settings.effective_source_base_url == "https://github.com/custom-org/custom-repo"


def test_source_base_url_variable_expansion(monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", "myorg/myrepo")
    monkeypatch.setenv("SOURCE_BASE_URL", "https://github.com/$GITHUB_REPOSITORY/")

    settings = Settings(_env_file=None)

    assert settings.effective_source_base_url == "https://github.com/myorg/myrepo"


def test_source_base_url_custom_explicit_override(monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", "myorg/myrepo")
    monkeypatch.setenv("SOURCE_BASE_URL", "https://gitlab.com/custom/source")

    settings = Settings(_env_file=None)

    assert settings.effective_source_base_url == "https://gitlab.com/custom/source"

