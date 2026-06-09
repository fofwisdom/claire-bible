"""그래프 뷰어 데이터 변환 — vis.js nodes/edges + 고아 엣지 제외."""

from __future__ import annotations

import sqlite3

from claire.graphview import graph_json, GRAPH_HTML
from claire.ontology.base import Entity, Relation
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
    # 페이지가 graph 데이터를 fetch 하고 vis.js 를 로드하는지(렌더 진입점 존재).
    assert "fetch('graph')" in GRAPH_HTML
    assert "vis-network" in GRAPH_HTML
    assert "id=\"net\"" in GRAPH_HTML
