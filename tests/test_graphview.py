"""그래프 뷰어 데이터 변환 — vis.js nodes/edges + 고아 엣지 제외."""

from __future__ import annotations

import sqlite3

from claire.graphview import (
    graph_json, node_detail, synthesis_context, synthesize, GRAPH_HTML,
)
from claire.extract.provider import MockProvider
from claire.ontology.base import Document, Entity, Relation
from claire.store import db as dbm


def _db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    dbm.init_db(conn)
    return conn


def test_graph_json_nodes_edges():
    conn = _db()
    dbm.upsert_entity(conn, Entity(id="e1", type="Tool", name="Claude Code",
                                   observations=["CLI coding agent"], sources=["d"]))
    dbm.upsert_entity(conn, Entity(id="e2", type="Org", name="Anthropic", sources=["d"]))
    dbm.upsert_relation(conn, Relation(id="r1", type="authored_by",
                                       source_id="e1", target_id="e2", sources=["d"]))
    g = graph_json(conn)
    assert g["stats"] == {"entities": 2, "relations": 1}
    ids = {n["id"] for n in g["nodes"]}
    assert ids == {"e1", "e2"}
    n1 = next(n for n in g["nodes"] if n["id"] == "e1")
    assert n1["label"] == "Claude Code" and n1["group"] == "Tool"
    assert n1["title"].startswith("CLI coding agent")  # observation 툴팁
    e = g["edges"][0]
    assert e["from"] == "e1" and e["to"] == "e2" and e["label"] == "authored_by"


def test_graph_json_excludes_dangling_edges():
    """양 끝 노드가 다 있는 관계만 — 고아 엣지는 vis.js 유령 노드를 만들어 깨진다."""
    conn = _db()
    dbm.upsert_entity(conn, Entity(id="e1", type="Tool", name="A", sources=["d"]))
    # target 이 존재하지 않는 관계
    dbm.upsert_relation(conn, Relation(id="r1", type="uses",
                                       source_id="e1", target_id="ghost", sources=["d"]))
    g = graph_json(conn)
    assert g["stats"]["entities"] == 1
    assert g["edges"] == [] and g["stats"]["relations"] == 0


def test_graph_html_self_contained_markers():
    # 페이지가 graph/node 를 fetch 하고 vis.js 를 로드하는지(렌더 진입점 존재).
    assert "fetch('graph')" in GRAPH_HTML
    assert "vis-network" in GRAPH_HTML
    assert "id=\"net\"" in GRAPH_HTML
    assert "loadNode" in GRAPH_HTML and "id=\"panel\"" in GRAPH_HTML  # 상세 패널


def test_node_detail_assembles_knowledge():
    """노드 상세 = 전체 observations + 소스 문서(제목·요약) + 타입 있는 이웃."""
    conn = _db()
    # 문서 + 추출(summary 보관)
    dbm.insert_document(conn, Document(id="d1", url="https://x/1", title="MCP 소개",
                                       raw_text="...", source_type="web", content_hash="h"))
    dbm.log_extraction(conn, document_id="d1", provider="mock", model="m",
                       prompt_version="v", raw_response='{"summary":"MCP는 표준 프로토콜"}')
    dbm.upsert_entity(conn, Entity(id="e1", type="Concept", name="MCP",
                                   aliases=["Model Context Protocol"],
                                   observations=["LLM 컨텍스트 표준"], sources=["d1"]))
    dbm.upsert_entity(conn, Entity(id="e2", type="Tool", name="Claude Code", sources=["d1"]))
    dbm.upsert_relation(conn, Relation(id="r1", type="uses",
                                       source_id="e2", target_id="e1", sources=["d1"]))

    d = node_detail(conn, "e1")
    assert d["name"] == "MCP" and d["type"] == "Concept"
    assert d["aliases"] == ["Model Context Protocol"]
    assert d["observations"] == ["LLM 컨텍스트 표준"]
    # 소스 문서: 제목 + extractions 에서 꺼낸 summary
    assert len(d["documents"]) == 1
    assert d["documents"][0]["title"] == "MCP 소개"
    assert d["documents"][0]["summary"] == "MCP는 표준 프로토콜"
    # 이웃: Claude Code 가 e1 을 uses (e1 입장에선 incoming)
    assert len(d["neighbors"]) == 1
    n = d["neighbors"][0]
    assert n["name"] == "Claude Code" and n["rel"] == "uses" and n["dir"] == "in"


def test_node_detail_missing_returns_none():
    assert node_detail(_db(), "nope") is None


def _seed_two(conn):
    dbm.insert_document(conn, Document(id="d1", url="https://x/1", title="MCP 문서",
                                       raw_text=".", source_type="web", content_hash="h"))
    dbm.log_extraction(conn, document_id="d1", provider="mock", model="m",
                       prompt_version="v", raw_response='{"summary":"MCP는 컨텍스트 표준"}')
    dbm.upsert_entity(conn, Entity(id="e1", type="Concept", name="MCP",
                                   aliases=["Model Context Protocol"],
                                   observations=["LLM 컨텍스트 표준"], sources=["d1"]))
    dbm.upsert_entity(conn, Entity(id="e2", type="Tool", name="Claude Code",
                                   observations=["CLI agent"], sources=["d1"]))
    dbm.upsert_relation(conn, Relation(id="r1", type="uses",
                                       source_id="e2", target_id="e1", sources=["d1"]))


def test_synthesis_context_assembles_knowledge():
    """종합 컨텍스트(결정론적): 선택 노드의 관찰·연결·출처요약을 모은다."""
    conn = _db()
    _seed_two(conn)
    ctx, names = synthesis_context(conn, ["e1", "e2"])
    assert names == ["MCP", "Claude Code"]
    assert "LLM 컨텍스트 표준" in ctx          # 관찰
    assert "Model Context Protocol" in ctx     # 별칭
    assert "uses" in ctx                        # 연결
    assert "MCP는 컨텍스트 표준" in ctx         # 출처요약
    # 존재하지 않는 id 는 무시
    ctx2, names2 = synthesis_context(conn, ["e1", "ghost"])
    assert names2 == ["MCP"]


def test_synthesize_routes_context_through_provider():
    """종합 경로 연결: context 가 provider.summarize_search 로 흘러가 답이 나온다(mock)."""
    conn = _db()
    _seed_two(conn)
    out = synthesize(conn, MockProvider(), ["e1", "e2"])
    assert "error" not in out
    assert out["entities"] == ["MCP", "Claude Code"]
    # mock 은 query::context 를 반환 → 컨텍스트(관찰)가 답에 포함됨
    assert "LLM 컨텍스트 표준" in out["answer"]


def test_synthesize_empty_selection():
    assert "error" in synthesize(_db(), MockProvider(), [])
    assert "error" in synthesize(_db(), MockProvider(), ["ghost"])
