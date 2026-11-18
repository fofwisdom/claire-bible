"""``plugins/project.lock``: id, plugin revision, and digest of each project plugin (INGEST_ADAPTER_PLUGIN_DESIGN.md §6.2)."""

from __future__ import annotations

import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path

LOCK_FILE = "project.lock"


class LockError(ValueError):
    pass


@dataclass(frozen=True)
class LockItem:
    id: str
    revision: int
    digest: str


def parse_lock(text: str) -> dict[str, LockItem]:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise LockError(f"{LOCK_FILE}: {exc}") from exc
    unknown = set(data) - {"plugin"}
    if unknown:
        raise LockError(f"{LOCK_FILE}: unknown key {sorted(unknown)[0]!r}")
    items: dict[str, LockItem] = {}
    for raw in data.get("plugin", []):
        if not isinstance(raw, dict) or set(raw) != {"id", "revision", "digest"}:
            raise LockError(f"{LOCK_FILE}: each [[plugin]] has exactly id, revision, and digest")
        plugin_id, revision, digest = raw["id"], raw["revision"], raw["digest"]
        if not isinstance(plugin_id, str) or not isinstance(digest, str) or isinstance(revision, bool) or not isinstance(revision, int):
            raise LockError(f"{LOCK_FILE}: wrong value type in [[plugin]]")
        if plugin_id in items:
            raise LockError(f"{LOCK_FILE}: duplicate id {plugin_id!r}")
        items[plugin_id] = LockItem(plugin_id, revision, digest)
    return items


def read_lock(project_folder: Path) -> dict[str, LockItem]:
    """The lock of the project plugin folder. An absent file is an empty lock."""
    path = project_folder / LOCK_FILE
    if not path.is_file():
        return {}
    return parse_lock(path.read_text(encoding="utf-8"))


def render_lock(items: dict[str, LockItem]) -> str:
    lines = [
        "# Project plugins: id, plugin revision, and digest. Written by `claire plugin lock`.",
        "# docs/design/INGEST_ADAPTER_PLUGIN_DESIGN.md section 6.2",
    ]
    for plugin_id in sorted(items):
        item = items[plugin_id]
        lines += ["", "[[plugin]]", f'id = "{item.id}"', f"revision = {item.revision}", f'digest = "{item.digest}"']
    return "\n".join(lines) + "\n"


def head_lock(project_folder: Path) -> dict[str, LockItem] | None:
    """The lock of ``HEAD`` in the Git checkout of ``project_folder``. ``None`` outside a checkout or when ``HEAD`` has no lock."""
    try:
        top = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"], cwd=project_folder, capture_output=True, text=True, timeout=10, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if top.returncode != 0:
        return None
    rel = (project_folder / LOCK_FILE).resolve().relative_to(Path(top.stdout.strip()).resolve()).as_posix()
    shown = subprocess.run(["git", "show", f"HEAD:{rel}"], cwd=project_folder, capture_output=True, text=True, timeout=10, check=False)
    if shown.returncode != 0:
        return None
    return parse_lock(shown.stdout)
