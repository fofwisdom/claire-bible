"""The federation of shared knowledge between two real Claire servers and their real sidecars (KNOWLEDGE_FEDERATION_DESIGN.md units KF2 to KF4).

The tests of the units (``tests/test_knowledge_federation_kf*_e2e.py``) simulate the sidecars and the network. This test runs two Claire processes, A (``https://a.claire.test``) and B (``https://b.claire.test``), and the real sidecar code (``sidecar/fedify/e2e/two_servers_e2e.ts``): only the network between the two sidecars is in one Deno process.

Steps (the criterion of unit KF4): A reads the server and the ``/kb`` collection of B; a theme of A has the shared knowledge base Cities of B; ben of A contributes a document to the theme, and the account actor of ben sends ``Offer`` (and the server actor of A follows Cities); a moderator of B approves; the knowledge base actor sends ``Accept``; the submission page of A shows the decision. Then the moderator gives ben the role collaborator (``Grant``), and the next contribution of ben needs no approval.

The same as ``tests/test_fedify_sidecar.py``: it needs Deno, and it is skipped without it.
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import httpx
import pytest

from claire.api.federation_internal import federation_socket_path, service_key_path
from claire.core import ids
from claire.federation.knowledge import known_servers
from claire.store import accounts as accounts_store
from claire.store import themes as themes_store
from tests.test_contributions import EXTRACTION
from tests.test_fedify_sidecar import _clean_env
from tests.test_renditions_exchange import DENO, SIDECAR_DIR, _account, _Server

pytestmark = pytest.mark.skipif(DENO is None, reason="deno is not installed")

BEN_PUBLIC = 116412345678901236


def _run(a: _Server, b: _Server, work: str) -> list[str]:
    """Run the queues of ``work`` (for example ``a:outbox,b:outbox``) until they are empty; return the traffic between the sidecars."""
    sockets = [federation_socket_path(a.data_dir), federation_socket_path(b.data_dir)]
    env = {**_clean_env(), "NO_COLOR": "1", "E2E_WORK": work, "CLAIRE_A_SOCKET": str(sockets[0]), "CLAIRE_A_KEY_FILE": str(service_key_path(a.data_dir)), "CLAIRE_B_SOCKET": str(sockets[1]), "CLAIRE_B_KEY_FILE": str(service_key_path(b.data_dir))}
    command = [DENO, "test", "--frozen", "--allow-net=claire.internal:80," + ",".join(f"unix:{s}" for s in sockets), "--allow-env", f"--allow-read={SIDECAR_DIR},{a.data_dir},{b.data_dir}", "--allow-write=" + ",".join(str(s) for s in sockets), "e2e/two_servers_e2e.ts"]
    run = subprocess.run(command, cwd=SIDECAR_DIR, env=env, capture_output=True, text=True, timeout=300)
    assert run.returncode == 0, f"{work}:\n" + run.stdout[-6000:] + run.stderr[-3000:]
    return json.loads(next(line for line in run.stdout.splitlines() if "TRAFFIC " in line).split("TRAFFIC ", 1)[1])


def _setup(server: _Server, admin: str, accounts: dict[str, int]) -> dict[str, int]:
    conn = server.db()
    try:
        store = accounts_store.AccountStore(conn)
        store.complete_setup(1, username=admin, display_name=admin)
        store.set_setting("mode", "network", updated_by=1)
        made = {name: _account(conn, name, public_id) for name, public_id in accounts.items()}
        conn.commit()
        return made
    finally:
        conn.close()


def _caller(server: _Server, account_id: int):
    """Requests of one account with a session of a sign-in of now (the step-up routes need it)."""
    conn = server.db()
    try:
        token = accounts_store.create_account_session(conn, account_id=account_id, scope="owner", now=time.time())
        conn.commit()
    finally:
        conn.close()

    def call(method: str, path: str, body: dict | None = None) -> httpx.Response:
        headers = {"Host": server.host, "Cookie": f"claire_session={token}"}
        if method != "GET":
            headers["Origin"] = server.origin
        return httpx.request(method, f"http://127.0.0.1:{server.port}{path}", headers=headers, json=body, timeout=30)

    return call


def test_e2e_a_contribution_across_two_real_servers_with_approval_and_a_grant(tmp_path: Path) -> None:
    a, b = _Server(tmp_path, "a"), _Server(tmp_path, "b")
    a_ids = _setup(a, "admin_a", {"ben": BEN_PUBLIC, "ann": 116412345678901237})
    b_ids = _setup(b, "admin_b", {"ann": 216412345678901237})
    ben_actor = f"{a.origin}/ap/actors/{BEN_PUBLIC}"
    # A personal knowledge base of ben with one document and its extraction (the input of a contribution).
    conn = a.db()
    try:
        conn.execute("INSERT INTO knowledge_bases (id, seq, label, is_personal, is_public, created_at, updated_at, account_id, number) VALUES (40, 40, 'ben', 1, 0, 1, 1, ?, 516412345678901240)", (a_ids["ben"],))
        for document_id, title in (("doc-cities", "Cities of Korea"), ("doc-towns", "Towns of Korea")):
            conn.execute("INSERT INTO documents (id, kb_id, url, canonical_url, title, raw_text, hidden, fetched_at, extraction_status, detail) VALUES (?, 40, ?, ?, ?, ?, 0, 1, 'extracted', ?)", (document_id, f"https://example.org/{document_id}", f"https://example.org/{document_id}", title, f"{title}: Seoul and Busan. " * 3, f"= {title}\n\nSeoul and Busan."))
            conn.execute("INSERT INTO extractions (kb_id, document_id, provider, model, prompt_version, raw_response, created_at) VALUES (40, ?, 'mock', 'mock', 'v1', ?, 1)", (document_id, json.dumps(EXTRACTION)))
        known_servers.note_server(conn, b.host, source="admin", added_by=1, now=time.time())
        conn.commit()
    finally:
        conn.close()
    a.start()
    b.start()
    try:
        ben, ann_a = _caller(a, a_ids["ben"]), _caller(a, a_ids["ann"])
        ann_b = _caller(b, b_ids["ann"])
        # B: the shared knowledge base Cities. A: the shared knowledge base Places.
        made = ann_b("POST", "/kb", {"label": "Cities", "knowledge_type": "real"})
        assert made.status_code in (200, 201), made.text
        cities = made.json()["knowledge_base"]
        kb_actor = f"{b.origin}/ap/kb/{cities['number']}"
        places = ann_a("POST", "/kb", {"label": "Places", "knowledge_type": "real"}).json()["knowledge_base"]

        # 1. KF2: A reads the server and the /kb collection of B with requests that its server actor signs.
        traffic = _run(a, b, "a:reads")
        assert any(line.startswith("GET b.claire.test/ap/kb") for line in traffic), traffic
        conn = a.db()
        try:
            remote_id = int(conn.execute("SELECT id FROM kf_remote_knowledge_bases WHERE iri=?", (kb_actor,)).fetchone()[0])
            theme = themes_store.create_theme(conn, label="Places", knowledge_type="real")
            themes_store.add_theme_member(conn, theme_id=theme["id"], kb_id=places["id"], source="admin", pinned=True)
            themes_store.set_admin_remote_member(conn, theme_id=theme["id"], remote_kb_id=remote_id, pinned=True, excluded=False)
            conn.commit()
            theme_number = ids.format_public_id(int(theme["number"]))
        finally:
            conn.close()

        # 2. KF4: ben contributes to the theme; the account actor of ben sends Offer, and the server actor of A follows Cities.
        sent = ben("POST", "/submissions", {"documents": ["doc-cities"], "theme": theme_number})
        assert sent.status_code == 201, sent.text
        submission = sent.json()["submission"]["number"]
        traffic = _run(a, b, "a:outbox,b:outbox")
        assert any(line.startswith(f"POST b.claire.test/ap/kb/{cities['number']}/inbox") for line in traffic), traffic
        pending = ann_b("GET", f"/kb/{cities['number']}/contributions?state=pending").json()["contributions"]
        assert [(c["state"], c["remote"]["credit"]) for c in pending] == [("pending", ben_actor)], pending
        # KF3: B keeps the server actor of A as a follower of Cities, and its Accept reached A.
        conn = b.db()
        try:
            assert [tuple(r) for r in conn.execute("SELECT actor_iri FROM kf_kb_followers WHERE kb_number=?", (int(cities["number"]),))] == [(f"{a.origin}/ap/actors/server",)]
        finally:
            conn.close()
        conn = a.db()
        try:
            assert [tuple(r) for r in conn.execute("SELECT kb_iri, state FROM kf_follows")] == [(kb_actor, "accepted")]
        finally:
            conn.close()

        # 3. The moderator of B approves; the knowledge base actor sends Accept with the result; the submission page of A shows it.
        approved = ann_b("PATCH", f"/kb/{cities['number']}/contributions/{pending[0]['number']}", {"action": "approve"})
        assert approved.status_code == 200, approved.text
        deadline = time.monotonic() + 60
        while ann_b("GET", f"/kb/{cities['number']}/contributions?state=pending").json()["contributions"] and time.monotonic() < deadline:
            time.sleep(0.5)
        _run(a, b, "b:outbox,a:outbox")
        targets = [t for item in ben("GET", f"/submissions/{submission}").json()["items"] for t in item["targets"] if t.get("remote")]
        assert [(t["state"], t["approved"], t["result"]) for t in targets] == [("accepted", True, f"{kb_actor}/documents/{pending[0]['number']}")], targets

        # 4. The moderator gives ben the role collaborator: the Grant reaches A, which lists it for ben.
        given = ann_b("POST", f"/kb/{cities['number']}/remote-collaborators", {"actor": ben_actor})
        assert given.status_code == 201, given.text
        _run(a, b, "b:outbox,a:outbox")
        grants = ben("GET", "/api/kb/remote-grants").json()["grants"]
        assert [(g["knowledge_base"], g["state"], g["remote_id"]) for g in grants] == [(kb_actor, "active", remote_id)], grants

        # 5. With the grant, ben sends a document directly to Cities: the Offer has the grant as its capability, and B accepts it without a moderator.
        sent = ben("POST", "/submissions", {"documents": ["doc-towns"], "remote_knowledge_base": remote_id})
        assert sent.status_code == 201, sent.text
        direct = sent.json()["submission"]["number"]
        _run(a, b, "a:outbox,b:outbox")
        targets: list[dict] = []
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            _run(a, b, "b:outbox,a:outbox")
            targets = [t for item in ben("GET", f"/submissions/{direct}").json()["items"] for t in item["targets"] if t.get("remote")]
            if targets and targets[0]["state"] != "pending":
                break
            time.sleep(0.5)
        assert [(t["state"], t["approved"], t["with_grant"]) for t in targets] == [("accepted", True, True)], targets
        assert ann_b("GET", f"/kb/{cities['number']}/contributions?state=pending").json()["contributions"] == []
    except BaseException:
        print("A:", a.stop()[-3000:])
        print("B:", b.stop()[-3000:])
        raise
    finally:
        a.stop()
        b.stop()
