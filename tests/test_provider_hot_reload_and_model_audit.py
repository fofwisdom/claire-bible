"""Tests for cross-process provider hot-reload, API live settings, and model audit/remediation."""

from __future__ import annotations

import json
import os
import tarfile
import time
from compression import zstd
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from claire import cli
from claire.accounts.provider_policy import ProviderContext
from claire.api.server import create_app
from claire.core.config import Settings, get_settings
from claire.core.provider_manager import ProviderManager
from claire.extract.provider import ExtractionResult
from claire.ingest.normalize import content_hash
from claire.ingest.service import IngestService, IngestServicePool, context_cache_key
from claire.ontology.base import Document
from claire.store import auth_db, db as dbm
from claire.store.knowledge_base import KnowledgeBaseInfo, KnowledgeBaseManager
from claire.store.provider_keys import ActiveKey
from tests.auth_helpers import bearer_headers, ensure_test_account

ADMIN_KEY = "AIzaADMINadminADMINadminADMINadmin0001"
BYOK_KEY = "AIzaBYOKbyokBYOKbyokBYOKbyokBYOK00002"


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch, tmp_path):
    for name in list(os.environ):
        if name.startswith(("CLAIRE_", "GEMINI_", "STT_")) or name in ("CB_DATA_DIR",):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class _FakeGeminiInteractions:
    """Fake google.genai Client that records every model and thinking_level passed to interactions.create."""

    calls: list[dict[str, object]] = []

    def __init__(self, api_key=None, **_kwargs):
        self.api_key = api_key
        self.interactions = SimpleNamespace(create=self._create_interaction)
        self.models = SimpleNamespace(embed_content=self._embed_content)
        self.files = SimpleNamespace()

    def _create_interaction(self, **kwargs):
        _FakeGeminiInteractions.calls.append({"api_key": self.api_key, "model": kwargs.get("model"), "generation_config": dict(kwargs.get("generation_config") or {})})
        if "response_format" in kwargs:
            payload = json.dumps({
                "summary": "테스트 문서 요약이다.",
                "key_claims": ["핵심 주장이다."],
                "entities": [{"name": "A2UI", "type": "Framework", "aliases": [], "observations": ["UI 프레임워크이다."]}],
                "relations": [],
            }, ensure_ascii=False)
            return SimpleNamespace(outputs=[SimpleNamespace(text=payload)], usage=SimpleNamespace(total_input_tokens=10, total_output_tokens=20))
        input_text = str(kwargs.get("input") or "")
        if "watch" in input_text.lower() or "interval_days" in input_text.lower():
            return SimpleNamespace(outputs=[SimpleNamespace(text='{"watch": false, "reason": "static"}')], usage=SimpleNamespace(total_input_tokens=5, total_output_tokens=5))
        return SimpleNamespace(outputs=[SimpleNamespace(text="= Test Detail\n\n테스트 본문 내용이다.")], usage=SimpleNamespace(total_input_tokens=10, total_output_tokens=20))

    def _embed_content(self, **kwargs):
        contents = kwargs.get("contents")
        count = len(contents) if isinstance(contents, list) else 1
        return SimpleNamespace(embeddings=[SimpleNamespace(values=[0.1, 0.2, 0.3, 0.4]) for _ in range(count)])


def _setup_world(tmp_path: Path) -> tuple[Path, Path, Settings]:
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)
    db_file = data_dir / "claire.db"
    ensure_test_account(db_file, account_id=dbm.ADMIN_ACCOUNT_ID, username="admin", role="admin", status="active")
    conn = dbm.connect(db_file)
    dbm.init_db(conn)
    now = time.time()
    conn.execute(
        "INSERT OR IGNORE INTO knowledge_bases (id, seq, label, description, icon, db_path, is_personal, is_public, created_at, updated_at, account_id, number) VALUES (?, ?, ?, '', '📁', ?, ?, 0, ?, ?, ?, ?)",
        (1, 1, "보조 지식베이스", str(db_file), 0, now, now, dbm.ADMIN_ACCOUNT_ID, (1 << 56) + 1),
    )
    conn.commit()
    conn.close()
    auth_db.connect_auth_db(auth_db.auth_db_path(data_dir)).close()
    pm = ProviderManager(data_dir=data_dir, auto_migrate=False)
    pm.update_config({"active_provider": "gemini", "providers": {"gemini": {"api_key": ADMIN_KEY, "model": "gemini-4-argon-medium", "effort": "medium"}}})
    settings = Settings(
        _env_file=None,
        CLAIRE_DB_PATH=str(db_file),
        CLAIRE_ENVIRONMENT="development",
        CLAIRE_FQDN="127.0.0.1:8765",
        provider="gemini",
        gemini_api_key=ADMIN_KEY,
        gemini_model="gemini-4-argon-medium",
        gemini_effort="medium",
        gemini_min_interval=0.0,
        gemini_max_retries=0,
        auto_expand=False,
    )
    return data_dir, db_file, settings


def test_context_cache_key_tracks_feature_models_and_languages():
    ctx_a = ProviderContext(account_id=1, is_admin=False, source="byok", provider="gemini", model="gemini-4-argon-medium", embed_model="gemini-embedding-2", effort="medium", credential_key="byok:fp1", feature_models={"render_detail": {"provider": "gemini", "model": "gemini-4-argon-medium", "effort": "medium"}}, languages=("ko", "en"))
    ctx_b = ProviderContext(account_id=1, is_admin=False, source="byok", provider="gemini", model="gemini-4-argon-medium", embed_model="gemini-embedding-2", effort="medium", credential_key="byok:fp1", feature_models={"render_detail": {"provider": "gemini", "model": "gemini-4-argon-high", "effort": "high"}}, languages=("ko", "en"))
    assert context_cache_key(ctx_a) != context_cache_key(ctx_b)


def test_ingest_service_pool_hot_reloads_providers_for_admin_and_byok(tmp_path: Path, monkeypatch):
    import google.genai as genai

    _FakeGeminiInteractions.calls = []
    monkeypatch.setattr(genai, "Client", _FakeGeminiInteractions)

    data_dir, db_file, settings = _setup_world(tmp_path)
    from claire.store import provider_keys

    monkeypatch.setattr(provider_keys, "load_active_key", lambda conn, *, account_id, purpose, secret_service=None: ActiveKey(provider="gemini", fingerprint="fp-admin", api_key=BYOK_KEY))
    monkeypatch.setattr(provider_keys, "has_key", lambda conn, *, account_id: True)

    pool = IngestServicePool(settings, theme_manager=KnowledgeBaseManager(settings), account_id=dbm.ADMIN_ACCOUNT_ID)
    svc_before = pool.get_service(0)
    assert svc_before.s.gemini_model == "gemini-4-argon-medium"
    assert svc_before.provider.model == "gemini-4-argon-medium"

    pm = ProviderManager(data_dir=data_dir, auto_migrate=False)
    pm.update_config({"providers": {"gemini": {"model": "gemini-4-argon-high", "effort": "high"}}})

    svc_after = pool.get_service(0)
    assert svc_after.s.gemini_model == "gemini-4-argon-high"
    assert svc_after.provider.model == "gemini-4-argon-high"
    assert svc_after is not svc_before


def test_worker_loop_runtime_config_reload_and_fingerprint(tmp_path: Path):
    data_dir, _db_file, settings = _setup_world(tmp_path)
    refreshed_1 = cli._with_current_runtime_config(settings)
    fp_1 = cli._providers_fingerprint(refreshed_1)
    assert refreshed_1.gemini_model == "gemini-4-argon-medium"

    pm = ProviderManager(data_dir=data_dir, auto_migrate=False)
    pm.update_config({"providers": {"gemini": {"model": "gemini-4-argon-high", "effort": "high"}}})

    refreshed_2 = cli._with_current_runtime_config(refreshed_1)
    fp_2 = cli._providers_fingerprint(refreshed_2)
    assert refreshed_2.gemini_model == "gemini-4-argon-high"
    assert refreshed_2.gemini_effort == "high"
    assert fp_2 != fp_1


def test_api_patch_providers_updates_support_bundle_config_sanitized(tmp_path: Path, monkeypatch):
    import claire.core.provider_manager as pm_mod

    monkeypatch.setattr(pm_mod, "discover_gemini_models", lambda api_key, force_refresh=False: {"source": "fallback", "models": [{"id": "gemini-4-argon-medium"}, {"id": "gemini-4-argon-high"}], "embed_models": [{"id": "gemini-embedding-2"}], "base_models": ["gemini-4-argon"]})

    data_dir, db_file, settings = _setup_world(tmp_path)
    owner_headers = bearer_headers(db_file, scope="owner", account_id=dbm.ADMIN_ACCOUNT_ID)
    app = create_app(settings)
    client = TestClient(app, base_url=settings.public_url)

    patch_res = client.patch("/providers", json={"providers": {"gemini": {"model": "gemini-4-argon-high", "effort": "high"}}}, headers=owner_headers)
    assert patch_res.status_code == 200
    assert patch_res.json()["config"]["providers"]["gemini"]["model"] == "gemini-4-argon-high"

    bundle_res = client.post("/api/v1/support/bundles", json={"scope": "server", "days": 1}, headers=owner_headers)
    assert bundle_res.status_code == 201
    link = bundle_res.json()["bundle"]["link"]
    token = link.rsplit("/", 1)[-1]
    dl_res = client.get(f"/support/bundle/{token}")
    assert dl_res.status_code == 200

    raw_tar = zstd.decompress(dl_res.content)
    with tarfile.open(fileobj=BytesIO(raw_tar), mode="r:") as tf:
        cfg_member = next(m for m in tf.getmembers() if m.name.endswith("/diagnostics/config_sanitized.json"))
        extracted = tf.extractfile(cfg_member)
        assert extracted is not None
        cfg_data = json.load(extracted)
    assert cfg_data["gemini_model"] == "gemini-4-argon-high"
    assert cfg_data["gemini_effort"] == "high"


def test_audit_document_models_filters_and_detects_lineage_mismatch(tmp_path: Path):
    _data_dir, db_file, _settings = _setup_world(tmp_path)
    conn = dbm.connect(db_file)
    try:
        docs = [
            ("doc_old_med", 0, 1000.0, "gemini-4-hydrogen-medium", ["gemini-4-hydrogen-medium"]),
            ("doc_new_med", 0, 2000.0, "gemini-4-hydrogen-medium", ["gemini-4-hydrogen-medium"]),
            ("doc_new_high", 0, 2100.0, "gemini-4-hydrogen-high", ["gemini-4-hydrogen-high"]),
            ("doc_mixed", 0, 2200.0, "gemini-4-hydrogen-medium", ["gemini-4-hydrogen-medium", "gemini-4-hydrogen-high"]),
            ("doc_kb1_med", 1, 2300.0, "gemini-4-hydrogen-medium", ["gemini-4-hydrogen-medium"]),
        ]
        for did, kb_id, ts, ext_model, lineage_models in docs:
            doc = Document(
                id=did,
                title=f"Title {did}",
                raw_text="Body text sufficient for audit testing in database.",
                canonical_url=f"https://example.com/{did}",
                source_type="web",
                content_hash=content_hash(did),
                fetched_at=ts,
                meta={"applied_effort": ext_model.rsplit("-", 1)[-1], "model_lineage": {"stages": [{"stage": "extract_graph", "final_model": lineage_models[0], "status": "ok"}, {"stage": "render_detail", "final_model": lineage_models[-1], "status": "ok"}], "final_output_models": {"extract_graph": {"model": lineage_models[0], "status": "ok"}, "render_detail": {"model": lineage_models[-1], "status": "ok"}}}},
            )
            dbm.insert_document(conn, doc, kb_id=kb_id)
            conn.execute("INSERT INTO extractions(kb_id, document_id, provider, model, prompt_version, raw_response, created_at) VALUES (?, ?, 'gemini', ?, 'extract-v8', '{\"summary\":\"요약\"}', ?)", (kb_id, did, ext_model, ts))
            conn.execute("INSERT INTO raw_inbox(kb_id, received_at, source, kind, payload, document_id, status) VALUES (?, ?, 'telegram', 'url', ?, ?, 'done')", (kb_id, ts, doc.canonical_url, did))
        conn.commit()

        audit_kb0 = dbm.audit_document_models(conn, kb_id=0)
        assert audit_kb0["total_documents"] == 4
        assert audit_kb0["mismatch_count"] == 1
        assert audit_kb0["model_counts"] == {"gemini-4-hydrogen-medium": 3, "gemini-4-hydrogen-high": 1}

        med_since = dbm.audit_document_models(conn, kb_id=0, model="gemini-4-hydrogen-medium", since=1500.0)
        assert [d["document_id"] for d in med_since["documents"]] == ["doc_mixed", "doc_new_med"]

        excl_high = dbm.audit_document_models(conn, kb_id=0, exclude_model="gemini-4-hydrogen-high")
        assert {d["document_id"] for d in excl_high["documents"]} == {"doc_old_med", "doc_new_med", "doc_mixed"}

        audit_all = dbm.audit_document_models(conn, kb_id=None, model="gemini-4-hydrogen-medium", since=1500.0)
        assert {d["document_id"] for d in audit_all["documents"]} == {"doc_kb1_med", "doc_mixed", "doc_new_med"}
    finally:
        conn.close()


def test_e2e_hot_reload_and_selective_cli_remediation(tmp_path: Path, monkeypatch, capsys):
    """E2E: ingest under medium, switch providers.json to high without restart, verify new ingest uses high, then remediate post-cutoff medium docs via CLI."""
    import google.genai as genai

    _FakeGeminiInteractions.calls = []
    monkeypatch.setattr(genai, "Client", _FakeGeminiInteractions)

    data_dir, db_file, settings = _setup_world(tmp_path)
    monkeypatch.setenv("CLAIRE_DB_PATH", str(db_file))
    monkeypatch.setenv("CLAIRE_DATA_DIR", str(data_dir))
    monkeypatch.setenv("CLAIRE_DATA_LIFECYCLE", "purgeable")
    monkeypatch.setenv("CLAIRE_ALLOW_PURGE", "1")
    get_settings.cache_clear()

    pool = IngestServicePool(settings, theme_manager=KnowledgeBaseManager(settings), account_id=dbm.ADMIN_ACCOUNT_ID)

    # 1. Ingest doc_before and doc_affected while providers.json is gemini-4-argon-medium.
    svc1 = pool.get_service(0)
    doc_a = Document(url="https://example.com/a", canonical_url="https://example.com/a", title="Doc A", raw_text="A2UI architecture overview and components. " * 4, source_type="web", content_hash=content_hash("doc-a"))
    rep_a = svc1.ingest("https://example.com/a", source="telegram", prefetched=doc_a)
    assert rep_a.document_id is not None

    cutoff_ts = time.time() + 0.01
    time.sleep(0.02)

    doc_b = Document(url="https://example.com/b", canonical_url="https://example.com/b", title="Doc B", raw_text="A2UI renderer lifecycle and actions. " * 4, source_type="web", content_hash=content_hash("doc-b"))
    rep_b = svc1.ingest("https://example.com/b", source="telegram", prefetched=doc_b)
    assert rep_b.document_id is not None

    # 2. Update providers.json to gemini-4-argon-high WITHOUT restarting or calling pool.clear().
    pm = ProviderManager(data_dir=data_dir, auto_migrate=False)
    pm.update_config({"providers": {"gemini": {"model": "gemini-4-argon-high", "effort": "high"}}})

    # 3. Existing pool must immediately use gemini-4-argon-high for new ingest (doc_c).
    svc2 = pool.get_service(0)
    doc_c = Document(url="https://example.com/c", canonical_url="https://example.com/c", title="Doc C", raw_text="A2UI catalog generation and validation. " * 4, source_type="web", content_hash=content_hash("doc-c"))
    rep_c = svc2.ingest("https://example.com/c", source="telegram", prefetched=doc_c)
    assert rep_c.document_id is not None

    conn = dbm.connect_existing(db_file, readonly=True)
    try:
        assert dbm.latest_extraction_model(conn, rep_a.document_id, kb_id=0) == ("gemini", "gemini-4-argon-medium")
        assert dbm.latest_extraction_model(conn, rep_b.document_id, kb_id=0) == ("gemini", "gemini-4-argon-medium")
        assert dbm.latest_extraction_model(conn, rep_c.document_id, kb_id=0) == ("gemini", "gemini-4-argon-high")
    finally:
        conn.close()

    # 4. Dry-run CLI audit targets only doc_b (medium and >= cutoff_ts).
    capsys.readouterr()
    rc_dry = cli.main(["regenerate", "--all-themes", "--model", "gemini-4-argon-medium", "--since", str(cutoff_ts), "--all", "--json"])
    assert rc_dry == 0
    raw_dry = json.loads(capsys.readouterr().out)
    dry_payload = raw_dry.get("data", raw_dry)
    assert dry_payload["dry_run"] is True
    assert dry_payload["count"] == 1
    assert dry_payload["targets"][0]["document_id"] == rep_b.document_id
    assert dry_payload["model_counts"] == {"gemini-4-argon-medium": 2, "gemini-4-argon-high": 1}

    # 5. Apply remediation via CLI; doc_b becomes high, doc_a (before cutoff) stays medium.
    rc_apply = cli.main(["regenerate", "--all-themes", "--model", "gemini-4-argon-medium", "--since", str(cutoff_ts), "--all", "--apply", "--yes", "--json"])
    assert rc_apply == 0
    raw_apply = json.loads(capsys.readouterr().out)
    apply_payload = raw_apply.get("data", raw_apply)
    assert apply_payload["dry_run"] is False
    assert apply_payload["count"] == 1
    assert apply_payload["targets"][0]["document_id"] == rep_b.document_id

    conn = dbm.connect_existing(db_file, readonly=True)
    try:
        assert dbm.latest_extraction_model(conn, rep_a.document_id, kb_id=0) == ("gemini", "gemini-4-argon-medium")
        assert dbm.latest_extraction_model(conn, rep_b.document_id, kb_id=0) == ("gemini", "gemini-4-argon-high")
        assert dbm.latest_extraction_model(conn, rep_c.document_id, kb_id=0) == ("gemini", "gemini-4-argon-high")
        doc_b_row = dbm.get_document(conn, rep_b.document_id, kb_id=0)
        assert doc_b_row is not None and isinstance(doc_b_row.meta, dict)
        assert doc_b_row.meta["model_lineage"]["final_output_models"]["extract_graph"]["model"] == "gemini-4-argon-high"
        assert doc_b_row.meta["model_lineage"]["final_output_models"]["render_detail"]["model"] == "gemini-4-argon-high"
    finally:
        conn.close()
