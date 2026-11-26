"""Rendition objects of a published document (DOCUMENT_RENDITIONS_AND_PUBLICATION_DESIGN.md Section 5.5, phase E4b, contract 1.20, claire.db version 52).

The interface between Claire and the sidecar (each endpoint agrees with its contract fixture; the sidecar tests read the same files), and the life of a rendition object in the store.
"""

from __future__ import annotations

import json

from claire.ingest.source_key import source_key
from claire.store import db as dbm
from claire.store import document_renditions as renditions_store
from claire.store import federation as federation_store
from tests.test_federation_routes import ALICE_THEME_ID, OBJECT_ID, _isolate, fixture, internal  # noqa: F401  (pytest fixtures)
from tests.typing_helpers import need

RENDITION_ID = 816412345678901260
FIXTURES = ["object_rendition", "object_collection_renditions", "object_body", "object_body_unsigned", "source_index", "source_index_unsigned"]


def _seed(internal) -> None:  # noqa: F811
    """The rendition rows of the published document of the fixture, its source key, and the object of its rendition ``concise`` with a fixed number."""
    conn = dbm.connect_existing(internal.settings.db_file)
    try:
        conn.execute("UPDATE documents SET url='https://arxiv.org/abs/2101.00001', source_key=?, source_id_version=1 WHERE id='doc-published'", (source_key("https://arxiv.org/abs/2101.00001"),))
        conn.execute("INSERT INTO document_renditions (document_id, kb_id, template, template_version, detail, detail_html, model_lineage, created_at, updated_at) VALUES ('doc-published', ?, 'standard', 'standard-v1', NULL, NULL, '{}', 1800000300, 1800000300)", (ALICE_THEME_ID,))
        conn.execute("INSERT INTO document_renditions (document_id, kb_id, template, template_version, detail, detail_html, model_lineage, created_at, updated_at) VALUES ('doc-published', ?, 'concise', 'concise-v1', '= 간결\n\n== 핵심\n\n* Hello.', '<h2>핵심</h2><ul><li>Hello.</li></ul><script>x</script>', '{}', 1800000330, 1800000330)", (ALICE_THEME_ID,))
        conn.execute("INSERT INTO federation_rendition_objects (public_id, parent_public_id, template, created_at, updated_at) VALUES (?, ?, 'concise', 1800000330, 1800000330)", (RENDITION_ID, OBJECT_ID))
        conn.commit()
    finally:
        conn.close()


def test_rendition_endpoints_agree_with_the_fixtures(internal):  # noqa: F811
    _seed(internal)
    for name in FIXTURES:
        internal.assert_fixture(name)
    primary = internal.get(f"/objects/{OBJECT_ID}").json()
    assert (primary["template"], primary["template_version"], primary["rendition_of"]) == ("standard", "standard-v1", None)
    assert primary["source_key"] == fixture("object_rendition")["response"]["source_key"]


def test_refusals_of_the_body_and_of_the_collection(internal):  # noqa: F811
    _seed(internal)
    body = f"/objects/{RENDITION_ID}/body"
    # Only the server actor of another server gets the body (KS1); each refusal is the same 404.
    for signer in ("https://remote.example/users/bob", "https://claire.example.com/ap/actors/server"):
        assert internal.get(f"{body}?signer={signer}").status_code == 404, signer
    conn = dbm.connect_existing(internal.settings.db_file)
    try:
        federation_store.add_domain_block(conn, "remote.example", created_by=1, now=1.0)
        conn.commit()
    finally:
        conn.close()
    assert internal.get(f"{body}?signer=https://remote.example/ap/actors/server").status_code == 404
    assert internal.get(f"{body}?other=1").status_code == 400
    # The collection of the renditions is on the document object only.
    assert internal.get(f"/objects/{RENDITION_ID}/collections/renditions").status_code == 404
    # A rendition object is public with its document: a hidden document hides it.
    conn = dbm.connect_existing(internal.settings.db_file)
    try:
        conn.execute("UPDATE documents SET hidden=1 WHERE id='doc-published'")
        conn.commit()
    finally:
        conn.close()
    assert internal.get(f"/objects/{RENDITION_ID}").status_code == 404


def test_rendition_objects_follow_the_renditions_of_a_published_document(internal):  # noqa: F811
    """A new rendition of a published document gets an object, a deleted one gets a tombstone (410), and the retraction of the document gives each rendition object a tombstone. No outbox task comes from a rendition."""
    conn = dbm.connect_existing(internal.settings.db_file)
    try:
        tasks_before = conn.execute("SELECT COUNT(*) FROM federation_outbox").fetchone()[0]
        renditions_store.record_primary(conn, "doc-published", ALICE_THEME_ID, lineage={"provider": "mock", "model": "m"})
        renditions_store.store_additional(conn, "doc-published", ALICE_THEME_ID, template="academic", detail="= 학술\n\n본문.", lineage={"provider": "mock", "model": "m"}, now=1800000400.0)
        made = federation_store.rendition_objects_of(conn, OBJECT_ID)
        assert [r.template for r in made] == ["academic"]
        # A changed body changes the time of the object; the number stays.
        renditions_store.store_additional(conn, "doc-published", ALICE_THEME_ID, template="academic", detail="= 학술\n\n새 본문.", lineage={"provider": "mock", "model": "m"}, now=1800000500.0)
        again = federation_store.rendition_objects_of(conn, OBJECT_ID)
        assert (again[0].public_id, again[0].updated_at) == (made[0].public_id, 1800000500.0)
        assert conn.execute("SELECT COUNT(*) FROM federation_outbox").fetchone()[0] == tasks_before
        conn.commit()
    finally:
        conn.close()
    rendition_path = f"/objects/{made[0].public_id}"
    assert internal.get(rendition_path).json()["template"] == "academic"
    conn = dbm.connect_existing(internal.settings.db_file)
    try:
        assert renditions_store.delete_additional(conn, "doc-published", ALICE_THEME_ID, "academic") is True
        conn.commit()
    finally:
        conn.close()
    assert internal.get(rendition_path).status_code == 410
    # The retraction of the document: each rendition object gets its tombstone.
    conn = dbm.connect_existing(internal.settings.db_file)
    try:
        renditions_store.store_additional(conn, "doc-published", ALICE_THEME_ID, template="concise", detail="= 간결\n\n본문.", lineage={}, now=1800000600.0)
        concise = federation_store.rendition_objects_of(conn, OBJECT_ID)[0]
        federation_store.retract_object(conn, need(federation_store.get_object(conn, OBJECT_ID)), now=1800000700.0)
        conn.commit()
    finally:
        conn.close()
    assert internal.get(f"/objects/{concise.public_id}").status_code == 410


def test_the_readers_of_a_body_get_one_delete_when_the_rendition_is_gone(internal):  # noqa: F811
    """The exact delete list (Section 5.4 rule 4, phase E5a): a body read records the server actor; when the rendition object is gone, the knowledge outbox has one ``Delete`` that the account actor signs, for that server actor only. The rows go away after the task."""
    from claire.federation.knowledge import outbox as kf_outbox
    from claire.federation.knowledge import vocabulary

    _seed(internal)
    reader = "https://remote.example/ap/actors/server"
    assert internal.get(f"/objects/{RENDITION_ID}/body?signer={reader}").status_code == 200
    conn = dbm.connect_existing(internal.settings.db_file)
    try:
        assert [tuple(r) for r in conn.execute("SELECT object_public_id, server_actor_iri, gone_at FROM federation_body_readers")] == [(RENDITION_ID, reader, None)]
        assert renditions_store.delete_additional(conn, "doc-published", ALICE_THEME_ID, "concise") is True
        kf_outbox.sync(conn, origin="https://claire.example.com", now=1800000900.0)
        conn.commit()
        tasks = conn.execute("SELECT t.activity_iri, t.kind, t.signer, t.recipient, a.payload FROM kf_outbox t JOIN kf_outbox_activities a ON a.activity_iri = t.activity_iri WHERE t.kind='delete_rendition'").fetchall()
        assert len(tasks) == 1
        activity_iri, kind, signer, recipient, payload = tasks[0]
        activity = json.loads(payload)
        assert (kind, recipient, signer.startswith("account:")) == ("delete_rendition", reader, True)
        assert vocabulary.validate_activity(activity) == "delete_rendition"
        assert activity["object"] == {"id": f"https://claire.example.com/ap/objects/{RENDITION_ID}", "type": "Tombstone", "formerType": "Article", "claire:template": "concise"}
        assert conn.execute("SELECT COUNT(*) FROM federation_body_readers").fetchone()[0] == 0
    finally:
        conn.close()


def test_a_like_and_an_announce_of_a_rendition_object_are_counted_with_its_own_number(internal):  # noqa: F811
    """Section 5.6 rule 2: a rendition object is not a post, but a reaction to it is stored as for each object, and its record shows the counts."""
    _seed(internal)
    like = {**fixture("inbox_event_like")["request"]["body"], "object_iri": f"https://claire.example.com/ap/objects/{RENDITION_ID}"}
    for kind, event_id in (("Like", "https://remote.example/activities/301"), ("Announce", "https://remote.example/activities/302")):
        answer = internal.call({"method": "POST", "path": "/internal/federation/v1/inbox-events", "body": {**like, "type": kind, "event_id": event_id}})
        assert answer.json()["decision"] == "accepted", answer.json()
    record = internal.get(f"/objects/{RENDITION_ID}").json()
    assert (record["likes"], record["shares"]) == (1, 1)
    assert (internal.get(f"/objects/{OBJECT_ID}").json()["likes"]) == 0
    # The Undo removes the like of the rendition object.
    undo = {**like, "type": "Undo", "object_type": "Like", "event_id": "https://remote.example/activities/303", "object_iri": f"https://claire.example.com/ap/objects/{RENDITION_ID}"}
    assert internal.call({"method": "POST", "path": "/internal/federation/v1/inbox-events", "body": undo}).json()["decision"] == "accepted"
    assert internal.get(f"/objects/{RENDITION_ID}").json()["likes"] == 0
