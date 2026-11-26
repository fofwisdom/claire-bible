"""Collections of an own published object: ``replies``, ``likes``, and ``shares`` (DOCUMENT_RENDITIONS_AND_PUBLICATION_DESIGN.md Section 5.11 A, phase E6, contract 1.19).

The interface between Claire and the sidecar: each endpoint agrees with its contract fixture. The sidecar tests read the same files.
"""

from __future__ import annotations

from claire.api import federation_collection_routes
from claire.federation import object_collections
from claire.store import client_db
from claire.store import db as dbm
from tests.test_federation_routes import E6_FIXTURES, OBJECT_ID, _isolate, fixture, internal  # noqa: F401  (pytest fixtures)
from tests.typing_helpers import need

ORIGIN = "https://claire.example.com"
OBJECT = f"{ORIGIN}/ap/objects/{OBJECT_ID}"
SELF_REPLY, BOB_REPLY, DELETED_REPLY = 816412345678901250, 816412345678901251, 816412345678901252
CAROL_REPLY = "https://remote.example/users/carol/notes/9"


def _seed(internal) -> None:  # noqa: F811
    """One reply of ``alice`` (the author of the object), one of ``private_bob``, one deleted reply, one received post that two accounts have, one received post on a blocked domain, and two likes and one boost."""
    client = client_db.connect_client_existing(client_db.client_db_path(internal.settings.db_file))
    try:
        for public_id, account_id, deleted_at in ((SELF_REPLY, internal.alice, None), (BOB_REPLY, internal.bob, None), (DELETED_REPLY, internal.alice, 1800000400.0)):
            client.execute("INSERT INTO federation_notes (public_id, account_id, in_reply_to_iri, content_text, content_html, mentions_json, published_at, updated_at, deleted_at) VALUES (?, ?, ?, 'A reply', '<p>A reply</p>', '[]', 1800000390.0, 1800000390.0, ?)", (public_id, account_id, OBJECT, deleted_at))
        for account_id, object_iri in ((internal.alice, CAROL_REPLY), (internal.bob, CAROL_REPLY), (internal.alice, "https://blocked.example/notes/1"), (internal.alice, "https://remote.example/users/carol/notes/other")):
            reply_to = None if object_iri.endswith("/other") else OBJECT
            client.execute("INSERT INTO federation_timeline_items (account_id, object_iri, object_type, author_iri, author_json, content_html, in_reply_to_iri, received_at) VALUES (?, ?, 'Note', 'https://remote.example/users/carol', '{}', '', ?, 1800000395.0)", (account_id, object_iri, reply_to))
        for kind, actor in (("like", "https://remote.example/users/carol"), ("like", "https://remote.example/users/dan"), ("announce", "https://remote.example/users/dan")):
            client.execute("INSERT INTO federation_object_reactions (object_public_id, kind, actor_iri, account_id, activity_iri, received_at) VALUES (?, ?, ?, ?, ?, 1800000396.0)", (OBJECT_ID, kind, actor, internal.alice, f"{actor}#{kind}"))
        client.commit()
    finally:
        client.close()


def test_collection_endpoints_agree_with_the_fixtures(internal):  # noqa: F811
    _seed(internal)
    assert E6_FIXTURES == ["object_collection_replies", "object_collection_replies_page", "object_collection_likes", "object_collection_not_found"]
    for name in E6_FIXTURES:
        internal.assert_fixture(name)
    federation_collection_routes.ObjectCollectionResponse.model_validate(fixture("object_collection_replies")["response"])
    # The object record has the two numbers; the page of shares has the boost.
    record = internal.get(f"/objects/{OBJECT_ID}").json()
    assert (record["likes"], record["shares"]) == (2, 1)
    assert internal.get(f"/objects/{OBJECT_ID}/collections/shares").json()["document"]["totalItems"] == 1


def test_replies_have_no_deleted_note_no_blocked_domain_and_each_post_one_time(internal):  # noqa: F811
    _seed(internal)
    first = internal.get(f"/objects/{OBJECT_ID}/collections/replies").json()["document"]
    page = internal.get(f"/objects/{OBJECT_ID}/collections/replies?page=true").json()["document"]
    listed = [*first["first"]["items"], *page["items"]]
    assert listed == [f"{ORIGIN}/ap/objects/{SELF_REPLY}", f"{ORIGIN}/ap/objects/{BOB_REPLY}", CAROL_REPLY]
    assert "next" not in page
    # An account that is not an actor now has no reply in the list.
    conn = dbm.connect_existing(internal.settings.db_file)
    try:
        conn.execute("UPDATE accounts SET status='suspended' WHERE id=?", (internal.bob,))
        conn.commit()
    finally:
        conn.close()
    assert internal.get(f"/objects/{OBJECT_ID}/collections/replies?page=true").json()["document"]["items"] == [CAROL_REPLY]


def test_refusals_of_the_collection_route(internal):  # noqa: F811
    _seed(internal)
    base = f"/objects/{OBJECT_ID}/collections"
    for path, status in ((f"{base}/followers", 404), (f"{base}/likes?page=true", 404), (f"{base}/replies?page=1", 400), (f"{base}/replies?cursor=20", 400), (f"{base}/replies?page=true&cursor=x", 400), (f"{base}/replies?page=true&other=1", 400), (f"{base}/replies?page=true&cursor=40", 404), ("/objects/abc/collections/replies", 404)):
        assert internal.get(path).status_code == status, path
    # A note is an own object too: it has collections. A deleted note has none.
    assert internal.get(f"/objects/{SELF_REPLY}/collections/replies").json()["document"]["totalItems"] == 0
    assert internal.get(f"/objects/{DELETED_REPLY}/collections/replies").status_code == 404


def test_pages_of_replies_have_the_page_size_and_the_next_link():
    """Input and output of the builders: the first page has at most five replies of the author; each later page has twenty replies."""
    own = [f"{ORIGIN}/ap/objects/{n}" for n in range(1, 8)]
    others = [f"https://remote.example/notes/{n}" for n in range(30)]
    iri = f"{OBJECT}/replies"
    collection = object_collections.replies_collection(iri, own, others)
    assert (collection["totalItems"], collection["first"]["items"], collection["first"]["next"]) == (37, own[:5], f"{iri}?page=true")
    first = need(object_collections.replies_page(iri, own, others, 0))
    assert first["items"] == [*own[5:], *others[:18]] and first["next"] == f"{iri}?page=true&cursor=20"
    last = need(object_collections.replies_page(iri, own, others, 20))
    assert last["items"] == others[18:] and "next" not in last
    assert object_collections.replies_page(iri, own, others, 40) is None
    empty = object_collections.replies_collection(iri, [], [])
    assert empty["first"] == {"type": "CollectionPage", "partOf": iri, "items": []}
