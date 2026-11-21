"""``claire plugin …`` (docs/implementation/INGESTION.md §2.2). ASD-STE100 English; ``--json`` gives the ``AgentOutput`` contract (C3)."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from . import ORIGIN_PROJECT, PLUGIN_API_LEVELS, PLUGIN_TYPES, PROJECT_PUBLISHER, STATE_ENABLED, TYPE_INGEST_ADAPTER
from .manifest import ID_RE, ManifestError, publisher_of

EXIT_OK, EXIT_ARGS, EXIT_PRECONDITION = 0, 2, 4


def _json(args: Any) -> bool:
    return bool(getattr(args, "json", False))


def _emit(args: Any, data: Any, human: str) -> None:
    if _json(args):
        print(json.dumps(data, ensure_ascii=False, default=str))
    else:
        print(human)


def _fail(args: Any, code: int, message: str, **data: Any) -> int:
    if _json(args):
        print(json.dumps({"error": message, **data}, ensure_ascii=False, default=str))
    print(f"[error] {message}", file=sys.stderr)
    return code


def _version(item: dict[str, Any]) -> str:
    if item.get("revision") is None:
        return "-"
    return f"r{item['revision']} @ {(item.get('core_commit') or '')[:8]}"


def cmd_list(args: Any) -> int:
    from .service import list_plugins

    data = list_plugins()
    lines = [
        f"project plugin folder: {data['project_plugin_folder']}",
        f"plugin folder        : {data['plugin_folder']}",
        f"core commit          : {data['core_commit'] or 'unknown'}",
        "",
        f"{'ID':<28} {'ORIGIN':<14} {'STATE':<20} {'VERSION':<16} REASON",
    ]
    for item in data["plugins"]:
        lines.append(f"{item['id']:<28} {item['origin'] or '-':<14} {item['state']:<20} {_version(item):<16} {item['reason'] or ''}")
    for folder in data["unknown_folders"]:
        lines.append(f"unknown folder: {folder} (not loaded)")
    _emit(args, data, "\n".join(lines))
    return EXIT_OK


def cmd_show(args: Any) -> int:
    from .service import PluginServiceError, list_plugins

    data = list_plugins()
    for item in data["plugins"]:
        if item["id"] == args.plugin_id:
            _emit(args, item, json.dumps(item, ensure_ascii=False, indent=2, default=str))
            return EXIT_OK
    err = PluginServiceError(404, "not_found", f"plugin {args.plugin_id!r} is not found")
    return _fail(args, EXIT_PRECONDITION, str(err))


def cmd_enable(args: Any) -> int:
    from .service import PluginServiceError, switch_plugin

    try:
        result = switch_plugin(args.plugin_id, enabled=True, digest=args.digest, replace=args.replace or [], actor=None, via="cli")
    except PluginServiceError as exc:
        return _fail(args, EXIT_ARGS if exc.status == 400 else EXIT_PRECONDITION, str(exc), code=exc.code, **exc.detail)
    _emit(args, {"id": result.plugin_id, "enabled": True, "replaced": list(result.replaced)}, f"enabled: {result.plugin_id}" + (f" (switched off: {', '.join(result.replaced)})" if result.replaced else ""))
    return EXIT_OK


def cmd_disable(args: Any) -> int:
    from .service import PluginServiceError, switch_plugin

    try:
        switch_plugin(args.plugin_id, enabled=False, actor=None, via="cli")
    except PluginServiceError as exc:
        return _fail(args, EXIT_ARGS if exc.status == 400 else EXIT_PRECONDITION, str(exc), code=exc.code, **exc.detail)
    _emit(args, {"id": args.plugin_id, "enabled": False}, f"disabled: {args.plugin_id}")
    return EXIT_OK


def check_plugin_folder(folder: Path) -> list[str]:
    """Problems of one plugin folder for a contributor: manifest, digest, lock, overlap, import check, core commit, registration."""
    from .discovery import PluginStatus, discover, overlap_pairs
    from .lock import read_lock
    from . import runtime as rt
    from .runtime import PluginRegistrationMismatch, register_plugin

    problems: list[str] = []
    folder = folder.resolve()
    project = rt.project_plugin_folder().resolve()
    origin = ORIGIN_PROJECT if folder.is_relative_to(project) else "plugin_folder"
    root = folder.parent.parent
    discovery = discover(root, Path("/nonexistent")) if origin == ORIGIN_PROJECT else discover(Path("/nonexistent"), root)
    item = next((p for p in discovery.plugins if p.path.resolve() == folder), None)
    if item is None:
        return [f"{folder}: not a plugin folder (<root>/<type folder>/<id>/ with a known type folder: {', '.join(PLUGIN_TYPES)})"]
    if item.problem:
        problems.append(item.problem)
    if item.manifest is None:
        return problems
    m = item.manifest
    if m.api not in PLUGIN_API_LEVELS:
        problems.append(f"plugin API level {m.api} is not supported")
    if origin == ORIGIN_PROJECT:
        lock = read_lock(project)
        if m.id in lock and lock[m.id].digest != item.digest:
            problems.append("the digest is not the digest in project.lock; raise the revision and run `claire plugin lock`")
        if "ko" not in m.text:
            problems.append("a project plugin needs [text.ko]")
    project_discovery = discover(project, Path("/nonexistent"))
    others = [p.manifest for p in project_discovery.plugins if p.manifest is not None and p.manifest.id != m.id]
    pairs = overlap_pairs([m, *others])
    if pairs.get(m.id) and origin == ORIGIN_PROJECT:
        # Project plugins must never overlap (§14 rule 1). A plugin of the plugin folder may overlap; the administrator selects one (§6.4).
        problems.append(f"overlaps project plugins: {', '.join(sorted(pairs[m.id]))}")
    try:
        anc = subprocess.run(["git", "merge-base", "--is-ancestor", m.core_commit, "HEAD"], cwd=folder, capture_output=True, timeout=10, check=False)
        if anc.returncode == 1:
            problems.append(f"core_commit {m.core_commit[:8]} is not an ancestor of HEAD")
    except (OSError, subprocess.SubprocessError):
        pass
    if item.problem is None and m.type == TYPE_INGEST_ADAPTER:
        status = PluginStatus(id=m.id, origin=origin, path=item.path, plugin_type=item.plugin_type, manifest=m, digest=item.digest, state=STATE_ENABLED)
        try:
            register_plugin(status)
        except PluginRegistrationMismatch as exc:
            problems.append(str(exc))
        except Exception as exc:  # noqa: BLE001
            problems.append(f"import or register(ctx) failed: {type(exc).__name__}: {exc}")
    return problems


def cmd_check(args: Any) -> int:
    from .discovery import discover
    from . import runtime as rt

    if args.folder:
        folders = [Path(args.folder)]
    else:
        discovery = discover(rt.project_plugin_folder(), rt.plugin_folder())
        folders = [p.path for p in discovery.plugins]
    results = {str(folder): check_plugin_folder(folder) for folder in folders}
    failed = {k: v for k, v in results.items() if v}
    lines = [f"{'FAIL' if problems else 'ok  '} {folder}" + "".join(f"\n     - {p}" for p in problems) for folder, problems in results.items()]
    _emit(args, {"results": results}, "\n".join(lines) or "no plugin found")
    return EXIT_PRECONDITION if failed else EXIT_OK


def cmd_lock(args: Any) -> int:
    from .discovery import discover
    from .lock import LOCK_FILE, LockItem, head_lock, read_lock, render_lock
    from . import runtime as rt

    project = rt.project_plugin_folder()
    head = head_lock(project) or {}
    current = read_lock(project)
    discovery = discover(project, Path("/nonexistent"))
    items: dict[str, LockItem] = {}
    refused: list[str] = []
    for plugin in discovery.plugins:
        if plugin.manifest is None or plugin.digest is None:
            refused.append(f"{plugin.folder_name}: {plugin.problem}")
            continue
        if plugin.problem and plugin.problem != "the project plugin is not in project.lock" and not plugin.problem.startswith("lock: "):
            refused.append(f"{plugin.manifest.id}: {plugin.problem}")
            continue
        if plugin.manifest.id in items:
            refused.append(f"{plugin.manifest.id}: duplicate project plugin id")
            continue
        if publisher_of(plugin.manifest.id) != PROJECT_PUBLISHER:
            refused.append(f"{plugin.manifest.id}: the project plugin folder holds only the publisher {PROJECT_PUBLISHER!r}")
            continue
        base = head.get(plugin.manifest.id)
        if base is not None and base.digest != plugin.digest and plugin.manifest.revision <= base.revision:
            refused.append(f"{plugin.manifest.id}: the files changed, but the revision {plugin.manifest.revision} is not above r{base.revision} of HEAD")
            continue
        items[plugin.manifest.id] = LockItem(plugin.manifest.id, plugin.manifest.revision, plugin.digest)
    if refused:
        return _fail(args, EXIT_PRECONDITION, "project.lock is not written: " + "; ".join(refused), refused=refused)
    (project / LOCK_FILE).write_text(render_lock(items), encoding="utf-8")
    changed = sorted(k for k in items if current.get(k) != items[k])
    _emit(args, {"written": str(project / LOCK_FILE), "changed": changed}, f"written: {project / LOCK_FILE} ({len(items)} plugins, changed: {', '.join(changed) or 'none'})")
    return EXIT_OK


def cmd_new(args: Any) -> int:
    from . import runtime as rt
    from .service import running_core_commit

    plugin_id = args.plugin_id
    if not ID_RE.fullmatch(plugin_id):
        return _fail(args, EXIT_ARGS, "the id must be <publisher>.<name> (publisher [a-z][a-z0-9]*, name snake_case)")
    if args.type not in PLUGIN_TYPES:
        return _fail(args, EXIT_ARGS, f"unknown plugin type {args.type!r}")
    commit = running_core_commit()
    if not commit or not re.fullmatch(r"[0-9a-f]{40}", commit):
        return _fail(args, EXIT_PRECONDITION, "the commit of the running core is not known (no Git checkout and no build commit)")
    project = rt.project_plugin_folder()
    template = project / args.type / "_template"
    root = project if publisher_of(plugin_id) == PROJECT_PUBLISHER else rt.plugin_folder()
    target = root / args.type / plugin_id
    if not template.is_dir():
        return _fail(args, EXIT_PRECONDITION, f"the template is missing: {template}")
    if target.exists():
        return _fail(args, EXIT_PRECONDITION, f"the folder exists: {target}")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(template, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    except OSError as exc:
        return _fail(args, EXIT_PRECONDITION, f"cannot write {target}: {exc} (in a container the plugin folder is read-only; make the plugin in a checkout)")
    manifest = target / "plugin.toml"
    text = manifest.read_text(encoding="utf-8")
    name = plugin_id.split(".", 1)[1]
    replacements = {
        "__ID__": plugin_id,
        "__NAME__": name,
        "__CORE_COMMIT__": commit,
        "__API__": str(max(PLUGIN_API_LEVELS)),
    }
    for key, value in replacements.items():
        text = text.replace(key, value)
    manifest.write_text(text, encoding="utf-8")
    for path in target.rglob("*.py"):
        content = path.read_text(encoding="utf-8")
        path.write_text(content.replace("__NAME__", name).replace("__ID__", plugin_id), encoding="utf-8")
    tests = target / "tests"
    if tests.is_dir():
        for path in tests.glob("test_template*.py"):
            path.rename(path.with_name(path.name.replace("test_template", f"test_{plugin_id.replace('.', '_')}")))
    _emit(args, {"created": str(target)}, f"created: {target}")
    return EXIT_OK


def cmd_run(args: Any) -> int:
    from .service import snapshot

    snap = snapshot()
    status = snap.status(args.plugin_id)
    if status is None:
        return _fail(args, EXIT_PRECONDITION, f"plugin {args.plugin_id!r} is not found")
    if status.state != STATE_ENABLED:
        return _fail(args, EXIT_PRECONDITION, f"plugin {args.plugin_id!r} is not enabled (state: {status.state})", state=status.state)
    commands = snap.commands.get(args.plugin_id, {})
    if args.command not in commands:
        available = ", ".join(sorted(commands)) or "none"
        return _fail(args, EXIT_ARGS, f"plugin {args.plugin_id!r} has no command {args.command!r} (commands: {available})")
    command = commands[args.command]
    parser = argparse.ArgumentParser(prog=f"claire plugin run {args.plugin_id} {args.command}")
    parser.add_argument("--json", action="store_true", help="output in JSON")
    command.build_parser(parser)
    sub_args = parser.parse_args(args.arguments)
    if _json(args):
        sub_args.json = True
    return int(command.run(sub_args) or 0)


def add_parser(sub: Any) -> None:
    """Add ``plugin`` and its subcommands to the ``claire`` parser."""
    p = sub.add_parser(
        "plugin",
        help="list, check, and switch plugins (ingest adapters and agent clients); run plugin commands",
        description="Plugins of this server: the project plugin folder <app root>/plugins and the plugin folder CLAIRE_PLUGINS_DIR. See docs/implementation/INGESTION.md section 2.",
    )
    ps = p.add_subparsers(dest="plugin_action", required=True)

    pl = ps.add_parser("list", help="show the state of all plugins")
    pl.add_argument("--json", action="store_true", help="output in JSON")
    pl.set_defaults(func=cmd_list)

    psh = ps.add_parser("show", help="show the manifest, digest, and record of one plugin")
    psh.add_argument("plugin_id")
    psh.add_argument("--json", action="store_true", help="output in JSON")
    psh.set_defaults(func=cmd_show)

    pe = ps.add_parser("enable", help="switch a plugin on (host operator path; the same checks and audit as the console)")
    pe.add_argument("plugin_id")
    pe.add_argument("--digest", required=True, help="the digest that you checked (see `claire plugin show`)")
    pe.add_argument("--replace", nargs="*", default=[], metavar="ID", help="enabled plugins of the overlap group to switch off")
    pe.add_argument("--yes", "-y", action="store_true", help="no confirmation (the command asks none)")
    pe.add_argument("--json", action="store_true", help="output in JSON")
    pe.set_defaults(func=cmd_enable)

    pd = ps.add_parser("disable", help="switch a plugin off")
    pd.add_argument("plugin_id")
    pd.add_argument("--yes", "-y", action="store_true", help="no confirmation (the command asks none)")
    pd.add_argument("--json", action="store_true", help="output in JSON")
    pd.set_defaults(func=cmd_disable)

    pc = ps.add_parser("check", help="check plugin folders for contributors (manifest, digest, lock, overlap, imports, registration)")
    pc.add_argument("folder", nargs="?", default=None, help="the folder of one plugin (default: all plugins)")
    pc.add_argument("--json", action="store_true", help="output in JSON")
    pc.set_defaults(func=cmd_check)

    plk = ps.add_parser("lock", help="write plugins/project.lock")
    plk.add_argument("--json", action="store_true", help="output in JSON")
    plk.set_defaults(func=cmd_lock)

    pn = ps.add_parser("new", help="make a plugin folder from the template")
    pn.add_argument("plugin_id")
    pn.add_argument("--type", required=True, choices=list(PLUGIN_TYPES))
    pn.add_argument("--json", action="store_true", help="output in JSON")
    pn.set_defaults(func=cmd_new)

    pr = ps.add_parser("run", help="run a command of an enabled plugin")
    pr.add_argument("plugin_id")
    pr.add_argument("command")
    pr.add_argument("arguments", nargs=argparse.REMAINDER)
    pr.set_defaults(func=cmd_run)


__all__ = ["add_parser", "check_plugin_folder", "ManifestError"]
