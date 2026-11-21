"""Discovery and states of plugins (docs/implementation/INGESTION.md §2.2).

Discovery reads manifests and file hashes only. It never imports plugin code (PL-D8, S1).
"""

from __future__ import annotations

import importlib.metadata
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..ingest.hosts import patterns_overlap
from ..store.plugins import PluginRecords
from . import (
    ORIGIN_PLUGIN_FOLDER,
    ORIGIN_PROJECT,
    PLUGIN_API_LEVELS,
    PLUGIN_TYPES,
    PROJECT_PUBLISHER,
    STATE_CHANGED,
    STATE_DISABLED,
    STATE_ENABLED,
    STATE_INCOMPATIBLE,
    STATE_INVALID,
    STATE_MISSING_REQUIREMENT,
    STATE_NEW,
    STATE_OVERLAP,
    STATE_REMOVED,
    STATE_UNLOCKED,
)
from .files import PluginFileError, folder_digest
from .importcheck import check_folder
from .lock import LockError, LockItem, read_lock
from .manifest import Manifest, ManifestError, publisher_of, read_manifest


@dataclass
class Discovered:
    """One folder found in a type folder."""

    folder_name: str
    origin: str
    path: Path
    plugin_type: str
    manifest: Manifest | None = None
    digest: str | None = None
    problem: str | None = None  # reason of the state ``invalid`` found by discovery
    lock_item: LockItem | None = None

    @property
    def id(self) -> str:
        return self.manifest.id if self.manifest else self.folder_name


@dataclass
class PluginStatus:
    """A plugin with its computed state."""

    id: str
    origin: str | None
    path: Path | None
    plugin_type: str | None
    manifest: Manifest | None
    digest: str | None
    state: str
    reason: str | None = None
    overlap_group: tuple[str, ...] = ()
    record: dict[str, Any] | None = None

    def to_dict(self, *, locale: str = "en") -> dict[str, Any]:
        m = self.manifest
        return {
            "id": self.id,
            "origin": self.origin,
            "path": str(self.path) if self.path else None,
            "type": self.plugin_type,
            "state": self.state,
            "reason": self.reason,
            "digest": self.digest,
            "revision": m.revision if m else None,
            "core_commit": m.core_commit if m else None,
            "api": m.api if m else None,
            "maturity": m.maturity if m else None,
            "license": m.license if m else None,
            "maintainers": list(m.maintainers) if m else [],
            "name": m.text_for(locale)["name"] if m else self.id,
            "description": m.text_for(locale)["description"] if m else None,
            "provides": [p.to_dict() for p in m.provides] if m else [],
            "permissions": m.permissions.to_dict() if m else None,
            "requires": m.requires.to_dict() if m else None,
            "overlap_group": list(self.overlap_group),
            "record": self.record,
        }


@dataclass
class Discovery:
    plugins: list[Discovered] = field(default_factory=list)
    unknown_folders: list[Path] = field(default_factory=list)
    lock_problem: str | None = None


def _scan_root(root: Path, origin: str, out: Discovery, *, check_owner: bool) -> None:
    if not root.is_dir():
        return
    from .files import is_read_only_mount

    read_only = origin == ORIGIN_PLUGIN_FOLDER and is_read_only_mount(root)
    for type_dir in sorted(root.iterdir()):
        if not type_dir.is_dir() or type_dir.name.startswith((".", "_")):
            continue
        if type_dir.name not in PLUGIN_TYPES:
            out.unknown_folders.append(type_dir)
            continue
        for folder in sorted(type_dir.iterdir()):
            if folder.name.startswith((".", "_")) or not (folder.is_dir() or folder.is_symlink()):
                continue
            item = Discovered(folder_name=folder.name, origin=origin, path=folder, plugin_type=type_dir.name)
            out.plugins.append(item)
            try:
                item.digest = folder_digest(folder, check_owner=check_owner, read_only_mount=read_only)
            except (PluginFileError, OSError) as exc:
                item.problem = f"files: {exc}"
                continue
            try:
                item.manifest = read_manifest(folder)
            except ManifestError as exc:
                item.problem = f"manifest: {exc}"
                continue
            m = item.manifest
            if m.id != folder.name:
                item.problem = f"the id {m.id!r} is not the folder name {folder.name!r}"
            elif m.type != type_dir.name:
                item.problem = f"the type {m.type!r} is not the type folder {type_dir.name!r}"


def discover(project_folder: Path, plugin_folder: Path) -> Discovery:
    """Read both folders. No import."""
    out = Discovery()
    try:
        lock = read_lock(project_folder)
    except LockError as exc:
        lock = {}
        out.lock_problem = str(exc)
    _scan_root(project_folder, ORIGIN_PROJECT, out, check_owner=False)
    _scan_root(plugin_folder, ORIGIN_PLUGIN_FOLDER, out, check_owner=True)

    project_ids = {p.id for p in out.plugins if p.origin == ORIGIN_PROJECT}
    for item in out.plugins:
        if item.problem or item.manifest is None:
            continue
        publisher = publisher_of(item.manifest.id)
        if item.origin == ORIGIN_PROJECT:
            if out.lock_problem:
                item.problem = f"lock: {out.lock_problem}"
            elif publisher != PROJECT_PUBLISHER:
                item.problem = f"the project plugin folder holds only the publisher {PROJECT_PUBLISHER!r}"
            elif item.manifest.id not in lock:
                item.problem = "the project plugin is not in project.lock"
            else:
                item.lock_item = lock[item.manifest.id]
        else:
            if publisher == PROJECT_PUBLISHER:
                item.problem = f"the publisher {PROJECT_PUBLISHER!r} is reserved for project plugins"
            elif item.manifest.id in project_ids:
                item.problem = "a project plugin has the same id"
        if item.problem is None:
            findings = check_folder(item.path, network_modules=item.manifest.permissions.network_modules, executables=item.manifest.permissions.executables)
            if findings:
                item.problem = "import check: " + "; ".join(f.text() for f in findings[:5])
    return out


def overlap_pairs(manifests: list[Manifest]) -> dict[str, set[str]]:
    """Direct overlaps between plugins of the same type (§6.4)."""
    pairs: dict[str, set[str]] = {m.id: set() for m in manifests}
    for i, a in enumerate(manifests):
        for b in manifests[i + 1 :]:
            if a.type != b.type:
                continue
            if _overlap(a, b):
                pairs[a.id].add(b.id)
                pairs[b.id].add(a.id)
    return pairs


def _overlap(a: Manifest, b: Manifest) -> bool:
    for pa in a.provides:
        for pb in b.provides:
            if pa.kind == pb.kind and pa.name == pb.name:
                return True
            if any(patterns_overlap(ha, hb) for ha in pa.hosts for hb in pb.hosts):
                return True
    return False


def overlap_hosts(a: Manifest, b: Manifest) -> list[str]:
    shared: set[str] = set()
    for pa in a.provides:
        for pb in b.provides:
            if pa.kind == pb.kind and pa.name == pb.name:
                shared.add(f"name:{pa.name}")
            for ha in pa.hosts:
                for hb in pb.hosts:
                    if patterns_overlap(ha, hb):
                        shared.add(ha if len(ha) >= len(hb) else hb)
    return sorted(shared)


def overlap_groups(pairs: dict[str, set[str]]) -> dict[str, tuple[str, ...]]:
    """Connected groups (§6.4): plugins that overlap directly or through other plugins. A plugin without overlap has no group."""
    groups: dict[str, tuple[str, ...]] = {}
    seen: set[str] = set()
    for start in sorted(pairs):
        if start in seen or not pairs[start]:
            continue
        stack, group = [start], set()
        while stack:
            node = stack.pop()
            if node in group:
                continue
            group.add(node)
            stack.extend(pairs[node] - group)
        seen |= group
        members = tuple(sorted(group))
        for member in members:
            groups[member] = members
    return groups


def _missing_requirement(m: Manifest) -> str | None:
    for dist in m.requires.distributions:
        try:
            importlib.metadata.distribution(dist)
        except importlib.metadata.PackageNotFoundError:
            return f"the distribution {dist!r} is not installed"
    for exe in m.requires.executables:
        if shutil.which(exe) is None:
            return f"the executable {exe!r} is not found"
    return None


def compute_states(discovery: Discovery, records: PluginRecords | None) -> list[PluginStatus]:
    """The state of each plugin (§8.1), without ``failed``: only a load attempt finds it."""
    stored = records.records if records else {}
    statuses: list[PluginStatus] = []
    ids_seen: set[str] = set()
    for item in discovery.plugins:
        record = stored.get(item.id)
        status = PluginStatus(
            id=item.id,
            origin=item.origin,
            path=item.path,
            plugin_type=item.plugin_type,
            manifest=item.manifest,
            digest=item.digest,
            state=STATE_INVALID,
            record=record.to_dict() if record else None,
        )
        statuses.append(status)
        if item.id in ids_seen:
            status.reason = "another folder has the same id"
            continue
        ids_seen.add(item.id)
        m = item.manifest
        if item.problem or m is None:
            status.reason = item.problem or "manifest is missing"
            continue
        if item.origin == ORIGIN_PROJECT and item.lock_item is not None and item.lock_item.digest != item.digest:
            status.state, status.reason = STATE_UNLOCKED, "the digest is not the digest in project.lock; run `claire plugin lock`"
            continue
        if m.api not in PLUGIN_API_LEVELS:
            status.state, status.reason = STATE_INCOMPATIBLE, f"plugin API level {m.api} is not supported (supported: {list(PLUGIN_API_LEVELS)})"
            continue
        missing = _missing_requirement(m)
        if missing:
            status.state, status.reason = STATE_MISSING_REQUIREMENT, missing
            continue
        if record is None:
            status.state = STATE_NEW
            continue
        if not record.enabled:
            status.state = STATE_DISABLED
            continue
        if item.origin == ORIGIN_PLUGIN_FOLDER and record.approved_digest != item.digest:
            status.state, status.reason = STATE_CHANGED, "the files changed after the approval; approve the new digest"
            continue
        status.state = STATE_ENABLED

    valid = [s.manifest for s in statuses if s.manifest is not None and s.state != STATE_INVALID]
    groups = overlap_groups(overlap_pairs(valid))
    for status in statuses:
        if status.state == STATE_INVALID:
            continue
        status.overlap_group = groups.get(status.id, ())
    for status in statuses:
        if status.state != STATE_ENABLED or not status.overlap_group:
            continue
        others = [s for s in statuses if s.id in status.overlap_group and s.id != status.id and s.state in (STATE_ENABLED, STATE_OVERLAP)]
        if others:
            status.state = STATE_OVERLAP
            status.reason = "another plugin of the overlap group is also on: " + ", ".join(o.id for o in others)
            for other in others:
                other.state = STATE_OVERLAP
                other.reason = "another plugin of the overlap group is also on: " + status.id

    for plugin_id, record in sorted(stored.items()):
        if plugin_id not in ids_seen:
            statuses.append(
                PluginStatus(id=plugin_id, origin=None, path=None, plugin_type=None, manifest=None, digest=None, state=STATE_REMOVED, reason="the folder is absent", record=record.to_dict())
            )
    return statuses
