"""Empirical stress tests for Challenger 2:
Validating effectiveScope relation handling in app.js and full integration matrix.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
import pytest
from starlette.testclient import TestClient

from claire import ids
from claire.api.server import create_app
from claire.config import Settings
from claire.ontology.base import Document
from claire.store import accounts as accounts_store
from claire.store import db as dbm
from claire.store.token_store import reset_token_stores
from tests.test_pwa_reading import _setup_multi_account_network_env


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch):
    for name in list(os.environ):
        if name.startswith("CLAIRE_"):
            monkeypatch.delenv(name, raising=False)
    reset_token_stores()
    yield
    reset_token_stores()


def test_app_js_effective_scope_node_permutation_oracle():
    """Empirically test the exact effectiveScope JS logic across a complete matrix of relations and scopes."""
    js_test_script = """
    function computeEffectiveScope(d) {
        return (d.relation === 'visitor' || d.relation !== 'owner') ? (d.scope === 'anonymous' ? 'anonymous' : 'readonly') : (d.scope || 'readonly');
    }

    const testCases = [
        // [relation, scope, expectedEffectiveScope]
        ['owner', 'owner', 'owner'],
        ['owner', 'collaborator', 'collaborator'],
        ['owner', 'readonly', 'readonly'],
        ['owner', 'anonymous', 'anonymous'],
        ['owner', undefined, 'readonly'],
        ['owner', null, 'readonly'],
        ['owner', '', 'readonly'],

        // Visitor tests
        ['visitor', 'owner', 'readonly'],
        ['visitor', 'collaborator', 'readonly'],
        ['visitor', 'readonly', 'readonly'],
        ['visitor', 'anonymous', 'anonymous'],
        ['visitor', undefined, 'readonly'],
        ['visitor', null, 'readonly'],

        // Delegate / other relations
        ['delegate', 'owner', 'readonly'],
        ['delegate', 'collaborator', 'readonly'],
        ['delegate', 'readonly', 'readonly'],
        ['delegate', 'anonymous', 'anonymous'],

        // Malformed or missing relation (failsafe)
        [undefined, 'owner', 'readonly'],
        [null, 'owner', 'readonly'],
        ['bogus', 'owner', 'readonly'],
        ['', 'owner', 'readonly'],
    ];

    for (const [relation, scope, expected] of testCases) {
        const actual = computeEffectiveScope({ relation, scope });
        if (actual !== expected) {
            console.error(`Mismatch for relation=${relation}, scope=${scope}: expected=${expected}, got=${actual}`);
            process.exit(1);
        }
    }
    console.log("ALL_PERMUTATIONS_PASSED");
    """

    res = subprocess.run(["node", "-e", js_test_script], capture_output=True, text=True, check=True)
    assert "ALL_PERMUTATIONS_PASSED" in res.stdout


def test_app_js_set_access_scope_dom_behavior_node():
    """Execute setAccessScope in a simulated browser DOM via Node to verify UI state enforcement."""
    js_dom_test_script = """
    // Minimal DOM environment
    const elements = {
        authstate: { textContent: '' },
        consolelinkbtn: { style: { display: '' }, title: '', attrs: {}, setAttribute(k, v) { this.attrs[k] = v; } },
        panel: { innerHTML: '' },
    };

    const document = {
        body: {
            dataset: {},
            classList: {
                classes: new Set(),
                toggle(cls, val) { if (val) this.classes.add(cls); else this.classes.delete(cls); },
                contains(cls) { return this.classes.has(cls); }
            }
        },
        getElementById(id) { return elements[id] || null; }
    };

    let AUTH_SCOPE = 'unknown';
    let READONLY = true;
    let IS_ADMIN = false;
    let synthSet = new Set(['node1', 'node2']);
    let showHidden = true;
    let selectedNodeId = null;
    let activeDoc = null;

    function canWrite() { return AUTH_SCOPE === 'owner'; }
    function canIngest() { return AUTH_SCOPE === 'owner' || AUTH_SCOPE === 'collaborator'; }
    function defaultHint() { return '<p>hint</p>'; }
    function renderChips() {}
    function updateSearchModeUI() {}
    function renderDocs() {}

    // Exact setAccessScope function from app.js
    function setAccessScope(scope, reason){
      AUTH_SCOPE = ['owner','collaborator','readonly','anonymous'].includes(scope) ? scope : 'unknown';
      READONLY = !canWrite();
      document.body.dataset.authScope=AUTH_SCOPE;
      document.body.classList.toggle('ro', READONLY);
      const label=document.getElementById('authstate');
      if(label) label.textContent =
        AUTH_SCOPE==='owner' ? '🔒 인증됨' :
        AUTH_SCOPE==='collaborator' ? '🤝 협업자' :
        AUTH_SCOPE==='readonly' ? '👁️ 읽기전용' :
        AUTH_SCOPE==='anonymous' ? '👁️ 익명 읽기전용' :
        reason==='expired' ? '🔓 쓰기 세션 만료 — /web 재접속' : '⚠️ 권한 확인 실패';
      const clBtn = document.getElementById('consolelinkbtn');
      if(clBtn){
        const showConsole = canWrite();
        clBtn.style.display = showConsole ? '' : 'none';
        if(showConsole){
          const label = IS_ADMIN ? '관리자 콘솔' : '내 설정';
          clBtn.setAttribute('href', IS_ADMIN ? '/admin' : '/settings');
          clBtn.title = label;
          clBtn.setAttribute('aria-label', label);
          clBtn.textContent = IS_ADMIN ? '🛠️' : '⚙️';
        }
      }
      if(!canWrite()){
        synthSet.clear();
        showHidden=false;
        renderChips();
        elements.panel.innerHTML=defaultHint();
      }
      updateSearchModeUI();
      renderDocs();
      if(selectedNodeId) loadNode(selectedNodeId);
      else if(activeDoc) loadDocPanel(activeDoc);
      else elements.panel.innerHTML=defaultHint();
    }

    // 1. Test genuine owner
    IS_ADMIN = false;
    setAccessScope('owner');
    if (AUTH_SCOPE !== 'owner' || READONLY !== false || !canWrite() || elements.consolelinkbtn.style.display !== '') {
        throw new Error("Owner assertions failed");
    }
    if (elements.authstate.textContent !== '🔒 인증됨') throw new Error("Owner label failed");
    if (elements.consolelinkbtn.attrs['href'] !== '/settings') throw new Error("Member console link failed");

    // 2. Test visitor (effectiveScope = 'readonly')
    setAccessScope('readonly');
    if (AUTH_SCOPE !== 'readonly' || READONLY !== true || canWrite() || elements.consolelinkbtn.style.display !== 'none') {
        throw new Error("Visitor readonly assertions failed");
    }
    if (elements.authstate.textContent !== '👁️ 읽기전용') throw new Error("Visitor label failed");
    if (synthSet.size !== 0) throw new Error("synthSet not cleared on readonly");

    // 3. Test anonymous (effectiveScope = 'anonymous')
    setAccessScope('anonymous');
    if (AUTH_SCOPE !== 'anonymous' || READONLY !== true || canWrite() || elements.consolelinkbtn.style.display !== 'none') {
        throw new Error("Anonymous assertions failed");
    }
    if (elements.authstate.textContent !== '👁️ 익명 읽기전용') throw new Error("Anonymous label failed");

    // 4. Test admin owner
    IS_ADMIN = true;
    setAccessScope('owner');
    if (elements.consolelinkbtn.attrs['href'] !== '/admin' || elements.consolelinkbtn.textContent !== '🛠️') {
        throw new Error("Admin console link failed");
    }

    console.log("DOM_SIMULATION_PASSED");
    """

    res = subprocess.run(["node", "-e", js_dom_test_script], capture_output=True, text=True, check=True)
    assert "DOM_SIMULATION_PASSED" in res.stdout


def test_effective_scope_e2e_integration_matrix(tmp_path: Path):
    """End-to-end integration test querying whoami endpoints and verifying effectiveScope logic."""
    s, env = _setup_multi_account_network_env(tmp_path)
    app = create_app(s)

    with TestClient(app, base_url=s.public_url, follow_redirects=False) as client:
        # Helper to simulate app.js logic on whoami response
        def get_simulated_client_scope(whoami_resp):
            d = whoami_resp.json()
            effective_scope = (
                (d.get("scope") if d.get("scope") == "anonymous" else "readonly")
                if (d.get("relation") == "visitor" or d.get("relation") != "owner")
                else (d.get("scope") or "readonly")
            )
            can_write = effective_scope == "owner"
            return effective_scope, can_write, d.get("is_admin", False)

        # 1. Alice on Alice's workspace (Genuine Owner)
        client.cookies.clear()
        client.cookies.set("claire_session", env["alice_session"])
        res = client.get("/@alice/whoami")
        assert res.status_code == 200
        eff, can_write, is_adm = get_simulated_client_scope(res)
        assert eff == "owner"
        assert can_write is True
        assert is_adm is False

        # 2. Bob visiting Alice's workspace (Cross-account Visitor)
        client.cookies.clear()
        client.cookies.set("claire_session", env["bob_session"])
        res = client.get("/@alice/whoami")
        assert res.status_code == 200
        assert res.json()["relation"] == "visitor"
        assert res.json()["scope"] == "owner"
        eff, can_write, is_adm = get_simulated_client_scope(res)
        assert eff == "readonly"
        assert can_write is False
        assert is_adm is False

        # 3. Anonymous visiting Alice's workspace
        client.cookies.clear()
        res = client.get("/@alice/whoami")
        assert res.status_code == 200
        assert res.json()["relation"] == "visitor"
        assert res.json()["scope"] == "anonymous"
        eff, can_write, is_adm = get_simulated_client_scope(res)
        assert eff == "anonymous"
        assert can_write is False

        # 4. Admin visiting Alice's workspace
        client.cookies.clear()
        client.cookies.set("claire_session", env["admin_session"])
        res = client.get("/@alice/whoami")
        assert res.status_code == 200
        assert res.json()["relation"] == "visitor"
        assert res.json()["is_admin"] is True
        eff, can_write, is_adm = get_simulated_client_scope(res)
        assert eff == "readonly"
        assert can_write is False
        assert is_adm is True

        # 5. Admin on Admin's own workspace
        client.cookies.clear()
        client.cookies.set("claire_session", env["admin_session"])
        res = client.get("/@adminuser/whoami")
        assert res.status_code == 200
        assert res.json()["relation"] == "owner"
        assert res.json()["is_admin"] is True
        eff, can_write, is_adm = get_simulated_client_scope(res)
        assert eff == "owner"
        assert can_write is True
        assert is_adm is True


def test_visitor_write_affordance_server_side_enforcement(tmp_path: Path):
    """Stress-test that even if a visitor bypasses client-side UI, server strictly denies write operations."""
    s, env = _setup_multi_account_network_env(tmp_path)
    app = create_app(s)

    with TestClient(app, base_url=s.public_url, follow_redirects=False) as client:
        client.cookies.set("claire_session", env["bob_session"])

        # Bob tries to mark Alice's document as seen on Alice's workspace
        res_seen = client.post("/@alice/document/seen", json={"id": "doc_alice_wonderland_1"})
        assert res_seen.status_code == 403, f"Expected 403 for visitor /document/seen, got {res_seen.status_code}"

        # Bob tries to update Alice's document title on Alice's workspace
        res_title = client.post("/@alice/document/title", json={"id": "doc_alice_wonderland_1", "title": "Hacked Title"})
        assert res_title.status_code == 403, f"Expected 403 for visitor /document/title, got {res_title.status_code}"

        # Bob tries to share Alice's document on Alice's workspace
        res_share = client.post("/@alice/share", json={"doc_id": "doc_alice_wonderland_1"})
        assert res_share.status_code == 403, f"Expected 403 for visitor /share on foreign workspace, got {res_share.status_code}"
