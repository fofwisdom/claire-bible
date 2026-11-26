"""Static and integration contract tests for Docker Compose mounts and container isolation.

These tests ensure that:
1. Optional host tools (/host-bin) stay connected read-only, and no host CLI credentials are mounted.
2. Custom CA certificate mounting (/extra-certs) is strictly preserved.
3. Host-specific Ubuntu system paths (/etc/ssl/certs, /etc/localtime, etc.) do NOT regress.
4. Dockerfile environment (PATH with /host-bin, gosu installation) is strictly enforced.
"""

from __future__ import annotations

from pathlib import Path
import re
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_docker_compose_mounts_host_bin():
    """Verify /host-bin:ro is mounted in both production and development Compose configs."""
    prod_compose = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    dev_compose = (REPO_ROOT / "docker-compose.dev.yml").read_text(encoding="utf-8")

    assert "/host-bin:ro" in prod_compose, "docker-compose.yml must mount host binaries at /host-bin:ro"
    assert "/host-bin:ro" in dev_compose, "docker-compose.dev.yml must mount host binaries at /host-bin:ro"


def test_docker_compose_mounts_no_host_cli_credentials():
    """The host-cli providers are retired (AED8): the containers get no login directory of a host CLI."""
    prod_compose = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    dev_compose = (REPO_ROOT / "docker-compose.dev.yml").read_text(encoding="utf-8")
    entrypoint = (REPO_ROOT / "scripts" / "docker-entrypoint.sh").read_text(encoding="utf-8")

    for text in ("/root/.gemini", "/root/.codex", "CB_GEMINI_DIR", "CB_CODEX_DIR"):
        assert text not in prod_compose, f"docker-compose.yml must not mount {text}"
        assert text not in dev_compose, f"docker-compose.dev.yml must not mount {text}"
        assert text not in entrypoint


def test_docker_compose_mounts_custom_ca():
    """Verify /extra-certs:ro is mounted for custom/private CA trust injection."""
    prod_compose = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    dev_compose = (REPO_ROOT / "docker-compose.dev.yml").read_text(encoding="utf-8")

    assert "/extra-certs:ro" in prod_compose, "docker-compose.yml must mount custom certs at /extra-certs:ro"
    assert "/extra-certs:ro" in dev_compose, "docker-compose.dev.yml must mount custom certs at /extra-certs:ro"


def test_docker_compose_no_ubuntu_system_mounts():
    """Ensure host Ubuntu system paths are NOT mounted (preventing certificate shadowing)."""
    prod_compose = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    dev_compose = (REPO_ROOT / "docker-compose.dev.yml").read_text(encoding="utf-8")

    forbidden_patterns = [
        r"- /etc/ssl/certs",
        r"- /etc/ca-certificates",
        r"- /usr/local/share/ca-certificates",
        r"- /etc/localtime",
    ]

    for pat in forbidden_patterns:
        assert not re.search(pat, prod_compose), f"docker-compose.yml must not mount host system path matching {pat}"
        assert not re.search(pat, dev_compose), f"docker-compose.dev.yml must not mount host system path matching {pat}"


def test_dockerfile_path_contains_host_bin():
    """Verify Dockerfile PATH places /host-bin after $PATH so host tools do not hide image tools."""
    dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    match = re.search(r'PATH="([^"]*)"', dockerfile)
    assert match is not None, "Dockerfile must define PATH"
    entries = match.group(1).split(":")
    assert "/app/.venv/bin" in entries
    assert "$PATH" in entries
    assert "/host-bin" in entries
    assert entries.index("$PATH") < entries.index("/host-bin"), "Dockerfile PATH must place /host-bin after $PATH"


def test_dockerfile_installs_gosu():
    """Verify Dockerfile installs gosu for privilege drop."""
    dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "gosu" in dockerfile, "Dockerfile must install gosu package"


def test_docker_entrypoint_privilege_drop():
    """Verify entrypoint script includes CA update and gosu privilege drop."""
    entrypoint = (REPO_ROOT / "scripts" / "docker-entrypoint.sh").read_text(encoding="utf-8")
    assert "update-ca-certificates" in entrypoint, "docker-entrypoint.sh must update CA certificates"
    assert "gosu" in entrypoint, "docker-entrypoint.sh must use gosu for privilege drop"
    assert "CB_UID" in entrypoint, "docker-entrypoint.sh must support CB_UID"
    assert "CB_GID" in entrypoint, "docker-entrypoint.sh must support CB_GID"


def test_ci_script_volume_assertions():
    """Verify scripts/ci.sh validates /host-bin:ro, /extra-certs:ro and bans host Ubuntu paths."""
    ci_script = (REPO_ROOT / "scripts" / "ci.sh").read_text(encoding="utf-8")
    assert "grep -q '/host-bin:ro' docker-compose.yml" in ci_script
    assert "grep -q '/host-bin:ro' docker-compose.dev.yml" in ci_script
    assert "grep -q '/extra-certs:ro' docker-compose.yml" in ci_script
    assert "grep -q '/extra-certs:ro' docker-compose.dev.yml" in ci_script
    assert "Host Ubuntu system paths must not be mounted" in ci_script


def test_preflight_does_not_check_host_cli_binaries(tmp_path):
    """The host-cli providers are retired (AED8). An old CLAIRE_PROVIDER=antigravity does not make the preflight look for agy or for a /host-bin mount."""
    import subprocess
    from unittest.mock import patch

    import ops.cb_manuscript as cb

    env_content = (
        "CLAIRE_ENVIRONMENT=production\n"
        "CB_API_BIND=127.0.0.1\n"
        "CLAIRE_FQDN=localhost\n"
        "CLAIRE_PROVIDER=antigravity\n"
        "CLAIRE_AGY_BIN=nonexistent_agy\n"
        "CLAIRE_INJECT_TOKEN=" + "a" * 32 + "\n"
    )
    (tmp_path / ".env").write_text(env_content, encoding="utf-8")
    (tmp_path / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    (tmp_path / "certs").mkdir()
    (tmp_path / "vault").mkdir()
    (tmp_path / "data").mkdir()

    runtime = cb.load_runtime(cb.Layout(tmp_path))
    with patch.object(cb.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)):
        assert cb.command_preflight(runtime) == 0
