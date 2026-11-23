/**
 * Claire Bible - Document Reader & Modal Viewer
 * Manages document reading view, heading navigation rail, STT modal, font sizing, and sharing.
 */

// Catalog text (static/js/i18n.js). Without the runtime, the key shows. The name is not ``tr``: app.js declares ``tr`` in the same bundle.
function rtr(key, params) {
  return (typeof ClaireI18n !== 'undefined' && ClaireI18n) ? ClaireI18n.t(key, params) : key;
}

function readerApiUrl(path) {
  var cleanPath = String(path || '').replace(/^\/+/, '');
  if (typeof apiUrl === 'function') {
    return apiUrl(cleanPath);
  }
  if (typeof window !== 'undefined' && typeof window.apiUrl === 'function') {
    return window.apiUrl(cleanPath);
  }
  var meta = typeof document !== 'undefined' ? document.querySelector('meta[name="claire-api-base"]') : null;
  var base = meta ? meta.getAttribute('content') : '';
  if (base) {
    return base.replace(/\/+$/, '') + '/' + cleanPath;
  }
  return cleanPath;
}

// 목록 설명 줄수(0/2/4) — 브라우저에 기억. 문서 많아지면 제목/설명이 height 를 너무
// 차지한다는 피드백(사용자 지적) → #docs 에 lc0/lc4 클래스로 CSS line-clamp 토글(기본 2줄).
let descLines = 3;
try{ const v=parseInt(localStorage.getItem('claireDescLines')); if(v===0||v===3||v===4||v===2) descLines=(v===0?0:3); }catch(e){}
function doclistToolbarHtml(){
  return '<div class="doclist-toolbar">'+
    '<select id="desclines" onchange="setDescLines(this.value)" title="' + rtr("ws.list.desc_lines") + '" aria-label="' + rtr("ws.list.desc_lines") + '">'+
    '<option value="0"'+(descLines===0?' selected':'')+'>' + rtr("ws.list.titles_only") + '</option>'+
    '<option value="3"'+(descLines===3?' selected':'')+'>' + rtr("ws.list.with_summary") + '</option>'+
    '</select></div>';
}
function applyDescLines(){
  const docs=document.getElementById('docs'); if(!docs) return;
  docs.classList.remove('lc0','lc3','lc4');
  if(descLines===0) docs.classList.add('lc0');
  else docs.classList.add('lc3');
  const sel=document.getElementById('desclines'); if(sel) sel.value=String(descLines===0?0:3);
}
function setDescLines(v){
  descLines = parseInt(v)===0 ? 0 : 3;
  try{ localStorage.setItem('claireDescLines', descLines); }catch(e){}
  applyDescLines();
}
applyDescLines();

// 읽기 글자 크기(A−/A+) — 브라우저에 기억. 팝업의 --read-fs 변수로 .doc-content 본문에 적용.
let readFS = 16;
try{ const v=parseInt(localStorage.getItem('claireReadFS')); if(v>=12 && v<=28) readFS=v; }catch(e){}
function applyReadFS(){
  const r=document.getElementById('reader'); if(r) r.style.setProperty('--read-fs', readFS+'px');
  const v=document.getElementById('rfs'); if(v) v.textContent=readFS;
}
function setReadFS(delta){
  readFS = Math.max(12, Math.min(28, readFS + delta));
  try{ localStorage.setItem('claireReadFS', readFS); }catch(e){}
  applyReadFS();
  if(typeof updateReaderRail === 'function') updateReaderRail();
}

// 중앙 읽기 — 좌측 문서의 '읽기' 버튼/노드 상세의 본문 보기 단추로 연다(nav 와 분리, 사용자 요구).
let curReaderDoc=null;   // 현재 읽기 팝업의 문서 id(공유 링크 생성 대상)
let curReaderDocData=null; // 현재 읽기 팝업의 문서 객체
let readerReturnFocus=null, readerReturnDocId=null;
// /p/presentation has no namespace: the parameter kb (number) selects the knowledge base (design Section 5.2 rule 2).
function readerKbQuery(){
  const n = (typeof window.currentKbNumber === 'function') ? window.currentKbNumber() : '';
  return n ? '&kb=' + encodeURIComponent(n) : '';
}
function setReaderBackgroundInert(on){
  if(mobileMQ.matches){
    const el=document.getElementById('bar'); if(el) el.inert=on;
  }
}
function readerFocusable(){
  return [...document.querySelectorAll('#reader button:not([disabled]),#reader a[href],'+
    '#reader input:not([disabled]),#reader [tabindex]:not([tabindex="-1"])')]
    .filter(el=>el.getClientRects().length>0);
}

function _scrollReaderBy(el, top, left, behavior){
  if(!el) return;
  if(typeof el.scrollBy === 'function'){
    try{ el.scrollBy({ top: top, left: left, behavior: behavior || 'auto' }); return; }catch(_){}
  }
  if(top) el.scrollTop = (el.scrollTop || 0) + top;
  if(left) el.scrollLeft = (el.scrollLeft || 0) + left;
}

function _scrollReaderTo(el, top, left, behavior){
  if(!el) return;
  if(typeof el.scrollTo === 'function'){
    try{ el.scrollTo({ top: top, left: left, behavior: behavior || 'auto' }); return; }catch(_){}
  }
  if(top !== undefined) el.scrollTop = top;
  if(left !== undefined) el.scrollLeft = left;
}

function setupReaderAccessibleBlocks(container){
  if(!container || typeof container.querySelectorAll !== 'function') return;
  const blocks = container.querySelectorAll('table, pre, .mathblock');
  blocks.forEach(el => {
    if(!el.hasAttribute('tabindex')) el.setAttribute('tabindex', '0');
    if(!el.hasAttribute('role')) el.setAttribute('role', 'region');
    if(!el.hasAttribute('aria-label')){
      const label = el.tagName === 'TABLE' ? rtr("reader.block.table") : (el.tagName === 'PRE' ? rtr("reader.block.code") : rtr("reader.block.math"));
      el.setAttribute('aria-label', label);
    }
  });
}

function handleReaderKey(e){
  const r=document.getElementById('reader');
  if(!r) return false;

  const isMobileOpen = r.classList && r.classList.contains('open');
  const isDesktopOpen = typeof centerView !== 'undefined' && centerView === 'reader' && (typeof mobileMQ === 'undefined' || !mobileMQ.matches);
  if(!isMobileOpen && !isDesktopOpen) return false;

  const activeEl = document.activeElement;
  const isInput = activeEl && (
    activeEl.tagName === 'INPUT' ||
    activeEl.tagName === 'TEXTAREA' ||
    activeEl.tagName === 'SELECT' ||
    activeEl.isContentEditable
  );
  if(isInput) return false;

  const docsPane = document.getElementById('docs');
  if(docsPane && typeof docsPane.contains === 'function' && docsPane.contains(activeEl)) return false;
  const detailPane = document.getElementById('detailpane');
  if(detailPane && typeof detailPane.contains === 'function' && detailPane.contains(activeEl)) return false;

  if(e.key==='Escape' && isMobileOpen){
    e.preventDefault();
    closeReader(false, true);
    return true;
  }

  if(e.key==='Tab' && isMobileOpen){
    const items=readerFocusable();
    if(!items.length){ e.preventDefault(); r.querySelector('.sheet')?.focus(); return true; }
    const first=items[0], last=items[items.length-1];
    if(e.shiftKey && (activeEl===first || activeEl===r.querySelector('.sheet'))){
      e.preventDefault(); last.focus(); return true;
    }else if(!e.shiftKey && activeEl===last){
      e.preventDefault(); first.focus(); return true;
    }
    return true;
  }

  const rbody = document.getElementById('rbody');
  if(!rbody) return false;

  if(e.key === ' ' && activeEl && (activeEl.tagName === 'BUTTON' || activeEl.tagName === 'A')){
    return false;
  }

  if(e.key === ' ' || e.key === 'PageDown' || e.key === 'PageUp'){
    e.preventDefault();
    const clientH = rbody.clientHeight || (typeof window !== 'undefined' && window.innerHeight ? window.innerHeight * 0.75 : 600);
    const scrollStep = Math.max(120, Math.round(clientH * 0.85));
    const isUp = (e.key === 'PageUp' || (e.key === ' ' && e.shiftKey));
    _scrollReaderBy(rbody, isUp ? -scrollStep : scrollStep, 0, 'smooth');
    return true;
  }

  if(e.key === 'ArrowDown' || e.key === 'ArrowUp'){
    if(activeEl && activeEl !== rbody && typeof rbody.contains === 'function' && rbody.contains(activeEl) && activeEl.scrollHeight > activeEl.clientHeight){
      _scrollReaderBy(activeEl, e.key === 'ArrowDown' ? 60 : -60, 0, 'auto');
      e.preventDefault();
      return true;
    }
    e.preventDefault();
    _scrollReaderBy(rbody, e.key === 'ArrowDown' ? 60 : -60, 0, 'auto');
    return true;
  }

  if(e.key === 'ArrowLeft' || e.key === 'ArrowRight'){
    const hStep = e.key === 'ArrowRight' ? 50 : -50;
    if(activeEl && activeEl !== rbody && typeof rbody.contains === 'function' && rbody.contains(activeEl) && activeEl.scrollWidth > activeEl.clientWidth){
      _scrollReaderBy(activeEl, 0, hStep, 'auto');
      e.preventDefault();
      return true;
    }
    if(rbody.scrollWidth > rbody.clientWidth){
      _scrollReaderBy(rbody, 0, hStep, 'auto');
      e.preventDefault();
      return true;
    }
    if(typeof rbody.querySelectorAll === 'function'){
      const rbodyRect = (typeof rbody.getBoundingClientRect === 'function')
        ? rbody.getBoundingClientRect()
        : { top: 0, bottom: (typeof window !== 'undefined' && window.innerHeight) ? window.innerHeight : 800 };
      const scrollables = Array.from(rbody.querySelectorAll('table, pre, .mathblock')).filter(el => {
        if(el.scrollWidth <= el.clientWidth) return false;
        if(typeof el.getBoundingClientRect !== 'function') return true;
        const rect = el.getBoundingClientRect();
        return rect.bottom > rbodyRect.top && rect.top < rbodyRect.bottom;
      });
      if(scrollables.length > 0){
        _scrollReaderBy(scrollables[0], 0, hStep, 'auto');
        e.preventDefault();
        return true;
      }
    }
    return false;
  }

  if(e.key === 'Home' || e.key === 'End'){
    e.preventDefault();
    const targetTop = (e.key === 'Home' ? 0 : (rbody.scrollHeight || 100000));
    _scrollReaderTo(rbody, targetTop, 0, 'smooth');
    return true;
  }

  if((e.key === 'p' || e.key === 'P') && !e.ctrlKey && !e.metaKey && !e.altKey){
    e.preventDefault();
    setCenterView('presentation');
    return true;
  }

  return false;
}
async function markDocumentSeen(docId){
  if(!canWrite()) return;
  try{
    const r=await fetch(readerApiUrl('document/seen'),{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({id:docId})});
    if(r.status===401||r.status===404){ expireWriteAccess(); return; }
    if(!r.ok) return;
    const dc=allDocs && allDocs.find(d=>d.id===docId);
    if(dc && dc.seen!==1){ dc.seen=1; renderDocs(document.getElementById('docq').value); }
  }catch(_){}
}
// The list filter (#q) filters the list below it. Its text and its function follow the list (documents or entities), not the center view.
function updateCenterSearchMode(){
  const qEl = document.getElementById('q');
  const qclear = document.getElementById('qclear');
  if(!qEl) return;
  if(qclear) qclear.style.display = qEl.value ? 'flex' : 'none';
  const t = (key) => (window.ClaireI18n ? ClaireI18n.t(key) : key);
  const ent = (typeof listMode === 'function') && listMode() === 'entities';
  const isAuthed = (typeof AUTH_SCOPE !== 'undefined' && AUTH_SCOPE !== 'anonymous' && AUTH_SCOPE !== 'unknown');
  const key = ent ? 'list.filter.entities' : 'list.filter.documents';
  qEl.dataset.i18nPlaceholder = key;
  qEl.placeholder = t(key);
  qEl.title = t(ent ? 'list.filter.entities_tip' : (isAuthed ? 'list.filter.documents_tip_ai' : 'list.filter.documents_tip'));
}
window.updateCenterSearchMode = updateCenterSearchMode;

function setCenterView(mode){
  if(mode === 'graph'){
    if(typeof resumeGraphAfterIngest === 'function'){
      resumeGraphAfterIngest();
    }
  }
  const nextView = (mode==='graph' ? 'graph' : (mode==='matrix' ? 'matrix' : (mode==='stream' ? 'stream' : (mode==='presentation' ? 'presentation' : (mode==='home' ? 'home' : 'reader')))));
  if(mode === 'matrix' || mode === 'graph' || mode === 'stream' || mode === 'presentation' || mode === 'reader' || mode === 'home'){
    activePane = 'graph';
    document.body.dataset.activePane = 'graph';
  }
  if(centerView === nextView){
    if(centerView === 'presentation'){
      updatePresentationView();
    }
    if(centerView === 'reader' && mobileMQ.matches){
      const docId = curReaderDoc || activeDoc || (allDocs && allDocs.length ? allDocs[0].id : null);
      if(docId && curReaderDoc !== docId && typeof openReader === 'function'){
        openReader(docId);
      }
    }
    return;
  }
  centerView = nextView;
  document.body.dataset.centerView = centerView;
  // The list goes back to the list of the view. A selection of the user stays only until the next change of the view.
  if(typeof listOverride !== 'undefined') listOverride = null;
  if(typeof syncListPane === 'function') syncListPane();
  updateCenterSearchMode(centerView);
  const mt = document.getElementById('menu-section-title');
  if(mt){ mt.textContent = (centerView==='graph' ? rtr("reader.menu.graph") : (centerView==='matrix' ? rtr("reader.menu.matrix") : (centerView==='stream' ? rtr("ws.view.stream") : (centerView==='presentation' ? rtr("ws.view.presentation") : rtr("ws.menu.section_title"))))); }
  // The view buttons are a navigation, not a tab list: the active one has aria-current.
  ['home','reader','graph','presentation','stream'].forEach(name => {
    const btn = document.getElementById('centertab-' + name);
    if(!btn) return;
    btn.classList.toggle('active', centerView === name);
    if(centerView === name) btn.setAttribute('aria-current', 'page'); else btn.removeAttribute('aria-current');
  });
  if(typeof syncManageButton === 'function') syncManageButton();
  if(centerView==='home'){
    document.title = typeof HOME_TITLE !== 'undefined' ? HOME_TITLE : rtr('home.title');
    if(typeof renderHome === 'function') renderHome();
    if(typeof loadHomeStats === 'function') loadHomeStats();
  }
  if(typeof syncWorkspaceLayout === 'function') syncWorkspaceLayout();
  // Update entity detail panel when an entity is selected and center view changes.
  if(selectedNodeId && typeof currentNodeData !== 'undefined' && currentNodeData && typeof renderPanel === 'function'){
    renderPanel(currentNodeData);
  }
  if(centerView==='reader'){
    // Show document context when returning to reader view without an entity selection.
    if(!selectedNodeId){
      const docId = (typeof curReaderDoc !== 'undefined' && curReaderDoc) || activeDoc;
      if(docId){
        if(typeof curReaderDocData !== 'undefined' && curReaderDocData && curReaderDocData.id === docId && typeof renderDocPanel === 'function'){
          renderDocPanel(curReaderDocData);
        } else if(typeof loadDocPanel === 'function'){
          loadDocPanel(docId);
        }
      } else {
        const p = document.getElementById('panel');
        if(p && typeof defaultHint === 'function') p.innerHTML = defaultHint();
      }
    }
    if(mobileMQ.matches){
      const docId = curReaderDoc || activeDoc || (allDocs && allDocs.length ? allDocs[0].id : null);
      if(docId && curReaderDoc !== docId && typeof openReader === 'function'){
        openReader(docId);
      }
    }
    if(typeof window !== 'undefined' && typeof window.requestAnimationFrame === 'function'){
      window.requestAnimationFrame(() => updateReaderRail());
    } else {
      updateReaderRail();
    }
  }
  if(centerView==='presentation'){
    updatePresentationView();
  }
  if(centerView==='stream'){
    if(typeof renderDecisionStreamCenter === 'function'){
      renderDecisionStreamCenter();
    }
  }
  if(centerView==='graph'){
    graphCamera = null;
    if(!selectedNodeId && typeof renderGraphInsights === 'function'){
      renderGraphInsights();
    }
    requestAnimationFrame(()=>{
      relayout(true);
      applyTouchMode();
      if(activeDoc){
        const docId = activeDoc;
        const ids = [];
        allNodes && allNodes.forEach(n => {
          if(!n.hidden && (n.sources || []).includes(docId)) ids.push(n.id);
        });
        if(ids.length) net && net.selectNodes(ids);
        if(!ids.length) resetGraphCamera(); else cameraToNodes(ids);
      } else {
        fitGraphContext();
      }
    });
  }
}
function openDocGraph(docId){
  const targetId = docId || activeDoc || curReaderDoc || null;
  if(targetId){
    activeDoc = targetId;
    curReaderDoc = targetId;
    recordSelectedDoc(targetId);
  }
  setCenterView('graph');
  revealWorkspace('graph');
  if(targetId) setActiveDoc(targetId);
  if(mobileMQ.matches && typeof closeReader === 'function'){
    closeReader(false, false);
  }
  if(drawerOpen || detailOpen){
    if(compactMQ.matches || mobileMQ.matches) closeDrawer(false, false);
  }
}
function openReader(docId, pushHist=true){
  hideNodePop();
  if(!docId && allDocs && allDocs.length) docId = allDocs[0].id;
  if(!docId) return;
  curReaderDoc=docId;
  activeDoc=docId;
  recordSelectedDoc(docId);
  selectedNodeId=null;                          // 문서 모드로 전환 — 노드 inspect 해제
  if(typeof contextPinned !== 'undefined') contextPinned = false;
  readerReturnFocus=document.activeElement;
  readerReturnDocId=docId;
  const sb=document.getElementById('sharebox'); if(sb){ sb.className='sharebox'; sb.innerHTML=''; }  // 이전 공유링크 닫기
  applyReadFS();   // 저장된 글자 크기 적용
  document.getElementById('rtitle').textContent=rtr("ws.reader.loading");
  document.getElementById('rbody').innerHTML='';
  if(panel) panel.innerHTML='<p class=hint>' + rtr("ws.reader.loading") + '</p>';
  const r=document.getElementById('reader');
  r.setAttribute('aria-hidden','false');
  r.setAttribute('aria-busy','true');
  if(drawerOpen || detailOpen){
    if(compactMQ.matches || mobileMQ.matches) closeDrawer(false, false);
  }
  if(typeof docDrawerOpen !== 'undefined' && docDrawerOpen){
    closeDocDrawer(false);
  }
  if(centerView === 'presentation'){
    updatePresentationView(docId);
  } else {
    setCenterView('reader');
  }
  if(typeof syncWorkspaceLayout === 'function') syncWorkspaceLayout();
  renderDocs(document.getElementById('docq') ? document.getElementById('docq').value : '');
  applyView();
  if(activeDoc){
    const targetDocId=activeDoc;
    requestAnimationFrame(()=>{ if(net && activeDoc===targetDocId){
      const ids=[]; allNodes.forEach(n=>{ if(!n.hidden && (n.sources||[]).includes(targetDocId)) ids.push(n.id); });
      if(ids.length) net.selectNodes(ids);
      if(!ids.length) resetGraphCamera(); else cameraToNodes(ids);
    }});
  }
  fetch(readerApiUrl('document?id=' + encodeURIComponent(docId))).then(x=>x.json()).then(dc=>{
    if(!dc || dc.error){
      document.getElementById('rbody').innerHTML='<p class=hint>' + rtr("ws.reader.not_found") + '</p>';
      r.setAttribute('aria-busy','false');
      if(activeDoc===docId && panel) panel.innerHTML='<p class=hint>' + rtr("ws.reader.not_found") + '</p>';
      return;
    }
    renderReader(dc);
    if(activeDoc===docId) renderDocPanel(dc);
    markDocumentSeen(docId);
  }).catch(()=>{
    document.getElementById('rbody').innerHTML='<p class=hint>' + rtr("ws.reader.load_failed") + '</p>';
    r.setAttribute('aria-busy','false');
    if(activeDoc===docId && panel) panel.innerHTML='<p class=hint>' + rtr("ws.reader.load_failed") + '</p>';
  });
}
function docMetaHtml(dc){
  if(!dc) return '';
  const options = typeof arguments[1] === 'boolean' ? { showOrigin: arguments[1] } : (arguments[1] || {});
  const showOrigin = options.showOrigin !== false;
  const isWebUrl = u => !!u && /^https?:\/\//i.test(String(u).trim());
  const hasUrl = isWebUrl(dc.url);
  const canAccessRaw = (typeof canIngest === 'function' ? canIngest() : false) || Boolean(dc.can_access_raw);
  const isTrunc = !!(dc.raw_truncated || (dc.meta && dc.meta.raw_truncated));
  const isAppTrunc = isTrunc && !!(dc.appendix_truncated || (dc.meta && dc.meta.appendix_truncated));
  const isRefTrunc = isTrunc && !!(dc.references_truncated || (dc.meta && dc.meta.references_truncated));
  const isParserFallback = !!(dc.pdf_parser_fallback || (dc.meta && dc.meta.pdf_parser_fallback));
  const fallbackReason = (dc.pdf_parser_fallback_reason || (dc.meta && dc.meta.pdf_parser_fallback_reason) || rtr("reader.tag.parser_runtime_error"));
  const parserRequested = (dc.pdf_parser_requested || (dc.meta && dc.meta.pdf_parser_requested) || '');
  const parserUsed = (dc.pdf_parser_used || (dc.meta && dc.meta.pdf_parser_used) || '');
  const isEncodingFlaw = !!(dc.pdf_encoding_flaw_detected || (dc.meta && dc.meta.pdf_encoding_flaw_detected));
  const encodingFlaws = (dc.pdf_encoding_flaws || (dc.meta && dc.meta.pdf_encoding_flaws) || []);
  const isScanned = !!(dc.pdf_is_scanned || (dc.meta && dc.meta.pdf_is_scanned));
  const isStt = !!(dc.is_stt || (dc.meta && (dc.meta.is_stt || dc.meta.stt_applied || dc.meta.stt)));
  const isSttTrunc = isStt && !!(dc.stt_truncated || (dc.meta && dc.meta.stt_truncated) || isTrunc);
  const hasTranscript = !!(dc.has_transcript || (dc.meta && dc.meta.has_transcript));
  const isCc = !isStt && (hasTranscript || !!(dc.meta && (dc.meta.caption_status === 'available' || (dc.meta.transcript_source && dc.meta.transcript_source !== 'stt'))));
  const isVlm = !!(dc.vlm_transcribed || (dc.meta && dc.meta.vlm_transcribed));
  const presentation = dc.presentation_pdf || (dc.meta && dc.meta.presentation_pdf) || {};
  const hasPresentation = presentation.status === 'available' && isWebUrl(presentation.public_url);
  const origChars = (dc.orig_chars || (dc.meta && dc.meta.orig_chars) || dc.stt_orig_chars || (dc.meta && dc.meta.stt_orig_chars)) || 0;
  const rawChars = (dc.raw_chars || (dc.meta && dc.meta.raw_chars) || dc.stt_raw_chars || (dc.meta && dc.meta.stt_raw_chars)) || 0;
  const fallbackChars = (dc.raw_text ? dc.raw_text.length : 0) || (dc.detail ? dc.detail.length : 0);
  const totalChars = origChars || rawChars || fallbackChars;
  let charTip = '';
  if(origChars > 0 && rawChars > 0 && origChars !== rawChars){
    charTip = rtr('reader.char_tip_cut', { orig: origChars.toLocaleString(), raw: rawChars.toLocaleString() });
  } else if(totalChars > 0){
    charTip = rtr('reader.char_tip', { count: totalChars.toLocaleString() });
  }
  const hasRawLink = showOrigin && canAccessRaw && Boolean(dc.has_raw_file);
  const hasRawText = Boolean(rawChars > 0 || dc.has_raw_file || (dc.raw_text && String(dc.raw_text).length > 0));
  if((showOrigin && !hasUrl && !hasRawLink) && !isTrunc && !isStt && !isCc && !isParserFallback && !hasPresentation && !isEncodingFlaw && !isScanned && !isVlm && !hasRawText && !totalChars) return '';
  if(!showOrigin && !isTrunc && !isStt && !isCc && !isParserFallback && !hasPresentation && !isEncodingFlaw && !isScanned && !isVlm && !hasRawText && !totalChars) return '';
  let h='<p class=docmeta' + (charTip ? ' title="'+esc(charTip)+'"' : '') + '>';
  if(showOrigin){
    if(hasUrl){
      h+='<a href="'+esc(dc.url)+'" target=_blank rel=noopener>' + rtr("ws.node.open_source") + '</a>';
      if(canAccessRaw && dc.has_raw_file){
        h+=' <a href="/document/raw?id='+encodeURIComponent(dc.id)+'" target=_blank rel=noopener><span data-icon="doc"></span> ' + rtr("ws.node.open_raw") + '</a>';
      }
      if(isStt){
        h+=' <a href="#" class="stt-link" onclick="openSttReader();return false;" title="' + rtr("reader.open_transcript_title") + '">' + rtr("ws.ctx.open_transcript") + '</a>';
      }
      if(hasPresentation){
        h+=' <a href="'+esc(presentation.public_url)+'" target=_blank rel=noopener>↗ Presentation PDF</a>';
      }
    } else {
      if(canAccessRaw && dc.has_raw_file){
        h+='<a href="/document/raw?id='+encodeURIComponent(dc.id)+'" target=_blank rel=noopener><span data-icon="doc"></span> ' + rtr("ws.node.open_raw") + '</a>';
        if(isStt){
          h+=' <a href="#" class="stt-link" onclick="openSttReader();return false;" title="' + rtr("reader.open_transcript_title") + '">' + rtr("ws.ctx.open_transcript") + '</a>';
        }
        if(hasPresentation){
          h+=' <a href="'+esc(presentation.public_url)+'" target=_blank rel=noopener>↗ Presentation PDF</a>';
        }
      } else {
        if(isStt){
          h+='<a href="#" class="stt-link" onclick="openSttReader();return false;" title="' + rtr("reader.open_transcript_title") + '">' + rtr("ws.ctx.open_transcript") + '</a>';
        } else {
          h+='<span></span>';
        }
        if(hasPresentation){
          h+=' <a href="'+esc(presentation.public_url)+'" target=_blank rel=noopener>↗ Presentation PDF</a>';
        }
      }
    }
  }
  let tags=[];
  if(isParserFallback){
    const reqName = parserRequested ? (parserRequested === 'pypdfium2' ? 'PyPDFium2' : (parserRequested === 'pypdf' ? 'PyPDF' : (parserRequested === 'docling' ? 'Docling' : parserRequested))) : 'PyPDFium2';
    const usedName = parserUsed ? (parserUsed === 'pypdfium2' ? 'PyPDFium2' : (parserUsed === 'pypdf' ? 'PyPDF' : (parserUsed === 'docling' ? 'Docling' : parserUsed))) : 'PyPDF';
    const fallbackTitle = rtr('reader.tag.parser_fallback', { requested: reqName, used: usedName });
    tags.push('<span class="prov det trunc-tag parser-fallback-tag" title="'+esc(rtr('reader.tag.fallback_reason', { reason: fallbackReason }))+'"><span data-icon="warning"></span> '+esc(fallbackTitle)+'</span>');
  }
  if(isEncodingFlaw){
    const flawTxt = Array.isArray(encodingFlaws) && encodingFlaws.length ? encodingFlaws.join(', ') : rtr("reader.tag.encoding");
    tags.push('<span class="prov det trunc-tag encoding-flaw-tag" title="'+esc(rtr('reader.tag.encoding_detail', { detail: flawTxt }))+'"><span data-icon="warning"></span> ' + rtr("reader.tag.pdf_encoding") + '</span>');
  }
  if(isScanned){
    tags.push('<span class="prov det trunc-tag scanned-tag" title="' + rtr("reader.tag.scanned_tip") + '"><span data-icon="camera"></span> ' + rtr("reader.tag.scanned") + '</span>');
  }
  if(hasPresentation){
    const pdfChars = Number(presentation.raw_chars || 0);
    const pdfParser = String(presentation.parser_used || '').trim();
    const artifactState = presentation.artifact_path ? rtr("reader.tag.artifact_kept") : rtr("reader.tag.artifact_unknown");
    const bundleIcon = isStt ? '<span data-icon="mic"></span><span data-icon="doc"></span>' : '<span data-icon="captions"></span><span data-icon="doc"></span>';
    const bundleType = isStt ? 'STT×PDF' : 'CC×PDF';
    const pdfLabel = bundleIcon+' '+bundleType;
    const bundleSource = rtr(isStt ? 'reader.tag.bundle_stt' : 'reader.tag.bundle_cc');
    const metaParts = [];
    if(pdfChars > 0) metaParts.push(rtr('reader.chars', { count: pdfChars.toLocaleString() }));
    if(pdfParser) metaParts.push(pdfParser);
    if(presentation.parser_fallback) metaParts.push(rtr("reader.tag.docling_fallback"));
    const metaDetail = metaParts.length ? ' ('+metaParts.join(' · ')+')' : '';
    const tip = rtr('reader.tag.bundle_tip', { source: bundleSource })+metaDetail+' · '+artifactState;
    const tagClass = 'prov det focus-tag' + (isStt ? ' stt-tag' : '');
    tags.push('<span class="'+tagClass+'" title="'+esc(tip)+'">'+pdfLabel+'</span>');
  }
  if(isStt && !hasPresentation){
    const sttLang = (dc.stt_language || (dc.meta && dc.meta.stt_language) || '').trim();
    const tip = sttLang ? rtr('reader.tag.stt_lang', { lang: sttLang }) : rtr('reader.tag.stt');
    tags.push('<span class="prov det focus-tag stt-tag" title="'+esc(tip)+'"><span data-icon="mic"></span> STT</span>');
  }
  if(isCc && !hasPresentation){
    const capLang = (dc.caption_language || (dc.meta && dc.meta.caption_language) || '').trim();
    const tip = capLang ? rtr('reader.tag.cc_lang', { lang: capLang }) : rtr('reader.tag.cc');
    tags.push('<span class="prov det focus-tag cc-tag" title="'+esc(tip)+'"><span data-icon="captions"></span> CC</span>');
  }
  if(isVlm){
    const vlmCount = Number((dc.vlm_images_count !== undefined ? dc.vlm_images_count : (dc.meta && dc.meta.vlm_images_count)) || 0);
    const vlmModel = String((dc.vlm_model || (dc.meta && dc.meta.vlm_model)) || '').trim();
    const vlmLang = String((dc.vlm_language || (dc.meta && dc.meta.vlm_language)) || '').trim();
    let vlmLabel = rtr("reader.tag.vlm_label");
    if(vlmCount > 0){
      vlmLabel += ' (' + rtr('reader.tag.images', { count: vlmCount.toLocaleString() }) + ')';
    }
    let vlmTip = vlmLang ? rtr('reader.tag.vlm_lang', { lang: vlmLang }) : rtr('reader.tag.vlm');
    if(vlmCount > 0){
      vlmTip += ' (' + rtr('reader.tag.images_transcribed', { count: vlmCount.toLocaleString() }) + ')';
    }
    if(vlmModel){
      vlmTip += ' · ' + rtr('reader.tag.model', { model: vlmModel });
    }
    tags.push('<span class="prov det focus-tag vlm-tag" title="'+esc(vlmTip)+'"><span data-icon="image"></span> '+esc(vlmLabel)+'</span>');
  }
  // The reason of a cut source text. It is the hover text of the source badge; the badge has the two character counts (one notice, not two).
  let truncTip = '';
  if(isAppTrunc && isRefTrunc) truncTip = rtr("reader.trunc.appendix_references");
  else if(isRefTrunc) truncTip = rtr("reader.trunc.references");
  else if(isAppTrunc) truncTip = rtr("reader.trunc.appendix");
  else if(isSttTrunc) truncTip = rtr("reader.trunc.stt");
  else if(isTrunc) truncTip = rtr("reader.trunc.limit");
  if(truncTip && origChars > 0 && rawChars > 0) truncTip += ' (' + rtr('reader.chars_cut', { orig: origChars.toLocaleString(), raw: rawChars.toLocaleString() }) + ')';
  const truncKind = (isAppTrunc && isRefTrunc) ? 'appendix-references' : (isRefTrunc ? 'references' : (isAppTrunc ? 'appendix' : (isSttTrunc ? 'stt' : (isTrunc ? 'limit' : ''))));
  const hasI18nT = typeof ClaireI18n !== 'undefined' && ClaireI18n && typeof ClaireI18n.t === 'function';
  if(isTrunc){
    let keptPartLabel = rtr('context.doc.source_kept_partial');
    if(origChars > 0 && rawChars > 0){
      keptPartLabel += ' (' + rtr('reader.chars_part', { raw: rawChars.toLocaleString(), orig: origChars.toLocaleString() }) + ')';
    } else if(rawChars > 0){
      keptPartLabel += ' (' + rtr('reader.chars', { count: rawChars.toLocaleString() }) + ')';
    }
    tags.push('<span class="prov det source-badge" data-source-badge="partial" data-trunc="'+esc(truncKind)+'" title="'+esc(truncTip)+'">'+esc(keptPartLabel)+'</span>');
  } else if(hasRawText){
    const keptFullLabel = rtr('context.doc.source_kept');
    tags.push('<span class="prov det source-badge" data-source-badge="full">'+esc(keptFullLabel)+'</span>');
  }
  // The model of the latest extraction, to the right of the source badge. A later ingestion with a different model changes it.
  const exModel = String(dc.extraction_model || '').trim(), exProvider = String(dc.extraction_provider || '').trim();
  const exModelLabel = exModel || (exProvider && exProvider !== 'agent_client' ? exProvider : '');
  if(exModelLabel){
    const modelTip = hasI18nT ? ClaireI18n.t('context.doc.model_tip') : '';
    tags.push('<span class="prov det model-badge" data-model-badge title="'+esc(modelTip)+'">'+esc(exModelLabel)+'</span>');
  }
  if(tags.length){
    h+='<span class="docmeta-tags">'+tags.join(' ')+'</span>';
  } else if(!showOrigin){
    return '';
  }
  h+='</p>';
  return h;
}
const docMetaHeaderHtml = docMetaHtml;
function docBiblioHtml(dc){
  return '';
}
function getDocMetaMessages(dc){
  if(!dc) return [];
  const messages = [];
  const isTrunc = !!(dc.raw_truncated || (dc.meta && dc.meta.raw_truncated));
  const isAppTrunc = isTrunc && !!(dc.appendix_truncated || (dc.meta && dc.meta.appendix_truncated));
  const isRefTrunc = isTrunc && !!(dc.references_truncated || (dc.meta && dc.meta.references_truncated));
  const isParserFallback = !!(dc.pdf_parser_fallback || (dc.meta && dc.meta.pdf_parser_fallback));
  const fallbackReason = (dc.pdf_parser_fallback_reason || (dc.meta && dc.meta.pdf_parser_fallback_reason) || rtr("reader.tag.parser_runtime_error"));
  const parserRequested = (dc.pdf_parser_requested || (dc.meta && dc.meta.pdf_parser_requested) || '');
  const parserUsed = (dc.pdf_parser_used || (dc.meta && dc.meta.pdf_parser_used) || '');
  const isEncodingFlaw = !!(dc.pdf_encoding_flaw_detected || (dc.meta && dc.meta.pdf_encoding_flaw_detected));
  const encodingFlaws = (dc.pdf_encoding_flaws || (dc.meta && dc.meta.pdf_encoding_flaws) || []);
  const isScanned = !!(dc.pdf_is_scanned || (dc.meta && dc.meta.pdf_is_scanned));
  const isStt = !!(dc.is_stt || (dc.meta && (dc.meta.is_stt || dc.meta.stt_applied || dc.meta.stt)));
  const isSttTrunc = isStt && !!(dc.stt_truncated || (dc.meta && dc.meta.stt_truncated) || isTrunc);
  const hasTranscript = !!(dc.has_transcript || (dc.meta && dc.meta.has_transcript));
  const isCc = !isStt && (hasTranscript || !!(dc.meta && (dc.meta.caption_status === 'available' || (dc.meta.transcript_source && dc.meta.transcript_source !== 'stt'))));
  const isVlm = !!(dc.vlm_transcribed || (dc.meta && dc.meta.vlm_transcribed));
  const presentation = dc.presentation_pdf || (dc.meta && dc.meta.presentation_pdf) || {};
  const isWebUrl = u => !!u && /^https?:\/\//i.test(String(u).trim());
  const hasPresentation = presentation.status === 'available' && isWebUrl(presentation.public_url);

  if(isParserFallback){
    const reqName = parserRequested ? (parserRequested === 'pypdfium2' ? 'PyPDFium2' : (parserRequested === 'pypdf' ? 'PyPDF' : (parserRequested === 'docling' ? 'Docling' : parserRequested))) : 'PyPDFium2';
    const usedName = parserUsed ? (parserUsed === 'pypdfium2' ? 'PyPDFium2' : (parserUsed === 'pypdf' ? 'PyPDF' : (parserUsed === 'docling' ? 'Docling' : parserUsed))) : 'PyPDF';
    const fallbackTitle = rtr('reader.tag.parser_fallback', { requested: reqName, used: usedName });
    messages.push(rtr('reader.tag.fallback_reason', { reason: fallbackReason }) + ' (' + fallbackTitle + ')');
  }
  if(isEncodingFlaw){
    const flawTxt = Array.isArray(encodingFlaws) && encodingFlaws.length ? encodingFlaws.join(', ') : rtr("reader.tag.encoding");
    messages.push(rtr('reader.tag.encoding_detail', { detail: flawTxt }));
  }
  if(isScanned){
    messages.push(rtr("reader.tag.scanned_tip"));
  }
  if(hasPresentation){
    const pdfChars = Number(presentation.raw_chars || 0);
    const pdfParser = String(presentation.parser_used || '').trim();
    const artifactState = presentation.artifact_path ? rtr("reader.tag.artifact_kept") : rtr("reader.tag.artifact_unknown");
    const bundleSource = rtr(isStt ? 'reader.tag.bundle_stt' : 'reader.tag.bundle_cc');
    const metaParts = [];
    if(pdfChars > 0) metaParts.push(rtr('reader.chars', { count: pdfChars.toLocaleString() }));
    if(pdfParser) metaParts.push(pdfParser);
    if(presentation.parser_fallback) metaParts.push(rtr("reader.tag.docling_fallback"));
    const metaDetail = metaParts.length ? ' ('+metaParts.join(' · ')+')' : '';
    messages.push(rtr('reader.tag.bundle_tip', { source: bundleSource })+metaDetail+' · '+artifactState);
  }
  if(isStt && !hasPresentation){
    const sttLang = (dc.stt_language || (dc.meta && dc.meta.stt_language) || '').trim();
    messages.push(sttLang ? rtr('reader.tag.stt_lang', { lang: sttLang }) : rtr('reader.tag.stt'));
  }
  if(isCc && !hasPresentation){
    const capLang = (dc.caption_language || (dc.meta && dc.meta.caption_language) || '').trim();
    messages.push(capLang ? rtr('reader.tag.cc_lang', { lang: capLang }) : rtr('reader.tag.cc'));
  }
  if(isVlm){
    const vlmCount = Number((dc.vlm_images_count !== undefined ? dc.vlm_images_count : (dc.meta && dc.meta.vlm_images_count)) || 0);
    const vlmModel = String((dc.vlm_model || (dc.meta && dc.meta.vlm_model)) || '').trim();
    const vlmLang = String((dc.vlm_language || (dc.meta && dc.meta.vlm_language)) || '').trim();
    let msg = vlmLang ? rtr('reader.tag.vlm_lang', { lang: vlmLang }) : rtr('reader.tag.vlm');
    if(vlmCount > 0){
      msg += ' (' + rtr('reader.tag.images_transcribed', { count: vlmCount.toLocaleString() }) + ')';
    }
    if(vlmModel){
      msg += ' · ' + rtr('reader.tag.model', { model: vlmModel });
    }
    messages.push(msg);
  }
  if(isAppTrunc && isRefTrunc){
    const orig=(dc.orig_chars || (dc.meta && dc.meta.orig_chars)) || 0;
    const raw=(dc.raw_chars || (dc.meta && dc.meta.raw_chars)) || 0;
    let tip = rtr("reader.trunc.appendix_references");
    if(orig > 0 && raw > 0){
      tip+=' (' + rtr('reader.chars_cut', { orig: orig.toLocaleString(), raw: raw.toLocaleString() }) + ')';
    } else if(raw > 0){
      tip+=' (' + rtr('reader.chars', { count: raw.toLocaleString() }) + ')';
    }
    messages.push(tip);
  } else if(isRefTrunc){
    const orig=(dc.orig_chars || (dc.meta && dc.meta.orig_chars)) || 0;
    const raw=(dc.raw_chars || (dc.meta && dc.meta.raw_chars)) || 0;
    let tip = rtr("reader.trunc.references");
    if(orig > 0 && raw > 0){
      tip+=' (' + rtr('reader.chars_cut', { orig: orig.toLocaleString(), raw: raw.toLocaleString() }) + ')';
    } else if(raw > 0){
      tip+=' (' + rtr('reader.chars', { count: raw.toLocaleString() }) + ')';
    }
    messages.push(tip);
  } else if(isAppTrunc){
    const orig=(dc.orig_chars || (dc.meta && dc.meta.orig_chars)) || 0;
    const raw=(dc.raw_chars || (dc.meta && dc.meta.raw_chars)) || 0;
    let tip = rtr("reader.trunc.appendix");
    if(orig > 0 && raw > 0){
      tip+=' (' + rtr('reader.chars_cut', { orig: orig.toLocaleString(), raw: raw.toLocaleString() }) + ')';
    } else if(raw > 0){
      tip+=' (' + rtr('reader.chars', { count: raw.toLocaleString() }) + ')';
    }
    messages.push(tip);
  } else if(isSttTrunc){
    const orig=(dc.stt_orig_chars || (dc.meta && dc.meta.stt_orig_chars) || dc.orig_chars || (dc.meta && dc.meta.orig_chars)) || 0;
    const raw=(dc.stt_raw_chars || (dc.meta && dc.meta.stt_raw_chars) || dc.raw_chars || (dc.meta && dc.meta.raw_chars)) || 0;
    let tip = rtr("reader.trunc.stt");
    if(orig > 0 && raw > 0){
      tip += ' (' + rtr('reader.chars_cut', { orig: orig.toLocaleString(), raw: raw.toLocaleString() }) + ')';
    } else if(raw > 0){
      tip += ' (' + rtr('reader.chars', { count: raw.toLocaleString() }) + ')';
    }
    messages.push(tip);
  } else if(isTrunc){
    const orig=(dc.orig_chars || (dc.meta && dc.meta.orig_chars)) || 0;
    const raw=(dc.raw_chars || (dc.meta && dc.meta.raw_chars)) || 0;
    let tip = rtr("reader.trunc.limit");
    if(orig > 0 && raw > 0){
      tip+=' (' + rtr('reader.chars_cut', { orig: orig.toLocaleString(), raw: raw.toLocaleString() }) + ')';
    } else if(raw > 0){
      tip+=' (' + rtr('reader.chars', { count: raw.toLocaleString() }) + ')';
    }
    messages.push(tip);
  }
  return messages;
}
function docMetaMessageHtml(dc){
  if(!dc) return '';
  const msgs = getDocMetaMessages(dc);
  if(!msgs.length) return '';
  return '<div class="docmeta-message-box" style="margin:.4em 0 .6em;padding:6px 10px;background:var(--card-bg);border:1px solid var(--border);border-radius:5px;font-size:12px;color:var(--muted);line-height:1.45">' +
    msgs.map(m => '<div>' + esc(m) + '</div>').join('') +
    '</div>';
}
function renderReader(dc){
  curReaderDocData=dc;
  if(dc && dc.title){
    document.title = dc.title + ' — Claire Bible';
  } else {
    document.title = rtr('reader.doc_title');
  }
  document.getElementById('rtitle').innerHTML = esc(dc.title||rtr("home.recent.untitled"))
    + (dc.source_type?' <span class=rmeta>'+esc(dc.source_type)+'</span>':'');
  let h='';

  // Heatmap Matrix 대조 배너 검사 (확인할 때까지 표시)
  const matrixKey = 'doc_matrix_' + (dc.id || '');
  const confirmedKey = 'doc_matrix_confirmed_' + (dc.id || '');
  try {
    const isConfirmed = localStorage.getItem(confirmedKey) === '1' || sessionStorage.getItem(confirmedKey) === '1';
    let matData = null;
    const rawMatrix = sessionStorage.getItem(matrixKey);
    if(rawMatrix) {
      matData = JSON.parse(rawMatrix);
    } else if(dc.heatmap_matrix) {
      matData = dc.heatmap_matrix;
    } else if(dc.meta && dc.meta.heatmap_matrix) {
      matData = dc.meta.heatmap_matrix;
    }
    if(matData && !isConfirmed && typeof renderMiniMatrixBannerHtml === 'function'){
      h += renderMiniMatrixBannerHtml(dc.id, matData);
    }
  } catch(_) {}

  h+=docMetaHtml(dc);
  const isStt = !!(dc.is_stt || (dc.meta && (dc.meta.is_stt || dc.meta.stt_applied || dc.meta.stt)));
  const isSttTrunc = isStt && !!(dc.stt_truncated || (dc.meta && dc.meta.stt_truncated) || dc.raw_truncated || (dc.meta && dc.meta.raw_truncated));
  if(isSttTrunc){
    h+='<div class="stt-trunc-banner"><span data-icon="warning"></span> '+rtr('reader.banner.stt_trunc', { command: '<code>claire video-reprocess --doc-id '+esc(dc.id||'')+' --apply --full-content</code>' })+'</div>';
  }
  h+=extraSourcesHtml(dc);
  if(dc.summary){
    h+='<div class=rsection>' + rtr("reader.section.summary") + '</div><div class="doc-content">'+renderPlainText(dc.summary)+'</div>';
  }else if(dc.detail_html){
    h+='<div class=rsection>' + rtr("reader.section.summary") + '</div><div class="doc-content stt-trunc-banner" style="margin-top:.5em"><span data-icon="warning"></span> '+rtr('reader.banner.no_summary', { command: '<code>./cb-manuscript app regenerate '+esc(dc.id||'')+' --summary --apply</code>' })+
      ((typeof canIngest === 'function' && canIngest()) ? '<div style="margin-top:6px"><button type="button" class="sec" style="font-size:12px;padding:3px 8px;cursor:pointer" onclick="openRegen('+jsArg(dc.id)+', \'summary\')"><span data-icon="repeat"></span> ' + rtr("reader.regen_summary") + '</button></div>' : '')+'</div>';
  }
  if(dc.detail_html){
    const purifier=window.DOMPurify;
    const cleanHtml=(purifier && typeof purifier.sanitize==='function')?purifier.sanitize(dc.detail_html, DOMPURIFY_OPTS):dc.detail_html;
    h+='<div class=rsection>' + rtr("reader.section.detail") + '</div><div class="doc-content">'+cleanHtml+'</div>';
  }else if(dc.summary){
    h+='<div class=rsection>' + rtr("reader.section.detail") + '</div><div class="doc-content stt-trunc-banner" style="margin-top:.5em"><span data-icon="warning"></span> '+rtr('reader.banner.no_detail', { command: '<code>./cb-manuscript app regenerate '+esc(dc.id||'')+' --detail --apply</code>' })+
      ((typeof canIngest === 'function' && canIngest()) ? '<div style="margin-top:6px"><button type="button" class="sec" style="font-size:12px;padding:3px 8px;cursor:pointer" onclick="openRegen('+jsArg(dc.id)+', \'detail\')"><span data-icon="repeat"></span> ' + rtr("reader.regen_detail") + '</button></div>' : '')+'</div>';
  }
  if(!dc.summary && !dc.detail_html){
    h+='<div class=rsection>' + rtr("reader.section.summary_detail") + '</div><div class="doc-content stt-trunc-banner" style="margin-top:.5em"><span data-icon="warning"></span> '+rtr('reader.banner.no_both', { command: '<code>./cb-manuscript app regenerate '+esc(dc.id||'')+' --all --apply</code>' })+
      ((typeof canIngest === 'function' && canIngest()) ? '<div style="margin-top:6px"><button type="button" class="sec" style="font-size:12px;padding:3px 8px;cursor:pointer" onclick="openRegen('+jsArg(dc.id)+', \'all\')"><span data-icon="repeat"></span> ' + rtr("reader.regen_all") + '</button></div>' : '')+'</div>';
  }
  const body=document.getElementById('rbody'); body.innerHTML=h; body.scrollTop=0; body.scrollLeft=0;
  applyMathRendering(body);
  document.getElementById('reader').setAttribute('aria-busy','false');
  updateReaderRail();
  setupReaderAccessibleBlocks(body);
  if(typeof applyEntityMarks === 'function'){
    applyEntityMarks(dc);
  }
  if(typeof window !== 'undefined' && typeof window.requestAnimationFrame === 'function'){
    window.requestAnimationFrame(()=>{
      if(body && typeof body.focus === 'function'){
        try{ body.focus({preventScroll:true}); }catch(_){ body.focus(); }
      }
    });
  }
  if(typeof window.gtag === 'function' && dc && dc.id){
    try{
      window.gtag('event', 'page_view', {
        page_title: document.title,
        page_location: window.location.origin + '/doc/' + dc.id
      });
      window.gtag('event', 'select_content', {
        content_type: 'document',
        item_id: dc.id
      });
    }catch(_){}
  }
}

// --- 슬림 마름모 바 & 탄성 확장 헤딩 내비게이션 레일 제어 ---
let curRailHeadings = [];
let isRailPressing = false;
let railScrubFocusIdx = -1;

function _rClsAdd(el, c){ if(el && el.classList && el.classList.add) el.classList.add(c); else if(el && el.className!==undefined && !el.className.includes(c)) el.className = (el.className+' '+c).trim(); }
function _rClsRem(el, c){ if(el && el.classList && el.classList.remove) el.classList.remove(c); else if(el && el.className) el.className = el.className.replace(new RegExp('\b'+c+'\b','g'),'').trim(); }

function updateReaderRail(){
  const rbody = document.getElementById('rbody');
  const rail = document.getElementById('rrail');
  const track = document.getElementById('rrail-track');
  const fill = document.getElementById('rrail-fill');
  const diamonds = document.getElementById('rrail-diamonds');
  if(!rbody || !rail || !track || !fill || !diamonds) return;

  const headingEls = rbody.querySelectorAll('.rsection, .doc-content h1, .doc-content h2, .doc-content h3, .doc-content h4');
  if(headingEls.length <= 1){
    _rClsRem(rail, 'visible');
    diamonds.innerHTML = '';
    curRailHeadings = [];
    return;
  }

  _rClsAdd(rail, 'visible');
  curRailHeadings = Array.from(headingEls).map((el, idx) => {
    let tag = 'h2';
    if(el.classList.contains('rsection')) tag = 'rsec';
    else if(el.tagName === 'H1') tag = 'h1';
    else if(el.tagName === 'H2') tag = 'h2';
    else if(el.tagName === 'H3') tag = 'h3';
    else if(el.tagName === 'H4') tag = 'h4';

    return {
      el: el,
      tag: tag,
      title: el.innerText ? el.innerText.trim() : '',
      percent: 0,
      expandedPercent: 0
    };
  });

  function _getElTop(el){
    if(!el || !rbody) return 0;
    if(typeof el.getBoundingClientRect === 'function' && typeof rbody.getBoundingClientRect === 'function'){
      return el.getBoundingClientRect().top - rbody.getBoundingClientRect().top + (rbody.scrollTop || 0);
    }
    return (el.offsetParent === rbody) ? el.offsetTop : Math.max(0, (el.offsetTop || 0) - (rbody.offsetTop || 0));
  }

  function computeRailPositions(){
    const tScroll = rbody.scrollHeight - rbody.clientHeight;
    const count = curRailHeadings.length;
    curRailHeadings.forEach((item, idx) => {
      let ratio = 0;
      if(tScroll > 0){
        const topOffset = _getElTop(item.el);
        ratio = Math.max(0, Math.min(1, topOffset / tScroll));
      } else {
        ratio = count > 1 ? (idx / (count - 1)) : 0.5;
      }
      item.percent = ratio * 100;
      const uniform = count > 1 ? (idx / (count - 1)) * 96 + 2 : 50;
      item.expandedPercent = item.percent * 0.2 + uniform * 0.8;
    });
  }

  computeRailPositions();

  diamonds.innerHTML = '';
  curRailHeadings.forEach((item, idx) => {
    const marker = document.createElement('div');
    marker.className = 'cb-diamond-marker cb-marker-' + item.tag;
    marker.style.top = item.percent + '%';
    marker.dataset.index = idx;
    marker.innerHTML = '<div class="cb-diamond-tooltip">' + esc(item.title) + '</div><div class="cb-diamond-shape"></div>';

    marker.addEventListener('click', (e) => {
      e.stopPropagation();
      rbody.scrollTo({ top: Math.max(0, _getElTop(item.el) - 12), behavior: 'smooth' });
    });

    diamonds.appendChild(marker);
  });

  function onRbodyScroll(){
    const top = rbody.scrollTop;
    const tScroll = rbody.scrollHeight - rbody.clientHeight;
    const ratio = tScroll > 0 ? top / tScroll : 0;
    const pct = Math.min(100, Math.max(0, Math.round(ratio * 100)));
    fill.style.height = pct + '%';

    let activeIdx = 0;
    const threshold = top + 80;
    for(let i = 0; i < curRailHeadings.length; i++){
      if(_getElTop(curRailHeadings[i].el) <= threshold){
        activeIdx = i;
      } else {
        break;
      }
    }

    const markers = diamonds.querySelectorAll('.cb-diamond-marker');
    markers.forEach((m, i) => {
      if(i === activeIdx){
        _rClsAdd(m, 'active');
      } else {
        _rClsRem(m, 'active');
      }
    });
  }

  rbody.onscroll = onRbodyScroll;
  onRbodyScroll();

  function startRailExpand(clientY){
    isRailPressing = true;
    _rClsAdd(rail, 'is-expanding');
    const markers = diamonds.querySelectorAll('.cb-diamond-marker');
    markers.forEach((m, idx) => {
      if(curRailHeadings[idx]){
        m.style.top = curRailHeadings[idx].expandedPercent + '%';
      }
    });
    updateScrub(clientY);
  }

  function updateScrub(clientY){
    if(!isRailPressing) return;
    const rect = track.getBoundingClientRect ? track.getBoundingClientRect() : { top: 0, height: 100 };
    const relY = clientY - rect.top;
    const ratio = Math.max(0, Math.min(1, relY / rect.height));
    const curPct = ratio * 100;

    let closestIdx = 0;
    let minDiff = 999;
    curRailHeadings.forEach((item, idx) => {
      const diff = Math.abs(item.expandedPercent - curPct);
      if(diff < minDiff){
        minDiff = diff;
        closestIdx = idx;
      }
    });
    railScrubFocusIdx = closestIdx;

    const markers = diamonds.querySelectorAll('.cb-diamond-marker');
    markers.forEach((m, idx) => {
      if(idx === railScrubFocusIdx){
        _rClsAdd(m, 'scrub-focus');
      } else {
        _rClsRem(m, 'scrub-focus');
      }
    });
  }

  function endRailExpand(){
    if(!isRailPressing) return;
    isRailPressing = false;
    _rClsRem(rail, 'is-expanding');

    if(railScrubFocusIdx >= 0 && curRailHeadings[railScrubFocusIdx]){
      rbody.scrollTo({ top: Math.max(0, _getElTop(curRailHeadings[railScrubFocusIdx].el) - 12), behavior: 'smooth' });
    }

    const markers = diamonds.querySelectorAll('.cb-diamond-marker');
    markers.forEach((m, idx) => {
      _rClsRem(m, 'scrub-focus');
      if(curRailHeadings[idx]){
        m.style.top = curRailHeadings[idx].percent + '%';
      }
    });
    railScrubFocusIdx = -1;
  }

  if(!rail._boundEvents){
    rail._boundEvents = true;
    rail.addEventListener('mousedown', (e) => {
      e.preventDefault();
      startRailExpand(e.clientY);
    });
    window.addEventListener('mousemove', (e) => {
      if(isRailPressing) updateScrub(e.clientY);
    });
    window.addEventListener('mouseup', () => {
      endRailExpand();
    });

    rail.addEventListener('touchstart', (e) => {
      if(e.touches.length > 0) startRailExpand(e.touches[0].clientY);
    }, { passive: true });
    window.addEventListener('touchmove', (e) => {
      if(isRailPressing && e.touches.length > 0) updateScrub(e.touches[0].clientY);
    }, { passive: true });
    window.addEventListener('touchend', () => {
      endRailExpand();
    });

    window.addEventListener('resize', () => {
      if(typeof centerView !== 'undefined' && centerView === 'reader') updateReaderRail();
    });
    window.addEventListener('orientationchange', () => {
      setTimeout(() => {
        if(typeof centerView !== 'undefined' && centerView === 'reader') updateReaderRail();
      }, 200);
    });
  }
}

// --- STT 전사 열기 모달 뷰어 제어 ---
let curSttData = null;
let curSttFilter = '';

function openSttReader(docId){
  const did = docId || curReaderDoc || (curReaderDocData && curReaderDocData.id);
  if(!did && !curReaderDocData) return;

  function _render(dc){
    if(!dc) return;
    curSttData = dc;
    const modal = document.getElementById('sttmodal');
    if(!modal) return;

    if(!dc.is_stt && !(dc.meta && (dc.meta.is_stt || dc.meta.transcript_segments))){
      alert(rtr("reader.stt.none"));
      return;
    }

    const titleEl = document.getElementById('stttitle');
    if(titleEl) titleEl.textContent = rtr('reader.stt.title', { title: dc.title || rtr('home.recent.untitled') });

    const metaEl = document.getElementById('sttmeta');
    let metaTxt = '';
    const dur = (dc.meta && dc.meta.duration_sec) || dc.duration_sec || 0;
    if(dur > 0){
      const m = Math.floor(dur / 60);
      const s = Math.floor(dur % 60);
      metaTxt += rtr('reader.stt.duration', { minutes: m, seconds: s });
    }
    const segs = dc.transcript_segments || (dc.meta && dc.meta.transcript_segments) || [];
    if(segs.length > 0){
      metaTxt += (metaTxt ? ' · ' : '') + rtr('reader.stt.segments', { count: segs.length.toLocaleString() });
    }
    if(metaEl) metaEl.textContent = metaTxt;

    const input = document.getElementById('sttq');
    if(input) input.value = '';
    curSttFilter = '';

    renderSttLines();
    modal.classList.add('open');
    modal.style.display = 'flex';
    document.body.classList.add('stt-modal-open');
    if(input) requestAnimationFrame(()=>input.focus());
  }

  if(curReaderDocData && (!docId || curReaderDocData.id === docId) && (curReaderDocData.stt_transcript || curReaderDocData.transcript_segments)){
    _render(curReaderDocData);
  } else {
    fetch(readerApiUrl('document?id=' + encodeURIComponent(did))).then(r=>r.json()).then(dc=>{
      _render(dc);
    }).catch(e=>{
      alert(rtr('reader.stt.load_failed', { error: e }));
    });
  }
}

function closeSttReader(){
  const modal = document.getElementById('sttmodal');
  if(!modal) return;
  modal.classList.remove('open');
  modal.style.display = 'none';
  document.body.classList.remove('stt-modal-open');
}

function renderSttLines(){
  const body = document.getElementById('sttbody');
  const countEl = document.getElementById('sttcount');
  if(!body || !curSttData) return;

  const dc = curSttData;
  let segs = dc.transcript_segments || (dc.meta && dc.meta.transcript_segments) || [];
  let rawText = dc.stt_transcript || '';

  let h = '';
  const isTrunc = !!(dc.stt_truncated || (dc.meta && dc.meta.stt_truncated));
  if(isTrunc){
    h += '<div class="stt-trunc-banner"><span data-icon="warning"></span> ' + rtr('reader.stt.trunc_banner', { command: '<code>claire video-reprocess --doc-id ' + esc(dc.id||'') + ' --apply --full-content</code>' }) + '</div>';
  }

  const q = (curSttFilter || '').toLowerCase().trim();
  let matchCount = 0;
  let totalCount = 0;

  if(segs && segs.length > 0){
    totalCount = segs.length;
    segs.forEach(s => {
      const startF = s.start_sec != null ? s.start_sec : (s.start != null ? s.start : 0.0);
      const totalSec = Math.floor(startF);
      const hrs = Math.floor(totalSec / 3600);
      const mins = Math.floor((totalSec % 3600) / 60);
      const secs = totalSec % 60;
      const ts = hrs > 0 ? (String(hrs).padStart(2,'0')+':'+String(mins).padStart(2,'0')+':'+String(secs).padStart(2,'0')) : (String(mins).padStart(2,'0')+':'+String(secs).padStart(2,'0'));
      const txt = String(s.text || '');

      const isMatch = !q || txt.toLowerCase().includes(q) || ts.includes(q);
      if(isMatch){
        matchCount++;
        let dispTxt = esc(txt);
        if(q){
          let ltxt = txt.toLowerCase(), lq = q.toLowerCase();
          let idx = 0, out = '';
          while(true){
            let next = ltxt.indexOf(lq, idx);
            if(next === -1){ out += esc(txt.slice(idx)); break; }
            out += esc(txt.slice(idx, next)) + '<mark>' + esc(txt.slice(next, next + q.length)) + '</mark>';
            idx = next + q.length;
          }
          dispTxt = out;
        }
        h += '<div class="stt-line' + (q ? ' highlight' : '') + '">';
        h += '<span class="stt-ts" title="' + rtr("reader.stt.copy_ts_title") + '" data-ts="' + esc(ts) + '" onclick="copyTimestamp(this.dataset.ts)">[' + esc(ts) + ']</span>';
        h += '<span class="stt-text">' + dispTxt + '</span>';
        h += '</div>';
      }
    });
  } else if(rawText) {
    const lines = rawText.split('\n');
    totalCount = lines.length;
    lines.forEach(line => {
      const trimmed = line.trim();
      if(!trimmed) return;
      const isMatch = !q || trimmed.toLowerCase().includes(q);
      if(isMatch){
        matchCount++;
        let dispTxt = esc(trimmed);
        if(q){
          let ltxt = trimmed.toLowerCase(), lq = q.toLowerCase();
          let idx = 0, out = '';
          while(true){
            let next = ltxt.indexOf(lq, idx);
            if(next === -1){ out += esc(trimmed.slice(idx)); break; }
            out += esc(trimmed.slice(idx, next)) + '<mark>' + esc(trimmed.slice(next, next + q.length)) + '</mark>';
            idx = next + q.length;
          }
          dispTxt = out;
        }
        h += '<div class="stt-line' + (q ? ' highlight' : '') + '"><span class="stt-text">' + dispTxt + '</span></div>';
      }
    });
  } else {
    h = '<p class="hint">' + rtr("reader.stt.empty") + '</p>';
  }

  body.innerHTML = h;
  if(countEl){
    if(q){
      countEl.textContent = rtr('reader.stt.matches', { count: matchCount.toLocaleString(), total: totalCount.toLocaleString() });
    } else {
      countEl.textContent = totalCount > 0 ? rtr('reader.stt.items', { count: totalCount.toLocaleString() }) : '';
    }
  }
}

function filterSttLines(val){
  curSttFilter = val;
  renderSttLines();
}

function copyTimestamp(ts){
  if(navigator.clipboard && navigator.clipboard.writeText){
    navigator.clipboard.writeText('[' + ts + ']').catch(()=>{});
  }
}

function copySttText(withTs){
  if(!curSttData) return;
  const dc = curSttData;
  const segs = dc.transcript_segments || (dc.meta && dc.meta.transcript_segments) || [];
  let out = '';
  if(segs && segs.length > 0){
    const lines = segs.map(s => {
      const startF = s.start_sec != null ? s.start_sec : (s.start != null ? s.start : 0.0);
      const totalSec = Math.floor(startF);
      const hrs = Math.floor(totalSec / 3600);
      const mins = Math.floor((totalSec % 3600) / 60);
      const secs = totalSec % 60;
      const ts = hrs > 0 ? (String(hrs).padStart(2,'0')+':'+String(mins).padStart(2,'0')+':'+String(secs).padStart(2,'0')) : (String(mins).padStart(2,'0')+':'+String(secs).padStart(2,'0'));
      const txt = String(s.text || '').trim();
      return withTs ? ('[' + ts + '] ' + txt) : txt;
    });
    out = lines.join('\n');
  } else if(dc.stt_transcript) {
    out = dc.stt_transcript;
    if(!withTs){
      out = out.replace(/^\[\d{1,2}:\d{2}(?::\d{2})?\]\s*/gm, '');
    }
  }
  if(out){
    navigator.clipboard.writeText(out).then(()=>{
      alert(rtr("reader.stt.copied"));
    }).catch(()=>{
      alert(rtr("reader.copy_failed"));
    });
  }
}

if(typeof window !== 'undefined' && typeof window.addEventListener === 'function'){
  window.addEventListener('keydown', function(e){
    if(e.key === 'Escape'){
      const m = document.getElementById('sttmodal');
      if(m && m.classList.contains('open')){
        e.stopPropagation();
        closeSttReader();
      }
    }
  });
}
// [동일 주제 출처 병합, docs/implementation/EXPANSION.md] 이 문서에 흡수된 부가 출처 목록(원문 링크 계보).
function extraSourcesHtml(dc){
  const es=dc.extra_sources||[];
  if(!es.length) return '';
  return '<div class=rsection>'+rtr('reader.merged_sources', { count: es.length })+'</div><ul class=srclist>'+
    es.map(s=>'<li><a href="'+esc(s.url||'')+'" target=_blank rel=noopener>'+
      esc(s.title||s.url||'')+'</a></li>').join('')+'</ul>';
}
function closeReader(focus=false){
  if(typeof window !== 'undefined' && window.history && window.history.state && window.history.state.modal === 'reader' && !isPoppingHistory){
    window.history.back();
    return;
  }
  document.title = typeof HOME_TITLE !== 'undefined' ? HOME_TITLE : rtr('home.title');
  hideNodePop();
  if(typeof clearEntityMarks === 'function'){
    clearEntityMarks();
  }
  const r=document.getElementById('reader');
  r.classList.remove('open'); r.setAttribute('aria-hidden','true'); r.setAttribute('aria-busy','false');
  document.body.classList.remove('reader-open');
  setReaderBackgroundInert(false); syncWorkspaceLayout();
  if(typeof centerView !== 'undefined' && centerView === 'reader' && typeof setCenterView === 'function' && !(typeof homeEntryActive !== 'undefined' && homeEntryActive)){
    setCenterView('graph');
  }
  const sb=document.getElementById('sharebox'); if(sb) sb.className='sharebox';
  let target=readerReturnFocus && readerReturnFocus.isConnected ? readerReturnFocus : null;
  if(!target && readerReturnDocId){
    target=[...document.querySelectorAll('[data-read-doc]')].find(el=>el.dataset.readDoc===readerReturnDocId);
  }
  if(!target) target=mobileMQ.matches ? (paneTabs[activePane] || document.getElementById('docs')) : document.getElementById('palettetrigger');
  readerReturnFocus=null; readerReturnDocId=null;
  replaceAppHistory({ modal: getActiveModalName() });
  if(focus && target) requestAnimationFrame(()=>target.focus());
  else requestAnimationFrame(()=>{ if(target) target.focus(); });
  if(typeof window.gtag === 'function'){
    try{
      window.gtag('event', 'page_view', {
        page_title: document.title,
        page_location: window.location.origin + '/'
      });
    }catch(_){}
  }
}

// --- 문서 공유 핫링크 — 세션 토큰(nginx 통과)과 별개의, 이 문서만 여는 읽기전용 링크 ---
// POST /share sets link access of the document and gives its permalink (/silvercord/{token}).
async function editDocTitle(){
  if(!canWrite() || !curReaderDoc) return;
  const current = (curReaderDocData && curReaderDocData.title && curReaderDocData.title !== rtr('home.recent.untitled')) ? curReaderDocData.title : '';
  const newTitle = prompt(rtr("reader.new_title"), current);
  if(newTitle === null) return;
  const trimmed = newTitle.trim();
  try{
    const r = await fetch(readerApiUrl('document/title'), {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({id: curReaderDoc, title: trimmed})
    });
    if(r.status === 401 || r.status === 404){
      expireWriteAccess();
      alert(rtr("ws.common.session_expired_or_denied"));
      return;
    }
    if(!r.ok){
      const err = await r.json().catch(()=>({}));
      alert(rtr('reader.title_failed', { reason: err.detail || err.error || ('HTTP ' + r.status) }));
      return;
    }
    const d = await r.json();
    const updatedTitle = d.title || rtr("home.recent.untitled");
    if(curReaderDocData) curReaderDocData.title = d.title;
    document.getElementById('rtitle').innerHTML = esc(updatedTitle)
      + (curReaderDocData && curReaderDocData.source_type ? ' <span class=rmeta>' + esc(curReaderDocData.source_type) + '</span>' : '');
    const dc = allDocs && allDocs.find(x => x.id === curReaderDoc);
    if(dc){
      dc.title = d.title;
      renderDocs(document.getElementById('docq') ? document.getElementById('docq').value : '');
    }
  }catch(e){
    alert(rtr('reader.title_failed', { reason: String(e) }));
  }
}
async function shareDoc(){
  if(!curReaderDoc) return;
  const sb=document.getElementById('sharebox');
  if(!sb) return;
  sb.className='sharebox on'; sb.innerHTML='<span class=pt>' + rtr("reader.share.making") + '</span>';
  try{
    const r=await fetch(readerApiUrl('share'),{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({doc_id:curReaderDoc})});
    if(r.status===401||r.status===404){
      if(typeof canWrite === 'function' && canWrite()){
        if(typeof expireWriteAccess === 'function') expireWriteAccess();
        sb.innerHTML='<span class=pt>' + rtr("ws.common.session_expired") + '</span>';
      } else {
        sb.innerHTML='<span class=pt>' + rtr("reader.share.unavailable") + '</span>';
      }
      return;
    }
    const d=await r.json();
    if(d.error||!d.path){ sb.innerHTML='<span class=pt>'+esc(rtr('reader.share.failed', { reason: d.error || rtr('reader.pres.unknown') }))+'</span>'; return; }
    const url=location.origin+d.path;
    let copied=false;
    try{
      if(navigator.clipboard && navigator.clipboard.writeText){
        await navigator.clipboard.writeText(url);
        copied=true;
      }
    }catch(_){}
    if(typeof window.gtag === 'function' && curReaderDoc){
      try{ window.gtag('event', 'share', { method: 'link', content_type: 'document', item_id: curReaderDoc }); }catch(_){}
    }
    sb.innerHTML='<input id="shareurl" readonly value="'+esc(url)+'" onclick="this.select()"/>'+
      '<button onclick="copyShare()">'+(copied?rtr("reader.copied"):rtr("reader.copy"))+'</button>';
  }catch(e){ sb.innerHTML='<span class=pt>'+esc(rtr('reader.share.failed', { reason: String(e) }))+'</span>'; }
}
function copyShare(){
  const i=document.getElementById('shareurl'); if(!i) return;
  i.select();
  const markCopied = () => { const b=document.querySelector('#sharebox button'); if(b) b.textContent=rtr("reader.copied"); };
  if(navigator.clipboard && navigator.clipboard.writeText){
    navigator.clipboard.writeText(i.value).then(markCopied).catch(()=>{
      try{ document.execCommand('copy'); markCopied(); }catch(_){}
    });
  } else {
    try{ document.execCommand('copy'); markCopied(); }catch(_){}
  }
}

function updatePresentationView(docId){
  const targetId = docId || (typeof curReaderDoc !== 'undefined' && curReaderDoc) ||
                   (typeof activeDoc !== 'undefined' && activeDoc) ||
                   (typeof allDocs !== 'undefined' && allDocs && allDocs.length ? allDocs[0].id : null);
  const wrap = document.getElementById('presentationwrap');
  const frame = document.getElementById('presentation-frame');
  const empty = document.getElementById('presentation-empty');
  const emptyTitle = document.getElementById('presentation-empty-title');
  const emptyMsg = document.getElementById('presentation-empty-msg');
  const genBtn = document.getElementById('presentation-gen-btn');
  const titleEl = document.getElementById('presentation-title');
  const toolsEl = document.getElementById('presentationtools');
  const sharebox = document.getElementById('presentation-sharebox');
  if(!wrap || !frame) return;

  if(sharebox){ sharebox.className = 'sharebox'; sharebox.innerHTML = ''; }

  if(typeof isPresentationEnabledForActiveTheme === 'function' && !isPresentationEnabledForActiveTheme()){
    if(titleEl) titleEl.textContent = rtr("reader.pres.off_title");
    if(toolsEl) toolsEl.style.display = 'none';
    const coordsEl = document.getElementById('presentation-coords');
    if(coordsEl) coordsEl.textContent = '';
    frame.removeAttribute('data-doc-id');
    frame.src = 'about:blank';
    if(empty) empty.style.display = 'flex';
    if(frame) frame.style.display = 'none';
    if(emptyTitle) emptyTitle.textContent = rtr("reader.pres.off");
    if(emptyMsg) emptyMsg.textContent = rtr("reader.pres.off_msg");
    if(genBtn) genBtn.style.display = 'none';
    return;
  }

  if(!targetId){
    if(titleEl) titleEl.textContent = rtr("ws.reader.select_doc");
    if(toolsEl) toolsEl.style.display = 'none';
    const coordsEl = document.getElementById('presentation-coords');
    if(coordsEl) coordsEl.textContent = '';
    frame.removeAttribute('data-doc-id');
    frame.src = 'about:blank';
    if(empty) empty.style.display = 'flex';
    if(frame) frame.style.display = 'none';
    if(emptyTitle) emptyTitle.textContent = rtr("ws.reader.select_doc");
    if(emptyMsg) emptyMsg.textContent = rtr("reader.pres.select_msg");
    if(genBtn) genBtn.style.display = 'none';
    return;
  }

  const dc = (typeof allDocs !== 'undefined' && allDocs && allDocs.find(d => d.id === targetId)) ||
             (typeof curReaderDocData !== 'undefined' && curReaderDocData && curReaderDocData.id === targetId ? curReaderDocData : null);

  if(titleEl){
    titleEl.textContent = dc ? (dc.title || rtr("reader.untitled")) : rtr("ws.list.docs_loading");
  }

  if(dc && dc.has_presentation === false){
    if(toolsEl) toolsEl.style.display = 'none';
    const coordsEl = document.getElementById('presentation-coords');
    if(coordsEl) coordsEl.textContent = '';
    frame.removeAttribute('data-doc-id');
    frame.src = 'about:blank';
    if(empty) empty.style.display = 'flex';
    if(frame) frame.style.display = 'none';
    if(emptyTitle) emptyTitle.textContent = rtr("reader.pres.not_made");
    if(emptyMsg) emptyMsg.textContent = rtr("reader.pres.can_make");
    if(genBtn){
      genBtn.style.display = (typeof canWrite === 'function' && canWrite()) ? 'inline-block' : 'none';
      genBtn.textContent = rtr("ws.presentation.generate");
      genBtn.disabled = false;
    }
    return;
  }

  if(toolsEl){
    toolsEl.style.display = 'flex';
  }
  if(empty) empty.style.display = 'none';
  if(frame) frame.style.display = 'block';
  const targetUrl = '/p/presentation?id=' + encodeURIComponent(targetId) + readerKbQuery() + '&embed=true';
  if(frame.getAttribute('data-doc-id') !== targetId){
    frame.setAttribute('data-doc-id', targetId);
    frame.src = targetUrl;
    frame.onload = function(){
      syncPresentationCoords();
      try {
        if(frame.contentWindow && frame.contentWindow.Reveal){
          frame.contentWindow.Reveal.on('slidechanged', syncPresentationCoords);
          frame.contentWindow.Reveal.on('ready', syncPresentationCoords);
        }
      }catch(_){}
    };
  } else {
    syncPresentationCoords();
  }
}

function syncPresentationCoords(){
  const frame = document.getElementById('presentation-frame');
  const coordsEl = document.getElementById('presentation-coords');
  if(!frame || !frame.contentWindow || !coordsEl) return;
  try {
    const R = frame.contentWindow.Reveal;
    if(R && typeof R.getIndices === 'function'){
      const indices = R.getIndices();
      const total = typeof R.getTotalSlides === 'function' ? R.getTotalSlides() : '?';
      const h = String(indices.h + 1).padStart(2, '0');
      const v = indices.v > 0 ? '.' + String(indices.v + 1) : '';
      coordsEl.textContent = h + v + ' / ' + total;
    }
  }catch(_){}
}

function presentationSpeaker(){
  const frame = document.getElementById('presentation-frame');
  if(!frame || !frame.contentWindow) return;
  try {
    if(typeof frame.contentWindow.cbToggleSpeaker === 'function'){
      frame.contentWindow.cbToggleSpeaker();
    } else if(frame.contentWindow.Reveal){
      const notesPlugin = frame.contentWindow.Reveal.getPlugin('notes');
      if(notesPlugin && typeof notesPlugin.open === 'function'){
        notesPlugin.open();
      } else {
        const notesUrl = '/static/vendor/reveal.js/plugin/notes/speaker-view.html';
        window.open(notesUrl, 'reveal.js - Notes', 'width=1100,height=700');
      }
    }
  }catch(_){
    const notesUrl = '/static/vendor/reveal.js/plugin/notes/speaker-view.html';
    window.open(notesUrl, 'reveal.js - Notes', 'width=1100,height=700');
  }
}

function presentationOverview(){
  const frame = document.getElementById('presentation-frame');
  if(frame && frame.contentWindow && frame.contentWindow.Reveal){
    try {
      const R = frame.contentWindow.Reveal;
      if(typeof R.toggleOverview === 'function'){
        if(typeof R.isOverview === 'function'){
          R.toggleOverview(!R.isOverview());
        } else {
          R.toggleOverview();
        }
      }
    }catch(_){}
  }
}

function presentationDownloadPdf(){
  if(typeof canWrite === 'function' && !canWrite()){
    alert(rtr("reader.pres.pdf_owner_only"));
    return;
  }
  const targetId = (typeof curReaderDoc !== 'undefined' && curReaderDoc) ||
                   (typeof activeDoc !== 'undefined' && activeDoc);
  if(!targetId) return;

  const btn = document.getElementById('presentation-download-btn');
  if(btn) btn.disabled = true;

  let printFrame = document.getElementById('presentation-print-frame');
  if(!printFrame){
    printFrame = document.createElement('iframe');
    printFrame.id = 'presentation-print-frame';
    printFrame.style.position = 'fixed';
    printFrame.style.right = '0';
    printFrame.style.bottom = '0';
    printFrame.style.width = '0';
    printFrame.style.height = '0';
    printFrame.style.border = '0';
    printFrame.style.opacity = '0.01';
    printFrame.style.pointerEvents = 'none';
    document.body.appendChild(printFrame);
  }

  const printUrl = '/p/presentation?id=' + encodeURIComponent(targetId) + readerKbQuery() + '&print-pdf&embed=true';
  printFrame.src = printUrl;

  const doPrint = () => {
    try {
      printFrame.contentWindow.focus();
      printFrame.contentWindow.print();
    } catch(e) {
      console.warn('iframe print fallback:', e);
    } finally {
      if(btn) btn.disabled = false;
    }
  };

  printFrame.onload = () => {
    try {
      const cw = printFrame.contentWindow;
      const R = cw && cw.Reveal;
      if(R){
        if(typeof R.isReady === 'function' && R.isReady()){
          setTimeout(doPrint, 250);
        } else if(typeof R.on === 'function'){
          R.on('pdf-ready', () => setTimeout(doPrint, 150));
          R.on('ready', () => setTimeout(doPrint, 250));
        } else {
          setTimeout(doPrint, 500);
        }
      } else {
        setTimeout(doPrint, 600);
      }
    } catch(_) {
      setTimeout(doPrint, 600);
    }
  };
}

async function presentationShare(){
  if(typeof isPresentationEnabledForActiveTheme === 'function' && !isPresentationEnabledForActiveTheme()){
    alert(rtr("reader.pres.off_db"));
    return;
  }
  const targetId = (typeof curReaderDoc !== 'undefined' && curReaderDoc) ||
                   (typeof activeDoc !== 'undefined' && activeDoc);
  if(!targetId) return;
  const sb = document.getElementById('presentation-sharebox');
  if(!sb) return;
  sb.className = 'sharebox on';
  sb.innerHTML = '<span class="pt">' + rtr("reader.share.making") + '</span>';
  try{
    const r = await fetch(readerApiUrl('share'), {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({doc_id: targetId})
    });
    if(r.status === 401 || r.status === 404){
      if(typeof canWrite === 'function' && canWrite()){
        if(typeof expireWriteAccess === 'function') expireWriteAccess();
        sb.innerHTML = '<span class="pt">' + rtr("ws.common.session_expired") + '</span>';
      } else {
        sb.innerHTML = '<span class="pt">' + rtr("reader.share.unavailable") + '</span>';
      }
      return;
    }
    const d = await r.json();
    if(d.error || !d.path){
      sb.innerHTML = '<span class="pt">' + esc(rtr('reader.share.failed', { reason: d.error || rtr('reader.pres.unknown') })) + '</span>';
      return;
    }
    const token = d.token || (d.path.includes('s=') ? d.path.split('s=')[1] : '');
    const url = token
      ? location.origin + '/p/presentation?s=' + encodeURIComponent(token)
      : location.origin + '/p/presentation?id=' + encodeURIComponent(targetId) + readerKbQuery();
    let copied = false;
    try {
      if(navigator.clipboard && navigator.clipboard.writeText){
        await navigator.clipboard.writeText(url);
        copied = true;
      }
    }catch(_){}
    if(typeof window.gtag === 'function'){
      try{ window.gtag('event', 'share', { method: 'link', content_type: 'presentation', item_id: targetId }); }catch(_){}
    }
    sb.innerHTML = '<input id="pres-shareurl" readonly value="' + esc(url) + '" onclick="this.select()"/>' +
      '<button onclick="copyPresentationShare()">' + (copied ? rtr("reader.copied") : rtr("reader.copy")) + '</button>';
  }catch(e){
    sb.innerHTML = '<span class="pt">' + esc(rtr('reader.share.failed', { reason: String(e) })) + '</span>';
  }
}

function copyPresentationShare(){
  const i = document.getElementById('pres-shareurl');
  if(!i) return;
  i.select();
  const markCopied = () => {
    const b = document.querySelector('#presentation-sharebox button');
    if(b) b.textContent = rtr("reader.copied");
  };
  if(navigator.clipboard && navigator.clipboard.writeText){
    navigator.clipboard.writeText(i.value).then(markCopied).catch(()=>{
      try { document.execCommand('copy'); markCopied(); }catch(_){}
    });
  } else {
    try { document.execCommand('copy'); markCopied(); }catch(_){}
  }
}

function presentationFullscreen(){
  const wrap = document.getElementById('presentationwrap');
  if(!wrap) return;
  if(!document.fullscreenElement){
    wrap.requestFullscreen().catch(()=>{});
  } else {
    document.exitFullscreen().catch(()=>{});
  }
}

if(typeof window !== 'undefined'){
  window.addEventListener('message', function(e) {
    if (e.data && e.data.type === 'cb-slidechanged') {
      const coordsEl = document.getElementById('presentation-coords');
      if (coordsEl && e.data.coords) {
        coordsEl.textContent = e.data.coords;
      }
    }
  });
}

let presPollTimer = null;
let presPollTargetId = null;

function stopPresentationPolling(){
  if(presPollTimer){
    clearTimeout(presPollTimer);
    presPollTimer = null;
  }
  presPollTargetId = null;
}

function _onPresentationReady(targetId){
  if(typeof curReaderDocData !== 'undefined' && curReaderDocData && curReaderDocData.id === targetId){
    curReaderDocData.has_presentation = true;
  }
  const dc = typeof allDocs !== 'undefined' && allDocs && allDocs.find(d => d.id === targetId);
  if(dc) dc.has_presentation = true;
  const frame = document.getElementById('presentation-frame');
  // Avoid re-triggering reload if the frame already loaded this presentation
  if(frame && (frame.getAttribute('data-doc-id') !== targetId || !frame.src || frame.src === 'about:blank' || !frame.src.includes(encodeURIComponent(targetId)))){
    frame.removeAttribute('data-doc-id');
  }
  updatePresentationView(targetId);
}

async function generatePresentationForCurrentDoc(){
  if(typeof isPresentationEnabledForActiveTheme === 'function' && !isPresentationEnabledForActiveTheme()){
    alert(rtr("reader.pres.off_db"));
    return;
  }
  const targetId = (typeof curReaderDoc !== 'undefined' && curReaderDoc) ||
                   (typeof activeDoc !== 'undefined' && activeDoc);
  if(!targetId) return;
  const genBtn = document.getElementById('presentation-gen-btn');
  const emptyMsg = document.getElementById('presentation-empty-msg');
  if(genBtn){
    genBtn.disabled = true;
    genBtn.textContent = rtr("reader.pres.making");
  }
  if(emptyMsg) emptyMsg.textContent = rtr("reader.pres.requesting");

  stopPresentationPolling();

  const startTime = Date.now();

  try {
    const res = await fetch(readerApiUrl('document/presentation/generate?id=' + encodeURIComponent(targetId) + '&wait=false'), { method: 'POST' });
    const data = await res.json().catch(() => ({}));

    if(res.status === 200 && data.status === 'ready'){
      _onPresentationReady(targetId);
      return;
    }

    if(res.status === 202 || res.status === 200){
      if(emptyMsg) emptyMsg.textContent = rtr('reader.pres.writing', { seconds: 0 });
      presPollTargetId = targetId;

      const pollStep = async () => {
        if(presPollTargetId !== targetId) return;
        const elapsed = Math.round((Date.now() - startTime) / 1000);
        if(elapsed > 120){
          stopPresentationPolling();
          if(emptyMsg) emptyMsg.textContent = rtr("reader.pres.timeout");
          if(genBtn){
            genBtn.disabled = false;
            genBtn.textContent = rtr("ws.common.retry");
          }
          return;
        }

        try {
          const pollRes = await fetch(readerApiUrl('document/presentation?id=' + encodeURIComponent(targetId)));
          if(presPollTargetId !== targetId) return;
          if(!pollRes.ok){
            presPollTimer = setTimeout(pollStep, 2500);
            return;
          }
          const pollData = await pollRes.json();
          if(presPollTargetId !== targetId) return;

          if(pollData.status === 'ready'){
            stopPresentationPolling();
            _onPresentationReady(targetId);
          } else if(pollData.status === 'failed'){
            stopPresentationPolling();
            if(emptyMsg) emptyMsg.textContent = rtr('reader.pres.failed', { reason: pollData.error_message || rtr('reader.pres.unknown') });
            if(genBtn){
              genBtn.disabled = false;
              genBtn.textContent = rtr("ws.common.retry");
            }
          } else {
            if(emptyMsg) emptyMsg.textContent = rtr('reader.pres.writing', { seconds: elapsed });
            presPollTimer = setTimeout(pollStep, 2500);
          }
        } catch(_pollErr){
          if(presPollTargetId === targetId){
            presPollTimer = setTimeout(pollStep, 2500);
          }
        }
      };

      presPollTimer = setTimeout(pollStep, 2500);
    } else {
      if(emptyMsg) emptyMsg.textContent = rtr('reader.pres.failed', { reason: data.error || rtr('reader.pres.try_later') });
      if(genBtn){
        genBtn.disabled = false;
        genBtn.textContent = rtr("ws.common.retry");
      }
    }
  } catch(e) {
    if(emptyMsg) emptyMsg.textContent = rtr('reader.pres.network_error', { error: e });
    if(genBtn){
      genBtn.disabled = false;
      genBtn.textContent = rtr("ws.common.retry");
    }
  }
}

function openDocPresentation(docId){
  const targetId = docId || (typeof curReaderDoc !== 'undefined' && curReaderDoc) ||
                (typeof curReaderDocData !== 'undefined' && curReaderDocData && curReaderDocData.id);
  if(targetId){
    curReaderDoc = targetId;
    activeDoc = targetId;
  }
  setCenterView('presentation');
  updatePresentationView(targetId);
}

// --- Reader Text Entity Marks (HW-5) ---
let readerActiveMarkRanges = [];
let readerHoverMarkEntityId = null;
let readerMarksInitializedListeners = false;

function isEntityMarksExcluded(){
  if(typeof window === 'undefined') return true;
  if(window.location && window.location.search && (window.location.search.includes('s=') || window.location.pathname.startsWith('/p'))) return true;
  if(document.body && document.body.classList && document.body.classList.contains('shared-page')) return true;
  return false;
}

function isAsciiAlphanumericChar(c){
  if(!c) return false;
  const code = c.charCodeAt(0);
  return (code >= 48 && code <= 57) || // 0-9
         (code >= 65 && code <= 90) || // A-Z
         (code >= 97 && code <= 122);  // a-z
}

function collectEntityCandidates(dc){
  if(!dc || !Array.isArray(dc.nodes)) return { candidates: [], totalCandidateNames: 0 };
  const rawCandidates = [];
  let totalCandidateNames = 0;

  dc.nodes.forEach(n => {
    const names = [];
    // Only a name of the entity is a candidate. The identifier is not a name (PRINCIPLES.md C1).
    if(n.label) names.push(n.label);
    else if(n.name) names.push(n.name);
    if(Array.isArray(n.aliases)){
      n.aliases.forEach(a => { if(a && typeof a === 'string') names.push(a); });
    }
    names.forEach(nameStr => {
      const trimmed = nameStr.trim();
      if(!trimmed) return;
      totalCandidateNames++;
      if(trimmed.length < 2) return;
      const isShortAscii = trimmed.length <= 3 && /^[A-Za-z0-9]{2,3}$/.test(trimmed);
      rawCandidates.push({
        text: trimmed,
        entityId: n.id,
        isShortAscii: isShortAscii,
        key: isShortAscii ? ('exact:' + trimmed) : ('lower:' + trimmed.toLowerCase())
      });
    });
  });

  const keyEntities = new Map();
  rawCandidates.forEach(cand => {
    if(!keyEntities.has(cand.key)) keyEntities.set(cand.key, new Set());
    keyEntities.get(cand.key).add(cand.entityId);
  });

  const validCandidates = [];
  const seenPairs = new Set();
  rawCandidates.forEach(cand => {
    const entSet = keyEntities.get(cand.key);
    if(entSet && entSet.size === 1){
      const pair = cand.key + '|' + cand.entityId;
      if(!seenPairs.has(pair)){
        seenPairs.add(pair);
        validCandidates.push(cand);
      }
    }
  });

  validCandidates.sort((a, b) => (b.text.length - a.text.length) || a.text.localeCompare(b.text));

  return { candidates: validCandidates, totalCandidateNames };
}

function findMarkableTextNodes(root){
  const textNodes = [];
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode: function(node){
      if(!node.nodeValue || !node.nodeValue.trim()) return NodeFilter.FILTER_REJECT;
      const parent = node.parentElement;
      if(!parent) return NodeFilter.FILTER_REJECT;
      if(parent.closest('h1, h2, h3, h4, h5, h6, code, pre, a, .katex, .katex-display, script, style')){
        return NodeFilter.FILTER_REJECT;
      }
      if(!parent.closest('p, li, td, th')){
        return NodeFilter.FILTER_REJECT;
      }
      return NodeFilter.FILTER_ACCEPT;
    }
  });
  let curr;
  while((curr = walker.nextNode())){
    textNodes.push(curr);
  }
  return textNodes;
}

function updateReaderLegend(message, isVisible){
  const el = document.getElementById('rlegend');
  if(!el) return;
  if(isVisible && message){
    el.textContent = message;
    el.classList.add('visible');
  } else {
    el.textContent = '';
    el.classList.remove('visible');
  }
}

function clearEntityMarks(){
  readerActiveMarkRanges = [];
  readerHoverMarkEntityId = null;
  if(typeof CSS !== 'undefined' && CSS.highlights){
    CSS.highlights.delete('claire-entity');
    CSS.highlights.delete('claire-entity-hover');
  }
  updateReaderLegend('', false);
}

function applyEntityMarks(dc){
  if(isEntityMarksExcluded()) {
    clearEntityMarks();
    return;
  }
  const body = document.getElementById('rbody');
  if(!body) return;

  if(typeof CSS === 'undefined' || !CSS.highlights){
    const msg = rtr('reader.marks.unsupported');
    updateReaderLegend(msg, true);
    return;
  }

  readerActiveMarkRanges = [];
  readerHoverMarkEntityId = null;
  CSS.highlights.delete('claire-entity');
  CSS.highlights.delete('claire-entity-hover');

  let marksEnabled = true;
  try{
    if(typeof localStorage !== 'undefined' && localStorage.getItem('claireEntityMarks') === '0'){
      marksEnabled = false;
    }
  }catch(_){}

  if(!marksEnabled){
    const msg = rtr('reader.marks.disabled');
    updateReaderLegend(msg, true);
    return;
  }

  const bodyText = body.textContent || '';
  if(bodyText.length > 400000){
    const msg = rtr('reader.marks.limits_exceeded');
    updateReaderLegend(msg, true);
    return;
  }

  const { candidates, totalCandidateNames } = collectEntityCandidates(dc);
  if(totalCandidateNames > 300){
    const msg = rtr('reader.marks.limits_exceeded');
    updateReaderLegend(msg, true);
    return;
  }

  if(!candidates.length){
    updateReaderLegend('', false);
    return;
  }

  const textNodes = findMarkableTextNodes(body);
  textNodes.forEach(textNode => {
    const content = textNode.nodeValue || '';
    if(!content) return;
    const lowerContent = content.toLowerCase();
    const intervals = [];

    candidates.forEach(cand => {
      const candText = cand.text;
      const candLen = candText.length;
      const searchTarget = cand.isShortAscii ? content : lowerContent;
      const query = cand.isShortAscii ? candText : candText.toLowerCase();
      let searchIdx = 0;

      while(searchIdx < searchTarget.length){
        const idx = searchTarget.indexOf(query, searchIdx);
        if(idx === -1) break;
        const endIdx = idx + candLen;
        searchIdx = idx + 1;

        if(isAsciiAlphanumericChar(candText[0])){
          if(idx > 0 && isAsciiAlphanumericChar(content[idx - 1])) continue;
        }
        if(isAsciiAlphanumericChar(candText[candLen - 1])){
          if(endIdx < content.length && isAsciiAlphanumericChar(content[endIdx])) continue;
        }

        let overlaps = false;
        for(let k = 0; k < intervals.length; k++){
          const inv = intervals[k];
          if(Math.max(idx, inv.start) < Math.min(endIdx, inv.end)){
            overlaps = true;
            break;
          }
        }
        if(overlaps) continue;

        intervals.push({ start: idx, end: endIdx });
        try{
          const range = new Range();
          range.setStart(textNode, idx);
          range.setEnd(textNode, endIdx);
          readerActiveMarkRanges.push({
            range: range,
            entityId: cand.entityId,
            textNode: textNode,
            start: idx,
            end: endIdx
          });
        }catch(_){}
      }
    });
  });

  if(readerActiveMarkRanges.length > 0){
    const ranges = readerActiveMarkRanges.map(item => item.range);
    const highlight = new Highlight(...ranges);
    CSS.highlights.set('claire-entity', highlight);
    const msg = rtr('reader.marks.legend');
    updateReaderLegend(msg, true);
  } else {
    updateReaderLegend('', false);
  }

  setupReaderMarkListeners();
}

function getCaretPosition(e){
  if(document.caretPositionFromPoint){
    const pos = document.caretPositionFromPoint(e.clientX, e.clientY);
    if(pos) return { node: pos.offsetNode, offset: pos.offset };
  } else if(document.caretRangeFromPoint){
    const r = document.caretRangeFromPoint(e.clientX, e.clientY);
    if(r) return { node: r.startContainer, offset: r.startOffset };
  }
  return null;
}

// The mark at a pointer position, or null. The caret functions give the nearest character also for a point in the margin or after the end of a line, thus the point must be in the rectangle of the range.
function readerMarkAtPoint(e){
  if(!readerActiveMarkRanges.length) return null;
  const caret = getCaretPosition(e);
  if(!caret) return null;
  for(let i = 0; i < readerActiveMarkRanges.length; i++){
    const item = readerActiveMarkRanges[i];
    if(item.textNode !== caret.node || caret.offset < item.start || caret.offset > item.end) continue;
    const rects = item.range.getClientRects();
    for(let k = 0; k < rects.length; k++){
      const r = rects[k];
      if(e.clientX >= r.left - 1 && e.clientX <= r.right + 1 && e.clientY >= r.top - 1 && e.clientY <= r.bottom + 1) return item;
    }
  }
  return null;
}

function setupReaderMarkListeners(){
  if(readerMarksInitializedListeners) return;
  const body = document.getElementById('rbody');
  if(!body) return;
  readerMarksInitializedListeners = true;

  body.addEventListener('mousemove', e => {
    if(!readerActiveMarkRanges.length){
      if(body.style.cursor === 'pointer') body.style.cursor = '';
      return;
    }
    const hitItem = readerMarkAtPoint(e);
    if(hitItem){
      body.style.cursor = 'pointer';
      if(readerHoverMarkEntityId !== hitItem.entityId){
        readerHoverMarkEntityId = hitItem.entityId;
        if(typeof window.setEntityHoverHighlight === 'function'){
          window.setEntityHoverHighlight(hitItem.entityId);
        }
      }
    } else {
      if(body.style.cursor === 'pointer') body.style.cursor = '';
      if(readerHoverMarkEntityId !== null){
        readerHoverMarkEntityId = null;
        if(typeof window.clearEntityHoverHighlight === 'function'){
          window.clearEntityHoverHighlight();
        }
      }
    }
  });

  body.addEventListener('mouseleave', () => {
    if(body.style.cursor === 'pointer') body.style.cursor = '';
    if(readerHoverMarkEntityId !== null){
      readerHoverMarkEntityId = null;
      if(typeof window.clearEntityHoverHighlight === 'function'){
        window.clearEntityHoverHighlight();
      }
    }
  });

  body.addEventListener('click', e => {
    if(!readerActiveMarkRanges.length) return;
    const sel = window.getSelection();
    if(sel && !sel.isCollapsed && sel.toString().trim().length > 0) return;

    const hitItem = readerMarkAtPoint(e);
    if(hitItem){
      if(typeof selectDocEntity === 'function'){
        selectDocEntity(hitItem.entityId);
      } else if(typeof loadNode === 'function'){
        loadNode(hitItem.entityId, true, false);
      }
    }
  });
}

function setReaderEntityMarkHover(entityId){
  if(!entityId || typeof CSS === 'undefined' || !CSS.highlights) return;
  const matchingRanges = [];
  readerActiveMarkRanges.forEach(item => {
    if(item.entityId === entityId) matchingRanges.push(item.range);
  });
  if(matchingRanges.length > 0){
    const hoverHighlight = new Highlight(...matchingRanges);
    CSS.highlights.set('claire-entity-hover', hoverHighlight);
  } else {
    CSS.highlights.delete('claire-entity-hover');
  }
}

function clearReaderEntityMarkHover(){
  if(typeof CSS === 'undefined' || !CSS.highlights) return;
  CSS.highlights.delete('claire-entity-hover');
}

// --- Global Export & Namespace ---
const ClaireReader = {
  applyEntityMarks,
  clearEntityMarks,
  setReaderEntityMarkHover,
  clearReaderEntityMarkHover,
  openReader,
  closeReader,
  applyReadFS,
  setReadFS,
  updateReaderRail,
  openSttReader,
  closeSttReader,
  renderSttLines,
  filterSttLines,
  copyTimestamp,
  copySttText,
  editDocTitle,
  shareDoc,
  copyShare,
  openDocPresentation,
  updatePresentationView,
  generatePresentationForCurrentDoc,
  presentationDownloadPdf,
  presentationPrintPdf: presentationDownloadPdf,
  docMetaHtml,
  docMetaHeaderHtml: docMetaHtml,
  docBiblioHtml,
  docMetaMessageHtml,
  getDocMetaMessages,
  renderReader,
  setCenterView,
  openDocGraph,
  applyDescLines,
  setDescLines,
  doclistToolbarHtml,
  get curReaderDoc() { return curReaderDoc; },
  get curReaderDocData() { return curReaderDocData; },
};

if (typeof window !== 'undefined') {
  window.ClaireReader = ClaireReader;
  window.openReader = openReader;
  window.closeReader = closeReader;
  window.applyReadFS = applyReadFS;
  window.setReadFS = setReadFS;
  window.updateReaderRail = updateReaderRail;
  window.openSttReader = openSttReader;
  window.closeSttReader = closeSttReader;
  window.renderSttLines = renderSttLines;
  window.filterSttLines = filterSttLines;
  window.copyTimestamp = copyTimestamp;
  window.copySttText = copySttText;
  window.editDocTitle = editDocTitle;
  window.shareDoc = shareDoc;
  window.copyShare = copyShare;
  window.openDocPresentation = openDocPresentation;
  window.updatePresentationView = updatePresentationView;
  window.generatePresentationForCurrentDoc = generatePresentationForCurrentDoc;
  window.presentationSpeaker = presentationSpeaker;
  window.presentationOverview = presentationOverview;
  window.presentationDownloadPdf = presentationDownloadPdf;
  window.presentationPrintPdf = presentationDownloadPdf;
  window.presentationShare = presentationShare;
  window.copyPresentationShare = copyPresentationShare;
  window.presentationFullscreen = presentationFullscreen;
  window.syncPresentationCoords = syncPresentationCoords;
  window.docMetaHtml = docMetaHtml;
  window.docMetaHeaderHtml = docMetaHtml;
  window.docBiblioHtml = docBiblioHtml;
  window.docMetaMessageHtml = docMetaMessageHtml;
  window.getDocMetaMessages = getDocMetaMessages;
  window.renderReader = renderReader;
  window.setCenterView = setCenterView;
  window.openDocGraph = openDocGraph;
  window.applyDescLines = applyDescLines;
  window.setDescLines = setDescLines;
  window.doclistToolbarHtml = doclistToolbarHtml;
  window.applyEntityMarks = applyEntityMarks;
  window.clearEntityMarks = clearEntityMarks;
  window.setReaderEntityMarkHover = setReaderEntityMarkHover;
  window.clearReaderEntityMarkHover = clearReaderEntityMarkHover;
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = ClaireReader;
}
