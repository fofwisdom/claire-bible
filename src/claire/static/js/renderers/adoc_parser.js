/**
 * Claire Bible - AsciiDoc & Markdown Parser / Renderer
 * Unified parser for workspace reader and standalone share view.
 */

function esc(s) {
  return (s || '').replace(/[&<>"]/g, function(c) {
    return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c] || c);
  });
}

const DOMPURIFY_OPTS = {
  ADD_ATTR: ['target', 'aria-hidden', 'data-math', 'style', 'xmlns', 'display', 'class'],
  ADD_TAGS: ['mark', 'math', 'semantics', 'mrow', 'mi', 'mo', 'mn', 'msup', 'msub', 'msubsup', 'mfrac', 'munder', 'mover', 'munderover', 'mtable', 'mtr', 'mtd', 'mtext', 'mspace', 'mpadded', 'mphantom', 'annotation', 'span']
};
function renderMarkdown(src){
  if(!src) return '';
  const raw=String(src);
  const fallback=()=>esc(raw).replace(/\\r?\\n/g,'<br>');
  const parser=window.marked, purifier=window.DOMPurify;
  if(!parser||!purifier||typeof purifier.sanitize!=='function'||
     (typeof parser.parse!=='function'&&typeof parser!=='function')) return fallback();
  try{
    const s=raw.replace(/==([^=]+?)==/g,'<mark>$1</mark>');
    const html=typeof parser.parse==='function'?parser.parse(s):parser(s);
    return purifier.sanitize(html, DOMPURIFY_OPTS);
  }catch(e){ return fallback(); }
}

function splitTableCells(line){
  var raw=(line||'').trim();
  if(!raw.startsWith('|')) return [raw];
  raw=raw.substring(1);
  if(raw.endsWith('|') && !raw.endsWith('\\|')){
    raw=raw.substring(0, raw.length - 1);
  }
  var placeholder='';
  var parts=raw.replace(/\\\|/g, placeholder).split('|');
  return parts.map(function(p){
    return p.replace(new RegExp(placeholder, 'g'), '|').trim();
  });
}
function parseColsAttr(text){
  if(!text) return null;
  var m=text.match(/cols=["']?([^"'\]]+)["']?/i);
  var colsVal=m ? m[1].trim() : text.replace(/[\[\]]/g, '').trim();
  var starM=colsVal.match(/^(\d+)\*/);
  if(starM) return parseInt(starM[1], 10);
  if(colsVal.indexOf(',') !== -1){
    var parts=colsVal.split(',').filter(function(p){ return p.trim().length > 0; });
    var total=0;
    for(var i=0; i<parts.length; i++){
      var sm=parts[i].trim().match(/^(\d+)\*/);
      if(sm) total += parseInt(sm[1], 10);
      else total += 1;
    }
    return total > 0 ? total : null;
  }
  if(/^\d+$/.test(colsVal)) return parseInt(colsVal, 10);
  return null;
}
function parseCellSpec(specStr){
  var res={colspan:1, rowspan:1, align:null, style:null};
  if(!specStr) return res;
  var spec=specStr.trim();
  var mSpan=spec.match(/(\\d+)?\\.(\\d+)\\+/);
  if(mSpan){
    if(mSpan[1]) res.colspan=parseInt(mSpan[1], 10);
    if(mSpan[2]) res.rowspan=parseInt(mSpan[2], 10);
  }else{
    var mCol=spec.match(/(?<!\\.)(\\d+)\\+/);
    if(mCol) res.colspan=parseInt(mCol[1], 10);
    var mRow=spec.match(/\\.(\\d+)\\+/);
    if(mRow) res.rowspan=parseInt(mRow[1], 10);
    var mDup=spec.match(/^(\\d+)\\*$/);
    if(mDup) res.colspan=parseInt(mDup[1], 10);
  }
  if(spec.indexOf('^') !== -1) res.align='center';
  else if(spec.indexOf('>') !== -1) res.align='right';
  else if(spec.indexOf('<') !== -1) res.align='left';
  var mStyle=spec.match(/([a-z])(?=\\|$)/i);
  if(mStyle) res.style=mStyle[1].toLowerCase();
  return res;
}
function extractCellsAndCols(tableLines, explicitCols){
  var placeholder='';
  var cellTokenRe=/(?:^|(?<=\s))((?:\d*\.?\d+\+|\d+\*)?[\^<>]?[a-z]?|[\^<>]?[a-z]?)\|/g;
  var cells=[];
  var firstLineCols=null;
  var firstBlockCols=0;
  var inFirstBlock=true;

  for(var i=0; i<tableLines.length; i++){
    var raw=tableLines[i].trim();
    if(!raw){
      if(inFirstBlock && cells.length > 0) inFirstBlock=false;
      continue;
    }
    var safe=raw.replace(/\\\|/g, placeholder);
    var matches=[];
    var m;
    cellTokenRe.lastIndex=0;
    while((m=cellTokenRe.exec(safe)) !== null){
      matches.push({index: m.index, spec: m[1] || '', length: m[0].length});
    }

    if(matches.length === 0 || matches[0].index > 0){
      if(cells.length > 0 && matches.length === 0){
        cells[cells.length - 1].text += '\n' + raw.replace(/\\\|/g, '|');
        continue;
      }else if(matches.length === 0){
        var specObj=parseCellSpec('');
        cells.push({text: raw.replace(/\\\|/g, '|'), spec: '', colspan: specObj.colspan, rowspan: specObj.rowspan, align: specObj.align, style: specObj.style});
        if(inFirstBlock) firstBlockCols += specObj.colspan;
        continue;
      }
    }

    var lineCellsCount=0;
    for(var j=0; j<matches.length; j++){
      var cur=matches[j];
      var spec=(cur.spec || '').trim();
      var specObj=parseCellSpec(spec);
      var startPos=cur.index + cur.length;
      var endPos=(j + 1 < matches.length) ? matches[j + 1].index : safe.length;
      var cellText=safe.substring(startPos, endPos).trim().replace(new RegExp(placeholder, 'g'), '|');
      cells.push({text: cellText, spec: spec, colspan: specObj.colspan, rowspan: specObj.rowspan, align: specObj.align, style: specObj.style});
      lineCellsCount += specObj.colspan;
      if(inFirstBlock) firstBlockCols += specObj.colspan;
    }
    if(firstLineCols === null && lineCellsCount > 0){
      firstLineCols=lineCellsCount;
    }
  }

  var numCols=explicitCols;
  if(!numCols || numCols <= 0){
    if(firstLineCols && firstLineCols > 1) numCols=firstLineCols;
    else if(firstBlockCols > 1) numCols=firstBlockCols;
    else numCols=1;
  }
  return {cells: cells, numCols: numCols};
}
function parseAdocTableRows(tableLines, explicitCols){
  var res=extractCellsAndCols(tableLines, explicitCols);
  var cells=res.cells;
  var numCols=res.numCols;
  if(!cells || cells.length === 0) return [];
  if(numCols <= 0) numCols=1;

  var rows=[];
  var cellIdx=0;
  var occupied=[];
  for(var c=0; c<numCols; c++) occupied.push(0);

  while(cellIdx < cells.length){
    var rowCells=[];
    var col=0;
    while(col < numCols && cellIdx < cells.length){
      if(occupied[col] > 0){
        occupied[col]--;
        col++;
        continue;
      }
      var cell=cells[cellIdx++];
      rowCells.push(cell);
      if(cell.rowspan > 1){
        for(var spanC=0; spanC<cell.colspan; spanC++){
          if(col + spanC < numCols){
            occupied[col + spanC]=cell.rowspan - 1;
          }
        }
      }
      col += cell.colspan;
    }
    while(col < numCols){
      if(occupied[col] > 0) occupied[col]--;
      col++;
    }
    if(rowCells.length > 0) rows.push(rowCells);
  }
  return rows;
}
function renderTableHtml(tableLines, blockMeta, anchorId){
  var explicitCols=parseColsAttr(blockMeta.cols || '');
  var rows=parseAdocTableRows(tableLines, explicitCols);
  if(!rows || rows.length === 0) return '';
  var idAttr=anchorId ? ' id="' + esc(anchorId) + '"' : '';
  var tHtml='<table' + idAttr + '>';
  if(blockMeta.title) tHtml += '<caption>' + esc(blockMeta.title) + '</caption>';

  function renderCell(cell, tag){
    var attrs=[];
    if(cell.rowspan > 1) attrs.push('rowspan="' + cell.rowspan + '"');
    if(cell.colspan > 1) attrs.push('colspan="' + cell.colspan + '"');
    if(cell.align) attrs.push('style="text-align:' + cell.align + '"');
    var attrStr=attrs.length > 0 ? ' ' + attrs.join(' ') : '';
    var text=(cell.text || '').trim();
    var innerHtml='';
    if(cell.style==='a' || (text.indexOf('\n')!==-1 && /(?:^|\n)\s*[\*\-\.]\s+/.test(text))){
      innerHtml=convertAsciidocToHtml(text);
      if(innerHtml.startsWith('<p>') && innerHtml.endsWith('</p>') && (innerHtml.match(/<p>/g)||[]).length===1 && innerHtml.indexOf('\n')===-1){
        innerHtml=innerHtml.substring(3, innerHtml.length-4);
      }
    }else if(text.indexOf('\n\n')!==-1){
      innerHtml=convertAsciidocToHtml(text);
    }else{
      var rawLines=text.split('\n');
      var parts=rawLines.map(function(l){ return inlineAdocFormat(l); });
      innerHtml=parts.join(' ');
    }
    return '<' + tag + attrStr + '>' + innerHtml + '</' + tag + '>';
  }

  tHtml += '<thead><tr>' + rows[0].map(function(c){ return renderCell(c, 'th'); }).join('') + '</tr></thead>';
  if(rows.length > 1){
    tHtml += '<tbody>';
    for(var r=1; r<rows.length; r++){
      tHtml += '<tr>' + rows[r].map(function(c){ return renderCell(c, 'td'); }).join('') + '</tr>';
    }
    tHtml += '</tbody>';
  }
  tHtml += '</table>';
  return tHtml;
}

function inlineAdocFormat(text){
  if(!text) return '';
  var s=esc(text);
  var codeSpans=[];
  s=s.replace(/`(?![\s])([^`\n]+?)(?<![\s])`/g, function(_, m1){
    codeSpans.push('<code>'+m1+'</code>');
    return '\x00ADOCCODE'+(codeSpans.length-1)+'\x00';
  });
  s=s.replace(/\+\+(?![\s])([^\+\n]+?)(?<![\s])\+\+/g, function(_, m1){
    codeSpans.push('<code>'+m1+'</code>');
    return '\x00ADOCCODE'+(codeSpans.length-1)+'\x00';
  });
  var varSpans=[];
  s=s.replace(/(?<![\w\\\$])\$[A-Z_][A-Za-z0-9_]*\b/g, function(m){
    varSpans.push(m);
    return '\x00ADOCVAR'+(varSpans.length-1)+'\x00';
  });
  s=s.replace(/(?<![\w\\\$])\$\{[A-Za-z0-9_]+\}/g, function(m){
    varSpans.push(m);
    return '\x00ADOCVAR'+(varSpans.length-1)+'\x00';
  });
  s=s.replace(/(?<![\w\\\$])\$\d+(?:,\d{3})*(?:\.\d+)?\b/g, function(m){
    varSpans.push(m);
    return '\x00ADOCVAR'+(varSpans.length-1)+'\x00';
  });
  var mathSpans=[];
  s=s.replace(/(stem|latexmath|asciimath):\\[(.*?)\\]/gi, function(_, kind, content){
    mathSpans.push('<span class="math inline" data-math="'+kind.toLowerCase()+'"><code>'+content+'</code></span>');
    return '\x00ADOCMATH'+(mathSpans.length-1)+'\x00';
  });
  s=s.replace(/\\\((.*?)\\\)/g, function(_, content){
    mathSpans.push('<span class="math inline" data-math="latex"><code>'+content+'</code></span>');
    return '\x00ADOCMATH'+(mathSpans.length-1)+'\x00';
  });
  s=s.replace(/\$\$([^\$]+?)\$\$/g, function(_, content){
    mathSpans.push('<span class="math inline" data-math="latex"><code>'+content+'</code></span>');
    return '\x00ADOCMATH'+(mathSpans.length-1)+'\x00';
  });
  s=s.replace(/(?<![\w\\\$])\$([^\$\n]+?)\$(?![\w\$])/g, function(_, content){
    mathSpans.push('<span class="math inline" data-math="latex"><code>'+content+'</code></span>');
    return '\x00ADOCMATH'+(mathSpans.length-1)+'\x00';
  });
  var linkSpans=[];
  s=s.replace(/(https?:\/\/[^\s\[\]]+)\[(.*?)\]/g, function(_, u, l){
    linkSpans.push('<a href="'+u+'" target="_blank" rel="noopener">'+l+'</a>');
    return '\x00ADOCLINK'+(linkSpans.length-1)+'\x00';
  });
  s=s.replace(/(?<!href=")(https?:\/\/[^\s<>"\'\)]+)/g, function(_, u){
    linkSpans.push('<a href="'+u+'" target="_blank" rel="noopener">'+u+'</a>');
    return '\x00ADOCLINK'+(linkSpans.length-1)+'\x00';
  });
  s=s.replace(/&lt;&lt;([^\s\[\],>&]+)(?:,\s*([^&]+?))?&gt;&gt;/g, function(_, a, l){
    var anc=a.trim().replace(/^#/, '');
    var label=(l||a).trim();
    linkSpans.push('<a href="#'+anc+'" class="xref">'+label+'</a>');
    return '\x00ADOCLINK'+(linkSpans.length-1)+'\x00';
  });
  s=s.replace(/xref:([^\s\[\],]+)\[(.*?)\]/gi, function(_, a, l){
    var anc=a.trim().replace(/^#/, '');
    var label=(l||a).trim();
    linkSpans.push('<a href="#'+anc+'" class="xref">'+label+'</a>');
    return '\x00ADOCLINK'+(linkSpans.length-1)+'\x00';
  });
  s=s.replace(/\[\[([^\s\[\],]+)\]\]/g, '<a id="$1" class="anchor"></a>');
  s=s.replace(/\s+\+\s*$/g, '<br>');
  s=s.replace(/(?<!#)#(?![\s#])([^#\n]+?)(?<![\s#])#(?!#)/g, '<mark>$1</mark>');
  s=s.replace(/\*\*(?![\s\*])([^*\n]+?)(?<![\s\*])\*\*/g, '<strong>$1</strong>');
  s=s.replace(/(?<!\*)\*(?![\s\*])([^*\n]+?)(?<![\s\*])\*(?!\*)/g, '<strong>$1</strong>');
  s=s.replace(/__(?![\s_])([^_\n]+?)(?<![\s_])__/g, '<em>$1</em>');
  s=s.replace(/(?<!_)_(?![\s_])([^_\n]+?)(?<![\s_])_(?!_)/g, '<em>$1</em>');
  s=s.replace(/\^(?![\s\^])([^\^\n]+?)(?<![\s\^])\^/g, '<sup>$1</sup>');
  s=s.replace(/~(?![\s~])([^~\n]+?)(?<![\s~])~/g, '<sub>$1</sub>');
  for(var i=0; i<linkSpans.length; i++) s=s.replace('\x00ADOCLINK'+i+'\x00', linkSpans[i]);
  for(var j=0; j<codeSpans.length; j++) s=s.replace('\x00ADOCCODE'+j+'\x00', codeSpans[j]);
  for(var k=0; k<mathSpans.length; k++) s=s.replace('\x00ADOCMATH'+k+'\x00', mathSpans[k]);
  for(var v=0; v<varSpans.length; v++) s=s.replace('\x00ADOCVAR'+v+'\x00', varSpans[v]);
  return s;
}

// 자체 완결형 경량 AsciiDoc 렌더러 (외부 CDN/루비런타임 의존성 제로, 번개같은 로딩 속도)
function convertAsciidocToHtml(raw){
  if(!raw) return '';
  const NL=String.fromCharCode(10);
  const lines=String(raw).split(NL);
  const out=[];
  let inBlock=null;
  let blockMeta={};
  let blockLines=[];

  var listStack=[];
  var inItem=false;
  var inContinuation=false;
  var pendingContinuation=false;
  var continuationLines=[];
  var pendingMeta=null;
  var pendingBlockLines=[];
  var normalPLines=[];
  var pendingAnchor=null;

  function closeItem(){
    if(inItem){ inItem=false; return ['</li>']; }
    return [];
  }
  function flushList(){
    if(listStack.length===0) return;
    out.push.apply(out, closeItem());
    while(listStack.length>0){
      var entry=listStack.pop();
      out.push('</'+entry.tag+'>');
      if(listStack.length>0) out.push('</li>');
    }
  }
  function adjustListLevel(tag, level){
    if(listStack.length===0){
      out.push('<'+tag+'>');
      listStack.push({tag:tag, level:level});
      return;
    }
    var top=listStack[listStack.length-1];
    if(level > top.level){
      out.push('<'+tag+'>');
      listStack.push({tag:tag, level:level});
    }else if(level < top.level){
      out.push.apply(out, closeItem());
      while(listStack.length>0 && listStack[listStack.length-1].level > level){
        var e=listStack.pop();
        out.push('</'+e.tag+'>');
        if(listStack.length>0) out.push('</li>');
      }
      if(listStack.length>0 && listStack[listStack.length-1].tag !== tag){
        var old=listStack.pop();
        out.push('</'+old.tag+'>');
        out.push('<'+tag+'>');
        listStack.push({tag:tag, level:level});
      }
    }else{
      if(top.tag !== tag){
        out.push.apply(out, closeItem());
        listStack.pop();
        out.push('</'+top.tag+'>');
        out.push('<'+tag+'>');
        listStack.push({tag:tag, level:level});
      }else{
        out.push.apply(out, closeItem());
      }
    }
  }

  function formatParagraphLines(pLines){
    if(!pLines || pLines.length===0) return '';
    var formattedParts=pLines.map(function(l){ return inlineAdocFormat(l); });
    var res='';
    for(var i=0; i<formattedParts.length; i++){
      var p=formattedParts[i];
      if(i===0){ res=p; }
      else{
        if(res.endsWith('<br>')) res += p;
        else res += ' ' + p;
      }
    }
    return res;
  }

  function flushPendingSingleBlock(){
    if(!pendingMeta || (pendingMeta.kind!=='quote' && pendingMeta.kind!=='admonition')) return;
    if(pendingBlockLines.length===0){ pendingMeta=null; return; }
    var anchorAttr=pendingAnchor ? ' id="'+esc(pendingAnchor)+'"' : '';
    pendingAnchor=null;
    var pContent=formatParagraphLines(pendingBlockLines);
    if(pendingMeta.kind==='quote'){
      var attrText=pendingMeta.author?esc(pendingMeta.author)+(pendingMeta.source?' — '+esc(pendingMeta.source):''):'';
      out.push('<div class="quoteblock"' + anchorAttr + '><blockquote><p>'+pContent+'</p></blockquote>'+(attrText?'<div class="attribution">'+attrText+'</div>':'')+'</div>');
    }else if(pendingMeta.kind==='admonition'){
      var admType=(pendingMeta.type||'NOTE').toLowerCase();
      var admTitle=esc(pendingMeta.type||'NOTE');
      out.push('<div class="admonitionblock '+admType+'"' + anchorAttr + '><div class="title">'+admTitle+'</div><div class="content"><p>'+pContent+'</p></div></div>');
    }
    pendingMeta=null;
    pendingBlockLines=[];
  }

  function flushContinuation(){
    if(inContinuation && continuationLines.length>0){
      var anchorAttr=pendingAnchor ? ' id="'+esc(pendingAnchor)+'"' : '';
      pendingAnchor=null;
      out.push('<p' + anchorAttr + '>'+formatParagraphLines(continuationLines)+'</p>');
    }
    inContinuation=false;
    pendingContinuation=false;
    continuationLines=[];
  }

  function flushNormalP(){
    if(normalPLines.length>0){
      flushList();
      var anchorAttr=pendingAnchor ? ' id="'+esc(pendingAnchor)+'"' : '';
      pendingAnchor=null;
      out.push('<p' + anchorAttr + '>'+formatParagraphLines(normalPLines)+'</p>');
      normalPLines=[];
    }
  }

  function flushBlock(){
    if(!inBlock) return;
    var anchorAttr=pendingAnchor ? ' id="'+esc(pendingAnchor)+'"' : '';
    pendingAnchor=null;

    if(inBlock==='quote'){
      var qParagraphs=[];
      var currP=[];
      for(var b=0; b<blockLines.length; b++){
        var bl=blockLines[b];
        if(!bl.trim()){
          if(currP.length>0){ qParagraphs.push(formatParagraphLines(currP)); currP=[]; }
        }else{ currP.push(bl); }
      }
      if(currP.length>0) qParagraphs.push(formatParagraphLines(currP));
      var qContent=qParagraphs.map(function(p){ return '<p>'+p+'</p>'; }).join('');
      var attr='';
      if(blockMeta.author||blockMeta.source){
        attr='<div class="attribution">'+esc(blockMeta.author||'')+
             (blockMeta.source?' — '+esc(blockMeta.source):'')+'</div>';
      }
      out.push('<div class="quoteblock"' + anchorAttr + '><blockquote>'+qContent+'</blockquote>'+attr+'</div>');
    }else if(inBlock==='admonition'){
      var admParagraphs=[];
      var currP=[];
      for(var b=0; b<blockLines.length; b++){
        var bl=blockLines[b];
        if(!bl.trim()){
          if(currP.length>0){ admParagraphs.push(formatParagraphLines(currP)); currP=[]; }
        }else{ currP.push(bl); }
      }
      if(currP.length>0) admParagraphs.push(formatParagraphLines(currP));
      var admContent=admParagraphs.map(function(p){ return '<p>'+p+'</p>'; }).join('');
      var type=(blockMeta.type||'NOTE').toLowerCase();
      out.push('<div class="admonitionblock '+esc(type)+'"' + anchorAttr + '><div class="title">'+
               esc(blockMeta.type||'NOTE')+'</div><div class="content">'+admContent+'</div></div>');
    }else if(inBlock==='code'){
      var codeText=esc(blockLines.join(NL)).replace(/&lt;(\d+)&gt;/g,'<span class="conum">&lt;$1&gt;</span>');
      out.push('<div class="listingblock"' + anchorAttr + '><div class="content"><pre><code class="language-'+esc(blockMeta.lang||'')+'">'+codeText+'</code></pre></div></div>');
    }else if(inBlock==='math'){
      var mathText=esc(blockLines.join(NL));
      var mType=esc(blockMeta.type||'latex');
      out.push('<div class="mathblock display"' + anchorAttr + ' data-math="'+mType+'"><div class="content"><pre class="math"><code>'+mathText+'</code></pre></div></div>');
    }else if(inBlock==='table'){
      var tblHtml=renderTableHtml(blockLines, blockMeta, anchorAttr.replace(' id="', '').replace('"', ''));
      if(tblHtml) out.push(tblHtml);
    }
    inBlock=null; blockMeta={}; blockLines=[];
  }

  function matchList(trimmed){
    var mStar=trimmed.match(/^(\*{1,5})\s+(.+)$/);
    if(mStar) return {tag:'ul', level:mStar[1].length, text:mStar[2]};
    var mHyphen=trimmed.match(/^-\s+(.+)$/);
    if(mHyphen) return {tag:'ul', level:1, text:mHyphen[1]};
    var mDot=trimmed.match(/^(\.{1,5})\s+(.+)$/);
    if(mDot) return {tag:'ol', level:mDot[1].length, text:mDot[2]};
    var mNum=trimmed.match(/^\d+[\.\)]\s+(.+)$/);
    if(mNum) return {tag:'ol', level:1, text:mNum[1]};
    return null;
  }

  function extractHeadingAnchor(hText){
    var m=hText.match(/\[#([^\s\[\],]+)\]|\[\[([^\s\[\],]+)\]\]/);
    if(m){
      var anc=m[1]||m[2];
      var clean=(hText.substring(0, m.index)+hText.substring(m.index+m[0].length)).trim();
      return {text:clean, anchor:anc};
    }
    var anc2=pendingAnchor;
    pendingAnchor=null;
    return {text:hText, anchor:anc2};
  }

  for(var i=0; i<lines.length; i++){
    var line=lines[i];
    var trimmed=line.trim();

    if(!inBlock){
      var anchorM=trimmed.match(/^\[#([^\s\[\],]+)\]$/) || trimmed.match(/^\[\[([^\s\[\],]+)\]\]$/);
      if(anchorM){
        flushNormalP(); flushContinuation(); flushPendingSingleBlock();
        pendingAnchor=anchorM[1].trim();
        continue;
      }

      var qm=trimmed.match(/^\[quote(?:,\s*([^,\]]+))?(?:,\s*([^\]]+))?\]/i);
      if(qm){
        flushNormalP(); flushContinuation(); flushPendingSingleBlock(); flushList();
        pendingMeta={kind:'quote', author:qm[1]?qm[1].trim():'', source:qm[2]?qm[2].trim():''};
        pendingBlockLines=[];
        continue;
      }
      var am=trimmed.match(/^\[(NOTE|IMPORTANT|TIP|WARNING|CAUTION)\]/i);
      if(am){
        flushNormalP(); flushContinuation(); flushPendingSingleBlock(); flushList();
        pendingMeta={kind:'admonition', type:am[1].toUpperCase()};
        pendingBlockLines=[];
        continue;
      }
      var sm=trimmed.match(/^\[source(?:,\s*([a-zA-Z0-9_-]+))?\]/i);
      if(sm){
        flushNormalP(); flushContinuation(); flushPendingSingleBlock(); flushList();
        pendingMeta={kind:'code', lang:sm[1]?sm[1].trim():''};
        continue;
      }
      var mathM=trimmed.match(/^\[(latexmath|stem|asciimath)\]$/i);
      if(mathM){
        flushNormalP(); flushContinuation(); flushPendingSingleBlock(); flushList();
        pendingMeta={kind:'math', type:mathM[1].toLowerCase()};
        continue;
      }
      var tm=trimmed.match(/^\[(.*cols.*|.*header.*|\d+\*|[0-9,]+)\]$/i);
      if(tm){
        flushNormalP(); flushContinuation(); flushPendingSingleBlock(); flushList();
        if(pendingMeta && pendingMeta.kind==='table'){
          pendingMeta.cols=tm[1];
        }else{
          pendingMeta={kind:'table', cols:tm[1]};
        }
        continue;
      }
      var titleM=trimmed.match(/^\.([^\.\s].*)$/);
      if(titleM){
        flushNormalP(); flushContinuation(); flushPendingSingleBlock(); flushList();
        if(pendingMeta && pendingMeta.kind==='table'){
          pendingMeta.title=titleM[1].trim();
        }else{
          pendingMeta={kind:'table', title:titleM[1].trim()};
        }
        continue;
      }

      if(trimmed==='____'){
        flushNormalP(); flushContinuation(); flushList();
        inBlock='quote';
        blockMeta=(pendingMeta&&pendingMeta.kind==='quote')?pendingMeta:{};
        pendingMeta=null; pendingBlockLines=[]; blockLines=[];
        continue;
      }
      if(trimmed==='===='){
        flushNormalP(); flushContinuation(); flushList();
        inBlock='admonition';
        blockMeta=(pendingMeta&&pendingMeta.kind==='admonition')?pendingMeta:{type:'NOTE'};
        pendingMeta=null; pendingBlockLines=[]; blockLines=[];
        continue;
      }
      if(trimmed==='----'){
        flushNormalP(); flushContinuation(); flushList();
        if(pendingMeta && pendingMeta.kind==='math'){
          inBlock='math';
          blockMeta=pendingMeta;
        }else{
          inBlock='code';
          blockMeta=(pendingMeta&&pendingMeta.kind==='code')?pendingMeta:{};
        }
        pendingMeta=null; pendingBlockLines=[]; blockLines=[];
        continue;
      }
      if(trimmed==='++++'){
        flushNormalP(); flushContinuation(); flushList();
        inBlock='math';
        blockMeta=(pendingMeta&&pendingMeta.kind==='math')?pendingMeta:{kind:'math', type:'latex'};
        pendingMeta=null; pendingBlockLines=[]; blockLines=[];
        continue;
      }
      if(trimmed==='|==='){
        flushNormalP(); flushContinuation(); flushList();
        inBlock='table';
        blockMeta=(pendingMeta&&pendingMeta.kind==='table')?pendingMeta:{};
        pendingMeta=null; pendingBlockLines=[]; blockLines=[];
        continue;
      }

      var imgMatch=trimmed.match(/^image::([^\[]+)\[([^,\]]*)(?:,\s*title=(?:"([^"]*)"|'([^']*)'|([^\]]*)))?\]/);
      if(imgMatch){
        flushNormalP(); flushContinuation(); flushPendingSingleBlock(); flushList();
        var src=imgMatch[1].trim();
        var alt=imgMatch[2]?imgMatch[2].trim():'';
        var cap=imgMatch[3]||imgMatch[4]||imgMatch[5]||'';
        var anchorAttr=pendingAnchor ? ' id="'+esc(pendingAnchor)+'"' : '';
        pendingAnchor=null;
        out.push('<div class="imageblock"' + anchorAttr + '><img src="'+esc(src)+'" alt="'+esc(alt)+'">'+
                 (cap?'<div class="title">'+esc(cap)+'</div>':'')+'</div>');
        continue;
      }
      var colMatch=trimmed.match(/^<(\d+)>\s*(.+)/);
      if(colMatch){
        flushNormalP(); flushContinuation(); flushPendingSingleBlock(); flushList();
        out.push('<div class="colist"><span class="conum">&lt;'+colMatch[1]+'&gt;</span> '+inlineAdocFormat(colMatch[2])+'</div>');
        continue;
      }
      if(/^'{3,}$/.test(trimmed)){
        flushNormalP(); flushContinuation(); flushPendingSingleBlock(); flushList();
        var anchorAttr=pendingAnchor ? ' id="'+esc(pendingAnchor)+'"' : '';
        pendingAnchor=null;
        out.push('<hr' + anchorAttr + '>');
        continue;
      }
      var h1Match=trimmed.match(/^=\s+(.+)$/);
      if(h1Match){ flushNormalP(); flushContinuation(); flushPendingSingleBlock(); flushList(); var hInfo=extractHeadingAnchor(h1Match[1]); var idAttr=hInfo.anchor?' id="'+esc(hInfo.anchor)+'"':''; out.push('<h1'+idAttr+'>'+inlineAdocFormat(hInfo.text)+'</h1>'); continue; }
      var h2Match=trimmed.match(/^==\s+(.+)$/);
      if(h2Match){ flushNormalP(); flushContinuation(); flushPendingSingleBlock(); flushList(); var hInfo=extractHeadingAnchor(h2Match[1]); var idAttr=hInfo.anchor?' id="'+esc(hInfo.anchor)+'"':''; out.push('<h2'+idAttr+'>'+inlineAdocFormat(hInfo.text)+'</h2>'); continue; }
      var h3Match=trimmed.match(/^===\s+(.+)$/);
      if(h3Match){ flushNormalP(); flushContinuation(); flushPendingSingleBlock(); flushList(); var hInfo=extractHeadingAnchor(h3Match[1]); var idAttr=hInfo.anchor?' id="'+esc(hInfo.anchor)+'"':''; out.push('<h3'+idAttr+'>'+inlineAdocFormat(hInfo.text)+'</h3>'); continue; }
      var h4Match=trimmed.match(/^====\s+(.+)$/);
      if(h4Match){ flushNormalP(); flushContinuation(); flushPendingSingleBlock(); flushList(); var hInfo=extractHeadingAnchor(h4Match[1]); var idAttr=hInfo.anchor?' id="'+esc(hInfo.anchor)+'"':''; out.push('<h4'+idAttr+'>'+inlineAdocFormat(hInfo.text)+'</h4>'); continue; }

      var attrMatch=trimmed.match(/^:[a-zA-Z0-9_-]+:\s*(.*)$/);
      if(attrMatch){ continue; }

      var singleAdm=trimmed.match(/^(NOTE|TIP|IMPORTANT|WARNING|CAUTION):\s*(.+)$/i);
      if(singleAdm){
        flushNormalP(); flushContinuation(); flushPendingSingleBlock(); flushList();
        var admType=singleAdm[1].toUpperCase();
        var anchorAttr=pendingAnchor ? ' id="'+esc(pendingAnchor)+'"' : '';
        pendingAnchor=null;
        out.push('<div class="admonitionblock '+admType.toLowerCase()+'"' + anchorAttr + '><div class="title">'+esc(admType)+'</div><div class="content"><p>'+inlineAdocFormat(singleAdm[2])+'</p></div></div>');
        continue;
      }

      if(trimmed==='+'){
        if(inItem){
          flushContinuation();
          pendingContinuation=true;
        }
        continue;
      }

      var listM=matchList(trimmed);
      if(listM){
        flushNormalP(); flushContinuation(); pendingContinuation=false; flushPendingSingleBlock();
        var itemText=inlineAdocFormat(listM.text);
        adjustListLevel(listM.tag, listM.level);
        out.push('<li>'+itemText);
        inItem=true;
        inContinuation=false;
        continue;
      }

      if(!trimmed){
        if(inContinuation){
          flushContinuation();
        }
        flushNormalP(); flushPendingSingleBlock();
        continue;
      }

      if(pendingMeta && (pendingMeta.kind==='quote' || pendingMeta.kind==='admonition')){
        pendingBlockLines.push(trimmed);
        continue;
      }

      if((pendingContinuation || inContinuation) && inItem){
        pendingContinuation=false;
        inContinuation=true;
        continuationLines.push(trimmed);
        continue;
      }

      normalPLines.push(trimmed);
    }else{
      if(inBlock==='quote'&&trimmed==='____') flushBlock();
      else if(inBlock==='admonition'&&trimmed==='====') flushBlock();
      else if(inBlock==='code'&&trimmed==='----') flushBlock();
      else if(inBlock==='math'&&(trimmed==='++++'||trimmed==='----')) flushBlock();
      else if(inBlock==='table'&&trimmed==='|===') flushBlock();
      else{
        blockLines.push(line);
      }
    }
  }
  flushBlock();
  flushNormalP();
  flushContinuation();
  flushPendingSingleBlock();
  flushList();
  return out.join(NL);
}

function renderAsciidoc(src){
  if(!src) return '';
  const raw=String(src);
  const purifier=window.DOMPurify;
  try{
    const html = convertAsciidocToHtml(raw);
    if(purifier && typeof purifier.sanitize==='function'){
      return purifier.sanitize(html, DOMPURIFY_OPTS);
    }
    return html;
  }catch(_){
    return renderMarkdown(raw);
  }
}

function applyMathRendering(container){
  if(!container) return;
  if(typeof renderMathInElement === 'function'){
    try{
      renderMathInElement(container, {
        delimiters: [
          {left: '$$', right: '$$', display: true},
          {left: '$', right: '$', display: false},
          {left: '\\(', right: '\\)', display: false},
          {left: '\\[', right: '\\]', display: true}
        ],
        ignoredTags: ['script', 'noscript', 'style', 'textarea', 'pre', 'code', 'option'],
        throwOnError: false
      });
    }catch(_){}
  }
  if(typeof katex !== 'undefined'){
    try{
      container.querySelectorAll('.mathblock').forEach(function(mb){
        var codeEl = mb.querySelector('pre.math code, pre.math');
        if(codeEl && !mb.getAttribute('data-katex-rendered')){
          var text = (codeEl.textContent || '').trim();
          if(text){
            try{
              mb.innerHTML = '';
              katex.render(text, mb, { displayMode: true, throwOnError: false });
              mb.setAttribute('data-katex-rendered', 'true');
            }catch(_){}
          }
        }
      });
      container.querySelectorAll('.math.inline').forEach(function(mi){
        var codeEl = mi.querySelector('code') || mi;
        if(codeEl && !mi.getAttribute('data-katex-rendered')){
          var text = (codeEl.textContent || '').trim();
          if(text){
            try{
              var span = document.createElement('span');
              katex.render(text, span, { displayMode: false, throwOnError: false });
              mi.parentNode.replaceChild(span, mi);
            }catch(_){}
          }
        }
      });
    }catch(_){}
  }
}

function isAsciidoc(src, format){
  if(format){
    const fmt=String(format).toLowerCase().trim();
    if(fmt==='adoc'||fmt==='asciidoc') return true;
    if(fmt==='md'||fmt==='markdown') return false;
  }
  if(!src) return false;
  const s=String(src);
  return /(?:^|\n)\[(NOTE|TIP|IMPORTANT|WARNING|CAUTION|quote|source)[^\]]*\]|(?:^|\n)\|===|(?:^|\n)image::/m.test(s);
}

function renderContent(src, format){
  if(!src) return '';
  if(isAsciidoc(src, format)){
    return renderAsciidoc(src);
  }
  return renderMarkdown(src);
}

// --- Global Export & Namespace ---
const ClaireAdocParser = {
  esc,
  renderMarkdown,
  splitTableCells,
  parseColsAttr,
  parseCellSpec,
  extractCellsAndCols,
  parseAdocTableRows,
  renderTableHtml,
  inlineAdocFormat,
  convertAsciidocToHtml,
  renderAsciidoc,
  applyMathRendering,
  isAsciidoc,
  renderContent,
  DOMPURIFY_OPTS
};

if (typeof window !== 'undefined') {
  window.ClaireAdocParser = ClaireAdocParser;
  window.esc = esc;
  window.renderMarkdown = renderMarkdown;
  window.splitTableCells = splitTableCells;
  window.parseColsAttr = parseColsAttr;
  window.parseCellSpec = parseCellSpec;
  window.extractCellsAndCols = extractCellsAndCols;
  window.parseAdocTableRows = parseAdocTableRows;
  window.renderTableHtml = renderTableHtml;
  window.inlineAdocFormat = inlineAdocFormat;
  window.convertAsciidocToHtml = convertAsciidocToHtml;
  window.renderAsciidoc = renderAsciidoc;
  window.applyMathRendering = applyMathRendering;
  window.isAsciidoc = isAsciidoc;
  window.renderContent = renderContent;
  window.DOMPURIFY_OPTS = DOMPURIFY_OPTS;
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = ClaireAdocParser;
}
