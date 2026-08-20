"""Graphview runtime & JavaScript validation tests.

These tests execute the real client-side JavaScript in Node.js / simulated DOM
to catch syntax errors, TDZ (Temporal Dead Zone) ReferenceErrors, missing DOM elements,
and ensure loading states ('권한 확인 중', '문서 로딩…', '로딩…') always transition to rendered content.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from claire.graphview import GRAPH_HTML, _SHARED_HTML


@pytest.fixture(scope="module")
def node_available() -> bool:
    return shutil.which("node") is not None


def extract_scripts(html: str) -> list[str]:
    # Extract only non-JSON scripts
    matches = re.findall(r"<script(?![^>]*type=['\"]application/json['\"])[^>]*>(.*?)</script>", html, re.DOTALL)
    return [m.strip() for m in matches if m.strip()]


def test_graphview_js_syntax(node_available: bool) -> None:
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


def test_shared_html_js_syntax(node_available: bool) -> None:
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


def test_graphview_headless_dom_runtime(node_available: bool) -> None:
    """Simulates page execution in Node.js with mock DOM and network endpoints.

    Verifies:
    1. Zero uncaught exceptions during page startup.
    2. #authstate transitions away from '⏳ 권한 확인 중'.
    3. #doclist transitions away from '문서 로딩…' and contains rendered documents.
    4. #stat transitions away from '로딩…'.
    5. #reader loads and renders document title and summary.
    """
    if not node_available:
        pytest.skip("Node.js is not installed on the system")

    scripts = extract_scripts(GRAPH_HTML)
    assert len(scripts) >= 1

    runner_code = r"""
const fs = require('fs');

// Minimal simulated DOM environment
class MockElement {
  constructor(tag, id = '') {
    this.tagName = (tag || 'div').toUpperCase();
    this.id = id;
    this.className = '';
    this.classList = {
      _classes: new Set(),
      add(...cls) { cls.forEach(c => this._classes.add(c)); },
      remove(...cls) { cls.forEach(c => this._classes.delete(c)); },
      contains(c) { return this._classes.has(c); },
      toggle(c, force) {
        if (force === undefined) {
          if (this._classes.has(c)) this._classes.delete(c);
          else this._classes.add(c);
        } else if (force) this._classes.add(c);
        else this._classes.delete(c);
      }
    };
    this.style = {
      _props: {},
      setProperty(k, v) { this._props[k] = v; },
      getPropertyValue(k) { return this._props[k] || ''; }
    };
    this.dataset = {};
    this.attributes = {};
    this._innerHTML = '';
    this._textContent = '';
    this.value = '';
    this.children = [];
  }
  get innerHTML() { return this._innerHTML; }
  set innerHTML(v) { this._innerHTML = String(v); this._textContent = String(v).replace(/<[^>]*>/g, ''); }
  get textContent() { return this._textContent; }
  set textContent(v) { this._textContent = String(v); this._innerHTML = String(v); }
  setAttribute(k, v) { this.attributes[k] = String(v); if(k==='id') this.id=String(v); }
  getAttribute(k) { return this.attributes[k] !== undefined ? this.attributes[k] : null; }
  removeAttribute(k) { delete this.attributes[k]; }
  getBoundingClientRect() { return { width: 1000, height: 700, top: 0, left: 0, right: 1000, bottom: 700 }; }
  querySelector(sel) { return new MockElement('div'); }
  querySelectorAll(sel) { return []; }
  addEventListener() {}
  removeEventListener() {}
  focus() {}
  select() {}
}

const elements = new Map();
function getOrCreate(id, tag='div') {
  if (!elements.has(id)) {
    elements.set(id, new MockElement(tag, id));
  }
  return elements.get(id);
}

// Pre-create known HTML elements
const knownIds = [
  'wrap', 'centerwrap', 'netwrap', 'net', 'reader', 'rtitle', 'rbody', 'rfs', 'sharebox',
  'docs', 'docq', 'desclines', 'pinnedhead', 'pinnedlist', 'doclist', 'showhidden', 'hiddenlist',
  'detailpane', 'panel', 'fslider', 'fmin', 'bar', 'worktabs', 'tab-docs', 'tab-graph', 'tab-search', 'tab-menu',
  'morebtn', 'nodepop', 'stat', 'authstate', 'themebtn', 'sem', 'searchkind', 'synthchips', 'synthbtn',
  'addbtn', 'dedupbtn', 'pathbtn', 'repolink', 'format-warn-banner', 'format-warn-text', 'graphnotice',
  'graphdocnav', 'graphdocpick', 'graphdoclabel', 'graphdocprev', 'graphdocnext', 'graphdocmenu', 'graphdocq', 'graphdoclist', 'graphdocempty'
];
knownIds.forEach(id => getOrCreate(id));

const document = {
  documentElement: getOrCreate('html', 'html'),
  body: getOrCreate('body', 'body'),
  getElementById(id) { return elements.get(id) || null; },
  querySelector(sel) {
    if (sel.startsWith('#')) return document.getElementById(sel.slice(1));
    return new MockElement('div');
  },
  querySelectorAll(sel) { return []; },
  addEventListener() {},
  removeEventListener() {},
  activeElement: null
};

const window = {
  document,
  matchMedia(query) {
    return {
      matches: false,
      media: query,
      addEventListener() {},
      removeEventListener() {}
    };
  },
  addEventListener() {},
  removeEventListener() {},
  setTimeout: global.setTimeout,
  clearTimeout: global.clearTimeout,
  setInterval: global.setInterval,
  clearInterval: global.clearInterval,
  requestAnimationFrame(fn) { return global.setTimeout(fn, 0); },
  DOMPurify: { sanitize(html) { return html; } },
  marked: { parse(src) { return '<p>' + src + '</p>'; } },
  vis: {
    DataSet: class {
      constructor(data) { this._data = data || []; }
      get(id) { return this._data.find(d => d.id === id); }
      getIds() { return this._data.map(d => d.id); }
      update() {}
      add() {}
      forEach(fn) { this._data.forEach(fn); }
    },
    Network: class {
      constructor() {}
      setSize() {}
      redraw() {}
      fit() {}
      focus() {}
      moveTo() {}
      on() {}
      selectNodes() {}
      unselectAll() {}
      getSelectedNodes() { return []; }
      getScale() { return 1.0; }
      getViewPosition() { return { x: 0, y: 0 }; }
      getPositions() { return {}; }
      canvasToDOM(p) { return p; }
      getPosition() { return { x: 0, y: 0 }; }
      setOptions() {}
    }
  },
  localStorage: {
    _store: {},
    getItem(k) { return this._store[k] || null; },
    setItem(k, v) { this._store[k] = String(v); }
  },
  location: { origin: 'http://127.0.0.1:8766' }
};

// Mock fetch
async function fetch(url, opts) {
  if (url === 'whoami') {
    return { ok: true, status: 200, json: async () => ({ scope: 'owner' }) };
  }
  if (url === 'documents') {
    return {
      ok: true,
      status: 200,
      json: async () => ({
        documents: [
          { id: 'doc-101', title: '클레어 바이블 문서 1', summary: '첫 번째 요약', seen: 0, pinned: 0, hidden: 0, fetched_at: 1724100000 }
        ],
        format_status: { needs_migration: false }
      })
    };
  }
  if (url === 'graph') {
    return {
      ok: true,
      status: 200,
      json: async () => ({
        nodes: [{ id: 'n1', label: '엔티티1', group: 'Concept', degree: 2 }],
        edges: [{ from: 'n1', to: 'n2', label: '관련' }],
        stats: { max_degree: 2 }
      })
    };
  }
  if (url.startsWith('document?id=')) {
    return {
      ok: true,
      status: 200,
      json: async () => ({
        id: 'doc-101',
        title: '클레어 바이블 문서 1',
        summary: '첫 번째 요약',
        detail: '본문 내용 상세'
      })
    };
  }
  return { ok: true, status: 200, json: async () => ({}) };
}

global.window = window;
global.document = document;
global.fetch = fetch;
global.vis = window.vis;
global.DOMPurify = window.DOMPurify;
global.marked = window.marked;
global.localStorage = window.localStorage;
global.location = window.location;
global.getComputedStyle = () => ({ getPropertyValue: () => '#ffffff' });

// Read and execute target script
const scriptContent = fs.readFileSync(process.argv[2], 'utf8');
try {
  eval(scriptContent);
} catch (err) {
  console.error("FATAL_EVAL_ERROR:", err.stack || err);
  process.exit(1);
}

// Wait for async promises to resolve
setTimeout(() => {
  const result = {
    authstate: document.getElementById('authstate').textContent,
    doclist: document.getElementById('doclist').innerHTML,
    stat: document.getElementById('stat').textContent,
    rtitle: document.getElementById('rtitle').textContent,
    bodyCenterView: document.body.dataset.centerView,
    activePane: document.body.dataset.activePane
  };
  console.log("EXEC_RESULT:" + JSON.stringify(result));
  process.exit(0);
}, 150);
""";

    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f_script:
        f_script.write(scripts[0])
        script_file = f_script.name

    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f_runner:
        f_runner.write(runner_code)
        runner_file = f_runner.name

    try:
        proc = subprocess.run(
            ["node", runner_file, script_file],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert proc.returncode == 0, f"Node.js headless execution crashed:\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"

        # Parse EXEC_RESULT
        match = re.search(r"EXEC_RESULT:(.*)", proc.stdout)
        assert match is not None, f"Execution output did not contain EXEC_RESULT:\n{proc.stdout}"

        data = json.loads(match.group(1))

        # 1. Authstate must transition away from '⏳ 권한 확인 중'
        assert data["authstate"] != "⏳ 권한 확인 중", f"Authstate remained frozen: {data['authstate']}"
        assert "인증됨" in data["authstate"] or "읽기전용" in data["authstate"], f"Unexpected authstate: {data['authstate']}"

        # 2. Doclist must contain loaded document and not '문서 로딩…'
        assert "문서 로딩…" not in data["doclist"], f"Doclist remained frozen at loading state: {data['doclist']}"
        assert "클레어 바이블 문서 1" in data["doclist"], f"Document title was not rendered in doclist: {data['doclist']}"

        # 3. Stat must transition away from '로딩…'
        assert data["stat"] != "로딩…", f"Stat remained frozen at loading state: {data['stat']}"

        # 4. Reader must have loaded first document on desktop view
        assert "클레어 바이블 문서 1" in data["rtitle"], f"First document was not loaded in reader: {data['rtitle']}"

    finally:
        Path(script_file).unlink(missing_ok=True)
        Path(runner_file).unlink(missing_ok=True)
