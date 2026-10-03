"""Heads-Up Display (HUD) toolbar injector for Claire Bible presentations.

Provides an unobtrusive floating glassmorphic toolbar with navigation return,
2D slide coordinate tracking, speaker notes, overview, PDF print, and fullscreen toggle.
Adopts BookStack theme aesthetics (Light/Dark) and decorates vertical subslides in headings.
"""

from __future__ import annotations

HUD_SNIPPET = """
<!-- Claire Bible Presentation HUD Toolbar -->
<style>
.cb-hud {
  position: fixed;
  top: 0;
  left: 0;
  width: 100%;
  height: 48px;
  background: rgba(255, 255, 255, 0.9);
  backdrop-filter: blur(16px);
  -webkit-backdrop-filter: blur(16px);
  border-bottom: 1px solid rgba(0, 0, 0, 0.12);
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 16px;
  box-sizing: border-box;
  z-index: 99999;
  opacity: 0;
  transform: translateY(-8px);
  transition: opacity 0.25s ease, transform 0.25s ease;
  pointer-events: none;
  font-family: var(--cb-font-sans, 'Noto Sans KR', sans-serif);
  font-size: 13px;
  color: #1f2328;
}
[data-theme="dark"] .cb-hud, body.theme-dark .cb-hud {
  background: rgba(14, 17, 22, 0.88);
  border-bottom: 1px solid rgba(255, 255, 255, 0.12);
  color: #c9d1d9;
}
.cb-hud.visible, .cb-hud:hover {
  opacity: 1;
  transform: translateY(0);
  pointer-events: auto;
}
.cb-hud-left, .cb-hud-right {
  display: flex;
  align-items: center;
  gap: 8px;
}
.cb-hud-center {
  font-weight: 600;
  font-family: var(--cb-font-mono, 'D2Coding', 'JetBrains Mono', monospace);
  color: var(--cb-accent-blue, #0969da);
  letter-spacing: 0.05em;
}
[data-theme="dark"] .cb-hud-center, body.theme-dark .cb-hud-center {
  color: #58a6ff;
}
.cb-hud-btn {
  background: rgba(0, 0, 0, 0.05);
  border: 1px solid rgba(0, 0, 0, 0.12);
  color: #1f2328;
  padding: 5px 10px;
  border-radius: 6px;
  cursor: pointer;
  font-size: 12px;
  font-weight: 500;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  transition: all 0.15s ease;
}
[data-theme="dark"] .cb-hud-btn, body.theme-dark .cb-hud-btn {
  background: rgba(255, 255, 255, 0.08);
  border: 1px solid rgba(255, 255, 255, 0.15);
  color: #f0f6fc;
}
.cb-hud-btn:hover {
  background: rgba(9, 105, 218, 0.12);
  border-color: #0969da;
  color: #0969da;
}
[data-theme="dark"] .cb-hud-btn:hover, body.theme-dark .cb-hud-btn:hover {
  background: rgba(88, 166, 255, 0.2);
  border-color: #58a6ff;
  color: #ffffff;
}
.cb-hud-title {
  color: var(--cb-muted, #656d76);
  font-size: 12px;
  max-width: 380px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  margin-left: 6px;
}
.cb-hud-sharebox {
  position: fixed;
  top: 54px;
  right: 16px;
  display: none;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  background: #ffffff;
  border: 1px solid #0969da;
  border-radius: 6px;
  box-shadow: 0 6px 20px rgba(0, 0, 0, 0.15);
  z-index: 100000;
  font-size: 12px;
}
[data-theme="dark"] .cb-hud-sharebox, body.theme-dark .cb-hud-sharebox {
  background: rgba(22, 27, 34, 0.96);
  border-color: #58a6ff;
  box-shadow: 0 6px 20px rgba(0, 0, 0, 0.5);
}
.cb-hud-sharebox input {
  width: 280px;
  background: #f6f8fa;
  border: 1px solid #d0d7de;
  border-radius: 4px;
  color: #1f2328;
  padding: 5px 8px;
  font-size: 12px;
  font-family: var(--cb-font-mono, 'D2Coding', monospace);
  outline: none;
}
[data-theme="dark"] .cb-hud-sharebox input, body.theme-dark .cb-hud-sharebox input {
  background: rgba(13, 17, 23, 0.9);
  border-color: rgba(255, 255, 255, 0.2);
  color: #f0f6fc;
}
.cb-hud-sharebox input:focus {
  border-color: var(--cb-accent-blue, #0969da);
}
.cb-hud-sharebox button {
  background: #0969da;
  color: #fff;
  border: 0;
  border-radius: 4px;
  padding: 5px 10px;
  font-size: 12px;
  cursor: pointer;
  font-weight: 600;
  white-space: nowrap;
}
@media (max-width: 768px) {
  .cb-hud-title { display: none; }
  .cb-hud-btn span { display: none; }
}
@media print {
  #cb-hud, .cb-hud-sharebox, .cb-down-hint { display: none !important; }
  @page {
    size: landscape;
    margin: 0;
  }
  html, body, .reveal {
    -webkit-print-color-adjust: exact !important;
    print-color-adjust: exact !important;
    color-adjust: exact !important;
    background: #ffffff !important;
    color: #1f2328 !important;
  }
  [data-theme="dark"] html, [data-theme="dark"] body, [data-theme="dark"] .reveal {
    background: #0e1116 !important;
    color: #d7dbe0 !important;
  }
}
/* When embedded in an iframe (e.g. Claire Bible workspace), hide in-frame floating HUD */
html.is-embedded #cb-hud, body.is-embedded #cb-hud {
  display: none !important;
}
</style>

<div id="cb-hud" class="cb-hud">
  <div class="cb-hud-left">
    <span class="cb-hud-logo" style="font-weight:700;color:var(--cb-accent-blue,#0969da);margin-right:6px">📊 Claire Bible</span>
    <span class="cb-hud-title" id="cb-hud-title"></span>
  </div>
  <div class="cb-hud-center">
    <span id="cb-hud-coords">01 / 01</span>
  </div>
  <div class="cb-hud-right">
    <button class="cb-hud-btn" onclick="cbToggleSpeaker()" title="발표자 모드 (S)">
      🎙️ <span>발표자</span>
    </button>
    <button class="cb-hud-btn" onclick="if(window.Reveal) Reveal.toggleOverview();" title="프레젠테이션 개요 (O / ESC)">
      🗂️ <span>개요</span>
    </button>
    <button class="cb-hud-btn" id="cb-hud-download-btn" onclick="cbDownloadPdf()" title="PDF로 저장" aria-label="PDF로 저장">
      📥 <span>PDF로 저장</span>
    </button>
    <button class="cb-hud-btn" onclick="cbCopySlideLink()" title="현재 프레젠테이션 링크 복사">
      🔗 <span>공유</span>
    </button>
    <button class="cb-hud-btn" onclick="cbToggleFullscreen()" title="전체화면 (F)">
      ⛶
    </button>
  </div>
</div>

<div id="cb-down-hint" class="cb-down-hint" onclick="if(window.Reveal) Reveal.down();" title="다음 하위 슬라이드로 계속 (↓ 방향키 또는 클릭)">
  <span>▼ 하위 슬라이드로 계속 (↓)</span>
</div>

<div id="cb-hud-sharebox" class="cb-hud-sharebox" style="display:none">
  <input id="cb-hud-shareurl" readonly value="" onclick="this.select()"/>
  <button id="cb-hud-sharecopybtn" onclick="cbCopyHudShareInput()">✓ 복사됨</button>
</div>

<script>
(function() {
  const hud = document.getElementById('cb-hud');
  const titleEl = document.getElementById('cb-hud-title');
  const coordsEl = document.getElementById('cb-hud-coords');
  const downHint = document.getElementById('cb-down-hint');

  // Sync theme with parent window (BookStack theme integration)
  function syncTheme() {
    try {
      let theme = 'light';
      if (window.parent && window.parent !== window && window.parent.document) {
        const pTheme = window.parent.document.documentElement.getAttribute('data-theme');
        if (pTheme) theme = pTheme;
      } else if (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches) {
        theme = 'dark';
      }
      document.documentElement.setAttribute('data-theme', theme);
      if (theme === 'dark') {
        document.documentElement.classList.add('theme-dark');
        document.body.classList.add('theme-dark');
      } else {
        document.documentElement.classList.remove('theme-dark');
        document.body.classList.remove('theme-dark');
      }
    } catch (_) {}
  }
  syncTheme();

  window.addEventListener('message', function(e) {
    if (e && e.data && e.data.type === 'cb-theme-change' && e.data.theme) {
      document.documentElement.setAttribute('data-theme', e.data.theme);
      if (e.data.theme === 'dark') {
        document.documentElement.classList.add('theme-dark');
        document.body.classList.add('theme-dark');
      } else {
        document.documentElement.classList.remove('theme-dark');
        document.body.classList.remove('theme-dark');
      }
    }
  });

  if (window.self !== window.top || new URLSearchParams(window.location.search).get('embed') === 'true') {
    document.documentElement.classList.add('is-embedded');
    document.body.classList.add('is-embedded');
    if (hud) hud.style.display = 'none';
  }

  if (document.title) {
    titleEl.textContent = document.title;
  }

  // Show HUD when mouse is near the top
  let hideTimer = null;
  function showHud() {
    if (document.documentElement.classList.contains('is-embedded')) return;
    hud.classList.add('visible');
    clearTimeout(hideTimer);
    hideTimer = setTimeout(() => {
      hud.classList.remove('visible');
    }, 3500);
  }

  document.addEventListener('mousemove', function(e) {
    if (e.clientY < 60) {
      showHud();
    }
  });
  document.addEventListener('touchstart', showHud, { passive: true });

  // Update downward navigation hint visibility
  function updateDownHint() {
    if (!downHint || !window.Reveal) return;
    const routes = Reveal.availableRoutes ? Reveal.availableRoutes() : null;
    if (routes && routes.down) {
      downHint.classList.add('visible');
    } else {
      downHint.classList.remove('visible');
    }
  }

  // Update slide coordinates and UI indicators
  function updateCoords() {
    if (!window.Reveal) return;
    const indices = Reveal.getIndices();
    const total = Reveal.getTotalSlides ? Reveal.getTotalSlides() : '?';
    const h = String(indices.h + 1).padStart(2, '0');
    const v = indices.v > 0 ? '.' + String(indices.v + 1) : '';
    const text = h + v + ' / ' + total;
    coordsEl.textContent = text;
    updateDownHint();
    try {
      if (window.parent && window.parent !== window) {
        window.parent.postMessage({
          type: 'cb-slidechanged',
          coords: text,
          h: indices.h,
          v: indices.v,
          total: total
        }, '*');
      }
    } catch (_) {}
  }

  function onRevealReady() {
    syncTheme();
    updateCoords();
    updateDownHint();
    fitCodeBlocks();
  }

  function onSlideChanged() {
    updateCoords();
    updateDownHint();
    fitCodeBlocks();
  }

  if (window.Reveal) {
    Reveal.on('ready', onRevealReady);
    Reveal.on('slidechanged', onSlideChanged);
  } else {
    window.addEventListener('load', function() {
      if (window.Reveal) {
        Reveal.on('ready', onRevealReady);
        Reveal.on('slidechanged', onSlideChanged);
      }
    });
  }

  window.cbToggleSpeaker = function() {
    if (!window.Reveal) return;
    const notesPlugin = Reveal.getPlugin('notes');
    if (notesPlugin && typeof notesPlugin.open === 'function') {
      notesPlugin.open();
    } else {
      const notesUrl = '/static/vendor/reveal.js/plugin/notes/speaker-view.html';
      window.open(notesUrl, 'reveal.js - Notes', 'width=1100,height=700');
    }
  };

  window.cbDownloadPdf = function() {
    if (window.location.search.includes('print-pdf')) {
      window.print();
      return;
    }
    let printFrame = document.getElementById('cb-hud-print-frame');
    if (!printFrame) {
      printFrame = document.createElement('iframe');
      printFrame.id = 'cb-hud-print-frame';
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
    const url = new URL(window.location.href);
    url.searchParams.set('print-pdf', '');
    printFrame.src = url.toString();
    printFrame.onload = function() {
      const cw = printFrame.contentWindow;
      const R = cw && cw.Reveal;
      const doPrint = function() {
        try { cw.focus(); cw.print(); } catch(_) { window.print(); }
      };
      if (R && typeof R.isReady === 'function' && R.isReady()) {
        setTimeout(doPrint, 250);
      } else if (R && typeof R.on === 'function') {
        R.on('pdf-ready', function() { setTimeout(doPrint, 150); });
        R.on('ready', function() { setTimeout(doPrint, 250); });
      } else {
        setTimeout(doPrint, 500);
      }
    };
  };
  window.cbOpenPrintPdf = window.cbDownloadPdf;

  window.cbCopySlideLink = async function() {
    let shareUrl = window.location.href;
    const urlObj = new URL(window.location.href);
    urlObj.searchParams.delete('embed');
    shareUrl = urlObj.toString();

    const docId = urlObj.searchParams.get('id');
    const existingToken = urlObj.searchParams.get('s');
    if (docId && !existingToken) {
      try {
        const r = await fetch('/share', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({doc_id: docId})
        });
        if (r.ok) {
          const d = await r.json();
          if (d.path) {
            const pUrl = new URL(d.path, window.location.origin);
            const token = pUrl.searchParams.get('s');
            if (token) {
              shareUrl = window.location.origin + '/p/presentation?s=' + encodeURIComponent(token);
            }
          }
        }
      } catch(_) {}
    }

    let copied = false;
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        await navigator.clipboard.writeText(shareUrl);
        copied = true;
      }
    } catch(_) {}

    const sb = document.getElementById('cb-hud-sharebox');
    const inp = document.getElementById('cb-hud-shareurl');
    const btn = document.getElementById('cb-hud-sharecopybtn');
    if (sb && inp && btn) {
      inp.value = shareUrl;
      btn.textContent = copied ? '✓ 복사됨' : '복사';
      sb.style.display = 'flex';
      inp.select();
      setTimeout(function() {
        if (sb) sb.style.display = 'none';
      }, 5000);
    }
  };
  window.cbCopyPresentationLink = window.cbCopySlideLink;

  window.cbCopyHudShareInput = function() {
    const inp = document.getElementById('cb-hud-shareurl');
    const btn = document.getElementById('cb-hud-sharecopybtn');
    if (inp) {
      inp.select();
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(inp.value).then(function() {
          if (btn) btn.textContent = '✓ 복사됨';
        }).catch(function() {
          try { document.execCommand('copy'); if (btn) btn.textContent = '✓ 복사됨'; } catch(_) {}
        });
      } else {
        try { document.execCommand('copy'); if (btn) btn.textContent = '✓ 복사됨'; } catch(_) {}
      }
    }
  };

  window.cbToggleFullscreen = function() {
    if (!document.fullscreenElement) {
      document.documentElement.requestFullscreen().catch(() => {});
    } else {
      document.exitFullscreen().catch(() => {});
    }
  };

  // Auto-fit code blocks to prevent horizontal overflow and scrollbars
  function fitCodeBlocks() {
    const pres = document.querySelectorAll('.reveal pre');
    pres.forEach(function(pre) {
      const code = pre.querySelector('code') || pre;
      code.style.fontSize = '';
      let fs = parseFloat(window.getComputedStyle(code).fontSize);
      const containerWidth = pre.clientWidth - 28;
      let safety = 30;
      while (code.scrollWidth > containerWidth && fs > 8 && safety > 0) {
        fs -= 0.5;
        code.style.fontSize = fs + 'px';
        safety--;
      }
    });
  }

  window.addEventListener('resize', fitCodeBlocks);
})();
</script>
<!-- End Claire Bible Presentation HUD Toolbar -->
"""


def sanitize_presentation_assets(html_content: str) -> str:
    """Ensure relative reveal.js asset paths are sanitized to /static/vendor/reveal.js/."""
    import re
    return re.sub(r"""(['"])reveal\.js/(dist|plugin)/""", r"""\1/static/vendor/reveal.js/\2/""", html_content)


def inject_hud_toolbar(html_content: str) -> str:
    """Inject glassmorphic HUD toolbar before </body> tag in reveal.js presentation HTML."""
    html_content = sanitize_presentation_assets(html_content)
    if "<!-- Claire Bible Presentation HUD Toolbar -->" in html_content:
        import re
        return re.sub(
            r"<!-- Claire Bible Presentation HUD Toolbar -->.*?<!-- End Claire Bible Presentation HUD Toolbar -->",
            HUD_SNIPPET.strip(),
            html_content,
            flags=re.DOTALL,
        )

    if "</body>" in html_content:
        return html_content.replace("</body>", f"{HUD_SNIPPET}\n</body>", 1)
    elif "</html>" in html_content:
        return html_content.replace("</html>", f"{HUD_SNIPPET}\n</html>", 1)
    else:
        return html_content + f"\n{HUD_SNIPPET}"
