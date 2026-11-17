"""Store of remote knowledge bases and themes across servers (KNOWLEDGE_FEDERATION_DESIGN.md Sections 8.1 and 8.2, unit KF2).

The tests use a real ``claire.db`` in ``tmp_path`` and examine the interfaces between modules: the migration step v30 (``store.db``), the store ``knowledge_federation.remote_kbs`` with the known servers and the domain blocks, the internal routes of the sidecar for the reads of a ``/kb`` collection, and the remote members of a theme (``store.themes``, ``theme_judgement``, the admin routes). This server is ``https://a.example``; the remote knowledge base is the contract fixture of ``https://b.example``.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from claire.knowledge_federation import known_servers, remote_kbs
from claire.store import db as dbm
from claire.store import federation as federation_store
from claire.store import themes as themes_store
from claire.store.secrets import reset_secret_service
from tests.knowledge_federation_servers import B_KB_NUMBER, KfServer, fixture

ORIGIN = "https://a.example"
B_KB = f"https://b.example/ap/kb/{B_KB_NUMBER}"
ADA = ["Ada Lovelace", "Analytical Engine", "Charles Babbage"]
A_KB_NUMBER = 1846000000000000200


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch):
    for key in list(os.environ):
        if key.startswith(("CLAIRE_", "CB_")):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("CLAIRE_SECRET_KEY", "66" * 32)
    reset_secret_service()
    yield
    reset_secret_service()


@pytest.fixture
def a(tmp_path: Path) -> KfServer:
    return KfServer(tmp_path, ORIGIN, username="alice_admin", mode="network")


def _item(**changes) -> dict:
    """The ``claire:KnowledgeBase`` of the contract fixture, as an item of a collection page (no ``@context``). Its signature has the entity type of the test knowledge bases."""
    item = {key: value for key, value in fixture("objects", "knowledge_base").items() if key != "@context"}
    item["claire:signature"] = {**item["claire:signature"], "entities": [["Concept", "ada lovelace", 6], ["Concept", "analytical engine", 5], ["Concept", "charles babbage", 4]]}
    return {**item, **changes}


def _known_and_read(server: KfServer, domain: str = "b.example") -> None:
    """The domain is a known server with a good read of its server actor: the condition of a read of its ``/kb`` collection."""

    def _apply(conn) -> None:
        known_servers.note_server(conn, domain, source="admin", added_by=1, now=server.now)
        known_servers.record_read(conn, domain, server_actor_iri=f"https://{domain}/ap/actors/server", software_name="claire-bible", software_version="1.0", shared_kb_count=None, now=server.now)

    server.db(_apply)


def _read(server: KfServer, items: list[dict], *, complete: bool = True, domain: str = "b.example"):
    """Simulate the sidecar: take the lease, then give the items of the collection in one batch."""
    leased = server.internal("GET", "/knowledge-kb-reads")
    assert leased.status_code == 200 and {"domain": domain, "url": f"https://{domain}/ap/kb"} in leased.json()["reads"]
    return server.internal("POST", "/knowledge-kb-reads", {"domain": domain, "items": items, "complete": complete, "error": None})


# --- Migration step v30 ---------------------------------------------------------


def test_migration_v30_makes_the_tables_and_keeps_a_table_of_another_form(tmp_path: Path) -> None:
    path = tmp_path / "claire.db"
    conn = dbm.connect(path)
    try:
        dbm.init_db(conn)
        for table, columns in {**remote_kbs.TABLES, themes_store.REMOTE_MEMBER_TABLE: themes_store.REMOTE_MEMBER_COLUMNS}.items():
            assert set(columns) <= dbm._column_names(conn, table)
        conn.execute("DROP TABLE kf_remote_knowledge_bases")
        conn.execute("CREATE TABLE kf_remote_knowledge_bases (iri TEXT PRIMARY KEY, note TEXT)")
        conn.execute("INSERT INTO kf_remote_knowledge_bases VALUES ('https://old.example/kb/1', 'kept')")
        conn.commit()
    finally:
        conn.close()
    for _ in range(2):
        conn = dbm.connect(path)
        try:
            dbm.init_db(conn)
            assert [tuple(row) for row in conn.execute("SELECT iri, note FROM kf_remote_knowledge_bases_retired_v30")] == [("https://old.example/kb/1", "kept")]
            assert set(remote_kbs.TABLES["kf_remote_knowledge_bases"]) <= dbm._column_names(conn, "kf_remote_knowledge_bases")
            assert not dbm._table_exists(conn, "kf_remote_knowledge_bases_retired_v30_2")
            assert dbm.stored_schema_version(conn) == dbm.SCHEMA_VERSION >= 30
        finally:
            conn.close()


# --- Reads of a /kb collection --------------------------------------------------


def test_a_read_is_due_after_a_good_server_read_and_one_time_each_day(a: KfServer) -> None:
    a.db(lambda conn: known_servers.note_server(conn, "b.example", source="admin", added_by=1, now=a.now))
    # The server actor of B was not read: no read of its collection.
    assert a.internal("GET", "/knowledge-kb-reads").json() == {"reads": []}
    _known_and_read(a)
    assert a.internal("GET", "/knowledge-kb-reads").json() == {"reads": [{"domain": "b.example", "url": "https://b.example/ap/kb"}]}
    # The lease: a second request gives nothing.
    assert a.internal("GET", "/knowledge-kb-reads").json() == {"reads": []}
    done = a.internal("POST", "/knowledge-kb-reads", {"domain": "b.example", "items": [_item()], "complete": True, "error": None})
    assert done.status_code == 200 and done.json() == {"domain": "b.example", "stored": 1, "refused": 0, "refused_code": None, "removed": 0}
    # The read ended: a batch without a lease gets 404, and the next read is due after one day.
    assert a.internal("POST", "/knowledge-kb-reads", {"domain": "b.example", "items": [], "complete": True, "error": None}).status_code == 404
    a.now += remote_kbs.READ_INTERVAL_SECONDS - 60
    assert a.internal("GET", "/knowledge-kb-reads").json() == {"reads": []}
    a.now += 120
    assert [read["domain"] for read in a.internal("GET", "/knowledge-kb-reads").json()["reads"]] == ["b.example"]
    # While the switch ``federation`` is off, no read is due.
    a.now += remote_kbs.READ_LEASE_SECONDS + 1
    a.db(lambda conn: conn.execute("UPDATE server_settings SET value='off' WHERE key='federation'"))
    assert a.internal("GET", "/knowledge-kb-reads").json() == {"reads": []}


def test_a_batch_stores_valid_items_and_refuses_the_others(a: KfServer) -> None:
    _known_and_read(a)
    other_server = _item(id="https://c.example/ap/kb/1", inbox="https://c.example/ap/kb/1/inbox", outbox="https://c.example/ap/kb/1/outbox", followers="https://c.example/ap/kb/1/followers", **{"claire:documents": "https://c.example/ap/kb/1/documents"})
    invalid = {key: value for key, value in _item(id=f"{B_KB}9").items() if key != "claire:knowledgeType"}
    result = _read(a, [_item(), other_server, invalid, {"type": "Note"}, _item(id=f"{B_KB}8", name="x" * (remote_kbs.LABEL_MAX + 1))], complete=False)
    assert result.status_code == 200
    assert result.json() == {"domain": "b.example", "stored": 1, "refused": 4, "refused_code": "other_server", "removed": None}

    def _stored(conn) -> list[tuple]:
        return [(kb.iri, kb.domain, kb.url, kb.label, kb.knowledge_type, kb.document_count, remote_kbs.signature_rows(kb)[0]) for kb in remote_kbs.list_all(conn)]

    assert a.db(_stored) == [(B_KB, "b.example", f"https://b.example/kb/{B_KB_NUMBER}/", "Ada Lovelace and the Analytical Engine", "real", 12, ("Concept", "ada lovelace", 6.0))]
    # A failed read keeps the records.
    failed = a.internal("POST", "/knowledge-kb-reads", {"domain": "b.example", "items": [], "complete": False, "error": "fetch_failed"})
    assert failed.status_code == 200
    state = a.db(lambda conn: remote_kbs.read_state(conn, "b.example"))
    assert (state.last_read_at, state.error_count, state.last_error, state.refused_code) == (None, 1, "fetch_failed", "other_server")
    assert len(a.db(_stored)) == 1


@pytest.mark.parametrize(
    "body",
    [
        {"domain": "b.example", "items": [], "complete": True},  # ``error`` is necessary (null for a good batch)
        {"domain": "b.example", "items": [], "complete": True, "error": None, "extra": 1},
        {"domain": "b.example", "items": [{}], "complete": False, "error": "fetch_failed"},  # an error has no item
        {"domain": "b.example", "items": [], "complete": True, "error": "fetch_failed"},  # an error does not end a read
        {"domain": "b.example", "items": [], "complete": True, "error": "Not A Code"},
    ],
)
def test_the_read_route_has_a_sealed_body(a: KfServer, body: dict) -> None:
    response = a.internal("POST", "/knowledge-kb-reads", body)
    assert response.status_code == 400 and response.json() == {"error": "invalid_request"}


def test_a_complete_read_and_a_domain_block_remove_records_with_their_theme_memberships(a: KfServer) -> None:
    _known_and_read(a)
    second = _item(id=f"{B_KB}1", name="Second", url=f"https://b.example/kb/{B_KB_NUMBER}1/", inbox=f"{B_KB}1/inbox", outbox=f"{B_KB}1/outbox", followers=f"{B_KB}1/followers", **{"claire:documents": f"{B_KB}1/documents"})
    assert _read(a, [_item(), second]).json()["stored"] == 2

    def _pin_all(conn) -> int:
        theme = themes_store.create_theme(conn, label="Computing", knowledge_type="real")
        for kb in remote_kbs.list_all(conn):
            themes_store.set_admin_remote_member(conn, theme_id=theme["id"], remote_kb_id=kb.id, pinned=True, excluded=False)
        return int(theme["id"])

    theme_id = a.db(_pin_all)
    assert len(a.db(lambda conn: themes_store.remote_members(conn, theme_id=theme_id))) == 2
    # The next complete read does not have the second knowledge base: its record and its membership go.
    a.now += remote_kbs.READ_INTERVAL_SECONDS + 1
    assert _read(a, [_item()]).json() == {"domain": "b.example", "stored": 1, "refused": 0, "refused_code": None, "removed": 1}
    assert [kb.iri for kb in a.db(remote_kbs.list_all)] == [B_KB]
    assert len(a.db(lambda conn: themes_store.remote_members(conn, theme_id=theme_id))) == 1
    # A domain block of the admin removes the records of the domain; the block of a parent domain applies too.
    session = a.admin_session()
    assert a.request("POST", "/api/admin/federation/domain-blocks", session=session, json={"domain": "b.example", "note": "test"}).status_code == 201
    assert a.db(remote_kbs.list_all) == []
    assert a.db(lambda conn: themes_store.remote_members(conn, theme_id=theme_id)) == []
    assert a.db(lambda conn: remote_kbs.read_state(conn, "b.example")) is None
    # A blocked server has no read.
    a.now += remote_kbs.READ_INTERVAL_SECONDS + 1
    assert a.internal("GET", "/knowledge-kb-reads").json() == {"reads": []}
    assert a.db(lambda conn: federation_store.blocking_domain(conn, "b.example")) == "b.example"


# --- Remote members of a theme --------------------------------------------------


def test_admin_adjustments_apply_to_a_remote_member_and_stay_after_a_run(a: KfServer) -> None:
    a.add_shared_kb(A_KB_NUMBER, "Early computing")
    a.fill_kb(A_KB_NUMBER, ADA)
    _known_and_read(a)
    assert _read(a, [_item()]).json()["stored"] == 1
    report = a.judge()
    assert (report.themes_active, report.remote_memberships) == (1, 1)
    session = a.admin_session()
    theme = a.request("GET", "/api/admin/themes", session=session).json()["themes"][0]
    remote = next(kb for kb in theme["knowledge_bases"] if kb["remote"])
    assert (remote["server"], remote["url"], remote["source"], remote["pinned"], remote["excluded"], remote["number"]) == ("b.example", f"https://b.example/kb/{B_KB_NUMBER}/", "computed", False, False, None)
    assert remote["score"] >= 0.25
    member = f"/api/admin/themes/{theme['number']}/remote-knowledge-bases/{remote['remote_id']}"

    # Exclude: the remote knowledge base is not in the theme view, and a new run does not put it back.
    excluded = a.request("PUT", member, session=session, json={"membership": "excluded"})
    assert excluded.status_code == 200
    assert [kb["excluded"] for kb in excluded.json()["theme"]["knowledge_bases"] if kb["remote"]] == [True]
    assert a.judge().remote_memberships == 0
    view = a.request("GET", f"/themes/{theme['number']}/", headers={"Accept": "application/json"}).json()
    assert (view["remote_knowledge_bases"], [kb for kb in view["knowledge_bases"] if kb["remote"]]) == (0, [])
    # Pin: the membership stays after a run, also when the signature does not match the theme.
    assert a.request("PUT", member, session=session, json={"membership": "pinned"}).status_code == 200
    a.db(lambda conn: conn.execute("UPDATE kf_remote_knowledge_bases SET signature=NULL"))
    a.judge()
    view = a.request("GET", f"/themes/{theme['number']}/", headers={"Accept": "application/json"}).json()
    assert [(kb["server"], kb["label"]) for kb in view["knowledge_bases"] if kb["remote"]] == [("b.example", "Ada Lovelace and the Analytical Engine")]
    # Merge: the remote member goes to the other theme as a pinned member.
    target = a.request("POST", "/api/admin/themes", session=session, json={"label": "Machines", "knowledge_type": "real"}).json()["theme"]
    merged = a.request("POST", f"/api/admin/themes/{theme['number']}/merge", session=session, json={"into": target["number"]})
    assert merged.status_code == 200
    assert [(kb["remote"], kb["pinned"]) for kb in merged.json()["theme"]["knowledge_bases"]] == [(False, True), (True, True)]
    # ``computed`` gives the decision back to the judgement. A record that does not exist, and each other principal, gets 404.
    back = a.request("PUT", f"/api/admin/themes/{target['number']}/remote-knowledge-bases/{remote['remote_id']}", session=session, json={"membership": "computed"})
    assert [kb["remote"] for kb in back.json()["theme"]["knowledge_bases"]] == [False]
    assert a.request("PUT", f"/api/admin/themes/{target['number']}/remote-knowledge-bases/999", session=session, json={"membership": "pinned"}).status_code == 404
    assert a.request("PUT", member, json={"membership": "pinned"}).status_code == 404
    # A theme has one knowledge type.
    fiction = a.request("POST", "/api/admin/themes", session=session, json={"label": "Fiction", "knowledge_type": "fictional"}).json()["theme"]
    mismatch = a.request("PUT", f"/api/admin/themes/{fiction['number']}/remote-knowledge-bases/{remote['remote_id']}", session=session, json={"membership": "pinned"})
    assert (mismatch.status_code, mismatch.json()) == (409, {"error": "knowledge_type_mismatch"})


def test_a_remote_knowledge_base_makes_no_theme_and_a_new_record_makes_a_run_due(a: KfServer) -> None:
    from claire import theme_judgement

    a.judge()
    assert a.db(lambda conn: theme_judgement.run_is_due(conn, now=time.time())) is False
    _known_and_read(a)
    a.now = time.time() + 5
    assert _read(a, [_item()]).json()["stored"] == 1
    # The new record makes a run due. With no local shared knowledge base of the subject there is no theme: a remote knowledge base attaches to a local theme and never makes one.
    assert a.db(lambda conn: theme_judgement.run_is_due(conn, now=time.time())) is True
    report = a.judge()
    assert (report.themes_active, report.themes_new, report.remote_memberships) == (0, 0, 0)
    assert a.db(lambda conn: themes_store.remote_members(conn)) == []
