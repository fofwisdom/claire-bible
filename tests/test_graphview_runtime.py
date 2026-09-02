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

from claire.graphview import _SHARED_HTML, GRAPH_HTML, shared_html


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


def test_shared_html_headless_dom_runtime(node_available: bool) -> None:
    """Simulates page execution of shared_html in Node.js with mock DOM.

    Verifies:
    1. Zero uncaught exceptions during page startup.
    2. wrap.innerHTML contains docmeta element with .docmeta, .docmeta-tags,
       truncation tag, directive tag, STT tag, and rmeta.
    """
    if not node_available:
        pytest.skip("Node.js is not installed on the system")

    doc = {
        "id": "doc_shared_runtime",
        "title": "런타임 공유 문서",
        "url": "https://example.com/runtime-doc",
        "source_type": "web",
        "raw_truncated": True,
        "appendix_truncated": True,
        "orig_chars": 30000,
        "raw_chars": 20000,
        "directive": "시스템 아키텍처",
        "is_stt": True,
        "summary": "간단 요약",
        "detail": "상세 본문",
    }
    rendered_html = shared_html(doc)
    scripts = extract_scripts(rendered_html)
    assert len(scripts) >= 1

    script_content = scripts[-1]

    runner_code = r"""
const docData = """ + json.dumps(doc) + r""";

class MockElement {
  constructor(tag, id = '') {
    this.tagName = (tag || 'div').toUpperCase();
    this.id = id;
    this.className = '';
    this.innerHTML = '';
    this.textContent = '';
  }
  setAttribute(k, v) {}
  getAttribute(k) { return null; }
  querySelectorAll() { return []; }
}

const elements = new Map();
const docDataEl = new MockElement('script', 'docdata');
docDataEl.textContent = JSON.stringify(docData);
elements.set('docdata', docDataEl);

const wrapEl = new MockElement('div', 'wrap');
elements.set('wrap', wrapEl);

global.document = {
  getElementById(id) { return elements.get(id) || new MockElement('div', id); },
  title: '',
};
global.window = {
  location: { origin: 'http://localhost:8766', pathname: '/p' },
};

""" + script_content + r"""

console.log('WRAP_HTML_START' + wrapEl.innerHTML + 'WRAP_HTML_END');
"""
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
        f.write(runner_code)
        tmp_path = f.name
    try:
        res = subprocess.run(["node", tmp_path], capture_output=True, text=True)
        assert res.returncode == 0, f"Error executing shared_html script in Node.js:\n{res.stderr}"
        output = res.stdout
        assert "WRAP_HTML_START" in output
        html_body = output.split("WRAP_HTML_START")[1].split("WRAP_HTML_END")[0]
        assert "class=docmeta" in html_body or 'class="docmeta"' in html_body
        assert "docmeta-tags" in html_body
        assert "directive-tag" in html_body
        assert "stt-tag" in html_body
        assert "trunc-tag" in html_body
        assert "✂️ 원문 일부 절단" in html_body
        assert "🎯" in html_body
        assert "🎙️ STT" in html_body
        assert "rmeta" in html_body
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
  'detailpane', 'detailtogglebtn', 'panel', 'degctl', 'fslider', 'fmin', 'bar', 'worktabs', 'tab-docs', 'tab-graph', 'tab-search', 'tab-menu',
  'morebtn', 'nodepop', 'stat', 'authstate', 'themebtn', 'sem', 'searchkind', 'synthchips', 'synthbtn',
  'advsearchbtn', 'advsearchpane', 'fts-opt-wrap', 'semchk', 'semkind', 'sembadge', 'semantic-opt-wrap',
  'addbtn', 'dedupbtn', 'pathbtn', 'graph-section', 'repolink', 'format-warn-banner', 'format-warn-text', 'format-warn-badge', 'format-warn-icon', 'format-warn-title', 'format-warn-actbtn', 'graphnotice',
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
global.requestAnimationFrame = window.requestAnimationFrame;

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
  const initialResult = {
    authstate: document.getElementById('authstate').textContent,
    doclist: document.getElementById('doclist').innerHTML,
    stat: document.getElementById('stat').textContent,
    bodyCenterView: document.body.dataset.centerView,
    activePane: document.body.dataset.activePane
  };

  selectDoc('doc-101');

  setTimeout(() => {
    const result = {
      ...initialResult,
      rtitle: document.getElementById('rtitle').textContent,
      panel: document.getElementById('panel').innerHTML,
      selectedCenterView: document.body.dataset.centerView
    };
    console.log("EXEC_RESULT:" + JSON.stringify(result));
    process.exit(0);
  }, 50);
}, 150);
""";

    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f_script:
        f_script.write("\n".join(scripts))
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

        # 4. Initial view is full graph
        assert data["bodyCenterView"] == "graph"
        assert data["activePane"] == "graph"

        # 5. After selecting document, reader and panel are loaded
        assert data["selectedCenterView"] == "reader"
        assert "클레어 바이블 문서 1" in data["rtitle"], f"Selected document was not loaded in reader: {data['rtitle']}"
        assert "클레어 바이블 문서 1" in data["panel"], f"Panel was not updated with document title: {data['panel']}"
        assert "첫 번째 요약" in data["panel"], f"Panel was not updated with document summary: {data['panel']}"
        assert "이 문서의 지식 노드" in data["panel"], f"Panel was not updated with document nodes section: {data['panel']}"

    finally:
        Path(script_file).unlink(missing_ok=True)
        Path(runner_file).unlink(missing_ok=True)


def test_graphview_network_error_graceful_recovery(node_available: bool) -> None:
    """Verifies that when backend endpoints return HTTP 500 / fail,

    the frontend handles them gracefully without throwing unhandled exceptions
    and transitions all loading states to failure/empty states.
    """
    if not node_available:
        pytest.skip("Node.js is not installed on the system")

    scripts = extract_scripts(GRAPH_HTML)
    assert len(scripts) >= 1

    runner_code = r"""
const fs = require('fs');

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

const knownIds = [
  'wrap', 'centerwrap', 'netwrap', 'net', 'reader', 'rtitle', 'rbody', 'rfs', 'sharebox',
  'docs', 'docq', 'desclines', 'pinnedhead', 'pinnedlist', 'doclist', 'showhidden', 'hiddenlist',
  'detailpane', 'detailtogglebtn', 'panel', 'degctl', 'fslider', 'fmin', 'bar', 'worktabs', 'tab-docs', 'tab-graph', 'tab-search', 'tab-menu',
  'morebtn', 'nodepop', 'stat', 'authstate', 'themebtn', 'sem', 'searchkind', 'synthchips', 'synthbtn',
  'advsearchbtn', 'advsearchpane', 'fts-opt-wrap', 'semchk', 'semkind', 'sembadge', 'semantic-opt-wrap',
  'addbtn', 'dedupbtn', 'pathbtn', 'graph-section', 'repolink', 'format-warn-banner', 'format-warn-text', 'format-warn-badge', 'format-warn-icon', 'format-warn-title', 'format-warn-actbtn', 'graphnotice',
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

// Simulate all endpoints failing with 500 error
async function fetch(url, opts) {
  return { ok: false, status: 500, json: async () => ({ error: 'Internal Server Error' }) };
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
global.requestAnimationFrame = window.requestAnimationFrame;

const scriptContent = fs.readFileSync(process.argv[2], 'utf8');
try {
  eval(scriptContent);
} catch (err) {
  console.error("FATAL_EVAL_ERROR:", err.stack || err);
  process.exit(1);
}

setTimeout(() => {
  const result = {
    authstate: document.getElementById('authstate').textContent,
    doclist: document.getElementById('doclist').innerHTML,
    stat: document.getElementById('stat').textContent
  };
  console.log("EXEC_RESULT:" + JSON.stringify(result));
  process.exit(0);
}, 150);
""";

    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f_script:
        f_script.write("\n".join(scripts))
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
        assert proc.returncode == 0, f"Node.js execution crashed on network error:\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"

        match = re.search(r"EXEC_RESULT:(.*)", proc.stdout)
        assert match is not None, f"Execution output did not contain EXEC_RESULT:\n{proc.stdout}"

        data = json.loads(match.group(1))

        # None of the elements should remain in their frozen initial loading string
        assert data["authstate"] != "⏳ 권한 확인 중", f"Authstate remained frozen on error: {data['authstate']}"
        assert data["doclist"] != "문서 로딩…", f"Doclist remained frozen on error: {data['doclist']}"
        assert data["stat"] != "로딩…", f"Stat remained frozen on error: {data['stat']}"

    finally:
        Path(script_file).unlink(missing_ok=True)
        Path(runner_file).unlink(missing_ok=True)


def test_graphview_watchdog_clears_loading_placeholders(node_available: bool) -> None:
    """Verifies that the head watchdog script forcibly clears loading placeholders.

    Even if main execution is blocked or an unhandled exception occurs, the watchdog
    guarantees that '권한 확인 중', '문서 로딩…', and '로딩…' are cleared.
    """
    if not node_available:
        pytest.skip("Node.js is not installed on the system")

    scripts = extract_scripts(GRAPH_HTML)
    assert len(scripts) >= 2  # Script #0 is head watchdog, Script #1 is main body

    head_script = scripts[0]

    runner_code = r"""
const fs = require('fs');

class MockElement {
  constructor(id, initialContent) {
    this.id = id;
    this._innerHTML = initialContent;
    this._textContent = initialContent;
  }
  get innerHTML() { return this._innerHTML; }
  set innerHTML(v) { this._innerHTML = String(v); this._textContent = String(v).replace(/<[^>]*>/g, ''); }
  get textContent() { return this._textContent; }
  set textContent(v) { this._textContent = String(v); this._innerHTML = String(v); }
}

const elements = new Map([
  ['authstate', new MockElement('authstate', '⏳ 권한 확인 중')],
  ['doclist', new MockElement('doclist', '<p class="hint" style="padding:10px">문서 로딩…</p>')],
  ['stat', new MockElement('stat', '로딩…')],
  ['searchkind', new MockElement('searchkind', '검색 모드 확인 중')]
]);

const document = {
  documentElement: { setAttribute() {} },
  getElementById(id) { return elements.get(id) || null; }
};

const window = {
  document,
  addEventListener() {},
  localStorage: { getItem() { return 'light'; }, setItem() {} },
  setTimeout(fn, delay) { fn(); } // Execute immediately for testing
};

global.window = window;
global.document = document;

const headScript = fs.readFileSync(process.argv[2], 'utf8');
eval(headScript);

// Trigger clear loading
window.__CLAIRE_CLEAR_LOADING('테스트');

const result = {
  authstate: document.getElementById('authstate').textContent,
  doclist: document.getElementById('doclist').innerHTML,
  stat: document.getElementById('stat').textContent,
  searchkind: document.getElementById('searchkind').textContent
};
console.log("WATCHDOG_RESULT:" + JSON.stringify(result));
""";

    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f_script:
        f_script.write(head_script)
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
        assert proc.returncode == 0, f"Watchdog runner crashed:\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"

        match = re.search(r"WATCHDOG_RESULT:(.*)", proc.stdout)
        assert match is not None, f"Execution output did not contain WATCHDOG_RESULT:\n{proc.stdout}"

        data = json.loads(match.group(1))
        assert "권한 확인 중" not in data["authstate"], f"Authstate was not cleared: {data['authstate']}"
        assert "문서 로딩…" not in data["doclist"], f"Doclist was not cleared: {data['doclist']}"
        assert "로딩…" not in data["stat"], f"Stat was not cleared: {data['stat']}"
        assert "확인 중" not in data["searchkind"], f"Searchkind was not cleared: {data['searchkind']}"
    finally:
        Path(script_file).unlink(missing_ok=True)
        Path(runner_file).unlink(missing_ok=True)


def test_claire_status_banner_runtime(node_available: bool) -> None:
    """GRAPH_HTML 내의 ClaireStatusBanner 상태 관리 기능이 정상 동작하는지 headless Node.js 런타임으로 검증."""
    if not node_available:
        pytest.skip("Node.js is not installed on the system")

    from claire.graphview import GRAPH_HTML

    scripts = re.findall(r"<script(?:\s+type=[\"']module[\"'])?>(.*?)</script>", GRAPH_HTML, re.DOTALL)
    assert len(scripts) >= 2, "Expected at least 2 script tags in GRAPH_HTML"
    main_script = scripts[-1]

    runner_code = r"""
const fs = require('fs');

class MockElement {
  constructor(tagName, id) {
    this.tagName = tagName.toUpperCase();
    this.id = id || '';
    this.className = '';
    this.classList = {
      _classes: new Set(),
      add: (c) => this.classList._classes.add(c),
      remove: (c) => this.classList._classes.delete(c),
      contains: (c) => this.classList._classes.has(c),
      toggle: (c, force) => {
        if (force === true) { this.classList._classes.add(c); return true; }
        if (force === false) { this.classList._classes.delete(c); return false; }
        if (this.classList._classes.has(c)) { this.classList._classes.delete(c); return false; }
        this.classList._classes.add(c); return true;
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
    this.disabled = false;
  }
  get innerHTML() { return this._innerHTML; }
  set innerHTML(v) { this._innerHTML = String(v); this._textContent = String(v).replace(/<[^>]*>/g, ''); }
  get textContent() { return this._textContent; }
  set textContent(v) { this._textContent = String(v); this._innerHTML = String(v); }
  setAttribute(k, v) { this.attributes[k] = String(v); }
  getAttribute(k) { return this.attributes[k] || null; }
  removeAttribute(k) { delete this.attributes[k]; }
  getBoundingClientRect() { return { width: 1000, height: 700, top: 0, left: 0, right: 1000, bottom: 700 }; }
  querySelector(sel) { return new MockElement('div'); }
  querySelectorAll(sel) { return []; }
  addEventListener() {}
  removeEventListener() {}
  focus() {}
  blur() {}
  click() {}
}

const elements = new Map();
function getOrCreate(id, tag='div') {
  if (!elements.has(id)) {
    elements.set(id, new MockElement(tag, id));
  }
  return elements.get(id);
}

const knownIds = [
  'wrap', 'centerwrap', 'netwrap', 'net', 'reader', 'rtitle', 'rbody', 'rfs', 'sharebox',
  'docs', 'docq', 'desclines', 'pinnedhead', 'pinnedlist', 'doclist', 'showhidden', 'hiddenlist',
  'detailpane', 'detailtogglebtn', 'panel', 'degctl', 'fslider', 'fmin', 'bar', 'worktabs', 'tab-docs', 'tab-graph', 'tab-search', 'tab-menu',
  'morebtn', 'nodepop', 'stat', 'authstate', 'themebtn', 'sem', 'searchkind', 'synthchips', 'synthbtn',
  'advsearchbtn', 'advsearchpane', 'fts-opt-wrap', 'semchk', 'semkind', 'sembadge', 'semantic-opt-wrap',
  'addbtn', 'dedupbtn', 'pathbtn', 'graph-section', 'repolink', 'format-warn-banner', 'format-warn-text', 'format-warn-badge',
  'format-warn-icon', 'format-warn-title', 'format-warn-actbtn', 'graphnotice',
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

let clipboardText = '';
const navigator = {
  clipboard: {
    writeText: async (t) => { clipboardText = t; }
  }
};

const window = {
  document,
  navigator,
  addEventListener() {},
  removeEventListener() {},
  setTimeout: global.setTimeout,
  clearTimeout: global.clearTimeout,
  setInterval: global.setInterval,
  clearInterval: global.clearInterval,
  requestAnimationFrame(fn) { return global.setTimeout(fn, 0); },
  DOMPurify: { sanitize(html) { return html; } },
  marked: { parse(src) { return '<p>' + src + '</p>'; } },
  matchMedia(query) {
    return { matches: false, media: query, addEventListener() {}, removeEventListener() {} };
  },
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
  location: { origin: 'http://127.0.0.1:8766', reload() {} }
};

async function fetch(url, opts) {
  if (url === 'whoami') return { ok: true, status: 200, json: async () => ({ scope: 'owner' }) };
  if (url === 'documents') return { ok: true, status: 200, json: async () => ({ documents: [], format_status: { needs_migration: false } }) };
  return { ok: true, status: 200, json: async () => ({}) };
}

global.window = window;
global.document = document;
global.navigator = navigator;
global.fetch = fetch;
global.requestAnimationFrame = window.requestAnimationFrame;

const code = fs.readFileSync(process.argv[2], 'utf8');
eval(code);

const banner = window.claireDebug.statusBanner;
const tests = [];

// 1. Preset keys check
const presetKeys = Object.keys(banner.presets);
tests.push({ name: 'has_presets', ok: presetKeys.includes('format_mismatch') && presetKeys.includes('format_missing') && presetKeys.includes('format_ok') });

// 2. format_mismatch show
banner.show('format_mismatch', { configured: 'adoc', mismatched: 3 });
const bannerEl = document.getElementById('format-warn-banner');
const textEl = document.getElementById('format-warn-text');
const titleEl = document.getElementById('format-warn-title');
const actBtn = document.getElementById('format-warn-actbtn');

tests.push({
  name: 'format_mismatch_render',
  displayed: bannerEl.style.display === 'flex',
  isWarning: bannerEl.className.includes('banner-warning'),
  title: titleEl.textContent === '렌더링 포맷 불일치',
  hasText: textEl.innerHTML.includes('3개') && textEl.innerHTML.includes('MD'),
  noActionBtn: actBtn.style.display === 'none'
});

// 4. format_missing show
banner.show('format_missing', { configured: 'adoc', missing_detail_docs: 5 });
tests.push({
  name: 'format_missing_render',
  isInfo: bannerEl.className.includes('banner-info'),
  title: titleEl.textContent === '가독 렌더링 미생성',
  hasText: textEl.innerHTML.includes('5개')
});

// 5. format_ok show
banner.show('format_ok', { configured: 'adoc', total_docs: 42 });
tests.push({
  name: 'format_ok_render',
  isSuccess: bannerEl.className.includes('banner-success'),
  hasText: textEl.innerHTML.includes('42건')
});

// 6. readonly_mode show
banner.show('readonly_mode');
tests.push({
  name: 'readonly_mode_render',
  title: titleEl.textContent === '읽기 전용 모드'
});

// 7. hide
banner.hide();
tests.push({
  name: 'hide_banner',
  hidden: bannerEl.style.display === 'none',
  statusNull: window.claireDebug.activeBannerStatus.status === null
});

console.log("BANNER_TEST_RESULT:" + JSON.stringify(tests));
process.exit(0);
"""

    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f_script:
        f_script.write(main_script)
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
        assert proc.returncode == 0, f"Status banner runner crashed:\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
        match = re.search(r"BANNER_TEST_RESULT:(.*)", proc.stdout)
        assert match is not None, f"Execution output did not contain BANNER_TEST_RESULT:\n{proc.stdout}"

        results = json.loads(match.group(1))
        for t in results:
            for k, v in t.items():
                if k != "name":
                    assert v is True or v == True or bool(v), f"Test {t['name']} failed on {k}: {v}"
    finally:
        Path(script_file).unlink(missing_ok=True)
        Path(runner_file).unlink(missing_ok=True)


def test_graphview_reset_home(node_available: bool) -> None:
    if not node_available:
        pytest.skip("Node.js is not installed on the system")

    scripts = extract_scripts(GRAPH_HTML)
    main_script = "\n".join(scripts)

    runner_code = r"""
const fs = require('fs');

class MockElement {
  constructor(tag, id = '') {
    this.tagName = (tag || 'div').toUpperCase();
    this.id = id;
    this.className = '';
    this.classList = {
      _classes: new Set(),
      add(c) { this._classes.add(c); },
      remove(c) { this._classes.delete(c); },
      contains(c) { return this._classes.has(c); },
      toggle(c, force) {
        if (force === true) this._classes.add(c);
        else if (force === false) this._classes.delete(c);
        else if (this._classes.has(c)) this._classes.delete(c);
        else this._classes.add(c);
      }
    };
    this.style = {
      display: '',
      setProperty() {},
      removeProperty() {}
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

const knownIds = [
  'wrap', 'centerwrap', 'netwrap', 'net', 'reader', 'rtitle', 'rbody', 'rfs', 'sharebox',
  'docs', 'docq', 'desclines', 'pinnedhead', 'pinnedlist', 'doclist', 'showhidden', 'hiddenlist',
  'detailpane', 'detailtogglebtn', 'panel', 'degctl', 'fslider', 'fmin', 'bar', 'worktabs', 'tab-docs', 'tab-graph', 'tab-search', 'tab-menu',
  'morebtn', 'nodepop', 'stat', 'authstate', 'themebtn', 'sem', 'searchkind', 'synthchips', 'synthbtn',
  'advsearchbtn', 'advsearchpane', 'fts-opt-wrap', 'semchk', 'semkind', 'sembadge', 'semantic-opt-wrap',
  'addbtn', 'dedupbtn', 'pathbtn', 'graph-section', 'repolink', 'format-warn-banner', 'format-warn-text', 'format-warn-badge', 'format-warn-icon', 'format-warn-title', 'format-warn-actbtn', 'graphnotice',
  'graphdocnav', 'graphdocpick', 'graphdoclabel', 'graphdocprev', 'graphdocnext', 'graphdocmenu', 'graphdocq', 'graphdoclist', 'graphdocempty', 'q', 'legend'
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
      remove() {}
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

async function fetch(url, opts) {
  if (url === 'whoami') return { ok: true, status: 200, json: async () => ({ scope: 'owner' }) };
  if (url === 'documents') return {
    ok: true, status: 200, json: async () => ({
      documents: [
        { id: 'doc-1', title: '문서1', summary: '요약1', seen: 1, pinned: 0, hidden: 0, fetched_at: 1724100000 },
        { id: 'doc-2', title: '문서2', summary: '요약2', seen: 1, pinned: 0, hidden: 0, fetched_at: 1724100000 }
      ]
    })
  };
  if (url.startsWith('document?id=')) return { ok: true, status: 200, json: async () => ({ id: 'doc-1', title: '문서1', summary: '요약1' }) };
  if (url === 'graph') return { ok: true, status: 200, json: async () => ({ nodes: [], edges: [], types: [], rel_types: [] }) };
  return { ok: true, status: 200, json: async () => ({}) };
}

global.window = window;
global.document = document;
global.fetch = fetch;
global.DOMPurify = window.DOMPurify;
global.marked = window.marked;
global.vis = window.vis;
global.localStorage = window.localStorage;
global.location = window.location;
global.requestAnimationFrame = window.requestAnimationFrame;

const code = fs.readFileSync(process.argv[2], 'utf8');
eval(code);

setTimeout(() => {
  // Switch to graph view or modify state
  setCenterView('graph');
  document.getElementById('docq').value = '테스트';
  
  // Call resetHome()
  resetHome();

  const results = {
    centerView: document.body.dataset.centerView,
    activeDoc: window.claireDebug.activeDoc,
    docqEmpty: document.getElementById('docq').value === ''
  };
  console.log("RESET_HOME_RESULT:" + JSON.stringify(results));
  process.exit(0);
}, 150);
"""

    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f_script:
        f_script.write(main_script)
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
        assert proc.returncode == 0, f"resetHome runner crashed:\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
        match = re.search(r"RESET_HOME_RESULT:(.*)", proc.stdout)
        assert match is not None, f"Output did not contain RESET_HOME_RESULT:\n{proc.stdout}"
        data = json.loads(match.group(1))
        assert data["centerView"] == "graph"
        assert data["activeDoc"] is None
        assert data["docqEmpty"] is True
    finally:
        Path(script_file).unlink(missing_ok=True)
        Path(runner_file).unlink(missing_ok=True)


def test_graphview_mobile_search_and_docs_return(node_available: bool) -> None:
    if not node_available:
        pytest.skip("Node.js is not installed on the system")

    scripts = extract_scripts(GRAPH_HTML)
    main_script = "\n".join(scripts)

    runner_code = r"""
const fs = require('fs');

class MockElement {
  constructor(tag, id = '') {
    this.tagName = (tag || 'div').toUpperCase();
    this.id = id;
    this.className = '';
    this.classList = {
      _classes: new Set(),
      add(c) { this._classes.add(c); },
      remove(c) { this._classes.delete(c); },
      contains(c) { return this._classes.has(c); },
      toggle(c, force) {
        if (force === true) this._classes.add(c);
        else if (force === false) this._classes.delete(c);
        else if (this._classes.has(c)) this._classes.delete(c);
        else this._classes.add(c);
      }
    };
    this.style = {
      display: '',
      setProperty() {},
      removeProperty() {}
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
  getBoundingClientRect() { return { width: 390, height: 844, top: 0, left: 0, right: 390, bottom: 844 }; }
  querySelector(sel) { return new MockElement('div'); }
  querySelectorAll(sel) { return []; }
  addEventListener() {}
  removeEventListener() {}
  focus() {}
  select() {}
}

const elements = {};
function getOrCreate(id, tag = 'div') {
  if (!elements[id]) {
    elements[id] = new MockElement(tag, id);
  }
  return elements[id];
}

const requiredIds = [
  'bar', 'themebtn', 'morebtn', 'format-warn-banner', 'format-warn-badge',
  'format-warn-icon', 'format-warn-title', 'format-warn-text', 'format-warn-actbtn',
  'drawerbackdrop', 'wrap', 'docs', 'docq', 'desclines', 'pinnedhead', 'pinnedlist',
  'doclist', 'showhidden', 'hiddenlist', 'centerwrap', 'netwrap', 'netsearch',
  'barsearch', 'q', 'legendbar', 'graphdocnav', 'graphdocprev',
  'graphdocpick', 'graphdoclabel', 'graphdocnext', 'graphdocmenu', 'graphdocq',
  'graphdoclist', 'net', 'graphnotice', 'zoomctl', 'reader', 'rtitle', 'rfs',
  'reditbtn', 'sharebox', 'rbody', 'detailpane', 'detailhead', 'detailclose',
  'drawerscroll', 'drawer-graph-action', 'opengraphbtn', 'openreaderbtn', 'moremenu', 'sem',
  'searchkind', 'advsearchbtn', 'advsearchpane', 'fts-opt-wrap', 'semchk', 'semkind', 'sembadge', 'semantic-opt-wrap', 'synthchips', 'synthbtn', 'addbtn', 'dedupbtn', 'pathbtn', 'graph-section', 'menu-section-title',
  'degctl', 'fmin', 'fslider', 'repolink', 'authstate', 'stat', 'panel', 'worktabs',
  'tab-docs', 'tab-search', 'tab-menu', 'nodepop'
];

requiredIds.forEach(id => getOrCreate(id));

const document = {
  documentElement: {
    getAttribute: () => 'light',
    setAttribute: () => {},
    style: { setProperty: () => {} }
  },
  body: new MockElement('body'),
  getElementById: (id) => getOrCreate(id),
  querySelector: (sel) => {
    if (sel.startsWith('#')) return getOrCreate(sel.slice(1));
    return new MockElement('div');
  },
  querySelectorAll: () => [],
  activeElement: null,
  addEventListener: () => {}
};

const window = {
  matchMedia: (q) => {
    const isMobile = q.includes('max-width:720px');
    return {
      matches: isMobile,
      addEventListener: () => {},
      removeEventListener: () => {}
    };
  },
  addEventListener: () => {},
  requestAnimationFrame: (cb) => { setTimeout(cb, 0); return 1; },
  cancelAnimationFrame: () => {},
  localStorage: {
    getItem: () => null,
    setItem: () => {},
    removeItem: () => {}
  },
  location: { hash: '', search: '', pathname: '/' },
  DOMPurify: { sanitize: (s) => s },
  marked: { parse: (s) => `<p>${s}</p>` },
  vis: {
    DataSet: class {
      constructor(data) { this._data = data || []; }
      get(id) { return this._data.find(d => d.id === id); }
      getIds() { return this._data.map(d => d.id); }
      update() {}
      add() {}
      remove() {}
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
  }
};

const mockDocs = [
  { id: 'doc-1', title: '자료 1', summary: '첫 번째 자료', fetched_at: 1700000000, seen: 1, pinned: 0, hidden: 0 },
  { id: 'doc-2', title: '자료 2', summary: '두 번째 자료', fetched_at: 1700000000, seen: 1, pinned: 0, hidden: 0 }
];

async function fetch(url) {
  if (url === 'whoami') return { ok: true, status: 200, json: async () => ({ scope: 'owner' }) };
  if (url === 'auth/state') return { ok: true, status: 200, json: async () => ({ scope: 'anonymous', readonly: true }) };
  if (url === 'documents') return { ok: true, status: 200, json: async () => ({ documents: mockDocs }) };
  if (url.startsWith('document?id=')) return { ok: true, status: 200, json: async () => ({ id: 'doc-1', title: '자료 1', text: '본문' }) };
  if (url === 'graph') return { ok: true, status: 200, json: async () => ({ nodes: [], edges: [], types: [], rel_types: [] }) };
  return { ok: true, status: 200, json: async () => ({}) };
}

global.window = window;
global.document = document;
global.fetch = fetch;
global.DOMPurify = window.DOMPurify;
global.marked = window.marked;
global.vis = window.vis;
global.localStorage = window.localStorage;
global.location = window.location;
global.requestAnimationFrame = window.requestAnimationFrame;
global.getComputedStyle = window.getComputedStyle = () => ({ getPropertyValue: () => '' });

const code = fs.readFileSync(process.argv[2], 'utf8');
eval(code);

setTimeout(() => {
  // 1. Initial state: documents are rendered in doclist
  const initialHasDocs = document.getElementById('doclist').innerHTML.includes('자료 1');

  // 2. Click search tab (focusMobileSearch)
  focusMobileSearch();
  const searchStatePrompt = document.getElementById('doclist').innerHTML.includes('검색어를 입력하세요');
  const searchDocSearchActive = window.claireDebug.docSearchActive;

  // 3. User types in search input
  document.getElementById('docq').value = '자료';
  renderDocs('자료');

  // 4. User clicks docs tab (revealWorkspace('docs'))
  revealWorkspace('docs');
  const returnHasDocs = document.getElementById('doclist').innerHTML.includes('자료 1');
  const returnDocSearchActive = window.claireDebug.docSearchActive;
  const returnDocqEmpty = document.getElementById('docq').value === '';

  const results = {
    initialHasDocs,
    searchStatePrompt,
    searchDocSearchActive,
    returnHasDocs,
    returnDocSearchActive,
    returnDocqEmpty
  };
  console.log("MOBILE_SEARCH_RETURN_RESULT:" + JSON.stringify(results));
  process.exit(0);
}, 150);
"""

    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f_script:
        f_script.write(main_script)
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
        assert proc.returncode == 0, f"Mobile search return runner crashed:\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
        match = re.search(r"MOBILE_SEARCH_RETURN_RESULT:(.*)", proc.stdout)
        assert match is not None, f"Output did not contain MOBILE_SEARCH_RETURN_RESULT:\n{proc.stdout}"
        data = json.loads(match.group(1))
        assert data["initialHasDocs"] is True
        assert data["searchStatePrompt"] is True
        assert data["searchDocSearchActive"] is True
        assert data["returnHasDocs"] is True
        assert data["returnDocSearchActive"] is False
        assert data["returnDocqEmpty"] is True
    finally:
        Path(script_file).unlink(missing_ok=True)
        Path(runner_file).unlink(missing_ok=True)


def test_center_view_mode_switching_and_stat_display_runtime(node_available: bool) -> None:
    if not node_available:
        pytest.skip("Node.js is not installed on the system")
    """setCenterView 호출 시 우측 메뉴 타이틀 전환 및 renderDocs 시 span#stat 업데이트 런타임 검증."""
    scripts = extract_scripts(GRAPH_HTML)
    main_script = "\n".join(scripts)

    runner_code = r"""
const fs = require('fs');

class MockElement {
  constructor(tag, id = '') {
    this.tagName = (tag || 'div').toUpperCase();
    this.id = id;
    this.className = '';
    this.classList = {
      _classes: new Set(),
      add(c) { this._classes.add(c); },
      remove(c) { this._classes.delete(c); },
      contains(c) { return this._classes.has(c); },
      toggle(c, force) {
        if (force === true) this._classes.add(c);
        else if (force === false) this._classes.delete(c);
        else if (this._classes.has(c)) this._classes.delete(c);
        else this._classes.add(c);
      }
    };
    this.style = {
      display: '',
      setProperty() {},
      removeProperty() {}
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
  getBoundingClientRect() { return { width: 390, height: 844, top: 0, left: 0, right: 390, bottom: 844 }; }
  querySelector(sel) { return new MockElement('div'); }
  querySelectorAll(sel) { return []; }
  addEventListener() {}
  removeEventListener() {}
  focus() {}
  select() {}
}

const elements = {};
function getOrCreate(id, tag = 'div') {
  if (!elements[id]) {
    elements[id] = new MockElement(tag, id);
  }
  return elements[id];
}

const requiredIds = [
  'bar', 'themebtn', 'morebtn', 'format-warn-banner', 'format-warn-badge',
  'format-warn-icon', 'format-warn-title', 'format-warn-text', 'format-warn-actbtn',
  'drawerbackdrop', 'wrap', 'docs', 'docq', 'desclines', 'pinnedhead', 'pinnedlist',
  'doclist', 'showhidden', 'hiddenlist', 'centerwrap', 'netwrap', 'netsearch',
  'barsearch', 'q', 'legendbar', 'graphdocnav', 'graphdocprev',
  'graphdocpick', 'graphdoclabel', 'graphdocnext', 'graphdocmenu', 'graphdocq',
  'graphdoclist', 'net', 'graphnotice', 'zoomctl', 'reader', 'rtitle', 'rfs',
  'reditbtn', 'sharebox', 'rbody', 'detailpane', 'detailhead', 'detailclose',
  'drawerscroll', 'drawer-graph-action', 'opengraphbtn', 'openreaderbtn', 'moremenu', 'sem',
  'searchkind', 'advsearchbtn', 'advsearchpane', 'fts-opt-wrap', 'semchk', 'semkind', 'sembadge', 'semantic-opt-wrap', 'synthchips', 'synthbtn', 'addbtn', 'dedupbtn', 'pathbtn', 'graph-section', 'menu-section-title',
  'degctl', 'fmin', 'fslider', 'repolink', 'authstate', 'stat', 'panel', 'worktabs',
  'tab-docs', 'tab-search', 'tab-menu', 'nodepop'
];

requiredIds.forEach(id => getOrCreate(id));

const document = {
  documentElement: {
    getAttribute: () => 'light',
    setAttribute: () => {},
    style: { setProperty: () => {} }
  },
  body: (() => {
    const b = new MockElement('body');
    b.dataset = { centerView: 'reader' };
    return b;
  })(),
  getElementById: (id) => getOrCreate(id),
  querySelector: (sel) => {
    if (sel.startsWith('#')) return getOrCreate(sel.slice(1));
    return new MockElement('div');
  },
  querySelectorAll: () => [],
  activeElement: null,
  addEventListener: () => {}
};

const window = {
  matchMedia: (q) => {
    const isMobile = q.includes('max-width:720px');
    return {
      matches: isMobile,
      addEventListener: () => {},
      removeEventListener: () => {}
    };
  },
  addEventListener: () => {},
  requestAnimationFrame: (cb) => { setTimeout(cb, 0); return 1; },
  cancelAnimationFrame: () => {},
  localStorage: {
    getItem: () => null,
    setItem: () => {},
    removeItem: () => {}
  },
  location: { hash: '', search: '', pathname: '/' },
  DOMPurify: { sanitize: (s) => s },
  marked: { parse: (s) => `<p>${s}</p>` },
  vis: {
    DataSet: class {
      constructor(data) { this._data = data || []; }
      get(id) { return this._data.find(d => d.id === id); }
      getIds() { return this._data.map(d => d.id); }
      update() {}
      add() {}
      remove() {}
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
  }
};

const mockDocs = [
  { id: 'doc-1', title: '자료 1', summary: '첫 번째 자료', fetched_at: 1700000000, seen: 1, pinned: 0, hidden: 0 },
  { id: 'doc-2', title: '특별한 문서', summary: '두 번째 자료', fetched_at: 1700000000, seen: 1, pinned: 0, hidden: 0 }
];

async function fetch(url) {
  if (url === 'whoami') return { ok: true, status: 200, json: async () => ({ scope: 'owner' }) };
  if (url === 'auth/state') return { ok: true, status: 200, json: async () => ({ scope: 'anonymous', readonly: true }) };
  if (url === 'documents') return { ok: true, status: 200, json: async () => ({ documents: mockDocs }) };
  if (url.startsWith('document?id=')) return { ok: true, status: 200, json: async () => ({ id: 'doc-1', title: '자료 1', text: '본문' }) };
  if (url === 'graph') return { ok: true, status: 200, json: async () => ({ nodes: [], edges: [], types: [], rel_types: [], stats: { entities: 5, relations: 3, max_degree: 2 } }) };
  return { ok: true, status: 200, json: async () => ({}) };
}

global.window = window;
global.document = document;
global.fetch = fetch;
global.DOMPurify = window.DOMPurify;
global.marked = window.marked;
global.vis = window.vis;
global.localStorage = window.localStorage;
global.location = window.location;
global.requestAnimationFrame = window.requestAnimationFrame;
global.getComputedStyle = window.getComputedStyle = () => ({ getPropertyValue: () => '' });

const code = fs.readFileSync(process.argv[2], 'utf8');
eval(code);

setTimeout(() => {
  // 1. Initial centerView is reader
  const initialCenterView = document.body.dataset.centerView;
  const initialTitle = document.getElementById('menu-section-title').textContent;

  // 2. Switch to graph view
  setCenterView('graph');
  const graphCenterView = document.body.dataset.centerView;
  const graphTitle = document.getElementById('menu-section-title').textContent;

  // 3. Switch back to reader view
  setCenterView('reader');
  const readerCenterView = document.body.dataset.centerView;
  const readerTitle = document.getElementById('menu-section-title').textContent;

  // 4. Test doc search updates span#stat
  renderDocs('특별한');
  const statFilteredText = document.getElementById('stat').textContent;

  renderDocs('');
  const statAllText = document.getElementById('stat').innerHTML;

  const results = {
    initialCenterView,
    initialTitle,
    graphCenterView,
    graphTitle,
    readerCenterView,
    readerTitle,
    statFilteredText,
    statAllText
  };
  console.log("CENTER_VIEW_STAT_RESULT:" + JSON.stringify(results));
  process.exit(0);
}, 150);
"""

    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f_script:
        f_script.write(main_script)
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
        assert proc.returncode == 0, f"Center view test runner crashed:\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
        match = re.search(r"CENTER_VIEW_STAT_RESULT:(.*)", proc.stdout)
        assert match is not None, f"Output did not contain CENTER_VIEW_STAT_RESULT:\n{proc.stdout}"
        data = json.loads(match.group(1))
        assert data["initialCenterView"] == "graph"
        assert data["graphCenterView"] == "graph"
        assert data["graphTitle"] == "그래프 도구"
        assert data["readerCenterView"] == "reader"
        assert data["readerTitle"] == "문서와 그래프"
        assert "1개 발견" in data["statFilteredText"]
        assert "2개 문서" in data["statAllText"]
    finally:
        Path(script_file).unlink(missing_ok=True)
        Path(runner_file).unlink(missing_ok=True)


def test_graphview_search_concurrency_and_mode_safety(node_available: bool) -> None:
    """Verifies that slow semantic search cannot mask or overwrite a newer fulltext search result."""
    if not node_available:
        pytest.skip("Node.js is not installed on the system")

    scripts = extract_scripts(GRAPH_HTML)
    assert len(scripts) >= 1
    main_script = "\n".join(scripts)

    runner_code = r"""
const fs = require('fs');

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
    this.listeners = {};
    this.checked = false;
    this.disabled = false;
  }
  get innerHTML() { return this._innerHTML; }
  set innerHTML(v) { this._innerHTML = String(v); this._textContent = String(v).replace(/<[^>]*>/g, ''); }
  get textContent() { return this._textContent; }
  set textContent(v) { this._textContent = String(v); this._innerHTML = String(v); }
  setAttribute(k, v) { this.attributes[k] = String(v); if(k==='id') this.id=String(v); }
  getAttribute(k) { return this.attributes[k] !== undefined ? this.attributes[k] : null; }
  removeAttribute(k) { delete this.attributes[k]; }
  getBoundingClientRect() { return { width: 1000, height: 700, top: 0, left: 0, right: 1000, bottom: 700 }; }
  querySelector() { return new MockElement('div'); }
  querySelectorAll() { return []; }
  addEventListener(evt, fn) {
    if(!this.listeners[evt]) this.listeners[evt] = [];
    this.listeners[evt].push(fn);
  }
  removeEventListener() {}
  dispatchEvent(evt) {
    const list = this.listeners[evt.type] || [];
    list.forEach(fn => fn(evt));
  }
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

const window = {
  location: { reload() {}, href: 'http://localhost/' },
  localStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} },
  DOMPurify: { sanitize: (s) => s },
  matchMedia: () => ({ matches: false, addEventListener: () => {}, removeEventListener: () => {} }),
  addEventListener: () => {},
  removeEventListener: () => {},
  requestAnimationFrame: (cb) => setTimeout(cb, 0),
  getComputedStyle: () => ({ getPropertyValue: () => '' }),
  marked: { parse: (s) => `<p>${s}</p>` },
  vis: {
    DataSet: class {
      constructor(data) { this._data = data || []; }
      get(id) { return this._data.find(d => d.id === id); }
      getIds() { return this._data.map(d => d.id); }
      update() {}
      add() {}
      remove() {}
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
  }
};

const document = {
  body: new MockElement('body'),
  documentElement: new MockElement('html'),
  getElementById(id) { return getOrCreate(id); },
  querySelector(sel) {
    if (sel.startsWith('#')) return getOrCreate(sel.slice(1));
    return new MockElement('div');
  },
  querySelectorAll() { return []; },
  createElement(tag) { return new MockElement(tag); },
  createTextNode(text) { return { textContent: text }; },
  addEventListener() {},
  removeEventListener() {}
};

let capturedSearchRequests = [];

async function fetch(url, opts) {
  if (url === 'whoami') return { ok: true, status: 200, json: async () => ({ scope: 'owner' }) };
  if (url === 'auth/state') return { ok: true, status: 200, json: async () => ({ scope: 'owner', readonly: false }) };
  if (url === 'documents') return { ok: true, status: 200, json: async () => ({ documents: [] }) };
  if (url === 'graph') return {
    ok: true,
    status: 200,
    json: async () => ({
      nodes: [
        { id: 'node-semantic', label: 'Semantic Hit' },
        { id: 'node-fts', label: 'FTS Hit' }
      ],
      edges: [],
      types: [],
      rel_types: [],
      stats: { entities: 2, relations: 0, max_degree: 0 }
    })
  };
  if (url === 'search') {
    const body = JSON.parse((opts && opts.body) || '{}');
    capturedSearchRequests.push(body);
    const mode = body.mode || 'hybrid';
    const isSemantic = mode === 'hybrid';
    const delay = isSemantic ? 120 : 20;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        if (opts && opts.signal && opts.signal.aborted) {
          const err = new Error('The operation was aborted');
          err.name = 'AbortError';
          reject(err);
          return;
        }
        resolve({
          ok: true,
          status: 200,
          json: async () => {
            if (isSemantic) {
              return {
                query: body.query,
                mode: 'hybrid',
                hits: [{ id: 'node-semantic', name: 'Semantic Hit' }]
              };
            } else {
              return {
                query: body.query,
                mode: 'fts',
                hits: [{ id: 'node-fts', name: 'FTS Hit' }]
              };
            }
          }
        });
      }, delay);

      if (opts && opts.signal) {
        opts.signal.addEventListener('abort', () => {
          clearTimeout(timer);
          const err = new Error('The operation was aborted');
          err.name = 'AbortError';
          reject(err);
        });
      }
    });
  }
  return { ok: true, status: 200, json: async () => ({}) };
}

global.window = window;
global.document = document;
global.fetch = fetch;
global.DOMPurify = window.DOMPurify;
global.marked = window.marked;
global.vis = window.vis;
global.localStorage = window.localStorage;
global.location = window.location;
global.requestAnimationFrame = window.requestAnimationFrame;
global.getComputedStyle = window.getComputedStyle;

const code = fs.readFileSync(process.argv[2], 'utf8') + '\nwindow.__getHighlightSet = () => highlightSet;\n';
eval(code);

setTimeout(async () => {
  // Step 1: Trigger slow semantic search
  const semchk = document.getElementById('semchk');
  const sem = document.getElementById('sem');
  semchk.checked = true;
  sem.checked = false;
  const p1 = semanticSearch('slow semantic query');

  // Step 2: Shortly after (10ms), switch to FTS and run fast fulltext search
  await new Promise(r => setTimeout(r, 10));
  semchk.checked = false;
  sem.checked = true;
  const p2 = semanticSearch('fast fulltext query');

  // Step 3: Wait for all promises
  try { await p1; } catch(_) {}
  try { await p2; } catch(_) {}
  await new Promise(r => setTimeout(r, 160));

  const finalStat = document.getElementById('stat').textContent;
  const hlSet = window.__getHighlightSet ? window.__getHighlightSet() : null;
  const finalHighlight = hlSet ? Array.from(hlSet) : [];

  console.log("SEARCH_SAFETY_RESULT:" + JSON.stringify({
    requests: capturedSearchRequests,
    finalStat,
    finalHighlight
  }));
  process.exit(0);
}, 60);
"""

    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f_script:
        f_script.write(main_script)
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
        assert proc.returncode == 0, f"Search concurrency test runner crashed:\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
        match = re.search(r"SEARCH_SAFETY_RESULT:(.*)", proc.stdout)
        assert match is not None, f"Output did not contain SEARCH_SAFETY_RESULT:\n{proc.stdout}"
        data = json.loads(match.group(1))

        # Verify requests sent correct mode
        assert len(data["requests"]) == 2
        assert data["requests"][0]["mode"] == "hybrid"
        assert data["requests"][1]["mode"] == "fts"

        # Verify that fast FTS search won and was NOT overwritten by the slow semantic search
        assert "Full-Text Search" in data["finalStat"]
        assert "Semantic Search" not in data["finalStat"]
        assert data["finalHighlight"] == ["node-fts"]
    finally:
        Path(script_file).unlink(missing_ok=True)
        Path(runner_file).unlink(missing_ok=True)


def test_two_column_graph_menu_selection_runtime(node_available: bool) -> None:
    """2단 보기 화면에서 그래프 메뉴(openDocGraph / revealWorkspace)를 켰을 때 선택된 노트의 노드들이 캔버스 크기 갱신과 함께 전체 표시(fit/focus)되는지 런타임 검증."""
    if not node_available:
        pytest.skip("Node.js is not installed on the system")

    scripts = extract_scripts(GRAPH_HTML)
    main_script = "\n".join(scripts)

    runner_code = r"""
const fs = require('fs');

class MockElement {
  constructor(tag, id = '') {
    this.tagName = (tag || 'div').toUpperCase();
    this.id = id;
    this.className = '';
    this.classList = {
      _classes: new Set(),
      add(c) { this._classes.add(c); },
      remove(c) { this._classes.delete(c); },
      contains(c) { return this._classes.has(c); },
      toggle(c, force) {
        if (force === true) this._classes.add(c);
        else if (force === false) this._classes.delete(c);
        else if (this._classes.has(c)) this._classes.delete(c);
        else this._classes.add(c);
      }
    };
    this.style = {
      display: '',
      setProperty() {},
      removeProperty() {}
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
  getBoundingClientRect() {
    if (this.id === 'net') return { width: 720, height: 600, top: 0, left: 280, right: 1000, bottom: 600 };
    return { width: 1000, height: 700, top: 0, left: 0, right: 1000, bottom: 700 };
  }
  querySelector(sel) { return new MockElement('div'); }
  querySelectorAll(sel) { return []; }
  addEventListener() {}
  removeEventListener() {}
  focus() {}
  select() {}
}

const elements = {};
function getOrCreate(id, tag = 'div') {
  if (!elements[id]) {
    elements[id] = new MockElement(tag, id);
  }
  return elements[id];
}

const requiredIds = [
  'wrap', 'centerwrap', 'netwrap', 'net', 'reader', 'rtitle', 'rbody', 'rfs', 'sharebox',
  'docs', 'docq', 'desclines', 'pinnedhead', 'pinnedlist', 'doclist', 'showhidden', 'hiddenlist',
  'detailpane', 'detailhead', 'detailclose', 'detailtogglebtn', 'panel', 'degctl', 'fslider', 'fmin', 'bar',
  'worktabs', 'tab-docs', 'tab-graph', 'tab-search', 'tab-menu', 'legendbar',
  'morebtn', 'nodepop', 'stat', 'authstate', 'themebtn', 'sem', 'searchkind', 'synthchips', 'synthbtn',
  'advsearchbtn', 'advsearchpane', 'fts-opt-wrap', 'semchk', 'semkind', 'sembadge', 'semantic-opt-wrap',
  'addbtn', 'dedupbtn', 'pathbtn', 'opengraphbtn', 'openreaderbtn', 'graph-section', 'menu-section-title',
  'repolink', 'format-warn-banner', 'format-warn-text', 'format-warn-badge', 'format-warn-icon', 'format-warn-title', 'format-warn-actbtn', 'graphnotice',
  'graphdocnav', 'graphdocpick', 'graphdoclabel', 'graphdocprev', 'graphdocnext', 'graphdocmenu', 'graphdocq', 'graphdoclist', 'graphdocempty'
];
requiredIds.forEach(id => getOrCreate(id));

const document = {
  documentElement: getOrCreate('html', 'html'),
  body: getOrCreate('body', 'body'),
  getElementById(id) { return getOrCreate(id); },
  querySelector(sel) {
    if (sel.startsWith('#')) return getOrCreate(sel.slice(1));
    return new MockElement('div');
  },
  querySelectorAll(sel) { return []; },
  addEventListener() {},
  removeEventListener() {},
  activeElement: null
};

const calls = {
  setSize: [],
  selectNodes: [],
  fit: [],
  focus: [],
  moveTo: []
};

const window = {
  document,
  matchMedia(query) {
    return {
      matches: query.includes('1100px'), // 2단 보기 (compact layout)
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
      constructor(data) { this._data = (data || []).map(d => ({ ...d })); }
      get(id) { return this._data.find(d => d.id === id); }
      getIds() { return this._data.map(d => d.id); }
      update(items) {
        (Array.isArray(items) ? items : [items]).forEach(item => {
          const idx = this._data.findIndex(d => d.id === item.id);
          if (idx >= 0) Object.assign(this._data[idx], item);
          else this._data.push({ ...item });
        });
      }
      add(items) { this.update(items); }
      forEach(fn) { this._data.forEach(fn); }
      get length() { return this._data.length; }
    },
    Network: class {
      constructor() {}
      setSize(w, h) { calls.setSize.push({ w, h }); }
      redraw() {}
      fit(opts) { calls.fit.push(opts); }
      focus(id, opts) { calls.focus.push({ id, opts }); }
      moveTo(opts) { calls.moveTo.push(opts); }
      on() {}
      selectNodes(ids) { calls.selectNodes.push(ids); }
      unselectAll() {}
      getSelectedNodes() { return []; }
      getScale() { return 1.0; }
      getViewPosition() { return { x: 0, y: 0 }; }
      getPositions() { return {}; }
      canvasToDOM(p) { return p; }
      getPosition() { return { x: 0, y: 0 }; }
      setOptions() {}
    }
  }
};

const mockDocs = [
  { id: 'doc-note-1', title: '노트 1 (다중 노드)', summary: '첫 번째 노트', fetched_at: 1700000000, seen: 1, pinned: 0, hidden: 0 },
  { id: 'doc-note-2', title: '노트 2 (단일 노드)', summary: '두 번째 노트', fetched_at: 1700000000, seen: 1, pinned: 0, hidden: 0 }
];

const mockGraph = {
  nodes: [
    { id: 'node-A', label: '엔티티 A', group: 'Concept', degree: 2, sources: ['doc-note-1'] },
    { id: 'node-B', label: '엔티티 B', group: 'Tool', degree: 3, sources: ['doc-note-1'] },
    { id: 'node-C', label: '엔티티 C', group: 'Model', degree: 1, sources: ['doc-note-2'] }
  ],
  edges: [
    { id: 'e1', from: 'node-A', to: 'node-B', label: '연결' }
  ],
  stats: { entities: 3, relations: 1, max_degree: 3 }
};

async function fetch(url) {
  if (url === 'whoami') return { ok: true, status: 200, json: async () => ({ scope: 'owner' }) };
  if (url === 'auth/state') return { ok: true, status: 200, json: async () => ({ scope: 'anonymous', readonly: true }) };
  if (url === 'documents') return { ok: true, status: 200, json: async () => ({ documents: mockDocs }) };
  if (url === 'graph') return { ok: true, status: 200, json: async () => mockGraph };
  if (url.startsWith('document?id=')) {
    const id = decodeURIComponent(url.split('=')[1]);
    const doc = mockDocs.find(d => d.id === id) || mockDocs[0];
    return { ok: true, status: 200, json: async () => ({ id: doc.id, title: doc.title, summary: doc.summary, detail: '본문' }) };
  }
  return { ok: true, status: 200, json: async () => ({}) };
}

global.window = window;
global.document = document;
global.fetch = fetch;
global.DOMPurify = window.DOMPurify;
global.marked = window.marked;
global.vis = window.vis;
global.localStorage = { getItem() { return null; }, setItem() {} };
global.location = { origin: 'http://127.0.0.1:8766' };
global.requestAnimationFrame = window.requestAnimationFrame;
global.getComputedStyle = () => ({ getPropertyValue: () => '#ffffff' });

const code = fs.readFileSync(process.argv[2], 'utf8');
eval(code);

setTimeout(async () => {
  for (let i = 0; i < 50; i++) {
    if (typeof net !== 'undefined' && net && allNodes && allNodes.length) break;
    await new Promise(r => setTimeout(r, 10));
  }

  // 1. Initial state check: 2-column view with reader as centerView
  selectDoc('doc-note-1');
  await new Promise(r => setTimeout(r, 40));

  // Reset call history to isolate openDocGraph actions
  calls.setSize = [];
  calls.selectNodes = [];
  calls.fit = [];
  calls.focus = [];
  calls.moveTo = [];

  // 2. Open graph for doc-note-1 (has 2 nodes: node-A, node-B)
  openDocGraph('doc-note-1');
  await new Promise(r => setTimeout(r, 60));

  const multiNodeResult = {
    centerView: document.body.dataset.centerView,
    activePane: document.body.dataset.activePane,
    setSizeCalls: [...calls.setSize],
    selectNodesCalls: [...calls.selectNodes],
    fitCalls: [...calls.fit],
    focusCalls: [...calls.focus]
  };

  // Reset calls
  calls.setSize = [];
  calls.selectNodes = [];
  calls.fit = [];
  calls.focus = [];
  calls.moveTo = [];

  // 3. Open graph for doc-note-2 (has 1 node: node-C -> should focus, not fit)
  openDocGraph('doc-note-2');
  await new Promise(r => setTimeout(r, 60));

  const singleNodeResult = {
    centerView: document.body.dataset.centerView,
    selectNodesCalls: [...calls.selectNodes],
    fitCalls: [...calls.fit],
    focusCalls: [...calls.focus]
  };

  console.log("TWO_COL_RESULT:" + JSON.stringify({
    multiNode: multiNodeResult,
    singleNode: singleNodeResult
  }));
  process.exit(0);
}, 60);
"""

    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f_script:
        f_script.write(main_script)
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
        assert proc.returncode == 0, f"Two-column graph menu test runner crashed:\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
        match = re.search(r"TWO_COL_RESULT:(.*)", proc.stdout)
        assert match is not None, f"Output did not contain TWO_COL_RESULT:\n{proc.stdout}"
        data = json.loads(match.group(1))

        # 1. Multi-node note verification
        multi = data["multiNode"]
        assert multi["centerView"] == "graph"
        assert multi["activePane"] == "graph"
        # setSize should have been called with visible 720px width
        assert any(c["w"] == "720px" for c in multi["setSizeCalls"])
        # Both nodes of doc-note-1 should be selected
        assert any("node-A" in ids and "node-B" in ids for ids in multi["selectNodesCalls"])
        # Both nodes should be fitted
        assert any(
            isinstance(f, dict) and "nodes" in f and "node-A" in f["nodes"] and "node-B" in f["nodes"]
            for f in multi["fitCalls"]
        )

        # 2. Single-node note verification
        single = data["singleNode"]
        assert single["centerView"] == "graph"
        assert any("node-C" in ids for ids in single["selectNodesCalls"])
        # Single node should use focus (scale 1.2) rather than fit
        assert any(f["id"] == "node-C" for f in single["focusCalls"])
    finally:
        Path(script_file).unlink(missing_ok=True)
        Path(runner_file).unlink(missing_ok=True)


def test_mobile_node_tap_popup_runtime(node_available: bool) -> None:
    """Simulates mobile node tap interaction and verifies popup display.

    Verifies:
    1. Tapping a node in mobile mode shows #nodepop with label, degree, and action buttons.
    2. Mobile 1st tap keeps detail drawer closed so the graph remains unobscured.
    3. Tapping empty canvas hides #nodepop.
    4. Tapping the same node again opens detail pane.
    """
    if not node_available:
        pytest.skip("Node.js is not installed on the system")

    scripts = extract_scripts(GRAPH_HTML)
    assert len(scripts) >= 1
    main_script = scripts[-1]

    runner_code = r"""
const fs = require('fs');

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
    this.offsetWidth = 260;
    this.offsetHeight = 120;
  }
  get innerHTML() { return this._innerHTML; }
  set innerHTML(v) { this._innerHTML = String(v); this._textContent = String(v).replace(/<[^>]*>/g, ''); }
  get textContent() { return this._textContent; }
  set textContent(v) { this._textContent = String(v); this._innerHTML = String(v); }
  setAttribute(k, v) { this.attributes[k] = String(v); if(k==='id') this.id=String(v); }
  getAttribute(k) { return this.attributes[k] !== undefined ? this.attributes[k] : null; }
  removeAttribute(k) { delete this.attributes[k]; }
  getBoundingClientRect() { return { width: 400, height: 700, top: 0, left: 0, right: 400, bottom: 700 }; }
  querySelector(sel) { return new MockElement('div'); }
  querySelectorAll(sel) { return []; }
  addEventListener() {}
  removeEventListener() {}
  focus() {}
  select() {}
  contains() { return false; }
  closest() { return null; }
}

const elements = new Map();
function getOrCreate(id, tag='div') {
  if (!elements.has(id)) {
    elements.set(id, new MockElement(tag, id));
  }
  return elements.get(id);
}

const knownIds = [
  'wrap', 'centerwrap', 'netwrap', 'net', 'reader', 'rtitle', 'rbody', 'rfs', 'sharebox',
  'docs', 'docq', 'desclines', 'pinnedhead', 'pinnedlist', 'doclist', 'showhidden', 'hiddenlist',
  'detailpane', 'detailtogglebtn', 'panel', 'degctl', 'fslider', 'fmin', 'bar', 'worktabs', 'tab-docs', 'tab-graph', 'tab-search', 'tab-menu',
  'morebtn', 'nodepop', 'stat', 'authstate', 'themebtn', 'sem', 'searchkind', 'synthchips', 'synthbtn',
  'advsearchbtn', 'advsearchpane', 'fts-opt-wrap', 'semchk', 'semkind', 'sembadge', 'semantic-opt-wrap',
  'addbtn', 'dedupbtn', 'pathbtn', 'graph-section', 'repolink', 'format-warn-banner', 'format-warn-text', 'format-warn-badge', 'format-warn-icon', 'format-warn-title', 'format-warn-actbtn', 'graphnotice',
  'graphdocnav', 'graphdocpick', 'graphdoclabel', 'graphdocprev', 'graphdocnext', 'graphdocmenu', 'graphdocq', 'graphdoclist', 'graphdocempty',
  'legendbar'
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

const netCallbacks = {};
const window = {
  document,
  innerWidth: 412,
  innerHeight: 800,
  matchMedia(query) {
    const isMobile = query.includes('720px') || query.includes('1100px');
    return {
      matches: isMobile,
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
      get(id) {
        if (id === undefined) return this._data;
        if (Array.isArray(id)) return id.map(i => this._data.find(d => d.id === i)).filter(Boolean);
        return this._data.find(d => d.id === id) || null;
      }
      getIds() { return this._data.map(d => d.id); }
      update(items) {
        (Array.isArray(items) ? items : [items]).forEach(item => {
          const idx = this._data.findIndex(d => d.id === item.id);
          if (idx >= 0) Object.assign(this._data[idx], item);
          else this._data.push(Object.assign({}, item));
        });
      }
      add(items) { this.update(items); }
      forEach(fn) { this._data.forEach(fn); }
      get length() { return this._data.length; }
    },
    Network: class {
      constructor() {}
      setSize() {}
      redraw() {}
      fit() {}
      focus() {}
      moveTo() {}
      on(ev, fn) { netCallbacks[ev] = fn; }
      selectNodes() {}
      unselectAll() {}
      getSelectedNodes() { return []; }
      getScale() { return 1.0; }
      getViewPosition() { return { x: 0, y: 0 }; }
      getPositions() { return { 'n1': { x: 50, y: 50 } }; }
      canvasToDOM(p) { return { x: 100, y: 150 }; }
      getPosition() { return { x: 50, y: 50 }; }
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
        nodes: [{ id: 'n1', label: '엔티티1', group: 'Concept', degree: 2, obs: '첫 번째 관찰' }],
        edges: [{ from: 'n1', to: 'n2', label: '관련' }],
        stats: { max_degree: 2 }
      })
    };
  }
  if (url.startsWith('node?id=')) {
    return {
      ok: true,
      status: 200,
      json: async () => ({
        id: 'n1',
        name: '엔티티1',
        type: 'Concept',
        observations: ['첫 번째 관찰', '두 번째 관찰'],
        aliases: [],
        neighbors: [],
        documents: [{ id: 'doc-101', title: '클레어 바이블 문서 1', summary: '문서 요약' }]
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
global.requestAnimationFrame = window.requestAnimationFrame;

const scriptContent = fs.readFileSync(process.argv[2], 'utf8') +
  '\nwindow.__getDebug = () => ({ allNodes, selectedNodeId: typeof selectedNodeId !== "undefined" ? selectedNodeId : null, detailOpen: typeof detailOpen !== "undefined" ? detailOpen : false, drawerOpen: typeof drawerOpen !== "undefined" ? drawerOpen : false });\n';
try {
  eval(scriptContent);
} catch (err) {
  console.error("FATAL_EVAL_ERROR:", err.stack || err);
  process.exit(1);
}

setTimeout(async () => {
  for (let i = 0; i < 50; i++) {
    if (netCallbacks['click']) break;
    await new Promise(r => setTimeout(r, 20));
  }

  const nodepopEl = document.getElementById('nodepop');
  const clickHandler = netCallbacks['click'];
  if (!clickHandler) {
    const st = document.getElementById('stat');
    console.error("clickHandler not registered. Stat text:", st ? st.textContent : "no stat");
    process.exit(1);
  }

  // 1. First mobile tap on n1: should display #nodepop and keep detail drawer closed
  clickHandler({
    nodes: ['n1'],
    pointer: { DOM: { x: 100, y: 150 } },
    event: { srcEvent: { changedTouches: [{ clientX: 120, clientY: 180 }] } }
  });
  await new Promise(r => setTimeout(r, 40));

  // Simulate vis-network firing blurNode and hold right after tap on mobile:
  if (netCallbacks['blurNode']) netCallbacks['blurNode']();
  if (netCallbacks['hold']) netCallbacks['hold']();
  await new Promise(r => setTimeout(r, 20));

  const dbg1 = window.__getDebug();
  const firstTapResult = {
    popDisplay: nodepopEl.style.display,
    popHtml: nodepopEl.innerHTML,
    selectedNodeId: dbg1.selectedNodeId,
    detailOpen: dbg1.detailOpen,
    drawerOpen: dbg1.drawerOpen
  };

  // 2. Empty canvas tap: should hide #nodepop
  clickHandler({
    nodes: [],
    pointer: { DOM: { x: 50, y: 50 } },
    event: { srcEvent: {} }
  });
  await new Promise(r => setTimeout(r, 30));

  const emptyTapResult = {
    popDisplay: nodepopEl.style.display
  };

  // 3. Second tap on n1 (tap while already selected with popup) -> opens detail drawer
  clickHandler({
    nodes: ['n1'],
    pointer: { DOM: { x: 100, y: 150 } },
    event: { srcEvent: {} }
  });
  await new Promise(r => setTimeout(r, 20));
  // Tap again on same node:
  clickHandler({
    nodes: ['n1'],
    pointer: { DOM: { x: 100, y: 150 } },
    event: { srcEvent: {} }
  });
  await new Promise(r => setTimeout(r, 30));

  const dbg2 = window.__getDebug();
  const secondTapResult = {
    detailOpen: dbg2.detailOpen,
    drawerOpen: dbg2.drawerOpen
  };

  console.log("MOBILE_POPUP_RESULT:" + JSON.stringify({
    firstTap: firstTapResult,
    emptyTap: emptyTapResult,
    secondTap: secondTapResult
  }));
  process.exit(0);
}, 80);
"""

    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f_script:
        f_script.write(main_script)
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
        assert proc.returncode == 0, f"Mobile node popup test runner crashed:\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
        match = re.search(r"MOBILE_POPUP_RESULT:(.*)", proc.stdout)
        assert match is not None, f"Output did not contain MOBILE_POPUP_RESULT:\n{proc.stdout}"
        data = json.loads(match.group(1))

        # 1. First tap: popup must be shown, content must contain node label and action, drawer remains closed
        first = data["firstTap"]
        assert first["popDisplay"] == "block"
        assert "엔티티1" in first["popHtml"]
        assert "자세히 보기" in first["popHtml"]
        assert first["selectedNodeId"] == "n1"
        assert first["detailOpen"] is False
        assert first["drawerOpen"] is False

        # 2. Empty tap: popup hidden
        assert data["emptyTap"]["popDisplay"] == "none"

        # 3. Second tap: detail drawer opens
        assert data["secondTap"]["detailOpen"] is True
        assert data["secondTap"]["drawerOpen"] is True
    finally:
        Path(script_file).unlink(missing_ok=True)
        Path(runner_file).unlink(missing_ok=True)











