"""Interfaces of unit KF3 of the federation of shared knowledge (KNOWLEDGE_FEDERATION_DESIGN.md Sections 9.2, 9.3, 10, and 11).

The tests examine the interfaces between modules and the database: the migration step v31, the handlers of the inbox journal with the outbox (``knowledge_federation.handlers``, ``outbox``, ``actors``), the reads for a curation (``remote_documents``), and the copies of documents of another server with the merge job (``contributions``). The sidecar is simulated: the tests call the internal federation API with the values that a sidecar gives. The completion criteria of the unit are in ``tests/test_knowledge_federation_kf3_e2e.py``.

Server B is ``https://b.example`` with the shared knowledge base of the contract fixtures; server A is ``https://a.example``.
"""

from __future__ import annotations

import copy
import json
import os
import sqlite3
from pathlib import Path

import pytest

from claire import contributions
from claire.ingest.normalize import content_hash
from claire.ingest.service import run_waiting_merge_jobs
from claire.knowledge_federation import handlers, journal, known_servers, outbox, remote_documents, remote_kbs, vocabulary
from claire.store import accounts as accounts_store
from claire.store import db as dbm
from claire.store import federation as federation_store
from claire.store import kb_collab
from claire.store import themes as themes_store
from claire.store.secrets import reset_secret_service
from claire.store.token_store import reset_token_stores
from tests.knowledge_federation_servers import ALICE, B_KB_NUMBER, KfServer, KfWorld, fixture, server_actor
from tests.test_contributions import no_llm  # noqa: F401 (fixture)

A_SERVER = server_actor("https://a.example")
B_SERVER = server_actor("https://b.example")
KB_IRI = f"https://b.example/ap/kb/{B_KB_NUMBER}"
DOCUMENT_IRI = "https://b.example/ap/documents/1846000000000000400"


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


def _tasks(server) -> list[dict]:
    return server.internal("GET", "/knowledge-outbox-tasks?limit=50").json()["tasks"]


def _ack(server, task_id: int, result: str = "delivered", error: str | None = None) -> str:
    return server.internal("POST", f"/knowledge-outbox-tasks/{task_id}/ack", {"result": result, "error_code": error}).json()["status"]


# --- Migration step v31 ---------------------------------------------------------


def test_migration_adds_the_remote_columns_and_keeps_the_rows_and_a_table_of_another_form(tmp_path: Path) -> None:
    path = tmp_path / "claire.db"
    conn = dbm.connect(path)
    try:
        dbm.init_db(conn)
        conn.execute("INSERT INTO knowledge_bases (seq, label, is_personal, is_public, created_at, updated_at, number, knowledge_type) VALUES (7, 'K', 0, 1, 1, 1, 77, 'real')")
        kb_id = int(conn.execute("SELECT id FROM knowledge_bases WHERE number=77").fetchone()[0])
        # A database of version 30: the contributions have no column of a remote source, and a table with a name of this unit has another form.
        for column, _kind in kb_collab.REMOTE_COLUMNS:
            conn.execute("DROP INDEX IF EXISTS idx_kb_contributions_remote_source")
            conn.execute(f"ALTER TABLE kb_contributions DROP COLUMN {column}")
        conn.execute("INSERT INTO kb_contributions (public_id, kb_id, document_id, origin, state, contributed_at, updated_at) VALUES (5, ?, 'doc_old', 'direct', 'visible', 1, 1)", (kb_id,))
        conn.execute("DROP TABLE kf_outbox")
        conn.execute("CREATE TABLE kf_outbox (id INTEGER PRIMARY KEY, note TEXT)")
        conn.execute("INSERT INTO kf_outbox VALUES (1, 'kept')")
        conn.commit()
    finally:
        conn.close()
    for _ in range(2):
        conn = dbm.connect(path)
        try:
            dbm.init_db(conn)
            row = kb_collab.get_contribution(conn, 5)
            assert row is not None and (row["document_id"], row["remote_source_iri"], row["origin_removed_at"]) == ("doc_old", None, None)
            assert [tuple(r) for r in conn.execute("SELECT id, note FROM kf_outbox_retired_v31")] == [(1, "kept")]
            assert set(outbox.TABLES["kf_outbox"]) <= dbm._column_names(conn, "kf_outbox")
            assert dbm.stored_schema_version(conn) == dbm.SCHEMA_VERSION >= 31
        finally:
            conn.close()


# --- Knowledge base actor of this server: followers and outbound activities ------------------------


def test_follow_accept_undo_and_the_activities_of_a_knowledge_base_actor(tmp_path: Path) -> None:
    b = KfServer(tmp_path, "https://b.example", username="bora")
    b.add_shared_kb(B_KB_NUMBER, "Analytical Engine")
    follow = fixture("activities", "follow")
    # Only the server actor of another server follows, and only a shared knowledge base of this server (design Section 9.2 rule 5).
    by_account = vocabulary.build_follow(activity_id=f"{ALICE}#follows/1", server_actor=ALICE, knowledge_base=KB_IRI)
    assert b.deliver(by_account, signer=ALICE, recipient=KB_IRI).json()["reason"] == "not_a_server_actor"
    unknown = vocabulary.build_follow(activity_id=f"{A_SERVER}#follows/9", server_actor=A_SERVER, knowledge_base="https://b.example/ap/kb/1846000000000000999")
    assert b.deliver(unknown, signer=A_SERVER, recipient=None).json()["reason"] == "knowledge_base_not_found"
    assert _tasks(b) == []

    assert b.deliver(follow, signer=A_SERVER, recipient=KB_IRI).json()["decision"] == "processed"
    assert b.db(lambda conn: [server.source for server in known_servers.list_servers(conn)]) == ["remote_curation"]
    (accept,) = _tasks(b)
    assert (accept["kind"], accept["signer"], accept["recipient"]) == ("accept_follow", str(B_KB_NUMBER), A_SERVER)
    assert vocabulary.validate_activity(accept["activity"]) == "accept_follow" and accept["activity"]["object"]["id"] == follow["id"]
    # A task with a lease does not come again. ``retry`` gives it again after a wait; after the last attempt it ends as ``failed``.
    assert _tasks(b) == []
    assert _ack(b, accept["id"], "retry", "inbox_unreachable") == "pending"
    assert _tasks(b) == []
    b.now += 61
    assert [task["id"] for task in _tasks(b)] == [accept["id"]]
    for _ in range(outbox.MAX_ATTEMPTS - 2):
        assert _ack(b, accept["id"], "retry", "inbox_unreachable") == "pending"
    assert _ack(b, accept["id"], "retry", "inbox_unreachable") == "failed"
    assert _ack(b, accept["id"]) == "failed"  # a task that ended does not change
    b.now += 86400
    assert _tasks(b) == []

    # A change of the record goes to the followers as ``Update``; a removed contribution as ``Delete`` with its reason code. Each one goes one time.
    def _change(conn: sqlite3.Connection) -> None:
        kb_id = int(conn.execute("SELECT id FROM knowledge_bases WHERE number=?", (B_KB_NUMBER,)).fetchone()[0])
        conn.execute("UPDATE knowledge_bases SET label='Engines' WHERE id=?", (kb_id,))
        conn.execute("INSERT INTO kb_contributions (public_id, kb_id, document_id, origin, state, decision_reason, decided_at, contributed_at, updated_at) VALUES (500, ?, NULL, 'direct', 'removed', 'spam', ?, 1, 1)", (kb_id, b.now))

    b.db(_change)
    tasks = {task["kind"]: task for task in _tasks(b)}
    assert set(tasks) == {"update_knowledge_base", "delete_document"}
    assert (tasks["update_knowledge_base"]["activity"]["object"]["name"], tasks["update_knowledge_base"]["recipient"]) == ("Engines", A_SERVER)
    removed = tasks["delete_document"]["activity"]
    assert (removed["object"]["id"], removed["claire:reasonCode"], removed["actor"]) == (f"{KB_IRI}/documents/500", "spam", KB_IRI)
    for task in tasks.values():
        assert _ack(b, task["id"]) == "delivered"
    b.now += 10
    assert _tasks(b) == []

    # A blocked domain gets no delivery: its task ends as ``refused``.
    b.db(lambda conn: conn.execute("UPDATE knowledge_bases SET label='Machines' WHERE number=?", (B_KB_NUMBER,)))
    b.db(lambda conn: federation_store.add_domain_block(conn, "a.example", created_by=1, now=b.now))
    assert _tasks(b) == []
    b.db(lambda conn: federation_store.remove_domain_block(conn, "a.example"))

    # The knowledge base is deleted: the followers get the ``Delete`` of its record. Claire gives the keys of the actor while that task waits.
    b.db(lambda conn: conn.execute("DELETE FROM knowledge_bases WHERE number=?", (B_KB_NUMBER,)))
    (gone,) = _tasks(b)
    assert (gone["kind"], vocabulary.validate_activity(gone["activity"])) == ("delete_knowledge_base", "delete_knowledge_base")
    assert b.internal("GET", f"/knowledge-kb-actors/{B_KB_NUMBER}/keys").status_code == 200
    assert b.internal("GET", f"/knowledge-kb-actors/{B_KB_NUMBER}").status_code == 404
    assert _ack(b, gone["id"]) == "delivered"
    assert b.internal("GET", f"/knowledge-kb-actors/{B_KB_NUMBER}/keys").status_code == 404
    assert b.db(lambda conn: conn.execute("SELECT COUNT(*) FROM kf_kb_followers").fetchone()[0]) == 0


def test_undo_removes_the_follower_and_a_personal_knowledge_base_is_no_actor(tmp_path: Path) -> None:
    b = KfServer(tmp_path, "https://b.example", username="bora")
    b.add_shared_kb(B_KB_NUMBER, "Analytical Engine")
    assert b.deliver(fixture("activities", "follow"), signer=A_SERVER, recipient=KB_IRI).json()["decision"] == "processed"
    assert b.deliver(fixture("activities", "undo_follow"), signer=A_SERVER, recipient=KB_IRI).json()["decision"] == "processed"
    assert b.db(lambda conn: conn.execute("SELECT COUNT(*) FROM kf_kb_followers").fetchone()[0]) == 0
    # The personal knowledge base of the admin has a number, but it is never an actor (KD3).
    personal = b.db(lambda conn: conn.execute("SELECT number FROM knowledge_bases WHERE is_personal=1 AND number IS NOT NULL").fetchone())
    if personal is not None:
        assert b.internal("GET", f"/knowledge-kb-actors/{personal[0]}").status_code == 404
        assert b.internal("GET", f"/knowledge-kb-actors/{personal[0]}/keys").status_code == 404
    # While the switch is off, no actor document, no key, and no task.
    b.db(lambda conn: accounts_store.AccountStore(conn).set_federation_state("off", updated_by=1))
    assert b.internal("GET", f"/knowledge-kb-actors/{B_KB_NUMBER}").status_code == 404
    assert b.internal("GET", f"/knowledge-kb-actors/{B_KB_NUMBER}/keys").status_code == 404
    assert _tasks(b) == []


# --- This server follows a knowledge base actor of another server ----------------------------------


def _record_of_b(a) -> int:
    """A knows the shared knowledge base of B (the read of unit KF2). Return the ID of the record."""
    kb = {key: value for key, value in fixture("objects", "knowledge_base").items() if key != "@context"}

    def _store(conn: sqlite3.Connection) -> int:
        known_servers.note_server(conn, "b.example", source="admin", added_by=1, now=a.now)
        known_servers.record_read(conn, "b.example", server_actor_iri=B_SERVER, software_name="claire-bible", software_version="1.0.0", shared_kb_count=None, now=a.now)
        assert remote_kbs.lease_reads(conn, now=a.now, limit=5) == ["b.example"]
        remote_kbs.record_batch(conn, "b.example", items=[kb], origin=a.origin, now=a.now)
        remote_kbs.finish_read(conn, "b.example", now=a.now)
        return remote_kbs.get_by_iri(conn, KB_IRI).id

    return a.db(_store)


def test_this_server_follows_a_theme_member_and_takes_changes_only_from_an_actor_that_it_follows(tmp_path: Path) -> None:
    a = KfServer(tmp_path, "https://a.example", username="alice_admin")
    remote_id = _record_of_b(a)
    # An actor that this server does not follow changes nothing here.
    for name in ("update_knowledge_base", "update_document", "delete_document", "delete_knowledge_base"):
        activity = fixture("activities", name)
        activity["id"] += "/early"
        answer = a.deliver(activity, signer=KB_IRI, recipient=None).json()
        assert (answer["decision"], answer["reason"]) == ("refused", "not_following"), name
    assert a.deliver(fixture("activities", "accept_follow"), signer=KB_IRI, recipient=None).json()["reason"] == "follow_not_found"
    assert _tasks(a) == []

    # The knowledge base of B becomes a member of an active theme of A: the server actor of A follows its actor (design Section 9.2 rule 2).
    def _theme(conn: sqlite3.Connection) -> int:
        theme = themes_store.create_theme(conn, label="Engines", knowledge_type="real")
        themes_store.set_admin_remote_member(conn, theme_id=theme["id"], remote_kb_id=remote_id, pinned=True, excluded=False)
        conn.execute("UPDATE themes SET state='active' WHERE id=?", (theme["id"],))
        return int(theme["id"])

    theme_id = a.db(_theme)
    (task,) = _tasks(a)
    assert (task["kind"], task["signer"], task["recipient"], task["activity"]["actor"], task["activity"]["object"]) == ("follow", "server", KB_IRI, A_SERVER, KB_IRI)
    assert _ack(a, task["id"]) == "delivered" and _tasks(a) == []
    follow = task["activity"]
    assert a.db(lambda conn: outbox.follow_states(conn)) == {KB_IRI: "pending"}
    # The ``Accept`` must be for this ``Follow``.
    other = vocabulary.build_accept_follow(activity_id=f"{KB_IRI}#accepts/0", follow=fixture("activities", "follow"))
    assert a.deliver(other, signer=KB_IRI, recipient=None).json()["reason"] == "follow_not_found"
    accept = vocabulary.build_accept_follow(activity_id=f"{KB_IRI}#accepts/1", follow=follow)
    assert a.deliver(accept, signer=KB_IRI, recipient=None).json()["decision"] == "processed"
    assert a.db(lambda conn: outbox.follow_states(conn)) == {KB_IRI: "accepted"}

    # Now the record follows the ``Update`` of its actor.
    assert a.deliver(fixture("activities", "update_knowledge_base"), signer=KB_IRI, recipient=None).json()["decision"] == "processed"
    assert a.db(lambda conn: remote_kbs.get(conn, remote_id).label) == "Ada Lovelace and the Analytical Engine"
    # The knowledge base leaves the theme: the follow ends with an ``Undo`` of that ``Follow``.
    a.db(lambda conn: themes_store.set_admin_remote_member(conn, theme_id=theme_id, remote_kb_id=remote_id, pinned=False, excluded=False))
    (undo,) = _tasks(a)
    assert (undo["kind"], undo["activity"]["object"]["id"], undo["recipient"]) == ("undo_follow", follow["id"], KB_IRI)
    assert a.db(lambda conn: outbox.follow_states(conn)) == {}
    # A new membership is a new ``Follow`` with a new IRI; the ``Delete`` of the record then ends the follow without an ``Undo``.
    a.db(lambda conn: themes_store.set_admin_remote_member(conn, theme_id=theme_id, remote_kb_id=remote_id, pinned=True, excluded=False))
    (again,) = _tasks(a)
    assert again["kind"] == "follow" and again["activity"]["id"] != follow["id"]
    assert a.deliver(fixture("activities", "delete_knowledge_base"), signer=KB_IRI, recipient=None).json()["decision"] == "processed"
    assert a.db(lambda conn: (remote_kbs.get(conn, remote_id), outbox.follow_states(conn), themes_store.remote_members(conn))) == (None, {}, [])
    a.now += 10
    assert _tasks(a) == []


def test_the_read_of_a_documents_collection_keeps_only_the_documents_of_its_server(tmp_path: Path) -> None:
    a = KfServer(tmp_path, "https://a.example", username="alice_admin")
    remote_id = _record_of_b(a)
    source = a.db(lambda conn: remote_kbs.get(conn, remote_id))
    documents = f"{KB_IRI}/documents"
    item = {"id": f"{documents}/1", "type": "Article", "name": "One", "claire:contentHash": "ab" * 32, "published": "2026-11-16T00:00:00Z"}
    a.db(lambda conn: remote_documents.request_listing(conn, source, now=a.now))
    assert a.db(lambda conn: remote_documents.request_listing(conn, source, now=a.now))["id"] == 1  # one read waits: no second one
    (read,) = a.internal("GET", "/knowledge-remote-reads").json()["reads"]
    assert read == {"id": 1, "url": documents} and a.internal("GET", "/knowledge-remote-reads").json() == {"reads": []}
    # The root has ``first``: the same read gets the address of the page.
    root = {"id": documents, "type": "OrderedCollection", "totalItems": 3, "first": f"{documents}?page=true"}
    assert a.internal("POST", "/knowledge-remote-reads", {"id": 1, "document": root, "error": None}).json()["status"] == "pending"
    assert a.internal("GET", "/knowledge-remote-reads").json() == {"reads": [{"id": 1, "url": f"{documents}?page=true"}]}
    # An item of another server, and an item that is not the metadata of a document, are not stored.
    page = {"id": f"{documents}?page=true", "type": "OrderedCollectionPage", "orderedItems": [item, {**item, "id": "https://c.example/ap/kb/1/documents/2"}, {**item, "id": f"{documents}/3", "name": ""}, "text"]}
    assert a.internal("POST", "/knowledge-remote-reads", {"id": 1, "document": page, "error": None}).json() == {"id": 1, "status": "done", "error_code": None}
    assert a.db(lambda conn: [(doc["iri"], doc["title"]) for doc in remote_documents.listing(conn, KB_IRI)]) == [(f"{documents}/1", "One")]
    assert a.db(lambda conn: remote_documents.listing_state(conn, source))["state"] == "ready"
    # A page on another server ends the read, and the list stays.
    a.now += remote_documents.LISTING_MAX_AGE_SECONDS + 1
    assert a.db(lambda conn: remote_documents.request_listing(conn, source, now=a.now))["id"] == 2
    a.internal("GET", "/knowledge-remote-reads")
    other = {"id": documents, "type": "OrderedCollection", "first": "https://c.example/ap/kb/1/documents?page=true"}
    assert a.internal("POST", "/knowledge-remote-reads", {"id": 2, "document": other, "error": None}).json() == {"id": 2, "status": "failed", "error_code": "page_on_other_server"}
    assert a.db(lambda conn: (remote_documents.listing_state(conn, source)["state"], len(remote_documents.listing(conn, KB_IRI)))) == ("failed", 1)
    # A document that is not in the list of S is not a curation target; S must share a theme with K.
    with pytest.raises(remote_documents.SourceError, match="source_not_in_theme"):
        a.db(lambda conn: remote_documents.source_of(conn, kb_id=1, remote_kb_id=remote_id))
    with pytest.raises(remote_documents.SourceError, match="document_not_found"):
        a.db(lambda conn: remote_documents.request_document(conn, source, document_iri=f"{documents}/9", kb_id=1, account_id=1, now=a.now))


# --- Copies of documents of another server ---------------------------------------------------------


def _article(text: str, *, entity: str = "Ada Lovelace") -> dict:
    """The document of the contract fixture ``update_document`` with another content. The entity ``entity`` has the authority identifier ``wd:Q7259``."""
    article = copy.deepcopy(fixture("activities", "update_document")["object"])
    article["content"] = f"<p>{text}</p><script>alert(1)</script>"
    article[vocabulary.CONTENT_HASH] = content_hash(text)
    extraction = article[vocabulary.EXTRACTION_OF]
    for relation in extraction[vocabulary.RELATIONS]:
        for key in ("source", "target"):
            if relation[key] == "Ada Lovelace":
                relation[key] = entity
    extraction[vocabulary.ENTITIES][0].update({"name": entity, "norm_name": entity.casefold()})
    return article


def _update(article: dict, number: int) -> dict:
    activity = copy.deepcopy(fixture("activities", "update_document"))
    activity["id"] = f"{DOCUMENT_IRI}#updates/{number}"
    activity["object"] = article
    return activity


def test_a_copy_of_a_remote_document_follows_the_rules_of_local_revisions(tmp_path: Path, no_llm) -> None:  # noqa: F811
    a = KfWorld(tmp_path, "https://a.example")
    try:
        places = a.client.post("/kb", json={"label": "Places", "knowledge_type": "real"}, headers=a.headers("ann")).json()["knowledge_base"]
        base, kb_id = f"/kb/{places['number']}", int(places["id"])
        a.db(lambda conn: conn.execute("INSERT INTO kf_follows (kb_iri, domain, follow_iri, state, created_at) VALUES (?, 'b.example', ?, 'accepted', 1)", (KB_IRI, f"{A_SERVER}#follows/1")))

        def _state() -> dict:
            (row,) = a.client.get(f"{base}/contributions", headers=a.headers("ann")).json()["contributions"]
            return row

        def _entities() -> dict[str, tuple[list, dict]]:
            return a.db(lambda conn: {row["name"]: (json.loads(row["aliases"] or "[]"), json.loads(row["props"] or "{}")) for row in conn.execute("SELECT name, aliases, props FROM entities WHERE kb_id=?", (kb_id,))})

        def _deliver(article: dict, number: int) -> str:
            return a.deliver(_update(article, number), signer=KB_IRI, recipient=None).json()["decision"]

        # The copy of a curation: the HTML goes through the sanitizer, and the entity keeps its authority identifier (KD7).
        a.db(lambda conn: contributions.curate_remote(conn, kb_id=kb_id, source_kb_iri=KB_IRI, article=_article("The first version."), curator_account_id=2))
        run_waiting_merge_jobs(a.settings)
        first = _state()
        assert (first["state"], first["origin"], first["remote"]["document"]) == ("visible", "curation", DOCUMENT_IRI)
        assert a.db(lambda conn: conn.execute("SELECT detail_html FROM documents WHERE id=?", (first["document"],)).fetchone()[0]) == "<p>The first version.</p>"
        assert _entities()["Ada Lovelace"][1]["same_as"] == ["wd:Q7259"]

        # A revision whose entity has another name and the same authority identifier: the merge job matches the entity that K has (design Section 10.4 rule 2).
        assert _deliver(_article("The second version.", entity="Augusta Ada King"), 2) == "processed"
        second = _state()
        assert (second["state"], second["document"] != first["document"], [r["state"] for r in second["revisions"]]) == ("visible", True, ["done"])
        entities = _entities()
        assert "Augusta Ada King" not in entities and "Augusta Ada King" in entities["Ada Lovelace"][0]

        # Stopped updates: the copy keeps its version (CT20). After the restart the next revision applies.
        assert a.client.patch(f"{base}/contributions/{first['number']}", json={"action": "stop_updates"}, headers=a.headers("ann")).status_code == 200
        assert _deliver(_article("The third version."), 3) == "processed" and _state()["document"] == second["document"]
        assert a.client.patch(f"{base}/contributions/{first['number']}", json={"action": "restart_updates"}, headers=a.headers("ann")).status_code == 200
        # The same content makes no revision.
        assert _deliver(_article("The second version.", entity="Augusta Ada King"), 4) == "processed" and len(_state()["revisions"]) == 1

        # A hidden copy takes the revision and stays hidden.
        assert a.client.patch(f"{base}/contributions/{first['number']}", json={"action": "hide"}, headers=a.headers("ann")).status_code == 200
        assert _deliver(_article("The fifth version."), 5) == "processed"
        fifth = _state()
        assert (fifth["state"], len(fifth["revisions"])) == ("hidden", 2) and a.db(lambda conn: conn.execute("SELECT hidden FROM documents WHERE id=?", (fifth["document"],)).fetchone()[0]) == 1
        assert a.client.patch(f"{base}/contributions/{first['number']}", json={"action": "unhide"}, headers=a.headers("ann")).status_code == 200

        # The newest version wins: two revisions come before a job runs. The first one is closed, and its job removes its copy.
        def _two(conn: sqlite3.Connection) -> None:
            contributions.start_remote_revisions(conn, source_kb_iri=KB_IRI, article=_article("The sixth version."))
            contributions.start_remote_revisions(conn, source_kb_iri=KB_IRI, article=_article("The seventh version."))

        a.db(_two)
        run_waiting_merge_jobs(a.settings)
        seventh = _state()
        assert [r["state"] for r in seventh["revisions"]] == ["done", "done", "failed", "done"]
        assert a.db(lambda conn: [row[0] for row in conn.execute("SELECT detail_html FROM documents WHERE kb_id=?", (kb_id,))]) == ["<p>The seventh version.</p>"]
        # A revision from another actor than the one of the copy changes nothing.
        assert a.db(lambda conn: contributions.start_remote_revisions(conn, source_kb_iri="https://b.example/ap/kb/1", article=_article("Another."))) == []

        # The removal at the origin (KD1): hidden at once, in the review queue. Here the reviewers remove it: a tombstone keeps the document out.
        assert a.deliver(fixture("activities", "delete_document"), signer=KB_IRI, recipient=None).json()["decision"] == "processed"
        removed = _state()
        assert (removed["state"], removed["origin_removed"]) == ("hidden", {"at": removed["origin_removed"]["at"], "reason": "off_subject", "waiting": True})
        assert a.client.post(f"{base}/contributions/{first['number']}/remove", json={"reason": "off_subject"}, headers=a.headers("ann")).status_code == 200
        assert _state()["origin_removed"]["waiting"] is False
        with pytest.raises(contributions.ContributionError, match="contribution_tombstoned"):
            a.db(lambda conn: contributions.curate_remote(conn, kb_id=kb_id, source_kb_iri=KB_IRI, article=_article("The eighth version."), curator_account_id=2))
        assert no_llm == []
    finally:
        a.close()


def test_a_removal_at_the_origin_while_the_merge_job_waits_leaves_the_copy_hidden(tmp_path: Path) -> None:
    a = KfWorld(tmp_path, "https://a.example")
    try:
        places = a.client.post("/kb", json={"label": "Places", "knowledge_type": "real"}, headers=a.headers("ann")).json()["knowledge_base"]
        kb_id = int(places["id"])
        a.db(lambda conn: contributions.curate_remote(conn, kb_id=kb_id, source_kb_iri=KB_IRI, article=_article("The first version."), curator_account_id=2))
        assert a.db(lambda conn: contributions.origin_removed(conn, source_kb_iri=KB_IRI, source_iri=DOCUMENT_IRI, reason="spam")) == 1
        run_waiting_merge_jobs(a.settings)
        (row,) = a.db(lambda conn: kb_collab.list_contributions(conn, kb_id))
        assert (row["state"], contributions.in_origin_review(row), row["origin_removed_reason"]) == ("hidden", True, "spam")
        assert a.db(lambda conn: conn.execute("SELECT hidden FROM documents WHERE id=?", (row["document_id"],)).fetchone()[0]) == 1
        # A copy whose origin removed the document is no reason to follow the actor.
        assert a.db(lambda conn: kb_collab.remote_source_kbs_with_copies(conn)) == set()
    finally:
        a.close()
