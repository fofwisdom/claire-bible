"""Architecture boundary tests for bible, claire, and claire_bible packages."""

from __future__ import annotations

import ast
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

import claire_bible.bootstrap
import claire_bible.web.app
from claire.core.ports import (
    RendererNotConfiguredError,
    get_document_renderer,
    get_slide_renderer,
    get_web_ui_renderer,
    ports_configured,
    reset_ports,
)
from claire.ontology.base import Document
from claire.plugin_host.importcheck import check_source
from claire.store import accounts as accounts_store
from claire.store import db as dbm

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SRC_ROOT = _REPO_ROOT / "src"
_MANAGED_PACKAGES = ("bible", "claire", "claire_bible")


@dataclass(frozen=True)
class _ImportRecord:
    file_path: Path
    line: int
    source_package: str
    target_package: str
    target_module: str


def _resolve_relative_package(pkg_parts: tuple[str, ...], level: int, module_name: str | None) -> tuple[str, str]:
    steps_up = level - 1
    if steps_up >= len(pkg_parts):
        suffix = f".{module_name}" if module_name else ""
        return ("<relative-escape>", f"{'.' * level}{suffix}")
    anchor = pkg_parts[: len(pkg_parts) - steps_up]
    if module_name:
        full = ".".join([*anchor, module_name])
    else:
        full = ".".join(anchor)
    return (anchor[0], full)


def _string_literal(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _kw_literal(call: ast.Call, name: str) -> str | None:
    for kw in call.keywords:
        if kw.arg == name:
            return _string_literal(kw.value)
    return None


def _iter_source_imports() -> Iterator[_ImportRecord]:
    for pkg in _MANAGED_PACKAGES:
        pkg_dir = _SRC_ROOT / pkg
        for py_file in sorted(pkg_dir.rglob("*.py")):
            rel_parent_parts = py_file.relative_to(_SRC_ROOT).parent.parts
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        yield _ImportRecord(py_file, node.lineno, pkg, alias.name.split(".", 1)[0], alias.name)
                elif isinstance(node, ast.ImportFrom):
                    if node.level == 0:
                        if node.module:
                            yield _ImportRecord(py_file, node.lineno, pkg, node.module.split(".", 1)[0], node.module)
                    else:
                        target_pkg, full_mod = _resolve_relative_package(rel_parent_parts, node.level, node.module)
                        yield _ImportRecord(py_file, node.lineno, pkg, target_pkg, full_mod)
                elif isinstance(node, ast.Call):
                    is_import_call = (isinstance(node.func, ast.Attribute) and node.func.attr == "import_module") or (isinstance(node.func, ast.Name) and node.func.id in {"import_module", "__import__"})
                    if not is_import_call:
                        continue
                    mod_arg = _string_literal(node.args[0]) if node.args else _kw_literal(node, "name")
                    if not mod_arg:
                        continue
                    if mod_arg.startswith("."):
                        dots = len(mod_arg) - len(mod_arg.lstrip("."))
                        stripped = mod_arg.lstrip(".") or None
                        base_pkg_str = (_string_literal(node.args[1]) if len(node.args) > 1 else None) or _kw_literal(node, "package")
                        base_parts = tuple(base_pkg_str.split(".")) if base_pkg_str else rel_parent_parts
                        target_pkg, full_mod = _resolve_relative_package(base_parts, dots, stripped)
                        yield _ImportRecord(py_file, node.lineno, pkg, target_pkg, full_mod)
                    else:
                        yield _ImportRecord(py_file, node.lineno, pkg, mod_arg.split(".", 1)[0], mod_arg)


def test_bible_and_claire_have_zero_cross_imports_and_form_dag() -> None:
    """Verify AST import boundaries across bible, claire, and claire_bible packages."""
    records = list(_iter_source_imports())
    bible_violations = [r for r in records if r.source_package == "bible" and r.target_package in {"claire", "claire_bible", "<relative-escape>"}]
    claire_violations = [r for r in records if r.source_package == "claire" and r.target_package in {"bible", "claire_bible", "<relative-escape>"}]

    assert bible_violations == [], f"Forbidden imports in src/bible: {bible_violations}"
    assert claire_violations == [], f"Forbidden imports in src/claire: {claire_violations}"

    edges = {(r.source_package, r.target_package) for r in records if r.target_package in _MANAGED_PACKAGES and r.target_package != r.source_package}
    assert edges <= {("claire_bible", "claire"), ("claire_bible", "bible")}

    # Check acyclic topological order across managed packages.
    indegree = {pkg: 0 for pkg in _MANAGED_PACKAGES}
    adjacency: dict[str, list[str]] = {pkg: [] for pkg in _MANAGED_PACKAGES}
    for src_pkg, dst_pkg in edges:
        adjacency[src_pkg].append(dst_pkg)
        indegree[dst_pkg] += 1
    queue = [pkg for pkg, deg in indegree.items() if deg == 0]
    visited: list[str] = []
    while queue:
        curr = queue.pop(0)
        visited.append(curr)
        for nxt in adjacency[curr]:
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                queue.append(nxt)
    assert len(visited) == len(_MANAGED_PACKAGES)


def test_legacy_coupled_claire_modules_are_removed() -> None:
    """Verify legacy render and chrome paths no longer exist in src/claire."""
    removed_paths = [
        _SRC_ROOT / "claire" / "render",
        _SRC_ROOT / "claire" / "templates",
        _SRC_ROOT / "claire" / "static",
        _SRC_ROOT / "claire" / "graphview.py",
        _SRC_ROOT / "claire" / "ui_chrome.py",
    ]
    for path in removed_paths:
        assert not path.exists(), f"Removed legacy path still exists: {path}"


def test_ports_fail_closed_before_bootstrap_and_restore_via_configure() -> None:
    """Verify getters fail when ports are reset and succeed after bootstrap configure."""
    try:
        reset_ports()
        assert ports_configured() is False
        with pytest.raises(RendererNotConfiguredError):
            get_document_renderer()
        with pytest.raises(RendererNotConfiguredError):
            get_slide_renderer()
        with pytest.raises(RendererNotConfiguredError):
            get_web_ui_renderer()

        claire_bible.bootstrap.configure()
        assert ports_configured() is True
        assert get_document_renderer().renderer_revision >= 1
        assert get_slide_renderer().render_unready_presentation_page(title="Deck", status="pending")
        assert get_web_ui_renderer().static_dir.is_dir()
    finally:
        claire_bible.bootstrap.configure(force=True)


def test_plugin_importcheck_blocks_bible_and_claire_bible() -> None:
    """Verify plugin import checker marks bible, claire, and claire_bible as core_import."""
    source = "\n".join(
        [
            "import bible",
            "from bible.adoc import aot",
            "import claire_bible",
            "from claire_bible import bootstrap",
            "import claire",
        ]
    )
    findings = check_source(source, "plugin.py")
    assert len(findings) == 5
    assert all(f.kind == "core_import" for f in findings)


@dataclass
class _E2ESettings:
    db_file: Path
    data_dir: Path
    environment: str = "development"
    public_url: str = "http://127.0.0.1:8765"
    inject_token: str = "owner-" + "o" * 32
    readonly_token: str = "readonly-" + "r" * 32
    cors_allowed_origins: str = ""
    anonymous_readonly: bool = True
    ui_lang: str = "ko"


def test_end_to_end_bootstrap_store_render_and_web_app(tmp_path: Path) -> None:
    """Verify full stack from bootstrap through store persistence and web HTTP routes."""
    claire_bible.bootstrap.configure(force=True)
    db_path = tmp_path / "arch_e2e.db"
    conn = dbm.connect(db_path)
    try:
        dbm.init_db(conn)
        accounts_store.AccountStore(conn).complete_setup(1, username="arch_admin", display_name="Admin")
        doc = Document(id="arch-doc-1", title="Architecture Verification", url="https://example.com/arch", raw_text="Raw body")
        dbm.insert_document(conn, doc)
        dbm.set_document_detail(conn, doc.id, "[NOTE]\n====\nArchitecture boundary check\n====", format="adoc")
        rendered_html = dbm.get_document_detail_html(conn, doc.id)
        assert rendered_html is not None
        assert "admonitionblock" in rendered_html
        assert "Architecture boundary check" in rendered_html
    finally:
        conn.close()

    settings = _E2ESettings(db_file=db_path, data_dir=tmp_path)
    app = claire_bible.web.app.create_app(settings, SimpleNamespace(provider=SimpleNamespace(name="stub")))
    with TestClient(app, base_url=settings.public_url) as client:
        root_res = client.get("/", follow_redirects=True)
        assert root_res.status_code == 200
        assert "Claire Bible" in root_res.text or "claire" in root_res.text.lower()

        static_js_res = client.get("/static/js/i18n.js")
        assert static_js_res.status_code == 200
        assert static_js_res.text.strip() != ""

        locale_res = client.get("/static/locales/ko.json")
        assert locale_res.status_code == 200
        assert isinstance(locale_res.json(), dict)
