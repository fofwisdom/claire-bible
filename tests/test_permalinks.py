"""The permalink registry and the permalink route (docs/design/PERMALINK_UNIFICATION_DESIGN.md).

Unit tests: the migration (I/O) and the lifecycle of the registry. End-to-end test: one document through the route ``/silvercord/{token}`` for an anonymous caller and for a member.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from starlette.testclient import TestClient

from claire.api.server import create_app
from claire.store import accounts as accounts_store
from claire.store import db as dbm
from claire.store import permalinks
from tests.test_public_directory import _setup_network_env


def _registry_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE documents (id TEXT PRIMARY KEY, kb_id INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE entities (id TEXT PRIMARY KEY, kb_id INTEGER NOT NULL DEFAULT 0, type TEXT NOT NULL, norm_name TEXT NOT NULL);
        CREATE TABLE doc_shares (token TEXT PRIMARY KEY, kb_id INTEGER NOT NULL DEFAULT 0, document_id TEXT NOT NULL, created_at REAL, expires_at REAL);
        """
    )
    return conn


def test_migration_keeps_each_valid_share_token_and_gives_each_item_one_token() -> None:
    conn = _registry_db()
    now = time.time()
    conn.executemany("INSERT INTO documents (id, kb_id) VALUES (?, ?)", [("doc-shared", 3), ("doc-plain", 3)])
    conn.execute("INSERT INTO entities (id, kb_id, type, norm_name) VALUES ('ent-1', 3, 'Concept', 'graph')")
    conn.executemany(
        "INSERT INTO doc_shares (token, kb_id, document_id, created_at, expires_at) VALUES (?, 3, 'doc-shared', ?, ?)",
        [("aaaaaaaaaaaaaaaa", now - 300, None), ("bbbbbbbbbbbbbbbb", now - 100, None), ("cccccccccccccccc", now - 50, now - 10)],
    )

    permalinks.migrate(conn)
    permalinks.migrate(conn)

    # The newest valid share token is the address token, with link access. The older valid one is an alias. The expired one is not in the registry.
    assert permalinks.token_of(conn, "document", "doc-shared") == "bbbbbbbbbbbbbbbb"
    assert permalinks.link_access(conn, "document", "doc-shared") is True
    alias = permalinks.resolve(conn, "aaaaaaaaaaaaaaaa")
    assert alias is not None and (alias.token, alias.alias, alias.target) == ("bbbbbbbbbbbbbbbb", "aaaaaaaaaaaaaaaa", "doc-shared")
    assert permalinks.resolve(conn, "cccccccccccccccc") is None
    # A document without a share and an entity get a new token, without link access.
    plain = permalinks.token_of(conn, "document", "doc-plain")
    entity = permalinks.token_of(conn, "entity", "ent-1")
    assert plain is not None and entity is not None and permalinks.plausible_token(plain) and permalinks.plausible_token(entity)
    assert permalinks.link_access(conn, "document", "doc-plain") is False
    # The second run made no second row.
    assert conn.execute("SELECT COUNT(*) FROM permalinks").fetchone()[0] == 4


def test_merge_makes_aliases_and_keeps_link_access() -> None:
    conn = _registry_db()
    conn.executemany("INSERT INTO documents (id, kb_id) VALUES (?, 1)", [("keeper",), ("loser",)])
    permalinks.migrate(conn)
    keeper_token = permalinks.token_of(conn, "document", "keeper")
    loser_token = permalinks.token_of(conn, "document", "loser")
    permalinks.set_link_access(conn, "document", "loser", True)

    permalinks.merge_documents(conn, "keeper", ["loser"], kb_id=1)

    found = permalinks.resolve(conn, str(loser_token))
    assert found is not None and found.token == keeper_token and found.alias == loser_token
    assert permalinks.link_access(conn, "document", "keeper") is True


def test_an_entity_that_is_made_again_gets_its_token_again() -> None:
    conn = _registry_db()
    conn.execute("INSERT INTO entities (id, kb_id, type, norm_name) VALUES ('ent-old', 5, 'Person', 'ada')")
    permalinks.migrate(conn)
    token = permalinks.token_of(conn, "entity", "ent-old")

    # A second extraction deletes the entity and makes it again with a new ID.
    conn.execute("DELETE FROM entities WHERE id='ent-old'")
    conn.execute("INSERT INTO entities (id, kb_id, type, norm_name) VALUES ('ent-new', 5, 'Person', 'ada')")
    assert permalinks.ensure_entity(conn, "ent-new") == token
    found = permalinks.resolve(conn, str(token))
    assert found is not None and found.target == "ent-new" and found.gone_at is None
    # An entity with a different identity, or in a different knowledge base, gets a new token.
    conn.execute("INSERT INTO entities (id, kb_id, type, norm_name) VALUES ('ent-other', 6, 'Person', 'ada')")
    assert permalinks.ensure_entity(conn, "ent-other") != token
    assert permalinks.ensure_entity(conn, "ent-missing") is None


def test_e2e_one_permalink_for_an_anonymous_caller_and_a_member(tmp_path: Path) -> None:
    s, env = _setup_network_env(tmp_path)
    conn = dbm.connect_existing(env["db_file"])
    try:
        session_token = accounts_store.create_account_session(conn, account_id=env["alice_id"], scope="owner")
        token = permalinks.ensure_document(conn, "doc1", 10)
        hidden_token = permalinks.ensure_document(conn, "doc3", 10)
        conn.commit()
    finally:
        conn.close()
    test_client = TestClient(create_app(s), base_url=s.public_url, follow_redirects=False)
    address = f"/silvercord/{token}"
    origin = s.public_url.rstrip("/")

    class _Caller:
        """One caller: anonymous, or the member with the session cookie. The cookie store of the client is empty before and after each request."""

        def __init__(self, session: str | None) -> None:
            self.session = session

        def request(self, method: str, path: str, **kwargs):
            headers = {"Origin": origin} if method not in ("GET", "HEAD") else {}
            if self.session is not None:
                headers["Cookie"] = f"claire_session={self.session}"
            test_client.cookies.clear()
            response = test_client.request(method, path, headers=headers, **kwargs)
            test_client.cookies.clear()
            return response

        def get(self, path: str):
            return self.request("GET", path)

    client = _Caller(None)
    alice = _Caller(session_token)

    # A public knowledge base: an anonymous caller gets the item view, with the permalink as the canonical link.
    anon = client.get(address)
    assert anon.status_code == 200 and 'class="shared-page"' in anon.text
    assert f'<link rel="canonical" href="{s.public_url.rstrip("/")}/silvercord/{token}"/>' in anon.text
    assert "x-robots-tag" not in anon.headers
    assert client.get(f"/silvercord/{hidden_token}").status_code == 404
    assert client.get("/silvercord/2222222222222222").status_code == 404
    # Old links redirect to the permalink.
    old_share = client.get(f"/p?s={token}")
    assert old_share.status_code == 308 and old_share.headers["location"] == address
    old_doc = client.get("/@alice/?doc=doc1")
    assert old_doc.status_code == 308 and old_doc.headers["location"] == address

    # A private knowledge base without link access: no item view for an anonymous caller.
    conn = dbm.connect_existing(env["db_file"])
    try:
        conn.execute("UPDATE knowledge_bases SET is_public=0 WHERE id=10")
        conn.commit()
    finally:
        conn.close()
    closed = client.get(address)
    assert 'class="shared-page"' not in closed.text and "Doc 1" not in closed.text

    # The member gets the workspace with the document open, at the same address.
    member = alice.get(address)
    assert member.status_code == 200 and "Knowledge graph" in member.text
    assert '<meta name="claire-open-item" content="document:doc1"/>' in member.text

    # The share action sets link access: the anonymous caller gets the item view, and a search engine must not index it.
    shared = alice.request("POST", "/@alice/share", json={"doc_id": "doc1"})
    assert shared.status_code == 200 and shared.json() == {"token": token, "path": address, "link_access": True, "kb_id": 10}
    linked = client.get(address)
    assert linked.status_code == 200 and 'class="shared-page"' in linked.text
    assert linked.headers["x-robots-tag"] == "noindex, nofollow" and '<meta name="robots" content="noindex, nofollow"/>' in linked.text

    # The stop-sharing action keeps the address and ends the access.
    stopped = alice.request("DELETE", "/@alice/share", json={"doc_id": "doc1"})
    assert stopped.status_code == 200 and stopped.json()["link_access"] is False and stopped.json()["path"] == address
    assert 'class="shared-page"' not in client.get(address).text

    # A document that does not exist now: 410.
    conn = dbm.connect_existing(env["db_file"])
    try:
        conn.execute("DELETE FROM documents WHERE id='doc1'")
        conn.commit()
    finally:
        conn.close()
    assert client.get(address).status_code == 410
