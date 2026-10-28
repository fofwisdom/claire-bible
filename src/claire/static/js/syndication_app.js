/* Claire Bible — TT-RSS Clean-Room Syndication Reader & FlatList Controller */
(function () {
  const ST = window.CLAIRE_SYND_STORAGE;
  const THEMES = JSON.parse(JSON.stringify(window.CLAIRE_SYND_SEEDS || {}));
  const cfg = window.__CLAIRE_CONFIG || {};

  function loadPrefs() {
    try {
      const p = JSON.parse(localStorage.getItem(ST.PREFS_KEY) || "null");
      if (!p) return JSON.parse(JSON.stringify(ST.DEFAULT_PREFS));
      return {
        ...ST.DEFAULT_PREFS,
        ...p,
        tagPolicy: { ...ST.DEFAULT_PREFS.tagPolicy, ...(p.tagPolicy || {}) },
        outboundPolicy: { ...ST.DEFAULT_PREFS.outboundPolicy, ...(p.outboundPolicy || {}) }
      };
    } catch (_) {
      return JSON.parse(JSON.stringify(ST.DEFAULT_PREFS));
    }
  }

  function savePrefs() {
    try { localStorage.setItem(ST.PREFS_KEY, JSON.stringify(prefs)); } catch (_) {}
  }

  function loadCustom() {
    try { return JSON.parse(localStorage.getItem(ST.CUSTOM_KEY) || "{}"); } catch (_) { return {}; }
  }

  function saveCustom(data) {
    try { localStorage.setItem(ST.CUSTOM_KEY, JSON.stringify(data)); } catch (_) {}
  }

  let prefs = loadPrefs();
  let customState = loadCustom();

  if (Array.isArray(cfg.themes)) {
    cfg.themes.forEach((srv) => {
      const k = String(srv.id);
      if (!THEMES[k]) {
        const clone = JSON.parse(JSON.stringify(THEMES["0"]));
        clone.id = srv.id;
        clone.seq = srv.seq || srv.id;
        clone.icon = srv.icon || "📁";
        clone.label = srv.label || `테마 #${srv.id}`;
        clone.fqdn = srv.fqdn || "";
        clone.dbPath = srv.db_path || (srv.id === 0 ? "data/claire.db" : `data/themes/${srv.seq || srv.id}/claire.db`);
        clone.isPublic = srv.is_public !== false;
        clone.defaultFocus = srv.default_focus || "";
        clone.actorHandle = clone.isPublic ? `@theme${srv.id}@${srv.fqdn || "claire.example.com"}` : "(비공개 테마 — 외부 발행 차단)";
        THEMES[k] = clone;
      } else {
        if (srv.label) THEMES[k].label = srv.label;
        if (srv.icon) THEMES[k].icon = srv.icon;
        if (srv.is_public !== undefined) THEMES[k].isPublic = srv.is_public;
      }
    });
  }

  Object.keys(THEMES).forEach((k) => {
    const cs = customState[k];
    if (!cs) return;
    if (Array.isArray(cs.extraFeeds)) {
      cs.extraFeeds.forEach((ef) => {
        if (!THEMES[k].feeds.some((f) => f.id === ef.id)) THEMES[k].feeds.push(ef);
      });
    }
  });

  const urlParams = new URLSearchParams(window.location.search);
  const reqTheme = urlParams.get("theme");
  const initThemeKey = (reqTheme && THEMES[reqTheme]) ? reqTheme : (THEMES[String(cfg.activeThemeId)] ? String(cfg.activeThemeId) : "0");

  const state = {
    themeId: initThemeKey,
    layoutMode: prefs.layoutMode === "split" ? "split" : "flatlist",
    treeSel: { kind: "virtual", value: "all", label: "전체 아티클 (All articles)" },
    adaptiveFilter: "all",
    protoFilter: "all",
    sortBy: "newest",
    searchQuery: "",
    selectedItemId: THEMES[initThemeKey].items[0] ? THEMES[initThemeKey].items[0].id : null,
    expandedIds: new Set(),
    checkedIds: new Set(),
    contentTabByItem: {},
    activeArticleMenuItemId: null,
    liveSynced: false
  };

  function curTheme() { return THEMES[state.themeId] || THEMES["0"]; }
  function esc(s) { return String(s || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;"); }
  function ticker(msg) {
    const el = document.getElementById("activity-ticker");
    if (el) el.textContent = `${new Date().toLocaleTimeString("ko-KR", { hour12: false })} · ${msg}`;
  }

  function noiseTagSet() {
    return new Set(String(prefs.tagPolicy.ignoredNoiseTags || "").split(",").map((s) => s.trim().toLowerCase()).filter(Boolean));
  }

  /* 수집 원문 태그 목록 (적재 전 외부 원본 태그) */
  function getItemSourceTags(item) {
    const noise = noiseTagSet();
    const seen = new Set();
    const result = [];
    (item.sourceTags || []).forEach((rawTag) => {
      const clean = String(rawTag || "").replace(/^#+/, "").trim();
      if (!clean || noise.has(clean.toLowerCase())) return;
      const key = clean.toLowerCase();
      if (seen.has(key)) return;
      seen.add(key);
      result.push(clean);
    });
    return result;
  }

  /* 프로젝트 적재 시 생성·연계된 클레어바이블 엔티티 노드 목록 */
  function getLinkedDocEntities(item) {
    if (item.ingestState !== "linked" || !item.linkedDoc) return [];
    if (Array.isArray(item.linkedDoc.entities) && item.linkedDoc.entities.length > 0) {
      return item.linkedDoc.entities;
    }
    const theme = curTheme();
    const sourceTags = getItemSourceTags(item);
    return sourceTags.map((tag) => {
      const found = theme.existingEntities.find((e) => e.name.toLowerCase() === tag.toLowerCase());
      return { name: found ? found.name : tag, type: found ? found.type : "Concept" };
    });
  }

  function getActiveContentTab(item) {
    if (state.contentTabByItem[item.id]) return state.contentTabByItem[item.id];
    if (prefs.defaultContentTab === "original") return "original";
    if (prefs.defaultContentTab === "ingested") return "ingested";
    return item.ingestState === "linked" ? "ingested" : "original";
  }

  function getVisibleItems() {
    const theme = curTheme();
    const q = state.searchQuery.trim().toLowerCase();
    const list = theme.items.filter((it) => {
      const s = state.treeSel;
      if (s.kind === "virtual") {
        if (s.value === "fresh" && !it.unread) return false;
        if (s.value === "starred" && !it.starred) return false;
        if (s.value === "linked" && it.ingestState !== "linked") return false;
        if (s.value === "published" && it.publishState === "none") return false;
      } else if (s.kind === "feed" && it.feedId !== s.value) {
        return false;
      } else if (s.kind === "folder") {
        const f = theme.feeds.find((x) => x.id === it.feedId);
        if (!f || f.folder !== s.value) return false;
      } else if (s.kind === "entity") {
        const targetEnt = s.value.toLowerCase();
        const docEntities = getLinkedDocEntities(it);
        const hasLinkedEnt = docEntities.some((e) => e.name.toLowerCase() === targetEnt);
        const sourceTags = getItemSourceTags(it);
        const hasMatchingTag = sourceTags.some((t) => t.toLowerCase() === targetEnt);
        if (!hasLinkedEnt && !hasMatchingTag) return false;
      }
      if (state.adaptiveFilter === "unread" && !it.unread) return false;
      if (state.adaptiveFilter === "unlinked" && it.ingestState !== "unlinked") return false;
      if (state.adaptiveFilter === "linked" && it.ingestState !== "linked") return false;
      if (state.protoFilter !== "all" && it.proto !== state.protoFilter) return false;
      if (q) {
        const docEntStr = getLinkedDocEntities(it).map((e) => e.name).join(" ");
        const tagStr = getItemSourceTags(it).join(" ");
        const hay = `${it.title} ${it.author} ${it.rawBody} ${docEntStr} ${tagStr}`.toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });
    return list.sort((a, b) => state.sortBy === "oldest" ? a.timestamp - b.timestamp : (state.sortBy === "score" ? b.score - a.score : b.timestamp - a.timestamp));
  }

  function applyLayoutAttr() {
    document.body.setAttribute("data-layout", state.layoutMode);
    const lbl = document.getElementById("menu-layout-label");
    if (lbl) lbl.textContent = state.layoutMode === "flatlist" ? "◫ 3단 분할 뷰로 전환 (Three-panel)" : "☰ 한 줄 FlatList 모드로 전환";
    const quickBtn = document.getElementById("btn-quick-layout");
    if (quickBtn) quickBtn.textContent = state.layoutMode === "flatlist" ? "☰ FlatList" : "◫ 3단 뷰";
    const prefLink = document.getElementById("menu-pref-link");
    const topPrefLink = document.getElementById("top-pref-link");
    const href = `/syndication/preferences?theme=${encodeURIComponent(state.themeId)}`;
    if (prefLink) prefLink.href = href;
    if (topPrefLink) topPrefLink.href = href;
  }

  function renderTopBar() {
    const sel = document.getElementById("theme-selector");
    sel.innerHTML = Object.values(THEMES).map((t) => `<option value="${t.id}" ${String(t.id) === String(state.themeId) ? "selected" : ""}>${esc(t.icon)} ${esc(t.label)}</option>`).join("");
    document.getElementById("active-stream-title").textContent = state.treeSel.label;
    const cntEl = document.getElementById("selected-count-label");
    cntEl.textContent = state.checkedIds.size > 0 ? `${state.checkedIds.size}개 아티클 선택됨` : "";
    document.getElementById("btn-adaptive-label").textContent = ({ all: "Adaptive (전체)", unread: "안읽음만", unlinked: "미적재만", linked: "🔗 적재 연동만" })[state.adaptiveFilter] || "Adaptive";
    document.getElementById("btn-sort-label").textContent = ({ newest: "최신순", oldest: "오래된순", score: "엔티티 연관도순" })[state.sortBy] || "최신순";
  }

  function renderTree() {
    const theme = curTheme();
    const items = theme.items;
    const unreadAll = items.filter((i) => i.unread).length;
    const linkedCnt = items.filter((i) => i.ingestState === "linked").length;
    const starredCnt = items.filter((i) => i.starred).length;
    const pubCnt = items.filter((i) => i.publishState !== "none").length;

    const specials = [
      { v: "all", icon: "📥", label: "전체 아티클", cnt: items.length, fresh: false },
      { v: "fresh", icon: "🌱", label: "새 아티클 (안읽음)", cnt: unreadAll, fresh: unreadAll > 0 },
      { v: "starred", icon: "★", label: "별표 아티클", cnt: starredCnt, fresh: false },
      { v: "published", icon: "📡", label: "발행·부스트됨", cnt: pubCnt, fresh: false },
      { v: "linked", icon: "🔗", label: "프로젝트 적재 연동", cnt: linkedCnt, fresh: false }
    ];
    document.getElementById("tree-specials").innerHTML = specials.map((s) => {
      const act = state.treeSel.kind === "virtual" && state.treeSel.value === s.v ? "active" : "";
      return `<button type="button" class="tt-tree-item ${act}" data-tree-kind="virtual" data-tree-val="${s.v}" data-tree-lbl="${esc(s.label)}"><span>${s.icon} ${esc(s.label)}</span><span class="tt-count-pill ${s.fresh ? "fresh" : ""}">${s.cnt}</span></button>`;
    }).join("");

    const folders = {};
    theme.feeds.forEach((f) => {
      if (!folders[f.folder]) folders[f.folder] = [];
      folders[f.folder].push(f);
    });
    document.getElementById("tree-folders").innerHTML = Object.entries(folders).map(([folder, fList]) => {
      const folderUnread = items.filter((it) => fList.some((f) => f.id === it.feedId) && it.unread).length;
      const children = fList.map((f) => {
        const cnt = items.filter((it) => it.feedId === f.id && it.unread).length;
        const pIcon = f.proto === "ap" ? "🟣" : (f.proto === "at" ? "🔵" : "🟠");
        const act = state.treeSel.kind === "feed" && state.treeSel.value === f.id ? "active" : "";
        return `<button type="button" class="tt-tree-item ${act}" data-tree-kind="feed" data-tree-val="${esc(f.id)}" data-tree-lbl="${esc(f.title)}"><span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${pIcon} ${esc(f.title)}</span><span class="tt-count-pill ${cnt > 0 ? "fresh" : ""}">${cnt}</span></button>`;
      }).join("");
      return `<details class="tt-folder" open><summary><span><span class="tt-folder-marker"></span>📁 ${esc(folder)}</span><span class="tt-count-pill">${folderUnread}</span></summary><div class="tt-tree-children">${children}</div></details>`;
    }).join("");

    document.getElementById("tree-entities").innerHTML = theme.existingEntities.slice(0, 10).map((ent) => {
      const act = state.treeSel.kind === "entity" && state.treeSel.value === ent.name ? "active" : "";
      return `<button type="button" class="tt-tree-item ${act}" data-tree-kind="entity" data-tree-val="${esc(ent.name)}" data-tree-lbl="[[${esc(ent.name)}]]"><span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">[[${esc(ent.name)}]] <small style="color:var(--muted)">(${esc(ent.type)})</small></span><span class="tt-count-pill">${ent.count || 1}</span></button>`;
    }).join("");

    document.querySelectorAll("[data-tree-kind]").forEach((btn) => {
      btn.addEventListener("click", () => {
        state.treeSel = { kind: btn.getAttribute("data-tree-kind"), value: btn.getAttribute("data-tree-val"), label: btn.getAttribute("data-tree-lbl") };
        renderAll();
      });
    });
  }

  function buildReaderHtml(item) {
    const theme = curTheme();
    const feed = theme.feeds.find((f) => f.id === item.feedId) || { title: item.format, proto: item.proto };
    const activeTab = getActiveContentTab(item);
    const hasLinked = item.ingestState === "linked" && item.linkedDoc;
    const fontCls = prefs.readerFont === "serif" ? "font-serif" : "";
    const pubLabel = item.publishState === "boosted" ? "🔁 부스트/인용 완료" : (item.publishState === "published_create" ? "📡 적재 발행 완료" : "🔁 부스트·인용 / 📡 적재 발행");

    let bodySection = "";
    if (activeTab === "original") {
      bodySection = `<div class="tt-reader-body ${fontCls}" style="--read-fs:${prefs.readerFontSize}px"><article class="doc-content">${item.rawHtml || `<p>${esc(item.rawBody)}</p>`}</article></div>`;
    } else if (hasLinked) {
      const doc = item.linkedDoc;
      bodySection = `<div class="tt-reader-body ${fontCls}" style="--read-fs:${prefs.readerFontSize}px">
        <div class="tt-summary-callout"><strong>📌 Claire 적재 요약 (${esc(doc.docId)})</strong> — ${esc(doc.summary)}<div style="margin-top:4px;font-size:11.5px;color:var(--muted)">적재 시각: ${esc(doc.ingestedAt)} · 적용 초점: ${esc(doc.focusUsed)} · 공개 링크: <code>${esc(doc.shareUrl)}</code></div></div>
        <article class="doc-content">${doc.detailHtml}</article>
      </div>`;
    } else {
      const srcTags = getItemSourceTags(item);
      bodySection = `<div class="tt-summary-callout" style="border-left-color:var(--accent);background:var(--panel-bg)">
        <strong>아직 프로젝트 DB에 적재되지 않은 수집 원문입니다.</strong>
        <p style="margin:6px 0 10px;font-size:12.5px;color:var(--muted)"><b>엔티티는 적재 시에 생성됩니다.</b> 아래의 [프로젝트 적재 전달]을 누르면 수집 원문의 태그(${srcTags.map((t) => `<code>#${esc(t)}</code>`).join(", ") || "없음"})가 클레어바이블 엔티티 정책(<code>[태그]</code>)에 따라 테마 DB 엔티티(<code>[[Entity]]</code>)로 자동 생성·연계되며, 이 뷰 안에서 <b>수집 원문 ⇄ 적재 본문</b>을 즉시 전환해 읽을 수 있습니다.</p>
        <div style="display:flex;gap:6px;flex-wrap:wrap;align-items:center">
          <input type="text" class="tt-search" style="flex:1;min-width:220px" data-focus-for="${esc(item.id)}" value="${esc(theme.defaultFocus || "")}" placeholder="추출 초점 입력 (선택 — 비워두면 중립 정제)"/>
          <button type="button" class="tt-btn tt-btn-pri" data-do-ingest="${esc(item.id)}">📥 지금 프로젝트 적재 전달</button>
        </div>
      </div>`;
    }

    /* 엔티티는 적재 시 생성되므로, 탭 상태 및 적재 여부에 따라 하단 바를 엄격히 구분 표시한다. */
    let bottomMetaBar = "";
    if (activeTab === "ingested" && hasLinked) {
      const entities = getLinkedDocEntities(item);
      const entChips = entities.map((ent) => `<button type="button" class="tt-entity-chip resolved" data-filter-entity="${esc(ent.name)}" title="프로젝트 적재 시 생성된 엔티티 (클릭하여 스트림 필터링)">[[${esc(ent.name)}]] <small style="opacity:0.8">(${esc(ent.type || "Concept")})</small></button>`).join("");
      bottomMetaBar = `<footer class="tt-entity-bar" aria-label="프로젝트 적재 엔티티 바">
        <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap">
          <span style="font-weight:700;color:var(--accent)">🔗 생성된 엔티티 (Ingested Entities):</span>
          ${entChips || `<span style="color:var(--muted)">적재 시 생성된 엔티티 없음</span>`}
          <button type="button" class="tt-btn" style="height:23px;padding:0 7px;font-size:11px" data-add-entity="${esc(item.id)}">+ 엔티티 추가</button>
        </div>
        <span style="font-size:11px;color:var(--muted)">* 프로젝트 적재 시 온톨로지 저장소(entities)에 생성·연계된 지식 엔티티입니다.</span>
      </footer>`;
    } else {
      const sourceTags = getItemSourceTags(item);
      const tagChips = sourceTags.map((tag) => {
        const matchesThemeEnt = theme.existingEntities.some((e) => e.name.toLowerCase() === tag.toLowerCase());
        return `<span class="tt-entity-chip candidate" title="${matchesThemeEnt ? "테마 DB 기존 엔티티 명칭과 일치 (적재 시 즉시 연계)" : "수집 원문 태그 (적재 시 신규 엔티티로 승격)"}">#${esc(tag)}${matchesThemeEnt ? " <small style='color:var(--accent)'>[일치]</small>" : ""}</span>`;
      }).join("");
      bottomMetaBar = `<footer class="tt-entity-bar" aria-label="수집 원문 태그 바">
        <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap">
          <span style="font-weight:700;color:var(--muted)">🏷️ 수집 태그 (Feed Tags):</span>
          ${tagChips || `<span style="color:var(--muted)">수집 원문에 포함된 태그 없음</span>`}
          <button type="button" class="tt-btn" style="height:23px;padding:0 7px;font-size:11px" data-add-tag="${esc(item.id)}">+ 태그 추가</button>
        </div>
        <span style="font-size:11px;color:var(--muted)">* 외부 피드 수집 태그입니다. 프로젝트 적재(Ingest) 시 클레어바이블 엔티티 노드로 생성·등록됩니다.</span>
      </footer>`;
    }

    return `<div class="tt-article-card" data-reader-for="${esc(item.id)}">
      <header class="tt-art-header">
        <div class="tt-art-title-row">
          <h2 class="tt-art-title"><a href="${esc(item.url)}" target="_blank" rel="noopener noreferrer">${esc(item.title)}</a></h2>
          <div style="display:inline-flex;align-items:center;gap:4px;flex-shrink:0">
            <button type="button" class="tt-btn" data-font-delta="-1" title="본문 글자 축소">A-</button>
            <button type="button" class="tt-btn" data-font-delta="1" title="본문 글자 확대">A+</button>
            <button type="button" class="tt-btn" data-toggle-serif="1" title="고딕/명조 서체 전환">${prefs.readerFont === "serif" ? "명조" : "고딕"}</button>
            <button type="button" class="tt-btn" popovertarget="pop-article-actions" data-art-menu-item="${esc(item.id)}" title="아티클 추가 작업">⋯</button>
          </div>
        </div>
        <div class="tt-art-meta">
          <span><strong>${esc(feed.title)}</strong> · 작성자: ${esc(item.author)} · ${esc(item.timeAgo)} · 프로토콜: <code>${esc(item.format)}</code> · 연관도 <b>+${item.score}</b></span>
          <a href="${esc(item.url)}" target="_blank" rel="noopener noreferrer" style="color:var(--accent);text-decoration:none;font-weight:600">원문 링크 열기 ↗</a>
        </div>
      </header>

      <div class="tt-view-switcher" role="region" aria-label="수집 원문 및 적재 본문 전환">
        <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
          <div class="tt-seg" role="group" aria-label="본문 표시 모드">
            <button type="button" class="tt-seg-btn ${activeTab === "original" ? "active" : ""}" data-switch-body="${esc(item.id)}" data-target-tab="original">📄 수집 원문 (Original)</button>
            <button type="button" class="tt-seg-btn ${activeTab === "ingested" ? "active" : ""}" data-switch-body="${esc(item.id)}" data-target-tab="ingested">📚 적재 본문 (Claire 정제본${hasLinked ? " · 보유" : " · 미적재"})</button>
          </div>
          ${hasLinked ? `<span class="tt-link-dot linked">🔗 ${esc(item.linkedDoc.docId)} 적재 완료 (엔티티 생성됨)</span>` : `<button type="button" class="tt-btn tt-btn-pri" data-do-ingest="${esc(item.id)}">📥 프로젝트 적재 전달</button>`}
        </div>
        <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap">
          ${theme.isPublic
            ? `<button type="button" class="tt-btn tt-btn-ap" data-open-pub="${esc(item.id)}">${pubLabel}</button>`
            : `<span style="font-size:11.5px;color:var(--muted)">🔒 비공개 테마 (외부 발행 차단)</span>`}
        </div>
      </div>

      ${bodySection}
      ${bottomMetaBar}
    </div>`;
  }

  function renderStreamAndReader() {
    const visible = getVisibleItems();
    const theme = curTheme();
    const container = document.getElementById("headlines-container");

    if (visible.length === 0) {
      container.innerHTML = `<div style="padding:42px 20px;text-align:center;color:var(--muted);font-size:13px">조건에 일치하는 수집 아티클이 없습니다.</div>`;
    } else {
      container.innerHTML = visible.map((it) => {
        const feed = theme.feeds.find((f) => f.id === it.feedId) || { title: it.format, proto: it.proto };
        const isExpanded = state.layoutMode === "flatlist" && state.expandedIds.has(it.id);
        const isSel = state.selectedItemId === it.id;
        const checked = state.checkedIds.has(it.id) ? "checked" : "";
        const pDot = it.proto === "ap" ? "🟣" : (it.proto === "at" ? "🔵" : "🟠");
        const pubIcon = it.publishState === "boosted" ? "🔁" : (it.publishState === "published_create" ? "📡" : "rss");
        const linkBadge = it.ingestState === "linked"
          ? `<span class="tt-link-dot linked" title="프로젝트 DB 적재 완료 (엔티티 생성됨)">🔗 적재</span>`
          : `<span class="tt-link-dot unlinked" title="미적재 수집 원문 (적재 시 엔티티 생성)">○</span>`;
        const excerptHtml = prefs.showExcerpt ? `<span class="tt-flat-excerpt">— ${esc(it.rawBody)}</span>` : "";

        return `<div class="tt-row-wrap ${it.unread ? "is-unread" : ""} ${isSel ? "is-selected" : ""}" data-row-id="${esc(it.id)}">
          <div class="tt-flat-row" data-click-row="${esc(it.id)}">
            <input type="checkbox" class="tt-row-check" data-check-id="${esc(it.id)}" ${checked} aria-label="${esc(it.title)} 선택"/>
            <button type="button" class="tt-icon-btn ${it.starred ? "starred" : ""}" data-star-id="${esc(it.id)}" title="별표 토글">${it.starred ? "★" : "☆"}</button>
            <button type="button" class="tt-icon-btn ${it.publishState !== "none" ? "published" : ""}" data-pub-id="${esc(it.id)}" title="부스트/인용 발행 또는 적재 발행">${pubIcon === "rss" ? "📡" : pubIcon}</button>
            ${linkBadge}
            <div class="tt-flat-main">
              <span class="tt-flat-title">${esc(it.title)}</span>
              <span class="tt-flat-author">${esc(it.author)}</span>
              ${excerptHtml}
            </div>
            <div class="tt-flat-right">
              <span class="tt-feed-pill proto-${esc(it.proto)}">${esc(feed.title)}</span>
              <span class="tt-flat-time">${esc(it.timeAgo)}</span>
              <a class="tt-ext-link" href="${esc(it.url)}" target="_blank" rel="noopener noreferrer" title="수집 원문 열기">→</a>
              <span class="tt-proto-dot" title="${esc(it.format)}">${pDot}</span>
            </div>
          </div>
          ${isExpanded ? buildReaderHtml(it) : ""}
        </div>`;
      }).join("");
    }

    const readerPane = document.getElementById("reader-content");
    if (state.layoutMode === "split") {
      const selItem = theme.items.find((x) => x.id === state.selectedItemId) || visible[0];
      readerPane.innerHTML = selItem ? buildReaderHtml(selItem) : `<div style="padding:36px;color:var(--muted);font-size:13px">좌측 FlatList 목록에서 읽을 아티클을 선택하세요.</div>`;
    } else {
      readerPane.innerHTML = "";
    }

    const linkedTotal = theme.items.filter((x) => x.ingestState === "linked").length;
    document.getElementById("footer-stats").textContent = `Theme #${theme.id} (${theme.dbPath}) · 총 ${theme.items.length}건 · 🔗 적재완료 ${linkedTotal}건 · 액터 ${theme.actorHandle}`;
    bindStreamAndReaderEvents();
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
        const it = curTheme().items.find((x) => x.id === btn.getAttribute("data-star-id"));
        if (it) { it.starred = !it.starred; renderAll(); }
      });
    });

    document.querySelectorAll("[data-pub-id], [data-open-pub]").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        const id = btn.getAttribute("data-pub-id") || btn.getAttribute("data-open-pub");
        openPublishModal(id);
      });
    });

    document.querySelectorAll(".tt-ext-link").forEach((a) => a.addEventListener("click", (e) => e.stopPropagation()));

    document.querySelectorAll("[data-click-row]").forEach((row) => {
      row.addEventListener("click", () => {
        const id = row.getAttribute("data-click-row");
        const it = curTheme().items.find((x) => x.id === id);
        state.selectedItemId = id;
        if (it && prefs.autoMarkReadOnOpen) it.unread = false;
        if (state.layoutMode === "flatlist") {
          if (state.expandedIds.has(id)) state.expandedIds.delete(id);
          else {
            state.expandedIds.add(id);
            hydrateLiveDocumentIfNeeded(it);
          }
        } else {
          hydrateLiveDocumentIfNeeded(it);
        }
        renderAll();
      });
    });

    document.querySelectorAll("[data-switch-body]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const id = btn.getAttribute("data-switch-body");
        state.contentTabByItem[id] = btn.getAttribute("data-target-tab");
        renderStreamAndReader();
      });
    });

    document.querySelectorAll("[data-do-ingest]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const id = btn.getAttribute("data-do-ingest");
        const focusInput = document.querySelector(`[data-focus-for="${id}"]`);
        ingestItemToClaire(id, focusInput ? focusInput.value.trim() : "");
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

    /* 수집 원문 태그 추가 */
    document.querySelectorAll("[data-add-tag]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const id = btn.getAttribute("data-add-tag");
        const it = curTheme().items.find((x) => x.id === id);
        if (!it) return;
        const name = window.prompt("수집 원문에 추가할 피드 태그(#Tag)를 입력하세요 (적재 시 엔티티로 승격됨):", "");
        if (!name || !name.trim()) return;
        const clean = name.trim().replace(/^#+/, "");
        if (!it.sourceTags) it.sourceTags = [];
        if (!it.sourceTags.includes(clean)) it.sourceTags.push(clean);
        ticker(`🏷️ 수집 태그 '#${clean}' 추가 완료 (적재 시 테마 DB 엔티티 노드로 생성됩니다)`);
        renderAll();
      });
    });

    /* 적재 본문 엔티티 추가 (적재 완료 문서) */
    document.querySelectorAll("[data-add-entity]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const id = btn.getAttribute("data-add-entity");
        const it = curTheme().items.find((x) => x.id === id);
        if (!it || !it.linkedDoc) return;
        const name = window.prompt("적재 문서에 연계할 클레어바이블 엔티티([[Entity]]) 이름을 입력하세요:", "");
        if (!name || !name.trim()) return;
        const clean = name.trim().replace(/^#+|\[\[|\]\]/g, "");
        if (!Array.isArray(it.linkedDoc.entities)) it.linkedDoc.entities = [];
        if (!it.linkedDoc.entities.some((e) => e.name.toLowerCase() === clean.toLowerCase())) {
          it.linkedDoc.entities.push({ name: clean, type: "Concept" });
        }
        if (!curTheme().existingEntities.some((e) => e.name.toLowerCase() === clean.toLowerCase())) {
          curTheme().existingEntities.unshift({ name: clean, type: "Concept", count: 1 });
        }
        ticker(`🔗 적재 문서에 온톨로지 엔티티 [[${clean}]] 연계 완료`);
        renderAll();
      });
    });

    document.querySelectorAll("[data-art-menu-item]").forEach((btn) => {
      btn.addEventListener("click", () => {
        state.activeArticleMenuItemId = btn.getAttribute("data-art-menu-item");
      });
    });
  }

  /* 엔티티는 적재(Ingest) 시에 생성되는 것이 핵심 불변식이다. */
  function ingestItemToClaire(itemId, customFocus, onComplete) {
    const theme = curTheme();
    const item = theme.items.find((x) => x.id === itemId);
    if (!item) return;

    const sourceTags = getItemSourceTags(item);
    const createdEntities = [];

    sourceTags.forEach((tagName) => {
      const existing = theme.existingEntities.find((e) => e.name.toLowerCase() === tagName.toLowerCase());
      if (!existing) {
        const newEnt = { name: tagName, type: "Concept", count: 1 };
        theme.existingEntities.unshift(newEnt);
        createdEntities.push(newEnt);
      } else {
        existing.count = (existing.count || 1) + 1;
        createdEntities.push({ name: existing.name, type: existing.type });
      }
    });

    if (createdEntities.length === 0) {
      createdEntities.push({ name: item.title.slice(0, 20), type: "Concept" });
    }

    const usedFocus = customFocus || theme.defaultFocus || "(중립 기본 추출 + [태그] 엔티티 정책)";
    const docId = `doc_${Math.random().toString(16).slice(2, 8)}`;
    const tagListText = sourceTags.map((t) => `<code>#${esc(t)}</code>`).join(", ") || "없음";
    const entListText = createdEntities.map((e) => `<code>[[${esc(e.name)}]]</code> (${esc(e.type)})`).join(", ");

    item.ingestState = "linked";
    item.linkedDoc = {
      docId,
      ingestedAt: "방금 전 (프로젝트 DB 적재 완료)",
      focusUsed: usedFocus,
      entityCount: createdEntities.length,
      relationCount: Math.max(1, createdEntities.length - 1),
      shareUrl: theme.isPublic ? `/p?s=sh_${docId.slice(4)}` : "(비공개 테마 — 외부 공유 링크 미발급)",
      entities: createdEntities,
      summary: `[Claire 정제 요약] ${item.rawBody}`,
      detailHtml: `<h2>구조화 적재 본문 (${esc(item.title)})</h2>
        <p>수집 원문(${esc(item.format)})과 수집 태그(${tagListText})를 클레어바이블 적재 정책([태그] → entities)에 따라 정규화하여 생성한 지식 문서입니다.</p>
        <blockquote>적용된 추출 초점: ${esc(usedFocus)}</blockquote>
        ${item.rawHtml || `<p>${esc(item.rawBody)}</p>`}
        <h3>적재 시 생성·연계된 엔티티 노드 (${createdEntities.length}건)</h3>
        <p>${entListText}</p>`
    };
    state.contentTabByItem[item.id] = "ingested";
    state.expandedIds.add(item.id);
    ticker(`🔗 프로젝트 적재 완료 (${docId}) — 수집 태그로부터 엔티티 ${createdEntities.length}건이 테마 DB에 생성되었습니다!`);
    renderAll();
    if (typeof onComplete === "function") onComplete();
  }

  function openPublishModal(itemId) {
    const theme = curTheme();
    const item = theme.items.find((x) => x.id === itemId);
    if (!item) return;
    if (!theme.isPublic) {
      ticker("🔒 비공개 격리 테마의 항목은 외부 연합우주로 발행·부스트할 수 없습니다.");
      return;
    }

    const modal = document.getElementById("modal-publish");
    const box = document.getElementById("modal-publish-body");
    const isAp = item.proto === "ap" && !!item.apObjectUri;
    let mode = isAp ? (prefs.outboundPolicy.apDefaultMode || "announce") : (prefs.outboundPolicy.rssDefaultMode || "quote_boost");
    if (!isAp && mode === "announce") mode = "quote_boost";
    let commentText = `핵심 인사이트 공유: ${item.title}`;

    function buildPayload(curMode, comment) {
      const isIngested = item.ingestState === "linked" && item.linkedDoc;
      const tagTokens = isIngested
        ? getLinkedDocEntities(item).map((e) => e.name)
        : getItemSourceTags(item);
      const apTags = prefs.tagPolicy.attachEntityHashtagsOnPublish
        ? tagTokens.map((t) => ({ type: "Hashtag", name: `#${t.replace(/\s+/g, "")}` }))
        : [];
      const hashText = apTags.map((t) => t.name).join(" ");

      if (curMode === "announce" && isAp) {
        return {
          "@context": "https://www.w3.org/ns/activitystreams",
          type: "Announce",
          actor: `https://${theme.fqdn || "claire.example.com"}/ap/actor`,
          object: item.apObjectUri,
          to: ["https://www.w3.org/ns/activitystreams#Public"]
        };
      }
      if (curMode === "quote_boost") {
        return {
          "@context": ["https://www.w3.org/ns/activitystreams", { quoteUrl: "https://w3id.org/fep/044f#quoteUrl" }],
          type: "Create",
          actor: `https://${theme.fqdn || "claire.example.com"}/ap/actor`,
          object: {
            type: "Note",
            content: `${comment ? comment + "\n\n" : ""}📌 ${item.title} — ${item.url} ${hashText}`.trim(),
            quoteUrl: item.apObjectUri || item.url,
            attachment: [{ type: "Link", href: item.url, name: item.title }],
            tag: apTags
          }
        };
      }
      const doc = item.linkedDoc;
      const shareSuffix = (doc && prefs.outboundPolicy.includeCanonicalShareLink) ? `\n🔗 Claire 지식 그래프 열람: ${window.location.origin}${doc.shareUrl}` : "";
      return {
        "@context": "https://www.w3.org/ns/activitystreams",
        type: "Create",
        actor: `https://${theme.fqdn || "claire.example.com"}/ap/actor`,
        object: {
          type: "Article",
          name: item.title,
          summary: doc ? doc.summary : item.rawBody,
          content: `${doc ? doc.summary : item.rawBody}${shareSuffix}\n${hashText}`.trim(),
          url: item.url,
          tag: apTags
        }
      };
    }

    function drawModal() {
      const hasLinked = item.ingestState === "linked" && !!item.linkedDoc;
      const payload = buildPayload(mode, commentText);
      box.innerHTML = `<div style="display:flex;justify-content:space-between;align-items:flex-start;gap:10px">
        <div>
          <h2 id="modal-publish-title" style="margin:0;font-size:16px;font-weight:700">외부 연합우주 발행 · 부스트 (${esc(item.format)})</h2>
          <p style="margin:4px 0 0;font-size:12px;color:var(--muted)">발신 액터: <code>${esc(theme.actorHandle)}</code> · RSS/ATOM 및 AP/AT 모두 <b>부스트/인용 발행</b>과 <b>적재 발행</b>을 지원합니다.</p>
        </div>
        <button type="button" id="pub-close-x" class="tt-btn">✕</button>
      </div>

      <div class="tt-seg" style="display:grid;grid-template-columns:${isAp ? "1fr 1fr 1fr" : "1fr 1fr"}">
        ${isAp ? `<button type="button" class="tt-seg-btn ${mode === "announce" ? "active" : ""}" data-pub-mode="announce">🔁 순수 부스트 (Announce)</button>` : ""}
        <button type="button" class="tt-seg-btn ${mode === "quote_boost" ? "active" : ""}" data-pub-mode="quote_boost">💬 부스트 / 인용 발행 (원문 공유)</button>
        <button type="button" class="tt-seg-btn ${mode === "ingested_create" ? "active" : ""}" data-pub-mode="ingested_create">📡 적재 발행 (Claire 적재 본문)</button>
      </div>

      ${mode === "quote_boost" ? `<div><label for="pub-quote-comment" style="display:block;font-size:12px;font-weight:700;margin-bottom:4px">인용/부스트 코멘트 (선택)</label><input id="pub-quote-comment" type="text" class="tt-search" style="width:100%" value="${esc(commentText)}"/></div>` : ""}

      ${mode === "ingested_create" && !hasLinked ? `<div class="tt-summary-callout" style="margin:0">아직 미적재 항목입니다. 아래 <b>적재 후 즉시 적재 발행</b>을 누르면 프로젝트 적재(엔티티 생성)와 ActivityPub 적재 본문 발행을 연속 수행합니다.</div>` : ""}

      <div>
        <div style="font-size:11.5px;font-weight:700;color:var(--muted);margin-bottom:4px">ActivityPub Outbox JSON-LD 미리보기</div>
        <pre style="margin:0;padding:10px;border-radius:6px;border:1px solid var(--border);background:var(--panel-bg);font-family:'D2Coding',monospace;font-size:11.5px;max-height:210px;overflow:auto">${esc(JSON.stringify(payload, null, 2))}</pre>
      </div>

      <div style="display:flex;justify-content:flex-end;gap:8px;padding-top:10px;border-top:1px solid var(--border)">
        <button type="button" id="pub-cancel" class="tt-btn">취소</button>
        <button type="button" id="pub-submit" class="tt-btn ${mode === "ingested_create" ? "tt-btn-pri" : "tt-btn-ap"}">${mode === "ingested_create" && !hasLinked ? "📥 적재 후 즉시 적재 발행" : (mode === "ingested_create" ? "📡 적재 본문 발행 실행" : "🔁 부스트 / 인용 발행 실행")}</button>
      </div>`;

      box.querySelector("#pub-close-x").addEventListener("click", () => modal.close());
      box.querySelector("#pub-cancel").addEventListener("click", () => modal.close());
      box.querySelectorAll("[data-pub-mode]").forEach((b) => b.addEventListener("click", () => { mode = b.getAttribute("data-pub-mode"); drawModal(); }));
      const cInput = box.querySelector("#pub-quote-comment");
      if (cInput) cInput.addEventListener("input", (e) => { commentText = e.target.value; });
      box.querySelector("#pub-submit").addEventListener("click", () => {
        if (mode === "ingested_create" && item.ingestState !== "linked") {
          ingestItemToClaire(item.id, "");
        }
        item.publishState = mode === "ingested_create" ? "published_create" : "boosted";
        item.publishedInfo = { mode, at: "방금 전" };
        modal.close();
        ticker(`${mode === "ingested_create" ? "📡 적재 본문 발행(Create)" : "🔁 부스트/인용 발행"} 완료: ${item.title}`);
        renderAll();
      });
    }

    drawModal();
    modal.showModal();
  }

  async function hydrateLiveDocumentIfNeeded(item) {
    if (!item || !item.liveDocId || item.liveHydrated) return;
    try {
      const q = `?id=${encodeURIComponent(item.liveDocId)}&theme=${encodeURIComponent(state.themeId)}`;
      const resp = await fetch(`/document${q}`, { headers: { Accept: "application/json" } });
      if (!resp.ok) return;
      const d = await resp.json();
      item.liveHydrated = true;
      if (d.raw_text) item.rawHtml = `<p>${esc(d.raw_text).replace(/\n\n+/g, "</p><p>")}</p>`;
      if (item.linkedDoc) {
        item.linkedDoc.detailHtml = d.detail_html || `<p>${esc(d.detail || d.summary || item.title)}</p>`;
      }
      renderStreamAndReader();
    } catch (_) {}
  }

  async function syncLiveThemeDb() {
    const theme = curTheme();
    try {
      const q = `?theme=${encodeURIComponent(theme.id)}`;
      const [gResp, dResp] = await Promise.all([
        fetch(`/graph${q}`, { headers: { Accept: "application/json" } }),
        fetch(`/documents${q}`, { headers: { Accept: "application/json" } })
      ]);
      if (gResp.ok) {
        const g = await gResp.json();
        (g.nodes || []).slice(0, 12).forEach((n) => {
          const name = String(n.label || n.name || "").trim();
          if (name && !theme.existingEntities.some((e) => e.name.toLowerCase() === name.toLowerCase())) {
            theme.existingEntities.unshift({ name, type: n.group || n.type || "Concept", count: n.degree || 1 });
          }
        });
      }
      if (dResp.ok) {
        const dJson = await dResp.json();
        const docs = Array.isArray(dJson.documents) ? dJson.documents : [];
        docs.slice(0, 6).forEach((doc) => {
          const liveId = `live_${doc.id}`;
          if (theme.items.some((x) => x.id === liveId || (x.url && doc.url && x.url === doc.url))) return;
          theme.items.unshift({
            id: liveId,
            liveDocId: doc.id,
            feedId: theme.feeds[0] ? theme.feeds[0].id : "f0-1",
            proto: "rss",
            format: (doc.source_type || "DB").toUpperCase(),
            title: doc.title || doc.id,
            author: doc.author || "Claire DB",
            url: doc.url || `/p?id=${encodeURIComponent(doc.id)}`,
            timeAgo: "실제 DB",
            timestamp: Math.floor(doc.fetched_at || Date.now() / 1000),
            unread: false,
            starred: !!doc.pinned,
            score: 90,
            sourceTags: [],
            rawBody: doc.summary || doc.title || "실제 테마 SQLite DB에서 동기화된 문서입니다.",
            rawHtml: `<p>${esc(doc.summary || doc.title || "")}</p>`,
            ingestState: "linked",
            linkedDoc: {
              docId: doc.id,
              ingestedAt: "실제 SQLite DB 적재 문서",
              focusUsed: theme.defaultFocus || "(실제 DB 적재본)",
              entityCount: doc.entity_count || 1,
              relationCount: doc.relation_count || 1,
              shareUrl: `/p?id=${encodeURIComponent(doc.id)}`,
              entities: [{ name: doc.title || "문서엔티티", type: "Concept" }],
              summary: doc.summary || doc.title || "",
              detailHtml: `<p>${esc(doc.summary || doc.title || "")}</p>`
            },
            publishState: "none",
            publishedInfo: null
          });
        });
        state.liveSynced = true;
      }
    } catch (_) {}
    renderAll();
  }

  function setupPopoverPositionFallback() {
    if ("positionArea" in document.documentElement.style) return;
    let lastTrigger = null;
    document.addEventListener("click", (e) => {
      const btn = e.target.closest("[popovertarget]");
      if (btn) lastTrigger = btn;
    }, true);
    document.querySelectorAll(".tt-popover").forEach((pop) => {
      pop.addEventListener("toggle", (e) => {
        if (e.newState !== "open" || !lastTrigger) return;
        const r = lastTrigger.getBoundingClientRect();
        pop.style.position = "fixed";
        pop.style.margin = "0";
        const pw = pop.offsetWidth || 220;
        const left = pop.classList.contains("tt-popover-end") ? Math.max(8, r.right - pw) : Math.min(window.innerWidth - pw - 8, Math.max(8, r.left));
        const top = (r.bottom + 240 > window.innerHeight && r.top > 240) ? Math.max(8, r.top - (pop.offsetHeight || 200) - 4) : r.bottom + 4;
        pop.style.left = `${left}px`;
        pop.style.top = `${top}px`;
      });
    });
  }

  function closeOpenPopovers() {
    document.querySelectorAll(".tt-popover").forEach((p) => {
      try { if (p.matches(":popover-open") || p.classList.contains(":popover-open")) p.hidePopover(); } catch (_) {}
    });
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

    document.getElementById("theme-selector").addEventListener("change", (e) => {
      state.themeId = e.target.value;
      state.treeSel = { kind: "virtual", value: "all", label: "전체 아티클 (All articles)" };
      state.checkedIds.clear();
      state.expandedIds.clear();
      const first = curTheme().items[0];
      state.selectedItemId = first ? first.id : null;
      syncLiveThemeDb();
    });

    function toggleLayout() {
      state.layoutMode = state.layoutMode === "flatlist" ? "split" : "flatlist";
      prefs.layoutMode = state.layoutMode;
      savePrefs();
      closeOpenPopovers();
      renderAll();
    }
    document.getElementById("btn-quick-layout").addEventListener("click", toggleLayout);
    document.getElementById("menu-toggle-layout").addEventListener("click", toggleLayout);

    document.getElementById("menu-expand-all").addEventListener("click", () => {
      const vis = getVisibleItems();
      state.layoutMode = "flatlist";
      if (state.expandedIds.size >= vis.length) state.expandedIds.clear();
      else vis.forEach((x) => state.expandedIds.add(x.id));
      closeOpenPopovers();
      renderAll();
    });

    document.getElementById("btn-mark-read").addEventListener("click", () => {
      const targets = state.checkedIds.size > 0 ? curTheme().items.filter((x) => state.checkedIds.has(x.id)) : getVisibleItems();
      targets.forEach((x) => { x.unread = false; });
      ticker(`${targets.length}개 아티클 읽음 처리 완료`);
      renderAll();
    });

    document.getElementById("stream-search").addEventListener("input", (e) => {
      state.searchQuery = e.target.value;
      renderStreamAndReader();
    });

    document.querySelectorAll("[data-select-cmd]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const cmd = btn.getAttribute("data-select-cmd");
        const vis = getVisibleItems();
        if (cmd === "all") vis.forEach((x) => state.checkedIds.add(x.id));
        else if (cmd === "unread") { state.checkedIds.clear(); vis.filter((x) => x.unread).forEach((x) => state.checkedIds.add(x.id)); }
        else if (cmd === "invert") vis.forEach((x) => { if (state.checkedIds.has(x.id)) state.checkedIds.delete(x.id); else state.checkedIds.add(x.id); });
        else if (cmd === "none") state.checkedIds.clear();
        else if (cmd === "toggle-unread") curTheme().items.filter((x) => state.checkedIds.has(x.id)).forEach((x) => { x.unread = !x.unread; });
        else if (cmd === "toggle-star") curTheme().items.filter((x) => state.checkedIds.has(x.id)).forEach((x) => { x.starred = !x.starred; });
        else if (cmd === "ingest-selected") curTheme().items.filter((x) => state.checkedIds.has(x.id) && x.ingestState === "unlinked").forEach((x) => ingestItemToClaire(x.id, ""));
        closeOpenPopovers();
        renderAll();
      });
    });

    document.querySelectorAll("[data-adaptive]").forEach((btn) => {
      btn.addEventListener("click", () => {
        state.adaptiveFilter = btn.getAttribute("data-adaptive");
        closeOpenPopovers();
        renderAll();
      });
    });

    document.querySelectorAll("[data-proto]").forEach((btn) => {
      btn.addEventListener("click", () => {
        state.protoFilter = btn.getAttribute("data-proto");
        closeOpenPopovers();
        renderAll();
      });
    });

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
        const vis = getVisibleItems();
        const idx = vis.findIndex((x) => x.id === state.activeArticleMenuItemId);
        const it = vis[idx] || curTheme().items.find((x) => x.id === state.activeArticleMenuItemId);
        closeOpenPopovers();
        if (!it) return;
        if (cmd === "open-url") window.open(it.url, "_blank", "noopener");
        else if (cmd === "show-url") window.prompt("수집 원문 URL:", it.url);
        else if (cmd === "toggle-unread") { it.unread = !it.unread; renderAll(); }
        else if (cmd === "toggle-star") { it.starred = !it.starred; renderAll(); }
        else if (cmd === "publish") openPublishModal(it.id);
        else if (cmd === "mark-above" && idx > 0) { vis.slice(0, idx).forEach((x) => { x.unread = false; }); renderAll(); }
        else if (cmd === "mark-below" && idx >= 0) { vis.slice(idx + 1).forEach((x) => { x.unread = false; }); renderAll(); }
      });
    });

    const subModal = document.getElementById("modal-subscribe");
    document.getElementById("menu-open-subscribe").addEventListener("click", () => { closeOpenPopovers(); subModal.showModal(); });
    document.getElementById("btn-open-subscribe").addEventListener("click", () => subModal.showModal());

    document.getElementById("form-subscribe").addEventListener("submit", (e) => {
      e.preventDefault();
      const proto = document.getElementById("sub-proto").value;
      const url = document.getElementById("sub-url").value.trim();
      const title = document.getElementById("sub-title").value.trim();
      const folder = document.getElementById("sub-folder").value.trim() || "기본 폴더";
      const tagsRaw = document.getElementById("sub-tags").value.trim();
      if (!url || !title) return;
      const fid = `f_custom_${Date.now()}`;
      const newFeed = { id: fid, folder, proto, format: proto.toUpperCase(), interval: "15m", title, url, etag: "방금 수집됨" };
      curTheme().feeds.push(newFeed);
      if (!customState[state.themeId]) customState[state.themeId] = { extraFeeds: [] };
      if (!Array.isArray(customState[state.themeId].extraFeeds)) customState[state.themeId].extraFeeds = [];
      customState[state.themeId].extraFeeds.push(newFeed);
      saveCustom(customState);

      const sourceTags = tagsRaw ? tagsRaw.split(",").map((s) => s.trim()).filter(Boolean) : [title];
      const newItemId = `it_custom_${Date.now()}`;
      curTheme().items.unshift({
        id: newItemId, feedId: fid, proto, format: proto.toUpperCase(),
        title: `[신규 수집] ${title} 첫 수집 컨텐트`, author: title,
        apObjectUri: proto === "ap" ? `https://federated.example/notes/${Date.now()}` : undefined,
        url: url.startsWith("http") ? url : `https://example.com/${encodeURIComponent(url)}`,
        timeAgo: "방금", timestamp: Math.floor(Date.now() / 1000), unread: true, starred: false, score: 86,
        sourceTags,
        rawBody: `${title} (${url})에서 수집된 원문입니다. 제시된 태그(${sourceTags.join(", ")})는 적재 시 클레어 엔티티로 생성됩니다.`,
        rawHtml: `<p><strong>${esc(title)}</strong> 대상에서 수집된 원문 본문입니다.</p><p>제시된 태그: <code>${esc(sourceTags.map((t) => "#" + t).join(" "))}</code></p>`,
        ingestState: "unlinked", linkedDoc: null, publishState: "none", publishedInfo: null
      });
      subModal.close();
      state.selectedItemId = newItemId;
      state.expandedIds.add(newItemId);
      ticker(`신규 수집 대상 '${title}' 등록 및 첫 원문 수집 완료 (엔티티는 적재 시 생성됨)`);
      renderAll();
    });

    document.addEventListener("keydown", (e) => {
      if (e.target.matches("input, textarea, select") || document.querySelector("dialog[open]")) return;
      const vis = getVisibleItems();
      if (!vis.length) return;
      const idx = vis.findIndex((x) => x.id === state.selectedItemId);
      if (e.key === "j" || e.key === "k") {
        const nextIdx = e.key === "j" ? Math.min(vis.length - 1, idx + 1) : Math.max(0, idx - 1);
        const target = vis[nextIdx] || vis[0];
        state.selectedItemId = target.id;
        if (prefs.autoMarkReadOnOpen) target.unread = false;
        if (state.layoutMode === "flatlist") { state.expandedIds.clear(); state.expandedIds.add(target.id); }
        renderAll();
      } else if (e.key === "v") {
        const cur = vis[idx >= 0 ? idx : 0];
        if (cur) {
          state.contentTabByItem[cur.id] = getActiveContentTab(cur) === "original" ? "ingested" : "original";
          renderStreamAndReader();
        }
      } else if (e.key === "i") {
        const cur = vis[idx >= 0 ? idx : 0];
        if (cur && cur.ingestState === "unlinked") ingestItemToClaire(cur.id, "");
      } else if (e.key === "p") {
        const cur = vis[idx >= 0 ? idx : 0];
        if (cur) openPublishModal(cur.id);
      }
    });
  }

  function renderAll() {
    applyLayoutAttr();
    renderTopBar();
    renderTree();
    renderStreamAndReader();
  }

  setupPopoverPositionFallback();
  bindGlobalControls();
  renderAll();
  syncLiveThemeDb();
})();
