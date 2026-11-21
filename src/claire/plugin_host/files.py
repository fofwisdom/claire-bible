"""Files of a plugin folder: digest (docs/implementation/INGESTION.md §2.2) and safety checks.

The digest is the SHA-256 of the sorted lines ``<sha256 of the content>  <path relative to the folder of the plugin>``. A person can make the same lines with ``sha256sum``.
"""

from __future__ import annotations

import hashlib
import os
import stat
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

SKIP_DIRS = frozenset({"__pycache__"})
SKIP_SUFFIXES = (".pyc",)
_EXEC_BITS = stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH


class PluginFileError(ValueError):
    """A file of the plugin cannot be accepted (symbolic link, not a regular file, unsafe mode or owner)."""


@dataclass(frozen=True)
class FileListing:
    files: tuple[tuple[str, Path], ...]  # (relative path with "/", absolute path), sorted by relative path
    signature: tuple[tuple[str, int, int, int, int], ...]  # (relative path, size, mtime_ns, ctime_ns, inode)


def _check_safe(path: Path, st: os.stat_result, *, check_owner: bool) -> None:
    """No file that other users can write. World-writable is refused; group-writable is refused when the group is not the group of the process (a user private group is safe)."""
    if st.st_mode & stat.S_IWOTH:
        raise PluginFileError(f"{path.name}: other users can write it")
    if st.st_mode & stat.S_IWGRP and hasattr(os, "getgid") and st.st_gid not in (os.getgid(), 0):
        raise PluginFileError(f"{path.name}: users of another group can write it")
    if check_owner and hasattr(os, "getuid") and st.st_uid not in (os.getuid(), 0):
        raise PluginFileError(f"{path.name}: it does not belong to the process user or to root")


def list_files(folder: Path, *, check_owner: bool = False, read_only_mount: bool = False) -> FileListing:
    """All regular files of the folder of a plugin, sorted. Raise ``PluginFileError``.

    ``check_owner`` applies the owner rule (a file of the plugin folder belongs to the process user or to root). ``read_only_mount`` lifts the mode and owner rules: a read-only mount cannot be changed by the process.
    """
    root = folder.resolve()
    entries: list[tuple[str, Path]] = []
    signature: list[tuple[str, int, int, int, int]] = []
    root_st = os.lstat(folder)
    if stat.S_ISLNK(root_st.st_mode):
        raise PluginFileError("the folder of the plugin is a symbolic link")
    if not read_only_mount:
        _check_safe(folder, root_st, check_owner=check_owner)
    for current, dirs, files in os.walk(folder, followlinks=False):
        current_path = Path(current)
        kept_dirs = []
        for name in sorted(dirs):
            if name in SKIP_DIRS:
                continue
            path = current_path / name
            st = os.lstat(path)
            if stat.S_ISLNK(st.st_mode):
                raise PluginFileError(f"{path.relative_to(folder).as_posix()}: symbolic link")
            if not read_only_mount:
                _check_safe(path, st, check_owner=check_owner)
            kept_dirs.append(name)
        dirs[:] = kept_dirs
        for name in sorted(files):
            if name.endswith(SKIP_SUFFIXES):
                continue
            path = current_path / name
            st = os.lstat(path)
            rel = path.relative_to(folder).as_posix()
            if stat.S_ISLNK(st.st_mode):
                raise PluginFileError(f"{rel}: symbolic link")
            if not stat.S_ISREG(st.st_mode):
                raise PluginFileError(f"{rel}: not a regular file")
            if not path.resolve().is_relative_to(root):
                raise PluginFileError(f"{rel}: outside the folder of the plugin")
            if not read_only_mount:
                _check_safe(path, st, check_owner=check_owner)
            entries.append((rel, path))
            signature.append((rel, st.st_size, st.st_mtime_ns, st.st_ctime_ns, st.st_ino))
    entries.sort(key=lambda item: item[0])
    signature.sort(key=lambda item: item[0])
    return FileListing(files=tuple(entries), signature=tuple(signature))


def file_digests(listing: FileListing) -> dict[str, str]:
    """Sorted ``{relative_posix_path: sha256_hexdigest}`` for every regular file in ``listing``."""
    return {rel: hashlib.sha256(path.read_bytes()).hexdigest() for rel, path in listing.files}


def digest_lines(listing: FileListing, digests: dict[str, str] | None = None) -> list[str]:
    hashes = file_digests(listing) if digests is None else digests
    return [f"{hashes[rel]}  {rel}" for rel, _path in listing.files]


def compute_digest(listing: FileListing, digests: dict[str, str] | None = None) -> str:
    text = "\n".join(digest_lines(listing, digests=digests))
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


_CACHE: dict[str, tuple[tuple[tuple[str, int, int, int, int], ...], str, dict[str, str]]] = {}


def folder_digest_detail(folder: Path, *, check_owner: bool = False, read_only_mount: bool = False) -> tuple[str, dict[str, str]]:
    """The digest of the folder of a plugin and per-file SHA-256 hex digests. Raise ``PluginFileError``.

    A cache keyed on size, modification time, change time, and inode of each file avoids a new hash when no file changed. The change time changes at each write and a process cannot set it back. A file changed less than one second ago is always hashed again.
    """
    import time

    listing = list_files(folder, check_owner=check_owner, read_only_mount=read_only_mount)
    key = str(folder.resolve())
    cached = _CACHE.get(key)
    # A write in the same coarse clock tick can keep size and times; a signature less than one second old is not trusted (as store.db.db_file_signature).
    newest = max((max(sig[2], sig[3]) for sig in listing.signature), default=0)
    fresh = time.time_ns() - newest < 1_000_000_000
    if cached is not None and cached[0] == listing.signature and not fresh:
        return cached[1], dict(cached[2])
    hashes = file_digests(listing)
    digest = compute_digest(listing, digests=hashes)
    _CACHE[key] = (listing.signature, digest, hashes)
    return digest, dict(hashes)


def folder_digest(folder: Path, *, check_owner: bool = False, read_only_mount: bool = False) -> str:
    """The digest of the folder of a plugin. Raise ``PluginFileError``."""
    digest, _hashes = folder_digest_detail(folder, check_owner=check_owner, read_only_mount=read_only_mount)
    return digest


def check_no_executable_files(folder: Path, relpaths: Iterable[str]) -> None:
    """Raise ``PluginFileError`` if any declared file is missing, a symlink, non-regular, escapes ``folder``, or has any execute mode bit."""
    try:
        root_st = os.lstat(folder)
    except OSError as exc:
        raise PluginFileError(f"{folder}: {exc}") from exc
    if stat.S_ISLNK(root_st.st_mode):
        raise PluginFileError("the folder of the plugin is a symbolic link")
    if not stat.S_ISDIR(root_st.st_mode):
        raise PluginFileError("the folder of the plugin is not a directory")
    root = folder.resolve()
    for rel in relpaths:
        rel_path = Path(rel)
        if rel_path.is_absolute():
            raise PluginFileError(f"{rel}: outside the folder of the plugin")
        cur = folder
        for part in rel_path.parts[:-1]:
            if part == ".":
                continue
            if part == "..":
                raise PluginFileError(f"{rel}: outside the folder of the plugin")
            cur = cur / part
            try:
                dir_st = os.lstat(cur)
            except OSError as exc:
                raise PluginFileError(f"{rel}: {exc}") from exc
            if stat.S_ISLNK(dir_st.st_mode):
                raise PluginFileError(f"{cur.relative_to(folder).as_posix()}: symbolic link")
            if not stat.S_ISDIR(dir_st.st_mode):
                raise PluginFileError(f"{cur.relative_to(folder).as_posix()}: not a directory")
        path = folder / rel
        try:
            st = os.lstat(path)
        except OSError as exc:
            raise PluginFileError(f"{rel}: {exc}") from exc
        if stat.S_ISLNK(st.st_mode):
            raise PluginFileError(f"{rel}: symbolic link")
        if not stat.S_ISREG(st.st_mode):
            raise PluginFileError(f"{rel}: not a regular file")
        if not path.resolve().is_relative_to(root):
            raise PluginFileError(f"{rel}: outside the folder of the plugin")
        if st.st_mode & _EXEC_BITS:
            raise PluginFileError(f"{rel}: executable mode bits are not allowed in agent_client")


def is_read_only_mount(path: Path) -> bool:
    """True when the file system of ``path`` is mounted read-only (``ST_RDONLY``)."""
    try:
        return bool(os.statvfs(path).f_flag & os.ST_RDONLY)
    except (OSError, AttributeError):
        return False
