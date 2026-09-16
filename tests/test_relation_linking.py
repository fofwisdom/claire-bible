"""지식 그래프 자동 관계 형성 파이프라인 및 다목적 릴레이션 판정 (Phase 2) 검증."""

from __future__ import annotations

import sqlite3
from argparse import Namespace
from pathlib import Path

import pytest

from claire.config import get_settings
from claire.extract.prompts import judge_relationship_prompt
from claire.extract.provider import (
    ExtractedEntity,
    ExtractedRelation,
    ExtractionResult,
    MockProvider,
    RelationCandidate,
    RelationJudgement,
)
from claire.extract.resolver import ResolutionResult, resolve_or_create
from claire.ingest.pipeline import extract_resolve_store, ingest
from claire.ontology.base import Document, Entity, Relation
from claire.ontology.registry import classify_relation_type, validate_relation
from claire.ontology.types import RelationType
from claire.store import db as dbm
from claire.store.graph import GraphStore
from claire.store.vectors import VectorStore


def _mem_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    dbm.init_db(conn)
    return conn


# --- 1. 온톨로지 신규 관계 타입 검증 ---

def test_ontology_improves_and_derived_from():
    assert "improves" in [t.value for t in RelationType]
    assert "derived_from" in [t.value for t in RelationType]

    vr1 = validate_relation("improves", "Tool", "Tool")
    assert vr1.ok is True
    assert vr1.provisional is False

    vr2 = validate_relation("derived_from", "Model", "Model")
    assert vr2.ok is True
    assert vr2.provisional is False


# --- 2. GraphStore 단위 검증 ---

def test_graph_store_add_edge_and_query():
    conn = _mem_conn()
    gstore = GraphStore(conn)

    e1 = Entity(type="Tool", name="vLLM", observations=["LLM engine"], sources=["doc1"])
    e2 = Entity(type="Concept", name="PagedAttention", observations=["Memory algorithm"], sources=["doc2"])
    dbm.upsert_entity(conn, e1)
    dbm.upsert_entity(conn, e2)

    # 1) 정상 엣지 생성
    rel = gstore.add_edge(e1.id, e2.id, "uses", confidence=0.9, sources=["doc1"])
    assert rel is not None
    assert rel.type == "uses"
    assert rel.source_id == e1.id
    assert rel.target_id == e2.id
    assert rel.confidence == 0.9
    assert "doc1" in rel.sources

    # 2) 엣지 존재 여부 확인 (has_edge)
    assert gstore.has_edge(e1.id, e2.id, "uses") is True
    assert gstore.has_edge(e2.id, e1.id, "uses", bidirectional=True) is True
    assert gstore.has_edge(e2.id, e1.id, "uses", bidirectional=False) is False
    assert gstore.has_edge(e1.id, e2.id, "improves") is False

    # 3) 동일 엣지 추가 시 멱등적 병합 (sources 누적, confidence 갱신)
    rel_merged = gstore.add_edge(e1.id, e2.id, "uses", confidence=0.95, sources=["doc3"])
    assert rel_merged is not None
    assert rel_merged.confidence == 0.95
    assert set(rel_merged.sources) == {"doc1", "doc3"}
    assert len(gstore.all_edges()) == 1

    # 4) 자기 자신 루프 거부
    assert gstore.add_edge(e1.id, e1.id, "uses") is None

    # 5) 존재하지 않는 엔티티 거부
    assert gstore.add_edge(e1.id, "nonexistent_id", "uses") is None


# --- 3. Relation Candidate, Prompt & Provider Judge 검증 ---

def test_judge_relationship_prompt_construction():
    rc = RelationCandidate(
        entity_a_name="FlashAttention-2",
        entity_a_type="Concept",
        entity_a_observations=["Faster exact attention algorithm", "Better parallelism"],
        entity_a_aliases=["FA2"],
        entity_b_name="FlashAttention",
        entity_b_type="Concept",
        entity_b_observations=["IO-aware exact attention algorithm"],
        entity_b_aliases=["FA1"],
        similarity_score=0.82,
        context="FlashAttention-2 improves upon FlashAttention throughput by 2x.",
    )
    prompt = judge_relationship_prompt(rc)
    assert "FlashAttention-2" in prompt
    assert "FlashAttention" in prompt
    assert "improves" in prompt
    assert "uses" in prompt
    assert "HIGH PRECISION" in prompt
    assert "Faster exact attention algorithm" in prompt


def test_mock_provider_judge_relationship():
    prov = MockProvider()

    # improves 휴리스틱
    rc1 = RelationCandidate(
        entity_a_name="FlashAttention-2",
        entity_a_type="Concept",
        entity_a_observations=["Improves upon FlashAttention throughput"],
        entity_b_name="FlashAttention",
        entity_b_type="Concept",
        entity_b_observations=["Exact attention"],
    )
    j1 = prov.judge_relationship(rc1)
    assert j1.has_relation is True
    assert j1.relation_type == "improves"
    assert j1.direction == "forward"

    # uses 휴리스틱
    rc2 = RelationCandidate(
        entity_a_name="vLLM",
        entity_a_type="Tool",
        entity_a_observations=["High throughput serving engine using pagedattention"],
        entity_b_name="PagedAttention",
        entity_b_type="Concept",
        entity_b_observations=["Memory manager"],
    )
    j2 = prov.judge_relationship(rc2)
    assert j2.has_relation is True
    assert j2.relation_type == "uses"
    assert j2.direction == "forward"

    # 무관계
    rc3 = RelationCandidate(
        entity_a_name="Django",
        entity_a_type="Framework",
        entity_a_observations=["Web framework"],
        entity_b_name="PyTorch",
        entity_b_type="Framework",
        entity_b_observations=["Deep learning"],
    )
    j3 = prov.judge_relationship(rc3)
    assert j3.has_relation is False


# --- 4. 파이프라인 전역 횡단 링킹 종단간(End-to-End) 검증 ---

class CustomJudgeMockProvider(MockProvider):
    def __init__(self):
        super().__init__()
        self.embed_map: dict[str, list[float]] = {}
        self.judge_rel_calls: list[RelationCandidate] = []

    def embed(self, text: str, *, task_type: str | None = None, title: str | None = None) -> list[float]:
        for k, v in self.embed_map.items():
            if k.lower() in text.lower():
                return v
        return super().embed(text, task_type=task_type, title=title)

    def extract(self, doc: Document, ontology_block: str | None = None, **kwargs) -> ExtractionResult:
        if "doc1" in (doc.id or ""):
            return ExtractionResult(
                summary="vLLM 관련 문서",
                entities=[ExtractedEntity(name="vLLM", type="Tool", observations=["High performance LLM serving engine using pagedattention"])],
                relations=[],
            )
        elif "doc2" in (doc.id or ""):
            return ExtractionResult(
                summary="PagedAttention 관련 문서",
                entities=[ExtractedEntity(name="PagedAttention", type="Concept", observations=["Memory manager for attention keys and values"])],
                relations=[],
            )
        return super().extract(doc, ontology_block or "")

    def judge_relationship(self, rc: RelationCandidate) -> RelationJudgement:
        self.judge_rel_calls.append(rc)
        # vLLM과 PagedAttention 사이에 uses 관계 수립
        if ("vllm" in rc.entity_a_name.lower() and "pagedattention" in rc.entity_b_name.lower()) or \
           ("pagedattention" in rc.entity_a_name.lower() and "vllm" in rc.entity_b_name.lower()):
            is_forward = "vllm" in rc.entity_a_name.lower()
            return RelationJudgement(
                has_relation=True,
                relation_type="uses",
                direction="forward" if is_forward else "backward",
                reason="vLLM uses PagedAttention algorithm",
                confidence=0.95,
            )
        return RelationJudgement(has_relation=False)


def test_pipeline_cross_document_relation_linking(tmp_path):
    conn = _mem_conn()
    vstore = VectorStore(conn, "brute")
    provider = CustomJudgeMockProvider()
    vault_dir = tmp_path / "vault"
    vault_dir.mkdir(parents=True, exist_ok=True)

    # 서로 코사인 유사도 0.82 (Tier 3: 직접 관계 후보 대역 [0.70, 0.93))를 갖는 의사 임베딩 설정
    # v1 = [1.0, 0.0], v2 = [0.82, 0.572] -> dot product = 0.82
    provider.embed_map["vllm"] = [1.0, 0.0]
    provider.embed_map["pagedattention"] = [0.82, 0.57236]

    # 1) 첫 번째 문서 적재 (vLLM 노드 생성)
    doc1 = Document(id="doc1", title="vLLM Engine", raw_text="vLLM is a high-throughput engine")
    rep1 = ingest(
        "https://example.com/vllm",
        conn=conn,
        provider=provider,
        vstore=vstore,
        vault_dir=vault_dir,
        prefetched=doc1,
    )
    assert rep1.error is None
    assert rep1.entities_created == 1
    assert rep1.cross_relations_added == 0

    # 2) 두 번째 문서 적재 (PagedAttention 노드 생성, 문서 내에는 vLLM 언급 없음)
    doc2 = Document(id="doc2", title="PagedAttention Paper", raw_text="PagedAttention optimizes KV cache")
    rep2 = ingest(
        "https://example.com/pagedattention",
        conn=conn,
        provider=provider,
        vstore=vstore,
        vault_dir=vault_dir,
        prefetched=doc2,
    )
    assert rep2.error is None
    assert rep2.entities_created == 1

    # Phase 2 횡단 관계 자동 수립 검증!
    assert rep2.cross_relations_added == 1
    assert len(rep2.cross_linked_relations) == 1
    assert "uses" in rep2.cross_linked_relations[0]

    # DB 및 GraphStore에 실제 엣지가 저장되었는지 검증
    gstore = GraphStore(conn)
    vllm_ent = dbm.find_entities_by_name_or_alias(conn, "vLLM")[0]
    pa_ent = dbm.find_entities_by_name_or_alias(conn, "PagedAttention")[0]

    assert gstore.has_edge(vllm_ent.id, pa_ent.id, "uses") is True

    # Vault 마크다운 파일에 상호 위키링크가 반영되었는지 검증
    from claire.store.vault import _entity_filename

    vllm_md = (vault_dir / _entity_filename(vllm_ent)).read_text(encoding="utf-8")
    assert "pagedattention" in vllm_md.lower()

    # 3) 동일 문서 재적재 시 이미 존재하는 엣지이므로 중복 LLM 질의 없이 스킵되는지 확인
    initial_judge_count = len(provider.judge_rel_calls)
    doc3 = Document(id="doc3", title="PagedAttention Doc 3", raw_text="PagedAttention again")
    rep3 = ingest(
        "https://example.com/pagedattention-2",
        conn=conn,
        provider=provider,
        vstore=vstore,
        vault_dir=vault_dir,
        prefetched=doc3,
    )
    # 이미 vLLM과 PagedAttention 사이에 엣지가 존재하므로 has_edge 체크로 judge_relationship 호출 0
    assert len(provider.judge_rel_calls) == initial_judge_count


# --- 5. CLI claire link-relations 검증 ---

def test_cmd_link_relations_dry_run_and_apply(tmp_path, monkeypatch):
    from claire.cli import cmd_link_relations

    db_file = tmp_path / "test_link.db"
    conn = dbm.connect(db_file)
    dbm.init_db(conn)
    vstore = VectorStore(conn, "brute")

    # 2개 엔티티 생성 (코사인 유사도 0.82)
    e1 = Entity(type="Concept", name="FlashAttention-2", observations=["Improves upon FlashAttention throughput"], sources=["doc1"])
    e2 = Entity(type="Concept", name="FlashAttention", observations=["Exact attention"], sources=["doc2"])
    dbm.upsert_entity(conn, e1)
    dbm.upsert_entity(conn, e2)

    vstore.put(e1.id, [1.0, 0.0], "test")
    vstore.put(e2.id, [0.82, 0.57236], "test")
    conn.close()

    monkeypatch.setenv("CLAIRE_DB_PATH", str(db_file))
    monkeypatch.setenv("CLAIRE_PROVIDER", "mock")
    monkeypatch.setenv("CLAIRE_VECTOR_ADAPTIVE_CENTERING", "false")
    get_settings.cache_clear()

    # 1) dry-run 실행
    ret_dry = cmd_link_relations(Namespace(limit=10, min_score=0.70, dry_run=True, theme=None))
    assert ret_dry == 0

    conn_check = dbm.connect(db_file)
    assert len(dbm.all_relations(conn_check)) == 0
    conn_check.close()

    # 2) apply 실행
    ret_apply = cmd_link_relations(Namespace(limit=10, min_score=0.70, dry_run=False, theme=None))
    assert ret_apply == 0

    conn_check2 = dbm.connect(db_file)
    rels = dbm.all_relations(conn_check2)
    assert len(rels) == 1
    assert rels[0].type == "improves"
    conn_check2.close()
