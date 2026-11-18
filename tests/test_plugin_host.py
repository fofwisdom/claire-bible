"""Plugin host: manifest, digest, discovery and states, overlap, records, loading, service, CLI (docs/implementation/INGESTION.md §2.2)."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from claire.config import get_settings
from claire.plugin_host import runtime as rt
from claire.plugin_host.discovery import compute_states, discover, overlap_groups, overlap_pairs
from claire.plugin_host.files import PluginFileError, folder_digest
from claire.plugin_host.importcheck import check_source
from claire.plugin_host.lock import LockItem, parse_lock, render_lock
from claire.plugin_host.manifest import ManifestError, parse_manifest, python_name
from claire.store import plugins as store

COMMIT = "b8694402b6ee0ce08f5af1a6af2853bb911575d5"

ADAPTER = '''
from claire.plugin_sdk import BaseFetcher, Document

class Fetcher(BaseFetcher):
    @classmethod
    def can_handle(cls, url):
        return "/item/" in url

    @classmethod
    def fetch(cls, url, **kwargs):
        return Document(url=url, title="plugin:{name}", raw_text="text", source_type="{pid}")

def register(ctx):
    ctx.fetcher(Fetcher, name="{name}", priority={priority}, hosts={hosts!r})
'''


def manifest_text(pid: str, *, name: str, hosts: list[str], priority: int = 100, revision: int = 1, api: int = 1, extra: str = "") -> str:
    return f'''manifest = 1
id = "{pid}"
type = "ingest_adapter"
revision = {revision}
core_commit = "{COMMIT}"
api = {api}
entry = "adapter:register"
license = "MIT"
maturity = "experimental"
{extra}
[text.en]
name = "{name}"
description = "Test plugin."

[text.ko]
name = "{name}"
description = "시험 플러그인."

[[provides]]
kind = "fetcher"
name = "{name}"
priority = {priority}
hosts = {json.dumps(hosts)}
source_types = ["{pid}"]

[permissions]
network = {json.dumps(hosts)}
'''


def make_plugin(root: Path, pid: str, *, name: str | None = None, hosts: list[str] | None = None, priority: int = 100, adapter: str | None = None, **kw) -> Path:
    name = name or pid.split(".", 1)[1]
    hosts = hosts or [f"{name}.test"]
    folder = root / "ingest_adapter" / pid
    folder.mkdir(parents=True)
    (folder / "plugin.toml").write_text(manifest_text(pid, name=name, hosts=hosts, priority=priority, **kw), encoding="utf-8")
    (folder / "__init__.py").write_text("", encoding="utf-8")
    (folder / "adapter.py").write_text(adapter if adapter is not None else ADAPTER.format(name=name, pid=pid, priority=priority, hosts=tuple(hosts)), encoding="utf-8")
    return folder


def lock_project(project: Path) -> None:
    items = {}
    for folder in sorted((project / "ingest_adapter").iterdir()):
        if folder.name.startswith("_"):
            continue
        m = parse_manifest(__import__("tomllib").loads((folder / "plugin.toml").read_text()))
        items[m.id] = LockItem(m.id, m.revision, folder_digest(folder))
    (project / "project.lock").write_text(render_lock(items), encoding="utf-8")


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A server with a temporary project plugin folder, plugin folder, and claire.db."""
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
    rt.runtime().invalidate()
    yield type("Env", (), {"project": project, "folder": folder, "db": db_file})
    get_settings.cache_clear()
    rt.runtime().invalidate()


# --- manifest ------------------------------------------------------------------


def test_manifest_rejects_unknown_key_and_bad_id():
    import tomllib

    good = tomllib.loads(manifest_text("example.forum", name="forum", hosts=["forum.test"]))
    assert parse_manifest(good).id == "example.forum"
    with pytest.raises(ManifestError, match="unknown key"):
        parse_manifest({**good, "extra": 1})
    with pytest.raises(ManifestError, match="id"):
        parse_manifest({**good, "id": "Example.forum-x"})
    with pytest.raises(ManifestError, match="host pattern"):
        parse_manifest({**good, "provides": [{**good["provides"][0], "hosts": ["x*.test"]}]})
    with pytest.raises(ManifestError, match="text.en"):
        parse_manifest({**good, "text": {"ko": good["text"]["ko"]}})


def test_python_name_is_injective_for_two_part_ids():
    assert python_name("claire.naver_clip") == "claire_naver_clip"
    assert python_name("a.b_c") != python_name("ab.c")


# --- files and lock -------------------------------------------------------------


def test_digest_equals_sha256sum_lines(tmp_path):
    folder = tmp_path / "p"
    (folder / "sub").mkdir(parents=True)
    (folder / "a.txt").write_bytes(b"A")
    (folder / "sub" / "b.txt").write_bytes(b"B")
    (folder / "__pycache__").mkdir()
    (folder / "__pycache__" / "x.pyc").write_bytes(b"skip")
    lines = "\n".join(
        [f"{hashlib.sha256(b'A').hexdigest()}  a.txt", f"{hashlib.sha256(b'B').hexdigest()}  sub/b.txt"]
    )
    assert folder_digest(folder) == "sha256:" + hashlib.sha256(lines.encode()).hexdigest()


def test_symbolic_link_and_world_writable_file_are_refused(tmp_path):
    folder = tmp_path / "p"
    folder.mkdir()
    (folder / "x").write_text("x")
    (folder / "link").symlink_to(folder / "x")
    with pytest.raises(PluginFileError, match="symbolic link"):
        folder_digest(folder)
    (folder / "link").unlink()
    os.chmod(folder / "x", 0o666)
    with pytest.raises(PluginFileError, match="other users"):
        folder_digest(folder)


def test_lock_round_trip():
    items = {"claire.a": LockItem("claire.a", 2, "sha256:abc")}
    assert parse_lock(render_lock(items)) == items


# --- import check ----------------------------------------------------------------


def test_import_check_finds_core_network_and_process_imports():
    src = "import httpx\nfrom claire.store import db\nimport subprocess\nfrom claire.plugin_sdk import Document\nfrom . import helper\n"
    kinds = sorted(f.kind for f in check_source(src, "a.py", network_modules=(), executables=()))
    assert kinds == ["core_import", "network_module", "process"]
    assert check_source(src.replace("from claire.store import db\n", ""), "a.py", network_modules=("httpx",), executables=("ffmpeg",)) == []


# --- discovery and states ---------------------------------------------------------


def test_states_of_both_folders(env):
    make_plugin(env.project, "claire.alpha")
    make_plugin(env.project, "claire.beta")
    lock_project(env.project)
    make_plugin(env.project, "claire.unlisted")  # not in the lock
    make_plugin(env.folder, "claire.taken")  # reserved publisher in the plugin folder
    make_plugin(env.folder, "example.gamma")
    make_plugin(env.folder, "example.future", api=99)
    (env.folder / "theme").mkdir()  # unknown type folder
    # beta changes after the lock
    (env.project / "ingest_adapter" / "claire.beta" / "adapter.py").write_text(ADAPTER.format(name="beta", pid="claire.beta", priority=101, hosts=("beta.test",)))
    states = {s.id: s.state for s in compute_states(discover(env.project, env.folder), None)}
    assert states == {
        "claire.alpha": "new",
        "claire.beta": "unlocked",
        "claire.unlisted": "invalid",
        "claire.taken": "invalid",
        "example.gamma": "new",
        "example.future": "incompatible",
    }
    assert [p.name for p in discover(env.project, env.folder).unknown_folders] == ["theme"]


def test_overlap_groups_ignore_star_and_join_through_members():
    import tomllib

    def m(pid, hosts, name=None):
        return parse_manifest(tomllib.loads(manifest_text(pid, name=name or pid.split(".")[1], hosts=hosts)))

    manifests = [m("a.one", ["a.x.test"]), m("b.two", ["*.x.test", "y.test"]), m("c.three", ["y.test"]), m("d.four", ["*"]), m("e.five", ["*"], name="one"), m("f.six", ["x.test"])]
    groups = overlap_groups(overlap_pairs(manifests))
    # b overlaps a (subdomain) and c (same host); e has the entry name of a; "*" alone is no overlap; "*.x.test" does not match x.test
    assert groups["a.one"] == ("a.one", "b.two", "c.three", "e.five")
    assert "d.four" not in groups and "f.six" not in groups


# --- records, first start, loading ---------------------------------------------------


def test_first_start_enables_project_plugins_and_loads_them(env):
    make_plugin(env.project, "claire.alpha", hosts=["alpha.test"])
    lock_project(env.project)
    make_plugin(env.folder, "example.gamma", hosts=["gamma.test"])
    snap = rt.runtime().current()
    states = {s.id: s.state for s in snap.statuses}
    assert states == {"claire.alpha": "enabled", "example.gamma": "new"}
    assert [(e.plugin_id, e.name) for e in snap.entries] == [("claire.alpha", "alpha")]

    from claire.ingest.registry import registry

    assert registry.classify("https://alpha.test/item/1") == "alpha"
    assert registry.classify("https://alpha.test/other") == "web"  # can_handle refuses by path
    assert registry.classify("https://gamma.test/item/1") == "web"  # new: not loaded


def test_no_project_plugins_does_not_start_the_store(env):
    rt.runtime().current()
    assert store.read_records_file(env.db) is None


def test_switch_with_overlap_needs_exact_replace(env):
    from claire.plugin_host.service import PluginServiceError, switch_plugin

    make_plugin(env.project, "claire.alpha", hosts=["alpha.test"])
    lock_project(env.project)
    make_plugin(env.folder, "example.alt", name="alt", hosts=["*.alpha.test", "alpha.test"])
    snap = rt.runtime().current()
    alt = snap.status("example.alt")
    assert alt.state == "new" and alt.overlap_group == ("claire.alpha", "example.alt")

    with pytest.raises(PluginServiceError) as exc:
        switch_plugin("example.alt", enabled=True, digest=alt.digest, replace=[], actor=1, via="test")
    assert exc.value.code == "plugin_overlap" and exc.value.detail["enabled"] == ["claire.alpha"]
    with pytest.raises(PluginServiceError) as exc:
        switch_plugin("example.alt", enabled=True, digest="sha256:old", replace=["claire.alpha"], actor=1, via="test")
    assert exc.value.code == "plugin_changed"
    with pytest.raises(PluginServiceError) as exc:
        switch_plugin("example.alt", enabled=True, digest=None, actor=1, via="test")
    assert exc.value.code == "digest_required"

    switch_plugin("example.alt", enabled=True, digest=alt.digest, replace=["claire.alpha"], actor=1, via="test")
    states = {s.id: s.state for s in rt.runtime().current().statuses}
    assert states == {"claire.alpha": "disabled", "example.alt": "enabled"}

    from claire.store import auth_db

    conn = auth_db.connect_auth_db(auth_db.auth_db_path(env.db.parent))
    actions = [(r["action"], (r["detail"] or {}).get("replaced_by")) for r in auth_db.list_admin_audit(conn)]
    conn.close()
    assert ("plugin.disable", "example.alt") in actions and ("plugin.enable", None) in actions


def test_changed_plugin_of_plugin_folder_stops_until_new_approval(env):
    from claire.plugin_host.service import switch_plugin

    make_plugin(env.project, "claire.alpha")
    lock_project(env.project)
    folder = make_plugin(env.folder, "example.gamma", hosts=["gamma.test"])
    status = rt.runtime().current().status("example.gamma")
    switch_plugin("example.gamma", enabled=True, digest=status.digest, actor=1, via="test")
    assert rt.runtime().current().status("example.gamma").state == "enabled"
    (folder / "extra.txt").write_text("new file")
    rt.runtime().invalidate()
    assert rt.runtime().current().status("example.gamma").state == "changed"


def test_failed_and_mismatched_plugins_do_not_stop_the_process(env):
    make_plugin(env.project, "claire.broken", adapter="raise RuntimeError('boom')\n")
    make_plugin(env.project, "claire.liar", adapter=ADAPTER.format(name="liar", pid="claire.liar", priority=5, hosts=("liar.test",)))
    make_plugin(env.project, "claire.alpha")
    lock_project(env.project)
    states = {s.id: (s.state, s.reason) for s in rt.runtime().current().statuses}
    assert states["claire.broken"][0] == "failed" and "boom" in states["claire.broken"][1]
    assert states["claire.liar"][0] == "invalid" and "provides" in states["claire.liar"][1]
    assert states["claire.alpha"][0] == "enabled"


def test_removed_record_and_process_report(env):
    from claire.plugin_host.service import delete_record

    make_plugin(env.project, "claire.alpha")
    folder = make_plugin(env.project, "claire.beta")
    lock_project(env.project)
    runtime = rt.PluginRuntime()
    runtime.set_process_name("test")
    runtime.current()
    report = json.loads((env.db.parent / "run" / "plugins_test.json").read_text())
    assert {p["id"] for p in report["plugins"]} == {"claire.alpha", "claire.beta"}

    import shutil

    shutil.rmtree(folder)
    lock_project(env.project)
    rt.runtime().invalidate()
    assert rt.runtime().current().status("claire.beta").state == "removed"
    delete_record("claire.beta", actor=1, via="test")
    assert rt.runtime().current().status("claire.beta") is None


# --- CLI ------------------------------------------------------------------------------


def test_cli_new_lock_check_and_run(env, monkeypatch, capsys):
    import shutil

    from claire.cli import main

    shutil.copytree(Path(__file__).resolve().parents[1] / "plugins" / "ingest_adapter" / "_template", env.project / "ingest_adapter" / "_template")
    monkeypatch.setattr("claire.plugin_host.service.running_core_commit", lambda: COMMIT)
    assert main(["plugin", "new", "claire.sample", "--type", "ingest_adapter"]) == 0
    assert main(["plugin", "new", "example.mine", "--type", "ingest_adapter"]) == 0
    assert (env.folder / "ingest_adapter" / "example.mine" / "tests" / "test_example_mine.py").is_file()
    monkeypatch.setattr("claire.plugin_host.lock.head_lock", lambda folder: None)
    assert main(["plugin", "lock"]) == 0
    capsys.readouterr()
    assert main(["plugin", "list", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    states = {p["id"]: p["state"] for p in out["data"]["plugins"]}
    assert states == {"claire.sample": "enabled", "example.mine": "new"}
    rc = main(["plugin", "check", str(env.folder / "ingest_adapter" / "example.mine")])
    assert rc == 0, capsys.readouterr()
    assert main(["plugin", "run", "example.mine", "x"]) == 4  # not enabled


def test_cli_lock_refuses_change_without_new_revision(env, monkeypatch):
    from claire.cli import main

    make_plugin(env.project, "claire.alpha")
    lock_project(env.project)
    head = parse_lock((env.project / "project.lock").read_text())
    monkeypatch.setattr("claire.plugin_host.lock.head_lock", lambda folder: head)
    (env.project / "ingest_adapter" / "claire.alpha" / "note.txt").write_text("change")
    assert main(["plugin", "lock"]) == 4
