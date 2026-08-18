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
