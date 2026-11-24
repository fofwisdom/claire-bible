/* Shared radial local graph renderer and cross-view hover helpers (SD6). */
(function () {
  'use strict';

  const TYPE_COLORS = {Tool:'#1f6feb',Framework:'#8957e5',Model:'#bf3989',Paper:'#bf8700',Article:'#1a7f37',Repo:'#1b7c83',Concept:'#cf222e',Person:'#bc4c00',Org:'#9a6700',Event:'#0969da',Note:'#6e7781'};

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c];
    });
  }

  function jsArg(v) {
    return esc(JSON.stringify(String(v == null ? '' : v)));
  }

  function cssAttrEscape(v) {
    return String(v == null ? '' : v).replace(/\\/g, '\\\\').replace(/"/g, '\\"');
  }

  function clearDomHover(scope) {
    if (scope.classList && scope.classList.contains('ctx-local-graph')) {
      scope.classList.remove('has-hover');
    }
    scope.querySelectorAll('.ctx-local-graph').forEach(function (el) { el.classList.remove('has-hover'); });
    scope.querySelectorAll('.ctx-lg-node.hover-active, .ctx-lg-edge.hover-active, .ctx-lg-doc-edge.hover-active, .sc-gp-row.hover-active').forEach(function (el) { el.classList.remove('hover-active'); });
    scope.querySelectorAll('.ctx-entity-row.entity-hover-active, .home-map-row.entity-hover-active, .entitem.entity-hover-active').forEach(function (el) { el.classList.remove('entity-hover-active'); });
  }

  function setEntityHoverHighlight(id, root) {
    if (!id) return;
    const scope = (root && typeof root.querySelectorAll === 'function') ? root : document;
    const safeId = cssAttrEscape(id);
    clearDomHover(scope);
    if (scope.classList && scope.classList.contains('ctx-local-graph')) {
      scope.classList.add('has-hover');
    }
    scope.querySelectorAll('.ctx-local-graph').forEach(function (el) { el.classList.add('has-hover'); });
    scope.querySelectorAll('.ctx-lg-node[data-entity-id="' + safeId + '"]').forEach(function (el) { el.classList.add('hover-active'); });
    scope.querySelectorAll('.ctx-lg-edge[data-from="' + safeId + '"], .ctx-lg-edge[data-to="' + safeId + '"]').forEach(function (el) { el.classList.add('hover-active'); });
    scope.querySelectorAll('.ctx-lg-doc-edge[data-entity-id="' + safeId + '"]').forEach(function (el) { el.classList.add('hover-active'); });
    scope.querySelectorAll('.ctx-entity-row[data-entity-id="' + safeId + '"]').forEach(function (el) { el.classList.add('entity-hover-active'); });
    scope.querySelectorAll('.home-map-row[data-home-ent="' + safeId + '"]').forEach(function (el) { el.classList.add('entity-hover-active'); });
    scope.querySelectorAll('.entitem[data-ent-id="' + safeId + '"]').forEach(function (el) { el.classList.add('entity-hover-active'); });
    scope.querySelectorAll('.sc-gp-row[data-entity-id="' + safeId + '"]').forEach(function (el) { el.classList.add('hover-active'); });
    if (typeof window.setReaderEntityMarkHover === 'function') {
      window.setReaderEntityMarkHover(id);
    }
  }

  function clearEntityHoverHighlight(root) {
    const scope = (root && typeof root.querySelectorAll === 'function') ? root : document;
    clearDomHover(scope);
    if (typeof window.clearReaderEntityMarkHover === 'function') {
      window.clearReaderEntityMarkHover();
    }
  }

  function defaultInteractiveAttrs(n) {
    return 'onclick="selectDocEntity(' + jsArg(n.id) + ')" onkeydown="if(event.key===\'Enter\'||event.key===\' \'){event.preventDefault();selectDocEntity(' + jsArg(n.id) + ');}" onmouseenter="setEntityHoverHighlight(' + jsArg(n.id) + ')" onmouseleave="clearEntityHoverHighlight()"';
  }

  function renderSvg(opts) {
    const cfg = opts || {};
    const docTitle = cfg.docTitle || 'Document';
    const r1 = Array.isArray(cfg.r1) ? cfg.r1.slice(0, 24) : [];
    const r1Set = new Set(r1.map(function (n) { return n.id; }));
    const rawR2 = Array.isArray(cfg.r2) ? cfg.r2.slice(0, 12) : [];
    const edgeList = Array.isArray(cfg.edges) ? cfg.edges : [];
    const pos = {};

    r1.forEach(function (n, i) {
      const angle = -Math.PI / 2 + (i * 2 * Math.PI / Math.max(r1.length, 1));
      pos[n.id] = { x: Math.round(Math.cos(angle) * 78 * 10) / 10, y: Math.round(Math.sin(angle) * 78 * 10) / 10, angle: angle };
    });

    const r2 = rawR2.map(function (cand) {
      const connected = new Set();
      if (cand && cand.connectedR1 && typeof cand.connectedR1.forEach === 'function') {
        cand.connectedR1.forEach(function (rid) { connected.add(rid); });
      } else if (cand) {
        edgeList.forEach(function (e) {
          if (!e || (typeof e.id === 'string' && e.id.indexOf('cl_') === 0)) return;
          if (e.from === cand.id && r1Set.has(e.to)) connected.add(e.to);
          if (e.to === cand.id && r1Set.has(e.from)) connected.add(e.from);
        });
      }
      let sinSum = 0;
      let cosSum = 0;
      let count = 0;
      connected.forEach(function (r1Id) {
        if (pos[r1Id]) {
          sinSum += Math.sin(pos[r1Id].angle);
          cosSum += Math.cos(pos[r1Id].angle);
          count += 1;
        }
      });
      return { node: cand, targetAngle: count > 0 ? Math.atan2(sinSum, cosSum) : 0 };
    });

    r2.sort(function (a, b) { return a.targetAngle - b.targetAngle; });
    r2.forEach(function (entry, idx) {
      const angle = r2.length <= 4 ? entry.targetAngle : (-Math.PI / 2 + (idx * 2 * Math.PI / r2.length));
      pos[entry.node.id] = { x: Math.round(Math.cos(angle) * 148 * 10) / 10, y: Math.round(Math.sin(angle) * 148 * 10) / 10, angle: angle };
    });

    let linesHtml = '';
    r1.forEach(function (n) {
      const p = pos[n.id];
      linesHtml += '<line class="ctx-lg-doc-edge" data-entity-id="' + esc(n.id) + '" x1="0" y1="0" x2="' + p.x + '" y2="' + p.y + '" />';
    });

    const pairEdges = new Map();
    edgeList.forEach(function (e) {
      if (!e || (typeof e.id === 'string' && e.id.indexOf('cl_') === 0)) return;
      if (pos[e.from] && pos[e.to] && e.from !== e.to) {
        const pairKey = e.from < e.to ? (e.from + ':' + e.to) : (e.to + ':' + e.from);
        const existing = pairEdges.get(pairKey);
        const isProv = Boolean(e.provisional || e.dashes === true);
        const existingProv = existing ? Boolean(existing.provisional || existing.dashes === true) : false;
        if (!existing || (existingProv && !isProv)) {
          pairEdges.set(pairKey, e);
        }
      }
    });
    pairEdges.forEach(function (e) {
      const p1 = pos[e.from];
      const p2 = pos[e.to];
      if (p1 && p2) {
        const isProv = Boolean(e.provisional || e.dashes === true);
        linesHtml += '<line class="ctx-lg-edge' + (isProv ? ' provisional" stroke-dasharray="3 3' : '') + '" data-from="' + esc(e.from) + '" data-to="' + esc(e.to) + '" x1="' + p1.x + '" y1="' + p1.y + '" x2="' + p2.x + '" y2="' + p2.y + '" />';
      }
    });

    let nodesHtml = '<g class="ctx-lg-center" role="img" aria-label="' + esc(docTitle) + '"><circle cx="0" cy="0" r="14" /><path d="M4 2.5h5l3 3v8H4zM9 2.5v3h3M6 8.5h4M6 11h4" transform="translate(-8,-8)" fill="none" stroke="var(--fg)" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round"/></g>';

    function renderNode(n, isR1) {
      const p = pos[n.id];
      const color = TYPE_COLORS[n.group] || '#8b949e';
      const label = n.label || n.name || n.id;
      const trunc = label.length > 10 ? (label.slice(0, 9) + '…') : label;
      const anchor = p.x >= 0 ? 'start' : 'end';
      const tx = p.x >= 0 ? (p.x + 8) : (p.x - 8);
      const ty = p.y + 3;
      const r = isR1 ? 6 : 4.5;
      let extra = '';
      if (typeof cfg.interactiveAttrs === 'function') {
        const raw = cfg.interactiveAttrs(n, isR1);
        if (raw) extra = ' ' + String(raw).trim();
      } else if (cfg.interactiveAttrs === true) {
        extra = ' ' + defaultInteractiveAttrs(n);
      } else if (typeof cfg.interactiveAttrs === 'string' && cfg.interactiveAttrs.trim()) {
        extra = ' ' + cfg.interactiveAttrs.trim();
      }
      return '<g class="ctx-lg-node ' + (isR1 ? 'ring-1' : 'ring-2') + '" role="button" tabindex="0" aria-label="' + esc(label) + '" data-entity-id="' + esc(n.id) + '"' + extra + '>' + '<circle cx="' + p.x + '" cy="' + p.y + '" r="14" fill="none" pointer-events="all" />' + '<circle cx="' + p.x + '" cy="' + p.y + '" r="' + r + '" fill="' + esc(color) + '" stroke="var(--bg)" stroke-width="1.2" />' + '<text x="' + tx + '" y="' + ty + '" text-anchor="' + anchor + '">' + esc(trunc) + '</text>' + '</g>';
    }

    r1.forEach(function (n) { nodesHtml += renderNode(n, true); });
    r2.forEach(function (entry) { nodesHtml += renderNode(entry.node, false); });

    const svgAria = cfg.ariaLabel || 'Local graph';
    let h = '<svg class="ctx-local-graph" viewBox="-160 -160 320 320" role="region" aria-label="' + esc(svgAria) + '">' + linesHtml + nodesHtml + '</svg>';

    const totalUnshown = Number(cfg.unshownCount || 0);
    if (totalUnshown > 0) {
      const overflowLabel = (cfg.unshownText != null && cfg.unshownText !== '') ? String(cfg.unshownText) : ('+' + totalUnshown);
      h += '<p class="ctx-local-graph-unshown">' + esc(overflowLabel) + '</p>';
    }
    return h;
  }

  function bindInteractions(rootEl, handlers) {
    if (!rootEl || typeof rootEl.addEventListener !== 'function') return function () {};
    rootEl._claireLocalGraphHandlers = handlers || {};
    if (rootEl._claireLocalGraphBound) return rootEl._claireLocalGraphUnbind;

    function findNode(target) {
      return target && typeof target.closest === 'function' ? target.closest('.ctx-lg-node[data-entity-id]') : null;
    }

    function onClick(e) {
      const node = findNode(e.target);
      if (!node) return;
      const id = node.getAttribute('data-entity-id');
      const h = rootEl._claireLocalGraphHandlers || {};
      if (id && typeof h.onSelect === 'function') h.onSelect(id, node, e);
    }

    function onKeydown(e) {
      if (e.key !== 'Enter' && e.key !== ' ') return;
      const node = findNode(e.target);
      if (!node) return;
      e.preventDefault();
      const id = node.getAttribute('data-entity-id');
      const h = rootEl._claireLocalGraphHandlers || {};
      if (id && typeof h.onSelect === 'function') h.onSelect(id, node, e);
    }

    function onMouseover(e) {
      const node = findNode(e.target);
      if (!node) return;
      if (e.relatedTarget && node.contains(e.relatedTarget)) return;
      const id = node.getAttribute('data-entity-id');
      if (!id) return;
      const h = rootEl._claireLocalGraphHandlers || {};
      if (typeof h.onHover === 'function') h.onHover(id, node, e);
      else setEntityHoverHighlight(id, rootEl);
    }

    function onMouseout(e) {
      const node = findNode(e.target);
      if (!node) return;
      if (e.relatedTarget && node.contains(e.relatedTarget)) return;
      const id = node.getAttribute('data-entity-id');
      const h = rootEl._claireLocalGraphHandlers || {};
      if (typeof h.onLeave === 'function') h.onLeave(id, node, e);
      else clearEntityHoverHighlight(rootEl);
    }

    rootEl.addEventListener('click', onClick);
    rootEl.addEventListener('keydown', onKeydown);
    rootEl.addEventListener('mouseover', onMouseover);
    rootEl.addEventListener('mouseout', onMouseout);
    rootEl._claireLocalGraphBound = true;
    rootEl._claireLocalGraphUnbind = function () {
      rootEl.removeEventListener('click', onClick);
      rootEl.removeEventListener('keydown', onKeydown);
      rootEl.removeEventListener('mouseover', onMouseover);
      rootEl.removeEventListener('mouseout', onMouseout);
      delete rootEl._claireLocalGraphBound;
      delete rootEl._claireLocalGraphHandlers;
      delete rootEl._claireLocalGraphUnbind;
    };
    return rootEl._claireLocalGraphUnbind;
  }

  window.ClaireLocalGraph = {
    TYPE_COLORS: TYPE_COLORS,
    renderSvg: renderSvg,
    bindInteractions: bindInteractions,
    setEntityHoverHighlight: setEntityHoverHighlight,
    clearEntityHoverHighlight: clearEntityHoverHighlight
  };
})();
