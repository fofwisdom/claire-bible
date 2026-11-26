"""Contract tests for the deployment wiring of the Fedify sidecar (federation design, Sections 4.2 and 6.1).

The static tests read the Compose files, the sidecar Dockerfile, and the license table. The Compose model tests call `docker compose config` and are skipped when Docker is not installed.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SIDECAR_DIR = REPO_ROOT / "sidecar" / "fedify"
DOCKER = shutil.which("docker")
PERMITTED_LICENSES = re.compile(r"^(MIT|Apache-2\.0|ISC|BSD-[23]-Clause|0BSD)$")


def _fedify_block(name: str) -> str:
    """Return the lines of the service `fedify` in one Compose file, without the comments above the service key."""
    lines: list[str] = []
    found = False
    for line in (REPO_ROOT / name).read_text(encoding="utf-8").splitlines():
        if re.fullmatch(r"  fedify:\s*", line):
            found = True
            continue
        if found and re.match(r"  [^\s#]", line):
            break
        if found:
            lines.append(line)
    assert lines, f"{name} must define the service fedify"
    return "\n".join(lines)


def _compose_model(*, dev: bool) -> dict:
    argv = [DOCKER, "compose", "--project-directory", str(REPO_ROOT), "--env-file", str(REPO_ROOT / ".env.example")]
    env = {**os.environ, "CB_ENV_FILE": ".env.example"}
    for key in ("CB_DATA_DIR", "CB_UID", "CB_GID", "CB_API_BIND", "CB_API_PORT", "CB_FEDIFY_PORT", "CB_IMAGE_TAG", "CB_PROJECT_NAME", "CLAIRE_ENVIRONMENT"):
        env.pop(key, None)
    if dev:
        argv += ["--env-file", str(REPO_ROOT / ".env.dev.example")]
        env["CB_DEV_ENV_FILE"] = ".env.dev.example"
    argv += ["-f", str(REPO_ROOT / "docker-compose.yml")]
    if dev:
        argv += ["-f", str(REPO_ROOT / "docker-compose.dev.yml")]
    argv += ["config", "--format", "json"]
    result = subprocess.run(argv, cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr[-4000:]
    return json.loads(result.stdout)


@pytest.mark.parametrize("name", ["docker-compose.yml", "docker-compose.dev.yml"])
def test_fedify_service_text_has_only_the_permitted_mounts(name):
    block = _fedify_block(name)
    sources = re.findall(r'source:\s*"?([^"\n]+)"?', block)
    assert len(sources) == 3
    assert [source.rsplit("}", 1)[-1] for source in sources] == ["/run", "/fedify", "/auth/fedify_service.key"]
    assert block.count("create_host_path: false") == 3
    assert "read_only: true" in block
    for forbidden in ("claire.db", "/app/data", "/app/vault", "env_file", ".gemini", ".codex", "/host-bin"):
        assert forbidden not in block


def test_fedify_service_text_has_no_profile_and_the_claire_user():
    block = _fedify_block("docker-compose.yml")
    # Federation has no switch: the service starts with the other services.
    assert "profiles:" not in block
    assert 'user: "${CB_UID:-1000}:${CB_GID:-1000}"' in block
    assert "context: sidecar/fedify" in block
    assert "condition: service_healthy" in block


def test_sidecar_dockerfile_keeps_the_frozen_cache_and_the_limited_permissions():
    dockerfile = (SIDECAR_DIR / "Dockerfile").read_text(encoding="utf-8")
    command = next(line for line in dockerfile.splitlines() if line.startswith("CMD "))
    for flag in ('"--frozen"', '"--cached-only"', '"--allow-read=/data,/app"', '"--allow-write=/data/fedify,/data/run/federation.sock"'):
        assert flag in command
    assert '"--allow-all"' not in command and '"-A"' not in command
    assert '"--allow-write"' not in command and '"--allow-read"' not in command and '"--allow-env"' not in command
    assert "THIRD_PARTY_LICENSES.md" in dockerfile
    assert "/app/licenses" in dockerfile
    # The process does not run as the image user: all users must be able to read the module cache.
    assert 'chmod -R a+rX "$DENO_DIR" /app' in dockerfile


def test_license_table_lists_each_locked_dependency_with_a_permitted_license():
    lock = json.loads((SIDECAR_DIR / "deno.lock").read_text(encoding="utf-8"))
    locked: set[tuple[str, str]] = set()
    for key in lock["jsr"]:
        name, version = key.rsplit("@", 1)
        locked.add((name, version))
    for key in lock["npm"]:
        match = re.fullmatch(r"(@?[^@]+)@([^_]+)(?:_.*)?", key)
        assert match, key
        locked.add((match.group(1), match.group(2)))

    listed: dict[tuple[str, str], str] = {}
    for line in (SIDECAR_DIR / "THIRD_PARTY_LICENSES.md").read_text(encoding="utf-8").splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if line.startswith("| `") and len(cells) >= 3:
            listed[(cells[0].strip("`"), cells[1])] = cells[2]

    assert set(listed) == locked
    not_permitted = {key: value for key, value in listed.items() if not PERMITTED_LICENSES.fullmatch(value)}
    assert not not_permitted


@pytest.mark.skipif(DOCKER is None, reason="docker is not installed")
@pytest.mark.parametrize("dev", [False, True])
def test_compose_model_starts_fedify_with_the_other_services(dev):
    model = _compose_model(dev=dev)
    assert "fedify" in model["services"] and "profiles" not in model["services"]["fedify"]
    assert "api" in model["services"]


@pytest.mark.skipif(DOCKER is None, reason="docker is not installed")
@pytest.mark.parametrize(("dev", "data_dir", "host_port"), [(False, "data", None), (True, ".dev/data", "8791")])
def test_compose_model_mounts_only_socket_directory_database_directory_and_key_file(dev, data_dir, host_port):
    model = _compose_model(dev=dev)
    fedify = model["services"]["fedify"]
    data = REPO_ROOT / data_dir

    mounts = {volume["target"]: volume for volume in fedify["volumes"]}
    assert {target: volume["source"] for target, volume in mounts.items()} == {
        "/data/run": str(data / "run"),
        "/data/fedify": str(data / "fedify"),
        "/data/auth/fedify_service.key": str(data / "auth" / "fedify_service.key"),
    }
    assert all(volume["type"] == "bind" and volume["bind"]["create_host_path"] is False for volume in mounts.values())
    assert mounts["/data/auth/fedify_service.key"]["read_only"] is True

    assert fedify["user"] == "1000:1000"
    assert "env_file" not in fedify
    assert set(fedify["environment"]) == {"TZ", "CLAIRE_FEDIFY_SOCKET", "CLAIRE_FEDIFY_SERVICE_KEY_FILE", "CLAIRE_FEDIFY_DB", "CLAIRE_FEDIFY_HOST", "CLAIRE_FEDIFY_PORT"}
    assert fedify["build"]["context"] == str(SIDECAR_DIR)
    expected_ports = [("127.0.0.1", host_port, 8790)] if host_port else []
    assert [(port["host_ip"], str(port["published"]), port["target"]) for port in fedify.get("ports", [])] == expected_ports
    assert fedify["depends_on"]["api"]["condition"] == "service_healthy"


@pytest.mark.skipif(DOCKER is None, reason="docker is not installed")
def test_compose_model_of_the_development_network_keeps_the_mount_rule_and_has_no_route_out_for_the_sidecar(tmp_path):
    """The overlay of a server of the development network (ops/dev/devnet/compose.server.yml) with the environment of ``./cb-manuscript dev devnet up``: the sidecar keeps the mounts of FEDERATION.md Section 9.1 and gets only the certificate of the private authority; the sidecar has no route out; no service has a host port."""
    from ops import devnet

    assert DOCKER is not None
    net = devnet.Devnet(REPO_ROOT)
    net.run_dir = tmp_path / "devnet"
    env = net._server_env("a")
    net.run_dir.mkdir()
    (net.run_dir / "a.env").write_text("CLAIRE_FQDN=a.claire.test\n", encoding="utf-8")
    result = subprocess.run([DOCKER, "compose", "-f", str(REPO_ROOT / "docker-compose.yml"), "-f", str(net.ops / "compose.server.yml"), "--profile", "bot", "config", "--format", "json"], cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr[-4000:]
    model = json.loads(result.stdout)
    fedify = model["services"]["fedify"]
    data = net.run_dir / "a" / "data"
    mounts = {volume["target"]: volume for volume in fedify["volumes"]}
    assert {target: volume["source"] for target, volume in mounts.items()} == {
        "/data/run": str(data / "run"),
        "/data/fedify": str(data / "fedify"),
        "/data/auth/fedify_service.key": str(data / "auth" / "fedify_service.key"),
        "/tls/ca.crt": str(net.run_dir / "tls" / "ca" / "ca.crt"),
    }
    assert mounts["/data/auth/fedify_service.key"]["read_only"] is True and mounts["/tls/ca.crt"]["read_only"] is True
    assert "env_file" not in fedify and fedify["environment"]["DENO_CERT"] == "/tls/ca.crt"
    # The sidecar is only on internal networks; the Claire services have the route out.
    assert set(fedify["networks"]) == {"default", "edge"}
    assert model["networks"]["default"]["internal"] is True and model["networks"]["edge"]["name"] == "claire-devnet-edge"
    assert set(model["services"]["api"]["networks"]) == {"default", "edge", "out"}
    assert model["services"]["edge"]["networks"]["edge"]["aliases"] == ["a-edge"]
    assert all(not service.get("ports") for service in model["services"].values())
    assert model["services"]["bot"]["profiles"] == ["bot"]
