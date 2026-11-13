"""End-to-end test of the follow lifecycle on the Claire side (FEDERATION_REMOTE_READING_DESIGN.md Sections 5.1 to 5.3, unit FP4-3).

A real app in network mode and the signed internal API of the same files: the owner follows a handle, the sidecar takes the task and reports the resolved actor, the remote server accepts, the following collection counts it, the owner unfollows, and a domain block rejects the follows of its domain. The sidecar side of the same contract is in ``sidecar/fedify/tests/client_test.ts``.
"""

from __future__ import annotations

import json
import time
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from claire.api import federation_routes, server
from claire.api.federation_internal import INTERNAL_PREFIX, SIGNATURE_HEADER, TIMESTAMP_HEADER, create_internal_federation_app, ensure_service_key, load_service_key, service_key_path, sign_request
from claire.store import accounts as accounts_store
from claire.store import client_db
from tests.test_federation_console import ALPHA_PUBLIC_ID, PUBLIC_URL, _Client, _db, _isolate, _prepare, _session  # noqa: F401  (_isolate is an autouse fixture)

FOLLOWING = "/api/federation/following"
ALPHA_URL = f"{PUBLIC_URL}/ap/actors/{ALPHA_PUBLIC_ID}"
CAROL = "https://remote.example/users/carol"


class _Sidecar:
    """The calls of the sidecar to the internal API, signed with the service key."""

    def __init__(self, settings) -> None:
        ensure_service_key(service_key_path(settings.data_dir))
        self.key = load_service_key(service_key_path(settings.data_dir))
        self.client = TestClient(create_internal_federation_app(settings, routes=federation_routes.federation_internal_routes(settings)))

    def call(self, method: str, path: str, body: dict | None = None):
        target = f"{INTERNAL_PREFIX}{path}"
        content = b"" if body is None else json.dumps(body).encode()
        ts = str(int(time.time()))
        return self.client.request(method, target, content=content, headers={TIMESTAMP_HEADER: ts, SIGNATURE_HEADER: sign_request(self.key, method, target, ts, content)})


@pytest.fixture
def env(tmp_path):
    settings, alpha, _beta = _prepare(tmp_path)
    # Alpha is an actor: federation on, public profile.
    _db(settings, lambda conn: accounts_store.AccountStore(conn).set_federation_state("on", updated_by=1))
    _db(settings, lambda conn: conn.execute("UPDATE accounts SET profile_public=1 WHERE id=?", (alpha,)))
    app = server.create_app(settings, SimpleNamespace(provider=SimpleNamespace(name="stub")))
    with TestClient(app, base_url=PUBLIC_URL) as tc:
        yield SimpleNamespace(client=_Client(tc), sidecar=_Sidecar(settings), settings=settings, alpha=alpha, admin=_session(settings, 1), alpha_session=_session(settings, alpha))


def _client_rows(settings, sql: str, params: tuple = ()) -> list[tuple]:
    conn = client_db.connect_client_existing(client_db.client_db_path(settings.db_file), readonly=True)
    try:
        return [tuple(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def test_e2e_follow_accept_unfollow_and_domain_block(env):
    c, sc = env.client, env.sidecar
    # The owner follows a handle: 202, a pending follow, and one follow task in the same file.
    res = c.request("POST", FOLLOWING, session=env.alpha_session, json={"target": "@carol@remote.example"})
    assert res.status_code == 202, res.text
    following_id = res.json()["following"]["id"]
    assert res.json()["following"]["state"] == "pending"
    # The same request again gives the same follow and no second task.
    assert c.request("POST", FOLLOWING, session=env.alpha_session, json={"target": "@carol@remote.example"}).status_code == 200
    # A handle on a blocked domain and a value that is not a handle or an IRI are refused.
    c.request("POST", "/api/admin/federation/domain-blocks", session=env.admin, json={"domain": "blocked.example"})
    assert c.request("POST", FOLLOWING, session=env.alpha_session, json={"target": "@eve@blocked.example"}).json()["error"] == "blocked_domain"
    assert c.request("POST", FOLLOWING, session=env.alpha_session, json={"target": "not a handle"}).status_code == 400

    # The sidecar takes the task, sends Follow, and reports the resolved actor.
    tasks = sc.call("GET", "/client-tasks?limit=10").json()["tasks"]
    assert [(t["kind"], t["target"], t["following_id"]) for t in tasks] == [("follow", "@carol@remote.example", following_id)]
    actor = {"iri": CAROL, "type": "Person", "inbox": f"{CAROL}/inbox", "shared_inbox": "https://remote.example/inbox", "name": "Carol"}
    assert sc.call("POST", f"/client-tasks/{tasks[0]['id']}/ack", {"result": "handed_off", "error_code": None, "actor": actor}).json()["status"] == "handed_off"
    item = c.request("GET", FOLLOWING, session=env.alpha_session).json()["items"][0]
    assert (item["actor_iri"], item["display_name"], item["state"]) == (CAROL, "Carol", "pending")
    assert sc.call("GET", f"/actors/{ALPHA_PUBLIC_ID}/following").json() == {"public_id": str(ALPHA_PUBLIC_ID), "total": 0}

    # The remote server accepts the Follow with its ID: the follow is accepted and counted.
    event = {"event_id": f"{CAROL}#accepts/1", "type": "Accept", "object_type": None, "recipient_public_id": str(ALPHA_PUBLIC_ID), "actor": {"iri": CAROL, "inbox": f"{CAROL}/inbox", "shared_inbox": "https://remote.example/inbox"}, "object_iri": f"{ALPHA_URL}#follows/{following_id}", "verified_by": "http-signature"}
    assert sc.call("POST", "/inbox-events", event).json() == {"decision": "accepted", "reply": None, "reason": None}
    assert c.request("GET", FOLLOWING, session=env.alpha_session).json()["items"][0]["state"] == "accepted"
    assert sc.call("GET", f"/actors/{ALPHA_PUBLIC_ID}/following").json()["total"] == 1
    # An Accept from a different actor for the same follow changes nothing.
    other = {**event, "event_id": "https://other.example/users/x#accepts/1", "actor": {"iri": "https://other.example/users/x", "inbox": "https://other.example/users/x/inbox", "shared_inbox": None}}
    assert sc.call("POST", "/inbox-events", other).json()["decision"] == "ignored"

    # Unfollow: the row goes, and an undo task refers to the same follow.
    assert c.request("DELETE", f"{FOLLOWING}/{following_id}", session=env.alpha_session).json() == {"removed": True}
    assert c.request("GET", FOLLOWING, session=env.alpha_session).json()["total"] == 0
    tasks = sc.call("GET", "/client-tasks?limit=10").json()["tasks"]
    assert [(t["kind"], t["target"], t["following_id"]) for t in tasks] == [("undo_follow", CAROL, following_id)]

    # A new domain block rejects the follows of the domain and makes undo tasks (Section 5.1 rule 4).
    res = c.request("POST", FOLLOWING, session=env.alpha_session, json={"target": "@dan@other.example"})
    dan_id = res.json()["following"]["id"]
    dan_task = sc.call("GET", "/client-tasks?limit=10").json()["tasks"][-1]
    sc.call("POST", f"/client-tasks/{dan_task['id']}/ack", {"result": "handed_off", "error_code": None, "actor": {**actor, "iri": "https://other.example/users/dan", "inbox": "https://other.example/users/dan/inbox", "shared_inbox": None, "name": "Dan"}})
    c.request("POST", "/api/admin/federation/domain-blocks", session=env.admin, json={"domain": "other.example"})
    assert _client_rows(env.settings, "SELECT state FROM federation_following WHERE id=?", (int(dan_id),)) == [("rejected",)]
    assert ("undo_follow", "https://other.example/users/dan") in _client_rows(env.settings, "SELECT kind, target_iri FROM federation_client_outbox WHERE status='pending'")


def test_only_an_actor_can_follow_and_the_limit_applies(env):
    c = env.client
    _db(env.settings, lambda conn: conn.execute("UPDATE accounts SET profile_public=0 WHERE id=?", (env.alpha,)))
    assert c.request("POST", FOLLOWING, session=env.alpha_session, json={"target": "@carol@remote.example"}).json() == {"error": "not_actor"}
    _db(env.settings, lambda conn: conn.execute("UPDATE accounts SET profile_public=1 WHERE id=?", (env.alpha,)))
    c.request("PUT", "/api/admin/federation/reading", session=env.admin, json={"following_limit": 1})
    assert c.request("POST", FOLLOWING, session=env.alpha_session, json={"target": "@carol@remote.example"}).status_code == 202
    assert c.request("POST", FOLLOWING, session=env.alpha_session, json={"target": "@dan@remote.example"}).json() == {"error": "following_limit", "limit": 1}


def test_removal_of_a_follower_makes_a_reject_task(env):
    from claire.store import federation as federation_store

    _db(env.settings, lambda conn: federation_store.upsert_follower(conn, account_id=env.alpha, actor_iri=CAROL, inbox_iri=f"{CAROL}/inbox", shared_inbox_iri=None, salt=accounts_store.AccountStore(conn).id_salt(), now=1.0))
    assert env.client.request("DELETE", "/api/federation/followers", session=env.alpha_session, json={"actor_iri": CAROL}).status_code == 200
    assert _client_rows(env.settings, "SELECT kind, target_iri FROM federation_client_outbox") == [("reject_follow", CAROL)]
