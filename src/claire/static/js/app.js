/**
 * Claire Bible - Knowledge Graph Workspace Application
 * Controls the ClaireGraph network (static/js/graph/), search, clustering, degree filtering, tabs, and workspace state.
 */

// Catalog text (static/js/i18n.js). Without the runtime, the key shows.
const tr = (key, params) => (window.ClaireI18n ? ClaireI18n.t(key, params) : key);

// The loading watchdog and the global error handlers are in templates/index.html: they run before this bundle.

// --- WebUI Surface & Bootstrap Metadata (§6.4) ---
const _metaApiBase = (document.querySelector('meta[name="claire-api-base"]') || {}).content;
const _metaRootBase = (document.querySelector('meta[name="claire-root-base"]') || {}).content;
const _metaStorageKey = (document.querySelector('meta[name="claire-storage-key"]') || {}).content;

const API_BASE = (_metaApiBase && !_metaApiBase.startsWith('__')) ? _metaApiBase : '/';
const ROOT_BASE = (_metaRootBase && !_metaRootBase.startsWith('__')) ? _metaRootBase : '/';
const STORAGE_KEY = (_metaStorageKey && !_metaStorageKey.startsWith('__')) ? _metaStorageKey : 'anon';

function apiUrl(path) {
  const p = (path || '').startsWith('/') ? path.slice(1) : (path || '');
  return API_BASE + p;
}
window.apiUrl = apiUrl;

function rootUrl(path) {
  const p = (path || '').startsWith('/') ? path.slice(1) : (path || '');
  return ROOT_BASE + p;
}

// --- Account-scoped LocalStorage (§6.4, §7.3) ---
function getAccountStorage(key) {
  try {
    const prefKey = `${STORAGE_KEY}:${key}`;
    const val = localStorage.getItem(prefKey);
    if (val !== null) return val;
    // Migrate old un-prefixed key on first read
    const oldVal = localStorage.getItem(key);
    if (oldVal !== null) {
      localStorage.setItem(prefKey, oldVal);
      localStorage.removeItem(key);
      return oldVal;
    }
  } catch (_) {}
  return null;
}

function setAccountStorage(key, value) {
  try {
    localStorage.setItem(`${STORAGE_KEY}:${key}`, String(value));
  } catch (_) {}
}

function removeAccountStorage(key) {
  try {
    localStorage.removeItem(`${STORAGE_KEY}:${key}`);
    localStorage.removeItem(key);
  } catch (_) {}
}

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
function isPwaOrMobileLaunch() {
  try {
    var isCompact = !!(window.matchMedia && window.matchMedia('(max-width: 900px)').matches);
    var isStandalone = !!(
      (window.matchMedia && window.matchMedia('(display-mode: standalone)').matches) ||
      (window.navigator && window.navigator.standalone === true)
    );
    var params = new URLSearchParams(window.location.search);
    var isPwaParam = params.get('pwa') === '1';
    return isCompact || isStandalone || isPwaParam;
  } catch (e) {
    return false;
  }
}

function shouldDefaultToReader(stats) {
  var params = new URLSearchParams(window.location.search);
  if (params.get('node')) return false;
  var hasActiveIngest = false;
  if (stats && Array.isArray(stats.jobs)) {
    hasActiveIngest = stats.jobs.some(function(j) { return j.status === 'running' || j.status === 'pending'; });
  }
  if (!hasActiveIngest && stats && Array.isArray(stats.active_jobs)) {
    hasActiveIngest = stats.active_jobs.some(function(j) { return j && (j.active || j.status === 'running' || j.status === 'pending'); });
  }
  if (!hasActiveIngest && stats && ((stats.active_ingest && stats.active_ingest.active) || stats.ingesting)) {
    hasActiveIngest = true;
  }
  if (hasActiveIngest) return false;
  return isPwaOrMobileLaunch();
}

function switchView(mode) {
  if (typeof setCenterView === 'function') {
    return setCenterView(mode);
  }
}

var initialDefaultToReader = shouldDefaultToReader(null);
// The first view is the home overview (design HD1). A launch on a narrow screen or as an installed application starts in the reader, and a link to a node starts in the graph.
function initialCenterView(){
  if(initialDefaultToReader) return 'reader';
  try{ if(new URLSearchParams(window.location.search).get('node')) return 'graph'; }catch(_){}
  return 'home';
}
let activePane='graph', detailOpen=false, centerView=initialCenterView(), drawerOpen=false;
let detailReturnFocus=null, docSearchActive=false, serverSearchResults=null;
let graphCamera = null, preservingGraphCamera = false, netBusy = false;
let isDraggingNode = false;
let lastNetSize = {w:0, h:0};
let allTypes = [], allRelTypes = [], allDocs = [];
let net = null, allNodes = null, allEdges = null;
let curMinDeg = 0, activeDoc = null, highlightSet = null, selectedNodeId = null, hoverTimer = null;
// 노드(그래프·'이 문서의 노드' 버튼)에 커서를 이만큼 올려 두면 요약 팝업을 띄운다.
const NODE_POP_DELAY_MS = 1000;
const HOME_TITLE = (window.ClaireI18n ? ClaireI18n.t('home.title') : 'Claire Bible');
if(window.ClaireI18n) ClaireI18n.applyDom();
if(centerView === 'home') document.title = HOME_TITLE;
let activeJobs = [];
let selectedJobId = null;
let isolatedNodeId = null, activeHoverEdges = null, activeHoverNodes = null;
let lastSelectedDocId = null;
try{
  const savedLastDoc = getAccountStorage('claireLastDoc');
  if(savedLastDoc) lastSelectedDocId = savedLastDoc;
}catch(_){}

function recordSelectedDoc(id){
  if(!id) return;
  lastSelectedDocId = id;
  try{ setAccountStorage('claireLastDoc', id); }catch(_){}
}

function esc(s){return (s||'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
// 인라인 이벤트 핸들러 속성 안의 JS 인자. 브라우저가 속성값을 HTML 디코딩한 뒤 JS로 실행하므로
// esc만으로는 '가 되살아난다. JSON 문자열 리터럴로 만든 뒤 속성용으로 esc한다(따옴표 없이 사용).
function jsArg(v){return esc(JSON.stringify(String(v??'')));}
function isWebUrl(u){ return !!u && /^https?:\/\//i.test(String(u).trim()); }

// --- 지식 테마 (Multi-Database Themes) 상태 및 전역 fetch 인터셉터 ---
function isMultiThemeEnabled(){
  return Boolean(window.__CLAIRE_CONFIG && window.__CLAIRE_CONFIG.themeMode === 'multi');
}

function getThemeKey(t){
  if(!t) return '0';
  if(t.is_personal || String(t.id) === '0' || t.number === '0') return '0';
  if(t.number !== null && t.number !== undefined && String(t.number).trim() !== ''){
    return String(t.number).trim();
  }
  return String(t.id ?? '0').trim();
}

let availableThemes = (function(){
  if(window.__CLAIRE_CONFIG && Array.isArray(window.__CLAIRE_CONFIG.themes) && window.__CLAIRE_CONFIG.themes.length){
    return window.__CLAIRE_CONFIG.themes;
  }
  return [{id: 0, seq: 0, number: '0', label: tr("ws.bar.theme_default"), description: tr("ws.bar.theme_default_desc"), icon: '📚', is_personal: true}];
})();

function findTheme(themeRef){
  if(!availableThemes || !availableThemes.length) return null;
  const ref = String(themeRef ?? '').trim();
  if(!ref || ref === '0'){
    return availableThemes.find(t => t.is_personal || String(t.id) === '0' || t.number === '0') || availableThemes[0];
  }
  return availableThemes.find(t => String(t.number) === ref)
    || availableThemes.find(t => String(t.id) === ref)
    || null;
}

// Design Sections 5.2 and 7: the path selects the knowledge base. API_BASE is "/kb/<number>/" when the page path pins a shared knowledge base; else it is the path of a personal knowledge base.
const KB_PIN_RE = /^\/kb\/(\d+)\/$/;
let activeThemeId = (function(){
  const m = API_BASE.match(KB_PIN_RE);
  return m ? m[1] : '0';
})();
// The number of the knowledge base of this page, for routes without a namespace that need the parameter ``kb`` (design Section 5.2 rule 2).
function currentKbNumber(){
  const t = (typeof findTheme === 'function') ? findTheme(activeThemeId) : null;
  const n = t && t.number != null ? String(t.number) : '';
  return /^[1-9]\d*$/.test(n) ? n : '';
}
window.currentKbNumber = currentKbNumber;
// The page path of a knowledge base. A shared one: "/kb/<number>/". The personal one: this page when it is not pinned; else "/kb/<its number>/", and the server sends 308 to its own path (design Section 7).
function kbHomePath(themeKey){
  const key = String(themeKey ?? '0');
  if(key !== '0') return ROOT_BASE + 'kb/' + encodeURIComponent(key) + '/';
  if(!KB_PIN_RE.test(API_BASE)) return API_BASE;
  const personal = (typeof findTheme === 'function') ? findTheme('0') : null;
  const n = personal && personal.number != null ? String(personal.number) : '';
  return /^[1-9]\d*$/.test(n) ? ROOT_BASE + 'kb/' + encodeURIComponent(n) + '/' : ROOT_BASE;
}


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
    // The shared knowledge bases come from the collection /kb (design Section 7). The personal knowledge base of this page stays from the inline list.
    const r = await fetch(rootUrl('kb'), {headers: {Accept: 'application/json'}});
    if(!r.ok) return;
    const data = await r.json();
    const personal = (availableThemes || []).filter(t => t.is_personal);
    const shared = data.knowledge_bases || [];
    if(personal.length || shared.length){
      availableThemes = [...personal, ...shared];
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
    availableThemes = [{id: 0, seq: 0, number: '0', label: tr("ws.bar.theme_default"), description: tr("ws.bar.theme_default_desc"), icon: '📚', is_personal: true}];
  }

  const current = findTheme(activeThemeId) || availableThemes[0];
  if(current){
    activeThemeId = getThemeKey(current);
  }

  // 일반 접속 시: 싱글 테마 모드이거나 테마가 1개뿐인 경우 숨김, 멀티 테마일 때 드롭다운 박스 표시
  if(wrap){
    wrap.style.display = (isMultiThemeEnabled() && availableThemes.length > 1) ? 'inline-flex' : 'none';
    wrap.title = tr("ws.bar.theme_title");
  }
  if(ctrl) ctrl.style.display = 'inline-flex';
  if(plainDisplay) plainDisplay.style.display = 'none';
  if(iconEl && current){
    iconEl.textContent = current.icon || '📚';
  }

  sel.innerHTML = availableThemes.map(t => {
    const key = getThemeKey(t);
    const label = esc(t.label);
    const lockStr = t.is_public === false ? ' ' + tr("ws.bar.private_suffix") : '';
    return `<option value="${key}" ${key === String(activeThemeId) ? 'selected' : ''}>${label}${lockStr}</option>`;
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
  el.textContent = tr("ws.ctx.manager") + ' ' + mgr;
  el.title = tr("ws.ctx.manager") + ' ' + mgr;
}

function isPresentationEnabledForActiveTheme(){
  if(!availableThemes || !availableThemes.length) return false;
  const current = findTheme(activeThemeId) || availableThemes[0];
  return Boolean(current && current.presentation_enabled === true);
}

function isDecisionStreamEnabledForActiveTheme(){
  if(!availableThemes || !availableThemes.length) return false;
  const current = findTheme(activeThemeId) || availableThemes[0];
  return Boolean(current && current.decision_stream_enabled === true);
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
  const current = findTheme(activeThemeId);
  if(current){
    if(iconEl) iconEl.textContent = current.icon || '📚';
    if(plainIcon) plainIcon.textContent = current.icon || '📚';
    if(plainLabel) plainLabel.textContent = current.label || tr("ws.bar.theme_default");
    updateDrawerManager(current);
  }
  syncPresentationFeatureUI();
}

function renderThemeOptions(selectedId){
  const themes = (availableThemes && availableThemes.length)
    ? availableThemes
    : [{id: 0, seq: 0, number: '0', label: tr("ws.bar.theme_default"), description: tr("ws.bar.theme_default_desc"), icon: '📚'}];
  const activeId = selectedId !== undefined ? String(selectedId) : String(activeThemeId);
  return themes.map(t => {
    const key = getThemeKey(t);
    const icon = esc(t.icon || '📚');
    const label = esc(t.label);
    const lockStr = t.is_public === false ? ' ' + tr("ws.bar.private_suffix") : '';
    return `<option value="${key}" ${key === activeId ? 'selected' : ''}>${icon} ${label}${lockStr}</option>`;
  }).join('');
}

async function loadThemeDocuments(){
  // The document data starts again: the first list page (31 documents), the pinned documents, and the hidden documents.
  const main = resetDocStore();
  try{
    // fetch('documents')
    const [d] = await Promise.all([fetchDocuments(docPagerParams(main)), loadDocSideLists()]);
    if(main !== docMainPager) return;
    applyDocPage(main, d);
    rebuildAllDocs();
    renderDocs(document.getElementById('docq') ? document.getElementById('docq').value : '');
    if(d && d.format_status){
      window.currentFormatStatus = d.format_status;
      if(typeof renderHome === 'function' && centerView === 'home') renderHome();
    }
    if(allDocs && allDocs.length > 0){
      let docFromUrl = null;
      try{
        const sp = new URLSearchParams(window.location.search);
        const p = sp.get('doc');
        // A deep link to a document that is not on the first page loads the pages up to that document.
        if(p && await ensureDocLoaded(p)){ docFromUrl = p; docScrollToActive = true; }
      }catch(_){}
      if(main !== docMainPager) return;
      if(docFromUrl) renderDocs(document.getElementById('docq') ? document.getElementById('docq').value : '');
      const currentDoc = (typeof curReaderDoc !== 'undefined' && curReaderDoc) || activeDoc;
      const isReaderElementOpen = Boolean(document.getElementById('reader') && document.getElementById('reader').classList.contains('open'));
      const hasDocOpen = Boolean(currentDoc || isReaderElementOpen);
      if(docFromUrl){
        if(!currentDoc || currentDoc !== docFromUrl){
          if(typeof selectDoc === 'function'){
            selectDoc(docFromUrl);
          } else if(typeof openReader === 'function'){
            openReader(docFromUrl, false);
          }
        }
      } else if(typeof centerView !== 'undefined' && centerView === 'reader' && !hasDocOpen){
        const firstDocId = allDocs[0].id;
        if(typeof selectDoc === 'function'){
          selectDoc(firstDocId);
        } else if(typeof openReader === 'function'){
          openReader(firstDocId, false);
        }
      }
    }
  }catch(e){
    if(main !== docMainPager) return;
    resetDocStore();
    const dl = document.getElementById('doclist');
    if(dl) dl.innerHTML = (typeof doclistToolbarHtml==='function'?doclistToolbarHtml():'') + '<p class="hint" style="padding:10px">' + tr("ws.list.load_failed") + '</p>';
  }
}

async function reloadThemeData(resetCamera = true){
  try{
    // fetch('graph')
    const r = await fetch(apiUrl('graph'));
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
  const ref = String(themeId ?? '').trim();
  if(!ref) return;
  const themeObj = findTheme(ref);
  const targetId = themeObj ? getThemeKey(themeObj) : ref;
  if(targetId === String(activeThemeId)) return;
  // Navigation between knowledge bases uses paths, not a header (design Section 5.3).
  window.location.assign(kbHomePath(targetId));
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
      const saved = getAccountStorage('claireLastDoc');
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
let IS_ADMIN=false;
let relFilter = null;
let pathMode = false, pathPicks = [], pathNodes = null, pathEdges = null;
let edgeLabelsByZoom = false, selectedEdgeIds = new Set(), lastZoomTier = 0;
let graphStabilized = false;
// --- Context panel on demand (design Section 4.5) ---
// The right pane has a width on a wide screen only when a context exists (a document, an entity, or the graph view) and the user did not close it.
let contextUserOpen = true, contextPinned = false;
try{ if(getAccountStorage('claireContextOpen') === 'false') contextUserOpen = false; }catch(_){}
function hasContext(){
  // A form or a result in the panel (ingest, regenerate, research, synthesis) is a context in each view, also in the home view.
  if(contextPinned || selectedNodeId) return true;
  if(centerView === 'home') return false;
  return Boolean(activeDoc || curReaderDoc || centerView === 'graph');
}
function isContextOpen(){ return hasContext() && contextUserOpen; }
function setContextUserOpen(open){
  contextUserOpen = Boolean(open);
  try{ setAccountStorage('claireContextOpen', contextUserOpen ? 'true' : 'false'); }catch(_){}
  if(contextUserOpen && centerView === 'graph' && !selectedNodeId && typeof renderGraphInsights === 'function'){
    renderGraphInsights();
  }
  syncWorkspaceLayout();
}
// The context panel has one close button, #detailclose in its own head. The open buttons (#rctxbtn in the reader head, #gctxbtn on the graph canvas, #botnav-context in the bottom bar of a narrow screen) only open it, and the header bar has none.
function contextOpenButton(){
  for(const id of ['rctxbtn','gctxbtn']){
    const b = document.getElementById(id);
    if(b && !b.hidden && b.getClientRects().length) return b;
  }
  return null;
}
function openContextPanel(){
  const opener = document.activeElement;
  if(compactMQ.matches || mobileMQ.matches){
    if(drawerOpen || detailOpen) return;
    openDrawer();
  }else{
    if(!hasContext()) return;
    setContextUserOpen(true);
  }
  // The open button hides when the panel opens; the focus goes into the panel and not to the page body.
  if(opener && opener.hidden){
    const dc = document.getElementById('detailclose');
    if(dc) dc.focus();
  }
}
function closeContextPanel(){
  if(compactMQ.matches || mobileMQ.matches){ closeDrawer(true); return; }
  const dp = document.getElementById('detailpane');
  const hadFocus = Boolean(dp && dp.contains(document.activeElement));
  setContextUserOpen(false);
  if(hadFocus){
    const target = contextOpenButton() || paneEls[activePane];
    if(target) target.focus();
  }
}
// --- List pane: open state and the list that the user selects (design Section 4.3) ---
let listPaneOpen = true, listOverride = null, lastListMode = null;
try{ if(getAccountStorage('claireListOpen') === 'false') listPaneOpen = false; }catch(_){}
function toggleListPane(){
  if(mobileMQ.matches){ toggleDocDrawer(); return; }
  listPaneOpen = !listPaneOpen;
  try{ setAccountStorage('claireListOpen', listPaneOpen ? 'true' : 'false'); }catch(_){}
  syncWorkspaceLayout();
}
function setListOverride(mode){
  listOverride = (mode === 'entities' || mode === 'docs') ? mode : null;
  syncListPane();
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
function canIngest(){ return AUTH_SCOPE==='owner' || AUTH_SCOPE==='write'; }
// 함수로 둔 이유: READONLY 는 /whoami 가 비동기로 확정하므로, 호출 시점 기준으로
// 종합 안내 줄을 넣을지 뺄지 판단해야 한다(고정 문자열이면 초기 로드 시점 값에 박제됨).
function personalKbCardHtml(){
  if(!canWrite()) return '';
  return '<div class="claire-card" id="ctx-personal-kb-card" style="margin-top:12px"><div class="claire-card-head"><h3><span data-icon="folder"></span> ' + esc(tr('ws.kb.personal_kb_title')) + '</h3><a class="claire-btn claire-btn-sm" href="/settings/kb">' + esc(tr('ws.kb.personal_kb_manage')) + '</a></div></div>';
}
function defaultHint(){
  const synthLine = canWrite()
    ? '• ' + tr('ws.hint.synth') + '<br>'
    : '';
  return '<p class="hint">'+tr('ws.hint.click_node')+'<br><br>'+synthLine+
    '• '+tr('ws.hint.hover', { seconds: NODE_POP_DELAY_MS/1000 })+'<br>'+
    '• '+tr('ws.hint.source_pick')+' <b><span data-icon="book"></span></b><br>'+
    '• '+tr('ws.hint.theme')+' <b><span data-icon="moon"></span>/<span data-icon="sun"></span></b></p>' + personalKbCardHtml();
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
    '<button type="button" class=pclose onclick="event.stopPropagation();hideNodePop()" aria-label="' + tr("common.action.close") + '">✕</button></div>'+
    '<div class=pt><i style="background:'+c+'"></i>'+tr('ws.node.connections', { count: n.degree||0 })+'</div>';
  const foot=isMobile?'<div class=pact><button type="button" onclick="event.stopPropagation();hideNodePop();openDetailPane()">' + tr("ws.node.more_details") + '</button></div>':'';
  nodepop.innerHTML=head+(n.obs?'<div class=po>'+esc(n.obs)+'</div>':'')+foot;
  nodepop.style.display='block';
  positionPop(px, py);                  // 표시 후(폭/높이 확정) 화면 밖으로 안 나가게 배치
  fetch(apiUrl('node?id='+encodeURIComponent(id)+'&docs=brief')).then(r=>r.json()).then(d=>{
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
// 팝업 하단의 출처 문서 한 건 — 제목 + 요약 일부. (GET /node?docs=brief has no detail text.)
function popSource(d){
  const body=((d.summary||'').replace(/\s+/g,' ').trim());
  return '<div class=psrc><div class=ptt><span data-icon="doc"></span> '+esc(d.title||tr("home.recent.untitled"))+'</div>'+
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
  if(b) b.setAttribute('data-icon', curTheme()==='dark'?'sun':'moon'); }
function onAppThemeChanged(next){
  const presFrame = document.getElementById('presentation-frame');
  if(presFrame && presFrame.contentWindow){
    try{ presFrame.contentWindow.postMessage({type: 'cb-theme-change', theme: next}, '*'); }catch(_){}
  }
  if(net){ const th=T();
    net.setOptions({nodes:{font:{color:th.nodeFont}},
      edges:{color:{color:th.edge,highlight:th.edgeHi},font:{color:th.nodeFont}},
      groups:buildGroups()});
    applyView(); }   // 노드별 테두리(lit/normal)도 새 테마 색으로 다시 칠함
  if(typeof drawHomeMiniMap === 'function' && centerView === 'home') drawHomeMiniMap();
}
window.__CLAIRE_ON_THEME_CHANGE = onAppThemeChanged;
function toggleTheme(){
  if(window.ClaireAppBar && typeof window.ClaireAppBar.toggleTheme === 'function'){
    return window.ClaireAppBar.toggleTheme();
  }
  const next = curTheme()==='dark'?'light':'dark';
  document.documentElement.setAttribute('data-theme', next);
  try{ localStorage.setItem('claireTheme', next); }catch(e){}
  syncThemeBtn();
  onAppThemeChanged(next);
  return next;
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

  // Wide screens: the context panel and the list pane have a width only when they are open.
  const wide = !isMobileOrCompact, ctxOpen = isContextOpen();
  document.body.classList.toggle('ctx-closed', wide && !ctxOpen);
  document.body.classList.toggle('list-closed', !mobileMQ.matches && !listPaneOpen);
  if(dp && wide){
    dp.setAttribute('aria-hidden', ctxOpen ? 'false' : 'true');
    dp.inert = !ctxOpen;
  }
  // The open buttons of the context panel show only while the panel (the drawer on a narrow screen) is closed and a context exists. The panel closes only with #detailclose.
  const ctxShown = wide ? ctxOpen : isDrawerActive;
  ['rctxbtn','gctxbtn'].forEach(id => {
    const b = document.getElementById(id);
    if(b) b.hidden = ctxShown || !hasContext();
  });
  const listBtn = document.getElementById('listtoggle');
  if(listBtn) listBtn.setAttribute('aria-expanded', listPaneOpen ? 'true' : 'false');
  const docsPane = document.getElementById('docs');
  const docOpenNarrow = mobileMQ.matches && typeof docDrawerOpen !== 'undefined' && docDrawerOpen;
  const ctxOpenNarrow = mobileMQ.matches && isDrawerActive;
  if(docsPane){
    if(!mobileMQ.matches){
      docsPane.setAttribute('aria-hidden', listPaneOpen ? 'false' : 'true');
      docsPane.inert = !listPaneOpen;
    }
    if(docOpenNarrow){
      docsPane.setAttribute('role', 'dialog');
      docsPane.setAttribute('aria-modal', 'true');
    }else{
      docsPane.removeAttribute('role');
      docsPane.removeAttribute('aria-modal');
    }
  }
  if(dp){
    if(ctxOpenNarrow){
      dp.setAttribute('role', 'dialog');
      dp.setAttribute('aria-modal', 'true');
    }else{
      dp.removeAttribute('role');
      dp.removeAttribute('aria-modal');
    }
  }
  const cw = document.getElementById('centerwrap');
  if(cw) cw.inert = Boolean(docOpenNarrow || ctxOpenNarrow);
  ['home','reader','graph'].forEach(name => {
    const b = document.getElementById('botnav-' + name);
    if(!b) return;
    if(centerView === name) b.setAttribute('aria-current', 'page'); else b.removeAttribute('aria-current');
  });
  const bList = document.getElementById('botnav-list');
  if(bList) bList.setAttribute('aria-expanded', docOpenNarrow ? 'true' : 'false');
  const bCtx = document.getElementById('botnav-context');
  if(bCtx) bCtx.setAttribute('aria-expanded', ctxOpenNarrow ? 'true' : 'false');

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
  contextPinned=true;
  contextUserOpen=true;
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
  if(!target) target=contextOpenButton() || paneEls[activePane];
  detailReturnFocus=null;
  replaceAppHistory({ modal: getActiveModalName() });
  requestAnimationFrame(()=>{ if(target) target.focus(); });
}
let pendingDrawerFocus = false, docDrawerReturnFocus = null, pendingDocDrawerFocus = false;
function openDrawer(pushHist=true){
  hideNodePop();
  let replacedModal = false;
  if(mobileMQ.matches && typeof docDrawerOpen !== 'undefined' && docDrawerOpen){
    docDrawerOpen = false;
    docDrawerReturnFocus = null;
    pendingDocDrawerFocus = false;
    document.body.classList.remove('docdrawer-open');
    if(typeof window !== 'undefined' && window.history && window.history.state && window.history.state.modal === 'docdrawer'){
      replaceAppHistory({ modal: 'drawer' });
      replacedModal = true;
    }
  }
  detailReturnFocus=document.activeElement;
  drawerOpen=true;
  detailOpen=true;
  if(pushHist && !replacedModal && (compactMQ.matches || mobileMQ.matches)) pushAppHistory({ modal: 'drawer' });
  closeGraphDocPicker();
  syncWorkspaceLayout();
  if(mobileMQ.matches){
    requestAnimationFrame(()=>{
      const dp = document.getElementById('detailpane');
      if((drawerOpen || detailOpen) && dp && !dp.contains(document.activeElement)){
        const dc = document.getElementById('detailclose') || dp;
        dc.focus();
      }
    });
  }
}
function closeDrawer(focus=false){
  const restoreFocus = (typeof focus === 'object' && focus !== null) ? focus.restoreFocus !== false : Boolean(focus);
  if(typeof window !== 'undefined' && window.history && window.history.state && window.history.state.modal === 'drawer' && !isPoppingHistory){
    pendingDrawerFocus = restoreFocus;
    window.history.back();
    return;
  }
  const shouldFocus = restoreFocus || pendingDrawerFocus;
  pendingDrawerFocus = false;
  hideNodePop();
  drawerOpen=false;
  detailOpen=false;
  syncWorkspaceLayout();
  let target=detailReturnFocus && detailReturnFocus.isConnected ? detailReturnFocus : null;
  if(!target) target=mobileMQ.matches ? (document.getElementById('botnav-context') || contextOpenButton() || paneEls[activePane]) : (contextOpenButton() || paneEls[activePane]);
  detailReturnFocus=null;
  replaceAppHistory({ modal: getActiveModalName() });
  if(shouldFocus && target) requestAnimationFrame(()=>target.focus());
}
let docDrawerOpen = false;
function openDocDrawer(pushHist=true){
  hideNodePop();
  let replacedModal = false;
  if(mobileMQ.matches && (drawerOpen || detailOpen)){
    drawerOpen = false;
    detailOpen = false;
    detailReturnFocus = null;
    pendingDrawerFocus = false;
    if(typeof window !== 'undefined' && window.history && window.history.state && window.history.state.modal === 'drawer'){
      replaceAppHistory({ modal: 'docdrawer' });
      replacedModal = true;
    }
  }
  docDrawerReturnFocus = document.activeElement;
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
  syncWorkspaceLayout();
  if(pushHist && !replacedModal && mobileMQ.matches) pushAppHistory({ modal: 'docdrawer' });
  if(mobileMQ.matches){
    requestAnimationFrame(()=>{
      if(docDrawerOpen && docsEl && !docsEl.contains(document.activeElement)){
        const f = document.querySelector('#docs #listswitch button, #docclose') || docsEl;
        f.focus();
      }
    });
  }
}
function closeDocDrawer(focus=false){
  const restoreFocus = (typeof focus === 'object' && focus !== null) ? focus.restoreFocus !== false : Boolean(focus);
  if(typeof window !== 'undefined' && window.history && window.history.state && window.history.state.modal === 'docdrawer' && !isPoppingHistory){
    pendingDocDrawerFocus = restoreFocus;
    window.history.back();
    return;
  }
  const shouldFocus = restoreFocus || pendingDocDrawerFocus;
  pendingDocDrawerFocus = false;
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
  syncWorkspaceLayout();
  replaceAppHistory({ modal: getActiveModalName() });
  const target = docDrawerReturnFocus && docDrawerReturnFocus.isConnected ? docDrawerReturnFocus : (document.getElementById('botnav-list') || document.getElementById('mobile-doclist-btn'));
  docDrawerReturnFocus = null;
  if(shouldFocus && target) requestAnimationFrame(()=>target.focus());
}
function toggleDocDrawer(){
  if(docDrawerOpen) closeDocDrawer(true);
  else openDocDrawer();
}
function closeAllDrawers(){
  if(docDrawerOpen) closeDocDrawer(true);
  if(drawerOpen || detailOpen) closeDrawer(true);
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
function graphSelectableDocs(){
  const visible=allDocs.filter(dc=>dc.hidden!==1);
  return visible.filter(dc=>dc.pinned===1).concat(visible.filter(dc=>dc.pinned!==1));
}
function syncGraphDocNav(){
  const pick=document.getElementById('graphdocpick');
  if(!pick) return;
  const dc=activeDoc ? allDocs.find(item=>item.id===activeDoc) : null;
  document.getElementById('graphdoclabel').textContent=dc ? dc.title : tr("ws.graph.all");
  pick.setAttribute('aria-label',dc ? tr('ws.graph.pick_current', { title: dc.title }) : tr('ws.graph.pick_all'));
  const prev=document.getElementById('graphdocprev'), next=document.getElementById('graphdocnext');
  if(prev && next){
    const docs=graphSelectableDocs();
    const current=docs.findIndex(item=>item.id===activeDoc);
    const usable=current>=0 && docs.length>1;
    prev.disabled=!usable; next.disabled=!usable;
    if(usable){
      const prevDoc=docs[(current-1+docs.length)%docs.length];
      const nextDoc=docs[(current+1)%docs.length];
      prev.setAttribute('aria-label',tr('ws.graph.prev_doc', { title: prevDoc.title }));
      next.setAttribute('aria-label',tr('ws.graph.next_doc', { title: nextDoc.title }));
      prev.title=tr('ws.graph.prev_doc', { title: prevDoc.title });
      next.title=tr('ws.graph.next_doc', { title: nextDoc.title });
    }else{
      prev.setAttribute('aria-label',tr("ws.graph.prev"));
      next.setAttribute('aria-label',tr("ws.graph.next"));
      prev.title=tr("ws.graph.prev"); next.title=tr("ws.graph.next");
    }
  }
  const menu=document.getElementById('graphdocmenu');
  if(!menu.hidden) renderGraphDocPicker(document.getElementById('graphdocq').value);
}
// The picker shows the documents that the client knows (the loaded list pages and the pinned documents). When the list has more pages, filter text also asks the server (title or summary), thus a document after the loaded pages can be found.
let docPickTimer = null;
function queryGraphDocPicker(q, filter){
  const seq = ++docPickSeq, main = docMainPager;
  clearTimeout(docPickTimer);
  docPickTimer = setTimeout(async () => {
    try{
      const d = await fetchDocuments(new URLSearchParams({limit: String(LIST_PAGE), hidden: '0', q: q}));
      if(seq !== docPickSeq || main !== docMainPager) return;
      (d.documents || []).forEach(internDoc);
      rebuildAllDocs();
      const menu = document.getElementById('graphdocmenu'), input = document.getElementById('graphdocq');
      if(menu && !menu.hidden && input && input.value === filter) renderGraphDocPicker(filter, true);
    }catch(_){}
  }, 250);
}
function renderGraphDocPicker(filter='', fromServer=false){
  const q=filter.trim().toLowerCase();
  const docs=graphSelectableDocs().filter(dc=>
    !q || ((dc.title||'')+' '+(dc.summary||'')).toLowerCase().includes(q));
  let h='<button id="graphdocall" class="graphdocoption" data-graph-doc=""'+
    (!activeDoc?' aria-current="true"':'')+'>' + tr("ws.graph.all") + '</button>';
  h+=docs.map(dc=>'<button class="graphdocoption" data-graph-doc="'+esc(dc.id)+'"'+
      (dc.id===activeDoc?' aria-current="true"':'')+'>'+
      (dc.pinned===1?'<span data-icon="star" class="filled"></span> ':'')+esc(dc.title||tr("home.recent.untitled"))+
      (dc.source_type?'<small>'+esc(dc.source_type)+'</small>':'')+'</button>').join('');
  const morePages = docMainPager.loaded && docMainPager.next != null;
  if(!docs.length) h+='<div id="graphdocempty">'+((morePages && q && !fromServer) ? tr("ws.graph.searching") : tr("ws.graph.no_results"))+'</div>';
  else if(morePages && !q) h+='<div id="graphdocmore" class="hint">' + tr("ws.graph.older_hint") + '</div>';
  document.getElementById('graphdoclist').innerHTML=h;
  if(morePages && q && !fromServer) queryGraphDocPicker(q, filter);
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
  const bnav=document.getElementById('botnav')||document.getElementById('worktabs');
  if((drawerOpen||detailOpen) && pane && (!bar||!bar.contains(e.target)) && (!bnav||!bnav.contains(e.target)) && !pane.contains(e.target)) closeDrawer(false, true);
  const nav=document.getElementById('graphdocnav');
  const gdm=document.getElementById('graphdocmenu');
  if(gdm && !gdm.hidden && nav && !nav.contains(e.target)) closeGraphDocPicker(false, true);
});
// Remove entity selection and restore panel state for the active center view.
function deselectEntity(){
  if(!selectedNodeId) return;
  selectedNodeId = null;
  currentNodeData = null;
  contextPinned = false;
  nodeLoadSeq++;
  if(isolatedNodeId) clearNodeIsolation();
  if(net) net.unselectAll();
  if(typeof closeEntityRow === 'function') closeEntityRow();
  if(centerView === 'graph'){
    if(typeof renderGraphInsights === 'function') renderGraphInsights();
    syncWorkspaceLayout();
  } else if(centerView === 'reader'){
    const returnDocId = (typeof curReaderDoc !== 'undefined' && curReaderDoc) || activeDoc;
    if(returnDocId && typeof loadDocPanel === 'function'){
      loadDocPanel(returnDocId);
    } else {
      const p = document.getElementById('panel');
      if(p && typeof defaultHint === 'function') p.innerHTML = defaultHint();
    }
    syncWorkspaceLayout();
  } else if(centerView === 'home'){
    const p = document.getElementById('panel');
    if(p && typeof defaultHint === 'function') p.innerHTML = defaultHint();
    if(compactMQ.matches || mobileMQ.matches){
      closeDrawer(true);
    } else {
      syncWorkspaceLayout();
    }
  } else {
    const p = document.getElementById('panel');
    if(p && typeof defaultHint === 'function') p.innerHTML = defaultHint();
    syncWorkspaceLayout();
  }
  applyView();
  if(typeof drawHomeMiniMap === 'function' && centerView === 'home') drawHomeMiniMap();
}
window.deselectEntity = deselectEntity;

document.addEventListener('keydown',e=>{
  if(e.key==='Escape'){
    if(document.querySelector('dialog[open]')) return;
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
    const gdm = document.getElementById('graphdocmenu');
    if(gdm && !gdm.hidden){
      closeGraphDocPicker(true, true);
      return;
    }
    if(docDrawerOpen){
      e.preventDefault();
      e.stopImmediatePropagation();
      closeDocDrawer(true);
      return;
    }
    if(selectedNodeId){
      e.preventDefault();
      e.stopImmediatePropagation();
      deselectEntity();
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
  // fetch('graph')
  fetch(apiUrl('graph')).then(r=>{ if(!r.ok) throw new Error('graph fetch HTTP '+r.status); return r.json(); }).then(d=>{
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
    if(st) st.textContent = tr("ws.list.data_ready");
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
    if(!netEl.clientWidth || !netEl.clientHeight) graphStabilized = true;
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
        currentNodeData=null;
        contextPinned=false;
        nodeLoadSeq++;
        if(isolatedNodeId){
          isolatedNodeId = null;
        }
        if(typeof closeEntityRow === 'function') closeEntityRow();
        applyView();
        if(centerView === 'graph' && typeof renderGraphInsights === 'function') renderGraphInsights();
        syncWorkspaceLayout();
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
  if(centerView === 'graph' && !selectedNodeId && typeof renderGraphInsights === 'function'){
    renderGraphInsights();
  } else if(centerView === 'reader' && activeDoc && !selectedNodeId){
    if(curReaderDocData && curReaderDocData.id===activeDoc) renderDocPanel(curReaderDocData);
    else loadDocPanel(activeDoc);
  } else if(centerView === 'reader' && curDocPanelData && activeDocCtxTab === 'links'){
    renderDocPanel(curDocPanelData);
  }
  if(onComplete) onComplete();
}).catch(err => {
  console.warn('graph load error:', err);
  const st=document.getElementById('stat');
  if(st && st.textContent === tr('ws.list.loading')) st.textContent=tr("ws.graph.ready");
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
fetch(apiUrl('stats')).then(r => r.ok ? r.json() : null).then(d => {
  applyEffortSpecFromStats(d);
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
  } else if(view === 'home+banner'){
    homeStats = d || homeStats;
    initGraph();
    if(shouldDefaultToReader(d)){
      setCenterView('reader');
    }
    renderHome();
    showOtherThemeJobBanner(d);
    if(centerView === 'stream' && typeof renderDecisionStreamCenter === 'function'){
      renderDecisionStreamCenter(false);
    }
  } else {
    homeStats = d || homeStats;
    initGraph();
    if(shouldDefaultToReader(d)){
      setCenterView('reader');
    }
    renderHome();
    if(centerView === 'stream' && typeof renderDecisionStreamCenter === 'function'){
      renderDecisionStreamCenter(false);
    }
  }
}).catch(() => {
  initGraph();
  if(shouldDefaultToReader(null)){
    setCenterView('reader');
  }
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
  highlightSet=null; selectedNodeId=null; currentNodeData=null;
  contextPinned = false;
  nodeLoadSeq++;
  if(isolatedNodeId){
    isolatedNodeId = null;
  }
  const q=document.getElementById('q'); if(q) q.value='';
  if(net) net.unselectAll();
  unclusterEdges();   // 검색으로 뭉치게 한 임시 spring 엣지 제거 → 물리가 원래대로
  if(centerView === 'graph'){
    if(typeof renderGraphInsights === 'function') renderGraphInsights();
  } else if(centerView === 'reader' && (activeDoc || (typeof curReaderDoc !== 'undefined' && curReaderDoc))){
    loadDocPanel(activeDoc || curReaderDoc);
  } else if(panel){
    panel.innerHTML = defaultHint();
  }
  applyView();
  syncWorkspaceLayout();
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
    labelEl.innerHTML = '<span style="color:var(--accent);font-weight:600">' + tr("ws.node.alias_pick") + '</span>';
  }
  if(cancelBtn){
    cancelBtn.style.display = 'inline-flex';
  }
  if(chipsEl){
    chipsEl.classList.add('selecting');
    chipsEl.innerHTML = currentNodeData.aliases.map(al =>
      '<button type="button" class="node-alias-chip selectable" title="' + esc(tr('ws.node.alias_promote_title', { alias: al })) + '" onclick="promoteEntityAlias(' + jsArg(currentNodeData.id) + ',' + jsArg(al) + ')">' +
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
    labelEl.textContent = tr('ws.node.aliases', { count: currentNodeData.aliases.length });
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

let nodeLoadSeq = 0;
function loadNode(id, hidePop=true){
  const focusCamera = arguments.length > 2 ? arguments[2] : true;
  if(hidePop) hideNodePop();
  selectedNodeId = id;
  setContextUserOpen(true);
  // Narrow screens: the selection opens the context sheet, but not from the list drawer. A row of the entity list shows its detail in the row, and the list stays open.
  if((compactMQ.matches || mobileMQ.matches) && !drawerOpen && !detailOpen && !docDrawerOpen){
    openDrawer(true);
  }
  if(net) net.selectNodes([id]);   // 클릭 inspect — hover 는 더 이상 패널을 안 쓴다(팝업으로 분리)
  openEntityRow(id);               // The entity list opens the row of this node before applyView(), thus applyView() does not render the list again. The row uses the response of the request below.
  applyView();                     // 선택 노드 주변 엣지만 강조하고 관계 라벨을 펼친다
  if(focusCamera) focusOnNode(id);
  if(typeof drawHomeMiniMap === 'function' && centerView === 'home') drawHomeMiniMap();
  // Only the response of the last request is used. A late response for an earlier node must not replace the panel.
  const seq = ++nodeLoadSeq;
  fetch(apiUrl('node?id='+encodeURIComponent(id)+'&docs=brief')).then(r=>r.json()).then(d=>{
    if(seq !== nodeLoadSeq) return;
    renderPanel(d); setEntityRowDetail(id, d);
  }).catch(()=>{
    if(seq !== nodeLoadSeq) return;
    currentNodeData = null;
    panel.innerHTML='<p class=hint>' + tr("ws.node.load_failed") + '</p>';
    setEntityRowDetail(id, {error:true, failed:true});
  });
}
// --- Source document rows of the node detail panel (unit LP-4) ---
let srcWindowList = null, srcDocsAll = [];
// The identifier of the summary region uses the index of the row in the rendered list. An identifier from the document identifier is not always unique after the removal of characters.
function srcRowHtml(dc, prev, index){
  const sid = 'srcsum-' + index;
  const day = dc.fetched_at ? dayOf(dc.fetched_at) : '';
  let h = '<div class="srcrow" role="listitem" data-src-id="' + esc(dc.id) + '"><div class="srchead">';
  h += '<button type="button" class="srctitle" aria-expanded="false" aria-controls="' + sid + '"><span class="srccaret" aria-hidden="true">▸</span><span class="srcname">' + esc(dc.title) + '</span>';
  h += '<span class="srcmeta">' + (day ? '<span class="srcdate">' + day + '</span>' : '') + (dc.source_type ? '<span class="srctype">' + esc(dc.source_type) + '</span>' : '') + '</span></button>';
  // The default response of GET /node has detail, and docs=brief has has_detail.
  if(dc.has_detail || dc.detail || dc.summary) h += '<button type="button" class="srcread" data-read-doc="' + esc(dc.id) + '" title="' + tr("ws.node.read_doc") + '" aria-label="' + tr("ws.node.read_doc") + '"><span data-icon="book"></span></button>';
  h += '</div><div id="' + sid + '" class="srcbody" role="region" aria-label="' + esc(tr('ws.node.src_summary', { title: dc.title })) + '" hidden>';
  h += dc.summary ? '<p class="srcsum">' + esc(dc.summary) + '</p>' : '<p class="srcsum srcnone">' + tr("ws.node.no_summary") + '</p>';
  const links = [];
  if(isWebUrl(dc.url)) links.push('<a href="' + esc(dc.url) + '" target=_blank rel=noopener>' + tr("ws.node.open_source") + '</a>');
  if(canIngest() && dc.has_raw_file) links.push('<a href="' + apiUrl('document/raw?id=' + encodeURIComponent(dc.id)) + '" target=_blank rel=noopener><span data-icon="doc"></span> ' + tr("ws.node.open_raw") + '</a>');
  if(links.length) h += '<p class="src">' + links.join(' · ') + '</p>';
  return h + '</div></div>';
}
// Writes the rows into #srclist, LIST_PAGE rows at a time. The scroll container of the panel (#drawerscroll) is the observer root. All rows are closed after each call.
function mountSourceList(docs){
  if(srcWindowList){ srcWindowList.invalidate(); srcWindowList = null; }
  srcDocsAll = docs || [];
  const box = document.getElementById('srclist'); if(!box) return;
  srcWindowList = createWindowedList({container: box, root: document.getElementById('drawerscroll'), renderItem: srcRowHtml});
  srcWindowList.render(srcDocsAll, 'src:');
}
function filterSourceList(v){
  if(!srcWindowList) return;
  const q = (v||'').trim().toLowerCase();
  const items = q ? srcDocsAll.filter(dc => String(dc.title||'').toLowerCase().includes(q)) : srcDocsAll;
  srcWindowList.render(items, 'src:' + q, {empty: '<p class="hint">' + tr("ws.node.no_title_match") + '</p>'});
}
panel.addEventListener('input', e => { if(e.target && e.target.id === 'srcfilter') filterSourceList(e.target.value); });
panel.addEventListener('click', e => {
  const insight = e.target.closest('[data-insight-ent]');
  if(insight && insight.dataset.insightEnt){
    loadNode(insight.dataset.insightEnt);
    return;
  }
  const read = e.target.closest('.srcread');
  if(read){ openReader(read.dataset.readDoc); setCenterView('reader'); return; }
  const t = e.target.closest('.srctitle'); if(!t) return;
  const body = document.getElementById(t.getAttribute('aria-controls')); if(!body) return;
  const open = body.hidden;
  body.hidden = !open;
  t.setAttribute('aria-expanded', open ? 'true' : 'false');
  const caret = t.querySelector('.srccaret'); if(caret) caret.textContent = open ? '▾' : '▸';
});
// The button of the detail panel: shows the entity list row of this node (관찰/주장 and 연결). It changes to the graph view, opens the list drawer on a small screen, opens the row, and scrolls to it.
function showNodeInList(id){
  if(activePane !== 'graph') revealWorkspace('graph');
  else if(centerView !== 'graph') setCenterView('graph');
  if((compactMQ.matches || mobileMQ.matches) && (drawerOpen || detailOpen)){
    // The detail drawer covers the list. It closes without a history step back, and its history entry stays as the entry below the list drawer.
    const st = window.history && window.history.state;
    drawerOpen = false; detailOpen = false; detailReturnFocus = null;
    syncWorkspaceLayout();
    if(st && st.modal === 'drawer') replaceAppHistory({modal: null});
  }
  if(mobileMQ.matches && !docDrawerOpen) openDocDrawer();
  if(currentNodeData && currentNodeData.id === id){
    // The panel already has the response of GET /node. There is no new request, and a response for a different node that is not complete must not write the panel.
    nodeLoadSeq++;
    openEntityRow(id);
    setEntityRowDetail(id, currentNodeData);
  } else {
    selectedNodeId = id;
    loadNode(id);
  }
  const r = entRowEl(id); if(r) r.querySelector('.entrow').focus({preventScroll: true});
}
window.showNodeInList = showNodeInList;
function renderPanel(d){
  if(!d || d.error){ currentNodeData = null; panel.innerHTML='<p class=hint>' + tr("ws.node.not_found") + '</p>'; return; }
  currentNodeData = d;
  const inSet = synthSet.has(d.id);
  // Provide a back link to document context when a document is open in reader view.
  const returnDocId = (centerView === 'reader') ? ((typeof curReaderDoc !== 'undefined' && curReaderDoc) || activeDoc) : null;
  const showBackLink = Boolean(returnDocId);
  let h = showBackLink ? ('<button type="button" class="backlink" onclick="loadDocPanel(' + jsArg(returnDocId) + ', true)" aria-label="' + tr("ws.node.back_to_doc_label") + '">' + tr("ws.node.back_to_doc") + '</button>') : '';
  // The node header (name, type, action buttons) stays in view while the panel scrolls.
  h+='<div class="nodehead">';
  h+='<h2>'+esc(d.name)+' <small>'+esc(d.type)+(d.provisional?' <span data-icon="warning"></span>provisional':'')+'</small></h2>';
  const isIsolated = (isolatedNodeId === d.id);
  const isoBtn = '<button type="button" class="sec node-isolate-btn '+(isIsolated?'active':'')+'" onclick="toggleNodeIsolation('+jsArg(d.id)+')" title="' + tr("ws.node.isolate_title") + '">'+(isIsolated?tr("ws.node.isolate_off"):'<span data-icon="search"></span> ' + tr("ws.node.isolate_on"))+'</button>';
  h+='<div class="node-actions-bar">';
  h+=isoBtn;
  if(canWrite()){
    h+='<button class="sec" onclick="addToSynth('+jsArg(d.id)+')">'+(inSet?tr("ws.node.in_synth"):'<span data-icon="plus"></span> ' + tr("ws.node.add_synth"))+'</button>';
    if(d.aliases && d.aliases.length){
      h+='<div class="node-manage-dropdown">';
      h+='<button type="button" class="sec node-manage-btn" id="node-manage-btn" aria-haspopup="true" aria-expanded="false" onclick="toggleNodeManageMenu(event)"><span data-icon="settings"></span> ' + tr("ws.node.manage") + '</button>';
      h+='<div class="node-manage-menu" id="node-manage-menu" style="display:none">';
      h+='<button type="button" class="node-manage-item" id="node-manage-alias-btn" onclick="startAliasSelectMode()"><span data-icon="tag"></span> ' + tr("ws.node.primary_alias") + '</button>';
      h+='</div>';
      h+='</div>';
    }
  }
  h+='</div></div>';
  // 관찰/주장 and 연결 are in the entity list row only. This button shows that row.
  const obsCount = (d.observations||[]).length, nbCount = (d.neighbors||[]).length;
  h+='<button type="button" class="sec node-listrow-btn" id="node-listrow-btn" onclick="showNodeInList('+jsArg(d.id)+')" title="' + tr("ws.node.show_in_list_title") + '">'+tr('ws.node.show_in_list', { count: obsCount+nbCount })+'</button>';
  if(d.aliases && d.aliases.length){
    h+='<div class="node-aliases-section" id="node-aliases-section">';
    h+='<div class="node-aliases-header">';
    h+='<span class="al-label" id="node-aliases-label">'+tr('ws.node.aliases', { count: d.aliases.length })+'</span>';
    h+='<button type="button" class="alias-cancel-btn" id="alias-cancel-btn" style="display:none" onclick="cancelAliasSelectMode()">' + tr("common.action.cancel") + '</button>';
    h+='</div>';
    h+='<div class="node-alias-chips" id="node-alias-chips">';
    d.aliases.forEach(al=>{
      h+='<span class="node-alias-chip"><span class="alias-text">'+esc(al)+'</span></span>';
    });
    h+='</div></div>';
  }
  // 맥락 조사 — 읽다가 더 알고 싶은 키워드/문장을 지금 맥락으로 조사해 그래프 확장.
  // readonly 는 /research 도 서버에서 막혀있어 입력창·버튼 자체를 안 그림.
  if(canWrite()) h+='<h3>' + tr("ws.research.more") + '</h3>'+
    '<div class=research><input id="rq" placeholder="' + tr("ws.research.placeholder") + '" '+
    'onkeydown="if(event.key===\'Enter\')doResearch()"/>'+
    '<button onclick="doResearch()">' + tr("ws.research.run") + '</button></div>'+
    '<p class=al>' + tr("ws.research.hint") + '</p>';
  // Source documents: the newest is first. Each row shows the title only; a click on the title shows the summary.
  const srcDocs = (d.documents||[]).slice().sort((x, y) => (y.fetched_at||0) - (x.fetched_at||0));
  if(srcDocs.length){
    h+='<h3>'+tr('ws.node.source_docs', { count: srcDocs.length })+'</h3>';
    if(srcDocs.length > LIST_PAGE) h+='<input id="srcfilter" class="srcfilter" type="search" placeholder="' + tr("ws.node.src_filter_placeholder") + '" aria-label="' + tr("ws.node.src_filter") + '" autocomplete="off"/>';
    h+='<div id="srclist" class="srclist" role="list"></div>';
  }
  panel.innerHTML=h;
  mountSourceList(srcDocs);
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
  if(!confirm(tr('ws.node.alias_promote_confirm', { alias: chosenAlias }))) return;
  try{
    const r = await fetch(apiUrl('entity/primary-label'), {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({id: entityId, name: chosenAlias})
    });
    if(r.status === 401 || r.status === 403){
      expireWriteAccess();
      alert(tr("ws.common.session_expired_or_denied"));
      return;
    }
    if(!r.ok){
      const err = await r.json().catch(() => ({}));
      alert(tr("ws.node.alias_failed") + ' ' + (err.detail || err.error || ('HTTP ' + r.status)));
      return;
    }
    const updated = await r.json();
    if(typeof allNodes !== 'undefined' && allNodes && allNodes.update){
      allNodes.update({id: entityId, label: updated.name});
    }
    renderPanel(updated);
  }catch(e){
    alert(tr("ws.common.request_error") + ' ' + e);
  }
}

// --- 맥락 조사: 조사(grounding)→판정 게이트→통과 시 그래프 적재(서버) ---
// 서버가 NDJSON 스트림으로 진행 이벤트({stage,msg})를 흘리고 마지막 줄이
// {done:true, result:{...}} — 마냥 기다리지 않고 단계·rate limit 상황을 실시간 표시(피드백).
async function doResearch(){
  if(!canWrite()) return;
  const q=((document.getElementById('rq')||{}).value||'').trim();
  if(!q){ alert(tr("ws.research.need_query")); return; }
  if(!selectedNodeId && !activeDoc){ alert(tr("ws.research.need_context")); return; }
  const backId=selectedNodeId;
  panel.innerHTML='<h2><span data-icon="search"></span> '+esc(tr('ws.research.title', { query: q }))+'</h2><p class="al" id="relapsed">'+tr('ws.common.starting')+'</p><ul id="rprog"></ul>';
  openDetailPane();
  const t0=Date.now();
  const timer=setInterval(()=>{ const el=document.getElementById('relapsed');
    if(el) el.textContent=tr('ws.common.elapsed', { seconds: Math.round((Date.now()-t0)/1000) }); else clearInterval(timer); },1000);
  let result=null;
  try{
    // fetch('research'
    const r=await fetch(apiUrl('research'),{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({query:q, node_id:selectedNodeId, doc_id:activeDoc})});
    if(r.status===401||r.status===404){ clearInterval(timer); expireWriteAccess();
      panel.innerHTML='<p class=hint>' + tr("ws.common.session_expired") + '</p>'; return; }
    if(!r.ok){ clearInterval(timer); let d={};
      try{ d=await r.json(); }catch(_){}
      panel.innerHTML='<p class=hint>' + tr("ws.research.failed") + ' '+esc(d.error||('HTTP '+r.status))+'</p>'; return; }
    if(!r.body) throw new Error(tr("ws.common.no_stream"));
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
          li.textContent=(ev.stage==='llm'?'':'')+msg;
          ul.appendChild(li); }
      }
    }
  }catch(e){ clearInterval(timer);
    panel.innerHTML='<p class=hint>' + tr("ws.common.request_failed") + ' '+esc(String(e))+'</p>'; return; }
  clearInterval(timer);
  if(!result){ panel.innerHTML='<p class=hint>' + tr("ws.common.response_cut") + '</p>'; return; }
  renderResearchResult(result, backId);
}
function renderResearchResult(d, backId){
  if(d.error){ panel.innerHTML='<p class=hint>' + tr("ws.common.error_prefix") + ' '+esc(d.error)+'</p>'; return; }
  let h='<h2><span data-icon="search"></span> '+esc(tr('ws.research.title', { query: d.query }))+(d.context_focus?' <small>'+esc(tr('ws.research.context', { focus: d.context_focus }))+'</small>':'')+'</h2>';
  h+='<p class=al>'+(d.added?'<span data-icon="check-circle"></span> ':'<span data-icon="alert"></span> ')+esc(d.verdict||'')+
    ' <span class=meter>'+tr('ws.research.relevance')+' '+(d.relevance!=null?(+d.relevance).toFixed(2):'-')+
    ' · '+tr('ws.research.quality')+' '+(d.quality!=null?(+d.quality).toFixed(2):'-')+'</span></p>';
  if(d.interpretation) h+='<p class=al>' + tr("ws.research.interpretation") + ' '+esc(d.interpretation)+'</p>';
  if(d.report) h+='<div class=synth>'+esc(d.report)+'</div>';
  if((d.sources||[]).length){ h+='<h3>' + tr("ws.research.sources") + '</h3><ul>'+d.sources.map(s=>
    '<li><a href="'+esc(s.url)+'" target=_blank>'+esc(s.title||s.url)+'</a></li>').join('')+'</ul>'; }
  if(d.added && d.ingest){
    h+='<p class=al>'+tr('ws.research.graph_result', { created: d.ingest.entities_created, linked: d.ingest.entities_linked, relations: d.ingest.relations_added })+'</p>';
    refreshGraph();
  } else if(d.reason){ h+='<p class=al>' + tr("ws.research.decision") + ' '+esc(d.reason)+'</p>'; }
  if(backId) h+='<p><a href="#" onclick="loadNode('+jsArg(backId)+');return false">' + tr("ws.research.back_to_node") + '</a></p>';
  panel.innerHTML=h;
}

// --- 웹 적재: URL/텍스트를 그래프에 적재(서버 /ingest-stream, /research 와 동일 NDJSON 스트리밍) ---
// 텔레그램 DM 과 같은 통로(svc.ingest, source='web') — 관련 링크 자동 확장도 동일하게 동작.
let activeIngest = null;

function updateIngestElapsed(){
  if(!activeIngest) return;
  const el = document.getElementById('ielapsed');
  if(el){
    const elapsedSec = Math.max(0, Math.round((Date.now() - activeIngest.t0) / 1000));
    const opts = activeIngest.optionLabels || [];
    el.textContent = tr('ws.common.elapsed', { seconds: elapsedSec }) + (opts.length ? ' (' + opts.join(' · ') + ')' : '');
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
    ? tr('ws.common.elapsed', { seconds: elapsedSec }) + (activeIngest.optionLabels && activeIngest.optionLabels.length ? ' (' + activeIngest.optionLabels.join(' · ') + ')' : '')
    : activeIngest.labelText;
  let h = '<div id="ingest-status-view">';
  h += '<h2><span data-icon="plus"></span> ' + tr("ws.ingest.state") + ' <small>' + tr("ws.ingest.in_progress") + '</small></h2>';
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

let latestIngestEffortSpec = null;

function applyEffortSpecFromStats(stats){
  if(!stats || !stats.ingest_effort) return;
  latestIngestEffortSpec = stats.ingest_effort;
  if(window.ClaireIngestDialogs && typeof window.ClaireIngestDialogs.setEffortSpec === 'function'){
    window.ClaireIngestDialogs.setEffortSpec(stats.ingest_effort);
  }
}

// The effort options come from the knowledge base of the page path (design Section 5.2): no parameter selects another one.
async function refreshIngestEffortSpec(){
  if(!canIngest()) return;
  try{
    const r = await fetch(apiUrl('stats'), { cache: 'no-store' });
    if(!r.ok) return;
    const d = await r.json();
    applyEffortSpecFromStats(d);
  }catch(_){}
}

function openIngest(){
  if(!canIngest()) return;
  if(activeIngest && activeIngest.running){
    renderIngestStatus();
    return;
  }
  // Ingest writes only to the personal knowledge base of the account (design Section 5.2 rule 3): the form has no knowledge base selector.
  if(window.ClaireIngestDialogs){
    window.ClaireIngestDialogs.openIngestDialog({ effortSpec: latestIngestEffortSpec });
    refreshIngestEffortSpec();
  }
}
async function runIngest(){
  if(!canIngest()) return;
  if(activeIngest && activeIngest.running){
    alert(tr("ws.ingest.busy"));
    renderIngestStatus();
    return;
  }
  const ta=document.getElementById('ingin');
  const payload=((ta||{}).value||'').trim();
  if(!payload){ alert(tr("ws.ingest.need_payload")); return; }
  if(window.ClaireIngestDialogs) window.ClaireIngestDialogs.closeDialog('modal-ingest');
  const amountChoice=document.querySelector('input[name="ingest-amount"]:checked');
  const effortChoice=document.querySelector('input[name="ingest-effort"]:checked');
  const fullContent=!!amountChoice && amountChoice.value==='full';
  const effort=effortChoice ? effortChoice.value : '';
  let labelText = tr("ws.common.starting");
  const optionLabels=[];
  if(fullContent) optionLabels.push(tr("ws.ingest.opt_full"));
  if(effort) optionLabels.push(tr('ws.ingest.opt_effort', { effort: effort }));
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
      initIngestMatrixView(payload, '');
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
    if(effort) bodyObj.effort=effort;
    const r=await fetch(apiUrl('ingest-stream'),{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify(bodyObj)});
    if(r.status===401||r.status===404){
      if(activeIngest){
        if(activeIngest.timer) clearInterval(activeIngest.timer);
        activeIngest.timer = null;
        activeIngest.running = false;
      }
      expireWriteAccess();
      panel.innerHTML='<p class=hint>' + tr("ws.common.session_expired") + '</p>'; return;
    }
    if(!r.ok){
      if(activeIngest){
        if(activeIngest.timer) clearInterval(activeIngest.timer);
        activeIngest.timer = null;
        activeIngest.running = false;
      }
      let d={};
      try{ d=await r.json(); }catch(_){}
      panel.innerHTML='<p class=hint>' + tr("ws.ingest.failed") + ' '+esc(d.error||d.detail||('HTTP '+r.status))+'</p>'; return;
    }
    if(!r.body) throw new Error(tr("ws.common.no_stream"));
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
    panel.innerHTML='<p class=hint>' + tr("ws.common.request_failed") + ' '+esc(String(e))+'</p>'; return;
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
  if(!result){ panel.innerHTML='<p class=hint>' + tr("ws.common.response_cut") + '</p>'; return; }
  renderIngestResult(result);
}
function renderIngestResult(d){
  if(d.error){ panel.innerHTML='<h2><span data-icon="plus"></span> ' + tr("ws.ingest.title_short") + '</h2><p class=hint>' + tr("ws.common.error_prefix") + ' '+esc(d.error)+'</p>'+
    '<p class=al>' + tr("ws.ingest.kept_for_recover") + '</p>'; return; }
  let h='<h2>'+(d.duplicate?'<span data-icon="merge"></span> ' + tr("ws.ingest.duplicate"):(d.updated?'<span data-icon="restore"></span> ' + tr("ws.ingest.updated"):'<span data-icon="check-circle"></span> ' + tr("ws.ingest.done")))+'</h2>';
  h+='<p class=al><b>'+esc(d.title||d.document_id||tr("home.recent.untitled"))+'</b>'+(d.partial?' <small><span data-icon="warning"></span> ' + tr("ws.ingest.partial") + '</small>':'')+'</p>';
  const appliedOptions=[];
  if(d.full_content) appliedOptions.push(tr("ws.ingest.opt_full"));
  if(d.effort) appliedOptions.push(esc(tr('ws.ingest.opt_effort', { effort: d.effort })));
  if(appliedOptions.length) h+='<p class=al><b>' + tr("ws.ingest.applied") + '</b> '+appliedOptions.join(' · ')+'</p>';
  if(!d.duplicate) h+='<p class=al>'+tr('ws.ingest.counts', { created: d.entities_created||0, linked: d.entities_linked||0, relations: d.relations_added||0 })+'</p>';
  if(d.summary) h+='<div class=synth>'+esc(d.summary)+'</div>';
  if(KB_PIN_RE.test(API_BASE)){
    // An ingest writes to the personal knowledge base of the account (design Section 5.2 rule 3). This page shows a shared knowledge base, thus the link goes to the personal one.
    h+='<p style="margin-top:12px"><a class="sec" href="'+esc(kbHomePath('0'))+'">'+esc(tr('ws.ingest.open_personal'))+'</a></p>';
  } else {
    if(d.document_id) h+='<p><a href="#" onclick="selectDoc('+jsArg(d.document_id)+');setCenterView(\'reader\');return false">' + tr("ws.ingest.open_doc") + '</a></p>';
    refreshGraph();
  }
  // Design Section 8.7: a document that looks like fiction shows with the action "Contribute to this theme".
  if(window.ClaireIngestDialogs && typeof window.ClaireIngestDialogs.ingestNoticesHtml === 'function') h+=window.ClaireIngestDialogs.ingestNoticesHtml(d);
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
  // Re-ingest acts on the knowledge base of the page path: the form has no knowledge base selector.
  if(window.ClaireIngestDialogs){
    window.ClaireIngestDialogs.openReingestDialog({
      effortSpec: latestIngestEffortSpec,
      target: defTarget,
      selectedDocLabel: defTitle ? (tr('ws.common.selected_doc') + ' <b>' + defTitle + '</b>') : ''
    });
    refreshIngestEffortSpec();
  }
}

async function runReingest(){
  if(!canIngest()) return;
  if(activeIngest && activeIngest.running){
    alert(tr("ws.reingest.busy"));
    renderIngestStatus();
    return;
  }
  const ta=document.getElementById('reingtarget');
  const target=((ta||{}).value||'').trim();
  if(!target){ alert(tr("ws.reingest.need_target")); return; }
  if(window.ClaireIngestDialogs) window.ClaireIngestDialogs.closeDialog('modal-reingest');
  const amountChoice=document.querySelector('input[name="reingest-amount"]:checked');
  const effortChoice=document.querySelector('input[name="reingest-effort"]:checked');
  const untombstoneChk=document.getElementById('reinguntombstone');
  const untombstone=!!(untombstoneChk && untombstoneChk.checked);
  const fullContent=!!amountChoice && amountChoice.value==='full';
  const effort=effortChoice ? effortChoice.value : '';
  let labelText = tr("ws.reingest.starting");
  const optionLabels=[];
  if(fullContent) optionLabels.push(tr("ws.reingest.opt_full"));
  if(effort) optionLabels.push(tr('ws.ingest.opt_effort', { effort: effort }));
  if(untombstone) optionLabels.push(tr("ws.reingest.opt_untombstone"));
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
      initIngestMatrixView(target, '');
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
    if(effort) bodyObj.effort=effort;
    const r=await fetch(apiUrl('reingest-stream'),{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify(bodyObj)});
    if(r.status===401||r.status===404){
      if(activeIngest){
        if(activeIngest.timer) clearInterval(activeIngest.timer);
        activeIngest.timer = null;
        activeIngest.running = false;
      }
      expireWriteAccess();
      panel.innerHTML='<p class=hint>' + tr("ws.common.session_expired") + '</p>'; return;
    }
    if(!r.ok){
      if(activeIngest){
        if(activeIngest.timer) clearInterval(activeIngest.timer);
        activeIngest.timer = null;
        activeIngest.running = false;
      }
      let d={};
      try{ d=await r.json(); }catch(_){}
      panel.innerHTML='<p class=hint>' + tr("ws.reingest.failed") + ' '+esc(d.error||d.detail||('HTTP '+r.status))+'</p>'; return;
    }
    if(!r.body) throw new Error(tr("ws.common.no_stream"));
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
    panel.innerHTML='<p class=hint>' + tr("ws.reingest.stream_error") + ' '+esc(String(e))+'</p>'; return;
  }
  if(activeIngest){
    if(activeIngest.timer) clearInterval(activeIngest.timer);
    activeIngest.timer = null;
    activeIngest.running = false;
  }
  resumeGraphAfterIngest();
  if(!result){
    panel.innerHTML='<p class=hint>' + tr("ws.reingest.no_result") + '</p>';
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
  // Regenerate acts on the knowledge base of the page path: the form has no knowledge base selector.
  if(window.ClaireIngestDialogs){
    window.ClaireIngestDialogs.openRegenDialog({
      effortSpec: latestIngestEffortSpec,
      target: defTarget,
      component: compOverride || 'all',
      selectedDocLabel: defTitle ? (tr('ws.common.selected_doc') + ' <b>' + defTitle + '</b>') : ''
    });
    refreshIngestEffortSpec();
  }
}

async function runRegen(isDryRun){
  if(!canIngest()) return;
  if(!isDryRun && activeIngest && activeIngest.running){
    alert(tr("ws.regen.busy"));
    renderIngestStatus();
    return;
  }
  const ta = document.getElementById('regentarget');
  const target = ((ta||{}).value||'').trim();
  if(!target){ alert(tr("ws.regen.need_target")); return; }
  const compChoice = document.querySelector('input[name="regen-comp"]:checked');
  const component = compChoice ? compChoice.value : 'all';
  const effortChoice = document.querySelector('input[name="regen-effort"]:checked');
  const effort = effortChoice ? effortChoice.value : '';
  const refetchChk = document.getElementById('regenrefetch');
  const refetch = !!(refetchChk && refetchChk.checked);

  const resDiv = document.getElementById('regenresult');

  if(isDryRun){
    if(resDiv) resDiv.innerHTML = '<p class="al"><span data-icon="clock"></span> ' + tr("ws.regen.analyzing") + '</p>';
    try{
      const payload = {
        target: target,
        component: component,
        dry_run: true,
        refetch: refetch,
      };
      if(effort) payload.effort = effort;
      const r = await fetch(apiUrl('regenerate'), {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(payload),
      });
      if(r.status === 401 || r.status === 404){
        expireWriteAccess();
        if(resDiv) resDiv.innerHTML = '<p class="hint">' + tr("ws.common.session_expired") + '</p>';
        return;
      }
      const d = await r.json();
      if(!r.ok || d.error){
        if(resDiv) resDiv.innerHTML = '<p class="hint">' + tr("ws.common.dry_run_failed") + ' ' + esc(d.error || d.detail || ('HTTP ' + r.status)) + '</p>';
        return;
      }
      let h = '<div style="padding:10px;background:var(--card-bg);border:1px solid var(--border);border-radius:6px">';
      h += '<h3 style="margin:0 0 6px"><span data-icon="search"></span> ' + tr("ws.regen.dry_title") + '</h3>';
      const tgts = d.targets || [];
      h += '<p class="al">' + tr('ws.regen.dry_counts', { count: d.count || tgts.length, component: esc(component) }) + '</p>';
      if(tgts.length > 0){
        h += '<ul style="margin:6px 0;padding-left:18px;font-size:12px">';
        tgts.forEach(t => {
          const acts = (t.actions || []).join(', ');
          h += '<li><b>' + esc(t.title || t.document_id) + '</b> <small class="al">(' + esc(t.document_id) + ')</small>';
          if(acts) h += '<br><span style="color:var(--muted)">' + esc(tr('ws.regen.planned', { actions: acts })) + '</span>';
          if(t.current_summary) h += '<br><span style="color:var(--muted)">' + tr('ws.regen.current_summary') + ' ' + esc(t.current_summary.slice(0, 100)) + (t.current_summary.length > 100 ? '…' : '') + '</span>';
          h += '</li>';
        });
        h += '</ul>';
        h += '<button type="button" class="readbtn" onclick="runRegen(false)" style="margin-top:8px;background:var(--accent,#2da44e);color:#fff;border:none;padding:5px 10px;border-radius:4px;cursor:pointer">' + tr("ws.regen.confirm_run") + '</button>';
      } else {
        h += '<p class="hint">' + tr("ws.common.no_matching_doc") + '</p>';
      }
      h += '</div>';
      if(resDiv) resDiv.innerHTML = h;
    }catch(e){
      if(resDiv) resDiv.innerHTML = '<p class="hint">' + tr("ws.common.analysis_error") + ' ' + esc(String(e)) + '</p>';
    }
    return;
  }

  // 실제 재생성 실행 (스트리밍)
  if(window.ClaireIngestDialogs) window.ClaireIngestDialogs.closeDialog('modal-regen');
  let labelText = tr("ws.regen.starting");
  const optionLabels = [];
  optionLabels.push(tr('ws.regen.opt_component', { component: component }));
  if(effort) optionLabels.push(tr('ws.ingest.opt_effort', { effort: effort }));
  if(refetch) optionLabels.push(tr("ws.regen.opt_refetch"));
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

  let result = null;
  try{
    const r = await fetch(apiUrl('regenerate-stream'), {
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
      panel.innerHTML = '<p class="hint">' + tr("ws.common.session_expired") + '</p>';
      return;
    }
    if(!r.ok){
      let d={};
      try{ d = await r.json(); }catch(_){}
      throw new Error(d.error || d.detail || ('HTTP ' + r.status));
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
    panel.innerHTML = '<p class="hint">' + tr("ws.regen.stream_error") + ' ' + esc(String(e)) + '</p>';
    return;
  }
  if(activeIngest){
    if(activeIngest.timer) clearInterval(activeIngest.timer);
    activeIngest.timer = null;
    activeIngest.running = false;
  }
  resumeGraphAfterIngest();
  if(!result){
    panel.innerHTML = '<p class="hint">' + tr("ws.regen.no_result") + '</p>';
    return;
  }

  // 결과 화면 표시 및 지식그래프 갱신
  if(result.error){
    panel.innerHTML = '<p class="hint">' + tr("ws.regen.failed") + ' ' + esc(result.error) + '</p>';
    return;
  }
  let h = '<h2><span data-icon="check-circle"></span> ' + tr("ws.regen.done") + '</h2>';
  const tgts = result.targets || [];
  h += '<div style="padding:10px;background:var(--card-bg);border:1px solid var(--border);border-radius:6px">';
  h += '<p class="al">' + tr('ws.regen.updated_docs', { count: result.count || tgts.length }) + '</p>';
  if(tgts.length > 0){
    h += '<ul style="margin:6px 0;padding-left:18px;font-size:12px">';
    tgts.forEach(t => {
      h += '<li><b>' + esc(t.title || t.document_id) + '</b> <small class="al">(' + esc(t.document_id) + ')</small>';
      if(t.entities_created !== undefined || t.entities_linked !== undefined){
        h += '<br><span style="color:var(--muted)">' + tr('ws.ingest.counts', { created: t.entities_created||0, linked: t.entities_linked||0, relations: t.relations_added||0 }) + '</span>';
      }
      if(t.new_summary){
        h += '<br><span style="color:var(--muted)">' + tr('ws.regen.new_summary') + ' ' + esc(t.new_summary.slice(0, 120)) + (t.new_summary.length > 120 ? '…' : '') + '</span>';
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
    alert(tr("ws.purge.owner_only"));
    return;
  }
  const host = document.getElementById('purge-modal-body') || panel;
  const dlg = document.getElementById('modal-purge');
  const curDoc = (allDocs && allDocs.find(d => d.id === activeDoc)) || curReaderDocData;
  const defTarget = (curDoc && (curDoc.id || curDoc.url || '')) || '';
  const defTitle = (curDoc && curDoc.title) ? esc(curDoc.title) : '';
  const isMulti = isMultiThemeEnabled();

  host.innerHTML = '<div style="margin:0 0 10px;padding:8px 10px;background:rgba(235,87,87,0.12);border:1px solid rgba(235,87,87,0.4);border-radius:6px;font-size:12px;color:var(--fg);line-height:1.45">'+
      '<span data-icon="warning"></span> <b>' + tr("ws.purge.warning_title") + '</b><br>' + tr("ws.purge.warning")+
    '</div>'+
    '<div class="ingest-form">'+
      '<div class="ingest-field">'+
        '<label class="ingest-label" for="purgetarget">' + tr("ws.purge.target") + ' <span class="ingest-help">' + tr("ws.purge.target_hint") + '</span></label>'+
        '<input type="text" id="purgetarget" class="ingest-input" value="'+esc(defTarget)+'" placeholder="' + tr("ws.purge.target_placeholder") + '">'+
        (defTitle ? '<small style="color:var(--muted);display:block;margin-top:3px">' + tr("ws.common.selected_doc") + ' <b>'+defTitle+'</b></small>' : '')+
      '</div>'+
      '<div class="ingest-field">'+
        '<label class="ingest-label" for="purgereason">' + tr("ws.purge.reason") + ' <span class="ingest-help">' + tr("ws.purge.reason_hint") + '</span></label>'+
        '<input type="text" id="purgereason" class="ingest-input" value="web_purge" placeholder="manual_purge">'+
      '</div>'+
      '<div class="ingest-field" style="margin-top:6px">'+
        '<label class="claire-dialog-check">'+
          '<input type="checkbox" id="purgetombstone" checked> '+
          '<span>' + tr("ws.purge.tombstone") + ' <small style="color:var(--muted)">' + tr("ws.purge.tombstone_hint") + '</small></span>'+
        '</label>'+
      '</div>'+
      '<div class="claire-dialog-foot" style="margin-top:12px">'+
        '<button type="button" data-close-dialog="modal-purge" class="sec">' + tr("common.action.cancel") + '</button>'+
        '<button type="button" class="sec" onclick="runPurgeCheck(true)"><span class="btn-icon"><span data-icon="search"></span></span> <span class="btn-label">' + tr("ws.common.dry_run") + '</span></button>'+
        '<button type="button" class="readbtn" onclick="runPurgeCheck(false)" style="background:#e03131;color:#fff;border:none"><span class="btn-icon"><span data-icon="flame"></span></span> <span class="btn-label">' + tr("ws.purge.run") + '</span></button>'+
      '</div>'+
      '<div id="purgeresult" class="claire-dialog-result" style="margin-top:10px"></div>'+
    '</div>';
  if(dlg){ if(!dlg.open) dlg.showModal(); } else openDetailPane();
  const inp=document.getElementById('purgetarget'); if(inp) inp.focus();
}

async function runPurgeCheck(isDryRun){
  if(AUTH_SCOPE !== 'owner'){
    alert(tr("ws.purge.owner_only"));
    return;
  }
  const isMulti = isMultiThemeEnabled();
  const targetInp = document.getElementById('purgetarget');
  const target = ((targetInp||{}).value||'').trim();
  if(!target){
    alert(tr("ws.purge.need_target"));
    return;
  }
  const reasonInp = document.getElementById('purgereason');
  const reason = ((reasonInp||{}).value||'').trim() || 'web_purge';
  const tombstoneChk = document.getElementById('purgetombstone');
  const tombstone = !!(tombstoneChk && tombstoneChk.checked);
  const targetThemeId = String(activeThemeId || '0');  // The page path selects the knowledge base (design Section 5.2).

  if(!isDryRun){
    if(!confirm(tr("ws.purge.confirm"))){
      return;
    }
  }

  const resDiv = document.getElementById('purgeresult');
  if(resDiv){
    resDiv.innerHTML = '<p class="al"><span data-icon="clock"></span> ' + tr(isDryRun ? 'ws.purge.analyzing' : 'ws.purge.running') + '</p>';
  }

  try {
    const payload = {
      target: target,
      dry_run: isDryRun,
      tombstone: tombstone,
      reason: reason,
    };
    const r = await fetch(apiUrl('document/purge'), {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload),
    });
    if(r.status === 401 || r.status === 404){
      expireWriteAccess();
      if(resDiv) resDiv.innerHTML = '<p class="hint">' + tr("ws.common.session_expired") + '</p>';
      return;
    }
    const d = await r.json();
    if(!r.ok || d.error){
      if(resDiv) resDiv.innerHTML = '<p class="hint">' + tr("ws.purge.failed") + ' ' + esc(d.error || ('HTTP ' + r.status)) + '</p>';
      return;
    }

    if(isDryRun){
      let h = '<div style="padding:10px;background:var(--card-bg);border:1px solid var(--border);border-radius:6px">';
      h += '<h3 style="margin:0 0 6px"><span data-icon="search"></span> ' + tr("ws.purge.dry_title") + '</h3>';
      const tgts = d.targets || [];
      h += '<p class="al">' + tr('ws.purge.dry_counts', { docs: tgts.length, files: d.disk_files_count || 0 }) + '</p>';
      h += '<p class="al">' + tr('ws.purge.tombstone_label') + ' <b>' + tr(tombstone ? 'ws.purge.tombstone_on' : 'ws.purge.tombstone_off') + '</b></p>';
      if(tgts.length > 0){
        h += '<ul style="margin:6px 0;padding-left:18px;font-size:12px">';
        tgts.forEach(t => {
          h += '<li><b>' + esc(t.title || t.id) + '</b> <small class="al">(' + esc(t.id) + ')</small></li>';
        });
        h += '</ul>';
        h += '<button type="button" class="readbtn" onclick="runPurgeCheck(false)" style="margin-top:8px;background:#e03131;color:#fff;border:none;padding:5px 10px;border-radius:4px;cursor:pointer">' + tr("ws.purge.confirm_run") + '</button>';
      } else {
        h += '<p class="hint">' + tr("ws.common.no_matching_doc") + '</p>';
      }
      h += '</div>';
      if(resDiv) resDiv.innerHTML = h;
    } else {
      let h = '<div style="padding:10px;background:var(--card-bg);border:1px solid var(--border);border-radius:6px">';
      h += '<h3 style="margin:0 0 6px"><span data-icon="check-circle"></span> ' + tr("ws.purge.done") + '</h3>';
      h += '<p class="al">' + tr('ws.purge.done_counts', { docs: d.deleted_documents || 0, files: d.disk_files_unlinked || 0 }) + '</p>';
      if(d.purged_count){
        h += '<p class="al">' + tr('ws.purge.tombstones', { count: d.purged_count }) + '</p>';
      }
      const gh = d.graph_healed || {};
      if(gh.ghost_entities_pruned || gh.dangling_relations_removed){
        h += '<p class="al">' + tr('ws.purge.graph_repair', { nodes: gh.ghost_entities_pruned || 0, relations: gh.dangling_relations_removed || 0 }) + '</p>';
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
    if(resDiv) resDiv.innerHTML = '<p class="hint">' + tr("ws.purge.request_error") + ' ' + esc(String(e)) + '</p>';
  }
}

// --- 중복 문서 정리: 근사중복 클러스터를 찾아(/dedup/scan) 유지문서를 골라 병합(/dedup/merge) ---
// 병합 직전 정본은 서버가 내부 checkpoint로 보존한다(파괴적 작업 안전장치).
// keeper(유지) 외 문서는 참조(엔티티/관계 sources 등)를 keeper 로 재배치한 뒤 삭제.
let dedupClusters=[];
function dedupTargetHost(){ return document.getElementById('dedup-modal-body') || panel; }
async function openDedup(){
  if(!canWrite()) return;
  const host = dedupTargetHost();
  const dlg = document.getElementById('modal-dedup');
  host.innerHTML='<p class="al">' + tr("ws.dedup.checking") + ' '+
    '<small>' + tr("ws.dedup.slow_hint") + '</small></p>';
  if(dlg){ if(!dlg.open) dlg.showModal(); } else openDetailPane();
  let d;
  try{
    const r=await fetch(apiUrl('dedup/scan'),{method:'POST'});
    if(r.status===401||r.status===404){ expireWriteAccess();
      host.innerHTML='<p class=hint>' + tr("ws.common.session_expired") + '</p>'; return; }
    d=await r.json();
  }catch(e){ host.innerHTML='<p class=hint>' + tr("ws.dedup.failed") + ' '+esc(String(e))+'</p>'; return; }
  renderDedup(d);
}
function renderDedup(d){
  if(!canWrite()) return;
  const host = dedupTargetHost();
  if(d.error){ host.innerHTML='<p class=hint>' + tr("ws.common.error_prefix") + ' '+esc(d.error)+'</p>'; return; }
  dedupClusters=d.clusters||[];
  let h='<p class=al>'+tr('ws.dedup.counts', { docs: d.documents||0, clusters: dedupClusters.length })+'</p>';
  if(!dedupClusters.length){ h+='<p class=al>' + tr("ws.dedup.none") + '</p>'; host.innerHTML=h; return; }
  h+='<p class=al><small>' + tr("ws.dedup.intro") + '</small></p>';
  dedupClusters.forEach((c,ci)=>{
    h+='<div class=doc><p class=al>'+tr('ws.dedup.similarity')+' '+(c.score!=null?(+c.score).toFixed(2):'-')+'</p>';
    c.docs.forEach(dc=>{
      h+='<label style="display:block;margin:.3em 0">'+
        '<input type=radio name="keep'+ci+'" value="'+esc(dc.id)+'"'+(dc.id===c.keeper?' checked':'')+'> '+
        '<b>'+esc(dc.title||tr('home.recent.untitled'))+'</b> <small class=al>'+tr('ws.dedup.chars', { count: dc.len||0 })+'</small>'+
        (isWebUrl(dc.url)?'<br><small class=al style="margin-left:1.5em;word-break:break-all">'+esc(dc.url)+'</small>':'')+
        '</label>';
    });
    h+='<button class=readbtn onclick="runDedupMerge('+ci+')">' + tr("ws.dedup.merge") + '</button></div>';
  });
  host.innerHTML=h;
}
async function runDedupMerge(ci){
  if(!canWrite()) return;
  const host = dedupTargetHost();
  const c=dedupClusters[ci]; if(!c) return;
  const sel=document.querySelector('input[name="keep'+ci+'"]:checked');
  const keeper=sel?sel.value:c.keeper;
  const losers=c.docs.map(x=>x.id).filter(id=>id!==keeper);
  if(!losers.length){ alert(tr("ws.dedup.nothing")); return; }
  if(!confirm(tr('ws.dedup.merge_confirm', { count: losers.length }))) return;
  host.innerHTML='<p class=al>' + tr("ws.dedup.merging_detail") + '</p>';
  try{
    const r=await fetch(apiUrl('dedup/merge'),{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({keeper:keeper, losers:losers})});
    if(r.status===401||r.status===404){ expireWriteAccess();
      host.innerHTML='<p class=hint>' + tr("ws.common.session_expired") + '</p>'; return; }
    const d=await r.json();
    if(!canWrite()) return;
    if(d.error){ host.innerHTML='<p class=hint>' + tr("ws.common.error_prefix") + ' '+esc(d.error)+'</p>'; return; }
    let h='<h3><span data-icon="check-circle"></span> ' + tr("ws.dedup.done") + '</h3>';
    h+='<p class=al>'+tr('ws.dedup.merged', { docs: d.deleted||0, entities: d.entities_repointed||0, relations: d.relations_repointed||0 })+'</p>';
    if(d.checkpoint) h+='<p class=al><small>'+esc(tr('ws.dedup.checkpoint', { path: d.checkpoint }))+'</small></p>';
    h+='<p><a href="#" onclick="openDedup();return false">' + tr("ws.dedup.again") + '</a></p>';
    host.innerHTML=h;
    refreshGraph();   // 문서목록·그래프(병합으로 줄어든 sources) 갱신
  }catch(e){ host.innerHTML='<p class=hint>' + tr("ws.common.request_failed") + ' '+esc(String(e))+'</p>'; }
}


// 조사로 그래프가 늘어난 뒤 새로고침 없이 신규 노드/엣지·문서목록을 반영.
// 엣지 id 는 rowid 순 enumerate(append-only)라 기존 id 는 안정 — 신규만 add.
function refreshGraph(){
  if(graphSuspended) return;
  // fetch('graph')
  fetch(apiUrl('graph')).then(r=>r.json()).then(d=>{
    if(graphSuspended) return;
    if(d && d.nodes && d.edges) applyTopologicalStructure(d.nodes, d.edges);
    const changed=[], added=[];
    d.nodes.forEach(n=>{
      const r = nodeRadius(n.degree), fs = nodeFontSize(n.degree);
      if(allNodes && allNodes.get(n.id))
        changed.push({id:n.id, degree:n.degree, size:r, font:{size:fs}, sources:n.sources, obs:n.obs, created_at:n.created_at, ...(n.physics===false?{orbit:n.orbit,physics:false}:{orbit:null,physics:true})});
      else if(allNodes)
        added.push({...n, size:r, font:{size:fs}});
    });
    if(allNodes && changed.length) allNodes.update(changed);
    if(allNodes && added.length) allNodes.add(added);
    if(allEdges) d.edges.forEach(e=>{ if(!allEdges.get(e.id)) allEdges.add(e); });
    homeMiniCacheKey = '';
    homeDecisionsCacheKey = '';
    const fslider = document.getElementById('fslider');
    if(fslider && d.stats) fslider.max = d.stats.max_degree;
    updateDegPresets();
    applyView();
    if(centerView === 'graph' && !selectedNodeId && typeof renderGraphInsights === 'function'){
      renderGraphInsights();
    } else if(centerView === 'home' && typeof renderHomeMap === 'function'){
      renderHomeMap();
    } else if(centerView === 'reader' && activeDoc && curReaderDocData && curReaderDocData.id === activeDoc){
      renderDocPanel(curReaderDocData);
    }
  });
  // fetch('documents'): only the first page and the pinned and hidden lists. The pages that the user loaded stay.
  refreshDocs();
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
    initIngestMatrixView(activeInfo.payload, activeInfo.theme_label);
    activeMatrixInitialized = true;
  }
  const spin = document.getElementById('matrix-progress-spinner');
  if(spin && activeInfo.active) spin.style.display = 'inline-block';
  if(activeInfo.title){
    const docMetaEl = document.getElementById('matrix-target-doc');
    if(docMetaEl) docMetaEl.textContent = tr("ws.cockpit.target_prefix") + ' ' + activeInfo.title;
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
    if(msgEl) msgEl.innerHTML = '<span data-icon="block"></span> <strong>' + esc(activeInfo.msg || tr("ws.cockpit.error")) + '</strong>';
  } else {
    if(msgEl) msgEl.innerHTML = '<span data-icon="check-circle"></span> <strong>' + tr("ws.cockpit.done") + '</strong>';
  }
  if(activeInfo && activeInfo.heatmap_matrix){
    renderHeatmapMatrix(activeInfo.heatmap_matrix, activeInfo.title, false);
  }
}

let lastStatsSig = null;
async function pollForUpdates(){
  try{
    const r = await fetch(apiUrl('stats'));
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
        if(msgEl && cockpitMsgInProgress(msgEl)){
          msgEl.innerHTML = '<span data-icon="check-circle"></span> <strong>' + tr("ws.cockpit.compare_done") + '</strong> ' + tr("ws.cockpit.compare_done_detail");
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
        fetch(apiUrl('document?id='+encodeURIComponent(activeDoc))).then(x=>x.json()).then(dc=>{
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
    rel = '<span class=lgsep>' + tr("ws.graph.relations_prefix") + '</span> ' + allRelTypes.map((t,i)=>{
      const on = !relFilter || relFilter.has(t);
      return '<button type=button class="reltog'+(on?'':' off')+'" onclick="toggleRel('+i+
        ')" aria-pressed="'+(on?'true':'false')+'" title="' + tr("ws.graph.relation_toggle") + '">'+esc(t)+'</button>';
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
// The filter state that applyView() and the entity list share: the path mode and the isolation set.
function nodeViewContext(){
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
  return {pathActive: !!(pathNodes && pathNodes.size), isolatedSet: isolatedSet};
}
// True when the graph hides this node: it is not in the isolation set, or its degree is below the #degctl threshold.
function isNodeHiddenByView(n, ctx){
  if(ctx.isolatedSet) return !ctx.isolatedSet.has(n.id);
  return n.degree < curMinDeg;
}
// True when the emphasis filters (path, selected document, search) include this node. The graph dims a node that does not match.
function isNodeMatchedByView(n, ctx){
  if(ctx.pathActive) return pathNodes.has(n.id);   // 경로 모드: 경로 노드만 강조(다른 필터보다 우선)
  let match = true;
  if(activeDoc) match = match && (n.sources||[]).includes(activeDoc);
  if(highlightSet) match = match && highlightSet.has(n.id);  // 검색(라벨/의미) 강조 집합
  return match;
}
// One predicate for the graph and the entity list: the graph shows this node and no filter dims it.
function isNodeListed(n, ctx){
  if(typeof n.id==='string' && n.id.indexOf('cl_')===0) return false;
  ctx = ctx || nodeViewContext();
  return !isNodeHiddenByView(n, ctx) && isNodeMatchedByView(n, ctx);
}
function applyView(){
  if(!allNodes) return;
  let shown=0, emph=0;
  const th=T();
  const netBg=(typeof getComputedStyle==='function'?getComputedStyle(document.documentElement).getPropertyValue('--net-bg').trim():'')||'#ffffff';
  const viewCtx = nodeViewContext();
  const pathActive = viewCtx.pathActive, isolatedSet = viewCtx.isolatedSet;
  const hasFilter = activeDoc || highlightSet || pathActive;

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
    if(isNodeHiddenByView(n, viewCtx)){ nodeUpdates.push({id:n.id, hidden:true}); return; }
    const match = isNodeMatchedByView(n, viewCtx);
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
  // The graph status is in span.graphstat: the document list hides it (it is not about the documents). A document status overwrites the span.
  document.getElementById('stat').innerHTML = '<span class="graphstat">' +
    tr('ws.graph.status_shown', { shown: shown, total: allNodes.length })
    + (isolatedSet ? ' · <span data-icon="search"></span> ' + tr('ws.graph.status_isolated', { count: isolatedSet.size }) : '')
    + (curMinDeg>0 && !isolatedSet ? ' · ' + tr('ws.graph.status_degree', { count: curMinDeg }) : '')
    + (relFilter ? ' · ' + tr('ws.graph.status_relations', { shown: relFilter.size, total: allRelTypes.length }) : '')
    + (pathActive ? ' · <span data-icon="link"></span>' + tr('ws.graph.status_path', { count: pathNodes.size }) : (hasFilter && !isolatedSet ? ' · ' + tr('ws.graph.status_emphasis', { count: emph }) : '')) + '</span>';
  renderEntityList();
  if(typeof renderHome === 'function' && centerView === 'home') renderHome();
}

// --- 2노드 경로 하이라이트(전용 모드): 경로 → 시작/끝 노드 클릭 → 최단경로(BFS) 강조 ---
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
  panel.innerHTML='<h2><span data-icon="link"></span> ' + tr("ws.path.title") + '</h2><p class=al>'+
    (n===0?tr('ws.path.click_start'):tr('ws.path.click_end')+' <small>('+esc(tr('ws.path.start_node', { label: nodeLabel(pathPicks[0]) }))+')</small>')+
    '</p><p class=al><small>' + tr("ws.path.filter_hint") + '</small></p>';
  setGraphNotice(n===0?tr("ws.path.select_start"):tr("ws.path.select_end"));
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
    panel.innerHTML='<h2><span data-icon="link"></span> '+tr('ws.menu.path')+'</h2><p class=hint>'+tr(relFilter ? 'ws.path.none_filtered' : 'ws.path.none')+'</p>'+
      '<p class=al><a href="#" onclick="restartPath();return false">' + tr("ws.path.again") + '</a></p>';
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
  panel.innerHTML='<h2><span data-icon="link"></span> '+tr('ws.menu.path')+' <small>'+tr('ws.path.steps', { count: order.length-1 })+'</small></h2>'+
    '<p class=al>'+order.map(id=>esc(nodeLabel(id))).join(' → ')+'</p>'+
    '<p class=al><a href="#" onclick="restartPath();return false">' + tr("ws.path.other") + '</a> · '+
    '<a href="#" onclick="clearPath();return false">' + tr("ws.path.clear") + '</a></p>';
  openDetailPane();
}
function restartPath(){ pathMode=true; pathPicks=[];
  const b=document.getElementById('pathbtn'); if(b) b.classList.add('on'); showPathHint(); }
function clearPath(){ pathMode=false; pathPicks=[]; pathNodes=null; pathEdges=null;
  const b=document.getElementById('pathbtn'); if(b) b.classList.remove('on');
  setGraphNotice(''); applyView(); panel.innerHTML=''; revealWorkspace('graph'); }

// --- 좌측 문서 패널(일자별 그룹) ---
function dayOf(ts){ if(!ts) return tr("ws.list.no_date");
  const d=new Date(ts*1000);
  return d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0')+'-'+String(d.getDate()).padStart(2,'0'); }
// 문서 하나의 목록 아이템 HTML — 즐겨찾기/일반/숨김 목록이 모두 공유(중복 방지).
// 클릭=문서 열기, 제목 좌측의 별표 단추는 stopPropagation으로 즐겨찾기 토글.
function docItemHtml(dc){
  const unread = dc.seen===0, watching = dc.watch===1, pinned = dc.pinned===1, hid = dc.hidden===1;
  const pinBtn = canWrite()
    ? '<button class="docpin-btn'+(pinned?' pinned':'')+'" title="'+(pinned?tr("ws.list.unpin"):tr("ws.list.pin"))+
      '" aria-label="'+(pinned?tr("ws.list.unpin"):tr("ws.list.pin"))+
      '" onclick="event.stopPropagation();togglePin('+jsArg(dc.id)+','+(!pinned)+')">'+'<span data-icon="star"'+(pinned?' class="filled"':'')+'></span>'+'</button>'
    : (pinned ? '<span class="docpin-icon pinned" title="' + tr("ws.list.pinned") + '"><span data-icon="star" class="filled"></span></span>' : '');
  return '<div class="docitem'+(dc.id===activeDoc?' active':'')+(unread?' unread':'')+(hid?' hidden-doc':'')+
    '" onclick="selectDoc('+jsArg(dc.id)+')">'+
    '<div class=doctitle-line>'+
    pinBtn+
    (watching?'<span class=wbadge title="' + tr("ws.list.watch") + '"><span data-icon="restore"></span></span>':'')+
    (unread?'<span class=ubadge title="' + tr("ws.list.unread") + '">●</span>':'')+
    '<b>'+esc(dc.title)+'</b><span class=st>'+esc(dc.source_type||'')+'</span>'+
    '</div>'+
    (dc.summary?'<p>'+esc(dc.summary.slice(0,110))+'</p>':'')+'</div>';
}

// --- Shared knowledge base bar (shared knowledge base design Section 10.3) ---
// The workspace of a shared knowledge base ("/kb/<number>/") shows the actions of the account on it: join or leave, direct contribution of a document of the personal knowledge base, fork, and the link to the review page. The buttons and the dialogs are those of kb_dialogs.js.
let sharedKbBarRequest = 0;
function sharedKbBarActive(){
  return KB_PIN_RE.test(API_BASE) && (AUTH_SCOPE === 'owner' || AUTH_SCOPE === 'write' || AUTH_SCOPE === 'readonly');
}
function loadSharedKbBar(){
  const bar = document.getElementById('shared-kb-bar');
  if(!bar) return;
  const request = ++sharedKbBarRequest;
  const dlgs = window.ClaireKbDialogs, ui = window.ClaireUi;
  if(!sharedKbBarActive() || !dlgs || !ui){ bar.hidden = true; bar.innerHTML = ''; return; }
  const number = API_BASE.match(KB_PIN_RE)[1];
  // GET /kb/{number}/ with "Accept: application/json": {"knowledge_base": {...}} with ``role`` and ``membership_state`` of the requester.
  ui.request(ROOT_BASE + 'kb/' + encodeURIComponent(number) + '/').then(res => {
    if(request !== sharedKbBarRequest) return;
    const kb = res.ok && res.data ? res.data.knowledge_base : null;
    if(!kb || kb.is_personal || !sharedKbBarActive()){ bar.hidden = true; bar.innerHTML = ''; return; }
    const membership = dlgs.membershipOf(kb);
    const stateTag = membership.role
      ? '<span class="claire-tag ok">' + esc(tr('kb.role.' + membership.role)) + '</span>'
      : (membership.state ? '<span class="claire-tag ' + (membership.state === 'banned' ? 'danger' : 'warn') + '">' + esc(tr('kb.member_state.' + membership.state)) + '</span>' : '');
    bar.innerHTML = '<div class="shared-kb-bar-head"><span class="claire-tag ' + (kb.knowledge_type === 'fictional' ? 'fic' : 'acc') + '">' + esc(tr('ws.kb.shared_kb_badge')) + '</span>' + stateTag + '</div>' +
      '<div class="claire-actions">' + dlgs.kbActionsHtml(kb, { isAdmin: IS_ADMIN, small: true }) + '</div>' + dlgs.kbMembershipNoteHtml(kb);
    bar.hidden = false;
    if(typeof ui.enhance === 'function') ui.enhance(bar);
    dlgs.bindKbActions(bar, kb, {
      homeHref: kbHomePath('0'),
      onChanged: function(){ loadSharedKbBar(); },
      onForked: function(fork){ if(fork && fork.number) window.location.assign(kbHomePath(String(fork.number))); }
    });
  });
}
let showHidden = false;
function toggleShowHidden(){ if(!canWrite()) return; showHidden=!showHidden; renderDocs(); }

function onDocqInput(v){
  docSearchActive = true;
  if(serverSearchResults && serverSearchResults.query !== (v||'').trim().toLowerCase()){
    serverSearchResults = null;
  }
  const qEl = document.getElementById('q');
  if(qEl && listMode() === 'docs' && qEl.value !== v){
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
  if(qEl && listMode() === 'docs'){
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

  if(docWindowList) docWindowList.invalidate();
  const dl = document.getElementById('doclist');
  if(dl){
    let html = doclistToolbarHtml();

    if(res.answer){
      html += '<div class="search-ai-card">' +
        '<div class="search-ai-head"><span><span data-icon="info"></span></span> <b>' + tr("ws.search.ai_summary") + '</b></div>' +
        '<div class="search-ai-body">' + esc(res.answer) + '</div>' +
        '</div>';
    }

    const hitCount = (res.hits || []).length;
    html += '<div class="search-action-bar">' +
      (hitCount > 0
        ? '<button type="button" class="sec search-graph-link-btn" onclick="highlightSearchInGraph()" title="' + tr("ws.search.highlight_title") + '">' +
          '<span class="btn-icon"><span data-icon="globe"></span></span> <span class="btn-label">' + tr('ws.search.show_in_graph', { count: hitCount }) + '</span></button>'
        : '') +
      '<button type="button" class="sec search-reset-btn" onclick="clearServerSearch()" title="' + tr("ws.search.reset_title") + '">' + tr("ws.common.close_x") + '</button>' +
      '</div>';

    if(res.docs && res.docs.length){
      html += res.docs.map(docItemHtml).join('');
    } else {
      html += '<p class="hint" style="padding:24px 12px;text-align:center">' + tr("ws.search.no_match") + '</p>';
    }
    dl.innerHTML = html;
  }

  const st = document.getElementById('stat');
  if(st){
    const modeLabel = res.mode === 'hybrid' ? tr("ws.search.semantic") : tr("ws.search.fulltext");
    st.textContent = tr('ws.search.found', { mode: modeLabel, count: res.docs ? res.docs.length : 0 });
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
    statEl.textContent = isSemantic ? tr("ws.search.ai_running") : tr("ws.search.fts_running");
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
    r = await fetch(apiUrl('search'), reqOpts);
  } catch(e){
    if(abortController && abortController.signal.aborted) return;
    if(seq !== currentSearchSeq) return;
    if(statEl) statEl.textContent = tr("ws.search.failed");
    return;
  }
  if(seq !== currentSearchSeq) return;
  if(r.status === 401 || r.status === 404){
    expireWriteAccess();
    if(statEl) statEl.textContent = tr("ws.common.session_expired_short");
    return;
  }
  if(r.status === 429){
    if(statEl) statEl.textContent = tr("ws.search.rate_limited_short");
    return;
  }
  let d = {};
  try { d = await r.json(); } catch(_) {}
  if(seq !== currentSearchSeq) return;
  if(!r.ok){
    if(statEl) statEl.textContent = tr('ws.search.http_error', { status: r.status });
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

  // A hit can name a document that is not in the loaded pages. One request gets these documents.
  await loadDocsById([...docScoreMap.keys()]);
  if(seq !== currentSearchSeq) return;

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

// --- 목록 영역(aside#docs): 보기별 목록 전환 + 31건 단위 추가 렌더링 ---
// Limits: docs/implementation/IMPLEMENTATION_STATUS.md, section 1.
const LIST_PAGE = 31;
// The entity list is ready (unit LP-3). The graph view shows it; all other views show the document list.
const ENTITY_LIST_READY = true;

// A list that renders LIST_PAGE items first and adds the next page when the sentinel at the end comes into view.
// opts.root: the scroll container, when it is not the container of the rows (default: the container).
// render(items, key, {head, empty}): the same key keeps the rendered count and the scroll position (background refresh); a new key starts from the first page.
// opts.more() and opts.loadMore() (optional, unit LP-5): the items are pages from the server. more() gives {hasMore, error, total}. When all items are rendered and hasMore is true, the sentinel stays and loadMore() gets the next page; the caller then calls render() with the same key. When error is true, a retry control replaces the sentinel, thus a failed request does not repeat.
function createWindowedList(opts){
  const container = opts.container, pageSize = opts.pageSize || LIST_PAGE, renderItem = opts.renderItem, onChange = opts.onChange || function(){};
  let items = [], count = 0, key = null, io = null;
  const hasIO = (typeof IntersectionObserver !== 'undefined');
  function body(from, to){ let h = ''; for(let i = from; i < to; i++) h += renderItem(items[i], i > 0 ? items[i-1] : null, i); return h; }
  function moreState(){ return opts.more ? (opts.more() || {}) : {}; }
  function total(){ const m = moreState(); return (typeof m.total === 'number') ? Math.max(m.total, items.length) : items.length; }
  function tail(){
    if(count < items.length) return '<div class="list-sentinel" aria-hidden="true"></div>';
    const m = moreState();
    if(m.error) return '<div class="list-more-error" role="status">' + tr("ws.list.next_failed") + ' <button type="button" class="sec list-retry">' + tr("ws.common.retry") + '</button></div>';
    if(m.hasMore) return '<div class="list-sentinel" aria-hidden="true"></div>';
    return items.length > pageSize ? '<div class="list-end">— ' + tr('ws.list.end', { count: items.length }) + ' —</div>' : '';
  }
  function onSentinel(){
    if(count < items.length) growTo(count + pageSize);
    else if(opts.loadMore && moreState().hasMore) opts.loadMore();
  }
  if(opts.loadMore) container.addEventListener('click', e => { if(e.target.closest('.list-retry')) opts.loadMore(); });
  function observe(){
    if(io){ io.disconnect(); io = null; }
    const s = container.querySelector('.list-sentinel');
    if(!s || !hasIO) return;
    // A new observer reports the current state at once. Thus the next page loads when the sentinel is still in view.
    io = new IntersectionObserver(es => { if(es.some(e => e.isIntersecting)) onSentinel(); }, {root: opts.root || container, rootMargin: '300px'});
    io.observe(s);
  }
  function growTo(n){
    const target = Math.min(items.length, n);
    const s = container.querySelector('.list-sentinel');
    if(target <= count || !s) return;
    const from = count; count = target;
    s.insertAdjacentHTML('beforebegin', body(from, count));
    if(count >= items.length) s.outerHTML = tail();
    observe();
    onChange(api);
  }
  const api = {
    render(newItems, newKey, o){
      o = o || {};
      const keep = (key !== null && newKey === key);
      // With opts.root, a different element scrolls (the container is a part of its content). Its scroll position stays.
      const top = keep ? container.scrollTop : 0;
      items = newItems || []; key = newKey;
      count = hasIO ? Math.min(items.length, keep ? Math.max(count, pageSize) : pageSize) : items.length;
      container.innerHTML = (o.head || '') + (items.length ? body(0, count) + tail() : (o.empty || ''));
      if(!opts.root) container.scrollTop = top;
      observe();
      onChange(api);
    },
    // Grows the window to the page that contains the item at this index.
    ensureVisible(index){ if(index >= count) growTo(Math.ceil((index + 1) / pageSize) * pageSize); },
    // Other code wrote the container. The next render starts from the first page.
    invalidate(){ if(io){ io.disconnect(); io = null; } key = null; items = []; count = 0; onChange(api); },
    get count(){ return count; },
    get total(){ return total(); },
    get active(){ return key !== null; }
  };
  return api;
}

// --- Document list data: keyset pages from GET /documents (unit LP-5) ---
// Contract: GET /documents in docs/implementation/API_HTTP_CONTRACT.md. Limits: docs/implementation/IMPLEMENTATION_STATUS.md, section 1.2.
// docById has one object for each document that the client knows. A pager has the documents of the list pages for one filter text, in the order of the server. allDocs is all known documents in that order; the code that reads allDocs sees the loaded pages, the pinned documents, the hidden documents, and the documents that a search or a deep link added.
const DOC_DEEP_LINK_LIMIT = 300, DOC_SEARCH_IDS_MAX = 100, DOC_FILTER_DELAY = 300;
let docFilterTimer = null;
const docById = new Map();
let docMainPager = newDocPager(''), docFilterPager = null, docListPager = null, docListTotal = 0, docScrollToActive = false, docPickSeq = 0;
// gen changes when the list of a pager starts again from a new first page. A page response for an earlier gen is ignored. pending is the promise of the load in flight.
function newDocPager(q){ return {q: q, items: [], next: null, total: 0, loaded: false, loading: false, error: false, gen: 0, pending: null}; }
function isLiveDocPager(p){ return p === docMainPager || p === docFilterPager; }
function resetDocStore(){
  docById.clear(); allDocs = [];
  docMainPager = newDocPager(''); docFilterPager = null; docListPager = null; docListTotal = 0;
  return docMainPager;
}
// The order of the server: fetched_at descending (no value is last), then id descending.
function docCmp(a, b){
  const fa = a.fetched_at, fb = b.fetched_at;
  if(fa !== fb){ if(fa == null) return 1; if(fb == null) return -1; return fb - fa; }
  return a.id < b.id ? 1 : (a.id > b.id ? -1 : 0);
}
function internDoc(d){
  const cur = docById.get(d.id);
  if(cur){ Object.assign(cur, d); return cur; }
  docById.set(d.id, d);
  return d;
}
function rebuildAllDocs(){ allDocs = [...docById.values()].sort(docCmp); if(centerView === 'home') renderHome(); }
// The list has the documents that are not hidden and not pinned. The pinned and hidden documents come from two other requests.
function docPagerParams(p){
  const sp = new URLSearchParams({limit: String(LIST_PAGE), hidden: '0', pinned: '0'});
  const kw = p.q.toLowerCase();
  if(kw === 'is:missing-detail' || kw === 'missing:detail') sp.set('detail', 'missing');
  else if(kw === 'is:mismatched' || kw === 'format:mismatch') sp.set('detail', 'mismatched');
  else if(p.q) sp.set('q', p.q);
  return sp;
}
async function fetchDocuments(sp){
  const r = await fetch(apiUrl('documents?' + sp.toString()));
  if(!r.ok) throw new Error('documents fetch failed: HTTP ' + r.status);
  return r.json();
}
function applyDocPage(p, d){
  const have = new Set(p.items.map(x => x.id));
  ((d && d.documents) || []).forEach(x => { if(!have.has(x.id)) p.items.push(internDoc(x)); });
  p.next = (d && d.next_before) || null;
  p.total = (d && typeof d.total === 'number') ? d.total : p.items.length;
  p.loaded = true; p.error = false;
}
// Loads the next page of a pager. A response for a pager that is not in use (the filter text or the knowledge theme changed) is ignored.
// The response of an earlier gen is ignored: a background refresh started the list again while the request was in flight.
function loadDocPage(p){
  if(p.loading || (p.loaded && p.next == null)) return p.pending || Promise.resolve();
  p.loading = true; p.error = false;
  const gen = p.gen;
  p.pending = (async () => {
    try{
      const sp = docPagerParams(p);
      if(p.loaded && p.next) sp.set('before', p.next);
      const d = await fetchDocuments(sp);
      if(isLiveDocPager(p) && gen === p.gen) applyDocPage(p, d);
    }catch(e){
      if(gen === p.gen) p.error = true;
    }
    p.loading = false; p.pending = null;
    if(isLiveDocPager(p)){ rebuildAllDocs(); renderDocs(); }
  })();
  return p.pending;
}
// The pinned documents and the hidden documents (the server gives no hidden document to an anonymous session). A maximum of 300 each.
// seq: the docRefreshSeq of the refresh that calls this function. A response that comes after a newer refresh started does not change the data.
async function loadDocSideLists(seq){
  const main = docMainPager;
  const res = await Promise.allSettled([fetchDocuments(new URLSearchParams({pinned: '1'})), fetchDocuments(new URLSearchParams({hidden: '1'}))]);
  if(main !== docMainPager || (seq !== undefined && seq !== docRefreshSeq)) return;
  ['pinned', 'hidden'].forEach((flag, i) => {
    if(res[i].status !== 'fulfilled') return;
    const fresh = new Set();
    (res[i].value.documents || []).forEach(x => { fresh.add(x.id); internDoc(x); });
    // A document that lost the flag on the server.
    docById.forEach(x => { if(x[flag] === 1 && !fresh.has(x.id)) x[flag] = 0; });
  });
}
// Background refresh: gets the first page again and merges it by id. The later pages that the user loaded stay as they are; a change in them shows after a reload.
async function refreshDocPagerHead(p, seq){
  const d = await fetchDocuments(docPagerParams(p));
  if(!isLiveDocPager(p) || seq !== docRefreshSeq) return;
  const fresh = (d.documents || []).map(internDoc), last = fresh[fresh.length - 1];
  const freshIds = new Set(fresh.map(x => x.id)), had = new Set(p.items.map(x => x.id));
  if(d.next_before && last && had.has(last.id)){
    // The loaded documents continue after the new first page. A loaded document in the range of the first page that the server did not send is deleted, pinned, or hidden.
    if(p === docMainPager) p.items.forEach(x => { if(!freshIds.has(x.id) && docCmp(x, last) <= 0 && x.pinned !== 1 && x.hidden !== 1) docById.delete(x.id); });
    p.items = fresh.concat(p.items.filter(x => !freshIds.has(x.id) && docCmp(x, last) > 0));
    if(p.items.length === fresh.length && !p.loading) p.next = d.next_before;
  } else {
    // The first page is the full list, or the loaded documents do not join the new first page (more than one page of new documents). The list starts again from this page. A page request in flight is for the old list: the new gen makes loadDocPage() ignore its response.
    if(p === docMainPager){
      const keep = new Set(docFilterPager ? docFilterPager.items.map(x => x.id) : []);
      p.items.forEach(x => { if(!freshIds.has(x.id) && !keep.has(x.id) && x.pinned !== 1 && x.hidden !== 1) docById.delete(x.id); });
    }
    p.gen++;
    p.items = fresh; p.next = d.next_before || null;
  }
  p.total = (typeof d.total === 'number') ? d.total : p.items.length;
  p.error = false;
}
let docRefreshSeq = 0;
async function refreshDocs(){
  const main = docMainPager, seq = ++docRefreshSeq;
  // The first page did not load (an error): the background refresh sends the request again.
  if(!main.loaded){ if(main.error && !main.loading) loadDocPage(main); return; }
  const jobs = [refreshDocPagerHead(main, seq), loadDocSideLists(seq)];
  if(docFilterPager && docFilterPager.loaded) jobs.push(refreshDocPagerHead(docFilterPager, seq));
  await Promise.allSettled(jobs);
  if(main !== docMainPager || seq !== docRefreshSeq) return;
  rebuildAllDocs();
  const docq = document.getElementById('docq');
  renderDocs(docq ? docq.value : '');
}
// Makes a document known to the client and, when it is in the list order after the loaded pages, loads the pages up to it (one request, a maximum of DOC_DEEP_LINK_LIMIT documents). False when the server does not give the document to this session.
async function ensureDocLoaded(id){
  const main = docMainPager;
  if(main.items.some(x => x.id === id)) return true;
  let doc = docById.get(id) || null;
  if(!doc){
    const d = await fetchDocuments(new URLSearchParams([['id', id]]));
    if(main !== docMainPager) return false;
    doc = (d.documents && d.documents[0]) ? internDoc(d.documents[0]) : null;
  }
  if(!doc) return false;
  // A scroll load in flight uses the same cursor: wait for it, then hold the pager so that no scroll load starts during this request.
  while(main.pending) await main.pending;
  if(main !== docMainPager) return false;
  if(main.items.some(x => x.id === id)){ rebuildAllDocs(); return true; }
  if(doc.pinned !== 1 && doc.hidden !== 1 && main.loaded && main.next){
    const sp = docPagerParams(main);
    sp.set('limit', String(DOC_DEEP_LINK_LIMIT)); sp.set('before', main.next);
    const gen = main.gen;
    main.loading = true;
    try{
      const d = await fetchDocuments(sp);
      if(main !== docMainPager) return false;
      if(gen === main.gen) applyDocPage(main, d);
    }catch(_){
    }finally{ main.loading = false; }
  }
  rebuildAllDocs();
  return true;
}
// Gets the documents with these identifiers that the client does not know (search results). A maximum of DOC_SEARCH_IDS_MAX identifiers in one request.
async function loadDocsById(ids){
  const main = docMainPager, missing = [...new Set(ids)].filter(id => id && !docById.has(id)).slice(0, DOC_SEARCH_IDS_MAX);
  if(!missing.length) return;
  try{
    const d = await fetchDocuments(new URLSearchParams(missing.map(id => ['id', id])));
    if(main !== docMainPager) return;
    (d.documents || []).forEach(internDoc);
    rebuildAllDocs();
  }catch(_){}
}
// The pager of the list for this filter text. A new filter text makes a new pager; a response for the old pager is ignored.
function docPagerFor(q){
  if(!q) return docMainPager;
  if(!docFilterPager || docFilterPager.q !== q) docFilterPager = newDocPager(q);
  return docFilterPager;
}
// A pin or hide change in the client. A document that comes back to the list goes to its position when that position is in the loaded pages.
function docListPlace(d){
  const p = docMainPager;
  if(!d || d.pinned === 1 || d.hidden === 1 || !p.loaded || p.items.includes(d)) return;
  const lastItem = p.items[p.items.length - 1];
  if(p.next != null && lastItem && docCmp(d, lastItem) > 0) return;
  p.items.push(d); p.items.sort(docCmp); p.total += 1;
}

let docWindowList = null;
function docWindow(){
  if(docWindowList) return docWindowList;
  const dl = document.getElementById('doclist');
  if(!dl) return null;
  docWindowList = createWindowedList({
    container: dl,
    renderItem: (dc, prev) => {
      const day = dayOf(dc.fetched_at);
      return ((!prev || dayOf(prev.fetched_at) !== day) ? '<div class=dday>' + day + '</div>' : '') + docItemHtml(dc);
    },
    more: () => { const p = docListPager; return p ? {hasMore: p.next != null, error: p.error, total: docListTotal} : {}; },
    loadMore: () => { if(docListPager) loadDocPage(docListPager); },
    onChange: updateListTitle
  });
  dl.addEventListener('scroll', syncListTopBtn, {passive: true});
  return docWindowList;
}
function listMode(){
  if(!ENTITY_LIST_READY) return 'docs';
  return listOverride || (centerView === 'graph' ? 'entities' : 'docs');
}
function updateListTitle(){
  const el = document.getElementById('listtitle'); if(!el) return;
  const ent = listMode() === 'entities', w = ent ? entWindowList : docWindowList, name = ent ? tr("list.entities") : tr("list.documents");
  // The row with the mark 필터 밖 is not a matching entity, thus the two numbers do not include it.
  const extra = (ent && entOutId != null) ? 1 : 0;
  el.innerHTML = (w && w.active && w.total) ? name + ' <b>' + (w.count - extra) + '</b> / ' + (w.total - extra) : name;
}
// Sets the visible list for the center view. setCenterView() calls this function.
function syncListPane(){
  const docsEl = document.getElementById('docs'); if(!docsEl) return;
  const mode = listMode();
  if(lastListMode && lastListMode !== mode) resetListFilter(lastListMode);
  lastListMode = mode;
  docsEl.dataset.list = mode;
  docsEl.setAttribute('aria-label', mode === 'entities' ? tr("ws.list.entities_label") : tr("ws.list.docs_label"));
  document.querySelectorAll('#listswitch .list-switch-btn').forEach(b => b.setAttribute('aria-pressed', b.dataset.list === mode ? 'true' : 'false'));
  if(mode === 'entities') renderEntityList();
  else if(net && typeof net.setHoverNode === 'function') net.setHoverNode(null);
  updateListTitle();
  syncListTopBtn();
  if(typeof updateCenterSearchMode === 'function') updateCenterSearchMode();
}
// The list filter belongs to one list. A change of the list removes the text and the effect of the filter on the list before.
function resetListFilter(prevMode){
  const q = document.getElementById('q');
  if(!q || !q.value) return;
  q.value = '';
  const dq = document.getElementById('docq'); if(dq) dq.value = '';
  if(prevMode === 'entities'){
    if(typeof onSearchInput === 'function') onSearchInput('');
  } else {
    docSearchActive = false;
    serverSearchResults = null;
    if(typeof cancelServerSearch === 'function') cancelServerSearch();
    renderDocs('');
  }
}
// A search in the document bodies from the command palette. The result shows in the document list.
function runFullTextSearch(query){
  const v = String(query || '').trim(); if(!v) return;
  if(listMode() !== 'docs') setListOverride('docs');
  if(mobileMQ.matches){ if(typeof openDocDrawer === 'function') openDocDrawer(); }
  else if(!listPaneOpen){ toggleListPane(); }
  const q = document.getElementById('q'); if(q) q.value = v;
  const dq = document.getElementById('docq'); if(dq) dq.value = v;
  updateCenterSearchMode();
  doDocServerSearch(v);
}
// The scroll container of the visible list.
function listScroller(){ return document.getElementById(listMode() === 'entities' ? 'entlist' : 'doclist'); }
function syncListTopBtn(){
  const btn = document.getElementById('listtopbtn'), dl = listScroller();
  if(!btn || !dl) return;
  btn.hidden = !(dl.scrollTop > dl.clientHeight * 2);
}
function scrollListTop(){
  const dl = listScroller(); if(!dl) return;
  dl.scrollTo({top: 0, behavior: reducedMotionMQ.matches ? 'auto' : 'smooth'});
  dl.focus && dl.focus({preventScroll: true});
}
window.syncListPane = syncListPane;

// --- 엔티티 목록(그래프 보기): GET /graph 의 노드 데이터로 만들고, 그래프와 같은 필터를 쓴다 ---
// Limits: docs/implementation/IMPLEMENTATION_STATUS.md, section 1.3.
const ENT_OBS_PREVIEW = 5, ENT_REL_GROUP_OPEN_MAX = 10;
let entWindowList = null, entSort = 'degree', entOpenId = null, entDetail = null, entObsAll = false, entLastSig = null, entMaxDeg = 1, entOutId = null, entScrollToId = null, entLastDetail = null, entChipsHtml = null;
// The open or closed state that the user set for each 연결 group of the open row (relation type → boolean). It is empty after the open row changes.
// The click handler of the list writes it at the time of the click on the summary (a key press on the summary also sends a click). The toggle event of a details element is not used: it comes in a later task, and a render before that task removes the element, thus the listener on the list never gets the event. The browser also sends a toggle event for each open details element that a render inserts.
const entRelOpen = new Map();
function entWindow(){
  if(entWindowList) return entWindowList;
  const el = document.getElementById('entlist');
  if(!el) return null;
  entWindowList = createWindowedList({container: el, renderItem: entItemHtml, onChange: updateListTitle});
  el.addEventListener('scroll', syncListTopBtn, {passive: true});
  el.addEventListener('click', onEntListClick);
  el.addEventListener('keydown', onEntListKeydown);
  // Only a mouse pointer sets the hover state. A tap on a touch screen must not leave the highlight on.
  el.addEventListener('pointerover', e => { if(e.pointerType !== 'mouse') return; const it = e.target.closest('.entitem'); hoverEntityNode(it ? it.dataset.entId : null); });
  el.addEventListener('pointerleave', e => { if(e.pointerType !== 'mouse') return; hoverEntityNode(null); });
  return entWindowList;
}
// Pointer on a row: the graph engine shows its hover state for the node. The camera does not move.
function hoverEntityNode(id){ if(net && typeof net.setHoverNode === 'function') net.setHoverNode(id); }
function entCmp(a, b){
  if(entSort !== 'name' && b.degree !== a.degree) return b.degree - a.degree;
  return String(a.label).localeCompare(String(b.label));
}
function setEntitySort(mode){
  entSort = (mode === 'name') ? 'name' : 'degree';
  document.querySelectorAll('.entsort-btn').forEach(b => { const on = b.dataset.sort === entSort; b.classList.toggle('active', on); b.setAttribute('aria-pressed', on ? 'true' : 'false'); });
  renderEntityList();
}
function entItemHtml(n){
  const open = (n.id === entOpenId), out = (n.id === entOutId), deg = n.degree || 0;
  const c = TYPE_COLORS[n.group] || '#8b949e';
  let h = '<div class="entitem' + (open ? ' open' : '') + ((open || n.id === selectedNodeId) ? ' active' : '') + '" role="listitem" data-ent-id="' + esc(n.id) + '" onmouseenter="setEntityHoverHighlight(' + jsArg(n.id) + ')" onmouseleave="clearEntityHoverHighlight()">';
  h += '<button type="button" class="entrow" aria-expanded="' + (open ? 'true' : 'false') + '"' + (open ? ' aria-controls="entbody"' : '') + '>';
  h += '<span class="entline"><i class="entdot" style="background:' + c + '"></i><span class="entname">' + esc(n.label) + '</span>';
  if(out) h += '<span class="entout" title="' + tr("ws.ent.outside_title") + '">' + tr("ws.ent.outside") + '</span>';
  h += '<small class="enttype">' + esc(n.group || '') + '</small><span class="entdeg" title="' + esc(tr('ws.node.connections', { count: deg })) + '">' + deg + '</span></span>';
  h += '<span class="entbar" aria-hidden="true"><i style="width:' + Math.round(100 * deg / entMaxDeg) + '%"></i></span></button>';
  if(open) h += '<div id="entbody" class="entbody" role="region" aria-label="' + esc(tr('ws.ent.body_label', { label: n.label })) + '">' + entBodyHtml(n) + '</div>';
  return h + '</div>';
}
function entBodyHtml(n){
  const d = entDetail;
  let h = '<button type="button" class="sec entgo" data-ent-act="go">' + tr("ws.ent.show_in_graph") + '</button>';
  if(!d) return h + '<p class="hint">' + tr("ws.list.loading") + '</p>';
  if(d.error) return h + '<p class="hint">' + (d.failed ? tr("ws.ent.load_failed") : tr("ws.node.not_found")) + '</p>';
  const obs = d.observations || [], nb = d.neighbors || [];
  h += '<h4>' + tr("ws.ent.observations") + '</h4>';
  if(!obs.length) h += '<p class="hint">' + tr("ws.ent.no_observations") + '</p>';
  else {
    const shownObs = entObsAll ? obs : obs.slice(0, ENT_OBS_PREVIEW);
    h += '<ul class="entobs">' + shownObs.map(o => '<li>' + esc(o) + '</li>').join('') + '</ul>';
    if(obs.length > shownObs.length) h += '<button type="button" class="entmore" data-ent-act="more">' + tr('ws.ent.more', { count: obs.length - shownObs.length }) + '</button>';
  }
  h += '<h4>' + tr('ws.ent.connections', { count: nb.length }) + '</h4>';
  if(!nb.length) return h + '<p class="hint">' + tr("ws.ent.no_connections") + '</p>';
  // Groups by relation type. The group with the most items is first.
  const groups = new Map();
  nb.forEach(x => { if(!groups.has(x.rel)) groups.set(x.rel, []); groups.get(x.rel).push(x); });
  [...groups.entries()].sort((a, b) => b[1].length - a[1].length || String(a[0]).localeCompare(String(b[0]))).forEach(g => {
    const relOpen = entRelOpen.has(String(g[0])) ? entRelOpen.get(String(g[0])) : g[1].length <= ENT_REL_GROUP_OPEN_MAX;
    h += '<details class="entrel" data-rel="' + esc(g[0]) + '"' + (relOpen ? ' open' : '') + '><summary><span class="rel">' + esc(g[0]) + '</span> <span class="entrel-count">' + g[1].length + '</span></summary><ul>';
    h += g[1].map(x => '<li>' + (x.dir == 'out' ? '→' : '←') + ' <a href="#" data-ent-go="' + esc(x.id) + '">' + esc(x.name) + '</a> <small>' + esc(x.type) + '</small></li>').join('') + '</ul></details>';
  });
  return h;
}
function entRowEl(id){
  const el = document.getElementById('entlist'); if(!el || id == null) return null;
  return [...el.querySelectorAll('.entitem')].find(x => x.dataset.entId === String(id)) || null;
}
// The filters that make the list shorter, as chips with one reset control.
function renderEntityChips(ctx){
  const box = document.getElementById('entchips'); if(!box) return;
  const chips = [];
  if(ctx.isolatedSet) chips.push(tr("ws.node.isolate_on"));
  else if(curMinDeg > 0) chips.push(tr('ws.ent.chip_degree', { count: curMinDeg }));
  if(ctx.pathActive) chips.push(tr('ws.graph.status_path', { count: pathNodes.size }));
  else {
    if(activeDoc){ const dc = allDocs.find(x => x.id === activeDoc); chips.push(tr('ws.ent.chip_doc', { title: (dc && dc.title) || activeDoc })); }
    if(highlightSet) chips.push(tr('ws.ent.chip_search', { count: highlightSet.size }));
  }
  const html = chips.length ? chips.map(t => '<span class="entchip" title="' + esc(t) + '">' + esc(t) + '</span>').join('') + '<button type="button" class="entchip-reset" onclick="resetEntityFilters()" title="' + tr("ws.ent.reset_title") + '" aria-label="' + tr("ws.ent.reset_title") + '">' + tr("ws.ent.reset") + '</button>' : '';
  // The container is a live region. It is written only when its content changes.
  if(html === entChipsHtml) return;
  entChipsHtml = html;
  box.hidden = !chips.length;
  box.innerHTML = html;
}
// Removes the filters that the chips show. The selected node and the open row stay.
function resetEntityFilters(){
  const box = document.getElementById('entchips');
  const hadFocus = !!(box && box.contains(document.activeElement));
  // A search request or a search timer that is not complete must not set the filter again after the reset.
  cancelServerSearch(); currentSearchSeq++; clearTimeout(searchDebounce);
  const wasPath = pathMode, wasIsolated = !!isolatedNodeId;
  if(wasPath) clearPath();
  isolatedNodeId = null;
  const q = document.getElementById('q'); if(q) q.value = '';
  const qclear = document.getElementById('qclear'); if(qclear) qclear.style.display = 'none';
  if(highlightSet){ highlightSet = null; unclusterEdges(); }
  if(activeDoc){ activeDoc = null; renderDocs(); syncGraphDocNav(); }
  setDeg(0);
  restoreSelection();
  // clearPath() empties the detail panel, and the isolation button shows the old state. The selected node stays, thus the panel shows it again.
  if(wasPath || wasIsolated){
    if(selectedNodeId != null && !(currentNodeData && currentNodeData.id === selectedNodeId)) loadNode(selectedNodeId, true, false);
    else if(currentNodeData && (selectedNodeId != null || !wasPath)) renderPanel(currentNodeData);
    else if(wasPath) panel.innerHTML = defaultHint();
  }
  // The reset button is removed with the chips. The focus goes to the sort control, not to the document body.
  if(hadFocus && !(box && box.contains(document.activeElement))){ const b = document.querySelector('.entsort-btn.active'); if(b) b.focus({preventScroll: true}); }
}
// Renders the entity list. applyView() calls this function, thus each filter change comes here. A new filter or sort starts from the first page.
function renderEntityList(){
  if(listMode() !== 'entities' || !allNodes) return;
  const w = entWindow(); if(!w) return;
  const ctx = nodeViewContext();
  if(entOpenId != null && !allNodes.get(entOpenId)){ entOpenId = null; entDetail = null; }
  const items = [];
  let maxDeg = 1;
  allNodes.forEach(n => {
    if(typeof n.id==='string' && n.id.indexOf('cl_')===0) return;
    if(n.degree > maxDeg) maxDeg = n.degree;
    if(isNodeListed(n, ctx)) items.push(n);
  });
  entMaxDeg = maxDeg;
  items.sort(entCmp);
  // The selected entity always has a row. When the filter excludes it, its row is first.
  const pinId = (entOpenId != null) ? entOpenId : selectedNodeId;
  const pinNode = (pinId != null) ? allNodes.get(pinId) : null;
  entOutId = (pinNode && !isNodeListed(pinNode, ctx)) ? pinId : null;
  if(entOutId != null) items.unshift(pinNode);
  renderEntityChips(ctx);
  const key = ['ent', entSort, curMinDeg, activeDoc || '', highlightSet ? [...highlightSet].join(',') : '', ctx.isolatedSet ? isolatedNodeId : '', ctx.pathActive ? [...pathNodes].join(',') : ''].join('|');
  const sig = key + '#' + items.map(n => n.id + ':' + n.degree + ':' + n.label + ':' + n.group).join(';') + '#' + [entOutId, entOpenId, selectedNodeId, entObsAll].join('|');
  const scrollId = entScrollToId; entScrollToId = null;
  const el = document.getElementById('entlist');
  if(sig !== entLastSig || entDetail !== entLastDetail){
    entLastSig = sig; entLastDetail = entDetail;
    // A render writes the rows again. Thus the keyboard focus goes back to the same row.
    const focusItem = (document.activeElement && el.contains(document.activeElement)) ? document.activeElement.closest('.entitem') : null;
    const focusId = focusItem ? focusItem.dataset.entId : null;
    w.render(items, key, {empty: '<p class="hint" style="padding:10px">' + (allNodes.length ? tr("ws.ent.none_filtered") : tr("ws.ent.none")) + '</p>'});
    // The open row stays in the rendered window after a sort or a filter change. The list does not scroll to it.
    if(entOpenId != null){ const oi = items.findIndex(n => n.id === entOpenId); if(oi >= 0) w.ensureVisible(oi); }
    if(focusId != null && !el.contains(document.activeElement)){ const r = entRowEl(focusId); if(r) r.querySelector('.entrow').focus({preventScroll: true}); }
    syncListTopBtn();
  }
  if(scrollId != null){
    const i = items.findIndex(n => n.id === scrollId);
    if(i >= 0){ w.ensureVisible(i); scrollEntRowIntoView(entRowEl(scrollId)); }
  }
}
// Scrolls only the list container, not the page. A row that is higher than the container shows from its top.
function scrollEntRowIntoView(r){
  const el = document.getElementById('entlist'); if(!el || !r) return;
  const rr = r.getBoundingClientRect(), er = el.getBoundingClientRect();
  if(rr.top < er.top) el.scrollTop -= er.top - rr.top;
  else if(rr.bottom > er.bottom) el.scrollTop += Math.min(rr.top - er.top, rr.bottom - er.bottom);
}
// loadNode() calls this function before its request: the row of the node opens (in the loading state) and the list scrolls to it.
function openEntityRow(id){
  if(entOpenId !== id){ entRelOpen.clear(); entObsAll = false; entDetail = null; }
  else if(entDetail && entDetail.id !== id) entDetail = null;
  entOpenId = id;
  entScrollToId = id;
  renderEntityList();
}
// loadNode() calls this function with the response of GET /node. An old response for a different row is ignored.
function setEntityRowDetail(id, d){
  if(entOpenId !== id) return;
  entDetail = d || {error: true};
  entScrollToId = id;
  renderEntityList();
}
// A knowledge theme change: the two lists start from the first page and no entity row is open.
function resetListWindows(){
  if(docWindowList) docWindowList.invalidate();
  if(entWindowList) entWindowList.invalidate();
  entLastSig = null; entOpenId = null; entDetail = null; entObsAll = false; entRelOpen.clear();
}
function closeEntityRow(){
  entOpenId = null; entDetail = null; entObsAll = false; entRelOpen.clear();
  renderEntityList();
}
// A click on a row or on a neighbor: one GET /node request through loadNode(). The graph selects the node and the camera moves to it.
function selectEntity(id){
  selectedNodeId = id;
  loadNode(id);
}
function onEntListClick(e){
  const item = e.target.closest('.entitem'); if(!item) return;
  const id = item.dataset.entId;
  const go = e.target.closest('[data-ent-go]');
  if(go){ e.preventDefault(); selectEntity(go.dataset.entGo); const nr = entRowEl(go.dataset.entGo); if(nr) nr.querySelector('.entrow').focus({preventScroll: true}); return; }
  const sum = e.target.closest('details.entrel > summary');
  if(sum){ const det = sum.parentElement; if(!e.defaultPrevented) entRelOpen.set(det.dataset.rel, !det.open); return; }
  const act = e.target.closest('[data-ent-act]');
  if(act && act.dataset.entAct === 'more'){ entObsAll = true; renderEntityList(); const r = entRowEl(id); if(r) r.querySelector('.entrow').focus({preventScroll: true}); return; }
  if(act && act.dataset.entAct === 'go'){ closeDocDrawer(); focusOnNode(id); return; }
  if(!e.target.closest('.entrow')) return;
  // A row in the error state sends the request again. It does not close.
  if(entOpenId === id && !(entDetail && entDetail.error)) closeEntityRow(); else selectEntity(id);
}
// ArrowUp and ArrowDown move the focus between rows. Escape closes the open row. Enter and Space are the default button keys.
function onEntListKeydown(e){
  const el = e.currentTarget;
  if(e.key === 'ArrowDown' || e.key === 'ArrowUp'){
    const row = e.target.closest('.entrow'); if(!row) return;
    const rows = [...el.querySelectorAll('.entrow')], next = rows[rows.indexOf(row) + (e.key === 'ArrowDown' ? 1 : -1)];
    e.preventDefault();
    if(next) next.focus();
  } else if(e.key === 'Escape'){
    if(selectedNodeId != null){
      const id = selectedNodeId;
      e.preventDefault(); e.stopPropagation();
      deselectEntity();
      const r = entRowEl(id); if(r) r.querySelector('.entrow').focus({preventScroll: true});
      return;
    }
    if(entOpenId != null){
      const id = entOpenId;
      e.preventDefault(); e.stopPropagation();
      closeEntityRow();
      const r = entRowEl(id); if(r) r.querySelector('.entrow').focus({preventScroll: true});
    }
  }
}

function renderDocs(filter){
  // rawQ goes to the server as the user typed it (SQLite LIKE folds the case of ASCII letters only). q is for the local match.
  const rawQ = (filter !== undefined ? filter : (document.getElementById('docq') ? document.getElementById('docq').value : '')).trim();
  const q = rawQ.toLowerCase();
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
    if(docWindowList) docWindowList.invalidate();
    const dl = document.getElementById('doclist'); if(dl) dl.innerHTML = doclistToolbarHtml() + '<p class="hint" style="padding:16px 12px;text-align:center"><span data-icon="search"></span> ' + tr("ws.search.need_query") + '</p>';
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
  // The list rows are the server pages for this filter text. The first page of a new filter text is not loaded yet: the list keeps its rows until the response comes, then loadDocPage() calls this function again.
  const pager = docPagerFor(rawQ);
  if(!pager.loaded && !pager.error){
    // The first page of a filter text is requested when the typing stops (DOC_FILTER_DELAY ms), thus one key press sends no request.
    if(pager !== docMainPager){
      clearTimeout(docFilterTimer);
      docFilterTimer = setTimeout(() => { if(pager === docFilterPager) loadDocPage(pager); }, DOC_FILTER_DELAY);
    }
    syncGraphDocNav();
    return;
  }
  // 숨김(hidden)은 기본 목록·즐겨찾기 양쪽에서 제외(목록 전용 숨김, 그래프는 안 건드림).
  // The pinned and hidden documents are all in the client, thus the filter text selects them here.
  const pinned = allDocs.filter(dc=> dc.hidden!==1 && dc.pinned===1 && match(dc));
  const rest = pager.items.filter(dc=> dc.hidden!==1 && dc.pinned!==1);
  const hiddenDocs = allDocs.filter(dc=> dc.hidden===1 && match(dc));
  // The total of the server counts the documents that were not pinned and not hidden at the time of the request. A document of the loaded pages that the client pinned or hid after that is not in the list.
  docListPager = pager;
  docListTotal = (pager.next == null) ? rest.length : Math.max(rest.length, pager.total - (pager.items.length - rest.length));

  const ph = document.getElementById('pinnedhead');
  if(ph){
    ph.style.display = pinned.length ? '' : 'none';
    ph.textContent = tr('ws.list.pinned_count', { count: pinned.length });
  }
  const pl = document.getElementById('pinnedlist'); if(pl){ pl.innerHTML = pinned.map(docItemHtml).join(''); if(window.ClaireUi) window.ClaireUi.enhance(pl); }

  const dw = docWindow();
  if(dw){
    // The same search text keeps the rendered count and the scroll position. A new search text starts from the first 31 documents.
    dw.render(rest, 'docs:' + q, {head: doclistToolbarHtml(), empty: pager.error ? '<div class="list-more-error" role="status">' + tr("ws.list.docs_failed") + ' <button type="button" class="sec list-retry">' + tr("ws.common.retry") + '</button></div>' : '<p class=hint style="padding:10px">' + tr("ws.list.no_docs") + '</p>'});
    if(window.ClaireUi){ const dl = document.getElementById('doclist'); if(dl) window.ClaireUi.enhance(dl); }
    const activeIndex = activeDoc ? rest.findIndex(dc => dc.id === activeDoc) : -1;
    if(activeIndex >= 0) dw.ensureVisible(activeIndex);
    // After a deep link, the list scrolls to the row of the open document one time, when the list is in view.
    if(docScrollToActive && activeIndex >= 0){
      const dl = document.getElementById('doclist'), row = dl.querySelector('.docitem.active');
      if(row && dl.offsetParent !== null){
        docScrollToActive = false;
        const rr = row.getBoundingClientRect(), er = dl.getBoundingClientRect();
        if(rr.top < er.top || rr.bottom > er.bottom) dl.scrollTop += rr.top - er.top - Math.max(0, (er.height - rr.height) / 2);
      }
    }
    syncListTopBtn();
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
      sh.textContent = (showHidden?'▲ ':'▼ ')+tr(showHidden ? 'ws.list.hidden_collapse' : 'ws.list.hidden_show', { count: hiddenDocs.length });
    }
    if(hl){
      const prevScroll = hl.scrollTop;
      hl.innerHTML = showHidden ? hiddenDocs.map(docItemHtml).join('') : '';
      hl.style.display = showHidden ? 'block' : 'none';
      if(showHidden && prevScroll > 0) hl.scrollTop = prevScroll;
      if(showHidden && window.ClaireUi) window.ClaireUi.enhance(hl);
    }
  }
  const st = document.getElementById('stat');
  if(st){
    if(q){
      st.textContent = tr('ws.list.found', { count: docListTotal + pinned.length });
    } else {
      st.innerHTML = tr('ws.list.doc_count', { count: docListTotal + pinned.length + hiddenDocs.length }) +
        (allNodes && allNodes.length ? ' (' + tr('ws.list.entity_count', { count: allNodes.length }) + ')' : '');
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
  const d=allDocs.find(x=>x.id===id); if(d){ d.pinned = val?1:0; docListPlace(d); }
  renderDocs(document.getElementById('docq').value);
  try{
    const r=await fetch(apiUrl('document/pin'),{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({id:id, pinned:val})});
    if(r.status===401||r.status===404) expireWriteAccess();
    if(!r.ok && d){ d.pinned = val?0:1; docListPlace(d); renderDocs(document.getElementById('docq').value); }
  }catch(e){ if(d){ d.pinned = val?0:1; docListPlace(d); renderDocs(document.getElementById('docq').value); } }
  return true;
}
async function toggleHide(id, val){
  if(!canWrite()) return false;
  // 숨기는 방향(val=true)만 컨펌 — 오클릭 방지(사용자 지적) + 되돌리는 방법을 그 자리에서
  // 안내(어디서 다시 꺼내는지 몰라 헤매지 않게). 숨김 해제(val=false)는 안전한 방향이라
  // 컨펌 없이 즉시. 반환값 = 실제로 적용됐는지(컨펌 취소 시 false — 호출측 버튼 갱신 판단용).
  if(val && !confirm(tr('ws.list.hide_confirm'))){
    return false;
  }
  const d=allDocs.find(x=>x.id===id); if(d){ d.hidden = val?1:0; docListPlace(d); }
  renderDocs(document.getElementById('docq').value);
  try{
    const r=await fetch(apiUrl('document/hide'),{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({id:id, hidden:val})});
    if(r.status===401||r.status===404) expireWriteAccess();
    if(!r.ok && d){ d.hidden = val?0:1; docListPlace(d); renderDocs(document.getElementById('docq').value); }
  }catch(e){ if(d){ d.hidden = val?0:1; docListPlace(d); renderDocs(document.getElementById('docq').value); } }
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
  if(lbl) lbl.textContent = val ? tr("ws.list.hidden_done") : tr("ws.list.hide");
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
  { const sq = document.getElementById('streamq'); if(sq) sq.value = ''; }
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
  currentNodeData = null;
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
  contextPinned = false;
  listOverride = null;
  if(panel) panel.innerHTML = defaultHint();
  resetGraphCamera();
  syncGraphDocNav();
}

function resolveHomeView(stats, activeThemeId){
  if(!stats) return 'home';
  const targetTheme = String((activeThemeId !== undefined && activeThemeId !== null) ? activeThemeId : '0');
  let jobs = [];
  if(Array.isArray(stats.active_jobs) && stats.active_jobs.length > 0){
    jobs = stats.active_jobs.filter(j => j && j.active);
  } else if(stats.active_ingest && stats.active_ingest.active){
    jobs = [stats.active_ingest];
  } else if(stats.ingesting){
    jobs = [{ active: true, kb_id: targetTheme }];
  }
  if(jobs.length === 0) return 'home';
  const matchesCurrent = jobs.some(j => {
    const jTheme = String((j.kb_id !== undefined && j.kb_id !== null) ? j.kb_id : '0');
    const curThemeObj = findTheme(targetTheme);
    const jThemeObj = findTheme(jTheme);
    if(curThemeObj && jThemeObj && curThemeObj === jThemeObj) return true;
    return jTheme === targetTheme;
  });
  if(matchesCurrent) return 'job';
  return 'home+banner';
}

function showOtherThemeJobBanner(stats){
  const banner = document.getElementById('format-warn-banner');
  if(!banner) return;
  const curThemeStr = String(activeThemeId || '0');
  const otherJobs = (stats && Array.isArray(stats.active_jobs) ? stats.active_jobs : (stats && stats.active_ingest ? [stats.active_ingest] : []))
    .filter(j => {
      if(!j || !j.active) return false;
      const jThemeStr = String(j.kb_id !== undefined ? j.kb_id : '0');
      const curThemeObj = findTheme(curThemeStr);
      const jThemeObj = findTheme(jThemeStr);
      if(curThemeObj && jThemeObj && curThemeObj === jThemeObj) return false;
      return jThemeStr !== curThemeStr;
    });
  if(!otherJobs.length) return;
  const job = otherJobs[0];
  const tName = job.theme_label || (tr("ws.jobs.theme_hash") + (job.kb_id || 0));
  if(typeof ClaireStatusBanner !== 'undefined' && ClaireStatusBanner && ClaireStatusBanner.show){
    ClaireStatusBanner.show('custom_notice', {
      message: tr('ws.jobs.other_theme', { theme: tName, job: job.title || tr('ws.jobs.job') })
    }, {
      level: 'info',
      icon: 'restore',
      title: tr("ws.jobs.background"),
      actionLabel: tr("ws.jobs.go_theme"),
      action: function(){
        switchKnowledgeTheme(String(job.kb_id || '0'));
      }
    });
  }
}

function hideOtherThemeJobBanner(){
  if(typeof ClaireStatusBanner !== 'undefined' && ClaireStatusBanner && ClaireStatusBanner.hide){
    ClaireStatusBanner.hide();
  }
}

// True while the home entry resets the workspace. closeReader() then does not change the view to the graph, thus the view goes from the reader to the home view in one step.
let homeEntryActive = false;
async function enterHome({reason = 'brand'} = {}){
  homeEntryActive = true;
  try{ resetWorkspaceState(); } finally { homeEntryActive = false; }
  // The home view shows at once. A job of this theme changes it to the job view after the stats arrive.
  if(centerView !== 'matrix') setCenterView('home');
  document.title = HOME_TITLE;

  // URL / History: the path selects the knowledge base; replaceState without pushing
  const url = new URL(window.location.href);
  url.search = '';
  url.hash = '';
  window.history.replaceState({ pane: 'graph', modal: null, view: 'home' }, HOME_TITLE, url.pathname);

  const brandLink = document.querySelector('a.brand');
  if(brandLink){
    brandLink.href = kbHomePath(activeThemeId);
  }

  let stats = null;
  try{
    const r = await fetch(apiUrl('stats'), { cache: 'no-store' });
    if(r.ok) stats = await r.json();
  }catch(_){}
  applyEffortSpecFromStats(stats);

  const view = resolveHomeView(stats, activeThemeId);

  if(view === 'job'){
    suspendGraphForIngest();
    setCenterView('matrix');
    processActiveJobsStats(stats);
    startFastIngestPolling();
  } else {
    // 'home' or 'home+banner': the overview. The graph data loads for the lists and the counts; the graph pane is hidden, thus the engine does not run its loop.
    homeStats = stats || homeStats;
    setCenterView('home');
    if(!graphInitialized) initGraph(); else applyView();
    if(view === 'home+banner') showOtherThemeJobBanner(stats); else hideOtherThemeJobBanner();
    syncWorkspaceLayout();
  }

  if(reason === 'brand' && typeof window.gtag === 'function'){
    try{
      window.gtag('event', 'home_navigate', {
        reason: 'brand',
        kb_id: activeThemeId
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
    if(centerView === 'reader') loadDocPanel(activeDoc);
    else if(centerView === 'graph' && !selectedNodeId && typeof renderGraphInsights === 'function') renderGraphInsights();
  } else {
    if(net) net.unselectAll();
    resetGraphCamera();
    if(centerView === 'graph' && !selectedNodeId && typeof renderGraphInsights === 'function') renderGraphInsights();
    else if(panel) panel.innerHTML = defaultHint();
  }
}

// 좌측 문서 선택 시 우측 패널: 문서 요약 + 상세 + '이 문서의 노드' 버튼.
function loadDocPanel(id, focusTab = false){
  selectedNodeId = null;
  currentNodeData = null;
  if(isolatedNodeId) clearNodeIsolation();
  if(net) net.unselectAll();
  if(typeof closeEntityRow === 'function') closeEntityRow();
  applyView();
  function restoreTabFocus(){
    if(!focusTab) return;
    requestAnimationFrame(() => {
      const tabBtn = document.getElementById('ctx-tab-' + activeDocCtxTab);
      if(tabBtn && typeof tabBtn.focus === 'function') tabBtn.focus();
    });
  }
  if(curReaderDocData && curReaderDocData.id===id){
    renderDocPanel(curReaderDocData);
    markDocumentSeen(id);
    restoreTabFocus();
    if(typeof centerView !== 'undefined' && centerView === 'presentation'){
      if(typeof updatePresentationView === 'function') updatePresentationView(id);
    }
    return;
  }
  panel.innerHTML='<p class=hint>' + tr("ws.reader.loading") + '</p>';
  fetch(apiUrl('document?id='+encodeURIComponent(id))).then(r=>r.json()).then(dc=>{
    if(activeDoc!==id && (typeof curReaderDoc === 'undefined' || curReaderDoc !== id)) return;
    if(!dc || dc.error){ panel.innerHTML='<p class=hint>' + tr("ws.reader.not_found") + '</p>'; return; }
    curReaderDocData=dc;
    const docInAll = allDocs && allDocs.find(d => d.id === id);
    if(docInAll && dc.has_presentation !== undefined){
      docInAll.has_presentation = dc.has_presentation;
    }
    renderDocPanel(dc);
    markDocumentSeen(id);
    restoreTabFocus();
    if(typeof centerView !== 'undefined' && centerView === 'presentation'){
      if(typeof updatePresentationView === 'function') updatePresentationView(id);
    }
  }).catch(()=>{ panel.innerHTML='<p class=hint>' + tr("ws.reader.load_failed") + '</p>'; });
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
const DOC_CTX_TABS = ['links', 'entities', 'source'];
let activeDocCtxTab = 'links';
let curDocPanelData = null;
const sharedDocsAttempted = new Set();

function sourceBadgeHtml(dc){
  if(!dc) return '';
  const isTrunc = !!(dc.raw_truncated || (dc.meta && dc.meta.raw_truncated));
  const origChars = Number((dc.orig_chars || (dc.meta && dc.meta.orig_chars) || dc.stt_orig_chars || (dc.meta && dc.meta.stt_orig_chars)) || 0);
  const rawChars = Number((dc.raw_chars || (dc.meta && dc.meta.raw_chars) || dc.stt_raw_chars || (dc.meta && dc.meta.stt_raw_chars)) || 0);
  const hasRawText = Boolean(rawChars > 0 || dc.has_raw_file || (dc.raw_text && String(dc.raw_text).length > 0));
  if(isTrunc){
    let label = tr('context.doc.source_kept_partial');
    if(origChars > 0 && rawChars > 0){
      label += ' (' + rawChars.toLocaleString() + ' / ' + origChars.toLocaleString() + ')';
    } else if(rawChars > 0){
      label += ' (' + rawChars.toLocaleString() + ')';
    }
    return '<span class="prov det source-badge" data-source-badge="partial">' + esc(label) + '</span>';
  }
  if(hasRawText){
    return '<span class="prov det source-badge" data-source-badge="full">' + esc(tr('context.doc.source_kept')) + '</span>';
  }
  return '';
}

function computeSharedDocs(dc){
  if(!dc || !dc.id) return [];
  const ns = docNodes(dc.id, dc);
  const counts = new Map();
  const seenEnt = new Set();
  ns.forEach(n => {
    if(!n || !n.id || seenEnt.has(n.id)) return;
    seenEnt.add(n.id);
    const existing = (allNodes && typeof allNodes.get === 'function') ? allNodes.get(n.id) : null;
    const srcs = (existing && Array.isArray(existing.sources) && existing.sources.length) ? existing.sources : (Array.isArray(n.sources) ? n.sources : []);
    const docSet = new Set(srcs);
    docSet.forEach(otherId => {
      if(!otherId || otherId === dc.id) return;
      const d = docById.get(otherId);
      if(d && d.hidden === 1 && !showHidden) return;
      counts.set(otherId, (counts.get(otherId) || 0) + 1);
    });
  });
  return [...counts.entries()]
    .map(([id, count]) => ({ id, count, doc: docById.get(id) || null }))
    .sort((a, b) => (b.count - a.count) || String(a.id).localeCompare(String(b.id)))
    .slice(0, 5);
}

function setEntityHoverHighlight(id){
  if(!id) return;
  document.querySelectorAll('.ctx-local-graph').forEach(el => el.classList.add('has-hover'));
  document.querySelectorAll('.ctx-lg-node[data-entity-id="' + id + '"]').forEach(el => el.classList.add('hover-active'));
  document.querySelectorAll('.ctx-lg-edge[data-from="' + id + '"], .ctx-lg-edge[data-to="' + id + '"]').forEach(el => el.classList.add('hover-active'));
  document.querySelectorAll('.ctx-lg-doc-edge[data-entity-id="' + id + '"]').forEach(el => el.classList.add('hover-active'));
  document.querySelectorAll('.ctx-entity-row[data-entity-id="' + id + '"]').forEach(el => el.classList.add('entity-hover-active'));
  document.querySelectorAll('.home-map-row[data-home-ent="' + id + '"]').forEach(el => el.classList.add('entity-hover-active'));
  document.querySelectorAll('.entitem[data-ent-id="' + id + '"]').forEach(el => el.classList.add('entity-hover-active'));
  if(typeof window.setReaderEntityMarkHover === 'function'){
    window.setReaderEntityMarkHover(id);
  }
}

function clearEntityHoverHighlight(){
  document.querySelectorAll('.ctx-local-graph').forEach(el => el.classList.remove('has-hover'));
  document.querySelectorAll('.ctx-lg-node.hover-active').forEach(el => el.classList.remove('hover-active'));
  document.querySelectorAll('.ctx-lg-edge.hover-active').forEach(el => el.classList.remove('hover-active'));
  document.querySelectorAll('.ctx-lg-doc-edge.hover-active').forEach(el => el.classList.remove('hover-active'));
  document.querySelectorAll('.ctx-entity-row.entity-hover-active').forEach(el => el.classList.remove('entity-hover-active'));
  document.querySelectorAll('.home-map-row.entity-hover-active').forEach(el => el.classList.remove('entity-hover-active'));
  document.querySelectorAll('.entitem.entity-hover-active').forEach(el => el.classList.remove('entity-hover-active'));
  if(typeof window.clearReaderEntityMarkHover === 'function'){
    window.clearReaderEntityMarkHover();
  }
}
window.setEntityHoverHighlight = setEntityHoverHighlight;
window.clearEntityHoverHighlight = clearEntityHoverHighlight;

function renderLocalGraphHtml(dc){
  if(!dc) return '';
  const allDocEntities = docNodes(dc.id, dc);
  const r1 = allDocEntities.slice(0, 24);
  const r1Set = new Set(r1.map(n => n.id));
  const docEntSet = new Set(allDocEntities.map(n => n.id));
  const pos = {};

  r1.forEach((n, i) => {
    const angle = -Math.PI / 2 + (i * 2 * Math.PI / Math.max(r1.length, 1));
    pos[n.id] = {
      x: Math.round(Math.cos(angle) * 78 * 10) / 10,
      y: Math.round(Math.sin(angle) * 78 * 10) / 10,
      angle: angle
    };
  });

  let r2 = [];
  let r2CandidatesCount = 0;
  if(graphInitialized && allEdges){
    const candMap = new Map();
    allEdges.forEach(e => {
      if(!e || (typeof e.id === 'string' && e.id.indexOf('cl_') === 0)) return;
      if(r1Set.has(e.from) && !docEntSet.has(e.to)){
        if(!candMap.has(e.to)) candMap.set(e.to, { id: e.to, connectedR1: new Set() });
        candMap.get(e.to).connectedR1.add(e.from);
      }
      if(r1Set.has(e.to) && !docEntSet.has(e.from)){
        if(!candMap.has(e.from)) candMap.set(e.from, { id: e.from, connectedR1: new Set() });
        candMap.get(e.from).connectedR1.add(e.to);
      }
    });
    const candidates = Array.from(candMap.values()).map(cand => {
      const nodeInfo = allNodes ? allNodes.get(cand.id) : null;
      return {
        id: cand.id,
        connectedR1: cand.connectedR1,
        degree: (nodeInfo && nodeInfo.degree) || 0,
        label: (nodeInfo && (nodeInfo.label || nodeInfo.name)) || cand.id,
        group: (nodeInfo && nodeInfo.group) || 'Concept',
      };
    });
    candidates.sort((a, b) => ((b.degree || 0) - (a.degree || 0)) || String(a.label).localeCompare(String(b.label)));
    r2CandidatesCount = candidates.length;
    r2 = candidates.slice(0, 12);

    r2.forEach(cand => {
      let sinSum = 0, cosSum = 0, count = 0;
      cand.connectedR1.forEach(r1Id => {
        if(pos[r1Id]){
          sinSum += Math.sin(pos[r1Id].angle);
          cosSum += Math.cos(pos[r1Id].angle);
          count++;
        }
      });
      cand.targetAngle = count > 0 ? Math.atan2(sinSum, cosSum) : 0;
    });
    r2.sort((a, b) => a.targetAngle - b.targetAngle);
    r2.forEach((cand, idx) => {
      const angle = r2.length <= 4 ? cand.targetAngle : (-Math.PI / 2 + (idx * 2 * Math.PI / r2.length));
      pos[cand.id] = {
        x: Math.round(Math.cos(angle) * 148 * 10) / 10,
        y: Math.round(Math.sin(angle) * 148 * 10) / 10,
        angle: angle
      };
    });
  } else if(typeof initGraph === 'function' && !graphInitialized){
    initGraph();
  }

  let linesHtml = '';
  r1.forEach(n => {
    const p = pos[n.id];
    linesHtml += '<line class="ctx-lg-doc-edge" data-entity-id="' + esc(n.id) + '" x1="0" y1="0" x2="' + p.x + '" y2="' + p.y + '" />';
  });

  if(graphInitialized && allEdges){
    const renderedSet = new Set([...r1.map(n => n.id), ...r2.map(n => n.id)]);
    const pairEdges = new Map();
    allEdges.forEach(e => {
      if(!e || (typeof e.id === 'string' && e.id.indexOf('cl_') === 0)) return;
      if(renderedSet.has(e.from) && renderedSet.has(e.to) && e.from !== e.to){
        const pairKey = e.from < e.to ? (e.from + ':' + e.to) : (e.to + ':' + e.from);
        const existing = pairEdges.get(pairKey);
        if(!existing || (existing.dashes && !e.dashes)){
          pairEdges.set(pairKey, e);
        }
      }
    });
    pairEdges.forEach(e => {
      const p1 = pos[e.from], p2 = pos[e.to];
      if(p1 && p2){
        const isProv = e.dashes === true;
        linesHtml += '<line class="ctx-lg-edge' + (isProv ? ' provisional" stroke-dasharray="3 3' : '') + '" data-from="' + esc(e.from) + '" data-to="' + esc(e.to) + '" x1="' + p1.x + '" y1="' + p1.y + '" x2="' + p2.x + '" y2="' + p2.y + '" />';
      }
    });
  }

  const docTitle = dc.title || 'Document';
  let nodesHtml = '<g class="ctx-lg-center" role="img" aria-label="' + esc(docTitle) + '"><circle cx="0" cy="0" r="14" /><path d="M4 2.5h5l3 3v8H4zM9 2.5v3h3M6 8.5h4M6 11h4" transform="translate(-8,-8)" fill="none" stroke="var(--fg)" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round"/></g>';

  const renderNode = (n, isR1) => {
    const p = pos[n.id];
    const color = TYPE_COLORS[n.group] || '#8b949e';
    const label = n.label || n.name || n.id;
    const trunc = label.length > 10 ? (label.slice(0, 9) + '…') : label;
    const anchor = p.x >= 0 ? 'start' : 'end';
    const tx = p.x >= 0 ? (p.x + 8) : (p.x - 8);
    const ty = p.y + 3;
    const r = isR1 ? 6 : 4.5;
    return '<g class="ctx-lg-node ' + (isR1 ? 'ring-1' : 'ring-2') + '" role="button" tabindex="0" aria-label="' + esc(label) + '" data-entity-id="' + esc(n.id) + '" onclick="selectDocEntity(' + jsArg(n.id) + ')" onkeydown="if(event.key===\'Enter\'||event.key===\' \'){event.preventDefault();selectDocEntity(' + jsArg(n.id) + ');}" onmouseenter="setEntityHoverHighlight(' + jsArg(n.id) + ')" onmouseleave="clearEntityHoverHighlight()">' +
      '<circle cx="' + p.x + '" cy="' + p.y + '" r="14" fill="none" pointer-events="all" />' +
      '<circle cx="' + p.x + '" cy="' + p.y + '" r="' + r + '" fill="' + esc(color) + '" stroke="var(--bg)" stroke-width="1.2" />' +
      '<text x="' + tx + '" y="' + ty + '" text-anchor="' + anchor + '">' + esc(trunc) + '</text>' +
      '</g>';
  };

  r1.forEach(n => { nodesHtml += renderNode(n, true); });
  r2.forEach(n => { nodesHtml += renderNode(n, false); });

  const svgAria = tr('context.doc.local_graph_aria');
  let h = '<svg class="ctx-local-graph" viewBox="-160 -160 320 320" role="region" aria-label="' + esc(svgAria) + '">' +
    linesHtml + nodesHtml + '</svg>';

  const unshownR1 = Math.max(0, allDocEntities.length - 24);
  const unshownR2 = Math.max(0, r2CandidatesCount - 12);
  const totalUnshown = unshownR1 + unshownR2;
  if(totalUnshown > 0){
    h += '<p class="ctx-local-graph-unshown">' + esc(tr('context.doc.local_graph_unshown', {count: totalUnshown})) + '</p>';
  }
  return h;
}

function renderDocLinksTab(dc){
  let h = '<div id="ctx-local-graph-slot">' + renderLocalGraphHtml(dc) + '</div>';
  const shared = computeSharedDocs(dc);
  h += '<section aria-labelledby="ctx-shared-heading"><div id="ctx-shared-heading" class="ctx-sec-head"><span>' + esc(tr('context.doc.shared_docs')) + '</span></div>';
  if(shared.length){
    h += '<div class="ctx-shared-list" role="list">';
    shared.forEach(item => {
      const title = (item.doc && item.doc.title) ? item.doc.title : item.id;
      const sType = (item.doc && item.doc.source_type) ? item.doc.source_type : '';
      h += '<button type="button" class="ctx-shared-row" role="listitem" data-shared-doc="' + esc(item.id) + '" onclick="selectDoc(' + jsArg(item.id) + ')">';
      h += '<span class="ctx-row-top"><span class="ctx-row-title">' + esc(title) + '</span><span class="prov det">' + esc(tr('context.doc.shared_unit', {count: item.count})) + '</span></span>';
      if(sType) h += '<span class="ctx-row-obs">' + esc(sType) + '</span>';
      h += '</button>';
    });
    h += '</div>';
  } else {
    h += '<p class="al">' + esc(tr('context.doc.no_shared')) + '</p>';
  }
  h += '</section>';
  return h;
}

function renderDocEntitiesTab(dc, ns){
  let h = '<section aria-labelledby="ctx-entities-heading"><div id="ctx-entities-heading" class="ctx-sec-head"><span>' + tr('ws.ctx.doc_nodes', { count: ns.length }) + '</span></div>';
  if(ns.length){
    h += '<div class="ctx-entity-list nodebtns" role="list">';
    ns.forEach(n => {
      const c = TYPE_COLORS[n.group] || '#8b949e';
      const firstObs = Array.isArray(n.obs) ? (n.obs[0] || '') : (n.obs || '');
      h += '<button type="button" class="ctx-entity-row nodebtn" role="listitem" data-entity-id="' + esc(n.id) + '" title="' + esc(n.group || '') + '" onmouseenter="peekNode(event,' + jsArg(n.id) + ');setEntityHoverHighlight(' + jsArg(n.id) + ')" onmouseleave="leaveNode();clearEntityHoverHighlight()" onclick="selectDocEntity(' + jsArg(n.id) + ')">';
      h += '<span class="ctx-row-top"><span class="ctx-row-title"><i class="home-map-dot" style="background:' + c + '"></i>' + esc(n.label) + ' <small style="color:var(--muted);font-weight:400">' + esc(n.group || '') + '</small></span><span class="prov det">' + esc(tr('context.doc.degree_unit', {count: n.degree || 0})) + '</span></span>';
      if(firstObs) h += '<span class="ctx-row-obs">' + esc(firstObs) + '</span>';
      h += '</button>';
    });
    h += '</div>';
  } else {
    h += '<p class="al">' + tr("ws.ctx.no_nodes") + '</p>';
  }
  h += '</section>';
  return h;
}

function renderDocSourceTab(dc, ns){
  const canAccessRaw = (typeof canIngest === 'function' ? canIngest() : false) || Boolean(dc.can_access_raw);
  const isStt = !!(dc.is_stt || (dc.meta && (dc.meta.is_stt || dc.meta.stt_applied || dc.meta.stt)));
  const presentation = dc.presentation_pdf || (dc.meta && dc.meta.presentation_pdf) || {};
  const hasPresentation = presentation.status === 'available' && isWebUrl(presentation.public_url);
  const origChars = Number((dc.orig_chars || (dc.meta && dc.meta.orig_chars) || dc.stt_orig_chars || (dc.meta && dc.meta.stt_orig_chars)) || 0);
  const rawChars = Number((dc.raw_chars || (dc.meta && dc.meta.raw_chars) || dc.stt_raw_chars || (dc.meta && dc.meta.stt_raw_chars)) || 0);
  const author = (dc.author || (dc.meta && dc.meta.author) || '').trim();
  const publishedAt = dc.published_at || (dc.meta && dc.meta.published_at) || '';

  let h = '<section aria-labelledby="ctx-src-heading"><div id="ctx-src-heading" class="ctx-sec-head"><span>' + esc(tr('context.doc.source_text')) + '</span>' + sourceBadgeHtml(dc) + '</div>';
  const links = [];
  if(isWebUrl(dc.url)) links.push('<a href="' + esc(dc.url) + '" target="_blank" rel="noopener">' + tr("ws.node.open_source") + '</a>');
  if(canAccessRaw && dc.has_raw_file) links.push('<a href="' + apiUrl('document/raw?id=' + encodeURIComponent(dc.id)) + '" target="_blank" rel="noopener"><span data-icon="doc"></span> ' + tr("ws.node.open_raw") + '</a>');
  if(isStt) links.push('<a href="#" class="stt-link" onclick="openSttReader();return false;">' + tr("ws.ctx.open_transcript") + '</a>');
  if(hasPresentation) links.push('<a href="' + esc(presentation.public_url) + '" target="_blank" rel="noopener">↗ Presentation PDF</a>');
  if(links.length) h += '<div class="ctx-source-links">' + links.join(' ') + '</div>';

  h += '<dl class="ctx-kv">';
  h += '<dt>' + esc(tr('context.doc.field_type')) + '</dt><dd>' + esc(dc.source_type || '—') + '</dd>';
  h += '<dt>' + esc(tr('context.doc.field_fetched')) + '</dt><dd>' + esc(dc.fetched_at ? dayOf(dc.fetched_at) : '—') + '</dd>';
  if(author) h += '<dt>' + esc(tr('context.doc.field_author')) + '</dt><dd>' + esc(author) + '</dd>';
  if(publishedAt) h += '<dt>' + esc(tr('context.doc.field_published')) + '</dt><dd>' + esc(typeof publishedAt === 'number' ? dayOf(publishedAt) : String(publishedAt)) + '</dd>';
  if(origChars > 0 || rawChars > 0){
    const charVal = (origChars > 0 && rawChars > 0 && origChars !== rawChars) ? (rawChars.toLocaleString() + ' / ' + origChars.toLocaleString()) : (rawChars || origChars).toLocaleString();
    h += '<dt>' + esc(tr('context.doc.field_chars')) + '</dt><dd>' + esc(charVal) + '</dd>';
  }
  h += '</dl>';
  if(Array.isArray(dc.extra_sources) && dc.extra_sources.length && typeof extraSourcesHtml === 'function'){
    h += '<div class="ctx-source-extra">' + extraSourcesHtml(dc) + '</div>';
  }
  h += '</section>';

  h += '<section aria-labelledby="ctx-model-heading"><div id="ctx-model-heading" class="ctx-sec-head"><span>' + esc(tr('context.doc.model_outputs')) + '</span></div>';
  h += '<dl class="ctx-kv">';
  const exModel = String(dc.extraction_model || '').trim(), exProvider = String(dc.extraction_provider || '').trim();
  if(exModel || exProvider) h += '<dt>' + esc(tr('context.doc.field_model')) + '</dt><dd id="ctx-src-model">' + esc([exProvider, exModel].filter(Boolean).join(' · ')) + '</dd>';
  h += '<dt>' + esc(tr('context.doc.summary_status')) + '</dt><dd>' + esc(Boolean(dc.summary) ? tr('context.doc.present') : tr('context.doc.absent')) + '</dd>';
  h += '<dt>' + esc(tr('context.doc.detail_status')) + '</dt><dd>' + esc(Boolean(dc.detail || dc.detail_html) ? tr('context.doc.present') : tr('context.doc.absent')) + '</dd>';
  h += '<dt>' + esc(tr('context.doc.entity_count')) + '</dt><dd>' + esc(String(ns.length)) + '</dd>';
  h += '</dl></section>';

  const resLog = Array.isArray(dc.resolution_log) ? dc.resolution_log : (dc.meta && Array.isArray(dc.meta.resolution_log) ? dc.meta.resolution_log : []);
  h += '<section aria-labelledby="ctx-res-heading"><div id="ctx-res-heading" class="ctx-sec-head"><span>' + esc(tr('context.doc.resolution')) + '</span></div>';
  if(resLog.length){
    const grouped = new Map();
    resLog.forEach(item => {
      const st = String((item && item.stage) || '');
      let key = st, badge = st ? ('<span>' + esc(st) + '</span>') : '<span>—</span>';
      if(st === 'exact_match' || st === 'alias_match' || st === 'acronym_match'){
        key = 'rule';
        badge = '<span class="prov det">' + esc(tr('stage.rule')) + '</span>';
      } else if(st === 'high_confidence_vector'){
        key = 'vector';
        badge = '<span class="prov model">' + esc(tr('stage.vector')) + '</span>';
      } else if(st === 'borderline_llm_judge'){
        key = 'llm';
        badge = '<span class="prov model">' + esc(tr('stage.llm')) + '</span>';
      } else if(st === 'no_match'){
        key = 'new_entity';
        badge = '<span class="prov det">' + esc(tr('stage.new_entity')) + '</span>';
      }
      const cur = grouped.get(key) || { badge, count: 0 };
      cur.count += 1;
      grouped.set(key, cur);
    });
    h += '<dl class="ctx-kv">';
    grouped.forEach(g => {
      h += '<dt>' + g.badge + '</dt><dd>' + esc(String(g.count)) + '</dd>';
    });
    h += '</dl>';
  } else {
    h += '<p class="al">' + esc(tr('context.doc.no_resolution')) + '</p>';
  }
  if(resLog.length || dc.has_decision_stream){
    h += '<button type="button" class="home-card-link ctx-stream-link" onclick="setCenterView(\'stream\')">' + esc(tr('context.doc.open_stream')) + '</button>';
  }
  h += '</section>';
  return h;
}

function scheduleSharedDocTitles(dc){
  if(!dc || !dc.id || activeDocCtxTab !== 'links') return;
  const shared = computeSharedDocs(dc);
  const missingIds = shared.filter(item => !item.doc && !sharedDocsAttempted.has(item.id)).map(item => item.id);
  if(!missingIds.length || typeof loadDocsById !== 'function') return;
  missingIds.forEach(id => sharedDocsAttempted.add(id));
  loadDocsById(missingIds).then(() => {
    if(activeDoc === dc.id && curDocPanelData && curDocPanelData.id === dc.id && activeDocCtxTab === 'links'){
      renderDocPanel(curDocPanelData);
    }
  });
}

function switchDocCtxTab(tab, focusTab = false){
  if(!DOC_CTX_TABS.includes(tab)) return;
  activeDocCtxTab = tab;
  if(curDocPanelData) renderDocPanel(curDocPanelData);
  if(focusTab){
    const btn = document.getElementById('ctx-tab-' + tab);
    if(btn && typeof btn.focus === 'function') btn.focus();
  }
}

function selectDocEntity(id){
  if(centerView === 'graph'){
    focusNode(id);
  } else {
    loadNode(id, true, false);
  }
}

panel.addEventListener('keydown', e => {
  const tabBtn = e.target && e.target.closest ? e.target.closest('.ctx-tab[data-ctx-tab]') : null;
  if(!tabBtn) return;
  const curTab = tabBtn.dataset.ctxTab;
  const idx = DOC_CTX_TABS.indexOf(curTab);
  if(idx === -1) return;
  let nextIdx = -1;
  if(e.key === 'ArrowRight') nextIdx = (idx + 1) % DOC_CTX_TABS.length;
  else if(e.key === 'ArrowLeft') nextIdx = (idx - 1 + DOC_CTX_TABS.length) % DOC_CTX_TABS.length;
  else if(e.key === 'Home') nextIdx = 0;
  else if(e.key === 'End') nextIdx = DOC_CTX_TABS.length - 1;
  if(nextIdx !== -1){
    e.preventDefault();
    switchDocCtxTab(DOC_CTX_TABS[nextIdx], true);
  }
});

function renderDocPanel(dc){
  curReaderDocData=dc;
  curDocPanelData=dc;
  const tab=DOC_CTX_TABS.includes(activeDocCtxTab)?activeDocCtxTab:'links';
  activeDocCtxTab=tab;
  let h='<div class="ctx-doc-head"><h2>'+esc(dc.title)+' <small>'+esc(dc.source_type||'')+'</small></h2>';
  if(canWrite()){
    h+='<div class=dochide-row><label class=dochide-label>'+
      '<input type="checkbox" id="panelhidechk" '+(dc.hidden===1?'checked':'')+
      ' onchange="panelToggleHide('+jsArg(dc.id)+',this.checked)">'+
      '<span id="panelhidelabel">'+(dc.hidden===1?tr("ws.list.hidden_done"):tr("ws.list.hide"))+'</span>'+
      '</label></div>';
  }
  h+='</div>';

  const tabMeta=[
    {id:'links',label:tr('context.doc.tab_links')},
    {id:'entities',label:tr('context.doc.tab_entities')},
    {id:'source',label:tr('context.doc.tab_source')}
  ];
  h+='<div class="ctx-tabs" role="tablist" aria-label="'+esc(tr('context.doc.tabs_aria'))+'">';
  tabMeta.forEach(item=>{
    const sel=item.id===tab;
    h+='<button type="button" id="ctx-tab-'+item.id+'" class="ctx-tab" role="tab" data-ctx-tab="'+item.id+'" aria-selected="'+(sel?'true':'false')+'" tabindex="'+(sel?'0':'-1')+'" aria-controls="ctx-tabpanel-doc" onclick="switchDocCtxTab('+jsArg(item.id)+')">'+esc(item.label)+'</button>';
  });
  h+='</div>';

  const ns=docNodes(dc.id, dc);
  h+='<div id="ctx-tabpanel-doc" class="ctx-tabpanel" role="tabpanel" aria-labelledby="ctx-tab-'+tab+'">';
  if(tab==='links'){
    h+=renderDocLinksTab(dc);
  } else if(tab==='entities'){
    h+=renderDocEntitiesTab(dc, ns);
  } else {
    if(typeof docMetaMessageHtml === 'function'){
      h+=docMetaMessageHtml(dc);
    }
    h+=renderDocSourceTab(dc, ns);
  }
  h+='</div>';
  panel.innerHTML=h;

  if(tab==='links') scheduleSharedDocTitles(dc);
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
    return '<span class=chip onclick="toggleSynth('+jsArg(id)+')" title="' + tr("ws.synth.remove") + '">'+esc(n?n.label:id)+' ✕</span>'; }).join('');
  const sb=document.getElementById('synthbtn');
  if(sb){
    sb.innerHTML='<span class="btn-icon"><span data-icon="puzzle"></span></span> <span class="btn-label">'+tr('ws.menu.synth')+' ('+synthSet.size+')</span>';
    sb.title=tr("ws.menu.synth") + ' ('+synthSet.size+')';
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
  // The filter works on the list that shows: documents or entities.
  if(listMode() === 'entities') onSearchInput(v);
  else onDocqInput(v);
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
    if(listMode() !== 'entities'){
      cancelServerSearch();
      currentSearchSeq++;
      clearTimeout(searchDebounce);
      doDocServerSearch(val);
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
  AUTH_SCOPE = ['owner','write','readonly','anonymous'].includes(scope) ? scope : 'unknown';
  READONLY = !canWrite();
  document.body.dataset.authScope=AUTH_SCOPE;
  document.body.classList.toggle('ro', READONLY);
  const addBtn = document.getElementById('addbtn');
  if(addBtn) addBtn.hidden = !canIngest();
  // 관리·설정 기능은 콘솔(/admin/*, /settings/*)에 있다. 햄버거 메뉴의 콘솔 항목으로 이동한다.
  if(window.ClaireAppBar){
    window.ClaireAppBar.setConsoleLink({
      visible: canWrite(),
      href: IS_ADMIN ? '/admin' : '/settings',
      label: IS_ADMIN ? tr("ws.bar.admin_console") : tr("ws.bar.my_settings"),
      icon: IS_ADMIN ? 'wrench' : 'settings'
    });
    window.ClaireAppBar.setLogoutVisible(AUTH_SCOPE === 'owner' || AUTH_SCOPE === 'write' || AUTH_SCOPE === 'readonly');
    window.ClaireAppBar.setSyndicationVisible(canWrite());
  }
  loadSharedKbBar();
  ['synthbtn', 'dedupbtn', 'purgebtn'].forEach(function(id){
    const el = document.getElementById(id);
    if(el) el.hidden = !canWrite();
  });
  const dangerSep = document.querySelector('#graph-section .menu-danger-sep');
  if(dangerSep) dangerSep.hidden = !canWrite();
  if(!canWrite()){
    synthSet.clear();
    showHidden=false;
    renderChips();
    // owner 전용 동적 UI를 네트워크 재조회보다 먼저 제거한다. 뒤늦게 도착하는
    // node/document 응답도 render 시점의 canWrite()로 다시 판정한다.
    panel.innerHTML=defaultHint();
  }
  updateSearchModeUI();
  syncManageButton();
  renderDocs();
  if(typeof renderHome === 'function' && centerView === 'home') renderHome();
  if(selectedNodeId) loadNode(selectedNodeId);
  else if(centerView === 'graph' && typeof renderGraphInsights === 'function') renderGraphInsights();
  else if(centerView === 'reader' && (activeDoc || (typeof curReaderDoc !== 'undefined' && curReaderDoc))) loadDocPanel(activeDoc || curReaderDoc);
  else if(panel) panel.innerHTML=defaultHint();
  if(canIngest()) refreshIngestEffortSpec();
}
function expireWriteAccess(){
  IS_ADMIN = false;
  setAccessScope('unknown','expired');
}

async function synth(){
  if(!canWrite()) return;
  const ids=[...synthSet];
  if(!ids.length){ alert(tr("ws.synth.need_nodes")); return; }
  panel.innerHTML='<p class=hint><span data-icon="puzzle"></span> '+tr('ws.synth.running', { count: ids.length })+'</p>';
  openDetailPane();
  // 인증은 claire_session 쿠키(/web 진입)로 자동 전송됨 — 별도 헤더 불필요.
  fetch(apiUrl('synthesize'),{method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({node_ids:ids})})
   .then(r=> { if(r.status===401||r.status===404){ expireWriteAccess(); return {error:tr("ws.common.session_expired")}; } return r.json(); })
   .then(d=>{
     if(d.error){ panel.innerHTML='<p class=hint>' + tr("ws.common.error_prefix") + ' '+esc(d.error)+'</p>'; return; }
     let h='<h2><span data-icon="puzzle"></span> '+tr('ws.synth.title')+' <small>'+tr('ws.synth.nodes', { count: d.entities.length })+'</small></h2>';
     h+='<div class=synth>'+esc(d.answer)+'</div>';
     h+='<p class=al>'+tr('ws.synth.targets')+' '+d.entities.map(esc).join(', ')+'</p>';
     panel.innerHTML=h;
   }).catch(e=>{ panel.innerHTML='<p class=hint>' + tr("ws.common.request_failed") + ' '+esc(String(e))+'</p>'; });
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
  const requestedMode = isSemantic ? tr("ws.search.ai") : tr("ws.search.fts");
  const statEl = document.getElementById('stat');
  if(statEl) statEl.textContent=tr('ws.search.in_progress', { mode: requestedMode });
  let r;
  try{
    const reqOpts = {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({query:q, summarize:false, limit:12, mode:searchMode})
    };
    if(abortController) reqOpts.signal = abortController.signal;
    r=await fetch(apiUrl('search'), reqOpts);
  }
  catch(e){
    if(abortController && abortController.signal.aborted) return;
    if(seq !== currentSearchSeq) return;
    if(statEl) statEl.textContent=tr("ws.search.failed");
    return;
  }
  if(seq !== currentSearchSeq) return;
  if(r.status===401||r.status===404){ expireWriteAccess(); if(statEl) statEl.textContent=tr("ws.common.session_expired_web"); return; }
  if(r.status===429){ if(statEl) statEl.textContent=tr("ws.search.rate_limited"); return; }
  let d={}; try{ d=await r.json(); }catch(_){}
  if(seq !== currentSearchSeq) return;
  if(!r.ok){ if(statEl) statEl.textContent=tr('ws.search.http_error', { status: r.status }); return; }
  const ids=(d.hits||[]).map(h=>h.id).filter(Boolean);
  highlightSet = new Set(ids);   // 라벨 검색과 동일하게 강조+dim 방식 사용
  applyView();
  clusterMatches(ids, ()=>fitToMatches(ids));   // 의미검색 결과도 점차 뭉치게 + 한눈에 fit
  const actualMode=(d.mode==='fts'||searchMode==='fts')?tr("ws.search.fts"):tr("ws.search.ai");
  if(statEl){
    statEl.textContent=ids.length
      ? tr('ws.search.mode_count', { mode: actualMode, count: ids.length })
      : tr('ws.search.no_result', { mode: actualMode });
  }
}

// --- 고유 상태 및 안내 배너 시스템 (ClaireStatusBanner) ---
const ClaireStatusBanner = (function(){
  const presets = {
    format_mismatch: {
      level: 'warning',
      icon: 'warning',
      title: tr("ws.banner.format_title"),
      render: function(data){
        const cfg = (data.configured || 'adoc').toUpperCase();
        const other = (data.configured === 'adoc' ? 'md' : 'adoc').toUpperCase();
        const count = data.mismatched || data.mismatched_docs || 0;
        const samples = data.mismatched_docs_sample || [];
        if (count === 1 && samples.length > 0) {
          const doc = samples[0];
          const safeTitle = esc(doc.title || doc.id);
          return tr('ws.banner.format_one', { configured: cfg, other: other, doc: `<b><a href="javascript:void(0)" onclick="selectDoc(${jsArg(doc.id)});setCenterView('reader')" style="color:var(--accent);text-decoration:underline;">${safeTitle}</a></b>` });
        }
        return tr('ws.banner.format_many', { configured: cfg, count: count, other: other, command: '<code>./cb-manuscript app format-migrate --apply</code>' });
      },
      actionLabel: function(data){
        const count = (data && (data.mismatched || data.mismatched_docs)) || 0;
        return count === 1 ? tr("ws.banner.open_doc") : tr("ws.banner.filter_mismatch");
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
      icon: 'info',
      title: tr("ws.banner.detail_title"),
      render: function(data){
        const count = data.missing_detail_docs || 0;
        const samples = data.missing_docs_sample || [];
        if (count === 1 && samples.length > 0) {
          const doc = samples[0];
          const safeTitle = esc(doc.title || doc.id);
          return tr('ws.banner.detail_one') + ` <a href="javascript:void(0)" onclick="selectDoc(${jsArg(doc.id)});setCenterView('reader')" style="color:var(--accent);font-weight:600;text-decoration:underline;">${safeTitle}</a>`;
        }
        return tr('ws.banner.detail_many', { count: count, command: '<code>./cb-manuscript app format-migrate --apply</code>' });
      },
      actionLabel: function(data){
        const count = (data && data.missing_detail_docs) || 0;
        return count === 1 ? tr("ws.banner.open_doc") : tr("ws.banner.filter_missing");
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
      icon: 'check-circle',
      title: tr("ws.banner.format_ok_title"),
      render: function(data){
        const cfg = (data.configured || 'adoc').toUpperCase();
        const total = data.total_docs || data.matching_docs || 0;
        return tr('ws.banner.format_ok', { count: total, format: cfg });
      }
    },
    readonly_mode: {
      level: 'info',
      icon: 'lock',
      title: tr("ws.banner.readonly_title"),
      render: function(){
        return tr("ws.banner.readonly");
      }
    },
    network_error: {
      level: 'error',
      icon: 'bolt',
      title: tr("ws.banner.offline_title"),
      render: function(data){
        return (data && data.message) || tr("ws.banner.offline");
      },
      actionLabel: tr("ws.banner.reload"),
      action: function(){
        if (typeof window !== 'undefined' && window.location && window.location.reload) {
          window.location.reload();
        }
      }
    },
    refresh_pending: {
      level: 'warning',
      icon: 'clock',
      title: tr("ws.banner.queued_title"),
      render: function(data){
        const count = (data && data.count) || tr('ws.banner.some');
        return tr('ws.banner.queued', { count: count });
      }
    },
    custom_notice: {
      level: 'info',
      icon: 'megaphone',
      title: tr("ws.banner.notice"),
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
      if(presetKey === 'format_mismatch' || presetKey === 'format_missing') return;
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
      if (iconEl) iconEl.setAttribute('data-icon', opts.icon || 'info');
      if (titleEl) titleEl.textContent = opts.title || tr("ws.banner.notice");
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
syncThemeBtn();   // 저장된 테마에 맞춰 달/해 아이콘 동기화(테마 자체는 head 인라인에서 선적용)
fetchThemes();    // 지식 관리자가 정의한 테마 목록을 조회하여 셀렉터 구성
if(initialDefaultToReader && typeof setCenterView === 'function'){
  setCenterView('reader');
}
loadThemeDocuments();
fetch(apiUrl('whoami')).then(r=>{ if(!r.ok) throw new Error('whoami failed'); return r.json(); }).then(d=>{
  IS_ADMIN = d.is_admin === true;
  var effectiveScope = (d.relation === 'visitor' || d.relation !== 'owner') ? (d.scope === 'anonymous' ? 'anonymous' : 'readonly') : (d.scope || 'readonly');
  setAccessScope(effectiveScope); // replaces setAccessScope(d.scope) with relation-checked effectiveScope
}).catch(()=>{
  IS_ADMIN = false;
  setAccessScope('unknown','failed');
});

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

syncListPane();
syncManageButton();
// 초기 베이스 히스토리 엔트리 등록
replaceAppHistory({ pane: activePane || 'graph', modal: getActiveModalName() });

// 읽기전용 디버그 핸들(테스트/Playwright 검증용 — closure 상태 관찰). 부작용 없음.

// --- Home view (design Section 5, units HW-1 & HW-2): facts, recent ingestion, knowledge map, recent decisions, maintenance ---
// Rule: a card shows only stored facts. When its source is absent, the card is hidden.
let homeStats = null, homeStatsSeq = 0;
let homeMiniCacheKey = '', homeMiniLayout = null;
let homeDecisionsCacheKey = '', homeDecisionsData = [], homeDecisionsSeq = 0;
function homeEntityCount(docId){
  if(!allNodes) return null;
  let n = 0;
  allNodes.forEach(node => { if((node.sources || []).includes(docId)) n++; });
  return n;
}
function resolutionStageBadge(stage, score){
  const s = String(stage || '');
  const scoreStr = (score !== undefined && score !== null && Number.isFinite(Number(score))) ? ' ' + Number(score).toFixed(2) : '';
  if(s === 'exact_match' || s === 'alias_match' || s === 'acronym_match') return '<span class="prov det">' + esc(tr('stage.rule')) + '</span>';
  if(s === 'high_confidence_vector') return '<span class="prov model">' + esc(tr('stage.vector') + scoreStr) + '</span>';
  if(s === 'borderline_llm_judge') return '<span class="prov model">' + esc(tr('stage.llm') + scoreStr) + '</span>';
  if(s === 'no_match') return '<span class="prov det">' + esc(tr('stage.new_entity')) + '</span>';
  return s ? '<span>' + esc(s) + '</span>' : '';
}
function buildHomeMiniTopo(realNodes){
  const n = realNodes.length;
  const ids = realNodes.map(it => it.id);
  const index = new Map(ids.map((id, i) => [id, i]));
  const group = new Array(n);
  const radius = new Float32Array(n);
  const eligible = new Uint8Array(n), anchor = new Uint8Array(n), hidden = new Uint8Array(n);
  const fixedX = new Float64Array(n).fill(NaN), fixedY = new Float64Array(n).fill(NaN);
  const initX = new Float64Array(n).fill(NaN), initY = new Float64Array(n).fill(NaN);
  const orbitBand = new Int8Array(n).fill(-1), orbitIndex = new Float64Array(n), orbitSide = new Int8Array(n);
  for(let i = 0; i < n; i++){
    const it = realNodes[i];
    const fixed = !!it.fixed && (it.fixed === true || !!it.fixed.x || !!it.fixed.y);
    group[i] = it.group || '';
    radius[i] = Math.max(0.5, Number(it.size != null ? it.size : nodeRadius(it.degree || 0)) || 4);
    anchor[i] = 0;
    hidden[i] = 0;
    eligible[i] = (!fixed && it.physics !== false) ? 1 : 0;
  }
  const ea = [], eb = [], springs = [];
  const adj = Array.from({length: n}, () => []);
  if(allEdges){
    allEdges.forEach(e => {
      if(!e || (typeof e.id === 'string' && e.id.indexOf('cl_') === 0)) return;
      const a = index.get(e.from), b = index.get(e.to);
      if(a === undefined || b === undefined) return;
      ea.push(a); eb.push(b);
      if(a !== b){ adj[a].push(b); adj[b].push(a); }
    });
  }
  const adjStart = new Int32Array(n + 1);
  for(let i = 0; i < n; i++) adjStart[i + 1] = adjStart[i] + adj[i].length;
  const adjList = new Int32Array(adjStart[n]);
  for(let i = 0; i < n; i++) adjList.set(adj[i], adjStart[i]);
  return {ids, group, radius, eligible, anchor, hidden, fixedX, fixedY, initX, initY, adjStart, adjList, springs, orbitBand, orbitIndex, orbitSide, _ea: ea, _eb: eb};
}
function computeHomeMiniLayout(realNodes){
  const key = String(activeThemeId || '0') + ':' + realNodes.length + ':' + (allEdges ? allEdges.length : 0);
  if(homeMiniCacheKey === key && homeMiniLayout) return homeMiniLayout;
  const G = window.ClaireGraph;
  if(!G || !G.Simulation || !realNodes.length){
    homeMiniCacheKey = key;
    homeMiniLayout = null;
    return null;
  }
  const topo = buildHomeMiniTopo(realNodes);
  const sim = new G.Simulation();
  sim.setTopology(topo);
  sim.snapToHomes();
  sim.warmup();
  const points = realNodes.map((n, i) => ({
    id: n.id,
    group: n.group,
    degree: n.degree || 0,
    x: Number.isFinite(sim.x[i]) ? sim.x[i] : 0,
    y: Number.isFinite(sim.y[i]) ? sim.y[i] : 0
  }));
  homeMiniCacheKey = key;
  homeMiniLayout = {points: points, ea: topo._ea, eb: topo._eb};
  return homeMiniLayout;
}
function drawHomeMiniMap(){
  const wrap = document.getElementById('home-minimap');
  const canvas = document.getElementById('home-minimap-canvas');
  if(!wrap || !canvas || !allNodes) return;
  const realNodes = [];
  allNodes.forEach(n => { if(n && !(typeof n.id === 'string' && n.id.indexOf('cl_') === 0)) realNodes.push(n); });
  if(!realNodes.length) return;
  const layout = computeHomeMiniLayout(realNodes);
  if(!layout || !layout.points.length) return;
  const w = Math.max(120, wrap.clientWidth || 220);
  const h = Math.max(120, wrap.clientHeight || 170);
  const dpr = Math.min(2, window.devicePixelRatio || 1);
  if(canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)){
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
  }
  const ctx = canvas.getContext('2d');
  if(!ctx) return;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);
  let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
  layout.points.forEach(p => {
    if(p.x < minX) minX = p.x; if(p.x > maxX) maxX = p.x;
    if(p.y < minY) minY = p.y; if(p.y > maxY) maxY = p.y;
  });
  const pad = 12;
  // The hint text is at the bottom of the box. The drawing does not go below the text.
  const hintEl = document.getElementById('home-minimap-hint');
  const reserve = hintEl ? Math.min(h / 2, hintEl.offsetHeight + 10) : 0;
  const drawH = h - reserve;
  const spanX = Math.max(1e-3, maxX - minX), spanY = Math.max(1e-3, maxY - minY);
  const scale = Math.min((w - pad * 2) / spanX, (drawH - pad * 2) / spanY);
  const ox = (w - spanX * scale) / 2 - minX * scale;
  const oy = (drawH - spanY * scale) / 2 - minY * scale;
  const dark = curTheme() === 'dark';
  // The dot size and the line opacity follow the density: a graph with thousands of nodes stays a map of clusters, not one filled area.
  const count = layout.points.length;
  const unit = Math.max(0.45, Math.min(2.2, Math.sqrt((w * drawH) / (count * 22))));
  const edgeAlpha = Math.max(0.06, Math.min(0.28, 60 / Math.max(1, layout.ea.length)));
  ctx.strokeStyle = dark ? 'rgba(139,148,158,' + edgeAlpha + ')' : 'rgba(101,109,118,' + edgeAlpha + ')';
  ctx.lineWidth = Math.max(0.4, Math.min(0.8, unit * 0.5));
  ctx.beginPath();
  for(let k = 0; k < layout.ea.length; k++){
    const pa = layout.points[layout.ea[k]], pb = layout.points[layout.eb[k]];
    if(!pa || !pb) continue;
    ctx.moveTo(pa.x * scale + ox, pa.y * scale + oy);
    ctx.lineTo(pb.x * scale + ox, pb.y * scale + oy);
  }
  ctx.stroke();
  const accent = (typeof getComputedStyle === 'function' ? getComputedStyle(document.documentElement).getPropertyValue('--accent').trim() : '') || '#0969da';
  // Nodes with many connections come last, thus the hubs stay on top.
  if(!layout.order) layout.order = layout.points.slice().sort((a, b) => a.degree - b.degree);
  layout.order.forEach(p => {
    const cx = p.x * scale + ox, cy = p.y * scale + oy;
    const rad = Math.min(6, unit * (1 + Math.sqrt(p.degree) * 0.28));
    ctx.beginPath();
    ctx.arc(cx, cy, rad, 0, Math.PI * 2);
    ctx.fillStyle = TYPE_COLORS[p.group] || '#8b949e';
    // In a dense graph the nodes with one connection or less are lighter, thus the cluster centers show.
    ctx.globalAlpha = (count > 600 && p.degree <= 1) ? 0.55 : 1;
    ctx.fill();
    ctx.globalAlpha = 1;
    if(selectedNodeId && p.id === selectedNodeId){
      ctx.beginPath();
      ctx.arc(cx, cy, rad + 2.5, 0, Math.PI * 2);
      ctx.strokeStyle = accent;
      ctx.lineWidth = 1.6;
      ctx.stroke();
    }
  });
}
// Extract the three insight lists from graph nodes.
function getGraphInsightLists(){
  const realNodes = [];
  if(allNodes) allNodes.forEach(n => { if(n && !(typeof n.id === 'string' && n.id.indexOf('cl_') === 0)) realNodes.push(n); });
  if(!realNodes.length) return null;
  const byLabel = (a, b) => String(a.label || a.id || '').localeCompare(String(b.label || b.id || ''));
  const hubs = [...realNodes].sort((a, b) => ((b.degree || 0) - (a.degree || 0)) || byLabel(a, b)).slice(0, 5);
  // An entity that is in no document is not a bridge.
  const bridges = realNodes.filter(n => (n.sources || []).length > 0).sort((a, b) => (((b.sources || []).length) - ((a.sources || []).length)) || ((b.degree || 0) - (a.degree || 0)) || byLabel(a, b)).slice(0, 5);
  const cutoff = (Date.now() / 1000) - 7 * 86400;
  const recent = realNodes.filter(n => Number(n.created_at || 0) >= cutoff).sort((a, b) => (Number(b.created_at || 0) - Number(a.created_at || 0)) || ((b.degree || 0) - (a.degree || 0)) || byLabel(a, b)).slice(0, 5);
  return { hubs, bridges, recent };
}

function renderMapRowsHtml(items, unitKey, valueFn, extraClass = ''){
  return items.map(n => {
    const color = TYPE_COLORS[n.group] || '#8b949e';
    const val = tr(unitKey, {count: valueFn(n)});
    const cls = 'home-map-row' + (extraClass ? (' ' + extraClass) : '');
    return '<button type="button" class="' + cls + '" role="listitem" data-home-ent="' + esc(n.id) + '" data-insight-ent="' + esc(n.id) + '" onmouseenter="setEntityHoverHighlight(' + jsArg(n.id) + ')" onmouseleave="clearEntityHoverHighlight()" onfocus="setEntityHoverHighlight(' + jsArg(n.id) + ')" onblur="clearEntityHoverHighlight()"><span class="home-map-dot" style="background:' + esc(color) + '"></span><span class="home-map-name">' + esc(n.label || n.id) + '</span><span class="home-map-val">' + esc(val) + '</span></button>';
  }).join('');
}
function renderHomeMapRows(el, items, unitKey, valueFn){
  if(!el) return;
  el.innerHTML = renderMapRowsHtml(items, unitKey, valueFn);
}
function renderHomeMap(){
  const card = document.getElementById('home-map'); if(!card) return;
  const insights = getGraphInsightLists();
  if(!insights){ card.hidden = true; return; }
  card.hidden = false;
  const { hubs, bridges, recent } = insights;
  renderHomeMapRows(document.getElementById('home-map-hubs-rows'), hubs, 'home.map.connections_unit', n => n.degree || 0);
  renderHomeMapRows(document.getElementById('home-map-bridges-rows'), bridges, 'home.map.docs_unit', n => (n.sources || []).length);
  const colRecent = document.getElementById('home-map-col-recent');
  if(colRecent){
    colRecent.hidden = !recent.length;
    if(recent.length) renderHomeMapRows(document.getElementById('home-map-recent-rows'), recent, 'home.map.connections_unit', n => n.degree || 0);
  }
  drawHomeMiniMap();
}

// Render the graph view insight lists in the context panel.
function renderGraphInsights(){
  const p = document.getElementById('panel');
  if(!p) return;
  if(centerView !== 'graph' || selectedNodeId) return;
  const insights = getGraphInsightLists();
  if(!insights){
    p.innerHTML = defaultHint();
    return;
  }
  const { hubs, bridges, recent } = insights;
  let h = '<div class="ctx-insights" id="ctx-insights">';
  h += '<div class="ctx-insights-head"><h2 class="ctx-sec-head" data-i18n="home.map.header">' + esc(tr('home.map.header')) + '</h2></div>';
  h += '<div class="ctx-insights-cols">';
  h += '<div class="ctx-insights-col" id="ctx-insights-col-hubs" data-map-list="hubs">';
  h += '<div class="home-map-col-head"><span>' + esc(tr('home.map.hubs')) + '</span><span class="home-map-col-sub">' + esc(tr('home.map.hubs_sub')) + '</span></div>';
  h += '<div class="home-map-col-rows ctx-insights-rows" id="ctx-insights-hubs-rows" role="list">';
  h += renderMapRowsHtml(hubs, 'home.map.connections_unit', n => n.degree || 0, 'ctx-insight-row');
  h += '</div></div>';
  h += '<div class="ctx-insights-col" id="ctx-insights-col-bridges" data-map-list="bridges">';
  h += '<div class="home-map-col-head"><span>' + esc(tr('home.map.bridges')) + '</span><span class="home-map-col-sub">' + esc(tr('home.map.bridges_sub')) + '</span></div>';
  h += '<div class="home-map-col-rows ctx-insights-rows" id="ctx-insights-bridges-rows" role="list">';
  h += renderMapRowsHtml(bridges, 'home.map.docs_unit', n => (n.sources || []).length, 'ctx-insight-row');
  h += '</div></div>';
  if(recent.length){
    h += '<div class="ctx-insights-col" id="ctx-insights-col-recent" data-map-list="recent">';
    h += '<div class="home-map-col-head"><span>' + esc(tr('home.map.recent')) + '</span><span class="home-map-col-sub">' + esc(tr('home.map.recent_sub')) + '</span></div>';
    h += '<div class="home-map-col-rows ctx-insights-rows" id="ctx-insights-recent-rows" role="list">';
    h += renderMapRowsHtml(recent, 'home.map.connections_unit', n => n.degree || 0, 'ctx-insight-row');
    h += '</div></div>';
  }
  h += '</div></div>' + personalKbCardHtml();
  p.innerHTML = h;
}
window.renderGraphInsights = renderGraphInsights;
window.getGraphInsightLists = getGraphInsightLists;
function paintHomeDecisions(){
  const card = document.getElementById('home-decisions'), list = document.getElementById('home-decisions-list');
  if(!card || !list) return;
  if(!isDecisionStreamEnabledForActiveTheme() || !homeDecisionsData.length){
    card.hidden = true;
    list.innerHTML = '';
    return;
  }
  card.hidden = false;
  list.innerHTML = homeDecisionsData.slice(0, 4).map(dec => {
    const title = dec.candidate ? (String(dec.entity || '') + ' → ' + String(dec.candidate)) : String(dec.entity || '');
    const entId = dec.target_entity_id || dec.source_entity_id || '';
    const sub = [dec.timestamp ? dayOf(dec.timestamp) : '', dec.document_title || ''].filter(Boolean).join(' · ');
    const attr = entId ? (' data-home-ent="' + esc(entId) + '"') : '';
    return '<button type="button" class="home-row" role="listitem"' + attr + (entId ? (' onmouseenter="setEntityHoverHighlight(' + jsArg(entId) + ')" onmouseleave="clearEntityHoverHighlight()"') : '') + '><span class="home-row-main"><span class="home-row-title">' + esc(title) + '</span><span class="home-row-sub">' + esc(sub) + '</span></span>' + resolutionStageBadge(dec.stage, dec.score) + '</button>';
  }).join('');
}
function renderHomeDecisions(){
  const card = document.getElementById('home-decisions'); if(!card) return;
  if(!isDecisionStreamEnabledForActiveTheme()){ card.hidden = true; return; }
  const key = String(activeThemeId || '0');
  if(homeDecisionsCacheKey === key){
    paintHomeDecisions();
    return;
  }
  homeDecisionsCacheKey = key;
  const seq = ++homeDecisionsSeq;
  fetch(apiUrl('resolution/decisions?limit=4'), {cache: 'no-store'}).then(r => r.ok ? r.json() : null).then(d => {
    if(seq !== homeDecisionsSeq) return;
    homeDecisionsData = (d && Array.isArray(d.decisions)) ? d.decisions.slice(0, 4) : [];
    if(centerView === 'home') paintHomeDecisions();
  }).catch(() => {
    if(seq !== homeDecisionsSeq) return;
    homeDecisionsData = [];
    if(centerView === 'home') paintHomeDecisions();
  });
}
function renderHomeMaintenance(){
  const card = document.getElementById('home-maint'), list = document.getElementById('home-maint-list');
  if(!card || !list) return;
  const fs = window.currentFormatStatus;
  if(AUTH_SCOPE !== 'owner' || !fs){
    card.hidden = true;
    list.innerHTML = '';
    return;
  }
  const cmd = './cb-manuscript app format-migrate --apply';
  const items = [];
  const missing = Number(fs.missing_detail_docs || 0);
  if(missing > 0) items.push({label: tr('home.maint.missing_detail', {count: missing}), cmd: cmd});
  const mismatched = Number((fs.mismatched != null ? fs.mismatched : fs.mismatched_docs) || 0);
  if(mismatched > 0) items.push({label: tr('home.maint.mismatched', {count: mismatched}), cmd: cmd});
  if(!items.length){
    card.hidden = true;
    list.innerHTML = '';
    return;
  }
  card.hidden = false;
  list.innerHTML = items.map(it =>
    '<div class="home-maint-row" role="listitem"><span class="home-row-title">' + esc(it.label) + '</span><span class="home-maint-cmdwrap"><code class="home-cmd">' + esc(it.cmd) + '</code><button type="button" class="home-copy-btn" data-home-copy="' + esc(it.cmd) + '">' + esc(tr('home.maint.copy')) + '</button></span></div>'
  ).join('');
}
let homeThemesCache = null;
function renderHomeThemes(){
  const card = document.getElementById('home-themes'), list = document.getElementById('home-themes-list');
  if(!card || !list) return;
  const paint = (themes) => {
    const items = Array.isArray(themes) ? themes : [];
    card.hidden = !items.length;
    if(!items.length){ list.innerHTML = '<p class="hint">' + esc(tr('ws.kb.home_themes_empty')) + '</p>'; return; }
    list.innerHTML = '<div class="claire-tags">' + items.slice(0, 8).map(th => {
      const num = th.number;
      const cls = th.knowledge_type === 'fictional' ? 'fic' : 'acc';
      return '<a class="claire-tag ' + cls + '" href="/themes/' + encodeURIComponent(num) + '/">' + esc(th.label || ('#' + num)) + '</a>';
    }).join('') + '</div>';
  };
  if(homeThemesCache){ paint(homeThemesCache); return; }
  // GET /themes: the root of the collection has each theme in ``themes`` (design Section 7). The home card shows the active ones.
  fetch('/themes', { credentials: 'same-origin', headers: { 'Accept': 'application/json' } }).then(r => r.ok ? r.json() : null).then(d => {
    homeThemesCache = ((d && d.themes) || []).filter(th => th.state === 'active');
    paint(homeThemesCache);
  }).catch(() => {});
}
function renderHome(){
  const wrap = document.getElementById('homewrap'); if(!wrap) return;
  const theme = (typeof findTheme === 'function') ? findTheme(activeThemeId) : null;
  const label = document.getElementById('home-theme-label');
  if(label) label.textContent = (homeStats && homeStats.theme_label) || (theme && theme.label) || '';
  const docs = (allDocs || []).filter(d => d.hidden !== 1);
  const facts = document.getElementById('home-facts');
  if(facts){
    const parts = [];
    if(homeStats && homeStats.documents != null) parts.push(tr('home.facts', {documents: homeStats.documents, entities: homeStats.entities, relations: homeStats.relations}));
    if(docs.length && docs[0].fetched_at) parts.push(tr('home.facts.last_ingest', {time: dayOf(docs[0].fetched_at)}));
    facts.textContent = parts.join(' · ');
  }
  const card = document.getElementById('home-recent'), list = document.getElementById('home-recent-list');
  if(card && list){
    const rows = docs.slice(0, 5);
    card.hidden = !rows.length;
    list.innerHTML = rows.map(d => {
      const count = homeEntityCount(d.id);
      const sub = [d.fetched_at ? dayOf(d.fetched_at) : '', count == null ? '' : tr('home.recent.entities', {count: count})].filter(Boolean).join(' · ');
      return '<button type="button" class="home-row" role="listitem" data-home-doc="' + esc(d.id) + '"><span class="home-row-kind">' + esc(d.source_type || '') + '</span><span class="home-row-main"><span class="home-row-title">' + esc(d.title || tr('home.recent.untitled')) + '</span><span class="home-row-sub">' + esc(sub) + '</span></span></button>';
    }).join('');
  }
  renderHomeMap();
  renderHomeDecisions();
  renderHomeThemes();
  renderHomeMaintenance();
  renderHomeFlow();
}
// --- Stage strip (design Section 5.6, unit HW-8): collection, ingestion, publication ---
// The strip is for the owner. Each count comes from a route that exists; a route that gives 404 (the federation routes while federation is off) or fails gives no count, and the stage shows its link only.
let homeFlowData = null, homeFlowKey = '', homeFlowSeq = 0;
function paintHomeFlow(){
  const nav = document.getElementById('home-flow'); if(!nav) return;
  if(AUTH_SCOPE !== 'owner'){ nav.hidden = true; return; }
  nav.hidden = false;
  const d = homeFlowData || {};
  const set = (id, parts) => { const el = document.getElementById(id); if(el) el.textContent = parts.filter(Boolean).join(' · '); };
  // FP4-5: the counts of the stage bar of /syndication (timeline items, mentions, published documents) come first.
  const tl = d.timeline;
  set('home-flow-collect-counts', [
    tl ? tr('home.flow.timeline', {count: tl.total}) : '',
    tl ? tr('home.flow.unread', {count: tl.unread}) : '',
    tl ? tr('home.flow.mentions', {count: tl.unread_mentions}) : '',
    d.feeds ? tr('home.flow.feeds', {count: d.feeds.count}) : '',
    // Without a timeline (federation off) the unread count is that of the feeds.
    d.feeds && !tl ? tr('home.flow.unread', {count: d.feeds.unread}) : '',
    d.following != null ? tr('home.flow.following', {count: d.following}) : ''
  ]);
  const jobs = (homeStats && Array.isArray(homeStats.active_jobs)) ? homeStats.active_jobs.filter(j => j && j.active).length : null;
  set('home-flow-ingest-counts', [
    (homeStats && homeStats.documents != null) ? tr('home.flow.documents', {count: homeStats.documents}) : '',
    jobs != null ? tr('home.flow.active_jobs', {count: jobs}) : ''
  ]);
  set('home-flow-publish-counts', [
    tl ? tr('home.flow.published', {count: tl.published}) : '',
    d.followers != null ? tr('home.flow.followers', {count: d.followers}) : '',
    d.pending != null ? tr('home.flow.pending', {count: d.pending}) : ''
  ]);
}
function renderHomeFlow(){
  const nav = document.getElementById('home-flow'); if(!nav) return;
  if(AUTH_SCOPE !== 'owner'){ nav.hidden = true; return; }
  paintHomeFlow();
  const key = String(activeThemeId || '0');
  if(homeFlowKey === key) return;
  homeFlowKey = key;
  const seq = ++homeFlowSeq;
  const getJson = url => fetch(url, {cache: 'no-store', credentials: 'same-origin'}).then(r => r.ok ? r.json() : null).catch(() => null);
  Promise.all([getJson('/api/feeds'), getJson('/api/federation/following?limit=1'), getJson('/api/federation/status'), getJson('/api/federation/timeline?limit=1')]).then(([feeds, following, status, timeline]) => {
    if(seq !== homeFlowSeq) return;
    const data = {};
    if(feeds && Array.isArray(feeds.feeds)) data.feeds = {count: feeds.feeds.length, unread: feeds.feeds.reduce((n, f) => n + (Number(f.unread) || 0), 0)};
    if(following && typeof following.total === 'number') data.following = following.total;
    if(status && status.followers && typeof status.followers.total === 'number') data.followers = status.followers.total;
    if(status && typeof status.pending_tasks === 'number') data.pending = status.pending_tasks;
    if(timeline && timeline.counts && typeof timeline.counts.total === 'number') data.timeline = timeline.counts;
    homeFlowData = data;
    if(centerView === 'home') paintHomeFlow();
  });
}
function loadHomeStats(){
  const seq = ++homeStatsSeq;
  fetch(apiUrl('stats'), {cache: 'no-store'}).then(r => r.ok ? r.json() : null).then(d => {
    if(seq !== homeStatsSeq || !d) return;
    homeStats = d;
    if(centerView === 'home') renderHome();
  }).catch(() => {});
}
(function(){
  const wrap = document.getElementById('homewrap');
  if(wrap) wrap.addEventListener('click', e => {
    const docRow = e.target.closest('[data-home-doc]');
    if(docRow){ selectDoc(docRow.dataset.homeDoc); return; }
    const entRow = e.target.closest('[data-home-ent]');
    if(entRow && entRow.dataset.homeEnt){ loadNode(entRow.dataset.homeEnt, true, false); return; }
    const copyBtn = e.target.closest('[data-home-copy]');
    if(copyBtn && copyBtn.dataset.homeCopy && navigator.clipboard && navigator.clipboard.writeText){
      navigator.clipboard.writeText(copyBtn.dataset.homeCopy).catch(() => {});
    }
  });
})();

// --- Hamburger menu sync hook ---
function syncManageButton(){
  const btn = document.getElementById('hamburger-btn');
  if(btn) btn.hidden = false;
}

// The content of the panel changes when the context changes (a node, a document, or the default hint), thus the open state of the context panel follows it.
if(panel && typeof MutationObserver === 'function'){
  new MutationObserver(() => syncWorkspaceLayout()).observe(panel, {childList: true});
}

window.claireDebug = {
  get allNodes(){ return allNodes; },
  get allEdges(){ return allEdges; },
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
  get contextOpen(){ return isContextOpen(); },
  get hasContext(){ return hasContext(); },
  get listMode(){ return listMode(); },
  get centerView(){ return centerView; },
  get allDocs(){ return allDocs; },
  openContextPanel: openContextPanel,
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
  init: tr("ws.cockpit.st_prepare"),
  fetch: tr("ws.cockpit.st_fetch"),
  extract: tr("ws.cockpit.st_extract"),
  resolve: tr("ws.cockpit.st_resolve"),
  detail: tr("ws.cockpit.st_detail"),
  done: tr("ws.cockpit.st_done"),
  error: tr("ws.cockpit.st_error")
};
const STAGE_FULL_TITLES = {
  init: tr("ws.cockpit.stage1"),
  fetch: tr("ws.cockpit.stage1"),
  extract: tr("ws.cockpit.stage2"),
  resolve: tr("ws.cockpit.stage3"),
  detail: tr("ws.cockpit.stage4"),
  done: tr("ws.cockpit.stage5"),
  error: tr("ws.cockpit.stage_error")
};
const STAGE_DESCS = {
  fetch: tr("ws.cockpit.sub1"),
  extract: tr("ws.cockpit.sub2"),
  resolve: tr("ws.cockpit.sub3"),
  detail: tr("ws.cockpit.sub4"),
  done: tr("ws.cockpit.sub5")
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
  ingest: tr("ws.ingest.title_short"),
  reingest: tr("ws.menu.reingest"),
  regenerate: tr("ws.menu.regen")
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
      kb_id: (typeof activeThemeId !== 'undefined' ? activeThemeId : 0),
      title: tr("ws.cockpit.ingest_running"),
      stage: 'extract',
      msg: tr("ws.cockpit.analysis_preparing")
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
  if(!job) return tr("ws.cockpit.elapsed_zero");
  if(job.started_at){
    const elapsedSec = Math.max(0, Math.floor((job.updated_at || (Date.now() / 1000)) - job.started_at));
    const mm = String(Math.floor(elapsedSec / 60)).padStart(2, '0');
    const ss = String(elapsedSec % 60).padStart(2, '0');
    return tr('ws.cockpit.elapsed', { time: mm + ':' + ss });
  }
  return job.active === false ? tr("ws.cockpit.st_done") : tr("ws.cockpit.tracking");
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
      icon = isError ? '✕' : '◐';
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
  const stageTitle = STAGE_FULL_TITLES[curStage] || STAGE_LABELS[curStage] || tr("ws.cockpit.in_progress_word");
  const msg = (job && job.msg) ? job.msg : tr("ws.cockpit.analysis_preparing");
  const pct = getJobPct(job);
  const curIdx = Math.max(0, STAGES.indexOf(curStage === 'init' ? 'fetch' : curStage));

  const logLines = [
    { idx: 0, text: tr("ws.cockpit.log1") },
    { idx: 1, text: tr("ws.cockpit.log2") },
    { idx: 2, text: tr("ws.cockpit.log3") },
    { idx: 3, text: tr("ws.cockpit.log4") }
  ].map(item => {
    if(item.idx < curIdx){
      return '<div class="cockpit-live-log-line done">✓ ' + esc(item.text) + ' ' + tr("ws.cockpit.log_done") + '</div>';
    }
    if(item.idx === curIdx){
      return '<div class="cockpit-live-log-line active">▶ ' + esc(msg) + '</div>';
    }
    return '<div class="cockpit-live-log-line">· ' + esc(item.text) + ' ' + tr("ws.cockpit.log_wait") + '</div>';
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
    + '    <small class="wave-sub">' + tr("ws.cockpit.tensor_hint") + '</small>'
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
  const kindTxt = KIND_LABELS[job.kind] || tr("ws.ingest.title_short");
  const themeTxt = job.theme_label || (tr("ws.jobs.theme_hash") + (job.kb_id !== undefined ? job.kb_id : 0));

  const statusBadge = document.getElementById('cockpit-status-badge');
  const statusText = document.getElementById('cockpit-status-text');
  if(statusBadge && statusText){
    statusBadge.className = 'cockpit-status-badge' + (isError ? ' error' : (isDone ? ' done' : ''));
    statusText.textContent = isError ? tr("ws.cockpit.status_error") : (isDone ? tr("ws.ingest.done") : tr("ws.cockpit.in_progress"));
  }

  const themeBadge = document.getElementById('cockpit-theme-badge');
  if(themeBadge) themeBadge.textContent = '' + themeTxt;

  const kindBadge = document.getElementById('cockpit-kind-badge');
  if(kindBadge) kindBadge.textContent = kindTxt + ' ' + tr("ws.cockpit.pipeline");

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
      ? tr('ws.cockpit.stage_done')
      : tr('ws.cockpit.stage_n', { n: Math.min(stages.length, curIdx + 1), total: stages.length, title: fullTitle });
  }
  const mobilePctEl = document.getElementById('cockpit-mobile-pct');
  if(mobilePctEl) mobilePctEl.textContent = pct + '%';
  const mobileFillEl = document.getElementById('cockpit-mobile-fill');
  if(mobileFillEl) mobileFillEl.style.width = pct + '%';

  const engineEl = document.getElementById('cockpit-telemetry-engine');
  if(engineEl) engineEl.textContent = tr('ws.cockpit.kind_worker', { kind: kindTxt });

  const metricsEl = document.getElementById('cockpit-telemetry-metrics');
  if(metricsEl){
    const stageShort = STAGE_LABELS[curStage] || curStage;
    const mat = job.heatmap_matrix;
    const secondPill = (mat && Array.isArray(mat.rows))
      ? (tr('ws.cockpit.matrix') + ' <strong>' + mat.rows.length + '×' + (mat.cols ? mat.cols.length : 0) + '</strong>')
      : (tr('ws.cockpit.progress') + ' <strong>' + pct + '%</strong>');
    metricsEl.innerHTML = '<span class="cockpit-metric-pill">' + tr('ws.cockpit.stage') + ' <strong>' + esc(stageShort) + '</strong></span>'
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
    titleEl.innerHTML = tr("ws.cockpit.title") + ' <small id="matrix-subtitle">| Ingest Cockpit (' + esc(job.title || tr("ws.cockpit.job_running")) + ')</small>';
  }
  const docMetaEl = document.getElementById('matrix-target-doc');
  if(docMetaEl){
    const kindTxt = KIND_LABELS[job.kind] || tr("ws.ingest.title_short");
    const themeTxt = job.theme_label || ('#' + (job.kb_id !== undefined ? job.kb_id : 0));
    docMetaEl.textContent = tr('ws.cockpit.target_full', { title: job.title || tr('home.recent.untitled'), theme: themeTxt, kind: kindTxt });
  }

  const spinner = document.getElementById('matrix-progress-spinner');
  if(spinner){
    spinner.style.display = job.active ? 'inline-block' : 'none';
  }

  const msgEl = document.getElementById('matrix-progress-msg');
  if(msgEl){
    msgEl.textContent = job.msg || (job.active ? tr('ws.cockpit.in_progress_short') : tr('ws.cockpit.done_short'));
  }

  updateCockpitChrome(job);

  if(job.heatmap_matrix){
    renderHeatmapMatrix(job.heatmap_matrix, job.title, job.active);
  } else {
    const theadRow = document.getElementById('matrix-thead-row');
    if(theadRow){
      theadRow.innerHTML = '<th style="min-width:140px">' + tr("ws.cockpit.matrix_corner") + '</th>';
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
    const curThemeStr = String(activeThemeId || '0');
    const curThemeObj = findTheme(curThemeStr);
    const themeMatch = jobs.find(j => {
      const jThemeStr = String(j.kb_id !== undefined ? j.kb_id : '0');
      const jThemeObj = findTheme(jThemeStr);
      if(curThemeObj && jThemeObj && curThemeObj === jThemeObj) return true;
      return jThemeStr === curThemeStr;
    });
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

function initIngestMatrixView(payload, themeLabel){
  const wrap = document.getElementById('matrixwrap');
  if(!wrap) return;
  const titleEl = document.getElementById('matrix-title');
  if(titleEl) titleEl.innerHTML = esc(tr('ws.cockpit.title')) + ' <small id="matrix-subtitle">' + esc(tr('ws.cockpit.subtitle')) + '</small>';
  const cleanPayload = (payload || '').replace(/\s+/g, ' ').trim();
  const shortDesc = cleanPayload.length > 80 ? cleanPayload.slice(0, 80) + '…' : cleanPayload;
  const docMetaEl = document.getElementById('matrix-target-doc');
  if(docMetaEl){
    docMetaEl.textContent = tr('ws.cockpit.target', { title: shortDesc }) + (themeLabel ? ' (' + tr('ws.ingest.opt_theme', { theme: themeLabel }) + ')' : '');
  }
  const spin = document.getElementById('matrix-progress-spinner');
  if(spin) spin.style.display = 'inline-block';
  const defaultMsg = tr('ws.cockpit.analysis_preparing');
  const msgEl = document.getElementById('matrix-progress-msg');
  if(msgEl) msgEl.textContent = defaultMsg;

  const synthJob = {
    active: true,
    kind: 'ingest',
    kb_id: (typeof activeThemeId !== 'undefined' ? activeThemeId : 0),
    theme_label: themeLabel || tr("ws.bar.theme_default"),
    title: shortDesc || tr("ws.cockpit.collect_target"),
    stage: 'fetch',
    progress: 20,
    msg: defaultMsg,
    heatmap_matrix: null
  };
  updateCockpitChrome(synthJob);

  const theadRow = document.getElementById('matrix-thead-row');
  if(theadRow){
    theadRow.innerHTML = '<th style="min-width:140px">' + tr("ws.cockpit.matrix_corner") + '</th>';
  }
  const tbody = document.getElementById('matrix-tbody');
  if(tbody){
    tbody.innerHTML = '<tr><td colspan="4" style="padding:0;border:0;">' + buildLiveStreamPlaceholderHtml(synthJob) + '</td></tr>';
  }
}

// True while the progress message of the cockpit is not a final message. The job messages come from the server and can be Korean (server text, not interface text).
const COCKPIT_SERVER_PROGRESS_WORDS = ['준비 중', '진행 중'];
function cockpitMsgInProgress(el){
  const text = el.textContent || '';
  if(!text) return true;
  return COCKPIT_SERVER_PROGRESS_WORDS.concat([tr('ws.cockpit.preparing'), tr('ws.cockpit.analysis_preparing'), tr('ws.cockpit.in_progress_short')]).some(word => text.includes(word));
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
    kb_id: (typeof activeThemeId !== 'undefined' ? activeThemeId : 0),
    theme_label: tr("ws.bar.theme_default"),
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
    document_title: ev.document_title || tr("ws.cockpit.live"),
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
      label: tr("ws.cockpit.lv_merge")
    };
  }
  if(numScore >= borderlineThresh){
    return {
      tier: 'borderline',
      bg: '#0284c7',
      border: '1.5px dashed #0369a1',
      fg: '#ffffff',
      label: tr("ws.cockpit.lv_judge")
    };
  }
  if(numScore >= 0.55){
    return {
      tier: 'high',
      bg: '#3b82f6',
      border: '1px solid #2563eb',
      fg: '#ffffff',
      label: tr("ws.cockpit.lv_high")
    };
  }
  if(numScore >= 0.40){
    return {
      tier: 'mid-high',
      bg: '#60a5fa',
      border: '1px solid #3b82f6',
      fg: '#ffffff',
      label: tr("ws.cockpit.lv_mid_high")
    };
  }
  if(numScore >= 0.25){
    return {
      tier: 'mid',
      bg: '#93c5fd',
      border: '1px solid #60a5fa',
      fg: '#1e3a8a',
      label: tr("ws.cockpit.lv_mid")
    };
  }
  if(numScore >= 0.15){
    return {
      tier: 'mid-low',
      bg: '#bfdbfe',
      border: '1px solid #93c5fd',
      fg: '#1e3a8a',
      label: tr("ws.cockpit.lv_mid_low")
    };
  }
  if(numScore >= 0.07){
    return {
      tier: 'low',
      bg: '#dbeafe',
      border: '1px solid #bfdbfe',
      fg: '#1e3a8a',
      label: tr("ws.cockpit.lv_low")
    };
  }
  return {
    tier: 'base',
    bg: '#eff6ff',
    border: '1px solid #e0e7ff',
    fg: '#64748b',
    label: tr("ws.cockpit.lv_base")
  };
}

function renderHeatmapMatrix(matrixData, title, isIngesting){
  if(!matrixData) return;
  currentMatrixData = matrixData;
  window.activeHeatmapMatrix = matrixData;

  if(title){
    const docMetaEl = document.getElementById('matrix-target-doc');
    if(docMetaEl){
      docMetaEl.innerHTML = tr("ws.cockpit.target_prefix") + ' <a href="#" class="matrix-doc-link" onclick="openMatrixTargetDoc();return false;" title="' + tr("ws.cockpit.read_doc") + '">' + esc(title) + '</a>';
    }
  }

  const rows = matrixData.rows || [];
  const cols = matrixData.cols || [];
  const mat = matrixData.matrix || [];
  const autoMergeThresh = matrixData.threshold_auto_merge || 0.93;
  const borderlineThresh = matrixData.threshold_borderline || 0.72;

  const theadRow = document.getElementById('matrix-thead-row');
  if(theadRow){
    theadRow.innerHTML = '<th class="matrix-corner-header" title="' + esc(tr('ws.cockpit.corner_title', { count: cols.length })) + '"><div class="matrix-corner-label">' + esc(tr('ws.cockpit.corner', { count: cols.length })) + '</div></th>' +
      cols.map(c => '<th class="matrix-col-header" title="' + esc(c) + '"><div class="matrix-col-label">' + esc(c.length > 8 ? c.slice(0, 7) + '…' : c) + '</div></th>').join('');
  }

  const tbody = document.getElementById('matrix-tbody');
  if(tbody){
    if(!rows.length || !cols.length){
      tbody.innerHTML = '<tr><td colspan="' + Math.max(1, cols.length + 1) + '" style="text-align:center;padding:32px 16px;color:var(--muted)">' + tr("ws.cockpit.no_candidates") + '</td></tr>';
      return;
    }

    // 애니메이션은 오직 적재 중 실시간으로 확률 색상을 부여할 때만 발동
    const isActivelyIngesting = (typeof isIngesting === 'boolean') ? isIngesting : !!(window.activeIngest && window.activeIngest.running);

    const spin = document.getElementById('matrix-progress-spinner');
    const msgEl = document.getElementById('matrix-progress-msg');
    if(!isActivelyIngesting){
      if(spin) spin.style.display = 'none';
      if(msgEl && cockpitMsgInProgress(msgEl)){
        msgEl.innerHTML = '<span data-icon="check-circle"></span> <strong>' + tr("ws.cockpit.compare_done") + '</strong> ' + tr("ws.cockpit.compare_done_detail");
      }
    } else {
      if(spin) spin.style.display = 'inline-block';
      if(msgEl && (!msgEl.textContent || msgEl.textContent === tr('ws.cockpit.preparing'))){
        msgEl.textContent = tr("ws.cockpit.live_compare");
      }
    }

    if(!activeJobs || activeJobs.length === 0){
      updateCockpitChrome({
        active: isActivelyIngesting,
        kind: 'ingest',
        kb_id: (typeof activeThemeId !== 'undefined' ? activeThemeId : 0),
        theme_label: tr("ws.bar.theme_default"),
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

        const tooltip = esc(rName) + ' ↔ ' + esc(cName) + '\n' + tr('ws.cockpit.similarity', { score: score.toFixed(4) }) + ' (' + vis.label + ')';

        return '<td><div class="' + cellClass + '" data-tier="' + vis.tier + '" title="' + tooltip + '" style="background:' + vis.bg + ';color:' + vis.fg + ';border:' + vis.border + ';' + animStyle + '" onmouseenter="inspectMatrixCell(' + jsArg(rName) + ',' + jsArg(cName) + ',' + score + ',' + jsArg(vis.label) + ')" onclick="inspectMatrixCell(' + jsArg(rName) + ',' + jsArg(cName) + ',' + score + ',' + jsArg(vis.label) + ')"></div></td>';
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
    msgEl.innerHTML = '<span data-icon="check-circle"></span> <strong>' + tr('ws.cockpit.done') + '</strong> (' + tr('ws.ingest.counts', { created: (result && result.entities_created)||0, linked: (result && result.entities_linked)||0, relations: (result && result.relations_added)||0 }) + ')';
  }
  updateCockpitChrome({
    active: false,
    kind: 'ingest',
    kb_id: (typeof activeThemeId !== 'undefined' ? activeThemeId : 0),
    theme_label: tr("ws.bar.theme_default"),
    title: (result && result.title) || '',
    stage: 'done',
    progress: 100,
    heatmap_matrix: (result && result.heatmap_matrix) || currentMatrixData
  });
  if(result && result.title){
    const docMetaEl = document.getElementById('matrix-target-doc');
    if(docMetaEl){
      docMetaEl.innerHTML = tr("ws.cockpit.target_prefix") + ' <a href="#" class="matrix-doc-link" onclick="openMatrixTargetDoc();return false;" title="' + tr("ws.cockpit.read_doc") + '">' + esc(result.title) + '</a>';
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
  const text = esc(rName) + ' ↔ ' + esc(cName) + ' · ' + tr('ws.cockpit.similarity', { score: Number(score).toFixed(4) }) + (label ? ' (' + label + ')' : '');
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
    return tr('ws.stream.timezone', { zone: tz || 'Local', offset: `UTC${sign}${offH}:${offM}` });
  } catch(_) {
    return tr("ws.stream.local_timezone");
  }
}
window.getBrowserTimezoneInfo = getBrowserTimezoneInfo;

function renderDecisionCardHtml(dec){
  const isMerge = dec.decision === 'MERGE';
  const isRolledBack = dec.decision === 'ROLLED_BACK' || dec.rolled_back;

  let badgeClass = 'badge-new';
  let badgeLabel = tr("ws.stream.kind_create");
  if(isMerge){
    badgeClass = 'badge-merge';
    badgeLabel = tr("ws.stream.kind_merge");
  } else if(isRolledBack){
    badgeClass = 'badge-rollback';
    badgeLabel = tr("ws.stream.kind_revert");
  } else if(dec.decision === 'CROSS_LINK'){
    badgeClass = 'badge-cross';
    badgeLabel = tr("ws.stream.kind_relation");
  }

  const scoreText = (dec.score !== undefined && dec.score !== null) ? ' · ' + tr('ws.dedup.similarity') + ' ' + Number(dec.score).toFixed(3) : '';
  const candText = dec.candidate ? ' ↔ ' + tr('ws.stream.candidate') + ' [' + esc(dec.candidate) + ']' : '';

  let html = '<div class="decision-card">';
  html += '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">';
  html += '<div style="display:flex;align-items:center;gap:8px">';
  html += '<strong style="font-size:14px;color:var(--fg)"><span data-icon="tag"></span> ' + esc(dec.entity || '') + '</strong>';
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
    html += '<span style="margin-right:4px"><span data-icon="info"></span></span>' + esc(dec.reason);
    html += '</div>';
  }

  html += '<div style="display:flex;justify-content:space-between;align-items:center;padding-top:6px;border-top:1px dashed var(--border);font-size:11.5px;color:var(--muted)">';
  if(dec.document_id){
    html += '<span><span data-icon="doc"></span> ' + tr('ws.stream.source') + ' <a href="javascript:void(0)" onclick="selectDoc(' + jsArg(dec.document_id) + ');setCenterView(\'reader\')" style="color:var(--accent,#0284c7);text-decoration:underline">' + esc(dec.document_title || dec.document_id) + '</a></span>';
  } else {
    html += '<span></span>';
  }
  if(isMerge && !isRolledBack){
    html += '<button type="button" class="sec" disabled title="' + tr("ws.stream.rollback_off_title") + '" style="font-size:11px;padding:3px 8px;color:var(--muted);border-color:var(--border);opacity:0.5;cursor:not-allowed;">' + tr("ws.stream.rollback_disabled") + '</button>';
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
    if(listEl && !appendOnly) listEl.innerHTML = '<p class="hint" style="padding:40px;text-align:center">' + tr("ws.stream.loading_records") + '</p>';
    try {
      const r = await fetch(apiUrl('resolution/decisions?limit=500'));
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
    statEl.textContent = tr('ws.stream.shown', { shown: visible.length, total: total });
  }

  const fBar = document.getElementById('stream-filter-bar');
  if(fBar){
    const totalCount = cachedDecisions.length;
    const mergeCount = cachedDecisions.filter(d => d.decision === 'MERGE').length;
    const newCount = cachedDecisions.filter(d => d.decision === 'CREATE_NEW').length;
    const rollbackCount = cachedDecisions.filter(d => d.decision === 'ROLLED_BACK' || d.rolled_back).length;
    fBar.innerHTML = '<button type="button" class="sec' + (currentDecisionFilter==='ALL'?' active':'') + '" onclick="filterDecisionStreamCenter(\'ALL\')">' + tr("ws.stream.all") + ' (' + totalCount + ')</button>' +
      '<button type="button" class="sec' + (currentDecisionFilter==='MERGE'?' active':'') + '" onclick="filterDecisionStreamCenter(\'MERGE\')">' + tr("ws.stream.merge") + ' (' + mergeCount + ')</button>' +
      '<button type="button" class="sec' + (currentDecisionFilter==='CREATE_NEW'?' active':'') + '" onclick="filterDecisionStreamCenter(\'CREATE_NEW\')">' + tr("ws.stream.new") + ' (' + newCount + ')</button>' +
      '<button type="button" class="sec' + (currentDecisionFilter==='ROLLED_BACK'?' active':'') + '" onclick="filterDecisionStreamCenter(\'ROLLED_BACK\')">' + tr("ws.stream.rolled_back") + ' (' + rollbackCount + ')</button>';
  }

  if(!visible.length){
    listEl.innerHTML = '<p class="hint" style="padding:40px;text-align:center">' + tr("ws.stream.empty") + '</p>';
    if(sentinelEl) sentinelEl.textContent = '';
    return;
  }

  listEl.innerHTML = visible.map(d => renderDecisionCardHtml(d)).join('');

  if(sentinelEl){
    if(visible.length < total){
      sentinelEl.innerHTML = '<span>' + tr('ws.stream.scroll_more', { count: total - visible.length }) + '</span>';
    } else {
      sentinelEl.innerHTML = '<span>' + tr('ws.stream.all_seen', { count: total }) + '</span>';
    }
  }
}

function renderDecisionStreamPanel(){
  const totalCount = cachedDecisions.length;
  let filtered = getFilteredDecisions();
  let ph = '<div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:6px">';
  ph += '<h2><span data-icon="list"></span> ' + tr("ws.view.stream") + '</h2>';
  ph += '<span style="font-size:11px;color:var(--muted)">' + tr('ws.stream.total', { count: totalCount }) + '</span>';
  ph += '</div>';
  ph += '<p class="al"><small>' + tr("ws.stream.intro") + '</small></p>';

  if(!filtered.length){
    ph += '<p class="hint" style="padding:16px 0;text-align:center">' + tr("ws.stream.empty") + '</p>';
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
    title = window.latestHeatmapDocTitle || tr("ws.cockpit.recent");
  }

  if(!mat){
    return;
  }

  const titleEl = document.getElementById('matrix-title');
  if(titleEl){
    titleEl.innerHTML = tr("ws.cockpit.title") + ' <small id="matrix-subtitle">| Ingest Cockpit (' + esc(title || tr("ws.presentation.fullscreen")) + ')</small>';
  }

  renderHeatmapMatrix(mat, title, false);
  const spin = document.getElementById('matrix-progress-spinner');
  if(spin) spin.style.display = 'none';
  const msgEl = document.getElementById('matrix-progress-msg');
  if(msgEl){
    msgEl.innerHTML = '<span data-icon="check-circle"></span> <strong>' + tr("ws.cockpit.compare_done") + '</strong> ' + tr("ws.cockpit.compare_done_detail");
  }
  setCenterView('matrix');
}

async function triggerRollback(docId, entity){
  alert(tr("ws.stream.rollback_off_hint"));
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

  const confirmBtnHtml = '<button type="button" class="pri matrix-purge-btn" onclick="confirmPurgeMatrix(' + jsArg(docId) + ')" style="padding:4px 10px;font-size:11px;font-weight:bold;background:var(--accent,#0284c7);color:#fff;border:none;border-radius:4px;cursor:pointer;" title="' + tr("ws.cockpit.confirm_title") + '">' + tr("ws.cockpit.confirm") + '</button>';

  return '<div id="doc-matrix-banner-' + esc(docId) + '" class="matrix-preview-banner" style="margin-bottom:16px;padding:12px 14px;background:var(--card-bg);border:2px solid var(--accent,#0284c7);border-radius:8px;">' +
    '<div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px;">' +
      '<div>' +
        '<strong style="color:var(--accent,#0284c7);font-size:13px;display:inline-flex;align-items:center;gap:5px"><span><span data-icon="bolt"></span></span> ' + tr("ws.cockpit.compare_title") + '</strong>' +
        '<p style="font-size:11.5px;color:var(--muted);margin:2px 0 0">' + tr("ws.cockpit.compare_intro") + '</p>' +
      '</div>' +
      '<div style="display:flex;align-items:center;gap:6px;">' +
        '<button type="button" class="sec" onclick="openHeatmapMatrix(' + jsArg(docId) + ')" style="padding:4px 8px;font-size:11px;"><span data-icon="search"></span> ' + tr("ws.cockpit.show") + '</button>' +
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
      const res = await fetch(apiUrl('document/matrix/purge'), {
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
    setAccountStorage('doc_matrix_confirmed_' + docId, '1');
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
window.shouldDefaultToReader = shouldDefaultToReader;
window.isPwaOrMobileLaunch = isPwaOrMobileLaunch;
window.switchView = typeof switchView === 'function' ? switchView : setCenterView;

document.addEventListener('visibilitychange', () => {
  if(document.visibilityState === 'visible'){
    pollForUpdates();
  }
});

// Open command palette from the hamburger menu (HW-7).
function openCommandPaletteFromMenu(){
  if(window.ClaireAppBar) window.ClaireAppBar.closePopovers();
  if(window.ClaireCommandPalette) window.ClaireCommandPalette.open();
}
window.openCommandPaletteFromMenu = openCommandPaletteFromMenu;

if(window.ClaireIngestDialogs){
  window.ClaireIngestDialogs.init({
    openIngest: openIngest,
    submitIngest: runIngest,
    openReingest: openReingest,
    submitReingest: runReingest,
    openRegen: openRegen,
    submitRegen: runRegen
  });
}

if(window.ClaireCommandPalette){
  window.ClaireCommandPalette.init({
    dialogId: 'cmdpalette',
    getCommands: () => [
      {id: 'home', icon: '⌂', iconName: 'home', label: tr('nav.palette.cmd_home'), keywords: 'home overview', run: () => enterHome({reason: 'nav'})},
      {id: 'graph', icon: '›', iconName: 'graph', label: tr('nav.palette.cmd_graph'), keywords: 'graph map', run: () => setCenterView('graph')},
      {id: 'ingest', icon: '+', iconName: 'plus', label: tr('nav.palette.cmd_ingest'), keywords: 'ingest add url text', run: () => openIngest()},
      {id: 'light', icon: '›', iconName: 'sun', label: tr('nav.palette.cmd_theme_light'), keywords: 'light theme', run: () => { if(curTheme() !== 'light') toggleTheme(); }},
      {id: 'dark', icon: '›', iconName: 'moon', label: tr('nav.palette.cmd_theme_dark'), keywords: 'dark theme', run: () => { if(curTheme() !== 'dark') toggleTheme(); }}
    ],
    getQueryActions: q => [{id: 'fulltext', icon: '›', iconName: 'search', label: tr('nav.palette.cmd_fulltext', {query: q}), run: () => runFullTextSearch(q)}],
    getListFilter: () => {
      const qEl = document.getElementById('q');
      return (qEl && listMode() === 'docs') ? String(qEl.value || '').trim() : '';
    },
    getCachedDocuments: query => {
      const q = String(query || '').trim().toLowerCase();
      const src = Array.isArray(allDocs) ? allDocs : [];
      if(!q) return src.slice(0, 10);
      return src.filter(d => String((d && (d.title || d.id)) || '').toLowerCase().includes(q)).slice(0, 10);
    },
    fetchDocuments: async query => {
      const sp = new URLSearchParams({limit: '10', hidden: '0'});
      const q = String(query || '').trim();
      const kw = q.toLowerCase();
      if(kw === 'is:missing-detail' || kw === 'missing:detail') sp.set('detail', 'missing');
      else if(kw === 'is:mismatched' || kw === 'format:mismatch') sp.set('detail', 'mismatched');
      else if(q) sp.set('q', q);
      const data = await fetchDocuments(sp);
      return (data && data.documents) || [];
    },
    getEntities: () => (allNodes && typeof allNodes.get === 'function') ? allNodes.get() : (Array.isArray(allNodes) ? allNodes : []),
    onSelectDocument: d => {
      if(!d || !d.id) return;
      openReader(d.id);
      setCenterView('reader');
    },
    onSelectEntity: n => {
      if(!n || !n.id) return;
      loadNode(n.id);
    }
  });
}

