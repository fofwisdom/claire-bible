"""Unit KF4 of the federation of shared knowledge (KNOWLEDGE_FEDERATION_DESIGN.md Sections 12.1, 12.2, and 14): contributions across servers and remote collaborators.

The tests examine the interfaces between modules and the database: the migration step v32, the handlers of the inbox journal with the stores of offers and grants and with the outbox, the checks of an inbound ``Offer`` (KS2, KS3), the offers of an account that is not a federation actor, and ``Delete`` of a remote actor through the inbox event route. One Claire server (B) is a real app with the mock provider; the activities of the other server (``https://a.example``) are made with the builders of the vocabulary and handed to the internal route as a sidecar does after it verified a signature.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

from claire import contributions, ids
from claire.knowledge_federation import grants, handlers, journal, offers, remote_kbs, vocabulary
from claire.store import db as dbm
from claire.store.secrets import reset_secret_service
from claire.store.token_store import reset_token_stores
from tests.knowledge_federation_servers import KfWorld
from tests.test_contributions import EXTRACTION, _personal_document, no_llm  # noqa: F401 (fixture)

A = "https://a.example"
ALICE = f"{A}/ap/actors/1846000000000000001"
CAROL = f"{A}/ap/actors/1846000000000000002"
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


def _offer(kb_actor: str, *, actor: str = ALICE, name: str = "one", capability: str | None = None, **changes) -> dict:
    """An ``Offer`` of an account of A with a document of its own."""
    extraction = vocabulary.build_extraction({"summary": "A city.", "key_claims": [], "entities": [{"name": "Korea", "type": "Concept", "aliases": [], "observations": [], "proposed_type": None}], "relations": []}, {})
    iri = f"{actor}/documents/doc_{name}"
    document = vocabulary.build_document({"title": f"Title {name}", "content_html": f"<p>{name}</p>", "canonical_url": f"https://example.org/{name}", "content_hash": next(HASHES), "published": 1_700_000_000.0, "updated": None}, {"id": iri, "attributed_to": actor, "origin": None, "root": iri, **changes}, extraction)
    contribution = vocabulary.build_contribution(document=document, attributed_to=actor, knowledge_base=kb_actor, contribution_id=None, automatic=False)
    return vocabulary.build_offer(activity_id=f"{actor}#offers/{name}", contribution=contribution, capability=capability)


def _deliver(world: KfWorld, activity: dict, *, recipient: str | None) -> tuple[str, str | None]:
    answer = world.deliver(activity, signer=activity["actor"], recipient=recipient).json()
    return answer["decision"], answer["reason"]


def _outbox(world: KfWorld) -> list[tuple[str, str, str]]:
    """``(kind, signer, recipient)`` of each task that waits; the lease is removed so that the next call gives the task again."""
    tasks = world.internal("GET", "/knowledge-outbox-tasks?limit=50").json()["tasks"]
    world.db(lambda conn: conn.execute("UPDATE kf_outbox SET lease_until=NULL"))
    return [(task["kind"], task["signer"], task["recipient"]) for task in tasks]


def _contributions(world: KfWorld, kb: dict) -> list[dict]:
    return world.client.get(f"/kb/{kb['number']}/contributions", headers=world.headers("ann")).json()["contributions"]


# --- Migration step v32 -----------------------------------------------------------------------------------


def test_migration_v32_makes_the_tables_and_keeps_a_table_of_another_form(tmp_path: Path) -> None:
    path = tmp_path / "claire.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE kf_grants (id INTEGER PRIMARY KEY, note TEXT)")
    conn.execute("INSERT INTO kf_grants (id, note) VALUES (1, 'kept')")
    conn.commit()
    conn.close()
    for _ in range(2):
        conn = dbm.connect(path)
        try:
            dbm.init_db(conn)
            for module in (offers, grants):
                for table, columns in module.TABLES.items():
                    assert set(columns) <= dbm._column_names(conn, table), table
            assert [tuple(r) for r in conn.execute("SELECT id, note FROM kf_grants_retired_v32")] == [(1, "kept")]
            assert dbm.stored_schema_version(conn) == dbm.SCHEMA_VERSION >= 32
        finally:
            conn.close()


# --- Inbound offers: the checks of the receiver (design Section 12.1 rule 3, KS2, KS3) ------------------------


def test_an_offer_makes_a_pending_contribution_and_the_refusals_make_none(b: KfWorld, no_llm) -> None:  # noqa: F811
    kb, kb_actor = _kb(b)
    # A knowledge base that this server does not have, and an inbox of another actor.
    assert _deliver(b, _offer(f"{b.origin}/ap/kb/1846000000000000999", name="none"), recipient=None) == ("refused", "knowledge_base_not_found")
    assert _deliver(b, _offer(kb_actor, name="inbox"), recipient=f"{b.origin}/ap/kb/1846000000000000999") == ("refused", "recipient_mismatch")
    # The document must be the own document of the contributor: not a copy, its own root, and on the server of the actor.
    assert _deliver(b, _offer(kb_actor, name="copy", origin="https://c.example/ap/kb/1/documents/2"), recipient=kb_actor) == ("refused", "not_own_document")
    assert _deliver(b, _offer(kb_actor, name="root", root="https://a.example/ap/kb/1/documents/2"), recipient=kb_actor) == ("refused", "not_own_document")
    assert _contributions(b, kb) == [] and _outbox(b) == []
    # A valid offer: the pending contribution with the remote credit. It also comes through the shared inbox.
    assert _deliver(b, _offer(kb_actor, name="one"), recipient=None) == ("processed", None)
    (pending,) = _contributions(b, kb)
    assert (pending["state"], pending["origin"], pending["remote"], pending["offer"]["server"]) == ("pending", "bulk", {"document": f"{ALICE}/documents/doc_one", "knowledge_base": None, "credit": ALICE}, "a.example")
    # A pending contribution is no reason to follow an actor, and no answer goes out before a decision.
    assert _outbox(b) == []
    # The same document again (another offer): the knowledge base has it. The answer is a ``Reject`` with the reason code ``duplicate``, signed by the knowledge base actor; the journal row is processed.
    again = _offer(kb_actor, name="one")
    again["id"] = f"{ALICE}#offers/again"
    assert _deliver(b, again, recipient=kb_actor) == ("processed", None)
    assert _outbox(b) == [("reject_offer", kb["number"], ALICE)]
    row = b.db(lambda conn: offers.inbound_of_iri(conn, again["id"]))
    assert (row["state"], row["reason_code"], row["detail"], row["contribution_public_id"]) == ("rejected", "duplicate", "contribution_duplicate", None)
    assert len(_contributions(b, kb)) == 1 and no_llm == []


def test_the_limits_for_a_remote_account_and_for_a_domain(b: KfWorld) -> None:
    kb, kb_actor = _kb(b)
    b.db(lambda conn: [contributions.write_limit(conn, key, value, updated_by=1) for key, value in (("remote_account_pending_limit", 2), ("remote_domain_pending_limit", 3), ("remote_domain_daily_offers", 6))])
    # The pending limit of one remote account in one knowledge base (KS3).
    assert [_deliver(b, _offer(kb_actor, name=f"a{n}"), recipient=kb_actor)[0] for n in range(3)] == ["processed"] * 3
    assert b.db(lambda conn: [dict(r) for r in conn.execute("SELECT state, detail FROM kf_inbound_offers ORDER BY id")]) == [{"state": "pending", "detail": None}, {"state": "pending", "detail": None}, {"state": "rejected", "detail": "pending_limit"}]
    # The pending limit of one domain on this server: another account of the same server.
    assert [_deliver(b, _offer(kb_actor, actor=CAROL, name=f"c{n}"), recipient=kb_actor)[0] for n in range(2)] == ["processed"] * 2
    assert b.db(lambda conn: [r[0] for r in conn.execute("SELECT detail FROM kf_inbound_offers WHERE actor_iri=? ORDER BY id", (CAROL,))]) == [None, "domain_pending_limit"]
    assert len(_contributions(b, kb)) == 3
    assert sorted(_outbox(b)) == [("reject_offer", kb["number"], ALICE), ("reject_offer", kb["number"], CAROL)]
    # The limit of the offers of one domain in 24 hours (KS2): the sixth offer made a row, the seventh is refused without an answer.
    assert _deliver(b, _offer(kb_actor, actor=CAROL, name="c5"), recipient=kb_actor)[0] == "processed"
    assert _deliver(b, _offer(kb_actor, actor=CAROL, name="c6"), recipient=kb_actor) == ("refused", "daily_limit")
    # An archived knowledge base takes no contribution: the answer is a ``Reject``.
    other, other_actor = _kb(b, "Old")
    assert b.client.patch(f"/kb/{other['number']}/", json={"state": "archived"}, headers=b.headers("ann")).status_code == 200
    b.db(lambda conn: contributions.write_limit(conn, "remote_domain_daily_offers", 50, updated_by=1))
    assert _deliver(b, _offer(other_actor, name="old"), recipient=other_actor)[0] == "processed"
    assert b.db(lambda conn: conn.execute("SELECT detail FROM kf_inbound_offers ORDER BY id DESC LIMIT 1").fetchone()[0]) == "kb_archived"


def test_answers_and_grants_come_only_from_the_actor_that_owns_them(b: KfWorld) -> None:
    kb_actor = "https://a.example/ap/kb/1846000000000000100"
    other_actor = "https://a.example/ap/kb/1846000000000000101"
    ben = f"{b.origin}/ap/actors/{ids.format_public_id((1 << 57) + 3)}"

    def _row(conn):
        conn.execute("INSERT INTO kf_outbound_offers (offer_iri, account_id, actor_iri, kb_iri, domain, source_kb_id, source_document_id, state, created_at) VALUES (?, 3, ?, ?, 'a.example', 1, 'doc_x', 'pending', 1)", (f"{ben}#offers/1", ben, kb_actor))

    b.db(_row)
    # An ``Accept`` of another knowledge base actor is not an answer to the offer.
    accept = vocabulary.build_accept_offer(activity_id=f"{other_actor}#accepts/1", knowledge_base=other_actor, offer=f"{ben}#offers/1", offer_actor=ben, result=f"{other_actor}/documents/5")
    assert _deliver(b, accept, recipient=ben) == ("refused", "offer_not_found")
    accept = vocabulary.build_accept_offer(activity_id=f"{kb_actor}#accepts/1", knowledge_base=kb_actor, offer=f"{ben}#offers/1", offer_actor=ben, result=f"{kb_actor}/documents/5")
    assert _deliver(b, accept, recipient=ben) == ("processed", None)
    # A later ``Reject`` does not change an offer that has a decision.
    reject = vocabulary.build_reject_offer(activity_id=f"{kb_actor}#rejects/1", knowledge_base=kb_actor, offer=f"{ben}#offers/1", offer_actor=ben, reason_code="spam")
    assert _deliver(b, reject, recipient=ben) == ("processed", None)
    assert b.db(lambda conn: tuple(conn.execute("SELECT state, result_iri, reason_code FROM kf_outbound_offers").fetchone())) == ("accepted", f"{kb_actor}/documents/5", None)
    # A ``Grant`` for an actor that is not a federation actor of this server, and one in the inbox of another account.
    now = b.now
    for target, recipient, reason in ((f"{b.origin}/ap/actors/1846000000000000777", None, "account_not_found"), (ben, f"{b.origin}/ap/actors/{ids.format_public_id((1 << 57) + 2)}", "recipient_mismatch")):
        grant = vocabulary.build_grant(activity_id=f"{kb_actor}#grants/{reason}", knowledge_base=kb_actor, account_actor=target, published=now, end_time=now + 100)
        assert _deliver(b, grant, recipient=recipient) == ("refused", reason)
    grant = vocabulary.build_grant(activity_id=f"{kb_actor}#grants/1", knowledge_base=kb_actor, account_actor=ben, published=now, end_time=now + 100)
    assert _deliver(b, grant, recipient=ben) == ("processed", None)
    assert b.db(lambda conn: grants.capability_for(conn, account_id=3, kb_iri=kb_actor, now=now + 1)) == f"{kb_actor}#grants/1"
    # After the end time the grant is not a capability (KS8).
    assert b.db(lambda conn: grants.capability_for(conn, account_id=3, kb_iri=kb_actor, now=now + 101)) is None
    # Only the actor that gave the grant can revoke it.
    revoke = vocabulary.build_revoke(activity_id=f"{other_actor}#grants/1/revoke", knowledge_base=other_actor, account_actor=ben, grant=f"{other_actor}#grants/1")
    assert _deliver(b, revoke, recipient=ben) == ("refused", "grant_not_found")
    revoke = vocabulary.build_revoke(activity_id=f"{kb_actor}#grants/1/revoke", knowledge_base=kb_actor, account_actor=ben, grant=f"{kb_actor}#grants/1")
    assert _deliver(b, revoke, recipient=ben) == ("processed", None)
    assert b.db(lambda conn: grants.capability_for(conn, account_id=3, kb_iri=kb_actor, now=now + 1)) is None


# --- Outbound offers: the checks of the sender (design Section 12.1 rule 2) ------------------------------------


def test_an_account_that_is_not_a_federation_actor_sends_no_offer(b: KfWorld) -> None:
    """A is the sender here (the world ``b`` plays it): the remote knowledge base is one of ``https://a.example``."""
    kb_actor = "https://a.example/ap/kb/1846000000000000100"
    document = _personal_document(b.settings, b.manager, 3, "Seoul and Busan are cities of Korea. " * 3, url="https://example.org/cities", extraction=EXTRACTION)
    personal = b.manager.personal_kb_for(3).id

    def _record(conn):
        conn.execute("INSERT INTO kf_remote_knowledge_bases (iri, domain, url, label, description, knowledge_type, document_count, entity_count, relation_count, payload, first_seen_at, seen_at, changed_at) VALUES (?, 'a.example', 'https://a.example/kb/1/', 'Cities', '', 'real', 0, 0, 0, '{}', 1, 1, 1)", (kb_actor,))
        return remote_kbs.get_by_iri(conn, kb_actor)

    record = b.db(_record)

    def _send(conn):
        return offers.send(conn, account_id=3, source_kb_id=personal, source_document_id=document, remote_kb=record, submission_item_id=None, pending_limit=1, automatic=False, origin=b.origin, now=b.now)

    # No public profile: the account has no actor that can sign (design Section 12.1 rule 2).
    b.db(lambda conn: conn.execute("UPDATE accounts SET profile_public=0 WHERE id=3"))
    assert b.db(_send)["reason_code"] == "not_a_federation_actor"
    b.db(lambda conn: conn.execute("UPDATE accounts SET profile_public=1 WHERE id=3"))
    sent = b.db(_send)
    assert (sent["state"], sent["capability_iri"]) == ("pending", None) and sent["offer_iri"].startswith(f"{b.origin}/ap/actors/")
    # The same document again, and the pending limit of the sender (shared knowledge base design Section 9.2 rule 5).
    assert b.db(_send)["reason_code"] == "contribution_duplicate"
    second = _personal_document(b.settings, b.manager, 3, "Daegu is a city of Korea. " * 3, url="https://example.org/daegu", extraction=EXTRACTION)
    assert b.db(lambda conn: offers.send(conn, account_id=3, source_kb_id=personal, source_document_id=second, remote_kb=record, submission_item_id=None, pending_limit=1, automatic=False, origin=b.origin, now=b.now))["reason_code"] == "pending_limit"
    # A blocked domain gets no offer.
    b.db(lambda conn: conn.execute("INSERT INTO federation_domain_blocks (domain, created_at, created_by) VALUES ('a.example', 1, 1)"))
    assert b.db(lambda conn: offers.send(conn, account_id=3, source_kb_id=personal, source_document_id=second, remote_kb=record, submission_item_id=None, pending_limit=5, automatic=False, origin=b.origin, now=b.now))["reason_code"] == "blocked_domain"
    assert b.db(lambda conn: [r[0] for r in conn.execute("SELECT kind FROM kf_outbox")]) == ["offer_contribution"]


# --- ``Delete`` of a remote actor (design Section 12.1 rule 4, CT7, CT19) ----------------------------------------


def test_delete_of_a_remote_actor_keeps_its_contributions_with_the_credit_deleted_account(b: KfWorld, no_llm) -> None:  # noqa: F811
    kb, kb_actor = _kb(b)
    assert _deliver(b, _offer(kb_actor, name="kept"), recipient=kb_actor)[0] == "processed"
    (kept,) = _contributions(b, kb)
    assert b.client.patch(f"/kb/{kb['number']}/contributions/{kept['number']}", json={"action": "approve"}, headers=b.headers("ann")).json()["contribution"]["state"] == "visible"
    assert _deliver(b, _offer(kb_actor, name="waits"), recipient=kb_actor)[0] == "processed"
    grant = b.db(lambda conn: grants.give(conn, kb_number=int(kb["number"]), iri=grants.grant_iri(kb_actor), actor_iri=ALICE, domain="a.example", account_id=2, now=b.now)[0])
    assert b.db(lambda conn: grants.valid(conn, grant["grant_iri"], kb_number=int(kb["number"]), actor_iri=ALICE, now=b.now + 1))
    # The sidecar hands the ``Delete`` of the actor to Claire through the inbox event route of the account federation.
    event = {"event_id": f"{ALICE}#delete", "type": "Delete", "object_type": None, "recipient_public_id": None, "actor": {"iri": ALICE, "inbox": f"{ALICE}/inbox", "shared_inbox": None}, "object_iri": ALICE, "verified_by": "http-signature"}
    answer = b.internal("POST", "/inbox-events", event)
    assert answer.status_code == 200 and answer.json()["decision"] == "accepted", answer.text
    # The approved contribution stays, without the credit; the pending one is gone with its hidden copy; the grant ended.
    (left,) = _contributions(b, kb)
    assert (left["number"], left["state"], left["remote"]["credit"], left["offer"]["actor"], left["offer"]["server"]) == (kept["number"], "visible", None, None, "a.example")
    assert b.db(lambda conn: conn.execute("SELECT COUNT(*) FROM documents WHERE kb_id=? AND hidden=1", (kb["id"],)).fetchone()[0]) == 0
    assert not b.db(lambda conn: grants.valid(conn, grant["grant_iri"], kb_number=int(kb["number"]), actor_iri=ALICE, now=b.now + 1))
    # The actor has no inbox now: each offer that had no answer in the outbox is closed, and no answer goes out.
    assert b.db(lambda conn: sorted(r[0] for r in conn.execute("SELECT state FROM kf_inbound_offers"))) == ["closed", "closed"]
    assert _outbox(b) == []


# --- Limits of the stores that other servers fill, and the HTML of an offer (KS2) ------------------------------


def test_the_html_of_an_offer_goes_through_the_sanitizer_for_remote_html(b: KfWorld) -> None:
    kb, kb_actor = _kb(b)
    offer = _offer(kb_actor, name="html")
    offer["object"][vocabulary.DOCUMENT]["content"] = '<p>Text</p><img src="https://tracker.example/pixel.png"><script>alert(1)</script><iframe src="https://c.example/"></iframe>'
    assert _deliver(b, offer, recipient=kb_actor) == ("processed", None)
    (pending,) = _contributions(b, kb)
    stored = b.db(lambda conn: conn.execute("SELECT detail_html FROM documents WHERE id=?", (pending["document"],)).fetchone()[0])
    assert "Text" in stored and "<img" not in stored and "<script" not in stored and "<iframe" not in stored and "tracker.example" not in stored


def test_received_grants_have_a_limit_for_each_domain_and_old_grants_go(b: KfWorld, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(grants, "RECEIVED_PER_DOMAIN_MAX", 2)
    ben = f"{b.origin}/ap/actors/{ids.format_public_id((1 << 57) + 3)}"
    now = b.now

    def _grant(number: int, name: str, *, end: float = 1000.0) -> dict:
        kb_actor = f"{A}/ap/kb/18460000000000001{number:02d}"
        return vocabulary.build_grant(activity_id=f"{kb_actor}#grants/{name}", knowledge_base=kb_actor, account_actor=ben, published=now - 10, end_time=now + end)

    assert _deliver(b, _grant(1, "a"), recipient=ben) == ("processed", None)
    assert _deliver(b, _grant(2, "b"), recipient=ben) == ("processed", None)
    # The third grant of the same domain for the same account is over the limit: nothing is stored.
    assert _deliver(b, _grant(3, "c"), recipient=ben) == ("refused", "grant_limit")
    assert b.db(lambda conn: len(grants.received_of_account(conn, 3))) == 2
    # A grant that is not in effect goes when a newer grant of the same knowledge base actor comes: the renewal takes its place.
    revoke = vocabulary.build_revoke(activity_id=f"{A}/ap/kb/1846000000000000101#grants/a/revoke", knowledge_base=f"{A}/ap/kb/1846000000000000101", account_actor=ben, grant=f"{A}/ap/kb/1846000000000000101#grants/a")
    assert _deliver(b, revoke, recipient=ben) == ("processed", None)
    assert _deliver(b, _grant(1, "a2"), recipient=ben) == ("processed", None)
    assert b.db(lambda conn: sorted(row["grant_iri"].rsplit("/", 1)[1] for row in grants.received_of_account(conn, 3))) == ["a2", "b"]
    # A grant that ended more than 30 days ago goes at the next grant.
    b.db(lambda conn: conn.execute("UPDATE kf_received_grants SET end_time=? WHERE grant_iri LIKE '%/b'", (now - grants.RECEIVED_RETENTION_SECONDS - 1,)))
    assert _deliver(b, _grant(3, "c2"), recipient=ben) == ("processed", None)
    assert b.db(lambda conn: sorted(row["grant_iri"].rsplit("/", 1)[1] for row in grants.received_of_account(conn, 3))) == ["a2", "c2"]


def test_an_inbound_row_without_a_contribution_goes_after_its_retention(b: KfWorld) -> None:
    kb, kb_actor = _kb(b)
    assert _deliver(b, _offer(kb_actor, name="one"), recipient=kb_actor) == ("processed", None)
    again = _offer(kb_actor, name="one")
    again["id"] = f"{ALICE}#offers/again"
    assert _deliver(b, again, recipient=kb_actor) == ("processed", None)
    assert b.db(lambda conn: [r[0] for r in conn.execute("SELECT state FROM kf_inbound_offers ORDER BY id")]) == ["pending", "rejected"]
    # The ``Reject`` at once is older than the retention time: the next row of an offer deletes it. The row of the pending contribution stays.
    b.db(lambda conn: conn.execute("UPDATE kf_inbound_offers SET answered_at=? WHERE state='rejected'", (b.now - offers.INBOUND_RETENTION_SECONDS - 1,)))
    assert _deliver(b, _offer(kb_actor, name="two"), recipient=kb_actor) == ("processed", None)
    assert b.db(lambda conn: [r[0] for r in conn.execute("SELECT state FROM kf_inbound_offers ORDER BY id")]) == ["pending", "pending"]
