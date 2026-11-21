/**
 * Claire Bible - display helpers for document content in the workspace reader and the share page.
 *
 * This file does not convert AsciiDoc. The server converts a document body to HTML (src/bible/adoc/aot.py) and sends it as detail_html. The browser cleans that HTML with DOMPurify, shows a summary as plain text, and draws math with KaTeX.
 */

function esc(s, quote) {
  if (quote === false) {
    return (s || '').replace(/[&<>]/g, function(c) {
      return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c] || c);
    });
  }
  return (s || '').replace(/[&<>"']/g, function(c) {
    return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c] || c);
  });
}

const DOMPURIFY_OPTS = {
  ADD_ATTR: ['target', 'aria-hidden', 'data-math', 'style', 'xmlns', 'display', 'class'],
  ADD_TAGS: ['mark', 'math', 'semantics', 'mrow', 'mi', 'mo', 'mn', 'msup', 'msub', 'msubsup', 'mfrac', 'munder', 'mover', 'munderover', 'mtable', 'mtr', 'mtd', 'mtext', 'mspace', 'mpadded', 'mphantom', 'annotation', 'span']
};
// Plain text to HTML: each group of lines between empty lines is one paragraph, and a line break in a paragraph stays a line break. A summary is plain text, so no mark in it has a meaning.
function renderPlainText(src){
  if(!src) return '';
  return String(src).split(/\r?\n[ \t]*(?:\r?\n[ \t]*)+/).map(function(block){ return block.trim(); }).filter(Boolean).map(function(block){ return '<p>'+esc(block).replace(/\r?\n/g,'<br>')+'</p>'; }).join('');
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

// --- Global Export & Namespace ---
const ClaireDocContent = {
  esc,
  renderPlainText,
  applyMathRendering,
  DOMPURIFY_OPTS
};

if (typeof window !== 'undefined') {
  window.ClaireDocContent = ClaireDocContent;
  window.esc = esc;
  window.renderPlainText = renderPlainText;
  window.applyMathRendering = applyMathRendering;
  window.DOMPURIFY_OPTS = DOMPURIFY_OPTS;
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = ClaireDocContent;
}
