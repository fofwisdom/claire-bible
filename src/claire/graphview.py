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


def node_detail(conn: sqlite3.Connection, entity_id: str) -> dict | None:
    """한 노드의 '쓸 수 있는 지식': 전체 observations + 소스 문서(제목·요약·URL) +
    타입 있는 이웃. 패널에 그대로 펼친다. 없으면 None."""
    ent = dbm.get_entity(conn, entity_id)
    if ent is None:
        return None

    neighbors = []
    for r in dbm.neighbors(conn, entity_id):
        out = r.source_id == entity_id
        other = dbm.get_entity(conn, r.target_id if out else r.source_id)
        if other:
            neighbors.append({
                "id": other.id, "name": other.name, "type": other.type,
                "rel": r.type, "dir": "out" if out else "in",
                "provisional": r.provisional,
            })

    documents = []
    for did in ent.sources:
        row = dbm.get_document_row(conn, did)
        if row:
            documents.append({
                "id": did,
                "title": row["title"] or "(제목 없음)",
                "url": row["url"],
                "summary": dbm.latest_extraction_summary(conn, did) or "",
            })

    return {
        "id": ent.id, "name": ent.name, "type": ent.type,
        "aliases": ent.aliases, "observations": ent.observations,
        "provisional": ent.provisional,
        "neighbors": neighbors, "documents": documents,
    }


def synthesis_context(conn: sqlite3.Connection, entity_ids: list[str]) -> tuple[str, list[str]]:
    """선택 노드들의 지식(관찰·연결·출처요약)을 LLM 종합용 컨텍스트 텍스트로 조립.

    결정론적(LLM 없음) — 이 텍스트가 summarize_search 의 근거가 된다. (context, names)."""
    blocks: list[str] = []
    names: list[str] = []
    for eid in entity_ids:
        ent = dbm.get_entity(conn, eid)
        if ent is None:
            continue
        names.append(ent.name)
        parts = [f"## {ent.name} ({ent.type})"]
        if ent.aliases:
            parts.append("별칭: " + ", ".join(ent.aliases))
        if ent.observations:
            parts.append("관찰: " + " ".join(ent.observations))
        rels = []
        for r in dbm.neighbors(conn, eid):
            out = r.source_id == eid
            other = dbm.get_entity(conn, r.target_id if out else r.source_id)
            if other:
                rels.append(f"{r.type} {'→' if out else '←'} {other.name}")
        if rels:
            parts.append("연결: " + ", ".join(rels[:12]))
        for did in ent.sources:
            summ = dbm.latest_extraction_summary(conn, did)
            if summ:
                parts.append(f"출처요약: {summ}")
        blocks.append("\n".join(parts))
    return "\n\n".join(blocks), names


def synthesize(conn, provider, entity_ids: list[str], query: str | None = None) -> dict:  # noqa: ANN001
    """선택 노드들을 아우르는 종합 지식 문서(인용 포함, 한국어)를 생성.

    summarize_search 재사용(검색 정리와 동일 경로) — 컨텍스트는 그래프(관찰·연결·출처요약).
    비용(LLM 호출)이 있으므로 호출측(API)에서 토큰 인증 + 명시적 액션으로만 부른다.
    """
    context, names = synthesis_context(conn, entity_ids)
    if not context:
        return {"error": "유효한 노드가 없습니다"}
    if not hasattr(provider, "summarize_search"):
        return {"error": "이 provider 는 종합을 지원하지 않습니다"}
    q = query or f"선택한 항목들({', '.join(names)})을 아우르는 핵심 지식을 정리해줘."
    answer = provider.summarize_search(q, context)
    return {"answer": answer, "entities": names, "query": q}


# vis.js 9(unpkg CDN) 기반 단일 페이지. /graph 로 그래프, 노드 클릭 시 /node 로 상세.
GRAPH_HTML = """<!doctype html>
<html lang="ko"><head><meta charset="utf-8"/>
<title>claire_bible — 지식 그래프</title>
<script src="https://unpkg.com/vis-network/standalone/umd/vis-network.min.js"></script>
<style>
  html,body{margin:0;height:100%;font-family:system-ui,sans-serif;background:#0e1116;color:#d7dbe0}
  #bar{padding:8px 12px;background:#161b22;border-bottom:1px solid #2a2f37;font-size:14px}
  #bar b{color:#7ee787}
  #wrap{display:flex;height:calc(100% - 39px)}
  #net{flex:1;min-width:0}
  #panel{width:380px;overflow:auto;padding:14px 16px;background:#10151c;border-left:1px solid #2a2f37;font-size:13px;line-height:1.5}
  #panel h2{margin:.2em 0;font-size:18px} #panel h2 small{color:#8b949e;font-size:12px;font-weight:normal}
  #panel h3{margin:1em 0 .3em;font-size:13px;color:#7ee787;border-bottom:1px solid #2a2f37;padding-bottom:2px}
  #panel ul{margin:.2em 0;padding-left:18px} #panel li{margin:.25em 0}
  #panel .doc{margin:.5em 0;padding:6px 8px;background:#161b22;border-radius:5px}
  #panel .doc p{margin:.3em 0 0;color:#adbac7} #panel a{color:#58a6ff;text-decoration:none}
  #panel .rel{color:#d29922;font-size:11px} #panel .al{color:#8b949e}
  #panel .hint{color:#6e7681;margin-top:2em}
  input{background:#0e1116;color:#d7dbe0;border:1px solid #2a2f37;border-radius:4px;padding:3px 8px;margin-left:8px;width:220px}
  button{background:#238636;color:#fff;border:0;border-radius:4px;padding:4px 10px;margin-left:8px;cursor:pointer;font-size:13px}
  button:hover{background:#2ea043}
  #panel .synth{white-space:pre-wrap;background:#161b22;border:1px solid #2a2f37;border-radius:5px;padding:10px;margin:.4em 0;line-height:1.6}
</style></head>
<body>
<div id="bar">claire_bible 지식 그래프 — <span id="stat">로딩…</span>
  <input id="q" placeholder="이름 검색(엔터=해당 노드로 이동)" oninput="hl(this.value)"/>
  <button id="synthbtn" onclick="synth()">🧩 선택 노드 종합</button></div>
<div id="wrap"><div id="net"></div>
  <div id="panel"><p class="hint">노드를 클릭하면 관찰·출처 문서·연결이 여기 표시됩니다.</p></div>
</div>
<script>
let net, allNodes;
const panel = document.getElementById('panel');
fetch('graph').then(r=>r.json()).then(d=>{
  document.getElementById('stat').innerHTML =
    '엔티티 <b>'+d.stats.entities+'</b> · 관계 <b>'+d.stats.relations+'</b>';
  allNodes = new vis.DataSet(d.nodes);
  const data = {nodes: allNodes, edges: new vis.DataSet(d.edges)};
  const opts = {
    nodes:{shape:'dot',size:14,font:{color:'#d7dbe0',size:13}},
    edges:{color:{color:'#3a4250',highlight:'#7ee787'},font:{color:'#8b949e',size:10},smooth:false},
    physics:{stabilization:{iterations:200},barnesHut:{gravitationalConstant:-8000,springLength:120}},
    interaction:{hover:true,tooltipDelay:120}
  };
  net = new vis.Network(document.getElementById('net'), data, opts);
  net.on('click', p => { if(p.nodes.length) loadNode(p.nodes[0]); });
});
function esc(s){return (s||'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}
function loadNode(id){
  if(net) net.selectNodes([id]);
  fetch('node?id='+encodeURIComponent(id)).then(r=>r.json()).then(renderPanel);
}
function renderPanel(d){
  if(!d || d.error){ panel.innerHTML='<p class=hint>노드를 찾을 수 없습니다.</p>'; return; }
  let h='<h2>'+esc(d.name)+' <small>'+esc(d.type)+(d.provisional?' ⚠️provisional':'')+'</small></h2>';
  if(d.aliases.length) h+='<p class=al>별칭: '+d.aliases.map(esc).join(', ')+'</p>';
  if(d.observations.length){ h+='<h3>관찰 · 주장</h3><ul>'+
    d.observations.map(o=>'<li>'+esc(o)+'</li>').join('')+'</ul>'; }
  if(d.documents.length){ h+='<h3>출처 문서 ('+d.documents.length+')</h3>';
    d.documents.forEach(dc=>{ h+='<div class=doc><b>'+esc(dc.title)+'</b>'+
      (dc.url?' <a href="'+esc(dc.url)+'" target=_blank>↗</a>':'')+
      (dc.summary?'<p>'+esc(dc.summary)+'</p>':'')+'</div>'; }); }
  if(d.neighbors.length){ h+='<h3>연결 ('+d.neighbors.length+')</h3><ul>';
    d.neighbors.forEach(n=>{ const ar=n.dir=='out'?'→':'←';
      h+='<li><span class=rel>'+esc(n.rel)+'</span> '+ar+
         ' <a href="#" onclick="loadNode(\\''+n.id+'\\');return false">'+esc(n.name)+
         '</a> <small>'+esc(n.type)+'</small></li>'; }); h+='</ul>'; }
  panel.innerHTML=h;
}
function hl(q){
  if(!allNodes) return;
  q=q.trim().toLowerCase();
  const matches=[];
  allNodes.forEach(n=>{
    const on = q && n.label.toLowerCase().includes(q);
    if(on) matches.push(n.id);
    allNodes.update({id:n.id, color: on?'#7ee787':undefined, font:{color:on?'#7ee787':'#d7dbe0'}});
  });
  if(matches.length){ net.selectNodes(matches); net.focus(matches[0],{scale:1.1,animation:true}); }
  else if(!q && net){ net.unselectAll(); net.fit({animation:true}); }
}
document.getElementById('q').addEventListener('keydown',e=>{
  if(e.key==='Enter'){ const m=net.getSelectedNodes(); if(m.length) loadNode(m[0]); }
});
function token(){
  let t = localStorage.getItem('claire_token') || '';
  if(!t){ t = prompt('inject token (CLAIRE_INJECT_TOKEN):') || '';
          if(t) localStorage.setItem('claire_token', t); }
  return t;
}
function synth(){
  if(!net){ return; }
  const ids = net.getSelectedNodes();
  if(!ids.length){ alert('노드를 먼저 선택하세요 (클릭 또는 검색 후 매치 선택).'); return; }
  panel.innerHTML='<p class=hint>🧩 '+ids.length+'개 노드 종합 중… (LLM 호출)</p>';
  fetch('synthesize',{method:'POST',
    headers:{'Content-Type':'application/json','X-Token':token()},
    body:JSON.stringify({node_ids:ids})})
   .then(r=> r.status===401 ? (localStorage.removeItem('claire_token'),{error:'인증 실패 — 토큰을 다시 입력하세요'}) : r.json())
   .then(d=>{
     if(d.error){ panel.innerHTML='<p class=hint>오류: '+esc(d.error)+'</p>'; return; }
     let h='<h2>🧩 종합 지식 <small>'+d.entities.length+'개 노드</small></h2>';
     h+='<div class=synth>'+esc(d.answer)+'</div>';
     h+='<p class=al>대상: '+d.entities.map(esc).join(', ')+'</p>';
     panel.innerHTML=h;
   }).catch(e=>{ panel.innerHTML='<p class=hint>요청 실패: '+esc(String(e))+'</p>'; });
}
</script></body></html>
"""
