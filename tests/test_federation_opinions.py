"""Opinions of published objects (DOCUMENT_RENDITIONS_AND_PUBLICATION_DESIGN.md Section 5.11 B and C, phases E7a and E7c, contract 1.23, client.db version 11).

The interface between Claire and the sidecar (each endpoint agrees with its contract fixture; the sidecar tests read the same files), the rules of the store (which posts are opinions, and what a 404 does), the remote ``Delete`` of an opinion, and the run that deletes the opinions of a gone object.
"""

from __future__ import annotations

from typing import Any

from claire.federation import opinions as opinions_mod
from claire.store import client_db
from claire.store import federation_opinions as opinions_store
from claire.store import db as dbm
from tests.test_federation_routes import GONE_OBJECT_ID, NOW, OBJECT_ID, _isolate, internal  # noqa: F401  (pytest fixtures)

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
        fetch = opinions_store.get_fetch(client, OBJECT)
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
        first = opinions_store.request_fetch(client, OBJECT, own=True, object_published=NOW - 600.0, now=NOW)
        # A fetch that waits is the answer of the next request; no second row.
        assert opinions_store.request_fetch(client, OBJECT, own=True, object_published=NOW - 600.0, now=NOW + 5).id == first.id
        opinions_store.finish(client, first.id, result_count=0, now=NOW + 10)
        assert opinions_store.request_fetch(client, OBJECT, own=True, object_published=NOW - 600.0, now=NOW + 60) is None
        again = opinions_store.request_fetch(client, OBJECT, own=True, object_published=NOW - 600.0, now=NOW + opinions_store.FETCH_INTERVAL_SECONDS + 1)
        assert again.status == "pending"
        # A fetch that the sidecar leased three times without an end fails.
        for step in range(opinions_store.MAX_ATTEMPTS):
            assert len(opinions_store.lease(client, now=NOW + 2000 + step * 400, limit=5)) == 1
        assert opinions_store.lease(client, now=NOW + 4000, limit=5) == []
        assert opinions_store.get_fetch(client, OBJECT).status == "failed"
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
    assert opinion_scheduler.run_once(internal.settings) == {"removed": 1}
    client = _client(internal)
    conn = dbm.connect_existing(internal.settings.db_file)
    try:
        assert opinions_store.object_iris(client) == [OBJECT]
        assert opinions_mod.purge_gone_objects(conn, client, origin=ORIGIN) == 0
    finally:
        conn.close()
        client.close()
