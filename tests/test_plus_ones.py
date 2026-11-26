"""+1 marks of renditions at their origin (DOCUMENT_RENDITIONS_AND_PUBLICATION_DESIGN.md Section 5.12, phase E9a, contract 1.22, claire.db version 55).

The interface with the other server (the handlers of the activities ``plus_one`` and ``undo_plus_one``) and with the sidecar (the counts in the object record and in the collection ``plus-ones``).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from claire.federation.knowledge import handlers, journal, vocabulary
from claire.store import db as dbm
from claire.store import federation as federation_store
from claire.store import plus_ones
from tests.test_federation_rendition_objects import RENDITION_ID, _seed
from tests.test_federation_routes import OBJECT_ID, _isolate, internal  # noqa: F401  (pytest fixtures)
from tests.typing_helpers import need

ORIGIN = "https://claire.example.com"
RENDITION = f"{ORIGIN}/ap/objects/{RENDITION_ID}"


def _entry(actor: str) -> Any:
    return SimpleNamespace(actor=actor, actor_domain=actor.split("/")[2])


def _mark(actor: str, rendition: str = RENDITION) -> dict:
    return vocabulary.build_plus_one(activity_id=f"{actor}#plus-ones/1", account_actor=actor, rendition=rendition, author=f"{ORIGIN}/ap/actors/116412345678901234", published=1800000400.0)


def test_marks_of_other_servers_make_the_counts_of_the_object_and_of_the_collection(internal):  # noqa: F811
    _seed(internal)
    conn = dbm.connect_existing(internal.settings.db_file)
    try:
        ctx = journal.HandlerContext(conn=conn, origin=ORIGIN, now=1800000500.0)
        actors = ["https://b.example/ap/actors/1", "https://b.example/ap/actors/2", "https://c.example/ap/actors/9"]
        for actor in actors:
            handlers.plus_one(ctx, _entry(actor), _mark(actor))
        # A second +1 of the same account changes nothing.
        handlers.plus_one(ctx, _entry(actors[0]), _mark(actors[0]))
        conn.commit()
    finally:
        conn.close()
    internal.assert_fixture("object_collection_plus_ones")
    record = internal.get(f"/objects/{RENDITION_ID}").json()
    assert (record["plus_ones"], record["plus_one_servers"], record["plus_one_score"]) == (3, 2, 3)
    # The document object is the 대표 정리본: it has its own counts. A note has no such collection.
    assert internal.get(f"/objects/{OBJECT_ID}/collections/plus-ones").json()["document"]["totalItems"] == 0
    assert internal.get("/objects/816412345678901240/collections/plus-ones").status_code == 404

    conn = dbm.connect_existing(internal.settings.db_file)
    try:
        ctx = journal.HandlerContext(conn=conn, origin=ORIGIN, now=1800000600.0)
        # The Undo takes the mark of its actor back; an Undo without a mark changes nothing.
        undo = vocabulary.build_undo_plus_one(activity_id="https://c.example/ap/actors/9#plus-ones/1/undo", plus_one=_mark("https://c.example/ap/actors/9"))
        handlers.undo_plus_one(ctx, _entry("https://c.example/ap/actors/9"), undo)
        handlers.undo_plus_one(ctx, _entry("https://c.example/ap/actors/9"), undo)
        assert plus_ones.counts(conn, RENDITION_ID) == {"total": 2, "servers": 1, "score": 2}
        # The object must be an own published rendition.
        for other in (f"{ORIGIN}/ap/objects/816412345678901240", f"{ORIGIN}/ap/objects/999", "https://b.example/ap/objects/1", f"{ORIGIN}/ap/actors/116412345678901234"):
            with pytest.raises(journal.HandlerRefusal) as refusal:
                handlers.plus_one(ctx, _entry("https://b.example/ap/actors/3"), {"id": "https://b.example/x", "object": other})
            assert refusal.value.code == "not_a_rendition", other
        # The retraction of the document deletes the marks of its rendition objects.
        federation_store.retract_object(conn, need(federation_store.get_object(conn, OBJECT_ID)), now=1800000700.0)
        assert plus_ones.counts(conn, RENDITION_ID)["total"] == 0
        conn.commit()
    finally:
        conn.close()


def test_one_server_counts_at_most_its_limit_and_a_domain_has_an_hourly_limit(internal):  # noqa: F811
    _seed(internal)
    conn = dbm.connect_existing(internal.settings.db_file)
    try:
        assert plus_ones.SERVER_LIMIT == vocabulary.PLUS_ONE_SERVER_LIMIT == 1000
        conn.executemany("INSERT INTO rendition_plus_ones (object_public_id, actor_iri, actor_host, activity_iri, created_at) VALUES (?, ?, 'farm.example', ?, 1)", [(RENDITION_ID, f"https://farm.example/ap/actors/{n}", f"https://farm.example/a/{n}") for n in range(1, 1206)])
        plus_ones.add(conn, RENDITION_ID, actor_iri="https://b.example/ap/actors/1", actor_host="b.example", activity_iri="https://b.example/a/1", now=2.0)
        # Owner decision O15: the marks of one server count at most 1,000 in the score.
        assert plus_ones.counts(conn, RENDITION_ID) == {"total": 1206, "servers": 2, "score": 1001}
        # The receiver takes at most 600 marks from one domain in one hour.
        now = 1800000000.0
        conn.executemany("INSERT INTO rendition_plus_ones (object_public_id, actor_iri, actor_host, activity_iri, created_at) VALUES (?, ?, 'busy.example', ?, ?)", [(OBJECT_ID, f"https://busy.example/ap/actors/{n}", f"https://busy.example/a/{n}", now - 60) for n in range(plus_ones.DOMAIN_HOURLY_LIMIT)])
        ctx = journal.HandlerContext(conn=conn, origin=ORIGIN, now=now)
        with pytest.raises(journal.HandlerRefusal) as refusal:
            handlers.plus_one(ctx, _entry("https://busy.example/ap/actors/9999"), _mark("https://busy.example/ap/actors/9999"))
        assert refusal.value.code == "rate_limited"
        # After the hour the domain can send marks again.
        handlers.plus_one(journal.HandlerContext(conn=conn, origin=ORIGIN, now=now + 3600), _entry("https://busy.example/ap/actors/9999"), _mark("https://busy.example/ap/actors/9999"))
    finally:
        conn.close()
