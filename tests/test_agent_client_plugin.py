"""Tests for agent_client plugin manifest, checksums, security locks, discovery, runtime, service, and CLI."""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import stat
import sys
import tarfile
import tomllib
from pathlib import Path

import pytest

from claire import plugin_sdk
from claire.config import get_settings
from claire.plugin_host import ORIGIN_PROJECT, PLUGIN_API_LEVELS, PLUGIN_TYPES, STATE_CHANGED, STATE_DISABLED, STATE_ENABLED, STATE_INVALID, STATE_NEW, STATE_OVERLAP, STATE_UNLOCKED, TYPE_AGENT_CLIENT, TYPE_INGEST_ADAPTER
from claire.plugin_host import manifest as manifest_mod
from claire.plugin_host import runtime as rt
from claire.plugin_host.discovery import compute_states, discover, overlap_hosts, overlap_pairs
from claire.plugin_host.files import PluginFileError, check_no_executable_files, folder_digest, folder_digest_detail
from claire.plugin_host.importcheck import check_folder, check_source
from claire.plugin_host.lock import LockItem, parse_lock, render_lock
from claire.plugin_host.manifest import ManifestError, parse_manifest
from claire.store import plugins as store

COMMIT = "b8694402b6ee0ce08f5af1a6af2853bb911575d5"

DEFAULT_BUNDLE_FILES: dict[str, str] = {
    "mcp_config.json.tmpl": '{"mcpServers":{"claire":{"url":"{{MCP_URL}}"}}}',
    "rules/AGENTS.md": "# Extractor Rules\nFollow the extraction schema.\n",
    "agents/claire-extractor.md": "# Claire Extractor Subagent\nUse MCP tools only.\n",
    "skills/extract/SKILL.md": "# Skill\nExtract source documents.\n",
}

INGEST_ADAPTER_SRC = """from claire.plugin_sdk import BaseFetcher, Document

class Fetcher(BaseFetcher):
    @classmethod
    def can_handle(cls, url):
        return "/item/" in url

    @classmethod
    def fetch(cls, url, **kwargs):
        return Document(url=url, title="plugin:{name}", raw_text="text", source_type="{pid}")

def register(ctx):
    ctx.fetcher(Fetcher, name="{name}", priority={priority}, hosts={hosts!r})
"""


def _harden_tree(root: Path) -> None:
    """Set mode 0o755 on directories and mode 0o644 on regular files without following symbolic links."""
    st = os.lstat(root)
    if stat.S_ISLNK(st.st_mode):
        return
    if stat.S_ISDIR(st.st_mode):
        os.chmod(root, 0o755)
        for entry in root.iterdir():
            _harden_tree(entry)
    elif stat.S_ISREG(st.st_mode):
        os.chmod(root, 0o644)


def _agent_client_manifest_text(pid: str = "claire.antigravity", *, client_id: str = "antigravity", required_scopes: list[str] | None = None, files: list[str] | None = None, revision: int = 1, api: int = 1, extra: str = "") -> str:
    scopes = required_scopes if required_scopes is not None else ["readonly", "write"]
    file_list = files if files is not None else list(DEFAULT_BUNDLE_FILES)
    return f"""manifest = 1
id = {json.dumps(pid)}
type = "agent_client"
revision = {revision}
core_commit = "{COMMIT}"
api = {api}
client_id = {json.dumps(client_id)}
required_scopes = {json.dumps(scopes)}
files = {json.dumps(file_list)}
license = "MIT"
maturity = "stable"
{extra}
[text.en]
name = "Antigravity"
description = "Reference agent_client bundle."

[text.ko]
name = "안티그래비티"
description = "참조 에이전트 클라이언트 번들."
"""


def _ingest_manifest_text(pid: str = "claire.alpha", *, name: str = "alpha", hosts: list[str] | None = None, priority: int = 100, revision: int = 1, api: int = 1, extra: str = "") -> str:
    host_list = hosts if hosts is not None else [f"{name}.test"]
    return f"""manifest = 1
id = {json.dumps(pid)}
type = "ingest_adapter"
revision = {revision}
core_commit = "{COMMIT}"
api = {api}
entry = "adapter:register"
license = "MIT"
maturity = "stable"
{extra}
[text.en]
name = {json.dumps(name)}
description = "Test ingest adapter."

[text.ko]
name = {json.dumps(name)}
description = "시험 인제스트 어댑터."

[[provides]]
kind = "fetcher"
name = {json.dumps(name)}
priority = {priority}
hosts = {json.dumps(host_list)}
source_types = [{json.dumps(pid)}]

[permissions]
network = {json.dumps(host_list)}
"""


def make_agent_client(root: Path, pid: str = "claire.antigravity", *, client_id: str = "antigravity", required_scopes: list[str] | None = None, bundle_files: dict[str, str] | None = None, manifest_files: list[str] | None = None, revision: int = 1, api: int = 1, extra: str = "", extra_files: dict[str, str] | None = None) -> Path:
    """Create an agent_client plugin folder with hardened permissions."""
    folder = root / TYPE_AGENT_CLIENT / pid
    folder.mkdir(parents=True, exist_ok=True)
    written_files = dict(DEFAULT_BUNDLE_FILES if bundle_files is None else bundle_files)
    declared_files = list(written_files) if manifest_files is None else manifest_files
    (folder / "plugin.toml").write_text(_agent_client_manifest_text(pid, client_id=client_id, required_scopes=required_scopes, files=declared_files, revision=revision, api=api, extra=extra), encoding="utf-8")
    for rel, content in written_files.items():
        target = folder / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    for rel, content in (extra_files or {}).items():
        target = folder / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    _harden_tree(folder)
    return folder


def make_ingest_adapter(root: Path, pid: str = "claire.alpha", *, name: str | None = None, hosts: list[str] | None = None, priority: int = 100, revision: int = 1) -> Path:
    """Create an ingest_adapter plugin folder with hardened permissions."""
    adapter_name = name or pid.split(".", 1)[1]
    host_list = hosts or [f"{adapter_name}.test"]
    folder = root / TYPE_INGEST_ADAPTER / pid
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "plugin.toml").write_text(_ingest_manifest_text(pid, name=adapter_name, hosts=host_list, priority=priority, revision=revision), encoding="utf-8")
    (folder / "__init__.py").write_text("", encoding="utf-8")
    (folder / "adapter.py").write_text(INGEST_ADAPTER_SRC.format(name=adapter_name, pid=pid, priority=priority, hosts=tuple(host_list)), encoding="utf-8")
    _harden_tree(folder)
    return folder


def lock_project(project: Path) -> None:
    """Write project.lock for all valid folders under project/ingest_adapter and project/agent_client."""
    items: dict[str, LockItem] = {}
    for type_name in PLUGIN_TYPES:
        type_dir = project / type_name
        if not type_dir.is_dir():
            continue
        for folder in sorted(type_dir.iterdir()):
            if folder.name.startswith(("_", ".")) or not folder.is_dir():
                continue
            m = parse_manifest(tomllib.loads((folder / "plugin.toml").read_text(encoding="utf-8")))
            items[m.id] = LockItem(m.id, m.revision, folder_digest(folder))
    (project / "project.lock").write_text(render_lock(items), encoding="utf-8")


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Create a temporary server environment with project plugins, external plugins, and claire.db."""
    from claire.store import db as dbm

    project = tmp_path / "app" / "plugins"
    folder = tmp_path / "var" / "plugins"
    project.mkdir(parents=True)
    folder.mkdir(parents=True)
    db_file = tmp_path / "data" / "claire.db"
    db_file.parent.mkdir()
    conn = dbm.connect(db_file)
    dbm.init_db(conn)
    conn.close()
    monkeypatch.setenv("CLAIRE_DB_PATH", str(db_file))
    monkeypatch.setenv("CLAIRE_PLUGINS_DIR", str(folder))
    get_settings.cache_clear()
    monkeypatch.setattr(rt, "project_plugin_folder", lambda: project)
    monkeypatch.setattr(rt, "_RUNTIME", rt.PluginRuntime())
    yield type("Env", (), {"project": project, "folder": folder, "db": db_file})
    get_settings.cache_clear()


def test_plugin_sdk_and_types_keep_api_level_one():
    """Verify PLUGIN_API_LEVEL stays 1, PLUGIN_TYPES adds agent_client, and Manifest.to_dict branches by type."""
    assert plugin_sdk.PLUGIN_API_LEVEL == 1
    assert PLUGIN_API_LEVELS == (1,)
    assert PLUGIN_TYPES == ("ingest_adapter", "agent_client")
    assert manifest_mod.PLUGIN_TYPES == ("ingest_adapter", "agent_client")

    agent_manifest = parse_manifest(tomllib.loads(_agent_client_manifest_text()))
    assert agent_manifest.entry is None
    with pytest.raises(ValueError, match="has no Python entry"):
        _ = agent_manifest.entry_module
    with pytest.raises(ValueError, match="has no Python entry"):
        _ = agent_manifest.entry_function
    agent_dict = agent_manifest.to_dict()
    assert agent_dict["type"] == "agent_client"
    assert agent_dict["entry"] is None
    assert agent_dict["client_id"] == "antigravity"
    assert agent_dict["required_scopes"] == ["readonly", "write"]
    assert agent_dict["files"] == list(DEFAULT_BUNDLE_FILES)

    ingest_manifest = parse_manifest(tomllib.loads(_ingest_manifest_text()))
    assert ingest_manifest.entry_module == "adapter"
    assert ingest_manifest.entry_function == "register"
    ingest_dict = ingest_manifest.to_dict()
    assert "client_id" not in ingest_dict
    assert "required_scopes" not in ingest_dict
    assert "files" not in ingest_dict


def test_agent_client_manifest_valid_and_ingest_regression():
    """Verify valid agent_client manifest fields and ensure ingest_adapter rejects agent_client fields."""
    nested = ["mcp_config.json.tmpl", "rules/AGENTS.md", "agents/claire-extractor.md", "skills/claire-extract/SKILL.md"]
    parsed = parse_manifest(tomllib.loads(_agent_client_manifest_text(files=nested, extra="provides = []\n[permissions]\nprivate_network = false\n")))
    assert parsed.type == "agent_client"
    assert parsed.client_id == "antigravity"
    assert parsed.required_scopes == ("readonly", "write")
    assert parsed.files == tuple(nested)
    assert parsed.provides == ()
    assert parsed.permissions.private_network is False

    # Keep "clients" valid as a manifest client_id because username reservation belongs to AE1b.
    clients_manifest = parse_manifest(tomllib.loads(_agent_client_manifest_text(pid="example.clients", client_id="clients")))
    assert clients_manifest.client_id == "clients"

    ingest_data = tomllib.loads(_ingest_manifest_text())
    for forbidden_key, value in (("client_id", "antigravity"), ("required_scopes", ["readonly"]), ("files", ["rules/AGENTS.md"])):
        with pytest.raises(ManifestError, match="unknown key"):
            parse_manifest({**ingest_data, forbidden_key: value})


@pytest.mark.parametrize(
    ("mutator", "match_pattern"),
    [
        (lambda d: d.pop("type"), "missing key 'type'"),
        (lambda d: d.__setitem__("type", "unknown_type"), "known plugin type"),
        (lambda d: d.__setitem__("entry", "mod:fn"), "must not declare 'entry'"),
        (lambda d: d.__setitem__("provides", [{"kind": "fetcher", "name": "x", "priority": 100, "hosts": ["x.test"]}]), "must not declare provides"),
        (lambda d: d.pop("client_id"), "missing key 'client_id'"),
        (lambda d: d.__setitem__("client_id", ""), "not empty"),
        (lambda d: d.__setitem__("client_id", "   "), "not empty"),
        (lambda d: d.__setitem__("client_id", "a"), "client_id"),
        (lambda d: d.__setitem__("client_id", "a" * 33), "client_id"),
        (lambda d: d.__setitem__("client_id", "Antigravity"), "client_id"),
        (lambda d: d.__setitem__("client_id", "-agy"), "client_id"),
        (lambda d: d.__setitem__("client_id", "agy_1"), "client_id"),
        (lambda d: d.__setitem__("client_id", "antigravity\n"), "client_id"),
        (lambda d: d.__setitem__("id", "claire.agy\n"), "id:"),
        (lambda d: d.__setitem__("core_commit", COMMIT + "\n"), "core_commit:"),
        (lambda d: d.pop("required_scopes"), "missing key 'required_scopes'"),
        (lambda d: d.__setitem__("required_scopes", []), "must not be empty"),
        (lambda d: d.__setitem__("required_scopes", ["readonly", "readonly"]), "duplicate"),
        (lambda d: d.__setitem__("required_scopes", ["admin"]), "required_scopes"),
        (lambda d: d.__setitem__("required_scopes", ["READONLY"]), "required_scopes"),
        (lambda d: d.__setitem__("unexpected", 1), "unknown key"),
    ],
)
def test_agent_client_manifest_rejects_invalid_fields(mutator, match_pattern):
    """Verify ManifestError on invalid, missing, or malformed agent_client manifest fields."""
    data = tomllib.loads(_agent_client_manifest_text())
    mutator(data)
    with pytest.raises(ManifestError, match=match_pattern):
        parse_manifest(data)


@pytest.mark.parametrize(
    "bad_files",
    [
        [],
        ["rules/AGENTS.md", "rules/AGENTS.md"],
        [""],
        ["   "],
        ["/abs.md"],
        ["dir/"],
        ["."],
        [".."],
        ["../up.md"],
        ["a/../b.md"],
        ["./a.md"],
        ["a//b.md"],
        ["a\\b.md"],
        ["a\x00b.md"],
        ["rules/AGENTS.md\n"],
        ["rules/AGENTS\r.md"],
        ["rules/AGENTS\t.md"],
        ["rules/AGENTS\x7f.md"],
        ["plugin.toml"],
        ["__pycache__/x.txt"],
        ["sub/__pycache__/x.txt"],
        ["tests/helper.md"],
        ["script.py"],
        ["SCRIPT.PY"],
        ["mod.pyc"],
        ["mod.pyo"],
        ["lib.so"],
        ["lib.dll"],
        ["lib.dylib"],
        ["run.sh"],
        ["run.bash"],
        ["run.zsh"],
        ["run.ps1"],
        ["run.bat"],
        ["run.cmd"],
        ["bin.exe"],
    ],
)
def test_agent_client_manifest_rejects_bad_paths_and_suffixes(bad_files):
    """Verify ManifestError on invalid bundle file paths, control characters, and disallowed suffixes."""
    data = tomllib.loads(_agent_client_manifest_text())
    data["files"] = bad_files
    with pytest.raises(ManifestError):
        parse_manifest(data)


@pytest.mark.parametrize(
    "permissions",
    [
        {"unknown_perm": True},
        {"private_network": True},
        {"private_network": "false"},
        {"network": ["example.com"]},
        {"network_modules": ["httpx"]},
        {"executables": ["ffmpeg"]},
        {"providers": ["vlm"]},
    ],
)
def test_agent_client_manifest_rejects_disallowed_permissions(permissions):
    """Verify ManifestError on disallowed permissions in agent_client manifests."""
    data = tomllib.loads(_agent_client_manifest_text())
    data["permissions"] = permissions
    with pytest.raises(ManifestError):
        parse_manifest(data)


def test_agent_client_checksums_and_rapid_digest_refresh(tmp_path):
    """Verify files_sha256 covers declared files only, folder digest tracks all files, and rapid edits bypass the digest cache."""
    project = tmp_path / "app" / "plugins"
    external = tmp_path / "var" / "plugins"
    project.mkdir(parents=True)
    external.mkdir(parents=True)
    folder = make_agent_client(project, "claire.antigravity", extra_files={"README.md": "initial readme\n"})
    lock_project(project)

    disc = discover(project, external)
    item = next(p for p in disc.plugins if p.id == "claire.antigravity")
    assert item.problem is None
    expected_hashes = {rel: hashlib.sha256(content.encode("utf-8")).hexdigest() for rel, content in DEFAULT_BUNDLE_FILES.items()}
    assert item.files_sha256 == expected_hashes
    assert "plugin.toml" not in item.files_sha256
    assert "README.md" not in item.files_sha256

    statuses = compute_states(disc, None)
    status_dict = statuses[0].to_dict()
    assert status_dict["type"] == "agent_client"
    assert status_dict["client_id"] == "antigravity"
    assert status_dict["required_scopes"] == ["readonly", "write"]
    assert status_dict["files"] == list(DEFAULT_BUNDLE_FILES)
    assert status_dict["files_sha256"] == expected_hashes

    # Modify an undeclared file immediately and confirm the folder digest updates while files_sha256 stays unchanged.
    old_digest, old_all = folder_digest_detail(folder)
    (folder / "README.md").write_text("updated readme\n", encoding="utf-8")
    _harden_tree(folder)
    new_digest, new_all = folder_digest_detail(folder)
    assert new_digest != old_digest
    assert new_all["README.md"] != old_all["README.md"]

    disc_after = discover(project, external)
    statuses_after = compute_states(disc_after, None)
    assert statuses_after[0].state == STATE_UNLOCKED
    assert statuses_after[0].files_sha256 == expected_hashes


def test_agent_client_discovery_rejects_missing_and_directory_targets(env):
    """Verify discovery marks agent_client invalid when a declared bundle file is missing or is a directory."""
    make_agent_client(env.folder, "example.missing", manifest_files=[*DEFAULT_BUNDLE_FILES, "missing.md"])
    make_agent_client(env.folder, "example.dirtarget", manifest_files=["rules"])

    statuses = {s.id: s for s in compute_states(discover(env.project, env.folder), None)}
    assert statuses["example.missing"].state == STATE_INVALID
    assert "missing.md" in (statuses["example.missing"].reason or "")
    assert statuses["example.dirtarget"].state == STATE_INVALID
    assert "rules" in (statuses["example.dirtarget"].reason or "")


@pytest.mark.parametrize("exec_mode", [0o755, 0o654, 0o645])
def test_agent_client_rejects_executable_mode_bits(env, exec_mode):
    """Verify execute mode bits on declared bundle files cause STATE_INVALID."""
    folder = make_agent_client(env.folder, "example.exec")
    os.chmod(folder / "rules/AGENTS.md", exec_mode)
    statuses = {s.id: s for s in compute_states(discover(env.project, env.folder), None)}
    assert statuses["example.exec"].state == STATE_INVALID
    assert "executable mode bits" in (statuses["example.exec"].reason or "")


def test_agent_client_file_safety_locks(tmp_path):
    """Verify symlinks, world-writable permissions, FIFOs, and path escapes are rejected."""
    project = tmp_path / "app" / "plugins"
    external = tmp_path / "var" / "plugins"
    project.mkdir(parents=True)
    external.mkdir(parents=True)

    # World-writable file and directory.
    ww_file_folder = make_agent_client(external, "example.wwfile")
    os.chmod(ww_file_folder / "rules/AGENTS.md", 0o666)
    ww_dir_folder = make_agent_client(external, "example.wwdir")
    os.chmod(ww_dir_folder / "rules", 0o777)

    # Symlinked file, symlinked subdirectory, symlinked plugin root, and FIFO non-regular file.
    sym_file_folder = make_agent_client(external, "example.symfile")
    (sym_file_folder / "rules/AGENTS.md").unlink()
    (sym_file_folder / "rules/AGENTS.md").symlink_to(sym_file_folder / "mcp_config.json.tmpl")

    sym_dir_folder = make_agent_client(external, "example.symdir")
    (sym_dir_folder / "rules/AGENTS.md").unlink()
    (sym_dir_folder / "rules").rmdir()
    (sym_dir_folder / "rules").symlink_to(sym_dir_folder / "agents")

    real_root = make_agent_client(tmp_path / "outside", "example.real")
    (external / TYPE_AGENT_CLIENT / "example.symroot").symlink_to(real_root)

    fifo_folder = make_agent_client(external, "example.fifo")
    os.mkfifo(fifo_folder / "pipe")

    # Type-folder mismatch and publisher origin mismatch.
    make_agent_client(external, "claire.reserved")
    wrong_type = make_agent_client(external, "example.wrongtype")
    wrong_type_target = external / TYPE_INGEST_ADAPTER / "example.wrongtype"
    wrong_type_target.parent.mkdir(parents=True, exist_ok=True)
    wrong_type.rename(wrong_type_target)

    statuses = {s.id: s for s in compute_states(discover(project, external), None)}
    assert statuses["example.wwfile"].state == STATE_INVALID and "other users can write it" in (statuses["example.wwfile"].reason or "")
    assert statuses["example.wwdir"].state == STATE_INVALID and "other users can write it" in (statuses["example.wwdir"].reason or "")
    assert statuses["example.symfile"].state == STATE_INVALID and "symbolic link" in (statuses["example.symfile"].reason or "")
    assert statuses["example.symdir"].state == STATE_INVALID and "symbolic link" in (statuses["example.symdir"].reason or "")
    assert statuses["example.symroot"].state == STATE_INVALID and "symbolic link" in (statuses["example.symroot"].reason or "")
    assert statuses["example.fifo"].state == STATE_INVALID and "not a regular file" in (statuses["example.fifo"].reason or "")
    assert statuses["claire.reserved"].state == STATE_INVALID and "reserved" in (statuses["claire.reserved"].reason or "")
    assert statuses["example.wrongtype"].state == STATE_INVALID and "type folder" in (statuses["example.wrongtype"].reason or "")

    # Standalone check_no_executable_files rejects internal symlinked parent dirs, non-dir parents, and path escapes.
    standalone = tmp_path / "standalone"
    (standalone / "real_sub").mkdir(parents=True)
    (standalone / "real_sub" / "a.md").write_text("ok", encoding="utf-8")
    (standalone / "not_a_dir").write_text("file", encoding="utf-8")
    _harden_tree(standalone)
    (standalone / "sub").symlink_to(standalone / "real_sub")

    with pytest.raises(PluginFileError, match="symbolic link"):
        check_no_executable_files(standalone, ["sub/a.md"])
    with pytest.raises(PluginFileError, match="not a directory"):
        check_no_executable_files(standalone, ["not_a_dir/a.md"])
    with pytest.raises(PluginFileError, match="outside the folder"):
        check_no_executable_files(standalone, ["../outside.txt"])
    with pytest.raises(PluginFileError, match="outside the folder"):
        check_no_executable_files(standalone, ["/etc/passwd"])


@pytest.mark.parametrize(
    "source",
    [
        "import socket\n",
        "import ssl\n",
        "import http\n",
        "import http.client\n",
        "from http import client\n",
        "from http.client import HTTPConnection\n",
        "from http.server import HTTPServer\n",
        "import urllib\n",
        "from urllib import request\n",
        "from urllib.request import urlopen\n",
        "import httpx\n",
        "import requests\n",
        "import websockets\n",
        "import subprocess\n",
        "from subprocess import run\n",
        "import pty\n",
        "import multiprocessing\n",
        "import asyncio.subprocess\n",
        "from asyncio import subprocess\n",
        "from asyncio import create_subprocess_exec\n",
        "import asyncio\nasync def f():\n    await asyncio.create_subprocess_shell('x')\n",
        "import os\nos.system('x')\n",
        "import os\nos.popen('x')\n",
        "import os\nos.spawnvp(os.P_WAIT, 'x', ['x'])\n",
        "import os\nos.execl('/bin/sh', 'sh')\n",
        "from os import system\n",
        "from os import popen, execl\n",
        "import ctypes\n",
        "from ctypes import CDLL\n",
        "import _ctypes\n",
        "import importlib\n",
        "import importlib.util\n",
        "from importlib import util\n",
        "import claire\n",
        "from claire.plugin_sdk import Document\n",
        "from claire.store import db\n",
        "__import__('socket')\n",
        "import builtins\nbuiltins.__import__('socket')\n",
        "foo.__import__('socket')\n",
        "importlib_alias.import_module('os')\n",
        "exec('1')\n",
        "eval('1')\n",
        "import builtins\nbuiltins.exec('1')\n",
        "import builtins\nbuiltins.eval('1')\n",
        "from builtins import exec as run_it\nrun_it('1')\n",
        "from builtins import eval as ev\nev('1')\n",
        "from builtins import __import__ as imp\nimp('socket')\n",
    ],
)
def test_agent_client_import_check_blocks_forbidden_constructs(source):
    """Verify check_source blocks network, subprocess, FFI, loader, core, and dynamic execution in agent_client."""
    findings = check_source(source, "helper.py", network_modules=("httpx", "socket"), executables=("sh",), plugin_type=TYPE_AGENT_CLIENT)
    assert findings, f"expected importcheck finding for: {source!r}"


def test_agent_client_import_check_allows_benign_stdlib_and_ignores_tests(env):
    """Verify benign stdlib imports pass, undeclared forbidden .py fails discovery, and tests/ is skipped."""
    benign = "import json\nimport pathlib\nimport asyncio\nimport shutil\n"
    assert check_source(benign, "helper.py", plugin_type=TYPE_AGENT_CLIENT) == []

    bad_folder = make_agent_client(env.folder, "example.badpy", extra_files={"helper.py": "import builtins\nbuiltins.__import__('socket')\n"})
    test_only_folder = make_agent_client(env.folder, "example.testonly", extra_files={"tests/test_helper.py": "import socket\n"})

    assert check_folder(bad_folder, plugin_type=TYPE_AGENT_CLIENT)
    assert check_folder(test_only_folder, plugin_type=TYPE_AGENT_CLIENT) == []

    statuses = {s.id: s for s in compute_states(discover(env.project, env.folder), None)}
    assert statuses["example.badpy"].state == STATE_INVALID
    assert "import check:" in (statuses["example.badpy"].reason or "")
    assert statuses["example.testonly"].state == STATE_NEW


def test_agent_client_overlap_and_replace(env):
    """Verify duplicate client_id triggers overlap checks, 409 plugin_overlap, STATE_OVERLAP, and replace switching."""
    from claire.plugin_host.service import PluginServiceError, get_agent_client, switch_plugin

    make_agent_client(env.project, "claire.antigravity", client_id="antigravity")
    lock_project(env.project)
    ext = make_agent_client(env.folder, "example.alt", client_id="antigravity")
    other = make_agent_client(env.folder, "example.other", client_id="other-cli")

    disc = discover(env.project, env.folder)
    manifests = [p.manifest for p in disc.plugins if p.manifest is not None]
    pairs = overlap_pairs(manifests)
    assert pairs["claire.antigravity"] == {"example.alt"}
    assert pairs["example.alt"] == {"claire.antigravity"}
    assert pairs["example.other"] == set()

    by_id = {m.id: m for m in manifests}
    assert overlap_hosts(by_id["claire.antigravity"], by_id["example.alt"]) == ["client_id:antigravity"]
    assert overlap_hosts(by_id["claire.antigravity"], by_id["example.other"]) == []

    snap = rt.runtime().current()
    alt_status = snap.status("example.alt")
    assert alt_status is not None
    assert alt_status.state == STATE_NEW
    assert alt_status.overlap_group == ("claire.antigravity", "example.alt")
    assert snap.status("example.other").overlap_group == ()

    with pytest.raises(PluginServiceError) as exc_info:
        switch_plugin("example.alt", enabled=True, digest=alt_status.digest, replace=[], actor=1, via="test")
    assert exc_info.value.status == 409
    assert exc_info.value.code == "plugin_overlap"
    assert exc_info.value.detail["enabled"] == ["claire.antigravity"]

    result = switch_plugin("example.alt", enabled=True, digest=alt_status.digest, replace=["claire.antigravity"], actor=1, via="test")
    assert result.replaced == ("claire.antigravity",)
    assert get_agent_client("antigravity", fresh=True).id == "example.alt"

    # When DB records enable both conflicting plugins simultaneously, both enter STATE_OVERLAP and lookup returns None.
    from claire.store import db as dbm

    conn = dbm.connect_existing(env.db)
    try:
        store.switch(conn, "claire.antigravity", enabled=True, digest=snap.status("claire.antigravity").digest, revision=1, replace=(), changed_by=1)
    finally:
        conn.close()
    rt.runtime().invalidate()
    overlap_snap = rt.runtime().current()
    assert overlap_snap.status("claire.antigravity").state == STATE_OVERLAP
    assert overlap_snap.status("example.alt").state == STATE_OVERLAP
    assert rt.runtime().agent_client("antigravity") is None
    assert ext.is_dir() and other.is_dir()


def test_agent_client_non_import_runtime_and_store_lifecycle(env):
    """Verify agent_client runtime non-import, ImportError guards, store persistence, changed, and unlocked transitions."""
    from claire.plugin_host.runtime import import_plugin, register_plugin
    from claire.plugin_host.service import get_agent_client, switch_plugin

    proj_folder = make_agent_client(env.project, "claire.antigravity", client_id="antigravity")
    lock_project(env.project)
    ext_folder = make_agent_client(env.folder, "example.client", client_id="example-agent")

    snap = rt.runtime().current()
    proj_status = snap.status("claire.antigravity")
    ext_status = snap.status("example.client")
    assert proj_status is not None and proj_status.state == STATE_ENABLED
    assert ext_status is not None and ext_status.state == STATE_NEW
    assert snap.entries == []
    assert snap.commands == {}
    assert rt.runtime()._loaded == {}
    assert not any(k.startswith("claire_plugins.agent_client") for k in sys.modules)
    assert [s.id for s in snap.agent_clients()] == ["claire.antigravity"]
    assert snap.agent_client("antigravity") is proj_status
    assert rt.runtime().agent_client("antigravity") is proj_status
    assert get_agent_client("antigravity") is proj_status
    assert get_agent_client("example-agent") is None

    with pytest.raises(ImportError):
        import_plugin(proj_status)
    with pytest.raises(ImportError):
        register_plugin(proj_status)

    switch_plugin("example.client", enabled=True, digest=ext_status.digest, actor=1, via="test")
    assert get_agent_client("example-agent", fresh=True).id == "example.client"
    records = store.read_records_file(env.db)
    assert records is not None and records.records["example.client"].enabled is True

    # A fresh PluginRuntime instance restores the enabled state from claire.db without importing Python code.
    fresh_rt = rt.PluginRuntime()
    assert fresh_rt.agent_client("example-agent").id == "example.client"
    assert not any(k.startswith("claire_plugins.agent_client") for k in sys.modules)

    switch_plugin("example.client", enabled=False, actor=1, via="test")
    assert rt.runtime().current().status("example.client").state == STATE_DISABLED
    assert get_agent_client("example-agent") is None

    switch_plugin("example.client", enabled=True, digest=ext_status.digest, actor=1, via="test")
    assert get_agent_client("example-agent", fresh=True).id == "example.client"

    # Modifying a file in the external bundle transitions it to STATE_CHANGED and hides it from get_agent_client.
    (ext_folder / "rules/AGENTS.md").write_text("# Modified\n", encoding="utf-8")
    _harden_tree(ext_folder)
    rt.runtime().invalidate()
    assert rt.runtime().current().status("example.client").state == STATE_CHANGED
    assert get_agent_client("example-agent") is None

    # Modifying a file in the locked project bundle transitions it to STATE_UNLOCKED and hides it from get_agent_client.
    (proj_folder / "rules/AGENTS.md").write_text("# Tampered\n", encoding="utf-8")
    _harden_tree(proj_folder)
    rt.runtime().invalidate()
    assert rt.runtime().current().status("claire.antigravity").state == STATE_UNLOCKED
    assert get_agent_client("antigravity") is None


def test_cli_plugin_commands_and_mixed_project_lock(env, monkeypatch, capsys):
    """Verify CLI list, show, check, disable, enable, run, parser help, and mixed or broken project.lock handling."""
    from claire.cli import build_parser, main
    from claire.plugin_host.service import get_agent_client

    assert "agent clients" in build_parser().format_help()

    make_ingest_adapter(env.project, "claire.alpha")
    ag_folder = make_agent_client(env.project, "claire.antigravity", client_id="antigravity")
    ext_folder = make_agent_client(env.folder, "example.ext", client_id="ext-agent")
    monkeypatch.setattr("claire.plugin_host.lock.head_lock", lambda folder: None)

    assert main(["plugin", "lock", "--json"]) == 0
    capsys.readouterr()
    locked = parse_lock((env.project / "project.lock").read_text(encoding="utf-8"))
    assert set(locked) == {"claire.alpha", "claire.antigravity"}

    assert main(["plugin", "list", "--json"]) == 0
    listed = json.loads(capsys.readouterr().out)["data"]["plugins"]
    by_id = {item["id"]: item for item in listed}
    assert by_id["claire.antigravity"]["state"] == STATE_ENABLED
    assert by_id["claire.antigravity"]["type"] == TYPE_AGENT_CLIENT
    assert by_id["claire.antigravity"]["client_id"] == "antigravity"
    assert by_id["claire.antigravity"]["required_scopes"] == ["readonly", "write"]
    assert set(by_id["claire.antigravity"]["files_sha256"]) == set(DEFAULT_BUNDLE_FILES)

    assert main(["plugin", "show", "claire.antigravity", "--json"]) == 0
    shown = json.loads(capsys.readouterr().out)["data"]
    assert shown["id"] == "claire.antigravity"
    assert shown["files_sha256"] == by_id["claire.antigravity"]["files_sha256"]

    assert main(["plugin", "check", str(ag_folder), "--json"]) == 0
    check_proj = json.loads(capsys.readouterr().out)["data"]["results"]
    assert check_proj[str(ag_folder.resolve())] == []

    assert main(["plugin", "check", str(ext_folder), "--json"]) == 0
    check_ext = json.loads(capsys.readouterr().out)["data"]["results"]
    assert check_ext[str(ext_folder.resolve())] == []

    assert main(["plugin", "disable", "claire.antigravity", "--json"]) == 0
    capsys.readouterr()
    assert get_agent_client("antigravity", fresh=True) is None

    assert main(["plugin", "enable", "claire.antigravity", "--digest", shown["digest"], "--json"]) == 0
    capsys.readouterr()
    assert get_agent_client("antigravity", fresh=True).id == "claire.antigravity"

    assert main(["plugin", "run", "claire.antigravity", "anything"]) == 2
    capsys.readouterr()

    # Verify cmd_lock refuses a broken project agent_client (executable bit, missing declared file, forbidden import) without overwriting project.lock.
    lock_before = (env.project / "project.lock").read_text(encoding="utf-8")
    os.chmod(ag_folder / "rules/AGENTS.md", 0o755)
    assert main(["plugin", "lock", "--json"]) == 4
    err_exec = json.loads(capsys.readouterr().out)
    assert "executable mode bits" in err_exec["error"]
    assert (env.project / "project.lock").read_text(encoding="utf-8") == lock_before

    os.chmod(ag_folder / "rules/AGENTS.md", 0o644)
    (ag_folder / "rules/AGENTS.md").unlink()
    assert main(["plugin", "lock", "--json"]) == 4
    err_missing = json.loads(capsys.readouterr().out)
    assert "missing or not a regular file" in err_missing["error"]
    assert (env.project / "project.lock").read_text(encoding="utf-8") == lock_before

    (ag_folder / "rules/AGENTS.md").write_text(DEFAULT_BUNDLE_FILES["rules/AGENTS.md"], encoding="utf-8")
    (ag_folder / "sneaky.py").write_text("import socket\n", encoding="utf-8")
    _harden_tree(ag_folder)
    assert main(["plugin", "lock", "--json"]) == 4
    err_import = json.loads(capsys.readouterr().out)
    assert "import check:" in err_import["error"]
    assert (env.project / "project.lock").read_text(encoding="utf-8") == lock_before

    # Verify cmd_lock refuses duplicate project plugin IDs across type folders.
    (ag_folder / "sneaky.py").unlink()
    make_ingest_adapter(env.project, "claire.antigravity")
    assert main(["plugin", "lock", "--json"]) == 4
    err_dup = json.loads(capsys.readouterr().out)
    assert "duplicate project plugin id" in err_dup["error"]
    assert (env.project / "project.lock").read_text(encoding="utf-8") == lock_before


def test_repo_antigravity_reference_bundle_and_delivery_contract():
    """Verify the shipped claire.antigravity bundle passes discovery, lock, security, tool isolation, and archive packaging checks."""
    from claire.api import clients_host

    repo_root = Path(__file__).resolve().parents[1]
    project_plugins = repo_root / "plugins"
    bundle_dir = project_plugins / "agent_client" / "claire.antigravity"
    expected_files = (
        "client_bundle/mcp_config.json.tmpl",
        "client_bundle/rules/AGENTS.md",
        "client_bundle/agents/claire-extractor.md",
        "client_bundle/skills/claire-extract/SKILL.md",
        "client_bundle/skills/claire-query/SKILL.md",
    )

    disc = discover(project_plugins, Path("/nonexistent"))
    item = next((p for p in disc.plugins if p.id == "claire.antigravity"), None)
    assert item is not None, "claire.antigravity is missing from plugins/agent_client/"
    assert item.problem is None, item.problem
    assert item.manifest is not None
    assert item.manifest.type == TYPE_AGENT_CLIENT
    assert item.manifest.client_id == "antigravity"
    assert item.manifest.api == 1
    assert item.manifest.required_scopes == ("readonly", "write")
    assert item.manifest.files == expected_files
    assert item.manifest.permissions.network == ()
    assert item.manifest.permissions.private_network is False
    assert item.manifest.permissions.network_modules == ()
    assert item.manifest.permissions.executables == ()
    assert item.manifest.permissions.providers == ()
    assert check_folder(bundle_dir, plugin_type=TYPE_AGENT_CLIENT) == []
    check_no_executable_files(bundle_dir, expected_files)

    for rel in expected_files:
        assert item.files_sha256[rel] == hashlib.sha256((bundle_dir / rel).read_bytes()).hexdigest()

    status = next(s for s in compute_states(disc, None) if s.id == "claire.antigravity")
    assert status.state == STATE_NEW
    manifest_payload = json.loads(clients_host.build_manifest_bytes(status, delivery_mode="uri_path").decode("utf-8"))
    assert manifest_payload["client_id"] == "antigravity"
    assert [entry["path"] for entry in manifest_payload["files"]] == sorted(expected_files)

    tar_bytes = clients_host.build_bundle_tar_gz(status)
    with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:gz") as tf:
        assert [member.name for member in tf.getmembers()] == sorted(expected_files)

    mcp_template_raw = (bundle_dir / "client_bundle/mcp_config.json.tmpl").read_text(encoding="utf-8")
    assert json.loads(mcp_template_raw) == {"mcpServers": {"claire": {"url": "{{MCP_URL}}"}}}
    for forbidden_secret_key in ("authorization", "bearer", "secret", "password", "api_key"):
        assert forbidden_secret_key not in mcp_template_raw.lower()

    extractor_text = (bundle_dir / "client_bundle/agents/claire-extractor.md").read_text(encoding="utf-8")
    fm_match = re.match(r"^---\n(.*?)\n---\n", extractor_text, flags=re.DOTALL)
    assert fm_match is not None
    fm_body = fm_match.group(1)
    assert "name: claire-extractor" in fm_body
    assert "subagent: true" in fm_body
    assert "mainAgent: false" in fm_body
    tools_match = re.search(r"^tools:\s*\[([^\]]+)\]\s*$", fm_body, flags=re.MULTILINE)
    assert tools_match is not None
    declared_tools = [tok.strip() for tok in tools_match.group(1).split(",") if tok.strip()]
    assert declared_tools == [
        "list_staged_sources",
        "claim_staged_source",
        "renew_staged_claim",
        "get_staged_source",
        "get_extraction_spec",
        "resolve_entity",
        "search",
        "neighbors",
        "submit_client_extraction",
    ]

    rules_text = (bundle_dir / "client_bundle/rules/AGENTS.md").read_text(encoding="utf-8")
    assert not rules_text.startswith("---\n")
    assert "<untrusted_source_document" in rules_text
    assert "claire-extractor" in rules_text
    assert "CT4" in rules_text and "CT23" in rules_text
    assert "dry_run=True" in rules_text

    extract_skill_text = (bundle_dir / "client_bundle/skills/claire-extract/SKILL.md").read_text(encoding="utf-8")
    assert "name: claire-extract" in extract_skill_text
    assert "offset" in extract_skill_text and "limit" in extract_skill_text and "has_more" in extract_skill_text
    assert "dry_run=True" in extract_skill_text and "submit_client_extraction" in extract_skill_text

    query_skill_text = (bundle_dir / "client_bundle/skills/claire-query/SKILL.md").read_text(encoding="utf-8")
    assert "name: claire-query" in query_skill_text
    for query_tool in ("overview", "resolve_entity", "search", "neighbors", "path", "context", "node", "documents", "document", "stats"):
        assert query_tool in query_skill_text

    for path in sorted(bundle_dir.rglob("*")):
        if path.is_file():
            lower_text = path.read_text(encoding="utf-8").lower()
            for retired in ("claude", "codex", "chatgpt", "server-everything", "stdio"):
                assert retired not in lower_text, f"{retired!r} found in {path.relative_to(bundle_dir)}"
