"""``./cb-manuscript dev toolbox``: run tests and checks in the contributor toolbox container (docs/design/CONTRIBUTOR_DEVELOPMENT_ENVIRONMENT_DESIGN.md Sections 7 and 14 DE1).

Only the Python standard library (3.11+ compatible) is used. Does not read ``.env`` or ``.env.dev``.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Sequence

TOOLBOX_IMAGE = "claire-toolbox:dev"
TOOLBOX_DOCKERFILE = Path("ops") / "dev" / "toolbox.Dockerfile"
TOOLBOX_MEMORY = "4g"


class ToolboxError(RuntimeError):
    """Expected error from the toolbox command runner."""


def _usage() -> str:
    return (
        "Usage: ./cb-manuscript dev toolbox [--build] [--network <name>] <command ...>\n\n"
        "Options:\n"
        "  --build           Rebuild the claire-toolbox:dev image before running\n"
        "  --network <name>  Attach the container to a Docker network\n"
        "  -h, --help        Show this help text\n"
    )


def parse_toolbox_args(argv: Sequence[str]) -> tuple[bool, str | None, list[str], bool]:
    """Parse leading toolbox options without consuming flags that belong to the inner command."""
    build = False
    network: str | None = None
    args = list(argv)
    idx = 0
    while idx < len(args):
        token = args[idx]
        if token == "--":
            idx += 1
            break
        if token in ("-h", "--help"):
            return build, network, [], True
        if token == "--build":
            build = True
            idx += 1
            continue
        if token == "--network":
            if idx + 1 >= len(args) or not args[idx + 1]:
                raise ToolboxError("--network requires a network name.")
            network = args[idx + 1]
            idx += 2
            continue
        if token.startswith("--network="):
            value = token.split("=", 1)[1]
            if not value:
                raise ToolboxError("--network requires a network name.")
            network = value
            idx += 1
            continue
        if token.startswith("-"):
            raise ToolboxError(f"Unknown toolbox option: {token}")
        break
    return build, network, args[idx:], False


def _has_deno_config(deno_args: Sequence[str]) -> bool:
    for arg in deno_args:
        if arg == "--":
            return False
        if arg in ("-c", "--config") or arg.startswith("--config="):
            return True
    return False


def resolve_workdir(command: Sequence[str]) -> str:
    """Set workdir to /src/sidecar/fedify when the command invokes deno without --config."""
    if command and Path(command[0]).name == "deno" and not _has_deno_config(command[1:]):
        return "/src/sidecar/fedify"
    return "/src"


def _host_user_flag() -> str:
    uid = os.getuid() if hasattr(os, "getuid") else 1000
    gid = os.getgid() if hasattr(os, "getgid") else 1000
    return f"{uid}:{gid}"


def _is_tty() -> bool:
    return bool(sys.stdin.isatty() and sys.stdout.isatty())


def _image_exists(image: str) -> bool:
    done = subprocess.run(["docker", "image", "inspect", image], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    return done.returncode == 0


def build_toolbox_image(root: Path, *, image: str = TOOLBOX_IMAGE) -> None:
    """Build the toolbox image from ops/dev/toolbox.Dockerfile with the repository root as context."""
    dockerfile = root / TOOLBOX_DOCKERFILE
    if not dockerfile.is_file():
        raise ToolboxError(f"Toolbox Dockerfile not found: {dockerfile}")
    done = subprocess.run(["docker", "build", "-t", image, "-f", str(dockerfile), str(root)], check=False)
    if done.returncode != 0:
        raise ToolboxError(f"docker build failed with exit {done.returncode}.")


def build_docker_run_argv(
    root: Path,
    command: Sequence[str],
    *,
    network: str | None = None,
    image: str = TOOLBOX_IMAGE,
    interactive_tty: bool | None = None,
    user: str | None = None,
) -> list[str]:
    """Construct the docker run argv list for the toolbox container."""
    workdir = resolve_workdir(command)
    use_tty = _is_tty() if interactive_tty is None else interactive_tty
    argv = ["docker", "run", "--rm"]
    if use_tty:
        argv.append("-it")
    argv.extend([
        "--user", user or _host_user_flag(),
        "--memory", TOOLBOX_MEMORY,
        "--memory-swap", TOOLBOX_MEMORY,
        "-v", f"{root}:/src",
        "-w", workdir,
        "-e", "TMPDIR=/tmp",
        "-e", "HOME=/tmp",
        "-e", "CLAIRE_IGNORE_DOTENV=1",
        "-e", "UV_PROJECT_ENVIRONMENT=/opt/claire-venv",
        "-e", "VIRTUAL_ENV=/opt/claire-venv",
        "-e", "CLAIRE_E2E_NODE_MODULES=/opt/claire-e2e/node_modules",
        "-e", "CLAIRE_E2E_PYTHON=/opt/claire-venv/bin/python",
        "-e", "PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright",
        "-e", "DENO_DIR=/opt/deno-cache",
    ])
    if network:
        argv.extend(["--network", network])
    argv.append(image)
    argv.extend(command)
    return argv


def run_toolbox(root: Path, argv: Sequence[str]) -> int:
    """Run a command inside the claire-toolbox:dev container without reading .env or .env.dev."""
    build, network, command, show_help = parse_toolbox_args(argv)
    if show_help:
        print(_usage(), end="")
        return 0
    if not command:
        print(_usage(), end="", file=sys.stderr)
        raise ToolboxError("A command is required after toolbox.")
    if shutil.which("docker") is None:
        raise ToolboxError("docker is not installed or not in PATH.")
    if build or not _image_exists(TOOLBOX_IMAGE):
        build_toolbox_image(root, image=TOOLBOX_IMAGE)
    run_argv = build_docker_run_argv(root, command, network=network, image=TOOLBOX_IMAGE)
    return subprocess.run(run_argv, check=False).returncode


def main(root: Path, argv: Sequence[str]) -> int:
    """Entry point called by cb-manuscript dev toolbox."""
    try:
        return run_toolbox(root, argv)
    except ToolboxError as exc:
        print(f"toolbox: {exc}", file=sys.stderr)
        return 2
