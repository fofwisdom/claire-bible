"""Stores and rules of the federation of shared knowledge (KNOWLEDGE_FEDERATION_DESIGN.md Sections 6.2, 6.3, 13, and 14; unit KF1).

The tests use a real ``claire.db`` in ``tmp_path``: the known servers (``knowledge_federation.known_servers``), the access rule (``knowledge_federation.access``), the inbox journal (``knowledge_federation.journal``), and the migration step v29 (``store.db``). This server is ``https://b.example``; the activities are the contract fixtures of unit KF0 from ``https://a.example``.
"""

from __future__ import annotations

import copy
import json
import sqlite3
from pathlib import Path

import pytest

from claire.federation.knowledge import access, journal, known_servers
from claire.store import accounts as accounts_store
from claire.store import db as dbm
from claire.store import federation as federation_store
from tests.knowledge_federation_servers import ALICE, fixture, server_actor

ORIGIN = "https://b.example"
A_SERVER = server_actor("https://a.example")
KB_IRI = "https://b.example/ap/kb/1846000000000000100"
T0 = 1_800_000_000.0


@pytest.fixture
def conn(tmp_path: Path):
    """``claire.db`` of server B with the switch ``federation`` on."""
    connection = dbm.connect(tmp_path / "claire.db")
    dbm.init_db(connection)
    connection.commit()
    yield connection
    connection.close()


@pytest.fixture
def handlers(monkeypatch: pytest.MonkeyPatch) -> dict:
    """The dispatch table of the journal, empty for each test: unit KF1 registers no handler."""
    table: dict = {}
    monkeypatch.setattr(journal, "HANDLERS", table)
    return table


def _block(conn: sqlite3.Connection, domain: str) -> None:
    federation_store.add_domain_block(conn, domain, created_by=1, now=T0)
    conn.commit()


def _offer() -> dict:
    return fixture("activities", "offer_contribution")


def _receive(conn: sqlite3.Connection, activity: object, *, signer: str = ALICE, now: float = T0) -> journal.Receipt:
    return journal.receive(conn, activity=activity, signer=signer, verified_by="http-signature", recipient=KB_IRI, origin=ORIGIN, now=now)


# --- Migration step v29 ---------------------------------------------------------


def test_migration_makes_the_tables_and_keeps_a_table_of_another_form(tmp_path: Path) -> None:
    """The step runs at each start. A table with the name but without the columns of the code is kept under another name, with its rows; the start continues."""
    path = tmp_path / "claire.db"
    conn = dbm.connect(path)
    try:
        dbm.init_db(conn)
        assert set(known_servers.COLUMNS) <= dbm._column_names(conn, known_servers.TABLE)
        assert set(journal.COLUMNS) <= dbm._column_names(conn, journal.TABLE)
        conn.execute("DROP TABLE kf_known_servers")
        conn.execute("DROP TABLE kf_inbox_journal")
        conn.execute("CREATE TABLE kf_known_servers (domain TEXT PRIMARY KEY, note TEXT)")
        conn.execute("INSERT INTO kf_known_servers VALUES ('old.example', 'kept')")
        conn.execute("CREATE TABLE kf_inbox_journal (iri TEXT)")
        conn.execute("CREATE INDEX idx_kf_inbox_journal_status ON kf_inbox_journal(iri)")
        conn.commit()
    finally:
        conn.close()

    for _ in range(2):
        conn = dbm.connect(path)
        try:
            dbm.init_db(conn)
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'kf_%'")}
            # The tables of the remote knowledge bases are from the step v30 (unit KF2); the tables of the knowledge base actors, the outbox, and the reads for a curation are from the step v31 (unit KF3).
            v31 = {"kf_kb_followers", "kf_kb_actor_state", "kf_document_announcements", "kf_outbox", "kf_follows", "kf_remote_documents", "kf_remote_reads"}
            assert tables == {"kf_known_servers", "kf_inbox_journal", "kf_known_servers_retired_v29", "kf_inbox_journal_retired_v29", "kf_remote_knowledge_bases", "kf_remote_kb_reads"} | v31 | {"kf_outbound_offers", "kf_inbound_offers", "kf_grants", "kf_received_grants"} | {"kf_outbox_activities"}  # the offers and the grants are from the step v32 (unit KF4); the activities of the outbox from the step v33
            assert [tuple(row) for row in conn.execute("SELECT domain, note FROM kf_known_servers_retired_v29")] == [("old.example", "kept")]
            assert set(known_servers.COLUMNS) <= dbm._column_names(conn, "kf_known_servers")
            index = conn.execute("SELECT tbl_name FROM sqlite_master WHERE type='index' AND name='idx_kf_inbox_journal_status'").fetchone()
            assert index[0] == "kf_inbox_journal"
            assert dbm.stored_schema_version(conn) == dbm.SCHEMA_VERSION >= 29
        finally:
            conn.close()


# --- Known servers ----------------------------------------------------------------


def test_a_contact_makes_a_known_server_and_the_first_source_stays(conn) -> None:
    server, made = known_servers.note_server(conn, "A.Example.", source="remote_contribution", now=T0)
    assert made and server.domain == "a.example"
    assert (server.source, server.added_by, server.created_at, server.last_contact_at) == ("remote_contribution", None, T0, T0)

    again, made = known_servers.note_server(conn, "a.example", source="remote_curation", now=T0 + 10)
    assert not made
    assert (again.source, again.created_at, again.last_contact_at) == ("remote_contribution", T0, T0 + 10)

    # An admin entry of a known server changes nothing: how the server became known stays.
    admin, made = known_servers.note_server(conn, "a.example", source="admin", added_by=1, now=T0 + 20)
    assert not made and (admin.source, admin.added_by, admin.last_contact_at) == ("remote_contribution", None, T0 + 10)

    assert known_servers.touch_contact(conn, "a.example", now=T0 + 30)
    assert known_servers.get_server(conn, "a.example").last_contact_at == T0 + 30
    # A contact alone does not make a server known.
    assert not known_servers.touch_contact(conn, "unknown.example", now=T0)
    assert known_servers.get_server(conn, "unknown.example") is None


def test_an_admin_entry_has_no_contact_and_names_the_admin(conn) -> None:
    server, made = known_servers.note_server(conn, "c.example", source="admin", added_by=1, now=T0)
    assert made and (server.source, server.added_by, server.last_contact_at) == ("admin", 1, None)
    with pytest.raises(ValueError):
        known_servers.note_server(conn, "d.example", source="admin", now=T0)
    with pytest.raises(ValueError):
        known_servers.note_server(conn, "d.example", source="remote_contribution", added_by=1, now=T0)
    with pytest.raises(ValueError):
        known_servers.note_server(conn, "d.example", source="partnership", now=T0)
    with pytest.raises(ValueError):
        known_servers.note_server(conn, "https://d.example/", source="admin", added_by=1, now=T0)
    assert [s.domain for s in known_servers.list_servers(conn)] == ["c.example"]


def test_the_state_of_a_known_server_comes_from_the_domain_blocks(conn) -> None:
    server, _ = known_servers.note_server(conn, "kb.c.example", source="admin", added_by=1, now=T0)
    assert known_servers.server_view(conn, server)["status"] == "active"
    _block(conn, "c.example")
    view = known_servers.server_view(conn, server)
    assert (view["status"], view["blocked_by"]) == ("blocked", "c.example")
    assert set(view) == {"domain", "status", "blocked_by", "source", "created_at", "last_contact_at", "server_actor", "software_name", "software_version", "kb_count", "last_read_at", "error_count", "last_error", "last_error_at"}


def test_reads_are_leased_recorded_and_due_again_after_one_day(conn) -> None:
    for domain in ("a.example", "c.example", "d.example"):
        known_servers.note_server(conn, domain, source="admin", added_by=1, now=T0)
    _block(conn, "c.example")

    due = known_servers.lease_reads(conn, now=T0, limit=5)
    assert [s.domain for s in due] == ["a.example", "d.example"]  # a blocked server is never read
    # The lease stops a second read of the same server.
    assert known_servers.lease_reads(conn, now=T0 + 1, limit=5) == []
    # A lease that the sidecar did not report ends.
    assert [s.domain for s in known_servers.lease_reads(conn, now=T0 + known_servers.READ_LEASE_SECONDS + 1, limit=1)] == ["a.example"]

    assert known_servers.record_read(conn, "a.example", server_actor_iri=A_SERVER, software_name="claire-bible", software_version="1.0", shared_kb_count=3, now=T0 + 400)
    assert known_servers.record_read_error(conn, "d.example", error="nodeinfo_unreachable", now=T0 + 400)
    assert known_servers.record_read_error(conn, "d.example", error="x" * 300, now=T0 + 401)
    assert not known_servers.record_read(conn, "unknown.example", server_actor_iri=A_SERVER, software_name="s", software_version="1", shared_kb_count=None, now=T0)
    a = known_servers.get_server(conn, "a.example")
    d = known_servers.get_server(conn, "d.example")
    assert (a.server_actor_iri, a.software_name, a.shared_kb_count, a.last_read_at, a.read_lease_until, a.error_count) == (A_SERVER, "claire-bible", 3, T0 + 400, None, 0)
    assert (d.error_count, len(d.last_error), d.last_error_at, d.read_lease_until) == (2, 200, T0 + 401, None)

    assert known_servers.lease_reads(conn, now=T0 + 500, limit=5) == []
    later = T0 + 402 + known_servers.READ_INTERVAL_SECONDS
    assert [s.domain for s in known_servers.lease_reads(conn, now=later, limit=5)] == ["d.example", "a.example"]
    # A good read removes the error state.
    assert known_servers.record_read(conn, "d.example", server_actor_iri=server_actor("https://d.example"), software_name="claire-bible", software_version="1.0", shared_kb_count=None, now=later)
    assert known_servers.get_server(conn, "d.example").error_count == 0


# --- Access rule --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("signer", "reason"),
    [
        (None, "unsigned"),
        ("http://a.example/ap/actors/server", "invalid_actor"),  # http is for an http origin only
        ("https://user:pw@a.example/ap/actors/server", "invalid_actor"),
        ("https://b.example/ap/actors/server", "invalid_actor"),  # this server
        ("not an iri", "invalid_actor"),
        (ALICE, "not_a_server_actor"),
        ("https://a.example/ap/actors/server?x=1", "not_a_server_actor"),
        ("https://kb.c.example/ap/actors/server", "blocked_domain"),
        (A_SERVER, None),
    ],
)
def test_a_collection_answers_only_the_server_actor_of_a_server_that_is_not_blocked(conn, signer, reason) -> None:
    _block(conn, "c.example")
    decision = access.collection_access(conn, signer=signer, origin=ORIGIN)
    assert (decision.allowed, decision.reason) == (reason is None, reason)
    if reason in (None, "not_a_server_actor", "blocked_domain"):
        assert decision.domain is not None


def test_an_http_address_of_another_server_is_permitted_in_development(conn) -> None:
    # In development the origin is http, and an http address of another server is permitted.
    assert access.remote_server_domain("http://a.example/ap/actors/server", "http://127.0.0.1:8765") == "a.example"


# --- Inbox journal --------------------------------------------------------------------


def test_an_activity_without_a_handler_is_recorded_as_failed_and_its_server_does_not_become_known(conn, handlers) -> None:
    receipt = _receive(conn, _offer())
    assert (receipt.decision, receipt.reason) == ("failed", "no_handler")
    entry = receipt.entry
    assert (entry.status, entry.kind, entry.error_code, entry.attempts, entry.actor, entry.actor_domain, entry.recipient, entry.verified_by) == ("failed", "offer_contribution", "no_handler", 1, ALICE, "a.example", KB_IRI, "http-signature")
    # The activity is stored as it came.
    assert json.loads(entry.payload) == _offer()
    # Only a handler that accepts the activity makes its server known (design Section 6.2 rule 2).
    assert known_servers.get_server(conn, "a.example") is None
    view = journal.entry_view(entry, with_payload=True)
    assert view["replayable"] is True and view["received_at"] == "2027-01-15T08:00:00Z" and view["payload"] == _offer()
    assert "payload" not in journal.entry_view(entry)


def test_a_second_delivery_is_a_duplicate_and_changes_nothing(conn, handlers) -> None:
    first = _receive(conn, _offer())
    second = _receive(conn, _offer(), now=T0 + 60)
    assert (second.decision, second.reason, second.entry.id) == ("duplicate", "failed", first.entry.id)
    assert journal.get_entry(conn, first.entry.id).attempts == 1
    assert conn.execute("SELECT COUNT(*) FROM kf_inbox_journal").fetchone()[0] == 1


def test_a_row_whose_processing_did_not_end_is_processed_at_the_next_delivery(conn, handlers) -> None:
    applied: list[str] = []
    first = _receive(conn, _offer())
    conn.execute("UPDATE kf_inbox_journal SET status='received', error_code=NULL WHERE id=?", (first.entry.id,))
    conn.commit()
    handlers["offer_contribution"] = lambda ctx, entry, activity: applied.append(activity["id"])
    again = _receive(conn, _offer(), now=T0 + 60)
    assert (again.decision, again.entry.status, again.entry.processed_at) == ("processed", "processed", T0 + 60)
    assert applied == [_offer()["id"]]


def test_a_contract_error_is_recorded_with_its_code(conn, handlers) -> None:
    invalid = fixture("invalid", "follow_without_recipient")
    receipt = _receive(conn, invalid["value"], signer=A_SERVER)
    assert (receipt.decision, receipt.reason) == ("refused", invalid["error"])
    assert (receipt.entry.status, receipt.entry.error_code, receipt.entry.error_detail, receipt.entry.kind) == ("refused", "missing_recipient", "to", None)
    assert journal.entry_view(receipt.entry)["replayable"] is False
    with pytest.raises(journal.JournalError) as exc:
        journal.replay(conn, receipt.entry.id, origin=ORIGIN, now=T0)
    assert exc.value.code == "not_replayable"


@pytest.mark.parametrize(
    ("change", "signer", "reason"),
    [
        (lambda a: a, "https://c.example/ap/actors/server", "blocked_domain"),
        (lambda a: a, "https://evil.example/ap/actors/1", "signer_mismatch"),  # the activity is on a.example
        (lambda a: {**a, "actor": "https://evil.example/ap/actors/1"}, ALICE, "signer_mismatch"),
        (lambda a: {k: v for k, v in a.items() if k != "id"}, ALICE, "invalid_activity"),
        (lambda a: [a], ALICE, "invalid_activity"),
        (lambda a: {**a, "id": "https://b.example/x"}, ALICE, "invalid_activity"),  # an IRI of this server
        (lambda a: a, "https://b.example/ap/actors/server", "invalid_actor"),
    ],
)
def test_a_delivery_that_the_controls_stop_gets_no_row(conn, handlers, change, signer, reason) -> None:
    _block(conn, "c.example")
    receipt = _receive(conn, change(_offer()), signer=signer)
    assert (receipt.decision, receipt.reason, receipt.entry) == ("rejected", reason, None)
    assert conn.execute("SELECT COUNT(*) FROM kf_inbox_journal").fetchone()[0] == 0
    assert known_servers.list_servers(conn) == []


def test_an_unknown_signature_mechanism_is_an_error(conn, handlers) -> None:
    with pytest.raises(ValueError):
        journal.receive(conn, activity=_offer(), signer=ALICE, verified_by="none", recipient=None, origin=ORIGIN, now=T0)


def test_a_handler_error_keeps_the_row_without_the_changes_and_a_replay_processes_it(conn, handlers) -> None:
    def broken(ctx: journal.HandlerContext, entry: journal.JournalEntry, activity: dict) -> None:
        ctx.conn.execute("INSERT INTO server_settings (key, value, updated_at) VALUES ('kf_test', 'half', 0)")
        raise RuntimeError("disk full")

    handlers["offer_contribution"] = broken
    failed = _receive(conn, _offer()).entry
    assert (failed.status, failed.error_code, failed.error_detail) == ("failed", "handler_error", "RuntimeError: disk full")
    assert conn.execute("SELECT COUNT(*) FROM server_settings WHERE key='kf_test'").fetchone()[0] == 0
    # A failed activity does not make its server known: only a handler that accepts it does (design Section 6.2 rule 2).
    assert known_servers.get_server(conn, "a.example") is None

    def good(ctx: journal.HandlerContext, entry: journal.JournalEntry, activity: dict) -> None:
        assert (ctx.origin, ctx.now, entry.id, activity) == (ORIGIN, T0 + 100, failed.id, _offer())
        ctx.conn.execute("INSERT INTO server_settings (key, value, updated_at) VALUES ('kf_test', 'done', 0)")

    handlers["offer_contribution"] = good
    done = journal.replay(conn, failed.id, origin=ORIGIN, now=T0 + 100)
    assert (done.status, done.error_code, done.error_detail, done.attempts, done.processed_at) == ("processed", None, None, 2, T0 + 100)
    assert conn.execute("SELECT value FROM server_settings WHERE key='kf_test'").fetchone()[0] == "done"
    with pytest.raises(journal.JournalError) as exc:
        journal.replay(conn, failed.id, origin=ORIGIN, now=T0 + 200)
    assert exc.value.code == "not_replayable"
    # A processed row does not change.
    assert journal.process(conn, failed.id, origin=ORIGIN, now=T0 + 300) == journal.get_entry(conn, failed.id)
    with pytest.raises(journal.JournalError) as exc:
        journal.replay(conn, 999, origin=ORIGIN, now=T0)
    assert exc.value.code == "not_found"


def test_a_handler_refusal_is_recorded_with_its_code(conn, handlers) -> None:
    def refuse(ctx: journal.HandlerContext, entry: journal.JournalEntry, activity: dict) -> None:
        ctx.conn.execute("INSERT INTO server_settings (key, value, updated_at) VALUES ('kf_test', 'x', 0)")
        raise journal.HandlerRefusal("kb_not_found", "no such knowledge base")

    handlers["offer_contribution"] = refuse
    entry = _receive(conn, _offer()).entry
    assert (entry.status, entry.error_code, entry.error_detail) == ("refused", "kb_not_found", "no such knowledge base")
    assert conn.execute("SELECT COUNT(*) FROM server_settings WHERE key='kf_test'").fetchone()[0] == 0


def test_a_block_refuses_the_row_of_a_replay(conn, handlers) -> None:
    failed = _receive(conn, _offer()).entry

    _block(conn, "a.example")
    refused = journal.replay(conn, failed.id, origin=ORIGIN, now=T0 + 20)
    assert (refused.status, refused.error_code, refused.attempts) == ("refused", "blocked_domain", 2)


def test_register_handler_accepts_one_handler_for_each_known_kind(handlers) -> None:
    def handler(ctx: journal.HandlerContext, entry: journal.JournalEntry, activity: dict) -> None:
        return None

    journal.register_handler("follow", handler)
    assert handlers == {"follow": handler}
    with pytest.raises(ValueError):
        journal.register_handler("follow", handler)
    with pytest.raises(ValueError):
        journal.register_handler("like", handler)


def test_list_entries_pages_and_prune_keeps_failed_rows_longer(conn, handlers) -> None:
    ids = []
    for n in range(3):
        activity = copy.deepcopy(_offer())
        activity["id"] = f"{ALICE}#offers/{n + 10}"
        ids.append(_receive(conn, activity, now=T0 + n).entry.id)
    follow = _receive(conn, fixture("activities", "follow"), signer=A_SERVER, now=T0 + 3).entry
    assert follow.kind == "follow" and follow.status == "failed"
    assert [e.id for e in journal.list_entries(conn, limit=2)] == [follow.id, ids[2]]
    assert [e.id for e in journal.list_entries(conn, limit=10, max_id=ids[2])] == ids[1::-1]
    assert [e.id for e in journal.list_entries(conn, status="processed", limit=10)] == []
    with pytest.raises(ValueError):
        journal.list_entries(conn, status="lost", limit=10)

    conn.execute("UPDATE kf_inbox_journal SET status='processed' WHERE id=?", (ids[0],))
    conn.commit()
    now = T0 + journal.PROCESSED_RETENTION_SECONDS + 10
    assert journal.prune(conn, now=now) == 1
    assert journal.get_entry(conn, ids[1]) is not None
    assert journal.prune(conn, now=T0 + journal.FAILED_RETENTION_SECONDS + 10) == 3
    assert journal.list_entries(conn, limit=10) == []
