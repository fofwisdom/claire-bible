/* Calm Federated Observatory interactions: upfront hovercard ARIA wiring, progressive hover/focus fallback, and responsive Bezier flow edges. */
(function () {
  "use strict";

  var NS = "http://www.w3.org/2000/svg";

  function wireHovercards() {
    var triggers = document.querySelectorAll("[interestfor]");
    if (!triggers.length) return;
    var supportsNativeInterest = "interestForElement" in HTMLButtonElement.prototype;
    var supportsAnchor = typeof CSS !== "undefined" && typeof CSS.supports === "function" && CSS.supports("position-area", "block-end");

    triggers.forEach(function (trigger) {
      var targetId = trigger.getAttribute("interestfor");
      var card = targetId ? document.getElementById(targetId) : null;
      if (!card || card.getAttribute("popover") !== "hint") return;

      if (!card.hasAttribute("role")) card.setAttribute("role", "tooltip");
      if (!trigger.hasAttribute("aria-describedby")) trigger.setAttribute("aria-describedby", targetId);
      if (trigger.dataset.obsHcBound === "1") return;
      trigger.dataset.obsHcBound = "1";

      if (supportsNativeInterest) return;

      var hideTimer = null;
      function placeFallback() {
        if (supportsAnchor) return;
        var rect = trigger.getBoundingClientRect();
        var vw = window.innerWidth || 1024;
        var vh = window.innerHeight || 768;
        var w = card.offsetWidth || 280;
        var h = card.offsetHeight || 120;
        var top = rect.bottom + 6;
        if (top + h > vh - 10 && rect.top - 6 - h >= 10) top = rect.top - 6 - h;
        var left = Math.max(10, Math.min(rect.left, vw - w - 10));
        card.style.top = Math.round(top) + "px";
        card.style.left = Math.round(left) + "px";
      }

      function showCard() {
        if (hideTimer) {
          clearTimeout(hideTimer);
          hideTimer = null;
        }
        document.querySelectorAll(".obs-hovercard[popover]").forEach(function (other) {
          if (other === card) return;
          other.classList.remove("is-open");
          try {
            if (other.matches(":popover-open")) other.hidePopover();
          } catch (_) {}
        });
        try {
          if (typeof card.showPopover === "function") {
            if (!card.matches(":popover-open")) card.showPopover({ source: trigger });
          } else {
            card.classList.add("is-open");
          }
        } catch (_) {
          card.classList.add("is-open");
        }
        placeFallback();
      }

      function scheduleHide() {
        if (hideTimer) clearTimeout(hideTimer);
        hideTimer = setTimeout(function () {
          card.classList.remove("is-open");
          try {
            if (typeof card.hidePopover === "function" && card.matches(":popover-open")) card.hidePopover();
          } catch (_) {}
        }, 120);
      }

      trigger.addEventListener("pointerenter", showCard);
      trigger.addEventListener("pointerleave", scheduleHide);
      trigger.addEventListener("focus", showCard);
      trigger.addEventListener("blur", scheduleHide);
      card.addEventListener("pointerenter", function () {
        if (hideTimer) {
          clearTimeout(hideTimer);
          hideTimer = null;
        }
      });
      card.addEventListener("pointerleave", scheduleHide);
    });

    document.addEventListener("keydown", function (ev) {
      if (ev.key !== "Escape") return;
      document.querySelectorAll(".obs-hovercard[popover]").forEach(function (card) {
        card.classList.remove("is-open");
        try {
          if (typeof card.hidePopover === "function" && card.matches(":popover-open")) card.hidePopover();
        } catch (_) {}
      });
    });
  }

  function makeSvgEl(tag, attrs) {
    var el = document.createElementNS(NS, tag);
    Object.keys(attrs).forEach(function (k) {
      el.setAttribute(k, String(attrs[k]));
    });
    return el;
  }

  function drawObservatoryCurves() {
    var shell = document.querySelector(".obs-flow-shell");
    if (!shell) return;
    var svg = shell.querySelector(".obs-flow-svg");
    if (!svg) return;
    var grid = shell.querySelector(".obs-flow-grid");
    if (!grid) return;

    while (svg.firstChild) svg.removeChild(svg.firstChild);

    if (getComputedStyle(svg).display === "none") return;

    var shellRect = shell.getBoundingClientRect();
    var w = Math.max(1, Math.round(shellRect.width));
    var h = Math.max(1, Math.round(shellRect.height));
    svg.setAttribute("viewBox", "0 0 " + w + " " + h);

    var localNode = shell.querySelector('[data-obs-node="local"]');
    var themeNodes = Array.prototype.slice.call(shell.querySelectorAll('[data-obs-lane="themes"] .obs-node'));
    var peerNodes = Array.prototype.slice.call(shell.querySelectorAll('[data-obs-lane="peers"] .obs-node'));
    var reducedMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    function rightAnchor(el) {
      var r = el.getBoundingClientRect();
      return { x: r.right - shellRect.left, y: r.top - shellRect.top + r.height / 2 };
    }

    function leftAnchor(el) {
      var r = el.getBoundingClientRect();
      return { x: r.left - shellRect.left, y: r.top - shellRect.top + r.height / 2 };
    }

    function curvePath(p1, p2) {
      var dx = Math.max(24, (p2.x - p1.x) * 0.48);
      return "M " + p1.x.toFixed(1) + " " + p1.y.toFixed(1) + " C " + (p1.x + dx).toFixed(1) + " " + p1.y.toFixed(1) + ", " + (p2.x - dx).toFixed(1) + " " + p2.y.toFixed(1) + ", " + p2.x.toFixed(1) + " " + p2.y.toFixed(1);
    }

    function addEdge(d, cls, dotCls, durSec) {
      var path = makeSvgEl("path", { d: d, class: cls });
      svg.appendChild(path);
      if (!reducedMotion && dotCls) {
        var circle = makeSvgEl("circle", { r: "2.4", class: dotCls, opacity: "0.78" });
        var anim = makeSvgEl("animateMotion", { dur: durSec + "s", repeatCount: "indefinite", path: d });
        circle.appendChild(anim);
        svg.appendChild(circle);
      }
    }

    if (localNode && themeNodes.length) {
      var src = rightAnchor(localNode);
      themeNodes.forEach(function (thEl, idx) {
        var dst = leftAnchor(thEl);
        addEdge(curvePath(src, dst), "obs-edge-local", idx === 0 ? "obs-edge-dot" : "", 4.2);
      });
    }

    if (themeNodes.length && peerNodes.length) {
      var peerMap = {};
      peerNodes.forEach(function (pEl) {
        var dom = pEl.getAttribute("data-obs-peer");
        if (dom) peerMap[dom] = pEl;
      });
      var anyExplicit = false;
      themeNodes.forEach(function (thEl, tIdx) {
        var rawPeers = thEl.getAttribute("data-obs-peers") || "";
        var connected = rawPeers ? rawPeers.split(",").filter(Boolean) : [];
        if (!connected.length) return;
        var src = rightAnchor(thEl);
        connected.forEach(function (dom, pIdx) {
          var pEl = peerMap[dom];
          if (!pEl) return;
          anyExplicit = true;
          addEdge(curvePath(src, leftAnchor(pEl)), "obs-edge-peer", tIdx === 0 && pIdx === 0 ? "obs-edge-dot obs-edge-dot-remote" : "", 5.4);
        });
      });
      if (!anyExplicit && peerNodes[0]) {
        var fallbackSrc = rightAnchor(themeNodes[0]);
        peerNodes.forEach(function (pEl, idx) {
          addEdge(curvePath(fallbackSrc, leftAnchor(pEl)), "obs-edge-peer", idx === 0 ? "obs-edge-dot obs-edge-dot-remote" : "", 5.6);
        });
      }
    }
  }

  function boot() {
    wireHovercards();
    drawObservatoryCurves();
    var shell = document.querySelector(".obs-flow-shell");
    if (shell && typeof ResizeObserver === "function") {
      var ro = new ResizeObserver(drawObservatoryCurves);
      ro.observe(shell);
    } else {
      window.addEventListener("resize", drawObservatoryCurves);
    }
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
