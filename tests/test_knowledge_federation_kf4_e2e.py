"""End-to-end test of the completion criterion of unit KF4 (KNOWLEDGE_FEDERATION_DESIGN.md Section 17).

Criterion: an account of A contributes to a shared knowledge base of B; a reviewer of B approves; the submission page on A shows the decision; after a grant, the next contribution needs no approval; after ``Revoke``, it needs approval again.

Two Claire servers run as two app objects with two databases: A (``https://a.example``) and B (``https://b.example``), both in network mode with the switch ``federation`` on and the mock provider. On B, ``ann`` founds the shared knowledge base ``Cities`` and is its moderator. On A, ``ann`` founds ``Places``; a theme of A has ``Places`` and the record of ``Cities`` of B. ``ben`` of A has documents in his personal knowledge base.

What is simulated: the two Fedify sidecars and the network between them. In a real exchange the sidecar of a server takes the outbound activities from its Claire, signs each one with the key of its actor (the server actor, a knowledge base actor, or an account actor), and the sidecar of the other server verifies the signature and hands the JSON to its Claire. The test does these steps with direct calls to the internal federation API of A and of B: it gives the receiving server the actor that a sidecar found in a valid signature and the activity JSON without a change. No HTTP request leaves the test process, and no signature is made or verified. The step-up of a moderator is a session row with a recent sign-in.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from claire import ids, reputation
from claire.knowledge_federation import handlers, journal, known_servers, vocabulary
from claire.store import accounts as accounts_store
from claire.store import themes as themes_store
from claire.store.secrets import reset_secret_service
from claire.store.token_store import reset_token_stores
from tests.knowledge_federation_servers import KfWorld, server_actor
from tests.test_contributions import EXTRACTION, _personal_document, no_llm  # noqa: F401 (fixture)

ANN, BEN = 2, 3


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


def actor_of(world: KfWorld, account_id: int) -> str:
    return f"{world.origin}/ap/actors/{ids.format_public_id((1 << 57) + account_id)}"


def signer_iri(world: KfWorld, signer: str) -> str:
    """The actor of a task: ``server``, the number of a knowledge base actor, or ``account:`` and the public ID of an account actor."""
    if signer == "server":
        return server_actor(world.origin)
    if signer.startswith("account:"):
        return f"{world.origin}/ap/actors/{signer.removeprefix('account:')}"
    return f"{world.origin}/ap/kb/{signer}"


def sidecar_delivers(sender: KfWorld, receiver: KfWorld) -> list[tuple[str, str]]:
    """The work of the sidecar of ``sender`` for its outbound activities: each one goes to the inbox of its recipient on ``receiver`` (here: the internal route of ``receiver``, with the actor of the task as the signer), then the task is acknowledged. Return ``(kind, decision)`` for each task."""
    done = []
    for task in sender.internal("GET", "/knowledge-outbox-tasks").json()["tasks"]:
        signer = signer_iri(sender, task["signer"])
        assert task["activity"]["actor"] == signer and task["recipient"].startswith(receiver.origin)
        # The inbox of a knowledge base actor or of an account actor has an owner; the server actor gets its activities in the shared inbox.
        recipient = None if task["recipient"].endswith("/ap/actors/server") else task["recipient"]
        receipt = receiver.deliver(task["activity"], signer=signer, recipient=recipient)
        assert receipt.status_code == 200, receipt.text
        assert sender.internal("POST", f"/knowledge-outbox-tasks/{task['id']}/ack", {"result": "delivered", "error_code": None}).json()["status"] == "delivered"
        done.append((task["kind"], receipt.json()["decision"]))
    return done


def as_session(world: KfWorld, account_id: int, method: str, path: str, **kwargs):
    """One request with a session of the account that has a sign-in of now: the step-up routes need it (shared knowledge base design Section 6.8). The cookie jar of the client is empty before and after, so that the next request has its bearer token only."""
    token = world.db(lambda conn: accounts_store.create_account_session(conn, account_id=account_id, scope="owner", now=time.time()))
    world.client.cookies.clear()
    response = world.client.request(method, path, headers={"Cookie": f"claire_session={token}", "Origin": world.origin}, **kwargs)
    world.client.cookies.clear()
    return response


def read_kb_collection(a: KfWorld, b: KfWorld) -> None:
    """The reads of unit KF2: A knows B and has the records of its shared knowledge bases."""
    a.db(lambda conn: known_servers.note_server(conn, "b.example", source="admin", added_by=1, now=a.now))
    assert a.internal("GET", "/knowledge-server-reads").json() == {"servers": [{"domain": "b.example"}]}
    assert a.internal("POST", "/knowledge-server-reads", {"domain": "b.example", "ok": True, "server_actor": server_actor(b.origin), "software_name": "claire-bible", "software_version": "1.0.0", "shared_kb_count": None, "error": None}).status_code == 200
    assert a.internal("GET", "/knowledge-kb-reads").json()["reads"] == [{"domain": "b.example", "url": "https://b.example/ap/kb"}]
    page = b.internal("GET", f"/knowledge-collections/kb?signer={server_actor(a.origin)}&page=true").json()["document"]
    assert a.internal("POST", "/knowledge-kb-reads", {"domain": "b.example", "items": page["orderedItems"], "complete": True, "error": None}).status_code == 200


def remote_targets(world: KfWorld, name: str, number: str) -> list[dict]:
    submission = world.client.get(f"/submissions/{number}", headers=world.headers(name))
    assert submission.status_code == 200, submission.text
    return [target for item in submission.json()["items"] for target in item["targets"] if target.get("remote")]


def test_kf4_contribution_across_servers_with_approval_then_a_grant_then_a_revoke(servers, no_llm) -> None:  # noqa: F811
    a, b = servers
    ben_actor = actor_of(a, BEN)
    # B: the shared knowledge base Cities; ann is its moderator.
    cities = b.client.post("/kb", json={"label": "Cities", "knowledge_type": "real"}, headers=b.headers("ann")).json()["knowledge_base"]
    b_base = f"/kb/{cities['number']}"
    kb_actor = f"{b.origin}/ap/kb/{cities['number']}"
    # A: the shared knowledge base Places, the record of Cities, and a theme with the two.
    places = a.client.post("/kb", json={"label": "Places", "knowledge_type": "real"}, headers=a.headers("ann")).json()["knowledge_base"]
    read_kb_collection(a, b)

    def _theme(conn):
        theme = themes_store.create_theme(conn, label="Places", knowledge_type="real")
        themes_store.add_theme_member(conn, theme_id=theme["id"], kb_id=places["id"], source="admin", pinned=True)
        remote_id = int(conn.execute("SELECT id FROM kf_remote_knowledge_bases WHERE iri=?", (kb_actor,)).fetchone()[0])
        themes_store.set_admin_remote_member(conn, theme_id=theme["id"], remote_kb_id=remote_id, pinned=True, excluded=False)
        return ids.format_public_id(int(theme["number"])), remote_id

    theme_number, remote_id = a.db(_theme)
    first = _personal_document(a.settings, a.manager, BEN, "Seoul and Busan are cities of Korea. " * 3, url="https://example.org/cities", extraction=EXTRACTION)
    second = _personal_document(a.settings, a.manager, BEN, "Daegu is a city in the south-east of Korea. " * 3, url="https://example.org/daegu", extraction={**EXTRACTION, "entities": [EXTRACTION["entities"][0], {"name": "Daegu", "type": "Concept", "aliases": [], "observations": ["A city."]}], "relations": [{"source": "Daegu", "target": "Korea", "type": "part_of"}]})
    third = _personal_document(a.settings, a.manager, BEN, "Gwangju is a city in the south-west of Korea. " * 3, url="https://example.org/gwangju", extraction={**EXTRACTION, "entities": [EXTRACTION["entities"][0], {"name": "Gwangju", "type": "Concept", "aliases": [], "observations": ["A city."]}], "relations": []})

    # 1. ben of A contributes the first document to the theme. The theme has Cities of B: the account actor of ben sends an ``Offer`` (design Section 12.1 rules 1 and 2).
    sent = a.client.post("/submissions", json={"documents": [first], "theme": theme_number}, headers=a.headers("ben"))
    assert sent.status_code == 201, sent.text
    submission_1 = sent.json()["submission"]["number"]
    (target,) = remote_targets(a, "ben", submission_1)
    assert (target["label"], target["server"], target["remote_id"], target["actor"], target["state"], target["approved"], target["reason"], target["result"], target["with_grant"], target["delivery"]) == ("Cities", "b.example", remote_id, kb_actor, "pending", False, None, None, False, "pending")
    assert a.db(lambda conn: known_servers.get_server(conn, "b.example").source) == "admin"  # the first source of a known server stays
    # The sidecar of A delivers: the ``Follow`` of the server actor (Cities is in a theme of A), and the ``Offer`` with the account actor as the signer.
    tasks = a.internal("GET", "/knowledge-outbox-tasks").json()["tasks"]
    offer_task = next(task for task in tasks if task["kind"] == "offer_contribution")
    assert offer_task["signer"] == f"account:{ids.format_public_id((1 << 57) + BEN)}" and offer_task["recipient"] == kb_actor
    offer = offer_task["activity"]
    assert vocabulary.validate_activity(offer) == "offer_contribution" and (offer["actor"], offer["target"], "capability" in offer) == (ben_actor, kb_actor, False)
    a.db(lambda conn: conn.execute("UPDATE kf_outbox SET lease_until=NULL"))  # the sidecar takes the tasks again
    assert sorted(sidecar_delivers(a, b)) == [("follow", "processed"), ("offer_contribution", "processed")]
    assert remote_targets(a, "ben", submission_1)[0]["delivery"] == "delivered"

    # B: a pending contribution with the remote actor as the contributor. The copy is hidden and has no graph (design Section 12.1 rule 3).
    (pending,) = b.client.get(f"{b_base}/contributions?state=pending", headers=b.headers("ann")).json()["contributions"]
    assert (pending["state"], pending["origin"], pending["approved"], pending["contributor"], pending["remote"]["credit"], pending["remote"]["knowledge_base"]) == ("pending", "bulk", False, None, ben_actor, None)
    assert pending["offer"] == {"actor": ben_actor, "server": "a.example", "with_grant": False, "grant_valid": False} and pending["contributor_reputation"] == {"level": "good", "approval_rate": None}
    assert b.db(lambda conn: (conn.execute("SELECT hidden FROM documents WHERE id=?", (pending["document"],)).fetchone()[0], conn.execute("SELECT COUNT(*) FROM entities WHERE kb_id=?", (cities["id"],)).fetchone()[0])) == (1, 0)
    assert b.client.get(f"{b_base}/contributions").json()["contributions"] == []  # a reader that is not a reviewer does not see it
    assert b.db(lambda conn: known_servers.get_server(conn, "a.example").source) == "remote_contribution"  # design Section 6.2 rule 2: the offer made A a known server of B
    # No answer goes out before the decision: only the ``Accept`` of the ``Follow``.
    assert sidecar_delivers(b, a) == [("accept_follow", "processed")]
    assert remote_targets(a, "ben", submission_1)[0]["state"] == "pending"

    # 2. Criterion: a reviewer of B approves. The merge job builds the graph without an LLM call; the knowledge base actor sends ``Accept`` with ``result``.
    approved = b.client.patch(f"{b_base}/contributions/{pending['number']}", json={"action": "approve"}, headers=b.headers("ann"))
    assert approved.status_code == 200 and approved.json()["contribution"]["state"] == "visible", approved.text
    assert b.db(lambda conn: {row[0] for row in conn.execute("SELECT name FROM entities WHERE kb_id=?", (cities["id"],))}) == {"Korea", "Seoul", "Busan"} and no_llm == []
    assert sidecar_delivers(b, a) == [("accept_offer", "processed")]
    # Criterion: the submission page on A shows the decision.
    (target,) = remote_targets(a, "ben", submission_1)
    result_iri = f"{kb_actor}/documents/{pending['number']}"
    assert (target["state"], target["approved"], target["result"], target["reason"]) == ("accepted", True, result_iri, None)
    listed = a.client.get("/submissions", headers=a.headers("ben")).json()["submissions"]
    assert [t["state"] for t in listed[0]["items"][0]["targets"] if t.get("remote")] == ["accepted"]
    # The document is in the documents collection of Cities for other servers, with the credit of ben and the document on A as its origin.
    served = b.internal("GET", f"/knowledge-kb-actors/{cities['number']}/documents/{pending['number']}?signer={server_actor(a.origin)}").json()["document"]
    assert (served["id"], served["attributedTo"], served["origin"]) == (result_iri, ben_actor, f"{ben_actor}/documents/{first}")
    # A direct choice of Cities needs a grant: only a collaborator has ``contribute_direct`` without a membership (design Section 12.2 rule 4).
    refused = a.client.post("/submissions", json={"documents": [second], "remote_knowledge_base": remote_id}, headers=a.headers("ben"))
    assert (refused.status_code, refused.json()["error"]) == (403, "grant_required")

    # 3. Criterion: after a grant, the next contribution needs no approval. ann, the moderator of Cities, gives ben of A the role ``collaborator`` (design Section 12.2 rule 1). The step needs a recent sign-in in a session.
    assert b.client.post(f"{b_base}/remote-collaborators", json={"actor": ben_actor}, headers=b.headers("ann")).status_code == 403
    assert as_session(b, BEN, "POST", f"{b_base}/remote-collaborators", json={"actor": ben_actor}).status_code == 403  # ben of B is not a moderator
    given = as_session(b, ANN, "POST", f"{b_base}/remote-collaborators", json={"actor": ben_actor})
    assert given.status_code == 201, given.text
    grant = given.json()["grant"]
    assert (grant["actor"], grant["server"], grant["state"], given.json()["renewed"]) == (ben_actor, "a.example", "active", False) and grant["end_time"] - grant["published_at"] == 183 * 86400
    listed_grants = b.client.get(f"{b_base}/remote-collaborators", headers=b.headers("ann"))
    assert listed_grants.status_code == 200, (listed_grants.status_code, listed_grants.text[:300])
    assert [g["id"] for g in listed_grants.json()["grants"]] == [grant["id"]]
    assert sidecar_delivers(b, a) == [("grant", "processed")]
    (received,) = a.client.get("/api/kb/remote-grants", headers=a.headers("ben")).json()["grants"]
    assert (received["grant"], received["knowledge_base"], received["server"], received["remote_id"], received["label"], received["state"]) == (grant["grant"], kb_actor, "b.example", remote_id, "Cities", "active")
    assert a.client.get("/api/kb/remote-grants", headers=a.headers("ann")).json()["grants"] == []
    # ben contributes the second document directly to Cities. The ``Offer`` carries the grant as its capability.
    sent = a.client.post("/submissions", json={"documents": [second], "remote_knowledge_base": remote_id}, headers=a.headers("ben"))
    assert sent.status_code == 201, sent.text
    submission_2 = sent.json()["submission"]["number"]
    assert "code" not in sent.json()["submission"]["items"][0] and remote_targets(a, "ben", submission_2)[0]["with_grant"] is True
    assert sidecar_delivers(a, b) == [("offer_contribution", "processed")]
    # B checked the grant (its own, this knowledge base, this actor, in its term, not revoked): the contribution is visible, and no reviewer did anything.
    contributions = {c["title"]: c for c in b.client.get(f"{b_base}/contributions", headers=b.headers("ann")).json()["contributions"]}
    granted = next(c for c in contributions.values() if c["offer"]["with_grant"])
    assert (granted["state"], granted["origin"], granted["approved"], granted["offer"]["grant_valid"], granted["remote"]["credit"]) == ("visible", "direct", True, True, ben_actor)
    assert "Daegu" in b.db(lambda conn: {row[0] for row in conn.execute("SELECT name FROM entities WHERE kb_id=?", (cities["id"],))}) and no_llm == []
    assert b.db(lambda conn: [row[0] for row in conn.execute("SELECT action FROM kb_moderation_log WHERE kb_id=? ORDER BY id", (cities["id"],))]) == ["contribution_approve", "remote_collaborator_grant"]
    assert sidecar_delivers(b, a) == [("accept_offer", "processed")]
    assert [(t["state"], t["result"]) for t in remote_targets(a, "ben", submission_2)] == [("accepted", f"{kb_actor}/documents/{granted['number']}")]

    # 4. Criterion: after ``Revoke``, the next contribution needs approval again. ann ends the grant; before the ``Revoke`` gets to A, ben sends the third document with the capability.
    assert b.client.delete(f"{b_base}/remote-collaborators/{grant['id']}", headers=b.headers("ann")).status_code == 403
    revoked = as_session(b, ANN, "DELETE", f"{b_base}/remote-collaborators/{grant['id']}")
    assert revoked.status_code == 200 and revoked.json()["grant"]["state"] == "revoked", revoked.text
    assert as_session(b, ANN, "DELETE", f"{b_base}/remote-collaborators/{grant['id']}").status_code == 409
    sent = a.client.post("/submissions", json={"documents": [third], "remote_knowledge_base": remote_id}, headers=a.headers("ben"))
    submission_3 = sent.json()["submission"]["number"]
    assert sent.status_code == 201 and remote_targets(a, "ben", submission_3)[0]["with_grant"] is True
    assert sidecar_delivers(a, b) == [("offer_contribution", "processed")]
    # Each use checks the grant on B (KS8): the capability is not in effect, so the offer waits for approval as each other one.
    (waiting,) = b.client.get(f"{b_base}/contributions?state=pending", headers=b.headers("ann")).json()["contributions"]
    assert (waiting["state"], waiting["approved"], waiting["offer"]) == ("pending", False, {"actor": ben_actor, "server": "a.example", "with_grant": True, "grant_valid": False})
    # The ``Revoke`` gets to A: the grant of ben ended, and a direct choice of Cities is refused on A.
    assert sidecar_delivers(b, a) == [("revoke", "processed")]
    assert a.client.get("/api/kb/remote-grants", headers=a.headers("ben")).json()["grants"][0]["state"] == "revoked"
    assert a.client.post("/submissions", json={"documents": [third], "remote_knowledge_base": remote_id}, headers=a.headers("ben")).json()["error"] == "grant_required"
    # A reviewer of B rejects the third document with a reason code: the submission page on A shows it.
    assert b.client.patch(f"{b_base}/contributions/{waiting['number']}", json={"action": "reject", "reason": "off_subject"}, headers=b.headers("ann")).status_code == 200
    assert sidecar_delivers(b, a) == [("reject_offer", "processed")]
    assert [(t["state"], t["approved"], t["reason"], t["result"]) for t in remote_targets(a, "ben", submission_3)] == [("rejected", False, "off_subject", None)]
    # The same answer again changes nothing (design Section 13 rule 2), and no activity waits.
    assert sidecar_delivers(a, b) == [] and sidecar_delivers(b, a) == []
    # B computes the reputation of the remote account only from its own decisions (design Section 12.1 rule 5): one approval of a reviewer and one rejection.
    assert b.db(lambda conn: reputation.compute_remote(conn, ben_actor)) == ("good", None, 2)
