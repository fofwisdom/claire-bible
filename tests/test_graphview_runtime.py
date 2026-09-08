"""Graphview runtime & JavaScript validation tests.

These tests verify:
1. Pure static JavaScript files (adoc_parser.js, reader.js, app.js) have valid syntax via `node --check`.
2. Standalone bundled HTML pages (GRAPH_HTML, _SHARED_HTML) have valid JavaScript syntax.
3. HTML templates and bundles maintain matching structural tags (<head>, <body>, <style>).

Legacy fake DOM simulation tests (MockElement) have been retired in favor of
real browser testing via Playwright E2E (e2e/workspace.spec.js).
"""

from __future__ import annotations

from pathlib import Path
import re
import shutil
import subprocess
import tempfile

import pytest

from claire.graphview import _SHARED_HTML, GRAPH_HTML

_PACKAGE_DIR = Path(__file__).resolve().parent.parent / "src" / "claire"
_STATIC_JS_DIR = _PACKAGE_DIR / "static" / "js"
_TEMPLATES_DIR = _PACKAGE_DIR / "templates"


@pytest.fixture(scope="module")
def node_available() -> bool:
    if shutil.which("node") is not None:
        return True
    import os
    nvm_node = Path(os.path.expanduser("~/.nvm/versions/node/v26.7.0/bin/node"))
    if nvm_node.is_file():
        os.environ["PATH"] = f"{nvm_node.parent}:{os.environ.get('PATH', '')}"
        return True
    return False


def extract_scripts(html: str) -> list[str]:
    # Extract only non-JSON scripts
    matches = re.findall(
        r"<script(?![^>]*type=['\"]application/json['\"])[^>]*>(.*?)</script>",
        html,
        re.DOTALL,
    )
    return [m.strip() for m in matches if m.strip()]


def test_static_js_files_syntax(node_available: bool) -> None:
    """Verify that all modular frontend JavaScript source files have valid syntax."""
    if not node_available:
        pytest.skip("Node.js is not installed on the system")

    js_files = [
        _STATIC_JS_DIR / "renderers" / "adoc_parser.js",
        _STATIC_JS_DIR / "reader.js",
        _STATIC_JS_DIR / "app.js",
    ]

    for js_path in js_files:
        assert js_path.is_file(), f"Expected static JS file not found: {js_path}"
        res = subprocess.run(
            ["node", "--check", str(js_path)],
            capture_output=True,
            text=True,
        )
        assert res.returncode == 0, f"JS Syntax error in {js_path.name}:\n{res.stderr}"


def test_graphview_bundle_js_syntax(node_available: bool) -> None:
    """Verify that standalone GRAPH_HTML bundle contains valid JavaScript."""
    if not node_available:
        pytest.skip("Node.js is not installed on the system")

    scripts = extract_scripts(GRAPH_HTML)
    assert len(scripts) >= 1, "GRAPH_HTML should contain at least one <script> block"

    for i, script in enumerate(scripts):
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
            f.write(script)
            tmp_path = f.name
        try:
            res = subprocess.run(
                ["node", "--check", tmp_path],
                capture_output=True,
                text=True,
            )
            assert res.returncode == 0, f"JS Syntax error in GRAPH_HTML script #{i + 1}:\n{res.stderr}"
        finally:
            Path(tmp_path).unlink(missing_ok=True)


def test_shared_html_bundle_js_syntax(node_available: bool) -> None:
    """Verify that standalone _SHARED_HTML bundle contains valid JavaScript."""
    if not node_available:
        pytest.skip("Node.js is not installed on the system")

    scripts = extract_scripts(_SHARED_HTML)
    assert len(scripts) >= 1, "_SHARED_HTML should contain at least one <script> block"

    for i, script in enumerate(scripts):
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
            f.write(script)
            tmp_path = f.name
        try:
            res = subprocess.run(
                ["node", "--check", tmp_path],
                capture_output=True,
                text=True,
            )
            assert res.returncode == 0, f"JS Syntax error in _SHARED_HTML script #{i + 1}:\n{res.stderr}"
        finally:
            Path(tmp_path).unlink(missing_ok=True)


def test_html_tag_structure_integrity():
    """Verify that templates and standalone bundles have well-formed, matching structural tags."""
    index_tmpl = (_TEMPLATES_DIR / "index.html").read_text(encoding="utf-8")
    share_tmpl = (_TEMPLATES_DIR / "share.html").read_text(encoding="utf-8")

    targets = [
        ("index.html template", index_tmpl),
        ("share.html template", share_tmpl),
        ("GRAPH_HTML bundle", GRAPH_HTML),
        ("_SHARED_HTML bundle", _SHARED_HTML),
    ]

    for name, html in targets:
        assert "<head>" in html or "<head " in html or "<head>" in html.lower(), f"{name}: missing <head>"
        assert "</head>" in html, f"{name}: missing </head>"
        assert "<body" in html, f"{name}: missing <body>"
        assert "</body>" in html, f"{name}: missing </body>"

        assert len(re.findall(r"<head(?:\s+[^>]*)?>", html, re.IGNORECASE)) == html.count("</head>"), (
            f"{name}: unmatched <head> tags"
        )
        assert len(re.findall(r"<body(?:\s+[^>]*)?>", html, re.IGNORECASE)) == html.count("</body>"), (
            f"{name}: unmatched <body> tags"
        )

    for name, html in [("GRAPH_HTML bundle", GRAPH_HTML), ("_SHARED_HTML bundle", _SHARED_HTML)]:
        assert "<style>" in html
        assert "</style>" in html
        assert html.count("<style>") == html.count("</style>"), f"{name}: unmatched <style> tags"
        assert "</style></head>" in html or ("</style>" in html and "</head>" in html), (
            f"{name}: missing </style> or </head>"
        )
        assert '<div class="wrap" id="wrap"></div>' in html or '<div id="wrap">' in html
