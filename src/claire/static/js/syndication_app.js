/* Claire Bible — TT-RSS Clean-Room Syndication Shell (App Tabs) & Knowledge-Base Controller */
(function () {
  const ST = window.CLAIRE_SYND_STORAGE;
  const THEMES = JSON.parse(JSON.stringify(window.CLAIRE_SYND_SEEDS || {}));
  const ACCOUNT_TEMPLATES = window.CLAIRE_SYND_ACCOUNT_TEMPLATES || {};
  const cfg = window.__CLAIRE_CONFIG || {};

  /* 목록 스타일 표시 문자열 (i18n). 값 키 list/compact/card/roomy 는 영문 명칭과 같다. */
  const LIST_STYLE_META = {
    list: { icon: "☰", label: { ko: "목록", en: "List" }, desc: { ko: "한 줄 행", en: "Single-line rows" } },
    compact: { icon: "≡", label: { ko: "조밀하게", en: "Compact" }, desc: { ko: "요약 표시 안 함", en: "No summary" } },
    card: { icon: "▤", label: { ko: "일반", en: "Card" }, desc: { ko: "요약 2줄 미리보기", en: "Two-line summary" } },
    roomy: { icon: "▭", label: { ko: "넓게", en: "Roomy" }, desc: { ko: "요약 전체 표시", en: "Full summary" } }
  };
  const UI_LANG = String(document.documentElement.lang || "ko").toLowerCase().startsWith("en") ? "en" : "ko";
  function i18n(entry) { return entry[UI_LANG] || entry.ko; }

  function normalizeListStyle(v) {
    const mapped = (ST.LEGACY_LIST_STYLES || {})[v] || v;
    return ST.LIST_STYLES.includes(mapped) ? mapped : ST.DEFAULT_PREFS.listStyle;
  }

  function normalizePrefs(p) {
    p.listStyle = normalizeListStyle(p.listStyle);
    p.appTab = p.appTab === "kb" ? "kb" : "client";
    /* 직접 연합(Announce) 시절의 값은 연결 계정 부스트(reblog/repost)로 이관한다. */
    if (p.outboundPolicy.apDefaultMode === "announce") p.outboundPolicy.apDefaultMode = "boost";
    return p;
  }

  function loadPrefs() {
    try {
      const p = JSON.parse(localStorage.getItem(ST.PREFS_KEY) || "null");
      if (!p) return JSON.parse(JSON.stringify(ST.DEFAULT_PREFS));
      return normalizePrefs({
        ...ST.DEFAULT_PREFS,
        ...p,
        tagPolicy: { ...ST.DEFAULT_PREFS.tagPolicy, ...(p.tagPolicy || {}) },
        outboundPolicy: { ...ST.DEFAULT_PREFS.outboundPolicy, ...(p.outboundPolicy || {}) }
      });
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
        /* 서버 설정에만 존재하는 테마는 기본 테마의 연결 계정 구성을 복제하되, 계정 식별자와 핸들을 테마별로 분리한다. */
        clone.accounts.forEach((acc) => {
          const oldId = acc.id;
          acc.id = `${oldId}-t${srv.id}`;
          acc.handle = acc.proto === "ap" ? `@theme${srv.id}@${acc.server}` : `@theme${srv.id}.${acc.server}`;
          if (!clone.isPublic) { acc.canPublish = false; acc.scopes = acc.proto === "ap" ? ["read"] : acc.scopes; acc.statusNote = "비공개 테마 — 읽기 전용"; }
          clone.posts.forEach((p) => { if (p.accountId === oldId) p.accountId = acc.id; });
          clone.feeds.forEach((f) => { if (f.via === oldId) f.via = acc.id; });
          clone.items.forEach((it) => { if (it.viaAccount === oldId) it.viaAccount = acc.id; });
        });
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
    if (Array.isArray(cs.extraAccounts)) {
      cs.extraAccounts.forEach((ea) => {
        if (!THEMES[k].accounts.some((a) => a.id === ea.id)) THEMES[k].accounts.push(ea);
      });
    }
  });

  const urlParams = new URLSearchParams(window.location.search);
  const reqTheme = urlParams.get("theme");
  const initThemeKey = (reqTheme && THEMES[reqTheme]) ? reqTheme : (THEMES[String(cfg.activeThemeId)] ? String(cfg.activeThemeId) : "0");
  const reqTab = urlParams.get("tab");

  const state = {
    themeId: initThemeKey,
    appTab: reqTab === "kb" || reqTab === "client" ? reqTab : prefs.appTab,
    layoutMode: prefs.layoutMode === "two-panel" ? "two-panel" : "three-panel",
    listStyle: prefs.listStyle,
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

  /* 연합 클라이언트 탭 컨트롤러 (초기화 마지막 단계에서 CLAIRE_SYND_CLIENT_FACTORY 로 생성) */
  let client = null;
  /* 다른 사용자 발행 본문 (두 탭 공용 · 제안, CLAIRE_SYND_PEERS_FACTORY 로 생성) */
  let peers = null;

  /* AP/AT 수집 대상은 연결 계정의 서버 API 를 경유해 수집한다. */
  const SOURCE_KINDS = {
    ap: [
      { id: "account", label: "계정 게시물", api: "GET /api/v1/accounts/:id/statuses" },
      { id: "hashtag", label: "해시태그", api: "GET /api/v1/timelines/tag/:hashtag" },
      { id: "list", label: "리스트", api: "GET /api/v1/timelines/list/:list_id" }
    ],
    at: [
      { id: "author", label: "작성자 피드", api: "app.bsky.feed.getAuthorFeed" },
      { id: "feed", label: "커스텀 피드", api: "app.bsky.feed.getFeed" },
      { id: "list", label: "리스트", api: "app.bsky.feed.getListFeed" },
      { id: "tag", label: "태그 검색", api: "app.bsky.feed.searchPosts?tag=" }
    ]
  };

  function curTheme() { return THEMES[state.themeId] || THEMES["0"]; }
  function esc(s) { return String(s || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;"); }
  function ticker(msg) {
    const el = document.getElementById("activity-ticker");
    if (el) el.textContent = `${new Date().toLocaleTimeString("ko-KR", { hour12: false })} · ${msg}`;
  }

  /* ── 연결 서버 계정 (ActivityPub = Mastodon Client API · Mastodon 5.0 기능 기준, AT = PDS XRPC) ── */
  function themeAccounts(theme) { return (theme || curTheme()).accounts || []; }
  function findAccount(id, theme) { return themeAccounts(theme).find((a) => a.id === id) || null; }
  function publishAccounts(theme) { return themeAccounts(theme).filter((a) => a.canPublish); }
  function protoDot(proto) { return proto === "ap" ? "🟣" : (proto === "at" ? "🔵" : (proto === "claire" ? "👥" : "🟠")); }
  function accountsSummary(theme) {
    const accs = themeAccounts(theme);
    if (!accs.length) return "연결 계정 없음";
    return accs.map((a) => `${protoDot(a.proto)} ${a.handle}${a.canPublish ? "" : " (읽기 전용)"}`).join(" · ");
  }
  function apBase(acc) { return `https://${acc.server}`; }

  /*
   * ActivityPub 클라이언트 기능 기준은 Mastodon 5.0 이상이 제공하는 기능 전체다 (CLAIRE_SYND_AP_FEATURES).
   * 서버 종류로 연결을 거부하지 않는다. 연결 서버가 제공하지 않는 기능(acc.missingFeatures)만 비활성으로 표시한다.
   */
  function accountSupports(acc, feature) {
    return !!acc && !(acc.missingFeatures || []).includes(feature);
  }
  function atXrpc(acc, nsid) { return `https://${acc.server}/xrpc/${nsid}`; }

  function graphemeLength(text) {
    try {
      if (typeof Intl !== "undefined" && Intl.Segmenter) return [...new Intl.Segmenter("ko", { granularity: "grapheme" }).segment(text)].length;
    } catch (_) {}
    return [...text].length;
  }

  function truncateGraphemes(text, limit) {
    if (graphemeLength(text) <= limit) return text;
    let segs = [...text];
    try {
      if (typeof Intl !== "undefined" && Intl.Segmenter) segs = [...new Intl.Segmenter("ko", { granularity: "grapheme" }).segment(text)].map((s) => s.segment);
    } catch (_) {}
    return `${segs.slice(0, Math.max(0, limit - 1)).join("")}…`;
  }

  /* AT richtext facet 은 UTF-8 바이트 오프셋 기준이므로 한국어 태그도 TextEncoder 로 계산한다. */
  function buildTagFacets(text, tags) {
    const enc = new TextEncoder();
    const facets = [];
    (tags || []).forEach((tag) => {
      const token = `#${tag}`;
      const idx = text.indexOf(token);
      if (idx < 0) return;
      const byteStart = enc.encode(text.slice(0, idx)).length;
      facets.push({ index: { byteStart, byteEnd: byteStart + enc.encode(token).length }, features: [{ $type: "app.bsky.richtext.facet#tag", tag }] });
    });
    return facets;
  }

  function hashtagToken(name) { return `#${String(name).replace(/[\s\-()]+/g, "")}`; }

  /*
   * 연결 계정의 서버 API 요청을 결정론적으로 구성한다 (Claire 서버가 프록시하며 토큰은 브라우저에 노출되지 않는다).
   * target: { proto, remoteId, uri, cid, url, title, description }
   * post op: { text, tags, cw, visibility, language, sensitive, media: [{ alt }], poll: { options, expiresIn, multiple, hideTotals },
   *            scheduledAt, quotePolicy, replyTo, replyRoot, quote, external, editOf }
   */
  function buildServerRequest(acc, op) {
    const nowIso = new Date().toISOString();
    const media = op.media || [];
    if (acc.proto === "ap") {
      if (op.kind === "boost" || op.kind === "favourite" || op.kind === "bookmark") {
        const verb = { boost: "reblog", favourite: "favourite", bookmark: "bookmark" }[op.kind];
        const id = op.target.remoteId || "{id}";
        const req = { method: "POST", url: `${apBase(acc)}/api/v1/statuses/${id}/${op.undo ? "un" : ""}${verb}` };
        if (!op.target.remoteId) req.note = `선행 조회: GET ${apBase(acc)}/api/v2/search?q=${encodeURIComponent(op.target.uri || op.target.url)}&resolve=true&type=statuses → {id}`;
        return req;
      }
      const status = op.text || "";
      const body = { status, visibility: op.visibility || "public", language: op.language || "ko" };
      if (op.cw) body.spoiler_text = op.cw;
      if (op.replyTo && op.replyTo.proto === "ap") body.in_reply_to_id = op.replyTo.remoteId || "{id}";
      if (op.quote) {
        if (op.quote.proto === "ap" && op.quote.remoteId && accountSupports(acc, "quote")) {
          body.quoted_status_id = op.quote.remoteId;
        } else if (op.quote.url && !status.includes(op.quote.url)) {
          body.status = `${status}\n${op.quote.url}`.trim();
        }
      }
      if (media.length) {
        body.media_ids = media.map((_, i) => `{media_id_${i + 1}}`);
        if (op.sensitive) body.sensitive = true;
      } else if (op.poll && op.poll.options && op.poll.options.length >= 2) {
        body.poll = { options: op.poll.options, expires_in: op.poll.expiresIn || 86400, multiple: !!op.poll.multiple, hide_totals: !!op.poll.hideTotals };
      }
      if (op.quotePolicy && accountSupports(acc, "quote") && !op.editOf) body.quote_approval_policy = op.quotePolicy;
      if (op.scheduledAt && !op.editOf) body.scheduled_at = op.scheduledAt;
      const req = op.editOf
        ? { method: "PUT", url: `${apBase(acc)}/api/v1/statuses/${op.editOf}`, body }
        : { method: "POST", url: `${apBase(acc)}/api/v1/statuses`, headers: { "Idempotency-Key": `claire-${Date.now().toString(36)}` }, body };
      if (media.length) req.note = `선행: POST ${apBase(acc)}/api/v2/media × ${media.length} (file, description=대체 텍스트) → media_ids`;
      return req;
    }

    const url = atXrpc(acc, op.undo ? "com.atproto.repo.deleteRecord" : "com.atproto.repo.createRecord");
    if (op.kind === "boost" || op.kind === "favourite") {
      const collection = op.kind === "boost" ? "app.bsky.feed.repost" : "app.bsky.feed.like";
      if (op.undo) return { method: "POST", url, body: { repo: acc.did, collection, rkey: "{rkey}" } };
      return { method: "POST", url, body: { repo: acc.did, collection, record: { $type: collection, subject: { uri: op.target.uri, cid: op.target.cid || "{cid}" }, createdAt: nowIso } } };
    }
    if (op.kind === "bookmark") return null;
    const text = truncateGraphemes(op.text || "", acc.charLimit || 300);
    const record = { $type: "app.bsky.feed.post", text, langs: [op.language || "ko"], createdAt: nowIso };
    const facets = buildTagFacets(text, op.tags);
    if (facets.length) record.facets = facets;
    if (op.replyTo && op.replyTo.proto === "at") {
      const root = op.replyRoot || op.replyTo;
      record.reply = { root: { uri: root.uri, cid: root.cid || "{cid}" }, parent: { uri: op.replyTo.uri, cid: op.replyTo.cid || "{cid}" } };
    }
    const images = media.length ? { $type: "app.bsky.embed.images", images: media.slice(0, 4).map((m) => ({ alt: m.alt || "", image: "{blob}" })) } : null;
    let embed = null;
    if (op.quote && op.quote.proto === "at" && op.quote.uri) {
      embed = { $type: "app.bsky.embed.record", record: { uri: op.quote.uri, cid: op.quote.cid || "{cid}" } };
    } else if (op.quote || op.external) {
      const ext = op.external || op.quote;
      embed = { $type: "app.bsky.embed.external", external: { uri: ext.url, title: ext.title || "", description: ext.description || "" } };
    }
    if (images && embed && embed.$type === "app.bsky.embed.record") record.embed = { $type: "app.bsky.embed.recordWithMedia", record: embed, media: images };
    else if (images) record.embed = images;
    else if (embed) record.embed = embed;
    const req = { method: "POST", url, body: { repo: acc.did, collection: "app.bsky.feed.post", record } };
    if (images) req.note = `선행: ${atXrpc(acc, "com.atproto.repo.uploadBlob")} × ${images.images.length} → {blob}`;
    return req;
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
    if (state.treeSel.kind === "peer") return [];
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

  function listStyleLabel(style) {
    const m = LIST_STYLE_META[style] || LIST_STYLE_META.list;
    return `${m.icon} ${i18n(m.label)}`;
  }

  function applyLayoutAttr() {
    document.body.setAttribute("data-app-tab", state.appTab);
    document.body.setAttribute("data-layout", state.layoutMode);
    document.body.setAttribute("data-list-style", state.listStyle);

    document.querySelectorAll('[role="tab"][data-app-tab-btn]').forEach((btn) => {
      const on = btn.getAttribute("data-app-tab-btn") === state.appTab;
      btn.setAttribute("aria-selected", on ? "true" : "false");
      btn.tabIndex = on ? 0 : -1;
    });
    document.querySelectorAll("[data-app-panel]").forEach((panel) => {
      panel.hidden = panel.getAttribute("data-app-panel") !== state.appTab;
    });

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

    const prefLink = document.getElementById("menu-pref-link");
    const topPrefLink = document.getElementById("top-pref-link");
    const href = `/syndication/preferences?theme=${encodeURIComponent(state.themeId)}`;
    if (prefLink) prefLink.href = href;
    if (topPrefLink) topPrefLink.href = href;
  }

  function renderTabBar() {
    const theme = curTheme();
    const clientUnread = (theme.posts || []).filter((p) => p.unread).length;
    const kbUnread = theme.items.filter((i) => i.unread).length;
    const cEl = document.getElementById("tab-client-count");
    if (cEl) { cEl.textContent = String(clientUnread); cEl.classList.toggle("fresh", clientUnread > 0); }
    const kEl = document.getElementById("tab-kb-count");
    if (kEl) { kEl.textContent = String(kbUnread); kEl.classList.toggle("fresh", kbUnread > 0); }

    const accEl = document.getElementById("tabbar-accounts");
    if (accEl) {
      const accs = themeAccounts(theme);
      accEl.innerHTML = accs.length
        ? accs.map((a) => `<button type="button" class="tt-acct-chip status-${esc(a.status || "ok")}" data-goto-account="${esc(a.id)}" title="${esc(a.software)}${a.serverVersion ? ` ${esc(a.serverVersion)}` : ""} · ${esc(a.auth)} · scope: ${esc(a.scopes.join(" "))}${a.statusNote ? ` · ${esc(a.statusNote)}` : ""}"><span class="tt-acct-dot" aria-hidden="true"></span>${protoDot(a.proto)} ${esc(a.server)}${a.canPublish ? "" : " · 읽기 전용"}</button>`).join("")
        : `<span class="tt-acct-none">연결된 서버 계정 없음</span>`;
      accEl.querySelectorAll("[data-goto-account]").forEach((b) => b.addEventListener("click", () => {
        if (client) client.openAccount(b.getAttribute("data-goto-account"));
        setAppTab("client");
      }));
    }
  }

  function renderTopBar() {
    const sel = document.getElementById("theme-selector");
    sel.innerHTML = Object.values(THEMES).map((t) => `<option value="${t.id}" ${String(t.id) === String(state.themeId) ? "selected" : ""}>${esc(t.icon)} ${esc(t.label)}</option>`).join("");
    document.getElementById("active-stream-title").textContent = state.appTab === "client" && client ? client.title() : state.treeSel.label;
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
        const pIcon = protoDot(f.proto);
        const act = state.treeSel.kind === "feed" && state.treeSel.value === f.id ? "active" : "";
        const viaAcc = f.via ? findAccount(f.via, theme) : null;
        const tip = viaAcc ? `${f.title} — 연결 계정 ${viaAcc.handle} 경유 · ${f.source || ""}` : `${f.title} — ${f.url}`;
        return `<button type="button" class="tt-tree-item ${act}" data-tree-kind="feed" data-tree-val="${esc(f.id)}" data-tree-lbl="${esc(f.title)}" title="${esc(tip)}"><span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${pIcon} ${esc(f.title)}${viaAcc ? ` <small class="tt-via">via ${esc(viaAcc.server)}</small>` : ""}</span><span class="tt-count-pill ${cnt > 0 ? "fresh" : ""}">${cnt}</span></button>`;
      }).join("");
      return `<details class="tt-folder" open><summary><span><span class="tt-folder-marker"></span>📁 ${esc(folder)}</span><span class="tt-count-pill">${folderUnread}</span></summary><div class="tt-tree-children">${children}</div></details>`;
    }).join("");

    const peerTree = document.getElementById("tree-peers");
    if (peerTree) peerTree.innerHTML = peers ? peers.treeHtml("kb", state.treeSel.kind === "peer" ? state.treeSel.value : null) : "";

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
    const viaAcc = item.viaAccount ? findAccount(item.viaAccount, theme) : null;
    const clientPost = client ? client.findPostForKbItem(item) : null;
    const pubLabel =item.publishState === "boosted" ? "🔁 부스트/인용 완료" : (item.publishState === "published_create" ? "📡 적재 발행 완료" : "🔁 부스트·인용 / 📡 적재 발행");

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
          <span><strong>${esc(feed.title)}</strong> · 작성자: ${esc(item.author)} · ${esc(item.timeAgo)} · 프로토콜: <code>${esc(item.format)}</code>${viaAcc ? ` · 경유 계정 <code>${esc(viaAcc.handle)}</code>` : ""} · 연관도 <b>+${item.score}</b></span>
          <span style="display:inline-flex;gap:10px;align-items:center">
            ${clientPost ? `<button type="button" class="tt-linklike" data-open-client-post="${esc(clientPost.id)}" title="연합 클라이언트 탭에서 원 게시물의 스레드·반응 보기">🌐 연합 클라이언트에서 스레드 보기</button>` : ""}
            <a href="${esc(item.url)}" target="_blank" rel="noopener noreferrer" style="color:var(--accent);text-decoration:none;font-weight:600">원문 링크 열기 ↗</a>
          </span>
        </div>
      </header>

      ${peers ? peers.provenanceHtml(item) : ""}

      <div class="tt-view-switcher" role="region" aria-label="수집 원문 및 적재 본문 전환">
        <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
          <div class="tt-seg" role="group" aria-label="본문 표시 모드">
            <button type="button" class="tt-seg-btn ${activeTab === "original" ? "active" : ""}" data-switch-body="${esc(item.id)}" data-target-tab="original">📄 수집 원문 (Original)</button>
            <button type="button" class="tt-seg-btn ${activeTab === "ingested" ? "active" : ""}" data-switch-body="${esc(item.id)}" data-target-tab="ingested">📚 적재 본문 (Claire 정제본${hasLinked ? " · 보유" : " · 미적재"})</button>
          </div>
          ${hasLinked ? `<span class="tt-link-dot linked">🔗 ${esc(item.linkedDoc.docId)} 적재 완료 (엔티티 생성됨)</span>` : `<button type="button" class="tt-btn tt-btn-pri" data-do-ingest="${esc(item.id)}">📥 프로젝트 적재 전달</button>`}
        </div>
        <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap">
          ${publishAccounts(theme).length
            ? `<button type="button" class="tt-btn tt-btn-ap" data-open-pub="${esc(item.id)}">${pubLabel}</button>`
            : `<span style="font-size:11.5px;color:var(--muted)">🔒 발행 권한이 있는 연결 계정 없음 (읽기 전용)</span>`}
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

    if (state.treeSel.kind === "peer" && peers) {
      const key = state.treeSel.value;
      container.innerHTML = `<div class="tt-stream-head">${peers.streamHeadHtml(key)}</div>${peers.streamHtml(key)}`;
      document.getElementById("reader-content").innerHTML = peers.readerHtml(key);
      document.getElementById("footer-stats").textContent = `Theme #${theme.id} · ${peers.footerText(key)} · ${accountsSummary(theme)}`;
      return;
    }

    if (visible.length === 0) {
      container.innerHTML = `<div style="padding:42px 20px;text-align:center;color:var(--muted);font-size:13px">조건에 일치하는 수집 아티클이 없습니다.</div>`;
    } else {
      container.innerHTML = visible.map((it) => {
        const feed = theme.feeds.find((f) => f.id === it.feedId) || { title: it.format, proto: it.proto };
        const isSel = state.selectedItemId === it.id;
        const checked = state.checkedIds.has(it.id) ? "checked" : "";
        const pDot = it.proto === "ap" ? "🟣" : (it.proto === "at" ? "🔵" : "🟠");
        const pubIcon = it.publishState === "boosted" ? "🔁" : (it.publishState === "published_create" ? "📡" : "rss");
        const linkBadge = it.ingestState === "linked"
          ? `<span class="tt-link-dot linked" title="프로젝트 DB 적재 완료 (엔티티 생성됨)">🔗 적재</span>`
          : `<span class="tt-link-dot unlinked" title="미적재 수집 원문 (적재 시 엔티티 생성)">○</span>`;

        if (state.listStyle !== "list") {
          // 다단 카드 계열: 조밀하게(compact, 요약 없음) · 일반(card, 요약 2줄) · 넓게(roomy, 요약 전체)
          const pubBadge = it.publishState === "boosted"
            ? `<span class="tt-btn-ap" style="font-size:10.5px;padding:1px 6px;border-radius:4px" title="ActivityPub Announce 부스트됨">🔁 AP 부스트됨</span>`
            : (it.publishState === "published_create"
              ? `<span class="tt-btn-pri" style="font-size:10.5px;padding:1px 6px;border-radius:4px" title="Claire 적재 본문 ActivityPub 게시됨">📡 적재 발행됨</span>`
              : "");
          const cardLinkBadge = it.ingestState === "linked" && it.linkedDoc
            ? `<span class="tt-link-dot linked" title="Claire 프로젝트 DB 적재 연동됨">🔗 적재 · ${esc(it.linkedDoc.docId)}</span>`
            : `<span class="tt-link-dot unlinked">○ 미적재</span>`;

          const tagChips = it.ingestState === "linked"
            ? getLinkedDocEntities(it).map((e) => `<span class="tt-entity-chip resolved" title="적재 시 생성된 엔티티">[[${esc(e.name)}]] <small style="opacity:0.8">(${esc(e.type || "Concept")})</small></span>`).join("")
            : getItemSourceTags(it).map((t) => `<span class="tt-entity-chip candidate" title="외부 피드 태그 (적재 시 엔티티로 승격)">#${esc(t)}</span>`).join("");

          const quickIngestBtn = it.ingestState === "unlinked"
            ? `<button type="button" data-quick-ingest="${esc(it.id)}" class="tt-btn tt-btn-pri" style="height:23px;padding:0 7px;font-size:11px" title="현재 테마 프로젝트 DB로 즉시 적재 전달">➕ 적재 전달</button>`
            : "";
          const canBoostNative = publishAccounts(theme).some((a) => a.proto === it.proto);
          const quickPublishBtn = publishAccounts(theme).length && it.publishState === "none"
            ? `<button type="button" data-quick-publish="${esc(it.id)}" class="tt-btn" style="height:23px;padding:0 7px;font-size:11px">${canBoostNative ? "🔁 부스트" : "📡 발행"}</button>`
            : "";

          let summaryHtml = "";
          if (state.listStyle === "card") {
            summaryHtml = `<p class="tt-card-snippet">${esc(it.rawBody)}</p>`;
          } else if (state.listStyle === "roomy") {
            summaryHtml = `<p class="tt-card-snippet is-full">${esc(it.rawBody)}</p>`;
            if (it.ingestState === "linked" && it.linkedDoc && it.linkedDoc.summary) {
              summaryHtml += `<p class="tt-card-claire-summary"><strong>📌 Claire 적재 요약</strong> ${esc(it.linkedDoc.summary)}</p>`;
            }
          }

          return `<article class="tt-card-row ${it.unread ? "is-unread" : ""} ${isSel ? "is-selected" : ""}" data-card-id="${esc(it.id)}">
            <div class="tt-card-topbar">
              <div class="tt-card-meta-left">
                <input type="checkbox" class="tt-row-check" data-check-id="${esc(it.id)}" ${checked} aria-label="${esc(it.title)} 선택"/>
                <button type="button" class="tt-icon-btn ${it.starred ? "starred" : ""}" data-star-id="${esc(it.id)}" title="별표 토글">${it.starred ? "★" : "☆"}</button>
                <span class="tt-feed-pill proto-${esc(it.proto)}">${esc(it.format)}</span>
                <span class="tt-card-feed-title">${esc(feed.title)}</span>
                <span style="font-size:11px;color:var(--muted)">·</span>
                <span class="tt-card-score" title="테마 DB 연관도 점수">+${it.score}</span>
              </div>
              <div class="tt-card-meta-right">
                ${cardLinkBadge}
                ${pubBadge}
                <span class="tt-card-time">${esc(it.timeAgo)}</span>
              </div>
            </div>

            <h3 class="tt-card-title">${esc(it.title)}</h3>
            ${summaryHtml}

            <div class="tt-card-bottom">
              <div class="tt-card-tags">
                ${tagChips || `<span style="font-size:11px;color:var(--muted)">태그 없음</span>`}
              </div>
              <div class="tt-card-actions">
                ${quickIngestBtn}
                ${quickPublishBtn}
                <a class="tt-ext-link" href="${esc(it.url)}" target="_blank" rel="noopener noreferrer" title="원문 링크 새 탭 열기">↗</a>
              </div>
            </div>
          </article>`;
        } else {
          // 목록(list): TT-RSS FlatList 단일 행 (34px)
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
          </div>`;
        }
      }).join("");
    }

    const readerPane = document.getElementById("reader-content");
    const selItem = theme.items.find((x) => x.id === state.selectedItemId) || visible[0];
    readerPane.innerHTML = selItem ? buildReaderHtml(selItem) : `<div style="padding:36px;color:var(--muted);font-size:13px">목록에서 읽을 아티클을 선택하세요.</div>`;

    const linkedTotal = theme.items.filter((x) => x.ingestState === "linked").length;
    document.getElementById("footer-stats").textContent = `Theme #${theme.id} (${theme.dbPath}) · 총 ${theme.items.length}건 · 🔗 적재완료 ${linkedTotal}건 · ${accountsSummary(theme)}`;
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

    document.querySelectorAll("[data-card-id], [data-click-row]").forEach((el) => {
      el.addEventListener("click", (e) => {
        if (e.target.closest("button") || e.target.closest("input") || e.target.closest("a")) return;
        const id = el.getAttribute("data-card-id") || el.getAttribute("data-click-row");
        const it = curTheme().items.find((x) => x.id === id);
        state.selectedItemId = id;
        if (it && prefs.autoMarkReadOnOpen) it.unread = false;
        hydrateLiveDocumentIfNeeded(it);
        renderAll();
      });
    });

    document.querySelectorAll("[data-quick-ingest]").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        const id = btn.getAttribute("data-quick-ingest");
        state.selectedItemId = id;
        ingestItemToClaire(id, "");
      });
    });

    document.querySelectorAll("[data-quick-publish]").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        const id = btn.getAttribute("data-quick-publish");
        state.selectedItemId = id;
        openPublishModal(id);
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

    document.querySelectorAll("#panel-kb [data-open-client-post]").forEach((btn) => {
      btn.addEventListener("click", () => {
        if (!client) return;
        client.openPost(btn.getAttribute("data-open-client-post"));
        setAppTab("client");
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

  /*
   * 발행은 Claire 자체 액터가 아니라 테마에 연결된 서버 계정(들)을 통해 수행한다.
   * - 부스트: 항목과 같은 프로토콜의 계정에서만 가능 (AP reblog / AT repost)
   * - 인용: 같은 프로토콜이면 네이티브 인용(quoted_status_id / embed.record), 아니면 원문 링크 인용
   * - 적재 발행: Claire 적재 본문 요약 + 공개 공유 링크 + 엔티티 해시태그로 새 글 작성
   */
  function openPublishModal(itemId) {
    const theme = curTheme();
    const item = theme.items.find((x) => x.id === itemId);
    if (!item) return;
    /* 다른 사용자 본문에서 가져온 항목은 원 게시물 부스트 · 인용과 원문 표시가 필요하므로 전용 발행 창을 쓴다. */
    if (item.peerPubId && peers) { peers.openReshare(item.peerPubId); return; }
    const pubAccs = publishAccounts(theme);
    if (!pubAccs.length) {
      ticker("🔒 이 테마에는 발행 권한(write scope)이 있는 연결 계정이 없습니다 — 읽기 전용 계정으로는 외부 발행할 수 없습니다.");
      return;
    }

    const modal = document.getElementById("modal-publish");
    const box = document.getElementById("modal-publish-body");
    const nativeAcc = pubAccs.find((a) => a.proto === item.proto) || null;
    const policy = prefs.outboundPolicy;
    let mode = item.proto === "ap" ? policy.apDefaultMode : (item.proto === "at" ? policy.atDefaultMode : policy.rssDefaultMode);
    if (mode === "announce") mode = "boost";
    if (mode === "boost" && !nativeAcc) mode = "quote_boost";
    const selected = new Set(nativeAcc ? [nativeAcc.id] : pubAccs.map((a) => a.id));
    let commentText = `핵심 인사이트 공유: ${item.title}`;

    function itemTarget() {
      return {
        proto: item.proto,
        remoteId: item.proto === "ap" ? item.remoteId : null,
        uri: item.proto === "ap" ? item.apObjectUri : item.atUri,
        cid: item.atCid,
        url: item.url,
        title: item.title,
        description: item.rawBody
      };
    }

    function tagNames() {
      if (!prefs.tagPolicy.attachEntityHashtagsOnPublish) return [];
      const isIngested = item.ingestState === "linked" && item.linkedDoc;
      const names = isIngested ? getLinkedDocEntities(item).map((e) => e.name) : getItemSourceTags(item);
      return names.map((n) => hashtagToken(n).slice(1));
    }

    function requestsFor(curMode, comment) {
      const tags = tagNames();
      const hashText = tags.map((t) => `#${t}`).join(" ");
      return pubAccs.filter((a) => selected.has(a.id)).map((acc) => {
        let effMode = curMode;
        let fallback = "";
        if (curMode === "boost" && acc.proto !== item.proto) {
          effMode = "quote_boost";
          fallback = "교차 프로토콜 부스트 불가 → 원문 링크 인용으로 대체";
        }
        let req;
        if (effMode === "boost") {
          req = buildServerRequest(acc, { kind: "boost", target: itemTarget() });
        } else if (effMode === "quote_boost") {
          const tpl = String(policy.quoteTemplate || "📌 {{title}} — {{url}} {{hashtags}}");
          const native = acc.proto === item.proto;
          const quoteLine = tpl
            .replace(/\{\{title\}\}/g, item.title)
            .replace(/\{\{url\}\}/g, native ? "" : item.url)
            .replace(/\{\{author\}\}/g, item.author || "")
            .replace(/\{\{hashtags\}\}/g, hashText)
            .replace(/\s+—\s+(?=\s|#|$)/, " ")
            .trim();
          const text = `${comment ? `${comment}\n\n` : ""}${quoteLine}`.trim();
          req = buildServerRequest(acc, { kind: "post", text, tags, quote: itemTarget() });
          if (native && acc.proto === "ap" && !accountSupports(acc, "quote")) fallback = "연결 서버가 인용 게시물을 제공하지 않음 → 링크 인용";
          else if (native) fallback = acc.proto === "ap" ? "네이티브 인용 (quoted_status_id · 원 작성자가 인용을 허용하지 않으면 링크 인용으로 대체)" : "네이티브 인용 (app.bsky.embed.record)";
        } else {
          const doc = item.linkedDoc;
          const summary = doc ? doc.summary : item.rawBody;
          const shareUrl = doc && policy.includeCanonicalShareLink && String(doc.shareUrl || "").startsWith("/") ? `${window.location.origin}${doc.shareUrl}` : "";
          const tail = `${shareUrl ? `\n🔗 ${shareUrl}` : ""}${hashText ? `\n${hashText}` : ""}`;
          const budget = Math.max(40, (acc.charLimit || 500) - graphemeLength(tail) - (acc.proto === "at" ? 0 : 4));
          const body = truncateGraphemes(`📡 ${item.title}\n${summary}`, budget);
          req = buildServerRequest(acc, {
            kind: "post",
            text: acc.proto === "at" ? `${body}${hashText ? `\n${hashText}` : ""}` : `${body}${tail}`,
            tags,
            external: acc.proto === "at" && shareUrl ? { url: shareUrl, title: item.title, description: summary } : null
          });
        }
        return { acc, req, note: fallback };
      });
    }

    function previewHtml(reqs) {
      return reqs.length ? reqs.map((r) => `<div class="tt-req">
          <div class="tt-req-head">${protoDot(r.acc.proto)} <b>${esc(r.acc.handle)}</b>${r.note ? ` <span class="tt-req-note">${esc(r.note)}</span>` : ""}</div>
          <pre>${esc(`${r.req.method} ${r.req.url}`)}${r.req.note ? `\n# ${esc(r.req.note)}` : ""}${r.req.body ? `\n${esc(JSON.stringify(r.req.body, null, 2))}` : ""}</pre>
        </div>`).join("") : `<div class="tt-summary-callout" style="margin:0">발행할 계정을 하나 이상 선택하세요.</div>`;
    }

    function drawModal() {
      const hasLinked = item.ingestState === "linked" && !!item.linkedDoc;
      const reqs = requestsFor(mode, commentText);
      const accRows = pubAccs.map((a) => `<label class="tt-acc-pick ${selected.has(a.id) ? "on" : ""}">
          <input type="checkbox" data-pub-acc="${esc(a.id)}" ${selected.has(a.id) ? "checked" : ""}/>
          <span>${protoDot(a.proto)} <b>${esc(a.handle)}</b> <small>${esc(a.software)} · ${a.proto === "at" ? `${a.charLimit} graphemes` : `${a.charLimit}자`}</small></span>
        </label>`).join("");
      const readonlyAccs = themeAccounts(theme).filter((a) => !a.canPublish);

      box.innerHTML = `<div style="display:flex;justify-content:space-between;align-items:flex-start;gap:10px">
        <div>
          <h2 id="modal-publish-title" style="margin:0;font-size:16px;font-weight:700">연결 계정으로 발행 · 부스트 (${esc(item.format)})</h2>
          <p style="margin:4px 0 0;font-size:12px;color:var(--muted)">Claire 는 연합 서버를 직접 운영하지 않습니다. 아래 연결 계정의 <b>서버 API</b>로 <b>부스트 / 인용 발행</b> 또는 <b>적재 발행</b>을 요청합니다.</p>
        </div>
        <button type="button" id="pub-close-x" class="tt-btn">✕</button>
      </div>

      <fieldset class="tt-fieldset">
        <legend>발행할 연결 계정 (교차 게시 가능)</legend>
        <div class="tt-acc-pick-row">${accRows}</div>
        ${readonlyAccs.length ? `<div style="font-size:11px;color:var(--muted)">읽기 전용 계정 제외: ${readonlyAccs.map((a) => esc(a.handle)).join(", ")}</div>` : ""}
      </fieldset>

      <div class="tt-seg" style="display:grid;grid-template-columns:1fr 1fr 1fr">
        <button type="button" class="tt-seg-btn ${mode === "boost" ? "active" : ""}" data-pub-mode="boost" ${nativeAcc ? "" : "disabled title=\"같은 프로토콜의 연결 계정이 있어야 부스트할 수 있습니다\""}>🔁 부스트 (${item.proto === "at" ? "repost" : "reblog"})</button>
        <button type="button" class="tt-seg-btn ${mode === "quote_boost" ? "active" : ""}" data-pub-mode="quote_boost">💬 부스트 / 인용 발행</button>
        <button type="button" class="tt-seg-btn ${mode === "ingested_create" ? "active" : ""}" data-pub-mode="ingested_create">📡 적재 발행 (Claire 적재 본문)</button>
      </div>

      ${mode === "quote_boost" ? `<div><label for="pub-quote-comment" style="display:block;font-size:12px;font-weight:700;margin-bottom:4px">인용 코멘트 (선택)</label><input id="pub-quote-comment" type="text" class="tt-search" style="width:100%" value="${esc(commentText)}"/></div>` : ""}

      ${mode === "ingested_create" && !hasLinked ? `<div class="tt-summary-callout" style="margin:0">아직 미적재 항목입니다. 아래 <b>적재 후 즉시 적재 발행</b>을 누르면 프로젝트 적재(엔티티 생성)와 연결 계정 발행을 연속 수행합니다.</div>` : ""}
      ${mode === "ingested_create" ? `<div style="font-size:11.5px;color:var(--muted)">👥 <span class="peer-proposal">제안</span> ${theme.isPublic ? "공개 테마의 적재 발행은 인스턴스 공용 발행 색인에도 올라가, 같은 Claire 의 다른 사용자가 목록에서 보고 자기 계정으로 부스트 · 인용 · 가져오기 할 수 있습니다. 재사용 범위는 환경설정 › 부스트 · 적재 발행에서 정합니다." : "비공개 테마의 적재 발행은 공용 발행 색인에 올라가지 않습니다."}</div>` : ""}

      <div>
        <div style="font-size:11.5px;font-weight:700;color:var(--muted);margin-bottom:4px">연결 서버 API 요청 미리보기 (Claire 서버 프록시 경유 · 토큰은 서버에만 보관)</div>
        <div id="pub-req-preview">${previewHtml(reqs)}</div>
      </div>

      <div style="display:flex;justify-content:flex-end;gap:8px;padding-top:10px;border-top:1px solid var(--border)">
        <button type="button" id="pub-cancel" class="tt-btn">취소</button>
        <button type="button" id="pub-submit" class="tt-btn ${mode === "ingested_create" ? "tt-btn-pri" : "tt-btn-ap"}" ${reqs.length ? "" : "disabled"}>${mode === "ingested_create" && !hasLinked ? "📥 적재 후 즉시 적재 발행" : (mode === "ingested_create" ? "📡 적재 본문 발행 실행" : (mode === "boost" ? "🔁 부스트 실행" : "💬 인용 발행 실행"))}</button>
      </div>`;

      box.querySelector("#pub-close-x").addEventListener("click", () => modal.close());
      box.querySelector("#pub-cancel").addEventListener("click", () => modal.close());
      box.querySelectorAll("[data-pub-mode]").forEach((b) => b.addEventListener("click", () => { mode = b.getAttribute("data-pub-mode"); drawModal(); }));
      box.querySelectorAll("[data-pub-acc]").forEach((chk) => chk.addEventListener("change", () => {
        const id = chk.getAttribute("data-pub-acc");
        if (chk.checked) selected.add(id); else selected.delete(id);
        drawModal();
      }));
      const cInput = box.querySelector("#pub-quote-comment");
      if (cInput) cInput.addEventListener("input", (e) => {
        commentText = e.target.value;
        box.querySelector("#pub-req-preview").innerHTML = previewHtml(requestsFor(mode, commentText));
      });
      box.querySelector("#pub-submit").addEventListener("click", () => {
        const reqs = requestsFor(mode, commentText);
        if (!reqs.length) return;
        if (mode === "ingested_create" && item.ingestState !== "linked") {
          ingestItemToClaire(item.id, "");
        }
        item.publishState = mode === "ingested_create" ? "published_create" : "boosted";
        item.publishedInfo = { mode, at: "방금 전", accounts: reqs.map((r) => r.acc.id) };
        if (client) client.recordOutbound(item, mode, reqs.map((r) => r.acc));
        modal.close();
        ticker(`${mode === "ingested_create" ? "📡 적재 발행" : (mode === "boost" ? "🔁 부스트" : "💬 인용 발행")} 완료 → ${reqs.map((r) => r.acc.handle).join(", ")} · ${item.title}`);
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

  const APP_TABS = ["client", "kb"];
  const TAB_HINTS = {
    client: "연합 클라이언트 단축키: j/k(이동), r(답글), b(부스트), f(좋아요), q(인용), x(CW 펼치기), h(민감한 미디어), /(검색), n(새 글), s(수집함 보내기), i(보내고 적재), l(목록 스타일), m(분할), 1/2(탭)",
    kb: "지식베이스 단축키: j/k(이동), l(목록 스타일), m(3단/2단), v(원문⇄적재), i(적재 전달), p(부스트·발행), 1/2(탭)"
  };

  function setAppTab(tab) {
    const next = APP_TABS.includes(tab) ? tab : "client";
    const changed = state.appTab !== next;
    state.appTab = next;
    prefs.appTab = next;
    savePrefs();
    closeOpenPopovers();
    if (changed) ticker(TAB_HINTS[next]);
    renderAll();
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

    document.getElementById("theme-selector").addEventListener("change", (e) => {
      state.themeId = e.target.value;
      state.treeSel = { kind: "virtual", value: "all", label: "전체 아티클 (All articles)" };
      state.checkedIds.clear();
      state.expandedIds.clear();
      const first = curTheme().items[0];
      state.selectedItemId = first ? first.id : null;
      if (client) client.onThemeChange();
      syncLiveThemeDb();
    });

    document.querySelectorAll("[data-app-tab-btn]").forEach((btn) => {
      btn.addEventListener("click", () => setAppTab(btn.getAttribute("data-app-tab-btn")));
    });
    const tablist = document.getElementById("app-tablist");
    if (tablist) {
      tablist.addEventListener("keydown", (e) => {
        const tabs = APP_TABS;
        const idx = tabs.indexOf(state.appTab);
        let next = null;
        if (e.key === "ArrowRight") next = tabs[(idx + 1) % tabs.length];
        else if (e.key === "ArrowLeft") next = tabs[(idx - 1 + tabs.length) % tabs.length];
        else if (e.key === "Home") next = tabs[0];
        else if (e.key === "End") next = tabs[tabs.length - 1];
        if (!next) return;
        e.preventDefault();
        setAppTab(next);
        const btn = document.querySelector(`[data-app-tab-btn="${next}"]`);
        if (btn) btn.focus();
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
    const subProto = document.getElementById("sub-proto");
    const subVia = document.getElementById("sub-via");
    const subSource = document.getElementById("sub-source");

    function refreshSubscribeForm() {
      const proto = subProto.value;
      const viaWrap = document.getElementById("sub-via-wrap");
      viaWrap.hidden = proto === "rss";
      if (proto === "rss") return;
      const accs = themeAccounts().filter((a) => a.proto === proto);
      subVia.innerHTML = accs.length
        ? accs.map((a) => `<option value="${esc(a.id)}">${protoDot(a.proto)} ${esc(a.handle)}${a.canPublish ? "" : " (읽기 전용)"}</option>`).join("")
        : `<option value="">연결된 ${proto === "ap" ? "ActivityPub" : "AT"} 계정 없음 — 연합 클라이언트 탭에서 먼저 연결</option>`;
      subSource.innerHTML = SOURCE_KINDS[proto].map((k) => `<option value="${esc(k.id)}">${esc(k.label)} · ${esc(k.api)}</option>`).join("");
      document.getElementById("sub-url").placeholder = proto === "ap" ? "@user@server.example 또는 #해시태그" : "handle.bsky.social 또는 at://… 피드 URI";
    }

    function openSubscribe() {
      closeOpenPopovers();
      refreshSubscribeForm();
      subModal.showModal();
    }
    subProto.addEventListener("change", refreshSubscribeForm);
    document.getElementById("menu-open-subscribe").addEventListener("click", openSubscribe);
    document.getElementById("btn-open-subscribe").addEventListener("click", openSubscribe);

    document.getElementById("form-subscribe").addEventListener("submit", (e) => {
      e.preventDefault();
      const proto = subProto.value;
      const url = document.getElementById("sub-url").value.trim();
      const title = document.getElementById("sub-title").value.trim();
      const folder = document.getElementById("sub-folder").value.trim() || "기본 폴더";
      const tagsRaw = document.getElementById("sub-tags").value.trim();
      if (!url || !title) return;
      const viaAcc = proto === "rss" ? null : findAccount(subVia.value);
      if (proto !== "rss" && !viaAcc) {
        ticker(`⚠️ ${proto === "ap" ? "ActivityPub" : "AT Protocol"} 수집 대상은 연결 계정을 경유해야 합니다. 연합 클라이언트 탭에서 서버 계정을 먼저 연결하세요.`);
        return;
      }
      const sourceKind = viaAcc ? SOURCE_KINDS[proto].find((k) => k.id === subSource.value) || SOURCE_KINDS[proto][0] : null;
      const fid = `f_custom_${Date.now()}`;
      const newFeed = viaAcc
        ? { id: fid, folder, proto, format: proto === "ap" ? "ActivityPub" : "AT Protocol", interval: proto === "ap" ? "stream" : "15m", title, handle: url, url, via: viaAcc.id, source: `${sourceKind.label} · ${sourceKind.api}`, etag: "연결 계정 경유 · 방금 수집됨" }
        : { id: fid, folder, proto, format: "RSS/ATOM", interval: "15m", title, url, etag: "방금 수집됨" };
      addKbFeed(newFeed);

      const sourceTags = tagsRaw ? tagsRaw.split(",").map((s) => s.trim()).filter(Boolean) : [title];
      const newItemId = `it_custom_${Date.now()}`;
      curTheme().items.unshift({
        id: newItemId, feedId: fid, proto, format: proto === "ap" ? "AP" : (proto === "at" ? "AT-URI" : "RSS"), viaAccount: viaAcc ? viaAcc.id : undefined,
        title: `[신규 수집] ${title} 첫 수집 컨텐트`, author: title,
        apObjectUri: proto === "ap" ? `https://federated.example/notes/${Date.now()}` : undefined,
        atUri: proto === "at" ? `at://did:plc:newsource/app.bsky.feed.post/${Date.now().toString(36)}` : undefined,
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
      ticker(`신규 수집 대상 '${title}' 등록${viaAcc ? ` (경유: ${viaAcc.handle})` : ""} 및 첫 원문 수집 완료 (엔티티는 적재 시 생성됨)`);
      renderAll();
    });

    document.addEventListener("keydown", (e) => {
      if (e.target.matches("input, textarea, select") || document.querySelector("dialog[open]")) return;
      if (e.ctrlKey || e.metaKey || e.altKey) return;

      if (e.key === "1" || e.key === "2") { setAppTab(e.key === "1" ? "client" : "kb"); return; }
      if (e.key === "l" || e.key === "L") { cycleListStyle(); return; }
      if (e.key === "m" || e.key === "M") { toggleLayoutMode(); return; }
      if (state.appTab === "client") { if (client) client.handleKey(e); return; }
      if (state.treeSel.kind === "peer") { if (peers) peers.handleKey(e, state.treeSel.value); return; }

      const vis = getVisibleItems();
      if (!vis.length) return;
      const idx = vis.findIndex((x) => x.id === state.selectedItemId);

      if (e.key === "j" || e.key === "k") {
        const nextIdx = e.key === "j" ? Math.min(vis.length - 1, idx + 1) : Math.max(0, idx - 1);
        const target = vis[nextIdx] || vis[0];
        state.selectedItemId = target.id;
        if (prefs.autoMarkReadOnOpen) target.unread = false;
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
      } else if (e.key === "f") {
        const cur = vis[idx >= 0 ? idx : 0];
        if (cur) { cur.starred = !cur.starred; renderAll(); }
      } else if (e.key === "u") {
        const cur = vis[idx >= 0 ? idx : 0];
        if (cur) { cur.unread = !cur.unread; renderAll(); }
      }
    });
  }

  function renderAll() {
    applyLayoutAttr();
    renderTopBar();
    renderTabBar();
    if (state.appTab === "client" && client) {
      client.render();
    } else {
      renderTree();
      renderStreamAndReader();
    }
  }

  /* 연합 클라이언트 탭(syndication_client.js)과 공유하는 지식베이스 브리지 */
  function addKbFeed(feed) {
    curTheme().feeds.push(feed);
    if (!customState[state.themeId]) customState[state.themeId] = {};
    if (!Array.isArray(customState[state.themeId].extraFeeds)) customState[state.themeId].extraFeeds = [];
    customState[state.themeId].extraFeeds.push(feed);
    saveCustom(customState);
  }

  function addAccount(acc) {
    curTheme().accounts.push(acc);
    if (!customState[state.themeId]) customState[state.themeId] = {};
    if (!Array.isArray(customState[state.themeId].extraAccounts)) customState[state.themeId].extraAccounts = [];
    customState[state.themeId].extraAccounts.push(acc);
    saveCustom(customState);
  }

  function openKbItem(itemId) {
    state.treeSel = { kind: "virtual", value: "all", label: "전체 아티클 (All articles)" };
    state.adaptiveFilter = "all";
    state.protoFilter = "all";
    state.searchQuery = "";
    const search = document.getElementById("stream-search");
    if (search) search.value = "";
    state.selectedItemId = itemId;
    setAppTab("kb");
  }

  client = typeof window.CLAIRE_SYND_CLIENT_FACTORY === "function"
    ? window.CLAIRE_SYND_CLIENT_FACTORY({
      THEMES, state, prefs, LIST_STYLE_META, SOURCE_KINDS, ACCOUNT_TEMPLATES,
      curTheme, esc, ticker, protoDot, themeAccounts, findAccount, publishAccounts,
      buildServerRequest, accountSupports, graphemeLength, hashtagToken, getItemSourceTags, getLinkedDocEntities,
      renderAll, setAppTab, closeOpenPopovers, ingestItemToClaire,
      addKbFeed, addAccount, openKbItem, peers: () => peers
    })
    : null;

  function showPeerView(tab, userKey) {
    if (tab === "kb") {
      const key = String(userKey || "all");
      state.treeSel = { kind: "peer", value: key, label: key === "all" ? peers.VIEW_LABEL : state.treeSel.label };
      setAppTab("kb");
      return;
    }
    if (client) client.showPeers();
    setAppTab("client");
  }

  function openClientPost(postId) {
    if (!client) return;
    client.openPost(postId);
    setAppTab("client");
  }

  peers = typeof window.CLAIRE_SYND_PEERS_FACTORY === "function"
    ? window.CLAIRE_SYND_PEERS_FACTORY({
      state, prefs, curTheme, esc, ticker, protoDot, themeAccounts, publishAccounts,
      buildServerRequest, accountSupports, graphemeLength, truncateGraphemes, hashtagToken,
      ingestItemToClaire, renderAll, openKbItem, showPeerView, openClientPost
    })
    : null;

  setupPopoverPositionFallback();
  bindGlobalControls();
  ticker(state.appTab === "client" ? TAB_HINTS.client : TAB_HINTS.kb);
  renderAll();
  syncLiveThemeDb();
})();
