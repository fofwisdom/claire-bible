"""Regression tests of the security review of the federation of shared knowledge (KNOWLEDGE_FEDERATION_DESIGN.md Sections 6, 9 to 13, and 14: KS1 to KS5 and the limits).

Each test examines one finding of the review (F1 to F13; F1 is a test of the sidecar in ``sidecar/fedify/tests/knowledge_test.ts``) at the interfaces between the modules and the database: the inbox journal with its handlers, the stores of remote knowledge bases, remote documents, and the outbox, the copies of documents of another server with the merge job, the internal routes of the sidecar, and the admin routes. The other server is simulated: the activities are the contract fixtures or the builders of the vocabulary, handed to the internal route as a sidecar does after it verified a signature.
"""

from __future__ import annotations

import asyncio
import copy
import json
import os
import sqlite3
from pathlib import Path

import pytest

from claire.collaboration import contributions
from claire.api import knowledge_federation_internal
from claire.api.accounts_routes.knowledge_federation import ADMIN_KNOWN_SERVERS_API_PATH, AUDIT_KNOWN_SERVER_REMOVE
from claire.federation.knowledge import actors, collections, handlers, journal, known_servers, outbox, remote_documents, remote_kbs, vocabulary
from claire.store import accounts as accounts_store
from claire.store import actor_keys, auth_db
from claire.store import db as dbm
from claire.store import federation as federation_store
from claire.store import kb_collab
from claire.store.secrets import get_secret_service, reset_secret_service
from claire.store.token_store import reset_token_stores
from tests.knowledge_federation_servers import ALICE, B_KB_NUMBER, KfServer, KfWorld, fixture, server_actor
from tests.test_contributions import no_llm  # noqa: F401 (fixture)

A_SERVER = server_actor("https://a.example")
KB_IRI = f"https://b.example/ap/kb/{B_KB_NUMBER}"
DOCUMENT_IRI = "https://b.example/ap/documents/1846000000000000400"
T0 = 1_800_000_000.0


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
def conn(tmp_path: Path):
    """``claire.db`` of server B with the switch ``federation`` on."""
    connection = dbm.connect(tmp_path / "claire.db")
    dbm.init_db(connection)
    accounts_store.AccountStore(connection).set_federation_state("on", updated_by=1)
    connection.commit()
    yield connection
    connection.close()


def _receive(conn: sqlite3.Connection, activity: dict, *, signer: str, now: float = T0, origin: str = "https://b.example") -> journal.Receipt:
    return journal.receive(conn, activity=activity, signer=signer, verified_by="http-signature", recipient=None, origin=origin, now=now)


def _article(text: str, *, updated: str = "2026-10-17T12:00:00Z", url: str = "https://source.example/notes", document: str = DOCUMENT_IRI, entities: list[dict] | None = None) -> dict:
    """The document of the contract fixture ``update_document`` with another content, time, address, and entities."""
    article = copy.deepcopy(fixture("activities", "update_document")["object"])
    article.update({"id": document, "content": f"<p>{text}</p>", "updated": updated, "url": url})
    if entities is not None:
        extraction = article[vocabulary.EXTRACTION_OF]
        extraction[vocabulary.ENTITIES] = entities
        extraction[vocabulary.RELATIONS] = []
    return article


def _update(article: dict, number: int) -> dict:
    activity = copy.deepcopy(fixture("activities", "update_document"))
    activity["id"] = f"{article['id']}#updates/{number}"
    activity["object"] = article
    return activity


def _places(world: KfWorld) -> tuple[str, int]:
    places = world.client.post("/kb", json={"label": "Places", "knowledge_type": "real"}, headers=world.headers("ann")).json()["knowledge_base"]
    world.db(lambda conn: conn.execute("INSERT INTO kf_follows (kb_iri, domain, follow_iri, state, created_at) VALUES (?, 'b.example', ?, 'accepted', 1)", (KB_IRI, f"{A_SERVER}#follows/1")))
    return f"/kb/{places['number']}", int(places["id"])


def _copies(world: KfWorld, kb_id: int) -> list[dict]:
    return world.db(lambda conn: kb_collab.list_contributions(conn, kb_id))


# --- F2 (a): a server becomes known only when a handler accepts its activity -------------------------


def test_a_refused_follow_does_not_make_its_server_known(tmp_path: Path) -> None:
    b = KfServer(tmp_path, "https://b.example", username="bora")
    b.add_shared_kb(B_KB_NUMBER, "Analytical Engine")
    other = server_actor("https://x1.spam.example.com")
    unknown = vocabulary.build_follow(activity_id=f"{other}#follows/1", server_actor=other, knowledge_base="https://b.example/ap/kb/1846000000000000999")
    assert b.deliver(unknown, signer=other, recipient=None).json()["reason"] == "knowledge_base_not_found"
    assert b.db(lambda conn: known_servers.list_servers(conn)) == []
    # An accepted follow makes the server known.
    assert b.deliver(fixture("activities", "follow"), signer=A_SERVER, recipient=KB_IRI).json()["decision"] == "processed"
    assert b.db(lambda conn: [(s.domain, s.source) for s in known_servers.list_servers(conn)]) == [("a.example", "remote_curation")]


# --- F2 (b, c): the store of remote knowledge bases keeps only the contract -----------------------------


def _read_items(conn: sqlite3.Connection, items: list[dict], *, now: float = T0) -> remote_kbs.BatchResult:
    known_servers.note_server(conn, "b.example", source="admin", added_by=1, now=now)
    known_servers.record_read(conn, "b.example", server_actor_iri=server_actor("https://b.example"), software_name="claire-bible", software_version="1", shared_kb_count=None, now=now)
    assert remote_kbs.lease_reads(conn, now=now, limit=5) == ["b.example"]
    result = remote_kbs.record_batch(conn, "b.example", items=items, origin="https://a.example", now=now)
    assert result is not None
    return result


def test_a_record_keeps_only_the_contract_keys_and_the_signature_names_have_a_limit(conn) -> None:
    kb = {key: value for key, value in fixture("objects", "knowledge_base").items() if key != "@context"}
    extra = {**kb, "x:junk": "y" * 100_000, "publicKey": {"publicKeyPem": "z" * 1000}}
    long_name = copy.deepcopy(kb)
    long_name["id"] = kb["id"] + "0"
    long_name["url"] = kb["url"] + "0/"
    long_name[vocabulary.SIGNATURE]["entities"][0][1] = "n" * (remote_kbs.SIGNATURE_NAME_MAX + 1)
    result = _read_items(conn, [extra, long_name])
    assert (result.stored, result.refused, result.refused_code) == (1, 1, "signature_too_large")
    stored = remote_kbs.get_by_iri(conn, kb["id"])
    assert set(json.loads(stored.payload)) <= set(remote_kbs.RECORD_KEYS) and "x:junk" not in stored.payload
    # The list of all records does not read the payload (the public root page and the judgement read it).
    assert [record.payload for record in remote_kbs.list_all(conn)] == [None]
    assert remote_kbs.documents_url(stored) == kb[vocabulary.DOCUMENTS]


# --- F2 (d): the quota of the inbox journal for each registrable domain and day ---------------------------


def test_the_journal_has_a_quota_of_rows_and_bytes_for_each_registrable_domain_and_day(conn) -> None:
    contributions.write_limit(conn, "remote_domain_daily_activities", 2, updated_by=1)
    conn.commit()
    follow = fixture("activities", "follow")
    # Two subdomains of one registrable domain share the quota.
    for n, host in enumerate(("one.spam.example.com", "two.spam.example.com", "three.spam.example.com")):
        actor = server_actor(f"https://{host}")
        activity = {**follow, "id": f"{actor}#follows/{n}", "actor": actor}
        decision = _receive(conn, activity, signer=actor).decision
        assert decision == ("rejected" if n == 2 else "refused"), host
    assert _receive(conn, {**follow, "id": f"{server_actor('https://spam.example.com')}#follows/9", "actor": server_actor("https://spam.example.com")}, signer=server_actor("https://spam.example.com")).reason == "domain_quota"
    # Another domain has its own quota, and the quota is for 24 hours.
    assert _receive(conn, follow, signer=A_SERVER).decision == "refused"
    later = {**follow, "id": f"{server_actor('https://one.spam.example.com')}#follows/later", "actor": server_actor("https://one.spam.example.com")}
    assert _receive(conn, later, signer=server_actor("https://one.spam.example.com"), now=T0 + 86401).decision == "refused"
    # The bytes of 24 hours.
    contributions.write_limit(conn, "remote_domain_daily_activities", 100, updated_by=1)
    contributions.write_limit(conn, "remote_domain_daily_bytes", 1500, updated_by=1)
    conn.commit()
    big = {**follow, "id": f"{server_actor('https://c.example')}#follows/big", "actor": server_actor("https://c.example"), "x:padding": "p" * 2000}
    assert _receive(conn, big, signer=server_actor("https://c.example"), now=T0 + 90000).reason == "domain_quota"
    # A refused activity keeps no payload.
    payloads = {row[0] for row in conn.execute("SELECT payload FROM kf_inbox_journal WHERE status='refused'")}
    assert payloads == {"{}"}


# --- F2 (e): limits of the followers, and the outbox stores each activity one time --------------------------


def test_the_followers_have_limits_for_each_knowledge_base_and_each_domain(tmp_path: Path) -> None:
    b = KfServer(tmp_path, "https://b.example", username="bora")
    b.add_shared_kb(B_KB_NUMBER, "Analytical Engine")
    b.add_shared_kb(B_KB_NUMBER + 1, "Difference Engine")
    b.db(lambda conn: [contributions.write_limit(conn, key, value, updated_by=1) for key, value in (("kb_followers_limit", 2), ("remote_domain_followers_limit", 2))])

    def _follow(host: str, number: int = B_KB_NUMBER) -> str | None:
        actor = server_actor(f"https://{host}")
        target = f"https://b.example/ap/kb/{number}"
        return b.deliver(vocabulary.build_follow(activity_id=f"{actor}#follows/{number}", server_actor=actor, knowledge_base=target), signer=actor, recipient=target).json()["reason"]

    assert _follow("one.spam.example.com") is None
    assert _follow("two.spam.example.com") is None
    # The knowledge base has two followers.
    assert _follow("c.example") == "follower_limit"
    # The registrable domain example.com has two follows, also of another knowledge base.
    assert _follow("three.spam.example.com", B_KB_NUMBER + 1) == "domain_follower_limit"
    assert _follow("c.example", B_KB_NUMBER + 1) is None
    # A follower that is there can follow again.
    assert b.db(lambda conn: actors.follower_count(conn, B_KB_NUMBER)) == 2


def test_the_outbox_keeps_one_copy_of_an_activity_for_its_recipients_and_the_migration_moves_old_copies(tmp_path: Path) -> None:
    path = tmp_path / "claire.db"
    conn = dbm.connect(path)
    try:
        dbm.init_db(conn)
        activity = vocabulary.build_delete_knowledge_base(activity_id=f"{'https://b.example/ap/kb/1'}#delete", knowledge_base="https://b.example/ap/kb/1", followers="https://b.example/ap/kb/1/followers")
        recipients = [server_actor(f"https://s{n}.example") for n in range(3)]
        assert outbox.enqueue(conn, activity, signer="1", recipients=recipients, origin="https://b.example", now=T0) == 3
        assert conn.execute("SELECT COUNT(*) FROM kf_outbox_activities").fetchone()[0] == 1
        assert {task.payload for task in outbox.lease(conn, origin="https://b.example", now=T0, limit=10)} == {json.dumps(activity, ensure_ascii=False, separators=(",", ":"))}
        # A database of version 32: each task has its own copy of the activity.
        conn.execute("DROP TABLE kf_outbox_activities")
        conn.execute("ALTER TABLE kf_outbox ADD COLUMN payload TEXT NOT NULL DEFAULT ''")
        conn.execute("UPDATE kf_outbox SET payload=?", (json.dumps(activity),))
        conn.commit()
    finally:
        conn.close()
    for _ in range(2):
        conn = dbm.connect(path)
        try:
            dbm.init_db(conn)
            assert "payload" not in dbm._column_names(conn, "kf_outbox")
            assert [json.loads(row[0]) for row in conn.execute("SELECT payload FROM kf_outbox_activities")] == [activity]
            assert conn.execute("SELECT COUNT(*) FROM kf_outbox").fetchone()[0] == 3
            assert dbm.stored_schema_version(conn) == dbm.SCHEMA_VERSION >= 33
        finally:
            conn.close()


# --- F3, F6, F8, F13: revisions from the origin --------------------------------------------------------------


def test_a_revision_keeps_the_address_of_the_copy_uses_the_hash_of_this_server_and_cannot_take_the_place_of_another_document(tmp_path: Path, no_llm) -> None:  # noqa: F811
    a = KfWorld(tmp_path, "https://a.example")
    try:
        _base, kb_id = _places(a)
        a.db(lambda conn: contributions.write_limit(conn, "remote_revision_min_seconds", 0, updated_by=1))
        other_document = "https://b.example/ap/documents/1846000000000000401"
        # Two copies from B: the document of the fixture and another one.
        a.db(lambda conn: contributions.curate_remote(conn, kb_id=kb_id, source_kb_iri=KB_IRI, article=_article("First."), curator_account_id=2))
        a.db(lambda conn: contributions.curate_remote(conn, kb_id=kb_id, source_kb_iri=KB_IRI, article=_article("Other.", url="https://other.example/page", document=other_document), curator_account_id=2))
        first = next(row for row in _copies(a, kb_id) if row["remote_source_iri"] == DOCUMENT_IRI)
        # The revision has another address and a declared hash that is not of its content: the copy keeps its address, and its hash is the one of this server.
        revised = _article("Second.", updated="2026-10-18T12:00:00Z", url="https://victim.example/wanted")
        revised[vocabulary.CONTENT_HASH] = "0" * 64
        assert a.deliver(_update(revised, 2), signer=KB_IRI, recipient=None).json()["decision"] == "processed"
        (revision,) = a.db(lambda conn: kb_collab.list_revisions(conn, int(first["public_id"])))
        row = a.db(lambda conn: dict(conn.execute("SELECT canonical_url, url, content_hash FROM documents WHERE id=?", (revision["document_id"],)).fetchone()))
        assert row == {"canonical_url": "https://source.example/notes", "url": "https://source.example/notes", "content_hash": contributions.remote_content_hash(contributions.remote_html(revised), revised)}
        assert revision["content_hash"] == row["content_hash"] != "0" * 64
        # A revision with the content of another document of K: the duplicate check closes it (no new copy).
        other_article = _article("Other.", url="https://other.example/page", document=other_document)
        clash = _article("Other.", updated="2026-10-19T12:00:00Z")
        clash[vocabulary.EXTRACTION_OF] = other_article[vocabulary.EXTRACTION_OF]
        assert a.deliver(_update(clash, 3), signer=KB_IRI, recipient=None).json()["decision"] == "processed"
        assert len(a.db(lambda conn: kb_collab.list_revisions(conn, int(first["public_id"])))) == 1
    finally:
        a.close()


def test_an_old_update_that_comes_late_does_not_take_the_place_of_a_newer_one(tmp_path: Path, no_llm) -> None:  # noqa: F811
    a = KfWorld(tmp_path, "https://a.example")
    try:
        _base, kb_id = _places(a)
        a.db(lambda conn: contributions.write_limit(conn, "remote_revision_min_seconds", 0, updated_by=1))
        a.db(lambda conn: contributions.curate_remote(conn, kb_id=kb_id, source_kb_iri=KB_IRI, article=_article("First.", updated="2026-10-17T12:00:00Z"), curator_account_id=2))
        assert a.deliver(_update(_article("Third.", updated="2026-10-19T12:00:00Z"), 3), signer=KB_IRI, recipient=None).json()["decision"] == "processed"
        late = a.deliver(_update(_article("Second.", updated="2026-10-18T12:00:00Z"), 2), signer=KB_IRI, recipient=None).json()
        assert (late["decision"], late["reason"]) == ("refused", "stale_revision")
        (row,) = _copies(a, kb_id)
        assert row["remote_updated_at"] == vocabulary.time_seconds("2026-10-19T12:00:00Z")
        # The journal forgets a processed row after its retention: the same activity again is still refused, because the copy has a newer version.
        a.db(lambda conn: conn.execute("DELETE FROM kf_inbox_journal"))
        again = a.deliver(_update(_article("Second.", updated="2026-10-18T12:00:00Z"), 2), signer=KB_IRI, recipient=None).json()
        assert (again["decision"], again["reason"]) == ("refused", "stale_revision")
        # A time in the future is refused: the origin cannot stop each later revision.
        future = a.deliver(_update(_article("Future.", updated="2099-01-01T00:00:00Z"), 4), signer=KB_IRI, recipient=None).json()
        assert (future["decision"], future["reason"]) == ("refused", "invalid_time")
    finally:
        a.close()


def test_the_html_of_a_copy_loads_no_resource_of_the_other_server_and_a_credit_on_a_third_server_is_not_kept(tmp_path: Path) -> None:
    a = KfWorld(tmp_path, "https://a.example")
    try:
        _base, kb_id = _places(a)
        article = _article("Text.")
        article["content"] = '<p>Text.<img src="https://b.example/pixel.png"><a href="javascript:alert(1)">x</a><a href="ftp://b.example/f">y</a></p>'
        # The fixture document is a copy of a document of a.example: its credit and root are not on the server of the sender (B).
        assert article["attributedTo"].startswith("https://a.example/") and article[vocabulary.ROOT].startswith("https://a.example/")
        row = a.db(lambda conn: contributions.curate_remote(conn, kb_id=kb_id, source_kb_iri=KB_IRI, article=article, curator_account_id=2))
        html = a.db(lambda conn: conn.execute("SELECT detail_html FROM documents WHERE id=?", (row["document_id"],)).fetchone()[0])
        assert "<img" not in html and "javascript:" not in html and "ftp:" not in html and "Text." in html
        assert (row["remote_credit_iri"], row["remote_root_iri"]) == (None, DOCUMENT_IRI)
        # A credit and a root on the server of the sender are kept.
        own = _article("Own.", document="https://b.example/ap/documents/1846000000000000402", url="https://other.example/own")
        own["attributedTo"] = "https://b.example/ap/actors/1846000000000000009"
        own[vocabulary.ROOT] = own["id"]
        mine = a.db(lambda conn: contributions.curate_remote(conn, kb_id=kb_id, source_kb_iri=KB_IRI, article=own, curator_account_id=2))
        assert (mine["remote_credit_iri"], mine["remote_root_iri"]) == (own["attributedTo"], own["id"])
    finally:
        a.close()


# --- F4: authority identifiers ------------------------------------------------------------------------------


def _entity(name: str, *, kind: str = "person", aliases: list[str] | None = None, same_as: list[str] | None = None) -> dict:
    entity = {"name": name, "type": kind, "norm_name": name.casefold(), "aliases": aliases or [], "observations": ["One observation."]}
    if same_as is not None:
        entity["sameAs"] = same_as
    return entity


def test_an_authority_identifier_of_another_server_makes_no_merge_alone_and_does_not_go_onward(tmp_path: Path, no_llm) -> None:  # noqa: F811
    from claire.ingest.service import run_waiting_merge_jobs

    a = KfWorld(tmp_path, "https://a.example")
    try:
        _base, kb_id = _places(a)

        def _curate(n: int, entity: dict) -> None:
            article = _article(f"Text {n}.", document=f"https://b.example/ap/documents/18460000000000005{n:02d}", url=f"https://source.example/{n}", entities=[entity])
            a.db(lambda conn: contributions.curate_remote(conn, kb_id=kb_id, source_kb_iri=KB_IRI, article=article, curator_account_id=2))
            run_waiting_merge_jobs(a.settings)

        def _entities() -> dict[str, list[str]]:
            return a.db(lambda conn: {row["name"]: json.loads(row["props"] or "{}").get("same_as", []) for row in conn.execute("SELECT name, props FROM entities WHERE kb_id=?", (kb_id,))})

        # Only the known forms are kept; another form is not stored.
        _curate(1, _entity("Ada Lovelace", same_as=["wd:Q7259", "evil:anything", "wd:Q0"]))
        assert _entities() == {"Ada Lovelace": ["wd:Q7259"]}
        # The same identifier with another name, no alias that agrees: no merge.
        _curate(2, _entity("Charles Babbage", same_as=["wd:Q7259"]))
        assert set(_entities()) == {"Ada Lovelace", "Charles Babbage"}
        # The same identifier and the same name with another type: no match by the identifier.
        person = a.db(lambda conn: conn.execute("SELECT type FROM entities WHERE kb_id=? AND name='Ada Lovelace'", (kb_id,)).fetchone()[0])
        assert a.db(lambda conn: contributions._entity_with_authority_id(conn, kb_id, ["wd:Q7259"], etype=person, names=["Lady Ada", "Ada Lovelace"])) == "Ada Lovelace"
        assert a.db(lambda conn: contributions._entity_with_authority_id(conn, kb_id, ["wd:Q7259"], etype=person + "_other", names=["Ada Lovelace"])) is None
        # A merge by the name alone does not give the entity the identifiers of the other server.
        _curate(4, _entity("Ada Lovelace", same_as=["wd:Q42"]))
        assert _entities()["Ada Lovelace"] == ["wd:Q7259"]
        # The extraction that goes to another server has no identifier: each one came from another server.
        for row in _copies(a, kb_id):
            a.db(lambda conn, row=row: conn.execute("UPDATE kb_contributions SET state='visible' WHERE public_id=?", (row["public_id"],)))
        kb = a.db(lambda conn: conn.execute("SELECT id, number, label, description, knowledge_type FROM knowledge_bases WHERE id=?", (kb_id,)).fetchone())
        documents = [a.db(lambda conn, row=row: actors.document_object(conn, kb, kb_collab.get_contribution(conn, int(row["public_id"])), origin=a.origin)) for row in _copies(a, kb_id)]
        assert documents and all(doc is not None and all("sameAs" not in e for e in doc[vocabulary.EXTRACTION_OF][vocabulary.ENTITIES]) for doc in documents)
    finally:
        a.close()


def test_the_contract_limits_the_identifiers_of_one_entity() -> None:
    extraction = {"type": ["Object", vocabulary.EXTRACTION], "summary": "", vocabulary.KEY_CLAIMS: [], vocabulary.ENTITIES: [_entity("X", same_as=[f"wd:Q{n}" for n in range(1, vocabulary.MAX_SAME_AS + 2)])], vocabulary.RELATIONS: []}
    with pytest.raises(vocabulary.ContractError, match="invalid_field"):
        vocabulary.validate_object(extraction, top_level=False)
    extraction[vocabulary.ENTITIES] = [_entity("X", same_as=["wd:" + "1" * vocabulary.MAX_SAME_AS_LENGTH])]
    with pytest.raises(vocabulary.ContractError, match="invalid_field"):
        vocabulary.validate_object(extraction, top_level=False)


# --- F5: limits of the revisions and of each field; the merge jobs run after the answer -----------------------


def test_the_fields_of_a_document_of_another_server_have_limits() -> None:
    assert contributions.remote_article_refusal(_article("Text.")) is None
    long_url = _article("Text.", url="https://source.example/" + "u" * contributions.REMOTE_URL_MAX)
    assert contributions.remote_article_refusal(long_url) == "document_too_large"
    for change in (
        lambda e: e[vocabulary.ENTITIES][0]["observations"].append("o" * (contributions.REMOTE_OBSERVATION_MAX + 1)),
        lambda e: e[vocabulary.ENTITIES][0].update(name="n" * (contributions.REMOTE_NAME_MAX + 1)),
        lambda e: e[vocabulary.ENTITIES][0]["aliases"].extend(["a"] * (contributions.REMOTE_LIST_MAX + 1)),
        lambda e: e.update(summary="s" * (contributions.REMOTE_SUMMARY_MAX + 1)),
        lambda e: e[vocabulary.KEY_CLAIMS].extend(["c"] * (contributions.REMOTE_KEY_CLAIMS_MAX + 1)),
        lambda e: e[vocabulary.RELATIONS][0].update(type="t" * (contributions.REMOTE_TYPE_MAX + 1)),
    ):
        article = _article("Text.")
        change(article[vocabulary.EXTRACTION_OF])
        assert contributions.remote_article_refusal(article) == "extraction_too_large"


def test_revisions_have_a_minimum_time_for_each_copy_and_a_budget_for_each_domain(tmp_path: Path, no_llm) -> None:  # noqa: F811
    a = KfWorld(tmp_path, "https://a.example")
    try:
        _base, kb_id = _places(a)
        a.db(lambda conn: contributions.curate_remote(conn, kb_id=kb_id, source_kb_iri=KB_IRI, article=_article("First."), curator_account_id=2))
        assert a.deliver(_update(_article("Second.", updated="2026-10-18T00:00:00Z"), 2), signer=KB_IRI, recipient=None).json()["decision"] == "processed"
        soon = a.deliver(_update(_article("Third.", updated="2026-10-18T00:00:01Z"), 3), signer=KB_IRI, recipient=None).json()
        assert (soon["decision"], soon["reason"]) == ("refused", "revision_too_soon")
        # The budget of the registrable domain for 24 hours.
        a.db(lambda conn: [contributions.write_limit(conn, key, value, updated_by=1) for key, value in (("remote_revision_min_seconds", 0), ("remote_domain_daily_revisions", 1))])
        budget = a.deliver(_update(_article("Fourth.", updated="2026-10-18T00:00:02Z"), 4), signer=KB_IRI, recipient=None).json()
        assert (budget["decision"], budget["reason"]) == ("refused", "revision_budget")
    finally:
        a.close()


def test_the_merge_jobs_of_an_activity_run_after_the_answer_to_the_sidecar(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from starlette.requests import Request

    b = KfServer(tmp_path, "https://b.example", username="bora")
    b.add_shared_kb(B_KB_NUMBER, "Analytical Engine")
    ran: list[str] = []
    monkeypatch.setattr("claire.ingest.service.run_waiting_merge_jobs", lambda settings: ran.append("jobs"))
    route = next(r for r in knowledge_federation_internal.knowledge_internal_routes(b.settings, now=lambda: b.now, origin=lambda: b.origin) if r.path == "/knowledge-activities")
    body = json.dumps({"activity": fixture("activities", "follow"), "signer": A_SERVER, "verified_by": "http-signature", "recipient": KB_IRI}).encode()

    async def _receive() -> dict:
        return {"type": "http.request", "body": body, "more_body": False}

    request = Request({"type": "http", "method": "POST", "path": "/knowledge-activities", "headers": [], "query_string": b""}, _receive)
    response = asyncio.run(route.endpoint(request))
    # The answer is ready and no job ran in the request: the jobs are a background task of the answer.
    assert json.loads(response.body)["decision"] == "processed" and ran == []
    assert response.background is not None
    asyncio.run(response.background())
    assert ran == ["jobs"]


# --- F7: a removal at the origin before the follow ------------------------------------------------------------


def test_after_the_accept_of_a_follow_each_copy_is_read_again_and_a_404_of_the_origin_is_a_removal(tmp_path: Path) -> None:
    a = KfWorld(tmp_path, "https://a.example")
    try:
        _base, kb_id = _places(a)
        a.db(lambda conn: conn.execute("UPDATE kf_follows SET state='pending'"))
        follow_iri = f"{A_SERVER}#follows/1"
        a.db(lambda conn: contributions.curate_remote(conn, kb_id=kb_id, source_kb_iri=KB_IRI, article=_article("First."), curator_account_id=2))
        other = "https://b.example/ap/documents/1846000000000000401"
        a.db(lambda conn: contributions.curate_remote(conn, kb_id=kb_id, source_kb_iri=KB_IRI, article=_article("Other.", document=other, url="https://other.example/x"), curator_account_id=2))
        accept = vocabulary.build_accept_follow(activity_id=f"{KB_IRI}#accepts/1", follow={"id": follow_iri, "type": "Follow", "actor": A_SERVER, "object": KB_IRI})
        assert a.deliver(accept, signer=KB_IRI, recipient=None).json()["decision"] == "processed"
        reads = {read["url"]: read["id"] for read in a.internal("GET", "/knowledge-remote-reads").json()["reads"]}
        assert set(reads) == {DOCUMENT_IRI, other}
        # The origin answers 404 for the first one (the sidecar gives ``origin_removed``): the copy is hidden and waits in the review queue.
        assert a.internal("POST", "/knowledge-remote-reads", {"id": reads[DOCUMENT_IRI], "document": None, "error": "origin_removed"}).json()["status"] == "done"
        # The other one has a newer version: it is a revision.
        newer = vocabulary.with_context(_article("Other, revised.", document=other, url="https://other.example/x", updated="2026-10-20T00:00:00Z"))
        assert a.internal("POST", "/knowledge-remote-reads", {"id": reads[other], "document": newer, "error": None}).json()["status"] == "done"
        rows = {row["remote_source_iri"]: row for row in _copies(a, kb_id)}
        assert (rows[DOCUMENT_IRI]["origin_removed_reason"], contributions.in_origin_review(rows[DOCUMENT_IRI])) == ("other", True)
        assert rows[other]["origin_removed_at"] is None and len(a.db(lambda conn: kb_collab.list_revisions(conn, int(rows[other]["public_id"])))) == 1
    finally:
        a.close()


# --- F9: the signer and the actor ------------------------------------------------------------------------------


def test_the_actor_must_be_the_signer_or_an_actor_of_the_server_whose_server_actor_signed_and_the_port_counts(conn) -> None:
    offer = fixture("activities", "offer_contribution")
    other_account = "https://a.example/ap/actors/1846000000000000002"
    # Another account of the same server signed: refused.
    assert _receive(conn, offer, signer=other_account).reason == "signer_mismatch"
    # Another port of the same host is another server.
    assert _receive(conn, offer, signer="https://a.example:8443/ap/actors/1846000000000000001").reason == "signer_mismatch"
    moved = {**offer, "id": "https://a.example:8443/ap/actors/1846000000000000001#offers/1"}
    assert _receive(conn, moved, signer=ALICE).reason == "signer_mismatch"
    # The server actor of the same origin answers for its actors.
    assert _receive(conn, offer, signer=A_SERVER).decision != "rejected"


# --- F10: the list of the documents of a remote knowledge base -----------------------------------------------


def test_the_list_of_a_remote_knowledge_base_has_a_limit_also_after_failed_reads_and_its_items_have_a_form(conn, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(remote_documents, "MAX_LISTED_DOCUMENTS", 3)
    documents = f"{KB_IRI}/documents"

    def _item(n: int, **changes) -> dict:
        return {"id": f"{documents}/{n}", "type": "Article", "name": f"Doc {n}", vocabulary.CONTENT_HASH: f"{n:064x}", "published": "2026-11-16T00:00:00Z", **changes}

    def _read(items: list[dict], *, following: str | None, now: float) -> str:
        cur = conn.execute("INSERT INTO kf_remote_reads (kind, kb_iri, domain, url, status, started_at) VALUES ('documents', ?, 'b.example', ?, 'pending', ?)", (KB_IRI, documents, now))
        page = {"id": documents, "type": "OrderedCollectionPage", "orderedItems": items, **({"next": following} if following else {})}
        return remote_documents.record_result(conn, int(cur.lastrowid), document=page, error=None, origin="https://a.example", now=now)["status"]

    # Reads that fail (a next page on another server) store rows each time; the list stays at its limit.
    for n in range(4):
        assert _read([_item(10 * n + k) for k in range(3)], following="https://c.example/next", now=T0 + n) == "failed"
    assert conn.execute("SELECT COUNT(*) FROM kf_remote_documents WHERE kb_iri=?", (KB_IRI,)).fetchone()[0] == 3
    # Items without the form of the contract are not stored.
    conn.execute("DELETE FROM kf_remote_documents")
    bad = [_item(1, **{vocabulary.CONTENT_HASH: "zz"}), _item(2, published="yesterday"), _item(3, updated=12), _item(4)]
    assert _read(bad, following=None, now=T0 + 10) == "done"
    assert [row["iri"] for row in remote_documents.listing(conn, KB_IRI)] == [f"{documents}/4"]


# --- F11: counts ------------------------------------------------------------------------------------------------


def test_a_count_over_two_to_the_53_is_outside_the_contract_and_gives_no_server_error(tmp_path: Path) -> None:
    kb = {key: value for key, value in fixture("objects", "knowledge_base").items() if key != "@context"}
    huge = {**kb, vocabulary.ENTITY_COUNT: 2**63}
    with pytest.raises(vocabulary.ContractError, match="invalid_field"):
        vocabulary.validate_object(huge, top_level=False)
    b = KfServer(tmp_path, "https://a.example", username="alice")
    b.db(lambda conn: (known_servers.note_server(conn, "b.example", source="admin", added_by=1, now=b.now), known_servers.record_read(conn, "b.example", server_actor_iri=server_actor("https://b.example"), software_name="claire-bible", software_version="1", shared_kb_count=None, now=b.now)))
    assert b.internal("GET", "/knowledge-kb-reads").json()["reads"][0]["domain"] == "b.example"
    answer = b.internal("POST", "/knowledge-kb-reads", {"domain": "b.example", "items": [huge], "complete": False, "error": None})
    assert answer.status_code == 200 and (answer.json()["refused"], answer.json()["refused_code"]) == (1, "invalid_field")


# --- F12: the admin removes a known server; a block deletes the followers ---------------------------------


def test_the_admin_removes_a_known_server_and_what_this_server_keeps_of_it(tmp_path: Path) -> None:
    b = KfServer(tmp_path, "https://b.example", username="bora")
    b.add_shared_kb(B_KB_NUMBER, "Analytical Engine")
    admin = b.admin_session()
    assert b.deliver(fixture("activities", "follow"), signer=A_SERVER, recipient=KB_IRI).json()["decision"] == "processed"
    kb = {key: value for key, value in fixture("objects", "knowledge_base").items() if key != "@context"}
    kb.update({"id": "https://a.example/ap/kb/7", "url": "https://a.example/kb/7/", "inbox": "https://a.example/ap/kb/7/inbox", "outbox": "https://a.example/ap/kb/7/outbox", "followers": "https://a.example/ap/kb/7/followers", vocabulary.DOCUMENTS: "https://a.example/ap/kb/7/documents"})

    def _record(conn: sqlite3.Connection) -> None:
        known_servers.record_read(conn, "a.example", server_actor_iri=A_SERVER, software_name="claire-bible", software_version="1", shared_kb_count=None, now=b.now)
        assert remote_kbs.lease_reads(conn, now=b.now, limit=5) == ["a.example"]
        remote_kbs.record_batch(conn, "a.example", items=[kb], origin=b.origin, now=b.now)
        remote_kbs.finish_read(conn, "a.example", now=b.now)

    b.db(_record)
    body = {"domain": "a.example"}
    # The guards of an admin change.
    assert b.request("DELETE", ADMIN_KNOWN_SERVERS_API_PATH, session=admin, origin=None, json=body).status_code == 403
    assert b.request("DELETE", ADMIN_KNOWN_SERVERS_API_PATH, session=b.admin_session(authenticated_ago=3600), json=body).status_code == 401
    assert b.request("DELETE", ADMIN_KNOWN_SERVERS_API_PATH, session=admin, json={"domain": "c.example"}).json()["error"] == "known_server_not_found"
    assert b.request("DELETE", ADMIN_KNOWN_SERVERS_API_PATH, session=admin, json={"domain": "a.example", "x": 1}).status_code == 400
    answer = b.request("DELETE", ADMIN_KNOWN_SERVERS_API_PATH, session=admin, json=body)
    assert answer.status_code == 200
    assert answer.json() == {"removed": True, "domain": "a.example", "deleted": {"knowledge_bases": 1, "listed_documents": 0, "followers": 1, "outbox_tasks": 1, "journal_payloads": 1}}
    assert b.db(lambda conn: (known_servers.get_server(conn, "a.example"), remote_kbs.list_of_domain(conn, "a.example"), actors.followers(conn, B_KB_NUMBER))) == (None, [], [])
    assert b.db(lambda conn: [tuple(r) for r in conn.execute("SELECT status, error_code FROM kf_outbox")]) == [("refused", "server_removed")]
    auth = auth_db.connect_auth_db(auth_db.auth_db_path(b.settings.data_dir))
    try:
        assert [row[0] for row in auth.execute("SELECT action FROM admin_audit")][-1] == AUDIT_KNOWN_SERVER_REMOVE
    finally:
        auth.close()


def test_a_domain_block_deletes_the_followers_of_that_domain(tmp_path: Path) -> None:
    b = KfServer(tmp_path, "https://b.example", username="bora")
    b.add_shared_kb(B_KB_NUMBER, "Analytical Engine")
    assert b.deliver(fixture("activities", "follow"), signer=A_SERVER, recipient=KB_IRI).json()["decision"] == "processed"
    assert b.request("POST", "/api/admin/federation/domain-blocks", session=b.admin_session(), json={"domain": "a.example", "note": "test"}).status_code == 201
    assert b.db(lambda conn: actors.followers(conn, B_KB_NUMBER)) == []
    assert b.request("DELETE", "/api/admin/federation/domain-blocks", session=b.admin_session(), json={"domain": "a.example"}).status_code == 200
    # The follower count of the actor is zero; the server gets activities again only after a new ``Follow``.
    assert b.internal("GET", f"/knowledge-kb-actors/{B_KB_NUMBER}/followers?signer={A_SERVER}").json()["document"]["totalItems"] == 0


# --- Unit KF4: the limits for each domain are for each registrable domain -----------------------------------


def test_the_subdomains_of_one_registrable_domain_share_the_daily_limit_of_offers_and_an_iri_has_a_length_limit(tmp_path: Path) -> None:
    from tests.test_knowledge_federation_kf4 import _offer

    b = KfWorld(tmp_path, "https://b.example")
    try:
        kb = b.client.post("/kb", json={"label": "Cities", "knowledge_type": "real"}, headers=b.headers("ann")).json()["knowledge_base"]
        kb_actor = f"{b.origin}/ap/kb/{kb['number']}"
        b.db(lambda conn: contributions.write_limit(conn, "remote_domain_daily_offers", 2, updated_by=1))

        def _deliver(host: str, name: str, **changes: str) -> tuple[str, str | None]:
            offer = _offer(kb_actor, actor=f"https://{host}/ap/actors/1846000000000000001", name=name)
            offer.update(changes)
            answer = b.deliver(offer, signer=offer["actor"], recipient=kb_actor).json()
            return answer["decision"], answer["reason"]

        assert _deliver("one.spam.example.com", "a")[0] == "processed"
        assert _deliver("two.spam.example.com", "b")[0] == "processed"
        assert _deliver("three.spam.example.com", "c") == ("refused", "daily_limit")
        # A capability IRI over the length limit of the contract is refused before a row of an offer is made.
        assert _deliver("c.example", "d", capability=f"{kb_actor}#grants/" + "g" * vocabulary.MAX_IRI_LENGTH) == ("refused", "invalid_iri")
    finally:
        b.close()


# --- F13: the scheme of the collection, the claim of a journal row, the counts, the keys of a deleted actor ------


def test_the_documents_collection_of_a_record_has_the_scheme_of_its_actor() -> None:
    kb = {key: value for key, value in fixture("objects", "knowledge_base").items() if key != "@context"}
    kb[vocabulary.DOCUMENTS] = kb[vocabulary.DOCUMENTS].replace("https://", "http://")
    with pytest.raises(vocabulary.ContractError, match="origin_mismatch"):
        vocabulary.validate_object(kb, top_level=False)


def test_two_deliveries_of_one_activity_at_the_same_time_run_its_handler_one_time(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "claire.db"
    first = dbm.connect(path)
    dbm.init_db(first)
    accounts_store.AccountStore(first).set_federation_state("on", updated_by=1)
    first.commit()
    calls: list[str] = []

    def handler(ctx: journal.HandlerContext, entry: journal.JournalEntry, activity: dict) -> None:
        calls.append(entry.activity_iri)
        if len(calls) == 1:
            # A second delivery while this handler runs: it cannot take the row; it waits for the lock (here: no wait, so the lock error shows it).
            second = sqlite3.connect(str(path), timeout=0)
            second.row_factory = sqlite3.Row
            try:
                with pytest.raises(sqlite3.OperationalError, match="locked"):
                    journal.process(second, entry.id, origin="https://b.example", now=T0)
            finally:
                second.close()

    monkeypatch.setattr(journal, "HANDLERS", {"offer_contribution": handler})
    offer = fixture("activities", "offer_contribution")
    receipt = _receive(first, offer, signer=ALICE)
    assert receipt.decision == "processed" and calls == [offer["id"]]
    # After the first one ended, the row is processed: a second processing changes nothing.
    again = sqlite3.connect(str(path))
    again.row_factory = sqlite3.Row
    try:
        assert journal.process(again, receipt.entry.id, origin="https://b.example", now=T0).status == "processed"
    finally:
        again.close()
        first.close()
    assert calls == [offer["id"]]


def test_the_counts_of_a_record_have_no_graph_of_hidden_documents(tmp_path: Path) -> None:
    b = KfServer(tmp_path, "https://b.example", username="bora")
    b.add_shared_kb(B_KB_NUMBER, "Analytical Engine")
    b.fill_kb(B_KB_NUMBER, ["Ada", "Babbage"], documents=2)

    def _hide_and_count(conn: sqlite3.Connection) -> tuple[int, int, int]:
        kb_id = int(conn.execute("SELECT id FROM knowledge_bases WHERE number=?", (B_KB_NUMBER,)).fetchone()[0])
        conn.execute("INSERT INTO documents (id, kb_id, title, raw_text, hidden) VALUES ('doc_hidden', ?, 'Hidden', 'text', 1)", (kb_id,))
        conn.execute("INSERT INTO entities (id, kb_id, type, name, norm_name, aliases, sources, created_at, updated_at) VALUES ('ent_hidden', ?, 'Concept', 'Secret', 'secret', '[]', '[\"doc_hidden\"]', 1, 1)", (kb_id,))
        conn.execute("INSERT INTO relations (id, kb_id, type, source_id, target_id, sources, created_at) VALUES ('rel_hidden', ?, 'related_to', 'ent_hidden', ?, '[\"doc_hidden\"]', 1)", (kb_id, f"ent_{kb_id}_0"))
        obj = actors.knowledge_base_object(conn, B_KB_NUMBER, origin=b.origin)
        return obj[vocabulary.DOCUMENT_COUNT], obj[vocabulary.ENTITY_COUNT], obj[vocabulary.RELATION_COUNT]

    assert b.db(_hide_and_count) == (2, 2, 0)
    assert collections.visible_graph_counts is not None


def test_the_keys_of_a_deleted_knowledge_base_actor_are_deleted_when_no_task_waits(tmp_path: Path) -> None:
    b = KfServer(tmp_path, "https://b.example", username="bora")
    b.add_shared_kb(B_KB_NUMBER, "Analytical Engine")
    assert b.internal("GET", f"/knowledge-kb-actors/{B_KB_NUMBER}/keys").status_code == 200
    secret = get_secret_service(Path(b.settings.data_dir) / "auth" / "master.key")

    def _keys() -> int:
        auth = auth_db.connect_auth_db(auth_db.auth_db_path(b.settings.data_dir))
        try:
            return len(actor_keys.load_kb_actor_keys(auth, B_KB_NUMBER, secret))
        finally:
            auth.close()

    assert _keys() == 2
    assert b.deliver(fixture("activities", "follow"), signer=A_SERVER, recipient=KB_IRI).json()["decision"] == "processed"
    b.db(lambda conn: conn.execute("DELETE FROM knowledge_bases WHERE number=?", (B_KB_NUMBER,)))
    tasks = b.internal("GET", "/knowledge-outbox-tasks?limit=50").json()["tasks"]
    # The ``Accept`` and the ``Delete`` of the record wait: the keys stay.
    assert _keys() == 2
    for task in tasks:
        b.internal("POST", f"/knowledge-outbox-tasks/{task['id']}/ack", {"result": "delivered", "error_code": None})
    assert b.internal("GET", "/knowledge-outbox-tasks?limit=50").json()["tasks"] == []
    assert _keys() == 0
    assert federation_store.iri_host(KB_IRI) == "b.example"
