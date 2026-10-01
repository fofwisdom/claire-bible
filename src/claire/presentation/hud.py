"""Heads-Up Display (HUD) toolbar injector for Claire Bible presentations.

Provides an unobtrusive floating glassmorphic toolbar with navigation return,
2D slide coordinate tracking, speaker notes, overview, PDF print, and fullscreen toggle.
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
  background: rgba(14, 17, 22, 0.85);
  backdrop-filter: blur(16px);
  -webkit-backdrop-filter: blur(16px);
  border-bottom: 1px solid rgba(255, 255, 255, 0.12);
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
  font-family: 'Noto Sans KR', -apple-system, BlinkMacSystemFont, sans-serif;
  font-size: 13px;
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
  font-family: 'JetBrains Mono', 'D2Coding', monospace;
  color: #58a6ff;
  letter-spacing: 0.05em;
}
.cb-hud-btn {
  background: rgba(255, 255, 255, 0.08);
  border: 1px solid rgba(255, 255, 255, 0.15);
  color: #f0f6fc;
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
.cb-hud-btn:hover {
  background: rgba(88, 166, 255, 0.2);
  border-color: #58a6ff;
  color: #ffffff;
}
.cb-hud-title {
  color: #8b949e;
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
  background: rgba(22, 27, 34, 0.96);
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
  border: 1px solid #58a6ff;
  border-radius: 6px;
  box-shadow: 0 6px 20px rgba(0,0,0,0.5);
  z-index: 100000;
  font-size: 12px;
}
.cb-hud-sharebox input {
  width: 280px;
  background: rgba(13, 17, 23, 0.9);
  border: 1px solid rgba(255, 255, 255, 0.2);
  border-radius: 4px;
  color: #f0f6fc;
  padding: 5px 8px;
  font-size: 12px;
  font-family: 'JetBrains Mono', 'D2Coding', monospace;
  outline: none;
}
.cb-hud-sharebox input:focus {
  border-color: #58a6ff;
}
.cb-hud-sharebox button {
  background: #0284c7;
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
/* When embedded in an iframe (e.g. Claire Bible workspace), hide in-frame floating HUD */
html.is-embedded #cb-hud, body.is-embedded #cb-hud {
  display: none !important;
}
</style>

<div id="cb-hud" class="cb-hud">
  <div class="cb-hud-left">
    <span class="cb-hud-logo" style="font-weight:700;color:#58a6ff;margin-right:6px">📽️ Claire Bible</span>
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
    <button class="cb-hud-btn" onclick="cbOpenPrintPdf()" title="PDF 인쇄 모드">
      🖨️ <span>인쇄</span>
    </button>
    <button class="cb-hud-btn" onclick="cbCopySlideLink()" title="현재 프레젠테이션 링크 복사">
      🔗 <span>공유</span>
    </button>
    <button class="cb-hud-btn" onclick="cbToggleFullscreen()" title="전체화면 (F)">
      ⛶
    </button>
  </div>
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

  // Update slide coordinates
  function updateCoords() {
    if (!window.Reveal) return;
    const indices = Reveal.getIndices();
    const total = Reveal.getTotalSlides ? Reveal.getTotalSlides() : '?';
    const h = String(indices.h + 1).padStart(2, '0');
    const v = indices.v > 0 ? '.' + String(indices.v + 1) : '';
    const text = h + v + ' / ' + total;
    coordsEl.textContent = text;
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

  if (window.Reveal) {
    Reveal.on('ready', updateCoords);
    Reveal.on('slidechanged', updateCoords);
  } else {
    window.addEventListener('load', function() {
      if (window.Reveal) {
        Reveal.on('ready', updateCoords);
        Reveal.on('slidechanged', updateCoords);
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

  window.cbOpenPrintPdf = function() {
    const url = new URL(window.location.href);
    url.searchParams.set('print-pdf', '');
    window.open(url.toString(), '_blank');
  };

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

  if (window.Reveal) {
    Reveal.on('ready', fitCodeBlocks);
    Reveal.on('slidechanged', fitCodeBlocks);
  } else {
    window.addEventListener('load', fitCodeBlocks);
  }
  window.addEventListener('resize', fitCodeBlocks);
})();
</script>
<!-- End Claire Bible Presentation HUD Toolbar -->
"""


def inject_hud_toolbar(html_content: str) -> str:
    """Inject glassmorphic HUD toolbar before </body> tag in reveal.js presentation HTML."""
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
