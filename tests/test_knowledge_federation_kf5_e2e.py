"""End-to-end test of the completion criterion of unit KF5 (KNOWLEDGE_FEDERATION_DESIGN.md Sections 12.3 and 17).

Criterion: with ``kb_auto_contribution`` on both servers, a new shared knowledge base on B gets the matching documents of an account of A after a run. The second test is the case with the switch off on B: the record of B tells A that B accepts no automatic contributions, and an automatic offer that comes while the switch of B is off gets a ``Reject`` with the reason ``automatic_not_accepted``.

Two Claire servers run as two app objects with two databases: A (``https://a.example``) and B (``https://b.example``), both in network mode with the switch ``federation`` on and the mock provider. On A, ``ann`` founds the shared knowledge base ``Places`` about Korean cities, and the judgement of A makes a theme for it; ``ben`` of A has documents in his personal knowledge base and an automatic rule with that theme. After the rule is on, ``ann`` of B founds the new shared knowledge base ``Cities`` about the same subject.

What is simulated: the two Fedify sidecars and the network between them. In a real exchange the sidecar of a server reads the ``/kb`` collection of the other server with a signed GET, takes the outbound activities from its Claire, signs each one with the key of its actor (the server actor, a knowledge base actor, or an account actor), and the sidecar of the other server verifies the signature and hands the JSON to its Claire. The test does these steps with direct calls to the internal federation API of A and of B: it gives the receiving server the actor that a sidecar found in a valid signature and the activity JSON without a change. No HTTP request leaves the test process, and no signature is made or verified. The daily interval of the read of a ``/kb`` collection is made shorter with a change of the time of the last read. A recent sign-in is a session row with the time of now.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

import pytest

from claire.collaboration import auto_contribution, theme_judgement
from claire.core import ids
from claire.federation.knowledge import handlers, journal, remote_kbs, vocabulary
from claire.ontology.base import normalize_name
from claire.store.secrets import reset_secret_service
from claire.store.token_store import reset_token_stores
from tests.knowledge_federation_servers import KfWorld, server_actor
from tests.test_contributions import no_llm  # noqa: F401 (fixture)
from tests.test_knowledge_federation_kf4_e2e import actor_of, as_session, read_kb_collection, sidecar_delivers

ANN, BEN = 2, 3
KOREA = ["Korea", "Seoul", "Busan", "Daegu", "Incheon"]


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


def personal_document(world: KfWorld, account_id: int, name: str, entities: list[str]) -> str:
    """One document of the personal knowledge base of the account, with its extraction and its entities (the keys of the document match, design Section 8.5)."""
    kb_id = world.manager.personal_kb_for(account_id).id
    document_id = f"doc_{name}"
    url = f"https://example.org/{name}"
    text = f"{name}: " + ", ".join(entities) + "."
    extraction = {"summary": text, "key_claims": [], "entities": [{"name": e, "type": "Concept", "aliases": [], "observations": [f"{e} in {name}."]} for e in entities], "relations": []}

    def _insert(conn) -> None:
        conn.execute(
            "INSERT INTO documents (id, kb_id, title, raw_text, detail, detail_format, url, canonical_url, source_type, content_hash, hidden, fetched_at) VALUES (?, ?, ?, ?, ?, 'adoc', ?, ?, 'web', ?, 0, ?)",
            (document_id, kb_id, name, text, text, url, url, hashlib.sha256(text.encode()).hexdigest(), time.time()),
        )
        conn.execute("INSERT INTO extractions (kb_id, document_id, provider, model, prompt_version, raw_response, created_at) VALUES (?, ?, 'mock', 'mock', 'test', ?, ?)", (kb_id, document_id, json.dumps(extraction), time.time()))
        for entity in entities:
            row = conn.execute("SELECT id, sources FROM entities WHERE kb_id=? AND norm_name=?", (kb_id, normalize_name(entity))).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO entities (id, kb_id, type, name, norm_name, aliases, sources, created_at, updated_at) VALUES (?, ?, 'Concept', ?, ?, '[]', ?, 1, 1)",
                    (f"ent_{account_id}_{normalize_name(entity)}", kb_id, entity, normalize_name(entity), json.dumps([document_id])),
                )
            else:
                conn.execute("UPDATE entities SET sources=? WHERE id=?", (json.dumps([*json.loads(row["sources"]), document_id]), row["id"]))

    world.db(_insert)
    return document_id


def fill_shared_kb(world: KfWorld, kb_id: int, names: list[str], *, documents: int = 6) -> None:
    """Visible documents and one entity for each name in a shared knowledge base, so that the judgement gives it a signature (as ``KfServer.fill_kb``)."""

    def _fill(conn) -> None:
        doc_ids = [f"doc_{kb_id}_{i}" for i in range(documents)]
        for doc_id in doc_ids:
            conn.execute("INSERT INTO documents (id, kb_id, title, raw_text, hidden) VALUES (?, ?, ?, 'text', 0)", (doc_id, kb_id, doc_id))
        for rank, name in enumerate(names):
            conn.execute(
                "INSERT INTO entities (id, kb_id, type, name, norm_name, aliases, sources, created_at, updated_at) VALUES (?, ?, 'Concept', ?, ?, '[]', ?, 1, 1)",
                (f"ent_{kb_id}_{rank}", kb_id, name, normalize_name(name), json.dumps(doc_ids[: max(1, documents - rank)])),
            )

    world.db(_fill)


def judge(world: KfWorld) -> None:
    world.db(lambda conn: theme_judgement.run_judgement(conn))


def switch(world: KfWorld, state: str) -> None:
    """The admin turns the automatic contribution of the server on or off (``/admin/federation``)."""
    answer = world.client.put("/api/admin/auto-contribution", json={"state": state}, headers=world.headers("admin"))
    assert answer.status_code == 200 and answer.json()["state"] == state, answer.text


def read_again(a: KfWorld, b: KfWorld) -> None:
    """The next daily read of the ``/kb`` collection of B by the sidecar of A (design Section 8.1 rule 2). The time of the last read goes one day back, so that the read is due now."""
    a.db(lambda conn: conn.execute("UPDATE kf_remote_kb_reads SET last_read_at=last_read_at-?", (remote_kbs.READ_INTERVAL_SECONDS + 60,)))
    assert a.internal("GET", "/knowledge-kb-reads").json()["reads"] == [{"domain": "b.example", "url": "https://b.example/ap/kb"}]
    page = b.internal("GET", f"/knowledge-collections/kb?signer={server_actor(a.origin)}&page=true").json()["document"]
    assert a.internal("POST", "/knowledge-kb-reads", {"domain": "b.example", "items": page["orderedItems"], "complete": True, "error": None}).status_code == 200


def a_run(a: KfWorld) -> None:
    """A run of the automatic rules of A: here the judgement run of the admin (design Section 9.9 rule 4: after each judgement run)."""
    assert a.client.post("/api/admin/themes/judge", headers=a.headers("admin")).status_code == 200


def setup_a(a: KfWorld) -> tuple[dict, str, dict[str, str]]:
    """A: the shared knowledge base Places with its theme, the documents of ben, and the rule of ben with that theme (turned on after the dry run, with a recent sign-in). Return Places, the number of the theme, and the documents."""
    places = a.client.post("/kb", json={"label": "Places", "knowledge_type": "real"}, headers=a.headers("ann")).json()["knowledge_base"]
    fill_shared_kb(a, int(places["id"]), KOREA)
    judge(a)
    (theme,) = a.client.get("/themes", headers=a.headers("ben")).json()["themes"]
    docs = {
        "seoul": personal_document(a, BEN, "seoul", ["Korea", "Seoul", "Busan"]),
        "daegu": personal_document(a, BEN, "daegu", ["Korea", "Daegu", "Incheon"]),
        "space": personal_document(a, BEN, "space", ["Mars", "Jupiter", "Saturn"]),
        "never": personal_document(a, BEN, "never", ["Korea", "Seoul", "Incheon"]),
    }
    # ben sets the flag "never contribute" on one document (shared knowledge base design Section 9.9 rule 2).
    assert a.client.put(f"/api/contribution-blocks/{docs['never']}", headers=a.headers("ben")).status_code == 200
    rule = {"state": "active", "memberships": False, "mutual": False, "include_uploads": False, "themes": [theme["number"]]}
    saved = as_session(a, BEN, "PUT", "/api/auto-rule", json=rule)
    assert saved.status_code == 200 and saved.json()["rule"]["state"] == "active", saved.text
    return places, theme["number"], docs


def dry_run(a: KfWorld, theme_number: str) -> dict:
    rule = {"state": "active", "memberships": False, "mutual": False, "include_uploads": False, "themes": [theme_number]}
    answer = a.client.post("/api/auto-rule/dry-run", json=rule, headers=a.headers("ben"))
    assert answer.status_code == 200, answer.text
    return answer.json()


def remote_rows(a: KfWorld) -> list[dict]:
    """The automatic contributions of ben to other servers in ``GET /api/contributions?origin=auto``."""
    return [c for c in a.client.get("/api/contributions?origin=auto", headers=a.headers("ben")).json()["contributions"] if c.get("remote_target")]


def new_cities(b: KfWorld) -> tuple[dict, str]:
    """B: ann founds the new shared knowledge base Cities about the same subject; the judgement of B gives it a signature."""
    cities = b.client.post("/kb", json={"label": "Cities", "knowledge_type": "real"}, headers=b.headers("ann")).json()["knowledge_base"]
    fill_shared_kb(b, int(cities["id"]), KOREA)
    judge(b)
    return cities, f"{b.origin}/ap/kb/{cities['number']}"


def test_kf5_a_new_shared_knowledge_base_on_b_gets_the_matching_documents_of_an_account_of_a(servers, no_llm) -> None:  # noqa: F811
    a, b = servers
    ben_actor = actor_of(a, BEN)
    # 1. The admins of the two servers turn on the automatic contribution (design Section 12.3 rule 1: on both servers).
    switch(a, "on")
    switch(b, "on")
    places, theme_number, docs = setup_a(a)
    # The rule ran when it was turned on: the local target Places got the matching documents (ben is not a member: pending). No remote target yet.
    assert a.db(lambda conn: sorted(r[0] for r in conn.execute("SELECT source_document_id FROM kb_contributions WHERE kb_id=? AND origin='auto'", (places["id"],)))) == sorted([docs["seoul"], docs["daegu"]])
    assert remote_rows(a) == []

    # 2. Criterion: a new shared knowledge base on B. A reads the ``/kb`` collection of B (unit KF2); the record has the signature of Cities and tells that B accepts automatic contributions.
    cities, kb_actor = new_cities(b)
    read_kb_collection(a, b)
    record = a.db(lambda conn: remote_kbs.get_by_iri(conn, kb_actor))
    assert record is not None and remote_kbs.accepts_automatic(record) is True and record.signature is not None
    # The judgement of A puts Cities of B in the theme of Places (unit KF2). The dry run shows the remote target with its server and its mode; the document with "never contribute" and the document of another subject are not in it.
    judge(a)
    dry = dry_run(a, theme_number)
    assert dry["server_switch"] is True and dry["remote_state"] == "on"
    (target,) = [t for t in dry["targets"] if t["remote"]]
    assert (target["label"], target["server"], target["actor"], target["remote_id"], target["mode"], target["knowledge_base"]) == ("Cities", "b.example", kb_actor, record.id, "pending", None)
    assert sorted(d["document"] for d in target["documents"]) == sorted([docs["seoul"], docs["daegu"]])

    # 3. A run on A: the account actor of ben sends one ``Offer`` with ``claire:automatic`` for each matching document.
    a_run(a)
    tasks = a.internal("GET", "/knowledge-outbox-tasks").json()["tasks"]
    sent = [task for task in tasks if task["kind"] == "offer_contribution"]
    assert len(sent) == 2 and all(task["signer"] == f"account:{ids.format_public_id((1 << 57) + BEN)}" and task["recipient"] == kb_actor for task in sent)
    assert all(task["activity"]["object"][vocabulary.AUTOMATIC] is True and vocabulary.validate_activity(task["activity"]) == "offer_contribution" for task in sent)
    assert sorted(row["document"] for row in remote_rows(a)) == sorted([docs["seoul"], docs["daegu"]])
    assert {(row["state"], row["origin"], row["remote_target"]["server"], row["remote_target"]["delivery"]) for row in remote_rows(a)} == {("pending", "auto", "b.example", "pending")}
    a.db(lambda conn: conn.execute("UPDATE kf_outbox SET lease_until=NULL"))
    assert sorted(sidecar_delivers(a, b)) == [("follow", "processed"), ("offer_contribution", "processed"), ("offer_contribution", "processed")]

    # Criterion: Cities on B has the matching documents of ben as contributions with the origin ``auto``. They wait for the reviewers of B (ben has no grant), with the remote credit.
    pending = b.client.get(f"/kb/{cities['number']}/contributions?state=pending", headers=b.headers("ann")).json()["contributions"]
    assert sorted(c["title"] for c in pending) == ["daegu", "seoul"]
    assert {(c["state"], c["origin"], c["remote"]["credit"], c["offer"]["server"]) for c in pending} == {("pending", "auto", ben_actor, "a.example")}
    # A second run sends nothing again: each document has its offer.
    a_run(a)
    assert [task for task in a.internal("GET", "/knowledge-outbox-tasks").json()["tasks"] if task["kind"] == "offer_contribution"] == []

    # 4. A reviewer of B approves one: ``Accept`` with ``result``; the list of automatic contributions of ben on A shows the decision of B.
    seoul = next(c for c in pending if c["title"] == "seoul")
    assert b.client.patch(f"/kb/{cities['number']}/contributions/{seoul['number']}", json={"action": "approve"}, headers=b.headers("ann")).json()["contribution"]["state"] == "visible"
    assert sorted(sidecar_delivers(b, a)) == [("accept_follow", "processed"), ("accept_offer", "processed")]
    states = {row["document"]: (row["state"], row["approved"], row["remote_target"]["result"]) for row in remote_rows(a)}
    assert states == {docs["seoul"]: ("accepted", True, f"{kb_actor}/documents/{seoul['number']}"), docs["daegu"]: ("pending", False, None)}
    assert no_llm == []


def test_kf5_switch_off_on_b_the_sender_sees_it_and_an_automatic_offer_gets_a_clear_reject(servers) -> None:
    a, b = servers
    switch(a, "on")
    # B keeps its switch off (the default, KD4).
    _places, theme_number, docs = setup_a(a)
    cities, kb_actor = new_cities(b)
    read_kb_collection(a, b)
    judge(a)

    # 1. The record of Cities tells that B accepts no automatic contributions: the dry run shows the target as ``closed`` with no document, and a run sends no ``Offer``.
    assert a.db(lambda conn: remote_kbs.accepts_automatic(remote_kbs.get_by_iri(conn, kb_actor))) is False
    (target,) = [t for t in dry_run(a, theme_number)["targets"] if t["remote"]]
    assert (target["label"], target["server"], target["mode"], target["documents"]) == ("Cities", "b.example", "closed", [])
    a_run(a)
    assert [task["kind"] for task in a.internal("GET", "/knowledge-outbox-tasks").json()["tasks"]] == ["follow"]
    assert remote_rows(a) == []

    # 2. The admin of B turns the switch on. The next read of A shows it; the next run sends the offers.
    switch(b, "on")
    read_again(a, b)
    assert a.db(lambda conn: remote_kbs.accepts_automatic(remote_kbs.get_by_iri(conn, kb_actor))) is True
    a_run(a)
    # 3. The admin of B turns the switch off again before the offers come. B answers each automatic offer at once with ``Reject`` and the reason ``automatic_not_accepted``; it makes no contribution.
    switch(b, "off")
    a.db(lambda conn: conn.execute("UPDATE kf_outbox SET lease_until=NULL"))
    assert sorted(sidecar_delivers(a, b)) == [("follow", "processed"), ("offer_contribution", "processed"), ("offer_contribution", "processed")]
    assert b.client.get(f"/kb/{cities['number']}/contributions", headers=b.headers("ann")).json()["contributions"] == []
    assert b.db(lambda conn: sorted((r[0], r[1]) for r in conn.execute("SELECT reason_code, detail FROM kf_inbound_offers"))) == [("automatic_not_accepted", "kb_auto_contribution_off")] * 2
    delivered = sidecar_delivers(b, a)
    assert sorted(delivered) == [("accept_follow", "processed"), ("reject_offer", "processed"), ("reject_offer", "processed")]
    # The refusal is clear on A: the state and the reason of each offer, and the record of Cities says that B accepts none now. The dry run shows ``closed`` again.
    assert {(row["document"], row["state"], row["reason"]) for row in remote_rows(a)} == {(docs["seoul"], "rejected", "automatic_not_accepted"), (docs["daegu"], "rejected", "automatic_not_accepted")}
    assert [t["mode"] for t in dry_run(a, theme_number)["targets"] if t["remote"]] == ["closed"]

    # 4. A refusal for the switch is no decision of the reviewers: when B accepts automatic contributions again, the same documents go (design Section 9.9 rule 2 keeps only a rejected or removed document out).
    switch(b, "on")
    read_again(a, b)
    a_run(a)
    again = [task for task in a.internal("GET", "/knowledge-outbox-tasks").json()["tasks"] if task["kind"] == "offer_contribution"]
    assert sorted(task["activity"]["object"][vocabulary.DOCUMENT]["name"] for task in again) == ["daegu", "seoul"]
    # The rule of the sender needs the switch of A too: with it off, no run starts (design Section 12.3 rule 1).
    switch(a, "off")
    assert a.db(lambda conn: auto_contribution.run_all(conn, origin=a.origin)) == 0
