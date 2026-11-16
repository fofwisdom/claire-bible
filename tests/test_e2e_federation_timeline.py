"""End-to-end test of the inbound posts on the Claire side (FEDERATION_REMOTE_READING_DESIGN.md Sections 6.1 to 6.5, unit FP4-4).

A real app in network mode and the signed internal API of the same files: the sidecar gives timeline events, and Claire makes the items of the accounts in ``client.db``. The sidecar side of the same contract (the normal form, the fetch of an announced object, and the parent chain) is in ``sidecar/fedify/tests/timeline_test.ts``.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from claire.api import server
from claire.store import accounts as accounts_store
from claire.store import client_db
from tests.test_e2e_federation_following import _client_rows, _Sidecar
from tests.test_federation_console import ALPHA_PUBLIC_ID, PUBLIC_URL, _Client, _db, _isolate, _prepare, _session  # noqa: F401  (_isolate is an autouse fixture)

CAROL = "https://remote.example/users/carol"
GROUP = "https://forum.example/c/rust"
ERIN = "https://forum.example/users/erin"
STRANGER = "https://other.example/users/zed"


def _actor(iri: str) -> dict:
    return {"iri": iri, "inbox": f"{iri}/inbox", "shared_inbox": None}


def _profile(iri: str) -> dict:
    return {"iri": iri, "name": iri.rsplit("/", 1)[-1].title(), "handle": f"@{iri.rsplit('/', 1)[-1]}@{iri.split('/')[2]}"}


def _object(iri: str, author: str, **changes) -> dict:
    return {"object_iri": iri, "url": iri, "type": "Note", "author": _profile(author), "name": None, "summary": None, "sensitive": False, "content_html": "<p>text <script>alert(1)</script></p>", "language": "en", "published_at": 1800000000.0, "updated_at": None, "attachments": [], "context": None, "in_reply_to": None, "quote": None, **changes}


def _event(kind: str, actor: str, obj: dict | None = None, *, object_iri: str | None = None, public: bool | None = True, parents: list[dict] | None = None, actor_type: str | None = "Person", mentioned: list[str] | None = None, in_reply_to: str | None = None, n: list[int] = [0]) -> dict:
    n[0] += 1
    return {"event_id": f"{actor}#activities/{n[0]}", "type": kind, "recipient_public_id": None, "actor": _actor(actor), "announced_by": _profile(actor) if kind == "Announce" else None, "object_iri": object_iri or obj["object_iri"], "public": public, "object": obj, "parents": parents or [], "actor_type": actor_type, "mentioned": mentioned or [], "in_reply_to": in_reply_to if obj is None else obj["in_reply_to"], "verified_by": "http-signature"}


@pytest.fixture
def env(tmp_path):
    settings, alpha, beta = _prepare(tmp_path)
    _db(settings, lambda conn: accounts_store.AccountStore(conn).set_federation_state("on", updated_by=1))
    _db(settings, lambda conn: conn.execute("UPDATE accounts SET profile_public=1 WHERE id IN (?, ?)", (alpha, beta)))
    app = server.create_app(settings, SimpleNamespace(provider=SimpleNamespace(name="stub")))
    with TestClient(app, base_url=PUBLIC_URL) as tc:
        # Alpha and beta follow carol. Only alpha follows the group.
        client = client_db.connect_client_existing(client_db.client_db_path(settings.db_file))
        try:
            for account_id, actor in ((alpha, CAROL), (beta, CAROL), (alpha, GROUP)):
                client.execute("INSERT INTO federation_following (account_id, request, actor_iri, state, created_at, updated_at) VALUES (?, ?, ?, 'accepted', 1, 1)", (account_id, actor, actor))
            client.commit()
        finally:
            client.close()
        yield SimpleNamespace(client=_Client(tc), sidecar=_Sidecar(settings), settings=settings, alpha=alpha, beta=beta, admin=_session(settings, 1))


def _send(env, event: dict) -> dict:
    response = env.sidecar.call("POST", "/timeline-events", event)
    assert response.status_code == 200, response.text
    return response.json()


def _items(env, where: str = "1=1", params: tuple = ()) -> list[tuple]:
    return _client_rows(env.settings, f"SELECT account_id, object_iri, in_timeline FROM federation_timeline_items WHERE {where} ORDER BY id", params)


def test_e2e_create_announce_reply_update_and_delete(env):
    note = f"{CAROL}/notes/1"
    # A public Create from a followed actor makes one item for each account that follows the actor. The HTML is stored as it is.
    assert _send(env, _event("Create", CAROL, _object(note, CAROL))) == {"decision": "accepted", "reason": None, "items": 2, "notices": []}
    assert _items(env) == [(env.alpha, note, 1), (env.beta, note, 1)]
    assert _client_rows(env.settings, "SELECT DISTINCT content_html, unread, starred FROM federation_timeline_items") == [("<p>text <script>alert(1)</script></p>", 1, 0)]
    # The same event again makes no second item (the key is the account and the object).
    assert _send(env, _event("Create", CAROL, _object(note, CAROL)))["items"] == 0
    assert len(_items(env)) == 2
    # A followers-only object makes no item, and its content does not come to Claire.
    assert _send(env, _event("Create", CAROL, None, object_iri=f"{CAROL}/notes/private", public=False)) == {"decision": "ignored", "reason": "not_public", "items": 0, "notices": []}
    # An actor that no account follows makes no item.
    assert _send(env, _event("Create", STRANGER, _object(f"{STRANGER}/notes/1", STRANGER)))["reason"] == "not_followed"
    assert len(_items(env)) == 2

    # A group Announce makes one item with the announcer, for the account that follows the group.
    post = "https://forum.example/post/42"
    assert _send(env, _event("Announce", GROUP, _object(post, ERIN, type="Page", name="Forum post")))["items"] == 1
    assert _client_rows(env.settings, "SELECT account_id, object_type, author_iri, announced_by_iri FROM federation_timeline_items WHERE object_iri=?", (post,)) == [(env.alpha, "Page", ERIN, GROUP)]
    # A second Announce of the same object changes nothing.
    assert _send(env, _event("Announce", CAROL, _object(post, ERIN, type="Page")))["items"] == 1  # one new item, for beta
    assert _client_rows(env.settings, "SELECT account_id, announced_by_iri FROM federation_timeline_items WHERE object_iri=? ORDER BY account_id", (post,)) == [(env.alpha, GROUP), (env.beta, CAROL)]

    # A reply to an actor that is not followed is an item of the thread only, and its parents come with it.
    parent, root = f"{STRANGER}/notes/9", f"{STRANGER}/notes/8"
    reply = f"{CAROL}/notes/2"
    parents = [_object(parent, STRANGER, in_reply_to=root), _object(root, STRANGER)]
    assert _send(env, _event("Create", CAROL, _object(reply, CAROL, in_reply_to=parent), parents=parents))["items"] == 2
    assert _items(env, "account_id=? AND object_iri IN (?, ?, ?)", (env.alpha, reply, parent, root)) == [(env.alpha, reply, 0), (env.alpha, parent, 0), (env.alpha, root, 0)]
    # A reply to a followed actor, to the author, and to an item that the account has from a followed actor shows in the timeline.
    assert _send(env, _event("Create", CAROL, _object(f"{CAROL}/notes/3", CAROL, in_reply_to=note)))["items"] == 2
    assert _items(env, "object_iri=?", (f"{CAROL}/notes/3",)) == [(env.alpha, f"{CAROL}/notes/3", 1), (env.beta, f"{CAROL}/notes/3", 1)]
    # A comment that the group announces, to a post of an author that alpha does not follow: thread only.
    assert _send(env, _event("Announce", GROUP, _object("https://forum.example/comment/7", "https://forum.example/users/frank", in_reply_to=post)))["items"] == 1
    assert _items(env, "object_iri=?", ("https://forum.example/comment/7",)) == [(env.alpha, "https://forum.example/comment/7", 0)]

    # Update: the author changes the content of all items of the object. Another actor changes nothing.
    assert _send(env, _event("Update", CAROL, _object(note, CAROL, content_html="<p>edited</p>", updated_at=1800000500.0)))["items"] == 2
    assert _client_rows(env.settings, "SELECT DISTINCT content_html, updated_at FROM federation_timeline_items WHERE object_iri=?", (note,)) == [("<p>edited</p>", 1800000500.0)]
    assert _send(env, _event("Update", STRANGER, _object(note, STRANGER, content_html="<p>forged</p>")))["reason"] == "no_item"
    assert _send(env, _event("Update", CAROL, _object(note, STRANGER)))["reason"] == "invalid_object"

    # Undo(Announce): the item goes for the account that does not follow the author. Delete: only the author deletes.
    assert _send(env, _event("Undo", GROUP, None, object_iri=post, public=None))["items"] == 1
    assert _client_rows(env.settings, "SELECT account_id FROM federation_timeline_items WHERE object_iri=?", (post,)) == [(env.beta,)]
    assert _send(env, _event("Delete", STRANGER, None, object_iri=note, public=None))["reason"] == "no_item"
    assert _send(env, _event("Delete", CAROL, None, object_iri=note, public=None)) == {"decision": "accepted", "reason": None, "items": 2, "notices": []}
    assert _items(env, "object_iri=?", (note,)) == []
    # An Update that makes the object not public deletes the items.
    assert _send(env, _event("Update", CAROL, None, object_iri=f"{CAROL}/notes/3", public=False)) == {"decision": "ignored", "reason": "not_public", "items": 2, "notices": []}


def test_e2e_blocks_limits_and_the_actor_rule(env):
    c = env.client
    # Beta stops being an actor: only alpha gets the item (Section 6.4 rule 2).
    _db(env.settings, lambda conn: conn.execute("UPDATE accounts SET profile_public=0 WHERE id=?", (env.beta,)))
    assert _send(env, _event("Create", CAROL, _object(f"{CAROL}/notes/10", CAROL)))["items"] == 1
    assert [row[0] for row in _items(env)] == [env.alpha]
    assert env.sidecar.call("GET", "/timeline-interest?actor=" + CAROL.replace(":", "%3A").replace("/", "%2F")).json() == {"followed": True, "context_fetch": "on", "context_depth": 5}
    assert env.sidecar.call("GET", "/timeline-interest?actor=" + STRANGER.replace(":", "%3A").replace("/", "%2F")).json()["followed"] is False

    # Limits of Section 6.3: a larger event is refused.
    assert _send(env, _event("Create", CAROL, _object(f"{CAROL}/notes/11", CAROL, content_html="x" * (64 * 1024 + 1)))) == {"decision": "rejected", "reason": "too_large", "items": 0, "notices": []}
    attachment = {"url": "https://remote.example/media/1.png", "media_type": "image/png", "name": None, "width": None, "height": None, "blurhash": None}
    assert _send(env, _event("Create", CAROL, _object(f"{CAROL}/notes/12", CAROL, attachments=[attachment] * 9)))["reason"] == "too_large"
    assert _send(env, _event("Create", CAROL, _object(f"{CAROL}/notes/12", CAROL, attachments=[attachment] * 8)))["items"] == 1

    # A domain block applies to the author and to the host of the object of an Announce, and to the actor.
    c.request("POST", "/api/admin/federation/domain-blocks", session=env.admin, json={"domain": "blocked.example"})
    assert _send(env, _event("Announce", GROUP, _object("https://forum.example/post/50", "https://blocked.example/users/eve")))["reason"] == "blocked_domain"
    assert _send(env, _event("Announce", GROUP, _object("https://sub.blocked.example/post/51", ERIN)))["reason"] == "blocked_domain"
    assert _send(env, _event("Create", "https://blocked.example/users/eve", _object("https://blocked.example/notes/1", "https://blocked.example/users/eve")))["reason"] == "blocked_domain"
    # A blocked parent stops the chain; the reply is stored without it.
    blocked_parent = _object("https://blocked.example/notes/2", "https://blocked.example/users/eve")
    assert _send(env, _event("Create", CAROL, _object(f"{CAROL}/notes/13", CAROL, in_reply_to=blocked_parent["object_iri"]), parents=[blocked_parent]))["items"] == 1
    assert _items(env, "object_iri LIKE 'https://blocked.example/%'") == []
    # An object of this server is not an item of a timeline.
    assert _send(env, _event("Announce", GROUP, _object(f"{PUBLIC_URL}/ap/objects/1", f"{PUBLIC_URL}/ap/actors/{ALPHA_PUBLIC_ID}")))["reason"] == "invalid_object"

    # A body outside the sealed model is refused.
    assert env.sidecar.call("POST", "/timeline-events", {**_event("Create", CAROL, _object(f"{CAROL}/notes/14", CAROL)), "extra": 1}).status_code == 400
    # Federation off: nothing is applied.
    _db(env.settings, lambda conn: accounts_store.AccountStore(conn).set_federation_state("off", updated_by=1))
    assert _send(env, _event("Create", CAROL, _object(f"{CAROL}/notes/15", CAROL)))["reason"] == "federation_off"
