"""End-to-end test of the sources of a personal knowledge base for other readers (schema v37, ``accounts.personal_source_public``).

A real app in network mode. Alice has a public profile and a personal knowledge base with two documents; one of them has a visible contribution in a shared knowledge base. Bob and an anonymous visitor read Alice's knowledge base:

- With the value 0, they get no source (``url``, ``extra_sources``, and each key with "url" in ``meta``) from ``GET /documents``, ``GET /document``, ``GET /document/export``, and ``GET /node``, except for the contributed document: the contribution made its source public.
- Alice always sees her sources.
- The default value (1) shows each source to each reader; ``PATCH /api/me`` with ``personal_source_public: false`` hides them.
"""

from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from claire.api import server
from claire.core.config import Settings
from claire.ontology.base import Document, Entity
from claire.store import accounts as accounts_store
from claire.store import db as dbm
from claire.store import kb_collab
from claire.store.knowledge_base import KnowledgeBaseManager
from claire.store.queries import documents_with_hidden_source, hide_document_source
from claire.store.secrets import reset_secret_service
from claire.store.token_store import get_token_store, reset_token_stores

PUBLIC_URL = "http://127.0.0.1:8765"
PRIVATE_URL = "https://private.example/secret-article"
CONTRIBUTED_URL = "https://news.example/contributed"


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch):
    reset_secret_service()
    reset_token_stores()
    for key in list(os.environ):
        if key.startswith("CLAIRE_"):
            monkeypatch.delenv(key, raising=False)
    yield
    reset_secret_service()
    reset_token_stores()


def _add_account(conn: sqlite3.Connection, account_id: int, username: str, role: str = "member") -> None:
    now = time.time()
    conn.execute(
        "INSERT INTO accounts (id, public_id, username, role, status, display_name, profile_public, created_at, updated_at) VALUES (?, ?, ?, ?, 'active', ?, 1, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET username=excluded.username, role=excluded.role, status='active', profile_public=1",
        (account_id, (1 << 57) + account_id, username, role, username.capitalize(), now, now),
    )


def _add_kb(conn: sqlite3.Connection, db_file: Path, *, kb_id: int, seq: int, label: str, account_id: int | None, personal: bool) -> None:
    now = time.time()
    conn.execute(
        "INSERT INTO knowledge_bases (id, seq, number, label, description, icon, is_personal, is_public, account_id, db_path, default_focus, knowledge_type, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, '', '📁', ?, 1, ?, ?, '', ?, ?, ?)",
        (kb_id, seq, (1 << 56) | seq, label, 1 if personal else 0, account_id, str(db_file), None if personal else "real", now, now),
    )


@pytest.fixture
def env(tmp_path: Path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    db_file = data_dir / "claire.db"
    conn = dbm.connect(db_file)
    dbm.init_db(conn)
    _add_account(conn, 1, "admin", role="admin")
    _add_account(conn, 2, "alice")
    _add_account(conn, 3, "bob")
    accounts_store.AccountStore(conn).set_setting("mode", "network", updated_by=1)
    _add_kb(conn, db_file, kb_id=20, seq=1, label="Alice", account_id=2, personal=True)
    _add_kb(conn, db_file, kb_id=40, seq=2, label="Shared", account_id=None, personal=False)
    private = Document(id="doc-private", url=PRIVATE_URL, canonical_url=PRIVATE_URL, title="Private", raw_text="private text", meta={"effective_url": PRIVATE_URL, "note": "kept"})
    contributed = Document(id="doc-contributed", url=CONTRIBUTED_URL, canonical_url=CONTRIBUTED_URL, title="Contributed", raw_text="contributed text")
    dbm.insert_document(conn, private, kb_id=20)
    dbm.insert_document(conn, contributed, kb_id=20)
    dbm.upsert_entity(conn, Entity(id="ent-a", type="Concept", name="Alpha", sources=["doc-private", "doc-contributed"]), kb_id=20)
    shared_copy = Document(id="doc-shared-copy", url=CONTRIBUTED_URL, canonical_url=CONTRIBUTED_URL, title="Contributed", raw_text="contributed text")
    dbm.insert_document(conn, shared_copy, kb_id=40)
    kb_collab.add_contribution(conn, kb_id=40, document_id="doc-shared-copy", origin="direct", state="visible", contributor_account_id=2, source_kb_id=20, source_document_id="doc-contributed", root_kb_id=20, root_document_id="doc-contributed")
    conn.commit()
    conn.close()

    settings = Settings(
        CLAIRE_DB_PATH=str(db_file),
        CLAIRE_DATA_DIR=str(data_dir),
        CLAIRE_PROVIDER="mock",
        CLAIRE_ENVIRONMENT="development",
        CLAIRE_PUBLIC_URL=PUBLIC_URL,
        CLAIRE_MULTI_THEME=True,
    )
    KnowledgeBaseManager(settings).reload()
    tokens = get_token_store(data_dir=data_dir)
    alice = tokens.create_token(name="alice", scope="owner", account_id=2)["token"]
    bob = tokens.create_token(name="bob", scope="owner", account_id=3)["token"]
    app = server.create_app(settings, SimpleNamespace(provider=SimpleNamespace(name="mock")))
    with TestClient(app, base_url=PUBLIC_URL) as client:
        yield SimpleNamespace(client=client, db_file=db_file, alice={"Authorization": f"Bearer {alice}"}, bob={"Authorization": f"Bearer {bob}"}, anonymous={})


def _sources(env: SimpleNamespace, headers: dict[str, str]) -> dict[str, object]:
    """The sources that one reader gets from each read route of Alice's personal knowledge base."""
    c = env.client
    listed = c.get("/@alice/documents", headers=headers)
    assert listed.status_code == 200, listed.text
    detail = c.get("/@alice/document", params={"id": "doc-private"}, headers=headers)
    assert detail.status_code == 200, detail.text
    contributed = c.get("/@alice/document", params={"id": "doc-contributed"}, headers=headers).json()
    node = c.get("/@alice/node", params={"id": "ent-a"}, headers=headers)
    assert node.status_code == 200, node.text
    export = c.get("/@alice/document/export", params={"id": "doc-private"}, headers=headers)
    assert export.status_code == 200, export.text
    return {
        "list": {d["id"]: d["url"] for d in listed.json()["documents"]},
        "detail": detail.json()["url"],
        "detail_meta_url": detail.json()["meta"].get("effective_url"),
        "detail_meta_note": detail.json()["meta"].get("note"),
        "contributed": contributed["url"],
        "node": {d["id"]: d["url"] for d in node.json()["documents"]},
        "export_has_source": PRIVATE_URL in export.text,
    }


def test_e2e_sources_of_a_personal_knowledge_base_follow_the_account_setting(env):
    hidden = {
        "list": {"doc-private": None, "doc-contributed": CONTRIBUTED_URL},
        "detail": None,
        "detail_meta_url": None,
        "detail_meta_note": "kept",
        "contributed": CONTRIBUTED_URL,
        "node": {"doc-private": None, "doc-contributed": CONTRIBUTED_URL},
        "export_has_source": False,
    }
    shown = {
        "list": {"doc-private": PRIVATE_URL, "doc-contributed": CONTRIBUTED_URL},
        "detail": PRIVATE_URL,
        "detail_meta_url": PRIVATE_URL,
        "detail_meta_note": "kept",
        "contributed": CONTRIBUTED_URL,
        "node": {"doc-private": PRIVATE_URL, "doc-contributed": CONTRIBUTED_URL},
        "export_has_source": True,
    }
    # The default value: each reader sees each source.
    assert env.client.get("/api/me", headers=env.alice).json()["personal_source_public"] is True
    assert _sources(env, env.bob) == shown
    assert _sources(env, env.anonymous) == shown

    # A value that is not a boolean is refused and changes nothing.
    res = env.client.patch("/api/me", json={"personal_source_public": 0}, headers=env.alice)
    assert res.status_code == 400 and res.json()["error"] == "invalid_personal_source_public"
    assert _sources(env, env.bob) == shown
    # Alice hides her sources: only Alice sees them. A contributed document shows its source to each reader.
    res = env.client.patch("/api/me", json={"personal_source_public": False}, headers=env.alice)
    assert res.status_code == 200, res.text
    assert res.json()["personal_source_public"] is False
    assert _sources(env, env.bob) == hidden
    assert _sources(env, env.anonymous) == hidden
    assert _sources(env, env.alice) == shown

    # Back to public.
    assert env.client.patch("/api/me", json={"personal_source_public": True}, headers=env.alice).status_code == 200
    assert _sources(env, env.bob) == shown

    # The shared knowledge base always shows the source of the copy.
    shared = env.client.get("/kb/" + _shared_number(env) + "/document", params={"id": "doc-shared-copy"}, headers=env.bob)
    assert shared.status_code == 200 and shared.json()["url"] == CONTRIBUTED_URL


def _shared_number(env: SimpleNamespace) -> str:
    from claire.core import ids

    return ids.format_public_id((1 << 56) | 2)


def test_store_functions_of_the_source_rule(env):
    """I/O of the two store functions: the account value and the visible contributions decide; the scrub removes each key with "url" and keeps the text."""
    conn = dbm.connect(env.db_file)
    try:
        assert documents_with_hidden_source(conn, account_id=2, kb_id=20, document_ids=["doc-private"]) == set()  # the default value 1
        conn.execute("UPDATE accounts SET personal_source_public=0 WHERE id=2")
        assert documents_with_hidden_source(conn, account_id=2, kb_id=20, document_ids=["doc-private", "doc-contributed", ""]) == {"doc-private"}
        assert documents_with_hidden_source(conn, account_id=2, kb_id=20, document_ids=[]) == set()
        # A contribution that is not visible (pending) does not make a source public.
        conn.execute("UPDATE kb_contributions SET state='pending'")
        assert documents_with_hidden_source(conn, account_id=2, kb_id=20, document_ids=["doc-contributed"]) == {"doc-contributed"}
        conn.execute("UPDATE accounts SET personal_source_public=1 WHERE id=2")
        assert documents_with_hidden_source(conn, account_id=2, kb_id=20, document_ids=["doc-private"]) == set()
        conn.rollback()
    finally:
        conn.close()
    doc = {"id": "d", "url": "https://a.example/", "title": "T", "extra_sources": [{"url": "https://b.example/"}], "meta": {"effective_url": "https://a.example/", "images": [{"url": "https://c.example/i.png", "alt": "x"}]}, "presentation_pdf": {"public_url": "https://a.example/p.pdf", "status": "available"}}
    assert hide_document_source(doc) == {"id": "d", "url": None, "title": "T", "extra_sources": [], "meta": {"images": [{"alt": "x"}]}, "presentation_pdf": {"status": "available"}}
    assert doc["url"] == "https://a.example/"  # the input does not change
