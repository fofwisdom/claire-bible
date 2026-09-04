/**
 * Claire Bible - Document Reader & Modal Viewer
 * Manages document reading view, heading navigation rail, STT modal, font sizing, and sharing.
 */

// 목록 설명 줄수(0/2/4) — 브라우저에 기억. 문서 많아지면 제목/설명이 height 를 너무
// 차지한다는 피드백(사용자 지적) → #docs 에 lc0/lc4 클래스로 CSS line-clamp 토글(기본 2줄).
let descLines = 3;
try{ const v=parseInt(localStorage.getItem('claireDescLines')); if(v===0||v===3||v===4||v===2) descLines=(v===0?0:3); }catch(e){}
function doclistToolbarHtml(){
  return '<div class="doclist-toolbar">'+
    '<select id="desclines" onchange="setDescLines(this.value)" title="목록 설명 줄수" aria-label="목록 설명 줄수">'+
    '<option value="0"'+(descLines===0?' selected':'')+'>제목만 표시</option>'+
    '<option value="3"'+(descLines===3?' selected':'')+'>요약 표시</option>'+
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
}

// 중앙 읽기 — 좌측 문서의 '읽기' 버튼/노드 상세의 📖 로 연다(nav 와 분리, 사용자 요구).
let curReaderDoc=null;   // 현재 읽기 팝업의 문서 id(🔗 공유 링크 생성 대상)
let curReaderDocData=null; // 현재 읽기 팝업의 문서 객체
let readerReturnFocus=null, readerReturnDocId=null;
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
      const label = el.tagName === 'TABLE' ? '데이터 표' : (el.tagName === 'PRE' ? '코드 블록' : '수식 블록');
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

  return false;
}
async function markDocumentSeen(docId){
  if(!canWrite()) return;
  try{
    const r=await fetch('document/seen',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({id:docId})});
    if(r.status===401||r.status===404){ expireWriteAccess(); return; }
    if(!r.ok) return;
    const dc=allDocs && allDocs.find(d=>d.id===docId);
    if(dc && dc.seen!==1){ dc.seen=1; renderDocs(document.getElementById('docq').value); }
  }catch(_){}
}
function setCenterView(mode){
  centerView = (mode==='graph' ? 'graph' : 'reader');
  document.body.dataset.centerView = centerView;
  const mt = document.getElementById('menu-section-title');
  if(mt){ mt.textContent = (centerView==='graph' ? '그래프 도구' : '문서와 그래프'); }
  if(centerView==='graph'){
    graphCamera = null;
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
  readerReturnFocus=document.activeElement;
  readerReturnDocId=docId;
  const sb=document.getElementById('sharebox'); if(sb){ sb.className='sharebox'; sb.innerHTML=''; }  // 이전 공유링크 닫기
  applyReadFS();   // 저장된 글자 크기 적용
  document.getElementById('rtitle').textContent='문서 불러오는 중…';
  document.getElementById('rbody').innerHTML='';
  if(panel) panel.innerHTML='<p class=hint>문서 불러오는 중…</p>';
  const r=document.getElementById('reader');
  r.setAttribute('aria-hidden','false');
  r.setAttribute('aria-busy','true');
  if(drawerOpen || detailOpen){
    if(compactMQ.matches || mobileMQ.matches) closeDrawer(false, false);
  }
  if(mobileMQ.matches){
    r.classList.add('open');
    document.body.classList.add('reader-open');
    setReaderBackgroundInert(true);
    if(pushHist) pushAppHistory({ modal: 'reader', docId: docId });
    requestAnimationFrame(()=>r.querySelector('.sheet')?.focus());
  } else {
    setCenterView('reader');
    renderDocs(document.getElementById('docq').value);
  }
  applyView();
  if(activeDoc){
    const targetDocId=activeDoc;
    requestAnimationFrame(()=>{ if(net && activeDoc===targetDocId){
      const ids=[]; allNodes.forEach(n=>{ if(!n.hidden && (n.sources||[]).includes(targetDocId)) ids.push(n.id); });
      if(ids.length) net.selectNodes(ids);
      if(!ids.length) resetGraphCamera(); else cameraToNodes(ids);
    }});
  }
  fetch('document?id='+encodeURIComponent(docId)).then(x=>x.json()).then(dc=>{
    if(!dc || dc.error){
      document.getElementById('rbody').innerHTML='<p class=hint>문서를 찾을 수 없습니다.</p>';
      r.setAttribute('aria-busy','false');
      if(activeDoc===docId && panel) panel.innerHTML='<p class=hint>문서를 찾을 수 없습니다.</p>';
      return;
    }
    renderReader(dc);
    if(activeDoc===docId) renderDocPanel(dc);
    markDocumentSeen(docId);
  }).catch(()=>{
    document.getElementById('rbody').innerHTML='<p class=hint>문서 로드 실패.</p>';
    r.setAttribute('aria-busy','false');
    if(activeDoc===docId && panel) panel.innerHTML='<p class=hint>문서 로드 실패.</p>';
  });
}
function docMetaHtml(dc){
  if(!dc) return '';
  const hasUrl = !!dc.url;
  const isTrunc = !!(dc.raw_truncated || (dc.meta && dc.meta.raw_truncated));
  const isAppTrunc = isTrunc && !!(dc.appendix_truncated || (dc.meta && dc.meta.appendix_truncated));
  const isRefTrunc = isTrunc && !!(dc.references_truncated || (dc.meta && dc.meta.references_truncated));
  const isParserFallback = !!(dc.pdf_parser_fallback || (dc.meta && dc.meta.pdf_parser_fallback));
  const fallbackReason = (dc.pdf_parser_fallback_reason || (dc.meta && dc.meta.pdf_parser_fallback_reason) || 'Docling 런타임 오류');
  const directive = (dc.directive || (dc.meta && dc.meta.directive) || '').trim();
  const author = (dc.author || (dc.meta && dc.meta.author) || (dc.biblio && dc.biblio.author) || (dc.meta && dc.meta.biblio && dc.meta.biblio.author) || '').trim();
  const pubAt = (dc.published_at || (dc.meta && dc.meta.published_at) || (dc.biblio && dc.biblio.published_at) || (dc.meta && dc.meta.biblio && dc.meta.biblio.published_at) || '').trim();
  const isStt = !!(dc.is_stt || (dc.meta && (dc.meta.is_stt || dc.meta.stt_applied || dc.meta.stt)));
  const isSttTrunc = isStt && !!(dc.stt_truncated || (dc.meta && dc.meta.stt_truncated) || isTrunc);
  const presentation = dc.presentation_pdf || (dc.meta && dc.meta.presentation_pdf) || {};
  const hasPresentation = presentation.status === 'available' && !!presentation.public_url;
  if(!hasUrl && !isTrunc && !directive && !isStt && !author && !pubAt && !isParserFallback && !hasPresentation) return '';
  let h='<p class=docmeta>';
  if(hasUrl){
    h+='<a href="'+esc(dc.url)+'" target=_blank rel=noopener>↗ 원문 열기</a>';
    if(isStt){
      h+=' <a href="#" class="stt-link" onclick="openSttReader();return false;" title="음성 인식(STT) 전사 텍스트 열기">↗ 전사 열기</a>';
    }
    if(hasPresentation){
      h+=' <a href="'+esc(presentation.public_url)+'" target=_blank rel=noopener>↗ Presentation PDF</a>';
    }
  } else {
    if(isStt){
      h+='<a href="#" class="stt-link" onclick="openSttReader();return false;" title="음성 인식(STT) 전사 텍스트 열기">↗ 전사 열기</a>';
    } else {
      h+='<span></span>';
    }
  }
  let tags=[];
  if(author || pubAt){
    let bibTxt = '';
    if(author && pubAt) bibTxt = author + ' (' + pubAt + ')';
    else if(author) bibTxt = author;
    else if(pubAt) bibTxt = pubAt;
    tags.push('<span class="directive-tag" style="background:rgba(255,255,255,0.06);border-color:rgba(255,255,255,0.15);color:var(--muted)" title="서지 메타데이터: '+esc(bibTxt)+'">✍️ '+esc(bibTxt)+'</span>');
  }
  if(isParserFallback){
    tags.push('<span class="trunc-tag parser-fallback-tag" title="Docling 실패 사유: '+esc(fallbackReason)+'">⚠️ Docling 폴백 (PyPDF)</span>');
  }
  if(hasPresentation){
    const pdfChars = Number(presentation.raw_chars || 0);
    const pdfParser = String(presentation.parser_used || '').trim();
    const artifactState = presentation.artifact_path ? '원본 보존됨' : '원본 경로 미확인';
    const bundleIcon = isStt ? '🎙️⚡📄' : '🔤⚡📄';
    const bundleType = isStt ? 'STT×PDF' : 'CC×PDF';
    const pdfLabel = bundleIcon+' '+bundleType;
    const bundleSource = isStt ? '영상 음성 전사와' : '영상 자막과';
    const metaParts = [];
    if(pdfChars > 0) metaParts.push(pdfChars.toLocaleString()+'자');
    if(pdfParser) metaParts.push(pdfParser);
    if(presentation.parser_fallback) metaParts.push('Docling 폴백');
    const metaDetail = metaParts.length ? ' ('+metaParts.join(' · ')+')' : '';
    const tip = bundleSource+' 원본 PDF 함께 적재'+metaDetail+' · '+artifactState;
    const tagClass = 'directive-tag' + (isStt ? ' stt-tag' : '');
    tags.push('<span class="'+tagClass+'" title="'+esc(tip)+'">'+pdfLabel+'</span>');
  }
  if(directive){
    const dispDir = directive.length > 25 ? directive.slice(0, 25) + '…' : directive;
    tags.push('<span class="directive-tag" title="적재 시 지정한 초점: '+esc(directive)+'">🎯 '+esc(dispDir)+'</span>');
  }
  if(isStt && !hasPresentation){
    tags.push('<span class="directive-tag stt-tag" title="음성 인식(STT)을 적용하여 작성한 문서">🎙️ STT</span>');
  }
  if(isAppTrunc && isRefTrunc){
    const orig=(dc.orig_chars || (dc.meta && dc.meta.orig_chars)) || 0;
    const raw=(dc.raw_chars || (dc.meta && dc.meta.raw_chars)) || 0;
    let tip = '원문의 부록(Appendix) 및 참고문헌(References) 부분을 제외한 문서';
    let label='✂️ 부록·참고문헌 제외';
    if(orig > 0 && raw > 0){
      tip+=' (원문: '+orig.toLocaleString()+'자 → 적재: '+raw.toLocaleString()+'자)';
      label+=' ('+raw.toLocaleString()+' / '+orig.toLocaleString()+'자)';
    } else if(raw > 0){
      label+=' ('+raw.toLocaleString()+'자)';
    }
    tags.push('<span class="trunc-tag trunc-appendix" title="'+esc(tip)+'">'+esc(label)+'</span>');
  } else if(isRefTrunc){
    const orig=(dc.orig_chars || (dc.meta && dc.meta.orig_chars)) || 0;
    const raw=(dc.raw_chars || (dc.meta && dc.meta.raw_chars)) || 0;
    let tip = '원문의 참고문헌(References) 부분을 제외한 문서';
    let label='✂️ 참고문헌 제외';
    if(orig > 0 && raw > 0){
      tip+=' (원문: '+orig.toLocaleString()+'자 → 적재: '+raw.toLocaleString()+'자)';
      label+=' ('+raw.toLocaleString()+' / '+orig.toLocaleString()+'자)';
    } else if(raw > 0){
      label+=' ('+raw.toLocaleString()+'자)';
    }
    tags.push('<span class="trunc-tag trunc-references" title="'+esc(tip)+'">'+esc(label)+'</span>');
  } else if(isAppTrunc){
    const orig=(dc.orig_chars || (dc.meta && dc.meta.orig_chars)) || 0;
    const raw=(dc.raw_chars || (dc.meta && dc.meta.raw_chars)) || 0;
    let tip = '원문의 부록(Appendix) 부분을 절단한 문서';
    let label='✂️ 원문 일부 절단';
    if(orig > 0 && raw > 0){
      tip+=' (원문: '+orig.toLocaleString()+'자 → 적재: '+raw.toLocaleString()+'자)';
      label+=' ('+raw.toLocaleString()+' / '+orig.toLocaleString()+'자)';
    } else if(raw > 0){
      label+=' ('+raw.toLocaleString()+'자)';
    }
    tags.push('<span class="trunc-tag trunc-appendix" title="'+esc(tip)+'">'+esc(label)+'</span>');
  } else if(isSttTrunc){
    const orig=(dc.stt_orig_chars || (dc.meta && dc.meta.stt_orig_chars) || dc.orig_chars || (dc.meta && dc.meta.orig_chars)) || 0;
    const raw=(dc.stt_raw_chars || (dc.meta && dc.meta.stt_raw_chars) || dc.raw_chars || (dc.meta && dc.meta.raw_chars)) || 0;
    let tip = '음성 전사(STT) 전문이 일부 절단된 상태에서 본문(상세)이 작성된 문서';
    let label = '✂️ STT 일부 절단';
    if(orig > 0 && raw > 0){
      tip += ' (원문: '+orig.toLocaleString()+'자 → 적재: '+raw.toLocaleString()+'자)';
      label += ' ('+raw.toLocaleString()+' / '+orig.toLocaleString()+'자)';
    } else if(raw > 0){
      label += ' ('+raw.toLocaleString()+'자)';
    }
    tags.push('<span class="trunc-tag trunc-stt" title="'+esc(tip)+'">'+esc(label)+'</span>');
  } else if(isTrunc){
    const orig=(dc.orig_chars || (dc.meta && dc.meta.orig_chars)) || 0;
    const raw=(dc.raw_chars || (dc.meta && dc.meta.raw_chars)) || 0;
    let tip = '글자 수 상한으로 원문 일부를 절단한 문서';
    let label='✂️ 원문 일부 절단';
    if(orig > 0 && raw > 0){
      tip+=' (원문: '+orig.toLocaleString()+'자 → 적재: '+raw.toLocaleString()+'자)';
      label+=' ('+raw.toLocaleString()+' / '+orig.toLocaleString()+'자)';
    } else if(raw > 0){
      label+=' ('+raw.toLocaleString()+'자)';
    }
    tags.push('<span class="trunc-tag" title="'+esc(tip)+'">'+esc(label)+'</span>');
  }
  if(tags.length){
    h+='<span class="docmeta-tags">'+tags.join(' ')+'</span>';
  }
  h+='</p>';
  return h;
}
function renderReader(dc){
  curReaderDocData=dc;
  if(dc && dc.title){
    document.title = dc.title + ' — Claire Bible';
  } else {
    document.title = '문서 — Claire Bible';
  }
  document.getElementById('rtitle').innerHTML = esc(dc.title||'(제목 없음)')
    + (dc.source_type?' <span class=rmeta>'+esc(dc.source_type)+'</span>':'');
  let h='';
  h+=docMetaHtml(dc);
  const isStt = !!(dc.is_stt || (dc.meta && (dc.meta.is_stt || dc.meta.stt_applied || dc.meta.stt)));
  const isSttTrunc = isStt && !!(dc.stt_truncated || (dc.meta && dc.meta.stt_truncated) || dc.raw_truncated || (dc.meta && dc.meta.raw_truncated));
  if(isSttTrunc){
    h+='<div class="stt-trunc-banner">⚠️ <strong>음성 전사(STT) 일부 절단 안내</strong>: 전체 전사 내용 중 일부만 반영된 상태에서 본문(상세)이 작성되었습니다. 전체 재전사 명령: <code>claire video-reprocess --doc-id '+esc(dc.id||'')+' --apply --full-content</code></div>';
  }
  h+=extraSourcesHtml(dc);
  const directive = (dc.directive || (dc.meta && dc.meta.directive) || '').trim();
  if(directive){
    h+='<div class="rsection">초점</div><div class="doc-content" style="margin-bottom:.8em">🎯 <strong>'+esc(directive)+'</strong></div>';
  }
  if(dc.summary) h+='<div class=rsection>요약</div><div class="doc-content">'+renderContent(dc.summary, dc.detail_format)+'</div>';
  if(dc.detail_html){
    const purifier=window.DOMPurify;
    const cleanHtml=(purifier && typeof purifier.sanitize==='function')?purifier.sanitize(dc.detail_html, DOMPURIFY_OPTS):dc.detail_html;
    h+='<div class=rsection>상세</div><div class="doc-content">'+cleanHtml+'</div>';
  }else if(dc.detail){
    h+='<div class=rsection>상세</div><div class="doc-content">'+renderContent(dc.detail, dc.detail_format)+'</div>';
  }
  if(!dc.summary && !dc.detail && !dc.detail_html) h+='<p class=hint>문서에 요약/상세 내용이 없습니다.</p>';
  const body=document.getElementById('rbody'); body.innerHTML=h; body.scrollTop=0; body.scrollLeft=0;
  applyMathRendering(body);
  document.getElementById('reader').setAttribute('aria-busy','false');
  updateReaderRail();
  setupReaderAccessibleBlocks(body);
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
    if(!el) return 0;
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
      alert('음성 전사(STT) 데이터가 없는 문서입니다.');
      return;
    }

    const titleEl = document.getElementById('stttitle');
    if(titleEl) titleEl.textContent = '🎙️ 음성 전사 (STT) — ' + (dc.title || '(제목 없음)');

    const metaEl = document.getElementById('sttmeta');
    let metaTxt = '';
    const dur = (dc.meta && dc.meta.duration_sec) || dc.duration_sec || 0;
    if(dur > 0){
      const m = Math.floor(dur / 60);
      const s = Math.floor(dur % 60);
      metaTxt += '재생 시간: ' + m + '분 ' + s + '초';
    }
    const segs = dc.transcript_segments || (dc.meta && dc.meta.transcript_segments) || [];
    if(segs.length > 0){
      metaTxt += (metaTxt ? ' · ' : '') + '총 ' + segs.length.toLocaleString() + '개 발화 구간';
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
    fetch('document?id=' + encodeURIComponent(did)).then(r=>r.json()).then(dc=>{
      _render(dc);
    }).catch(e=>{
      alert('전사 데이터를 불러오지 못했습니다: ' + e);
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
    h += '<div class="stt-trunc-banner">⚠️ <strong>전사 일부 절단 상태</strong>: 글자 수 상한 또는 오디오 구간 누락으로 인해 음성 전사의 일부만 반영되었습니다. 전체 내용을 복원하려면 <code>claire video-reprocess --doc-id ' + esc(dc.id||'') + ' --apply --full-content</code>를 실행하십시오.</div>';
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
        h += '<span class="stt-ts" title="클릭하여 타임스탬프 복사" data-ts="' + esc(ts) + '" onclick="copyTimestamp(this.dataset.ts)">[' + esc(ts) + ']</span>';
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
    h = '<p class="hint">저장된 전사 내용이 없습니다.</p>';
  }

  body.innerHTML = h;
  if(countEl){
    if(q){
      countEl.textContent = matchCount.toLocaleString() + ' / ' + totalCount.toLocaleString() + '개 일치';
    } else {
      countEl.textContent = totalCount > 0 ? totalCount.toLocaleString() + '개 항목' : '';
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
      alert('클립보드에 전사 내용이 복사되었습니다.');
    }).catch(()=>{
      alert('복사에 실패했습니다.');
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
// [1홉 병합, ONEHOP_MERGE_DESIGN.md] 이 문서에 흡수된 부가 출처 목록(원문 링크 계보).
function extraSourcesHtml(dc){
  const es=dc.extra_sources||[];
  if(!es.length) return '';
  return '<div class=rsection>병합된 출처 ('+es.length+')</div><ul class=srclist>'+
    es.map(s=>'<li><a href="'+esc(s.url||'')+'" target=_blank rel=noopener>'+
      esc(s.title||s.url||'')+'</a></li>').join('')+'</ul>';
}
function closeReader(focus=false){
  if(typeof window !== 'undefined' && window.history && window.history.state && window.history.state.modal === 'reader' && !isPoppingHistory){
    window.history.back();
    return;
  }
  document.title = 'Claire Bible — 지식 그래프';
  hideNodePop();
  const r=document.getElementById('reader');
  r.classList.remove('open'); r.setAttribute('aria-hidden','true'); r.setAttribute('aria-busy','false');
  document.body.classList.remove('reader-open');
  setReaderBackgroundInert(false); syncWorkspaceLayout();
  const sb=document.getElementById('sharebox'); if(sb) sb.className='sharebox';
  let target=readerReturnFocus && readerReturnFocus.isConnected ? readerReturnFocus : null;
  if(!target && readerReturnDocId){
    target=[...document.querySelectorAll('[data-read-doc]')].find(el=>el.dataset.readDoc===readerReturnDocId);
  }
  if(!target) target=mobileMQ.matches ? paneTabs[activePane] : document.getElementById('q');
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
// /share 가 공유 토큰을 발급(인증 필요) → /p?s=token 은 비인증으로 그 문서만 보여준다.
async function editDocTitle(){
  if(!canWrite() || !curReaderDoc) return;
  const current = (curReaderDocData && curReaderDocData.title && curReaderDocData.title !== '(제목 없음)') ? curReaderDocData.title : '';
  const newTitle = prompt('새 제목을 입력하세요:', current);
  if(newTitle === null) return;
  const trimmed = newTitle.trim();
  try{
    const r = await fetch('document/title', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({id: curReaderDoc, title: trimmed})
    });
    if(r.status === 401 || r.status === 404){
      expireWriteAccess();
      alert('세션 만료 또는 권한 없음 — 텔레그램 /web 으로 다시 접속하세요');
      return;
    }
    if(!r.ok){
      const err = await r.json().catch(()=>({}));
      alert('제목 변경 실패: ' + (err.detail || err.error || ('HTTP ' + r.status)));
      return;
    }
    const d = await r.json();
    const updatedTitle = d.title || '(제목 없음)';
    if(curReaderDocData) curReaderDocData.title = d.title;
    document.getElementById('rtitle').innerHTML = esc(updatedTitle)
      + (curReaderDocData && curReaderDocData.source_type ? ' <span class=rmeta>' + esc(curReaderDocData.source_type) + '</span>' : '');
    const dc = allDocs && allDocs.find(x => x.id === curReaderDoc);
    if(dc){
      dc.title = d.title;
      renderDocs(document.getElementById('docq') ? document.getElementById('docq').value : '');
    }
  }catch(e){
    alert('제목 변경 실패: ' + String(e));
  }
}
async function shareDoc(){
  if(!canWrite() || !curReaderDoc) return;
  const sb=document.getElementById('sharebox');
  sb.className='sharebox on'; sb.innerHTML='<span class=pt>공유 링크 생성 중…</span>';
  try{
    const r=await fetch('share',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({doc_id:curReaderDoc})});
    if(r.status===401||r.status===404){ expireWriteAccess();
      sb.innerHTML='<span class=pt>세션 만료 — 텔레그램 /web 으로 다시 접속하세요</span>'; return; }
    const d=await r.json();
    if(d.error||!d.path){ sb.innerHTML='<span class=pt>공유 실패: '+esc(d.error||'알 수 없음')+'</span>'; return; }
    const url=location.origin+d.path;
    let copied=false;
    try{ await navigator.clipboard.writeText(url); copied=true; }catch(_){}
    if(typeof window.gtag === 'function' && curReaderDoc){
      try{ window.gtag('event', 'share', { method: 'link', content_type: 'document', item_id: curReaderDoc }); }catch(_){}
    }
    sb.innerHTML='<input id="shareurl" readonly value="'+esc(url)+'" onclick="this.select()"/>'+
      '<button onclick="copyShare()">'+(copied?'✓ 복사됨':'복사')+'</button>';
  }catch(e){ sb.innerHTML='<span class=pt>공유 실패: '+esc(String(e))+'</span>'; }
}
function copyShare(){
  const i=document.getElementById('shareurl'); if(!i) return;
  i.select();
  (navigator.clipboard?navigator.clipboard.writeText(i.value):Promise.reject())
    .then(()=>{ const b=document.querySelector('#sharebox button'); if(b) b.textContent='✓ 복사됨'; })
    .catch(()=>{ try{ document.execCommand('copy'); const b=document.querySelector('#sharebox button'); if(b) b.textContent='✓ 복사됨'; }catch(_){} });
}

// --- Global Export & Namespace ---
const ClaireReader = {
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
  docMetaHtml,
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
  window.docMetaHtml = docMetaHtml;
  window.renderReader = renderReader;
  window.setCenterView = setCenterView;
  window.openDocGraph = openDocGraph;
  window.applyDescLines = applyDescLines;
  window.setDescLines = setDescLines;
  window.doclistToolbarHtml = doclistToolbarHtml;
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = ClaireReader;
}
