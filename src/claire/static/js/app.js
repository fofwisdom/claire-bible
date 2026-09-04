/**
 * Claire Bible - Knowledge Graph Workspace Application
 * Controls vis.js network, search, clustering, degree filtering, tabs, and workspace state.
 */

// --- Client Loading Watchdog & Global Error Handlers ---
window.__CLAIRE_CLEAR_LOADING = function(reason) {
    try {
      var auth = document.getElementById('authstate');
      if (auth && (auth.textContent.indexOf('확인') !== -1 || auth.textContent.indexOf('로딩') !== -1)) {
        auth.textContent = '👁️ 익명 읽기전용' + (reason ? ' (' + reason + ')' : '');
      }
      var dl = document.getElementById('doclist');
      if (dl && (dl.innerHTML.indexOf('문서 로딩…') !== -1 || dl.textContent.indexOf('문서 로딩') !== -1)) {
        dl.innerHTML = (typeof doclistToolbarHtml==='function'?doclistToolbarHtml():'')+
          '<p class="hint" style="padding:10px">문서 목록 조회 지연 (새로고침 권장)</p>';
      }
      var st = document.getElementById('stat');
      if (st && (st.textContent.indexOf('로딩…') !== -1 || st.textContent.indexOf('확인') !== -1)) {
        st.textContent = reason ? '상태: ' + reason : '준비 완료';
      }
      var sk = document.getElementById('searchkind');
      if (sk && sk.textContent.indexOf('확인') !== -1) {
        sk.textContent = 'Full-Text Search';
      }
    } catch (_) {}
  };
  window.addEventListener('error', function(e) {
    console.warn('Claire global error:', e.error || e.message);
    window.__CLAIRE_CLEAR_LOADING('오류');
  });
  window.addEventListener('unhandledrejection', function(e) {
    console.warn('Claire unhandled rejection:', e.reason);
    window.__CLAIRE_CLEAR_LOADING('지연');
  });
  setTimeout(function() {
    window.__CLAIRE_CLEAR_LOADING('');
  }, 2500);

// --- Workspace State & Core Theme Configuration ---
const TYPE_COLORS = {Tool:'#1f6feb',Framework:'#8957e5',Model:'#bf3989',Paper:'#bf8700',
  Article:'#1a7f37',Repo:'#1b7c83',Concept:'#cf222e',Person:'#bc4c00',Org:'#9a6700',
  Event:'#0969da',Note:'#6e7781'};
const DIM = 0.16;
// vis 캔버스는 CSS 변수가 안 닿아 테마별 색을 JS 로 직접 갱신한다(노드 글자/엣지/테두리).
const THEMES = {
  light:{nodeFont:'#1f2328', edge:'#c4ccd6', edgeHi:'#1a7f37', nodeBorder:'#d0d7de', lit:'#0969da'},
  dark: {nodeFont:'#d7dbe0', edge:'#3a4250', edgeHi:'#7ee787', nodeBorder:'#2a2f37', lit:'#ffffff'},
};
function curTheme(){ return document.documentElement.getAttribute('data-theme')==='dark'?'dark':'light'; }
function T(){ return THEMES[curTheme()] || THEMES.light; }

// --- 반응형 환경 감지 & 작업영역 상태 (최상단 안전 선언) ---
const mobileMQ = window.matchMedia('(max-width:720px)');
const compactMQ = window.matchMedia('(max-width:1100px)');
const toolbarMQ = window.matchMedia('(max-width:1500px)');
const reducedMotionMQ = window.matchMedia('(prefers-reduced-motion:reduce)');
const paneNames=['docs','graph'];
let activePane='graph', detailOpen=false, centerView='graph', drawerOpen=false;
let detailReturnFocus=null, docSearchActive=false;
let graphCamera = null, preservingGraphCamera = false, netBusy = false;
let isDraggingNode = false, settleTimer = null;
let lastNetSize = {w:0, h:0};
let allTypes = [], allRelTypes = [], allDocs = [];
let net = null, allNodes = null, allEdges = null;
let curMinDeg = 0, activeDoc = null, highlightSet = null, selectedNodeId = null, hoverTimer = null;
let lastSelectedDocId = null;
try{
  const savedLastDoc = localStorage.getItem('claireLastDoc');
  if(savedLastDoc) lastSelectedDocId = savedLastDoc;
}catch(_){}

function recordSelectedDoc(id){
  if(!id) return;
  lastSelectedDocId = id;
  try{ localStorage.setItem('claireLastDoc', id); }catch(_){}
}

// --- 웹 표준 History API 기반 모바일 뒤로가기/내비게이션 관리 ---
let isPoppingHistory = false;
let lastPushedHistory = null;

function getActiveModalName(){
  const r = document.getElementById('reader');
  const gdm = document.getElementById('graphdocmenu');
  if(mobileMQ.matches && r && r.classList.contains('open')){
    return 'reader';
  }
  if((compactMQ.matches || mobileMQ.matches) && (drawerOpen || detailOpen)){
    return 'drawer';
  }
  if(gdm && !gdm.hidden){
    return 'graphdocmenu';
  }
  return null;
}

function getAppHistorySnapshot(){
  return {
    pane: activePane || 'docs',
    modal: getActiveModalName(),
    docId: curReaderDoc || activeDoc || null,
    nodeId: selectedNodeId || null,
  };
}

function pushAppHistory(patch = {}){
  if(isPoppingHistory) return;
  if(typeof window === 'undefined' || !window.history || typeof window.history.pushState !== 'function') return;
  const base = getAppHistorySnapshot();
  const next = Object.assign({}, base, patch);
  if(lastPushedHistory &&
     lastPushedHistory.pane === next.pane &&
     lastPushedHistory.modal === next.modal &&
     lastPushedHistory.docId === next.docId &&
     lastPushedHistory.nodeId === next.nodeId){
    return;
  }
  lastPushedHistory = next;
  try{ window.history.pushState(next, ''); }catch(_){}
  if(typeof window.gtag === 'function'){
    try{
      let virtPath = '/';
      if(next.modal === 'reader' && next.docId){
        virtPath = '/doc/' + next.docId;
      } else if(next.nodeId){
        virtPath = '/node/' + next.nodeId;
      } else if(next.pane && next.pane !== 'graph'){
        virtPath = '/' + next.pane;
      }
      window.gtag('event', 'page_view', {
        page_title: document.title,
        page_location: window.location.origin + virtPath
      });
    }catch(_){}
  }
}

function replaceAppHistory(patch = {}){
  if(isPoppingHistory) return;
  if(typeof window === 'undefined' || !window.history || typeof window.history.replaceState !== 'function') return;
  const base = getAppHistorySnapshot();
  const next = Object.assign({}, base, patch);
  lastPushedHistory = next;
  try{ window.history.replaceState(next, ''); }catch(_){}
}

function docWithMostNodes(){
  if(!allDocs || !allDocs.length) return null;
  const counts = new Map();
  if(allNodes){
    allNodes.forEach(n=>{
      if(n.hidden || (typeof n.id==='string' && n.id.indexOf('cl_')===0)) return;
      (n.sources||[]).forEach(docId=>{
        counts.set(docId, (counts.get(docId)||0) + 1);
      });
    });
  }
  let bestDoc = null, maxCount = -1;
  const visibleDocs = allDocs.filter(d => d.hidden !== 1);
  const targetDocs = visibleDocs.length ? visibleDocs : allDocs;
  for(const d of targetDocs){
    const count = counts.get(d.id) || 0;
    if(count > maxCount){
      maxCount = count;
      bestDoc = d;
    }
  }
  return bestDoc ? bestDoc.id : (targetDocs[0]?.id || null);
}

function getRecentDocId(){
  let target = activeDoc || curReaderDoc || lastSelectedDocId;
  if(!target){
    try{
      const saved = localStorage.getItem('claireLastDoc');
      if(saved) target = saved;
    }catch(_){}
  }
  if(target && allDocs && allDocs.length){
    const exists = allDocs.find(d => d.id === target && d.hidden !== 1);
    if(exists) return exists.id;
  }
  return null;
}
let clusterEdges = null, clusterAnchor = null, searchDebounce = null;
let currentSearchSeq = 0, currentSearchAbort = null;
function cancelServerSearch(){
  if(currentSearchAbort){
    try{ currentSearchAbort.abort(); }catch(_){}
    currentSearchAbort = null;
  }
}
let synthSet = new Set();
let AUTH_SCOPE='unknown'; let READONLY=true;
let relFilter = null;
let pathMode = false, pathPicks = [], pathNodes = null, pathEdges = null;
let edgeLabelsByZoom = false, selectedEdgeIds = new Set();
let graphStabilized = false;
let detailCompact = false;
try{
  const savedDetailCompact = localStorage.getItem('claireDetailCompact');
  if(savedDetailCompact === 'true') detailCompact = true;
}catch(_){}

function toggleDetailCompact(force){
  if(typeof force === 'boolean'){
    detailCompact = force;
  }else{
    detailCompact = !document.body.classList.contains('detail-compact');
  }
  try{ localStorage.setItem('claireDetailCompact', detailCompact ? 'true' : 'false'); }catch(_){}
  syncWorkspaceLayout();
}

const paneEls = {
  get docs(){ return document.getElementById('docs'); },
  get graph(){ return document.getElementById('netwrap'); }
};
const paneTabs = {
  get docs(){ return document.getElementById('tab-docs'); },
  get graph(){ return document.getElementById('tab-graph') || document.getElementById('tab-docs'); }
};

const panel = document.getElementById('panel');
function canWrite(){ return AUTH_SCOPE==='owner'; }
// 함수로 둔 이유: READONLY 는 /whoami 가 비동기로 확정하므로, 호출 시점 기준으로
// 종합 안내 줄을 넣을지 뺄지 판단해야 한다(고정 문자열이면 초기 로드 시점 값에 박제됨).
function defaultHint(){
  const synthLine = canWrite()
    ? '• <b>Ctrl+클릭</b> 또는 상세의 <b>➕ 종합에 추가</b>로 여러 노드를 모아 종합<br>'
    : '';
  return '<p class="hint">노드를 클릭하면 관찰·출처 문서·연결이 표시됩니다.<br><br>'+synthLine+
    '• 다른 노드에 <b>1.5초</b> 올리면 마우스 옆에 <b>요약 팝업</b>(더 끌면 출처 문서까지)<br>'+
    '• <b>자료</b>에서 문서를 고르면 그래프가 강조되고, <b>📖</b>로 크게 읽습니다.<br>'+
    '• 상단 <b>⋯ 도구</b>의 <b>🌙/🌞</b>로 라이트·다크를 전환합니다.</p>';
}
if(panel) panel.innerHTML = defaultHint();
function esc(s){return (s||'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}

// --- 노드 hover 및 모바일 탭 요약 팝업(마우스/탭 위치) — fetch 없이 클라 데이터(allNodes)만 쓴다 ---
// vis hoverNode 이벤트는 진입 위치를 안 주므로 #net 위 mousemove 로 커서 좌표를 추적해 둔다.
let mouseXY={x:0,y:0};
function canShowNodePop(id){
  const isMobile = (compactMQ && compactMQ.matches) || (mobileMQ && mobileMQ.matches);
  if(document.body.classList.contains('detail-open') || document.body.classList.contains('reader-open') || document.body.classList.contains('drawer-open')) return false;
  if(isMobile && (drawerOpen || detailOpen)) return false;
  return true;
}
window.addEventListener('pointerdown', e=>{
  const np = document.getElementById('nodepop');
  if(np && np.style.display!=='none' && !np.contains(e.target) && !e.target.closest('#net')){
    hideNodePop();
  }
}, {passive:true});
const netEl = document.getElementById('net');
if(netEl) netEl.addEventListener('mousemove', e=>{ mouseXY.x=e.clientX; mouseXY.y=e.clientY; });
const nodepop = document.getElementById('nodepop');
let popReqId=null;        // 현재 팝업이 다루는 노드 id — 늦게 온 fetch 응답(stale) 무시용
let popExpandTimer=null;  // '좀 더 기다리면' 출처 문서를 펼치는 타이머
// id 의 요약 팝업을 (x,y) 위치에 띄운다. 좌표 생략 시 #net 의 커서 추적값(mouseXY) 사용
// → 그래프 hover 는 좌표 없이, 우측 '이 문서의 노드' hover 는 버튼 진입 좌표를 넘긴다.
// 1단계: 클라 데이터(이름·타입·연결수+관찰 첫 줄)로 즉시. 2단계: node fetch 로 관찰 3개.
// 3단계: 더 끌면 출처 문서 1건(제목+글)을 덧붙인다(점진적 공개, 사용자 요구).
function showNodePop(id, x, y){
  if(!canShowNodePop(id)){ hideNodePop(); return; }
  const n=allNodes&&allNodes.get(id); if(!n){ hideNodePop(); return; }
  popReqId=id; clearTimeout(popExpandTimer);
  const px = x==null?mouseXY.x:x, py = y==null?mouseXY.y:y;
  nodepop.dataset.x=px; nodepop.dataset.y=py;   // fetch 보강 후 재배치에 쓰려고 보관
  const c=TYPE_COLORS[n.group]||'#8b949e';
  const isMobile = (compactMQ && compactMQ.matches) || (mobileMQ && mobileMQ.matches);
  const head='<div class=phead><div><b>'+esc(n.label)+'</b> <span class=pt>'+esc(n.group||'')+'</span></div>'+
    '<button type="button" class=pclose onclick="event.stopPropagation();hideNodePop()" aria-label="닫기">✕</button></div>'+
    '<div class=pt><i style="background:'+c+'"></i>연결 '+(n.degree||0)+'개</div>';
  const foot=isMobile?'<div class=pact><button type="button" onclick="event.stopPropagation();hideNodePop();openDetailPane()">자세히 보기 ›</button></div>':'';
  nodepop.innerHTML=head+(n.obs?'<div class=po>'+esc(n.obs)+'</div>':'')+foot;
  nodepop.style.display='block';
  positionPop(px, py);                  // 표시 후(폭/높이 확정) 화면 밖으로 안 나가게 배치
  fetch('node?id='+encodeURIComponent(id)).then(r=>r.json()).then(d=>{
    if(!canShowNodePop(id) || popReqId!==id || nodepop.style.display==='none' || !d || d.error) return;  // 이미 떠났거나 상세 열렸으면 무시
    const obs=(d.observations||[]).slice(0,3);   // 관찰 최대 3개(설명이 너무 적던 문제)
    const base=head + obs.map(o=>'<div class=po>'+esc((o||'').slice(0,200))+'</div>').join('');
    nodepop.innerHTML=base+foot; positionPop(+nodepop.dataset.x, +nodepop.dataset.y);
    const docs=d.documents||[];
    if(docs.length){                    // 좀 더 머물면 출처 문서 1건을 덧붙임
      popExpandTimer=setTimeout(()=>{
        if(!canShowNodePop(id) || popReqId!==id || nodepop.style.display==='none') return;
        nodepop.innerHTML=base+popSource(docs[0])+foot;
        positionPop(+nodepop.dataset.x, +nodepop.dataset.y);
      }, 1400);
    }
  }).catch(()=>{});
}
// 팝업 하단의 출처 문서 한 건 — 제목 + 글(요약 우선, 없으면 상세 앞부분) 일부.
function popSource(d){
  const body=((d.summary||d.detail||'').replace(/\s+/g,' ').trim());
  return '<div class=psrc><div class=ptt>📄 '+esc(d.title||'(제목 없음)')+'</div>'+
    (body?'<div class=psb>'+esc(body.slice(0,240))+(body.length>240?'…':'')+'</div>':'')+'</div>';
}
function positionPop(x, y){
  const pad=14, pw=nodepop.offsetWidth || 280, ph=nodepop.offsetHeight || 120;
  let nx=x+pad, ny=y+pad;
  const isMobile = (compactMQ && compactMQ.matches) || (mobileMQ && mobileMQ.matches);
  if(isMobile){
    if(nx + pw > window.innerWidth - 8){
      nx = Math.max(12, window.innerWidth - pw - 12);
    }
    if(ny + ph > window.innerHeight - 56){
      ny = Math.max(50, y - pad - ph);
    }
  } else {
    if(nx+pw > window.innerWidth-4) nx=x-pad-pw;     // 오른쪽 넘치면 커서 왼쪽으로
    if(ny+ph > window.innerHeight-4) ny=y-pad-ph;    // 아래 넘치면 커서 위로
  }
  nodepop.style.left=Math.max(4,nx)+'px'; nodepop.style.top=Math.max(4,ny)+'px';
}
function hideNodePop(){
  clearTimeout(hoverTimer); hoverTimer=null;
  popReqId=null; clearTimeout(popExpandTimer); popExpandTimer=null;
  if(nodepop) nodepop.style.display='none';
}

// 타입별 노드 그룹 색(테마별 테두리). 테마 전환 시 다시 만들어 setOptions 로 적용.
function buildGroups(){ const g={}, th=T();
  allTypes.forEach(t=>{ const c=TYPE_COLORS[t]||'#8b949e';
    g[t]={color:{background:c,border:th.nodeBorder,highlight:{background:c,border:th.lit},hover:{background:c,border:th.lit}}}; });
  return g; }
function syncThemeBtn(){ const b=document.getElementById('themebtn');
  if(b) b.textContent = curTheme()==='dark'?'🌞':'🌙'; }
// 라이트/다크 전환 — localStorage 영속 + vis 캔버스 색(글자/엣지/테두리) 즉시 갱신.
function toggleTheme(){ const next = curTheme()==='dark'?'light':'dark';
  document.documentElement.setAttribute('data-theme', next);
  try{ localStorage.setItem('claireTheme', next); }catch(e){}
  syncThemeBtn();
  if(net){ const th=T();
    net.setOptions({nodes:{font:{color:th.nodeFont}},
      edges:{color:{color:th.edge,highlight:th.edgeHi},font:{color:th.nodeFont}},
      groups:buildGroups()});
    applyView(); }   // 노드별 테두리(lit/normal)도 새 테마 색으로 다시 칠함
}

// 마크다운/AsciiDoc → 안전한 HTML. DOMPurify 로 스크랩 본문 유래 위험 태그 제거.

// --- Knowledge Graph Canvas, Interactive Network & Layout ---
// 캔버스를 #net 박스의 실제 픽셀 크기에 맞춰 재설정(이슈1: 모바일에서 vis 가 생성 시점의
// 미해결 높이로 캔버스를 150px 로 잡아 상단 일부만 차지하던 버그). '100%' 는 flex/auto
// 체인에서 안 먹어서 getBoundingClientRect 의 실측 px 로 강제한다. ResizeObserver 가
// 레이아웃 확정·세로스택 전환·회전 시점마다(초기 1회 포함) 다시 맞춘다.
function relayout(force=false){ if(!net) return;
  const el=document.getElementById('net'); if(!el) return;
  const r=el.getBoundingClientRect();
  if(r.width<=0 || r.height<=0) return;
  const isFirstVisibleSize = (lastNetSize.w <= 0 || lastNetSize.h <= 0);
  if(netBusy && !force && !isFirstVisibleSize) return;
  if(!force && !isFirstVisibleSize && Math.abs(r.width-lastNetSize.w)<1 && Math.abs(r.height-lastNetSize.h)<1) return;
  lastNetSize = {w:r.width, h:r.height};
  net.setSize(r.width+'px', r.height+'px'); net.redraw(); }
if(window.ResizeObserver){ new ResizeObserver(()=>relayout()).observe(document.getElementById('net')); }
window.addEventListener('resize', ()=>relayout());
window.addEventListener('orientationchange', ()=>setTimeout(()=>relayout(), 300));

function graphAnimation(value=true){ return reducedMotionMQ.matches ? false : value; }
function rememberGraphCamera(){
  if(!net || preservingGraphCamera || netBusy) return;
  graphCamera={position:net.getViewPosition(),scale:net.getScale()};
}
function relayoutPreservingCamera(){
  if(!net || netBusy) return;
  const el=document.getElementById('net');
  if(!el) return;
  const r=el.getBoundingClientRect();
  if(r.width<=0 || r.height<=0) return;
  const sizeChanged = Math.abs(r.width-lastNetSize.w)>=2 || Math.abs(r.height-lastNetSize.h)>=2;
  const saved=graphCamera;
  if(sizeChanged){
    preservingGraphCamera=true;
    relayout(true);
    preservingGraphCamera=false;
    if(saved && net){
      net.moveTo({position:saved.position,scale:saved.scale,animation:false});
    }
  }
  rememberGraphCamera();
}
function syncWorkspaceLayout(){
  document.body.dataset.activePane=activePane;
  document.body.dataset.centerView=centerView;
  paneNames.forEach(name=>{
    const selected=name===activePane;
    const tab=paneTabs[name];
    if(tab){
      tab.setAttribute('aria-selected', selected?'true':'false');
      tab.tabIndex=selected?0:-1;
    }
    const el=paneEls[name];
    if(el){
      if(mobileMQ.matches){
        el.setAttribute('aria-hidden', selected?'false':'true');
        el.inert=!selected;
      }else{
        el.setAttribute('aria-hidden','false');
        el.inert=false;
      }
    }
  });
  const isMobileOrCompact = compactMQ.matches || mobileMQ.matches;
  const isDrawerActive = isMobileOrCompact && (drawerOpen || detailOpen);
  document.body.classList.toggle('detail-open', isMobileOrCompact && detailOpen);
  document.body.classList.toggle('drawer-open', isDrawerActive);
  const moreBtn = document.getElementById('morebtn');
  if(moreBtn) moreBtn.setAttribute('aria-expanded', isDrawerActive?'true':'false');
  const menuBtn = document.getElementById('tab-menu');
  if(menuBtn) menuBtn.setAttribute('aria-expanded', (isMobileOrCompact && drawerOpen)?'true':'false');

  const dp = document.getElementById('detailpane');
  let detailVisible = false;
  if(dp){
    if(compactMQ.matches || mobileMQ.matches){
      dp.setAttribute('aria-hidden', isDrawerActive?'false':'true');
      dp.inert=!isDrawerActive;
      detailVisible = isDrawerActive;
    }else{
      dp.setAttribute('aria-hidden','false');
      dp.inert=false;
      detailVisible = true;
    }
  }

  // aside#detailpane의 aria-hidden이 false일 때 (또는 detailCompact 설정 시)
  // 우측 메뉴를 아이콘만 보여주는 컴팩트 레일 모드로 전환
  const isCompact = !mobileMQ.matches && detailVisible && detailCompact;
  document.body.classList.toggle('detail-compact', isCompact);
  const wrapEl = document.getElementById('wrap');
  if(wrapEl) wrapEl.classList.toggle('detail-compact', isCompact);
  if(dp) dp.classList.toggle('compact-rail', isCompact);
  const toggleBtn = document.getElementById('detailtogglebtn');
  if(toggleBtn){
    toggleBtn.setAttribute('aria-expanded', isCompact ? 'false' : 'true');
    toggleBtn.title = isCompact ? '우측 메뉴 펼치기' : '우측 메뉴 축소(아이콘 모드)';
    toggleBtn.setAttribute('aria-label', isCompact ? '우측 메뉴 펼치기' : '우측 메뉴 축소(아이콘 모드)');
  }

  if(activePane==='graph'){
    requestAnimationFrame(()=>{ relayoutPreservingCamera(); applyTouchMode(); });
  }
}
function revealWorkspace(name, focusTab=false){
  hideNodePop();
  if(!paneNames.includes(name)) return;
  if(activePane==='graph' && !netBusy) rememberGraphCamera();
  if(activePane !== name) pushAppHistory({ pane: name, modal: null });
  activePane=name;
  const r=document.getElementById('reader');
  if(r && r.classList.contains('open') && typeof closeReader==='function') closeReader();
  if(name==='graph'){
    setCenterView('graph');
    if(!activeDoc){
      fitGraphContext();
    }
  }
  if(name==='docs'){
    docSearchActive=false;
    const q=document.getElementById('docq');
    if(q && q.value){ q.value=''; }
    renderDocs('');
  }
  detailOpen=false;
  drawerOpen=false;
  detailReturnFocus=null;
  if(name!=='graph') closeGraphDocPicker();
  closeToolsMenu();
  syncWorkspaceLayout();
  if(focusTab && mobileMQ.matches && paneTabs[name]) paneTabs[name].focus();
}
function openDetailPane(){
  hideNodePop();
  detailReturnFocus=document.activeElement;
  detailOpen=true;
  drawerOpen=true;
  detailCompact=false;
  try{ localStorage.setItem('claireDetailCompact', 'false'); }catch(_){}
  if((compactMQ.matches || mobileMQ.matches)) pushAppHistory({ modal: 'drawer' });
  closeGraphDocPicker();
  syncWorkspaceLayout();
}
function closeDetailPane(){
  if(typeof window !== 'undefined' && window.history && window.history.state && window.history.state.modal === 'drawer' && !isPoppingHistory){
    window.history.back();
    return;
  }
  hideNodePop();
  detailOpen=false;
  drawerOpen=false;
  syncWorkspaceLayout();
  let target=detailReturnFocus && detailReturnFocus.isConnected ? detailReturnFocus : null;
  if(!target) target=(compactMQ.matches || mobileMQ.matches) ? (document.getElementById('tab-menu') || paneTabs[activePane] || document.getElementById('tab-docs')) : paneEls[activePane];
  detailReturnFocus=null;
  replaceAppHistory({ modal: getActiveModalName() });
  requestAnimationFrame(()=>{ if(target) target.focus(); });
}
function openDrawer(){
  hideNodePop();
  detailReturnFocus=document.activeElement;
  drawerOpen=true;
  detailOpen=true;
  if((compactMQ.matches || mobileMQ.matches)) pushAppHistory({ modal: 'drawer' });
  closeGraphDocPicker();
  syncWorkspaceLayout();
}
function closeDrawer(focus=false){
  if(typeof window !== 'undefined' && window.history && window.history.state && window.history.state.modal === 'drawer' && !isPoppingHistory){
    window.history.back();
    return;
  }
  hideNodePop();
  drawerOpen=false;
  detailOpen=false;
  syncWorkspaceLayout();
  let target=detailReturnFocus && detailReturnFocus.isConnected ? detailReturnFocus : null;
  if(!target) target=(compactMQ.matches || mobileMQ.matches) ? (document.getElementById('tab-menu') || paneTabs[activePane] || document.getElementById('tab-docs')) : document.getElementById('morebtn');
  detailReturnFocus=null;
  replaceAppHistory({ modal: getActiveModalName() });
  if(focus && target) requestAnimationFrame(()=>target.focus());
}
function toggleDrawer(){
  if(drawerOpen || detailOpen) closeDrawer(true);
  else openDrawer();
}
function openGraphFromDrawer(){
  openDocGraph(activeDoc || curReaderDoc || (mobileMQ.matches ? (getRecentDocId() || docWithMostNodes()) : null));
}
function focusMobileSearch(){
  revealWorkspace('docs');
  docSearchActive=true;
  const q=document.getElementById('docq');
  if(q){ q.value=''; renderDocs(''); q.focus(); }
}
function closeToolsMenu(focus=false){ closeDrawer(focus); }
function toggleToolsMenu(){ toggleDrawer(); }
function graphSelectableDocs(){
  const visible=allDocs.filter(dc=>dc.hidden!==1);
  return visible.filter(dc=>dc.pinned===1).concat(visible.filter(dc=>dc.pinned!==1));
}
function syncGraphDocNav(){
  const docs=graphSelectableDocs();
  const dc=activeDoc ? allDocs.find(item=>item.id===activeDoc) : null;
  const pick=document.getElementById('graphdocpick');
  document.getElementById('graphdoclabel').textContent=dc ? dc.title : '전체 그래프';
  pick.setAttribute('aria-label',dc ? '자료 전환, 현재 '+dc.title : '자료 선택, 현재 전체 그래프');
  const current=docs.findIndex(item=>item.id===activeDoc);
  const usable=current>=0 && docs.length>1;
  const prev=document.getElementById('graphdocprev'), next=document.getElementById('graphdocnext');
  prev.disabled=!usable; next.disabled=!usable;
  if(usable){
    const prevDoc=docs[(current-1+docs.length)%docs.length];
    const nextDoc=docs[(current+1)%docs.length];
    prev.setAttribute('aria-label','이전 자료: '+prevDoc.title);
    next.setAttribute('aria-label','다음 자료: '+nextDoc.title);
    prev.title='이전 자료: '+prevDoc.title;
    next.title='다음 자료: '+nextDoc.title;
  }else{
    prev.setAttribute('aria-label','이전 자료');
    next.setAttribute('aria-label','다음 자료');
    prev.title='이전 자료'; next.title='다음 자료';
  }
  const menu=document.getElementById('graphdocmenu');
  if(!menu.hidden) renderGraphDocPicker(document.getElementById('graphdocq').value);
}
function renderGraphDocPicker(filter=''){
  const q=filter.trim().toLowerCase();
  const docs=graphSelectableDocs().filter(dc=>
    !q || ((dc.title||'')+' '+(dc.summary||'')).toLowerCase().includes(q));
  let h='<button id="graphdocall" class="graphdocoption" data-graph-doc=""'+
    (!activeDoc?' aria-current="true"':'')+'>전체 그래프</button>';
  h+=docs.map(dc=>'<button class="graphdocoption" data-graph-doc="'+esc(dc.id)+'"'+
      (dc.id===activeDoc?' aria-current="true"':'')+'>'+
      (dc.pinned===1?'⭐ ':'')+esc(dc.title||'(제목 없음)')+
      (dc.source_type?'<small>'+esc(dc.source_type)+'</small>':'')+'</button>').join('');
  if(!docs.length) h+='<div id="graphdocempty">검색 결과가 없습니다.</div>';
  document.getElementById('graphdoclist').innerHTML=h;
}
function openGraphDocPicker(pushHist=true){
  const menu=document.getElementById('graphdocmenu'), q=document.getElementById('graphdocq');
  if(drawerOpen || detailOpen){
    closeDrawer(false, false);
  }
  q.value='';
  renderGraphDocPicker();
  menu.hidden=false;
  menu.inert=false;
  menu.setAttribute('aria-hidden','false');
  document.getElementById('graphdocpick').setAttribute('aria-expanded','true');
  if(pushHist) pushAppHistory({ modal: 'graphdocmenu' });
  requestAnimationFrame(()=>q.focus());
}
function closeGraphDocPicker(focus=true){
  if(typeof window !== 'undefined' && window.history && window.history.state && window.history.state.modal === 'graphdocmenu' && !isPoppingHistory){
    window.history.back();
    return;
  }
  const menu=document.getElementById('graphdocmenu');
  if(menu.hidden) return;
  menu.hidden=true;
  menu.inert=true;
  menu.setAttribute('aria-hidden','true');
  document.getElementById('graphdocpick').setAttribute('aria-expanded','false');
  replaceAppHistory({ modal: getActiveModalName() });
  if(focus) requestAnimationFrame(()=>document.getElementById('graphdocpick').focus());
}
function toggleGraphDocPicker(){
  const menu=document.getElementById('graphdocmenu');
  if(menu.hidden) openGraphDocPicker(); else closeGraphDocPicker(true);
}
function chooseGraphDoc(id){
  closeGraphDocPicker();
  setActiveDoc(id||null);
  requestAnimationFrame(()=>document.getElementById('graphdocpick').focus());
}
function stepGraphDoc(delta){
  const docs=graphSelectableDocs();
  const current=docs.findIndex(dc=>dc.id===activeDoc);
  if(current<0 || docs.length<2) return;
  const next=(current+delta+docs.length)%docs.length;
  chooseGraphDoc(docs[next].id);
}
const gdl = document.getElementById('graphdoclist');
if(gdl){
  gdl.addEventListener('click',e=>{
    const button=e.target.closest('[data-graph-doc]');
    if(button) chooseGraphDoc(button.dataset.graphDoc);
  });
}
syncGraphDocNav();
const wt = document.getElementById('worktabs');
if(wt){
  wt.addEventListener('click',e=>{
    const b=e.target.closest('[role=tab]'); if(b && b.dataset.pane) revealWorkspace(b.dataset.pane);
  });
  wt.addEventListener('keydown',e=>{
    const b=e.target.closest('[role=tab]'); if(!b) return;
    let i=paneNames.indexOf(b.dataset.pane);
    if(e.key==='ArrowRight') i=(i+1)%paneNames.length;
    else if(e.key==='ArrowLeft') i=(i+paneNames.length-1)%paneNames.length;
    else if(e.key==='Home') i=0;
    else if(e.key==='End') i=paneNames.length-1;
    else return;
    e.preventDefault(); revealWorkspace(paneNames[i],true);
  });
}
document.addEventListener('pointerdown',e=>{
  const bar=document.getElementById('bar');
  const pane=document.getElementById('detailpane');
  const bnav=document.getElementById('worktabs');
  if((drawerOpen||detailOpen) && pane && (!bar||!bar.contains(e.target)) && (!bnav||!bnav.contains(e.target)) && !pane.contains(e.target)) closeDrawer(false, true);
  const nav=document.getElementById('graphdocnav');
  const gdm=document.getElementById('graphdocmenu');
  if(gdm && !gdm.hidden && nav && !nav.contains(e.target)) closeGraphDocPicker(false, true);
});
document.addEventListener('keydown',e=>{
  if(e.key==='Escape' && (drawerOpen||detailOpen)){
    e.preventDefault(); closeDrawer(true, true);
  }
});
function responsiveChanged(){ closeToolsMenu(); syncWorkspaceLayout(); }
mobileMQ.addEventListener('change',responsiveChanged);
compactMQ.addEventListener('change',responsiveChanged);
toolbarMQ.addEventListener('change',responsiveChanged);
syncWorkspaceLayout();

// 단일-pane 그래프는 페이지 스크롤과 경쟁하지 않는다. vis/hammer가 그래프의 양방향
// pan을 처리하도록 touch-action:none을 유지하고, 다른 pane은 inert로 입력을 받지 않는다.
function applyTouchMode(){
  document.querySelectorAll('#net, #net div, #net canvas').forEach(el=>{ el.style.touchAction='none'; });
}
// 휠 줌 평탄화: vis 기본 줌은 wheel deltaY '크기'에 비례해 한 이벤트로 여러 단계 점프한다.
// Mac 트랙패드/매직마우스는 한 제스처에 큰 deltaY 를 모멘텀으로 연속 발사 → "한꺼번에 확대"
// (사용자 보고). 그래서 zoomView:false 로 두고, 스크롤 '거리'를 누적해 일정량(STEP_DELTA)마다
// 한 스텝씩, 프레임당 최대 1스텝만 포인터(커서) 중심으로 적용한다 — 플랫폼 무관하게 균일하고
// 모멘텀 폭주는 시간축으로 펼쳐져 점프가 사라진다.
function setupWheelZoom(){
  const cont=document.getElementById('net');
  const STEP_DELTA=80, FACTOR=1.12, MIN=0.05, MAX=5, CAP=STEP_DELTA*3;
  let accum=0, px=0, py=0, raf=0;
  function zoomAt(factor){  // 커서 아래 그래프 좌표가 고정되도록 scale 변경 후 view 보정
    const rect=cont.getBoundingClientRect(), dom={x:px-rect.left, y:py-rect.top};
    const before=net.DOMtoCanvas(dom);
    const scale=Math.max(MIN, Math.min(MAX, net.getScale()*factor));
    const vp=net.getViewPosition();
    net.moveTo({scale:scale, animation:false});
    const after=net.DOMtoCanvas(dom);
    net.moveTo({position:{x:vp.x+(before.x-after.x), y:vp.y+(before.y-after.y)}, scale:scale, animation:false});
  }
  function flush(){
    raf=0;
    if(Math.abs(accum)>=STEP_DELTA){           // 프레임당 한 스텝만 → 모멘텀을 시간축으로 펼침
      const expand=accum<0;                    // 위로 스크롤(deltaY<0)=확대
      accum+=expand?STEP_DELTA:-STEP_DELTA;
      zoomAt(expand?FACTOR:1/FACTOR);
    }
    if(Math.abs(accum)>=STEP_DELTA) raf=requestAnimationFrame(flush);  // 남으면 다음 프레임
  }
  cont.addEventListener('wheel', e=>{
    e.preventDefault();
    accum+=e.deltaY; px=e.clientX; py=e.clientY;
    accum=Math.max(-CAP, Math.min(CAP, accum)); // 모멘텀 폭주 상한(손 떼면 곧 멈춤)
    if(!raf) raf=requestAnimationFrame(flush);
  }, {passive:false});
}
// 모바일 +/- 줌 버튼 — 화면 중심 기준(커서 개념이 없으니 뷰 중심 고정, moveTo 가 position
// 생략 시 현재 중심을 유지한다).
function zoomBtn(dir){
  if(!net) return;
  const scale=Math.max(0.05, Math.min(5, net.getScale()*(dir>0?1.25:1/1.25)));
  net.moveTo({scale:scale, animation:graphAnimation({duration:150})});
}
function cameraToNodes(ids){
  if(!net || !ids.length) return;
  if(ids.length===1) net.focus(ids[0],{scale:1.2,animation:graphAnimation(true)});
  else net.fit({nodes:ids,animation:graphAnimation(true)});
}
function fitGraphContext(){
  if(!net || !allNodes) return;
  let ids=[];
  if(pathNodes && pathNodes.size) ids=[...pathNodes];
  else if(selectedNodeId) ids=[selectedNodeId];
  else{
    allNodes.forEach(n=>{
      if(n.hidden || (typeof n.id==='string' && n.id.indexOf('cl_')===0)) return;
      let match=true;
      if(activeDoc) match=match && (n.sources||[]).includes(activeDoc);
      if(highlightSet) match=match && highlightSet.has(n.id);
      if(match) ids.push(n.id);
    });
  }
  if(!ids.length) resetGraphCamera(); else cameraToNodes(ids);
}
function resetGraphCamera(){
  if(!net || !allNodes) return;
  const ids=[];
  allNodes.forEach(n=>{
    if(!n.hidden && !(typeof n.id==='string' && n.id.indexOf('cl_')===0)) ids.push(n.id);
  });
  if(ids.length) net.fit({nodes:ids,animation:graphAnimation(true)});
}

fetch('graph').then(r=>{ if(!r.ok) throw new Error('graph fetch HTTP '+r.status); return r.json(); }).then(d=>{
  if(typeof vis !== 'undefined' && vis.DataSet && vis.Network){
    const rawNodes = ((d && d.nodes) || []).map(n => ({
      ...n,
      size: nodeRadius(n.degree),
      font: { size: nodeFontSize(n.degree) }
    }));
    allNodes = new vis.DataSet(rawNodes);
    allEdges = new vis.DataSet((d && d.edges) || []);
    if(!d || !d.nodes || !d.nodes.length){ graphStabilized=true; }
    const totalCount = rawNodes.length;
    let initialDeg = 0;
    if(totalCount >= 200){
      initialDeg = 2;
    } else if(totalCount >= 80){
      initialDeg = 1;
    } else {
      initialDeg = 0;
    }
    curMinDeg = initialDeg;
    const sl = document.getElementById('fslider');
    if(sl){ sl.max = (d && d.stats && d.stats.max_degree) || 0; sl.value = initialDeg; }
    const fmin = document.getElementById('fmin');
    if(fmin) fmin.textContent = initialDeg;
    updateDegPresets();
    allTypes=[...new Set(rawNodes.map(n=>n.group))].sort();
    allRelTypes=[...new Set(((d && d.edges) || []).map(e=>e.label).filter(Boolean))].sort();
    renderLegend();
    const th=T();
    const netBg = (typeof getComputedStyle==='function'?getComputedStyle(document.documentElement).getPropertyValue('--net-bg').trim():'')||'#ffffff';
    const opts = {
      nodes:{shape:'dot',size:14,font:{color:th.nodeFont,size:13},borderWidth:1,borderWidthSelected:3},
      edges:{color:{color:th.edge,highlight:th.edgeHi},
        font:{color:th.nodeFont,size:0,strokeWidth:3,strokeColor:netBg},smooth:false},
      groups:buildGroups(),
      physics:getPhysicsOpts(totalCount),
      interaction:{hover:true,tooltipDelay:120,multiselect:true,zoomView:false}
    };
    const netEl = document.getElementById('net');
    if(netEl) net = new vis.Network(netEl, {nodes:allNodes, edges:allEdges}, opts);
    isDraggingNode = false;
    clearTimeout(settleTimer);
    settleTimer = setTimeout(()=>{
      if(net && !isDraggingNode){
        net.setOptions({physics:false});
      }
    }, 2500);
    requestAnimationFrame(()=>{ relayout(); setTimeout(relayout, 300); });
    applyTouchMode();
    setupWheelZoom();
  } else {
    const st = document.getElementById('stat');
    if(st) st.textContent = '데이터 준비 완료';
  }
  // pane 전환·주소창 접힘 등으로 viewport가 바뀌어도 fit 애니메이션 중 setSize가
  // 카메라를 흔들지 않게 한다. 드래그/애니메이션 중엔 relayout
  // 을 미루고, 크기 변화가 실제로 없으면 아예 스킵(불필요한 setSize+redraw 로 인한 churn 방지).
  // animationFinished 에만 기대면 발화 안 되는 경우(실측: 0개 노드 fit 등) netBusy 가 영원히
  // true 로 굳어 relayout 이 죽는 더 나쁜 회귀가 됨 → 항상 풀리는 타임아웃을 안전망으로 병행.
  let busyTimer = null;
  function markBusy(ms){ netBusy = true; clearTimeout(busyTimer); busyTimer = setTimeout(()=>{netBusy=false;}, ms); }
  function getAnimDuration(opts){
    if(!opts || !opts.animation) return 0;
    if(typeof opts.animation==='object' && typeof opts.animation.duration==='number') return opts.animation.duration;
    return 1000;
  }
  if(net){
    net.on('dragStart', p => {
      netBusy = true;
      hideNodePop();
      if(p && p.nodes && p.nodes.length){
        isDraggingNode = true;
        clearTimeout(settleTimer);
        net.setOptions({physics:true});
      }
    });
    net.on('dragEnd', () => {
      netBusy = false;
      rememberGraphCamera();
      if(isDraggingNode){
        isDraggingNode = false;
        setTimeout(()=>{
          if(net && !isDraggingNode) net.setOptions({physics:false});
        }, 800);
      }
    });
    net.on('animationFinished', () => {
      netBusy = false;
      clearTimeout(busyTimer);
      const show=net.getScale()>=1.45;
      if(show!==edgeLabelsByZoom){ edgeLabelsByZoom=show; applyView(); }
      rememberGraphCamera();
    });
    net.on('stabilized', ()=>{
      graphStabilized=true;
      if(!netBusy) rememberGraphCamera();
    });
    const _fit = net.fit.bind(net), _focus = net.focus.bind(net), _moveTo = net.moveTo.bind(net);
    net.fit = (opts) => {
      const dur = getAnimDuration(opts);
      if(dur > 0) markBusy(dur + 350);
      return _fit(opts);
    };
    net.focus = (id, opts) => {
      const dur = getAnimDuration(opts);
      if(dur > 0) markBusy(dur + 350);
      return _focus(id, opts);
    };
    net.moveTo = (opts) => {
      const dur = getAnimDuration(opts);
      if(dur > 0) markBusy(dur + 350);
      return _moveTo(opts);
    };
    net.on('click', p => {
      if(!p.nodes.length){
        // 빈 캔버스 클릭: inspect 만 해제하고 검색(라벨/의미) 강조 선택은 유지(이슈4).
        // vis 가 내부적으로 선택을 비우므로 그 뒤에 검색 선택을 다시 적용한다.
        hideNodePop();
        selectedNodeId=null;
        applyView();
        if(highlightSet && highlightSet.size) setTimeout(restoreSelection, 0);
        return;
      }
      const id=p.nodes[0], ev=p.event.srcEvent;
      if(pathMode){ hideNodePop(); pickPathNode(id); return; }                // 경로 모드: 클릭으로 시작/끝 노드 지정
      if(ev && (ev.ctrlKey||ev.metaKey) && canWrite()){ hideNodePop(); toggleSynth(id); } // owner만 종합 수집
      else {
        const isMobile = (compactMQ && compactMQ.matches) || (mobileMQ && mobileMQ.matches);
        let px = null, py = null;
        if(ev){
          if(ev.clientX != null && ev.clientY != null){
            px = ev.clientX; py = ev.clientY;
          } else if(ev.changedTouches && ev.changedTouches[0]){
            px = ev.changedTouches[0].clientX; py = ev.changedTouches[0].clientY;
          } else if(ev.touches && ev.touches[0]){
            px = ev.touches[0].clientX; py = ev.touches[0].clientY;
          }
        }
        if(px == null || py == null){
          try {
            const netRect = netEl ? netEl.getBoundingClientRect() : { left: 0, top: 0 };
            if(p.pointer && p.pointer.DOM){
              px = p.pointer.DOM.x + netRect.left;
              py = p.pointer.DOM.y + netRect.top;
            } else {
              const domPos = net.canvasToDOM(net.getPosition(id));
              px = domPos.x + netRect.left;
              py = domPos.y + netRect.top;
            }
          } catch(_) {}
        }
        if(px == null || py == null){
          px = mouseXY.x; py = mouseXY.y;
        }
        if(isMobile){
          // 모바일: 탭 시 마우스 롤오버 요약 팝업 표시 및 노드 선택
          // 이미 팝업이 떠 있는 동일 노드를 다시 탭하면 상세 서랍을 열어준다
          if(selectedNodeId === id && nodepop && nodepop.style.display !== 'none'){
            hideNodePop();
            openDetailPane();
          } else {
            selectedNodeId=id;
            loadNode(id, false);
            showNodePop(id, px, py);
          }
        } else {
          hideNodePop();
          selectedNodeId=id;
          loadNode(id, true);
          openDetailPane();
        }
      }
    });
    // hover → 1.5초 뒤 마우스 위치에 작은 요약 팝업(우측 패널은 안 건드림 — 난잡함 해소, 사용자 요구).
    // 우측 패널은 클릭(inspect)일 때만 바뀐다 → hover 가 패널/선택을 흔들지 않아 복원 로직도 불필요.
    net.on('hoverNode', p => {
      const isMobile = (compactMQ && compactMQ.matches) || (mobileMQ && mobileMQ.matches);
      if(isMobile) return;
      clearTimeout(hoverTimer);
      if(!canShowNodePop(p.node)) return;
      hoverTimer=setTimeout(()=>showNodePop(p.node), 1500);
    });
    net.on('blurNode', () => {
      const isMobile = (compactMQ && compactMQ.matches) || (mobileMQ && mobileMQ.matches);
      if(!isMobile) hideNodePop();
    });
    net.on('hold', () => {
      const isMobile = (compactMQ && compactMQ.matches) || (mobileMQ && mobileMQ.matches);
      if(!isMobile) hideNodePop();
    });
    net.on('dragStart', hideNodePop);   // 드래그/줌 중엔 팝업 숨김(커서를 따라다니지 않게)
    net.on('selectEdge', p=>{ selectedEdgeIds=new Set(p.edges||[]); applyView(); });
    net.on('deselectEdge', ()=>{ selectedEdgeIds.clear(); applyView(); });
    let zoomDebounceTimer = null;
    net.on('zoom', ()=>{
      hideNodePop();
      if(!netBusy){
        rememberGraphCamera();
        clearTimeout(zoomDebounceTimer);
        zoomDebounceTimer = setTimeout(()=>{
          if(!net) return;
          const show=net.getScale()>=1.45;
          if(show!==edgeLabelsByZoom){ edgeLabelsByZoom=show; applyView(); }
        }, 120);
      }
    });
  }
  applyView();
  if(activeDoc && !selectedNodeId){
    if(curReaderDocData && curReaderDocData.id===activeDoc) renderDocPanel(curReaderDocData);
    else loadDocPanel(activeDoc);
  }
}).catch(err => {
  console.warn('graph load error:', err);
  const st=document.getElementById('stat');
  if(st && st.textContent.indexOf('로딩')!==-1) st.textContent='그래프 준비 완료';
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
  unclusterEdges();   // 검색으로 뭉치게 한 임시 spring 엣지 제거 → 물리가 원래대로
  applyView();
}
document.addEventListener('keydown', e=>{
  if(handleReaderKey(e)) return;
  if(e.key!=='Escape') return;
  const bar=document.getElementById('bar');
  if(!document.getElementById('graphdocmenu').hidden){ closeGraphDocPicker(true, true); return; }
  if(bar.classList.contains('tools-open')){ closeToolsMenu(true, true); return; }
  if(document.body.classList.contains('detail-open')){ closeDetailPane(true); return; }
  clearSelections(); });

function loadNode(id, hidePop=true){
  if(hidePop) hideNodePop();
  if(net) net.selectNodes([id]);   // 클릭 inspect — hover 는 더 이상 패널을 안 쓴다(팝업으로 분리)
  applyView();                     // 선택 노드 주변 엣지만 강조하고 관계 라벨을 펼친다
  fetch('node?id='+encodeURIComponent(id)).then(r=>r.json()).then(renderPanel);
}
function renderPanel(d){
  if(!d || d.error){ panel.innerHTML='<p class=hint>노드를 찾을 수 없습니다.</p>'; return; }
  const inSet = synthSet.has(d.id);
  // 문서를 고른 상태에서 노드로 들어왔으면 문서 패널로 한 번에 돌아갈 링크.
  let h = activeDoc ? '<span class=backlink onclick="loadDocPanel(activeDoc)">← 문서로 돌아가기</span>' : '';
  h+='<h2>'+esc(d.name)+' <small>'+esc(d.type)+(d.provisional?' ⚠️provisional':'')+'</small></h2>';
  // readonly(/webro) 세션은 종합(/synthesize)이 서버에서 막혀있어 버튼 자체를 안 그림.
  if(canWrite()) h+='<button class="sec" onclick="addToSynth(\''+d.id+'\')">'+(inSet?'✓ 종합 목록에 있음':'➕ 종합에 추가')+'</button>';
  if(d.aliases.length) h+='<p class=al>별칭: '+d.aliases.map(esc).join(', ')+'</p>';
  if(d.observations.length){ h+='<h3>관찰 · 주장</h3><ul>'+
    d.observations.map(o=>'<li>'+esc(o)+'</li>').join('')+'</ul>'; }
  if(d.documents.length){ h+='<h3>출처 문서 ('+d.documents.length+')</h3>';
    // 설명(summary) → 📖 본문 보기(중앙 리더로 열기) → 원문 링크 순.
    d.documents.forEach(dc=>{ h+='<div class=doc><b>'+esc(dc.title)+'</b>'+
      (dc.summary?'<p>'+esc(dc.summary)+'</p>':'')+
      ((dc.detail||dc.summary)?'<button class=readbtn data-read-doc="'+esc(dc.id)+
        '" onclick="openReader(\''+dc.id+'\')">📖 본문 보기</button>':'')+
      (dc.url?'<p class=src><a href="'+esc(dc.url)+'" target=_blank rel=noopener>↗ 원문 열기</a></p>':'')+
      '</div>'; }); }
  if(d.neighbors.length){ h+='<h3>연결 ('+d.neighbors.length+')</h3><ul>';
    d.neighbors.forEach(n=>{ const ar=n.dir=='out'?'→':'←';
      h+='<li><span class=rel>'+esc(n.rel)+'</span> '+ar+
         ' <a href="#" onclick="loadNode(\''+n.id+'\');return false">'+esc(n.name)+
         '</a> <small>'+esc(n.type)+'</small></li>'; }); h+='</ul>'; }
  // 맥락 확장 조사 — 읽다가 더 알고 싶은 키워드/문장을 지금 맥락으로 조사해 그래프 확장.
  // readonly 는 /research 도 서버에서 막혀있어 입력창·버튼 자체를 안 그림.
  if(canWrite()) h+='<h3>🔬 더 알아보기</h3>'+
    '<div class=research><input id="rq" placeholder="더 알고 싶은 키워드/문장" '+
    'onkeydown="if(event.key===\'Enter\')doResearch()"/>'+
    '<button onclick="doResearch()">조사</button></div>'+
    '<p class=al>지금 보는 맥락에 맞춰 웹 조사 → 맥락 일치·품질 통과 시 그래프에 추가됩니다.</p>';
  panel.innerHTML=h;
  if(typeof window.gtag === 'function' && d && d.id){
    try{
      window.gtag('event', 'select_content', {
        content_type: 'node',
        item_id: d.id
      });
    }catch(_){}
  }
}

// --- 맥락 확장 조사: 조사(grounding)→판정 게이트→통과 시 그래프 적재(서버) ---
// 서버가 NDJSON 스트림으로 진행 이벤트({stage,msg})를 흘리고 마지막 줄이
// {done:true, result:{...}} — 마냥 기다리지 않고 단계·rate limit 상황을 실시간 표시(피드백).
async function doResearch(){
  if(!canWrite()) return;
  const q=((document.getElementById('rq')||{}).value||'').trim();
  if(!q){ alert('조사할 키워드/문장을 입력하세요.'); return; }
  if(!selectedNodeId && !activeDoc){ alert('노드를 선택하거나 문서를 연 뒤 조사하세요.'); return; }
  const backId=selectedNodeId;
  panel.innerHTML='<h2>🔬 조사: '+esc(q)+'</h2><p class="al" id="relapsed">시작…</p><ul id="rprog"></ul>';
  openDetailPane();
  const t0=Date.now();
  const timer=setInterval(()=>{ const el=document.getElementById('relapsed');
    if(el) el.textContent='⏱ 경과 '+Math.round((Date.now()-t0)/1000)+'s'; else clearInterval(timer); },1000);
  let result=null;
  try{
    const r=await fetch('research',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({query:q, node_id:selectedNodeId, doc_id:activeDoc})});
    if(r.status===401||r.status===404){ clearInterval(timer); expireWriteAccess();
      panel.innerHTML='<p class=hint>세션 만료 — 텔레그램 /web 으로 다시 접속하세요</p>'; return; }
    if(!r.ok){ clearInterval(timer); let d={};
      try{ d=await r.json(); }catch(_){}
      panel.innerHTML='<p class=hint>조사 요청 실패: '+esc(d.error||('HTTP '+r.status))+'</p>'; return; }
    if(!r.body) throw new Error('스트림 본문이 없습니다');
    const reader=r.body.getReader(), dec=new TextDecoder(); let buf='';
    while(true){
      const {done,value}=await reader.read(); if(done) break;
      buf+=dec.decode(value,{stream:true});
      let i; while((i=buf.indexOf('\n'))>=0){
        const line=buf.slice(0,i).trim(); buf=buf.slice(i+1);
        if(!line) continue;
        let ev; try{ ev=JSON.parse(line); }catch(_){ continue; }
        if(ev.done){ result=ev.result; continue; }
        const ul=document.getElementById('rprog');
        if(ul){ const li=document.createElement('li'); li.className='al';
          const msg=(ev.msg||'').replace(/^[•*-]\\s*/, '');
          li.textContent=(ev.stage==='llm'?'⏳ ':'')+msg;
          ul.appendChild(li); }
      }
    }
  }catch(e){ clearInterval(timer);
    panel.innerHTML='<p class=hint>요청 실패: '+esc(String(e))+'</p>'; return; }
  clearInterval(timer);
  if(!result){ panel.innerHTML='<p class=hint>응답이 끊겼습니다 — 잠시 후 다시 시도하세요.</p>'; return; }
  renderResearchResult(result, backId);
}
function renderResearchResult(d, backId){
  if(d.error){ panel.innerHTML='<p class=hint>오류: '+esc(d.error)+'</p>'; return; }
  let h='<h2>🔬 조사: '+esc(d.query)+(d.context_focus?' <small>'+esc(d.context_focus)+' 맥락</small>':'')+'</h2>';
  h+='<p class=al>'+(d.added?'✅ ':'⏸ ')+esc(d.verdict||'')+
    ' <span class=meter>맥락일치 '+(d.relevance!=null?(+d.relevance).toFixed(2):'-')+
    ' · 품질 '+(d.quality!=null?(+d.quality).toFixed(2):'-')+'</span></p>';
  if(d.interpretation) h+='<p class=al>해석: '+esc(d.interpretation)+'</p>';
  if(d.report) h+='<div class=synth>'+esc(d.report)+'</div>';
  if((d.sources||[]).length){ h+='<h3>출처</h3><ul>'+d.sources.map(s=>
    '<li><a href="'+esc(s.url)+'" target=_blank>'+esc(s.title||s.url)+'</a></li>').join('')+'</ul>'; }
  if(d.added && d.ingest){
    h+='<p class=al>그래프 반영: 신규 '+d.ingest.entities_created+' · 기존연결 '+
      d.ingest.entities_linked+' · 관계 '+d.ingest.relations_added+'</p>';
    refreshGraph();
  } else if(d.reason){ h+='<p class=al>판정: '+esc(d.reason)+'</p>'; }
  if(backId) h+='<p><a href="#" onclick="loadNode(\''+backId+'\');return false">← 노드로 돌아가기</a></p>';
  panel.innerHTML=h;
}

// --- 웹 적재: URL/텍스트를 그래프에 적재(서버 /ingest-stream, /research 와 동일 NDJSON 스트리밍) ---
// 텔레그램 DM 과 같은 통로(svc.ingest, source='web') — 관련 링크 1홉 자동확장도 동일하게 동작.
function openIngest(){
  if(!canWrite()) return;
  panel.innerHTML='<h2>➕ 자료 적재</h2>'+
    '<p class=al>URL 또는 메모 텍스트를 입력하고 적재 방식을 선택하세요.</p>'+
    '<div class="ingest-form">'+
      '<div class="ingest-field">'+
        '<label class="ingest-label" for="ingin">자료 <span class="ingest-help">URL 또는 텍스트</span></label>'+
        '<textarea id="ingin" rows="5" placeholder="https://example.com/article"></textarea>'+
      '</div>'+
      '<fieldset class="ingest-options">'+
        '<legend class="ingest-label">적재 분량</legend>'+
        '<div class="ingest-choice-grid">'+
          '<div class="ingest-choice"><input id="ingamount-standard" type="radio" name="ingest-amount" value="standard" checked><label for="ingamount-standard" title="설정된 글자 수 상한을 적용합니다">일반</label></div>'+
          '<div class="ingest-choice"><input id="ingamount-full" type="radio" name="ingest-amount" value="full"><label for="ingamount-full" title="원문을 절단하지 않고 전문을 수집하고 분석합니다">전문</label></div>'+
        '</div>'+
      '</fieldset>'+
      '<fieldset class="ingest-options">'+
        '<legend class="ingest-label">사고 수준 <span class="ingest-help">미지정 시 서버 설정 사용</span></legend>'+
        '<div class="ingest-choice-grid effort">'+
          '<div class="ingest-choice"><input id="ingeffort-default" type="radio" name="ingest-effort" value="" checked><label for="ingeffort-default">기본</label></div>'+
          '<div class="ingest-choice"><input id="ingeffort-none" type="radio" name="ingest-effort" value="none"><label for="ingeffort-none">없음</label></div>'+
          '<div class="ingest-choice"><input id="ingeffort-minimal" type="radio" name="ingest-effort" value="minimal"><label for="ingeffort-minimal">최소</label></div>'+
          '<div class="ingest-choice"><input id="ingeffort-low" type="radio" name="ingest-effort" value="low"><label for="ingeffort-low">낮음</label></div>'+
          '<div class="ingest-choice"><input id="ingeffort-medium" type="radio" name="ingest-effort" value="medium"><label for="ingeffort-medium">중간</label></div>'+
          '<div class="ingest-choice"><input id="ingeffort-high" type="radio" name="ingest-effort" value="high"><label for="ingeffort-high">높음</label></div>'+
          '<div class="ingest-choice"><input id="ingeffort-max" type="radio" name="ingest-effort" value="max"><label for="ingeffort-max">최대</label></div>'+
        '</div>'+
      '</fieldset>'+
      '<div class="ingest-field">'+
        '<label class="ingest-label" for="ingfocus">초점 <span class="ingest-help">선택 사항</span></label>'+
        '<textarea id="ingfocus" rows="2" placeholder="예: 시스템 아키텍처와 내부 동작 중심"></textarea>'+
      '</div>'+
      '<div class="ingest-submit"><button type="button" onclick="runIngest()">적재 시작</button></div>'+
    '</div>';
  openDetailPane();
  const ta=document.getElementById('ingin'); if(ta) ta.focus();
}
async function runIngest(){
  if(!canWrite()) return;
  const ta=document.getElementById('ingin');
  const payload=((ta||{}).value||'').trim();
  if(!payload){ alert('적재할 URL 또는 텍스트를 입력하세요.'); return; }
  const focus=((document.getElementById('ingfocus')||{}).value||'').trim();
  const amountChoice=document.querySelector('input[name="ingest-amount"]:checked');
  const effortChoice=document.querySelector('input[name="ingest-effort"]:checked');
  const fullContent=!!amountChoice && amountChoice.value==='full';
  const effort=effortChoice ? effortChoice.value : '';
  let labelText = '시작…';
  const optionLabels=[];
  if(fullContent) optionLabels.push('전문 적재');
  if(effort) optionLabels.push('사고: '+effort);
  if(focus) optionLabels.push('초점: '+(focus.length > 20 ? focus.slice(0,20)+'…' : focus));
  if(optionLabels.length){
    labelText += ' ('+optionLabels.join(' · ')+')';
  }
  panel.innerHTML='<h2>➕ 적재 중</h2><p class="al" id="ielapsed">' + esc(labelText) + '</p><ul id="iprog"></ul>';
  openDetailPane();
  const t0=Date.now();
  const timer=setInterval(()=>{ const el=document.getElementById('ielapsed');
    if(el) el.textContent='⏱ 경과 '+Math.round((Date.now()-t0)/1000)+'s'+(optionLabels.length?' ('+optionLabels.join(' · ')+')':''); else clearInterval(timer); },1000);
  let result=null;
  try{
    const bodyObj = {payload:payload, full_content:fullContent};
    if(effort) bodyObj.effort=effort;
    if(focus) bodyObj.focus=focus;
    const r=await fetch('ingest-stream',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify(bodyObj)});
    if(r.status===401||r.status===404){ clearInterval(timer); expireWriteAccess();
      panel.innerHTML='<p class=hint>세션 만료 — 텔레그램 /web 으로 다시 접속하세요</p>'; return; }
    if(!r.ok){ clearInterval(timer); let d={};
      try{ d=await r.json(); }catch(_){}
      panel.innerHTML='<p class=hint>적재 요청 실패: '+esc(d.error||('HTTP '+r.status))+'</p>'; return; }
    if(!r.body) throw new Error('스트림 본문이 없습니다');
    const reader=r.body.getReader(), dec=new TextDecoder(); let buf='';
    while(true){
      const {done,value}=await reader.read(); if(done) break;
      buf+=dec.decode(value,{stream:true});
      let i; while((i=buf.indexOf('\n'))>=0){
        const line=buf.slice(0,i).trim(); buf=buf.slice(i+1);
        if(!line) continue;
        let ev; try{ ev=JSON.parse(line); }catch(_){ continue; }
        if(ev.done){ result=ev.result; continue; }
        const ul=document.getElementById('iprog');
        if(ul){ const li=document.createElement('li'); li.className='al';
          li.textContent=(ev.msg||'').replace(/^[•*-]\\s*/, ''); ul.appendChild(li); }
      }
    }
  }catch(e){ clearInterval(timer);
    panel.innerHTML='<p class=hint>요청 실패: '+esc(String(e))+'</p>'; return; }
  clearInterval(timer);
  if(!result){ panel.innerHTML='<p class=hint>응답이 끊겼습니다 — 잠시 후 다시 시도하세요.</p>'; return; }
  renderIngestResult(result);
}
function renderIngestResult(d){
  if(d.error){ panel.innerHTML='<h2>➕ 적재</h2><p class=hint>오류: '+esc(d.error)+'</p>'+
    '<p class=al>원본은 보관되어 자동복구(recover) 대상이 됩니다.</p>'; return; }
  let h='<h2>'+(d.duplicate?'♻️ 이미 있는 자료':(d.updated?'🔄 내용 갱신':'✅ 적재 완료'))+'</h2>';
  h+='<p class=al><b>'+esc(d.title||d.document_id||'(제목 없음)')+'</b>'+(d.partial?' <small>⚠️ 부분 처리</small>':'')+'</p>';
  if(d.directive) h+='<p class=al><b>초점:</b> '+esc(d.directive)+'</p>';
  const appliedOptions=[];
  if(d.full_content) appliedOptions.push('전문 적재');
  if(d.effort) appliedOptions.push('사고 수준 '+esc(d.effort));
  if(appliedOptions.length) h+='<p class=al><b>적용 설정:</b> '+appliedOptions.join(' · ')+'</p>';
  if(!d.duplicate) h+='<p class=al>노드 신규 '+(d.entities_created||0)+' · 기존연결 '+
    (d.entities_linked||0)+' · 관계 '+(d.relations_added||0)+'</p>';
  if(d.summary) h+='<div class=synth>'+esc(d.summary)+'</div>';
  if(d.document_id) h+='<p><a href="#" onclick="selectDoc(\''+d.document_id+'\');return false">문서 보기 →</a></p>';
  panel.innerHTML=h;
  refreshGraph();   // 신규 노드/엣지·문서목록 즉시 반영(새로고침 없이)
}

// --- 중복 문서 정리: 근사중복 클러스터를 찾아(/dedup/scan) 유지문서를 골라 병합(/dedup/merge) ---
// 병합 직전 정본은 서버가 내부 checkpoint로 보존한다(파괴적 작업 안전장치).
// keeper(유지) 외 문서는 참조(엔티티/관계 sources 등)를 keeper 로 재배치한 뒤 삭제.
let dedupClusters=[];
async function openDedup(){
  if(!canWrite()) return;
  panel.innerHTML='<h2>♻️ 중복 문서 정리</h2><p class="al">근사 중복 검사 중… '+
    '<small>(문서가 많으면 잠시 걸립니다)</small></p>';
  openDetailPane();
  let d;
  try{
    const r=await fetch('dedup/scan',{method:'POST'});
    if(r.status===401||r.status===404){ expireWriteAccess();
      panel.innerHTML='<p class=hint>세션 만료 — 텔레그램 /web 으로 다시 접속하세요</p>'; return; }
    d=await r.json();
  }catch(e){ panel.innerHTML='<h2>♻️ 중복 문서 정리</h2><p class=hint>검사 실패: '+esc(String(e))+'</p>'; return; }
  renderDedup(d);
}
function renderDedup(d){
  if(!canWrite()) return;
  if(d.error){ panel.innerHTML='<h2>♻️ 중복 문서 정리</h2><p class=hint>오류: '+esc(d.error)+'</p>'; return; }
  dedupClusters=d.clusters||[];
  let h='<h2>♻️ 중복 문서 정리</h2>';
  h+='<p class=al>검사 '+(d.documents||0)+'개 · 근사중복 클러스터 <b>'+dedupClusters.length+'</b>개</p>';
  if(!dedupClusters.length){ h+='<p class=al>근사 중복 문서가 없습니다. ✅</p>'; panel.innerHTML=h; return; }
  h+='<p class=al><small>유지할 문서를 고르고 병합하세요. 나머지는 유지문서로 합쳐지고 참조는 보존됩니다(병합 전 내부 체크포인트 생성).</small></p>';
  dedupClusters.forEach((c,ci)=>{
    h+='<div class=doc><p class=al>유사도 '+(c.score!=null?(+c.score).toFixed(2):'-')+'</p>';
    c.docs.forEach(dc=>{
      h+='<label style="display:block;margin:.3em 0">'+
        '<input type=radio name="keep'+ci+'" value="'+esc(dc.id)+'"'+(dc.id===c.keeper?' checked':'')+'> '+
        '<b>'+esc(dc.title||'(제목 없음)')+'</b> <small class=al>'+(dc.len||0)+'자</small>'+
        (dc.url?'<br><small class=al style="margin-left:1.5em;word-break:break-all">'+esc(dc.url)+'</small>':'')+
        '</label>';
    });
    h+='<button class=readbtn onclick="runDedupMerge('+ci+')">선택한 문서로 병합</button></div>';
  });
  panel.innerHTML=h;
}
async function runDedupMerge(ci){
  if(!canWrite()) return;
  const c=dedupClusters[ci]; if(!c) return;
  const sel=document.querySelector('input[name="keep'+ci+'"]:checked');
  const keeper=sel?sel.value:c.keeper;
  const losers=c.docs.map(x=>x.id).filter(id=>id!==keeper);
  if(!losers.length){ alert('합칠 문서가 없습니다.'); return; }
  if(!confirm(losers.length+'개 문서를 유지문서로 합칩니다. 계속할까요?\n(병합 전 내부 체크포인트를 생성합니다)')) return;
  panel.innerHTML='<h2>♻️ 병합 중…</h2><p class=al>체크포인트 생성 후 참조 재배치 중…</p>';
  try{
    const r=await fetch('dedup/merge',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({keeper:keeper, losers:losers})});
    if(r.status===401||r.status===404){ expireWriteAccess();
      panel.innerHTML='<p class=hint>세션 만료 — 텔레그램 /web 으로 다시 접속하세요</p>'; return; }
    const d=await r.json();
    if(!canWrite()) return;
    if(d.error){ panel.innerHTML='<h2>♻️ 병합</h2><p class=hint>오류: '+esc(d.error)+'</p>'; return; }
    let h='<h2>✅ 병합 완료</h2>';
    h+='<p class=al>문서 '+(d.deleted||0)+'개를 합쳤습니다. 엔티티 '+(d.entities_repointed||0)+
      ' · 관계 '+(d.relations_repointed||0)+' 참조 재배치.</p>';
    if(d.checkpoint) h+='<p class=al><small>내부 체크포인트: '+esc(d.checkpoint)+'</small></p>';
    h+='<p><a href="#" onclick="openDedup();return false">← 다시 검사</a></p>';
    panel.innerHTML=h;
    refreshGraph();   // 문서목록·그래프(병합으로 줄어든 sources) 갱신
  }catch(e){ panel.innerHTML='<h2>♻️ 병합</h2><p class=hint>요청 실패: '+esc(String(e))+'</p>'; }
}


// 조사로 그래프가 늘어난 뒤 새로고침 없이 신규 노드/엣지·문서목록을 반영.
// 엣지 id 는 rowid 순 enumerate(append-only)라 기존 id 는 안정 — 신규만 add.
function refreshGraph(){
  fetch('graph').then(r=>r.json()).then(d=>{
    // applyView 와 동일한 이유(그래프가 클수록 개별 update() 호출이 선형으로 느려짐)로
    // 갱신분을 모아 한 번에 반영 — 신규 노드(add)는 기존 add 도 이미 배열을 받으므로 그대로.
    const changed=[], added=[];
    d.nodes.forEach(n=>{
      const r = nodeRadius(n.degree), fs = nodeFontSize(n.degree);
      if(allNodes.get(n.id))
        changed.push({id:n.id, degree:n.degree, size:r, font:{size:fs}, sources:n.sources, obs:n.obs});
      else
        added.push({...n, size:r, font:{size:fs}});
    });
    if(changed.length) allNodes.update(changed);
    if(added.length) allNodes.add(added);
    d.edges.forEach(e=>{ if(!allEdges.get(e.id)) allEdges.add(e); });
    document.getElementById('fslider').max = d.stats.max_degree;
    updateDegPresets();
    applyView();
    if(activeDoc && curReaderDocData && curReaderDocData.id === activeDoc){
      renderDocPanel(curReaderDocData);
    }
  });
  fetch('documents').then(r=>r.json()).then(d=>{ allDocs=d.documents||[];
    renderDocs(document.getElementById('docq').value); });
}

// 전체 리로드 없이 새 글/엔티티/관계 반영 — 가벼운 주기 폴링. /stats 문서/엔티티/관계 개수 확인하고
// 바뀐 경우에만 refreshGraph()(append-only 병합) 실행 — 안 바뀌면 아무 요청도 안 함.
let lastStatsSig = null;
async function pollForUpdates(){
  try{
    const r = await fetch('stats');
    if(!r.ok) return;               // 401 등이면 조용히 다음 틱에 재시도
    const d = await r.json();
    const sig = [d.documents, d.entities, d.relations].join(':');
    if(lastStatsSig===null){ lastStatsSig = sig; return; }  // 최초 틱=기준값만 기록
    if(sig !== lastStatsSig){
      lastStatsSig = sig;
      refreshGraph();
      if(activeDoc){
        fetch('document?id='+encodeURIComponent(activeDoc)).then(x=>x.json()).then(dc=>{
          if(dc && !dc.error && activeDoc===dc.id){
            curReaderDocData=dc;
            renderDocPanel(dc);
          }
        }).catch(()=>{});
      }
    }
  }catch(e){ /* 네트워크 일시 오류 — 다음 틱에 재시도 */ }
}
setInterval(pollForUpdates, 25000);

// 단일 가시 규칙: degree(스케일)=hidden, 강조 필터(문서 선택 + 검색)=비매치 dim.
// 문서/라벨검색/의미검색이 모두 같은 강조 방식을 공유한다(시각 언어 통일).
// 범례 = 노드 타입 색(비클릭) + 관계 타입 토글 칩(클릭=필터). off 표시 타입은 그래프에서 숨김.
function renderLegend(){
  const nodeleg = allTypes.map(t=>
    '<span><i style="background:'+(TYPE_COLORS[t]||'#8b949e')+'"></i>'+esc(t)+'</span>').join('');
  let rel='';
  if(allRelTypes.length){
    rel = '<span class=lgsep>관계:</span> ' + allRelTypes.map((t,i)=>{
      const on = !relFilter || relFilter.has(t);
      return '<button type=button class="reltog'+(on?'':' off')+'" onclick="toggleRel('+i+
        ')" aria-pressed="'+(on?'true':'false')+'" title="이 관계만/제외 토글">'+esc(t)+'</button>';
    }).join(' ');
  }
  document.getElementById('legendbar').innerHTML = nodeleg + rel;
}
function toggleRel(i){
  const t=allRelTypes[i]; if(t===undefined) return;
  if(!relFilter){ relFilter=new Set([t]); }            // 첫 클릭(진입): 찍은 관계만 표시
  else if(relFilter.has(t)){ relFilter.delete(t);      // 켜진 것 다시 끄기
    if(relFilter.size===0) relFilter=null; }           // 다 끄면 전체로 복귀(빈 화면 방지)
  else { relFilter.add(t);                             // 다른 관계도 추가로 보기(누적)
    if(relFilter.size===allRelTypes.length) relFilter=null; }  // 전부 켜지면 필터 해제(=전체)
  renderLegend(); applyView();
}
function nodeLabel(id){ const n=allNodes&&allNodes.get(id); return n?n.label:id; }

function nodeRadius(deg){
  const d = deg || 0;
  if(d <= 1) return 10;
  if(d === 2) return 12;
  return Math.min(26, Math.round(11 + Math.sqrt(d) * 2.2));
}

function nodeFontSize(deg){
  const d = deg || 0;
  if(d <= 1) return 11;
  if(d === 2) return 12;
  return Math.min(15, Math.round(11 + Math.log2(d + 1)));
}

function getPhysicsOpts(nodeCount){
  const count = nodeCount || 0;
  let grav = -12000, cg = 0.12, spring = 150, overlap = 0.8;
  if(count >= 500){
    grav = -35000;
    cg = 0.04;
    spring = 220;
    overlap = 1.0;
  } else if(count >= 200){
    grav = -25000;
    cg = 0.06;
    spring = 190;
    overlap = 0.9;
  } else if(count >= 80){
    grav = -18000;
    cg = 0.09;
    spring = 170;
    overlap = 0.8;
  }
  return {
    solver: 'barnesHut',
    barnesHut: {
      gravitationalConstant: grav,
      centralGravity: cg,
      springLength: spring,
      springConstant: 0.03,
      damping: 0.4,
      avoidOverlap: overlap
    },
    minVelocity: 0.75,
    maxVelocity: 50,
    timestep: 0.5,
    stabilization: { iterations: 150 }
  };
}

function updateDegPresets(){
  const btns = document.querySelectorAll('.deg-preset-btn');
  btns.forEach(b => {
    const d = parseInt(b.getAttribute('data-deg'), 10);
    b.classList.toggle('active', curMinDeg === d);
  });
}

function setDeg(v){
  curMinDeg = +v;
  const sl = document.getElementById('fslider');
  if(sl && +sl.value !== curMinDeg) sl.value = curMinDeg;
  const fm = document.getElementById('fmin');
  if(fm) fm.textContent = v;
  updateDegPresets();
  applyView();
}
// 노드/엣지 개수가 늘수록 클릭마다 체감 지연이 커지던 원인: 아래 두 루프가 예전엔
// DataSet.update() 를 노드/엣지마다 하나씩(수백~천 회) 개별 호출했다 — vis DataSet 은
// update 호출마다 change 이벤트를 쏘고 Network 가 그때마다 리드로우를 예약해, 그래프가
// 자랄수록(현재 엔티티 500+·관계 500+, 매일 증가) 선형으로 느려졌다. update() 는 배열을
// 통째로 넘기면 이벤트/리드로우가 1회로 묶인다 — 계산은 그대로 하되 실제 반영만 모아서
// 한 번에 한다(문서 선택·검색·필터·degree 슬라이더 전부 이 함수를 타므로 공통 개선).
function applyView(){
  if(!allNodes) return;
  let shown=0, emph=0;
  const th=T();
  const netBg=(typeof getComputedStyle==='function'?getComputedStyle(document.documentElement).getPropertyValue('--net-bg').trim():'')||'#ffffff';
  const pathActive = !!(pathNodes && pathNodes.size);
  const hasFilter = activeDoc || highlightSet || pathActive;
  const nodeUpdates=[], matchedNodes=new Set();
  allNodes.forEach(n=>{
    if(typeof n.id==='string' && n.id.indexOf('cl_')===0) return;  // 검색 중앙 앵커는 안 건드림(숨김 유지)
    if(n.degree < curMinDeg){ nodeUpdates.push({id:n.id, hidden:true}); return; }
    let match = true;
    if(pathActive){ match = pathNodes.has(n.id); }   // 경로 모드: 경로 노드만 강조(다른 필터보다 우선)
    else {
      if(activeDoc) match = match && (n.sources||[]).includes(activeDoc);
      if(highlightSet) match = match && highlightSet.has(n.id);  // 검색(라벨/의미) 강조 집합
    }
    // 강조(문서 선택·검색·경로) 매치 노드는 흰 굵은 테두리 — dim 만으론 안 띄어서(피드백).
    // 노드별 color 가 group 색을 덮으므로 background/highlight 를 같이 명시해 유지한다.
    const lit = hasFilter && match, c = TYPE_COLORS[n.group]||'#8b949e';
    if(match) matchedNodes.add(n.id);
    const r = nodeRadius(n.degree);
    const fs = nodeFontSize(n.degree);
    nodeUpdates.push({id:n.id, hidden:false, size:r,
      font:{size:fs, color:th.nodeFont},
      opacity: match?1:DIM, borderWidth: lit?3:1,
      color:{background:c, border: lit?th.lit:th.nodeBorder,
             highlight:{background:c, border:th.lit},
             hover:{background:c, border: lit?th.lit:th.nodeBorder}}});
    shown++; if(match) emph++;
  });
  if(nodeUpdates.length) allNodes.update(nodeUpdates);  // 1회 배치(개별 호출 대신)
  // 엣지 가시성은 방금 갱신된 노드 hidden 상태를 봐야 하므로 노드 배치 반영 뒤에 계산.
  const edgeUpdates=[];
  allEdges.forEach(e=>{
    if(typeof e.id==='string' && e.id.indexOf('cl_')===0) return;  // 임시 클러스터 spring 엣지는 안 건드림(물리 유지)
    const f=allNodes.get(e.from), t=allNodes.get(e.to);
    let visible = !!(f && t && !f.hidden && !t.hidden);
    if(relFilter && !relFilter.has(e.label)) visible=false;       // 관계 타입 필터
    const onPath = !!(pathEdges && pathEdges.has(e.id));          // 경로 엣지 강조
    const incident = !!(selectedNodeId && (e.from===selectedNodeId || e.to===selectedNodeId));
    const selected = selectedEdgeIds.has(e.id);
    const contextEdge = !hasFilter || (matchedNodes.has(e.from) && matchedNodes.has(e.to));
    const muted = (selectedNodeId && !incident) || (hasFilter && !contextEdge);
    const labelOn = onPath || incident || selected || edgeLabelsByZoom;
    edgeUpdates.push({id:e.id, hidden: !visible, width:onPath?4:(incident||selected?2:1),
      color:onPath ? {color:th.lit,highlight:th.lit,opacity:1}
        : {color:th.edge,highlight:th.edgeHi,opacity:muted ? 0.08 : 1},
      font:{size:labelOn?10:0,color:th.nodeFont,strokeWidth:3,
        strokeColor:netBg}});
  });
  if(edgeUpdates.length) allEdges.update(edgeUpdates);  // 1회 배치
  document.getElementById('stat').innerHTML =
    '표시 <b>'+shown+'</b>/'+allNodes.length
    + (curMinDeg>0?' · 연결≥'+curMinDeg:'')
    + (relFilter?' · 관계 '+relFilter.size+'/'+allRelTypes.length:'')
    + (pathActive?' · 🔗경로 '+pathNodes.size+'노드':(hasFilter?' · 강조 '+emph+'개':''));
}

// --- 2노드 경로 하이라이트(전용 모드): 🔗 경로 → 시작/끝 노드 클릭 → 최단경로(BFS) 강조 ---
function togglePathMode(){
  pathMode=!pathMode; pathPicks=[];
  const b=document.getElementById('pathbtn'); if(b) b.classList.toggle('on', pathMode);
  if(pathMode) showPathHint();
  else { pathNodes=null; pathEdges=null; setGraphNotice(''); applyView(); panel.innerHTML=''; }
}
function setGraphNotice(text){
  const el=document.getElementById('graphnotice');
  el.textContent=text||''; el.classList.toggle('on',!!text);
}
function showPathHint(){
  const n=pathPicks.length;
  panel.innerHTML='<h2>🔗 경로 찾기</h2><p class=al>'+
    (n===0?'시작 노드를 클릭하세요.':'끝 노드를 클릭하세요. <small>(시작: '+esc(nodeLabel(pathPicks[0]))+')</small>')+
    '</p><p class=al><small>관계 필터가 켜져 있으면 그 관계만 따라 경로를 찾습니다.</small></p>';
  setGraphNotice(n===0?'경로 시작 노드를 선택하세요':'경로 끝 노드를 선택하세요');
  setCenterView('graph');
  revealWorkspace('graph');
}
function pickPathNode(id){
  pathPicks.push(id);
  if(pathPicks.length>=2) computePath(pathPicks[0], pathPicks[1]);
  else showPathHint();
}
function computePath(a, b){
  // 무방향 BFS — 전체 그래프 기준(relFilter 켜져 있으면 그 관계만). 클러스터 spring 엣지 제외.
  const adj={};
  allEdges.forEach(e=>{
    if(typeof e.id==='string' && e.id.indexOf('cl_')===0) return;
    if(relFilter && !relFilter.has(e.label)) return;
    (adj[e.from]=adj[e.from]||[]).push([e.to,e.id]);
    (adj[e.to]=adj[e.to]||[]).push([e.from,e.id]);
  });
  const prev={}, prevE={}, seen=new Set([a]); let q=[a];
  while(q.length){ const u=q.shift(); if(u===b) break;
    (adj[u]||[]).forEach(([v,eid])=>{ if(!seen.has(v)){ seen.add(v); prev[v]=u; prevE[v]=eid; q.push(v); } }); }
  pathPicks=[];
  if(!seen.has(b)){ pathNodes=null; pathEdges=null; applyView();
    setGraphNotice('');
    panel.innerHTML='<h2>🔗 경로</h2><p class=hint>두 노드 사이 연결 경로가 없습니다'+
      (relFilter?' (현재 관계 필터 기준)':'')+'.</p>'+
      '<p class=al><a href="#" onclick="restartPath();return false">다시 찾기</a></p>';
    openDetailPane();
    return; }
  pathNodes=new Set(); pathEdges=new Set();
  const order=[]; let cur=b;
  while(cur!==undefined){ order.push(cur); pathNodes.add(cur);
    if(prevE[cur]!==undefined) pathEdges.add(prevE[cur]);
    if(cur===a) break; cur=prev[cur]; }
  order.reverse();
  setGraphNotice('');
  applyView();
  if(net) net.fit({nodes:[...pathNodes], animation:graphAnimation(true)});
  panel.innerHTML='<h2>🔗 경로 <small>'+(order.length-1)+'단계</small></h2>'+
    '<p class=al>'+order.map(id=>esc(nodeLabel(id))).join(' → ')+'</p>'+
    '<p class=al><a href="#" onclick="restartPath();return false">다른 경로</a> · '+
    '<a href="#" onclick="clearPath();return false">해제</a></p>';
  openDetailPane();
}
function restartPath(){ pathMode=true; pathPicks=[];
  const b=document.getElementById('pathbtn'); if(b) b.classList.add('on'); showPathHint(); }
function clearPath(){ pathMode=false; pathPicks=[]; pathNodes=null; pathEdges=null;
  const b=document.getElementById('pathbtn'); if(b) b.classList.remove('on');
  setGraphNotice(''); applyView(); panel.innerHTML=''; revealWorkspace('graph'); }

// --- 좌측 문서 패널(일자별 그룹) ---
function dayOf(ts){ if(!ts) return '(날짜 미상)';
  const d=new Date(ts*1000);
  return d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0')+'-'+String(d.getDate()).padStart(2,'0'); }
// 문서 하나의 목록 아이템 HTML — 즐겨찾기/일반/숨김 목록이 모두 공유(중복 방지).
// 클릭=문서 열기, 제목 좌측의 별표(⭐/☆)는 stopPropagation으로 즐겨찾기 토글.
function docItemHtml(dc){
  const unread = dc.seen===0, watching = dc.watch===1, pinned = dc.pinned===1, hid = dc.hidden===1;
  const pinBtn = canWrite()
    ? '<button class="docpin-btn'+(pinned?' pinned':'')+'" title="'+(pinned?'즐겨찾기 해제':'즐겨찾기에 추가')+
      '" aria-label="'+(pinned?'즐겨찾기 해제':'즐겨찾기에 추가')+
      '" onclick="event.stopPropagation();togglePin(\''+dc.id+'\','+(!pinned)+')">'+(pinned?'⭐':'☆')+'</button>'
    : (pinned ? '<span class="docpin-icon pinned" title="즐겨찾기">⭐</span>' : '');
  return '<div class="docitem'+(dc.id===activeDoc?' active':'')+(unread?' unread':'')+(hid?' hidden-doc':'')+
    '" onclick="selectDoc(\''+dc.id+'\')">'+
    '<div class=doctitle-line>'+
    pinBtn+
    (watching?'<span class=wbadge title="주기 갱신 추적(watch)">🔄</span>':'')+
    (unread?'<span class=ubadge title="아직 안 본 문서">●</span>':'')+
    '<b>'+esc(dc.title)+'</b><span class=st>'+esc(dc.source_type||'')+'</span>'+
    '</div>'+
    (dc.summary?'<p>'+esc(dc.summary.slice(0,110))+'</p>':'')+'</div>';
}
let showHidden = false;
function toggleShowHidden(){ if(!canWrite()) return; showHidden=!showHidden; renderDocs(); }
function renderDocs(filter){
  const q = (filter !== undefined ? filter : (document.getElementById('docq') ? document.getElementById('docq').value : '')).trim().toLowerCase();
  if(docSearchActive && q){
    cancelServerSearch();
    currentSearchSeq++;
  }
  if(docSearchActive && !q){
    const ph = document.getElementById('pinnedhead'); if(ph) ph.style.display = 'none';
    const pl = document.getElementById('pinnedlist'); if(pl) pl.innerHTML = '';
    const dl = document.getElementById('doclist'); if(dl) dl.innerHTML = doclistToolbarHtml() + '<p class="hint" style="padding:16px 12px;text-align:center">🔎 검색어를 입력하세요.</p>';
    const sh = document.getElementById('showhidden'); if(sh) sh.style.display = 'none';
    const hl = document.getElementById('hiddenlist'); if(hl) hl.innerHTML = '';
    syncGraphDocNav();
    return;
  }
  const match = dc => !q || (dc.title+' '+(dc.summary||'')).toLowerCase().includes(q);
  // 숨김(hidden)은 기본 목록·즐겨찾기 양쪽에서 제외(목록 전용 숨김, 그래프는 안 건드림).
  const visible = allDocs.filter(dc=> dc.hidden!==1 && match(dc));
  const pinned = visible.filter(dc=>dc.pinned===1);
  const rest = visible.filter(dc=>dc.pinned!==1);
  const hiddenDocs = allDocs.filter(dc=> dc.hidden===1 && match(dc));

  const ph = document.getElementById('pinnedhead');
  if(ph){
    ph.style.display = pinned.length ? '' : 'none';
    ph.textContent = '⭐ 즐겨찾기 (' + pinned.length + ')';
  }
  const pl = document.getElementById('pinnedlist'); if(pl) pl.innerHTML = pinned.map(docItemHtml).join('');

  const dl = document.getElementById('doclist');
  if(dl){
    dl.innerHTML = doclistToolbarHtml() + (rest.length
      ? (()=>{ let html='', curDay=null;
          rest.forEach(dc=>{ const day=dayOf(dc.fetched_at);
            if(day!==curDay){ html+='<div class=dday>'+day+'</div>'; curDay=day; }
            html+=docItemHtml(dc); });
          return html; })()
      : '<p class=hint style="padding:10px">문서 없음</p>');
  }

  const sh=document.getElementById('showhidden');
  const hl=document.getElementById('hiddenlist');
  // readonly 는 숨기기/해제 버튼이 없어 이 구간이 눌러도 소용없는 관리용 UI — 아예 숨김.
  if(!canWrite() || !hiddenDocs.length){
    if(sh) sh.style.display='none';
    if(hl) hl.innerHTML='';
  }else{
    if(sh){
      sh.style.display='';
      sh.textContent = (showHidden?'▲ ':'▼ ')+'🙈 숨김 '+hiddenDocs.length+'개 '+(showHidden?'접기':'보기');
    }
    if(hl) hl.innerHTML = showHidden ? hiddenDocs.map(docItemHtml).join('') : '';
  }
  const st = document.getElementById('stat');
  if(st){
    if(q){
      st.textContent = visible.length + '개 발견';
    } else {
      st.innerHTML = (allDocs ? allDocs.length : 0) + '개 문서' +
        (allNodes && allNodes.length ? ' (' + allNodes.length + ' 엔티티)' : '');
    }
  }
  syncGraphDocNav();
}
// 즐겨찾기/숨기기 토글 — 낙관적 갱신(즉시 반영) 후 서버 반영, 실패하면 되돌림.
async function togglePin(id, val){
  if(!canWrite()) return false;
  const d=allDocs.find(x=>x.id===id); if(d) d.pinned = val?1:0;
  renderDocs(document.getElementById('docq').value);
  try{
    const r=await fetch('document/pin',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({id:id, pinned:val})});
    if(r.status===401||r.status===404) expireWriteAccess();
    if(!r.ok && d){ d.pinned = val?0:1; renderDocs(document.getElementById('docq').value); }
  }catch(e){ if(d){ d.pinned = val?0:1; renderDocs(document.getElementById('docq').value); } }
  return true;
}
async function toggleHide(id, val){
  if(!canWrite()) return false;
  // 숨기는 방향(val=true)만 컨펌 — 오클릭 방지(사용자 지적) + 되돌리는 방법을 그 자리에서
  // 안내(어디서 다시 꺼내는지 몰라 헤매지 않게). 숨김 해제(val=false)는 안전한 방향이라
  // 컨펌 없이 즉시. 반환값 = 실제로 적용됐는지(컨펌 취소 시 false — 호출측 버튼 갱신 판단용).
  if(val && !confirm('이 문서를 목록에서 숨길까요?\n\n그래프 엔티티는 그대로 남고, 문서 '+
      '목록 맨 아래 "🙈 숨김 N개 보기"를 누르면 언제든 다시 꺼내(숨김 해제) 볼 수 있습니다.')){
    return false;
  }
  const d=allDocs.find(x=>x.id===id); if(d) d.hidden = val?1:0;
  renderDocs(document.getElementById('docq').value);
  try{
    const r=await fetch('document/hide',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({id:id, hidden:val})});
    if(r.status===401||r.status===404) expireWriteAccess();
    if(!r.ok && d){ d.hidden = val?0:1; renderDocs(document.getElementById('docq').value); }
  }catch(e){ if(d){ d.hidden = val?0:1; renderDocs(document.getElementById('docq').value); } }
  return true;
}
// 상세 패널의 숨기기 체크박스 — toggleHide 결과(컨펌 취소 여부)를 보고 체크박스 및 라벨 갱신.
async function panelToggleHide(id, val){
  if(!canWrite()) return;
  const ok = await toggleHide(id, val);
  const chk = document.getElementById('panelhidechk');
  const lbl = document.getElementById('panelhidelabel');
  if(!ok){
    if(chk) chk.checked = !val;
    return;
  }
  if(chk) chk.checked = !!val;
  if(lbl) lbl.textContent = val ? '🙈 숨김 처리됨' : '목록에서 숨기기';
}
function resetHome(){
  cancelServerSearch();
  currentSearchSeq++;
  hideNodePop();
  closeDrawer();
  toggleAdvSearch(false);
  docSearchActive = false;
  const dq = document.getElementById('docq');
  if(dq && dq.value){
    dq.value = '';
  }
  renderDocs('');
  const q = document.getElementById('q');
  if(q && q.value){
    q.value = '';
  }
  const sem = document.getElementById('sem');
  if(sem && sem.checked){
    sem.checked = false;
  }
  const semchk = document.getElementById('semchk');
  if(semchk && semchk.checked){
    semchk.checked = false;
  }
  updateSearchModeUI();
  clearTimeout(searchDebounce);
  highlightSet = null;
  unclusterEdges();
  if(pathMode) clearPath();
  if(net){
    try{ net.unselectAll(); }catch(_){}
  }
  resetGraphCamera();
  panel.innerHTML = defaultHint();
  activeDoc = null;
  selectedNodeId = null;
  curReaderDoc = null;
  setCenterView('graph');
  revealWorkspace('graph', false, true);
  renderDocs();
  applyView();
  resetGraphCamera();
  syncGraphDocNav();
  document.title = 'Claire Bible — 지식 그래프';
  if(mobileMQ.matches){
    closeReader(false, false);
  }
  if(typeof window.gtag === 'function'){
    try{
      window.gtag('event', 'page_view', {
        page_title: document.title,
        page_location: window.location.origin + '/'
      });
    }catch(_){}
  }
}

function selectDoc(id){
  recordSelectedDoc(id);
  openReader(id);
}
function setActiveDoc(id){
  activeDoc = id;
  if(id){
    curReaderDoc = id;
    recordSelectedDoc(id);
  }
  selectedNodeId=null;                          // 문서 모드로 전환 — 노드 inspect 해제
  renderDocs(document.getElementById('docq').value);
  applyView();
  if(activeDoc){
    const docId=activeDoc;
    requestAnimationFrame(()=>{ if(net && activeDoc===docId){
      if(centerView==='graph') relayout(true);
      // 전체 fit 이 아니라 그 문서의 노드들만 화면에 차게 — 최적 줌/위치로 이동(피드백).
      const ids=[]; allNodes.forEach(n=>{ if(!n.hidden && (n.sources||[]).includes(docId)) ids.push(n.id); });
      if(ids.length) net.selectNodes(ids);
      if(!ids.length) resetGraphCamera(); else cameraToNodes(ids);
    }});
    loadDocPanel(activeDoc);    // wide rail/후속 문맥 작업을 위해 내용만 준비
  } else {
    if(net) net.unselectAll();
    resetGraphCamera();
    panel.innerHTML = defaultHint();             // 해제 시 기본 힌트로 복원
  }
}

// 좌측 문서 선택 시 우측 패널: 문서 요약 + 상세 + '이 문서의 노드' 버튼.
function loadDocPanel(id){
  if(curReaderDocData && curReaderDocData.id===id){
    renderDocPanel(curReaderDocData);
    markDocumentSeen(id);
    return;
  }
  panel.innerHTML='<p class=hint>문서 불러오는 중…</p>';
  fetch('document?id='+encodeURIComponent(id)).then(r=>r.json()).then(dc=>{
    if(activeDoc!==id) return;                  // 그 사이 다른 문서/노드로 이동했으면 무시
    if(!dc || dc.error){ panel.innerHTML='<p class=hint>문서를 찾을 수 없습니다.</p>'; return; }
    curReaderDocData=dc;
    renderDocPanel(dc);
    markDocumentSeen(id);
  }).catch(()=>{ panel.innerHTML='<p class=hint>문서 로드 실패.</p>'; });
}
// 한 문서(article)에 속한 노드 — dc.nodes(서버 실시간 DB 조회) 우선, allNodes fallback.
function docNodes(docId, dc){
  if(dc && Array.isArray(dc.nodes) && dc.nodes.length > 0){
    const out = dc.nodes.map(n => {
      const existing = (allNodes && typeof allNodes.get === 'function') ? allNodes.get(n.id) : null;
      return {
        id: n.id,
        label: n.label || n.name || n.id,
        group: n.group || n.type || 'Concept',
        degree: existing ? (existing.degree || 0) : 0,
        sources: existing ? (existing.sources || [docId]) : [docId],
        obs: n.observations || (existing ? existing.obs : []),
      };
    });
    out.sort((a,b)=> (b.degree||0)-(a.degree||0));
    return out;
  }
  const out=[]; if(!allNodes) return out;
  allNodes.forEach(n=>{ if((n.sources||[]).includes(docId)) out.push(n); });
  out.sort((a,b)=> (b.degree||0)-(a.degree||0));   // 중심성 높은 노드부터(핵심이 위로)
  return out;
}
function renderDocPanel(dc){
  curReaderDocData=dc;
  let h='<h2>'+esc(dc.title)+' <small>'+esc(dc.source_type||'')+'</small></h2>';
  h+=docMetaHtml(dc);
  h+=extraSourcesHtml(dc);
  const directive = (dc.directive || (dc.meta && dc.meta.directive) || '').trim();
  if(directive){
    h+='<div style="margin:.4em 0 .6em;padding:6px 8px;background:var(--card-bg);border:1px solid var(--border);border-radius:5px;font-size:12px"><b style="color:var(--accent2)">🎯 초점:</b> '+esc(directive)+'</div>';
  }
  // 숨기기 — 상세 패널의 FTS 스타일 체크박스로(사용자 요구).
  if(canWrite()){
    h+='<div class=dochide-row><label class=dochide-label>'+
      '<input type="checkbox" id="panelhidechk" '+(dc.hidden===1?'checked':'')+
      ' onchange="panelToggleHide(\''+dc.id+'\',this.checked)">'+
      '<span id="panelhidelabel">'+(dc.hidden===1?'🙈 숨김 처리됨':'목록에서 숨기기')+'</span>'+
      '</label></div>';
  }
  if(dc.summary) h+='<h3>요약</h3><div class=synth>'+esc(dc.summary)+'</div>';
  // 이 문서의 노드 버튼 — 요약 바로 아래(피드백). 누르면 그래프에서 그 노드로 이동(nav).
  const ns=docNodes(dc.id, dc);
  h+='<h3>이 문서의 지식 노드 ('+ns.length+')</h3>';
  if(ns.length){ h+='<div class=nodebtns>'+ ns.map(n=>{
      const c=TYPE_COLORS[n.group]||'#8b949e';
      return '<button class=nodebtn title="'+esc(n.group||'')+'" onmouseenter="peekNode(event,\''+n.id+'\')" '+
        'onmouseleave="leaveNode()" onclick="focusNode(\''+n.id+'\')">'+
        '<i style="background:'+c+'"></i>'+esc(n.label)+'</button>'; }).join('')+'</div>';
  } else { h+='<p class=al>이 문서에서 추출된 노드가 없습니다.</p>'; }
  if(!dc.summary && !dc.detail) h+='<p class=al>문서에 요약/상세 내용이 없습니다.</p>';
  panel.innerHTML=h;

  if(dc && Array.isArray(dc.nodes) && dc.nodes.length > 0){
    const missing = allNodes && typeof allNodes.get === 'function' && dc.nodes.some(n => !allNodes.get(n.id));
    if(missing && typeof refreshGraph === 'function'){
      refreshGraph();
    }
  }
}
// 노드 버튼 클릭 → 그래프에서 그 노드로 카메라 이동 + 선택 + 우측은 노드 상세로 전환.
// activeDoc 은 유지 → 노드 상세 상단의 '← 문서로' 로 문서 패널에 즉시 복귀 가능.
function focusNode(id, pushHist=true){
  selectedNodeId=id;
  loadNode(id);
  setCenterView('graph');
  revealWorkspace('graph', false, pushHist);
  if(pushHist) pushAppHistory({ pane: 'graph', nodeId: id, modal: (compactMQ.matches || mobileMQ.matches) ? 'drawer' : null });
  if(net && !isDraggingNode){
    clearTimeout(settleTimer);
    net.setOptions({physics:false});
  }
  requestAnimationFrame(()=>{
    if(net){
      relayout(true);
      net.selectNodes([id]);
      net.focus(id,{scale:1.3,animation:graphAnimation(true)});
    }
  });
}

// --- 종합 수집(synthSet) — inspect(클릭)와 분리 ---
function toggleSynth(id){ if(!canWrite()) return; if(synthSet.has(id)) synthSet.delete(id); else synthSet.add(id); renderChips(); }
function addToSynth(id){ if(!canWrite()) return; synthSet.add(id); renderChips(); if(id===selectedNodeId) loadNode(id); }
function renderChips(){
  const box=document.getElementById('synthchips');
  box.innerHTML=[...synthSet].map(id=>{ const n=allNodes&&allNodes.get(id);
    return '<span class=chip onclick="toggleSynth(\''+id+'\')" title="제거">'+esc(n?n.label:id)+' ✕</span>'; }).join('');
  const sb=document.getElementById('synthbtn');
  if(sb){
    sb.innerHTML='<span class="btn-icon">🧩</span> <span class="btn-label">종합 ('+synthSet.size+')</span>';
    sb.title='종합 ('+synthSet.size+')';
  }
}

// --- 검색: 즉시 라벨 매칭(기본) vs 의미검색 버튼(체크 시) ---
// 검색 결과(라벨·의미)를 한눈에 — 선택 노드들이 모두 화면에 들어오게 zoom/위치 조절.
// 단일 결과는 fit 이 과확대되므로 focus(고정 scale). 여러 결과는 fit({nodes})로 전부 보이게.
// degree 슬라이더로 숨겨진 매치는 제외(보이는 것만 카메라 대상; selectDoc 과 동일 방식).
function fitToMatches(ids){
  if(!net || !ids.length) return;
  net.selectNodes(ids);
  const vis = ids.filter(id=>{ const n=allNodes && allNodes.get(id); return n && !n.hidden; });
  if(!vis.length) return;
  if(vis.length===1) net.focus(vis[0],{scale:1.1,animation:graphAnimation(true)});
  else net.fit({nodes:vis, animation:graphAnimation(true)});
}
// 검색 결과를 '점차 뭉치게' — physics 를 켠 채로 보이지 않는 중앙 앵커를 쓴다.
//   · 매칭 노드 → 중앙 앵커에 spring 엣지(인력): 가운데로 끌려와 한 덩어리로 모인다.
//   · 앵커는 큰 mass·고정 → barnesHut 반발이 *모든* 노드를 밀어내지만, 매칭은 spring 이
//     반발을 이겨 중앙에 붙들리고, 비매칭은 반발만 받아 중앙에서 바깥으로 밀려난다.
// 위치를 강제로 옮기지 않으므로 physics ON 유지(드래그·관성 그대로). 검색 해제 시 앵커와
// 엣지만 빼면 물리가 원래 레이아웃으로 되돌린다(unclusterEdges).
const ANCHOR_MASS=60;   // 앵커 반발 세기(비매칭 밀어냄). ↑ 강하게 밀어냄
const CENTER_LEN=55;    // 매칭~중앙 spring 길이. ↓ 가운데로 더 바싹
function clusterMatches(ids, done){
  unclusterEdges();
  const vs=(ids||[]).filter(id=>{ const n=allNodes&&allNodes.get(id); return n && !n.hidden; });
  if(!net || !allEdges || vs.length<2){ if(done) done(); return; }
  net.setOptions({physics:true});
  const c=net.getViewPosition();   // 현재 화면 중앙 좌표에 앵커를 둔다
  allNodes.add({id:'cl_anchor', x:c.x, y:c.y, fixed:true, physics:true, hidden:true,
                mass:ANCHOR_MASS, label:'', shape:'dot', size:1});
  clusterAnchor='cl_anchor';
  const eids=[];
  const newEdges=vs.map((id,i)=>{
    const eid='cl_'+i; eids.push(eid);
    return {id:eid, from:'cl_anchor', to:id, color:{opacity:0}, width:0, length:CENTER_LEN,
            physics:true, smooth:false};
  });
  allEdges.add(newEdges);
  clusterEdges=eids;
  // 물리가 뭉치는 동안 기다렸다 한눈에 fit(여러 결과면 fit, 1개면 focus).
  if(done) setTimeout(done, 900);
}
// 검색 해제 시 임시 앵커·spring 엣지 제거 → 물리가 원래 레이아웃으로 자연 복귀.
function unclusterEdges(){
  if(clusterEdges && allEdges && clusterEdges.length){
    try{ allEdges.remove(clusterEdges); }catch(e){}
  }
  if(clusterAnchor && allNodes){ try{ allNodes.remove(clusterAnchor); }catch(e){} }
  clusterEdges=null; clusterAnchor=null;
  if(net && !isDraggingNode) net.setOptions({physics:false});
}
// 우측 '이 문서의 노드' 버튼 hover — 요약 팝업(1.5초 머물 시). 클릭 시 focusNode 로 카메라 이동.
function peekNode(ev, id){
  clearTimeout(hoverTimer);
  if(!canShowNodePop(id)) return;
  const x=ev.clientX, y=ev.clientY;
  hoverTimer=setTimeout(()=>showNodePop(id, x, y), 1500);
}
function leaveNode(){ clearTimeout(hoverTimer); hideNodePop(); }
// 타이핑마다 즉시 검색하면 매 키 입력에 강조+물리 클러스터링이 돌아 무겁고 출렁인다.
// 디바운스: 입력이 멈춘 뒤(350ms) 한 번만 실행. 단 검색창을 비우면 즉시 해제(반응성).
function onSearchInput(v){
  const sem=document.getElementById('sem');
  const semchk=document.getElementById('semchk');
  if((sem && sem.checked) || (semchk && semchk.checked)) return;   // 고급검색(FTS/Semantic)은 엔터로만
  cancelServerSearch();
  currentSearchSeq++;
  clearTimeout(searchDebounce);
  if(!v.trim()){ hl(''); return; }                     // 비우기 → 즉시 강조/클러스터 해제
  searchDebounce=setTimeout(()=>hl(v), 550);
}
// 라벨 검색: 매치 강조 + 나머지 dim(문서 선택과 동일 방식). 색칠 대신 highlightSet+applyView.
function hl(q){
  if(!allNodes) return;
  cancelServerSearch();
  currentSearchSeq++;
  q=q.trim().toLowerCase();
  if(!q){ highlightSet=null; applyView(); unclusterEdges();
    if(net){ net.unselectAll(); net.fit({animation:graphAnimation(true)}); } return; }
  if(typeof window.gtag === 'function'){
    try{ window.gtag('event', 'search', { search_term: q, search_mode: 'label' }); }catch(_){}
  }
  const matches=[];
  allNodes.forEach(n=>{ if(n.label.toLowerCase().includes(q)) matches.push(n.id); });
  highlightSet = new Set(matches);
  applyView();
  clusterMatches(matches, ()=>fitToMatches(matches));   // 결과를 점차 뭉치게 한 뒤 한눈에 fit
}
function toggleAdvSearch(force){
  const pane = document.getElementById('advsearchpane');
  const btn = document.getElementById('advsearchbtn');
  if(!pane || !btn) return;
  const isHidden = force !== undefined ? !force : !pane.hidden;
  pane.hidden = isHidden;
  pane.setAttribute('aria-hidden', String(isHidden));
  btn.setAttribute('aria-expanded', String(!isHidden));
  btn.classList.toggle('active', !isHidden);
}
const semEl=document.getElementById('sem');
const semchkEl=document.getElementById('semchk');
if(semEl){
  semEl.addEventListener('change',e=>{
    if(e.target.checked && semchkEl) semchkEl.checked = false;
    cancelServerSearch();
    currentSearchSeq++;
    if(e.target.checked) hl('');
  });
}
if(semchkEl){
  semchkEl.addEventListener('change',e=>{
    if(e.target.checked && semEl) semEl.checked = false;
    cancelServerSearch();
    currentSearchSeq++;
    if(e.target.checked) hl('');
  });
}
const docqEl=document.getElementById('docq');
if(docqEl){
  docqEl.addEventListener('keydown',e=>{
    if(e.key!=='Enter') return;
    const sem=document.getElementById('sem');
    const semchk=document.getElementById('semchk');
    if((sem && sem.checked) || (semchk && semchk.checked)){ doSemantic(); }
  });
}
const qEl=document.getElementById('q');
if(qEl){
  qEl.addEventListener('keydown',e=>{
    if(e.key!=='Enter') return;
    const sem=document.getElementById('sem');
    const semchk=document.getElementById('semchk');
    if((sem && sem.checked) || (semchk && semchk.checked)){ doSemantic(); }
    else { cancelServerSearch(); currentSearchSeq++; clearTimeout(searchDebounce); hl(e.target.value);
           revealWorkspace('graph');
           if(net){ const m=net.getSelectedNodes(); if(m.length) loadNode(m[0]); } }
  });
  qEl.addEventListener('focus', e=> e.target.select());
}
function doSemantic(){
  revealWorkspace('graph');
  const docqVal = (document.getElementById('docq') ? document.getElementById('docq').value : '').trim();
  const qVal = (document.getElementById('q') ? document.getElementById('q').value : '').trim();
  const qv = docqVal || qVal;
  semanticSearch(qv);
}

// --- 인증 상태 표시 ---
// 첫 페인트는 unknown/read-only이며, /whoami가 exact owner를 확인한 경우에만 쓰기 UI를
// 승격한다. 버튼 숨김과 별개로 모든 쓰기 함수도 canWrite()를 확인한다.
function updateSearchModeUI(){
  const sem=document.getElementById('sem');
  const kind=document.getElementById('searchkind');
  const semchk=document.getElementById('semchk');
  const sembadge=document.getElementById('sembadge');
  const semwrap=document.getElementById('semantic-opt-wrap');
  const unknown=AUTH_SCOPE==='unknown';
  const isAnon=AUTH_SCOPE==='anonymous';

  if(sem){
    sem.disabled=unknown;
    if(unknown) sem.checked=false;
  }
  if(kind) kind.textContent = 'Full-Text Search';

  if(semchk){
    semchk.disabled = unknown || isAnon;
    if(unknown || isAnon) semchk.checked = false;
  }
  if(sembadge){
    sembadge.style.display = isAnon ? '' : 'none';
  }
  const ftswrap=document.getElementById('fts-opt-wrap');
  if(ftswrap){
    ftswrap.title = 'SQLite FTS5 기반 BM25';
  }
  if(semwrap){
    semwrap.style.opacity = isAnon ? '0.65' : '1';
    semwrap.title = isAnon
      ? 'FTS + AI RRF 기반 벡터 하이브리드 (인증 필요)'
      : 'FTS + AI RRF 기반 벡터 하이브리드';
  }
}
function setAccessScope(scope, reason){
  AUTH_SCOPE = ['owner','readonly','anonymous'].includes(scope) ? scope : 'unknown';
  READONLY = !canWrite();
  document.body.dataset.authScope=AUTH_SCOPE;
  document.body.classList.toggle('ro', READONLY);
  const label=document.getElementById('authstate');
  if(label) label.textContent =
    AUTH_SCOPE==='owner' ? '🔒 인증됨' :
    AUTH_SCOPE==='readonly' ? '👁️ 읽기전용' :
    AUTH_SCOPE==='anonymous' ? '👁️ 익명 읽기전용' :
    reason==='expired' ? '🔓 쓰기 세션 만료 — /web 재접속' : '⚠️ 권한 확인 실패';
  if(!canWrite()){
    synthSet.clear();
    showHidden=false;
    renderChips();
    // owner 전용 동적 UI를 네트워크 재조회보다 먼저 제거한다. 뒤늦게 도착하는
    // node/document 응답도 render 시점의 canWrite()로 다시 판정한다.
    panel.innerHTML=defaultHint();
  }
  updateSearchModeUI();
  renderDocs();
  if(selectedNodeId) loadNode(selectedNodeId);
  else if(activeDoc) loadDocPanel(activeDoc);
  else panel.innerHTML=defaultHint();
}
function expireWriteAccess(){ setAccessScope('unknown','expired'); }
async function synth(){
  if(!canWrite()) return;
  const ids=[...synthSet];
  if(!ids.length){ alert('종합할 노드를 먼저 모으세요 — Ctrl+클릭 또는 상세의 "➕ 종합에 추가".'); return; }
  panel.innerHTML='<p class=hint>🧩 '+ids.length+'개 노드 종합 중… (LLM 호출)</p>';
  openDetailPane();
  // 인증은 claire_session 쿠키(/web 진입)로 자동 전송됨 — 별도 헤더 불필요.
  fetch('synthesize',{method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({node_ids:ids})})
   .then(r=> { if(r.status===401||r.status===404){ expireWriteAccess(); return {error:'세션 만료 — 텔레그램 /web 으로 다시 접속하세요'}; } return r.json(); })
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
  cancelServerSearch();
  const seq = ++currentSearchSeq;
  let abortController = null;
  if(typeof AbortController !== 'undefined'){
    abortController = new AbortController();
    currentSearchAbort = abortController;
  }
  const semchk=document.getElementById('semchk');
  const isSemantic = semchk && semchk.checked && AUTH_SCOPE !== 'anonymous';
  const searchMode = isSemantic ? 'hybrid' : 'fts';
  if(typeof window.gtag === 'function'){
    try{ window.gtag('event', 'search', { search_term: q, search_mode: searchMode }); }catch(_){}
  }
  const requestedMode = isSemantic ? 'Semantic Search' : 'Full-Text Search';
  const statEl = document.getElementById('stat');
  if(statEl) statEl.textContent='🔎 '+requestedMode+' 중…';
  let r;
  try{
    const reqOpts = {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({query:q, summarize:false, limit:12, mode:searchMode})
    };
    if(abortController) reqOpts.signal = abortController.signal;
    r=await fetch('search', reqOpts);
  }
  catch(e){
    if(abortController && abortController.signal.aborted) return;
    if(seq !== currentSearchSeq) return;
    if(statEl) statEl.textContent='검색 실패';
    return;
  }
  if(seq !== currentSearchSeq) return;
  if(r.status===401||r.status===404){ expireWriteAccess(); if(statEl) statEl.textContent='세션 만료 — /web 으로 재접속'; return; }
  if(r.status===429){ if(statEl) statEl.textContent='검색 요청이 많습니다 — 잠시 후 다시 시도하세요'; return; }
  let d={}; try{ d=await r.json(); }catch(_){}
  if(seq !== currentSearchSeq) return;
  if(!r.ok){ if(statEl) statEl.textContent='검색 실패: HTTP '+r.status; return; }
  const ids=(d.hits||[]).map(h=>h.id).filter(Boolean);
  highlightSet = new Set(ids);   // 라벨 검색과 동일하게 강조+dim 방식 사용
  applyView();
  clusterMatches(ids, ()=>fitToMatches(ids));   // 의미검색 결과도 점차 뭉치게 + 한눈에 fit
  const actualMode=(d.mode==='fts'||searchMode==='fts')?'Full-Text Search':'Semantic Search';
  if(statEl){
    statEl.textContent=ids.length
      ? '🔎 '+actualMode+': '+ids.length+'개'
      : '🔎 '+actualMode+': 결과 없음';
  }
}

// --- 고유 상태 및 안내 배너 시스템 (ClaireStatusBanner) ---
const ClaireStatusBanner = (function(){
  const presets = {
    format_mismatch: {
      level: 'warning',
      icon: '⚠️',
      title: '렌더링 포맷 불일치',
      render: function(data){
        const cfg = (data.configured || 'adoc').toUpperCase();
        const other = (data.configured === 'adoc' ? 'md' : 'adoc').toUpperCase();
        const count = data.mismatched || data.mismatched_docs || 0;
        return '.env 설정은 <b>' + cfg + '</b>이나, DB 문서 중 <b>' + count + '개</b>가 <b>' + other + '</b> 포맷입니다. <code>./cb-manuscript app format-migrate --apply</code> 실행이 필요합니다.';
      }
    },
    format_missing: {
      level: 'info',
      icon: 'ℹ️',
      title: '가독 렌더링 미생성',
      render: function(data){
        const count = data.missing_detail_docs || 0;
        return '전체 문서 중 <b>' + count + '개</b>의 상세(detail)가 아직 생성되지 않았습니다. <code>./cb-manuscript app format-migrate --apply</code> 실행으로 자동 생성할 수 있습니다.';
      }
    },
    format_ok: {
      level: 'success',
      icon: '✅',
      title: '포맷 동기화 완료',
      render: function(data){
        const cfg = (data.configured || 'adoc').toUpperCase();
        const total = data.total_docs || data.matching_docs || 0;
        return '모든 문서(' + total + '건)가 목표 포맷(<b>' + cfg + '</b>)으로 정상 렌더링되고 있습니다.';
      }
    },
    readonly_mode: {
      level: 'info',
      icon: '🔒',
      title: '읽기 전용 모드',
      render: function(){
        return '현재 게스트(읽기 전용) 권한으로 접속 중입니다. 지식그래프 탐색 및 검색이 가능합니다.';
      }
    },
    network_error: {
      level: 'error',
      icon: '⚡',
      title: '연결 확인 필요',
      render: function(data){
        return (data && data.message) || '서버와의 통신이 원활하지 않습니다. 네트워크 연결을 확인해 주세요.';
      },
      actionLabel: '🔄 새로고침',
      action: function(){
        if (typeof window !== 'undefined' && window.location && window.location.reload) {
          window.location.reload();
        }
      }
    },
    refresh_pending: {
      level: 'warning',
      icon: '⏳',
      title: '데이터 갱신 대기',
      render: function(data){
        const count = (data && data.count) || '일부';
        return '문서 갱신 작업(' + count + '건)이 대기열에 등록되어 백그라운드 처리 중입니다.';
      }
    },
    custom_notice: {
      level: 'info',
      icon: '📢',
      title: '안내',
      render: function(data){
        return (data && data.message) || '';
      }
    }
  };

  let activeStatus = null;
  let activeData = null;

  return {
    presets: presets,
    register: function(key, def){
      presets[key] = def;
    },
    show: function(presetKey, data, options){
      const banner = document.getElementById('format-warn-banner');
      const text = document.getElementById('format-warn-text');
      const badge = document.getElementById('format-warn-badge');
      const iconEl = document.getElementById('format-warn-icon');
      const titleEl = document.getElementById('format-warn-title');
      const actBtn = document.getElementById('format-warn-actbtn');
      if(!banner || !text) return;

      const preset = presets[presetKey] || presets.custom_notice;
      const opts = Object.assign({}, preset, options || {});
      const pData = data || {};
      activeStatus = presetKey;
      activeData = pData;

      banner.className = 'status-banner banner-' + (opts.level || 'warning');
      if (iconEl) iconEl.textContent = opts.icon || 'ℹ️';
      if (titleEl) titleEl.textContent = opts.title || '안내';
      if (badge && opts.title === '') badge.style.display = 'none';
      else if (badge) badge.style.display = 'inline-flex';

      const html = typeof opts.render === 'function' ? opts.render(pData) : (opts.message || '');
      text.innerHTML = html;

      if (actBtn) {
        if (opts.actionLabel && typeof opts.action === 'function') {
          actBtn.textContent = opts.actionLabel;
          actBtn.style.display = 'inline-block';
        } else {
          actBtn.style.display = 'none';
        }
      }

      banner.style.display = 'flex';
    },
    hide: function(){
      const banner = document.getElementById('format-warn-banner');
      if (banner) banner.style.display = 'none';
      activeStatus = null;
      activeData = null;
    },
    handleAction: function(){
      if (!activeStatus) return;
      const preset = presets[activeStatus];
      const actBtn = document.getElementById('format-warn-actbtn');
      if (preset && typeof preset.action === 'function') {
        preset.action(activeData, actBtn);
      }
    },
    getStatus: function(){
      return { status: activeStatus, data: activeData };
    }
  };
})();

// documents와 /whoami를 병렬로 읽되, scope가 확정되기 전 렌더는 항상 read-only다.
syncThemeBtn();   // 저장된 테마에 맞춰 🌙/🌞 라벨 동기화(테마 자체는 head 인라인에서 선적용)
fetch('documents').then(r=>{ if(!r.ok) throw new Error('documents fetch failed: HTTP '+r.status); return r.json(); }).then(d=>{
  allDocs=(d && d.documents)||[];
  renderDocs();
  if(d && d.format_status){
    const fs=d.format_status;
    if(fs.needs_migration){
      if((fs.mismatched || fs.mismatched_docs || 0) > 0){
        ClaireStatusBanner.show('format_mismatch', fs);
      } else if((fs.missing_detail_docs || 0) > 0){
        ClaireStatusBanner.show('format_missing', fs);
      }
    }
  }
}).catch(e=>{
  allDocs=[];
  const dl=document.getElementById('doclist');
  if(dl) dl.innerHTML=doclistToolbarHtml()+'<p class="hint" style="padding:10px">문서 로드 실패</p>';
});
fetch('whoami').then(r=>{ if(!r.ok) throw new Error('whoami failed'); return r.json(); }).then(d=>{
  setAccessScope(d.scope);
}).catch(()=>{ setAccessScope('unknown','failed'); });

// --- 브라우저 히스토리 (뒤로가기 / 앞으로가기) 내비게이션 핸들러 ---
window.addEventListener('popstate', e => {
  isPoppingHistory = true;
  try{
    const state = e.state || { pane: 'graph', modal: null, docId: null, nodeId: null };
    lastPushedHistory = state;
    const r = document.getElementById('reader');
    const gdm = document.getElementById('graphdocmenu');

    // 1. 모달 닫기
    if(state.modal !== 'reader' && r && r.classList.contains('open')){
      closeReader(false, false);
    }
    if(state.modal !== 'drawer' && (drawerOpen || detailOpen)){
      closeDrawer(false, false);
    }
    if(state.modal !== 'graphdocmenu' && gdm && !gdm.hidden){
      closeGraphDocPicker(true, false);
    }

    // 2. 작업 영역(pane) 전환
    if(state.pane && state.pane !== activePane){
      revealWorkspace(state.pane, false, false);
    }

    // 3. 모달 열기
    if(state.modal === 'reader' && state.docId){
      if(!r || !r.classList.contains('open') || curReaderDoc !== state.docId){
        openReader(state.docId, false);
      }
    } else if(state.modal === 'drawer'){
      if(!drawerOpen && !detailOpen){
        openDrawer(false);
      }
    } else if(state.modal === 'graphdocmenu'){
      if(gdm && gdm.hidden){
        openGraphDocPicker(false);
      }
    }

    // 4. 노드 포커스
    if(state.pane === 'graph' && state.nodeId && state.nodeId !== selectedNodeId){
      focusNode(state.nodeId, false);
    }
  }finally{
    isPoppingHistory = false;
  }
});

// 초기 베이스 히스토리 엔트리 등록
replaceAppHistory({ pane: activePane || 'graph', modal: getActiveModalName() });

// 읽기전용 디버그 핸들(테스트/Playwright 검증용 — closure 상태 관찰). 부작용 없음.
window.claireDebug = {
  get sel(){ return net ? net.getSelectedNodes() : []; },
  get highlight(){ return highlightSet ? [...highlightSet] : null; },
  get selected(){ return selectedNodeId; },
  get activeDoc(){ return activeDoc; },
  get synth(){ return [...synthSet]; },
  get authScope(){ return AUTH_SCOPE; },
  get canWrite(){ return canWrite(); },
  get statusBanner(){ return ClaireStatusBanner; },
  get activeBannerStatus(){ return ClaireStatusBanner.getStatus(); },
  positions(ids){ return net ? net.getPositions(ids) : {}; },
  visibleNodePoints(){
    if(!net || !allNodes) return [];
    return allNodes.getIds().filter(id=>{
      const n=allNodes.get(id);
      return n && !n.hidden && !(typeof id==='string' && id.indexOf('cl_')===0);
    }).map(id=>({id:id,...net.canvasToDOM(net.getPosition(id))}));
  },
  get scale(){ return net ? net.getScale() : null; },
  get viewpos(){ return net ? net.getViewPosition() : null; },
  get clustered(){ return clusterEdges; },
  get activePane(){ return activePane; },
  get detailOpen(){ return detailOpen || drawerOpen; },
  get drawerOpen(){ return drawerOpen; },
  get toolsOpen(){ return drawerOpen || detailOpen; },
  get readerOpen(){ return document.getElementById('reader').classList.contains('open'); },
  get docSearchActive(){ return docSearchActive; },
  get stabilized(){ return graphStabilized; },
  get detailCompact(){ return document.body.classList.contains('detail-compact'); },
  toggleDetailCompact: toggleDetailCompact,
  docWithMostNodes: docWithMostNodes,
  getRecentDocId: getRecentDocId,
  get lastSelectedDocId(){ return lastSelectedDocId; },
  get sourceBaseUrl(){ return '__SOURCE_BASE_URL__'; },
  get githubRepository(){ return '__GITHUB_REPOSITORY__'; },
  get sorcerer(){ return '__SORCERER__'; },
  get owner(){ return '__SORCERER__'; },
  get knowledgeManager(){ return '__SORCERER__'; },
};
