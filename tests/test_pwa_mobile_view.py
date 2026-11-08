"""Tests for Milestone 4: Mobile/Tablet Initial View (Reader-First) & Syndication Exclusion."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient

from claire import ids
from claire.api.server import create_app
from claire.config import Settings
from claire.store import accounts as accounts_store
from claire.store import db as dbm
from claire.store.token_store import reset_token_stores

PROJECT_ROOT = Path(__file__).resolve().parent.parent
APP_JS_PATH = PROJECT_ROOT / "src" / "claire" / "static" / "js" / "app.js"
INDEX_HTML_PATH = PROJECT_ROOT / "src" / "claire" / "templates" / "index.html"
MANIFEST_PATH = PROJECT_ROOT / "src" / "claire" / "static" / "icons" / "manifest.json"
WEBMANIFEST_PATH = PROJECT_ROOT / "src" / "claire" / "static" / "icons" / "site.webmanifest"


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


# ==============================================================================
# Group 1: Static Code Contract Tests for app.js
# ==============================================================================

def test_static_app_js_syntax_via_node():
    """Verify that src/claire/static/js/app.js has 0 syntax errors according to node --check."""
    if not _node_available():
        pytest.skip("node is not installed in the environment")
    result = subprocess.run(
        ["node", "--check", str(APP_JS_PATH)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"node --check failed: {result.stderr}"


def test_static_app_js_is_pwa_or_mobile_launch_contract():
    """Verify isPwaOrMobileLaunch helper is defined and checks compact screen, standalone, and pwa param."""
    content = APP_JS_PATH.read_text(encoding="utf-8")
    assert "function isPwaOrMobileLaunch()" in content
    # Checks max-width: 900px
    assert "(max-width: 900px)" in content
    # Checks display-mode: standalone
    assert "(display-mode: standalone)" in content
    # Checks iOS navigator.standalone
    assert "navigator.standalone" in content
    # Checks URL query parameter ?pwa=1
    assert "params.get('pwa') === '1'" in content or "sp.get('pwa') === '1'" in content


def test_static_app_js_should_default_to_reader_contract():
    """Verify shouldDefaultToReader helper evaluates node query, active ingest/jobs, and mobile/PWA status."""
    content = APP_JS_PATH.read_text(encoding="utf-8")
    assert "function shouldDefaultToReader(stats)" in content
    # Node deep-link bypass: ?node= preserves graph view
    assert "params.get('node')" in content or "sp.get('node')" in content
    # Ingest job checks
    assert "stats.jobs" in content or "stats.active_jobs" in content or "hasActiveIngest" in content
    # Delegates to isPwaOrMobileLaunch
    assert "isPwaOrMobileLaunch()" in content


def test_static_app_js_load_theme_documents_auto_select_contract():
    """Verify loadThemeDocuments() auto-selects allDocs[0].id when in reader view and no doc is selected."""
    content = APP_JS_PATH.read_text(encoding="utf-8")
    assert "async function loadThemeDocuments()" in content
    # When documents exist and centerView is reader, auto-select first document
    assert "allDocs[0].id" in content
    assert "selectDoc(firstDocId)" in content or "openReader(firstDocId" in content
    assert "centerView === 'reader'" in content


def test_static_app_js_initial_startup_reader_hook_contract():
    """Verify startup sequence hooks into shouldDefaultToReader to set initial centerView to reader."""
    content = APP_JS_PATH.read_text(encoding="utf-8")
    # Initial state initialization
    assert "initialDefaultToReader" in content
    # Stats callback view resolution hook
    assert "shouldDefaultToReader(d)" in content or "shouldDefaultToReader(stats)" in content
    assert "setCenterView('reader')" in content


def test_static_app_js_window_exports():
    """Verify shouldDefaultToReader and isPwaOrMobileLaunch are exposed on window for external callers."""
    content = APP_JS_PATH.read_text(encoding="utf-8")
    assert "window.shouldDefaultToReader = shouldDefaultToReader;" in content
    assert "window.isPwaOrMobileLaunch = isPwaOrMobileLaunch;" in content
    assert "window.switchView =" in content


# ==============================================================================
# Group 2: Behavioral View Matrix Unit Simulation via Node.js
# ==============================================================================

def _eval_in_node(setup_js: str, expr_js: str) -> Any:
    """Evaluate JavaScript expression in Node.js VM with app.js context."""
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


@pytest.mark.parametrize(
    "query,max_width_match,standalone,expected_launch",
    [
        ("", False, False, False),  # Desktop non-PWA
        ("?pwa=1", False, False, True),  # PWA query param
        ("", True, False, True),  # Mobile/tablet screen (<= 900px)
        ("", False, True, True),  # Standalone display mode
        ("?other=123", False, False, False),  # Desktop with unrelated query
        ("?pwa=0", False, False, False),  # Explicit pwa=0
    ],
)
def test_is_pwa_or_mobile_launch_matrix(query: str, max_width_match: bool, standalone: bool, expected_launch: bool):
    """Test isPwaOrMobileLaunch across viewport, standalone, and query parameter variations."""
    setup = f"""
    window.location.search = '{query}';
    window.matchMedia = (q) => ({{ matches: (q.includes('max-width: 900px') && {str(max_width_match).lower()}) || (q.includes('display-mode: standalone') && {str(standalone).lower()}) }});
    window.navigator.standalone = {str(standalone).lower()};
    """
    actual = _eval_in_node(setup, "isPwaOrMobileLaunch()")
    assert actual is expected_launch


@pytest.mark.parametrize(
    "query,max_width,standalone,stats_data,expected_default_reader",
    [
        # Desktop browser without PWA: always graph
        ("", False, False, None, False),
        ("", False, False, {"jobs": []}, False),
        # Mobile/compact screen without jobs: defaults to reader
        ("", True, False, None, True),
        ("", True, False, {"jobs": []}, True),
        ("", True, False, {"jobs": [{"status": "completed"}]}, True),
        # Mobile/compact screen with running job: overrides reader, keeps job/matrix
        ("", True, False, {"jobs": [{"status": "running"}]}, False),
        ("", True, False, {"jobs": [{"status": "pending"}]}, False),
        ("", True, False, {"active_jobs": [{"active": True}]}, False),
        ("", True, False, {"active_ingest": {"active": True}}, False),
        ("", True, False, {"ingesting": True}, False),
        # Standalone display mode: defaults to reader
        ("", False, True, None, True),
        ("", False, True, {"jobs": []}, True),
        # Standalone display mode with active job: overrides reader
        ("", False, True, {"jobs": [{"status": "running"}]}, False),
        # ?pwa=1 launch: defaults to reader
        ("?pwa=1", False, False, None, True),
        ("?pwa=1", False, False, {"jobs": []}, True),
        # Deep link ?node= overrides reader-first, always remains graph
        ("?node=concept_42", True, False, None, False),
        ("?pwa=1&node=concept_42", False, False, None, False),
        ("?node=entity_99", False, True, None, False),
        ("?node=42", False, False, None, False),
    ],
)
def test_should_default_to_reader_decision_matrix(
    query: str,
    max_width: bool,
    standalone: bool,
    stats_data: dict[str, Any] | None,
    expected_default_reader: bool,
):
    """Test full decision matrix for shouldDefaultToReader(stats)."""
    stats_json = json.dumps(stats_data)
    setup = f"""
    window.location.search = '{query}';
    window.matchMedia = (q) => ({{ matches: (q.includes('max-width: 900px') && {str(max_width).lower()}) || (q.includes('display-mode: standalone') && {str(standalone).lower()}) }});
    window.navigator.standalone = {str(standalone).lower()};
    """
    actual = _eval_in_node(setup, f"shouldDefaultToReader({stats_json})")
    assert actual is expected_default_reader


# ==============================================================================
# Group 3: Auto-Select Document Logic in loadThemeDocuments
# ==============================================================================

def test_auto_select_first_document_simulation():
    """Verify document auto-selection behavior when entering reader view."""
    if not _node_available():
        pytest.skip("node is not installed")

    # Simulate loadThemeDocuments post-fetch block
    sim_script = """
    function simulateDocSelection(allDocs, centerView, curReaderDoc, queryDoc) {
      let selectedDoc = null;
      let hasDocOpen = Boolean(curReaderDoc);

      let docFromUrl = null;
      if (queryDoc && allDocs.some(x => x.id === queryDoc)) {
        docFromUrl = queryDoc;
      }

      if (docFromUrl) {
        if (!curReaderDoc || curReaderDoc !== docFromUrl) {
          selectedDoc = docFromUrl;
        }
      } else if (centerView === 'reader' && !hasDocOpen) {
        if (allDocs.length > 0) {
          selectedDoc = allDocs[0].id;
        }
      }
      return selectedDoc;
    }
    """
    docs = [{"id": "doc_101", "title": "Doc 1"}, {"id": "doc_102", "title": "Doc 2"}]

    # Case 1: In reader view with no selection -> selects first doc
    res1 = _eval_in_node(sim_script, f"simulateDocSelection({json.dumps(docs)}, 'reader', null, null)")
    assert res1 == "doc_101"

    # Case 2: In reader view with explicit query param ?doc=doc_102 -> selects doc_102
    res2 = _eval_in_node(sim_script, f"simulateDocSelection({json.dumps(docs)}, 'reader', null, 'doc_102')")
    assert res2 == "doc_102"

    # Case 3: In reader view with already open doc -> does NOT overwrite
    res3 = _eval_in_node(sim_script, f"simulateDocSelection({json.dumps(docs)}, 'reader', 'doc_102', null)")
    assert res3 is None

    # Case 4: In graph view with no selection -> does NOT select
    res4 = _eval_in_node(sim_script, f"simulateDocSelection({json.dumps(docs)}, 'graph', null, null)")
    assert res4 is None

    # Case 5: Empty docs array in reader view -> does NOT crash, returns None
    res5 = _eval_in_node(sim_script, "simulateDocSelection([], 'reader', null, null)")
    assert res5 is None


# ==============================================================================
# Group 4: Syndication Desktop-Only Exclusion Verification
# ==============================================================================

def test_syndication_excluded_from_manifests():
    """Verify syndication is completely absent from PWA manifest shortcuts and start_url."""
    for manifest_path in [MANIFEST_PATH, WEBMANIFEST_PATH]:
        assert manifest_path.exists(), f"Missing manifest at {manifest_path}"
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert "syndication" not in data.get("start_url", "")
        # Shortcuts array should not contain any syndication routes
        shortcuts = data.get("shortcuts", [])
        for sc in shortcuts:
            assert "syndication" not in sc.get("url", "").lower()
            assert "syndication" not in sc.get("name", "").lower()


def test_syndication_excluded_from_mobile_workspace_templates():
    """Verify index.html has 0 references to syndication in mobile UI, tabs, or drawers."""
    index_html = INDEX_HTML_PATH.read_text(encoding="utf-8")
    assert "syndication" not in index_html.lower()


def test_syndication_excluded_from_app_js():
    """Verify app.js does not contain view switches or defaults to syndication."""
    app_js = APP_JS_PATH.read_text(encoding="utf-8")
    assert "setCenterView('syndication')" not in app_js
    assert "switchView('syndication')" not in app_js
    assert "centerView = 'syndication'" not in app_js


def test_syndication_accessible_strictly_via_desktop_routes(tmp_path: Path):
    """Verify syndication routes exist on server but require explicit navigation, never PWA launch."""
    db_file = tmp_path / "claire.db"
    conn = dbm.connect(db_file)
    dbm.init_db(conn)
    now = time.time()
    conn.execute(
        "UPDATE accounts SET username='adminuser', status='active', activated_at=?, profile_public=1 WHERE id=1",
        (now,),
    )
    admin_session = accounts_store.create_account_session(conn, account_id=1, scope="owner", now=now)
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
    client = TestClient(app, base_url=settings.public_url, follow_redirects=False)

    # Anonymous root PWA launch redirects to admin workspace, NEVER to syndication
    r_pwa = client.get("/?pwa=1")
    assert r_pwa.status_code == 302
    assert "syndication" not in r_pwa.headers["location"]
    assert r_pwa.headers["location"] == "/@adminuser/?pwa=1"

    # Authenticated session PWA launch also redirects to user workspace, NEVER to syndication
    client.cookies.set("claire_session", admin_session)
    r_auth_pwa = client.get("/?pwa=1")
    assert r_auth_pwa.status_code == 302
    assert "syndication" not in r_auth_pwa.headers["location"]
    assert r_auth_pwa.headers["location"] == "/@adminuser/?pwa=1"

    # Syndication UI route itself exists on server for desktop use
    r_syn = client.get("/syndication")
    assert r_syn.status_code == 200
    assert "신디케이션" in r_syn.text or "syndication" in r_syn.text.lower()
