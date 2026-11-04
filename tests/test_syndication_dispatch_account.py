"""Unit and integration tests for syndication dispatch account attribution and cross-account enforcement."""

from __future__ import annotations

from pathlib import Path

import pytest
from starlette.testclient import TestClient

from claire.api.server import create_app
from claire.config import Settings
from claire.store import accounts as accounts_store
from claire.store import db as dbm
from claire.store.theme import get_theme_manager
from claire.store.token_store import get_token_store

OWNER_TOKEN = "owner-" + ("o" * 32)
READONLY_TOKEN = "readonly-" + ("r" * 32)
COLLAB_TOKEN = "collab-" + ("c" * 32)
OWNER_HEADERS = {"Authorization": f"Bearer {OWNER_TOKEN}"}


@pytest.fixture
def dispatch_account_env(tmp_path: Path):
    data_dir = tmp_path / "data"
    vault_dir = tmp_path / "vault"
    data_dir.mkdir(parents=True)
    vault_dir.mkdir(parents=True)

    db_path = str(data_dir / "claire.db")
    vault_path = str(vault_dir)

    settings = Settings(
        CLAIRE_DATA_DIR=str(data_dir),
        CLAIRE_DB_PATH=db_path,
        CLAIRE_VAULT_PATH=vault_path,
        CLAIRE_PROVIDER="mock",
        CLAIRE_ENVIRONMENT="development",
        CLAIRE_PUBLIC_URL="http://127.0.0.1:8765",
        CLAIRE_INJECT_TOKEN=OWNER_TOKEN,
        CLAIRE_READONLY_TOKEN=READONLY_TOKEN,
        CLAIRE_COLLABORATOR_TOKEN=COLLAB_TOKEN,
        CLAIRE_ANONYMOUS_READONLY=False,
        CLAIRE_MULTI_THEME=True,
    )

    conn = dbm.connect(settings.db_file)
    dbm.init_db(conn)

    # Create Member Account 2 in accounts table
    acc_store = accounts_store.AccountStore(conn)
    member_acc = acc_store.create_member(username="researcher_two", display_name="Researcher Two")
    member_id = member_acc.id
    conn.close()

    # Define themes via ThemeManager:
    # Theme 1 (Account 1, Owner), Theme 2 (Account 1, Owner)
    # Theme 3 (Account 2, Member), Theme 4 (Account 2, Private)
    theme_mgr = get_theme_manager(settings=settings)
    t1 = theme_mgr.define_theme("AI Research", account_id=dbm.ADMIN_ACCOUNT_ID)
    t2 = theme_mgr.define_theme("Security Analysis", account_id=dbm.ADMIN_ACCOUNT_ID)
    t3 = theme_mgr.define_theme("NLP Studies", account_id=member_id, is_public=True, is_collaborator_accessible=True)
    t4 = theme_mgr.define_theme("Private Sandbox", account_id=member_id, is_public=False, is_collaborator_accessible=False)

    # Create API tokens for member account 2
    tok_store = get_token_store(data_dir=settings.data_dir)
    tok_collab = tok_store.create_api_token(name="member2-collab", scope="collaborator", account_id=member_id)
    tok_owner = tok_store.create_api_token(name="member2-owner", scope="owner", account_id=member_id)

    app = create_app(settings=settings)
    client = TestClient(app, base_url=settings.public_url)

    return {
        "client": client,
        "settings": settings,
        "member_id": member_id,
        "theme1_id": t1.id,
        "theme2_id": t2.id,
        "theme3_id": t3.id,
        "theme4_id": t4.id,
        "member_collab_headers": {"Authorization": f"Bearer {tok_collab['token']}"},
        "member_owner_headers": {"Authorization": f"Bearer {tok_owner['token']}"},
    }


def test_syndication_dispatch_records_principal_account_id(dispatch_account_env) -> None:
    """Verify dispatch endpoint records dispatched_by_account_id for authenticated principal."""
    env = dispatch_account_env
    client = env["client"]
    settings = env["settings"]
    t1_id = env["theme1_id"]
    t2_id = env["theme2_id"]

    # Ingest document into Theme 1 by owner
    ingest_res = client.post(
        "/ingest",
        json={"payload": "Research on neural alignment and agentic workflows.", "theme": t1_id, "format": "md"},
        headers=OWNER_HEADERS,
    )
    assert ingest_res.status_code == 200
    doc_id = ingest_res.json()["document_id"]
    assert doc_id

    # Dispatch from Theme 1 to Theme 2 by Owner (Account ID 1 in single mode)
    dispatch_res = client.post(
        "/api/syndication/dispatch",
        json={"document_id": doc_id, "source_theme_id": t1_id, "target_theme_id": t2_id, "focus": "Alignment focus"},
        headers=OWNER_HEADERS,
    )
    assert dispatch_res.status_code == 200
    d_data = dispatch_res.json()
    assert d_data["ok"] is True
    assert d_data["dispatched_by_account_id"] == 1
    dispatch_id = d_data["dispatch_id"]

    # Verify database persistence of dispatched_by_account_id
    conn = dbm.connect_existing(settings.db_file)
    try:
        row = dbm.get_syndication_dispatch(conn, dispatch_id)
        assert row is not None
        assert row["dispatched_by_account_id"] == 1
    finally:
        conn.close()


def test_syndication_dispatch_cross_account_permission_enforcement(dispatch_account_env) -> None:
    """Verify permission enforcement when dispatching cross-account or cross-theme."""
    env = dispatch_account_env
    client = env["client"]
    t1_id = env["theme1_id"]
    t2_id = env["theme2_id"]
    t3_id = env["theme3_id"]
    t4_id = env["theme4_id"]

    # Ingest a document into Theme 1 (Account 1) via API
    res_ingest = client.post(
        "/ingest",
        json={"payload": "Foundational transformer architecture and attention mechanisms.", "theme": t1_id, "format": "md"},
        headers=OWNER_HEADERS,
    )
    assert res_ingest.status_code == 200
    doc1_id = res_ingest.json()["document_id"]

    # Insert a document into Theme 3 (Account 2) via store layer
    from claire.ontology.base import Document, new_id
    doc3_id = new_id("doc")
    doc3 = Document(
        id=doc3_id,
        title="Syntactic Parsing",
        raw_text="Transition-based dependency parsing algorithms and shift-reduce actions.",
        source_type="test",
        content_hash="hash3",
    )
    conn = dbm.connect_existing(env["settings"].db_file)
    try:
        dbm.insert_document(conn, doc3, theme_id=t3_id)
    finally:
        conn.close()

    # 1. Member collaborator on Account 2 attempts to dispatch into Account 1's Theme 2:
    # Target theme belongs to Account 1. Caller belongs to Account 2 with collaborator scope.
    # Must fail with 403 Forbidden.
    res_forbidden = client.post(
        "/api/syndication/dispatch",
        json={"document_id": doc3_id, "source_theme_id": t3_id, "target_theme_id": t2_id},
        headers=env["member_collab_headers"],
    )
    assert res_forbidden.status_code == 403
    assert "다른 계정의 테마에 적재할 권한이 없습니다" in (res_forbidden.json().get("error") or res_forbidden.json().get("detail") or "")

    # 2. Member collaborator on Account 2 attempts to read from private Theme 4 (not collaborator accessible):
    # Must fail with 404 or 403.
    res_inaccessible = client.post(
        "/api/syndication/dispatch",
        json={"document_id": doc1_id, "source_theme_id": t4_id, "target_theme_id": t3_id},
        headers=env["member_collab_headers"],
    )
    assert res_inaccessible.status_code in {403, 404}

    # 3. Instance Owner (admin role) can dispatch across accounts from Theme 1 to Theme 3:
    res_owner_ok = client.post(
        "/api/syndication/dispatch",
        json={"document_id": doc1_id, "source_theme_id": t1_id, "target_theme_id": t3_id, "focus": "Cross-account sync"},
        headers=OWNER_HEADERS,
    )
    # Allowed across accounts for owner (not rejected with 403 Forbidden)
    assert res_owner_ok.status_code == 200
    assert res_owner_ok.json()["dispatched_by_account_id"] == 1
    assert res_owner_ok.json()["dispatch_id"]


def test_syndication_dispatches_query_filtering_by_account_id(dispatch_account_env) -> None:
    """Verify GET /api/syndication/dispatches filters properly by dispatched_by_account_id."""
    env = dispatch_account_env
    client = env["client"]
    settings = env["settings"]
    member_id = env["member_id"]
    t1_id = env["theme1_id"]
    t2_id = env["theme2_id"]

    conn = dbm.connect_existing(settings.db_file)
    try:
        # Create directly via store layer with distinct account IDs
        d_admin = dbm.create_syndication_dispatch(
            conn,
            theme_id=t1_id,
            target_theme_id=t2_id,
            document_id=None,
            mode="cross_theme",
            status="done",
            dispatched_by_account_id=1,
        )
        d_member = dbm.create_syndication_dispatch(
            conn,
            theme_id=t1_id,
            target_theme_id=t2_id,
            document_id=None,
            mode="cross_theme",
            status="done",
            dispatched_by_account_id=member_id,
        )
    finally:
        conn.close()

    # Query without filter: both dispatches appear
    res_all = client.get("/api/syndication/dispatches", headers=OWNER_HEADERS)
    assert res_all.status_code == 200
    all_ids = {d["id"] for d in res_all.json()["dispatches"]}
    assert d_admin in all_ids
    assert d_member in all_ids

    # Query filtered by account_id=1
    res_admin = client.get("/api/syndication/dispatches?account_id=1", headers=OWNER_HEADERS)
    assert res_admin.status_code == 200
    admin_dispatches = res_admin.json()["dispatches"]
    assert any(d["id"] == d_admin for d in admin_dispatches)
    assert not any(d["id"] == d_member for d in admin_dispatches)
    for d in admin_dispatches:
        assert d["dispatched_by_account_id"] == 1

    # Query filtered by dispatched_by_account_id=<member_id>
    res_member = client.get(f"/api/syndication/dispatches?dispatched_by_account_id={member_id}", headers=OWNER_HEADERS)
    assert res_member.status_code == 200
    member_dispatches = res_member.json()["dispatches"]
    assert any(d["id"] == d_member for d in member_dispatches)
    assert not any(d["id"] == d_admin for d in member_dispatches)
    for d in member_dispatches:
        assert d["dispatched_by_account_id"] == member_id
