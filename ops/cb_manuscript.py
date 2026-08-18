#!/usr/bin/env python3
"""One entry point for Claire's local and remote container operations.

The wrapper intentionally treats dotenv files as data.  It never sources them,
and every external command is passed to ``subprocess`` as an argv sequence.
"""

from __future__ import annotations

import argparse
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import fcntl
import hashlib
import ipaddress
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shutil
import sqlite3
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
from typing import Iterator, Mapping, Sequence
from urllib.parse import quote, urlsplit


VERSION = "0.2.0"
DEFAULT_PROJECT = "claire-bible"
DEFAULT_DEV_PROJECT = "claire-bible-dev"
DEFAULT_WAIT_TIMEOUT = 120
ENVIRONMENT_KEY = "CLAIRE_ENVIRONMENT"
ANONYMOUS_READONLY_KEY = "CLAIRE_ANONYMOUS_READONLY"
DEVELOPMENT = "development"
PRODUCTION = "production"
ENVIRONMENTS = frozenset((DEVELOPMENT, PRODUCTION))
BACKUP_FORMAT_VERSION = 1
BACKUP_COMPONENTS = ("data", "vault")
BACKUP_ARCHIVE_SUFFIX = ".tar.gz"
BACKUP_ID_RE = re.compile(r"^cb-[0-9]{8}$")
PROJECT_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
DNS_LABEL_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")
WEB_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{32,128}$")
LEGACY_CONTAINERS = (
    "claire_bot",
    "claire_api",
    "claire_refresh",
    "claire_recover",
    "claire_expand",
    "claire_backup",
)
LOCKED_PASSTHROUGH_COMMANDS = {
    "up",
    "down",
    "restart",
    "shell",
    "app",
    "compose",
}
PASSTHROUGH_COMMANDS = {
    "up",
    "down",
    "restart",
    "status",
    "logs",
    "shell",
    "app",
    "compose",
}
PASSTHROUGH_HELP = {
    "up": "Start Compose services",
    "down": "Stop and remove Compose services",
    "restart": "Restart Compose services",
    "status": "Display Compose services status",
    "logs": "Display Compose services logs",
    "shell": "Execute shell command in a running service",
    "app": "Run one-off claire command in deployment environment (--advanced bypasses guards)",
    "compose": "Pass advanced arguments directly to Docker Compose",
}
APP_ADVANCED_OPTION = "--advanced"
APP_ONE_OFF_COMMANDS = {
    "doctor",
    "health",
    "liveness",
    "status",
    "stats",
    "replay-failed",
    "recover-run",
    "refresh-mark",
    "refresh-run",
    "expand-run",
    "ingest",
    "search",
    "backfill-detail",
    "backfill-images",
    "watch",
    "dedup-scan",
    "dedup-merge",
    "recanonicalize",
}
APP_GUARDED_COMMANDS = {
    "migrate": "Schema lifecycle command owned by install/update",
    "bot": "Persistent service owned by Compose",
    "serve-api": "Persistent service owned by Compose",
    "recover-loop": "Persistent service owned by Compose",
    "refresh-loop": "Persistent service owned by Compose",
    "expand-loop": "Persistent service owned by Compose",
    "reextract": "Destructive maintenance command that rebuilds the graph",
}


class ManuscriptError(RuntimeError):
    """Expected operator-facing error."""

    def __init__(self, message: str, exit_code: int = 2):
        super().__init__(message)
        self.exit_code = exit_code


class CommandError(ManuscriptError):
    """An external command returned a failure code."""

    def __init__(self, argv: Sequence[str], returncode: int):
        self.argv = tuple(argv)
        self.returncode = returncode
        super().__init__(
            f"Command failed (exit {returncode}): {_display_argv(argv)}",
            exit_code=returncode or 1,
        )


def _display_argv(argv: Sequence[str]) -> str:
    """Render argv for diagnostics without invoking a shell."""

    return " ".join(str(part) for part in argv)


@dataclass(frozen=True)
class Layout:
    root: Path

    @property
    def env(self) -> Path:
        return self.root / ".env"

    @property
    def dev_env(self) -> Path:
        return self.root / ".env.dev"

    @property
    def env_example(self) -> Path:
        return self.root / ".env.example"

    @property
    def dev_env_example(self) -> Path:
        return self.root / ".env.dev.example"

    @property
    def compose(self) -> Path:
        for name in ("docker-compose.yml", "compose.yaml", "compose.yml"):
            candidate = self.root / name
            if candidate.is_file():
                return candidate
        return self.root / "docker-compose.yml"

    @property
    def dev_compose(self) -> Path:
        for name in (
            "docker-compose.dev.yml",
            "compose.dev.yaml",
            "compose.dev.yml",
        ):
            candidate = self.root / name
            if candidate.is_file():
                return candidate
        return self.root / "docker-compose.dev.yml"

    @property
    def deploy_script(self) -> Path:
        return self.root / "deploy.sh"

    @property
    def data(self) -> Path:
        return self.root / "data"

    @property
    def vault(self) -> Path:
        return self.root / "vault"

    @property
    def backups(self) -> Path:
        return self.root / "backups"

    @property
    def state_dir(self) -> Path:
        return self.root / ".cb-manuscript"

    @property
    def production_state(self) -> Path:
        return self.state_dir / "production.json"

    @property
    def development_state(self) -> Path:
        return self.state_dir / "development.json"

    def state_for(self, *, dev: bool) -> Path:
        return self.development_state if dev else self.production_state


@dataclass(frozen=True)
class Runtime:
    layout: Layout
    environment: str
    values: Mapping[str, str]
    project: str
    wait_timeout: int
    bot_enabled: bool
    anonymous_readonly: bool

    @property
    def dev(self) -> bool:
        return self.environment == DEVELOPMENT

    @property
    def env_files(self) -> tuple[Path, ...]:
        if self.dev:
            return (self.layout.env, self.layout.dev_env)
        return (self.layout.env,)

    @property
    def compose_files(self) -> tuple[Path, ...]:
        if self.dev:
            return (self.layout.compose, self.layout.dev_compose)
        return (self.layout.compose,)

    def compose_argv(self, *args: str, all_profiles: bool = False) -> list[str]:
        argv = [
            "docker",
            "compose",
            "--project-directory",
            str(self.layout.root),
            "-p",
            self.project,
        ]
        for env_file in self.env_files:
            argv.extend(("--env-file", str(env_file)))
        for compose_file in self.compose_files:
            argv.extend(("-f", str(compose_file)))
        if all_profiles:
            argv.extend(("--profile", "*"))
        elif self.bot_enabled:
            argv.extend(("--profile", "bot"))
        argv.extend(args)
        return argv

def detect_host_antigravity_paths(
    values: Mapping[str, str] | None = None,
) -> tuple[str, str]:
    """Detect host agy binary location and .gemini credentials directory."""
    agy_name = "agy"
    if values:
        agy_name = values.get("CLAIRE_AGY_BIN", "").strip() or "agy"

    # 1. agy binary directory
    host_bin_dir = str(Path.home() / ".local" / "bin")
    agy_path: str | None = None

    if Path(agy_name).is_file() and os.access(agy_name, os.X_OK):
        agy_path = str(Path(agy_name).resolve())
    else:
        which_path = shutil.which(agy_name)
        if which_path and Path(which_path).is_file() and os.access(which_path, os.X_OK):
            agy_path = str(Path(which_path).resolve())
        else:
            candidates = [
                Path("/usr/local/bin") / agy_name,
                Path("/usr/bin") / agy_name,
                Path.home() / ".local" / "bin" / agy_name,
                Path("/root/.local/bin") / agy_name,
            ]
            home_root = Path("/home")
            if home_root.is_dir():
                try:
                    for u in home_root.iterdir():
                        if u.is_dir():
                            candidates.append(u / ".local" / "bin" / agy_name)
                except Exception:
                    pass
            for c in candidates:
                if c.is_file() and os.access(c, os.X_OK):
                    agy_path = str(c.resolve())
                    break

    if agy_path:
        host_bin_dir = str(Path(agy_path).parent)

    # 2. .gemini credentials directory
    host_gemini_dir = str(Path.home() / ".gemini")
    gemini_candidates = [Path.home() / ".gemini", Path("/root/.gemini")]
    home_root = Path("/home")
    if home_root.is_dir():
        try:
            for u in home_root.iterdir():
                if u.is_dir():
                    gemini_candidates.append(u / ".gemini")
        except Exception:
            pass

    for gc in gemini_candidates:
        if gc.is_dir():
            try:
                # Select directory that actually contains subfiles or settings
                if any(gc.iterdir()):
                    host_gemini_dir = str(gc.resolve())
                    break
            except Exception:
                pass

    return host_bin_dir, host_gemini_dir


    def compose_environment(self) -> dict[str, str]:
        env = os.environ.copy()
        # The three security boundary values are passed into the actual container by service env_file.
        # Prevent host process values of the same name from interfering with Compose interpolation.
        env.pop("CLAIRE_PUBLIC_URL", None)
        env.pop("CLAIRE_CORS_ALLOWED_ORIGINS", None)
        env.pop(ANONYMOUS_READONLY_KEY, None)
        env[ENVIRONMENT_KEY] = self.environment
        env["CB_ENV_FILE"] = str(self.layout.env.resolve())
        if self.dev:
            env["CB_DEV_ENV_FILE"] = str(self.layout.dev_env.resolve())
        else:
            env.pop("CB_DEV_ENV_FILE", None)

        tz_value = self.values.get("TZ", "").strip() or env.get("TZ", "").strip()
        if not tz_value:
            tz_value = _detect_system_timezone()
        if tz_value:
            env["TZ"] = tz_value

        host_bin_dir, host_gemini_dir = detect_host_antigravity_paths(self.values)
        env["CB_BIN_DIR"] = env.get("CB_BIN_DIR", "").strip() or host_bin_dir
        env["CB_GEMINI_DIR"] = env.get("CB_GEMINI_DIR", "").strip() or host_gemini_dir
        return env


def run_command(
    argv: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str] | None = None,
    capture: bool = False,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run argv directly while inheriting the terminal unless capture is needed."""

    command = [str(part) for part in argv]
    kwargs: dict[str, object] = {
        "cwd": str(cwd),
        "env": None if env is None else dict(env),
        "check": False,
    }
    if capture:
        kwargs.update(
            {
                "stdout": subprocess.PIPE,
                "stderr": subprocess.PIPE,
                "text": True,
            }
        )
    try:
        result = subprocess.run(command, **kwargs)
    except FileNotFoundError as exc:
        raise ManuscriptError(f"Command not found: {command[0]}", 127) from exc
    if check and result.returncode:
        raise CommandError(command, result.returncode)
    return result


def run_compose(
    runtime: Runtime,
    args: Sequence[str],
    *,
    all_profiles: bool = False,
    capture: bool = False,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return run_command(
        runtime.compose_argv(*args, all_profiles=all_profiles),
        cwd=runtime.layout.root,
        env=runtime.compose_environment(),
        capture=capture,
        check=check,
    )


def _dotenv_value(raw: str, path: Path, lineno: int) -> str:
    value = raw.strip()
    if not value:
        return ""
    if value[0] not in {"'", '"'}:
        return re.sub(r"\s+#.*$", "", value).strip()

    quote = value[0]
    escaped = False
    closing = -1
    for index, char in enumerate(value[1:], start=1):
        if quote == '"' and char == "\\" and not escaped:
            escaped = True
            continue
        if char == quote and not escaped:
            closing = index
            break
        escaped = False
    if closing < 0:
        raise ManuscriptError(f"{path}:{lineno}: unclosed quote")
    suffix = value[closing + 1 :].strip()
    if suffix and not suffix.startswith("#"):
        raise ManuscriptError(f"{path}:{lineno}: unexpected characters after quote")
    return value[1:closing]


def _exact_anonymous_readonly_value(raw: str, path: Path, lineno: int) -> str:
    """Exact boolean selector does not allow dotenv quote or whitespace normalization."""

    if raw not in {"0", "1"}:
        raise ManuscriptError(
            f"{path}:{lineno}: {ANONYMOUS_READONLY_KEY} must be "
            "exactly 0 or 1 without surrounding whitespace."
        )
    return raw


def read_dotenv(path: Path) -> dict[str, str]:
    """Read a conservative dotenv subset without evaluating any content."""

    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ManuscriptError(
            f"File not found: {path}. Run `./cb-manuscript init` first."
        ) from exc
    except OSError as exc:
        raise ManuscriptError(f"Cannot read file {path}: {exc}") from exc

    values: dict[str, str] = {}
    for lineno, original in enumerate(text.splitlines(), start=1):
        line = original.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            raise ManuscriptError(f"{path}:{lineno}: not in KEY=VALUE format")
        key, raw = line.split("=", 1)
        key = key.strip()
        if not KEY_RE.fullmatch(key):
            raise ManuscriptError(f"{path}:{lineno}: invalid environment variable name")
        if key == ANONYMOUS_READONLY_KEY:
            lexical = original.rstrip("\r\n").lstrip()
            if lexical.startswith("export "):
                lexical = lexical[7:].lstrip()
            _lexical_key, lexical_raw = lexical.split("=", 1)
            values[key] = _exact_anonymous_readonly_value(
                lexical_raw,
                path,
                lineno,
            )
        else:
            values[key] = _dotenv_value(raw, path, lineno)
    return values


def _effective(values: Mapping[str, str], key: str) -> str:
    if key in os.environ:
        return os.environ[key]
    return values.get(key, "")


def _parse_environment(raw: str, *, source: str) -> str:
    value = raw
    if value not in ENVIRONMENTS:
        expected = " or ".join(sorted(ENVIRONMENTS))
        if value:
            raise ManuscriptError(
                f"Invalid {ENVIRONMENT_KEY}={value!r} in {source}. "
                f"Expected one of: {expected}."
            )
        raise ManuscriptError(
            f"{ENVIRONMENT_KEY} is required in {source}. "
            f"Expected one of: {expected}."
        )
    return value


def _resolve_environment(
    layout: Layout,
    base_values: Mapping[str, str],
    *,
    legacy_dev: bool,
) -> tuple[str, dict[str, str]]:
    process_value = os.environ.get(ENVIRONMENT_KEY)
    if process_value is not None:
        environment = _parse_environment(
            process_value,
            source="process environment",
        )
        if legacy_dev and environment != DEVELOPMENT:
            raise ManuscriptError(
                f"`dev` alias is reserved for {ENVIRONMENT_KEY}={DEVELOPMENT}. "
                f"Cannot be used with {environment!r} in process environment."
            )
    elif legacy_dev:
        environment = DEVELOPMENT
    else:
        environment = _parse_environment(
            base_values.get(ENVIRONMENT_KEY, ""),
            source=str(layout.env),
        )

    values = dict(base_values)
    if environment == DEVELOPMENT:
        development_values = read_dotenv(layout.dev_env)
        if (
            base_values.get(ANONYMOUS_READONLY_KEY) == "1"
            and ANONYMOUS_READONLY_KEY not in development_values
        ):
            raise ManuscriptError(
                f"{layout.dev_env} is missing {ANONYMOUS_READONLY_KEY}. "
                "Specify 0 or 1 with `./cb-manuscript init` so development does not implicitly "
                "inherit production anonymous settings."
            )
        values.update(development_values)
        declared_raw = development_values.get(ENVIRONMENT_KEY, "")
        declared = _parse_environment(
            declared_raw,
            source=str(layout.dev_env),
        )
        if declared != DEVELOPMENT:
            raise ManuscriptError(
                f"{layout.dev_env} {ENVIRONMENT_KEY} must be {DEVELOPMENT!r}."
            )
    else:
        declared_raw = base_values.get(ENVIRONMENT_KEY, "")
        if declared_raw:
            declared = _parse_environment(
                declared_raw,
                source=str(layout.env),
            )
            if declared != PRODUCTION:
                raise ManuscriptError(
                    f"{layout.env} {ENVIRONMENT_KEY} must be {PRODUCTION!r}."
                )

    effective = _parse_environment(
        _effective(values, ENVIRONMENT_KEY),
        source="effective configuration",
    )
    if effective != environment:
        raise ManuscriptError(
            f"Selected environment {environment!r} conflicts with effective "
            f"{ENVIRONMENT_KEY} {effective!r}."
        )
    return environment, values


def _parse_port(values: Mapping[str, str]) -> int:
    port = _effective(values, "CB_API_PORT").strip() or "8765"
    try:
        parsed_port = int(port)
    except ValueError as exc:
        raise ManuscriptError("CB_API_PORT must be an integer.") from exc
    if not 1 <= parsed_port <= 65535:
        raise ManuscriptError("CB_API_PORT must be in range 1-65535.")
    return parsed_port


def _validate_api_bind(values: Mapping[str, str]) -> str:
    raw = _effective(values, "CB_API_BIND").strip()
    if not raw:
        raise ManuscriptError("CB_API_BIND requires an IPv4 address for host publishing.")
    try:
        address = ipaddress.ip_address(raw)
    except ValueError as exc:
        raise ManuscriptError(
            "CB_API_BIND must be a single IPv4 address, not a hostname."
        ) from exc
    if not isinstance(address, ipaddress.IPv4Address):
        raise ManuscriptError("CB_API_BIND must be an IPv4 address.")
    if address.is_unspecified or address.is_multicast:
        raise ManuscriptError(
            "CB_API_BIND cannot be 0.0.0.0 or a multicast address."
        )
    return str(address)


def _validate_dns_hostname(hostname: str, *, field: str) -> None:
    if not hostname or hostname.endswith(".") or "*" in hostname:
        raise ManuscriptError(f"{field} requires an exact DNS hostname.")
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        raise ManuscriptError(f"{field} requires a DNS hostname, not an IP address.")
    if len(hostname) > 253 or any(
        not DNS_LABEL_RE.fullmatch(label) for label in hostname.split(".")
    ):
        raise ManuscriptError(f"Invalid DNS hostname format for {field}.")


def _split_url(raw: str, *, field: str):
    if not raw:
        raise ManuscriptError(f"{field} is required.")
    if "*" in raw:
        raise ManuscriptError(f"{field} cannot contain wildcards.")
    try:
        parsed = urlsplit(raw)
    except ValueError as exc:
        raise ManuscriptError(f"Invalid format for {field}.") from exc
    if (
        not parsed.scheme
        or not parsed.netloc
        or parsed.netloc.endswith(":")
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ManuscriptError(f"Invalid format for {field}.")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ManuscriptError(f"Invalid port format for {field}.") from exc
    if port is not None and port < 1:
        raise ManuscriptError(f"{field} port must be in range 1-65535.")
    return parsed


def _validate_public_url(
    values: Mapping[str, str],
    *,
    environment: str,
    bind: str,
    port: int,
) -> None:
    # This value enters the container via Compose service env_file. Process env is not
    # passed to container, so validate the effective file value directly here as well.
    raw = values.get("CLAIRE_PUBLIC_URL", "")
    if raw != raw.strip():
        raise ManuscriptError("CLAIRE_PUBLIC_URL cannot have leading or trailing whitespace.")
    parsed = _split_url(raw, field="CLAIRE_PUBLIC_URL")
    if parsed.path not in {"", "/"}:
        raise ManuscriptError("CLAIRE_PUBLIC_URL can only use a root path.")

    if environment == DEVELOPMENT:
        expected_authority = f"{bind}:{port}"
        if parsed.scheme != "http" or parsed.netloc != expected_authority:
            raise ManuscriptError(
                "CLAIRE_PUBLIC_URL in development must be "
                f"http://{expected_authority}/"
            )
        return

    if parsed.scheme != "https":
        raise ManuscriptError(
            "CLAIRE_PUBLIC_URL in production must be an external reverse proxy "
            "https URL."
        )
    hostname = parsed.hostname or ""
    _validate_dns_hostname(hostname, field="CLAIRE_PUBLIC_URL")


def _validate_cors_origins(
    values: Mapping[str, str],
    *,
    environment: str,
) -> None:
    # Similar to CLAIRE_PUBLIC_URL, validate the actual container value passed from env_file.
    # Whitespace between items is allowed identically to the application.
    raw = values.get("CLAIRE_CORS_ALLOWED_ORIGINS", "").strip()
    if not raw:
        return
    seen: set[str] = set()
    for origin in (item.strip() for item in raw.split(",")):
        if not origin:
            raise ManuscriptError(
                "CLAIRE_CORS_ALLOWED_ORIGINS cannot contain empty origins."
            )
        if origin in seen:
            raise ManuscriptError(
                f"Duplicate origin in CLAIRE_CORS_ALLOWED_ORIGINS: {origin}"
            )
        parsed = _split_url(origin, field="CLAIRE_CORS_ALLOWED_ORIGINS")
        if parsed.path:
            raise ManuscriptError(
                "CLAIRE_CORS_ALLOWED_ORIGINS cannot include paths."
            )
        if parsed.scheme not in {"http", "https"}:
            raise ManuscriptError(
                "CLAIRE_CORS_ALLOWED_ORIGINS only allows http or https origins."
            )
        if environment == PRODUCTION and parsed.scheme != "https":
            raise ManuscriptError(
                "CLAIRE_CORS_ALLOWED_ORIGINS in production only allows https."
            )
        hostname = parsed.hostname
        if not hostname:
            raise ManuscriptError(
                "CLAIRE_CORS_ALLOWED_ORIGINS requires a hostname."
            )
        try:
            ipaddress.ip_address(hostname)
        except ValueError:
            _validate_dns_hostname(
                hostname,
                field="CLAIRE_CORS_ALLOWED_ORIGINS",
            )
        seen.add(origin)


def _validate_web_tokens(values: Mapping[str, str]) -> None:
    owner = values.get("CLAIRE_INJECT_TOKEN", "")
    readonly = values.get("CLAIRE_READONLY_TOKEN", "")
    if not WEB_TOKEN_RE.fullmatch(owner):
        raise ManuscriptError(
            "CLAIRE_INJECT_TOKEN must be a 32-128 character URL-safe token. "
            "Run `./cb-manuscript init` to generate a token."
        )
    if readonly and not WEB_TOKEN_RE.fullmatch(readonly):
        raise ManuscriptError(
            "CLAIRE_READONLY_TOKEN must be empty or a 32-128 character URL-safe token."
        )
    if readonly and secrets.compare_digest(owner, readonly):
        raise ManuscriptError(
            "CLAIRE_INJECT_TOKEN and CLAIRE_READONLY_TOKEN must be different."
        )


def _validate_anonymous_readonly(values: Mapping[str, str]) -> bool:
    raw = values.get(ANONYMOUS_READONLY_KEY)
    if raw is None:
        return False
    if raw not in {"0", "1"}:
        raise ManuscriptError(
            f"{ANONYMOUS_READONLY_KEY} must be exactly 0 or 1."
        )
    return raw == "1"


def load_runtime(layout: Layout, *, legacy_dev: bool = False) -> Runtime:
    base_values = read_dotenv(layout.env)
    environment, values = _resolve_environment(
        layout,
        base_values,
        legacy_dev=legacy_dev,
    )
    dev = environment == DEVELOPMENT

    project = _effective(values, "CB_PROJECT_NAME").strip()
    if not project:
        project = DEFAULT_DEV_PROJECT if dev else DEFAULT_PROJECT
    if not PROJECT_RE.fullmatch(project):
        raise ManuscriptError(
            "CB_PROJECT_NAME must use only lowercase alphanumeric characters, '-', and '_', "
            "and start with a lowercase letter or digit."
        )

    timeout_text = _effective(values, "CB_WAIT_TIMEOUT").strip()
    if not timeout_text:
        timeout_text = str(DEFAULT_WAIT_TIMEOUT)
    try:
        wait_timeout = int(timeout_text)
    except ValueError as exc:
        raise ManuscriptError("CB_WAIT_TIMEOUT must be an integer in seconds.") from exc
    if not 1 <= wait_timeout <= 86400:
        raise ManuscriptError("CB_WAIT_TIMEOUT must be in range 1-86400.")

    parsed_port = _parse_port(values)
    api_bind = _validate_api_bind(values)
    _validate_public_url(
        values,
        environment=environment,
        bind=api_bind,
        port=parsed_port,
    )
    _validate_cors_origins(values, environment=environment)
    _validate_web_tokens(values)
    anonymous_readonly = _validate_anonymous_readonly(values)

    bot_enabled = bool(_effective(values, "TELEGRAM_BOT_TOKEN").strip())
    return Runtime(
        layout=layout,
        environment=environment,
        values=values,
        project=project,
        wait_timeout=wait_timeout,
        bot_enabled=bot_enabled,
        anonymous_readonly=anonymous_readonly,
    )


def config_preflight(runtime: Runtime) -> None:
    for path in (*runtime.env_files, *runtime.compose_files):
        if not path.is_file():
            raise ManuscriptError(f"Required file not found: {path}")
    run_compose(runtime, ("config", "--quiet"))


def _atomic_write(path: Path, content: str, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary_path, mode)
        os.replace(temporary_path, path)
        try:
            directory_fd = os.open(path.parent, os.O_RDONLY)
        except OSError:
            directory_fd = -1
        if directory_fd >= 0:
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def _copy_once(source: Path, target: Path) -> bool:
    if target.exists():
        os.chmod(target, 0o600)
        return False
    if not source.is_file():
        raise ManuscriptError(f"Environment template file not found: {source}")
    _atomic_write(target, source.read_text(encoding="utf-8"), mode=0o600)
    return True


def _ensure_environment_selector(path: Path, expected: str) -> bool:
    """Safely backfill canonical environment selector into existing env file."""

    text = path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    matches: list[tuple[int, str]] = []

    for index, original in enumerate(lines):
        candidate = original.strip()
        if not candidate or candidate.startswith("#"):
            continue
        if candidate.startswith("export "):
            candidate = candidate[7:].lstrip()
        if "=" not in candidate:
            continue
        key, raw = candidate.split("=", 1)
        if key.strip() == ENVIRONMENT_KEY:
            matches.append((index, _dotenv_value(raw, path, index + 1)))

    if len(matches) > 1:
        raise ManuscriptError(f"Duplicate {ENVIRONMENT_KEY} in {path}.")
    if matches:
        index, value = matches[0]
        if value == expected:
            os.chmod(path, 0o600)
            return False
        if value:
            raise ManuscriptError(
                f"{path} {ENVIRONMENT_KEY} must be {expected!r}."
            )
        newline = "\n" if lines[index].endswith("\n") else ""
        lines[index] = f"{ENVIRONMENT_KEY}={expected}{newline}"
    else:
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        lines.append(f"{ENVIRONMENT_KEY}={expected}\n")

    _atomic_write(path, "".join(lines), mode=0o600)
    return True


def _ensure_inject_token(path: Path) -> bool:
    """Generate empty inject token and verify strength of existing value."""

    text = path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    found = False
    changed = False
    generated: str | None = None

    for index, original in enumerate(lines):
        candidate = original.strip()
        if not candidate or candidate.startswith("#"):
            continue
        if candidate.startswith("export "):
            candidate = candidate[7:].lstrip()
        if "=" not in candidate:
            continue
        key, raw = candidate.split("=", 1)
        if key.strip() != "CLAIRE_INJECT_TOKEN":
            continue
        found = True
        value = _dotenv_value(raw, path, index + 1)
        if value:
            if not WEB_TOKEN_RE.fullmatch(value):
                raise ManuscriptError(
                    f"{path} CLAIRE_INJECT_TOKEN must be a 32-128 character URL-safe token."
                )
            break
        generated = generated or secrets.token_urlsafe(32)
        newline = "\n" if original.endswith("\n") else ""
        lines[index] = f"CLAIRE_INJECT_TOKEN={generated}{newline}"
        changed = True
        break

    if not found:
        generated = secrets.token_urlsafe(32)
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        lines.append(f"CLAIRE_INJECT_TOKEN={generated}\n")
        changed = True

    if changed:
        _atomic_write(path, "".join(lines), mode=0o600)
    else:
        os.chmod(path, 0o600)
    return changed


def _ensure_anonymous_readonly(path: Path) -> bool:
    """Safely backfill missing anonymous readonly setting with default 0."""

    text = path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    matches: list[str] = []

    for index, original in enumerate(lines):
        candidate = original.strip()
        if not candidate or candidate.startswith("#"):
            continue
        if candidate.startswith("export "):
            candidate = candidate[7:].lstrip()
        if "=" not in candidate:
            continue
        key, _raw = candidate.split("=", 1)
        if key.strip() == ANONYMOUS_READONLY_KEY:
            lexical = original.rstrip("\r\n").lstrip()
            if lexical.startswith("export "):
                lexical = lexical[7:].lstrip()
            _lexical_key, lexical_raw = lexical.split("=", 1)
            matches.append(
                _exact_anonymous_readonly_value(
                    lexical_raw,
                    path,
                    index + 1,
                )
            )

    if len(matches) > 1:
        raise ManuscriptError(
            f"Duplicate {ANONYMOUS_READONLY_KEY} in {path}."
        )
    if matches:
        if matches[0] not in {"0", "1"}:
            raise ManuscriptError(
                f"{path} {ANONYMOUS_READONLY_KEY} must be exactly 0 or 1."
            )
        os.chmod(path, 0o600)
        return False

    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    lines.append(f"{ANONYMOUS_READONLY_KEY}=0\n")
    _atomic_write(path, "".join(lines), mode=0o600)
    return True


def _detect_system_timezone() -> str:
    """Detect host system timezone adhering to timedatectl / system config."""

    # 1. Process environment
    env_tz = os.environ.get("TZ", "").strip()
    if env_tz:
        return env_tz

    # 2. /etc/timezone (traditional Debian/Ubuntu)
    try:
        tz_path = Path("/etc/timezone")
        if tz_path.is_file():
            val = tz_path.read_text(encoding="utf-8").strip()
            if val:
                return val
    except Exception:
        pass

    # 3. /etc/localtime symlink target (standard Linux/systemd)
    try:
        localtime = Path("/etc/localtime")
        if localtime.is_symlink():
            resolved = str(localtime.resolve())
            for marker in ("/zoneinfo/", "/zoneinfo.default/"):
                if marker in resolved:
                    val = resolved.split(marker, 1)[1].strip()
                    if val:
                        return val
    except Exception:
        pass

    # 4. Python timezone detection
    try:
        dt = datetime.now().astimezone()
        if dt.tzinfo is not None:
            key = getattr(dt.tzinfo, "key", None)
            if key and isinstance(key, str):
                return key
            name = dt.tzname()
            if name:
                return name
    except Exception:
        pass

    return "UTC"


def _ensure_timezone(path: Path) -> bool:
    """Ensure TZ is populated with detected system timezone if empty."""

    text = path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    found = False
    changed = False
    detected = _detect_system_timezone()

    for index, original in enumerate(lines):
        candidate = original.strip()
        if not candidate or candidate.startswith("#"):
            continue
        if candidate.startswith("export "):
            candidate = candidate[7:].lstrip()
        if "=" not in candidate:
            continue
        key, raw = candidate.split("=", 1)
        if key.strip() != "TZ":
            continue
        found = True
        value = _dotenv_value(raw, path, index + 1)
        if value:
            # already populated
            break
        newline = "\n" if original.endswith("\n") else ""
        lines[index] = f"TZ={detected}{newline}"
        changed = True
        break

    if not found:
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        lines.append(f"TZ={detected}\n")
        changed = True

    if changed:
        _atomic_write(path, "".join(lines), mode=0o600)
    else:
        os.chmod(path, 0o600)
    return changed


def _sync_missing_env_keys(target_path: Path, template_path: Path) -> list[str]:
    """Safely backfill missing environment variables from template into target env file."""

    if not target_path.is_file() or not template_path.is_file():
        return []

    target_values = read_dotenv(target_path)
    template_text = template_path.read_text(encoding="utf-8")

    blocks: list[tuple[list[str], str, str]] = []
    current_comments: list[str] = []
    for line in template_text.splitlines(keepends=True):
        candidate = line.strip()
        if not candidate:
            current_comments = []
            continue
        if candidate.startswith("#"):
            current_comments.append(line)
            continue
        if candidate.startswith("export "):
            candidate = candidate[7:].lstrip()
        if "=" in candidate:
            key = candidate.split("=", 1)[0].strip()
            if KEY_RE.fullmatch(key):
                blocks.append((list(current_comments), key, line))
        current_comments = []

    missing_keys: list[str] = []
    append_lines: list[str] = []

    for comments, key, line in blocks:
        if key not in target_values:
            missing_keys.append(key)
            append_lines.extend(comments)
            append_lines.append(line if line.endswith("\n") else line + "\n")

    if append_lines:
        target_content = target_path.read_text(encoding="utf-8")
        if target_content and not target_content.endswith("\n"):
            target_content += "\n"
        target_content += "".join(append_lines)
        _atomic_write(target_path, target_content, mode=0o600)
    else:
        os.chmod(target_path, 0o600)

    return missing_keys


def sync_environment_files(layout: Layout) -> dict[str, list[str]]:
    """Synchronize both .env and .env.dev with templates and ensure canonical settings."""

    changes: dict[str, list[str]] = {"env": [], "dev_env": []}

    if layout.env.is_file() and layout.env_example.is_file():
        added = _sync_missing_env_keys(layout.env, layout.env_example)
        if added:
            changes["env"].extend(added)
        if _ensure_environment_selector(layout.env, PRODUCTION):
            if ENVIRONMENT_KEY not in changes["env"]:
                changes["env"].append(ENVIRONMENT_KEY)
        if _ensure_timezone(layout.env):
            if "TZ" not in changes["env"]:
                changes["env"].append("TZ")
        if _ensure_anonymous_readonly(layout.env):
            if ANONYMOUS_READONLY_KEY not in changes["env"]:
                changes["env"].append(ANONYMOUS_READONLY_KEY)
        if _ensure_inject_token(layout.env):
            if "CLAIRE_INJECT_TOKEN" not in changes["env"]:
                changes["env"].append("CLAIRE_INJECT_TOKEN")

    dev_example = (
        layout.dev_env_example
        if layout.dev_env_example.is_file()
        else layout.env_example
    )
    if layout.dev_env.is_file() and dev_example.is_file():
        added_dev = _sync_missing_env_keys(layout.dev_env, dev_example)
        if added_dev:
            changes["dev_env"].extend(added_dev)
        if _ensure_environment_selector(layout.dev_env, DEVELOPMENT):
            if ENVIRONMENT_KEY not in changes["dev_env"]:
                changes["dev_env"].append(ENVIRONMENT_KEY)
        if _ensure_timezone(layout.dev_env):
            if "TZ" not in changes["dev_env"]:
                changes["dev_env"].append("TZ")
        if _ensure_anonymous_readonly(layout.dev_env):
            if ANONYMOUS_READONLY_KEY not in changes["dev_env"]:
                changes["dev_env"].append(ANONYMOUS_READONLY_KEY)
        if _ensure_inject_token(layout.dev_env):
            if "CLAIRE_INJECT_TOKEN" not in changes["dev_env"]:
                changes["dev_env"].append("CLAIRE_INJECT_TOKEN")

    return changes


def command_init(layout: Layout) -> int:
    created_env = _copy_once(layout.env_example, layout.env)
    dev_source = (
        layout.dev_env_example
        if layout.dev_env_example.is_file()
        else layout.env_example
    )
    created_dev_env = _copy_once(dev_source, layout.dev_env)
    changes = sync_environment_files(layout)
    layout.data.mkdir(parents=True, exist_ok=True)
    layout.vault.mkdir(parents=True, exist_ok=True)
    (Path.home() / ".gemini").mkdir(parents=True, exist_ok=True)
    (Path.home() / ".local" / "bin").mkdir(parents=True, exist_ok=True)

    detected_tz = _detect_system_timezone()
    print(f".env: {'created' if created_env else 'kept'}")
    print(f".env.dev: {'created' if created_dev_env else 'kept'}")
    print(
        f"{ENVIRONMENT_KEY}: "
        f"{'populated' if ENVIRONMENT_KEY in changes['env'] or ENVIRONMENT_KEY in changes['dev_env'] else 'kept'}"
    )
    print(
        f"TZ: "
        f"{f'populated ({detected_tz})' if 'TZ' in changes['env'] or 'TZ' in changes['dev_env'] else 'kept'}"
    )
    print(
        f"{ANONYMOUS_READONLY_KEY}: "
        f"{'populated (default 0)' if ANONYMOUS_READONLY_KEY in changes['env'] or ANONYMOUS_READONLY_KEY in changes['dev_env'] else 'kept'}"
    )
    print(
        f"CLAIRE_INJECT_TOKEN: "
        f"{'created' if 'CLAIRE_INJECT_TOKEN' in changes['env'] or 'CLAIRE_INJECT_TOKEN' in changes['dev_env'] else 'kept'}"
    )
    print("data/, vault/: ready")
    return 0


class InstanceLock(AbstractContextManager["InstanceLock"]):
    def __init__(self, runtime: Runtime):
        runtime.layout.state_dir.mkdir(parents=True, exist_ok=True)
        self.path = runtime.layout.state_dir / f"{runtime.project}.lock"
        self._stream = None

    def __enter__(self) -> "InstanceLock":
        self._stream = self.path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(self._stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self._stream.close()
            self._stream = None
            raise ManuscriptError(
                f"Another cb-manuscript operation is in progress: {self.path}", 73
            ) from exc
        self._stream.seek(0)
        self._stream.truncate()
        self._stream.write(f"pid={os.getpid()}\n")
        self._stream.flush()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:  # noqa: ANN001
        if self._stream is not None:
            fcntl.flock(self._stream.fileno(), fcntl.LOCK_UN)
            self._stream.close()
            self._stream = None


class BackupNamespaceLock(AbstractContextManager["BackupNamespaceLock"]):
    """Serialize the shared daily backup name across production and dev profiles."""

    def __init__(self, layout: Layout):
        layout.state_dir.mkdir(parents=True, exist_ok=True)
        self.path = layout.state_dir / "backups.lock"
        self._stream = None

    def __enter__(self) -> "BackupNamespaceLock":
        self._stream = self.path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(self._stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self._stream.close()
            self._stream = None
            raise ManuscriptError(
                f"Another backup/restore operation is in progress: {self.path}", 73
            ) from exc
        self._stream.seek(0)
        self._stream.truncate()
        self._stream.write(f"pid={os.getpid()}\n")
        self._stream.flush()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:  # noqa: ANN001
        if self._stream is not None:
            fcntl.flock(self._stream.fileno(), fcntl.LOCK_UN)
            self._stream.close()
            self._stream = None


@dataclass(frozen=True)
class StorageLayout:
    data: Path
    vault: Path
    database_relative: Path

    @property
    def database(self) -> Path:
        return self.data / self.database_relative

    def component(self, name: str) -> Path:
        if name == "data":
            return self.data
        if name == "vault":
            return self.vault
        raise ManuscriptError(f"Unsupported backup component: {name}")


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _configured_host_path(runtime: Runtime, key: str, default: str) -> Path:
    raw = _effective(runtime.values, key).strip() or default
    if "\x00" in raw:
        raise ManuscriptError(f"{key} cannot contain NUL characters.")
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        if not (raw.startswith("./") or raw.startswith("../")):
            raise ManuscriptError(
                f"{key}={raw!r} is not a valid host bind path. "
                "Use an absolute path or a relative path starting with ./ or ../."
            )
        candidate = runtime.layout.root / candidate
    if candidate.is_symlink():
        raise ManuscriptError(f"{key} top-level path cannot be a symlink: {candidate}")
    return candidate.resolve()


def _database_relative_path(runtime: Runtime) -> Path:
    raw = _effective(runtime.values, "CLAIRE_DB_PATH").strip() or "data/claire.db"
    if "\x00" in raw or "\\" in raw:
        raise ManuscriptError("Invalid CLAIRE_DB_PATH format.")
    source = PurePosixPath(raw)
    if any(part in {"", ".", ".."} for part in source.parts):
        raise ManuscriptError(
            "CLAIRE_DB_PATH must be a normalized path under /app/data."
        )
    if source.is_absolute():
        container_path = source
    else:
        container_path = PurePosixPath("/app") / source
    try:
        relative = container_path.relative_to(PurePosixPath("/app/data"))
    except ValueError as exc:
        raise ManuscriptError(
            "CLAIRE_DB_PATH must be under /app/data in container to be backed up."
        ) from exc
    if not relative.parts:
        raise ManuscriptError("CLAIRE_DB_PATH must be a file path.")
    return Path(*relative.parts)


def resolve_storage(runtime: Runtime) -> StorageLayout:
    data = _configured_host_path(runtime, "CB_DATA_DIR", "./data")
    vault = _configured_host_path(runtime, "CB_VAULT_DIR", "./vault")
    backup_root = runtime.layout.backups.resolve()
    repository = runtime.layout.root.resolve()
    home = Path.home().resolve()

    for name, path in (("CB_DATA_DIR", data), ("CB_VAULT_DIR", vault)):
        if path in {Path("/"), repository, home} or len(path.parts) < 3:
            raise ManuscriptError(f"{name} points to an overly broad path: {path}")
        if _is_within(path, backup_root) or _is_within(backup_root, path):
            raise ManuscriptError(
                f"{name} and backups directory contain each other and cannot be backed up: {path}"
            )
    if (
        data == vault
        or _is_within(data, vault)
        or _is_within(vault, data)
    ):
        raise ManuscriptError("CB_DATA_DIR and CB_VAULT_DIR cannot be identical or nested.")
    return StorageLayout(
        data=data,
        vault=vault,
        database_relative=_database_relative_path(runtime),
    )


def _captured_stdout(result: subprocess.CompletedProcess[str]) -> str:
    return result.stdout or ""


def _source_revision(layout: Layout) -> str | None:
    try:
        revision = run_command(
            ("git", "rev-parse", "HEAD"),
            cwd=layout.root,
            capture=True,
            check=False,
        )
    except ManuscriptError as exc:
        if exc.exit_code == 127:
            return None
        raise
    value = _captured_stdout(revision).strip()
    return value if revision.returncode == 0 and value else None


def _read_state(path: Path) -> dict[str, object] | None:
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ManuscriptError(f"Cannot read state file: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ManuscriptError(f"Invalid state file format: {path}")
    return value


def _profile_state(runtime: Runtime) -> dict[str, object] | None:
    return _read_state(runtime.layout.state_for(dev=runtime.dev))


def _require_stable_project(runtime: Runtime, state: Mapping[str, object]) -> None:
    previous = state.get("project")
    if isinstance(previous, str) and previous and previous != runtime.project:
        state_path = runtime.layout.state_for(dev=runtime.dev)
        raise ManuscriptError(
            f"CB_PROJECT_NAME was changed from {previous!r} to {runtime.project!r}. "
            "Aborting to prevent duplicate stacks. Run `cb-manuscript down` with the previous "
            f"name, verify the transition, and remove {state_path}."
        )


def _record_success(
    runtime: Runtime, *, action: str, previous_revision: str | None
) -> None:
    image = _effective(runtime.values, "CB_IMAGE").strip() or "claire-bible"
    tag = _effective(runtime.values, "CB_IMAGE_TAG").strip() or "local"
    state = {
        "schema_version": 1,
        "cli_version": VERSION,
        "action": action,
        "profile": runtime.environment,
        "project": runtime.project,
        "image": f"{image}:{tag}",
        "source_revision": _source_revision(runtime.layout),
        "previous_source_revision": previous_revision,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    _atomic_write(
        runtime.layout.state_for(dev=runtime.dev),
        json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        mode=0o600,
    )


def update_source(layout: Layout) -> None:
    status = run_command(
        ("git", "status", "--porcelain", "--untracked-files=normal"),
        cwd=layout.root,
        capture=True,
    )
    if _captured_stdout(status).strip():
        raise ManuscriptError(
            "Working tree is not clean. Aborting update. Commit or stash your changes."
        )

    upstream = run_command(
        (
            "git",
            "rev-parse",
            "--abbrev-ref",
            "--symbolic-full-name",
            "@{upstream}",
        ),
        cwd=layout.root,
        capture=True,
        check=False,
    )
    if upstream.returncode or not _captured_stdout(upstream).strip():
        raise ManuscriptError("Current branch has no upstream to update from.")
    run_command(("git", "pull", "--ff-only"), cwd=layout.root)


def stop_legacy_containers(layout: Layout) -> list[str]:
    """Stop, but never remove, exact-name containers from the legacy stack."""

    stopped: list[str] = []
    try:
        for name in LEGACY_CONTAINERS:
            found = run_command(
                ("docker", "ps", "-q", "--filter", f"name=^/{name}$"),
                cwd=layout.root,
                capture=True,
            )
            if not _captured_stdout(found).strip():
                continue
            run_command(("docker", "stop", name), cwd=layout.root)
            stopped.append(name)
    except BaseException:
        for name in stopped:
            run_command(("docker", "start", name), cwd=layout.root, check=False)
        raise
    return stopped


def _resume_after_failed_transition(
    runtime: Runtime,
    *,
    project_stopped: Sequence[str],
    legacy_stopped: Sequence[str],
) -> None:
    if project_stopped:
        run_command(
            ("docker", "start", *project_stopped),
            cwd=runtime.layout.root,
            check=False,
        )
    for name in legacy_stopped:
        run_command(("docker", "start", name), cwd=runtime.layout.root, check=False)


def _stop_current_project(runtime: Runtime) -> tuple[str, ...]:
    running = run_compose(
        runtime,
        ("ps", "-q"),
        all_profiles=True,
        capture=True,
    )
    container_ids = tuple(_captured_stdout(running).split())
    if not container_ids:
        return ()
    try:
        run_compose(runtime, ("stop",), all_profiles=True)
    except BaseException:
        run_command(
            ("docker", "start", *container_ids),
            cwd=runtime.layout.root,
            check=False,
        )
        raise
    return container_ids


@dataclass(frozen=True)
class QuiescedContainers:
    project: tuple[str, ...]
    legacy: tuple[str, ...]


def _quiesce_writers(runtime: Runtime) -> QuiescedContainers:
    project = _stop_current_project(runtime)
    try:
        legacy = tuple(stop_legacy_containers(runtime.layout))
    except BaseException:
        _resume_after_failed_transition(
            runtime,
            project_stopped=project,
            legacy_stopped=(),
        )
        raise
    return QuiescedContainers(project=project, legacy=legacy)


def _resume_writers(
    runtime: Runtime, containers: QuiescedContainers
) -> list[str]:
    failures: list[str] = []
    if containers.project:
        result = run_command(
            ("docker", "start", *containers.project),
            cwd=runtime.layout.root,
            capture=True,
            check=False,
        )
        if result.returncode:
            failures.append("Compose project containers")
    for name in containers.legacy:
        result = run_command(
            ("docker", "start", name),
            cwd=runtime.layout.root,
            capture=True,
            check=False,
        )
        if result.returncode:
            failures.append(name)
    return failures


def _stop_captured_writers(
    runtime: Runtime, containers: QuiescedContainers
) -> None:
    if containers.project:
        run_command(
            ("docker", "stop", *containers.project),
            cwd=runtime.layout.root,
            check=False,
        )
    for name in containers.legacy:
        run_command(
            ("docker", "stop", name),
            cwd=runtime.layout.root,
            check=False,
        )


@contextmanager
def _writers_stopped(runtime: Runtime) -> Iterator[QuiescedContainers]:
    containers = _quiesce_writers(runtime)
    try:
        yield containers
    except BaseException:
        failures = _resume_writers(runtime, containers)
        if failures:
            print(
                "cb-manuscript: Failed to resume writers after error: "
                + ", ".join(failures),
                file=sys.stderr,
            )
        raise
    else:
        failures = _resume_writers(runtime, containers)
        if failures:
            raise ManuscriptError(
                "Backup was created, but failed to resume writers: "
                + ", ".join(failures)
            )


def _lstat(path: Path) -> os.stat_result:
    try:
        return path.lstat()
    except FileNotFoundError as exc:
        raise ManuscriptError(f"Path does not exist: {path}") from exc
    except OSError as exc:
        raise ManuscriptError(f"Cannot inspect path: {path}: {exc}") from exc


def _assert_regular_directory(path: Path, *, label: str) -> None:
    mode = _lstat(path).st_mode
    if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
        raise ManuscriptError(f"{label} must be a regular directory, not a symlink: {path}")


def _assert_regular_file(path: Path, *, label: str) -> None:
    mode = _lstat(path).st_mode
    if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
        raise ManuscriptError(f"{label} must be a regular file, not a symlink: {path}")


def _remove_path(path: Path) -> None:
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return
    if stat.S_ISDIR(mode) and not stat.S_ISLNK(mode):
        shutil.rmtree(path)
    else:
        path.unlink()


def _copy_path_safely(
    source: Path,
    destination: Path,
    *,
    relative: Path = Path(),
    excluded: frozenset[Path] = frozenset(),
) -> None:
    for skipped in excluded:
        if relative == skipped or _is_within(relative, skipped):
            return

    source_stat = _lstat(source)
    mode = source_stat.st_mode
    if stat.S_ISLNK(mode):
        raise ManuscriptError(f"Symlink found in backup source: {source}")
    if stat.S_ISREG(mode):
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination, follow_symlinks=False)
        return
    if not stat.S_ISDIR(mode):
        raise ManuscriptError(f"Special file found in backup source: {source}")

    destination.mkdir(parents=True, exist_ok=True)
    os.chmod(destination, stat.S_IMODE(mode))
    try:
        children = sorted(source.iterdir(), key=lambda item: item.name)
    except OSError as exc:
        raise ManuscriptError(f"Cannot read directory: {source}: {exc}") from exc
    for child in children:
        child_relative = relative / child.name
        _copy_path_safely(
            child,
            destination / child.name,
            relative=child_relative,
            excluded=excluded,
        )


def _sqlite_uri(path: Path) -> str:
    return "file:" + quote(str(path), safe="/") + "?mode=ro"


def _validate_sqlite_database(path: Path) -> dict[str, object]:
    _assert_regular_file(path, label="SQLite DB")
    try:
        conn = sqlite3.connect(_sqlite_uri(path), uri=True, timeout=5.0)
        try:
            quick_rows = [str(row[0]) for row in conn.execute("PRAGMA quick_check")]
            if quick_rows != ["ok"]:
                raise ManuscriptError(
                    f"SQLite quick_check failed: {path}: {quick_rows[:5]}"
                )
            foreign_keys = conn.execute("PRAGMA foreign_key_check").fetchmany(10)
            if foreign_keys:
                raise ManuscriptError(
                    f"SQLite foreign_key_check failed: {path}: "
                    f"{foreign_keys[:5]}"
                )
            row = conn.execute(
                "SELECT value FROM meta WHERE key='schema_version'"
            ).fetchone()
            if row is None:
                raise ManuscriptError(
                    f"SQLite schema_version not found: {path}"
                )
            try:
                schema_version = int(row[0])
            except (TypeError, ValueError) as exc:
                raise ManuscriptError(
                    f"Invalid SQLite schema_version: {path}: {row[0]!r}"
                ) from exc
        finally:
            conn.close()
    except ManuscriptError:
        raise
    except sqlite3.Error as exc:
        raise ManuscriptError(f"Cannot validate SQLite database: {path}: {exc}") from exc
    return {
        "path": "",
        "schema_version": schema_version,
        "quick_check": "ok",
        "foreign_key_check": "ok",
    }


def _snapshot_database(source: Path, destination: Path) -> dict[str, object]:
    source_stat = _lstat(source)
    if stat.S_ISLNK(source_stat.st_mode) or not stat.S_ISREG(source_stat.st_mode):
        raise ManuscriptError(f"SQLite database is not a regular file: {source}")
    _validate_sqlite_database(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise ManuscriptError(f"SQLite snapshot destination already exists: {destination}")
    source_conn = None
    destination_conn = None
    try:
        source_conn = sqlite3.connect(_sqlite_uri(source), uri=True, timeout=5.0)
        destination_conn = sqlite3.connect(str(destination))
        source_conn.backup(destination_conn)
        mode = destination_conn.execute("PRAGMA journal_mode=DELETE").fetchone()
        if mode is None or str(mode[0]).lower() != "delete":
            raise ManuscriptError(
                "Failed to create SQLite snapshot in single-file DELETE journal mode."
            )
    except sqlite3.Error as exc:
        _remove_path(destination)
        raise ManuscriptError(f"Failed to create SQLite snapshot: {exc}") from exc
    finally:
        if destination_conn is not None:
            destination_conn.close()
        if source_conn is not None:
            source_conn.close()
    _remove_path(Path(str(destination) + "-wal"))
    _remove_path(Path(str(destination) + "-shm"))
    os.chmod(destination, stat.S_IMODE(source_stat.st_mode))
    return _validate_sqlite_database(destination)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            while True:
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
    except OSError as exc:
        raise ManuscriptError(f"Cannot compute hash for file: {path}: {exc}") from exc
    return digest.hexdigest()


def _scan_payload(payload: Path) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []

    def visit(path: Path) -> None:
        path_stat = _lstat(path)
        relative = path.relative_to(payload).as_posix()
        mode = path_stat.st_mode
        if stat.S_ISLNK(mode):
            raise ManuscriptError(f"Symlink found in backup payload: {relative}")
        if stat.S_ISDIR(mode):
            entries.append(
                {
                    "path": relative,
                    "type": "directory",
                    "mode": f"{stat.S_IMODE(mode):04o}",
                }
            )
            for child in sorted(path.iterdir(), key=lambda item: item.name):
                visit(child)
            return
        if stat.S_ISREG(mode):
            entries.append(
                {
                    "path": relative,
                    "type": "file",
                    "size": path_stat.st_size,
                    "mode": f"{stat.S_IMODE(mode):04o}",
                    "sha256": _sha256(path),
                }
            )
            return
        raise ManuscriptError(f"Special file found in backup payload: {relative}")

    _assert_regular_directory(payload, label="backup payload")
    for child in sorted(payload.iterdir(), key=lambda item: item.name):
        visit(child)
    return entries


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _local_now() -> datetime:
    return datetime.now().astimezone()


def _backup_id() -> str:
    return _local_now().strftime("cb-%Y%m%d")


def _current_schema_version(layout: Layout) -> int:
    path = layout.root / "src" / "claire" / "store" / "db.py"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ManuscriptError(f"Cannot read current DB schema version: {path}") from exc
    match = re.search(r"(?m)^SCHEMA_VERSION\s*=\s*([0-9]+)\s*$", text)
    if match is None:
        raise ManuscriptError(f"Current DB schema version not found: {path}")
    return int(match.group(1))


def _backup_paths(layout: Layout, backup_id: str) -> tuple[Path, Path]:
    return (
        layout.backups / backup_id,
        layout.backups / f"{backup_id}{BACKUP_ARCHIVE_SUFFIX}",
    )


def _path_exists(path: Path) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    return True


def _validate_backup_manifest(root: Path) -> dict[str, object]:
    _assert_regular_directory(root, label="backup")
    top_level = {child.name for child in root.iterdir()}
    if top_level != {"manifest.json", "payload"}:
        raise ManuscriptError(
            "Backup root must contain only manifest.json and payload."
        )

    manifest_path = root / "manifest.json"
    _assert_regular_file(manifest_path, label="backup manifest")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ManuscriptError(f"Cannot read backup manifest: {exc}") from exc
    if not isinstance(manifest, dict):
        raise ManuscriptError("Backup manifest must be a JSON object.")
    if manifest.get("format_version") != BACKUP_FORMAT_VERSION:
        raise ManuscriptError(
            f"Unsupported backup format_version: "
            f"{manifest.get('format_version')!r}"
        )
    backup_id = manifest.get("id")
    if not isinstance(backup_id, str) or not BACKUP_ID_RE.fullmatch(backup_id):
        raise ManuscriptError(f"Invalid backup id format: {backup_id!r}")
    for key in ("created_at", "profile", "project", "cb_manuscript_version"):
        if not isinstance(manifest.get(key), str) or not manifest[key]:
            raise ManuscriptError(f"Invalid {key} value in backup manifest.")

    raw_components = manifest.get("components")
    if (
        not isinstance(raw_components, list)
        or not raw_components
        or any(component not in BACKUP_COMPONENTS for component in raw_components)
        or len(set(raw_components)) != len(raw_components)
    ):
        raise ManuscriptError("Invalid components value in backup manifest.")
    components = tuple(raw_components)
    if tuple(component for component in BACKUP_COMPONENTS if component in components) != components:
        raise ManuscriptError("Invalid components order in backup manifest.")

    raw_entries = manifest.get("entries")
    if not isinstance(raw_entries, list) or not raw_entries:
        raise ManuscriptError("No entries found in backup manifest.")
    expected: dict[str, dict[str, object]] = {}
    for value in raw_entries:
        if not isinstance(value, dict):
            raise ManuscriptError("Invalid backup manifest entry format.")
        raw_path = value.get("path")
        entry_type = value.get("type")
        entry_mode = value.get("mode")
        if not isinstance(raw_path, str) or not raw_path:
            raise ManuscriptError("Invalid backup manifest entry path.")
        logical = PurePosixPath(raw_path)
        if (
            logical.is_absolute()
            or any(part in {"", ".", ".."} for part in logical.parts)
            or logical.as_posix() != raw_path
            or logical.parts[0] not in components
        ):
            raise ManuscriptError(f"Unsafe backup entry path: {raw_path!r}")
        if raw_path in expected:
            raise ManuscriptError(f"Duplicate backup entry path: {raw_path}")
        if (
            entry_type not in {"file", "directory"}
            or not isinstance(entry_mode, str)
            or re.fullmatch(r"[0-7]{4}", entry_mode) is None
        ):
            raise ManuscriptError(f"Invalid backup entry metadata: {raw_path}")
        required = {"path", "type", "mode"}
        if entry_type == "file":
            required.update(("size", "sha256"))
            size = value.get("size")
            digest = value.get("sha256")
            if (
                not isinstance(size, int)
                or isinstance(size, bool)
                or size < 0
                or not isinstance(digest, str)
                or re.fullmatch(r"[0-9a-f]{64}", digest) is None
            ):
                raise ManuscriptError(
                    f"Invalid backup file metadata: {raw_path}"
                )
        if set(value) != required:
            raise ManuscriptError(
                f"Unknown field in backup entry: {raw_path}"
            )
        expected[raw_path] = value

    payload = root / "payload"
    actual_entries = _scan_payload(payload)
    actual = {entry["path"]: entry for entry in actual_entries}
    if expected != actual:
        missing = sorted(set(expected) - set(actual))
        extra = sorted(set(actual) - set(expected))
        changed = sorted(
            path
            for path in set(expected) & set(actual)
            if expected[path] != actual[path]
        )
        raise ManuscriptError(
            "Backup payload integrity verification failed"
            f" (missing={missing[:3]}, extra={extra[:3]}, changed={changed[:3]})."
        )
    for component in components:
        entry = expected.get(component)
        if entry is None or entry.get("type") != "directory":
            raise ManuscriptError(f"Missing backup component: {component}")

    file_entries = [entry for entry in actual_entries if entry["type"] == "file"]
    totals = manifest.get("totals")
    expected_totals = {
        "files": len(file_entries),
        "bytes": sum(int(entry["size"]) for entry in file_entries),
    }
    if totals != expected_totals:
        raise ManuscriptError("Backup manifest totals do not match payload.")

    database = manifest.get("database")
    if "data" in components:
        if not isinstance(database, dict):
            raise ManuscriptError("Data backup is missing database metadata.")
        database_path = database.get("path")
        if not isinstance(database_path, str):
            raise ManuscriptError("Invalid database path metadata.")
        logical_database = PurePosixPath(database_path)
        if (
            logical_database.is_absolute()
            or not logical_database.parts
            or logical_database.parts[0] != "data"
            or any(part in {"", ".", ".."} for part in logical_database.parts)
        ):
            raise ManuscriptError("Unsafe database path metadata.")
        report = _validate_sqlite_database(
            payload / Path(*logical_database.parts)
        )
        if (
            database.get("quick_check") != "ok"
            or database.get("foreign_key_check") != "ok"
            or database.get("schema_version") != report["schema_version"]
            or set(database)
            != {"path", "schema_version", "quick_check", "foreign_key_check"}
        ):
            raise ManuscriptError("Database metadata does not match actual database.")
    elif database is not None:
        raise ManuscriptError("Backup without data component contains database metadata.")
    return manifest


def _build_backup_staging(
    runtime: Runtime,
    storage: StorageLayout,
    components: tuple[str, ...],
    staging: Path,
    backup_id: str,
) -> dict[str, object]:
    staging.mkdir(mode=0o700)
    payload = staging / "payload"
    payload.mkdir(mode=0o700)
    database_report: dict[str, object] | None = None

    for component in components:
        source = storage.component(component)
        _assert_regular_directory(source, label=f"{component} source")
        excluded: frozenset[Path] = frozenset()
        if component == "data":
            database = storage.database_relative
            excluded = frozenset(
                {
                    Path("backups"),
                    Path("offsite-backups"),
                    Path("checkpoints"),
                    database,
                    Path(str(database) + "-wal"),
                    Path(str(database) + "-shm"),
                }
            )
        _copy_path_safely(
            source,
            payload / component,
            excluded=excluded,
        )
        if component == "data":
            database_report = _snapshot_database(
                storage.database,
                payload / "data" / storage.database_relative,
            )
            database_report["path"] = (
                PurePosixPath("data")
                .joinpath(*storage.database_relative.parts)
                .as_posix()
            )

    entries = _scan_payload(payload)
    files = [entry for entry in entries if entry["type"] == "file"]
    manifest: dict[str, object] = {
        "format_version": BACKUP_FORMAT_VERSION,
        "id": backup_id,
        "kind": "manual",
        "created_at": _utc_now().isoformat(),
        "profile": "development" if runtime.dev else "production",
        "project": runtime.project,
        "cb_manuscript_version": VERSION,
        "source_revision": _source_revision(runtime.layout),
        "components": list(components),
        "database": database_report,
        "entries": entries,
        "totals": {
            "files": len(files),
            "bytes": sum(int(entry["size"]) for entry in files),
        },
    }
    _atomic_write(
        staging / "manifest.json",
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        mode=0o600,
    )
    _validate_backup_manifest(staging)
    return manifest


def _create_backup_archive(source: Path, destination: Path) -> None:
    if _path_exists(destination):
        raise ManuscriptError(f"Archive staging file already exists: {destination}")
    try:
        with tarfile.open(destination, mode="w:gz") as archive:
            archive.dereference = True
            archive.add(source / "manifest.json", arcname="manifest.json")
            archive.add(source / "payload", arcname="payload")
        os.chmod(destination, 0o600)
        with destination.open("rb") as stream:
            os.fsync(stream.fileno())
    except (OSError, tarfile.TarError) as exc:
        _remove_path(destination)
        raise ManuscriptError(f"Failed to create backup archive: {exc}") from exc


def _safe_extract_archive(source: Path, destination: Path) -> None:
    _assert_regular_file(source, label="backup archive")
    try:
        with tarfile.open(source, mode="r:*") as archive:
            members = archive.getmembers()
            normalized: set[str] = set()
            for member in members:
                raw = member.name.rstrip("/")
                logical = PurePosixPath(raw)
                if (
                    not raw
                    or logical.is_absolute()
                    or any(part in {"", ".", ".."} for part in logical.parts)
                    or logical.as_posix() != raw
                    or raw in normalized
                    or logical.parts[0] not in {"manifest.json", "payload"}
                ):
                    raise ManuscriptError(
                        f"Unsafe or duplicate archive member: {member.name!r}"
                    )
                if not (member.isdir() or member.isfile()):
                    raise ManuscriptError(
                        f"Links and special files are not allowed in archive: {member.name}"
                    )
                normalized.add(raw)

            for member in members:
                raw = member.name.rstrip("/")
                target = destination / Path(*PurePosixPath(raw).parts)
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    os.chmod(target, member.mode & 0o7777)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                source_stream = archive.extractfile(member)
                if source_stream is None:
                    raise ManuscriptError(
                        f"Cannot read archive member: {member.name}"
                    )
                with source_stream, target.open("xb") as output:
                    shutil.copyfileobj(source_stream, output, length=1024 * 1024)
                os.chmod(target, member.mode & 0o7777)
    except ManuscriptError:
        raise
    except (OSError, tarfile.TarError, EOFError) as exc:
        raise ManuscriptError(f"Cannot read backup archive: {exc}") from exc


@contextmanager
def _materialized_backup(
    source: Path,
    *,
    temporary_parent: Path | None = None,
) -> Iterator[tuple[Path, dict[str, object]]]:
    source_stat = _lstat(source)
    if stat.S_ISLNK(source_stat.st_mode):
        raise ManuscriptError(f"Backup source cannot be a symlink: {source}")
    if temporary_parent is not None:
        temporary_parent.mkdir(parents=True, exist_ok=True)
        os.chmod(temporary_parent, 0o700)
    with tempfile.TemporaryDirectory(
        prefix="cb-manuscript-restore-",
        dir=temporary_parent,
    ) as temporary:
        materialized = Path(temporary) / "backup"
        if stat.S_ISDIR(source_stat.st_mode):
            _copy_path_safely(source, materialized)
        elif stat.S_ISREG(source_stat.st_mode):
            materialized.mkdir(mode=0o700)
            _safe_extract_archive(source, materialized)
        else:
            raise ManuscriptError(
                f"Backup source must be a regular file or directory: {source}"
            )
        manifest = _validate_backup_manifest(materialized)
        yield materialized, manifest


def _publish_backup(
    new_artifact: Path,
    target: Path,
    *,
    conflicting_paths: Sequence[Path],
    replace: bool,
) -> None:
    existing = [path for path in conflicting_paths if _path_exists(path)]
    if existing and not replace:
        raise ManuscriptError(
            "Today's backup already exists: "
            + ", ".join(str(path) for path in existing)
            + ". Specify --replace to overwrite."
        )
    for path in existing:
        mode = _lstat(path).st_mode
        if stat.S_ISLNK(mode) or not (
            stat.S_ISDIR(mode) or stat.S_ISREG(mode)
        ):
            raise ManuscriptError(f"Existing backup format is unsafe: {path}")

    token = secrets.token_hex(8)
    moved: list[tuple[Path, Path]] = []
    published = False
    try:
        for path in existing:
            old = path.parent / f".{path.name}.replace-{token}"
            os.replace(path, old)
            moved.append((path, old))
        os.replace(new_artifact, target)
        published = True
        directory_fd = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except BaseException:
        if published:
            _remove_path(target)
        for original, old in reversed(moved):
            if _path_exists(old):
                os.replace(old, original)
        raise
    for _original, old in moved:
        _remove_path(old)


def _selected_components(raw: Sequence[str] | None) -> tuple[str, ...]:
    selected = set(raw or BACKUP_COMPONENTS)
    if not selected or not selected.issubset(BACKUP_COMPONENTS):
        raise ManuscriptError("Backup component must be data or vault.")
    return tuple(component for component in BACKUP_COMPONENTS if component in selected)


def command_backup(
    runtime: Runtime,
    *,
    output_format: str,
    raw_components: Sequence[str] | None,
    replace: bool,
) -> int:
    components = _selected_components(raw_components)
    with BackupNamespaceLock(runtime.layout), InstanceLock(runtime):
        config_preflight(runtime)
        storage = resolve_storage(runtime)
        runtime.layout.backups.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(runtime.layout.backups, 0o700)
        backup_id = _backup_id()
        directory_target, archive_target = _backup_paths(runtime.layout, backup_id)
        target = (
            directory_target if output_format == "directory" else archive_target
        )
        conflicts = (directory_target, archive_target)
        existing = [path for path in conflicts if _path_exists(path)]
        if existing and not replace:
            raise ManuscriptError(
                "Today's backup already exists: "
                + ", ".join(str(path) for path in existing)
                + ". Specify --replace to overwrite."
            )

        for component in components:
            _assert_regular_directory(
                storage.component(component),
                label=f"{component} source",
            )
        if "data" in components:
            _validate_sqlite_database(storage.database)

        token = secrets.token_hex(8)
        staging = runtime.layout.backups / f".{backup_id}.staging-{token}"
        archive_staging = (
            runtime.layout.backups / f".{backup_id}.archive-{token}.tmp"
        )
        try:
            with _writers_stopped(runtime):
                _build_backup_staging(
                    runtime,
                    storage,
                    components,
                    staging,
                    backup_id,
                )
                if output_format == "archive":
                    _create_backup_archive(staging, archive_staging)
                    with _materialized_backup(
                        archive_staging,
                        temporary_parent=runtime.layout.state_dir,
                    ):
                        pass
                    new_artifact = archive_staging
                else:
                    new_artifact = staging
                _publish_backup(
                    new_artifact,
                    target,
                    conflicting_paths=conflicts,
                    replace=replace,
                )
        finally:
            _remove_path(staging)
            _remove_path(archive_staging)

    print(f"Backup completed: {target}")
    print(f"  id={backup_id} · format={output_format} · components={','.join(components)}")
    return 0


def _verify_component_copy(
    root: Path,
    component: str,
    manifest: Mapping[str, object],
) -> None:
    expected_entries = manifest.get("entries")
    if not isinstance(expected_entries, list):
        raise ManuscriptError("Invalid backup manifest entries.")
    expected = {
        str(entry["path"]): entry
        for entry in expected_entries
        if isinstance(entry, dict)
        and isinstance(entry.get("path"), str)
        and (
            entry["path"] == component
            or str(entry["path"]).startswith(component + "/")
        )
    }
    actual: dict[str, dict[str, object]] = {}

    def visit(path: Path, logical: PurePosixPath) -> None:
        path_stat = _lstat(path)
        mode = path_stat.st_mode
        key = logical.as_posix()
        if stat.S_ISLNK(mode):
            raise ManuscriptError(f"Symlink found in restore staging: {key}")
        if stat.S_ISDIR(mode):
            actual[key] = {
                "path": key,
                "type": "directory",
                "mode": f"{stat.S_IMODE(mode):04o}",
            }
            for child in sorted(path.iterdir(), key=lambda item: item.name):
                visit(child, logical / child.name)
            return
        if stat.S_ISREG(mode):
            actual[key] = {
                "path": key,
                "type": "file",
                "size": path_stat.st_size,
                "mode": f"{stat.S_IMODE(mode):04o}",
                "sha256": _sha256(path),
            }
            return
        raise ManuscriptError(f"Special file found in restore staging: {key}")

    visit(root, PurePosixPath(component))
    if expected != actual:
        raise ManuscriptError(
            f"{component} restore staging does not match backup manifest."
        )


@dataclass
class RestoreSwap:
    component: str
    target: Path
    staging: Path
    rollback: Path
    had_original: bool = False
    installed: bool = False


def _prepare_restore_swaps(
    materialized: Path,
    manifest: Mapping[str, object],
    storage: StorageLayout,
    components: tuple[str, ...],
) -> list[RestoreSwap]:
    token = secrets.token_hex(8)
    swaps: list[RestoreSwap] = []
    try:
        for component in components:
            target = storage.component(component)
            target.parent.mkdir(parents=True, exist_ok=True)
            staging = target.parent / f".{target.name}.cb-restore-new-{token}"
            rollback = target.parent / f".{target.name}.cb-restore-old-{token}"
            if _path_exists(staging) or _path_exists(rollback):
                raise ManuscriptError("Restore staging path conflict occurred.")
            _copy_path_safely(
                materialized / "payload" / component,
                staging,
            )
            _verify_component_copy(staging, component, manifest)
            swaps.append(
                RestoreSwap(
                    component=component,
                    target=target,
                    staging=staging,
                    rollback=rollback,
                )
            )
    except BaseException:
        for swap in swaps:
            _remove_path(swap.staging)
            _remove_path(swap.rollback)
        raise
    return swaps


def _write_restore_journal(
    runtime: Runtime,
    manifest: Mapping[str, object],
    swaps: Sequence[RestoreSwap],
    phase: str,
) -> Path:
    journal = runtime.layout.state_dir / "restore-transaction.json"
    content = {
        "format_version": 1,
        "backup_id": manifest.get("id"),
        "profile": "development" if runtime.dev else "production",
        "project": runtime.project,
        "phase": phase,
        "updated_at": _utc_now().isoformat(),
        "components": [
            {
                "name": swap.component,
                "target": str(swap.target),
                "staging": str(swap.staging),
                "rollback": str(swap.rollback),
                "had_original": swap.had_original,
                "installed": swap.installed,
            }
            for swap in swaps
        ],
    }
    _atomic_write(
        journal,
        json.dumps(content, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        mode=0o600,
    )
    return journal


def _apply_restore_swaps(swaps: Sequence[RestoreSwap]) -> None:
    for swap in swaps:
        if _path_exists(swap.target):
            _assert_regular_directory(
                swap.target,
                label=f"{swap.component} restore target",
            )
            os.replace(swap.target, swap.rollback)
            swap.had_original = True
        try:
            os.replace(swap.staging, swap.target)
            swap.installed = True
        except BaseException:
            if swap.had_original and _path_exists(swap.rollback):
                os.replace(swap.rollback, swap.target)
                swap.had_original = False
            raise


def _rollback_restore_swaps(swaps: Sequence[RestoreSwap]) -> list[str]:
    failures: list[str] = []
    for swap in reversed(swaps):
        try:
            if swap.installed and _path_exists(swap.target):
                _remove_path(swap.target)
                swap.installed = False
            if swap.had_original and _path_exists(swap.rollback):
                os.replace(swap.rollback, swap.target)
                swap.had_original = False
        except BaseException as exc:  # noqa: BLE001 - preserve every recovery path
            failures.append(f"{swap.component}: {exc}")
    return failures


def _remove_restore_rollback(runtime: Runtime, path: Path) -> None:
    try:
        _remove_path(path)
        return
    except PermissionError:
        pass
    _assert_regular_directory(path, label="restore rollback")
    image = _effective(runtime.values, "CB_IMAGE").strip() or "claire-bible"
    tag = _effective(runtime.values, "CB_IMAGE_TAG").strip() or "local"
    cleanup_code = (
        "import os,shutil;"
        "[(shutil.rmtree(e.path) if e.is_dir(follow_symlinks=False) "
        "else os.unlink(e.path)) for e in os.scandir('/cb-discard')]"
    )
    result = run_command(
        (
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--mount",
            f"type=bind,source={path},target=/cb-discard",
            f"{image}:{tag}",
            "python",
            "-c",
            cleanup_code,
        ),
        cwd=runtime.layout.root,
        capture=True,
        check=False,
    )
    if result.returncode:
        raise ManuscriptError(
            f"Failed to clean up root-owned restore rollback content: {path}"
        )
    try:
        path.rmdir()
    except OSError as exc:
        raise ManuscriptError(f"Cannot remove restore rollback path: {path}") from exc


def _wait_for_restored_liveness(runtime: Runtime) -> bool:
    for attempt in range(5):
        result = run_compose(
            runtime,
            ("exec", "-T", "api", "claire", "liveness"),
            check=False,
        )
        if result.returncode == 0:
            return True
        if attempt < 4:
            time.sleep(1)
    return False


def _resolve_backup_source(layout: Layout, raw: str) -> Path:
    if not raw or "\x00" in raw:
        raise ManuscriptError("Backup file or directory path is required for restore.")
    source = Path(raw).expanduser()
    if not source.is_absolute():
        source = layout.root / source
    return source.absolute()


def _restore_components(
    raw: Sequence[str] | None,
    manifest: Mapping[str, object],
) -> tuple[str, ...]:
    available_value = manifest.get("components")
    if not isinstance(available_value, list):
        raise ManuscriptError("Invalid backup manifest components.")
    available = set(available_value)
    selected = set(raw) if raw else available
    if not selected or not selected.issubset(available):
        raise ManuscriptError(
            "Requested restore component not found in backup: "
            + ", ".join(sorted(selected - available))
        )
    return tuple(component for component in BACKUP_COMPONENTS if component in selected)


def command_restore(
    runtime: Runtime,
    *,
    raw_source: str,
    raw_components: Sequence[str] | None,
    confirmed: bool,
) -> int:
    if not confirmed:
        raise ManuscriptError(
            "Restore will replace current data. Specify --yes to confirm."
        )
    source = _resolve_backup_source(runtime.layout, raw_source)
    with BackupNamespaceLock(runtime.layout), InstanceLock(runtime):
        config_preflight(runtime)
        storage = resolve_storage(runtime)
        resolved_source = source.resolve(strict=False)
        for component in BACKUP_COMPONENTS:
            target = storage.component(component)
            if _is_within(resolved_source, target):
                raise ManuscriptError(
                    f"Restore source is inside {component} target and cannot be used."
                )

        with _materialized_backup(
            source,
            temporary_parent=runtime.layout.state_dir,
        ) as (materialized, manifest):
            expected_profile = "development" if runtime.dev else "production"
            if manifest.get("profile") != expected_profile:
                raise ManuscriptError(
                    f"Backup profile does not match current profile: "
                    f"{manifest.get('profile')!r} != {expected_profile!r}"
                )
            if manifest.get("project") != runtime.project:
                raise ManuscriptError(
                    f"Backup project does not match current project: "
                    f"{manifest.get('project')!r} != {runtime.project!r}"
                )
            components = _restore_components(raw_components, manifest)
            database = manifest.get("database")
            if "data" in components:
                if not isinstance(database, dict):
                    raise ManuscriptError("Data backup is missing database metadata.")
                expected_database = (
                    PurePosixPath("data")
                    .joinpath(*storage.database_relative.parts)
                    .as_posix()
                )
                if database.get("path") != expected_database:
                    raise ManuscriptError(
                        "Backup DB path differs from current CLAIRE_DB_PATH: "
                        f"{database.get('path')!r} != {expected_database!r}"
                    )
                schema_version = database.get("schema_version")
                if (
                    not isinstance(schema_version, int)
                    or isinstance(schema_version, bool)
                    or schema_version > _current_schema_version(runtime.layout)
                ):
                    raise ManuscriptError(
                        "Cannot restore DB schema backup newer than current code."
                    )

            swaps = _prepare_restore_swaps(
                materialized,
                manifest,
                storage,
                components,
            )
            containers: QuiescedContainers | None = None
            journal: Path | None = None
            resume_attempted = False
            try:
                containers = _quiesce_writers(runtime)
                journal = _write_restore_journal(
                    runtime,
                    manifest,
                    swaps,
                    "prepared",
                )
                _apply_restore_swaps(swaps)
                _write_restore_journal(runtime, manifest, swaps, "swapped")

                if "data" in components:
                    _migrate(runtime)
                    _validate_sqlite_database(storage.database)
                _write_restore_journal(runtime, manifest, swaps, "validated")

                resume_attempted = True
                resume_failures = _resume_writers(runtime, containers)
                if resume_failures:
                    raise ManuscriptError(
                        "Failed to resume writers after restore: "
                        + ", ".join(resume_failures)
                    )
                if containers.project and not _wait_for_restored_liveness(runtime):
                    raise ManuscriptError("Liveness check failed after restore.")
            except BaseException:
                if containers is not None and resume_attempted:
                    _stop_captured_writers(runtime, containers)
                rollback_failures = _rollback_restore_swaps(swaps)
                if rollback_failures:
                    if journal is not None:
                        _write_restore_journal(
                            runtime,
                            manifest,
                            swaps,
                            "rollback-failed",
                        )
                    raise ManuscriptError(
                        "Restore rollback failed. Keeping writers stopped. "
                        f"{journal}: "
                        + "; ".join(rollback_failures)
                    )
                if containers is not None:
                    resume_failures = _resume_writers(runtime, containers)
                    if resume_failures:
                        if journal is not None:
                            _write_restore_journal(
                                runtime,
                                manifest,
                                swaps,
                                "rollback-restart-failed",
                            )
                        raise ManuscriptError(
                            "Restored existing data, but failed to resume writers: "
                            + ", ".join(resume_failures)
                        )
                if journal is not None:
                    _remove_path(journal)
                raise
            else:
                for swap in swaps:
                    try:
                        _remove_restore_rollback(runtime, swap.rollback)
                    except (OSError, ManuscriptError) as exc:
                        print(
                            f"cb-manuscript: Failed to clean up old restore rollback path: "
                            f"{swap.rollback}: {exc}",
                            file=sys.stderr,
                        )
                if journal is not None:
                    _remove_path(journal)
            finally:
                for swap in swaps:
                    _remove_path(swap.staging)

    print(
        f"Restore completed: {source} · components={','.join(components)}"
    )
    return 0


def _migrate(runtime: Runtime) -> None:
    run_compose(
        runtime,
        (
            "run",
            "--rm",
            "--no-deps",
            "api",
            "claire",
            "migrate",
        ),
    )


def _activate_and_check(runtime: Runtime) -> None:
    run_compose(
        runtime,
        (
            "up",
            "-d",
            "--remove-orphans",
            "--wait",
            "--wait-timeout",
            str(runtime.wait_timeout),
        ),
    )
    run_compose(
        runtime,
        (
            "exec",
            "-T",
            "api",
            "claire",
            "liveness",
        ),
    )


def _transition(runtime: Runtime) -> None:
    project_stopped = _stop_current_project(runtime)
    legacy_stopped: list[str] = []
    try:
        legacy_stopped = stop_legacy_containers(runtime.layout)
        _migrate(runtime)
    except BaseException:
        # No replacement has occurred yet: resume exactly what was running.
        _resume_after_failed_transition(
            runtime,
            project_stopped=project_stopped,
            legacy_stopped=legacy_stopped,
        )
        raise

    try:
        _activate_and_check(runtime)
    except BaseException:
        print(
            "cb-manuscript: Failed to start/verify new stack. "
            "Failed state preserved; inspect with `./cb-manuscript status` and "
            "`./cb-manuscript logs`.",
            file=sys.stderr,
        )
        raise


def command_install(runtime: Runtime) -> int:
    with InstanceLock(runtime):
        env_changes = sync_environment_files(runtime.layout)
        if env_changes.get("env"):
            print(
                f"cb-manuscript: Backfilled environment variables into .env: {', '.join(env_changes['env'])}"
            )
        if env_changes.get("dev_env"):
            print(
                f"cb-manuscript: Backfilled environment variables into .env.dev: {', '.join(env_changes['dev_env'])}"
            )
        runtime = load_runtime(runtime.layout, legacy_dev=runtime.dev)
        config_preflight(runtime)
        previous_state = _profile_state(runtime) or {}
        _require_stable_project(runtime, previous_state)
        previous_revision = previous_state.get("source_revision")
        if not isinstance(previous_revision, str):
            previous_revision = None
        run_compose(runtime, ("build",))
        _transition(runtime)
        _record_success(
            runtime,
            action="install",
            previous_revision=previous_revision,
        )
    return 0


def command_update(runtime: Runtime, *, no_fetch: bool) -> int:
    with InstanceLock(runtime):
        config_preflight(runtime)
        previous_state = _profile_state(runtime) or {}
        _require_stable_project(runtime, previous_state)
        previous_revision = _source_revision(runtime.layout)
        if not no_fetch:
            update_source(runtime.layout)

        env_changes = sync_environment_files(runtime.layout)
        if env_changes.get("env"):
            print(
                f"cb-manuscript: Backfilled environment variables into .env: {', '.join(env_changes['env'])}"
            )
        if env_changes.get("dev_env"):
            print(
                f"cb-manuscript: Backfilled environment variables into .env.dev: {', '.join(env_changes['dev_env'])}"
            )
        runtime = load_runtime(runtime.layout, legacy_dev=runtime.dev)
        config_preflight(runtime)

        # Existing containers continue serving throughout fetch and build.
        run_compose(runtime, ("build",))
        _transition(runtime)
        _record_success(
            runtime,
            action="update",
            previous_revision=previous_revision,
        )
    return 0


def command_doctor(runtime: Runtime) -> int:
    run_command(("docker", "--version"), cwd=runtime.layout.root)
    run_command(("docker", "compose", "version"), cwd=runtime.layout.root)
    run_command(
        ("docker", "info", "--format", "{{.ServerVersion}}"),
        cwd=runtime.layout.root,
    )
    run_command(("git", "--version"), cwd=runtime.layout.root)
    config_preflight(runtime)
    print(f"root: {runtime.layout.root}")
    print(f"profile: {runtime.environment}")
    print(f"project: {runtime.project}")
    print(f"bot profile: {'enabled' if runtime.bot_enabled else 'disabled'}")
    anonymous_status = "disabled"
    if runtime.anonymous_readonly:
        anonymous_status = (
            "ENABLED - full knowledge base, including hidden documents, is public"
        )
    print(f"anonymous readonly: {anonymous_status}")
    raw_provider = runtime.values.get("CLAIRE_PROVIDER", "").strip().lower()
    print(f"provider: {raw_provider or 'mock'}")
    if raw_provider in ("antigravity", "agy"):
        host_bin_dir, host_gemini_dir = detect_host_antigravity_paths(runtime.values)
        agy_bin = Path(host_bin_dir) / (runtime.values.get("CLAIRE_AGY_BIN", "").strip() or "agy")
        if agy_bin.is_file():
            print(f"antigravity binary: {agy_bin}")
        else:
            print(f"antigravity binary: NOT found (will fall back to mock in container)")
        print(f"antigravity credentials: {host_gemini_dir}")
    print("doctor: OK")
    return 0


def command_health(runtime: Runtime) -> int:
    result = run_compose(
        runtime,
        (
            "exec",
            "-T",
            "api",
            "claire",
            "liveness",
        ),
        check=False,
    )
    return result.returncode


def command_remote(layout: Layout, action: str) -> int:
    if not layout.deploy_script.is_file():
        raise ManuscriptError(f"Remote deployment script not found: {layout.deploy_script}")
    env = os.environ.copy()
    env[ENVIRONMENT_KEY] = PRODUCTION
    env["DEPLOY_ACTION"] = action
    result = run_command(
        ("bash", str(layout.deploy_script)),
        cwd=layout.root,
        env=env,
        check=False,
    )
    return result.returncode


def command_version(layout: Layout) -> int:
    print(f"cb-manuscript {VERSION}")
    revision = run_command(
        ("git", "rev-parse", "--short", "HEAD"),
        cwd=layout.root,
        capture=True,
        check=False,
    )
    if revision.returncode == 0 and _captured_stdout(revision).strip():
        print(f"source {_captured_stdout(revision).strip()}")
    for dev in (False, True):
        state = _read_state(layout.state_for(dev=dev))
        if state:
            print(
                "deployed "
                f"{state.get('source_revision') or 'unknown'} "
                f"({state.get('profile') or 'unknown'}, "
                f"{state.get('updated_at') or 'unknown'})"
            )
    return 0


def _app_guard_reason(args: Sequence[str]) -> str | None:
    if not args or args[0] in {"-h", "--help"}:
        return None
    command = args[0]
    if command.startswith("-"):
        return "claire global option unclassified in cb-manuscript safety policy"
    if command in APP_GUARDED_COMMANDS:
        return APP_GUARDED_COMMANDS[command]
    applies_merge = any(
        token.startswith("--")
        and len(token) > 2
        and "--apply".startswith(token)
        for token in args[1:]
    )
    if command == "dedup-merge" and applies_merge:
        return "destructive maintenance command that deletes documents"
    if command == "recanonicalize" and "--dry-run" not in args[1:]:
        return "maintenance command that modifies persistent data"
    if command in APP_ONE_OFF_COMMANDS:
        return None
    return "unclassified app command in cb-manuscript safety policy"


def _prepare_app_args(args: Sequence[str]) -> tuple[str, ...]:
    remaining = list(args)
    advanced = bool(remaining[:1] == [APP_ADVANCED_OPTION])
    if advanced:
        remaining.pop(0)
    if remaining[:1] == ["--"]:
        remaining.pop(0)
    if not remaining:
        raise ManuscriptError(
            "A claire command is required after app. "
            "See `./cb-manuscript app --help` for available commands."
        )

    reason = _app_guard_reason(remaining)
    if reason and not advanced:
        command = _display_argv(remaining)
        raise ManuscriptError(
            f"`app {command}` is blocked by default: {reason}. "
            f"To run directly without safety guards, use `app {APP_ADVANCED_OPTION} "
            f"{command}`."
        )
    if advanced:
        detail = f": {reason}" if reason else ""
        print(
            "cb-manuscript: warning: app --advanced does not guarantee service quiescence, "
            f"migration ordering, backup, or recoverability{detail}",
            file=sys.stderr,
        )
    return tuple(remaining)


def dispatch_passthrough(
    runtime: Runtime, command: str, args: Sequence[str]
) -> int:
    if command == "up":
        if args:
            compose_args = ("up", *args)
        else:
            compose_args = (
                "up",
                "-d",
                "--wait",
                "--wait-timeout",
                str(runtime.wait_timeout),
            )
    elif command == "down":
        compose_args = ("down", *args)
    elif command == "restart":
        compose_args = ("restart", *args)
    elif command == "status":
        compose_args = ("ps", *args)
    elif command == "logs":
        compose_args = ("logs", *args)
    elif command == "compose":
        compose_args = tuple(args[1:]) if args and args[0] == "--" else tuple(args)
        if not compose_args:
            raise ManuscriptError("Docker Compose arguments required after compose.")
    elif command == "app":
        app_args = _prepare_app_args(args)
        compose_args = (
            "run",
            "--rm",
            "--no-deps",
            "api",
            "claire",
            *app_args,
        )
    elif command == "shell":
        remaining = list(args)
        service = "api"
        if remaining and remaining[0] != "--":
            service = remaining.pop(0)
        if remaining[:1] == ["--"]:
            remaining.pop(0)
        shell_command = remaining or ["bash"]
        compose_args = ("exec", service, *shell_command)
    else:  # pragma: no cover - guarded by PASSTHROUGH_COMMANDS
        raise ManuscriptError(f"Unknown command: {command}")

    result = run_compose(
        runtime,
        compose_args,
        all_profiles=command == "down",
        check=False,
    )
    return result.returncode


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cb-manuscript",
        description=(
            f"Claire container management tool.\n"
            f"Environment is selected via {ENVIRONMENT_KEY} ({DEVELOPMENT}/{PRODUCTION}); `dev` is a convenience alias."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  ./cb-manuscript init            # Initialize configuration and directories\n"
            "  ./cb-manuscript doctor          # Pre-flight check configuration and environment\n"
            "  ./cb-manuscript install         # Build, migrate, start, and healthcheck\n"
            "  ./cb-manuscript update          # Update source, rebuild, and restart\n"
            "  ./cb-manuscript status          # Check container status\n"
            "  ./cb-manuscript logs -f api     # View real-time logs for api service\n"
            "  ./cb-manuscript shell           # Interactive shell in api container\n"
            "  ./cb-manuscript health          # Check API liveness\n"
            "  ./cb-manuscript app health      # Diagnose full application health\n"
            "  ./cb-manuscript dev <command>   # Run in development environment"
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # 1. init
    subparsers.add_parser(
        "init",
        help="Initialize .env/.env.dev and data/vault directories",
        description=(
            "Create .env and .env.dev environment files and initialize data/ and vault/ directories.\n\n"
            "Actions:\n"
            "  - Copy templates from .env.example and .env.dev.example (preserves existing files)\n"
            "  - Generate CLAIRE_INJECT_TOKEN and CLAIRE_READONLY_TOKEN\n"
            "  - Create data/ and vault/ directories with secure permissions (0700)"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  ./cb-manuscript init          # Initialize production configuration\n"
            "  ./cb-manuscript dev init      # Initialize development configuration"
        ),
    )

    # 2. doctor
    subparsers.add_parser(
        "doctor",
        help="Check configuration and Docker Compose preflight",
        description=(
            "Pre-flight check deployment configuration, Docker Compose validity, network, and security constraints.\n\n"
            "Checks:\n"
            "  - Environment files (.env / .env.dev) existence and syntax\n"
            "  - CB_API_BIND (IPv4), CB_API_PORT, and CLAIRE_PUBLIC_URL consistency\n"
            "  - Security tokens and anonymous read-only (CLAIRE_ANONYMOUS_READONLY) settings\n"
            "  - Docker compose config syntax and legacy container conflicts\n"
            "  - data/ and vault/ directory permissions (0700)"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  ./cb-manuscript doctor        # Check production configuration\n"
            "  ./cb-manuscript dev doctor    # Check development configuration"
        ),
    )

    # 3. install
    subparsers.add_parser(
        "install",
        help="Build images, migrate database, start services, and verify health",
        description=(
            "Execute initial installation and service startup pipeline.\n\n"
            "Steps:\n"
            "  1. Pre-flight check (doctor)\n"
            "  2. Build Docker images (docker compose build)\n"
            "  3. Migrate database (claire migrate)\n"
            "  4. Start Compose service stack (up -d --wait)\n"
            "  5. API healthcheck (health)"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  ./cb-manuscript install       # Initial production install and start\n"
            "  ./cb-manuscript dev install   # Initial development install and start"
        ),
    )

    # 4. update
    update = subparsers.add_parser(
        "update",
        help="Fast-forward update and safely restart services",
        description=(
            "Fast-forward pull Git source, rebuild images, and restart services in a safe order.\n\n"
            "Steps:\n"
            "  1. Check Git working tree and fast-forward pull (skipped if --no-fetch is given)\n"
            "  2. Rebuild Docker images\n"
            "  3. Safely stop services and migrate DB\n"
            "  4. Restart Compose services (up -d --wait)\n"
            "  5. API healthcheck"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  ./cb-manuscript update             # git pull, rebuild, and restart\n"
            "  ./cb-manuscript update --no-fetch  # Redeploy current code without git fetch"
        ),
    )
    update.add_argument(
        "--no-fetch",
        action="store_true",
        help="Skip git pull (fetch) and redeploy using local source",
    )

    # 5. backup
    backup = subparsers.add_parser(
        "backup",
        help="Backup data/vault to a verifiable directory or archive",
        description=(
            "Backup data directory (DB, etc.) and vault directory (encrypted credentials, etc.).\n"
            "Automatically computes checksums and metadata (manifest.json) to verify integrity."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  ./cb-manuscript backup                               # Default directory backup (backups/cb-YYYYMMDD/)\n"
            "  ./cb-manuscript backup --format archive              # Compressed archive backup (.tar.gz)\n"
            "  ./cb-manuscript backup --component data              # Backup only data component\n"
            "  ./cb-manuscript backup --replace                     # Overwrite existing backup for today"
        ),
    )
    backup.add_argument(
        "--format",
        choices=("directory", "archive"),
        default="directory",
        help="Backup format: directory=backups/cb-YYYYMMDD/, archive=.tar.gz (default: directory)",
    )
    backup.add_argument(
        "--component",
        choices=BACKUP_COMPONENTS,
        action="append",
        help="Components to backup (data, vault; repeatable; default: all)",
    )
    backup.add_argument(
        "--replace",
        action="store_true",
        help="Replace existing backup for today if present",
    )

    # 6. restore
    restore = subparsers.add_parser(
        "restore",
        help="Verify and restore data from backup directory or archive",
        description=(
            "Verify integrity of backup directory or archive (.tar.gz) and restore data.\n"
            "Stopping services prior to restore is recommended for safety."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  ./cb-manuscript restore backups/cb-20260818 --yes\n"
            "  ./cb-manuscript restore backups/cb-20260818.tar.gz --component data --yes"
        ),
    )
    restore.add_argument("source", help="Path to backup directory or .tar.gz archive to restore")
    restore.add_argument(
        "--component",
        choices=BACKUP_COMPONENTS,
        action="append",
        help="Components to restore (data, vault; repeatable; default: all from backup)",
    )
    restore.add_argument(
        "--yes",
        action="store_true",
        help="Explicitly confirm replacement of current selected components without prompt",
    )

    # 7. health
    subparsers.add_parser(
        "health",
        help="Check liveness of running API",
        description=(
            "Check response from running API container HTTP liveness endpoint (GET /health).\n\n"
            "Note: For detailed application health (degraded, DB stats, etc.), use './cb-manuscript app health'."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  ./cb-manuscript health        # Check production API liveness\n"
            "  ./cb-manuscript dev health    # Check development API liveness"
        ),
    )

    # 8. version
    subparsers.add_parser(
        "version",
        help="Display wrapper and source versions",
        description="Display cb-manuscript wrapper version and packaged claire version.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # 9. up
    subparsers.add_parser(
        "up",
        help=PASSTHROUGH_HELP["up"],
        description=(
            "Start Docker Compose services.\n"
            "Running without arguments starts safely in background (-d --wait --wait-timeout <timeout>).\n"
            "Passing additional arguments forwards them to docker compose up."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Key Compose options and arguments:\n"
            "  -d, --detach          Run containers in background\n"
            "  --build               Build images before starting containers\n"
            "  --no-deps             Do not start linked services\n"
            "  [service ...]         Specific service names to start (e.g. api, bot)\n\n"
            "Examples:\n"
            "  ./cb-manuscript up                    # Default safe startup (-d --wait)\n"
            "  ./cb-manuscript up --build            # Rebuild images and start\n"
            "  ./cb-manuscript up -d api             # Start only api service in background"
        ),
    )

    # 10. down
    subparsers.add_parser(
        "down",
        help=PASSTHROUGH_HELP["down"],
        description=(
            "Stop running Docker Compose services and remove containers.\n"
            "Runs docker compose --profile * down across all profiles (including bot)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Key Compose options:\n"
            "  -v, --volumes         Remove named volumes (Caution: persistent data may be deleted)\n"
            "  --remove-orphans      Remove orphan containers not defined in Compose file\n"
            "  -t, --timeout sec     Shutdown timeout in seconds\n\n"
            "Examples:\n"
            "  ./cb-manuscript down                  # Safely stop and remove all services\n"
            "  ./cb-manuscript down --remove-orphans # Remove including orphan containers"
        ),
    )

    # 11. restart
    subparsers.add_parser(
        "restart",
        help=PASSTHROUGH_HELP["restart"],
        description="Restart running Docker Compose services (docker compose restart).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Arguments:\n"
            "  [service ...]         Service names to restart (restarts all services if omitted)\n\n"
            "Examples:\n"
            "  ./cb-manuscript restart               # Restart all services\n"
            "  ./cb-manuscript restart api           # Restart only api service\n"
            "  ./cb-manuscript restart bot           # Restart only bot service"
        ),
    )

    # 12. status
    subparsers.add_parser(
        "status",
        help=PASSTHROUGH_HELP["status"],
        description="Display running status of Docker Compose containers (docker compose ps).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Key Compose options:\n"
            "  -a, --all             Show all containers including stopped ones\n"
            "  --format format       Output format (table, json, etc.)\n"
            "  --status status       Filter by status (running, exited, paused, etc.)\n"
            "  [service ...]         Specific service names to query (e.g. api, bot)\n\n"
            "Examples:\n"
            "  ./cb-manuscript status                # Running services status\n"
            "  ./cb-manuscript status -a             # Full status including stopped containers\n"
            "  ./cb-manuscript status --format json  # Output in JSON format"
        ),
    )

    # 13. logs
    subparsers.add_parser(
        "logs",
        help=PASSTHROUGH_HELP["logs"],
        description=(
            "Display logs of Docker Compose services (docker compose logs).\n\n"
            "Target services:\n"
            "  api                   API web server container\n"
            "  bot                   Telegram bot container (when TELEGRAM_BOT_TOKEN is set)"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Key Compose options and arguments:\n"
            "  -f, --follow          Stream logs in real time\n"
            "  --tail lines          Number of lines to show from end of logs (default: all)\n"
            "  --since time          Filter logs since timestamp (e.g. 10m, 2026-08-18T10:00:00)\n"
            "  -t, --timestamps      Show log timestamps\n"
            "  [service ...]         Services to view logs for (e.g. api, bot)\n\n"
            "Examples:\n"
            "  ./cb-manuscript logs                  # View all service logs\n"
            "  ./cb-manuscript logs api              # View api service logs\n"
            "  ./cb-manuscript logs -f api           # Stream api logs in real time\n"
            "  ./cb-manuscript logs --tail 100 api   # View last 100 lines of api service logs"
        ),
    )

    # 14. shell
    subparsers.add_parser(
        "shell",
        help=PASSTHROUGH_HELP["shell"],
        description=(
            "Run a shell or command inside a running service container (docker compose exec).\n"
            "Default service is api, and default command is bash."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Syntax:\n"
            "  ./cb-manuscript shell [service] [-- command ...]\n\n"
            "Examples:\n"
            "  ./cb-manuscript shell                 # Interactive bash shell in api container\n"
            "  ./cb-manuscript shell bot             # Interactive bash shell in bot container\n"
            "  ./cb-manuscript shell api -- python3  # Run python3 REPL in api container\n"
            "  ./cb-manuscript shell api -- env      # Inspect environment variables in api container"
        ),
    )

    # 15. app
    subparsers.add_parser(
        "app",
        help=PASSTHROUGH_HELP["app"],
        description=(
            "Run a claire one-off command inside the api container with current environment (.env) and volume mounts (data, vault) (docker compose run --rm --no-deps api claire ...).\n\n"
            "Safety Policy:\n"
            "  - One-off query and maintenance commands can be run by default.\n"
            "  - Service lifecycle commands (migrate, bot, serve-api, reextract, etc.) are blocked by default for safety.\n"
            "  - Use --advanced flag to run blocked commands."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Allowed Commands (one-off):\n"
            "  health, doctor, status, stats, search, ingest, refresh-run, recover-run, ...\n\n"
            "Guarded Commands (blocked by default):\n"
            "  migrate, bot, serve-api, recover-loop, refresh-loop, expand-loop, reextract\n\n"
            "Options:\n"
            "  --advanced            Bypass safety guards to execute guarded commands\n\n"
            "Examples:\n"
            "  ./cb-manuscript app health            # Check detailed application health\n"
            "  ./cb-manuscript app doctor            # Run app-level diagnostics\n"
            "  ./cb-manuscript app search \"query\"     # Run CLI search\n"
            "  ./cb-manuscript app --help            # Display claire CLI help\n"
            "  ./cb-manuscript app --advanced migrate # Bypass safety guards to run migration"
        ),
    )

    # 16. compose
    subparsers.add_parser(
        "compose",
        help=PASSTHROUGH_HELP["compose"],
        description=(
            "Advanced escape hatch to pass arbitrary arguments directly to Docker Compose with cb-manuscript environment settings (.env, project name, etc.) injected."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Syntax:\n"
            "  ./cb-manuscript compose [--] <args ...>\n\n"
            "Examples:\n"
            "  ./cb-manuscript compose ps\n"
            "  ./cb-manuscript compose config\n"
            "  ./cb-manuscript compose top\n"
            "  ./cb-manuscript compose -- exec api env"
        ),
    )

    # 17. remote
    remote = subparsers.add_parser(
        "remote",
        help="Remote execution via deploy.sh",
        description="Connect to SSH remote host via deploy.sh and execute deployment commands (production only).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  ./cb-manuscript remote install\n"
            "  ./cb-manuscript remote update"
        ),
    )
    remote_subparsers = remote.add_subparsers(dest="remote_action", required=True)
    remote_subparsers.add_parser(
        "install",
        help="Run cb-manuscript install on remote host",
        description="Connect to SSH remote host and perform initial installation (install).",
    )
    remote_subparsers.add_parser(
        "update",
        help="Run cb-manuscript update on remote host",
        description="Connect to SSH remote host and perform update.",
    )
    return parser


def _split_dev_prefix(argv: Sequence[str]) -> tuple[bool, list[str]]:
    args = list(argv)
    if args[:1] == ["dev"]:
        return True, args[1:]
    return False, args


def _validate_remote_environment(layout: Layout, *, legacy_dev: bool) -> None:
    if legacy_dev:
        raise ManuscriptError("remote install/update can only be executed in production environment.")
    process_value = os.environ.get(ENVIRONMENT_KEY)
    if process_value is not None:
        environment = _parse_environment(process_value, source="process environment")
    else:
        values = read_dotenv(layout.env)
        environment = _parse_environment(
            values.get(ENVIRONMENT_KEY, ""),
            source=str(layout.env),
        )
    if environment != PRODUCTION:
        raise ManuscriptError(
            f"remote install/update is reserved for {ENVIRONMENT_KEY}={PRODUCTION}."
        )


def main(argv: Sequence[str] | None = None, *, root: Path | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    dev, args = _split_dev_prefix(raw)
    layout = Layout((root or Path(__file__).resolve().parents[1]).resolve())

    try:
        if args and args[0] in PASSTHROUGH_COMMANDS:
            if args[0] != "app" and args[1:] in (["--help"], ["-h"]):
                build_parser().parse_args([args[0], "--help"])
                return 0
            runtime = load_runtime(layout, legacy_dev=dev)
            if args[0] in LOCKED_PASSTHROUGH_COMMANDS:
                with InstanceLock(runtime):
                    return dispatch_passthrough(runtime, args[0], args[1:])
            return dispatch_passthrough(runtime, args[0], args[1:])

        if args[:1] == ["remote"] and len(args) >= 2:
            _validate_remote_environment(layout, legacy_dev=dev)
            action = args[1]
            if action not in {"install", "update"}:
                build_parser().error("remote action must be install or update")
            if len(args) != 2:
                raise ManuscriptError("remote install/update does not accept additional arguments.")
            return command_remote(layout, action)

        parsed = build_parser().parse_args(args)
        if parsed.command == "init":
            return command_init(layout)
        if parsed.command == "version":
            return command_version(layout)

        runtime = load_runtime(layout, legacy_dev=dev)
        if parsed.command == "doctor":
            return command_doctor(runtime)
        if parsed.command == "install":
            return command_install(runtime)
        if parsed.command == "update":
            return command_update(runtime, no_fetch=parsed.no_fetch)
        if parsed.command == "backup":
            return command_backup(
                runtime,
                output_format=parsed.format,
                raw_components=parsed.component,
                replace=parsed.replace,
            )
        if parsed.command == "restore":
            return command_restore(
                runtime,
                raw_source=parsed.source,
                raw_components=parsed.component,
                confirmed=parsed.yes,
            )
        if parsed.command == "health":
            return command_health(runtime)
        raise ManuscriptError(f"Unknown command: {parsed.command}")
    except SystemExit as exc:
        if argv is None:
            raise
        return int(exc.code or 0)
    except ManuscriptError as exc:
        print(f"cb-manuscript: {exc}", file=sys.stderr)
        return exc.exit_code
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
