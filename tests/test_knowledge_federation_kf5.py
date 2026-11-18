"""Unit KF5 of the federation of shared knowledge (KNOWLEDGE_FEDERATION_DESIGN.md Section 12.3): automatic contribution across servers.

The tests examine the interfaces between modules and the database: the migration step v34, the handler of an inbound automatic ``Offer`` with the switch of the receiver, the handler of a ``Reject`` with the reason ``automatic_not_accepted`` and the store of remote knowledge bases, the state of the switch in the record of a knowledge base of this server and its ``Update`` to the followers, and the checks of the sender for an automatic offer. One Claire server is a real app with the mock provider; the activities of the other server are made with the builders of the vocabulary and handed to the internal route as a sidecar does after it verified a signature. The end-to-end test with two servers is ``tests/test_knowledge_federation_kf5_e2e.py``.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

import pytest

from claire import auto_contribution
from claire.knowledge_federation import grants, handlers, journal, offers, remote_kbs, vocabulary
from claire.store import db as dbm
from claire.store.secrets import reset_secret_service
from claire.store.token_store import reset_token_stores
from tests.knowledge_federation_servers import KfWorld, server_actor
from tests.test_contributions import no_llm  # noqa: F401 (fixture)

A = "https://a.example"
ALICE = f"{A}/ap/actors/1846000000000000001"
HASHES = iter(f"{n:064x}" for n in range(1, 10_000))


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
def b(tmp_path: Path):
    world = KfWorld(tmp_path, "https://b.example")
    yield world
    world.close()


def _kb(world: KfWorld, label: str = "Cities") -> tuple[dict, str]:
    kb = world.client.post("/kb", json={"label": label, "knowledge_type": "real"}, headers=world.headers("ann")).json()["knowledge_base"]
    return kb, f"{world.origin}/ap/kb/{kb['number']}"


def _offer(kb_actor: str, *, name: str, automatic: bool, capability: str | None = None) -> dict:
    """An ``Offer`` of alice of A with a document of her own; ``automatic``: her automatic rule selected it."""
    extraction = vocabulary.build_extraction({"summary": "A city.", "key_claims": [], "entities": [{"name": "Korea", "type": "Concept", "aliases": [], "observations": [], "proposed_type": None}], "relations": []}, {})
    iri = f"{ALICE}/documents/doc_{name}"
    document = vocabulary.build_document({"title": f"Title {name}", "content_html": f"<p>{name}</p>", "canonical_url": f"https://example.org/{name}", "content_hash": next(HASHES), "published": 1_700_000_000.0, "updated": None}, {"id": iri, "attributed_to": ALICE, "origin": None, "root": iri}, extraction)
    contribution = vocabulary.build_contribution(document=document, attributed_to=ALICE, knowledge_base=kb_actor, contribution_id=None, automatic=automatic)
    return vocabulary.build_offer(activity_id=f"{ALICE}#offers/{name}", contribution=contribution, capability=capability)


def _deliver(world: KfWorld, activity: dict, *, recipient: str | None) -> tuple[str, str | None]:
    answer = world.deliver(activity, signer=activity["actor"], recipient=recipient).json()
    return answer["decision"], answer["reason"]


def _outbox(world: KfWorld) -> list[dict]:
    tasks = world.internal("GET", "/knowledge-outbox-tasks?limit=50").json()["tasks"]
    world.db(lambda conn: conn.execute("UPDATE kf_outbox SET lease_until=NULL"))
    return tasks


def _contributions(world: KfWorld, kb: dict) -> list[dict]:
    return world.client.get(f"/kb/{kb['number']}/contributions", headers=world.headers("ann")).json()["contributions"]


def _switch(world: KfWorld, on: bool) -> None:
    world.db(lambda conn: auto_contribution.set_switch(conn, on, updated_by=1))


# --- Migration step v34 -----------------------------------------------------------------------------------


def test_migration_v34_adds_the_column_and_keeps_the_rows_of_version_33(tmp_path: Path) -> None:
    path = tmp_path / "claire.db"
    conn = dbm.connect(path)
    try:
        dbm.init_db(conn)
        # The table of version 32 and 33: no column ``automatic``. One row of an offer of a submission.
        conn.execute("DROP TABLE kf_outbound_offers")
        conn.execute("CREATE TABLE kf_outbound_offers (id INTEGER PRIMARY KEY AUTOINCREMENT, offer_iri TEXT UNIQUE, account_id INTEGER, actor_iri TEXT, kb_iri TEXT NOT NULL, domain TEXT NOT NULL, source_kb_id INTEGER, source_document_id TEXT NOT NULL, submission_item_id INTEGER, capability_iri TEXT, state TEXT NOT NULL, result_iri TEXT, reason_code TEXT, created_at REAL NOT NULL, decided_at REAL)")
        conn.execute("INSERT INTO kf_outbound_offers (offer_iri, account_id, kb_iri, domain, source_document_id, submission_item_id, state, created_at) VALUES ('https://b.example/x#offers/1', 3, 'https://c.example/ap/kb/1', 'c.example', 'doc_1', 7, 'pending', 1.0)")
        conn.commit()
    finally:
        conn.close()
    for _ in range(2):
        conn = dbm.connect(path)
        try:
            dbm.init_db(conn)
            assert set(offers.TABLES["kf_outbound_offers"]) <= dbm._column_names(conn, "kf_outbound_offers")
            assert [tuple(r) for r in conn.execute("SELECT offer_iri, submission_item_id, automatic, state FROM kf_outbound_offers")] == [("https://b.example/x#offers/1", 7, 0, "pending")]
            assert conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE name LIKE 'kf_outbound_offers_retired%'").fetchone()[0] == 0
            assert dbm.stored_schema_version(conn) == dbm.SCHEMA_VERSION >= 34
        finally:
            conn.close()


# --- The receiver (design Section 12.3 rules 1 and 3) -----------------------------------------------------


def test_an_automatic_offer_needs_the_switch_of_the_receiver(b: KfWorld, no_llm) -> None:  # noqa: F811
    kb, kb_actor = _kb(b)
    # The switch is off (the default, KD4): a ``Reject`` at once with the reason ``automatic_not_accepted``, signed by the knowledge base actor; no contribution.
    assert _deliver(b, _offer(kb_actor, name="off", automatic=True), recipient=kb_actor) == ("processed", None)
    (task,) = _outbox(b)
    assert (task["kind"], task["signer"], task["recipient"], task["activity"][vocabulary.REASON_CODE]) == ("reject_offer", kb["number"], ALICE, "automatic_not_accepted")
    row = b.db(lambda conn: offers.inbound_of_iri(conn, f"{ALICE}#offers/off"))
    assert (row["state"], row["reason_code"], row["detail"]) == ("rejected", "automatic_not_accepted", "kb_auto_contribution_off")
    # A manual offer does not need the switch.
    assert _deliver(b, _offer(kb_actor, name="manual", automatic=False), recipient=kb_actor) == ("processed", None)
    assert [(c["title"], c["origin"], c["state"]) for c in _contributions(b, kb)] == [("Title manual", "bulk", "pending")]
    # With the switch on: a pending contribution with the origin ``auto`` (no grant); with a grant in effect: no approval, the origin stays ``auto``.
    _switch(b, True)
    assert _deliver(b, _offer(kb_actor, name="on", automatic=True), recipient=kb_actor) == ("processed", None)
    b.db(lambda conn: grants.give(conn, kb_number=int(kb["number"]), iri=grants.grant_iri(kb_actor), actor_iri=ALICE, domain="a.example", account_id=2, now=b.now))
    grant_iri = b.db(lambda conn: conn.execute("SELECT grant_iri FROM kf_grants").fetchone()[0])
    assert _deliver(b, _offer(kb_actor, name="granted", automatic=True, capability=grant_iri), recipient=kb_actor) == ("processed", None)
    made = {c["title"]: (c["origin"], c["state"], c["approved"], c["offer"]["with_grant"]) for c in _contributions(b, kb)}
    assert made == {"Title manual": ("bulk", "pending", False, False), "Title on": ("auto", "pending", False, False), "Title granted": ("auto", "visible", True, True)}
    assert no_llm == []


def test_the_record_of_a_knowledge_base_tells_the_switch_and_its_change_goes_to_the_followers(b: KfWorld) -> None:
    kb, kb_actor = _kb(b)
    signer = server_actor(A)
    page = lambda: b.internal("GET", f"/knowledge-collections/kb?signer={signer}&page=true").json()["document"]["orderedItems"]  # noqa: E731
    assert [item[vocabulary.ACCEPTS_AUTOMATIC] for item in page()] == [False]
    follow = vocabulary.build_follow(activity_id=f"{signer}#follows/1", server_actor=signer, knowledge_base=kb_actor)
    assert _deliver(b, follow, recipient=kb_actor) == ("processed", None)
    assert [task["kind"] for task in _outbox(b)] == ["accept_follow"]
    b.db(lambda conn: conn.execute("UPDATE kf_outbox SET status='delivered'"))
    # The admin turns the switch on: the record says it, and the followers get an ``Update`` of the record (``actors.record_digest``).
    _switch(b, True)
    assert [item[vocabulary.ACCEPTS_AUTOMATIC] for item in page()] == [True]
    (update,) = _outbox(b)
    assert (update["kind"], update["activity"]["object"][vocabulary.ACCEPTS_AUTOMATIC]) == ("update_knowledge_base", True)
    # The switch ``federation`` is part of the rule: the record of an actor document is the same object.
    document = b.internal("GET", f"/knowledge-kb-actors/{kb['number']}").json()["document"]
    assert document[vocabulary.ACCEPTS_AUTOMATIC] is True


# --- The sender (design Section 12.3 rules 1 and 2) -------------------------------------------------------


def _record(world: KfWorld, iri: str, *, accepts: bool | None) -> remote_kbs.RemoteKnowledgeBase:
    """A record of a remote knowledge base of ``c.example`` as a read stores it; ``accepts`` None: the record has no ``claire:acceptsAutomatic`` (a read before unit KF5)."""
    payload = {"id": iri, "name": "Remote", "url": iri.replace("/ap/kb/", "/kb/") + "/"}
    if accepts is not None:
        payload[vocabulary.ACCEPTS_AUTOMATIC] = accepts

    def _insert(conn) -> remote_kbs.RemoteKnowledgeBase:
        conn.execute(
            "INSERT INTO kf_remote_knowledge_bases (iri, domain, url, label, description, knowledge_type, document_count, entity_count, relation_count, signature, payload, first_seen_at, seen_at, changed_at) VALUES (?, 'c.example', ?, 'Remote', '', 'real', 1, 1, 0, NULL, ?, 1, 1, 1)",
            (iri, payload["url"], json.dumps(payload, sort_keys=True)),
        )
        found = remote_kbs.get_by_iri(conn, iri)
        assert found is not None
        return found

    return world.db(_insert)


def test_a_reject_for_the_switch_marks_the_records_of_that_server_and_the_documents_can_go_again(b: KfWorld) -> None:
    """Here B is the sender: ben of B offered documents to knowledge bases of ``c.example``."""
    first, second = "https://c.example/ap/kb/1", "https://c.example/ap/kb/2"
    _record(b, first, accepts=True)
    _record(b, second, accepts=None)

    def _offer_row(conn, name: str, *, automatic: bool) -> None:
        conn.execute(
            "INSERT INTO kf_outbound_offers (offer_iri, account_id, actor_iri, kb_iri, domain, source_kb_id, source_document_id, automatic, state, created_at) VALUES (?, 3, ?, ?, 'c.example', 1, ?, ?, 'pending', 1.0)",
            (f"{b.origin}/ap/actors/x#offers/{name}", f"{b.origin}/ap/actors/x", first, f"doc_{name}", 1 if automatic else 0),
        )

    b.db(lambda conn: [_offer_row(conn, "manual", automatic=False), _offer_row(conn, "auto", automatic=True)])

    def _reject(name: str) -> dict:
        return vocabulary.build_reject_offer(activity_id=f"{first}#rejects/{name}", knowledge_base=first, offer=f"{b.origin}/ap/actors/x#offers/{name}", offer_actor=f"{b.origin}/ap/actors/x", reason_code=vocabulary.AUTOMATIC_NOT_ACCEPTED)

    accepts = lambda: b.db(lambda conn: [remote_kbs.accepts_automatic(remote_kbs.get_by_iri(conn, iri)) for iri in (first, second)])  # noqa: E731
    # The reason on a manual offer changes no record: only the state of an automatic offer tells the switch.
    assert _deliver(b, _reject("manual"), recipient=None) == ("processed", None)
    assert accepts() == [True, None]
    changed_at = b.db(lambda conn: remote_kbs.get_by_iri(conn, first).changed_at)
    assert _deliver(b, _reject("auto"), recipient=None) == ("processed", None)
    assert accepts() == [False, False]
    assert b.db(lambda conn: remote_kbs.get_by_iri(conn, first).changed_at) == changed_at  # the content did not change: no new judgement run
    # The rows keep the reason; a refusal for the switch does not keep the document from that knowledge base (``RETRY_REASONS``), a rejection of the reviewers does.
    rows = b.db(lambda conn: {r["source_document_id"]: (r["state"], r["reason_code"]) for r in offers.automatic_of_account(conn, 3)})
    assert rows == {"doc_auto": ("rejected", "automatic_not_accepted")}
    b.db(lambda conn: conn.execute("UPDATE kf_outbound_offers SET state='rejected', reason_code='off_subject' WHERE source_document_id='doc_manual'"))
    assert b.db(lambda conn: offers.automatic_offered(conn, account_id=3, kb_iri=first)) == {"doc_manual"}


def test_the_sender_skips_a_knowledge_base_whose_server_accepts_no_automatic_contribution(b: KfWorld) -> None:
    closed = _record(b, "https://c.example/ap/kb/3", accepts=False)
    row = b.db(lambda conn: offers.send(conn, account_id=3, source_kb_id=1, source_document_id="doc_x", remote_kb=closed, submission_item_id=None, pending_limit=5, automatic=True, origin=b.origin, now=b.now))
    assert (row["state"], row["reason_code"], row["automatic"], row["offer_iri"]) == ("skipped", "automatic_not_accepted", 1, None)
    # The same record does not stop a manual offer: the account selected the document itself. (The account has no document here: the next check skips it.)
    manual = b.db(lambda conn: offers.send(conn, account_id=3, source_kb_id=1, source_document_id="doc_x", remote_kb=closed, submission_item_id=None, pending_limit=5, automatic=False, origin=b.origin, now=b.now))
    assert (manual["state"], manual["reason_code"], manual["automatic"]) == ("skipped", "document_not_extracted", 0)
    assert _outbox(b) == []


def test_the_remote_targets_of_a_rule(b: KfWorld) -> None:
    """``memberships`` selects the knowledge bases of other servers that gave the account a grant in its term; ``themes`` selects the remote members of the themes of the rule; ``mutual`` selects none on another server."""
    from claire.store import themes as themes_store

    granted = _record(b, "https://c.example/ap/kb/10", accepts=True)
    member = _record(b, "https://c.example/ap/kb/11", accepts=True)
    _record(b, "https://c.example/ap/kb/12", accepts=True)
    now = b.now
    b.db(lambda conn: grants.receive(conn, iri=f"{granted.iri}#grants/1", kb_iri=granted.iri, domain="c.example", account_id=3, published_at=now - 10, end_time=now + 1000, now=now))

    def _theme(conn) -> int:
        theme = themes_store.create_theme(conn, label="Cities", knowledge_type="real")
        themes_store.set_admin_remote_member(conn, theme_id=theme["id"], remote_kb_id=member.id, pinned=True, excluded=False)
        themes_store.update_theme(conn, theme["id"], state="active")  # a theme with only pinned remote members is active (unit KF2)
        return int(theme["id"])

    theme_id = b.db(_theme)
    rule = {"to_memberships": True, "to_mutual": True, "include_uploads": False, "theme_ids": [theme_id]}
    found = b.db(lambda conn: [r.iri for r in auto_contribution.remote_targets(conn, 3, rule, now=now)])
    assert found == [granted.iri, member.iri]
    assert b.db(lambda conn: [r.iri for r in auto_contribution.remote_targets(conn, 3, {**rule, "to_memberships": False}, now=now)]) == [member.iri]
    assert b.db(lambda conn: auto_contribution.remote_targets(conn, 3, {**rule, "to_memberships": False, "theme_ids": []}, now=now)) == []
    # The state of the account: ben of B is a federation actor; an origin and the switch ``federation`` are necessary.
    assert b.db(lambda conn: [auto_contribution.remote_state(conn, 3, origin) for origin in (b.origin, None)]) == ["on", "no_origin"]
