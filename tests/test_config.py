"""Focused tests for security-sensitive environment parsing."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from claire.core.config import Settings, resolve_app_root


def test_app_root_uses_source_checkout_when_pyproject_exists(tmp_path):
    module = tmp_path / "src/claire/core/config.py"
    (tmp_path / "pyproject.toml").write_text("[project]\nname='test'\n")

    assert resolve_app_root(
        module,
        configured_root="",
        working_directory="/unexpected",
    ) == tmp_path.resolve()


def test_app_root_uses_explicit_container_path_for_installed_wheel(tmp_path):
    installed_module = (
        tmp_path
        / ".venv/lib/python3.15/site-packages/claire/core/config.py"
    )

    assert resolve_app_root(
        installed_module,
        configured_root="/app",
        working_directory="/unexpected",
    ) == Path("/app")


def test_app_root_does_not_treat_site_packages_parent_as_project_root(tmp_path):
    installed_module = (
        tmp_path
        / ".venv/lib/python3.15/site-packages/claire/core/config.py"
    )
    runtime_cwd = tmp_path / "runtime"

    assert resolve_app_root(
        installed_module,
        configured_root="",
        working_directory=runtime_cwd,
    ) == runtime_cwd.resolve()


@pytest.mark.parametrize("configured", ("app", " /app", "/app ", "/app\x00bad"))
def test_app_root_rejects_ambiguous_explicit_paths(configured):
    with pytest.raises(ValueError, match="CLAIRE_APP_ROOT"):
        resolve_app_root(configured_root=configured)


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


def test_sorcerer_defaults(monkeypatch):
    monkeypatch.delenv("CLAIRE_SORCERER", raising=False)

    settings = Settings(_env_file=None)

    assert settings.sorcerer == "owner"
    assert settings.effective_sorcerer == "owner"
    assert settings.owner == "owner"
    assert settings.effective_owner == "owner"
    assert settings.knowledge_manager == "owner"
    assert settings.effective_knowledge_manager == "owner"


def test_sorcerer_custom_env(monkeypatch):
    monkeypatch.setenv("CLAIRE_SORCERER", "merlin")
    s1 = Settings(_env_file=None)
    assert s1.sorcerer == "merlin"
    assert s1.effective_sorcerer == "merlin"
    assert s1.owner == "merlin"
    assert s1.effective_owner == "merlin"

    monkeypatch.setenv("CLAIRE_SORCERER", "   ")
    s2 = Settings(_env_file=None)
    assert s2.effective_sorcerer == "owner"


def test_slicing_config_defaults(monkeypatch, tmp_path):
    monkeypatch.delenv("CLAIRE_RAW_CHAR_BUDGET", raising=False)
    monkeypatch.delenv("CLAIRE_PDF_MAX_EXTRACT_CHARS", raising=False)
    monkeypatch.delenv("CLAIRE_PRESENTATION_PDF_MAX_BYTES", raising=False)
    monkeypatch.delenv("CLAIRE_EXTRACT_CHAR_BUDGET", raising=False)
    monkeypatch.delenv("CLAIRE_MERGED_EXTRACT_CHAR_BUDGET", raising=False)
    monkeypatch.delenv("CLAIRE_SLICING_STRATEGY", raising=False)
    monkeypatch.delenv("CLAIRE_EMBED_CHAR_BUDGET", raising=False)
    monkeypatch.delenv("CLAIRE_EXPAND_CHAR_BUDGET", raising=False)
    monkeypatch.delenv("CLAIRE_RESEARCH_CONTEXT_BUDGET", raising=False)
    monkeypatch.setenv("CB_DATA_DIR", str(tmp_path))

    settings = Settings(_env_file=None)

    assert settings.raw_char_budget == 20000
    assert settings.pdf_max_extract_chars == 50000
    assert settings.presentation_pdf_max_bytes == 64 * 1024 * 1024
    assert settings.extract_char_budget == 20000
    assert settings.merged_extract_char_budget == 0
    assert settings.effective_merged_extract_char_budget == 40000
    assert settings.slicing_strategy == "table-exemption"
    assert settings.embed_char_budget == 8000
    assert settings.expand_char_budget == 2000
    assert settings.research_context_budget == 8000


def test_slicing_config_custom_values(monkeypatch, tmp_path):
    # The limit fields come from data/ingest_limits.json, which wins over the environment. A constructor argument wins over the file.
    monkeypatch.setenv("CB_DATA_DIR", str(tmp_path))
    settings = Settings(
        _env_file=None,
        CLAIRE_RAW_CHAR_BUDGET=15000,
        CLAIRE_PDF_MAX_EXTRACT_CHARS=80000,
        CLAIRE_PRESENTATION_PDF_MAX_BYTES=1048576,
        CLAIRE_EXTRACT_CHAR_BUDGET=10000,
        CLAIRE_MERGED_EXTRACT_CHAR_BUDGET=25000,
        CLAIRE_SLICING_STRATEGY="strict",
        CLAIRE_EMBED_CHAR_BUDGET=4000,
        CLAIRE_EXPAND_CHAR_BUDGET=1500,
        CLAIRE_RESEARCH_CONTEXT_BUDGET=5000,
    )

    assert settings.raw_char_budget == 15000
    assert settings.pdf_max_extract_chars == 80000
    assert settings.presentation_pdf_max_bytes == 1048576
    assert settings.extract_char_budget == 10000
    assert settings.merged_extract_char_budget == 25000
    assert settings.effective_merged_extract_char_budget == 25000
    assert settings.slicing_strategy == "strict"
    assert settings.embed_char_budget == 4000
    assert settings.expand_char_budget == 1500
    assert settings.research_context_budget == 5000


def test_ingest_limits_file_wins_over_exported_variable(monkeypatch, tmp_path):
    from claire.core.ingest_limit_manager import IngestLimitManager

    monkeypatch.setenv("CB_DATA_DIR", str(tmp_path))
    IngestLimitManager(data_dir=tmp_path, auto_migrate=False).update_config({"limits": {"raw_char_budget": 111, "auto_expand": False}})
    monkeypatch.setenv("CLAIRE_RAW_CHAR_BUDGET", "777")
    monkeypatch.setenv("CLAIRE_AUTO_EXPAND", "1")

    settings = Settings(_env_file=None)

    assert settings.raw_char_budget == 111
    assert settings.auto_expand is False


def test_slicing_strategy_invalid(monkeypatch, tmp_path):
    monkeypatch.setenv("CB_DATA_DIR", str(tmp_path))

    with pytest.raises(ValidationError, match="CLAIRE_SLICING_STRATEGY must be 'table-exemption' or 'strict'"):
        Settings(_env_file=None, CLAIRE_SLICING_STRATEGY="invalid_strategy")


def test_presentation_pdf_max_bytes_must_be_positive(monkeypatch, tmp_path):
    monkeypatch.setenv("CB_DATA_DIR", str(tmp_path))

    with pytest.raises(ValidationError, match="greater than 0"):
        Settings(_env_file=None, CLAIRE_PRESENTATION_PDF_MAX_BYTES=0)


def test_old_host_cli_variables_are_ignored(monkeypatch, tmp_path: Path):
    """An old .env or environment can still set the variables of the retired host-cli providers. The settings load and have no field for them."""
    old = {
        "CLAIRE_AGY_BIN": "agy",
        "CLAIRE_AGY_EFFORT": "high",
        "CLAIRE_AGY_TIMEOUT": "180",
        "CLAIRE_AGY_DETAIL_TIMEOUT": "300",
        "CLAIRE_AGY_MAX_CONCURRENCY": "2",
        "CLAIRE_CODEX_BIN": "codex",
        "CLAIRE_CODEX_EFFORT": "medium",
        "CLAIRE_CODEX_TIMEOUT": "300",
        "CLAIRE_CODEX_MAX_CONCURRENCY": "1",
    }
    for name, value in old.items():
        monkeypatch.setenv(name, value)
    dotenv = tmp_path / ".env"
    dotenv.write_text("".join(f"{name}={value}\n" for name, value in old.items()), encoding="utf-8")

    settings = Settings(_env_file=dotenv)

    assert not [name for name in Settings.model_fields if name.startswith(("agy_", "codex_"))]
    assert not hasattr(settings, "agy_bin") and not hasattr(settings, "codex_bin")


def test_preferred_languages_come_from_the_database_and_not_from_the_environment(monkeypatch, tmp_path):
    """ACCOUNT_PREFERRED_LANGUAGES_DESIGN.md P3: the server default language of claire.db; the old variable has no effect."""
    from claire.accounts import languages
    from claire.store import db as dbm

    monkeypatch.delenv("CLAIRE_PREFERRED_LANGUAGES", raising=False)
    db_file = tmp_path / "claire.db"
    conn = dbm.connect(db_file)
    try:
        dbm.init_db(conn)
        languages.set_server_languages(conn, ["ko", "ja", "zh"], updated_by=1)
    finally:
        conn.close()
    monkeypatch.setenv("CLAIRE_PREFERRED_LANGUAGES", "fr")
    monkeypatch.setenv("PREFERRED_LANGUAGES", "fr")
    settings = Settings(_env_file=None, CLAIRE_DB_PATH=str(db_file))
    assert settings.effective_preferred_languages == ["ko", "ja", "zh", "en"]
    assert settings.effective_youtube_languages == ["ko", "ja", "zh", "en"]
    # The list of a job (settings_for_context) keeps the place of en.
    assert Settings(_env_file=None, CLAIRE_DB_PATH=str(db_file), preferred_languages="en,fr").effective_preferred_languages == ["en", "fr"]
    # No database: English only.
    assert Settings(_env_file=None, CLAIRE_DB_PATH=str(tmp_path / "none.db")).effective_preferred_languages == ["en"]


def test_model_fields_have_no_environment_variable(monkeypatch, tmp_path):
    """The Web UI (providers.json) sets the models. An environment variable and a ``.env`` line do not."""
    from claire.core.config import MODEL_FIELDS
    from claire.core.provider_manager import DEFAULT_PROVIDERS_CONFIG, ProviderManager

    # A providers.json without models: no value of the file hides an environment value.
    config = json.loads(json.dumps(DEFAULT_PROVIDERS_CONFIG))
    for name, keys in (("gemini", ("model", "embed_model")), ("jev", ("model",)), ("stt", ("model",))):
        for key in keys:
            config["providers"][name].pop(key, None)
    (tmp_path / "providers.json").write_text(json.dumps(config), encoding="utf-8")
    for field in MODEL_FIELDS:
        monkeypatch.setenv(f"CLAIRE_{field.upper()}", "from-env")
        monkeypatch.setenv(field.upper(), "from-env")
    dotenv = tmp_path / ".env"
    dotenv.write_text("CLAIRE_GEMINI_MODEL=from-dotenv\n", encoding="utf-8")
    settings = Settings(_env_file=dotenv, data_dir=tmp_path)
    assert {field: getattr(settings, field) for field in MODEL_FIELDS} == {field: Settings.model_fields[field].default for field in MODEL_FIELDS}
    # The field defaults are the defaults of providers.json.
    manager = ProviderManager.__new__(ProviderManager)
    manager.get_config = lambda: json.loads(json.dumps(DEFAULT_PROVIDERS_CONFIG))
    effective = ProviderManager.get_settings_dict(manager)
    assert all(Settings.model_fields[field].default == effective.get(field, Settings.model_fields[field].default) for field in MODEL_FIELDS)
