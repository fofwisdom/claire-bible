/* --- Claire Bible: Unified Silvercord Shell, Pull-Out Local Graph, and Opinions Prototype --- */
(function () {
  "use strict";

  var TYPE_COLORS = {
    Tool: "#1f6feb",
    Framework: "#8957e5",
    Model: "#bf3989",
    Paper: "#bf8700",
    Article: "#1a7f37",
    Repo: "#1b7c83",
    Concept: "#cf222e",
    Person: "#bc4c00",
    Org: "#9a6700",
    Event: "#0969da",
    Note: "#6e7781"
  };

  function readDocData() {
    if (window.dc && typeof window.dc === "object") return window.dc;
    var el = document.getElementById("docdata");
    if (!el) return null;
    try {
      return JSON.parse(el.textContent || "{}");
    } catch (_) {
      return null;
    }
  }

  var dc = readDocData();
  if (!dc || !dc.silvercord || typeof dc.silvercord !== "object") {
    return;
  }

  var sc = dc.silvercord;
  var escFn = typeof window.esc === "function" ? window.esc : function (v) {
    return String(v == null ? "" : v).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  };
  var rtrFn = typeof window.rtr === "function" ? window.rtr : function (k) { return k; };

  function iconSvg(name, cls) {
    if (window.ClaireIcons && typeof window.ClaireIcons.svg === "function") {
      return window.ClaireIcons.svg(name, cls);
    }
    return "";
  }

  function applyStoredTheme() {
    var theme = "light";
    try {
      var stored = localStorage.getItem("claireTheme");
      if (stored === "dark" || stored === "light") {
        theme = stored;
      } else if (window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches) {
        theme = "dark";
      }
    } catch (_) {}
    document.documentElement.setAttribute("data-theme", theme);
    return theme;
  }

  function syncThemeButton(theme) {
    var btn = document.getElementById("sc-theme-btn");
    if (!btn) return;
    btn.innerHTML = iconSvg(theme === "dark" ? "sun" : "moon");
    btn.setAttribute("aria-pressed", theme === "dark" ? "true" : "false");
  }

  function toggleTheme() {
    var cur = document.documentElement.getAttribute("data-theme") === "dark" ? "dark" : "light";
    var next = cur === "dark" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", next);
    try { localStorage.setItem("claireTheme", next); } catch (_) {}
    syncThemeButton(next);
  }

  var initialTheme = applyStoredTheme();
  document.documentElement.classList.add("sc-enabled");
  if (document.body) document.body.classList.add("sc-enabled");

  var scBar = document.getElementById("sc-bar");
  var scBottomBar = document.getElementById("sc-bottom-bar");
  var scKbBadge = document.getElementById("sc-kb-badge");
  var scWsLink = document.getElementById("sc-ws-link");
  var scThemeBtn = document.getElementById("sc-theme-btn");
  var scGraphToggleTop = document.getElementById("sc-graph-toggle-top");
  var scGraphToggleBot = document.getElementById("sc-graph-toggle-bot");
  var scGraphCountTop = document.getElementById("sc-graph-count-top");
  var scGraphCountBot = document.getElementById("sc-graph-count-bot");
  var scOpBadgeTop = document.getElementById("sc-op-sample-badge-top");
  var scOpBadgeBot = document.getElementById("sc-op-sample-badge-bot");
  var scMainCol = document.getElementById("sc-main-col");
  var panelDoc = document.getElementById("sc-panel-doc");
  var panelOpinions = document.getElementById("sc-panel-opinions");
  var opinionsInner = document.getElementById("sc-opinions-inner");
  var desktopAside = document.getElementById("sc-desktop-aside");
  var mobileDialog = document.getElementById("sc-mobile-dialog");
  var srail = document.getElementById("srail");

  if (!scBar || !panelDoc || !panelOpinions || !desktopAside || !mobileDialog) {
    return;
  }

  scBar.hidden = false;
  if (scBottomBar) scBottomBar.hidden = false;
  desktopAside.hidden = false;

  var token = String(sc.token || "");
  var kbLabel = String(sc.kb_label || "").trim();
  var kbPublic = Boolean(sc.kb_public);
  var canWorkspace = Boolean(sc.can_open_workspace && sc.workspace_url);
  var workspaceUrl = String(sc.workspace_url || "");
  var graphUrl = String(sc.graph_url || (token ? ("/silvercord/" + encodeURIComponent(token) + "/graph") : ""));
  var initialEntityCount = Number(sc.entity_count || 0);

  if (scKbBadge) {
    scKbBadge.textContent = kbLabel || rtrFn("share.sc.link_only_badge");
  }
  if (scWsLink) {
    if (canWorkspace) {
      scWsLink.setAttribute("href", workspaceUrl);
      scWsLink.hidden = false;
    } else {
      scWsLink.hidden = true;
    }
  }
  if (scGraphCountTop) scGraphCountTop.textContent = String(initialEntityCount);
  if (scGraphCountBot) scGraphCountBot.textContent = String(initialEntityCount);
  if (scOpBadgeTop) scOpBadgeTop.hidden = !kbPublic;
  if (scOpBadgeBot) scOpBadgeBot.hidden = !kbPublic;
  syncThemeButton(initialTheme);
  if (scThemeBtn) {
    scThemeBtn.addEventListener("click", toggleTheme);
  }

  var state = {
    activeTab: "doc",
    savedDocScrollY: 0,
    graphOpen: false,
    graphSubtab: "graph",
    selectedEntityId: null,
    graphData: null,
    graphLoading: false,
    graphError: false
  };

  var mqDesktopChrome = window.matchMedia ? window.matchMedia("(min-width: 820px)") : null;
  var mqDesktopGraph = window.matchMedia ? window.matchMedia("(min-width: 1100px)") : null;

  function isDesktopChrome() {
    return mqDesktopChrome ? mqDesktopChrome.matches : window.innerWidth >= 820;
  }

  function isDesktopGraph() {
    return mqDesktopGraph ? mqDesktopGraph.matches : window.innerWidth >= 1100;
  }

  function currentHost() {
    return isDesktopGraph() ? desktopAside : mobileDialog;
  }

  function syncPanelLabels() {
    var desktop = isDesktopChrome();
    panelDoc.setAttribute("aria-labelledby", desktop ? "tab-doc-top" : "tab-doc-bot");
    panelOpinions.setAttribute("aria-labelledby", desktop ? "tab-op-top" : "tab-op-bot");
    if (scGraphToggleTop) {
      scGraphToggleTop.setAttribute("aria-controls", isDesktopGraph() ? "sc-desktop-aside" : "sc-mobile-dialog");
    }
  }

  function renderOpinionsPrototype() {
    if (!opinionsInner) return;
    var targetObj = sc.object_id != null ? ("/ap/objects/" + String(sc.object_id)) : (token ? ("/silvercord/" + token) : "/ap/objects/0");
    var html = '<div class="op-proto-banner">' +
      '<div class="op-proto-head">' +
      iconSvg("broadcast") +
      "<span>" + escFn(rtrFn("share.sc.op_heading")) + "</span>" +
      '<span class="proto-pill">' + escFn(rtrFn("share.sc.op_proto_badge")) + "</span>" +
      "</div>" +
      '<p class="op-proto-desc">' + escFn(rtrFn("share.sc.op_proto_desc")) + "</p>" +
      "</div>";

    if (kbPublic) {
      html += '<div id="op-public-content">' +
        '<div class="op-ap-meta">' +
        '<span class="remote-pill">' + iconSvg("globe") + " " + escFn(rtrFn("share.sc.op_ap_planned")) + "</span>" +
        "<span>" + escFn(rtrFn("share.sc.op_target_label")) + ": <code>" + escFn(targetObj) + "</code></span>" +
        "</div>" +
        '<div class="op-composer" aria-label="' + escFn(rtrFn("share.sc.op_composer_aria")) + '">' +
        '<textarea disabled aria-disabled="true" placeholder="' + escFn(rtrFn("share.sc.op_composer_placeholder", { target: targetObj })) + '"></textarea>' +
        '<div class="op-composer-foot">' +
        '<div class="op-action-row">' +
        '<button type="button" class="op-disabled-btn" disabled aria-disabled="true">' + iconSvg("heart") + " " + escFn(rtrFn("share.sc.op_like_planned")) + "</button>" +
        '<button type="button" class="op-disabled-btn" disabled aria-disabled="true">' + iconSvg("repeat") + " " + escFn(rtrFn("share.sc.op_boost_planned")) + "</button>" +
        "</div>" +
        '<button type="button" class="op-disabled-btn" disabled aria-disabled="true">' + iconSvg("send") + " " + escFn(rtrFn("share.sc.op_submit_disabled")) + "</button>" +
        "</div>" +
        "</div>" +
        '<div class="op-thread-box">' +
        '<div class="op-thread-title">' +
        "<span>" + escFn(rtrFn("share.sc.op_thread_title")) + "</span>" +
        '<span class="proto-pill">' + escFn(rtrFn("share.sc.op_sample_data")) + "</span>" +
        "</div>" +
        '<article class="op-post">' +
        '<div class="op-post-meta">' +
        '<span class="op-post-author">Alice</span>' +
        "<span>@alice@claire.example</span>" +
        '<span class="chip-pill">' + escFn(rtrFn("share.sc.op_badge_local")) + "</span>" +
        "<span>" + escFn(rtrFn("share.sc.op_sample_1_time")) + "</span>" +
        "</div>" +
        '<div class="op-post-body">' + escFn(rtrFn("share.sc.op_sample_1_body")) + "</div>" +
        '<div class="op-post-foot"><span>' + escFn(rtrFn("share.sc.op_sample_1_foot_1")) + "</span><span>" + escFn(rtrFn("share.sc.op_sample_1_foot_2")) + "</span></div>" +
        "</article>" +
        '<article class="op-post is-reply">' +
        '<div class="op-post-meta">' +
        '<span class="op-post-author">Infra Lab</span>' +
        "<span>@researcher@mastodon.example</span>" +
        '<span class="remote-pill">' + escFn(rtrFn("share.sc.op_badge_remote")) + "</span>" +
        "<span>" + escFn(rtrFn("share.sc.op_sample_2_time")) + "</span>" +
        "</div>" +
        '<div class="op-post-body">' + escFn(rtrFn("share.sc.op_sample_2_body")) + "</div>" +
        '<div class="op-post-foot"><span>' + escFn(rtrFn("share.sc.op_sample_2_foot")) + "</span></div>" +
        "</article>" +
        "</div>" +
        "</div>";
    } else {
      html += '<div id="op-private-content" class="op-private-box">' +
        "<strong>" + iconSvg("lock") + " " + escFn(rtrFn("share.sc.op_private_title")) + "</strong>" +
        "<span>" + escFn(rtrFn("share.sc.op_private_desc")) + "</span>" +
        "</div>";
    }
    opinionsInner.innerHTML = html;
  }

  function syncRailVisibility() {
    if (!srail) return;
    var hide = state.activeTab !== "doc" || mobileDialog.open;
    srail.hidden = hide;
    if (!hide && typeof window.updateSharedRail === "function") {
      window.updateSharedRail();
    }
  }

  function switchTab(nextTab, focusTab, opts) {
    var preserveScroll = !opts || opts.preserveScroll !== false;
    if (state.activeTab === "doc" && nextTab === "opinions") {
      state.savedDocScrollY = window.scrollY || window.pageYOffset || 0;
    }
    state.activeTab = nextTab;

    var showDoc = nextTab === "doc";
    if (showDoc) {
      panelDoc.removeAttribute("hidden");
      panelOpinions.setAttribute("hidden", "until-found");
      document.body.classList.remove("sc-opinions-active");
    } else {
      panelDoc.setAttribute("hidden", "until-found");
      panelOpinions.removeAttribute("hidden");
      document.body.classList.add("sc-opinions-active");
    }

    document.querySelectorAll(".sc-tab[data-tab]").forEach(function (btn) {
      var sel = btn.dataset.tab === nextTab;
      btn.setAttribute("aria-selected", sel ? "true" : "false");
      btn.toggleAttribute("focusgroupstart", sel);
      if (focusTab && sel && btn.offsetParent !== null) {
        btn.focus();
      }
    });

    if (preserveScroll) {
      if (showDoc) {
        window.scrollTo({ top: state.savedDocScrollY, behavior: "instant" });
      } else {
        window.scrollTo({ top: 0, behavior: "instant" });
      }
    }
    syncRailVisibility();
  }

  function syncGraphToggleState(expanded) {
    var val = expanded ? "true" : "false";
    if (scGraphToggleTop) scGraphToggleTop.setAttribute("aria-expanded", val);
    if (scGraphToggleBot) scGraphToggleBot.setAttribute("aria-expanded", val);
  }

  function normalizeGraphPayload(raw) {
    var data = raw && typeof raw === "object" ? raw : {};
    var scope = data.scope === "link_only" ? "link_only" : "full";
    var rawEdges = Array.isArray(data.edges) ? data.edges : [];
    var edges = rawEdges.map(function (e) {
      var isProv = Boolean(e && (e.provisional || e.dashes));
      return {
        from: String((e && e.from) || ""),
        to: String((e && e.to) || ""),
        rel: String((e && (e.rel || e.label)) || ""),
        provisional: isProv,
        dashes: isProv
      };
    });

    function normNode(n) {
      var obs = Array.isArray(n && n.observations) ? n.observations : (Array.isArray(n && n.obs) ? n.obs : []);
      var aliases = Array.isArray(n && n.aliases) ? n.aliases : [];
      var otherDocs = Array.isArray(n && n.other_docs) ? n.other_docs : [];
      var r1Conn = Array.isArray(n && n.connectedR1) ? n.connectedR1 : [];
      return {
        id: String((n && n.id) || ""),
        label: String((n && (n.label || n.name)) || (n && n.id) || ""),
        name: String((n && (n.name || n.label)) || (n && n.id) || ""),
        group: String((n && (n.group || n.type)) || "Concept"),
        degree: n && n.degree != null ? Number(n.degree) : null,
        aliases: aliases.map(String),
        observations: obs.map(String),
        entity_path: n && n.entity_path ? String(n.entity_path) : null,
        other_docs: otherDocs,
        hidden_other_docs_count: Number((n && n.hidden_other_docs_count) || 0),
        connectedR1: r1Conn
      };
    }

    var r1 = (Array.isArray(data.ring1) ? data.ring1 : []).map(normNode);
    var r1Ids = new Set(r1.map(function (n) { return n.id; }));
    var r2 = (Array.isArray(data.ring2) ? data.ring2 : []).map(function (rawN) {
      var n = normNode(rawN);
      if (!n.connectedR1.length) {
        var conn = [];
        edges.forEach(function (e) {
          if (e.from === n.id && r1Ids.has(e.to)) conn.push(e.to);
          else if (e.to === n.id && r1Ids.has(e.from)) conn.push(e.from);
        });
        n.connectedR1 = conn;
      }
      return n;
    });

    return {
      scope: scope,
      document: data.document || {},
      ring1: r1,
      ring2: r2,
      edges: edges,
      unshown_r1: Number(data.unshown_r1 || 0),
      unshown_r2: Number(data.unshown_r2 || 0),
      shared_docs: Array.isArray(data.shared_docs) ? data.shared_docs : [],
      hidden_shared_docs_count: Number(data.hidden_shared_docs_count || 0)
    };
  }

  function findEntity(id) {
    if (!state.graphData || !id) return null;
    var all = state.graphData.ring1.concat(state.graphData.ring2);
    for (var i = 0; i < all.length; i++) {
      if (all[i].id === id) return all[i];
    }
    return null;
  }

  /* --- CSS Custom Highlight API for Document Body Entity Marks --- */
  var activeMarkRanges = [];
  var hoverMarkEntityId = null;
  var markListenersBound = false;

  function isAsciiAlnum(ch) {
    if (!ch) return false;
    var code = ch.charCodeAt(0);
    return (code >= 48 && code <= 57) || (code >= 65 && code <= 90) || (code >= 97 && code <= 122);
  }

  function collectCandidates(ring1) {
    var raw = [];
    var totalNames = 0;
    (ring1 || []).forEach(function (n) {
      var names = [];
      if (n.label) names.push(n.label);
      else if (n.name) names.push(n.name);
      if (Array.isArray(n.aliases)) {
        n.aliases.forEach(function (a) { if (a && typeof a === "string") names.push(a); });
      }
      names.forEach(function (s) {
        var trimmed = s.trim();
        if (!trimmed) return;
        totalNames += 1;
        if (trimmed.length < 2) return;
        var isShortAscii = trimmed.length <= 3 && /^[A-Za-z0-9]{2,3}$/.test(trimmed);
        raw.push({
          text: trimmed,
          entityId: n.id,
          isShortAscii: isShortAscii,
          key: isShortAscii ? ("exact:" + trimmed) : ("lower:" + trimmed.toLowerCase())
        });
      });
    });

    if (totalNames > 300) return [];
    var keyEntities = new Map();
    raw.forEach(function (c) {
      if (!keyEntities.has(c.key)) keyEntities.set(c.key, new Set());
      keyEntities.get(c.key).add(c.entityId);
    });

    var valid = [];
    var seen = new Set();
    raw.forEach(function (c) {
      var entSet = keyEntities.get(c.key);
      if (entSet && entSet.size === 1) {
        var pair = c.key + "|" + c.entityId;
        if (!seen.has(pair)) {
          seen.add(pair);
          valid.push(c);
        }
      }
    });
    valid.sort(function (a, b) { return (b.text.length - a.text.length) || a.text.localeCompare(b.text); });
    return valid;
  }

  function findMarkableTextNodes(root) {
    var nodes = [];
    if (!root || !document.createTreeWalker) return nodes;
    var walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
      acceptNode: function (node) {
        if (!node.nodeValue || !node.nodeValue.trim()) return NodeFilter.FILTER_REJECT;
        var p = node.parentElement;
        if (!p) return NodeFilter.FILTER_REJECT;
        if (p.closest("h1, h2, h3, h4, h5, h6, code, pre, a, .katex, .katex-display, script, style")) return NodeFilter.FILTER_REJECT;
        if (!p.closest("p, li, td, th, blockquote")) return NodeFilter.FILTER_REJECT;
        return NodeFilter.FILTER_ACCEPT;
      }
    });
    var curr;
    while ((curr = walker.nextNode())) nodes.push(curr);
    return nodes;
  }

  function setMarkHover(entityId) {
    if (!entityId || typeof CSS === "undefined" || !CSS.highlights || typeof Highlight === "undefined") return;
    var matching = [];
    activeMarkRanges.forEach(function (item) {
      if (item.entityId === entityId) matching.push(item.range);
    });
    if (matching.length > 0) {
      CSS.highlights.set("claire-entity-hover", new Highlight(...matching));
    } else {
      CSS.highlights.delete("claire-entity-hover");
    }
  }

  function clearMarkHover() {
    if (typeof CSS === "undefined" || !CSS.highlights) return;
    CSS.highlights.delete("claire-entity-hover");
  }

  window.setReaderEntityMarkHover = setMarkHover;
  window.clearReaderEntityMarkHover = clearMarkHover;

  function setUnifiedHover(entityId) {
    if (!entityId) return;
    var host = currentHost();
    if (window.ClaireLocalGraph && typeof window.ClaireLocalGraph.setEntityHoverHighlight === "function") {
      window.ClaireLocalGraph.setEntityHoverHighlight(entityId, host);
    } else {
      document.querySelectorAll(".ctx-local-graph").forEach(function (el) { el.classList.add("has-hover"); });
      document.querySelectorAll('.ctx-lg-node[data-entity-id="' + entityId + '"]').forEach(function (el) { el.classList.add("hover-active"); });
      document.querySelectorAll('.ctx-lg-edge[data-from="' + entityId + '"], .ctx-lg-edge[data-to="' + entityId + '"]').forEach(function (el) { el.classList.add("hover-active"); });
      document.querySelectorAll('.ctx-lg-doc-edge[data-entity-id="' + entityId + '"]').forEach(function (el) { el.classList.add("hover-active"); });
      document.querySelectorAll('.sc-gp-row[data-entity-id="' + entityId + '"]').forEach(function (el) { el.classList.add("hover-active"); });
      setMarkHover(entityId);
    }
  }

  function clearUnifiedHover() {
    var host = currentHost();
    if (window.ClaireLocalGraph && typeof window.ClaireLocalGraph.clearEntityHoverHighlight === "function") {
      window.ClaireLocalGraph.clearEntityHoverHighlight(host);
    } else {
      document.querySelectorAll(".ctx-local-graph").forEach(function (el) { el.classList.remove("has-hover"); });
      document.querySelectorAll(".ctx-lg-node.hover-active, .ctx-lg-edge.hover-active, .ctx-lg-doc-edge.hover-active, .sc-gp-row.hover-active").forEach(function (el) { el.classList.remove("hover-active"); });
      clearMarkHover();
    }
  }

  function getCaretAtPoint(ev) {
    if (document.caretPositionFromPoint) {
      var pos = document.caretPositionFromPoint(ev.clientX, ev.clientY);
      if (pos) return { node: pos.offsetNode, offset: pos.offset };
    } else if (document.caretRangeFromPoint) {
      var r = document.caretRangeFromPoint(ev.clientX, ev.clientY);
      if (r) return { node: r.startContainer, offset: r.startOffset };
    }
    return null;
  }

  function markAtPoint(ev) {
    if (!activeMarkRanges.length) return null;
    var caret = getCaretAtPoint(ev);
    if (!caret) return null;
    for (var i = 0; i < activeMarkRanges.length; i++) {
      var item = activeMarkRanges[i];
      if (item.textNode !== caret.node || caret.offset < item.start || caret.offset > item.end) continue;
      var rects = item.range.getClientRects();
      for (var k = 0; k < rects.length; k++) {
        var r = rects[k];
        if (ev.clientX >= r.left - 1 && ev.clientX <= r.right + 1 && ev.clientY >= r.top - 1 && ev.clientY <= r.bottom + 1) return item;
      }
    }
    return null;
  }

  function bindBodyMarkListeners() {
    if (markListenersBound) return;
    markListenersBound = true;
    panelDoc.addEventListener("mousemove", function (ev) {
      if (!activeMarkRanges.length) {
        if (panelDoc.style.cursor === "pointer") panelDoc.style.cursor = "";
        return;
      }
      var hit = markAtPoint(ev);
      if (hit) {
        panelDoc.style.cursor = "pointer";
        if (hoverMarkEntityId !== hit.entityId) {
          hoverMarkEntityId = hit.entityId;
          setUnifiedHover(hit.entityId);
        }
      } else {
        if (panelDoc.style.cursor === "pointer") panelDoc.style.cursor = "";
        if (hoverMarkEntityId !== null) {
          hoverMarkEntityId = null;
          clearUnifiedHover();
        }
      }
    });
    panelDoc.addEventListener("mouseleave", function () {
      if (panelDoc.style.cursor === "pointer") panelDoc.style.cursor = "";
      if (hoverMarkEntityId !== null) {
        hoverMarkEntityId = null;
        clearUnifiedHover();
      }
    });
    panelDoc.addEventListener("click", function (ev) {
      if (!activeMarkRanges.length) return;
      var sel = window.getSelection ? window.getSelection() : null;
      if (sel && !sel.isCollapsed && sel.toString().trim().length > 0) return;
      var hit = markAtPoint(ev);
      if (hit) {
        hoverMarkEntityId = null;
        clearUnifiedHover();
        state.selectedEntityId = hit.entityId;
        openGraphPanel();
      }
    });
  }

  function applySilvercordEntityMarks(data) {
    if (typeof CSS === "undefined" || !CSS.highlights || typeof Highlight === "undefined") return;
    activeMarkRanges = [];
    hoverMarkEntityId = null;
    CSS.highlights.delete("claire-entity");
    CSS.highlights.delete("claire-entity-hover");

    var wrap = document.getElementById("wrap");
    if (!wrap || (wrap.textContent || "").length > 400000) return;
    var candidates = collectCandidates(data && data.ring1);
    if (!candidates.length) return;

    var roots = wrap.querySelectorAll(".doc-content");
    if (!roots.length) return;

    roots.forEach(function (root) {
      var textNodes = findMarkableTextNodes(root);
      textNodes.forEach(function (textNode) {
        var content = textNode.nodeValue || "";
        if (!content) return;
        var lower = content.toLowerCase();
        var intervals = [];
        candidates.forEach(function (cand) {
          var q = cand.isShortAscii ? cand.text : cand.text.toLowerCase();
          var target = cand.isShortAscii ? content : lower;
          var len = cand.text.length;
          var pos = 0;
          while (pos < target.length) {
            var idx = target.indexOf(q, pos);
            if (idx === -1) break;
            var endIdx = idx + len;
            pos = idx + 1;
            if (isAsciiAlnum(cand.text[0]) && idx > 0 && isAsciiAlnum(content[idx - 1])) continue;
            if (isAsciiAlnum(cand.text[len - 1]) && endIdx < content.length && isAsciiAlnum(content[endIdx])) continue;
            var overlaps = false;
            for (var k = 0; k < intervals.length; k++) {
              if (Math.max(idx, intervals[k].start) < Math.min(endIdx, intervals[k].end)) {
                overlaps = true;
                break;
              }
            }
            if (overlaps) continue;
            intervals.push({ start: idx, end: endIdx });
            try {
              var range = new Range();
              range.setStart(textNode, idx);
              range.setEnd(textNode, endIdx);
              activeMarkRanges.push({ range: range, entityId: cand.entityId, textNode: textNode, start: idx, end: endIdx });
            } catch (_) {}
          }
        });
      });
    });

    if (activeMarkRanges.length > 0) {
      CSS.highlights.set("claire-entity", new Highlight(...activeMarkRanges.map(function (i) { return i.range; })));
      bindBodyMarkListeners();
    }
  }

  /* --- Fallback Radial SVG Renderer (when local_graph.js is not yet merged) --- */
  function buildLocalGraphFallbackHtml(opts) {
    var r1 = (opts.r1 || []).slice(0, 24);
    var r2 = (opts.r2 || []).slice(0, 12);
    var edges = opts.edges || [];
    var pos = {};

    r1.forEach(function (n, i) {
      var angle = -Math.PI / 2 + (i * 2 * Math.PI / Math.max(r1.length, 1));
      pos[n.id] = { x: Math.round(Math.cos(angle) * 78 * 10) / 10, y: Math.round(Math.sin(angle) * 78 * 10) / 10, angle: angle };
    });

    r2.forEach(function (cand, idx) {
      var sinSum = 0;
      var cosSum = 0;
      var count = 0;
      (cand.connectedR1 || []).forEach(function (rid) {
        if (pos[rid]) {
          sinSum += Math.sin(pos[rid].angle);
          cosSum += Math.cos(pos[rid].angle);
          count += 1;
        }
      });
      var targetAngle = count > 0 ? Math.atan2(sinSum, cosSum) : (-Math.PI / 2 + idx * 2 * Math.PI / Math.max(r2.length, 1));
      cand._targetAngle = targetAngle;
    });
    var sortedR2 = r2.slice().sort(function (a, b) { return a._targetAngle - b._targetAngle; });
    sortedR2.forEach(function (cand, idx) {
      var angle = sortedR2.length <= 4 ? cand._targetAngle : (-Math.PI / 2 + (idx * 2 * Math.PI / sortedR2.length));
      pos[cand.id] = { x: Math.round(Math.cos(angle) * 148 * 10) / 10, y: Math.round(Math.sin(angle) * 148 * 10) / 10, angle: angle };
    });

    var linesHtml = "";
    r1.forEach(function (n) {
      var p = pos[n.id];
      linesHtml += '<line class="ctx-lg-doc-edge" data-entity-id="' + escFn(n.id) + '" x1="0" y1="0" x2="' + p.x + '" y2="' + p.y + '" />';
    });
    edges.forEach(function (e) {
      var p1 = pos[e.from];
      var p2 = pos[e.to];
      if (!p1 || !p2) return;
      var prov = Boolean(e.provisional || e.dashes);
      linesHtml += '<line class="ctx-lg-edge' + (prov ? ' provisional" stroke-dasharray="3 3' : "") + '" data-from="' + escFn(e.from) + '" data-to="' + escFn(e.to) + '" x1="' + p1.x + '" y1="' + p1.y + '" x2="' + p2.x + '" y2="' + p2.y + '" />';
    });

    var docTitle = opts.docTitle || rtrFn("share.sc.center_doc_aria");
    var nodesHtml = '<g class="ctx-lg-center" role="img" aria-label="' + escFn(docTitle) + '"><circle cx="0" cy="0" r="14" /><path d="M4 2.5h5l3 3v8H4zM9 2.5v3h3M6 8.5h4M6 11h4" transform="translate(-8,-8)" fill="none" stroke="var(--fg)" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round"/></g>';

    function renderNode(n, isR1) {
      var p = pos[n.id];
      if (!p) return "";
      var color = TYPE_COLORS[n.group] || "#8b949e";
      var label = n.label || n.name || n.id;
      var trunc = label.length > 10 ? (label.slice(0, 9) + "…") : label;
      var anchor = p.x >= 0 ? "start" : "end";
      var tx = p.x >= 0 ? (p.x + 8) : (p.x - 8);
      var ty = p.y + 3;
      var r = isR1 ? 6 : 4.5;
      return '<g class="ctx-lg-node ' + (isR1 ? "ring-1" : "ring-2") + '" role="button" tabindex="0" aria-label="' + escFn(label) + '" data-entity-id="' + escFn(n.id) + '">' +
        '<circle cx="' + p.x + '" cy="' + p.y + '" r="14" fill="none" pointer-events="all" />' +
        '<circle cx="' + p.x + '" cy="' + p.y + '" r="' + r + '" fill="' + escFn(color) + '" stroke="var(--bg)" stroke-width="1.2" />' +
        '<text x="' + tx + '" y="' + ty + '" text-anchor="' + anchor + '">' + escFn(trunc) + "</text></g>";
    }

    r1.forEach(function (n) { nodesHtml += renderNode(n, true); });
    sortedR2.forEach(function (n) { nodesHtml += renderNode(n, false); });

    var html = '<svg class="ctx-local-graph" viewBox="-160 -160 320 320" role="region" aria-label="' + escFn(opts.ariaLabel || rtrFn("share.sc.local_graph_aria")) + '">' + linesHtml + nodesHtml + "</svg>";
    if (opts.unshownCount > 0 && opts.unshownText) {
      html += '<p class="ctx-local-graph-unshown">' + escFn(opts.unshownText) + "</p>";
    }
    return html;
  }

  function renderLocalGraphSvgHtml(data) {
    var unshownCount = (data.unshown_r1 || 0) + (data.unshown_r2 || 0);
    var opts = {
      docTitle: (data.document && data.document.title) || dc.title || rtrFn("share.sc.center_doc_aria"),
      r1: data.ring1,
      r2: data.ring2,
      edges: data.edges,
      unshownCount: unshownCount,
      ariaLabel: rtrFn("share.sc.local_graph_aria"),
      unshownText: unshownCount > 0 ? rtrFn("share.sc.graph_unshown", { count: unshownCount }) : "",
      interactiveAttrs: false
    };
    if (window.ClaireLocalGraph && typeof window.ClaireLocalGraph.renderSvg === "function") {
      return window.ClaireLocalGraph.renderSvg(opts);
    }
    return buildLocalGraphFallbackHtml(opts);
  }

  function renderPanelBody() {
    var host = currentHost();
    var body = host.querySelector("#gp-body");
    var scopeNote = host.querySelector("#gp-scope-note");
    if (!body) return;

    var data = state.graphData;
    var isLinkOnly = data ? data.scope === "link_only" : Boolean(sc.link_only);
    var r1Count = data ? data.ring1.length : initialEntityCount;
    var r2Count = data ? data.ring2.length : 0;

    if (scopeNote) {
      if (isLinkOnly) {
        scopeNote.innerHTML = iconSvg("lock") + "<span>" + escFn(rtrFn("share.sc.scope_link_only")) + "</span>";
      } else {
        scopeNote.innerHTML = iconSvg("globe") + "<span>" + escFn(rtrFn("share.sc.scope_full", { r1: r1Count, r2: r2Count })) + "</span>";
      }
    }

    host.querySelectorAll(".gp-subtab").forEach(function (btn) {
      if (btn.dataset.gsub === "entities") {
        btn.textContent = rtrFn("share.sc.subtab_entities", { count: r1Count });
      }
      var sel = btn.dataset.gsub === state.graphSubtab && !state.selectedEntityId;
      btn.setAttribute("aria-selected", sel ? "true" : "false");
    });

    if (state.graphLoading) {
      body.innerHTML = '<div class="gp-state-box"><span>' + escFn(rtrFn("share.sc.graph_loading")) + "</span></div>";
      return;
    }

    if (state.graphError) {
      body.innerHTML = '<div class="gp-state-box"><span>' + escFn(rtrFn("share.sc.graph_error")) + '</span><button type="button" class="sc-btn" id="gp-retry-btn">' + escFn(rtrFn("share.sc.graph_retry")) + "</button></div>";
      var retryBtn = body.querySelector("#gp-retry-btn");
      if (retryBtn) {
        retryBtn.addEventListener("click", function () { fetchGraphData(true); });
      }
      return;
    }

    if (!data) {
      body.innerHTML = '<div class="gp-state-box"><span>' + escFn(rtrFn("share.sc.graph_loading")) + "</span></div>";
      return;
    }

    if (state.selectedEntityId) {
      var ent = findEntity(state.selectedEntityId);
      if (!ent) {
        state.selectedEntityId = null;
        renderPanelBody();
        return;
      }
      var rels = data.edges.filter(function (e) { return e.from === ent.id || e.to === ent.id; });
      var color = TYPE_COLORS[ent.group] || "#8b949e";
      var detailHtml = '<button type="button" class="gp-back-btn" id="gp-back-btn">' + escFn(rtrFn("share.sc.back_to_graph")) + "</button>";
      detailHtml += '<div class="gp-detail-card">';
      detailHtml += '<h3><span class="gp-dot" style="background:' + escFn(color) + '"></span>' + escFn(ent.label) + ' <span class="chip-pill">' + escFn(ent.group) + "</span></h3>";
      detailHtml += '<div class="gp-detail-id">' + escFn(rtrFn("share.sc.entity_id_label")) + ": <code>" + escFn(ent.id) + "</code></div>";

      if (ent.aliases && ent.aliases.length) {
        detailHtml += '<div><div class="gp-detail-sec">' + escFn(rtrFn("share.sc.entity_aliases")) + '</div><div class="gp-detail-chips">' + ent.aliases.map(function (a) { return '<span class="chip-pill">' + escFn(a) + "</span>"; }).join("") + "</div></div>";
      }
      if (ent.observations && ent.observations.length) {
        detailHtml += '<div><div class="gp-detail-sec">' + escFn(rtrFn("share.sc.entity_observations")) + '</div><ul class="gp-detail-list">' + ent.observations.map(function (o) { return "<li>" + escFn(o) + "</li>"; }).join("") + "</ul></div>";
      }

      detailHtml += '<div><div class="gp-detail-sec">' + escFn(rtrFn("share.sc.entity_relations", { count: rels.length })) + '</div><ul class="gp-detail-list">';
      rels.forEach(function (r) {
        var otherId = r.from === ent.id ? r.to : r.from;
        var other = findEntity(otherId);
        var arrow = r.from === ent.id ? "→" : "←";
        var relText = arrow + " " + r.rel + (r.provisional ? (" " + rtrFn("share.sc.rel_provisional")) : "");
        var otherLabel = other ? other.label : otherId;
        var targetHtml = other ? ('<button type="button" class="gp-rel-jump" data-jump-entity="' + escFn(other.id) + '">' + escFn(otherLabel) + "</button>") : escFn(otherLabel);
        detailHtml += "<li><code>" + escFn(relText) + "</code> " + targetHtml + "</li>";
      });
      detailHtml += "</ul></div>";

      if (isLinkOnly) {
        if (ent.hidden_other_docs_count > 0) {
          detailHtml += '<div class="gp-detail-note">' + escFn(rtrFn("share.sc.entity_other_docs_blocked", { count: ent.hidden_other_docs_count })) + "</div>";
        }
      } else if (ent.other_docs && ent.other_docs.length) {
        detailHtml += '<div><div class="gp-detail-sec">' + escFn(rtrFn("share.sc.entity_other_docs")) + '</div><ul class="gp-detail-list">';
        ent.other_docs.forEach(function (d) {
          if (d && d.path) {
            detailHtml += '<li><a href="' + escFn(d.path) + '">' + escFn(d.title || d.path) + "</a></li>";
          } else if (d && d.title) {
            detailHtml += "<li>" + escFn(d.title) + "</li>";
          }
        });
        detailHtml += "</ul></div>";
      }

      if (ent.entity_path) {
        detailHtml += '<div><a class="sc-btn" href="' + escFn(ent.entity_path) + '">' + escFn(rtrFn("share.sc.entity_open_page")) + "</a></div>";
      } else if (isLinkOnly) {
        detailHtml += '<div class="gp-detail-note">' + escFn(rtrFn("share.sc.entity_private_note")) + "</div>";
      }

      detailHtml += "</div>";
      body.innerHTML = detailHtml;

      var backBtn = body.querySelector("#gp-back-btn");
      if (backBtn) {
        backBtn.addEventListener("click", function () {
          state.selectedEntityId = null;
          renderPanelBody();
        });
      }
      body.querySelectorAll("[data-jump-entity]").forEach(function (btn) {
        btn.addEventListener("click", function () {
          state.selectedEntityId = btn.dataset.jumpEntity;
          renderPanelBody();
        });
      });
      return;
    }

    if (state.graphSubtab === "entities") {
      var listHtml = '<div class="gp-sec-title"><span>' + escFn(rtrFn("share.sc.entities_section", { count: data.ring1.length })) + "</span></div>";
      if (!data.ring1.length) {
        listHtml += '<p class="gp-empty-note">' + escFn(rtrFn("share.sc.graph_empty")) + "</p>";
      } else {
        listHtml += '<div class="gp-row-list">';
        data.ring1.forEach(function (n) {
          var c = TYPE_COLORS[n.group] || "#8b949e";
          listHtml += '<button type="button" class="sc-gp-row" data-entity-id="' + escFn(n.id) + '">' +
            '<span class="gp-row-top"><span><span class="gp-dot" style="background:' + escFn(c) + '"></span>' + escFn(n.label) + ' <small class="gp-row-type">' + escFn(n.group) + "</small></span>" +
            (n.degree != null ? ('<span class="chip-pill">' + escFn(rtrFn("share.sc.degree_count", { count: n.degree })) + "</span>") : "") + "</span>" +
            (n.observations[0] ? ('<span class="gp-row-sub">' + escFn(n.observations[0]) + "</span>") : "") +
            "</button>";
        });
        listHtml += "</div>";
      }
      body.innerHTML = listHtml;
    } else {
      var graphHtml = "";
      if (!data.ring1.length) {
        graphHtml += '<p class="gp-empty-note">' + escFn(rtrFn("share.sc.graph_empty")) + "</p>";
      } else {
        graphHtml += renderLocalGraphSvgHtml(data);
        graphHtml += '<div class="gp-legend">' +
          '<span class="gp-legend-item"><span class="gp-legend-line"></span>' + escFn(rtrFn("share.sc.legend_confirmed")) + "</span>" +
          '<span class="gp-legend-item"><span class="gp-legend-line is-provisional"></span>' + escFn(rtrFn("share.sc.legend_provisional")) + "</span>" +
          '<span class="gp-legend-item">' + escFn(rtrFn("share.sc.legend_ring2", { count: data.ring2.length })) + "</span>" +
          "</div>";
      }

      graphHtml += '<div class="gp-sec-title"><span>' + escFn(rtrFn("share.sc.shared_docs_title")) + "</span></div>";
      if (isLinkOnly) {
        var blockedText = data.hidden_shared_docs_count > 0 ? rtrFn("share.sc.blocked_docs_desc", { count: data.hidden_shared_docs_count }) : rtrFn("share.sc.blocked_docs_desc_zero");
        graphHtml += '<div class="gp-blocked-box">' +
          "<strong>" + iconSvg("lock") + " " + escFn(rtrFn("share.sc.blocked_docs_title")) + "</strong>" +
          "<span>" + escFn(blockedText) + "</span>" +
          "</div>";
      } else if (data.shared_docs.length) {
        graphHtml += '<div class="gp-row-list">';
        data.shared_docs.forEach(function (d) {
          var count = d.shared_count != null ? d.shared_count : (d.count != null ? d.count : 0);
          var sub = [d.source_type, d.path].filter(Boolean).join(" · ");
          graphHtml += '<a class="sc-gp-row" href="' + escFn(d.path || "#") + '">' +
            '<span class="gp-row-top"><span>' + escFn(d.title || d.path || "") + '</span><span class="chip-pill">' + escFn(rtrFn("share.sc.shared_count", { count: count })) + "</span></span>" +
            (sub ? ('<span class="gp-row-sub">' + escFn(sub) + "</span>") : "") +
            "</a>";
        });
        graphHtml += "</div>";
      } else {
        graphHtml += '<p class="gp-empty-note">' + escFn(rtrFn("share.sc.shared_docs_empty")) + "</p>";
      }
      body.innerHTML = graphHtml;
    }

    function selectEntity(eid) {
      clearUnifiedHover();
      state.selectedEntityId = eid;
      renderPanelBody();
    }

    if (window.ClaireLocalGraph && typeof window.ClaireLocalGraph.bindInteractions === "function") {
      window.ClaireLocalGraph.bindInteractions(body, {
        onSelect: selectEntity,
        onHover: setUnifiedHover,
        onLeave: clearUnifiedHover
      });
    } else {
      body.querySelectorAll(".ctx-lg-node[data-entity-id]").forEach(function (el) {
        var eid = el.dataset.entityId;
        el.addEventListener("mouseenter", function () { setUnifiedHover(eid); });
        el.addEventListener("mouseleave", clearUnifiedHover);
        el.addEventListener("focus", function () { setUnifiedHover(eid); });
        el.addEventListener("blur", clearUnifiedHover);
        el.addEventListener("click", function () { selectEntity(eid); });
        el.addEventListener("keydown", function (ev) {
          if (ev.key === "Enter" || ev.key === " ") {
            ev.preventDefault();
            selectEntity(eid);
          }
        });
      });
    }

    body.querySelectorAll(".sc-gp-row[data-entity-id]").forEach(function (el) {
      var eid = el.dataset.entityId;
      el.addEventListener("mouseenter", function () { setUnifiedHover(eid); });
      el.addEventListener("mouseleave", clearUnifiedHover);
      el.addEventListener("focus", function () { setUnifiedHover(eid); });
      el.addEventListener("blur", clearUnifiedHover);
      el.addEventListener("click", function () { selectEntity(eid); });
      el.addEventListener("keydown", function (ev) {
        if (ev.key === "Enter" || ev.key === " ") {
          ev.preventDefault();
          selectEntity(eid);
        }
      });
    });
  }

  function fetchGraphData(force) {
    if (!graphUrl) {
      state.graphError = true;
      renderPanelBody();
      return;
    }
    if (state.graphLoading) return;
    if (state.graphData && !force) return;

    state.graphLoading = true;
    state.graphError = false;
    if (state.graphOpen) renderPanelBody();

    fetch(graphUrl, { credentials: "same-origin", headers: { Accept: "application/json" } })
      .then(function (resp) {
        if (!resp.ok) throw new Error("HTTP " + resp.status);
        return resp.json();
      })
      .then(function (payload) {
        var norm = normalizeGraphPayload(payload);
        state.graphData = norm;
        state.graphLoading = false;
        state.graphError = false;
        var totalR1 = norm.ring1.length + (norm.unshown_r1 || 0);
        if (scGraphCountTop) scGraphCountTop.textContent = String(totalR1);
        if (scGraphCountBot) scGraphCountBot.textContent = String(totalR1);
        applySilvercordEntityMarks(norm);
        if (state.graphOpen) renderPanelBody();
      })
      .catch(function () {
        state.graphLoading = false;
        state.graphError = true;
        if (state.graphOpen) renderPanelBody();
      });
  }

  function mountGraphPanel() {
    var tmpl = document.getElementById("sc-graph-panel-template");
    desktopAside.innerHTML = "";
    mobileDialog.innerHTML = "";
    if (!state.graphOpen || !tmpl) return;

    var host = currentHost();
    host.appendChild(tmpl.content.cloneNode(true));
    if (window.ClaireIcons && typeof window.ClaireIcons.hydrate === "function") {
      window.ClaireIcons.hydrate(host);
    }

    var closeBtn = host.querySelector(".gp-close-btn");
    if (closeBtn) closeBtn.addEventListener("click", closeGraphPanel);

    var subtabs = Array.from(host.querySelectorAll(".gp-subtab"));
    subtabs.forEach(function (btn, idx) {
      btn.addEventListener("click", function () {
        state.graphSubtab = btn.dataset.gsub;
        state.selectedEntityId = null;
        renderPanelBody();
      });
      btn.addEventListener("keydown", function (ev) {
        var nextIdx = -1;
        if (ev.key === "ArrowRight") nextIdx = (idx + 1) % subtabs.length;
        else if (ev.key === "ArrowLeft") nextIdx = (idx - 1 + subtabs.length) % subtabs.length;
        else if (ev.key === "Home") nextIdx = 0;
        else if (ev.key === "End") nextIdx = subtabs.length - 1;
        if (nextIdx !== -1) {
          ev.preventDefault();
          state.graphSubtab = subtabs[nextIdx].dataset.gsub;
          state.selectedEntityId = null;
          renderPanelBody();
          subtabs[nextIdx].focus();
        }
      });
    });

    renderPanelBody();
  }

  function openGraphPanel() {
    state.graphOpen = true;
    syncGraphToggleState(true);

    if (isDesktopGraph()) {
      if (mobileDialog.open) mobileDialog.close();
      document.body.classList.add("sc-graph-open");
      document.body.classList.remove("sc-dialog-open");
      mountGraphPanel();
    } else {
      document.body.classList.remove("sc-graph-open");
      document.body.classList.add("sc-dialog-open");
      mountGraphPanel();
      if (!mobileDialog.open) mobileDialog.showModal();
    }
    syncRailVisibility();

    if (!state.graphData && !state.graphLoading) {
      fetchGraphData(false);
    }
  }

  function closeGraphPanel() {
    state.graphOpen = false;
    syncGraphToggleState(false);
    document.body.classList.remove("sc-graph-open");
    document.body.classList.remove("sc-dialog-open");
    if (mobileDialog.open) mobileDialog.close();
    desktopAside.innerHTML = "";
    mobileDialog.innerHTML = "";
    clearUnifiedHover();
    syncRailVisibility();
  }

  function bindDialogDismiss(dlg) {
    if (!dlg || dlg.dataset.cbBound === "1") return;
    dlg.dataset.cbBound = "1";
    var pressedOutside = false;
    function outside(ev) {
      var r = dlg.getBoundingClientRect();
      return ev.clientX < r.left || ev.clientX > r.right || ev.clientY < r.top || ev.clientY > r.bottom;
    }
    dlg.addEventListener("pointerdown", function (ev) {
      pressedOutside = ev.target === dlg && outside(ev);
    });
    dlg.addEventListener("pointerup", function (ev) {
      if (pressedOutside && ev.target === dlg && outside(ev) && dlg.open) {
        closeGraphPanel();
      }
      pressedOutside = false;
    });
    dlg.addEventListener("cancel", function (ev) {
      ev.preventDefault();
      closeGraphPanel();
    });
    dlg.addEventListener("close", function () {
      if (state.graphOpen && !isDesktopGraph()) {
        closeGraphPanel();
      } else {
        document.body.classList.remove("sc-dialog-open");
        syncRailVisibility();
      }
    });
  }

  bindDialogDismiss(mobileDialog);

  [scGraphToggleTop, scGraphToggleBot].forEach(function (btn) {
    if (!btn) return;
    btn.addEventListener("click", function () {
      if (state.graphOpen) closeGraphPanel();
      else openGraphPanel();
    });
  });

  document.querySelectorAll(".sc-tab[data-tab]").forEach(function (tab) {
    tab.addEventListener("click", function () {
      switchTab(tab.dataset.tab, false);
    });
    tab.addEventListener("keydown", function (ev) {
      var list = tab.closest(".sc-tablist");
      if (!list) return;
      var tabs = Array.from(list.querySelectorAll(".sc-tab[data-tab]"));
      var idx = tabs.indexOf(tab);
      var next = -1;
      if (ev.key === "ArrowRight") next = (idx + 1) % tabs.length;
      else if (ev.key === "ArrowLeft") next = (idx - 1 + tabs.length) % tabs.length;
      else if (ev.key === "Home") next = 0;
      else if (ev.key === "End") next = tabs.length - 1;
      if (next !== -1) {
        ev.preventDefault();
        switchTab(tabs[next].dataset.tab, true);
      }
    });
  });

  if (scMainCol) {
    scMainCol.addEventListener("beforematch", function (ev) {
      var target = ev.target;
      var panel = target && target.closest ? target.closest("#sc-panel-doc, #sc-panel-opinions") : null;
      if (panel === panelOpinions) {
        switchTab("opinions", false, { preserveScroll: false });
      } else if (panel === panelDoc) {
        switchTab("doc", false, { preserveScroll: false });
      }
    });
  }

  function handleViewportChange() {
    syncPanelLabels();
    if (state.graphOpen) {
      openGraphPanel();
    }
  }

  if (mqDesktopChrome && mqDesktopChrome.addEventListener) {
    mqDesktopChrome.addEventListener("change", syncPanelLabels);
  }
  if (mqDesktopGraph && mqDesktopGraph.addEventListener) {
    mqDesktopGraph.addEventListener("change", handleViewportChange);
  }

  syncPanelLabels();
  renderOpinionsPrototype();
  syncRailVisibility();
})();
