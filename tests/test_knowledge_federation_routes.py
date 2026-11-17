"""HTTP routes of the federation of shared knowledge (KNOWLEDGE_FEDERATION_DESIGN.md Sections 6.2, 6.3, 6.5, 13, and 14; unit KF1).

- The internal routes of the sidecar (``api.knowledge_federation_internal``), through the signed internal app.
- The admin routes (``api.accounts_routes.knowledge_federation``), through the public app.

Server B is ``https://b.example`` in single mode with the switch ``federation`` on.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from claire.knowledge_federation import journal
from claire.store import auth_db
from claire.store import federation as federation_store
from claire.store.secrets import reset_secret_service
from tests.knowledge_federation_servers import ALICE, B_KB_NUMBER, KfServer, fixture, server_actor

KNOWN = "/api/admin/federation/known-servers"
JOURNAL = "/api/admin/federation/journal"
A_SERVER = server_actor("https://a.example")
KB_IRI = f"https://b.example/ap/kb/{B_KB_NUMBER}"


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch):
    for key in list(os.environ):
        if key.startswith(("CLAIRE_", "CB_")):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("CLAIRE_SECRET_KEY", "55" * 32)
    monkeypatch.setattr(journal, "HANDLERS", {})
    reset_secret_service()
    yield
    reset_secret_service()


@pytest.fixture
def b(tmp_path: Path) -> KfServer:
    server = KfServer(tmp_path, "https://b.example", username="bora")
    server.add_shared_kb(B_KB_NUMBER, "Analytical Engine")
    server.add_shared_kb(B_KB_NUMBER + 1, "Middle-earth", knowledge_type="fictional")
    server.db(lambda conn: federation_store.add_domain_block(conn, "c.example", created_by=1, now=server.now))
    return server


def _audit(server: KfServer) -> list[tuple[str, dict]]:
    conn = auth_db.connect_auth_db(auth_db.auth_db_path(server.settings.data_dir))
    try:
        return [(row[0], json.loads(row[1])) for row in conn.execute("SELECT action, detail_json FROM admin_audit ORDER BY id").fetchall()]
    finally:
        conn.close()


# --- Internal: POST /knowledge-activities ------------------------------------------


def test_internal_activity_route_records_the_journal_and_answers_the_decision(b) -> None:
    offer = fixture("activities", "offer_contribution")
    first = b.deliver(offer, signer=ALICE, recipient=KB_IRI)
    assert first.status_code == 200
    body = first.json()
    assert body["decision"] == "failed" and body["reason"] == "no_handler" and isinstance(body["journal_id"], int)
    assert b.deliver(offer, signer=ALICE, recipient=KB_IRI).json() == {"decision": "duplicate", "reason": "failed", "journal_id": body["journal_id"]}
    assert b.deliver(offer, signer="https://c.example/ap/actors/server", recipient=None).json() == {"decision": "rejected", "reason": "blocked_domain", "journal_id": None}
    invalid = fixture("invalid", "follow_without_recipient")
    refused = b.deliver(invalid["value"], signer=A_SERVER, recipient=None).json()
    assert (refused["decision"], refused["reason"]) == ("refused", "missing_recipient")


@pytest.mark.parametrize(
    "body",
    [
        {"activity": {}, "signer": ALICE, "verified_by": "none", "recipient": None},
        {"activity": {}, "signer": ALICE, "verified_by": "http-signature"},  # ``recipient`` is necessary (null for the shared inbox)
        {"activity": {}, "signer": ALICE, "verified_by": "http-signature", "recipient": None, "extra": 1},
        {"activity": [], "signer": ALICE, "verified_by": "http-signature", "recipient": None},
        {"activity": {}, "signer": "", "verified_by": "http-signature", "recipient": None},
    ],
)
def test_internal_activity_route_has_a_sealed_body(b, body) -> None:
    response = b.internal("POST", "/knowledge-activities", body)
    assert response.status_code == 400 and response.json() == {"error": "invalid_request"}


# --- Internal: GET /knowledge-collections/{name} -------------------------------------


def test_internal_collection_route_answers_a_server_actor_and_gives_404_to_each_refusal(b) -> None:
    kb = b.internal("GET", f"/knowledge-collections/kb?signer={A_SERVER}")
    assert kb.status_code == 200
    body = kb.json()
    assert (body["collection"], body["server"]) == ("kb", "a.example")
    assert [(i["number"], i["label"], i["knowledge_type"], i["state"], i["url"]) for i in body["items"]] == [
        (str(B_KB_NUMBER + 1), "Middle-earth", "fictional", "active", f"https://b.example/kb/{B_KB_NUMBER + 1}/"),
        (str(B_KB_NUMBER), "Analytical Engine", "real", "active", f"https://b.example/kb/{B_KB_NUMBER}/"),
    ]
    # The personal knowledge base of the admin is never in the collection (KD3).
    assert all(i["label"] != "기본 지식베이스" for i in body["items"])
    themes = b.internal("GET", f"/knowledge-collections/themes?signer={A_SERVER}")
    assert themes.status_code == 200 and themes.json()["collection"] == "themes"

    not_found = {"error": "not_found"}
    for query in ("", "?signer=https://c.example/ap/actors/server", "?signer=https://kb.c.example/ap/actors/server", f"?signer={ALICE}", "?signer=https://b.example/ap/actors/server"):
        for name in ("kb", "themes"):
            response = b.internal("GET", f"/knowledge-collections/{name}{query}")
            assert response.status_code == 404, (name, query)
            assert response.json() == not_found
    assert b.internal("GET", f"/knowledge-collections/accounts?signer={A_SERVER}").status_code == 404
    assert b.internal("GET", f"/knowledge-collections/kb?signer={A_SERVER}&page=2").status_code == 400


def test_internal_collection_route_gives_404_while_the_switch_is_off(b) -> None:
    from claire.store import accounts as accounts_store

    b.db(lambda conn: accounts_store.AccountStore(conn).set_federation_state("off", updated_by=1))
    assert b.internal("GET", f"/knowledge-collections/kb?signer={A_SERVER}").status_code == 404


# --- Internal: /knowledge-server-reads ----------------------------------------------


def test_internal_server_reads_lease_and_record(b) -> None:
    from claire.knowledge_federation import known_servers

    b.db(lambda conn: [known_servers.note_server(conn, d, source="admin", added_by=1, now=b.now - 10) for d in ("a.example", "c.example", "d.example")])
    due = b.internal("GET", "/knowledge-server-reads?limit=5")
    assert due.status_code == 200 and due.json() == {"servers": [{"domain": "a.example"}, {"domain": "d.example"}]}
    assert b.internal("GET", "/knowledge-server-reads").json() == {"servers": []}
    for query in ("?limit=0", "?limit=21", "?limit=x", "?other=1"):
        assert b.internal("GET", f"/knowledge-server-reads{query}").status_code == 400, query

    good = {"domain": "a.example", "ok": True, "server_actor": A_SERVER, "software_name": "claire-bible", "software_version": "1.0", "shared_kb_count": 2, "error": None}
    assert b.internal("POST", "/knowledge-server-reads", good).json() == {"domain": "a.example", "recorded": True}
    failed = {"domain": "d.example", "ok": False, "server_actor": None, "software_name": None, "software_version": None, "shared_kb_count": None, "error": "nodeinfo_timeout"}
    assert b.internal("POST", "/knowledge-server-reads", failed).status_code == 200
    # The server actor must be on the domain that was read; a failure has no data; the error is a code.
    for bad in ({**good, "server_actor": server_actor("https://d.example")}, {**failed, "software_name": "x"}, {**failed, "error": "Time out"}, {**good, "error": "x"}):
        assert b.internal("POST", "/knowledge-server-reads", bad).status_code == 400
    assert b.internal("POST", "/knowledge-server-reads", {**good, "domain": "unknown.example", "server_actor": server_actor("https://unknown.example")}).status_code == 404
    view = b.db(lambda conn: known_servers.server_view(conn, known_servers.get_server(conn, "a.example")))
    assert (view["server_actor"], view["software_name"], view["kb_count"]) == (A_SERVER, "claire-bible", 2)


# --- Admin routes -------------------------------------------------------------------------


def test_admin_known_servers_list_and_entry(b) -> None:
    session = b.admin_session()
    empty = b.request("GET", KNOWN, session=session)
    assert empty.status_code == 200 and empty.headers["cache-control"] == "no-store"
    assert empty.json() == {"servers": [], "switches": {"federation": "on", "kb_auto_contribution": "off", "signed_get": "required"}}

    made = b.request("POST", KNOWN, session=session, json={"domain": "A.Example"})
    assert made.status_code == 201 and made.json()["created"] is True
    server = made.json()["server"]
    assert (server["domain"], server["source"], server["status"], server["last_contact_at"]) == ("a.example", "admin", "active", None)
    again = b.request("POST", KNOWN, session=session, json={"domain": "a.example"})
    assert again.status_code == 200 and again.json()["created"] is False
    blocked = b.request("POST", KNOWN, session=session, json={"domain": "kb.c.example"})
    assert blocked.status_code == 201 and (blocked.json()["server"]["status"], blocked.json()["server"]["blocked_by"]) == ("blocked", "c.example")
    assert [s["domain"] for s in b.request("GET", KNOWN, session=session).json()["servers"]] == ["a.example", "kb.c.example"]
    # One audit row for each server that the admin added.
    assert _audit(b)[-2:] == [("federation.known_server_add", {"domain": "a.example"}), ("federation.known_server_add", {"domain": "kb.c.example"})]

    for body, error in (({"domain": "https://a.example/"}, "invalid_domain"), ({"domain": 7}, "invalid_domain"), ({"domain": "b.example"}, "own_domain"), ({"domain": "a.example", "note": "x"}, "invalid_request")):
        response = b.request("POST", KNOWN, session=session, json=body)
        assert response.status_code == 400 and response.json()["error"] == error, body
    assert b.request("POST", KNOWN, session=session, json=[]).status_code == 400


def test_admin_routes_are_404_for_others_and_a_change_needs_origin_and_recent_auth(b) -> None:
    for path in (KNOWN, JOURNAL, f"{JOURNAL}/1"):
        assert b.request("GET", path).status_code == 404
    assert b.request("POST", KNOWN, json={"domain": "a.example"}).status_code == 404
    assert b.request("POST", f"{JOURNAL}/1/replay", json={}).status_code == 404
    session = b.admin_session()
    assert b.request("POST", KNOWN, session=session, json={"domain": "a.example"}, origin="https://evil.example").status_code == 403
    stale = b.admin_session(authenticated_ago=3600)
    assert b.request("GET", KNOWN, session=stale).status_code == 200
    response = b.request("POST", KNOWN, session=stale, json={"domain": "a.example"})
    assert response.status_code == 401 and response.json()["error"] == "reauth_required"
    assert b.request("POST", f"{JOURNAL}/1/replay", session=stale, json={}).status_code == 401
    assert b.request("GET", KNOWN, session=session).json()["servers"] == []


def test_admin_journal_list_detail_and_replay(b, monkeypatch: pytest.MonkeyPatch) -> None:
    offer = fixture("activities", "offer_contribution")
    entry_id = b.deliver(offer, signer=ALICE, recipient=KB_IRI).json()["journal_id"]
    follow_id = b.deliver(fixture("activities", "follow"), signer=A_SERVER, recipient=None).json()["journal_id"]
    session = b.admin_session()

    listing = b.request("GET", JOURNAL, session=session)
    assert listing.status_code == 200 and listing.headers["cache-control"] == "no-store"
    entries = listing.json()["entries"]
    assert [(e["id"], e["kind"], e["status"], e["error_code"], e["replayable"]) for e in entries] == [(follow_id, "follow", "failed", "no_handler", True), (entry_id, "offer_contribution", "failed", "no_handler", True)]
    assert "payload" not in entries[0] and listing.json()["next_max_id"] is None
    page = b.request("GET", f"{JOURNAL}?limit=1", session=session).json()
    assert [e["id"] for e in page["entries"]] == [follow_id] and page["next_max_id"] == follow_id
    assert [e["id"] for e in b.request("GET", f"{JOURNAL}?limit=1&max_id={follow_id}", session=session).json()["entries"]] == [entry_id]
    assert b.request("GET", f"{JOURNAL}?status=processed", session=session).json()["entries"] == []
    for query, error in (("?status=lost", "invalid_status"), ("?limit=0", "invalid_limit"), ("?limit=201", "invalid_limit"), ("?max_id=01", "invalid_max_id")):
        response = b.request("GET", f"{JOURNAL}{query}", session=session)
        assert response.status_code == 400 and response.json()["error"] == error, query

    detail = b.request("GET", f"{JOURNAL}/{entry_id}", session=session)
    assert detail.status_code == 200 and detail.json()["entry"]["payload"] == offer
    assert b.request("GET", f"{JOURNAL}/999", session=session).json() == {"error": "journal_entry_not_found"}
    assert b.request("GET", f"{JOURNAL}/x", session=session).status_code == 404

    # A later unit installs the handler; the admin processes the failed row again.
    applied: list[str] = []
    monkeypatch.setitem(journal.HANDLERS, "offer_contribution", lambda ctx, entry, activity: applied.append(activity["id"]))
    assert b.request("POST", f"{JOURNAL}/{entry_id}/replay", session=session, json={"x": 1}).status_code == 400
    replayed = b.request("POST", f"{JOURNAL}/{entry_id}/replay", session=session, json={})
    assert replayed.status_code == 200
    assert (replayed.json()["entry"]["status"], replayed.json()["entry"]["attempts"]) == ("processed", 2)
    assert applied == [offer["id"]]
    again = b.request("POST", f"{JOURNAL}/{entry_id}/replay", session=session, json={})
    assert again.status_code == 409 and again.json() == {"error": "journal_entry_not_replayable", "states": ["failed", "received"]}
    assert b.request("POST", f"{JOURNAL}/999/replay", session=session, json={}).status_code == 404
    assert _audit(b)[-1] == ("federation.journal_replay", {"id": entry_id, "status": "processed", "error_code": None})

    from claire.store import accounts as accounts_store

    b.db(lambda conn: accounts_store.AccountStore(conn).set_federation_state("off", updated_by=1))
    off = b.request("POST", f"{JOURNAL}/{follow_id}/replay", session=session, json={})
    assert off.status_code == 409 and off.json() == {"error": "federation_off"}
    assert b.request("GET", f"{JOURNAL}/{follow_id}", session=session).json()["entry"]["status"] == "failed"
