"""End-to-end test of the completion criterion of unit KF2 (KNOWLEDGE_FEDERATION_DESIGN.md Section 17).

Criterion: a shared knowledge base on server B appears in a theme of server A and on the root page of A, without any agreement.

Two Claire servers run as two app objects with two databases: A (``https://a.example``, network mode, so that it has a root page) and B (``https://b.example``), both with the switch ``federation`` on. The admin of B does nothing for A: B does not know A, and no admin of B approves a request.

What is simulated: the two Fedify sidecars and the network between them. In a real exchange the sidecar of A takes the reads that are due from its Claire, reads NodeInfo and the server actor of B, reads the ``/kb`` collection of B with a GET that the server actor of A signs, and gives the items to its Claire; the sidecar of B verifies the signature and asks its Claire for the collection document. The test does these steps with direct calls to the internal federation API of A and of B: it gives B the actor that a sidecar found in a valid signature, and it gives A the documents that B answered, without a change. No HTTP request goes between the servers, and no signature is made or verified. The subject judgement of A runs through the admin route; the documents and entities of the two knowledge bases are rows of the test, not results of an ingest.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from claire.federation.knowledge import journal, vocabulary
from claire.store.secrets import reset_secret_service
from tests.knowledge_federation_servers import B_KB_NUMBER, KfServer, root_recent, server_actor

A_KB_NUMBER = 1846000000000000200
ENGINE = ["Ada Lovelace", "Analytical Engine", "Charles Babbage", "Difference Engine", "Punched card"]
JSON = {"Accept": "application/json"}


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


def _sidecar_reads_kb_collection(a: KfServer, b: KfServer) -> dict:
    """The work of the sidecar of A for one read that is due: the pages of the ``/kb`` collection of B, given to A in batches. Return the answer of A to the last batch."""
    reads = a.internal("GET", "/knowledge-kb-reads").json()["reads"]
    assert reads == [{"domain": "b.example", "url": "https://b.example/ap/kb"}]
    signer = server_actor(a.origin)
    # The sidecar of B verified the signature of the server actor of A and asks its Claire for the document.
    root = b.internal("GET", f"/knowledge-collections/kb?signer={signer}").json()["document"]
    assert (root["id"], root["type"], root["totalItems"]) == (reads[0]["url"], "OrderedCollection", 1)
    page_url = root["first"]
    while True:
        query = page_url.split("?", 1)[1]
        page = b.internal("GET", f"/knowledge-collections/kb?signer={signer}&{query}").json()["document"]
        last = "next" not in page
        answer = a.internal("POST", "/knowledge-kb-reads", {"domain": "b.example", "items": page["orderedItems"], "complete": last, "error": None})
        assert answer.status_code == 200
        if last:
            return answer.json()
        page_url = page["next"]


def test_kf2_a_shared_knowledge_base_of_b_is_in_a_theme_and_on_the_root_page_of_a(tmp_path: Path) -> None:
    a = KfServer(tmp_path, "https://a.example", username="alice_admin", mode="network")
    b = KfServer(tmp_path, "https://b.example", username="bora")
    # B has a shared knowledge base with documents; its own judgement gives it a signature. B also has the personal knowledge base of its admin.
    b.add_shared_kb(B_KB_NUMBER, "Analytical Engine")
    b.fill_kb(B_KB_NUMBER, ENGINE, documents=8)
    b.judge()
    # A has a shared knowledge base of the same subject, so A has a theme.
    a.add_shared_kb(A_KB_NUMBER, "Early computing")
    a.fill_kb(A_KB_NUMBER, ENGINE[:4])
    a.judge()
    a_admin = a.admin_session()
    themes = a.request("GET", "/themes", headers=JSON).json()["themes"]
    assert [(t["state"], t["knowledge_bases"], t["remote_knowledge_bases"]) for t in themes] == [("active", 1, 0)]
    theme_number = themes[0]["number"]

    # 1. B becomes a known server of A (here: an admin entry of A; a contribution or a follow does the same). No step on B.
    assert a.request("POST", "/api/admin/federation/known-servers", session=a_admin, json={"domain": "b.example"}).status_code == 201
    # 2. The sidecar of A reads NodeInfo and the server actor of B (the first contact).
    assert a.internal("GET", "/knowledge-server-reads").json() == {"servers": [{"domain": "b.example"}]}
    recorded = a.internal("POST", "/knowledge-server-reads", {"domain": "b.example", "ok": True, "server_actor": server_actor(b.origin), "software_name": "claire-bible", "software_version": "1.0.0", "shared_kb_count": None, "error": None})
    assert recorded.status_code == 200
    # 3. The sidecar of A reads the ``/kb`` collection of B in full.
    assert _sidecar_reads_kb_collection(a, b) == {"domain": "b.example", "stored": 1, "refused": 0, "refused_code": None, "removed": 0}
    # The item that went from B to A is a valid ``claire:KnowledgeBase`` with a signature; the personal knowledge base of B is not in the collection (KD3).
    page = b.internal("GET", f"/knowledge-collections/kb?signer={server_actor(a.origin)}&page=true").json()["document"]
    assert [vocabulary.validate_object(item, top_level=False) for item in page["orderedItems"]] == ["knowledge_base"]
    assert page["orderedItems"][0]["claire:signature"]["entities"][0] == ["Concept", "ada lovelace", 8.0]
    # B does not know A: there was no agreement and no entry on B.
    assert b.request("GET", "/api/admin/federation/known-servers", session=b.admin_session()).json()["servers"] == []
    # The admin of A sees the records of B.
    known = a.request("GET", "/api/admin/federation/known-servers", session=a_admin).json()["servers"]
    assert [(s["domain"], s["kb_count"], [kb["label"] for kb in s["kbs"]], s["kb_read"]["stored"]) for s in known] == [("b.example", 1, ["Analytical Engine"], 1)]

    # 4. The subject judgement of A includes the signature of the remote knowledge base.
    judged = a.request("POST", "/api/admin/themes/judge", session=a_admin, json={})
    assert judged.status_code == 200 and judged.json()["report"]["remote_memberships"] == 1

    # Criterion, part 1: the shared knowledge base of B is in a theme of A, with its origin link and its server.
    theme = a.request("GET", f"/themes/{theme_number}/", headers=JSON).json()
    assert (theme["shared_knowledge_bases"], theme["remote_knowledge_bases"]) == (1, 1)
    assert [(kb["remote"], kb["server"], kb["label"], kb["url"], kb["number"]) for kb in theme["knowledge_bases"]] == [
        (False, None, "Early computing", f"https://a.example/kb/{A_KB_NUMBER}/", str(A_KB_NUMBER)),
        (True, "b.example", "Analytical Engine", f"https://b.example/kb/{B_KB_NUMBER}/", None),
    ]
    listed = a.request("GET", "/themes", headers=JSON).json()["themes"]
    assert [(t["number"], t["knowledge_bases"], t["remote_knowledge_bases"]) for t in listed] == [(theme_number, 1, 1)]

    # Criterion, part 2: the root page of A. It has the theme with the knowledge base of B, and the new knowledge base of B in the recent activity. The list of the federated servers is not public (owner decision, 2026-11-19), and the old path /explore/federation is gone.
    root = a.request("GET", "/", headers={"Accept": "text/html"})
    assert root.status_code == 200
    assert f'href="https://b.example/kb/{B_KB_NUMBER}/"' in root.text and "(b.example)" in root.text and 'id="public-home-recent"' in root.text
    recent = root_recent(root.text)
    assert recent == [{"kind": "remote_knowledge_base", "at": recent[0]["at"], "server": "b.example", "label": "Analytical Engine", "url": f"https://b.example/kb/{B_KB_NUMBER}/", "knowledge_type": "real"}]
    assert "claire-bible 1.0.0" not in root.text
    assert a.request("GET", "/explore/federation").status_code == 404

    # Section 8.3 rule 4: before an account makes a shared knowledge base, it sees the similar ones of this server and of other servers.
    similar = a.request("GET", "/api/kb/similar?knowledge_type=real&label=Analytical%20Engine&seed=Ada%20Lovelace", session=a_admin).json()
    assert similar["matched"] is True
    assert [(kb["remote"], kb["server"], kb["label"], kb["score"]) for kb in similar["knowledge_bases"]] == [(False, None, "Early computing", 1.0), (True, "b.example", "Analytical Engine", 1.0)]  # with the same score, the knowledge base of this server is first
    assert a.request("GET", "/api/kb/similar?knowledge_type=fictional&label=Analytical%20Engine", session=a_admin).json() == {"matched": True, "knowledge_bases": []}
    assert a.request("GET", "/api/kb/similar?label=x", session=a_admin).status_code == 400

    # A one-sided control (Section 6.3): A blocks B. The records of B go from the theme and from the root page of A.
    assert a.request("POST", "/api/admin/federation/domain-blocks", session=a_admin, json={"domain": "b.example", "note": "test"}).status_code == 201
    assert [kb["remote"] for kb in a.request("GET", f"/themes/{theme_number}/", headers=JSON).json()["knowledge_bases"]] == [False]
    assert root_recent(a.request("GET", "/", headers={"Accept": "text/html"}).text) == []
    assert [t["remote_knowledge_bases"] for t in a.request("GET", "/themes", headers=JSON).json()["themes"]] == [0]
    # The server actor of A still reads the collection of B, because B has no block; a server that B blocks gets 404 (unit KF1).
    assert b.internal("GET", f"/knowledge-collections/kb?signer={server_actor(a.origin)}").status_code == 200
