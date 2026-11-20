"""Tests for per-feature provider routing, BYOK split-key encryption, JEV filter/hybrid gating, and model lineage persistence."""

from __future__ import annotations

import concurrent.futures
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

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


def test_old_sealed_key_needs_the_seal_once_and_is_then_unsealed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A key stored with the old split-key seal needs the seal for the first decryption. That decryption stores it again with the server master key only (owner decision 2026-11-19)."""
    monkeypatch.setenv("CLAIRE_SECRET_KEY", "55" * 32)
    reset_secret_service()
    try:
        conn = auth_db.connect_auth_db(auth_db.auth_db_path(tmp_path))
        try:
            secret_svc = SecretService(master_key=bytes.fromhex("55" * 32))
            seal = generate_user_seal()
            provider_keys.put_key(conn, account_id=7, provider="gemini", api_key="AIzaSplitKeySecretTestValue012345", status="active", user_seal=seal, sealed=True, secret_service=secret_svc)
            clear_volatile_user_seals()
            with pytest.raises((provider_keys.ProviderKeyError, SecretDecryptionError)):
                provider_keys.load_active_key(conn, account_id=7, purpose="request", secret_service=secret_svc)
            restored = provider_keys.load_active_key(conn, account_id=7, purpose="request", secret_service=secret_svc, user_seal=seal)
            assert restored is not None and restored.api_key == "AIzaSplitKeySecretTestValue012345"
            clear_volatile_user_seals()
            again = provider_keys.load_active_key(conn, account_id=7, purpose="request", secret_service=secret_svc)
            assert again is not None and again.api_key == "AIzaSplitKeySecretTestValue012345"
            # A new key needs no seal.
            provider_keys.put_key(conn, account_id=8, provider="gemini", api_key="AIzaPlainKeySecretTestValue0123456", status="active", secret_service=secret_svc)
            clear_volatile_user_seals()
            assert provider_keys.load_active_key(conn, account_id=8, purpose="request", secret_service=secret_svc).api_key == "AIzaPlainKeySecretTestValue0123456"
        finally:
            conn.close()
    finally:
        reset_secret_service()


def _judge_that_merges(name: str | None, calls: list[str]):
    def judge(nm: str, et: str, obs: list[str], cand: Entity, aliases: list[str] | None = None) -> bool:
        calls.append(cand.name)
        return cand.name == name
    return judge


def test_jev_shadow_never_changes_the_decision(monkeypatch: pytest.MonkeyPatch) -> None:
    """Jev has only the shadow role (owner decision 2026-11-20): a low score skips no judge call, and a high score merges nothing without the judge."""
    import claire.extract.resolver as resolver_mod

    monkeypatch.setattr(resolver_mod, "_get_tier_thresholds", lambda: (0.93, 0.72, 0.70, 0.55, False))
    for scores, judge_says_same in (({"AlphaCore": 0.05, "BetaTool": 0.05}, "AlphaCore"), ({"AlphaCore": 0.99, "BetaTool": 0.99}, None)):
        conn = _memory_db()
        vstore = VectorStore(conn, "brute")
        e_alpha = Entity(type="Tool", name="AlphaCore", observations=["engine"], sources=["d0"])
        e_beta = Entity(type="Tool", name="BetaTool", observations=["cli"], sources=["d0"])
        dbm.upsert_entity(conn, e_alpha)
        dbm.upsert_entity(conn, e_beta)
        vstore.put(e_alpha.id, [0.82, 0.57, 0.0], "claire")
        vstore.put(e_beta.id, [0.80, 0.60, 0.0], "claire")
        judge_calls: list[str] = []
        shadow = JevShadow(_FakeJevClient(scores), executor=_InlineExecutor())
        res = resolve_or_create(
            conn, vstore, name="Alpha Engine", etype="Tool", aliases=[], observations=["engine"], document_id="d1",
            embed_fn=lambda: [1.0, 0.0, 0.0],
            judge_fn=_judge_that_merges(judge_says_same, judge_calls),
            shadow=shadow,
        )
        assert judge_calls, "the judge decides every borderline candidate"
        outcomes = {c["name"]: c["outcome"] for c in res.decision.candidates}
        assert not {"jev_filtered", "jev_hybrid_auto_merge"} & set(outcomes.values())
        assert res.decision.shadow["role"] == "shadow"
        assert all("jev_noul" in c for c in res.decision.candidates)
        if judge_says_same:
            assert res.created is False and res.entity.id == e_alpha.id and res.decision.stage == "borderline_llm_judge"
            # The candidate after the merge keeps its name in the record (not_reached)
            assert outcomes == {"AlphaCore": "judge_same", "BetaTool": "not_reached"}
        else:
            assert res.created is True and res.decision.stage == "no_match"


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


def test_jev_shadow_asks_about_invariant_rejects(monkeypatch: pytest.MonkeyPatch) -> None:
    """Design decision 3: Jev gets every candidate of the loop, also a candidate that the invariants reject before the judge."""
    import claire.extract.resolver as resolver_mod

    monkeypatch.setattr(resolver_mod, "_get_tier_thresholds", lambda: (0.93, 0.72, 0.70, 0.55, False))
    conn = _memory_db()
    vstore = VectorStore(conn, "brute")
    e_alpha = Entity(type="Tool", name="AlphaCore", observations=["engine"], sources=["d0"])
    e_wrong = Entity(type="Tool", name="Alpha Engine 3.11", observations=["older release"], sources=["d0"])
    dbm.upsert_entity(conn, e_alpha)
    dbm.upsert_entity(conn, e_wrong)
    vstore.put(e_alpha.id, [0.82, 0.57, 0.0], "claire")
    vstore.put(e_wrong.id, [0.81, 0.58, 0.0], "claire")
    client = _FakeJevClient({"AlphaCore": 0.20, "Alpha Engine 3.11": 0.99})
    res = resolve_or_create(
        conn, vstore, name="Alpha Engine 3.12", etype="Tool", aliases=[], observations=["new release"], document_id="d1",
        embed_fn=lambda: [1.0, 0.0, 0.0],
        judge_fn=lambda nm, et, obs, cand: False,
        shadow=JevShadow(client, executor=_InlineExecutor()),
    )
    assert len(client.requests) == 1
    sent_names = {c["name"] for c in client.requests[0][0]["candidates"].values()}
    assert sent_names == {"AlphaCore", "Alpha Engine 3.11"}
    by_name = {c["name"]: c for c in res.decision.candidates}
    assert by_name["Alpha Engine 3.11"]["outcome"] == "invariant_reject" and by_name["Alpha Engine 3.11"]["jev_noul"] == 0.99
    assert res.created is True


def test_embedding_task_types_fixed_and_cli_rejected_from_server_scope(tmp_path: Path) -> None:
    """Verify query search fixes RETRIEVAL_QUERY, Gemini models discovery works, and server_scope blocks CLI."""
    from claire.retrieval.query import search

    embed_calls: list[tuple[str, str | None]] = []

    class _RecordingProvider:
        name = "gemini"
        embed_model = "claire"

        def has_embed_capability(self) -> bool:
            return True

        def embed(self, text: str, *, task_type: str | None = None, title: str | None = None) -> list[float]:
            embed_calls.append((text, task_type))
            return [1.0, 0.0, 0.0]

    conn = _memory_db()
    vstore = VectorStore(conn, "brute")
    search(conn, vstore, _RecordingProvider(), "find python library", summarize=False)
    assert embed_calls == [("find python library", "RETRIEVAL_QUERY")]

    pm = ProviderManager(data_dir=tmp_path / "data", auto_migrate=False)
    with pytest.raises(ValueError):
        pm.update_config({"active_provider": "antigravity"}, server_scope=True)
    with pytest.raises(ValueError):
        pm.update_config({"feature_routing": {"extract_graph": {"provider": "codex"}}}, server_scope=True)
