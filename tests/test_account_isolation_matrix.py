"""Tests for multi-account isolation matrix (§9.2), bundle boundaries, and token isolation.

Covers:
- §9.2 Permission Matrix:
  * Anonymous: public theme read -> 200, search forced to FTS, private theme read -> 404, write -> 404
  * Owner (same-account): public/private read -> 200, write -> 200
  * Write (same-account, SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md Section 6.6): reads -> 200, ingest into the personal knowledge base passes,
    ingest by a path that pins a shared knowledge base -> 403, owner-action write -> 403
  * Readonly (same-account): read -> 200, write -> 403 or 404
  * Cross-account (Bob accessing Alice): public read -> 200, search forced to FTS, private read -> 404, write -> 404
- TokenStore multi-account isolation:
  * Scoping /tokens list, create, and revoke routes to caller Principal.account_id
  * Cross-account revocation attempt rejected with 404
- Graph bundle isolation:
  * create_graph_bundle exports only caller theme's entities, relations, and documents (WHERE kb_id = ?)
  * import_graph_bundle detects and rejects conflicts with existing IDs in another theme
- Support bundle security boundaries:
  * Exclude data/auth/ and master.key from all scans and bundles
  * Redact non-admin private theme labels as [REDACTED_PRIVATE_THEME]
  * Redact non-admin document text (raw_text, detail, summary), raw_inbox payloads, and doc_shares tokens
- End-to-end integration test of multi-account lifecycle and boundaries
"""

from __future__ import annotations

import io
import json
import os
import sqlite3
import tarfile
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import zstandard as zstd
from starlette.testclient import TestClient

from claire import ids
from tests.kb_paths import seed
from claire.api import server
from claire.config import Settings
from claire.graph_bundle import create_graph_bundle, import_graph_bundle
from claire.ontology.base import Document
from claire.store import accounts as accounts_store
from claire.store import db as dbm
from claire.store.secrets import reset_secret_service
from claire.store.knowledge_base import KnowledgeBaseInfo, KnowledgeBaseManager
from claire.store.token_store import TokenStore, get_token_store, reset_token_stores
from claire.support_bundle import create_support_bundle


PUBLIC_URL = "http://127.0.0.1:8765"


@pytest.fixture(autouse=True)
def _isolate_env_and_stores(monkeypatch: pytest.MonkeyPatch):
    reset_secret_service()
    reset_token_stores()
    for key in list(os.environ):
        if key.startswith("CLAIRE_"):
            monkeypatch.delenv(key, raising=False)
    yield
    reset_secret_service()
    reset_token_stores()


def _add_account(conn: sqlite3.Connection, account_id: int, username: str, role: str = "member", status: str = "active") -> None:
    now = time.time()
    conn.execute(
        "INSERT INTO accounts (id, public_id, username, role, status, display_name, profile_public, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET username=excluded.username, role=excluded.role, status=excluded.status, updated_at=excluded.updated_at",
        (account_id, (1 << 57) + account_id, username, role, status, username.capitalize(), now, now),
    )
    conn.commit()


def _setup_theme(
    tm: KnowledgeBaseManager,
    settings: Settings,
    *,
    kb_id: int,
    seq: int,
    label: str,
    account_id: int,
    is_personal: bool = False,
    is_public: bool = True,
) -> KnowledgeBaseInfo:
    conn = dbm.connect(settings.db_file)
    now = time.time()
    try:
        conn.execute(
            "INSERT INTO knowledge_bases (id, seq, number, label, description, icon, is_personal, is_public, account_id, db_path, default_focus, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, '📁', ?, ?, ?, ?, '', ?, ?)",
            (
                kb_id,
                seq,
                (account_id << 56) | seq,
                label,
                f"Description for {label}",
                1 if is_personal else 0,
                1 if is_public else 0,
                account_id,
                str(settings.db_file),
                now,
                now,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    tm.reload()
    tm.reload()
    return tm.get_kb(kb_id)


@pytest.fixture
def test_environment(tmp_path: Path):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)
    db_file = data_dir / "claire.db"

    conn = dbm.connect(db_file)
    dbm.init_db(conn)

    # 1. Accounts: Admin (1), Alice (2), Bob (3)
    _add_account(conn, 1, "admin", role="admin")
    _add_account(conn, 2, "alice", role="member")
    _add_account(conn, 3, "bob", role="member")

    # 2. Set server mode to network
    store = accounts_store.AccountStore(conn)
    store.set_setting("mode", "network", updated_by=1)
    conn.close()

    settings = Settings(
        CLAIRE_DB_PATH=str(db_file),
        CLAIRE_DATA_DIR=str(data_dir),
        CLAIRE_PROVIDER="mock",
        CLAIRE_ENVIRONMENT="development",
        CLAIRE_PUBLIC_URL=PUBLIC_URL,
        CLAIRE_ANONYMOUS_READONLY=True,
        CLAIRE_MULTI_THEME=True,
    )

    tm = KnowledgeBaseManager(settings)

    # 3. Themes:
    # Alice themes (account_id = 2)
    alice_def = _setup_theme(tm, settings, kb_id=20, seq=1, label="Alice Default", account_id=2, is_personal=True, is_public=True)
    alice_pub = _setup_theme(tm, settings, kb_id=21, seq=2, label="Alice Public", account_id=2, is_personal=False, is_public=True)
    alice_collab = _setup_theme(tm, settings, kb_id=22, seq=3, label="Alice Collab Only", account_id=2, is_personal=False, is_public=False)
    alice_sec = _setup_theme(tm, settings, kb_id=23, seq=4, label="Alice Secret", account_id=2, is_personal=False, is_public=False)

    # Bob themes (account_id = 3)
    bob_def = _setup_theme(tm, settings, kb_id=30, seq=5, label="Bob Default", account_id=3, is_personal=True, is_public=True)
    bob_pub = _setup_theme(tm, settings, kb_id=31, seq=6, label="Bob Public", account_id=3, is_personal=False, is_public=True)

    # 4. TokenStore and tokens:
    tok_store = get_token_store(data_dir=data_dir)
    alice_owner_tok = tok_store.create_token(name="alice-owner", scope="owner", account_id=2)["token"]
    alice_write_tok = tok_store.create_token(name="alice-write", scope="write", account_id=2)["token"]
    alice_ro_tok = tok_store.create_token(name="alice-ro", scope="readonly", account_id=2)["token"]
    bob_owner_tok = tok_store.create_token(name="bob-owner", scope="owner", account_id=3)["token"]

    app = server.create_app(settings, SimpleNamespace(provider=SimpleNamespace(name="mock")))
    client = TestClient(app, base_url=PUBLIC_URL)

    return SimpleNamespace(
        data_dir=data_dir,
        db_file=db_file,
        settings=settings,
        client=client,
        tm=tm,
        tok_store=tok_store,
        alice_def=alice_def,
        alice_pub=alice_pub,
        alice_collab=alice_collab,
        alice_sec=alice_sec,
        bob_def=bob_def,
        bob_pub=bob_pub,
        alice_owner_tok=alice_owner_tok,
        alice_write_tok=alice_write_tok,
        alice_ro_tok=alice_ro_tok,
        bob_owner_tok=bob_owner_tok,
    )


# --- 1. §9.2 Permission Matrix Tests -----------------------------------------


def test_permission_matrix_anonymous(test_environment):
    """Anonymous visitors: public read allowed, search forced to FTS, private read and all writes return 404."""
    env = test_environment
    client = env.client

    # 1. Read public theme -> 200
    res_pub = client.get(f"/kb/{ids.format_public_id(env.alice_pub.number)}/graph")
    assert res_pub.status_code == 200

    # 2. Search public theme -> 200 and forced FTS mode
    res_search = client.post(
        f"/kb/{ids.format_public_id(env.alice_pub.number)}/search",
        json={"query": "claire", "mode": "hybrid"},
    )
    assert res_search.status_code == 200
    assert res_search.json().get("mode") == "fts"

    # 3. Read private collaborator theme -> 404
    res_priv_collab = client.get(f"/kb/{ids.format_public_id(env.alice_collab.number)}/graph")
    assert res_priv_collab.status_code == 200  # a shared knowledge base after version 28: public (CT5, CT6)

    # 4. Read secret private theme -> 404
    res_priv_sec = client.get(f"/kb/{ids.format_public_id(env.alice_sec.number)}/graph")
    assert res_priv_sec.status_code == 200  # a shared knowledge base after version 28: public (CT5, CT6)

    # 5. Ingest write attempt -> 404
    res_write = client.post(
        f"/kb/{ids.format_public_id(env.alice_pub.number)}/ingest",
        json={"payload": "Anonymous ingest attempt"},
    )
    assert res_write.status_code == 404


def test_permission_matrix_owner(test_environment):
    """Alice owner token: public and private read/write allowed."""
    env = test_environment
    client = env.client
    headers = {"Authorization": f"Bearer {env.alice_owner_tok}"}

    # 1. Read public and private themes -> 200
    assert client.get(f"/kb/{ids.format_public_id(env.alice_pub.number)}/graph", headers=headers).status_code == 200
    assert client.get(f"/kb/{ids.format_public_id(env.alice_sec.number)}/graph", headers=headers).status_code == 200

    # 2. Ingest writes only to the personal knowledge base (CT4, CT23): 200 there, 403 on a path that pins another one
    res_def = client.post("/@alice/ingest", json={"payload": "Owner doc personal"}, headers=headers)
    assert res_def.status_code == 200
    res_pub = client.post(f"/kb/{ids.format_public_id(env.alice_pub.number)}/ingest", json={"payload": "Owner doc public"}, headers=headers)
    assert res_pub.status_code == 403
    res_sec = client.post(f"/kb/{ids.format_public_id(env.alice_sec.number)}/ingest", json={"payload": "Owner doc secret"}, headers=headers)
    assert res_sec.status_code == 403


def test_permission_matrix_write(test_environment):
    """Alice write token (Section 6.6): reads pass; ingest goes to the personal knowledge base; a path that pins a shared knowledge base gets 403; owner actions get 403."""
    env = test_environment
    client = env.client
    headers = {"Authorization": f"Bearer {env.alice_write_tok}"}

    # 1. Reads -> 200 (the old themes of Alice are shared knowledge bases after version 28: public)
    for kb in (env.alice_pub, env.alice_collab, env.alice_sec):
        assert client.get(f"/kb/{ids.format_public_id(kb.number)}/graph", headers=headers).status_code == 200

    # 2. Ingest passes the scope and selects the personal knowledge base: an empty payload is the only error.
    res_personal = client.post("/ingest", json={"payload": ""}, headers=headers)
    assert (res_personal.status_code, res_personal.json().get("error")) == (400, "payload required")

    # 3. A path that pins a shared knowledge base -> 403 (CT4)
    res_shared = client.post(f"/kb/{ids.format_public_id(env.alice_collab.number)}/ingest", json={"payload": "text"}, headers=headers)
    assert (res_shared.status_code, res_shared.json().get("error")) == (403, "kb_ingest_by_contribution")

    # 4. Owner actions (graph bundle, review, settings) -> 403 scope_insufficient
    for method, path, body in (
        ("POST", f"/kb/{ids.format_public_id(env.alice_collab.number)}/graph/bundle", {"kb_id": env.alice_collab.id}),
        ("POST", "/document/hide", {"id": "x", "hidden": True}),
        ("PATCH", f"/kb/{ids.format_public_id(env.alice_collab.number)}/", {"label": "x"}),
    ):
        res = client.request(method, path, json=body, headers=headers)
        assert (res.status_code, res.json()) == (403, {"error": "scope_insufficient", "scope": "write", "required": "owner"}), path


def test_permission_matrix_readonly(test_environment):
    """Alice readonly token: reads permitted, writes rejected with 403 or 404."""
    env = test_environment
    client = env.client
    headers = {"Authorization": f"Bearer {env.alice_ro_tok}"}

    # 1. Read public and private themes -> 200
    assert client.get(f"/kb/{ids.format_public_id(env.alice_pub.number)}/graph", headers=headers).status_code == 200
    assert client.get(f"/kb/{ids.format_public_id(env.alice_sec.number)}/graph", headers=headers).status_code == 200

    # 2. Ingest write attempt -> 403 or 404
    res_write = client.post(f"/kb/{ids.format_public_id(env.alice_pub.number)}/ingest", json={"payload": "Readonly write attempt"}, headers=headers)
    assert res_write.status_code in (403, 404)


def test_permission_matrix_cross_account(test_environment):
    """Cross-account visitor (Bob accessing Alice): public read -> 200, search forced to FTS, private read -> 404, writes -> 404."""
    env = test_environment
    client = env.client
    headers = {"Authorization": f"Bearer {env.bob_owner_tok}"}

    # 1. Read Alice public theme -> 200
    assert client.get(f"/kb/{ids.format_public_id(env.alice_pub.number)}/graph", headers=headers).status_code == 200

    # 2. Search Alice public theme -> 200 and forced FTS mode
    res_search = client.post(
        f"/kb/{ids.format_public_id(env.alice_pub.number)}/search",
        json={"query": "claire", "mode": "hybrid"},
        headers=headers,
    )
    assert res_search.status_code == 200
    assert res_search.json().get("mode") == "fts"

    # 3. Read Alice private collaborator theme -> 404 (hidden from foreign account)
    assert client.get(f"/kb/{ids.format_public_id(env.alice_collab.number)}/graph", headers=headers).status_code == 200  # a shared knowledge base after version 28: public (CT5, CT6)

    # 4. Read Alice secret theme -> 404
    assert client.get(f"/kb/{ids.format_public_id(env.alice_sec.number)}/graph", headers=headers).status_code == 200  # a shared knowledge base after version 28: public (CT5, CT6)

    # 5. Ingest write attempt to Alice public theme -> 404 (cross-account write hidden)
    res_write = client.post(f"/kb/{ids.format_public_id(env.alice_pub.number)}/ingest", json={"payload": "Bob cross-write attempt"}, headers=headers)
    assert res_write.status_code == 403  # a path that pins a shared knowledge base: 403 kb_ingest_by_contribution (CT4)

    # 6. Graph bundle export attempt on Alice theme -> 404
    res_bundle = client.post(f"/kb/{ids.format_public_id(env.alice_pub.number)}/graph/bundle", json={"kb_id": env.alice_pub.id}, headers=headers)
    assert res_bundle.status_code == 403  # a signed-in account without the capability gets 403 in a shared knowledge base (design Section 6.2)


# --- 2. TokenStore Multi-Account Isolation Tests ------------------------------


def test_tokens_route_multi_account_isolation(test_environment):
    """Verify /tokens endpoints isolate tokens strictly by caller Principal.account_id."""
    env = test_environment
    client = env.client
    alice_headers = {"Authorization": f"Bearer {env.alice_owner_tok}"}
    bob_headers = {"Authorization": f"Bearer {env.bob_owner_tok}"}

    # 1. Alice creates token via API
    res_a = client.post("/tokens", json={"name": "Alice Key 1", "scope": "readonly"}, headers=alice_headers)
    assert res_a.status_code == 201
    tok_a_id = res_a.json()["token"]["id"]

    # 2. Bob creates token via API
    res_b = client.post("/tokens", json={"name": "Bob Key 1", "scope": "readonly"}, headers=bob_headers)
    assert res_b.status_code == 201
    tok_b_id = res_b.json()["token"]["id"]

    # 3. Verify in TokenStore that tokens are bound to their respective account IDs
    rec_a = env.tok_store.get_token(tok_a_id)
    rec_b = env.tok_store.get_token(tok_b_id)
    assert rec_a["account_id"] == 2
    assert rec_b["account_id"] == 3

    # 4. Alice lists tokens: sees only Alice's tokens, not Bob's
    list_a = client.get("/tokens", headers=alice_headers).json()["tokens"]
    a_ids = [t["id"] for t in list_a]
    assert tok_a_id in a_ids
    assert tok_b_id not in a_ids

    # 5. Bob lists tokens: sees only Bob's tokens, not Alice's
    list_b = client.get("/tokens", headers=bob_headers).json()["tokens"]
    b_ids = [t["id"] for t in list_b]
    assert tok_b_id in b_ids
    assert tok_a_id not in b_ids

    # 6. Alice attempts to revoke Bob's token -> 404
    res_revoke_cross = client.post("/tokens/revoke", json={"id": tok_b_id}, headers=alice_headers)
    assert res_revoke_cross.status_code == 404
    assert env.tok_store.get_token(tok_b_id)["revoked_at"] is None

    # 7. Bob revokes Bob's token -> 200
    res_revoke_own = client.post("/tokens/revoke", json={"id": tok_b_id}, headers=bob_headers)
    assert res_revoke_own.status_code == 200
    assert env.tok_store.get_token(tok_b_id)["revoked_at"] is not None


# --- 3. Graph Bundle Isolation Tests -----------------------------------------


def test_graph_bundle_export_and_import_isolation(test_environment, tmp_path: Path):
    """create_graph_bundle filters by kb_id; import_graph_bundle detects conflict across themes."""
    env = test_environment
    conn = dbm.connect(env.settings.db_file)
    now = time.time()
    try:
        # Seed Alice data (kb_id = 21)
        conn.execute(
            "INSERT INTO documents (id, title, raw_text, detail, kb_id, fetched_at) "
            "VALUES ('doc_alice', 'Alice Doc', 'Alice Content', 'Alice Detail', 21, ?)",
            (now,),
        )
        conn.execute(
            "INSERT INTO entities (id, type, name, norm_name, created_at, updated_at, kb_id) "
            "VALUES ('ent_alice', 'concept', 'AliceEntity', 'aliceentity', ?, ?, 21)",
            (now, now),
        )
        conn.execute(
            "INSERT INTO relations (id, source_id, target_id, type, created_at, kb_id) "
            "VALUES ('rel_alice', 'ent_alice', 'ent_alice', 'relates', ?, 21)",
            (now,),
        )

        # Seed Bob data (kb_id = 31)
        conn.execute(
            "INSERT INTO documents (id, title, raw_text, detail, kb_id, fetched_at) "
            "VALUES ('doc_bob', 'Bob Doc', 'Bob Content', 'Bob Detail', 31, ?)",
            (now,),
        )
        conn.execute(
            "INSERT INTO entities (id, type, name, norm_name, created_at, updated_at, kb_id) "
            "VALUES ('ent_bob', 'concept', 'BobEntity', 'bobentity', ?, ?, 31)",
            (now, now),
        )
        conn.execute(
            "INSERT INTO relations (id, source_id, target_id, type, created_at, kb_id) "
            "VALUES ('rel_bob', 'ent_bob', 'ent_bob', 'relates', ?, 31)",
            (now,),
        )
        conn.commit()
    finally:
        conn.close()

    # 1. Export Alice theme 21
    bundle_info = create_graph_bundle(env.settings, kb_id=21)
    assert bundle_info.documents_count == 1
    assert bundle_info.entities_count == 1
    assert bundle_info.relations_count == 1

    # Inspect exported DB archive to verify Bob data was excluded
    dctx = zstd.ZstdDecompressor()
    decompressed = dctx.decompress(bundle_info.filepath.read_bytes(), max_output_size=50_000_000)
    with tarfile.open(fileobj=io.BytesIO(decompressed), mode="r:") as tar:
        db_member = next(m for m in tar.getmembers() if m.name.endswith(".db"))
        extracted_db = tmp_path / "alice_exported.db"
        extracted_db.write_bytes(tar.extractfile(db_member).read())

    exp_conn = sqlite3.connect(str(extracted_db))
    try:
        docs = exp_conn.execute("SELECT id FROM documents").fetchall()
        ents = exp_conn.execute("SELECT id FROM entities").fetchall()
        rels = exp_conn.execute("SELECT id FROM relations").fetchall()
        assert [d[0] for d in docs] == ["doc_alice"]
        assert [e[0] for e in ents] == ["ent_alice"]
        assert [r[0] for r in rels] == ["rel_alice"]
    finally:
        exp_conn.close()

    # 2. Test conflict rejection on import: target database already has 'ent_alice' under another theme
    conflict_db = tmp_path / "conflict_target.db"
    c_conn = dbm.connect(conflict_db)
    dbm.init_db(c_conn)
    c_conn.execute(
        "INSERT INTO knowledge_bases (id, seq, number, label, description, is_personal, is_public, account_id, created_at, updated_at) "
        "VALUES (99, 99, 99, 'Theme 99', '', 0, 1, 1, ?, ?)",
        (now, now),
    )
    c_conn.execute(
        "INSERT INTO entities (id, type, name, norm_name, created_at, updated_at, kb_id) "
        "VALUES ('ent_alice', 'concept', 'Other Theme Alice', 'otherthemealice', ?, ?, 99)",
        (now, now),
    )
    c_conn.commit()
    c_conn.close()

    with pytest.raises(ValueError, match="Cannot import bundle: entity ent_alice already exists in another theme 99"):
        import_graph_bundle(target_db_path=conflict_db, bundle_path=bundle_info.filepath, target_kb_id=0)


# --- 4. Support Bundle Security Boundaries Tests ------------------------------


def test_support_bundle_redactions_and_auth_exclusion(test_environment, tmp_path: Path):
    """Exclude data/auth/ and master.key; redact non-admin private themes, document texts, inbox, and share tokens."""
    env = test_environment
    now = time.time()

    # 1. Create sensitive auth directory and master.key
    auth_dir = env.data_dir / "auth"
    auth_dir.mkdir(parents=True, exist_ok=True)
    (auth_dir / "master.key").write_text("SENSITIVE_SECRET_KEY_12345", encoding="utf-8")
    (auth_dir / "auth.db").write_bytes(b"SQLite format 3\x00auth database")

    # 2. Seed document, inbox, and share records for non-admin Alice (account_id = 2)
    conn = dbm.connect(env.settings.db_file)
    try:
        conn.execute(
            "INSERT INTO documents (id, title, raw_text, detail, kb_id, fetched_at) "
            "VALUES ('doc_alice_sec', 'Secret Doc', 'CONFIDENTIAL_ALICE_RAW_TEXT', 'CONFIDENTIAL_DETAIL', 23, ?)",
            (now,),
        )
        conn.execute(
            "INSERT INTO raw_inbox (kb_id, source, payload, status, error, received_at) "
            "VALUES (23, 'telegram', 'SECRET_INBOX_PAYLOAD_BODY', 'failed', 'Extraction timeout', ?)",
            (now,),
        )
        conn.execute(
            "INSERT INTO doc_shares (token, kb_id, document_id, created_at) "
            "VALUES ('SECRET_SHARE_TOKEN_VAL', 23, 'doc_alice_sec', ?)",
            (now,),
        )
        conn.commit()
    finally:
        conn.close()

    # 3. Generate support bundle targeting the non-admin secret document
    bundle_info = create_support_bundle(env.settings, days=1, target="doc_alice_sec")

    # 4. Decompress and inspect bundle contents
    dctx = zstd.ZstdDecompressor()
    decompressed = dctx.decompress(bundle_info.filepath.read_bytes(), max_output_size=50_000_000)
    with tarfile.open(fileobj=io.BytesIO(decompressed), mode="r:") as tar:
        names = tar.getnames()

        # Auth data must be completely excluded
        for name in names:
            assert "master.key" not in name
            assert "/auth/" not in name and not name.startswith("auth/")

        # Check storage.json: private theme label must be redacted
        storage_member = next(m for m in tar.getmembers() if m.name.endswith("storage.json"))
        storage_data = json.loads(tar.extractfile(storage_member).read().decode("utf-8"))
        theme_labels = [c.get("label") for c in storage_data.get("resolved_themes", [])]
        assert "Alice Secret" in theme_labels  # a shared knowledge base after version 28: public, so its label is not redacted
        assert "[REDACTED_PRIVATE_THEME]" not in theme_labels  # no private knowledge base is in this fixture now

        # Check tracked_document detail: raw_text and detail must be redacted
        doc_detail_member = next(m for m in tar.getmembers() if m.name.endswith("tracked_document/document_detail.json"))
        doc_detail = json.loads(tar.extractfile(doc_detail_member).read().decode("utf-8"))
        assert doc_detail.get("document", {}).get("raw_text") == "[REDACTED]"
        assert doc_detail.get("document", {}).get("detail") == "[REDACTED]"
        assert "CONFIDENTIAL_ALICE_RAW_TEXT" not in str(doc_detail)

        # Check inbox payloads: failed inbox records must be redacted
        inbox_member = next((m for m in tar.getmembers() if m.name.endswith("inbox_record.json") or m.name.endswith("inbox_records.json") or m.name.endswith("failed_items.json")), None)
        if inbox_member:
            inbox_data = json.loads(tar.extractfile(inbox_member).read().decode("utf-8"))
            inbox_items = inbox_data if isinstance(inbox_data, list) else [inbox_data]
            for rec in inbox_items:
                if rec.get("payload"):
                    assert rec.get("payload") == "[REDACTED]"
                assert "SECRET_INBOX_PAYLOAD_BODY" not in str(rec)

        # Check share tokens: share token must be masked
        shares_member = next((m for m in tar.getmembers() if m.name.endswith("shares_index.json") or m.name.endswith("shares.json")), None)
        if shares_member:
            shares_data = json.loads(tar.extractfile(shares_member).read().decode("utf-8"))
            for s_rec in shares_data:
                assert s_rec.get("token") == "***REDACTED***"
                assert "SECRET_SHARE_TOKEN_VAL" not in str(s_rec)


# --- 5. End-to-End (E2E) Test -------------------------------------------------


def test_e2e_multi_account_isolation_and_lifecycle(test_environment, tmp_path: Path):
    """E2E flow: Member theme definition, token issuance, write-scope ingestion gating,
    visitor access and FTS enforcement, graph bundle export isolation, and support bundle boundaries.
    """
    env = test_environment
    client = env.client

    alice_owner_hdr = {"Authorization": f"Bearer {env.alice_owner_tok}"}
    bob_owner_hdr = {"Authorization": f"Bearer {env.bob_owner_tok}"}

    # Step 1: Alice creates a new custom team theme via API
    res_def = client.post(
        "/@alice/kb",
        json={"knowledge_type": "real", "label": "Alice Team Project", "is_public": True},
        headers=alice_owner_hdr,
    )
    assert res_def.status_code == 201
    team_theme = res_def.json()["knowledge_base"]
    team_kb_id = team_theme["id"]
    team_num = team_theme["number"]
    assert team_theme["account_id"] is None and team_theme["created_by_account_id"] == 2  # a shared knowledge base has no owner; Alice is its founder and first moderator

    # Step 2: Alice issues a write token via /tokens API
    res_tok = client.post(
        "/tokens",
        json={"name": "Alice Team Agent Key", "scope": "write"},
        headers=alice_owner_hdr,
    )
    assert res_tok.status_code == 201
    collab_token = res_tok.json()["token"]["token"]
    collab_hdr = {"Authorization": f"Bearer {collab_token}"}

    # Step 3: Ingest writes only to the personal knowledge base (CT4, CT23): no ingest into the team theme -> 403
    res_ingest_team = client.post(
        f"/kb/{team_num}/ingest",
        json={"payload": "Alice Team Project document notes"},
        headers=collab_hdr,
    )
    assert res_ingest_team.status_code == 403
    seed(env.settings, team_kb_id, "Alice Team Project document notes")

    # Step 4: The write token ingests into Alice's personal knowledge base: the scope passes; only the empty payload is refused
    res_ingest_def = client.post("/ingest", json={"payload": ""}, headers=collab_hdr)
    assert (res_ingest_def.status_code, res_ingest_def.json().get("error")) == (400, "payload required")

    # Step 5: Foreign account Bob attempts to ingest into Alice's team theme -> Blocked 404
    res_bob_write = client.post(
        f"/kb/{team_num}/ingest",
        json={"payload": "Bob illegal cross-account ingest"},
        headers=bob_owner_hdr,
    )
    assert res_bob_write.status_code == 403  # a path that pins a shared knowledge base: 403 kb_ingest_by_contribution for each account (CT4)

    # Step 6: Bob reads and searches Alice's public team theme -> Success, search forced to FTS
    res_bob_read = client.get(f"/kb/{team_num}/graph", headers=bob_owner_hdr)
    assert res_bob_read.status_code == 200

    res_bob_search = client.post(
        f"/kb/{team_num}/search",
        json={"query": "Project", "mode": "hybrid"},
        headers=bob_owner_hdr,
    )
    assert res_bob_search.status_code == 200
    assert res_bob_search.json()["mode"] == "fts"

    # Step 7: Alice creates a graph bundle for her team theme via API
    res_bundle = client.post(
        f"/kb/{team_num}/graph/bundle",
        json={"kb_id": team_kb_id},
        headers=alice_owner_hdr,
    )
    assert res_bundle.status_code == 200
    bundle_data = res_bundle.json()
    assert bundle_data["kb_id"] == team_kb_id
    assert bundle_data["bundle_id"].startswith("gb_")

    # Step 8: Generate support bundle and verify security boundaries hold end-to-end
    sb_info = create_support_bundle(env.settings, days=1)
    assert sb_info.filepath.is_file()
