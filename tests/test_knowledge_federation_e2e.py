"""End-to-end test of the completion criteria of unit KF1 (KNOWLEDGE_FEDERATION_DESIGN.md Section 17).

Criteria: two test servers know each other after a contribution or an admin entry; a blocked third server gets 404 on the shared-knowledge collections; a failed activity can be processed again; single mode ``/`` gives 302 to ``/@{username}/``.

Two Claire servers run as two app objects with two databases: A (``https://a.example``) and B (``https://b.example``), both in single mode with the switch ``federation`` on. C (``https://c.example``) is the third server; B blocks it.

What is simulated: the two Fedify sidecars and the network between them. A sidecar verifies the HTTP signature of a request of another server and then calls the internal federation API of its Claire with the activity JSON unchanged and the actor whose signature is valid. The test calls the internal API of B directly with those values; no HTTP request goes between the servers. The activity is the contract fixture ``offer_contribution`` of unit KF0 (an ``Offer`` of the account ``alice`` of A to the shared knowledge base of B), because the outbox of A that builds it is unit KF4. The admin actions go through the public app of each server with an admin session.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from claire.federation.knowledge import journal
from claire.store.secrets import reset_secret_service
from tests.knowledge_federation_servers import ALICE, B_KB_NUMBER, KfServer, fixture, server_actor

KNOWN = "/api/admin/federation/known-servers"
JOURNAL = "/api/admin/federation/journal"
BLOCKS = "/api/admin/federation/domain-blocks"


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch):
    for key in list(os.environ):
        if key.startswith(("CLAIRE_", "CB_")):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("CLAIRE_SECRET_KEY", "66" * 32)
    monkeypatch.setattr(journal, "HANDLERS", {})
    reset_secret_service()
    yield
    reset_secret_service()


def _known(server: KfServer, session: str) -> dict[str, dict]:
    response = server.request("GET", KNOWN, session=session)
    assert response.status_code == 200
    return {s["domain"]: s for s in response.json()["servers"]}


def test_kf1_completion_criteria_with_two_servers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    a = KfServer(tmp_path, "https://a.example", username="alice_admin")
    b = KfServer(tmp_path, "https://b.example", username="bora")
    b.add_shared_kb(B_KB_NUMBER, "Analytical Engine")
    a_admin, b_admin = a.admin_session(), b.admin_session()

    # 1. Single mode: ``/`` gives 302 to ``/@{username}/`` on each server, and the target is the workspace.
    for server in (a, b):
        root = server.request("GET", "/")
        assert root.status_code == 302 and root.headers["location"] == f"/@{server.username}/"
        assert server.request("GET", root.headers["location"]).status_code == 200

    # 2. An admin entry: the admin of A adds B.
    assert a.request("POST", KNOWN, session=a_admin, json={"domain": "b.example"}).status_code == 201
    # 3. A contribution: the sidecar of B verified the signature of alice on A and delivers the Offer.
    offer = fixture("activities", "offer_contribution")
    delivered = b.deliver(offer, signer=ALICE, recipient=f"https://b.example/ap/kb/{B_KB_NUMBER}")
    assert delivered.status_code == 200
    # No handler of ``offer_contribution`` is installed (unit KF4 adds it): the row is ``failed`` and stays in the journal.
    assert (delivered.json()["decision"], delivered.json()["reason"]) == ("failed", "no_handler")
    # A knows B (admin entry). B does not know A yet: an activity that no handler accepted does not make its server known (design Section 6.2 rule 2; the handler of KF4 makes it known when the offer makes a contribution).
    assert (_known(a, a_admin)["b.example"]["source"], _known(a, a_admin)["b.example"]["status"]) == ("admin", "active")
    assert "a.example" not in _known(b, b_admin)

    # 4. B blocks the third server C. Its server actor gets 404 on each collection of shared knowledge; A reads them.
    assert b.request("POST", BLOCKS, session=b_admin, json={"domain": "c.example", "note": "test"}).status_code == 201
    for name in ("kb", "themes"):
        blocked = b.internal("GET", f"/knowledge-collections/{name}?signer={server_actor('https://c.example')}")
        assert blocked.status_code == 404 and blocked.json() == {"error": "not_found"}
        allowed = b.internal("GET", f"/knowledge-collections/{name}?signer={server_actor('https://a.example')}")
        assert allowed.status_code == 200 and allowed.json()["server"] == "a.example"
    assert [item["id"] for item in b.internal("GET", f"/knowledge-collections/kb?signer={server_actor('https://a.example')}&page=true").json()["document"]["orderedItems"]] == [f"https://b.example/ap/kb/{B_KB_NUMBER}"]
    # An activity of C gets no row.
    c_offer = {**offer, "id": "https://c.example/ap/actors/1#offers/1", "actor": "https://c.example/ap/actors/1"}
    assert b.deliver(c_offer, signer="https://c.example/ap/actors/1", recipient=None).json()["decision"] == "rejected"

    # 5. The failed activity is processed again after the handler is installed.
    entries = b.request("GET", f"{JOURNAL}?status=failed", session=b_admin).json()["entries"]
    assert [(e["activity_iri"], e["error_code"], e["replayable"]) for e in entries] == [(offer["id"], "no_handler", True)]
    applied: list[str] = []
    monkeypatch.setitem(journal.HANDLERS, "offer_contribution", lambda ctx, entry, activity: applied.append(activity["object"]["context"]))
    replayed = b.request("POST", f"{JOURNAL}/{entries[0]['id']}/replay", session=b_admin, json={})
    assert replayed.status_code == 200 and replayed.json()["entry"]["status"] == "processed"
    assert applied == [f"https://b.example/ap/kb/{B_KB_NUMBER}"]
    assert b.request("GET", f"{JOURNAL}?status=failed", session=b_admin).json()["entries"] == []
    # A second delivery of the same activity changes nothing.
    assert b.deliver(offer, signer=ALICE, recipient=None).json()["decision"] == "duplicate"
    assert applied == [f"https://b.example/ap/kb/{B_KB_NUMBER}"]
