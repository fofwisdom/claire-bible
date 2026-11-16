"""End-to-end test of the thread fetch on the Claire side (FEDERATION_REMOTE_READING_DESIGN.md Section 6.5 rules 4 to 6, unit FP4-6).

A real app in network mode and the signed internal API of the same files. The owner opens the thread of an item (``POST /api/federation/timeline/{item_id}/context``), the sidecar takes the task (``context-tasks``), gives the objects (``context-objects``), and acknowledges the task; the thread view then has the new items. The sidecar side of the same contract (the fetch with ``@fedify/backfill`` and its limits) is in ``sidecar/fedify/tests/context_test.ts``.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from claire.api import server
from claire.store import accounts as accounts_store
from claire.store import client_db
from claire.store import federation_client as client_store
from tests.test_e2e_federation_following import _client_rows, _Sidecar
from tests.test_e2e_federation_timeline import _event, _object
from tests.test_federation_console import ALPHA_PUBLIC_ID, PUBLIC_URL, _Client, _db, _isolate, _prepare, _session  # noqa: F401  (_isolate is an autouse fixture)

TIMELINE = "/api/federation/timeline"
CAROL = "https://remote.example/users/carol"
ERIN = "https://remote.example/users/erin"
ROOT = f"{CAROL}/notes/1"
CONTEXT = "https://remote.example/contexts/1"


@pytest.fixture
def env(tmp_path):
    settings, alpha, beta = _prepare(tmp_path)
    _db(settings, lambda conn: accounts_store.AccountStore(conn).set_federation_state("on", updated_by=1))
    _db(settings, lambda conn: conn.execute("UPDATE accounts SET profile_public=1 WHERE id IN (?, ?)", (alpha, beta)))
    app = server.create_app(settings, SimpleNamespace(provider=SimpleNamespace(name="stub")))
    with TestClient(app, base_url=PUBLIC_URL) as tc:
        client = client_db.connect_client_existing(client_db.client_db_path(settings.db_file))
        try:
            for account_id in (alpha, beta):
                client.execute("INSERT INTO federation_following (account_id, request, actor_iri, state, created_at, updated_at) VALUES (?, ?, ?, 'accepted', 1, 1)", (account_id, CAROL, CAROL))
            client.commit()
        finally:
            client.close()
        env = SimpleNamespace(client=_Client(tc), sidecar=_Sidecar(settings), settings=settings, alpha=alpha, beta=beta, alpha_session=_session(settings, alpha), beta_session=_session(settings, beta))
        # A post of carol and her reply to it: two items of one thread for alpha and for beta.
        for obj in (_object(ROOT, CAROL, context=CONTEXT), _object(f"{CAROL}/notes/2", CAROL, context=CONTEXT, in_reply_to=ROOT)):
            assert env.sidecar.call("POST", "/timeline-events", _event("Create", CAROL, obj)).json()["items"] == 2
        yield env


def _item_id(env, account_id: int, object_iri: str) -> int:
    return _client_rows(env.settings, "SELECT id FROM federation_timeline_items WHERE account_id=? AND object_iri=?", (account_id, object_iri))[0][0]


def _ask(env, item_id: int, session: str | None = None):
    return env.client.request("POST", f"{TIMELINE}/{item_id}/context", session=session or env.alpha_session)


def _tasks(env) -> list[tuple]:
    return _client_rows(env.settings, "SELECT account_id, kind, target_iri, ref_id, status, error_code FROM federation_client_outbox WHERE kind='fetch_context' ORDER BY id")


def _set_reading(env, **values) -> None:
    _db(env.settings, lambda conn: client_store.set_reading_settings(conn, values, updated_by=1))


def _move_tasks(env, seconds: float) -> None:
    """Make the tasks older, as if the time had moved."""
    conn = client_db.connect_client_existing(client_db.client_db_path(env.settings.db_file))
    try:
        conn.execute("UPDATE federation_client_outbox SET created_at=created_at-?", (seconds,))
        conn.commit()
    finally:
        conn.close()


def test_e2e_thread_fetch_stores_the_replies_and_makes_no_second_task(env):
    reply_id = _item_id(env, env.alpha, f"{CAROL}/notes/2")
    root_id = _item_id(env, env.alpha, ROOT)

    # The owner opens the thread of the reply: one task for the top stored object of the thread.
    res = _ask(env, reply_id)
    assert res.status_code == 202 and res.json() == {"state": "queued", "pending": True}
    assert res.headers["cache-control"] == "no-store"
    assert _tasks(env) == [(env.alpha, "fetch_context", ROOT, reply_id, "pending", None)]
    # A second request for the same thread, also from another item of it, makes no task (completion criterion).
    assert _ask(env, reply_id).json() == {"state": "recent", "pending": True}
    res = _ask(env, root_id)
    assert res.status_code == 200 and res.json() == {"state": "recent", "pending": True}
    assert len(_tasks(env)) == 1
    # The task is not a task of the other client kinds.
    assert env.sidecar.call("GET", "/client-tasks").json() == {"tasks": []}

    # The sidecar takes the task with the limits of the server.
    task = env.sidecar.call("GET", "/context-tasks").json()["tasks"]
    assert [(t["actor_public_id"], t["object_iri"], t["max_items"], t["max_depth"]) for t in task] == [(str(ALPHA_PUBLIC_ID), ROOT, 100, 5)]
    task_id = task[0]["id"]
    # The lease: a second read gives no task.
    assert env.sidecar.call("GET", "/context-tasks").json() == {"tasks": []}

    # The sidecar gives the objects. Claire stores the objects of the thread for the account that asked, outside the timeline.
    erin_reply = _object(f"{ERIN}/notes/5", ERIN, context=CONTEXT, in_reply_to=ROOT)
    nested = _object(f"{ERIN}/notes/6", ERIN, in_reply_to=f"{ERIN}/notes/5")
    objects = [
        nested,
        erin_reply,
        # An object that the account has already: no second item.
        _object(f"{CAROL}/notes/2", CAROL, context=CONTEXT, in_reply_to=ROOT, content_html="<p>other text</p>"),
        # No link to the thread, a body above the limit, and an address of this server: not stored.
        _object(f"{ERIN}/notes/7", ERIN),
        _object(f"{ERIN}/notes/8", ERIN, context=CONTEXT, content_html="x" * (64 * 1024 + 1)),
        _object(f"{PUBLIC_URL}/ap/objects/1", ERIN, context=CONTEXT),
    ]
    res = env.sidecar.call("POST", "/context-objects", {"task_id": task_id, "objects": objects})
    assert res.status_code == 200 and res.json() == {"decision": "accepted", "reason": None, "stored": 2, "refused": 4}
    stored = _client_rows(env.settings, "SELECT account_id, object_iri, in_timeline, unread FROM federation_timeline_items WHERE author_iri=? ORDER BY object_iri", (ERIN,))
    assert stored == [(env.alpha, f"{ERIN}/notes/5", 0, 1), (env.alpha, f"{ERIN}/notes/6", 0, 1)]
    assert _client_rows(env.settings, "SELECT content_html FROM federation_timeline_items WHERE account_id=? AND object_iri=?", (env.alpha, f"{CAROL}/notes/2")) == [("<p>text <script>alert(1)</script></p>",)]
    # The same objects again: nothing new.
    assert env.sidecar.call("POST", "/context-objects", {"task_id": task_id, "objects": [erin_reply]}).json()["stored"] == 0

    assert env.sidecar.call("POST", f"/client-tasks/{task_id}/ack", {"result": "handed_off", "error_code": None, "actor": None}).json() == {"id": task_id, "status": "handed_off"}
    # After the acknowledgement the task takes no object.
    assert env.sidecar.call("POST", "/context-objects", {"task_id": task_id, "objects": []}).status_code == 404

    # The thread view of the owner shows the new items; the timeline does not.
    thread = env.client.request("GET", f"{TIMELINE}/{reply_id}", session=env.alpha_session).json()["thread"]
    assert sorted(entry["object_iri"] for entry in thread) == sorted([ROOT, f"{CAROL}/notes/2", f"{ERIN}/notes/5", f"{ERIN}/notes/6"])
    assert [item["object_iri"] for item in env.client.request("GET", TIMELINE, session=env.alpha_session).json()["items"]] == [f"{CAROL}/notes/2", ROOT]
    # The other account has the same thread and no new item.
    assert len(env.client.request("GET", f"{TIMELINE}/{_item_id(env, env.beta, ROOT)}", session=env.beta_session).json()["thread"]) == 2

    # Within context_refetch_minutes (default 15) no second task is made, also from an item that the fetch brought.
    _move_tasks(env, 14 * 60)
    assert _ask(env, _item_id(env, env.alpha, f"{ERIN}/notes/6")).json() == {"state": "recent", "pending": False}
    assert len(_tasks(env)) == 1
    # After that time a new task is made.
    _move_tasks(env, 2 * 60)
    assert _ask(env, reply_id).json() == {"state": "queued", "pending": True}
    assert [row[4] for row in _tasks(env)] == ["handed_off", "pending"]


def test_e2e_thread_fetch_limits_switch_and_owner_rule(env):
    root_id = _item_id(env, env.alpha, ROOT)
    # Only the owner of the item can ask; an unknown item and no session give 404.
    assert _ask(env, root_id, env.beta_session).status_code == 404
    assert _ask(env, 999999).status_code == 404
    assert env.client.request("POST", f"{TIMELINE}/{root_id}/context").status_code in (401, 403, 404)
    assert _tasks(env) == []

    # context_max_items applies to each request of objects.
    _set_reading(env, context_max_items=2)
    assert _ask(env, root_id).status_code == 202
    task_id = env.sidecar.call("GET", "/context-tasks").json()["tasks"][0]["id"]
    objects = [_object(f"{ERIN}/notes/{n}", ERIN, context=CONTEXT, in_reply_to=ROOT) for n in range(5)]
    assert env.sidecar.call("POST", "/context-objects", {"task_id": task_id, "objects": objects}).json() == {"decision": "accepted", "reason": None, "stored": 2, "refused": 3}
    assert len(_client_rows(env.settings, "SELECT 1 FROM federation_timeline_items WHERE author_iri=?", (ERIN,))) == 2

    # A domain block applies to the author and to the host of the object, as for a Create.
    _db(env.settings, lambda conn: conn.execute("INSERT INTO federation_domain_blocks (domain, created_at, created_by) VALUES ('blocked.example', 1, 1)"))
    blocked = [_object("https://blocked.example/notes/1", "https://blocked.example/users/eve", context=CONTEXT), _object("https://other.example/notes/1", "https://blocked.example/users/eve", context=CONTEXT)]
    assert env.sidecar.call("POST", "/context-objects", {"task_id": task_id, "objects": blocked}).json() == {"decision": "accepted", "reason": None, "stored": 0, "refused": 2}
    # A body that is not valid, and a task of another kind.
    assert env.sidecar.call("POST", "/context-objects", {"task_id": task_id, "objects": [{"object_iri": "x"}]}).status_code == 400
    assert env.sidecar.call("POST", "/context-objects", {"task_id": 0, "objects": []}).status_code == 400

    # The admin sets the fetch to off (rule 6): the objects of a task that waits are refused, a task that waits is rejected when the sidecar asks, and no new task is made.
    _set_reading(env, context_fetch="off")
    assert env.sidecar.call("POST", "/context-objects", {"task_id": task_id, "objects": objects}).json() == {"decision": "rejected", "reason": "context_fetch_off", "stored": 0, "refused": 5}
    assert env.sidecar.call("POST", f"/client-tasks/{task_id}/ack", {"result": "rejected", "error_code": "context_fetch_off", "actor": None}).json()["status"] == "rejected"
    _move_tasks(env, 3600)
    res = _ask(env, root_id)
    assert res.status_code == 200 and res.json() == {"state": "off", "pending": False}
    assert len(_tasks(env)) == 1
    # The limit 0 is the same as off.
    _set_reading(env, context_fetch="on", context_max_items=0)
    assert _ask(env, root_id).json()["state"] == "off"

    # A task that the owner made before the switch is rejected at the lease.
    _set_reading(env, context_max_items=100)
    assert _ask(env, root_id).json()["state"] == "queued"
    _set_reading(env, context_fetch="off")
    assert env.sidecar.call("GET", "/context-tasks").json() == {"tasks": []}
    assert _tasks(env)[-1][4:] == ("rejected", "context_fetch_off")

    # An account that is not an actor makes no task: the sidecar takes no task of such an account.
    _set_reading(env, context_fetch="on")
    _db(env.settings, lambda conn: conn.execute("UPDATE accounts SET profile_public=0 WHERE id=?", (env.beta,)))
    assert _ask(env, _item_id(env, env.beta, ROOT), env.beta_session).json() == {"state": "off", "pending": False}
    assert all(row[0] == env.alpha for row in _tasks(env))
