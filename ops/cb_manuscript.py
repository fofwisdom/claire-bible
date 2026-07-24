#!/usr/bin/env python3
"""One entry point for Claire's local and remote container operations.

The wrapper intentionally treats dotenv files as data.  It never sources them,
and every external command is passed to ``subprocess`` as an argv sequence.
"""

from __future__ import annotations

import argparse
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import tempfile
from typing import Mapping, Sequence


VERSION = "0.1.0"
DEFAULT_PROJECT = "claire-bible"
DEFAULT_DEV_PROJECT = "claire-bible-dev"
DEFAULT_WAIT_TIMEOUT = 120
PROJECT_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
LEGACY_CONTAINERS = (
    "claire_bot",
    "claire_api",
    "claire_refresh",
    "claire_recover",
    "claire_expand",
    "claire_backup",
)
LOCKED_PASSTHROUGH_COMMANDS = {"up", "down", "restart", "app"}
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
    "reextract": "그래프를 재구축하고 기존 백업 구현에 의존하는 유지보수 명령",
    "backup": "현재 통합 운영 범위에서 제외된 백업 명령",
    "backup-loop": "현재 통합 운영 범위에서 제외된 백업 명령",
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
    dev: bool
    values: Mapping[str, str]
    project: str
    wait_timeout: int
    bot_enabled: bool

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
        values[key] = _dotenv_value(raw, path, lineno)
    return values


def _effective(values: Mapping[str, str], key: str) -> str:
    if key in os.environ:
        return os.environ[key]
    return values.get(key, "")


def load_runtime(layout: Layout, *, dev: bool) -> Runtime:
    values = read_dotenv(layout.env)
    if dev:
        values.update(read_dotenv(layout.dev_env))

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

    port = _effective(values, "CB_API_PORT").strip()
    if port:
        try:
            parsed_port = int(port)
        except ValueError as exc:
            raise ManuscriptError("CB_API_PORT는 정수여야 합니다.") from exc
        if not 1 <= parsed_port <= 65535:
            raise ManuscriptError("CB_API_PORT는 1~65535 범위여야 합니다.")

    bot_enabled = bool(_effective(values, "TELEGRAM_BOT_TOKEN").strip())
    return Runtime(
        layout=layout,
        dev=dev,
        values=values,
        project=project,
        wait_timeout=wait_timeout,
        bot_enabled=bot_enabled,
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


def _ensure_inject_token(path: Path) -> bool:
    """Fill a missing/blank inject token, preserving every non-empty value."""

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


def command_init(layout: Layout) -> int:
    created_env = _copy_once(layout.env_example, layout.env)
    dev_source = (
        layout.dev_env_example
        if layout.dev_env_example.is_file()
        else layout.env_example
    )
    created_dev_env = _copy_once(dev_source, layout.dev_env)
    token_created = _ensure_inject_token(layout.env)
    _ensure_inject_token(layout.dev_env)
    layout.data.mkdir(parents=True, exist_ok=True)
    layout.vault.mkdir(parents=True, exist_ok=True)

    print(f".env: {'생성' if created_env else '유지'}")
    print(f".env.dev: {'생성' if created_dev_env else '유지'}")
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
        "profile": "dev" if runtime.dev else "production",
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
    print(f"profile: {'dev' if runtime.dev else 'production'}")
    print(f"project: {runtime.project}")
    print(f"bot profile: {'enabled' if runtime.bot_enabled else 'disabled'}")
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
            "Claire 컨테이너 관리. 개발 overlay는 "
            "`cb-manuscript dev <command>`로 선택합니다."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("init", help=".env/.env.dev와 data/vault를 준비")
    subparsers.add_parser("doctor", help="설정과 Docker Compose를 점검")
    subparsers.add_parser("install", help="이미지 build, migrate, 기동, health 확인")
    update = subparsers.add_parser("update", help="ff-only 갱신 후 안전한 순서로 재기동")
    update.add_argument("--no-fetch", action="store_true", help="git pull을 생략")
    subparsers.add_parser("health", help="실행 중인 API의 liveness 확인")
    subparsers.add_parser("version", help="wrapper와 source 버전 표시")

    for name in sorted(PASSTHROUGH_COMMANDS):
        subparsers.add_parser(name, help=PASSTHROUGH_HELP[name])

    remote = subparsers.add_parser("remote", help="deploy.sh 원격 연결")
    remote_subparsers = remote.add_subparsers(dest="remote_action", required=True)
    remote_subparsers.add_parser("install")
    remote_subparsers.add_parser("update")
    return parser


def _split_dev_prefix(argv: Sequence[str]) -> tuple[bool, list[str]]:
    args = list(argv)
    if args[:1] == ["dev"]:
        return True, args[1:]
    return False, args


def main(argv: Sequence[str] | None = None, *, root: Path | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    dev, args = _split_dev_prefix(raw)
    layout = Layout((root or Path(__file__).resolve().parents[1]).resolve())

    try:
        if args and args[0] in PASSTHROUGH_COMMANDS:
            if args[0] != "app" and args[1:] == ["--help"]:
                build_parser().parse_args([args[0], "--help"])
                return 0
            runtime = load_runtime(layout, dev=dev)
            if args[0] in LOCKED_PASSTHROUGH_COMMANDS:
                with InstanceLock(runtime):
                    return dispatch_passthrough(runtime, args[0], args[1:])
            return dispatch_passthrough(runtime, args[0], args[1:])

        if args[:1] == ["remote"] and len(args) >= 2:
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

        runtime = load_runtime(layout, dev=dev)
        if parsed.command == "doctor":
            return command_doctor(runtime)
        if parsed.command == "install":
            return command_install(runtime)
        if parsed.command == "update":
            return command_update(runtime, no_fetch=parsed.no_fetch)
        if parsed.command == "health":
            return command_health(runtime)
        raise ManuscriptError(f"알 수 없는 명령: {parsed.command}")
    except ManuscriptError as exc:
        print(f"cb-manuscript: {exc}", file=sys.stderr)
        return exc.exit_code
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
