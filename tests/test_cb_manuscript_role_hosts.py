"""Role host and development port wiring checks in cb-manuscript (design §5.1, O13/O14).

No Docker daemon, Git, or network is used except the optional ``docker compose config``
model checks, which only read the repository Compose files.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from ops import cb_manuscript as cb

REPO_ROOT = Path(__file__).resolve().parent.parent
OWNER_TOKEN = "owner-" + ("x" * 32)


@pytest.fixture(autouse=True)
def _isolate_environment(monkeypatch):
    # Host shells may carry CLAIRE_* / CB_* values that cb-manuscript reads with priority.
    for key in list(os.environ):
        if key.startswith(("CLAIRE_", "CB_")):
            monkeypatch.delenv(key, raising=False)


def _write_env(root: Path, *, prod: dict[str, str], dev: dict[str, str] | None = None) -> cb.Layout:
    base = {
        "CLAIRE_ENVIRONMENT": "production",
        "CB_API_BIND": "127.0.0.1",
        "CB_API_PORT": "8765",
        "CLAIRE_FQDN": "claire.example.com",
        "CLAIRE_ANONYMOUS_READONLY": "1",
        "CLAIRE_INJECT_TOKEN": OWNER_TOKEN,
    }
    base.update(prod)
    (root / ".env").write_text(
        "".join(f"{key}={value}\n" for key, value in base.items()), encoding="utf-8"
    )
    if dev is not None:
        overlay = {
            "CLAIRE_ENVIRONMENT": "development",
            "CB_API_BIND": "127.0.0.1",
            "CB_API_PORT": "8766",
            "CLAIRE_FQDN": "127.0.0.1:8766",
            "CLAIRE_ANONYMOUS_READONLY": "1",
        }
        overlay.update(dev)
        (root / ".env.dev").write_text(
            "".join(f"{key}={value}\n" for key, value in overlay.items()),
            encoding="utf-8",
        )
    return cb.Layout(root)


def _load_dev(tmp_path: Path, **dev: str) -> cb.Runtime:
    return cb.load_runtime(_write_env(tmp_path, prod={}, dev=dev), legacy_dev=True)


def _load_prod(tmp_path: Path, **prod: str) -> cb.Runtime:
    return cb.load_runtime(_write_env(tmp_path, prod=prod))


# --- development -----------------------------------------------------------------


def test_development_without_role_hosts_is_unchanged(tmp_path):
    runtime = _load_dev(tmp_path)
    assert runtime.environment == cb.DEVELOPMENT


def test_development_accepts_wired_role_hosts(tmp_path):
    runtime = _load_dev(
        tmp_path,
        CB_USERCONTENT_PORT="8767",
        CLAIRE_USERCONTENT_FQDN="127.0.0.1:8767",
        CB_MEDIA_PORT="8768",
        CLAIRE_MEDIA_FQDN="127.0.0.1:8768",
    )
    assert runtime.values["CLAIRE_USERCONTENT_FQDN"] == "127.0.0.1:8767"


def test_development_port_without_role_host_is_allowed(tmp_path):
    _load_dev(tmp_path, CB_USERCONTENT_PORT="8767")


def test_development_overlay_blank_clears_production_role_hosts(tmp_path):
    layout = _write_env(
        tmp_path,
        prod={
            "CLAIRE_SITE_DOMAIN": "example.com",
            "CLAIRE_USERCONTENT_FQDN": "usercontent.example.com",
        },
        dev={"CLAIRE_USERCONTENT_FQDN": "", "CLAIRE_SITE_DOMAIN": ""},
    )
    cb.load_runtime(layout, legacy_dev=True)


def test_development_inherited_production_role_host_is_rejected(tmp_path):
    layout = _write_env(
        tmp_path,
        prod={
            "CLAIRE_SITE_DOMAIN": "example.com",
            "CLAIRE_USERCONTENT_FQDN": "usercontent.example.com",
        },
        dev={},
    )
    with pytest.raises(cb.ManuscriptError, match="CLAIRE_USERCONTENT_FQDN in development requires CB_USERCONTENT_PORT"):
        cb.load_runtime(layout, legacy_dev=True)


@pytest.mark.parametrize(
    ("settings", "message"),
    (
        (
            {"CLAIRE_USERCONTENT_FQDN": "127.0.0.1:8767"},
            "CLAIRE_USERCONTENT_FQDN in development requires CB_USERCONTENT_PORT",
        ),
        (
            {"CB_USERCONTENT_PORT": "8767", "CLAIRE_USERCONTENT_FQDN": "192.168.0.10:8767"},
            r"CLAIRE_USERCONTENT_FQDN in development must be 127\.0\.0\.1:8767",
        ),
        (
            {"CB_USERCONTENT_PORT": "8767", "CLAIRE_USERCONTENT_FQDN": "127.0.0.1:8769"},
            r"CLAIRE_USERCONTENT_FQDN in development must be 127\.0\.0\.1:8767",
        ),
        (
            {"CB_USERCONTENT_PORT": "8767", "CLAIRE_USERCONTENT_FQDN": "127.0.0.1"},
            r"CLAIRE_USERCONTENT_FQDN in development must be 127\.0\.0\.1:8767",
        ),
        (
            {"CB_USERCONTENT_PORT": "8767", "CLAIRE_USERCONTENT_FQDN": "http://127.0.0.1:8767"},
            "without a scheme or path",
        ),
        (
            {"CB_USERCONTENT_PORT": "8766", "CLAIRE_USERCONTENT_FQDN": "127.0.0.1:8766"},
            r"CB_USERCONTENT_PORT \(8766\) must differ from CB_API_PORT",
        ),
        (
            {"CB_MEDIA_PORT": "8766"},
            r"CB_MEDIA_PORT \(8766\) must differ from CB_API_PORT",
        ),
        (
            {
                "CB_USERCONTENT_PORT": "8767",
                "CLAIRE_USERCONTENT_FQDN": "127.0.0.1:8767",
                "CB_MEDIA_PORT": "8767",
                "CLAIRE_MEDIA_FQDN": "127.0.0.1:8767",
            },
            r"CB_MEDIA_PORT \(8767\) must differ from CB_USERCONTENT_PORT",
        ),
        (
            {"CLAIRE_MEDIA_FQDN": "127.0.0.1:8768"},
            "CLAIRE_MEDIA_FQDN in development requires CB_MEDIA_PORT",
        ),
        ({"CB_USERCONTENT_PORT": "abc"}, "CB_USERCONTENT_PORT must be an integer"),
        ({"CB_MEDIA_PORT": "70000"}, "CB_MEDIA_PORT must be in range 1-65535"),
        (
            {"CB_USERCONTENT_PORT": "8767", "CLAIRE_USERCONTENT_FQDN": "\" 127.0.0.1:8767\""},
            "leading or trailing whitespace",
        ),
    ),
)
def test_development_role_host_failures(tmp_path, settings, message):
    with pytest.raises(cb.ManuscriptError, match=message):
        _load_dev(tmp_path, **settings)


def test_development_role_port_from_process_environment_wins(tmp_path, monkeypatch):
    # Compose interpolation lets process env override --env-file, so validation follows it.
    monkeypatch.setenv("CB_USERCONTENT_PORT", "8770")
    with pytest.raises(cb.ManuscriptError, match=r"must be 127\.0\.0\.1:8770"):
        _load_dev(
            tmp_path,
            CB_USERCONTENT_PORT="8767",
            CLAIRE_USERCONTENT_FQDN="127.0.0.1:8767",
        )


def test_compose_environment_drops_host_role_host_values(tmp_path, monkeypatch):
    runtime = _load_dev(tmp_path)
    monkeypatch.setenv("CLAIRE_USERCONTENT_FQDN", "evil.example")
    monkeypatch.setenv("CLAIRE_MEDIA_FQDN", "evil.example")
    monkeypatch.setenv("CLAIRE_PROTOTYPE_FQDN", "evil.example")
    monkeypatch.setenv("CLAIRE_SITE_DOMAIN", "example")
    env = runtime.compose_environment()
    for key in ("CLAIRE_USERCONTENT_FQDN", "CLAIRE_MEDIA_FQDN", "CLAIRE_PROTOTYPE_FQDN", "CLAIRE_SITE_DOMAIN"):
        assert key not in env


# --- production ------------------------------------------------------------------


@pytest.mark.parametrize(
    "settings",
    (
        {},
        {"CLAIRE_SITE_DOMAIN": "example.com"},
        {
            "CLAIRE_SITE_DOMAIN": "example.com",
            "CLAIRE_USERCONTENT_FQDN": "usercontent.example.com",
        },
        {
            "CLAIRE_SITE_DOMAIN": "Example.COM",
            "CLAIRE_USERCONTENT_FQDN": "UserContent.example.com",
            "CLAIRE_MEDIA_FQDN": "media.example.com",
        },
        # A role host under the app host is not a parent of it.
        {
            "CLAIRE_SITE_DOMAIN": "example.com",
            "CLAIRE_USERCONTENT_FQDN": "u.claire.example.com",
        },
        # The app host may be the registrable domain itself.
        {
            "CLAIRE_FQDN": "example.com",
            "CLAIRE_SITE_DOMAIN": "example.com",
            "CLAIRE_USERCONTENT_FQDN": "usercontent.example.com",
        },
        # Production ignores development host ports.
        {
            "CLAIRE_SITE_DOMAIN": "example.com",
            "CLAIRE_USERCONTENT_FQDN": "usercontent.example.com",
            "CB_USERCONTENT_PORT": "8765",
        },
    ),
)
def test_production_accepts_valid_role_hosts(tmp_path, settings):
    runtime = _load_prod(tmp_path, **settings)
    assert runtime.environment == cb.PRODUCTION


def test_production_accepts_legacy_public_url_app_host(tmp_path):
    layout = _write_env(
        tmp_path,
        prod={
            "CLAIRE_FQDN": "",
            "CLAIRE_PUBLIC_URL": "https://claire.example.com/",
            "CLAIRE_SITE_DOMAIN": "example.com",
            "CLAIRE_USERCONTENT_FQDN": "usercontent.example.com",
        },
    )
    cb.load_runtime(layout)


@pytest.mark.parametrize(
    ("settings", "message"),
    (
        (
            {"CLAIRE_USERCONTENT_FQDN": "usercontent.example.com"},
            "CLAIRE_SITE_DOMAIN is required in production when CLAIRE_USERCONTENT_FQDN is set",
        ),
        (
            {"CLAIRE_MEDIA_FQDN": "media.example.com"},
            "CLAIRE_SITE_DOMAIN is required in production when CLAIRE_MEDIA_FQDN is set",
        ),
        (
            {"CLAIRE_SITE_DOMAIN": "example.com", "CLAIRE_USERCONTENT_FQDN": "usercontent.other.com"},
            r"CLAIRE_USERCONTENT_FQDN \(usercontent\.other\.com\) must be example\.com or one of its subdomains",
        ),
        (
            {"CLAIRE_SITE_DOMAIN": "example.com", "CLAIRE_MEDIA_FQDN": "badexample.com"},
            r"CLAIRE_MEDIA_FQDN \(badexample\.com\) must be example\.com or one of its subdomains",
        ),
        (
            {"CLAIRE_SITE_DOMAIN": "other.com", "CLAIRE_USERCONTENT_FQDN": "usercontent.other.com"},
            r"CLAIRE_FQDN \(claire\.example\.com\) must be other\.com or one of its subdomains",
        ),
        (
            {"CLAIRE_SITE_DOMAIN": "example.com", "CLAIRE_USERCONTENT_FQDN": "claire.example.com"},
            r"CLAIRE_USERCONTENT_FQDN \(claire\.example\.com\) must differ from CLAIRE_FQDN",
        ),
        (
            {
                "CLAIRE_SITE_DOMAIN": "example.com",
                "CLAIRE_USERCONTENT_FQDN": "assets.example.com",
                "CLAIRE_MEDIA_FQDN": "ASSETS.example.com",
            },
            r"CLAIRE_MEDIA_FQDN \(assets\.example\.com\) must differ from CLAIRE_USERCONTENT_FQDN",
        ),
        (
            {"CLAIRE_SITE_DOMAIN": "example.com", "CLAIRE_USERCONTENT_FQDN": "example.com"},
            r"CLAIRE_USERCONTENT_FQDN \(example\.com\) cannot be a parent domain of the app host",
        ),
        (
            {
                "CLAIRE_FQDN": "claire.app.example.com",
                "CLAIRE_SITE_DOMAIN": "example.com",
                "CLAIRE_MEDIA_FQDN": "app.example.com",
            },
            r"CLAIRE_MEDIA_FQDN \(app\.example\.com\) cannot be a parent domain",
        ),
        (
            {"CLAIRE_SITE_DOMAIN": "example.com", "CLAIRE_USERCONTENT_FQDN": "usercontent.example.com:8443"},
            "Invalid DNS hostname format for CLAIRE_USERCONTENT_FQDN",
        ),
        (
            {"CLAIRE_SITE_DOMAIN": "example.com", "CLAIRE_USERCONTENT_FQDN": "*.example.com"},
            "CLAIRE_USERCONTENT_FQDN requires an exact DNS hostname",
        ),
        (
            {"CLAIRE_SITE_DOMAIN": "example.com", "CLAIRE_USERCONTENT_FQDN": "203.0.113.5"},
            "CLAIRE_USERCONTENT_FQDN requires a DNS hostname, not an IP address",
        ),
        (
            {"CLAIRE_SITE_DOMAIN": "example.com", "CLAIRE_USERCONTENT_FQDN": "https://usercontent.example.com"},
            "without a scheme or path",
        ),
        ({"CLAIRE_SITE_DOMAIN": "example.com."}, "CLAIRE_SITE_DOMAIN requires an exact DNS hostname"),
        ({"CLAIRE_SITE_DOMAIN": '" example.com"'}, "CLAIRE_SITE_DOMAIN cannot have leading or trailing whitespace"),
    ),
)
def test_production_role_host_failures(tmp_path, settings, message):
    with pytest.raises(cb.ManuscriptError, match=message):
        _load_prod(tmp_path, **settings)


def test_role_host_failure_stops_command_before_docker(tmp_path, monkeypatch):
    _write_env(tmp_path, prod={"CLAIRE_USERCONTENT_FQDN": "usercontent.example.com"})
    (tmp_path / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    calls: list[object] = []
    monkeypatch.setattr(cb.subprocess, "run", lambda *a, **k: calls.append(a))
    assert cb.main(["status"], root=tmp_path) == 2
    assert calls == []


# --- Compose wiring --------------------------------------------------------------


def test_repository_templates_validate(tmp_path):
    shutil.copy(REPO_ROOT / ".env.example", tmp_path / ".env")
    shutil.copy(REPO_ROOT / ".env.dev.example", tmp_path / ".env.dev")
    for name in (".env", ".env.dev"):
        path = tmp_path / name
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                "CLAIRE_INJECT_TOKEN=\n", f"CLAIRE_INJECT_TOKEN={OWNER_TOKEN}\n"
            ),
            encoding="utf-8",
        )
    layout = cb.Layout(tmp_path)
    for key in ("CLAIRE_SITE_DOMAIN", "CLAIRE_USERCONTENT_FQDN", "CLAIRE_MEDIA_FQDN", "CLAIRE_PROTOTYPE_FQDN"):
        assert key in cb.read_dotenv(layout.env)
        assert key in cb.read_dotenv(layout.dev_env)
    for key in ("CB_USERCONTENT_PORT", "CB_MEDIA_PORT", "CB_PROTOTYPE_PORT"):
        assert key in cb.read_dotenv(layout.dev_env)
    assert cb.load_runtime(layout).environment == cb.PRODUCTION
    assert cb.load_runtime(layout, legacy_dev=True).environment == cb.DEVELOPMENT


def test_compose_files_publish_role_ports_only_in_development():
    prod = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    dev = (REPO_ROOT / "docker-compose.dev.yml").read_text(encoding="utf-8")
    assert "CB_USERCONTENT_PORT" not in prod and "CB_MEDIA_PORT" not in prod and "CB_PROTOTYPE_PORT" not in prod
    assert "${CB_USERCONTENT_PORT:-${CB_API_PORT" in dev
    assert "${CB_MEDIA_PORT:-${CB_API_PORT" in dev
    assert "${CB_PROTOTYPE_PORT:-${CB_API_PORT" in dev
    # Stale dev images must not outlive pyproject/uv.lock changes.
    assert "pull_policy: build" in dev


def _compose_ports(tmp_path: Path, extra: dict[str, str]) -> dict[str, list[tuple[str, str, int]]]:
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(tmp_path),
        "DOCKER_CONFIG": os.environ.get("DOCKER_CONFIG", str(Path.home() / ".docker")),
        "CB_ENV_FILE": ".env.example",
        "CB_DEV_ENV_FILE": ".env.dev.example",
        **extra,
    }
    result = subprocess.run(
        [
            "docker", "compose",
            "--env-file", ".env.example", "--env-file", ".env.dev.example",
            "-f", "docker-compose.yml", "-f", "docker-compose.dev.yml",
            "config", "--format", "json",
        ],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    model = json.loads(result.stdout)
    return {
        name: [(p["host_ip"], str(p["published"]), int(p["target"])) for p in svc.get("ports", [])]
        for name, svc in model["services"].items()
    }


def _has_compose() -> bool:
    if shutil.which("docker") is None:
        return False
    probe = subprocess.run(["docker", "compose", "version"], capture_output=True)
    return probe.returncode == 0


@pytest.mark.skipif(not _has_compose(), reason="docker compose is not available")
def test_dev_compose_model_wires_role_ports_to_api_port(tmp_path):
    ports = _compose_ports(tmp_path, {})
    assert ports["api"] == [("127.0.0.1", "8766", 8766)]

    ports = _compose_ports(tmp_path, {"CB_USERCONTENT_PORT": "8767"})
    assert ports["api"] == [("127.0.0.1", "8766", 8766), ("127.0.0.1", "8767", 8766)]

    ports = _compose_ports(tmp_path, {"CB_USERCONTENT_PORT": "8767", "CB_MEDIA_PORT": "8768"})
    assert ports["api"] == [
        ("127.0.0.1", "8766", 8766),
        ("127.0.0.1", "8767", 8766),
        ("127.0.0.1", "8768", 8766),
    ]
    for worker in ("refresh", "recover", "expand"):
        assert ports[worker] == []
