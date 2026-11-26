"""Opinions of published objects (DOCUMENT_RENDITIONS_AND_PUBLICATION_DESIGN.md Section 5.11 B, C, and E, phases E7a, E7c, and E8, contract 1.23, client.db version 11).

The interface between Claire and the sidecar (each endpoint agrees with its contract fixture; the sidecar tests read the same files), the rules of the store (which posts are opinions, and what a 404 does), the remote ``Delete`` of an opinion, and the scheduled run (the opinions of a gone object, and the daily refresh).
"""

from __future__ import annotations

from typing import Any

from claire.federation import opinions as opinions_mod
from claire.store import client_db
from claire.store import federation_opinions as opinions_store
from claire.store import db as dbm
from tests.test_federation_routes import GONE_OBJECT_ID, NOW, OBJECT_ID, _isolate, internal  # noqa: F401  (pytest fixtures)
from tests.typing_helpers import need

ORIGIN = "https://claire.example.com"
OBJECT = f"{ORIGIN}/ap/objects/{OBJECT_ID}"
CAROL = "https://remote.example/users/carol/notes/9"
ERIN = "https://other.example/users/erin/notes/3"
DAN_QUOTE = "https://remote.example/users/dan/notes/5"
OUTSIDE = "https://other.example/users/erin/notes/4"
FIXTURES = ["opinion_tasks", "opinion_objects"]


def post(iri: str, *, in_reply_to: str | None = None, quote: str | None = None, text: str = "A reply", published: float = NOW - 100.0) -> dict[str, Any]:
    """A post in the normal form of the timeline (FEDERATION_REMOTE_READING_DESIGN.md Section 6.3)."""
    author = iri.rsplit("/notes/", 1)[0]
    return {"object_iri": iri, "url": iri, "type": "Note", "author": {"iri": author, "name": author.rsplit("/", 1)[1].title(), "handle": f"@{author.rsplit('/', 1)[1]}@{author.split('/')[2]}"}, "name": None, "summary": None, "sensitive": False, "content_html": f"<p>{text} <a href=\"https://example.org/x\">link</a></p><script>x</script>", "language": "en", "published_at": published, "updated_at": None, "attachments": [], "context": None, "in_reply_to": in_reply_to, "quote": quote}


def _client(internal):  # noqa: F811
    return client_db.connect_client_existing(client_db.client_db_path(internal.settings.db_file))


def _seed(internal) -> None:  # noqa: F811
    """A delivered reply of carol, an approved quote of dan, and a fetch that waits."""
    client = _client(internal)
    try:
        client.execute("INSERT INTO federation_timeline_items (account_id, object_iri, object_type, author_iri, author_json, content_html, in_reply_to_iri, received_at) VALUES (?, ?, 'Note', 'https://remote.example/users/carol', '{}', '', ?, ?)", (internal.alice, CAROL, OBJECT, NOW - 200.0))
        client.execute("INSERT INTO federation_quote_authorizations (account_id, object_public_id, quoting_object_iri, quoting_actor_iri, request_iri, state, created_at, updated_at) VALUES (?, ?, ?, 'https://remote.example/users/dan', 'https://remote.example/requests/1', 'approved', ?, ?)", (internal.alice, OBJECT_ID, DAN_QUOTE, NOW - 150.0, NOW - 150.0))
        assert opinions_store.request_fetch(client, OBJECT, own=True, object_published=NOW - 3600.0, now=NOW - 10.0) is not None
        client.commit()
    finally:
        client.close()


def test_opinion_endpoints_agree_with_the_fixtures_and_store_the_opinions(internal):  # noqa: F811
    _seed(internal)
    for name in FIXTURES:
        internal.assert_fixture(name)
    client = _client(internal)
    try:
        stored = opinions_store.opinions_of(client, OBJECT)
        assert [(o.opinion_iri, o.parent_iri, o.kind, o.source) for o in stored] == [(CAROL, OBJECT, "reply", "delivered"), (DAN_QUOTE, OBJECT, "quote", "fetched"), (ERIN, CAROL, "reply", "fetched")]
        fetch = need(opinions_store.get_fetch(client, OBJECT))
        assert (fetch.status, fetch.result_count) == ("finished", 3)
    finally:
        client.close()
    # The replies collection lists the fetched direct reply once, and no post that is not an opinion of the object.
    page = internal.get(f"/objects/{OBJECT_ID}/collections/replies").json()["document"]
    assert page["totalItems"] == 1 and page["first"]["items"] == [] and internal.get(f"/objects/{OBJECT_ID}/collections/replies?page=true").json()["document"]["items"] == [CAROL]
    # A later call for a fetch that does not wait has no answer.
    assert internal.call({"method": "POST", "path": "/internal/federation/v1/opinion-objects", "body": {"task_id": 1, "objects": [], "gone": [], "done": True}}).status_code == 404


def test_a_gone_opinion_is_forgotten_its_replies_stay_and_a_hidden_one_is_not_listed(internal):  # noqa: F811
    _seed(internal)
    client = _client(internal)
    try:
        assert opinions_store.store_objects(client, OBJECT, [post(CAROL, in_reply_to=OBJECT), post(ERIN, in_reply_to=CAROL)], quoting=set(), delivered=set(), now=NOW) == 2
        # A post that replies to no opinion of the object, and a post that quotes another object, are not opinions.
        assert opinions_store.store_objects(client, OBJECT, [post(OUTSIDE, in_reply_to="https://other.example/x"), post(DAN_QUOTE, quote="https://other.example/y")], quoting=set(), delivered=set(), now=NOW) == 0
        # A quote post of the object is an opinion; a reply to the quote post is not (Section 5.11 B4).
        assert opinions_store.store_objects(client, OBJECT, [post(DAN_QUOTE, quote=OBJECT), post(OUTSIDE, in_reply_to=DAN_QUOTE)], quoting=set(), delivered=set(), now=NOW) == 1
        assert opinions_store.forget(client, OBJECT, [DAN_QUOTE]) == 1
        # A second read of the same post changes its row and makes no new one.
        assert opinions_store.store_objects(client, OBJECT, [post(CAROL, in_reply_to=OBJECT, text="Changed")], quoting=set(), delivered=set(), now=NOW + 1) == 0
        assert "Changed" in next(o for o in opinions_store.opinions_of(client, OBJECT) if o.opinion_iri == CAROL).content_html
        # A 404 of carol: her row goes away and the reply of erin hangs under the object.
        assert opinions_store.forget(client, OBJECT, [CAROL]) == 1
        assert [(o.opinion_iri, o.parent_iri) for o in opinions_store.opinions_of(client, OBJECT)] == [(ERIN, OBJECT)]
        assert opinions_store.hide(client, OBJECT, ERIN, hidden_by=1, hidden=True, now=NOW) is True
        assert opinions_store.opinions_of(client, OBJECT) == []
        client.commit()
    finally:
        client.close()
    # erin hangs under the object now, but she is hidden: not listed. The delivered reply of carol stays in the timeline until its Delete comes.
    assert internal.get(f"/objects/{OBJECT_ID}/collections/replies?page=true").json()["document"]["items"] == [CAROL]


def test_a_fetch_is_due_only_for_an_object_older_than_five_minutes_and_after_fifteen_minutes(internal):  # noqa: F811
    client = _client(internal)
    try:
        assert opinions_store.request_fetch(client, OBJECT, own=True, object_published=NOW - 60.0, now=NOW) is None
        first = need(opinions_store.request_fetch(client, OBJECT, own=True, object_published=NOW - 600.0, now=NOW))
        # A fetch that waits is the answer of the next request; no second row.
        assert need(opinions_store.request_fetch(client, OBJECT, own=True, object_published=NOW - 600.0, now=NOW + 5)).id == first.id
        opinions_store.finish(client, first.id, result_count=0, now=NOW + 10)
        assert opinions_store.request_fetch(client, OBJECT, own=True, object_published=NOW - 600.0, now=NOW + 60) is None
        again = need(opinions_store.request_fetch(client, OBJECT, own=True, object_published=NOW - 600.0, now=NOW + opinions_store.FETCH_INTERVAL_SECONDS + 1))
        assert again.status == "pending"
        # A fetch that the sidecar leased three times without an end fails.
        for step in range(opinions_store.MAX_ATTEMPTS):
            assert len(opinions_store.lease(client, now=NOW + 2000 + step * 400, limit=5)) == 1
        assert opinions_store.lease(client, now=NOW + 4000, limit=5) == []
        assert need(opinions_store.get_fetch(client, OBJECT)).status == "failed"
    finally:
        client.close()


def test_the_text_of_a_remote_post_is_text_and_links_only() -> None:
    """Section 5.11 D2: no markup, image, or style of a remote server enters the page."""
    html = '<p>Hello <b>bold</b> <a href="https://a.example/x" onclick="x()">site</a><br>next line</p><p><img src="https://a.example/i.png"> <a href="javascript:alert(1)">bad</a><script>alert(1)</script></p><ul><li>one</li><li>two</li></ul>'
    assert opinions_mod.text_segments(html) == [
        [{"text": "Hello bold "}, {"text": "site", "href": "https://a.example/x"}, {"text": "\n"}, {"text": "next line"}],
        [{"text": " bad"}],
        [{"text": "one"}],
        [{"text": "two"}],
    ]


def _delete_event(actor: str, object_iri: str) -> dict[str, Any]:
    """The timeline event of a remote ``Delete`` (the form of the fixture ``timeline_event_delete``)."""
    return {"method": "POST", "path": "/internal/federation/v1/timeline-events", "body": {"event_id": f"{actor}#delete/1", "type": "Delete", "recipient_public_id": None, "actor": {"iri": actor, "inbox": f"{actor}/inbox", "shared_inbox": None}, "announced_by": None, "object_iri": object_iri, "public": None, "object": None, "parents": [], "actor_type": "Person", "mentioned": [], "in_reply_to": None, "verified_by": "http-signature"}}


def test_a_remote_delete_of_the_author_removes_the_opinion_and_keeps_its_replies(internal):  # noqa: F811
    client = _client(internal)
    try:
        assert opinions_store.store_objects(client, OBJECT, [post(CAROL, in_reply_to=OBJECT), post(ERIN, in_reply_to=CAROL)], quoting=set(), delivered=set(), now=NOW) == 2
        client.commit()
    finally:
        client.close()
    # Only the author deletes: a Delete of another actor changes nothing.
    assert internal.call(_delete_event("https://remote.example/users/dan", CAROL)).json()["decision"] == "ignored"
    answer = internal.call(_delete_event("https://remote.example/users/carol", CAROL)).json()
    assert (answer["decision"], answer["items"]) == ("accepted", 1)
    client = _client(internal)
    try:
        assert [(o.opinion_iri, o.parent_iri) for o in opinions_store.opinions_of(client, OBJECT)] == [(ERIN, OBJECT)]
    finally:
        client.close()


def test_the_run_deletes_the_opinions_of_a_gone_object_only(internal):  # noqa: F811
    from claire.federation import opinion_scheduler

    gone = f"{ORIGIN}/ap/objects/{GONE_OBJECT_ID}"
    client = _client(internal)
    try:
        assert opinions_store.store_objects(client, OBJECT, [post(CAROL, in_reply_to=OBJECT)], quoting=set(), delivered=set(), now=NOW) == 1
        assert opinions_store.store_objects(client, gone, [post(ERIN, in_reply_to=gone)], quoting=set(), delivered=set(), now=NOW) == 1
        assert opinions_store.request_fetch(client, gone, own=True, object_published=NOW - 3600.0, now=NOW) is not None
        client.commit()
    finally:
        client.close()
    assert opinion_scheduler.run_once(internal.settings, now=NOW)["removed"] == 1
    client = _client(internal)
    conn = dbm.connect_existing(internal.settings.db_file)
    try:
        assert opinions_store.object_iris(client) == [OBJECT]
        assert opinions_mod.purge_gone_objects(conn, client, origin=ORIGIN) == 0
    finally:
        conn.close()
        client.close()


def test_the_daily_refresh_asks_for_the_objects_with_activity_in_seven_days(internal):  # noqa: F811
    from claire.federation import opinion_scheduler
    from tests.test_federation_routes import SECOND_OBJECT_ID

    # Three days after the publication: a delivered reply to the object two days ago, and a like of the second object eight days ago.
    at = NOW + 3 * 86400
    client = _client(internal)
    try:
        client.execute("INSERT INTO federation_timeline_items (account_id, object_iri, object_type, author_iri, author_json, content_html, in_reply_to_iri, received_at) VALUES (?, ?, 'Note', 'https://remote.example/users/carol', '{}', '', ?, ?)", (internal.alice, CAROL, OBJECT, at - 2 * 86400))
        client.execute("INSERT INTO federation_object_reactions (object_public_id, kind, actor_iri, activity_iri, account_id, received_at) VALUES (?, 'like', 'https://remote.example/users/dan', 'https://remote.example/likes/1', ?, ?)", (SECOND_OBJECT_ID, internal.alice, at - 8 * 86400))
        client.commit()
    finally:
        client.close()
    assert opinion_scheduler.run_once(internal.settings, now=at) == {"removed": 0, "refreshed": 1}
    client = _client(internal)
    try:
        assert [(f.object_iri, f.status, f.own) for f in (opinions_store.get_fetch(client, iri) for iri in (OBJECT, f"{ORIGIN}/ap/objects/{SECOND_OBJECT_ID}")) if f is not None] == [(OBJECT, "pending", True)]
        opinions_store.finish(client, need(opinions_store.get_fetch(client, OBJECT)).id, result_count=0, now=at + 60)
        client.commit()
    finally:
        client.close()
    # One refresh in one day: a run one hour later asks for nothing, a run one day later asks again.
    assert opinion_scheduler.run_once(internal.settings, now=at + 3600)["refreshed"] == 0
    assert opinion_scheduler.run_once(internal.settings, now=at + 86400)["refreshed"] == 1


def test_version_12_adds_the_attachments_to_a_table_of_version_11(tmp_path):
    import sqlite3

    conn = sqlite3.connect(tmp_path / "client.db")
    try:
        # The table of version 11, with one row.
        conn.execute("CREATE TABLE federation_opinions (object_iri TEXT NOT NULL, opinion_iri TEXT NOT NULL, parent_iri TEXT NOT NULL, kind TEXT NOT NULL, source TEXT NOT NULL, author_iri TEXT NOT NULL, author_name TEXT, author_handle TEXT, url TEXT, content_html TEXT NOT NULL, language TEXT, summary TEXT, sensitive INTEGER NOT NULL DEFAULT 0, published REAL, updated REAL, fetched_at REAL NOT NULL, hidden_at REAL, hidden_by INTEGER, PRIMARY KEY (object_iri, opinion_iri))")
        conn.execute("INSERT INTO federation_opinions (object_iri, opinion_iri, parent_iri, kind, source, author_iri, content_html, fetched_at) VALUES (?, ?, ?, 'reply', 'fetched', 'https://remote.example/users/carol', '<p>x</p>', 1.0)", (OBJECT, CAROL, OBJECT))
        opinions_store.add_attachments_column(conn)
        opinions_store.add_attachments_column(conn)
        assert [o.attachments for o in opinions_store.opinions_of(conn, OBJECT)] == [[]]
    finally:
        conn.close()


def test_opinions_view_includes_multi_hop_local_replies_up_to_depth_eight(internal):  # noqa: F811
    client = _client(internal)
    conn = dbm.connect_existing(internal.settings.db_file)
    try:
        parent = OBJECT
        for hop in range(1, 11):
            pub = 7000 + hop
            client.execute("INSERT INTO federation_notes (public_id, account_id, in_reply_to_iri, content_text, content_html, mentions_json, published_at, updated_at) VALUES (?, ?, ?, ?, ?, '[]', ?, ?)", (pub, internal.alice, parent, f"Hop {hop}", f"<p>Hop {hop}</p>", NOW + hop, NOW + hop))
            parent = f"{ORIGIN}/ap/objects/{pub}"
        # Hide an opinion and add a local reply to it: the hidden child must stay hidden.
        opinions_store.store_objects(client, OBJECT, [post(CAROL, in_reply_to=OBJECT)], quoting=set(), delivered=set(), now=NOW)
        opinions_store.hide(client, OBJECT, CAROL, hidden_by=internal.alice, hidden=True, now=NOW)
        client.execute("INSERT INTO federation_notes (public_id, account_id, in_reply_to_iri, content_text, content_html, mentions_json, published_at, updated_at) VALUES (7099, ?, ?, 'Under hidden', '<p>Under hidden</p>', '[]', ?, ?)", (internal.alice, CAROL, NOW + 99, NOW + 99))
        client.commit()
        view = opinions_mod.opinions_view(conn, client, object_iri=OBJECT, object_public_id=OBJECT_ID, origin=ORIGIN, actor_ids={internal.alice}, now=NOW + 200)
        iris = [item["iri"] for item in view["opinions"]]
        assert iris == [f"{ORIGIN}/ap/objects/{7000 + h}" for h in range(1, 9)]
        assert f"{ORIGIN}/ap/objects/7099" not in iris
    finally:
        conn.close()
        client.close()


def test_rendition_replies_and_self_or_hidden_reactions(internal):  # noqa: F811
    from claire.store import document_renditions as renditions_store
    from claire.store import federation as federation_store
    from tests.test_federation_routes import ALICE_IRI, ALICE_THEME_ID, BOB_IRI, SECOND_OBJECT_ID

    conn = dbm.connect_existing(internal.settings.db_file)
    try:
        renditions_store.record_primary(conn, "doc-published", ALICE_THEME_ID, lineage={}, now=NOW - 100)
        renditions_store.store_additional(conn, "doc-published", ALICE_THEME_ID, template="concise", detail="= 간결\n\nShort", lineage={}, now=NOW - 90)
        conn.commit()
        rendition_id = federation_store.rendition_objects_of(conn, OBJECT_ID)[0].public_id
    finally:
        conn.close()
    rendition_iri = f"{ORIGIN}/ap/objects/{rendition_id}"

    # Non-follower remote reply to a non-primary rendition object (no explicit mention) triggers mention, timeline item, notification, and opinion discovery.
    reply_iri = "https://remote.example/users/stranger/notes/77"
    reply_event = {
        "method": "POST",
        "path": "/internal/federation/v1/timeline-events",
        "body": {
            "event_id": "https://remote.example/activities/r77",
            "type": "Create",
            "recipient_public_id": None,
            "actor": {"iri": "https://remote.example/users/stranger", "inbox": "https://remote.example/users/stranger/inbox", "shared_inbox": None},
            "announced_by": None,
            "object_iri": reply_iri,
            "public": True,
            "object": {**post(reply_iri, in_reply_to=rendition_iri), "quote": None},
            "parents": [],
            "actor_type": "Person",
            "mentioned": [],
            "in_reply_to": rendition_iri,
            "verified_by": "http-signature",
        },
    }
    res = internal.call(reply_event)
    assert res.status_code == 200 and res.json() == {"decision": "accepted", "reason": None, "items": 1, "notices": []}
    client = _client(internal)
    try:
        assert [(r[0], r[1], r[2]) for r in client.execute("SELECT in_timeline, mention, in_reply_to_iri FROM federation_timeline_items WHERE account_id=? AND object_iri=?", (internal.alice, reply_iri))] == [(1, 1, rendition_iri)]
        assert [r[0] for r in client.execute("SELECT kind FROM notifications WHERE account_id=?", (internal.alice,))] == ["ap_mention"]
        assert opinions_store.request_fetch(client, rendition_iri, own=True, object_published=NOW - 3600, now=NOW) is not None
        client.commit()
    finally:
        client.close()
    tasks = internal.get("/opinion-tasks").json()["tasks"]
    assert any(t["object_iri"] == rendition_iri and reply_iri in t["start"] for t in tasks)

    # Self-like on own document object and own note are ignored without storing reactions; Announce from owner is still accepted.
    own_note_id = 816412345678901255
    client = _client(internal)
    try:
        client.execute("INSERT INTO federation_notes (public_id, account_id, content_text, content_html, published_at, updated_at) VALUES (?, ?, 'hi', '<p>hi</p>', ?, ?)", (own_note_id, internal.alice, NOW, NOW))
        client.commit()
    finally:
        client.close()

    def _inbox_like(actor_iri: str, target_iri: str, kind: str = "Like"):
        return internal.call({"method": "POST", "path": "/internal/federation/v1/inbox-events", "body": {"event_id": f"{actor_iri}#act/{kind}/{target_iri[-4:]}", "type": kind, "object_type": None, "recipient_public_id": None, "actor": {"iri": actor_iri, "inbox": f"{actor_iri}/inbox", "shared_inbox": None}, "object_iri": target_iri, "verified_by": "http-signature"}})

    assert _inbox_like(ALICE_IRI, OBJECT).json() == {"decision": "ignored", "reply": None, "reason": None}
    assert _inbox_like(ALICE_IRI, f"{ORIGIN}/ap/objects/{own_note_id}").json() == {"decision": "ignored", "reply": None, "reason": None}
    assert _inbox_like(ALICE_IRI, OBJECT, kind="Announce").json() == {"decision": "accepted", "reply": None, "reason": None}

    # Hidden document rejects new reactions with no_object.
    conn = dbm.connect_existing(internal.settings.db_file)
    try:
        conn.execute("UPDATE documents SET hidden=1 WHERE id='doc-second'")
        conn.commit()
    finally:
        conn.close()
    assert _inbox_like(BOB_IRI, f"{ORIGIN}/ap/objects/{SECOND_OBJECT_ID}").json() == {"decision": "ignored", "reply": None, "reason": "no_object"}


def test_opinions_item_access_control_and_note_id_collision_guard(internal, monkeypatch):  # noqa: F811
    from types import SimpleNamespace
    from starlette.testclient import TestClient
    from claire.api.server import create_app
    from claire.core import ids
    from claire.store import accounts as accounts_store
    from tests.test_federation_routes import SECOND_OBJECT_ID

    rendition_id = 816412345678901266
    second_obj_iri = f"{ORIGIN}/ap/objects/{SECOND_OBJECT_ID}"
    conn = dbm.connect_existing(internal.settings.db_file)
    try:
        conn.execute("INSERT INTO federation_rendition_objects (public_id, parent_public_id, template, created_at, updated_at) VALUES (?, ?, 'concise', ?, ?)", (rendition_id, OBJECT_ID, NOW, NOW))
        alice_session = accounts_store.create_account_session(conn, account_id=internal.alice, scope="owner", now=NOW)
        conn.commit()
    finally:
        conn.close()

    client = _client(internal)
    try:
        # Local notes: n_valid -> OBJECT, n_chain_16 -> ... -> n_valid, n_too_deep (17 hops), n_unrelated -> None, n_hidden -> OBJECT (also hidden in federation_opinions), n_under_hidden -> n_hidden, n_cycle_a <-> n_cycle_b.
        n_valid = 7100
        client.execute("INSERT INTO federation_notes (public_id, account_id, in_reply_to_iri, content_text, content_html, published_at, updated_at) VALUES (?, ?, ?, 'valid', '<p>valid</p>', ?, ?)", (n_valid, internal.alice, OBJECT, NOW, NOW))
        prev = f"{ORIGIN}/ap/objects/{n_valid}"
        for idx in range(1, 17):
            nid = 7100 + idx
            client.execute("INSERT INTO federation_notes (public_id, account_id, in_reply_to_iri, content_text, content_html, published_at, updated_at) VALUES (?, ?, ?, 'hop', '<p>hop</p>', ?, ?)", (nid, internal.alice, prev, NOW, NOW))
            prev = f"{ORIGIN}/ap/objects/{nid}"
        n_unrelated = 7200
        client.execute("INSERT INTO federation_notes (public_id, account_id, in_reply_to_iri, content_text, content_html, published_at, updated_at) VALUES (?, ?, NULL, 'unrelated', '<p>unrelated</p>', ?, ?)", (n_unrelated, internal.alice, NOW, NOW))
        n_hidden = 7201
        hidden_iri = f"{ORIGIN}/ap/objects/{n_hidden}"
        client.execute("INSERT INTO federation_notes (public_id, account_id, in_reply_to_iri, content_text, content_html, published_at, updated_at) VALUES (?, ?, ?, 'hidden note', '<p>hidden</p>', ?, ?)", (n_hidden, internal.alice, OBJECT, NOW, NOW))
        opinions_store.store_objects(client, OBJECT, [post(hidden_iri, in_reply_to=OBJECT)], quoting=set(), delivered=set(), now=NOW)
        opinions_store.hide(client, OBJECT, hidden_iri, hidden_by=internal.alice, hidden=True, now=NOW)
        n_under_hidden = 7202
        client.execute("INSERT INTO federation_notes (public_id, account_id, in_reply_to_iri, content_text, content_html, published_at, updated_at) VALUES (?, ?, ?, 'under hidden', '<p>under</p>', ?, ?)", (n_under_hidden, internal.alice, hidden_iri, NOW, NOW))
        client.execute("INSERT INTO federation_notes (public_id, account_id, in_reply_to_iri, content_text, content_html, published_at, updated_at) VALUES (7203, ?, ?, 'cyc a', '<p>a</p>', ?, ?)", (internal.alice, f"{ORIGIN}/ap/objects/7204", NOW, NOW))
        client.execute("INSERT INTO federation_notes (public_id, account_id, in_reply_to_iri, content_text, content_html, published_at, updated_at) VALUES (7204, ?, ?, 'cyc b', '<p>b</p>', ?, ?)", (internal.alice, f"{ORIGIN}/ap/objects/7203", NOW, NOW))
        client.commit()
    finally:
        client.close()

    with TestClient(create_app(settings=internal.settings, service=SimpleNamespace(provider=SimpleNamespace(name="stub"))), base_url=ORIGIN, follow_redirects=False) as http:
        headers = {"Cookie": f"claire_session={alice_session}", "Origin": ORIGIN}
        # Allowed: target object itself, direct local note, 15-hop local note (16 steps including direct parent -> OBJECT).
        assert http.post("/api/federation/opinions/item", json={"object": OBJECT, "opinion": OBJECT}, headers=headers).status_code == 200
        assert http.post("/api/federation/opinions/item", json={"object": OBJECT, "opinion": f"{ORIGIN}/ap/objects/{n_valid}"}, headers=headers).status_code == 200
        assert http.post("/api/federation/opinions/item", json={"object": OBJECT, "opinion": f"{ORIGIN}/ap/objects/7115"}, headers=headers).status_code == 200

        # Refused with 404 opinion_not_found: >16 hops, unrelated note, unrelated published document, hidden opinion (must not fall through), reply under hidden opinion, cycle.
        for bad_opinion in (f"{ORIGIN}/ap/objects/7116", f"{ORIGIN}/ap/objects/{n_unrelated}", second_obj_iri, hidden_iri, f"{ORIGIN}/ap/objects/{n_under_hidden}", f"{ORIGIN}/ap/objects/7203"):
            r = http.post("/api/federation/opinions/item", json={"object": OBJECT, "opinion": bad_opinion}, headers=headers)
            assert (r.status_code, r.json()["error"]) == (404, "opinion_not_found"), bad_opinion

        # Hidden target document also returns 404 opinion_not_found.
        conn = dbm.connect_existing(internal.settings.db_file)
        try:
            conn.execute("UPDATE documents SET hidden=1 WHERE id='doc-second'")
            conn.commit()
        finally:
            conn.close()
        r = http.post("/api/federation/opinions/item", json={"object": second_obj_iri, "opinion": second_obj_iri}, headers=headers)
        assert (r.status_code, r.json()["error"]) == (404, "opinion_not_found")

        # Note ID generation avoids colliding with federation_rendition_objects.
        generated = iter([rendition_id, 816412345678901277])
        monkeypatch.setattr(ids, "timestamp_id", lambda *_args, **_kwargs: next(generated))
        note_res = http.post("/api/federation/notes", json={"content": "No collision"}, headers=headers)
        assert note_res.status_code == 201 and note_res.json()["note"]["id"] == "816412345678901277"
