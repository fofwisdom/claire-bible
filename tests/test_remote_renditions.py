"""Renditions of other Claire servers: the reads and the cache (DOCUMENT_RENDITIONS_AND_PUBLICATION_DESIGN.md Section 5.4 rules 3 to 6, phase E5b).

The interface with the other server: the documents are the ones that a Claire origin gives (the source index and the body from its builders, the object in the form of its sidecar). The sidecar is not in the test: its part is the read queue of the knowledge federation, which gives the JSON as it came.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from claire.federation import object_collections, remote_renditions
from claire.federation.knowledge import handlers, journal, known_servers, remote_documents
from claire.ingest.source_key import source_key
from claire.store import db as dbm
from claire.store import federation as federation_store

A = "https://a.example"  # this server
B = "https://b.example"  # the origin of the renditions
KEY = source_key("https://arxiv.org/abs/2101.00001")
BOB = f"{B}/ap/actors/7001"
PRIMARY, CONCISE = f"{B}/ap/objects/9001", f"{B}/ap/objects/9002"
BODIES = {PRIMARY: "= 기본\n\n본문.", CONCISE: "= 간결\n\n== 핵심\n\n* 하나."}
T0 = 1800000000.0


def _object(iri: str, template: str, *, body: str | None = None, **changes) -> dict:
    """The object at its ID, as the sidecar of the origin gives it (FEDERATION.md Section 4.6)."""
    text = BODIES[iri] if body is None else body
    doc = {"@context": ["https://www.w3.org/ns/activitystreams", "https://w3id.org/claire-bible/ns/v2"], "id": iri, "type": "Article", "attributedTo": BOB, "name": "Same paper", "url": f"{B}/silvercord/tokentokentoken1" + ("" if template == "standard" else f"/renditions/{template}"), "updated": "2027-01-15T08:05:20Z", "claire:template": template, "claire:templateVersion": f"{template}-v1", "claire:body": f"{iri}/body", "claire:bodyHash": hashlib.sha256(text.encode()).hexdigest(), "claire:sourceKey": KEY, "claire:sourceIdVersion": 1}
    if template != "standard":
        doc["claire:renditionOf"] = PRIMARY
    doc.update(changes)
    return doc


def _body(iri: str, text: str | None = None) -> dict:
    text = BODIES[iri] if text is None else text
    return object_collections.rendition_body_document(f"{iri}/body", iri, html=f"<p>{text}</p><script>x</script>", source=text, body_hash=hashlib.sha256(text.encode()).hexdigest())


@pytest.fixture
def conn(tmp_path: Path):
    connection = dbm.connect(tmp_path / "claire.db")
    dbm.init_db(connection)
    known_servers.note_server(connection, "b.example", source="admin", added_by=1, now=T0)
    connection.commit()
    yield connection
    connection.close()


def _answer(conn, documents: dict, *, now: float) -> list[str]:
    """Do each read that waits with the documents of the origin, as the sidecar does, until no read waits. Return the addresses that were read."""
    done: list[str] = []
    while True:
        reads = remote_documents.lease(conn, now=now, limit=20)
        if not reads:
            return done
        for read in reads:
            document = documents.get(read["url"])
            remote_documents.record_result(conn, read["id"], document=document, error=None if document is not None else "origin_removed", origin=A, now=now)
            done.append(read["url"])


def _documents() -> dict:
    return {
        f"{B}/ap/sources/{KEY}": object_collections.source_index(f"{B}/ap/sources/{KEY}", [PRIMARY, CONCISE, "https://c.example/ap/objects/1"]),
        PRIMARY: _object(PRIMARY, "standard"),
        CONCISE: _object(CONCISE, "concise"),
        f"{PRIMARY}/body": _body(PRIMARY),
        f"{CONCISE}/body": _body(CONCISE),
        BOB: {"id": BOB, "type": "Person", "preferredUsername": "bob", "name": "Bob Lee"},
    }


def test_a_request_reads_the_index_the_objects_the_bodies_and_the_account_of_a_known_server(conn) -> None:
    assert remote_renditions.request_set(conn, KEY, origin=A, now=T0) == 1
    # One index read for each server and key in the lifetime.
    assert remote_renditions.request_set(conn, KEY, origin=A, now=T0 + 10) == 0
    read = _answer(conn, _documents(), now=T0 + 20)
    # An address of the index that is on another server gets no read.
    assert sorted(read) == sorted([f"{B}/ap/sources/{KEY}", PRIMARY, CONCISE, f"{PRIMARY}/body", f"{CONCISE}/body", BOB])
    shown = remote_renditions.renditions_of(conn, KEY, now=T0 + 30)
    assert [(r.iri, r.template, r.template_version, r.account_handle, r.account_name, r.rendition_of) for r in shown] == [(PRIMARY, "standard", "standard-v1", "@bob@b.example", "Bob Lee", None), (CONCISE, "concise", "concise-v1", "@bob@b.example", "Bob Lee", PRIMARY)]
    # The HTML of the other server goes through the sanitizer again; the AsciiDoc text stays as it came.
    assert shown[1].body_html == "<p>= 간결\n\n== 핵심\n\n* 하나.</p>" and shown[1].body_source == BODIES[CONCISE]
    assert len(shown[0].key) == 16 and shown[0].key != shown[1].key
    assert [tuple(r) for r in conn.execute("SELECT domain, status FROM remote_rendition_indexes")] == [("b.example", "ok")]
    # After the lifetime the copies are not shown, and the next request asks again. A body with the same hash is not read again.
    later = T0 + remote_renditions.LIFETIME_SECONDS + 100
    assert remote_renditions.renditions_of(conn, KEY, now=later) == []
    assert remote_renditions.request_set(conn, KEY, origin=A, now=later) == 1
    assert sorted(_answer(conn, _documents(), now=later)) == sorted([f"{B}/ap/sources/{KEY}", PRIMARY, CONCISE])
    assert len(remote_renditions.renditions_of(conn, KEY, now=later + 1)) == 2


def test_a_copy_goes_away_when_its_origin_removes_it_changes_it_or_is_blocked(conn) -> None:
    remote_renditions.request_set(conn, KEY, origin=A, now=T0)
    _answer(conn, _documents(), now=T0)
    later = T0 + remote_renditions.LIFETIME_SECONDS + 1
    # The index does not list the rendition "concise" now, and the body of the 대표 정리본 changed: the old body is not shown, the new one is.
    documents = _documents()
    documents[f"{B}/ap/sources/{KEY}"] = object_collections.source_index(f"{B}/ap/sources/{KEY}", [PRIMARY])
    documents[PRIMARY] = _object(PRIMARY, "standard", body="= 기본\n\n새 본문.")
    documents[f"{PRIMARY}/body"] = _body(PRIMARY, "= 기본\n\n새 본문.")
    remote_renditions.request_set(conn, KEY, origin=A, now=later)
    assert f"{PRIMARY}/body" in _answer(conn, documents, now=later)
    shown = remote_renditions.renditions_of(conn, KEY, now=later)
    assert [(r.iri, r.body_source) for r in shown] == [(PRIMARY, "= 기본\n\n새 본문.")]
    # A body whose hash is not the hash of the object is not stored.
    again = later + remote_renditions.LIFETIME_SECONDS + 1
    documents[PRIMARY] = _object(PRIMARY, "standard", body="= 기본\n\n셋째 본문.")
    remote_renditions.request_set(conn, KEY, origin=A, now=again)
    _answer(conn, documents, now=again)
    assert remote_renditions.renditions_of(conn, KEY, now=again) == []
    assert conn.execute("SELECT error_code FROM kf_remote_reads WHERE kind='rendition_body' ORDER BY id DESC LIMIT 1").fetchone()[0] == "hash_mismatch"
    # A blocked domain: its copies are not shown, and it gets no read.
    documents[f"{PRIMARY}/body"] = _body(PRIMARY, "= 기본\n\n셋째 본문.")
    more = again + remote_renditions.LIFETIME_SECONDS + 1
    remote_renditions.request_set(conn, KEY, origin=A, now=more)
    _answer(conn, documents, now=more)
    assert len(remote_renditions.renditions_of(conn, KEY, now=more)) == 1
    federation_store.add_domain_block(conn, "b.example", created_by=1, now=more)
    assert remote_renditions.renditions_of(conn, KEY, now=more) == []
    assert remote_renditions.request_set(conn, KEY, origin=A, now=more + remote_renditions.LIFETIME_SECONDS + 1) == 0


def test_an_object_outside_the_contract_is_not_stored_and_a_gone_object_is_forgotten(conn) -> None:
    for change in ({"claire:sourceKey": "0" * 64}, {"claire:template": "default"}, {"claire:body": "https://c.example/body"}, {"attributedTo": "https://c.example/ap/actors/1"}, {"id": f"{B}/ap/objects/1"}, {"type": "Note"}, {"claire:bodyHash": "xyz"}):
        conn.execute("DELETE FROM remote_rendition_indexes")
        remote_renditions.request_set(conn, KEY, origin=A, now=T0)
        documents = _documents()
        documents[CONCISE] = _object(CONCISE, "concise", **change)
        _answer(conn, documents, now=T0)
        assert [r.iri for r in remote_renditions.renditions_of(conn, KEY, now=T0)] == [PRIMARY], change
    # A 404 or 410 of the object address: the copy goes away at once.
    conn.execute("DELETE FROM remote_rendition_indexes")
    remote_renditions.request_set(conn, KEY, origin=A, now=T0 + 1)
    documents = _documents()
    del documents[PRIMARY]
    _answer(conn, documents, now=T0 + 1)
    assert [r.iri for r in remote_renditions.renditions_of(conn, KEY, now=T0 + 2)] == [CONCISE]
    with pytest.raises(ValueError):
        remote_renditions.request_set(conn, "not-a-key", origin=A, now=T0)


def test_the_delete_of_the_origin_removes_the_copy_and_only_its_account_can_send_it(conn) -> None:
    """The handler of ``delete_rendition`` (the exact delete list of the origin, Section 5.4 rule 4)."""
    remote_renditions.request_set(conn, KEY, origin=A, now=T0)
    _answer(conn, _documents(), now=T0)
    ctx = journal.HandlerContext(conn=conn, origin=A, now=T0 + 5)
    activity = {"object": {"id": CONCISE, "type": "Tombstone", "formerType": "Article", "claire:template": "concise"}}
    with pytest.raises(journal.HandlerRefusal):
        handlers.delete_rendition(ctx, SimpleNamespace(actor=f"{B}/ap/actors/7002"), activity)
    assert len(remote_renditions.renditions_of(conn, KEY, now=T0 + 5)) == 2
    handlers.delete_rendition(ctx, SimpleNamespace(actor=BOB), activity)
    assert [r.iri for r in remote_renditions.renditions_of(conn, KEY, now=T0 + 5)] == [PRIMARY]
    # A Delete for a rendition that this server does not have changes nothing.
    handlers.delete_rendition(ctx, SimpleNamespace(actor=BOB), activity)
