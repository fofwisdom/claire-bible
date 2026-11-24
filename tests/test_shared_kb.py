"""Shared knowledge bases (SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md, unit CT-1 part b).

- Section 12.2: the conversion of a version 27 database.
- Section 6.2 and 6.5: the capability matrix (one function).
- Section 7 and 10.1: an account makes a shared knowledge base and reads it at ``/kb/{number}/``.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from claire.accounts import kb_access
from claire.api.server import create_app
from claire.core.config import Settings
from claire.store import db as dbm
from claire.store import kb_collab
from claire.store.knowledge_base import KnowledgeBaseManager, KnowledgeBaseNotFound

OWNER_TOKEN = "owner-" + ("s" * 32)
OWNER_HEADERS = {"Authorization": f"Bearer {OWNER_TOKEN}"}


def _settings(tmp_path: Path) -> Settings:
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    return Settings(
        CLAIRE_DB_PATH=str(tmp_path / "data" / "claire.db"),
        CLAIRE_PROVIDER="mock",
        CLAIRE_ENVIRONMENT="development",
        CLAIRE_FQDN="127.0.0.1:8765",
        CLAIRE_INJECT_TOKEN=OWNER_TOKEN,
    )


def _make_v27_database(path: Path) -> None:
    """A database with one personal knowledge base and three knowledge bases of the old shape (an owner, not personal), with documents and queue rows."""
    conn = dbm.connect(path)
    dbm.init_db(conn)
    for kb_id, public in ((1, 1), (2, 0), (3, 1)):
        conn.execute(
            "INSERT INTO knowledge_bases (id, seq, label, is_personal, is_public, created_at, updated_at, account_id, number) VALUES (?, ?, ?, 0, ?, 1, 1, 1, ?)",
            (kb_id, kb_id, f"old theme {kb_id}", public, (1 << 57) + kb_id),
        )
    docs = (("d0", 0, 0), ("d1a", 1, 0), ("d1b", 1, 1), ("d2", 2, 0))
    for doc_id, kb_id, hidden in docs:
        conn.execute("INSERT INTO documents (id, kb_id, title, hidden, watch_enabled, fetched_at) VALUES (?, ?, ?, ?, 1, 1)", (doc_id, kb_id, doc_id, hidden))
    conn.execute("INSERT INTO refresh_queue (kb_id, document_id, payload) VALUES (1, 'd1a', 'u1'), (0, 'd0', 'u0')")
    conn.execute("UPDATE meta SET value='27' WHERE key='schema_version'")
    conn.commit()
    conn.close()


def _state(conn: sqlite3.Connection) -> list:
    out = []
    for table in ("knowledge_bases", "kb_memberships", "kb_contributions", "documents", "refresh_queue"):
        out.append((table, sorted(tuple(r) for r in conn.execute(f"SELECT * FROM {table}"))))
    return out


def test_v27_database_with_four_knowledge_bases_starts_as_v28(tmp_path: Path) -> None:
    path = tmp_path / "claire.db"
    _make_v27_database(path)
    conn = dbm.connect(path)
    try:
        dbm.init_db(conn)
        assert dbm.stored_schema_version(conn) == dbm.SCHEMA_VERSION  # 28 (this conversion) and then 29 (knowledge federation tables) in the same start
        rows = {r["id"]: r for r in conn.execute("SELECT id, is_personal, account_id, created_by_account_id, is_public, knowledge_type, join_policy, state FROM knowledge_bases")}
        # One personal knowledge base without a knowledge type.
        assert tuple(rows[0])[1:6] == (1, 1, None, 1, None)
        # Three shared knowledge bases: no owner, public (also the one that was private), type ``real``.
        for kb_id in (1, 2, 3):
            assert tuple(rows[kb_id])[1:] == (0, None, 1, 1, "real", "approval", "active")
        assert kb_collab.kind_violations(conn) == []
        # The admin (the old owner) is the moderator of each.
        for kb_id in (1, 2, 3):
            m = kb_collab.get_membership(conn, kb_id, 1)
            assert (m["role"], m["state"]) == ("moderator", "active")
        # One approved contribution for each document, with the origin ``migration`` and no root.
        got = sorted((c["kb_id"], c["document_id"], c["origin"], c["state"], c["contributor_account_id"], c["root_document_id"], c["approved_at"] is not None) for kb in (1, 2, 3) for c in kb_collab.list_contributions(conn, kb))
        assert got == [(1, "d1a", "migration", "visible", 1, None, True), (1, "d1b", "migration", "hidden", 1, None, True), (2, "d2", "migration", "visible", 1, None, True)]
        assert kb_collab.list_contributions(conn, 0) == []
        # CT10: watch stops and the queues lose the rows of the shared knowledge bases; the personal one keeps its rows.
        assert {r[0]: r[1] for r in conn.execute("SELECT id, watch_enabled FROM documents")} == {"d0": 1, "d1a": 0, "d1b": 0, "d2": 0}
        assert [tuple(r) for r in conn.execute("SELECT kb_id, document_id FROM refresh_queue")] == [(0, "d0")]
        first = _state(conn)
        # A second start changes nothing.
        dbm.init_db(conn)
        assert _state(conn) == first
    finally:
        conn.close()


def test_owner_that_is_not_active_gets_no_membership(tmp_path: Path) -> None:
    path = tmp_path / "claire.db"
    conn = dbm.connect(path)
    dbm.init_db(conn)
    conn.execute("INSERT INTO accounts (id, public_id, username, role, status, display_name, profile_public, created_at, updated_at) VALUES (7, ?, 'gone', 'member', 'deleting', '', 0, 1, 1)", ((1 << 57) + 7,))
    conn.execute("INSERT INTO knowledge_bases (id, seq, label, is_personal, is_public, created_at, updated_at, account_id) VALUES (4, 4, 'left behind', 0, 1, 1, 1, 7)")
    conn.commit()
    dbm.init_db(conn)
    row = conn.execute("SELECT account_id, created_by_account_id FROM knowledge_bases WHERE id=4").fetchone()
    assert tuple(row) == (None, 7)
    assert kb_collab.list_memberships(conn, 4) == []  # the admin moderates (capability matrix)
    conn.close()


def test_failed_conversion_leaves_an_unavailable_row_and_the_next_start_tries_again(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(tmp_path)
    KnowledgeBaseManager(settings)  # makes the database
    conn = dbm.connect(settings.db_file)
    conn.execute("INSERT INTO knowledge_bases (id, seq, label, is_personal, is_public, created_at, updated_at, account_id, number) VALUES (5, 5, 'breaks', 0, 1, 1, 1, 1, ?)", ((1 << 57) + 5,))
    conn.execute("INSERT INTO documents (id, kb_id, title, fetched_at) VALUES ('d5', 5, 'd5', 1)")
    conn.commit()
    conn.close()

    def boom(*_a, **_k):
        raise RuntimeError("no contribution")

    with monkeypatch.context() as m:
        m.setattr(kb_collab, "add_contribution", boom)
        manager = KnowledgeBaseManager(settings)  # the start continues
        # The row stays as it was and no route serves it; the admin list shows it with the reason.
        with pytest.raises(KnowledgeBaseNotFound):
            manager.get_kb(5)
        assert [(kb.id, reason) for kb, reason in manager.unavailable_kbs()] == [(5, "shared_with_owner")]
        with TestClient(create_app(settings=settings), base_url=settings.public_url) as client:
            assert client.get(f"/kb/{(1 << 57) + 5}/documents", headers=OWNER_HEADERS).status_code == 404
            assert client.get(f"/themes/{(1 << 57) + 5}/documents", headers=OWNER_HEADERS).status_code == 404
        check = dbm.connect_existing(settings.db_file, readonly=True)
        assert tuple(check.execute("SELECT account_id, knowledge_type FROM knowledge_bases WHERE id=5").fetchone()) == (1, None)
        assert check.execute("SELECT COUNT(*) FROM kb_memberships WHERE kb_id=5").fetchone()[0] == 0  # the savepoint took back the membership
        check.close()

    # The next start converts it.
    manager = KnowledgeBaseManager(settings)
    assert manager.get_kb(5).is_shared and manager.unavailable_kbs() == []


def test_next_start_keeps_the_founder_and_the_knowledge_type_of_a_shared_knowledge_base(tmp_path: Path) -> None:
    """A shared knowledge base has no owner. The owner step of version 15 must not give it to the admin: the conversion would then replace its founder and its type."""
    settings = _settings(tmp_path)
    conn = dbm.connect(settings.db_file)
    try:
        dbm.init_db(conn)
        conn.execute("INSERT INTO accounts (id, public_id, username, role, status, display_name, profile_public, created_at, updated_at) VALUES (2, 902, 'founder', 'member', 'active', 'Founder', 1, 1, 1)")
        conn.commit()
    finally:
        conn.close()
    manager = KnowledgeBaseManager(settings)
    made = manager.define_kb("Middle-earth", account_id=2, knowledge_type="fictional")

    def _state() -> list[tuple]:
        c = dbm.connect_existing(settings.db_file, readonly=True)
        try:
            kb = tuple(c.execute("SELECT account_id, created_by_account_id, knowledge_type FROM knowledge_bases WHERE id=?", (made.id,)).fetchone())
            members = [tuple(r) for r in c.execute("SELECT account_id, role, state FROM kb_memberships WHERE kb_id=? ORDER BY account_id", (made.id,))]
            return [kb, members]
        finally:
            c.close()

    expected = [(None, 2, "fictional"), [(2, "moderator", "active")]]
    assert _state() == expected
    # The next starts: a new manager, a reload, and the schema step again.
    KnowledgeBaseManager(settings).reload()
    manager.reload()
    conn = dbm.connect(settings.db_file)
    try:
        dbm.init_db(conn)
        conn.commit()
    finally:
        conn.close()
    assert _state() == expected
    assert [(kb.label, kb.knowledge_type, kb.created_by_account_id) for kb in manager.list_shared_kbs()] == [("Middle-earth", "fictional", 2)]


def _decide(capability: str, actor: kb_access.Actor, *, personal: bool = False, owner: int | None = None, public: bool = True) -> str:
    return kb_access.decide(is_personal=personal, owner_account_id=owner, is_public=public, capability=capability, actor=actor)


def test_capability_matrix_of_a_shared_knowledge_base() -> None:
    """Section 6.2, each cell: allow, 403 (forbidden), or 404 (not_found)."""
    anonymous = kb_access.Actor(account_id=None, scope=None)
    account = kb_access.Actor(account_id=5, scope="owner")
    pending = kb_access.Actor(account_id=5, scope="owner", membership=("member", "pending"))
    banned = kb_access.Actor(account_id=5, scope="owner", membership=("member", "banned"))
    member = kb_access.Actor(account_id=5, scope="owner", membership=("member", "active"))
    collaborator = kb_access.Actor(account_id=5, scope="owner", membership=("collaborator", "active"))
    moderator = kb_access.Actor(account_id=5, scope="owner", membership=("moderator", "active"))
    admin = kb_access.Actor(account_id=1, scope="owner", is_admin=True)
    a, f, n = "allow", "forbidden", "not_found"
    #                     anonymous account member collaborator moderator admin
    expected = {
        "read":              (a, a, a, a, a, a),
        "join":              (n, a, a, a, a, a),
        "contribute_direct": (n, f, a, a, a, a),
        "contribute_theme":  (n, a, a, a, a, a),
        "fork":              (n, a, a, a, a, a),
        "review":            (n, f, f, a, a, a),
        "curate":            (n, f, f, a, a, a),
        "maintain":          (n, f, f, a, a, a),
        "people":            (n, f, f, f, a, a),
        "settings":          (n, f, f, f, a, a),
        "remove":            (n, f, f, f, a, a),
        "moderators":        (n, f, f, f, f, a),
        "delete":            (n, f, f, f, f, a),
    }
    assert set(expected) == set(kb_access.SHARED_CAPABILITIES)
    for capability, row in expected.items():
        got = tuple(_decide(capability, actor) for actor in (anonymous, account, member, collaborator, moderator, admin))
        assert got == row, capability
        # Rule 1: a pending member is as an account that is not a member; a banned account has only read and fork.
        assert _decide(capability, pending) == _decide(capability, account), capability
        assert _decide(capability, banned) == (a if capability in ("read", "fork") else f), capability
        # Section 6.6: a ``readonly`` credential has only ``read``; a ``write`` credential has only ``read`` and the contributions, also for a moderator or the admin.
        readonly = kb_access.Actor(account_id=5, scope="readonly", membership=("moderator", "active"))
        assert _decide(capability, readonly) == (a if capability == "read" else f), capability
        for holder in (kb_access.Actor(account_id=5, scope="write", membership=("moderator", "active")), kb_access.Actor(account_id=1, scope="write", is_admin=True)):
            assert _decide(capability, holder) == (a if capability in ("read", "contribute_direct", "contribute_theme") else f), capability


def test_share_link_capability() -> None:
    """``share`` (make a share link): each account that reads a shared knowledge base, with each scope, but not a banned one and not an anonymous request; in a personal knowledge base only its account."""
    for scope in ("owner", "write", "readonly"):
        assert _decide("share", kb_access.Actor(account_id=5, scope=scope)) == "allow"
        assert _decide("share", kb_access.Actor(account_id=2, scope=scope), personal=True, owner=2, public=False) == "allow"
    assert _decide("share", kb_access.Actor(account_id=5, scope="owner", membership=("member", "banned"))) == "forbidden"
    assert _decide("share", kb_access.Actor(account_id=None, scope=None)) == "not_found"
    assert _decide("share", kb_access.Actor(account_id=3, scope="owner"), personal=True, owner=2, public=True) == "forbidden"
    assert _decide("share", kb_access.Actor(account_id=None, scope=None), personal=True, owner=2, public=True) == "not_found"
    # Single mode keeps its rule: a reader of the public knowledge base of the admin makes share links.
    anonymous = kb_access.Actor(account_id=None, scope=None)
    assert kb_access.decide(is_personal=True, owner_account_id=1, is_public=True, capability="share", actor=anonymous, single_mode=True) == "allow"
    assert kb_access.decide(is_personal=True, owner_account_id=1, is_public=False, capability="share", actor=anonymous, single_mode=True) == "not_found"


def test_capability_matrix_of_a_personal_knowledge_base() -> None:
    """Section 6.5: only the owner has rights; a private one gives the same 404 as an unknown number."""
    owner = kb_access.Actor(account_id=2, scope="owner")
    other = kb_access.Actor(account_id=3, scope="owner")
    admin = kb_access.Actor(account_id=1, scope="owner", is_admin=True)
    anonymous = kb_access.Actor(account_id=None, scope=None)
    for capability in ("write", "settings"):
        assert _decide(capability, owner, personal=True, owner=2) == "allow"
        assert _decide(capability, other, personal=True, owner=2, public=True) == "forbidden"
        assert _decide(capability, other, personal=True, owner=2, public=False) == "not_found"
        assert _decide(capability, admin, personal=True, owner=2, public=True) == "forbidden"  # the admin is not a content superuser
        assert _decide(capability, anonymous, personal=True, owner=2, public=True) == "not_found"
    assert _decide("read", other, personal=True, owner=2, public=True) == "allow"
    assert _decide("read", other, personal=True, owner=2, public=False) == "not_found"
    assert _decide("read", anonymous, personal=True, owner=2, public=False) == "not_found"
    assert _decide("read", owner, personal=True, owner=2, public=False) == "allow"
    # Section 6.6: ``write`` reads and writes the personal knowledge base of its account; ``readonly`` reads, private included.
    writer = kb_access.Actor(account_id=2, scope="write")
    reader = kb_access.Actor(account_id=2, scope="readonly")
    assert [_decide(c, writer, personal=True, owner=2, public=False) for c in ("read", "write", "settings")] == ["allow", "allow", "forbidden"]
    assert [_decide(c, reader, personal=True, owner=2, public=False) for c in ("read", "write", "settings")] == ["allow", "forbidden", "forbidden"]


def test_e2e_an_account_makes_a_shared_knowledge_base_and_reads_it_at_the_kb_path(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    with TestClient(create_app(settings=settings), base_url=settings.public_url, follow_redirects=False) as client:
        # CT12: the knowledge type is required; CT5: a shared knowledge base is always public.
        assert client.post("/kb", json={"label": "Cities"}, headers=OWNER_HEADERS).status_code == 400
        assert client.post("/kb", json={"label": "Cities", "knowledge_type": "real", "is_public": False}, headers=OWNER_HEADERS).status_code == 400
        made = client.post("/kb", json={"label": "Cities", "knowledge_type": "real"}, headers=OWNER_HEADERS)
        assert made.status_code == 201, made.text
        kb = made.json()["knowledge_base"]
        assert (kb["is_personal"], kb["account_id"], kb["created_by_account_id"], kb["knowledge_type"], kb["is_public"], kb["join_policy"], kb["state"]) == (False, None, 1, "real", True, "approval", "active")
        number = kb["number"]
        # Labels need not be unique (Section 10.1).
        assert client.post("/kb", json={"label": "Cities", "knowledge_type": "fictional"}, headers=OWNER_HEADERS).status_code == 201

        # CT8: the founder is the first moderator.
        conn = dbm.connect_existing(settings.db_file, readonly=True)
        m = kb_collab.get_membership(conn, kb["id"], 1)
        conn.close()
        assert (m["role"], m["state"]) == ("moderator", "active")

        # Section 7: /kb/{number}/ serves it, also to an anonymous reader; the old /themes/{number}/ link gets 308.
        assert client.get(f"/kb/{number}/documents").status_code == 200
        old = client.get(f"/themes/{number}/documents?x=1")
        assert old.status_code == 308 and old.headers["location"] == f"/kb/{number}/documents?x=1"
        # The number of the personal knowledge base at /kb/ gets 308 to its own path.
        personal = client.get("/kb", headers=OWNER_HEADERS).json()["personal_kb"]
        home = client.get(f"/kb/{personal['number']}/documents", headers=OWNER_HEADERS)
        assert home.status_code == 308 and home.headers["location"] == "/documents"
        # The collection /kb has the shared knowledge bases; the founder moderates both (Section 7).
        listed = client.get("/kb", headers=OWNER_HEADERS).json()["knowledge_bases"]
        assert [t["label"] for t in listed] == ["Cities", "Cities"] and {t["role"] for t in listed} == {"moderator"}

        # Section 6.2: an anonymous write is 404; ingest by a path that pins a shared knowledge base is 403 (CT4).
        assert client.post(f"/kb/{number}/document/pin", json={"id": "x", "pinned": True}).status_code in (401, 403, 404)
        assert client.post(f"/kb/{number}/ingest", json={"payload": "text"}, headers=OWNER_HEADERS).status_code == 403
        # Settings need the capability ``settings``: the admin (moderator) changes the label; --private is refused.
        assert client.patch(f"/kb/{number}/", json={"label": "World cities"}, headers=OWNER_HEADERS).status_code == 200
        assert client.patch(f"/kb/{number}/", json={"is_public": False}, headers=OWNER_HEADERS).status_code == 400
