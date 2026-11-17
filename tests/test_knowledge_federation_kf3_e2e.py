"""End-to-end test of the completion criteria of unit KF3 (KNOWLEDGE_FEDERATION_DESIGN.md Section 17).

Criteria: a revision on B reaches the copy on A without approval; a removal on B hides the copy on A and puts it in the review queue.

Two Claire servers run as two app objects with two databases: A (``https://a.example``) and B (``https://b.example``), both in network mode with the switch ``federation`` on and the mock provider. On B, ``ann`` founds the shared knowledge base ``Cities`` and ``ben`` contributes a document of its personal knowledge base. On A, ``ann`` founds the shared knowledge base ``Places``; a theme of A has ``Places`` and the record of ``Cities`` of B.

What is simulated: the two Fedify sidecars and the network between them. In a real exchange the sidecar of a server takes the reads and the outbound activities from its Claire, signs each request with the key of the server actor or of a knowledge base actor, and the sidecar of the other server verifies the signature and asks its Claire. The test does these steps with direct calls to the internal federation API of A and of B: it gives one server the actor that a sidecar found in a valid signature, and it gives the other server the JSON documents and the activities without a change. No HTTP request goes between the servers, no signature is made or verified, and no inbox is read from an actor document. The theme of A is a row of the test (the subject judgement is in the test of unit KF2). The revision on B starts as the ingest pipeline starts it after a new fetch of the root document.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from claire import ids
from claire.ingest.normalize import content_hash
from claire.knowledge_federation import handlers, journal, known_servers, vocabulary
from claire.store import themes as themes_store
from claire.store.secrets import reset_secret_service
from claire.store.token_store import reset_token_stores
from tests.knowledge_federation_servers import KfWorld, server_actor
from tests.test_contributions import EXTRACTION, _new_version, _personal_document, no_llm  # noqa: F401 (fixture)

BEN = 3
REVISED = {**EXTRACTION, "entities": [EXTRACTION["entities"][0], {"name": "Incheon", "type": "Concept", "aliases": [], "observations": ["A port city."]}], "relations": [{"source": "Incheon", "target": "Korea", "type": "part_of"}]}


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch):
    for key in list(os.environ):
        if key.startswith(("CLAIRE_", "CB_")):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("CLAIRE_SECRET_KEY", "66" * 32)
    monkeypatch.setattr(journal, "HANDLERS", {})
    handlers.install()
    reset_secret_service()
    reset_token_stores()
    yield
    reset_secret_service()
    reset_token_stores()


@pytest.fixture
def servers(tmp_path: Path):
    a, b = KfWorld(tmp_path, "https://a.example"), KfWorld(tmp_path, "https://b.example")
    yield a, b
    a.close()
    b.close()


def _graph(world: KfWorld, kb_id: int) -> set[str]:
    return world.db(lambda conn: {row[0] for row in conn.execute("SELECT name FROM entities WHERE kb_id=?", (kb_id,))})


def _sidecar_reads(a: KfWorld, b: KfWorld, b_number: str) -> list[dict]:
    """The work of the sidecar of A for the reads that wait: each address is read from B with a GET that the server actor of A signs (here: the internal route of B with that signer), and the JSON goes to A without a change. Return the answers of A."""
    answers = []
    signer = server_actor(a.origin)
    prefix = f"{b.origin}/ap/kb/{b_number}/"
    while True:
        reads = a.internal("GET", "/knowledge-remote-reads").json()["reads"]
        if not reads:
            return answers
        for read in reads:
            assert read["url"].startswith(prefix), read
            path, _, query = read["url"][len(prefix):].partition("?")
            got = b.internal("GET", f"/knowledge-kb-actors/{b_number}/{path}?signer={signer}" + (f"&{query}" if query else ""))
            body = {"id": read["id"], "document": got.json()["document"], "error": None} if got.status_code == 200 else {"id": read["id"], "document": None, "error": "document_unavailable"}
            answer = a.internal("POST", "/knowledge-remote-reads", body)
            assert answer.status_code == 200, answer.text
            answers.append(answer.json())


def _sidecar_delivers(sender: KfWorld, receiver: KfWorld) -> list[tuple[str, str, str]]:
    """The work of the sidecar of ``sender`` for its outbound activities: each one goes to the inbox of its recipient on ``receiver`` (here: the internal route of ``receiver``, with the actor of the task as the signer), then the task is acknowledged. Return ``(kind, signer, decision)`` for each task."""
    done = []
    for task in sender.internal("GET", "/knowledge-outbox-tasks").json()["tasks"]:
        signer = server_actor(sender.origin) if task["signer"] == "server" else f"{sender.origin}/ap/kb/{task['signer']}"
        assert task["activity"]["actor"] == signer and task["recipient"].startswith(receiver.origin)
        recipient = task["recipient"] if "/ap/kb/" in task["recipient"] else None  # the inbox of a knowledge base actor, or the shared inbox for a server actor
        receipt = receiver.deliver(task["activity"], signer=signer, recipient=recipient)
        assert receipt.status_code == 200, receipt.text
        assert sender.internal("POST", f"/knowledge-outbox-tasks/{task['id']}/ack", {"result": "delivered", "error_code": None}).json()["status"] == "delivered"
        done.append((task["kind"], task["signer"], receipt.json()["decision"]))
    return done


def test_kf3_curation_across_servers_then_a_revision_and_a_removal_at_the_origin(servers, no_llm) -> None:  # noqa: F811
    a, b = servers
    # B: the shared knowledge base Cities with one visible document that ben contributed.
    cities = b.client.post("/kb", json={"label": "Cities", "knowledge_type": "real"}, headers=b.headers("ann")).json()["knowledge_base"]
    b_base = f"/kb/{cities['number']}"
    assert b.client.patch(f"{b_base}/", json={"join_policy": "open"}, headers=b.headers("ann")).status_code == 200
    assert b.client.post(f"{b_base}/membership", headers=b.headers("ben")).status_code == 201
    root_document = _personal_document(b.settings, b.manager, BEN, "Seoul and Busan are cities of Korea. " * 3, url="https://example.org/cities", extraction=EXTRACTION)
    assert b.client.post(f"{b_base}/contributions", json={"document": root_document}, headers=b.headers("ben")).status_code == 201
    made = b.client.post(f"{b_base}/contributions", json={"document": root_document}, headers=b.headers("ben"))
    assert made.status_code == 409  # the first request made the contribution
    b_contribution = b.client.get(f"{b_base}/contributions", headers=b.headers("ann")).json()["contributions"][0]
    assert b_contribution["state"] == "visible"
    kb_actor = f"{b.origin}/ap/kb/{cities['number']}"
    document_iri = f"{kb_actor}/documents/{b_contribution['number']}"

    # A: the shared knowledge base Places, the record of Cities (the read of unit KF2), and a theme with the two.
    places = a.client.post("/kb", json={"label": "Places", "knowledge_type": "real"}, headers=a.headers("ann")).json()["knowledge_base"]
    a_base = f"/kb/{places['number']}"
    a_signer = server_actor(a.origin)
    a.db(lambda conn: known_servers.note_server(conn, "b.example", source="admin", added_by=1, now=a.now))
    assert a.internal("GET", "/knowledge-server-reads").json() == {"servers": [{"domain": "b.example"}]}
    assert a.internal("POST", "/knowledge-server-reads", {"domain": "b.example", "ok": True, "server_actor": server_actor(b.origin), "software_name": "claire-bible", "software_version": "1.0.0", "shared_kb_count": None, "error": None}).status_code == 200
    assert a.internal("GET", "/knowledge-kb-reads").json()["reads"] == [{"domain": "b.example", "url": "https://b.example/ap/kb"}]
    page = b.internal("GET", f"/knowledge-collections/kb?signer={a_signer}&page=true").json()["document"]
    assert a.internal("POST", "/knowledge-kb-reads", {"domain": "b.example", "items": page["orderedItems"], "complete": True, "error": None}).json()["stored"] == 1

    def _theme(conn):
        theme = themes_store.create_theme(conn, label="Places", knowledge_type="real")
        themes_store.add_theme_member(conn, theme_id=theme["id"], kb_id=places["id"], source="admin", pinned=True)
        remote_id = int(conn.execute("SELECT id FROM kf_remote_knowledge_bases WHERE iri=?", (kb_actor,)).fetchone()[0])
        themes_store.set_admin_remote_member(conn, theme_id=theme["id"], remote_kb_id=remote_id, pinned=True, excluded=False)
        return remote_id

    remote_id = a.db(_theme)

    # The knowledge base actor of B (design Section 9.2): the actor document has the record and the public key; its keys sign its activities.
    actor = b.internal("GET", f"/knowledge-kb-actors/{cities['number']}").json()["document"]
    assert (actor["id"], actor["type"], actor["inbox"], actor["publicKey"]["owner"]) == (kb_actor, ["Group", "claire:KnowledgeBase"], f"{kb_actor}/inbox", kb_actor)
    assert actor["publicKey"]["publicKeyPem"].startswith("-----BEGIN PUBLIC KEY-----") and vocabulary.validate_object(actor, top_level=True) == "knowledge_base"
    keys = b.internal("GET", f"/knowledge-kb-actors/{cities['number']}/keys").json()
    assert [key["alg"] for key in keys["keys"]] == ["rsa2048", "ed25519"] and keys == b.internal("GET", f"/knowledge-kb-actors/{cities['number']}/keys").json()
    # A collection of the actor answers only a signed request of a server actor (KS1).
    assert b.internal("GET", f"/knowledge-kb-actors/{cities['number']}/documents").status_code == 404
    assert b.internal("GET", f"/knowledge-kb-actors/{cities['number']}/documents/{b_contribution['number']}?signer={a.origin}/ap/actors/1").status_code == 404

    # 1. Curation across servers (design Section 11). ann of A is the moderator of Places: she opens Cities of B.
    sources = a.client.get(f"{a_base}/remote-sources", headers=a.headers("ann")).json()
    assert [(source["id"], source["label"], source["server"], source["follow"]) for source in sources["sources"]] == [(remote_id, "Cities", "b.example", None)] and sources["curations"] == []
    assert a.client.get(f"{a_base}/remote-sources?source={remote_id}", headers=a.headers("ann")).json()["selected"]["state"] == "reading"
    assert a.client.get(f"{a_base}/remote-sources", headers=a.headers("ben")).status_code == 403  # ben is not a reviewer of Places
    # The sidecar of A reads the documents collection of B: the root, then one page.
    assert [answer["status"] for answer in _sidecar_reads(a, b, cities["number"])] == ["pending", "done"]
    selected = a.client.get(f"{a_base}/remote-sources?source={remote_id}", headers=a.headers("ann")).json()["selected"]
    assert (selected["state"], [(doc["iri"], doc["title"], doc["url"], doc["curated"]) for doc in selected["documents"]]) == ("ready", [(document_iri, selected["documents"][0]["title"], "https://example.org/cities", None)])
    # She selects the document. The answer is 202: the sidecar reads the document with its extraction.
    asked = a.client.post(f"{a_base}/curations", json={"remote_source": remote_id, "documents": [document_iri, f"{kb_actor}/documents/1"]}, headers=a.headers("ann"))
    assert asked.status_code == 202, asked.text
    assert [(result["document"], result.get("state"), result.get("error")) for result in asked.json()["curations"]] == [(document_iri, "queued", None), (f"{kb_actor}/documents/1", None, "document_not_found")]
    assert [answer["status"] for answer in _sidecar_reads(a, b, cities["number"])] == ["done"]
    curations = a.client.get(f"{a_base}/remote-sources", headers=a.headers("ann")).json()["curations"]
    assert [(c["document"], c["state"], c["error"]) for c in curations] == [(document_iri, "done", None)]
    # The copy is a contribution of Places with the origin ``curation``; the merge job built the graph from the extraction that came, without an LLM call.
    (copy,) = a.client.get(f"{a_base}/contributions", headers=a.headers("ann")).json()["contributions"]
    assert (copy["number"], copy["origin"], copy["state"], copy["approved"], copy["contributor"]) == (curations[0]["contribution"], "curation", "visible", True, None)
    # The credit is the actor of ben on B (a federation actor); the root of the document is on B.
    ben_actor = f"{b.origin}/ap/actors/{ids.format_public_id((1 << 57) + BEN)}"
    assert copy["remote"] == {"document": document_iri, "knowledge_base": kb_actor, "credit": ben_actor}
    row = a.db(lambda conn: dict(conn.execute("SELECT contributor_account_id, curator_account_id, source_kb_id, root_document_id, remote_root_iri FROM kb_contributions WHERE public_id=?", (int(copy["number"]),)).fetchone()))
    assert row == {"contributor_account_id": None, "curator_account_id": 2, "source_kb_id": None, "root_document_id": None, "remote_root_iri": document_iri}
    assert _graph(a, places["id"]) == {"Korea", "Seoul", "Busan"} and no_llm == []
    first_copy = copy["document"]
    # The same document again is a duplicate, as for a local curation.
    again = a.client.post(f"{a_base}/curations", json={"remote_source": remote_id, "documents": [document_iri]}, headers=a.headers("ann"))
    assert (again.status_code, again.json()["curations"]) == (200, [{"document": document_iri, "error": "contribution_duplicate"}])

    # 2. A follows the knowledge base actor of B (design Section 11 rule 3); B accepts.
    assert _sidecar_delivers(a, b) == [("follow", "server", "processed")]
    assert b.internal("GET", f"/knowledge-kb-actors/{cities['number']}/followers?signer={a_signer}").json()["document"]["totalItems"] == 1
    assert _sidecar_delivers(b, a) == [("accept_follow", cities["number"], "processed")]
    assert a.client.get(f"{a_base}/remote-sources", headers=a.headers("ann")).json()["sources"][0]["follow"] == "accepted"
    assert _sidecar_delivers(a, b) == [] and _sidecar_delivers(b, a) == []

    # 3. Criterion, part 1: a revision on B. The root document of ben gets a new content; B revises its copy (no approval), and its knowledge base actor sends ``Update`` to its followers.
    second_text = "The second version of a page about Korea. " * 3
    _new_version(b, root_document, b.manager.personal_kb_for(BEN).id, second_text, REVISED)
    delivered = _sidecar_delivers(b, a)
    assert delivered == [("update_document", cities["number"], "processed")]
    # The copy on A has the new version. No reviewer of A did anything: the contribution is visible, and it points to a new copy.
    (revised,) = a.client.get(f"{a_base}/contributions", headers=a.headers("ann")).json()["contributions"]
    assert (revised["number"], revised["state"], [r["state"] for r in revised["revisions"]]) == (copy["number"], "visible", ["done"]) and revised["document"] != first_copy
    assert a.db(lambda conn: tuple(conn.execute("SELECT content_hash, hidden FROM documents WHERE id=?", (revised["document"],)).fetchone())) == (content_hash(second_text), 0)
    assert a.db(lambda conn: conn.execute("SELECT COUNT(*) FROM documents WHERE id=?", (first_copy,)).fetchone()[0]) == 0
    assert _graph(a, places["id"]) == {"Korea", "Incheon"} and no_llm == []
    assert a.db(lambda conn: [row[0] for row in conn.execute("SELECT action FROM kb_moderation_log WHERE kb_id=?", (places["id"],))]) == ["curate"]
    # The same activity again changes nothing (design Section 13 rule 2).
    update = json.loads(a.db(lambda conn: conn.execute("SELECT payload FROM kf_inbox_journal WHERE kind='update_document'").fetchone()[0]))
    assert a.deliver(update, signer=kb_actor, recipient=None).json()["decision"] == "duplicate"

    # 4. Criterion, part 2: a removal on B. A moderator of B removes the contribution; the knowledge base actor sends ``Delete`` with the reason code.
    assert b.client.post(f"{b_base}/contributions/{b_contribution['number']}/remove", json={"reason": "spam"}, headers=b.headers("ann")).status_code == 200
    assert _sidecar_delivers(b, a) == [("delete_document", cities["number"], "processed")]
    # The copy on A is hidden at once and is in the review queue of Places, with the reason code of the origin. It is not removed.
    (queued,) = a.client.get(f"{a_base}/contributions?state=hidden", headers=a.headers("ann")).json()["contributions"]
    assert (queued["number"], queued["document"], queued["origin_removed"]["reason"], queued["origin_removed"]["waiting"]) == (copy["number"], revised["document"], "spam", True)
    assert a.db(lambda conn: conn.execute("SELECT hidden FROM documents WHERE id=?", (revised["document"],)).fetchone()[0]) == 1
    assert a.client.get(f"{a_base}/contributions").json()["contributions"] == []  # a reader that is not a reviewer does not see it
    # The reviewers of A decide. Here: show it again. The removal on B stays a decision of B (design Section 6.4).
    shown = a.client.patch(f"{a_base}/contributions/{copy['number']}", json={"action": "unhide"}, headers=a.headers("ann"))
    assert shown.status_code == 200 and (shown.json()["contribution"]["state"], shown.json()["contribution"]["origin_removed"]["waiting"]) == ("visible", False)
    assert a.db(lambda conn: [row[0] for row in conn.execute("SELECT action FROM kb_moderation_log WHERE kb_id=? ORDER BY id", (places["id"],))]) == ["curate", "origin_removed", "contribution_unhide"]
    # A holds no copy that follows B now, but Cities is in a theme of A: the follow stays.
    assert _sidecar_delivers(a, b) == []
