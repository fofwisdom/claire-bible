"""cb-manuscript orchestration tests; no Docker, Git, or network is used."""

from __future__ import annotations

import stat
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from claire import cli as claire_cli
from ops import cb_manuscript as cb


@pytest.fixture(autouse=True)
def _clear_environment_selector(monkeypatch):
    monkeypatch.delenv("CLAIRE_ENVIRONMENT", raising=False)


def _write_layout(
    root: Path,
    *,
    dev: bool = True,
    token: str = "",
    owner_token: str = "owner-" + ("x" * 32),
    readonly_token: str = "",
) -> None:
    (root / ".env.example").write_text(
        "\n".join(
            (
                "CLAIRE_ENVIRONMENT=production",
                "CB_PROJECT_NAME=claire-bible",
                "CB_WAIT_TIMEOUT=45",
                "CB_API_BIND=127.0.0.1",
                "CB_API_PORT=8765",
                "CLAIRE_PUBLIC_URL=https://claire.example.com/",
                "CLAIRE_CORS_ALLOWED_ORIGINS=",
                "CLAIRE_ANONYMOUS_READONLY=1",
                f"CLAIRE_READONLY_TOKEN={readonly_token}",
                f"CLAIRE_INJECT_TOKEN={owner_token}",
                "GEMINI_API_KEY=",
                f"TELEGRAM_BOT_TOKEN={token}",
                "",
            )
        ),
        encoding="utf-8",
    )
    (root / ".env.dev.example").write_text(
        "\n".join(
            (
                "CLAIRE_ENVIRONMENT=development",
                "CB_PROJECT_NAME=claire-bible-dev",
                "CB_WAIT_TIMEOUT=15",
                "CB_API_BIND=127.0.0.1",
                "CB_API_PORT=8766",
                "CLAIRE_PUBLIC_URL=http://127.0.0.1:8766/",
                "CLAIRE_CORS_ALLOWED_ORIGINS=",
                "CLAIRE_ANONYMOUS_READONLY=1",
                f"CLAIRE_READONLY_TOKEN={readonly_token}",
                f"CLAIRE_INJECT_TOKEN={owner_token}",
                f"TELEGRAM_BOT_TOKEN={token}",
                "",
            )
        ),
        encoding="utf-8",
    )
    (root / ".env").write_text(
        (root / ".env.example").read_text(encoding="utf-8"), encoding="utf-8"
    )
    if dev:
        (root / ".env.dev").write_text(
            (root / ".env.dev.example").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
    (root / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    if dev:
        (root / "docker-compose.dev.yml").write_text(
            "services: {}\n", encoding="utf-8"
        )
    (root / "deploy.sh").write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    (root / "deploy.sh").chmod(0o755)


def _completed(
    argv: list[str] | tuple[str, ...],
    returncode: int = 0,
    *,
    stdout: str = "",
    stderr: str = "",
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(argv, returncode, stdout, stderr)


def _fake_success(argv, **_kwargs):
    return _completed(argv)


def _commands(mock_run) -> list[list[str]]:
    return [call.args[0] for call in mock_run.call_args_list]


def test_app_policy_classifies_every_claire_command():
    parser = claire_cli.build_parser()
    command_action = next(
        action for action in parser._actions if action.dest == "cmd"
    )
    assert set(command_action.choices) == (
        cb.APP_ONE_OFF_COMMANDS | set(cb.APP_GUARDED_COMMANDS)
    )


def test_init_is_atomic_idempotent_and_does_not_replace_user_secrets(tmp_path):
    _write_layout(tmp_path, owner_token="")
    (tmp_path / ".env").unlink()
    (tmp_path / ".env.dev").unlink()

    prod_token = "prod-" + ("p" * 32)
    dev_token = "dev-" + ("d" * 32)
    with patch.object(cb.secrets, "token_urlsafe", side_effect=[prod_token, dev_token]):
        assert cb.main(["init"], root=tmp_path) == 0

    prod = (tmp_path / ".env").read_text(encoding="utf-8")
    dev = (tmp_path / ".env.dev").read_text(encoding="utf-8")
    assert f"CLAIRE_INJECT_TOKEN={prod_token}" in prod
    assert f"CLAIRE_INJECT_TOKEN={dev_token}" in dev
    assert "GEMINI_API_KEY=" in prod
    assert "TELEGRAM_BOT_TOKEN=" in prod
    assert stat.S_IMODE((tmp_path / ".env").stat().st_mode) == 0o600
    assert stat.S_IMODE((tmp_path / ".env.dev").stat().st_mode) == 0o600
    assert (tmp_path / "data").is_dir()
    assert (tmp_path / "vault").is_dir()

    user_token = "user-" + ("u" * 32)
    prod = prod.replace(
        f"CLAIRE_INJECT_TOKEN={prod_token}",
        f"CLAIRE_INJECT_TOKEN={user_token}",
    )
    (tmp_path / ".env").write_text(prod, encoding="utf-8")
    with patch.object(cb.secrets, "token_urlsafe") as generate:
        assert cb.main(["init"], root=tmp_path) == 0
    generate.assert_not_called()
    assert f"CLAIRE_INJECT_TOKEN={user_token}" in (
        tmp_path / ".env"
    ).read_text(encoding="utf-8")


def test_init_migrates_missing_environment_selectors_without_replacing_secrets(
    tmp_path,
):
    _write_layout(tmp_path, owner_token="")
    prod_path = tmp_path / ".env"
    dev_path = tmp_path / ".env.dev"
    prod = prod_path.read_text(encoding="utf-8").replace(
        "CLAIRE_ENVIRONMENT=production\n",
        "",
    )
    prod = prod.replace(
        "CLAIRE_INJECT_TOKEN=",
        "CLAIRE_INJECT_TOKEN=existing-prod-" + ("p" * 32),
    )
    dev = dev_path.read_text(encoding="utf-8").replace(
        "CLAIRE_ENVIRONMENT=development\n",
        "",
    )
    dev = dev.replace(
        "CLAIRE_INJECT_TOKEN=\n",
        "CLAIRE_INJECT_TOKEN=existing-dev-" + ("d" * 32) + "\n",
    )
    prod_path.write_text(prod, encoding="utf-8")
    dev_path.write_text(dev, encoding="utf-8")

    with patch.object(cb.secrets, "token_urlsafe") as generate:
        assert cb.main(["init"], root=tmp_path) == 0

    generate.assert_not_called()
    migrated_prod = prod_path.read_text(encoding="utf-8")
    migrated_dev = dev_path.read_text(encoding="utf-8")
    assert migrated_prod.count("CLAIRE_ENVIRONMENT=production") == 1
    assert migrated_dev.count("CLAIRE_ENVIRONMENT=development") == 1
    assert "CLAIRE_INJECT_TOKEN=existing-prod-" + ("p" * 32) in migrated_prod
    assert "CLAIRE_INJECT_TOKEN=existing-dev-" + ("d" * 32) in migrated_dev


def test_init_fills_missing_anonymous_setting_and_preserves_explicit_value(
    tmp_path,
):
    _write_layout(tmp_path)
    prod_path = tmp_path / ".env"
    dev_path = tmp_path / ".env.dev"
    prod_path.write_text(
        prod_path.read_text(encoding="utf-8").replace(
            "CLAIRE_ANONYMOUS_READONLY=1\n",
            "",
        ),
        encoding="utf-8",
    )
    dev_path.write_text(
        dev_path.read_text(encoding="utf-8").replace(
            "CLAIRE_ANONYMOUS_READONLY=1",
            "CLAIRE_ANONYMOUS_READONLY=0",
        ),
        encoding="utf-8",
    )

    assert cb.main(["init"], root=tmp_path) == 0
    assert cb.main(["init"], root=tmp_path) == 0

    prod = prod_path.read_text(encoding="utf-8")
    dev = dev_path.read_text(encoding="utf-8")
    assert prod.count("CLAIRE_ANONYMOUS_READONLY=1") == 1
    assert "CLAIRE_ANONYMOUS_READONLY=0" in dev
    assert dev.count("CLAIRE_ANONYMOUS_READONLY=") == 1


def test_init_preserves_blank_readonly_token_by_default_and_allows_explicit_token(
    tmp_path,
):
    _write_layout(tmp_path)
    assert cb.main(["init"], root=tmp_path) == 0

    prod_text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "CLAIRE_READONLY_TOKEN=" in prod_text
    assert "CLAIRE_READONLY_TOKEN=\n" in prod_text

    explicit_readonly = "readonly-" + ("r" * 32)
    (tmp_path / ".env").write_text(
        prod_text.replace(
            "CLAIRE_READONLY_TOKEN=",
            f"CLAIRE_READONLY_TOKEN={explicit_readonly}",
        ),
        encoding="utf-8",
    )
    assert cb.main(["init"], root=tmp_path) == 0
    assert f"CLAIRE_READONLY_TOKEN={explicit_readonly}" in (
        tmp_path / ".env"
    ).read_text(encoding="utf-8")


def test_detect_system_timezone_prefers_env_and_detects_system(monkeypatch):
    monkeypatch.setenv("TZ", "Asia/Tokyo")
    assert cb._detect_system_timezone() == "Asia/Tokyo"

    monkeypatch.delenv("TZ", raising=False)
    tz = cb._detect_system_timezone()
    assert isinstance(tz, str)
    assert len(tz) > 0


def test_init_populates_timezone_and_preserves_existing(tmp_path, monkeypatch):
    monkeypatch.setenv("TZ", "Asia/Seoul")
    _write_layout(tmp_path)
    # Remove TZ from .env and .env.dev to test initial population
    prod_text = (tmp_path / ".env").read_text(encoding="utf-8")
    dev_text = (tmp_path / ".env.dev").read_text(encoding="utf-8")
    (tmp_path / ".env").write_text(f"{prod_text}\nTZ=\n", encoding="utf-8")
    (tmp_path / ".env.dev").write_text(f"{dev_text}\nTZ=\n", encoding="utf-8")

    assert cb.main(["init"], root=tmp_path) == 0
    assert "TZ=Asia/Seoul" in (tmp_path / ".env").read_text(encoding="utf-8")
    assert "TZ=Asia/Seoul" in (tmp_path / ".env.dev").read_text(encoding="utf-8")

    # Explicit custom TZ preserved on re-init
    custom_prod = (tmp_path / ".env").read_text(encoding="utf-8").replace("TZ=Asia/Seoul", "TZ=America/New_York")
    (tmp_path / ".env").write_text(custom_prod, encoding="utf-8")
    assert cb.main(["init"], root=tmp_path) == 0
    assert "TZ=America/New_York" in (tmp_path / ".env").read_text(encoding="utf-8")


def test_compose_environment_includes_tz(tmp_path, monkeypatch):
    monkeypatch.setenv("TZ", "Asia/Seoul")
    _write_layout(tmp_path, dev=False)
    runtime = cb.load_runtime(cb.Layout(tmp_path))
    env = runtime.compose_environment()
    assert env.get("TZ") == "Asia/Seoul"


def test_load_runtime_validates_readonly_token_rules(tmp_path):
    _write_layout(tmp_path, dev=False)
    # 1. Blank readonly token is allowed (fail-closed default)
    runtime = cb.load_runtime(cb.Layout(tmp_path))
    assert runtime.values.get("CLAIRE_READONLY_TOKEN") == ""

    # 2. Valid readonly token is allowed
    readonly_secret = "readonly-" + ("r" * 32)
    (tmp_path / ".env").write_text(
        (tmp_path / ".env")
        .read_text(encoding="utf-8")
        .replace("CLAIRE_READONLY_TOKEN=", f"CLAIRE_READONLY_TOKEN={readonly_secret}"),
        encoding="utf-8",
    )
    runtime = cb.load_runtime(cb.Layout(tmp_path))
    assert runtime.values.get("CLAIRE_READONLY_TOKEN") == readonly_secret

    # 3. Invalid token (too short) is rejected
    (tmp_path / ".env").write_text(
        (tmp_path / ".env")
        .read_text(encoding="utf-8")
        .replace(
            f"CLAIRE_READONLY_TOKEN={readonly_secret}",
            "CLAIRE_READONLY_TOKEN=short",
        ),
        encoding="utf-8",
    )
    with pytest.raises(cb.ManuscriptError, match="CLAIRE_READONLY_TOKEN"):
        cb.load_runtime(cb.Layout(tmp_path))

    # 4. Readonly token identical to inject token is rejected
    owner = runtime.values["CLAIRE_INJECT_TOKEN"]
    (tmp_path / ".env").write_text(
        (tmp_path / ".env")
        .read_text(encoding="utf-8")
        .replace("CLAIRE_READONLY_TOKEN=short", f"CLAIRE_READONLY_TOKEN={owner}"),
        encoding="utf-8",
    )
    with pytest.raises(cb.ManuscriptError, match="must be different"):
        cb.load_runtime(cb.Layout(tmp_path))


def test_dotenv_is_parsed_as_data_not_executed(tmp_path):
    _write_layout(tmp_path, dev=False)
    marker = tmp_path / "executed"
    with (tmp_path / ".env").open("a", encoding="utf-8") as stream:
        stream.write(f"UNRELATED=$(touch {marker})\n")

    runtime = cb.load_runtime(cb.Layout(tmp_path))

    assert runtime.values["UNRELATED"] == f"$(touch {marker})"
    assert not marker.exists()


def test_dev_prefix_uses_overlay_stable_project_and_compose_environment(
    tmp_path, monkeypatch
):
    _write_layout(tmp_path, token="telegram-secret")
    monkeypatch.delenv("CB_PROJECT_NAME", raising=False)
    monkeypatch.delenv("CB_WAIT_TIMEOUT", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)

    with patch.object(cb.subprocess, "run", side_effect=_fake_success) as run:
        assert cb.main(["dev", "status"], root=tmp_path) == 0

    call = run.call_args
    argv = call.args[0]
    assert argv[:2] == ["docker", "compose"]
    assert argv[argv.index("-p") + 1] == "claire-bible-dev"
    assert str(tmp_path / "docker-compose.dev.yml") in argv
    assert argv[argv.index("--profile") + 1] == "bot"
    assert argv[-1] == "ps"
    env = call.kwargs["env"]
    assert env["CLAIRE_ENVIRONMENT"] == "development"
    assert env["CB_ENV_FILE"] == str((tmp_path / ".env").resolve())
    assert env["CB_DEV_ENV_FILE"] == str((tmp_path / ".env.dev").resolve())


def test_process_environment_selects_development_without_legacy_prefix(
    tmp_path, monkeypatch
):
    _write_layout(tmp_path)
    monkeypatch.setenv("CLAIRE_ENVIRONMENT", "development")

    with patch.object(cb.subprocess, "run", side_effect=_fake_success) as run:
        assert cb.main(["status"], root=tmp_path) == 0

    argv = run.call_args.args[0]
    assert str(tmp_path / "docker-compose.dev.yml") in argv
    assert argv[argv.index("-p") + 1] == "claire-bible-dev"
    assert run.call_args.kwargs["env"]["CLAIRE_ENVIRONMENT"] == "development"


def test_process_web_values_do_not_override_container_env_files(
    tmp_path, monkeypatch
):
    _write_layout(tmp_path, dev=False)
    monkeypatch.setenv("CLAIRE_PUBLIC_URL", " https://wrong.example.com/")
    monkeypatch.setenv("CLAIRE_CORS_ALLOWED_ORIGINS", "https://bad_host")
    monkeypatch.setenv("CLAIRE_ANONYMOUS_READONLY", "1")

    with patch.object(cb.subprocess, "run", side_effect=_fake_success) as run:
        assert cb.main(["status"], root=tmp_path) == 0

    env = run.call_args.kwargs["env"]
    assert "CLAIRE_PUBLIC_URL" not in env
    assert "CLAIRE_CORS_ALLOWED_ORIGINS" not in env
    assert "CLAIRE_ANONYMOUS_READONLY" not in env


def test_missing_anonymous_setting_defaults_disabled_in_production(tmp_path):
    _write_layout(tmp_path, dev=False)
    env_path = tmp_path / ".env"
    env_path.write_text(
        env_path.read_text(encoding="utf-8").replace(
            "CLAIRE_ANONYMOUS_READONLY=1\n",
            "",
        ),
        encoding="utf-8",
    )

    runtime = cb.load_runtime(cb.Layout(tmp_path))

    assert runtime.anonymous_readonly is False


def test_development_does_not_inherit_enabled_production_anonymous_setting(
    tmp_path,
):
    _write_layout(tmp_path)
    dev_path = tmp_path / ".env.dev"
    dev_path.write_text(
        dev_path.read_text(encoding="utf-8").replace(
            "CLAIRE_ANONYMOUS_READONLY=1\n",
            "",
        ),
        encoding="utf-8",
    )

    with patch.object(cb.subprocess, "run") as run:
        assert cb.main(["dev", "status"], root=tmp_path) == 2

    run.assert_not_called()


def test_profiles_use_their_explicit_anonymous_settings(tmp_path):
    _write_layout(tmp_path)
    dev_path = tmp_path / ".env.dev"
    dev_path.write_text(
        dev_path.read_text(encoding="utf-8").replace(
            "CLAIRE_ANONYMOUS_READONLY=1",
            "CLAIRE_ANONYMOUS_READONLY=0",
        ),
        encoding="utf-8",
    )

    production = cb.load_runtime(cb.Layout(tmp_path))
    development = cb.load_runtime(cb.Layout(tmp_path), legacy_dev=True)

    assert production.anonymous_readonly is True
    assert development.anonymous_readonly is False


@pytest.mark.parametrize(
    "value",
    ("", "true", "yes", "2", "01", " 1", "1 ", "\t0", "0\t", '"1"'),
)
def test_anonymous_setting_requires_exact_zero_or_one(
    tmp_path,
    value,
):
    _write_layout(tmp_path, dev=False)
    env_path = tmp_path / ".env"
    env_path.write_text(
        env_path.read_text(encoding="utf-8").replace(
            "CLAIRE_ANONYMOUS_READONLY=1",
            f"CLAIRE_ANONYMOUS_READONLY={value}",
        ),
        encoding="utf-8",
    )

    with patch.object(cb.subprocess, "run") as run:
        assert cb.main(["status"], root=tmp_path) == 2

    run.assert_not_called()


def test_environment_is_required_without_process_or_legacy_alias(
    tmp_path, monkeypatch
):
    _write_layout(tmp_path, dev=False)
    monkeypatch.delenv("CLAIRE_ENVIRONMENT", raising=False)
    env_path = tmp_path / ".env"
    env_path.write_text(
        env_path.read_text(encoding="utf-8").replace(
            "CLAIRE_ENVIRONMENT=production\n", ""
        ),
        encoding="utf-8",
    )

    with patch.object(cb.subprocess, "run") as run:
        assert cb.main(["status"], root=tmp_path) == 2

    run.assert_not_called()


@pytest.mark.parametrize("value", ("prod", "Development", " production "))
def test_environment_requires_exact_supported_value(
    tmp_path, monkeypatch, value
):
    _write_layout(tmp_path, dev=False)
    monkeypatch.setenv("CLAIRE_ENVIRONMENT", value)

    with patch.object(cb.subprocess, "run") as run:
        assert cb.main(["status"], root=tmp_path) == 2

    run.assert_not_called()


def test_legacy_dev_alias_rejects_process_production(tmp_path, monkeypatch):
    _write_layout(tmp_path)
    monkeypatch.setenv("CLAIRE_ENVIRONMENT", "production")

    with patch.object(cb.subprocess, "run") as run:
        assert cb.main(["dev", "status"], root=tmp_path) == 2

    run.assert_not_called()


def test_development_overlay_must_declare_development(tmp_path, monkeypatch):
    _write_layout(tmp_path)
    monkeypatch.delenv("CLAIRE_ENVIRONMENT", raising=False)
    dev_path = tmp_path / ".env.dev"
    dev_path.write_text(
        dev_path.read_text(encoding="utf-8").replace(
            "CLAIRE_ENVIRONMENT=development",
            "CLAIRE_ENVIRONMENT=production",
        ),
        encoding="utf-8",
    )

    with patch.object(cb.subprocess, "run") as run:
        assert cb.main(["dev", "status"], root=tmp_path) == 2

    run.assert_not_called()


@pytest.mark.parametrize(
    "bind",
    ("0.0.0.0", "224.0.0.1", "claire.internal", "::1"),
)
def test_api_bind_requires_concrete_ipv4(tmp_path, monkeypatch, bind):
    _write_layout(tmp_path, dev=False)
    monkeypatch.delenv("CLAIRE_ENVIRONMENT", raising=False)
    env_path = tmp_path / ".env"
    env_path.write_text(
        env_path.read_text(encoding="utf-8").replace(
            "CB_API_BIND=127.0.0.1",
            f"CB_API_BIND={bind}",
        ),
        encoding="utf-8",
    )

    with patch.object(cb.subprocess, "run") as run:
        assert cb.main(["status"], root=tmp_path) == 2

    run.assert_not_called()


@pytest.mark.parametrize(
    "public_url",
    (
        "http://claire.example.com/",
        "https://127.0.0.1/",
        "https://claire.example.com/subpath",
        "https://*.example.com/",
        "https://claire.example.com/?next=/graph",
    ),
)
def test_production_public_url_requires_https_dns_root(
    tmp_path, monkeypatch, public_url
):
    _write_layout(tmp_path, dev=False)
    monkeypatch.delenv("CLAIRE_ENVIRONMENT", raising=False)
    env_path = tmp_path / ".env"
    env_path.write_text(
        env_path.read_text(encoding="utf-8").replace(
            "CLAIRE_PUBLIC_URL=https://claire.example.com/",
            f"CLAIRE_PUBLIC_URL={public_url}",
        ),
        encoding="utf-8",
    )

    with patch.object(cb.subprocess, "run") as run:
        assert cb.main(["status"], root=tmp_path) == 2

    run.assert_not_called()


def test_public_url_rejects_quoted_outer_whitespace(tmp_path, monkeypatch):
    _write_layout(tmp_path, dev=False)
    monkeypatch.delenv("CLAIRE_ENVIRONMENT", raising=False)
    env_path = tmp_path / ".env"
    env_path.write_text(
        env_path.read_text(encoding="utf-8").replace(
            "CLAIRE_PUBLIC_URL=https://claire.example.com/",
            'CLAIRE_PUBLIC_URL=" https://claire.example.com/"',
        ),
        encoding="utf-8",
    )

    with patch.object(cb.subprocess, "run") as run:
        assert cb.main(["status"], root=tmp_path) == 2

    run.assert_not_called()


def test_development_public_url_must_match_published_authority(
    tmp_path, monkeypatch
):
    _write_layout(tmp_path)
    monkeypatch.delenv("CLAIRE_ENVIRONMENT", raising=False)
    dev_path = tmp_path / ".env.dev"
    dev_path.write_text(
        dev_path.read_text(encoding="utf-8").replace(
            "CLAIRE_PUBLIC_URL=http://127.0.0.1:8766/",
            "CLAIRE_PUBLIC_URL=http://127.0.0.1:9999/",
        ),
        encoding="utf-8",
    )

    with patch.object(cb.subprocess, "run") as run:
        assert cb.main(["dev", "status"], root=tmp_path) == 2

    run.assert_not_called()


@pytest.mark.parametrize(
    "origins",
    (
        "*",
        "https://*.example.com",
        "https://ui.example.com/path",
        "http://ui.example.com",
        "https://bad_host",
        "https://ui.example.com,https://ui.example.com",
    ),
)
def test_production_cors_origins_are_exact_https_origins(
    tmp_path, monkeypatch, origins
):
    _write_layout(tmp_path, dev=False)
    monkeypatch.delenv("CLAIRE_ENVIRONMENT", raising=False)
    env_path = tmp_path / ".env"
    env_path.write_text(
        env_path.read_text(encoding="utf-8").replace(
            "CLAIRE_CORS_ALLOWED_ORIGINS=",
            f"CLAIRE_CORS_ALLOWED_ORIGINS={origins}",
        ),
        encoding="utf-8",
    )

    with patch.object(cb.subprocess, "run") as run:
        assert cb.main(["status"], root=tmp_path) == 2

    run.assert_not_called()


def test_empty_telegram_token_does_not_enable_bot_profile(tmp_path, monkeypatch):
    _write_layout(tmp_path, token="")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)

    with patch.object(cb.subprocess, "run", side_effect=_fake_success) as run:
        assert cb.main(["status"], root=tmp_path) == 0

    assert "--profile" not in run.call_args.args[0]


def test_passthrough_preserves_arguments_tty_and_exit_code(tmp_path):
    _write_layout(tmp_path, dev=False)

    def fail(argv, **kwargs):
        assert "stdout" not in kwargs
        assert "stderr" not in kwargs
        return _completed(argv, 37)

    with patch.object(cb.subprocess, "run", side_effect=fail) as run:
        rc = cb.main(["logs", "-f", "--tail", "25", "api"], root=tmp_path)

    assert rc == 37
    assert run.call_args.args[0][-5:] == ["logs", "-f", "--tail", "25", "api"]


def test_shell_and_app_keep_interactive_stdio(tmp_path):
    _write_layout(tmp_path, dev=False)
    with patch.object(cb.subprocess, "run", side_effect=_fake_success) as run:
        assert cb.main(["shell"], root=tmp_path) == 0
        assert cb.main(
            ["app", "search", "topic", "--no-summary"],
            root=tmp_path,
        ) == 0

    shell_call, app_call = run.call_args_list
    assert shell_call.args[0][-3:] == ["exec", "api", "bash"]
    assert app_call.args[0][-8:] == [
        "run",
        "--rm",
        "--no-deps",
        "api",
        "claire",
        "search",
        "topic",
        "--no-summary",
    ]
    for call in (shell_call, app_call):
        assert "stdout" not in call.kwargs
        assert "stdin" not in call.kwargs
    assert (tmp_path / ".cb-manuscript" / "claire-bible.lock").is_file()


@pytest.mark.parametrize(
    "app_args",
    (
        ("--help",),
        ("status", "--help"),
    ),
)
def test_app_help_is_forwarded_to_claire(tmp_path, app_args):
    _write_layout(tmp_path, dev=False)

    with patch.object(cb.subprocess, "run", side_effect=_fake_success) as run:
        assert cb.main(["app", *app_args], root=tmp_path) == 0

    assert run.call_args.args[0][-5 - len(app_args) :] == [
        "run",
        "--rm",
        "--no-deps",
        "api",
        "claire",
        *app_args,
    ]


@pytest.mark.parametrize(
    "app_args",
    (
        ("migrate",),
        ("bot",),
        ("serve-api",),
        ("recover-loop",),
        ("refresh-loop",),
        ("expand-loop",),
        ("reextract",),
        ("dedup-merge", "--apply"),
        ("dedup-merge", "--a"),
        ("recanonicalize",),
    ),
)
def test_app_rejects_managed_or_unsafe_commands_by_default(
    tmp_path, capsys, app_args
):
    _write_layout(tmp_path, dev=False)

    with patch.object(cb.subprocess, "run") as run:
        assert cb.main(["app", *app_args], root=tmp_path) == 2

    run.assert_not_called()
    error = capsys.readouterr().err
    assert app_args[0] in error
    assert "--advanced" in error


@pytest.mark.parametrize(
    "app_args",
    (
        ("migrate",),
        ("serve-api",),
        ("reextract", "--limit", "2"),
        ("dedup-merge", "--apply"),
        ("recanonicalize",),
    ),
)
def test_app_advanced_override_runs_managed_or_unsafe_commands(
    tmp_path, capsys, app_args
):
    _write_layout(tmp_path, dev=False)

    with patch.object(cb.subprocess, "run", side_effect=_fake_success) as run:
        assert cb.main(["app", "--advanced", *app_args], root=tmp_path) == 0

    command = run.call_args.args[0]
    assert "--advanced" not in command
    assert command[-len(app_args) :] == list(app_args)
    assert "does not guarantee" in capsys.readouterr().err


def test_app_unknown_command_requires_advanced_override(tmp_path, capsys):
    _write_layout(tmp_path, dev=False)

    with patch.object(cb.subprocess, "run") as run:
        assert cb.main(["app", "future-command"], root=tmp_path) == 2
    run.assert_not_called()
    assert "unclassified app command" in capsys.readouterr().err

    with patch.object(cb.subprocess, "run", side_effect=_fake_success) as run:
        assert cb.main(
            ["app", "--advanced", "future-command"],
            root=tmp_path,
        ) == 0
    assert run.call_args.args[0][-2:] == ["claire", "future-command"]
    assert "does not guarantee" in capsys.readouterr().err


def test_app_advanced_always_warns_even_for_allowed_command(tmp_path, capsys):
    _write_layout(tmp_path, dev=False)

    with patch.object(cb.subprocess, "run", side_effect=_fake_success):
        assert cb.main(["app", "--advanced", "status"], root=tmp_path) == 0

    assert "does not guarantee" in capsys.readouterr().err


@pytest.mark.parametrize("argv", (("app",), ("app", "--advanced")))
def test_app_requires_a_claire_command(tmp_path, argv):
    _write_layout(tmp_path, dev=False)

    with patch.object(cb.subprocess, "run") as run:
        assert cb.main(list(argv), root=tmp_path) == 2

    run.assert_not_called()


def test_app_delimiters_are_supported(tmp_path, capsys):
    _write_layout(tmp_path, dev=False)

    with patch.object(cb.subprocess, "run", side_effect=_fake_success) as run:
        assert cb.main(["app", "--", "--help"], root=tmp_path) == 0
        assert cb.main(
            ["app", "--advanced", "--", "migrate"],
            root=tmp_path,
        ) == 0

    help_call, migrate_call = run.call_args_list
    assert help_call.args[0][-2:] == ["claire", "--help"]
    assert migrate_call.args[0][-2:] == ["claire", "migrate"]
    assert "does not guarantee" in capsys.readouterr().err


def test_app_returns_child_exit_code(tmp_path):
    _write_layout(tmp_path, dev=False)

    def fail(argv, **_kwargs):
        return _completed(argv, returncode=29)

    with patch.object(cb.subprocess, "run", side_effect=fail):
        assert cb.main(["app", "status"], root=tmp_path) == 29


def test_app_rejects_lock_contention_before_subprocess(tmp_path):
    _write_layout(tmp_path, dev=False)
    runtime = cb.load_runtime(cb.Layout(tmp_path))

    with cb.InstanceLock(runtime), patch.object(cb.subprocess, "run") as run:
        assert cb.main(["app", "status"], root=tmp_path) == 73

    run.assert_not_called()


def test_app_allows_non_applying_dedup_plan_without_advanced_override(tmp_path):
    _write_layout(tmp_path, dev=False)

    with patch.object(cb.subprocess, "run", side_effect=_fake_success) as run:
        assert cb.main(
            ["app", "dedup-merge", "--threshold", "0.95"],
            root=tmp_path,
        ) == 0

    assert run.call_args.args[0][-3:] == [
        "dedup-merge",
        "--threshold",
        "0.95",
    ]


def test_app_allows_recanonicalize_dry_run_without_advanced_override(tmp_path):
    _write_layout(tmp_path, dev=False)

    with patch.object(cb.subprocess, "run", side_effect=_fake_success) as run:
        assert cb.main(
            ["app", "recanonicalize", "--dry-run"],
            root=tmp_path,
        ) == 0

    assert run.call_args.args[0][-2:] == ["recanonicalize", "--dry-run"]


def test_install_orders_build_legacy_stop_migrate_up_and_health(tmp_path):
    _write_layout(tmp_path, dev=False)

    def fake(argv, **_kwargs):
        if argv[:3] == ["docker", "ps", "-q"]:
            name_filter = argv[-1]
            found = "legacy-id\n" if name_filter == "name=^/claire_api$" else ""
            return _completed(argv, stdout=found)
        return _completed(argv)

    with patch.object(cb.subprocess, "run", side_effect=fake) as run:
        assert cb.main(["install"], root=tmp_path) == 0

    commands = _commands(run)
    build_index = next(i for i, cmd in enumerate(commands) if cmd[-1:] == ["build"])
    legacy_index = commands.index(["docker", "stop", "claire_api"])
    migrate_index = next(
        i for i, cmd in enumerate(commands) if cmd[-6:] == [
            "run",
            "--rm",
            "--no-deps",
            "api",
            "claire",
            "migrate",
        ]
    )
    up_index = next(i for i, cmd in enumerate(commands) if "up" in cmd[-7:])
    health_index = next(i for i, cmd in enumerate(commands) if cmd[-5:] == [
        "exec",
        "-T",
        "api",
        "claire",
        "liveness",
    ])
    assert build_index < legacy_index < migrate_index < up_index < health_index
    up = commands[up_index]
    assert up[up.index("--wait-timeout") + 1] == "45"


def test_install_rerun_stops_current_project_before_migration(tmp_path):
    _write_layout(tmp_path, dev=False)

    def fake(argv, **_kwargs):
        if argv[:2] == ["docker", "compose"] and argv[-2:] == ["ps", "-q"]:
            return _completed(argv, stdout="existing-api\n")
        if argv[:3] == ["docker", "ps", "-q"]:
            return _completed(argv, stdout="")
        return _completed(argv)

    with patch.object(cb.subprocess, "run", side_effect=fake) as run:
        assert cb.main(["install"], root=tmp_path) == 0

    commands = _commands(run)
    stop_index = next(i for i, cmd in enumerate(commands) if cmd[-1:] == ["stop"])
    migrate_index = next(i for i, cmd in enumerate(commands) if cmd[-2:] == [
        "claire",
        "migrate",
    ])
    assert stop_index < migrate_index
    project_stop = commands[stop_index]
    assert project_stop[project_stop.index("--profile") + 1] == "*"


def test_migration_failure_resumes_only_previously_running_containers(tmp_path):
    _write_layout(tmp_path, dev=False)

    def fake(argv, **_kwargs):
        if argv[:2] == ["docker", "compose"] and argv[-2:] == ["ps", "-q"]:
            return _completed(argv, stdout="existing-api\n")
        if argv[:3] == ["docker", "ps", "-q"]:
            stdout = "legacy-id\n" if argv[-1] == "name=^/claire_api$" else ""
            return _completed(argv, stdout=stdout)
        if argv[-2:] == ["claire", "migrate"]:
            return _completed(argv, returncode=17)
        return _completed(argv)

    with patch.object(cb.subprocess, "run", side_effect=fake) as run:
        assert cb.main(["install"], root=tmp_path) == 17

    commands = _commands(run)
    assert ["docker", "start", "existing-api"] in commands
    assert ["docker", "start", "claire_api"] in commands
    assert not any("up" in command for command in commands)
    assert not (tmp_path / ".cb-manuscript" / "production.json").exists()


def test_project_stop_failure_restarts_exact_previous_containers(tmp_path):
    _write_layout(tmp_path, dev=False)

    def fake(argv, **_kwargs):
        if argv[:2] == ["docker", "compose"] and argv[-2:] == ["ps", "-q"]:
            return _completed(argv, stdout="project-api\nproject-worker\n")
        if argv[:2] == ["docker", "compose"] and argv[-1:] == ["stop"]:
            return _completed(argv, returncode=19)
        return _completed(argv)

    with patch.object(cb.subprocess, "run", side_effect=fake) as run:
        assert cb.main(["install"], root=tmp_path) == 19

    commands = _commands(run)
    assert ["docker", "start", "project-api", "project-worker"] in commands
    assert not any(command[-2:] == ["claire", "migrate"] for command in commands)


def test_partial_legacy_stop_failure_resumes_legacy_and_project(tmp_path):
    _write_layout(tmp_path, dev=False)

    def fake(argv, **_kwargs):
        if argv[:2] == ["docker", "compose"] and argv[-2:] == ["ps", "-q"]:
            return _completed(argv, stdout="project-api\n")
        if argv[:3] == ["docker", "ps", "-q"]:
            if argv[-1] in {
                "name=^/claire_api$",
                "name=^/claire_refresh$",
            }:
                return _completed(argv, stdout="legacy-id\n")
            return _completed(argv, stdout="")
        if argv == ["docker", "stop", "claire_refresh"]:
            return _completed(argv, returncode=20)
        return _completed(argv)

    with patch.object(cb.subprocess, "run", side_effect=fake) as run:
        assert cb.main(["install"], root=tmp_path) == 20

    commands = _commands(run)
    assert ["docker", "start", "claire_api"] in commands
    assert ["docker", "start", "project-api"] in commands
    assert not any(command[-2:] == ["claire", "migrate"] for command in commands)


def test_activation_failure_preserves_failed_state_without_starting_legacy(
    tmp_path,
):
    _write_layout(tmp_path, dev=False)

    def fake(argv, **_kwargs):
        if argv[:2] == ["docker", "compose"] and argv[-2:] == ["ps", "-q"]:
            return _completed(argv, stdout="existing-api\n")
        if argv[:3] == ["docker", "ps", "-q"]:
            stdout = "legacy-id\n" if argv[-1] == "name=^/claire_api$" else ""
            return _completed(argv, stdout=stdout)
        if "up" in argv:
            return _completed(argv, returncode=18)
        return _completed(argv)

    with patch.object(cb.subprocess, "run", side_effect=fake) as run:
        assert cb.main(["install"], root=tmp_path) == 18

    commands = _commands(run)
    assert not any(command[:2] == ["docker", "start"] for command in commands)
    assert ["docker", "start", "claire_api"] not in commands
    assert not (tmp_path / ".cb-manuscript" / "production.json").exists()


def test_success_records_secret_free_deployment_state(tmp_path):
    _write_layout(tmp_path, dev=False, token="do-not-record")

    def fake(argv, **_kwargs):
        if argv[:3] == ["git", "rev-parse", "HEAD"]:
            return _completed(argv, stdout="a" * 40 + "\n")
        if argv[:3] == ["docker", "ps", "-q"]:
            return _completed(argv, stdout="")
        return _completed(argv)

    with patch.object(cb.subprocess, "run", side_effect=fake):
        assert cb.main(["install"], root=tmp_path) == 0

    state_path = tmp_path / ".cb-manuscript" / "production.json"
    state_text = state_path.read_text(encoding="utf-8")
    assert stat.S_IMODE(state_path.stat().st_mode) == 0o600
    assert "do-not-record" not in state_text
    assert '"action": "install"' in state_text
    assert '"source_revision": "' + ("a" * 40) + '"' in state_text


def test_production_and_development_keep_separate_state(tmp_path):
    _write_layout(tmp_path)

    def fake(argv, **_kwargs):
        if argv[:3] == ["docker", "ps", "-q"]:
            return _completed(argv, stdout="")
        return _completed(argv)

    with patch.object(cb.subprocess, "run", side_effect=fake):
        assert cb.main(["install"], root=tmp_path) == 0
        assert cb.main(["dev", "install"], root=tmp_path) == 0

    state_dir = tmp_path / ".cb-manuscript"
    prod = (state_dir / "production.json").read_text(encoding="utf-8")
    dev = (state_dir / "development.json").read_text(encoding="utf-8")
    assert '"profile": "production"' in prod
    assert '"project": "claire-bible"' in prod
    assert '"profile": "development"' in dev
    assert '"project": "claire-bible-dev"' in dev


def test_project_name_change_is_rejected_before_build(tmp_path):
    _write_layout(tmp_path, dev=False)

    def fake(argv, **_kwargs):
        if argv[:3] == ["docker", "ps", "-q"]:
            return _completed(argv, stdout="")
        return _completed(argv)

    with patch.object(cb.subprocess, "run", side_effect=fake):
        assert cb.main(["install"], root=tmp_path) == 0

    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    (tmp_path / ".env").write_text(
        env_text.replace(
            "CB_PROJECT_NAME=claire-bible",
            "CB_PROJECT_NAME=renamed-project",
        ),
        encoding="utf-8",
    )
    with patch.object(cb.subprocess, "run", side_effect=fake) as run:
        assert cb.main(["install"], root=tmp_path) == 2

    commands = _commands(run)
    assert any(command[-2:] == ["config", "--quiet"] for command in commands)
    assert not any(command[-1:] == ["build"] for command in commands)


def test_update_build_failure_does_not_stop_existing_stack(tmp_path):
    _write_layout(tmp_path, dev=False)

    def fake(argv, **_kwargs):
        if argv[:2] == ["git", "status"]:
            return _completed(argv, stdout="")
        if argv[:3] == ["git", "rev-parse", "--abbrev-ref"]:
            return _completed(argv, stdout="origin/main\n")
        if argv[-1:] == ["build"]:
            return _completed(argv, returncode=9)
        return _completed(argv)

    with patch.object(cb.subprocess, "run", side_effect=fake) as run:
        assert cb.main(["update"], root=tmp_path) == 9

    commands = _commands(run)
    assert ["git", "pull", "--ff-only"] in commands
    assert not any(cmd[-1:] == ["stop"] for cmd in commands if cmd[:2] == ["docker", "compose"])
    assert not any(cmd[:2] == ["docker", "stop"] for cmd in commands)


def test_update_no_fetch_skips_git_and_stops_project_after_build(tmp_path):
    _write_layout(tmp_path, dev=False)

    def fake(argv, **_kwargs):
        if argv[:2] == ["docker", "compose"] and argv[-2:] == ["ps", "-q"]:
            return _completed(argv, stdout="project-container\n")
        if argv[:3] == ["docker", "ps", "-q"]:
            return _completed(argv, stdout="")
        return _completed(argv)

    with patch.object(cb.subprocess, "run", side_effect=fake) as run:
        assert cb.main(["update", "--no-fetch"], root=tmp_path) == 0

    commands = _commands(run)
    assert ["git", "pull", "--ff-only"] not in commands
    assert not any(
        command[:2] == ["git", "status"]
        or command[:3] == ["git", "rev-parse", "--abbrev-ref"]
        for command in commands
    )
    build_index = next(i for i, cmd in enumerate(commands) if cmd[-1:] == ["build"])
    stop_index = next(i for i, cmd in enumerate(commands) if cmd[-1:] == ["stop"])
    assert build_index < stop_index


def test_update_backfills_missing_env_variables_and_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("TZ", "Asia/Seoul")
    _write_layout(tmp_path, dev=False)

    # Simulate template getting a new variable in upstream
    example_text = (tmp_path / ".env.example").read_text(encoding="utf-8")
    example_text += "\n# Upstream added new setting\nNEW_FEATURE_FLAG=1\n"
    (tmp_path / ".env.example").write_text(example_text, encoding="utf-8")

    # Target .env does not have TZ and does not have NEW_FEATURE_FLAG
    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    env_text = env_text.replace("TZ=\n", "").replace("TZ=", "")
    (tmp_path / ".env").write_text(env_text, encoding="utf-8")
    assert "NEW_FEATURE_FLAG" not in (tmp_path / ".env").read_text(encoding="utf-8")

    def fake(argv, **_kwargs):
        if argv[:2] == ["docker", "compose"] and argv[-2:] == ["ps", "-q"]:
            return _completed(argv, stdout="project-container\n")
        if argv[:3] == ["docker", "ps", "-q"]:
            return _completed(argv, stdout="")
        return _completed(argv)

    with patch.object(cb.subprocess, "run", side_effect=fake):
        # 1st update execution
        assert cb.main(["update", "--no-fetch"], root=tmp_path) == 0

    updated_env = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "TZ=Asia/Seoul" in updated_env
    assert "NEW_FEATURE_FLAG=1" in updated_env
    assert "CLAIRE_ENVIRONMENT=production" in updated_env

    # 2nd update execution (Idempotency test)
    with patch.object(cb.subprocess, "run", side_effect=fake):
        assert cb.main(["update", "--no-fetch"], root=tmp_path) == 0

    reupdated_env = (tmp_path / ".env").read_text(encoding="utf-8")
    assert reupdated_env == updated_env
    assert reupdated_env.count("NEW_FEATURE_FLAG=1") == 1
    assert reupdated_env.count("TZ=Asia/Seoul") == 1


def test_update_rejects_dirty_tree_before_pull_build_or_stop(tmp_path):
    _write_layout(tmp_path, dev=False)

    def fake(argv, **_kwargs):
        if argv[:2] == ["git", "status"]:
            return _completed(argv, stdout=" M src/claire/cli.py\n")
        return _completed(argv)

    with patch.object(cb.subprocess, "run", side_effect=fake) as run:
        assert cb.main(["update"], root=tmp_path) == 2

    commands = _commands(run)
    assert ["git", "pull", "--ff-only"] not in commands
    assert not any(cmd[-1:] in (["build"], ["stop"]) for cmd in commands)


def test_update_requires_upstream(tmp_path):
    _write_layout(tmp_path, dev=False)

    def fake(argv, **_kwargs):
        if argv[:2] == ["git", "status"]:
            return _completed(argv, stdout="")
        if argv[:3] == ["git", "rev-parse", "--abbrev-ref"]:
            return _completed(argv, returncode=1, stderr="no upstream")
        return _completed(argv)

    with patch.object(cb.subprocess, "run", side_effect=fake) as run:
        assert cb.main(["update"], root=tmp_path) == 2

    assert ["git", "pull", "--ff-only"] not in _commands(run)


def test_remote_actions_delegate_to_deploy_script_with_action_env(tmp_path):
    _write_layout(tmp_path, dev=False)
    with patch.object(cb.subprocess, "run", side_effect=_fake_success) as run:
        assert cb.main(["remote", "update"], root=tmp_path) == 0

    call = run.call_args
    assert call.args[0] == ["bash", str(tmp_path / "deploy.sh")]
    assert call.kwargs["env"]["DEPLOY_ACTION"] == "update"
    assert call.kwargs["env"]["CLAIRE_ENVIRONMENT"] == "production"


@pytest.mark.parametrize(
    "argv,environment",
    (
        (["dev", "remote", "update"], None),
        (["remote", "update"], "development"),
    ),
)
def test_remote_rejects_development_selection(
    tmp_path, monkeypatch, argv, environment
):
    _write_layout(tmp_path)
    if environment is None:
        monkeypatch.delenv("CLAIRE_ENVIRONMENT", raising=False)
    else:
        monkeypatch.setenv("CLAIRE_ENVIRONMENT", environment)

    with patch.object(cb.subprocess, "run") as run:
        assert cb.main(argv, root=tmp_path) == 2

    run.assert_not_called()


def test_remote_rejects_unused_extra_arguments(tmp_path):
    _write_layout(tmp_path, dev=False)
    with patch.object(cb.subprocess, "run") as run:
        assert cb.main(["remote", "update", "--skip-ci"], root=tmp_path) == 2
    run.assert_not_called()


def test_health_returns_liveness_exit_code_and_uses_noninteractive_exec(tmp_path):
    _write_layout(tmp_path, dev=False)

    def fake(argv, **_kwargs):
        return _completed(argv, returncode=8)

    with patch.object(cb.subprocess, "run", side_effect=fake) as run:
        assert cb.main(["health"], root=tmp_path) == 8

    assert run.call_args.args[0][-5:] == [
        "exec",
        "-T",
        "api",
        "claire",
        "liveness",
    ]


def test_doctor_reports_anonymous_readonly_exposure(tmp_path, capsys):
    _write_layout(tmp_path, dev=False)

    with patch.object(cb.subprocess, "run", side_effect=_fake_success):
        assert cb.main(["doctor"], root=tmp_path) == 0

    output = capsys.readouterr().out
    assert "anonymous readonly: ENABLED" in output
    assert "hidden documents" in output


def test_invalid_project_name_fails_before_subprocess(tmp_path, monkeypatch):
    _write_layout(tmp_path, dev=False)
    monkeypatch.setenv("CB_PROJECT_NAME", "../../wrong")
    with patch.object(cb.subprocess, "run") as run:
        assert cb.main(["status"], root=tmp_path) == 2
    run.assert_not_called()


def test_compose_passthrough_is_exact_after_common_prefix(tmp_path):
    _write_layout(tmp_path, dev=False)
    with patch.object(cb.subprocess, "run", side_effect=_fake_success) as run:
        assert cb.main(["compose", "--", "config", "--services"], root=tmp_path) == 0
    assert run.call_args.args[0][-2:] == ["config", "--services"]


def test_up_without_arguments_is_detached_waiting_and_locked(tmp_path):
    _write_layout(tmp_path, dev=False)
    with patch.object(cb.subprocess, "run", side_effect=_fake_success) as run:
        assert cb.main(["up"], root=tmp_path) == 0

    assert run.call_args.args[0][-5:] == [
        "up",
        "-d",
        "--wait",
        "--wait-timeout",
        "45",
    ]
    assert (tmp_path / ".cb-manuscript" / "claire-bible.lock").is_file()


@pytest.mark.parametrize("flag", ("--help", "-h"))
@pytest.mark.parametrize(
    "command, expected_keywords",
    (
        ("logs", ("api", "bot", "--tail", "-f", "--follow")),
        ("status", ("docker compose ps", "-a", "--all", "--format")),
        ("up", ("-d", "--detach", "--build", "api")),
        ("down", ("--profile * down", "-v", "--volumes", "--remove-orphans")),
        ("restart", ("restart", "api", "bot")),
        ("shell", ("exec", "api", "bash")),
        ("compose", ("Docker Compose", "escape hatch")),
    ),
)
def test_passthrough_help_flags_intercept_and_show_guide(
    tmp_path, capsys, command, flag, expected_keywords
):
    with patch.object(cb.subprocess, "run") as run:
        assert cb.main([command, flag], root=tmp_path) == 0

    run.assert_not_called()
    output = capsys.readouterr().out
    for keyword in expected_keywords:
        assert keyword in output


def test_all_subparsers_have_rich_descriptions():
    parser = cb.build_parser()
    subparsers_action = next(
        action
        for action in parser._actions
        if isinstance(action, cb.argparse._SubParsersAction)
    )
    for name, subparser in subparsers_action.choices.items():
        assert subparser.description, f"{name} subparser is missing a description"


def test_format_migrate_default_is_dry_run(tmp_path, capsys):
    _write_layout(tmp_path, dev=False)
    with patch.object(cb.subprocess, "run", side_effect=_fake_success) as run:
        code = cb.main(["format-migrate"], root=tmp_path)
        assert code == 0

    commands = _commands(run)
    assert len(commands) == 0  # Dry-run must not run compose commands
    captured = capsys.readouterr().out
    assert "Dry-Run" in captured
    assert "포맷 마이그레이션 진단 현황" in captured
    assert "--apply" in captured


def test_format_migrate_explicit_dry_run(tmp_path, capsys):
    _write_layout(tmp_path, dev=False)
    with patch.object(cb.subprocess, "run", side_effect=_fake_success) as run:
        code = cb.main(["format-migrate", "--dry-run"], root=tmp_path)
        assert code == 0

    commands = _commands(run)
    assert len(commands) == 0
    captured = capsys.readouterr().out
    assert "Dry-Run" in captured
    assert "--apply" in captured


def test_format_migrate_uses_env_render_format(tmp_path, capsys):
    _write_layout(tmp_path, dev=False)
    # Write custom format in .env
    env_content = (tmp_path / ".env").read_text(encoding="utf-8")
    (tmp_path / ".env").write_text(env_content + "\nCLAIRE_RENDER_FORMAT=md\n", encoding="utf-8")

    code = cb.main(["format-migrate"], root=tmp_path)
    assert code == 0
    captured = capsys.readouterr().out
    assert "MD (CLAIRE_RENDER_FORMAT=md)" in captured


def test_format_migrate_no_targets_returns_zero(tmp_path, capsys):
    _write_layout(tmp_path, dev=False)
    code = cb.main(["format-migrate", "--apply"], root=tmp_path)
    assert code == 0
    captured = capsys.readouterr().out
    assert "마이그레이션이 필요하지 않습니다" in captured


def test_format_migrate_apply_requires_confirmation_in_non_tty(tmp_path, capsys):
    _write_layout(tmp_path, dev=False)
    # Populate a doc needing migration
    storage = cb.resolve_storage(cb.load_runtime(cb.Layout(root=tmp_path)))
    storage.database.parent.mkdir(parents=True, exist_ok=True)
    conn = cb.sqlite3.connect(storage.database)
    conn.execute("CREATE TABLE documents (id TEXT PRIMARY KEY, detail TEXT, detail_format TEXT)")
    conn.execute("INSERT INTO documents VALUES ('doc1', 'text', 'md')")
    conn.commit()
    conn.close()

    # In non-tty without --yes, format-migrate --apply should exit with code 2 and guide --yes
    with patch.object(cb.sys.stdin, "isatty", return_value=False):
        code = cb.main(["format-migrate", "--apply"], root=tmp_path)
        assert code == 2
        captured = capsys.readouterr().out
        assert "--yes" in captured


def test_format_migrate_apply_interactive_cancellation(tmp_path, capsys):
    _write_layout(tmp_path, dev=False)
    # Populate a doc needing migration so it asks
    storage = cb.resolve_storage(cb.load_runtime(cb.Layout(root=tmp_path)))
    storage.database.parent.mkdir(parents=True, exist_ok=True)
    conn = cb.sqlite3.connect(storage.database)
    conn.execute("CREATE TABLE documents (id TEXT PRIMARY KEY, detail TEXT, detail_format TEXT)")
    conn.execute("INSERT INTO documents VALUES ('doc1', 'text', 'md')")
    conn.commit()
    conn.close()

    with patch.object(cb.sys.stdin, "isatty", return_value=True), \
         patch("builtins.input", return_value="n"), \
         patch.object(cb.subprocess, "run", side_effect=_fake_success) as run:
        code = cb.main(["format-migrate", "--apply"], root=tmp_path)
        assert code == 0
        assert len(_commands(run)) == 0
        captured = capsys.readouterr().out
        assert "취소되었습니다" in captured


def test_format_migrate_apply_with_yes_executes_backfill(tmp_path, capsys):
    _write_layout(tmp_path, dev=False)
    # Populate a doc needing migration
    storage = cb.resolve_storage(cb.load_runtime(cb.Layout(root=tmp_path)))
    storage.database.parent.mkdir(parents=True, exist_ok=True)
    conn = cb.sqlite3.connect(storage.database)
    conn.execute("CREATE TABLE documents (id TEXT PRIMARY KEY, detail TEXT, detail_format TEXT)")
    conn.execute("INSERT INTO documents VALUES ('doc1', 'text', 'md')")
    conn.commit()
    conn.close()

    with patch.object(cb.subprocess, "run", side_effect=_fake_success) as run:
        code = cb.main(["format-migrate", "--apply", "--yes"], root=tmp_path)
        assert code == 0

    commands = _commands(run)
    assert len(commands) == 1
    assert commands[0][-4:] == ["claire", "backfill-detail", "--format", "adoc"]
    captured = capsys.readouterr().out
    assert "completed successfully" in captured


