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
    "up": "Compose 서비스를 기동",
    "down": "Compose 서비스를 중지·제거",
    "restart": "Compose 서비스를 재시작",
    "status": "Compose 서비스 상태 표시",
    "logs": "Compose 서비스 로그 표시",
    "shell": "실행 중인 서비스에서 shell 명령 실행",
    "app": "배포 환경에서 claire one-off 실행(--advanced는 보호 우회)",
    "compose": "고급 Docker Compose 인수 전달",
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
    "migrate": "install/update가 소유하는 schema lifecycle 명령",
    "bot": "Compose가 소유하는 지속 실행 서비스",
    "serve-api": "Compose가 소유하는 지속 실행 서비스",
    "recover-loop": "Compose가 소유하는 지속 실행 서비스",
    "refresh-loop": "Compose가 소유하는 지속 실행 서비스",
    "expand-loop": "Compose가 소유하는 지속 실행 서비스",
    "reextract": "그래프를 재구축하는 파괴적 유지보수 명령",
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
            f"명령이 실패했습니다(exit {returncode}): {_display_argv(argv)}",
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

    def compose_environment(self) -> dict[str, str]:
        env = os.environ.copy()
        # 세 보안 경계 값은 service env_file이 실제 컨테이너에 전달한다. host process의
        # 동명 값이 Compose 보간에 끼어들어 사전 검사와 실행값을 갈라놓지 못하게 한다.
        env.pop("CLAIRE_PUBLIC_URL", None)
        env.pop("CLAIRE_CORS_ALLOWED_ORIGINS", None)
        env.pop(ANONYMOUS_READONLY_KEY, None)
        env[ENVIRONMENT_KEY] = self.environment
        env["CB_ENV_FILE"] = str(self.layout.env.resolve())
        if self.dev:
            env["CB_DEV_ENV_FILE"] = str(self.layout.dev_env.resolve())
        else:
            env.pop("CB_DEV_ENV_FILE", None)
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
        raise ManuscriptError(f"명령을 찾을 수 없습니다: {command[0]}", 127) from exc
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
        raise ManuscriptError(f"{path}:{lineno}: 닫히지 않은 따옴표")
    suffix = value[closing + 1 :].strip()
    if suffix and not suffix.startswith("#"):
        raise ManuscriptError(f"{path}:{lineno}: 따옴표 뒤에 허용되지 않은 문자가 있습니다")
    return value[1:closing]


def _exact_anonymous_readonly_value(raw: str, path: Path, lineno: int) -> str:
    """공개 범위 selector는 dotenv의 공백/따옴표 정규화를 허용하지 않는다."""

    if raw not in {"0", "1"}:
        raise ManuscriptError(
            f"{path}:{lineno}: {ANONYMOUS_READONLY_KEY}는 "
            "바깥 공백 없이 정확히 0 또는 1이어야 합니다."
        )
    return raw


def read_dotenv(path: Path) -> dict[str, str]:
    """Read a conservative dotenv subset without evaluating any content."""

    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ManuscriptError(
            f"{path} 파일이 없습니다. 먼저 `./cb-manuscript init`을 실행하세요."
        ) from exc
    except OSError as exc:
        raise ManuscriptError(f"{path} 파일을 읽을 수 없습니다: {exc}") from exc

    values: dict[str, str] = {}
    for lineno, original in enumerate(text.splitlines(), start=1):
        line = original.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            raise ManuscriptError(f"{path}:{lineno}: KEY=VALUE 형식이 아닙니다")
        key, raw = line.split("=", 1)
        key = key.strip()
        if not KEY_RE.fullmatch(key):
            raise ManuscriptError(f"{path}:{lineno}: 환경변수 이름이 잘못되었습니다")
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
        expected = " 또는 ".join(sorted(ENVIRONMENTS))
        if value:
            raise ManuscriptError(
                f"{source}의 {ENVIRONMENT_KEY}={value!r}은 잘못되었습니다. "
                f"{expected} 중 하나를 사용하세요."
            )
        raise ManuscriptError(
            f"{source}에 {ENVIRONMENT_KEY}가 필요합니다. "
            f"{expected} 중 하나를 설정하세요."
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
            source="프로세스 환경",
        )
        if legacy_dev and environment != DEVELOPMENT:
            raise ManuscriptError(
                f"`dev` 별칭은 {ENVIRONMENT_KEY}={DEVELOPMENT} 전용입니다. "
                f"프로세스 환경의 {environment!r}과 함께 사용할 수 없습니다."
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
                f"{layout.dev_env}에 {ANONYMOUS_READONLY_KEY}가 없습니다. "
                "production의 익명 공개 설정을 development가 암묵적으로 상속하지 않도록 "
                "`./cb-manuscript init`으로 0 또는 1을 명시하세요."
            )
        values.update(development_values)
        declared_raw = development_values.get(ENVIRONMENT_KEY, "")
        declared = _parse_environment(
            declared_raw,
            source=str(layout.dev_env),
        )
        if declared != DEVELOPMENT:
            raise ManuscriptError(
                f"{layout.dev_env}의 {ENVIRONMENT_KEY}는 "
                f"{DEVELOPMENT!r}이어야 합니다."
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
                    f"{layout.env}의 {ENVIRONMENT_KEY}는 "
                    f"{PRODUCTION!r}이어야 합니다."
                )

    effective = _parse_environment(
        _effective(values, ENVIRONMENT_KEY),
        source="유효 설정",
    )
    if effective != environment:
        raise ManuscriptError(
            f"선택한 환경 {environment!r}과 유효 {ENVIRONMENT_KEY} "
            f"{effective!r}이 충돌합니다."
        )
    return environment, values


def _parse_port(values: Mapping[str, str]) -> int:
    port = _effective(values, "CB_API_PORT").strip() or "8765"
    try:
        parsed_port = int(port)
    except ValueError as exc:
        raise ManuscriptError("CB_API_PORT는 정수여야 합니다.") from exc
    if not 1 <= parsed_port <= 65535:
        raise ManuscriptError("CB_API_PORT는 1~65535 범위여야 합니다.")
    return parsed_port


def _validate_api_bind(values: Mapping[str, str]) -> str:
    raw = _effective(values, "CB_API_BIND").strip()
    if not raw:
        raise ManuscriptError("CB_API_BIND에 호스트가 게시할 IPv4 주소가 필요합니다.")
    try:
        address = ipaddress.ip_address(raw)
    except ValueError as exc:
        raise ManuscriptError(
            "CB_API_BIND는 hostname이 아닌 단일 IPv4 주소여야 합니다."
        ) from exc
    if not isinstance(address, ipaddress.IPv4Address):
        raise ManuscriptError("CB_API_BIND는 IPv4 주소여야 합니다.")
    if address.is_unspecified or address.is_multicast:
        raise ManuscriptError(
            "CB_API_BIND에는 0.0.0.0 또는 multicast 주소를 사용할 수 없습니다."
        )
    return str(address)


def _validate_dns_hostname(hostname: str, *, field: str) -> None:
    if not hostname or hostname.endswith(".") or "*" in hostname:
        raise ManuscriptError(f"{field}에는 정확한 DNS hostname이 필요합니다.")
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        raise ManuscriptError(f"{field}에는 IP가 아닌 DNS hostname이 필요합니다.")
    if len(hostname) > 253 or any(
        not DNS_LABEL_RE.fullmatch(label) for label in hostname.split(".")
    ):
        raise ManuscriptError(f"{field}의 DNS hostname 형식이 잘못되었습니다.")


def _split_url(raw: str, *, field: str):
    if not raw:
        raise ManuscriptError(f"{field}가 필요합니다.")
    if "*" in raw:
        raise ManuscriptError(f"{field}에는 wildcard를 사용할 수 없습니다.")
    try:
        parsed = urlsplit(raw)
    except ValueError as exc:
        raise ManuscriptError(f"{field} 형식이 잘못되었습니다.") from exc
    if (
        not parsed.scheme
        or not parsed.netloc
        or parsed.netloc.endswith(":")
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ManuscriptError(f"{field} 형식이 잘못되었습니다.")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ManuscriptError(f"{field}의 port 형식이 잘못되었습니다.") from exc
    if port is not None and port < 1:
        raise ManuscriptError(f"{field}의 port는 1~65535 범위여야 합니다.")
    return parsed


def _validate_public_url(
    values: Mapping[str, str],
    *,
    environment: str,
    bind: str,
    port: int,
) -> None:
    # 이 값은 Compose service의 env_file에서 컨테이너로 들어간다. 프로세스 환경은
    # 컨테이너에 전달되지 않으므로 여기서도 파일의 유효값을 그대로 검사한다.
    raw = values.get("CLAIRE_PUBLIC_URL", "")
    if raw != raw.strip():
        raise ManuscriptError("CLAIRE_PUBLIC_URL에는 바깥 공백을 사용할 수 없습니다.")
    parsed = _split_url(raw, field="CLAIRE_PUBLIC_URL")
    if parsed.path not in {"", "/"}:
        raise ManuscriptError("CLAIRE_PUBLIC_URL은 root 경로만 사용할 수 있습니다.")

    if environment == DEVELOPMENT:
        expected_authority = f"{bind}:{port}"
        if parsed.scheme != "http" or parsed.netloc != expected_authority:
            raise ManuscriptError(
                "development의 CLAIRE_PUBLIC_URL은 "
                f"http://{expected_authority}/ 이어야 합니다."
            )
        return

    if parsed.scheme != "https":
        raise ManuscriptError(
            "production의 CLAIRE_PUBLIC_URL은 외부 reverse proxy의 "
            "https URL이어야 합니다."
        )
    hostname = parsed.hostname or ""
    _validate_dns_hostname(hostname, field="CLAIRE_PUBLIC_URL")


def _validate_cors_origins(
    values: Mapping[str, str],
    *,
    environment: str,
) -> None:
    # CLAIRE_PUBLIC_URL과 마찬가지로 env_file에서 전달되는 실제 컨테이너 값을
    # 검사한다. 항목 사이 공백은 앱과 동일하게 허용한다.
    raw = values.get("CLAIRE_CORS_ALLOWED_ORIGINS", "").strip()
    if not raw:
        return
    seen: set[str] = set()
    for origin in (item.strip() for item in raw.split(",")):
        if not origin:
            raise ManuscriptError(
                "CLAIRE_CORS_ALLOWED_ORIGINS에는 빈 origin을 사용할 수 없습니다."
            )
        if origin in seen:
            raise ManuscriptError(
                f"CLAIRE_CORS_ALLOWED_ORIGINS에 중복된 origin이 있습니다: {origin}"
            )
        parsed = _split_url(origin, field="CLAIRE_CORS_ALLOWED_ORIGINS")
        if parsed.path:
            raise ManuscriptError(
                "CLAIRE_CORS_ALLOWED_ORIGINS에는 path를 포함할 수 없습니다."
            )
        if parsed.scheme not in {"http", "https"}:
            raise ManuscriptError(
                "CLAIRE_CORS_ALLOWED_ORIGINS는 http 또는 https origin만 허용합니다."
            )
        if environment == PRODUCTION and parsed.scheme != "https":
            raise ManuscriptError(
                "production의 CLAIRE_CORS_ALLOWED_ORIGINS는 https만 허용합니다."
            )
        hostname = parsed.hostname
        if not hostname:
            raise ManuscriptError(
                "CLAIRE_CORS_ALLOWED_ORIGINS에는 hostname이 필요합니다."
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
            "CLAIRE_INJECT_TOKEN은 URL-safe 문자로 된 32~128자 토큰이어야 합니다. "
            "`./cb-manuscript init`으로 빈 값을 생성할 수 있습니다."
        )
    if readonly and not WEB_TOKEN_RE.fullmatch(readonly):
        raise ManuscriptError(
            "CLAIRE_READONLY_TOKEN은 비워 두거나 URL-safe 문자로 된 "
            "32~128자 토큰이어야 합니다."
        )
    if readonly and secrets.compare_digest(owner, readonly):
        raise ManuscriptError(
            "CLAIRE_INJECT_TOKEN과 CLAIRE_READONLY_TOKEN은 서로 달라야 합니다."
        )


def _validate_anonymous_readonly(values: Mapping[str, str]) -> bool:
    raw = values.get(ANONYMOUS_READONLY_KEY)
    if raw is None:
        return False
    if raw not in {"0", "1"}:
        raise ManuscriptError(
            f"{ANONYMOUS_READONLY_KEY}는 정확히 0 또는 1이어야 합니다."
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
            "CB_PROJECT_NAME은 소문자, 숫자, '-', '_'만 사용하고 "
            "소문자 또는 숫자로 시작해야 합니다."
        )

    timeout_text = _effective(values, "CB_WAIT_TIMEOUT").strip()
    if not timeout_text:
        timeout_text = str(DEFAULT_WAIT_TIMEOUT)
    try:
        wait_timeout = int(timeout_text)
    except ValueError as exc:
        raise ManuscriptError("CB_WAIT_TIMEOUT은 초 단위 정수여야 합니다.") from exc
    if not 1 <= wait_timeout <= 86400:
        raise ManuscriptError("CB_WAIT_TIMEOUT은 1~86400 범위여야 합니다.")

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
            raise ManuscriptError(f"필수 파일이 없습니다: {path}")
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
        raise ManuscriptError(f"환경 예시 파일이 없습니다: {source}")
    _atomic_write(target, source.read_text(encoding="utf-8"), mode=0o600)
    return True


def _ensure_environment_selector(path: Path, expected: str) -> bool:
    """기존 env 파일에 새 canonical selector를 안전하게 보충한다."""

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
        raise ManuscriptError(f"{path}에 {ENVIRONMENT_KEY}가 중복되어 있습니다.")
    if matches:
        index, value = matches[0]
        if value == expected:
            os.chmod(path, 0o600)
            return False
        if value:
            raise ManuscriptError(
                f"{path}의 {ENVIRONMENT_KEY}는 {expected!r}이어야 합니다."
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
    """빈 inject token을 생성하고 기존 값은 충분한 강도인지 확인한다."""

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
                    f"{path}의 CLAIRE_INJECT_TOKEN은 URL-safe 문자로 된 "
                    "32~128자 토큰이어야 합니다."
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
    """누락된 익명 읽기 설정을 안전한 기본값 0으로 보충한다."""

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
            f"{path}에 {ANONYMOUS_READONLY_KEY}가 중복되어 있습니다."
        )
    if matches:
        if matches[0] not in {"0", "1"}:
            raise ManuscriptError(
                f"{path}의 {ANONYMOUS_READONLY_KEY}는 정확히 0 또는 1이어야 합니다."
            )
        os.chmod(path, 0o600)
        return False

    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    lines.append(f"{ANONYMOUS_READONLY_KEY}=0\n")
    _atomic_write(path, "".join(lines), mode=0o600)
    return True


def command_init(layout: Layout) -> int:
    created_env = _copy_once(layout.env_example, layout.env)
    dev_source = (
        layout.dev_env_example
        if layout.dev_env_example.is_file()
        else layout.env_example
    )
    created_dev_env = _copy_once(dev_source, layout.dev_env)
    selector_migrated = _ensure_environment_selector(layout.env, PRODUCTION)
    dev_selector_migrated = _ensure_environment_selector(
        layout.dev_env,
        DEVELOPMENT,
    )
    anonymous_migrated = _ensure_anonymous_readonly(layout.env)
    dev_anonymous_migrated = _ensure_anonymous_readonly(layout.dev_env)
    token_created = _ensure_inject_token(layout.env)
    _ensure_inject_token(layout.dev_env)
    layout.data.mkdir(parents=True, exist_ok=True)
    layout.vault.mkdir(parents=True, exist_ok=True)

    print(f".env: {'생성' if created_env else '유지'}")
    print(f".env.dev: {'생성' if created_dev_env else '유지'}")
    print(
        f"{ENVIRONMENT_KEY}: "
        f"{'보충' if selector_migrated or dev_selector_migrated else '유지'}"
    )
    print(
        f"{ANONYMOUS_READONLY_KEY}: "
        f"{'보충(기본 0)' if anonymous_migrated or dev_anonymous_migrated else '유지'}"
    )
    print(f"CLAIRE_INJECT_TOKEN: {'생성' if token_created else '유지'}")
    print("data/, vault/: 준비됨")
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
                f"다른 cb-manuscript 작업이 진행 중입니다: {self.path}", 73
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
                f"다른 백업/복원 작업이 진행 중입니다: {self.path}", 73
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
        raise ManuscriptError(f"지원하지 않는 백업 구성요소입니다: {name}")


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _configured_host_path(runtime: Runtime, key: str, default: str) -> Path:
    raw = _effective(runtime.values, key).strip() or default
    if "\x00" in raw:
        raise ManuscriptError(f"{key}에 NUL 문자를 사용할 수 없습니다.")
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        if not (raw.startswith("./") or raw.startswith("../")):
            raise ManuscriptError(
                f"{key}={raw!r}은 host bind 경로로 식별할 수 없습니다. "
                "./ 또는 ../로 시작하는 상대 경로나 절대 경로를 사용하세요."
            )
        candidate = runtime.layout.root / candidate
    if candidate.is_symlink():
        raise ManuscriptError(f"{key} 최상위 경로는 symlink일 수 없습니다: {candidate}")
    return candidate.resolve()


def _database_relative_path(runtime: Runtime) -> Path:
    raw = _effective(runtime.values, "CLAIRE_DB_PATH").strip() or "data/claire.db"
    if "\x00" in raw or "\\" in raw:
        raise ManuscriptError("CLAIRE_DB_PATH 형식이 잘못되었습니다.")
    source = PurePosixPath(raw)
    if any(part in {"", ".", ".."} for part in source.parts):
        raise ManuscriptError(
            "CLAIRE_DB_PATH는 /app/data 아래의 정규화된 경로여야 합니다."
        )
    if source.is_absolute():
        container_path = source
    else:
        container_path = PurePosixPath("/app") / source
    try:
        relative = container_path.relative_to(PurePosixPath("/app/data"))
    except ValueError as exc:
        raise ManuscriptError(
            "CLAIRE_DB_PATH는 컨테이너의 /app/data 아래에 있어야 백업할 수 있습니다."
        ) from exc
    if not relative.parts:
        raise ManuscriptError("CLAIRE_DB_PATH는 파일 경로여야 합니다.")
    return Path(*relative.parts)


def resolve_storage(runtime: Runtime) -> StorageLayout:
    data = _configured_host_path(runtime, "CB_DATA_DIR", "./data")
    vault = _configured_host_path(runtime, "CB_VAULT_DIR", "./vault")
    backup_root = runtime.layout.backups.resolve()
    repository = runtime.layout.root.resolve()
    home = Path.home().resolve()

    for name, path in (("CB_DATA_DIR", data), ("CB_VAULT_DIR", vault)):
        if path in {Path("/"), repository, home} or len(path.parts) < 3:
            raise ManuscriptError(f"{name}이 너무 넓은 경로를 가리킵니다: {path}")
        if _is_within(path, backup_root) or _is_within(backup_root, path):
            raise ManuscriptError(
                f"{name}과 backups 경로가 서로 포함되어 백업할 수 없습니다: {path}"
            )
    if (
        data == vault
        or _is_within(data, vault)
        or _is_within(vault, data)
    ):
        raise ManuscriptError("CB_DATA_DIR과 CB_VAULT_DIR은 같거나 중첩될 수 없습니다.")
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
        raise ManuscriptError(f"상태 파일을 읽을 수 없습니다: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ManuscriptError(f"상태 파일 형식이 잘못되었습니다: {path}")
    return value


def _profile_state(runtime: Runtime) -> dict[str, object] | None:
    return _read_state(runtime.layout.state_for(dev=runtime.dev))


def _require_stable_project(runtime: Runtime, state: Mapping[str, object]) -> None:
    previous = state.get("project")
    if isinstance(previous, str) and previous and previous != runtime.project:
        state_path = runtime.layout.state_for(dev=runtime.dev)
        raise ManuscriptError(
            f"CB_PROJECT_NAME이 기존 {previous!r}에서 {runtime.project!r}(으)로 "
            "변경되었습니다. 중복 스택 방지를 위해 중단합니다. 이전 이름으로 "
            "`cb-manuscript down`을 실행한 뒤, 전환을 확인하고 "
            f"{state_path}을 제거하세요."
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
            "작업 트리가 깨끗하지 않아 update를 중단합니다. 변경을 commit/stash하세요."
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
        raise ManuscriptError("현재 브랜치에 upstream이 없어 update할 수 없습니다.")
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
                "cb-manuscript: 오류 후 다음 writer를 재개하지 못했습니다: "
                + ", ".join(failures),
                file=sys.stderr,
            )
        raise
    else:
        failures = _resume_writers(runtime, containers)
        if failures:
            raise ManuscriptError(
                "백업은 생성되었지만 다음 writer를 재개하지 못했습니다: "
                + ", ".join(failures)
            )


def _lstat(path: Path) -> os.stat_result:
    try:
        return path.lstat()
    except FileNotFoundError as exc:
        raise ManuscriptError(f"경로가 없습니다: {path}") from exc
    except OSError as exc:
        raise ManuscriptError(f"경로를 검사할 수 없습니다: {path}: {exc}") from exc


def _assert_regular_directory(path: Path, *, label: str) -> None:
    mode = _lstat(path).st_mode
    if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
        raise ManuscriptError(f"{label}은 symlink가 아닌 디렉터리여야 합니다: {path}")


def _assert_regular_file(path: Path, *, label: str) -> None:
    mode = _lstat(path).st_mode
    if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
        raise ManuscriptError(f"{label}은 symlink가 아닌 일반 파일이어야 합니다: {path}")


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
        raise ManuscriptError(f"백업 대상에 symlink가 있습니다: {source}")
    if stat.S_ISREG(mode):
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination, follow_symlinks=False)
        return
    if not stat.S_ISDIR(mode):
        raise ManuscriptError(f"백업 대상에 특수 파일이 있습니다: {source}")

    destination.mkdir(parents=True, exist_ok=True)
    os.chmod(destination, stat.S_IMODE(mode))
    try:
        children = sorted(source.iterdir(), key=lambda item: item.name)
    except OSError as exc:
        raise ManuscriptError(f"디렉터리를 읽을 수 없습니다: {source}: {exc}") from exc
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
                    f"SQLite quick_check가 실패했습니다: {path}: {quick_rows[:5]}"
                )
            foreign_keys = conn.execute("PRAGMA foreign_key_check").fetchmany(10)
            if foreign_keys:
                raise ManuscriptError(
                    f"SQLite foreign_key_check가 실패했습니다: {path}: "
                    f"{foreign_keys[:5]}"
                )
            row = conn.execute(
                "SELECT value FROM meta WHERE key='schema_version'"
            ).fetchone()
            if row is None:
                raise ManuscriptError(
                    f"SQLite schema_version을 찾을 수 없습니다: {path}"
                )
            try:
                schema_version = int(row[0])
            except (TypeError, ValueError) as exc:
                raise ManuscriptError(
                    f"SQLite schema_version이 잘못되었습니다: {path}: {row[0]!r}"
                ) from exc
        finally:
            conn.close()
    except ManuscriptError:
        raise
    except sqlite3.Error as exc:
        raise ManuscriptError(f"SQLite DB를 검증할 수 없습니다: {path}: {exc}") from exc
    return {
        "path": "",
        "schema_version": schema_version,
        "quick_check": "ok",
        "foreign_key_check": "ok",
    }


def _snapshot_database(source: Path, destination: Path) -> dict[str, object]:
    source_stat = _lstat(source)
    if stat.S_ISLNK(source_stat.st_mode) or not stat.S_ISREG(source_stat.st_mode):
        raise ManuscriptError(f"SQLite DB가 일반 파일이 아닙니다: {source}")
    _validate_sqlite_database(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise ManuscriptError(f"SQLite snapshot 대상이 이미 있습니다: {destination}")
    source_conn = None
    destination_conn = None
    try:
        source_conn = sqlite3.connect(_sqlite_uri(source), uri=True, timeout=5.0)
        destination_conn = sqlite3.connect(str(destination))
        source_conn.backup(destination_conn)
        mode = destination_conn.execute("PRAGMA journal_mode=DELETE").fetchone()
        if mode is None or str(mode[0]).lower() != "delete":
            raise ManuscriptError(
                "SQLite snapshot을 단일-file DELETE journal mode로 만들 수 없습니다."
            )
    except sqlite3.Error as exc:
        _remove_path(destination)
        raise ManuscriptError(f"SQLite snapshot 생성에 실패했습니다: {exc}") from exc
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
        raise ManuscriptError(f"파일 hash를 계산할 수 없습니다: {path}: {exc}") from exc
    return digest.hexdigest()


def _scan_payload(payload: Path) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []

    def visit(path: Path) -> None:
        path_stat = _lstat(path)
        relative = path.relative_to(payload).as_posix()
        mode = path_stat.st_mode
        if stat.S_ISLNK(mode):
            raise ManuscriptError(f"backup payload에 symlink가 있습니다: {relative}")
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
        raise ManuscriptError(f"backup payload에 특수 파일이 있습니다: {relative}")

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
        raise ManuscriptError(f"현재 DB schema 버전을 읽을 수 없습니다: {path}") from exc
    match = re.search(r"(?m)^SCHEMA_VERSION\s*=\s*([0-9]+)\s*$", text)
    if match is None:
        raise ManuscriptError(f"현재 DB schema 버전을 찾을 수 없습니다: {path}")
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
            "backup 최상위에는 manifest.json과 payload만 있어야 합니다."
        )

    manifest_path = root / "manifest.json"
    _assert_regular_file(manifest_path, label="backup manifest")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ManuscriptError(f"backup manifest를 읽을 수 없습니다: {exc}") from exc
    if not isinstance(manifest, dict):
        raise ManuscriptError("backup manifest는 JSON object여야 합니다.")
    if manifest.get("format_version") != BACKUP_FORMAT_VERSION:
        raise ManuscriptError(
            f"지원하지 않는 backup format_version입니다: "
            f"{manifest.get('format_version')!r}"
        )
    backup_id = manifest.get("id")
    if not isinstance(backup_id, str) or not BACKUP_ID_RE.fullmatch(backup_id):
        raise ManuscriptError(f"backup id 형식이 잘못되었습니다: {backup_id!r}")
    for key in ("created_at", "profile", "project", "cb_manuscript_version"):
        if not isinstance(manifest.get(key), str) or not manifest[key]:
            raise ManuscriptError(f"backup manifest의 {key} 값이 잘못되었습니다.")

    raw_components = manifest.get("components")
    if (
        not isinstance(raw_components, list)
        or not raw_components
        or any(component not in BACKUP_COMPONENTS for component in raw_components)
        or len(set(raw_components)) != len(raw_components)
    ):
        raise ManuscriptError("backup manifest의 components 값이 잘못되었습니다.")
    components = tuple(raw_components)
    if tuple(component for component in BACKUP_COMPONENTS if component in components) != components:
        raise ManuscriptError("backup manifest의 components 순서가 잘못되었습니다.")

    raw_entries = manifest.get("entries")
    if not isinstance(raw_entries, list) or not raw_entries:
        raise ManuscriptError("backup manifest에 entries가 없습니다.")
    expected: dict[str, dict[str, object]] = {}
    for value in raw_entries:
        if not isinstance(value, dict):
            raise ManuscriptError("backup manifest entry 형식이 잘못되었습니다.")
        raw_path = value.get("path")
        entry_type = value.get("type")
        entry_mode = value.get("mode")
        if not isinstance(raw_path, str) or not raw_path:
            raise ManuscriptError("backup manifest entry path가 잘못되었습니다.")
        logical = PurePosixPath(raw_path)
        if (
            logical.is_absolute()
            or any(part in {"", ".", ".."} for part in logical.parts)
            or logical.as_posix() != raw_path
            or logical.parts[0] not in components
        ):
            raise ManuscriptError(f"안전하지 않은 backup entry path입니다: {raw_path!r}")
        if raw_path in expected:
            raise ManuscriptError(f"중복 backup entry path입니다: {raw_path}")
        if (
            entry_type not in {"file", "directory"}
            or not isinstance(entry_mode, str)
            or re.fullmatch(r"[0-7]{4}", entry_mode) is None
        ):
            raise ManuscriptError(f"backup entry metadata가 잘못되었습니다: {raw_path}")
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
                    f"backup file metadata가 잘못되었습니다: {raw_path}"
                )
        if set(value) != required:
            raise ManuscriptError(
                f"backup entry에 알 수 없는 필드가 있습니다: {raw_path}"
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
            "backup payload 무결성 검증에 실패했습니다"
            f" (missing={missing[:3]}, extra={extra[:3]}, changed={changed[:3]})."
        )
    for component in components:
        entry = expected.get(component)
        if entry is None or entry.get("type") != "directory":
            raise ManuscriptError(f"backup component가 없습니다: {component}")

    file_entries = [entry for entry in actual_entries if entry["type"] == "file"]
    totals = manifest.get("totals")
    expected_totals = {
        "files": len(file_entries),
        "bytes": sum(int(entry["size"]) for entry in file_entries),
    }
    if totals != expected_totals:
        raise ManuscriptError("backup manifest의 totals가 payload와 일치하지 않습니다.")

    database = manifest.get("database")
    if "data" in components:
        if not isinstance(database, dict):
            raise ManuscriptError("data backup에 database metadata가 없습니다.")
        database_path = database.get("path")
        if not isinstance(database_path, str):
            raise ManuscriptError("database path metadata가 잘못되었습니다.")
        logical_database = PurePosixPath(database_path)
        if (
            logical_database.is_absolute()
            or not logical_database.parts
            or logical_database.parts[0] != "data"
            or any(part in {"", ".", ".."} for part in logical_database.parts)
        ):
            raise ManuscriptError("database path metadata가 안전하지 않습니다.")
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
            raise ManuscriptError("database metadata가 실제 DB와 일치하지 않습니다.")
    elif database is not None:
        raise ManuscriptError("data가 없는 backup에 database metadata가 있습니다.")
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
        raise ManuscriptError(f"archive staging 파일이 이미 있습니다: {destination}")
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
        raise ManuscriptError(f"backup archive 생성에 실패했습니다: {exc}") from exc


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
                        f"안전하지 않거나 중복된 archive member입니다: {member.name!r}"
                    )
                if not (member.isdir() or member.isfile()):
                    raise ManuscriptError(
                        f"archive의 link/특수 파일을 허용하지 않습니다: {member.name}"
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
                        f"archive member를 읽을 수 없습니다: {member.name}"
                    )
                with source_stream, target.open("xb") as output:
                    shutil.copyfileobj(source_stream, output, length=1024 * 1024)
                os.chmod(target, member.mode & 0o7777)
    except ManuscriptError:
        raise
    except (OSError, tarfile.TarError, EOFError) as exc:
        raise ManuscriptError(f"backup archive를 읽을 수 없습니다: {exc}") from exc


@contextmanager
def _materialized_backup(
    source: Path,
    *,
    temporary_parent: Path | None = None,
) -> Iterator[tuple[Path, dict[str, object]]]:
    source_stat = _lstat(source)
    if stat.S_ISLNK(source_stat.st_mode):
        raise ManuscriptError(f"backup source는 symlink일 수 없습니다: {source}")
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
                f"backup source는 일반 파일 또는 디렉터리여야 합니다: {source}"
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
            "오늘의 backup이 이미 있습니다: "
            + ", ".join(str(path) for path in existing)
            + ". 교체하려면 --replace를 명시하세요."
        )
    for path in existing:
        mode = _lstat(path).st_mode
        if stat.S_ISLNK(mode) or not (
            stat.S_ISDIR(mode) or stat.S_ISREG(mode)
        ):
            raise ManuscriptError(f"기존 backup 형식이 안전하지 않습니다: {path}")

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
        raise ManuscriptError("backup 구성요소는 data 또는 vault여야 합니다.")
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
                "오늘의 backup이 이미 있습니다: "
                + ", ".join(str(path) for path in existing)
                + ". 교체하려면 --replace를 명시하세요."
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

    print(f"backup 완료: {target}")
    print(f"  id={backup_id} · format={output_format} · components={','.join(components)}")
    return 0


def _verify_component_copy(
    root: Path,
    component: str,
    manifest: Mapping[str, object],
) -> None:
    expected_entries = manifest.get("entries")
    if not isinstance(expected_entries, list):
        raise ManuscriptError("backup manifest entries가 잘못되었습니다.")
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
            raise ManuscriptError(f"restore staging에 symlink가 있습니다: {key}")
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
        raise ManuscriptError(f"restore staging에 특수 파일이 있습니다: {key}")

    visit(root, PurePosixPath(component))
    if expected != actual:
        raise ManuscriptError(
            f"{component} restore staging이 backup manifest와 일치하지 않습니다."
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
                raise ManuscriptError("restore staging 경로 충돌이 발생했습니다.")
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
            f"root-owned restore rollback 내용을 정리하지 못했습니다: {path}"
        )
    try:
        path.rmdir()
    except OSError as exc:
        raise ManuscriptError(f"restore rollback 경로를 제거할 수 없습니다: {path}") from exc


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
        raise ManuscriptError("restore할 backup 파일 또는 폴더 경로가 필요합니다.")
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
        raise ManuscriptError("backup manifest components가 잘못되었습니다.")
    available = set(available_value)
    selected = set(raw) if raw else available
    if not selected or not selected.issubset(available):
        raise ManuscriptError(
            "요청한 restore 구성요소가 backup에 없습니다: "
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
            "restore는 현재 데이터를 교체합니다. 실행하려면 --yes를 명시하세요."
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
                    f"restore source가 {component} target 안에 있어 사용할 수 없습니다."
                )

        with _materialized_backup(
            source,
            temporary_parent=runtime.layout.state_dir,
        ) as (materialized, manifest):
            expected_profile = "development" if runtime.dev else "production"
            if manifest.get("profile") != expected_profile:
                raise ManuscriptError(
                    f"backup profile이 현재 profile과 다릅니다: "
                    f"{manifest.get('profile')!r} != {expected_profile!r}"
                )
            if manifest.get("project") != runtime.project:
                raise ManuscriptError(
                    f"backup project가 현재 project와 다릅니다: "
                    f"{manifest.get('project')!r} != {runtime.project!r}"
                )
            components = _restore_components(raw_components, manifest)
            database = manifest.get("database")
            if "data" in components:
                if not isinstance(database, dict):
                    raise ManuscriptError("data backup에 database metadata가 없습니다.")
                expected_database = (
                    PurePosixPath("data")
                    .joinpath(*storage.database_relative.parts)
                    .as_posix()
                )
                if database.get("path") != expected_database:
                    raise ManuscriptError(
                        "backup DB 경로와 현재 CLAIRE_DB_PATH가 다릅니다: "
                        f"{database.get('path')!r} != {expected_database!r}"
                    )
                schema_version = database.get("schema_version")
                if (
                    not isinstance(schema_version, int)
                    or isinstance(schema_version, bool)
                    or schema_version > _current_schema_version(runtime.layout)
                ):
                    raise ManuscriptError(
                        "현재 코드보다 새로운 DB schema backup은 restore할 수 없습니다."
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
                        "restore 후 writer를 재개하지 못했습니다: "
                        + ", ".join(resume_failures)
                    )
                if containers.project and not _wait_for_restored_liveness(runtime):
                    raise ManuscriptError("restore 후 liveness 검증에 실패했습니다.")
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
                        "restore rollback에 실패했습니다. writer를 중지 상태로 "
                        f"유지합니다. {journal}: "
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
                            "기존 데이터는 복구했지만 writer 재개에 실패했습니다: "
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
                            f"cb-manuscript: 오래된 restore rollback 경로를 "
                            f"정리하지 못했습니다: {swap.rollback}: {exc}",
                            file=sys.stderr,
                        )
                if journal is not None:
                    _remove_path(journal)
            finally:
                for swap in swaps:
                    _remove_path(swap.staging)

    print(
        f"restore 완료: {source} · components={','.join(components)}"
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
            "cb-manuscript: 새 스택 기동/검증에 실패했습니다. "
            "실패 상태를 보존했으므로 `./cb-manuscript status`와 "
            "`./cb-manuscript logs`로 확인하세요.",
            file=sys.stderr,
        )
        raise


def command_install(runtime: Runtime) -> int:
    with InstanceLock(runtime):
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
        raise ManuscriptError(f"원격 배포 스크립트가 없습니다: {layout.deploy_script}")
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
        return "cb-manuscript 안전 정책에 분류되지 않은 claire 전역 옵션"
    if command in APP_GUARDED_COMMANDS:
        return APP_GUARDED_COMMANDS[command]
    applies_merge = any(
        token.startswith("--")
        and len(token) > 2
        and "--apply".startswith(token)
        for token in args[1:]
    )
    if command == "dedup-merge" and applies_merge:
        return "문서를 삭제하는 파괴적 유지보수 명령"
    if command == "recanonicalize" and "--dry-run" not in args[1:]:
        return "영속 데이터를 변경하는 유지보수 명령"
    if command in APP_ONE_OFF_COMMANDS:
        return None
    return "cb-manuscript 안전 정책에 분류되지 않은 앱 명령"


def _prepare_app_args(args: Sequence[str]) -> tuple[str, ...]:
    remaining = list(args)
    advanced = bool(remaining[:1] == [APP_ADVANCED_OPTION])
    if advanced:
        remaining.pop(0)
    if remaining[:1] == ["--"]:
        remaining.pop(0)
    if not remaining:
        raise ManuscriptError(
            "app 뒤에 claire 명령이 필요합니다. "
            "`./cb-manuscript app --help`로 목록을 확인하세요."
        )

    reason = _app_guard_reason(remaining)
    if reason and not advanced:
        command = _display_argv(remaining)
        raise ManuscriptError(
            f"`app {command}`은(는) 기본 실행이 차단됩니다: {reason}. "
            f"보호 절차 없이 직접 실행하려면 `app {APP_ADVANCED_OPTION} "
            f"{command}`을 사용하세요."
        )
    if advanced:
        detail = f": {reason}" if reason else ""
        print(
            "cb-manuscript: 경고: app --advanced는 서비스 정지, migration 순서, "
            f"백업 또는 복구 가능성을 보장하지 않습니다{detail}",
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
            raise ManuscriptError("compose 뒤에 Docker Compose 인수가 필요합니다.")
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
        raise ManuscriptError(f"알 수 없는 명령: {command}")

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
            f"Claire 컨테이너 관리 도구.\n"
            f"환경은 {ENVIRONMENT_KEY}의 {DEVELOPMENT}/{PRODUCTION}으로 선택하며 `dev`는 개발 호환 별칭입니다."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "예시:\n"
            "  ./cb-manuscript init            # 설정 및 디렉터리 초기화\n"
            "  ./cb-manuscript doctor          # 설정 및 환경 사전 점검\n"
            "  ./cb-manuscript install         # 빌드, 마이그레이션, 기동 및 헬스체크\n"
            "  ./cb-manuscript update          # 소스 갱신 후 재빌드 및 재기동\n"
            "  ./cb-manuscript status          # 서비스 컨테이너 상태 확인\n"
            "  ./cb-manuscript logs -f api     # api 서비스 실시간 로그 확인\n"
            "  ./cb-manuscript shell           # api 컨테이너 대화형 셸 접속\n"
            "  ./cb-manuscript health          # API liveness 확인\n"
            "  ./cb-manuscript app health      # 전체 애플리케이션 상태 진단\n"
            "  ./cb-manuscript dev <command>   # 개발 환경(development)으로 실행"
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # 1. init
    subparsers.add_parser(
        "init",
        help=".env/.env.dev와 data/vault를 준비",
        description=(
            ".env, .env.dev 환경 파일 템플릿을 생성하고 data/ 및 vault/ 디렉터리를 초기화합니다.\n\n"
            "작업 내용:\n"
            "  - .env.example, .env.dev.example에서 설정 파일 복사 (기존 파일 보존)\n"
            "  - CLAIRE_INJECT_TOKEN, CLAIRE_READONLY_TOKEN 토큰 자동 생성\n"
            "  - data/, vault/ 디렉터리 생성 및 보안 권한(0700) 설정"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "예시:\n"
            "  ./cb-manuscript init          # 프로덕션 환경 설정 초기화\n"
            "  ./cb-manuscript dev init      # 개발 환경 설정 초기화"
        ),
    )

    # 2. doctor
    subparsers.add_parser(
        "doctor",
        help="설정과 Docker Compose를 점검",
        description=(
            "배포 환경 설정, Docker Compose 유효성, 네트워크 및 보안 제약을 사전 점검합니다.\n\n"
            "점검 항목:\n"
            "  - 환경 파일(.env / .env.dev) 존재 및 구문 유효성\n"
            "  - CB_API_BIND(IPv4), CB_API_PORT, CLAIRE_PUBLIC_URL 정합성\n"
            "  - 보안 토큰 및 익명 읽기 전용(CLAIRE_ANONYMOUS_READONLY) 설정\n"
            "  - docker compose config 구문 및 레거시 컨테이너 충돌\n"
            "  - data/, vault/ 디렉터리 권한(0700)"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "예시:\n"
            "  ./cb-manuscript doctor        # 프로덕션 설정 점검\n"
            "  ./cb-manuscript dev doctor    # 개발 설정 점검"
        ),
    )

    # 3. install
    subparsers.add_parser(
        "install",
        help="이미지 build, migrate, 기동, health 확인",
        description=(
            "초기 설치 및 서비스 구동 파이프라인을 실행합니다.\n\n"
            "실행 단계:\n"
            "  1. 사전 점검 (doctor)\n"
            "  2. Docker 이미지 빌드 (docker compose build)\n"
            "  3. 데이터베이스 마이그레이션 (claire migrate)\n"
            "  4. Compose 서비스 스택 기동 (up -d --wait)\n"
            "  5. API 헬스체크 (health)"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "예시:\n"
            "  ./cb-manuscript install       # 프로덕션 초기 설치 및 기동\n"
            "  ./cb-manuscript dev install   # 개발 환경 초기 설치 및 기동"
        ),
    )

    # 4. update
    update = subparsers.add_parser(
        "update",
        help="ff-only 갱신 후 안전한 순서로 재기동",
        description=(
            "Git 소스를 fast-forward 갱신하고 서비스를 안전한 순서로 재빌드·재기동합니다.\n\n"
            "실행 단계:\n"
            "  1. Git working tree 점검 및 fast-forward pull (--no-fetch 지정 시 생략)\n"
            "  2. Docker 이미지 재빌드\n"
            "  3. 서비스 안전 중지 및 DB 마이그레이션\n"
            "  4. Compose 서비스 재기동 (up -d --wait)\n"
            "  5. API 헬스체크"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "예시:\n"
            "  ./cb-manuscript update             # git pull 후 재빌드 및 재기동\n"
            "  ./cb-manuscript update --no-fetch  # 소스 fetch 없이 현재 코드로 재배치"
        ),
    )
    update.add_argument(
        "--no-fetch",
        action="store_true",
        help="git pull(fetch)을 생략하고 로컬 소스로 재배치",
    )

    # 5. backup
    backup = subparsers.add_parser(
        "backup",
        help="data/vault를 검증 가능한 폴더 또는 archive로 백업",
        description=(
            "data 디렉터리(DB 등)와 vault 디렉터리(암호화 데이터 등)를 백업합니다.\n"
            "백업 데이터의 체크섬과 메타데이터(manifest.json)를 자동 생성하여 무결성을 검증합니다."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "예시:\n"
            "  ./cb-manuscript backup                               # 디렉터리 형태 기본 백업 (backups/cb-YYYYMMDD/)\n"
            "  ./cb-manuscript backup --format archive              # 압축 아카이브 백업 (.tar.gz)\n"
            "  ./cb-manuscript backup --component data              # data 구성요소만 백업\n"
            "  ./cb-manuscript backup --replace                     # 같은 날짜의 기존 백업 교체"
        ),
    )
    backup.add_argument(
        "--format",
        choices=("directory", "archive"),
        default="directory",
        help="백업 형식: directory=backups/cb-YYYYMMDD/, archive=.tar.gz (기본값: directory)",
    )
    backup.add_argument(
        "--component",
        choices=BACKUP_COMPONENTS,
        action="append",
        help="백업할 구성요소 (data, vault 중 선택, 반복 지정 가능, 기본값: data+vault 전체)",
    )
    backup.add_argument(
        "--replace",
        action="store_true",
        help="같은 날짜의 기존 파일/폴더가 있으면 검증된 새 backup으로 교체",
    )

    # 6. restore
    restore = subparsers.add_parser(
        "restore",
        help="backup 폴더 또는 archive를 검증한 뒤 복원",
        description=(
            "생성된 백업 폴더 또는 아카이브(.tar.gz)의 무결성을 검증한 뒤 데이터를 복원합니다.\n"
            "안전을 위해 서비스가 중지된 상태에서 복원하는 것을 권장합니다."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "예시:\n"
            "  ./cb-manuscript restore backups/cb-20260818 --yes\n"
            "  ./cb-manuscript restore backups/cb-20260818.tar.gz --component data --yes"
        ),
    )
    restore.add_argument("source", help="복원할 backup 폴더 또는 .tar.gz archive 경로")
    restore.add_argument(
        "--component",
        choices=BACKUP_COMPONENTS,
        action="append",
        help="복원할 구성요소 (data, vault 중 선택, 반복 지정 가능, 기본값: backup 전체)",
    )
    restore.add_argument(
        "--yes",
        action="store_true",
        help="현재 선택 구성요소 교체를 확인 프롬프트 없이 명시적으로 승인",
    )

    # 7. health
    subparsers.add_parser(
        "health",
        help="실행 중인 API의 liveness 확인",
        description=(
            "실행 중인 API 컨테이너의 HTTP liveness 엔드포인트(GET /health)를 호출하여 응답을 점검합니다.\n\n"
            "참고: 애플리케이션 상세 상태(degraded, DB 통계 등) 진단은 './cb-manuscript app health'를 사용하세요."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "예시:\n"
            "  ./cb-manuscript health        # 프로덕션 API liveness 확인\n"
            "  ./cb-manuscript dev health    # 개발 API liveness 확인"
        ),
    )

    # 8. version
    subparsers.add_parser(
        "version",
        help="wrapper와 source 버전 표시",
        description="cb-manuscript 래퍼 스크립트 버전 및 패키징된 claire 패키지 버전을 표시합니다.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # 9. up
    subparsers.add_parser(
        "up",
        help=PASSTHROUGH_HELP["up"],
        description=(
            "Docker Compose 서비스를 기동합니다.\n"
            "인수 없이 실행 시 백그라운드 안전 기동(-d --wait --wait-timeout <timeout>)으로 동작합니다.\n"
            "추가 인수를 전달하면 해당 인수로 docker compose up을 실행합니다."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "주요 Compose 옵션 및 인수:\n"
            "  -d, --detach          백그라운드에서 컨테이너 실행\n"
            "  --build               컨테이너 시작 전 이미지 빌드\n"
            "  --no-deps             의존성 서비스는 시작하지 않음\n"
            "  [service ...]         기동할 특정 서비스 이름 (예: api, bot)\n\n"
            "예시:\n"
            "  ./cb-manuscript up                    # 기본 안전 기동 (-d --wait)\n"
            "  ./cb-manuscript up --build            # 이미지 재빌드 후 기동\n"
            "  ./cb-manuscript up -d api             # api 서비스만 백그라운드 기동"
        ),
    )

    # 10. down
    subparsers.add_parser(
        "down",
        help=PASSTHROUGH_HELP["down"],
        description=(
            "실행 중인 Docker Compose 서비스를 중지하고 컨테이너를 제거합니다.\n"
            "모든 프로필(bot 포함)을 대상으로 docker compose --profile * down을 실행합니다."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "주요 Compose 옵션:\n"
            "  -v, --volumes         네임드 볼륨 제거 (주의: 영속 데이터가 삭제될 수 있음)\n"
            "  --remove-orphans      Compose 파일에 정의되지 않은 고아 컨테이너 제거\n"
            "  -t, --timeout sec     종료 대기 제한시간(초)\n\n"
            "예시:\n"
            "  ./cb-manuscript down                  # 모든 서비스 안전 중지 및 제거\n"
            "  ./cb-manuscript down --remove-orphans # 고아 컨테이너 포함 제거"
        ),
    )

    # 11. restart
    subparsers.add_parser(
        "restart",
        help=PASSTHROUGH_HELP["restart"],
        description="실행 중인 Docker Compose 서비스를 재시작합니다 (docker compose restart).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "인수:\n"
            "  [service ...]         재시작할 서비스 이름 (지정하지 않으면 전체 서비스 재시작)\n\n"
            "예시:\n"
            "  ./cb-manuscript restart               # 전체 서비스 재시작\n"
            "  ./cb-manuscript restart api           # api 서비스만 재시작\n"
            "  ./cb-manuscript restart bot           # bot 서비스만 재시작"
        ),
    )

    # 12. status
    subparsers.add_parser(
        "status",
        help=PASSTHROUGH_HELP["status"],
        description="Docker Compose 컨테이너들의 실행 상태를 표시합니다 (docker compose ps).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "주요 Compose 옵션:\n"
            "  -a, --all             정지된 컨테이너를 포함한 모든 컨테이너 표시\n"
            "  --format format       출력 형식 (table, json 등)\n"
            "  --status status       상태 필터 (running, exited, paused 등)\n"
            "  [service ...]         조회할 특정 서비스 이름 (예: api, bot)\n\n"
            "예시:\n"
            "  ./cb-manuscript status                # 현재 실행 중인 서비스 상태\n"
            "  ./cb-manuscript status -a             # 중지된 컨테이너 포함 전체 상태\n"
            "  ./cb-manuscript status --format json  # JSON 형식 출력"
        ),
    )

    # 13. logs
    subparsers.add_parser(
        "logs",
        help=PASSTHROUGH_HELP["logs"],
        description=(
            "Docker Compose 서비스의 로그를 출력합니다 (docker compose logs).\n\n"
            "대상 서비스:\n"
            "  api                   API 웹 서버 컨테이너\n"
            "  bot                   Telegram 봇 컨테이너 (TELEGRAM_BOT_TOKEN 설정 시)"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "주요 Compose 옵션 및 인수:\n"
            "  -f, --follow          실시간 로그 스트리밍 (follow)\n"
            "  --tail lines          출력할 마지막 줄 수 지정 (기본값: all)\n"
            "  --since time          특정 시점 이후 로그 필터 (예: 10m, 2026-08-18T10:00:00)\n"
            "  -t, --timestamps      로그 타임스탬프 표시\n"
            "  [service ...]         로그를 확인할 대상 서비스 (예: api, bot)\n\n"
            "예시:\n"
            "  ./cb-manuscript logs                  # 전체 서비스 로그 출력\n"
            "  ./cb-manuscript logs api              # api 서비스 로그 출력\n"
            "  ./cb-manuscript logs -f api           # api 실시간 로그 스트리밍\n"
            "  ./cb-manuscript logs --tail 100 api   # api 서비스 최근 100줄 출력"
        ),
    )

    # 14. shell
    subparsers.add_parser(
        "shell",
        help=PASSTHROUGH_HELP["shell"],
        description=(
            "실행 중인 서비스 컨테이너 내에서 셸 또는 명령을 실행합니다 (docker compose exec).\n"
            "기본 서비스는 api이며, 기본 명령은 bash입니다."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "구문:\n"
            "  ./cb-manuscript shell [service] [-- command ...]\n\n"
            "예시:\n"
            "  ./cb-manuscript shell                 # api 컨테이너 bash 대화형 셸 접속\n"
            "  ./cb-manuscript shell bot             # bot 컨테이너 bash 접속\n"
            "  ./cb-manuscript shell api -- python3  # api 컨테이너에서 python3 REPL 실행\n"
            "  ./cb-manuscript shell api -- env      # api 컨테이너 환경변수 확인"
        ),
    )

    # 15. app
    subparsers.add_parser(
        "app",
        help=PASSTHROUGH_HELP["app"],
        description=(
            "현재 배포 환경의 .env 설정과 마운트 볼륨(data, vault)을 그대로 사용하여\n"
            "api 컨테이너 내에서 claire one-off 명령을 실행합니다 (docker compose run --rm --no-deps api claire ...).\n\n"
            "안전 정책:\n"
            "  - one-off 조회/유지보수 명령은 기본 실행 가능합니다.\n"
            "  - 서비스 수명주기 명령(migrate, bot, serve-api, reextract 등)은 보호를 위해 기본 차단됩니다.\n"
            "  - 차단된 명령을 실행하려면 --advanced 플래그를 사용하세요."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "주요 허용 명령 (one-off):\n"
            "  health, doctor, status, stats, search, ingest, refresh-run, recover-run, ...\n\n"
            "보호 대상 명령 (기본 차단):\n"
            "  migrate, bot, serve-api, recover-loop, refresh-loop, expand-loop, reextract\n\n"
            "옵션:\n"
            "  --advanced            안전 가드를 우회하여 보호 대상 명령 실행\n\n"
            "예시:\n"
            "  ./cb-manuscript app health            # 전체 애플리케이션 상세 상태 확인\n"
            "  ./cb-manuscript app doctor            # 앱 레벨 진단 실행\n"
            "  ./cb-manuscript app search \"키워드\"     # CLI 검색 실행\n"
            "  ./cb-manuscript app --help            # claire CLI의 전체 명령 도움말 표시\n"
            "  ./cb-manuscript app --advanced migrate # 보호 가드 우회하여 마이그레이션 실행"
        ),
    )

    # 16. compose
    subparsers.add_parser(
        "compose",
        help=PASSTHROUGH_HELP["compose"],
        description=(
            "cb-manuscript의 환경 설정(.env, project명 등)이 주입된 Docker Compose로\n"
            "임의의 인수를 직접 전달하는 고급 탈출구(escape hatch)입니다."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "구문:\n"
            "  ./cb-manuscript compose [--] <args ...>\n\n"
            "예시:\n"
            "  ./cb-manuscript compose ps\n"
            "  ./cb-manuscript compose config\n"
            "  ./cb-manuscript compose top\n"
            "  ./cb-manuscript compose -- exec api env"
        ),
    )

    # 17. remote
    remote = subparsers.add_parser(
        "remote",
        help="deploy.sh 원격 연결",
        description="deploy.sh를 통해 SSH 원격 호스트에 접속하여 배포 명령을 실행합니다 (production 전용).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "예시:\n"
            "  ./cb-manuscript remote install\n"
            "  ./cb-manuscript remote update"
        ),
    )
    remote_subparsers = remote.add_subparsers(dest="remote_action", required=True)
    remote_subparsers.add_parser(
        "install",
        help="원격 호스트에 cb-manuscript install 실행",
        description="SSH 원격 호스트에 접속하여 초기 설치(install)를 수행합니다.",
    )
    remote_subparsers.add_parser(
        "update",
        help="원격 호스트에 cb-manuscript update 실행",
        description="SSH 원격 호스트에 접속하여 업데이트(update)를 수행합니다.",
    )
    return parser


def _split_dev_prefix(argv: Sequence[str]) -> tuple[bool, list[str]]:
    args = list(argv)
    if args[:1] == ["dev"]:
        return True, args[1:]
    return False, args


def _validate_remote_environment(layout: Layout, *, legacy_dev: bool) -> None:
    if legacy_dev:
        raise ManuscriptError("remote install/update는 production 환경에서만 실행할 수 있습니다.")
    process_value = os.environ.get(ENVIRONMENT_KEY)
    if process_value is not None:
        environment = _parse_environment(process_value, source="프로세스 환경")
    else:
        values = read_dotenv(layout.env)
        environment = _parse_environment(
            values.get(ENVIRONMENT_KEY, ""),
            source=str(layout.env),
        )
    if environment != PRODUCTION:
        raise ManuscriptError(
            f"remote install/update는 {ENVIRONMENT_KEY}={PRODUCTION} 전용입니다."
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
                build_parser().error("remote action은 install 또는 update여야 합니다")
            if len(args) != 2:
                raise ManuscriptError("remote install/update는 추가 인수를 받지 않습니다.")
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
        raise ManuscriptError(f"알 수 없는 명령: {parsed.command}")
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
