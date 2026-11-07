/* Claire Bible — Live Syndication & Cross-Theme Knowledge Workspace (/syndication) */
(function () {
  "use strict";

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
    list: { icon: "☰", label: { ko: "목록", en: "List" }, desc: { ko: "한 줄 행", en: "Single-line rows" } },
    compact: { icon: "≡", label: { ko: "조밀하게", en: "Compact" }, desc: { ko: "요약 표시 안 함", en: "No summary" } },
    card: { icon: "▤", label: { ko: "일반", en: "Card" }, desc: { ko: "요약 2줄 미리보기", en: "Two-line summary" } },
    roomy: { icon: "▭", label: { ko: "넓게", en: "Roomy" }, desc: { ko: "요약 전체 표시", en: "Full summary" } }
  };

  const UI_LANG = String(document.documentElement.lang || "ko").toLowerCase().startsWith("en") ? "en" : "ko";
  function i18n(entry) { return entry[UI_LANG] || entry.ko; }
  function esc(s) { return String(s || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;"); }

  const cfg = window.__CLAIRE_CONFIG || {};
  const RAW_THEMES = Array.isArray(cfg.themes) && cfg.themes.length
    ? cfg.themes
    : [{ id: 0, seq: 0, number: "0", label: "기본 지식베이스", icon: "📚", is_default: true, is_public: true }];

  const THEMES = {};
  RAW_THEMES.forEach((srv) => {
    const k = String(srv.id);
    THEMES[k] = {
      id: srv.id,
      seq: srv.seq != null ? srv.seq : srv.id,
      number: srv.number != null ? String(srv.number) : String(srv.id),
      icon: srv.icon || "📚",
      label: srv.label || `테마 #${srv.id}`,
      isPublic: srv.is_public !== false,
      defaultFocus: srv.default_focus || "",
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
    const reqTheme = urlParams.get("theme");
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
    treeSel: { kind: "virtual", value: "all", label: "전체 적재 문서 (All documents)" },
    sortBy: "newest",
    searchQuery: "",
    selectedDocId: null,
    checkedIds: new Set(),
    contentTabByDoc: {},
    activeArticleMenuDocId: null,
    loading: false
  };

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
    if (el) el.textContent = `${new Date().toLocaleTimeString("ko-KR", { hour12: false })} · ${msg}`;
  }

  function formatTimestamp(ts) {
    if (!ts) return "방금";
    const num = Number(ts);
    if (!Number.isFinite(num) || num <= 0) return String(ts);
    const diff = Math.max(0, Math.floor(Date.now() / 1000 - num));
    if (diff < 60) return "방금 전";
    if (diff < 3600) return `${Math.floor(diff / 60)}분 전`;
    if (diff < 86400) return `${Math.floor(diff / 3600)}시간 전`;
    return new Date(num * 1000).toLocaleDateString("ko-KR");
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
    if (layoutBtn) layoutBtn.textContent = state.layoutMode === "three-panel" ? "◫ 3단 분할" : "⬒ 2단 상하";
    const menu3p = document.getElementById("menu-layout-3pane-label");
    if (menu3p) menu3p.textContent = `${state.layoutMode === "three-panel" ? "✓ " : "　"}3단 분할 뷰 (좌-중-우)`;
    const menu2p = document.getElementById("menu-layout-2pane-label");
    if (menu2p) menu2p.textContent = `${state.layoutMode === "two-panel" ? "✓ " : "　"}2단 상하 분할 뷰 (좌-상하)`;

    const themeQuery = `?theme=${encodeURIComponent(themeRefOf(state.themeId))}`;
    document.querySelectorAll("a[data-settings-path]").forEach((a) => {
      a.href = `${a.getAttribute("data-settings-path")}${themeQuery}`;
    });
  }

  function renderTopBar() {
    const sel = document.getElementById("theme-selector");
    if (sel) {
      sel.innerHTML = Object.values(THEMES).map((t) => `<option value="${esc(t.id)}" ${String(t.id) === String(state.themeId) ? "selected" : ""}>${esc(t.icon)} ${esc(t.label)}</option>`).join("");
    }
    const streamTitle = document.getElementById("active-stream-title");
    if (streamTitle) streamTitle.textContent = state.treeSel.label;
    const cntEl = document.getElementById("selected-count-label");
    if (cntEl) cntEl.textContent = state.checkedIds.size > 0 ? `${state.checkedIds.size}개 문서 선택됨` : "";
    const sortEl = document.getElementById("btn-sort-label");
    if (sortEl) sortEl.textContent = ({ newest: "최신순", oldest: "오래된순", entities: "엔티티순" })[state.sortBy] || "최신순";
  }

  function renderTree() {
    const theme = curTheme();
    const docs = theme.documents || [];
    const unreadCnt = docs.filter((d) => d.unread).length;
    const starredCnt = docs.filter((d) => d.starred).length;
    const dispatchCnt = (theme.dispatches || []).length;

    const specials = [
      { v: "all", icon: "📥", label: "전체 적재 문서", cnt: docs.length, fresh: false },
      { v: "unread", icon: "🌱", label: "안읽음 문서", cnt: unreadCnt, fresh: unreadCnt > 0 },
      { v: "starred", icon: "★", label: "별표(고정) 문서", cnt: starredCnt, fresh: false },
      { v: "dispatches", icon: "📡", label: "교차 테마 전달 이력", cnt: dispatchCnt, fresh: false }
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
            return `<button type="button" class="tt-tree-item ${act}" data-tree-kind="source" data-tree-val="${esc(srcType)}" data-tree-lbl="출처: ${esc(srcType.toUpperCase())}"><span>🗂️ ${esc(srcType.toUpperCase())}</span><span class="tt-count-pill">${cnt}</span></button>`;
          }).join("")
        : `<div style="padding:6px 10px;font-size:11.5px;color:var(--muted)">적재된 문서 없음</div>`;
    }

    const entEl = document.getElementById("tree-entities");
    if (entEl) {
      const ents = (theme.entities || []).slice(0, 20);
      entEl.innerHTML = ents.length
        ? ents.map((ent) => {
            const act = state.treeSel.kind === "entity" && state.treeSel.value === ent.name ? "active" : "";
            return `<button type="button" class="tt-tree-item ${act}" data-tree-kind="entity" data-tree-val="${esc(ent.name)}" data-tree-lbl="[[${esc(ent.name)}]]"><span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">[[${esc(ent.name)}]] <small style="color:var(--muted)">(${esc(ent.type)})</small></span><span class="tt-count-pill">${ent.count || 1}</span></button>`;
          }).join("")
        : `<div style="padding:6px 10px;font-size:11.5px;color:var(--muted)">생성된 엔티티 없음</div>`;
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
        <div class="tt-summary-callout"><strong>📄 수집 원문 (Collected Original)</strong> — 적재 당시 보관된 원문 텍스트입니다.</div>
        <article class="doc-content">${safeRaw}</article>
      </div>`;
    } else {
      const safeDetail = sanitizeContentHtml(doc.detailHtml || `<p>${esc(doc.summary || doc.title || "")}</p>`);
      const syndNote = doc.syndicationMeta
        ? ` · 교차 테마 출처: Theme #${esc(doc.syndicationMeta.source_theme_id)} (${esc(doc.syndicationMeta.source_document_id)})`
        : "";
      bodySection = `<div class="tt-reader-body ${fontCls}" style="--read-fs:${prefs.readerFontSize}px">
        <div class="tt-summary-callout"><strong>📌 Claire 적재 요약 (${esc(doc.id)})</strong> — ${esc(doc.summary || doc.title)}<div style="margin-top:4px;font-size:11.5px;color:var(--muted)">적재 시각: ${esc(doc.timeAgo)} · 연결 엔티티: ${doc.entityCount || 0}개 · 관계: ${doc.relationCount || 0}개${syndNote}</div></div>
        <article class="doc-content">${safeDetail}</article>
      </div>`;
    }

    const entities = doc.entities || [];
    const entChips = entities.map((ent) => `<button type="button" class="tt-entity-chip resolved" data-filter-entity="${esc(ent.name)}" title="클릭하여 엔티티로 스트림 필터링">[[${esc(ent.name)}]] <small style="opacity:0.8">(${esc(ent.type || "Concept")})</small></button>`).join("");
    const sourceTags = doc.sourceTags || [];
    const tagChips = sourceTags.map((tag) => `<span class="tt-entity-chip candidate">#${esc(tag)}</span>`).join("");

    const bottomMetaBar = `<footer class="tt-entity-bar" aria-label="문서 엔티티 및 수집 태그 바">
      <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap">
        <span style="font-weight:700;color:var(--accent)">🔗 생성된 엔티티:</span>
        ${entChips || `<span style="color:var(--muted)">연결된 엔티티 없음</span>`}
      </div>
      ${sourceTags.length ? `<div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;margin-top:4px"><span style="font-weight:700;color:var(--muted)">🏷️ 수집 태그:</span>${tagChips}</div>` : ""}
    </footer>`;

    return `<div class="tt-article-card" data-reader-for="${esc(doc.id)}">
      <header class="tt-art-header">
        <div class="tt-art-title-row">
          <h2 class="tt-art-title"><a href="${esc(doc.url)}" target="_blank" rel="noopener noreferrer">${esc(doc.title)}</a></h2>
          <div style="display:inline-flex;align-items:center;gap:4px;flex-shrink:0">
            <button type="button" class="tt-btn" data-font-delta="-1" title="본문 글자 축소">A-</button>
            <button type="button" class="tt-btn" data-font-delta="1" title="본문 글자 확대">A+</button>
            <button type="button" class="tt-btn" data-toggle-serif="1" title="고딕/명조 서체 전환">${prefs.readerFont === "serif" ? "명조" : "고딕"}</button>
            <button type="button" class="tt-btn" popovertarget="pop-article-actions" data-art-menu-doc="${esc(doc.id)}" title="문서 추가 작업">⋯</button>
          </div>
        </div>
        <div class="tt-art-meta">
          <span><strong>${esc(theme.label)}</strong> · 작성자: ${esc(doc.author)} · ${esc(doc.timeAgo)} · 유형: <code>${esc((doc.sourceType || "web").toUpperCase())}</code></span>
          <span style="display:inline-flex;gap:10px;align-items:center">
            <a href="${esc(doc.url)}" target="_blank" rel="noopener noreferrer" style="color:var(--accent);text-decoration:none;font-weight:600">원문 링크 열기 ↗</a>
          </span>
        </div>
      </header>

      <div class="tt-view-switcher" role="region" aria-label="수집 원문 및 적재 본문 전환">
        <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
          <div class="tt-seg" role="group" aria-label="본문 표시 모드">
            <button type="button" class="tt-seg-btn ${activeTab === "original" ? "active" : ""}" data-switch-body="${esc(doc.id)}" data-target-tab="original">📄 수집 원문 (Original)</button>
            <button type="button" class="tt-seg-btn ${activeTab === "ingested" ? "active" : ""}" data-switch-body="${esc(doc.id)}" data-target-tab="ingested">📚 적재 본문 (Claire 정제본)</button>
          </div>
          <span class="tt-link-dot linked">🔗 ${esc(doc.id)}</span>
        </div>
        <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap">
          <button type="button" class="tt-btn tt-btn-pri" data-open-dispatch="${esc(doc.id)}" title="이 문서를 다른 테마 지식베이스로 전달하여 초점 적재 (POST /api/syndication/dispatch)">📡 다른 테마로 적재 전달</button>
        </div>
      </div>

      ${bodySection}
      ${bottomMetaBar}
    </div>`;
  }

  function renderDispatchesStream(container, readerPane, theme) {
    const dispatches = theme.dispatches || [];
    if (!dispatches.length) {
      container.innerHTML = `<div style="padding:42px 20px;text-align:center;color:var(--muted);font-size:13px">현재 테마와 관련된 교차 테마 적재 전달(Dispatch) 이력이 없습니다.</div>`;
      readerPane.innerHTML = `<div style="padding:36px;color:var(--muted);font-size:13px">문서를 선택한 뒤 <b>📡 다른 테마로 적재 전달</b>을 실행하면 <code>POST /api/syndication/dispatch</code>를 통해 대상 테마로 적재되고 이력이 기록됩니다.</div>`;
      return;
    }
    container.innerHTML = dispatches.map((d) => {
      const statusBadge = d.status === "done"
        ? `<span class="tt-link-dot linked">● 완료</span>`
        : `<span class="tt-link-dot unlinked">● ${esc(d.status || "pending")}</span>`;
      return `<article class="tt-card-row" data-dispatch-doc="${esc(d.document_id || "")}">
        <div class="tt-card-topbar">
          <div class="tt-card-meta-left">
            ${statusBadge}
            <span class="tt-feed-pill proto-rss">${esc(d.mode || "cross_theme")}</span>
            <span class="tt-card-feed-title">Theme #${esc(d.theme_id)} → Theme #${esc(d.target_theme_id)}</span>
          </div>
          <div class="tt-card-meta-right">
            <span class="tt-card-time">${esc(formatTimestamp(d.created_at))}</span>
          </div>
        </div>
        <h3 class="tt-card-title">원본 문서: ${esc(d.document_id)} → 대상 문서: ${esc(d.remote_ref || "-")}</h3>
        <p class="tt-card-snippet">적용 초점(Focus): ${esc(d.applied_focus || "(기본 초점)")}${d.error ? ` · 오류: ${esc(d.error)}` : ""}</p>
      </article>`;
    }).join("");
    readerPane.innerHTML = `<div style="padding:36px;color:var(--muted);font-size:13px">교차 테마 적재 전달 이력 총 ${dispatches.length}건이 조회되었습니다.</div>`;
  }

  function renderStreamAndReader() {
    const theme = curTheme();
    const container = document.getElementById("headlines-container");
    const readerPane = document.getElementById("reader-content");
    if (!container || !readerPane) return;

    if (state.treeSel.kind === "virtual" && state.treeSel.value === "dispatches") {
      renderDispatchesStream(container, readerPane, theme);
      const footer = document.getElementById("footer-stats");
      if (footer) footer.textContent = `Theme #${theme.id} · 교차 테마 전달 이력 ${theme.dispatches.length}건`;
      return;
    }

    const visible = getVisibleDocuments();
    if (state.loading && !theme.loaded) {
      container.innerHTML = `<div style="padding:42px 20px;text-align:center;color:var(--muted);font-size:13px">테마 지식베이스 문서를 불러오는 중입니다…</div>`;
    } else if (visible.length === 0) {
      container.innerHTML = `<div style="padding:42px 20px;text-align:center;color:var(--muted);font-size:13px">표시할 적재 문서가 없습니다. 상단의 <b>+ 문서 적재</b> 버튼으로 문서를 적재하세요.</div>`;
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
                <input type="checkbox" class="tt-row-check" data-check-id="${esc(doc.id)}" ${checked} aria-label="${esc(doc.title)} 선택"/>
                <button type="button" class="tt-icon-btn ${doc.starred ? "starred" : ""}" data-star-id="${esc(doc.id)}" title="별표(고정) 토글">${doc.starred ? "★" : "☆"}</button>
                <span class="tt-feed-pill proto-rss">${esc(srcLabel)}</span>
                <span class="tt-card-feed-title">${esc(doc.author)}</span>
              </div>
              <div class="tt-card-meta-right">
                <span class="tt-link-dot linked">🔗 ${esc(doc.id)}</span>
                <span class="tt-card-time">${esc(doc.timeAgo)}</span>
              </div>
            </div>
            <h3 class="tt-card-title">${esc(doc.title)}</h3>
            ${summaryHtml}
            <div class="tt-card-bottom">
              <div class="tt-card-tags">${entChips || `<span style="font-size:11px;color:var(--muted)">엔티티 ${doc.entityCount || 0}개</span>`}</div>
              <div class="tt-card-actions">
                <button type="button" data-quick-dispatch="${esc(doc.id)}" class="tt-btn" style="height:23px;padding:0 7px;font-size:11px">📡 테마 전달</button>
                <a class="tt-ext-link" href="${esc(doc.url)}" target="_blank" rel="noopener noreferrer" title="원문 링크 새 탭 열기">↗</a>
              </div>
            </div>
          </article>`;
        }

        const excerptHtml = prefs.showExcerpt ? `<span class="tt-flat-excerpt">— ${esc(doc.summary || doc.rawText || "")}</span>` : "";
        return `<div class="tt-row-wrap ${doc.unread ? "is-unread" : ""} ${isSel ? "is-selected" : ""}" data-row-id="${esc(doc.id)}">
          <div class="tt-flat-row" data-click-row="${esc(doc.id)}">
            <input type="checkbox" class="tt-row-check" data-check-id="${esc(doc.id)}" ${checked} aria-label="${esc(doc.title)} 선택"/>
            <button type="button" class="tt-icon-btn ${doc.starred ? "starred" : ""}" data-star-id="${esc(doc.id)}" title="별표(고정) 토글">${doc.starred ? "★" : "☆"}</button>
            <button type="button" class="tt-icon-btn" data-quick-dispatch="${esc(doc.id)}" title="다른 테마로 적재 전달">📡</button>
            <span class="tt-link-dot linked" title="테마 DB 적재 문서">🔗 적재</span>
            <div class="tt-flat-main">
              <span class="tt-flat-title">${esc(doc.title)}</span>
              <span class="tt-flat-author">${esc(doc.author)}</span>
              ${excerptHtml}
            </div>
            <div class="tt-flat-right">
              <span class="tt-feed-pill proto-rss">${esc(srcLabel)}</span>
              <span class="tt-flat-time">${esc(doc.timeAgo)}</span>
              <a class="tt-ext-link" href="${esc(doc.url)}" target="_blank" rel="noopener noreferrer" title="원문 열기">→</a>
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
      : `<div style="padding:36px;color:var(--muted);font-size:13px">목록에서 읽을 적재 문서를 선택하세요.</div>`;

    const footer = document.getElementById("footer-stats");
    if (footer) {
      footer.textContent = `Theme #${theme.id} (${theme.label}) · 적재 문서 ${theme.documents.length}건 · 엔티티 ${theme.entities.length}개 · 전달 이력 ${theme.dispatches.length}건`;
    }
    bindStreamAndReaderEvents();
  }

  async function togglePinDocument(doc) {
    if (!doc) return;
    const nextPinned = !doc.starred;
    doc.starred = nextPinned;
    renderAll();
    try {
      await fetch("/document/pin", {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({ id: doc.id, pinned: nextPinned, theme: themeRefOf(state.themeId) })
      });
    } catch (_) {}
  }

  async function markDocumentSeen(doc) {
    if (!doc || !doc.unread) return;
    doc.unread = false;
    try {
      await fetch("/document/seen", {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({ id: doc.id, theme: themeRefOf(state.themeId) })
      });
    } catch (_) {}
  }

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
        <h2 id="modal-dispatch-title" style="margin:0;font-size:16px;font-weight:700">📡 교차 테마 적재 전달 (POST /api/syndication/dispatch)</h2>
        <button type="button" id="btn-close-dispatch" class="tt-btn">✕</button>
      </div>
      <div style="font-size:12.5px;color:var(--muted)">선택한 문서(<code>${esc(doc.id)}</code> · <strong>${esc(doc.title)}</strong>)의 원문을 대상 테마의 적재 파이프라인으로 전달하여 대상 테마 초점에 맞춰 새로 정제·적재합니다.</div>
      <div>
        <label for="dispatch-target-theme" style="display:block;font-size:12px;font-weight:700;margin-bottom:4px">대상 지식 테마 (target_theme_id)</label>
        <select id="dispatch-target-theme" class="tt-btn" style="width:100%;height:34px">${targetOptions}</select>
      </div>
      <div>
        <label for="dispatch-focus" style="display:block;font-size:12px;font-weight:700;margin-bottom:4px">적용할 추출 초점 (focus · 선택)</label>
        <input id="dispatch-focus" type="text" class="tt-search" style="width:100%;height:34px" value="${esc(theme.defaultFocus || "")}" placeholder="비워두면 대상 테마의 기본 초점(default_focus)이 적용됩니다"/>
      </div>
      <div id="dispatch-status-msg" style="font-size:12px;color:var(--muted)"></div>
      <div style="display:flex;justify-content:flex-end;gap:8px;padding-top:10px;border-top:1px solid var(--border)">
        <button type="button" id="btn-cancel-dispatch" class="tt-btn">취소</button>
        <button type="button" id="btn-confirm-dispatch" class="tt-btn tt-btn-pri">📡 대상 테마로 적재 전달</button>
      </div>
    `;

    const closeBtn = document.getElementById("btn-close-dispatch");
    const cancelBtn = document.getElementById("btn-cancel-dispatch");
    const confirmBtn = document.getElementById("btn-confirm-dispatch");
    if (closeBtn) closeBtn.addEventListener("click", () => modal.close());
    if (cancelBtn) cancelBtn.addEventListener("click", () => modal.close());

    if (confirmBtn) {
      confirmBtn.addEventListener("click", async () => {
        const targetThemeId = document.getElementById("dispatch-target-theme").value;
        const focusVal = document.getElementById("dispatch-focus").value.trim();
        const msgEl = document.getElementById("dispatch-status-msg");
        confirmBtn.disabled = true;
        if (msgEl) msgEl.textContent = "교차 테마 적재 전달을 수행하고 있습니다…";
        try {
          const resp = await fetch("/api/syndication/dispatch", {
            method: "POST",
            headers: { "Content-Type": "application/json", Accept: "application/json" },
            body: JSON.stringify({
              document_id: doc.id,
              source_theme_id: Number(theme.id),
              target_theme_id: Number(targetThemeId),
              focus: focusVal || undefined
            })
          });
          const data = await resp.json().catch(() => ({}));
          if (!resp.ok || !data.ok) {
            const errText = data.detail || data.error || `HTTP ${resp.status}`;
            if (msgEl) msgEl.textContent = `전달 실패: ${errText}`;
            ticker(`교차 테마 적재 전달 실패: ${errText}`);
            confirmBtn.disabled = false;
            return;
          }
          modal.close();
          ticker(`📡 교차 테마 적재 완료: ${doc.id} → Theme #${targetThemeId} (${data.target_document_id})`);
          await syncLiveThemeDb();
        } catch (err) {
          if (msgEl) msgEl.textContent = `네트워크 오류: ${err.message || err}`;
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
      const q = `?id=${encodeURIComponent(doc.id)}&theme=${encodeURIComponent(themeRefOf(state.themeId))}`;
      const resp = await fetch(`/document${q}`, { headers: { Accept: "application/json" } });
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
      const q = `?theme=${encodeURIComponent(ref)}`;
      const [gResp, dResp, dispResp] = await Promise.all([
        fetch(`/graph${q}`, { headers: { Accept: "application/json" } }),
        fetch(`/documents${q}`, { headers: { Accept: "application/json" } }),
        fetch(`/api/syndication/dispatches?theme_id=${encodeURIComponent(theme.id)}`, { headers: { Accept: "application/json" } })
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
    ticker(`목록 스타일: ${listStyleLabel(state.listStyle)} — ${i18n(LIST_STYLE_META[state.listStyle].desc)}`);
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
    ticker(`화면 분할: ${state.layoutMode === "three-panel" ? "3단 분할 (좌-중-우)" : "2단 상하 분할 (좌-상하)"}`);
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
        state.themeId = e.target.value;
        const nextPrefs = loadPrefs(state.themeId);
        Object.keys(prefs).forEach((k) => { delete prefs[k]; });
        Object.assign(prefs, nextPrefs);
        state.layoutMode = prefs.layoutMode === "two-panel" ? "two-panel" : "three-panel";
        state.listStyle = prefs.listStyle;
        state.treeSel = { kind: "virtual", value: "all", label: "전체 적재 문서 (All documents)" };
        state.checkedIds.clear();
        state.selectedDocId = null;
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
        ticker(`${targets.length}개 문서 읽음 처리 완료`);
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
        else if (cmd === "show-url") window.prompt("문서 URL:", doc.url);
        else if (cmd === "toggle-unread") { markDocumentSeen(doc); renderAll(); }
        else if (cmd === "toggle-star") togglePinDocument(doc);
        else if (cmd === "dispatch") openDispatchModal(doc.id);
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
        const focusEl = document.getElementById("ingest-focus");
        const submitBtn = document.getElementById("btn-submit-ingest");
        const payload = payloadEl ? payloadEl.value.trim() : "";
        const focus = focusEl ? focusEl.value.trim() : "";
        if (!payload) return;
        if (submitBtn) submitBtn.disabled = true;
        ticker("문서 적재 요청을 전송하고 있습니다…");
        try {
          const resp = await fetch("/ingest", {
            method: "POST",
            headers: { "Content-Type": "application/json", Accept: "application/json" },
            body: JSON.stringify({
              payload,
              focus: focus || undefined,
              theme: themeRefOf(state.themeId)
            })
          });
          const data = await resp.json().catch(() => ({}));
          if (!resp.ok || data.error) {
            ticker(`적재 실패: ${data.detail || data.error || resp.status}`);
          } else {
            if (ingestModal) ingestModal.close();
            if (payloadEl) payloadEl.value = "";
            if (focusEl) focusEl.value = "";
            if (data.document_id) state.selectedDocId = data.document_id;
            ticker(`📥 문서 적재 완료: ${data.document_id || ""}`);
            await syncLiveThemeDb();
          }
        } catch (err) {
          ticker(`적재 오류: ${err.message || err}`);
        } finally {
          if (submitBtn) submitBtn.disabled = false;
        }
      });
    }

    document.addEventListener("keydown", (e) => {
      if (e.target.matches("input, textarea, select") || document.querySelector("dialog[open]")) return;
      if (e.ctrlKey || e.metaKey || e.altKey) return;

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
  }

  bindGlobalControls();
  renderAll();
  syncLiveThemeDb();
})();
