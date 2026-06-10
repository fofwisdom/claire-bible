"""읽기전용 그래프 시각화 — vis.js 용 데이터 변환 + 정적 HTML 페이지.

로컬 inject API(aiohttp)가 /graph(JSON)·/node·/documents·/synthesize 로 노출한다.
정본 DB 를 읽고, 종합(synthesize)만 LLM 비용이 있어 세션/토큰 인증 뒤에 둔다.
"""

from __future__ import annotations

import sqlite3
from collections import Counter

from .store import db as dbm


def graph_json(conn: sqlite3.Connection) -> dict:
    """엔티티/관계를 vis.js network 형식(nodes/edges)으로. dangling edge 는 제외.

    각 노드에 degree(연결 수)를 실어 UI 가 degree-centrality 임계로 핵심 서브그래프만
    표시할 수 있게 한다(전체 N개 렌더 → 큰 그래프의 가시성/스케일 문제 해소)."""
    ents = dbm.all_entities(conn)
    rels = dbm.all_relations(conn)
    ent_ids = {e.id for e in ents}
    # 양 끝 노드가 모두 존재하는 관계만(고아 엣지는 vis.js 가 유령 노드를 만들어 깨짐).
    edges = [
        {"id": f"e{i}", "from": r.source_id, "to": r.target_id, "label": r.type,
         "arrows": "to", "dashes": r.provisional}
        for i, r in enumerate(
            r for r in rels if r.source_id in ent_ids and r.target_id in ent_ids)
    ]
    deg: Counter = Counter()
    for e in edges:
        deg[e["from"]] += 1
        deg[e["to"]] += 1
    nodes = [
        {
            "id": e.id,
            "label": e.name,
            "group": e.type,
            "degree": deg.get(e.id, 0),
            "sources": e.sources,  # 문서 기반 필터용(문서 클릭 → 그 문서 엔티티만)
            "title": (e.observations[0][:200] if e.observations else e.type),
        }
        for e in ents
    ]
    max_degree = max((n["degree"] for n in nodes), default=0)
    return {"nodes": nodes, "edges": edges,
            "stats": {"entities": len(nodes), "relations": len(edges),
                      "max_degree": max_degree}}


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


def documents_list(conn: sqlite3.Connection, limit: int = 300) -> list[dict]:
    """좌측 문서 패널용 — 최신순 문서(제목·요약·출처타입·시각)."""
    out = []
    for r in dbm.documents_timeline(conn, limit):
        out.append({
            "id": r["id"],
            "title": r["title"] or "(제목 없음)",
            "url": r["url"],
            "source_type": r["source_type"],
            "fetched_at": r["fetched_at"],
            "summary": dbm.latest_extraction_summary(conn, r["id"]) or "",
        })
    return out


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


# vis.js 9(unpkg CDN) 기반 단일 페이지. /graph·/node·/documents·/auth·/synthesize 사용.
GRAPH_HTML = """<!doctype html>
<html lang="ko"><head><meta charset="utf-8"/>
<title>claire_bible — 지식 그래프</title>
<script src="https://unpkg.com/vis-network/standalone/umd/vis-network.min.js"></script>
<style>
  html,body{margin:0;height:100%;font-family:system-ui,sans-serif;background:#0e1116;color:#d7dbe0}
  #bar{display:flex;align-items:center;gap:6px;padding:6px 12px;background:#161b22;border-bottom:1px solid #2a2f37;font-size:13px;white-space:nowrap}
  #bar .brand{font-weight:600}
  #bar b{color:#7ee787}
  .spacer{flex:1}
  #stat{color:#8b949e;text-align:right}
  #authstate{cursor:pointer;padding:2px 7px;border:1px solid #2a2f37;border-radius:4px}
  #synthchips{display:flex;gap:4px;overflow:hidden;max-width:280px}
  #synthchips .chip{background:#1f2937;border-radius:10px;padding:1px 7px;font-size:11px;cursor:pointer}
  #legendbar{display:flex;flex-wrap:wrap;gap:10px;padding:4px 12px;background:#10151c;border-bottom:1px solid #2a2f37;font-size:11px;color:#8b949e}
  #legendbar i{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:3px;vertical-align:middle}
  #wrap{display:flex;height:calc(100% - 68px)}
  #net{flex:1;min-width:0}
  #docs{width:280px;overflow:auto;background:#10151c;border-right:1px solid #2a2f37;font-size:12px}
  #docs .dhead{padding:8px 10px;border-bottom:1px solid #2a2f37;position:sticky;top:0;background:#10151c;z-index:2}
  .dday{position:sticky;top:37px;background:#161b22;color:#7ee787;font-size:11px;padding:3px 10px;border-bottom:1px solid #2a2f37;z-index:1}
  .docitem{padding:7px 10px;border-bottom:1px solid #1c2330;cursor:pointer}
  .docitem:hover{background:#161b22}
  .docitem.active{background:#1f2937;border-left:3px solid #7ee787}
  .docitem b{font-size:12px} .docitem .st{color:#6e7681;font-size:10px;margin-left:6px}
  .docitem p{margin:.2em 0 0;color:#8b949e;font-size:11px}
  #panel{width:360px;overflow:auto;padding:14px 16px;background:#10151c;border-left:1px solid #2a2f37;font-size:13px;line-height:1.5}
  #panel h2{margin:.2em 0;font-size:18px} #panel h2 small{color:#8b949e;font-size:12px;font-weight:normal}
  #panel h3{margin:1em 0 .3em;font-size:13px;color:#7ee787;border-bottom:1px solid #2a2f37;padding-bottom:2px}
  #panel ul{margin:.2em 0;padding-left:18px} #panel li{margin:.25em 0}
  #panel .doc{margin:.5em 0;padding:6px 8px;background:#161b22;border-radius:5px}
  #panel .doc p{margin:.3em 0 0;color:#adbac7} #panel a{color:#58a6ff;text-decoration:none}
  #panel .rel{color:#d29922;font-size:11px} #panel .al{color:#8b949e}
  #panel .hint{color:#6e7681;margin-top:1em}
  #panel .synth{white-space:pre-wrap;background:#161b22;border:1px solid #2a2f37;border-radius:5px;padding:10px;margin:.4em 0;line-height:1.6}
  input{background:#0e1116;color:#d7dbe0;border:1px solid #2a2f37;border-radius:4px;padding:3px 8px;font-size:13px}
  #q{width:150px}
  button{background:#238636;color:#fff;border:0;border-radius:4px;padding:4px 10px;cursor:pointer;font-size:13px}
  button:hover{background:#2ea043}
  button.sec{background:#30363d} button.sec:hover{background:#3c444d}
  #fslider{width:100px;vertical-align:middle}
</style></head>
<body>
<div id="bar">
  <span class="brand">claire_bible</span>
  <input id="q" placeholder="검색(엔터)" oninput="onSearchInput(this.value)"/>
  <label style="font-size:12px"><input type="checkbox" id="sem" style="width:auto"/> 의미</label>
  <button id="searchbtn" class="sec" onclick="doSemantic()" style="display:none">🔎 의미검색</button>
  <span id="synthchips"></span>
  <button id="synthbtn" onclick="synth()">🧩 종합 (0)</button>
  <label>연결 ≥ <b id="fmin">0</b> <input id="fslider" type="range" min="0" max="0" value="0" oninput="setDeg(this.value)"/></label>
  <span class="spacer"></span>
  <span id="authstate" onclick="authClick()">🔓 미인증</span>
  <span id="stat">로딩…</span>
</div>
<div id="legendbar"></div>
<div id="wrap">
  <div id="docs"><div class="dhead"><input id="docq" placeholder="문서 검색(제목·요약)" oninput="renderDocs(this.value)" style="width:92%"/></div>
    <div id="doclist"><p class="hint" style="padding:10px">문서 로딩…</p></div></div>
  <div id="net"></div>
  <div id="panel"><p class="hint">노드를 클릭하면 관찰·출처 문서·연결이 표시됩니다.<br><br>• <b>Ctrl+클릭</b> 또는 상세의 <b>➕ 종합에 추가</b>로 여러 노드를 모아 종합<br>• 다른 노드에 <b>1초</b> 올리면 미리보기(벗어나면 복귀)<br>• 좌측 문서를 누르면 그 문서의 노드만 진하게</p></div>
</div>
<script>
const TYPE_COLORS = {Tool:'#58a6ff',Framework:'#bc8cff',Model:'#f778ba',Paper:'#d29922',
  Article:'#7ee787',Repo:'#39c5cf',Concept:'#ff7b72',Person:'#ffa657',Org:'#e3b341',
  Event:'#a5d6ff',Note:'#8b949e'};
const DIM = 0.16;
let net, allNodes, allEdges, allDocs=[];
let curMinDeg=0, activeDoc=null, highlightSet=null, selectedNodeId=null, hoverTimer=null;
let synthSet=new Set(), authTimer=null;
const panel = document.getElementById('panel');
function esc(s){return (s||'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}

fetch('graph').then(r=>r.json()).then(d=>{
  allNodes = new vis.DataSet(d.nodes);
  allEdges = new vis.DataSet(d.edges);
  const sl = document.getElementById('fslider'); sl.max = d.stats.max_degree; sl.value = 0;
  const types=[...new Set(d.nodes.map(n=>n.group))].sort();
  document.getElementById('legendbar').innerHTML = types.map(t=>
    '<span><i style="background:'+(TYPE_COLORS[t]||'#8b949e')+'"></i>'+esc(t)+'</span>').join('');
  const groups={}; types.forEach(t=>{ const c=TYPE_COLORS[t]||'#8b949e';
    groups[t]={color:{background:c,border:'#2a2f37',highlight:{background:c,border:'#7ee787'}}}; });
  const opts = {
    nodes:{shape:'dot',size:14,font:{color:'#d7dbe0',size:13}},
    edges:{color:{color:'#3a4250',highlight:'#7ee787'},font:{color:'#8b949e',size:10},smooth:false},
    groups, physics:{stabilization:{iterations:200},barnesHut:{gravitationalConstant:-8000,springLength:120}},
    interaction:{hover:true,tooltipDelay:120,multiselect:true}
  };
  net = new vis.Network(document.getElementById('net'), {nodes:allNodes, edges:allEdges}, opts);
  net.on('click', p => {
    if(!p.nodes.length){
      // 빈 캔버스 클릭: inspect 만 해제하고 검색(라벨/의미) 강조 선택은 유지(이슈4).
      // vis 가 내부적으로 선택을 비우므로 그 뒤에 검색 선택을 다시 적용한다.
      selectedNodeId=null;
      if(highlightSet && highlightSet.size) setTimeout(restoreSelection, 0);
      return;
    }
    const id=p.nodes[0], ev=p.event.srcEvent;
    if(ev && (ev.ctrlKey||ev.metaKey)){ toggleSynth(id); }   // Ctrl/Cmd+클릭 = 종합 수집(선택과 분리)
    else { selectedNodeId=id; loadNode(id); }                // 일반 클릭 = 상세 inspect
  });
  net.on('hoverNode', p => { clearTimeout(hoverTimer);
    hoverTimer=setTimeout(()=>{ if(p.node!==selectedNodeId) loadNode(p.node, true); }, 1000); });
  net.on('blurNode', () => { clearTimeout(hoverTimer);
    // hover 미리보기를 닫고 inspect/검색 선택을 원복(이슈4 + GOALS ①⑤: hover↔selection 분리).
    if(selectedNodeId) loadNode(selectedNodeId, false);
    restoreSelection(); });
  applyView();
});

// 검색 강조(highlightSet)와 inspect(selectedNodeId)를 vis 시각 선택으로 복원.
// hover/blur·빈클릭으로 vis 가 선택을 비워도 검색 결과 선택이 사라지지 않게 한다(이슈4).
function restoreSelection(){
  if(!net) return;
  let ids = highlightSet ? [...highlightSet] : [];
  if(selectedNodeId && !ids.includes(selectedNodeId)) ids = ids.concat(selectedNodeId);
  if(ids.length) net.selectNodes(ids); else net.unselectAll();
}
// 검색·inspect 모두 해제(ESC / 검색창 비우기). synthSet(종합 수집)은 보존.
function clearSelections(){
  highlightSet=null; selectedNodeId=null;
  const q=document.getElementById('q'); if(q) q.value='';
  if(net) net.unselectAll();
  applyView();
}
document.addEventListener('keydown', e=>{ if(e.key==='Escape') clearSelections(); });

function loadNode(id, isHover){
  if(net && !isHover) net.selectNodes([id]);  // hover 미리보기는 선택을 바꾸지 않음
  fetch('node?id='+encodeURIComponent(id)).then(r=>r.json()).then(renderPanel);
}
function renderPanel(d){
  if(!d || d.error){ panel.innerHTML='<p class=hint>노드를 찾을 수 없습니다.</p>'; return; }
  const inSet = synthSet.has(d.id);
  let h='<h2>'+esc(d.name)+' <small>'+esc(d.type)+(d.provisional?' ⚠️provisional':'')+'</small></h2>';
  h+='<button class="sec" onclick="addToSynth(\\''+d.id+'\\')">'+(inSet?'✓ 종합 목록에 있음':'➕ 종합에 추가')+'</button>';
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

// 단일 가시 규칙: degree(스케일)=hidden, 강조 필터(문서 선택 + 검색)=비매치 dim.
// 문서/라벨검색/의미검색이 모두 같은 강조 방식을 공유한다(시각 언어 통일).
function setDeg(v){ curMinDeg=+v; document.getElementById('fmin').textContent=v; applyView(); }
function applyView(){
  if(!allNodes) return;
  let shown=0, emph=0;
  const hasFilter = activeDoc || highlightSet;
  allNodes.forEach(n=>{
    if(n.degree < curMinDeg){ allNodes.update({id:n.id, hidden:true}); return; }
    let match = true;
    if(activeDoc) match = match && (n.sources||[]).includes(activeDoc);
    if(highlightSet) match = match && highlightSet.has(n.id);  // 검색(라벨/의미) 강조 집합
    allNodes.update({id:n.id, hidden:false, opacity: match?1:DIM});
    shown++; if(match) emph++;
  });
  allEdges.forEach(e=>{ const f=allNodes.get(e.from), t=allNodes.get(e.to);
    allEdges.update({id:e.id, hidden: !(f && t && !f.hidden && !t.hidden)}); });
  document.getElementById('stat').innerHTML =
    '표시 <b>'+shown+'</b>/'+allNodes.length
    + (curMinDeg>0?' · 연결≥'+curMinDeg:'') + (hasFilter?' · 강조 '+emph+'개':'');
}

// --- 좌측 문서 패널(일자별 그룹) ---
function dayOf(ts){ if(!ts) return '(날짜 미상)';
  const d=new Date(ts*1000);
  return d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0')+'-'+String(d.getDate()).padStart(2,'0'); }
function renderDocs(filter){
  const q=(filter||'').trim().toLowerCase();
  const items = allDocs.filter(dc=> !q || (dc.title+' '+dc.summary).toLowerCase().includes(q));
  if(!items.length){ document.getElementById('doclist').innerHTML='<p class=hint style="padding:10px">문서 없음</p>'; return; }
  let html='', curDay=null;
  items.forEach(dc=>{ const day=dayOf(dc.fetched_at);
    if(day!==curDay){ html+='<div class=dday>'+day+'</div>'; curDay=day; }
    html+='<div class="docitem'+(dc.id===activeDoc?' active':'')+'" onclick="selectDoc(\\''+dc.id+'\\')">'+
      '<b>'+esc(dc.title)+'</b><span class=st>'+esc(dc.source_type||'')+'</span>'+
      (dc.summary?'<p>'+esc(dc.summary.slice(0,110))+'</p>':'')+'</div>'; });
  document.getElementById('doclist').innerHTML=html;
}
function selectDoc(id){
  activeDoc = (activeDoc===id ? null : id);     // 같은 문서 재클릭 → 해제
  renderDocs(document.getElementById('docq').value);
  applyView();
  if(activeDoc && net) net.fit({animation:true});
}

// --- 종합 수집(synthSet) — inspect(클릭)와 분리 ---
function toggleSynth(id){ if(synthSet.has(id)) synthSet.delete(id); else synthSet.add(id); renderChips(); }
function addToSynth(id){ synthSet.add(id); renderChips(); if(id===selectedNodeId) loadNode(id); }
function renderChips(){
  const box=document.getElementById('synthchips');
  box.innerHTML=[...synthSet].map(id=>{ const n=allNodes&&allNodes.get(id);
    return '<span class=chip onclick="toggleSynth(\\''+id+'\\')" title="제거">'+esc(n?n.label:id)+' ✕</span>'; }).join('');
  document.getElementById('synthbtn').textContent='🧩 종합 ('+synthSet.size+')';
}

// --- 검색: 즉시 라벨 매칭(기본) vs 의미검색 버튼(체크 시) ---
function onSearchInput(v){ if(!document.getElementById('sem').checked) hl(v); }
// 라벨 검색: 매치 강조 + 나머지 dim(문서 선택과 동일 방식). 색칠 대신 highlightSet+applyView.
function hl(q){
  if(!allNodes) return;
  q=q.trim().toLowerCase();
  if(!q){ highlightSet=null; applyView(); if(net){ net.unselectAll(); net.fit({animation:true}); } return; }
  const matches=[];
  allNodes.forEach(n=>{ if(n.label.toLowerCase().includes(q)) matches.push(n.id); });
  highlightSet = new Set(matches);
  applyView();
  if(matches.length && net){ net.selectNodes(matches); net.focus(matches[0],{scale:1.1,animation:true}); }
}
document.getElementById('sem').addEventListener('change',e=>{
  document.getElementById('searchbtn').style.display = e.target.checked?'':'none';
  if(e.target.checked) hl('');   // 즉시 라벨강조 해제(의미검색은 버튼으로만)
});
document.getElementById('q').addEventListener('keydown',e=>{
  if(e.key!=='Enter') return;
  if(document.getElementById('sem').checked){ doSemantic(); }
  else { const m=net.getSelectedNodes(); if(m.length) loadNode(m[0]); }
});
// 입력창 포커스 시 기존 검색어 전체 선택 → 바로 새로 타이핑 가능(GOALS ④).
document.getElementById('q').addEventListener('focus', e=> e.target.select());
function doSemantic(){ semanticSearch(document.getElementById('q').value); }

// --- 인증(텔레그램 버튼 승인 → 세션) + 상태 표시 ---
function setAuth(state, detail){
  const el=document.getElementById('authstate');
  if(state==='authed') el.textContent='🔒 인증됨';
  else if(state==='pending') el.textContent='📨 승인 대기 '+detail+'s';
  else el.textContent='🔓 미인증';
}
function authClick(){ if(!localStorage.getItem('claire_session')) ensureSession(); }
function on401(){ localStorage.removeItem('claire_session'); setAuth('idle'); }
async function ensureSession(){
  let sess=localStorage.getItem('claire_session');
  if(sess){ setAuth('authed'); return sess; }
  let d;
  try{ d=await (await fetch('auth/request',{method:'POST'})).json(); }
  catch(e){ setAuth('idle'); return null; }
  if(d.error){ setAuth('idle'); document.getElementById('authstate').textContent='🔓 '+d.error; return null; }
  let left=Math.floor(d.ttl||600);
  setAuth('pending', left);
  if(authTimer) clearInterval(authTimer);
  authTimer=setInterval(()=>{ left-=1; if(left>0) setAuth('pending', left); }, 1000);
  const deadline=Date.now()+(d.ttl||600)*1000;
  while(Date.now()<deadline){
    await new Promise(r=>setTimeout(r,2000));
    let p; try{ p=await (await fetch('auth/poll?nonce='+encodeURIComponent(d.nonce))).json(); }catch(e){ continue; }
    if(p.session){ clearInterval(authTimer); localStorage.setItem('claire_session',p.session);
      setAuth('authed'); return p.session; }
  }
  clearInterval(authTimer); setAuth('idle');
  document.getElementById('authstate').textContent='🔓 승인 시간초과';
  return null;
}
async function synth(){
  const ids=[...synthSet];
  if(!ids.length){ alert('종합할 노드를 먼저 모으세요 — Ctrl+클릭 또는 상세의 "➕ 종합에 추가".'); return; }
  panel.innerHTML='<p class=hint>🧩 '+ids.length+'개 노드 종합 중… (LLM 호출)</p>';
  // 인증은 claire_session 쿠키(/web 진입)로 자동 전송됨 — 별도 헤더 불필요.
  fetch('synthesize',{method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({node_ids:ids})})
   .then(r=> (r.status===401||r.status===404) ? {error:'세션 만료 — 텔레그램 /web 으로 다시 접속하세요'} : r.json())
   .then(d=>{
     if(d.error){ panel.innerHTML='<p class=hint>오류: '+esc(d.error)+'</p>'; return; }
     let h='<h2>🧩 종합 지식 <small>'+d.entities.length+'개 노드</small></h2>';
     h+='<div class=synth>'+esc(d.answer)+'</div>';
     h+='<p class=al>대상: '+d.entities.map(esc).join(', ')+'</p>';
     panel.innerHTML=h;
   }).catch(e=>{ panel.innerHTML='<p class=hint>요청 실패: '+esc(String(e))+'</p>'; });
}
async function semanticSearch(q){
  q=(q||'').trim(); if(!q) return;
  document.getElementById('stat').innerHTML='🔎 의미검색 중…';
  let r;
  try{ r=await fetch('search',{method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({query:q, summarize:false, limit:12})}); }
  catch(e){ document.getElementById('stat').textContent='검색 실패'; return; }
  if(r.status===401||r.status===404){ document.getElementById('stat').textContent='세션 만료 — /web 으로 재접속'; return; }
  const d=await r.json();
  const ids=(d.hits||[]).map(h=>h.id).filter(Boolean);
  highlightSet = new Set(ids);   // 라벨 검색과 동일하게 강조+dim 방식 사용
  applyView();
  if(ids.length && net){ net.selectNodes(ids); net.focus(ids[0],{scale:1.1,animation:true}); }
  if(!ids.length){ document.getElementById('stat').textContent='🔎 의미검색: 결과 없음'; }
}

// 이 페이지가 로드됐다는 것 자체가 인증됨을 의미(미인증이면 게이트가 404). 쿠키 기반.
setAuth('authed');
fetch('documents').then(r=>r.json()).then(d=>{ allDocs=d.documents||[]; renderDocs(); });

// 읽기전용 디버그 핸들(테스트/Playwright 검증용 — closure 상태 관찰). 부작용 없음.
window.claireDebug = {
  get sel(){ return net ? net.getSelectedNodes() : []; },
  get highlight(){ return highlightSet ? [...highlightSet] : null; },
  get selected(){ return selectedNodeId; },
  get synth(){ return [...synthSet]; },
};
</script></body></html>
"""
