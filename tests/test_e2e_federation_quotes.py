"""End-to-end test of quotes on the Claire side (FEP-044f; FEDERATION_REMOTE_READING_DESIGN.md Sections 5.4 rules 5 and 6 and 6.6 rule 5; unit FP4-9Q).

A real app in network mode and the signed internal API of the same files. Outbound: the owner quotes an item, the sidecar takes the ``quote_request`` task, and the answers of the author (``Accept`` with a ``QuoteAuthorization``, ``Reject``, ``Delete`` of the authorization) change the quote state. Inbound: a ``QuoteRequest`` for an own note or a published document gets the result of the quote policy, the owner decides a request that waits, and a revoked authorization is gone (410). The sidecar side of the same contract (the activities at a local test inbox and the public path of an authorization) is in ``sidecar/fedify/tests/quotes_test.ts``.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from claire.api import server
from claire.store import accounts as accounts_store
from claire.store import client_db
from claire.store import db as dbm
from tests.test_e2e_federation_following import _client_rows, _Sidecar
from tests.test_e2e_federation_timeline import CAROL, STRANGER, _actor, _event, _object
from tests.test_federation_console import ALPHA_PUBLIC_ID, PUBLIC_URL, _Client, _db, _isolate, _prepare, _session  # noqa: F401  (_isolate is an autouse fixture)

ALPHA_ACTOR = f"{PUBLIC_URL}/ap/actors/{ALPHA_PUBLIC_ID}"
NOTES = "/api/federation/notes"
QUOTES = "/api/federation/quotes"
OBJECTS = "/api/federation/objects"
TIMELINE = "/api/federation/timeline"
PARENT = f"{CAROL}/notes/1"
SECOND = f"{CAROL}/notes/2"
OBJECT_ID = 816412345678901234
OBJECT = f"{PUBLIC_URL}/ap/objects/{OBJECT_ID}"
YURI = "https://other.example/users/yuri"
AUTHORIZATION = f"{CAROL}/quote-authorizations/1"


@pytest.fixture
def env(tmp_path):
    settings, alpha, beta = _prepare(tmp_path)
    _db(settings, lambda conn: accounts_store.AccountStore(conn).set_federation_state("on", updated_by=1))
    _db(settings, lambda conn: conn.execute("UPDATE accounts SET profile_public=1 WHERE id IN (?, ?)", (alpha, beta)))
    # Alpha has one published document in a public theme.
    _db(settings, lambda conn: conn.execute("INSERT INTO knowledge_bases (id, seq, label, is_personal, is_public, is_collaborator_accessible, created_at, updated_at, account_id, number) VALUES (11, 11, 'alpha theme', 1, 1, 0, 1, 1, ?, 516412345678901234)", (alpha,)))
    _db(settings, lambda conn: conn.execute("INSERT INTO documents (id, kb_id, title, detail, detail_format, hidden, fetched_at) VALUES ('doc-published', 11, 'Published', 'body', 'md', 0, 1)"))
    _db(settings, lambda conn: conn.execute("INSERT INTO federation_objects (public_id, account_id, kb_id, document_id, published_by_account_id, published_at, updated_at) VALUES (?, ?, 11, 'doc-published', ?, 1800000000.0, 1800000000.0)", (OBJECT_ID, alpha, alpha)))
    app = server.create_app(settings, SimpleNamespace(provider=SimpleNamespace(name="stub")))
    with TestClient(app, base_url=PUBLIC_URL) as tc:
        client = client_db.connect_client_existing(client_db.client_db_path(settings.db_file))
        try:
            client.execute("INSERT INTO federation_following (account_id, request, actor_iri, actor_type, state, created_at, updated_at) VALUES (?, '@carol@remote.example', ?, 'Person', 'accepted', 1, 1)", (alpha, CAROL))
            client.commit()
        finally:
            client.close()
        env = SimpleNamespace(client=_Client(tc), sidecar=_Sidecar(settings), settings=settings, alpha=alpha, beta=beta, admin=_session(settings, 1), alpha_session=_session(settings, alpha), beta_session=_session(settings, beta))
        # Two posts of carol are in the timeline of alpha.
        for iri in (PARENT, SECOND):
            assert env.sidecar.call("POST", "/timeline-events", _event("Create", CAROL, _object(iri, CAROL))).json()["items"] == 1
        yield env


def _item_id(env, object_iri: str) -> str:
    [(item_id,)] = _client_rows(env.settings, "SELECT id FROM federation_timeline_items WHERE account_id=? AND object_iri=?", (env.alpha, object_iri))
    return str(item_id)


def _tasks(env) -> list[dict]:
    response = env.sidecar.call("GET", "/interaction-tasks?limit=20")
    assert response.status_code == 200, response.text
    return response.json()["tasks"]


def _ack(env, task: dict) -> None:
    response = env.sidecar.call("POST", f"/interaction-tasks/{task['id']}/ack", {"result": "handed_off", "error_code": None, "mentions": []})
    assert response.status_code == 200 and response.json()["status"] == "handed_off", response.text


def _quote_event(env, type_: str, actor: str, object_iri: str, *, instrument: str | None = None, authorization: dict | None = None, n: list[int] = [0]) -> dict:
    n[0] += 1
    body = {"event_id": f"{actor}#quote-activities/{n[0]}", "type": type_, "recipient_public_id": None, "actor": _actor(actor), "object_iri": object_iri, "instrument_iri": instrument, "authorization": authorization, "verified_by": "http-signature"}
    response = env.sidecar.call("POST", "/quote-events", body)
    assert response.status_code == 200, response.text
    return response.json()


def _note(env, **body) -> dict:
    response = env.client.request("POST", NOTES, session=env.alpha_session, json=body)
    assert response.status_code == 201, response.text
    return response.json()


def _quote_state(env, item_id: str) -> dict:
    return env.client.request("GET", f"{TIMELINE}/{item_id}", session=env.alpha_session).json()["item"]["quote"]


def test_e2e_quote_of_a_remote_object_becomes_approved_refused_and_revoked(env):
    c, sc = env.client, env.sidecar
    quoted_item = _item_id(env, PARENT)
    made = _note(env, content="Look at this", quote_of=quoted_item)
    note, item_id = made["note"], made["item_id"]
    request_iri = f"{note['url']}#quote-request"
    # Section 5.4 rule 5: the quote waits, and the item of the note names the quoted item.
    assert note["quote"] == {"iri": PARENT, "state": "pending"}
    assert _quote_state(env, item_id) == {"iri": PARENT, "item_id": quoted_item, "state": "pending"}

    # The sidecar gets one task: QuoteRequest to the author of the quoted object, then Create of the note with the quote.
    [task] = _tasks(env)
    assert (task["kind"], task["ref_id"], task["object_iri"], task["recipients"], task["quote"]) == ("quote_request", note["id"], PARENT, [CAROL], None)
    record = sc.call("GET", f"/notes/{note['id']}").json()
    assert (record["quote"], record["quote_authorization"], record["quote_policy"]) == (PARENT, None, "public")
    # A reader that does not show quotes has the link in the HTML.
    assert f'<p class="quote-inline">RE: <a href="{PARENT}"' in record["content"]
    _ack(env, task)

    authorization = {"iri": AUTHORIZATION, "interacting_object": note["url"], "interaction_target": PARENT, "attributed_to": CAROL}
    # Untrusted input: an answer of an actor that is not the author of the quoted object, and an authorization that names another note, change nothing.
    assert _quote_event(env, "Accept", STRANGER, request_iri, authorization={**authorization, "iri": f"{STRANGER}/stamps/1", "attributed_to": STRANGER})["reason"] == "not_the_author"
    assert _quote_event(env, "Accept", CAROL, request_iri, authorization={**authorization, "interacting_object": f"{PUBLIC_URL}/ap/objects/1"})["reason"] == "invalid_authorization"
    assert _quote_event(env, "Reject", STRANGER, request_iri)["reason"] == "not_the_author"
    assert _quote_state(env, item_id)["state"] == "pending" and _tasks(env) == []

    # Completion criterion: the quote becomes approved after the authorization. The followers get the note again with the authorization.
    assert _quote_event(env, "Accept", CAROL, request_iri, authorization=authorization) == {"decision": "accepted", "reason": None, "state": "approved"}
    assert _quote_state(env, item_id)["state"] == "approved"
    [task] = _tasks(env)
    assert (task["kind"], task["ref_id"]) == ("update_note", note["id"])
    record = sc.call("GET", f"/notes/{note['id']}").json()
    assert (record["quote"], record["quote_authorization"], record["edited_at"]) == (PARENT, AUTHORIZATION, None)
    _ack(env, task)

    # A Delete of another object, or of the authorization by another actor, is not a revoke.
    assert sc.call("POST", "/timeline-events", _event("Delete", STRANGER, None, object_iri=AUTHORIZATION, public=None)).json()["decision"] == "ignored"
    assert _quote_state(env, item_id)["state"] == "approved"
    # The author deletes the authorization: the quote is revoked, and the note keeps a link only (RT9: the state changes on the Delete activity).
    assert sc.call("POST", "/timeline-events", _event("Delete", CAROL, None, object_iri=AUTHORIZATION, public=None)).json()["decision"] == "accepted"
    assert _quote_state(env, item_id) == {"iri": PARENT, "item_id": quoted_item, "state": "revoked"}
    record = sc.call("GET", f"/notes/{note['id']}").json()
    assert (record["quote"], record["quote_authorization"]) == (None, None) and f'RE: <a href="{PARENT}"' in record["content"]
    [task] = _tasks(env)
    assert task["kind"] == "update_note"
    _ack(env, task)

    # A second quote gets a Reject: refused. A server that does not answer leaves the third one pending.
    refused = _note(env, content="Second quote", quote_of=_item_id(env, SECOND))
    waiting = _note(env, content="Third quote", quote_of=_item_id(env, SECOND), in_reply_to=quoted_item)
    assert [(t["kind"], t["ref_id"]) for t in _tasks(env)] == [("quote_request", refused["note"]["id"]), ("quote_request", waiting["note"]["id"])]
    assert _quote_event(env, "Reject", CAROL, f"{refused['note']['url']}#quote-request") == {"decision": "accepted", "reason": None, "state": "refused"}
    assert _quote_state(env, refused["item_id"])["state"] == "refused"
    assert sc.call("GET", f"/notes/{refused['note']['id']}").json()["quote"] is None
    assert _quote_state(env, waiting["item_id"])["state"] == "pending"

    # Refusals of the route: an item that the caller does not have, an own note, and a blocked domain.
    assert c.request("POST", NOTES, session=env.beta_session, json={"content": "x", "quote_of": quoted_item}).json() == {"error": "item_not_found"}
    assert c.request("POST", NOTES, session=env.alpha_session, json={"content": "x", "quote_of": item_id}).json() == {"error": "own_item"}
    assert c.request("POST", NOTES, session=env.alpha_session, json={"content": "x", "quote_policy": "everyone"}).json() == {"error": "invalid_quote_policy"}
    c.request("POST", "/api/admin/federation/domain-blocks", session=env.admin, json={"domain": "remote.example"})
    assert c.request("POST", NOTES, session=env.alpha_session, json={"content": "x", "quote_of": quoted_item}).json() == {"error": "blocked_domain"}


def test_e2e_inbound_quote_of_a_remote_actor_shows_a_link(env):
    # RT9: the sidecar reads ``quote`` first; Claire stores the address and gives the item of the quoted object when the account has it.
    quoting = f"{CAROL}/notes/9"
    assert env.sidecar.call("POST", "/timeline-events", _event("Create", CAROL, _object(quoting, CAROL, quote=PARENT))).json()["items"] == 1
    assert _quote_state(env, _item_id(env, quoting)) == {"iri": PARENT, "item_id": _item_id(env, PARENT), "state": None}
    assert _quote_state(env, _item_id(env, PARENT)) is None


def test_e2e_quote_requests_for_own_objects_follow_the_quote_policy(env):
    c, sc = env.client, env.sidecar
    made = _note(env, content="Quote me")
    note = made["note"]
    assert note["quote_policy"] == "public" and note["quotes"] == 0
    [task] = _tasks(env)
    _ack(env, task)

    def ask(actor: str, number: int, target: str = note["url"]) -> dict:
        return _quote_event(env, "QuoteRequest", actor, target, instrument=f"{actor}/notes/{number}")

    def policy(value: str) -> None:
        res = c.request("PATCH", f"{NOTES}/{note['id']}", session=env.alpha_session, json={"quote_policy": value})
        assert res.status_code == 200 and res.json()["note"]["quote_policy"] == value, res.text
        # A change of the policy sends Update of the note, and it is not an edit of the text.
        assert res.json()["note"]["edited_at"] is None
        [update] = _tasks(env)
        assert (update["kind"], update["ref_id"]) == ("update_note", note["id"])
        assert sc.call("GET", f"/notes/{note['id']}").json()["quote_policy"] == value
        _ack(env, update)

    # ``public``: automatic approval. The task carries the request and the quoting object for the Accept with the QuoteAuthorization.
    assert ask(STRANGER, 1) == {"decision": "accepted", "reason": None, "state": "approved"}
    [task] = _tasks(env)
    assert (task["kind"], task["object_iri"], task["recipients"], task["quote"]["quoting_object_iri"]) == ("quote_authorize", note["url"], [STRANGER], f"{STRANGER}/notes/1")
    first_id = task["ref_id"]
    _ack(env, task)
    # The authorization is dereferenceable, and its record does not have the quoting object, only its address.
    record = sc.call("GET", f"/quote-authorizations/{first_id}")
    assert record.status_code == 200 and record.json() == {"id": first_id, "actor_public_id": str(ALPHA_PUBLIC_ID), "object_public_id": note["id"], "quoting_object_iri": f"{STRANGER}/notes/1"}
    # A repeated request keeps the row and gets the answer again.
    assert ask(STRANGER, 1)["state"] == "approved"
    [task] = _tasks(env)
    assert (task["kind"], task["ref_id"]) == ("quote_authorize", first_id)
    _ack(env, task)
    assert c.request("GET", f"{TIMELINE}/{made['item_id']}", session=env.alpha_session).json()["item"]["note"]["quotes"] == 1

    # ``nobody``. Completion criterion: the request gets a Reject.
    policy("nobody")
    assert ask(STRANGER, 2) == {"decision": "rejected", "reason": "quote_refused", "state": "refused"}
    [task] = _tasks(env)
    assert (task["kind"], task["recipients"]) == ("quote_reject", [STRANGER])
    _ack(env, task)
    assert sc.call("GET", f"/quote-authorizations/{task['ref_id']}").status_code == 404

    # ``followers``: a follower gets the approval, another actor gets a Reject.
    policy("followers")
    follow = {"event_id": f"{CAROL}#follows/1", "type": "Follow", "object_type": None, "recipient_public_id": None, "actor": _actor(CAROL), "object_iri": ALPHA_ACTOR, "verified_by": "http-signature"}
    assert sc.call("POST", "/inbox-events", follow).json()["decision"] == "accepted"
    assert ask(CAROL, 3)["state"] == "approved"
    assert ask(YURI, 3)["state"] == "refused"
    tasks = _tasks(env)
    assert [(t["kind"], t["recipients"]) for t in tasks] == [("quote_authorize", [CAROL]), ("quote_reject", [YURI])]
    for task in tasks:
        _ack(env, task)

    # ``manual``: the request waits for the owner, and no answer goes out.
    policy("manual")
    assert ask(YURI, 4) == {"decision": "accepted", "reason": None, "state": "pending"}
    assert ask(STRANGER, 5)["state"] == "pending"
    listed = c.request("GET", QUOTES, session=env.alpha_session).json()
    assert listed["counts"] == {"pending": 2, "approved": 2}
    assert [(q["quoting_object"], q["quoting_actor"], q["state"], q["object"]["kind"], q["object"]["text"]) for q in listed["requests"]] == [(f"{STRANGER}/notes/5", STRANGER, "pending", "note", "Quote me"), (f"{YURI}/notes/4", YURI, "pending", "note", "Quote me")]
    by_object = {q["quoting_object"]: q["id"] for q in listed["requests"]}
    # A request of another account gives the same answer as no request. A state that the owner cannot set is refused.
    assert c.request("PATCH", f"{QUOTES}/{by_object[f'{YURI}/notes/4']}", session=env.beta_session, json={"state": "approved"}).json() == {"error": "quote_not_found"}
    assert c.request("PATCH", f"{QUOTES}/{by_object[f'{YURI}/notes/4']}", session=env.alpha_session, json={"state": "pending"}).status_code == 400
    assert c.request("PATCH", f"{QUOTES}/{by_object[f'{YURI}/notes/4']}", session=env.alpha_session, json={"state": "revoked"}).json() == {"error": "invalid_state", "state": "pending"}
    # The owner approves one and refuses the other.
    approved = c.request("PATCH", f"{QUOTES}/{by_object[f'{YURI}/notes/4']}", session=env.alpha_session, json={"state": "approved"}).json()["quote"]
    assert (approved["state"], approved["authorization"]) == ("approved", f"{PUBLIC_URL}/ap/quote-authorizations/{approved['id']}")
    assert c.request("PATCH", f"{QUOTES}/{by_object[f'{STRANGER}/notes/5']}", session=env.alpha_session, json={"state": "refused"}).json()["quote"]["state"] == "refused"
    tasks = _tasks(env)
    assert [(t["kind"], t["ref_id"], t["recipients"]) for t in tasks] == [("quote_authorize", approved["id"], [YURI]), ("quote_reject", by_object[f"{STRANGER}/notes/5"], [STRANGER])]
    for task in tasks:
        _ack(env, task)
    assert sc.call("GET", f"/quote-authorizations/{approved['id']}").status_code == 200

    # The owner revokes an authorization. Completion criterion: a revoked authorization gives 410, and the task sends the Delete.
    revoked = c.request("PATCH", f"{QUOTES}/{approved['id']}", session=env.alpha_session, json={"state": "revoked"})
    assert revoked.status_code == 200 and revoked.json()["quote"]["state"] == "revoked"
    gone = sc.call("GET", f"/quote-authorizations/{approved['id']}")
    assert gone.status_code == 410 and gone.json()["error"] == "gone"
    [task] = _tasks(env)
    assert (task["kind"], task["ref_id"], task["recipients"]) == ("quote_revoke", approved["id"], [YURI])
    _ack(env, task)
    assert approved["id"] not in [q["id"] for q in c.request("GET", QUOTES, session=env.alpha_session).json()["authorizations"]]
    # A new request for a revoked authorization gets a Reject, and the row stays revoked.
    assert ask(YURI, 4)["state"] == "revoked"
    assert [t["kind"] for t in _tasks(env)] == ["quote_reject"]

    # Untrusted input: a quoting object on another host, an object path without an object, and a blocked domain.
    assert _quote_event(env, "QuoteRequest", YURI, note["url"], instrument="https://third.example/notes/77")["reason"] == "invalid_object"
    assert _quote_event(env, "QuoteRequest", YURI, f"{PUBLIC_URL}/ap/objects/1", instrument=f"{YURI}/notes/78")["reason"] == "no_object"
    # The quoting actor deletes its object: its request goes.
    before = _client_rows(env.settings, "SELECT COUNT(*) FROM federation_quote_authorizations")[0][0]
    sc.call("POST", "/timeline-events", _event("Delete", STRANGER, None, object_iri=f"{STRANGER}/notes/1", public=None))
    assert _client_rows(env.settings, "SELECT COUNT(*) FROM federation_quote_authorizations")[0][0] == before - 1
    assert sc.call("GET", f"/quote-authorizations/{first_id}").status_code == 404
    c.request("POST", "/api/admin/federation/domain-blocks", session=env.admin, json={"domain": "other.example"})
    assert ask(YURI, 79)["reason"] == "blocked_domain"


def test_e2e_quote_policy_of_a_published_document(env):
    c, sc = env.client, env.sidecar

    def outbox() -> list[tuple]:
        conn = dbm.connect_existing(env.settings.db_file, readonly=True)
        try:
            return [tuple(r) for r in conn.execute("SELECT kind, object_public_id, status FROM federation_outbox ORDER BY id")]
        finally:
            conn.close()

    # The default of a published document is ``manual``: a request waits for the owner.
    [item] = c.request("GET", OBJECTS, session=env.alpha_session).json()["items"]
    assert (item["quote_policy"], item["quotes"]) == ("manual", 0)
    assert sc.call("GET", f"/objects/{OBJECT_ID}").json()["quote_policy"] == "manual"
    assert _quote_event(env, "QuoteRequest", STRANGER, OBJECT, instrument=f"{STRANGER}/notes/1")["state"] == "pending"
    [request] = c.request("GET", QUOTES, session=env.alpha_session).json()["requests"]
    assert (request["object"]["kind"], request["object"]["text"], request["object"]["document_id"]) == ("document", "Published", "doc-published")
    assert _tasks(env) == []

    # A change of the policy and its ``update`` task are in one transaction of claire.db. The same value a second time makes no task.
    for _ in range(2):
        res = c.request("PATCH", f"{OBJECTS}/{OBJECT_ID}", session=env.alpha_session, json={"quote_policy": "public"})
        assert res.status_code == 200 and res.json()["object"] == {"id": str(OBJECT_ID), "kb_id": 11, "document_id": "doc-published", "quote_policy": "public"}, res.text
    assert outbox() == [("update", OBJECT_ID, "pending")]
    assert sc.call("GET", f"/objects/{OBJECT_ID}").json()["quote_policy"] == "public"
    assert _quote_event(env, "QuoteRequest", YURI, OBJECT, instrument=f"{YURI}/notes/1")["state"] == "approved"
    [task] = _tasks(env)
    assert (task["kind"], task["object_iri"]) == ("quote_authorize", OBJECT)
    assert c.request("GET", OBJECTS, session=env.alpha_session).json()["items"][0]["quotes"] == 1

    # Refusals: an object of another account, a value outside the set, and an object that is not public now.
    assert c.request("PATCH", f"{OBJECTS}/{OBJECT_ID}", session=env.beta_session, json={"quote_policy": "nobody"}).json() == {"error": "object_not_found"}
    assert c.request("PATCH", f"{OBJECTS}/{OBJECT_ID}", session=env.alpha_session, json={"quote_policy": "all"}).json() == {"error": "invalid_quote_policy"}
    _db(env.settings, lambda conn: conn.execute("UPDATE knowledge_bases SET is_public=0 WHERE id=11"))
    assert _quote_event(env, "QuoteRequest", YURI, OBJECT, instrument=f"{YURI}/notes/2")["reason"] == "no_object"
    # The authorization of an object that is not public now has no public form.
    assert sc.call("GET", f"/quote-authorizations/{task['ref_id']}").status_code == 404
