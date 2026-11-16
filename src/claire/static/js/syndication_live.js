/* Claire Bible — Live Syndication & Cross-Theme Knowledge Workspace (/syndication) */
(function () {
  "use strict";

  // Line icon as inline SVG (static/js/icons.js).
  const ic = (name) => (window.ClaireIcons ? window.ClaireIcons.svg(name) : "");

  const ST = {
    PREFS_SLOT_PREFIX: "claire_synd_prefs_v2:",
    LIST_STYLES: ["list", "compact", "card", "roomy"],
    LEGACY_LIST_STYLES: { flat: "list" },
    DEFAULT_PREFS: {
      appTab: "kb",
      layoutMode: "three-panel",
      listStyle: "list",
      showExcerpt: true,
      defaultContentTab: "auto",
      readerFont: "sans",
      readerFontSize: 15,
      autoMarkReadOnOpen: true,
      autoIngestThreshold: 85,
      tagPolicy: {
        autoMapSourceTagsToEntities: true,
        createProvisionalEntityForUnknownTag: true,
        attachEntityHashtagsOnPublish: true,
        ignoredNoiseTags: "uncategorized, general, news, sponsor"
      },
      outboundPolicy: {
        rssDefaultMode: "quote_boost",
        apDefaultMode: "boost",
        atDefaultMode: "boost",
        includeCanonicalShareLink: true,
        quoteTemplate: "📌 {{title}} — {{url}} {{hashtags}}"
      }
    }
  };

  const LIST_STYLE_META = {
    list: { icon: "☰", label: "synd.kb.list_style.list", desc: "synd.kb.list_style.list_desc" },
    compact: { icon: "≡", label: "synd.kb.list_style.compact", desc: "synd.kb.list_style.compact_desc" },
    card: { icon: "▤", label: "synd.kb.list_style.card", desc: "synd.kb.list_style.card_desc" },
    roomy: { icon: "▭", label: "synd.kb.list_style.roomy", desc: "synd.kb.list_style.roomy_desc" }
  };

  // Each text of this view is a catalog key (ClaireI18n, static/locales/{en,ko}.json).
  const tr = (key, params) => window.ClaireI18n.t(key, params);
  const UI_LOCALE = document.documentElement.lang || "en";
  function i18n(key) { return tr(key); }
  function esc(s) { return String(s || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;"); }

  const cfg = window.__CLAIRE_CONFIG || {};
  const RAW_THEMES = Array.isArray(cfg.themes) && cfg.themes.length
    ? cfg.themes
    : [{ id: 0, seq: 0, number: "0", label: tr("synd.kb.default_theme"), icon: "📚", is_personal: true, is_public: true }];

  const THEMES = {};
  RAW_THEMES.forEach((srv) => {
    const k = String(srv.id);
    THEMES[k] = {
      id: srv.id,
      seq: srv.seq != null ? srv.seq : srv.id,
      number: srv.number != null ? String(srv.number) : String(srv.id),
      icon: srv.icon || "📚",
      label: srv.label || tr("synd.kb.theme_number", { id: srv.id }),
      isPublic: srv.is_public !== false,
      isPersonal: !!srv.is_personal,
      documents: [],
      entities: [],
      dispatches: [],
      loaded: false
    };
  });

  function normalizeListStyle(v) {
    const mapped = (ST.LEGACY_LIST_STYLES || {})[v] || v;
    return ST.LIST_STYLES.includes(mapped) ? mapped : ST.DEFAULT_PREFS.listStyle;
  }

  function normalizePrefs(p) {
    p.listStyle = normalizeListStyle(p.listStyle);
    if (p.outboundPolicy && p.outboundPolicy.apDefaultMode === "announce") p.outboundPolicy.apDefaultMode = "boost";
    return p;
  }

  const STORAGE_ACCOUNT_KEY = (() => {
    const meta = document.querySelector('meta[name="claire-storage-key"]');
    const v = meta ? String(meta.getAttribute("content") || "") : "";
    return v && !v.includes("__") ? v : "";
  })();

  function themeRefOf(themeKey) {
    const t = THEMES[String(themeKey)];
    return t && t.number != null ? String(t.number) : String(themeKey);
  }

  // Design Section 5.2 (CT23): the path selects the knowledge base. The personal knowledge base has the plain path; another one has "/themes/<number>" before the route (the server sends 308 to the path of the owner).
  function kbPath(themeKey, route) {
    const t = THEMES[String(themeKey)];
    if (!t || t.isPersonal || String(themeKey) === "0") return route;
    return `/themes/${encodeURIComponent(themeRefOf(themeKey))}${route}`;
  }

  function slotKey(themeKey) {
    return STORAGE_ACCOUNT_KEY ? `${ST.PREFS_SLOT_PREFIX}${STORAGE_ACCOUNT_KEY}:t${themeRefOf(themeKey)}` : null;
  }

  function readSlot(themeKey) {
    const key = slotKey(themeKey);
    if (!key) return null;
    try {
      const parsed = JSON.parse(localStorage.getItem(key) || "null");
      return parsed && typeof parsed === "object" && parsed.v === 2 ? parsed : null;
    } catch (_) {
      return null;
    }
  }

  function writeSlot(themeKey, slotPrefs, slotCustom) {
    const key = slotKey(themeKey);
    if (!key) return;
    try { localStorage.setItem(key, JSON.stringify({ v: 2, prefs: slotPrefs, custom: slotCustom || {} })); } catch (_) {}
  }

  function loadPrefs(themeKey) {
    const slot = readSlot(themeKey);
    const p = slot && slot.prefs && typeof slot.prefs === "object" ? slot.prefs : null;
    if (!p) return JSON.parse(JSON.stringify(ST.DEFAULT_PREFS));
    return normalizePrefs({
      ...ST.DEFAULT_PREFS,
      ...p,
      tagPolicy: { ...ST.DEFAULT_PREFS.tagPolicy, ...(p.tagPolicy || {}) },
      outboundPolicy: { ...ST.DEFAULT_PREFS.outboundPolicy, ...(p.outboundPolicy || {}) }
    });
  }

  function loadCustom(themeKey) {
    const slot = readSlot(themeKey);
    return slot && slot.custom && typeof slot.custom === "object" ? slot.custom : {};
  }

  function resolveInitialThemeKey() {
    const urlParams = new URLSearchParams(window.location.search);
    const reqTheme = urlParams.get("kb");
    if (reqTheme) {
      if (THEMES[reqTheme]) return reqTheme;
      const byNum = Object.keys(THEMES).find((k) => String(THEMES[k].number) === String(reqTheme));
      if (byNum) return byNum;
    }
    const activeKey = String(cfg.activeThemeId != null ? cfg.activeThemeId : "0");
    if (THEMES[activeKey]) return activeKey;
    return Object.keys(THEMES)[0] || "0";
  }

  const initThemeKey = resolveInitialThemeKey();
  const prefs = loadPrefs(initThemeKey);

  const state = {
    themeId: initThemeKey,
    layoutMode: prefs.layoutMode === "two-panel" ? "two-panel" : "three-panel",
    listStyle: prefs.listStyle,
    treeSel: { kind: "virtual", value: "all", label: tr("synd.kb.stream.all") },
    sortBy: "newest",
    searchQuery: "",
    selectedDocId: null,
    checkedIds: new Set(),
    contentTabByDoc: {},
    activeArticleMenuDocId: null,
    loading: false,
    // Stage 3 of the stage bar (FP4-5): "all", "published", or "unpublished". publishedKeys has "themeId:documentId" of each published document of the account.
    pubFilter: "all",
    publishedKeys: new Set(),
    // The counters of each published document (FP4-8): "themeId:documentId" gives the text that the client script made from the catalog.
    reactionLabels: {}
  };

  function reactionBadge(doc) {
    const theme = curTheme();
    const label = theme && state.reactionLabels[`${theme.id}:${doc.id}`];
    return label ? `<span class="tt-link-dot cl-inbox" data-reactions="${esc(doc.id)}">${esc(label)}</span>` : "";
  }

  function savePrefs() {
    writeSlot(state.themeId, prefs, loadCustom(state.themeId));
  }

  function curTheme() {
    return THEMES[state.themeId] || THEMES[Object.keys(THEMES)[0]];
  }

  function sanitizeContentHtml(html) {
    if (!html) return "";
    const purifier = window.DOMPurify;
    if (purifier && typeof purifier.sanitize === "function") {
      return purifier.sanitize(html, {
        ALLOWED_TAGS: ["p", "br", "b", "i", "em", "strong", "a", "ul", "ol", "li", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "code", "pre", "span", "div", "table", "thead", "tbody", "tr", "th", "td", "img", "figure", "figcaption", "hr", "sub", "sup"],
        ALLOWED_ATTR: ["href", "src", "alt", "title", "class", "target", "rel", "width", "height"],
        ALLOW_DATA_ATTR: false
      });
    }
    const doc = new DOMParser().parseFromString(html, "text/html");
    doc.querySelectorAll("script, iframe, object, embed, style, form, input, button, link").forEach((el) => el.remove());
    doc.body.querySelectorAll("*").forEach((el) => {
      for (const attr of Array.from(el.attributes)) {
        if (attr.name.toLowerCase().startsWith("on") || attr.value.toLowerCase().startsWith("javascript:")) {
          el.removeAttribute(attr.name);
        }
      }
    });
    return doc.body.innerHTML;
  }

  function ticker(msg) {
    const el = document.getElementById("activity-ticker");
    if (el) el.textContent = `${new Date().toLocaleTimeString(UI_LOCALE, { hour12: false })} · ${msg}`;
  }

  function formatTimestamp(ts) {
    if (!ts) return tr("synd.kb.time.now");
    const num = Number(ts);
    if (!Number.isFinite(num) || num <= 0) return String(ts);
    const diff = Math.max(0, Math.floor(Date.now() / 1000 - num));
    if (diff < 60) return tr("synd.kb.time.now");
    if (diff < 3600) return tr("synd.kb.time.minutes", { count: Math.floor(diff / 60) });
    if (diff < 86400) return tr("synd.kb.time.hours", { count: Math.floor(diff / 3600) });
    return new Date(num * 1000).toLocaleDateString(UI_LOCALE);
  }

  function getActiveContentTab(doc) {
    if (state.contentTabByDoc[doc.id]) return state.contentTabByDoc[doc.id];
    if (prefs.defaultContentTab === "original") return "original";
    return "ingested";
  }

  function getVisibleDocuments() {
    const theme = curTheme();
    if (state.treeSel.kind === "virtual" && state.treeSel.value === "dispatches") return [];
    const q = state.searchQuery.trim().toLowerCase();
    const list = (theme.documents || []).filter((doc) => {
      if (state.pubFilter !== "all" && state.publishedKeys.has(`${theme.id}:${doc.id}`) !== (state.pubFilter === "published")) return false;
      const s = state.treeSel;
      if (s.kind === "virtual") {
        if (s.value === "unread" && !doc.unread) return false;
        if (s.value === "starred" && !doc.starred) return false;
        if (s.value === "syndicated" && !doc.syndicationMeta && !doc.dispatchedCount) return false;
      } else if (s.kind === "source") {
        if ((doc.sourceType || "web") !== s.value) return false;
      } else if (s.kind === "entity") {
        const target = s.value.toLowerCase();
        const hasEnt = (doc.entities || []).some((e) => String(e.name || "").toLowerCase() === target);
        const inTitleOrBody = `${doc.title} ${doc.summary} ${doc.rawText}`.toLowerCase().includes(target);
        if (!hasEnt && !inTitleOrBody) return false;
      }
      if (q) {
        const entStr = (doc.entities || []).map((e) => e.name).join(" ");
        const tagStr = (doc.sourceTags || []).join(" ");
        const hay = `${doc.title} ${doc.author} ${doc.summary} ${doc.rawText} ${entStr} ${tagStr}`.toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });
    return list.sort((a, b) => {
      if (state.sortBy === "oldest") return (a.timestamp || 0) - (b.timestamp || 0);
      if (state.sortBy === "entities") return (b.entityCount || 0) - (a.entityCount || 0);
      return (b.timestamp || 0) - (a.timestamp || 0);
    });
  }

  function listStyleLabel(style) {
    const m = LIST_STYLE_META[style] || LIST_STYLE_META.list;
    return `${m.icon} ${i18n(m.label)}`;
  }

  function applyLayoutAttr() {
    document.body.setAttribute("data-layout", state.layoutMode);
    document.body.setAttribute("data-list-style", state.listStyle);

    const listBtn = document.getElementById("btn-list-style-label");
    if (listBtn) listBtn.textContent = listStyleLabel(state.listStyle);
    document.querySelectorAll("[data-set-list-style]").forEach((btn) => {
      const style = btn.getAttribute("data-set-list-style");
      const on = style === state.listStyle;
      btn.setAttribute("aria-checked", on ? "true" : "false");
      const lbl = btn.querySelector("[data-list-style-label]");
      if (lbl) lbl.textContent = `${on ? "✓ " : "　"}${listStyleLabel(style)}`;
      const desc = btn.querySelector("[data-list-style-desc]");
      if (desc && LIST_STYLE_META[style]) desc.textContent = i18n(LIST_STYLE_META[style].desc);
    });

    const layoutBtn = document.getElementById("btn-layout-label");
    if (layoutBtn) layoutBtn.textContent = state.layoutMode === "three-panel" ? `◫ ${tr("synd.kb.layout.three")}` : `⬒ ${tr("synd.kb.layout.two")}`;
    const menu3p = document.getElementById("menu-layout-3pane-label");
    if (menu3p) menu3p.textContent = `${state.layoutMode === "three-panel" ? "✓ " : "　"}${tr("synd.kb.layout.three_long")}`;
    const menu2p = document.getElementById("menu-layout-2pane-label");
    if (menu2p) menu2p.textContent = `${state.layoutMode === "two-panel" ? "✓ " : "　"}${tr("synd.kb.layout.two_long")}`;

    const cur = THEMES[String(state.themeId)];
    const kbQuery = cur && !cur.isPersonal && String(state.themeId) !== "0" ? `?kb=${encodeURIComponent(themeRefOf(state.themeId))}` : "";
    document.querySelectorAll("a[data-settings-path]").forEach((a) => {
      a.href = `${a.getAttribute("data-settings-path")}${kbQuery}`;
    });
  }

  function renderTopBar() {
    const sel = document.getElementById("theme-selector");
    if (sel) {
      sel.innerHTML = Object.values(THEMES).map((t) => `<option value="${esc(t.id)}" ${String(t.id) === String(state.themeId) ? "selected" : ""}>${esc(t.icon)} ${esc(t.label)}</option>`).join("");
      // The same rule as in the workspace: the picker shows only when there is more than one theme.
      const wrap = sel.closest(".theme-picker-wrap");
      if (wrap) wrap.hidden = Object.keys(THEMES).length <= 1;
    }
    const streamTitle = document.getElementById("active-stream-title");
    if (streamTitle) streamTitle.textContent = state.treeSel.label;
    const cntEl = document.getElementById("selected-count-label");
    if (cntEl) cntEl.textContent = state.checkedIds.size > 0 ? tr("synd.kb.selected", { count: state.checkedIds.size }) : "";
    const sortEl = document.getElementById("btn-sort-label");
    if (sortEl) sortEl.textContent = tr("synd.kb.sort." + (["newest", "oldest", "entities"].includes(state.sortBy) ? state.sortBy : "newest"));
  }

  function renderTree() {
    const theme = curTheme();
    const docs = theme.documents || [];
    const unreadCnt = docs.filter((d) => d.unread).length;
    const starredCnt = docs.filter((d) => d.starred).length;
    const dispatchCnt = (theme.dispatches || []).length;

    const specials = [
      { v: "all", icon: ic("inbox"), label: tr("synd.kb.stream.all"), cnt: docs.length, fresh: false },
      { v: "unread", icon: ic("dot"), label: tr("synd.kb.stream.unread"), cnt: unreadCnt, fresh: unreadCnt > 0 },
      { v: "starred", icon: "★", label: tr("synd.kb.stream.starred"), cnt: starredCnt, fresh: false },
      { v: "dispatches", icon: ic("broadcast"), label: tr("synd.kb.stream.dispatches"), cnt: dispatchCnt, fresh: false }
    ];
    const specEl = document.getElementById("tree-specials");
    if (specEl) {
      specEl.innerHTML = specials.map((s) => {
        const act = state.treeSel.kind === "virtual" && state.treeSel.value === s.v ? "active" : "";
        return `<button type="button" class="tt-tree-item ${act}" data-tree-kind="virtual" data-tree-val="${esc(s.v)}" data-tree-lbl="${esc(s.label)}"><span>${s.icon} ${esc(s.label)}</span><span class="tt-count-pill ${s.fresh ? "fresh" : ""}">${s.cnt}</span></button>`;
      }).join("");
    }

    const sourceCounts = {};
    docs.forEach((d) => {
      const k = d.sourceType || "web";
      sourceCounts[k] = (sourceCounts[k] || 0) + 1;
    });
    const srcEl = document.getElementById("tree-sources");
    if (srcEl) {
      const entries = Object.entries(sourceCounts);
      srcEl.innerHTML = entries.length
        ? entries.map(([srcType, cnt]) => {
            const act = state.treeSel.kind === "source" && state.treeSel.value === srcType ? "active" : "";
            return `<button type="button" class="tt-tree-item ${act}" data-tree-kind="source" data-tree-val="${esc(srcType)}" data-tree-lbl="${esc(tr("synd.kb.source_label", { type: srcType.toUpperCase() }))}"><span>${ic("folder")} ${esc(srcType.toUpperCase())}</span><span class="tt-count-pill">${cnt}</span></button>`;
          }).join("")
        : `<div style="padding:6px 10px;font-size:11.5px;color:var(--muted)">${esc(tr("synd.kb.empty.documents"))}</div>`;
    }

    const entEl = document.getElementById("tree-entities");
    if (entEl) {
      const ents = (theme.entities || []).slice(0, 20);
      entEl.innerHTML = ents.length
        ? ents.map((ent) => {
            const act = state.treeSel.kind === "entity" && state.treeSel.value === ent.name ? "active" : "";
            return `<button type="button" class="tt-tree-item ${act}" data-tree-kind="entity" data-tree-val="${esc(ent.name)}" data-tree-lbl="[[${esc(ent.name)}]]"><span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">[[${esc(ent.name)}]] <small style="color:var(--muted)">(${esc(ent.type)})</small></span><span class="tt-count-pill">${ent.count || 1}</span></button>`;
          }).join("")
        : `<div style="padding:6px 10px;font-size:11.5px;color:var(--muted)">${esc(tr("synd.kb.empty.entities"))}</div>`;
    }

    document.querySelectorAll("[data-tree-kind]").forEach((btn) => {
      btn.addEventListener("click", () => {
        state.treeSel = {
          kind: btn.getAttribute("data-tree-kind"),
          value: btn.getAttribute("data-tree-val"),
          label: btn.getAttribute("data-tree-lbl")
        };
        renderAll();
      });
    });
  }

  function buildReaderHtml(doc) {
    const theme = curTheme();
    const activeTab = getActiveContentTab(doc);
    const fontCls = prefs.readerFont === "serif" ? "font-serif" : "";

    let bodySection = "";
    if (activeTab === "original") {
      const rawText = doc.rawText || doc.summary || doc.title || "";
      const safeRaw = sanitizeContentHtml(`<p>${esc(rawText).replace(/\n\n+/g, "</p><p>").replace(/\n/g, "<br/>")}</p>`);
      bodySection = `<div class="tt-reader-body ${fontCls}" style="--read-fs:${prefs.readerFontSize}px">
        <div class="tt-summary-callout"><strong>${ic("doc")} ${esc(tr("synd.kb.reader.original_title"))}</strong> — ${esc(tr("synd.kb.reader.original_note"))}</div>
        <article class="doc-content">${safeRaw}</article>
      </div>`;
    } else {
      const safeDetail = sanitizeContentHtml(doc.detailHtml || `<p>${esc(doc.summary || doc.title || "")}</p>`);
      const syndNote = doc.syndicationMeta
        ? ` · ${esc(tr("synd.kb.reader.cross_source", { theme: doc.syndicationMeta.source_kb_id, document: doc.syndicationMeta.source_document_id }))}`
        : "";
      bodySection = `<div class="tt-reader-body ${fontCls}" style="--read-fs:${prefs.readerFontSize}px">
        <div class="tt-summary-callout"><strong>${ic("pin")} ${esc(tr("synd.kb.reader.summary_title", { id: doc.id }))}</strong> — ${esc(doc.summary || doc.title)}<div style="margin-top:4px;font-size:11.5px;color:var(--muted)">${esc(tr("synd.kb.reader.summary_meta", { time: doc.timeAgo, entities: doc.entityCount || 0, relations: doc.relationCount || 0 }))}${syndNote}</div></div>
        <article class="doc-content">${safeDetail}</article>
      </div>`;
    }

    const entities = doc.entities || [];
    const entChips = entities.map((ent) => `<button type="button" class="tt-entity-chip resolved" data-filter-entity="${esc(ent.name)}" title="${esc(tr("synd.kb.entity_filter_title"))}">[[${esc(ent.name)}]] <small style="opacity:0.8">(${esc(ent.type || "Concept")})</small></button>`).join("");
    const sourceTags = doc.sourceTags || [];
    const tagChips = sourceTags.map((tag) => `<span class="tt-entity-chip candidate">#${esc(tag)}</span>`).join("");

    const bottomMetaBar = `<footer class="tt-entity-bar" aria-label="${esc(tr("synd.kb.reader.meta_bar"))}">
      <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap">
        <span style="font-weight:700;color:var(--accent)">${ic("link")} ${esc(tr("synd.kb.reader.entities_label"))}:</span>
        ${entChips || `<span style="color:var(--muted)">${esc(tr("synd.kb.reader.no_entities"))}</span>`}
      </div>
      ${sourceTags.length ? `<div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;margin-top:4px"><span style="font-weight:700;color:var(--muted)">${ic("tag")} ${esc(tr("synd.kb.reader.tags_label"))}:</span>${tagChips}</div>` : ""}
    </footer>`;

    return `<div class="tt-article-card" data-reader-for="${esc(doc.id)}">
      <header class="tt-art-header">
        <div class="tt-art-title-row">
          <h2 class="tt-art-title"><a href="${esc(doc.url)}" target="_blank" rel="noopener noreferrer">${esc(doc.title)}</a></h2>
          <div style="display:inline-flex;align-items:center;gap:4px;flex-shrink:0">
            <button type="button" class="tt-btn" data-font-delta="-1" title="${esc(tr("synd.kb.reader.font_smaller"))}">A-</button>
            <button type="button" class="tt-btn" data-font-delta="1" title="${esc(tr("synd.kb.reader.font_larger"))}">A+</button>
            <button type="button" class="tt-btn" data-toggle-serif="1" title="${esc(tr("synd.kb.reader.font_toggle"))}">${esc(tr(prefs.readerFont === "serif" ? "synd.kb.reader.font_serif" : "synd.kb.reader.font_sans"))}</button>
            <button type="button" class="tt-btn" popovertarget="pop-article-actions" data-art-menu-doc="${esc(doc.id)}" title="${esc(tr("synd.kb.reader.more_actions"))}">⋯</button>
          </div>
        </div>
        <div class="tt-art-meta">
          <span><strong>${esc(theme.label)}</strong> · ${esc(tr("synd.kb.reader.author"))}: ${esc(doc.author)} · ${esc(doc.timeAgo)} · ${esc(tr("synd.kb.reader.type"))}: <code>${esc((doc.sourceType || "web").toUpperCase())}</code></span>
          <span style="display:inline-flex;gap:10px;align-items:center">
            <a href="${esc(doc.url)}" target="_blank" rel="noopener noreferrer" style="color:var(--accent);text-decoration:none;font-weight:600">${esc(tr("synd.kb.reader.open_source"))} ↗</a>
          </span>
        </div>
      </header>

      <div class="tt-view-switcher" role="region" aria-label="${esc(tr("synd.kb.reader.body_switch"))}">
        <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
          <div class="tt-seg" role="group" aria-label="${esc(tr("synd.kb.reader.body_mode"))}">
            <button type="button" class="tt-seg-btn ${activeTab === "original" ? "active" : ""}" data-switch-body="${esc(doc.id)}" data-target-tab="original">${ic("doc")} ${esc(tr("synd.kb.reader.tab_original"))}</button>
            <button type="button" class="tt-seg-btn ${activeTab === "ingested" ? "active" : ""}" data-switch-body="${esc(doc.id)}" data-target-tab="ingested">${ic("book")} ${esc(tr("synd.kb.reader.tab_ingested"))}</button>
          </div>
          <span class="tt-link-dot linked">${ic("link")} ${esc(doc.id)}</span>
        </div>
        <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap">
          <button type="button" class="tt-btn tt-btn-pri" data-open-dispatch="${esc(doc.id)}" title="${esc(tr("synd.kb.dispatch.action_title"))}">${ic("broadcast")} ${esc(tr("synd.kb.dispatch.action"))}</button>
        </div>
      </div>

      ${bodySection}
      ${bottomMetaBar}
    </div>`;
  }

  function renderDispatchesStream(container, readerPane, theme) {
    const dispatches = theme.dispatches || [];
    if (!dispatches.length) {
      container.innerHTML = `<div style="padding:42px 20px;text-align:center;color:var(--muted);font-size:13px">${esc(tr("synd.kb.dispatch.history_empty"))}</div>`;
      readerPane.innerHTML = `<div style="padding:36px;color:var(--muted);font-size:13px">${esc(tr("synd.kb.dispatch.history_hint"))}</div>`;
      return;
    }
    container.innerHTML = dispatches.map((d) => {
      const statusBadge = d.status === "done"
        ? `<span class="tt-link-dot linked">● ${esc(tr("synd.kb.dispatch.done"))}</span>`
        : `<span class="tt-link-dot unlinked">● ${esc(d.status || "pending")}</span>`;
      return `<article class="tt-card-row" data-dispatch-doc="${esc(d.document_id || "")}">
        <div class="tt-card-topbar">
          <div class="tt-card-meta-left">
            ${statusBadge}
            <span class="tt-feed-pill proto-rss">${esc(d.mode || "cross_theme")}</span>
            <span class="tt-card-feed-title">Theme #${esc(d.kb_id)} → Theme #${esc(d.target_kb_id)}</span>
          </div>
          <div class="tt-card-meta-right">
            <span class="tt-card-time">${esc(formatTimestamp(d.created_at))}</span>
          </div>
        </div>
        <h3 class="tt-card-title">${esc(tr("synd.kb.dispatch.source_doc"))}: ${esc(d.document_id)} → ${esc(tr("synd.kb.dispatch.target_doc"))}: ${esc(d.remote_ref || "-")}</h3>
        <p class="tt-card-snippet">${d.error ? `${esc(tr("synd.kb.error"))}: ${esc(d.error)}` : ""}</p>
      </article>`;
    }).join("");
    readerPane.innerHTML = `<div style="padding:36px;color:var(--muted);font-size:13px">${esc(tr("synd.kb.dispatch.history_count", { count: dispatches.length }))}</div>`;
  }

  function renderStreamAndReader() {
    const theme = curTheme();
    const container = document.getElementById("headlines-container");
    const readerPane = document.getElementById("reader-content");
    if (!container || !readerPane) return;

    if (state.treeSel.kind === "virtual" && state.treeSel.value === "dispatches") {
      renderDispatchesStream(container, readerPane, theme);
      const footer = document.getElementById("footer-stats");
      if (footer) footer.textContent = tr("synd.kb.footer.dispatches", { id: theme.id, count: theme.dispatches.length });
      return;
    }

    const visible = getVisibleDocuments();
    if (state.loading && !theme.loaded) {
      container.innerHTML = `<div style="padding:42px 20px;text-align:center;color:var(--muted);font-size:13px">${esc(tr("synd.kb.loading"))}</div>`;
    } else if (visible.length === 0) {
      container.innerHTML = `<div style="padding:42px 20px;text-align:center;color:var(--muted);font-size:13px">${esc(tr("synd.kb.empty.stream"))}</div>`;
    } else {
      container.innerHTML = visible.map((doc) => {
        const isSel = state.selectedDocId === doc.id;
        const checked = state.checkedIds.has(doc.id) ? "checked" : "";
        const srcLabel = (doc.sourceType || "web").toUpperCase();

        if (state.listStyle !== "list") {
          const entChips = (doc.entities || []).slice(0, 6).map((e) => `<span class="tt-entity-chip resolved">[[${esc(e.name)}]]</span>`).join("");
          let summaryHtml = "";
          if (state.listStyle === "card") {
            summaryHtml = `<p class="tt-card-snippet">${esc(doc.summary || doc.rawText || "")}</p>`;
          } else if (state.listStyle === "roomy") {
            summaryHtml = `<p class="tt-card-snippet is-full">${esc(doc.summary || doc.rawText || "")}</p>`;
          }

          return `<article class="tt-card-row ${doc.unread ? "is-unread" : ""} ${isSel ? "is-selected" : ""}" data-card-id="${esc(doc.id)}">
            <div class="tt-card-topbar">
              <div class="tt-card-meta-left">
                <input type="checkbox" class="tt-row-check" data-check-id="${esc(doc.id)}" ${checked} aria-label="${esc(tr("synd.kb.row.select", { title: doc.title }))}"/>
                <button type="button" class="tt-icon-btn ${doc.starred ? "starred" : ""}" data-star-id="${esc(doc.id)}" title="${esc(tr("synd.kb.row.star"))}">${doc.starred ? "★" : "☆"}</button>
                <span class="tt-feed-pill proto-rss">${esc(srcLabel)}</span>
                <span class="tt-card-feed-title">${esc(doc.author)}</span>
              </div>
              <div class="tt-card-meta-right">
                ${reactionBadge(doc)}
                <span class="tt-link-dot linked">${ic("link")} ${esc(doc.id)}</span>
                <span class="tt-card-time">${esc(doc.timeAgo)}</span>
              </div>
            </div>
            <h3 class="tt-card-title">${esc(doc.title)}</h3>
            ${summaryHtml}
            <div class="tt-card-bottom">
              <div class="tt-card-tags">${entChips || `<span style="font-size:11px;color:var(--muted)">${esc(tr("synd.kb.row.entity_count", { count: doc.entityCount || 0 }))}</span>`}</div>
              <div class="tt-card-actions">
                <button type="button" data-quick-dispatch="${esc(doc.id)}" class="tt-btn" style="height:23px;padding:0 7px;font-size:11px">${ic("broadcast")} ${esc(tr("synd.kb.row.dispatch_short"))}</button>
                <a class="tt-ext-link" href="${esc(doc.url)}" target="_blank" rel="noopener noreferrer" title="${esc(tr("synd.kb.row.open_new_tab"))}">↗</a>
              </div>
            </div>
          </article>`;
        }

        const excerptHtml = prefs.showExcerpt ? `<span class="tt-flat-excerpt">— ${esc(doc.summary || doc.rawText || "")}</span>` : "";
        return `<div class="tt-row-wrap ${doc.unread ? "is-unread" : ""} ${isSel ? "is-selected" : ""}" data-row-id="${esc(doc.id)}">
          <div class="tt-flat-row" data-click-row="${esc(doc.id)}">
            <input type="checkbox" class="tt-row-check" data-check-id="${esc(doc.id)}" ${checked} aria-label="${esc(tr("synd.kb.row.select", { title: doc.title }))}"/>
            <button type="button" class="tt-icon-btn ${doc.starred ? "starred" : ""}" data-star-id="${esc(doc.id)}" title="${esc(tr("synd.kb.row.star"))}">${doc.starred ? "★" : "☆"}</button>
            <button type="button" class="tt-icon-btn" data-quick-dispatch="${esc(doc.id)}" title="${esc(tr("synd.kb.dispatch.action"))}">${ic("broadcast")}</button>
            <span class="tt-link-dot linked" title="${esc(tr("synd.kb.row.ingested_title"))}">${ic("link")} ${esc(tr("synd.kb.row.ingested"))}</span>
            ${reactionBadge(doc)}
            <div class="tt-flat-main">
              <span class="tt-flat-title">${esc(doc.title)}</span>
              <span class="tt-flat-author">${esc(doc.author)}</span>
              ${excerptHtml}
            </div>
            <div class="tt-flat-right">
              <span class="tt-feed-pill proto-rss">${esc(srcLabel)}</span>
              <span class="tt-flat-time">${esc(doc.timeAgo)}</span>
              <a class="tt-ext-link" href="${esc(doc.url)}" target="_blank" rel="noopener noreferrer" title="${esc(tr("synd.kb.row.open_source"))}">→</a>
            </div>
          </div>
        </div>`;
      }).join("");
    }

    const selDoc = (theme.documents || []).find((x) => x.id === state.selectedDocId) || visible[0];
    if (selDoc && state.selectedDocId !== selDoc.id) {
      state.selectedDocId = selDoc.id;
      hydrateLiveDocument(selDoc);
    }
    readerPane.innerHTML = selDoc
      ? buildReaderHtml(selDoc)
      : `<div style="padding:36px;color:var(--muted);font-size:13px">${esc(tr("synd.kb.reader.empty"))}</div>`;

    const footer = document.getElementById("footer-stats");
    if (footer) {
      footer.textContent = tr("synd.kb.footer.summary", { id: theme.id, label: theme.label, documents: theme.documents.length, entities: theme.entities.length, dispatches: theme.dispatches.length });
    }
    bindStreamAndReaderEvents();
  }

  async function togglePinDocument(doc) {
    if (!doc) return;
    const nextPinned = !doc.starred;
    doc.starred = nextPinned;
    renderAll();
    try {
      await fetch(kbPath(state.themeId, "/document/pin"), {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({ id: doc.id, pinned: nextPinned })
      });
    } catch (_) {}
  }

  async function markDocumentSeen(doc) {
    if (!doc || !doc.unread) return;
    doc.unread = false;
    try {
      await fetch(kbPath(state.themeId, "/document/seen"), {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({ id: doc.id })
      });
    } catch (_) {}
  }

  /* 네이티브 연합 액터 (Fedify 사이드카 설계 §9.2): 이 계정이 연합 액터일 때만 발행 대상으로 보인다. 단독 모드에서는 상태 경로가 404 이고, 연합 off · 일시 중지에서는 is_actor 가 false 이므로 null 로 남는다. */
  let nativeActor = null;
  (async () => {
    try {
      const resp = await fetch("/api/federation/status", { headers: { Accept: "application/json" } });
      if (!resp.ok) return;
      const st = await resp.json();
      if (st && st.is_actor && st.handle) nativeActor = { handle: String(st.handle) };
    } catch (_) {}
  })();

  function openDispatchModal(docId) {
    const theme = curTheme();
    const doc = (theme.documents || []).find((x) => x.id === docId);
    if (!doc) return;
    const modal = document.getElementById("modal-dispatch");
    const body = document.getElementById("modal-dispatch-body");
    if (!modal || !body) return;

    const allThemes = Object.values(THEMES);
    const targetOptions = allThemes.map((t) => `<option value="${esc(t.id)}" ${String(t.id) !== String(theme.id) ? "selected" : ""}>${esc(t.icon)} ${esc(t.label)} (#${esc(t.id)})</option>`).join("");

    body.innerHTML = `
      <div style="display:flex;justify-content:space-between;align-items:center">
        <h2 id="modal-dispatch-title" style="margin:0;font-size:16px;font-weight:700">${ic("broadcast")} ${esc(tr("synd.kb.dispatch.dialog_title"))}</h2>
        <button type="button" id="btn-close-dispatch" class="tt-btn">✕</button>
      </div>
      <div style="font-size:12.5px;color:var(--muted)"><code>${esc(doc.id)}</code> · <strong>${esc(doc.title)}</strong><br/>${esc(tr("synd.kb.dispatch.dialog_note"))}</div>
      <div>
        <label for="dispatch-target-theme" style="display:block;font-size:12px;font-weight:700;margin-bottom:4px">${esc(tr("synd.kb.dispatch.target_theme"))}</label>
        <select id="dispatch-target-theme" class="tt-btn" style="width:100%;height:34px">${targetOptions}</select>
      </div>
      <div id="dispatch-status-msg" style="font-size:12px;color:var(--muted)"></div>
      <div style="display:flex;justify-content:flex-end;gap:8px;padding-top:10px;border-top:1px solid var(--border)">
        <button type="button" id="btn-cancel-dispatch" class="tt-btn">${esc(tr("common.action.cancel"))}</button>
        <button type="button" id="btn-confirm-dispatch" class="tt-btn tt-btn-pri">${ic("broadcast")} ${esc(tr("synd.kb.dispatch.confirm"))}</button>
      </div>
      ${nativeActor ? `<fieldset id="dispatch-native" style="border:1px solid var(--border);border-radius:8px;padding:10px 12px;display:flex;flex-direction:column;gap:6px">
        <legend style="font-size:12px;font-weight:700;padding:0 4px">${ic("home")} ${esc(tr("synd.kb.publish.action"))}</legend>
        <div style="font-size:12.5px">${esc(tr("synd.kb.publish.target"))}: <b data-native-handle>${esc(nativeActor.handle)}</b> · ${esc(tr("synd.kb.publish.format"))}</div>
        <div style="font-size:11.5px;color:var(--muted)">${esc(tr(theme.isPublic ? "synd.kb.publish.public_note" : "synd.kb.publish.private_note"))}</div>
        <div id="dispatch-native-msg" style="font-size:12px;color:var(--muted)"></div>
        <div style="display:flex;justify-content:flex-end"><button type="button" id="btn-native-publish" class="tt-btn tt-btn-pri" ${theme.isPublic ? "" : "disabled"}>${ic("home")} ${esc(tr("synd.kb.publish.action"))}</button></div>
      </fieldset>` : ""}
    `;

    const nativeBtn = document.getElementById("btn-native-publish");
    if (nativeBtn) {
      nativeBtn.addEventListener("click", async () => {
        const msgEl = document.getElementById("dispatch-native-msg");
        nativeBtn.disabled = true;
        if (msgEl) msgEl.textContent = tr("synd.kb.publish.sending");
        try {
          const resp = await fetch("/api/federation/publish", {
            method: "POST",
            headers: { "Content-Type": "application/json", Accept: "application/json" },
            body: JSON.stringify({ kb: themeRefOf(theme.id), document_id: doc.id })
          });
          const data = await resp.json().catch(() => ({}));
          if (resp.status !== 202 || !data.object) {
            const errText = data.error || `HTTP ${resp.status}`;
            if (msgEl) msgEl.textContent = tr("synd.kb.publish.failed", { error: errText });
            ticker(tr("synd.kb.publish.failed", { error: errText }));
            nativeBtn.disabled = false;
            return;
          }
          modal.close();
          ticker(tr(data.created ? "synd.kb.publish.created" : "synd.kb.publish.updated", { id: doc.id, handle: nativeActor.handle }));
        } catch (err) {
          if (msgEl) msgEl.textContent = tr("synd.kb.network_error", { error: err.message || err });
          nativeBtn.disabled = false;
        }
      });
    }

    const closeBtn = document.getElementById("btn-close-dispatch");
    const cancelBtn = document.getElementById("btn-cancel-dispatch");
    const confirmBtn = document.getElementById("btn-confirm-dispatch");
    if (closeBtn) closeBtn.addEventListener("click", () => modal.close());
    if (cancelBtn) cancelBtn.addEventListener("click", () => modal.close());

    if (confirmBtn) {
      confirmBtn.addEventListener("click", async () => {
        const targetThemeId = document.getElementById("dispatch-target-theme").value;
        const msgEl = document.getElementById("dispatch-status-msg");
        confirmBtn.disabled = true;
        if (msgEl) msgEl.textContent = tr("synd.kb.dispatch.sending");
        try {
          const resp = await fetch("/api/syndication/dispatch", {
            method: "POST",
            headers: { "Content-Type": "application/json", Accept: "application/json" },
            body: JSON.stringify({
              document_id: doc.id,
              source_kb_id: Number(theme.id),
              target_kb_id: Number(targetThemeId)
            })
          });
          const data = await resp.json().catch(() => ({}));
          if (!resp.ok || !data.ok) {
            const errText = data.detail || data.error || `HTTP ${resp.status}`;
            if (msgEl) msgEl.textContent = tr("synd.kb.dispatch.failed", { error: errText });
            ticker(tr("synd.kb.dispatch.failed", { error: errText }));
            confirmBtn.disabled = false;
            return;
          }
          modal.close();
          ticker(tr("synd.kb.dispatch.completed", { id: doc.id, theme: targetThemeId, target: data.target_document_id }));
          await syncLiveThemeDb();
        } catch (err) {
          if (msgEl) msgEl.textContent = tr("synd.kb.network_error", { error: err.message || err });
          confirmBtn.disabled = false;
        }
      });
    }

    modal.showModal();
  }

  function bindStreamAndReaderEvents() {
    document.querySelectorAll("[data-check-id]").forEach((chk) => {
      chk.addEventListener("click", (e) => e.stopPropagation());
      chk.addEventListener("change", (e) => {
        const id = chk.getAttribute("data-check-id");
        if (e.target.checked) state.checkedIds.add(id); else state.checkedIds.delete(id);
        renderTopBar();
      });
    });

    document.querySelectorAll("[data-star-id]").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        const doc = (curTheme().documents || []).find((x) => x.id === btn.getAttribute("data-star-id"));
        togglePinDocument(doc);
      });
    });

    document.querySelectorAll("[data-quick-dispatch], [data-open-dispatch]").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        const id = btn.getAttribute("data-quick-dispatch") || btn.getAttribute("data-open-dispatch");
        openDispatchModal(id);
      });
    });

    document.querySelectorAll(".tt-ext-link").forEach((a) => a.addEventListener("click", (e) => e.stopPropagation()));

    document.querySelectorAll("[data-card-id], [data-click-row]").forEach((el) => {
      el.addEventListener("click", (e) => {
        if (e.target.closest("button") || e.target.closest("input") || e.target.closest("a")) return;
        const id = el.getAttribute("data-card-id") || el.getAttribute("data-click-row");
        const doc = (curTheme().documents || []).find((x) => x.id === id);
        state.selectedDocId = id;
        if (doc && prefs.autoMarkReadOnOpen) markDocumentSeen(doc);
        hydrateLiveDocument(doc);
        renderAll();
      });
    });

    document.querySelectorAll("[data-switch-body]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const id = btn.getAttribute("data-switch-body");
        state.contentTabByDoc[id] = btn.getAttribute("data-target-tab");
        renderStreamAndReader();
      });
    });

    document.querySelectorAll("[data-font-delta]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const delta = Number(btn.getAttribute("data-font-delta")) || 0;
        prefs.readerFontSize = Math.min(22, Math.max(13, (prefs.readerFontSize || 15) + delta));
        savePrefs();
        renderStreamAndReader();
      });
    });

    document.querySelectorAll("[data-toggle-serif]").forEach((btn) => {
      btn.addEventListener("click", () => {
        prefs.readerFont = prefs.readerFont === "serif" ? "sans" : "serif";
        savePrefs();
        renderStreamAndReader();
      });
    });

    document.querySelectorAll("[data-filter-entity]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const entName = btn.getAttribute("data-filter-entity");
        state.treeSel = { kind: "entity", value: entName, label: `[[${entName}]]` };
        renderAll();
      });
    });

    document.querySelectorAll("[data-art-menu-doc]").forEach((btn) => {
      btn.addEventListener("click", () => {
        state.activeArticleMenuDocId = btn.getAttribute("data-art-menu-doc");
      });
    });
  }

  async function hydrateLiveDocument(doc) {
    if (!doc || doc.hydrated) return;
    try {
      const resp = await fetch(`${kbPath(state.themeId, "/document")}?id=${encodeURIComponent(doc.id)}`, { headers: { Accept: "application/json" } });
      if (!resp.ok) return;
      const d = await resp.json();
      doc.hydrated = true;
      if (d.raw_text) doc.rawText = d.raw_text;
      if (d.detail_html) doc.detailHtml = d.detail_html;
      else if (d.detail || d.summary) doc.detailHtml = `<p>${esc(d.detail || d.summary)}</p>`;
      if (Array.isArray(d.nodes) && d.nodes.length) {
        doc.entities = d.nodes.map((n) => ({
          name: String(n.label || n.name || n.id || "").trim(),
          type: n.group || n.type || "Concept"
        })).filter((e) => e.name);
        doc.entityCount = doc.entities.length;
      }
      if (d.meta && typeof d.meta === "object") {
        if (Array.isArray(d.meta.tags)) doc.sourceTags = d.meta.tags.map(String);
        if (d.meta.syndication) doc.syndicationMeta = d.meta.syndication;
      }
      renderStreamAndReader();
    } catch (_) {}
  }

  async function syncLiveThemeDb() {
    const theme = curTheme();
    const ref = themeRefOf(state.themeId);
    state.loading = true;
    renderStreamAndReader();
    try {
      const [gResp, dResp, dispResp] = await Promise.all([
        fetch(kbPath(state.themeId, "/graph"), { headers: { Accept: "application/json" } }),
        fetch(kbPath(state.themeId, "/documents"), { headers: { Accept: "application/json" } }),
        fetch(`/api/syndication/dispatches?kb_id=${encodeURIComponent(theme.id)}`, { headers: { Accept: "application/json" } })
      ]);

      if (gResp.ok) {
        const g = await gResp.json();
        theme.entities = (g.nodes || []).map((n) => ({
          name: String(n.label || n.name || "").trim(),
          type: n.group || n.type || "Concept",
          count: n.degree || 1
        })).filter((e) => e.name);
      }

      if (dResp.ok) {
        const dJson = await dResp.json();
        const rawDocs = Array.isArray(dJson.documents) ? dJson.documents : (Array.isArray(dJson.items) ? dJson.items : []);
        theme.documents = rawDocs.map((doc) => ({
          id: doc.id,
          title: doc.title || doc.id,
          author: doc.author || "Claire DB",
          url: doc.url || `/p?id=${encodeURIComponent(doc.id)}`,
          sourceType: doc.source_type || "web",
          timestamp: Number(doc.fetched_at || 0),
          timeAgo: formatTimestamp(doc.fetched_at),
          unread: !doc.seen,
          starred: !!doc.pinned,
          summary: doc.summary || doc.title || "",
          rawText: doc.raw_text || doc.summary || "",
          detailHtml: doc.detail_html || `<p>${esc(doc.summary || doc.title || "")}</p>`,
          entityCount: Number(doc.entity_count || 0),
          relationCount: Number(doc.relation_count || 0),
          entities: [],
          sourceTags: [],
          syndicationMeta: null,
          hydrated: false
        }));
      }

      if (dispResp.ok) {
        const dispJson = await dispResp.json();
        theme.dispatches = Array.isArray(dispJson.dispatches) ? dispJson.dispatches : [];
      }

      theme.loaded = true;
      if (!state.selectedDocId && theme.documents.length > 0) {
        state.selectedDocId = theme.documents[0].id;
      }
      const sel = theme.documents.find((d) => d.id === state.selectedDocId);
      if (sel) hydrateLiveDocument(sel);
    } catch (_) {
      theme.loaded = true;
    } finally {
      state.loading = false;
    }
    renderAll();
  }

  function closeOpenPopovers() {
    document.querySelectorAll(".tt-popover").forEach((p) => {
      try { if (p.matches(":popover-open") || p.classList.contains(":popover-open")) p.hidePopover(); } catch (_) {}
    });
  }

  function setListStyle(style) {
    state.listStyle = normalizeListStyle(style);
    prefs.listStyle = state.listStyle;
    savePrefs();
    closeOpenPopovers();
    ticker(`${tr("synd.kb.list_style.title")}: ${listStyleLabel(state.listStyle)} — ${i18n(LIST_STYLE_META[state.listStyle].desc)}`);
    renderAll();
  }

  function cycleListStyle() {
    const i = ST.LIST_STYLES.indexOf(state.listStyle);
    setListStyle(ST.LIST_STYLES[(i + 1) % ST.LIST_STYLES.length]);
  }

  function toggleLayoutMode(forced) {
    if (forced) state.layoutMode = forced;
    else state.layoutMode = state.layoutMode === "three-panel" ? "two-panel" : "three-panel";
    prefs.layoutMode = state.layoutMode;
    savePrefs();
    closeOpenPopovers();
    ticker(`${tr("synd.kb.layout.title")}: ${tr(state.layoutMode === "three-panel" ? "synd.kb.layout.three_long" : "synd.kb.layout.two_long")}`);
    renderAll();
  }

  function bindGlobalControls() {
    document.querySelectorAll('dialog[closedby="any"]').forEach((dlg) => {
      if (!("closedBy" in HTMLDialogElement.prototype)) {
        dlg.addEventListener("click", (ev) => { if (ev.target === dlg) dlg.close(); });
      }
    });
    document.querySelectorAll("[data-close-dialog]").forEach((b) => {
      b.addEventListener("click", () => {
        const d = document.getElementById(b.getAttribute("data-close-dialog"));
        if (d) d.close();
      });
    });

    const themeSel = document.getElementById("theme-selector");
    if (themeSel) {
      themeSel.addEventListener("change", (e) => {
        selectTheme(e.target.value);
        syncLiveThemeDb();
      });
    }

    document.querySelectorAll("[data-set-list-style]").forEach((btn) => {
      btn.addEventListener("click", () => setListStyle(btn.getAttribute("data-set-list-style")));
    });

    const btnLayout = document.getElementById("btn-toggle-layout");
    if (btnLayout) btnLayout.addEventListener("click", () => toggleLayoutMode());

    const menuLayout3p = document.getElementById("menu-layout-3pane");
    if (menuLayout3p) menuLayout3p.addEventListener("click", () => toggleLayoutMode("three-panel"));

    const menuLayout2p = document.getElementById("menu-layout-2pane");
    if (menuLayout2p) menuLayout2p.addEventListener("click", () => toggleLayoutMode("two-panel"));

    const btnRefresh = document.getElementById("btn-refresh");
    if (btnRefresh) btnRefresh.addEventListener("click", () => syncLiveThemeDb());

    const btnMarkRead = document.getElementById("btn-mark-read");
    if (btnMarkRead) {
      btnMarkRead.addEventListener("click", () => {
        const docs = curTheme().documents || [];
        const targets = state.checkedIds.size > 0 ? docs.filter((x) => state.checkedIds.has(x.id)) : getVisibleDocuments();
        targets.forEach((d) => markDocumentSeen(d));
        ticker(tr("synd.kb.marked_read", { count: targets.length }));
        renderAll();
      });
    }

    const searchInput = document.getElementById("stream-search");
    if (searchInput) {
      searchInput.addEventListener("input", (e) => {
        state.searchQuery = e.target.value;
        renderStreamAndReader();
      });
    }

    document.querySelectorAll("[data-sort]").forEach((btn) => {
      btn.addEventListener("click", () => {
        state.sortBy = btn.getAttribute("data-sort");
        closeOpenPopovers();
        renderAll();
      });
    });

    document.querySelectorAll("[data-art-cmd]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const cmd = btn.getAttribute("data-art-cmd");
        const doc = (curTheme().documents || []).find((x) => x.id === state.activeArticleMenuDocId);
        closeOpenPopovers();
        if (!doc) return;
        if (cmd === "open-url") window.open(doc.url, "_blank", "noopener");
        else if (cmd === "show-url") window.prompt(tr("synd.kb.url_prompt"), doc.url);
        else if (cmd === "toggle-unread") { markDocumentSeen(doc); renderAll(); }
        else if (cmd === "toggle-star") togglePinDocument(doc);
        else if (cmd === "dispatch") openDispatchModal(doc.id);
        // MD-3: the Markdown export of a knowledge base document. The response is a file (Content-Disposition: attachment).
        else if (cmd === "export-md") window.location.assign(`/document/export?id=${encodeURIComponent(doc.id)}&format=markdown&theme=${encodeURIComponent(themeRefOf(state.themeId))}`);
      });
    });

    const ingestModal = document.getElementById("modal-ingest");
    function openIngestModal() {
      closeOpenPopovers();
      if (ingestModal) ingestModal.showModal();
    }
    const btnOpenIngest = document.getElementById("btn-open-ingest");
    if (btnOpenIngest) btnOpenIngest.addEventListener("click", openIngestModal);
    const menuOpenIngest = document.getElementById("menu-open-ingest");
    if (menuOpenIngest) menuOpenIngest.addEventListener("click", openIngestModal);

    const formIngest = document.getElementById("form-ingest");
    if (formIngest) {
      formIngest.addEventListener("submit", async (e) => {
        e.preventDefault();
        const payloadEl = document.getElementById("ingest-payload");
        const submitBtn = document.getElementById("btn-submit-ingest");
        const payload = payloadEl ? payloadEl.value.trim() : "";
        if (!payload) return;
        if (submitBtn) submitBtn.disabled = true;
        ticker(tr("synd.kb.ingest.sending"));
        try {
          const resp = await fetch("/ingest", {
            method: "POST",
            headers: { "Content-Type": "application/json", Accept: "application/json" },
            body: JSON.stringify({ payload })
          });
          const data = await resp.json().catch(() => ({}));
          if (!resp.ok || data.error) {
            ticker(tr("synd.kb.ingest.failed", { error: data.detail || data.error || resp.status }));
          } else {
            if (ingestModal) ingestModal.close();
            if (payloadEl) payloadEl.value = "";
            if (focusEl) focusEl.value = "";
            if (data.document_id) state.selectedDocId = data.document_id;
            ticker(tr("synd.kb.ingest.completed", { id: data.document_id || "" }));
            await syncLiveThemeDb();
          }
        } catch (err) {
          ticker(tr("synd.kb.ingest.failed", { error: err.message || err }));
        } finally {
          if (submitBtn) submitBtn.disabled = false;
        }
      });
    }

    document.addEventListener("keydown", (e) => {
      if (e.target.matches("input, textarea, select") || document.querySelector("dialog[open]")) return;
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      // The keys below are for the knowledge base view. The federation client view (stage 1) has its own keys.
      if (document.body.getAttribute("data-stage") === "collect") return;

      if (e.key === "l" || e.key === "L") { cycleListStyle(); return; }
      if (e.key === "m" || e.key === "M") { toggleLayoutMode(); return; }
      if (e.key === "r" || e.key === "R") { syncLiveThemeDb(); return; }

      const vis = getVisibleDocuments();
      if (!vis.length) return;
      const idx = vis.findIndex((x) => x.id === state.selectedDocId);

      if (e.key === "j" || e.key === "k") {
        const nextIdx = e.key === "j" ? Math.min(vis.length - 1, idx + 1) : Math.max(0, idx - 1);
        const target = vis[nextIdx] || vis[0];
        state.selectedDocId = target.id;
        if (prefs.autoMarkReadOnOpen) markDocumentSeen(target);
        hydrateLiveDocument(target);
        renderAll();
      } else if (e.key === "v") {
        const cur = vis[idx >= 0 ? idx : 0];
        if (cur) {
          state.contentTabByDoc[cur.id] = getActiveContentTab(cur) === "original" ? "ingested" : "original";
          renderStreamAndReader();
        }
      } else if (e.key === "d") {
        const cur = vis[idx >= 0 ? idx : 0];
        if (cur) openDispatchModal(cur.id);
      } else if (e.key === "f") {
        const cur = vis[idx >= 0 ? idx : 0];
        if (cur) togglePinDocument(cur);
      }
    });
  }

  function renderAll() {
    applyLayoutAttr();
    renderTopBar();
    renderTree();
    renderStreamAndReader();
    if (typeof bridge.onRender === "function") bridge.onRender();
  }

  function selectTheme(themeKey) {
    state.themeId = String(themeKey);
    const nextPrefs = loadPrefs(state.themeId);
    Object.keys(prefs).forEach((k) => { delete prefs[k]; });
    Object.assign(prefs, nextPrefs);
    state.layoutMode = prefs.layoutMode === "two-panel" ? "two-panel" : "three-panel";
    state.listStyle = prefs.listStyle;
    state.treeSel = { kind: "virtual", value: "all", label: tr("synd.kb.stream.all") };
    state.checkedIds.clear();
    state.selectedDocId = null;
  }

  // The interface for the federation client view (syndication_client.js, FP4-5). The client and the knowledge base are two views of this page (design Section 1.3 rule 3).
  const bridge = {
    themes: THEMES,
    state,
    curTheme,
    themeRefOf,
    ticker,
    renderAll,
    reload: syncLiveThemeDb,
    openDispatchModal,
    // Shows one document of a theme in the knowledge base view.
    showDocument(themeKey, docId) {
      if (THEMES[String(themeKey)] && String(themeKey) !== String(state.themeId)) selectTheme(themeKey);
      state.selectedDocId = docId;
      return syncLiveThemeDb();
    },
    setPublishFilter(filter, publishedKeys) {
      state.pubFilter = filter;
      if (publishedKeys) state.publishedKeys = publishedKeys;
      renderAll();
    },
    onRender: null
  };
  window.ClaireSyndication = bridge;

  bindGlobalControls();
  renderAll();
  syncLiveThemeDb();
})();
