/* Claire Settings Console controller for Groups 2–3 (/settings/{reader,connections,feeds,tags,rules,outbound,opml,mcp,webmcp}).
 *
 * Ported from the legacy syndication_preferences.html panels (design P3-b). Each /settings/<slug> page holds one panel.
 * State is isolated per account and per theme: localStorage key "claire_synd_prefs_v2:<claire-storage-key>:t<theme ref>".
 * This bundle has no operator data: no MCP call audit rows, no IdP operator config, no user directory.
 */
(function () {
  "use strict";

  var root = document.getElementById("settings-syndication-root");
  if (!root) return;

  function parseBootstrap() {
    var el = document.getElementById("console-bootstrap");
    if (!el || !el.textContent) throw new Error("console-bootstrap JSON is missing");
    return JSON.parse(el.textContent);
  }

  var boot = parseBootstrap();
  var synd = boot.syndication;
  if (!synd || !synd.theme_ref) throw new Error("console-bootstrap has no syndication theme_ref");

  var metaKey = document.querySelector('meta[name="claire-storage-key"]');
  var storageKey = metaKey ? metaKey.getAttribute("content") : "";
  // C1: do not share one localStorage slot between accounts. Stop if the account key is missing.
  if (!storageKey || storageKey === "a" || storageKey.indexOf("$") !== -1) throw new Error("claire-storage-key meta is missing");

  var SECTION = root.dataset.section;
  var THEME_REF = root.dataset.themeRef;
  if (THEME_REF !== synd.theme_ref) throw new Error("theme ref mismatch between body and bootstrap");
  var OWNED_REFS = Array.isArray(synd.owned_theme_refs) ? synd.owned_theme_refs.map(String) : [];
  var NAV_SLUGS = Array.isArray(synd.nav_slugs) ? synd.nav_slugs.map(String) : [];
  var STORE_KEY = "claire_synd_prefs_v2:" + storageKey + ":t" + THEME_REF;

  function escapeHtml(s) {
    return String(s == null ? "" : s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }
  function byId(id) { return document.getElementById(id); }
  function clone(v) { return JSON.parse(JSON.stringify(v)); }

  /* ── Theme selector (#console-theme-select) ─────────────────────────────────────── */

  function withTheme(href, ref) {
    var url = new URL(href, window.location.origin);
    url.searchParams.set("theme", ref);
    return url.pathname + url.search + url.hash;
  }

  function updateThemeLinks(ref) {
    document.querySelectorAll("a[data-nav-section]").forEach(function (a) {
      if (NAV_SLUGS.indexOf(a.getAttribute("data-nav-section")) === -1) return;
      a.setAttribute("href", withTheme(a.getAttribute("href"), ref));
    });
    var back = byId("console-back-syndication");
    if (back) back.setAttribute("href", withTheme(back.getAttribute("href"), ref));
  }

  updateThemeLinks(THEME_REF);

  var themeSelect = byId("console-theme-select");
  if (themeSelect) {
    themeSelect.addEventListener("change", function () {
      var ref = String(themeSelect.value);
      if (OWNED_REFS.indexOf(ref) === -1) {
        themeSelect.value = THEME_REF;
        showToast("소유하지 않은 테마는 선택할 수 없습니다");
        return;
      }
      updateThemeLinks(ref);
      window.location.assign(withTheme(window.location.pathname, ref));
    });
  }

  /* ── Preferences and per-theme state ────────────────────────────────────────────── */

  var LIST_STYLES = ["list", "compact", "card", "roomy"];
  var DEFAULT_PREFS = {
    appTab: "client",
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
    },
    peerSharePolicy: { listed: true, allowQuote: true, allowImport: true, license: "" },
    mcpPolicy: { writeScope: "none", proposedTools: {} },
    webmcpPolicy: { enabled: true, registration: "imperative", confirm: "write", showAgentActivity: true }
  };
  var NESTED = ["tagPolicy", "outboundPolicy", "peerSharePolicy", "mcpPolicy", "webmcpPolicy"];

  function mergePrefs(parsed) {
    var merged = Object.assign(clone(DEFAULT_PREFS), parsed || {});
    NESTED.forEach(function (k) { merged[k] = Object.assign(clone(DEFAULT_PREFS[k]), (parsed && parsed[k]) || {}); });
    if (LIST_STYLES.indexOf(merged.listStyle) === -1) merged.listStyle = DEFAULT_PREFS.listStyle;
    return merged;
  }

  function loadState() {
    var raw = null;
    try { raw = window.localStorage.getItem(STORE_KEY); } catch (_e) { raw = null; }
    if (!raw) return { v: 2, prefs: clone(DEFAULT_PREFS), custom: {} };
    var parsed;
    try { parsed = JSON.parse(raw); } catch (_e) { parsed = null; }
    if (!parsed || typeof parsed !== "object" || parsed.v !== 2) {
      // C1: a damaged slot is not silently reused. Start from defaults and tell the user.
      setTimeout(function () { showToast("저장된 설정을 읽을 수 없어 기본값으로 시작합니다"); }, 0);
      return { v: 2, prefs: clone(DEFAULT_PREFS), custom: {} };
    }
    return { v: 2, prefs: mergePrefs(parsed.prefs), custom: (parsed.custom && typeof parsed.custom === "object") ? parsed.custom : {} };
  }

  var state = loadState();
  var prefs = state.prefs;
  var custom = state.custom;

  var toastTimer = null;
  function showToast(msg) {
    var el = byId("pref-toast");
    if (!el) return;
    el.textContent = msg;
    el.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { el.classList.remove("show"); }, 1800);
  }

  function persist() {
    try {
      window.localStorage.setItem(STORE_KEY, JSON.stringify({ v: 2, prefs: prefs, custom: custom }));
      return true;
    } catch (_e) {
      showToast("브라우저 저장소에 쓸 수 없어 설정을 저장하지 못했습니다");
      return false;
    }
  }
  function savePrefs(msg) {
    if (persist()) showToast(msg || "선택한 테마의 환경설정이 저장되었습니다");
  }

  /* ── Seed data (local mock identifiers) ─────────────── */

  var MASTODON_SCOPES = ["read", "write:statuses", "write:media", "write:favourites", "write:bookmarks", "write:follows", "write:lists", "write:mutes", "write:blocks", "write:filters", "write:reports", "write:notifications", "write:conversations", "write:accounts"];
  var SEED_THEMES = [
    {
      accounts: [
        { id: "acc0-ap", proto: "ap", software: "Mastodon", serverVersion: "5.0.0", server: "social.example.com", handle: "@claire@social.example.com", auth: "OAuth 2.0 (Authorization Code + PKCE)", scopes: MASTODON_SCOPES, canPublish: true, missingFeatures: [] },
        { id: "acc0-at", proto: "at", software: "Bluesky PDS", server: "bsky.social", handle: "@claire.example.com", auth: "AT OAuth (PAR + DPoP)", scopes: ["atproto", "transition:generic"], canPublish: true }
      ],
      feeds: [
        { id: "f0-1", folder: "오픈소스 릴리스", proto: "rss", interval: "15m", title: "SQLite Release Log", url: "https://sqlite.org/news.atom", etag: "200 OK" },
        { id: "f0-2", folder: "Fediverse 표준 기구", proto: "ap", interval: "stream", title: "W3C Social Web WG", url: "@w3c@w3c.social", via: "acc0-ap", etag: "연결 계정 경유 · 스트리밍" },
        { id: "f0-3", folder: "분산 소셜 프로토콜", proto: "at", interval: "15m", title: "AT Protocol Core Dev", url: "@atproto.com", via: "acc0-at", etag: "PDS 커서 동기화" }
      ],
      rules: [
        { id: "r0-1", name: "SQLite 릴리스 노트 우선 오거나이즈", cond: "엔티티/태그 일치: [[SQLite]]", action: "연관도 +45 · 별표 부여", enabled: true },
        { id: "r0-2", name: "W3C 표준 권고안 자동 적재 전달", cond: "출처: @w3c@w3c.social (연결 계정 경유) + [[ActivityPub]]", action: "프로젝트 적재 파이프라인 자동 전달", enabled: true }
      ]
    },
    {
      accounts: [
        { id: "acc1-ap", proto: "ap", software: "Mastodon API 호환 서버", server: "fedi.ai.example.com", handle: "@ai@fedi.ai.example.com", auth: "OAuth 2.0 (Authorization Code + PKCE)", scopes: MASTODON_SCOPES, canPublish: true, missingFeatures: ["trends", "quote", "translate", "notifRequests", "scheduled"] },
        { id: "acc1-at", proto: "at", software: "자체 호스팅 PDS", server: "pds.ai.example.com", handle: "@ai.example.com", auth: "앱 비밀번호 (com.atproto.server.createSession)", scopes: ["app-password"], canPublish: true }
      ],
      feeds: [
        { id: "f1-1", folder: "학술 프리프린트", proto: "rss", interval: "15m", title: "arXiv cs.LG & cs.DC", url: "https://rss.arxiv.org/atom/cs.LG+cs.DC", etag: "200 OK" },
        { id: "f1-2", folder: "학술 프리프린트", proto: "ap", interval: "stream", title: "Scholar Papers Bot", url: "@papers@scholar.social", via: "acc1-ap", etag: "연결 계정 경유 · 폴링" },
        { id: "f1-3", folder: "엔지니어링 블로그", proto: "rss", interval: "15m", title: "vLLM Engineering Blog", url: "https://blog.vllm.ai/feed.xml", etag: "304 Not Modified" },
        { id: "f1-4", folder: "Fediverse 연구자", proto: "ap", interval: "5m", title: "Georgi Gerganov", url: "@ggerganov@mastodon.social", via: "acc1-ap", etag: "연결 계정 경유 · 폴링" },
        { id: "f1-5", folder: "AT Protocol 연구 스트림", proto: "at", interval: "15m", title: "ML Systems Feed (AT)", url: "at://did:plc:mlsys-feed/app.bsky.feed.generator/ml-systems", via: "acc1-at", etag: "커서 수집 정상" }
      ],
      rules: [
        { id: "r1-1", name: "추론 최적화 논문 고득점 오거나이즈", cond: "엔티티/태그 일치 ≥ 2 ([[vLLM]], [[KV Cache]], [[Speculative Decoding]])", action: "연관도 +50 · 상단 배치", enabled: true },
        { id: "r1-2", name: "vLLM 아키텍처 포스트 자동 적재", cond: "출처: vLLM Engineering Blog + [[PagedAttention]]", action: "프로젝트 적재 자동 전달 (초점 주입)", enabled: true }
      ]
    },
    {
      accounts: [
        { id: "acc2-ap", proto: "ap", software: "Mastodon", serverVersion: "5.0.0", server: "scholar.example", handle: "@classics@scholar.example", auth: "OAuth 2.0 (Authorization Code + PKCE)", scopes: MASTODON_SCOPES, canPublish: true, missingFeatures: [] }
      ],
      feeds: [
        { id: "f2-1", folder: "성서학 저널", proto: "rss", interval: "1h", title: "Biblical Archaeology Review", url: "https://www.biblicalarchaeology.org/feed/", etag: "200 OK" },
        { id: "f2-2", folder: "교부학 Fediverse", proto: "ap", interval: "stream", title: "Patristics Colloquium", url: "@patristics@scholar.example", via: "acc2-ap", etag: "연결 계정 경유 · 스트리밍" }
      ],
      rules: [
        { id: "r2-1", name: "70인역 사본 연구 우선 오거나이즈", cond: "엔티티/태그 일치: [[70인역 (LXX)]]", action: "연관도 +40 · 별표 부여", enabled: true }
      ]
    },
    {
      accounts: [
        { id: "acc3-ap", proto: "ap", software: "Mastodon", serverVersion: "5.0.0", server: "infosec.example", handle: "@secwatch@infosec.example", auth: "OAuth 2.0 (Authorization Code + PKCE)", scopes: ["read"], canPublish: false, missingFeatures: [] }
      ],
      feeds: [
        { id: "f3-1", folder: "취약점 공지", proto: "rss", interval: "5m", title: "OSS-Security Advisories", url: "https://www.openwall.com/lists/oss-security/rss", etag: "200 OK" }
      ],
      rules: [
        { id: "r3-1", name: "커널 권한 상승 취약점 즉시 적재", cond: "엔티티/태그: [[Linux Kernel]] + [[CVE-2026]]", action: "비공개 DB 자동 적재", enabled: true }
      ]
    }
  ];

  function buildThemeMeta() {
    var seedIndex = Number(root.dataset.themeSeed);
    if (!Number.isInteger(seedIndex) || seedIndex < 0) throw new Error("data-theme-seed is invalid");
    var isPublic = root.dataset.themePublic === "true";
    var meta = { ref: THEME_REF, label: root.dataset.themeLabel || "", icon: root.dataset.themeIcon || "", isPublic: isPublic };
    if (SEED_THEMES[seedIndex]) {
      var s = clone(SEED_THEMES[seedIndex]);
      meta.accounts = s.accounts;
      meta.feeds = s.feeds;
      meta.rules = s.rules;
    } else {
      var base = SEED_THEMES[0];
      meta.accounts = base.accounts.map(function (a) {
        return Object.assign({}, a, {
          id: a.id + "-t" + seedIndex,
          handle: a.proto === "ap" ? "@theme" + seedIndex + "@" + a.server : "@theme" + seedIndex + "." + a.server,
          canPublish: isPublic && a.canPublish,
          scopes: isPublic ? a.scopes : (a.proto === "ap" ? ["read"] : a.scopes)
        });
      });
      meta.feeds = clone(base.feeds).map(function (f) { return f.via ? Object.assign({}, f, { via: f.via + "-t" + seedIndex }) : f; });
      meta.rules = clone(base.rules);
    }
    return meta;
  }

  var THEME = buildThemeMeta();

  function getThemeAccounts() {
    var extra = Array.isArray(custom.extraAccounts) ? custom.extraAccounts : [];
    return THEME.accounts.concat(extra.map(function (a) { return Object.assign({}, a, { removable: true }); }));
  }
  function getThemeFeeds() {
    var extra = Array.isArray(custom.extraFeeds) ? custom.extraFeeds : [];
    return THEME.feeds.concat(extra);
  }
  function protoLabel(proto) { return proto === "ap" ? "ActivityPub" : (proto === "at" ? "AT Proto" : "RSS/ATOM"); }
  function protoPillClass(proto) { return proto === "ap" ? "pref-pill-ap" : (proto === "at" ? "pref-pill-at" : "pref-pill-rss"); }

  /* Bind a control only when it exists on this section page. */
  function onChange(id, fn) {
    var el = byId(id);
    if (el) el.addEventListener("change", function (e) { fn(e.target); });
  }

  /* ── reader ─────────────────────────────────────────────────────────────────────── */

  function initReader() {
    byId("pref-app-tab").value = prefs.appTab === "kb" ? "kb" : "client";
    byId("pref-list-style").value = prefs.listStyle || "list";
    byId("pref-layout-mode").value = prefs.layoutMode || "three-panel";
    byId("pref-show-excerpt").checked = !!prefs.showExcerpt;
    byId("pref-default-content-tab").value = prefs.defaultContentTab;
    byId("pref-reader-font").value = prefs.readerFont;
    byId("pref-reader-fontsize").value = String(prefs.readerFontSize);
    byId("pref-auto-read").checked = !!prefs.autoMarkReadOnOpen;
    onChange("pref-app-tab", function (el) { prefs.appTab = el.value; savePrefs(); });
    onChange("pref-list-style", function (el) { prefs.listStyle = el.value; savePrefs(); });
    onChange("pref-layout-mode", function (el) { prefs.layoutMode = el.value; savePrefs(); });
    onChange("pref-show-excerpt", function (el) { prefs.showExcerpt = el.checked; savePrefs(); });
    onChange("pref-default-content-tab", function (el) { prefs.defaultContentTab = el.value; savePrefs(); });
    onChange("pref-reader-font", function (el) { prefs.readerFont = el.value; savePrefs(); });
    onChange("pref-reader-fontsize", function (el) { prefs.readerFontSize = Number(el.value) || 15; savePrefs(); });
    onChange("pref-auto-read", function (el) { prefs.autoMarkReadOnOpen = el.checked; savePrefs(); });
  }

  /* ── connections (#panel-accounts) ──────────────────────────────────────────────── */

  function renderAccounts() {
    var accounts = getThemeAccounts();
    byId("pref-accounts-tbody").innerHTML = accounts.length ? accounts.map(function (a) {
      var missing = a.missingFeatures || [];
      return "<tr>"
        + '<td><span class="pref-pill ' + protoPillClass(a.proto) + '">' + protoLabel(a.proto) + "</span></td>"
        + "<td><strong>" + escapeHtml(a.server) + '</strong><br/><span style="color:var(--muted);font-size:11.5px">' + escapeHtml(a.software) + (a.serverVersion ? " " + escapeHtml(a.serverVersion) : "") + "</span>"
        + (missing.length ? '<br/><span style="color:#9a6700;font-size:11px" title="' + escapeHtml(missing.join(", ")) + '">기준 기능 ' + missing.length + "개 미제공 → 비활성</span>" : "") + "</td>"
        + '<td><code class="pref-mono">' + escapeHtml(a.handle) + "</code></td>"
        + "<td>" + escapeHtml(a.auth) + "</td>"
        + '<td><code class="pref-mono" style="white-space:normal">' + escapeHtml(a.scopes.join(" ")) + "</code></td>"
        + "<td>" + (a.canPublish ? '<span style="color:var(--accent2);font-weight:700">● 허용</span>' : '<span style="color:var(--muted)">읽기 전용</span>') + "</td>"
        + "<td>" + (a.removable
          ? '<button type="button" class="pref-btn" data-remove-account="' + escapeHtml(a.id) + '" title="' + (a.proto === "ap" ? "POST /oauth/revoke 로 토큰 폐기 후 연결 해제" : "AT 세션·토큰 폐기 후 연결 해제") + '">연결 해제</button>'
          : '<span style="color:var(--muted);font-size:11.5px">프로토타입 시드</span>') + "</td>"
        + "</tr>";
    }).join("") : '<tr><td colspan="7" style="color:var(--muted)">이 테마에 연결된 서버 계정이 없습니다.</td></tr>';
  }

  function initConnections() {
    renderAccounts();
    byId("panel-accounts").addEventListener("click", function (e) {
      var btn = e.target.closest("[data-remove-account]");
      if (!btn) return;
      var id = btn.getAttribute("data-remove-account");
      custom.extraAccounts = (custom.extraAccounts || []).filter(function (a) { return a.id !== id; });
      if (persist()) showToast("연결 계정을 해제했습니다 (서버 토큰 폐기 시뮬레이션)");
      renderAccounts();
    });
  }

  /* ── feeds ──────────────────────────────────────────────────────────────────────── */

  function refreshViaSelect() {
    var proto = byId("new-feed-proto").value;
    var sel = byId("new-feed-via");
    if (proto === "rss") {
      sel.innerHTML = '<option value="">(RSS/ATOM 은 직접 수집)</option>';
      sel.disabled = true;
      return;
    }
    var accs = getThemeAccounts().filter(function (a) { return a.proto === proto; });
    sel.disabled = false;
    sel.innerHTML = accs.length
      ? accs.map(function (a) { return '<option value="' + escapeHtml(a.id) + '">' + escapeHtml(a.handle) + "</option>"; }).join("")
      : '<option value="">연결 계정 없음 — 먼저 연결</option>';
  }

  function renderFeeds() {
    var accounts = getThemeAccounts();
    byId("pref-feeds-tbody").innerHTML = getThemeFeeds().map(function (f) {
      var viaAcc = f.via ? accounts.find(function (a) { return a.id === f.via; }) : null;
      var viaCell = f.proto === "rss"
        ? '<span style="color:var(--muted)">직접 수집</span>'
        : (viaAcc ? '<code class="pref-mono">' + escapeHtml(viaAcc.handle) + "</code>" : '<span style="color:#cf222e">연결 계정 없음</span>');
      return "<tr>"
        + '<td><span class="pref-pill ' + protoPillClass(f.proto) + '">' + protoLabel(f.proto) + "</span></td>"
        + "<td>" + escapeHtml(f.folder) + "</td>"
        + "<td><strong>" + escapeHtml(f.title) + "</strong></td>"
        + '<td><code class="pref-mono">' + escapeHtml(f.url) + "</code></td>"
        + "<td>" + viaCell + "</td>"
        + "<td>" + escapeHtml(f.interval) + "</td>"
        + '<td><span style="color:var(--accent2);font-weight:700">● ' + escapeHtml(f.etag || "정상") + "</span></td>"
        + "</tr>";
    }).join("");
  }

  function initFeeds() {
    renderFeeds();
    refreshViaSelect();
    onChange("new-feed-proto", refreshViaSelect);
    byId("pref-add-feed-form").addEventListener("submit", function (e) {
      e.preventDefault();
      var proto = byId("new-feed-proto").value;
      var folder = byId("new-feed-folder").value.trim() || "기본 폴더";
      var title = byId("new-feed-title").value.trim();
      var url = byId("new-feed-url").value.trim();
      var interval = byId("new-feed-interval").value;
      var via = byId("new-feed-via").value;
      if (!title || !url) return;
      if (proto !== "rss" && !via) {
        showToast("ActivityPub · AT 수집 대상은 연결 계정을 경유해야 합니다");
        return;
      }
      if (!Array.isArray(custom.extraFeeds)) custom.extraFeeds = [];
      custom.extraFeeds.push({
        id: "f_pref_" + Date.now(),
        folder: folder,
        proto: proto,
        format: proto === "ap" ? "ActivityPub" : (proto === "at" ? "AT Protocol" : "RSS 2.0"),
        interval: interval,
        title: title,
        url: url,
        via: proto === "rss" ? undefined : via,
        health: "ok",
        etag: proto === "rss" ? "환경설정에서 등록됨" : "연결 계정 경유 · 환경설정에서 등록됨"
      });
      if (persist()) showToast("신규 수집 대상 '" + title + "' 등록 완료");
      byId("new-feed-title").value = "";
      byId("new-feed-url").value = "";
      renderFeeds();
    });
  }

  /* ── tags ───────────────────────────────────────────────────────────────────────── */

  function initTags() {
    byId("pref-tag-automap").checked = !!prefs.tagPolicy.autoMapSourceTagsToEntities;
    byId("pref-tag-provisional").checked = !!prefs.tagPolicy.createProvisionalEntityForUnknownTag;
    byId("pref-tag-hashtags").checked = !!prefs.tagPolicy.attachEntityHashtagsOnPublish;
    byId("pref-noise-tags").value = prefs.tagPolicy.ignoredNoiseTags;
    onChange("pref-tag-automap", function (el) { prefs.tagPolicy.autoMapSourceTagsToEntities = el.checked; savePrefs(); });
    onChange("pref-tag-provisional", function (el) { prefs.tagPolicy.createProvisionalEntityForUnknownTag = el.checked; savePrefs(); });
    onChange("pref-tag-hashtags", function (el) { prefs.tagPolicy.attachEntityHashtagsOnPublish = el.checked; savePrefs(); });
    onChange("pref-noise-tags", function (el) { prefs.tagPolicy.ignoredNoiseTags = el.value; savePrefs(); });
  }

  /* ── rules ──────────────────────────────────────────────────────────────────────── */

  function renderRules() {
    var overrides = custom.ruleStates || {};
    byId("pref-rules-list").innerHTML = THEME.rules.map(function (r) {
      var on = overrides[r.id] !== undefined ? overrides[r.id] : r.enabled;
      return '<div style="display:flex;justify-content:space-between;align-items:center;gap:12px;padding:10px 12px;border:1px solid var(--border);border-radius:6px;background:var(--panel-bg)">'
        + '<div><div style="font-size:13px;font-weight:700">' + escapeHtml(r.name) + "</div>"
        + '<div class="pref-mono" style="font-size:12px;color:var(--muted);margin-top:2px">조건: ' + escapeHtml(r.cond) + " → " + escapeHtml(r.action) + "</div></div>"
        + '<label style="display:inline-flex;align-items:center;gap:6px;font-size:12px;font-weight:700;cursor:pointer">'
        + "<span>" + (on ? "활성" : "비활성") + "</span>"
        + '<input type="checkbox" switch data-rule-id="' + escapeHtml(r.id) + '" ' + (on ? "checked" : "") + "/></label></div>";
    }).join("");
  }

  function initRules() {
    byId("pref-auto-threshold").value = String(prefs.autoIngestThreshold);
    onChange("pref-auto-threshold", function (el) { prefs.autoIngestThreshold = Number(el.value) || 85; savePrefs(); });
    renderRules();
    byId("pref-rules-list").addEventListener("change", function (e) {
      var rid = e.target.getAttribute && e.target.getAttribute("data-rule-id");
      if (!rid) return;
      custom.ruleStates = Object.assign({}, custom.ruleStates || {});
      custom.ruleStates[rid] = e.target.checked;
      if (persist()) showToast("오거나이즈 규칙 상태가 저장되었습니다");
      renderRules();
    });
  }

  /* ── outbound (incl. peerSharePolicy) ───────────────────────────────────────────── */

  function renderOutbound() {
    byId("pref-rss-pubmode").value = prefs.outboundPolicy.rssDefaultMode;
    byId("pref-ap-pubmode").value = prefs.outboundPolicy.apDefaultMode;
    byId("pref-at-pubmode").value = prefs.outboundPolicy.atDefaultMode;
    byId("pref-include-sharelink").checked = !!prefs.outboundPolicy.includeCanonicalShareLink;
    byId("pref-quote-template").value = prefs.outboundPolicy.quoteTemplate;
    byId("pref-peer-listed").checked = !!prefs.peerSharePolicy.listed;
    byId("pref-peer-quote").checked = !!prefs.peerSharePolicy.allowQuote;
    byId("pref-peer-import").checked = !!prefs.peerSharePolicy.allowImport;
    byId("pref-peer-license").value = prefs.peerSharePolicy.license || "";
    ["pref-peer-quote", "pref-peer-import", "pref-peer-license"].forEach(function (id) { byId(id).disabled = !prefs.peerSharePolicy.listed; });
    var pub = getThemeAccounts().filter(function (a) { return a.canPublish; });
    byId("pref-actor-handle").textContent = pub.length ? pub.map(function (a) { return a.handle; }).join("\n") : "(발행 가능 계정 없음)";
    byId("pref-actor-desc").textContent = pub.length
      ? "아래 연결 계정의 서버 API 로 부스트·인용 발행 및 적재 본문 발행이 수행됩니다."
      : "발행 권한이 있는 연결 계정이 없습니다. 수집 및 내부 적재만 허용되며 외부 발행은 차단됩니다.";
  }

  function initOutbound() {
    renderOutbound();
    onChange("pref-rss-pubmode", function (el) { prefs.outboundPolicy.rssDefaultMode = el.value; savePrefs(); });
    onChange("pref-ap-pubmode", function (el) { prefs.outboundPolicy.apDefaultMode = el.value; savePrefs(); });
    onChange("pref-at-pubmode", function (el) { prefs.outboundPolicy.atDefaultMode = el.value; savePrefs(); });
    onChange("pref-include-sharelink", function (el) { prefs.outboundPolicy.includeCanonicalShareLink = el.checked; savePrefs(); });
    onChange("pref-quote-template", function (el) { prefs.outboundPolicy.quoteTemplate = el.value; savePrefs(); });
    [["pref-peer-listed", "listed"], ["pref-peer-quote", "allowQuote"], ["pref-peer-import", "allowImport"]].forEach(function (pair) {
      onChange(pair[0], function (el) { prefs.peerSharePolicy[pair[1]] = el.checked; savePrefs(); renderOutbound(); });
    });
    onChange("pref-peer-license", function (el) { prefs.peerSharePolicy.license = el.value; savePrefs(); });
  }

  /* ── opml ───────────────────────────────────────────────────────────────────────── */

  function renderOpmlPreview() {
    var outlines = getThemeFeeds().map(function (f) {
      return '      <outline text="' + escapeHtml(f.title) + '" title="' + escapeHtml(f.title) + '" type="' + escapeHtml(f.proto) + '" xmlUrl="' + escapeHtml(f.url) + '" category="' + escapeHtml(f.folder) + '"/>';
    }).join("\n");
    byId("opml-preview-box").textContent = '<?xml version="1.0" encoding="UTF-8"?>\n<opml version="2.0">\n  <head>\n    <title>Claire Bible Syndication — ' + escapeHtml(THEME.label) + "</title>\n  </head>\n  <body>\n" + outlines + "\n  </body>\n</opml>";
  }

  function initOpml() {
    renderOpmlPreview();
    byId("btn-export-opml").addEventListener("click", function () {
      renderOpmlPreview();
      showToast("현재 테마의 OPML 2.0 문서가 생성되었습니다");
    });
    byId("btn-sim-import-opml").addEventListener("click", function () {
      if (!Array.isArray(custom.extraFeeds)) custom.extraFeeds = [];
      custom.extraFeeds.push({
        id: "f_opml_" + Date.now(),
        folder: "OPML 가져옴",
        proto: "rss",
        format: "ATOM 1.0",
        interval: "1h",
        title: "ACM Queue Systems Feed",
        url: "https://queue.acm.org/rss/feeds/queuecontent.xml",
        health: "ok",
        etag: "OPML 가져오기 완료"
      });
      if (persist()) showToast("OPML 구독 항목 가져오기 시뮬레이션 완료");
      renderOpmlPreview();
    });
  }

  /* ── mcp (user slice: endpoint, discovery, snippets, tool catalog, draft scope) ── */

  var MCP_TOOLS = [
    { name: "resolve_entity", args: [["name", "string", true]], desc: "이름 · 별칭으로 엔티티 찾기 — 탐색 루프의 진입점" },
    { name: "search", args: [["query", "string", true], ["entity_type", "string"], ["near_ids", "array"], ["limit", "integer"]], desc: "FTS5 전문 검색 (LLM 호출 0)" },
    { name: "neighbors", args: [["entity_ids", "array", true], ["exclude_ids", "array"], ["limit", "integer"]], desc: "여러 노드의 1홉 이웃 합집합 · degree" },
    { name: "path", args: [["from_id", "string", true], ["to_id", "string", true], ["max_hops", "integer"]], desc: "두 노드 사이 최단 경로 (BFS)" },
    { name: "context", args: [["entity_ids", "array", true], ["compact", "boolean"]], desc: "observations · 관계 · 출처 종합 (최대 10개 노드)" },
    { name: "overview", args: [], desc: "엔티티 타입 분포 · 허브 노드 (오리엔테이션)" },
    { name: "node", args: [["entity_id", "string", true], ["full", "boolean"]], desc: "노드 1개 상세 (연결 문서 최대 10건)" },
    { name: "documents", args: [["limit", "integer"], ["since", "string"], ["query", "string"]], desc: "최신순 문서 목록 (최대 100건)" },
    { name: "document", args: [["document_id", "string", true]], desc: "문서 본문 · 메타데이터 (mark_seen 부작용 없음)" },
    { name: "stats", args: [], desc: "문서 · 엔티티 · 관계 등 규모 요약" }
  ];

  var MCP_PROPOSED_TOOLS = [
    { name: "synd_targets", scope: "syndication:read", args: [["folder", "string"]], desc: "수집 대상 · 폴더 · 경유 계정 (토큰 제외)" },
    { name: "synd_stream", scope: "syndication:read", args: [["stream", "string"], ["cursor", "string"], ["limit", "integer"]], desc: "스트림 항목 (unread · starred · 🔗 적재 필터, 커서)" },
    { name: "synd_item", scope: "syndication:read", args: [["item_id", "string", true]], desc: "항목 1건: 수집 원문 · 수집 태그 · 연결 문서 id" },
    { name: "synd_timeline", scope: "syndication:read", args: [["account_id", "string", true], ["timeline", "string"], ["cursor", "string"]], desc: "연결 계정 타임라인 프록시 조회 (home · local · list · tag)" },
    { name: "synd_draft_publish", scope: "syndication:draft", write: true, args: [["item_id", "string", true], ["mode", "string", true], ["account_ids", "array"]], desc: "부스트 · 인용 · 적재 발행 초안 → 소유자 승인 큐 (직접 발행 없음)" }
  ];

  function mcpEndpoint() { return window.location.origin + "/mcp"; }

  function schemaFor(args, withTheme) {
    var properties = {};
    var required = [];
    args.forEach(function (a) {
      properties[a[0]] = a[1] === "array" ? { type: "array", items: { type: "string" } } : { type: a[1] };
      if (a[2]) required.push(a[0]);
    });
    if (withTheme) properties.theme = { anyOf: [{ type: "string" }, { type: "integer" }, { type: "null" }], default: null };
    var schema = { type: "object", properties: properties };
    if (required.length) schema.required = required;
    return schema;
  }

  function argLabel(args, withTheme) {
    var parts = args.map(function (a) { return a[2] ? a[0] : a[0] + "?"; });
    if (withTheme) parts.push("theme?");
    return parts.length ? parts.join(", ") : "—";
  }

  function proposedToolOn(t) {
    if (t.scope === "syndication:draft" && prefs.mcpPolicy.writeScope !== "draft") return false;
    var st = prefs.mcpPolicy.proposedTools || {};
    return st[t.name] !== undefined ? st[t.name] : true;
  }

  function renderMcpPanel() {
    var proposedOn = MCP_PROPOSED_TOOLS.filter(proposedToolOn);
    var ep = mcpEndpoint();
    byId("pref-mcp-endpoint").textContent = ep;
    byId("pref-mcp-theme-example").textContent = JSON.stringify({ name: "search", arguments: { query: "…", theme: THEME_REF } });
    byId("pref-mcp-write").value = prefs.mcpPolicy.writeScope === "draft" ? "draft" : "none";
    byId("pref-mcp-stats").innerHTML = [
      ["Streamable HTTP", "stateless · JSON 응답"],
      [MCP_TOOLS.length + "종", "읽기 전용 도구 (구현)"],
      ["3채널", "상시 토큰 · 텔레그램 세션 · OAuth 2.1"],
      ["+" + proposedOn.length, "제안 도구 (미리보기)"]
    ].map(function (p) { return '<div class="pref-stat"><b>' + escapeHtml(p[0]) + "</b><span>" + escapeHtml(p[1]) + "</span></div>"; }).join("");

    byId("pref-mcp-snippet-cli").textContent = "claude mcp add --transport http claire " + ep + " \\\n  --header \"Authorization: Bearer cb_…\"";
    byId("pref-mcp-snippet-json").textContent = JSON.stringify({ mcpServers: { claire: { type: "http", url: ep, headers: { Authorization: "Bearer cb_…" } } } }, null, 2);

    byId("pref-mcp-tools-tbody").innerHTML = MCP_TOOLS.map(function (t) {
      return '<tr><td><code class="pref-mono">' + escapeHtml(t.name) + '</code></td><td class="pref-mono">' + escapeHtml(argLabel(t.args, true)) + '</td><td style="font-size:12px">' + escapeHtml(t.desc) + "</td></tr>";
    }).join("");

    byId("pref-mcp-proposed-tbody").innerHTML = MCP_PROPOSED_TOOLS.map(function (t) {
      var available = !(t.scope === "syndication:draft" && prefs.mcpPolicy.writeScope !== "draft");
      var on = proposedToolOn(t);
      return '<tr data-off="' + (on ? "false" : "true") + '">'
        + '<td><input type="checkbox" switch data-mcp-proposed="' + escapeHtml(t.name) + '" ' + (on ? "checked" : "") + " " + (available ? "" : "disabled") + ' aria-label="' + escapeHtml(t.name) + ' 미리보기 포함"/></td>'
        + '<td><code class="pref-mono">' + escapeHtml(t.name) + '</code><br/><span class="pref-mono" style="color:var(--muted)">' + escapeHtml(argLabel(t.args, true)) + "</span></td>"
        + '<td><code class="pref-mono">' + escapeHtml(t.scope) + "</code>" + (available ? "" : '<br/><span style="color:var(--muted);font-size:11px">\'발행 초안 허용\'에서만</span>') + "</td>"
        + '<td style="font-size:12px">' + escapeHtml(t.desc) + "</td></tr>";
    }).join("");

    var listed = MCP_TOOLS.map(function (t) { return { name: t.name, description: t.desc, inputSchema: schemaFor(t.args, true) }; })
      .concat(proposedOn.map(function (t) { return { name: t.name, description: "[제안] " + t.desc, inputSchema: schemaFor(t.args, true) }; }));
    byId("pref-mcp-toolslist").textContent = JSON.stringify({ jsonrpc: "2.0", id: 1, result: { tools: listed } }, null, 2);
  }

  function initMcp() {
    renderMcpPanel();
    onChange("pref-mcp-write", function (el) {
      prefs.mcpPolicy.writeScope = el.value === "draft" ? "draft" : "none";
      savePrefs("제안 스코프 미리보기가 바뀌었습니다");
      renderMcpPanel();
    });
    var panel = byId("panel-mcp");
    panel.addEventListener("change", function (e) {
      var name = e.target.getAttribute && e.target.getAttribute("data-mcp-proposed");
      if (!name) return;
      var tools = Object.assign({}, prefs.mcpPolicy.proposedTools || {});
      tools[name] = e.target.checked;
      prefs.mcpPolicy.proposedTools = tools;
      savePrefs("제안 도구 " + name + " 미리보기 " + (e.target.checked ? "포함" : "제외"));
      renderMcpPanel();
    });
    panel.addEventListener("click", function (e) {
      var copy = e.target.closest("[data-copy-target]");
      if (!copy) return;
      var text = byId(copy.getAttribute("data-copy-target")).textContent;
      if (!navigator.clipboard) { showToast(text); return; }
      navigator.clipboard.writeText(text).then(function () { showToast("엔드포인트를 복사했습니다"); }, function () { showToast(text); });
    });
  }

  /* ── webmcp (per-theme exposure, registration, confirmation, tool table, simulator) ── */

  /* tab: client | kb | any. kind: read | ui | write | publish. untrusted: returns third-party content (untrustedContentHint). */
  var WEBMCP_TOOLS = [
    { name: "synd_get_view", title: "현재 화면 상태", tab: "any", kind: "read", desc: "현재 탭 · 테마 · 목록 스타일 · 선택 항목", input: {}, example: {} },
    { name: "kb_list_items", title: "스트림 항목 목록", tab: "kb", kind: "read", untrusted: true, desc: "현재 스트림에 보이는 항목 (필터 · 정렬 반영)", input: { stream: { type: "string", enum: ["all", "unread", "starred", "linked"] }, limit: { type: "integer", maximum: 50 } }, example: { stream: "unread", limit: 10 } },
    { name: "kb_get_item", title: "항목 읽기", tab: "kb", kind: "read", untrusted: true, desc: "항목 1건의 수집 원문 · 수집 태그 · 적재 상태", input: { item_id: { type: "string" } }, required: ["item_id"], example: { item_id: "i0-3" } },
    { name: "client_read_timeline", title: "타임라인 읽기", tab: "client", kind: "read", untrusted: true, desc: "열린 컬럼의 게시물 (CW · 필터 적용 결과 그대로)", input: { column: { type: "string", enum: ["home", "notifications", "local", "federated", "list"] }, limit: { type: "integer", maximum: 40 } }, example: { column: "home", limit: 5 } },
    { name: "client_search", title: "연결 서버 검색", tab: "client", kind: "read", untrusted: true, desc: "연결 서버 검색 (계정 · 해시태그 · 게시물)", input: { q: { type: "string" }, type: { type: "string", enum: ["accounts", "hashtags", "statuses"] } }, required: ["q"], example: { q: "#ActivityPub", type: "statuses" } },
    { name: "synd_set_view", title: "화면 전환", tab: "any", kind: "ui", desc: "탭 · 목록 스타일 · 분할 구조 전환", input: { tab: { type: "string", enum: ["client", "kb"] }, list_style: { type: "string", enum: ["list", "compact", "card", "roomy"] } }, example: { tab: "kb", list_style: "roomy" } },
    { name: "synd_open_item", title: "리더에 열기", tab: "any", kind: "ui", desc: "항목 · 게시물을 리더에 열기", input: { id: { type: "string" } }, required: ["id"], example: { id: "p0-12" } },
    { name: "client_compose_draft", title: "새 글 초안 채우기", tab: "client", kind: "ui", desc: "새 글 대화상자를 채움 (전송은 사용자가 직접)", input: { text: { type: "string" }, spoiler_text: { type: "string" }, visibility: { type: "string", enum: ["public", "unlisted", "private", "direct"] } }, required: ["text"], example: { text: "Mastodon 5.0 인용 정책 정리 #ActivityPub", visibility: "unlisted" } },
    { name: "kb_ingest_item", title: "프로젝트 적재로 전달", tab: "kb", kind: "write", desc: "항목을 프로젝트 적재로 전달 (초점 지정)", input: { item_id: { type: "string" }, focus: { type: "string" } }, required: ["item_id"], example: { item_id: "i0-3", focus: "SQLite WAL 모드 변경점" } },
    { name: "kb_subscribe", title: "수집 대상 추가", tab: "kb", kind: "write", desc: "수집 대상 추가 (AP · AT는 경유 계정 필수)", input: { url: { type: "string" }, folder: { type: "string" }, via_account: { type: "string" } }, required: ["url"], example: { url: "https://lwn.net/headlines/rss", folder: "엔지니어링 블로그" } },
    { name: "client_interact", title: "게시물 상호작용", tab: "client", kind: "write", external: true, desc: "좋아요 · 북마크 · 부스트 (연결 계정으로 서버 반영)", input: { status_id: { type: "string" }, action: { type: "string", enum: ["favourite", "bookmark", "reblog"] } }, required: ["status_id", "action"], example: { status_id: "p0-12", action: "bookmark" } },
    { name: "kb_publish", title: "발행 대화상자 열기", tab: "kb", kind: "publish", external: true, desc: "부스트 · 인용 · 적재 발행 (발행 대화상자에서 미리보기 확인 후 전송)", input: { item_id: { type: "string" }, mode: { type: "string", enum: ["boost", "quote", "ingested_publish"] }, accounts: { type: "array", items: { type: "string" } } }, required: ["item_id", "mode"], example: { item_id: "i0-3", mode: "quote", accounts: ["acc0-ap"] } }
  ];

  function webmcpState() {
    if (!custom.webmcp) custom.webmcp = {};
    return custom.webmcp;
  }
  function webmcpExposed() {
    var st = webmcpState();
    return st.exposed !== undefined ? st.exposed : true;
  }
  function webmcpToolBlocked(t) {
    if (t.external && !THEME.isPublic) return "비공개 테마";
    if (t.kind === "publish" && !getThemeAccounts().some(function (a) { return a.canPublish; })) return "발행 계정 없음";
    return "";
  }
  function webmcpToolOn(t) {
    if (webmcpToolBlocked(t)) return false;
    var st = webmcpState().tools || {};
    return st[t.name] !== undefined ? st[t.name] : t.kind !== "publish";
  }
  function webmcpNeedsConfirm(t) {
    var mode = prefs.webmcpPolicy.confirm;
    if (t.kind === "publish" || t.kind === "write") return true;
    if (mode === "all") return true;
    return mode === "ui_and_write" && t.kind === "ui";
  }
  function webmcpAnnotations(t) {
    var a = { readOnlyHint: t.kind === "read" };
    if (t.kind === "write" || t.kind === "publish") a.consequentialHint = true;
    if (t.untrusted) a.untrustedContentHint = true;
    return a;
  }
  function webmcpKindLabel(kind) { return { read: "읽기", ui: "화면 조작", write: "쓰기", publish: "외부 발행" }[kind] || kind; }
  function webmcpKindPill(kind) { return { read: "pref-pill-mcp", ui: "pref-pill-at", write: "pref-pill-web", publish: "pref-pill-warn" }[kind] || ""; }
  function webmcpTabLabel(tab) { return { any: "두 탭", client: "🌐 연합 클라이언트", kb: "📚 지식베이스" }[tab] || tab; }
  function webmcpSchema(t) {
    var schema = { type: "object", properties: t.input || {} };
    if (t.required) schema.required = t.required;
    return schema;
  }

  function webmcpRegistrationCode(t) {
    var confirm = webmcpNeedsConfirm(t);
    var indent = function (s) { return s.replace(/\n/g, "\n  "); };
    var lines = [
      "const mc = document.modelContext ?? navigator.modelContext;  // 사양 · 이전 초안 이름",
      "const registration = new AbortController();               // 테마 전환 · 비활성화 시 abort()",
      "",
      "await mc.registerTool({",
      "  name: " + JSON.stringify(t.name) + ",",
      "  title: " + JSON.stringify(t.title) + ",",
      "  description: " + JSON.stringify(t.desc) + ",",
      "  inputSchema: " + indent(JSON.stringify(webmcpSchema(t), null, 2)) + ",",
      "  annotations: " + JSON.stringify(webmcpAnnotations(t)).replace(/"(\w+)":/g, "$1: ").replace(/,/g, ", ") + ",",
      "  async execute(input, { signal }) {"
    ];
    if (confirm) {
      lines.push(
        "    const ok = await claireConfirm(" + JSON.stringify(t.name) + ", input, { signal });  // 페이지 자체 확인 창",
        "    if (!ok) return { ok: false, error: \"user_denied\" };"
      );
    }
    lines.push(
      "    return SyndicationTools." + t.name + "(input, { signal });  // JSON 직렬화 가능한 값",
      "  }",
      "}, { signal: registration.signal });"
    );
    if (prefs.webmcpPolicy.registration === "both" && t.name === "kb_subscribe") {
      lines.push("", "<!-- 선언형 (explainer 제안, 사양 외) -->",
        "<form id=\"subscribe-form\" toolname=\"kb_subscribe\"",
        "      tooldescription=\"수집 대상 추가 (AP · AT는 경유 계정 필수)\">",
        "  <input name=\"url\" toolparamdescription=\"피드 URL 또는 @계정\">");
    }
    return lines.join("\n");
  }

  function webmcpSimResult(t, input) {
    switch (t.name) {
      case "synd_get_view": return { theme: THEME_REF, tab: prefs.appTab, list_style: prefs.listStyle, layout: prefs.layoutMode, selected: null };
      case "kb_list_items": return { items: [{ id: "i0-3", title: "SQLite 3.51 Release", unread: true, linked: false }], truncated: false };
      case "kb_get_item": return { id: input.item_id, source_tags: ["#SQLite", "#Release"], linked_doc_id: null };
      case "client_read_timeline": return { column: input.column || "home", statuses: [{ id: "p0-12", account: "@w3c@w3c.social", spoiler_text: "", text: "…" }] };
      case "client_search": return { statuses: 3, accounts: 0, hashtags: 1 };
      case "synd_set_view": return { applied: input };
      case "synd_open_item": return { opened: input.id };
      case "client_compose_draft": return { compose_dialog: "opened", sent: false, note: "전송은 사용자가 직접 누릅니다" };
      case "kb_ingest_item": return { ok: true, forwarded: input.item_id, focus: input.focus || "", queue: "project_ingest" };
      case "kb_subscribe": return { ok: true, target_id: "f_agent_1", folder: input.folder || "기본 폴더" };
      case "client_interact": return { ok: true, status_id: input.status_id, action: input.action, server: "social.example.com" };
      case "kb_publish": return { ok: true, dialog: "publish", mode: input.mode, accounts: input.accounts || [], note: "전송은 발행 대화상자에서 사용자가 누릅니다" };
      default: return {};
    }
  }

  function renderWebMcpPanel() {
    var hasDoc = "modelContext" in document;
    var hasNav = typeof navigator !== "undefined" && "modelContext" in navigator;
    var enabled = !!prefs.webmcpPolicy.enabled;
    var exposed = webmcpExposed();
    var support = byId("pref-webmcp-support");
    support.dataset.tone = hasDoc || hasNav ? "ok" : "";
    var insecure = window.isSecureContext ? "" : " 이 페이지는 보안 컨텍스트(HTTPS)가 아니어서 지원 브라우저에서도 API가 노출되지 않습니다.";
    support.innerHTML = hasDoc
      ? "✅ 이 브라우저에서 <code>document.modelContext</code>(사양 이름)를 감지했습니다." + insecure
      : (hasNav
        ? "✅ 이 브라우저는 이전 초안 이름인 <code>navigator.modelContext</code>를 제공합니다. 등록 코드는 두 이름을 모두 시도합니다." + insecure
        : "⚪ 이 브라우저는 <code>document.modelContext</code>를 제공하지 않습니다. 설정은 저장되며, WebMCP를 지원하는 브라우저에서 <code>/syndication</code>을 열면 적용됩니다." + insecure);

    byId("pref-webmcp-enabled").checked = enabled;
    byId("pref-webmcp-exposed").checked = exposed;
    byId("pref-webmcp-exposed").disabled = !enabled;
    byId("pref-webmcp-exposed-desc").innerHTML = THEME.isPublic
      ? "테마를 바꾸면 이전 등록 신호를 <code>abort()</code>하고 새 테마의 도구를 다시 등록합니다."
      : "🔒 비공개 테마: 조회 · 화면 조작 · 내부 적재 도구만 등록되고, 외부 서버에 반영되는 도구(상호작용 · 발행)는 등록되지 않습니다.";
    byId("pref-webmcp-mode").value = prefs.webmcpPolicy.registration === "both" ? "both" : "imperative";
    byId("pref-webmcp-confirm").value = prefs.webmcpPolicy.confirm;
    byId("pref-webmcp-activity").checked = !!prefs.webmcpPolicy.showAgentActivity;

    var live = enabled && exposed;
    byId("pref-webmcp-tools-tbody").innerHTML = WEBMCP_TOOLS.map(function (t) {
      var blocked = webmcpToolBlocked(t);
      var on = webmcpToolOn(t);
      var lockedConfirm = t.kind === "publish" || t.kind === "write";
      var ann = webmcpAnnotations(t);
      var hints = Object.keys(ann).filter(function (k) { return ann[k]; }).map(function (k) { return k.replace("Hint", ""); }).join(" · ") || "—";
      return '<tr data-off="' + (on && live ? "false" : "true") + '">'
        + '<td><input type="checkbox" switch data-webmcp-tool="' + escapeHtml(t.name) + '" ' + (on ? "checked" : "") + " " + (blocked || !live ? "disabled" : "") + ' aria-label="' + escapeHtml(t.name) + ' 등록"/></td>'
        + '<td><code class="pref-mono">' + escapeHtml(t.name) + '</code><br/><span style="color:var(--muted);font-size:11px">' + escapeHtml(t.title) + "</span>" + (blocked ? '<br/><span style="color:var(--muted);font-size:11px">⛔ ' + escapeHtml(blocked) + "</span>" : "") + "</td>"
        + '<td style="font-size:12px;white-space:nowrap">' + webmcpTabLabel(t.tab) + "</td>"
        + '<td><span class="pref-pill ' + webmcpKindPill(t.kind) + '">' + webmcpKindLabel(t.kind) + "</span></td>"
        + '<td class="pref-mono">' + escapeHtml(hints) + "</td>"
        + '<td style="font-size:12px;white-space:nowrap">' + (webmcpNeedsConfirm(t) ? (lockedConfirm ? "🔒 항상" : "✋ 확인") : "—") + "</td>"
        + '<td style="font-size:12px">' + escapeHtml(t.desc) + "</td></tr>";
    }).join("");

    var sel = byId("pref-webmcp-sim-tool");
    var prev = sel.value || "kb_ingest_item";
    sel.innerHTML = WEBMCP_TOOLS.map(function (t) { return '<option value="' + escapeHtml(t.name) + '" ' + (t.name === prev ? "selected" : "") + ">" + escapeHtml(t.name) + " — " + webmcpKindLabel(t.kind) + "</option>"; }).join("");
    renderWebMcpSimTool(false);
  }

  function currentSimTool() {
    var name = byId("pref-webmcp-sim-tool").value;
    return WEBMCP_TOOLS.find(function (t) { return t.name === name; }) || WEBMCP_TOOLS[0];
  }

  function renderWebMcpSimTool(resetInput) {
    var t = currentSimTool();
    byId("pref-webmcp-code").textContent = webmcpRegistrationCode(t);
    var input = byId("pref-webmcp-sim-input");
    if (resetInput || !input.value || input.dataset.tool !== t.name) {
      input.value = JSON.stringify(t.example || {}, null, 2);
      input.dataset.tool = t.name;
      byId("pref-webmcp-sim-output").innerHTML = "";
    }
  }

  function simBlock(label, obj, isError) {
    return '<span style="font-size:12px;font-weight:700;' + (isError ? "color:#cf222e" : "") + '">' + escapeHtml(label) + '</span><pre class="pref-code">' + escapeHtml(typeof obj === "string" ? obj : JSON.stringify(obj, null, 2)) + "</pre>";
  }
  function setSimOutput(html) { byId("pref-webmcp-sim-output").innerHTML = html; }

  function runWebMcpSimulation() {
    var t = currentSimTool();
    var input;
    try {
      input = JSON.parse(byId("pref-webmcp-sim-input").value || "{}");
    } catch (_e) {
      setSimOutput(simBlock("입력 JSON 파싱 실패", { error: "invalid JSON" }, true));
      return;
    }
    if (!prefs.webmcpPolicy.enabled || !webmcpExposed() || !webmcpToolOn(t)) {
      var why = !prefs.webmcpPolicy.enabled || !webmcpExposed() ? "WebMCP 비활성" : (webmcpToolBlocked(t) || "등록 해제된 도구");
      setSimOutput(simBlock("getTools()에 없음 → 에이전트가 호출할 수 없음", { reason: why }, true));
      return;
    }
    var missing = (t.required || []).filter(function (k) { return input[k] === undefined || input[k] === ""; });
    if (missing.length) {
      setSimOutput(simBlock("도구가 입력 오류를 JSON으로 반환", { ok: false, error: "invalid_input", missing: missing }, true));
      return;
    }
    if (!webmcpNeedsConfirm(t)) {
      setSimOutput(simBlock("execute() 결과 → JSON 문자열로 에이전트에 전달", webmcpSimResult(t, input)));
      return;
    }
    setSimOutput('<div class="pref-confirm" role="alertdialog" aria-label="에이전트 요청 확인">'
      + '<div>🕸️ <b>브라우저 에이전트</b>가 <code class="pref-mono">' + escapeHtml(t.name) + "</code> (" + escapeHtml(t.title) + ") 실행을 요청합니다.</div>"
      + '<pre class="pref-code" style="max-height:120px">' + escapeHtml(JSON.stringify(input, null, 2)) + "</pre>"
      + (t.external ? '<div style="font-size:12px;color:var(--muted)">연결 계정으로 외부 서버에 반영됩니다.</div>' : "")
      + '<div style="font-size:12px;color:var(--muted)">이 창은 페이지가 직접 띄웁니다. 에이전트가 호출을 취소하면 <code>signal</code>이 abort되어 창이 닫힙니다.</div>'
      + '<div class="pref-confirm-actions"><button type="button" class="pref-btn" data-sim-answer="deny">거부</button><button type="button" class="pref-btn pref-btn-accent" data-sim-answer="allow">허용</button></div>'
      + "</div>");
    document.querySelectorAll("[data-sim-answer]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        if (btn.getAttribute("data-sim-answer") === "allow") {
          setSimOutput(simBlock("사용자 허용 → execute() 결과", webmcpSimResult(t, input)));
        } else {
          setSimOutput(simBlock("사용자 거부 → execute() 결과", { ok: false, error: "user_denied" }, true));
        }
      });
    });
  }

  function initWebMcp() {
    renderWebMcpPanel();
    var pol = function (id, fn) {
      onChange(id, function (el) { fn(el); savePrefs(); renderWebMcpPanel(); });
    };
    pol("pref-webmcp-enabled", function (el) { prefs.webmcpPolicy.enabled = el.checked; });
    pol("pref-webmcp-mode", function (el) { prefs.webmcpPolicy.registration = el.value; });
    pol("pref-webmcp-confirm", function (el) { prefs.webmcpPolicy.confirm = el.value; });
    pol("pref-webmcp-activity", function (el) { prefs.webmcpPolicy.showAgentActivity = el.checked; });
    onChange("pref-webmcp-exposed", function (el) {
      webmcpState().exposed = el.checked;
      if (persist()) showToast(el.checked ? "이 테마에서 WebMCP 도구를 등록합니다" : "이 테마의 WebMCP 도구 등록을 중단했습니다");
      renderWebMcpPanel();
    });
    byId("panel-webmcp").addEventListener("change", function (e) {
      var name = e.target.getAttribute && e.target.getAttribute("data-webmcp-tool");
      if (!name) return;
      var st = webmcpState();
      st.tools = Object.assign({}, st.tools || {});
      st.tools[name] = e.target.checked;
      if (persist()) showToast("WebMCP 도구 " + name + " " + (e.target.checked ? "등록" : "등록 해제"));
      renderWebMcpPanel();
    });
    onChange("pref-webmcp-sim-tool", function () { renderWebMcpSimTool(true); });
    byId("btn-webmcp-sim-run").addEventListener("click", runWebMcpSimulation);
  }

  var INIT = {
    reader: initReader,
    connections: initConnections,
    feeds: initFeeds,
    tags: initTags,
    rules: initRules,
    outbound: initOutbound,
    opml: initOpml,
    mcp: initMcp,
    webmcp: initWebMcp
  };
  if (!INIT[SECTION]) throw new Error("unknown settings syndication section: " + SECTION);
  INIT[SECTION]();

  window.ClaireSettingsSyndication = { storeKey: STORE_KEY, section: SECTION, themeRef: THEME_REF };
})();
