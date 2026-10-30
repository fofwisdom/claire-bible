/**
 * Claire Bible - Knowledge Graph Workspace Application
 * Controls the ClaireGraph network (static/js/graph/), search, clustering, degree filtering, tabs, and workspace state.
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
// 그래프 캔버스는 CSS 변수가 안 닿아 테마별 색을 JS 로 직접 갱신한다(노드 글자/엣지/테두리).
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
let detailReturnFocus=null, docSearchActive=false, serverSearchResults=null;
let graphCamera = null, preservingGraphCamera = false, netBusy = false;
let isDraggingNode = false;
let lastNetSize = {w:0, h:0};
let allTypes = [], allRelTypes = [], allDocs = [];
let net = null, allNodes = null, allEdges = null;
let curMinDeg = 0, activeDoc = null, highlightSet = null, selectedNodeId = null, hoverTimer = null;
// 노드(그래프·'이 문서의 노드' 버튼)에 커서를 이만큼 올려 두면 요약 팝업을 띄운다.
const NODE_POP_DELAY_MS = 1000;
const HOME_TITLE = 'Claire Bible — 지식 그래프';
let activeJobs = [];
let selectedJobId = null;
let isolatedNodeId = null, activeHoverEdges = null, activeHoverNodes = null;
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

function esc(s){return (s||'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}
function isWebUrl(u){ return !!u && /^https?:\/\//i.test(String(u).trim()); }

// --- 지식 테마 (Multi-Database Themes) 상태 및 전역 fetch 인터셉터 ---
function isMultiThemeEnabled(){
  return Boolean(window.__CLAIRE_CONFIG && window.__CLAIRE_CONFIG.themeMode === 'multi');
}

let availableThemes = (function(){
  if(window.__CLAIRE_CONFIG && Array.isArray(window.__CLAIRE_CONFIG.themes) && window.__CLAIRE_CONFIG.themes.length){
    return window.__CLAIRE_CONFIG.themes;
  }
  return [{id: 0, seq: 0, label: '기본 지식베이스', description: '일반 수집 자료 및 기본 지식', icon: '📚', is_default: true}];
})();

let activeThemeId = (function(){
  if(!isMultiThemeEnabled()){
    return 0;
  }
  try{
    const currHost = window.location.hostname.toLowerCase();
    if(availableThemes && availableThemes.length){
      const matched = availableThemes.find(t => t.fqdn && t.fqdn.toLowerCase() === currHost);
      if(matched){
        return matched.id;
      }
    }
    const sp = new URLSearchParams(window.location.search);
    const q = sp.get('theme');
    if(q !== null && q !== ''){
      const val = parseInt(q, 10);
      return isNaN(val) ? 0 : val;
    }
    const saved = localStorage.getItem('claireKnowledgeTheme');
    if(saved !== null && saved !== ''){
      const val = parseInt(saved, 10);
      return isNaN(val) ? 0 : val;
    }
  }catch(_){}
  return 0;
})();

(function(){
  const _origFetch = window.fetch;
  window.fetch = function(resource, init){
    init = init || {};
    let url = typeof resource === 'string' ? resource : (resource && resource.url ? resource.url : '');
    const isRelative = typeof url === 'string' && !url.startsWith('http://') && !url.startsWith('https://') && !url.startsWith('//');
    if(isRelative || (url && url.startsWith(window.location.origin))){
      if(isMultiThemeEnabled() && activeThemeId !== null && activeThemeId !== undefined){
        let headers = init.headers;
        if(headers instanceof Headers){
          if(!headers.has('X-Claire-Theme')){
            headers.set('X-Claire-Theme', String(activeThemeId));
          }
        } else if(Array.isArray(headers)){
          const hasTheme = headers.some(([k]) => k.toLowerCase() === 'x-claire-theme');
          if(!hasTheme){
            headers.push(['X-Claire-Theme', String(activeThemeId)]);
          }
        } else {
          headers = Object.assign({}, headers);
          const hasTheme = Object.keys(headers).some(k => k.toLowerCase() === 'x-claire-theme');
          if(!hasTheme){
            headers['X-Claire-Theme'] = String(activeThemeId);
          }
          init.headers = headers;
        }
      }
    }
    return _origFetch.call(this, resource, init);
  };
})();

async function fetchThemes(){
  if(!isMultiThemeEnabled()){
    // 싱글 테마 모드: 추가 네트워크 왕복(RTT) 완전 배제 (0 RTT & 0 CLS)
    renderThemeSelector();
    return;
  }
  // 멀티 모드: 서버에서 인라인된 테마가 있으면 우선 즉시 렌더링
  if(availableThemes && availableThemes.length){
    renderThemeSelector();
  }
  try{
    const r = await fetch('themes');
    if(!r.ok) return;
    const data = await r.json();
    if(data.themes && data.themes.length){
      availableThemes = data.themes;
      const currHost = window.location.hostname.toLowerCase();
      const matched = availableThemes.find(t => t.fqdn && t.fqdn.toLowerCase() === currHost);
      if(matched){
        activeThemeId = matched.id;
      }
      renderThemeSelector();
    }
  }catch(e){
    console.warn('Failed to fetch themes:', e);
  }
}

function renderThemeSelector(){
  const sel = document.getElementById('theme-select');
  const iconEl = document.getElementById('theme-curr-icon');
  const wrap = document.getElementById('theme-picker-wrap');
  const ctrl = document.getElementById('theme-select-control');
  const plainDisplay = document.getElementById('theme-plain-display');
  const plainIcon = document.getElementById('theme-plain-icon');
  const plainLabel = document.getElementById('theme-plain-label');
  if(!sel) return;

  if(!availableThemes || !availableThemes.length){
    availableThemes = [{id: 0, seq: 0, label: '기본 지식베이스', description: '일반 수집 자료 및 기본 지식', icon: '📚', is_default: true}];
  }

  const currHost = window.location.hostname.toLowerCase();
  const current = availableThemes.find(t => t.id === activeThemeId) || availableThemes[0];
  if(current){
    activeThemeId = current.id;
  }

  // FQDN 전용 도메인 접속 여부 판별 (해당 테마 FQDN 일치)
  const isFqdnAccess = Boolean(current && current.fqdn && current.fqdn.toLowerCase() === currHost);

  if(isFqdnAccess){
    // FQDN 접속 시: 드롭다운 상자 숨기고 브랜드 우측 순수 텍스트만 표시
    if(wrap){
      wrap.style.display = 'inline-flex';
      wrap.title = '지식 테마: ' + (current.label || '');
    }
    if(ctrl) ctrl.style.display = 'none';
    if(plainDisplay) plainDisplay.style.display = 'inline-flex';
    if(plainIcon) plainIcon.textContent = current.icon || '📚';
    if(plainLabel) plainLabel.textContent = current.label || '기본 지식베이스';
  } else {
    // 일반 접속 시: 싱글 테마 모드이거나 테마가 1개뿐인 경우 숨김, 멀티 테마일 때 드롭다운 박스 표시
    if(wrap){
      wrap.style.display = (isMultiThemeEnabled() && availableThemes.length > 1) ? 'inline-flex' : 'none';
      wrap.title = '지식 테마 선택 (데이터베이스 전환)';
    }
    if(ctrl) ctrl.style.display = 'inline-flex';
    if(plainDisplay) plainDisplay.style.display = 'none';
    if(iconEl && current){
      iconEl.textContent = current.icon || '📚';
    }
  }

  sel.innerHTML = availableThemes.map(t => {
    const label = esc(t.label);
    const lockStr = t.is_public === false ? ' 🔒 (비공개)' : '';
    return `<option value="${t.id}" ${t.id === activeThemeId ? 'selected' : ''}>${label}${lockStr}</option>`;
  }).join('');
  updateDrawerManager(current);
  syncPresentationFeatureUI();
}

function updateDrawerManager(currentTheme){
  const el = document.getElementById('drawermanager');
  if(!el) return;
  const defaultMgr = (window.__CLAIRE_CONFIG && window.__CLAIRE_CONFIG.sorcerer) || 'owner';
  const rawMgr = currentTheme ? (currentTheme.sorcerer || currentTheme.knowledge_manager || '').trim() : '';
  const mgr = rawMgr || (currentTheme && currentTheme.effective_sorcerer) || defaultMgr;
  el.textContent = '지식 관리자: ' + mgr;
  el.title = '지식 관리자: ' + mgr;
}

function isPresentationEnabledForActiveTheme(){
  if(!availableThemes || !availableThemes.length) return false;
  const current = availableThemes.find(t => t.id === activeThemeId) || availableThemes[0];
  return Boolean(current && current.presentation_enabled === true);
}

function syncPresentationFeatureUI(){
  const isPresEnabled = isPresentationEnabledForActiveTheme();
  const presTab = document.getElementById('centertab-presentation');
  if(presTab){
    presTab.style.display = isPresEnabled ? 'inline-flex' : 'none';
  }
  if(typeof updatePresentationView === 'function' && typeof centerView !== 'undefined' && centerView === 'presentation'){
    updatePresentationView();
  }
}

function syncThemeSelectorUI(){
  const sel = document.getElementById('theme-select');
  const iconEl = document.getElementById('theme-curr-icon');
  const plainIcon = document.getElementById('theme-plain-icon');
  const plainLabel = document.getElementById('theme-plain-label');
  if(sel){
    sel.value = String(activeThemeId);
  }
  const current = availableThemes.find(t => t.id === activeThemeId);
  if(current){
    if(iconEl) iconEl.textContent = current.icon || '📚';
    if(plainIcon) plainIcon.textContent = current.icon || '📚';
    if(plainLabel) plainLabel.textContent = current.label || '기본 지식베이스';
    updateDrawerManager(current);
  }
  syncPresentationFeatureUI();
}

function renderThemeOptions(selectedId){
  const themes = (availableThemes && availableThemes.length)
    ? availableThemes
    : [{id: 0, seq: 0, label: '기본 지식베이스', description: '일반 수집 자료 및 기본 지식', icon: '📚'}];
  const activeId = selectedId !== undefined ? selectedId : activeThemeId;
  return themes.map(t => {
    const icon = t.icon || '📚';
    const label = esc(t.label);
    const lockStr = t.is_public === false ? ' 🔒 (비공개)' : '';
    return `<option value="${t.id}" ${t.id === activeId ? 'selected' : ''}>${icon} ${label}${lockStr}</option>`;
  }).join('');
}

async function loadThemeDocuments(){
  try{
    const r = await fetch('documents');
    if(!r.ok) throw new Error('documents fetch failed: HTTP ' + r.status);
    const d = await r.json();
    allDocs = (d && d.documents) || [];
    renderDocs(document.getElementById('docq') ? document.getElementById('docq').value : '');
    if(d && d.format_status){
      window.currentFormatStatus = d.format_status;
      const fs = d.format_status;
      if(fs.needs_migration){
        if((fs.mismatched || fs.mismatched_docs || 0) > 0){
          if(typeof ClaireStatusBanner !== 'undefined') ClaireStatusBanner.show('format_mismatch', fs);
        } else if((fs.missing_detail_docs || 0) > 0){
          if(typeof ClaireStatusBanner !== 'undefined') ClaireStatusBanner.show('format_missing', fs);
        }
      } else {
        if(typeof ClaireStatusBanner !== 'undefined') ClaireStatusBanner.hide();
      }
    }
  }catch(e){
    allDocs = [];
    const dl = document.getElementById('doclist');
    if(dl) dl.innerHTML = (typeof doclistToolbarHtml==='function'?doclistToolbarHtml():'') + '<p class="hint" style="padding:10px">문서 로드 실패</p>';
  }
}

async function reloadThemeData(resetCamera = true){
  try{
    const r = await fetch('graph');
    if(!r.ok) throw new Error('graph fetch failed: HTTP ' + r.status);
    const d = await r.json();
    if(net && allNodes && allEdges){
      const rawNodes = ((d && d.nodes) || []).map(n => ({
        ...n,
        size: nodeRadius(n.degree),
        font: { size: nodeFontSize(n.degree) }
      }));
      const rawEdges = (d && d.edges) || [];
      applyTopologicalStructure(rawNodes, rawEdges);
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
      allTypes = [...new Set(rawNodes.map(n => n.group))].sort();
      allRelTypes = [...new Set(rawEdges.map(e => e.label).filter(Boolean))].sort();
      renderLegend();

      allNodes.clear();
      allEdges.clear();
      if(rawNodes.length) allNodes.add(rawNodes);
      if(rawEdges.length) allEdges.add(rawEdges);
      graphStabilized = !rawNodes.length;
      applyView();
      if(resetCamera){
        setTimeout(()=>{ try{ net.fit({animation: false}); }catch(_){} }, 100);
      }
    }
  }catch(e){
    console.warn('reloadThemeData failed:', e);
  }
}

async function switchKnowledgeTheme(themeId){
  if(!isMultiThemeEnabled()) return;
  const tid = parseInt(themeId, 10);
  if(isNaN(tid)) return;
  if(tid === activeThemeId) return;

  activeThemeId = tid;
  try{ localStorage.setItem('claireKnowledgeTheme', String(tid)); }catch(_){}

  const url = new URL(window.location);
  if(tid === 0){
    url.searchParams.delete('theme');
  } else {
    url.searchParams.set('theme', String(tid));
  }
  window.history.replaceState(window.history.state, '', url.pathname + (url.search ? url.search : '') + url.hash);

  syncThemeSelectorUI();
  await enterHome({reason: 'theme'});

  await Promise.all([
    reloadThemeData(true),
    loadThemeDocuments()
  ]);

  lastStatsSig = null;
  try{
    const r = await fetch('stats');
    if(r.ok){
      const d = await r.json();
      lastStatsSig = [d.documents, d.entities, d.relations].join(':');
    }
  }catch(_){}
}

// --- 웹 표준 History API 기반 모바일 뒤로가기/내비게이션 관리 ---
let isPoppingHistory = false;
let lastPushedHistory = null;

function getActiveModalName(){
  if(mobileMQ.matches && typeof docDrawerOpen !== 'undefined' && docDrawerOpen){
    return 'docdrawer';
  }
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
let edgeLabelsByZoom = false, selectedEdgeIds = new Set(), lastZoomTier = 0;
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
function canIngest(){ return AUTH_SCOPE==='owner' || AUTH_SCOPE==='collaborator'; }
// 함수로 둔 이유: READONLY 는 /whoami 가 비동기로 확정하므로, 호출 시점 기준으로
// 종합 안내 줄을 넣을지 뺄지 판단해야 한다(고정 문자열이면 초기 로드 시점 값에 박제됨).
function defaultHint(){
  const synthLine = canWrite()
    ? '• <b>Ctrl+클릭</b> 또는 상세의 <b>➕ 종합에 추가</b>로 여러 노드를 모아 종합<br>'
    : '';
  return '<p class="hint">노드를 클릭하면 관찰·출처 문서·연결이 표시됩니다.<br><br>'+synthLine+
    '• 다른 노드에 <b>'+(NODE_POP_DELAY_MS/1000)+'초</b> 올리면 마우스 옆에 <b>요약 팝업</b>(더 끌면 출처 문서까지)<br>'+
    '• <b>자료</b>에서 문서를 고르면 그래프가 강조되고, <b>📖</b>로 크게 읽습니다.<br>'+
    '• 상단 <b>⋯ 도구</b>의 <b>🌙/🌞</b>로 라이트·다크를 전환합니다.</p>';
}
if(panel) panel.innerHTML = defaultHint();

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

function applyHoverHighlight(id){
  if(!net || !allEdges || !allNodes || selectedNodeId || isolatedNodeId || pathMode) return;
  try{
    const rawEdges = net.getConnectedEdges(id);
    const rawNodes = net.getConnectedNodes(id);
    if(!rawEdges || !rawEdges.length) return;
    const th = T();

    activeHoverEdges = rawEdges;
    activeHoverNodes = [id, ...(Array.isArray(rawNodes) ? rawNodes : [])];

    const edgeUpdates = rawEdges.map(eid => ({
      id: eid,
      width: 2.2,
      color: { color: th.lit, highlight: th.lit, opacity: 1.0 }
    }));
    allEdges.update(edgeUpdates);

    if(activeHoverNodes && activeHoverNodes.length){
      const nodeUpdates = activeHoverNodes.map(nid => {
        const n = allNodes.get(nid);
        const fs = n ? nodeFontSize(n.degree) : 12;
        return {
          id: nid,
          opacity: 1.0,
          font: { size: fs, color: th.nodeFont }
        };
      });
      allNodes.update(nodeUpdates);
    }
  }catch(_){}
}

// 줌 단계별 기본 엣지 LOD. 강조가 없을 때 기본 엣지는 양끝 노드 타입색 그라디언트(inherit)로
// 그려 별마다 구성 타입의 색이 은은하게 번지게 한다.
function idleEdgeStyle(scale){
  if(scale >= 1.0) return {opacity:0.6, width:1.2};
  if(scale >= 0.4) return {opacity:0.42, width:1.0};
  return {opacity:0.32, width:0.8};
}

function clearHoverHighlight(){
  if(!net || !allEdges || !activeHoverEdges) return;
  // 문서·검색·경로 강조 중에는 맥락별 투명도가 엣지마다 달라 전체를 다시 계산한다.
  if(activeDoc || highlightSet || (pathNodes && pathNodes.size)){
    activeHoverEdges = null;
    activeHoverNodes = null;
    applyView();
    return;
  }
  try{
    const idle = idleEdgeStyle(net.getScale());
    const th = T();

    const edgeResets = activeHoverEdges.map(eid => ({
      id: eid,
      width: idle.width,
      color: { color: th.edge, highlight: th.edgeHi, opacity: idle.opacity, inherit: true }
    }));
    allEdges.update(edgeResets);

    if(activeHoverNodes && activeHoverNodes.length){
      const nodeResets = activeHoverNodes.map(nid => {
        const n = allNodes.get(nid);
        const fs = n ? nodeFontSize(n.degree) : 12;
        return {
          id: nid,
          opacity: 1.0,
          font: { size: fs, color: th.nodeFont }
        };
      });
      allNodes.update(nodeResets);
    }
  }catch(_){}
  activeHoverEdges = null;
  activeHoverNodes = null;
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
  const presFrame = document.getElementById('presentation-frame');
  if(presFrame && presFrame.contentWindow){
    try{ presFrame.contentWindow.postMessage({type: 'cb-theme-change', theme: next}, '*'); }catch(_){}
  }
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
  const el=document.getElementById('net');
  if(!el || el.clientWidth<=0 || el.clientHeight<=0) return;
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
        const isDocDrawerOpen = (name === 'docs' && typeof docDrawerOpen !== 'undefined' && docDrawerOpen);
        el.setAttribute('aria-hidden', (selected || isDocDrawerOpen)?'false':'true');
        el.inert=!(selected || isDocDrawerOpen);
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
    toggleBtn.title = isCompact ? '상세 메뉴 펼치기' : '상세 메뉴 축소(아이콘 모드)';
    toggleBtn.setAttribute('aria-label', isCompact ? '상세 메뉴 펼치기' : '상세 메뉴 축소(아이콘 모드)');
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
    if(!graphCamera && !activeDoc){
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
  if(!target) target=(compactMQ.matches || mobileMQ.matches) ? (document.getElementById('morebtn') || paneEls[activePane]) : paneEls[activePane];
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
  if(!target) target=(compactMQ.matches || mobileMQ.matches) ? (document.getElementById('morebtn') || paneEls[activePane]) : document.getElementById('morebtn');
  detailReturnFocus=null;
  replaceAppHistory({ modal: getActiveModalName() });
  if(focus && target) requestAnimationFrame(()=>target.focus());
}
function toggleDrawer(){
  if(drawerOpen || detailOpen) closeDrawer(true);
  else openDrawer();
}
let docDrawerOpen = false;
function openDocDrawer(pushHist=true){
  hideNodePop();
  docDrawerOpen = true;
  document.body.classList.add('docdrawer-open');
  const docsEl = document.getElementById('docs');
  if(docsEl){
    docsEl.setAttribute('aria-hidden', 'false');
    docsEl.inert = false;
  }
  const backdrop = document.getElementById('drawerbackdrop');
  if(backdrop) backdrop.setAttribute('aria-hidden', 'false');
  closeGraphDocPicker();
  if(pushHist && mobileMQ.matches) pushAppHistory({ modal: 'docdrawer' });
}
function closeDocDrawer(focus=false){
  if(typeof window !== 'undefined' && window.history && window.history.state && window.history.state.modal === 'docdrawer' && !isPoppingHistory){
    window.history.back();
    return;
  }
  hideNodePop();
  docDrawerOpen = false;
  document.body.classList.remove('docdrawer-open');
  const docsEl = document.getElementById('docs');
  if(docsEl && mobileMQ.matches){
    docsEl.setAttribute('aria-hidden', 'true');
    if(activePane !== 'docs') docsEl.inert = true;
  }
  const backdrop = document.getElementById('drawerbackdrop');
  if(backdrop && !drawerOpen && !detailOpen) backdrop.setAttribute('aria-hidden', 'true');
  replaceAppHistory({ modal: getActiveModalName() });
  if(focus){
    const btn = document.getElementById('mobile-doclist-btn');
    if(btn) requestAnimationFrame(()=>btn.focus());
  }
}
function toggleDocDrawer(){
  if(docDrawerOpen) closeDocDrawer(true);
  else openDocDrawer();
}
function closeAllDrawers(){
  if(docDrawerOpen) closeDocDrawer();
  if(drawerOpen || detailOpen) closeDrawer();
}
function openGraphFromDrawer(){
  openDocGraph(activeDoc || curReaderDoc || (mobileMQ.matches ? (getRecentDocId() || docWithMostNodes()) : null));
}
function focusMobileSearch(){
  docSearchActive=true;
  const q=document.getElementById('q') || document.getElementById('docq');
  if(q){ q.value=''; onCenterSearchInput(''); q.focus(); }
}
function closeToolsMenu(focus=false){ closeDrawer(focus); }
function toggleToolsMenu(){ toggleDrawer(); }
function graphSelectableDocs(){
  const visible=allDocs.filter(dc=>dc.hidden!==1);
  return visible.filter(dc=>dc.pinned===1).concat(visible.filter(dc=>dc.pinned!==1));
}
function syncGraphDocNav(){
  const pick=document.getElementById('graphdocpick');
  if(!pick) return;
  const dc=activeDoc ? allDocs.find(item=>item.id===activeDoc) : null;
  document.getElementById('graphdoclabel').textContent=dc ? dc.title : '전체 그래프';
  pick.setAttribute('aria-label',dc ? '자료 전환, 현재 '+dc.title : '자료 선택, 현재 전체 그래프');
  const prev=document.getElementById('graphdocprev'), next=document.getElementById('graphdocnext');
  if(prev && next){
    const docs=graphSelectableDocs();
    const current=docs.findIndex(item=>item.id===activeDoc);
    const usable=current>=0 && docs.length>1;
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
  if(e.key==='Escape'){
    if(docDrawerOpen){
      e.preventDefault();
      e.stopImmediatePropagation();
      closeDocDrawer(true);
      return;
    }
    if(drawerOpen||detailOpen){
      e.preventDefault();
      e.stopImmediatePropagation();
      closeDrawer(true, true);
      return;
    }
  }
});
function responsiveChanged(){
  closeToolsMenu();
  if(!mobileMQ.matches && docDrawerOpen) closeDocDrawer();
  syncWorkspaceLayout();
}
mobileMQ.addEventListener('change',responsiveChanged);
compactMQ.addEventListener('change',responsiveChanged);
toolbarMQ.addEventListener('change',responsiveChanged);
syncWorkspaceLayout();

// 단일-pane 그래프는 페이지 스크롤과 경쟁하지 않는다. 그래프 엔진의 포인터 입력이 양방향
// pan·핀치를 처리하도록 touch-action:none을 유지하고, 다른 pane은 inert로 입력을 받지 않는다.
function applyTouchMode(){
  document.querySelectorAll('#net, #net div, #net canvas').forEach(el=>{ el.style.touchAction='none'; });
}
// 휠 줌 평탄화: 기본 휠 줌은 wheel deltaY '크기'에 비례해 한 이벤트로 여러 단계 점프한다.
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

let graphInitialized = false;
let graphSuspended = false;

function initGraph(onComplete){
  if(graphInitialized){
    if(onComplete) onComplete();
    return;
  }
  fetch('graph').then(r=>{ if(!r.ok) throw new Error('graph fetch HTTP '+r.status); return r.json(); }).then(d=>{
    graphInitialized = true;
  const G = window.ClaireGraph;
  if(G && G.DataSet && G.Network){
    const rawNodes = ((d && d.nodes) || []).map(n => ({
      ...n,
      size: nodeRadius(n.degree),
      font: { size: nodeFontSize(n.degree) }
    }));
    const rawEdges = (d && d.edges) || [];
    applyTopologicalStructure(rawNodes, rawEdges);
    allNodes = new G.DataSet(rawNodes);
    allEdges = new G.DataSet(rawEdges);
    if(!d || !d.nodes || !d.nodes.length){ graphStabilized=true; }
    const totalCount = rawNodes.length;
    let initialDeg = 0;
    if(totalCount >= 800){
      initialDeg = 5;
    } else if(totalCount >= 200){
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
    // 엔진이 허브-스포크 계층 물리·안정화·궤도 회전·흐름 패킷을 스스로 관리한다(별도 settle 타이머 불필요).
    const opts = {
      nodes:{size:4,font:{color:th.nodeFont,size:13}},
      edges:{color:{color:th.edge,highlight:th.edgeHi,opacity:0.35},
        font:{color:th.nodeFont,size:0,strokeColor:netBg}},
      groups:buildGroups(),
      physics:true,
      interaction:{hover:true,multiselect:true,zoomView:false},
      // 그래프 위에 겹친 밀집도·카메라 컨트롤은 카메라 맞춤과 별 무리 비율에서 뺀다(가려진 라벨 방지).
      renderer:{fitAvoid:['#degctl','#zoomctl']}
    };
    const netEl = document.getElementById('net');
    if(netEl) net = new G.Network(netEl, {nodes:allNodes, edges:allEdges}, opts);
    isDraggingNode = false;
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
    // 드래그 물리는 엔진이 맡는다: 잡은 허브의 별은 오므라들며 따라오고, 놓으면 마지막 포인터
    // 속도로 던져진 뒤 회복 감쇠를 거쳐 tether 길이·궤도로 돌아온다(탄성 새총 없이 안정).
    net.on('dragStart', p => {
      netBusy = true;
      hideNodePop();
      isDraggingNode = !!(p && p.nodes && p.nodes.length);
    });
    net.on('dragEnd', () => {
      netBusy = false;
      isDraggingNode = false;
      rememberGraphCamera();
    });
    net.on('animationFinished', () => {
      netBusy = false;
      clearTimeout(busyTimer);
      const scale = net.getScale();
      const show = scale >= 1.2;
      const tier = scale >= 1.0 ? 2 : (scale >= 0.4 ? 1 : 0);
      if(show !== edgeLabelsByZoom || tier !== lastZoomTier){
        edgeLabelsByZoom = show;
        lastZoomTier = tier;
        applyView();
      }
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
        if(isolatedNodeId){
          isolatedNodeId = null;
          if(currentNodeData) renderPanel(currentNodeData);
        }
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
    // hover → NODE_POP_DELAY_MS 뒤 마우스 위치에 작은 요약 팝업(우측 패널은 안 건드림 — 난잡함 해소, 사용자 요구).
    // 우측 패널은 클릭(inspect)일 때만 바뀐다 → hover 가 패널/선택을 흔들지 않아 복원 로직도 불필요.
    net.on('hoverNode', p => {
      const isMobile = (compactMQ && compactMQ.matches) || (mobileMQ && mobileMQ.matches);
      if(isMobile) return;
      applyHoverHighlight(p.node);
      clearTimeout(hoverTimer);
      if(!canShowNodePop(p.node)) return;
      hoverTimer=setTimeout(()=>showNodePop(p.node), NODE_POP_DELAY_MS);
    });
    net.on('blurNode', () => {
      const isMobile = (compactMQ && compactMQ.matches) || (mobileMQ && mobileMQ.matches);
      if(!isMobile){
        clearHoverHighlight();
        hideNodePop();
      }
    });
    net.on('hold', () => {
      const isMobile = (compactMQ && compactMQ.matches) || (mobileMQ && mobileMQ.matches);
      if(!isMobile){
        clearHoverHighlight();
        hideNodePop();
      }
    });
    net.on('dragStart', () => {
      clearHoverHighlight();
      hideNodePop();
    });   // 드래그/줌 중엔 팝업 숨김(커서를 따라다니지 않게)
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
          const scale = net.getScale();
          const show = scale >= 1.2;
          const tier = scale >= 1.0 ? 2 : (scale >= 0.4 ? 1 : 0);
          if(show !== edgeLabelsByZoom || tier !== lastZoomTier){
            edgeLabelsByZoom = show;
            lastZoomTier = tier;
            applyView();
          }
        }, 120);
      }
    });
  }
  applyView();
  if(activeDoc && !selectedNodeId){
    if(curReaderDocData && curReaderDocData.id===activeDoc) renderDocPanel(curReaderDocData);
    else loadDocPanel(activeDoc);
  }
  if(onComplete) onComplete();
}).catch(err => {
  console.warn('graph load error:', err);
  const st=document.getElementById('stat');
  if(st && st.textContent.indexOf('로딩')!==-1) st.textContent='그래프 준비 완료';
  if(onComplete) onComplete();
});
}

function suspendGraphForIngest(){
  graphSuspended = true;
  netBusy = true;
  if(net){
    try { net.stopSimulation(); } catch(_) {}
  }
  const netwrap = document.getElementById('netwrap');
  if(netwrap) netwrap.style.display = 'none';
}

function resumeGraphAfterIngest(){
  if(!graphSuspended) return;
  graphSuspended = false;
  netBusy = false;
  const netwrap = document.getElementById('netwrap');
  if(netwrap) netwrap.style.display = '';
  if(!graphInitialized || !net){
    initGraph();
  } else {
    try { net.startSimulation(); } catch(_) {}
    refreshGraph();
  }
}

// 초기 구동 검사: 적재 진행 중이면 그래프를 표시하거나 계산하지 않고 즉시 대조 매트릭스로 진입
fetch('stats').then(r => r.ok ? r.json() : null).then(d => {
  if(d && d.active_ingest && d.active_ingest.heatmap_matrix){
    const docId = (d.active_ingest.result && d.active_ingest.result.document_id) || d.active_ingest.heatmap_matrix.document_id || null;
    if(docId){
      try {
        sessionStorage.setItem('doc_matrix_' + docId, JSON.stringify(d.active_ingest.heatmap_matrix));
      } catch(_) {}
    }
    window.latestHeatmapMatrix = d.active_ingest.heatmap_matrix;
    window.latestHeatmapDocId = docId;
    window.latestHeatmapDocTitle = d.active_ingest.title || '';
  }
  const view = resolveHomeView(d, activeThemeId);
  if(view === 'job'){
    suspendGraphForIngest();
    setCenterView('matrix');
    processActiveJobsStats(d);
    startFastIngestPolling();
  } else if(view === 'graph+banner'){
    initGraph();
    showOtherThemeJobBanner(d);
    if(centerView === 'stream' && typeof renderDecisionStreamCenter === 'function'){
      renderDecisionStreamCenter(false);
    }
  } else {
    initGraph();
    if(centerView === 'stream' && typeof renderDecisionStreamCenter === 'function'){
      renderDecisionStreamCenter(false);
    }
  }
}).catch(() => {
  initGraph();
  if(centerView === 'stream' && typeof renderDecisionStreamCenter === 'function'){
    renderDecisionStreamCenter(false);
  }
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
  if(isolatedNodeId){
    isolatedNodeId = null;
    if(currentNodeData) renderPanel(currentNodeData);
  }
  const q=document.getElementById('q'); if(q) q.value='';
  if(net) net.unselectAll();
  unclusterEdges();   // 검색으로 뭉치게 한 임시 spring 엣지 제거 → 물리가 원래대로
  applyView();
}
document.addEventListener('keydown', e=>{
  if(handleReaderKey(e)) return;
  if(e.key!=='Escape') return;
  const nodeMenu = document.getElementById('node-manage-menu');
  if(nodeMenu && nodeMenu.style.display !== 'none'){
    closeNodeManageMenu();
    return;
  }
  const aliasCancelBtn = document.getElementById('alias-cancel-btn');
  if(aliasCancelBtn && aliasCancelBtn.style.display !== 'none'){
    cancelAliasSelectMode();
    return;
  }
  const bar=document.getElementById('bar');
  if(!document.getElementById('graphdocmenu').hidden){ closeGraphDocPicker(true, true); return; }
  if(bar.classList.contains('tools-open')){ closeToolsMenu(true, true); return; }
  if(document.body.classList.contains('detail-open')){ closeDetailPane(true); return; }
  clearSelections(); });

let currentNodeData = null;

function toggleNodeManageMenu(e){
  if(e) e.stopPropagation();
  const menu = document.getElementById('node-manage-menu');
  const btn = document.getElementById('node-manage-btn');
  if(!menu || !btn) return;
  const isHidden = menu.style.display === 'none';
  if(isHidden){
    menu.style.display = 'block';
    btn.setAttribute('aria-expanded', 'true');
  } else {
    menu.style.display = 'none';
    btn.setAttribute('aria-expanded', 'false');
  }
}

function closeNodeManageMenu(){
  const menu = document.getElementById('node-manage-menu');
  const btn = document.getElementById('node-manage-btn');
  if(menu){
    menu.style.display = 'none';
  }
  if(btn){
    btn.setAttribute('aria-expanded', 'false');
  }
}

function startAliasSelectMode(){
  closeNodeManageMenu();
  if(!currentNodeData || !currentNodeData.aliases || !currentNodeData.aliases.length) return;
  const labelEl = document.getElementById('node-aliases-label');
  const cancelBtn = document.getElementById('alias-cancel-btn');
  const chipsEl = document.getElementById('node-alias-chips');
  if(labelEl){
    labelEl.innerHTML = '<span style="color:var(--accent);font-weight:600">대표로 지정할 별칭 선택</span>';
  }
  if(cancelBtn){
    cancelBtn.style.display = 'inline-flex';
  }
  if(chipsEl){
    chipsEl.classList.add('selecting');
    chipsEl.innerHTML = currentNodeData.aliases.map(al =>
      '<button type="button" class="node-alias-chip selectable" title="\'' + esc(al) + '\'을(를) 대표 레이블로 지정" onclick="promoteEntityAlias(\'' + esc(currentNodeData.id) + '\',\'' + esc(al).replace(/'/g, "\\'") + '\')">' +
      '<span class="alias-star" aria-hidden="true">★</span> <span class="alias-text">' + esc(al) + '</span>' +
      '</button>'
    ).join('');
    const firstSelectable = chipsEl.querySelector('button.selectable');
    if(firstSelectable) firstSelectable.focus();
  }
}

function cancelAliasSelectMode(){
  if(!currentNodeData || !currentNodeData.aliases) return;
  const labelEl = document.getElementById('node-aliases-label');
  const cancelBtn = document.getElementById('alias-cancel-btn');
  const chipsEl = document.getElementById('node-alias-chips');
  if(labelEl){
    labelEl.textContent = '별칭 (' + currentNodeData.aliases.length + ')';
  }
  if(cancelBtn){
    cancelBtn.style.display = 'none';
  }
  if(chipsEl){
    chipsEl.classList.remove('selecting');
    chipsEl.innerHTML = currentNodeData.aliases.map(al =>
      '<span class="node-alias-chip"><span class="alias-text">' + esc(al) + '</span></span>'
    ).join('');
  }
}

window.addEventListener('click', function(e){
  const menu = document.getElementById('node-manage-menu');
  const btn = document.getElementById('node-manage-btn');
  if(menu && menu.style.display !== 'none'){
    if(!menu.contains(e.target) && !btn.contains(e.target)){
      closeNodeManageMenu();
    }
  }
});

function focusOnNode(id){
  if(!net || !allNodes) return;
  requestAnimationFrame(()=>{
    try{
      const neighbors = (typeof net.getConnectedNodes === 'function') ? net.getConnectedNodes(id) : [];
      const focusIds = [id, ...(Array.isArray(neighbors) ? neighbors : [])].filter(nid => {
        const node = allNodes.get(nid);
        return node && !node.hidden;
      });
      cameraToNodes(focusIds);
    }catch(_){}
  });
}

function toggleNodeIsolation(id){
  if(isolatedNodeId === id){
    isolatedNodeId = null;
  } else {
    isolatedNodeId = id;
    selectedNodeId = id;
  }
  applyView();
  if(isolatedNodeId){
    focusOnNode(isolatedNodeId);
  } else {
    resetGraphCamera();
  }
  if(currentNodeData && currentNodeData.id === id){
    renderPanel(currentNodeData);
  }
}

function clearNodeIsolation(){
  if(!isolatedNodeId) return;
  isolatedNodeId = null;
  applyView();
  resetGraphCamera();
  if(currentNodeData){
    renderPanel(currentNodeData);
  }
}
window.toggleNodeIsolation = toggleNodeIsolation;
window.clearNodeIsolation = clearNodeIsolation;

function loadNode(id, hidePop=true){
  const focusCamera = arguments.length > 2 ? arguments[2] : true;
  if(hidePop) hideNodePop();
  if(net) net.selectNodes([id]);   // 클릭 inspect — hover 는 더 이상 패널을 안 쓴다(팝업으로 분리)
  applyView();                     // 선택 노드 주변 엣지만 강조하고 관계 라벨을 펼친다
  if(focusCamera) focusOnNode(id);
  fetch('node?id='+encodeURIComponent(id)).then(r=>r.json()).then(renderPanel);
}
function renderPanel(d){
  if(!d || d.error){ panel.innerHTML='<p class=hint>노드를 찾을 수 없습니다.</p>'; return; }
  currentNodeData = d;
  const inSet = synthSet.has(d.id);
  // 문서를 고른 상태에서 노드로 들어왔으면 문서 패널로 한 번에 돌아갈 링크.
  let h = activeDoc ? '<span class=backlink onclick="loadDocPanel(activeDoc)">← 문서로 돌아가기</span>' : '';
  h+='<h2>'+esc(d.name)+' <small>'+esc(d.type)+(d.provisional?' ⚠️provisional':'')+'</small></h2>';
  const isIsolated = (isolatedNodeId === d.id);
  const isoBtn = '<button type="button" class="sec node-isolate-btn '+(isIsolated?'active':'')+'" onclick="toggleNodeIsolation(\''+d.id+'\')" title="이 노드와 직접 연결된 이웃만 화면에 남깁니다">'+(isIsolated?'✕ 격리 해제':'🔍 주변만 보기')+'</button>';
  h+='<div class="node-actions-bar">';
  h+=isoBtn;
  if(canWrite()){
    h+='<button class="sec" onclick="addToSynth(\''+d.id+'\')">'+(inSet?'✓ 종합 목록에 있음':'➕ 종합에 추가')+'</button>';
    if(d.aliases && d.aliases.length){
      h+='<div class="node-manage-dropdown">';
      h+='<button type="button" class="sec node-manage-btn" id="node-manage-btn" aria-haspopup="true" aria-expanded="false" onclick="toggleNodeManageMenu(event)">⚙️ 노드 관리 ▾</button>';
      h+='<div class="node-manage-menu" id="node-manage-menu" style="display:none">';
      h+='<button type="button" class="node-manage-item" id="node-manage-alias-btn" onclick="startAliasSelectMode()">🏷️ 대표 별칭</button>';
      h+='</div>';
      h+='</div>';
    }
  }
  h+='</div>';
  if(d.aliases && d.aliases.length){
    h+='<div class="node-aliases-section" id="node-aliases-section">';
    h+='<div class="node-aliases-header">';
    h+='<span class="al-label" id="node-aliases-label">별칭 ('+d.aliases.length+')</span>';
    h+='<button type="button" class="alias-cancel-btn" id="alias-cancel-btn" style="display:none" onclick="cancelAliasSelectMode()">취소</button>';
    h+='</div>';
    h+='<div class="node-alias-chips" id="node-alias-chips">';
    d.aliases.forEach(al=>{
      h+='<span class="node-alias-chip"><span class="alias-text">'+esc(al)+'</span></span>';
    });
    h+='</div></div>';
  }
  if(d.observations.length){ h+='<h3>관찰 · 주장</h3><ul>'+
    d.observations.map(o=>'<li>'+esc(o)+'</li>').join('')+'</ul>'; }
  if(d.documents.length){ h+='<h3>출처 문서 ('+d.documents.length+')</h3>';
    // 설명(summary) → 📖 본문 보기(중앙 리더로 열기) → 원문 링크 순.
    d.documents.forEach(dc=>{ h+='<div class=doc><b>'+esc(dc.title)+'</b>'+
      (dc.summary?'<p>'+esc(dc.summary)+'</p>':'')+
      ((dc.detail||dc.summary)?'<button class=readbtn data-read-doc="'+esc(dc.id)+
        '" onclick="openReader(\''+dc.id+'\');setCenterView(\'reader\')">📖 본문 보기</button>':'')+
      (isWebUrl(dc.url)?'<p class=src><a href="'+esc(dc.url)+'" target=_blank rel=noopener>↗ 원문 열기</a></p>':'')+
      (canIngest() && dc.has_raw_file?'<p class=src><a href="/document/raw?id='+encodeURIComponent(dc.id)+'" target=_blank rel=noopener>📄 원본 열기</a></p>':'')+
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

async function promoteEntityAlias(entityId, chosenAlias){
  if(!canWrite() || !entityId || !chosenAlias) return;
  if(!confirm("'" + chosenAlias + "'을(를) 이 노드의 대표 레이블로 변경하시겠습니까?\n\n(기존 대표 명칭은 별칭 목록으로 보존되어 연결 무결성이 유지됩니다)")) return;
  try{
    const r = await fetch('entity/primary-label', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({id: entityId, name: chosenAlias})
    });
    if(r.status === 401 || r.status === 403){
      expireWriteAccess();
      alert('세션 만료 또는 권한 부족 — 텔레그램 /web 으로 다시 접속하세요');
      return;
    }
    if(!r.ok){
      const err = await r.json().catch(() => ({}));
      alert('대표 레이블 전환 실패: ' + (err.detail || err.error || ('HTTP ' + r.status)));
      return;
    }
    const updated = await r.json();
    if(typeof allNodes !== 'undefined' && allNodes && allNodes.update){
      allNodes.update({id: entityId, label: updated.name});
    }
    renderPanel(updated);
  }catch(e){
    alert('요청 중 오류 발생: ' + e);
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
let activeIngest = null;

function updateIngestElapsed(){
  if(!activeIngest) return;
  const el = document.getElementById('ielapsed');
  if(el){
    const elapsedSec = Math.max(0, Math.round((Date.now() - activeIngest.t0) / 1000));
    const opts = activeIngest.optionLabels || [];
    el.textContent = '⏱ 경과 ' + elapsedSec + 's' + (opts.length ? ' (' + opts.join(' · ') + ')' : '');
  }
}

function renderIngestStatus(){
  if(!activeIngest) return;
  openDetailPane();
  const existing = document.getElementById('ingest-status-view');
  if(existing){
    updateIngestElapsed();
    return;
  }
  const elapsedSec = Math.max(0, Math.round((Date.now() - activeIngest.t0) / 1000));
  const elapsedText = elapsedSec > 0
    ? '⏱ 경과 ' + elapsedSec + 's' + (activeIngest.optionLabels && activeIngest.optionLabels.length ? ' (' + activeIngest.optionLabels.join(' · ') + ')' : '')
    : activeIngest.labelText;
  let h = '<div id="ingest-status-view">';
  h += '<h2>➕ 적재 상태 <small>적재 중</small></h2>';
  h += '<p class="al" id="ielapsed">' + esc(elapsedText) + '</p>';
  h += '<ul id="iprog">';
  if(activeIngest.messages){
    for(const msg of activeIngest.messages){
      h += '<li class="al">' + esc(msg) + '</li>';
    }
  }
  h += '</ul>';
  h += '</div>';
  panel.innerHTML = h;
}

function openIngest(){
  if(!canIngest()) return;
  if(activeIngest && activeIngest.running){
    renderIngestStatus();
    return;
  }
  const isMulti = isMultiThemeEnabled();
  let themeFieldHtml = '';
  if(isMulti && availableThemes && availableThemes.length > 0){
    if(AUTH_SCOPE === 'collaborator'){
      const eligibleThemes = availableThemes.filter(t => t.id > 0 && t.is_collaborator_accessible !== false);
      if(eligibleThemes.length === 0){
        themeFieldHtml = '<div class="ingest-field"><p class="hint" style="color:var(--warn,#f39c12);margin:0;">⚠️ 적재 가능한 협업 테마가 없습니다. 지식 관리자(owner)에게 테마 공개를 요청하세요.</p></div>';
      } else {
        const selId = eligibleThemes.some(t => t.id === activeThemeId) ? activeThemeId : eligibleThemes[0].id;
        const opts = eligibleThemes.map(t => {
          const icon = t.icon || '📁';
          const label = esc(t.label);
          return `<option value="${t.id}" ${t.id === selId ? 'selected' : ''}>${icon} ${label}</option>`;
        }).join('');
        themeFieldHtml = ('<div class="ingest-field">'+
          '<label class="ingest-label" for="ingtheme">적재 대상 테마 <span class="ingest-help">협업이 허용된 지식 테마</span></label>'+
          '<select id="ingtheme" class="ingest-theme-select">'+
            opts+
          '</select>'+
        '</div>');
      }
    } else if(availableThemes.length > 1){
      themeFieldHtml = ('<div class="ingest-field">'+
        '<label class="ingest-label" for="ingtheme">적재 대상 테마 <span class="ingest-help">지식 관리자가 정의한 테마</span></label>'+
        '<select id="ingtheme" class="ingest-theme-select">'+
          renderThemeOptions(activeThemeId)+
        '</select>'+
      '</div>');
    }
  }
  panel.innerHTML='<h2>➕ 자료 적재</h2>'+
    '<p class=al>URL 또는 메모 텍스트를 입력하고 적재 방식을 선택하세요.</p>'+
    '<div class="ingest-form">'+
      themeFieldHtml +
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

  const ingthemeEl = document.getElementById('ingtheme');
  const ingfocusEl = document.getElementById('ingfocus');
  function updateFocusPlaceholder(){
    if(!ingfocusEl) return;
    const tid = ingthemeEl ? parseInt(ingthemeEl.value, 10) : (isMulti ? activeThemeId : 0);
    const themeObj = availableThemes.find(t => t.id === tid);
    if(themeObj && themeObj.id > 0 && themeObj.default_focus){
      ingfocusEl.placeholder = `기본 초점: ${themeObj.default_focus} (비워둘 시 자동 적용)`;
    } else {
      ingfocusEl.placeholder = '예: 시스템 아키텍처와 내부 동작 중심';
    }
  }
  if(ingthemeEl){
    ingthemeEl.addEventListener('change', updateFocusPlaceholder);
  }
  updateFocusPlaceholder();
}
async function runIngest(){
  if(!canIngest()) return;
  if(activeIngest && activeIngest.running){
    alert('이미 적재가 진행 중입니다.');
    renderIngestStatus();
    return;
  }
  const isMulti = isMultiThemeEnabled();
  const ta=document.getElementById('ingin');
  const payload=((ta||{}).value||'').trim();
  if(!payload){ alert('적재할 URL 또는 텍스트를 입력하세요.'); return; }
  const ingthemeEl = document.getElementById('ingtheme');
  const targetThemeId = (isMulti && ingthemeEl) ? parseInt(ingthemeEl.value, 10) : (isMulti ? activeThemeId : 0);
  if(AUTH_SCOPE === 'collaborator'){
    if(!targetThemeId || targetThemeId === 0){
      alert('기본 지식베이스(ID 0)에는 협업자 권한으로 적재할 수 없습니다. 협업이 허용된 추가 지식 테마를 선택하세요.');
      return;
    }
  }
  const focus=((document.getElementById('ingfocus')||{}).value||'').trim();
  const amountChoice=document.querySelector('input[name="ingest-amount"]:checked');
  const effortChoice=document.querySelector('input[name="ingest-effort"]:checked');
  const fullContent=!!amountChoice && amountChoice.value==='full';
  const effort=effortChoice ? effortChoice.value : '';
  let labelText = '시작…';
  const optionLabels=[];
  const targetThemeObj = isMulti ? availableThemes.find(t => t.id === targetThemeId) : null;
  if(targetThemeObj && targetThemeObj.id !== 0){
    optionLabels.push('테마: ' + (targetThemeObj.icon ? targetThemeObj.icon + ' ' : '') + targetThemeObj.label);
  }
  if(fullContent) optionLabels.push('전문 적재');
  if(effort) optionLabels.push('사고: '+effort);
  if(focus){
    optionLabels.push('초점: '+(focus.length > 20 ? focus.slice(0,20)+'…' : focus));
  } else if(targetThemeObj && targetThemeObj.id > 0 && targetThemeObj.default_focus){
    const df = targetThemeObj.default_focus;
    optionLabels.push('초점(기본): '+(df.length > 20 ? df.slice(0,20)+'…' : df));
  }
  if(optionLabels.length){
    labelText += ' ('+optionLabels.join(' · ')+')';
  }
  activeIngest = {
    running: true,
    t0: Date.now(),
    labelText: labelText,
    optionLabels: optionLabels,
    messages: [],
    timer: null,
  };
  renderIngestStatus();
  activeIngest.timer = setInterval(() => {
    if(!activeIngest || !activeIngest.running){
      if(activeIngest && activeIngest.timer){
        clearInterval(activeIngest.timer);
        activeIngest.timer = null;
      }
      return;
    }
    updateIngestElapsed();
  }, 1000);
  let result=null;
  suspendGraphForIngest();
  if(typeof initIngestMatrixView === 'function'){
    try {
      initIngestMatrixView(payload, focus, targetThemeObj ? targetThemeObj.label : '');
    } catch(e) {
      console.warn('initIngestMatrixView error:', e);
    }
  }
  if(typeof setCenterView === 'function'){
    setCenterView('matrix');
  }
  if(mobileMQ.matches && typeof closeDrawer === 'function'){
    closeDrawer(false, false);
  }
  try{
    const bodyObj = {payload:payload, full_content:fullContent};
    if(isMulti && targetThemeId !== undefined && targetThemeId !== null && targetThemeId !== 0) bodyObj.theme = targetThemeId;
    if(effort) bodyObj.effort=effort;
    if(focus) bodyObj.focus=focus;
    const r=await fetch('ingest-stream',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify(bodyObj)});
    if(r.status===401||r.status===404){
      if(activeIngest){
        if(activeIngest.timer) clearInterval(activeIngest.timer);
        activeIngest.timer = null;
        activeIngest.running = false;
      }
      expireWriteAccess();
      panel.innerHTML='<p class=hint>세션 만료 — 텔레그램 /web 으로 다시 접속하세요</p>'; return;
    }
    if(!r.ok){
      if(activeIngest){
        if(activeIngest.timer) clearInterval(activeIngest.timer);
        activeIngest.timer = null;
        activeIngest.running = false;
      }
      let d={};
      try{ d=await r.json(); }catch(_){}
      panel.innerHTML='<p class=hint>적재 요청 실패: '+esc(d.error||('HTTP '+r.status))+'</p>'; return;
    }
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
        if(ev.stage === 'heatmap_matrix' && ev.matrix && typeof renderHeatmapMatrix === 'function'){
          renderHeatmapMatrix(ev.matrix, null, true);
          continue;
        }
        if(ev.stage === 'decision' && typeof addDecisionStreamCard === 'function'){
          addDecisionStreamCard(ev);
          continue;
        }
        const cleanMsg = (ev.msg||'').replace(/^[•*-]\s*/, '');
        if(cleanMsg){
          if(typeof updateMatrixProgress === 'function') updateMatrixProgress(cleanMsg);
          if(activeIngest) activeIngest.messages.push(cleanMsg);
          const ul=document.getElementById('iprog');
          if(ul){
            const li=document.createElement('li');
            li.className='al';
            li.textContent=cleanMsg;
            ul.appendChild(li);
          }
        }
      }
    }
  }catch(e){
    if(activeIngest){
      if(activeIngest.timer) clearInterval(activeIngest.timer);
      activeIngest.timer = null;
      activeIngest.running = false;
    }
    panel.innerHTML='<p class=hint>요청 실패: '+esc(String(e))+'</p>'; return;
  }
  if(activeIngest){
    if(activeIngest.timer) clearInterval(activeIngest.timer);
    activeIngest.timer = null;
    activeIngest.running = false;
  }
  if(result && result.heatmap_matrix && result.document_id){
    try {
      sessionStorage.setItem('doc_matrix_' + result.document_id, JSON.stringify(result.heatmap_matrix));
    } catch(_) {}
    window.latestHeatmapMatrix = result.heatmap_matrix;
    window.latestHeatmapDocId = result.document_id;
  }
  if(result && typeof completeIngestMatrixView === 'function'){
    completeIngestMatrixView(result);
  }
  if(!result){ panel.innerHTML='<p class=hint>응답이 끊겼습니다 — 잠시 후 다시 시도하세요.</p>'; return; }
  renderIngestResult(result);
}
function renderIngestResult(d){
  if(d.error){ panel.innerHTML='<h2>➕ 적재</h2><p class=hint>오류: '+esc(d.error)+'</p>'+
    '<p class=al>원본은 보관되어 자동복구(recover) 대상이 됩니다.</p>'; return; }
  let h='<h2>'+(d.duplicate?'♻️ 이미 있는 자료':(d.updated?'🔄 내용 갱신':'✅ 적재 완료'))+'</h2>';
  h+='<p class=al><b>'+esc(d.title||d.document_id||'(제목 없음)')+'</b>'+(d.partial?' <small>⚠️ 부분 처리</small>':'')+'</p>';
  if(d.focus) h+='<p class=al><b>초점:</b> '+esc(d.focus)+'</p>';
  const appliedOptions=[];
  if(d.theme_id !== undefined){
    appliedOptions.push('테마: ' + esc(d.theme_label || ('#' + d.theme_id)));
  }
  if(d.full_content) appliedOptions.push('전문 적재');
  if(d.effort) appliedOptions.push('사고 수준 '+esc(d.effort));
  if(appliedOptions.length) h+='<p class=al><b>적용 설정:</b> '+appliedOptions.join(' · ')+'</p>';
  if(!d.duplicate) h+='<p class=al>노드 신규 '+(d.entities_created||0)+' · 기존연결 '+
    (d.entities_linked||0)+' · 관계 '+(d.relations_added||0)+'</p>';
  if(d.summary) h+='<div class=synth>'+esc(d.summary)+'</div>';
  if(d.theme_id !== undefined && d.theme_id !== activeThemeId){
    h+='<p style="margin-top:12px"><button type="button" class="sec" onclick="switchKnowledgeTheme('+d.theme_id+')">👉 '+esc(d.theme_label||('테마 #'+d.theme_id))+' 테마로 전환</button></p>';
  } else {
    if(d.document_id) h+='<p><a href="#" onclick="selectDoc(\''+d.document_id+'\');setCenterView(\'reader\');return false">문서 보기 →</a></p>';
    refreshGraph();   // 현재 활성 테마에 적재된 경우 즉시 그래프/문서 반영
  }
  panel.innerHTML=h;
}

// --- 자료 재적재 (Reingest) 및 소각 (Purge) ---
function openReingest(){
  if(!canIngest()) return;
  if(activeIngest && activeIngest.running){
    renderIngestStatus();
    return;
  }
  const curDoc = (allDocs && allDocs.find(d => d.id === activeDoc)) || curReaderDocData;
  const defTarget = (curDoc && (curDoc.url || curDoc.canonical_url || curDoc.id || '')) || '';
  const defTitle = (curDoc && curDoc.title) ? esc(curDoc.title) : '';
  const isMulti = isMultiThemeEnabled();
  let themeFieldHtml = '';
  const curDocThemeId = (curDoc && curDoc.theme_id !== undefined && curDoc.theme_id !== null) ? curDoc.theme_id : activeThemeId;
  if(isMulti && availableThemes && availableThemes.length > 0){
    if(AUTH_SCOPE === 'collaborator'){
      const eligibleThemes = availableThemes.filter(t => t.id > 0 && t.is_collaborator_accessible !== false);
      if(eligibleThemes.length === 0){
        themeFieldHtml = '<div class="ingest-field"><p class="hint" style="color:var(--warn,#f39c12);margin:0;">⚠️ 재적재 가능한 협업 테마가 없습니다.</p></div>';
      } else {
        const selId = eligibleThemes.some(t => t.id === curDocThemeId) ? curDocThemeId : eligibleThemes[0].id;
        const opts = eligibleThemes.map(t => {
          const icon = t.icon || '📁';
          const label = esc(t.label);
          return `<option value="${t.id}" ${t.id === selId ? 'selected' : ''}>${icon} ${label}</option>`;
        }).join('');
        themeFieldHtml = ('<div class="ingest-field">'+
          '<label class="ingest-label" for="reingtheme">재적재 대상 테마 <span class="ingest-help">협업이 허용된 지식 테마</span></label>'+
          '<select id="reingtheme" class="ingest-theme-select">'+opts+'</select>'+
        '</div>');
      }
    } else if(availableThemes.length > 1){
      themeFieldHtml = ('<div class="ingest-field">'+
        '<label class="ingest-label" for="reingtheme">재적재 대상 테마 <span class="ingest-help">지식 테마 선택</span></label>'+
        '<select id="reingtheme" class="ingest-theme-select">'+
          renderThemeOptions(curDocThemeId)+
        '</select>'+
      '</div>');
    }
  }

  panel.innerHTML = '<h2>🔄 자료 재적재</h2>'+
    '<p class="al">문서 ID, URL 또는 텍스트를 입력하여 기존 문서를 다시 수집하고 지식그래프를 재적재합니다.</p>'+
    '<div class="ingest-form">'+
      themeFieldHtml +
      '<div class="ingest-field">'+
        '<label class="ingest-label" for="reingtarget">재적재 대상 <span class="ingest-help">문서 ID, URL 또는 텍스트</span></label>'+
        '<textarea id="reingtarget" rows="4" placeholder="https://example.com/article 또는 문서 ID">'+esc(defTarget)+'</textarea>'+
        (defTitle ? '<small style="color:var(--muted);display:block;margin-top:3px">현재 선택된 문서: <b>'+defTitle+'</b></small>' : '')+
      '</div>'+
      '<fieldset class="ingest-options">'+
        '<legend class="ingest-label">적재 분량</legend>'+
        '<div class="ingest-choice-grid">'+
          '<div class="ingest-choice"><input id="reingamount-standard" type="radio" name="reingest-amount" value="standard" checked><label for="reingamount-standard" title="설정된 글자 수 상한을 적용합니다">일반</label></div>'+
          '<div class="ingest-choice"><input id="reingamount-full" type="radio" name="reingest-amount" value="full"><label for="reingamount-full" title="원문을 절단하지 않고 전문을 수집하고 분석합니다">전문</label></div>'+
        '</div>'+
      '</fieldset>'+
      '<fieldset class="ingest-options">'+
        '<legend class="ingest-label">사고 수준 <span class="ingest-help">미지정 시 서버 설정 사용</span></legend>'+
        '<div class="ingest-choice-grid effort">'+
          '<div class="ingest-choice"><input id="reingeffort-default" type="radio" name="reingest-effort" value="" checked><label for="reingeffort-default">기본</label></div>'+
          '<div class="ingest-choice"><input id="reingeffort-none" type="radio" name="reingest-effort" value="none"><label for="reingeffort-none">없음</label></div>'+
          '<div class="ingest-choice"><input id="reingeffort-minimal" type="radio" name="reingest-effort" value="minimal"><label for="reingeffort-minimal">최소</label></div>'+
          '<div class="ingest-choice"><input id="reingeffort-low" type="radio" name="reingest-effort" value="low"><label for="reingeffort-low">낮음</label></div>'+
          '<div class="ingest-choice"><input id="reingeffort-medium" type="radio" name="reingest-effort" value="medium"><label for="reingeffort-medium">중간</label></div>'+
          '<div class="ingest-choice"><input id="reingeffort-high" type="radio" name="reingest-effort" value="high"><label for="reingeffort-high">높음</label></div>'+
          '<div class="ingest-choice"><input id="reingeffort-max" type="radio" name="reingest-effort" value="max"><label for="reingeffort-max">최대</label></div>'+
        '</div>'+
      '</fieldset>'+
      '<div class="ingest-field">'+
        '<label class="ingest-label" for="reingfocus">초점 <span class="ingest-help">선택 사항</span></label>'+
        '<textarea id="reingfocus" rows="2" placeholder="예: 시스템 아키텍처와 내부 동작 중심"></textarea>'+
      '</div>'+
      '<div class="ingest-field" style="margin-top:6px">'+
        '<label style="display:flex;align-items:center;gap:6px;font-size:12px;cursor:pointer">'+
          '<input type="checkbox" id="reinguntombstone" checked> '+
          '<span>소각 이력이 있는 경우 툼스톤 자동 해제 (Untombstone)</span>'+
        '</label>'+
      '</div>'+
      '<div class="ingest-submit"><button type="button" onclick="runReingest()">재적재 시작</button></div>'+
    '</div>';
  openDetailPane();
}

async function runReingest(){
  if(!canIngest()) return;
  if(activeIngest && activeIngest.running){
    alert('이미 적재 또는 재적재가 진행 중입니다.');
    renderIngestStatus();
    return;
  }
  const isMulti = isMultiThemeEnabled();
  const ta=document.getElementById('reingtarget');
  const target=((ta||{}).value||'').trim();
  if(!target){ alert('재적재할 대상(문서 ID 또는 URL)을 입력하세요.'); return; }
  const reingthemeEl = document.getElementById('reingtheme');
  const targetThemeId = (isMulti && reingthemeEl) ? parseInt(reingthemeEl.value, 10) : (isMulti ? activeThemeId : 0);
  if(AUTH_SCOPE === 'collaborator'){
    if(!targetThemeId || targetThemeId === 0){
      alert('기본 지식베이스(ID 0)에는 협업자 권한으로 재적재할 수 없습니다. 협업이 허용된 추가 지식 테마를 선택하세요.');
      return;
    }
  }
  const focus=((document.getElementById('reingfocus')||{}).value||'').trim();
  const amountChoice=document.querySelector('input[name="reingest-amount"]:checked');
  const effortChoice=document.querySelector('input[name="reingest-effort"]:checked');
  const untombstoneChk=document.getElementById('reinguntombstone');
  const untombstone=!!(untombstoneChk && untombstoneChk.checked);
  const fullContent=!!amountChoice && amountChoice.value==='full';
  const effort=effortChoice ? effortChoice.value : '';
  let labelText = '재적재 시작…';
  const optionLabels=[];
  const targetThemeObj = isMulti ? availableThemes.find(t => t.id === targetThemeId) : null;
  if(targetThemeObj && targetThemeObj.id !== 0){
    optionLabels.push('테마: ' + (targetThemeObj.icon ? targetThemeObj.icon + ' ' : '') + targetThemeObj.label);
  }
  if(fullContent) optionLabels.push('전문 재적재');
  if(effort) optionLabels.push('사고: '+effort);
  if(focus){
    optionLabels.push('초점: '+(focus.length > 20 ? focus.slice(0,20)+'…' : focus));
  }
  if(untombstone) optionLabels.push('툼스톤 해제');
  if(optionLabels.length){
    labelText += ' ('+optionLabels.join(' · ')+')';
  }
  activeIngest = {
    running: true,
    t0: Date.now(),
    labelText: labelText,
    optionLabels: optionLabels,
    messages: [],
    timer: null,
  };
  renderIngestStatus();
  activeIngest.timer = setInterval(() => {
    if(!activeIngest || !activeIngest.running){
      if(activeIngest && activeIngest.timer){
        clearInterval(activeIngest.timer);
        activeIngest.timer = null;
      }
      return;
    }
    updateIngestElapsed();
  }, 1000);
  let result=null;
  suspendGraphForIngest();
  if(typeof initIngestMatrixView === 'function'){
    try {
      initIngestMatrixView(target, focus, targetThemeObj ? targetThemeObj.label : '');
    } catch(e) {
      console.warn('initIngestMatrixView error:', e);
    }
  }
  if(typeof setCenterView === 'function'){
    setCenterView('matrix');
  }
  if(mobileMQ.matches && typeof closeDrawer === 'function'){
    closeDrawer(false, false);
  }
  try{
    const bodyObj = {target:target, full_content:fullContent, untombstone:untombstone};
    if(isMulti && targetThemeId !== undefined && targetThemeId !== null && targetThemeId !== 0) bodyObj.theme = targetThemeId;
    if(effort) bodyObj.effort=effort;
    if(focus) bodyObj.focus=focus;
    const r=await fetch('reingest-stream',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify(bodyObj)});
    if(r.status===401||r.status===404){
      if(activeIngest){
        if(activeIngest.timer) clearInterval(activeIngest.timer);
        activeIngest.timer = null;
        activeIngest.running = false;
      }
      expireWriteAccess();
      panel.innerHTML='<p class=hint>세션 만료 — 텔레그램 /web 으로 다시 접속하세요</p>'; return;
    }
    if(!r.ok){
      if(activeIngest){
        if(activeIngest.timer) clearInterval(activeIngest.timer);
        activeIngest.timer = null;
        activeIngest.running = false;
      }
      let d={};
      try{ d=await r.json(); }catch(_){}
      panel.innerHTML='<p class=hint>재적재 요청 실패: '+esc(d.error||('HTTP '+r.status))+'</p>'; return;
    }
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
        if(ev.stage === 'heatmap_matrix' && ev.matrix && typeof renderHeatmapMatrix === 'function'){
          renderHeatmapMatrix(ev.matrix, null, true);
          continue;
        }
        if(ev.stage === 'decision' && typeof addDecisionStreamCard === 'function'){
          addDecisionStreamCard(ev);
          continue;
        }
        const cleanMsg = (ev.msg||'').replace(/^[•*-]\s*/, '');
        if(cleanMsg){
          if(typeof updateMatrixProgress === 'function') updateMatrixProgress(cleanMsg);
          if(activeIngest) activeIngest.messages.push(cleanMsg);
          const ul=document.getElementById('iprog');
          if(ul){
            const li=document.createElement('li');
            li.textContent=cleanMsg;
            ul.appendChild(li);
            ul.scrollTop=ul.scrollHeight;
          }
        }
      }
    }
  }catch(e){
    if(activeIngest){
      if(activeIngest.timer) clearInterval(activeIngest.timer);
      activeIngest.timer = null;
      activeIngest.running = false;
    }
    resumeGraphAfterIngest();
    panel.innerHTML='<p class=hint>재적재 스트림 오류: '+esc(String(e))+'</p>'; return;
  }
  if(activeIngest){
    if(activeIngest.timer) clearInterval(activeIngest.timer);
    activeIngest.timer = null;
    activeIngest.running = false;
  }
  resumeGraphAfterIngest();
  if(!result){
    panel.innerHTML='<p class=hint>재적재 결과가 수신되지 않았습니다.</p>';
    return;
  }
  renderIngestResult(result);
}

// --- 문서 컴포넌트 재생성 (Regenerate) ---
function openRegen(docIdOverride, compOverride){
  if(!canIngest()) return;
  if(activeIngest && activeIngest.running){
    renderIngestStatus();
    return;
  }
  const curDoc = (docIdOverride && allDocs && allDocs.find(d => d.id === docIdOverride)) ||
                 (allDocs && allDocs.find(d => d.id === activeDoc)) || curReaderDocData;
  const defTarget = (docIdOverride || (curDoc && (curDoc.id || curDoc.url || '')) || '');
  const defTitle = (curDoc && curDoc.title) ? esc(curDoc.title) : '';
  const isMulti = isMultiThemeEnabled();
  let themeFieldHtml = '';
  const curDocThemeId = (curDoc && curDoc.theme_id !== undefined && curDoc.theme_id !== null) ? curDoc.theme_id : activeThemeId;
  if(isMulti && availableThemes && availableThemes.length > 0){
    if(AUTH_SCOPE === 'collaborator'){
      const eligibleThemes = availableThemes.filter(t => t.id > 0 && t.is_collaborator_accessible !== false);
      if(eligibleThemes.length === 0){
        themeFieldHtml = '<div class="ingest-field"><p class="hint" style="color:var(--warn,#f39c12);margin:0;">⚠️ 재생성 가능한 협업 테마가 없습니다.</p></div>';
      } else {
        const selId = eligibleThemes.some(t => t.id === curDocThemeId) ? curDocThemeId : eligibleThemes[0].id;
        const opts = eligibleThemes.map(t => {
          const icon = t.icon || '📁';
          const label = esc(t.label);
          return `<option value="${t.id}" ${t.id === selId ? 'selected' : ''}>${icon} ${label}</option>`;
        }).join('');
        themeFieldHtml = ('<div class="ingest-field">'+
          '<label class="ingest-label" for="regentheme">재생성 대상 테마 <span class="ingest-help">협업이 허용된 지식 테마</span></label>'+
          '<select id="regentheme" class="ingest-theme-select">'+opts+'</select>'+
        '</div>');
      }
    } else if(availableThemes.length > 1){
      themeFieldHtml = ('<div class="ingest-field">'+
        '<label class="ingest-label" for="regentheme">재생성 대상 테마 <span class="ingest-help">지식 테마 선택</span></label>'+
        '<select id="regentheme" class="ingest-theme-select">'+
          renderThemeOptions(curDocThemeId)+
        '</select>'+
      '</div>');
    }
  }

  const selectedComp = compOverride || 'all';

  panel.innerHTML = '<h2>🔁 문서 컴포넌트 재생성</h2>'+
    '<p class="al">기존 원문(raw_text)을 기반으로 요약, 가독 본문(상세), 지식 노드/관계를 LLM으로 다시 생성합니다.</p>'+
    '<div class="ingest-form">'+
      themeFieldHtml +
      '<div class="ingest-field">'+
        '<label class="ingest-label" for="regentarget">재생성 대상 <span class="ingest-help">문서 ID, URL, 또는 공유 토큰</span></label>'+
        '<input type="text" id="regentarget" class="ingest-input" style="width:100%;box-sizing:border-box;padding:6px 8px;border:1px solid var(--border);border-radius:4px;background:var(--input-bg);color:var(--fg)" value="'+esc(defTarget)+'" placeholder="문서 ID (예: doc_...) 또는 URL">'+
        (defTitle ? '<small style="color:var(--muted);display:block;margin-top:3px">현재 선택된 문서: <b>'+defTitle+'</b></small>' : '')+
      '</div>'+
      '<fieldset class="ingest-options">'+
        '<legend class="ingest-label">재생성 컴포넌트</legend>'+
        '<div class="ingest-choice-grid">'+
          '<div class="ingest-choice"><input id="regencomp-all" type="radio" name="regen-comp" value="all" '+(selectedComp==='all'?'checked':'')+'><label for="regencomp-all" title="요약, 본문, 지식그래프를 모두 재생성합니다">전체 (All)</label></div>'+
          '<div class="ingest-choice"><input id="regencomp-detail" type="radio" name="regen-comp" value="detail" '+(selectedComp==='detail'?'checked':'')+'><label for="regencomp-detail" title="가독 본문(상세 렌더)만 재생성합니다">본문 상세 (Detail)</label></div>'+
          '<div class="ingest-choice"><input id="regencomp-summary" type="radio" name="regen-comp" value="summary" '+(selectedComp==='summary'?'checked':'')+'><label for="regencomp-summary" title="요약문만 LLM 재추출합니다">요약 (Summary)</label></div>'+
          '<div class="ingest-choice"><input id="regencomp-graph" type="radio" name="regen-comp" value="graph" '+(selectedComp==='graph'?'checked':'')+'><label for="regencomp-graph" title="엔티티와 지식 노드/관계만 재추출합니다">지식그래프 (Graph)</label></div>'+
        '</div>'+
      '</fieldset>'+
      '<fieldset class="ingest-options">'+
        '<legend class="ingest-label">사고 수준 <span class="ingest-help">미지정 시 서버 설정 사용</span></legend>'+
        '<div class="ingest-choice-grid effort">'+
          '<div class="ingest-choice"><input id="regeneffort-default" type="radio" name="regen-effort" value="" checked><label for="regeneffort-default">기본</label></div>'+
          '<div class="ingest-choice"><input id="regeneffort-none" type="radio" name="regen-effort" value="none"><label for="regeneffort-none">없음</label></div>'+
          '<div class="ingest-choice"><input id="regeneffort-minimal" type="radio" name="regen-effort" value="minimal"><label for="regeneffort-minimal">최소</label></div>'+
          '<div class="ingest-choice"><input id="regeneffort-low" type="radio" name="regen-effort" value="low"><label for="regeneffort-low">낮음</label></div>'+
          '<div class="ingest-choice"><input id="regeneffort-medium" type="radio" name="regen-effort" value="medium"><label for="regeneffort-medium">중간</label></div>'+
          '<div class="ingest-choice"><input id="regeneffort-high" type="radio" name="regen-effort" value="high"><label for="regeneffort-high">높음</label></div>'+
          '<div class="ingest-choice"><input id="regeneffort-max" type="radio" name="regen-effort" value="max"><label for="regeneffort-max">최대</label></div>'+
        '</div>'+
      '</fieldset>'+
      '<div class="ingest-field">'+
        '<label class="ingest-label" for="regenfocus">상세 초점 (Focus) <span class="ingest-help">선택 사항 — 특정 관점 중심 기술</span></label>'+
        '<textarea id="regenfocus" rows="2" placeholder="예: 시스템 아키텍처 및 내부 동작 구조 중심"></textarea>'+
      '</div>'+
      '<div class="ingest-field" style="margin-top:6px">'+
        '<label style="display:flex;align-items:center;gap:6px;font-size:12px;cursor:pointer">'+
          '<input type="checkbox" id="regenrefetch"> '+
          '<span>원본 웹 URL에서 최신 원문 재수집 후 재생성 (Refetch)</span>'+
        '</label>'+
      '</div>'+
      '<div class="action-btn-row" style="margin-top:12px;gap:8px">'+
        '<button type="button" class="sec" onclick="runRegen(true)" style="padding:6px 12px;cursor:pointer"><span class="btn-icon">🔍</span> <span class="btn-label">영향 분석 (Dry-Run)</span></button>'+
        '<button type="button" class="readbtn" onclick="runRegen(false)" style="background:var(--accent,#2da44e);color:#fff;border:none;padding:6px 12px;cursor:pointer;border-radius:4px"><span class="btn-icon">🔁</span> <span class="btn-label">재생성 시작</span></button>'+
      '</div>'+
      '<div id="regenresult" style="margin-top:14px"></div>'+
    '</div>';
  openDetailPane();
  const inp=document.getElementById('regentarget'); if(inp) inp.focus();
}

async function runRegen(isDryRun){
  if(!canIngest()) return;
  if(!isDryRun && activeIngest && activeIngest.running){
    alert('이미 적재, 재적재 또는 재생성이 진행 중입니다.');
    renderIngestStatus();
    return;
  }
  const isMulti = isMultiThemeEnabled();
  const ta = document.getElementById('regentarget');
  const target = ((ta||{}).value||'').trim();
  if(!target){ alert('재생성할 대상(문서 ID 또는 URL)을 입력하세요.'); return; }
  const regenthemeEl = document.getElementById('regentheme');
  const targetThemeId = (isMulti && regenthemeEl) ? parseInt(regenthemeEl.value, 10) : (isMulti ? activeThemeId : 0);
  if(AUTH_SCOPE === 'collaborator'){
    if(!targetThemeId || targetThemeId === 0){
      alert('기본 지식베이스(ID 0)에는 협업자 권한으로 재생성할 수 없습니다. 협업이 허용된 추가 지식 테마를 선택하세요.');
      return;
    }
  }
  const compChoice = document.querySelector('input[name="regen-comp"]:checked');
  const component = compChoice ? compChoice.value : 'all';
  const effortChoice = document.querySelector('input[name="regen-effort"]:checked');
  const effort = effortChoice ? effortChoice.value : '';
  const focus = ((document.getElementById('regenfocus')||{}).value||'').trim();
  const refetchChk = document.getElementById('regenrefetch');
  const refetch = !!(refetchChk && refetchChk.checked);

  const resDiv = document.getElementById('regenresult');

  if(isDryRun){
    if(resDiv) resDiv.innerHTML = '<p class="al">⏳ 재생성 영향 분석 중…</p>';
    try{
      const payload = {
        target: target,
        component: component,
        dry_run: true,
        refetch: refetch,
      };
      if(effort) payload.effort = effort;
      if(focus) payload.focus = focus;
      if(isMulti && targetThemeId !== undefined && targetThemeId !== null){
        payload.theme = targetThemeId;
      }
      const r = await fetch('regenerate', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(payload),
      });
      if(r.status === 401 || r.status === 404){
        expireWriteAccess();
        if(resDiv) resDiv.innerHTML = '<p class="hint">세션 만료 — 텔레그램 /web 으로 다시 접속하세요</p>';
        return;
      }
      const d = await r.json();
      if(!r.ok || d.error){
        if(resDiv) resDiv.innerHTML = '<p class="hint">영향 분석 실패: ' + esc(d.error || ('HTTP ' + r.status)) + '</p>';
        return;
      }
      let h = '<div style="padding:10px;background:var(--card-bg);border:1px solid var(--border);border-radius:6px">';
      h += '<h3 style="margin:0 0 6px">🔍 재생성 영향 분석 결과 (Dry-Run)</h3>';
      const tgts = d.targets || [];
      h += '<p class="al">진단 대상 문서: <b>' + (d.count || tgts.length) + '건</b> · 컴포넌트: <b>' + esc(component) + '</b></p>';
      if(tgts.length > 0){
        h += '<ul style="margin:6px 0;padding-left:18px;font-size:12px">';
        tgts.forEach(t => {
          const acts = (t.actions || []).join(', ');
          h += '<li><b>' + esc(t.title || t.document_id) + '</b> <small class="al">(' + esc(t.document_id) + ')</small>';
          if(acts) h += '<br><span style="color:var(--muted)">적용 예정 작업: ' + esc(acts) + '</span>';
          if(t.current_summary) h += '<br><span style="color:var(--muted)">현재 요약: ' + esc(t.current_summary.slice(0, 100)) + (t.current_summary.length > 100 ? '…' : '') + '</span>';
          h += '</li>';
        });
        h += '</ul>';
        h += '<button type="button" class="readbtn" onclick="runRegen(false)" style="margin-top:8px;background:var(--accent,#2da44e);color:#fff;border:none;padding:5px 10px;border-radius:4px;cursor:pointer">위 대상을 확인하고 재생성 실행</button>';
      } else {
        h += '<p class="hint">일치하는 문서를 찾을 수 없습니다.</p>';
      }
      h += '</div>';
      if(resDiv) resDiv.innerHTML = h;
    }catch(e){
      if(resDiv) resDiv.innerHTML = '<p class="hint">분석 요청 오류: ' + esc(String(e)) + '</p>';
    }
    return;
  }

  // 실제 재생성 실행 (스트리밍)
  let labelText = '재생성 시작…';
  const optionLabels = [];
  const targetThemeObj = isMulti ? availableThemes.find(t => t.id === targetThemeId) : null;
  if(targetThemeObj && targetThemeObj.id !== 0){
    optionLabels.push('테마: ' + (targetThemeObj.icon ? targetThemeObj.icon + ' ' : '') + targetThemeObj.label);
  }
  optionLabels.push('컴포넌트: ' + component);
  if(effort) optionLabels.push('사고: ' + effort);
  if(focus) optionLabels.push('초점: ' + (focus.length > 20 ? focus.slice(0,20)+'…' : focus));
  if(refetch) optionLabels.push('원문 재수집');
  if(optionLabels.length){
    labelText += ' (' + optionLabels.join(' · ') + ')';
  }
  activeIngest = {
    running: true,
    t0: Date.now(),
    labelText: labelText,
    optionLabels: optionLabels,
    messages: [],
    timer: null,
  };
  suspendGraphForIngest();
  renderIngestStatus();
  activeIngest.timer = setInterval(updateIngestElapsed, 1000);

  const payload = {
    target: target,
    component: component,
    dry_run: false,
    refetch: refetch,
  };
  if(effort) payload.effort = effort;
  if(focus) payload.focus = focus;
  if(isMulti && targetThemeId !== undefined && targetThemeId !== null){
    payload.theme = targetThemeId;
  }

  let result = null;
  try{
    const r = await fetch('regenerate-stream', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload),
    });
    if(r.status === 401 || r.status === 404){
      expireWriteAccess();
      if(activeIngest){
        if(activeIngest.timer) clearInterval(activeIngest.timer);
        activeIngest.timer = null;
        activeIngest.running = false;
      }
      resumeGraphAfterIngest();
      panel.innerHTML = '<p class="hint">세션 만료 — 텔레그램 /web 으로 다시 접속하세요</p>';
      return;
    }
    if(!r.ok){
      throw new Error('HTTP ' + r.status);
    }
    const reader = r.body.getReader();
    const dec = new TextDecoder('utf-8');
    let buf = '';
    while(true){
      const {done, value} = await reader.read();
      if(done) break;
      buf += dec.decode(value, {stream: true});
      const lines = buf.split('\n');
      buf = lines.pop();
      for(const line of lines){
        const trimmed = line.trim();
        if(!trimmed) continue;
        let ev;
        try{ ev = JSON.parse(trimmed); }catch(_){ continue; }
        if(ev.done){
          result = ev.result;
          continue;
        }
        const cleanMsg = (ev.msg||'').replace(/^[•*-]\s*/, '');
        if(cleanMsg){
          if(typeof updateMatrixProgress === 'function') updateMatrixProgress(cleanMsg);
          if(activeIngest) activeIngest.messages.push(cleanMsg);
          const ul = document.getElementById('iprog');
          if(ul){
            const li = document.createElement('li');
            li.textContent = cleanMsg;
            ul.appendChild(li);
            ul.scrollTop = ul.scrollHeight;
          }
        }
      }
    }
  }catch(e){
    if(activeIngest){
      if(activeIngest.timer) clearInterval(activeIngest.timer);
      activeIngest.timer = null;
      activeIngest.running = false;
    }
    resumeGraphAfterIngest();
    panel.innerHTML = '<p class="hint">재생성 스트림 오류: ' + esc(String(e)) + '</p>';
    return;
  }
  if(activeIngest){
    if(activeIngest.timer) clearInterval(activeIngest.timer);
    activeIngest.timer = null;
    activeIngest.running = false;
  }
  resumeGraphAfterIngest();
  if(!result){
    panel.innerHTML = '<p class="hint">재생성 결과가 수신되지 않았습니다.</p>';
    return;
  }

  // 결과 화면 표시 및 지식그래프 갱신
  if(result.error){
    panel.innerHTML = '<p class="hint">재생성 실패: ' + esc(result.error) + '</p>';
    return;
  }
  let h = '<h2>✅ 컴포넌트 재생성 완료</h2>';
  const tgts = result.targets || [];
  h += '<div style="padding:10px;background:var(--card-bg);border:1px solid var(--border);border-radius:6px">';
  h += '<p class="al">갱신 완료 문서: <b>' + (result.count || tgts.length) + '건</b></p>';
  if(tgts.length > 0){
    h += '<ul style="margin:6px 0;padding-left:18px;font-size:12px">';
    tgts.forEach(t => {
      h += '<li><b>' + esc(t.title || t.document_id) + '</b> <small class="al">(' + esc(t.document_id) + ')</small>';
      if(t.entities_created !== undefined || t.entities_linked !== undefined){
        h += '<br><span style="color:var(--muted)">지식 노드 신규: ' + (t.entities_created||0) + ' · 기존 연결: ' + (t.entities_linked||0) + ' · 관계: ' + (t.relations_added||0) + '</span>';
      }
      if(t.new_summary){
        h += '<br><span style="color:var(--muted)">새 요약: ' + esc(t.new_summary.slice(0, 120)) + (t.new_summary.length > 120 ? '…' : '') + '</span>';
      }
      h += '</li>';
    });
    h += '</ul>';
  }
  h += '</div>';
  panel.innerHTML = h;

  await Promise.all([
    reloadThemeData(true),
    loadThemeDocuments()
  ]);
  const affectedIds = tgts.map(t => t.document_id);
  if(activeDoc && affectedIds.includes(activeDoc)){
    openReader(activeDoc);
  }
}

function openPurge(){
  if(AUTH_SCOPE !== 'owner'){
    alert('소각(Purge) 권한은 소유자(owner) 전용입니다.');
    return;
  }
  const curDoc = (allDocs && allDocs.find(d => d.id === activeDoc)) || curReaderDocData;
  const defTarget = (curDoc && (curDoc.id || curDoc.url || '')) || '';
  const defTitle = (curDoc && curDoc.title) ? esc(curDoc.title) : '';
  const isMulti = isMultiThemeEnabled();
  let themeFieldHtml = '';
  if(isMulti && availableThemes && availableThemes.length > 1){
    themeFieldHtml = ('<div class="ingest-field">'+
      '<label class="ingest-label" for="purgetheme">소각 대상 테마 <span class="ingest-help">테마 데이터베이스 선택</span></label>'+
      '<select id="purgetheme" class="ingest-theme-select">'+
        renderThemeOptions(activeThemeId)+
      '</select>'+
    '</div>');
  }

  panel.innerHTML = '<h2>🔥 문서 소각 (Purge)</h2>'+
    '<div style="margin:0 0 10px;padding:8px 10px;background:rgba(235,87,87,0.12);border:1px solid rgba(235,87,87,0.4);border-radius:6px;font-size:12px;color:var(--fg);line-height:1.45">'+
      '⚠️ <b>영구 파괴 작업 주의</b><br>문서 데이터, 원문 아티팩트 파일, 지식그래프 고아 엔티티와 관계가 영구 소각되고 DB 물리 압축(VACUUM)이 일괄 수행됩니다.'+
    '</div>'+
    '<div class="ingest-form">'+
      themeFieldHtml +
      '<div class="ingest-field">'+
        '<label class="ingest-label" for="purgetarget">소각 대상 <span class="ingest-help">문서 ID, URL, 또는 검색어</span></label>'+
        '<input type="text" id="purgetarget" class="ingest-input" style="width:100%;box-sizing:border-box;padding:6px 8px;border:1px solid var(--border);border-radius:4px;background:var(--input-bg);color:var(--fg)" value="'+esc(defTarget)+'" placeholder="문서 ID, URL 또는 검색어">'+
        (defTitle ? '<small style="color:var(--muted);display:block;margin-top:3px">현재 선택된 문서: <b>'+defTitle+'</b></small>' : '')+
      '</div>'+
      '<div class="ingest-field">'+
        '<label class="ingest-label" for="purgereason">소각 사유 <span class="ingest-help">툼스톤 기록 사유</span></label>'+
        '<input type="text" id="purgereason" class="ingest-input" style="width:100%;box-sizing:border-box;padding:6px 8px;border:1px solid var(--border);border-radius:4px;background:var(--input-bg);color:var(--fg)" value="web_purge" placeholder="manual_purge">'+
      '</div>'+
      '<div class="ingest-field" style="margin-top:6px">'+
        '<label style="display:flex;align-items:center;gap:6px;font-size:12px;cursor:pointer">'+
          '<input type="checkbox" id="purgetombstone" checked> '+
          '<span>재유입 방지 툼스톤(Tombstone) 등록 <small style="color:var(--muted)">(동일 URL/해시의 재적재 영구 차단)</small></span>'+
        '</label>'+
      '</div>'+
      '<div class="action-btn-row" style="margin-top:12px;gap:8px">'+
        '<button type="button" class="sec" onclick="runPurgeCheck(true)" style="padding:6px 12px;cursor:pointer"><span class="btn-icon">🔍</span> <span class="btn-label">영향 분석 (Dry-Run)</span></button>'+
        '<button type="button" class="readbtn" onclick="runPurgeCheck(false)" style="background:#e03131;color:#fff;border:none;padding:6px 12px;cursor:pointer;border-radius:4px"><span class="btn-icon">🔥</span> <span class="btn-label">소각 실행</span></button>'+
      '</div>'+
      '<div id="purgeresult" style="margin-top:14px"></div>'+
    '</div>';
  openDetailPane();
  const inp=document.getElementById('purgetarget'); if(inp) inp.focus();
}

async function runPurgeCheck(isDryRun){
  if(AUTH_SCOPE !== 'owner'){
    alert('소각(Purge) 권한은 소유자(owner) 전용입니다.');
    return;
  }
  const isMulti = isMultiThemeEnabled();
  const targetInp = document.getElementById('purgetarget');
  const target = ((targetInp||{}).value||'').trim();
  if(!target){
    alert('소각할 대상(문서 ID 또는 URL)을 입력하세요.');
    return;
  }
  const reasonInp = document.getElementById('purgereason');
  const reason = ((reasonInp||{}).value||'').trim() || 'web_purge';
  const tombstoneChk = document.getElementById('purgetombstone');
  const tombstone = !!(tombstoneChk && tombstoneChk.checked);
  const themeEl = document.getElementById('purgetheme');
  const targetThemeId = (isMulti && themeEl) ? parseInt(themeEl.value, 10) : (isMulti ? activeThemeId : 0);

  if(!isDryRun){
    if(!confirm('정말 대상 문서를 영구 소각하시겠습니까?\n이 작업은 되돌릴 수 없으며 원본 문서와 파일, 관련 고아 지식 노드가 즉시 파괴됩니다.')){
      return;
    }
  }

  const resDiv = document.getElementById('purgeresult');
  if(resDiv){
    resDiv.innerHTML = '<p class="al">⏳ 소각 ' + (isDryRun ? '영향 분석 중…' : '실행 중…') + '</p>';
  }

  try {
    const payload = {
      target: target,
      dry_run: isDryRun,
      tombstone: tombstone,
      reason: reason,
    };
    if(isMulti && targetThemeId !== undefined && targetThemeId !== null && targetThemeId !== 0){
      payload.theme = targetThemeId;
    }
    const r = await fetch('document/purge', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload),
    });
    if(r.status === 401 || r.status === 404){
      expireWriteAccess();
      if(resDiv) resDiv.innerHTML = '<p class="hint">세션 만료 — 텔레그램 /web 으로 다시 접속하세요</p>';
      return;
    }
    const d = await r.json();
    if(!r.ok || d.error){
      if(resDiv) resDiv.innerHTML = '<p class="hint">소각 처리 실패: ' + esc(d.error || ('HTTP ' + r.status)) + '</p>';
      return;
    }

    if(isDryRun){
      let h = '<div style="padding:10px;background:var(--card-bg);border:1px solid var(--border);border-radius:6px">';
      h += '<h3 style="margin:0 0 6px">🔍 소각 영향 분석 결과 (Dry-Run)</h3>';
      const tgts = d.targets || [];
      h += '<p class="al">소각 대상 문서: <b>' + tgts.length + '건</b> · 디스크 파일: <b>' + (d.disk_files_count || 0) + '개</b></p>';
      h += '<p class="al">툼스톤 등록: <b>' + (tombstone ? '등록 (재적재 차단)' : '미등록 (재적재 허용)') + '</b></p>';
      if(tgts.length > 0){
        h += '<ul style="margin:6px 0;padding-left:18px;font-size:12px">';
        tgts.forEach(t => {
          h += '<li><b>' + esc(t.title || t.id) + '</b> <small class="al">(' + esc(t.id) + ')</small></li>';
        });
        h += '</ul>';
        h += '<button type="button" class="readbtn" onclick="runPurgeCheck(false)" style="margin-top:8px;background:#e03131;color:#fff;border:none;padding:5px 10px;border-radius:4px;cursor:pointer">위 대상을 확인하고 소각 실행</button>';
      } else {
        h += '<p class="hint">일치하는 문서를 찾을 수 없습니다.</p>';
      }
      h += '</div>';
      if(resDiv) resDiv.innerHTML = h;
    } else {
      let h = '<div style="padding:10px;background:var(--card-bg);border:1px solid var(--border);border-radius:6px">';
      h += '<h3 style="margin:0 0 6px">✅ 영구 소각 완료</h3>';
      h += '<p class="al">소각된 문서: <b>' + (d.deleted_documents || 0) + '건</b> · 삭제된 파일: <b>' + (d.disk_files_unlinked || 0) + '개</b></p>';
      if(d.purged_count){
        h += '<p class="al">등록된 툼스톤: <b>' + d.purged_count + '건</b></p>';
      }
      const gh = d.graph_healed || {};
      if(gh.ghost_entities_pruned || gh.dangling_relations_removed){
        h += '<p class="al">지식그래프 수복: 고아 노드 <b>' + (gh.ghost_entities_pruned || 0) + '개</b> · 고아 관계 <b>' + (gh.dangling_relations_removed || 0) + '개</b> 정리</p>';
      }
      h += '</div>';
      if(resDiv) resDiv.innerHTML = h;

      const purgedIds = (d.targets || []).map(t => t.id);
      if(activeDoc && purgedIds.includes(activeDoc)){
        activeDoc = null;
        curReaderDocData = null;
      }
      refreshGraph();
    }
  } catch(e) {
    if(resDiv) resDiv.innerHTML = '<p class="hint">소각 요청 오류: ' + esc(String(e)) + '</p>';
  }
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
        (isWebUrl(dc.url)?'<br><small class=al style="margin-left:1.5em;word-break:break-all">'+esc(dc.url)+'</small>':'')+
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
  if(graphSuspended) return;
  fetch('graph').then(r=>r.json()).then(d=>{
    if(graphSuspended) return;
    if(d && d.nodes && d.edges) applyTopologicalStructure(d.nodes, d.edges);
    const changed=[], added=[];
    d.nodes.forEach(n=>{
      const r = nodeRadius(n.degree), fs = nodeFontSize(n.degree);
      if(allNodes && allNodes.get(n.id))
        changed.push({id:n.id, degree:n.degree, size:r, font:{size:fs}, sources:n.sources, obs:n.obs, ...(n.physics===false?{orbit:n.orbit,physics:false}:{orbit:null,physics:true})});
      else if(allNodes)
        added.push({...n, size:r, font:{size:fs}});
    });
    if(allNodes && changed.length) allNodes.update(changed);
    if(allNodes && added.length) allNodes.add(added);
    if(allEdges) d.edges.forEach(e=>{ if(!allEdges.get(e.id)) allEdges.add(e); });
    const fslider = document.getElementById('fslider');
    if(fslider && d.stats) fslider.max = d.stats.max_degree;
    updateDegPresets();
    applyView();
    if(activeDoc && curReaderDocData && curReaderDocData.id === activeDoc){
      renderDocPanel(curReaderDocData);
    }
  });
  fetch('documents').then(r=>r.json()).then(d=>{
    allDocs=d.documents||[];
    const docq = document.getElementById('docq');
    renderDocs(docq ? docq.value : '');
  });
}

// 전체 리로드 없이 새 글/엔티티/관계 반영 및 적재 상태 추적 폴링
let pollTimer = null;
let currentPollInterval = 25000;
let isFastPolling = false;
let trackingIngest = false;

function scheduleNextPoll(delayMs){
  if(pollTimer) clearTimeout(pollTimer);
  pollTimer = setTimeout(pollForUpdates, delayMs !== undefined ? delayMs : currentPollInterval);
}

function startFastIngestPolling(){
  isFastPolling = true;
  trackingIngest = true;
  currentPollInterval = 1500;
  scheduleNextPoll(1500);
}

function stopFastIngestPolling(){
  isFastPolling = false;
  trackingIngest = false;
  currentPollInterval = 25000;
  scheduleNextPoll(25000);
}

let activeMatrixInitialized = false;

function handleActiveIngestState(activeInfo){
  if(!activeInfo) return;
  if(!activeMatrixInitialized && typeof initIngestMatrixView === 'function'){
    initIngestMatrixView(activeInfo.payload, activeInfo.focus, activeInfo.theme_label);
    activeMatrixInitialized = true;
  }
  const spin = document.getElementById('matrix-progress-spinner');
  if(spin && activeInfo.active) spin.style.display = 'inline-block';
  if(activeInfo.title){
    const docMetaEl = document.getElementById('matrix-target-doc');
    if(docMetaEl) docMetaEl.textContent = '대조 대상: ' + activeInfo.title;
  }
  if(activeInfo.msg){
    updateMatrixProgress(activeInfo.msg);
  }
  if(activeInfo.heatmap_matrix){
    renderHeatmapMatrix(activeInfo.heatmap_matrix, activeInfo.title, true);
  }
}

function handleActiveIngestCompleted(activeInfo){
  activeMatrixInitialized = false;
  stopFastIngestPolling();
  const spin = document.getElementById('matrix-progress-spinner');
  if(spin) spin.style.display = 'none';
  const msgEl = document.getElementById('matrix-progress-msg');

  if(activeInfo && activeInfo.result){
    const result = activeInfo.result;
    if(result.heatmap_matrix && result.document_id){
      try {
        sessionStorage.setItem('doc_matrix_' + result.document_id, JSON.stringify(result.heatmap_matrix));
      } catch(_) {}
      window.latestHeatmapMatrix = result.heatmap_matrix;
      window.latestHeatmapDocId = result.document_id;
      window.latestHeatmapDocTitle = result.title || activeInfo.title || '';
    }
    completeIngestMatrixView(result);
  } else if(activeInfo && activeInfo.stage === 'error'){
    if(msgEl) msgEl.innerHTML = '❌ <strong>' + esc(activeInfo.msg || '적재 중 오류 발생') + '</strong>';
  } else {
    if(msgEl) msgEl.innerHTML = '✅ <strong>대조 및 적재 완료</strong>';
  }
  if(activeInfo && activeInfo.heatmap_matrix){
    renderHeatmapMatrix(activeInfo.heatmap_matrix, activeInfo.title, false);
  }
}

let lastStatsSig = null;
async function pollForUpdates(){
  try{
    const r = await fetch('stats');
    if(!r.ok){ scheduleNextPoll(); return; }
    const d = await r.json();
    activeJobs = extractActiveJobs(d);
    const hasRunningJobs = activeJobs.some(j => j.active);

    // 최신 active_ingest 정보가 있으면 매트릭스 캐시 동기화
    if(d.active_ingest && d.active_ingest.heatmap_matrix){
      const docId = (d.active_ingest.result && d.active_ingest.result.document_id) || d.active_ingest.heatmap_matrix.document_id || null;
      if(docId){
        try {
          sessionStorage.setItem('doc_matrix_' + docId, JSON.stringify(d.active_ingest.heatmap_matrix));
        } catch(_) {}
      }
      window.latestHeatmapMatrix = d.active_ingest.heatmap_matrix;
      window.latestHeatmapDocId = docId;
      window.latestHeatmapDocTitle = d.active_ingest.title || '';
    }

    if(hasRunningJobs){
      if(!trackingIngest){
        trackingIngest = true;
        suspendGraphForIngest();
        setCenterView('matrix');
        startFastIngestPolling();
      }
      renderMultiSlotUI(activeJobs);
      scheduleNextPoll(1500);
      return;
    }

    if(trackingIngest){
      trackingIngest = false;
      stopFastIngestPolling();
      renderMultiSlotUI(activeJobs);
      if(d.active_ingest){
        handleActiveIngestCompleted(d.active_ingest);
      }
      lastStatsSig = [d.documents, d.entities, d.relations].join(':');
      scheduleNextPoll();
      return;
    }

    // trackingIngest 상태가 아니었으나 matrix view가 열려있고 적재가 진행 중이 아닌 경우, 스피너 및 완료 상태 정리
    if(typeof centerView !== 'undefined' && centerView === 'matrix' && !hasRunningJobs){
      if(activeJobs.length > 0){
        renderMultiSlotUI(activeJobs);
      } else {
        const container = document.getElementById('job-slots-container');
        if(container) container.style.display = 'none';
        const spin = document.getElementById('matrix-progress-spinner');
        if(spin && spin.style.display !== 'none') spin.style.display = 'none';
        const msgEl = document.getElementById('matrix-progress-msg');
        if(msgEl && (!msgEl.textContent || msgEl.textContent.includes('준비 중') || msgEl.textContent.includes('진행 중'))){
          msgEl.innerHTML = '✅ <strong>대조 완료</strong> (온톨로지 대조 밀도 계산 완료)';
        }
      }
    }

    const sig = [d.documents, d.entities, d.relations].join(':');
    if(lastStatsSig === null){
      lastStatsSig = sig;
      scheduleNextPoll();
      return;
    }
    if(sig !== lastStatsSig){
      lastStatsSig = sig;
      if(!graphSuspended){
        refreshGraph();
      }
      if(activeDoc){
        fetch('document?id='+encodeURIComponent(activeDoc)).then(x=>x.json()).then(dc=>{
          if(dc && !dc.error && activeDoc===dc.id){
            curReaderDocData=dc;
            renderDocPanel(dc);
          }
        }).catch(()=>{});
      }
    }
  }catch(e){
    /* network retry */
  }
  scheduleNextPoll();
}
scheduleNextPoll(25000);

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

// 그래프 엔진의 월드 반경(px). 화면 반경은 확대율^0.6 로 완만히 커져, 축소 시엔 Shopify 식의
// 촘촘한 점 성운이 되고 확대하면 개별 노드가 또렷해진다.
function nodeRadius(deg){
  const d = deg || 0;
  if(d <= 1) return 3;
  if(d === 2) return 3.5;
  return Math.min(9, Math.round((3.5 + Math.sqrt(d) * 0.55) * 10) / 10);
}

function nodeFontSize(deg){
  const d = deg || 0;
  if(d <= 1) return 11;
  if(d === 2) return 12;
  if(d >= 8) return Math.min(18, Math.round(12 + Math.log2(d + 1) * 1.3));
  return Math.min(15, Math.round(11 + Math.log2(d + 1)));
}

function applyTopologicalStructure(nodes, edges){
  if(!Array.isArray(nodes)) return;
  const degMap = new Map();
  const adj = new Map();
  nodes.forEach(n => {
    degMap.set(n.id, n.degree || 0);
    adj.set(n.id, new Set());
  });
  (edges || []).forEach(e => {
    if(adj.has(e.from) && adj.has(e.to)){
      adj.get(e.from).add(e.to);
      adj.get(e.to).add(e.from);
    }
  });

  const isolatedNodes = [];
  const dyadPairs = [];
  const visitedDyads = new Set();

  nodes.forEach(n => {
    const deg = degMap.get(n.id) || 0;
    if(deg === 0){
      isolatedNodes.push(n);
    } else if(deg === 1){
      const neighbors = [...(adj.get(n.id) || [])];
      if(neighbors.length === 1){
        const partnerId = neighbors[0];
        const partnerDeg = degMap.get(partnerId) || 0;
        if(partnerDeg === 1 && !visitedDyads.has(n.id) && !visitedDyads.has(partnerId)){
          visitedDyads.add(n.id);
          visitedDyads.add(partnerId);
          dyadPairs.push([n.id, partnerId]);
        }
      }
    }
  });

  isolatedNodes.sort((a, b) => {
    const gA = a.group || '', gB = b.group || '';
    if(gA !== gB) return gA.localeCompare(gB);
    return (a.label || '').localeCompare(b.label || '');
  });

  // 고립·2노드 쌍은 물리에서 빼고 별 무리 바깥 궤도에 둔다. 앱은 띠(0: 2노드 쌍, 1: 고립)와 순서만
  // 정하고, 반경은 엔진이 실제 별 무리 경계에 맞춰 잡는다(필터로 무리가 줄면 궤도도 따라 줄어든다).
  isolatedNodes.forEach((n, idx) => {
    n.orbit = { band: 1, index: idx };
    n.physics = false;
  });

  const nodeById = new Map(nodes.map(n => [n.id, n]));
  dyadPairs.forEach(([idA, idB], idx) => {
    const nA = nodeById.get(idA);
    const nB = nodeById.get(idB);
    if(nA){
      nA.orbit = { band: 0, index: idx, side: -1 };
      nA.physics = false;
    }
    if(nB){
      nB.orbit = { band: 0, index: idx, side: 1 };
      nB.physics = false;
    }
  });
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

  let isolatedSet = null;
  if(isolatedNodeId && net && typeof net.getConnectedNodes === 'function'){
    try{
      const rawConn = net.getConnectedNodes(isolatedNodeId);
      isolatedSet = new Set(Array.isArray(rawConn) ? rawConn : []);
      isolatedSet.add(isolatedNodeId);
    }catch(_){
      isolatedSet = new Set([isolatedNodeId]);
    }
  }

  let selectedNeighbors = null;
  if(selectedNodeId && net && typeof net.getConnectedNodes === 'function'){
    try{
      const rawN = net.getConnectedNodes(selectedNodeId);
      selectedNeighbors = new Set(Array.isArray(rawN) ? rawN : []);
      selectedNeighbors.add(selectedNodeId);
    }catch(_){
      selectedNeighbors = new Set([selectedNodeId]);
    }
  }

  const nodeUpdates=[], matchedNodes=new Set();
  allNodes.forEach(n=>{
    if(typeof n.id==='string' && n.id.indexOf('cl_')===0) return;  // 검색 중앙 앵커는 안 건드림(숨김 유지)
    if(isolatedSet && !isolatedSet.has(n.id)){ nodeUpdates.push({id:n.id, hidden:true}); return; }
    if(n.degree < curMinDeg && (!isolatedSet || !isolatedSet.has(n.id))){ nodeUpdates.push({id:n.id, hidden:true}); return; }
    let match = true;
    if(pathActive){ match = pathNodes.has(n.id); }   // 경로 모드: 경로 노드만 강조(다른 필터보다 우선)
    else {
      if(activeDoc) match = match && (n.sources||[]).includes(activeDoc);
      if(highlightSet) match = match && highlightSet.has(n.id);  // 검색(라벨/의미) 강조 집합
    }
    // 강조(문서 선택·검색·경로) 매치 노드는 흰 굵은 테두리 — dim 만으론 안 띄어서(피드백).
    // 노드별 color 가 group 색을 덮으므로 background/highlight 를 같이 명시해 유지한다.
    const lit = (hasFilter && match) || (selectedNodeId && n.id === selectedNodeId);
    const isNeighbor = selectedNeighbors && selectedNeighbors.has(n.id);
    const isLandmark = (n.degree >= 8);
    const c = TYPE_COLORS[n.group]||'#8b949e';
    if(match) matchedNodes.add(n.id);
    const r = nodeRadius(n.degree);
    const fs = nodeFontSize(n.degree);

    let nodeOpacity = 1;
    let nodeBorderWidth = 1;
    let nodeBorderColor = th.nodeBorder;
    let nodeFs = fs;

    if(selectedNodeId && selectedNeighbors){
      if(n.id === selectedNodeId){
        nodeOpacity = 1;
        nodeBorderWidth = 3;
        nodeBorderColor = th.lit;
        nodeFs = Math.max(fs, 14);
      } else if(isNeighbor){
        nodeOpacity = 0.95;
        nodeBorderWidth = 2;
        nodeBorderColor = th.lit;
        nodeFs = Math.max(fs, 12);
      } else {
        nodeOpacity = 0.12;
        nodeBorderWidth = 1;
        nodeBorderColor = th.nodeBorder;
        nodeFs = 0;
      }
    } else if(hasFilter){
      nodeOpacity = match ? 1 : DIM;
      nodeBorderWidth = lit ? 3 : 1;
      nodeBorderColor = lit ? th.lit : th.nodeBorder;
      nodeFs = match ? fs : 0;
    } else {
      nodeOpacity = 1;
      nodeBorderWidth = 1;
      nodeBorderColor = th.nodeBorder;
      nodeFs = isLandmark ? Math.max(fs, 14) : fs;
    }

    nodeUpdates.push({id:n.id, hidden:false, size:r,
      font:{size:nodeFs, color:th.nodeFont},
      opacity: nodeOpacity, borderWidth: lit?3:1,
      color:{background:c, border: nodeBorderColor,
             highlight:{background:c, border:th.lit},
             hover:{background:c, border: lit?th.lit:th.nodeBorder}}});
    shown++; if(match) emph++;
  });
  if(nodeUpdates.length) allNodes.update(nodeUpdates);  // 1회 배치(개별 호출 대신)

  // 엣지 가시성은 방금 갱신된 노드 hidden 상태를 봐야 하므로 노드 배치 반영 뒤에 계산.
  // 줌 레벨 기반 엣지 Level of Detail (LOD)
  const idle = idleEdgeStyle(net ? net.getScale() : 0.1);
  const defaultEdgeOpacity = idle.opacity;
  const defaultEdgeWidth = idle.width;

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

    let edgeOpacity = defaultEdgeOpacity;
    let edgeWidth = defaultEdgeWidth;
    let edgeColor = th.edge;
    let edgeHighlight = th.edgeHi;
    let edgeInherit = false;

    if(onPath){
      edgeOpacity = 1;
      edgeWidth = 4;
      edgeColor = th.lit;
      edgeHighlight = th.lit;
    } else if(selectedNodeId){
      if(incident || selected){
        edgeOpacity = 1;
        edgeWidth = 2.5;
        edgeColor = th.lit;
        edgeHighlight = th.edgeHi;
      } else {
        edgeOpacity = 0.04;
        edgeWidth = 0.5;
        edgeColor = th.edge;
      }
    } else if(hasFilter){
      if(contextEdge){
        edgeOpacity = 0.45;
        edgeWidth = 1.2;
        edgeColor = th.edge;
      } else {
        edgeOpacity = 0.05;
        edgeWidth = 0.5;
        edgeColor = th.edge;
      }
    } else {
      edgeOpacity = defaultEdgeOpacity;
      edgeWidth = defaultEdgeWidth;
      edgeColor = th.edge;
      edgeHighlight = th.edgeHi;
      edgeInherit = true;
    }

    const labelOn = onPath || incident || selected || edgeLabelsByZoom;
    edgeUpdates.push({id:e.id, hidden: !visible, width:edgeWidth,
      color: {color:edgeColor, highlight:edgeHighlight, opacity:edgeOpacity, inherit:edgeInherit},
      font:{size:labelOn?10:0,color:th.nodeFont,strokeWidth:3,
        strokeColor:netBg}});
  });
  if(edgeUpdates.length) allEdges.update(edgeUpdates);  // 1회 배치
  document.getElementById('stat').innerHTML =
    '표시 <b>'+shown+'</b>/'+allNodes.length
    + (isolatedSet ? ' · 🔍 격리 뷰 ('+isolatedSet.size+'개)' : '')
    + (curMinDeg>0 && !isolatedSet ? ' · 연결≥'+curMinDeg:'')
    + (relFilter?' · 관계 '+relFilter.size+'/'+allRelTypes.length:'')
    + (pathActive?' · 🔗경로 '+pathNodes.size+'노드':(hasFilter && !isolatedSet ?' · 강조 '+emph+'개':''));
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

function onDocqInput(v){
  docSearchActive = true;
  if(serverSearchResults && serverSearchResults.query !== (v||'').trim().toLowerCase()){
    serverSearchResults = null;
  }
  const qEl = document.getElementById('q');
  if(qEl && typeof centerView !== 'undefined' && centerView === 'reader' && qEl.value !== v){
    qEl.value = v;
    const qclear = document.getElementById('qclear');
    if(qclear) qclear.style.display = v ? 'flex' : 'none';
  }
  renderDocs(v);
}

function clearServerSearch(){
  serverSearchResults = null;
  cancelServerSearch();
  const dq = document.getElementById('docq');
  if(dq){ dq.value = ''; }
  const qEl = document.getElementById('q');
  if(qEl && typeof centerView !== 'undefined' && centerView === 'reader'){
    qEl.value = '';
    const qclear = document.getElementById('qclear');
    if(qclear) qclear.style.display = 'none';
  }
  docSearchActive = false;
  renderDocs('');
}

function highlightSearchInGraph(){
  if(!serverSearchResults || !serverSearchResults.hits) return;
  const ids = serverSearchResults.hits.map(h => h.id).filter(Boolean);
  if(!ids.length) return;
  setCenterView('graph');
  revealWorkspace('graph');
  highlightSet = new Set(ids);
  applyView();
  clusterMatches(ids, () => fitToMatches(ids));
}

function renderServerDocs(){
  if(!serverSearchResults) return;
  const res = serverSearchResults;
  const ph = document.getElementById('pinnedhead'); if(ph) ph.style.display = 'none';
  const pl = document.getElementById('pinnedlist'); if(pl) pl.innerHTML = '';
  const sh = document.getElementById('showhidden'); if(sh){ sh.style.display = 'none'; sh.setAttribute('aria-expanded', 'false'); }
  const hl = document.getElementById('hiddenlist'); if(hl){ hl.innerHTML = ''; hl.style.display = 'none'; }

  const dl = document.getElementById('doclist');
  if(dl){
    let html = doclistToolbarHtml();

    if(res.answer){
      html += '<div class="search-ai-card">' +
        '<div class="search-ai-head"><span>💡</span> <b>AI 지식 요약</b></div>' +
        '<div class="search-ai-body">' + esc(res.answer) + '</div>' +
        '</div>';
    }

    const hitCount = (res.hits || []).length;
    html += '<div class="search-action-bar">' +
      (hitCount > 0
        ? '<button type="button" class="sec search-graph-link-btn" onclick="highlightSearchInGraph()" title="지식 그래프에서 연관 개념 노드 하이라이트">' +
          '<span class="btn-icon">🌐</span> <span class="btn-label">그래프에서 맥락 보기 (' + hitCount + ')</span></button>'
        : '') +
      '<button type="button" class="sec search-reset-btn" onclick="clearServerSearch()" title="검색 결과 초기화">✕ 닫기</button>' +
      '</div>';

    if(res.docs && res.docs.length){
      html += res.docs.map(docItemHtml).join('');
    } else {
      html += '<p class="hint" style="padding:24px 12px;text-align:center">일치하는 문서를 찾지 못했습니다.</p>';
    }
    dl.innerHTML = html;
  }

  const st = document.getElementById('stat');
  if(st){
    const modeLabel = res.mode === 'hybrid' ? '🧠 AI 의미 검색' : '📄 전문(Full-Text) 검색';
    st.textContent = modeLabel + ': ' + (res.docs ? res.docs.length : 0) + '건 발견';
  }
  syncGraphDocNav();
}

async function doDocServerSearch(q){
  q = (q || '').trim();
  if(!q){
    clearServerSearch();
    return;
  }
  cancelServerSearch();
  const seq = ++currentSearchSeq;
  let abortController = null;
  if(typeof AbortController !== 'undefined'){
    abortController = new AbortController();
    currentSearchAbort = abortController;
  }
  const isSemantic = AUTH_SCOPE !== 'anonymous' && AUTH_SCOPE !== 'unknown';
  const searchMode = isSemantic ? 'hybrid' : 'fts';
  const shouldSummarize = isSemantic && canWrite();

  const statEl = document.getElementById('stat');
  if(statEl){
    statEl.textContent = isSemantic ? '🧠 AI 지식 검색 및 요약 생성 중…' : '🔎 전체 본문(FTS5) 검색 중…';
  }

  let r;
  try {
    const reqOpts = {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        query: q,
        summarize: shouldSummarize,
        limit: 20,
        mode: searchMode
      })
    };
    if(abortController) reqOpts.signal = abortController.signal;
    r = await fetch('search', reqOpts);
  } catch(e){
    if(abortController && abortController.signal.aborted) return;
    if(seq !== currentSearchSeq) return;
    if(statEl) statEl.textContent = '검색 실패';
    return;
  }
  if(seq !== currentSearchSeq) return;
  if(r.status === 401 || r.status === 404){
    expireWriteAccess();
    if(statEl) statEl.textContent = '세션 만료';
    return;
  }
  if(r.status === 429){
    if(statEl) statEl.textContent = '검색 요청이 많습니다 — 잠시 후 재시도';
    return;
  }
  let d = {};
  try { d = await r.json(); } catch(_) {}
  if(seq !== currentSearchSeq) return;
  if(!r.ok){
    if(statEl) statEl.textContent = '검색 오류 (HTTP ' + r.status + ')';
    return;
  }

  const docScoreMap = new Map();
  const hits = d.hits || [];
  hits.forEach(h => {
    const score = Number(h.score) || 1.0;
    (h.sources || []).forEach(docId => {
      const cur = docScoreMap.get(docId) || 0;
      docScoreMap.set(docId, cur + score);
    });
  });

  const matchedDocs = [];
  const queryLower = q.toLowerCase();
  if(allDocs && allDocs.length){
    allDocs.forEach(dc => {
      if(dc.hidden === 1 && !canWrite()) return;
      const score = docScoreMap.get(dc.id);
      const titleMatch = (dc.title || '').toLowerCase().includes(queryLower);
      const summaryMatch = (dc.summary || '').toLowerCase().includes(queryLower);
      if(score !== undefined || titleMatch || summaryMatch){
        matchedDocs.push({
          doc: dc,
          rankScore: (score || 0) + (titleMatch ? 2.0 : 0) + (summaryMatch ? 1.0 : 0)
        });
      }
    });
    matchedDocs.sort((a, b) => b.rankScore - a.rankScore);
  }

  const resultDocs = matchedDocs.map(item => item.doc);

  serverSearchResults = {
    mode: d.mode || searchMode,
    query: queryLower,
    answer: d.answer || null,
    docs: resultDocs,
    hits: hits
  };

  renderServerDocs();
}

function renderDocs(filter){
  const q = (filter !== undefined ? filter : (document.getElementById('docq') ? document.getElementById('docq').value : '')).trim().toLowerCase();
  if(serverSearchResults && serverSearchResults.query === q){
    renderServerDocs();
    return;
  }
  if(docSearchActive && q){
    cancelServerSearch();
    currentSearchSeq++;
  }
  if(docSearchActive && !q){
    const ph = document.getElementById('pinnedhead'); if(ph) ph.style.display = 'none';
    const pl = document.getElementById('pinnedlist'); if(pl) pl.innerHTML = '';
    const dl = document.getElementById('doclist'); if(dl) dl.innerHTML = doclistToolbarHtml() + '<p class="hint" style="padding:16px 12px;text-align:center">🔎 검색어를 입력하세요.</p>';
    const sh = document.getElementById('showhidden'); if(sh){ sh.style.display = 'none'; sh.setAttribute('aria-expanded', 'false'); }
    const hl = document.getElementById('hiddenlist'); if(hl){ hl.innerHTML = ''; hl.style.display = 'none'; }
    syncGraphDocNav();
    return;
  }
  const match = dc => {
    if(!q) return true;
    if(q === 'is:missing-detail' || q === 'missing:detail'){
      return !dc.has_detail;
    }
    if(q === 'is:mismatched' || q === 'format:mismatch'){
      const targetFmt = (window.currentFormatStatus && window.currentFormatStatus.configured) || 'adoc';
      return dc.has_detail && (dc.detail_format || 'md').toLowerCase() !== targetFmt.toLowerCase();
    }
    return (dc.title+' '+(dc.summary||'')).toLowerCase().includes(q);
  };
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
    if(sh){ sh.style.display='none'; sh.setAttribute('aria-expanded', 'false'); }
    if(hl){ hl.innerHTML=''; hl.style.display='none'; }
  }else{
    if(sh){
      sh.style.display='';
      sh.setAttribute('aria-expanded', showHidden ? 'true' : 'false');
      sh.textContent = (showHidden?'▲ ':'▼ ')+'🙈 숨김 '+hiddenDocs.length+'개 '+(showHidden?'접기':'보기');
    }
    if(hl){
      const prevScroll = hl.scrollTop;
      hl.innerHTML = showHidden ? hiddenDocs.map(docItemHtml).join('') : '';
      hl.style.display = showHidden ? 'block' : 'none';
      if(showHidden && prevScroll > 0) hl.scrollTop = prevScroll;
    }
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

function filterDocsByMissingDetail(){
  const docq = document.getElementById('docq');
  if(docq){
    docq.value = 'is:missing-detail';
  }
  if(typeof openDocDrawer === 'function') openDocDrawer();
  renderDocs('is:missing-detail');
}

function filterDocsByFormatMismatch(){
  const docq = document.getElementById('docq');
  if(docq){
    docq.value = 'is:mismatched';
  }
  if(typeof openDocDrawer === 'function') openDocDrawer();
  renderDocs('is:mismatched');
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
function resetWorkspaceState(){
  cancelServerSearch();
  currentSearchSeq++;
  hideNodePop();
  closeDrawer();
  if(typeof docDrawerOpen !== 'undefined' && docDrawerOpen){
    closeDocDrawer(false);
    docDrawerOpen = false;
    document.body.classList.remove('docdrawer-open');
  }
  const gdm = document.getElementById('graphdocmenu');
  if(gdm && !gdm.hidden){
    closeGraphDocPicker(false, false);
  }
  closeReader(false, false);
  if(typeof closeSttReader === 'function') closeSttReader();

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
  const qclear = document.getElementById('qclear');
  if(qclear) qclear.style.display = 'none';
  streamSearchQuery = '';
  updateSearchModeUI();
  clearTimeout(searchDebounce);

  highlightSet = null;
  isolatedNodeId = null;
  unclusterEdges();
  if(pathMode) clearPath();
  if(net){
    try{ net.unselectAll(); }catch(_){}
  }

  activeDoc = null;
  selectedNodeId = null;
  curReaderDoc = null;
  curReaderDocData = null;

  // Reset curMinDeg to initial default based on node count (0/1/2/5)
  const totalCount = allNodes ? allNodes.length : 0;
  let initialDeg = 0;
  if(totalCount >= 400){
    initialDeg = 5;
  } else if(totalCount >= 180){
    initialDeg = 2;
  } else if(totalCount >= 80){
    initialDeg = 1;
  } else {
    initialDeg = 0;
  }
  curMinDeg = initialDeg;
  const sl = document.getElementById('fslider');
  if(sl){
    sl.value = initialDeg;
  }
  const fm = document.getElementById('fmin');
  if(fm) fm.textContent = initialDeg;
  updateDegPresets();

  // Reset relFilter and legend
  relFilter = null;
  renderLegend();

  // Reset panel hint and graph camera
  if(panel) panel.innerHTML = defaultHint();
  resetGraphCamera();
  syncGraphDocNav();
}

function resolveHomeView(stats, activeThemeId){
  if(!stats) return 'graph';
  const targetTheme = (activeThemeId !== undefined && activeThemeId !== null) ? Number(activeThemeId) : 0;
  let jobs = [];
  if(Array.isArray(stats.active_jobs) && stats.active_jobs.length > 0){
    jobs = stats.active_jobs.filter(j => j && j.active);
  } else if(stats.active_ingest && stats.active_ingest.active){
    jobs = [stats.active_ingest];
  } else if(stats.ingesting){
    jobs = [{ active: true, theme_id: targetTheme }];
  }
  if(jobs.length === 0) return 'graph';
  const matchesCurrent = jobs.some(j => {
    const jTheme = (j.theme_id !== undefined && j.theme_id !== null) ? Number(j.theme_id) : 0;
    return jTheme === targetTheme;
  });
  if(matchesCurrent) return 'job';
  return 'graph+banner';
}

function showOtherThemeJobBanner(stats){
  const banner = document.getElementById('format-warn-banner');
  if(!banner) return;
  const otherJobs = (stats && Array.isArray(stats.active_jobs) ? stats.active_jobs : (stats && stats.active_ingest ? [stats.active_ingest] : []))
    .filter(j => j && j.active && Number(j.theme_id !== undefined ? j.theme_id : 0) !== Number(activeThemeId));
  if(!otherJobs.length) return;
  const job = otherJobs[0];
  const tName = job.theme_label || ('테마 #' + (job.theme_id || 0));
  if(typeof ClaireStatusBanner !== 'undefined' && ClaireStatusBanner && ClaireStatusBanner.show){
    ClaireStatusBanner.show('custom_notice', {
      message: '다른 테마(' + tName + ')에서 ' + (job.title || '작업') + ' 진행 중입니다.'
    }, {
      level: 'info',
      icon: '🔄',
      title: '백그라운드 작업',
      actionLabel: '👉 테마로 이동',
      action: function(){
        switchKnowledgeTheme(job.theme_id || 0);
      }
    });
  }
}

function hideOtherThemeJobBanner(){
  if(typeof ClaireStatusBanner !== 'undefined' && ClaireStatusBanner && ClaireStatusBanner.hide){
    ClaireStatusBanner.hide();
  }
}

async function enterHome({reason = 'brand'} = {}){
  resetWorkspaceState();
  document.title = HOME_TITLE;

  // URL / History: preserve ?theme=N if non-zero, replaceState without pushing
  const url = new URL(window.location.href);
  const currentThemeParam = url.searchParams.get('theme');
  url.search = (activeThemeId && activeThemeId !== 0) ? ('?theme=' + activeThemeId) : (currentThemeParam ? ('?theme=' + currentThemeParam) : '');
  url.hash = '';
  window.history.replaceState({ pane: 'graph', modal: null }, HOME_TITLE, url.pathname + url.search);

  // Sync brand href with current theme
  const brandLink = document.querySelector('a.brand');
  if(brandLink){
    brandLink.href = (activeThemeId && activeThemeId !== 0) ? ('./?theme=' + activeThemeId) : './';
  }

  let stats = null;
  try{
    const r = await fetch('stats', { cache: 'no-store' });
    if(r.ok) stats = await r.json();
  }catch(_){}

  const view = resolveHomeView(stats, activeThemeId);

  if(view === 'job'){
    suspendGraphForIngest();
    setCenterView('matrix');
    processActiveJobsStats(stats);
    startFastIngestPolling();
  } else if(view === 'graph+banner'){
    setCenterView('graph');
    revealWorkspace('graph', false, true);
    if(!graphInitialized){
      initGraph();
    } else {
      applyView();
      resetGraphCamera();
    }
    showOtherThemeJobBanner(stats);
  } else {
    // 'graph'
    setCenterView('graph');
    revealWorkspace('graph', false, true);
    if(!graphInitialized){
      initGraph();
    } else {
      applyView();
      resetGraphCamera();
    }
    hideOtherThemeJobBanner();
  }

  if(reason === 'brand' && typeof window.gtag === 'function'){
    try{
      window.gtag('event', 'home_navigate', {
        reason: 'brand',
        theme_id: activeThemeId
      });
    }catch(_){}
  }
}

function resetHome(){
  return enterHome({reason: 'brand'});
}

function selectDoc(id){
  if(docDrawerOpen){
    closeDocDrawer(false);
    docDrawerOpen = false;
    document.body.classList.remove('docdrawer-open');
    const docsEl = document.getElementById('docs');
    if(docsEl && mobileMQ.matches){
      docsEl.setAttribute('aria-hidden', 'true');
      if(activePane !== 'docs') docsEl.inert = true;
    }
    const backdrop = document.getElementById('drawerbackdrop');
    if(backdrop && !drawerOpen && !detailOpen) backdrop.setAttribute('aria-hidden', 'true');
  }
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
    if(typeof centerView !== 'undefined' && centerView === 'presentation'){
      if(typeof updatePresentationView === 'function') updatePresentationView(id);
    }
    return;
  }
  panel.innerHTML='<p class=hint>문서 불러오는 중…</p>';
  fetch('document?id='+encodeURIComponent(id)).then(r=>r.json()).then(dc=>{
    if(activeDoc!==id) return;                  // 그 사이 다른 문서/노드로 이동했으면 무시
    if(!dc || dc.error){ panel.innerHTML='<p class=hint>문서를 찾을 수 없습니다.</p>'; return; }
    curReaderDocData=dc;
    const docInAll = allDocs && allDocs.find(d => d.id === id);
    if(docInAll && dc.has_presentation !== undefined){
      docInAll.has_presentation = dc.has_presentation;
    }
    renderDocPanel(dc);
    markDocumentSeen(id);
    if(typeof centerView !== 'undefined' && centerView === 'presentation'){
      if(typeof updatePresentationView === 'function') updatePresentationView(id);
    }
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
  if(typeof docMetaMessageHtml === 'function'){
    h+=docMetaMessageHtml(dc);
  }
  const focus = (dc.focus || (dc.meta && dc.meta.focus) || '').trim();
  if(focus){
    h+='<div style="margin:.4em 0 .6em;padding:6px 8px;background:var(--card-bg);border:1px solid var(--border);border-radius:5px;font-size:12px"><b style="color:var(--accent2)">🎯 초점:</b> '+esc(focus)+'</div>';
  }
  // 숨기기 — 상세 패널의 FTS 스타일 체크박스로(사용자 요구).
  if(canWrite()){
    h+='<div class=dochide-row><label class=dochide-label>'+
      '<input type="checkbox" id="panelhidechk" '+(dc.hidden===1?'checked':'')+
      ' onchange="panelToggleHide(\''+dc.id+'\',this.checked)">'+
      '<span id="panelhidelabel">'+(dc.hidden===1?'🙈 숨김 처리됨':'목록에서 숨기기')+'</span>'+
      '</label></div>';
  }

  // 본문의 원문 여는 기능 및 요약 내용은 중앙 리더 본문과 중복 표시이므로 우측 상세 패널에서는 제거함.
  // 이 문서의 노드 버튼 — 누르면 그래프에서 그 노드로 이동(nav).
  const ns=docNodes(dc.id, dc);
  h+='<h3>이 문서의 지식 노드 ('+ns.length+')</h3>';
  if(ns.length){ h+='<div class=nodebtns>'+ ns.map(n=>{
      const c=TYPE_COLORS[n.group]||'#8b949e';
      return '<button class=nodebtn title="'+esc(n.group||'')+'" onmouseenter="peekNode(event,\''+n.id+'\')" '+
        'onmouseleave="leaveNode()" onclick="focusNode(\''+n.id+'\')">'+
        '<i style="background:'+c+'"></i>'+esc(n.label)+'</button>'; }).join('')+'</div>';
  } else { h+='<p class=al>이 문서에서 추출된 노드가 없습니다.</p>'; }
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
  // 선택이 생기면 엔진이 궤도 회전을 멈추고, focus 애니메이션은 노드의 현재 위치를 따라간다.
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
// 검색 결과를 '점차 뭉치게' — 보이지 않는 중앙 앵커를 쓴다.
//   · 앵커는 숨김+고정 노드 → 엔진이 cl_* 엣지를 앵커 스프링으로 해석한다.
//   · 매칭 노드는 자기 허브 tether 에서 풀려나 앵커 쪽으로 끌려와 한 덩어리로 모이고(충돌로 겹침 없음),
//     비매칭은 제자리 별에 남는다.
// 위치를 강제로 옮기지 않으므로 드래그·관성은 그대로다. 검색 해제 시 앵커와 엣지만 빼면
// 매칭 노드가 tether 로 원래 별 자리에 미끄러져 돌아간다(unclusterEdges).
const CENTER_LEN=55;    // 매칭~중앙 spring 길이. ↓ 가운데로 더 바싹
function clusterMatches(ids, done){
  unclusterEdges();
  const vs=(ids||[]).filter(id=>{ const n=allNodes&&allNodes.get(id); return n && !n.hidden; });
  if(!net || !allEdges || vs.length<2){ if(done) done(); return; }
  const c=net.getViewPosition();   // 현재 화면 중앙 좌표에 앵커를 둔다
  allNodes.add({id:'cl_anchor', x:c.x, y:c.y, fixed:true, hidden:true, label:'', size:1});
  clusterAnchor='cl_anchor';
  const eids=[];
  const newEdges=vs.map((id,i)=>{
    const eid='cl_'+i; eids.push(eid);
    return {id:eid, from:'cl_anchor', to:id, color:{opacity:0}, width:0, length:CENTER_LEN};
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
}
// 우측 '이 문서의 노드' 버튼 hover — 요약 팝업(NODE_POP_DELAY_MS 머물 시). 클릭 시 focusNode 로 카메라 이동.
function peekNode(ev, id){
  clearTimeout(hoverTimer);
  if(!canShowNodePop(id)) return;
  const x=ev.clientX, y=ev.clientY;
  hoverTimer=setTimeout(()=>showNodePop(id, x, y), NODE_POP_DELAY_MS);
}
function leaveNode(){ clearTimeout(hoverTimer); hideNodePop(); }
// 공통 헤더 검색창 입력 핸들러 (본문/그래프/의사결정 스트림 상태에 따라 분기)
function onCenterSearchInput(v){
  const qclear = document.getElementById('qclear');
  if(qclear){
    qclear.style.display = v ? 'flex' : 'none';
  }
  const dq = document.getElementById('docq');
  if(dq && dq.value !== v){
    dq.value = v;
  }
  const view = (typeof centerView !== 'undefined' ? centerView : 'graph');
  if(view === 'reader'){
    onDocqInput(v);
  } else if(view === 'stream'){
    if(typeof filterDecisionStreamByQuery === 'function'){
      filterDecisionStreamByQuery(v);
    }
  } else {
    onSearchInput(v);
  }
}
window.onCenterSearchInput = onCenterSearchInput;

function clearCenterSearch(){
  const q = document.getElementById('q');
  if(q){
    q.value = '';
    onCenterSearchInput('');
    q.focus();
  }
}
window.clearCenterSearch = clearCenterSearch;

// 타이핑마다 즉시 검색하면 매 키 입력에 강조+물리 클러스터링이 돌아 무겁고 출렁인다.
// 디바운스: 입력이 멈춘 뒤(350ms) 한 번만 실행. 단 검색창을 비우면 즉시 해제(반응성).
function onSearchInput(v){
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
const docqEl=document.getElementById('docq');
if(docqEl){
  docqEl.addEventListener('keydown',e=>{
    if(e.key!=='Enter') return;
    e.preventDefault();
    doDocServerSearch(e.target.value);
  });
}
const qEl=document.getElementById('q');
if(qEl){
  qEl.addEventListener('keydown',e=>{
    if(e.key === 'Escape'){
      e.preventDefault();
      clearCenterSearch();
      return;
    }
    if(e.key!=='Enter') return;
    e.preventDefault();
    const val = (e.target.value || '').trim();
    const view = (typeof centerView !== 'undefined' ? centerView : 'graph');
    if(view === 'reader'){
      cancelServerSearch();
      currentSearchSeq++;
      clearTimeout(searchDebounce);
      doDocServerSearch(val);
    } else if(view === 'stream'){
      if(typeof filterDecisionStreamByQuery === 'function'){
        filterDecisionStreamByQuery(val);
      }
    } else {
      cancelServerSearch();
      currentSearchSeq++;
      clearTimeout(searchDebounce);
      hl(val);
      if(net && allNodes){
        const matches = [];
        if(val){
          const qVal = val.toLowerCase();
          allNodes.forEach(n => {
            if(n.label.toLowerCase().includes(qVal)) matches.push(n.id);
          });
        }
        if(matches.length){
          net.selectNodes(matches);
          cameraToNodes(matches);
          if(matches.length === 1) loadNode(matches[0]);
        }
      }
    }
  });
  qEl.addEventListener('focus', e=> e.target.select());
}
function doSemantic(){
  const docqVal = (document.getElementById('docq') ? document.getElementById('docq').value : '').trim();
  const qVal = (document.getElementById('q') ? document.getElementById('q').value : '').trim();
  const qv = docqVal || qVal;
  if(docqVal){
    doDocServerSearch(docqVal);
  } else {
    semanticSearch(qv);
  }
}

// --- 인증 상태 표시 ---
// 첫 페인트는 unknown/read-only이며, /whoami가 exact owner를 확인한 경우에만 쓰기 UI를
// 승격한다. 버튼 숨김과 별개로 모든 쓰기 함수도 canWrite()를 확인한다.
function updateSearchModeUI(){
  if(typeof updateCenterSearchMode === 'function' && typeof centerView !== 'undefined'){
    updateCenterSearchMode(centerView);
  }
}
function setAccessScope(scope, reason){
  AUTH_SCOPE = ['owner','collaborator','readonly','anonymous'].includes(scope) ? scope : 'unknown';
  READONLY = !canWrite();
  document.body.dataset.authScope=AUTH_SCOPE;
  document.body.classList.toggle('ro', READONLY);
  const label=document.getElementById('authstate');
  if(label) label.textContent =
    AUTH_SCOPE==='owner' ? '🔒 인증됨' :
    AUTH_SCOPE==='collaborator' ? '🤝 협업자' :
    AUTH_SCOPE==='readonly' ? '👁️ 읽기전용' :
    AUTH_SCOPE==='anonymous' ? '👁️ 익명 읽기전용' :
    reason==='expired' ? '🔓 쓰기 세션 만료 — /web 재접속' : '⚠️ 권한 확인 실패';
  const tmBtn = document.getElementById('thememanagebtn');
  const canAccessThemes = (canWrite() || AUTH_SCOPE === 'collaborator') && isMultiThemeEnabled();
  if(tmBtn) tmBtn.style.display = canAccessThemes ? '' : 'none';
  const pmBtn = document.getElementById('providermanagebtn');
  if(pmBtn) pmBtn.style.display = canWrite() ? '' : 'none';
  const lmBtn = document.getElementById('limitmanagebtn');
  if(lmBtn) lmBtn.style.display = canWrite() ? '' : 'none';
  const kmBtn = document.getElementById('tokenmanagebtn');
  if(kmBtn) kmBtn.style.display = canWrite() ? '' : 'none';
  const mgSec = document.getElementById('manage-section');
  if(mgSec) mgSec.style.display = (canAccessThemes || canWrite()) ? '' : 'none';
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

async function openThemeManager(){
  const isOwner = canWrite();
  const isCollab = AUTH_SCOPE === 'collaborator';
  if(!isOwner && !isCollab) return;
  openDetailPane();
  panel.innerHTML = '<h2>📁 지식 테마 관리</h2><p class="hint">테마 목록 조회 중…</p>';
  try{
    const r = await fetch('themes');
    if(!r.ok) throw new Error('HTTP ' + r.status);
    const data = await r.json();
    const themes = data.themes || [];
    let h = '<h2>📁 지식 테마 관리</h2>';
    if(isOwner){
      h += '<p class="al">지식 관리자(owner)는 테마별로 공개 여부 및 협업자 접근을 설정할 수 있습니다. 비공개 테마는 익명 사용자에게 노출되지 않습니다.</p>';
    } else {
      h += '<p class="al">협업자(collaborator)는 권한이 부여된 테마의 상세 설정 및 옵션을 확인할 수 있습니다. (테마 수정/생성/삭제는 소유자 전용)</p>';
    }
    h += '<div class="theme-manager-list" style="display:flex;flex-direction:column;gap:10px;margin-bottom:16px;">';
    for(const t of themes){
      const isPub = t.is_public !== false;
      const pubBadge = isPub
        ? '<span style="color:var(--accent,#00ffaa);font-weight:600;font-size:12px;background:rgba(0,255,170,0.1);padding:2px 6px;border-radius:4px">공개</span>'
        : '<span style="color:var(--warn,#f39c12);font-weight:600;font-size:12px;background:rgba(243,156,18,0.1);padding:2px 6px;border-radius:4px">비공개 🔒</span>';
      const toggleBtn = isPub
        ? `<button type="button" class="sec" style="font-size:11px;padding:3px 8px;" onclick="toggleThemeVisibility(${t.id}, false)">비공개로 전환</button>`
        : `<button type="button" class="sec" style="font-size:11px;padding:3px 8px;" onclick="toggleThemeVisibility(${t.id}, true)">공개로 전환</button>`;
      const isCollabAcc = t.is_collaborator_accessible !== false;
      const collabBadge = isCollabAcc
        ? '<span style="color:#60a5fa;font-weight:600;font-size:12px;background:rgba(96,165,250,0.1);padding:2px 6px;border-radius:4px">협업자 공개 🤝</span>'
        : '<span style="color:var(--muted,#888);font-weight:600;font-size:12px;background:rgba(136,136,136,0.1);padding:2px 6px;border-radius:4px">협업자 차단 🚫</span>';
      const toggleCollabBtn = isCollabAcc
        ? `<button type="button" class="sec" style="font-size:11px;padding:3px 8px;" onclick="toggleThemeCollaborator(${t.id}, false)">협업자 차단</button>`
        : `<button type="button" class="sec" style="font-size:11px;padding:3px 8px;" onclick="toggleThemeCollaborator(${t.id}, true)">협업자 공개</button>`;
      const isPres = t.presentation_enabled === true;
      const presBadge = isPres
        ? '<span style="color:#38bdf8;font-weight:600;font-size:12px;background:rgba(56,189,248,0.12);padding:2px 6px;border-radius:4px" title="프레젠테이션 기능 활성화됨">📊 발표 ON</span>'
        : '<span style="color:var(--muted,#888);font-weight:600;font-size:12px;background:rgba(136,136,136,0.1);padding:2px 6px;border-radius:4px" title="프레젠테이션 기능 비활성화됨">🚫 발표 OFF</span>';
      const togglePresBtn = isPres
        ? `<button type="button" class="sec" style="font-size:11px;padding:3px 8px;" onclick="toggleThemePresentation(${t.id}, false)">발표 끄기</button>`
        : `<button type="button" class="sec" style="font-size:11px;padding:3px 8px;" onclick="toggleThemePresentation(${t.id}, true)">발표 켜기</button>`;
      const canIngestHere = t.id > 0 && isCollabAcc;
      const idLabel = t.id === 0 ? '기본 (ID 0)' : `테마 #${t.id}`;
      const defaultMgr = (window.__CLAIRE_CONFIG && window.__CLAIRE_CONFIG.sorcerer) || 'owner';
      const rawMgr = (t.sorcerer || t.knowledge_manager || '').trim();
      const effMgr = rawMgr || (t.effective_sorcerer || defaultMgr);
      h += `<div style="border:1px solid var(--border);border-radius:6px;padding:8px 10px;background:var(--sec-bg);">
        <div style="display:flex;align-items:center;justify-content:space-between;gap:8px;flex-wrap:wrap;">
          <strong style="font-size:13px;">${t.icon || '📁'} ${esc(t.label)} <small style="opacity:0.7">(${idLabel})</small></strong>
          <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;">
            ${t.fqdn ? `<span style="color:#a78bfa;font-weight:600;font-size:12px;background:rgba(167,139,250,0.15);padding:2px 6px;border-radius:4px" title="전용 도메인">🌐 ${esc(t.fqdn)}</span>` : ''}
            ${t.fqdn ? `<a href="//${esc(t.fqdn)}" target="_blank" rel="noopener noreferrer" style="font-size:11px;color:var(--accent2,#00aaff);text-decoration:none;padding:2px 4px;" title="전용 도메인으로 새 창 열기">↗ 열기</a>` : ''}
            ${t.ga_measurement_id ? `<span style="color:#fbbf24;font-weight:600;font-size:12px;background:rgba(251,191,36,0.15);padding:2px 6px;border-radius:4px" title="Google Analytics 4">📊 ${esc(t.ga_measurement_id)}</span>` : ''}
            ${pubBadge}
            ${isOwner ? toggleBtn : ''}
            ${t.id > 0 ? collabBadge : ''}
            ${(isOwner && t.id > 0) ? toggleCollabBtn : ''}
            ${presBadge}
            ${isOwner ? togglePresBtn : ''}
            ${isOwner ? `<button type="button" class="sec" style="font-size:11px;padding:3px 8px;" onclick="showThemeEditForm(${t.id})">✏️ 수정</button>` : ''}
            <button type="button" class="sec" style="font-size:11px;padding:3px 8px;" onclick="toggleThemeOptionsView(${t.id})">⚙️ 옵션 보기</button>
            ${(isOwner && t.id > 0) ? `<button type="button" class="sec danger-btn" style="font-size:11px;padding:3px 8px;color:var(--err,#cf222e);border-color:rgba(207,34,46,0.3);" onclick="deleteThemeFromUI(${t.id}, '${esc(t.label)}')">🗑️ 삭제</button>` : ''}
          </div>
        </div>
        ${t.description ? `<p style="margin:4px 0 0;font-size:12px;opacity:0.8">${esc(t.description)}</p>` : ''}
        <p style="margin:4px 0 0;font-size:12px;opacity:0.8">지식 관리자: ${esc(effMgr)}</p>
        ${(t.id > 0 && t.default_focus) ? `<p style="margin:4px 0 0;font-size:12px;color:var(--accent,#00ffaa);font-weight:500;">🎯 기본 초점: ${esc(t.default_focus)}</p>` : ''}
        
        <!-- 테마 설정 옵션 상세 뷰 (협업자 및 소유자 확인용) -->
        <div id="theme-options-view-${t.id}" style="display:${isCollab ? 'flex' : 'none'};margin-top:10px;padding:8px 10px;border-top:1px dashed var(--border);background:var(--panel-bg);border-radius:4px;font-size:12px;flex-direction:column;gap:6px;">
          <div style="font-weight:600;color:var(--accent2);margin-bottom:2px;">⚙️ 테마 설정 옵션</div>
          <div>• <b>레이블(이름)</b>: ${esc(t.label)}</div>
          <div>• <b>지식 관리자</b>: ${esc(effMgr)}</div>
          <div>• <b>아이콘</b>: ${esc(t.icon || '📁')}</div>
          <div>• <b>설명</b>: ${esc(t.description || '(설명 없음)')}</div>
          <div>• <b>전용 FQDN (도메인)</b>: ${esc(t.fqdn || '(미지정 - 기본 도메인 공유)')}</div>
          <div>• <b>GA4 측정 ID</b>: ${esc(t.ga_measurement_id || '(미지정 - 비활성)')}</div>
          ${t.id > 0 ? `<div>• <b>기본 초점</b>: ${esc(t.default_focus || '(미지정 - 기본 분석 적용)')}</div>` : ''}
          <div>• <b>공개 여부</b>: ${isPub ? '익명 사용자 공개' : '비공개 🔒'}</div>
          ${t.id > 0 ? `<div>• <b>협업자 공개 여부</b>: ${isCollabAcc ? '협업자 공개 (접근 및 적재 허용)' : '협업자 차단'}</div>` : ''}
          <div>• <b>협업자 적재 권한</b>: ${canIngestHere ? '<span style="color:var(--accent,#00ffaa);font-weight:600;">허용됨 (자료 적재 가능 ✍️)</span>' : (t.id === 0 ? '<span style="color:var(--muted);font-weight:600;">제한됨 (소유자 기본 지식베이스)</span>' : '<span style="color:var(--warn);font-weight:600;">제한됨 (소유자 미허용)</span>')}</div>
          <div>• <b>프레젠테이션 기능</b>: ${isPres ? '<span style="color:#38bdf8;font-weight:600;">활성화됨 (발표 기능 사용 가능 📊)</span>' : '<span style="color:var(--muted);font-weight:600;">비활성화됨 (발표 기능 숨김 🚫)</span>'}</div>
          ${t.stats ? `<div>• <b>지식 현황</b>: 문서 ${t.stats.documents || 0}건 · 엔티티 ${t.stats.entities || 0}개 · 관계 ${t.stats.relations || 0}개</div>` : ''}
        </div>

        ${isOwner ? `<div id="theme-edit-form-${t.id}" style="display:none;margin-top:10px;padding-top:8px;border-top:1px dashed var(--border);flex-direction:column;gap:8px;">
          <div><label style="font-size:11px;opacity:0.8">레이블(이름)</label><input id="editthemep-label-${t.id}" style="width:100%;box-sizing:border-box" value="${esc(t.label)}"/></div>
          <div><label style="font-size:11px;opacity:0.8">지식 관리자</label><input id="editthemep-sorcerer-${t.id}" style="width:100%;box-sizing:border-box" placeholder="지식 관리자" value="${esc(rawMgr)}"/></div>
          <div><label style="font-size:11px;opacity:0.8">설명</label><input id="editthemep-desc-${t.id}" style="width:100%;box-sizing:border-box" value="${esc(t.description || '')}"/></div>
          <div><label style="font-size:11px;opacity:0.8">전용 도메인 (FQDN, 예: ai.example.com)</label><input id="editthemep-fqdn-${t.id}" style="width:100%;box-sizing:border-box" placeholder="예: ai.example.com" value="${esc(t.fqdn || '')}"/></div>
          <div><label style="font-size:11px;opacity:0.8">Google Analytics 4 측정 ID (예: G-XXXXXXXXXX)</label><input id="editthemep-ga-${t.id}" style="width:100%;box-sizing:border-box" placeholder="예: G-XXXXXXXXXX" value="${esc(t.ga_measurement_id || '')}"/></div>
          ${t.id > 0 ? `<div><label style="font-size:11px;opacity:0.8">기본 초점 (적재 시 기본 적용할 방향/지침)</label><input id="editthemep-focus-${t.id}" style="width:100%;box-sizing:border-box" placeholder="예: 시스템 아키텍처 및 핵심 API 사양 중심" value="${esc(t.default_focus || '')}"/></div>` : ''}
          <div style="display:flex;gap:8px;flex-wrap:wrap;">
            <div style="flex:1;min-width:80px;"><label style="font-size:11px;opacity:0.8">아이콘</label><input id="editthemep-icon-${t.id}" style="width:100%;box-sizing:border-box" value="${esc(t.icon || '📁')}"/></div>
            <div style="flex:1;display:flex;align-items:center;padding-top:14px;"><label style="font-size:12px;cursor:pointer;"><input id="editthemep-pub-${t.id}" type="checkbox" ${isPub ? 'checked' : ''} style="width:auto;margin-right:4px;vertical-align:middle;"/>익명 사용자에게 공개</label></div>
            ${t.id > 0 ? `<div style="flex:1;display:flex;align-items:center;padding-top:14px;"><label style="font-size:12px;cursor:pointer;"><input id="editthemep-collab-${t.id}" type="checkbox" ${isCollabAcc ? 'checked' : ''} style="width:auto;margin-right:4px;vertical-align:middle;"/>협업자에게 공개</label></div>` : ''}
            <div style="flex:1;display:flex;align-items:center;padding-top:14px;"><label style="font-size:12px;cursor:pointer;"><input id="editthemep-pres-${t.id}" type="checkbox" ${isPres ? 'checked' : ''} style="width:auto;margin-right:4px;vertical-align:middle;"/>프레젠테이션 기능 활성화</label></div>
          </div>
          <div style="display:flex;gap:6px;margin-top:4px;align-items:center;">
            <button type="button" style="padding:4px 12px;" onclick="updateThemeFromUI(${t.id})">저장</button>
            <button type="button" class="sec" style="padding:4px 12px;" onclick="hideThemeEditForm(${t.id})">취소</button>
            ${t.id > 0 ? `<button type="button" class="sec danger-btn" style="margin-left:auto;padding:4px 10px;font-size:11px;color:var(--err,#cf222e);border-color:rgba(207,34,46,0.3);" onclick="deleteThemeFromUI(${t.id}, '${esc(t.label)}')">🗑️ 테마 완전 삭제</button>` : ''}
          </div>
        </div>` : ''}
      </div>`;
    }
    h += '</div>';

    // 신규 테마 정의 폼 (owner 전용)
    if(isOwner){
      h += '<details style="border:1px solid var(--border);border-radius:6px;padding:8px 10px;background:var(--sec-bg);"><summary style="cursor:pointer;font-weight:600;font-size:13px;">➕ 새 테마 추가</summary>';
      h += '<div style="display:flex;flex-direction:column;gap:8px;margin-top:8px;">';
      h += '<div><label style="font-size:11px;opacity:0.8">레이블(이름)</label><input id="newthemep-label" style="width:100%;box-sizing:border-box" placeholder="예: 경제 및 금융"/></div>';
      h += '<div><label style="font-size:11px;opacity:0.8">지식 관리자</label><input id="newthemep-sorcerer" style="width:100%;box-sizing:border-box" placeholder="지식 관리자"/></div>';
      h += '<div><label style="font-size:11px;opacity:0.8">설명</label><input id="newthemep-desc" style="width:100%;box-sizing:border-box" placeholder="예: 거시경제 및 시장 분석"/></div>';
      h += '<div><label style="font-size:11px;opacity:0.8">전용 도메인 (FQDN, 예: ai.example.com)</label><input id="newthemep-fqdn" style="width:100%;box-sizing:border-box" placeholder="예: ai.example.com"/></div>';
      h += '<div><label style="font-size:11px;opacity:0.8">Google Analytics 4 측정 ID (예: G-XXXXXXXXXX)</label><input id="newthemep-ga" style="width:100%;box-sizing:border-box" placeholder="예: G-XXXXXXXXXX"/></div>';
      h += '<div><label style="font-size:11px;opacity:0.8">기본 초점 (적재 시 기본 적용할 방향/지침)</label><input id="newthemep-focus" style="width:100%;box-sizing:border-box" placeholder="예: 거시경제 지표 및 시장 영향 중심"/></div>';
      h += '<div style="display:flex;gap:8px;flex-wrap:wrap;"><div style="flex:1;min-width:80px;"><label style="font-size:11px;opacity:0.8">아이콘</label><input id="newthemep-icon" style="width:100%;box-sizing:border-box" value="📁"/></div>';
      h += '<div style="flex:1;display:flex;align-items:center;padding-top:14px;"><label style="font-size:12px;cursor:pointer;"><input id="newthemep-pub" type="checkbox" checked style="width:auto;margin-right:4px;vertical-align:middle;"/>익명 사용자에게 공개</label></div>';
      h += '<div style="flex:1;display:flex;align-items:center;padding-top:14px;"><label style="font-size:12px;cursor:pointer;"><input id="newthemep-collab" type="checkbox" checked style="width:auto;margin-right:4px;vertical-align:middle;"/>협업자에게 공개</label></div>';
      h += '<div style="flex:1;display:flex;align-items:center;padding-top:14px;"><label style="font-size:12px;cursor:pointer;"><input id="newthemep-pres" type="checkbox" style="width:auto;margin-right:4px;vertical-align:middle;"/>프레젠테이션 기능 활성화</label></div></div>';
      h += '<button type="button" style="align-self:flex-start;padding:4px 12px;margin-top:4px;" onclick="createThemeFromUI()">테마 생성</button>';
      h += '</div></details>';
    } else {
      h += '<p class="hint" style="margin-top:14px;font-size:12px;">💡 새로운 지식 테마 생성 또는 설정 변경이 필요한 경우 지식 관리자(owner)에게 요청하세요.</p>';
    }

    panel.innerHTML = h;
  }catch(e){
    panel.innerHTML = '<p class="hint">테마 목록 조회 실패: ' + esc(String(e)) + '</p>';
  }
}

function toggleThemeOptionsView(themeId){
  const el = document.getElementById('theme-options-view-' + themeId);
  if(el){
    el.style.display = (el.style.display === 'none') ? 'flex' : 'none';
  }
}

function showThemeEditForm(themeId){
  const form = document.getElementById('theme-edit-form-' + themeId);
  if(form){
    form.style.display = form.style.display === 'none' ? 'flex' : 'none';
  }
}

function hideThemeEditForm(themeId){
  const form = document.getElementById('theme-edit-form-' + themeId);
  if(form){
    form.style.display = 'none';
  }
}

async function updateThemeFromUI(themeId){
  if(!canWrite()) return;
  const labelEl = document.getElementById('editthemep-label-' + themeId);
  const descEl = document.getElementById('editthemep-desc-' + themeId);
  const focusEl = document.getElementById('editthemep-focus-' + themeId);
  const iconEl = document.getElementById('editthemep-icon-' + themeId);
  const pubEl = document.getElementById('editthemep-pub-' + themeId);
  const collabEl = document.getElementById('editthemep-collab-' + themeId);
  const presEl = document.getElementById('editthemep-pres-' + themeId);
  const fqdnEl = document.getElementById('editthemep-fqdn-' + themeId);
  const gaEl = document.getElementById('editthemep-ga-' + themeId);
  const sorcererEl = document.getElementById('editthemep-sorcerer-' + themeId);

  const label = ((labelEl||{}).value||'').trim();
  if(!label){ alert('테마 이름을 입력하세요.'); return; }
  const description = ((descEl||{}).value||'').trim();
  const icon = ((iconEl||{}).value||'📁').trim() || '📁';
  const is_public = pubEl ? pubEl.checked : true;

  const payload = {id: themeId, label, description, icon, is_public};
  if(sorcererEl !== null && sorcererEl !== undefined){
    payload.sorcerer = sorcererEl.value.trim();
  }
  if(collabEl){
    payload.is_collaborator_accessible = collabEl.checked;
  }
  if(presEl){
    payload.presentation_enabled = presEl.checked;
  }
  if(focusEl){
    payload.default_focus = focusEl.value.trim();
  }
  if(fqdnEl){
    payload.fqdn = fqdnEl.value.trim() || null;
  }
  if(gaEl){
    payload.ga_measurement_id = gaEl.value.trim() || null;
  }

  try{
    const r = await fetch('themes', {
      method: 'PATCH',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload)
    });
    if(!r.ok){
      const err = await r.json().catch(()=>({}));
      alert('테마 수정 실패: ' + (err.detail || err.error || ('HTTP ' + r.status)));
      return;
    }
    await fetchThemes();
    openThemeManager();
  }catch(e){
    alert('오류: ' + e);
  }
}

async function toggleThemeVisibility(themeId, newVisibility){
  if(!canWrite()) return;
  try{
    const r = await fetch('themes', {
      method: 'PATCH',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({id: themeId, is_public: newVisibility})
    });
    if(!r.ok){
      const err = await r.json().catch(()=>({}));
      alert('테마 공개 여부 수정 실패: ' + (err.detail || err.error || ('HTTP ' + r.status)));
      return;
    }
    await fetchThemes();
    openThemeManager();
  }catch(e){
    alert('오류: ' + e);
  }
}

async function toggleThemeCollaborator(themeId, newAccess){
  if(!canWrite()) return;
  try{
    const r = await fetch('themes', {
      method: 'PATCH',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({id: themeId, is_collaborator_accessible: newAccess})
    });
    if(!r.ok){
      const err = await r.json().catch(()=>({}));
      alert('협업자 공개 여부 수정 실패: ' + (err.detail || err.error || ('HTTP ' + r.status)));
      return;
    }
    await fetchThemes();
    openThemeManager();
  }catch(e){
    alert('오류: ' + e);
  }
}

async function toggleThemePresentation(themeId, newPres){
  if(!canWrite()) return;
  try{
    const r = await fetch('themes', {
      method: 'PATCH',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({id: themeId, presentation_enabled: Boolean(newPres)})
    });
    if(!r.ok){
      const err = await r.json().catch(()=>({}));
      alert('프레젠테이션 설정 수정 실패: ' + (err.detail || err.error || ('HTTP ' + r.status)));
      return;
    }
    await fetchThemes();
    openThemeManager();
  }catch(e){
    alert('오류: ' + e);
  }
}

async function createThemeFromUI(){
  if(!canWrite()) return;
  const labelEl = document.getElementById('newthemep-label');
  const descEl = document.getElementById('newthemep-desc');
  const focusEl = document.getElementById('newthemep-focus');
  const iconEl = document.getElementById('newthemep-icon');
  const pubEl = document.getElementById('newthemep-pub');
  const collabEl = document.getElementById('newthemep-collab');
  const presEl = document.getElementById('newthemep-pres');
  const fqdnEl = document.getElementById('newthemep-fqdn');
  const gaEl = document.getElementById('newthemep-ga');
  const sorcererEl = document.getElementById('newthemep-sorcerer');

  const label = ((labelEl||{}).value||'').trim();
  if(!label){ alert('테마 이름을 입력하세요.'); return; }
  const description = ((descEl||{}).value||'').trim();
  const default_focus = ((focusEl||{}).value||'').trim();
  const icon = ((iconEl||{}).value||'📁').trim() || '📁';
  const is_public = pubEl ? pubEl.checked : true;
  const is_collaborator_accessible = collabEl ? collabEl.checked : true;
  const presentation_enabled = presEl ? presEl.checked : false;
  const fqdn = ((fqdnEl||{}).value||'').trim() || null;
  const ga_measurement_id = ((gaEl||{}).value||'').trim() || null;
  const sorcerer = ((sorcererEl||{}).value||'').trim();

  const payload = {label, description, default_focus, icon, is_public, is_collaborator_accessible, presentation_enabled};
  if(sorcerer) payload.sorcerer = sorcerer;
  if(fqdn) payload.fqdn = fqdn;
  if(ga_measurement_id) payload.ga_measurement_id = ga_measurement_id;

  try{
    const r = await fetch('themes', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload)
    });
    if(!r.ok){
      const err = await r.json().catch(()=>({}));
      alert('테마 생성 실패: ' + (err.detail || err.error || ('HTTP ' + r.status)));
      return;
    }
    await fetchThemes();
    openThemeManager();
  }catch(e){
    alert('오류: ' + e);
  }
}

async function deleteThemeFromUI(themeId, label){
  if(!canWrite()) return;
  if(themeId === 0){
    alert('기본 지식베이스(기본 테마)는 삭제할 수 없습니다.');
    return;
  }
  const confirmed = confirm(`'${label}' 테마를 완전히 삭제하시겠습니까?\n\n이 테마에 저장된 데이터베이스 및 모든 파일이 영구 삭제(소각)됩니다.`);
  if(!confirmed) return;

  try{
    const r = await fetch(`themes?id=${themeId}&purge=1`, {
      method: 'DELETE'
    });
    if(!r.ok){
      const err = await r.json().catch(()=>({}));
      alert('테마 삭제 실패: ' + (err.detail || err.error || ('HTTP ' + r.status)));
      return;
    }
    if(activeThemeId === themeId){
      activeThemeId = 0;
      try{ localStorage.setItem('claire_active_theme', '0'); }catch(e){}
    }
    await fetchThemes();
    await reloadThemeData();
    await loadThemeDocuments();
    openThemeManager();
  }catch(e){
    alert('오류: ' + e);
  }
}

let _provConfig = null;

async function openProviderManager(){
  if(!canWrite()) return;
  openDetailPane();
  panel.innerHTML = '<h2>⚡ 프로바이더 관리</h2><p class="hint">설정 로딩 중…</p>';
  try{
    const r = await fetch('providers');
    if(!r.ok) throw new Error('HTTP ' + r.status);
    const data = await r.json();
    _provConfig = data;
    renderProviderManager();
  }catch(e){
    panel.innerHTML = `<h2>⚡ 프로바이더 관리</h2><p class="hint" style="color:var(--err,#cf222e)">설정을 불러올 수 없습니다: ${esc(e.message||e)}</p>`;
  }
}

function renderProviderManager(msg, isSuccess){
  if(!_provConfig) return;
  const cfg = _provConfig;
  const provs = cfg.providers || {};
  const gemini = provs.gemini || {};
  const agy = provs.antigravity || {};
  const cdx = provs.codex || {};
  const oai = provs.openai || {};
  const jev = provs.jev || {};
  const stt = provs.stt || {};
  const active = cfg.active_provider || 'mock';

  let alertHtml = '';
  if(msg){
    const color = isSuccess ? 'var(--accent2,#1a7f37)' : 'var(--err,#cf222e)';
    const bg = isSuccess ? 'rgba(26,127,55,0.1)' : 'rgba(207,34,46,0.1)';
    const icon = isSuccess ? '✅' : '⚠️';
    alertHtml = `<div style="padding:8px 10px;border-radius:6px;background:${bg};color:${color};font-size:12px;margin-bottom:12px;border:1px solid ${color};">${icon} ${esc(msg)}</div>`;
  }

  let h = '<h2>⚡ LLM &amp; 프로바이더 관리</h2>';
  h += '<p class="al">지식 추출, 요약, 임베딩, 전사에 사용할 하이퍼스케일러 및 LLM 프로바이더를 설정합니다. 변경 사항은 <code>data/providers.json</code>에 즉시 영구 저장됩니다.</p>';
  h += alertHtml;

  // 1. 메인 프로바이더 선택
  h += '<div style="border:1px solid var(--border);border-radius:6px;padding:10px;background:var(--sec-bg);margin-bottom:14px;">';
  h += '<label style="font-weight:600;font-size:13px;display:block;margin-bottom:6px;">메인 LLM 프로바이더</label>';
  h += '<select id="prov-active-select" style="width:100%;padding:6px 8px;font-size:13px;border-radius:6px;border:1px solid var(--border);background:var(--card-bg);color:var(--fg);box-sizing:border-box;">';
  const opts = [
    {val: 'gemini', label: 'Google Gemini (권장 - 고품질 LLM 및 임베딩)'},
    {val: 'antigravity', label: 'Google Antigravity CLI (agy 바이너리)'},
    {val: 'codex', label: 'Codex CLI (codex - 호스트 네이티브 전용)'},
    {val: 'openai', label: 'OpenAI / Azure / 호환 엔드포인트'},
    {val: 'mock', label: 'Mock (가상 테스트 프로바이더 - 외부 호출 없음)'}
  ];
  for(const opt of opts){
    h += `<option value="${opt.val}" ${active === opt.val ? 'selected' : ''}>${opt.label}</option>`;
  }
  h += '</select>';
  h += `<div style="margin-top:6px;font-size:11px;opacity:0.8;">현재 동작 중: <b>${esc(cfg.effective_provider || active)}</b> · STT 동작: <b>${esc(cfg.effective_stt_provider || 'mock')}</b></div>`;
  h += '</div>';

  // 2. Google Gemini Card
  h += '<div style="border:1px solid var(--border);border-radius:6px;padding:10px;background:var(--sec-bg);margin-bottom:14px;">';
  h += '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px;">';
  h += '<strong style="font-size:13px;">🌐 Google Gemini</strong>';
  h += '<button type="button" class="sec" style="font-size:11px;padding:2px 8px;" onclick="testProviderConnection(\'gemini\')">연결 테스트</button>';
  h += '</div>';
  h += '<div style="display:flex;flex-direction:column;gap:8px;font-size:12px;">';
  h += '<div><label style="font-size:11px;opacity:0.8;">Gemini API Key</label>';
  h += '<div style="display:flex;gap:4px;margin-top:2px;">';
  h += `<input id="prov-gemini-key" type="password" style="flex:1;box-sizing:border-box;" placeholder="${gemini.has_api_key ? '설정됨 (••••••••)' : 'Gemini API Key 입력'}"/>`;
  h += '<button type="button" class="sec" style="font-size:11px;padding:2px 6px;" onclick="togglePassVisibility(\'prov-gemini-key\')">👁️</button>';
  if(gemini.has_api_key){
    h += '<button type="button" class="sec danger-btn" style="font-size:11px;padding:2px 6px;color:var(--err,#cf222e);" onclick="clearApiKeyInput(\'prov-gemini-key\', \'gemini\')">삭제</button>';
  }
  h += '</div></div>';
  h += '<div><label style="font-size:11px;opacity:0.8;">모델 (Model)</label>';
  h += `<input id="prov-gemini-model" style="width:100%;box-sizing:border-box;margin-top:2px;" list="gemini-model-list" value="${esc(gemini.model||'gemini-3.1-flash-lite')}"/>`;
  h += '<datalist id="gemini-model-list"><option value="gemini-3.1-flash-lite"/><option value="gemini-2.5-flash"/><option value="gemini-2.5-pro"/><option value="gemini-3.7-flash"/></datalist></div>';
  h += '<div style="display:flex;gap:8px;">';
  h += '<div style="flex:1;"><label style="font-size:11px;opacity:0.8;">추론 수준 (Effort)</label>';
  h += `<select id="prov-gemini-effort" style="width:100%;box-sizing:border-box;margin-top:2px;">
    <option value="low" ${gemini.effort==='low'?'selected':''}>low</option>
    <option value="medium" ${gemini.effort==='medium'||!gemini.effort?'selected':''}>medium</option>
    <option value="high" ${gemini.effort==='high'?'selected':''}>high</option>
  </select></div>`;
  h += '<div style="flex:1;"><label style="font-size:11px;opacity:0.8;">임베딩 모델</label>';
  h += `<input id="prov-gemini-embed" style="width:100%;box-sizing:border-box;margin-top:2px;" value="${esc(gemini.embed_model||'gemini-embedding-001')}"/></div>`;
  h += '</div>';
  h += '<div style="display:flex;gap:8px;">';
  h += `<div style="flex:1;"><label style="font-size:11px;opacity:0.8;">최소 간격 (초)</label><input id="prov-gemini-interval" type="number" step="0.5" style="width:100%;box-sizing:border-box;margin-top:2px;" value="${gemini.min_interval||4.0}"/></div>`;
  h += `<div style="flex:1;"><label style="font-size:11px;opacity:0.8;">최대 재시도 (회)</label><input id="prov-gemini-retries" type="number" style="width:100%;box-sizing:border-box;margin-top:2px;" value="${gemini.max_retries||5}"/></div>`;
  h += '</div>';
  h += '</div>';
  h += '<div id="prov-test-res-gemini" style="display:none;margin-top:8px;font-size:11px;"></div>';
  h += '</div>';

  // 3. Antigravity CLI Card
  const agyBadge = agy.installed
    ? '<span style="color:var(--accent2,#1a7f37);font-size:11px;background:rgba(26,127,55,0.1);padding:1px 6px;border-radius:4px;">감지됨</span>'
    : '<span style="color:var(--muted,#888);font-size:11px;background:rgba(136,136,136,0.1);padding:1px 6px;border-radius:4px;">바이너리 없음</span>';
  h += '<div style="border:1px solid var(--border);border-radius:6px;padding:10px;background:var(--sec-bg);margin-bottom:14px;">';
  h += '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px;">';
  h += `<strong style="font-size:13px;">🚀 Antigravity CLI (agy) ${agyBadge}</strong>`;
  h += '<button type="button" class="sec" style="font-size:11px;padding:2px 8px;" onclick="testProviderConnection(\'antigravity\')">CLI 확인</button>';
  h += '</div>';
  h += '<div style="display:flex;flex-direction:column;gap:8px;font-size:12px;">';
  h += '<div style="display:flex;gap:8px;">';
  h += `<div style="flex:1;"><label style="font-size:11px;opacity:0.8;">바이너리 (Bin)</label><input id="prov-agy-bin" style="width:100%;box-sizing:border-box;margin-top:2px;" value="${esc(agy.bin||'agy')}"/></div>`;
  h += `<div style="flex:1;"><label style="font-size:11px;opacity:0.8;">모델 (Model)</label><input id="prov-agy-model" style="width:100%;box-sizing:border-box;margin-top:2px;" value="${esc(agy.model||'gemini-3.7-flash')}"/></div>`;
  h += '</div>';
  h += '<div style="display:flex;gap:8px;">';
  h += '<div style="flex:1;"><label style="font-size:11px;opacity:0.8;">추론 수준 (Effort)</label>';
  h += `<select id="prov-agy-effort" style="width:100%;box-sizing:border-box;margin-top:2px;">
    <option value="low" ${agy.effort==='low'?'selected':''}>low</option>
    <option value="medium" ${agy.effort==='medium'||!agy.effort?'selected':''}>medium</option>
    <option value="high" ${agy.effort==='high'?'selected':''}>high</option>
  </select></div>`;
  h += `<div style="flex:1;"><label style="font-size:11px;opacity:0.8;">타임아웃 (초)</label><input id="prov-agy-timeout" type="number" style="width:100%;box-sizing:border-box;margin-top:2px;" value="${agy.timeout||120.0}"/></div>`;
  h += `<div style="flex:1;"><label style="font-size:11px;opacity:0.8;">동시성</label><input id="prov-agy-concurrency" type="number" style="width:100%;box-sizing:border-box;margin-top:2px;" value="${agy.max_concurrency||2}"/></div>`;
  h += '</div>';
  h += '</div>';
  h += '<div id="prov-test-res-antigravity" style="display:none;margin-top:8px;font-size:11px;"></div>';
  h += '</div>';

  // 4. Codex CLI Card
  const cdxBadge = cdx.installed
    ? '<span style="color:var(--accent2,#1a7f37);font-size:11px;background:rgba(26,127,55,0.1);padding:1px 6px;border-radius:4px;">감지됨</span>'
    : '<span style="color:var(--muted,#888);font-size:11px;background:rgba(136,136,136,0.1);padding:1px 6px;border-radius:4px;">미설치</span>';
  h += '<div style="border:1px solid var(--border);border-radius:6px;padding:10px;background:var(--sec-bg);margin-bottom:14px;">';
  h += '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:6px;">';
  h += `<strong style="font-size:13px;">💻 Codex CLI ${cdxBadge}</strong>`;
  h += '<button type="button" class="sec" style="font-size:11px;padding:2px 8px;" onclick="testProviderConnection(\'codex\')">CLI 확인</button>';
  h += '</div>';
  h += '<p style="margin:0 0 8px;font-size:11px;color:var(--muted,#888);">네이티브 호스트 전용 (Docker 환경에서는 자동 거부됩니다)</p>';
  h += '<div style="display:flex;flex-direction:column;gap:8px;font-size:12px;">';
  h += '<div style="display:flex;gap:8px;">';
  h += `<div style="flex:1;"><label style="font-size:11px;opacity:0.8;">바이너리 (Bin)</label><input id="prov-cdx-bin" style="width:100%;box-sizing:border-box;margin-top:2px;" value="${esc(cdx.bin||'codex')}"/></div>`;
  h += `<div style="flex:1;"><label style="font-size:11px;opacity:0.8;">모델 (공란시 계정 기본)</label><input id="prov-cdx-model" style="width:100%;box-sizing:border-box;margin-top:2px;" placeholder="기본 모델" value="${esc(cdx.model||'')}"/></div>`;
  h += '</div>';
  h += '<div style="display:flex;gap:8px;">';
  h += '<div style="flex:1;"><label style="font-size:11px;opacity:0.8;">추론 수준 (Effort)</label>';
  h += `<select id="prov-cdx-effort" style="width:100%;box-sizing:border-box;margin-top:2px;">
    <option value="low" ${cdx.effort==='low'?'selected':''}>low</option>
    <option value="medium" ${cdx.effort==='medium'||!cdx.effort?'selected':''}>medium</option>
    <option value="high" ${cdx.effort==='high'?'selected':''}>high</option>
  </select></div>`;
  h += `<div style="flex:1;"><label style="font-size:11px;opacity:0.8;">타임아웃 (초)</label><input id="prov-cdx-timeout" type="number" style="width:100%;box-sizing:border-box;margin-top:2px;" value="${cdx.timeout||300.0}"/></div>`;
  h += `<div style="flex:1;"><label style="font-size:11px;opacity:0.8;">동시성</label><input id="prov-cdx-concurrency" type="number" style="width:100%;box-sizing:border-box;margin-top:2px;" value="${cdx.max_concurrency||1}"/></div>`;
  h += '</div>';
  h += '</div>';
  h += '<div id="prov-test-res-codex" style="display:none;margin-top:8px;font-size:11px;"></div>';
  h += '</div>';

  // 5. OpenAI 호환 Card
  h += '<div style="border:1px solid var(--border);border-radius:6px;padding:10px;background:var(--sec-bg);margin-bottom:14px;">';
  h += '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px;">';
  h += '<strong style="font-size:13px;">⚡ OpenAI / 하이퍼스케일러 호환</strong>';
  h += '<button type="button" class="sec" style="font-size:11px;padding:2px 8px;" onclick="testProviderConnection(\'openai\')">설정 검증</button>';
  h += '</div>';
  h += '<div style="display:flex;flex-direction:column;gap:8px;font-size:12px;">';
  h += '<div><label style="font-size:11px;opacity:0.8;">API Key</label>';
  h += '<div style="display:flex;gap:4px;margin-top:2px;">';
  h += `<input id="prov-oai-key" type="password" style="flex:1;box-sizing:border-box;" placeholder="${oai.has_api_key ? '설정됨 (••••••••)' : 'API Key 입력'}"/>`;
  h += '<button type="button" class="sec" style="font-size:11px;padding:2px 6px;" onclick="togglePassVisibility(\'prov-oai-key\')">👁️</button>';
  if(oai.has_api_key){
    h += '<button type="button" class="sec danger-btn" style="font-size:11px;padding:2px 6px;color:var(--err,#cf222e);" onclick="clearApiKeyInput(\'prov-oai-key\', \'openai\')">삭제</button>';
  }
  h += '</div></div>';
  h += '<div style="display:flex;gap:8px;">';
  h += `<div style="flex:1;"><label style="font-size:11px;opacity:0.8;">Base URL</label><input id="prov-oai-url" style="width:100%;box-sizing:border-box;margin-top:2px;" value="${esc(oai.base_url||'https://api.openai.com/v1')}"/></div>`;
  h += `<div style="flex:1;"><label style="font-size:11px;opacity:0.8;">모델</label><input id="prov-oai-model" style="width:100%;box-sizing:border-box;margin-top:2px;" value="${esc(oai.model||'gpt-4o-mini')}"/></div>`;
  h += '</div>';
  h += '<div><label style="font-size:11px;opacity:0.8;">임베딩 모델</label>';
  h += `<input id="prov-oai-embed" style="width:100%;box-sizing:border-box;margin-top:2px;" value="${esc(oai.embed_model||'text-embedding-3-small')}"/></div>`;
  h += '</div>';
  h += '<div id="prov-test-res-openai" style="display:none;margin-top:8px;font-size:11px;"></div>';
  h += '</div>';

  // 6. Jev & STT Group Card
  h += '<div style="border:1px solid var(--border);border-radius:6px;padding:10px;background:var(--sec-bg);margin-bottom:14px;">';
  h += '<strong style="font-size:13px;display:block;margin-bottom:8px;">🧩 보조 엔진 (Jev &amp; 음성 전사)</strong>';
  h += '<div style="display:flex;flex-direction:column;gap:10px;font-size:12px;">';
  // Jev
  h += '<div>';
  h += `<label style="cursor:pointer;font-weight:600;"><input id="prov-jev-enabled" type="checkbox" ${jev.enabled?'checked':''} style="width:auto;margin-right:6px;vertical-align:middle;"/>TypeSafe AI Jev (엔티티 해소 shadow 평가 · 판정 비관여)</label>`;
  h += '<div style="margin-top:6px;display:flex;gap:4px;">';
  h += `<input id="prov-jev-key" type="password" style="flex:1;box-sizing:border-box;" placeholder="${jev.has_api_key ? 'Jev 키 설정됨 (••••••••)' : 'Jev API Key'}"/>`;
  h += '<button type="button" class="sec" style="font-size:11px;padding:2px 6px;" onclick="togglePassVisibility(\'prov-jev-key\')">👁️</button>';
  h += '</div>';
  h += `<input id="prov-jev-url" style="width:100%;box-sizing:border-box;margin-top:4px;" placeholder="Base URL" value="${esc(jev.base_url||'https://api.typesafe.ai/v1')}"/>`;
  h += '</div>';
  // STT
  h += '<div style="border-top:1px dashed var(--border);padding-top:8px;">';
  h += `<label style="cursor:pointer;font-weight:600;"><input id="prov-stt-enabled" type="checkbox" ${stt.enabled!==false?'checked':''} style="width:auto;margin-right:6px;vertical-align:middle;"/>비디오 &amp; 오디오 음성 전사 (STT)</label>`;
  h += '<div style="display:flex;gap:8px;margin-top:6px;">';
  h += '<div style="flex:1;"><label style="font-size:11px;opacity:0.8;">STT 프로바이더</label>';
  h += `<select id="prov-stt-prov" style="width:100%;box-sizing:border-box;margin-top:2px;">
    <option value="gemini" ${stt.provider==='gemini'?'selected':''}>Gemini (API)</option>
    <option value="mock" ${stt.provider==='mock'?'selected':''}>Mock</option>
  </select></div>`;
  h += `<div style="flex:1;"><label style="font-size:11px;opacity:0.8;">언어 코드</label><input id="prov-stt-lang" style="width:100%;box-sizing:border-box;margin-top:2px;" value="${esc(stt.language||'ko')}"/></div>`;
  h += '</div>';
  h += '</div>';
  h += '</div>';
  h += '</div>';

  // 7. 하단 저장 버튼
  h += '<div style="display:flex;gap:8px;margin-top:16px;">';
  h += '<button type="button" class="btn" style="flex:1;background:var(--btn-bg,#1f883d);color:#fff;font-weight:600;padding:8px 12px;border:none;border-radius:6px;cursor:pointer;" onclick="saveProviderSettings()">💾 설정 저장</button>';
  h += '<button type="button" class="sec" style="padding:8px 12px;" onclick="closeDrawer()">닫기</button>';
  h += '</div>';

  panel.innerHTML = h;
}

function togglePassVisibility(id){
  const el = document.getElementById(id);
  if(!el) return;
  el.type = el.type === 'password' ? 'text' : 'password';
}

function clearApiKeyInput(inputId, provName){
  const el = document.getElementById(inputId);
  if(el){
    el.value = '';
    el.placeholder = 'API Key 입력 (비어있음)';
    el.dataset.cleared = 'true';
  }
}

async function testProviderConnection(provName){
  const resEl = document.getElementById('prov-test-res-' + provName);
  if(resEl){
    resEl.style.display = 'block';
    resEl.style.color = 'var(--muted,#888)';
    resEl.textContent = '⏳ 테스트 실행 중…';
  }
  const payload = { provider: provName, config: {} };
  if(provName === 'gemini'){
    const kEl = document.getElementById('prov-gemini-key');
    if(kEl && kEl.value) payload.config.api_key = kEl.value;
    const mEl = document.getElementById('prov-gemini-model');
    if(mEl && mEl.value) payload.config.model = mEl.value;
  } else if(provName === 'antigravity'){
    const bEl = document.getElementById('prov-agy-bin');
    if(bEl && bEl.value) payload.config.bin = bEl.value;
  } else if(provName === 'codex'){
    const bEl = document.getElementById('prov-cdx-bin');
    if(bEl && bEl.value) payload.config.bin = bEl.value;
  } else if(provName === 'openai'){
    const kEl = document.getElementById('prov-oai-key');
    if(kEl && kEl.value) payload.config.api_key = kEl.value;
  }
  try{
    const r = await fetch('providers/test', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload)
    });
    const d = await r.json();
    if(resEl){
      if(d.ok){
        resEl.style.color = 'var(--accent2,#1a7f37)';
        resEl.textContent = '✅ ' + (d.message || '연결 성공');
      } else {
        resEl.style.color = 'var(--err,#cf222e)';
        resEl.textContent = '❌ ' + (d.error || '연결 실패');
      }
    }
  }catch(e){
    if(resEl){
      resEl.style.color = 'var(--err,#cf222e)';
      resEl.textContent = '❌ 통신 오류: ' + (e.message || e);
    }
  }
}

async function saveProviderSettings(){
  if(!_provConfig) return;
  const activeProv = document.getElementById('prov-active-select').value;
  const gemKey = document.getElementById('prov-gemini-key');
  const gemModel = document.getElementById('prov-gemini-model').value.trim();
  const gemEffort = document.getElementById('prov-gemini-effort').value;
  const gemEmbed = document.getElementById('prov-gemini-embed').value.trim();
  const gemInterval = parseFloat(document.getElementById('prov-gemini-interval').value) || 4.0;
  const gemRetries = parseInt(document.getElementById('prov-gemini-retries').value, 10) || 5;

  const agyBin = document.getElementById('prov-agy-bin').value.trim();
  const agyModel = document.getElementById('prov-agy-model').value.trim();
  const agyEffort = document.getElementById('prov-agy-effort').value;
  const agyTimeout = parseFloat(document.getElementById('prov-agy-timeout').value) || 120.0;
  const agyConc = parseInt(document.getElementById('prov-agy-concurrency').value, 10) || 2;

  const cdxBin = document.getElementById('prov-cdx-bin').value.trim();
  const cdxModel = document.getElementById('prov-cdx-model').value.trim();
  const cdxEffort = document.getElementById('prov-cdx-effort').value;
  const cdxTimeout = parseFloat(document.getElementById('prov-cdx-timeout').value) || 300.0;
  const cdxConc = parseInt(document.getElementById('prov-cdx-concurrency').value, 10) || 1;

  const oaiKey = document.getElementById('prov-oai-key');
  const oaiUrl = document.getElementById('prov-oai-url').value.trim();
  const oaiModel = document.getElementById('prov-oai-model').value.trim();
  const oaiEmbed = document.getElementById('prov-oai-embed').value.trim();

  const jevEnabled = document.getElementById('prov-jev-enabled').checked;
  const jevKey = document.getElementById('prov-jev-key');
  const jevUrl = document.getElementById('prov-jev-url').value.trim();

  const sttEnabled = document.getElementById('prov-stt-enabled').checked;
  const sttProv = document.getElementById('prov-stt-prov').value;
  const sttLang = document.getElementById('prov-stt-lang').value.trim();

  const updatePayload = {
    active_provider: activeProv,
    providers: {
      gemini: {
        model: gemModel,
        effort: gemEffort,
        embed_model: gemEmbed,
        min_interval: gemInterval,
        max_retries: gemRetries
      },
      antigravity: {
        bin: agyBin,
        model: agyModel,
        effort: agyEffort,
        timeout: agyTimeout,
        max_concurrency: agyConc
      },
      codex: {
        bin: cdxBin,
        model: cdxModel,
        effort: cdxEffort,
        timeout: cdxTimeout,
        max_concurrency: cdxConc
      },
      openai: {
        base_url: oaiUrl,
        model: oaiModel,
        embed_model: oaiEmbed
      },
      jev: {
        enabled: jevEnabled,
        base_url: jevUrl
      },
      stt: {
        enabled: sttEnabled,
        provider: sttProv,
        language: sttLang
      }
    }
  };

  if(gemKey && gemKey.value) updatePayload.providers.gemini.api_key = gemKey.value;
  else if(gemKey && gemKey.dataset.cleared === 'true') updatePayload.providers.gemini.clear_api_key = true;

  if(oaiKey && oaiKey.value) updatePayload.providers.openai.api_key = oaiKey.value;
  else if(oaiKey && oaiKey.dataset.cleared === 'true') updatePayload.providers.openai.clear_api_key = true;

  if(jevKey && jevKey.value) updatePayload.providers.jev.api_key = jevKey.value;
  else if(jevKey && jevKey.dataset.cleared === 'true') updatePayload.providers.jev.clear_api_key = true;

  try{
    const r = await fetch('providers', {
      method: 'PATCH',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(updatePayload)
    });
    if(!r.ok){
      const err = await r.json().catch(()=>({}));
      renderProviderManager('저장 실패: ' + (err.detail || ('HTTP ' + r.status)), false);
      return;
    }
    const resData = await r.json();
    _provConfig = resData.config;
    renderProviderManager('프로바이더 설정이 성공적으로 저장되었습니다 (data/providers.json 반영 완료).', true);
  }catch(e){
    renderProviderManager('오류 발생: ' + (e.message || e), false);
  }
}

let _limitConfig = null;

async function openLimitManager(){
  if(!canWrite()) return;
  openDetailPane();
  panel.innerHTML = '<h2>📏 적재 제한 관리</h2><p class="hint">설정 로딩 중…</p>';
  try{
    const r = await fetch('limits');
    if(!r.ok) throw new Error('HTTP ' + r.status);
    const data = await r.json();
    _limitConfig = data;
    renderLimitManager();
  }catch(e){
    panel.innerHTML = `<h2>📏 적재 제한 관리</h2><p class="hint" style="color:var(--err,#cf222e)">설정을 불러올 수 없습니다: ${esc(e.message||e)}</p>`;
  }
}

function renderLimitManager(msg, isSuccess){
  if(!_limitConfig) return;
  const cfg = _limitConfig;
  const lims = cfg.limits || {};

  let alertHtml = '';
  if(msg){
    const color = isSuccess ? 'var(--accent2,#1a7f37)' : 'var(--err,#cf222e)';
    const bg = isSuccess ? 'rgba(26,127,55,0.1)' : 'rgba(207,34,46,0.1)';
    const icon = isSuccess ? '✅' : '⚠️';
    alertHtml = `<div style="padding:8px 10px;border-radius:6px;background:${bg};color:${color};font-size:12px;margin-bottom:12px;border:1px solid ${color};">${icon} ${esc(msg)}</div>`;
  }

  let h = '<h2>📏 상황 별 적재 제한 관리</h2>';
  h += '<p class="al">수집 경로 및 문서 유형(웹, PDF, 슬라이싱, 벡터 임베딩, 확장, 미디어 등)별 적재 제한과 글자 수 예산을 단독 기능으로 설정합니다. 저장된 설정은 <code>data/ingest_limits.json</code>에 즉시 영구 저장됩니다.</p>';
  h += alertHtml;

  // 1. 일반 웹 / 텍스트 수집 상황
  h += '<div style="border:1px solid var(--border);border-radius:6px;padding:10px;background:var(--sec-bg);margin-bottom:14px;">';
  h += '<strong style="font-size:13px;display:block;margin-bottom:8px;">🌐 일반 웹 / 텍스트 수집 제한</strong>';
  h += '<div style="display:flex;flex-direction:column;gap:8px;font-size:12px;">';
  h += '<div>';
  h += '<label style="font-size:11px;opacity:0.8;display:block;">원문 수집 본문 상한 (raw_char_budget, 글자 수)</label>';
  h += `<input id="limit-raw-char-budget" type="number" min="0" style="width:100%;box-sizing:border-box;margin-top:2px;" value="${lims.raw_char_budget ?? 20000}"/>`;
  h += '<div style="font-size:10px;opacity:0.7;margin-top:2px;">웹, 일반 텍스트 파일, X, 유튜브 수집 시 보관할 최대 본문 분량</div>';
  h += '</div>';
  h += '</div>';
  h += '</div>';

  // 2. PDF 문서 수집 및 학술 논문 처리 상황
  h += '<div style="border:1px solid var(--border);border-radius:6px;padding:10px;background:var(--sec-bg);margin-bottom:14px;">';
  h += '<strong style="font-size:13px;display:block;margin-bottom:8px;">📄 PDF 문서 수집 및 논문 판정 제한</strong>';
  h += '<div style="display:flex;flex-direction:column;gap:8px;font-size:12px;">';
  h += '<div style="display:flex;gap:8px;">';
  h += `<div style="flex:1;"><label style="font-size:11px;opacity:0.8;">PDF 최대 추출 분량 (글자 수)</label><input id="limit-pdf-max-extract" type="number" min="0" style="width:100%;box-sizing:border-box;margin-top:2px;" value="${lims.pdf_max_extract_chars ?? 50000}"/></div>`;
  h += `<div style="flex:1;"><label style="font-size:11px;opacity:0.8;">논문 판정 기준 분량 (글자 수)</label><input id="limit-pdf-paper-threshold" type="number" min="0" style="width:100%;box-sizing:border-box;margin-top:2px;" value="${lims.pdf_paper_threshold_chars ?? 15000}"/></div>`;
  h += '</div>';
  h += '<div><label style="font-size:11px;opacity:0.8;">PDF 파서 엔진</label>';
  h += `<select id="limit-pdf-parser" style="width:100%;box-sizing:border-box;margin-top:2px;">
    <option value="default" selected>기본 Chromium PDFium 고속 파서</option>
  </select></div>`;
  h += '<div style="display:flex;flex-direction:column;gap:4px;margin-top:4px;">';
  h += `<label style="cursor:pointer;"><input id="limit-pdf-exclude-appendix" type="checkbox" ${lims.pdf_exclude_appendix!==false?'checked':''} style="width:auto;margin-right:6px;vertical-align:middle;"/>논문 부록(Appendix) 자동 제외</label>`;
  h += `<label style="cursor:pointer;"><input id="limit-pdf-exclude-references" type="checkbox" ${lims.pdf_exclude_references!==false?'checked':''} style="width:auto;margin-right:6px;vertical-align:middle;"/>논문 참고문헌(References) 자동 제외</label>`;
  h += `<label style="cursor:pointer;"><input id="limit-pdf-detect-encoding-flaws" type="checkbox" ${lims.pdf_detect_encoding_flaws!==false?'checked':''} style="width:auto;margin-right:6px;vertical-align:middle;"/>텍스트 인코딩 결함 및 스캔본 자동 감지</label>`;
  h += '</div>';
  h += '<div style="display:flex;gap:8px;margin-top:4px;">';
  h += `<div style="flex:1;"><label style="font-size:11px;opacity:0.8;">논문 추론 수준</label>
    <select id="limit-pdf-paper-effort" style="width:100%;box-sizing:border-box;margin-top:2px;">
      <option value="low" ${lims.pdf_paper_effort==='low'?'selected':''}>low</option>
      <option value="medium" ${lims.pdf_paper_effort==='medium'?'selected':''}>medium</option>
      <option value="high" ${(lims.pdf_paper_effort||'high')==='high'?'selected':''}>high</option>
    </select>
  </div>`;
  h += `<div style="flex:1;"><label style="font-size:11px;opacity:0.8;">단문/일반 추론 수준</label>
    <select id="limit-pdf-default-effort" style="width:100%;box-sizing:border-box;margin-top:2px;">
      <option value="" ${!lims.pdf_default_effort?'selected':''}>기본값(공란)</option>
      <option value="low" ${lims.pdf_default_effort==='low'?'selected':''}>low</option>
      <option value="medium" ${lims.pdf_default_effort==='medium'?'selected':''}>medium</option>
      <option value="high" ${lims.pdf_default_effort==='high'?'selected':''}>high</option>
    </select>
  </div>`;
  h += `<div style="flex:1;"><label style="font-size:11px;opacity:0.8;">1차 분류 추론 수준</label>
    <select id="limit-pdf-classifier-effort" style="width:100%;box-sizing:border-box;margin-top:2px;">
      <option value="low" ${(lims.pdf_classifier_effort||'low')==='low'?'selected':''}>low</option>
      <option value="medium" ${lims.pdf_classifier_effort==='medium'?'selected':''}>medium</option>
      <option value="high" ${lims.pdf_classifier_effort==='high'?'selected':''}>high</option>
    </select>
  </div>`;
  h += '</div>';
  h += '</div>';
  h += '</div>';

  // 3. 지식 그래프(KG) 추출 및 슬라이싱 상황
  h += '<div style="border:1px solid var(--border);border-radius:6px;padding:10px;background:var(--sec-bg);margin-bottom:14px;">';
  h += '<strong style="font-size:13px;display:block;margin-bottom:8px;">🧩 지식 그래프(KG) 추출 및 슬라이싱 예산</strong>';
  h += '<div style="display:flex;flex-direction:column;gap:8px;font-size:12px;">';
  h += '<div style="display:flex;gap:8px;">';
  h += `<div style="flex:1;"><label style="font-size:11px;opacity:0.8;">단일 문서 추출 투입 상한 (글자 수)</label><input id="limit-extract-char-budget" type="number" min="0" style="width:100%;box-sizing:border-box;margin-top:2px;" value="${lims.extract_char_budget ?? 20000}"/></div>`;
  h += `<div style="flex:1;"><label style="font-size:11px;opacity:0.8;">병합 문서 투입 상한 (0=2배 자동)</label><input id="limit-merged-extract-budget" type="number" min="0" style="width:100%;box-sizing:border-box;margin-top:2px;" value="${lims.merged_extract_char_budget ?? 0}"/></div>`;
  h += '</div>';
  h += '<div><label style="font-size:11px;opacity:0.8;">본문 슬라이싱 전략</label>';
  h += `<select id="limit-slicing-strategy" style="width:100%;box-sizing:border-box;margin-top:2px;">
    <option value="table-exemption" ${(lims.slicing_strategy||'table-exemption')==='table-exemption'?'selected':''}>표 보존형 (table-exemption, 테이블 데이터 완전 보존)</option>
    <option value="strict" ${lims.slicing_strategy==='strict'?'selected':''}>단순 절단형 (strict, 글자 수 한도 엄격 절단)</option>
  </select></div>`;
  h += '</div>';
  h += '</div>';

  // 4. 임베딩 벡터, 1홉 확장, 리서치 맥락 상황
  h += '<div style="border:1px solid var(--border);border-radius:6px;padding:10px;background:var(--sec-bg);margin-bottom:14px;">';
  h += '<strong style="font-size:13px;display:block;margin-bottom:8px;">🧭 임베딩·1홉 확장·리서치 맥락 제한</strong>';
  h += '<div style="display:flex;flex-direction:column;gap:8px;font-size:12px;">';
  h += '<div style="display:flex;gap:8px;">';
  h += `<div style="flex:1;"><label style="font-size:11px;opacity:0.8;">임베딩 벡터 본문 상한 (글자 수)</label><input id="limit-embed-budget" type="number" min="0" style="width:100%;box-sizing:border-box;margin-top:2px;" value="${lims.embed_char_budget ?? 8000}"/></div>`;
  h += `<div style="flex:1;"><label style="font-size:11px;opacity:0.8;">심층 리서치 맥락 상한 (글자 수)</label><input id="limit-research-budget" type="number" min="0" style="width:100%;box-sizing:border-box;margin-top:2px;" value="${lims.research_context_budget ?? 8000}"/></div>`;
  h += '</div>';
  h += '<div style="display:flex;gap:8px;">';
  h += `<div style="flex:1;"><label style="font-size:11px;opacity:0.8;">1홉 확장 선별 컨텍스트 (글자 수)</label><input id="limit-expand-budget" type="number" min="0" style="width:100%;box-sizing:border-box;margin-top:2px;" value="${lims.expand_char_budget ?? 2000}"/></div>`;
  h += `<div style="flex:1;"><label style="font-size:11px;opacity:0.8;">1홉 확장 최대 링크 수</label><input id="limit-expand-max" type="number" min="0" style="width:100%;box-sizing:border-box;margin-top:2px;" value="${lims.expand_max ?? 5}"/></div>`;
  h += '</div>';
  h += '</div>';
  h += '</div>';

  // 5. 미디어 및 첨부파일 적재 제한
  h += '<div style="border:1px solid var(--border);border-radius:6px;padding:10px;background:var(--sec-bg);margin-bottom:14px;">';
  h += '<strong style="font-size:13px;display:block;margin-bottom:8px;">🎬 미디어 및 첨부파일 적재 제한</strong>';
  h += '<div style="display:flex;flex-direction:column;gap:8px;font-size:12px;">';
  h += '<div>';
  h += '<label style="font-size:11px;opacity:0.8;display:block;">프레젠테이션 PDF 첨부파일 용량 상한 (bytes, 기본 64MiB = 67,108,864)</label>';
  h += `<input id="limit-presentation-pdf-bytes" type="number" min="1" style="width:100%;box-sizing:border-box;margin-top:2px;" value="${lims.presentation_pdf_max_bytes ?? 67108864}"/>`;
  h += '</div>';
  h += '<div style="display:flex;gap:8px;">';
  h += `<div style="flex:1;"><label style="font-size:11px;opacity:0.8;">비디오 STT 최대 추출 분량</label><input id="limit-video-max-extract" type="number" min="0" style="width:100%;box-sizing:border-box;margin-top:2px;" value="${lims.video_max_extract_chars ?? 200000}"/></div>`;
  h += `<div style="flex:1;"><label style="font-size:11px;opacity:0.8;">비디오 분할 단위 (초)</label><input id="limit-video-chunk-sec" type="number" min="1" style="width:100%;box-sizing:border-box;margin-top:2px;" value="${lims.video_chunk_duration_sec ?? 240}"/></div>`;
  h += '</div>';
  h += '<div>';
  h += '<label style="font-size:11px;opacity:0.8;display:block;">VLM 시각 자료 최대 이미지 수</label>';
  h += `<input id="limit-vlm-max-images" type="number" min="0" style="width:100%;box-sizing:border-box;margin-top:2px;" value="${lims.vlm_max_images ?? 20}"/>`;
  h += '</div>';
  h += '</div>';
  h += '</div>';

  // 6. 하단 버튼
  h += '<div style="display:flex;gap:8px;margin-top:16px;">';
  h += '<button type="button" class="btn" style="flex:1;background:var(--btn-bg,#1f883d);color:#fff;font-weight:600;padding:8px 12px;border:none;border-radius:6px;cursor:pointer;" onclick="saveLimitSettings()">💾 설정 저장</button>';
  h += '<button type="button" class="sec" style="padding:8px 12px;" onclick="resetLimitSettings()">↩️ 기본값 복원</button>';
  h += '<button type="button" class="sec" style="padding:8px 12px;" onclick="closeDrawer()">닫기</button>';
  h += '</div>';

  panel.innerHTML = h;
}

async function saveLimitSettings(){
  if(!_limitConfig) return;
  const rawBudget = parseInt(document.getElementById('limit-raw-char-budget').value, 10);
  const pdfMax = parseInt(document.getElementById('limit-pdf-max-extract').value, 10);
  const pdfPaper = parseInt(document.getElementById('limit-pdf-paper-threshold').value, 10);
  const pdfParser = document.getElementById('limit-pdf-parser').value;
  const pdfExApp = document.getElementById('limit-pdf-exclude-appendix').checked;
  const pdfExRef = document.getElementById('limit-pdf-exclude-references').checked;
  const pdfDetectFlaws = document.getElementById('limit-pdf-detect-encoding-flaws').checked;
  const pdfPaperEff = document.getElementById('limit-pdf-paper-effort').value;
  const pdfDefEff = document.getElementById('limit-pdf-default-effort').value;
  const pdfClassEff = document.getElementById('limit-pdf-classifier-effort').value;

  const extBudget = parseInt(document.getElementById('limit-extract-char-budget').value, 10);
  const mergedExtBudget = parseInt(document.getElementById('limit-merged-extract-budget').value, 10);
  const slicingStrat = document.getElementById('limit-slicing-strategy').value;

  const embedBudget = parseInt(document.getElementById('limit-embed-budget').value, 10);
  const expandBudget = parseInt(document.getElementById('limit-expand-budget').value, 10);
  const expandMax = parseInt(document.getElementById('limit-expand-max').value, 10);
  const researchBudget = parseInt(document.getElementById('limit-research-budget').value, 10);

  const presPdfBytes = parseInt(document.getElementById('limit-presentation-pdf-bytes').value, 10);
  const videoMax = parseInt(document.getElementById('limit-video-max-extract').value, 10);
  const videoChunk = parseInt(document.getElementById('limit-video-chunk-sec').value, 10);
  const vlmMax = parseInt(document.getElementById('limit-vlm-max-images').value, 10);

  const payload = {
    limits: {
      raw_char_budget: isNaN(rawBudget) ? 20000 : rawBudget,
      pdf_max_extract_chars: isNaN(pdfMax) ? 50000 : pdfMax,
      pdf_paper_threshold_chars: isNaN(pdfPaper) ? 15000 : pdfPaper,
      pdf_parser: pdfParser,
      pdf_exclude_appendix: pdfExApp,
      pdf_exclude_references: pdfExRef,
      pdf_detect_encoding_flaws: pdfDetectFlaws,
      pdf_paper_effort: pdfPaperEff,
      pdf_default_effort: pdfDefEff,
      pdf_classifier_effort: pdfClassEff,
      extract_char_budget: isNaN(extBudget) ? 20000 : extBudget,
      merged_extract_char_budget: isNaN(mergedExtBudget) ? 0 : mergedExtBudget,
      slicing_strategy: slicingStrat,
      embed_char_budget: isNaN(embedBudget) ? 8000 : embedBudget,
      expand_char_budget: isNaN(expandBudget) ? 2000 : expandBudget,
      expand_max: isNaN(expandMax) ? 5 : expandMax,
      research_context_budget: isNaN(researchBudget) ? 8000 : researchBudget,
      presentation_pdf_max_bytes: isNaN(presPdfBytes) ? 67108864 : presPdfBytes,
      video_max_extract_chars: isNaN(videoMax) ? 200000 : videoMax,
      video_chunk_duration_sec: isNaN(videoChunk) ? 240 : videoChunk,
      vlm_max_images: isNaN(vlmMax) ? 20 : vlmMax
    }
  };

  try{
    const r = await fetch('limits', {
      method: 'PATCH',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload)
    });
    if(!r.ok){
      const err = await r.json().catch(()=>({}));
      renderLimitManager('저장 실패: ' + (err.detail || ('HTTP ' + r.status)), false);
      return;
    }
    const resData = await r.json();
    _limitConfig = resData.config;
    renderLimitManager('적재 제한 설정이 성공적으로 저장되었습니다 (data/ingest_limits.json 반영 완료).', true);
  }catch(e){
    renderLimitManager('오류 발생: ' + (e.message || e), false);
  }
}

async function resetLimitSettings(){
  if(!confirm('적재 제한 설정을 시스템 표준 기본값으로 복원하시겠습니까?')) return;
  const defaults = {
    limits: {
      raw_char_budget: 20000,
      pdf_max_extract_chars: 50000,
      pdf_paper_threshold_chars: 15000,
      pdf_parser: 'default',
      pdf_exclude_appendix: true,
      pdf_exclude_references: true,
      pdf_detect_encoding_flaws: true,
      pdf_paper_effort: 'high',
      pdf_default_effort: '',
      pdf_classifier_effort: 'low',
      extract_char_budget: 20000,
      merged_extract_char_budget: 0,
      slicing_strategy: 'table-exemption',
      embed_char_budget: 8000,
      expand_char_budget: 2000,
      expand_max: 5,
      research_context_budget: 8000,
      presentation_pdf_max_bytes: 67108864,
      video_max_extract_chars: 200000,
      video_chunk_duration_sec: 240,
      vlm_max_images: 20
    }
  };
  try{
    const r = await fetch('limits', {
      method: 'PATCH',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(defaults)
    });
    if(!r.ok){
      const err = await r.json().catch(()=>({}));
      renderLimitManager('기본값 복원 실패: ' + (err.detail || ('HTTP ' + r.status)), false);
      return;
    }
    const resData = await r.json();
    _limitConfig = resData.config;
    renderLimitManager('적재 제한 설정이 기본값으로 초기화되었습니다.', true);
  }catch(e){
    renderLimitManager('오류 발생: ' + (e.message || e), false);
  }
}

let _tokenList = [];

async function openTokenManager(msg, isSuccess, newCreatedToken){
  if(!canWrite()) return;
  openDetailPane();
  panel.innerHTML = '<h2>🔑 API &amp; MCP 상시 이용 키 관리</h2><p class="hint">토큰 목록 조회 중…</p>';
  try{
    const r = await fetch('tokens');
    if(!r.ok) throw new Error('HTTP ' + r.status);
    const data = await r.json();
    _tokenList = data.tokens || [];
    renderTokenManager(msg, isSuccess, newCreatedToken);
  }catch(e){
    panel.innerHTML = `<h2>🔑 API &amp; MCP 상시 이용 키 관리</h2><p class="hint" style="color:var(--err,#cf222e)">토큰 목록을 불러올 수 없습니다: ${esc(e.message||e)}</p>`;
  }
}

function renderTokenManager(msg, isSuccess, newCreatedToken){
  let alertHtml = '';
  if(msg){
    const color = isSuccess ? 'var(--accent2,#1a7f37)' : 'var(--err,#cf222e)';
    const bg = isSuccess ? 'rgba(26,127,55,0.1)' : 'rgba(207,34,46,0.1)';
    const icon = isSuccess ? '✅' : '⚠️';
    alertHtml = `<div style="padding:8px 10px;border-radius:6px;background:${bg};color:${color};font-size:12px;margin-bottom:12px;border:1px solid ${color};">${icon} ${esc(msg)}</div>`;
  }

  let h = '<h2>🔑 API &amp; MCP 상시 이용 키 관리</h2>';
  h += '<p class="al">Claude Desktop, Cursor, 백그라운드 데몬 및 외부 API 연동을 위한 상시 이용(24/7) 토큰을 관리합니다. 환경변수 의존 없이 즉시 생성 및 폐기됩니다.</p>';
  h += alertHtml;

  if(newCreatedToken){
    const rawVal = newCreatedToken.token || '';
    const mcpOrigin = window.location.origin;
    const mcpUrl = `${mcpOrigin}/mcp`;
    h += '<div style="background:rgba(26,127,55,0.08);border:2px solid var(--accent2,#1a7f37);border-radius:8px;padding:12px;margin-bottom:16px;">';
    h += '<h3 style="margin:0 0 6px 0;font-size:14px;color:var(--accent2,#1a7f37);">🎉 새 토큰이 발급되었습니다</h3>';
    h += '<p style="margin:0 0 8px 0;font-size:12px;">이 토큰은 다시 전체 문자열로 표시되지 않습니다. 지금 복사하여 보관하세요.</p>';
    h += '<div style="display:flex;gap:6px;margin-bottom:8px;">';
    h += `<input type="text" id="newly-minted-token" readonly value="${esc(rawVal)}" style="flex:1;font-family:monospace;padding:6px 8px;font-size:12px;border:1px solid var(--border);border-radius:4px;background:var(--bg);" onclick="this.select();"/>`;
    h += '<button type="button" class="btn" style="padding:6px 10px;font-size:12px;background:var(--btn-bg,#1f883d);color:#fff;border:none;border-radius:4px;cursor:pointer;" onclick="navigator.clipboard.writeText(document.getElementById(\'newly-minted-token\').value).then(()=>alert(\'토큰이 클립보드에 복사되었습니다.\'))">📋 복사</button>';
    h += '</div>';
    h += '<details style="font-size:12px;color:var(--fg);"><summary style="cursor:pointer;font-weight:600;margin-bottom:4px;">📋 Claude Desktop / Cursor MCP 연동 JSON 보기</summary>';
    h += `<pre style="background:var(--bg);padding:8px;border-radius:4px;overflow-x:auto;font-size:11px;border:1px solid var(--border);">${esc(JSON.stringify({
      mcpServers: {
        "claire-bible": {
          command: "npx",
          args: ["-y", "@modelcontextprotocol/server-everything"],
          env: {
            CLAIRE_URL: mcpUrl,
            CLAIRE_TOKEN: rawVal
          }
        }
      }
    }, null, 2))}</pre></details>`;
    h += '</div>';
  }

  // 1. 새 토큰 발급 폼
  h += '<div style="border:1px solid var(--border);border-radius:6px;padding:12px;background:var(--sec-bg);margin-bottom:16px;">';
  h += '<h3 style="margin:0 0 10px 0;font-size:13px;">➕ 새 API &amp; MCP 토큰 발급</h3>';
  h += '<div style="display:flex;flex-direction:column;gap:10px;">';
  h += '<div>';
  h += '<label style="font-size:12px;font-weight:600;display:block;margin-bottom:4px;">토큰 이름 (용도 / 클라이언트 명칭)</label>';
  h += '<input type="text" id="new-token-name" placeholder="예: Claude Desktop, Cursor Agent, Backup Script" style="width:100%;box-sizing:border-box;padding:6px 8px;border:1px solid var(--border);border-radius:4px;background:var(--bg);color:var(--fg);font-size:13px;"/>';
  h += '</div>';

  h += '<div style="display:flex;gap:12px;flex-wrap:wrap;">';
  h += '<div style="flex:1;min-width:130px;">';
  h += '<label style="font-size:12px;font-weight:600;display:block;margin-bottom:4px;">권한 스코프 (Scope)</label>';
  h += '<select id="new-token-scope" style="width:100%;box-sizing:border-box;padding:6px 8px;border:1px solid var(--border);border-radius:4px;background:var(--bg);color:var(--fg);font-size:12px;">';
  h += '<option value="readonly" selected>읽기 전용 (readonly - MCP 조회 및 검색)</option>';
  h += '<option value="collaborator">협력자 (collaborator - 추가 테마 적재)</option>';
  h += '<option value="owner">소유자 (owner - 전체 읽기/쓰기/관리)</option>';
  h += '</select>';
  h += '</div>';

  h += '<div style="flex:1;min-width:130px;">';
  h += '<label style="font-size:12px;font-weight:600;display:block;margin-bottom:4px;">유효기간 (Expiration)</label>';
  h += '<select id="new-token-expiry" style="width:100%;box-sizing:border-box;padding:6px 8px;border:1px solid var(--border);border-radius:4px;background:var(--bg);color:var(--fg);font-size:12px;">';
  h += '<option value="0" selected>무기한 (상시 이용 가능)</option>';
  h += '<option value="30">30일</option>';
  h += '<option value="90">90일</option>';
  h += '<option value="365">1년 (365일)</option>';
  h += '</select>';
  h += '</div>';
  h += '</div>';

  h += '<button type="button" class="btn" style="margin-top:4px;background:var(--btn-bg,#1f883d);color:#fff;font-weight:600;padding:8px 12px;border:none;border-radius:6px;cursor:pointer;" onclick="createApiToken()">✨ 토큰 발급</button>';
  h += '</div>';
  h += '</div>';

  // 2. 발급된 토큰 목록
  h += '<h3 style="margin:0 0 8px 0;font-size:13px;">📋 발급된 토큰 목록 (' + _tokenList.length + '개)</h3>';
  if(!_tokenList.length){
    h += '<p class="hint">등록된 토큰이 없습니다.</p>';
  } else {
    h += '<div style="display:flex;flex-direction:column;gap:8px;">';
    for(const tok of _tokenList){
      const isRevoked = tok.revoked_at !== null && tok.revoked_at !== undefined;
      const nowTs = Date.now() / 1000;
      const isExpired = tok.expires_at && tok.expires_at < nowTs;
      let statusBadge = '<span style="color:#1a7f37;background:rgba(26,127,55,0.1);padding:2px 6px;border-radius:4px;font-size:11px;font-weight:600;">활성</span>';
      if(isRevoked){
        statusBadge = '<span style="color:#cf222e;background:rgba(207,34,46,0.1);padding:2px 6px;border-radius:4px;font-size:11px;font-weight:600;">폐기됨</span>';
      } else if(isExpired){
        statusBadge = '<span style="color:#9a6700;background:rgba(154,103,0,0.1);padding:2px 6px;border-radius:4px;font-size:11px;font-weight:600;">만료됨</span>';
      }

      let expStr = '무기한 (상시)';
      if(tok.expires_at){
        const d = new Date(tok.expires_at * 1000);
        expStr = d.toLocaleDateString() + ' ' + d.toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'});
      }

      let lastUsedStr = '사용 기록 없음';
      if(tok.last_used_at){
        const lu = new Date(tok.last_used_at * 1000);
        lastUsedStr = lu.toLocaleDateString() + ' ' + lu.toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'});
      }

      const scopeColors = {
        owner: 'var(--accent,#0969da)',
        collaborator: '#8250df',
        readonly: '#57606a'
      };
      const scopeCol = scopeColors[tok.scope] || '#57606a';

      h += `<div style="border:1px solid var(--border);border-radius:6px;padding:10px;background:var(--bg);opacity:${isRevoked ? '0.6' : '1'};">`;
      h += '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:6px;">';
      h += `<div><strong style="font-size:13px;">#${tok.id} ${esc(tok.name)}</strong> <span style="font-size:11px;font-weight:600;color:${scopeCol};border:1px solid ${scopeCol};padding:1px 5px;border-radius:4px;margin-left:4px;">${esc(tok.scope)}</span> ${statusBadge}</div>`;
      if(!isRevoked){
        h += `<button type="button" class="sec" style="font-size:11px;padding:2px 8px;color:var(--err,#cf222e);border-color:var(--err,#cf222e);" onclick="revokeApiToken(${tok.id}, '${esc(tok.name)}')">폐기</button>`;
      }
      h += '</div>';

      h += '<div style="font-size:12px;color:var(--sec-fg,#57606a);display:flex;flex-direction:column;gap:2px;">';
      h += `<div>식별자: <code style="font-size:11px;background:var(--sec-bg);padding:1px 4px;border-radius:3px;">${esc(tok.token_masked || 'cb_...')}</code></div>`;
      h += `<div>만료: ${esc(expStr)} · 최근 사용: ${esc(lastUsedStr)}</div>`;
      h += '</div>';
      h += '</div>';
    }
    h += '</div>';
  }

  h += '<div style="margin-top:16px;">';
  h += '<button type="button" class="sec" style="padding:8px 12px;width:100%;" onclick="closeDrawer()">닫기</button>';
  h += '</div>';

  panel.innerHTML = h;
}

async function createApiToken(){
  const nameInput = document.getElementById('new-token-name');
  const scopeSelect = document.getElementById('new-token-scope');
  const expirySelect = document.getElementById('new-token-expiry');
  if(!nameInput || !nameInput.value.trim()){
    alert('토큰 이름을 입력하세요.');
    return;
  }
  const name = nameInput.value.trim();
  const scope = scopeSelect ? scopeSelect.value : 'readonly';
  const expDays = expirySelect ? parseInt(expirySelect.value, 10) : 0;

  try{
    const r = await fetch('tokens', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        name: name,
        scope: scope,
        expires_in_days: expDays > 0 ? expDays : null
      })
    });
    if(!r.ok){
      const err = await r.json().catch(()=>({}));
      renderTokenManager('발급 실패: ' + (err.detail || ('HTTP ' + r.status)), false);
      return;
    }
    const data = await r.json();
    openTokenManager('토큰이 성공적으로 발급되었습니다.', true, data.token);
  }catch(e){
    renderTokenManager('오류 발생: ' + (e.message || e), false);
  }
}

async function revokeApiToken(id, name){
  if(!confirm(`정말 #${id} '${name}' 토큰을 폐기하시겠습니까? 즉시 모든 API 및 MCP 연결이 차단됩니다.`)) return;
  try{
    const r = await fetch('tokens/revoke', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({id: id})
    });
    if(!r.ok){
      const err = await r.json().catch(()=>({}));
      renderTokenManager('폐기 실패: ' + (err.detail || ('HTTP ' + r.status)), false);
      return;
    }
    openTokenManager(`토큰 #${id} '${name}'이(가) 즉시 폐기되었습니다.`, true);
  }catch(e){
    renderTokenManager('오류 발생: ' + (e.message || e), false);
  }
}

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
  const isSemantic = AUTH_SCOPE !== 'anonymous' && AUTH_SCOPE !== 'unknown';
  const searchMode = isSemantic ? 'hybrid' : 'fts';
  if(typeof window.gtag === 'function'){
    try{ window.gtag('event', 'search', { search_term: q, search_mode: searchMode }); }catch(_){}
  }
  const requestedMode = isSemantic ? 'AI 지식 검색' : '전문(FTS5) 검색';
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
  const actualMode=(d.mode==='fts'||searchMode==='fts')?'전문(FTS5) 검색':'AI 지식 검색';
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
        const samples = data.mismatched_docs_sample || [];
        if (count === 1 && samples.length > 0) {
          const doc = samples[0];
          const safeTitle = esc(doc.title || doc.id);
          return `.env 설정은 <b>${cfg}</b>이나, 대상 문서(<b><a href="javascript:void(0)" onclick="selectDoc('${esc(doc.id)}');setCenterView('reader')" style="color:var(--accent);text-decoration:underline;">${safeTitle}</a></b>)가 <b>${other}</b> 포맷입니다.`;
        }
        return '.env 설정은 <b>' + cfg + '</b>이나, DB 문서 중 <b>' + count + '개</b>가 <b>' + other + '</b> 포맷입니다. <code>./cb-manuscript app format-migrate --apply</code> 실행이 필요합니다.';
      },
      actionLabel: function(data){
        const count = (data && (data.mismatched || data.mismatched_docs)) || 0;
        return count === 1 ? '대상 문서 열기' : '불일치 문서 필터';
      },
      action: function(data){
        const count = (data && (data.mismatched || data.mismatched_docs)) || 0;
        const samples = (data && data.mismatched_docs_sample) || [];
        if (count === 1 && samples.length > 0) {
          selectDoc(samples[0].id);
          setCenterView('reader');
        } else {
          filterDocsByFormatMismatch();
        }
      }
    },
    format_missing: {
      level: 'info',
      icon: 'ℹ️',
      title: '가독 렌더링 미생성',
      render: function(data){
        const count = data.missing_detail_docs || 0;
        const samples = data.missing_docs_sample || [];
        if (count === 1 && samples.length > 0) {
          const doc = samples[0];
          const safeTitle = esc(doc.title || doc.id);
          return `전체 문서 중 <b>1개</b>의 상세(detail)가 아직 생성되지 않았습니다: <a href="javascript:void(0)" onclick="selectDoc('${esc(doc.id)}');setCenterView('reader')" style="color:var(--accent);font-weight:600;text-decoration:underline;">${safeTitle}</a>`;
        }
        return '전체 문서 중 <b>' + count + '개</b>의 상세(detail)가 아직 생성되지 않았습니다. <code>./cb-manuscript app format-migrate --apply</code> 실행으로 자동 생성할 수 있습니다.';
      },
      actionLabel: function(data){
        const count = (data && data.missing_detail_docs) || 0;
        return count === 1 ? '대상 문서 열기' : '미생성 문서 필터';
      },
      action: function(data){
        const count = (data && data.missing_detail_docs) || 0;
        const samples = (data && data.missing_docs_sample) || [];
        if (count === 1 && samples.length > 0) {
          selectDoc(samples[0].id);
          setCenterView('reader');
        } else {
          filterDocsByMissingDetail();
        }
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
        const rawLabel = typeof opts.actionLabel === 'function' ? opts.actionLabel(pData) : opts.actionLabel;
        if (rawLabel && typeof opts.action === 'function') {
          actBtn.textContent = rawLabel;
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
fetchThemes();    // 지식 관리자가 정의한 테마 목록을 조회하여 셀렉터 구성
loadThemeDocuments();
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
    if(state.modal !== 'docdrawer' && typeof docDrawerOpen !== 'undefined' && docDrawerOpen){
      closeDocDrawer(false);
    }
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
    if(state.modal === 'docdrawer'){
      if(typeof docDrawerOpen !== 'undefined' && !docDrawerOpen){
        openDocDrawer(false);
      }
    } else if(state.modal === 'reader' && state.docId){
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
  get canIngest(){ return canIngest(); },
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
  get graphRenderer(){ return net ? net.rendererInfo : null; },
  get viewpos(){ return net ? net.getViewPosition() : null; },
  get clustered(){ return clusterEdges; },
  get activePane(){ return activePane; },
  get detailOpen(){ return detailOpen || drawerOpen; },
  get drawerOpen(){ return drawerOpen; },
  get toolsOpen(){ return drawerOpen || detailOpen; },
  get readerOpen(){ return document.getElementById('reader').classList.contains('open'); },
  get docSearchActive(){ return docSearchActive; },
  get stabilized(){ return graphStabilized; },
  get graphSuspended(){ return graphSuspended; },
  get graphInitialized(){ return graphInitialized; },
  get trackingIngest(){ return trackingIngest; },
  get isFastPolling(){ return isFastPolling; },
  pollForUpdates: pollForUpdates,
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
  get activeThemeId(){ return activeThemeId; },
  get availableThemes(){ return availableThemes; },
  get themeMode(){ return (window.__CLAIRE_CONFIG && window.__CLAIRE_CONFIG.themeMode) || '__THEME_MODE__'; },
  switchKnowledgeTheme: switchKnowledgeTheme,
  get activeJobs(){ return activeJobs; },
  get selectedJobId(){ return selectedJobId; },
  get curMinDeg(){ return curMinDeg; },
  enterHome: enterHome,
  resetWorkspaceState: resetWorkspaceState,
  resolveHomeView: resolveHomeView,
  selectJobSlot: selectJobSlot,
  renderMultiSlotUI: renderMultiSlotUI,
};

// ============================================================================
// --- Heatmap Matrix & Decision Stream UI/UX Integration (Phase 1) ---
// ============================================================================

let currentMatrixData = null;

const STAGES = ['fetch', 'extract', 'resolve', 'detail', 'done'];
const STAGE_LABELS = {
  init: '준비',
  fetch: '수집',
  extract: '추출',
  resolve: '대조',
  detail: '상세',
  done: '완료',
  error: '오류'
};
const STAGE_FULL_TITLES = {
  init: '1. 수집 (Fetch)',
  fetch: '1. 수집 (Fetch)',
  extract: '2. 추출 (Extract)',
  resolve: '3. 대조 (Resolve)',
  detail: '4. 상세 (Detail)',
  done: '5. 완료 (Done)',
  error: '오류 발생 (Error)'
};
const STAGE_DESCS = {
  fetch: '원문 수집 및 정제',
  extract: '엔티티/관계 구조화',
  resolve: '전수 히트맵 텐서',
  detail: 'ADOC 본문 컴파일',
  done: '지식 그래프 반영'
};
const STAGE_PROGRESS = {
  init: 10,
  fetch: 20,
  extract: 45,
  resolve: 75,
  detail: 92,
  done: 100,
  error: 100
};
const KIND_LABELS = {
  ingest: '적재',
  reingest: '재적재',
  regenerate: '재생성'
};

function extractActiveJobs(d){
  if(!d) return [];
  if(Array.isArray(d.active_jobs) && d.active_jobs.length > 0){
    return d.active_jobs.filter(j => j && (j.active || (j.completed_at && (Date.now()/1000 - j.completed_at < 30))));
  }
  if(d.active_ingest && (d.active_ingest.active || (d.active_ingest.completed_at && (Date.now()/1000 - d.active_ingest.completed_at < 30)))){
    const copy = Object.assign({}, d.active_ingest);
    if(!copy.job_id) copy.job_id = 'default_active_ingest';
    return [copy];
  }
  if(d.ingesting){
    return [{
      job_id: 'default_ingest',
      active: true,
      kind: 'ingest',
      theme_id: (typeof activeThemeId !== 'undefined' ? activeThemeId : 0),
      title: '적재 진행 중…',
      stage: 'extract',
      msg: '원문 분석 및 엔티티 대조 준비 중…'
    }];
  }
  return [];
}

function getJobPct(job){
  if(!job) return 0;
  if(typeof job.progress === 'number') return Math.max(0, Math.min(100, Math.round(job.progress)));
  return STAGE_PROGRESS[job.stage] || (job.active ? 30 : 100);
}

function formatJobElapsed(job){
  if(!job) return '00:00 경과';
  if(job.started_at){
    const elapsedSec = Math.max(0, Math.floor((job.updated_at || (Date.now() / 1000)) - job.started_at));
    const mm = String(Math.floor(elapsedSec / 60)).padStart(2, '0');
    const ss = String(elapsedSec % 60).padStart(2, '0');
    return mm + ':' + ss + ' 경과';
  }
  return job.active === false ? '완료' : '실시간 추적 중';
}

function buildDesktopStepperHtml(job){
  const stages = (job && Array.isArray(job.stages) && job.stages.length > 0) ? job.stages : STAGES;
  const curStage = (job && job.stage) ? job.stage : 'fetch';
  const curIdx = stages.indexOf(curStage === 'init' ? 'fetch' : curStage);
  const isDone = curStage === 'done' || (job && job.active === false && curStage !== 'error');
  const isError = curStage === 'error';

  return stages.map((stg, i) => {
    let cls = 'pending';
    let icon = '○';
    if(isDone || (curIdx !== -1 && i < curIdx)){
      cls = 'completed';
      icon = '✓';
    } else if(i === curIdx){
      cls = isError ? 'error' : 'current';
      icon = isError ? '❌' : '◐';
    }
    const title = STAGE_FULL_TITLES[stg] || ((i + 1) + '. ' + (STAGE_LABELS[stg] || stg));
    const desc = STAGE_DESCS[stg] || '';
    return '<div class="cockpit-step-card stepper-step ' + cls + '" data-stage="' + esc(stg) + '">'
      + '<div class="cockpit-step-top">'
      + '  <span class="step-label">' + esc(title) + '</span>'
      + '  <span class="step-dot" aria-hidden="true">' + icon + '</span>'
      + '</div>'
      + '<div class="cockpit-step-desc">' + esc(desc) + '</div>'
      + '</div>';
  }).join('');
}

function buildStepperHtml(job){
  return buildDesktopStepperHtml(job);
}

function buildLiveStreamPlaceholderHtml(job){
  const curStage = (job && job.stage) ? job.stage : 'fetch';
  const stageTitle = STAGE_FULL_TITLES[curStage] || STAGE_LABELS[curStage] || '진행 중';
  const msg = (job && job.msg) ? job.msg : '원문 분석 및 엔티티 대조 준비 중…';
  const pct = getJobPct(job);
  const curIdx = Math.max(0, STAGES.indexOf(curStage === 'init' ? 'fetch' : curStage));

  const logLines = [
    { idx: 0, text: '[1/5 Fetch] 원문 수집 및 텍스트 정규화 파이프라인 가동' },
    { idx: 1, text: '[2/5 Extract] 핵심 엔티티(Entity) 및 도메인 관계(Relation) 구조화 추출' },
    { idx: 2, text: '[3/5 Resolve] 기존 지식베이스 후보군 전수 유사도 텐서(N×M) 산출' },
    { idx: 3, text: '[4/5 Detail] AsciiDoc(ADOC) 상세 본문 컴파일 및 출처 검증' }
  ].map(item => {
    if(item.idx < curIdx){
      return '<div class="cockpit-live-log-line done">✓ ' + esc(item.text) + ' (완료)</div>';
    }
    if(item.idx === curIdx){
      return '<div class="cockpit-live-log-line active">▶ ' + esc(msg) + '</div>';
    }
    return '<div class="cockpit-live-log-line">· ' + esc(item.text) + ' (대기)</div>';
  }).join('');

  return '<div class="matrix-placeholder-wave" id="cockpit-live-placeholder">'
    + '<div class="cockpit-live-stream-card">'
    + '  <div class="cockpit-live-stream-head">'
    + '    <div class="cockpit-live-stream-title">'
    + '      <div class="wave-pulse-ring"></div>'
    + '      <p class="wave-msg">' + esc(msg) + '</p>'
    + '    </div>'
    + '    <span class="cockpit-live-stream-stage-tag">' + esc(stageTitle) + ' · ' + pct + '%</span>'
    + '  </div>'
    + '  <div class="cockpit-live-log-box">' + logLines + '</div>'
    + '  <div class="cockpit-live-sub">'
    + '    <small class="wave-sub">3단계 대조(Resolve) 진입 시 이 영역이 실시간 N×M 히트맵 텐서로 즉시 전환됩니다.</small>'
    + '  </div>'
    + '</div>'
    + '</div>';
}

function updateCockpitChrome(job){
  if(!job) return;
  const pct = getJobPct(job);
  const curStage = job.stage || 'fetch';
  const isDone = curStage === 'done' || (job.active === false && curStage !== 'error');
  const isError = curStage === 'error';
  const kindTxt = KIND_LABELS[job.kind] || '적재';
  const themeTxt = job.theme_label || ('테마 #' + (job.theme_id !== undefined ? job.theme_id : 0));

  const statusBadge = document.getElementById('cockpit-status-badge');
  const statusText = document.getElementById('cockpit-status-text');
  if(statusBadge && statusText){
    statusBadge.className = 'cockpit-status-badge' + (isError ? ' error' : (isDone ? ' done' : ''));
    statusText.textContent = isError ? '적재 오류' : (isDone ? '적재 완료' : '적재 진행 중');
  }

  const themeBadge = document.getElementById('cockpit-theme-badge');
  if(themeBadge) themeBadge.textContent = '📁 ' + themeTxt;

  const kindBadge = document.getElementById('cockpit-kind-badge');
  if(kindBadge) kindBadge.textContent = kindTxt + ' 파이프라인';

  const bigPctEl = document.getElementById('cockpit-big-pct');
  if(bigPctEl){
    bigPctEl.textContent = pct + '%';
    bigPctEl.classList.toggle('done', isDone);
  }

  const elapsedEl = document.getElementById('cockpit-elapsed-time');
  if(elapsedEl) elapsedEl.textContent = formatJobElapsed(job);

  const stepperEl = document.getElementById('matrix-stepper');
  if(stepperEl) stepperEl.style.display = 'block';

  const desktopStepper = document.getElementById('cockpit-desktop-stepper');
  if(desktopStepper){
    desktopStepper.innerHTML = buildDesktopStepperHtml(job);
  } else if(stepperEl){
    stepperEl.innerHTML = buildDesktopStepperHtml(job);
  }

  const stages = (Array.isArray(job.stages) && job.stages.length > 0) ? job.stages : STAGES;
  const curIdx = Math.max(0, stages.indexOf(curStage === 'init' ? 'fetch' : curStage));
  const mobileStageEl = document.getElementById('cockpit-mobile-stage');
  if(mobileStageEl){
    const fullTitle = STAGE_FULL_TITLES[curStage] || (STAGE_LABELS[curStage] || curStage);
    mobileStageEl.textContent = isDone
      ? '단계 [5/5]: 5. 완료 (Done)'
      : ('단계 [' + Math.min(stages.length, curIdx + 1) + '/' + stages.length + ']: ' + fullTitle);
  }
  const mobilePctEl = document.getElementById('cockpit-mobile-pct');
  if(mobilePctEl) mobilePctEl.textContent = pct + '%';
  const mobileFillEl = document.getElementById('cockpit-mobile-fill');
  if(mobileFillEl) mobileFillEl.style.width = pct + '%';

  const engineEl = document.getElementById('cockpit-telemetry-engine');
  if(engineEl) engineEl.textContent = kindTxt + ' 워커:';

  const metricsEl = document.getElementById('cockpit-telemetry-metrics');
  if(metricsEl){
    const stageShort = STAGE_LABELS[curStage] || curStage;
    const mat = job.heatmap_matrix;
    const secondPill = (mat && Array.isArray(mat.rows))
      ? ('대조 행렬: <strong>' + mat.rows.length + '×' + (mat.cols ? mat.cols.length : 0) + '</strong>')
      : ('진행률: <strong>' + pct + '%</strong>');
    metricsEl.innerHTML = '<span class="cockpit-metric-pill">단계: <strong>' + esc(stageShort) + '</strong></span>'
      + '<span class="cockpit-metric-pill">' + secondPill + '</span>';
  }
}

function selectJobSlot(jobId){
  selectedJobId = jobId;
  renderMultiSlotUI(activeJobs);
}

function updateCenterJobView(job){
  if(!job) return;
  const titleEl = document.getElementById('matrix-title');
  if(titleEl){
    titleEl.innerHTML = '지식 적재 콕핏 <small id="matrix-subtitle">| Ingest Cockpit (' + esc(job.title || '작업 진행 중') + ')</small>';
  }
  const docMetaEl = document.getElementById('matrix-target-doc');
  if(docMetaEl){
    const kindTxt = KIND_LABELS[job.kind] || '적재';
    const themeTxt = job.theme_label || ('#' + (job.theme_id !== undefined ? job.theme_id : 0));
    docMetaEl.textContent = '대조 대상: ' + (job.title || '(제목 없음)') + ' · 테마: ' + themeTxt + ' (' + kindTxt + ')';
  }

  const spinner = document.getElementById('matrix-progress-spinner');
  if(spinner){
    spinner.style.display = job.active ? 'inline-block' : 'none';
  }

  const msgEl = document.getElementById('matrix-progress-msg');
  if(msgEl){
    msgEl.textContent = job.msg || (job.active ? '진행 중…' : '완료');
  }

  updateCockpitChrome(job);

  if(job.heatmap_matrix){
    renderHeatmapMatrix(job.heatmap_matrix, job.title, job.active);
  } else {
    const theadRow = document.getElementById('matrix-thead-row');
    if(theadRow){
      theadRow.innerHTML = '<th style="min-width:140px">추출 노드 \\ 기존 지식 노드</th>';
    }
    const tbody = document.getElementById('matrix-tbody');
    if(tbody){
      tbody.innerHTML = '<tr><td colspan="10" style="padding:0;border:0;">'
        + buildLiveStreamPlaceholderHtml(job)
        + '</td></tr>';
    }
  }
}

function renderMultiSlotUI(jobs){
  if(!jobs || jobs.length === 0) return;

  const existingJob = jobs.find(j => j.job_id === selectedJobId);
  if(!existingJob){
    const themeMatch = jobs.find(j => Number(j.theme_id !== undefined ? j.theme_id : 0) === Number(activeThemeId));
    selectedJobId = (themeMatch || jobs[0]).job_id;
  }

  const selectedJob = jobs.find(j => j.job_id === selectedJobId) || jobs[0];
  updateCenterJobView(selectedJob);
}

function processActiveJobsStats(d){
  if(!d) return;
  activeJobs = extractActiveJobs(d);
  if(activeJobs.length > 0){
    renderMultiSlotUI(activeJobs);
  }
}

function initIngestMatrixView(payload, focus, themeLabel){
  const wrap = document.getElementById('matrixwrap');
  if(!wrap) return;
  const titleEl = document.getElementById('matrix-title');
  if(titleEl) titleEl.innerHTML = '지식 적재 콕핏 <small id="matrix-subtitle">| Ingest Cockpit (실시간 파이프라인 콕핏)</small>';
  const cleanPayload = (payload || '').replace(/\s+/g, ' ').trim();
  const shortDesc = cleanPayload.length > 80 ? cleanPayload.slice(0, 80) + '…' : cleanPayload;
  const docMetaEl = document.getElementById('matrix-target-doc');
  if(docMetaEl){
    docMetaEl.textContent = '대조 대상: ' + shortDesc + (themeLabel ? ' (테마: ' + themeLabel + ')' : '');
  }
  const spin = document.getElementById('matrix-progress-spinner');
  if(spin) spin.style.display = 'inline-block';
  const defaultMsg = '원문 분석 및 엔티티 대조 준비 중…';
  const msgEl = document.getElementById('matrix-progress-msg');
  if(msgEl) msgEl.textContent = defaultMsg;

  const synthJob = {
    active: true,
    kind: 'ingest',
    theme_id: (typeof activeThemeId !== 'undefined' ? activeThemeId : 0),
    theme_label: themeLabel || '기본 지식베이스',
    title: shortDesc || '수집 대상',
    stage: 'fetch',
    progress: 20,
    msg: defaultMsg,
    heatmap_matrix: null
  };
  updateCockpitChrome(synthJob);

  const theadRow = document.getElementById('matrix-thead-row');
  if(theadRow){
    theadRow.innerHTML = '<th style="min-width:140px">추출 노드 \\ 기존 지식 노드</th>';
  }
  const tbody = document.getElementById('matrix-tbody');
  if(tbody){
    tbody.innerHTML = '<tr><td colspan="4" style="padding:0;border:0;">' + buildLiveStreamPlaceholderHtml(synthJob) + '</td></tr>';
  }
}

function updateMatrixProgress(msg){
  const msgEl = document.getElementById('matrix-progress-msg');
  if(msgEl) msgEl.textContent = msg;
  const text = String(msg || '');
  let inferredStage = 'extract';
  let inferredPct = 45;
  if(text.includes('수집') || text.includes('준비')){
    inferredStage = 'fetch';
    inferredPct = 20;
  } else if(text.includes('대조') || text.includes('텐서') || text.includes('유사도')){
    inferredStage = 'resolve';
    inferredPct = 75;
  } else if(text.includes('상세') || text.includes('ADOC') || text.includes('본문')){
    inferredStage = 'detail';
    inferredPct = 92;
  } else if(text.includes('완료')){
    inferredStage = 'done';
    inferredPct = 100;
  }
  const synthJob = {
    active: inferredStage !== 'done',
    kind: 'ingest',
    theme_id: (typeof activeThemeId !== 'undefined' ? activeThemeId : 0),
    theme_label: '기본 지식베이스',
    stage: inferredStage,
    progress: inferredPct,
    msg: text,
    heatmap_matrix: currentMatrixData
  };
  updateCockpitChrome(synthJob);
  const placeholderEl = document.getElementById('cockpit-live-placeholder');
  if(placeholderEl && !currentMatrixData){
    const tbody = document.getElementById('matrix-tbody');
    if(tbody){
      tbody.innerHTML = '<tr><td colspan="4" style="padding:0;border:0;">' + buildLiveStreamPlaceholderHtml(synthJob) + '</td></tr>';
    }
  }
}

function addDecisionStreamCard(ev){
  if(!ev) return;
  if(!Array.isArray(cachedDecisions)) cachedDecisions = [];
  cachedDecisions.unshift({
    stage: ev.stage || 'gating',
    entity: ev.entity || '',
    candidate: ev.candidate || null,
    score: ev.score,
    decision: ev.decision || 'MERGE',
    reason: ev.reason || '',
    document_id: ev.document_id || (activeIngest ? 'live' : null),
    document_title: ev.document_title || '실시간 적재',
    timestamp: Math.floor(Date.now() / 1000)
  });
  if(typeof centerView !== 'undefined' && centerView === 'stream'){
    renderDecisionStreamCenter(false);
  }
  if(document.querySelector('.decision-filter-bar') && document.getElementById('drawerscroll')){
    renderDecisionStreamPanel();
  }
}

function getMatrixCellVisual(score, autoMergeThresh, borderlineThresh){
  autoMergeThresh = (autoMergeThresh !== undefined && autoMergeThresh !== null) ? Number(autoMergeThresh) : 0.93;
  borderlineThresh = (borderlineThresh !== undefined && borderlineThresh !== null) ? Number(borderlineThresh) : 0.72;
  const numScore = Number(score) || 0.0;

  if(numScore >= autoMergeThresh){
    return {
      tier: 'merge',
      bg: '#059669',
      border: '2px solid #047857',
      fg: '#ffffff',
      label: '자동 병합'
    };
  }
  if(numScore >= borderlineThresh){
    return {
      tier: 'borderline',
      bg: '#0284c7',
      border: '1.5px dashed #0369a1',
      fg: '#ffffff',
      label: '경계선 탐색'
    };
  }
  if(numScore >= 0.55){
    return {
      tier: 'high',
      bg: '#3b82f6',
      border: '1px solid #2563eb',
      fg: '#ffffff',
      label: '높은 유사도'
    };
  }
  if(numScore >= 0.40){
    return {
      tier: 'mid-high',
      bg: '#60a5fa',
      border: '1px solid #3b82f6',
      fg: '#ffffff',
      label: '중상 유사도'
    };
  }
  if(numScore >= 0.25){
    return {
      tier: 'mid',
      bg: '#93c5fd',
      border: '1px solid #60a5fa',
      fg: '#1e3a8a',
      label: '중간 유사도'
    };
  }
  if(numScore >= 0.15){
    return {
      tier: 'mid-low',
      bg: '#bfdbfe',
      border: '1px solid #93c5fd',
      fg: '#1e3a8a',
      label: '중저 유사도'
    };
  }
  if(numScore >= 0.07){
    return {
      tier: 'low',
      bg: '#dbeafe',
      border: '1px solid #bfdbfe',
      fg: '#1e3a8a',
      label: '낮은 유사도'
    };
  }
  return {
    tier: 'base',
    bg: '#eff6ff',
    border: '1px solid #e0e7ff',
    fg: '#64748b',
    label: '기저 밀도'
  };
}

function renderHeatmapMatrix(matrixData, title, isIngesting){
  if(!matrixData) return;
  currentMatrixData = matrixData;
  window.activeHeatmapMatrix = matrixData;

  if(title){
    const docMetaEl = document.getElementById('matrix-target-doc');
    if(docMetaEl){
      docMetaEl.innerHTML = '대조 대상: <a href="#" class="matrix-doc-link" onclick="openMatrixTargetDoc();return false;" title="문서 본문 보기">' + esc(title) + '</a>';
    }
  }

  const rows = matrixData.rows || [];
  const cols = matrixData.cols || [];
  const mat = matrixData.matrix || [];
  const autoMergeThresh = matrixData.threshold_auto_merge || 0.93;
  const borderlineThresh = matrixData.threshold_borderline || 0.72;

  const theadRow = document.getElementById('matrix-thead-row');
  if(theadRow){
    theadRow.innerHTML = '<th class="matrix-corner-header" title="추출 노드 \\ 기존 지식 노드 (' + cols.length + ')"><div class="matrix-corner-label">추출 \\ 지식 (' + cols.length + ')</div></th>' +
      cols.map(c => '<th class="matrix-col-header" title="' + esc(c) + '"><div class="matrix-col-label">' + esc(c.length > 8 ? c.slice(0, 7) + '…' : c) + '</div></th>').join('');
  }

  const tbody = document.getElementById('matrix-tbody');
  if(tbody){
    if(!rows.length || !cols.length){
      tbody.innerHTML = '<tr><td colspan="' + Math.max(1, cols.length + 1) + '" style="text-align:center;padding:32px 16px;color:var(--muted)">비교 대상 후보 엔티티가 없거나 지식베이스 초기 상태입니다.</td></tr>';
      return;
    }

    // 애니메이션은 오직 적재 중 실시간으로 확률 색상을 부여할 때만 발동
    const isActivelyIngesting = (typeof isIngesting === 'boolean') ? isIngesting : !!(window.activeIngest && window.activeIngest.running);

    const spin = document.getElementById('matrix-progress-spinner');
    const msgEl = document.getElementById('matrix-progress-msg');
    if(!isActivelyIngesting){
      if(spin) spin.style.display = 'none';
      if(msgEl && (!msgEl.textContent || msgEl.textContent.includes('준비 중') || msgEl.textContent.includes('진행 중'))){
        msgEl.innerHTML = '✅ <strong>대조 완료</strong> (온톨로지 대조 밀도 계산 완료)';
      }
    } else {
      if(spin) spin.style.display = 'inline-block';
      if(msgEl && (!msgEl.textContent || msgEl.textContent === '적재 준비 중…')){
        msgEl.textContent = '실시간 엔티티 대조 진행 중…';
      }
    }

    if(!activeJobs || activeJobs.length === 0){
      updateCockpitChrome({
        active: isActivelyIngesting,
        kind: 'ingest',
        theme_id: (typeof activeThemeId !== 'undefined' ? activeThemeId : 0),
        theme_label: '기본 지식베이스',
        title: title || '',
        stage: isActivelyIngesting ? 'resolve' : 'done',
        progress: isActivelyIngesting ? 75 : 100,
        heatmap_matrix: matrixData
      });
    }

    tbody.innerHTML = rows.map((rName, rIdx) => {
      const rowScores = mat[rIdx] || [];
      const cells = cols.map((cName, cIdx) => {
        const score = rowScores[cIdx] !== undefined ? Number(rowScores[cIdx]) : 0.0;
        const vis = getMatrixCellVisual(score, autoMergeThresh, borderlineThresh);

        let cellClass = 'matrix-cell';
        let animStyle = '';
        if(isActivelyIngesting){
          cellClass += ' cell-ingest-anim';
          const delay = Math.min(600, (rIdx * cols.length + cIdx) * 8);
          animStyle = 'animation-delay:' + delay + 'ms;';
        }

        const tooltip = esc(rName) + ' ↔ ' + esc(cName) + '\n유사도: ' + score.toFixed(4) + ' (' + vis.label + ')';

        return '<td><div class="' + cellClass + '" data-tier="' + vis.tier + '" title="' + tooltip + '" style="background:' + vis.bg + ';color:' + vis.fg + ';border:' + vis.border + ';' + animStyle + '" onmouseenter="inspectMatrixCell(\'' + esc(rName) + '\',\'' + esc(cName) + '\',' + score + ',\'' + esc(vis.label) + '\')" onclick="inspectMatrixCell(\'' + esc(rName) + '\',\'' + esc(cName) + '\',' + score + ',\'' + esc(vis.label) + '\')"></div></td>';
      }).join('');

      const shortRName = rName.length > 10 ? rName.slice(0, 9) + '…' : rName;
      return '<tr><td class="matrix-row-header" title="' + esc(rName) + '"><div class="matrix-row-label">' + esc(shortRName) + '</div></td>' + cells + '</tr>';
    }).join('');
  }
}

function completeIngestMatrixView(result){
  const spin = document.getElementById('matrix-progress-spinner');
  if(spin) spin.style.display = 'none';
  const msgEl = document.getElementById('matrix-progress-msg');
  if(msgEl){
    msgEl.innerHTML = '✅ <strong>대조 및 적재 완료</strong> (노드 신규 ' + ((result && result.entities_created)||0) + ' · 기존 연결 ' + ((result && result.entities_linked)||0) + ' · 관계 ' + ((result && result.relations_added)||0) + ')';
  }
  updateCockpitChrome({
    active: false,
    kind: 'ingest',
    theme_id: (typeof activeThemeId !== 'undefined' ? activeThemeId : 0),
    theme_label: '기본 지식베이스',
    title: (result && result.title) || '',
    stage: 'done',
    progress: 100,
    heatmap_matrix: (result && result.heatmap_matrix) || currentMatrixData
  });
  if(result && result.title){
    const docMetaEl = document.getElementById('matrix-target-doc');
    if(docMetaEl){
      docMetaEl.innerHTML = '대조 대상: <a href="#" class="matrix-doc-link" onclick="openMatrixTargetDoc();return false;" title="문서 본문 보기">' + esc(result.title) + '</a>';
    }
  }
  if(result && result.heatmap_matrix){
    renderHeatmapMatrix(result.heatmap_matrix, result.title, false);
  }
}

function triggerMatrixWave(){
  // 인위적인 파동 효과는 사용자 요구사항에 따라 배제됨
}

function openMatrixTargetDoc(){
  const targetId = activeDoc || curReaderDoc || window.latestHeatmapDocId || (currentMatrixData && currentMatrixData.document_id);
  if(targetId){
    if(typeof selectDoc === 'function'){
      selectDoc(targetId);
    } else if(typeof openReader === 'function'){
      openReader(targetId);
    }
  } else {
    setCenterView('reader');
  }
}

async function confirmMatrixView(){
  const targetId = activeDoc || curReaderDoc || window.latestHeatmapDocId;
  if(targetId){
    await confirmPurgeMatrix(targetId);
  }
  closeMatrixView(targetId);
}

function closeMatrixView(targetDocId){
  resumeGraphAfterIngest();
  const targetDoc = targetDocId || activeDoc || curReaderDoc || window.latestHeatmapDocId;
  if(targetDoc){
    if(typeof selectDoc === 'function'){
      selectDoc(targetDoc);
    } else if(typeof openReader === 'function'){
      openReader(targetDoc);
    }
  } else {
    setCenterView('graph');
  }
}

function inspectMatrixCell(rName, cName, score, tierLabel){
  const label = tierLabel || (typeof getMatrixCellVisual === 'function' ? getMatrixCellVisual(score).label : '');
  const text = esc(rName) + ' ↔ ' + esc(cName) + ' · 유사도: ' + Number(score).toFixed(4) + (label ? ' (' + label + ')' : '');
  const inspectEl = document.getElementById('matrix-inspect-text');
  if(inspectEl) inspectEl.innerHTML = '<strong>' + text + '</strong>';
  if(typeof setGraphNotice === 'function') setGraphNotice(rName + ' ↔ ' + cName + ': ' + Number(score).toFixed(3));
}

// ============================================================================
// --- Global Decision Stream (지식 엔티티 상태 전이 및 해소 감사 로그) ---
// ============================================================================

let currentDecisionFilter = 'ALL';
let cachedDecisions = [];
let displayedStreamLimit = 30;
let isFetchingDecisions = false;
let streamSearchQuery = '';

function getFilteredDecisions(){
  if(!Array.isArray(cachedDecisions)) return [];
  let list = cachedDecisions;
  if(currentDecisionFilter === 'MERGE') list = list.filter(d => d.decision === 'MERGE');
  else if(currentDecisionFilter === 'CREATE_NEW') list = list.filter(d => d.decision === 'CREATE_NEW');
  else if(currentDecisionFilter === 'ROLLED_BACK') list = list.filter(d => d.decision === 'ROLLED_BACK' || d.rolled_back);

  if(streamSearchQuery){
    const sq = streamSearchQuery;
    list = list.filter(d => {
      const entityMatch = (d.entity_name || d.name || '').toLowerCase().includes(sq);
      const reasonMatch = (d.reason || '').toLowerCase().includes(sq);
      const typeMatch = (d.entity_type || d.type || '').toLowerCase().includes(sq);
      const docMatch = (d.document_id || '').toLowerCase().includes(sq);
      return entityMatch || reasonMatch || typeMatch || docMatch;
    });
  }
  return list;
}

function filterDecisionStreamByQuery(q){
  streamSearchQuery = (q || '').trim().toLowerCase();
  displayedStreamLimit = 30;
  const container = document.getElementById('stream-scroll-container');
  if(container) container.scrollTop = 0;
  if(typeof renderDecisionStreamCenter === 'function'){
    renderDecisionStreamCenter(false);
  }
}
window.filterDecisionStreamByQuery = filterDecisionStreamByQuery;

async function openDecisionStream(){
  setCenterView('stream');
}

function filterDecisionStream(filter){
  currentDecisionFilter = filter;
  displayedStreamLimit = 30;
  if(typeof centerView !== 'undefined' && centerView === 'stream'){
    renderDecisionStreamCenter(false);
  } else {
    renderDecisionStreamPanel();
  }
}

function filterDecisionStreamCenter(filter){
  currentDecisionFilter = filter;
  displayedStreamLimit = 30;
  const container = document.getElementById('stream-scroll-container');
  if(container) container.scrollTop = 0;
  renderDecisionStreamCenter(false);
}

function handleStreamScroll(el){
  if(!el) return;
  if(el.scrollHeight - el.scrollTop - el.clientHeight < 50){
    loadMoreStreamItems();
  }
}

function loadMoreStreamItems(){
  const filtered = getFilteredDecisions();
  if(displayedStreamLimit < filtered.length){
    displayedStreamLimit += 10;
    renderDecisionStreamCenter(true);
  }
}

function closeStreamView(){
  if(activeDoc){
    setCenterView('reader');
  } else {
    setCenterView('graph');
  }
}

function formatDecisionTimestamp(ts){
  if(!ts) return '';
  let d;
  const num = Number(ts);
  if(!isNaN(num) && num > 0){
    const ms = num > 1e11 ? num : num * 1000;
    d = new Date(ms);
  } else {
    d = new Date(ts);
  }
  if(!d || isNaN(d.getTime())) return '';
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  const h = String(d.getHours()).padStart(2, '0');
  const min = String(d.getMinutes()).padStart(2, '0');
  const s = String(d.getSeconds()).padStart(2, '0');
  return `${y}-${m}-${day} ${h}:${min}:${s}`;
}
window.formatDecisionTimestamp = formatDecisionTimestamp;

function getBrowserTimezoneInfo(){
  try {
    const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
    const offsetMin = -new Date().getTimezoneOffset();
    const sign = offsetMin >= 0 ? '+' : '-';
    const offH = String(Math.floor(Math.abs(offsetMin) / 60)).padStart(2, '0');
    const offM = String(Math.abs(offsetMin) % 60).padStart(2, '0');
    return `브라우저 타임존: ${tz || 'Local'} (UTC${sign}${offH}:${offM})`;
  } catch(_) {
    return '브라우저 로컬 타임존';
  }
}
window.getBrowserTimezoneInfo = getBrowserTimezoneInfo;

function renderDecisionCardHtml(dec){
  const isMerge = dec.decision === 'MERGE';
  const isRolledBack = dec.decision === 'ROLLED_BACK' || dec.rolled_back;

  let badgeClass = 'badge-new';
  let badgeLabel = '신규 (CREATE)';
  if(isMerge){
    badgeClass = 'badge-merge';
    badgeLabel = '병합 (MERGE)';
  } else if(isRolledBack){
    badgeClass = 'badge-rollback';
    badgeLabel = '롤백됨 (REVERT)';
  } else if(dec.decision === 'CROSS_LINK'){
    badgeClass = 'badge-cross';
    badgeLabel = '관계 연결';
  }

  const scoreText = (dec.score !== undefined && dec.score !== null) ? ' · 유사도 ' + Number(dec.score).toFixed(3) : '';
  const candText = dec.candidate ? ' ↔ 대상 [' + esc(dec.candidate) + ']' : '';

  let html = '<div class="decision-card">';
  html += '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">';
  html += '<div style="display:flex;align-items:center;gap:8px">';
  html += '<strong style="font-size:14px;color:var(--fg)">🏷️ ' + esc(dec.entity || '') + '</strong>';
  html += '<span class="decision-badge ' + badgeClass + '">' + esc(badgeLabel) + '</span>';
  html += '</div>';
  if(dec.timestamp){
    const dStr = formatDecisionTimestamp(dec.timestamp);
    if(dStr){
      const tzTitle = getBrowserTimezoneInfo();
      html += '<span style="font-size:11px;color:var(--muted);font-family:var(--font-mono, monospace)" title="' + esc(tzTitle) + '">' + esc(dStr) + '</span>';
    }
  }
  html += '</div>';

  html += '<div class="decision-card-body">';
  html += '<div style="display:flex;flex-wrap:wrap;align-items:center;gap:6px;margin-bottom:6px">';
  html += '<span class="decision-stage-tag">' + esc(dec.stage || 'gating') + '</span>';
  if(candText || scoreText){
    html += '<span style="color:var(--fg);font-weight:500">' + esc(candText + scoreText) + '</span>';
  }
  html += '</div>';

  if(dec.reason){
    html += '<div style="padding:8px 10px;background:var(--bg);border-left:3px solid var(--accent,#0284c7);border-radius:4px;font-size:12px;color:var(--fg);line-height:1.45;margin-bottom:8px">';
    html += '<span style="margin-right:4px">💡</span>' + esc(dec.reason);
    html += '</div>';
  }

  html += '<div style="display:flex;justify-content:space-between;align-items:center;padding-top:6px;border-top:1px dashed var(--border);font-size:11.5px;color:var(--muted)">';
  if(dec.document_id){
    html += '<span>📄 출처: <a href="javascript:void(0)" onclick="selectDoc(\'' + esc(dec.document_id) + '\');setCenterView(\'reader\')" style="color:var(--accent,#0284c7);text-decoration:underline">' + esc(dec.document_title || dec.document_id) + '</a></span>';
  } else {
    html += '<span></span>';
  }
  if(isMerge && !isRolledBack){
    html += '<button type="button" class="sec" disabled title="원자적 롤백은 미시험 기능으로 현재 안전을 위해 비활성화되어 있습니다" style="font-size:11px;padding:3px 8px;color:var(--muted);border-color:var(--border);opacity:0.5;cursor:not-allowed;">↩ 병합 롤백 (비활성화)</button>';
  }
  html += '</div>';
  html += '</div>';
  html += '</div>';
  return html;
}

async function renderDecisionStreamCenter(appendOnly){
  const listEl = document.getElementById('stream-card-list');
  const statEl = document.getElementById('stream-count-stat');
  const sentinelEl = document.getElementById('stream-scroll-sentinel');
  if(!listEl) return;

  if(!cachedDecisions.length && !isFetchingDecisions){
    isFetchingDecisions = true;
    if(listEl && !appendOnly) listEl.innerHTML = '<p class="hint" style="padding:40px;text-align:center">지식 엔티티 판단 기록을 불러오는 중…</p>';
    try {
      const r = await fetch('resolution/decisions?limit=500');
      if(r.ok){
        const data = await r.json();
        cachedDecisions = data.decisions || [];
      } else {
        cachedDecisions = [];
        if(Array.isArray(allDocs)){
          allDocs.forEach(d => {
            const rlog = (d.meta && d.meta.resolution_log) || [];
            rlog.forEach(item => {
              cachedDecisions.push({
                ...item,
                document_id: d.id,
                document_title: d.title || d.id,
                document_fetched_at: d.fetched_at || 0
              });
            });
          });
        }
      }
    } catch(_) {
      cachedDecisions = [];
    } finally {
      isFetchingDecisions = false;
    }
  }

  const filtered = getFilteredDecisions();
  const total = filtered.length;
  const visible = filtered.slice(0, displayedStreamLimit);

  if(statEl){
    statEl.textContent = '표시: ' + visible.length + ' / 총 ' + total + '건';
  }

  const fBar = document.getElementById('stream-filter-bar');
  if(fBar){
    const totalCount = cachedDecisions.length;
    const mergeCount = cachedDecisions.filter(d => d.decision === 'MERGE').length;
    const newCount = cachedDecisions.filter(d => d.decision === 'CREATE_NEW').length;
    const rollbackCount = cachedDecisions.filter(d => d.decision === 'ROLLED_BACK' || d.rolled_back).length;
    fBar.innerHTML = '<button type="button" class="sec' + (currentDecisionFilter==='ALL'?' active':'') + '" onclick="filterDecisionStreamCenter(\'ALL\')">전체 (' + totalCount + ')</button>' +
      '<button type="button" class="sec' + (currentDecisionFilter==='MERGE'?' active':'') + '" onclick="filterDecisionStreamCenter(\'MERGE\')">병합 (' + mergeCount + ')</button>' +
      '<button type="button" class="sec' + (currentDecisionFilter==='CREATE_NEW'?' active':'') + '" onclick="filterDecisionStreamCenter(\'CREATE_NEW\')">신규 (' + newCount + ')</button>' +
      '<button type="button" class="sec' + (currentDecisionFilter==='ROLLED_BACK'?' active':'') + '" onclick="filterDecisionStreamCenter(\'ROLLED_BACK\')">롤백됨 (' + rollbackCount + ')</button>';
  }

  if(!visible.length){
    listEl.innerHTML = '<p class="hint" style="padding:40px;text-align:center">기록된 의사결정 로그가 없습니다.</p>';
    if(sentinelEl) sentinelEl.textContent = '';
    return;
  }

  listEl.innerHTML = visible.map(d => renderDecisionCardHtml(d)).join('');

  if(sentinelEl){
    if(visible.length < total){
      sentinelEl.innerHTML = '<span>스크롤하여 10개 더 불러오기 (남은 기록: ' + (total - visible.length) + '개)</span>';
    } else {
      sentinelEl.innerHTML = '<span>모든 의사결정 기록을 확인했습니다 (총 ' + total + '개)</span>';
    }
  }
}

function renderDecisionStreamPanel(){
  const totalCount = cachedDecisions.length;
  let filtered = getFilteredDecisions();
  let ph = '<div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:6px">';
  ph += '<h2>📜 의사결정 스트림</h2>';
  ph += '<span style="font-size:11px;color:var(--muted)">총 ' + totalCount + '건의 판단 기록</span>';
  ph += '</div>';
  ph += '<p class="al"><small>지식 베이스 전체의 엔티티 해소(Entity Resolution), 불변식 차단 및 가역적 병합 감사 로그입니다.</small></p>';

  if(!filtered.length){
    ph += '<p class="hint" style="padding:16px 0;text-align:center">기록된 의사결정 로그가 없습니다.</p>';
  } else {
    ph += '<div style="display:flex;flex-direction:column;gap:10px">';
    filtered.slice(0, 30).forEach(dec => {
      ph += renderDecisionCardHtml(dec);
    });
    ph += '</div>';
  }

  if(typeof panel !== 'undefined' && panel) panel.innerHTML = ph;
}

function openHeatmapMatrix(docId){
  const targetId = docId || activeDoc || curReaderDoc;
  let mat = null;
  let title = '';

  if(targetId){
    window.latestHeatmapDocId = targetId;
    try {
      const raw = sessionStorage.getItem('doc_matrix_' + targetId);
      if(raw) mat = JSON.parse(raw);
    } catch(_) {}
    if(!mat && curReaderDocData && curReaderDocData.id === targetId){
      mat = curReaderDocData.heatmap_matrix || (curReaderDocData.meta && curReaderDocData.meta.heatmap_matrix) || null;
    }
    if(curReaderDocData && curReaderDocData.id === targetId){
      title = curReaderDocData.title;
    } else if(allDocs && allDocs.length){
      const docItem = allDocs.find(d => d.id === targetId);
      if(docItem) title = docItem.title;
    }
  }
  if(!mat && window.latestHeatmapMatrix){
    mat = window.latestHeatmapMatrix;
    title = window.latestHeatmapDocTitle || '최근 적재 자료';
  }

  if(!mat){
    return;
  }

  const titleEl = document.getElementById('matrix-title');
  if(titleEl){
    titleEl.innerHTML = '지식 적재 콕핏 <small id="matrix-subtitle">| Ingest Cockpit (' + esc(title || '전체화면') + ')</small>';
  }

  renderHeatmapMatrix(mat, title, false);
  const spin = document.getElementById('matrix-progress-spinner');
  if(spin) spin.style.display = 'none';
  const msgEl = document.getElementById('matrix-progress-msg');
  if(msgEl){
    msgEl.innerHTML = '✅ <strong>대조 완료</strong> (온톨로지 대조 밀도 계산 완료)';
  }
  setCenterView('matrix');
}

async function triggerRollback(docId, entity){
  alert('원자적 롤백 기능은 미시험 상태로 현재 안전을 위해 비활성화되어 있습니다.');
  return;
}

function renderMiniMatrixBannerHtml(docId, mat){
  if(!mat) return '';
  const rows = mat.rows || [];
  const cols = mat.cols || [];
  const matrix = mat.matrix || [];
  const autoMergeThresh = mat.threshold_auto_merge || 0.93;
  const borderlineThresh = mat.threshold_borderline || 0.72;

  let miniGrid = '';
  if(rows.length && cols.length){
    miniGrid = '<div style="display:flex;flex-wrap:wrap;gap:4px;margin-top:8px">';
    rows.slice(0, 6).forEach((r, rIdx) => {
      cols.slice(0, 8).forEach((c, cIdx) => {
        const score = (matrix[rIdx] && matrix[rIdx][cIdx] !== undefined) ? Number(matrix[rIdx][cIdx]) : 0;
        const vis = getMatrixCellVisual(score, autoMergeThresh, borderlineThresh);
        miniGrid += '<div class="matrix-preview-cell" data-tier="' + vis.tier + '" style="width:14px;height:14px;border-radius:2px;background:' + vis.bg + ';border:' + vis.border + '" title="' + esc(r) + ' ↔ ' + esc(c) + ' (' + vis.label + '): ' + score.toFixed(3) + '"></div>';
      });
    });
    miniGrid += '</div>';
  }

  const confirmBtnHtml = '<button type="button" class="pri matrix-purge-btn" onclick="confirmPurgeMatrix(\'' + esc(docId) + '\')" style="padding:4px 10px;font-size:11px;font-weight:bold;background:var(--accent,#0284c7);color:#fff;border:none;border-radius:4px;cursor:pointer;" title="대조 확인 및 매트릭스 소각 파기">✓ 확인</button>';

  return '<div id="doc-matrix-banner-' + esc(docId) + '" class="matrix-preview-banner" style="margin-bottom:16px;padding:12px 14px;background:var(--card-bg);border:2px solid var(--accent,#0284c7);border-radius:8px;">' +
    '<div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px;">' +
      '<div>' +
        '<strong style="color:var(--accent,#0284c7);font-size:13px;display:inline-flex;align-items:center;gap:5px"><span>⚡</span> 지식 적재 대조 콕핏</strong>' +
        '<p style="font-size:11.5px;color:var(--muted);margin:2px 0 0">적재 시점의 전수 대조 밀도 텐서입니다. 관리자 확인 시 완전히 파기됩니다.</p>' +
      '</div>' +
      '<div style="display:flex;align-items:center;gap:6px;">' +
        '<button type="button" class="sec" onclick="openHeatmapMatrix(\'' + esc(docId) + '\')" style="padding:4px 8px;font-size:11px;">🔍 콕핏에서 보기</button>' +
        confirmBtnHtml +
      '</div>' +
    '</div>' +
    miniGrid +
  '</div>';
}

async function confirmPurgeMatrix(docId){
  if(!docId) return;
  if(typeof canWrite === 'function' && canWrite()){
    try {
      const res = await fetch('document/matrix/purge', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({id: docId})
      });
      if(!res.ok){
        console.warn('Matrix purge request failed with status:', res.status);
      }
    } catch(e) {
      console.warn('Matrix purge fetch error:', e);
    }
  }

  try {
    sessionStorage.removeItem('doc_matrix_' + docId);
    sessionStorage.setItem('doc_matrix_confirmed_' + docId, '1');
    localStorage.setItem('doc_matrix_confirmed_' + docId, '1');
  } catch(_) {}

  if(window.latestHeatmapDocId === docId){
    window.latestHeatmapMatrix = null;
    window.latestHeatmapDocId = null;
    window.latestHeatmapDocTitle = '';
  }
  if(curReaderDocData && curReaderDocData.id === docId){
    curReaderDocData.heatmap_matrix = null;
    if(curReaderDocData.meta) delete curReaderDocData.meta.heatmap_matrix;
  }

  const el = document.getElementById('doc-matrix-banner-' + docId);
  if(el){
    el.style.opacity = '0';
    el.style.transform = 'scaleY(0)';
    setTimeout(() => { if(el.parentNode) el.parentNode.removeChild(el); }, 200);
  }
}

// Global Exports
window.suspendGraphForIngest = suspendGraphForIngest;
window.resumeGraphAfterIngest = resumeGraphAfterIngest;
window.handleActiveIngestState = handleActiveIngestState;
window.handleActiveIngestCompleted = handleActiveIngestCompleted;
window.initIngestMatrixView = initIngestMatrixView;
window.updateMatrixProgress = updateMatrixProgress;
window.addDecisionStreamCard = addDecisionStreamCard;
window.getMatrixCellVisual = getMatrixCellVisual;
window.renderHeatmapMatrix = renderHeatmapMatrix;
window.completeIngestMatrixView = completeIngestMatrixView;
window.triggerMatrixWave = triggerMatrixWave;
window.closeMatrixView = closeMatrixView;
window.confirmMatrixView = confirmMatrixView;
window.openMatrixTargetDoc = openMatrixTargetDoc;
window.openDecisionStream = openDecisionStream;
window.filterDecisionStream = filterDecisionStream;
window.filterDecisionStreamCenter = filterDecisionStreamCenter;
window.renderDecisionStreamCenter = renderDecisionStreamCenter;
window.handleStreamScroll = handleStreamScroll;
window.loadMoreStreamItems = loadMoreStreamItems;
window.closeStreamView = closeStreamView;
window.openHeatmapMatrix = openHeatmapMatrix;
window.triggerRollback = triggerRollback;
window.renderMiniMatrixBannerHtml = renderMiniMatrixBannerHtml;
window.confirmPurgeMatrix = confirmPurgeMatrix;
window.openDocDrawer = openDocDrawer;
window.closeDocDrawer = closeDocDrawer;
window.toggleDocDrawer = toggleDocDrawer;
window.closeAllDrawers = closeAllDrawers;
window.openRegen = openRegen;
window.runRegen = runRegen;
window.enterHome = enterHome;
window.resetHome = resetHome;
window.resetWorkspaceState = resetWorkspaceState;
window.resolveHomeView = resolveHomeView;
window.selectJobSlot = selectJobSlot;
window.renderMultiSlotUI = renderMultiSlotUI;
window.extractActiveJobs = extractActiveJobs;
window.processActiveJobsStats = processActiveJobsStats;
window.filterDocsByMissingDetail = filterDocsByMissingDetail;
window.filterDocsByFormatMismatch = filterDocsByFormatMismatch;

document.addEventListener('visibilitychange', () => {
  if(document.visibilityState === 'visible'){
    pollForUpdates();
  }
});

