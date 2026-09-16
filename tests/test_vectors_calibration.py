"""벡터 캘리브레이션, 적응형 중심화, RRF 결합 및 4-티어 유사도 대역 검증."""

from __future__ import annotations

import sqlite3
from argparse import Namespace

from claire.config import get_settings
from claire.extract.frame import format_entity_frame
from claire.extract.resolver import ResolutionResult, resolve_or_create
from claire.ontology.base import Entity
from claire.store import db as dbm
from claire.store.vectors import (
    VectorStore,
    center_and_normalize,
    compute_mean_vector,
    reciprocal_rank_fusion,
)


def _mem_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    dbm.init_db(conn)
    return conn


def test_compute_mean_and_center_normalize():
    vecs = [
        [1.0, 2.0, 3.0],
        [3.0, 2.0, 1.0],
    ]
    mean = compute_mean_vector(vecs)
    assert mean == [2.0, 2.0, 2.0]

    v = [3.0, 2.0, 2.0]
    centered = center_and_normalize(v, mean)
    # v - mean = [1.0, 0.0, 0.0], norm = 1.0 -> [1.0, 0.0, 0.0]
    assert centered == [1.0, 0.0, 0.0]


def test_vector_store_adaptive_centering():
    conn = _mem_conn()
    vstore = VectorStore(conn, "brute")

    # 배경 공통 오프셋(Common Bias)을 가진 벡터들
    bias = [10.0, 10.0]
    v1 = [bias[0] + 1.0, bias[1] + 0.0]
    v2 = [bias[0] - 1.0, bias[1] + 0.0]

    vstore.put("ent1", v1, "test")
    vstore.put("ent2", v2, "test")
    assert vstore.count() == 2

    q = [bias[0] + 0.5, bias[1] + 0.0]

    # adaptive_center=False 시 공통 오프셋 때문에 두 점수가 모두 매우 높음 (>0.99)
    raw_results = dict(vstore.search(q, limit=2, adaptive_center=False))
    assert raw_results["ent1"] > 0.99
    assert raw_results["ent2"] > 0.99

    # adaptive_center=True 시 중심화로 인해 ent1(양의 방향)과 ent2(음의 방향)의 변별력이 대폭 확대됨
    centered_results = dict(vstore.search(q, limit=2, adaptive_center=True))
    assert centered_results["ent1"] > centered_results["ent2"]
    assert centered_results["ent1"] > 0.9
    assert centered_results["ent2"] < 0.0


def test_reciprocal_rank_fusion():
    list_a = [("doc1", 0.9), ("doc2", 0.8), ("doc3", 0.7)]
    list_b = [("doc2", 0.95), ("doc3", 0.85), ("doc4", 0.75)]

    fused = reciprocal_rank_fusion([list_a, list_b], k=60)
    top_id = fused[0][0]
    # doc2는 list_a 2위, list_b 1위로 최상위로 융합되어야 함
    assert top_id == "doc2"
    fused_dict = dict(fused)
    assert "doc1" in fused_dict
    assert "doc4" in fused_dict


def test_format_entity_frame():
    ent = Entity(
        type="Tool",
        name="vLLM",
        aliases=["vllm-project"],
        observations=["High-throughput LLM serving engine", "PagedAttention memory manager"],
        sources=["doc_1"],
    )
    frame = format_entity_frame(ent)
    assert "[ENTITY: vLLM]" in frame
    assert "TYPE: Tool" in frame
    assert "ALIASES: vllm-project" in frame
    assert "PagedAttention memory manager" in frame


def test_resolver_4tier_candidate_routing(monkeypatch):
    conn = _mem_conn()
    vstore = VectorStore(conn, "brute")

    # Seed 엔티티 (기준 벡터: [1.0, 0.0])
    e_seed = Entity(type="Tool", name="vLLM", observations=["Serving engine"], sources=["doc_0"])
    dbm.upsert_entity(conn, e_seed)
    vstore.put(e_seed.id, [1.0, 0.0], "test")

    # 1) Tier 3 Relational 후보 (코사인 ~0.75): judge_fn이 DIFFERENT 반환 시
    res: ResolutionResult = resolve_or_create(
        conn,
        vstore,
        name="TGI",
        etype="Tool",
        aliases=[],
        observations=["Text Generation Inference"],
        document_id="doc_1",
        embed_fn=lambda: [0.75, 0.6614],  # cosine = 0.75
        judge_fn=lambda n, et, obs, cand: False,  # DIFFERENT
    )
    # 동일체가 아니므로 신규 생성
    assert res.created is True
    assert res.entity.name == "TGI"
    # 하지만 버려지지 않고 relational_candidates에 수집됨
    rel_cand_ids = [cid for cid, _ in res.relational_candidates]
    assert e_seed.id in rel_cand_ids


def test_cmd_reembed(monkeypatch, tmp_path):
    from claire.cli import cmd_reembed

    db_file = tmp_path / "test_reembed.db"
    conn = dbm.connect(db_file)
    dbm.init_db(conn)

    # 2개 엔티티 생성
    e1 = Entity(type="Tool", name="ToolA", observations=["Fast tool"], sources=["doc_1"])
    e2 = Entity(type="Concept", name="ConceptB", observations=["Key concept"], sources=["doc_1"])
    dbm.upsert_entity(conn, e1)
    dbm.upsert_entity(conn, e2)
    conn.close()

    monkeypatch.setenv("CLAIRE_DB_PATH", str(db_file))
    monkeypatch.setenv("CLAIRE_PROVIDER", "mock")
    get_settings.cache_clear()

    try:
        # dry-run 실행
        ret_dry = cmd_reembed(Namespace(limit=0, dry_run=True, theme=None))
        assert ret_dry == 0

        # 실제 재임베딩 실행
        ret_real = cmd_reembed(Namespace(limit=0, dry_run=False, theme=None))
        assert ret_real == 0

        # 벡터 스토어에 2개 엔티티 벡터가 정상 기록되었는지 확인
        conn2 = dbm.connect(db_file)
        vstore = VectorStore(conn2, "brute")
        assert vstore.count() == 2
        assert vstore.get(e1.id) is not None
        assert vstore.get(e2.id) is not None
        conn2.close()
    finally:
        get_settings.cache_clear()
