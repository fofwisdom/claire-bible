"""End-to-end test of the outbound interactions on the Claire side (FEDERATION_REMOTE_READING_DESIGN.md Sections 5.2 to 5.4, unit FP4-9).

A real app in network mode and the signed internal API of the same files: the owner writes a reply, a new note, an edit, a favourite, a boost, and a delete; the sidecar takes the tasks, reads the note, and reports the resolved mentions. The sidecar side of the same contract (the ``Create`` with ``inReplyTo`` and a ``Mention`` at a local test inbox, and the 410 of a deleted note) is in ``sidecar/fedify/tests/notes_test.ts``.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from claire.api import server
from claire.store import accounts as accounts_store
from claire.store import client_db
from tests.test_e2e_federation_following import _client_rows, _Sidecar
from tests.test_e2e_federation_timeline import CAROL, GROUP, STRANGER, _actor, _event, _object
from tests.test_federation_console import ALPHA_PUBLIC_ID, PUBLIC_URL, _Client, _db, _isolate, _prepare, _session  # noqa: F401  (_isolate is an autouse fixture)

ALPHA_ACTOR = f"{PUBLIC_URL}/ap/actors/{ALPHA_PUBLIC_ID}"
NOTES = "/api/federation/notes"
TIMELINE = "/api/federation/timeline"
PARENT = f"{CAROL}/notes/1"
GROUP_POST = "https://forum.example/users/erin/notes/7"


@pytest.fixture
def env(tmp_path):
    settings, alpha, beta = _prepare(tmp_path)
    _db(settings, lambda conn: conn.execute("UPDATE accounts SET profile_public=1 WHERE id IN (?, ?)", (alpha, beta)))
    app = server.create_app(settings, SimpleNamespace(provider=SimpleNamespace(name="stub")))
    with TestClient(app, base_url=PUBLIC_URL) as tc:
        client = client_db.connect_client_existing(client_db.client_db_path(settings.db_file))
        try:
            # Alpha follows carol (with the handle as the request) and a group.
            client.execute("INSERT INTO federation_following (account_id, request, actor_iri, actor_type, state, created_at, updated_at) VALUES (?, '@carol@remote.example', ?, 'Person', 'accepted', 1, 1)", (alpha, CAROL))
            client.execute("INSERT INTO federation_following (account_id, request, actor_iri, actor_type, state, created_at, updated_at) VALUES (?, ?, ?, 'Group', 'accepted', 1, 1)", (alpha, GROUP, GROUP))
            client.commit()
        finally:
            client.close()
        env = SimpleNamespace(client=_Client(tc), sidecar=_Sidecar(settings), settings=settings, alpha=alpha, beta=beta, admin=_session(settings, 1), alpha_session=_session(settings, alpha), beta_session=_session(settings, beta))
        # A post of carol and a post that the group announced are in the timeline of alpha.
        assert env.sidecar.call("POST", "/timeline-events", _event("Create", CAROL, _object(PARENT, CAROL, published_at=1700000000.0))).json()["items"] == 1
        assert env.sidecar.call("POST", "/timeline-events", _event("Announce", GROUP, _object(GROUP_POST, "https://forum.example/users/erin"))).json()["items"] == 1
        yield env


def _item_id(env, object_iri: str) -> str:
    [(item_id,)] = _client_rows(env.settings, "SELECT id FROM federation_timeline_items WHERE account_id=? AND object_iri=?", (env.alpha, object_iri))
    return str(item_id)


def _tasks(env) -> list[dict]:
    response = env.sidecar.call("GET", "/interaction-tasks?limit=20")
    assert response.status_code == 200, response.text
    return response.json()["tasks"]


def _ack(env, task: dict, *, result: str = "handed_off", error_code: str | None = None, mentions: list[dict] | None = None) -> dict:
    response = env.sidecar.call("POST", f"/interaction-tasks/{task['id']}/ack", {"result": result, "error_code": error_code, "mentions": mentions or []})
    assert response.status_code == 200, response.text
    return response.json()


def test_e2e_reply_edit_and_delete_of_a_note(env):
    c, sc = env.client, env.sidecar
    parent_item = _item_id(env, PARENT)
    # The owner writes a reply with a script, a hashtag, a link, a content warning, and a mention of an actor that Claire does not know.
    body = {"content": "@carol@remote.example Thank you <script>alert(1)</script> #Claire https://example.org/a\n\ncc @dan@other.example", "in_reply_to": parent_item, "summary": "A reply", "language": "ko"}
    res = c.request("POST", NOTES, session=env.alpha_session, json=body)
    assert res.status_code == 201, res.text
    note = res.json()["note"]
    assert (note["in_reply_to"], note["summary"], note["sensitive"], note["language"], note["tags"], note["edited_at"]) == (PARENT, "A reply", True, "ko", ["Claire"], None)
    assert note["mentions"] == ["@carol@remote.example", "@dan@other.example"]
    object_iri = note["url"]
    assert object_iri == f"{PUBLIC_URL}/ap/objects/{note['id']}"

    # Completion criterion (Claire side): the sidecar gets the note with the reply target and the mention of the author of the parent.
    [task] = _tasks(env)
    assert (task["kind"], task["ref_id"], task["actor_public_id"], task["recipients"]) == ("note", note["id"], str(ALPHA_PUBLIC_ID), [])
    record = sc.call("GET", f"/notes/{note['id']}").json()
    assert (record["type"], record["in_reply_to"], record["summary"], record["sensitive"], record["language"], record["audience"]) == ("Note", PARENT, "A reply", True, "ko", None)
    assert record["mentions"] == [{"handle": "@carol@remote.example", "iri": CAROL}, {"handle": "@dan@other.example", "iri": None}]
    assert record["tags"] == [{"name": "#Claire", "href": f"{PUBLIC_URL}/tags/claire"}]
    # RS10: the HTML comes from plain text. The script is text, the known mention and the address are links, and the unknown mention is text.
    assert "<script>" not in record["content"] and "&lt;script&gt;" in record["content"]
    assert f'<a href="{CAROL}" class="u-url mention"' in record["content"] and '<a href="https://example.org/a"' in record["content"]
    assert '<span class="h-card">@dan@other.example</span>' in record["content"]
    # The sidecar resolved the second mention: Claire stores the actor and makes the link.
    assert _ack(env, task, mentions=[{"handle": "@dan@other.example", "iri": "https://other.example/users/dan"}])["status"] == "handed_off"
    record = sc.call("GET", f"/notes/{note['id']}").json()
    assert record["mentions"][1] == {"handle": "@dan@other.example", "iri": "https://other.example/users/dan"}
    assert '<a href="https://other.example/users/dan" class="u-url mention"' in record["content"]
    assert _tasks(env) == []

    # Section 5.4 rule 9: the own note is an item of the thread of the parent, with the note and its counters.
    thread = c.request("GET", f"{TIMELINE}/{parent_item}", session=env.alpha_session).json()["thread"]
    assert [entry["object_iri"] for entry in thread] == [PARENT, object_iri]
    own = thread[1]
    assert (own["unread"], own["in_timeline"], own["author"]["iri"], own["note"]["id"], own["note"]["content"], own["note"]["likes"]) == (False, True, ALPHA_ACTOR, note["id"], note["content"], 0)
    assert thread[0]["note"] is None and thread[0]["favourited"] is False

    # A remote actor likes the note and answers it: the counter and a mention item (Section 6.6).
    like = {"event_id": f"{STRANGER}#likes/1", "type": "Like", "object_type": None, "recipient_public_id": None, "actor": _actor(STRANGER), "object_iri": object_iri, "verified_by": "http-signature"}
    assert sc.call("POST", "/inbox-events", like).json()["decision"] == "accepted"
    answer = f"{STRANGER}/notes/5"
    assert sc.call("POST", "/timeline-events", _event("Create", STRANGER, _object(answer, STRANGER, in_reply_to=object_iri))).json()["items"] == 1
    assert _client_rows(env.settings, "SELECT mention FROM federation_timeline_items WHERE account_id=? AND object_iri=?", (env.alpha, answer)) == [(1,)]
    assert c.request("GET", f"{TIMELINE}/{own['id']}", session=env.alpha_session).json()["item"]["note"]["likes"] == 1

    # Edit (rule 12): the text, the content warning, and the language change; the mention of the parent author stays; the task is update_note.
    res = c.request("PATCH", f"{NOTES}/{note['id']}", session=env.alpha_session, json={"content": "A new text #Other", "summary": None, "language": "en"})
    assert res.status_code == 200, res.text
    edited = res.json()["note"]
    assert (edited["content"], edited["summary"], edited["sensitive"], edited["language"], edited["tags"], edited["likes"]) == ("A new text #Other", None, False, "en", ["Other"], 1)
    assert edited["edited_at"] is not None and edited["mentions"] == ["@carol@remote.example"]
    [task] = _tasks(env)
    assert (task["kind"], task["ref_id"]) == ("update_note", note["id"])
    assert sc.call("GET", f"/notes/{note['id']}").json()["edited_at"] == edited["edited_at"]
    _ack(env, task)
    # A note of another account, and a field outside the model, are refused.
    assert c.request("PATCH", f"{NOTES}/{note['id']}", session=env.beta_session, json={"content": "x"}).json() == {"error": "note_not_found"}
    assert c.request("PATCH", f"{NOTES}/{note['id']}", session=env.alpha_session, json={"in_reply_to": "1"}).status_code == 400

    # The outbox collection has the note (RT7: one number space with the published documents).
    outbox = sc.call("GET", f"/actors/{ALPHA_PUBLIC_ID}/outbox?limit=5").json()
    assert (outbox["total"], [item["id"] for item in outbox["items"]]) == (1, [note["id"]])

    # Delete (rule 7). Completion criterion (Claire side): the note is gone (410), the item goes, and the task has the mentioned actors.
    assert c.request("DELETE", f"{NOTES}/{note['id']}", session=env.beta_session).json() == {"error": "note_not_found"}
    assert c.request("DELETE", f"{NOTES}/{note['id']}", session=env.alpha_session).json() == {"deleted": True, "id": note["id"]}
    gone = sc.call("GET", f"/notes/{note['id']}")
    assert gone.status_code == 410 and gone.json()["error"] == "gone"
    assert _client_rows(env.settings, "SELECT content_text, content_html, summary FROM federation_notes WHERE public_id=?", (int(note["id"]),)) == [("", "", None)]
    assert _client_rows(env.settings, "SELECT 1 FROM federation_timeline_items WHERE object_iri=?", (object_iri,)) == []
    [task] = _tasks(env)
    assert (task["kind"], task["ref_id"], task["recipients"]) == ("delete_note", note["id"], [CAROL])
    _ack(env, task)
    assert sc.call("GET", f"/actors/{ALPHA_PUBLIC_ID}/outbox?limit=5").json()["total"] == 0
    assert c.request("DELETE", f"{NOTES}/{note['id']}", session=env.alpha_session).status_code == 404
    # A new reaction to a deleted note is not counted.
    assert sc.call("POST", "/inbox-events", {**like, "event_id": f"{STRANGER}#likes/2", "actor": _actor(CAROL)}).json()["reason"] == "no_object"


def test_e2e_new_note_group_reply_and_refusals(env):
    c, sc = env.client, env.sidecar
    # A new note is not a reply. The language is the server default when the account has no locale.
    res = c.request("POST", NOTES, session=env.alpha_session, json={"content": "Hello world"})
    assert res.status_code == 201, res.text
    note = res.json()["note"]
    assert (note["in_reply_to"], note["summary"], note["sensitive"], note["mentions"]) == (None, None, False, [])
    assert sc.call("GET", f"/notes/{note['id']}").json()["language"] == note["language"] and note["language"]
    # A reply to a thread of a group also goes to the group (FEP-1b12): the audience is the group that announced the parent.
    res = c.request("POST", NOTES, session=env.alpha_session, json={"content": "To the forum", "in_reply_to": _item_id(env, GROUP_POST)})
    assert res.status_code == 201, res.text
    record = sc.call("GET", f"/notes/{res.json()['note']['id']}").json()
    assert (record["audience"], record["in_reply_to"]) == (GROUP, GROUP_POST)
    # The author of the parent is a mention also when the text does not have the handle.
    assert record["mentions"] == [{"handle": "@erin@forum.example", "iri": "https://forum.example/users/erin"}]

    # Refusals: the length limit of the admin, an empty text, a language that is not a tag, an item of another account, and a mention on a blocked domain.
    c.request("PUT", "/api/admin/federation/reading", session=env.admin, json={"note_max_chars": 500})
    assert c.request("POST", NOTES, session=env.alpha_session, json={"content": "x" * 501}).json() == {"error": "content_too_long", "limit": 500}
    assert c.request("POST", NOTES, session=env.alpha_session, json={"content": "  \n "}).json() == {"error": "empty_content"}
    assert c.request("POST", NOTES, session=env.alpha_session, json={"content": "x", "language": "not a tag"}).json() == {"error": "invalid_language"}
    assert c.request("POST", NOTES, session=env.beta_session, json={"content": "x", "in_reply_to": _item_id(env, PARENT)}).json() == {"error": "item_not_found"}
    c.request("POST", "/api/admin/federation/domain-blocks", session=env.admin, json={"domain": "blocked.example"})
    assert c.request("POST", NOTES, session=env.alpha_session, json={"content": "hi @eve@blocked.example"}).json() == {"error": "blocked_domain"}
    # Rule 11: an account that is not an actor sends no note, and its tasks wait.
    _db(env.settings, lambda conn: conn.execute("UPDATE accounts SET profile_public=0 WHERE id=?", (env.alpha,)))
    assert c.request("POST", NOTES, session=env.alpha_session, json={"content": "x"}).json() == {"error": "not_actor"}
    assert _tasks(env) == []
    assert sc.call("GET", f"/notes/{note['id']}").status_code == 404
    # The account can delete its note; the delete task waits.
    assert c.request("DELETE", f"{NOTES}/{note['id']}", session=env.alpha_session).status_code == 200
    assert sc.call("GET", f"/notes/{note['id']}").status_code == 410
    _db(env.settings, lambda conn: conn.execute("UPDATE accounts SET profile_public=1 WHERE id=?", (env.alpha,)))
    # The note task of the deleted note is refused, and its delete task follows.
    assert [(t["kind"], t["ref_id"] == note["id"]) for t in _tasks(env)] == [("note", False), ("delete_note", True)]
    assert ("note", "rejected", "note_removed") in _client_rows(env.settings, "SELECT kind, status, error_code FROM federation_client_outbox")


def test_e2e_favourite_and_boost_with_undo(env):
    c = env.client
    item = _item_id(env, PARENT)
    path = f"{TIMELINE}/{item}"
    # Favourite and boost: the item records the state, and each makes one task with the author as the recipient.
    assert c.request("POST", f"{path}/favourite", session=env.alpha_session).json() == {"item_id": item, "favourited": True, "boosted": False}
    assert c.request("POST", f"{path}/favourite", session=env.alpha_session).json()["favourited"] is True
    assert c.request("POST", f"{path}/boost", session=env.alpha_session).json() == {"item_id": item, "favourited": True, "boosted": True}
    view = c.request("GET", path, session=env.alpha_session).json()["item"]
    assert (view["favourited"], view["boosted"]) == (True, True)
    like, announce = _tasks(env)
    assert (like["kind"], like["object_iri"], like["recipients"], like["ref_id"]) == ("like", PARENT, [CAROL], str(like["id"]))
    assert (announce["kind"], announce["ref_id"]) == ("announce", str(announce["id"]))
    _ack(env, like)
    _ack(env, announce)
    # The Undo has the number of the activity that it undoes (rule 4).
    assert c.request("DELETE", f"{path}/favourite", session=env.alpha_session).json()["favourited"] is False
    assert c.request("DELETE", f"{path}/boost", session=env.alpha_session).json()["boosted"] is False
    assert c.request("DELETE", f"{path}/boost", session=env.alpha_session).json()["boosted"] is False
    undo_like, undo_announce = _tasks(env)
    assert (undo_like["kind"], undo_like["ref_id"], undo_like["object_iri"]) == ("undo_like", str(like["id"]), PARENT)
    assert (undo_announce["kind"], undo_announce["ref_id"]) == ("undo_announce", str(announce["id"]))
    # A second favourite is a new activity.
    c.request("POST", f"{path}/favourite", session=env.alpha_session)
    [again] = [t for t in _tasks(env) if t["kind"] == "like"]
    assert again["ref_id"] == str(again["id"]) != str(like["id"])
    # The sidecar cannot send it: the item shows no favourite.
    assert _ack(env, again, result="rejected", error_code="actor_not_found")["status"] == "rejected"
    assert c.request("GET", path, session=env.alpha_session).json()["item"]["favourited"] is False

    # Refusals: an item of another account, an own note, and an account that is not an actor.
    assert c.request("POST", f"{path}/favourite", session=env.beta_session).json() == {"error": "item_not_found"}
    own = c.request("POST", NOTES, session=env.alpha_session, json={"content": "mine"}).json()["item_id"]
    assert c.request("POST", f"{TIMELINE}/{own}/boost", session=env.alpha_session).json() == {"error": "own_item"}
    _db(env.settings, lambda conn: conn.execute("UPDATE accounts SET profile_public=0 WHERE id=?", (env.alpha,)))
    assert c.request("POST", f"{path}/boost", session=env.alpha_session).json() == {"error": "not_actor"}
