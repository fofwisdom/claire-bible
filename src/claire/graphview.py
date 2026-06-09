"""읽기전용 그래프 시각화 — vis.js 용 데이터 변환 + 정적 HTML 페이지.

로컬 inject API(aiohttp)가 /graph(JSON)와 /(HTML)로 노출한다. 정본 DB 를 읽기만 한다.
"""

from __future__ import annotations

import sqlite3

from .store import db as dbm


def graph_json(conn: sqlite3.Connection) -> dict:
    """엔티티/관계를 vis.js network 형식(nodes/edges)으로. dangling edge 는 제외."""
    ents = dbm.all_entities(conn)
    rels = dbm.all_relations(conn)
    ent_ids = {e.id for e in ents}
    nodes = [
        {
            "id": e.id,
            "label": e.name,
            "group": e.type,
            "title": (e.observations[0][:200] if e.observations else e.type),
        }
        for e in ents
    ]
    # 양 끝 노드가 모두 존재하는 관계만(고아 엣지는 vis.js 가 유령 노드를 만들어 깨짐).
    edges = [
        {"from": r.source_id, "to": r.target_id, "label": r.type,
         "arrows": "to", "dashes": r.provisional}
        for r in rels
        if r.source_id in ent_ids and r.target_id in ent_ids
    ]
    return {"nodes": nodes, "edges": edges,
            "stats": {"entities": len(nodes), "relations": len(edges)}}


# vis.js 9(unpkg CDN) 기반 단일 페이지. /graph 를 fetch 해 네트워크로 렌더.
GRAPH_HTML = """<!doctype html>
<html lang="ko"><head><meta charset="utf-8"/>
<title>claire_bible — 지식 그래프</title>
<script src="https://unpkg.com/vis-network/standalone/umd/vis-network.min.js"></script>
<style>
  html,body{margin:0;height:100%;font-family:system-ui,sans-serif;background:#0e1116;color:#d7dbe0}
  #bar{padding:8px 12px;background:#161b22;border-bottom:1px solid #2a2f37;font-size:14px}
  #bar b{color:#7ee787}
  #net{width:100%;height:calc(100% - 38px)}
  input{background:#0e1116;color:#d7dbe0;border:1px solid #2a2f37;border-radius:4px;padding:3px 8px;margin-left:8px}
</style></head>
<body>
<div id="bar">claire_bible 지식 그래프 — <span id="stat">로딩…</span>
  <input id="q" placeholder="이름으로 강조(검색)" oninput="hl(this.value)"/></div>
<div id="net"></div>
<script>
let net, allNodes;
fetch('graph').then(r=>r.json()).then(d=>{
  document.getElementById('stat').innerHTML =
    '엔티티 <b>'+d.stats.entities+'</b> · 관계 <b>'+d.stats.relations+'</b>';
  allNodes = new vis.DataSet(d.nodes);
  const data = {nodes: allNodes, edges: new vis.DataSet(d.edges)};
  const opts = {
    nodes:{shape:'dot',size:14,font:{color:'#d7dbe0',size:13}},
    edges:{color:{color:'#3a4250',highlight:'#7ee787'},font:{color:'#8b949e',size:10},smooth:false},
    groups:{},
    physics:{stabilization:{iterations:200},barnesHut:{gravitationalConstant:-8000,springLength:120}},
    interaction:{hover:true,tooltipDelay:120}
  };
  net = new vis.Network(document.getElementById('net'), data, opts);
});
function hl(q){
  if(!allNodes) return;
  q=q.trim().toLowerCase();
  allNodes.forEach(n=>{
    const on = q && n.label.toLowerCase().includes(q);
    allNodes.update({id:n.id, color: on ? '#7ee787' : undefined,
                     font:{color: on ? '#7ee787' : '#d7dbe0'}});
  });
}
</script></body></html>
"""
