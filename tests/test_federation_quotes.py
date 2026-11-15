"""I/O of the quote schema (unit FP4-9Q): ``client.db`` version 6 and ``claire.db`` version 24.

The behavior of quotes is in ``tests/test_e2e_federation_quotes.py``; the contract fixtures are in ``tests/test_federation_routes.py`` and ``tests/test_federation_note_routes.py``.
"""

from __future__ import annotations

from pathlib import Path

from claire.store import client_db
from claire.store import db as dbm
from claire.store import federation_notes as notes_store


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
            assert dbm.stored_schema_version(conn) == 24 == dbm.SCHEMA_VERSION
            # The default of a published document is manual (design Section 5.4 rule 6).
            assert conn.execute("SELECT quote_policy FROM federation_objects WHERE public_id=801").fetchone()[0] == "manual"
        finally:
            conn.close()
