/* Claire Bible — stage bar and federation client view of /syndication (FEDERATION_REMOTE_READING_DESIGN.md Sections 1.3, 4.4, 5.3, and 8, unit FP4-5).
 *
 * Rules of this file:
 * - The client and the knowledge base are two views of this page. The stage bar selects the view: stage 1 is the client, stages 2 and 3 are the knowledge base (syndication_live.js).
 * - The timeline list has plain text only. The body of a post is a sandbox iframe from the render URL that the server gives (the usercontent origin). This file never puts remote HTML into the page.
 * - Each text of this view is a catalog key (ClaireI18n). Each value from the server goes through esc().
 * - Live update is periodic polling while the tab is visible, and once when the tab becomes visible again (RD18).
 */
(function () {
  "use strict";

  // Line icon as inline SVG (static/js/icons.js).
  const ic = (name) => (window.ClaireIcons ? window.ClaireIcons.svg(name) : "");

  const bridge = window.ClaireSyndication;
  // Feed view (syndication_feeds.js, FP4-F3). While Feeds.active, the list pane and the reader pane show feed items.
  const Feeds = window.ClaireSyndicationFeeds;
  // Compose controls (syndication_compose.js, FP4-9): reply, new note, edit, delete, favourite, and boost.
  const Compose = window.ClaireSyndicationCompose;
  const cfg = window.__CLAIRE_CONFIG || {};
  const I18n = window.ClaireI18n;
  if (!bridge || !I18n) return;
  const tr = (key, params) => I18n.t(key, params);
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const $ = (id) => document.getElementById(id);

  const IS_OWNER = cfg.authScope === "owner";
  const IS_NETWORK = cfg.serverMode === "network";
  const STAGES = ["collect", "ingest", "publish"];
  const STREAMS = [
    { key: "home", icon: ic("home"), label: "synd.client.stream.home", filter: "" },
    { key: "unread", icon: ic("dot"), label: "synd.client.stream.unread", filter: "unread=1" },
    { key: "mentions", icon: "＠", label: "synd.client.stream.mentions", filter: "mention=1" },
    { key: "starred", icon: "★", label: "synd.client.stream.starred", filter: "starred=1" }
  ];
  const POLL_MS = 30000;
  const PAGE = 40;

  const C = {
    stage: "ingest",
    stream: "home",
    items: [],
    nextMaxId: null,
    counts: null,
    loaded: false,
    loading: false,
    error: "",
    selectedId: null,
    detail: null,
    openWarnings: new Set(),
    threadFetchId: null,
    following: null,
    actor: null,
    publishedLoaded: false,
    ingestItemId: null,
    seq: 0
  };

  I18n.applyDom(document);

  async function api(method, path, body) {
    const init = { method, headers: { Accept: "application/json" }, credentials: "same-origin" };
    if (body !== undefined) {
      init.headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(body);
    }
    const resp = await fetch(path, init);
    const data = await resp.json().catch(() => ({}));
    return { ok: resp.ok, status: resp.status, data };
  }

  function errorText(result) {
    return String((result.data && (result.data.detail || result.data.error)) || `HTTP ${result.status}`);
  }

  function timeAgo(seconds) {
    if (!seconds) return "";
    const diff = Math.round(seconds - Date.now() / 1000);
    const abs = Math.abs(diff);
    let rtf;
    try { rtf = new Intl.RelativeTimeFormat(document.documentElement.lang || "en", { numeric: "auto" }); } catch (_) { return new Date(seconds * 1000).toLocaleString(); }
    if (abs < 60) return rtf.format(Math.round(diff), "second");
    if (abs < 3600) return rtf.format(Math.round(diff / 60), "minute");
    if (abs < 86400) return rtf.format(Math.round(diff / 3600), "hour");
    if (abs < 86400 * 30) return rtf.format(Math.round(diff / 86400), "day");
    return new Date(seconds * 1000).toLocaleDateString();
  }

  function authorName(actor) {
    if (!actor) return "";
    return actor.name || actor.handle || actor.iri || "";
  }

  // --- Stage bar -----------------------------------------------------------

  function setStage(stage, options) {
    if (!STAGES.includes(stage)) stage = "ingest";
    C.stage = stage;
    document.body.setAttribute("data-stage", stage);
    const client = stage === "collect";
    $("panel-client").hidden = !client;
    $("panel-kb").hidden = client;
    $("pub-filter").hidden = stage !== "publish";
    document.querySelectorAll("[data-stage-btn]").forEach((btn) => {
      const on = btn.getAttribute("data-stage-btn") === stage;
      btn.classList.toggle("is-active", on);
      if (on) btn.setAttribute("aria-current", "page"); else btn.removeAttribute("aria-current");
    });
    if (!(options && options.keepUrl)) {
      const url = new URL(window.location.href);
      url.searchParams.set("stage", stage);
      window.history.replaceState(null, "", url);
    }
    if (stage === "publish") {
      loadPublished().then(() => bridge.setPublishFilter(bridge.state.pubFilter === "all" ? "unpublished" : bridge.state.pubFilter));
    } else if (bridge.state.pubFilter !== "all") {
      bridge.setPublishFilter("all");
    }
    if (client) {
      renderClient();
      if (Feeds) Feeds.open();
      if (IS_NETWORK && !C.loaded) loadTimeline(true);
      if (IS_NETWORK && C.following === null) loadFollowing();
    }
    renderStageBar();
  }

  function renderStageBar() {
    const bar = $("stagebar");
    if (!bar) return;
    bar.hidden = !IS_OWNER;
    if (!IS_OWNER) return;
    const set = (id, parts) => { const el = $(id); if (el) el.textContent = parts.filter(Boolean).join(" · "); };
    const counts = C.counts;
    set("stage-collect-counts", IS_NETWORK
      ? [counts ? tr("home.flow.unread", { count: counts.unread }) : "", counts ? tr("synd.stage.mentions", { count: counts.unread_mentions }) : ""]
      : [tr("synd.stage.single_mode")]);
    const theme = bridge.curTheme();
    const docs = (theme && theme.documents) || [];
    set("stage-ingest-counts", [theme && theme.loaded ? tr("home.flow.documents", { count: docs.length }) : "", counts && counts.ingested ? tr("synd.stage.from_posts", { count: counts.ingested }) : ""]);
    if (!IS_NETWORK) {
      set("stage-publish-counts", [tr("synd.stage.single_mode")]);
    } else if (C.publishedLoaded && theme && theme.loaded) {
      const published = docs.filter((d) => bridge.state.publishedKeys.has(`${theme.id}:${d.id}`)).length;
      set("stage-publish-counts", [tr("synd.stage.published", { count: published }), tr("synd.stage.unpublished", { count: docs.length - published })]);
    } else {
      set("stage-publish-counts", [counts ? tr("synd.stage.published", { count: counts.published }) : ""]);
    }
    document.querySelectorAll("[data-pub-filter]").forEach((btn) => {
      const on = btn.getAttribute("data-pub-filter") === bridge.state.pubFilter;
      btn.classList.toggle("tt-btn-pri", on);
      btn.setAttribute("aria-pressed", on ? "true" : "false");
    });
    if (Feeds) Feeds.decorateStageBar();
  }

  // The published documents of the account, as "themeId:documentId" keys for the filter of stage 3.
  async function loadPublished() {
    if (!IS_NETWORK || !IS_OWNER) { C.publishedLoaded = true; return; }
    const keys = new Set();
    // The counters of each published document (FP4-8), as a text for the knowledge base view.
    const reactions = {};
    // The object number and the quote policy of each published document (FP4-9Q).
    const objects = {};
    let maxId = null;
    for (let page = 0; page < 10; page++) {
      const result = await api("GET", `/api/federation/objects?limit=80${maxId ? `&max_id=${encodeURIComponent(maxId)}` : ""}`);
      if (!result.ok || !Array.isArray(result.data.items)) break;
      result.data.items.forEach((item) => {
        keys.add(`${item.kb_id}:${item.document_id}`);
        reactions[`${item.kb_id}:${item.document_id}`] = `${tr("synd.kb.reactions", { likes: item.likes || 0, boosts: item.announces || 0 })} · ${tr("synd.quote.count", { count: item.quotes || 0 })}`;
        objects[`${item.kb_id}:${item.document_id}`] = { id: item.id, quote_policy: item.quote_policy };
      });
      maxId = result.data.next_max_id;
      if (!maxId) break;
    }
    bridge.state.publishedKeys = keys;
    C.publishedObjects = objects;
    C.publishedLoaded = true;
    bridge.state.reactionLabels = reactions;
    if (keys.size) bridge.setPublishFilter(bridge.state.pubFilter);
  }

  // --- Timeline ------------------------------------------------------------

  function streamOf(key) {
    return STREAMS.find((s) => s.key === key) || STREAMS[0];
  }

  async function loadTimeline(reset) {
    if (!IS_NETWORK || !IS_OWNER) return;
    const seq = ++C.seq;
    C.loading = true;
    if (reset) { C.items = []; C.nextMaxId = null; }
    renderList();
    const filter = streamOf(C.stream).filter;
    const cursor = !reset && C.nextMaxId ? `&max_id=${encodeURIComponent(C.nextMaxId)}` : "";
    const result = await api("GET", `/api/federation/timeline?limit=${PAGE}${filter ? `&${filter}` : ""}${cursor}`);
    if (seq !== C.seq) return;
    C.loading = false;
    C.loaded = true;
    if (!result.ok) {
      C.error = errorText(result);
    } else {
      C.error = "";
      C.items = reset ? result.data.items : C.items.concat(result.data.items);
      C.nextMaxId = result.data.next_max_id;
      C.counts = result.data.counts;
    }
    renderClient();
    renderStageBar();
  }

  // New items come first. A poll reads the first page and puts the items that the list does not have before the others.
  async function pollTimeline() {
    if (!IS_NETWORK || !IS_OWNER || document.hidden || C.loading) return;
    const filter = streamOf(C.stream).filter;
    const seq = C.seq;
    const result = await api("GET", `/api/federation/timeline?limit=${PAGE}${filter ? `&${filter}` : ""}`);
    if (seq !== C.seq || !result.ok) return;
    C.counts = result.data.counts;
    // The quote requests of remote actors (FP4-9Q) come with the same poll.
    if (Compose) Compose.loadQuotes();
    if (C.stage === "collect" && C.loaded) {
      const known = new Set(C.items.map((item) => item.id));
      const fresh = result.data.items.filter((item) => !known.has(item.id));
      if (fresh.length) C.items = fresh.concat(C.items);
      renderClient();
    }
    renderStageBar();
  }

  function renderStreams() {
    const el = $("client-streams");
    if (!el) return;
    const counts = C.counts || {};
    const badge = { home: counts.total, unread: counts.unread, mentions: counts.unread_mentions, starred: counts.starred };
    el.innerHTML = STREAMS.map((s) => {
      const n = badge[s.key];
      const fresh = (s.key === "unread" || s.key === "mentions") && n > 0;
      return `<button type="button" class="tt-tree-item ${C.stream === s.key && !(Feeds && Feeds.active) ? "active" : ""}" data-client-stream="${s.key}"><span>${s.icon} ${esc(tr(s.label))}</span><span class="tt-count-pill ${fresh ? "fresh" : ""}">${n ? esc(n) : ""}</span></button>`;
    }).join("") + (counts.private_messages ? `<p class="synd-note" id="client-private-note">${esc(tr("synd.client.private_messages", { count: counts.private_messages }))}</p>` : "");
    const title = $("client-stream-title");
    if (title && !(Feeds && Feeds.active)) title.textContent = tr(streamOf(C.stream).label);
  }

  function itemBadges(item) {
    const badges = [];
    if (item.mention) badges.push(`<span class="tt-link-dot cl-inbox">${esc(tr("synd.client.badge.mention"))}</span>`);
    if (item.quote) badges.push(`<span class="tt-link-dot unlinked">${ic("quote")} ${esc(tr("synd.quote.badge"))}</span>`);
    if (item.in_reply_to) badges.push(`<span class="tt-link-dot unlinked">↩ ${esc(tr("synd.client.badge.reply"))}</span>`);
    if (item.linked) badges.push(`<span class="tt-link-dot linked">${ic("book")} ${esc(tr("synd.client.badge.ingested"))}</span>`);
    if (item.attachments) badges.push(`<span class="tt-link-dot unlinked">${ic("paperclip")} ${esc(item.attachments)}</span>`);
    return badges.join("");
  }

  function renderList() {
    const el = $("client-list");
    const notice = $("client-notice");
    if (!el) return;
    if (Feeds && Feeds.active) return;
    if (!IS_NETWORK) {
      notice.hidden = false;
      notice.textContent = tr("synd.client.single_mode");
      el.innerHTML = "";
      return;
    }
    const notActor = C.actor && !C.actor.is_actor;
    notice.hidden = !(notActor || C.error);
    notice.textContent = C.error ? tr("synd.error.request", { error: C.error }) : notActor ? tr("synd.client.not_actor") : "";
    if (!C.loaded && C.loading) {
      el.innerHTML = `<div class="synd-empty">${esc(tr("synd.client.loading"))}</div>`;
      return;
    }
    if (!C.items.length) {
      el.innerHTML = `<div class="synd-empty">${esc(tr(C.stream === "home" ? "synd.client.empty_home" : "synd.client.empty"))}</div>`;
      return;
    }
    const rows = C.items.map((item) => {
      const author = item.author || {};
      const boosted = item.announced_by ? `<div class="synd-post-boost">${ic("repeat")} ${esc(tr("synd.client.boosted_by", { name: authorName(item.announced_by) }))}</div>` : "";
      const body = item.summary
        ? `<div class="synd-post-text synd-post-cw">${ic("warning")} ${esc(item.summary)}</div>`
        : `${item.name ? `<div class="synd-post-title">${esc(item.name)}</div>` : ""}<div class="synd-post-text">${esc(item.text)}</div>`;
      return `<article class="synd-post ${item.unread ? "is-unread" : ""} ${String(C.selectedId) === String(item.id) ? "is-selected" : ""}" data-item-id="${esc(item.id)}" tabindex="0">
        ${boosted}
        <div class="synd-post-meta"><span class="synd-post-author">${esc(authorName(author))}</span><span class="synd-post-handle">${esc(author.handle || "")}</span><span class="synd-post-time">${esc(timeAgo(item.published_at || item.received_at))}</span>${itemBadges(item)}</div>
        ${body}
        <div class="synd-post-actions">
          <button type="button" class="tt-icon-btn ${item.starred ? "starred" : ""}" data-item-star="${esc(item.id)}" title="${esc(tr(item.starred ? "synd.client.action.unstar" : "synd.client.action.star"))}" aria-label="${esc(tr(item.starred ? "synd.client.action.unstar" : "synd.client.action.star"))}" aria-pressed="${item.starred ? "true" : "false"}">${item.starred ? "★" : "☆"}</button>
          <button type="button" class="tt-btn synd-mini" data-item-read="${esc(item.id)}">${esc(tr(item.unread ? "synd.client.action.mark_read" : "synd.client.action.mark_unread"))}</button>
          ${item.linked
            ? `<button type="button" class="tt-btn synd-mini" data-item-doc="${esc(item.id)}">${ic("book")} ${esc(tr("synd.client.action.open_document"))}</button>`
            : `<button type="button" class="tt-btn synd-mini" data-item-ingest="${esc(item.id)}">${ic("book")} ${esc(tr("synd.client.action.ingest"))}</button>`}
          ${Compose ? Compose.actionsHtml(item, true) : ""}
        </div>
      </article>`;
    }).join("");
    const more = C.nextMaxId ? `<div class="synd-more"><button type="button" class="tt-btn" id="client-load-more" ${C.loading ? "disabled" : ""}>${esc(tr("synd.client.load_more"))}</button></div>` : "";
    el.innerHTML = rows + more;
  }

  // --- Reader and thread ---------------------------------------------------

  function frameHtml(entry) {
    // The frame has no script permission and an opaque origin. The server sends the same policy in the Content-Security-Policy header of the page.
    return `<iframe class="synd-frame" sandbox="allow-popups allow-popups-to-escape-sandbox" referrerpolicy="no-referrer" loading="lazy" title="${esc(tr("synd.client.frame_title", { name: authorName(entry.author) }))}" src="${esc(entry.render_url)}"></iframe>`;
  }

  function entryHtml(entry, main) {
    const author = entry.author || {};
    const warned = entry.sensitive || Boolean(entry.summary);
    const open = !warned || C.openWarnings.has(String(entry.id));
    // Section 8 rule 4: an item with a content warning shows the summary first. The body loads after a click.
    const body = open
      ? frameHtml(entry)
      : `<div class="synd-cw"><span>${ic("warning")} ${esc(entry.summary || tr("synd.client.sensitive"))}</span><button type="button" class="tt-btn" data-open-warning="${esc(entry.id)}">${esc(tr("synd.client.action.show_content"))}</button></div>`;
    const link = entry.url || entry.object_iri;
    return `<section class="synd-entry ${main ? "is-main" : ""}" data-entry-id="${esc(entry.id)}">
      <header class="synd-entry-head">
        <div><b>${esc(authorName(author))}</b> <span class="synd-post-handle">${esc(author.handle || "")}</span>${entry.announced_by ? ` <span class="synd-post-boost">${ic("repeat")} ${esc(tr("synd.client.boosted_by", { name: authorName(entry.announced_by) }))}</span>` : ""}</div>
        <div class="synd-entry-tools"><span class="synd-post-time">${esc(timeAgo(entry.published_at || entry.received_at))}</span><a class="tt-ext-link" href="${esc(link)}" target="_blank" rel="noopener noreferrer nofollow">${esc(tr("synd.client.action.open_original"))} ↗</a></div>
      </header>
      ${entry.name ? `<h3 class="synd-entry-title">${esc(entry.name)}</h3>` : ""}
      ${Compose ? `<div class="synd-entry-actions">${Compose.actionsHtml(entry, false)}</div>${Compose.quoteHtml(entry)}` : ""}
      ${warned && open ? `<div class="synd-cw is-open">${ic("warning")} ${esc(entry.summary || tr("synd.client.sensitive"))}</div>` : ""}
      ${body}
    </section>`;
  }

  function renderReader() {
    const el = $("client-reader");
    if (!el) return;
    if (Feeds && Feeds.active) return;
    if (!IS_NETWORK) { el.innerHTML = ""; return; }
    const detail = C.detail;
    if (!detail || String(detail.item.id) !== String(C.selectedId)) {
      el.innerHTML = `<div class="synd-empty">${esc(tr(C.selectedId ? "synd.client.loading" : "synd.client.select_prompt"))}</div>`;
      return;
    }
    const item = detail.item;
    const actions = `<div class="synd-reader-actions">
      <button type="button" class="tt-btn" data-item-star="${esc(item.id)}" aria-pressed="${item.starred ? "true" : "false"}">${item.starred ? "★" : "☆"} ${esc(tr(item.starred ? "synd.client.action.unstar" : "synd.client.action.star"))}</button>
      ${item.linked
        ? `<button type="button" class="tt-btn" data-item-doc="${esc(item.id)}">${ic("book")} ${esc(tr("synd.client.action.open_document"))}</button><button type="button" class="tt-btn tt-btn-pri" data-item-publish="${esc(item.id)}">${ic("megaphone")} ${esc(tr("synd.client.action.publish"))}</button>`
        : `<button type="button" class="tt-btn tt-btn-pri" data-item-ingest="${esc(item.id)}">${ic("book")} ${esc(tr("synd.client.action.ingest"))}</button>`}
    </div>`;
    const thread = detail.thread.length > 1
      ? `<h2 class="synd-thread-title">${esc(tr("synd.client.thread_title", { count: detail.thread.length }))}</h2>${detail.thread.map((entry) => entryHtml(entry, String(entry.id) === String(item.id))).join("")}`
      : entryHtml(item, true);
    // Thread fetch (FP4-6): a note while the server fetches the thread. fetchThread() shows and hides it.
    const fetchNote = `<div class="synd-thread-fetch" id="client-thread-fetch" role="status" ${String(C.threadFetchId) === String(item.id) ? "" : "hidden"}>${esc(tr("synd.client.thread_fetching"))}</div>`;
    el.innerHTML = `<div class="synd-reader">${actions}${fetchNote}${thread}</div>`;
    const main = el.querySelector(".synd-entry.is-main");
    if (main && detail.thread.length > 1) main.scrollIntoView({ block: "nearest" });
  }

  async function selectItem(id) {
    C.selectedId = id;
    C.detail = null;
    C.threadFetchId = null;
    renderList();
    renderReader();
    const result = await api("GET", `/api/federation/timeline/${encodeURIComponent(id)}`);
    if (String(C.selectedId) !== String(id)) return;
    if (!result.ok) {
      $("client-reader").innerHTML = `<div class="synd-empty">${esc(tr("synd.error.request", { error: errorText(result) }))}</div>`;
      return;
    }
    C.detail = result.data;
    renderReader();
    // On a narrow screen the reader is below the list.
    if (window.matchMedia("(max-width: 720px)").matches) $("client-reader-pane").scrollIntoView({ block: "start" });
    const listed = C.items.find((item) => String(item.id) === String(id));
    if (listed && listed.unread) patchItem(id, { unread: false });
    fetchThread(id);
  }

  // --- Thread fetch (FEDERATION_REMOTE_READING_DESIGN.md Section 6.5 rule 4, unit FP4-6) ---
  // When the owner opens an item, the server makes a fetch task for its thread (one at most in the refetch time of the server). While a task waits for the sidecar, the reader shows a note and this code asks again. After the task has a result, or after the last attempt, the thread loads again.
  const THREAD_POLL_MS = 3000;
  const THREAD_POLL_MAX = 10;

  function showThreadFetch(id) {
    C.threadFetchId = id;
    const note = $("client-thread-fetch");
    if (note) note.hidden = id === null;
  }

  async function fetchThread(id) {
    const selected = () => String(C.selectedId) === String(id);
    for (let attempt = 0; attempt <= THREAD_POLL_MAX; attempt += 1) {
      const result = await api("POST", `/api/federation/timeline/${encodeURIComponent(id)}/context`);
      if (!selected()) return;
      if (!(result.ok && result.data.pending) || attempt === THREAD_POLL_MAX) {
        showThreadFetch(null);
        // Without a wait, the thread that the reader has is the current one.
        if (attempt > 0) await reloadThread(id);
        return;
      }
      showThreadFetch(id);
      await new Promise((resolve) => setTimeout(resolve, THREAD_POLL_MS));
      if (!selected()) return;
    }
  }

  async function reloadThread(id) {
    const result = await api("GET", `/api/federation/timeline/${encodeURIComponent(id)}`);
    if (!result.ok || String(C.selectedId) !== String(id) || !C.detail) return;
    const ids = (thread) => thread.map((entry) => String(entry.id)).join(",");
    // The frames load again when the reader is made again: only a thread with other items replaces the view.
    if (ids(result.data.thread) === ids(C.detail.thread)) return;
    C.detail = result.data;
    renderReader();
  }

  async function patchItem(id, change) {
    const result = await api("PATCH", `/api/federation/timeline/${encodeURIComponent(id)}`, change);
    if (!result.ok) { bridge.ticker(tr("synd.error.request", { error: errorText(result) })); return; }
    applyItem(result.data.item);
    C.counts = Object.assign({}, C.counts || {}, result.data.counts);
    renderClient();
    renderStageBar();
  }

  function applyItem(item) {
    const index = C.items.findIndex((x) => String(x.id) === String(item.id));
    if (index >= 0) C.items[index] = Object.assign({}, C.items[index], item);
    if (C.detail) {
      if (String(C.detail.item.id) === String(item.id)) C.detail.item = Object.assign({}, C.detail.item, item);
      C.detail.thread = C.detail.thread.map((entry) => String(entry.id) === String(item.id) ? Object.assign({}, entry, item) : entry);
    }
  }

  function findItem(id) {
    const fromList = C.items.find((x) => String(x.id) === String(id));
    if (fromList) return fromList;
    if (C.detail) return [C.detail.item].concat(C.detail.thread).find((x) => String(x.id) === String(id)) || null;
    return null;
  }

  function renderClient() {
    renderStreams();
    renderFollowing();
    renderList();
    renderReader();
  }

  // --- Follows -------------------------------------------------------------

  async function loadFollowing() {
    if (!IS_NETWORK || !IS_OWNER) return;
    const [following, status] = await Promise.all([api("GET", "/api/federation/following?limit=80"), api("GET", "/api/federation/status")]);
    C.following = following.ok ? following.data : { items: [], total: 0 };
    C.actor = status.ok ? status.data : null;
    renderClient();
  }

  function renderFollowing() {
    const el = $("client-following");
    const folder = $("client-following-folder");
    if (!el || !folder) return;
    folder.hidden = !IS_NETWORK;
    if (!IS_NETWORK || C.following === null) { el.innerHTML = ""; return; }
    const items = C.following.items || [];
    el.innerHTML = items.length
      ? items.map((f) => `<div class="synd-follow-row"><span class="synd-follow-name" title="${esc(f.actor_iri || f.request)}">${f.actor_type === "Group" ? ic("users") : ic("user")} ${esc(f.display_name || f.request)}</span><span class="tt-count-pill ${f.state === "accepted" ? "fresh" : ""}">${esc(tr(`synd.client.follow_state.${f.state}`))}</span><button type="button" class="tt-icon-btn" data-unfollow="${esc(f.id)}" title="${esc(tr("synd.client.unfollow"))}" aria-label="${esc(tr("synd.client.unfollow_name", { name: f.display_name || f.request }))}">✕</button></div>`).join("")
      : `<div class="synd-note">${esc(tr("synd.client.following_empty"))}</div>`;
  }

  async function follow(target) {
    const msg = $("client-follow-msg");
    const result = await api("POST", "/api/federation/following", { target });
    if (!result.ok) {
      const code = result.data && result.data.error;
      const known = ["not_actor", "blocked_domain", "invalid_target", "following_limit"].includes(code);
      msg.textContent = known ? tr(`synd.client.follow_error.${code}`) : tr("synd.error.request", { error: errorText(result) });
      return;
    }
    msg.textContent = tr("synd.client.follow_sent");
    $("client-follow-target").value = "";
    await loadFollowing();
  }

  async function unfollow(id) {
    const result = await api("DELETE", `/api/federation/following/${encodeURIComponent(id)}`);
    if (!result.ok) { $("client-follow-msg").textContent = tr("synd.error.request", { error: errorText(result) }); return; }
    await loadFollowing();
  }

  // --- Ingest of one item (Section 4.4) ------------------------------------

  function openIngestDialog(id) {
    const item = findItem(id);
    const dialog = $("modal-item-ingest");
    if (!item || !dialog) return;
    C.ingestItemId = id;
    const select = $("item-ingest-theme");
    const current = String(bridge.state.themeId);
    select.innerHTML = Object.values(bridge.themes).map((t) => `<option value="${esc(t.id)}" ${String(t.id) === current ? "selected" : ""}>${esc(t.icon)} ${esc(t.label)}</option>`).join("");
    $("item-ingest-msg").textContent = "";
    $("item-ingest-source").textContent = `${authorName(item.author)} — ${item.url || item.object_iri}`;
    $("btn-item-ingest").disabled = false;
    dialog.showModal();
  }

  async function submitIngest() {
    const id = C.ingestItemId;
    const themeKey = $("item-ingest-theme").value;
    const msg = $("item-ingest-msg");
    const button = $("btn-item-ingest");
    button.disabled = true;
    msg.textContent = tr("synd.ingest.running");
    const result = await api("POST", `/api/federation/timeline/${encodeURIComponent(id)}/ingest`, {});
    button.disabled = false;
    if (!result.ok || result.data.error || !result.data.linked) {
      msg.textContent = tr("synd.ingest.failed", { error: errorText(result) });
      return;
    }
    $("modal-item-ingest").close();
    bridge.ticker(tr("synd.ingest.done", { id: result.data.linked.document_id }));
    const item = findItem(id);
    if (item) applyItem(Object.assign({}, item, { linked: result.data.linked }));
    if (C.counts) C.counts.ingested = (C.counts.ingested || 0) + 1;
    renderClient();
    renderStageBar();
    // The new document is in the knowledge base of that theme. Read it again so that stage 2 shows it.
    if (String(themeKey) === String(bridge.state.themeId)) bridge.reload();
  }

  // The knowledge base view with the document of an item (the link between a post and its document).
  function openDocument(id, thenPublish) {
    const item = findItem(id);
    if (!item || !item.linked) return;
    setStage(thenPublish ? "publish" : "ingest");
    bridge.showDocument(item.linked.kb_id, item.linked.document_id).then(() => {
      // The publish dialog of the knowledge base (FP3-5), reached from the client.
      if (thenPublish) bridge.openDispatchModal(item.linked.document_id);
    });
  }

  // --- Events --------------------------------------------------------------

  document.querySelectorAll("[data-stage-btn]").forEach((btn) => btn.addEventListener("click", () => setStage(btn.getAttribute("data-stage-btn"))));
  document.querySelectorAll("[data-pub-filter]").forEach((btn) => btn.addEventListener("click", () => {
    bridge.setPublishFilter(btn.getAttribute("data-pub-filter"));
  }));

  $("panel-client").addEventListener("click", (e) => {
    const pick = (name) => { const el = e.target.closest(`[${name}]`); return el ? el.getAttribute(name) : null; };
    if (Compose && Compose.handleClick(e)) return;
    const stream = pick("data-client-stream");
    if (stream) { if (Feeds) Feeds.deactivate(); C.stream = stream; C.selectedId = null; C.detail = null; loadTimeline(true); return; }
    const star = pick("data-item-star");
    if (star) { const item = findItem(star); if (item) patchItem(star, { starred: !item.starred }); return; }
    const read = pick("data-item-read");
    if (read) { const item = findItem(read); if (item) patchItem(read, { unread: !item.unread }); return; }
    const ingest = pick("data-item-ingest");
    if (ingest) { openIngestDialog(ingest); return; }
    const doc = pick("data-item-doc");
    if (doc) { openDocument(doc, false); return; }
    const publish = pick("data-item-publish");
    if (publish) { openDocument(publish, true); return; }
    const warning = pick("data-open-warning");
    if (warning) { C.openWarnings.add(String(warning)); renderReader(); return; }
    const unfollowId = pick("data-unfollow");
    if (unfollowId) { unfollow(unfollowId); return; }
    if (e.target.closest("#client-load-more")) { loadTimeline(false); return; }
    if (e.target.closest("a, button, input")) return;
    const row = pick("data-item-id");
    if (row) selectItem(row);
  });

  $("client-list").addEventListener("keydown", (e) => {
    if (e.key !== "Enter" && e.key !== " ") return;
    const row = e.target.closest("[data-item-id]");
    if (!row || e.target !== row) return;
    e.preventDefault();
    selectItem(row.getAttribute("data-item-id"));
  });

  $("client-follow-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const target = $("client-follow-target").value.trim();
    if (target) follow(target);
  });

  $("form-item-ingest").addEventListener("submit", (e) => { e.preventDefault(); submitIngest(); });
  $("btn-client-refresh").addEventListener("click", () => { loadTimeline(true); loadFollowing(); });

  // Keys of the client view: j and k move in the list, s is the star.
  document.addEventListener("keydown", (e) => {
    if (C.stage !== "collect" || (Feeds && Feeds.active) || e.ctrlKey || e.metaKey || e.altKey) return;
    if (e.target.matches("input, textarea, select") || document.querySelector("dialog[open]")) return;
    if (!C.items.length) return;
    const index = C.items.findIndex((item) => String(item.id) === String(C.selectedId));
    if (e.key === "j" || e.key === "k") {
      const next = C.items[e.key === "j" ? Math.min(C.items.length - 1, index + 1) : Math.max(0, index - 1)];
      if (next) selectItem(next.id);
    } else if (e.key === "s" && index >= 0) {
      patchItem(C.items[index].id, { starred: !C.items[index].starred });
    }
  });

  // Quote policy of a published document (design Section 5.4 rule 6, FP4-9Q): a command of the context menu of the knowledge base view.
  function publishedObject(docId) {
    const theme = bridge.curTheme();
    return (theme && docId && C.publishedObjects && C.publishedObjects[`${theme.id}:${docId}`]) || null;
  }

  function openQuotePolicy(docId) {
    const object = publishedObject(docId);
    const dialog = $("modal-quote-policy");
    if (!object || !dialog) return;
    const theme = bridge.curTheme();
    const doc = ((theme && theme.documents) || []).find((d) => String(d.id) === String(docId));
    C.quotePolicyDoc = docId;
    $("quote-policy-doc").textContent = doc ? doc.title || "" : "";
    $("quote-policy-value").value = object.quote_policy || "manual";
    $("quote-policy-msg").textContent = "";
    dialog.showModal();
  }

  async function submitQuotePolicy() {
    const object = publishedObject(C.quotePolicyDoc);
    if (!object) return;
    const value = $("quote-policy-value").value;
    const result = await api("PATCH", `/api/federation/objects/${encodeURIComponent(object.id)}`, { quote_policy: value });
    if (!result.ok) { $("quote-policy-msg").textContent = errorText(result); return; }
    object.quote_policy = value;
    $("modal-quote-policy").close();
    bridge.ticker(tr("synd.quote.policy_saved"));
  }

  if ($("form-quote-policy")) $("form-quote-policy").addEventListener("submit", (e) => { e.preventDefault(); submitQuotePolicy(); });

  // Context menu (right click) of one document of the knowledge base view. The commands are those of the article menu in syndication_live.js.
  // The popover is "manual": the pointer release of the same right click would close an "auto" popover at once. This file closes it on a press outside, on Escape, and after a command.
  const contextMenu = $("pop-doc-context");
  const closeContextMenu = () => { if (contextMenu && contextMenu.matches(":popover-open")) contextMenu.hidePopover(); };
  $("headlines-container").addEventListener("contextmenu", (e) => {
    const row = e.target.closest("[data-row-id], [data-card-id]");
    if (!row || !contextMenu || typeof contextMenu.showPopover !== "function") return;
    e.preventDefault();
    bridge.state.activeArticleMenuDocId = row.getAttribute("data-row-id") || row.getAttribute("data-card-id");
    // The quote policy is a command of a published document of the owner only (FP4-9Q).
    const policyItem = $("menu-doc-quote-policy");
    if (policyItem) policyItem.hidden = !publishedObject(bridge.state.activeArticleMenuDocId);
    closeContextMenu();
    contextMenu.showPopover();
    const box = contextMenu.getBoundingClientRect();
    contextMenu.style.left = `${Math.max(4, Math.min(e.clientX, window.innerWidth - box.width - 4))}px`;
    contextMenu.style.top = `${Math.max(4, Math.min(e.clientY, window.innerHeight - box.height - 4))}px`;
    const first = contextMenu.querySelector("[role=menuitem]");
    if (first) first.focus();
  });
  if (contextMenu) {
    document.addEventListener("pointerdown", (e) => { if (!contextMenu.contains(e.target)) closeContextMenu(); });
    window.addEventListener("blur", closeContextMenu);
    contextMenu.addEventListener("click", (e) => {
      if (e.target.closest("[data-doc-quote-policy]")) { closeContextMenu(); openQuotePolicy(bridge.state.activeArticleMenuDocId); return; }
      if (e.target.closest("[data-art-cmd]")) closeContextMenu();
    });
    contextMenu.addEventListener("keydown", (e) => {
      if (e.key === "Escape") { e.preventDefault(); closeContextMenu(); return; }
      if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
      e.preventDefault();
      const items = [...contextMenu.querySelectorAll("[role=menuitem]")];
      const next = items[(items.indexOf(document.activeElement) + (e.key === "ArrowDown" ? 1 : items.length - 1)) % items.length];
      if (next) next.focus();
    });
  }

  // Command palette (HW-7): the module of the workspace, with the commands of this page.
  if (window.ClaireCommandPalette) {
    window.ClaireCommandPalette.init({
      getCommands: () => {
        const commands = [{ iconName: "graph", label: tr("nav.palette.cmd_graph"), run: () => { window.location.href = "/"; } }];
        if (IS_OWNER) {
          commands.unshift(
            { icon: "1", label: tr("synd.palette.stage_collect"), keywords: "collect timeline client", run: () => setStage("collect") },
            { icon: "2", label: tr("synd.palette.stage_ingest"), keywords: "ingest knowledge base", run: () => setStage("ingest") },
            { icon: "3", label: tr("synd.palette.stage_publish"), keywords: "publish", run: () => setStage("publish") }
          );
        }
        commands.push({ icon: "+", label: tr("nav.palette.cmd_ingest"), run: () => { setStage("ingest"); const btn = $("btn-open-ingest"); if (btn) btn.click(); } });
        return commands;
      },
      getCachedDocuments: (query) => {
        const q = String(query || "").trim().toLowerCase();
        return ((bridge.curTheme() || {}).documents || []).filter((d) => !q || String(d.title || "").toLowerCase().includes(q)).slice(0, 10).map((d) => ({ id: d.id, title: d.title, source_type: d.sourceType }));
      },
      onSelectDocument: (d) => { setStage("ingest"); bridge.showDocument(bridge.state.themeId, d.id); }
    });
    const paletteBtn = $("btn-palette");
    if (paletteBtn) paletteBtn.addEventListener("click", () => window.ClaireCommandPalette.open());
  }

  if (Compose) {
    Compose.init({
      findItem,
      applyItem,
      renderClient,
      ticker: bridge.ticker,
      // The quoted post of a post, when the account has it as an item.
      selectItem: (id) => selectItem(id),
      // A new note or an edit: the list and the open thread read again.
      refresh: () => { loadTimeline(true); if (C.selectedId) selectItem(C.selectedId); },
      // A deleted own note: its item is gone.
      removed: (id) => {
        const wasOpen = String(C.selectedId) === String(id);
        if (wasOpen) { C.selectedId = null; C.detail = null; }
        loadTimeline(true);
        if (!wasOpen && C.selectedId) selectItem(C.selectedId);
      }
    });
  }
  if (Feeds) Feeds.init({ setStage, renderStageBar, renderClient, stage: () => C.stage, selectTimelineItem: (id) => { renderClient(); selectItem(id); } });
  bridge.onRender = renderStageBar;
  document.addEventListener("visibilitychange", () => { if (!document.hidden) pollTimeline(); });
  setInterval(pollTimeline, POLL_MS);

  const requested = new URLSearchParams(window.location.search).get("stage");
  setStage(IS_OWNER && STAGES.includes(requested) ? requested : "ingest", { keepUrl: true });
  if (IS_OWNER && IS_NETWORK) {
    // The counts of the stage bar. The lists load when their stage opens.
    pollTimeline();
    loadPublished().then(renderStageBar);
  }
})();
