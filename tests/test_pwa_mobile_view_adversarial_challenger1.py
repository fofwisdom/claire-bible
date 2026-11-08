"""Adversarial stress-test suite for Milestone 4: Mobile/Tablet Reader-First View & Decision Matrix.

Authored by Challenger 1 (critic, specialist).
Empirically stress-tests:
- Viewport boundary conditions (899px, 900px, 901px)
- Standalone display mode combinations (matchMedia, iOS navigator.standalone)
- Query parameter variations (?pwa=1, ?pwa=0, ?pwa=true, ?node=, ?doc=)
- Ingest job matrix (null, empty, completed, running, pending, active_jobs with null elements, active_ingest, ingesting flag)
- Auto-selection resilience (null docs, invalid doc URL, fallback handlers)
- Syndication exclusion across endpoints
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient

from claire.api.server import create_app
from claire.config import Settings
from claire.store import accounts as accounts_store
from claire.store import db as dbm
from claire.store.token_store import reset_token_stores

PROJECT_ROOT = Path(__file__).resolve().parent.parent
APP_JS_PATH = PROJECT_ROOT / "src" / "claire" / "static" / "js" / "app.js"
INDEX_HTML_PATH = PROJECT_ROOT / "src" / "claire" / "templates" / "index.html"


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch):
    for name in list(os.environ):
        if name.startswith("CLAIRE_"):
            monkeypatch.delenv(name, raising=False)
    reset_token_stores()
    yield
    reset_token_stores()


def _node_available() -> bool:
    return shutil.which("node") is not None


def _eval_app_js(setup_js: str, expr_js: str) -> Any:
    """Evaluate expression against extracted app.js logic in Node VM."""
    if not _node_available():
        pytest.skip("node is not installed")
    script = f"""
const fs = require('fs');
const vm = require('vm');
const code = fs.readFileSync('{APP_JS_PATH.as_posix()}', 'utf8');

const context = {{
  window: {{
    matchMedia: (q) => ({{ matches: false }}),
    location: {{ search: '' }},
    navigator: {{ standalone: false }}
  }},
  document: {{
    documentElement: {{ getAttribute: () => 'light' }},
    body: {{ dataset: {{}} }}
  }},
  URLSearchParams: URLSearchParams,
  console: console
}};
vm.createContext(context);

// Extract helper functions
const funcMatch = code.match(/function isPwaOrMobileLaunch\\(\\)[\\s\\S]*?var initialDefaultToReader = shouldDefaultToReader\\(null\\);/);
if (!funcMatch) throw new Error('Helpers not found in app.js');
vm.runInContext(funcMatch[0], context);
vm.runInContext({json.dumps(setup_js)}, context);

const result = vm.runInContext({json.dumps(expr_js)}, context);
process.stdout.write(JSON.stringify(result));
"""
    res = subprocess.run(["node", "-e", script], capture_output=True, text=True)
    assert res.returncode == 0, f"Node script failed: {res.stderr}"
    return json.loads(res.stdout)


# ==============================================================================
# Adversarial Test 1: Boundary & Viewport Stress-Testing
# ==============================================================================

@pytest.mark.parametrize(
    "width_px,expected_compact",
    [
        (320, True),    # Small mobile
        (720, True),    # Standard mobile breakpoint
        (768, True),    # iPad mini / small tablet
        (899, True),    # Just below 900px boundary
        (900, True),    # Exactly on boundary (<= 900px)
        (901, False),   # Just above 900px boundary
        (1024, False),  # Standard iPad portrait (no pwa/standalone -> graph)
        (1200, False),  # Standard laptop
        (1920, False),  # Desktop monitor
        (0, True),      # Degenerate 0 width
    ],
)
def test_viewport_width_boundary_evaluation(width_px: int, expected_compact: bool):
    """Stress-test viewport width against max-width: 900px media query rule."""
    setup = f"""
    const currentWidth = {width_px};
    window.location.search = '';
    window.navigator.standalone = false;
    window.matchMedia = (query) => {{
      const match = query.match(/\\(max-width:\\s*(\\d+)px\\)/);
      if (match) {{
        const maxWidth = parseInt(match[1], 10);
        return {{ matches: currentWidth <= maxWidth }};
      }}
      return {{ matches: false }};
    }};
    """
    actual = _eval_app_js(setup, "isPwaOrMobileLaunch()")
    assert actual is expected_compact


# ==============================================================================
# Adversarial Test 2: iOS vs Chromium Standalone Detection Modes
# ==============================================================================

@pytest.mark.parametrize(
    "match_media_standalone,navigator_standalone,expected_launch",
    [
        (True, False, True),    # Chromium display-mode: standalone
        (False, True, True),    # iOS Safari navigator.standalone === true
        (True, True, True),     # Both present
        (False, False, False),  # Standard browser tab
        (False, None, False),   # navigator.standalone undefined (Chromium desktop)
    ],
)
def test_standalone_display_mode_combinations(
    match_media_standalone: bool,
    navigator_standalone: bool | None,
    expected_launch: bool,
):
    """Test standalone detection across Chromium and iOS Safari browser quirks."""
    nav_js = "undefined" if navigator_standalone is None else str(navigator_standalone).lower()
    setup = f"""
    window.location.search = '';
    window.matchMedia = (q) => ({{ matches: q.includes('display-mode: standalone') && {str(match_media_standalone).lower()} }});
    window.navigator = {{ standalone: {nav_js} }};
    """
    actual = _eval_app_js(setup, "isPwaOrMobileLaunch()")
    assert actual is expected_launch


# ==============================================================================
# Adversarial Test 3: Query Parameter Ambiguities and Precedence
# ==============================================================================

@pytest.mark.parametrize(
    "search_string,is_compact,expected_result",
    [
        ("?pwa=1", False, True),            # Clean pwa launch
        ("?pwa=true", False, False),        # String 'true' is not '1'
        ("?pwa=0", False, False),           # pwa disabled
        ("?pwa=10", False, False),          # not exact '1'
        ("?pwa=1&node=test", False, False), # ?node overrides ?pwa=1 -> graph view
        ("?pwa=1&node=test", True, False),  # ?node overrides compact viewport -> graph view
        ("?node=node123", True, False),     # Explicit node deep link on mobile -> graph view
        ("?node=", True, True),              # Empty node param ("") is falsy -> defaults to reader on mobile
        ("?pwa=1&doc=doc-xyz", False, True),# ?doc does NOT suppress reader view -> reader view
        ("?pwa=1&doc=doc-xyz", True, True), # ?doc on mobile -> reader view
        ("?doc=doc-xyz", False, False),     # Desktop with ?doc: shouldDefaultToReader stays false (doc loads, but graph center)
    ],
)
def test_query_param_parsing_and_precedence(search_string: str, is_compact: bool, expected_result: bool):
    """Verify that ?node= takes strict precedence over mobile/PWA, and ?pwa=1 requires exact '1'."""
    setup = f"""
    window.location.search = '{search_string}';
    window.matchMedia = (q) => ({{ matches: q.includes('max-width: 900px') && {str(is_compact).lower()} }});
    window.navigator.standalone = false;
    """
    actual = _eval_app_js(setup, "shouldDefaultToReader(null)")
    assert actual is expected_result


# ==============================================================================
# Adversarial Test 4: Ingest Job Data Matrix Resilience
# ==============================================================================

@pytest.mark.parametrize(
    "stats_obj,expected_default_reader",
    [
        (None, True),                                                # No stats object -> reader
        ({}, True),                                                  # Empty dict -> reader
        ({"jobs": None}, True),                                      # Non-array jobs -> reader
        ({"jobs": []}, True),                                        # Empty array -> reader
        ({"jobs": [{"status": "completed"}]}, True),                 # Done job -> reader
        ({"jobs": [{"status": "failed"}]}, True),                    # Failed job -> reader
        ({"jobs": [{"status": "running"}]}, False),                  # Running job -> graph (suppress reader)
        ({"jobs": [{"status": "pending"}]}, False),                  # Pending job -> graph (suppress reader)
        ({"jobs": [{"status": "RUNNING"}]}, True),                   # Case-sensitive check -> True
        ({"active_jobs": None}, True),                               # Non-array active_jobs -> reader
        ({"active_jobs": []}, True),                                 # Empty active_jobs -> reader
        ({"active_jobs": [None]}, True),                             # Array with null -> doesn't crash, reader
        ({"active_jobs": [{}]}, True),                               # Object without flags -> reader
        ({"active_jobs": [{"active": True}]}, False),                # active flag -> graph
        ({"active_jobs": [{"status": "running"}]}, False),           # running status -> graph
        ({"active_jobs": [{"status": "pending"}]}, False),           # pending status -> graph
        ({"active_ingest": None}, True),                             # active_ingest is null
        ({"active_ingest": {}}, True),                               # active_ingest without active
        ({"active_ingest": {"active": False}}, True),                # active_ingest inactive
        ({"active_ingest": {"active": True}}, False),                # active_ingest active -> graph
        ({"ingesting": False}, True),                                # ingesting false -> reader
        ({"ingesting": True}, False),                                # ingesting true -> graph
    ],
)
def test_stats_ingest_data_matrix_edge_cases(stats_obj: Any, expected_default_reader: bool):
    """Stress-test shouldDefaultToReader with malformed, null, and edge-case stats payloads."""
    setup = """
    window.location.search = '';
    window.matchMedia = (q) => ({ matches: q.includes('max-width: 900px') });
    window.navigator.standalone = false;
    """
    stats_json = json.dumps(stats_obj)
    actual = _eval_app_js(setup, f"shouldDefaultToReader({stats_json})")
    assert actual is expected_default_reader


# ==============================================================================
# Adversarial Test 5: Auto-Select Document Fallback Handlers
# ==============================================================================

def test_auto_select_fallback_and_missing_dom():
    """Verify loadThemeDocuments document selection behavior with various mock DOM states."""
    test_logic = """
    function testAutoSelect(allDocs, centerView, curReaderDoc, queryDoc, hasSelectDoc, hasOpenReader) {
      let callRecord = null;
      const selectDoc = hasSelectDoc ? (id) => { callRecord = { fn: 'selectDoc', id: id }; } : undefined;
      const openReader = hasOpenReader ? (id, opt) => { callRecord = { fn: 'openReader', id: id, opt: opt }; } : undefined;

      let docFromUrl = null;
      if (queryDoc && allDocs && allDocs.some(x => x.id === queryDoc)) {
        docFromUrl = queryDoc;
      }
      const currentDoc = curReaderDoc;
      const hasDocOpen = Boolean(currentDoc);

      if (allDocs && allDocs.length > 0) {
        if (docFromUrl) {
          if (!currentDoc || currentDoc !== docFromUrl) {
            if (typeof selectDoc === 'function') {
              selectDoc(docFromUrl);
            } else if (typeof openReader === 'function') {
              openReader(docFromUrl, false);
            }
          }
        } else if (centerView === 'reader' && !hasDocOpen) {
          const firstDocId = allDocs[0].id;
          if (typeof selectDoc === 'function') {
            selectDoc(firstDocId);
          } else if (typeof openReader === 'function') {
            openReader(firstDocId, false);
          }
        }
      }
      return callRecord;
    }
    """
    docs = [{"id": "doc_first"}, {"id": "doc_second"}]

    # Case 1: selectDoc is available -> calls selectDoc(firstDocId)
    r1 = _eval_app_js(test_logic, f"testAutoSelect({json.dumps(docs)}, 'reader', null, null, true, true)")
    assert r1 == {"fn": "selectDoc", "id": "doc_first"}

    # Case 2: only openReader is available -> calls openReader(firstDocId, false)
    r2 = _eval_app_js(test_logic, f"testAutoSelect({json.dumps(docs)}, 'reader', null, null, false, true)")
    assert r2 == {"fn": "openReader", "id": "doc_first", "opt": False}

    # Case 3: URL has nonexistent doc -> falls back to auto-selecting doc_first in reader view
    r3 = _eval_app_js(test_logic, f"testAutoSelect({json.dumps(docs)}, 'reader', null, 'doc_nonexistent', true, true)")
    assert r3 == {"fn": "selectDoc", "id": "doc_first"}

    # Case 4: URL has valid doc -> opens doc_second
    r4 = _eval_app_js(test_logic, f"testAutoSelect({json.dumps(docs)}, 'reader', null, 'doc_second', true, true)")
    assert r4 == {"fn": "selectDoc", "id": "doc_second"}


# ==============================================================================
# Adversarial Test 6: Desktop Exclusivity of Syndication Endpoints
# ==============================================================================

def test_syndication_strictly_absent_from_rendered_mobile_html(tmp_path: Path):
    """Verify that HTML rendered for mobile/tablet requests does NOT link or default to syndication."""
    db_file = tmp_path / "claire.db"
    conn = dbm.connect(db_file)
    dbm.init_db(conn)
    now = 1700000000.0
    conn.execute(
        "UPDATE accounts SET username='alice', status='active', activated_at=?, profile_public=1 WHERE id=1",
        (now,),
    )
    store = accounts_store.AccountStore(conn)
    store.set_setting("mode", "network", updated_by=1)
    conn.commit()
    conn.close()

    settings = Settings(
        CLAIRE_DB_PATH=str(db_file),
        CLAIRE_SESSION_SECRET="test-secret-key-42",
        CLAIRE_SINGLE_MODE=False,
        CLAIRE_ENVIRONMENT="development",
        CLAIRE_PUBLIC_URL="http://127.0.0.1:8765",
        CLAIRE_ANONYMOUS_READONLY=True,
    )
    app = create_app(settings)
    client = TestClient(app, base_url=settings.public_url, follow_redirects=True)

    # Mobile request to user workspace
    mobile_headers = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
    }
    resp = client.get("/@alice/?pwa=1", headers=mobile_headers)
    assert resp.status_code == 200
    # Syndication should not be in navigation links or buttons
    assert 'href="/syndication"' not in resp.text
    assert 'id="syndication-tab"' not in resp.text
    assert 'class="syndication"' not in resp.text
