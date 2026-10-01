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
  background: rgba(14, 17, 22, 0.82);
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
  max-width: 320px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  margin-left: 6px;
}
@media (max-width: 768px) {
  .cb-hud-title { display: none; }
  .cb-hud-btn span { display: none; }
}
</style>

<div id="cb-hud" class="cb-hud">
  <div class="cb-hud-left">
    <button class="cb-hud-btn" onclick="cbReturnToDoc()" title="문서로 복귀 (ESC / 닫기)">
      ← <span>문서로</span>
    </button>
    <span class="cb-hud-title" id="cb-hud-title"></span>
  </div>
  <div class="cb-hud-center">
    <span id="cb-hud-coords">01 / 01</span>
  </div>
  <div class="cb-hud-right">
    <button class="cb-hud-btn" onclick="cbToggleSpeaker()" title="발표자 모드 (S)">
      🎙️ <span>발표자</span>
    </button>
    <button class="cb-hud-btn" onclick="if(window.Reveal) Reveal.toggleOverview();" title="슬라이드 개요 (O / ESC)">
      🗂️ <span>개요</span>
    </button>
    <button class="cb-hud-btn" onclick="cbOpenPrintPdf()" title="PDF 인쇄 모드">
      🖨️ <span>인쇄</span>
    </button>
    <button class="cb-hud-btn" onclick="cbCopySlideLink()" title="현재 슬라이드 링크 복사">
      🔗 <span>공유</span>
    </button>
    <button class="cb-hud-btn" onclick="cbToggleFullscreen()" title="전체화면 (F)">
      ⛶
    </button>
  </div>
</div>

<script>
(function() {
  const hud = document.getElementById('cb-hud');
  const titleEl = document.getElementById('cb-hud-title');
  const coordsEl = document.getElementById('cb-hud-coords');

  if (document.title) {
    titleEl.textContent = document.title;
  }

  // Show HUD when mouse is near the top
  let hideTimer = null;
  function showHud() {
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
    coordsEl.textContent = h + v + ' / ' + total;
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

  window.cbReturnToDoc = function() {
    if (window.opener && !window.opener.closed) {
      window.close();
      window.opener.focus();
    } else {
      const url = new URL(window.location.href);
      const docId = url.searchParams.get('id');
      const shareToken = url.searchParams.get('s');
      if (shareToken) {
        window.location.href = '/p?s=' + encodeURIComponent(shareToken);
      } else if (docId) {
        window.location.href = '/?doc=' + encodeURIComponent(docId);
      } else {
        window.location.href = '/';
      }
    }
  };

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

  window.cbCopySlideLink = function() {
    const link = window.location.href;
    navigator.clipboard.writeText(link).then(function() {
      alert('현재 슬라이드 링크가 클립보드에 복사되었습니다:\\n' + link);
    }).catch(function() {
      prompt('슬라이드 링크 복사:', link);
    });
  };

  window.cbToggleFullscreen = function() {
    if (!document.fullscreenElement) {
      document.documentElement.requestFullscreen().catch(() => {});
    } else {
      document.exitFullscreen().catch(() => {});
    }
  };
})();
</script>
<!-- End Claire Bible Presentation HUD Toolbar -->
"""


def inject_hud_toolbar(html_content: str) -> str:
    """Inject glassmorphic HUD toolbar before </body> tag in reveal.js presentation HTML."""
    if "<!-- Claire Bible Presentation HUD Toolbar -->" in html_content:
        return html_content

    if "</body>" in html_content:
        return html_content.replace("</body>", f"{HUD_SNIPPET}\n</body>", 1)
    elif "</html>" in html_content:
        return html_content.replace("</html>", f"{HUD_SNIPPET}\n</html>", 1)
    else:
        return html_content + f"\n{HUD_SNIPPET}"
