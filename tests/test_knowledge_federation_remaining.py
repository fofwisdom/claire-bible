"""The remaining items of the federation of shared knowledge (KNOWLEDGE_FEDERATION.md Sections 11.6, 12.6, 14, and 15).

The tests examine the interfaces between modules and the database:

- the recent activity of the root page (design Section 8.3 rule 3): the three kinds, the privacy rules;
- the follow state on the public theme page (design Section 9.2 rule 2) for a reviewer and the admin only;
- the time of a record (``updated``): the sender makes each one newer, and the receiver refuses an ``Update`` that is not newer and keeps a newer record at a read;
- the reason ``limit_reached`` of a ``Reject`` for a limit of the receiver, and the sender that can offer the document again;
- the MCP tools ``kb_candidates`` and ``kb_submit`` with targets of other servers (shared knowledge base design Section 9.9 rule 7);
- the routes that send activities use only the configured public origin.

One Claire server is a real app with the mock provider; the activities of the other server are made with the builders of the vocabulary and handed to the internal route as a sidecar does after it verified a signature.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from claire import contributions, ids
from claire.knowledge_federation import grants, handlers, journal, offers, remote_kbs, vocabulary
from claire.store import accounts as accounts_store
from claire.store import db as dbm
from claire.store import federation as federation_store
from claire.store import themes as themes_store
from claire.store.secrets import reset_secret_service
from claire.store.token_store import reset_token_stores
from tests.knowledge_federation_servers import KfWorld, server_actor
from tests.test_auto_contribution import KOREA, _document, _mcp
from tests.test_contributions import _fresh_token_stores, no_llm, world  # noqa: F401 (fixtures)

A = "https://a.example"
C = "https://c.example"
ALICE = f"{A}/ap/actors/1846000000000000001"
HASHES = iter(f"{n:064x}" for n in range(20_000, 30_000))


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch):
    for key in list(os.environ):
        if key.startswith(("CLAIRE_", "CB_")):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("CLAIRE_SECRET_KEY", "77" * 32)
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


def _record(world, iri: str, *, label: str = "Remote", first_seen_at: float = 1.0, signature: list | None = None) -> remote_kbs.RemoteKnowledgeBase:
    """A record of a remote knowledge base as a read stores it."""
    domain = iri.split("//", 1)[1].split("/", 1)[0]
    url = iri.replace("/ap/kb/", "/kb/") + "/"
    payload = {"id": iri, "name": label, "url": url, vocabulary.DOCUMENTS: f"{iri}/documents"}
    sig = None if signature is None else json.dumps({"entities": signature, "visible_documents": 3, "computed_at": "2026-11-16T12:00:00Z"})

    def _insert(conn) -> remote_kbs.RemoteKnowledgeBase:
        conn.execute(
            "INSERT INTO kf_remote_knowledge_bases (iri, domain, url, label, description, knowledge_type, document_count, entity_count, relation_count, signature, payload, first_seen_at, seen_at, changed_at) VALUES (?, ?, ?, ?, '', 'real', 1, 1, 0, ?, ?, ?, ?, ?)",
            (iri, domain, url, label, sig, json.dumps(payload, sort_keys=True), first_seen_at, first_seen_at, first_seen_at),
        )
        found = remote_kbs.get_by_iri(conn, iri)
        assert found is not None
        return found

    if hasattr(world, "db"):
        return world.db(_insert)
    conn = dbm.connect(world.settings.db_file)
    try:
        found = _insert(conn)
        conn.commit()
        return found
    finally:
        conn.close()


def _outbox(world: KfWorld) -> list[dict]:
    tasks = world.internal("GET", "/knowledge-outbox-tasks?limit=50").json()["tasks"]
    world.db(lambda conn: conn.execute("UPDATE kf_outbox SET lease_until=NULL, status='delivered' WHERE status='pending'"))
    return tasks


def _deliver(world: KfWorld, activity: dict, *, recipient: str | None) -> tuple[str, str | None]:
    answer = world.deliver(activity, signer=activity["actor"], recipient=recipient).json()
    return answer["decision"], answer["reason"]


# --- Item 1: the recent activity of the root page (design Section 8.3 rule 3) ---------------------------------


def test_the_recent_activity_has_the_three_kinds_and_names_only_a_public_account(b: KfWorld) -> None:
    kb, _actor = _kb(b)
    cities = _record(b, f"{C}/ap/kb/1", label="Cities of C", first_seen_at=100.0)
    hidden_source = _record(b, f"{C}/ap/kb/2", label="Hidden source", first_seen_at=90.0)

    def _rows(conn) -> None:
        kb_id = int(conn.execute("SELECT id FROM knowledge_bases WHERE number=?", (ids.parse_public_id(kb["number"]),)).fetchone()[0])
        offer = "INSERT INTO kf_outbound_offers (offer_iri, account_id, actor_iri, kb_iri, domain, source_kb_id, source_document_id, state, result_iri, created_at, decided_at) VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, 1.0, ?)"
        # ben (a public profile) and ann: two approvals of C; one approval of a server that a block applies to; one offer that waits.
        conn.execute(offer, ("o1", 3, "x", cities.iri, "c.example", "doc_1", "accepted", f"{cities.iri}/documents/1", 300.0))
        conn.execute(offer, ("o2", 3, "x", cities.iri, "c.example", "doc_2", "accepted", f"{cities.iri}/documents/2", 250.0))
        conn.execute(offer, ("o3", 2, "x", cities.iri, "c.example", "doc_3", "accepted", f"{cities.iri}/documents/3", 200.0))
        conn.execute(offer, ("o4", 3, "x", "https://d.example/ap/kb/1", "d.example", "doc_4", "accepted", "https://d.example/ap/kb/1/documents/4", 500.0))
        conn.execute(offer, ("o5", 3, "x", cities.iri, "c.example", "doc_5", "pending", None, None))
        federation_store.add_domain_block(conn, "d.example", created_by=1, now=1.0)
        # ann has no public profile now: she is not named.
        conn.execute("UPDATE accounts SET profile_public=0 WHERE id=2")
        copy = "INSERT INTO kb_contributions (public_id, kb_id, document_id, origin, curator_account_id, state, approved_at, approved_by_account_id, contributed_at, updated_at, remote_source_kb_iri, remote_source_iri, remote_root_iri) VALUES (?, ?, ?, 'curation', 2, ?, ?, 2, ?, ?, ?, ?, ?)"
        conn.execute(copy, (1846000000000000901, kb_id, "copy_1", "visible", 400.0, 400.0, 400.0, cities.iri, f"{cities.iri}/documents/9", f"{cities.iri}/documents/9"))
        conn.execute(copy, (1846000000000000902, kb_id, "copy_2", "hidden", 450.0, 450.0, 450.0, hidden_source.iri, f"{hidden_source.iri}/documents/8", f"{hidden_source.iri}/documents/8"))

    b.db(_rows)
    response = b.client.get("/explore/federation")
    assert response.status_code == 200
    recent = response.json()["recent"]
    assert [(item["kind"], item["at"]) for item in recent] == [("curation", 400.0), ("accepted_contribution", 300.0), ("accepted_contribution", 200.0), ("remote_knowledge_base", 100.0), ("remote_knowledge_base", 90.0)]
    curation, ben, ann = recent[0], recent[1], recent[2]
    assert (curation["server"], curation["label"], curation["url"], curation["knowledge_base"]["number"], curation["knowledge_base"]["label"]) == ("c.example", "Cities of C", cities.url, kb["number"], "Cities")
    assert (ben["server"], ben["label"], ben["url"], ben["account"]) == ("c.example", "Cities of C", cities.url, {"username": "ben", "display_name": "Ben"})
    assert ann["account"] is None
    # No item has a document of a personal knowledge base or its title.
    text = json.dumps(recent)
    assert "doc_" not in text and "documents/" not in text and "title" not in text


# --- Item 2: the follow state on the public theme page ------------------------------------------------------


def test_the_theme_page_gives_the_follow_state_to_a_reviewer_and_the_admin_only(b: KfWorld) -> None:
    kb, _actor = _kb(b)
    followed = _record(b, f"{C}/ap/kb/1", label="Followed")
    other = _record(b, f"{C}/ap/kb/2", label="Not followed")

    def _theme(conn) -> str:
        kb_id = int(conn.execute("SELECT id FROM knowledge_bases WHERE number=?", (ids.parse_public_id(kb["number"]),)).fetchone()[0])
        theme = themes_store.create_theme(conn, label="Places", knowledge_type="real")
        themes_store.add_theme_member(conn, theme_id=theme["id"], kb_id=kb_id, source="admin", pinned=True)
        for record in (followed, other):
            themes_store.set_admin_remote_member(conn, theme_id=theme["id"], remote_kb_id=record.id, pinned=True, excluded=False)
        themes_store.update_theme(conn, theme["id"], state="active")
        conn.execute("INSERT INTO kf_follows (kb_iri, domain, follow_iri, state, created_at) VALUES (?, 'c.example', ?, 'pending', 1.0)", (followed.iri, f"{b.origin}/ap/actors/server#follows/1"))
        return ids.format_public_id(int(theme["number"]))

    number = b.db(_theme)

    def _view(headers: dict) -> tuple[bool, dict, str | None]:
        response = b.client.get(f"/themes/{number}/", headers={**headers, "Accept": "application/json"})
        assert response.status_code == 200, response.text
        body = response.json()
        return body["follows"], {k["label"]: k.get("follow", "absent") for k in body["knowledge_bases"] if k["remote"]}, response.headers.get("cache-control")

    # ann founded Cities (a reviewer of a local member), and the admin: the follow state of each remote member.
    for name in ("ann", "admin"):
        assert _view(b.headers(name)) == (True, {"Followed": "pending", "Not followed": None}, "private, no-store"), name
    # An anonymous reader and an account without a role get no follow state (the design gives it to no public page).
    for headers in ({}, b.headers("ben")):
        follows, states, _cache = _view(headers)
        assert (follows, states) == (False, {"Followed": "absent", "Not followed": "absent"})


# --- Item 4: the time of a record ---------------------------------------------------------------------------


def test_each_update_of_a_record_has_a_newer_time_and_the_collection_gives_it(b: KfWorld) -> None:
    kb, kb_actor = _kb(b)
    signer = server_actor(A)
    assert _deliver(b, vocabulary.build_follow(activity_id=f"{signer}#follows/1", server_actor=signer, knowledge_base=kb_actor), recipient=kb_actor) == ("processed", None)
    assert [task["kind"] for task in _outbox(b)] == ["accept_follow"]
    times = []
    for label in ("Towns", "Villages"):
        # Two changes in the same second: each ``Update`` has a time after the one before.
        b.db(lambda conn, label=label: conn.execute("UPDATE knowledge_bases SET label=? WHERE number=?", (label, ids.parse_public_id(kb["number"]))))
        (update,) = _outbox(b)
        assert (update["kind"], update["activity"]["object"]["name"]) == ("update_knowledge_base", label)
        assert vocabulary.validate_activity(update["activity"]) == "update_knowledge_base"
        times.append(vocabulary.time_seconds(update["activity"]["object"]["updated"]))
    assert times[1] > times[0]
    # The ``/kb`` collection and the actor document give the time of the record that the followers have.
    page = b.internal("GET", f"/knowledge-collections/kb?signer={signer}&page=true").json()["document"]["orderedItems"]
    assert [vocabulary.time_seconds(item["updated"]) for item in page] == [times[1]]
    document = b.internal("GET", f"/knowledge-kb-actors/{kb['number']}").json()["document"]
    assert vocabulary.time_seconds(document["updated"]) == times[1]


def test_the_receiver_refuses_an_update_that_is_not_newer_and_a_read_keeps_a_newer_record(b: KfWorld) -> None:
    iri = f"{C}/ap/kb/1846000000000000100"
    iris = {"id": iri, "url": f"{C}/kb/1846000000000000100/", "inbox": f"{iri}/inbox", "outbox": f"{iri}/outbox", "followers": f"{iri}/followers", "documents": f"{iri}/documents"}

    def _object(label: str, updated: float | None) -> dict:
        return vocabulary.build_knowledge_base({"label": label, "description": "", "knowledge_type": "real", "documents": 1, "entities": 1, "relations": 0, "signature": None, "accepts_automatic": True, "updated": updated}, iris)

    def _apply(label: str, updated: float) -> str | None:
        return b.db(lambda conn: remote_kbs.apply_update(conn, _object(label, updated), domain="c.example", origin=b.origin, now=b.now))

    assert _apply("First", 1000.0) is None
    assert _apply("Second", 2000.0) is None
    # A late ``Update`` with an older time, and the same one again, change nothing.
    assert _apply("Late", 1500.0) == "stale_update"
    assert _apply("Second again", 2000.0) == "stale_update"
    label = lambda: b.db(lambda conn: remote_kbs.get_by_iri(conn, iri).label)  # noqa: E731
    assert label() == "Second"

    # A read whose item is older than the record keeps the record (an ``Update`` came after the read started), and counts it as seen; a newer item changes it.
    def _read(item: dict) -> int | None:
        def _work(conn) -> int | None:
            known = conn.execute("SELECT 1 FROM kf_known_servers WHERE domain='c.example'").fetchone()
            if known is None:
                from claire.knowledge_federation import known_servers

                known_servers.note_server(conn, "c.example", source="admin", added_by=1, now=b.now)
                known_servers.record_read(conn, "c.example", server_actor_iri=f"{C}/ap/actors/server", software_name="claire-bible", software_version="1", shared_kb_count=None, now=b.now)
            conn.execute("DELETE FROM kf_remote_kb_reads WHERE domain='c.example'")
            assert remote_kbs.lease_reads(conn, now=b.now, limit=5) == ["c.example"]
            remote_kbs.record_batch(conn, "c.example", items=[item], origin=b.origin, now=b.now + 1)
            return remote_kbs.finish_read(conn, "c.example", now=b.now + 1)

        return b.db(_work)

    assert _read(_object("Old read", 1500.0)) == 0
    assert label() == "Second"
    assert _read(_object("New read", 3000.0)) == 0
    assert label() == "New read"


# --- Item 5: the reason ``limit_reached`` -----------------------------------------------------------------------


def _offer(kb_actor: str, *, name: str) -> dict:
    extraction = vocabulary.build_extraction({"summary": "A city.", "key_claims": [], "entities": [{"name": "Korea", "type": "Concept", "aliases": [], "observations": [], "proposed_type": None}], "relations": []}, {})
    iri = f"{ALICE}/documents/doc_{name}"
    document = vocabulary.build_document({"title": f"Title {name}", "content_html": f"<p>{name}</p>", "canonical_url": f"https://example.org/{name}", "content_hash": next(HASHES), "published": 1_700_000_000.0, "updated": None}, {"id": iri, "attributed_to": ALICE, "origin": None, "root": iri}, extraction)
    return vocabulary.build_offer(activity_id=f"{ALICE}#offers/{name}", contribution=vocabulary.build_contribution(document=document, attributed_to=ALICE, knowledge_base=kb_actor, contribution_id=None, automatic=False), capability=None)


def test_a_limit_of_the_receiver_rejects_with_limit_reached_and_the_sender_can_offer_again(b: KfWorld, no_llm) -> None:  # noqa: F811
    _kb_view, kb_actor = _kb(b)
    b.db(lambda conn: contributions.write_limit(conn, "remote_account_pending_limit", 1, updated_by=1))
    assert _deliver(b, _offer(kb_actor, name="one"), recipient=kb_actor) == ("processed", None)
    assert _deliver(b, _offer(kb_actor, name="two"), recipient=kb_actor) == ("processed", None)
    rejects = [task for task in _outbox(b) if task["kind"] == "reject_offer"]
    assert [(task["activity"]["object"], task["activity"][vocabulary.REASON_CODE]) for task in rejects] == [(f"{ALICE}#offers/two", "limit_reached")]
    assert b.db(lambda conn: tuple(conn.execute("SELECT reason_code, detail FROM kf_inbound_offers WHERE offer_iri=?", (f"{ALICE}#offers/two",)).fetchone())) == ("limit_reached", "pending_limit")

    # Here B is the sender: a ``Reject`` with ``limit_reached`` does not keep the document from that knowledge base; a reason of the reviewers does.
    remote = _record(b, f"{C}/ap/kb/5")
    sent = "INSERT INTO kf_outbound_offers (offer_iri, account_id, actor_iri, kb_iri, domain, source_kb_id, source_document_id, automatic, state, created_at) VALUES (?, 3, ?, ?, 'c.example', 1, ?, 1, 'pending', 1.0)"
    b.db(lambda conn: [conn.execute(sent, (f"{b.origin}/ap/actors/x#offers/{name}", f"{b.origin}/ap/actors/x", remote.iri, f"doc_{name}")) for name in ("limit", "spam")])
    for name, reason in (("limit", vocabulary.LIMIT_REACHED), ("spam", "spam")):
        reject = vocabulary.build_reject_offer(activity_id=f"{remote.iri}#rejects/{name}", knowledge_base=remote.iri, offer=f"{b.origin}/ap/actors/x#offers/{name}", offer_actor=f"{b.origin}/ap/actors/x", reason_code=reason)
        assert _deliver(b, reject, recipient=None) == ("processed", None)
    assert b.db(lambda conn: offers.automatic_offered(conn, account_id=3, kb_iri=remote.iri)) == {"doc_spam"}


# --- Item 7: the routes that send activities use the configured public origin only -------------------------


def test_the_routes_that_send_activities_refuse_without_a_configured_public_origin(b: KfWorld, monkeypatch: pytest.MonkeyPatch) -> None:
    kb, _actor = _kb(b)
    session = b.db(lambda conn: accounts_store.create_account_session(conn, account_id=2, scope="owner", now=time.time()))
    cookie = {"Cookie": f"claire_session={session}", "Origin": b.origin}
    # The request has its own address; the IRIs that leave the server do not come from it.
    monkeypatch.setattr(contributions, "canonical_origin", lambda settings: None)
    response = b.client.post(f"/kb/{kb['number']}/remote-collaborators", json={"actor": f"{C}/ap/actors/1846000000000000001"}, headers=cookie)
    assert (response.status_code, response.json().get("error")) == (409, "public_origin_missing")
    b.client.cookies.clear()
    response = b.client.post("/submissions", json={"documents": ["doc_x"], "remote_knowledge_base": 1}, headers=b.headers("ben"))
    assert (response.status_code, response.json().get("error")) == (409, "public_origin_missing")
    assert b.db(lambda conn: conn.execute("SELECT COUNT(*) FROM kf_grants").fetchone()[0]) == 0


# --- Item 3: the MCP tools with targets of other servers (shared knowledge base design Section 9.9 rule 7) ---


def test_mcp_candidates_and_submit_with_a_shared_knowledge_base_of_another_server(world, no_llm) -> None:  # noqa: F811
    conn = dbm.connect(world.settings.db_file)
    try:
        accounts_store.AccountStore(conn).set_federation_state("on", updated_by=1)
        conn.commit()
    finally:
        conn.close()
    match = _document(world, 3, "match", KOREA)
    remote = _record(world, "http://c.example/ap/kb/7", label="Korea of C", signature=[["Concept", "korea", 5], ["Concept", "seoul", 4], ["Concept", "busan", 3]])
    conn = dbm.connect(world.settings.db_file)
    try:
        theme = themes_store.create_theme(conn, label="Places", knowledge_type="real")
        themes_store.set_admin_remote_member(conn, theme_id=theme["id"], remote_kb_id=remote.id, pinned=True, excluded=False)
        themes_store.update_theme(conn, theme["id"], state="active")
        conn.commit()
    finally:
        conn.close()
    number = ids.format_public_id(theme["number"])
    readonly, write = world.bearer("ben", "readonly"), world.bearer("ben", "write")

    by_theme = _mcp(world, readonly, "kb_candidates", {"theme": number})
    assert by_theme["remote_state"] == "on"
    (target,) = by_theme["targets"]
    assert (target["remote"], target["remote_knowledge_base"], target["server"], target["url"], target["mode"], [d["document"] for d in target["documents"]]) == (True, remote.id, "c.example", remote.url, "pending", [match])
    assert _mcp(world, readonly, "kb_candidates", {"remote_knowledge_base": remote.id}) == by_theme
    assert _mcp(world, readonly, "kb_candidates", {"remote_knowledge_base": remote.id + 100}) == {"error": "knowledge_base_not_found"}
    assert _mcp(world, readonly, "kb_candidates", {"theme": number, "remote_knowledge_base": remote.id}) == {"error": "kb_or_theme_required"}

    # A direct choice needs a grant of that knowledge base, as ``POST /submissions``.
    assert _mcp(world, write, "kb_submit", {"documents": [match], "remote_knowledge_base": remote.id}) == {"error": "grant_required"}
    assert _mcp(world, write, "kb_submit", {"documents": [match], "remote_knowledge_base": remote.id, "theme": number}) == {"error": "remote_knowledge_base_invalid"}
    now = time.time()
    conn = dbm.connect(world.settings.db_file)
    try:
        grants.receive(conn, iri=f"{remote.iri}#grants/1", kb_iri=remote.iri, domain="c.example", account_id=3, published_at=now - 10, end_time=now + 1000, now=now)
        conn.commit()
    finally:
        conn.close()
    assert _mcp(world, readonly, "kb_candidates", {"remote_knowledge_base": remote.id})["targets"][0]["mode"] == "approved"
    made = _mcp(world, write, "kb_submit", {"documents": [match], "remote_knowledge_base": remote.id})
    (item,) = made["items"]
    assert "code" not in item
    assert [(t["remote"], t["server"], t["remote_knowledge_base"], t["state"]) for t in item["targets"]] == [(True, "c.example", remote.id, "pending")]
    # The document has a row for that knowledge base now: it is no candidate again.
    assert _mcp(world, readonly, "kb_candidates", {"remote_knowledge_base": remote.id})["targets"][0]["documents"] == []
