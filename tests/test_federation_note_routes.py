"""Internal federation API of the outbound interactions against the shared contract fixtures (contract 1.6, unit FP4-9), and the I/O of ``client.db`` version 5.

The sidecar tests read the same fixture files (``sidecar/fedify/tests/internal_api_test.ts``, ``sidecar/fedify/tests/notes_test.ts``).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from claire.federation import notes as note_text
from claire.api import federation_note_routes
from claire.store import client_db
from claire.store import db as dbm
from claire.store import federation as federation_store
from tests.test_federation_routes import FP49_FIXTURES, NOW, _isolate, fixture, internal  # noqa: F401  (pytest fixtures)

NOTE_ID = 816412345678901240
GONE_NOTE_ID = 816412345678901241
CAROL = "https://remote.example/users/carol"
PARENT = f"{CAROL}/notes/1"
ORIGIN = "https://claire.example.com"


def _seed(internal) -> None:  # noqa: F811
    """The rows of the contract 1.6 fixtures: one reply of ``alice`` with a content warning, a hashtag, a known mention, and a mention that is not resolved; one deleted note; one item of ``carol``; and four tasks."""
    text = "@carol@remote.example Hello #Claire @dan@other.example"
    html = note_text.render_note(text, origin=ORIGIN, mention_iris={"@carol@remote.example": CAROL}).html
    mentions = [{"handle": "@carol@remote.example", "iri": CAROL}, {"handle": "@dan@other.example", "iri": None}]
    client = client_db.connect_client_existing(client_db.client_db_path(internal.settings.db_file))
    try:
        client.execute(
            "INSERT INTO federation_notes (public_id, account_id, in_reply_to_iri, content_text, content_html, mentions_json, summary, sensitive, language, tags_json, published_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, 'Greeting', 1, 'en', '[\"Claire\"]', 1800000370.0, 1800000370.0)",
            (NOTE_ID, internal.alice, PARENT, text, html, json.dumps(mentions)),
        )
        client.execute("INSERT INTO federation_notes (public_id, account_id, content_text, content_html, mentions_json, published_at, updated_at, deleted_at) VALUES (?, ?, '', '', ?, 1800000375.0, 1800000380.0, 1800000380.0)", (GONE_NOTE_ID, internal.alice, json.dumps(mentions[:1])))
        client.execute("INSERT INTO federation_timeline_items (id, account_id, object_iri, object_type, author_iri, author_json, content_html, received_at, favourited, boosted) VALUES (1, ?, ?, 'Note', ?, '{}', '', 1800000365.0, 1, 1)", (internal.alice, PARENT, CAROL))
        for task_id, kind, target, ref_id, created_at in ((10, "note", None, NOTE_ID, 1800000371.0), (11, "delete_note", None, GONE_NOTE_ID, 1800000381.0), (12, "like", PARENT, 1, 1800000382.0), (13, "announce", PARENT, 1, 1800000383.0)):
            client.execute("INSERT INTO federation_client_outbox (id, account_id, kind, target_iri, ref_id, status, created_at) VALUES (?, ?, ?, ?, ?, 'pending', ?)", (task_id, internal.alice, kind, target, ref_id, created_at))
        client.commit()
    finally:
        client.close()


def _client_rows(internal, sql: str, params: tuple = ()) -> list[tuple]:  # noqa: F811
    conn = client_db.connect_client_existing(client_db.client_db_path(internal.settings.db_file), readonly=True)
    try:
        return [tuple(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


# --- Each endpoint agrees with its fixture (module interface) ---


def test_note_endpoints_agree_with_the_fixtures(internal):  # noqa: F811
    _seed(internal)
    assert FP49_FIXTURES == ["note", "note_gone", "interaction_tasks", "interaction_task_ack"]
    for name in FP49_FIXTURES:
        internal.assert_fixture(name)
    # The acknowledgement stored the resolved actor of the second mention, and the HTML has its link.
    [(mentions, html)] = _client_rows(internal, "SELECT mentions_json, content_html FROM federation_notes WHERE public_id=?", (NOTE_ID,))
    assert json.loads(mentions)[1] == {"handle": "@dan@other.example", "iri": "https://other.example/users/dan"}
    assert '<a href="https://other.example/users/dan" class="u-url mention"' in html
    # The tasks have a lease: a second request in that time gets none of them. The client tasks of contract 1.3 are a different list.
    assert internal.get("/interaction-tasks?limit=10").json() == {"tasks": []}
    assert [t["kind"] for t in internal.get("/client-tasks?limit=10").json()["tasks"]] == ["follow", "reject_follow"]


def test_note_models_are_sealed_and_bad_requests_are_refused(internal):  # noqa: F811
    _seed(internal)
    for model, name in ((federation_note_routes.NoteRecord, "note"), (federation_note_routes.InteractionTasksResponse, "interaction_tasks")):
        body = fixture(name)["response"]
        model.model_validate(body)
        try:
            model.model_validate({**body, "extra": 1})
        except ValueError:
            continue
        raise AssertionError(f"{name}: an extra field was accepted")
    ack = fixture("interaction_task_ack")["request"]
    assert internal.call({**ack, "body": {"result": "handed_off", "error_code": None}}).status_code == 400
    assert internal.call({**ack, "path": ack["path"].replace("/10/", "/999/")}).status_code == 404
    # A task of contract 1.3 has its own acknowledgement path.
    assert internal.call({**ack, "path": ack["path"].replace("/10/", "/1/")}).status_code == 404
    assert internal.get("/interaction-tasks?limit=0").status_code == 400
    assert internal.get("/notes/0").status_code == 404
    assert internal.get("/notes/816412345678901999").status_code == 404


def test_note_stays_public_for_private_profile_actor_hides_for_non_actor_and_blocks_bad_domains(internal):  # noqa: F811
    _seed(internal)
    ack = fixture("interaction_task_ack")["request"]
    assert internal.call({**ack, "body": {**ack["body"], "mentions": [{"handle": "@dan@other.example", "iri": "https://blocked.example/users/dan"}]}}).status_code == 200
    assert json.loads(_client_rows(internal, "SELECT mentions_json FROM federation_notes WHERE public_id=?", (NOTE_ID,))[0][0])[1]["iri"] is None
    # Decision GD8: with a private profile, own notes and public-KB published objects stay public in outbox and interaction tasks still lease; hiding the KB leaves own notes public.
    internal.pause(internal.alice)
    assert internal.get(f"/notes/{NOTE_ID}").status_code == 200
    outbox = internal.get("/actors/116412345678901234/outbox").json()
    assert (outbox["total"], [item["id"] for item in outbox["items"]]) == (3, [str(NOTE_ID), "816412345678901235", "816412345678901234"])
    conn = dbm.connect_existing(internal.settings.db_file)
    try:
        conn.execute("UPDATE knowledge_bases SET is_public=0 WHERE account_id=?", (internal.alice,))
        conn.commit()
    finally:
        conn.close()
    notes_only_outbox = internal.get("/actors/116412345678901234/outbox").json()
    assert (notes_only_outbox["total"], [item["id"] for item in notes_only_outbox["items"]]) == (1, [str(NOTE_ID)])
    assert [task["kind"] for task in internal.get("/interaction-tasks?limit=10").json()["tasks"]] == ["delete_note", "like", "announce"]
    # When the account is not an actor (suspended), own notes return 404 and ready interaction tasks wait, while a deleted note stays 410.
    client = client_db.connect_client_existing(client_db.client_db_path(internal.settings.db_file))
    try:
        client.execute("UPDATE federation_client_outbox SET lease_until=NULL WHERE id IN (11, 12, 13)")
        client.commit()
    finally:
        client.close()
    conn = dbm.connect_existing(internal.settings.db_file)
    try:
        conn.execute("UPDATE accounts SET status='suspended' WHERE id=?", (internal.alice,))
        conn.commit()
    finally:
        conn.close()
    assert internal.get(f"/notes/{NOTE_ID}").status_code == 404
    assert internal.get(f"/notes/{GONE_NOTE_ID}").status_code == 410
    assert internal.get("/interaction-tasks?limit=10").json() == {"tasks": []}


def test_outbox_collection_is_the_union_of_documents_and_notes(internal):  # noqa: F811
    _seed(internal)
    page = internal.get("/actors/116412345678901234/outbox?limit=2").json()
    # Two published documents and one note that is not deleted, newest number first.
    assert (page["total"], [item["id"] for item in page["items"]], page["next_max_id"]) == (3, [str(NOTE_ID), "816412345678901235"], "816412345678901235")
    rest = internal.get("/actors/116412345678901234/outbox?limit=2&max_id=816412345678901235").json()
    assert ([item["id"] for item in rest["items"]], rest["next_max_id"]) == (["816412345678901234"], None)


# --- client.db version 5 (I/O) ---


def test_version_4_file_gets_the_notes_table_and_keeps_its_rows(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "client.db"
    with monkeypatch.context() as m:
        m.setattr(client_db, "CLIENT_SCHEMA_VERSION", 4)
        m.setattr(client_db, "_migrate_v5", lambda conn: None)
        m.setattr(client_db, "_migrate_v6", lambda conn: None)
        conn = client_db.connect_client_db(path)
        conn.execute("INSERT INTO federation_following (account_id, request, state, created_at, updated_at) VALUES (1, '@a@remote.example', 'pending', 1, 1)")
        # Data that this code does not know stays (a table of a later tool).
        conn.execute("CREATE TABLE unexpected_table (x INTEGER)")
        conn.execute("INSERT INTO unexpected_table VALUES (7)")
        conn.commit()
        assert client_db.stored_client_version(conn) == 4
        assert conn.execute("SELECT 1 FROM sqlite_master WHERE name='federation_notes'").fetchone() is None
        conn.close()
    for _ in range(2):
        conn = client_db.connect_client_db(path)
        try:
            assert client_db.stored_client_version(conn) == client_db.CLIENT_SCHEMA_VERSION
            columns = {r[1] for r in conn.execute("PRAGMA table_info(federation_notes)")}
            assert {"public_id", "account_id", "content_text", "content_html", "mentions_json", "tags_json", "edited_at", "deleted_at"} <= columns
            assert conn.execute("SELECT COUNT(*) FROM federation_following").fetchone()[0] == 1
            assert conn.execute("SELECT x FROM unexpected_table").fetchone()[0] == 7
        finally:
            conn.close()


def test_purge_of_an_account_deletes_its_notes_and_leaves_tombstones(tmp_path: Path) -> None:
    path = tmp_path / "claire.db"
    conn = dbm.connect(path)
    dbm.init_db(conn)
    client = client_db.connect_client_db(client_db.client_db_path(path))
    for public_id, account_id, deleted_at in ((901, 1, None), (902, 2, None), (903, 2, 5.0)):
        client.execute("INSERT INTO federation_notes (public_id, account_id, content_text, content_html, published_at, updated_at, deleted_at) VALUES (?, ?, 't', '<p>t</p>', 1, 1, ?)", (public_id, account_id, deleted_at))
    client.commit()
    client.close()
    assert client_db.purge_account_of_claire_db(conn, 2) is True
    conn.commit()
    # The object path of a note of a purged account gives 410: the tombstone is in claire.db, as for a published document.
    assert federation_store.get_tombstone(conn, 901) is None
    assert federation_store.get_tombstone(conn, 902).kind == "object"
    assert federation_store.get_tombstone(conn, 903).gone_at == 5.0
    conn.close()
    client = sqlite3.connect(client_db.client_db_path(path))
    try:
        assert [r[0] for r in client.execute("SELECT public_id FROM federation_notes")] == [901]
    finally:
        client.close()
