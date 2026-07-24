"""cb-manuscript orchestration tests; no Docker, Git, or network is used."""

from __future__ import annotations

import os
from pathlib import Path
import stat
import subprocess
from unittest.mock import patch

import pytest

from ops import cb_manuscript as cb


def _write_layout(root: Path, *, dev: bool = True, token: str = "") -> None:
    (root / ".env.example").write_text(
        "\n".join(
            (
                "CB_PROJECT_NAME=claire-bible",
                "CB_WAIT_TIMEOUT=45",
                "CB_API_PORT=8765",
                "CLAIRE_INJECT_TOKEN=",
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
                "CB_PROJECT_NAME=claire-bible-dev",
                "CB_WAIT_TIMEOUT=15",
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


def _fake_success(argv, **_kwargs):  # noqa: ANN001
    return _completed(argv)


def _commands(mock_run) -> list[list[str]]:  # noqa: ANN001
    return [call.args[0] for call in mock_run.call_args_list]


def test_init_is_atomic_idempotent_and_does_not_replace_user_secrets(tmp_path):
    _write_layout(tmp_path)
    (tmp_path / ".env").unlink()
    (tmp_path / ".env.dev").unlink()

    with patch.object(cb.secrets, "token_urlsafe", side_effect=["prod-token", "dev-token"]):
        assert cb.main(["init"], root=tmp_path) == 0

    prod = (tmp_path / ".env").read_text(encoding="utf-8")
    dev = (tmp_path / ".env.dev").read_text(encoding="utf-8")
    assert "CLAIRE_INJECT_TOKEN=prod-token" in prod
    assert "CLAIRE_INJECT_TOKEN=dev-token" in dev
    assert "GEMINI_API_KEY=" in prod
    assert "TELEGRAM_BOT_TOKEN=" in prod
    assert stat.S_IMODE((tmp_path / ".env").stat().st_mode) == 0o600
    assert stat.S_IMODE((tmp_path / ".env.dev").stat().st_mode) == 0o600
    assert (tmp_path / "data").is_dir()
    assert (tmp_path / "vault").is_dir()

    prod = prod.replace("CLAIRE_INJECT_TOKEN=prod-token", "CLAIRE_INJECT_TOKEN=user-value")
    (tmp_path / ".env").write_text(prod, encoding="utf-8")
    with patch.object(cb.secrets, "token_urlsafe") as generate:
        assert cb.main(["init"], root=tmp_path) == 0
    generate.assert_not_called()
    assert "CLAIRE_INJECT_TOKEN=user-value" in (
        tmp_path / ".env"
    ).read_text(encoding="utf-8")


def test_dotenv_is_parsed_as_data_not_executed(tmp_path):
    _write_layout(tmp_path, dev=False)
    marker = tmp_path / "executed"
    with (tmp_path / ".env").open("a", encoding="utf-8") as stream:
        stream.write(f"UNRELATED=$(touch {marker})\n")

    runtime = cb.load_runtime(cb.Layout(tmp_path), dev=False)

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
    assert env["CB_ENV_FILE"] == str((tmp_path / ".env").resolve())
    assert env["CB_DEV_ENV_FILE"] == str((tmp_path / ".env.dev").resolve())


def test_empty_telegram_token_does_not_enable_bot_profile(tmp_path, monkeypatch):
    _write_layout(tmp_path, token="")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)

    with patch.object(cb.subprocess, "run", side_effect=_fake_success) as run:
        assert cb.main(["status"], root=tmp_path) == 0

    assert "--profile" not in run.call_args.args[0]


def test_passthrough_preserves_arguments_tty_and_exit_code(tmp_path):
    _write_layout(tmp_path, dev=False)

    def fail(argv, **kwargs):  # noqa: ANN001
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
        assert cb.main(["app", "status", "--full"], root=tmp_path) == 0

    shell_call, app_call = run.call_args_list
    assert shell_call.args[0][-3:] == ["exec", "api", "bash"]
    assert app_call.args[0][-10:] == [
        "run",
        "--rm",
        "--no-deps",
        "api",
        "uv",
        "run",
        "--frozen",
        "claire",
        "status",
        "--full",
    ]
    for call in (shell_call, app_call):
        assert "stdout" not in call.kwargs
        assert "stdin" not in call.kwargs


def test_install_orders_build_legacy_stop_migrate_up_and_health(tmp_path):
    _write_layout(tmp_path, dev=False)

    def fake(argv, **_kwargs):  # noqa: ANN001
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
        i for i, cmd in enumerate(commands) if cmd[-9:] == [
            "run",
            "--rm",
            "--no-deps",
            "api",
            "uv",
            "run",
            "--frozen",
            "claire",
            "migrate",
        ]
    )
    up_index = next(i for i, cmd in enumerate(commands) if "up" in cmd[-7:])
    health_index = next(i for i, cmd in enumerate(commands) if cmd[-8:] == [
        "exec",
        "-T",
        "api",
        "uv",
        "run",
        "--frozen",
        "claire",
        "liveness",
    ])
    assert build_index < legacy_index < migrate_index < up_index < health_index
    up = commands[up_index]
    assert up[up.index("--wait-timeout") + 1] == "45"


def test_install_rerun_stops_current_project_before_migration(tmp_path):
    _write_layout(tmp_path, dev=False)

    def fake(argv, **_kwargs):  # noqa: ANN001
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

    def fake(argv, **_kwargs):  # noqa: ANN001
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

    def fake(argv, **_kwargs):  # noqa: ANN001
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

    def fake(argv, **_kwargs):  # noqa: ANN001
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

    def fake(argv, **_kwargs):  # noqa: ANN001
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

    def fake(argv, **_kwargs):  # noqa: ANN001
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

    def fake(argv, **_kwargs):  # noqa: ANN001
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
    assert '"profile": "dev"' in dev
    assert '"project": "claire-bible-dev"' in dev


def test_project_name_change_is_rejected_before_build(tmp_path):
    _write_layout(tmp_path, dev=False)

    def fake(argv, **_kwargs):  # noqa: ANN001
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

    def fake(argv, **_kwargs):  # noqa: ANN001
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

    def fake(argv, **_kwargs):  # noqa: ANN001
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


def test_update_rejects_dirty_tree_before_pull_build_or_stop(tmp_path):
    _write_layout(tmp_path, dev=False)

    def fake(argv, **_kwargs):  # noqa: ANN001
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

    def fake(argv, **_kwargs):  # noqa: ANN001
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


def test_remote_rejects_unused_extra_arguments(tmp_path):
    _write_layout(tmp_path, dev=False)
    with patch.object(cb.subprocess, "run") as run:
        assert cb.main(["remote", "update", "--skip-ci"], root=tmp_path) == 2
    run.assert_not_called()


def test_health_returns_liveness_exit_code_and_uses_noninteractive_exec(tmp_path):
    _write_layout(tmp_path, dev=False)

    def fake(argv, **_kwargs):  # noqa: ANN001
        return _completed(argv, returncode=8)

    with patch.object(cb.subprocess, "run", side_effect=fake) as run:
        assert cb.main(["health"], root=tmp_path) == 8

    assert run.call_args.args[0][-8:] == [
        "exec",
        "-T",
        "api",
        "uv",
        "run",
        "--frozen",
        "claire",
        "liveness",
    ]


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
