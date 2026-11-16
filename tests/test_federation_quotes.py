"""Quotes (FEP-044f, unit FP4-9Q): the I/O of the quote schema (``client.db`` version 6, ``claire.db`` version 24), and the internal federation API against the shared contract fixtures (contract 1.7).

The sidecar tests read the same fixture files (``sidecar/fedify/tests/internal_api_test.ts``, ``sidecar/fedify/tests/quotes_test.ts``). The behavior of quotes from the routes of the owner is in ``tests/test_e2e_federation_quotes.py``.
"""

from __future__ import annotations

from pathlib import Path

from claire import federation_notes as note_text
from claire.api import federation_note_routes, federation_quote_routes
from claire.store import client_db
from claire.store import db as dbm
from claire.store import federation_notes as notes_store
from tests.test_federation_routes import FP49Q_FIXTURES, _isolate, fixture, internal  # noqa: F401  (pytest fixtures)

ORIGIN = "https://claire.example.com"
BOB = "https://remote.example/users/bob"
CAROL = "https://remote.example/users/carol"
PARENT = f"{CAROL}/notes/1"
QUOTED_NOTE_ID = 816412345678901240
QUOTING_NOTE_ID = 816412345678901242
CLOSED_NOTE_ID = 816412345678901243


def _seed(internal) -> None:  # noqa: F811
    """The rows of the contract 1.7 fixtures. Notes of ``alice``: one that remote actors can quote (policy ``public``), one that quotes a note of ``carol`` (the quote waits), and one that nobody can quote. Two requests of ``bob`` for the first note: one approved and one revoked. Three tasks."""
    html = note_text.render_note("Look at this", origin=ORIGIN, quote_url=PARENT).html
    client = client_db.connect_client_existing(client_db.client_db_path(internal.settings.db_file))
    try:
        for public_id, text, body, policy in ((QUOTED_NOTE_ID, "Quote me", "<p>Quote me</p>", "public"), (CLOSED_NOTE_ID, "Not this one", "<p>Not this one</p>", "nobody")):
            client.execute("INSERT INTO federation_notes (public_id, account_id, content_text, content_html, language, published_at, updated_at, quote_policy) VALUES (?, ?, ?, ?, 'en', 1800000370.0, 1800000370.0, ?)", (public_id, internal.alice, text, body, policy))
        client.execute(
            "INSERT INTO federation_notes (public_id, account_id, content_text, content_html, language, published_at, updated_at, quote_of_iri, quote_author_iri, quote_state, quote_policy) VALUES (?, ?, 'Look at this', ?, 'en', 1800000389.0, 1800000389.0, ?, ?, 'pending', 'followers')",
            (QUOTING_NOTE_ID, internal.alice, html, PARENT, CAROL),
        )
        for row_id, number, state, updated_at in ((1, 9, "approved", 1800000391.0), (2, 8, "revoked", 1800000392.0)):
            client.execute(
                "INSERT INTO federation_quote_authorizations (id, account_id, object_public_id, quoting_object_iri, quoting_actor_iri, request_iri, state, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, 1800000388.0, ?)",
                (row_id, internal.alice, QUOTED_NOTE_ID, f"https://remote.example/notes/{number}", BOB, f"{BOB}#quote-requests/{number}", state, updated_at),
            )
        for task_id, kind, target, ref_id, created_at in ((20, "quote_request", PARENT, QUOTING_NOTE_ID, 1800000390.0), (21, "quote_authorize", BOB, 1, 1800000391.0), (22, "quote_revoke", BOB, 2, 1800000392.0)):
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


def test_quote_endpoints_agree_with_the_fixtures(internal):  # noqa: F811
    _seed(internal)
    assert FP49Q_FIXTURES == ["quote_tasks", "quote_note", "quote_authorization", "quote_authorization_gone", "quote_event_request", "quote_event_nobody", "quote_event_accept"]
    for name in FP49Q_FIXTURES:
        internal.assert_fixture(name)
    tasks = lambda: _client_rows(internal, "SELECT kind, ref_id, status FROM federation_client_outbox WHERE id>22 ORDER BY id")  # noqa: E731
    # The request for the note with the policy ``public`` made an approved row and its task; the request for the ``nobody`` note made a refused row and the task of the Reject.
    assert _client_rows(internal, "SELECT id, object_public_id, state FROM federation_quote_authorizations WHERE id>2 ORDER BY id") == [(3, QUOTED_NOTE_ID, "approved"), (4, CLOSED_NOTE_ID, "refused")]
    # The Accept of ``carol`` made the quote of the own note approved, and the followers get the note again with the authorization.
    assert tasks() == [("quote_authorize", 3, "pending"), ("quote_reject", 4, "pending"), ("update_note", QUOTING_NOTE_ID, "pending")]
    record = internal.get(f"/notes/{QUOTING_NOTE_ID}").json()
    assert (record["quote"], record["quote_authorization"]) == (PARENT, "https://remote.example/users/carol/quote-authorizations/1")
    # A repeated Accept changes nothing.
    assert internal.call(fixture("quote_event_accept")["request"]).json() == {"decision": "accepted", "reason": None, "state": None}
    assert len(tasks()) == 3


def test_quote_models_are_sealed_and_untrusted_input_is_refused(internal):  # noqa: F811
    _seed(internal)
    for model, name in ((federation_note_routes.InteractionTasksResponse, "quote_tasks"), (federation_quote_routes.QuoteAuthorizationRecord, "quote_authorization"), (federation_quote_routes.QuoteEventResponse, "quote_event_request")):
        body = fixture(name)["response"]
        model.model_validate(body)
        try:
            model.model_validate({**body, "extra": 1})
        except ValueError:
            continue
        raise AssertionError(f"{name}: an extra field was accepted")
    accept = fixture("quote_event_accept")["request"]
    body = accept["body"]

    def send(change: dict) -> dict:
        return internal.call({**accept, "body": {**body, **change}}).json()

    dan = {"iri": "https://remote.example/users/dan", "inbox": "https://remote.example/users/dan/inbox", "shared_inbox": None}
    # Only the author of the quoted object can answer the request.
    assert send({"actor": dan}) == {"decision": "rejected", "reason": "not_the_author", "state": None}
    # The authorization must be on the origin of the author, and it must name the quoting note and the quoted object.
    for authorization in (
        None,
        {**body["authorization"], "iri": "https://other.example/quote-authorizations/1"},
        {**body["authorization"], "interacting_object": f"{ORIGIN}/ap/objects/{QUOTED_NOTE_ID}"},
        {**body["authorization"], "interacting_object": None},
        {**body["authorization"], "interaction_target": f"{CAROL}/notes/2"},
        {**body["authorization"], "attributed_to": dan["iri"]},
    ):
        assert send({"authorization": authorization}) == {"decision": "rejected", "reason": "invalid_authorization", "state": None}
    # An answer to an activity that is not a quote request of a note, and an answer for a note without a quote.
    assert send({"object_iri": f"{ORIGIN}/ap/objects/{QUOTING_NOTE_ID}#create"})["reason"] == "unsupported_type"
    assert send({"object_iri": f"{ORIGIN}/ap/objects/{QUOTED_NOTE_ID}#quote-request"})["reason"] == "no_quote"
    assert _client_rows(internal, "SELECT quote_state, quote_authorization_iri FROM federation_notes WHERE public_id=?", (QUOTING_NOTE_ID,)) == [("pending", None)]
    # A blocked domain, and a body outside the model.
    blocked = {"iri": "https://blocked.example/users/x", "inbox": "https://blocked.example/users/x/inbox", "shared_inbox": None}
    assert send({"actor": blocked})["reason"] == "blocked_domain"
    assert internal.call({**accept, "body": {**body, "extra": 1}}).status_code == 400
    request = fixture("quote_event_request")["request"]
    # The quoting object must be on the host of the actor that asks; an object path without an object gets no answer.
    assert internal.call({**request, "body": {**request["body"], "instrument_iri": "https://other.example/notes/1"}}).json()["reason"] == "invalid_object"
    assert internal.call({**request, "body": {**request["body"], "instrument_iri": None}}).json()["reason"] == "invalid_object"
    assert internal.call({**request, "body": {**request["body"], "object_iri": f"{ORIGIN}/ap/objects/816412345678901999"}}).json()["reason"] == "no_object"
    assert _client_rows(internal, "SELECT COUNT(*) FROM federation_quote_authorizations") == [(2,)]
    # The path of an authorization: a number that is not valid, and a request that waits.
    assert internal.get("/quote-authorizations/0").status_code == 404
    assert internal.get("/quote-authorizations/99").status_code == 404
    internal.set_state(federation="off")
    assert internal.get("/quote-authorizations/1").status_code == 404
    # A revoked authorization is gone in all states, the same as a deleted object.
    assert internal.get("/quote-authorizations/2").status_code == 410
    assert internal.call(request).json()["reason"] == "federation_off"


# --- Schema (I/O) ---


def test_version_5_file_gets_the_quote_schema_and_keeps_its_rows(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "client.db"
    with monkeypatch.context() as m:
        m.setattr(client_db, "CLIENT_SCHEMA_VERSION", 5)
        m.setattr(client_db, "_migrate_v6", lambda conn: None)
        conn = client_db.connect_client_db(path)
        conn.execute("INSERT INTO federation_notes (public_id, account_id, content_text, content_html, published_at, updated_at) VALUES (901, 1, 't', '<p>t</p>', 1, 1)")
        # Data that this code does not know stays (a table of a later tool).
        conn.execute("CREATE TABLE unexpected_table (x INTEGER)")
        conn.execute("INSERT INTO unexpected_table VALUES (7)")
        conn.commit()
        assert client_db.stored_client_version(conn) == 5
        assert not {r[1] for r in conn.execute("PRAGMA table_info(federation_notes)") if r[1].startswith("quote")}
        conn.close()
    # The migration completes itself at the start, and a second start changes nothing.
    for _ in range(2):
        conn = client_db.connect_client_db(path)
        try:
            assert client_db.stored_client_version(conn) == 6 == client_db.CLIENT_SCHEMA_VERSION
            assert {"quote_of_iri", "quote_author_iri", "quote_state", "quote_authorization_iri", "quote_policy"} <= {r[1] for r in conn.execute("PRAGMA table_info(federation_notes)")}
            assert {"id", "account_id", "object_public_id", "quoting_object_iri", "quoting_actor_iri", "request_iri", "state"} <= {r[1] for r in conn.execute("PRAGMA table_info(federation_quote_authorizations)")}
            # A note of version 5 has no quote and the default policy of a note.
            note = notes_store.get_note(conn, 901)
            assert (note.quote_of_iri, note.quote_state, note.quote_policy) == (None, None, "public")
            assert conn.execute("SELECT x FROM unexpected_table").fetchone()[0] == 7
        finally:
            conn.close()


def test_version_23_file_gets_the_quote_policy_of_published_documents(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "claire.db"
    with monkeypatch.context() as m:
        m.setattr(dbm, "SCHEMA_VERSION", 23)
        m.setattr(dbm, "_migrate_v24_object_quote_policy", lambda conn: None)
        conn = dbm.connect(path)
        dbm.init_db(conn)
        conn.execute("INSERT INTO themes (id, seq, label, is_default, is_public, is_collaborator_accessible, created_at, updated_at, account_id, number) VALUES (11, 11, 'physics', 0, 1, 1, 1, 1, 1, 516412345678901234)")
        conn.execute("INSERT INTO federation_objects (public_id, account_id, theme_id, document_id, published_by_account_id, published_at, updated_at) VALUES (801, 1, 11, 'doc-a', 1, 1, 1)")
        conn.commit()
        assert "quote_policy" not in {r[1] for r in conn.execute("PRAGMA table_info(federation_objects)")}
        conn.close()
    for _ in range(2):
        conn = dbm.connect(path)
        try:
            dbm.init_db(conn)
            assert dbm.stored_schema_version(conn) == dbm.SCHEMA_VERSION
            # The default of a published document is manual (design Section 5.4 rule 6).
            assert conn.execute("SELECT quote_policy FROM federation_objects WHERE public_id=801").fetchone()[0] == "manual"
        finally:
            conn.close()
