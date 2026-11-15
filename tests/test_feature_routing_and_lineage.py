"""Tests for per-feature provider routing, BYOK split-key encryption, JEV filter/hybrid gating, and model lineage persistence."""

from __future__ import annotations

import concurrent.futures
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from claire.accounts.provider_policy import MODEL_CAPABILITY_RANKS, clamp_byok_model_for_feature, is_model_equal_or_higher
from claire.config import Settings
from claire.extract.jev import JevShadow
from claire.extract.provider import ExtractedEntity, ExtractedRelation, ExtractionResult, FeatureRoutedProvider, MockProvider, RelationJudgement, get_provider
from claire.extract.resolver import resolve_or_create
from claire.ingest.pipeline import IngestReport, extract_resolve_store
from claire.ontology.base import Document, Entity
from claire.provider_manager import ProviderManager
from claire.store import auth_db, provider_keys
from claire.store import db as dbm
from claire.store.secrets import SecretDecryptionError, SecretService, clear_volatile_user_seals, generate_user_seal, reset_secret_service
from claire.store.vectors import VectorStore


class _InlineExecutor(concurrent.futures.Executor):
    """Run submitted JEV tasks synchronously for deterministic tests."""

    def submit(self, fn, /, *args, **kwargs):
        fut: concurrent.futures.Future = concurrent.futures.Future()
        try:
            fut.set_result(fn(*args, **kwargs))
        except BaseException as exc:  # noqa: BLE001
            fut.set_exception(exc)
        return fut


class _FakeJevClient:
    """Test double that returns predefined JEV noul scores per candidate name."""

    def __init__(self, noul_by_name: dict[str, float]) -> None:
        self.model = "jev-1.14.0"
        self.timeout = 1.0
        self.noul_by_name = noul_by_name
        self.requests: list[tuple[dict[str, Any], dict[str, Any]]] = []

    def systemone(self, state: dict[str, Any], questions: dict[str, Any]):
        self.requests.append((json.loads(json.dumps(state)), questions))
        answers = {key: {"type": "noul", "noul": self.noul_by_name.get(state["candidates"][key]["name"], 0.1)} for key in questions}
        return {"model": "jev-1.14.0", "answers": answers, "usage": {"input_tokens": 120, "output_tokens": 3}}, 18.5


def _memory_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    dbm.init_db(conn)
    return conn


def test_model_capability_ranks_and_byok_baseline_clamping() -> None:
    """Verify baseline rank checks reject weaker models and clamp to admin minimums."""
    assert MODEL_CAPABILITY_RANKS["mock"] < MODEL_CAPABILITY_RANKS["gemini-3.5-flash-lite"]
    assert is_model_equal_or_higher("gemini-4-hydrogen", "high", "gemini-3.8-flash", "medium") is True
    assert is_model_equal_or_higher("gemini-3.8-flash", "medium", "gemini-3.8-flash", "medium") is True
    assert is_model_equal_or_higher("gemini-3.5-flash-lite", "high", "gemini-3.8-flash", "medium") is False
    assert is_model_equal_or_higher("gemini-3.8-flash", "low", "gemini-3.8-flash", "high") is False

    clamped_model, clamped_effort = clamp_byok_model_for_feature("gemini-2.5-flash-lite", "low", "gemini-3.8-flash", "medium")
    assert clamped_model == "gemini-3.8-flash"
    assert clamped_effort == "medium"


def test_split_key_envelope_encryption_requires_volatile_seal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify split-key encrypted credentials fail decryption without the volatile user seal."""
    monkeypatch.setenv("CLAIRE_SECRET_KEY", "55" * 32)
    reset_secret_service()
    try:
        db_path = auth_db.auth_db_path(tmp_path)
        conn = auth_db.connect_auth_db(db_path)
        try:
            secret_svc = SecretService(master_key=bytes.fromhex("55" * 32))
            seal = generate_user_seal()
            record = provider_keys.put_key(conn, account_id=7, provider="gemini", api_key="AIzaSplitKeySecretTestValue012345", status="active", user_seal=seal, model_prefs={"model": "gemini-4-argon", "effort": "high"}, secret_service=secret_svc)
            assert record.fingerprint
            assert len(seal) >= 16

            # Decrypt succeeds with the active volatile user seal or explicit cookie seal.
            loaded = provider_keys.load_active_key(conn, account_id=7, secret_service=secret_svc)
            assert loaded is not None and loaded.api_key == "AIzaSplitKeySecretTestValue012345"

            # Admin or maintainer with only DB and master.key cannot decrypt after seals clear.
            clear_volatile_user_seals()
            with pytest.raises((provider_keys.ProviderKeyError, SecretDecryptionError)):
                provider_keys.load_active_key(conn, account_id=7, secret_service=secret_svc)

            # Providing the volatile user cookie seal restores decryption.
            restored = provider_keys.load_active_key(conn, account_id=7, secret_service=secret_svc, user_seal=seal)
            assert restored is not None and restored.api_key == "AIzaSplitKeySecretTestValue012345"
        finally:
            conn.close()
    finally:
        reset_secret_service()


def test_jev_filter_and_hybrid_modes_control_llm_judge_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify JEV filter skips low-score LLM judge calls and hybrid auto-merges high-score matches."""
    import claire.extract.resolver as resolver_mod

    monkeypatch.setattr(resolver_mod, "_get_tier_thresholds", lambda: (0.93, 0.72, 0.70, 0.55, False))
    conn = _memory_db()
    vstore = VectorStore(conn, "brute")
    e_alpha = Entity(type="Tool", name="AlphaCore", observations=["engine"], sources=["d0"])
    e_beta = Entity(type="Tool", name="BetaTool", observations=["cli"], sources=["d0"])
    dbm.upsert_entity(conn, e_alpha)
    dbm.upsert_entity(conn, e_beta)
    vstore.put(e_alpha.id, [0.82, 0.57, 0.0], "claire")
    vstore.put(e_beta.id, [0.80, 0.60, 0.0], "claire")

    # 1. filter mode: JEV score < 0.20 skips LLM judge_fn cleanly.
    judge_calls: list[str] = []
    client_low = _FakeJevClient({"AlphaCore": 0.12, "BetaTool": 0.15})
    shadow_low = JevShadow(client_low, executor=_InlineExecutor())
    res_filtered = resolve_or_create(
        conn, vstore, name="GammaModule", etype="Tool", aliases=[], observations=["unrelated"], document_id="d1",
        embed_fn=lambda: [1.0, 0.0, 0.0],
        judge_fn=lambda nm, et, obs, cand: judge_calls.append(cand.name) or False,
        shadow=shadow_low, jev_mode="filter", jev_filter_threshold=0.20, jev_auto_merge_threshold=0.90,
    )
    assert res_filtered.created is True
    assert judge_calls == []
    assert [c["outcome"] for c in res_filtered.decision.candidates] == ["jev_filtered", "jev_filtered"]

    # 2. hybrid mode: JEV score >= 0.90 auto-merges without calling LLM judge_fn.
    conn2 = _memory_db()
    vstore2 = VectorStore(conn2, "brute")
    e_alpha2 = Entity(type="Tool", name="AlphaCore", observations=["engine"], sources=["d0"])
    e_beta2 = Entity(type="Tool", name="BetaTool", observations=["cli"], sources=["d0"])
    dbm.upsert_entity(conn2, e_alpha2)
    dbm.upsert_entity(conn2, e_beta2)
    vstore2.put(e_alpha2.id, [0.82, 0.57, 0.0], "claire")
    vstore2.put(e_beta2.id, [0.80, 0.60, 0.0], "claire")
    client_high = _FakeJevClient({"AlphaCore": 0.94, "BetaTool": 0.10})
    shadow_high = JevShadow(client_high, executor=_InlineExecutor())
    res_hybrid = resolve_or_create(
        conn2, vstore2, name="Alpha Engine", etype="Tool", aliases=[], observations=["updated"], document_id="d2",
        embed_fn=lambda: [1.0, 0.0, 0.0],
        judge_fn=lambda nm, et, obs, cand: judge_calls.append(cand.name) or False,
        shadow=shadow_high, jev_mode="hybrid", jev_filter_threshold=0.20, jev_auto_merge_threshold=0.90,
    )
    assert res_hybrid.created is False
    assert res_hybrid.entity.id == e_alpha2.id
    assert res_hybrid.decision.stage == "jev_hybrid_auto_merge"
    assert judge_calls == []


def test_feature_routed_provider_decouples_extract_and_summarize_and_records_lineage(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify FeatureRoutedProvider dispatches stages separately and persists model_lineage."""
    calls: list[tuple[str, str]] = []

    class _FakeStageProvider:
        def __init__(self, slot_name: str, model_name: str) -> None:
            self.name = slot_name
            self.model = model_name
            self.effort = "medium"
            self.embed_model = model_name

        def has_embed_capability(self) -> bool:
            return True

        def extract(self, doc: Document, ontology_block: str = "", *, effort: str | None = None) -> ExtractionResult:
            calls.append(("extract", self.model))
            return ExtractionResult(
                summary="",
                key_claims=["claim"],
                entities=[ExtractedEntity(name="Claire Engine", type="Tool", observations=["knowledge graph engine"])],
                relations=[],
                model=self.model,
            )

        def summarize_document(self, doc: Document, *, effort: str | None = None) -> str:
            calls.append(("summarize_document", self.model))
            return "요약 모델이 생성한 독립 요약이다."

        def render_detail(self, doc: Document, format: str = "adoc", focus: str | None = None, *, effort: str | None = None) -> str:
            calls.append(("render_detail", self.model))
            return "상세 본문이다."

        def embed(self, text: str, *, task_type: str | None = None, title: str | None = None) -> list[float]:
            calls.append(("embed", self.model))
            return [0.5] * 8

        def judge_same_entity(self, mc, *, document_id: str | None = None) -> bool:
            return False

        def judge_relationship(self, rc, *, document_id: str | None = None) -> RelationJudgement:
            return RelationJudgement(has_relation=False, reason="none")

    def _fake_build(slot_settings: Any):
        if slot_settings.gemini_model == "gemini-4-hydrogen" and slot_settings.provider == "gemini":
            return _FakeStageProvider("gemini", "gemini-4-hydrogen")
        if slot_settings.provider == "mock":
            return _FakeStageProvider("mock", "mock-embed-64d")
        return _FakeStageProvider(slot_settings.provider, getattr(slot_settings, "antigravity_model", slot_settings.gemini_model))

    import claire.extract.provider as provider_mod

    monkeypatch.setattr(provider_mod, "_build_direct_provider", _fake_build)
    settings = Settings(
        _env_file=None,
        CLAIRE_PROVIDER="antigravity",
        CLAIRE_ENVIRONMENT="development",
        CLAIRE_AGY_MODEL="gemini-3.8-flash",
        GEMINI_API_KEY="test-key",
        feature_routing={
            "extract_graph": {"provider": "antigravity", "model": "gemini-3.8-flash", "effort": "medium"},
            "summarize": {"provider": "gemini", "model": "gemini-4-hydrogen", "effort": "high"},
            "embed": {"provider": "gemini", "model": "gemini-embedding-001", "effort": ""},
        },
    )
    routed = get_provider(settings)
    assert isinstance(routed, FeatureRoutedProvider)

    conn = _memory_db()
    vstore = VectorStore(conn, "brute")
    doc = Document(id="doc_lineage_1", title="Claire Architecture", raw_text="Claire Engine builds a verified knowledge graph with decoupled extract and summary stages.", source_type="text", content_hash="h_lineage_1")
    dbm.insert_document(conn, doc)
    report = IngestReport(document_id=doc.id, title=doc.title, source_type=doc.source_type)

    ok, err = extract_resolve_store(conn, routed, vstore, doc, report, settings=settings)
    assert ok is True and err is None
    assert ("extract", "gemini-3.8-flash") in calls
    assert ("summarize_document", "gemini-4-hydrogen") in calls
    assert report.summary == "요약 모델이 생성한 독립 요약이다."

    stored_doc = dbm.get_document(conn, doc.id)
    assert stored_doc is not None and isinstance(stored_doc.meta.get("model_lineage"), dict)
    lineage = stored_doc.meta["model_lineage"]
    assert lineage["final_output_models"]["extract_graph"]["model"] == "gemini-3.8-flash"
    assert lineage["final_output_models"]["summarize"]["model"] == "gemini-4-hydrogen"
    assert lineage["fallbacks_triggered"] == []
