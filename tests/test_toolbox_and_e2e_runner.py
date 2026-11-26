"""Tests for the contributor toolbox (DE1), F9 .env isolation, Git personal-path hygiene, and scripts/e2e.sh runner back ends (DE3)."""

from __future__ import annotations

import os
from pathlib import Path
import re
import stat
import subprocess
from unittest.mock import patch

from pydantic_settings import SettingsConfigDict
import pytest

from claire.core import config as config_mod
from claire.core.config import Settings
from ops import cb_manuscript as cb
from ops import toolbox

REPO_ROOT = Path(__file__).resolve().parent.parent

# Tracked paths allowed to contain /home/<name>/ or /Users/<name>/ patterns.
_PERSONAL_PATH_RE = re.compile(r"/(?:home|Users)/([A-Za-z0-9._-]+)/")
_ALLOWED_HOME_NAMES = frozenset({"claire", "linuxbrew"})
_ALLOWED_HYGIENE_PATHS = frozenset({
    "docs/design/CONTRIBUTOR_DEVELOPMENT_ENVIRONMENT_DESIGN.md",
    "src/bible/static/docs/scalar.standalone.js",
    "tests/test_toolbox_and_e2e_runner.py",
})


def _completed(argv: list[str], returncode: int = 0, *, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(argv, returncode, stdout, stderr)


def test_toolbox_dockerfile_multiarch_and_env_contract() -> None:
    """Verify ops/dev/toolbox.Dockerfile uses lockfile layering, multi-arch tools, and required ENV settings."""
    dockerfile = (REPO_ROOT / "ops" / "dev" / "toolbox.Dockerfile").read_text(encoding="utf-8")
    instructions = "\n".join(line for line in dockerfile.splitlines() if not line.lstrip().startswith("#"))

    assert "FROM python:3.15.2-slim" in instructions
    assert "denoland/deno:bin-2.9.8" in instructions
    assert "dpkg --print-architecture" in instructions
    assert "amd64" not in instructions
    assert "x86_64" not in instructions
    assert "uv sync --locked --no-install-project --all-extras" in instructions
    assert "npm ci" in instructions
    assert "playwright install --with-deps chromium" in instructions
    assert "deno install --frozen" in instructions
    assert "deno cache --frozen" in instructions
    for env_line in (
        "CLAIRE_IGNORE_DOTENV=1",
        "TMPDIR=/tmp",
        "CLAIRE_E2E_NODE_MODULES=/opt/claire-e2e/node_modules",
        "CLAIRE_E2E_PYTHON=/opt/claire-venv/bin/python",
        "VIRTUAL_ENV=/opt/claire-venv",
        "PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright",
        "DENO_DIR=/opt/deno-cache",
    ):
        assert env_line in instructions


def test_cb_manuscript_dev_toolbox_builds_when_missing_and_runs_without_dotenv(tmp_path: Path) -> None:
    """Verify `./cb-manuscript dev toolbox` runs without .env/.env.dev, builds when missing, and never creates checkout .venv or node_modules."""
    dockerfile = tmp_path / "ops" / "dev" / "toolbox.Dockerfile"
    dockerfile.parent.mkdir(parents=True)
    dockerfile.write_text("FROM python:3.15.2-slim\n", encoding="utf-8")

    calls: list[list[str]] = []

    def fake_run(argv, **_kwargs):
        cmd = [str(part) for part in argv]
        calls.append(cmd)
        if cmd[:3] == ["docker", "image", "inspect"]:
            return _completed(cmd, returncode=1)
        return _completed(cmd, returncode=0)

    with (
        patch.object(toolbox.shutil, "which", return_value="/usr/bin/docker"),
        patch.object(toolbox.subprocess, "run", side_effect=fake_run),
        patch.object(toolbox, "_is_tty", return_value=False),
    ):
        code = cb.main(["dev", "toolbox", "pytest", "-q"], root=tmp_path)

    assert code == 0
    assert calls[0] == ["docker", "image", "inspect", "claire-toolbox:dev"]
    assert calls[1] == ["docker", "build", "-t", "claire-toolbox:dev", "-f", str(dockerfile), str(tmp_path)]
    run_cmd = calls[2]
    uid = os.getuid() if hasattr(os, "getuid") else 1000
    gid = os.getgid() if hasattr(os, "getgid") else 1000
    assert run_cmd[:4] == ["docker", "run", "--rm", "--user"]
    assert f"{uid}:{gid}" in run_cmd
    assert "--memory" in run_cmd and "4g" in run_cmd
    assert "--memory-swap" in run_cmd
    assert "-it" not in run_cmd
    assert f"{tmp_path}:/src" in run_cmd
    assert run_cmd[run_cmd.index("-w") + 1] == "/src"
    for env_pair in (
        "TMPDIR=/tmp",
        "HOME=/tmp",
        "CLAIRE_IGNORE_DOTENV=1",
        "UV_PROJECT_ENVIRONMENT=/opt/claire-venv",
        "VIRTUAL_ENV=/opt/claire-venv",
        "CLAIRE_E2E_NODE_MODULES=/opt/claire-e2e/node_modules",
    ):
        assert env_pair in run_cmd
    assert run_cmd[-3:] == ["claire-toolbox:dev", "pytest", "-q"]
    assert not (tmp_path / ".venv").exists()
    assert not (tmp_path / "e2e" / "node_modules").exists()


def test_toolbox_options_tty_network_build_and_deno_workdir(tmp_path: Path) -> None:
    """Verify --build, --network, TTY -it flag, and deno workdir selection in ops/toolbox.py."""
    dockerfile = tmp_path / "ops" / "dev" / "toolbox.Dockerfile"
    dockerfile.parent.mkdir(parents=True)
    dockerfile.write_text("FROM python:3.15.2-slim\n", encoding="utf-8")

    calls: list[list[str]] = []

    def fake_run(argv, **_kwargs):
        cmd = [str(part) for part in argv]
        calls.append(cmd)
        return _completed(cmd, returncode=0)

    with (
        patch.object(toolbox.shutil, "which", return_value="/usr/bin/docker"),
        patch.object(toolbox.subprocess, "run", side_effect=fake_run),
        patch.object(toolbox, "_is_tty", return_value=True),
    ):
        assert cb.main(["dev", "toolbox", "--network", "claire-devnet-edge", "deno", "task", "test"], root=tmp_path) == 0
        assert cb.main(["dev", "toolbox", "--build", "deno", "check", "--config", "/src/other/deno.json", "main.ts"], root=tmp_path) == 0

    deno_default = calls[1]
    assert "-it" in deno_default
    assert deno_default[deno_default.index("-w") + 1] == "/src/sidecar/fedify"
    assert deno_default[deno_default.index("--network") + 1] == "claire-devnet-edge"
    assert deno_default[-4:] == ["claire-toolbox:dev", "deno", "task", "test"]

    assert calls[2][:2] == ["docker", "build"]
    deno_custom_cfg = calls[3]
    assert deno_custom_cfg[deno_custom_cfg.index("-w") + 1] == "/src"


def test_f9_dotenv_isolation_in_tests_and_explicit_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Verify CLAIRE_IGNORE_DOTENV=1 is active in pytest and skips default .env while preserving explicit _env_file and opt-out."""
    assert os.environ.get("CLAIRE_IGNORE_DOTENV") == "1"
    monkeypatch.delenv("CLAIRE_SITE_NAME", raising=False)
    monkeypatch.setenv("CB_DATA_DIR", str(tmp_path / "data"))

    checkout_dotenv = tmp_path / ".env"
    checkout_dotenv.write_text("CLAIRE_SITE_NAME=FromCheckoutDotEnv\n", encoding="utf-8")
    explicit_dotenv = tmp_path / "custom.env"
    explicit_dotenv.write_text("CLAIRE_SITE_NAME=FromExplicitDotEnv\n", encoding="utf-8")

    class _CheckoutSettings(Settings):
        model_config = SettingsConfigDict(**{**config_mod.Settings.model_config, "env_file": checkout_dotenv})

    ignored = _CheckoutSettings()
    assert ignored.site_name == "Claire Bible"

    explicit = _CheckoutSettings(_env_file=explicit_dotenv)
    assert explicit.site_name == "FromExplicitDotEnv"

    monkeypatch.delenv("CLAIRE_IGNORE_DOTENV", raising=False)
    loaded = _CheckoutSettings()
    assert loaded.site_name == "FromCheckoutDotEnv"


def test_git_tracked_files_have_no_personal_home_paths() -> None:
    """Fail if any tracked Git file outside the allow list contains /home/<name>/ or /Users/<name>/."""
    tracked = subprocess.run(["git", "-C", str(REPO_ROOT), "ls-files", "-z"], capture_output=True, check=True)
    violations: list[str] = []
    for raw_rel in tracked.stdout.split(b"\x00"):
        if not raw_rel:
            continue
        rel = raw_rel.decode("utf-8", errors="replace")
        if rel in _ALLOWED_HYGIENE_PATHS:
            continue
        path = REPO_ROOT / rel
        if not path.is_file():
            continue
        data = path.read_bytes()
        if b"\x00" in data:
            continue
        text = data.decode("utf-8", errors="replace")
        for lineno, line in enumerate(text.splitlines(), start=1):
            for match in _PERSONAL_PATH_RE.finditer(line):
                if match.group(1) not in _ALLOWED_HOME_NAMES:
                    violations.append(f"{rel}:{lineno}: {match.group(0)}")
    assert violations == [], f"Personal home path(s) found in tracked files: {violations}"


def _setup_e2e_sandbox(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    """Create a minimal repo layout with scripts/e2e.sh and a mock docker binary on PATH."""
    (tmp_path / "scripts").mkdir()
    (tmp_path / "e2e").mkdir()
    (tmp_path / "bin").mkdir()

    e2e_sh = tmp_path / "scripts" / "e2e.sh"
    e2e_sh.write_text((REPO_ROOT / "scripts" / "e2e.sh").read_text(encoding="utf-8"), encoding="utf-8")
    e2e_sh.chmod(0o755)
    (tmp_path / "e2e" / "sample.spec.js").write_text("// mock spec\n", encoding="utf-8")

    docker_log = tmp_path / "docker.log"
    docker_state = tmp_path / "docker_state"
    mock_docker = tmp_path / "bin" / "docker"
    mock_docker.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> "${MOCK_DOCKER_LOG}"
sub="${1:-}"
shift || true
case "$sub" in
  image)
    exit 0
    ;;
  run)
    run_dir=""
    for arg in "$@"; do
      if [[ "$arg" == /src/data/e2e-runs/* ]]; then
        run_dir="${MOCK_REPO_ROOT}/data/e2e-runs/${arg#/src/data/e2e-runs/}"
      fi
    done
    case "${MOCK_DOCKER_MODE:-passed}" in
      passed)
        mkdir -p "$run_dir"
        printf 'sample.spec.js\\t0\\t1\\t1 passed\\n' > "$run_dir/specs.tsv"
        echo passed > "$run_dir/result"
        echo 'false false 0' > "${MOCK_DOCKER_STATE}"
        ;;
      failed)
        mkdir -p "$run_dir"
        printf '1 failed\\n' > "$run_dir/sample.spec.js.log"
        printf 'sample.spec.js\\t1\\t1\\t1 failed\\n' > "$run_dir/specs.tsv"
        echo failed > "$run_dir/result"
        echo 'false false 1' > "${MOCK_DOCKER_STATE}"
        ;;
      oom)
        echo 'false true 137' > "${MOCK_DOCKER_STATE}"
        ;;
      timeout124)
        echo 'false false 124' > "${MOCK_DOCKER_STATE}"
        ;;
      timeout137)
        echo 'false false 137' > "${MOCK_DOCKER_STATE}"
        ;;
      lost)
        echo 'false false 1' > "${MOCK_DOCKER_STATE}"
        ;;
      running)
        echo 'true false 0' > "${MOCK_DOCKER_STATE}"
        ;;
    esac
    echo "mock-container-id"
    ;;
  inspect)
    if [[ -f "${MOCK_DOCKER_STATE}" ]]; then
      cat "${MOCK_DOCKER_STATE}"
      exit 0
    fi
    exit 1
    ;;
  rm)
    rm -f "${MOCK_DOCKER_STATE}"
    exit 0
    ;;
  *)
    exit 0
    ;;
esac
""",
        encoding="utf-8",
    )
    mock_docker.chmod(stat.S_IRWXU)
    return e2e_sh, docker_log, docker_state, tmp_path / "bin"


def test_e2e_runner_requires_claire_e2e_runner_and_supports_docker_states(tmp_path: Path) -> None:
    """E2E test for scripts/e2e.sh: CLAIRE_E2E_RUNNER validation, docker flags, state mapping, and stop."""
    e2e_sh, docker_log, _docker_state, mock_bin = _setup_e2e_sandbox(tmp_path)
    base_env = {
        "PATH": f"{mock_bin}:{os.environ.get('PATH', '')}",
        "MOCK_DOCKER_LOG": str(docker_log),
        "MOCK_DOCKER_STATE": str(tmp_path / "docker_state"),
        "MOCK_REPO_ROOT": str(tmp_path),
    }

    # --help works without CLAIRE_E2E_RUNNER.
    help_proc = subprocess.run(["bash", str(e2e_sh), "--help"], env=base_env, capture_output=True, text=True, check=False)
    assert help_proc.returncode == 0
    assert "CLAIRE_E2E_RUNNER" in help_proc.stdout

    # Missing or invalid CLAIRE_E2E_RUNNER fails clearly.
    missing_proc = subprocess.run(["bash", str(e2e_sh), "status"], env=base_env, capture_output=True, text=True, check=False)
    assert missing_proc.returncode != 0
    assert "CLAIRE_E2E_RUNNER" in missing_proc.stderr

    invalid_proc = subprocess.run(
        ["bash", str(e2e_sh), "status"],
        env={**base_env, "CLAIRE_E2E_RUNNER": "invalid"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert invalid_proc.returncode != 0
    assert "CLAIRE_E2E_RUNNER" in invalid_proc.stderr

    # Passed run checks docker run arguments, meta, and container file.
    passed = subprocess.run(
        ["bash", str(e2e_sh), "run"],
        env={**base_env, "CLAIRE_E2E_RUNNER": "docker", "MOCK_DOCKER_MODE": "passed"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert passed.returncode == 0, passed.stderr + passed.stdout
    latest = (tmp_path / "data" / "e2e-runs" / "latest").resolve()
    assert "runner=docker" in (latest / "meta").read_text(encoding="utf-8")
    assert (latest / "container").read_text(encoding="utf-8").strip() == "claire-e2e-8766"

    run_line = next(line for line in docker_log.read_text(encoding="utf-8").splitlines() if line.startswith("run "))
    for fragment in (
        "--name claire-e2e-8766",
        "--memory 2400m",
        "--memory-swap 2400m",
        "--pids-limit 512",
        f"-v {tmp_path}:/src",
        "-w /src",
        "-e CLAIRE_E2E_PORT=8766",
        "claire-toolbox:dev timeout -k 10 1200 bash /src/scripts/e2e.sh __worker /src/data/e2e-runs/",
    ):
        assert fragment in run_line

    # Failed, oom-kill, timeout (124 and 137), and lost states.
    for mode, expected_state, expected_rc in (
        ("failed", "failed", 1),
        ("oom", "oom-kill", 2),
        ("timeout124", "timeout", 2),
        ("timeout137", "timeout", 2),
        ("lost", "lost", 2),
    ):
        proc = subprocess.run(
            ["bash", str(e2e_sh), "run"],
            env={**base_env, "CLAIRE_E2E_RUNNER": "docker", "MOCK_DOCKER_MODE": mode},
            capture_output=True,
            text=True,
            check=False,
        )
        assert proc.returncode == expected_rc, f"mode={mode}: rc={proc.returncode}, out={proc.stdout}, err={proc.stderr}"
        assert expected_state in proc.stdout

    # Start running container, verify wait times out (rc=3), then stop (docker rm -f + stopped).
    start_proc = subprocess.run(
        ["bash", str(e2e_sh), "start"],
        env={**base_env, "CLAIRE_E2E_RUNNER": "docker", "MOCK_DOCKER_MODE": "running"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert start_proc.returncode == 0, start_proc.stderr

    wait_proc = subprocess.run(
        ["bash", str(e2e_sh), "wait", "1"],
        env={**base_env, "CLAIRE_E2E_RUNNER": "docker"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert wait_proc.returncode == 3

    stop_proc = subprocess.run(
        ["bash", str(e2e_sh), "stop"],
        env={**base_env, "CLAIRE_E2E_RUNNER": "docker"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert stop_proc.returncode == 0
    latest_stopped = (tmp_path / "data" / "e2e-runs" / "latest").resolve()
    assert (latest_stopped / "result").read_text(encoding="utf-8").strip() == "stopped"
    assert "rm -f claire-e2e-8766" in docker_log.read_text(encoding="utf-8")

    status_proc = subprocess.run(
        ["bash", str(e2e_sh), "status"],
        env={**base_env, "CLAIRE_E2E_RUNNER": "docker"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert status_proc.returncode == 0
    assert ": stopped" in status_proc.stdout
