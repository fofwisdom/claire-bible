"""Tests for Track D AE4: per-account extraction_mode, untruncated client staging, zero server LLM execution, and pending document isolation."""

from __future__ import annotations

import hashlib
import os
import sqlite3
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from starlette.testclient import TestClient

from claire.api import server
from claire.core.config import Settings
from claire.collaboration.contributions import ContributionError, contribute_direct, curate
from claire.extract.provider import MockProvider
from claire.ingest import pipeline
from claire.ingest.pipeline import STAGED_INGEST_MESSAGE, stage_raw
from claire.ingest.service import IngestService
from claire.ontology.base import Document, Entity
from claire.store import accounts as accounts_store
from claire.store import db as dbm
from claire.store import queries
from claire.store.knowledge_base import KnowledgeBaseManager
from claire.store.raw import load_artifact
from claire.store.secrets import reset_secret_service
from claire.store.token_store import get_token_store, reset_token_stores
from claire.store.vectors import make_vector_store
from claire.telegram.bot import _settle_status, build_app, parse_force_provider_flag

PUBLIC_URL = "http://127.0.0.1:8765"


class _SpyProvider:
    """Stub provider that fails if client staging triggers any server LLM or classifier call."""

    name = "spy-provider"
    model = "spy-model"
    effort = "medium"

    def __init__(self) -> None:
        self.calls: list[str] = []

    def __getattr__(self, item: str) -> Any:
        def _forbidden(*args: Any, **kwargs: Any) -> Any:
            self.calls.append(item)
            raise AssertionError(f"server provider method {item!r} was called during client staging")

        return _forbidden


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> Any:
    reset_secret_service()
    reset_token_stores()
    for key in list(os.environ):
        if key.startswith(("CLAIRE_", "CB_")):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("CLAIRE_SECRET_KEY", "77" * 32)
    yield
    reset_secret_service()
    reset_token_stores()


def _add_account(conn: sqlite3.Connection, account_id: int, username: str, *, role: str = "member", extraction_mode: str = "provider") -> None:
    now = time.time()
    conn.execute(
        "INSERT INTO accounts (id, public_id, username, role, status, display_name, profile_public, created_at, activated_at, updated_at, extraction_mode) "
        "VALUES (?, ?, ?, ?, 'active', ?, 1, ?, ?, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET username=excluded.username, role=excluded.role, status='active', profile_public=1, extraction_mode=excluded.extraction_mode",
        (account_id, (1 << 57) + account_id, username, role, username.capitalize(), now, now, now, extraction_mode),
    )


def _add_kb(conn: sqlite3.Connection, db_file: Path, *, kb_id: int, seq: int, label: str, account_id: int | None, personal: bool) -> None:
    now = time.time()
    conn.execute(
        "INSERT INTO knowledge_bases (id, seq, number, label, description, icon, is_personal, is_public, account_id, db_path, default_focus, knowledge_type, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, '', '📁', ?, 1, ?, ?, '', ?, ?, ?)",
        (kb_id, seq, (1 << 56) | seq, label, 1 if personal else 0, account_id, str(db_file), None if personal else "real", now, now),
    )


def _setup_network_env(tmp_path: Path, *, alice_mode: str = "provider") -> SimpleNamespace:
    data_dir = tmp_path / "data"
    vault_dir = tmp_path / "vault"
    data_dir.mkdir(parents=True, exist_ok=True)
    vault_dir.mkdir(parents=True, exist_ok=True)
    db_file = data_dir / "claire.db"
    conn = dbm.connect(db_file)
    dbm.init_db(conn)
    _add_account(conn, 1, "admin", role="admin")
    _add_account(conn, 2, "alice", extraction_mode=alice_mode)
    _add_account(conn, 3, "bob", extraction_mode="provider")
    store = accounts_store.AccountStore(conn)
    store.set_setting("mode", "network", updated_by=1)
    store.set_federation_state("on", updated_by=1)
    _add_kb(conn, db_file, kb_id=20, seq=1, label="Alice KB", account_id=2, personal=True)
    _add_kb(conn, db_file, kb_id=30, seq=2, label="Bob KB", account_id=3, personal=True)
    _add_kb(conn, db_file, kb_id=40, seq=3, label="Shared KB", account_id=None, personal=False)
    conn.commit()
    conn.close()

    settings = Settings(
        CLAIRE_DB_PATH=str(db_file),
        CLAIRE_DATA_DIR=str(data_dir),
        CLAIRE_VAULT_PATH=str(vault_dir),
        CLAIRE_PROVIDER="mock",
        CLAIRE_ENVIRONMENT="development",
        CLAIRE_PUBLIC_URL=PUBLIC_URL,
        CLAIRE_ANONYMOUS_READONLY=True,
        CLAIRE_MULTI_THEME=True,
        CLAIRE_FQDN="127.0.0.1:8765",
    )
    KnowledgeBaseManager(settings).reload()
    tokens = get_token_store(data_dir=data_dir)
    alice_tok = tokens.create_token(name="alice", scope="owner", account_id=2)["token"]
    bob_tok = tokens.create_token(name="bob", scope="owner", account_id=3)["token"]
    admin_tok = tokens.create_token(name="admin", scope="owner", account_id=1)["token"]
    return SimpleNamespace(
        settings=settings,
        data_dir=data_dir,
        db_file=db_file,
        alice_headers={"Authorization": f"Bearer {alice_tok}", "Origin": PUBLIC_URL},
        bob_headers={"Authorization": f"Bearer {bob_tok}", "Origin": PUBLIC_URL},
        admin_headers={"Authorization": f"Bearer {admin_tok}", "Origin": PUBLIC_URL},
        anonymous_headers={},
        alice_public_id=str((1 << 57) + 2),
        bob_public_id=str((1 << 57) + 3),
    )


def test_account_store_and_me_api_extraction_mode_interface(tmp_path: Path) -> None:
    """Verify AccountStore and GET/PATCH /api/me expose, update, and validate extraction_mode."""
    env = _setup_network_env(tmp_path)
    app = server.create_app(env.settings, SimpleNamespace(provider=SimpleNamespace(name="mock")))

    conn = dbm.connect_existing(env.db_file)
    try:
        store = accounts_store.AccountStore(conn)
        assert store.get(2).extraction_mode == "provider"
        with pytest.raises(ValueError, match="invalid_extraction_mode"):
            store.update_extraction_mode(2, "invalid")
    finally:
        conn.close()

    with TestClient(app, base_url=PUBLIC_URL) as client:
        me_initial = client.get("/api/me", headers=env.alice_headers)
        assert me_initial.status_code == 200
        assert me_initial.json()["extraction_mode"] == "provider"

        bad = client.patch("/api/me", json={"extraction_mode": "local_bad"}, headers=env.alice_headers)
        assert bad.status_code == 400
        assert bad.json()["error"] == "invalid_extraction_mode"

        bad_type = client.patch("/api/me", json={"extraction_mode": 123}, headers=env.alice_headers)
        assert bad_type.status_code == 400
        assert bad_type.json()["error"] == "invalid_extraction_mode"

        updated = client.patch("/api/me", json={"extraction_mode": "client"}, headers=env.alice_headers)
        assert updated.status_code == 200
        assert updated.json()["extraction_mode"] == "client"
        assert client.get("/api/me", headers=env.alice_headers).json()["extraction_mode"] == "client"

        # Other account keeps default provider mode.
        assert client.get("/api/me", headers=env.bob_headers).json()["extraction_mode"] == "provider"


def test_stage_raw_preserves_untruncated_content_and_txt_zst_archive(tmp_path: Path) -> None:
    """Verify stage_raw stores full untruncated content, SHA-256 hash, raw_chars, .txt.zst archive, and staged inbox state."""
    env = _setup_network_env(tmp_path, alice_mode="client")
    long_text = "Segment-Alpha " * 4500
    expected_sha = hashlib.sha256(long_text.strip().encode("utf-8")).hexdigest()
    seen_full_content: list[bool] = []
    seen_fetch_flags: list[tuple[bool, bool]] = []

    def _fake_fetch(payload: str, *, full_content: bool = False) -> Document:
        from claire.ingest.fetchers.base import _FETCH_SETTINGS

        bound = _FETCH_SETTINGS.get()
        seen_full_content.append(full_content)
        seen_fetch_flags.append((bool(getattr(bound, "enable_vlm_transcription", False)), bool(getattr(bound, "enable_video_transcription", False))))
        return Document(
            url=payload,
            canonical_url=payload,
            title="Long Client Spec",
            raw_text=long_text,
            source_type="web",
            content_hash="",
        )

    noisy_settings = env.settings.model_copy(update={"enable_vlm_transcription": True, "enable_video_transcription": True})
    conn = dbm.connect_existing(env.db_file)
    try:
        report = stage_raw(
            "https://docs.example/long-spec",
            conn=conn,
            fetch_fn=_fake_fetch,
            data_dir=env.data_dir,
            source="mcp",
            kb_id=20,
            settings=noisy_settings,
            account_id=2,
        )
        assert report.error is None
        assert report.staged is True
        assert report.extraction_status == "pending"
        assert report.message == STAGED_INGEST_MESSAGE
        assert report.raw_chars == len(long_text)
        assert report.content_hash and len(report.content_hash) == 64
        assert seen_full_content == [True]
        assert seen_fetch_flags == [(False, False)]

        doc_row = dbm.get_document_row(conn, report.document_id, kb_id=20)
        assert doc_row is not None
        assert doc_row["extraction_status"] == "pending"
        assert doc_row["raw_chars"] == len(long_text)
        assert len(doc_row["content_hash"]) == 64
        assert doc_row["raw_text"] == long_text
        assert load_artifact(env.data_dir, report.document_id) == long_text

        inbox_row = dbm.get_inbox(conn, report.inbox_id, kb_id=20)
        assert inbox_row is not None
        assert inbox_row["status"] == "staged"
        assert inbox_row["document_id"] == report.document_id
        assert conn.execute("SELECT COUNT(*) FROM extractions WHERE document_id=?", (report.document_id,)).fetchone()[0] == 0
        assert expected_sha != ""
    finally:
        conn.close()


def test_client_mode_ingest_service_and_recover_isolation_and_force_provider(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify client-mode ingest performs zero provider calls, recover_failed skips staged rows, and force_provider overrides cleanly."""
    env = _setup_network_env(tmp_path, alice_mode="client")
    spy = _SpyProvider()
    monkeypatch.setattr("claire.ingest.pipeline.default_fetch", lambda payload, full_content=False: Document(url=payload if payload.startswith("http") else None, canonical_url=payload if payload.startswith("http") else None, title="PDF Spec", raw_text=f"Full payload for {payload} (full_content={full_content})", source_type="pdf", content_hash=hashlib.sha256(payload.encode()).hexdigest()))

    # Direct pipeline.ingest on client-mode personal KB (even with full_content=False passed).
    conn = dbm.connect_existing(env.db_file)
    try:
        vstore = make_vector_store(conn, "brute", kb_id=20)
        rep1 = pipeline.ingest(
            "https://example.com/spec.pdf",
            conn=conn,
            provider=spy,
            vstore=vstore,
            data_dir=env.data_dir,
            source="api",
            full_content=False,
            kb_id=20,
            settings=env.settings,
        )
        assert rep1.error is None
        assert rep1.staged is True
        assert rep1.full_content is True
        assert rep1.extraction_status == "pending"
        assert rep1.message == STAGED_INGEST_MESSAGE
        assert spy.calls == []
    finally:
        conn.close()

    # IngestService on member account without any server key or tier still stages cleanly without LLM quota failure.
    svc = IngestService(env.settings, provider=spy, kb_id=20, account_id=2)
    rep2 = svc.ingest("https://example.com/article-two", source="telegram")
    assert rep2.error is None
    assert rep2.staged is True
    assert rep2.message == STAGED_INGEST_MESSAGE
    assert spy.calls == []

    # recover_failed and due_for_recovery must skip staged rows.
    conn2 = dbm.connect_existing(env.db_file)
    try:
        assert dbm.due_for_recovery(conn2, max_attempts=5, kb_id=20) == []
        assert dbm.pending_inbox(conn2, kb_id=20) == []
    finally:
        conn2.close()
    assert svc.recover_failed() == []
    assert spy.calls == []

    # force_provider=True overrides client mode and completes extraction of the previously staged document.
    mock_prov = MockProvider()
    conn3 = dbm.connect_existing(env.db_file)
    try:
        vstore3 = make_vector_store(conn3, "brute", kb_id=20)
        forced_rep = pipeline.ingest(
            "https://example.com/spec.pdf",
            conn=conn3,
            provider=mock_prov,
            vstore=vstore3,
            data_dir=env.data_dir,
            source="cli",
            kb_id=20,
            settings=env.settings,
            force_provider=True,
        )
        assert forced_rep.error is None
        assert forced_rep.staged is False
        assert forced_rep.document_id == rep1.document_id
        assert forced_rep.extraction_status == "extracted"
        doc_row = dbm.get_document_row(conn3, rep1.document_id, kb_id=20)
        assert doc_row is not None
        assert doc_row["extraction_status"] == "extracted"
        assert conn3.execute("SELECT COUNT(*) FROM extractions WHERE document_id=? AND kb_id=20", (rep1.document_id,)).fetchone()[0] >= 1
    finally:
        conn3.close()


@pytest.mark.asyncio
async def test_cli_and_telegram_interfaces_for_client_staging_and_force_provider(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """Verify CLI cmd_ingest/reingest and Telegram bot handle client staging without share links and honor --force-provider."""
    from claire.cli import build_parser, cmd_ingest

    env = _setup_network_env(tmp_path, alice_mode="client")
    monkeypatch.setattr("claire.ingest.pipeline.default_fetch", lambda payload, full_content=False: Document(url=payload, canonical_url=payload, title="CLI Note", raw_text=f"text for {payload}", source_type="web", content_hash=hashlib.sha256(payload.encode()).hexdigest()))
    personal_settings = env.settings.model_copy(update={"account_id": 2})
    theme_obj = SimpleNamespace(id=20, icon="📁", label="Alice KB")
    monkeypatch.setattr("claire.cli.get_effective_settings", lambda args, doc_target=None: (personal_settings, theme_obj))

    def _fail_get_provider(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("get_provider must not be called in client staging CLI ingest")

    monkeypatch.setattr("claire.extract.provider.get_provider", _fail_get_provider)
    parser = build_parser()
    args = parser.parse_args(["ingest", "https://example.com/cli-staged"])
    rc = cmd_ingest(args)
    assert rc == 0
    captured = capsys.readouterr().out
    assert "(mode=client-staging)" in captured
    assert STAGED_INGEST_MESSAGE in captured

    # CLI --force-provider invokes provider cleanly.
    monkeypatch.setattr("claire.extract.provider.get_provider", lambda s: MockProvider())
    args_forced = parser.parse_args(["ingest", "--force-provider", "https://example.com/cli-forced"])
    rc_forced = cmd_ingest(args_forced)
    assert rc_forced == 0
    assert "(provider=mock)" in capsys.readouterr().out

    # Telegram flag parsing and _settle_status staged handling (no public share link created).
    cleaned, forced = parse_force_provider_flag("https://example.com/a --force-provider")
    assert forced is True and cleaned == "https://example.com/a"
    _, not_forced = parse_force_provider_flag("https://example.com/a")
    assert not_forced is False

    status_msg = AsyncMock()
    orig_msg = AsyncMock()
    await _settle_status(
        status_msg,
        orig_msg,
        STAGED_INGEST_MESSAGE,
        [],
        has_error=False,
        is_duplicate=False,
        is_staged=True,
        retry_doc_id="doc-any",
        kb_id=20,
        db_file=env.db_file,
        settings=env.settings,
    )
    status_msg.edit_text.assert_awaited_once()
    call_kwargs = status_msg.edit_text.await_args.kwargs
    assert "reply_markup" not in call_kwargs
    conn = dbm.connect_existing(env.db_file)
    try:
        assert conn.execute("SELECT COUNT(*) FROM doc_shares").fetchone()[0] == 0
    finally:
        conn.close()

    # Telegram on_ingest forwards --force-provider to target_svc.ingest.
    tg_settings = env.settings.model_copy(update={"telegram_bot_token": "12345:token", "allowed_user_ids": []})
    bot_app = build_app(tg_settings)
    on_ingest = next(h.callback for h in bot_app.handlers[0] if getattr(h.callback, "__name__", "") == "on_ingest")
    seen_force_flags: list[bool] = []

    def _spy_svc_ingest(self, payload: str, **kwargs: Any) -> pipeline.IngestReport:
        seen_force_flags.append(bool(kwargs.get("force_provider", False)))
        return pipeline.IngestReport(document_id="doc_tg_1", title="TG", staged=not kwargs.get("force_provider", False), message=STAGED_INGEST_MESSAGE)

    monkeypatch.setattr("claire.ingest.service.IngestService.ingest", _spy_svc_ingest)
    msg = AsyncMock()
    msg.reply_text = AsyncMock(return_value=AsyncMock())
    msg.set_reaction = AsyncMock()
    upd = MagicMock()
    upd.effective_user = SimpleNamespace(id=100)
    upd.effective_chat = SimpleNamespace(id=200)
    upd.message = msg
    upd.update_id = 999
    ctx = MagicMock()
    ctx.args = ["https://example.com/tg-item", "#1", "--force-provider"]
    await on_ingest(upd, ctx)
    assert seen_force_flags == [True]


def test_e2e_pending_document_isolation_across_reads_publish_and_contributions(tmp_path: Path) -> None:
    """End-to-end test: pending staged documents stay visible to owner only and blocked from anonymous, cross-account, sitemap, publish, and contributions until extracted."""
    env = _setup_network_env(tmp_path, alice_mode="provider")
    app = server.create_app(env.settings, SimpleNamespace(provider=MockProvider()))

    conn = dbm.connect_existing(env.db_file)
    try:
        # Baseline extracted document in Alice's personal KB so public KB baseline works.
        extracted_doc = Document(id="doc-ready", url="https://ready.example/a", canonical_url="https://ready.example/a", title="Ready Doc", raw_text="Ready body", source_type="web", content_hash="h_ready")
        dbm.insert_document(conn, extracted_doc, kb_id=20, extraction_status="extracted")
        conn.execute("UPDATE documents SET detail='== Ready\n\nBody text.', detail_html='<p>Body text.</p>' WHERE id='doc-ready'")
        conn.execute("INSERT INTO extractions (kb_id, document_id, provider, model, prompt_version, raw_response, created_at) VALUES (20, 'doc-ready', 'mock', 'mock', 'v1', '{\"summary\":\"Ready\"}', 1.0)")
        dbm.upsert_entity(conn, Entity(id="ent-pending-only", type="Concept", name="SecretPendingConcept", sources=["doc-pending-placeholder"]), kb_id=20)
        conn.commit()
    finally:
        conn.close()

    with TestClient(app, base_url=PUBLIC_URL) as client:
        # 1. Alice opts into client extraction mode via /api/me.
        patch_res = client.patch("/api/me", json={"extraction_mode": "client"}, headers=env.alice_headers)
        assert patch_res.status_code == 200
        assert patch_res.json()["extraction_mode"] == "client"

        # 2. Stage a raw document via stage_raw (simulating client-mode ingest).
        conn = dbm.connect_existing(env.db_file)
        try:
            staged_rep = stage_raw(
                "https://secret-staging.example/draft",
                conn=conn,
                fetch_fn=lambda u, full_content=False: Document(id="doc-staged", url=u, canonical_url=u, title="Staged Draft", raw_text="Secret pending raw text", source_type="web", content_hash="h_staged"),
                data_dir=env.data_dir,
                kb_id=20,
                settings=env.settings,
                account_id=2,
            )
            assert staged_rep.staged is True
            pending_id = staged_rep.document_id
            # Attach entity pointing to pending_id and create a pre-existing share token to verify /p?s= still blocks pending docs.
            dbm.upsert_entity(conn, Entity(id="ent-pending-only", type="Concept", name="SecretPendingConcept", sources=[pending_id]), kb_id=20)
            share_tok = dbm.create_doc_share(conn, pending_id, kb_id=20)
            # Claim & renew by Alice's client while pending.
            claimed = dbm.claim_staged_document(conn, pending_id, account_id=2, client_id="alice-agent", lease_seconds=60.0)
            assert claimed is not None
            assert dbm.renew_staged_document_claim(conn, pending_id, claimed["claim_token"], account_id=2, lease_seconds=120.0) is not None
            assert dbm.claim_staged_document(conn, pending_id, account_id=3, client_id="bob-agent") is None
        finally:
            conn.close()

        # 3. Owner sees pending doc in /@alice/documents, /@alice/document, and list_staged_documents.
        alice_list = client.get("/@alice/documents", headers=env.alice_headers)
        assert alice_list.status_code == 200
        assert {d["id"] for d in alice_list.json()["documents"]} == {"doc-ready", pending_id}
        assert client.get("/@alice/document", params={"id": pending_id}, headers=env.alice_headers).status_code == 200

        # 4. Cross-account (Bob) and anonymous visitors CANNOT see the pending doc anywhere.
        for reader_headers in (env.bob_headers, env.anonymous_headers):
            docs_resp = client.get("/@alice/documents", headers=reader_headers)
            assert docs_resp.status_code == 200
            assert [d["id"] for d in docs_resp.json()["documents"]] == ["doc-ready"]
            assert client.get("/@alice/document", params={"id": pending_id}, headers=reader_headers).status_code == 404
            graph_resp = client.get("/@alice/graph", headers=reader_headers)
            assert graph_resp.status_code == 200
            assert all(n["id"] != "ent-pending-only" for n in graph_resp.json()["nodes"])
            assert client.get("/@alice/node", params={"id": "ent-pending-only"}, headers=reader_headers).status_code == 404

        assert client.get("/p", params={"s": share_tok}).status_code == 404
        sitemap_resp = client.get("/sitemap.xml")
        assert sitemap_resp.status_code == 200
        assert pending_id not in sitemap_resp.text
        assert share_tok not in sitemap_resp.text

        # 5. ActivityPub publish returns 409 document_not_extracted (not document_has_no_body).
        alice_kb_number = str((1 << 56) | 1)
        pub_res = client.post("/api/federation/publish", json={"kb": alice_kb_number, "document_id": pending_id}, headers=env.alice_headers)
        assert pub_res.status_code == 409
        assert pub_res.json() == {"error": "document_not_extracted"}

        # 6. Shared KB contribute_direct and curate reject pending document with ContributionError(409, "document_not_extracted").
        conn2 = dbm.connect_existing(env.db_file)
        try:
            assert dbm.pending_document_ids(conn2, kb_id=20) == {pending_id}
            assert queries.theme_summary(conn2, include_hidden=False, kb_id=20)["documents"] == 1
            assert queries.theme_summary(conn2, include_hidden=True, kb_id=20)["documents"] == 2
            with pytest.raises(ContributionError) as exc_contrib:
                contribute_direct(conn2, kb_id=40, source_kb_id=20, source_document_id=pending_id, contributor_account_id=2, approved=True, origin="direct")
            assert (exc_contrib.value.status, exc_contrib.value.code) == (409, "document_not_extracted")

            with pytest.raises(ContributionError) as exc_curate:
                curate(conn2, kb_id=40, source={"id": 20, "is_personal": True, "account_id": 2}, document_id=pending_id, curator_account_id=1)
            assert (exc_curate.value.status, exc_curate.value.code) == (409, "document_not_extracted")
        finally:
            conn2.close()

        # 7. Once force_provider=True completes extraction, document becomes visible and publishable.
        conn3 = dbm.connect_existing(env.db_file)
        try:
            vstore3 = make_vector_store(conn3, "brute", kb_id=20)
            done_rep = pipeline.ingest(
                "https://secret-staging.example/draft",
                conn=conn3,
                provider=MockProvider(),
                vstore=vstore3,
                fetch_fn=lambda u, full_content=False: Document(id="doc-ignored", url=u, canonical_url=u, title="Staged Draft", raw_text="Secret pending raw text", source_type="web", content_hash="h_staged"),
                data_dir=env.data_dir,
                kb_id=20,
                settings=env.settings,
                account_id=2,
                force_provider=True,
            )
            assert done_rep.error is None
            assert done_rep.document_id == pending_id
            assert done_rep.extraction_status == "extracted"
        finally:
            conn3.close()

        assert client.get("/@alice/document", params={"id": pending_id}, headers=env.bob_headers).status_code == 200
        assert client.get("/p", params={"s": share_tok}).status_code == 200
        pub_ok = client.post("/api/federation/publish", json={"kb": alice_kb_number, "document_id": pending_id}, headers=env.alice_headers)
        assert pub_ok.status_code == 202

        conn4 = dbm.connect_existing(env.db_file)
        try:
            contrib_row = contribute_direct(conn4, kb_id=40, source_kb_id=20, source_document_id=pending_id, contributor_account_id=2, approved=True, origin="direct")
            assert contrib_row["document_id"]
            # Re-staging an already extracted document on updated canonical_url flips it back to pending and re-isolates it even if prior extractions exist.
            re_staged = stage_raw(
                "https://secret-staging.example/draft",
                conn=conn4,
                fetch_fn=lambda u, full_content=False: Document(url=u, canonical_url=u, title="Staged Draft v2", raw_text="Updated raw text v2", source_type="web", content_hash="h_staged_v2"),
                data_dir=env.data_dir,
                kb_id=20,
                settings=env.settings,
                account_id=2,
            )
            assert re_staged.document_id == pending_id
            assert re_staged.staged is True
            assert re_staged.updated is True
            assert dbm.get_document_row(conn4, pending_id, kb_id=20)["extraction_status"] == "pending"
            with pytest.raises(ContributionError) as exc_re:
                _add_kb(conn4, env.db_file, kb_id=41, seq=4, label="Shared KB 2", account_id=None, personal=False)
                contribute_direct(conn4, kb_id=41, source_kb_id=20, source_document_id=pending_id, contributor_account_id=2, approved=True, origin="direct")
            assert (exc_re.value.status, exc_re.value.code) == (409, "document_not_extracted")
        finally:
            conn4.close()
        assert client.get("/@alice/document", params={"id": pending_id}, headers=env.bob_headers).status_code == 404
        assert client.post("/api/federation/publish", json={"kb": alice_kb_number, "document_id": pending_id}, headers=env.alice_headers).status_code == 409


def test_auto_contribution_excludes_restaged_pending_documents(tmp_path: Path) -> None:
    """Verify auto_contribution._over_threshold excludes documents with extraction_status='pending' even when an earlier extraction row exists."""
    import json
    from claire.collaboration import auto_contribution
    from claire.ontology.base import normalize_name

    env = _setup_network_env(tmp_path, alice_mode="client")
    conn = dbm.connect_existing(env.db_file)
    try:
        doc = Document(id="doc-auto-1", url="https://auto.example/item", canonical_url="https://auto.example/item", title="Auto Candidate", raw_text="Korea Seoul Busan semiconductor policy overview.", source_type="web", content_hash="h_auto_1")
        dbm.insert_document(conn, doc, kb_id=20, extraction_status="extracted")
        extraction_json = json.dumps({"summary": "Auto candidate", "key_claims": [], "entities": [{"name": "Korea", "type": "Concept", "aliases": [], "observations": ["Korea study"]}, {"name": "Seoul", "type": "Concept", "aliases": [], "observations": ["Seoul note"]}], "relations": []})
        conn.execute("INSERT INTO extractions (kb_id, document_id, provider, model, prompt_version, raw_response, created_at) VALUES (20, 'doc-auto-1', 'mock', 'mock', 'v1', ?, 1.0)", (extraction_json,))
        for idx, name in enumerate(("Korea", "Seoul")):
            dbm.upsert_entity(conn, Entity(id=f"ent-alice-{idx}", type="Concept", name=name, sources=["doc-auto-1"]), kb_id=20)
            assert normalize_name(name)
        conn.execute("UPDATE knowledge_bases SET seed_entities=? WHERE id=40", (json.dumps(["Korea", "Seoul"]),))
        conn.commit()

        matched = auto_contribution.candidates(conn, account_id=2, personal_kb_id=20, kb_id=40, include_uploads=False)
        assert [c["document"] for c in matched] == ["doc-auto-1"]

        dbm.mark_document_extraction_status(conn, "doc-auto-1", "pending", kb_id=20)
        assert auto_contribution.candidates(conn, account_id=2, personal_kb_id=20, kb_id=40, include_uploads=False) == []
    finally:
        conn.close()


def test_pipeline_near_duplicate_syncs_pending_staged_state(tmp_path: Path) -> None:
    """Verify Dedup 3 near-duplicate match syncs extraction_status and staged state when the matched document is pending."""
    env = _setup_network_env(tmp_path, alice_mode="client")
    base_words = [f"token_{i:03d}" for i in range(180)]
    text_v1 = " ".join(base_words)
    text_v2 = " ".join(base_words[:-2] + ["variant_alpha", "variant_beta"])
    spy = _SpyProvider()
    conn = dbm.connect_existing(env.db_file)
    try:
        vstore = make_vector_store(conn, "brute", kb_id=20)
        staged = stage_raw(
            "https://near.example/v1",
            conn=conn,
            fetch_fn=lambda u, full_content=False: Document(url=u, canonical_url=u, title="Near Staged", raw_text=text_v1, source_type="web", content_hash=hashlib.sha256(text_v1.encode()).hexdigest()),
            data_dir=env.data_dir,
            kb_id=20,
            settings=env.settings,
            account_id=2,
        )
        assert staged.staged is True

        near_rep = pipeline.ingest(
            "https://near.example/v2",
            conn=conn,
            provider=spy,
            vstore=vstore,
            fetch_fn=lambda u, full_content=False: Document(url=u, canonical_url=u, title="Near Variant", raw_text=text_v2, source_type="web", content_hash=hashlib.sha256(text_v2.encode()).hexdigest()),
            data_dir=env.data_dir,
            kb_id=20,
            settings=env.settings,
            account_id=2,
        )
        assert near_rep.error is None
        assert near_rep.duplicate is True
        assert near_rep.document_id == staged.document_id
        assert near_rep.extraction_status == "pending"
        assert near_rep.staged is True
        assert near_rep.message == STAGED_INGEST_MESSAGE
        assert spy.calls == []
    finally:
        conn.close()
