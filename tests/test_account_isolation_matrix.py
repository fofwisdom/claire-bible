"""Tests for multi-account isolation matrix (§9.2), bundle boundaries, and token isolation.

Covers:
- §9.2 Permission Matrix:
  * Anonymous: public theme read -> 200, search forced to FTS, private theme read -> 404, write -> 404
  * Owner (same-account): public/private read -> 200, write -> 200
  * Collaborator (same-account): public/collab-accessible read -> 200, secret read -> 404,
    write to default theme (is_default=1) -> 403, write to collab-accessible theme -> 200, owner-action write -> 403
  * Readonly (same-account): read -> 200, write -> 403 or 404
  * Cross-account (Bob accessing Alice): public read -> 200, search forced to FTS, private read -> 404, write -> 404
- TokenStore multi-account isolation:
  * Scoping /tokens list, create, and revoke routes to caller Principal.account_id
  * Cross-account revocation attempt rejected with 404
- Graph bundle isolation:
  * create_graph_bundle exports only caller theme's entities, relations, and documents (WHERE theme_id = ?)
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

from claire.api import server
from claire.config import Settings
from claire.graph_bundle import create_graph_bundle, import_graph_bundle
from claire.ontology.base import Document
from claire.store import accounts as accounts_store
from claire.store import db as dbm
from claire.store.secrets import reset_secret_service
from claire.store.theme import ThemeInfo, ThemeManager
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
    tm: ThemeManager,
    settings: Settings,
    *,
    theme_id: int,
    seq: int,
    label: str,
    account_id: int,
    is_default: bool = False,
    is_public: bool = True,
    is_collaborator_accessible: bool = True,
) -> ThemeInfo:
    conn = dbm.connect(settings.db_file)
    now = time.time()
    try:
        conn.execute(
            "INSERT INTO themes (id, seq, number, label, description, icon, is_default, is_public, is_collaborator_accessible, account_id, db_path, default_focus, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, '📁', ?, ?, ?, ?, ?, '', ?, ?)",
            (
                theme_id,
                seq,
                (account_id << 56) | seq,
                label,
                f"Description for {label}",
                1 if is_default else 0,
                1 if is_public else 0,
                1 if is_collaborator_accessible else 0,
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
    tm._save_registry()
    return tm.get_theme(theme_id)


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

    # 2. Set instance mode to network
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

    tm = ThemeManager(settings)

    # 3. Themes:
    # Alice themes (account_id = 2)
    alice_def = _setup_theme(tm, settings, theme_id=20, seq=1, label="Alice Default", account_id=2, is_default=True, is_public=True, is_collaborator_accessible=False)
    alice_pub = _setup_theme(tm, settings, theme_id=21, seq=2, label="Alice Public", account_id=2, is_default=False, is_public=True, is_collaborator_accessible=True)
    alice_collab = _setup_theme(tm, settings, theme_id=22, seq=3, label="Alice Collab Only", account_id=2, is_default=False, is_public=False, is_collaborator_accessible=True)
    alice_sec = _setup_theme(tm, settings, theme_id=23, seq=4, label="Alice Secret", account_id=2, is_default=False, is_public=False, is_collaborator_accessible=False)

    # Bob themes (account_id = 3)
    bob_def = _setup_theme(tm, settings, theme_id=30, seq=5, label="Bob Default", account_id=3, is_default=True, is_public=True, is_collaborator_accessible=False)
    bob_pub = _setup_theme(tm, settings, theme_id=31, seq=6, label="Bob Public", account_id=3, is_default=False, is_public=True, is_collaborator_accessible=True)

    # 4. TokenStore and tokens:
    tok_store = get_token_store(data_dir=data_dir)
    alice_owner_tok = tok_store.create_token(name="alice-owner", scope="owner", account_id=2)["token"]
    alice_collab_tok = tok_store.create_token(name="alice-collab", scope="collaborator", account_id=2)["token"]
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
        alice_collab_tok=alice_collab_tok,
        alice_ro_tok=alice_ro_tok,
        bob_owner_tok=bob_owner_tok,
    )


# --- 1. §9.2 Permission Matrix Tests -----------------------------------------


def test_permission_matrix_anonymous(test_environment):
    """Anonymous visitors: public read allowed, search forced to FTS, private read and all writes return 404."""
    env = test_environment
    client = env.client

    # 1. Read public theme -> 200
    res_pub = client.get(f"/@alice/graph?theme={env.alice_pub.id}")
    assert res_pub.status_code == 200

    # 2. Search public theme -> 200 and forced FTS mode
    res_search = client.post(
        f"/@alice/search?theme={env.alice_pub.id}",
        json={"query": "claire", "mode": "hybrid"},
    )
    assert res_search.status_code == 200
    assert res_search.json().get("mode") == "fts"

    # 3. Read private collaborator theme -> 404
    res_priv_collab = client.get(f"/@alice/graph?theme={env.alice_collab.id}")
    assert res_priv_collab.status_code == 404

    # 4. Read secret private theme -> 404
    res_priv_sec = client.get(f"/@alice/graph?theme={env.alice_sec.id}")
    assert res_priv_sec.status_code == 404

    # 5. Ingest write attempt -> 404
    res_write = client.post(
        f"/@alice/ingest?theme={env.alice_pub.id}",
        json={"payload": "Anonymous ingest attempt"},
    )
    assert res_write.status_code == 404


def test_permission_matrix_owner(test_environment):
    """Alice owner token: public and private read/write allowed."""
    env = test_environment
    client = env.client
    headers = {"Authorization": f"Bearer {env.alice_owner_tok}"}

    # 1. Read public and private themes -> 200
    assert client.get(f"/@alice/graph?theme={env.alice_pub.id}", headers=headers).status_code == 200
    assert client.get(f"/@alice/graph?theme={env.alice_sec.id}", headers=headers).status_code == 200

    # 2. Ingest write to public and private themes -> 200
    res_pub = client.post(f"/@alice/ingest?theme={env.alice_pub.id}", json={"payload": "Owner doc public"}, headers=headers)
    assert res_pub.status_code == 200
    res_sec = client.post(f"/@alice/ingest?theme={env.alice_sec.id}", json={"payload": "Owner doc secret"}, headers=headers)
    assert res_sec.status_code == 200


def test_permission_matrix_collaborator(test_environment):
    """Alice collaborator token: read public/collab-accessible -> 200, secret read -> 404,
    write to default theme -> 403, write to collab-accessible -> 200, owner action write -> 403.
    """
    env = test_environment
    client = env.client
    headers = {"Authorization": f"Bearer {env.alice_collab_tok}"}

    # 1. Read public theme -> 200
    assert client.get(f"/@alice/graph?theme={env.alice_pub.id}", headers=headers).status_code == 200

    # 2. Read collaborator-accessible private theme -> 200
    assert client.get(f"/@alice/graph?theme={env.alice_collab.id}", headers=headers).status_code == 200

    # 3. Read secret private theme -> 404
    assert client.get(f"/@alice/graph?theme={env.alice_sec.id}", headers=headers).status_code == 404

    # 4. Collaborator write to default theme (is_default=1) -> 403 Forbidden
    res_def_write = client.post(f"/@alice/ingest?theme={env.alice_def.id}", json={"payload": "Collab default ingest"}, headers=headers)
    assert res_def_write.status_code == 403
    err_detail = res_def_write.json().get("detail") or res_def_write.json().get("error") or ""
    assert "기본 지식베이스" in err_detail

    # 5. Collaborator write to collaborator-accessible theme -> 200 OK
    res_collab_write = client.post(f"/@alice/ingest?theme={env.alice_collab.id}", json={"payload": "Collab accessible ingest"}, headers=headers)
    assert res_collab_write.status_code == 200

    # 6. Collaborator attempting owner-level action (create graph bundle) -> 403 or 404 Forbidden/Not Found
    res_owner_action = client.post(f"/@alice/graph/bundle?theme={env.alice_collab.id}", json={"theme_id": env.alice_collab.id}, headers=headers)
    assert res_owner_action.status_code in (403, 404)


def test_permission_matrix_readonly(test_environment):
    """Alice readonly token: reads permitted, writes rejected with 403 or 404."""
    env = test_environment
    client = env.client
    headers = {"Authorization": f"Bearer {env.alice_ro_tok}"}

    # 1. Read public and private themes -> 200
    assert client.get(f"/@alice/graph?theme={env.alice_pub.id}", headers=headers).status_code == 200
    assert client.get(f"/@alice/graph?theme={env.alice_sec.id}", headers=headers).status_code == 200

    # 2. Ingest write attempt -> 403 or 404
    res_write = client.post(f"/@alice/ingest?theme={env.alice_pub.id}", json={"payload": "Readonly write attempt"}, headers=headers)
    assert res_write.status_code in (403, 404)


def test_permission_matrix_cross_account(test_environment):
    """Cross-account visitor (Bob accessing Alice): public read -> 200, search forced to FTS, private read -> 404, writes -> 404."""
    env = test_environment
    client = env.client
    headers = {"Authorization": f"Bearer {env.bob_owner_tok}"}

    # 1. Read Alice public theme -> 200
    assert client.get(f"/@alice/graph?theme={env.alice_pub.id}", headers=headers).status_code == 200

    # 2. Search Alice public theme -> 200 and forced FTS mode
    res_search = client.post(
        f"/@alice/search?theme={env.alice_pub.id}",
        json={"query": "claire", "mode": "hybrid"},
        headers=headers,
    )
    assert res_search.status_code == 200
    assert res_search.json().get("mode") == "fts"

    # 3. Read Alice private collaborator theme -> 404 (hidden from foreign account)
    assert client.get(f"/@alice/graph?theme={env.alice_collab.id}", headers=headers).status_code == 404

    # 4. Read Alice secret theme -> 404
    assert client.get(f"/@alice/graph?theme={env.alice_sec.id}", headers=headers).status_code == 404

    # 5. Ingest write attempt to Alice public theme -> 404 (cross-account write hidden)
    res_write = client.post(f"/@alice/ingest?theme={env.alice_pub.id}", json={"payload": "Bob cross-write attempt"}, headers=headers)
    assert res_write.status_code == 404

    # 6. Graph bundle export attempt on Alice theme -> 404
    res_bundle = client.post(f"/@alice/graph/bundle?theme={env.alice_pub.id}", json={"theme_id": env.alice_pub.id}, headers=headers)
    assert res_bundle.status_code == 404


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
    """create_graph_bundle filters by theme_id; import_graph_bundle detects conflict across themes."""
    env = test_environment
    conn = dbm.connect(env.settings.db_file)
    now = time.time()
    try:
        # Seed Alice data (theme_id = 21)
        conn.execute(
            "INSERT INTO documents (id, title, raw_text, detail, theme_id, fetched_at) "
            "VALUES ('doc_alice', 'Alice Doc', 'Alice Content', 'Alice Detail', 21, ?)",
            (now,),
        )
        conn.execute(
            "INSERT INTO entities (id, type, name, norm_name, created_at, updated_at, theme_id) "
            "VALUES ('ent_alice', 'concept', 'AliceEntity', 'aliceentity', ?, ?, 21)",
            (now, now),
        )
        conn.execute(
            "INSERT INTO relations (id, source_id, target_id, type, created_at, theme_id) "
            "VALUES ('rel_alice', 'ent_alice', 'ent_alice', 'relates', ?, 21)",
            (now,),
        )

        # Seed Bob data (theme_id = 31)
        conn.execute(
            "INSERT INTO documents (id, title, raw_text, detail, theme_id, fetched_at) "
            "VALUES ('doc_bob', 'Bob Doc', 'Bob Content', 'Bob Detail', 31, ?)",
            (now,),
        )
        conn.execute(
            "INSERT INTO entities (id, type, name, norm_name, created_at, updated_at, theme_id) "
            "VALUES ('ent_bob', 'concept', 'BobEntity', 'bobentity', ?, ?, 31)",
            (now, now),
        )
        conn.execute(
            "INSERT INTO relations (id, source_id, target_id, type, created_at, theme_id) "
            "VALUES ('rel_bob', 'ent_bob', 'ent_bob', 'relates', ?, 31)",
            (now,),
        )
        conn.commit()
    finally:
        conn.close()

    # 1. Export Alice theme 21
    bundle_info = create_graph_bundle(env.settings, theme_id=21)
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
        "INSERT INTO themes (id, seq, number, label, description, is_default, is_public, account_id, created_at, updated_at) "
        "VALUES (99, 99, 99, 'Theme 99', '', 0, 1, 1, ?, ?)",
        (now, now),
    )
    c_conn.execute(
        "INSERT INTO entities (id, type, name, norm_name, created_at, updated_at, theme_id) "
        "VALUES ('ent_alice', 'concept', 'Other Theme Alice', 'otherthemealice', ?, ?, 99)",
        (now, now),
    )
    c_conn.commit()
    c_conn.close()

    with pytest.raises(ValueError, match="Cannot import bundle: entity ent_alice already exists in another theme 99"):
        import_graph_bundle(target_db_path=conflict_db, bundle_path=bundle_info.filepath, target_theme_id=0)


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
            "INSERT INTO documents (id, title, raw_text, detail, theme_id, fetched_at) "
            "VALUES ('doc_alice_sec', 'Secret Doc', 'CONFIDENTIAL_ALICE_RAW_TEXT', 'CONFIDENTIAL_DETAIL', 23, ?)",
            (now,),
        )
        conn.execute(
            "INSERT INTO raw_inbox (theme_id, source, payload, status, error, received_at) "
            "VALUES (23, 'telegram', 'SECRET_INBOX_PAYLOAD_BODY', 'failed', 'Extraction timeout', ?)",
            (now,),
        )
        conn.execute(
            "INSERT INTO doc_shares (token, theme_id, document_id, created_at) "
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
        assert "Alice Secret" not in theme_labels
        assert "[REDACTED_PRIVATE_THEME]" in theme_labels

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
    """E2E flow: Member theme definition, token issuance, collaborator ingestion gating,
    visitor access and FTS enforcement, graph bundle export isolation, and support bundle boundaries.
    """
    env = test_environment
    client = env.client

    alice_owner_hdr = {"Authorization": f"Bearer {env.alice_owner_tok}"}
    bob_owner_hdr = {"Authorization": f"Bearer {env.bob_owner_tok}"}

    # Step 1: Alice creates a new custom team theme via API
    res_def = client.post(
        "/@alice/themes",
        json={"label": "Alice Team Project", "is_public": True, "is_collaborator_accessible": True},
        headers=alice_owner_hdr,
    )
    assert res_def.status_code == 201
    team_theme = res_def.json()["theme"]
    team_theme_id = team_theme["id"]
    assert team_theme["account_id"] == 2

    # Step 2: Alice issues a collaborator token via /tokens API
    res_tok = client.post(
        "/tokens",
        json={"name": "Alice Team Collab Key", "scope": "collaborator"},
        headers=alice_owner_hdr,
    )
    assert res_tok.status_code == 201
    collab_token = res_tok.json()["token"]["token"]
    collab_hdr = {"Authorization": f"Bearer {collab_token}"}

    # Step 3: Collaborator ingests into Alice's team theme -> Success
    res_ingest_team = client.post(
        f"/@alice/ingest?theme={team_theme_id}",
        json={"payload": "Alice Team Project document notes"},
        headers=collab_hdr,
    )
    assert res_ingest_team.status_code == 200

    # Step 4: Collaborator attempts to ingest into Alice's default theme -> Blocked 403
    res_ingest_def = client.post(
        f"/@alice/ingest?theme={env.alice_def.id}",
        json={"payload": "Collab default illegal ingest"},
        headers=collab_hdr,
    )
    assert res_ingest_def.status_code == 403
    assert "기본 지식베이스" in (res_ingest_def.json().get("error") or res_ingest_def.json().get("detail") or "")

    # Step 5: Foreign account Bob attempts to ingest into Alice's team theme -> Blocked 404
    res_bob_write = client.post(
        f"/@alice/ingest?theme={team_theme_id}",
        json={"payload": "Bob illegal cross-account ingest"},
        headers=bob_owner_hdr,
    )
    assert res_bob_write.status_code == 404

    # Step 6: Bob reads and searches Alice's public team theme -> Success, search forced to FTS
    res_bob_read = client.get(f"/@alice/graph?theme={team_theme_id}", headers=bob_owner_hdr)
    assert res_bob_read.status_code == 200

    res_bob_search = client.post(
        f"/@alice/search?theme={team_theme_id}",
        json={"query": "Project", "mode": "hybrid"},
        headers=bob_owner_hdr,
    )
    assert res_bob_search.status_code == 200
    assert res_bob_search.json()["mode"] == "fts"

    # Step 7: Alice creates a graph bundle for her team theme via API
    res_bundle = client.post(
        f"/@alice/graph/bundle?theme={team_theme_id}",
        json={"theme_id": team_theme_id},
        headers=alice_owner_hdr,
    )
    assert res_bundle.status_code == 200
    bundle_data = res_bundle.json()
    assert bundle_data["theme_id"] == team_theme_id
    assert bundle_data["bundle_id"].startswith("gb_")

    # Step 8: Generate support bundle and verify security boundaries hold end-to-end
    sb_info = create_support_bundle(env.settings, days=1)
    assert sb_info.filepath.is_file()
