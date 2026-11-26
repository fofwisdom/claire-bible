"""The federation of shared knowledge between two real Claire servers and their real sidecars (KNOWLEDGE_FEDERATION_DESIGN.md units KF2 to KF5).

The tests of the units (``tests/test_knowledge_federation_kf*_e2e.py``) simulate the sidecars and the network. Each test here runs two Claire processes, A (``https://a.claire.test``) and B (``https://b.claire.test``), and the real sidecar code (``sidecar/fedify/e2e/two_servers_e2e.ts``): only the network between the two sidecars is in one Deno process.

The first test (the criterion of unit KF4): A reads the server and the ``/kb`` collection of B; a theme of A has the shared knowledge base Cities of B; ben of A contributes a document to the theme, and the account actor of ben sends ``Offer`` (and the server actor of A follows Cities); a moderator of B approves; the knowledge base actor sends ``Accept``; the submission page of A shows the decision. Then the moderator gives ben the role collaborator (``Grant``), and the next contribution of ben needs no approval. At last the moderator ends the grant (``Revoke``) and rejects the next contribution with a reason code (``Reject``).

The second test (the criteria of unit KF3): a moderator of A curates a document of Cities of B (A reads the documents collection and the document with its extraction); the server actor of A follows Cities; a revision on B reaches the copy on A without approval (``Update``); a removal on B hides the copy on A and puts it in the review queue (``Delete``). A job on B starts the revision as the ingest pipeline starts it after a new fetch of the root document: the test has no web page to fetch again.

The third test (the criterion of unit KF5): with the automatic contribution on both servers, a new shared knowledge base on B gets the matching documents of an account of A after a run: A reads the record of B with ``claire:acceptsAutomatic``, and the account actor sends ``Offer`` with ``claire:automatic``.

The same as ``tests/test_fedify_sidecar.py``: it needs Deno, and it is skipped without it.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from claire.api.federation_internal import federation_socket_path, service_key_path
from claire.core import ids
from claire.federation.knowledge import known_servers, remote_kbs
from claire.ontology.base import normalize_name
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


def _personal_documents(server: _Server, account_id: int, documents: dict[str, str]) -> None:
    """A personal knowledge base of the account (ID 40) with one document and its extraction for each item of ``documents`` (ID: title)."""
    conn = server.db()
    try:
        conn.execute("INSERT INTO knowledge_bases (id, seq, label, is_personal, is_public, created_at, updated_at, account_id, number) VALUES (40, 40, 'personal', 1, 0, 1, 1, ?, 516412345678901240)", (account_id,))
        for document_id, title in documents.items():
            conn.execute("INSERT INTO documents (id, kb_id, url, canonical_url, title, raw_text, hidden, fetched_at, extraction_status, detail) VALUES (?, 40, ?, ?, ?, ?, 0, 1, 'extracted', ?)", (document_id, f"https://example.org/{document_id}", f"https://example.org/{document_id}", title, f"{title}: Seoul and Busan. " * 3, f"= {title}\n\nSeoul and Busan."))
            conn.execute("INSERT INTO extractions (kb_id, document_id, provider, model, prompt_version, raw_response, created_at) VALUES (40, ?, 'mock', 'mock', 'v1', ?, 1)", (document_id, json.dumps(EXTRACTION)))
        conn.commit()
    finally:
        conn.close()


def _read_server(a: _Server, b: _Server, kb_actor: str, places_id: int) -> tuple[int, str]:
    """KF2: A reads B; a theme of A has Places of A and the record of ``kb_actor``. Return the ID of the record and the number of the theme."""
    traffic = _run(a, b, "a:reads")
    assert any(line.startswith("GET b.claire.test/ap/kb") for line in traffic), traffic
    conn = a.db()
    try:
        remote_id = int(conn.execute("SELECT id FROM kf_remote_knowledge_bases WHERE iri=?", (kb_actor,)).fetchone()[0])
        theme = themes_store.create_theme(conn, label="Places", knowledge_type="real")
        themes_store.add_theme_member(conn, theme_id=theme["id"], kb_id=places_id, source="admin", pinned=True)
        themes_store.set_admin_remote_member(conn, theme_id=theme["id"], remote_kb_id=remote_id, pinned=True, excluded=False)
        conn.commit()
        return remote_id, ids.format_public_id(int(theme["number"]))
    finally:
        conn.close()


def _rows(server: _Server, sql: str, *params) -> list[tuple]:
    conn = server.db()
    try:
        return [tuple(row) for row in conn.execute(sql, params)]
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
    # A personal knowledge base of ben with documents and their extractions (the input of a contribution).
    _personal_documents(a, a_ids["ben"], {"doc-cities": "Cities of Korea", "doc-towns": "Towns of Korea", "doc-villages": "Villages of Korea"})
    conn = a.db()
    try:
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
        remote_id, theme_number = _read_server(a, b, kb_actor, places["id"])

        # 2. KF4: ben contributes to the theme; the account actor of ben sends Offer, and the server actor of A follows Cities.
        sent = ben("POST", "/submissions", {"documents": ["doc-cities"], "theme": theme_number})
        assert sent.status_code == 201, sent.text
        submission = sent.json()["submission"]["number"]
        traffic = _run(a, b, "a:outbox,b:outbox")
        assert any(line.startswith(f"POST b.claire.test/ap/kb/{cities['number']}/inbox") for line in traffic), traffic
        pending = ann_b("GET", f"/kb/{cities['number']}/contributions?state=pending").json()["contributions"]
        assert [(c["state"], c["remote"]["credit"]) for c in pending] == [("pending", ben_actor)], pending
        # KF3: B keeps the server actor of A as a follower of Cities, and its Accept reached A.
        assert _rows(b, "SELECT actor_iri FROM kf_kb_followers WHERE kb_number=?", int(cities["number"])) == [(f"{a.origin}/ap/actors/server",)]
        assert _rows(a, "SELECT kb_iri, state FROM kf_follows") == [(kb_actor, "accepted")]

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

        # 6. The moderator ends the grant. Before the Revoke reaches A, ben sends a third document with the capability: B checks the grant at each use, so the offer waits for approval.
        revoked = ann_b("DELETE", f"/kb/{cities['number']}/remote-collaborators/{given.json()['grant']['id']}")
        assert revoked.status_code == 200 and revoked.json()["grant"]["state"] == "revoked", revoked.text
        sent = ben("POST", "/submissions", {"documents": ["doc-villages"], "remote_knowledge_base": remote_id})
        assert sent.status_code == 201, sent.text
        third = sent.json()["submission"]["number"]
        _run(a, b, "a:outbox")
        (waiting,) = ann_b("GET", f"/kb/{cities['number']}/contributions?state=pending").json()["contributions"]
        assert (waiting["offer"]["with_grant"], waiting["offer"]["grant_valid"]) == (True, False), waiting
        # The Revoke reaches A: the grant of ben ended there too.
        _run(a, b, "b:outbox")
        assert [g["state"] for g in ben("GET", "/api/kb/remote-grants").json()["grants"]] == ["revoked"]

        # 7. The moderator rejects the third document with a reason code: the Reject reaches the submission page of A.
        assert ann_b("PATCH", f"/kb/{cities['number']}/contributions/{waiting['number']}", {"action": "reject", "reason": "off_subject"}).status_code == 200
        _run(a, b, "b:outbox")
        targets = [t for item in ben("GET", f"/submissions/{third}").json()["items"] for t in item["targets"] if t.get("remote")]
        assert [(t["state"], t["approved"], t["reason"]) for t in targets] == [("rejected", False, "off_subject")], targets
    except BaseException:
        print("A:", a.stop()[-3000:])
        print("B:", b.stop()[-3000:])
        raise
    finally:
        a.stop()
        b.stop()


# A new extraction of the root document on B: one entity changes.
REVISED = {**EXTRACTION, "entities": [EXTRACTION["entities"][0], {"name": "Incheon", "type": "Concept", "aliases": [], "observations": ["A port city."]}], "relations": [{"source": "Incheon", "target": "Korea", "type": "part_of"}]}

# A job on B: the root document gets a new content and a new extraction, and its revisions start, as the ingest pipeline does after a new fetch (``_start_contribution_revisions``); then the merge jobs run, as the CLI runs them (with the renderer ports of ``claire_bible``).
_REVISION_JOB = """
import sys, time
from pathlib import Path
from claire_bible import bootstrap
from claire.collaboration import contributions
from claire.core.config import get_settings
from claire.ingest.normalize import content_hash
from claire.ingest.service import run_waiting_merge_jobs
from claire.store import db as dbm

document_id, text, extraction = sys.argv[1:4]
bootstrap.configure()
settings = get_settings()
conn = dbm.connect_existing(settings.db_file)
try:
    conn.execute("UPDATE documents SET raw_text=?, content_hash=? WHERE id=?", (text, content_hash(text), document_id))
    conn.execute("INSERT INTO extractions (kb_id, document_id, provider, model, prompt_version, raw_response, created_at) VALUES (40, ?, 'mock', 'mock', 'test', ?, ?)", (document_id, extraction, time.time()))
    conn.commit()
    assert contributions.start_revisions(conn, Path(settings.data_dir), root_kb_id=40, root_document_id=document_id)
finally:
    conn.close()
assert run_waiting_merge_jobs(settings) >= 1
"""


def test_e2e_a_curation_across_two_real_servers_then_a_revision_and_a_removal_at_the_origin(tmp_path: Path) -> None:
    a, b = _Server(tmp_path, "a"), _Server(tmp_path, "b")
    a_ids = _setup(a, "admin_a", {"ann": 116412345678901237})
    b_ids = _setup(b, "admin_b", {"ann": 216412345678901237, "ben": 216412345678901236})
    ben_actor = f"{b.origin}/ap/actors/216412345678901236"
    _personal_documents(b, b_ids["ben"], {"doc-cities": "Cities of Korea"})
    conn = a.db()
    try:
        known_servers.note_server(conn, b.host, source="admin", added_by=1, now=time.time())
        conn.commit()
    finally:
        conn.close()
    a.start()
    b.start()
    try:
        ann_a, ann_b, ben_b = _caller(a, a_ids["ann"]), _caller(b, b_ids["ann"]), _caller(b, b_ids["ben"])
        # B: the shared knowledge base Cities with one visible document that ben contributed.
        cities = ann_b("POST", "/kb", {"label": "Cities", "knowledge_type": "real"}).json()["knowledge_base"]
        b_base = f"/kb/{cities['number']}"
        assert ann_b("PATCH", f"{b_base}/", {"join_policy": "open"}).status_code == 200
        assert ben_b("POST", f"{b_base}/membership").status_code == 201
        made = ben_b("POST", f"{b_base}/contributions", {"document": "doc-cities"})
        assert made.status_code == 201, made.text
        (b_contribution,) = ann_b("GET", f"{b_base}/contributions").json()["contributions"]
        assert b_contribution["state"] == "visible", b_contribution
        kb_actor = f"{b.origin}/ap/kb/{cities['number']}"
        document_iri = f"{kb_actor}/documents/{b_contribution['number']}"
        # A: the shared knowledge base Places, the record of Cities, and a theme with the two.
        places = ann_a("POST", "/kb", {"label": "Places", "knowledge_type": "real"}).json()["knowledge_base"]
        a_base = f"/kb/{places['number']}"
        remote_id, _theme = _read_server(a, b, kb_actor, places["id"])

        # 1. ann of A, the moderator of Places, opens Cities of B: the sidecar of A reads its documents collection with requests that the server actor of A signs.
        assert ann_a("GET", f"{a_base}/remote-sources?source={remote_id}").json()["selected"]["state"] == "reading"
        traffic = _run(a, b, "a:remote")
        assert any(line.startswith(f"GET b.claire.test/ap/kb/{cities['number']}/documents") for line in traffic), traffic
        selected = ann_a("GET", f"{a_base}/remote-sources?source={remote_id}").json()["selected"]
        assert (selected["state"], [(doc["iri"], doc["curated"]) for doc in selected["documents"]]) == ("ready", [(document_iri, None)]), selected
        # She selects the document: the sidecar reads it with its extraction, and the copy is a contribution of Places with the origin curation.
        asked = ann_a("POST", f"{a_base}/curations", {"remote_source": remote_id, "documents": [document_iri]})
        assert asked.status_code == 202, asked.text
        _run(a, b, "a:remote")
        assert [(c["document"], c["state"], c["error"]) for c in ann_a("GET", f"{a_base}/remote-sources").json()["curations"]] == [(document_iri, "done", None)]
        (copy,) = ann_a("GET", f"{a_base}/contributions").json()["contributions"]
        assert (copy["origin"], copy["state"], copy["remote"]) == ("curation", "visible", {"document": document_iri, "knowledge_base": kb_actor, "credit": ben_actor}), copy
        assert {"Korea", "Seoul", "Busan"} <= {name for (name,) in _rows(a, "SELECT name FROM entities WHERE kb_id=?", places["id"])}

        # 2. The server actor of A follows Cities; the knowledge base actor of B accepts.
        _run(a, b, "a:outbox,b:outbox")
        assert ann_a("GET", f"{a_base}/remote-sources").json()["sources"][0]["follow"] == "accepted"

        # 3. A revision on B: the knowledge base actor sends Update to its followers, and the copy on A has the new version without approval.
        job = subprocess.run([sys.executable, "-c", _REVISION_JOB, "doc-cities", "The second version of a page about Korea. " * 3, json.dumps(REVISED)], cwd=b.root, env=b.env, capture_output=True, text=True, timeout=120)
        assert job.returncode == 0, job.stdout + job.stderr
        traffic = _run(a, b, "b:outbox")
        assert traffic.count("POST a.claire.test/ap/actors/server/inbox") == 1, traffic
        (revised,) = ann_a("GET", f"{a_base}/contributions").json()["contributions"]
        assert (revised["number"], revised["state"], [r["state"] for r in revised["revisions"]]) == (copy["number"], "visible", ["done"]) and revised["document"] != copy["document"], revised
        assert "Incheon" in {name for (name,) in _rows(a, "SELECT name FROM entities WHERE kb_id=?", places["id"])}

        # 4. A removal on B: the knowledge base actor sends Delete with the reason code; the copy on A is hidden and waits in the review queue of Places.
        assert ann_b("POST", f"{b_base}/contributions/{b_contribution['number']}/remove", {"reason": "spam"}).status_code == 200
        _run(a, b, "b:outbox")
        (queued,) = ann_a("GET", f"{a_base}/contributions?state=hidden").json()["contributions"]
        assert (queued["number"], queued["origin_removed"]["reason"], queued["origin_removed"]["waiting"]) == (copy["number"], "spam", True), queued
        assert _rows(a, "SELECT hidden FROM documents WHERE id=?", revised["document"]) == [(1,)]
    except BaseException:
        print("A:", a.stop()[-3000:])
        print("B:", b.stop()[-3000:])
        raise
    finally:
        a.stop()
        b.stop()


KOREA = ["Korea", "Seoul", "Busan", "Daegu", "Incheon"]


def _keyed_documents(server: _Server, account_id: int, documents: dict[str, list[str]]) -> None:
    """A personal knowledge base of the account (ID 40) with one document, its extraction, and its entities for each item of ``documents`` (name: entities): the keys of the document match (design Section 8.5)."""
    conn = server.db()
    try:
        conn.execute("INSERT INTO knowledge_bases (id, seq, label, is_personal, is_public, created_at, updated_at, account_id, number) VALUES (40, 40, 'personal', 1, 0, 1, 1, ?, 516412345678901240)", (account_id,))
        for name, entities in documents.items():
            text = f"{name}: " + ", ".join(entities) + "."
            extraction = {"summary": text, "key_claims": [], "entities": [{"name": e, "type": "Concept", "aliases": [], "observations": [f"{e} in {name}."]} for e in entities], "relations": []}
            conn.execute("INSERT INTO documents (id, kb_id, title, raw_text, detail, detail_format, url, canonical_url, source_type, content_hash, hidden, fetched_at) VALUES (?, 40, ?, ?, ?, 'adoc', ?, ?, 'web', ?, 0, 1)", (f"doc_{name}", name, text, text, f"https://example.org/{name}", f"https://example.org/{name}", hashlib.sha256(text.encode()).hexdigest()))
            conn.execute("INSERT INTO extractions (kb_id, document_id, provider, model, prompt_version, raw_response, created_at) VALUES (40, ?, 'mock', 'mock', 'test', ?, 1)", (f"doc_{name}", json.dumps(extraction)))
            for entity in entities:
                row = conn.execute("SELECT id, sources FROM entities WHERE kb_id=40 AND norm_name=?", (normalize_name(entity),)).fetchone()
                if row is None:
                    conn.execute("INSERT INTO entities (id, kb_id, type, name, norm_name, aliases, sources, created_at, updated_at) VALUES (?, 40, 'Concept', ?, ?, '[]', ?, 1, 1)", (f"ent_40_{normalize_name(entity)}", entity, normalize_name(entity), json.dumps([f"doc_{name}"])))
                else:
                    conn.execute("UPDATE entities SET sources=? WHERE id=?", (json.dumps([*json.loads(row[1]), f"doc_{name}"]), row[0]))
        conn.commit()
    finally:
        conn.close()


def _fill_shared_kb(server: _Server, kb_id: int, names: list[str], documents: int = 6) -> None:
    """Visible documents and one entity for each name in a shared knowledge base, so that the judgement gives it a signature."""
    conn = server.db()
    try:
        doc_ids = [f"doc_{kb_id}_{i}" for i in range(documents)]
        for doc_id in doc_ids:
            conn.execute("INSERT INTO documents (id, kb_id, title, raw_text, hidden) VALUES (?, ?, ?, 'text', 0)", (doc_id, kb_id, doc_id))
        for rank, name in enumerate(names):
            conn.execute("INSERT INTO entities (id, kb_id, type, name, norm_name, aliases, sources, created_at, updated_at) VALUES (?, ?, 'Concept', ?, ?, '[]', ?, 1, 1)", (f"ent_{kb_id}_{rank}", kb_id, name, normalize_name(name), json.dumps(doc_ids[: max(1, documents - rank)])))
        conn.commit()
    finally:
        conn.close()


def test_e2e_an_automatic_contribution_across_two_real_servers(tmp_path: Path) -> None:
    a, b = _Server(tmp_path, "a"), _Server(tmp_path, "b")
    a_ids = _setup(a, "admin_a", {"ben": BEN_PUBLIC, "ann": 116412345678901237})
    b_ids = _setup(b, "admin_b", {"ann": 216412345678901237})
    ben_actor = f"{a.origin}/ap/actors/{BEN_PUBLIC}"
    _keyed_documents(a, a_ids["ben"], {"seoul": ["Korea", "Seoul", "Busan"], "daegu": ["Korea", "Daegu", "Incheon"], "space": ["Mars", "Jupiter", "Saturn"]})
    conn = a.db()
    try:
        known_servers.note_server(conn, b.host, source="admin", added_by=1, now=time.time())
        conn.commit()
    finally:
        conn.close()
    a.start()
    b.start()
    try:
        admin_a, admin_b, ann_a, ben = _caller(a, 1), _caller(b, 1), _caller(a, a_ids["ann"]), _caller(a, a_ids["ben"])
        ann_b = _caller(b, b_ids["ann"])
        # 1. The admins of the two servers turn on the automatic contribution (design Section 12.3 rule 1).
        for admin in (admin_a, admin_b):
            switched = admin("PUT", "/api/admin/auto-contribution", {"state": "on"})
            assert switched.status_code == 200 and switched.json()["state"] == "on", switched.text
        # A: Places about Korean cities with its theme; ben turns on a rule with that theme.
        places = ann_a("POST", "/kb", {"label": "Places", "knowledge_type": "real"}).json()["knowledge_base"]
        _fill_shared_kb(a, int(places["id"]), KOREA)
        assert admin_a("POST", "/api/admin/themes/judge").status_code == 200
        (theme,) = ben("GET", "/themes").json()["themes"]
        saved = ben("PUT", "/api/auto-rule", {"state": "active", "memberships": False, "mutual": False, "include_uploads": False, "themes": [theme["number"]]})
        assert saved.status_code == 200 and saved.json()["rule"]["state"] == "active", saved.text

        # 2. B: ann founds the new shared knowledge base Cities about the same subject. A reads the /kb collection of B: the record of Cities tells that B accepts automatic contributions.
        cities = ann_b("POST", "/kb", {"label": "Cities", "knowledge_type": "real"}).json()["knowledge_base"]
        _fill_shared_kb(b, int(cities["id"]), KOREA)
        assert admin_b("POST", "/api/admin/themes/judge").status_code == 200
        kb_actor = f"{b.origin}/ap/kb/{cities['number']}"
        _run(a, b, "a:reads")
        conn = a.db()
        try:
            record = remote_kbs.get_by_iri(conn, kb_actor)
            assert record is not None and remote_kbs.accepts_automatic(record) is True and record.signature is not None
        finally:
            conn.close()

        # 3. The judgement run of A puts Cities in the theme and runs the rules: the account actor of ben sends one Offer with claire:automatic for each matching document.
        assert admin_a("POST", "/api/admin/themes/judge").status_code == 200
        _run(a, b, "a:outbox,b:outbox")
        pending = ann_b("GET", f"/kb/{cities['number']}/contributions?state=pending").json()["contributions"]
        assert sorted((c["title"], c["origin"], c["remote"]["credit"]) for c in pending) == [("daegu", "auto", ben_actor), ("seoul", "auto", ben_actor)], pending

        # 4. The moderator of B approves one: the Accept reaches the list of automatic contributions of ben on A.
        seoul = next(c for c in pending if c["title"] == "seoul")
        assert ann_b("PATCH", f"/kb/{cities['number']}/contributions/{seoul['number']}", {"action": "approve"}).status_code == 200
        rows: list[dict] = []
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            _run(a, b, "b:outbox,a:outbox")
            rows = [c for c in ben("GET", "/api/contributions?origin=auto").json()["contributions"] if c.get("remote_target")]
            if any(row["state"] == "accepted" for row in rows):
                break
            time.sleep(0.5)
        assert {row["document"]: (row["state"], row["remote_target"]["result"]) for row in rows} == {"doc_seoul": ("accepted", f"{kb_actor}/documents/{seoul['number']}"), "doc_daegu": ("pending", None)}, rows
    except BaseException:
        print("A:", a.stop()[-3000:])
        print("B:", b.stop()[-3000:])
        raise
    finally:
        a.stop()
        b.stop()
